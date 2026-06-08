# Video Uniqualizer

Standalone macOS application for video uniqualization based on HYBRID V9.0 engine.

## Features

- Select multiple video files for batch processing
- Choose output folder
- Adjustable parameters (geometry, color, effects, output settings)
- V9.0 defaults loaded as template on startup
- macOS performance profiles: Max Quality, Balanced, and Fast Mac
- VideoToolbox hardware encoding support for Apple Silicon and Intel Macs
- Built-in encoder benchmark for checking real Mac performance
- ffmpeg bundled inside the app — no external dependencies needed
- Skeuomorphic dark UI design

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
│   ├── icons.py          # Programmatically drawn icons
│   ├── styles.py         # Skeuomorphic Qt stylesheet
│   └── gen_icns.py       # macOS .icns icon generator
└── ffmpeg_bin/           # ffmpeg binaries (created during build)
```
