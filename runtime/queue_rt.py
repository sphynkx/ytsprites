import threading
import uuid
from collections import deque
from typing import Dict, Optional

from .models_rt import Job
from proto.ytsprites_pb2 import JobState

class JobManager:
    def __init__(self, max_queue=100):
        self._jobs: Dict[str, Job] = {}
        self._queue: deque = deque()
        self._max_queue = max_queue
        self._lock = threading.RLock()

    def create_job(self, video_id, mime, options) -> Optional[str]:
        """Creates a task and adds it to the queue. Returns the job_id or None if the queue is full."""
        with self._lock:
            if len(self._queue) >= self._max_queue:
                return None
            
            job_id = str(uuid.uuid4())
            job = Job(job_id=job_id, video_id=video_id, video_mime=mime, options=options)
            job.state = JobState.JOB_STATE_QUEUED
            
            self._jobs[job_id] = job
            self._queue.append(job_id)
            return job_id

    def get_job(self, job_id) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def pop_next_job(self) -> Optional[Job]:
        """Takes the next task from the queue for the worker."""
        with self._lock:
            if not self._queue:
                return None
            
            job_id = self._queue.popleft()
            job = self._jobs.get(job_id)
            
            # Pass cancelled tasks
            if job and job.state == JobState.JOB_STATE_CANCELED:
                return self.pop_next_job()
                
            return job

    def cancel_job(self, job_id) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            # If the task is being processed, the worker should check it itself in the next step.
            job.state = JobState.JOB_STATE_CANCELED
            job.message = "Canceled by user"
            return True

    def get_queue_position(self, job_id) -> int:
        with self._lock:
            try:
                return self._queue.index(job_id) + 1
            except ValueError:
                return 0

# Global manager instance
job_manager = JobManager()