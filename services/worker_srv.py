import threading
import time
import os

from config.service_cfg import cfg
from runtime.queue_rt import job_manager
from runtime.models_rt import JobResult
from proto.ytsprites_pb2 import JobState
from utils import files_ut, ffmpeg_ut


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def log(msg: str):
    print(f"[{_ts()}] [ytsprites] {msg}")


def worker_loop(worker_id: int):
    log(f"[Worker-{worker_id}] Started")
    while True:
        job = job_manager.pop_next_job()
        if not job:
            time.sleep(1)
            continue

        log(f"[Worker-{worker_id}] Picked job_id={job.job_id} video_id={job.video_id} bytes={job.bytes_received}")

        workspace = None
        try:
            job.processing_started_at = time.time()
            job.update_status(JobState.JOB_STATE_PROCESSING, 0, "Starting...")

            workspace = job.temp_dir_path
            video_path = job.source_file_path

            if not workspace or not video_path or not os.path.exists(video_path):
                raise FileNotFoundError("Source video file or workspace lost")

            def on_progress(pct, msg_):
                if job.state == JobState.JOB_STATE_CANCELED:
                    raise InterruptedError("Job canceled")
                job.update_status(JobState.JOB_STATE_PROCESSING, pct, msg_)

            sprite_files_abs, vtt_text = ffmpeg_ut.process_video(
                video_path, workspace, job.options, on_progress
            )

            sprites_data = []
            total_bytes = 0
            for abs_path in sprite_files_abs:
                if os.path.exists(abs_path):
                    name = os.path.basename(abs_path)
                    with open(abs_path, "rb") as f:
                        data = f.read()
                    total_bytes += len(data)
                    sprites_data.append((name, data))
                else:
                    log(f"[Worker-{worker_id}] Warning: Result file not found {abs_path}")

            job.result = JobResult(
                sprites=sprites_data,
                vtt_content=vtt_text,
                video_id=job.video_id,
            )

            job.processing_finished_at = time.time()
            dt = job.processing_finished_at - job.processing_started_at
            log(f"[Worker-{worker_id}] DONE job_id={job.job_id} sprites={len(sprites_data)} total_sprite_bytes={total_bytes} seconds={dt:.2f}")

            job.update_status(JobState.JOB_STATE_DONE, 100, "Done")

        except InterruptedError:
            log(f"[Worker-{worker_id}] CANCELED job_id={job.job_id}")
        except Exception as e:
            log(f"[Worker-{worker_id}] FAILED job_id={job.job_id} err={e}")
            import traceback
            traceback.print_exc()
            job.update_status(JobState.JOB_STATE_FAILED, 0, str(e))
        finally:
            if workspace:
                files_ut.cleanup_workspace(workspace)
                log(f"[Worker-{worker_id}] Cleaned workspace job_id={job.job_id} path={workspace}")


def ttl_janitor_loop():
    interval = min(max(cfg.JOB_TTL_SEC // 10, 30), 300)  # 30s..5m
    log(f"[Janitor] Started ttl={cfg.JOB_TTL_SEC}s interval={interval}s")
    while True:
        try:
            removed = job_manager.cleanup_expired(cfg.JOB_TTL_SEC)
            if removed:
                log(f"[Janitor] Removed expired jobs: {removed}")
        except Exception as e:
            log(f"[Janitor] cleanup_expired failed: {e}")
        time.sleep(interval)


def start_workers():
    for i in range(cfg.MAX_WORKERS):
        t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
        t.start()

    j = threading.Thread(target=ttl_janitor_loop, daemon=True)
    j.start()