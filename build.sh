#!/bin/bash
# ============================================================
#   Build Video Uniqualizer.app for macOS
#   Modes:
#     ./build.sh        -> local development build
#     ./build.sh share  -> internal sharing build for testers
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="Video Uniqualizer"
MODE="${1:-local}"

SRC_DIR="$SCRIPT_DIR/src"
BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist"
FFMPEG_DIR="$SCRIPT_DIR/ffmpeg_bin"
VENV_DIR="$SCRIPT_DIR/.venv"

APP_DIR="$DIST_DIR/$APP_NAME.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
DMG_PATH="$DIST_DIR/$APP_NAME.dmg"
CHECKSUM_PATH="$DIST_DIR/$APP_NAME.dmg.sha256"
RELEASE_NOTES_PATH="$DIST_DIR/RELEASE_NOTES.txt"
TESTING_GUIDE_PATH="$DIST_DIR/TESTING_ON_MAC.md"
SMOKE_LOG="$BUILD_DIR/smoke-test.log"
HOST_ARCH="$(uname -m)"

print_usage() {
    echo "Usage: ./build.sh [local|share]"
}

fail() {
    echo ""
    echo "ERROR: $1" >&2
    exit 1
}

ensure_supported_mode() {
    case "$MODE" in
        local|share)
            ;;
        *)
            print_usage
            fail "Unsupported build mode: $MODE"
            ;;
    esac
}

cleanup_previous_outputs() {
    rm -rf "$DIST_DIR"
    rm -f "$DMG_PATH" "$CHECKSUM_PATH" "$RELEASE_NOTES_PATH" "$TESTING_GUIDE_PATH" "$SMOKE_LOG"
}

binary_archs() {
    local path="$1"
    if [ ! -f "$path" ]; then
        return 1
    fi
    lipo -archs "$path" 2>/dev/null || file "$path" 2>/dev/null
}

binary_supports_host_arch() {
    local path="$1"
    local archs
    archs="$(binary_archs "$path" 2>/dev/null || true)"
    case "$archs" in
        *"$HOST_ARCH"*) return 0 ;;
        *"universal"*) return 0 ;;
        *) return 1 ;;
    esac
}

copy_native_ffmpeg_from_path() {
    local ffmpeg_path ffprobe_path
    ffmpeg_path="$(command -v ffmpeg || true)"
    ffprobe_path="$(command -v ffprobe || true)"

    if [ -z "$ffmpeg_path" ] || [ -z "$ffprobe_path" ]; then
        return 1
    fi
    if ! binary_supports_host_arch "$ffmpeg_path" || ! binary_supports_host_arch "$ffprobe_path"; then
        return 1
    fi

    mkdir -p "$FFMPEG_DIR"
    cp "$ffmpeg_path" "$FFMPEG_DIR/ffmpeg"
    cp "$ffprobe_path" "$FFMPEG_DIR/ffprobe"
    chmod +x "$FFMPEG_DIR/ffmpeg" "$FFMPEG_DIR/ffprobe"
    return 0
}

setup_python_env() {
    echo ""
    echo "[1/7] Setting up Python virtual environment..."

    if [ ! -d "$VENV_DIR" ]; then
        echo "  Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi

    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"

    echo "  Installing dependencies..."
    pip install --quiet --upgrade pip
    pip install --quiet PyQt6 pyinstaller

    echo "  Python: $(python3 --version)"
    echo "  pip packages installed."
}

