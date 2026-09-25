# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for Video Uniqualizer (OPTIMIZED).
Use this for manual builds: pyinstaller VideoUniqualizer.spec
"""

import os
import sys

block_cipher = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(SPEC))
SRC_DIR = os.path.join(SCRIPT_DIR, 'src')
FFMPEG_DIR = os.path.join(SCRIPT_DIR, 'ffmpeg_bin')

# Data files to include
datas = [
    (os.path.join(SRC_DIR, 'engine.py'), '.'),
    (os.path.join(SRC_DIR, 'image_engine.py'), '.'),
    (os.path.join(SRC_DIR, 'exif.py'), '.'),
    (os.path.join(SRC_DIR, 'icons.py'), '.'),
    (os.path.join(SRC_DIR, 'styles.py'), '.'),
    (os.path.join(SRC_DIR, 'version.py'), '.'),
    (os.path.join(SRC_DIR, 'applog.py'), '.'),
]
# fingerprint.py and verify_quality.py are deliberately absent: they are the
# dev-time measurement tools and nothing in main.py imports them, so they stay
# out of the bundle.

# ffmpeg/ffprobe arrive from tools/bundle_ffmpeg.py already relinked to their
# dylibs in ffmpeg_bin/lib. They go in as `binaries`, so PyInstaller follows
# those references itself: it collects the dylibs into Contents/Frameworks,
# relinks them to @rpath and signs them. Listing lib/ as data as well would
# ship every library twice. build.sh verifies the result stays inside the app.
binaries = []
for _tool in ('ffmpeg', 'ffprobe'):
    if os.path.isfile(os.path.join(FFMPEG_DIR, _tool)):
        binaries.append((os.path.join(FFMPEG_DIR, _tool), 'ffmpeg'))

# Version comes from src/version.py so the About text, the log header and
# Info.plist cannot disagree.
_version_ns = {}
with open(os.path.join(SRC_DIR, 'version.py')) as _f:
    exec(_f.read(), _version_ns)
APP_VERSION = _version_ns['VERSION']
# build.sh passes the git commit count; a manual spec build falls back to 1.
BUILD_NUMBER = os.environ.get('UNIQ_BUILD_NUMBER', '1')
# build.sh passes the highest deployment target among the bundled Mach-O
# files. Homebrew bottles target the macOS they were built on, so this is
# usually far above what the Python/Qt side needs — and it is the real floor.
MIN_MACOS = os.environ.get('UNIQ_MIN_MACOS', '12.0')

# Icon file
icon_file = os.path.join(SCRIPT_DIR, 'build', 'app_icon.icns')
if not os.path.isfile(icon_file):
    icon_file = None

# ─── Massive exclude list to cut ~100 MB of bloat ───────
EXCLUDES = [
    # Qt modules we don't use (biggest savings)
    'PyQt6.QtNetwork',
    'PyQt6.QtDBus',
    'PyQt6.QtSvg',
    'PyQt6.QtSvgWidgets',
    'PyQt6.QtOpenGL',
    'PyQt6.QtOpenGLWidgets',
    'PyQt6.QtQml',
    'PyQt6.QtQuick',
    'PyQt6.QtQuickWidgets',
    'PyQt6.QtQuick3D',
    'PyQt6.QtDesigner',
    'PyQt6.QtHelp',
    'PyQt6.QtMultimedia',
    'PyQt6.QtMultimediaWidgets',
    'PyQt6.QtPdf',
    'PyQt6.QtPdfWidgets',
    'PyQt6.QtPositioning',
    'PyQt6.QtBluetooth',
    'PyQt6.QtNfc',
    'PyQt6.QtWebChannel',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebSockets',
    'PyQt6.QtRemoteObjects',
    'PyQt6.QtSensors',
    'PyQt6.QtSerialPort',
    'PyQt6.QtSql',
    'PyQt6.QtTest',
    'PyQt6.QtXml',
    'PyQt6.Qt3DCore',
    'PyQt6.Qt3DRender',
    'PyQt6.Qt3DInput',
    'PyQt6.Qt3DLogic',
    'PyQt6.Qt3DExtras',
    'PyQt6.Qt3DAnimation',
    'PyQt6.QtCharts',
    'PyQt6.QtDataVisualization',
    'PyQt6.QtStateMachine',
    'PyQt6.QtTextToSpeech',
    'PyQt6.QtVirtualKeyboard',
    'PyQt6.QtHttpServer',
    'PyQt6.QtSpatialAudio',
    # Python stdlib we don't use
    'tkinter', '_tkinter',
    'sqlite3',
    'unittest', 'pydoc', 'doctest',
    'xmlrpc', 'ftplib', 'imaplib', 'smtplib', 'nntplib', 'poplib', 'telnetlib',
    'turtle', 'turtledemo',
    'test', 'idlelib', 'lib2to3',
    'ensurepip', 'venv', 'distutils',
    'setuptools', 'pip', 'pkg_resources',
    # Large third-party (in case they sneak in)
    'numpy', 'PIL', 'matplotlib', 'scipy', 'pandas',
]

a = Analysis(
    [os.path.join(SRC_DIR, 'main.py')],
    pathex=[SRC_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        'PyQt6.sip',
        'PyQt6.QtCore',
        'PyQt6.QtGui',
        'PyQt6.QtWidgets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Video Uniqualizer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,          # build.sh strips after collection, then re-signs
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity='-',     # Ad-hoc sign (required for Apple Silicon)
    entitlements_file=None,
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Video Uniqualizer',
)

app = BUNDLE(
    coll,
    name='Video Uniqualizer.app',
    icon=icon_file,
    bundle_identifier='com.uniqualizer.video',
    info_plist={
        'NSHighResolutionCapable': True,
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': BUILD_NUMBER,
        'NSHumanReadableCopyright': 'Video Uniqualizer',
        'LSMinimumSystemVersion': MIN_MACOS,
    },
)
