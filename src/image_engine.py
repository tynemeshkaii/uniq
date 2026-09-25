"""Still-image uniqualization, sharing the video pipeline's spatial filter graph.

Design notes worth knowing before changing anything here:

* **The filter graph is not duplicated.** ``ImageParams`` deliberately carries
  the same field names the spatial half of the video graph reads, so
  ``engine.build_spatial_chain`` and ``engine.plan_geometry`` are called
  unchanged. Two copies of that graph would drift apart within a few commits
  and the measurements in CLAUDE.md would stop describing both pipelines.

* **The caps are tighter than video's, on purpose.** A still is examined, not
  watched: nothing averages the grain away over time, and the viewer can zoom.
  Grain and sharpening that vanish in motion are plainly visible on a photo, so
  ``MAX_IMAGE_*`` sits below the video equivalents. Do not import the video
  ranges here as a shortcut.

* **The canvas follows the source.** Video re-frames everything onto a fixed
  1080x1920 with a blurred backdrop. Doing that to an ad creative would be
  destructive — Meta's placements are driven by the creative's own aspect —
  so the target is derived from the source aspect and ``needs_background``
  never fires. ``ImageRanges.preserve_full_frame`` goes further and holds the
  whole reframing stage at identity, so the output keeps every source pixel.

* **Metadata is a post-pass.** ffmpeg's mjpeg encoder discards ``-metadata``,
  so the camera identity is written by ``exif.py`` after the encode.
"""

import logging
import math
import os
import random
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import exif
from engine import (
    DEVICE_PROFILES,
    MAX_HUE_DEGREES,
    MAX_ROTATE_DEGREES,
    MAX_CURVE_JITTER,
    UniqueParams,
    _even,
    build_effect_steps,
    build_geometry_steps,
    build_tone_curve,
    clamp,
    default_worker_count,
    generate_gps_coords,
    get_ffmpeg_path,
    reserve_natural_filepath,
    _set_file_times,
    plan_geometry,
    probe_file,
    unique_filepath,
)

log = logging.getLogger(__name__)

# Formats accepted on input. WEBP is decode-only in the bundled ffmpeg build,
# so it is transcoded out rather than round-tripped; HEIC has no decoder at all
# and is rejected with a message that says so instead of failing obscurely.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
UNSUPPORTED_IMAGE_EXTENSIONS = {".heic", ".heif", ".avif"}

_FFMPEG_IMAGE_TIMEOUT = 120

# ── Perceptual safety caps for stills ───────────────────────────────────
# Lower than the video pipeline's, because a still gets no temporal averaging
# and gets zoomed. The UI spin boxes are bound by these same numbers; changing
# one means changing both and re-running the quality gate.
MAX_IMAGE_NOISE = 4
MAX_IMAGE_UNSHARP = 0.5
MAX_IMAGE_VIGNETTE = 0.22
MAX_IMAGE_ROTATE_DEGREES = 0.6

# JPEG quality is ffmpeg's -q:v: 2 is near-lossless, 31 is unusable. Meta
# re-encodes on upload, so anything below about 6 is thrown away by their
# transcode while still costing bytes here.
JPEG_QUALITY_FLOOR = 2
JPEG_QUALITY_CEILING = 8

# Plausible software strings per make. Phones do not write a bare OS version in
# the Software field; Apple writes the iOS version, Android OEMs write a build
# id, so a bare "Android 13" would read as fabricated.
_SOFTWARE_STYLES = {
    "Apple": lambda profile: profile["sw"],
    "Samsung": lambda profile: random.choice(
        ["S918BXXU3AWJ1", "S908BXXS5CWD1", "S928BXXU1AXBA"]),
    "Google": lambda profile: "HDR+ 1.0.{}".format(random.randint(500000, 599999)),
    "Sony": lambda profile: "Xperia 1 V {}".format(random.randint(1, 9)),
    "OnePlus": lambda profile: "OnePlus11_{}".format(random.randint(11, 15)),
    "Xiaomi": lambda profile: "MIUI {}".format(random.choice(["14.0.3", "14.0.7"])),
}

