import os
import tempfile
from dotenv import load_dotenv

load_dotenv()

class Config:
    # gRPC settings
    ##GRPC_PORT = int(os.getenv('GRPC_PORT', 9094))
    GRPC_PORT = int(os.getenv("PORT", 9094))
    GRPC_HOST = os.getenv("HOST", "0.0.0.0")
    MAX_WORKERS = int(os.getenv('MAX_WORKERS', 2))
    
    # Runtime limits
    MAX_QUEUE_SIZE = 100
    MAX_VIDEO_SIZE_MB = 500
    
    # Temp paths
    # If None use system temp.
    TMP_DIR = os.getenv('TMP_DIR', None) 
    
    # Defaults for generation
    DEFAULT_STEP_SEC = 2.0
    DEFAULT_COLS = 10
    DEFAULT_ROWS = 10
    DEFAULT_FORMAT = 'jpg'
    DEFAULT_QUALITY = 70

cfg = Config()