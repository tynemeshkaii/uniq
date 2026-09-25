"""
Video Uniqualizer — macOS Application
Main window with skeuomorphic design.
"""

import sys
import os
import logging
import shutil
import subprocess
import time
import traceback
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QFileDialog,
    QProgressBar, QGroupBox, QDoubleSpinBox, QSpinBox, QCheckBox,
    QComboBox, QTabWidget, QScrollArea, QFrame, QSizePolicy,
    QAbstractItemView, QGridLayout, QMessageBox, QInputDialog
)
from PyQt6.QtCore import Qt, QThread, QObject, QTimer, pyqtSignal, QSize, QPropertyAnimation, QEasingCurve, QUrl, QMimeData
from PyQt6.QtGui import QAction, QCloseEvent, QColor, QIcon, QFont, QDragEnterEvent, QDropEvent

from engine import (
    ENCODER_H264_VIDEOTOOLBOX,
    ENCODER_LABELS,
    ENCODER_LIBX264,
    MATCH_SOURCE_MAX_LONG_SIDE,
    MAX_AUDIO_DELAY_MS,
    MAX_HUE_DEGREES,
    MAX_ROTATE_DEGREES,
    PERFORMANCE_PROFILE_BALANCED,
    PERFORMANCE_PROFILE_FAST_MAC,
    PERFORMANCE_PROFILE_LABELS,
    PERFORMANCE_PROFILE_QUALITY,
    SPEECH_SAFE_PITCH_MAX,
    SPEECH_SAFE_PITCH_MIN,
    SPEECH_SAFE_VIDEO_SPEED_MAX,
    SPEECH_SAFE_VIDEO_SPEED_MIN,
    RandomRanges,
    UniqueParams,
    available_video_encoders,
    benchmark_video_encoders,
    check_ffmpeg_available,
    default_worker_count,
    ffmpeg_version_line,
    get_ffmpeg_path,
    probe_file,
    process_batch,
)
from image_engine import (
    IMAGE_EXTENSIONS,
    JPEG_QUALITY_CEILING,
    JPEG_QUALITY_FLOOR,
    MAX_IMAGE_NOISE,
    MAX_IMAGE_ROTATE_DEGREES,
    MAX_IMAGE_UNSHARP,
    MAX_IMAGE_VIGNETTE,
    UNSUPPORTED_IMAGE_EXTENSIONS,
    ImageParams,
    ImageRanges,
    image_workers_for,
    is_image_file,
    is_unsupported_image,
    process_image_batch,
)
from icons import (
    app_icon, file_select_icon, output_folder_icon,
    process_icon, settings_icon, remove_icon
)
from styles import MAIN_STYLESHEET
import applog
import settings_store
from version import DISPLAY_VERSION

log = logging.getLogger("app")

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}
# Unsupported stills are accepted into the list on purpose: routing them to the
# image pipeline produces a message naming the format, whereas silently
# ignoring the drop looks like the app is broken.
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS | UNSUPPORTED_IMAGE_EXTENSIONS


# (key, label, width, height). "match" resolves per file in the engine.
OUTPUT_SIZE_PRESETS = [
    ("match", f"Match Source (≤ {MATCH_SOURCE_MAX_LONG_SIDE} px)", 0, 0),
    ("9x16", "9:16 Vertical — 1080×1920", 1080, 1920),
    ("4x5", "4:5 Portrait — 1080×1350", 1080, 1350),
    ("1x1", "1:1 Square — 1080×1080", 1080, 1080),
    ("16x9", "16:9 Landscape — 1920×1080", 1920, 1080),
    ("custom", "Custom…", 0, 0),
]


def _is_still(path: str) -> bool:
    return is_image_file(path) or is_unsupported_image(path)


# A dropped folder is walked off the GUI thread and capped: a home folder or a
# photo library dropped by accident would otherwise freeze the window for
# minutes and then fill the list with tens of thousands of rows.
MAX_SCAN_FILES = 2000
# Packages look like folders to os.walk but are single documents to the user;
# a Photos library alone holds every original ever imported.
_SKIP_DIR_SUFFIXES = (".app", ".photoslibrary", ".fcpbundle", ".imovielibrary",
                      ".tvlibrary", ".musiclibrary")


def _is_candidate(path: str) -> bool:
    """A media file worth listing.

    Names starting with a dot are skipped, and that includes the ``._name.mp4``
    AppleDouble files macOS writes next to every file on exFAT/FAT drives: they
    carry a media extension, hold only Finder metadata, and fail as a
    "corrupt video" once for every real file on the card.
    """
    name = os.path.basename(path)
    if name.startswith("."):
        return False
    return os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS


def _format_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


class FolderScanWorker(QThread):
    """Walks dropped folders for media files without blocking the window."""
    scanned = pyqtSignal(list, bool)   # paths, truncated at MAX_SCAN_FILES

    def __init__(self, folders, skip_dir="", parent=None):
        super().__init__(parent)
        self.folders = folders
        # The output folder: its contents are this app's own results, and
        # re-listing them re-processes the previous batch.
        self.skip_dir = os.path.realpath(skip_dir) if skip_dir else ""
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        found, truncated = [], False
        try:
            for folder in self.folders:
                for root, dirs, files in os.walk(folder):
                    if self._cancelled:
                        return
                    dirs[:] = sorted(
                        d for d in dirs
                        if not d.startswith(".")
                        and not d.lower().endswith(_SKIP_DIR_SUFFIXES)
                        and os.path.realpath(os.path.join(root, d)) != self.skip_dir)
                    for f in sorted(files):
                        path = os.path.join(root, f)
                        if _is_candidate(path):
                            found.append(path)
                            if len(found) >= MAX_SCAN_FILES:
                                truncated = True
                                break
                    if truncated:
                        break
                if truncated:
                    break
        except Exception:
            log.exception("folder scan failed")
        log.info("folder scan: %d files%s from %s", len(found),
                 " (truncated)" if truncated else "", self.folders)
        self.scanned.emit(found, truncated)


class _ErrorRelay(QObject):
    """Carries an error message from any thread to a dialog on the GUI thread.

    Qt must only show widgets from the GUI thread. A signal emitted elsewhere
    is queued to the thread the relay lives in, so the dialog still appears
    for an exception raised inside a worker.
    """
    show = pyqtSignal(str)


_error_relay = None


def _show_error_dialog(message: str):
    path = applog.log_path()
    where = f"The details are in the log:\n{path}" if path else \
        "The log file could not be opened, so details went to stderr."
    try:
        QMessageBox.critical(
            None, "Unexpected Error",
            f"An unexpected error occurred:\n\n{message}\n\n{where}\n\n"
            "Help → Copy Diagnostics puts everything needed for a bug report "
            "on the clipboard.")
    except Exception:
        pass