# Optics per make: (f-number, focal length mm, 35mm-equivalent, lens template).
_CAMERA_OPTICS = {
    "Apple": (1.78, 6.86, 24, "{model} back camera {focal:.2f}mm f/{fnum:.2f}"),
    "Samsung": (1.8, 6.4, 24, "{model} back camera {focal:.1f}mm f/{fnum:.1f}"),
    "Google": (1.85, 6.81, 25, "{model} back camera {focal:.2f}mm f/{fnum:.2f}"),
    "Sony": (1.9, 6.9, 24, "{model} back camera {focal:.1f}mm f/{fnum:.1f}"),
    "OnePlus": (1.75, 6.06, 23, "{model} back camera {focal:.2f}mm f/{fnum:.2f}"),
    "Xiaomi": (1.9, 8.7, 23, "{model} back camera {focal:.1f}mm f/{fnum:.1f}"),
}


@dataclass
class ImageRanges:
    """Per-batch spec for stills. The engine draws a fresh set per file.

    Geometry defaults mirror the video pipeline's reasoning — reframing is the
    strongest lever there and there is no reason for a still to behave
    differently — but the effect ranges are narrower, since a photo is looked
    at rather than watched.
    """
    # Geometry
    #
    # Measured on four photographs, 8 draws each, pHash distance from the
    # source against a matcher threshold near 10:
    #
    #   zoom 1.06-1.14 -> median 15, min  8, 10th pct 10
    #   zoom 1.08-1.16 -> median 18, min 10, 10th pct 12   <- chosen
    #   zoom 1.10-1.18 -> median 18, min 12, 10th pct 16
    #
    # 1.10-1.18 buys a higher floor and no higher median, for a real loss of
    # resolution, so the operating point matches the video pipeline's.
    #
    # Everything else in this pipeline measured a median of 0 in isolation —
    # rotation, lens distortion, micro-warp, the tone curve, hue, eq, grain,
    # sharpening, vignette and the JPEG quality draw all left the hash where
    # they found it. They stay because they defeat different attacks (byte
    # hashes, colour normalisation, a solved-alignment matcher), not because
    # they move this number. Reframing is the only thing that moves it.
    zoom: Tuple[float, float] = (1.08, 1.16)
    rotate_max: float = 0.4
    k1_max: float = 0.005
    crop_margin: Tuple[int, int] = (4, 14)
    pan_max: float = 0.6

    # Color — the same operating point as video; a colour grade this small is
    # no more visible on a still than in motion.
    color_shift_max: float = 0.06
    contrast: Tuple[float, float] = (0.96, 1.04)
    saturation: Tuple[float, float] = (0.94, 1.06)
    brightness_max: float = 0.025
    hue_max: float = 3.0

    # Effects — tighter than video. See the MAX_IMAGE_* note above.
    noise: Tuple[int, int] = (1, 3)
    unsharp: Tuple[float, float] = (-0.20, 0.35)
    vignette: Tuple[float, float] = (0.04, 0.15)

    # Encode
    jpeg_quality: Tuple[int, int] = (2, 4)
    png_compression: Tuple[int, int] = (6, 9)
    # A resample of well under a percent. The exact pixel dimensions of an
    # output are themselves a grouping key, and this breaks that for free —
    # it costs nothing, since the frame is already being rescaled by the crop.
    size_jitter: Tuple[float, float] = (0.985, 1.0)
    # 0 keeps the source resolution. Meta downscales above 1936px on the long
    # edge anyway, so capping here only saves upload bytes.
    max_long_side: int = 0

    # Toggles
    # When set, the still keeps its whole frame: nothing is cropped away and no
    # black wedge is left anywhere along an edge. That rules out the entire
    # reframing stage, not only the zoom — rotation and lens distortion leave
    # uncovered corners that only a crop can hide, the micro-warp pulls the
    # corners inward for the same reason, and crop_margin/pan are crops by
    # definition. So this toggle zeroes all six (see generate_random).
    #
    # The cost is real and worth stating: the ablation in CLAUDE.md puts every
    # remaining stage at a pHash distance of 0 on stills, so a full-frame output
    # is near-identical to the source perceptually. What still varies per file is
    # colour, grain/sharpening, the encode (quality, entropy coding, PNG
    # predictor), the output dimensions and the fabricated metadata — enough
    # against byte-level and metadata dedup, not against a perceptual matcher.
    preserve_full_frame: bool = True
    use_gamma: bool = True
    use_micro_warp: bool = True
    use_tone_curve: bool = True
    use_chroma_roundtrip: bool = True
    use_jpeg_huffman_jitter: bool = True
    use_hflip: bool = False        # mirrors on-screen text — opt-in only
    fake_meta: bool = True

    def draw_int(self, rng: Tuple[int, int]) -> int:
        lo, hi = min(rng), max(rng)
        return random.randint(int(lo), int(hi))

    def draw_float(self, rng: Tuple[float, float], digits: int = 3) -> float:
        lo, hi = min(rng), max(rng)
        return round(random.uniform(lo, hi), digits)


