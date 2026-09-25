"""The window, driven headless: defaults, persistence, presets, the file list,
a real batch with per-file statuses, and quitting mid-batch."""

import os
import subprocess
import sys
import threading

import pytest

from conftest import wait_for


@pytest.fixture
def window(qapp, no_dialogs):
    import main
    w = main.MainWindow()
    yield w
    if w.worker is not None:
        w.worker.cancel()
        w.worker.wait()
    if w.scanner is not None:
        w.scanner.cancel()
        w.scanner.wait()
    w.deleteLater()


# ─── Defaults ────────────────────────────────────────────

def test_defaults_are_simple_view_match_source_balanced(window):
    pp = window.params_panel
    assert pp.combo_size.currentData() == "match"
    assert pp.target_size() == (0, 0)
    assert not pp.tabs.isVisibleTo(window)
    assert pp.combo_performance.currentData() == "balanced"
    assert pp.combo_preset.currentText() == "fast"


def test_reset_defaults_applies_the_profile(window):
    """Regression: Reset used to leave 'Max Quality' over a fast preset."""
    pp = window.params_panel
    pp.combo_performance.setCurrentIndex(pp.combo_performance.findData("quality"))
    pp.combo_preset.setCurrentText("veryslow")
    window._reset_params()
    assert pp.combo_performance.currentData() == "balanced"
    assert pp.combo_preset.currentText() == "fast"


def test_custom_size_is_even(window):
    pp = window.params_panel
    pp.combo_size.setCurrentIndex(pp.combo_size.findData("custom"))
    assert pp.spin_width.isVisibleTo(window)
    pp.spin_width.setValue(1081)
    pp.spin_height.setValue(1351)
    assert pp.target_size() == (1080, 1350)


# ─── Persistence & presets ───────────────────────────────

def test_settings_round_trip_and_clamp(qapp, no_dialogs, tmp_path):
    import main
    import settings_store
    w = main.MainWindow()
    pp = w.params_panel
    pp.combo_size.setCurrentIndex(pp.combo_size.findData("4x5"))
    pp.combo_performance.setCurrentIndex(pp.combo_performance.findData("quality"))
    pp.combo_preset.setCurrentText("medium")      # overrides what the profile set
    pp.chk_advanced.setChecked(True)
    w.output_folder = str(tmp_path)
    w._save_settings()

    state = settings_store.load_last_state()
    state["spin_hue_max"] = 999          # past the safety cap
    state["control_that_no_longer_exists"] = 1
    settings_store.save_last_state(state)

    w2 = main.MainWindow()
    p2 = w2.params_panel
    assert p2.combo_size.currentData() == "4x5"
    assert p2.combo_performance.currentData() == "quality"
    assert p2.combo_preset.currentText() == "medium"
    assert p2.chk_advanced.isChecked()
    assert w2.output_folder == str(tmp_path)
    assert p2.spin_hue_max.value() == p2.spin_hue_max.maximum()


def test_incompatible_saved_state_is_ignored(window):
    import settings_store
    settings_store.settings().setValue("params", '{"schema": 999, "state": {}}')
    assert settings_store.load_last_state() is None


def test_presets(window, monkeypatch):
    import main
    import settings_store
    pp = window.params_panel
    monkeypatch.setattr(main.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Reels", True)))
    pp.combo_size.setCurrentIndex(pp.combo_size.findData("9x16"))
    window._save_preset()
    assert settings_store.list_presets() == ["Reels"]

    window._reset_params()
    assert pp.combo_size.currentData() == "match"
    window.combo_presets.setCurrentIndex(window.combo_presets.findData("Reels"))
    window._load_selected_preset()
    assert pp.combo_size.currentData() == "9x16"

    window._delete_preset()
    assert settings_store.list_presets() == []


def test_preset_name_with_slash_is_rejected(window, monkeypatch, no_dialogs):
    import main
    import settings_store
    monkeypatch.setattr(main.QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("a/b", True)))
    window._save_preset()
    assert settings_store.list_presets() == []
    assert "warning" in no_dialogs


# ─── File list ───────────────────────────────────────────

@pytest.fixture
def drop_tree(tmp_path):
    t = tmp_path / "drop"
    for d in ("sub/.hidden", "Lib.photoslibrary", "out"):
        (t / d).mkdir(parents=True)
    for f in ("a.mp4", "._a.mp4", ".DS_Store", "b.jpg", "notes.txt",
              "sub/c.mov", "sub/.hidden/d.mp4", "Lib.photoslibrary/e.jpg", "out/prev.mp4"):
        (t / f).write_bytes(b"")
    os.symlink(t / "a.mp4", t / "sub" / "link.mp4")
    return t


