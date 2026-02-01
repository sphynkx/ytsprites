import threading
import time
import os

from config.service_cfg import cfg
from runtime.queue_rt import job_manager
from runtime.models_rt import JobResult
from proto.ytsprites_pb2 import JobState
from utils import files_ut, ffmpeg_ut


def worker_loop(worker_id: int):
    print(f"[Worker-{worker_id}] Started")
    while True:
        job = job_manager.pop_next_job()
        if not job:
            time.sleep(1)
            continue

        print(f"[Worker-{worker_id}] Picked job {job.job_id}")

        workspace = None
        try:
            job.processing_started_at = time.time()
            job.update_status(JobState.JOB_STATE_PROCESSING, 0, "Starting...")

            workspace = job.temp_dir_path
            video_path = job.source_file_path

            if not workspace or not video_path or not os.path.exists(video_path):
                raise FileNotFoundError("Source video file or workspace lost")

            def on_progress(pct, msg):
                if job.state == JobState.JOB_STATE_CANCELED:
                    raise InterruptedError("Job canceled")
                job.update_status(JobState.JOB_STATE_PROCESSING, pct, msg)

            sprite_files_abs, vtt_text = ffmpeg_ut.process_video(
                video_path, workspace, job.options, on_progress
            )

            sprites_data = []
            for abs_path in sprite_files_abs:
                if os.path.exists(abs_path):
                    name = os.path.basename(abs_path)
                    with open(abs_path, "rb") as f:
                        data = f.read()
                    sprites_data.append((name, data))
                else:
                    print(f"[Worker-{worker_id}] Warning: Result file not found {abs_path}")

            job.result = JobResult(
                sprites=sprites_data,
                vtt_content=vtt_text,
                video_id=job.video_id,
            )

            job.processing_finished_at = time.time()
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
            # Clean temps after terminal outcome
            if workspace:
                files_ut.cleanup_workspace(workspace)


def ttl_janitor_loop():
    """
    Periodic cleanup for abandoned / expired jobs to avoid tmp-dir leaks.
    Uses cfg.JOB_TTL_SEC as expiration threshold.
    """
    interval = min(max(cfg.JOB_TTL_SEC // 10, 30), 300)  # 30s..5m
    print(f"[Janitor] Started. ttl={cfg.JOB_TTL_SEC}s interval={interval}s")
    while True:
        try:
            job_manager.cleanup_expired(cfg.JOB_TTL_SEC)
        except Exception as e:
            print(f"[Janitor] cleanup_expired failed: {e}")
        time.sleep(interval)


def start_workers():
    # Start workers
    for i in range(cfg.MAX_WORKERS):
        t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
        t.start()

    # Start janitor
    j = threading.Thread(target=ttl_janitor_loop, daemon=True)
    j.start()