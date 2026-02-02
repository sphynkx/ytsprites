import time
import grpc

from config.service_cfg import cfg
from proto import ytsprites_pb2
from proto import ytsprites_pb2_grpc
from runtime.queue_rt import job_manager
from runtime.models_rt import JobState


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _kv(**kwargs) -> str:
    parts = []
    for k, v in kwargs.items():
        if v is None or v == "":
            continue
        parts.append(f"{k}={v}")
    return (" " + " ".join(parts)) if parts else ""


def log(job_id: str, msg: str, **kwargs):
    print(f"[{_ts()}] [ytsprites] job_id={job_id} {msg}{_kv(**kwargs)}")


def _ctx_info(context: grpc.ServicerContext) -> dict:
    try:
        peer = context.peer()
    except Exception:
        peer = None
    try:
        active = context.is_active()
    except Exception:
        active = None
    return {"peer": peer, "ctx_active": active}


class SpritesService(ytsprites_pb2_grpc.SpritesServicer):
    def CreateJob(self, request, context):
        if not request.video_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("video_id is required")
            return ytsprites_pb2.CreateJobReply(accepted=False, job_id="", message="video_id is required")

        if not request.options:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("options is required")
            return ytsprites_pb2.CreateJobReply(accepted=False, job_id="", message="options is required")

        if not request.source or not request.source.rel_path or not request.source.storage.address:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("source.storage.address and source.rel_path are required")
            return ytsprites_pb2.CreateJobReply(accepted=False, job_id="", message="source is required")

        if not request.output or not request.output.base_rel_dir or not request.output.storage.address:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("output.storage.address and output.base_rel_dir are required")
            return ytsprites_pb2.CreateJobReply(accepted=False, job_id="", message="output is required")

        filename = getattr(request, "filename", "") or ""
        video_mime = getattr(request, "video_mime", "") or ""

        job_id = job_manager.create_job(
            video_id=request.video_id,
            options=request.options,
            filename=filename,
            video_mime=video_mime,
            source=request.source,
            output=request.output,
        )

        if not job_id:
            context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
            context.set_details("Queue/job capacity is full")
            return ytsprites_pb2.CreateJobReply(accepted=False, job_id="", message="Queue/job capacity is full")

        job = job_manager.get_job(job_id)
        log(
            job_id,
            "CreateJob accepted",
            video_id=request.video_id,
            src=request.source.rel_path,
            out=request.output.base_rel_dir,
            tmp=job.temp_dir_path if job else "",
            **_ctx_info(context),
        )
        return ytsprites_pb2.CreateJobReply(accepted=True, job_id=job_id, message="Created")

    def WatchStatus(self, request, context):
        job_id = request.job_id
        log(job_id, "WatchStatus connected", **_ctx_info(context))
        while True:
            job = job_manager.get_job(job_id)
            if not job:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details("Job not found")
                return

            yield ytsprites_pb2.StatusUpdate(
                job_id=job.job_id,
                state=job.state,
                percent=job.percent,
                message=job.message,
                bytes_processed=getattr(job, "bytes_processed", 0),
            )

            if job.state in (JobState.JOB_STATE_DONE, JobState.JOB_STATE_FAILED, JobState.JOB_STATE_CANCELED):
                return

            time.sleep(1)

    def GetResult(self, request, context):
        job_id = request.job_id
        job = job_manager.get_job(job_id)
        if not job:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Job not found")
            return ytsprites_pb2.ResultReply()

        if job.state not in (JobState.JOB_STATE_DONE, JobState.JOB_STATE_FAILED, JobState.JOB_STATE_CANCELED):
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details("Job not ready")
            return ytsprites_pb2.ResultReply()

        res = job.result
        if not res:
            return ytsprites_pb2.ResultReply(
                job_id=job.job_id,
                video_id=job.video_id,
                state=job.state,
                message=job.message or "",
            )

        return ytsprites_pb2.ResultReply(
            job_id=job.job_id,
            video_id=res.video_id,
            state=res.state,
            message=res.message or "",
            vtt=res.vtt if res.vtt else None,
            sprites=res.sprites,
            sprites_count=res.sprites_count,
            total_sprite_bytes=res.total_sprite_bytes,
            seconds=res.seconds,
        )

    def Cancel(self, request, context):
        job_id = request.job_id
        success = job_manager.cancel_job(job_id)
        return ytsprites_pb2.CancelReply(job_id=job_id, canceled=success)

    def Health(self, request, context):
        return ytsprites_pb2.HealthReply(status="ok", version=getattr(cfg, "VERSION", ""))