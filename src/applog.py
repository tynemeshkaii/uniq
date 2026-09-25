"""Persistent log file and the diagnostics a tester pastes into a bug report.

A bundled .app has no terminal, so anything printed to stderr is lost. The log
lives where Console.app looks for per-user app logs, rotates so a long beta
cannot fill the disk, and starts every session with the facts a report needs
before anyone has to ask: version, OS, architecture, and which ffmpeg ran.

No Qt here, so the engine and the command-line paths can use it too.
"""

import atexit
import faulthandler
import json
import logging
import logging.handlers
import os
import platform
import sys
import threading
import time
import zipfile
from typing import Optional

from version import DISPLAY_VERSION

APP_LOG_NAME = "Video Uniqualizer"
LOG_FILENAME = "app.log"
CRASH_FILENAME = "crash.log"
SESSION_MARKER = "session.lock"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 4

_log_path: Optional[str] = None
_crash_file = None   # kept open for the life of the process; faulthandler writes to its fd


def log_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Library", "Logs", APP_LOG_NAME)


def log_path() -> Optional[str]:
    """Path of the active log file, or None when it could not be opened."""
    return _log_path


def setup_logging(level: int = logging.INFO) -> Optional[str]:
    """Route the root logger to the rotating file and to stderr.

    Never raises: a read-only home or a full disk must not stop the app from
    starting, so a failure to open the file leaves stderr logging only.
    """
    global _log_path
    root = logging.getLogger()
    if getattr(root, "_uniq_configured", False):
        return _log_path
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        os.makedirs(log_dir(), exist_ok=True)
        path = os.path.join(log_dir(), LOG_FILENAME)
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
        handler.setFormatter(fmt)
        root.addHandler(handler)
        _log_path = path
    except OSError as exc:
        root.warning("could not open log file in %s: %s", log_dir(), exc)

    root._uniq_configured = True  # type: ignore[attr-defined]
    _install_thread_hook()
    return _log_path


