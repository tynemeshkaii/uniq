"""
Video Uniqualization Engine — adapted from HYBRID V9.0 GOD MODE script.
Provides parameterized video processing via ffmpeg.

Design notes
------------
Every randomized value is drawn *per file* from a ``RandomRanges`` spec that the
UI owns. ``UniqueParams`` carries the concrete values for one output. Settings
that describe the output container rather than the uniqualization (resolution,
CRF, encoder, ...) live in ``_STATIC_FIELDS`` and are copied from the template
instead of being randomized.

Perceptual budget: defaults are chosen so a viewer cannot tell an output from
its source. Anything that is visible or audible on its own (mirroring, large
hue rotation, audio delay beyond lip-sync tolerance) is either opt-in or capped.
"""

import json
import logging
import math
import os
import re
import sys
import random
import signal
import subprocess
import threading
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional, List, Callable, Tuple

log = logging.getLogger(__name__)

DEVICE_PROFILES = [
    {
        "make": "Apple", "model": "iPhone 13 Pro", "sw": "15.3.1",
        "encoder": "Apple QuickTime",
        "video_handler": "Core Media Video", "audio_handler": "Core Media Audio",
        "filename_style": "apple", "meta_style": "apple",
        "brand": "mp42", "timescale": 600,
    },
    {
        "make": "Apple", "model": "iPhone 14 Pro", "sw": "16.2",
        "encoder": "Apple QuickTime",
        "video_handler": "Core Media Video", "audio_handler": "Core Media Audio",
        "filename_style": "apple", "meta_style": "apple",
        "brand": "mp42", "timescale": 600,
    },
    {
        "make": "Apple", "model": "iPhone 15", "sw": "17.0.3",
        "encoder": "Apple QuickTime",
        "video_handler": "Core Media Video", "audio_handler": "Core Media Audio",
        "filename_style": "apple", "meta_style": "apple",
        "brand": "mp42", "timescale": 600,
    },
    {
        "make": "Apple", "model": "iPhone 16 Pro", "sw": "18.1",
        "encoder": "Apple QuickTime",
        "video_handler": "Core Media Video", "audio_handler": "Core Media Audio",
        "filename_style": "apple", "meta_style": "apple",
        "brand": "mp42", "timescale": 600,
    },
    {
        "make": "Samsung", "model": "Galaxy S22 Ultra", "sw": "Android 12",
        "encoder": "samsung",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung", "meta_style": "android",
        "brand": "mp42", "timescale": 90000, "android_version": "12",
    },
    {
        "make": "Samsung", "model": "Galaxy S23", "sw": "Android 13",
        "encoder": "samsung",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung_vid", "meta_style": "android",
        "brand": "mp42", "timescale": 90000, "android_version": "13",
    },
    {
        "make": "Samsung", "model": "Galaxy S24 Ultra", "sw": "Android 14",
        "encoder": "samsung",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung_vid", "meta_style": "android",
        "brand": "mp42", "timescale": 90000, "android_version": "14",
    },
    {
        "make": "Google", "model": "Pixel 7 Pro", "sw": "Android 13",
        "encoder": "Google",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "pixel", "meta_style": "android",
        "brand": "mp42", "timescale": 90000, "android_version": "13",
    },
    {
        "make": "Google", "model": "Pixel 8", "sw": "Android 14",
        "encoder": "Google",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "pixel", "meta_style": "android",
        "brand": "mp42", "timescale": 90000, "android_version": "14",
    },
    {
        "make": "Sony", "model": "Xperia 1 V", "sw": "Android 13",
        "encoder": "sony",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung", "meta_style": "android",
        "brand": "mp42", "timescale": 90000, "android_version": "13",
    },
    {
        "make": "OnePlus", "model": "11 5G", "sw": "Android 13",
        "encoder": "oneplus",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "samsung_vid", "meta_style": "android",
        "brand": "mp42", "timescale": 90000, "android_version": "13",
    },
    {
        "make": "Xiaomi", "model": "13 Pro", "sw": "Android 13",
        "encoder": "xiaomi",
        "video_handler": "VideoHandle", "audio_handler": "SoundHandle",
        "filename_style": "xiaomi", "meta_style": "android",
        "brand": "mp42", "timescale": 90000, "android_version": "13",
    },
]

# (latitude, longitude, typical altitude in metres)
GPS_LOCATIONS = [
    (55.7558, 37.6173, 156.0),
    (40.7128, -74.0060, 10.0),
    (51.5074, -0.1278, 11.0),
    (35.6762, 139.6503, 40.0),
    (48.8566, 2.3522, 35.0),
    (52.5200, 13.4050, 34.0),
    (34.0522, -118.2437, 93.0),
    (25.2048, 55.2708, 5.0),
    (41.0082, 28.9784, 39.0),
    (37.5665, 126.9780, 38.0),
    (1.3521, 103.8198, 15.0),
    (55.6761, 12.5683, 14.0),
    (59.9343, 30.3351, 3.0),
    (43.7102, 7.2620, 10.0),
    (45.4642, 9.1900, 120.0),
]

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920

_OUT_TIME_RE = re.compile(r"(\d+):(\d+):(\d+)\.(\d+)")
# A slow encode is not a hung one, so the hard ceiling scales with the clip
# (30x its duration, never under an hour). What catches a real hang is the stall
# timeout: ffmpeg reports out_time twice a second, and a job whose output
# position has not moved for this long is stuck on a read or a deadlock.
_FFMPEG_TIMEOUT = 3600
_FFMPEG_TIMEOUT_PER_SECOND = 30
_FFMPEG_STALL_TIMEOUT = 180

# Prefix of the error a job returns when the output volume filled up. The batch
# stops scheduling new files on it: every one of them would fail the same way,
# each after writing a partial file first.
DISK_FULL_ERROR = "output disk is full"
_DISK_FULL_MARKERS = ("No space left on device", "ENOSPC")


def is_disk_full(stderr_text: str) -> bool:
    return any(m in stderr_text for m in _DISK_FULL_MARKERS)
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

AUDIO_WORK_SAMPLE_RATE = 48000
AUDIO_OUTPUT_SAMPLE_RATES = (44100, 48000)

# Perceptual safety caps. Values outside these are audible or visible.
SPEECH_SAFE_VIDEO_SPEED_MIN = 0.97
SPEECH_SAFE_VIDEO_SPEED_MAX = 1.03
SPEECH_SAFE_PITCH_MIN = 0.98
SPEECH_SAFE_PITCH_MAX = 1.02
# Lip-sync tolerance: audio lagging video becomes noticeable around 45 ms.
# Warping the playback time map was tried against this budget and lost: capped
# at 12 ms it is shorter than a frame and than an audio analysis window, so it
# shifts no alignment while spending delay budget. See CLAUDE.md.
MAX_AUDIO_DELAY_MS = 40
# Rotation beyond this leaves corner wedges that eat too much of the frame.
MAX_ROTATE_DEGREES = 1.0
MAX_HUE_DEGREES = 12.0

# An additive low-frequency luma field belongs here and is deliberately absent:
# any amplitude low enough to stay invisible moved the perceptual hash by 0-2
# bits of 63, and by 0 on organic footage. See CLAUDE.md before retrying it.

# How far a tone-curve control point may sit from the diagonal, on the 0..1
# scale, on top of the colour shift it already carries. Beyond this the slope
# change starts to posterise smooth gradients.
MAX_CURVE_JITTER = 2.0 / 255.0
# Slowest and fastest drift of the micro-warp corners, in frames per cycle.
# At the 3 px corner cap these give roughly 0.01-0.02 px/frame, far below the
# rate at which a viewer reads frame-to-frame motion.
WARP_DRIFT_PERIOD_FRAMES = (900, 1800)

