"""AI coding assistants (Claude Code, OpenAI Codex, OpenCode): usage, live status and notify plumbing.

Two halves:

* ``AiUsage`` - a background reader that tails each tool's local transcripts/databases (read-only) and
  publishes a ``data`` dict snapshot: tokens in the current 5-hour window and today, plan-limit
  percentages where the tool records them, reset times, last activity, model and a passive status.
* Pure helpers that install/remove FreeDock as a notification hook in each tool's config
  (Claude Code ``settings.json`` hooks, Codex ``config.toml`` ``notify`` chaining, an OpenCode plugin),
  map their payloads to FreeDock events, and the default launch commands.

Nothing here ever writes into ~/.claude, ~/.codex or OpenCode's directories: SQLite databases are copied
to a temp dir before being opened, and the *_patch functions return new text/dicts for the caller to save.
Event CLI contract: ``freedock notify <tool> <event> [--message TEXT] [--progress 0..1]``.
"""
from __future__ import annotations

import copy
import glob
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

TOOLS = ("claude", "codex", "opencode", "custom")
EVENTS = ("working", "done", "question", "error", "progress", "idle")
BLOCK = 5 * 3600                     # Claude / Codex primary window length
LOOKBACK = 2 * 86400                 # transcripts older than this are never read
STATUS_OF_EVENT = {"working": "working", "progress": "working", "done": "done", "question": "needs_input",
                   "error": "error", "idle": "idle"}


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) if sys.platform == "win32" else Path.home()


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or _home() / ".claude")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or _home() / ".codex")


def opencode_data_dir() -> Path:
    # OpenCode uses xdg-basedir, i.e. ~/.local/share even on Windows.
    return Path(os.environ.get("XDG_DATA_HOME") or _home() / ".local" / "share") / "opencode"


def opencode_config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or _home() / ".config") / "opencode"


def opencode_plugin_path() -> Path:
    """Global plugin file (current docs: ``~/.config/opencode/plugins/``; very old builds used ``plugin/``)."""
    return opencode_config_dir() / "plugins" / "freedock.js"


def claude_limits_cache() -> Path:
    """Where ``claude_statusline_output`` stores the last ``rate_limits`` Claude Code handed the status line."""
    from ..profile import config_dir
    return config_dir() / "ai_claude_limits.json"


