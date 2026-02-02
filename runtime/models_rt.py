import time
from dataclasses import dataclass, field
from typing import List, Optional

from proto.ytsprites_pb2 import JobState, SpriteOptions, SourceRef, OutputRef, ArtifactRef


@dataclass
class JobResult:
    video_id: str
    state: int
    message: str

    vtt: Optional[ArtifactRef] = None
    sprites: List[ArtifactRef] = field(default_factory=list)

    sprites_count: int = 0
    total_sprite_bytes: int = 0
    seconds: float = 0.0


@dataclass
class Job:
    job_id: str
    video_id: str
    options: SpriteOptions

    # Optional meta
    filename: str = ""
    video_mime: str = ""

    # Storage-driven I/O refs
    source: Optional[SourceRef] = None
    output: Optional[OutputRef] = None

    # Internal paths
    temp_dir_path: Optional[str] = None
    source_file_path: Optional[str] = None  # local downloaded source.bin

    # State
    state: int = JobState.JOB_STATE_SUBMITTED
    percent: int = 0
    message: str = ""

    bytes_processed: int = 0

    processing_started_at: float = 0.0
    processing_finished_at: float = 0.0

    result: Optional[JobResult] = None

    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def update_status(self, state: int, percent: int, msg: str = "") -> None:
        self.state = state
        self.percent = percent
        self.message = msg
        self.updated_at = time.time()