"""Engine behaviour against real ffmpeg and against scripted stand-ins.

The stand-ins reproduce failures that real media cannot produce on demand:
an ffmpeg that dyld kills on launch, one that hangs without output, and one
that runs out of disk.
"""

import os
import subprocess
import threading
import time

import pytest

import engine
import image_engine

pytestmark = pytest.mark.ffmpeg


def _dims(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return tuple(int(x) for x in out.split(","))


def _no_orphans(marker):
    return subprocess.run(["pgrep", "-f", marker], capture_output=True).returncode != 0


# ─── ffmpeg availability ──────────────────────────────────

def test_check_ffmpeg_available_real():
    ok, err = engine.check_ffmpeg_available()
    assert ok, err


def test_check_ffmpeg_reports_a_binary_that_dies_on_launch(fake_ffmpeg, monkeypatch):
    """A missing dylib kills the process without a Python exception."""
    broken = fake_ffmpeg("dyld_broken", 'echo "dyld[1]: Library not loaded: /opt/homebrew/x.dylib" >&2\nkill -ABRT $$')
    monkeypatch.setattr(engine, "get_ffmpeg_path", lambda: broken)
    ok, err = engine.check_ffmpeg_available()
    assert not ok
    assert "Library not loaded" in err


# ─── Watchdog ────────────────────────────────────────────

def test_cancel_stops_a_silent_ffmpeg(media, fake_ffmpeg, monkeypatch, tmp_path):
    hang = fake_ffmpeg("hang_cancel", "exec sleep 611")
    monkeypatch.setattr(engine, "get_ffmpeg_path", lambda: hang)
    flag = threading.Event()
    threading.Timer(0.5, flag.set).start()
    out = tmp_path / "out"
    t0 = time.monotonic()
    ok, errors = engine.process_batch([media["land"]] * 3, str(out),
                                      max_workers=1, cancelled=flag.is_set)
    assert time.monotonic() - t0 < 5
    assert ok == 0 and errors == []
    assert os.listdir(out) == []                # partial output removed
    assert _no_orphans("sleep 611")


def test_stall_timeout_kills_a_hung_ffmpeg(media, fake_ffmpeg, monkeypatch, tmp_path):
    hang = fake_ffmpeg("hang_stall", "exec sleep 612")
    monkeypatch.setattr(engine, "get_ffmpeg_path", lambda: hang)
    monkeypatch.setattr(engine, "_FFMPEG_STALL_TIMEOUT", 1)
    ok, errors = engine.process_batch([media["land"]], str(tmp_path), max_workers=1)
    assert ok == 0
    assert len(errors) == 1 and "stopped making progress" in errors[0]
    assert _no_orphans("sleep 612")


def test_disk_full_stops_the_video_batch(media, fake_ffmpeg, monkeypatch, tmp_path):
    full = fake_ffmpeg("full", 'echo "av_interleaved_write_frame(): No space left on device" >&2\nexit 1')
    monkeypatch.setattr(engine, "get_ffmpeg_path", lambda: full)
    ok, errors = engine.process_batch([media["land"]] * 4, str(tmp_path), max_workers=1)
    assert ok == 0
    assert errors[0].endswith(f"{engine.DISK_FULL_ERROR} ({tmp_path})")
    assert errors[-1] == f"3 file(s) not started: {engine.DISK_FULL_ERROR}"


def test_disk_full_stops_the_image_batch(media, fake_ffmpeg, monkeypatch, tmp_path):
    full = fake_ffmpeg("full_img", 'echo "No space left on device" >&2\nexit 1')
    monkeypatch.setattr(image_engine, "get_ffmpeg_path", lambda: full)
    ok, errors = image_engine.process_image_batch([media["photo"]] * 3, str(tmp_path), max_workers=1)
    assert ok == 0
    assert errors[-1] == f"2 file(s) not started: {engine.DISK_FULL_ERROR}"


# ─── Real encodes ────────────────────────────────────────

def test_match_source_keeps_the_source_frame(media, tmp_path):
    template = engine.UniqueParams()
    template.target_width = template.target_height = 0
    template.preset = "ultrafast"
    ok, errors = engine.process_batch([media["land"]], str(tmp_path),
                                      params_template=template, max_workers=1)
    assert ok == 1, errors
    # The name and extension come from the fabricated device (IMG_1234.MOV,
    # VID_….mp4, …), so take the one file rather than guessing a pattern.
    (out,) = os.listdir(tmp_path)
    assert _dims(os.path.join(tmp_path, out)) == (640, 360)


def test_backdrop_preset_writes_square_pixels(media, tmp_path):
    """A 9:16 preset over a landscape source goes through the blurred backdrop.

    Scaling the backdrop to a different aspect rewrites its SAR, overlay takes
    that SAR, and a 1080x1920 file then played back as a 1920x1920 square.
    """
    template = engine.UniqueParams()
    template.target_width, template.target_height = 360, 640
    template.preset = "ultrafast"
    ok, errors = engine.process_batch([media["land"]], str(tmp_path),
                                      params_template=template, max_workers=1)
    assert ok == 1, errors
    (out,) = os.listdir(tmp_path)
    sar = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=sample_aspect_ratio", "-of", "csv=p=0",
         os.path.join(tmp_path, out)],
        capture_output=True, text=True, check=True).stdout.strip()
    assert _dims(os.path.join(tmp_path, out)) == (360, 640)
    assert sar in ("1:1", "N/A"), sar


