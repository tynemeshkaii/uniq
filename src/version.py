"""The one place the app's version lives.

main.py shows it, the log header records it, and VideoUniqualizer.spec reads
it into Info.plist. macOS wants CFBundleShortVersionString as bare integers, so
the pre-release tag is kept separate and only joined for display.
"""

VERSION = "1.0.0"
PRERELEASE = "beta.3"   # empty string for a final release

DISPLAY_VERSION = f"{VERSION}-{PRERELEASE}" if PRERELEASE else VERSION

# Where testers report problems and where releases are published. The update
# check reads the public GitHub Releases API for this repository; no other
# endpoint is contacted.
REPOSITORY = "tynemeshkaii/uniq"
ISSUES_URL = f"https://github.com/{REPOSITORY}/issues/new"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=20"


def parse_version(text: str):
    """Sortable key for "1.2.3", "v1.2.3" or "1.2.3-beta.4".

    A final release sorts above any pre-release of the same number, and
    pre-releases sort by their numeric suffix. Returns None for anything else,
    so a stray tag can never be mistaken for an update.
    """
    text = text.strip().lstrip("vV")
    core, dash, pre = text.partition("-")
    parts = core.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    nums = tuple(int(p) for p in parts)
    if not dash:
        return nums + (1, 0)
    if not pre:
        return None     # "1.0.0-": a malformed tag, not a final release
    label, _, num = pre.partition(".")
    if not label.isalpha() or (num and not num.isdigit()):
        return None
    return nums + (0, int(num or 0))
