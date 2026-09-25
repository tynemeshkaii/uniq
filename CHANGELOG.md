# Changelog

What changed for testers in each build. The newest is at the top. Version
numbers match the app's Help → About and the git tags.

When cutting a release, rename **Unreleased** to the new version; the CI
release job publishes that section as the GitHub release notes.

## 1.0.0-beta.3 — 2026-09-25

### Added
- **Help → Report a Problem…** writes a report zip to the Desktop (description,
  version, settings, crash traces, and the app log if you allow it) and can open
  a pre-filled GitHub issue.
- If the app crashes or is force-quit, it offers to create a report on the
  next launch. Hard crashes now leave a stack trace in `crash.log`.
- **Help → Check for Updates…**, plus an optional daily check (asked once on
  first launch). Only GitHub's public release list is contacted.
- Tagged builds are published as GitHub releases with the DMG attached.

### Changed
- Tester guide covers reporting problems and getting new betas.

### Fixed
- Dialogs (the end-of-batch summary, errors, the preset name prompt) showed
  light grey text on a light background and were hard to read.

## 1.0.0-beta.2 — 2026-09-25

### Added
- Settings are remembered between launches: parameters, output folder,
  window size.
- Named presets (Save Preset… / Delete in the top bar).
- A simple view by default: video size, speed profile and an
  *Advanced settings* switch for the full tuning tabs.
- Video size presets: Match Source (default), 9:16, 4:5, 1:1, 16:9, custom.
- Per-file status in the list (processing / done / failed with the reason on
  hover / skipped), **Retry Failed**, and **Show in Finder**.
- One progress bar for the whole batch with a time-left estimate and the
  names of the files being processed.

### Changed
- Videos keep their own aspect ratio by default (long side capped at
  1920 px) instead of always becoming 1080×1920 with a blurred backdrop.
- The default speed profile is *Balanced*; Reset Defaults no longer shows
  *Max Quality* over a fast preset.

### Fixed
- An overlay that cannot be read is rejected when chosen, instead of being
  skipped silently for every file.
- Status colours in the file list were not shown.

## 1.0.0-beta.1 — 2026-09-25

First closed-beta build.

### Added
- Still images (JPEG, PNG, WEBP) alongside video.
- Help menu: Show Log in Finder, Copy Diagnostics, Third-Party Licenses, About.
- A log file in `~/Library/Logs/Video Uniqualizer/`.

### Fixed
- The app bundled an ffmpeg that only ran on the Mac that built it; every
  file failed elsewhere. ffmpeg and its libraries are now inside the app.
- Quitting during processing crashed the app and left ffmpeg running.
- Cancel had no effect when ffmpeg stopped responding; a stuck file is now
  stopped after three minutes without progress.
- A full output disk failed every remaining file one by one; the batch now
  stops and says the disk is full, and warns beforehand when space is short.
- Dropping a large folder froze the window; hidden files, `._` files from
  external drives, photo libraries and the output folder are skipped.
