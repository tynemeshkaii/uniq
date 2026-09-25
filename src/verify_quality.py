"""
Quality gate for the uniqualization pipeline.

Answers one question: would a viewer notice that an output has been processed?

It separates the two things that change an image, because they need different
thresholds:

  * reframing  — zoom, rotation, pan, micro-crop. Moves pixels, so a pixel
    metric scores it harshly even though it looks completely normal.
  * degradation — colour drift, noise, sharpening, vignette, gamma, codec.
    This is what actually shows up as "the video looks worse".

Test A neutralises reframing and measures degradation alone against a plain
re-encode. That number is the one that has to stay high.

Run:  python3 src/verify_quality.py [source_video ...]
"""

import argparse
import copy
import os
import re
import subprocess
import sys
import tempfile
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fingerprint  # noqa: E402
import exif  # noqa: E402
from engine import (  # noqa: E402
    DEVICE_PROFILES,
    RandomRanges,
    UniqueParams,
    build_video_chain,
    check_ffmpeg_available,
    get_ffmpeg_path,
    plan_geometry,
    probe_file,
    process_single_video,
)
from image_engine import (  # noqa: E402
    IMAGE_EXTENSIONS,
    ImageParams,
    ImageRanges,
    plan_image_canvas,
    process_single_image,
)

# Thresholds below are set from a measured distribution: 30 draws across five
# clips spanning smooth gradients, a dark scene, colour bars, flat grey and a
# dense synthetic pattern. Observed spread was SSIM 0.988-0.9995 and PSNR
# 29.3-48.8 dB, with file size 0.89-1.67x the clean twin.

# The load-bearing gate. SSIM responds to structural damage, which is what a
# viewer perceives as "this looks worse". The floor sits far below the observed
# minimum, so tripping it means something is genuinely broken.
SSIM_DEGRADATION_MIN = 0.90

# Secondary, gross-breakage floor only. The colour stage shifts every pixel on
# purpose, and PSNR scores an intentional grade exactly like damage, so it runs
# a long low tail on saturated synthetic content while the picture still looks
# normal. Set below the observed minimum; real defects (an unmapped corner, a
# filter driven negative) blow past it by a wide margin.
PSNR_DEGRADATION_MIN = 25.0

LUFS_DELTA_MAX = 1.5          # EBU R128 "just noticeable" step is about 1 LU
TRUE_PEAK_MAX = -0.5          # dBTP, above this players may clip
AV_SYNC_MAX_MS = 45.0         # lip-sync detection threshold
DURATION_DRIFT_MAX = 0.15     # seconds between the audio and video tracks
SIZE_RATIO_MAX = 1.8          # vs the clean twin; grain used to inflate this 4x
# Below this the source compresses so well that the ratio measures the encoder's
# fixed overhead rather than the effects, so the size check is not meaningful.
DEGENERATE_BITRATE_KBPS = 250

IDENTITY_CURVE = "0/0 0.25/0.25 0.5/0.5 0.75/0.75 1/1"

# ── Uniqueness floors ───────────────────────────────────────────────────
# Everything above asks "does the output still look right". These ask the
# opposite question: "would a content matcher still recognise it".
#
# Reference points, measured here against a plain re-encode of the same clip
# with no uniqualization at all: pHash distance 0, audio bit error rate 0.07.
# That is what the pipeline has to beat, and it is why these numbers only mean
# something next to that control.
#
# Published matchers call two clips the same when the pHash distance is under
# roughly 10 of 63 bits, and the same recording when the audio bit error rate
# is under roughly 0.35. Those are the targets, not these floors: the floors
# sit below the observed spread so that tripping one means a stage regressed,
# not that a particular clip came out on the easy side of the distribution.
PHASH_DISTANCE_MIN = 6
PHASH_PAIR_DISTANCE_MIN = 4   # between two outputs drawn from the same source
AUDIO_BER_MIN = 0.12
# What the literature treats as a match; reported alongside, not gated on.
PHASH_MATCH_THRESHOLD = 10
AUDIO_BER_MATCH_THRESHOLD = 0.35


def run(cmd: List[str], timeout: int = 300) -> Tuple[int, str]:
    proc = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=timeout,
    )
    return proc.returncode, proc.stdout


def make_synthetic_source(path: str, seconds: int = 6) -> bool:
    """Fallback input: moving detail plus a voice-band tone sweep."""
    code, out = run([
        get_ffmpeg_path(), "-y", "-nostdin",
        "-f", "lavfi", "-i", f"testsrc2=size=1080x1920:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000",
        "-t", str(seconds),
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest",
        "-loglevel", "error", path,
    ])
    if code != 0:
        print(f"  could not build synthetic source: {out[:300]}")
    return code == 0


