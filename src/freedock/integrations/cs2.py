"""Counter-Strike 2 through Valve's Game State Integration (GSI).

CS2 reads every ``game/csgo/cfg/gamestate_integration_*.cfg`` at start-up and POSTs JSON to each
``uri`` whenever the state changes (plus a heartbeat). FreeDock installs its own file next to the
ones Razer Cortex / Logitech G HUB put there and listens on http://127.0.0.1:47812.

What GSI tells a *player* (not only observers): map + score, round phase and the bomb state
(``planted`` / ``defused`` / ``exploded``), the player's health, armor, flash/smoke/fire, money,
kills, weapons, and ``phase_countdowns`` (time left in the current phase - while the bomb ticks this
is the bomb timer). When phase_countdowns is missing the C4 timer runs locally from the moment the
plant was reported (40 s in CS2). While you are dead, ``player`` is whoever you spectate: own-player
events (kills, death, flashes) are only raised for your own steamid.

``telemetry.data`` is a snapshot dict for widgets; ``telemetry.events_since(cursor)`` is the event
stream the game lighting reacts to (bomb planted, kill, headshot, death, round won/lost...).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

GSI_PORT = 47812
CFG_NAME = "gamestate_integration_freedock.cfg"
CFG_REV = 1
GAME_FOLDER = "Counter-Strike Global Offensive"
BOMB_S = 40.0               # C4 timer in CS2
DEFUSE_S, DEFUSE_KIT_S = 10.0, 5.0
STALE_S = 15.0              # no POST (heartbeat is 2 s) for this long = game closed
LOW_HP = 30
PROCESS = "cs2.exe"

TEAM_COLOR = {"CT": "#5d79ae", "T": "#de9b35"}


def cfg_text(port: int = GSI_PORT, token: str = "") -> str:
    return f'''"FreeDock v{CFG_REV}"
{{
    "uri"        "http://127.0.0.1:{int(port)}/"
    "timeout"    "1.1"
    "buffer"     "0.0"
    "throttle"   "0.05"
    "heartbeat"  "2.0"
    "auth"
    {{
        "token"  "{token}"
    }}
    "data"
    {{
        "provider"            "1"
        "map"                 "1"
        "round"               "1"
        "player_id"           "1"
        "player_state"        "1"
        "player_weapons"      "1"
        "player_match_stats"  "1"
        "phase_countdowns"    "1"
        "bomb"                "1"
    }}
}}
'''


def new_token() -> str:
    return secrets.token_urlsafe(18)


def cfg_dirs() -> list[Path]:
    from .ets2 import steam_libraries
    out = []
    for lib in steam_libraries():
        d = lib / "steamapps" / "common" / GAME_FOLDER / "game" / "csgo" / "cfg"
        if d.is_dir():
            out.append(d)
    return out


def install_cfg(port: int, token: str, dirs: list[Path] | None = None) -> list[Path]:
    """Write (or refresh) FreeDock's GSI file in every CS2 install. Returns the files written; an
    up-to-date file is left alone. CS2 reads it at start-up, so a running game needs a restart."""
    written = []
    text = cfg_text(port, token)
    for d in cfg_dirs() if dirs is None else dirs:
        path = Path(d) / CFG_NAME
        try:
            if path.is_file() and path.read_text(encoding="utf-8", errors="replace") == text:
                continue
            path.write_text(text, encoding="utf-8")
            written.append(path)
            log.info("cs2: game state integration installed at %s", path)
        except OSError as exc:
            log.warning("cs2: cannot write %s: %s", path, exc)
    return written


# =========================================================================== state

def _int(v, default: int | None = 0) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _active_weapon(weapons: dict) -> dict:
    for w in (weapons or {}).values():
        if isinstance(w, dict) and w.get("state") == "active":
            return w
    return {}


def weapon_name(raw: str | None) -> str:
    """'weapon_ak47' -> 'AK-47'."""
    if not raw:
        return ""
    name = raw.removeprefix("weapon_")
    pretty = {"ak47": "AK-47", "m4a1": "M4A4", "m4a1_silencer": "M4A1-S", "awp": "AWP", "deagle": "Deagle",
              "usp_silencer": "USP-S", "glock": "Glock", "hkp2000": "P2000", "p250": "P250", "tec9": "Tec-9",
              "fiveseven": "Five-SeveN", "cz75a": "CZ75", "revolver": "R8", "elite": "Dualies",
              "galilar": "Galil", "famas": "FAMAS", "sg556": "SG 553", "aug": "AUG", "ssg08": "Scout",
              "mac10": "MAC-10", "mp9": "MP9", "mp7": "MP7", "mp5sd": "MP5", "ump45": "UMP", "p90": "P90",
              "bizon": "Bizon", "nova": "Nova", "xm1014": "XM1014", "mag7": "MAG-7", "sawedoff": "Sawed-Off",
              "negev": "Negev", "m249": "M249", "g3sg1": "G3SG1", "scar20": "SCAR-20", "taser": "Zeus",
              "c4": "C4", "hegrenade": "HE", "flashbang": "Flash", "smokegrenade": "Smoke",
              "molotov": "Molotov", "incgrenade": "Incendiary", "decoy": "Decoy"}
    if name.startswith(("knife", "bayonet")):
        return "Knife"
    return pretty.get(name, name.replace("_", " ").title())


class Cs2State:
    """Folds GSI payloads into a snapshot and an event stream. Thread-safe; `clock` is injectable."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._s: dict = {}
        self._last_post = -1e9
        self._events: deque = deque(maxlen=64)
        self._seq = 0
        self.my_id: str | None = None
        self.my_team: str | None = None
        self.bomb_ends: float | None = None
        self.bomb_estimated = False
        self.defuse_ends: float | None = None

    # -- events
    def _emit(self, name: str, **info) -> None:
        self._seq += 1
        self._events.append((self._seq, self.clock(), name, info))
        log.debug("cs2 event %s %s", name, info)

    def events_since(self, cursor: int) -> tuple[int, list[tuple[float, str, dict]]]:
        """(new cursor, [(time, name, info)]) of the events after `cursor`."""
        with self._lock:
            out = [(t, n, i) for seq, t, n, i in self._events if seq > cursor]
            return self._seq, out

    # -- input
    def update(self, payload: dict) -> None:
        now = self.clock()
        with self._lock:
            self._last_post = now
            self._fold(payload, now)

    def _fold(self, p: dict, now: float) -> None:
        prev = self._s
        provider = p.get("provider") or {}
        mp = p.get("map") or {}
        rnd = p.get("round") or {}
        pl = p.get("player") or {}
        st = pl.get("state") or {}
        ms = pl.get("match_stats") or {}
        pc = p.get("phase_countdowns") or {}

        self.my_id = provider.get("steamid") or self.my_id
        me = bool(pl) and (not pl.get("steamid") or pl.get("steamid") == self.my_id)
        if me and pl.get("team") in ("CT", "T"):
            self.my_team = pl["team"]
        in_match = bool(mp)

        weapon = _active_weapon(pl.get("weapons") or {})
        s = {
            "connected": True, "in_match": in_match, "activity": pl.get("activity"),
            "map": mp.get("name"), "mode": mp.get("mode"), "map_phase": mp.get("phase"),
            "round": _int(mp.get("round"), None), "round_phase": rnd.get("phase"),
            "score_ct": _int((mp.get("team_ct") or {}).get("score"), 0),
            "score_t": _int((mp.get("team_t") or {}).get("score"), 0),
            "win_team": rnd.get("win_team"), "team": self.my_team, "spectating": bool(pl) and not me,
            "player_name": pl.get("name"), "player_team": pl.get("team"),
            "health": _int(st.get("health"), None), "armor": _int(st.get("armor"), None),
            "helmet": bool(st.get("helmet")), "flashed": (_int(st.get("flashed"), 0) or 0) / 255,
            "smoked": (_int(st.get("smoked"), 0) or 0) / 255, "burning": (_int(st.get("burning"), 0) or 0) / 255,
            "money": _int(st.get("money"), None), "round_kills": _int(st.get("round_kills"), 0),
            "round_hs": _int(st.get("round_killhs"), 0), "equip_value": _int(st.get("equip_value"), None),
            "defusekit": bool(st.get("defusekit")),
            "kills": _int(ms.get("kills"), None), "deaths": _int(ms.get("deaths"), None),
            "assists": _int(ms.get("assists"), None), "mvps": _int(ms.get("mvps"), None),
            "weapon": weapon_name(weapon.get("name")), "weapon_type": weapon.get("type"),
            "ammo": _int(weapon.get("ammo_clip"), None), "ammo_max": _int(weapon.get("ammo_clip_max"), None),
            "reserve": _int(weapon.get("ammo_reserve"), None),
            "bomb": rnd.get("bomb"), "countdown_phase": pc.get("phase"),
        }
        s["alive"] = (s["health"] or 0) > 0

        # ---- bomb timer: phase_countdowns when the game sends it, else a local 40 s clock
        ends_in = _float(pc.get("phase_ends_in"))
        planted = s["bomb"] == "planted"
        if planted:
            if pc.get("phase") == "bomb" and ends_in is not None:
                self.bomb_ends, self.bomb_estimated = now + ends_in, False
                self.defuse_ends = None
            elif pc.get("phase") == "defuse" and ends_in is not None:
                self.defuse_ends = now + ends_in
            elif prev.get("bomb") != "planted":
                self.bomb_ends, self.bomb_estimated = now + BOMB_S, False
                self.defuse_ends = None
            if self.bomb_ends is None:                # joined mid-plant / mid-defuse: best guess
                left = BOMB_S if self.defuse_ends is None else max(self.defuse_ends - now, 0.0) + 1.0
                self.bomb_ends, self.bomb_estimated = now + left, True
        else:
            self.bomb_ends = self.defuse_ends = None
        if pc.get("phase") != "defuse":
            self.defuse_ends = None

        # ---- events (own player only for personal ones)
        was_me = not prev.get("spectating", True) if prev else False
        if prev:
            if s["bomb"] != prev.get("bomb"):
                if planted:
                    self._emit("bomb_planted")
                elif s["bomb"] == "defused":
                    self._emit("bomb_defused", mine=self.my_team == "CT")
                elif s["bomb"] == "exploded":
                    self._emit("bomb_exploded", mine=self.my_team == "T")
            if s["round_phase"] != prev.get("round_phase"):
                if s["round_phase"] == "freezetime":
                    self._emit("freezetime", team=self.my_team)
                elif s["round_phase"] == "live":
                    self._emit("round_start", team=self.my_team)
                elif s["round_phase"] == "over" and s["win_team"]:
                    won = s["win_team"] == self.my_team
                    self._emit("round_won" if won else "round_lost", team=s["win_team"])
            if s["map_phase"] == "gameover" and prev.get("map_phase") != "gameover":
                mine = s["score_ct"] if self.my_team == "CT" else s["score_t"]
                theirs = s["score_t"] if self.my_team == "CT" else s["score_ct"]
                self._emit("match_won" if mine > theirs else "match_lost" if mine < theirs else "match_tied")
            if me and was_me and s["round"] == prev.get("round"):
                if (s["round_kills"] or 0) > (prev.get("round_kills") or 0):
                    hs = (s["round_hs"] or 0) > (prev.get("round_hs") or 0)
                    self._emit("headshot" if hs else "kill", count=s["round_kills"])
                if prev.get("alive") and not s["alive"]:
                    self._emit("death")
                elif s["alive"] and (s["health"] or 0) < (prev.get("health") or 0):
                    self._emit("hurt", damage=(prev.get("health") or 0) - s["health"], health=s["health"])
            if me and was_me and s["flashed"] > 0.3 and (prev.get("flashed") or 0) <= 0.3:
                self._emit("flashed", amount=s["flashed"])
        self._s = s

    # -- output
    @property
    def connected(self) -> bool:
        return self.clock() - self._last_post < STALE_S

    @property
    def data(self) -> dict:
        return self.snapshot()

    def snapshot(self) -> dict:
        now = self.clock()
        with self._lock:
            if not self._s or now - self._last_post >= STALE_S:
                return {"connected": False}
            s = dict(self._s)
            bomb_left = max(0.0, self.bomb_ends - now) if self.bomb_ends is not None else None
            defuse_left = max(0.0, self.defuse_ends - now) if self.defuse_ends is not None else None
        s["bomb_left"] = bomb_left
        s["bomb_total"] = BOMB_S
        s["bomb_estimated"] = self.bomb_estimated
        s["defusing"] = defuse_left is not None
        s["defuse_left"] = defuse_left
        if bomb_left is not None:
            need = DEFUSE_KIT_S if s.get("defusekit") or s.get("team") != "CT" else DEFUSE_S
            # for a CT: can *I* still defuse (kit or not); for a T: can *they* still (assume a kit)
            s["defuse_need"] = need
            s["can_defuse"] = (defuse_left is not None and defuse_left < bomb_left) or bomb_left > need
        return s


