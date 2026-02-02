import os
import threading
import time
import uuid
from collections import deque
from typing import Dict, Optional, Tuple

from config.service_cfg import cfg
from proto.ytsprites_pb2 import JobState, SourceRef, OutputRef
from utils import files_ut
from .models_rt import Job


def _norm_rel(p: str) -> str:
    return (p or "").strip().replace("\\", "/").lstrip("/")


class JobManager:
    def __init__(self, max_queue=100):
        self._jobs: Dict[str, Job] = {}
        self._queue: deque = deque()
        self._max_queue = max_queue
        self._lock = threading.RLock()

        # Dedupe index: (video_id, source_rel, out_base) -> job_id
        self._active_by_key: Dict[Tuple[str, str, str], str] = {}

    def _dedupe_key(
        self,
        *,
        video_id: str,
        source: Optional[SourceRef],
        output: Optional[OutputRef],
    ) -> Tuple[str, str, str]:
        src_rel = _norm_rel(source.rel_path) if source else ""
        out_base = _norm_rel(output.base_rel_dir) if output else ""
        return (str(video_id or ""), src_rel, out_base)

    def create_job(
        self,
        *,
        video_id: str,
        options,
        filename: str = "",
        video_mime: str = "",
        source: Optional[SourceRef] = None,
        output: Optional[OutputRef] = None,
    ) -> Optional[str]:
        with self._lock:
            if len(self._jobs) >= self._max_queue * 3:
                return None

            # --- Dedupe: one active job per (video_id, source_rel, out_base) ---
            key = self._dedupe_key(video_id=video_id, source=source, output=output)
            existing_id = self._active_by_key.get(key)
            if existing_id:
                j = self._jobs.get(existing_id)
                if j and j.state not in (JobState.JOB_STATE_DONE, JobState.JOB_STATE_FAILED, JobState.JOB_STATE_CANCELED):
                    return existing_id
                # stale index
                try:
                    del self._active_by_key[key]
                except Exception:
                    pass

            job_id = str(uuid.uuid4())
            job = Job(
                job_id=job_id,
                video_id=video_id,
                options=options,
                filename=filename or "",
                video_mime=video_mime or "",
                source=source,
                output=output,
            )

            workspace = files_ut.create_job_workspace(job_id)
            source_path = os.path.join(workspace, "source.bin")
            job.temp_dir_path = workspace
            job.source_file_path = source_path

            job.update_status(JobState.JOB_STATE_SUBMITTED, 0, "Created")
            self._jobs[job_id] = job

            # store dedupe mapping
            self._active_by_key[key] = job_id

            # New protocol: enqueue immediately (no UploadSource)
            job.update_status(JobState.JOB_STATE_QUEUED, 0, "Queued")
            self._queue.append(job_id)

            return job_id

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def pop_next_job(self) -> Optional[Job]:
        with self._lock:
            if not self._queue:
                return None

            job_id = self._queue.popleft()
            job = self._jobs.get(job_id)

            if job and job.state == JobState.JOB_STATE_CANCELED:
                return self.pop_next_job()

            return job

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            job.update_status(JobState.JOB_STATE_CANCELED, job.percent, "Canceled by user")

            # cleanup temp dir best-effort
            if job.temp_dir_path:
                files_ut.cleanup_workspace(job.temp_dir_path)

            # drop dedupe key
            try:
                key = self._dedupe_key(video_id=job.video_id, source=job.source, output=job.output)
                if self._active_by_key.get(key) == job_id:
                    del self._active_by_key[key]
            except Exception:
                pass

            return True

    def cleanup_expired(self, ttl_sec: int) -> int:
        """
        Cleanup non-processing jobs that have been inactive (updated_at) longer than ttl_sec.
        """
        now = time.time()
        removed = 0
        with self._lock:
            to_delete = []
            for job_id, job in self._jobs.items():
                # never delete active processing jobs
                if job.state == JobState.JOB_STATE_PROCESSING:
                    continue

                # TTL by last activity, not by creation time
                age = now - float(getattr(job, "updated_at", job.created_at) or job.created_at)
                if age >= ttl_sec:
                    to_delete.append(job_id)

            for job_id in to_delete:
                job = self._jobs.pop(job_id, None)
                if not job:
                    continue
                removed += 1

                # remove from dedupe index if still mapped
                try:
                    key = self._dedupe_key(video_id=job.video_id, source=job.source, output=job.output)
                    if self._active_by_key.get(key) == job_id:
                        del self._active_by_key[key]
                except Exception:
                    pass

                if job.temp_dir_path:
                    files_ut.cleanup_workspace(job.temp_dir_path)

                # remove from queue if present
                try:
                    while True:
                        self._queue.remove(job_id)
                except ValueError:
                    pass

        return removed


job_manager = JobManager(max_queue=cfg.MAX_QUEUE_SIZE)