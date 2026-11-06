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
from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QImageReader,
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
    bg2: str | None = None          # second gradient stop
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
    image: str | None = None        # user picture (png/jpg/gif/webp), drawn as background
    # ---- customisation (all optional, see docs/CUSTOMIZE.md)
    font: str | None = None         # font family for labels and captions
    font_value: str | None = None   # font family for the big value (default: mono, or ``font``)
    font_scale: float = 1.0         # text size multiplier
    bold: bool = True
    grad: str = "diag"              # diag | vertical | horizontal | radial
    image_fit: str = "cover"        # cover | contain | stretch
    image_dim: float = 0.0          # darken the picture so text stays readable
    frame: int = 0                  # current frame of an animated picture
    icon_scale: float = 1.0
    border: str = "auto"            # auto | none | accent
    radius: float = 1.0             # corner roundness multiplier, 0 = square
    sheen: bool = True              # glassy top light
    anim: str | None = None         # one of ANIMATIONS
    anim_t: float = 0.0             # 0..1 animation phase, advanced by the engine

    def with_(self, **kw) -> "Visual":
        return replace(self, **kw)


# animation id -> period in seconds at speed 1
ANIMATIONS: dict[str, float] = {
    "pulse": 1.6, "breathe": 3.0, "glow": 2.2, "shimmer": 2.6, "rainbow": 4.0, "blink": 0.8,
    "float": 2.4, "spin": 2.0, "heartbeat": 1.2, "gradient": 6.0, "wave": 2.0, "scan": 1.8,
    "alert": 0.7, "sparkle": 2.0,
}


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


# --------------------------------------------------------------------------- pictures (static + animated)
MAX_FRAMES = 240


@dataclass(frozen=True)
class Picture:
    frames: tuple
    delays: tuple                   # ms per frame
    total_ms: int

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    def frame_at(self, ms: float) -> int:
        if not self.animated or self.total_ms <= 0:
            return 0
        t = ms % self.total_ms
        for i, d in enumerate(self.delays):
            if t < d:
                return i
            t -= d
        return len(self.frames) - 1


def _fit(img: QImage, w: int, h: int, fit: str) -> QImage:
    if fit == "stretch":
        return img.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    mode = Qt.AspectRatioMode.KeepAspectRatio if fit == "contain" else Qt.AspectRatioMode.KeepAspectRatioByExpanding
    return img.scaled(w, h, mode, Qt.TransformationMode.SmoothTransformation)


@lru_cache(maxsize=48)
def picture(path: str, w: int, h: int, fit: str = "cover") -> Picture | None:
    """Every frame of a picture, scaled for a tile. GIF/WebP/APNG keep their own frame timing."""
    reader = QImageReader(path)
    reader.setDecideFormatFromContent(True)
    frames, delays = [], []
    while len(frames) < MAX_FRAMES:
        img = reader.read()
        if img.isNull():
            break
        frames.append(_fit(img, w, h, fit))
        delay = reader.nextImageDelay()
        delays.append(max(20, delay if delay > 10 else 100))   # browsers also treat 0-10 ms as 100 ms
        if not reader.supportsAnimation():
            break
    if not frames:
        return None
    return Picture(tuple(frames), tuple(delays), sum(delays))


def picture_frame(path: str | None, w: int, h: int, fit: str, ms: float) -> int:
    """Which frame of ``path`` is showing ``ms`` milliseconds into its loop (0 for still pictures)."""
    if not path:
        return 0
    pic = picture(path, w, h, fit)
    return pic.frame_at(ms) if pic else 0


# --------------------------------------------------------------------------- painting
def _font(size: float, weight: int = 600, mono: bool = False, family: str | None = None) -> QFont:
    f = QFont(family or (MONO_FONT if mono else UI_FONT))
    f.setPixelSize(max(6, int(size)))
    f.setWeight(QFont.Weight(weight))
    return f


def _fit_font(text: str, max_w: float, size: float, weight: int, mono: bool, family: str | None = None) -> QFont:
    f = _font(size, weight, mono, family)
    while size > 8 and QFontMetricsF(f).horizontalAdvance(text) > max_w:
        size -= 1
        f = _font(size, weight, mono, family)
    return f


def _wave(t: float) -> float:
    """0 -> 1 -> 0 over one period."""
    return 0.5 - 0.5 * math.cos(2 * math.pi * t)


def _heartbeat(t: float) -> float:
    """Two quick beats then a rest, 0..1."""
    for c in (0.12, 0.32):
        d = abs(t - c)
        if d < 0.09:
            return 1 - d / 0.09
    return 0.0