@dataclass
class ImageParams:
    """Concrete values for one output image.

    The field names between ``k1`` and ``vignette_angle`` are load-bearing:
    ``engine.plan_geometry`` and ``engine.build_spatial_chain`` read them off
    this object by name. Renaming one here silently changes the filter graph.
    """
    # Geometry (read by plan_geometry / build_spatial_chain)
    k1: float = 0.0
    lens_cx: float = 0.5
    lens_cy: float = 0.5
    rotate: float = 0.0
    zoom: float = 1.06
    crop_margin: int = 8
    pan_x: float = 0.0
    pan_y: float = 0.0
    hflip: bool = False
    warp_offsets: Tuple[int, ...] = (0,) * 8
    # A still has no timeline, so the drift endpoint equals the start and the
    # period is zero — build_spatial_chain then emits a static eval=init warp.
    warp_drift_offsets: Tuple[int, ...] = (0,) * 8
    warp_drift_period: int = 0
    warp_drift_phases: Tuple[float, ...] = (0.0,) * 8

    # Color
    rs: float = 0.0
    gs: float = 0.0
    bs: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    brightness: float = 0.0
    hue_shift: float = 0.0
    gamma: float = 1.0
    tone_curve: Tuple[str, str, str] = ("", "", "")
    do_chroma_roundtrip: bool = False

    # Resampling kernel for the zoom upscale. See build_spatial_chain.
    scale_flags: str = "lanczos"

    # Effects
    unsharp_amount: float = 0.1
    noise_strength: int = 2
    noise_flags: str = "u"
    noise_seed: int = 0
    vignette_angle: float = 0.1

    # Output geometry, filled in per file from the source dimensions.
    target_width: int = 0
    target_height: int = 0
    size_jitter: float = 1.0
    max_long_side: int = 0

    # Encode
    # "auto" follows the source (see choose_output_format); "jpeg"/"png" force
    # one. It defaults to auto so a PNG is not silently flattened to JPEG.
    output_format: str = "auto"
    jpeg_quality: int = 3
    jpeg_huffman: str = "optimal"
    png_compression: int = 7
    png_pred: str = "mixed"

    # Meta
    fake_meta: bool = True

    @classmethod
    def generate_random(cls, ranges: Optional[ImageRanges] = None) -> "ImageParams":
        r = ranges or ImageRanges()
        p = cls()

        p.k1 = round(random.uniform(-r.k1_max, r.k1_max), 4)
        p.lens_cx = round(random.uniform(0.48, 0.52), 3)
        p.lens_cy = round(random.uniform(0.48, 0.52), 3)
        rotate_max = min(r.rotate_max, MAX_IMAGE_ROTATE_DEGREES, MAX_ROTATE_DEGREES)
        p.rotate = round(random.uniform(-rotate_max, rotate_max), 3)
        p.zoom = max(1.0, r.draw_float(r.zoom))
        p.crop_margin = r.draw_int(r.crop_margin)
        p.pan_x = round(random.uniform(-r.pan_max, r.pan_max), 3)
        p.pan_y = round(random.uniform(-r.pan_max, r.pan_max), 3)
        p.hflip = bool(r.use_hflip)

        if r.use_micro_warp:
            p.warp_offsets = tuple(random.randint(0, 3) for _ in range(8))
            # Static: the drift endpoint has to equal the start, or the shared
            # chain builder would emit a per-frame sine into a single frame.
            p.warp_drift_offsets = p.warp_offsets
        else:
            p.warp_offsets = (0,) * 8
            p.warp_drift_offsets = (0,) * 8
        p.warp_drift_period = 0

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
            p.tone_curve = tuple(
                build_tone_curve(channel_shift, p.gamma, MAX_CURVE_JITTER)
                for channel_shift in (p.rs, p.gs, p.bs)
            )
        else:
            p.tone_curve = ("", "", "")
        p.do_chroma_roundtrip = bool(r.use_chroma_roundtrip)

        p.unsharp_amount = clamp(r.draw_float(r.unsharp, 2),
                                 -MAX_IMAGE_UNSHARP, MAX_IMAGE_UNSHARP)
        p.noise_strength = int(min(r.draw_int(r.noise), MAX_IMAGE_NOISE))
        # "t" is temporal noise and means nothing to a single frame; "u"
        # (uniform) and "p" (pattern) are the two that apply.
        p.noise_flags = random.choice(["u", "p", "u+p"])
        p.noise_seed = random.randint(0, 2 ** 31 - 2)
        p.vignette_angle = min(r.draw_float(r.vignette, 2), MAX_IMAGE_VIGNETTE)

        p.size_jitter = r.draw_float(r.size_jitter, 4)
        p.max_long_side = int(r.max_long_side)

        if r.preserve_full_frame:
            # Everything that either removes pixels or would expose an
            # uncovered edge, held at identity. plan_geometry then resolves to
            # the whole source rectangle and the output is a pure resample.
            p.zoom = 1.0
            p.crop_margin = 0
            p.pan_x = 0.0
            p.pan_y = 0.0
            p.rotate = 0.0
            p.k1 = 0.0
            p.warp_offsets = (0,) * 8
            p.warp_drift_offsets = (0,) * 8

        p.jpeg_quality = int(clamp(r.draw_int(r.jpeg_quality),
                                   JPEG_QUALITY_FLOOR, JPEG_QUALITY_CEILING))
        # Two entropy-coding strategies that produce completely different bytes
        # for identical pixels. Free against a byte-level or exact-hash dedup,
        # worth nothing against a perceptual one — cheap enough to always do.
        p.jpeg_huffman = (random.choice(["optimal", "default"])
                          if r.use_jpeg_huffman_jitter else "optimal")
        p.png_compression = r.draw_int(r.png_compression)
        p.png_pred = random.choice(["mixed", "avg", "paeth", "sub"])
        p.fake_meta = bool(r.fake_meta)
        return p


