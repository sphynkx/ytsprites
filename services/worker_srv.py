import os
import threading
import time
from typing import List

import grpc

from config.service_cfg import cfg
from runtime.queue_rt import job_manager
from runtime.models_rt import JobResult
from proto.ytsprites_pb2 import JobState, ArtifactRef
from utils import files_ut, ffmpeg_ut

import proto.ytstorage_pb2 as spb  # type: ignore
import proto.ytstorage_pb2_grpc as sgrpc  # type: ignore


def _ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def log(msg: str):
    print(f"[{_ts()}] [ytsprites] {msg}")


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


def _auth_md(token: str) -> list[tuple]:
    t = (token or "").strip()
    if not t:
        return []
    return [("authorization", f"Bearer {t}")]


def _grpc_opts() -> list[tuple]:
    max_mb = int(os.getenv("YTSPRITES_YTSTORAGE_GRPC_MAX_MB", "64"))
    max_msg = max_mb * 1024 * 1024
    return [
        ("grpc.max_send_message_length", max_msg),
        ("grpc.max_receive_message_length", max_msg),
    ]


def _make_channel(addr: str, tls: bool) -> grpc.Channel:
    if tls:
        return grpc.secure_channel(addr, grpc.ssl_channel_credentials(), options=_grpc_opts())
    return grpc.insecure_channel(addr, options=_grpc_opts())


def _download_to_file(*, storage_addr: str, tls: bool, token: str, rel_path: str, dst_abs: str, on_tick=None) -> int:
    rel_path = _norm_rel(rel_path)
    os.makedirs(os.path.dirname(dst_abs), exist_ok=True)

    md = _auth_md(token)
    bytes_written = 0

    with _make_channel(storage_addr, tls) as channel:
        stub = sgrpc.StorageServiceStub(channel)
        stream = stub.Read(spb.ReadRequest(path=spb.Path(rel_path=rel_path), offset=0, length=-1), metadata=md)
        with open(dst_abs, "wb") as f:
            for ch in stream:
                data = bytes(getattr(ch, "data", b"") or b"")
                if not data:
                    continue
                f.write(data)
                bytes_written += len(data)
                if on_tick and bytes_written and (bytes_written % (128 * 1024 * 1024) == 0):
                    on_tick(bytes_written)

    return bytes_written


def _mkdirs(*, storage_addr: str, tls: bool, token: str, rel_path: str) -> None:
    rel_path = _norm_rel(rel_path)
    md = _auth_md(token)
    with _make_channel(storage_addr, tls) as channel:
        stub = sgrpc.StorageServiceStub(channel)
        stub.Mkdirs(spb.MkdirsRequest(path=spb.Path(rel_path=rel_path), exist_ok=True), metadata=md)