def _ts(value: Any) -> float | None:
    """ISO-8601 string / epoch seconds / epoch ms -> epoch seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value / 1000.0 if value > 1e12 else float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _midnight(now: float) -> float:
    return datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _empty(installed: bool = False) -> dict:
    return {"installed": installed, "tokens_5h": 0, "tokens_today": 0, "percent_5h": None, "percent_week": None,
            "resets_at": None, "resets_week_at": None, "last_activity": None, "model": None,
            "status": None, "status_since": None}


# ----------------------------------------------------------------------------------------------- readers

class _Tail:
    """Incremental line reader: remembers byte offsets so a growing JSONL file is only read once."""

    CHUNK = 4 << 20

    def __init__(self) -> None:
        self.pos: dict[str, tuple[int, int, int]] = {}      # path -> (offset, mtime_ns, size)

    def lines(self, path: str, st: os.stat_result) -> tuple[bool, Iterator[bytes]]:
        """(was_reset, new complete lines). Partial trailing lines are left for the next call."""
        prev = self.pos.get(path)
        if prev and prev[1] == st.st_mtime_ns and prev[2] == st.st_size:
            return False, iter(())
        off = prev[0] if prev and st.st_size >= prev[0] else 0
        reset = prev is not None and off == 0 and prev[0] > 0
        self.pos[path] = (off, st.st_mtime_ns, st.st_size)
        return reset, self._read(path, off, st)

    def _read(self, path: str, off: int, st: os.stat_result) -> Iterator[bytes]:
        try:
            with open(path, "rb") as f:
                f.seek(off)
                rest = b""                         # bytes after `off` not yet terminated by a newline
                while chunk := f.read(self.CHUNK):
                    buf = rest + chunk
                    cut = buf.rfind(b"\n")
                    if cut < 0:
                        rest = buf
                        continue
                    rest = buf[cut + 1:]
                    off += cut + 1
                    self.pos[path] = (off, st.st_mtime_ns, st.st_size)
                    yield from buf[:cut].splitlines()
        except OSError as exc:
            log.debug("tail %s: %s", path, exc)


def _recent_files(patterns: Iterable[str], since: float) -> list[tuple[str, os.stat_result]]:
    out = []
    for pat in patterns:
        for p in glob.iglob(pat, recursive=True):
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime >= since:
                out.append((p, st))
    return out


def _copy_db(src: Path, dst_dir: Path) -> Path:
    """Copy a (possibly WAL-mode, possibly locked) SQLite DB with its -wal/-shm into dst_dir."""
    dst = dst_dir / src.name
    for suffix in ("", "-wal", "-shm"):
        s = Path(str(src) + suffix)
        d = Path(str(dst) + suffix)
        if s.exists():
            shutil.copyfile(s, d)
        elif d.exists():
            d.unlink()
    return dst


def claude_blocks(entries: Iterable[tuple[float, int]], now: float) -> dict | None:
    """Active 5-hour block, ccusage-style: a block starts at the first message after the previous block
    expired (floored to the hour, UTC) and lasts 5 h; a 5 h gap also starts a new block.
    ``entries`` are (epoch, tokens). Returns {"start", "end", "tokens", "last"} or None when idle."""
    start = last = None
    tokens = 0
    for ts, tok in sorted(entries):
        if start is None or ts - start >= BLOCK or ts - last >= BLOCK:
            start, tokens = ts - ts % 3600, 0
        tokens += tok
        last = ts
    if start is None or now >= start + BLOCK or now - last >= BLOCK:
        return None
    return {"start": start, "end": start + BLOCK, "tokens": tokens, "last": last}


def claude_usage_tokens(usage: dict) -> int:
    return sum(int(usage.get(k) or 0) for k in
               ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


class _ClaudeReader:
    def __init__(self) -> None:
        self.tail = _Tail()
        self.records: dict[str, tuple[float, int, str]] = {}   # msg.id:requestId -> (ts, tokens, model)
        self.limits_mtime = 0.0
        self.limits: dict = {}
        self.prev_status: str | None = None
        self.status_since: float | None = None

    def installed(self, home: Path) -> bool:
        return home.is_dir() or bool(shutil.which("claude")) or (_home() / ".local" / "bin" / "claude.exe").exists()

    def feed(self, line: bytes) -> None:
        if b'"usage"' not in line or b'"assistant"' not in line:
            return
        try:
            o = json.loads(line)
        except ValueError:
            return
        msg = o.get("message") if isinstance(o, dict) else None
        if o.get("type") != "assistant" or not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
            return
        ts = _ts(o.get("timestamp"))
        if ts is None:
            return
        key = f"{msg.get('id')}:{o.get('requestId')}"
        if msg.get("id") is None and o.get("requestId") is None:
            key = str(o.get("uuid"))
        # Streaming writes one entry per content block with the same id; the last one has final usage.
        self.records[key] = (ts, claude_usage_tokens(msg["usage"]), str(msg.get("model") or ""))

    def read(self, now: float) -> dict:
        home = claude_home()
        out = _empty(self.installed(home))
        if not home.is_dir():
            return out
        for path, st in _recent_files([str(home / "projects" / "**" / "*.jsonl")], now - LOOKBACK):
            _, lines = self.tail.lines(path, st)
            for line in lines:
                self.feed(line)
        cutoff = now - LOOKBACK
        self.records = {k: v for k, v in self.records.items() if v[0] >= cutoff}
        recs = [v for v in self.records.values() if v[2] != "<synthetic>"]
        today = _midnight(now)
        out["tokens_today"] = sum(t for ts, t, _ in recs if ts >= today)
        blk = claude_blocks(((ts, t) for ts, t, _ in recs), now)
        if blk:
            out["tokens_5h"], out["resets_at"] = blk["tokens"], blk["end"]
        if recs:
            last = max(recs)
            out["last_activity"], out["model"] = last[0], last[2] or None
        self._apply_limits(out, now)
        self._status(home, out, now)
        return out

    def _apply_limits(self, out: dict, now: float) -> None:
        """Real plan-limit % only exists in the status-line stdin; FreeDock's status line caches it."""
        path = claude_limits_cache()
        try:
            m = path.stat().st_mtime
            if m != self.limits_mtime:
                self.limits_mtime, self.limits = m, json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        for key, pct, rst in (("five_hour", "percent_5h", "resets_at"), ("seven_day", "percent_week", "resets_week_at")):
            w = self.limits.get(key)
            if not isinstance(w, dict):
                continue
            reset = _ts(w.get("resets_at"))
            if reset and reset <= now:            # window rolled over since the last capture
                out[pct] = 0.0
                continue
            if w.get("used_percentage") is not None:
                out[pct] = float(w["used_percentage"])
            if reset:
                out[rst] = reset

    def _status(self, home: Path, out: dict, now: float) -> None:
        """Passive status from ~/.claude/sessions/<pid>.json ("status": "busy"/...) of live processes."""
        states = []
        for p in glob.glob(str(home / "sessions" / "*.json")):
            try:
                d = json.loads(Path(p).read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and _pid_alive(d.get("pid")):
                states.append(str(d.get("status") or "idle").lower())
        if not states:
            status = None
        elif any(re.search("wait|input|permission|block|ask", s) for s in states):
            status = "needs_input"
        elif any(s in ("busy", "running", "working", "thinking") for s in states):
            status = "working"
        else:
            status = "done" if self.prev_status in ("working", "done") else "idle"
        if status != self.prev_status:
            self.prev_status, self.status_since = status, now
        out["status"], out["status_since"] = status, self.status_since if status else None


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
        k32.CloseHandle(ctypes.c_void_p(h))
        return bool(ok) and code.value == 259                # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


CODEX_STATUS = {"task_started": "working", "turn_started": "working", "user_message": "working",
                "task_complete": "done", "turn_complete": "done", "turn_aborted": "idle",
                "exec_approval_request": "needs_input", "apply_patch_approval_request": "needs_input",
                "request_user_input": "needs_input", "elicitation_request": "needs_input",
                "error": "error", "stream_error": "error"}


class _CodexReader:
    """Rollout JSONL files (``sessions/YYYY/MM/DD/rollout-*.jsonl[.zst]``): ``event_msg`` ``token_count``
    events carry cumulative ``info.total_token_usage`` and ``rate_limits.primary/secondary``."""

    def __init__(self) -> None:
        self.tail = _Tail()
        self.events: dict[str, list[tuple[float, int]]] = {}   # path -> (ts, token delta)
        self.totals: dict[str, int] = {}
        self.zst_mtime: dict[str, int] = {}
        self.limits: tuple[float, dict] | None = None
        self.model: tuple[float, str] | None = None
        self.state: tuple[float, str] | None = None
        self.last: float | None = None

    @staticmethod
    def cli_path() -> str | None:
        base = Path(os.environ.get("LOCALAPPDATA") or _home() / "AppData" / "Local") / "OpenAI" / "Codex" / "bin"
        found = sorted(base.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True) if base.is_dir() else []
        return str(found[0]) if found else shutil.which("codex")

    def feed(self, path: str, line: bytes) -> None:
        try:
            o = json.loads(line)
        except ValueError:
            return
        if not isinstance(o, dict):
            return
        ts = _ts(o.get("timestamp")) or time.time()
        payload = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        kind = o.get("type")
        if kind == "turn_context" and payload.get("model"):
            if not self.model or ts >= self.model[0]:
                self.model = (ts, str(payload["model"]))
            return
        if kind != "event_msg":
            return
        ptype = payload.get("type")
        self.last = max(self.last or 0, ts)
        if ptype in CODEX_STATUS and (not self.state or ts >= self.state[0]):
            self.state = (ts, CODEX_STATUS[ptype])
        if ptype != "token_count":
            return
        info = payload.get("info") or {}
        total = (info.get("total_token_usage") or {}).get("total_tokens")
        if isinstance(total, int):
            delta = total - self.totals.get(path, 0)
            if delta > 0:
                self.events.setdefault(path, []).append((ts, delta))
            self.totals[path] = max(total, self.totals.get(path, 0))
        rl = payload.get("rate_limits")
        if isinstance(rl, dict) and (not self.limits or ts >= self.limits[0]):
            self.limits = (ts, rl)

    def _forget(self, path: str) -> None:
        self.events.pop(path, None)
        self.totals.pop(path, None)

    def read(self, now: float) -> dict:
        home = codex_home()
        out = _empty(home.is_dir() or bool(self.cli_path()))
        roots = [str(home / sub / "**" / pat) for sub in ("sessions", "archived_sessions")
                 for pat in ("rollout-*.jsonl", "rollout-*.jsonl.zst")]
        for path, st in _recent_files(roots, now - LOOKBACK):
            if path.endswith(".zst"):
                if self.zst_mtime.get(path) == st.st_mtime_ns:
                    continue
                self.zst_mtime[path] = st.st_mtime_ns
                self._forget(path)
                try:
                    from compression import zstd           # Python 3.14+
                    with zstd.open(path, "rb") as f:
                        for line in f:
                            self.feed(path, line)
                except (ImportError, OSError) as exc:
                    log.debug("codex zst %s: %s", path, exc)
                continue
            reset, lines = self.tail.lines(path, st)
            if reset:
                self._forget(path)
            for line in lines:
                self.feed(path, line)
        evs = [e for lst in self.events.values() for e in lst if e[0] >= now - LOOKBACK]
        out["tokens_today"] = sum(t for ts, t in evs if ts >= _midnight(now))
        window_start = now - BLOCK
        if self.limits:
            for name in ("primary", "secondary"):
                w = self.limits[1].get(name)
                if not isinstance(w, dict) or w.get("used_percent") is None:
                    continue
                mins = w.get("window_minutes") or (300 if name == "primary" else 10080)
                reset = _ts(w.get("resets_at"))
                if reset is None and w.get("resets_in_seconds") is not None:       # older rollouts
                    reset = self.limits[0] + float(w["resets_in_seconds"])
                pct = float(w["used_percent"])
                if reset is not None and reset <= now:
                    pct, reset = 0.0, None
                if mins <= 360:
                    out["percent_5h"], out["resets_at"] = pct, reset
                    if reset:
                        window_start = reset - mins * 60
                else:
                    out["percent_week"], out["resets_week_at"] = pct, reset
        out["tokens_5h"] = sum(t for ts, t in evs if ts >= window_start)
        out["last_activity"] = self.last
        out["model"] = self.model[1] if self.model else None
        if self.state:
            out["status"], out["status_since"] = self.state[1], self.state[0]
        return out


def opencode_message_tokens(d: dict) -> int:
    """Total tokens of an OpenCode assistant message (``tokens: {input, output, reasoning, cache:{read,write}}``)."""
    tok = d.get("tokens") if isinstance(d.get("tokens"), dict) else d.get("usage")
    if not isinstance(tok, dict):
        return 0
    cache = tok.get("cache") if isinstance(tok.get("cache"), dict) else {}
    vals = [tok.get("input"), tok.get("output"), tok.get("reasoning"), cache.get("read"), cache.get("write")]
    return sum(int(v) for v in vals if isinstance(v, (int, float)))


class _OpenCodeReader:
    """OpenCode >= 1.x keeps everything in ``opencode.db`` (SQLite); older builds wrote
    ``storage/message/<session>/<msg>.json``. Both store assistant messages with ``tokens``/``cost``."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="freedock-opencode-"))
        self.db_sig: tuple | None = None
        self.cached = _empty()
        self.files: dict[str, tuple[int, tuple[float, int, float, str]]] = {}

    def _db(self) -> Path | None:
        if os.environ.get("OPENCODE_DB") and os.environ["OPENCODE_DB"] != ":memory:":
            p = Path(os.environ["OPENCODE_DB"])
            return p if p.is_absolute() else opencode_data_dir() / p
        data = opencode_data_dir()
        cands = [data / "opencode.db"] + sorted(data.glob("opencode-*.db"))
        cands = [c for c in cands if c.exists()]
        return max(cands, key=lambda c: c.stat().st_mtime) if cands else None

    def read(self, now: float) -> dict:
        data = opencode_data_dir()
        installed = data.is_dir() or opencode_config_dir().is_dir() or bool(shutil.which("opencode"))
        msgs: list[tuple[float, int, float, str]] = []    # (ts, tokens, cost, model)
        status = None
        db = self._db()
        if db:
            sig = tuple((Path(str(db) + s).stat().st_mtime_ns if Path(str(db) + s).exists() else 0) for s in ("", "-wal"))
            if sig == self.db_sig:
                return dict(self.cached, installed=installed)
            self.db_sig = sig
            try:
                msgs, status = self._from_db(_copy_db(db, self.tmp), now)
            except (OSError, sqlite3.Error) as exc:
                log.info("opencode db: %s", exc)
        msgs += self._from_json(data / "storage" / "message", now)
        out = _empty(installed)
        today = _midnight(now)
        out["tokens_today"] = sum(t for ts, t, _, _ in msgs if ts >= today)
        out["tokens_5h"] = sum(t for ts, t, _, _ in msgs if ts >= now - BLOCK)
        out["cost_today"] = round(sum(c for ts, _, c, _ in msgs if ts >= today), 4)
        if msgs:
            last = max(msgs)
            out["last_activity"], out["model"] = last[0], last[3] or None
        if status:
            out["status"], out["status_since"] = status
        self.cached = out
        return out

    @staticmethod
    def _row(d: Any, ts: Any) -> tuple[float, int, float, str] | None:
        if not isinstance(d, dict) or d.get("role", "assistant") != "assistant":
            return None
        t = _ts(ts) or _ts((d.get("time") or {}).get("created") if isinstance(d.get("time"), dict) else None)
        if t is None:
            return None
        model = d.get("modelID") or (d["model"].get("id") if isinstance(d.get("model"), dict) else d.get("model"))
        return t, opencode_message_tokens(d), float(d.get("cost") or 0), str(model or "")

    def _from_db(self, path: Path, now: float):
        since_ms = int((min(_midnight(now), now - BLOCK) - 60) * 1000)
        con = sqlite3.connect(path)
        try:
            tables = {r[0]: r[1] for r in con.execute("select name, sql from sqlite_master where type='table'")}
            msgs = []
            for tbl in ("session_message", "message"):
                if tbl not in tables:
                    continue
                cols = tables[tbl] or ""
                where = "where time_created >= ?" if "time_created" in cols else ""
                q = f'select {"time_created" if "time_created" in cols else "null"}, data from "{tbl}" {where}'
                for ts, raw in con.execute(q, (since_ms,) if where else ()):
                    try:
                        row = self._row(json.loads(raw), ts)
                    except (TypeError, ValueError):
                        continue
                    if row and row[1]:
                        msgs.append(row)
            status = None
            if "session_v2" in tables and "time_busy" in (tables["session_v2"] or ""):
                r = con.execute("select max(time_busy), max(coalesce(time_idle,0)) from session_v2 "
                                "where parent_id is null").fetchone()
                busy, idle = _ts(r[0]) if r and r[0] else None, _ts(r[1]) if r and r[1] else None
                if busy and (idle is None or busy > idle):
                    status = ("working", busy)
                elif idle:
                    status = ("done" if now - idle < 600 else "idle", idle)
            return msgs, status
        finally:
            con.close()

    def _from_json(self, root: Path, now: float) -> list:
        if not root.is_dir():
            return []
        out = []
        for path, st in _recent_files([str(root / "*" / "*.json")], min(_midnight(now), now - BLOCK)):
            hit = self.files.get(path)
            if not hit or hit[0] != st.st_mtime_ns:
                try:
                    row = self._row(json.loads(Path(path).read_text("utf-8")), None)
                except (OSError, ValueError):
                    row = None
                hit = (st.st_mtime_ns, row)
                self.files[path] = hit
            if hit[1]:
                out.append(hit[1])
        return out


class AiUsage:
    """Polls the AI tools' local data on a background thread; ``data`` is a plain dict snapshot."""

    def __init__(self, interval: float = 3.0):
        self.data: dict = {"claude": _empty(), "codex": _empty(), "opencode": _empty()}
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.users = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        demo = bool(os.environ.get("FREEDOCK_DEMO"))
        self._thread = threading.Thread(target=self._demo if demo else self._run,
                                        name="ai-usage-demo" if demo else "ai-usage", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def poll(self) -> dict:
        """One synchronous refresh (also used by the thread)."""
        now = time.time()
        if not hasattr(self, "_readers"):
            self._readers = {"claude": _ClaudeReader(), "codex": _CodexReader(), "opencode": _OpenCodeReader()}
        out = dict(self.data)
        for name, reader in self._readers.items():
            try:
                out[name] = reader.read(now)
            except Exception as exc:                       # one broken tool must not kill the others
                log.warning("ai usage %s: %s", name, exc)
                out[name] = dict(out.get(name) or _empty(), error=str(exc))
        out["time"] = now
        self.data = out
        return out

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self._interval)

    def _demo(self) -> None:
        """Fake but plausible numbers (FREEDOCK_DEMO=1)."""
        t0 = time.time()
        cycle = ("working", "working", "needs_input", "working", "done", "idle")
        while not self._stop.is_set():
            now = time.time()
            t = now - t0
            st = cycle[int(t // 8) % len(cycle)]
            blk_end = now - now % BLOCK + BLOCK
            self.data = {
                "claude": dict(_empty(True), tokens_5h=int(1_840_000 + t * 2100), tokens_today=int(6_120_000 + t * 2100),
                               percent_5h=min(100.0, 37.0 + t / 30), percent_week=22.0, resets_at=blk_end,
                               resets_week_at=now + 3 * 86400, last_activity=now - 4, model="claude-opus-4-1",
                               status=st, status_since=t0 + (t // 8) * 8),
                "codex": dict(_empty(True), tokens_5h=412_000, tokens_today=1_300_000, percent_5h=18.0,
                              percent_week=41.0, resets_at=now + 7200, resets_week_at=now + 4 * 86400,
                              last_activity=now - 900, model="gpt-5-codex", status="done", status_since=now - 900),
                "opencode": dict(_empty(False)),
                "time": now,
            }
            self._stop.wait(1.0)


usage = AiUsage()


# ------------------------------------------------------------------------------------ status reducer (pure)

def apply_event(state: dict | None, event: str, message: str | None = None, progress: float | None = None,
                now: float | None = None) -> dict:
    """Fold a ``freedock notify`` event into one tool's status dict.
    Returns {"status", "message", "progress", "since", "alert"}; ``alert`` is True when the user should be
    told (task finished, question waiting, error) and the status actually changed."""
    if event not in EVENTS:
        raise ValueError(f"unknown event {event!r}")
    now = time.time() if now is None else now
    prev = state or {"status": "idle", "message": None, "progress": None, "since": now}
    status = STATUS_OF_EVENT[event]
    changed = status != prev.get("status")
    out = {"status": status, "message": message if message is not None else (None if changed else prev.get("message")),
           "progress": None, "since": now if changed else prev.get("since", now),
           "alert": changed and event in ("done", "question", "error")}
    if event == "progress":
        out["progress"] = None if progress is None else max(0.0, min(1.0, float(progress)))
    elif status == "working" and not changed:
        out["progress"] = prev.get("progress")
    return out


# --------------------------------------------------------------------------------------- Claude Code hooks

CLAUDE_HOOKS = [   # (hook event, matcher or None, FreeDock event)
    ("UserPromptSubmit", None, "working"),
    ("Stop", None, "done"),
    ("StopFailure", None, "error"),
    # permission_prompt: approval waiting ~6 s; elicitation_dialog: MCP form; agent_needs_input: background
    # session waiting. idle_prompt (60 s after a reply) is deliberately skipped - "done" already covers it.
    ("Notification", "permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input", "question"),
    ("SessionEnd", None, "idle"),
]


def _is_ours(hook: Any, tool: str = "claude") -> bool:
    if not isinstance(hook, dict):
        return False
    args = hook.get("args")
    if isinstance(args, list) and len(args) >= 3 and args[-3] == "notify" and args[-2] == tool:
        return True
    cmd = str(hook.get("command") or "")
    return "freedock" in cmd.lower() and f"notify {tool} " in cmd


def claude_hooks_remove(settings: dict) -> dict:
    """Copy of ``settings`` without FreeDock's hooks; everything else (and other hooks) untouched."""
    new = copy.deepcopy(settings)
    hooks = new.get("hooks")
    if not isinstance(hooks, dict):
        return new
    for ev in list(hooks):
        groups = hooks[ev]
        if not isinstance(groups, list):
            continue
        kept = []
        for g in groups:
            if isinstance(g, dict) and isinstance(g.get("hooks"), list) and any(_is_ours(h) for h in g["hooks"]):
                rest = [h for h in g["hooks"] if not _is_ours(h)]
                if rest:
                    kept.append(dict(g, hooks=rest))
            else:
                kept.append(g)
        if kept:
            hooks[ev] = kept
        else:
            del hooks[ev]
    if not hooks:
        del new["hooks"]
    return new


def claude_hooks_patch(settings: dict, command: list[str], subagents: bool = False) -> dict:
    """Copy of a Claude Code ``settings.json`` dict with FreeDock notify hooks (idempotent).
    Hooks use exec form (``command`` + ``args``, no shell) and ``async: true`` so they never block Claude."""
    if not command:
        raise ValueError("empty command")
    new = claude_hooks_remove(settings)
    hooks = new.setdefault("hooks", {})
    table = CLAUDE_HOOKS + ([("SubagentStop", None, "working")] if subagents else [])
    for ev, matcher, our in table:
        group: dict = {"hooks": [{"type": "command", "command": command[0],
                                  "args": [*command[1:], "notify", "claude", our],
                                  "async": True, "timeout": 10}]}
        if matcher:
            group = {"matcher": matcher, **group}
        hooks.setdefault(ev, []).append(group)
    return new


def short_message(text: str | None, limit: int = 90) -> str | None:
    """First sentence of an assistant reply, without markdown - fits a toast or a strip zone."""
    if not text:
        return None
    import re
    line = next((ln.strip() for ln in str(text).splitlines() if ln.strip()), "")
    line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)        # [text](url) -> text
    line = re.sub(r"[*_`#>]+", "", line)
    line = re.sub(r"^\s*(-|•|\d+[.)])\s+", "", line).strip()
    m = re.compile(r"[.!?](\s|$)").search(line, 20)           # first sentence, not a tiny "Ok."
    if m:
        line = line[:m.end()].strip()
    if len(line) > limit:
        line = line[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return line or None


def claude_event_from_hook(payload: str | dict) -> tuple[str, str | None] | None:
    """Map a Claude Code hook stdin payload to (event, message) - for a notify that reads stdin."""
    try:
        p = json.loads(payload) if isinstance(payload, str) else payload
    except ValueError:
        return None
    if not isinstance(p, dict):
        return None
    name = p.get("hook_event_name")
    if name == "Notification":
        kind = p.get("notification_type")
        if kind in ("idle_prompt", "auth_success", "agent_completed", "elicitation_complete", "elicitation_response"):
            return ("done", p.get("message")) if kind in ("idle_prompt", "agent_completed") else None
        return "question", p.get("message")
    table = {"UserPromptSubmit": "working", "Stop": "done", "StopFailure": "error", "SubagentStop": "working",
             "SessionEnd": "idle", "PermissionRequest": "question"}
    if name not in table:
        return None
    msg = p.get("last_assistant_message") if name == "Stop" else p.get("error") if name == "StopFailure" else None
    return table[name], short_message(msg)


def _shell_quote(arg: str) -> str:
    arg = arg.replace("\\", "/") if sys.platform == "win32" and re.match(r"^[A-Za-z]:\\", arg) else arg
    return arg if re.fullmatch(r"[\w@%+=:,./-]+", arg) else '"' + arg.replace('"', '\\"') + '"'


def claude_statusline_patch(settings: dict, command: list[str]) -> tuple[dict, dict | None]:
    """Point Claude Code's status line at ``<command> statusline`` so FreeDock receives ``rate_limits``
    (the only place Claude Code exposes real plan %). Returns (new settings, previous statusLine) - the
    caller keeps the previous one so ``claude_statusline_output`` can still print it."""
    new = copy.deepcopy(settings)
    prev = new.get("statusLine")
    line = " ".join(_shell_quote(a) for a in [*command, "statusline"])
    if isinstance(prev, dict) and prev.get("command") == line:
        return new, None
    new["statusLine"] = {"type": "command", "command": line, "padding": 0}
    return new, prev if isinstance(prev, dict) else None


def claude_statusline_remove(settings: dict, previous: dict | None) -> dict:
    new = copy.deepcopy(settings)
    cur = new.get("statusLine")
    if isinstance(cur, dict) and "statusline" in str(cur.get("command", "")) and "freedock" in str(cur.get("command", "")).lower():
        if previous:
            new["statusLine"] = previous
        else:
            new.pop("statusLine")
    return new


def claude_statusline_output(stdin_text: str, previous: dict | None = None, cache: Path | None = None) -> str:
    """Body of ``freedock statusline``: cache ``rate_limits`` for AiUsage, then print the previous status
    line's output (or a short default)."""
    try:
        d = json.loads(stdin_text)
    except ValueError:
        d = {}
    rl = d.get("rate_limits") if isinstance(d, dict) else None
    if isinstance(rl, dict):
        path = cache or claude_limits_cache()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({**{k: rl.get(k) for k in ("five_hour", "seven_day")},
                                       "captured_at": time.time()}), "utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            log.info("statusline cache: %s", exc)
    if previous and previous.get("command"):
        try:
            r = subprocess.run(previous["command"], input=stdin_text, capture_output=True, text=True, shell=True,
                               timeout=5, **_no_window())
            return r.stdout.rstrip("\n")
        except (OSError, subprocess.SubprocessError):
            pass
    model = ((d.get("model") or {}).get("display_name") if isinstance(d, dict) else None) or ""
    five = (rl or {}).get("five_hour") or {}
    return f"[{model}] 5h {five['used_percentage']:.0f}%" if five.get("used_percentage") is not None else f"[{model}]"


# --------------------------------------------------------------------------------- Codex notify chaining

CODEX_MARKER = "# freedock: previous notify ="
_TABLE_RE = re.compile(r"^\s*\[")
_NOTIFY_RE = re.compile(r"^\s*notify\s*=")


def _toml_array(items: list[str]) -> str:
    def one(s: str) -> str:
        if "'" not in s and "\n" not in s and "\r" not in s:
            return f"'{s}'"
        return json.dumps(s, ensure_ascii=False)
    return "[" + ", ".join(one(s) for s in items) + "]"


def _find_notify(lines: list[str]) -> tuple[int, int, list | None] | None:
    """Top-level ``notify = [...]`` (maybe multi-line) -> (first line, last line exclusive, value)."""
    for i, line in enumerate(lines):
        if _TABLE_RE.match(line):
            return None
        if _NOTIFY_RE.match(line):
            for j in range(i + 1, min(len(lines), i + 200) + 1):
                try:
                    return i, j, tomllib.loads("".join(lines[i:j]).strip() + "\n").get("notify")
                except tomllib.TOMLDecodeError:
                    continue
            return i, i + 1, None
    return None


def _marker(lines: list[str]) -> tuple[int, list | None] | None:
    for i, line in enumerate(lines):
        if _TABLE_RE.match(line):
            return None
        if line.strip().startswith(CODEX_MARKER):
            raw = line.strip()[len(CODEX_MARKER):].strip()
            if raw in ("", "none"):
                return i, None
            try:
                return i, tomllib.loads(f"v = {raw}\n")["v"]
            except tomllib.TOMLDecodeError:
                return i, None
    return None


def _is_freedock_cmd(cmd: Any) -> bool:
    return isinstance(cmd, list) and "notify" in cmd and "codex" in cmd and any("freedock" in str(c).lower() for c in cmd)


def codex_previous_notify(toml_text: str) -> list[str] | None:
    """The notify command FreeDock replaced (to pass to ``codex_forward``), or None."""
    m = _marker(toml_text.splitlines(keepends=True))
    return m[1] if m else None


def codex_notify_patch(toml_text: str, command: list[str]) -> str:
    """Point Codex's ``notify`` at ``[*command, "notify", "codex"]`` (Codex appends the JSON payload as the
    last argument), remembering the old value in a marker comment. Only the notify line(s) change."""
    nl = "\r\n" if "\r\n" in toml_text else "\n"
    ours = [*command, "notify", "codex"]
    lines = toml_text.splitlines(keepends=True)
    found = _find_notify(lines)
    mark = _marker(lines)
    new_line = f"notify = {_toml_array(ours)}{nl}"
    if found and found[2] == ours and mark:
        return toml_text
    if mark:                                    # already chained: just refresh our command
        prev = mark[1]
        mark_line = None
    else:
        prev = found[2] if found and not _is_freedock_cmd(found[2]) else None
        mark_line = f"{CODEX_MARKER} {_toml_array(prev) if prev else 'none'}{nl}"
    if found:
        lines[found[0]:found[1]] = [new_line]
        if mark_line:
            lines.insert(found[0], mark_line)
    else:
        lines[0:0] = ([mark_line] if mark_line else []) + [new_line]
    return "".join(lines)


def codex_notify_remove(toml_text: str) -> str:
    """Undo ``codex_notify_patch``: restore the previous notify (or drop ours if there was none)."""
    lines = toml_text.splitlines(keepends=True)
    mark = _marker(lines)
    if not mark:
        return toml_text
    nl = "\r\n" if "\r\n" in toml_text else "\n"
    found = _find_notify(lines)
    if found and _is_freedock_cmd(found[2]):
        lines[found[0]:found[1]] = [f"notify = {_toml_array(mark[1])}{nl}"] if mark[1] else []
    idx = _marker(lines)[0]
    del lines[idx]
    return "".join(lines)


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}     # CREATE_NO_WINDOW


def codex_forward(previous_cmd: list[str] | None, payload_json: str) -> bool:
    """Fire the notify program FreeDock displaced with the same payload (fire-and-forget)."""
    if not previous_cmd or _is_freedock_cmd(previous_cmd):
        return False
    try:
        subprocess.Popen([*previous_cmd, payload_json], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, **_no_window())
        return True
    except OSError as exc:
        log.info("codex forward %s: %s", previous_cmd[0], exc)
        return False


def codex_event_from_payload(payload: str | dict) -> tuple[str, str | None] | None:
    """Codex notify JSON -> (event, message). Legacy notify only sends ``agent-turn-complete``
    ({"type", "thread-id", "turn-id", "cwd", "client", "input-messages", "last-assistant-message"})."""
    try:
        p = json.loads(payload) if isinstance(payload, str) else payload
    except ValueError:
        return None
    if not isinstance(p, dict):
        return None
    kind = str(p.get("type") or "")
    if kind == "agent-turn-complete":
        msg = p.get("last-assistant-message")
        return "done", (str(msg).strip()[:200] if msg else None)
    if "approval" in kind or kind in ("user-input-requested", "elicitation-requested"):
        cmd = p.get("command")
        return "question", p.get("message") or (" ".join(map(str, cmd)) if isinstance(cmd, list) else cmd)
    if "error" in kind:
        return "error", p.get("message")
    if kind in ("agent-turn-started", "turn-started"):
        return "working", None
    return None


# ------------------------------------------------------------------------------------- OpenCode plugin

_OPENCODE_JS = r"""// FreeDock bridge for OpenCode - generated by FreeDock, safe to delete.
// Mirrors session state to the MiraBox deck via `freedock notify opencode <event>`.
import { spawn } from "node:child_process"

const CMD = __CMD__
const children = new Set()
let last = { event: "", at: 0 }

function send(event, message) {
  const now = Date.now()
  if (event === last.event && now - last.at < 2000 && !message) return
  last = { event, at: now }
  const args = [...CMD.slice(1), "notify", "opencode", event]
  if (message) args.push("--message", String(message).slice(0, 200))
  try {
    const p = spawn(CMD[0], args, { detached: true, stdio: "ignore", windowsHide: true })
    p.on("error", () => {})
    p.unref()
  } catch {}
}

export const FreeDock = async () => ({
  event: async ({ event }) => {
    const p = event.properties || {}
    const sid = p.sessionID || p.info?.sessionID || p.part?.sessionID
    if ((event.type === "session.created" || event.type === "session.updated") && p.info?.parentID) {
      children.add(p.info.id)
      return
    }
    if (sid && children.has(sid)) return          // sub-agent sessions don't drive the tile
    switch (event.type) {
      case "session.status": {
        const t = p.status?.type
        if (t === "busy" || t === "retry") send("working")
        break
      }
      case "message.part.updated":
        send("working")
        break
      case "session.idle":
        send("done")
        break
      case "permission.asked":
      case "permission.updated":
      case "question.asked":
        send("question", p.title || p.message || p.type || "")
        break
      case "session.error":
        send("error", p.error?.data?.message || p.error?.name || "")
        break
    }
  },
})
"""


def opencode_plugin_source(command: list[str]) -> str:
    """JS plugin for ``opencode_plugin_path()`` that shells out to ``<command> notify opencode <event>``."""
    if not command:
        raise ValueError("empty command")
    return _OPENCODE_JS.replace("__CMD__", json.dumps(list(command)))


# ------------------------------------------------------------------------------------------- launching

CODEX_APP_ID = "OpenAI.Codex_2p2nqsd0c76g0!App"     # Get-StartApps lists it as "ChatGPT"


def _in_terminal_win(title: str, cmd: list[str]) -> list[str]:
    if shutil.which("wt.exe") or shutil.which("wt"):
        return ["wt.exe", "-w", "new", "new-tab", "--title", title, *cmd]
    return ["cmd.exe", "/c", "start", title, "cmd.exe", "/k", *cmd]


def _in_terminal_linux(title: str, cmd: list[str]) -> list[str]:
    for term, pre in (("x-terminal-emulator", ["-T", title, "-e"]), ("gnome-terminal", ["--title", title, "--"]),
                      ("konsole", ["-p", f"tabtitle={title}", "-e"]), ("kitty", ["--title", title]),
                      ("alacritty", ["--title", title, "-e"]), ("xterm", ["-T", title, "-e"])):
        if shutil.which(term):
            return [term, *pre, *cmd]
    return ["xterm", "-T", title, "-e", *cmd]


def launch_commands(platform: str | None = None) -> dict[str, list[str]]:
    """argv per tool: claude / codex (CLI) / codex_app (desktop, Windows) / opencode."""
    platform = platform or sys.platform
    if platform == "win32":
        local_claude = _home() / ".local" / "bin" / "claude.exe"
        claude = str(local_claude) if local_claude.exists() else "claude"
        out = {"claude": _in_terminal_win("Claude Code", [claude]),
               "codex": _in_terminal_win("Codex", [_CodexReader.cli_path() or "codex"]),
               "opencode": _in_terminal_win("OpenCode", ["opencode"]),
               "codex_app": ["explorer.exe", f"shell:AppsFolder\\{CODEX_APP_ID}"]}
        return out
    return {"claude": _in_terminal_linux("Claude Code", ["claude"]),
            "codex": _in_terminal_linux("Codex", ["codex"]),
            "opencode": _in_terminal_linux("OpenCode", ["opencode"])}


LAUNCH = launch_commands()
