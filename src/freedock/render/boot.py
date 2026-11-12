"""Boot animation built from the deck's own keys: every key is one lit square.

Timeline (seconds):
  0.00-0.95  the squares pop in one after another, running around the 2x5 grid like a chaser light
  0.95-1.75  a white comet laps the ring of squares while the strip fills like a progress bar
  1.75-2.35  the squares shrink away in a diagonal wave, revealing the page

Pure functions of (index, t): the engine calls them every frame, tests and previews call them directly.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen

from .painter import UI_FONT, ease_in_out, ease_out_cubic

COLS, ROWS = 5, 2
# the ring: top row left -> right, then bottom row right -> left
RING = (0, 1, 2, 3, 4, 9, 8, 7, 6, 5)
POP_STEP, POP_LEN = 0.075, 0.28
COMET_T0, COMET_LAP = 0.95, 0.8
OUT_T0, OUT_STEP, OUT_LEN = 1.75, 0.06, 0.3
DURATION = OUT_T0 + (COLS - 1 + ROWS - 1) * OUT_STEP + OUT_LEN   # the last square has fully gone
PALETTE = ("#3b82f6", "#6366f1", "#8b5cf6", "#a855f7", "#d946ef", "#ec4899")


def _mix(a: QColor, b: QColor, f: float) -> QColor:
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * f, a.greenF() + (b.greenF() - a.greenF()) * f,
                           a.blueF() + (b.blueF() - a.blueF()) * f)


def ring_color(pos: float) -> QColor:
    """Colour of ring position 0..1 along the palette."""
    pos = max(0.0, min(1.0, pos)) * (len(PALETTE) - 1)
    i = min(int(pos), len(PALETTE) - 2)
    return _mix(QColor(PALETTE[i]), QColor(PALETTE[i + 1]), pos - i)


def _back(x: float) -> float:
    """Ease-out with a small overshoot: the square 'pops'."""
    c = 1.9
    x -= 1
    return 1 + (c + 1) * x ** 3 + c * x ** 2


def key_state(index: int, t: float) -> tuple[float, float, float]:
    """(scale 0..~1.1, opacity 0..1, comet glow 0..1) of key ``index`` at time ``t``."""
    n = RING.index(index)
    pop = (t - n * POP_STEP) / POP_LEN
    if pop <= 0:
        return 0.0, 0.0, 0.0
    scale = _back(min(1.0, pop))
    alpha = min(1.0, pop * 2)
    glow = 0.0
    if t >= COMET_T0:
        head = (t - COMET_T0) / COMET_LAP * len(RING)      # comet position along the ring
        d = (head - n) % len(RING)
        glow = math.exp(-d * 1.1) if head - n >= 0 else 0.0
    r, c = divmod(index, COLS)
    out = (t - OUT_T0 - (c + r) * OUT_STEP) / OUT_LEN
    if out > 0:
        k = ease_in_out(min(1.0, out))
        scale *= 1 - k
        alpha *= 1 - k
    return scale, alpha, glow


def key_tile(index: int, t: float, size: tuple[int, int]) -> QImage:
    w, h = size
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#000000"))
    scale, alpha, glow = key_state(index, t)
    if alpha <= 0.01 or scale <= 0.01:
        return img
    base = ring_color(RING.index(index) / (len(RING) - 1))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    side = min(w, h) * 0.78 * scale
    rect = QRectF((w - side) / 2, (h - side) / 2, side, side)
    radius = side * 0.2
    # soft halo behind the square
    p.setOpacity(alpha * (0.35 + 0.5 * glow))
    halo = rect.adjusted(-side * 0.08, -side * 0.08, side * 0.08, side * 0.08)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_mix(base, QColor("#000000"), 0.45))
    p.drawRoundedRect(halo, radius * 1.3, radius * 1.3)
    # the square: lighter on top, brighter while the comet passes
    p.setOpacity(alpha)
    top = _mix(base, QColor("#ffffff"), 0.25 + 0.6 * glow)
    bottom = _mix(base, QColor("#000000"), 0.25 - 0.2 * glow)
    g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    g.setColorAt(0, top)
    g.setColorAt(1, bottom)
    p.setBrush(g)
    p.setPen(QPen(_mix(base, QColor("#ffffff"), 0.55 + 0.45 * glow), max(1.5, side * 0.025)))
    p.drawRoundedRect(rect, radius, radius)
    p.end()
    return img


def strip_canvas(t: float, width: int, height: int) -> QImage:
    """The touch strip: a progress bar filling under the name while the comet runs."""
    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(QColor("#000000"))
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
    fade_in = min(1.0, max(0.0, (t - 0.3) / 0.4))
    fade_out = 1 - min(1.0, max(0.0, (t - OUT_T0 - 0.2) / 0.3))
    p.setOpacity(fade_in * fade_out)
    fill = ease_out_cubic(min(1.0, max(0.0, (t - 0.3) / (COMET_T0 + COMET_LAP - 0.3))))
    bar_h = max(6, height // 14)
    track = QRectF(width * 0.06, height * 0.74, width * 0.88, bar_h)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#141826"))
    p.drawRoundedRect(track, bar_h / 2, bar_h / 2)
    g = QLinearGradient(track.topLeft(), track.topRight())
    for i, c in enumerate(PALETTE):
        g.setColorAt(i / (len(PALETTE) - 1), QColor(c))
    p.setBrush(g)
    p.drawRoundedRect(QRectF(track.x(), track.y(), max(bar_h, track.width() * fill), bar_h), bar_h / 2, bar_h / 2)
    f = QFont(UI_FONT)
    f.setPixelSize(int(height * 0.34))
    f.setWeight(QFont.Weight.Black)
    p.setFont(f)
    p.setPen(QColor("#ffffff"))
    p.drawText(QRectF(0, 0, width, height * 0.7), Qt.AlignmentFlag.AlignCenter, "FreeDock")
    p.end()
    return img
