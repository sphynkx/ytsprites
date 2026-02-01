import os
import time
import grpc

from config.service_cfg import cfg
from proto import ytsprites_pb2
from proto import ytsprites_pb2_grpc
from runtime.queue_rt import job_manager
from runtime.models_rt import JobState
from utils import files_ut


class SpritesService(ytsprites_pb2_grpc.SpritesServicer):

    def CreateJob(self, request, context):
        # Validate
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

        print(f"[GRPC] CreateJob accepted: job_id={job_id} video_id={request.video_id} filename={filename} mime={video_mime}")
        return ytsprites_pb2.CreateJobReply(
            accepted=True,
            job_id=job_id,
            message="Created",
        )

    def UploadSource(self, request_iterator, context):
        """
        Client-streaming upload. MVP: strictly increasing offset (must match bytes_received).
        Writes to job.source_file_path without holding all bytes in memory.
        """
        job = None
        job_id = ""
        bytes_received = 0
        started = time.time()

        try:
            for chunk in request_iterator:
                # Basic validation
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

                    # If client retries after completion: reject for MVP
                    if job.upload_done:
                        context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                        context.set_details("Upload already completed")
                        return ytsprites_pb2.UploadReply(accepted=False, job_id=job_id, message="Upload already completed", bytes_received=job.bytes_received)

                    bytes_received = job.bytes_received
                    print(f"[GRPC] UploadSource started: job_id={job_id} current_bytes={bytes_received}")

                # Enforce strict offset monotonicity (MVP)
                if chunk.offset != bytes_received:
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
                    # Check max size BEFORE writing
                    if bytes_received + len(data) > cfg.MAX_UPLOAD_BYTES:
                        job.update_status(JobState.JOB_STATE_FAILED, 0, f"Upload too large (limit={cfg.MAX_UPLOAD_BYTES} bytes)")
                        # cleanup best-effort
                        if job.temp_dir_path:
                            files_ut.cleanup_workspace(job.temp_dir_path)

                        context.set_code(grpc.StatusCode.RESOURCE_EXHAUSTED)
                        context.set_details("Upload too large")
                        return ytsprites_pb2.UploadReply(
                            accepted=False,
                            job_id=job_id,
                            message="Upload too large",
                            bytes_received=bytes_received,
                        )

                    # Write chunk at offset
                    f = files_ut.open_spooled_file_for_write(job.source_file_path, bytes_received)
                    try:
                        f.write(data)
                        f.flush()
                    finally:
                        f.close()

                    bytes_received += len(data)
                    job.bytes_received = bytes_received

                if chunk.last:
                    # Finalize upload
                    job.message = "Upload complete"
                    job.updated_at = time.time()

                    # Enqueue for processing
                    ok = job_manager.mark_upload_complete(job_id)
                    if not ok:
                        context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                        context.set_details("Unable to enqueue job after upload")
                        return ytsprites_pb2.UploadReply(
                            accepted=False,
                            job_id=job_id,
                            message="Unable to enqueue job after upload",
                            bytes_received=bytes_received,
                        )

                    dt = time.time() - started
                    print(f"[GRPC] UploadSource done: job_id={job_id} bytes_received={bytes_received} in {dt:.2f}s")
                    return ytsprites_pb2.UploadReply(
                        accepted=True,
                        job_id=job_id,
                        message="Uploaded",
                        bytes_received=bytes_received,
                    )

            # Stream ended without last=true
            if job is not None:
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

        except grpc.RpcError:
            raise
        except Exception as e:
            if job is not None:
                job.update_status(JobState.JOB_STATE_FAILED, 0, f"Upload failed: {e}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(str(e))
            return ytsprites_pb2.UploadReply(accepted=False, job_id=job_id, message=str(e), bytes_received=bytes_received)

    def WatchStatus(self, request, context):
        job_id = request.job_id
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
                return

            time.sleep(1)

    def GetResult(self, request, context):
        print(f"[GRPC] GetResult request: job_id={request.job_id}")
        job = job_manager.get_job(request.job_id)
        if not job:
            print("[GRPC] GetResult Error: Job not found")
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details("Job not found")
            return ytsprites_pb2.ResultReply()

        if job.state != JobState.JOB_STATE_DONE:
            print(f"[GRPC] GetResult Error: Job not ready (state={job.state})")
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details("Job not ready")
            return ytsprites_pb2.ResultReply()

        res = job.result
        sprites_proto = []
        if res:
            for name, data in res.sprites:
                sprites_proto.append(ytsprites_pb2.SpriteBin(name=name, data=data))

            print(f"[GRPC] Returning result: {len(sprites_proto)} sprites")
            return ytsprites_pb2.ResultReply(
                job_id=job.job_id,
                sprites=sprites_proto,
                vtt=res.vtt_content,
                video_id=res.video_id,
            )
        return ytsprites_pb2.ResultReply()

    def Cancel(self, request, context):
        print(f"[GRPC] Cancel request: job_id={request.job_id}")
        success = job_manager.cancel_job(request.job_id)
        return ytsprites_pb2.CancelReply(job_id=request.job_id, canceled=success)

    def Health(self, request, context):
        return ytsprites_pb2.HealthReply(status="ok")