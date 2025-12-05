import threading
import time
import os
from config.service_cfg import cfg
from runtime.queue_rt import job_manager
from runtime.models_rt import JobResult
from proto.ytsprites_pb2 import JobState
from utils import files_ut, ffmpeg_ut

def worker_loop(worker_id):
    print(f"[Worker-{worker_id}] Started")
    while True:
        job = job_manager.pop_next_job()
        if not job:
            time.sleep(1)
            continue
            
        print(f"[Worker-{worker_id}] Picked job {job.job_id}")
        
        workspace = None
        try:
            job.update_status(JobState.JOB_STATE_PROCESSING, 0, "Starting...")
            
            workspace = files_ut.create_job_workspace(job.job_id)
            job.temp_dir_path = workspace
            
            # Save the video (assuming it's already loaded into job memory or transferred somehow)
            # In the current prototype, video_bytes are received in Submit.
            # In a real implementation, it's better to stream to a temporary file directly in the handler,
            # but since Submit is a Unary, the bytes are in the handler's memory.
            # We need to pass them here. We'll work on this in the handler (save to disk BEFORE the queue).            

            video_path = os.path.join(workspace, f"input_video")
            # Asume that file is available already (see handlers_srv)
            
            def on_progress(pct, msg):
                if job.state == JobState.JOB_STATE_CANCELED:
                    raise InterruptedError("Job canceled")
                job.update_status(JobState.JOB_STATE_PROCESSING, pct, msg)

            # Run processing
            sprites_files, vtt_text = ffmpeg_ut.process_video(
                video_path, workspace, job.options, on_progress
            )
            
            # Gather results
            sprites_data = []
            for fname in sprites_files:
                full_path = os.path.join(workspace, fname)
                if os.path.exists(full_path):
                    with open(full_path, 'rb') as f:
                        sprites_data.append((fname, f.read()))
            
            job.result = JobResult(
                sprites=sprites_data,
                vtt_content=vtt_text,
                video_id=job.video_id
            )
            
            job.update_status(JobState.JOB_STATE_DONE, 100, "Done")
            print(f"[Worker-{worker_id}] Job {job.job_id} DONE")
            
        except InterruptedError:
            print(f"[Worker-{worker_id}] Job {job.job_id} CANCELED during processing")
            # Status is CANCELED already
        except Exception as e:
            print(f"[Worker-{worker_id}] Job {job.job_id} FAILED: {e}")
            job.update_status(JobState.JOB_STATE_FAILED, 0, str(e))
        finally:
            if workspace:
                files_ut.cleanup_workspace(workspace)

def start_workers():
    for i in range(cfg.MAX_WORKERS):
        t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
        t.start()