ensure_ffmpeg() {
    echo ""
    echo "[2/7] Checking ffmpeg binaries..."
    echo "  Host architecture: $HOST_ARCH"

    if [ -f "$FFMPEG_DIR/ffmpeg" ] && [ -f "$FFMPEG_DIR/ffprobe" ]; then
        if binary_supports_host_arch "$FFMPEG_DIR/ffmpeg" && binary_supports_host_arch "$FFMPEG_DIR/ffprobe"; then
            echo "  ffmpeg binaries already present and native-compatible."
        else
            echo "  Existing ffmpeg binaries are not native-compatible for $HOST_ARCH."
            rm -f "$FFMPEG_DIR/ffmpeg" "$FFMPEG_DIR/ffprobe"
        fi
    fi

    if [ ! -f "$FFMPEG_DIR/ffmpeg" ] || [ ! -f "$FFMPEG_DIR/ffprobe" ]; then
        echo "  Looking for native ffmpeg/ffprobe in PATH..."
        if copy_native_ffmpeg_from_path; then
            echo "  Copied native ffmpeg binaries from PATH."
        fi
    fi

    if [ ! -f "$FFMPEG_DIR/ffmpeg" ] || [ ! -f "$FFMPEG_DIR/ffprobe" ]; then
        echo "  Downloading ffmpeg static build for macOS..."
        mkdir -p "$FFMPEG_DIR"

        if [ "$HOST_ARCH" = "arm64" ]; then
            echo "  Detected Apple Silicon (arm64)"
        else
            echo "  Detected Intel (x86_64)"
        fi

        ffmpeg_url="https://evermeet.cx/ffmpeg/ffmpeg-7.1.1.zip"
        ffprobe_url="https://evermeet.cx/ffmpeg/ffprobe-7.1.1.zip"

        if [ ! -f "$FFMPEG_DIR/ffmpeg" ]; then
            echo "  Downloading ffmpeg..."
            curl -L -o "$FFMPEG_DIR/ffmpeg.zip" "$ffmpeg_url"
            unzip -o "$FFMPEG_DIR/ffmpeg.zip" -d "$FFMPEG_DIR/"
            rm -f "$FFMPEG_DIR/ffmpeg.zip"
            chmod +x "$FFMPEG_DIR/ffmpeg"
        fi

        if [ ! -f "$FFMPEG_DIR/ffprobe" ]; then
            echo "  Downloading ffprobe..."
            curl -L -o "$FFMPEG_DIR/ffprobe.zip" "$ffprobe_url"
            unzip -o "$FFMPEG_DIR/ffprobe.zip" -d "$FFMPEG_DIR/"
            rm -f "$FFMPEG_DIR/ffprobe.zip"
            chmod +x "$FFMPEG_DIR/ffprobe"
        fi
    fi

    if ! binary_supports_host_arch "$FFMPEG_DIR/ffmpeg" || ! binary_supports_host_arch "$FFMPEG_DIR/ffprobe"; then
        echo "  ffmpeg archs:  $(binary_archs "$FFMPEG_DIR/ffmpeg" || true)"
        echo "  ffprobe archs: $(binary_archs "$FFMPEG_DIR/ffprobe" || true)"
        fail "Bundled ffmpeg must support host architecture '$HOST_ARCH'. Install native ffmpeg with Homebrew or provide universal binaries in ffmpeg_bin/."
    fi

    echo "  ffmpeg archs:  $(binary_archs "$FFMPEG_DIR/ffmpeg" || true)"
    echo "  ffprobe archs: $(binary_archs "$FFMPEG_DIR/ffprobe" || true)"

    echo "  ffmpeg: $("$FFMPEG_DIR/ffmpeg" -version 2>&1 | head -1)" || echo "  WARNING: Could not verify ffmpeg"
    echo "  ffprobe: $("$FFMPEG_DIR/ffprobe" -version 2>&1 | head -1)" || echo "  WARNING: Could not verify ffprobe"
    echo "  VideoToolbox encoders:"
    "$FFMPEG_DIR/ffmpeg" -hide_banner -encoders 2>/dev/null | grep -E 'h264_videotoolbox|hevc_videotoolbox' | sed 's/^/    /' || echo "    WARNING: VideoToolbox encoders not found"
}

generate_icon() {
    echo ""
    echo "[3/7] Generating app icon..."

    mkdir -p "$BUILD_DIR"
    python3 "$SRC_DIR/gen_icns.py"
}

build_app() {
    echo ""
    echo "[4/7] Building .app with PyInstaller..."

    chmod -R u+w ~/Library/Application\ Support/pyinstaller/ 2>/dev/null || true
    rm -rf ~/Library/Application\ Support/pyinstaller/ 2>/dev/null || true

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
}

