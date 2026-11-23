"""Pure functions: geometry, sizing, filter graphs, file-list filtering.

No ffmpeg and no encoding, so these run in well under a second and anywhere.
"""

import os
from pathlib import Path
import random

import pytest

import engine
import image_engine
from engine import (
    RandomRanges,
    UniqueParams,
    build_audio_chain,
    build_video_chain,
    match_source_size,
    plan_geometry,
    unique_filepath,
)

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "golden")


# ─── match_source_size ──────────────────────────────────────

@pytest.mark.parametrize("src, expected", [
    ((1280, 720), (1280, 720)),       # under the cap: untouched
    ((3840, 2160), (1920, 1080)),     # landscape 4K capped on the long side
    ((2160, 3840), (1080, 1920)),     # portrait 4K
    ((1080, 1080), (1080, 1080)),
    ((641, 361), (640, 360)),         # odd source: rounded down to even
])
def test_match_source_size(src, expected):
    assert match_source_size(*src) == expected


def test_match_source_size_never_upscales_and_keeps_aspect():
    for w, h in [(320, 240), (4000, 3000), (1234, 4321), (7680, 4320)]:
        out_w, out_h = match_source_size(w, h)
        assert out_w % 2 == 0 and out_h % 2 == 0
        assert max(out_w, out_h) <= max(1920, max(w, h) + 1)
        assert max(out_w, out_h) <= max(w, h) + 1
        assert abs(out_w / out_h - w / h) / (w / h) < 0.01


# ─── plan_geometry ─────────────────────────────────────────

@pytest.mark.parametrize("src", [(1920, 1080), (1080, 1920), (1280, 720), (720, 1280), (1000, 1000)])
@pytest.mark.parametrize("target", [(1080, 1920), (1080, 1080), (1920, 1080)])
def test_plan_geometry_invariants(src, target):
    """Over many random draws the crop is even, in bounds, and non-empty.

    ffmpeg rejects odd crops with yuv420p and fails outright on a window
    outside the frame, so these are the properties every draw must hold.
    """
    rng_state = random.getstate()
    random.seed(20260925)
    try:
        for _ in range(60):
            p = UniqueParams.generate_random(RandomRanges())
            p.target_width, p.target_height = target
            plan = plan_geometry(src[0], src[1], p)
            for v in (plan.crop_w, plan.crop_h, plan.crop_x, plan.crop_y, plan.fg_w, plan.fg_h):
                assert v % 2 == 0, plan
            assert plan.crop_w > 0 and plan.crop_h > 0
            assert plan.crop_x + plan.crop_w <= src[0]
            assert plan.crop_y + plan.crop_h <= src[1]
            assert plan.fg_w <= target[0] and plan.fg_h <= target[1]
    finally:
        random.setstate(rng_state)


# ─── Golden filter graphs ──────────────────────────────────

def _video_graphs():
    random.seed(424242)
    out = []
    for src, target in [((1920, 1080), (1080, 1920)), ((1080, 1920), (1080, 1920)),
                        ((1280, 720), (1280, 720))]:
        p = UniqueParams.generate_random(RandomRanges())
        p.target_width, p.target_height = target
        plan = plan_geometry(src[0], src[1], p)
        out.append(f"# {src} -> {target}")
        out.append(build_video_chain(p, plan, False, 30.0))
        out.append(build_audio_chain(p, True, p.pitch, p.video_speed))
    return "\n".join(out) + "\n"


def test_video_graph_matches_golden():
    """The video filter graph must not change by accident.

    CLAUDE.md: refactoring build_spatial_chain means re-checking the video
    graph is byte-identical, not just that the quality gate passes. This is
    that check. After an *intended* change to the graph, regenerate with
    ``UPDATE_GOLDEN=1 pytest tests/test_pure.py`` and review the diff.
    """
    actual = _video_graphs()
    path = os.path.join(GOLDEN_DIR, "video_graph.txt")
    if os.environ.get("UPDATE_GOLDEN") == "1" or not os.path.exists(path):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        with open(path, "w") as f:
            f.write(actual)
        if os.environ.get("UPDATE_GOLDEN") != "1":
            pytest.skip("golden file created; commit it")
    with open(path) as f:
        expected = f.read()
    assert actual == expected, "video filter graph changed; see test docstring"