def _background(p: QPainter, v: Visual, rect: QRectF, w: int, h: int) -> None:
    if v.image and (pic := picture(v.image, w, h, v.image_fit)) is not None:
        if v.image_fit == "contain":
            p.fillRect(rect, QColor(v.bg))
        img = pic.frames[v.frame % len(pic.frames)]
        p.drawImage(QPointF((w - img.width()) / 2, (h - img.height()) / 2), img)
        if v.image_dim > 0:
            p.fillRect(rect, QColor(0, 0, 0, int(255 * min(0.9, v.image_dim))))
        return
    if not v.bg2:
        p.fillRect(rect, QColor(v.bg))
        return
    if v.grad == "radial":
        g = QRadialGradient(w / 2, h * 0.4, max(w, h) * 0.75)
    else:
        angle = {"vertical": 90.0, "horizontal": 0.0}.get(v.grad, 45.0)
        if v.anim == "gradient":
            angle += 360 * v.anim_t
        r = math.radians(angle)
        dx, dy = math.cos(r) * w / 2, math.sin(r) * h / 2
        g = QLinearGradient(w / 2 - dx, h / 2 - dy, w / 2 + dx, h / 2 + dy)
    g.setColorAt(0, QColor(v.bg))
    g.setColorAt(1, QColor(v.bg2))
    p.fillRect(rect, QBrush(g))


def _anim_overlay(p: QPainter, v: Visual, rect: QRectF, w: int, h: int) -> None:
    """Animations drawn over the content (the ones that move the icon are applied in draw_icon)."""
    a, t = v.anim, v.anim_t
    acc = QColor(v.accent)
    if a == "breathe":
        p.fillRect(rect, QColor(0, 0, 0, int(80 * _wave(t))))
    elif a == "glow":
        glow = QRadialGradient(w / 2, h / 2, max(w, h) * 0.8)
        c = QColor(acc)
        c.setAlpha(0)
        glow.setColorAt(0.35, c)
        c.setAlpha(int(40 + 110 * _wave(t)))
        glow.setColorAt(1.0, c)
        p.fillRect(rect, QBrush(glow))
    elif a == "shimmer":
        x = -w + 3 * w * t
        g = QLinearGradient(x, 0, x + w * 0.6, h * 0.6)
        g.setColorAt(0, QColor(255, 255, 255, 0))
        g.setColorAt(0.5, QColor(255, 255, 255, 70))
        g.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(rect, QBrush(g))
    elif a == "blink":
        if t < 0.5:
            c = QColor(acc)
            c.setAlpha(95)
            p.fillRect(rect, c)
    elif a == "alert":
        c = QColor(acc)
        c.setAlpha(int(150 * _wave(t)))
        p.fillRect(rect, c)
    elif a == "rainbow":
        c = QColor.fromHsvF(t % 1.0, 0.85, 1.0)
        c.setAlpha(38)
        p.fillRect(rect, c)
    elif a == "wave":
        path = QPainterPath()
        base = h * 0.9
        path.moveTo(0, h)
        for i in range(0, w + 4, 4):
            path.lineTo(i, base + math.sin(i / w * 3 * math.pi + 2 * math.pi * t) * h * 0.04)
        path.lineTo(w, h)
        c = QColor(acc)
        c.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c)
        p.drawPath(path)
    elif a == "scan":
        y = h * t
        g = QLinearGradient(0, y - h * 0.12, 0, y + h * 0.02)
        c = QColor(acc)
        c.setAlpha(0)
        g.setColorAt(0, c)
        c.setAlpha(120)
        g.setColorAt(1, c)
        p.fillRect(QRectF(0, y - h * 0.12, w, h * 0.14), QBrush(g))
    elif a == "sparkle":
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(7):
            ph = (t + i / 7) % 1.0
            x = (math.sin(i * 12.9898 + 1) * 43758.5453) % 1.0 * w
            y = (math.sin(i * 78.233 + 2) * 12345.678) % 1.0 * h
            p.setBrush(QColor(255, 255, 255, int(220 * _wave(ph) ** 3)))
            r = 1.2 + 2.2 * _wave(ph)
            p.drawEllipse(QPointF(x, y), r, r)


