#!/bin/bash
# ============================================================
#   Build Video Uniqualizer.app for macOS — OPTIMIZED
#   Produces a minimal .app (~80-100 MB vs 239 MB)
# ============================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Video Uniqualizer"
SRC_DIR="$SCRIPT_DIR/src"
BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist"
FFMPEG_DIR="$SCRIPT_DIR/ffmpeg_bin"
VENV_DIR="$SCRIPT_DIR/.venv"

echo "============================================"
echo "  Building $APP_NAME (optimized)"
echo "============================================"

# ─── Step 1: Create venv & install dependencies ─────────
echo ""
echo "[1/6] Setting up Python virtual environment..."

if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate venv
source "$VENV_DIR/bin/activate"

echo "  Installing dependencies..."
pip install --quiet --upgrade pip
pip install --quiet PyQt6 pyinstaller

echo "  Python: $(python3 --version)"
echo "  pip packages installed."

# ─── Step 2: Download ffmpeg if not present ──────────────
echo ""
echo "[2/6] Checking ffmpeg binaries..."

if [ ! -f "$FFMPEG_DIR/ffmpeg" ] || [ ! -f "$FFMPEG_DIR/ffprobe" ]; then
    echo "  Downloading ffmpeg static build for macOS..."
    mkdir -p "$FFMPEG_DIR"

    # Detect architecture
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ]; then
        echo "  Detected Apple Silicon (arm64)"
    else
        echo "  Detected Intel (x86_64)"
    fi

    # evermeet.cx provides macOS universal static builds
    FFMPEG_URL="https://evermeet.cx/ffmpeg/ffmpeg-7.1.1.zip"
    FFPROBE_URL="https://evermeet.cx/ffmpeg/ffprobe-7.1.1.zip"

    # Download ffmpeg
    if [ ! -f "$FFMPEG_DIR/ffmpeg" ]; then
        echo "  Downloading ffmpeg..."
        curl -L -o "$FFMPEG_DIR/ffmpeg.zip" "$FFMPEG_URL"
        unzip -o "$FFMPEG_DIR/ffmpeg.zip" -d "$FFMPEG_DIR/"
        rm -f "$FFMPEG_DIR/ffmpeg.zip"
        chmod +x "$FFMPEG_DIR/ffmpeg"
    fi

    # Download ffprobe
    if [ ! -f "$FFMPEG_DIR/ffprobe" ]; then
        echo "  Downloading ffprobe..."
        curl -L -o "$FFMPEG_DIR/ffprobe.zip" "$FFPROBE_URL"
        unzip -o "$FFMPEG_DIR/ffprobe.zip" -d "$FFMPEG_DIR/"
        rm -f "$FFMPEG_DIR/ffprobe.zip"
        chmod +x "$FFMPEG_DIR/ffprobe"
    fi

    echo "  ffmpeg binaries ready."
else
    echo "  ffmpeg binaries already present."
fi

# Verify
echo "  ffmpeg: $("$FFMPEG_DIR/ffmpeg" -version 2>&1 | head -1)" || echo "  WARNING: Could not verify ffmpeg"
echo "  ffprobe: $("$FFMPEG_DIR/ffprobe" -version 2>&1 | head -1)" || echo "  WARNING: Could not verify ffprobe"

# ─── Step 3: Generate app icon (icns) ───────────────────
echo ""
echo "[3/6] Generating app icon..."

# Ensure build dir exists for icon
mkdir -p "$BUILD_DIR"
python3 "$SRC_DIR/gen_icns.py"

# ─── Step 4: Build with PyInstaller (OPTIMIZED) ─────────
echo ""
echo "[4/6] Building .app with PyInstaller (optimized)..."

# Clean previous builds and PyInstaller's binary cache (can have permission issues after upgrades)
rm -rf "$DIST_DIR"
chmod -R u+w ~/Library/Application\ Support/pyinstaller/ 2>/dev/null || true
rm -rf ~/Library/Application\ Support/pyinstaller/ 2>/dev/null || true

