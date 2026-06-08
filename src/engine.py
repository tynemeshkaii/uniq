"""
Video Uniqualization Engine — adapted from HYBRID V9.0 GOD MODE script.
Provides parameterized video processing via ffmpeg.
"""

import os
import re
import sys
import random
import signal
import subprocess
import threading
import tempfile
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Callable, Tuple

DEVICE_PROFILES = [
    {
        "make": "Apple", "model": "iPhone 13 Pro", "sw": "15.3.1",
        "encoder": "Apple QuickTime",
        "video_handler": "Core Media Video", "audio_handler": "Core Media Audio",
        "filename_style": "apple",
    },
    {
        "make": "Apple", "model": "iPhone 14 Pro", "sw": "16.2",
        "encoder": "Apple QuickTime",
        "video_handler": "Core Media Video", "audio_handler": "Core Media Audio",
        "filename_style": "apple",
    },
    {
        "make": "Apple", "model": "iPhone 15", "sw": "17.0.3",
        "encoder": "Apple QuickTime",
        "video_handler": "Core Media Video", "audio_handler": "Core Media Audio",
        "filename_style": "apple",
    },
    {
        "make": "Apple", "model": "iPhone 16 Pro", "sw": "18.1",
        "encoder": "Apple QuickTime",
        "video_handler": "Core Media Video", "audio_handler": "Core Media Audio",
        "filename_style": "apple",
    },
    {
        "make": "Samsung", "model": "Galaxy S22 Ultra", "sw": "Android 12",
        "encoder": "samsung",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung",
    },
    {
        "make": "Samsung", "model": "Galaxy S23", "sw": "Android 13",
        "encoder": "samsung",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung_vid",
    },
    {
        "make": "Samsung", "model": "Galaxy S24 Ultra", "sw": "Android 14",
        "encoder": "samsung",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung_vid",
    },
    {
        "make": "Google", "model": "Pixel 7 Pro", "sw": "Android 13",
        "encoder": "Google",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "pixel",
    },
    {
        "make": "Google", "model": "Pixel 8", "sw": "Android 14",
        "encoder": "Google",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "pixel",
    },
    {
        "make": "Sony", "model": "Xperia 1 V", "sw": "Android 13",
        "encoder": "sony",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung",
    },
    {
        "make": "OnePlus", "model": "11 5G", "sw": "Android 13",
        "encoder": "oneplus",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung_vid",
    },
    {
        "make": "Xiaomi", "model": "13 Pro", "sw": "Android 13",
        "encoder": "xiaomi",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "xiaomi",
    },
]

GPS_LOCATIONS = [
    (55.7558, 37.6173),
    (40.7128, -74.0060),
    (51.5074, -0.1278),
    (35.6762, 139.6503),
    (48.8566, 2.3522),
    (52.5200, 13.4050),
    (34.0522, -118.2437),
    (25.2048, 55.2708),
    (41.0082, 28.9784),
    (37.5665, 126.9780),
    (1.3521, 103.8198),
    (55.6761, 12.5683),
    (59.9343, 30.3351),
    (43.7102, 7.2620),
    (45.4642, 9.1900),
]

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+)\.(\d+)")
_FFMPEG_TIMEOUT = 3600
ENCODER_LIBX264 = "libx264"
ENCODER_H264_VIDEOTOOLBOX = "h264_videotoolbox"
ENCODER_HEVC_VIDEOTOOLBOX = "hevc_videotoolbox"

ENCODER_LABELS = {
    ENCODER_LIBX264: "Quality (libx264 CPU)",
    ENCODER_H264_VIDEOTOOLBOX: "Fast Mac (H.264 VideoToolbox)",
    ENCODER_HEVC_VIDEOTOOLBOX: "Fast Mac (HEVC VideoToolbox)",
}

PERFORMANCE_PROFILE_QUALITY = "quality"
PERFORMANCE_PROFILE_BALANCED = "balanced"
PERFORMANCE_PROFILE_FAST_MAC = "fast_mac"