def test_folder_scan_filters(window, qapp, drop_tree):
    window.output_folder = str(drop_tree / "out")
    window._scan_folders([str(drop_tree)])
    assert not window.btn_process.isEnabled()          # locked while scanning
    wait_for(qapp, lambda: window.scanner is None, timeout=10)
    rel = sorted(os.path.relpath(f, drop_tree) for f in window.input_files)
    # Hidden, AppleDouble, packages, the output folder and the duplicate
    # symlink are all gone.
    assert rel == ["a.mp4", "b.jpg", "sub/c.mov"]


def test_folder_scan_is_capped(window, qapp, tmp_path, monkeypatch):
    import main
    monkeypatch.setattr(main, "MAX_SCAN_FILES", 50)
    for i in range(80):
        (tmp_path / f"f{i:03d}.png").write_bytes(b"")
    window._scan_folders([str(tmp_path)])
    wait_for(qapp, lambda: window.scanner is None, timeout=10)
    assert len(window.input_files) == 50
    assert "stopped at 50" in window.status_label.text()


def test_bulk_add_is_fast(window, tmp_path):
    import time
    paths = [str(tmp_path / f"f{i}.png") for i in range(2000)]
    t0 = time.monotonic()
    window._add_files(paths)
    assert len(window.input_files) == 2000
    assert time.monotonic() - t0 < 2.0


def test_disk_space_check(window, tmp_path, monkeypatch, no_dialogs):
    import main
    window.output_folder = str(tmp_path)
    real = main.shutil.disk_usage
    monkeypatch.setattr(main.shutil, "disk_usage",
                        lambda p: real(p)._replace(free=100 * 1024 * 1024))
    assert window._confirm_disk_space([]) is False
    assert no_dialogs[-1] == "critical"
    monkeypatch.setattr(main.os.path, "getsize", lambda p: 10 ** 9)
    monkeypatch.setattr(main.shutil, "disk_usage",
                        lambda p: real(p)._replace(free=2 * 1024 ** 3))
    assert window._confirm_disk_space(["x"] * 3) is True     # dialog answered Yes
    assert no_dialogs[-1] == "warning"


# ─── A real batch ────────────────────────────────────────

@pytest.mark.ffmpeg
def test_batch_statuses_and_retry(window, qapp, media, tmp_path):
    window.output_folder = str(tmp_path / "out")
    os.makedirs(window.output_folder)
    window._add_files([media["land"], media["broken"], media["photo"]])
    window.params_panel.combo_preset.setCurrentText("ultrafast")
    fractions = []
    window._start_processing()
    window.worker.overall_progress.connect(fractions.append)
    wait_for(qapp, lambda: window.worker is None)

    texts = [window.file_list.item(r).text() for r in range(window.file_list.count())]
    assert texts == ["✓ land.mp4", "✗ broken.mp4", "✓ photo.jpg"]
    assert "could not read video dimensions" in window.file_list.item(1).toolTip()
    assert window.btn_retry.isVisibleTo(window)
    assert window._failed_paths() == [media["broken"]]
    assert fractions == sorted(fractions) and fractions[-1] == pytest.approx(1.0)
    assert window.progress_bar.format().startswith("Done — 2/3")

    window._retry_failed()
    assert window.worker is not None and window.worker.files == [media["broken"]]
    wait_for(qapp, lambda: window.worker is None)
    assert window.file_list.item(0).text() == "✓ land.mp4"   # untouched by the retry


@pytest.mark.ffmpeg
def test_quit_during_batch_stops_cleanly(window, qapp, media, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    window.output_folder = str(out)
    window._add_files([media["long"]])
    window.params_panel.combo_preset.setCurrentText("slow")
    window.show()
    window._start_processing()
    wait_for(qapp, lambda: os.listdir(out), timeout=30)       # encoding has begun

    window.close()                                           # Cmd+Q mid-encode
    assert window.isVisible()                                # waits for the worker
    wait_for(qapp, lambda: not window.isVisible(), timeout=30)
    assert window.worker is None
    assert os.listdir(out) == []                             # partial file removed
    assert subprocess.run(["pgrep", "-f", media["long"]], capture_output=True).returncode != 0


# ─── Error reporting ─────────────────────────────────────

def test_worker_thread_exception_reaches_the_gui_thread(qapp):
    import main
    main._install_exception_hook()
    main._error_relay = main._ErrorRelay()
    shown = []
    main._error_relay.show.connect(shown.append)

    def boom():
        try:
            raise RuntimeError("boom in worker")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

    t = threading.Thread(target=boom)
    t.start()
    t.join()
    wait_for(qapp, lambda: shown, timeout=5)
    assert shown == ["boom in worker"]
