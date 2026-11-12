"""Light scenes: pure functions from (time, game state) to colors for the keyboard, the strip and the
other Chroma devices. No I/O here - :mod:`game_lights` sends the frames.

A *theme* is a game's look: a palette, an animated background and highlighted keys (WASD for a
shooter, the arrows for a sim...). On top of it each game can paint its live state (CS2: health on
the F-row, the C4 countdown on the strip...) and short *effects* (a kill, a flashbang, the bomb
going off). Everything is resolution independent: strips of any length, the 6x22 Chroma keyboard grid.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

Color = tuple[int, int, int]
BLACK: Color = (0, 0, 0)
WHITE: Color = (255, 255, 255)
ROWS, COLS = 6, 22


# =========================================================================== colors

def hexc(h: str) -> Color:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(a: Color, b: Color, t: float) -> Color:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t), round(a[2] + (b[2] - a[2]) * t))


def scale(c: Color, k: float) -> Color:
    k = max(0.0, k)
    return (min(255, round(c[0] * k)), min(255, round(c[1] * k)), min(255, round(c[2] * k)))


def add(a: Color, b: Color) -> Color:
    return (min(255, a[0] + b[0]), min(255, a[1] + b[1]), min(255, a[2] + b[2]))


def hsv(h: float, s: float = 1.0, v: float = 1.0) -> Color:
    h = (h % 1.0) * 6
    i, f = int(h), h - int(h)
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i % 6]
    return round(r * 255), round(g * 255), round(b * 255)


def gradient(stops: Sequence[Color], t: float) -> Color:
    if len(stops) == 1:
        return stops[0]
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    return mix(stops[i], stops[i + 1], t - i)


def noise(*k: int) -> float:
    """Deterministic 0..1 hash noise (sparks, embers) - the same frame always looks the same."""
    n = 2166136261
    for v in k:
        n = ((n ^ (v & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF
    n ^= n >> 13
    n = (n * 1274126177) & 0xFFFFFFFF
    return (n & 0xFFFF) / 0xFFFF


# =========================================================================== keyboard map

# Chroma SDK keyboard grid positions (row, col) - RZKEY_* = row << 8 | col
KEYS: dict[str, tuple[int, int]] = {"esc": (0, 1), "prtsc": (0, 15), "scrlk": (0, 16), "pause": (0, 17)}
KEYS.update({f"f{i}": (0, 2 + i) for i in range(1, 13)})
KEYS.update({"`": (1, 1), "-": (1, 12), "=": (1, 13), "backspace": (1, 14), "ins": (1, 15), "home": (1, 16),
             "pgup": (1, 17), "numlk": (1, 18), "num/": (1, 19), "num*": (1, 20), "num-": (1, 21)})
KEYS.update({str((i + 1) % 10): (1, 2 + i) for i in range(10)})
KEYS.update({c: (2, 2 + i) for i, c in enumerate("qwertyuiop")})
KEYS.update({"tab": (2, 1), "[": (2, 12), "]": (2, 13), "\\": (2, 14), "del": (2, 15), "end": (2, 16),
             "pgdn": (2, 17), "num7": (2, 18), "num8": (2, 19), "num9": (2, 20), "num+": (2, 21)})
KEYS.update({c: (3, 2 + i) for i, c in enumerate("asdfghjkl")})
KEYS.update({"caps": (3, 1), ";": (3, 11), "'": (3, 12), "enter": (3, 14), "num4": (3, 18), "num5": (3, 19),
             "num6": (3, 20)})
KEYS.update({c: (4, 3 + i) for i, c in enumerate("zxcvbnm")})
KEYS.update({"lshift": (4, 1), ",": (4, 10), ".": (4, 11), "/": (4, 12), "rshift": (4, 14), "up": (4, 16),
             "num1": (4, 18), "num2": (4, 19), "num3": (4, 20), "numenter": (4, 21)})
KEYS.update({"lctrl": (5, 1), "lwin": (5, 2), "lalt": (5, 3), "space": (5, 7), "ralt": (5, 11), "fn": (5, 12),
             "menu": (5, 13), "rctrl": (5, 14), "left": (5, 15), "down": (5, 16), "right": (5, 17),
             "num0": (5, 19), "num.": (5, 20)})

GROUPS: dict[str, tuple[str, ...]] = {
    "wasd": ("w", "a", "s", "d"),
    "arrows": ("up", "down", "left", "right"),
    "numbers": tuple("1234567890"),
    "weapons": tuple("12345"),
    "frow": tuple(f"f{i}" for i in range(1, 13)),
    "mods": ("lshift", "lctrl", "space"),
    "movement": ("w", "a", "s", "d", "lshift", "lctrl", "space"),
    "util": ("q", "e", "r", "f", "g"),
    "numpad": ("num0", "num1", "num2", "num3", "num4", "num5", "num6", "num7", "num8", "num9"),
}
F_ROW = [KEYS[f"f{i}"] for i in range(1, 13)]
NUMBER_ROW = [KEYS[str((i + 1) % 10)] for i in range(10)]


def keys_of(spec: str | Sequence[str]) -> list[tuple[int, int]]:
    """'wasd' (a group), 'qerf' (letters) or ['space', 'lshift'] -> grid cells."""
    names: list[str] = []
    for part in ([spec] if isinstance(spec, str) else spec):
        if part in GROUPS:
            names += GROUPS[part]
        elif part in KEYS:
            names.append(part)
        else:
            names += [c for c in part if c in KEYS]
    return [KEYS[n] for n in names]


# =========================================================================== frames

@dataclass
class Frame:
    """One picture of every light. ``strip=None`` leaves the strip to the user's own mode (ambilight)."""
    keyboard: list[list[Color]]
    strip: list[Color] | None = None
    glow: Color = BLACK             # mouse, mousepad, headset, Chroma Link

    @classmethod
    def blank(cls, n: int | None = None, color: Color = BLACK) -> "Frame":
        return cls([[color] * COLS for _ in range(ROWS)], [color] * n if n else None, color)

    def fill_keyboard(self, fn: Callable[[int, int, Color], Color]) -> None:
        for r in range(ROWS):
            row = self.keyboard[r]
            for c in range(COLS):
                row[c] = fn(r, c, row[c])

    def overlay(self, color: Color, amount: float, strip: bool = True) -> None:
        """Blend every light toward `color` (flashes, fades)."""
        if amount <= 0:
            return
        self.fill_keyboard(lambda r, c, cur: mix(cur, color, amount))
        if strip and self.strip is not None:
            self.strip = [mix(cur, color, amount) for cur in self.strip]
        self.glow = mix(self.glow, color, amount)

    def bar(self, cells: Sequence[tuple[int, int]], ratio: float, colors: Sequence[Color],
            off: Color | None = None) -> None:
        """A level meter on a row of keys; each lit key keeps the gradient color of its position."""
        n = len(cells)
        lit = ratio * n
        for i, (r, c) in enumerate(cells):
            on = max(0.0, min(1.0, lit - i))
            base = self.keyboard[r][c] if off is None else off
            self.keyboard[r][c] = mix(base, gradient(colors, i / max(1, n - 1)), on)


