# Video Uniqualizer

Standalone macOS application for video and image uniqualization, based on the HYBRID V9.0 engine.

## Features

- Select multiple video **and image** files for batch processing — a mixed batch runs the stills first, then the video
- Choose output folder
- Adjustable parameters (geometry, color, effects, output settings)
- Every parameter is redrawn per file, so no two outputs share a setting
- JPEG and PNG output with fabricated camera EXIF, written directly (ffmpeg cannot author EXIF)
- Stills keep the source aspect ratio and resolution, so Meta Ads placement is unaffected
- Camera-style metadata with the encoder's own fingerprints stripped
- Parallel encoding of several files at once
- macOS performance profiles: Max Quality, Balanced, and Fast Mac
- VideoToolbox hardware encoding support for Apple Silicon and Intel Macs
- Built-in encoder benchmark for checking real Mac performance
- Built-in quality gate that measures both picture quality and how far the
  output has moved from the source's perceptual fingerprint (`src/verify_quality.py`)
- ffmpeg bundled inside the app — no external dependencies needed
- Skeuomorphic dark UI design

## Quality verification

The defaults are chosen so an output is indistinguishable from its source to a
viewer. To confirm that on your own footage:

```bash
python3 src/verify_quality.py /path/to/clip.mp4
```

It reports two opposing things. The quality side checks the clip against a
frame-aligned twin encoded with the same geometry but no colour/noise/sharpen/
vignette stage. The uniqueness side reports how far the output has moved from
the source under a perceptual hash and an audio fingerprint, labelling each as
evading or still matching a typical matcher — how far it moves depends heavily
on the footage, so run it on your own material rather than trusting a default.

The single biggest lever on that number is the zoom range, not any of the
filters: reframing is what a perceptual hash cannot ignore. It costs sharpness,
because the crop is scaled back up to the output size.

The quality comparison isolates
degradation from reframing, which a pixel metric would otherwise punish even
though it looks perfectly normal. Reported checks:

| Check | Meaning |
|---|---|
| SSIM / PSNR vs clean twin | how much the effects stage costs the picture |
| loudness shift, true peak | audio is neither crushed nor clipping |
| A/V start offset, duration drift | lip sync stays inside the perceptible threshold |
| uncovered edge pixels | no black wedges from rotation, lens or warp |
| encoder fingerprints | no `x264` / `Lavf` / `Lavc` strings left in the file |
| per-file randomisation | consecutive outputs really do differ |

It also writes two PNGs per clip — a full-frame source/output pair and a 1:1
detail crop — so the result can be judged by eye, which is the test that
actually matters. Green bar marks the source, red the output.

Run it with no arguments to check against a generated synthetic clip.

### Images

The same command takes a photo, and routes by extension:

```bash
python3 src/verify_quality.py /path/to/photo.jpg --keep /tmp/uniq-check
```

Everything that needs a timeline is dropped, and two checks are added:

| Check | Meaning |
|---|---|
| aspect preserved | the output ratio matches the source's, within an allowance that scales with the source size |
| camera metadata | the written EXIF names a real device profile and carries a capture date |
| alpha preserved | a transparent PNG comes out transparent, not composited onto black |

Transparency is handled rather than ignored: for PNG output the geometry runs on
the RGBA frame, the alpha plane is split off before the effect stage and merged
back afterwards, so a cut-out or logo keeps its transparency. Forcing JPEG on a
transparent source flattens it onto white, not ffmpeg's implicit black.

Rotated sources work too. A portrait phone photo stores landscape pixels plus an
EXIF orientation, and ffmpeg rotates on decode, so the pipeline plans its crop
against the rotated dimensions.

Measured on photographs, the picture is the same as for video, only starker:
reframing is the *only* thing that moves the perceptual hash. Rotation, lens
distortion, micro-warp, the tone curve, hue, eq, grain, sharpening, vignette
and the JPEG quality draw each measured zero in isolation; `zoom` measured 6,
12 and 20 bits at 1.02-1.05, 1.06-1.14 and 1.14-1.22, against a matcher
threshold near 10. The defaults land at 16-18, and a plain re-encode — the
control — scores 0. The zero-scoring stages stay because they defeat different
attacks (byte hashes, colour normalisation, a matcher that has solved
alignment), not because they move this number.

Note that Meta strips EXIF when a creative is uploaded. The metadata work is
for the file as it exists on disk, not for what survives inside the ad — the
pixel-side levers are what carry uniqueness through the upload.

## Building the .app

### Prerequisites

