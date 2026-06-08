"""
Programmatically drawn icons and graphics for the Video Uniqualizer app.
All icons are rendered via QPainter — no external image files needed.
"""

import functools
from PyQt6.QtCore import Qt, QRect, QRectF, QPointF, QSize
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QLinearGradient, QRadialGradient,
    QPen, QBrush, QFont, QPainterPath, QConicalGradient
)


def _make_pixmap(size: int, draw_func) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw_func(p, size)
    p.end()
    return pm


def make_icon(size: int, draw_func) -> QIcon:
    return QIcon(_make_pixmap(size, draw_func))


# ─── APP ICON ───────────────────────────────────────────────

def draw_app_icon(p: QPainter, s: int):
    """Main application icon — film reel + magic wand"""
    # Background rounded rect with gradient
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0.0, QColor(45, 25, 80))
    grad.setColorAt(0.5, QColor(75, 35, 130))
    grad.setColorAt(1.0, QColor(45, 25, 80))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    radius = s * 0.22
    p.drawRoundedRect(QRectF(s * 0.02, s * 0.02, s * 0.96, s * 0.96), radius, radius)

    # Inner glow
    glow = QRadialGradient(s * 0.5, s * 0.45, s * 0.5)
    glow.setColorAt(0.0, QColor(160, 100, 255, 60))
    glow.setColorAt(1.0, QColor(160, 100, 255, 0))
    p.setBrush(QBrush(glow))
    p.drawRoundedRect(QRectF(s * 0.02, s * 0.02, s * 0.96, s * 0.96), radius, radius)

    # Film strip
    p.setPen(QPen(QColor(255, 255, 255, 200), s * 0.02))
    p.setBrush(QColor(255, 255, 255, 30))

    # Film frame
    fx, fy, fw, fh = s * 0.18, s * 0.22, s * 0.64, s * 0.56
    p.drawRoundedRect(QRectF(fx, fy, fw, fh), s * 0.03, s * 0.03)

    # Sprocket holes
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 100))
    hole_size = s * 0.055
    for i in range(4):
        x = fx + fw * (0.15 + i * 0.25)
        p.drawRoundedRect(QRectF(x, fy + s * 0.02, hole_size, hole_size * 0.7), 2, 2)
        p.drawRoundedRect(QRectF(x, fy + fh - s * 0.04, hole_size, hole_size * 0.7), 2, 2)

    # Play triangle in center
    p.setBrush(QColor(200, 140, 255, 220))
    path = QPainterPath()
    cx, cy = s * 0.45, s * 0.5
    tri_s = s * 0.15
    path.moveTo(cx, cy - tri_s * 0.6)
    path.lineTo(cx + tri_s, cy)
    path.lineTo(cx, cy + tri_s * 0.6)
    path.closeSubpath()
    p.drawPath(path)

    # Sparkles
    p.setPen(QPen(QColor(255, 220, 100, 230), s * 0.015))
    sparkle_positions = [(0.75, 0.25), (0.82, 0.38), (0.7, 0.15)]
    for sx_r, sy_r in sparkle_positions:
        sx2, sy2 = s * sx_r, s * sy_r
        arm = s * 0.04
        p.drawLine(QPointF(sx2 - arm, sy2), QPointF(sx2 + arm, sy2))
        p.drawLine(QPointF(sx2, sy2 - arm), QPointF(sx2, sy2 + arm))


@functools.lru_cache(maxsize=4)
def app_icon(size=512) -> QIcon:
    return make_icon(size, draw_app_icon)


# ─── FILE SELECT ICON ──────────────────────────────────────