def strip_bar(n: int, ratio: float, colors: Sequence[Color], off: Color = BLACK, mirror: bool = True,
              inward: bool = False) -> list[Color]:
    """A strip meter. mirror: both ends at once (around a monitor). inward: drains from the ends toward
    the middle (countdowns) instead of rising from them."""
    ratio = max(0.0, min(1.0, ratio))
    if n <= 0:
        return []
    if ratio <= 0.0 or ratio >= 1.0:              # exact ends: nothing / everything (no half-lit edge LED)
        return [off] * n if ratio <= 0.0 else [gradient(colors, min(i, n - 1 - i) / max(1, (n - 1) / 2))
                                              if mirror else gradient(colors, i / max(1, n - 1)) for i in range(n)]
    if not mirror:
        lit = ratio * n
        return [mix(off, gradient(colors, i / max(1, n - 1)), max(0.0, min(1.0, lit - i))) for i in range(n)]
    half = (n + 1) // 2
    out = []
    for i in range(n):
        d = min(i, n - 1 - i) / max(1, half - 1)            # 0 at the ends, 1 in the middle
        # rising: lit where d < ratio; draining: lit where d > 1 - ratio (what is left gathers in the middle)
        edge = (d - (1 - ratio)) if inward else (ratio - d)
        on = max(0.0, min(1.0, edge * half + 0.5))
        out.append(mix(off, gradient(colors, d), on))
    return out


