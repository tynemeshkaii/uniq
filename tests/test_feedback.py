"""Crash capture, problem reports and the update check."""

import json
import os
import subprocess
import sys
import textwrap
import zipfile
from urllib.parse import parse_qs, urlparse

import pytest

import applog
import updates
from version import DISPLAY_VERSION, parse_version

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


# ─── Versions and the update choice ───────────────────────

def test_parse_version_ordering():
    assert parse_version("v1.0.0") > parse_version("1.0.0-beta.10") > parse_version("1.0.0-beta.2")
    assert parse_version("1.0.1-beta.1") > parse_version("1.0.0")
    for junk in ("latest", "1.0", "v1.0.0-", "1.0.0-beta.x", ""):
        assert parse_version(junk) is None


def _rel(tag, pre=False, draft=False):
    return {"tag_name": tag, "name": tag, "html_url": f"https://example/{tag}",
            "body": "notes", "prerelease": pre, "draft": draft}


def test_newest_update_for_a_beta_tester():
    releases = [_rel("v1.0.0-beta.1", True), _rel("v1.0.0-beta.3", True),
                _rel("v1.0.0-beta.4", True, draft=True), _rel("nightly", True)]
    r = updates.newest_update(releases, current="1.0.0-beta.2", include_prereleases=True)
    assert r.tag == "v1.0.0-beta.3" and r.url == "https://example/v1.0.0-beta.3"


def test_final_release_beats_betas():
    releases = [_rel("v1.0.0-beta.3", True), _rel("v1.0.0")]
    assert updates.newest_update(releases, "1.0.0-beta.2", True).tag == "v1.0.0"


def test_final_users_are_not_offered_betas():
    releases = [_rel("v1.1.0-beta.1", True)]
    assert updates.newest_update(releases, "1.0.0", include_prereleases=False) is None


def test_no_update_when_current():
    assert updates.newest_update([_rel("v1.0.0-beta.2", True)], "1.0.0-beta.2", True) is None


def test_fetch_failure_raises(monkeypatch):
    with pytest.raises(updates.UpdateCheckError):
        updates.fetch_releases("https://127.0.0.1:9/nothing-listens-here")


# ─── Crash detection (in child processes: faulthandler is process-wide) ──

