import os
import shutil
import tempfile
from config.service_cfg import cfg

def create_job_workspace(job_id: str):
    """Create temp dir for task"""
    base = cfg.TMP_DIR
    path = os.path.join(base if base else tempfile.gettempdir(), f"ytsprites_{job_id}")
    os.makedirs(path, exist_ok=True)
    return path

def cleanup_workspace(path: str):
    """Remove temp dir."""
    if path and os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)

def save_bytes_to_file(path: str, data: bytes):
    with open(path, 'wb') as f:
        f.write(data)