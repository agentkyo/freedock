"""Notifications from the outside world: AI assistants finishing a task, scripts, webhooks.

Three ways in, all ending in :meth:`Engine.notify_event`:

* ``freedock notify <source> <event> [--message ...] [--progress 0.4]`` - what Claude Code hooks,
  the Codex ``notify`` program and the OpenCode plugin call. It hands the event to the running
  FreeDock over its single-instance socket and exits at once.
* ``http://127.0.0.1:47810/notify?source=claude&event=done&message=...`` (GET or POST, JSON body
  also accepted) for anything that can make an HTTP request: shell scripts, Home Assistant, CI.
* ``GET /state`` returns the current state of every source as JSON; ``/quit`` closes FreeDock.
* ``/sound?id=<sound>&target=mic|local|both`` plays a soundboard sound, ``/sound?stop=1`` stops all.
* ``GET /info``, ``/page?id=...`` and ``/context?game=...&vehicle=...`` are for companion apps such as
  MyWheel - see :mod:`freedock.integrations.companion`.

Only the loopback interface is bound, so nothing is reachable from the network, and requests that a
browser marks as coming from a web page (``Origin`` / ``Sec-Fetch-Site``) are refused, so a website
cannot poke the deck through the user's browser.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger(__name__)

EVENTS = ("working", "progress", "done", "question", "error", "idle")
ATTENTION = {"done", "question", "error"}
DEFAULT_PORT = 47810


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    color: str
    icon: str


SOURCES: dict[str, Source] = {
    "claude": Source("claude", "Claude", "#d97757", "sparkles"),
    "codex": Source("codex", "Codex", "#10a37f", "square-terminal"),
    "opencode": Source("opencode", "OpenCode", "#f5a623", "code-xml"),
    "custom": Source("custom", "Script", "#5b8cff", "bell"),
}
EVENT_COLOR = {"done": "#22c55e", "question": "#f59e0b", "error": "#ef4444"}


def source(sid: str) -> Source:
    return SOURCES.get(sid) or Source(sid, sid.title()[:12], "#5b8cff", "bell")


@dataclass
class SourceState:
    status: str = "idle"           # idle | working | done | question | error
    message: str = ""
    progress: float | None = None
    t: float = 0.0                 # time.time() of the last event
    unseen: bool = False           # an attention event nobody acknowledged yet
    history: list = field(default_factory=list)


class NotifyCenter:
    """State of every notification source. Mutated on the UI thread only."""

    WORKING_STALE_S = 30 * 60      # a "working" with no news for this long is probably dead
    DONE_FADE_S = 15 * 60          # acknowledged results go back to idle after this

    def __init__(self):
        self.states: dict[str, SourceState] = {}

    def get(self, sid: str) -> SourceState:
        st = self.states.setdefault(sid, SourceState())
        now = time.time()
        if st.status == "working" and now - st.t > self.WORKING_STALE_S:
            st.status, st.progress = "idle", None
        elif st.status in ATTENTION and not st.unseen and now - st.t > self.DONE_FADE_S:
            st.status = "idle"
        return st

    def apply(self, sid: str, event: str, message: str = "", progress: float | None = None) -> bool:
        """Record an event. Returns True when it deserves the user's attention."""
        event = event if event in EVENTS else "done"
        st = self.states.setdefault(sid, SourceState())
        if event == "progress":
            event = "working"
        st.status = event
        st.t = time.time()
        if message:
            st.message = message[:200]
        elif event == "working":
            st.message = ""
        st.progress = None if progress is None else max(0.0, min(1.0, float(progress)))
        if event in ATTENTION:
            st.unseen = True
            st.progress = None
        elif event in ("working", "idle"):
            st.unseen = False
        st.history = (st.history + [(st.t, event, st.message)])[-20:]
        return event in ATTENTION

    def ack(self, sid: str | None = None) -> None:
        for k, st in self.states.items():
            if sid in (None, k):
                st.unseen = False

    def pending(self) -> list[str]:
        return [k for k, st in self.states.items() if st.unseen]

    def snapshot(self) -> dict:
        return {k: {"status": s.status, "message": s.message, "progress": s.progress, "time": s.t,
                    "unseen": s.unseen} for k, s in self.states.items()}


def parse_message(text: str) -> dict | None:
    """The JSON a ``freedock notify`` call sends over the instance socket."""
    try:
        d = json.loads(text)
    except ValueError:
        return None
    if not isinstance(d, dict) or not d.get("source"):
        return None
    return d


