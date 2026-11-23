"""Select a completed export by its on-disk byte count."""
from dataclasses import dataclass
import ctypes
import errno
import os
from pathlib import Path
import sys
import tempfile
from typing import Callable, Literal

VIDEO_CRF_STEPS = (21, 23)
JPEG_QUALITY_STEPS = (3, 4)

DISK_FULL_ERROR = "output disk is full"


@dataclass(frozen=True)
class CompressionResult:
    input_path: str
    output_path: str | None
    input_bytes: int
    output_bytes: int | None
    attempts: int
    outcome: Literal["reduced", "kept_without_saving", "skipped_without_saving"]


def file_error(exc: OSError) -> str:
    return DISK_FULL_ERROR if exc.errno == errno.ENOSPC else str(exc)


def _publish_exclusive(candidate: Path, destination: Path) -> None:
    """Make a complete file visible without replacing an existing directory entry."""
    if sys.platform == "darwin":
        # renamex_np(RENAME_EXCL) also works on volumes without hard links.
        rename = ctypes.CDLL(None, use_errno=True).renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        if rename(os.fsencode(candidate), os.fsencode(destination), 0x00000004):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))
    else:
        os.link(candidate, destination)


def select_candidate(source: str, preferred: str, limit: int, keep: bool,
                     encode: Callable[[str, int], str],
                     cancelled: Callable[[], bool] | None) -> CompressionResult:
    """Encode privately, then atomically acquire a free name for the chosen file."""
    dest = Path(preferred)

    def check_cancelled():
        if cancelled and cancelled():
            raise ValueError("cancelled")

    with open(source, "rb") as stream:
        size = os.fstat(stream.fileno()).st_size
    if size <= 0:
        raise ValueError("empty input")
    with tempfile.TemporaryDirectory(prefix=".gentle-", dir=dest.parent) as folder:
        first = Path(folder) / ("0" + dest.suffix)
        for attempt in range(limit):
            check_cancelled()
            path = Path(folder) / (str(attempt) + dest.suffix)
            error = encode(str(path), attempt)
            if error:
                raise ValueError(error)
            check_cancelled()
            written = path.stat().st_size
            if not written:
                raise ValueError("empty encoded file")
            if written < size:
                outcome = "reduced"
                break
        else:
            if not keep:
                return CompressionResult(source, None, size, None, limit, "skipped_without_saving")
            path = first
            written = first.stat().st_size
            outcome = "kept_without_saving"

        for suffix in range(10000):
            check_cancelled()
            name = dest if not suffix else dest.with_name(f"{dest.stem}_{suffix}{dest.suffix}")
            try:
                _publish_exclusive(path, name)
            except FileExistsError:
                continue
            return CompressionResult(source, str(name), size, written, attempt + 1, outcome)
        raise FileExistsError("all output names are in use")


def finish_compression(source, preferred, limit, keep, encode, cancelled, callback):
    try:
        result = select_candidate(source, preferred, limit, keep, encode, cancelled)
    except OSError as exc:
        return False, file_error(exc)
    except ValueError as exc:
        return False, str(exc)
    if callback:
        callback(result)
    return True, ""