def draw_file_icon(p: QPainter, s: int):
    """Folder with film strip"""
    # Folder back
    p.setPen(Qt.PenStyle.NoPen)
    grad = QLinearGradient(0, s * 0.2, 0, s * 0.9)
    grad.setColorAt(0.0, QColor(90, 160, 240))
    grad.setColorAt(1.0, QColor(50, 110, 200))
    p.setBrush(QBrush(grad))

    path = QPainterPath()
    path.moveTo(s * 0.1, s * 0.3)
    path.lineTo(s * 0.1, s * 0.22)
    path.quadTo(s * 0.1, s * 0.18, s * 0.15, s * 0.18)
    path.lineTo(s * 0.35, s * 0.18)
    path.lineTo(s * 0.42, s * 0.25)
    path.lineTo(s * 0.85, s * 0.25)
    path.quadTo(s * 0.9, s * 0.25, s * 0.9, s * 0.3)
    path.lineTo(s * 0.9, s * 0.8)
    path.quadTo(s * 0.9, s * 0.85, s * 0.85, s * 0.85)
    path.lineTo(s * 0.15, s * 0.85)
    path.quadTo(s * 0.1, s * 0.85, s * 0.1, s * 0.8)
    path.closeSubpath()
    p.drawPath(path)

    # Film icon inside folder
    p.setPen(QPen(QColor(255, 255, 255, 200), s * 0.02))
    p.setBrush(QColor(255, 255, 255, 40))
    p.drawRoundedRect(QRectF(s * 0.28, s * 0.42, s * 0.44, s * 0.3), s * 0.02, s * 0.02)

    # Small play button
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 200))
    tri = QPainterPath()
    tri.moveTo(s * 0.44, s * 0.49)
    tri.lineTo(s * 0.58, s * 0.57)
    tri.lineTo(s * 0.44, s * 0.65)
    tri.closeSubpath()
    p.drawPath(tri)

    # Plus badge
    p.setBrush(QColor(60, 200, 100))
    p.drawEllipse(QRectF(s * 0.65, s * 0.12, s * 0.28, s * 0.28))
    p.setPen(QPen(QColor(255, 255, 255), s * 0.04))
    cx2, cy2 = s * 0.79, s * 0.26
    p.drawLine(QPointF(cx2 - s * 0.07, cy2), QPointF(cx2 + s * 0.07, cy2))
    p.drawLine(QPointF(cx2, cy2 - s * 0.07), QPointF(cx2, cy2 + s * 0.07))


@functools.lru_cache(maxsize=4)
def file_select_icon(size=64) -> QIcon:
    return make_icon(size, draw_file_icon)


# ─── OUTPUT FOLDER ICON ────────────────────────────────────