optimize_bundle() {
    echo ""
    echo "[5/7] Post-build cleanup..."

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
    for plugin_dir in \
        "$APP_DIR"/Contents/Frameworks/PyQt6/Qt6/plugins/* \
        "$APP_DIR"/Contents/Resources/PyQt6/Qt6/plugins/* \
        "$APP_DIR"/Contents/MacOS/PyQt6/Qt6/plugins/* \
        "$APP_DIR"/Contents/MacOS/PyQt6/Qt/plugins/*; do
        if [ -d "$plugin_dir" ]; then
            dirname="$(basename "$plugin_dir")"
            case "$dirname" in
                platforms|styles|imageformats)
                    ;;
                *)
                    echo "    Removing plugin: $dirname"
                    rm -rf "$plugin_dir"
                    ;;
            esac
        fi
    done

    echo "  Pruning unused Qt image format plugins..."
    find "$APP_DIR" -path '*/PyQt6/Qt6/plugins/imageformats/*' -type f | while IFS= read -r plugin; do
        case "$(basename "$plugin")" in
            libqicns.dylib|libqico.dylib)
                ;;
            *)
                echo "    Removing image plugin: $(basename "$plugin")"
                rm -f "$plugin"
                ;;
        esac
    done

    echo "  Pruning unused Qt platform plugins..."
    find "$APP_DIR" -path '*/PyQt6/Qt6/plugins/platforms/*' -type f | while IFS= read -r plugin; do
        case "$(basename "$plugin")" in
            libqcocoa.dylib)
                ;;
            *)
                echo "    Removing platform plugin: $(basename "$plugin")"
                rm -f "$plugin"
                ;;
        esac
    done

    echo "  Keeping Qt frameworks intact..."

    if [ -d "$DIST_DIR/$APP_NAME" ]; then
        echo "  Removing extra PyInstaller folder output..."
        rm -rf "$DIST_DIR/$APP_NAME"
    fi
}

sign_bundle() {
    echo ""
    echo "[6/7] Code signing (ad-hoc)..."

    echo "  Signing .so files..."
    find "$APP_DIR" -name '*.so' -exec codesign --force --sign - {} \; 2>/dev/null || true

    echo "  Signing .dylib files..."
    find "$APP_DIR" -name '*.dylib' -exec codesign --force --sign - {} \; 2>/dev/null || true

    echo "  Signing frameworks..."
    find "$APP_DIR" -type d -name '*.framework' | while IFS= read -r framework; do
        codesign --force --sign - "$framework" 2>/dev/null || true
    done

    echo "  Signing main executable..."
    codesign --force --sign - "$MACOS_DIR/$APP_NAME" 2>/dev/null || true

    echo "  Signing bundled ffmpeg binaries..."
    find "$APP_DIR" -path '*/ffmpeg/ffmpeg' -type f -exec codesign --force --sign - {} \; 2>/dev/null || true
    find "$APP_DIR" -path '*/ffmpeg/ffprobe' -type f -exec codesign --force --sign - {} \; 2>/dev/null || true

    echo "  Signing the entire .app bundle..."
    codesign --force --deep --sign - "$APP_DIR"
}

verify_codesign() {
    echo "  Verifying code signature..."
    codesign --verify --deep --strict "$APP_DIR"
    echo "  ✓ Signature valid"
}

check_broken_symlinks() {
    echo "  Checking for broken symlinks..."

    local broken_links
    broken_links="$(find "$APP_DIR" -type l ! -exec test -e {} \; -print)"
    if [ -n "$broken_links" ]; then
        echo "$broken_links"
        fail "Found broken symlinks inside the app bundle"
    fi

    echo "  ✓ No broken symlinks found"
}

smoke_test_app() {
    echo "  Running smoke launch test..."

    mkdir -p "$BUILD_DIR"
    rm -f "$SMOKE_LOG"

    "$MACOS_DIR/$APP_NAME" >"$SMOKE_LOG" 2>&1 &
    local app_pid=$!
    sleep 3

    if ! kill -0 "$app_pid" 2>/dev/null; then
        echo "  Smoke log:"
        cat "$SMOKE_LOG" 2>/dev/null || true
        fail "App failed to stay running during smoke launch test"
    fi

    kill "$app_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true

    echo "  ✓ App launched successfully"
    if [ -s "$SMOKE_LOG" ]; then
        echo "  Smoke log saved to $SMOKE_LOG"
    fi
}

