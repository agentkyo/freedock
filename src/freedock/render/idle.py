"""The idle screen ("screensaver") drawn after the deck has not been touched for a while.

The keys are separate screens with thick bezels between them, so text spread across several keys
is unreadable (half a digit on each). Instead every tile carries one complete piece of
information, big enough to read from the chair:

* top row    - ``H H : M M``: one digit per key, the colon key pulses and shows the seconds
* bottom row - weekday, date, weather, CPU and RAM (each falls back to something useful)
* touch strip - unseen notifications (Claude finished, Codex asks...) or a dim aurora band

Everything sits on a near-black background with a faint, slowly drifting glow: readable at night,
gentle on the LCDs, and the content shifts by a couple of pixels over time against burn-in.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QLocale, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QRadialGradient

from .painter import UI_FONT, icon_image

BG = QColor("#05070b")


@dataclass
class IdleInfo:
    now: time.struct_time
    lang: str = "en"
    weather: dict = field(default_factory=dict)     # {"temp", "icon", "text"} or {}
    cpu: float | None = None
    ram: float | None = None
    alerts: list = field(default_factory=list)      # [(color, icon, name, status)] for the strip


def _canvas(w: int, h: int) -> tuple[QImage, QPainter]:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(BG)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                     | QPainter.RenderHint.SmoothPixmapTransform)
    return img, p


def _hue(t: float, col: int) -> int:
    return int(t * 3 + col * 22) % 360          # one full colour turn every two minutes


def _glow(p: QPainter, w: int, h: int, hue: int, strength: float = 1.0) -> None:
    g = QRadialGradient(QPointF(w * 0.5, h * 1.05), max(w, h) * 0.95)
    g.setColorAt(0, QColor.fromHsv(hue, 210, int(95 * strength)))
    g.setColorAt(1, BG)
    p.fillRect(QRectF(0, 0, w, h), g)


def _font(family: str, px: int, weight=QFont.Weight.Bold) -> QFont:
    f = QFont(family)
    f.setPixelSize(max(6, px))
    f.setWeight(weight)
    return f


def _text(p: QPainter, rect: QRectF, text: str, family: str, px: int, color: QColor,
          weight=QFont.Weight.Bold, align=Qt.AlignmentFlag.AlignCenter, min_px: int = 8) -> None:
    f = _font(family, px, weight)
    # shrink until it fits the rect (weekday names vary a lot between languages);
    # below min_px, wrap onto two lines instead of becoming unreadable
    flags = int(align)
    while True:
        f.setPixelSize(px)
        p.setFont(f)
        if p.fontMetrics().horizontalAdvance(text) <= rect.width():
            break
        if px - 2 < min_px:
            flags |= int(Qt.TextFlag.TextWordWrap)
            break
        px -= 2
    p.setPen(color)
    p.drawText(rect, flags, text)


def _drift(t: float) -> tuple[float, float]:
    """A slow +-2 px wander so static digits never sit on the same pixels for hours."""
    return 2 * math.sin(t / 97), 2 * math.sin(t / 131 + 1)


def _digit_key(ch: str, t: float, col: int, size: tuple[int, int]) -> QImage:
    w, h = size
    img, p = _canvas(w, h)
    _glow(p, w, h, _hue(t, col), 0.9)
    dx, dy = _drift(t)
    _text(p, QRectF(dx, dy - 2, w, h), ch, UI_FONT, int(h * 0.86), QColor(245, 247, 251))
    p.end()
    return img


def _colon_key(info: IdleInfo, t: float, col: int, size: tuple[int, int]) -> QImage:
    w, h = size
    img, p = _canvas(w, h)
    hue = _hue(t, col)
    _glow(p, w, h, hue, 0.9)
    sec = info.now.tm_sec
    # seconds ring
    ring = QRectF(10, 10, w - 20, h - 20)
    p.setPen(QPen(QColor(255, 255, 255, 28), 4))
    p.drawEllipse(ring)
    pen = QPen(QColor.fromHsv(hue, 120, 255), 4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawArc(ring, 90 * 16, -int(360 * 16 * (sec + 1) / 60))
    # the two dots, blinking with the seconds
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(245, 247, 251, 255 if sec % 2 == 0 else 90))
    r = w * 0.075
    for cy in (h * 0.36, h * 0.64):
        p.drawEllipse(QPointF(w / 2, cy), r, r)
    p.end()
    return img


def _info_key(top: str, main: str, size: tuple[int, int], t: float, col: int,
              icon: str | None = None, bar: float | None = None, accent: QColor | None = None) -> QImage:
    w, h = size
    img, p = _canvas(w, h)
    hue = _hue(t, col)
    _glow(p, w, h, hue, 0.7)
    dx, dy = _drift(t)
    accent = accent or QColor.fromHsv(hue, 90, 255)
    if top:
        _text(p, QRectF(6 + dx, 8 + dy, w - 12, h * 0.24), top, UI_FONT, int(h * 0.19), accent,
              QFont.Weight.DemiBold)
    y0 = h * 0.30 if top else h * 0.12
    if icon and (ic := icon_image(icon, "#f5f7fb", int(h * 0.36), 1.8)):
        p.drawImage(QPointF((w - ic.width()) / 2 + dx, y0 + dy), ic)
        _text(p, QRectF(dx, y0 + h * 0.36 + dy, w, h * 0.32), main, UI_FONT, int(h * 0.28),
              QColor(245, 247, 251))
    else:
        _text(p, QRectF(4 + dx, y0 + dy, w - 8, h * (0.62 if bar is None else 0.5)), main, UI_FONT,
              int(h * 0.40), QColor(245, 247, 251))
    if bar is not None:
        frac = max(0.0, min(1.0, bar))
        track = QRectF(14, h - 20, w - 28, 7)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(track, 3.5, 3.5)
        level = QColor("#22c55e") if frac < 0.6 else QColor("#f59e0b") if frac < 0.85 else QColor("#ef4444")
        p.setBrush(level)
        if frac > 0:   # even 2 % shows as a dot, so the bar never looks broken
            p.drawRoundedRect(QRectF(track.x(), track.y(), max(track.height(), track.width() * frac),
                                     track.height()), 3.5, 3.5)
    p.end()
    return img


def keys(info: IdleInfo, t: float, size: tuple[int, int], count: int = 10) -> list[QImage]:
    loc = QLocale(info.lang)
    n = info.now
    hhmm = f"{n.tm_hour:02d}{n.tm_min:02d}"
    tiles = [_digit_key(hhmm[0], t, 0, size), _digit_key(hhmm[1], t, 1, size), _colon_key(info, t, 2, size),
             _digit_key(hhmm[2], t, 3, size), _digit_key(hhmm[3], t, 4, size)]
    # Qt: 1 = Monday ... 7 = Sunday; struct_time: 0 = Monday
    weekday = loc.dayName(n.tm_wday + 1, QLocale.FormatType.ShortFormat).rstrip(".").upper()
    month = loc.monthName(n.tm_mon, QLocale.FormatType.ShortFormat).rstrip(".").upper()
    tiles.append(_info_key("", weekday, size, t, 5))
    tiles.append(_info_key(month, f"{n.tm_mday:02d}", size, t, 6))
    w = info.weather
    if w and w.get("temp") is not None:
        tiles.append(_info_key("", f"{w['temp']:.0f}°", size, t, 7, icon=w.get("icon") or "cloud-sun"))
    else:
        week = int(time.strftime("%V", info.now))
        tiles.append(_info_key(str(n.tm_year), f"S{week}" if info.lang in ("pt", "es") else f"W{week}",
                               size, t, 7))
    for col, (label, val) in ((8, ("CPU", info.cpu)), (9, ("RAM", info.ram))):
        if val is None:
            tiles.append(_info_key(label, "–", size, t, col))
        else:
            tiles.append(_info_key(label, f"{val:.0f}%", size, t, col, bar=val / 100))
    return tiles[:count]


def zones(info: IdleInfo, t: float, size: tuple[int, int], count: int = 4) -> list[QImage]:
    w, h = size
    out = []
    alerts = info.alerts[:count]
    for i in range(count):
        img, p = _canvas(w, h)
        if i < len(alerts):
            color, icon, name, status = alerts[i]
            c = QColor(color)
            pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 2.5))
            _glow(p, w, h, c.hsvHue() if c.hsvHue() >= 0 else 0, pulse)
            isz = int(h * 0.40)
            if ic := icon_image(icon, color, isz, 2.2):
                p.drawImage(QPointF(12, (h - ic.height()) / 2), ic)
            x = 22 + isz
            left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            _text(p, QRectF(x, h * 0.16, w - x - 8, h * 0.36), name, UI_FONT, int(h * 0.30),
                  QColor(245, 247, 251), QFont.Weight.Bold, left)
            if status:
                _text(p, QRectF(x, h * 0.50, w - x - 6, h * 0.44), status, UI_FONT, int(h * 0.21), c,
                      QFont.Weight.DemiBold, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                      min_px=17)
        else:
            # a dim band that continues across the strip; the zones light up left to right in turn
            phase = (t / 4 - i * 0.25) % 1.0
            _glow(p, w, h, _hue(t, i * 2 + 1), 0.35 + 0.25 * math.exp(-((phase - 0.5) ** 2) / 0.02))
        p.end()
        out.append(img)
    return out