def draw_output_icon(p: QPainter, s: int):
    """Folder with arrow (output/save)"""
    # Folder
    p.setPen(Qt.PenStyle.NoPen)
    grad = QLinearGradient(0, s * 0.2, 0, s * 0.9)
    grad.setColorAt(0.0, QColor(240, 170, 50))
    grad.setColorAt(1.0, QColor(200, 130, 30))
    p.setBrush(QBrush(grad))

    path = QPainterPath()
    path.moveTo(s * 0.1, s * 0.3)
    path.lineTo(s * 0.1, s * 0.22)
    path.quadTo(s * 0.1, s * 0.18, s * 0.15, s * 0.18)
    path.lineTo(s * 0.35, s * 0.18)
    path.lineTo(s * 0.42, s * 0.25)
    path.lineTo(s * 0.85, s * 0.25)
    path.quadTo(s * 0.9, s * 0.25, s * 0.9, s * 0.3)
    path.lineTo(s * 0.9, s * 0.8)
    path.quadTo(s * 0.9, s * 0.85, s * 0.85, s * 0.85)
    path.lineTo(s * 0.15, s * 0.85)
    path.quadTo(s * 0.1, s * 0.85, s * 0.1, s * 0.8)
    path.closeSubpath()
    p.drawPath(path)

    # Download arrow
    p.setPen(QPen(QColor(255, 255, 255, 220), s * 0.04, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    cx = s * 0.5
    p.drawLine(QPointF(cx, s * 0.38), QPointF(cx, s * 0.68))
    # Arrow head
    p.drawLine(QPointF(cx - s * 0.1, s * 0.58), QPointF(cx, s * 0.68))
    p.drawLine(QPointF(cx + s * 0.1, s * 0.58), QPointF(cx, s * 0.68))


@functools.lru_cache(maxsize=4)
def output_folder_icon(size=64) -> QIcon:
    return make_icon(size, draw_output_icon)


# ─── PROCESS (START) ICON ──────────────────────────────────

def draw_process_icon(p: QPainter, s: int):
    """Magic wand / process button"""
    # Circular background
    grad = QLinearGradient(0, 0, s, s)
    grad.setColorAt(0.0, QColor(100, 60, 200))
    grad.setColorAt(1.0, QColor(180, 80, 220))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawEllipse(QRectF(s * 0.05, s * 0.05, s * 0.9, s * 0.9))

    # Wand
    p.setPen(QPen(QColor(255, 255, 255, 240), s * 0.05, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(s * 0.3, s * 0.7), QPointF(s * 0.65, s * 0.35))

    # Star at wand tip
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 230, 100, 240))
    star = QPainterPath()
    cx, cy = s * 0.68, s * 0.32
    r1, r2 = s * 0.1, s * 0.04
    import math
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        r = r1 if i % 2 == 0 else r2
        x = cx + r * math.cos(angle)
        y = cy - r * math.sin(angle)
        if i == 0:
            star.moveTo(x, y)
        else:
            star.lineTo(x, y)
    star.closeSubpath()
    p.drawPath(star)

    # Sparkles
    p.setPen(QPen(QColor(255, 230, 100, 200), s * 0.015))
    for sx_r, sy_r, arm_r in [(0.45, 0.25, 0.035), (0.8, 0.5, 0.03), (0.55, 0.15, 0.025)]:
        sx2, sy2, arm = s * sx_r, s * sy_r, s * arm_r
        p.drawLine(QPointF(sx2 - arm, sy2), QPointF(sx2 + arm, sy2))
        p.drawLine(QPointF(sx2, sy2 - arm), QPointF(sx2, sy2 + arm))


@functools.lru_cache(maxsize=4)
def process_icon(size=64) -> QIcon:
    return make_icon(size, draw_process_icon)


# ─── SETTINGS ICON ─────────────────────────────────────────

def draw_settings_icon(p: QPainter, s: int):
    """Gear icon"""
    import math
    cx, cy = s * 0.5, s * 0.5
    outer_r = s * 0.42
    inner_r = s * 0.30
    teeth = 8
    tooth_width = math.pi / teeth * 0.6

    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(150, 150, 165))

    path = QPainterPath()
    for i in range(teeth):
        angle = i * 2 * math.pi / teeth
        # Outer point
        a1 = angle - tooth_width
        a2 = angle + tooth_width
        if i == 0:
            path.moveTo(cx + outer_r * math.cos(a1), cy + outer_r * math.sin(a1))
        else:
            path.lineTo(cx + outer_r * math.cos(a1), cy + outer_r * math.sin(a1))
        path.lineTo(cx + outer_r * math.cos(a2), cy + outer_r * math.sin(a2))
        # Inner arc to next tooth
        next_angle = (i + 1) * 2 * math.pi / teeth
        na1 = next_angle - tooth_width
        path.lineTo(cx + inner_r * math.cos(a2), cy + inner_r * math.sin(a2))
        path.lineTo(cx + inner_r * math.cos(na1), cy + inner_r * math.sin(na1))
    path.closeSubpath()
    p.drawPath(path)

    # Center hole
    p.setBrush(QColor(40, 40, 50))
    p.drawEllipse(QRectF(cx - s * 0.12, cy - s * 0.12, s * 0.24, s * 0.24))


@functools.lru_cache(maxsize=4)
def settings_icon(size=64) -> QIcon:
    return make_icon(size, draw_settings_icon)


# ─── REMOVE FILE ICON ──────────────────────────────────────

def draw_remove_icon(p: QPainter, s: int):
    """X / remove icon"""
    p.setPen(QPen(QColor(220, 80, 80), s * 0.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    margin = s * 0.25
    p.drawLine(QPointF(margin, margin), QPointF(s - margin, s - margin))
    p.drawLine(QPointF(s - margin, margin), QPointF(margin, s - margin))


@functools.lru_cache(maxsize=4)
def remove_icon(size=32) -> QIcon:
    return make_icon(size, draw_remove_icon)


# ─── PRESET ICON ───────────────────────────────────────────

def draw_preset_icon(p: QPainter, s: int):
    """Slider/preset icon"""
    p.setPen(QPen(QColor(140, 140, 160), s * 0.04))

    # Three horizontal lines with circles (sliders)
    for i, (line_y, knob_x) in enumerate([(0.3, 0.6), (0.5, 0.35), (0.7, 0.7)]):
        y = s * line_y
        p.drawLine(QPointF(s * 0.15, y), QPointF(s * 0.85, y))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(100, 160, 240))
        p.drawEllipse(QPointF(s * knob_x, y), s * 0.06, s * 0.06)
        p.setPen(QPen(QColor(140, 140, 160), s * 0.04))


@functools.lru_cache(maxsize=4)
def preset_icon(size=32) -> QIcon:
    return make_icon(size, draw_preset_icon)