# ===========================================================
# KEY OPTIMIZATION: exclude all Qt/Python modules we don't use
# This is the main size reduction (saves ~80-100 MB)
# ===========================================================
pyinstaller \
    --name "$APP_NAME" \
    --windowed \
    --onedir \
    --icon "$BUILD_DIR/app_icon.icns" \
    --osx-bundle-identifier "com.uniqualizer.video" \
    --codesign-identity "-" \
    --add-data "$FFMPEG_DIR/ffmpeg:ffmpeg" \
    --add-data "$FFMPEG_DIR/ffprobe:ffmpeg" \
    --add-data "$SRC_DIR/engine.py:." \
    --add-data "$SRC_DIR/icons.py:." \
    --add-data "$SRC_DIR/styles.py:." \
    --hidden-import PyQt6.sip \
    --hidden-import PyQt6.QtCore \
    --hidden-import PyQt6.QtGui \
    --hidden-import PyQt6.QtWidgets \
    \
    --exclude-module PyQt6.QtNetwork \
    --exclude-module PyQt6.QtDBus \
    --exclude-module PyQt6.QtSvg \
    --exclude-module PyQt6.QtSvgWidgets \
    --exclude-module PyQt6.QtOpenGL \
    --exclude-module PyQt6.QtOpenGLWidgets \
    --exclude-module PyQt6.QtQml \
    --exclude-module PyQt6.QtQuick \
    --exclude-module PyQt6.QtQuickWidgets \
    --exclude-module PyQt6.QtQuick3D \
    --exclude-module PyQt6.QtDesigner \
    --exclude-module PyQt6.QtHelp \
    --exclude-module PyQt6.QtMultimedia \
    --exclude-module PyQt6.QtMultimediaWidgets \
    --exclude-module PyQt6.QtPdf \
    --exclude-module PyQt6.QtPdfWidgets \
    --exclude-module PyQt6.QtPositioning \
    --exclude-module PyQt6.QtBluetooth \
    --exclude-module PyQt6.QtNfc \
    --exclude-module PyQt6.QtWebChannel \
    --exclude-module PyQt6.QtWebEngineCore \
    --exclude-module PyQt6.QtWebEngineWidgets \
    --exclude-module PyQt6.QtWebSockets \
    --exclude-module PyQt6.QtRemoteObjects \
    --exclude-module PyQt6.QtSensors \
    --exclude-module PyQt6.QtSerialPort \
    --exclude-module PyQt6.QtSql \
    --exclude-module PyQt6.QtTest \
    --exclude-module PyQt6.QtXml \
    --exclude-module PyQt6.Qt3DCore \
    --exclude-module PyQt6.Qt3DRender \
    --exclude-module PyQt6.Qt3DInput \
    --exclude-module PyQt6.Qt3DLogic \
    --exclude-module PyQt6.Qt3DExtras \
    --exclude-module PyQt6.Qt3DAnimation \
    --exclude-module PyQt6.QtCharts \
    --exclude-module PyQt6.QtDataVisualization \
    --exclude-module PyQt6.QtStateMachine \
    --exclude-module PyQt6.QtTextToSpeech \
    --exclude-module PyQt6.QtVirtualKeyboard \
    --exclude-module PyQt6.QtHttpServer \
    --exclude-module PyQt6.QtSpatialAudio \
    \
    --exclude-module tkinter \
    --exclude-module _tkinter \
    --exclude-module sqlite3 \
    --exclude-module unittest \
    --exclude-module pydoc \
    --exclude-module doctest \
    --exclude-module xmlrpc \
    --exclude-module ftplib \
    --exclude-module imaplib \
    --exclude-module smtplib \
    --exclude-module nntplib \
    --exclude-module poplib \
    --exclude-module telnetlib \
    --exclude-module turtle \
    --exclude-module turtledemo \
    --exclude-module test \
    --exclude-module idlelib \
    --exclude-module lib2to3 \
    --exclude-module ensurepip \
    --exclude-module venv \
    --exclude-module distutils \
    --exclude-module setuptools \
    --exclude-module pip \
    --exclude-module pkg_resources \
    --exclude-module numpy \
    --exclude-module PIL \
    --exclude-module matplotlib \
    --exclude-module scipy \
    --exclude-module pandas \
    \
    --noconfirm \
    --clean \
    --distpath "$DIST_DIR" \
    --workpath "$BUILD_DIR/pyinstaller" \
    --specpath "$BUILD_DIR" \
    "$SRC_DIR/main.py"

# ─── Step 5: Post-build cleanup (strip + remove junk) ───
echo ""
echo "[5/6] Post-build size optimization..."

APP_DIR="$DIST_DIR/$APP_NAME.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
FRAMEWORKS_DIR="$APP_DIR/Contents/Frameworks"

# Find the internal directory (PyInstaller puts files here)
INTERNAL_DIR=""
if [ -d "$MACOS_DIR" ]; then
    INTERNAL_DIR="$MACOS_DIR"
fi

echo "  Stripping debug symbols from binaries..."
find "$APP_DIR" -name '*.so' -exec strip -x {} \; 2>/dev/null || true
find "$APP_DIR" -name '*.dylib' -exec strip -x {} \; 2>/dev/null || true

echo "  Removing __pycache__ directories..."
find "$APP_DIR" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

echo "  Removing .pyi stub files..."
find "$APP_DIR" -name '*.pyi' -delete 2>/dev/null || true