# =========================================================================== HTTP receiver

class _Handler(BaseHTTPRequestHandler):
    state: Cs2State
    token: str = ""

    def do_POST(self):  # noqa: N802 - http.server API
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 1 << 20)
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            self.send_response(400)
            self.end_headers()
            return
        auth = (payload.get("auth") or {}).get("token", "") if isinstance(payload, dict) else ""
        if not isinstance(payload, dict) or (self.token and auth != self.token):
            self.send_response(403)
            self.end_headers()
            return
        self.state.update(payload)
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt, *args):  # quiet: CS2 posts many times a second
        pass


class Cs2Telemetry:
    """GSI receiver on a background thread. ``data`` / ``events_since`` read the shared state."""

    def __init__(self) -> None:
        self.state = Cs2State()
        self.port = GSI_PORT
        self.token = ""
        self._server: ThreadingHTTPServer | None = None
        self._demo: threading.Thread | None = None
        self.installed: list[Path] = []

    @property
    def data(self) -> dict:
        return self.state.snapshot()

    def events_since(self, cursor: int):
        return self.state.events_since(cursor)

    def configure(self, port: int | None = None, token: str | None = None) -> None:
        self.port = int(port or GSI_PORT)
        self.token = token or ""

    def start(self) -> None:
        if os.environ.get("FREEDOCK_NO_GAMES"):      # tests: no game folders, no ports
            return
        if os.environ.get("FREEDOCK_DEMO"):
            if self._demo is None:
                self._demo = threading.Thread(target=demo_loop, args=(self.state,), name="cs2-demo", daemon=True)
                self._demo.start()
            return
        if self._server is not None:
            return
        if sys.platform == "win32" or cfg_dirs():
            try:
                self.installed = install_cfg(self.port, self.token)
            except Exception:  # noqa: BLE001 - never block the deck on a game folder
                log.exception("cs2: config install failed")
        handler = type("Cs2Handler", (_Handler,), {"state": self.state, "token": self.token})
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        except OSError as exc:
            log.warning("cs2: port %s unavailable (%s); CS2 data disabled", self.port, exc)
            return
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="cs2-gsi", daemon=True).start()
        log.info("cs2: listening for game state on http://127.0.0.1:%s/", self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


telemetry = Cs2Telemetry()


# =========================================================================== demo

def demo_payloads(t: float, steamid: str = "76561190000000001") -> dict:
    """A scripted 60 s round as GSI would send it (FREEDOCK_DEMO=1, previews, tests).

    0-8 freeze time, 8 live, 14 kill, 19 headshot, 24 flashbang, 27 hit to 22 HP,
    30 bomb planted (40 s), 62 defused -> round won, then the loop restarts."""
    t %= 75.0
    phase = "freezetime" if t < 8 else "live" if t < 62 else "over"
    kills = (1 if t >= 14 else 0) + (1 if t >= 19 else 0)
    hp = 100 if t < 27 else 22
    flashed = int(max(0.0, 255 * (1 - (t - 24) / 2.5))) if 24 <= t < 26.5 else 0
    rnd = {"phase": phase}
    pc = {"phase": phase, "phase_ends_in": f"{max(0.0, (8 - t) if t < 8 else 115 - t):.1f}"}
    if 30 <= t < 62:
        rnd["bomb"] = "planted"
        pc = {"phase": "bomb", "phase_ends_in": f"{max(0.0, 70 - t):.1f}"}
        if t >= 56:
            pc = {"phase": "defuse", "phase_ends_in": f"{max(0.0, 61 - t):.1f}"}
    elif t >= 62:
        rnd.update(bomb="defused", win_team="CT")
    return {
        "provider": {"name": "Counter-Strike: Global Offensive", "appid": 730, "steamid": steamid},
        "map": {"mode": "competitive", "name": "de_mirage", "phase": "live", "round": 7,
                "team_ct": {"score": 4 + (1 if t >= 62 else 0)}, "team_t": {"score": 2}},
        "round": rnd,
        "player": {"steamid": steamid, "name": "you", "team": "CT", "activity": "playing",
                   "state": {"health": hp, "armor": 100 if t < 27 else 64, "helmet": True, "flashed": flashed,
                             "smoked": 0, "burning": 0, "money": 4350 if t < 8 else 1250,
                             "round_kills": kills, "round_killhs": 1 if t >= 19 else 0, "equip_value": 5200,
                             "defusekit": True},
                   "match_stats": {"kills": 9 + kills, "assists": 2, "deaths": 4, "mvps": 2, "score": 26},
                   "weapons": {"weapon_0": {"name": "weapon_knife", "type": "Knife", "state": "holstered"},
                               "weapon_1": {"name": "weapon_m4a1_silencer", "type": "Rifle", "state": "active",
                                            "ammo_clip": max(3, 20 - int(t / 3) % 20), "ammo_clip_max": 20,
                                            "ammo_reserve": 80}}},
        "phase_countdowns": pc,
    }


def demo_loop(state: Cs2State, stop: threading.Event | None = None) -> None:
    t0 = time.monotonic()
    while stop is None or not stop.is_set():
        state.update(demo_payloads(time.monotonic() - t0))
        time.sleep(0.1)
