"""OBS Studio over obs-websocket v5 (built into OBS 28+), using Qt's WebSocket - no extra dependency."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import uuid

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtWebSockets import QWebSocket

log = logging.getLogger(__name__)


class ObsClient(QObject):
    def __init__(self):
        super().__init__()
        self.host, self.port, self.password = "localhost", 4455, ""
        self.connected = False
        self.state: dict = {"recording": False, "streaming": False, "scene": "", "muted": {}}
        self._ws = QWebSocket()
        self._ws.textMessageReceived.connect(self._on_message)
        self._ws.disconnected.connect(self._on_closed)
        self._retry = QTimer(self)
        self._retry.setInterval(5000)
        self._retry.timeout.connect(self._connect)
        self.enabled = False

    def configure(self, host: str, port: int, password: str) -> None:
        self.host, self.port, self.password = host or "localhost", int(port or 4455), password or ""

    def ensure(self) -> None:
        """Start connecting the first time something on the deck needs OBS."""
        if not self.enabled:
            self.enabled = True
            self._connect()
            self._retry.start()

    def _connect(self) -> None:
        if self.connected:
            return
        self._ws.abort()
        self._ws.open(QUrl(f"ws://{self.host}:{self.port}"))

    def _on_closed(self) -> None:
        self.connected = False

    def _send(self, op: int, d: dict) -> None:
        self._ws.sendTextMessage(json.dumps({"op": op, "d": d}))

    def request(self, request_type: str, data: dict | None = None) -> None:
        if not self.connected:
            self.ensure()
            return
        self._send(6, {"requestType": request_type, "requestId": uuid.uuid4().hex,
                       "requestData": data or {}})

    def _on_message(self, text: str) -> None:
        msg = json.loads(text)
        op, d = msg.get("op"), msg.get("d", {})
        if op == 0:   # Hello
            ident = {"rpcVersion": 1, "eventSubscriptions": 1 | 4 | 8 | 64}  # general, scenes, inputs, outputs
            auth = d.get("authentication")
            if auth:
                secret = base64.b64encode(hashlib.sha256((self.password + auth["salt"]).encode()).digest()).decode()
                ident["authentication"] = base64.b64encode(
                    hashlib.sha256((secret + auth["challenge"]).encode()).digest()).decode()
            self._send(1, ident)
        elif op == 2:  # Identified
            self.connected = True
            log.info("OBS connected")
            for req in ("GetRecordStatus", "GetStreamStatus", "GetCurrentProgramScene"):
                self.request(req)
        elif op == 5:  # Event
            et, ed = d.get("eventType"), d.get("eventData", {})
            if et == "RecordStateChanged":
                self.state["recording"] = bool(ed.get("outputActive"))
            elif et == "StreamStateChanged":
                self.state["streaming"] = bool(ed.get("outputActive"))
            elif et == "CurrentProgramSceneChanged":
                self.state["scene"] = ed.get("sceneName", "")
            elif et == "InputMuteStateChanged":
                self.state["muted"][ed.get("inputName")] = bool(ed.get("inputMuted"))
        elif op == 7:  # RequestResponse
            rt, rd = d.get("requestType"), d.get("responseData") or {}
            if rt == "GetRecordStatus":
                self.state["recording"] = bool(rd.get("outputActive"))
            elif rt == "GetStreamStatus":
                self.state["streaming"] = bool(rd.get("outputActive"))
            elif rt == "GetCurrentProgramScene":
                self.state["scene"] = rd.get("currentProgramSceneName", rd.get("sceneName", ""))


obs: ObsClient | None = None


def client() -> ObsClient:
    global obs
    if obs is None:
        obs = ObsClient()
    return obs
