import os
import shutil
import tempfile
from config.service_cfg import cfg

def job_workspace_path(job_id: str) -> str:
    base = cfg.TMP_DIR
    return os.path.join(base if base else tempfile.gettempdir(), f"ytsprites_{job_id}")

def create_job_workspace(job_id: str) -> str:
    """Create temp dir for task"""
    path = job_workspace_path(job_id)
    os.makedirs(path, exist_ok=True)
    return path

def cleanup_workspace(path: str):
    """Remove temp dir."""
    if path and os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)

def save_bytes_to_file(path: str, data: bytes):
    with open(path, "wb") as f:
        f.write(data)

def safe_unlink(path: str):
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass

def open_spooled_file_for_write(path: str, offset: int):
    """
    Open file for random-access write (resume/offset support).
    Creates parent dir if needed.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    f = open(path, "r+b" if os.path.exists(path) else "w+b")
    f.seek(offset)
    return f