# Output settings copied from the UI template rather than randomized, matching
# the video pipeline's _STATIC_FIELDS discipline. A new output-container field
# added to ImageParams must be listed here or it will vary per file.
_STATIC_IMAGE_FIELDS = [
    "output_format", "max_long_side", "fake_meta",
]


def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTENSIONS


def is_unsupported_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in UNSUPPORTED_IMAGE_EXTENSIONS


def choose_output_format(input_path: str, requested: str = "auto") -> str:
    """JPEG in, JPEG out; PNG stays PNG; everything else becomes JPEG.

    PNG is kept lossless because flattening a graphic to JPEG puts ringing on
    every hard edge, which is exactly the kind of visible damage the pipeline
    exists to avoid. WEBP has no encoder in the bundled ffmpeg, so it leaves as
    JPEG whatever the request says.
    """
    if requested in ("jpeg", "png"):
        return requested
    return "png" if os.path.splitext(input_path)[1].lower() == ".png" else "jpeg"


def plan_image_canvas(src_w: int, src_h: int, p: ImageParams) -> Tuple[int, int]:
    """Output dimensions: the source aspect, jittered, optionally capped.

    Holding the aspect exactly is what keeps ``plan_geometry`` from deciding it
    needs a blurred backdrop, so the still never gets bars.

    The cap is applied first and the jitter multiplied on top of it, not the
    other way round. Overwriting the scale with ``cap / long_side`` pinned every
    capped output to exactly the cap, so two files from the same source came out
    the same size — which disables the size jitter precisely in the mode where
    someone is most likely to have turned the cap on.
    """
    long_side = max(src_w, src_h)
    scale = 1.0
    if p.max_long_side and long_side > p.max_long_side:
        scale = p.max_long_side / long_side
    scale *= float(p.size_jitter)

    # Only the long side is rounded; the short one is derived from it. Rounding
    # both independently drifted the output aspect away from the source by a
    # fraction of a percent, which was enough for plan_geometry's aspect snap
    # to shave pixels off a frame the caller asked to keep whole.
    if src_w >= src_h:
        out_w = _even(round(src_w * scale))
        out_h = _even(round(out_w * src_h / src_w))
    else:
        out_h = _even(round(src_h * scale))
        out_w = _even(round(out_h * src_w / src_h))
    return out_w, out_h


