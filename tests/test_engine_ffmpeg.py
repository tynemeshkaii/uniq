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


@pytest.mark.parametrize("gentle", [False, True])
@pytest.mark.parametrize("transfer", ["arib-std-b67", "smpte2084"])
def test_hdr_source_comes_out_as_sdr_bt709(transfer, gentle, tmp_path):
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

    template = engine.UniqueParams(gentle_compression=gentle, keep_without_saving=True)
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


def test_gentle_video_retries_same_command(media, tmp_path, fake_ffmpeg, monkeypatch):
    import json
    import sys
    from pathlib import Path
    trace = tmp_path / "trace"
    copy_script = tmp_path / "copy.py"
    copy_script.write_text(
        "import json,sys,shutil\n"
        f"with open({str(trace)!r}, 'a') as log: print(json.dumps(sys.argv[1:]), file=log)\n"
        "shutil.copyfile(sys.argv[sys.argv.index('-i')+1], sys.argv[-1])\n")
    fake = fake_ffmpeg("copy", f'exec "{sys.executable}" "{copy_script}" "$@"')
    monkeypatch.setattr(engine, "get_ffmpeg_path", lambda: fake)
    out = tmp_path / "out"
    results = []
    template = engine.UniqueParams(gentle_compression=True)
    count, errors = engine.process_batch([media["land"]], str(out), template,
                                         compression_callback=results.append)
    assert (count, errors) == (0, [])
    assert results[0].outcome == "skipped_without_saving"
    commands = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(commands) == 2
    for cmd, quality in zip(commands, ("21", "23")):
        assert cmd[cmd.index("-i")+1] == media["land"]
        assert cmd[cmd.index("-preset")+1] == "medium"
        assert cmd[cmd.index("-crf")+1] == quality
        cmd[cmd.index("-crf")+1] = ""
        assert Path(cmd.pop()).suffix.lower() in {".mp4", ".mov"}
    assert commands[0] == commands[1]
    assert list(out.iterdir()) == []


def test_gentle_image_measures_final_metadata(media, tmp_path, monkeypatch):
    from pathlib import Path
    source = media["photo"]
    original_size = Path(source).stat().st_size
    cmd_log = []
    def encode(cmd, cancelled):
        cmd_log.append(cmd.copy())
        Path(cmd[-1]).write_bytes(b"x" * (original_size - 1))
        return ""
    def metadata(path, *args, **kwargs):
        with open(path, "ab") as output:
            output.write(b"exif")
    monkeypatch.setattr(image_engine, "_apply_metadata", metadata)
    # Probe before replacing the encoder subprocess.
    info = engine.probe_file(source)
    monkeypatch.setattr(image_engine, "probe_file", lambda path: info)
    monkeypatch.setattr(image_engine, "_run_image_encode", encode)
    results = []
    ok, err = image_engine.process_single_image(
        source, str(tmp_path), 1, image_engine.ImageParams(gentle_compression=True),
        compression_callback=results.append)
    assert ok, err
    assert results[0].outcome == "skipped_without_saving"
    assert [cmd[cmd.index("-q:v")+1] for cmd in cmd_log] == ["3", "4"]
    assert all(cmd[cmd.index("-huffman")+1] == "optimal" for cmd in cmd_log)


def test_gentle_two_frame_clip_keeps_video_and_batch_continues(media, tmp_path):
    short = str(tmp_path / "short.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=320x180:d=0.08:r=25", short], check=True)
    params = engine.UniqueParams(target_width=0, target_height=0, gentle_compression=True,
                                 keep_without_saving=True)
    results = []
    count, errors = engine.process_batch([short, media["land"]], str(tmp_path / "out"),
                                         params, max_workers=1, compression_callback=results.append)
    assert (count, errors) == (2, [])
    import json
    streams = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time,duration",
        "-of", "json", results[1].output_path]))["streams"]
    assert {s["codec_type"] for s in streams} == {"video", "audio"}
    for field, tolerance in (("start_time", 0.045), ("duration", 0.15)):
        assert abs(float(streams[0][field]) - float(streams[1][field])) < tolerance
    for result in results:
        frames = subprocess.check_output(["ffprobe", "-v", "error", "-count_frames",
                                          "-select_streams", "v:0", "-show_entries",
                                          "stream=nb_read_frames", "-of", "csv=p=0",
                                          result.output_path], text=True)
        assert int(frames) > 0


