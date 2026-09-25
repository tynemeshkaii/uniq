"""
Skeuomorphic stylesheet for the Video Uniqualizer app.
Rich textures, gradients, shadows, and depth — inspired by classic macOS design.
"""

MAIN_STYLESHEET = """
/* ─── GLOBAL ─────────────────────────────────────────── */
QWidget {
    font-family: "SF Pro Display", "Helvetica Neue", "Segoe UI", sans-serif;
    font-size: 13px;
    color: #E8E8EC;
}

QMainWindow {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #2A2A32,
        stop:0.3 #232329,
        stop:0.7 #1E1E24,
        stop:1 #18181E
    );
}

/* ─── TOP BAR ────────────────────────────────────────── */
#topBar {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #3A3A45,
        stop:0.5 #2E2E38,
        stop:1 #28282F
    );
    border-bottom: 1px solid #1A1A20;
    border-top: 1px solid rgba(255, 255, 255, 30);
    min-height: 56px;
}

#appTitle {
    font-size: 18px;
    font-weight: bold;
    color: #F0F0F5;
    letter-spacing: 0.5px;
}

#appSubtitle {
    font-size: 11px;
    color: #8888A0;
    letter-spacing: 0.3px;
}

/* ─── PANELS (Skeuomorphic cards) ────────────────────── */
#panelCard {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #33333D,
        stop:1 #2A2A33
    );
    border: 1px solid #3E3E4A;
    border-top: 1px solid rgba(255, 255, 255, 18);
    border-radius: 12px;
}

#panelTitle {
    font-size: 14px;
    font-weight: 600;
    color: #D0D0DC;
}

/* ─── BUTTONS ────────────────────────────────────────── */
QPushButton {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #4A4A58,
        stop:0.5 #3E3E4C,
        stop:1 #353542
    );
    border: 1px solid #2A2A35;
    border-top: 1px solid rgba(255, 255, 255, 20);
    border-radius: 8px;
    padding: 8px 18px;
    color: #E0E0EA;
    font-weight: 500;
    min-height: 20px;
}

QPushButton:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #565668,
        stop:0.5 #4A4A5C,
        stop:1 #404050
    );
    border-top: 1px solid rgba(255, 255, 255, 30);
}

QPushButton:pressed {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #303040,
        stop:1 #383848
    );
    border-top: 1px solid rgba(255, 255, 255, 8);
    padding-top: 9px;
    padding-bottom: 7px;
}

QPushButton:disabled {
    background: #2A2A32;
    border: 1px solid #222228;
    color: #555560;
}

/* ─── PRIMARY ACTION BUTTON ──────────────────────────── */
#btnProcess {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #7B4FC8,
        stop:0.4 #6B3FB8,
        stop:1 #5A2FA5
    );
    border: 1px solid #4A1F95;
    border-top: 1px solid rgba(255, 255, 255, 35);
    border-radius: 10px;
    color: #FFFFFF;
    font-size: 15px;
    font-weight: 700;
    padding: 12px 32px;
    min-height: 28px;
    letter-spacing: 0.5px;
}

#btnProcess:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #8B5FD8,
        stop:0.4 #7B4FC8,
        stop:1 #6A3FB5
    );
    border-top: 1px solid rgba(255, 255, 255, 45);
}

#btnProcess:pressed {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #5A2FA5,
        stop:1 #6B3FB8
    );
    padding-top: 13px;
    padding-bottom: 11px;
}

#btnProcess:disabled {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #4A3A60,
        stop:1 #3A2A50
    );
    border: 1px solid #302040;
    color: #7A6A90;
}

/* Cancel button */
#btnCancel {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #C84F4F,
        stop:1 #A53030
    );
    border: 1px solid #8A2020;
    border-top: 1px solid rgba(255, 255, 255, 30);
    border-radius: 10px;
    color: #FFFFFF;
    font-size: 15px;
    font-weight: 700;
    padding: 12px 32px;
    min-height: 28px;
}

#btnCancel:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #D86060,
        stop:1 #B54040
    );
}

/* ─── FILE LIST ──────────────────────────────────────── */
QListWidget {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #252530,
        stop:1 #1E1E28
    );
    border: 1px solid #3A3A45;
    border-top: 1px solid rgba(0, 0, 0, 40);
    border-radius: 8px;
    color: #C8C8D4;
    padding: 4px;
    outline: none;
}

QListWidget::item {
    padding: 6px 8px;
    border-radius: 6px;
    margin: 1px 2px;
    /* No `color` here: an item-level colour overrides the per-row status
       colours set from code. The default text colour is on QListWidget. */
}

QListWidget::item:selected {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #4A3A70,
        stop:1 #3A2A60
    );
    color: #E8E0FF;
}

QListWidget::item:hover:!selected {
    background: rgba(255, 255, 255, 8);
}

/* ─── PROGRESS BAR ───────────────────────────────────── */
QProgressBar {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1A1A22,
        stop:0.5 #222230,
        stop:1 #1A1A22
    );
    border: 1px solid #3A3A45;
    border-top: 1px solid rgba(0, 0, 0, 60);
    border-radius: 8px;
    text-align: center;
    color: #C0C0D0;
    font-weight: 600;
    font-size: 12px;
    min-height: 22px;
}

QProgressBar::chunk {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #6B3FB8,
        stop:0.5 #8B5FD8,
        stop:1 #6B3FB8
    );
    border-radius: 7px;
}

/* ─── SCROLL BAR ─────────────────────────────────────── */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 4px 2px;
}

QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 40);
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 60);
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

/* ─── SPIN BOXES & LINE EDITS ────────────────────────── */
QDoubleSpinBox, QSpinBox {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #252530,
        stop:1 #1E1E28
    );
    border: 1px solid #3A3A48;
    border-top: 1px solid rgba(0, 0, 0, 40);
    border-radius: 6px;
    padding: 4px 8px;
    color: #D0D0DC;
    min-height: 18px;
    selection-background-color: #5A3FA0;
}

QDoubleSpinBox:focus, QSpinBox:focus {
    border: 1px solid #6B4FB8;
}

QDoubleSpinBox::up-button, QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid #3A3A48;
    border-bottom: 1px solid #3A3A48;
    border-top-right-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #404050, stop:1 #353545);
}

QDoubleSpinBox::down-button, QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 20px;
    border-left: 1px solid #3A3A48;
    border-bottom-right-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #404050, stop:1 #353545);
}

QDoubleSpinBox::up-arrow, QSpinBox::up-arrow {
    width: 8px;
    height: 6px;
}

QDoubleSpinBox::down-arrow, QSpinBox::down-arrow {
    width: 8px;
    height: 6px;
}

/* ─── COMBO BOX ──────────────────────────────────────── */
QComboBox {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #3E3E4C,
        stop:1 #333340
    );
    border: 1px solid #3A3A48;
    border-top: 1px solid rgba(255, 255, 255, 15);
    border-radius: 6px;
    padding: 4px 12px;
    color: #D0D0DC;
    min-height: 18px;
}

QComboBox:hover {
    border: 1px solid #5A4A80;
}

QComboBox::drop-down {
    border-left: 1px solid #3A3A48;
    width: 24px;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #404050, stop:1 #353545);
}

QComboBox QAbstractItemView {
    background: #2A2A35;
    border: 1px solid #3A3A48;
    border-radius: 6px;
    selection-background-color: #5A3FA0;
    color: #D0D0DC;
    padding: 4px;
}

/* ─── CHECK BOX ──────────────────────────────────────── */
QCheckBox {
    spacing: 8px;
    color: #C8C8D4;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid #4A4A58;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #303040,
        stop:1 #252535
    );
}

QCheckBox::indicator:checked {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #7B4FC8,
        stop:1 #5A2FA5
    );
    border: 1px solid #4A1F95;
    image: none;
}

QCheckBox::indicator:hover {
    border: 1px solid #6B4FB8;
}

/* ─── GROUP BOX ──────────────────────────────────────── */
QGroupBox {
    background: transparent;
    border: 1px solid #3A3A45;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 14px;
    font-weight: 600;
    color: #A0A0B4;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
}

/* ─── LABEL ──────────────────────────────────────────── */
QLabel {
    color: #B0B0C0;
}

#statusLabel {
    font-size: 12px;
    color: #9090A8;
    font-style: italic;
}

/* ─── SEPARATOR ──────────────────────────────────────── */
#separator {
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 transparent,
        stop:0.2 #3A3A48,
        stop:0.8 #3A3A48,
        stop:1 transparent
    );
    min-height: 1px;
    max-height: 1px;
}

/* ─── SLIDER ─────────────────────────────────────────── */
QSlider::groove:horizontal {
    height: 6px;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1A1A22,
        stop:1 #252530
    );
    border: 1px solid #2A2A35;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 18px;
    height: 18px;
    margin: -7px 0;
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #E0E0E8,
        stop:0.5 #C0C0C8,
        stop:1 #A0A0A8
    );
    border: 1px solid #808090;
    border-radius: 9px;
}

QSlider::handle:horizontal:hover {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #F0F0F8,
        stop:1 #B0B0B8
    );
}

QSlider::sub-page:horizontal {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #6B3FB8,
        stop:1 #5A2FA5
    );
    border-radius: 3px;
}

/* ─── TAB WIDGET ─────────────────────────────────────── */
QTabWidget::pane {
    background: transparent;
    border: 1px solid #3A3A45;
    border-radius: 8px;
    border-top-left-radius: 0px;
    top: -1px;
}

QTabBar::tab {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #353545,
        stop:1 #2A2A38
    );
    border: 1px solid #3A3A45;
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 6px 16px;
    margin-right: 2px;
    color: #8888A0;
}

QTabBar::tab:selected {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #454558,
        stop:1 #3A3A4C
    );
    color: #D0D0E0;
    border-top: 2px solid #7B4FC8;
}

QTabBar::tab:hover:!selected {
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #404055,
        stop:1 #333345
    );
    color: #B0B0C8;
}

/* ─── TOOLTIP ────────────────────────────────────────── */
QToolTip {
    background: #2E2E3A;
    border: 1px solid #4A4A58;
    border-radius: 6px;
    padding: 6px 10px;
    color: #D0D0DC;
    font-size: 12px;
}
"""
