"""Tile painter: turns a :class:`Visual` (what a key should show) into a QImage.

All drawing is QPainter on QImage, so the exact same pixels go to the device and to the editor's
live preview. Requires a QGuiApplication (fonts); FreeDock always creates one, even headless.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient,
                           QTransform)
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICON_DIR = ASSETS / "icons"
UI_FONT = "Inter"
MONO_FONT = "JetBrains Mono"
_fonts_loaded = False


def load_fonts() -> None:
    global _fonts_loaded
    if _fonts_loaded:
        return
    for name in ("Inter.ttf", "JetBrainsMono.ttf"):
        QFontDatabase.addApplicationFont(str(ASSETS / "fonts" / name))
    _fonts_loaded = True


@dataclass(frozen=True)
class Visual:
    """Everything a single key / strip zone can display."""
    bg: str = "#1b1f2a"
    bg2: str | None = None          # second gradient stop (top-left -> bottom-right)
    fg: str = "#f5f7fb"
    accent: str = "#5b8cff"
    icon: str | None = None
    icon_color: str | None = None
    label: str = ""
    value: str | None = None        # big centred text (widgets)
    sub: str | None = None          # small text under value
    progress: float | None = None   # 0..1
    progress_style: str = "ring"    # ring | bar
    active: bool = False            # glowing accent border (toggles that are on)
    badge: str | None = None        # small pill top-right
    mono: bool = False              # value in monospace (clocks, telemetry)
    top_band: str | None = None     # colour band along the top edge (shift lights)
    dim: float = 0.0                # 0..1 darkening overlay
    image: str | None = None        # user picture file, drawn as background

    def with_(self, **kw) -> "Visual":
        return replace(self, **kw)


@dataclass
class Fx:
    """Transient effects layered on top of a visual by the animator."""
    press: float = 0.0      # 0..1 while held
    ripple: float = -1.0    # 0..1 progress of the release ripple, <0 = none
    flash: float = 0.0      # 0..1 white flash
    extra: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- icons
@lru_cache(maxsize=512)
def _svg_bytes(name: str, color: str) -> bytes | None:
    path = ICON_DIR / f"{name}.svg"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").replace("currentColor", color)
    return text.encode("utf-8")


@lru_cache(maxsize=1024)
def icon_image(name: str, color: str, size: int, stroke: float = 2.0) -> QImage | None:
    data = _svg_bytes(name, color)
    if data is None:
        return None
    if stroke != 2.0:
        data = data.replace(b'stroke-width="2"', f'stroke-width="{stroke}"'.encode())
    renderer = QSvgRenderer(QByteArray(data))
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return img


def available_icons() -> list[str]:
    return sorted(p.stem for p in ICON_DIR.glob("*.svg"))


@lru_cache(maxsize=64)
def _picture(path: str, w: int, h: int) -> QImage | None:
    img = QImage(path)
    if img.isNull():
        return None
    return img.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                      Qt.TransformationMode.SmoothTransformation)


# --------------------------------------------------------------------------- painting
def _font(size: float, weight: int = 600, mono: bool = False) -> QFont:
    f = QFont(MONO_FONT if mono else UI_FONT)
    f.setPixelSize(max(6, int(size)))
    f.setWeight(QFont.Weight(weight))
    return f


def _fit_font(text: str, max_w: float, size: float, weight: int, mono: bool) -> QFont:
    f = _font(size, weight, mono)
    while size > 8 and QFontMetricsF(f).horizontalAdvance(text) > max_w:
        size -= 1
        f = _font(size, weight, mono)
    return f


def render(v: Visual, w: int, h: int, fx: Fx | None = None) -> QImage:
    load_fonts()
    fx = fx or Fx()
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#000000"))
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                     | QPainter.RenderHint.SmoothPixmapTransform)

    # press: shrink the whole tile a little, like a physical button going down
    if fx.press > 0:
        s = 1.0 - 0.08 * fx.press
        p.translate(w / 2, h / 2)
        p.scale(s, s)
        p.translate(-w / 2, -h / 2)

    rect = QRectF(0, 0, w, h)
    radius = min(w, h) * 0.16
    clip = QPainterPath()
    clip.addRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
    p.setClipPath(clip)

    # background
    if v.image and (pic := _picture(v.image, w, h)) is not None:
        p.drawImage(QPointF((w - pic.width()) / 2, (h - pic.height()) / 2), pic)
    elif v.bg2:
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor(v.bg))
        g.setColorAt(1, QColor(v.bg2))
        p.fillRect(rect, QBrush(g))
    else:
        p.fillRect(rect, QColor(v.bg))
    # soft top light so flat colours look like glass
    sheen = QLinearGradient(0, 0, 0, h)
    sheen.setColorAt(0, QColor(255, 255, 255, 28))
    sheen.setColorAt(0.5, QColor(255, 255, 255, 0))
    p.fillRect(rect, QBrush(sheen))

    if v.active:
        glow = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        c = QColor(v.accent)
        c.setAlpha(0)
        glow.setColorAt(0.55, c)
        c.setAlpha(110)
        glow.setColorAt(1.0, c)
        p.fillRect(rect, QBrush(glow))

    if v.top_band:
        p.fillRect(QRectF(0, 0, w, h * 0.14), QColor(v.top_band))

    fg = QColor(v.fg)
    has_value = v.value is not None
    has_label = bool(v.label)
    has_progress = v.progress is not None

    # progress ring / bar
    if has_progress:
        frac = max(0.0, min(1.0, float(v.progress)))
        if v.progress_style == "bar":
            bar_y = h * (0.71 if (has_label or v.sub) else 0.82)
            bar = QRectF(w * 0.12, bar_y, w * 0.76, max(4.0, h * 0.055))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 40))
            p.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)
            filled = QRectF(bar.x(), bar.y(), bar.width() * frac, bar.height())
            p.setBrush(QColor(v.accent))
            p.drawRoundedRect(filled, bar.height() / 2, bar.height() / 2)
        else:
            d = min(w, h) * 0.78
            ring = QRectF((w - d) / 2, (h - d) / 2 - (h * 0.04 if has_label else 0), d, d)
            pen = QPen(QColor(255, 255, 255, 38), d * 0.07)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(ring, 225 * 16, -270 * 16)
            pen.setColor(QColor(v.accent))
            p.setPen(pen)
            p.drawArc(ring, 225 * 16, int(-270 * 16 * frac))

    # icon
    icon_img = None
    if v.icon:
        if has_value:
            isz = int(min(w, h) * 0.26)
        elif has_label:
            isz = int(min(w, h) * 0.46)
        else:
            isz = int(min(w, h) * 0.58)
        icon_img = icon_image(v.icon, v.icon_color or v.fg, isz)

    if has_value:
        top = h * 0.10
        if icon_img is not None:
            p.drawImage(QPointF((w - icon_img.width()) / 2, top), icon_img)
            top += icon_img.height() + h * 0.02
        bottom = h * (0.78 if (has_label or v.sub) else 0.92)
        if has_progress and v.progress_style == "bar":
            bottom = min(bottom, h * (0.69 if (has_label or v.sub) else 0.80))
        area = QRectF(w * 0.06, top, w * 0.88, bottom - top)
        size = area.height() * 0.85
        p.setFont(_fit_font(v.value, area.width(), size, 700, v.mono))
        p.setPen(fg)
        p.drawText(area, Qt.AlignmentFlag.AlignCenter, v.value)
        caption = v.sub or v.label
        if caption:
            f = _fit_font(caption, w * 0.9, h * 0.13, 500, False)
            p.setFont(f)
            c = QColor(fg)
            c.setAlpha(190)
            p.setPen(c)
            p.drawText(QRectF(0, h * 0.78, w, h * 0.18), Qt.AlignmentFlag.AlignCenter, caption)
    else:
        if icon_img is not None:
            y = (h - icon_img.height()) / 2 - (h * 0.10 if has_label else 0)
            p.drawImage(QPointF((w - icon_img.width()) / 2, y), icon_img)
        if has_label:
            if icon_img is not None:
                area = QRectF(w * 0.05, h * 0.70, w * 0.90, h * 0.24)
                f = _fit_font(v.label, area.width(), h * 0.15, 600, False)
            else:
                area = QRectF(w * 0.06, h * 0.06, w * 0.88, h * 0.88)
                f = _fit_font(v.label, area.width(), h * 0.22, 700, False)
            p.setFont(f)
            p.setPen(fg)
            p.drawText(area, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, v.label)

    if v.badge:
        f = _font(h * 0.12, 700)
        p.setFont(f)
        tw = QFontMetricsF(f).horizontalAdvance(v.badge) + h * 0.10
        pill = QRectF(w - tw - w * 0.06, h * 0.06, tw, h * 0.17)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(v.accent))
        p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        p.setPen(QColor("#ffffff"))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, v.badge)

    # effects
    if fx.ripple >= 0:
        t = fx.ripple
        r = max(w, h) * (0.15 + 0.95 * t)
        c = QColor(v.accent)
        c.setAlphaF(max(0.0, 0.55 * (1 - t)))
        p.setPen(QPen(c, max(2.0, w * 0.06 * (1 - t))))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(w / 2, h / 2), r, r)
    if fx.press > 0:
        p.fillRect(rect, QColor(255, 255, 255, int(40 * fx.press)))
    if fx.flash > 0:
        p.fillRect(rect, QColor(255, 255, 255, int(200 * fx.flash)))
    if v.dim > 0:
        p.fillRect(rect, QColor(0, 0, 0, int(255 * min(1.0, v.dim))))

    # hairline border
    p.setClipping(False)
    border = QColor(v.accent if v.active else "#ffffff")
    border.setAlpha(170 if v.active else 22)
    p.setPen(QPen(border, 2.2 if v.active else 1.0))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
    p.end()
    return img


def blank(w: int, h: int) -> QImage:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor("#000000"))
    return img


def to_device_jpeg(img: QImage, rotation: int = 180, max_bytes: int = 10240) -> bytes:
    """Rotate for the panel and JPEG-encode under the firmware's size limit."""
    if rotation:
        img = img.transformed(QTransform().rotate(rotation))
    for quality in (92, 85, 75, 60, 45, 30):
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPG", quality)
        buf.close()
        data = bytes(ba.data())
        if len(data) <= max_bytes:
            return data
    return data[:max_bytes]


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    return 0.5 - math.cos(math.pi * max(0.0, min(1.0, t))) / 2
