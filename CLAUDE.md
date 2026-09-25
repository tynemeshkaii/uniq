# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Video Uniqualizer — a standalone macOS PyQt6 desktop app (HYBRID V9.0 engine) that batch-processes **videos and still images** so every output is perceptually identical to its source but differs in per-file randomized parameters and metadata. ffmpeg/ffprobe are bundled inside the `.app`; the built bundle needs no Python or system ffmpeg.

Stills target Meta Ads uploads: the source aspect is preserved (Meta drives placement off the creative's own ratio) and the output is JPEG for JPEG/WEBP sources, PNG for PNG.

## Commands

**Python environment.** `.venv/` (created by `build.sh`, Python 3.14) has PyQt6, PyInstaller and pytest; the Homebrew `python3` on this Mac has no pytest. Use `.venv/bin/python` for tests and the gate, or activate it first. From source, ffmpeg/ffprobe come from `PATH` (Homebrew); `get_ffmpeg_path()` only looks inside the bundle when frozen.

Modules under `src/` import each other flat (`import engine`, `from version import ...`) — there is no package. Run scripts from `src/` or put it on `sys.path` (`tests/conftest.py` adds `src/` and `tools/`).

Run the app from source (needs `pip3 install -r requirements.txt` + ffmpeg/ffprobe on PATH):
```bash
cd src && python3 main.py
```

Encoder benchmark (compares libx264, h264_videotoolbox, hevc_videotoolbox on this Mac):
```bash
cd src && python3 main.py --benchmark
```

Quality gate — **run this after any change to `engine.py` or `image_engine.py`**. It takes videos and images, routing by extension. It measures two opposing things, and a change that helps one usually costs the other:

1. *Does it still look right* — the output against a frame-aligned clean twin (same geometry, no color/noise/sharpen/vignette), isolating effect-stage degradation from reframing.
2. *Is it still recognisable* — pHash distance and audio bit error rate against the source, which is the only evidence that uniqualization does anything at all.

With no argument it uses a generated synthetic clip. `--keep DIR` puts the
intermediates and the two comparison PNGs (full frame, 1:1 detail crop) somewhere
you can look at them — judging by eye is the check that actually matters:
```bash
python3 src/verify_quality.py /path/to/clip.mp4 --keep /tmp/uniq-check
python3 src/verify_quality.py /path/to/photo.jpg --keep /tmp/uniq-check
```

The image path drops everything that needs a timeline (audio, A/V sync, GOP)
and adds two checks the video path has no use for: the source aspect must
survive within 1%, and the fabricated EXIF must name a real
`DEVICE_PROFILES` entry.

Exit status is non-zero if any check fails, and failures are listed per source.

Build the `.app`:
```bash
./build.sh          # local bundle in dist/
./build.sh share    # bundle + codesign verify + smoke launch + DMG + sha256 + release notes
```

Test suite (pytest, headless Qt via `QT_QPA_PLATFORM=offscreen`; 68 tests, ~6 s). Tests needing ffmpeg are marked `ffmpeg` and skip without it:
```bash
pip3 install -r requirements-dev.txt   # or just use .venv/bin/python
.venv/bin/python -m pytest
UPDATE_GOLDEN=1 python3 -m pytest tests/test_pure.py   # after an intended filter-graph change
```
- `tests/test_pure.py` — geometry invariants, sizing, naming, and `tests/golden/video_graph.txt`: a seeded snapshot of the video/audio filter graphs. That snapshot is the "video graph is byte-identical" check the `build_spatial_chain` note below asks for; regenerate it only for an intended change, and review the diff.
- `tests/test_engine_ffmpeg.py` — watchdog (cancel/stall), disk-full, dyld-dead ffmpeg (via scripted stand-in binaries), per-file callbacks, Match Source.
- `tests/test_feedback.py` — version ordering and update choice, crash detection (real segfault and `os._exit` in child processes), report contents and redaction, CHANGELOG sections.
- `tests/test_ui.py` — defaults, persistence and clamping, presets, folder scan filters, a real batch with statuses and Retry, quit mid-batch.
- Every test gets its own QSettings INI (`conftest.isolated_settings`); never point tests at the real preferences.

CI (`.github/workflows/ci.yml`, macOS arm64) runs pytest plus both quality gates on every push and PR; a `v*` tag also runs `./build.sh share` and uploads the DMG as an artifact. `verify_quality.py` remains the perceptual correctness gate; there is no linter.

## Architecture

Source layers under `src/`:
- **`engine.py`** — pure processing core for video. No Qt. Ffmpeg orchestration, randomization, geometry math.
- **`image_engine.py`** — the still pipeline. Reuses `engine.plan_geometry` and `engine.build_spatial_chain` rather than owning a second copy of the filter graph.
- **`exif.py`** — EXIF APP1 and PNG `tEXt` writer, by hand, no piexif. ffmpeg's mjpeg encoder discards `-metadata`, so the camera identity is a post-encode pass.
- **`main.py`** — PyQt6 GUI (~2.5k lines): `ParamsPanel` (every control; `build_ranges` / `build_image_ranges`), `MainWindow`, `MediaFileList`, `FolderScanWorker` (off-thread folder walk, capped at `MAX_SCAN_FILES`, skips dotfiles/AppleDouble and `.photoslibrary`-style packages), `ReportDialog`, `UpdateCheckWorker`, and `ProcessWorker(QThread)` — the only place engine work is invoked off the UI thread. `main()` also handles `--benchmark` and `UNIQ_SMOKE_TEST`.
- **`verify_quality.py`** — the quality gate (see Commands). Thresholds are module constants at the top (`SSIM_DEGRADATION_MIN`, `PHASH_DISTANCE_MIN`, `IMAGE_PHASH_DISTANCE_MIN`, ...); `neutralize_effects` / `neutralize_image_effects` build the clean twin.
- **`applog.py`** — rotating log, crash marker, `faulthandler`, diagnostics text, report zip. **`updates.py`** — GitHub Releases check via curl. **`version.py`** — `VERSION`, `PRERELEASE`, `DISPLAY_VERSION`, repo/issue URLs, `parse_version`.
- **`tools/bundle_ffmpeg.py`** (bundle/verify the ffmpeg dylib closure) and **`tools/changelog_section.py`** (prints a version's CHANGELOG section; CI release notes). `docs/superpowers/specs/` holds historical design notes, not current spec.
- **`settings_store.py`** — QSettings persistence and named presets. Reads state generically off `ParamsPanel`'s attributes (every spin/check/combo, by attribute name), so a new control is persisted with no extra code, and restores through the widgets' setters, so saved values are clamped to the current safety caps. Bump `SCHEMA_VERSION` if a saved value would change meaning.
- **`fingerprint.py`** — pHash and Haitsma-Kalker audio fingerprints, pure Python, no numpy. Dev-time only; nothing in `main.py` imports it, so it stays out of the bundle.
- **`styles.py` / `icons.py` / `gen_icns.py`** — skeuomorphic stylesheet, programmatically drawn Qt icons, and the macOS `.icns` generator (build-time).

### How the engine is wired (the key mental model)

- **`RandomRanges`** is the spec the UI owns: min/max per effect plus feature toggles. Built by `ParamsPanel.build_ranges()`.
- **`process_batch`** draws a **fresh `UniqueParams` per input file** from the ranges, copying only output-container settings (resolution, CRF, preset, encoder, overlay) from the UI template. So anything randomized varies per file, not per batch.
- **`plan_geometry(src_w, src_h, p)`** resolves rotation safety margin, micro-crop, zoom, and pan into a single crop rectangle in Python, emitting literal **even** numbers into the filter graph.
- **A 0×0 target means "match source".** `process_single_video` resolves it per file after probing via `match_source_size` (source aspect, long side capped at `MATCH_SOURCE_MAX_LONG_SIDE`, never upscaled), before `plan_geometry` runs. It is the UI default; the fixed presets (9:16, 4:5, 1:1, 16:9) fill any aspect mismatch with the blurred backdrop.
- **Per-file reporting.** Both batch functions take `started_callback(path)` and `result_callback(path, ok, error)`, keyed by full path, not basename; `process_batch` also takes `overall_progress_callback`, which counts partial progress of files in flight. The UI's list statuses, Retry Failed and the ETA are built on these.
- **`_STATIC_FIELDS`** is the explicit list that decides copied-vs-randomized. A new `UniqueParams` field is randomized per file *unless* you add it there — so any new output-container setting must be added to that list or it will silently vary per file.
- **`build_video_chain` / `build_audio_chain`** are pure functions of `UniqueParams` + `GeometryPlan`. That purity is what lets `verify_quality.py` exercise the filter chain without encoding.
- **`process_batch` is concurrent.** A `ThreadPoolExecutor` runs `default_worker_count()` files at once (cores/4, clamped 1-3, since x264 does not scale past a few cores), and splits x264 threads across jobs. Progress callbacks are the mean across active jobs, so per-file progress is not monotonic. Shared counters are under `state_lock`; keep new batch state there.
- **`build_spatial_chain` is the shared half**, and it is itself `build_geometry_steps` + `build_effect_steps`. It is every filter that is a function of one frame plus the resolved geometry, and both pipelines call it — video wraps it in `setpts`/`fps`, images use it alone. The two halves are separately addressable because a still with an alpha channel has to run geometry on both the colour and the alpha branch while running effects on the colour branch only. A still passes `warp_drift_period=0`, which is what makes the corner-drift term collapse to a static `eval=init` perspective, and `scale_flags="lanczos"`, which video does not set so it keeps `bicubic`. **Refactoring this function means re-checking the video graph is byte-identical**, not just that the gate passes.
- **`ImageParams` is duck-typed against that chain.** `plan_geometry` and `build_spatial_chain` read fields off it by name, so renaming a field in either dataclass silently changes the other pipeline's filter graph.
- **Stills mirror this with their own types:** `ImageRanges` → fresh `ImageParams` per file in `process_image_batch`, with `_STATIC_IMAGE_FIELDS` (`output_format`, `max_long_side`, `fake_meta`) as the copied list — same rule, a new container setting goes there. Inputs: `IMAGE_EXTENSIONS`; HEIC/HEIF/AVIF are `UNSUPPORTED_IMAGE_EXTENSIONS` (listed, then rejected with a message, not decoded).
- **Mixed batches run in two phases.** `ProcessWorker.run` does all stills first, then videos, as separate pools. The single "Parallel Jobs" value is a *video* worker count; `image_workers_for` rescales it onto the still scale (default = cores, max 8) rather than passing it through — passing it literally throttled stills ~2x. Overall progress weights a still at `_IMAGE_WEIGHT` (0.05) of a video.
- **`performance_profile`** (`quality` / `balanced` / `fast_mac`) and `encoder` (`libx264` / `h264_videotoolbox` / `hevc_videotoolbox`) both alter encode args. The UI's profile combo rewrites CRF/preset/encoder widgets in `_apply_performance_profile`, so profile changes reach the engine through those widgets, not as a separate path.

### Measure before adding a filter

The uniqueness numbers exist so that features are justified by evidence rather than by plausibility. Two results worth knowing before proposing anything:

- **Reframing dominates.** pHash distance rises monotonically with `zoom` — on organic footage 3.3 bits at zoom 1.02-1.05, 12.0 at 1.14-1.22, against a matcher threshold near 10. Every filter in `engine.py` combined moves that number less than the `zoom` range does. It costs sharpness, since the crop is scaled back up.
- **Low-amplitude global fields do not work.** An additive low-frequency luma field, sized to stay invisible, moved the hash by 0-2 bits of 63 and by 0 on organic content: pHash thresholds each DCT coefficient against the block median, and real footage has low-frequency coefficients that dwarf any perturbation small enough to hide. It was removed. A slow warp of the *playback time map* was removed for the same kind of reason — capped at 12 ms by lip-sync, it is smaller than one frame and smaller than one audio analysis frame. (Not to be confused with the spatial micro-warp, which is still in the engine: `warp_offsets` / `warp_drift_*` drift the four corners by up to 3 px, so a matcher cannot solve one fixed perspective transform.)

- **Stills behave the same way, only more so.** The same ablation on four photographs (pHash distance from the source, median over 6 draws each): rotation, lens distortion, micro-warp, the tone curve, hue, eq, grain at 1-3 *and* at 6-10, sharpening, vignette, the JPEG quality draw and the size jitter each measured **0**. `crop_margin` measured 2, `zoom` measured 6 / 12 / 20 at 1.02-1.05 / 1.06-1.14 / 1.14-1.22, and the defaults together measured 16-18. A plain re-encode scores 0, which is the control those numbers only mean something against.

  Those zero-scoring stages stay in the pipeline. They defeat different attacks — byte-level hashes, colour normalisation, a matcher that has already solved alignment — and they cost almost nothing. What the numbers rule out is *adding* another one in the hope that it will move the hash.

Run the ablation before and after: a feature that does not move either number is cost without benefit.

### Stills default to keeping the whole frame

`ImageRanges.preserve_full_frame` (the "Keep the full frame" checkbox, on by
default) holds the entire reframing stage at identity: zoom, `crop_margin`,
pan, rotation, lens distortion and the micro-warp all go to zero. The last
three are in that list because they are not crops themselves but leave
uncovered corners that only a crop hides, so keeping them would trade a crop
for a black wedge.

Read against the ablation above, that means the default output scores ~0 bits
of pHash distance — the mode is deliberately not trying to beat a perceptual
matcher. It still varies colour, grain, sharpening, the encode (quality,
entropy coding, PNG predictor), the output dimensions and the fabricated
metadata, which is what defeats byte-level and metadata dedup. Turning the
checkbox off restores the measured 16-18 bits at the cost of a visible crop.

`verify_quality.py` reflects the split: the uniqueness floors are measured
against a draw with `preserve_full_frame=False`, and `check_full_frame` asserts
the default mode's crop window is the whole source (2 px of slack for even
rounding).

### probe_file reports display geometry, not stored geometry

`probe_file` swaps width and height when the source carries a quarter-turn
rotation, because ffmpeg auto-rotates on decode and the filter graph therefore
sees the rotated frame. A portrait phone photo stores landscape pixels plus an
EXIF orientation; a phone video stores a display matrix. Planning the crop
against the stored pair made ffmpeg reject the graph outright (`Invalid too big
or non positive size for width ...`), so **every rotated input failed, on both
pipelines**. The rotation is *frame* side data for JPEG rather than stream side
data, which is why the probe decodes one frame (`-read_intervals %+#1`).

### Lossless output pays for detail that lossy output does not

`image_engine` zeroes grain and the vignette when the output is PNG. Measured
against an otherwise identical encode: on a flat graphic, grain cost **56x** the
file size and the vignette **19x**; on a photograph both were about 1.0x.
Sharpening and the whole colour stage stayed within 1.13x everywhere. Since the
ablation puts both effects' contribution to the perceptual hash at zero, on the
lossless branch they are pure cost — and PNG output is precisely the
graphics-and-logos case where flat colour makes the cost worst.

When measuring this, pin the twin's codec settings. Comparing a
`compression_level 9` output against a `level 6` twin swings the ratio by more
than 2x on its own and reads as an effects problem that is not there.

### ffmpeg supervision

`process_single_video._run` does not police ffmpeg from its stdout loop, because that loop blocks on ffmpeg's output and a hung ffmpeg never reaches it. A watchdog thread handles Cancel, the stall timeout (`_FFMPEG_STALL_TIMEOUT`: `out_time` not advancing) and the deadline (30x clip duration, at least an hour). A slow encode keeps advancing `out_time`, so do not replace the stall check with a tighter fixed deadline.

A full output disk returns `DISK_FULL_ERROR`, and both batch functions then stop starting new files instead of failing each one after writing a partial file.

### Perceptual safety caps (do not exceed)

`MAX_*` and `SPEECH_SAFE_*` constants in `engine.py` bound audio delay, rotation angle, hue rotation, and the speech-safe speed/pitch bands. `MAX_IMAGE_*` in `image_engine.py` does the same for stills, and sits **lower** than the video equivalents: a photo gets no temporal averaging and the viewer can zoom, so grain and sharpening that vanish in motion are plainly visible on it. The UI spin boxes are bounded by these same constants — a visible or audible setting must not be dialable. When changing a range, update the constant and the UI bound together, then re-run the quality gate.

### Anti-fingerprinting

Outputs strip encoder identity: `SEI_STRIP_BSF` removes SEI NAL units; metadata is rewritten from `DEVICE_PROFILES` / `GPS_LOCATIONS` with camera-style fields. `verify_quality.py` asserts no `x264`/`Lavf`/`Lavc` strings survive. Preserve this when touching metadata or encode args.

Stills go further, because a JPEG's non-pixel surface is bigger than a video's: the JFIF APP0 ffmpeg emits is dropped rather than kept (phone cameras write an Exif APP1 and no JFIF, so carrying both is a tell), the `Software` field carries a plausible per-make build id rather than a bare OS version, and shutter/aperture are written consistently in both their plain and APEX log forms. Note that Meta strips EXIF on upload — this work is for the file as it sits on disk and in whatever passes it around before the upload, not for what survives inside the ad.

## Packaging notes

- `VideoUniqualizer.spec` is the single source for bundle contents, excludes and Info.plist; `build.sh` runs it and only supplies computed values (`UNIQ_BUILD_NUMBER` = git commit count, `UNIQ_MIN_MACOS`).
- **The bundled ffmpeg must be self-contained.** A Homebrew ffmpeg is a stub over `/opt/homebrew/Cellar/*/lib`; copied alone it works on the build Mac and fails on every other one, and a plain smoke launch cannot tell. `tools/bundle_ffmpeg.py bundle` copies the dylib closure into `ffmpeg_bin/lib` relinked to `@loader_path`; the spec passes ffmpeg/ffprobe as `binaries`, so PyInstaller moves those libs into `Contents/Frameworks` under `@rpath`. `build.sh` then checks twice: `bundle_ffmpeg.py verify --boundary <app>` (every dependency resolves inside the app) and a run under `DYLD_PRINT_LIBRARIES` (dyld loads nothing from outside). Do not weaken either.
- **Prefer CI builds for testers.** The floor comes from the Homebrew bottles on the build machine: the `macos-15` runner produces a macOS 15+ app, a Mac on a newer OS produces a newer floor (26 at the time of writing). The CI release is the widest-compatible build.
- `LSMinimumSystemVersion` is the highest `minos` among the bundled ffmpeg Mach-Os. Homebrew bottles target the OS they were built on, so that is the real floor for testers; lowering it needs an ffmpeg built with a lower deployment target, not a plist edit.
- The version lives only in `src/version.py`. `./build.sh share` refuses a dirty tree (`ALLOW_DIRTY=1` overrides for throwaway builds).
- Third-party licenses: `bundle_ffmpeg.py` copies each keg's license files and writes `THIRD_PARTY_NOTICES.txt` into `ffmpeg_bin/licenses`, shipped as `Contents/Resources/licenses` (Help → Third-Party Licenses). The Homebrew ffmpeg is `--enable-gpl --enable-version3`, so distributing the app means distributing GPLv3 binaries; `build.sh` fails if the notices are missing.
- **Feedback loop.** `applog.start_session()` arms `faulthandler` (fatal-signal stack traces to `crash.log`) and a `session.lock` marker removed by `atexit`; a marker found at launch means the last session crashed or was force-quit, and the app offers **Help → Report a Problem**, which zips description, settings, crash log and (opt-in) app logs to the Desktop. Nothing is uploaded. `UNIQ_SMOKE_TEST=1` (set by `build.sh`'s smoke launch, which is killed) skips the marker and the first-launch prompts — keep it that way or every build leaves the builder a false crash prompt.
- **Updates.** `updates.py` reads this repo's public GitHub Releases via `/usr/bin/curl` (the frozen app has no CA bundle for Python's SSL), falling back to `releases.atom` when the anonymous API limit (60/h per IP) answers 403/429. Pre-release testers are offered newer pre-releases; the daily check runs only after the tester agreed once.
- **Releasing.** Bump `src/version.py`, rename CHANGELOG's *Unreleased* to the version, commit, then push the tag **on its own** (`git push origin vX`) — GitHub did not start the tag workflow when the tag went up in the same push as the branch. CI checks tag = version and that the CHANGELOG section exists, builds, and publishes a GitHub release (pre-release for hyphenated tags) with the DMG.
- **Developer ID / notarization** is wired but unverified (no certificate on the build Mac): `DEVELOPER_ID="Developer ID Application: … (TEAMID)" NOTARY_PROFILE=<keychain profile> ./build.sh share` signs inside-out with the hardened runtime and `packaging/entitlements.plist`, then notarizes and staples the DMG. Test a first launch on a clean Mac before relying on it.
- `src/applog.py` writes `~/Library/Logs/Video Uniqualizer/app.log` (rotating). The engines log ffmpeg failures with the full command and stderr tail; the UI only shows 500 characters. Help → Copy Diagnostics is what testers paste into bug reports.
- Apple Silicon requires arm64 (or universal) ffmpeg/ffprobe in `ffmpeg_bin/`; x86_64-only binaries fail the build rather than producing a Rosetta build. Check with `lipo -archs ffmpeg_bin/ffmpeg`.
- Ad-hoc code signing is mandatory on Apple Silicon (`codesign --force --deep --sign -`) — without it the app crashes on launch.
- The app is not notarized; Gatekeeper blocks first launch on other Macs. Tester steps live in `TESTING_ON_MAC.md`.