def test_filter_capacity_is_not_a_full_output_disk():
    assert not engine.is_disk_full("[fc#0 @ 123] Error sending frames: No space left on device")
    assert engine.is_disk_full("[out#0/mp4 @ 123] Error writing trailer: No space left on device")


@pytest.mark.parametrize("ext", ["png", "webp", "tiff"])
def test_gentle_auto_alpha_matches_processed_rgba(tmp_path, ext):
    from pathlib import Path
    from PyQt6.QtGui import QImage, qRgba
    src = tmp_path / f"edges.{ext}"
    image = QImage(96, 64, QImage.Format.Format_Indexed8)
    image.setColorTable([qRgba(50, 110, 200, a) for a in (0, 85, 170, 255)])
    for x in range(image.width()):
        for y in range(image.height()):
            image.setPixel(x, y, x // 24)
    if ext == "tiff":
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    assert image.save(str(src))
    info = engine.probe_file(str(src))
    assert info.has_alpha
    p = image_engine.ImageParams(gentle_compression=True, keep_without_saving=True,
                                 zoom=1, crop_margin=0, fake_meta=False)
    results = []
    ok, error = image_engine.process_single_image(str(src), str(tmp_path), 1, p,
                                                  compression_callback=results.append)
    assert ok, error
    output = Path(results[0].output_path)
    assert output.suffix == ".png" and results[0].attempts == 1
    p.output_format = "png"
    p.noise_strength = p.vignette_angle = 0
    p.target_width, p.target_height = image_engine.plan_image_canvas(info.width, info.height, p)
    plan = engine.plan_geometry(info.width, info.height, p)
    graph = image_engine.build_image_graph(p, plan, info.width, info.height, True)
    def rgba(path, filters):
        return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), *filters,
                                        "-frames:v", "1", "-pix_fmt", "rgba", "-f", "rawvideo", "-"])
    pixels = rgba(output, [])
    assert pixels == rgba(src, graph)
    assert {0, 255}.issubset(pixels[3::4]) and any(0 < a < 255 for a in pixels[3::4])


def test_cancel_hung_gentle_image(media, fake_ffmpeg, monkeypatch, tmp_path):
    hang = fake_ffmpeg("image_cancel", "exec sleep 613")
    monkeypatch.setattr(image_engine, "get_ffmpeg_path", lambda: hang)
    monkeypatch.setattr(image_engine, "_FFMPEG_IMAGE_TIMEOUT", 3)
    out = tmp_path / "out"
    out.mkdir()
    flag = threading.Event()
    threading.Timer(0.4, flag.set).start()
    started = time.monotonic()
    result = image_engine.process_single_image(media["photo"], str(out), 1,
                                               image_engine.ImageParams(gentle_compression=True),
                                               cancelled=flag.is_set)
    assert result == (False, "cancelled")
    assert time.monotonic() - started < 2.5
    assert list(out.iterdir()) == [] and _no_orphans("sleep 613")


def test_manual_hardware_failure_retries_software(media, tmp_path, fake_ffmpeg, monkeypatch):
    import shlex
    trace = tmp_path / "attempts"
    fake = fake_ffmpeg("hw", f'case "$*" in *"-c:v h264_videotoolbox"*) '
                       f'echo hw >> {shlex.quote(str(trace))}; exit 1;; esac\n'
                       f'echo sw >> {shlex.quote(str(trace))}\nexec ffmpeg "$@"')
    monkeypatch.setattr(engine, "available_video_encoders",
                        lambda: {engine.ENCODER_H264_VIDEOTOOLBOX, engine.ENCODER_LIBX264})
    monkeypatch.setattr(engine, "get_ffmpeg_path", lambda: fake)
    p = engine.UniqueParams(encoder=engine.ENCODER_H264_VIDEOTOOLBOX,
                            target_width=0, target_height=0, fake_meta=False)
    ok, err = engine.process_single_video(media["land"], str(tmp_path), 1, p)
    assert ok, err
    assert trace.read_text().splitlines() == ["hw", "sw"]
    assert _dims(str(tmp_path / "uniq_1.mp4")) == (640, 360)