# =========================================================================== themes

@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    processes: tuple[str, ...]
    base: str                         # background
    primary: str                      # main color (highlighted keys, glow)
    secondary: str                    # second color of the animation
    anim: str = "wave"                # static | breathe | wave | aurora | embers | split | pulse | scan
    speed: float = 1.0
    keys: dict = field(default_factory=dict)     # key spec -> hex color, e.g. {"wasd": "#de9b35"}
    native_chroma: bool = False       # the game drives Chroma itself: leave the keyboard alone
    page: str | None = None           # FreeDock page to open (auto-switch suggestion)


THEMES: dict[str, Theme] = {t.id: t for t in (
    Theme("cs2", "Counter-Strike 2", ("cs2.exe",), "#06101c", "#de9b35", "#5d79ae", "wave", 0.6,
          {"wasd": "#de9b35", "mods": "#5d79ae", "util": "#e7e2d4", "weapons": "#5d79ae"}, page="cs2"),
    Theme("ets2", "Euro Truck Simulator 2", ("eurotrucks2.exe", "amtrucks.exe"), "#080b14", "#f59e0b",
          "#1d4ed8", "breathe", 0.35, {"wasd": "#f59e0b", "arrows": "#f59e0b", "space": "#ef4444",
                                       "l": "#22c55e", "k": "#3b82f6", "e": "#22c55e"}, page="ets2"),
    Theme("beamng", "BeamNG.drive", ("beamng.drive.x64.exe",), "#0c0a09", "#f97316", "#14b8a6", "wave", 0.8,
          {"wasd": "#f97316", "arrows": "#f97316", "space": "#ef4444"}, page="beamng"),
    Theme("assetto", "Assetto Corsa", ("acs.exe", "acc.exe", "ac2-win64-shipping.exe"), "#0a0000", "#ef4444",
          "#f5f5f5", "scan", 0.9, {"wasd": "#ef4444", "arrows": "#ef4444"}, page="assetto"),
    Theme("msfs", "Flight Simulator", ("flightsimulator2024.exe", "flightsimulator.exe"), "#020617", "#22d3ee",
          "#1e3a8a", "aurora", 0.25, {"numpad": "#22d3ee", "g": "#22c55e", "b": "#ef4444", "f5": "#facc15",
                                      "f6": "#facc15", "f7": "#facc15", "f8": "#facc15"}, page="msfs"),
    Theme("forza", "Forza Horizon 5", ("forzahorizon5.exe",), "#12001f", "#ec4899", "#22d3ee", "aurora", 0.7,
          {"wasd": "#ec4899", "arrows": "#ec4899"}),
    Theme("fc26", "EA SPORTS FC 26", ("fc26.exe",), "#021a0c", "#22c55e", "#f8fafc", "scan", 0.4,
          {"wasd": "#f8fafc", "arrows": "#f8fafc", "space": "#22c55e"}),
    Theme("beatsaber", "Beat Saber", ("beat saber.exe",), "#05000a", "#ff1f3d", "#1f6bff", "split", 1.4),
    Theme("rocksmith", "Rocksmith 2014", ("rocksmith2014.exe",), "#0f0418", "#a855f7", "#facc15", "pulse", 0.9),
    Theme("taxilife", "Taxi Life", ("taxilife-win64-shipping.exe", "taxilife.exe"), "#0b0b0b", "#facc15",
          "#111111", "breathe", 0.5, {"wasd": "#facc15", "arrows": "#facc15"}),
    Theme("precinct", "The Precinct", ("precinct.exe",), "#020617", "#ef4444", "#2563eb", "split", 2.2,
          {"wasd": "#f8fafc"}),
    Theme("bodycam", "Bodycam", ("bodycam-win64-shipping.exe", "bodycam.exe"), "#050505", "#e5e7eb", "#7f1d1d",
          "breathe", 0.4, {"wasd": "#e5e7eb", "util": "#7f1d1d", "weapons": "#9ca3af"}),
    Theme("formulalegends", "Formula Legends", ("asc-win64-shipping.exe",), "#0a0012", "#f43f5e", "#38bdf8",
          "scan", 1.0, {"wasd": "#f43f5e", "arrows": "#f43f5e"}),
    Theme("tetris", "Tetris Effect", ("tetriseffect-win64-shipping.exe",), "#000010", "#38bdf8", "#a855f7",
          "aurora", 0.5, native_chroma=True),
)}