def test_video_graph_is_deterministic_for_a_seed():
    assert _video_graphs() == _video_graphs()


# ─── HDR, bitstream filters, frame rate ────────────────────

def test_hdr_step_sits_between_geometry_and_effects():
    """Converted after the downscale (cheap), before the colour stage (which
    was tuned on SDR). Both branches get it when the backdrop is built."""
    random.seed(7)
    p = UniqueParams.generate_random(RandomRanges())
    p.target_width, p.target_height = 1080, 1920
    step = engine.build_hdr_to_sdr_step("arib-std-b67")

    same_aspect = build_video_chain(p, plan_geometry(1080, 1920, p), False, 30.0, step)
    steps = same_aspect.split("[0:v]", 1)[1].split("[base]")[0].split(",")
    at = steps.index(step)
    assert steps[at - 1] == "setsar=1"
    assert steps[at + 1].startswith(("curves=", "colorbalance="))

    backdrop = build_video_chain(p, plan_geometry(1920, 1080, p), False, 30.0, step)
    assert backdrop.count(step) == 2


def test_no_hdr_step_leaves_the_graph_alone():
    random.seed(8)
    p = UniqueParams.generate_random(RandomRanges())
    plan = plan_geometry(1080, 1920, p)
    assert build_video_chain(p, plan, False, 30.0) == build_video_chain(p, plan, False, 30.0, "")
    assert "in_transfer" not in build_video_chain(p, plan, False, 30.0)


def test_sei_strip_bsf_is_per_codec():
    """HEVC type 6 is RADL_N, a picture; its SEI types are 39 and 40."""
    assert engine.sei_strip_bsf(engine.ENCODER_LIBX264).endswith("remove_types=6")
    assert engine.sei_strip_bsf(engine.ENCODER_H264_VIDEOTOOLBOX).endswith("remove_types=6")
    assert engine.sei_strip_bsf(engine.ENCODER_HEVC_VIDEOTOOLBOX).endswith("remove_types=39|40")


@pytest.mark.parametrize("rate, expected", [
    ("30/1", 30.0),
    ("30000/1001", 30000 / 1001),
    ("90000/1", 0.0),      # container timebase reported by a VFR phone clip
    ("0/0", 0.0),
    ("", 0.0),
    (None, 0.0),
    ("garbage", 0.0),
])
def test_parse_rate(rate, expected):
    assert engine._parse_rate(rate) == pytest.approx(expected)


# ─── Output naming ─────────────────────────────────────────

def test_unique_filepath_reserves_and_suffixes(tmp_path):
    a = unique_filepath(str(tmp_path), "clip.mp4")
    b = unique_filepath(str(tmp_path), "clip.mp4")
    assert a != b
    assert os.path.exists(a) and os.path.exists(b)   # reserved on disk


def test_unique_filepath_redraws_instead_of_suffixing(tmp_path):
    names = iter(["IMG_0002.MP4", "IMG_0003.MP4"])
    first = unique_filepath(str(tmp_path), "IMG_0001.MP4")
    second = unique_filepath(str(tmp_path), "IMG_0001.MP4", regenerate=lambda: next(names))
    assert os.path.basename(first) == "IMG_0001.MP4"
    assert os.path.basename(second) == "IMG_0002.MP4"


# ─── Disk-full detection ──────────────────────────────────

def test_is_disk_full():
    assert engine.is_disk_full("av_interleaved_write_frame(): No space left on device")
    assert not engine.is_disk_full("Invalid data found when processing input")


# ─── Image canvas ─────────────────────────────────────────

def test_image_canvas_keeps_aspect_and_cap():
    p = image_engine.ImageParams()
    p.size_jitter = 1.0
    p.max_long_side = 1000
    w, h = image_engine.plan_image_canvas(4000, 3000, p)
    assert (w, h) == (1000, 750)
    p.max_long_side = 0
    assert image_engine.plan_image_canvas(640, 480, p) == (640, 480)


# ─── File-list filtering (main.py helpers; no window needed) ──

