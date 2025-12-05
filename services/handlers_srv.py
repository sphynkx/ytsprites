import time
import grpc
from proto import ytsprites_pb2
from proto import ytsprites_pb2_grpc
from runtime.queue_rt import job_manager
from runtime.models_rt import JobState
from utils import files_ut

class SpritesService(ytsprites_pb2_grpc.SpritesServicer):
    
    def Submit(self, request, context):
        size_mb = len(request.video_bytes) / (1024 * 1024)
        print(f"[GRPC] Submit request: video_id={request.video_id}, size={size_mb:.2f}MB, mime={request.video_mime}")
        
        if not request.video_bytes:
             print("[GRPC] Submit Error: Empty video bytes")
             context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
             context.set_details('Empty video bytes')
             return ytsprites_pb2.SubmitReply(accepted=False)

        job_id = job_manager.create_job(
            video_id=request.video_id,
            mime=request.video_mime,
            options=request.options
        )
        
        if not job_id:
            print(f"[GRPC] Submit Rejected: Queue full")
            return ytsprites_pb2.SubmitReply(accepted=False, job_id="", queue_position=-1)
        
        print(f"[GRPC] Job Created: {job_id}")
        
        # Save video
        job = job_manager.get_job(job_id)
        if job:
            workspace = files_ut.create_job_workspace(job_id)
            video_path = f"{workspace}/input_video"
            files_ut.save_bytes_to_file(video_path, request.video_bytes)
            job.temp_dir_path = workspace
            job.video_file_path = video_path
            print(f"[GRPC] Video saved to: {video_path}")

        pos = job_manager.get_queue_position(job_id)
        return ytsprites_pb2.SubmitReply(job_id=job_id, accepted=True, queue_position=pos)

    def WatchStatus(self, request, context):
        # print(f"[GRPC] WatchStatus connected: job_id={request.job_id}")
        
        job_id = request.job_id
        while True:
            job = job_manager.get_job(job_id)
            if not job:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details('Job not found')
                return

            yield ytsprites_pb2.StatusUpdate(
                job_id=job.job_id,
                state=job.state,
                percent=job.percent,
                message=job.message
            )
            
            if job.state in [JobState.JOB_STATE_DONE, JobState.JOB_STATE_FAILED, JobState.JOB_STATE_CANCELED]:
                # print(f"[GRPC] WatchStatus finished: job_id={request.job_id}, state={job.state}")
                return
            
            time.sleep(1)

    def GetResult(self, request, context):
        print(f"[GRPC] GetResult request: job_id={request.job_id}")
        job = job_manager.get_job(request.job_id)
        if not job:
            print("[GRPC] GetResult Error: Job not found")
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details('Job not found')
            return ytsprites_pb2.ResultReply()
            
        if job.state != JobState.JOB_STATE_DONE:
            print(f"[GRPC] GetResult Error: Job not ready (state={job.state})")
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details('Job not ready')
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
                video_id=res.video_id
            )
        return ytsprites_pb2.ResultReply()

    def Cancel(self, request, context):
        print(f"[GRPC] Cancel request: job_id={request.job_id}")
        success = job_manager.cancel_job(request.job_id)
        return ytsprites_pb2.CancelReply(job_id=request.job_id, canceled=success)

    def Health(self, request, context):
        return ytsprites_pb2.HealthReply(status="ok")