def theme_for(processes: set[str] | Sequence[str], foreground: str | None = None) -> Theme | None:
    """The theme of the game in the foreground, else of any running game."""
    procs = {p.lower() for p in processes}
    fg = (foreground or "").lower()
    if fg:
        for t in THEMES.values():
            if fg in t.processes:
                return t
    for t in THEMES.values():
        if procs & set(t.processes):
            return t
    return None


def render_theme(theme: Theme, t: float, n_leds: int | None) -> Frame:
    """The animated background of a theme with its highlighted keys."""
    base, p, s = hexc(theme.base), hexc(theme.primary), hexc(theme.secondary)
    k = t * theme.speed
    anim = theme.anim

    def at(x: float, y: float) -> Color:              # x, y in 0..1 (keyboard) - y=0 for the strip
        if anim == "static":
            return mix(base, p, 0.25)
        if anim == "breathe":
            return mix(base, p, 0.12 + 0.22 * (0.5 + 0.5 * math.sin(k * 2 * math.pi * 0.5)))
        if anim == "wave":
            w = 0.5 + 0.5 * math.sin((x * 1.6 - y * 0.6 - k * 0.5) * 2 * math.pi)
            return mix(base, mix(p, s, w), 0.18 + 0.2 * w)
        if anim == "aurora":
            w = 0.5 + 0.5 * math.sin((x * 1.1 + 0.4 * math.sin(k * 0.7 + y * 3)) * 2 * math.pi - k)
            return mix(base, mix(s, p, w), 0.25 + 0.25 * w)
        if anim == "embers":
            spark = noise(int(x * 97), int(y * 13), int(k * 6))
            return mix(base, p, 0.15 + (0.6 if spark > 0.93 else 0.0))
        if anim == "split":                               # two halves (sabers, sirens)
            left = (x < 0.5) == (int(k * 2) % 2 == 0) if theme.id == "precinct" else x < 0.5
            pulse = 0.35 + 0.25 * (0.5 + 0.5 * math.sin(k * 6))
            return mix(base, p if left else s, pulse)
        if anim == "pulse":
            beat = math.exp(-((k * 2) % 1.0) * 5)
            return mix(base, mix(p, s, x), 0.12 + 0.45 * beat)
        if anim == "scan":                                # a light bar sweeping across
            pos = (k * 0.6) % 1.4 - 0.2
            d = abs(x - pos)
            return mix(mix(base, s, 0.06), p, max(0.0, 0.55 - d * 3.5))
        return base

    frame = Frame([[at(c / (COLS - 1), r / (ROWS - 1)) for c in range(COLS)] for r in range(ROWS)],
                  [at(i / max(1, n_leds - 1), 0.0) for i in range(n_leds)] if n_leds else None,
                  mix(base, p, 0.55))
    glow_beat = 0.5 + 0.5 * math.sin(k * 2 * math.pi * 0.3)
    for spec, color in theme.keys.items():
        col = hexc(color)
        for r, c in keys_of(spec):
            frame.keyboard[r][c] = mix(scale(col, 0.75), col, glow_beat)
    return frame


# =========================================================================== effects

