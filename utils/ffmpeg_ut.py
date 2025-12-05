import os
import math
import subprocess
import shutil
from typing import List, Tuple, Optional
from PIL import Image
from config.service_cfg import cfg

DEFAULT_TILE_W = 160
DEFAULT_TILE_H = 90


def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)


def run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    """Run ext cmd syncly."""
    # print(f"[CMD] {' '.join(cmd)}")
    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False
        )
        return res.returncode, res.stdout.decode('utf-8', 'ignore'), res.stderr.decode('utf-8', 'ignore')
    except FileNotFoundError:
        return -1, "", "Command not found"


def probe_duration_sec(src: str) -> float | None:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        src,
    ]
    code, out, err = run_cmd(cmd)
    if code == 0:
        try:
            return float(out.strip())
        except Exception:
            pass
    else:
        print(f"[FFPROBE DURATION ERROR] {err[:200]}")
    return None


def probe_video_dims(src: str) -> Tuple[Optional[int], Optional[int]]:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        src,
    ]
    code, out, err = run_cmd(cmd)
    if code == 0:
        line = out.strip()
        if "x" in line:
            try:
                w, h = line.split("x", 1)
                return int(w), int(h)
            except Exception:
                pass
    else:
        print(f"[FFPROBE DIMS ERROR] {err[:200]}")
    return None, None


def extract_frames(src: str, out_dir: str, interval_sec: float, tile_w: int, tile_h: int):
    ensure_dir(out_dir)
    # scale + pad to maintain aspect ratio in tiles
    vf = f"scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color=black,fps=1/{interval_sec}"
    out_pattern = os.path.join(out_dir, "frame_%05d.jpg")
    
    cmd = [
        "ffmpeg", "-y",
        "-i", src,
        "-loglevel", "error",
        "-vf", vf,
        out_pattern,
    ]
    print(f"[FFMPEG CMD] {' '.join(cmd)}")
    
    code, _, err = run_cmd(cmd)
    if code != 0:
        raise RuntimeError(f"ffmpeg extract failed: {err[:300]}")
    print("[FFMPEG OK] frames extracted")

def list_frames(frames_dir: str) -> List[str]:
    if not os.path.exists(frames_dir):
        return []
    files = [f for f in os.listdir(frames_dir) if f.lower().startswith("frame_") and f.lower().endswith(".jpg")]
    files.sort()
    return [os.path.join(frames_dir, f) for f in files]

def pack_sprites(
    frames: List[str],
    sprites_dir: str,
    cols: int,
    rows: int,
    tile_w: int,
    tile_h: int,
    quality: int = 85,
) -> List[str]:
    ensure_dir(sprites_dir)
    per_sprite = cols * rows
    sprite_paths: List[str] = []
    
    # Split the frames to chunks (one per sprite sheet)
    chunks_count = math.ceil(len(frames) / per_sprite)
    
    for sidx in range(chunks_count):
        chunk = frames[sidx*per_sprite : (sidx+1)*per_sprite]
        if not chunk:
            break
        
        # Create canvas
        sprite = Image.new("RGB", (cols*tile_w, rows*tile_h), (0, 0, 0))
        
        for i, fp in enumerate(chunk):
            try:
                with Image.open(fp) as img:
                    img = img.convert("RGB")
                    # Grid coords
                    x = (i % cols) * tile_w
                    y = (i // cols) * tile_h
                    sprite.paste(img, (x, y))
            except Exception as e:
                print(f"[SPRITE FRAME ERROR] {fp}: {e}")
                continue
        
        out_name = f"sprite_{sidx+1:04d}.jpg"
        out_path = os.path.join(sprites_dir, out_name)
        
        # Save
        sprite.save(out_path, format='JPEG', quality=quality, optimize=True)
        sprite_paths.append(out_path)
        
    print(f"[SPRITES BUILT] count={len(sprite_paths)}")
    return sprite_paths

def sec_fmt(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    ss = s - h*3600 - m*60
    return f"{h:02d}:{m:02d}:{ss:06.3f}"

def generate_vtt(
    total_frames: int,
    interval_sec: float,
    cols: int,
    rows: int,
    tile_w: int,
    tile_h: int,
) -> str:
    per_sprite = cols * rows
    lines = ["WEBVTT", ""]
    
    for i in range(total_frames):
        start = i * interval_sec
        end = (i + 1) * interval_sec
        
        # Sprite index and img index inside of sprite
        sidx = i // per_sprite
        idx = i % per_sprite
        
        x = (idx % cols) * tile_w
        y = (idx // cols) * tile_h
        
        # Rel path to  VTT
        sprite_name = f"sprite_{sidx+1:04d}.jpg"
        
        lines.append(f"{sec_fmt(start)} --> {sec_fmt(end)}")
        lines.append(f"{sprite_name}#xywh={x},{y},{tile_w},{tile_h}")
        lines.append("")
        
    return "\n".join(lines)

def process_video(video_path: str, workspace: str, options, progress_cb) -> Tuple[List[str], str]:
    """
    Base pipline.
    options: SpriteOptions (step_sec, cols, rows, format, quality)
    Returns list of absolute paths to sprites, vtt content
    """
    
    # 1. Check input data
    size_bytes = os.path.getsize(video_path)
    w0, h0 = probe_video_dims(video_path)
    dur = probe_duration_sec(video_path)
    
    print(f"[SOURCE OK] path={video_path} size={size_bytes} dims={w0}x{h0} dur={dur}")
    
    interval = options.step_sec if options.step_sec > 0 else cfg.DEFAULT_STEP_SEC
    cols = options.cols if options.cols > 0 else cfg.DEFAULT_COLS
    rows = options.rows if options.rows > 0 else cfg.DEFAULT_ROWS
    tile_w = DEFAULT_TILE_W
    tile_h = DEFAULT_TILE_H
    
    print(f"[PARAMS] interval={interval} tw={tile_w} th={tile_h} cols={cols} rows={rows}")
    
    progress_cb(10, "Extracting frames...")
    
    # 2. Frames extraction
    frames_dir = os.path.join(workspace, "frames_tmp")
    extract_frames(video_path, frames_dir, interval, tile_w, tile_h)
    
    frames = list_frames(frames_dir)
    print(f"[FRAMES FOUND] count={len(frames)}")
    if not frames:
        raise RuntimeError("No frames extracted")
        
    progress_cb(50, "Building sprites...")
    
    # 3. Compile sprites
    sprites_dir = os.path.join(workspace, "sprites")
    quality = options.quality if options.quality > 0 else 85
    
    sprite_paths = pack_sprites(frames, sprites_dir, cols, rows, tile_w, tile_h, quality)
    
    # 4. Generate VTT
    vtt_content = generate_vtt(len(frames), interval, cols, rows, tile_w, tile_h)
    
    progress_cb(90, "Finalizing...")
    
    # Clean workspace
    shutil.rmtree(frames_dir, ignore_errors=True)
    
    return sprite_paths, vtt_content