BLACK_THRESHOLD = 40


def count_uncovered_edge_pixels(src_w: int, src_h: int, draws: int = 12) -> Tuple[int, str]:
    """Push a flat 50% grey frame through the whole video chain, count black.

    Grey input has no dark content of its own, and the chain's legitimate
    darkening is bounded: vignette takes 128 down to about 125, noise to about
    116. Anything near zero is therefore a defect — a rotation wedge the safety
    crop missed, lens distortion sampling past the frame edge, a warp coordinate
    off the end of a row, or a filter whose parameters drove its output
    negative. Nothing here is neutralised, so the check covers all of them.
    """
    worst_count, worst_desc = 0, ""
    for _ in range(draws):
        p = UniqueParams.generate_random(RandomRanges())
        plan = plan_geometry(src_w, src_h, p)
        if plan.needs_background:
            # The blurred backdrop legitimately fills the frame; this check
            # targets the foreground chain only.
            continue

        chain = build_video_chain(p, plan, has_overlay=False, source_fps=30.0)
        body = chain.replace("[0:v]", "").split("[base]")[0]

        proc = subprocess.run(
            [get_ffmpeg_path(), "-v", "error",
             "-f", "lavfi", "-i", f"color=c=0x808080:size={src_w}x{src_h}:rate=1",
             "-frames:v", "1", "-vf", body,
             "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            capture_output=True,
        )
        buf = proc.stdout
        need = plan.fg_w * plan.fg_h
        if len(buf) < need:
            continue
        black = sum(1 for v in buf[:need] if v < BLACK_THRESHOLD)
        if black > worst_count:
            worst_count = black
            worst_desc = (f"rotate={p.rotate:+.3f} k1={p.k1:+.4f} "
                          f"vignette={p.vignette_angle} warp={plan.warp}")
    return worst_count, worst_desc


def neutralize_effects(p: UniqueParams) -> UniqueParams:
    """A twin of ``p`` with the degradation filters off and everything else kept.

    Geometry, trim, speed, GOP and encoder tuning are identical, so the two
    outputs are frame-aligned and a pixel metric between them measures exactly
    one thing: how much the colour/noise/sharpen/vignette stage costs.
    """
    clean = copy.deepcopy(p)
    clean.rs = clean.gs = clean.bs = 0.0
    clean.contrast = 1.0
    clean.saturation = 1.0
    clean.brightness = 0.0
    clean.hue_shift = 0.0
    clean.gamma = 1.0
    clean.unsharp_amount = 0.0
    clean.noise_strength = 0
    clean.vignette_angle = 0.0
    clean.do_chroma_roundtrip = False
    clean.do_audio_resample = False
    clean.audio_highpass = 0.0
    clean.audio_lowpass = 0.0
    clean.audio_notches = ()
    # An identity curve rather than no curve: the twin then runs the same filter
    # with the same interpolation, so the measurement isolates the shape of the
    # curve instead of also picking up the RGB round trip it forces.
    clean.tone_curve = (IDENTITY_CURVE,) * 3
    clean.audio_bass_db = 0.0
    clean.audio_treble_db = 0.0
    clean.audio_noise_dbfs = 0.0
    # Geometry and timing are deliberately left alone. The micro-warp drift in
    # particular has to stay identical, or the twins stop being frame-aligned
    # and every pixel metric below turns into noise.
    return clean


def measure_ssim_psnr(test: str, ref: str) -> Tuple[Optional[float], Optional[float]]:
    """Compare two videos frame by frame, normalising size and timebase."""
    info = probe_file(ref)
    if not info.has_geometry:
        return None, None

    # No frame-rate conversion here: the two inputs are frame-aligned twins,
    # and resampling would introduce misalignment rather than remove it.
    norm = (f"scale={info.width}:{info.height}:flags=bicubic,"
            f"setsar=1,settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p")
    code, out = run([
        get_ffmpeg_path(), "-nostdin",
        "-i", test, "-i", ref,
        "-lavfi",
        f"[0:v]{norm}[a];[1:v]{norm}[b];[a][b]ssim=stats_file=-;"
        f"[0:v]{norm}[c];[1:v]{norm}[d];[c][d]psnr=stats_file=-",
        "-f", "null", "-",
    ])
    if code != 0:
        # Fall back to running the two metrics separately.
        ssim = _single_metric(test, ref, "ssim", norm, r"All:([0-9.]+)")
        psnr = _single_metric(test, ref, "psnr", norm, r"average:([0-9.]+)")
        return ssim, psnr

    ssim = _search_float(out, r"SSIM .*All:([0-9.]+)")
    psnr = _search_float(out, r"PSNR .*average:([0-9.]+)")
    return ssim, psnr


def _single_metric(test: str, ref: str, filt: str, norm: str, pattern: str) -> Optional[float]:
    code, out = run([
        get_ffmpeg_path(), "-nostdin", "-i", test, "-i", ref,
        "-lavfi", f"[0:v]{norm}[a];[1:v]{norm}[b];[a][b]{filt}",
        "-f", "null", "-",
    ])
    if code != 0:
        return None
    return _search_float(out, pattern)


def _search_float(text: str, pattern: str) -> Optional[float]:
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def measure_loudness(path: str) -> Tuple[Optional[float], Optional[float]]:
    """Integrated loudness (LUFS) and true peak (dBTP) via EBU R128."""
    code, out = run([
        get_ffmpeg_path(), "-nostdin", "-i", path,
        "-af", "ebur128=peak=true", "-f", "null", "-",
    ])
    if code != 0:
        return None, None
    tail = out[-2000:]
    lufs = _search_float(tail, r"I:\s+(-?[0-9.]+)\s+LUFS")
    peak = _search_float(tail, r"Peak:\s+(-?[0-9.]+)\s+dBFS")
    return lufs, peak


def measure_stream_timing(path: str) -> dict:
    """Per-stream start time and duration — catches A/V desync and drift."""
    from engine import get_ffprobe_path
    import json as _json
    code, out = run([
        get_ffprobe_path(), "-v", "error",
        "-show_entries", "stream=codec_type,start_time,duration",
        "-of", "json", path,
    ])
    result = {}
    if code != 0:
        return result
    try:
        data = _json.loads(out)
    except ValueError:
        return result
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind not in ("video", "audio"):
            continue
        try:
            result[kind] = (
                float(stream.get("start_time") or 0.0),
                float(stream.get("duration") or 0.0),
            )
        except (TypeError, ValueError):
            continue
    return result


def scan_fingerprints(path: str) -> List[str]:
    """Encoder giveaways that identify the file as ffmpeg/x264 output.

    Metadata is read through ffprobe rather than by scanning raw bytes: short
    needles like ``Lavf`` turn up inside compressed video data by chance, which
    made a byte scan report failures on perfectly clean files. Only the x264
    banner is matched literally, and it is long enough not to collide.
    """
    from engine import get_ffprobe_path
    import json as _json

    hits = []
    proc = subprocess.run(
        [get_ffprobe_path(), "-v", "error",
         "-show_entries", "format_tags:stream_tags", "-of", "json", path],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        try:
            data = _json.loads(proc.stdout)
        except ValueError:
            data = {}
        blocks = [data.get("format", {}).get("tags", {})]
        blocks += [s.get("tags", {}) for s in data.get("streams", [])]
        for tags in blocks:
            for key, value in (tags or {}).items():
                text = f"{value}"
                for needle in ("Lavf", "Lavc", "libavformat", "libavcodec", "x264"):
                    if needle in text:
                        hits.append(f"{key}={text}")
                        break

    try:
        with open(path, "rb") as fh:
            if b"x264 - core" in fh.read():
                hits.append("x264 SEI banner in bitstream")
    except OSError:
        hits.append("<unreadable>")

    return hits


def _seek(at: Optional[float], extra: float = 0.0) -> List[str]:
    """``-ss`` arguments, or none at all.

    A still is a single frame: any seek at all lands past the end of it and
    ffmpeg writes nothing, so the image path passes ``at=None``.
    """
    return [] if at is None else ["-ss", str(at + extra)]


def side_by_side(src: str, out: str, dst: str, at: Optional[float] = 1.0) -> bool:
    """One PNG: source frame on the left, processed frame on the right.

    The bundled ffmpeg is built without freetype, so the panels are labelled by
    a colour bar rather than drawtext — green marks the source, red the output.
    """
    code, _ = run([
        get_ffmpeg_path(), "-y", "-nostdin",
    ] + _seek(at, 0.4) + ["-i", src] + _seek(at) + ["-i", out, "-filter_complex",
        # hstack needs matching heights, and the two frames often have
        # different aspects, so normalise on height rather than width.
        "[0:v]scale=-2:720,pad=iw:ih+24:0:24:color=green[a];"
        "[1:v]scale=-2:720,pad=iw:ih+24:0:24:color=red[b];"
        "[a][b]hstack=inputs=2",
        "-frames:v", "1", "-loglevel", "error", dst,
    ])
    return code == 0


def crop_matched_pair(src: str, out: str, dst: str, at: Optional[float] = 1.0) -> bool:
    """Zoomed 1:1 detail crop from both frames, for judging noise and sharpness."""
    code, _ = run([
        get_ffmpeg_path(), "-y", "-nostdin",
    ] + _seek(at, 0.4) + ["-i", src] + _seek(at) + ["-i", out, "-filter_complex",
        "[0:v]crop=480:480:(iw-480)/2:(ih-480)/2,pad=iw:ih+24:0:24:color=green[a];"
        "[1:v]crop=480:480:(iw-480)/2:(ih-480)/2,pad=iw:ih+24:0:24:color=red[b];"
        "[a][b]hstack=inputs=2",
        "-frames:v", "1", "-loglevel", "error", dst,
    ])
    return code == 0


class Report:
    def __init__(self):
        self.rows: List[Tuple[str, str, bool]] = []
        self.notes: List[str] = []

    def check(self, name: str, value: str, ok: bool):
        self.rows.append((name, value, ok))

    def note(self, text: str):
        self.notes.append(text)

    @property
    def failed(self) -> List[str]:
        return [n for n, _, ok in self.rows if not ok]

    def render(self) -> str:
        width = max((len(n) for n, _, _ in self.rows), default=10)
        lines = []
        for name, value, ok in self.rows:
            mark = "PASS" if ok else "FAIL"
            lines.append(f"  [{mark}] {name.ljust(width)}  {value}")
        for note in self.notes:
            lines.append(f"         {note}")
        return "\n".join(lines)


def verify_one(src: str, workdir: str) -> Report:
    rep = Report()
    info = probe_file(src)
    if not info.has_geometry:
        rep.check("probe", f"cannot read {os.path.basename(src)}", False)
        return rep

    rep.note(f"source: {info.width}x{info.height} @ {info.fps:.3f} fps, "
             f"{info.duration:.2f}s, audio={'yes' if info.has_audio else 'no'}")

    # ── Test A: degradation only, measured against a frame-aligned twin ──
    full_params = UniqueParams.generate_random(RandomRanges())
    full_params.crf = 24
    full_params.preset = "veryfast"

    full_dir = os.path.join(workdir, "full")
    clean_dir = os.path.join(workdir, "clean_twin")
    os.makedirs(full_dir, exist_ok=True)
    os.makedirs(clean_dir, exist_ok=True)

    ok, err = process_single_video(src, full_dir, index=1, params=full_params)
    if not ok:
        rep.check("pipeline (full)", err[:160], False)
        return rep
    full_out = os.path.join(full_dir, os.listdir(full_dir)[0])

    clean_params = neutralize_effects(full_params)
    clean_params.fake_meta = False
    ok, err = process_single_video(src, clean_dir, index=1, params=clean_params)
    if not ok:
        rep.check("pipeline (clean twin)", err[:160], False)
        return rep
    clean_out = os.path.join(clean_dir, os.listdir(clean_dir)[0])

    ssim, psnr = measure_ssim_psnr(full_out, clean_out)
    if ssim is None:
        rep.check("SSIM vs clean twin", "could not measure", False)
    else:
        rep.check("SSIM vs clean twin", f"{ssim:.4f}  (min {SSIM_DEGRADATION_MIN})",
                  ssim >= SSIM_DEGRADATION_MIN)
    if psnr is None:
        rep.note("PSNR unavailable")
    else:
        rep.check("PSNR vs clean twin", f"{psnr:.2f} dB  (min {PSNR_DEGRADATION_MIN})",
                  psnr >= PSNR_DEGRADATION_MIN)

    finfo = probe_file(full_out)
    rep.check("output geometry", f"{finfo.width}x{finfo.height} @ {finfo.fps:.3f} fps",
              finfo.width == full_params.target_width and finfo.height == full_params.target_height)

    # Grain is charged per frame by the encoder, so an over-strong noise setting
    # shows up here long before it shows up as a visible artefact. Measured
    # against the clean twin, not the source: a source may be an artificially
    # compressible test pattern, which makes a source ratio meaningless.
    try:
        clean_bytes = os.path.getsize(clean_out)
        clean_kbps = clean_bytes * 8 / 1000 / (probe_file(clean_out).duration or 1.0)
        if clean_kbps < DEGENERATE_BITRATE_KBPS:
            rep.note(f"size check skipped: clean twin is only {clean_kbps:.0f} kbps, "
                     f"too compressible for the ratio to mean anything")
        else:
            ratio = os.path.getsize(full_out) / clean_bytes
            rep.check("size vs clean twin", f"{ratio:.2f}x  (max {SIZE_RATIO_MAX})",
                      ratio <= SIZE_RATIO_MAX)
    except (OSError, ZeroDivisionError):
        pass

    # ── Audio integrity ─────────────────────────────────────────────────
    if info.has_audio:
        src_lufs, _ = measure_loudness(src)
        out_lufs, out_peak = measure_loudness(full_out)
        if src_lufs is not None and out_lufs is not None:
            delta = abs(out_lufs - src_lufs)
            rep.check("loudness shift",
                      f"{delta:.2f} LU  ({src_lufs:.1f} -> {out_lufs:.1f} LUFS)",
                      delta <= LUFS_DELTA_MAX)
        if out_peak is not None:
            rep.check("true peak", f"{out_peak:.2f} dBFS  (max {TRUE_PEAK_MAX})",
                      out_peak <= TRUE_PEAK_MAX)

        timing = measure_stream_timing(full_out)
        if "video" in timing and "audio" in timing:
            v_start, v_dur = timing["video"]
            a_start, a_dur = timing["audio"]
            offset_ms = abs(a_start - v_start) * 1000.0
            rep.check("A/V start offset", f"{offset_ms:.1f} ms  (max {AV_SYNC_MAX_MS})",
                      offset_ms <= AV_SYNC_MAX_MS)
            drift = abs(a_dur - v_dur)
            rep.check("A/V duration drift", f"{drift:.3f} s  (max {DURATION_DRIFT_MAX})",
                      drift <= DURATION_DRIFT_MAX)

    # ── Uncovered edges ─────────────────────────────────────────────────
    black, desc = count_uncovered_edge_pixels(info.width, info.height)
    rep.check("uncovered edge pixels",
              f"{black}" + (f"  ({desc})" if black else "  across 12 random draws"),
              black == 0)

    # ── Metadata scrub ──────────────────────────────────────────────────
    hits = scan_fingerprints(full_out)
    rep.check("encoder fingerprints", ", ".join(hits) if hits else "none found", not hits)

    # ── Uniqueness: would a content matcher still recognise the output? ──
    # Every check above this point wants the output to resemble the source.
    # These want the opposite, which is why they are worth running together:
    # a change that helps one usually costs the other.
    ffmpeg = get_ffmpeg_path()
    src_hashes = fingerprint.video_hashes(ffmpeg, src)
    phash_distance = fingerprint.video_distance(
        src_hashes, fingerprint.video_hashes(ffmpeg, full_out))
    if phash_distance is None:
        rep.check("pHash vs source", "could not measure", False)
    else:
        verdict = ("evades" if phash_distance >= PHASH_MATCH_THRESHOLD
                   else "STILL MATCHES")
        rep.check(
            "pHash vs source",
            f"{phash_distance}/{fingerprint.PHASH_BITS} bits — {verdict} a "
            f"{PHASH_MATCH_THRESHOLD}-bit matcher  (floor {PHASH_DISTANCE_MIN})",
            phash_distance >= PHASH_DISTANCE_MIN)

    if info.has_audio:
        src_print = fingerprint.audio_fingerprint(ffmpeg, src)
        ber = fingerprint.audio_bit_error_rate(
            src_print, fingerprint.audio_fingerprint(ffmpeg, full_out))
        if ber is None:
            rep.note("audio fingerprint skipped: clip too short to frame")
        else:
            verdict = ("evades" if ber >= AUDIO_BER_MATCH_THRESHOLD
                       else "STILL MATCHES")
            rep.check(
                "audio fp vs source",
                f"BER {ber:.3f} — {verdict} a {AUDIO_BER_MATCH_THRESHOLD} "
                f"matcher  (floor {AUDIO_BER_MIN})",
                ber >= AUDIO_BER_MIN)

    # ── Per-file variation (regression guard) ───────────────────────────
    var_dir = os.path.join(workdir, "variation")
    os.makedirs(var_dir, exist_ok=True)
    digests = []
    var_files: List[str] = []
    for i in range(3):
        vp = UniqueParams.generate_random(RandomRanges())
        vp.crf, vp.preset, vp.fake_meta = 28, "ultrafast", False
        ok, _ = process_single_video(src, var_dir, index=100 + i, params=vp)
        if ok:
            digests.append((vp.noise_seed, vp.deblock_alpha, vp.aq_strength,
                            vp.audio_highpass, vp.gamma))
            candidate = os.path.join(var_dir, f"uniq_{100 + i}.mp4")
            if os.path.exists(candidate):
                var_files.append(candidate)
    unique_draws = len(set(digests))
    rep.check("per-file randomisation", f"{unique_draws}/{len(digests)} distinct draws",
              len(digests) >= 2 and unique_draws == len(digests))

    # Distinct parameter draws are not the same thing as distinct pictures.
    # Two outputs of one source landing on the same hash would let a batch be
    # de-duplicated against itself, whatever the parameters say.
    if len(var_files) >= 2:
        pair = fingerprint.video_distance(
            fingerprint.video_hashes(ffmpeg, var_files[0]),
            fingerprint.video_hashes(ffmpeg, var_files[1]))
        if pair is not None:
            rep.check("pHash between outputs",
                      f"{pair}/{fingerprint.PHASH_BITS} bits  "
                      f"(floor {PHASH_PAIR_DISTANCE_MIN})",
                      pair >= PHASH_PAIR_DISTANCE_MIN)

    # ── Visual samples ──────────────────────────────────────────────────
    stem = os.path.splitext(os.path.basename(src))[0]
    full_png = os.path.join(workdir, f"compare_{stem}.png")
    if side_by_side(src, full_out, full_png):
        rep.note(f"full-frame comparison: {full_png}")
    detail_png = os.path.join(workdir, f"detail_{stem}.png")
    if crop_matched_pair(src, full_out, detail_png):
        rep.note(f"1:1 detail crop:        {detail_png}")

    return rep


# ─── Still images ───────────────────────────────────────────────────────
# The same two questions as the video path, minus everything that needs a
# timeline. Measured floors, from 8 draws across four photographs:
# pHash distance from the source ran 10-26 with a median of 18, and a plain
# re-encode of the same photo scored 0. The floors sit below the observed
# minimum, so tripping one means something is broken rather than unlucky.

IMAGE_PHASH_DISTANCE_MIN = 8
# Below the lowest pair distance observed (4), not equal to it: a floor sitting
# exactly on the observed minimum makes the gate fail on an unlucky draw rather
# than on a defect, which is the opposite of what a gate is for.
IMAGE_PHASH_PAIR_DISTANCE_MIN = 2
IMAGE_SIZE_RATIO_MAX = 1.8
# Aspect has to survive: Meta drives placement off the creative's own ratio, so
# a stretched output is a broken output. The allowance is dimension-aware
# because rounding the canvas to even numbers costs up to a pixel per axis, and
# one pixel is 0.5% of a 200 px side while being nothing at all on a 3000 px
# one. A flat 1% would have failed small sources for doing the right thing.
IMAGE_ASPECT_DRIFT_BASE = 0.005


def _aspect_drift_allowance(src_w: int, src_h: int) -> float:
    return IMAGE_ASPECT_DRIFT_BASE + 1.0 / max(1, src_w) + 1.0 / max(1, src_h)


def neutralize_image_effects(p: ImageParams) -> ImageParams:
    """A twin of ``p`` with the degradation filters off and geometry kept.

    Same contract as ``neutralize_effects``: the two outputs land on identical
    pixels-in-frame, so SSIM between them measures the colour/noise/sharpen/
    vignette stage and nothing else.
    """
    clean = copy.deepcopy(p)
    clean.rs = clean.gs = clean.bs = 0.0
    clean.contrast = 1.0
    clean.saturation = 1.0
    clean.brightness = 0.0
    clean.hue_shift = 0.0
    clean.gamma = 1.0
    clean.unsharp_amount = 0.0
    clean.noise_strength = 0
    clean.vignette_angle = 0.0
    clean.do_chroma_roundtrip = False
    clean.tone_curve = (IDENTITY_CURVE,) * 3
    clean.fake_meta = False
    # Encode settings are pinned rather than redrawn: the size ratio below is
    # only meaningful if both files went through the same codec settings. The
    # PNG pair matters as much as the JPEG one — compression level and filter
    # predictor between them swung the ratio by more than 2x on a synthetic
    # image, which read as an effects-stage problem that was not there.
    clean.jpeg_quality = p.jpeg_quality
    clean.jpeg_huffman = p.jpeg_huffman
    clean.png_compression = p.png_compression
    clean.png_pred = p.png_pred
    return clean


def _run_one_image(src: str, params: ImageParams, outdir: str) -> Optional[str]:
    """Process ``src`` into a private directory and return the output path."""
    os.makedirs(outdir, exist_ok=True)
    for stale in os.listdir(outdir):
        try:
            os.unlink(os.path.join(outdir, stale))
        except OSError:
            pass
    ok, err = process_single_image(src, outdir, 1, params=params)
    if not ok:
        return None
    entries = [os.path.join(outdir, f) for f in os.listdir(outdir)]
    return entries[0] if entries else None


def _plain_reencode_image(src: str, dst: str, quality: int) -> bool:
    """The control: same encoder, same quality, no uniqualization at all."""
    code, _ = run([
        get_ffmpeg_path(), "-y", "-nostdin", "-i", src,
        "-c:v", "mjpeg", "-q:v", str(quality), "-pix_fmt", "yuvj420p",
        "-frames:v", "1", "-map_metadata", "-1",
        "-fflags", "+bitexact", "-flags:v", "+bitexact",
        "-loglevel", "error", dst,
    ])
    return code == 0


# Rounding the canvas to even numbers, and snapping the crop window back onto
# that rounded aspect, can each cost a pixel per axis. Two is the whole budget:
# anything larger is a crop, which is what this check exists to catch.
FULL_FRAME_PIXEL_SLACK = 2


def check_full_frame(rep: Report, src_w: int, src_h: int, draws: int = 40):
    """The default still mode must keep the whole frame.

    Pure geometry, no encoding: it resolves the crop the way the engine does and
    asserts the window is the entire source. Cheap enough to run many draws,
    which matters because the failure mode is a rare unlucky sample — one draw
    that happens to leave rotation or a warp offset live.
    """
    worst = (0, 0, 0, 0)
    worst_loss = -1
    for _ in range(draws):
        p = ImageParams.generate_random(ImageRanges())
        p.target_width, p.target_height = plan_image_canvas(src_w, src_h, p)
        plan = plan_geometry(src_w, src_h, p)
        loss = max(src_w - plan.crop_w, src_h - plan.crop_h,
                   plan.crop_x, plan.crop_y)
        if loss > worst_loss:
            worst_loss = loss
            worst = (plan.crop_w, plan.crop_h, plan.crop_x, plan.crop_y)

    w, h, x, y = worst
    rep.check("full frame kept (default mode)",
              f"{src_w}x{src_h} -> crop {w}x{h}+{x}+{y}, worst loss "
              f"{worst_loss}px over {draws} draws "
              f"(max {FULL_FRAME_PIXEL_SLACK})",
              worst_loss <= FULL_FRAME_PIXEL_SLACK)


def verify_image(src: str, workdir: str) -> Report:
    rep = Report()
    ffmpeg = get_ffmpeg_path()

    info = probe_file(src)
    if not info.has_geometry:
        rep.check("probe", f"cannot read {os.path.basename(src)}", False)
        return rep

    # The uniqueness floors below were measured with reframing on, and
    # reframing is the only stage that moves a still's hash — so they only mean
    # anything against a draw that reframes. The full-frame mode, which is the
    # default in the UI, is checked separately by check_full_frame.
    params = ImageParams.generate_random(ImageRanges(preserve_full_frame=False))
    params.fake_meta = True
    out = _run_one_image(src, params, os.path.join(workdir, "out"))
    if not out:
        rep.check("process", "process_single_image failed", False)
        return rep

    clean = _run_one_image(src, neutralize_image_effects(params),
                           os.path.join(workdir, "clean"))
    if not clean:
        rep.check("clean twin", "could not build the comparison twin", False)
        return rep

    # ── Does it still look right ────────────────────────────────────────
    ssim, psnr = measure_ssim_psnr(out, clean)
    if ssim is None:
        rep.check("SSIM vs twin", "not measured", False)
    else:
        rep.check("SSIM vs twin", f"{ssim:.4f} (min {SSIM_DEGRADATION_MIN})",
                  ssim >= SSIM_DEGRADATION_MIN)
    if psnr is None:
        rep.check("PSNR vs twin", "not measured", False)
    else:
        rep.check("PSNR vs twin", f"{psnr:.1f} dB (min {PSNR_DEGRADATION_MIN})",
                  psnr >= PSNR_DEGRADATION_MIN)

    ratio = os.path.getsize(out) / max(1, os.path.getsize(clean))
    rep.check("size vs twin", f"{ratio:.2f}x (max {IMAGE_SIZE_RATIO_MAX})",
              ratio <= IMAGE_SIZE_RATIO_MAX)

    out_info = probe_file(out)
    src_ar = info.width / info.height
    out_ar = (out_info.width / out_info.height) if out_info.has_geometry else 0.0
    drift = abs(out_ar - src_ar) / src_ar if src_ar else 1.0
    allowance = _aspect_drift_allowance(info.width, info.height)
    rep.check("aspect preserved",
              f"{info.width}x{info.height} -> {out_info.width}x{out_info.height}"
              f" ({drift * 100:.2f}% drift, max {allowance * 100:.2f}%)",
              drift <= allowance)

    # ── Would a matcher still recognise it ──────────────────────────────
    src_hash = fingerprint.image_hash(ffmpeg, src)
    out_hash = fingerprint.image_hash(ffmpeg, out)
    if src_hash is None or out_hash is None:
        rep.check("pHash distance", "not measured", False)
    else:
        distance = fingerprint.hamming(src_hash, out_hash)
        rep.check("pHash distance",
                  f"{distance} bits of {fingerprint.PHASH_BITS} "
                  f"(min {IMAGE_PHASH_DISTANCE_MIN}, matcher threshold "
                  f"{PHASH_MATCH_THRESHOLD})",
                  distance >= IMAGE_PHASH_DISTANCE_MIN)

        control = os.path.join(workdir, "control.jpg")
        if _plain_reencode_image(src, control, params.jpeg_quality):
            control_hash = fingerprint.image_hash(ffmpeg, control)
            if control_hash is not None:
                rep.note(f"control: a plain re-encode scores "
                         f"{fingerprint.hamming(src_hash, control_hash)}")

    second = _run_one_image(src, ImageParams.generate_random(
                                ImageRanges(preserve_full_frame=False)),
                            os.path.join(workdir, "second"))
    if second:
        a, b = fingerprint.image_hash(ffmpeg, out), fingerprint.image_hash(ffmpeg, second)
        if a is not None and b is not None:
            pair = fingerprint.hamming(a, b)
            rep.check("pHash between outputs",
                      f"{pair} bits (min {IMAGE_PHASH_PAIR_DISTANCE_MIN})",
                      pair >= IMAGE_PHASH_PAIR_DISTANCE_MIN)

    # ── Anti-fingerprinting ─────────────────────────────────────────────
    if info.has_alpha:
        out_alpha = probe_file(out).has_alpha
        rep.check("alpha preserved",
                  "kept" if out_alpha else "flattened",
                  out_alpha or not out.lower().endswith(".png"))

    hits = scan_fingerprints(out)
    rep.check("no encoder fingerprints",
              "clean" if not hits else "; ".join(hits[:3]), not hits)

    # PNG carries the identity as text chunks rather than EXIF: there is no
    # EXIF convention for PNG that viewers agree on.
    if out.lower().endswith(".png"):
        tags = exif.read_png_text(out)
        make, model = tags.get("Make", ""), tags.get("Model", "")
        captured = tags.get("Creation Time", "")
    else:
        tags = exif.read_exif_strings(out) or {}
        make, model = tags.get("0x010F", ""), tags.get("0x0110", "")
        captured = tags.get("0x9003", "")

    known = {(d["make"], d["model"]) for d in DEVICE_PROFILES}
    rep.check("camera metadata",
              f"{make} {model}" if make else "no metadata written",
              (make, model) in known)
    rep.check("capture date written", captured or "missing", bool(captured))

    check_full_frame(rep, info.width, info.height)

    if workdir:
        side_by_side(src, out, os.path.join(workdir, "compare_full.png"), at=None)
        crop_matched_pair(src, out, os.path.join(workdir, "compare_crop.png"), at=None)
        rep.note(f"comparison images in {workdir}")

    return rep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*",
                        help="video or image files to check")
    parser.add_argument("--keep", metavar="DIR",
                        help="write intermediates and the comparison image here")
    args = parser.parse_args()

    ok, err = check_ffmpeg_available()
    if not ok:
        print(f"ffmpeg unavailable: {err}", file=sys.stderr)
        return 1

    workroot = args.keep or tempfile.mkdtemp(prefix="uniq-verify-")
    os.makedirs(workroot, exist_ok=True)

    sources = list(args.sources)
    if not sources:
        synthetic = os.path.join(workroot, "synthetic_source.mp4")
        print("No source given — generating a synthetic 1080x1920 clip.")
        if not make_synthetic_source(synthetic):
            return 1
        sources = [synthetic]

    all_failures = []
    for src in sources:
        print(f"\n=== {os.path.basename(src)} ===")
        workdir = os.path.join(workroot, os.path.splitext(os.path.basename(src))[0])
        os.makedirs(workdir, exist_ok=True)
        is_still = os.path.splitext(src)[1].lower() in IMAGE_EXTENSIONS
        rep = verify_image(src, workdir) if is_still else verify_one(src, workdir)
        print(rep.render())
        all_failures.extend(f"{os.path.basename(src)}: {f}" for f in rep.failed)

    print()
    if all_failures:
        print(f"FAILED ({len(all_failures)}):")
        for f in all_failures:
            print(f"  - {f}")
    else:
        print("All checks passed.")

    if not args.keep:
        print(f"\nIntermediates kept at {workroot} (pass --keep DIR to choose the location).")

    return 1 if all_failures else 0


if __name__ == "__main__":
    sys.exit(main())