def _upload_file(*, storage_addr: str, tls: bool, token: str, rel_path: str, src_abs: str) -> int:
    rel_path = _norm_rel(rel_path)
    md = _auth_md(token)

    def gen():
        yield spb.WriteEnvelope(
            header=spb.WriteHeader(
                path=spb.Path(rel_path=rel_path),
                overwrite=True,
                append=False,
                expected_size=0,
                etag="",
            )
        )
        with open(src_abs, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                yield spb.WriteEnvelope(data=spb.WriteData(data=chunk))

    last_ack = None
    with _make_channel(storage_addr, tls) as channel:
        stub = sgrpc.StorageServiceStub(channel)
        for ack in stub.Write(gen(), metadata=md):
            last_ack = ack

    if last_ack and getattr(last_ack, "ok", False):
        return int(getattr(last_ack, "bytes_written", 0) or 0)

    raise RuntimeError(f"upload failed: {getattr(last_ack, 'error', '')}")


def worker_loop(worker_id: int):
    log(f"[Worker-{worker_id}] Started")

    while True:
        job = job_manager.pop_next_job()
        if not job:
            time.sleep(0.5)
            continue

        if job.state == JobState.JOB_STATE_CANCELED:
            continue

        workspace = job.temp_dir_path
        local_video_path = job.source_file_path

        try:
            job.processing_started_at = time.time()
            job.update_status(JobState.JOB_STATE_PROCESSING, 0, "Starting...")

            if not workspace or not local_video_path:
                raise RuntimeError("Workspace not initialized")

            if not job.source or not job.output:
                raise RuntimeError("Missing source/output refs")

            src_storage = job.source.storage
            out_storage = job.output.storage

            src_rel = job.source.rel_path
            out_base = job.output.base_rel_dir
            sprites_dir = job.output.sprites_rel_dir or "sprites"
            vtt_name = job.output.vtt_name or "sprites.vtt"

            if not src_storage.address:
                raise RuntimeError("source.storage.address is empty")
            if not out_storage.address:
                raise RuntimeError("output.storage.address is empty")
            if not src_rel:
                raise RuntimeError("source.rel_path is empty")
            if not out_base:
                raise RuntimeError("output.base_rel_dir is empty")

            def on_progress(pct: int, msg: str):
                if job.state == JobState.JOB_STATE_CANCELED:
                    raise InterruptedError("Job canceled")
                job.update_status(JobState.JOB_STATE_PROCESSING, int(pct), msg)

            on_progress(1, "Downloading source from storage...")

            def _tick(bytes_dl: int):
                job.bytes_processed = int(bytes_dl)
                job.update_status(JobState.JOB_STATE_PROCESSING, 5, f"Downloading... {bytes_dl//(1024*1024)}MB")

            bytes_dl = _download_to_file(
                storage_addr=src_storage.address,
                tls=bool(src_storage.tls),
                token=str(src_storage.token or ""),
                rel_path=src_rel,
                dst_abs=local_video_path,
                on_tick=_tick,
            )
            job.bytes_processed = int(bytes_dl)
            on_progress(10, "Downloaded. Processing...")

            sprite_files_abs, vtt_text = ffmpeg_ut.process_video(local_video_path, workspace, job.options, on_progress)

            vtt_abs = os.path.join(workspace, "sprites.vtt")
            with open(vtt_abs, "w", encoding="utf-8") as f:
                f.write(vtt_text or "")

            on_progress(85, "Uploading results...")

            out_sprites_rel_dir = _join_rel(out_base, sprites_dir)
            _mkdirs(
                storage_addr=out_storage.address,
                tls=bool(out_storage.tls),
                token=str(out_storage.token or ""),
                rel_path=out_sprites_rel_dir,
            )

            vtt_rel = _join_rel(out_base, vtt_name)
            vtt_size = _upload_file(
                storage_addr=out_storage.address,
                tls=bool(out_storage.tls),
                token=str(out_storage.token or ""),
                rel_path=vtt_rel,
                src_abs=vtt_abs,
            )

            sprite_refs: List[ArtifactRef] = []
            total_sprite_bytes = 0
            for abs_path in sprite_files_abs:
                if not os.path.exists(abs_path):
                    continue
                name = os.path.basename(abs_path)
                rel = _join_rel(out_sprites_rel_dir, name)
                size = _upload_file(
                    storage_addr=out_storage.address,
                    tls=bool(out_storage.tls),
                    token=str(out_storage.token or ""),
                    rel_path=rel,
                    src_abs=abs_path,
                )
                total_sprite_bytes += int(size)
                sprite_refs.append(ArtifactRef(rel_path=rel, name=name, size_bytes=int(size)))

            job.processing_finished_at = time.time()
            dt = job.processing_finished_at - job.processing_started_at

            job.result = JobResult(
                video_id=job.video_id,
                state=JobState.JOB_STATE_DONE,
                message="OK",
                vtt=ArtifactRef(rel_path=vtt_rel, name=vtt_name, size_bytes=int(vtt_size)),
                sprites=sprite_refs,
                sprites_count=int(len(sprite_refs)),
                total_sprite_bytes=int(total_sprite_bytes),
                seconds=float(dt),
            )

            job.update_status(JobState.JOB_STATE_DONE, 100, "Done")
            log(f"[Worker-{worker_id}] DONE job_id={job.job_id} sprites={len(sprite_refs)} sec={dt:.2f}")

        except InterruptedError:
            log(f"[Worker-{worker_id}] CANCELED job_id={job.job_id}")
        except Exception as e:
            log(f"[Worker-{worker_id}] FAILED job_id={job.job_id} err={e!r}")
            import traceback
            traceback.print_exc()
            job.result = JobResult(video_id=job.video_id, state=JobState.JOB_STATE_FAILED, message=str(e))
            job.update_status(JobState.JOB_STATE_FAILED, 0, str(e))
        finally:
            if workspace:
                files_ut.cleanup_workspace(workspace)
                log(f"[Worker-{worker_id}] Cleaned workspace job_id={job.job_id} path={workspace}")


def ttl_janitor_loop():
    interval = min(max(cfg.JOB_TTL_SEC // 10, 30), 300)  # 30s..5m
    log(f"[Janitor] Started ttl={cfg.JOB_TTL_SEC}s interval={interval}s")
    while True:
        try:
            removed = job_manager.cleanup_expired(cfg.JOB_TTL_SEC)
            if removed:
                log(f"[Janitor] Removed expired jobs: {removed}")
        except Exception as e:
            log(f"[Janitor] cleanup_expired failed: {e}")
        time.sleep(interval)


def start_workers():
    for i in range(cfg.MAX_WORKERS):
        t = threading.Thread(target=worker_loop, args=(i,), daemon=True)
        t.start()

    j = threading.Thread(target=ttl_janitor_loop, daemon=True)
    j.start()