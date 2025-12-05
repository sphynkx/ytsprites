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
            
            # Use created workspace
            workspace = job.temp_dir_path
            video_path = job.video_file_path
            
            if not workspace or not video_path or not os.path.exists(video_path):
                 raise FileNotFoundError("Video file or workspace lost")

            def on_progress(pct, msg):
                # Canceling check
                if job.state == JobState.JOB_STATE_CANCELED:
                    raise InterruptedError("Job canceled")
                job.update_status(JobState.JOB_STATE_PROCESSING, pct, msg)

            # Run processing
            # Returns list of abs paths and vtt text
            sprite_files_abs, vtt_text = ffmpeg_ut.process_video(
                video_path, workspace, job.options, on_progress
            )
            
            # Gather results
            sprites_data = []
            for abs_path in sprite_files_abs:
                if os.path.exists(abs_path):
                    # Sprites filenames for client w/o paths!!
                    name = os.path.basename(abs_path)
                    with open(abs_path, 'rb') as f:
                        data = f.read()
                    sprites_data.append((name, data))
                else:
                    print(f"[Worker-{worker_id}] Warning: Result file not found {abs_path}")
            
            job.result = JobResult(
                sprites=sprites_data,
                vtt_content=vtt_text,
                video_id=job.video_id
            )
            
            job.update_status(JobState.JOB_STATE_DONE, 100, "Done")
            print(f"[Worker-{worker_id}] Job {job.job_id} DONE. Generated {len(sprites_data)} sprites.")
            
        except InterruptedError:
            print(f"[Worker-{worker_id}] Job {job.job_id} CANCELED")
            # Status is CANCELED already
        except Exception as e:
            print(f"[Worker-{worker_id}] Job {job.job_id} FAILED: {e}")
            import traceback
            traceback.print_exc()
            job.update_status(JobState.JOB_STATE_FAILED, 0, str(e))
        finally:
            # Clean temps
            if workspace:
                files_ut.cleanup_workspace(workspace)

def start_workers():
    for i in range(cfg.MAX_WORKERS):
        t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
        t.start()