check_spctl_status() {
    echo "  Checking Gatekeeper status (informational)..."

    local spctl_output
    if spctl_output="$(spctl --assess --type execute --verbose=4 "$APP_DIR" 2>&1)"; then
        echo "  ✓ Gatekeeper assessment passed"
    else
        echo "  WARNING: Gatekeeper rejected this build."
        echo "  This is expected for internal distribution without Developer ID notarization."
        echo "$spctl_output" | sed 's/^/    /'
    fi
}

run_validations() {
    echo ""
    echo "[7/7] Validating bundle..."
    verify_codesign
    check_broken_symlinks
    smoke_test_app
    check_spctl_status
}

create_dmg() {
    echo ""
    echo "[share] Creating DMG..."

    rm -f "$DMG_PATH"
    hdiutil create \
        -volname "$APP_NAME" \
        -srcfolder "$APP_DIR" \
        -ov -format UDZO \
        "$DMG_PATH"
}

create_checksum() {
    echo "[share] Writing SHA-256 checksum..."
    (
        cd "$DIST_DIR"
        shasum -a 256 "$APP_NAME.dmg" > "$APP_NAME.dmg.sha256"
    )
}

create_release_notes() {
    echo "[share] Writing release notes..."

    local build_date
    build_date="$(date '+%Y-%m-%d %H:%M:%S %Z')"

    cat > "$RELEASE_NOTES_PATH" <<EOF
Video Uniqualizer - Internal Test Build
Build date: $build_date

Included artifacts:
- $APP_NAME.app
- $APP_NAME.dmg
- $APP_NAME.dmg.sha256

Checksum verification:
shasum -a 256 "$APP_NAME.dmg"

Expected first-run behavior on another Mac:
- The app bundle is valid and self-contained.
- The app is not notarized yet.
- macOS Gatekeeper may block the first launch until the tester manually confirms it.

Tester install steps:
1. Open $APP_NAME.dmg
2. Drag $APP_NAME.app into Applications
3. Open Applications
4. Right click the app and choose Open
5. If macOS still blocks it, open System Settings -> Privacy & Security and use Open Anyway

Support note:
- Do not disable Gatekeeper globally.
- Do not change SIP.
- If launch still fails, send back the exact macOS warning dialog and the build artifacts you received.
EOF
}

copy_testing_guide() {
    echo "[share] Copying tester instructions..."
    cp "$SCRIPT_DIR/TESTING_ON_MAC.md" "$TESTING_GUIDE_PATH"
}

print_summary() {
    local app_size
    app_size="$(du -sh "$APP_DIR" | cut -f1)"

    echo ""
    echo "============================================"
    echo "  BUILD COMPLETE"
    echo "============================================"
    echo ""
    echo "  Mode: $MODE"
    echo "  App:  $APP_DIR ($app_size)"

    if [ "$MODE" = "share" ]; then
        local dmg_size
        dmg_size="$(du -sh "$DMG_PATH" | cut -f1)"
        echo "  DMG:  $DMG_PATH ($dmg_size)"
        echo "  SHA:  $CHECKSUM_PATH"
        echo "  Notes: $RELEASE_NOTES_PATH"
        echo "  Guide: $TESTING_GUIDE_PATH"
        echo ""
        echo "  Send testers the DMG, checksum file, and testing instructions."
        echo "  Gatekeeper approval on other Macs is not expected until notarization is added."
    else
        echo ""
        echo "  Local build completed. Use './build.sh share' to package it for testers."
    fi

    echo ""
}

main() {
    ensure_supported_mode

    echo "============================================"
    echo "  Building $APP_NAME ($MODE mode)"
    echo "============================================"

    cleanup_previous_outputs
    setup_python_env
    ensure_ffmpeg
    generate_icon
    build_app
    optimize_bundle
    sign_bundle
    run_validations

    if [ "$MODE" = "share" ]; then
        create_dmg
        create_checksum
        create_release_notes
        copy_testing_guide
    fi

    deactivate
    print_summary
}

main "$@"
