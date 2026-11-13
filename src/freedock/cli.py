"""Command line: ``freedock`` opens the editor; subcommands for headless use and diagnostics."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

from . import APP_NAME, __version__


def _setup_logging(verbose: bool) -> None:
    from .profile import config_dir
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()] if sys.stderr else []
    try:
        config_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(config_dir() / "freedock.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    # a native crash (driver, COM, ctypes) kills the process without a Python traceback;
    # faulthandler writes every thread's stack to crash.log so it can be diagnosed
    try:
        import faulthandler
        global _crash_file
        _crash_file = open(config_dir() / "crash.log", "a", encoding="utf-8")  # noqa: SIM115
        faulthandler.enable(_crash_file, all_threads=True)
    except OSError:
        pass


_crash_file = None


def _app(headless: bool):
    if headless and not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    app = (QGuiApplication if headless else QApplication)(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    return app


def cmd_list(args) -> int:
    from .device import enumerate_devices
    devs = enumerate_devices()
    if not devs:
        print("No MiraBox N4 found. On Linux, install the udev rule (see README).")
        return 1
    for d in devs:
        print(f"{d.label}  path={d.path.decode(errors='replace')}")
    return 0


def cmd_events(args) -> int:
    """Print every decoded input - handy to check a new N4 variant."""
    from .device import DeviceWatcher
    w = DeviceWatcher(lambda d: print(f"connected: {d.info.label} firmware={d.firmware}"),
                      lambda: print("disconnected"), lambda e: print(f"{e.kind.value}{e.index} {e.type.value} {e.value or ''}"))
    w.start()
    print("Press keys / turn knobs. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        w.stop()
    return 0


def _load(args):
    from .i18n import set_language
    from .profile import Profile, load_or_create
    from .settings import Settings
    settings = Settings.load()
    if getattr(args, "lang", None):
        settings.language = args.lang
    set_language(settings.language)
    profile = Profile.load(Path(args.profile)) if getattr(args, "profile", None) else load_or_create(settings.language)
    return settings, profile


INSTANCE = "freedock-" + (os.environ.get("USERNAME") or os.environ.get("USER") or "user")


def send_to_running(message: str, need_ack: bool = False) -> bool:
    """Deliver a message ("show", "quit", "notify {...}") to an already running FreeDock.

    True if one is running (``need_ack``: only if it also confirmed it got the message)."""
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if not sock.waitForConnected(400):
        return False
    sock.write(message.encode())
    sock.waitForBytesWritten(400)
    acked = sock.waitForReadyRead(2000) and bytes(sock.readAll().data()).startswith(b"ok")
    sock.disconnectFromServer()
    return acked if need_ack else True


_instance_lock = None


def claim_instance() -> bool:
    """Become the only FreeDock allowed to open the deck, right now.

    ``send_to_running`` only finds an instance that is already serving its socket, which happens
    seconds after start. Two launches inside that window (autostart plus a double-click, or a
    restart racing the old process) both went on to open the deck and fought over it. A lock file
    closes the window; the OS drops it if the process dies."""
    global _instance_lock
    import tempfile
    from PySide6.QtCore import QLockFile
    lock = QLockFile(str(Path(tempfile.gettempdir()) / (INSTANCE + ".lock")))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        return False
    _instance_lock = lock
    return True


def serve_instance(handlers: dict):
    """Become the single running instance; ``handlers`` maps message -> callable."""
    from PySide6.QtNetwork import QLocalServer
    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE)
    server.listen(INSTANCE)

    def on_conn():
        sock = server.nextPendingConnection()
        sock.waitForReadyRead(400)
        msg = bytes(sock.readAll().data()).decode(errors="ignore").strip()
        name, _, arg = msg.partition(" ")
        if msg:
            sock.write(b"ok")                 # acknowledge before acting: "quit" ends this process
            sock.waitForBytesWritten(200)
        if name in handlers:
            try:
                handlers[name](arg) if arg else handlers[name]()
            except Exception:  # noqa: BLE001 - a bad message must not take the app down
                logging.exception("instance message %s failed", name)
    server.newConnection.connect(on_conn)
    return server


def _deliver_notify(engine, arg: str) -> None:
    from .notify import parse_message
    d = parse_message(arg)
    if d:
        engine.notify_event(str(d["source"]), str(d.get("event") or "done"), str(d.get("message") or ""),
                            d.get("progress"))


def _hook_stdin() -> str:
    """The JSON a Claude Code hook writes to stdin (empty when run by hand)."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        # Claude Code writes UTF-8; text-mode stdin on Windows would decode it as cp1252 (mojibake)
        raw = sys.stdin.buffer.read(1_000_000) if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode()
        return raw.decode("utf-8-sig", errors="replace")
    except (OSError, ValueError):
        return ""