def _child(tmp_path, body):
    script = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {SRC!r})
        os.environ["HOME"] = {str(tmp_path)!r}
        import applog
        previous = applog.start_session()
        print("PREVIOUS=" + repr(previous), flush=True)
    """) + textwrap.dedent(body)
    return subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, env={**os.environ, "HOME": str(tmp_path)})


def test_clean_exit_leaves_no_marker(tmp_path):
    _child(tmp_path, "")
    second = _child(tmp_path, "")
    assert "PREVIOUS=None" in second.stdout


def test_hard_crash_is_detected_and_traced(tmp_path):
    crashed = _child(tmp_path, "import ctypes; ctypes.string_at(0)")   # segfault
    assert crashed.returncode != 0
    after = _child(tmp_path, "")
    assert "PREVIOUS=None" not in after.stdout
    assert DISPLAY_VERSION in after.stdout
    crash_log = (tmp_path / "Library" / "Logs" / "Video Uniqualizer" / "crash.log").read_text()
    assert "Fatal Python error" in crash_log


def test_force_quit_is_detected(tmp_path):
    _child(tmp_path, "os._exit(0)")      # skips atexit, like SIGKILL would
    assert "PREVIOUS=None" not in _child(tmp_path, "").stdout


# ─── Problem reports ─────────────────────────────────────

@pytest.fixture
def logs_home(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    (d / "app.log").write_text("processed /Users/x/secret-client/video.mp4\n")
    (d / "crash.log").write_text("Fatal Python error: Segmentation fault\n")
    monkeypatch.setattr(applog, "log_dir", lambda: str(d))
    return tmp_path


def test_report_with_logs(logs_home):
    path = applog.build_report("It froze at 40%", True, {"overlay_file": "/a/b.mp4"},
                               "/bin/ffmpeg", "ffmpeg version 8.1",
                               previous_crash="1.0.0-beta.2", dest_dir=str(logs_home))
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        report = z.read("report.txt").decode()
        settings = json.loads(z.read("settings.json"))
    assert names == {"report.txt", "settings.json", "crash.log", "app.log"}
    assert "It froze at 40%" in report and "Previous session ended abnormally" in report
    assert settings["overlay_file"] == "/a/b.mp4"


def test_report_without_logs_withholds_paths(logs_home):
    path = applog.build_report("x", False, {"overlay_file": "/a/b.mp4"},
                               "/bin/ffmpeg", "v", dest_dir=str(logs_home))
    with zipfile.ZipFile(path) as z:
        assert "app.log" not in z.namelist()
        assert "crash.log" in z.namelist()
        assert "/a/b.mp4" not in z.read("settings.json").decode()


def test_issue_url_carries_summary_only(qapp):
    import main
    url = main.issue_url("App froze\nsecond line", "VideoUniqualizer-report-1.zip")
    q = parse_qs(urlparse(url).query)
    assert q["title"] == ["App froze"]
    assert "VideoUniqualizer-report-1.zip" in q["body"][0]
    assert "processed" not in q["body"][0]
    assert len(url) < 8000     # browsers and GitHub reject much longer


# ─── Update check in the window ──────────────────────────

def test_manual_update_check_offers_the_release(qapp, no_dialogs, monkeypatch):
    import main
    from conftest import wait_for
    release = updates.Release("v9.0.0", "v9.0.0", "https://example/r", "notes", False)
    monkeypatch.setattr(updates, "check_for_update", lambda: release)
    w = main.MainWindow()
    w.check_for_updates(manual=True)
    wait_for(qapp, lambda: w.update_checker is None, timeout=10)
    assert "exec" in no_dialogs          # the "Update Available" box was shown


def test_first_launch_asks_before_checking(qapp, no_dialogs, monkeypatch):
    import main
    import settings_store
    calls = []
    monkeypatch.setattr(main.MainWindow, "check_for_updates",
                        lambda self, manual: calls.append(manual))
    w = main.MainWindow()
    w.startup_update_check()
    assert no_dialogs.count("question") == 1 and calls == [False]
    assert settings_store.settings().value("updates/auto", type=bool)
    w.startup_update_check()               # asked once only
    assert no_dialogs.count("question") == 1


# ─── Changelog / release notes ───────────────────────────

def test_changelog_section_extraction():
    import changelog_section as cs
    text = "# C\n\n## Unreleased\n- a\n\n## 1.0.0-beta.2 — 2026-09-25\n### Added\n- b\n\n## 1.0.0-beta.1 — x\n- c\n"
    assert cs.section(text, "1.0.0-beta.2") == "### Added\n- b"
    assert cs.section(text, "1.0.0-beta.1") == "- c"
    assert cs.section(text, "1.0.0") == ""          # no prefix matches
    assert cs.main(["v9.9.9"]) == 1


def test_changelog_describes_the_current_version():
    """A tag of the current version must be releasable (CI runs the same check)."""
    import changelog_section as cs
    with open(cs.CHANGELOG, encoding="utf-8") as f:
        text = f.read()
    assert cs.section(text, DISPLAY_VERSION) or "## Unreleased" in text


# ─── Rate limit fallback ─────────────────────────────────

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.0.0-beta.3</id>
    <link rel="alternate" type="text/html" href="https://github.com/o/r/releases/tag/v1.0.0-beta.3"/>
    <title>Video Uniqualizer 1.0.0-beta.3</title>
    <content type="html">&lt;h3&gt;Fixed&lt;/h3&gt;&lt;ul&gt;&lt;li&gt;Dialogs are readable&lt;/li&gt;&lt;/ul&gt;&lt;hr&gt;&lt;pre&gt;build 23&lt;/pre&gt;</content>
  </entry>
  <entry>
    <id>tag:github.com,2008:Repository/1/v1.0.0</id>
    <link rel="alternate" type="text/html" href="https://github.com/o/r/releases/tag/v1.0.0"/>
    <title>Video Uniqualizer 1.0.0</title>
    <content type="html">&lt;p&gt;Final&lt;/p&gt;</content>
  </entry>
</feed>"""


def test_parse_feed():
    releases = updates.parse_feed(_FEED)
    assert [r["tag_name"] for r in releases] == ["v1.0.0-beta.3", "v1.0.0"]
    assert releases[0]["prerelease"] and not releases[1]["prerelease"]
    assert releases[0]["body"] == "Fixed\n\n- Dialogs are readable"   # stops at <hr>
    assert releases[0]["html_url"].endswith("/v1.0.0-beta.3")


def test_rate_limited_api_falls_back_to_feed(monkeypatch):
    calls = []

    def fake_curl(url, accept):
        calls.append(url)
        return (403, '{"message": "API rate limit exceeded"}') if "api.github" in url else (200, _FEED)

    monkeypatch.setattr(updates, "_curl", fake_curl)
    r = updates.newest_update(updates.fetch_releases(), "1.0.0-beta.2", True)
    assert r.tag == "v1.0.0"
    assert len(calls) == 2 and calls[1].endswith("releases.atom")


def test_other_http_errors_are_reported(monkeypatch):
    monkeypatch.setattr(updates, "_curl", lambda url, accept: (500, "oops"))
    with pytest.raises(updates.UpdateCheckError, match="HTTP 500"):
        updates.fetch_releases()


def test_api_notes_drop_the_build_block():
    body = "### Fixed\n- a\n\n---\n```\nbuild 23\n```"
    assert updates.tester_notes(body) == "### Fixed\n- a"
