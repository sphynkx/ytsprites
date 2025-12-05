# DUMMY for future ffmpeg processing
import time

def process_video(video_path, output_dir, options, progress_callback):
    """
    Main video process (DUMMY).
    progress_callback(percent, message)
    Returns (list_of_sprite_files, vtt_string)
    """
    # Real ffmpeg call will be here!!
    # 1. Probe video (duration)
    # 2. Extract frames
    # 3. Tile frames
    # 4. Generate VTT
    
    # Immitate work..
    total_steps = 5
    for i in range(total_steps):
        time.sleep(1) # work
        pct = int((i + 1) / total_steps * 100)
        progress_callback(pct, f"Step {i+1}/{total_steps} processing")
    
    # Immitate result..
    vtt_mock = "WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nsprite_0001.jpg#xywh=0,0,120,68"
    sprites_mock = ["sprite_0001.jpg"] 
    
    return sprites_mock, vtt_mock