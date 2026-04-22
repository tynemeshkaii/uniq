"""
Video Uniqualization Engine — adapted from HYBRID V8.0 script.
Provides parameterized video processing via ffmpeg.
"""

import os
import sys
import random
import subprocess
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Callable

FAKE_CAMERAS = [
    {"make": "Apple",   "model": "iPhone 13 Pro",   "sw": "15.3.1"},
    {"make": "Apple",   "model": "iPhone 14 Pro",   "sw": "16.2"},
    {"make": "Apple",   "model": "iPhone 15",       "sw": "17.0.3"},
    {"make": "Samsung", "model": "Galaxy S22 Ultra", "sw": "Android 12"},
    {"make": "Samsung", "model": "Galaxy S23",       "sw": "Android 13"},
    {"make": "Google",  "model": "Pixel 7 Pro",      "sw": "Android 13"},
    {"make": "Google",  "model": "Pixel 8",          "sw": "Android 14"},
    {"make": "Sony",    "model": "Xperia 1 V",       "sw": "Android 13"},
    {"make": "OnePlus", "model": "11 5G",            "sw": "Android 13"},
    {"make": "Xiaomi",  "model": "13 Pro",           "sw": "Android 13"},
]

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920


def get_ffmpeg_path() -> str:
    """Return path to bundled ffmpeg, or system ffmpeg as fallback."""
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller bundle
        base = sys._MEIPASS
        ffmpeg = os.path.join(base, 'ffmpeg', 'ffmpeg')
        if os.path.isfile(ffmpeg):
            return ffmpeg
    # Fallback to system
    return 'ffmpeg'


