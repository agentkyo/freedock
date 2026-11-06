"""Grab the audio of a YouTube (or any site yt-dlp supports) link into the sound library.

yt-dlp is not bundled: sites change all the time and a frozen copy would break within weeks.
FreeDock uses the one on PATH or downloads the official release once into its tools folder
(checked against the release's SHA2-256SUMS) and updates it when a download fails. Audio is
fetched as-is (no ffmpeg needed) and decoded by Qt's FFmpeg.

Only download what you have the right to use; this is meant for short clips for personal use.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .library import Library, Sound, parse_time

log = logging.getLogger(__name__)

RELEASE = "https://github.com/yt-dlp/yt-dlp/releases/latest/download"
MAX_DURATION_S = 30 * 60
Progress = Callable[[float | None, str], None]     # (0..1 or None, message)


class DownloadError(Exception):
    pass


def tools_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "FreeDock" / "tools"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "freedock" / "tools"


def _asset() -> str:
    return "yt-dlp.exe" if sys.platform == "win32" else "yt-dlp"


def find_ytdlp() -> Path | None:
    local = tools_dir() / _asset()
    if local.is_file():
        return local
    found = shutil.which("yt-dlp")
    return Path(found) if found else None


def _no_window() -> dict:
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}       # CREATE_NO_WINDOW
    return {}


def install_ytdlp(progress: Progress | None = None) -> Path:
    """Download the official yt-dlp release and verify its checksum."""
    name = _asset()
    dest = tools_dir() / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(None, "yt-dlp…")
    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "FreeDock")]
    try:
        with opener.open(f"{RELEASE}/SHA2-256SUMS", timeout=30) as r:
            sums = r.read().decode("utf-8", "replace")
        expected = next((ln.split()[0] for ln in sums.splitlines() if ln.strip().endswith(f" {name}")), None)
        if not expected:
            raise DownloadError("yt-dlp checksum list has no entry for " + name)
        tmp = dest.with_suffix(".part")
        h = hashlib.sha256()
        with opener.open(f"{RELEASE}/{name}", timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got / total * 0.3, "yt-dlp…")
    except OSError as exc:
        raise DownloadError(f"could not download yt-dlp: {exc}") from exc
    if h.hexdigest() != expected.lower():
        tmp.unlink(missing_ok=True)
        raise DownloadError("yt-dlp download failed its checksum - not installed")
    tmp.replace(dest)
    if sys.platform != "win32":
        dest.chmod(0o755)
    log.info("installed yt-dlp into %s", dest)
    return dest


def ensure_ytdlp(progress: Progress | None = None) -> Path:
    return find_ytdlp() or install_ytdlp(progress)


def update_ytdlp() -> bool:
    exe = find_ytdlp()
    if exe is None or exe.parent != tools_dir():
        return False                    # a system copy is the package manager's business
    try:
        r = subprocess.run([str(exe), "-U"], capture_output=True, text=True, timeout=120, **_no_window())
        log.info("yt-dlp -U: %s", (r.stdout or r.stderr).strip()[-200:])
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def start_from_url(url: str) -> float | None:
    """The ``t=`` of a shared link (``?t=83``, ``&t=1m23s``, ``#t=83``)."""
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return None
    for q in (parts.query, parts.fragment):
        t = urllib.parse.parse_qs(q).get("t") or urllib.parse.parse_qs(q).get("start")
        if t:
            try:
                return parse_time(t[0])
            except ValueError:
                return None
    return None


_PCT = re.compile(r"(\d+(?:\.\d+)?)%")


def fetch_audio(url: str, folder: Path, progress: Progress | None = None, _retry: bool = True) -> tuple[Path, str]:
    """Download the best audio-only stream. Returns (file, title)."""
    exe = ensure_ytdlp(progress)
    cmd = [str(exe), "--no-playlist", "--no-colors", "--newline", "--no-part",
           "-f", "bestaudio[ext=m4a]/bestaudio/best",
           "--match-filter", f"duration <=? {MAX_DURATION_S}",
           "-o", str(folder / "%(id)s.%(ext)s"),
           "--print", "before_dl:TITLE=%(title)s",
           "--print", "after_move:FILE=%(filepath)s",
           "--no-simulate", url]
    log.info("yt-dlp %s", url)
    title, path, tail = "", None, []
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", **_no_window())
    except OSError as exc:
        raise DownloadError(f"could not run yt-dlp: {exc}") from exc
    t0 = time.monotonic()
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("TITLE="):
            title = line[6:]
            if progress:
                progress(0.35, title)
        elif line.startswith("FILE="):
            path = Path(line[5:])
        elif "[download]" in line and (m := _PCT.search(line)):
            if progress:
                progress(0.35 + 0.5 * float(m.group(1)) / 100, title or url)
        else:
            tail = (tail + [line])[-8:]
        if time.monotonic() - t0 > 600:
            proc.kill()
            raise DownloadError("download took too long")
    code = proc.wait()
    if code != 0 or path is None or not path.is_file():
        msg = next((ln for ln in reversed(tail) if "ERROR" in ln), tail[-1] if tail else f"exit {code}")
        if "does not pass filter" in " ".join(tail):
            raise DownloadError(f"longer than {MAX_DURATION_S // 60} minutes - pick a shorter video")
        if _retry and update_ytdlp():
            return fetch_audio(url, folder, progress, _retry=False)
        raise DownloadError(msg.replace("ERROR: ", "").strip())
    return path, title


def add_from_url(lib: Library, url: str, name: str = "", start=None, end=None,
                 progress: Progress | None = None) -> Sound:
    """Download, decode, trim and store. ``start`` defaults to the link's ``t=``."""
    from .decode import decode_file
    url = url.strip()
    if not re.match(r"^https?://", url):
        raise DownloadError("not a link")
    start_s = parse_time(start)
    if start_s is None:
        start_s = start_from_url(url) or 0.0
    end_s = parse_time(end)
    with tempfile.TemporaryDirectory(prefix="freedock-dl-") as tmp:
        path, title = fetch_audio(url, Path(tmp), progress)
        if progress:
            progress(0.9, title)
        samples = decode_file(path)
    sound = lib.add_samples(name or title or "YouTube", samples, source=url, start=start_s, end=end_s)
    if progress:
        progress(1.0, sound.name)
    return sound