echo "  Removing translation files (*.qm)..."
find "$APP_DIR" -name '*.qm' -delete 2>/dev/null || true

echo "  Removing unused Qt plugins..."
# We only need: platforms, styles (maybe), imageformats (basic)
# Remove everything else
for plugin_dir in "$APP_DIR"/Contents/MacOS/PyQt6/Qt6/plugins/* \
                  "$APP_DIR"/Contents/Resources/PyQt6/Qt6/plugins/* \
                  "$APP_DIR"/Contents/MacOS/PyQt6/Qt/plugins/*; do
    if [ -d "$plugin_dir" ]; then
        dirname=$(basename "$plugin_dir")
        case "$dirname" in
            platforms|styles|imageformats)
                # Keep these
                ;;
            *)
                echo "    Removing plugin: $dirname"
                rm -rf "$plugin_dir"
                ;;
        esac
    fi
done

echo "  Removing unused Qt frameworks..."
# Remove Qt frameworks/dylibs we don't need
for fw in QtNetwork QtDBus QtSvg QtOpenGL QtQml QtQuick QtPdf \
          QtMultimedia QtWebEngine QtSql QtTest QtXml QtRemoteObjects \
          QtBluetooth QtNfc QtSensors QtSerialPort QtPositioning \
          QtWebChannel QtWebSockets Qt3D QtCharts QtDataVisualization \
          QtVirtualKeyboard QtTextToSpeech QtHttpServer QtSpatialAudio \
          QtQuick3D; do
    # Remove .framework directories
    find "$APP_DIR" -type d -name "${fw}.framework" -exec rm -rf {} + 2>/dev/null || true
    # Remove .dylib files
    find "$APP_DIR" -name "lib${fw}*" -delete 2>/dev/null || true
    find "$APP_DIR" -name "${fw}.abi3.so" -delete 2>/dev/null || true
    find "$APP_DIR" -name "${fw}.so" -delete 2>/dev/null || true
done

# ─── Step 5b: Re-sign all binaries (CRITICAL for Apple Silicon) ─
echo ""
echo "[5b/7] Code signing (ad-hoc) for Apple Silicon..."

# After stripping, all code signatures are invalid.
# macOS on ARM64 KILLS any unsigned Mach-O binary.
# We must re-sign every .so, .dylib, .framework, and the main executable.

echo "  Signing .so files..."
find "$APP_DIR" -name '*.so' -exec codesign --force --sign - {} \; 2>/dev/null || true

echo "  Signing .dylib files..."
find "$APP_DIR" -name '*.dylib' -exec codesign --force --sign - {} \; 2>/dev/null || true

echo "  Signing frameworks..."
find "$APP_DIR" -type d -name '*.framework' | while IFS= read -r fw; do
    codesign --force --sign - "$fw" 2>/dev/null || true
done

echo "  Signing main executable..."
codesign --force --sign - "$MACOS_DIR/$APP_NAME" 2>/dev/null || true

echo "  Signing ffmpeg binaries..."
codesign --force --sign - "$MACOS_DIR/ffmpeg/ffmpeg" 2>/dev/null || true
codesign --force --sign - "$MACOS_DIR/ffprobe/ffprobe" 2>/dev/null || true
# Also try alternate paths PyInstaller might use
find "$APP_DIR" -name 'ffmpeg' -type f -exec codesign --force --sign - {} \; 2>/dev/null || true
find "$APP_DIR" -name 'ffprobe' -type f -exec codesign --force --sign - {} \; 2>/dev/null || true

echo "  Signing the entire .app bundle..."
codesign --force --deep --sign - "$APP_DIR"

echo "  Verifying signature..."
codesign --verify --deep --strict "$APP_DIR" && echo "  ✓ Signature valid" || echo "  ✗ Signature verification failed!"

# Size report
echo ""
APP_SIZE=$(du -sh "$APP_DIR" | cut -f1)
echo "  App size after optimization: $APP_SIZE"

# ─── Step 6: Create DMG ─────────────────────────────────
echo ""
echo "[6/7] Creating DMG..."

DMG_PATH="$DIST_DIR/$APP_NAME.dmg"
rm -f "$DMG_PATH"

hdiutil create \
    -volname "$APP_NAME" \
    -srcfolder "$APP_DIR" \
    -ov -format UDZO \
    "$DMG_PATH"

DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)

# Deactivate venv
deactivate

echo ""
echo "============================================"
echo "  BUILD COMPLETE!"
echo "============================================"
echo ""
echo "  App:  $APP_DIR ($APP_SIZE)"
echo "  DMG:  $DMG_PATH ($DMG_SIZE)"
echo ""
echo "  Copy the .dmg or .app to any Mac — it will"
echo "  work without Python or ffmpeg installed."
echo ""
