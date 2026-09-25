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
BUNDLE_TOOL="$SCRIPT_DIR/tools/bundle_ffmpeg.py"
APP_VERSION="$(cd "$SRC_DIR" && python3 -c 'from version import DISPLAY_VERSION; print(DISPLAY_VERSION)')"
BUILD_NUMBER="$(git -C "$SCRIPT_DIR" rev-list --count HEAD 2>/dev/null || echo 1)"
BUILD_COMMIT="$(git -C "$SCRIPT_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
MIN_MACOS=""
DIRTY_NOTE=""
if [ -n "$(git -C "$SCRIPT_DIR" status --porcelain 2>/dev/null)" ]; then
    DIRTY_NOTE=", uncommitted changes"
fi

print_usage() {
    echo "Usage: ./build.sh [local|share]"
}

fail() {
    echo ""
    echo "ERROR: $1" >&2
    exit 1
}

# A tester build has to be reproducible from a commit, or a bug report cannot
# be traced back to the code that produced it.
ensure_clean_tree_for_share() {
    [ "$MODE" = "share" ] || return 0
    [ -z "$DIRTY_NOTE" ] && return 0
    if [ "${ALLOW_DIRTY:-0}" = "1" ]; then
        echo "  WARNING: building a share build from uncommitted changes (ALLOW_DIRTY=1)."
        return 0
    fi
    git -C "$SCRIPT_DIR" status --short
    fail "Share builds must come from a committed tree. Commit (and tag) first, or set ALLOW_DIRTY=1 for a throwaway build."
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

ffmpeg_bin_is_self_contained() {
    [ -f "$FFMPEG_DIR/ffmpeg" ] && [ -f "$FFMPEG_DIR/ffprobe" ] || return 1
    python3 "$BUNDLE_TOOL" verify "$FFMPEG_DIR" --require-executables >/dev/null 2>&1
}

# Copies ffmpeg/ffprobe from PATH (or FFMPEG_SOURCE_DIR) together with every
# non-system dylib they load, relinked to @loader_path. Copying only the two
# executables used to produce a bundle that worked on the build Mac and nowhere
# else, since a Homebrew ffmpeg is a stub over /opt/homebrew/Cellar/*/lib.
bundle_ffmpeg_from_host() {
    local ffmpeg_path ffprobe_path
    if [ -n "${FFMPEG_SOURCE_DIR:-}" ]; then
        ffmpeg_path="$FFMPEG_SOURCE_DIR/ffmpeg"
        ffprobe_path="$FFMPEG_SOURCE_DIR/ffprobe"
    else
        ffmpeg_path="$(command -v ffmpeg || true)"
        ffprobe_path="$(command -v ffprobe || true)"
    fi

    if [ -z "$ffmpeg_path" ] || [ -z "$ffprobe_path" ] \
        || [ ! -f "$ffmpeg_path" ] || [ ! -f "$ffprobe_path" ]; then
        return 1
    fi
    if ! binary_supports_host_arch "$ffmpeg_path" || ! binary_supports_host_arch "$ffprobe_path"; then
        echo "  $ffmpeg_path is not built for $HOST_ARCH."
        return 1
    fi

    echo "  Bundling $ffmpeg_path and its libraries..."
    rm -rf "$FFMPEG_DIR/lib"
    rm -f "$FFMPEG_DIR/ffmpeg" "$FFMPEG_DIR/ffprobe"
    python3 "$BUNDLE_TOOL" bundle --ffmpeg "$ffmpeg_path" --ffprobe "$ffprobe_path" --dest "$FFMPEG_DIR"
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

    if ffmpeg_bin_is_self_contained && binary_supports_host_arch "$FFMPEG_DIR/ffmpeg" \
        && binary_supports_host_arch "$FFMPEG_DIR/ffprobe"; then
        echo "  ffmpeg_bin/ is self-contained and native for $HOST_ARCH."
    else
        if [ -f "$FFMPEG_DIR/ffmpeg" ]; then
            echo "  ffmpeg_bin/ is not self-contained (or not native) — rebuilding it."
        fi
        bundle_ffmpeg_from_host || fail "No usable ffmpeg. Install it with 'brew install ffmpeg', set FFMPEG_SOURCE_DIR to a folder holding ffmpeg + ffprobe, or put self-contained (static) binaries in ffmpeg_bin/."
    fi

    # Guard against a hand-placed binary as well as a failed bundle.
    python3 "$BUNDLE_TOOL" verify "$FFMPEG_DIR" --require-executables \
        || fail "ffmpeg_bin/ still depends on libraries outside the bundle"

    MIN_MACOS="$(python3 "$BUNDLE_TOOL" verify "$FFMPEG_DIR" --print-minos)"
    MIN_MACOS="${MIN_MACOS:-12.0}"
    echo "  ffmpeg archs:  $(binary_archs "$FFMPEG_DIR/ffmpeg" || true)"
    echo "  ffprobe archs: $(binary_archs "$FFMPEG_DIR/ffprobe" || true)"
    echo "  Lowest macOS the bundled ffmpeg can run on: $MIN_MACOS"

    "$FFMPEG_DIR/ffmpeg" -hide_banner -version >/dev/null 2>&1 \
        || fail "Bundled ffmpeg does not run on this machine"
    "$FFMPEG_DIR/ffprobe" -hide_banner -version >/dev/null 2>&1 \
        || fail "Bundled ffprobe does not run on this machine"
    echo "  ffmpeg: $("$FFMPEG_DIR/ffmpeg" -version 2>&1 | head -1)"
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

    # The spec is the single source for bundle contents, excludes, version and
    # Info.plist; build.sh only supplies the values it has to compute.
    UNIQ_BUILD_NUMBER="$BUILD_NUMBER" UNIQ_MIN_MACOS="$MIN_MACOS" pyinstaller \
        --noconfirm \
        --clean \
        --distpath "$DIST_DIR" \
        --workpath "$BUILD_DIR/pyinstaller" \
        "$SCRIPT_DIR/VideoUniqualizer.spec"
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
    find "$APP_DIR" -path '*/ffmpeg/ffmpeg' -type f -exec codesign --force --sign - {} \;
    find "$APP_DIR" -path '*/ffmpeg/ffprobe' -type f -exec codesign --force --sign - {} \;

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

# The load-command check proves nothing points outside the bundle; running the
# bundled ffmpeg under DYLD_PRINT_LIBRARIES proves dyld agrees. Both are needed
# because the build Mac has every Homebrew library installed, so an unbundled
# ffmpeg would pass any test that only checks it runs.
check_bundled_ffmpeg() {
    echo "  Checking bundled ffmpeg is self-contained..."

    local ffmpeg_in_app ffmpeg_root
    ffmpeg_in_app="$(find "$APP_DIR" -path '*/ffmpeg/ffmpeg' -type f | head -1)"
    [ -n "$ffmpeg_in_app" ] || fail "ffmpeg is missing from the app bundle"
    ffmpeg_root="$(dirname "$ffmpeg_in_app")"

    python3 "$BUNDLE_TOOL" verify "$ffmpeg_root" --require-executables --boundary "$APP_DIR" \
        || fail "Bundled ffmpeg depends on libraries outside the app"

    local outside
    outside="$(DYLD_PRINT_LIBRARIES=1 "$ffmpeg_in_app" -hide_banner \
        -f lavfi -i testsrc=d=0.2:s=64x64 -c:v libx264 -f null - 2>&1 \
        | sed -nE 's/^dyld\[[0-9]+\]: <[^>]+> (\/.*)$/\1/p' \
        | grep -vE '^/(System|usr/lib)/' \
        | grep -vF "$APP_DIR" || true)"
    if [ -n "$outside" ]; then
        echo "$outside" | sed 's/^/    /'
        fail "Bundled ffmpeg loaded libraries from outside the app"
    fi
    echo "  ✓ ffmpeg loads only system and bundled libraries"
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
    check_bundled_ffmpeg
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
Video Uniqualizer $APP_VERSION (build $BUILD_NUMBER, commit $BUILD_COMMIT$DIRTY_NOTE)
Build date: $build_date
Requires: macOS $MIN_MACOS or later, $HOST_ARCH

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
- For any processing problem, use Help -> Copy Diagnostics in the app and paste the result into your report.
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
    ensure_clean_tree_for_share

    echo "============================================"
    echo "  Building $APP_NAME $APP_VERSION ($MODE mode, build $BUILD_NUMBER, $BUILD_COMMIT$DIRTY_NOTE)"
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
