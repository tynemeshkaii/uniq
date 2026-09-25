# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Video Uniqualizer — a standalone macOS PyQt6 desktop app (HYBRID V9.0 engine) that batch-processes **videos and still images** so every output is perceptually identical to its source but differs in per-file randomized parameters and metadata. ffmpeg/ffprobe are bundled inside the `.app`; the built bundle needs no Python or system ffmpeg.

Stills target Meta Ads uploads: the source aspect is preserved (Meta drives placement off the creative's own ratio) and the output is JPEG for JPEG/WEBP sources, PNG for PNG.

## Commands

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

There is no unit-test suite or linter. `verify_quality.py` is the correctness gate.

## Architecture

Source layers under `src/`:
- **`engine.py`** — pure processing core for video. No Qt. Ffmpeg orchestration, randomization, geometry math.
- **`image_engine.py`** — the still pipeline. Reuses `engine.plan_geometry` and `engine.build_spatial_chain` rather than owning a second copy of the filter graph.
- **`exif.py`** — EXIF APP1 and PNG `tEXt` writer, by hand, no piexif. ffmpeg's mjpeg encoder discards `-metadata`, so the camera identity is a post-encode pass.
- **`main.py`** — PyQt6 GUI + `ProcessWorker(QThread)`. The only place engine work is invoked off the UI thread.
- **`fingerprint.py`** — pHash and Haitsma-Kalker audio fingerprints, pure Python, no numpy. Dev-time only; nothing in `main.py` imports it, so it stays out of the bundle.
- **`styles.py` / `icons.py` / `gen_icns.py`** — skeuomorphic stylesheet, programmatically drawn Qt icons, and the macOS `.icns` generator (build-time).

### How the engine is wired (the key mental model)

- **`RandomRanges`** is the spec the UI owns: min/max per effect plus feature toggles. Built by `ParamsPanel.build_ranges()`.
- **`process_batch`** draws a **fresh `UniqueParams` per input file** from the ranges, copying only output-container settings (resolution, CRF, preset, encoder, overlay) from the UI template. So anything randomized varies per file, not per batch.
- **`plan_geometry(src_w, src_h, p)`** resolves rotation safety margin, micro-crop, zoom, and pan into a single crop rectangle in Python, emitting literal **even** numbers into the filter graph.
- **`_STATIC_FIELDS`** is the explicit list that decides copied-vs-randomized. A new `UniqueParams` field is randomized per file *unless* you add it there — so any new output-container setting must be added to that list or it will silently vary per file.
- **`build_video_chain` / `build_audio_chain`** are pure functions of `UniqueParams` + `GeometryPlan`. That purity is what lets `verify_quality.py` exercise the filter chain without encoding.
- **`process_batch` is concurrent.** A `ThreadPoolExecutor` runs `default_worker_count()` files at once (cores/4, clamped 1-3, since x264 does not scale past a few cores), and splits x264 threads across jobs. Progress callbacks are the mean across active jobs, so per-file progress is not monotonic. Shared counters are under `state_lock`; keep new batch state there.
- **`build_spatial_chain` is the shared half**, and it is itself `build_geometry_steps` + `build_effect_steps`. It is every filter that is a function of one frame plus the resolved geometry, and both pipelines call it — video wraps it in `setpts`/`fps`, images use it alone. The two halves are separately addressable because a still with an alpha channel has to run geometry on both the colour and the alpha branch while running effects on the colour branch only. A still passes `warp_drift_period=0`, which is what makes the corner-drift term collapse to a static `eval=init` perspective, and `scale_flags="lanczos"`, which video does not set so it keeps `bicubic`. **Refactoring this function means re-checking the video graph is byte-identical**, not just that the gate passes.
- **`ImageParams` is duck-typed against that chain.** `plan_geometry` and `build_spatial_chain` read fields off it by name, so renaming a field in either dataclass silently changes the other pipeline's filter graph.
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

### Perceptual safety caps (do not exceed)

`MAX_*` and `SPEECH_SAFE_*` constants in `engine.py` bound audio delay, rotation angle, hue rotation, and the speech-safe speed/pitch bands. `MAX_IMAGE_*` in `image_engine.py` does the same for stills, and sits **lower** than the video equivalents: a photo gets no temporal averaging and the viewer can zoom, so grain and sharpening that vanish in motion are plainly visible on it. The UI spin boxes are bounded by these same constants — a visible or audible setting must not be dialable. When changing a range, update the constant and the UI bound together, then re-run the quality gate.

### Anti-fingerprinting

Outputs strip encoder identity: `SEI_STRIP_BSF` removes SEI NAL units; metadata is rewritten from `DEVICE_PROFILES` / `GPS_LOCATIONS` with camera-style fields. `verify_quality.py` asserts no `x264`/`Lavf`/`Lavc` strings survive. Preserve this when touching metadata or encode args.

Stills go further, because a JPEG's non-pixel surface is bigger than a video's: the JFIF APP0 ffmpeg emits is dropped rather than kept (phone cameras write an Exif APP1 and no JFIF, so carrying both is a tell), the `Software` field carries a plausible per-make build id rather than a bare OS version, and shutter/aperture are written consistently in both their plain and APEX log forms. Note that Meta strips EXIF on upload — this work is for the file as it sits on disk and in whatever passes it around before the upload, not for what survives inside the ad.

## Packaging notes

- `VideoUniqualizer.spec` is the single source for bundle contents, excludes and Info.plist; `build.sh` runs it and only supplies computed values (`UNIQ_BUILD_NUMBER` = git commit count, `UNIQ_MIN_MACOS`).
- **The bundled ffmpeg must be self-contained.** A Homebrew ffmpeg is a stub over `/opt/homebrew/Cellar/*/lib`; copied alone it works on the build Mac and fails on every other one, and a plain smoke launch cannot tell. `tools/bundle_ffmpeg.py bundle` copies the dylib closure into `ffmpeg_bin/lib` relinked to `@loader_path`; the spec passes ffmpeg/ffprobe as `binaries`, so PyInstaller moves those libs into `Contents/Frameworks` under `@rpath`. `build.sh` then checks twice: `bundle_ffmpeg.py verify --boundary <app>` (every dependency resolves inside the app) and a run under `DYLD_PRINT_LIBRARIES` (dyld loads nothing from outside). Do not weaken either.
- `LSMinimumSystemVersion` is the highest `minos` among the bundled ffmpeg Mach-Os. Homebrew bottles target the OS they were built on, so that is the real floor for testers; lowering it needs an ffmpeg built with a lower deployment target, not a plist edit.
- The version lives only in `src/version.py`. `./build.sh share` refuses a dirty tree (`ALLOW_DIRTY=1` overrides for throwaway builds).
- `src/applog.py` writes `~/Library/Logs/Video Uniqualizer/app.log` (rotating). The engines log ffmpeg failures with the full command and stderr tail; the UI only shows 500 characters. Help → Copy Diagnostics is what testers paste into bug reports.
- Apple Silicon requires arm64 (or universal) ffmpeg/ffprobe in `ffmpeg_bin/`; x86_64-only binaries fail the build rather than producing a Rosetta build. Check with `lipo -archs ffmpeg_bin/ffmpeg`.
- Ad-hoc code signing is mandatory on Apple Silicon (`codesign --force --deep --sign -`) — without it the app crashes on launch.
- The app is not notarized; Gatekeeper blocks first launch on other Macs. Tester steps live in `TESTING_ON_MAC.md`.