PERFORMANCE_PROFILE_LABELS = {
    PERFORMANCE_PROFILE_QUALITY: "Max Quality",
    PERFORMANCE_PROFILE_BALANCED: "Balanced",
    PERFORMANCE_PROFILE_FAST_MAC: "Fast Mac",
}


def get_ffmpeg_path() -> str:
    """Return path to bundled ffmpeg, or system ffmpeg as fallback."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
        ffmpeg = os.path.join(base, 'ffmpeg', 'ffmpeg')
        if os.path.isfile(ffmpeg):
            return ffmpeg
    return 'ffmpeg'


def get_ffprobe_path() -> str:
    """Return path to bundled ffprobe, or system ffprobe as fallback."""
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
        ffprobe = os.path.join(base, 'ffmpeg', 'ffprobe')
        if os.path.isfile(ffprobe):
            return ffprobe
    return 'ffprobe'


def check_ffmpeg_available() -> Tuple[bool, str]:
    """Check if ffmpeg and ffprobe are accessible. Returns (ok, error_message)."""
    for name, path_fn in [("ffmpeg", get_ffmpeg_path), ("ffprobe", get_ffprobe_path)]:
        path = path_fn()
        try:
            subprocess.run(
                [path, "-version"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10,
            )
        except FileNotFoundError:
            return False, f"{name} not found at '{path}'. Install ffmpeg or check bundle."
        except subprocess.TimeoutExpired:
            return False, f"{name} at '{path}' timed out during version check."
        except OSError as exc:
            return False, f"{name} at '{path}' cannot be launched: {exc}"
    return True, ""


def available_video_encoders() -> set:
    """Return encoder names advertised by the active ffmpeg binary."""
    try:
        out = subprocess.check_output(
            [get_ffmpeg_path(), "-hide_banner", "-encoders"],
            stderr=subprocess.STDOUT,
            timeout=15,
        ).decode("utf-8", errors="ignore")
    except Exception:
        return set()

    found = set()
    for enc in ENCODER_LABELS:
        if enc in out:
            found.add(enc)
    return found


def benchmark_video_encoders(duration: int = 4, width: int = 720, height: int = 1280) -> List[dict]:
    """Run a small synthetic encode benchmark for available macOS encoders."""
    results = []
    encoders = [ENCODER_LIBX264, ENCODER_H264_VIDEOTOOLBOX, ENCODER_HEVC_VIDEOTOOLBOX]
    available = available_video_encoders()

    with tempfile.TemporaryDirectory(prefix="video-uniqualizer-bench-") as tmp:
        for encoder in encoders:
            if encoder not in available:
                results.append({
                    "encoder": encoder,
                    "available": False,
                    "seconds": None,
                    "fps": None,
                    "size_mb": None,
                    "error": "encoder not available",
                })
                continue

            output_path = os.path.join(tmp, f"{encoder}.mp4")
            video_args = ["-c:v", encoder, "-pix_fmt", "yuv420p", "-g", "60"]
            if encoder == ENCODER_LIBX264:
                video_args += ["-preset", "veryfast", "-crf", "24"]
            else:
                video_args += ["-b:v", "6000k", "-maxrate", "9000k", "-bufsize", "12000k", "-allow_sw", "1"]

            cmd = [
                get_ffmpeg_path(), "-y", "-nostdin",
                "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30",
                "-t", str(duration),
            ] + video_args + [
                "-an", "-movflags", "+faststart", "-loglevel", "error", output_path,
            ]

            start = time.perf_counter()
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
            elapsed = time.perf_counter() - start
            if proc.returncode == 0:
                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                results.append({
                    "encoder": encoder,
                    "available": True,
                    "seconds": round(elapsed, 3),
                    "fps": round((duration * 30) / elapsed, 1) if elapsed > 0 else None,
                    "size_mb": round(size_mb, 2),
                    "error": "",
                })
            else:
                results.append({
                    "encoder": encoder,
                    "available": True,
                    "seconds": round(elapsed, 3),
                    "fps": None,
                    "size_mb": None,
                    "error": proc.stderr.strip()[:300],
                })

    return results


def clamp(n, minn, maxn):
    return max(min(maxn, n), minn)


def generate_natural_filename(profile: dict, date_str: str) -> str:
    dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
    style = profile.get("filename_style", "generic")

    if style == "apple":
        num = random.randint(100, 9999)
        return f"IMG_{num:04d}.MP4"
    elif style == "samsung":
        return f"{dt.strftime('%Y%m%d')}_{dt.strftime('%H%M%S')}.mp4"
    elif style == "samsung_vid":
        return f"VID_{dt.strftime('%Y%m%d')}_{dt.strftime('%H%M%S')}.mp4"
    elif style == "pixel":
        ms = random.randint(100, 999)
        return f"PXL_{dt.strftime('%Y%m%d')}_{dt.strftime('%H%M%S')}{ms}.mp4"
    elif style == "xiaomi":
        return f"VID_{dt.strftime('%Y%m%d')}_{dt.strftime('%H%M%S')}.mp4"
    else:
        num = random.randint(1000, 9999)
        return f"VID_{num}.mp4"


def unique_filepath(folder: str, filename: str) -> str:
    path = os.path.join(folder, filename)
    if not os.path.exists(path):
        return path
    name, ext = os.path.splitext(filename)
    for i in range(1, 10000):
        path = os.path.join(folder, f"{name}_{i}{ext}")
        if not os.path.exists(path):
            return path
    return path


def generate_gps_string() -> str:
    base_lat, base_lon = random.choice(GPS_LOCATIONS)
    lat = base_lat + random.uniform(-0.01, 0.01)
    lon = base_lon + random.uniform(-0.01, 0.01)
    lat_str = f"{'+' if lat >= 0 else ''}{lat:.4f}"
    lon_str = f"{'+' if lon >= 0 else ''}{lon:.4f}"
    return f"{lat_str}{lon_str}/"


def probe_file(file_path: str) -> Tuple[Optional[float], bool]:
    """Single ffprobe call returning (duration, has_audio)."""
    cmd = [
        get_ffprobe_path(), "-v", "error",
        "-show_entries", "format=duration",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", file_path
    ]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.STDOUT, timeout=30,
        ).decode("utf-8", errors="ignore").strip()
    except Exception:
        return None, False

    duration = None
    has_audio = False
    for line in out.splitlines():
        line = line.strip()
        if line == "audio":
            has_audio = True
        elif line == "video":
            continue
        else:
            try:
                duration = float(line)
            except ValueError:
                pass
    return duration, has_audio


def _parse_ffmpeg_time(line: str) -> Optional[float]:
    """Extract elapsed seconds from an ffmpeg progress line."""
    m = _TIME_RE.search(line)
    if not m:
        return None
    h, mn, s, frac = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    return h * 3600 + mn * 60 + s + float(f"0.{frac}")


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
    vignette_angle: float = 0.15

    # Sub-perceptual
    gamma: float = 1.0
    subpixel_x: float = 0.0
    subpixel_y: float = 0.0
    do_chroma_roundtrip: bool = False
    do_audio_resample: bool = False

    # Speed
    video_speed: float = 1.0

    # Trim
    trim_start: float = 0.3
    trim_end: float = 0.3

    # Audio
    pitch: float = 1.0
    adelay_ms: int = 100
    audio_highpass: float = 0.0
    audio_lowpass: float = 0.0
    audio_bitrate: int = 128

    # Overlay
    overlay_file: str = ""
    opacity: float = 0.07
    ov_speed: float = 1.0

    # Output
    target_width: int = TARGET_WIDTH
    target_height: int = TARGET_HEIGHT
    crf: int = 26
    gop_size: int = 60
    preset: str = "fast"
    encoder: str = ENCODER_LIBX264
    performance_profile: str = PERFORMANCE_PROFILE_BALANCED
    video_bitrate_kbps: int = 8000

    # x264 tuning
    deblock_alpha: int = 0
    deblock_beta: int = 0
    aq_strength: float = 1.0
    psy_rd: float = 1.0
    qcomp: float = 0.6

    # Encoding profile
    h264_profile: str = "high"
    h264_level: str = "4.1"

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
        """Generate a fully randomized parameter set (V9.0 defaults)."""
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
        p.vignette_angle = round(random.uniform(0.05, 0.25), 2)

        p.video_speed = round(random.uniform(0.97, 1.08), 4)
        p.gop_size = random.randint(30, 90)

        p.trim_start = round(random.uniform(0.1, 0.6), 2)
        p.trim_end = round(random.uniform(0.1, 0.5), 2)

        p.pitch = clamp(round(random.uniform(0.96, 1.04), 4), 0.9, 1.1)
        p.adelay_ms = random.randint(30, 200)

        p.audio_bitrate = random.randint(126, 132)
        p.h264_profile = random.choice(["main", "high"])
        p.h264_level = random.choice(["4.0", "4.1", "4.2"])

        p.opacity = round(random.uniform(0.04, 0.11), 2)
        p.ov_speed = round(random.uniform(0.88, 1.12), 2)

        return p


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


def _kill_process(proc: subprocess.Popen):
    """Terminate ffmpeg gracefully, then force kill after 5s."""
    if proc.poll() is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except OSError:
        pass


def process_single_video(
    input_path: str,
    output_folder: str,
    index: int,
    params: Optional[UniqueParams] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    file_progress_callback: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[bool, str]:
    """
    Process a single video file.
    Returns (success, error_message).
    """
    if params is None:
        params = UniqueParams.generate_random()

    overlay_path = params.overlay_file if params.overlay_file else ""
    has_overlay = overlay_path and os.path.isfile(overlay_path)

    duration, has_audio = probe_file(input_path)

    p = params

    if duration and (p.trim_start + p.trim_end) >= duration * 0.4:
        p.trim_start = 0.1
        p.trim_end = 0.1

    profile = random.choice(DEVICE_PROFILES)
    date_str = UniqueParams.random_date_str()

    if p.fake_meta:
        filename = generate_natural_filename(profile, date_str)
        output_path = unique_filepath(output_folder, filename)
    else:
        output_path = os.path.join(output_folder, f"uniq_{index}.mp4")

    trim_args = ["-ss", str(p.trim_start)]
    effective_duration = None
    if duration:
        effective_duration = duration - p.trim_start - p.trim_end
        if effective_duration > 1.0:
            trim_args.extend(["-t", str(round(effective_duration, 3))])
        else:
            effective_duration = duration

    inputs = trim_args + ["-i", input_path]
    if has_overlay:
        inputs.extend(["-stream_loop", "-1", "-i", overlay_path])

    # VIDEO FILTERS
    subpixel_crop = f"crop=iw-2:ih-2:{p.subpixel_x}:{p.subpixel_y}," if (p.subpixel_x > 0 or p.subpixel_y > 0) else ""
    gamma_filter = f"lutyuv=y=gammaval({p.gamma})," if p.gamma != 1.0 else ""
    chroma_rt = "format=yuv444p,format=yuv420p," if p.do_chroma_roundtrip else ""

    tw, th = p.target_width, p.target_height
    cm = p.crop_margin
    cm = min(cm, (min(tw, th) // 2) - 1)
    cm = max(cm, 0)
    micro_crop = f"crop=iw-{cm*2}:ih-{cm*2}:{cm}:{cm},"

    fg_scale = (
        f"scale=iw*{p.zoom}:-1,"
        f"scale={tw}:{th}:force_original_aspect_ratio=decrease"
    )

    lens_filter = f"lenscorrection=k1={p.k1}:k2={p.k1},"
    rotate_filter = f"rotate={p.rotate}*PI/180:fillcolor=none,"
    unsharp_filter = f"unsharp=5:5:{p.unsharp_amount}:5:5:0.0,"
    noise_strength = p.noise_strength
    noise_filter = f"noise=alls={noise_strength}:allf={p.noise_flags},"
    vignette_filter = f"vignette=PI/5*{p.vignette_angle}"

    # Optimized blur: downscale -> blur -> upscale (4x cheaper than full-res blur)
    bg_w, bg_h = tw // 4, th // 4
    video_chain = (
        f"[0:v]split=2[bg_src][fg_src];"
        f"[bg_src]"
        f"scale={bg_w}:{bg_h},"
        f"avgblur=sizeX=5:sizeY=5,"
        f"scale={tw}:{th}"
        f"[bg];"
        f"[fg_src]"
        f"setpts={round(1/p.video_speed, 6)}*PTS,"
        f"{lens_filter}"
        f"{rotate_filter}"
        f"{subpixel_crop}"
        f"{micro_crop}"
        f"{fg_scale},"
        f"colorbalance=rs={p.rs}:gs={p.gs}:bs={p.bs},"
        f"hue=h={p.hue_shift},"
        f"eq=contrast={p.contrast}:saturation={p.saturation}:brightness={p.brightness},"
        f"{gamma_filter}"
        f"{chroma_rt}"
        f"{unsharp_filter}"
        f"{noise_filter}"
        f"{vignette_filter}"
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
        audio_eq = ""
        if p.audio_highpass > 0:
            audio_eq += f"highpass=f={int(p.audio_highpass)},"
        if p.audio_lowpass > 0:
            audio_eq += f"lowpass=f={int(p.audio_lowpass)},"
        audio_resample_rt = "aresample=48000," if p.do_audio_resample else ""
        audio_chain = (
            f"[0:a]"
            f"adelay={p.adelay_ms}|{p.adelay_ms},"
            f"asetrate=44100*{p.pitch},"
            f"atempo={combined_tempo},"
            f"{audio_resample_rt}"
            f"aresample=44100,"
            f"{audio_eq}"
            f"apad"
            f"[a_final]"
        )
    else:
        audio_chain = "anullsrc=channel_layout=stereo:sample_rate=44100[a_final]"

    filter_complex = f"{video_chain};{audio_chain}"

    requested_encoder = p.encoder if p.encoder in ENCODER_LABELS else ENCODER_LIBX264
    encoders = available_video_encoders()
    encoder = requested_encoder if requested_encoder in encoders else ENCODER_LIBX264
    if encoder != ENCODER_LIBX264 and not p.video_bitrate_kbps:
        p.video_bitrate_kbps = 8000

    video_args = ["-c:v", encoder, "-pix_fmt", "yuv420p"]
    if encoder == ENCODER_LIBX264:
        video_args += [
            "-profile:v", p.h264_profile,
            "-level", p.h264_level,
            "-preset", p.preset,
            "-crf", str(p.crf),
            "-g", str(p.gop_size),
            "-x264-params", f"deblock={p.deblock_alpha},{p.deblock_beta}:aq-strength={p.aq_strength}:psy-rd={p.psy_rd},0.00:qcomp={p.qcomp}",
        ]
    else:
        video_args += [
            "-b:v", f"{int(p.video_bitrate_kbps)}k",
            "-maxrate", f"{int(p.video_bitrate_kbps * 1.6)}k",
            "-bufsize", f"{int(p.video_bitrate_kbps * 2)}k",
            "-g", str(p.gop_size),
            "-allow_sw", "1",
        ]

    # Use -progress pipe:1 for machine-readable progress on stdout.
    cmd = [
        get_ffmpeg_path(), "-y", "-nostdin",
    ] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[v_final]",
        "-map", "[a_final]",
    ] + video_args + [
        "-c:a", "aac",
        "-b:a", f"{p.audio_bitrate}k",
        "-shortest",
        "-map_metadata", "-1",
    ]

    if p.fake_meta:
        gps = generate_gps_string()
        cmd += [
            "-metadata", f"make={profile['make']}",
            "-metadata", f"model={profile['model']}",
            "-metadata", f"software={profile['sw']}",
            "-metadata", f"artist={profile['make']} {profile['model']}",
            "-metadata", f"comment={profile['sw']}",
            "-metadata", f"date={date_str}",
            "-metadata", f"creation_time={date_str}",
            "-metadata", f"encoder={profile['encoder']}",
            "-metadata", f"location={gps}",
            "-metadata:s:v:0", f"handler_name={profile['video_handler']}",
            "-metadata:s:a:0", f"handler_name={profile['audio_handler']}",
        ]

    cmd += [
        "-movflags", "+faststart+use_metadata_tags",
        "-progress", "pipe:1",
        "-loglevel", "error",
        output_path,
    ]

    if cancelled and cancelled():
        return False, "cancelled"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        return False, f"ffmpeg failed to launch: {exc}"

    try:
        last_time = 0.0
        stderr_lines = []

        def _read_stderr():
            if not proc.stderr:
                return
            for err_line in proc.stderr:
                if err_line:
                    stderr_lines.append(err_line.rstrip())
                    if len(stderr_lines) > 30:
                        del stderr_lines[:10]

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        for line in proc.stdout:
            if cancelled and cancelled():
                _kill_process(proc)
                try:
                    os.unlink(output_path)
                except OSError:
                    pass
                return False, "cancelled"

            if line.startswith("out_time=") and effective_duration and file_progress_callback:
                time_str = line.strip().split("=", 1)[1]
                m = re.match(r"(\d+):(\d+):(\d+)\.(\d+)", time_str)
                if m:
                    secs = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + float(f"0.{m.group(4)}")
                    if secs > last_time:
                        last_time = secs
                        pct = min(secs / effective_duration, 1.0)
                        file_progress_callback(pct)

        proc.wait(timeout=_FFMPEG_TIMEOUT)
        stderr_thread.join(timeout=1)
    except subprocess.TimeoutExpired:
        _kill_process(proc)
        try:
            os.unlink(output_path)
        except OSError:
            pass
        return False, f"ffmpeg timed out after {_FFMPEG_TIMEOUT}s"

    if proc.returncode != 0:
        stderr_out = "\n".join(stderr_lines)
        try:
            os.unlink(output_path)
        except OSError:
            pass
        return False, f"ffmpeg exited with code {proc.returncode}: {stderr_out[:500]}"

    return True, ""


# Fields to copy from template to per-file randomized params
_TEMPLATE_FIELDS = [
    "overlay_file", "target_width", "target_height", "crf", "preset",
    "encoder", "performance_profile", "video_bitrate_kbps",
    "fake_meta", "gamma", "subpixel_x", "subpixel_y",
    "deblock_alpha", "deblock_beta", "aq_strength", "psy_rd", "qcomp",
    "audio_highpass", "audio_lowpass", "do_chroma_roundtrip", "do_audio_resample",
]


def process_batch(
    input_files: List[str],
    output_folder: str,
    params_template: Optional[UniqueParams] = None,
    randomize_each: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    file_progress_callback: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[int, List[str]]:
    """
    Process a batch of video files.
    Returns (success_count, list_of_error_messages).

    progress_callback(current_index, total, filename)
    file_progress_callback(fraction_0_to_1) — per-file encoding progress
    """
    os.makedirs(output_folder, exist_ok=True)

    need_index = params_template is None or not params_template.fake_meta
    start_index = next_output_index(output_folder) if need_index else 1

    success_count = 0
    errors: List[str] = []

    for i, fpath in enumerate(input_files):
        if cancelled and cancelled():
            break

        fname = os.path.basename(fpath)
        if progress_callback:
            progress_callback(i, len(input_files), fname)

        if randomize_each:
            params = UniqueParams.generate_random()
            if params_template:
                for field in _TEMPLATE_FIELDS:
                    setattr(params, field, getattr(params_template, field))
        else:
            params = params_template or UniqueParams.generate_random()

        ok, err = process_single_video(
            input_path=fpath,
            output_folder=output_folder,
            index=start_index + i,
            params=params,
            file_progress_callback=file_progress_callback,
            cancelled=cancelled,
        )
        if ok:
            success_count += 1
        elif err and err != "cancelled":
            errors.append(f"{fname}: {err}")

    if progress_callback:
        progress_callback(len(input_files), len(input_files), "")

    return success_count, errors
