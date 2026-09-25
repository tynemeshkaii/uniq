"""Checks GitHub Releases for a newer build. No Qt, no third-party code.

The request goes through /usr/bin/curl rather than urllib: the frozen app
carries no CA bundle for Python's OpenSSL, so urllib fails certificate
verification inside the .app, while curl uses the macOS trust store. Only the
public releases list of this repository is fetched; nothing about the user or
the machine is sent beyond what any HTTPS request carries.

Testers on a pre-release are offered newer pre-releases as well as finals;
someone on a final build is only offered finals.

The REST API allows 60 anonymous requests an hour per IP address, and a
tester behind a shared office or VPN address can find that spent by others.
On a 403/429 the check falls back to the repository's releases Atom feed,
which is not counted against that limit and carries the same tags.
"""

import json
import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Optional, Tuple

from version import DISPLAY_VERSION, PRERELEASE, RELEASES_API, RELEASES_FEED, parse_version

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


def _curl(url: str, accept: str) -> Tuple[int, str]:
    """(HTTP status, body). Raises UpdateCheckError when no response arrives."""
    try:
        proc = subprocess.run(
            [_CURL, "-sSL", "--max-time", str(_TIMEOUT), "-w", "\n%{http_code}",
             "-H", f"Accept: {accept}",
             "-H", f"User-Agent: VideoUniqualizer/{DISPLAY_VERSION}", url],
            capture_output=True, text=True, timeout=_TIMEOUT + 5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateCheckError(f"could not run curl: {exc}") from exc
    if proc.returncode != 0:
        raise UpdateCheckError(proc.stderr.strip() or f"curl exited {proc.returncode}")
    body, _, code = proc.stdout.rpartition("\n")
    try:
        return int(code), body
    except ValueError as exc:
        raise UpdateCheckError("unreadable response") from exc


def fetch_releases(api_url: str = RELEASES_API, feed_url: str = RELEASES_FEED) -> List[dict]:
    """Release dicts in the REST API's shape, from the API or the feed."""
    status, body = _curl(api_url, "application/vnd.github+json")
    if status in (403, 429):
        log.info("GitHub API refused the request (%s, rate limit); using the feed", status)
        return fetch_feed(feed_url)
    if status != 200:
        raise UpdateCheckError(f"GitHub answered HTTP {status}")
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise UpdateCheckError("unreadable response from GitHub") from exc
    if not isinstance(data, list):
        raise UpdateCheckError("unexpected response from GitHub")
    return data


_ATOM = "{http://www.w3.org/2005/Atom}"


class _TextExtractor(HTMLParser):
    """Release-notes HTML to plain text: list items as "- ", stop at <hr>.

    Everything after the rule is the build's technical release notes, which
    are not what a tester needs in an update prompt.
    """

    def __init__(self):
        super().__init__()
        self.parts: List[str] = []
        self.done = False

    def handle_starttag(self, tag, attrs):
        if tag == "hr":
            self.done = True
        elif not self.done and tag == "li":
            self.parts.append("\n- ")
        elif not self.done and tag in ("h1", "h2", "h3", "p", "ul"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.done:
            self.parts.append(data)


def _html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    parser.feed(markup)
    text = "".join(parser.parts)
    return re.sub(r"[ \t]*\n\s*\n\s*", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def fetch_feed(url: str = RELEASES_FEED) -> List[dict]:
    status, body = _curl(url, "application/atom+xml")
    if status != 200:
        raise UpdateCheckError(f"GitHub answered HTTP {status}")
    return parse_feed(body)


def parse_feed(xml_text: str) -> List[dict]:
    """The feed has no pre-release flag; a hyphenated tag is a pre-release,
    which is the same rule the release workflow uses to set that flag."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise UpdateCheckError("unreadable release feed") from exc
    releases = []
    for entry in root.findall(f"{_ATOM}entry"):
        entry_id = entry.findtext(f"{_ATOM}id") or ""
        tag = entry_id.rsplit("/", 1)[-1]
        link = entry.find(f"{_ATOM}link")
        releases.append({
            "tag_name": tag,
            "name": entry.findtext(f"{_ATOM}title") or tag,
            "html_url": link.get("href", "") if link is not None else "",
            # findtext already undoes the XML escaping, which leaves HTML.
            "body": _html_to_text(entry.findtext(f"{_ATOM}content") or ""),
            "prerelease": "-" in tag,
            "draft": False,
        })
    return releases


def tester_notes(body: str) -> str:
    """The CHANGELOG part of a release body.

    The release workflow appends the build's technical notes after a "---"
    rule; the update prompt shows only what changed.
    """
    return re.split(r"^---\s*$", body, maxsplit=1, flags=re.M)[0].strip()


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
                       url=r.get("html_url", ""), notes=tester_notes(r.get("body") or ""),
                       prerelease=bool(r.get("prerelease")))
    return best


def check_for_update() -> Optional[Release]:
    """Raises UpdateCheckError when the check itself fails."""
    update = newest_update(fetch_releases())
    log.info("update check: current %s, newest offered %s",
             DISPLAY_VERSION, update.tag if update else "none")
    return update
