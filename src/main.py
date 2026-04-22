"""
Video Uniqualizer — macOS Application
Main window with skeuomorphic design.
"""

import sys
import os
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QFileDialog,
    QProgressBar, QGroupBox, QDoubleSpinBox, QSpinBox, QCheckBox,
    QComboBox, QTabWidget, QScrollArea, QFrame, QSizePolicy,
    QAbstractItemView, QGridLayout, QMessageBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QIcon, QFont, QDragEnterEvent, QDropEvent

from engine import UniqueParams, process_batch
from icons import (
    app_icon, file_select_icon, output_folder_icon,
    process_icon, settings_icon, remove_icon
)
from styles import MAIN_STYLESHEET


# ─── Worker Thread ──────────────────────────────────────

class ProcessWorker(QThread):
    progress = pyqtSignal(int, int, str)   # current, total, filename
    finished = pyqtSignal(int, bool)        # success_count, was_cancelled
    error = pyqtSignal(str)

    def __init__(self, files, output_folder, params, parent=None):
        super().__init__(parent)
        self.files = files
        self.output_folder = output_folder
        self.params = params
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def is_cancelled(self):
        return self._cancelled

    def run(self):
        try:
            count = process_batch(
                input_files=self.files,
                output_folder=self.output_folder,
                params_template=self.params,
                randomize_each=True,
                progress_callback=lambda cur, tot, fn: self.progress.emit(cur, tot, fn),
                cancelled=self.is_cancelled,
            )
            self.finished.emit(count, self._cancelled)
        except Exception as e:
            self.error.emit(str(e))


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

        tabs = QTabWidget()
        tabs.addTab(self._build_geometry_tab(), "Geometry")
        tabs.addTab(self._build_color_tab(), "Color")
        tabs.addTab(self._build_effects_tab(), "Effects")
        tabs.addTab(self._build_output_tab(), "Output")
        main_layout.addWidget(tabs)

    def _build_geometry_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        self.spin_zoom_min = QDoubleSpinBox()
        self.spin_zoom_min.setRange(1.0, 1.2)
        self.spin_zoom_min.setDecimals(3)
        self.spin_zoom_min.setSingleStep(0.005)
        layout.addLayout(make_param_row("Zoom Min:", self.spin_zoom_min, "Minimum random zoom factor"))

        self.spin_zoom_max = QDoubleSpinBox()
        self.spin_zoom_max.setRange(1.0, 1.2)
        self.spin_zoom_max.setDecimals(3)
        self.spin_zoom_max.setSingleStep(0.005)
        layout.addLayout(make_param_row("Zoom Max:", self.spin_zoom_max, "Maximum random zoom factor"))

        self.spin_rotate_max = QDoubleSpinBox()
        self.spin_rotate_max.setRange(0.0, 5.0)
        self.spin_rotate_max.setDecimals(2)
        self.spin_rotate_max.setSingleStep(0.1)
        layout.addLayout(make_param_row("Rotate Max (°):", self.spin_rotate_max, "Max rotation angle in degrees (+/-)"))

        self.spin_k1_max = QDoubleSpinBox()
        self.spin_k1_max.setRange(0.0, 0.05)
        self.spin_k1_max.setDecimals(4)
        self.spin_k1_max.setSingleStep(0.001)
        layout.addLayout(make_param_row("Lens K1 Max:", self.spin_k1_max, "Max barrel/pincushion distortion (+/-)"))

        self.spin_crop_min = QSpinBox()
        self.spin_crop_min.setRange(0, 40)
        layout.addLayout(make_param_row("Crop Margin Min (px):", self.spin_crop_min, "Min micro-crop pixels"))

        self.spin_crop_max = QSpinBox()
        self.spin_crop_max.setRange(0, 40)
        layout.addLayout(make_param_row("Crop Margin Max (px):", self.spin_crop_max, "Max micro-crop pixels"))

        self.chk_hflip = QCheckBox("Enable random horizontal flip (50% chance)")
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
        self.spin_hue_max.setRange(0.0, 30.0)
        self.spin_hue_max.setDecimals(1)
        self.spin_hue_max.setSingleStep(1.0)
        layout.addLayout(make_param_row("Hue Shift Max (°):", self.spin_hue_max, "Max hue rotation (+/-)"))

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
        self.spin_speed_min.setRange(0.8, 1.0)
        self.spin_speed_min.setDecimals(4)
        self.spin_speed_min.setSingleStep(0.005)
        layout.addLayout(make_param_row("Video Speed Min:", self.spin_speed_min))

        self.spin_speed_max = QDoubleSpinBox()
        self.spin_speed_max.setRange(1.0, 1.2)
        self.spin_speed_max.setDecimals(4)
        self.spin_speed_max.setSingleStep(0.005)
        layout.addLayout(make_param_row("Video Speed Max:", self.spin_speed_max))

        self.spin_pitch_min = QDoubleSpinBox()
        self.spin_pitch_min.setRange(0.8, 1.0)
        self.spin_pitch_min.setDecimals(4)
        self.spin_pitch_min.setSingleStep(0.005)
        layout.addLayout(make_param_row("Audio Pitch Min:", self.spin_pitch_min))

        self.spin_pitch_max = QDoubleSpinBox()
        self.spin_pitch_max.setRange(1.0, 1.2)
        self.spin_pitch_max.setDecimals(4)
        self.spin_pitch_max.setSingleStep(0.005)
        layout.addLayout(make_param_row("Audio Pitch Max:", self.spin_pitch_max))

        self.spin_adelay_min = QSpinBox()
        self.spin_adelay_min.setRange(0, 500)
        layout.addLayout(make_param_row("Audio Delay Min (ms):", self.spin_adelay_min))

        self.spin_adelay_max = QSpinBox()
        self.spin_adelay_max.setRange(0, 500)
        layout.addLayout(make_param_row("Audio Delay Max (ms):", self.spin_adelay_max))

        layout.addStretch()
        return w

    def _build_output_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(8)

        self.spin_width = QSpinBox()
        self.spin_width.setRange(320, 7680)
        self.spin_width.setSingleStep(10)
        layout.addLayout(make_param_row("Target Width:", self.spin_width))

        self.spin_height = QSpinBox()
        self.spin_height.setRange(320, 7680)
        self.spin_height.setSingleStep(10)
        layout.addLayout(make_param_row("Target Height:", self.spin_height))

        self.spin_crf = QSpinBox()
        self.spin_crf.setRange(0, 51)
        layout.addLayout(make_param_row("CRF (quality):", self.spin_crf, "0=lossless, 23=default, 51=worst"))

        self.spin_gop_min = QSpinBox()
        self.spin_gop_min.setRange(10, 300)
        layout.addLayout(make_param_row("GOP Size Min:", self.spin_gop_min, "Min keyframe interval (Broken GOP)"))

        self.spin_gop_max = QSpinBox()
        self.spin_gop_max.setRange(10, 300)
        layout.addLayout(make_param_row("GOP Size Max:", self.spin_gop_max, "Max keyframe interval (Broken GOP)"))

        self.combo_preset = QComboBox()
        self.combo_preset.addItems(["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"])
        layout.addLayout(make_param_row("Encoding Preset:", self.combo_preset))

        self.chk_fake_meta = QCheckBox("Write fake camera metadata (EXIF)")
        layout.addWidget(self.chk_fake_meta)

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
        if path:
            self._overlay_file = path
            self.overlay_path_label.setText(os.path.basename(path))
            self.overlay_path_label.setToolTip(path)

    def _clear_overlay(self):
        self._overlay_file = ""
        self.overlay_path_label.setText("No overlay selected")
        self.overlay_path_label.setToolTip("")

    def load_defaults(self):
        """Load the V9.0 script defaults."""
        self.spin_zoom_min.setValue(1.02)
        self.spin_zoom_max.setValue(1.05)
        self.spin_rotate_max.setValue(0.7)
        self.spin_k1_max.setValue(0.008)
        self.spin_crop_min.setValue(4)
        self.spin_crop_max.setValue(16)
        self.chk_hflip.setChecked(False)
        self.spin_trim_start_max.setValue(0.6)
        self.spin_trim_end_max.setValue(0.5)

        self.spin_color_shift.setValue(0.10)
        self.spin_contrast_min.setValue(0.93)
        self.spin_contrast_max.setValue(1.07)
        self.spin_sat_min.setValue(0.88)
        self.spin_sat_max.setValue(1.12)
        self.spin_bright_max.setValue(0.04)
        self.spin_hue_max.setValue(6.0)

        self.spin_noise_min.setValue(4)
        self.spin_noise_max.setValue(12)
        self.spin_unsharp_min.setValue(-0.4)
        self.spin_unsharp_max.setValue(0.6)
        self.spin_vignette_min.setValue(0.05)
        self.spin_vignette_max.setValue(0.25)
        self.spin_speed_min.setValue(0.97)
        self.spin_speed_max.setValue(1.08)
        self.spin_pitch_min.setValue(0.96)
        self.spin_pitch_max.setValue(1.04)
        self.spin_adelay_min.setValue(30)
        self.spin_adelay_max.setValue(200)

        self.spin_width.setValue(1080)
        self.spin_height.setValue(1920)
        self.spin_crf.setValue(24)
        self.spin_gop_min.setValue(30)
        self.spin_gop_max.setValue(90)
        self.combo_preset.setCurrentText("fast")
        self.chk_fake_meta.setChecked(True)

        self.spin_ov_opacity_min.setValue(0.04)
        self.spin_ov_opacity_max.setValue(0.11)
        self._overlay_file = ""
        self.overlay_path_label.setText("No overlay selected")

    def build_params(self) -> UniqueParams:
        """Build a UniqueParams template from current UI values."""
        import random
        from engine import clamp

        p = UniqueParams()
        p.k1 = round(random.uniform(-self.spin_k1_max.value(), self.spin_k1_max.value()), 4)
        p.rotate = round(random.uniform(-self.spin_rotate_max.value(), self.spin_rotate_max.value()), 3)
        p.zoom = round(random.uniform(self.spin_zoom_min.value(), self.spin_zoom_max.value()), 3)
        lo, hi = self.spin_crop_min.value(), self.spin_crop_max.value()
        p.crop_margin = random.randint(min(lo, hi), max(lo, hi))

        cs = self.spin_color_shift.value()
        p.rs = round(random.uniform(-cs, cs), 3)
        p.gs = round(random.uniform(-cs, cs), 3)
        p.bs = round(random.uniform(-cs, cs), 3)
        p.contrast = round(random.uniform(self.spin_contrast_min.value(), self.spin_contrast_max.value()), 3)
        p.saturation = round(random.uniform(self.spin_sat_min.value(), self.spin_sat_max.value()), 3)
        p.brightness = round(random.uniform(-self.spin_bright_max.value(), self.spin_bright_max.value()), 3)
        p.hue_shift = round(random.uniform(-self.spin_hue_max.value(), self.spin_hue_max.value()), 1)

        p.unsharp_amount = round(random.uniform(self.spin_unsharp_min.value(), self.spin_unsharp_max.value()), 2)
        lo, hi = self.spin_noise_min.value(), self.spin_noise_max.value()
        p.noise_strength = random.randint(min(lo, hi), max(lo, hi))
        p.noise_flags = random.choice(["t", "u", "t+u"])
        p.do_hflip = self.chk_hflip.isChecked() and random.choice([True, False])
        p.vignette_angle = round(random.uniform(self.spin_vignette_min.value(), self.spin_vignette_max.value()), 2)

        p.video_speed = round(random.uniform(self.spin_speed_min.value(), self.spin_speed_max.value()), 4)

        p.trim_start = round(random.uniform(0.1, self.spin_trim_start_max.value()), 2)
        p.trim_end = round(random.uniform(0.1, self.spin_trim_end_max.value()), 2)

        p.pitch = clamp(round(random.uniform(self.spin_pitch_min.value(), self.spin_pitch_max.value()), 4), 0.9, 1.1)
        lo, hi = self.spin_adelay_min.value(), self.spin_adelay_max.value()
        p.adelay_ms = random.randint(min(lo, hi), max(lo, hi))

        p.overlay_file = self._overlay_file
        p.opacity = round(random.uniform(self.spin_ov_opacity_min.value(), self.spin_ov_opacity_max.value()), 2)
        p.ov_speed = round(random.uniform(0.88, 1.12), 2)

        p.target_width = self.spin_width.value()
        p.target_height = self.spin_height.value()
        p.crf = self.spin_crf.value()
        lo, hi = self.spin_gop_min.value(), self.spin_gop_max.value()
        p.gop_size = random.randint(min(lo, hi), max(lo, hi))
        p.preset = self.combo_preset.currentText()
        p.fake_meta = self.chk_fake_meta.isChecked()

        return p


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
        self.input_files = []
        self.output_folder = ""

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

        self._update_button_states()

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
        t2 = QLabel("HYBRID V9.0 GOD MODE — MAXIMUM UNIQUE")
        t2.setObjectName("appSubtitle")
        titles.addWidget(t2)
        lay.addLayout(titles)
        lay.addStretch()

        # Reset params button
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

        layout.addLayout(input_header)

        # File list
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setMinimumHeight(120)
        self.file_list.setMaximumHeight(200)
        self.file_list.setAcceptDrops(True)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        layout.addWidget(self.file_list)

        self.file_count_label = QLabel("No files selected")
        self.file_count_label.setObjectName("statusLabel")
        layout.addWidget(self.file_count_label)

        layout.addWidget(make_separator())

        # Output folder
        output_header = QHBoxLayout()
        lbl2 = QLabel("Output Folder")
        lbl2.setObjectName("panelTitle")
        output_header.addWidget(lbl2)
        output_header.addStretch()

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

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Ready")
        self.progress_bar.setFixedHeight(28)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_process = QPushButton("  Uniqualize!")
        self.btn_process.setObjectName("btnProcess")
        self.btn_process.setIcon(process_icon(48))
        self.btn_process.setIconSize(QSize(28, 28))
        self.btn_process.clicked.connect(self._start_processing)
        btn_row.addWidget(self.btn_process)

        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("btnCancel")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._cancel_processing)
        btn_row.addWidget(self.btn_cancel)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        return card

    # ─── Slots ───────────────────────────────────────────

    def _select_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Video Files",
            "",
            "Video Files (*.mp4 *.mov *.avi *.mkv *.webm *.flv *.wmv);;All Files (*)"
        )
        if paths:
            for p in paths:
                if p not in self.input_files:
                    self.input_files.append(p)
                    item = QListWidgetItem(os.path.basename(p))
                    item.setToolTip(p)
                    self.file_list.addItem(item)
            self._update_file_count()
            self._update_button_states()

    def _remove_selected(self):
        for item in reversed(self.file_list.selectedItems()):
            row = self.file_list.row(item)
            self.file_list.takeItem(row)
            if row < len(self.input_files):
                self.input_files.pop(row)
        self._update_file_count()
        self._update_button_states()

    def _clear_files(self):
        self.file_list.clear()
        self.input_files.clear()
        self._update_file_count()
        self._update_button_states()

    def _select_output(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if folder:
            self.output_folder = folder
            self.output_label.setText(folder)
            self.output_label.setToolTip(folder)
            self._update_button_states()

    def _reset_params(self):
        self.params_panel.load_defaults()

    def _update_file_count(self):
        n = len(self.input_files)
        if n == 0:
            self.file_count_label.setText("No files selected")
        else:
            self.file_count_label.setText(f"{n} file{'s' if n != 1 else ''} selected")

    def _update_button_states(self):
        can_start = len(self.input_files) > 0 and bool(self.output_folder)
        self.btn_process.setEnabled(can_start and self.worker is None)

    def _start_processing(self):
        if not self.input_files or not self.output_folder:
            return

        params = self.params_panel.build_params()
        self.worker = ProcessWorker(
            files=list(self.input_files),
            output_folder=self.output_folder,
            params=params,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)

        self.btn_process.setVisible(False)
        self.btn_cancel.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Starting...")
        self._update_button_states()

        self.worker.start()

    def _cancel_processing(self):
        if self.worker:
            self.worker.cancel()
            self.status_label.setText("Cancelling...")

    def _on_progress(self, current, total, filename):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"{current}/{total} — {filename}")
            self.status_label.setText(f"Processing: {filename}")

    def _on_finished(self, success_count, was_cancelled):
        total = len(self.input_files)
        self.btn_process.setVisible(True)
        self.btn_cancel.setVisible(False)
        self.worker = None
        self._update_button_states()

        if was_cancelled:
            pct = int((success_count / total) * 100) if total > 0 else 0
            self.progress_bar.setValue(pct)
            self.progress_bar.setFormat(f"Cancelled — {success_count}/{total} processed")
            self.status_label.setText("")
        else:
            self.progress_bar.setValue(100)
            self.progress_bar.setFormat(f"Done! {success_count}/{total} processed")
            self.status_label.setText("")

            QMessageBox.information(
                self,
                "Processing Complete",
                f"Successfully processed {success_count} of {total} files.\n\n"
                f"Output folder: {self.output_folder}"
            )

    def _on_error(self, error_msg):
        self.progress_bar.setFormat("Error!")
        self.status_label.setText(f"Error: {error_msg}")
        self.btn_process.setVisible(True)
        self.btn_cancel.setVisible(False)
        self.worker = None
        self._update_button_states()

        QMessageBox.critical(self, "Error", f"Processing failed:\n{error_msg}")


# ─── Entry Point ────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Video Uniqualizer")
    app.setApplicationDisplayName("Video Uniqualizer")
    app.setStyleSheet(MAIN_STYLESHEET)
    app.setWindowIcon(app_icon(512))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
