import os
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

        filename = getattr(request, "filename", "") or ""
        video_mime = getattr(request, "video_mime", "") or ""

        job_id = job_manager.create_job(
            video_id=request.video_id,
            mime=video_mime,
            options=request.options,
            filename=filename,
        )

        if not job_id:
            context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
            context.set_details("Queue/job capacity is full")
            return ytsprites_pb2.CreateJobReply(
                accepted=False,
                job_id="",
                message="Queue/job capacity is full",
            )

        job = job_manager.get_job(job_id)
        log(
            job_id,
            "CreateJob accepted",
            video_id=request.video_id,
            filename=filename,
            mime=video_mime,
            step_sec=getattr(request.options, "step_sec", None),
            cols=getattr(request.options, "cols", None),
            rows=getattr(request.options, "rows", None),
            fmt=getattr(request.options, "format", None),
            quality=getattr(request.options, "quality", None),
            tmp=job.temp_dir_path if job else "",
            max_upload_bytes=cfg.MAX_UPLOAD_BYTES,
        )

        return ytsprites_pb2.CreateJobReply(
            accepted=True,
            job_id=job_id,
            message="Created",
        )

    def UploadSource(self, request_iterator, context):
        job = None
        job_id = ""
        bytes_received = 0
        started = time.time()

        # For periodic progress logs
        next_log_at = 0
        log_every_bytes = 64 * 1024 * 1024  # 64MB

        try:
            for chunk in request_iterator:
                if not chunk.job_id:
                    context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                    context.set_details("job_id is required in UploadChunk")
                    return ytsprites_pb2.UploadReply(accepted=False, job_id="", message="job_id is required", bytes_received=0)

                if job is None:
                    job_id = chunk.job_id
                    job = job_manager.get_job(job_id)
                    if not job:
                        context.set_code(grpc.StatusCode.NOT_FOUND)
                        context.set_details("Job not found")
                        return ytsprites_pb2.UploadReply(accepted=False, job_id=job_id, message="Job not found", bytes_received=0)

                    if job.state == JobState.JOB_STATE_CANCELED:
                        context.set_code(grpc.StatusCode.CANCELLED)
                        context.set_details("Job canceled")
                        return ytsprites_pb2.UploadReply(accepted=False, job_id=job_id, message="Job canceled", bytes_received=job.bytes_received)

                    if job.upload_started_at <= 0:
                        job.upload_started_at = time.time()
                        job.update_status(JobState.JOB_STATE_SUBMITTED, 0, "Uploading...")

                    if job.upload_done:
                        context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                        context.set_details("Upload already completed")
                        return ytsprites_pb2.UploadReply(accepted=False, job_id=job_id, message="Upload already completed", bytes_received=job.bytes_received)

                    bytes_received = job.bytes_received
                    next_log_at = bytes_received + log_every_bytes

                    log(
                        job_id,
                        "UploadSource started",
                        current_bytes=bytes_received,
                        dest=job.source_file_path,
                    )

                # Enforce strict offset monotonicity (MVP)
                if chunk.offset != bytes_received:
                    log(job_id, "UploadSource bad offset", got=chunk.offset, expected=bytes_received)
                    context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                    context.set_details(f"Unexpected offset: got={chunk.offset} expected={bytes_received}")
                    return ytsprites_pb2.UploadReply(
                        accepted=False,
                        job_id=job_id,
                        message=f"Unexpected offset: got={chunk.offset} expected={bytes_received}",
                        bytes_received=bytes_received,
                    )

                data = chunk.data or b""
                if data:
                    if bytes_received + len(data) > cfg.MAX_UPLOAD_BYTES:
                        log(job_id, "Upload too large", limit=cfg.MAX_UPLOAD_BYTES, would_be=bytes_received + len(data))
                        job.update_status(JobState.JOB_STATE_FAILED, 0, f"Upload too large (limit={cfg.MAX_UPLOAD_BYTES} bytes)")
                        # cleanup best-effort
                        if job.temp_dir_path:
                            try:
                                from utils import files_ut
                                files_ut.cleanup_workspace(job.temp_dir_path)
                            except Exception:
                                pass

                        context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
                        context.set_details("Upload too large")
                        return ytsprites_pb2.UploadReply(
                            accepted=False,
                            job_id=job_id,
                            message="Upload too large",
                            bytes_received=bytes_received,
                        )

                    # Write chunk at offset (no full-file buffering)
                    with open(job.source_file_path, "r+b" if os.path.exists(job.source_file_path) else "w+b") as f:
                        f.seek(bytes_received)
                        f.write(data)
                        f.flush()

                    bytes_received += len(data)
                    job.bytes_received = bytes_received

                    if bytes_received >= next_log_at:
                        dt = max(time.time() - started, 1e-6)
                        mb = bytes_received / (1024 * 1024)
                        speed = mb / dt
                        log(job_id, "Upload progress", bytes_received=bytes_received, mb=f"{mb:.1f}", mbps=f"{speed:.2f}")
                        next_log_at += log_every_bytes

                if chunk.last:
                    ok = job_manager.mark_upload_complete(job_id)
                    if not ok:
                        log(job_id, "Upload completed but enqueue failed")
                        context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                        context.set_details("Unable to enqueue job after upload")
                        return ytsprites_pb2.UploadReply(
                            accepted=False,
                            job_id=job_id,
                            message="Unable to enqueue job after upload",
                            bytes_received=bytes_received,
                        )

                    dt = max(time.time() - started, 1e-6)
                    mb = bytes_received / (1024 * 1024)
                    speed = mb / dt
                    log(job_id, "UploadSource done", bytes_received=bytes_received, mb=f"{mb:.1f}", seconds=f"{dt:.2f}", mbps=f"{speed:.2f}")

                    return ytsprites_pb2.UploadReply(
                        accepted=True,
                        job_id=job_id,
                        message="Uploaded",
                        bytes_received=bytes_received,
                    )

            # Stream ended without last=true
            if job is not None:
                log(job_id, "UploadSource ended without last=true", bytes_received=bytes_received)
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("Upload stream ended without last=true")
                return ytsprites_pb2.UploadReply(
                    accepted=False,
                    job_id=job_id,
                    message="Upload stream ended without last=true",
                    bytes_received=bytes_received,
                )

            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Empty upload stream")
            return ytsprites_pb2.UploadReply(accepted=False, job_id="", message="Empty upload stream", bytes_received=0)

        except Exception as e:
            if job is not None:
                log(job_id, "UploadSource failed", err=str(e))
                job.update_status(JobState.JOB_STATE_FAILED, 0, f"Upload failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return ytsprites_pb2.UploadReply(accepted=False, job_id=job_id, message=str(e), bytes_received=bytes_received)

    def WatchStatus(self, request, context):
        job_id = request.job_id
        # log(job_id, "WatchStatus connected")
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
            )

            if job.state in [JobState.JOB_STATE_DONE, JobState.JOB_STATE_FAILED, JobState.JOB_STATE_CANCELED]:
                # log(job_id, "WatchStatus finished", state=job.state)
                return

            time.sleep(1)

    def GetResult(self, request, context):
        job_id = request.job_id
        job = job_manager.get_job(job_id)
        if not job:
            log(job_id, "GetResult NOT_FOUND")
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Job not found")
            return ytsprites_pb2.ResultReply()

        if job.state != JobState.JOB_STATE_DONE:
            log(job_id, "GetResult not ready", state=job.state, percent=job.percent, msg=job.message)
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details("Job not ready")
            return ytsprites_pb2.ResultReply()

        res = job.result
        sprites_proto = []
        total_bytes = 0
        if res:
            for name, data in res.sprites:
                total_bytes += len(data)
                sprites_proto.append(ytsprites_pb2.SpriteBin(name=name, data=data))

            log(job_id, "GetResult returning", sprites=len(sprites_proto), total_bytes=total_bytes)
            return ytsprites_pb2.ResultReply(
                job_id=job.job_id,
                sprites=sprites_proto,
                vtt=res.vtt_content,
                video_id=res.video_id,
            )

        log(job_id, "GetResult empty result?!")
        return ytsprites_pb2.ResultReply()

    def Cancel(self, request, context):
        job_id = request.job_id
        success = job_manager.cancel_job(job_id)
        log(job_id, "Cancel", success=success)
        return ytsprites_pb2.CancelReply(job_id=job_id, canceled=success)

    def Health(self, request, context):
        return ytsprites_pb2.HealthReply(status="ok")