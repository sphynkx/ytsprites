import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # gRPC settings
    GRPC_PORT = int(os.getenv("PORT", 9094))
    GRPC_HOST = os.getenv("HOST", "0.0.0.0")
    MAX_WORKERS = int(os.getenv("MAX_WORKERS", 2))

    # Runtime limits
    MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", 100))

    # Legacy (keep, might still be used elsewhere)
    MAX_VIDEO_SIZE_MB = int(os.getenv("MAX_VIDEO_SIZE_MB", 500))

    # New: max upload bytes (prefer bytes env; fallback to MB)
    # Example: 2147483648 for 2GB
    MAX_UPLOAD_BYTES = int(
        os.getenv("YTSPRITES_MAX_UPLOAD_BYTES", str(MAX_VIDEO_SIZE_MB * 1024 * 1024))
    )

    # Temp paths
    # If None use system temp.
    TMP_DIR = os.getenv("TMP_DIR", None)

    # TTL cleanup for abandoned jobs (CreateJob but no upload, etc.)
    JOB_TTL_SEC = int(os.getenv("YTSPRITES_JOB_TTL_SEC", 60 * 60))  # 1h default

    # Defaults for generation
    DEFAULT_STEP_SEC = 2.0
    DEFAULT_COLS = 10
    DEFAULT_ROWS = 10
    DEFAULT_FORMAT = "jpg"
    DEFAULT_QUALITY = 70

cfg = Config()