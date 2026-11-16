"""Sharing a game's UDP telemetry port with other apps on the same PC.

A UDP port has one owner, and games such as BeamNG and Motor Town send their telemetry to a single
address. So the apps that want it cooperate: whoever binds the game's port first repeats every
packet, untouched, on a second "shared" port; whoever finds the game's port taken listens on the
shared one instead, and keeps trying the game's port so it takes over when the owner leaves.
PitGlass (github.com/agentkyo/pitglass) follows the same rule with the same ports.
"""
from __future__ import annotations

import logging
import socket
import time

log = logging.getLogger(__name__)

#: game port -> shared port
SHARE_PORTS = {4444: 47834, 33330: 47835}


class SharedPort:
    """A UDP listener on ``port`` that shares it through ``share`` (None/0: no sharing).

    ``recv()`` returns one packet, or None when nothing arrived within ``timeout`` seconds.
    """

    RETRY_S = 5.0

    def __init__(self, port: int, share: int | None = None, host: str = "127.0.0.1", timeout: float = 0.5):
        self.port, self.share, self.host, self.timeout = port, share or 0, host, timeout
        self._main: socket.socket | None = None
        self._alt: socket.socket | None = None
        self._out: socket.socket | None = None
        self._next = 0.0
        self._told = False

    @property
    def owner(self) -> bool:
        """True while this app holds the game's own port (and repeats it for the others)."""
        return self._main is not None

    def _bind(self, port: int) -> socket.socket | None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.host, port))
            sock.settimeout(self.timeout)
            return sock
        except OSError:
            sock.close()
            return None

    def _open(self) -> None:
        self._main = self._bind(self.port)
        if self._main is not None:
            if self._alt is not None:        # the shared feed is ours to send now, not to read
                self._alt.close()
                self._alt = None
            self._told = False
            return
        if self._alt is None and self.share:
            self._alt = self._bind(self.share)
        if self._alt is None and not self._told:
            self._told = True
            log.info("cannot listen on UDP %s:%s (in use by an app that does not share it)", self.host, self.port)

    def recv(self, size: int = 4096) -> bytes | None:
        now = time.monotonic()
        if self._main is None and now >= self._next:
            self._next = now + self.RETRY_S
            self._open()
        sock = self._main or self._alt
        if sock is None:
            time.sleep(self.timeout)
            return None
        try:
            pkt, addr = sock.recvfrom(size)
        except socket.timeout:
            return None
        except OSError:
            sock.close()
            if sock is self._main:
                self._main = None
            else:
                self._alt = None
            return None
        if addr[0] != self.host:
            return None
        if sock is self._main and self.share:
            try:
                # a socket of its own: on Windows, sending to a port nobody listens on makes the
                # sender's next recv fail, and this one never receives
                if self._out is None:
                    self._out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._out.sendto(pkt, (self.host, self.share))
            except OSError:
                pass
        return pkt

    def close(self) -> None:
        for sock in (self._main, self._alt, self._out):
            if sock is not None:
                sock.close()
        self._main = self._alt = self._out = None
