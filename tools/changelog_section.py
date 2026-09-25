#!/usr/bin/env python3
"""Print the CHANGELOG.md section for one version; used as release notes.

    python3 tools/changelog_section.py 1.0.0-beta.3

Exits non-zero when the section is missing or empty, so a tag cannot be
released without its notes having been written — the CI release job runs this
before it builds anything.
"""

import os
import re
import sys

CHANGELOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md")


def section(text: str, version: str) -> str:
    """Body under ``## <version>`` (optionally followed by `` — date``)."""
    heading = re.compile(rf"^## \[?{re.escape(version)}\]?(\s+—.*)?\s*$", re.M)
    m = heading.search(text)
    if not m:
        return ""
    nxt = re.compile(r"^## ", re.M).search(text, m.end())
    return text[m.end():nxt.start() if nxt else len(text)].strip()


def main(argv) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[0].lstrip("v")
    with open(CHANGELOG, encoding="utf-8") as f:
        body = section(f.read(), version)
    if not body:
        print(f"CHANGELOG.md has no section for {version}. Rename 'Unreleased' "
              f"to '{version} — <date>' before tagging.", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
