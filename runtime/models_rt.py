import time
from dataclasses import dataclass, field
from typing import List, Optional
from proto.ytsprites_pb2 import JobState, SpriteOptions

@dataclass
class JobResult:
    sprites: List[tuple]
    vtt_content: str
    video_id: str

@dataclass
class Job:
    job_id: str
    video_id: str
    video_mime: str
    options: SpriteOptions

    # Optional meta
    filename: str = ""

    # Internal paths
    temp_dir_path: Optional[str] = None
    source_file_path: Optional[str] = None  # uploaded bytes spool file

    # Upload tracking
    bytes_received: int = 0
    upload_done: bool = False
    upload_started_at: float = 0.0
    upload_finished_at: float = 0.0

    # State
    state: int = JobState.JOB_STATE_SUBMITTED
    percent: int = 0
    message: str = ""

    # Processing timing (optional)
    processing_started_at: float = 0.0
    processing_finished_at: float = 0.0

    # Save result in mem or links to files until GetResult or timeout
    result: Optional[JobResult] = None

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def update_status(self, state, percent, msg=""):
        self.state = state
        self.percent = percent
        self.message = msg
        self.updated_at = time.time()