def _install_thread_hook():
    """Log exceptions that escape a plain ``threading.Thread``.

    The default hook prints to stderr, which in the .app goes nowhere.
    """
    def _hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        logging.getLogger("thread").error(
            "unhandled exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = _hook


def system_summary() -> str:
    mac = platform.mac_ver()[0] or platform.platform()
    frozen = "app bundle" if getattr(sys, "frozen", False) else "source"
    return (f"Video Uniqualizer {DISPLAY_VERSION} ({frozen}); "
            f"macOS {mac} {platform.machine()}; "
            f"Python {platform.python_version()}")


def diagnostics(ffmpeg_path: str, ffmpeg_version: str, tail_lines: int = 150) -> str:
    """Plain-text block for Help -> Copy Diagnostics."""
    lines = [
        system_summary(),
        f"ffmpeg: {ffmpeg_path}",
        f"        {ffmpeg_version}",
        f"log: {_log_path or 'unavailable'}",
        "",
        f"--- last {tail_lines} log lines ---",
    ]
    if _log_path and os.path.isfile(_log_path):
        try:
            with open(_log_path, encoding="utf-8", errors="replace") as f:
                lines.extend(ln.rstrip("\n") for ln in f.readlines()[-tail_lines:])
        except OSError as exc:
            lines.append(f"(could not read log: {exc})")
    else:
        lines.append("(no log file)")
    return "\n".join(lines)


# ─── Crash detection ─────────────────────────────────────

def start_session() -> Optional[str]:
    """Arm crash capture; return a description of an unclean previous exit.

    Two mechanisms, because they catch different failures:

    * ``faulthandler`` dumps every thread's Python stack to crash.log on a
      fatal signal — a segfault inside Qt, an abort from qFatal — which kills
      the process before any Python exception hook can run.
    * A session marker is written at start and removed by ``atexit``. atexit
      does not run on a fatal signal or a force quit, so a marker still present
      at the next launch means the last session ended abnormally, including
      the kinds faulthandler cannot see (SIGKILL, a hang the user force-quit).
    """
    global _crash_file
    try:
        os.makedirs(log_dir(), exist_ok=True)
    except OSError:
        return None
    marker = os.path.join(log_dir(), SESSION_MARKER)
    previous = None
    if os.path.exists(marker):
        try:
            with open(marker, encoding="utf-8") as f:
                previous = f.read().strip() or "unknown session"
        except OSError:
            previous = "unknown session"
        logging.getLogger("app").warning("previous session did not exit cleanly: %s", previous)
    crash_path = os.path.join(log_dir(), CRASH_FILENAME)
    _trim(crash_path, _MAX_CRASH_BYTES, _KEEP_CRASH_BYTES)
    try:
        _crash_file = open(crash_path, "a", encoding="utf-8")
        _crash_file.write(f"\n=== session {time.strftime('%Y-%m-%d %H:%M:%S')} "
                          f"pid {os.getpid()} {DISPLAY_VERSION} ===\n")
        _crash_file.flush()
        faulthandler.enable(file=_crash_file, all_threads=True)
    except OSError as exc:
        logging.getLogger("app").warning("crash capture unavailable: %s", exc)
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write(f"{DISPLAY_VERSION}, started {time.strftime('%Y-%m-%d %H:%M:%S')}, pid {os.getpid()}")
        atexit.register(_end_session, marker)
    except OSError:
        pass
    return previous


_MAX_CRASH_BYTES = 1024 * 1024
_KEEP_CRASH_BYTES = 256 * 1024


def _trim(path: str, max_bytes: int, keep: int):
    """Every launch appends a session header, so the file is cut back when big."""
    try:
        if os.path.getsize(path) <= max_bytes:
            return
        with open(path, "rb") as f:
            f.seek(-keep, os.SEEK_END)
            tail = f.read()
        with open(path, "wb") as f:
            f.write(b"(older entries trimmed)\n" + tail)
    except OSError:
        pass


def _end_session(marker: str):
    try:
        os.remove(marker)
    except OSError:
        pass


def crash_log_tail(lines: int = 80) -> str:
    path = os.path.join(log_dir(), CRASH_FILENAME)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""


# ─── Problem reports ─────────────────────────────────────

def build_report(description: str, include_logs: bool, settings_state: dict,
                 ffmpeg_path: str, ffmpeg_version: str,
                 previous_crash: Optional[str] = None,
                 dest_dir: Optional[str] = None) -> str:
    """Write a zip a tester can attach to a report; return its path.

    Nothing is sent anywhere: the tester decides where the file goes. App logs
    name the files and folders that were processed, so they are included only
    when ``include_logs`` is set. The crash log holds stack traces of this
    app's own code and is always included.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest_dir = dest_dir or os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.isdir(dest_dir) or not os.access(dest_dir, os.W_OK):
        dest_dir = log_dir()
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"VideoUniqualizer-report-{stamp}.zip")

    summary = [
        "Video Uniqualizer problem report",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        system_summary(),
        f"ffmpeg: {ffmpeg_path}",
        f"        {ffmpeg_version}",
        f"Logs included: {'yes' if include_logs else 'no'}",
    ]
    if previous_crash:
        summary.append(f"Previous session ended abnormally: {previous_crash}")
    summary += ["", "What happened:", description.strip() or "(no description)", ""]

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.txt", "\n".join(summary))
        state = dict(settings_state)
        if not include_logs and state.get("overlay_file"):
            state["overlay_file"] = "(path withheld; logs not included)"
        z.writestr("settings.json", json.dumps(state, indent=2, sort_keys=True))
        crash = os.path.join(log_dir(), CRASH_FILENAME)
        if os.path.isfile(crash):
            z.write(crash, CRASH_FILENAME)
        if include_logs:
            for i in range(_BACKUPS + 1):
                name = LOG_FILENAME + (f".{i}" if i else "")
                src = os.path.join(log_dir(), name)
                if os.path.isfile(src):
                    z.write(src, name)
    logging.getLogger("app").info("problem report written to %s (logs %s)",
                                  path, "included" if include_logs else "excluded")
    return path
