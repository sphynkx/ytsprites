import os
import threading
import time
import uuid
from collections import deque
from typing import Dict, Optional

from config.service_cfg import cfg
from .models_rt import Job
from proto.ytsprites_pb2 import JobState
from utils import files_ut


class JobManager:
    def __init__(self, max_queue=100):
        self._jobs: Dict[str, Job] = {}
        self._queue: deque = deque()
        self._max_queue = max_queue
        self._lock = threading.RLock()

    def create_job(self, video_id, mime, options, filename: str = "") -> Optional[str]:
        with self._lock:
            if len(self._jobs) >= self._max_queue * 3:
                return None

            job_id = str(uuid.uuid4())
            job = Job(
                job_id=job_id,
                video_id=video_id,
                video_mime=mime,
                options=options,
                filename=filename or "",
            )

            workspace = files_ut.create_job_workspace(job_id)
            source_path = os.path.join(workspace, "source.bin")

            job.temp_dir_path = workspace
            job.source_file_path = source_path

            job.state = JobState.JOB_STATE_SUBMITTED
            job.percent = 0
            job.message = "Created"

            self._jobs[job_id] = job
            return job_id

    def get_job(self, job_id) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def mark_upload_complete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.state == JobState.JOB_STATE_CANCELED:
                return False
            if job.upload_done:
                return True

            job.upload_done = True
            job.upload_finished_at = time.time()
            job.update_status(JobState.JOB_STATE_QUEUED, 0, "Queued")
            self._queue.append(job_id)
            return True

    def pop_next_job(self) -> Optional[Job]:
        with self._lock:
            if not self._queue:
                return None

            job_id = self._queue.popleft()
            job = self._jobs.get(job_id)

            if job and job.state == JobState.JOB_STATE_CANCELED:
                return self.pop_next_job()

            return job

    def cancel_job(self, job_id) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            job.state = JobState.JOB_STATE_CANCELED
            job.message = "Canceled by user"
            job.updated_at = time.time()

            if job.temp_dir_path:
                files_ut.cleanup_workspace(job.temp_dir_path)

            return True

    def get_queue_position(self, job_id) -> int:
        with self._lock:
            try:
                return self._queue.index(job_id) + 1
            except ValueError:
                return 0

    def cleanup_expired(self, ttl_sec: int) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            to_delete = []
            for job_id, job in self._jobs.items():
                if job.state in (JobState.JOB_STATE_PROCESSING,):
                    continue

                age = now - job.created_at
                if age >= ttl_sec:
                    to_delete.append(job_id)

            for job_id in to_delete:
                job = self._jobs.pop(job_id, None)
                removed += 1
                if job and job.temp_dir_path:
                    files_ut.cleanup_workspace(job.temp_dir_path)
                try:
                    while True:
                        self._queue.remove(job_id)
                except ValueError:
                    pass

        return removed


job_manager = JobManager(max_queue=cfg.MAX_QUEUE_SIZE)