def test_is_candidate_filters_hidden_and_appledouble(qapp):
    import main
    assert main._is_candidate("/x/clip.MP4")
    assert main._is_candidate("/x/photo.jpeg")
    assert not main._is_candidate("/x/._clip.mp4")      # AppleDouble on exFAT
    assert not main._is_candidate("/x/.hidden.mp4")
    assert not main._is_candidate("/x/notes.txt")


def test_format_bytes(qapp):
    import main
    assert main._format_bytes(512) == "512 B"
    assert main._format_bytes(1536 * 1024) == "1.5 MB"
    assert main._format_bytes(3 * 1024 ** 3) == "3.0 GB"


@pytest.mark.parametrize("sizes,keep,outcome,published", [
    ([999], False, "reduced", 999),
    ([1000, 1001], False, "skipped_without_saving", None),
    ([1001, 1000], True, "kept_without_saving", 1001),
    ([1001, 999], True, "reduced", 999),
    ([0], True, None, None),
])
def test_candidate_selection(tmp_path, sizes, keep, outcome, published):
    from gentle_compression import select_candidate
    src = tmp_path / "original.jpg"
    src.write_bytes(b"x" * 1000)
    dest = unique_filepath(str(tmp_path), src.name, reserve=False)
    calls = []
    def encode(path, attempt):
        calls.append(attempt)
        Path(path).write_bytes(b"y" * sizes[attempt])
        return ""
    if outcome is None:
        with pytest.raises(ValueError, match="empty"):
            select_candidate(str(src), dest, 2, keep, encode, None)
    else:
        result = select_candidate(str(src), dest, 2, keep, encode, None)
        assert (result.outcome, result.output_bytes) == (outcome, published)
        assert result.attempts == len(calls)
    assert calls == list(range(len(sizes)))
    assert src.read_bytes() == b"x" * 1000
    assert set(tmp_path.iterdir()) == ({src, Path(dest)} if published else {src})


@pytest.mark.parametrize("failure", ["empty", "missing", "second", "cancel", "replace", "enospc"])
def test_gentle_failure_preserves_input_and_cleans_candidates(tmp_path, monkeypatch, failure):
    import errno
    from gentle_compression import finish_compression
    source = tmp_path / "source"
    original = b"" if failure == "empty" else b"original"
    if failure != "missing":
        source.write_bytes(original)
    reserved = tmp_path / "reserved.jpg"
    calls = []

    def encode(path, attempt):
        calls.append(attempt)
        if attempt == 1:
            if failure == "second":
                return "second encode failed"
            if failure == "replace":
                replacement = tmp_path / "replacement"
                replacement.write_bytes(b"another job")
                replacement.replace(reserved)
            Path(path).write_bytes(b"x")
        else:
            Path(path).write_bytes(original)
        return ""

    if failure == "enospc":
        def full(*args):
            raise OSError(errno.ENOSPC, "simulated full output")
        monkeypatch.setattr("gentle_compression._publish_exclusive", full)
    ok, error = finish_compression(str(source), str(reserved), 2, True, encode,
                                   lambda: failure == "cancel" and len(calls) == 2, None)
    if failure == "replace":
        assert ok and not error
        assert (tmp_path / "reserved_1.jpg").read_bytes() == b"x"
    else:
        assert not ok and error
    if failure not in ("empty", "missing"):
        assert calls == [0, 1]
    if failure == "enospc":
        assert error == engine.DISK_FULL_ERROR
    if failure == "replace":
        assert reserved.read_bytes() == b"another job"
    else:
        assert not reserved.exists()
    assert not list(tmp_path.glob(".gentle-*"))
    if source.exists():
        assert source.read_bytes() == original


def test_concurrent_gentle_exports_leave_existing_empty_file_intact(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from gentle_compression import select_candidate
    src = tmp_path / "input"
    src.write_bytes(b"original")
    dest = tmp_path / "output.jpg"
    dest.touch()
    barrier = Barrier(2)

    def export(payload):
        def encode(path, attempt):
            Path(path).write_bytes(payload)
            barrier.wait(timeout=5)
            return ""
        return select_candidate(str(src), str(dest), 1, False, encode, None)

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(export, (b"a", b"b")))
    assert dest.read_bytes() == b""
    assert {Path(r.output_path).read_bytes() for r in results} == {b"a", b"b"}
    assert len(list(tmp_path.iterdir())) == 4