def get_ffprobe_path() -> str:
    """Return path to bundled ffprobe, or system ffprobe as fallback."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
        ffprobe = os.path.join(base, 'ffmpeg', 'ffprobe')
        if os.path.isfile(ffprobe):
            return ffprobe
    return 'ffprobe'


def clamp(n, minn, maxn):
    return max(min(maxn, n), minn)


@dataclass
class UniqueParams:
    """All tuneable parameters for video uniqualization."""
    # Geometry
    k1: float = 0.0
    rotate: float = 0.0
    zoom: float = 1.03
    crop_margin: int = 8

    # Color
    rs: float = 0.0
    gs: float = 0.0
    bs: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    brightness: float = 0.0
    hue_shift: float = 0.0

    # Effects
    unsharp_amount: float = 0.1
    noise_strength: int = 8
    noise_flags: str = "t"
    do_hflip: bool = False
    vignette_angle: float = 0.15

    # Speed
    video_speed: float = 1.0

    # Trim
    trim_start: float = 0.3
    trim_end: float = 0.3

    # Audio
    pitch: float = 1.0
    adelay_ms: int = 100

    # Overlay
    overlay_file: str = ""
    opacity: float = 0.07
    ov_speed: float = 1.0

    # Output
    target_width: int = TARGET_WIDTH
    target_height: int = TARGET_HEIGHT
    crf: int = 26
    preset: str = "fast"

    # Meta
    fake_meta: bool = True

    @staticmethod
    def random_date_str() -> str:
        base = datetime.now()
        delta = timedelta(days=random.randint(1, 730))
        dt = base - delta
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    @classmethod
    def generate_random(cls) -> 'UniqueParams':
        """Generate a fully randomized parameter set (the V8.0 defaults)."""
        p = cls()
        p.k1 = round(random.uniform(-0.008, 0.008), 4)
        p.rotate = round(random.uniform(-0.7, 0.7), 3)
        p.zoom = round(random.uniform(1.02, 1.05), 3)
        p.crop_margin = random.randint(4, 16)

        p.rs = round(random.uniform(-0.10, 0.10), 3)
        p.gs = round(random.uniform(-0.10, 0.10), 3)
        p.bs = round(random.uniform(-0.10, 0.10), 3)
        p.contrast = round(random.uniform(0.93, 1.07), 3)
        p.saturation = round(random.uniform(0.88, 1.12), 3)
        p.brightness = round(random.uniform(-0.04, 0.04), 3)
        p.hue_shift = round(random.uniform(-6, 6), 1)

        p.unsharp_amount = round(random.uniform(-0.4, 0.6), 2)
        p.noise_strength = random.randint(4, 12)
        p.noise_flags = random.choice(["t", "u", "t+u"])
        p.do_hflip = random.choice([True, False])
        p.vignette_angle = round(random.uniform(0.05, 0.25), 2)

        p.video_speed = round(random.uniform(0.96, 1.04), 4)

        p.trim_start = round(random.uniform(0.1, 0.6), 2)
        p.trim_end = round(random.uniform(0.1, 0.5), 2)

        p.pitch = clamp(round(random.uniform(0.96, 1.04), 4), 0.9, 1.1)
        p.adelay_ms = random.randint(30, 180)

        p.opacity = round(random.uniform(0.04, 0.11), 2)
        p.ov_speed = round(random.uniform(0.88, 1.12), 2)

        return p


def check_audio_stream(file_path: str) -> bool:
    cmd = [
        get_ffprobe_path(), "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", file_path
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8", errors="ignore").strip()
        return len(out) > 0
    except Exception:
        return False


def get_video_duration(file_path: str) -> Optional[float]:
    cmd = [
        get_ffprobe_path(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0", file_path
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8", errors="ignore").strip()
        return float(out)
    except Exception:
        return None


def next_output_index(output_folder: str) -> int:
    max_index = 0
    if not os.path.exists(output_folder):
        return 1
    for name in os.listdir(output_folder):
        if name.startswith("uniq_") and name.endswith(".mp4"):
            try:
                middle = name.replace("uniq_", "").replace(".mp4", "")
                if middle.isdigit():
                    max_index = max(max_index, int(middle))
            except Exception:
                continue
    return max_index + 1


def process_single_video(
    input_path: str,
    output_folder: str,
    index: int,
    params: Optional[UniqueParams] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> bool:
    """
    Process a single video file.
    Returns True on success, False on failure.
    """
    if params is None:
        params = UniqueParams.generate_random()

    overlay_path = params.overlay_file if params.overlay_file else ""
    has_overlay = overlay_path and os.path.isfile(overlay_path)
    has_audio = check_audio_stream(input_path)
    duration = get_video_duration(input_path)

    p = params

    # Safety: trim should not eat more than 40% of video
    if duration and (p.trim_start + p.trim_end) >= duration * 0.4:
        p.trim_start = 0.1
        p.trim_end = 0.1

    output_filename = f"uniq_{index}.mp4"
    output_path = os.path.join(output_folder, output_filename)

    # TRIM
    trim_args = ["-ss", str(p.trim_start)]
    if duration:
        effective_duration = duration - p.trim_start - p.trim_end
        if effective_duration > 1.0:
            trim_args.extend(["-t", str(round(effective_duration, 3))])

    inputs = trim_args + ["-i", input_path]
    if has_overlay:
        inputs.extend(["-stream_loop", "-1", "-i", overlay_path])

    # VIDEO FILTERS
    flip_filter = "hflip," if p.do_hflip else ""
    tw, th = p.target_width, p.target_height
    cm = p.crop_margin
    cm = min(cm, (min(tw, th) // 2) - 1)
    cm = max(cm, 0)
    micro_crop = f"crop=iw-{cm*2}:ih-{cm*2}:{cm}:{cm},"

    fg_scale = (
        f"scale=iw*{p.zoom}:-1,"
        f"scale={tw}:{th}:force_original_aspect_ratio=decrease"
    )

    video_chain = (
        f"[0:v]split=2[bg_src][fg_src];"
        f"[bg_src]"
        f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
        f"crop={tw}:{th},"
        f"boxblur=20:2"
        f"[bg];"
        f"[fg_src]"
        f"setpts={round(1/p.video_speed, 6)}*PTS,"
        f"lenscorrection=k1={p.k1}:k2={p.k1},"
        f"{flip_filter}"
        f"rotate={p.rotate}*PI/180:fillcolor=none,"
        f"{micro_crop}"
        f"{fg_scale},"
        f"colorbalance=rs={p.rs}:gs={p.gs}:bs={p.bs},"
        f"hue=h={p.hue_shift},"
        f"eq=contrast={p.contrast}:saturation={p.saturation}:brightness={p.brightness},"
        f"unsharp=5:5:{p.unsharp_amount}:5:5:0.0,"
        f"noise=alls={p.noise_strength}:allf={p.noise_flags},"
        f"vignette=PI/5*{p.vignette_angle}"
        f"[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
    )

    if has_overlay:
        video_chain += (
            f";[1:v]"
            f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
            f"crop={tw}:{th},"
            f"setpts=PTS/{p.ov_speed},"
            f"format=yuva420p,"
            f"colorchannelmixer=aa={p.opacity}"
            f"[ov];"
            f"[base][ov]overlay=shortest=1[v_final]"
        )
    else:
        video_chain += ";[base]null[v_final]"

    # AUDIO
    if has_audio:
        combined_tempo = clamp(round((1 / p.pitch) * p.video_speed, 4), 0.5, 2.0)
        audio_chain = (
            f"[0:a]"
            f"adelay={p.adelay_ms}|{p.adelay_ms},"
            f"asetrate=44100*{p.pitch},"
            f"atempo={combined_tempo},"
            f"aresample=44100,"
            f"apad"
            f"[a_final]"
        )
    else:
        audio_chain = "anullsrc=channel_layout=stereo:sample_rate=44100[a_final]"

    filter_complex = f"{video_chain};{audio_chain}"

    # METADATA
    cam = random.choice(FAKE_CAMERAS) if p.fake_meta else {"make": "", "model": "", "sw": ""}
    date_str = UniqueParams.random_date_str()

    cmd = [
        get_ffmpeg_path(), "-y",
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[v_final]",
        "-map", "[a_final]",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", p.preset,
        "-crf", str(p.crf),
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-map_metadata", "-1",
    ]

    if p.fake_meta:
        cmd += [
            "-metadata", f"make={cam['make']}",
            "-metadata", f"model={cam['model']}",
            "-metadata", f"software={cam['sw']}",
            "-metadata", f"artist={cam['make']} {cam['model']}",
            "-metadata", f"comment={cam['sw']}",
            "-metadata", f"date={date_str}",
            "-metadata", f"creation_time={date_str}",
        ]

    cmd += [
        "-movflags", "+use_metadata_tags",
        "-loglevel", "error",
        output_path,
    ]

    if cancelled and cancelled():
        return False

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.returncode == 0
    except OSError as exc:
        print(f"[engine] ffmpeg failed to launch: {exc}", file=sys.stderr)
        return False


def process_batch(
    input_files: List[str],
    output_folder: str,
    params_template: Optional[UniqueParams] = None,
    randomize_each: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> int:
    """
    Process a batch of video files.
    Returns number of successfully processed files.

    progress_callback(current_index, total, filename)
    """
    os.makedirs(output_folder, exist_ok=True)
    start_index = next_output_index(output_folder)
    success_count = 0

    for i, fpath in enumerate(input_files):
        if cancelled and cancelled():
            break

        fname = os.path.basename(fpath)
        if progress_callback:
            progress_callback(i, len(input_files), fname)

        if randomize_each:
            params = UniqueParams.generate_random()
            # Copy non-random settings from template
            if params_template:
                params.overlay_file = params_template.overlay_file
                params.target_width = params_template.target_width
                params.target_height = params_template.target_height
                params.crf = params_template.crf
                params.preset = params_template.preset
                params.fake_meta = params_template.fake_meta
        else:
            params = params_template or UniqueParams.generate_random()

        ok = process_single_video(
            input_path=fpath,
            output_folder=output_folder,
            index=start_index + i,
            params=params,
            cancelled=cancelled,
        )
        if ok:
            success_count += 1

    if progress_callback:
        progress_callback(len(input_files), len(input_files), "")

    return success_count