@dataclass
class Effect:
    """A short animation over the scene. kind: flash | fade | strobe | sweep | burst."""
    kind: str
    color: Color
    t0: float
    dur: float
    strip: bool = True              # also takes the strip (event-only strip mode)
    color2: Color | None = None

    def done(self, now: float) -> bool:
        return now - self.t0 >= self.dur

    def apply(self, frame: Frame, now: float) -> None:
        t = (now - self.t0) / self.dur
        if t < 0 or t >= 1:
            return
        if self.kind == "flash":                          # instant hit, quick fall
            frame.overlay(self.color, (1 - t) ** 2, self.strip)
        elif self.kind == "fade":                         # rises then holds then falls (death, round lost)
            frame.overlay(self.color, min(1.0, t * 6) * (1 - t) ** 0.6, self.strip)
        elif self.kind == "strobe":
            frame.overlay(self.color, 0.9 if int(t * self.dur * 12) % 2 == 0 else 0.0, self.strip)
        elif self.kind == "sweep":                        # a band crossing left -> right (round won)
            pos = t * 1.6 - 0.3
            c2 = self.color2 or self.color
            frame.fill_keyboard(lambda r, c, cur: mix(cur, mix(self.color, c2, c / COLS),
                                                      max(0.0, 1 - abs(c / (COLS - 1) - pos) * 4)))
            if self.strip and frame.strip is not None:
                n = len(frame.strip)
                frame.strip = [mix(cur, mix(self.color, c2, i / n), max(0.0, 1 - abs(
                    min(i, n - 1 - i) / max(1, n / 2) - pos) * 3)) for i, cur in enumerate(frame.strip)]
        elif self.kind == "burst":                        # from the middle outward (explosion, headshot)
            radius = t * 1.3
            fade = (1 - t) ** 1.5

            def hit(d: float) -> float:
                return max(0.0, 1 - abs(d - radius) * 3) * fade + (0.6 * fade if d < radius else 0.0)

            frame.fill_keyboard(lambda r, c, cur: mix(cur, self.color, hit(math.hypot(
                (c - COLS / 2) / (COLS / 2), (r - ROWS / 2) / (ROWS / 1.2)))))
            if self.strip and frame.strip is not None:
                n = len(frame.strip)
                # the strip goes around the monitor: its middle is the top centre, ends at the bottom
                frame.strip = [mix(cur, self.color, hit(abs(i - n / 2) / (n / 2))) for i, cur in enumerate(frame.strip)]
            frame.glow = mix(frame.glow, self.color, fade)


# =========================================================================== CS2

HP_COLORS = (hexc("#ef4444"), hexc("#f59e0b"), hexc("#22c55e"))
AMMO_COLORS = (hexc("#ef4444"), hexc("#f8fafc"), hexc("#f8fafc"))
BOMB_COLORS = (hexc("#ff2a00"), hexc("#ff7a00"))
CT, T_SIDE = hexc("#5d79ae"), hexc("#de9b35")


def bomb_beep(left: float, total: float = 40.0) -> float:
    """0..1 light of the C4 LED: short blinks that speed up like the real beeps (~1/s -> ~8/s)."""
    if left <= 0:
        return 1.0
    e = max(0.0, total - left)
    f0, f1 = 1.0, 7.0                     # beeps per second right after the plant / at the end
    # beep frequency f(e) = f0 + (f1 - f0) (e / total)^2; the phase is its integral, so blinks never jump
    beeps = f0 * e + (f1 - f0) * e ** 3 / (3 * total ** 2)
    return math.exp(-(beeps % 1.0) * 9)