# Strip SEI NAL units — x264 writes its full option string there, which both
# identifies the encoder and leaks the randomized tuning parameters.
SEI_STRIP_BSF = "filter_units=remove_types=6"


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
    """Check that ffmpeg and ffprobe actually run. Returns (ok, error_message).

    Launching is not enough: a binary whose dylibs are missing starts, gets
    killed by dyld, and exits non-zero without raising anything in Python. That
    is exactly how an unbundled ffmpeg fails on a tester's Mac, so the exit
    status and dyld's own message are what this reports.
    """
    for name, path_fn in [("ffmpeg", get_ffmpeg_path), ("ffprobe", get_ffprobe_path)]:
        path = path_fn()
        try:
            proc = subprocess.run(
                [path, "-hide_banner", "-version"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10,
            )
        except FileNotFoundError:
            return False, f"{name} not found at '{path}'."
        except subprocess.TimeoutExpired:
            return False, f"{name} at '{path}' timed out during version check."
        except OSError as exc:
            return False, f"{name} at '{path}' cannot be launched: {exc}"
        if proc.returncode != 0:
            detail = proc.stderr.decode("utf-8", errors="replace").strip()
            detail = "\n".join(detail.splitlines()[-6:]) or "no output"
            return False, (f"{name} at '{path}' exited with code "
                           f"{proc.returncode}:\n{detail}")
    return True, ""


def ffmpeg_version_line() -> str:
    """First line of ``ffmpeg -version``, for logs and diagnostics."""
    try:
        out = subprocess.run(
            [get_ffmpeg_path(), "-hide_banner", "-version"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10,
        ).stdout.decode("utf-8", errors="replace")
        return out.splitlines()[0] if out else "unknown"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable ({exc})"


_encoder_cache: Optional[set] = None
_encoder_cache_lock = threading.Lock()


def available_video_encoders(refresh: bool = False) -> set:
    """Return encoder names advertised by the active ffmpeg binary.

    Cached: this used to spawn one ffmpeg per processed file.
    """
    global _encoder_cache
    with _encoder_cache_lock:
        if _encoder_cache is not None and not refresh:
            return _encoder_cache

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
        _encoder_cache = found
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


def _even(n: int, minimum: int = 16) -> int:
    """Round down to an even value — yuv420p needs even dimensions and offsets."""
    return max(minimum, int(n) & ~1)


def _even_up(n: float) -> int:
    """Round up to an even value — for spans that must not lose their last row."""
    return int(math.ceil(n / 2.0)) * 2


def generate_natural_filename(profile: dict, date_str: str,
                              kind: str = "video", ext: str = "") -> str:
    """A filename in the shape the chosen device actually writes.

    ``kind`` picks the camera-roll convention: phones name photos and videos
    differently (a Samsung photo is ``20240115_143022.jpg`` while its video is
    ``VID_20240115_143022.mp4``), and using the video shape on a still is the
    kind of detail that makes a batch look generated.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S")
    style = profile.get("filename_style", "generic")

    if kind == "image":
        ext = ext or ".jpg"
        stamp = f"{dt.strftime('%Y%m%d')}_{dt.strftime('%H%M%S')}"
        if style == "apple":
            return f"IMG_{random.randint(100, 9999):04d}{ext.upper()}"
        if style == "samsung":
            return f"{stamp}{ext}"
        if style == "pixel":
            return f"PXL_{stamp}{random.randint(100, 999)}{ext}"
        # samsung_vid, xiaomi and the generic fallback all use IMG_<stamp>.
        return f"IMG_{stamp}{ext}"

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


_filepath_lock = threading.Lock()


def unique_filepath(folder: str, filename: str,
                    regenerate: Optional[Callable[[], str]] = None,
                    attempts: int = 64) -> str:
    """Reserve a free path. Creates the file so concurrent workers cannot collide.

    ``regenerate`` returns a fresh candidate *name*; when given, a collision
    redraws the name instead of appending ``_1``. That matters for the natural
    filenames: Apple's style is a bare 4-digit counter, so a 200-file batch
    collides often, and ``IMG_1234_1.MP4`` is not a name any phone writes — the
    suffix is itself the giveaway the fake name exists to avoid. The numeric
    suffix survives only as the last resort after ``attempts`` redraws.
    """
    with _filepath_lock:
        candidate_name = filename
        for _ in range(max(1, attempts)):
            candidate = os.path.join(folder, candidate_name)
            if not os.path.exists(candidate):
                try:
                    open(candidate, "xb").close()
                    return candidate
                except FileExistsError:
                    pass
                except OSError:
                    return candidate
            if regenerate is None:
                break
            candidate_name = regenerate()

        name, ext = os.path.splitext(candidate_name)
        candidate = os.path.join(folder, candidate_name)
        for i in range(1, 10000):
            candidate = os.path.join(folder, f"{name}_{i}{ext}")
            if not os.path.exists(candidate):
                try:
                    open(candidate, "xb").close()
                except FileExistsError:
                    continue
                except OSError:
                    pass
                return candidate
    return candidate


def reserve_natural_filepath(folder: str, profile: dict, date_str: str,
                             kind: str = "video", ext: str = "") -> Tuple[str, str]:
    """Reserve an output path under a name the chosen device could have written.

    Returns ``(path, date_str)``. The date comes back because a collision
    redraws it: the date-stamped filename styles are a pure function of the
    capture time, so a fresh name means a fresh time, and the metadata written
    later has to agree with the name or the pair is a tell on its own.
    """
    state = {"date": date_str}

    def _draw() -> str:
        return generate_natural_filename(profile, state["date"], kind=kind, ext=ext)

    def _redraw() -> str:
        state["date"] = UniqueParams.random_date_str()
        return _draw()

    path = unique_filepath(folder, _draw(), regenerate=_redraw)
    return path, state["date"]


def generate_gps_coords() -> Tuple[float, float, float]:
    """A jittered (lat, lon, alt) near one of the seed cities."""
    base_lat, base_lon, base_alt = random.choice(GPS_LOCATIONS)
    return (
        base_lat + random.uniform(-0.01, 0.01),
        base_lon + random.uniform(-0.01, 0.01),
        base_alt + random.uniform(-15.0, 15.0),
    )


def generate_gps_string() -> str:
    """ISO 6709 location string, matching the padding real cameras write."""
    lat, lon, alt = generate_gps_coords()
    return f"{lat:+08.4f}{lon:+09.4f}{alt:+.3f}/"


def build_tone_curve(shift: float, gamma: float, jitter: float) -> str:
    """One channel of a monotonic tone curve, as ffmpeg ``curves`` points.

    This subsumes the shadow shift ``colorbalance`` used to apply and the bend
    ``lutyuv=gammaval`` used to apply, so it replaces two filters rather than
    adding a third. What it buys over them is resistance to normalisation: a
    matcher that undoes a colour grade fits a global model — a gain, a gamma, a
    histogram stretch — and those two stages belong to exactly those families,
    so the fit removed them cleanly. An arbitrary monotonic spline does not,
    and leaves a residue behind.

    The endpoints stay pinned. Moving them shifts the black and white level,
    which clips and shows up on flat areas.
    """
    points: List[str] = []
    prev = -1.0
    for x in (0.0, 0.25, 0.5, 0.75, 1.0):
        if x in (0.0, 1.0):
            y = x
        else:
            # colorbalance weighted the shadows most heavily; keeping that shape
            # holds the perceptual magnitude at the level that already ships.
            y = x + shift * (1.0 - x) ** 2 * 1.6
            y = y ** gamma
            y += random.uniform(-jitter, jitter)
        # Keep the points strictly increasing by a wide margin. A curves spline
        # through non-monotonic points solarises, which is grossly visible.
        y = clamp(y, prev + 0.05, 1.0)
        points.append(f"{x:g}/{round(y, 5):g}")
        prev = y
    return " ".join(points)


# Pixel formats that carry an alpha channel. Only the ones the decoders in the
# bundled build actually emit are listed; the check is a membership test rather
# than a substring match on "a", which would also catch "yuva"-less names like
# "gray" and "pal8".
ALPHA_PIX_FMTS = {
    "rgba", "bgra", "argb", "abgr", "rgba64be", "rgba64le", "bgra64be",
    "bgra64le", "ya8", "ya16be", "ya16le", "yuva420p", "yuva422p", "yuva444p",
    "gbrap", "gbrap10le", "gbrap12le", "gbrap16le", "pal8",
}


@dataclass
class MediaInfo:
    """What a single ffprobe call tells us about an input file."""
    duration: Optional[float] = None
    has_audio: bool = False
    width: int = 0
    height: int = 0
    fps: float = 0.0
    rotation: int = 0
    pix_fmt: str = ""

    @property
    def has_geometry(self) -> bool:
        return self.width > 0 and self.height > 0

    @property
    def has_alpha(self) -> bool:
        return self.pix_fmt in ALPHA_PIX_FMTS


def probe_file(file_path: str) -> MediaInfo:
    """Single ffprobe call returning duration, audio presence and video geometry.

    ``width``/``height`` are the dimensions ffmpeg will actually hand the filter
    graph, not the ones stored in the file. Those differ whenever the source
    carries a rotation — a portrait phone photo stores landscape pixels plus an
    EXIF orientation, and a phone video stores a display matrix — and ffmpeg
    auto-rotates on decode. Reporting the stored pair meant the crop was planned
    against a canvas the wrong way round, which ffmpeg rejected outright
    ("Invalid too big or non positive size for width ..."). The rotation is
    frame side data rather than stream side data for JPEG, so it needs the
    ``-read_intervals`` decode of one frame to show up at all.
    """
    cmd = [
        get_ffprobe_path(), "-v", "error",
        "-read_intervals", "%+#1",
        "-show_entries",
        "stream=index,codec_type,width,height,r_frame_rate,pix_fmt"
        ":frame_side_data=rotation:format=duration",
        "-of", "json", file_path,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=60)
        data = json.loads(out.decode("utf-8", errors="ignore"))
    except Exception:
        return MediaInfo()

    info = MediaInfo()
    try:
        info.duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        info.duration = None

    for frame in data.get("frames", []):
        for side_data in frame.get("side_data_list", []) or []:
            if "rotation" in side_data:
                try:
                    info.rotation = int(side_data["rotation"])
                except (TypeError, ValueError):
                    info.rotation = 0
                break
        if info.rotation:
            break

    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        if codec_type == "audio":
            info.has_audio = True
        elif codec_type == "video" and not info.has_geometry:
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.pix_fmt = stream.get("pix_fmt") or ""
            rate = stream.get("r_frame_rate") or "0/0"
            try:
                num, den = rate.split("/")
                info.fps = float(num) / float(den) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                info.fps = 0.0

    # A quarter turn either way swaps what the filter graph will see.
    if info.rotation % 180 != 0:
        info.width, info.height = info.height, info.width

    return info


@dataclass
class RandomRanges:
    """Per-batch spec the UI owns. The engine draws fresh values from it per file.

    Defaults are the perceptually safe operating point: every individual effect
    stays under the threshold where a viewer would notice it.
    """
    # Geometry
    # Reframing is the strongest lever in the whole pipeline, and it is not
    # close. Measured with fingerprint.py, pHash distance from the source rises
    # monotonically with zoom — on organic footage 3.3 bits at the old 1.02-1.05
    # default, 6.7 at 1.06-1.12, 12.0 at 1.14-1.22, against a matcher threshold
    # of about 10. Every filter in this file put together moves that number less
    # than this one range does. It is raised here because 1.02-1.05 simply did
    # not clear the threshold on smooth content.
    #
    # What it costs is sharpness, not cleanliness: the crop is scaled back up to
    # the output size, so a larger zoom is a real resolution loss. That trade is
    # invisible to the SSIM check, which compares against a twin at the same
    # geometry — judge it by eye on the detail crop the gate writes out.
    zoom: Tuple[float, float] = (1.08, 1.16)
    rotate_max: float = 0.5
    k1_max: float = 0.006
    crop_margin: Tuple[int, int] = (4, 16)
    pan_max: float = 0.6          # fraction of the slack that zoom frees up
    trim_start: Tuple[float, float] = (0.1, 0.6)
    trim_end: Tuple[float, float] = (0.1, 0.5)

    # Color
    color_shift_max: float = 0.06
    contrast: Tuple[float, float] = (0.96, 1.04)
    saturation: Tuple[float, float] = (0.94, 1.06)
    brightness_max: float = 0.025
    hue_max: float = 3.0

    # Effects
    # Grain above ~5 is nearly pure cost: measured against an otherwise
    # identical encode it leaves PSNR unchanged (the colour stage sets that
    # floor) while inflating the file 2x at strength 9 and up to 4.6x at 12,
    # and it is the first thing a perceptual hash averages away.
    noise: Tuple[int, int] = (2, 5)
    unsharp: Tuple[float, float] = (-0.25, 0.45)
    vignette: Tuple[float, float] = (0.05, 0.20)

    # Timing
    video_speed: Tuple[float, float] = (0.99, 1.02)
    pitch: Tuple[float, float] = (0.99, 1.01)
    adelay_ms: Tuple[int, int] = (0, MAX_AUDIO_DELAY_MS)
    gop: Tuple[int, int] = (30, 90)

    # Broadband spectral tilt, in dB. Fingerprints built on band energies read
    # this, and under 1 dB it is inaudible.
    audio_tilt_db: Tuple[float, float] = (0.3, 0.9)
    # Added noise floor, in dBFS. Well under a typical room tone.
    audio_noise_dbfs: Tuple[float, float] = (-68.0, -62.0)

    # Overlay
    ov_opacity: Tuple[float, float] = (0.04, 0.11)
    ov_speed: Tuple[float, float] = (0.88, 1.12)

    # Toggles
    use_gamma: bool = True
    use_micro_warp: bool = True
    use_warp_drift: bool = True
    use_tone_curve: bool = True
    use_chroma_roundtrip: bool = True
    use_audio_eq: bool = True
    use_audio_notch: bool = True
    use_audio_tilt: bool = True
    use_audio_noise_floor: bool = True
    use_audio_resample: bool = True
    use_x264_tuning: bool = True
    use_fps_jitter: bool = True
    use_hflip: bool = False        # mirrors on-screen text — opt-in only

    def draw_int(self, rng: Tuple[int, int]) -> int:
        lo, hi = min(rng), max(rng)
        return random.randint(int(lo), int(hi))

    def draw_float(self, rng: Tuple[float, float], digits: int = 3) -> float:
        lo, hi = min(rng), max(rng)
        return round(random.uniform(lo, hi), digits)


@dataclass
class UniqueParams:
    """Concrete parameter values for a single output file."""
    # Geometry
    k1: float = 0.0
    lens_cx: float = 0.5
    lens_cy: float = 0.5
    rotate: float = 0.0
    zoom: float = 1.03
    crop_margin: int = 8
    pan_x: float = 0.0
    pan_y: float = 0.0
    hflip: bool = False

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
    noise_seed: int = 0
    vignette_angle: float = 0.15

    # Sub-perceptual
    gamma: float = 1.0
    # Per-corner inward inset in pixels: TLx, TLy, TRx, TRy, BLx, BLy, BRx, BRy.
    # ``warp_offsets`` is where each corner starts, ``warp_drift_offsets`` where
    # it eases to, so the deformation field varies over time as well as space.
    warp_offsets: Tuple[int, ...] = (0,) * 8
    warp_drift_offsets: Tuple[int, ...] = (0,) * 8
    warp_drift_period: int = 0            # frames per cycle; 0 disables drift
    warp_drift_phases: Tuple[float, ...] = (0.0,) * 8
    # Per-channel monotonic tone curve, as ffmpeg `curves` control-point strings
    # (r, g, b). Empty means the curve stage is off.
    tone_curve: Tuple[str, str, str] = ("", "", "")
    do_chroma_roundtrip: bool = False
    do_audio_resample: bool = False

    # Speed
    video_speed: float = 1.0
    fps_scale: float = 1.0

    # Trim
    trim_start: float = 0.3
    trim_end: float = 0.3

    # Audio
    pitch: float = 1.0
    adelay_ms: int = 20
    audio_highpass: float = 0.0
    audio_lowpass: float = 0.0
    audio_notches: Tuple[Tuple[float, float, float], ...] = ()
    audio_bass_db: float = 0.0
    audio_treble_db: float = 0.0
    audio_noise_dbfs: float = 0.0         # 0 disables the added noise floor
    audio_noise_seed: int = 0
    audio_bitrate: int = 128
    audio_sample_rate: int = 48000

    # Overlay
    overlay_file: str = ""
    opacity: float = 0.07
    ov_speed: float = 1.0

    # Output
    target_width: int = TARGET_WIDTH
    target_height: int = TARGET_HEIGHT
    crf: int = 24
    gop_size: int = 60
    preset: str = "fast"
    encoder: str = ENCODER_LIBX264
    performance_profile: str = PERFORMANCE_PROFILE_BALANCED
    video_bitrate_kbps: int = 8000
    threads: int = 0

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
        """A plausible capture timestamp within the last two years.

        The time of day is randomized too. Subtracting whole days from now left
        every file in a batch stamped with the same hour, minute and second —
        which also propagated into the generated filenames.
        """
        dt = datetime.now() - timedelta(
            days=random.randint(1, 730),
            seconds=random.randint(0, 86399),
        )
        # Daytime hours: a clip captured at 04:00 is its own kind of outlier.
        dt = dt.replace(hour=random.randint(8, 22))
        return dt.strftime("%Y-%m-%dT%H:%M:%S")

    @classmethod
    def generate_random(cls, ranges: Optional[RandomRanges] = None) -> 'UniqueParams':
        """Draw a fresh parameter set for one output file."""
        r = ranges or RandomRanges()
        p = cls()

        # Geometry
        p.k1 = round(random.uniform(-r.k1_max, r.k1_max), 4)
        # k2 is the quartic term; keeping it equal to k1 doubles edge distortion,
        # so it is derived as a small fraction instead (see build_video_chain).
        p.lens_cx = round(random.uniform(0.48, 0.52), 3)
        p.lens_cy = round(random.uniform(0.48, 0.52), 3)
        rotate_max = min(r.rotate_max, MAX_ROTATE_DEGREES)
        p.rotate = round(random.uniform(-rotate_max, rotate_max), 3)
        p.zoom = max(1.0, r.draw_float(r.zoom))
        p.crop_margin = r.draw_int(r.crop_margin)
        p.pan_x = round(random.uniform(-r.pan_max, r.pan_max), 3)
        p.pan_y = round(random.uniform(-r.pan_max, r.pan_max), 3)
        p.hflip = bool(r.use_hflip)

        # Color
        cs = r.color_shift_max
        p.rs = round(random.uniform(-cs, cs), 3)
        p.gs = round(random.uniform(-cs, cs), 3)
        p.bs = round(random.uniform(-cs, cs), 3)
        p.contrast = r.draw_float(r.contrast)
        p.saturation = r.draw_float(r.saturation)
        p.brightness = round(random.uniform(-r.brightness_max, r.brightness_max), 3)
        hue_max = min(r.hue_max, MAX_HUE_DEGREES)
        p.hue_shift = round(random.uniform(-hue_max, hue_max), 1)
        p.gamma = round(random.uniform(0.985, 1.015), 3) if r.use_gamma else 1.0
        if r.use_tone_curve:
            # Folds the per-channel shift and the gamma bend into one filter.
            p.tone_curve = tuple(
                build_tone_curve(channel_shift, p.gamma, MAX_CURVE_JITTER)
                for channel_shift in (p.rs, p.gs, p.bs)
            )
        else:
            p.tone_curve = ("", "", "")

        # Effects
        p.unsharp_amount = r.draw_float(r.unsharp, 2)
        p.noise_strength = r.draw_int(r.noise)
        p.noise_flags = random.choice(["t", "u", "t+u"])
        # Without an explicit seed the noise pattern is identical in every file.
        p.noise_seed = random.randint(0, 2 ** 31 - 2)
        p.vignette_angle = r.draw_float(r.vignette, 2)

        if r.use_micro_warp:
            # Sub-pixel, spatially non-uniform displacement. Uniform transforms
            # (rotate/scale) are what perceptual hashes are built to ignore.
            p.warp_offsets = tuple(random.randint(0, 3) for _ in range(8))
            if r.use_warp_drift:
                # Each corner eases to a second inset over a period far longer
                # than a frame, so no single transform aligns the whole clip.
                p.warp_drift_offsets = tuple(random.randint(0, 3) for _ in range(8))
                p.warp_drift_period = random.randint(*WARP_DRIFT_PERIOD_FRAMES)
                p.warp_drift_phases = tuple(
                    round(random.uniform(0, 2 * math.pi), 4) for _ in range(8)
                )
            else:
                p.warp_drift_offsets = p.warp_offsets
                p.warp_drift_period = 0
        else:
            p.warp_offsets = (0,) * 8
            p.warp_drift_offsets = (0,) * 8
            p.warp_drift_period = 0

        p.do_chroma_roundtrip = bool(r.use_chroma_roundtrip)
        p.do_audio_resample = bool(r.use_audio_resample)

        # Timing
        p.video_speed = clamp(
            r.draw_float(r.video_speed, 4),
            SPEECH_SAFE_VIDEO_SPEED_MIN, SPEECH_SAFE_VIDEO_SPEED_MAX,
        )
        p.fps_scale = random.choice([1.0, 1000 / 1001, 1001 / 1000]) if r.use_fps_jitter else 1.0
        p.gop_size = r.draw_int(r.gop)
        p.trim_start = r.draw_float(r.trim_start, 2)
        p.trim_end = r.draw_float(r.trim_end, 2)

        # Audio
        p.pitch = clamp(r.draw_float(r.pitch, 4), SPEECH_SAFE_PITCH_MIN, SPEECH_SAFE_PITCH_MAX)
        p.adelay_ms = min(r.draw_int(r.adelay_ms), MAX_AUDIO_DELAY_MS)
        p.audio_bitrate = random.randint(126, 132)
        p.audio_sample_rate = random.choice(AUDIO_OUTPUT_SAMPLE_RATES)
        if r.use_audio_eq:
            p.audio_highpass = random.randint(25, 45)
            p.audio_lowpass = random.randint(16000, 18000)
        if r.use_audio_notch:
            # Narrow notches under 1.5 dB are inaudible but move the spectral
            # peak constellation that audio fingerprints are built from. The
            # bands do not overlap, so five of them are no more audible than
            # three while covering more of the spectrum a matcher looks at.
            p.audio_notches = tuple(
                (
                    float(random.randint(lo, hi)),
                    round(random.uniform(6.0, 12.0), 1),
                    round(random.uniform(-1.5, -0.5), 2),
                )
                for lo, hi in (
                    (100, 280), (300, 900), (1200, 3000),
                    (4000, 8000), (8500, 14000),
                )
            )
        if r.use_audio_tilt:
            # Narrow notches barely move a fingerprint that bins energy into a
            # few dozen wide log bands. A gentle broadband tilt does, and under
            # 1 dB across the whole spectrum is inaudible.
            tilt = r.draw_float(r.audio_tilt_db, 2)
            direction = random.choice((-1.0, 1.0))
            p.audio_bass_db = round(direction * tilt, 2)
            p.audio_treble_db = round(-direction * r.draw_float(r.audio_tilt_db, 2), 2)
        if r.use_audio_noise_floor:
            p.audio_noise_dbfs = r.draw_float(r.audio_noise_dbfs, 1)
            p.audio_noise_seed = random.randint(0, 2 ** 31 - 2)

        # Encoder-visible variation
        p.h264_profile = random.choice(["main", "high"])
        p.h264_level = random.choice(["4.1", "4.2"])
        if r.use_x264_tuning:
            p.deblock_alpha = random.randint(-3, 3)
            p.deblock_beta = random.randint(-3, 3)
            p.aq_strength = round(random.uniform(0.8, 1.2), 2)
            p.psy_rd = round(random.uniform(0.8, 1.2), 2)
            p.qcomp = round(random.uniform(0.5, 0.7), 2)

        # Overlay
        p.opacity = r.draw_float(r.ov_opacity, 2)
        p.ov_speed = r.draw_float(r.ov_speed, 2)

        return p


# Output/container settings. These describe the deliverable rather than the
# uniqualization, so they are copied from the template instead of randomized.
_STATIC_FIELDS = [
    "overlay_file", "target_width", "target_height", "crf", "preset",
    "encoder", "performance_profile", "video_bitrate_kbps", "fake_meta", "threads",
]


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


# ─── Geometry planning ──────────────────────────────────────────────────

@dataclass
class GeometryPlan:
    """Resolved pixel geometry for one file.

    All arithmetic happens in Python so the filter graph carries literal even
    numbers — crop offsets given as expressions get truncated to chroma-aligned
    integers by ffmpeg, which silently discarded sub-pixel intent before.

    Two rectangles live here and they are not interchangeable:

    * ``crop_*`` is **R**, the window in the *rotated* frame. It is what the
      aspect snap, the pan and the background decision are all reasoning about,
      and ``fg_*`` is what R scales up to. Nothing about that changed.
    * ``bbox_*`` is what ffmpeg actually crops: the axis-aligned bounding box of
      R's four corners *unrotated* back into the source frame. The rotation is
      then carried by ``corners`` — R's corners in bbox-relative coordinates —
      handed to one ``perspective`` filter, instead of by a ``rotate`` filter
      that resampled the whole frame before the crop threw most of it away.

    With ``rotate`` at zero the unrotation is the identity, bbox is exactly R,
    and ``corners`` describes the plain rectangle, which is why the perspective
    is dropped entirely in that case and the graph collapses to the crop+scale
    it has always been.
    """
    crop_w: int
    crop_h: int
    crop_x: int
    crop_y: int
    fg_w: int
    fg_h: int
    needs_background: bool
    warp: Tuple[int, ...]
    warp_end: Tuple[int, ...]
    bbox_w: int = 0
    bbox_h: int = 0
    bbox_x: int = 0
    bbox_y: int = 0
    # (x,y) of R's top-left, top-right, bottom-left, bottom-right corners,
    # relative to the bbox origin, already in the half-pixel-shifted convention
    # vf_perspective was measured to want (see _perspective_corners). Its own
    # corner order.
    corners: Tuple[float, ...] = (0.0,) * 8
    # cos/sin of the *unrotation* angle (−rotate), so the micro-warp's corner
    # insets — which are expressed in R's frame — can be rotated into the same
    # source frame the corners live in.
    rot_cos: float = 1.0
    rot_sin: float = 0.0
    # The perspective now runs *before* the scale, so a corner inset expressed
    # in output pixels has to be divided down into R's own resolution or the
    # micro-warp would grow by the zoom factor. The 3 px cap stays a cap on
    # what the viewer could see, which is the output.
    warp_scale_x: float = 1.0
    warp_scale_y: float = 1.0


# How far the source aspect may sit from the target before the blurred
# backdrop is worth building. Inside this band the crop is snapped to the
# target aspect instead, which avoids both the bars and any stretching.
ASPECT_SNAP_TOLERANCE = 0.05
# A snap that would shave no more than this is not a real aspect difference,
# only the even-number rounding the target size picked up. Shaving for it
# would crop a frame the caller may have asked to keep whole.
ASPECT_SNAP_SLACK = 2


def _perspective_corners(quad: List[Tuple[float, float]],
                         out_w: int, out_h: int) -> Tuple[float, ...]:
    """Convert R's corners into the numbers ``vf_perspective`` actually wants.

    Measured rather than assumed: the filter samples output pixel (x, y) at
    ``q0 + (x/W)*(q1-q0) + (y/H)*(q2-q0) + ...``. The divisor is the output
    width, not ``W-1``, so the identity mapping is the quad
    (0,0)-(W,0)-(0,H)-(W,H) — verified exact — and the parameter runs off the
    pixel *index* while the ``scale`` after it samples on pixel *centres*. That
    half-pixel between the two conventions is a real misalignment, worth about
    6 dB against the old rotate-then-crop path, so R's quad is evaluated half an
    output pixel along on both axes and then shifted from centre coordinates
    back to the index coordinates the filter parses.
    """
    (ax, ay), (bx, by), (cx_, cy_), (dx_, dy_) = quad
    du = 0.5 / out_w
    dv = 0.5 / out_h

    def at(u: float, v: float) -> Tuple[float, float]:
        return (
            ax * (1 - u) * (1 - v) + bx * u * (1 - v)
            + cx_ * (1 - u) * v + dx_ * u * v - 0.5,
            ay * (1 - u) * (1 - v) + by * u * (1 - v)
            + cy_ * (1 - u) * v + dy_ * u * v - 0.5,
        )

    return tuple(
        value
        for (u, v) in ((du, dv), (1 + du, dv), (du, 1 + dv), (1 + du, 1 + dv))
        for value in at(u, v)
    )


def plan_geometry(src_w: int, src_h: int, p: UniqueParams) -> GeometryPlan:
    """Resolve rotation safety margin, micro-crop, zoom and pan into one crop."""
    target_w, target_h = p.target_width, p.target_height
    target_ar = target_w / target_h

    # Rotation leaves uncovered wedges at the frame edges; crop past them.
    rot_inset = math.ceil(max(src_w, src_h) * abs(math.sin(math.radians(p.rotate))))
    inset = rot_inset + max(0, int(p.crop_margin))
    # Never eat more than a quarter of the frame on either axis.
    inset = int(min(inset, src_w // 4, src_h // 4))

    avail_w = max(16, src_w - 2 * inset)
    avail_h = max(16, src_h - 2 * inset)

    source_ar = src_w / src_h
    needs_background = abs(source_ar - target_ar) / target_ar > ASPECT_SNAP_TOLERANCE

    if not needs_background:
        # Snap the window to the exact target aspect so the final scale is a
        # pure resize: no bars, no anamorphic stretch. Sub-pixel drift is left
        # alone (see ASPECT_SNAP_SLACK); the residual stretch is far below what
        # the snap would have cost in cropped pixels.
        if avail_w / avail_h > target_ar:
            snapped = _even(avail_h * target_ar)
            if avail_w - snapped > ASPECT_SNAP_SLACK:
                avail_w = snapped
        else:
            snapped = _even(avail_w / target_ar)
            if avail_h - snapped > ASPECT_SNAP_SLACK:
                avail_h = snapped

    # Real zoom: take a smaller window out of the available area. The previous
    # `scale=iw*zoom:-1` followed by an aspect-preserving fit cancelled itself out.
    zoom = max(1.0, float(p.zoom))
    crop_w = min(_even(avail_w / zoom), _even(avail_w))
    crop_h = min(_even(avail_h / zoom), _even(avail_h))
    if not needs_background:
        # Keep the aspect exact after the even-number rounding — again only
        # when the correction is worth more than the pixels it costs.
        snapped_h = _even(crop_w / target_ar)
        if abs(crop_h - snapped_h) > ASPECT_SNAP_SLACK:
            crop_h = snapped_h
            if crop_h > avail_h:
                crop_h = _even(avail_h)
                crop_w = _even(crop_h * target_ar)

    # Pan only within the slack that zooming freed, so the crop never reaches
    # back into the rotation wedges.
    max_x = src_w - crop_w
    max_y = src_h - crop_h
    slack_x = max(0, (avail_w - crop_w) // 2)
    slack_y = max(0, (avail_h - crop_h) // 2)
    base_x = inset + (avail_w - crop_w) // 2
    base_y = inset + (avail_h - crop_h) // 2
    crop_x = _even(clamp(base_x + int(round(p.pan_x * slack_x)), 0, max_x), minimum=0)
    crop_y = _even(clamp(base_y + int(round(p.pan_y * slack_y)), 0, max_y), minimum=0)

    if needs_background:
        scale = min(target_w / crop_w, target_h / crop_h)
        fg_w = _even(round(crop_w * scale))
        fg_h = _even(round(crop_h * scale))
    else:
        fg_w, fg_h = _even(target_w), _even(target_h)

    # Micro-warp corner insets must stay well inside the frame. The drift
    # endpoint is bound by the same cap, so the corner cannot wander past it
    # part-way through the clip.
    warp_cap = max(0, min(3, fg_w // 200, fg_h // 200))
    warp = tuple(min(o, warp_cap) for o in p.warp_offsets)
    warp_end = tuple(min(o, warp_cap) for o in p.warp_drift_offsets)

    # ── Fold the rotation into the crop ────────────────────────────────────
    # Coordinates here are continuous, not pixel indices: pixel *i* spans
    # [i, i+1), so R covers [crop_x, crop_x + crop_w] and the centre of the
    # pixel-centre grid — ((W-1)/2 in index terms, which is what vf_rotate was
    # measured to pivot on — sits at exactly W/2.
    #
    # ffmpeg's rotate turns the image clockwise for a positive angle, so a point
    # r in the rotated frame was sampled from C + M(-theta)*(r - C) in the
    # source. Unrotating R's four corners that way gives the quad the crop has
    # to cover, and hands the rotation itself to one perspective filter.
    theta = math.radians(p.rotate)
    rot_cos = math.cos(-theta)
    rot_sin = math.sin(-theta)
    pivot_x = src_w / 2.0
    pivot_y = src_h / 2.0

    def _unrotate(x: float, y: float) -> Tuple[float, float]:
        dx, dy = x - pivot_x, y - pivot_y
        return (clamp(pivot_x + dx * rot_cos - dy * rot_sin, 0.0, float(src_w)),
                clamp(pivot_y + dx * rot_sin + dy * rot_cos, 0.0, float(src_h)))

    right = crop_x + crop_w
    bottom = crop_y + crop_h
    quad = [
        _unrotate(crop_x, crop_y), _unrotate(right, crop_y),
        _unrotate(crop_x, bottom), _unrotate(right, bottom),
    ]

    # The micro-warp only ever pulls corners inward, so the unperturbed quad
    # already bounds every position a drifting corner can reach.
    xs = [pt[0] for pt in quad]
    ys = [pt[1] for pt in quad]
    bbox_x = _even(int(math.floor(min(xs))), minimum=0)
    bbox_y = _even(int(math.floor(min(ys))), minimum=0)
    bbox_w = min(_even_up(math.ceil(max(xs)) - bbox_x),
                 _even(src_w - bbox_x, minimum=2))
    bbox_h = min(_even_up(math.ceil(max(ys)) - bbox_y),
                 _even(src_h - bbox_y, minimum=2))
    corners = _perspective_corners(
        [(x - bbox_x, y - bbox_y) for x, y in quad], bbox_w, bbox_h)

    return GeometryPlan(
        crop_w=crop_w, crop_h=crop_h, crop_x=crop_x, crop_y=crop_y,
        fg_w=fg_w, fg_h=fg_h, needs_background=needs_background, warp=warp,
        warp_end=warp_end,
        bbox_w=bbox_w, bbox_h=bbox_h, bbox_x=bbox_x, bbox_y=bbox_y,
        corners=corners, rot_cos=rot_cos, rot_sin=rot_sin,
        warp_scale_x=crop_w / fg_w, warp_scale_y=crop_h / fg_h,
    )


def build_geometry_steps(p, plan: GeometryPlan) -> List[str]:
    """The reframing half: everything that decides where a pixel lands.

    Split out from the effects because the still pipeline has to run these two
    stages on separate branches when the source carries an alpha channel — the
    alpha plane needs the identical geometry but none of the colour work. See
    ``image_engine.build_image_graph``.
    """
    steps: List[str] = []

    if p.hflip:
        steps.append("hflip")

    if p.k1:
        # i=bilinear: the default nearest-neighbour sampling produces visible
        # stair-stepping across the whole frame.
        steps.append(
            f"lenscorrection=cx={p.lens_cx}:cy={p.lens_cy}"
            f":k1={p.k1}:k2={round(p.k1 * 0.25, 5)}:i=bilinear"
        )
    # The rotation is not a filter of its own any more: it lives in the corner
    # mapping below, on a crop window that is only as large as the rotated
    # window needs. Rotating the whole frame first and discarding most of it was
    # the single most expensive stage in the graph.
    steps.append(f"crop={plan.bbox_w}:{plan.bbox_h}:{plan.bbox_x}:{plan.bbox_y}")

    perspective = build_perspective_step(p, plan)
    if perspective:
        steps.append(perspective)

    # Stills override this to lanczos. The zoom crop is scaled back up, and on
    # a photograph — examined rather than watched — bicubic's softening of that
    # upscale is visible where it is not in motion. Video keeps bicubic: the
    # default is what the attribute lookup falls back to, so the video graph is
    # unchanged.
    steps.append(f"scale={plan.fg_w}:{plan.fg_h}:flags="
                 f"{getattr(p, 'scale_flags', 'bicubic')}")
    # Rounding the crop to even numbers leaves the aspect a hair off, and scale
    # compensates by writing a non-square sample aspect ratio (e.g. 1018:1017).
    # Cameras always write square pixels, so pin it rather than leak the tell.
    steps.append("setsar=1")

    return steps


# Which way each corner's two insets pull, in R's own frame: x inward is +1 on
# the left corners and -1 on the right ones, y inward is +1 on the top pair and
# -1 on the bottom pair. Order is perspective's: TL, TR, BL, BR.
_CORNER_SIGNS = ((1, 1), (-1, 1), (1, -1), (-1, -1))


def _corner_expr(base: float, terms) -> str:
    """``base`` plus each ``(coefficient, inset)`` pair.

    Constant insets are folded into the literal so a non-drifting warp emits
    plain numbers and ffmpeg parses no expression at all.
    """
    const = base
    parts: List[str] = []
    for coefficient, value in terms:
        if isinstance(value, float):
            const += coefficient * value
        elif coefficient:
            parts.append(f"{coefficient:+.6f}*{value}")
    return f"{const:.4f}" + "".join(parts)


def build_perspective_step(p, plan: GeometryPlan) -> str:
    """The rotation and the micro-warp as one corner mapping, or "" for neither.

    ``sense=source`` means the four points name where in the *input* the output
    corners are sampled from, which is exactly what ``plan.corners`` holds: R's
    corners, unrotated into the source frame and made relative to the bbox crop.

    The micro-warp then pulls each corner inward by its own amount, so the
    displacement field varies across the frame instead of being a global
    transform — uniform transforms are what perceptual hashes are built to
    ignore. With drift on, each corner also eases between two insets over many
    hundreds of frames, so the field varies in time as well. At the 3 px cap
    that works out to roughly 0.01 px per frame, far below the rate at which a
    viewer reads frame-to-frame motion.
    """
    if not p.rotate and not any(plan.warp) and not any(plan.warp_end):
        # Nothing left to map: bbox is R and the corners are its own rectangle.
        # Emitting an identity perspective here would cost a resample for no
        # geometric change, and would break the byte-for-byte equivalence the
        # stills' preserve_full_frame mode relies on.
        return ""

    drifting = p.warp_drift_period > 0 and plan.warp != plan.warp_end

    def inset(idx: int):
        start, end = plan.warp[idx], plan.warp_end[idx]
        if not drifting or start == end:
            return float(start)
        mid = (start + end) / 2.0
        half = (end - start) / 2.0
        phase = p.warp_drift_phases[idx]
        return (f"({mid:g}{half:+g}*sin(2*PI*on/{p.warp_drift_period}"
                f"{phase:+.4f}))")

    cos_t, sin_t = plan.rot_cos, plan.rot_sin
    scale_x, scale_y = plan.warp_scale_x, plan.warp_scale_y
    coords: List[str] = []
    for corner, (sign_x, sign_y) in enumerate(_CORNER_SIGNS):
        base_x = plan.corners[2 * corner]
        base_y = plan.corners[2 * corner + 1]
        dx, dy = inset(2 * corner), inset(2 * corner + 1)
        # Each inset is pulled inward in R's frame, shrunk to R's resolution,
        # then rotated into the source frame alongside the corner it moves —
        # hence two terms per coordinate rather than a bare addition.
        push_x = sign_x * scale_x
        push_y = sign_y * scale_y
        coords.append(_corner_expr(
            base_x, ((push_x * cos_t, dx), (-push_y * sin_t, dy))))
        coords.append(_corner_expr(
            base_y, ((push_x * sin_t, dx), (push_y * cos_t, dy))))

    return (
        f"perspective=x0='{coords[0]}':y0='{coords[1]}'"
        f":x1='{coords[2]}':y1='{coords[3]}'"
        f":x2='{coords[4]}':y2='{coords[5]}'"
        f":x3='{coords[6]}':y3='{coords[7]}'"
        f":interpolation=linear:sense=source"
        f":eval={'frame' if drifting else 'init'}"
    )


def build_effect_steps(p) -> List[str]:
    """The degradation half: colour, grain, sharpening, vignette.

    Pure per-pixel work — nothing here moves a pixel — so it can be applied to
    a colour branch whose alpha has been split off and re-merged afterwards.
    """
    steps: List[str] = []

    use_curve = all(p.tone_curve)
    if use_curve:
        # One monotonic spline per channel, standing in for both colorbalance
        # and the gamma LUT. colorbalance already forces an RGB round trip, so
        # this costs a filter less than the pair it replaces.
        curve_r, curve_g, curve_b = p.tone_curve
        steps.append(f"curves=r='{curve_r}':g='{curve_g}':b='{curve_b}'")
    else:
        steps.append(f"colorbalance=rs={p.rs}:gs={p.gs}:bs={p.bs}")
    if p.hue_shift:
        steps.append(f"hue=h={p.hue_shift}")
    steps.append(
        f"eq=contrast={p.contrast}:saturation={p.saturation}:brightness={p.brightness}"
    )
    if p.gamma != 1.0 and not use_curve:
        steps.append(f"lutyuv=y=gammaval({p.gamma})")
    if p.do_chroma_roundtrip:
        steps.append("format=yuv444p")
        steps.append("format=yuv420p")
    if p.unsharp_amount:
        steps.append(f"unsharp=5:5:{p.unsharp_amount}:5:5:0.0")
    if p.noise_strength > 0:
        steps.append(
            f"noise=alls={p.noise_strength}:allf={p.noise_flags}:all_seed={p.noise_seed}"
        )
    if p.vignette_angle:
        # The centre is deliberately left at the default. Offsetting it by even
        # 5% drives the filter's falloff term negative and crushes whole corners
        # to black, which is both visible and an uncovered-region false alarm.
        steps.append(f"vignette=angle=PI/5*{p.vignette_angle}")

    return steps


def build_spatial_chain(p, plan: GeometryPlan) -> List[str]:
    """The frame-level half of the filter graph, shared by video and images.

    Every step here is a function of one frame plus the resolved geometry, so it
    applies unchanged to a still. The time-dependent stages — ``setpts`` and the
    fps jitter — stay with the caller, and the corner-drift term is emitted only
    when the params carry a drift period, so a still (period 0) gets a static
    ``eval=init`` perspective.

    ``p`` is duck-typed: :class:`UniqueParams` and the image pipeline's
    ``ImageParams`` both satisfy it. Keep it that way — two copies of this graph
    would drift apart, and the measurements in CLAUDE.md would stop applying to
    both pipelines at once.
    """
    return build_geometry_steps(p, plan) + build_effect_steps(p)


def build_video_chain(p: UniqueParams, plan: GeometryPlan, has_overlay: bool,
                      source_fps: float) -> str:
    """Assemble the filter_complex video graph."""
    tw, th = p.target_width, p.target_height

    steps: List[str] = [f"setpts={round(1 / p.video_speed, 6)}*PTS"]
    steps += build_spatial_chain(p, plan)
    if p.fps_scale != 1.0 and source_fps > 0:
        steps.append(f"fps={round(source_fps * p.fps_scale, 6)}")

    fg = ",".join(steps)

    if plan.needs_background:
        # Quarter-resolution blur: 4x cheaper than blurring at full size.
        bg_w, bg_h = _even(tw // 4), _even(th // 4)
        chain = (
            f"[0:v]split=2[bg_src][fg_src];"
            f"[bg_src]scale={bg_w}:{bg_h},avgblur=sizeX=5:sizeY=5,scale={tw}:{th}[bg];"
            f"[fg_src]{fg}[fg];"
            f"[bg][fg]overlay={(tw - plan.fg_w) // 2}:{(th - plan.fg_h) // 2}[base]"
        )
    else:
        # Source already matches the target aspect: the blurred backdrop would
        # be fully occluded, so skip building it at all.
        chain = f"[0:v]{fg}[base]"

    if has_overlay:
        chain += (
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
        chain += ";[base]null[v_final]"

    return chain


def build_audio_chain(p: UniqueParams, has_audio: bool, safe_pitch: float,
                      safe_speed: float) -> str:
    """Assemble the filter_complex audio graph."""
    if not has_audio:
        return (
            f"anullsrc=channel_layout=stereo:sample_rate={p.audio_sample_rate}[a_final]"
        )

    combined_tempo = clamp(round((1 / safe_pitch) * safe_speed, 4), 0.5, 2.0)
    steps = [f"aresample={AUDIO_WORK_SAMPLE_RATE}"]
    if p.adelay_ms > 0:
        steps.append(f"adelay={p.adelay_ms}|{p.adelay_ms}")
    steps.append(f"asetrate={AUDIO_WORK_SAMPLE_RATE}*{safe_pitch}")
    steps.append(f"atempo={combined_tempo}")
    steps.append(f"aresample={AUDIO_WORK_SAMPLE_RATE}")

    for freq, q, gain in p.audio_notches:
        steps.append(f"equalizer=f={freq}:width_type=q:w={q}:g={gain}")

    # A narrow notch barely moves a fingerprint that bins the spectrum into a
    # few dozen wide log bands. A gentle shelf at each end does, and under 1 dB
    # it is inaudible.
    if p.audio_bass_db:
        steps.append(f"bass=g={p.audio_bass_db}:f=200:width_type=q:w=0.7")
    if p.audio_treble_db:
        steps.append(f"treble=g={p.audio_treble_db}:f=6000:width_type=q:w=0.7")

    if p.audio_highpass > 0:
        steps.append(f"highpass=f={int(p.audio_highpass)}")
    if p.audio_lowpass > 0:
        steps.append(f"lowpass=f={int(p.audio_lowpass)}")
    if p.do_audio_resample:
        # Deliberate rate round-trip: resampling touches every sample.
        detour = 44100 if p.audio_sample_rate != 44100 else 48000
        steps.append(f"aresample={detour}")

    if steps[-1] != f"aresample={p.audio_sample_rate}":
        steps.append(f"aresample={p.audio_sample_rate}")

    dry = "[0:a]" + ",".join(steps)
    if p.audio_noise_dbfs >= 0:
        return dry + ",apad[a_final]"

    # A noise floor well under a typical room tone, mixed in as two independent
    # channels so it is not a correlated centre image. It has to go in before
    # apad, or amix would follow an input that never ends. Forcing stereo here
    # upmixes a mono source, which is what the phone profiles in the metadata
    # would have recorded anyway.
    amplitude = round(10 ** (p.audio_noise_dbfs / 20.0), 8)
    sr = p.audio_sample_rate
    return (
        f"{dry},aformat=channel_layouts=stereo[a_dry];"
        f"anoisesrc=color=pink:sample_rate={sr}:amplitude={amplitude}"
        f":seed={p.audio_noise_seed}[a_n0];"
        f"anoisesrc=color=pink:sample_rate={sr}:amplitude={amplitude}"
        f":seed={p.audio_noise_seed + 1}[a_n1];"
        f"[a_n0][a_n1]amerge=inputs=2,aformat=channel_layouts=stereo[a_noise];"
        f"[a_dry][a_noise]amix=inputs=2:duration=first:normalize=0,apad[a_final]"
    )


def build_metadata_args(profile: dict, date_str: str) -> List[str]:
    """Camera-style metadata, written in the namespace the real device uses."""
    gps = generate_gps_string()
    tz_offset = random.choice(["+0000", "+0100", "+0200", "+0300", "-0500", "-0800", "+0900"])
    args = [
        "-metadata", f"creation_time={date_str}",
        "-metadata", f"date={date_str}",
    ]

    if profile.get("meta_style") == "apple":
        args += [
            "-metadata", f"com.apple.quicktime.make={profile['make']}",
            "-metadata", f"com.apple.quicktime.model={profile['model']}",
            "-metadata", f"com.apple.quicktime.software={profile['sw']}",
            "-metadata", f"com.apple.quicktime.creationdate={date_str}{tz_offset}",
            "-metadata", f"com.apple.quicktime.location.ISO6709={gps}",
        ]
    else:
        args += [
            "-metadata", f"make={profile['make']}",
            "-metadata", f"model={profile['model']}",
            "-metadata", f"software={profile['sw']}",
            "-metadata", f"com.android.version={profile.get('android_version', '13')}",
            "-metadata", f"location={gps}",
        ]

    args += [
        "-metadata:s:v:0", f"handler_name={profile['video_handler']}",
        "-metadata:s:a:0", f"handler_name={profile['audio_handler']}",
        # ffmpeg otherwise stamps "Lavc<version> libx264" on the video stream.
        "-metadata:s:v:0", "encoder=",
        "-metadata:s:a:0", "encoder=",
    ]
    return args


def _set_file_times(path: str, date_str: str):
    """Match filesystem timestamps to the fake capture date."""
    try:
        ts = datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%S").timestamp()
        os.utime(path, (ts, ts))
    except (ValueError, OSError):
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

    p = params
    overlay_path = p.overlay_file if p.overlay_file else ""
    has_overlay = bool(overlay_path) and os.path.isfile(overlay_path)

    info = probe_file(input_path)
    if not info.has_geometry:
        return False, "could not read video dimensions (unsupported or corrupt file)"

    duration = info.duration
    if duration and (p.trim_start + p.trim_end) >= duration * 0.4:
        p.trim_start = 0.1
        p.trim_end = 0.1

    profile = random.choice(DEVICE_PROFILES)
    date_str = UniqueParams.random_date_str()

    if p.fake_meta:
        output_path, date_str = reserve_natural_filepath(
            output_folder, profile, date_str)
    else:
        output_path = unique_filepath(output_folder, f"uniq_{index}.mp4")

    trim_args = ["-ss", str(p.trim_start)]
    effective_duration = None
    if duration:
        candidate = duration - p.trim_start - p.trim_end
        if candidate > 1.0:
            effective_duration = candidate
            trim_args.extend(["-t", str(round(candidate, 3))])
        else:
            # Trim was skipped, so progress must be measured against what is
            # actually decoded: everything after the start offset.
            effective_duration = max(0.1, duration - p.trim_start)

    inputs = trim_args + ["-i", input_path]
    if has_overlay:
        inputs.extend(["-stream_loop", "-1", "-i", overlay_path])

    safe_video_speed = clamp(p.video_speed, SPEECH_SAFE_VIDEO_SPEED_MIN, SPEECH_SAFE_VIDEO_SPEED_MAX)
    safe_pitch = clamp(p.pitch, SPEECH_SAFE_PITCH_MIN, SPEECH_SAFE_PITCH_MAX)
    p.video_speed = safe_video_speed
    p.pitch = safe_pitch

    plan = plan_geometry(info.width, info.height, p)
    video_chain = build_video_chain(p, plan, has_overlay, info.fps)
    audio_chain = build_audio_chain(p, info.has_audio, safe_pitch, safe_video_speed)
    filter_complex = f"{video_chain};{audio_chain}"

    requested_encoder = p.encoder if p.encoder in ENCODER_LABELS else ENCODER_LIBX264
    encoders = available_video_encoders()
    encoder = requested_encoder if requested_encoder in encoders else ENCODER_LIBX264
    if encoder != ENCODER_LIBX264 and not p.video_bitrate_kbps:
        p.video_bitrate_kbps = 8000

    # No -filter_threads / -filter_complex_threads here, deliberately. The job
    # *is* filter-bound rather than encoder-bound, so capping only the encoder
    # with -threads looks like it leaves the larger half of the work
    # oversubscribing the machine — but measured on an 8-core Mac, a batch of
    # four 1080x1920 clips at the default two workers went 20.4s -> 24.5s with
    # both options set to cores//workers, and 21.7s -> 21.2s (inside the noise)
    # at four workers. ffmpeg's slice threading does not partition the way the
    # oversubscription story assumes. Re-measure before adding them back.

    def _encoder_args(enc: str) -> List[str]:
        args = ["-c:v", enc, "-pix_fmt", "yuv420p"]
        if enc == ENCODER_LIBX264:
            args += [
                "-profile:v", p.h264_profile,
                "-level", p.h264_level,
                "-preset", p.preset,
                "-crf", str(p.crf),
                "-g", str(p.gop_size),
                "-x264-params",
                f"deblock={p.deblock_alpha},{p.deblock_beta}:aq-strength={p.aq_strength}"
                f":psy-rd={p.psy_rd},0.00:qcomp={p.qcomp}",
            ]
        else:
            args += [
                "-b:v", f"{int(p.video_bitrate_kbps)}k",
                "-maxrate", f"{int(p.video_bitrate_kbps * 1.6)}k",
                "-bufsize", f"{int(p.video_bitrate_kbps * 2)}k",
                "-g", str(p.gop_size),
                "-allow_sw", "1",
            ]
        if p.threads > 0:
            args += ["-threads", str(p.threads)]
        return args

    def _build_cmd(enc: str) -> List[str]:
        cmd = [
            get_ffmpeg_path(), "-y", "-nostdin",
        ] + inputs + [
            "-filter_complex", filter_complex,
            "-map", "[v_final]",
            "-map", "[a_final]",
        ] + _encoder_args(enc) + [
            "-c:a", "aac",
            "-b:a", f"{p.audio_bitrate}k",
            "-ar", str(p.audio_sample_rate),
            "-shortest",
            "-map_metadata", "-1",
            "-bsf:v", SEI_STRIP_BSF,
            # bitexact suppresses the "Lavf<ver>" / "Lavc<ver> libx264" writer tags.
            "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        ]

        if p.fake_meta:
            cmd += ["-brand", profile.get("brand", "mp42")]
            cmd += ["-video_track_timescale", str(profile.get("timescale", 600))]
            cmd += build_metadata_args(profile, date_str)

        cmd += [
            "-movflags", "+faststart+use_metadata_tags",
            "-progress", "pipe:1",
            "-loglevel", "error",
            output_path,
        ]
        return cmd

    deadline = max(_FFMPEG_TIMEOUT,
                   (effective_duration or 0) * _FFMPEG_TIMEOUT_PER_SECOND)

    def _run(cmd: List[str]) -> Tuple[str, str]:
        """Run one ffmpeg invocation. Returns (status, detail).

        status is ``ok``, ``cancelled``, ``failed`` — a non-zero exit, the one
        case worth retrying on another encoder — or ``gave-up`` for a launch
        failure, a stall, a timeout or a full disk, where a second attempt
        would only cost the same wait again. The caller owns the output file,
        so nothing is unlinked here: a failed hardware encode is retried
        against the same path.

        Cancellation, the stall check and the deadline all live in a watchdog
        thread rather than in the stdout loop. The loop blocks on ffmpeg's
        output, so a hung ffmpeg — the case those checks exist for — used to
        leave them unreachable, and Cancel did nothing.
        """
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            return "gave-up", f"ffmpeg failed to launch: {exc}"

        stderr_lines: List[str] = []
        # Monotonic time the output position last advanced; the watchdog reads it.
        last_advance = [time.monotonic()]
        verdict: List[str] = []
        stop = threading.Event()

        def _read_stderr():
            if not proc.stderr:
                return
            for err_line in proc.stderr:
                if err_line:
                    stderr_lines.append(err_line.rstrip())
                    if len(stderr_lines) > 30:
                        del stderr_lines[:10]

        def _watch():
            started = time.monotonic()
            while not stop.wait(0.25):
                now = time.monotonic()
                if cancelled and cancelled():
                    reason = "cancelled"
                elif now - last_advance[0] > _FFMPEG_STALL_TIMEOUT:
                    reason = "stalled"
                elif now - started > deadline:
                    reason = "deadline"
                else:
                    continue
                verdict.append(reason)
                _kill_process(proc)
                return

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()
        watchdog = threading.Thread(target=_watch, daemon=True)
        watchdog.start()

        try:
            last_time = -1.0
            for line in proc.stdout:
                if not line.startswith("out_time="):
                    continue
                m = _OUT_TIME_RE.match(line.strip().split("=", 1)[1])
                if not m:
                    continue    # "N/A" before the first frame
                secs = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                        + int(m.group(3)) + float(f"0.{m.group(4)}"))
                if secs > last_time:
                    last_time = secs
                    last_advance[0] = time.monotonic()
                    if effective_duration and file_progress_callback:
                        file_progress_callback(min(secs / effective_duration, 1.0))
            # stdout closes as ffmpeg exits; the watchdog still covers a
            # process that closes it and then fails to exit.
            proc.wait()
        finally:
            stop.set()
            watchdog.join(timeout=15)
            stderr_thread.join(timeout=1)

        name = os.path.basename(input_path)
        if verdict:
            if verdict[0] == "cancelled":
                return "cancelled", "cancelled"
            if verdict[0] == "stalled":
                log.warning("ffmpeg made no progress on %s for %ss; killed\n"
                            "command: %s\nstderr:\n%s", name,
                            _FFMPEG_STALL_TIMEOUT, subprocess.list2cmdline(cmd),
                            "\n".join(stderr_lines))
                return "gave-up", (f"ffmpeg stopped making progress for "
                                   f"{_FFMPEG_STALL_TIMEOUT}s and was stopped")
            log.warning("ffmpeg exceeded %.0fs on %s; killed", deadline, name)
            return "gave-up", f"ffmpeg timed out after {deadline:.0f}s"

        if proc.returncode != 0 and is_disk_full("\n".join(stderr_lines)):
            log.error("disk full while writing %s", output_path)
            return "gave-up", f"{DISK_FULL_ERROR} ({output_folder})"

        if proc.returncode != 0:
            stderr_out = "\n".join(stderr_lines)
            # The UI gets 500 characters; the log gets the command and the
            # whole retained tail, which is what a bug report needs.
            log.warning("ffmpeg failed on %s (exit %s)\ncommand: %s\nstderr:\n%s",
                        os.path.basename(input_path), proc.returncode,
                        subprocess.list2cmdline(cmd), stderr_out)
            return "failed", (f"ffmpeg exited with code {proc.returncode}: "
                              f"{stderr_out[:500]}")
        return "ok", ""

    if cancelled and cancelled():
        _cleanup_output(output_path)
        return False, "cancelled"

    status, detail = _run(_build_cmd(encoder))

    if status == "failed" and encoder != ENCODER_LIBX264:
        # Being listed by `-encoders` only says the encoder was compiled in, not
        # that it will open: every VideoToolbox session can be busy, or the
        # resolution unsupported. One retry on the software path costs less than
        # a probe encode at startup and catches every one of those reasons.
        # The reserved output path is kept rather than unlinked: ffmpeg's -y
        # truncates whatever the failed attempt left, and dropping the name
        # would hand it to another worker mid-batch.
        log.info("%s failed on %s, retrying with libx264",
                 encoder, os.path.basename(input_path))
        encoder = ENCODER_LIBX264
        if file_progress_callback:
            file_progress_callback(0.0)
        status, detail = _run(_build_cmd(encoder))

    if status != "ok":
        _cleanup_output(output_path)
        return False, detail

    if p.fake_meta:
        _set_file_times(output_path, date_str)

    return True, ""


def _cleanup_output(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


def default_worker_count() -> int:
    """Concurrent ffmpeg processes.

    A job is filter-bound, not encoder-bound — roughly 75% of its CPU goes to
    the graph against 18% to x264 — so the ceiling is not "x264 stops scaling".
    It is measured: past three concurrent jobs the wall clock stops improving,
    because the graphs contend for the same cores. That holds for the
    VideoToolbox encoders too, since the bottleneck is not the encoder; do not
    raise the count for them.
    """
    cores = os.cpu_count() or 4
    return int(clamp(cores // 4, 1, 3))


def process_batch(
    input_files: List[str],
    output_folder: str,
    params_template: Optional[UniqueParams] = None,
    ranges: Optional[RandomRanges] = None,
    randomize_each: bool = True,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    file_progress_callback: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    max_workers: Optional[int] = None,
) -> Tuple[int, List[str]]:
    """
    Process a batch of video files, several at a time.
    Returns (success_count, list_of_error_messages).

    progress_callback(completed_count, total, filename)
    file_progress_callback(fraction_0_to_1) — mean progress across active jobs
    """
    os.makedirs(output_folder, exist_ok=True)

    total = len(input_files)
    if total == 0:
        if progress_callback:
            progress_callback(0, 0, "")
        return 0, []

    need_index = params_template is None or not params_template.fake_meta
    start_index = next_output_index(output_folder) if need_index else 1

    workers = max_workers if max_workers and max_workers > 0 else default_worker_count()
    workers = int(clamp(workers, 1, max(1, total)))

    cores = os.cpu_count() or 4
    # Bounds the encoder *and* the filter graph of each job (see
    # process_single_video). A lone worker keeps 0 — ffmpeg's own auto — because
    # with nothing to contend against there is no oversubscription to prevent.
    per_job_threads = 0 if workers == 1 else max(1, cores // workers)

    state_lock = threading.Lock()
    completed = 0
    success_count = 0
    errors: List[str] = []
    active_progress = {}
    disk_full = threading.Event()
    not_started: List[str] = []

    def _report_file_progress(job_id: int, fraction: float):
        if not file_progress_callback:
            return
        with state_lock:
            active_progress[job_id] = fraction
            mean = sum(active_progress.values()) / len(active_progress)
        file_progress_callback(mean)

    def _run(job: Tuple[int, str]):
        """Never raises.

        ``pool.map`` re-raises the first worker exception out of ``process_batch``,
        which would discard the results of every other file in the batch. An
        unexpected failure is recorded against its own file instead.
        """
        nonlocal completed
        job_id, fpath = job
        fname = os.path.basename(fpath)
        try:
            _run_one(job_id, fpath)
        except Exception as exc:  # noqa: BLE001 - one bad file must not sink the batch
            log.exception("unexpected error processing %s", fname)
            with state_lock:
                completed += 1
                done = completed
                active_progress.pop(job_id, None)
                errors.append(f"{fname}: unexpected error: {exc}")
            if progress_callback:
                progress_callback(done, total, fname)

    def _run_one(job_id: int, fpath: str):
        nonlocal completed, success_count
        fname = os.path.basename(fpath)

        if cancelled and cancelled():
            return
        if disk_full.is_set():
            with state_lock:
                not_started.append(fname)
            return

        with state_lock:
            active_progress[job_id] = 0.0

        if randomize_each:
            params = UniqueParams.generate_random(ranges)
            if params_template:
                for field_name in _STATIC_FIELDS:
                    setattr(params, field_name, getattr(params_template, field_name))
        else:
            params = params_template or UniqueParams.generate_random(ranges)

        params.threads = per_job_threads

        ok, err = process_single_video(
            input_path=fpath,
            output_folder=output_folder,
            index=start_index + job_id,
            params=params,
            file_progress_callback=lambda pct, jid=job_id: _report_file_progress(jid, pct),
            cancelled=cancelled,
        )

        with state_lock:
            completed += 1
            active_progress.pop(job_id, None)
            if ok:
                success_count += 1
            elif err and err != "cancelled":
                errors.append(f"{fname}: {err}")
            done = completed
        if not ok and err and err != "cancelled":
            log.warning("video failed: %s: %s", fpath, err)
            if err.startswith(DISK_FULL_ERROR):
                disk_full.set()

        if progress_callback:
            progress_callback(done, total, fname)

    jobs = list(enumerate(input_files))
    if workers == 1:
        for job in jobs:
            if cancelled and cancelled():
                break
            _run(job)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_run, jobs))

    if not_started:
        errors.append(f"{len(not_started)} file(s) not started: {DISK_FULL_ERROR}")

    if progress_callback:
        progress_callback(total, total, "")

    return success_count, errors