def _anim_border(v: Visual) -> tuple[QColor, float] | None:
    a, t = v.anim, v.anim_t
    if a == "pulse":
        c = QColor(v.accent)
        c.setAlpha(int(60 + 195 * _wave(t)))
        return c, 1.5 + 2.5 * _wave(t)
    if a == "rainbow":
        return QColor.fromHsvF(t % 1.0, 0.85, 1.0), 2.6
    if a == "alert":
        c = QColor(v.accent)
        c.setAlpha(int(120 + 135 * _wave(t)))
        return c, 3.0
    if a == "heartbeat":
        c = QColor(v.accent)
        c.setAlpha(int(60 + 195 * _heartbeat(t)))
        return c, 1.5 + 2.0 * _heartbeat(t)
    return None


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
    radius = min(w, h) * 0.16 * max(0.0, v.radius)
    clip = QPainterPath()
    clip.addRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
    p.setClipPath(clip)

    _background(p, v, rect, w, h)
    if v.sheen:
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
    fs = max(0.4, min(2.5, v.font_scale))
    weight_big, weight_mid = (700, 600) if v.bold else (400, 400)
    value_family = v.font_value or (None if v.mono else v.font)
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
            isz = min(w, h) * 0.26
        elif has_label:
            isz = min(w, h) * 0.46
        else:
            isz = min(w, h) * 0.58
        icon_img = icon_image(v.icon, v.icon_color or v.fg, int(isz * max(0.3, min(1.6, v.icon_scale))))

    def draw_icon(x: float, y: float) -> None:
        iw, ih = icon_img.width(), icon_img.height()
        if v.anim == "float":
            y += math.sin(2 * math.pi * v.anim_t) * h * 0.04
        if v.anim in ("spin", "heartbeat"):
            p.save()
            p.translate(x + iw / 2, y + ih / 2)
            if v.anim == "spin":
                p.rotate(360 * v.anim_t)
            else:
                k = 1 + 0.14 * _heartbeat(v.anim_t)
                p.scale(k, k)
            p.drawImage(QPointF(-iw / 2, -ih / 2), icon_img)
            p.restore()
        else:
            p.drawImage(QPointF(x, y), icon_img)

    if has_value:
        top = h * 0.10
        if icon_img is not None:
            draw_icon((w - icon_img.width()) / 2, top)
            top += icon_img.height() + h * 0.02
        bottom = h * (0.78 if (has_label or v.sub) else 0.92)
        if has_progress and v.progress_style == "bar":
            bottom = min(bottom, h * (0.69 if (has_label or v.sub) else 0.80))
        area = QRectF(w * 0.06, top, w * 0.88, bottom - top)
        size = area.height() * 0.85 * fs
        p.setFont(_fit_font(v.value, area.width(), size, weight_big, v.mono, value_family))
        p.setPen(fg)
        p.drawText(area, Qt.AlignmentFlag.AlignCenter, v.value)
        caption = v.sub or v.label
        if caption:
            f = _fit_font(caption, w * 0.9, h * 0.13 * fs, 500, False, v.font)
            p.setFont(f)
            c = QColor(fg)
            c.setAlpha(190)
            p.setPen(c)
            p.drawText(QRectF(0, h * 0.78, w, h * 0.18), Qt.AlignmentFlag.AlignCenter, caption)
    else:
        if icon_img is not None:
            y = (h - icon_img.height()) / 2 - (h * 0.10 if has_label else 0)
            draw_icon((w - icon_img.width()) / 2, y)
        if has_label:
            if icon_img is not None:
                area = QRectF(w * 0.05, h * 0.70, w * 0.90, h * 0.24)
                f = _fit_font(v.label, area.width(), h * 0.15 * fs, weight_mid, False, v.font)
            else:
                area = QRectF(w * 0.06, h * 0.06, w * 0.88, h * 0.88)
                f = _fit_font(v.label, area.width(), h * 0.22 * fs, weight_big, False, v.font)
            p.setFont(f)
            p.setPen(fg)
            p.drawText(area, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, v.label)

    if v.badge:
        f = _font(h * 0.12, 700, family=v.font)
        p.setFont(f)
        tw = QFontMetricsF(f).horizontalAdvance(v.badge) + h * 0.10
        pill = QRectF(w - tw - w * 0.06, h * 0.06, tw, h * 0.17)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(v.accent))
        p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
        p.setPen(QColor("#ffffff"))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, v.badge)

    if v.anim:
        _anim_overlay(p, v, rect, w, h)

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

    # border
    p.setClipping(False)
    special = _anim_border(v) if v.anim else None
    if special:
        border, width = special
    elif v.border == "none" and not v.active:
        border, width = None, 0.0
    elif v.active or v.border == "accent":
        border, width = QColor(v.accent), 2.2
        border.setAlpha(170)
    else:
        border, width = QColor(255, 255, 255, 22), 1.0
    if border is not None:
        p.setPen(QPen(border, width))
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