def build_image_graph(p: ImageParams, plan, src_w: int, src_h: int,
                      has_alpha: bool) -> List[str]:
    """The ffmpeg filter arguments for one still.

    Three shapes, because transparency cannot be ignored: an ad creative is
    often a logo or a cut-out on a transparent background, and the effect stage
    converts through ``yuv420p``, which silently composites it onto black.

    * **No alpha** — a plain ``-vf`` chain, the shared spatial graph.
    * **Alpha, PNG out** — geometry runs once on RGBA, then the frame is split:
      the alpha plane is extracted and set aside while the colour branch takes
      the effects (and whatever format conversions they force), and the two are
      merged back at the end. Effects never touch alpha, and the alpha plane
      gets exactly the same reframing as the colour, so they stay registered.
    * **Alpha, JPEG out** — JPEG cannot store alpha at all, so the image is
      flattened onto white *before* anything else. Compositing onto white
      rather than ffmpeg's implicit black is the choice that keeps a dark logo
      on a transparent background readable.
    """
    geometry = build_geometry_steps(p, plan)
    effects = build_effect_steps(p)

    if not has_alpha:
        return ["-vf", ",".join(geometry + effects)]

    if p.output_format != "png":
        return [
            "-filter_complex",
            f"color=c=white:s={src_w}x{src_h}[bg];"
            f"[bg][0:v]overlay=shortest=1:format=auto,format=rgb24,"
            f"{','.join(geometry + effects)}[out]",
            "-map", "[out]",
        ]

    return [
        "-filter_complex",
        # format=rgba before alphaextract is required: without it the filter
        # cannot choose a format and the graph fails to configure.
        f"[0:v]format=rgba,{','.join(geometry)},split=2[colour][alpha];"
        f"[alpha]format=rgba,alphaextract,format=gray[a];"
        f"[colour]{','.join(effects)},format=gbrp[c];"
        f"[c][a]alphamerge,format=rgba[out]",
        "-map", "[out]",
    ]


def _encode_args(p: ImageParams, has_alpha: bool = False) -> List[str]:
    if p.output_format == "png":
        return [
            "-c:v", "png",
            "-compression_level", str(p.png_compression),
            "-pred", p.png_pred,
            # Pinned rather than left to negotiation: the encoder would
            # otherwise pick rgb24 off the merged graph and drop the alpha the
            # graph went to the trouble of preserving.
            "-pix_fmt", "rgba" if has_alpha else "rgb24",
        ]
    return [
        "-c:v", "mjpeg",
        "-q:v", str(p.jpeg_quality),
        "-huffman", p.jpeg_huffman,
        "-pix_fmt", "yuvj420p",
    ]


def _build_exif_fields(profile: dict, date_str: str, width: int, height: int) -> dict:
    """Assemble the fabricated camera identity for one still."""
    make = profile["make"]
    fnum, focal, focal35, lens_tmpl = _CAMERA_OPTICS.get(
        make, (1.8, 5.5, 26, "{model} back camera {focal:.1f}mm f/{fnum:.1f}"))
    software = _SOFTWARE_STYLES.get(make, lambda pr: pr["sw"])(profile)
    lat, lon, alt = generate_gps_coords()

    # A hand-held daylight exposure. Shutter and aperture are stored twice in
    # EXIF, as a plain value and as an APEX log value; writing them
    # inconsistently is a giveaway, so both are derived from the same numbers.
    shutter_den = random.choice([60, 100, 120, 250, 500, 800])
    iso = random.choice([32, 40, 50, 64, 80, 100, 125])
    apex_shutter = math.log2(shutter_den)
    apex_aperture = 2 * math.log2(fnum)

    return {
        "make": make,
        "model": profile["model"],
        "software": software,
        "datetime": date_str.replace("-", ":").replace("T", " "),
        "subsec": f"{random.randint(0, 99):02d}",
        "exposure_time": (1, shutter_den),
        "f_number": (int(round(fnum * 100)), 100),
        "iso": iso,
        "shutter_speed": (int(round(apex_shutter * 1000)), 1000),
        "aperture": (int(round(apex_aperture * 1000)), 1000),
        "focal_length": (int(round(focal * 100)), 100),
        "focal_35mm": focal35,
        "lens_make": make,
        "lens_model": lens_tmpl.format(model=profile["model"], focal=focal, fnum=fnum),
        "width": width,
        "height": height,
        "lat": lat,
        "lon": lon,
        "alt": alt,
    }