# --------------------------------------------------------------------------- HTTP
class _Handler(BaseHTTPRequestHandler):
    engine: "Engine"

    def log_message(self, fmt, *args):   # keep the console quiet
        log.debug("http: " + fmt, *args)

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _params(self) -> dict:
        url = urlparse(self.path)
        params = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length:
            raw = self.rfile.read(min(length, 65536))
            try:
                body = json.loads(raw)
                if isinstance(body, dict):
                    params.update({k: v for k, v in body.items() if isinstance(v, (str, int, float))})
            except ValueError:
                params.update({k: v[-1] for k, v in parse_qs(raw.decode(errors="ignore")).items()})
        return params

    def _from_browser(self) -> bool:
        """Web pages can fire requests at 127.0.0.1 too; real browsers label them, scripts don't."""
        site = self.headers.get("Sec-Fetch-Site")
        return bool(self.headers.get("Origin")) or (site is not None and site not in ("none",))

    def _handle(self) -> None:
        if self._from_browser():
            self._reply(403, {"error": "requests from web pages are not accepted"})
            return
        url = urlparse(self.path)
        if url.path == "/state":
            self._reply(200, self.engine.notify_center.snapshot())
            return
        if url.path == "/quit":
            quit_fn = getattr(self.engine, "on_quit", None)
            if quit_fn is None:
                self._reply(404, {"error": "quit not available"})
                return
            self._reply(200, {"ok": True})
            self.engine.call_in_ui(quit_fn)
            return
        if url.path == "/sound":
            # /sound?id=airhorn&target=mic|local|both   /sound?stop=1
            p = self._params()
            play = getattr(self.engine, "sound_play", None)
            if play is None:
                self._reply(404, {"error": "soundboard not available"})
                return
            if p.get("stop"):
                self.engine.call_in_ui(lambda: self.engine.soundboard_op("stop"))
            else:
                sid, target = str(p.get("id") or ""), str(p.get("target") or "mic")
                self.engine.call_in_ui(lambda: play(sid, target))
            self._reply(200, {"ok": True})
            return
        companion = getattr(self, "companion", None)
        if companion is not None and url.path == "/info":
            # built on the UI thread: the profile may be mid-edit and the context mid-update
            box, done = {}, threading.Event()

            def build():
                try:
                    box["info"] = companion.info()
                finally:
                    done.set()
            self.engine.call_in_ui(build)
            if done.wait(2.0) and "info" in box:
                self._reply(200, box["info"])
            else:
                self._reply(503, {"error": "busy"})
            return
        if companion is not None and url.path == "/page":
            pid = str(self._params().get("id") or "")
            if not companion.has_page(pid):
                self._reply(404, {"error": f"no page {pid!r}"})
                return
            self.engine.call_in_ui(lambda: companion.show_page(pid))
            self._reply(200, {"ok": True})
            return
        if companion is not None and url.path == "/context":
            p = self._params()
            game, vehicle = str(p.get("game") or "")[:24], str(p.get("vehicle") or "")[:24]
            if not game or not vehicle:
                self._reply(400, {"error": "game and vehicle are required"})
                return
            name, source = str(p.get("name") or "")[:64], str(p.get("source") or "")[:24]
            self.engine.call_in_ui(lambda: companion.set_context(game, vehicle, name, source))
            self._reply(200, {"ok": True})
            return
        if url.path != "/notify":
            self._reply(404, {"error": "use /notify, /sound, /state, /info, /page or /context"})
            return
        p = self._params()
        src = str(p.get("source") or p.get("tool") or "custom")[:24]
        event = str(p.get("event") or "done")
        progress = p.get("progress")
        try:
            progress = None if progress in (None, "") else float(progress)
        except (TypeError, ValueError):
            progress = None
        msg = str(p.get("message") or "")
        self.engine.call_in_ui(lambda: self.engine.notify_event(src, event, msg, progress))
        self._reply(200, {"ok": True})

    do_GET = _handle
    do_POST = _handle


class HttpBridge:
    def __init__(self, engine: "Engine", port: int = DEFAULT_PORT):
        self.engine, self.port = engine, port
        self._server: ThreadingHTTPServer | None = None

    def start(self) -> None:
        if self._server or not self.port:
            return
        from .integrations.companion import Companion
        companion = Companion(self.engine)
        self.engine.companion = companion        # auto-switch asks it which vehicle page to open
        handler = type("Handler", (_Handler,), {"engine": self.engine, "companion": companion})
        try:
            self._server = ThreadingHTTPServer(("127.0.0.1", int(self.port)), handler)
        except OSError as exc:
            log.warning("notification HTTP port %s unavailable: %s", self.port, exc)
            return
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="notify-http", daemon=True).start()
        log.info("notifications on http://127.0.0.1:%s/notify", self.port)

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
