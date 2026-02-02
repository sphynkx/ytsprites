import os
import time
import threading
from typing import List, Tuple, Optional, AsyncIterator

from proto.ytsprites_pb2 import JobState, ArtifactRef
from runtime.queue_rt import job_manager
from runtime.models_rt import Job, JobResult
from services.ytstorage_client_srv import YtStorageClient


def _norm_rel(p: str) -> str:
    return (p or "").strip().replace("\\", "/").lstrip("/")


def _join_rel(a: str, b: str) -> str:
    a = _norm_rel(a)
    b = _norm_rel(b)
    if not a:
        return b
    if not b:
        return a
    return f"{a}/{b}"


async def _aiter_file(path: str, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    with open(path, "rb") as f:
        while True:
            ch = f.read(chunk_size)
            if not ch:
                break
            yield ch


def _run_generator(job: Job, workspace: str) -> Tuple[str, List[str], int]:
    """
    HOOK: run ffmpeg/sprites builder and return:
      - vtt_local_path (abs)
      - sprite_local_paths (abs list)
      - total_sprite_bytes
    Replace this stub with your existing generator pipeline.

    Expected output layout is up to you; simplest:
      workspace/out/sprites.vtt
      workspace/out/sprites/sprite_0001.jpg ...
    """
    # TODO: integrate actual existing generator.
    raise NotImplementedError("Integrate your sprites generator pipeline here")


async def process_job(job: Job) -> None:
    if not job.source or not job.output:
        job.update_status(JobState.JOB_STATE_FAILED, 0, "Missing source/output in job")
        return

    source_addr = job.source.storage.address
    source_token = job.source.storage.token
    source_tls = bool(job.source.storage.tls)

    out_addr = job.output.storage.address
    out_token = job.output.storage.token
    out_tls = bool(job.output.storage.tls)

    if not source_addr or not out_addr:
        job.update_status(JobState.JOB_STATE_FAILED, 0, "storage.address is required")
        return

    src_rel = _norm_rel(job.source.rel_path)
    out_base = _norm_rel(job.output.base_rel_dir)
    sprites_dir = _norm_rel(job.output.sprites_rel_dir or "sprites")
    vtt_name = (job.output.vtt_name or "sprites.vtt").strip() or "sprites.vtt"

    workspace = job.temp_dir_path or ""
    src_local = job.source_file_path or ""
    if not workspace or not src_local:
        job.update_status(JobState.JOB_STATE_FAILED, 0, "Internal workspace not initialized")
        return

    job.processing_started_at = time.time()
    job.update_status(JobState.JOB_STATE_PROCESSING, 1, "Downloading source from storage...")

    storage_in = YtStorageClient(source_addr, token=source_token, tls=source_tls)
    storage_out = storage_in
    if out_addr != source_addr or out_token != source_token or out_tls != source_tls:
        storage_out = YtStorageClient(out_addr, token=out_token, tls=out_tls)

    bytes_dl = 0
    try:
        # Download source to local file
        os.makedirs(os.path.dirname(src_local), exist_ok=True)
        with open(src_local, "wb") as f:
            async for chunk in storage_in.read(src_rel):
                f.write(chunk)
                bytes_dl += len(chunk)
                # very rough progress (download only)
                if bytes_dl and bytes_dl % (128 * 1024 * 1024) == 0:
                    job.update_status(JobState.JOB_STATE_PROCESSING, 5, "Downloading source...", bytes_processed=bytes_dl)

        job.update_status(JobState.JOB_STATE_PROCESSING, 10, "Source downloaded. Generating sprites...", bytes_processed=bytes_dl)

        # Run generator (ffmpeg/build) - sync hook for now
        vtt_abs, sprite_abs_list, total_sprite_bytes = _run_generator(job, workspace)

        job.update_status(JobState.JOB_STATE_PROCESSING, 80, "Uploading results to storage...", bytes_processed=bytes_dl)

        # Ensure output dirs
        out_sprites_rel_dir = _join_rel(out_base, sprites_dir)
        await storage_out.mkdirs(out_sprites_rel_dir, exist_ok=True)

        # Upload VTT
        vtt_rel = _join_rel(out_base, vtt_name)
        vtt_ack = await storage_out.write_bytes(vtt_rel, _aiter_file(vtt_abs), overwrite=True)
        if not vtt_ack.ok:
            raise RuntimeError(f"VTT upload failed: {vtt_ack.error}")

        # Upload sprites
        sprite_refs: List[ArtifactRef] = []
        for abs_path in sprite_abs_list:
            fname = os.path.basename(abs_path)
            rel = _join_rel(out_sprites_rel_dir, fname)
            ack = await storage_out.write_bytes(rel, _aiter_file(abs_path), overwrite=True)
            if not ack.ok:
                raise RuntimeError(f"Sprite upload failed: {ack.error}")
            sprite_refs.append(ArtifactRef(rel_path=rel, name=fname, size_bytes=int(ack.bytes_written)))

        seconds = time.time() - job.processing_started_at
        job.processing_finished_at = time.time()

        job.result = JobResult(
            video_id=job.video_id,
            state=JobState.JOB_STATE_DONE,
            message="OK",
            vtt=ArtifactRef(rel_path=vtt_rel, name=vtt_name, size_bytes=int(vtt_ack.bytes_written)),
            sprites=sprite_refs,
            sprites_count=len(sprite_refs),
            total_sprite_bytes=int(total_sprite_bytes),
            seconds=float(seconds),
        )
        job.update_status(JobState.JOB_STATE_DONE, 100, "Done", bytes_processed=bytes_dl)

    except Exception as e:
        job.result = JobResult(
            video_id=job.video_id,
            state=JobState.JOB_STATE_FAILED,
            message=str(e),
        )
        job.update_status(JobState.JOB_STATE_FAILED, job.percent, f"Failed: {e}")

    finally:
        try:
            await storage_in.close()
        except Exception:
            pass
        if storage_out is not storage_in:
            try:
                await storage_out.close()
            except Exception:
                pass


class WorkerThread(threading.Thread):
    def __init__(self, name: str = "Worker-0", poll_sec: float = 0.2):
        super().__init__(name=name, daemon=True)
        self._poll_sec = poll_sec
        self._stop = threading.Event()

    def run(self) -> None:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        while not self._stop.is_set():
            job = job_manager.pop_next_job()
            if not job:
                time.sleep(self._poll_sec)
                continue

            if job.state == JobState.JOB_STATE_CANCELED:
                continue

            loop.run_until_complete(process_job(job))

        try:
            loop.close()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()