def _apply_metadata(path: str, p: ImageParams, profile: dict, date_str: str,
                    width: int, height: int) -> Optional[str]:
    """Write the camera identity into the encoded file. Returns an error or None."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()

        fields = _build_exif_fields(profile, date_str, width, height)
        if p.output_format == "png":
            out = exif.insert_png_text(data, [
                ("Make", str(fields["make"])),
                ("Model", str(fields["model"])),
                ("Software", str(fields["software"])),
                ("Creation Time", str(fields["datetime"])),
            ])
        else:
            out = exif.insert_exif(data, exif.build_exif(fields))

        with open(path, "wb") as fh:
            fh.write(out)
    except (OSError, ValueError) as exc:
        return f"metadata write failed: {exc}"
    return None


def _cleanup(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


def process_single_image(
    input_path: str,
    output_folder: str,
    index: int,
    params: Optional[ImageParams] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[bool, str]:
    """Process one image. Returns (success, error_message)."""
    if is_unsupported_image(input_path):
        ext = os.path.splitext(input_path)[1].lower()
        return False, (f"{ext} is not supported by the bundled ffmpeg — "
                       f"convert to JPEG or PNG first")

    p = params or ImageParams.generate_random()
    p.output_format = choose_output_format(input_path, p.output_format)

    # Grain and the vignette are dropped for PNG, and only for PNG. Both are
    # free-form detail laid over the picture, and a lossless codec has to store
    # every bit of it: measured against an otherwise identical encode, on a flat
    # graphic grain cost 56x the file size and the vignette 19x, against roughly
    # 1.0x for both on a photograph. PNG output is exactly the graphics case,
    # and the ablation puts both effects' contribution to the perceptual hash at
    # zero — so on this branch they are pure cost.
    #
    # Everything else stays: sharpening and the whole colour stage measured
    # 1.00-1.13x even on the flat graphic.
    if p.output_format == "png":
        p.noise_strength = 0
        p.vignette_angle = 0.0

    info = probe_file(input_path)
    if not info.has_geometry:
        return False, "could not read image dimensions (unsupported or corrupt file)"

    p.target_width, p.target_height = plan_image_canvas(info.width, info.height, p)
    plan = plan_geometry(info.width, info.height, p)

    profile = random.choice(DEVICE_PROFILES)
    date_str = UniqueParams.random_date_str()
    ext = ".png" if p.output_format == "png" else ".jpg"

    if p.fake_meta:
        output_path, date_str = reserve_natural_filepath(
            output_folder, profile, date_str, kind="image", ext=ext)
    else:
        output_path = unique_filepath(output_folder, f"uniq_{index:04d}{ext}")

    if cancelled and cancelled():
        _cleanup(output_path)
        return False, "cancelled"

    cmd = [
        get_ffmpeg_path(), "-y", "-nostdin",
        "-i", input_path,
    ] + build_image_graph(p, plan, info.width, info.height, info.has_alpha) + [
        "-frames:v", "1",
    ] + _encode_args(p, info.has_alpha) + [
        "-map_metadata", "-1",
        # Suppresses the "Lavc<ver>" comment ffmpeg otherwise embeds.
        "-fflags", "+bitexact", "-flags:v", "+bitexact",
        "-loglevel", "error",
        output_path,
    ]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FFMPEG_IMAGE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        _cleanup(output_path)
        return False, f"ffmpeg timed out after {_FFMPEG_IMAGE_TIMEOUT}s"
    except OSError as exc:
        _cleanup(output_path)
        return False, f"ffmpeg failed to launch: {exc}"

    if proc.returncode != 0:
        _cleanup(output_path)
        log.warning("ffmpeg failed on %s (exit %s)\ncommand: %s\nstderr:\n%s",
                    os.path.basename(input_path), proc.returncode,
                    subprocess.list2cmdline(cmd), proc.stderr.strip())
        return False, f"ffmpeg exited with code {proc.returncode}: {proc.stderr.strip()[:500]}"

    if p.fake_meta:
        err = _apply_metadata(output_path, p, profile, date_str,
                              plan.fg_w, plan.fg_h)
        if err:
            _cleanup(output_path)
            return False, err
        _set_file_times(output_path, date_str)

    return True, ""


def next_output_index(output_folder: str) -> int:
    max_index = 0
    if not os.path.exists(output_folder):
        return 1
    for name in os.listdir(output_folder):
        stem, ext = os.path.splitext(name)
        if stem.startswith("uniq_") and ext.lower() in (".jpg", ".png"):
            middle = stem[len("uniq_"):]
            if middle.isdigit():
                max_index = max(max_index, int(middle))
    return max_index + 1


def default_image_worker_count() -> int:
    """Stills are not x264: they scale with cores until I/O gets in the way."""
    cores = os.cpu_count() or 4
    return int(clamp(cores, 1, 8))


def image_workers_for(video_workers: Optional[int]) -> int:
    """Translate the UI's video job count onto the still pipeline's scale.

    The window has one "Parallel Jobs" control and it asks one question — how
    many files to keep in flight — but the two pipelines can absorb very
    different answers.
    A video job is filter-bound and stops paying past three concurrent ffmpegs,
    so its default is ``cores // 4``; a still is a sub-second single-frame
    encode that keeps scaling to about one job per core, so its default is
    ``cores``. Handing the video number straight to ``process_image_batch``
    therefore throttles stills to the video ceiling: measured on 12 PNGs on an
    8-core Mac, 5.55s at two workers against 2.63s at eight.

    The number is therefore carried across by its *position on each pipeline's
    own scale* rather than as a literal file count: the video scale tops out at
    three jobs and the still scale at one per core, so the video default maps to
    the still default and half the video default to half the still one. Reading
    it as a literal count is what caused the regression, and the control never
    meant "use less of the Mac" anyway — a single video job already spreads its
    filter graph across every core.

    ``None`` or a non-positive value means "unset" and yields the still default,
    matching ``process_image_batch``'s own contract.
    """
    image_default = default_image_worker_count()
    if not video_workers or video_workers <= 0:
        return image_default
    video_default = max(1, default_worker_count())
    scaled = round(video_workers * image_default / video_default)
    return int(clamp(scaled, 1, image_default))


def process_image_batch(
    input_files: List[str],
    output_folder: str,
    params_template: Optional[ImageParams] = None,
    ranges: Optional[ImageRanges] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    max_workers: Optional[int] = None,
) -> Tuple[int, List[str]]:
    """Process a batch of images concurrently.

    Mirrors ``engine.process_batch``: a fresh parameter draw per file, with the
    output-container settings copied from the template.

    progress_callback(completed_count, total, filename)
    """
    os.makedirs(output_folder, exist_ok=True)

    total = len(input_files)
    if total == 0:
        return 0, []

    need_index = params_template is None or not params_template.fake_meta
    start_index = next_output_index(output_folder) if need_index else 1

    workers = max_workers if max_workers and max_workers > 0 else default_image_worker_count()
    workers = int(clamp(workers, 1, max(1, total)))

    state_lock = threading.Lock()
    completed = 0
    success_count = 0
    errors: List[str] = []

    def _run(job: Tuple[int, str]):
        """Never raises.

        ``pool.map`` re-raises the first worker exception out of
        ``process_image_batch``, which would discard the results of every other
        file in the batch. An unexpected failure is recorded against its own
        file instead.
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
                errors.append(f"{fname}: unexpected error: {exc}")
            if progress_callback:
                progress_callback(done, total, fname)

    def _run_one(job_id: int, fpath: str):
        nonlocal completed, success_count
        fname = os.path.basename(fpath)

        if cancelled and cancelled():
            return

        params = ImageParams.generate_random(ranges)
        if params_template:
            for field_name in _STATIC_IMAGE_FIELDS:
                setattr(params, field_name, getattr(params_template, field_name))

        ok, err = process_single_image(
            input_path=fpath,
            output_folder=output_folder,
            index=start_index + job_id,
            params=params,
            cancelled=cancelled,
        )

        with state_lock:
            completed += 1
            if ok:
                success_count += 1
            elif err and err != "cancelled":
                errors.append(f"{fname}: {err}")
            done = completed
        if not ok and err and err != "cancelled":
            log.warning("image failed: %s: %s", fpath, err)

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

    return success_count, errors