def _install_exception_hook():
    """Log unhandled exceptions and show a dialog instead of a silent crash."""
    def _handler(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.error("unhandled exception",
                  exc_info=(exc_type, exc_value, exc_tb))
        app = QApplication.instance()
        if app is None:
            return
        if QThread.currentThread() is app.thread():
            _show_error_dialog(str(exc_value))
        elif _error_relay is not None:
            _error_relay.show.emit(str(exc_value))
    sys.excepthook = _handler


# ─── Worker Thread ──────────────────────────────────────

class ProcessWorker(QThread):
    progress = pyqtSignal(int, int, str)   # completed, total, filename
    # Whole-batch fraction 0.0–1.0, including files in flight; drives the bar
    # and the ETA. Weighted so a still counts for a small fraction of a video
    # (see _IMAGE_WEIGHT), otherwise a mixed batch races to "90%" on its
    # stills and then sits there for the whole video phase.
    overall_progress = pyqtSignal(float)
    file_started = pyqtSignal(str)             # full path
    file_result = pyqtSignal(str, bool, str)   # full path, ok, error
    # Deliberately not named `finished` — that would shadow QThread.finished,
    # which Qt uses internally for thread teardown.
    job_finished = pyqtSignal(int, bool, list)  # success_count, was_cancelled, errors
    error = pyqtSignal(str)

    _IMAGE_WEIGHT = 0.05

    def __init__(self, files, output_folder, params, ranges, workers=0,
                 image_params=None, image_ranges=None, parent=None):
        super().__init__(parent)
        self.files = files
        self.output_folder = output_folder
        self.params = params
        self.ranges = ranges
        self.workers = workers
        self.image_params = image_params
        self.image_ranges = image_ranges
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled

    def run(self):
        """Stills first, then video.

        The two pipelines want different concurrency — a still is not x264 and
        scales with cores, while video jobs have to split a fixed thread budget
        — so they run as separate phases rather than sharing one pool, and the
        one "Parallel Jobs" number is translated onto the still scale by
        ``image_workers_for`` rather than passed through. Stills finish in well
        under a second each, so putting them first also means the progress bar
        starts moving immediately on a mixed batch.
        """
        images = [f for f in self.files if _is_still(f)]
        videos = [f for f in self.files if not _is_still(f)]
        total = len(self.files)
        errors = []
        count = 0
        image_units = len(images) * self._IMAGE_WEIGHT
        all_units = (image_units + len(videos)) or 1.0
        log.info("batch start: %d videos, %d images -> %s (workers=%s, "
                 "encoder=%s, profile=%s, preset=%s, crf=%s, %s)",
                 len(videos), len(images), self.output_folder, self.workers,
                 self.params.encoder, self.params.performance_profile,
                 self.params.preset, self.params.crf,
                 f"{self.params.target_width}x{self.params.target_height}"
                 if self.params.target_width else "match source")

        try:
            if images:
                def _image_progress(cur, tot, fn):
                    self.progress.emit(cur, total, fn)
                    done = cur / tot if tot else 1.0
                    self.overall_progress.emit(done * image_units / all_units)

                done_images, image_errors = process_image_batch(
                    input_files=images,
                    output_folder=self.output_folder,
                    params_template=self.image_params,
                    ranges=self.image_ranges,
                    progress_callback=_image_progress,
                    cancelled=self.is_cancelled,
                    max_workers=image_workers_for(self.workers),
                    started_callback=self.file_started.emit,
                    result_callback=self.file_result.emit,
                )
                count += done_images
                errors += image_errors

            if videos and not self._cancelled:
                offset = len(images)
                done_videos, video_errors = process_batch(
                    input_files=videos,
                    output_folder=self.output_folder,
                    params_template=self.params,
                    ranges=self.ranges,
                    randomize_each=True,
                    progress_callback=lambda cur, tot, fn: self.progress.emit(
                        offset + cur, total, fn),
                    cancelled=self.is_cancelled,
                    max_workers=self.workers,
                    started_callback=self.file_started.emit,
                    result_callback=self.file_result.emit,
                    overall_progress_callback=lambda f: self.overall_progress.emit(
                        (image_units + f * len(videos)) / all_units),
                )
                count += done_videos
                errors += video_errors

            log.info("batch %s: %d/%d ok, %d errors",
                     "cancelled" if self._cancelled else "done",
                     count, total, len(errors))
            self.job_finished.emit(count, self._cancelled, errors)
        except Exception as e:
            log.exception("batch aborted")
            self.error.emit(str(e))


# ─── Drag & Drop File List ──────────────────────────────

class MediaFileList(QListWidget):
    """QListWidget that accepts video and image drops from Finder."""
    files_dropped = pyqtSignal(list)
    folders_dropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return

        paths, folders = [], []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if not path:
                continue
            if os.path.isfile(path):
                if _is_candidate(path):
                    paths.append(path)
            elif os.path.isdir(path):
                folders.append(path)
        if paths:
            self.files_dropped.emit(paths)
        if folders:
            self.folders_dropped.emit(folders)
        event.acceptProposedAction()


# ─── Helper Widgets ─────────────────────────────────────

def make_separator():
    sep = QFrame()
    sep.setObjectName("separator")
    sep.setFrameShape(QFrame.Shape.HLine)
    sep.setFixedHeight(1)
    return sep


def make_panel(title_text: str = "") -> tuple:
    """Creates a skeuomorphic card panel. Returns (card_widget, inner_layout)."""
    card = QFrame()
    card.setObjectName("panelCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    if title_text:
        title = QLabel(title_text)
        title.setObjectName("panelTitle")
        layout.addWidget(title)
    return card, layout


def make_param_row(label_text: str, widget: QWidget, tooltip: str = "") -> QHBoxLayout:
    row = QHBoxLayout()
    lbl = QLabel(label_text)
    lbl.setFixedWidth(180)
    if tooltip:
        lbl.setToolTip(tooltip)
        widget.setToolTip(tooltip)
    row.addWidget(lbl)
    row.addWidget(widget, 1)
    return row


def _safe_range(a: float, b: float) -> tuple:
    """Ensure min ≤ max for random.uniform / random.randint calls."""
    return (min(a, b), max(a, b))


# ─── Parameters Panel ───────────────────────────────────

class ParamsPanel(QWidget):
    """Panel with all adjustable parameters organized in tabs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.load_defaults()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)

        # The essentials stay visible; the ~45 tuning controls sit behind
        # "Advanced settings", off by default. The defaults are the measured
        # safe operating point, so most batches never need the tabs.
        main_layout.addLayout(self._build_quick_row())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_geometry_tab(), "Geometry")
        self.tabs.addTab(self._build_color_tab(), "Color")
        self.tabs.addTab(self._build_effects_tab(), "Effects")
        self.tabs.addTab(self._build_images_tab(), "Images")
        self.tabs.addTab(self._build_output_tab(), "Output")
        main_layout.addWidget(self.tabs)

        self.chk_advanced.toggled.connect(self.tabs.setVisible)
        self.tabs.setVisible(False)

    def _build_quick_row(self):
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        size_hint = ("Video output frame. Match Source keeps each video's own "
                     f"aspect ratio and caps the long side at "
                     f"{MATCH_SOURCE_MAX_LONG_SIDE} px (never upscales). A fixed "
                     "size that differs from a source's aspect fills the rest "
                     "with a blurred backdrop.\n\nImages always keep their own "
                     "aspect ratio; see the Images tab.")
        lbl = QLabel("Video Size:")
        lbl.setToolTip(size_hint)
        self.combo_size = QComboBox()
        self.combo_size.setToolTip(size_hint)
        for key, label, _w, _h in OUTPUT_SIZE_PRESETS:
            self.combo_size.addItem(label, key)
        self.combo_size.currentIndexChanged.connect(self._sync_size_controls)
        grid.addWidget(lbl, 0, 0)
        grid.addWidget(self.combo_size, 0, 1)

        self.spin_width = QSpinBox()
        self.spin_width.setRange(320, 7680)
        self.spin_width.setSingleStep(2)
        self.spin_width.setPrefix("W ")
        self.spin_height = QSpinBox()
        self.spin_height.setRange(320, 7680)
        self.spin_height.setSingleStep(2)
        self.spin_height.setPrefix("H ")
        size_row = QHBoxLayout()
        size_row.addWidget(self.spin_width)
        size_row.addWidget(self.spin_height)
        grid.addLayout(size_row, 0, 2)

        lbl = QLabel("Speed Profile:")
        self.combo_performance = QComboBox()
        for key, label in PERFORMANCE_PROFILE_LABELS.items():
            self.combo_performance.addItem(label, key)
        profile_hint = (
            "Sets the encoder, preset and hardware bitrate on the Output tab. "
            "The filter chain is the same in every profile.\n\n"
            "Max Quality: libx264, slow preset.\n"
            "Balanced: libx264, fast preset.\n"
            "Fast Mac: VideoToolbox hardware encoder where available.")
        lbl.setToolTip(profile_hint)
        self.combo_performance.setToolTip(profile_hint)
        grid.addWidget(lbl, 1, 0)
        grid.addWidget(self.combo_performance, 1, 1)

        self.chk_advanced = QCheckBox("Advanced settings")
        self.chk_advanced.setToolTip(
            "Show every tuning control. The defaults are the measured safe "
            "operating point; Reset Defaults returns to it.")
        grid.addWidget(self.chk_advanced, 1, 2)
        grid.setColumnStretch(1, 1)
        return grid

    def _sync_size_controls(self):
        key = self.combo_size.currentData()
        custom = key == "custom"
        self.spin_width.setVisible(custom)
        self.spin_height.setVisible(custom)
        for k, _label, w, h in OUTPUT_SIZE_PRESETS:
            if k == key and w:
                self.spin_width.setValue(w)
                self.spin_height.setValue(h)

    def target_size(self) -> tuple:
        """(width, height) for the video template; (0, 0) means match source."""
        if self.combo_size.currentData() == "match":
            return 0, 0
        # libx264 with yuv420p rejects odd dimensions.
        return self.spin_width.value() // 2 * 2, self.spin_height.value() // 2 * 2

    def get_state(self) -> dict:
        state = settings_store.widget_state(self)
        # A view preference, not a processing setting: loading a preset must
        # not expand or collapse the panel.
        state.pop("chk_advanced", None)
        state["overlay_file"] = self._overlay_file
        return state

    def set_state(self, state: dict):
        """Apply saved state on top of the defaults.

        The profile goes first with its signal blocked: its handler rewrites
        encoder, preset and bitrate, and the saved values of those must win.
        """
        self.load_defaults()
        self.combo_performance.blockSignals(True)
        try:
            settings_store.apply_widget_state(self, state, order=["combo_performance"])
        finally:
            self.combo_performance.blockSignals(False)
        self._sync_size_controls()
        self._sync_image_geometry_enabled()
        overlay = state.get("overlay_file") or ""
        if overlay and not os.path.isfile(overlay):
            log.info("saved overlay %s no longer exists; cleared", overlay)
            overlay = ""
        self._set_overlay(overlay)

    def _build_geometry_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        zoom_hint = ("Reframing is by far the strongest lever on a perceptual "
                     "hash — measured, it beats every filter in this app. It "
                     "costs sharpness rather than cleanliness, because the crop "
                     "is scaled back up to the output size. On smooth footage "
                     "the hash only clears a matcher's threshold above 1.14.")

        self.spin_zoom_min = QDoubleSpinBox()
        self.spin_zoom_min.setRange(1.0, 1.3)
        self.spin_zoom_min.setDecimals(3)
        self.spin_zoom_min.setSingleStep(0.005)
        layout.addLayout(make_param_row("Zoom Min:", self.spin_zoom_min, zoom_hint))

        self.spin_zoom_max = QDoubleSpinBox()
        self.spin_zoom_max.setRange(1.0, 1.3)
        self.spin_zoom_max.setDecimals(3)
        self.spin_zoom_max.setSingleStep(0.005)
        layout.addLayout(make_param_row("Zoom Max:", self.spin_zoom_max, zoom_hint))

        self.spin_rotate_max = QDoubleSpinBox()
        self.spin_rotate_max.setRange(0.0, MAX_ROTATE_DEGREES)
        self.spin_rotate_max.setDecimals(2)
        self.spin_rotate_max.setSingleStep(0.05)
        layout.addLayout(make_param_row(
            "Rotate Max (°):", self.spin_rotate_max,
            "Max rotation angle in degrees (+/-). Rotated corners are cropped away, "
            "so a larger angle costs more of the frame."))

        self.spin_k1_max = QDoubleSpinBox()
        self.spin_k1_max.setRange(0.0, 0.05)
        self.spin_k1_max.setDecimals(4)
        self.spin_k1_max.setSingleStep(0.001)
        layout.addLayout(make_param_row("Lens K1 Max:", self.spin_k1_max, "Max barrel/pincushion distortion (+/-)"))

        self.spin_pan_max = QDoubleSpinBox()
        self.spin_pan_max.setRange(0.0, 1.0)
        self.spin_pan_max.setDecimals(2)
        self.spin_pan_max.setSingleStep(0.05)
        layout.addLayout(make_param_row(
            "Pan Max:", self.spin_pan_max,
            "How far the zoomed crop window drifts off-centre, as a fraction of "
            "the slack zoom frees up. Never reaches the rotated corners."))

        self.spin_crop_min = QSpinBox()
        self.spin_crop_min.setRange(0, 40)
        layout.addLayout(make_param_row("Crop Margin Min (px):", self.spin_crop_min, "Min micro-crop pixels"))

        self.spin_crop_max = QSpinBox()
        self.spin_crop_max.setRange(0, 40)
        layout.addLayout(make_param_row("Crop Margin Max (px):", self.spin_crop_max, "Max micro-crop pixels"))

        self.chk_micro_warp = QCheckBox("Micro-warp (invisible, non-uniform sub-pixel resample)")
        self.chk_micro_warp.setToolTip(
            "Shifts the four corners by 0–3 px. Unlike rotation or scaling, the "
            "displacement varies across the frame, which is what perceptual "
            "hashes are least tolerant of.")
        layout.addWidget(self.chk_micro_warp)

        self.chk_warp_drift = QCheckBox("Micro-warp drift (invisible, varies the warp over time)")
        self.chk_warp_drift.setToolTip(
            "Each corner eases to a second inset over many hundreds of frames — "
            "about 0.01 px per frame, far below what reads as motion. A static "
            "warp is one transform for a matcher to solve; a drifting one leaves "
            "no single transform that aligns the clip end to end.")
        layout.addWidget(self.chk_warp_drift)

        self.chk_hflip = QCheckBox("Mirror horizontally (strongest, but VISIBLE)")
        self.chk_hflip.setToolTip(
            "Defeats most perceptual hashes outright at no quality cost — but it "
            "mirrors any on-screen text, logos and faces. Leave off unless the "
            "footage has no readable content.")
        layout.addWidget(self.chk_hflip)

        self.spin_trim_start_max = QDoubleSpinBox()
        self.spin_trim_start_max.setRange(0.0, 3.0)
        self.spin_trim_start_max.setDecimals(2)
        self.spin_trim_start_max.setSingleStep(0.1)
        layout.addLayout(make_param_row("Trim Start Max (s):", self.spin_trim_start_max, "Max seconds trimmed from start"))

        self.spin_trim_end_max = QDoubleSpinBox()
        self.spin_trim_end_max.setRange(0.0, 3.0)
        self.spin_trim_end_max.setDecimals(2)
        self.spin_trim_end_max.setSingleStep(0.1)
        layout.addLayout(make_param_row("Trim End Max (s):", self.spin_trim_end_max, "Max seconds trimmed from end"))

        layout.addStretch()
        return w

    def _build_color_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        self.spin_color_shift = QDoubleSpinBox()
        self.spin_color_shift.setRange(0.0, 0.3)
        self.spin_color_shift.setDecimals(3)
        self.spin_color_shift.setSingleStep(0.01)
        layout.addLayout(make_param_row("Color Shift Max:", self.spin_color_shift, "Max RGB color balance shift (+/-)"))

        self.spin_contrast_min = QDoubleSpinBox()
        self.spin_contrast_min.setRange(0.8, 1.0)
        self.spin_contrast_min.setDecimals(3)
        self.spin_contrast_min.setSingleStep(0.01)
        layout.addLayout(make_param_row("Contrast Min:", self.spin_contrast_min))

        self.spin_contrast_max = QDoubleSpinBox()
        self.spin_contrast_max.setRange(1.0, 1.2)
        self.spin_contrast_max.setDecimals(3)
        self.spin_contrast_max.setSingleStep(0.01)
        layout.addLayout(make_param_row("Contrast Max:", self.spin_contrast_max))

        self.spin_sat_min = QDoubleSpinBox()
        self.spin_sat_min.setRange(0.7, 1.0)
        self.spin_sat_min.setDecimals(3)
        self.spin_sat_min.setSingleStep(0.01)
        layout.addLayout(make_param_row("Saturation Min:", self.spin_sat_min))

        self.spin_sat_max = QDoubleSpinBox()
        self.spin_sat_max.setRange(1.0, 1.3)
        self.spin_sat_max.setDecimals(3)
        self.spin_sat_max.setSingleStep(0.01)
        layout.addLayout(make_param_row("Saturation Max:", self.spin_sat_max))

        self.spin_bright_max = QDoubleSpinBox()
        self.spin_bright_max.setRange(0.0, 0.1)
        self.spin_bright_max.setDecimals(3)
        self.spin_bright_max.setSingleStep(0.005)
        layout.addLayout(make_param_row("Brightness Max:", self.spin_bright_max, "Max brightness shift (+/-)"))

        self.spin_hue_max = QDoubleSpinBox()
        self.spin_hue_max.setRange(0.0, MAX_HUE_DEGREES)
        self.spin_hue_max.setDecimals(1)
        self.spin_hue_max.setSingleStep(0.5)
        layout.addLayout(make_param_row(
            "Hue Shift Max (°):", self.spin_hue_max,
            "Max hue rotation (+/-). Past roughly 5° the drift shows on skin "
            "tones and brand colours."))

        self.chk_gamma = QCheckBox("Gamma micro-shift (invisible, breaks perceptual hash)")
        layout.addWidget(self.chk_gamma)

        self.chk_tone_curve = QCheckBox("Tone curve (invisible, resists colour normalisation)")
        self.chk_tone_curve.setToolTip(
            "Folds the colour shift and the gamma bend into one monotonic spline "
            "per channel, replacing two filters with one. A matcher that undoes a "
            "colour grade fits a gain or a gamma; an arbitrary spline is neither, "
            "so the fit leaves a residue behind.")
        layout.addWidget(self.chk_tone_curve)

        layout.addStretch()
        return w

    def _build_effects_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        self.spin_noise_min = QSpinBox()
        self.spin_noise_min.setRange(0, 50)
        layout.addLayout(make_param_row("Noise Min:", self.spin_noise_min, "Min noise/grain strength"))

        self.spin_noise_max = QSpinBox()
        self.spin_noise_max.setRange(0, 50)
        layout.addLayout(make_param_row("Noise Max:", self.spin_noise_max, "Max noise/grain strength"))

        self.spin_unsharp_min = QDoubleSpinBox()
        self.spin_unsharp_min.setRange(-2.0, 2.0)
        self.spin_unsharp_min.setDecimals(2)
        self.spin_unsharp_min.setSingleStep(0.1)
        layout.addLayout(make_param_row("Unsharp Min:", self.spin_unsharp_min, "Negative = blur, positive = sharpen"))

        self.spin_unsharp_max = QDoubleSpinBox()
        self.spin_unsharp_max.setRange(-2.0, 2.0)
        self.spin_unsharp_max.setDecimals(2)
        self.spin_unsharp_max.setSingleStep(0.1)
        layout.addLayout(make_param_row("Unsharp Max:", self.spin_unsharp_max))

        self.spin_vignette_min = QDoubleSpinBox()
        self.spin_vignette_min.setRange(0.0, 1.0)
        self.spin_vignette_min.setDecimals(2)
        self.spin_vignette_min.setSingleStep(0.05)
        layout.addLayout(make_param_row("Vignette Min:", self.spin_vignette_min))

        self.spin_vignette_max = QDoubleSpinBox()
        self.spin_vignette_max.setRange(0.0, 1.0)
        self.spin_vignette_max.setDecimals(2)
        self.spin_vignette_max.setSingleStep(0.05)
        layout.addLayout(make_param_row("Vignette Max:", self.spin_vignette_max))

        self.spin_speed_min = QDoubleSpinBox()
        self.spin_speed_min.setRange(SPEECH_SAFE_VIDEO_SPEED_MIN, 1.0)
        self.spin_speed_min.setDecimals(4)
        self.spin_speed_min.setSingleStep(0.005)
        layout.addLayout(make_param_row("Video Speed Min:", self.spin_speed_min))

        self.spin_speed_max = QDoubleSpinBox()
        self.spin_speed_max.setRange(1.0, SPEECH_SAFE_VIDEO_SPEED_MAX)
        self.spin_speed_max.setDecimals(4)
        self.spin_speed_max.setSingleStep(0.005)
        layout.addLayout(make_param_row("Video Speed Max:", self.spin_speed_max))

        self.spin_pitch_min = QDoubleSpinBox()
        self.spin_pitch_min.setRange(SPEECH_SAFE_PITCH_MIN, 1.0)
        self.spin_pitch_min.setDecimals(4)
        self.spin_pitch_min.setSingleStep(0.005)
        layout.addLayout(make_param_row("Audio Pitch Min:", self.spin_pitch_min))

        self.spin_pitch_max = QDoubleSpinBox()
        self.spin_pitch_max.setRange(1.0, SPEECH_SAFE_PITCH_MAX)
        self.spin_pitch_max.setDecimals(4)
        self.spin_pitch_max.setSingleStep(0.005)
        layout.addLayout(make_param_row("Audio Pitch Max:", self.spin_pitch_max))

        self.spin_adelay_min = QSpinBox()
        self.spin_adelay_min.setRange(0, MAX_AUDIO_DELAY_MS)
        layout.addLayout(make_param_row(
            "Audio Delay Min (ms):", self.spin_adelay_min,
            "Audio is shifted against the picture, so this is capped at "
            f"{MAX_AUDIO_DELAY_MS} ms — lip-sync error becomes noticeable around 45 ms."))

        self.spin_adelay_max = QSpinBox()
        self.spin_adelay_max.setRange(0, MAX_AUDIO_DELAY_MS)
        layout.addLayout(make_param_row("Audio Delay Max (ms):", self.spin_adelay_max))

        self.chk_fps_jitter = QCheckBox("Frame rate jitter (invisible, rewrites every timestamp)")
        self.chk_fps_jitter.setToolTip(
            "Retimes to within 0.1% of the source rate — one duplicated or "
            "dropped frame roughly every 30 seconds.")
        layout.addWidget(self.chk_fps_jitter)

        self.chk_audio_eq = QCheckBox("Inaudible frequency EQ (breaks audio fingerprint)")
        layout.addWidget(self.chk_audio_eq)

        self.chk_audio_notch = QCheckBox("Spectral notches (inaudible, moves audio fingerprint peaks)")
        self.chk_audio_notch.setToolTip(
            "Five narrow cuts under 1.5 dB. Audio fingerprints key off spectral "
            "peak positions, which a broad EQ leaves intact.")
        layout.addWidget(self.chk_audio_notch)

        self.chk_audio_tilt = QCheckBox("Broadband spectral tilt (inaudible, shifts band energies)")
        self.chk_audio_tilt.setToolTip(
            "A shelf under 1 dB at each end of the spectrum. Fingerprints that "
            "bin energy into wide log bands barely notice a narrow notch but do "
            "read a tilt across the whole range.")
        layout.addWidget(self.chk_audio_tilt)

        self.chk_audio_noise_floor = QCheckBox("Added noise floor (inaudible, sits under room tone)")
        self.chk_audio_noise_floor.setToolTip(
            "Pink noise around -65 dBFS, independent per channel. Well below "
            "the noise floor of any real recording, so it cannot be heard.")
        layout.addWidget(self.chk_audio_noise_floor)

        self.chk_audio_resample = QCheckBox("Audio sample rate round-trip (invisible, resamples all audio)")
        layout.addWidget(self.chk_audio_resample)

        self.chk_chroma_roundtrip = QCheckBox("Chroma subsampling round-trip (invisible, changes all color values)")
        layout.addWidget(self.chk_chroma_roundtrip)

        layout.addStretch()
        return w

    def _build_images_tab(self):
        """Controls whose safe bounds differ between stills and video.

        Colour and the sub-perceptual toggles are shared with the video tabs —
        the same grade is no more visible on a photo than in motion — but grain,
        sharpening, vignette and rotation get their own controls here, because a
        still is examined rather than watched and their caps are lower.
        """
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        note = QLabel(
            "Stills reuse the Color tab and the sub-perceptual toggles.\n"
            "The settings below are the ones whose safe limits are lower on a\n"
            "photo than on video, plus the JPEG/PNG output settings."
        )
        note.setStyleSheet("color: #888;")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.chk_img_full_frame = QCheckBox(
            "Keep the full frame (never crop — disables all reframing)")
        self.chk_img_full_frame.setToolTip(
            "Keeps every source pixel: no zoom crop, no crop margin, no pan, "
            "and no rotation, lens distortion or micro-warp, since those leave "
            "uncovered corners that only a crop can hide.\n\n"
            "Reframing is the only stage measured to move a still's perceptual "
            "hash, so with this on the output is perceptually near-identical to "
            "the source. Colour, grain, the encode, the output size and the "
            "fabricated metadata still vary per file.")
        self.chk_img_full_frame.toggled.connect(self._sync_image_geometry_enabled)
        layout.addWidget(self.chk_img_full_frame)

        self.spin_img_zoom_min = QDoubleSpinBox()
        self.spin_img_zoom_min.setRange(1.0, 1.4)
        self.spin_img_zoom_min.setDecimals(3)
        self.spin_img_zoom_min.setSingleStep(0.005)
        layout.addLayout(make_param_row(
            "Image Zoom Min:", self.spin_img_zoom_min,
            "Reframing is the strongest lever on a perceptual hash. On a still "
            "it is also a permanent resolution loss, since the crop is scaled "
            "back up — check the result at 1:1 before raising it."))

        self.spin_img_zoom_max = QDoubleSpinBox()
        self.spin_img_zoom_max.setRange(1.0, 1.4)
        self.spin_img_zoom_max.setDecimals(3)
        self.spin_img_zoom_max.setSingleStep(0.005)
        layout.addLayout(make_param_row("Image Zoom Max:", self.spin_img_zoom_max))

        self.spin_img_rotate_max = QDoubleSpinBox()
        self.spin_img_rotate_max.setRange(0.0, MAX_IMAGE_ROTATE_DEGREES)
        self.spin_img_rotate_max.setDecimals(2)
        self.spin_img_rotate_max.setSingleStep(0.05)
        layout.addLayout(make_param_row(
            "Image Rotate Max (°):", self.spin_img_rotate_max,
            f"Capped at {MAX_IMAGE_ROTATE_DEGREES}° — a tilted horizon is "
            "obvious on a photo in a way it is not mid-motion."))

        layout.addWidget(make_separator())

        self.spin_img_noise_min = QSpinBox()
        self.spin_img_noise_min.setRange(0, MAX_IMAGE_NOISE)
        layout.addLayout(make_param_row(
            "Image Grain Min:", self.spin_img_noise_min,
            f"Capped at {MAX_IMAGE_NOISE}. Nothing averages grain away on a "
            "still and the viewer can zoom, so the video range is visible here."))

        self.spin_img_noise_max = QSpinBox()
        self.spin_img_noise_max.setRange(0, MAX_IMAGE_NOISE)
        layout.addLayout(make_param_row("Image Grain Max:", self.spin_img_noise_max))

        self.spin_img_unsharp_min = QDoubleSpinBox()
        self.spin_img_unsharp_min.setRange(-MAX_IMAGE_UNSHARP, MAX_IMAGE_UNSHARP)
        self.spin_img_unsharp_min.setDecimals(2)
        self.spin_img_unsharp_min.setSingleStep(0.05)
        layout.addLayout(make_param_row("Image Sharpen Min:", self.spin_img_unsharp_min))

        self.spin_img_unsharp_max = QDoubleSpinBox()
        self.spin_img_unsharp_max.setRange(-MAX_IMAGE_UNSHARP, MAX_IMAGE_UNSHARP)
        self.spin_img_unsharp_max.setDecimals(2)
        self.spin_img_unsharp_max.setSingleStep(0.05)
        layout.addLayout(make_param_row("Image Sharpen Max:", self.spin_img_unsharp_max))

        self.spin_img_vignette_min = QDoubleSpinBox()
        self.spin_img_vignette_min.setRange(0.0, MAX_IMAGE_VIGNETTE)
        self.spin_img_vignette_min.setDecimals(2)
        self.spin_img_vignette_min.setSingleStep(0.01)
        layout.addLayout(make_param_row("Image Vignette Min:", self.spin_img_vignette_min))

        self.spin_img_vignette_max = QDoubleSpinBox()
        self.spin_img_vignette_max.setRange(0.0, MAX_IMAGE_VIGNETTE)
        self.spin_img_vignette_max.setDecimals(2)
        self.spin_img_vignette_max.setSingleStep(0.01)
        layout.addLayout(make_param_row("Image Vignette Max:", self.spin_img_vignette_max))

        layout.addWidget(make_separator())

        self.combo_image_format = QComboBox()
        self.combo_image_format.addItem("Match source (JPEG in → JPEG out)", "auto")
        self.combo_image_format.addItem("Always JPEG", "jpeg")
        self.combo_image_format.addItem("Always PNG", "png")
        layout.addLayout(make_param_row(
            "Output Format:", self.combo_image_format,
            "PNG is kept lossless because flattening a graphic to JPEG rings "
            "on every hard edge. WEBP has no encoder in the bundled ffmpeg and "
            "always leaves as JPEG."))

        self.spin_jpeg_q_min = QSpinBox()
        self.spin_jpeg_q_min.setRange(JPEG_QUALITY_FLOOR, JPEG_QUALITY_CEILING)
        layout.addLayout(make_param_row(
            "JPEG Quality Min:", self.spin_jpeg_q_min,
            "ffmpeg's -q:v scale: 2 is near-lossless, higher is worse. Meta "
            "re-encodes on upload, so the extra bytes below about 6 are thrown "
            "away by their transcode anyway."))

        self.spin_jpeg_q_max = QSpinBox()
        self.spin_jpeg_q_max.setRange(JPEG_QUALITY_FLOOR, JPEG_QUALITY_CEILING)
        layout.addLayout(make_param_row("JPEG Quality Max:", self.spin_jpeg_q_max))

        self.spin_max_long_side = QSpinBox()
        self.spin_max_long_side.setRange(0, 8000)
        self.spin_max_long_side.setSingleStep(64)
        self.spin_max_long_side.setSpecialValueText("Keep source size")
        layout.addLayout(make_param_row(
            "Max Long Side (px):", self.spin_max_long_side,
            "0 keeps the source resolution. Meta downscales above 1936 px on "
            "the long edge, so capping here only saves upload bytes."))

        self.chk_img_size_jitter = QCheckBox(
            "Jitter output dimensions (breaks exact-size grouping, invisible)")
        layout.addWidget(self.chk_img_size_jitter)

        self.chk_img_huffman = QCheckBox(
            "Randomize JPEG entropy coding (different bytes, identical pixels)")
        layout.addWidget(self.chk_img_huffman)

        layout.addStretch()
        return w

    def _build_output_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        self.spin_crf = QSpinBox()
        self.spin_crf.setRange(0, 51)
        layout.addLayout(make_param_row("CRF (quality):", self.spin_crf, "0=lossless, 23=default, 51=worst"))

        # The profile combo lives in the quick row; it drives these controls.
        self.combo_performance.currentIndexChanged.connect(self._apply_performance_profile)

        self.combo_encoder = QComboBox()
        available = available_video_encoders()
        for key, label in ENCODER_LABELS.items():
            self.combo_encoder.addItem(label, key)
            if key not in available:
                idx = self.combo_encoder.count() - 1
                self.combo_encoder.model().item(idx).setEnabled(False)
        layout.addLayout(make_param_row("Video Encoder:", self.combo_encoder, "libx264 is recommended for maximum uniqueness; VideoToolbox is optional"))

        self.spin_video_bitrate = QSpinBox()
        self.spin_video_bitrate.setRange(1000, 80000)
        self.spin_video_bitrate.setSingleStep(500)
        self.spin_video_bitrate.setSuffix(" kbps")
        layout.addLayout(make_param_row("HW Bitrate:", self.spin_video_bitrate, "Used by VideoToolbox encoders"))

        self.spin_gop_min = QSpinBox()
        self.spin_gop_min.setRange(10, 300)
        layout.addLayout(make_param_row("GOP Size Min:", self.spin_gop_min, "Min keyframe interval (Broken GOP)"))

        self.spin_gop_max = QSpinBox()
        self.spin_gop_max.setRange(10, 300)
        layout.addLayout(make_param_row("GOP Size Max:", self.spin_gop_max, "Max keyframe interval (Broken GOP)"))

        self.combo_preset = QComboBox()
        self.combo_preset.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"])
        layout.addLayout(make_param_row("Encoding Preset:", self.combo_preset))

        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(1, 8)
        layout.addLayout(make_param_row(
            "Parallel Jobs:", self.spin_workers,
            "How many videos encode at once. Each job gets a share of the CPU "
            "threads, so more jobs mostly helps on short clips.\n\n"
            "Images are far cheaper than a video encode and keep scaling to "
            "about one job per core, so a still batch reads this as the same "
            "position on its own scale, not the same number of files."))

        self.chk_fake_meta = QCheckBox("Write fake camera metadata (EXIF)")
        layout.addWidget(self.chk_fake_meta)

        self.chk_x264_tuning = QCheckBox("Randomize x264 encoding params (deblock, aq, psy-rd)")
        layout.addWidget(self.chk_x264_tuning)

        # Overlay section
        layout.addWidget(make_separator())
        overlay_lbl = QLabel("Overlay Video (optional):")
        overlay_lbl.setStyleSheet("font-weight: 600;")
        layout.addWidget(overlay_lbl)

        row = QHBoxLayout()
        self.overlay_path_label = QLabel("No overlay selected")
        self.overlay_path_label.setStyleSheet("color: #888;")
        row.addWidget(self.overlay_path_label, 1)

        btn_overlay = QPushButton("Choose...")
        btn_overlay.setFixedWidth(100)
        btn_overlay.clicked.connect(self._choose_overlay)
        row.addWidget(btn_overlay)

        btn_clear_overlay = QPushButton("Clear")
        btn_clear_overlay.setFixedWidth(60)
        btn_clear_overlay.clicked.connect(self._clear_overlay)
        row.addWidget(btn_clear_overlay)

        layout.addLayout(row)
        self._overlay_file = ""

        self.spin_ov_opacity_min = QDoubleSpinBox()
        self.spin_ov_opacity_min.setRange(0.0, 0.5)
        self.spin_ov_opacity_min.setDecimals(2)
        self.spin_ov_opacity_min.setSingleStep(0.01)
        layout.addLayout(make_param_row("Overlay Opacity Min:", self.spin_ov_opacity_min))

        self.spin_ov_opacity_max = QDoubleSpinBox()
        self.spin_ov_opacity_max.setRange(0.0, 0.5)
        self.spin_ov_opacity_max.setDecimals(2)
        self.spin_ov_opacity_max.setSingleStep(0.01)
        layout.addLayout(make_param_row("Overlay Opacity Max:", self.spin_ov_opacity_max))

        layout.addStretch()
        return w

    def _choose_overlay(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Overlay Video", "",
            "Video Files (*.mp4 *.mov *.avi *.mkv);;All Files (*)"
        )
        if not path:
            return
        # Probed now rather than at encode time: an unreadable overlay used to
        # be skipped silently for every file in the batch.
        if not probe_file(path).has_geometry:
            QMessageBox.warning(
                self, "Unusable Overlay",
                f"{os.path.basename(path)} could not be read as a video, so it "
                "cannot be used as an overlay.")
            return
        self._set_overlay(path)

    def _clear_overlay(self):
        self._set_overlay("")

    def _set_overlay(self, path: str):
        self._overlay_file = path
        self.overlay_path_label.setText(os.path.basename(path) if path else "No overlay selected")
        self.overlay_path_label.setToolTip(path)

    def load_defaults(self):
        """Load the perceptually safe operating point (see RandomRanges)."""
        d = RandomRanges()

        self.spin_zoom_min.setValue(d.zoom[0])
        self.spin_zoom_max.setValue(d.zoom[1])
        self.spin_rotate_max.setValue(d.rotate_max)
        self.spin_k1_max.setValue(d.k1_max)
        self.spin_pan_max.setValue(d.pan_max)
        self.spin_crop_min.setValue(d.crop_margin[0])
        self.spin_crop_max.setValue(d.crop_margin[1])

        self.spin_trim_start_max.setValue(d.trim_start[1])
        self.spin_trim_end_max.setValue(d.trim_end[1])

        self.spin_color_shift.setValue(d.color_shift_max)
        self.spin_contrast_min.setValue(d.contrast[0])
        self.spin_contrast_max.setValue(d.contrast[1])
        self.spin_sat_min.setValue(d.saturation[0])
        self.spin_sat_max.setValue(d.saturation[1])
        self.spin_bright_max.setValue(d.brightness_max)
        self.spin_hue_max.setValue(d.hue_max)

        self.spin_noise_min.setValue(d.noise[0])
        self.spin_noise_max.setValue(d.noise[1])
        self.spin_unsharp_min.setValue(d.unsharp[0])
        self.spin_unsharp_max.setValue(d.unsharp[1])
        self.spin_vignette_min.setValue(d.vignette[0])
        self.spin_vignette_max.setValue(d.vignette[1])
        self.spin_speed_min.setValue(d.video_speed[0])
        self.spin_speed_max.setValue(d.video_speed[1])
        self.spin_pitch_min.setValue(d.pitch[0])
        self.spin_pitch_max.setValue(d.pitch[1])
        self.spin_adelay_min.setValue(d.adelay_ms[0])
        self.spin_adelay_max.setValue(d.adelay_ms[1])

        self.combo_size.setCurrentIndex(self.combo_size.findData("match"))
        self._sync_size_controls()
        self.spin_width.setValue(1080)
        self.spin_height.setValue(1920)
        self.spin_crf.setValue(24)
        self.spin_gop_min.setValue(d.gop[0])
        self.spin_gop_max.setValue(d.gop[1])
        # The profile owns encoder, preset and bitrate. Setting the combo does
        # not fire currentIndexChanged when the index is already right, so the
        # profile is applied explicitly: otherwise Reset Defaults and a fresh
        # launch could leave "Max Quality" on screen over a `fast` preset.
        # Balanced is the default because it is what UniqueParams defaults to.
        self.combo_performance.blockSignals(True)
        self.combo_performance.setCurrentIndex(
            self.combo_performance.findData(UniqueParams().performance_profile))
        self.combo_performance.blockSignals(False)
        self._apply_performance_profile()
        self.spin_workers.setValue(default_worker_count())
        self.chk_fake_meta.setChecked(True)
        self.chk_micro_warp.setChecked(d.use_micro_warp)
        self.chk_warp_drift.setChecked(d.use_warp_drift)
        self.chk_tone_curve.setChecked(d.use_tone_curve)
        self.chk_audio_tilt.setChecked(d.use_audio_tilt)
        self.chk_audio_noise_floor.setChecked(d.use_audio_noise_floor)
        self.chk_hflip.setChecked(d.use_hflip)
        self.chk_gamma.setChecked(d.use_gamma)
        self.chk_fps_jitter.setChecked(d.use_fps_jitter)
        self.chk_audio_eq.setChecked(d.use_audio_eq)
        self.chk_audio_notch.setChecked(d.use_audio_notch)
        self.chk_audio_resample.setChecked(d.use_audio_resample)
        self.chk_chroma_roundtrip.setChecked(d.use_chroma_roundtrip)
        self.chk_x264_tuning.setChecked(d.use_x264_tuning)

        self.spin_ov_opacity_min.setValue(d.ov_opacity[0])
        self.spin_ov_opacity_max.setValue(d.ov_opacity[1])
        self._set_overlay("")

        di = ImageRanges()
        self.chk_img_full_frame.setChecked(di.preserve_full_frame)
        self._sync_image_geometry_enabled()
        self.spin_img_zoom_min.setValue(di.zoom[0])
        self.spin_img_zoom_max.setValue(di.zoom[1])
        self.spin_img_rotate_max.setValue(di.rotate_max)
        self.spin_img_noise_min.setValue(di.noise[0])
        self.spin_img_noise_max.setValue(di.noise[1])
        self.spin_img_unsharp_min.setValue(di.unsharp[0])
        self.spin_img_unsharp_max.setValue(di.unsharp[1])
        self.spin_img_vignette_min.setValue(di.vignette[0])
        self.spin_img_vignette_max.setValue(di.vignette[1])
        self.spin_jpeg_q_min.setValue(di.jpeg_quality[0])
        self.spin_jpeg_q_max.setValue(di.jpeg_quality[1])
        self.spin_max_long_side.setValue(di.max_long_side)
        self.combo_image_format.setCurrentIndex(
            self.combo_image_format.findData("auto"))
        self.chk_img_size_jitter.setChecked(True)
        self.chk_img_huffman.setChecked(di.use_jpeg_huffman_jitter)

    def _sync_image_geometry_enabled(self):
        """Grey out the still reframing controls while full-frame output is on.

        They are not merely ignored in that mode — the engine holds zoom,
        rotation, lens distortion, the micro-warp, the crop margin and the pan
        at identity — so leaving them live would misreport what runs.
        """
        enabled = not self.chk_img_full_frame.isChecked()
        for widget in (self.spin_img_zoom_min, self.spin_img_zoom_max,
                       self.spin_img_rotate_max):
            widget.setEnabled(enabled)

    def _apply_performance_profile(self):
        profile = self.combo_performance.currentData()
        if profile == PERFORMANCE_PROFILE_FAST_MAC:
            idx = self.combo_encoder.findData(ENCODER_H264_VIDEOTOOLBOX)
            if idx >= 0 and self.combo_encoder.model().item(idx).isEnabled():
                self.combo_encoder.setCurrentIndex(idx)
            self.combo_preset.setCurrentText("veryfast")
            self.spin_video_bitrate.setValue(9000)
        elif profile == PERFORMANCE_PROFILE_QUALITY:
            idx = self.combo_encoder.findData(ENCODER_LIBX264)
            if idx >= 0:
                self.combo_encoder.setCurrentIndex(idx)
            self.combo_preset.setCurrentText("slow")
            self.spin_video_bitrate.setValue(12000)
        else:
            idx = self.combo_encoder.findData(ENCODER_LIBX264)
            if idx >= 0:
                self.combo_encoder.setCurrentIndex(idx)
            self.combo_preset.setCurrentText("fast")
            self.spin_video_bitrate.setValue(8000)

    def build_ranges(self) -> RandomRanges:
        """Collect the randomization spec. The engine redraws it for every file.

        Previously the panel drew one concrete value per run and the engine
        discarded most of it, so these controls had no effect on the output.
        """
        return RandomRanges(
            zoom=_safe_range(self.spin_zoom_min.value(), self.spin_zoom_max.value()),
            rotate_max=self.spin_rotate_max.value(),
            k1_max=self.spin_k1_max.value(),
            crop_margin=_safe_range(self.spin_crop_min.value(), self.spin_crop_max.value()),
            pan_max=self.spin_pan_max.value(),
            trim_start=(0.1, max(0.1, self.spin_trim_start_max.value())),
            trim_end=(0.1, max(0.1, self.spin_trim_end_max.value())),

            color_shift_max=self.spin_color_shift.value(),
            contrast=_safe_range(self.spin_contrast_min.value(), self.spin_contrast_max.value()),
            saturation=_safe_range(self.spin_sat_min.value(), self.spin_sat_max.value()),
            brightness_max=self.spin_bright_max.value(),
            hue_max=self.spin_hue_max.value(),

            noise=_safe_range(self.spin_noise_min.value(), self.spin_noise_max.value()),
            unsharp=_safe_range(self.spin_unsharp_min.value(), self.spin_unsharp_max.value()),
            vignette=_safe_range(self.spin_vignette_min.value(), self.spin_vignette_max.value()),

            video_speed=_safe_range(self.spin_speed_min.value(), self.spin_speed_max.value()),
            pitch=_safe_range(self.spin_pitch_min.value(), self.spin_pitch_max.value()),
            adelay_ms=_safe_range(self.spin_adelay_min.value(), self.spin_adelay_max.value()),
            gop=_safe_range(self.spin_gop_min.value(), self.spin_gop_max.value()),

            ov_opacity=_safe_range(self.spin_ov_opacity_min.value(), self.spin_ov_opacity_max.value()),

            use_gamma=self.chk_gamma.isChecked(),
            use_micro_warp=self.chk_micro_warp.isChecked(),
            use_warp_drift=self.chk_warp_drift.isChecked(),
            use_tone_curve=self.chk_tone_curve.isChecked(),
            use_audio_tilt=self.chk_audio_tilt.isChecked(),
            use_audio_noise_floor=self.chk_audio_noise_floor.isChecked(),
            use_chroma_roundtrip=self.chk_chroma_roundtrip.isChecked(),
            use_audio_eq=self.chk_audio_eq.isChecked(),
            use_audio_notch=self.chk_audio_notch.isChecked(),
            use_audio_resample=self.chk_audio_resample.isChecked(),
            use_x264_tuning=self.chk_x264_tuning.isChecked(),
            use_fps_jitter=self.chk_fps_jitter.isChecked(),
            use_hflip=self.chk_hflip.isChecked(),
        )

    def build_params(self) -> UniqueParams:
        """Build the static template: output settings that must not be randomized."""
        p = UniqueParams()
        p.overlay_file = self._overlay_file
        p.target_width, p.target_height = self.target_size()
        p.crf = self.spin_crf.value()
        p.preset = self.combo_preset.currentText()
        p.performance_profile = self.combo_performance.currentData() or PERFORMANCE_PROFILE_BALANCED
        p.encoder = self.combo_encoder.currentData() or ENCODER_LIBX264
        p.video_bitrate_kbps = self.spin_video_bitrate.value()
        p.fake_meta = self.chk_fake_meta.isChecked()
        return p

    def build_image_ranges(self) -> ImageRanges:
        """The still spec.

        Colour and the sub-perceptual toggles come from the shared tabs; only
        the controls whose safe bounds differ come from the Images tab.
        """
        return ImageRanges(
            zoom=_safe_range(self.spin_img_zoom_min.value(), self.spin_img_zoom_max.value()),
            rotate_max=self.spin_img_rotate_max.value(),
            k1_max=self.spin_k1_max.value(),
            crop_margin=_safe_range(self.spin_crop_min.value(), self.spin_crop_max.value()),
            pan_max=self.spin_pan_max.value(),

            color_shift_max=self.spin_color_shift.value(),
            contrast=_safe_range(self.spin_contrast_min.value(), self.spin_contrast_max.value()),
            saturation=_safe_range(self.spin_sat_min.value(), self.spin_sat_max.value()),
            brightness_max=self.spin_bright_max.value(),
            hue_max=self.spin_hue_max.value(),

            noise=_safe_range(self.spin_img_noise_min.value(), self.spin_img_noise_max.value()),
            unsharp=_safe_range(self.spin_img_unsharp_min.value(), self.spin_img_unsharp_max.value()),
            vignette=_safe_range(self.spin_img_vignette_min.value(), self.spin_img_vignette_max.value()),

            jpeg_quality=_safe_range(self.spin_jpeg_q_min.value(), self.spin_jpeg_q_max.value()),
            size_jitter=((0.985, 1.0) if self.chk_img_size_jitter.isChecked()
                         else (1.0, 1.0)),
            max_long_side=self.spin_max_long_side.value(),

            preserve_full_frame=self.chk_img_full_frame.isChecked(),
            use_gamma=self.chk_gamma.isChecked(),
            use_micro_warp=self.chk_micro_warp.isChecked(),
            use_tone_curve=self.chk_tone_curve.isChecked(),
            use_chroma_roundtrip=self.chk_chroma_roundtrip.isChecked(),
            use_jpeg_huffman_jitter=self.chk_img_huffman.isChecked(),
            use_hflip=self.chk_hflip.isChecked(),
            fake_meta=self.chk_fake_meta.isChecked(),
        )

    def build_image_params(self) -> ImageParams:
        """The still template: output settings that must not be randomized."""
        p = ImageParams()
        p.output_format = self.combo_image_format.currentData() or "auto"
        p.max_long_side = self.spin_max_long_side.value()
        p.fake_meta = self.chk_fake_meta.isChecked()
        return p

    def worker_count(self) -> int:
        return self.spin_workers.value()


# ─── Main Window ────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Uniqualizer")
        self.setWindowIcon(app_icon(512))
        self.setMinimumSize(780, 680)
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            initial_h = min(740, available.height() - 40)
            self.resize(820, initial_h)
        else:
            self.resize(820, 740)

        self.worker = None
        self.scanner = None
        self.input_files = []
        self.output_folder = ""
        # Set when the user quits mid-batch: the window closes itself once the
        # worker has stopped, and the end-of-batch dialogs are skipped.
        self._quit_after_worker = False
        # Per-batch bookkeeping for the list statuses, the progress text and
        # the ETA.
        self._batch_files = []
        self._results = {}          # path -> (ok, error)
        self._active = []           # paths currently encoding, in start order
        self._done_count = 0
        self._batch_started = 0.0

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        root.addWidget(self._build_top_bar())

        # Content area
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(14)

        # File selection section
        content_layout.addWidget(self._build_file_section())

        # Parameters section (collapsible)
        content_layout.addWidget(self._build_params_section())

        # Action section
        content_layout.addWidget(self._build_action_section())

        scroll = QScrollArea()
        scroll.setWidget(content)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll, 1)

        self._build_menu()
        self._restore_settings()
        self._update_button_states()

    def _build_menu(self):
        help_menu = self.menuBar().addMenu("Help")

        act_log = QAction("Show Log in Finder", self)
        act_log.triggered.connect(self._show_log)
        help_menu.addAction(act_log)

        act_diag = QAction("Copy Diagnostics", self)
        act_diag.triggered.connect(self._copy_diagnostics)
        help_menu.addAction(act_diag)

        act_lic = QAction("Third-Party Licenses", self)
        act_lic.triggered.connect(self._show_licenses)
        help_menu.addAction(act_lic)

        # macOS moves an action with this role into the application menu.
        act_about = QAction("About Video Uniqualizer", self)
        act_about.setMenuRole(QAction.MenuRole.AboutRole)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _show_log(self):
        path = applog.log_path()
        if not path or not os.path.exists(path):
            QMessageBox.information(
                self, "No Log File",
                "The log file could not be created, so there is nothing to show.")
            return
        subprocess.run(["open", "-R", path], check=False)

    def _show_licenses(self):
        if getattr(sys, "frozen", False):
            folder = os.path.join(sys._MEIPASS, "licenses")
        else:
            folder = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "ffmpeg_bin", "licenses")
        notices = os.path.join(folder, "THIRD_PARTY_NOTICES.txt")
        if not os.path.isfile(notices):
            QMessageBox.information(
                self, "Third-Party Licenses",
                "License files are added when the app is built "
                "(./build.sh); this copy has none.")
            return
        subprocess.run(["open", "-R", notices], check=False)

    def _copy_diagnostics(self):
        text = applog.diagnostics(get_ffmpeg_path(), ffmpeg_version_line())
        QApplication.clipboard().setText(text)
        QMessageBox.information(
            self, "Diagnostics Copied",
            "Version, system, ffmpeg and the recent log are on the clipboard. "
            "Paste them into your bug report.\n\n"
            "The log contains the names and folders of files you processed.")

    def _show_about(self):
        QMessageBox.about(
            self, "About Video Uniqualizer",
            f"Video Uniqualizer {DISPLAY_VERSION}\n\n{applog.system_summary()}\n"
            f"{ffmpeg_version_line()}\n\n"
            "Includes FFmpeg and other open-source libraries under the GPL and "
            "other licenses. See Help → Third-Party Licenses.")

    def closeEvent(self, event: QCloseEvent):
        """Stop a running batch before the window goes away.

        Letting the window close mid-batch destroys the QThread while it runs,
        which aborts the process, and leaves ffmpeg children writing
        half-finished files. Instead the batch is cancelled — the engine kills
        ffmpeg and deletes partial outputs — and the window closes itself when
        the worker reports back.
        """
        if self.scanner is not None:
            self.scanner.cancel()
            self.scanner.wait()
            self.scanner = None
        if self.worker is None or not self.worker.isRunning():
            self._save_settings()
            event.accept()
            return
        event.ignore()
        if self._quit_after_worker:
            return  # already stopping; a second Cmd+Q must not re-prompt
        answer = QMessageBox.question(
            self, "Processing Is Running",
            "Stop processing and quit?\n\n"
            "Files that are already finished are kept; the ones in progress "
            "are deleted.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        log.info("quit requested during batch; cancelling")
        self._quit_after_worker = True
        self._cancel_processing()

    def _close_if_quitting(self) -> bool:
        if not self._quit_after_worker:
            return False
        QTimer.singleShot(0, self.close)
        return True

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 8, 20, 8)

        # Icon
        icon_label = QLabel()
        pm = app_icon(512).pixmap(QSize(40, 40))
        icon_label.setPixmap(pm)
        lay.addWidget(icon_label)

        # Titles
        titles = QVBoxLayout()
        titles.setSpacing(0)
        t1 = QLabel("Video Uniqualizer")
        t1.setObjectName("appTitle")
        titles.addWidget(t1)
        t2 = QLabel(f"Version {DISPLAY_VERSION}")
        t2.setObjectName("appSubtitle")
        titles.addWidget(t2)
        lay.addLayout(titles)
        lay.addStretch()

        # Presets: named snapshots of every parameter, kept in QSettings.
        self.combo_presets = QComboBox()
        self.combo_presets.setMinimumWidth(160)
        self.combo_presets.setToolTip("Load a saved set of parameters")
        self.combo_presets.activated.connect(self._load_selected_preset)
        lay.addWidget(self.combo_presets)

        btn_save_preset = QPushButton("Save Preset…")
        btn_save_preset.clicked.connect(self._save_preset)
        lay.addWidget(btn_save_preset)

        self.btn_delete_preset = QPushButton("Delete")
        self.btn_delete_preset.clicked.connect(self._delete_preset)
        lay.addWidget(self.btn_delete_preset)

        btn_reset = QPushButton("Reset Defaults")
        btn_reset.setIcon(settings_icon(32))
        btn_reset.clicked.connect(self._reset_params)
        lay.addWidget(btn_reset)

        return bar

    def _build_file_section(self) -> QWidget:
        card, layout = make_panel()

        # Input files row
        input_header = QHBoxLayout()
        lbl = QLabel("Input Files")
        lbl.setObjectName("panelTitle")
        input_header.addWidget(lbl)
        input_header.addStretch()

        btn_add = QPushButton("  Select Files")
        btn_add.setIcon(file_select_icon(32))
        btn_add.setIconSize(QSize(24, 24))
        btn_add.clicked.connect(self._select_files)
        input_header.addWidget(btn_add)

        btn_remove = QPushButton("  Remove Selected")
        btn_remove.setIcon(remove_icon(24))
        btn_remove.setIconSize(QSize(18, 18))
        btn_remove.clicked.connect(self._remove_selected)
        input_header.addWidget(btn_remove)

        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self._clear_files)
        input_header.addWidget(btn_clear)
        # Locked while a batch runs: the status column is written back to these
        # rows by path, and the worker holds its own copy of the list anyway.
        self._list_edit_buttons = [btn_add, btn_remove, btn_clear]

        layout.addLayout(input_header)

        # File list with drag & drop
        self.file_list = MediaFileList()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setMinimumHeight(120)
        self.file_list.setMaximumHeight(200)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.file_list.files_dropped.connect(self._add_files)
        self.file_list.folders_dropped.connect(self._scan_folders)
        # A drag-reorder rewrites the model behind our back; processing order and
        # `input_files` have to follow it.
        self.file_list.model().rowsMoved.connect(
            lambda *_: self._sync_files_from_list())
        self.file_list.model().rowsRemoved.connect(
            lambda *_: self._sync_files_from_list())
        self.file_list.model().rowsInserted.connect(
            lambda *_: self._sync_files_from_list())
        layout.addWidget(self.file_list)

        self.file_count_label = QLabel(
            "No files selected — drag & drop videos or images here")
        self.file_count_label.setObjectName("statusLabel")
        layout.addWidget(self.file_count_label)

        layout.addWidget(make_separator())

        # Output folder
        output_header = QHBoxLayout()
        lbl2 = QLabel("Output Folder")
        lbl2.setObjectName("panelTitle")
        output_header.addWidget(lbl2)
        output_header.addStretch()

        self.btn_show_output = QPushButton("Show in Finder")
        self.btn_show_output.clicked.connect(self._show_output_folder)
        output_header.addWidget(self.btn_show_output)

        btn_output = QPushButton("  Select Folder")
        btn_output.setIcon(output_folder_icon(32))
        btn_output.setIconSize(QSize(24, 24))
        btn_output.clicked.connect(self._select_output)
        output_header.addWidget(btn_output)
        layout.addLayout(output_header)

        self.output_label = QLabel("No folder selected")
        self.output_label.setObjectName("statusLabel")
        layout.addWidget(self.output_label)

        return card

    def _build_params_section(self) -> QWidget:
        card, layout = make_panel("Parameters")

        self.params_panel = ParamsPanel()
        layout.addWidget(self.params_panel)

        return card

    def _build_action_section(self) -> QWidget:
        card, layout = make_panel()

        # One bar for the whole batch. It counts the partial progress of files
        # in flight, so it moves during a long encode, and it feeds the ETA.
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setFixedHeight(28)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_process = QPushButton("  Uniqualize!")
        self.btn_process.setObjectName("btnProcess")
        self.btn_process.setIcon(process_icon(48))
        self.btn_process.setIconSize(QSize(28, 28))
        self.btn_process.clicked.connect(lambda: self._start_processing())
        btn_row.addWidget(self.btn_process)

        self.btn_retry = QPushButton("Retry Failed")
        self.btn_retry.setToolTip("Process again only the files marked ✗")
        self.btn_retry.setVisible(False)
        self.btn_retry.clicked.connect(self._retry_failed)
        btn_row.addWidget(self.btn_retry)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btnCancel")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel_processing)
        btn_row.addWidget(self.btn_cancel)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        return card

    # ─── Settings & presets ──────────────────────────────

    def _restore_settings(self):
        s = settings_store.settings()
        geometry = s.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        folder = s.value("output_folder", "", type=str)
        if folder and os.path.isdir(folder):
            self.output_folder = folder
            self.output_label.setToolTip(folder)
            self._refresh_output_label()
        state = settings_store.load_last_state()
        if state:
            self.params_panel.set_state(state)
        self.params_panel.chk_advanced.setChecked(s.value("advanced", False, type=bool))
        self._refresh_presets()

    def _save_settings(self):
        s = settings_store.settings()
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("output_folder", self.output_folder)
        s.setValue("advanced", self.params_panel.chk_advanced.isChecked())
        settings_store.save_last_state(self.params_panel.get_state())
        s.sync()

    def _refresh_presets(self, select: str = ""):
        self.combo_presets.blockSignals(True)
        self.combo_presets.clear()
        self.combo_presets.addItem("Presets", None)
        for name in settings_store.list_presets():
            self.combo_presets.addItem(name, name)
        idx = self.combo_presets.findData(select) if select else 0
        self.combo_presets.setCurrentIndex(max(idx, 0))
        self.combo_presets.blockSignals(False)
        self.btn_delete_preset.setEnabled(bool(select))

    def _load_selected_preset(self):
        name = self.combo_presets.currentData()
        self.btn_delete_preset.setEnabled(bool(name))
        if not name:
            return
        state = settings_store.load_preset(name)
        if state is None:
            QMessageBox.warning(
                self, "Preset Unavailable",
                f"“{name}” was saved by an incompatible version and cannot be loaded.")
            return
        self.params_panel.set_state(state)
        self.status_label.setText(f"Loaded preset “{name}”.")
        log.info("loaded preset %r", name)

    def _save_preset(self):
        current = self.combo_presets.currentData() or ""
        name, ok = QInputDialog.getText(self, "Save Preset", "Preset name:", text=current)
        name = name.strip()
        if not ok or not name:
            return
        # QSettings treats slashes as key separators.
        if "/" in name or "\\" in name:
            QMessageBox.warning(self, "Invalid Name", "Preset names cannot contain / or \\.")
            return
        if name in settings_store.list_presets():
            answer = QMessageBox.question(
                self, "Replace Preset?", f"A preset named “{name}” exists. Replace it?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes:
                return
        settings_store.save_preset(name, self.params_panel.get_state())
        self._refresh_presets(select=name)
        self.status_label.setText(f"Saved preset “{name}”.")
        log.info("saved preset %r", name)

    def _delete_preset(self):
        name = self.combo_presets.currentData()
        if not name:
            return
        answer = QMessageBox.question(
            self, "Delete Preset?", f"Delete the preset “{name}”?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        settings_store.delete_preset(name)
        self._refresh_presets()
        log.info("deleted preset %r", name)

    # ─── Per-file status ─────────────────────────────────

    _STATUS_ROLE = Qt.ItemDataRole.UserRole + 1
    _STATUS_STYLE = {
        # status: (prefix, colour or None)
        "pending": ("", None),
        # Picked for contrast on the dark list background.
        "running": ("▶ ", "#6FA8DC"),
        "ok": ("✓ ", "#5BC47A"),
        "failed": ("✗ ", "#E5605A"),
        "skipped": ("– ", "#8A8A96"),
    }

    def _items_by_path(self) -> dict:
        return {self.file_list.item(r).data(Qt.ItemDataRole.UserRole): self.file_list.item(r)
                for r in range(self.file_list.count())}

    def _set_status(self, item, status: str, detail: str = ""):
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        prefix, colour = self._STATUS_STYLE[status]
        item.setData(self._STATUS_ROLE, status)
        item.setText(prefix + os.path.basename(path))
        # None clears the override, so a pending row goes back to the style's colour.
        item.setData(Qt.ItemDataRole.ForegroundRole, QColor(colour) if colour else None)
        item.setToolTip(f"{path}\n{detail}" if detail else path)

    def _failed_paths(self) -> list:
        return [p for p, item in self._items_by_path().items()
                if item.data(self._STATUS_ROLE) == "failed"]

    def _show_output_folder(self):
        if self.output_folder and os.path.isdir(self.output_folder):
            subprocess.run(["open", self.output_folder], check=False)

    # ─── Slots ───────────────────────────────────────────

    def _scan_folders(self, folders: list):
        if self.scanner is not None:
            self.status_label.setText("Still scanning the previous folder — drop again when it finishes.")
            return
        self.status_label.setText("Scanning folder for videos and images…")
        self.scanner = FolderScanWorker(folders, skip_dir=self.output_folder)
        self.scanner.scanned.connect(self._on_folders_scanned)
        self._update_button_states()
        self.scanner.start()

    def _on_folders_scanned(self, paths: list, truncated: bool):
        if self.scanner is not None:
            self.scanner.wait()
            self.scanner = None
        before = len(self.input_files)
        self._add_files(paths)
        added = len(self.input_files) - before
        if not paths:
            self.status_label.setText("No videos or images found in that folder.")
        elif truncated:
            self.status_label.setText(
                f"Added {added} files — stopped at {MAX_SCAN_FILES}. "
                "Drop a smaller folder to add the rest.")
        else:
            self.status_label.setText(f"Added {added} files from the folder.")
        self._update_button_states()

    def _add_files(self, paths: list):
        """Add files from drag-drop or file dialog, deduplicating.

        Duplicates are matched on the resolved path, so the same file reached
        through a symlink or a second dropped parent folder is listed once
        rather than processed twice.
        """
        existing = {os.path.realpath(f) for f in self.input_files}
        # Each addItem fires rowsInserted, which would rebuild input_files and
        # the button states once per row: quadratic, and seconds of frozen UI
        # for a 2000-file folder. One sync at the end is enough.
        self._bulk_adding = True
        try:
            self._add_items(paths, existing)
        finally:
            self._bulk_adding = False
        self._sync_files_from_list()

    def _add_items(self, paths: list, existing: set):
        for p in paths:
            if not _is_candidate(p):
                continue
            real = os.path.realpath(p)
            if real in existing:
                continue
            existing.add(real)
            item = QListWidgetItem(os.path.basename(p))
            item.setToolTip(p)
            # The path lives on the item, not in a parallel list indexed by row:
            # the list is InternalMove, so rows reorder without the model ever
            # telling `input_files` about it.
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.file_list.addItem(item)

    def _sync_files_from_list(self):
        """Rebuild ``input_files`` from the widget — the widget is the truth."""
        if getattr(self, "_bulk_adding", False):
            return
        paths = []
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            path = item.data(Qt.ItemDataRole.UserRole) if item else None
            if path:
                paths.append(path)
        self.input_files = paths
        self._update_file_count()
        self._refresh_output_label()
        self._update_button_states()

    def _select_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Video or Image Files",
            "",
            "Media Files (*.mp4 *.mov *.avi *.mkv *.webm *.flv *.wmv "
            "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff);;"
            "Video Files (*.mp4 *.mov *.avi *.mkv *.webm *.flv *.wmv);;"
            "Image Files (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff);;"
            "All Files (*)"
        )
        if paths:
            self._add_files(paths)

    def _remove_selected(self):
        for item in self.file_list.selectedItems():
            self.file_list.takeItem(self.file_list.row(item))
        self._sync_files_from_list()

    def _clear_files(self):
        self.file_list.clear()
        self._sync_files_from_list()

    def _select_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_folder = folder
            self.output_label.setToolTip(folder)
            self._refresh_output_label()
            self._update_button_states()

    def _refresh_output_label(self):
        # The file list is wired up before this label exists, and its signals can
        # fire during that window.
        if not hasattr(self, "output_label"):
            return
        if not self.output_folder:
            self.output_label.setText("No folder selected")
            return
        text = self.output_folder
        if self._output_folder_holds_inputs():
            text += "  ⚠︎ also an input folder — outputs land next to sources"
        self.output_label.setText(text)

    def _reset_params(self):
        self.params_panel.load_defaults()
        self._refresh_presets()

    def _update_file_count(self):
        n = len(self.input_files)
        if n == 0:
            self.file_count_label.setText(
                "No files selected — drag & drop videos or images here")
        else:
            stills = sum(1 for f in self.input_files if _is_still(f))
            if stills and stills != n:
                self.file_count_label.setText(
                    f"{n} files selected ({n - stills} video, {stills} image)")
            elif stills:
                self.file_count_label.setText(
                    f"{n} image{'s' if n != 1 else ''} selected")
            else:
                self.file_count_label.setText(
                    f"{n} video{'s' if n != 1 else ''} selected")

    def _update_button_states(self):
        running = self.worker is not None
        can_start = len(self.input_files) > 0 and bool(self.output_folder)
        self.btn_process.setEnabled(can_start and not running and self.scanner is None)
        for btn in getattr(self, "_list_edit_buttons", []):
            btn.setEnabled(not running)
        self.file_list.setAcceptDrops(not running)
        self.btn_show_output.setEnabled(bool(self.output_folder) and os.path.isdir(self.output_folder))
        self.btn_retry.setVisible(not running and bool(self._failed_paths()))

    def _cleanup_worker(self):
        """Safely wait for and discard worker thread."""
        if self.worker is not None:
            self.worker.wait(5000)
            self.worker = None

    def _output_folder_holds_inputs(self) -> bool:
        """True when outputs would land in a folder a source file came from.

        That folder is what the user drags in next time, so the outputs get
        re-uniqualized as if they were sources — a second generation loss, and a
        batch where some files are two passes deep.
        """
        if not self.output_folder:
            return False
        try:
            out = os.path.realpath(self.output_folder)
        except OSError:
            return False
        for f in self.input_files:
            try:
                if os.path.realpath(os.path.dirname(f)) == out:
                    return True
            except OSError:
                continue
        return False

    def _confirm_output_folder(self) -> bool:
        if not self._output_folder_holds_inputs():
            return True
        answer = QMessageBox.warning(
            self,
            "Output Folder Is Also an Input Folder",
            "The output folder already holds some of the input files.\n\n"
            "New files land next to their sources, so the next batch you drag "
            "in from this folder will re-process the output of this one.\n\n"
            "Process anyway?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _confirm_disk_space(self, files) -> bool:
        """Check the output volume can plausibly hold the batch.

        The estimate is 1.5x the input size: a re-encode at the default CRF
        lands near the source for camera footage, but can exceed a heavily
        compressed source, and a PNG out of a JPEG is several times bigger.
        Running out mid-batch still stops cleanly (the engine detects it and
        skips the rest), so this only warns — except when the disk is already
        effectively full.
        """
        try:
            free = shutil.disk_usage(self.output_folder).free
        except OSError as exc:
            log.warning("disk_usage failed for %s: %s", self.output_folder, exc)
            return True
        total_in = 0
        for f in files:
            try:
                total_in += os.path.getsize(f)
            except OSError:
                pass
        needed = int(total_in * 1.5)
        log.info("disk check: %s free, ~%s needed", _format_bytes(free), _format_bytes(needed))
        if free < 200 * 1024 * 1024:
            QMessageBox.critical(
                self, "Output Disk Is Full",
                f"Only {_format_bytes(free)} is free on the output disk.\n\n"
                "Choose another output folder or free up space.")
            return False
        if free >= needed:
            return True
        answer = QMessageBox.warning(
            self, "Output Disk May Run Out of Space",
            f"The batch may need about {_format_bytes(needed)}, but only "
            f"{_format_bytes(free)} is free on the output disk.\n\n"
            "If it fills up, processing stops and the unfinished files are "
            "listed as errors. Process anyway?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _start_processing(self, files=None):
        files = list(files if files is not None else self.input_files)
        if not files or not self.output_folder:
            return

        if not self._confirm_output_folder():
            return
        if not self._confirm_disk_space(files):
            return
        if not self._confirm_overlay():
            return
        # Saved at start, not only on quit, so a crash mid-batch keeps them.
        self._save_settings()

        self.worker = ProcessWorker(
            files=files,
            output_folder=self.output_folder,
            params=self.params_panel.build_params(),
            ranges=self.params_panel.build_ranges(),
            workers=self.params_panel.worker_count(),
            image_params=self.params_panel.build_image_params(),
            image_ranges=self.params_panel.build_image_ranges(),
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.overall_progress.connect(self._on_overall_progress)
        self.worker.file_started.connect(self._on_file_started)
        self.worker.file_result.connect(self._on_file_result)
        self.worker.job_finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)

        self._batch_files = files
        self._results = {}
        self._active = []
        self._done_count = 0
        self._batch_started = time.monotonic()
        items = self._items_by_path()
        for path in files:
            self._set_status(items.get(path), "pending")

        self.btn_process.setVisible(False)
        self.btn_cancel.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat(f"0/{len(files)} done")
        self.status_label.setText("Starting…")
        self._update_button_states()

        self.worker.start()

    def _confirm_overlay(self) -> bool:
        overlay = self.params_panel._overlay_file
        if not overlay or os.path.isfile(overlay):
            return True
        answer = QMessageBox.warning(
            self, "Overlay Not Found",
            f"The overlay video is gone:\n{overlay}\n\n"
            "Process without an overlay?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.params_panel._clear_overlay()
        return True

    def _retry_failed(self):
        failed = self._failed_paths()
        if failed:
            log.info("retrying %d failed files", len(failed))
            self._start_processing(failed)

    def _cancel_processing(self):
        if self.worker:
            self.worker.cancel()
            self.status_label.setText("Cancelling (waiting for ffmpeg to stop)…")
            self.btn_cancel.setEnabled(False)

    def _on_progress(self, completed, total, filename):
        self._done_count = completed

    def _on_overall_progress(self, fraction: float):
        total = len(self._batch_files)
        self.progress_bar.setValue(int(fraction * 1000))
        text = f"{self._done_count}/{total} done · {int(fraction * 100)}%"
        eta = self._eta_text(fraction)
        self.progress_bar.setFormat(text + (f" · {eta}" if eta else ""))

    def _eta_text(self, fraction: float) -> str:
        """Linear extrapolation from elapsed time; hidden until it means something.

        Before ~3% or 5 seconds the estimate is dominated by startup (probing,
        the first keyframes) and swings wildly, so nothing is shown.
        """
        elapsed = time.monotonic() - self._batch_started
        if fraction < 0.03 or elapsed < 5 or fraction >= 1.0:
            return ""
        remaining = elapsed * (1.0 - fraction) / fraction
        if remaining < 60:
            return "less than a minute left"
        if remaining < 3600:
            return f"about {round(remaining / 60)} min left"
        return f"about {remaining / 3600:.1f} h left"

    def _on_file_started(self, path: str):
        self._active.append(path)
        self._set_status(self._items_by_path().get(path), "running")
        self._show_active()

    def _on_file_result(self, path: str, ok: bool, error: str):
        if path in self._active:
            self._active.remove(path)
        self._results[path] = (ok, error)
        item = self._items_by_path().get(path)
        if ok:
            self._set_status(item, "ok")
        elif error == "cancelled":
            self._set_status(item, "skipped", "Cancelled")
        else:
            self._set_status(item, "failed", error)
        self._show_active()

    def _show_active(self):
        if not self._active:
            return
        names = [os.path.basename(p) for p in self._active]
        shown = ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
        self.status_label.setText(f"Processing: {shown}")

    def _finish_batch_ui(self):
        """Common teardown for a batch that ended, however it ended."""
        items = self._items_by_path()
        for path in self._batch_files:
            if path not in self._results:
                # Never started (cancelled, or the disk filled) or never reported.
                self._set_status(items.get(path), "skipped", "Not processed")
        self._active = []
        self.btn_process.setVisible(True)
        self.btn_cancel.setVisible(False)
        self.btn_cancel.setEnabled(True)
        self._cleanup_worker()
        self._update_button_states()

    def _on_finished(self, success_count, was_cancelled, errors):
        total = len(self._batch_files)
        self._finish_batch_ui()
        if self._close_if_quitting():
            return

        failed = sum(1 for ok, err in self._results.values() if not ok and err != "cancelled")
        elapsed = time.monotonic() - self._batch_started
        took = f"{elapsed:.0f} s" if elapsed < 90 else f"{elapsed / 60:.1f} min"
        if was_cancelled:
            self.progress_bar.setValue(int(success_count / total * 1000) if total else 0)
            self.progress_bar.setFormat(f"Cancelled — {success_count}/{total} processed")
            self.status_label.setText("")
            return

        self.progress_bar.setValue(1000)
        self.progress_bar.setFormat(f"Done — {success_count}/{total} processed in {took}")
        self.status_label.setText(
            f"{failed} file(s) failed — hover a ✗ row for the reason, or use Retry Failed."
            if failed else "")

        msg = f"Processed {success_count} of {total} files in {took}."
        if errors:
            msg += f"\n\nErrors ({len(errors)}):\n" + "\n".join(errors[:10])
            if len(errors) > 10:
                msg += f"\n... and {len(errors) - 10} more"
            msg += "\n\nFailed files are marked ✗ in the list. Full details are in the log (Help → Show Log in Finder)."
        box = QMessageBox(QMessageBox.Icon.Information if not errors else QMessageBox.Icon.Warning,
                          "Processing Complete", msg, parent=self)
        show = box.addButton("Show in Finder", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is show:
            self._show_output_folder()

    def _on_error(self, error_msg):
        self._finish_batch_ui()
        self.progress_bar.setFormat("Error!")
        self.status_label.setText(f"Error: {error_msg}")
        if self._close_if_quitting():
            return

        QMessageBox.critical(
            self, "Error",
            f"Processing failed:\n{error_msg}\n\n"
            "Help → Copy Diagnostics collects the details for a bug report.")


# ─── Entry Point ────────────────────────────────────────

def _ffmpeg_missing_text(err: str) -> str:
    if getattr(sys, "frozen", False):
        # The bundle ships its own ffmpeg; telling a tester to install one
        # would send them after the wrong problem.
        fix = ("The ffmpeg bundled inside the app failed to start. This is a "
               "build problem, not something to fix on this Mac — please "
               "report it together with the log.")
    else:
        fix = "Install ffmpeg: brew install ffmpeg"
    path = applog.log_path()
    where = f"\n\nLog: {path}" if path else ""
    return f"Video Uniqualizer cannot process files.\n\n{err}\n\n{fix}{where}"


def main():
    applog.setup_logging()
    _install_exception_hook()
    log.info("start: %s", applog.system_summary())
    log.info("ffmpeg: %s — %s", get_ffmpeg_path(), ffmpeg_version_line())

    if "--benchmark" in sys.argv:
        ok, err = check_ffmpeg_available()
        if not ok:
            print(f"ffmpeg unavailable: {err}", file=sys.stderr)
            sys.exit(1)
        print("Video Uniqualizer macOS encoder benchmark")
        for row in benchmark_video_encoders():
            if not row["available"]:
                print(f"- {row['encoder']}: unavailable")
            elif row["error"]:
                print(f"- {row['encoder']}: failed after {row['seconds']}s: {row['error']}")
            else:
                print(f"- {row['encoder']}: {row['seconds']}s, {row['fps']} fps, {row['size_mb']} MB")
        return

    app = QApplication(sys.argv)
    app.setApplicationName("Video Uniqualizer")
    app.setApplicationDisplayName("Video Uniqualizer")
    app.setApplicationVersion(DISPLAY_VERSION)
    app.setStyleSheet(MAIN_STYLESHEET)
    app.setWindowIcon(app_icon(512))

    global _error_relay
    _error_relay = _ErrorRelay()
    _error_relay.show.connect(_show_error_dialog)

    ok, err = check_ffmpeg_available()
    if not ok:
        log.error("ffmpeg check failed: %s", err)
        QMessageBox.critical(None, "ffmpeg Unavailable", _ffmpeg_missing_text(err))
        sys.exit(1)

    window = MainWindow()
    window.show()

    code = app.exec()
    log.info("exit (%s)", code)
    sys.exit(code)


if __name__ == "__main__":
    main()
