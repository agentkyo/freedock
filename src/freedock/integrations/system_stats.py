"""System metrics sampled on a background thread: CPU, RAM, GPU (NVIDIA / AMD), network, weather."""
from __future__ import annotations

import glob
import json
import logging
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import psutil

log = logging.getLogger(__name__)


class SystemStats:
    def __init__(self):
        self.data: dict = {"cpu": 0.0, "ram": 0.0, "ram_used_gb": 0.0, "gpu": None, "gpu_temp": None,
                           "net_down": 0.0, "net_up": 0.0, "cpu_temp": None}
        self._thread: threading.Thread | None = None
        self._gpu_proc: subprocess.Popen | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sysstats", daemon=True)
        self._thread.start()
        threading.Thread(target=self._gpu_loop, name="gpustats", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        if self._gpu_proc:
            self._gpu_proc.terminate()

    def _run(self) -> None:
        psutil.cpu_percent(None)
        last = psutil.net_io_counters()
        last_t = time.monotonic()
        while not self._stop.wait(1.0):
            vm = psutil.virtual_memory()
            now = psutil.net_io_counters()
            t = time.monotonic()
            dt = max(0.001, t - last_t)
            self.data.update(cpu=psutil.cpu_percent(None), ram=vm.percent, ram_used_gb=vm.used / 2**30,
                             net_down=(now.bytes_recv - last.bytes_recv) / dt,
                             net_up=(now.bytes_sent - last.bytes_sent) / dt)
            last, last_t = now, t

    def _gpu_loop(self) -> None:
        smi = shutil.which("nvidia-smi")
        if smi:
            flags = 0x08000000 if sys.platform == "win32" else 0
            try:
                self._gpu_proc = subprocess.Popen(
                    [smi, "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits", "-l", "1"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=flags)
                for line in self._gpu_proc.stdout:
                    if self._stop.is_set():
                        break
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 2 and parts[0].isdigit():
                        self.data["gpu"], self.data["gpu_temp"] = float(parts[0]), float(parts[1])
                return
            except OSError:
                pass
        # AMD / Intel on Linux expose a busy percentage in sysfs
        files = glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")
        while files and not self._stop.wait(1.0):
            try:
                self.data["gpu"] = float(open(files[0]).read().strip())
            except (OSError, ValueError):
                return


class Weather:
    """Current weather from Open-Meteo (free, no API key). Refreshes every 15 minutes."""

    CODES = {0: ("sun", "Clear"), 1: ("cloud-sun", "Mostly clear"), 2: ("cloud-sun", "Partly cloudy"),
             3: ("cloud", "Overcast"), 45: ("cloud-fog", "Fog"), 48: ("cloud-fog", "Fog"),
             51: ("cloud-drizzle", "Drizzle"), 53: ("cloud-drizzle", "Drizzle"), 55: ("cloud-drizzle", "Drizzle"),
             61: ("cloud-rain", "Rain"), 63: ("cloud-rain", "Rain"), 65: ("cloud-rain", "Heavy rain"),
             71: ("cloud-snow", "Snow"), 73: ("cloud-snow", "Snow"), 75: ("cloud-snow", "Snow"),
             80: ("cloud-rain", "Showers"), 81: ("cloud-rain", "Showers"), 82: ("cloud-rain", "Showers"),
             95: ("cloud-lightning", "Storm"), 96: ("cloud-lightning", "Storm"), 99: ("cloud-lightning", "Storm")}

    def __init__(self):
        self.data: dict = {}
        self.where: tuple[float, float] | None = None
        self._last = 0.0
        self._busy = False

    def poll(self, lat, lon) -> dict:
        if lat is None or lon is None:
            return {}
        if (lat, lon) != self.where:
            self.where, self._last, self.data = (lat, lon), 0.0, {}
        if time.time() - self._last > 900 and not self._busy:
            self._busy = True
            threading.Thread(target=self._fetch, args=(lat, lon), daemon=True).start()
        return self.data

    def _fetch(self, lat, lon) -> None:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               "&current=temperature_2m,weather_code,relative_humidity_2m&timezone=auto")
        try:
            with urllib.request.urlopen(url, timeout=10) as r:   # noqa: S310 - fixed https host
                cur = json.load(r)["current"]
            icon, text = self.CODES.get(int(cur["weather_code"]), ("cloud", ""))
            self.data = {"temp": cur["temperature_2m"], "humidity": cur.get("relative_humidity_2m"),
                         "icon": icon, "text": text}
            self._last = time.time()
        except Exception as exc:  # noqa: BLE001 - offline is normal
            log.info("weather fetch failed: %s", exc)
            self._last = time.time() - 600   # retry in 5 minutes
        finally:
            self._busy = False


stats = SystemStats()
weather = Weather()
