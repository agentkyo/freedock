"""Wire protocol of the MiraBox / HotSpot "CRT" StreamDock family (N4, firmware V3.293N4.x).

Everything here is pure: functions build the exact bytes that go on the wire, so they can be
unit-tested without hardware. See docs/PROTOCOL.md for the full write-up.

Output reports are 1024 bytes of payload behind a report-id byte (0x00), so every write is
1025 bytes. Commands start with ``CRT\\0\\0``; image payloads are raw JPEG split across as many
reports as needed, zero-padded.
"""
from __future__ import annotations

PACKET_SIZE = 1024
REPORT_SIZE = PACKET_SIZE + 1
INPUT_REPORT_SIZE = 512
COMMAND_PREFIX = b"CRT\x00\x00"
MAX_IMAGE_BYTES = 10240  # firmware buffer per key image


def command(payload: bytes) -> bytes:
    """Wrap a command payload into one full output report."""
    report = b"\x00" + COMMAND_PREFIX + payload
    if len(report) > REPORT_SIZE:
        raise ValueError(f"command too long ({len(payload)} bytes)")
    return report.ljust(REPORT_SIZE, b"\x00")


def data_reports(data: bytes) -> list[bytes]:
    """Split raw data (a JPEG) into zero-padded output reports."""
    return [
        (b"\x00" + data[off:off + PACKET_SIZE]).ljust(REPORT_SIZE, b"\x00")
        for off in range(0, len(data), PACKET_SIZE)
    ]


def wake_screen() -> bytes:
    return command(b"DIS")


def brightness(percent: int) -> bytes:
    percent = max(0, min(100, int(percent)))
    return command(b"LIG\x00\x00" + bytes([percent]))


def clear(image_id: int = 0xFF) -> bytes:
    """Clear one image slot, or every slot with 0xFF."""
    return command(b"CLE\x00\x00\x00" + bytes([image_id & 0xFF]))


def flush() -> bytes:
    """``STP``: commit pending image writes to the panel."""
    return command(b"STP")


def heartbeat() -> bytes:
    """``CONNECT``: keep-alive; without it the firmware falls back to its idle screen."""
    return command(b"CONNECT")


def sleep_screen() -> bytes:
    return command(b"HAN")


def shutdown() -> bytes:
    """Hand the panel back to the firmware's own logo screen."""
    return command(b"CLE\x00\x00DC")


def image_header(image_id: int, length: int) -> bytes:
    """``BAT`` header announcing ``length`` bytes of JPEG for ``image_id``."""
    if not 0 < length <= 0xFFFFFFFF:
        raise ValueError("bad image length")
    return command(b"BAT" + length.to_bytes(4, "big") + bytes([image_id]))


def image(image_id: int, jpeg: bytes) -> list[bytes]:
    """All reports needed to upload one key image (header + data). Call :func:`flush` after."""
    if len(jpeg) > MAX_IMAGE_BYTES:
        raise ValueError(f"image is {len(jpeg)} bytes, the firmware accepts at most {MAX_IMAGE_BYTES}")
    return [image_header(image_id, len(jpeg)), *data_reports(jpeg)]


def background_header(length: int, x: int, y: int, width: int, height: int, selector: tuple[int, int] = (1, 0)) -> bytes:
    """``BGPIC`` header for a full-panel background (seen on N4 Pro firmware, experimental on N4)."""
    body = b"BGPIC" + length.to_bytes(4, "big")
    for value in (x, y, width, height):
        body += value.to_bytes(2, "big")
    return command(body + bytes(selector))


def parse_input(report: bytes | list[int]) -> tuple[int, int] | None:
    """Return ``(control_id, state)`` from an input report, or None for noise.

    Input reports echo the ``ACK`` header in bytes 0..8; byte 9 is the control id and byte 10 the
    state (1 = pressed, 0 = released, for controls that report both).
    """
    if len(report) < 11:
        return None
    control = report[9]
    if control == 0:
        return None
    return control, report[10]
