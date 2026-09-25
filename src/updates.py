"""Checks GitHub Releases for a newer build. No Qt, no third-party code.

The request goes through /usr/bin/curl rather than urllib: the frozen app
carries no CA bundle for Python's OpenSSL, so urllib fails certificate
verification inside the .app, while curl uses the macOS trust store. Only the
public releases list of this repository is fetched; nothing about the user or
the machine is sent beyond what any HTTPS request carries.

Testers on a pre-release are offered newer pre-releases as well as finals;
someone on a final build is only offered finals.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from version import DISPLAY_VERSION, PRERELEASE, RELEASES_API, parse_version

log = logging.getLogger(__name__)

_CURL = "/usr/bin/curl"
_TIMEOUT = 15


@dataclass
class Release:
    tag: str
    name: str
    url: str          # the release page, where the DMG is attached
    notes: str
    prerelease: bool


class UpdateCheckError(Exception):
    pass


def fetch_releases(url: str = RELEASES_API) -> List[dict]:
    try:
        proc = subprocess.run(
            [_CURL, "-fsSL", "--max-time", str(_TIMEOUT),
             "-H", "Accept: application/vnd.github+json",
             "-H", f"User-Agent: VideoUniqualizer/{DISPLAY_VERSION}", url],
            capture_output=True, text=True, timeout=_TIMEOUT + 5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateCheckError(f"could not run curl: {exc}") from exc
    if proc.returncode != 0:
        raise UpdateCheckError(proc.stderr.strip() or f"curl exited {proc.returncode}")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise UpdateCheckError("unreadable response from GitHub") from exc
    if not isinstance(data, list):
        raise UpdateCheckError("unexpected response from GitHub")
    return data


def newest_update(releases: List[dict], current: str = DISPLAY_VERSION,
                  include_prereleases: bool = bool(PRERELEASE)) -> Optional[Release]:
    """The newest published release above ``current``, or None."""
    current_key = parse_version(current)
    best, best_key = None, current_key
    for r in releases:
        if not isinstance(r, dict) or r.get("draft"):
            continue
        if r.get("prerelease") and not include_prereleases:
            continue
        key = parse_version(str(r.get("tag_name", "")))
        if key is None or (best_key is not None and key <= best_key):
            continue
        best_key = key
        best = Release(tag=r["tag_name"], name=r.get("name") or r["tag_name"],
                       url=r.get("html_url", ""), notes=r.get("body") or "",
                       prerelease=bool(r.get("prerelease")))
    return best


def check_for_update() -> Optional[Release]:
    """Raises UpdateCheckError when the check itself fails."""
    update = newest_update(fetch_releases())
    log.info("update check: current %s, newest offered %s",
             DISPLAY_VERSION, update.tag if update else "none")
    return update