def cmd_notify(args) -> int:
    """Tell the running FreeDock that something happened (used by AI hooks and scripts)."""
    import json
    event, message = args.event, args.message or ""
    if event.lstrip().startswith("{"):
        # Codex's notify program gets its JSON payload as the last argument
        from .integrations import ai, ai_setup
        mapped = ai.codex_event_from_payload(event)
        ai.codex_forward(ai_setup.codex_previous(), event)      # keep the Codex app's own notifier working
        if not mapped:
            return 0
        event, message = mapped[0], message or (mapped[1] or "")
    elif args.source == "claude" and not message:
        stdin = _hook_stdin()
        if stdin:
            from .integrations import ai
            mapped = ai.claude_event_from_hook(stdin)
            if mapped is None:
                return 0
            event, message = mapped[0], mapped[1] or ""
    payload = {"source": args.source, "event": event, "message": message}
    if args.progress is not None:
        payload["progress"] = args.progress
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.instance() or QCoreApplication([])
    if send_to_running("notify " + json.dumps(payload, ensure_ascii=False)):
        return 0
    print("FreeDock is not running.", file=sys.stderr)
    return 1


def cmd_statusline(args) -> int:
    """Claude Code status line: records the plan usage % for the deck, then prints the user's own line."""
    from .integrations import ai
    from .settings import Settings
    previous = (Settings.load().ai or {}).get("claude_statusline_previous")
    sys.stdout.write(ai.claude_statusline_output(_hook_stdin(), previous) + "\n")
    return 0


def cmd_sound(args) -> int:
    """Manage the soundboard library: add a link/file, list, remove."""
    from PySide6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])  # noqa: F841 - the decoder needs one
    from .soundboard.library import library
    lib = library()
    if args.op == "list":
        for s in lib.list():
            print(f"{s.id:24} {s.duration:6.1f}s  {s.name}" + (f"  <{s.source}>" if s.source and not s.builtin else ""))
        return 0
    if args.op == "remove":
        ok = lib.remove(args.target or "")
        print("removed" if ok else "no such sound")
        return 0 if ok else 1
    target = args.target or ""
    try:
        if target.startswith(("http://", "https://")):
            from .soundboard.ytdl import add_from_url

            def progress(frac, msg):
                print(f"  {'' if frac is None else f'{frac * 100:3.0f}% '}{msg}", flush=True)
            snd = add_from_url(lib, target, args.name or "", args.start, args.end, progress=progress)
        else:
            snd = lib.add_file(target, args.name or "", args.start, args.end)
    except Exception as exc:  # noqa: BLE001 - shown to the user
        print(f"error: {exc}")
        return 1
    print(f"added {snd.id}: {snd.name} ({snd.duration:.1f} s)")
    return 0


def cmd_ai(args) -> int:
    """Install or remove the Claude Code / Codex / OpenCode bridges."""
    from .integrations import ai_setup
    from .settings import Settings
    cmd = launch_command()
    tools = ("claude", "codex", "opencode") if args.tool == "all" else (args.tool,)
    s = Settings.load()
    for tool in tools:
        try:
            if args.op == "install":
                if tool == "claude":
                    prev = ai_setup.claude_install(cmd)
                    if prev:
                        s.ai = {**(s.ai or {}), "claude_statusline_previous": prev}
                        s.save()
                elif tool == "codex":
                    ai_setup.codex_install(cmd)
                else:
                    ai_setup.opencode_install(cmd)
            elif args.op == "remove":
                if tool == "claude":
                    ai_setup.claude_remove((s.ai or {}).get("claude_statusline_previous"))
                elif tool == "codex":
                    ai_setup.codex_remove()
                else:
                    ai_setup.opencode_remove()
            ok = getattr(ai_setup, f"{tool}_installed")()
            print(f"{tool:9} {'installed' if ok else 'not installed'}")
        except (OSError, ValueError) as exc:
            print(f"{tool:9} error: {exc}")
    return 0


def cmd_quit(args) -> int:
    """Close a running FreeDock cleanly (it hands the screens back to the firmware)."""
    _app(headless=True)
    if send_to_running("quit", need_ack=True):
        print("FreeDock is closing.")
        return 0
    # running but not answering on the instance socket: try the HTTP bridge
    from .settings import Settings
    import urllib.request
    port = int((Settings.load().notify or {}).get("http_port") or 0)
    if port:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/quit", timeout=3) as r:  # noqa: S310
                if r.status == 200:
                    print("FreeDock is closing (via HTTP).")
                    return 0
        except OSError:
            pass
    print("FreeDock is not running." if not send_to_running("ping") else "FreeDock is running but not answering.")
    return 1


