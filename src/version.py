"""The one place the app's version lives.

main.py shows it, the log header records it, and VideoUniqualizer.spec reads
it into Info.plist. macOS wants CFBundleShortVersionString as bare integers, so
the pre-release tag is kept separate and only joined for display.
"""

VERSION = "1.0.0"
PRERELEASE = "beta.2"   # empty string for a final release

DISPLAY_VERSION = f"{VERSION}-{PRERELEASE}" if PRERELEASE else VERSION