def cs2_frame(theme: Theme, d: dict, now: float, n_leds: int | None, strip_always: bool) -> Frame:
    """The CS2 scene from a telemetry snapshot (integrations.cs2)."""
    frame = render_theme(theme, now, n_leds if strip_always else None)
    if not d.get("connected") or not d.get("in_match"):
        return frame
    team = d.get("team") or d.get("player_team")
    tc = CT if team == "CT" else T_SIDE if team == "T" else hexc(theme.primary)
    base = scale(tc, 0.12)

    if d.get("spectating") or not d.get("alive", True):   # dead: grey world, team ember
        frame.fill_keyboard(lambda r, c, cur: mix(hexc("#0b0b0f"), scale(tc, 0.2), 0.3 + 0.2 * math.sin(now + c * 0.3)))
        frame.glow = scale(tc, 0.15)
    else:
        frame.fill_keyboard(lambda r, c, cur: mix(base, cur, 0.35))
        for r, c in keys_of("wasd"):
            frame.keyboard[r][c] = tc
        for r, c in keys_of(("lshift", "lctrl", "space")):
            frame.keyboard[r][c] = scale(tc, 0.55)
        hp = (d.get("health") or 0) / 100
        frame.bar(F_ROW, hp, HP_COLORS, off=hexc("#1a0505"))
        ammo, amax = d.get("ammo"), d.get("ammo_max")
        if ammo is not None and amax:
            frame.bar(NUMBER_ROW, ammo / amax, AMMO_COLORS, off=hexc("#0a0a0a"))
        if d.get("round_phase") == "freezetime":           # buy time: B and the money glow gold
            gold = 0.5 + 0.5 * math.sin(now * 5)
            for r, c in keys_of("b"):
                frame.keyboard[r][c] = mix(hexc("#facc15"), WHITE, gold * 0.4)
        if d.get("defusekit") and team == "CT":
            r, c = KEYS["e"]
            frame.keyboard[r][c] = hexc("#22c55e")
        if 0 < (d.get("health") or 0) < 30:               # heartbeat: faster the lower you are
            rate = 1.2 + (30 - d["health"]) / 30 * 1.8
            beat = math.exp(-((now * rate) % 1.0) * 6)
            frame.fill_keyboard(lambda r, c, cur: mix(cur, hexc("#b91c1c"), 0.55 * beat) if r > 0 else cur)
            frame.glow = mix(frame.glow, hexc("#b91c1c"), beat)
        else:
            frame.glow = tc
        if d.get("burning"):
            frame.fill_keyboard(lambda r, c, cur: mix(cur, hexc("#ff5a00"), d["burning"] * 0.7 * noise(r, c, int(now * 14))))

    left = d.get("bomb_left")
    if d.get("bomb") == "planted" and left is not None:
        total = d.get("bomb_total") or 40.0
        beep = bomb_beep(left, total)
        frac = left / total
        red = gradient(BOMB_COLORS, frac)
        # keyboard: the C4 LED blinks over everything, the F-row becomes the countdown
        frame.fill_keyboard(lambda r, c, cur: mix(cur, red, 0.25 + 0.55 * beep))
        frame.bar(F_ROW, frac, (hexc("#ff0000"), hexc("#ff7a00"), hexc("#ffd000")), off=hexc("#140000"))
        # number row: can the CTs still make it? green yes, red no
        ok = d.get("can_defuse")
        verdict = hexc("#22c55e") if ok else hexc("#ef4444")
        for r, c in NUMBER_ROW:
            frame.keyboard[r][c] = mix(scale(verdict, 0.35), verdict, beep)
        if d.get("defusing") and d.get("defuse_left") is not None:
            need = d.get("defuse_need") or 10.0
            done = 1 - d["defuse_left"] / need
            frame.bar(keys_of("qwertyuiop"), done, (hexc("#1d4ed8"), hexc("#60a5fa")), off=hexc("#020617"))
        frame.glow = mix(scale(red, 0.4), red, beep)
        if n_leds:
            ring = strip_bar(n_leds, frac, (hexc("#ff7a00"), hexc("#ff1a00")), off=hexc("#0a0000"), inward=True)
            light = 0.35 + 0.65 * beep
            if d.get("defusing") and d.get("defuse_left") is not None:
                need = d.get("defuse_need") or 10.0
                blue = strip_bar(n_leds, 1 - d["defuse_left"] / need, (hexc("#60a5fa"), hexc("#1d4ed8")),
                                 off=BLACK, mirror=True)
                ring = [add(scale(a, 0.6), b) for a, b in zip(ring, blue)]
            elif not ok:                                  # too late to defuse: hard strobe
                light = 1.0 if int(now * 10) % 2 == 0 else 0.15
            frame.strip = [scale(c, light) for c in ring]

    flashed = d.get("flashed") or 0
    if flashed > 0.02 and not d.get("spectating"):
        frame.overlay(WHITE, min(1.0, flashed * 1.1), strip=True)
        if frame.strip is None and n_leds:
            frame.strip = [scale(WHITE, flashed) for _ in range(n_leds)]
    smoked = d.get("smoked") or 0
    if smoked > 0.05:
        frame.overlay(hexc("#4b5563"), smoked * 0.6, strip=False)
    return frame


