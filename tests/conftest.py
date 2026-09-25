"""Shared fixtures.

The suite runs headless (Qt's offscreen platform) and never touches the real
user's settings or logs: QSettings is redirected to a per-test INI file.

Tests that need ffmpeg are marked ``ffmpeg`` and skipped when it is not on
PATH, so the pure-Python half still runs anywhere.
"""

import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def pytest_configure(config):
    config.addinivalue_line("markers", "ffmpeg: needs ffmpeg/ffprobe on PATH")


def pytest_collection_modifyitems(config, items):
    if HAVE_FFMPEG:
        return
    skip = pytest.mark.skip(reason="ffmpeg/ffprobe not on PATH")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Every test gets its own settings file; the user's are never read."""
    from PyQt6.QtCore import QSettings
    import settings_store
    ini = str(tmp_path / "settings.ini")
    monkeypatch.setattr(settings_store, "settings",
                        lambda: QSettings(ini, QSettings.Format.IniFormat))
    return ini


@pytest.fixture
def no_dialogs(monkeypatch):
    """Answer every modal with Yes/OK so nothing blocks the headless run."""
    import main
    from PyQt6.QtWidgets import QMessageBox
    yes = QMessageBox.StandardButton.Yes
    shown = []

    def _record(kind, answer):
        return staticmethod(lambda *a, **k: shown.append(kind) or answer)

    monkeypatch.setattr(main.QMessageBox, "question", _record("question", yes))
    monkeypatch.setattr(main.QMessageBox, "warning", _record("warning", yes))
    monkeypatch.setattr(main.QMessageBox, "critical", _record("critical", QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(main.QMessageBox, "information", _record("information", QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(main.QMessageBox, "exec", lambda self: shown.append("exec") or 0)
    return shown


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    """Small generated inputs, built once per session."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    d = tmp_path_factory.mktemp("media")
    land = str(d / "land.mp4")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:d=2:r=30",
            "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-preset",
            "ultrafast", "-c:a", "aac", "-shortest", land)
    # Long enough that a cancel always lands mid-encode.
    long_clip = str(d / "long.mp4")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=720x1280:d=30:r=30",
            "-f", "lavfi", "-i", "sine=d=30", "-c:v", "libx264", "-preset",
            "ultrafast", "-c:a", "aac", "-shortest", long_clip)
    photo = str(d / "photo.jpg")
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=s=800x600", "-frames:v", "1", photo)
    broken = str(d / "broken.mp4")
    with open(broken, "w") as f:
        f.write("not a video")
    return {"land": land, "long": long_clip, "photo": photo, "broken": broken}


@pytest.fixture
def fake_ffmpeg(tmp_path):
    """Build a stand-in ffmpeg from a shell body.

    It answers ``-encoders`` like the real one, so the encoder probe does not
    sit out its own timeout before the behaviour under test starts.
    """
    def make(name, body):
        path = tmp_path / name
        path.write_text(
            "#!/bin/sh\n"
            'case "$*" in *-encoders*) echo " V..... libx264"; exit 0;; esac\n'
            + body + "\n")
        path.chmod(0o755)
        return str(path)
    return make


def wait_for(app, predicate, timeout=120.0):
    """Pump the Qt event loop until ``predicate()`` or the timeout."""
    import time
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        app.processEvents()
        time.sleep(0.02)
