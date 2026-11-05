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


def send_to_running(message: str) -> bool:
    """Deliver a message ("show", "quit") to an already running FreeDock. True if one answered."""
    from PySide6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if not sock.waitForConnected(400):
        return False
    sock.write(message.encode())
    sock.waitForBytesWritten(400)
    sock.disconnectFromServer()
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
        if msg in handlers:
            handlers[msg]()
    server.newConnection.connect(on_conn)
    return server


def cmd_quit(args) -> int:
    """Close a running FreeDock cleanly (it hands the screens back to the firmware)."""
    _app(headless=True)
    if send_to_running("quit"):
        print("FreeDock is closing.")
        return 0
    print("FreeDock is not running.")
    return 1


def cmd_run(args) -> int:
    app = _app(headless=True)
    if send_to_running("ping"):
        print("FreeDock is already running (use 'freedock quit' first).")
        return 1
    from .engine import Engine
    settings, profile = _load(args)
    engine = Engine(profile, settings)
    engine.device_changed.connect(lambda s: logging.info("device: %s", s or "disconnected"))
    engine.page_changed.connect(lambda p: logging.info("page: %s", p))
    engine.start()
    _server = serve_instance({"quit": app.quit, "ping": lambda: None})  # noqa: F841 - keep alive
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


def gui_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freedock-gui")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--lang")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    app = _app(headless=False)
    if send_to_running("show"):       # already running: just bring its window up
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
    handlers = {"quit": cmd_quit, "run": cmd_run, "list": cmd_list, "events": cmd_events, "preview": cmd_preview,
                "import-streamdock": cmd_import, "reset-profile": cmd_reset}
    if args.cmd in (None, "gui"):
        return gui_main([])
    return handlers[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