- macOS 10.15+
- Python 3.9+ (only needed for building, not for running the app)
- pip

### Quick Build

```bash
chmod +x build.sh
./build.sh
```

This will:
1. Install Python dependencies (PyQt6, PyInstaller)
2. Use native ffmpeg/ffprobe from PATH or download compatible binaries
3. Generate the app icon (.icns)
4. Package everything into `dist/Video Uniqualizer.app`

On Apple Silicon, `build.sh` requires bundled ffmpeg/ffprobe to support `arm64`.
If an existing `ffmpeg_bin/` contains x86_64-only binaries, the build will replace
them from PATH when possible or fail with a clear message instead of producing a
slow Rosetta-dependent build.

### Internal Share Build

To prepare a tester-friendly package:

```bash
./build.sh share
```

This mode:

1. Builds the app bundle
2. Verifies code signing and bundle integrity
3. Runs a smoke launch test
4. Creates `dist/Video Uniqualizer.dmg`
5. Creates `dist/Video Uniqualizer.dmg.sha256`
6. Creates `dist/RELEASE_NOTES.txt`

### Manual Build

If the automated script doesn't work, you can build step by step:

```bash
# 1. Install dependencies
pip3 install PyQt6 pyinstaller

# 2. Place native or universal ffmpeg/ffprobe binaries into ffmpeg_bin/
#    Homebrew ffmpeg is recommended on Apple Silicon.

# 3. Generate icon
python3 src/gen_icns.py

# 4. Build
pyinstaller VideoUniqualizer.spec
```

### Providing your own ffmpeg

If you already have ffmpeg installed or want a specific version:

```bash
mkdir -p ffmpeg_bin
cp $(which ffmpeg) ffmpeg_bin/
cp $(which ffprobe) ffmpeg_bin/
```

The binaries must match the target Mac architecture. Check with:

```bash
lipo -archs ffmpeg_bin/ffmpeg ffmpeg_bin/ffprobe
```

## Distribution

The built `Video Uniqualizer.app` in `dist/` is fully self-contained.
It runs locally without needing Python or ffmpeg installed.

For internal tester distribution, use:

```bash
./build.sh share
```

Important:

- the app is not notarized yet
- Gatekeeper may reject first launch on another Mac
- testers should follow the steps in `TESTING_ON_MAC.md`
- this workflow is suitable for internal testing, not public macOS release

## Development

Run the app directly (requires Python + PyQt6 + ffmpeg in PATH):

```bash
cd src
python3 main.py
```

Run the synthetic macOS encoder benchmark:

```bash
cd src
python3 main.py --benchmark
```

The benchmark compares available encoders such as `libx264`,
`h264_videotoolbox`, and `hevc_videotoolbox`.

## Project Structure

```
├── build.sh              # Automated build script
├── VideoUniqualizer.spec  # PyInstaller spec for manual builds
├── requirements.txt       # Python dependencies
├── src/
│   ├── main.py           # Application entry point + GUI
│   ├── engine.py         # Video processing engine (HYBRID V9.0)
│   ├── image_engine.py   # Still-image pipeline, sharing engine.py's filter graph
│   ├── exif.py           # EXIF / PNG metadata writer (no third-party dependency)
│   ├── verify_quality.py # Quality gate — run this after changing either engine
│   ├── fingerprint.py    # pHash + audio fingerprints used by the gate
│   ├── icons.py          # Programmatically drawn icons
│   ├── styles.py         # Skeuomorphic Qt stylesheet
│   └── gen_icns.py       # macOS .icns icon generator
└── ffmpeg_bin/           # ffmpeg binaries (created during build)
```

## How the engine is wired

`RandomRanges` is the spec the UI owns: min/max for every effect plus the
feature toggles. `process_batch` draws a fresh `UniqueParams` from it for each
input file, then copies only the output-container settings (resolution, CRF,
preset, encoder, overlay) from the template. Anything randomized therefore
varies per file rather than per batch.

`plan_geometry` resolves rotation safety margin, micro-crop, zoom and pan into
a single crop rectangle computed in Python, so the filter graph carries literal
even numbers. `build_video_chain` and `build_audio_chain` are pure functions of
those values, which is what makes the quality gate able to exercise the chain
without encoding a file.

Perceptual caps live in `engine.py` as `MAX_*` constants — audio delay, rotation
angle, hue rotation, and the speech-safe speed and pitch bands. The UI spin
boxes are bounded by the same constants, so a setting that would be visible or
audible cannot be dialled in.