def cs2_effect(name: str, info: dict, now: float, team: str | None) -> Effect | None:
    """The light effect of a CS2 event (see integrations.cs2 events)."""
    tc = CT if team == "CT" else T_SIDE
    return {
        "kill": Effect("flash", tc, now, 0.35, strip=True),
        "headshot": Effect("burst", hexc("#facc15"), now, 0.7, strip=True),
        "death": Effect("fade", hexc("#7f1d1d"), now, 1.6, strip=True),
        "hurt": Effect("flash", hexc("#dc2626"), now, 0.25, strip=False) if info.get("damage", 0) >= 15 else None,
        "bomb_planted": Effect("strobe", hexc("#ff2a00"), now, 0.5),
        "bomb_defused": Effect("sweep", CT, now, 1.4, color2=hexc("#93c5fd")),
        "bomb_exploded": Effect("burst", hexc("#ffb020"), now, 2.6, color2=hexc("#ff3d00")),
        "round_won": Effect("sweep", tc, now, 1.6, color2=WHITE),
        "round_lost": Effect("fade", hexc("#3f0a0a"), now, 1.6),
        "match_won": Effect("sweep", hexc("#facc15"), now, 3.0, color2=hexc("#22c55e")),
        "match_lost": Effect("fade", hexc("#450a0a"), now, 3.0),
        "freezetime": Effect("sweep", tc, now, 1.0, strip=False),
    }.get(name)


# =========================================================================== racing / trucks

SHIFT_COLORS = (hexc("#22c55e"), hexc("#facc15"), hexc("#ef4444"))


def shift_overlay(frame: Frame, frac: float, n_leds: int | None, now: float = 0.0) -> None:
    """Rev bar: F-row on the keyboard, both sides of the strip; at the limiter both blink blue."""
    if frac >= 0.97:
        on = int(now * 12) % 2 == 0
        blue = hexc("#3b82f6") if on else hexc("#0b1a3a")
        frame.bar(F_ROW, 1.0, (blue,), off=BLACK)
        if n_leds:
            frame.strip = [blue] * n_leds
        return
    frame.bar(F_ROW, frac, SHIFT_COLORS, off=hexc("#050505"))
    if n_leds:
        frame.strip = strip_bar(n_leds, frac, SHIFT_COLORS, off=BLACK, mirror=True)


def blinker_overlay(frame: Frame, left: bool, right: bool, n_leds: int | None, beacon: bool = False,
                    now: float = 0.0) -> None:
    """Truck/car blinkers in sync with the dashboard lamps: that side of the keyboard and strip."""
    amber = hexc("#ffb000")
    if left or right:
        def paint(r, c, cur):
            if (left and c < 8) or (right and c > 13):
                return mix(cur, amber, 0.85)
            return cur
        frame.fill_keyboard(paint)
        if n_leds:
            strip = frame.strip or [BLACK] * n_leds
            third = n_leds // 3
            frame.strip = [amber if (left and i < third) or (right and i >= n_leds - third) else c
                           for i, c in enumerate(strip)]
    if beacon:                                            # rotating orange beacon across the keyboard
        pos = (now * 1.8) % 1.0
        frame.fill_keyboard(lambda r, c, cur: mix(cur, hexc("#ff8a00"), max(0.0, 1 - abs(c / (COLS - 1) - pos) * 6)))
