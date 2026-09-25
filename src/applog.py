"""Persistent log file and the diagnostics a tester pastes into a bug report.

A bundled .app has no terminal, so anything printed to stderr is lost. The log
lives where Console.app looks for per-user app logs, rotates so a long beta
cannot fill the disk, and starts every session with the facts a report needs
before anyone has to ask: version, OS, architecture, and which ffmpeg ran.

No Qt here, so the engine and the command-line paths can use it too.
"""

import logging
import logging.handlers
import os
import platform
import sys
import threading
from typing import Optional

from version import DISPLAY_VERSION

APP_LOG_NAME = "Video Uniqualizer"
LOG_FILENAME = "app.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 4

_log_path: Optional[str] = None


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