def cmd_run(args) -> int:
    app = _app(headless=True)
    if send_to_running("ping") or not claim_instance():
        print("FreeDock is already running (use 'freedock quit' first).")
        return 1
    from .engine import Engine
    settings, profile = _load(args)
    engine = Engine(profile, settings)
    engine.device_changed.connect(lambda s: logging.info("device: %s", s or "disconnected"))
    engine.page_changed.connect(lambda p: logging.info("page: %s", p))
    engine.start()
    engine.on_quit = app.quit
    _server = serve_instance({"quit": app.quit, "ping": lambda: None,  # noqa: F841 - keep alive
                              "notify": lambda arg: _deliver_notify(engine, arg)})
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    signal.signal(signal.SIGTERM, lambda *a: app.quit())
    from PySide6.QtCore import QTimer
    keepalive = QTimer()
    keepalive.start(250)          # lets Python handle Ctrl+C while Qt runs
    keepalive.timeout.connect(lambda: None)
    code = app.exec()
    engine.stop()
    return code


def cmd_preview(args) -> int:
    """Render every page to PNG (what the deck would show) without touching the device."""
    os.environ["FREEDOCK_NO_AUDIO"] = "1"      # rendering only: never open the microphone / PortAudio
    _app(headless=True)
    from PySide6.QtGui import QColor, QImage, QPainter
    from .device import N4, Kind
    from .engine import SLOTS, Engine
    settings, profile = _load(args)
    engine = Engine(profile, settings)
    engine.profile.animations = False
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    kw, kh = N4.key_size
    zw, zh = N4.zone_size
    gap = 10
    from PySide6.QtCore import QCoreApplication
    for page in engine.visible_pages():
        engine.page = page
        end = time.time() + float(args.warmup)
        while True:
            QCoreApplication.processEvents()
            engine._visuals.clear()
            engine._on_tick()
            if time.time() >= end:
                break
            time.sleep(1 / 30)
        sheet = QImage(max(5 * kw + 6 * gap, 4 * zw + 12 + 2 * gap), 2 * kh + zh + 5 * gap, QImage.Format.Format_RGB32)
        sheet.fill(QColor("#202329"))
        p = QPainter(sheet)
        for ref in SLOTS:
            img = engine.images[ref.key]
            if ref.kind == Kind.KEY:
                r, c = divmod(ref.index, 5)
                p.drawImage((sheet.width() - 5 * kw - 4 * gap) // 2 + c * (kw + gap), gap + r * (kh + gap), img)
            else:
                strip_w = 4 * zw + 3 * 4
                x0 = (sheet.width() - strip_w) // 2
                p.drawImage(x0 + ref.index * (zw + 4), 3 * gap + 2 * kh + gap, img)
        p.end()
        path = out / f"{page.id}.png"
        sheet.save(str(path))
        print(path)
    return 0


def cmd_import(args) -> int:
    _app(headless=True)
    from .importers.streamdock import import_profiles
    settings, profile = _load(args)
    pages = import_profiles(Path(args.path) if args.path else None)
    if not pages:
        print("No StreamDock N4 profiles found.")
        return 1
    profile.pages.extend(pages)
    profile.save()
    for p in pages:
        print(f"imported page '{p.name}'")
    return 0


def cmd_reset(args) -> int:
    from .presets import build_default_profile
    from .profile import default_profile_path
    from .settings import Settings
    settings = Settings.load()
    path = default_profile_path()
    if path.exists():
        path.replace(path.with_suffix(f".backup-{int(time.time())}.json"))
    build_default_profile(args.lang or settings.language).save(path)
    print(f"default profile written to {path}")
    return 0


def launch_command(gui: bool = True) -> list[str]:
    """How to start FreeDock again: the frozen FreeDock.exe, or the interpreter running the package.

    ``gui`` picks pythonw.exe on Windows so no console window appears.
    """
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        return [str(exe)]
    if gui and sys.platform == "win32" and exe.with_name("pythonw.exe").exists():
        exe = exe.with_name("pythonw.exe")
    return [str(exe), "-m", "freedock"]


def _supervise(argv: list[str]) -> int:
    """Run the GUI in a child process and bring it back if it dies.

    A native crash (a driver, COM or ctypes fault) takes the process down mid-upload and leaves
    the panel frozen on its last image. The restarted child resyncs the panel on connect, so the
    deck recovers by itself in a few seconds instead of staying stuck until someone notices.
    """
    import subprocess
    from PySide6.QtCore import QCoreApplication
    QCoreApplication([])
    if send_to_running("show"):
        return 0
    env = dict(os.environ, FREEDOCK_CHILD="1")
    cmd = [*launch_command(), *argv]
    quick_crashes = 0
    while True:
        started = time.monotonic()
        code = subprocess.call(cmd, env=env)
        if code == 0:
            return 0
        lived = time.monotonic() - started
        quick_crashes = quick_crashes + 1 if lived < 20 else 0
        logging.error("FreeDock exited with code %s (0x%08X) after %.0f s", code, code & 0xFFFFFFFF, lived)
        if quick_crashes >= 3:
            logging.error("crashed 3 times in a row right after starting; giving up")
            return code
        time.sleep(2)
        logging.info("restarting FreeDock")
        if "--minimized" not in cmd:
            cmd.append("--minimized")       # a restart should not pop the editor up in front of a game


def gui_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freedock-gui")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--lang")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    if not os.environ.get("FREEDOCK_CHILD") and not os.environ.get("FREEDOCK_NO_SUPERVISOR"):
        return _supervise(sys.argv[1:] if argv is None else list(argv))
    app = _app(headless=False)
    if send_to_running("show"):       # already running: just bring its window up
        return 0
    if not claim_instance():          # another one is still starting (or still closing): let it be
        logging.info("another FreeDock is starting or closing; not opening the deck twice")
        return 0
    from .gui.main_window import run_gui
    settings, profile = _load(args)
    return run_gui(app, profile, settings, minimized=args.minimized or settings.start_minimized)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--demo" in argv:          # fake Assetto Corsa / Flight Sim data, works with any command
        argv = [a for a in argv if a != "--demo"]
        os.environ["FREEDOCK_DEMO"] = "1"
    if not argv or argv[0].startswith("--") and argv[0] not in ("--version", "--help", "-h"):
        return gui_main(argv)
    parser = argparse.ArgumentParser(prog="freedock", description=f"{APP_NAME} {__version__} - MiraBox N4 without vendor software")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="open the editor (default)")
    r = sub.add_parser("run", help="run headless (no window), e.g. as a service")
    r.add_argument("--profile")
    r.add_argument("--lang")
    sub.add_parser("list", help="list connected devices")
    sub.add_parser("quit", help="close a running FreeDock cleanly")
    nt = sub.add_parser("notify", help="send an event to the running FreeDock (AI hooks, scripts)")
    nt.add_argument("source", help="claude | codex | opencode | custom | any name")
    nt.add_argument("event", nargs="?", default="done", help="working | progress | done | question | error | idle")
    nt.add_argument("--message", "-m", default="")
    nt.add_argument("--progress", type=float)
    sub.add_parser("statusline", help="Claude Code status line bridge (reads JSON on stdin)")
    ai_p = sub.add_parser("ai", help="install/remove the Claude Code, Codex and OpenCode bridges")
    ai_p.add_argument("op", choices=("install", "remove", "status"))
    ai_p.add_argument("tool", nargs="?", default="all", choices=("all", "claude", "codex", "opencode"))
    so = sub.add_parser("sound", help="soundboard library: add <link|file>, list, remove <id>")
    so.add_argument("op", choices=("add", "list", "remove"))
    so.add_argument("target", nargs="?", help="YouTube/other link or audio file (add), sound id (remove)")
    so.add_argument("--name", "-n")
    so.add_argument("--start", "-s", help="e.g. 1:23")
    so.add_argument("--end", "-e", help="e.g. 1:30")
    sub.add_parser("events", help="print decoded key/knob/touch events")
    pv = sub.add_parser("preview", help="render all pages to PNG files")
    pv.add_argument("--out", default="preview")
    pv.add_argument("--warmup", default="0", help="seconds of live data before each snapshot")
    pv.add_argument("--profile")
    pv.add_argument("--lang")
    im = sub.add_parser("import-streamdock", help="import N4 profiles from the official StreamDock app")
    im.add_argument("path", nargs="?")
    rs = sub.add_parser("reset-profile", help="back up the current profile and restore the default pages")
    rs.add_argument("--lang")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    handlers = {"quit": cmd_quit, "notify": cmd_notify, "statusline": cmd_statusline, "ai": cmd_ai, "sound": cmd_sound, "run": cmd_run, "list": cmd_list, "events": cmd_events, "preview": cmd_preview,
                "import-streamdock": cmd_import, "reset-profile": cmd_reset}
    if args.cmd in (None, "gui"):
        return gui_main([])
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
