"""Pure functions: geometry, sizing, filter graphs, file-list filtering.

No ffmpeg and no encoding, so these run in well under a second and anywhere.
"""

import os
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