def test_per_file_callbacks_are_keyed_by_path(media, tmp_path):
    template = engine.UniqueParams()
    template.preset = "ultrafast"
    started, results, overall = [], {}, []
    ok, errors = engine.process_batch(
        [media["land"], media["broken"]], str(tmp_path), params_template=template,
        max_workers=2,
        started_callback=started.append,
        result_callback=lambda path, ok, err: results.__setitem__(path, (ok, err)),
        overall_progress_callback=overall.append)
    assert ok == 1
    assert set(started) == {media["land"], media["broken"]}
    assert results[media["land"]] == (True, "")
    assert results[media["broken"]][0] is False
    assert "could not read video dimensions" in results[media["broken"]][1]
    assert overall and overall[-1] == pytest.approx(1.0)
    assert all(0.0 <= f <= 1.0 for f in overall)


def test_image_callbacks(media, tmp_path):
    started, results = [], {}
    ok, errors = image_engine.process_image_batch(
        [media["photo"]], str(tmp_path), max_workers=1,
        started_callback=started.append,
        result_callback=lambda path, ok, err: results.__setitem__(path, (ok, err)))
    assert ok == 1, errors
    assert started == [media["photo"]]
    assert results == {media["photo"]: (True, "")}


@pytest.mark.parametrize("transfer", ["arib-std-b67", "smpte2084"])
def test_hdr_source_comes_out_as_sdr_bt709(transfer, tmp_path):
    """An iPhone clip is HLG; left alone it came out as 8-bit HLG."""
    if not engine.scale_supports_color_management():
        pytest.skip("this ffmpeg's scale has no colour management")
    src = str(tmp_path / "hdr.mp4")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=640x360:d=2:r=30", "-vf",
         "format=yuv420p10le,setparams=color_primaries=bt2020"
         f":color_trc={transfer}:colorspace=bt2020nc",
         "-c:v", "libx264", "-preset", "ultrafast", src], check=True)
    assert engine.probe_file(src).is_hdr

    template = engine.UniqueParams()
    template.preset = "ultrafast"
    out = tmp_path / "out"
    ok, errors = engine.process_batch([src], str(out), params_template=template,
                                      max_workers=1)
    assert ok == 1, errors
    [result] = os.listdir(out)
    tags = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=color_transfer,color_primaries,color_space", "-of",
         "default=nw=1", str(out / result)],
        capture_output=True, text=True, check=True).stdout.split()
    assert sorted(tags) == ["color_primaries=bt709", "color_space=bt709",
                            "color_transfer=bt709"]
