"""N4Device / DeviceWatcher lifecycle against a fake HID backend (no hardware)."""
import threading
import time

import pytest

from freedock.device import n4, protocol
from freedock.device.layout import N4
from freedock.device.n4 import DeviceInfo, DeviceWatcher, N4Device

INFO = DeviceInfo(N4, b"fake-path", "TEST")


class FakeHid:
    """Stands in for ``hid.device``: records writes, can be told to fail."""

    instances: list["FakeHid"] = []

    def __init__(self):
        self.writes: list[bytes] = []
        self.closed = 0
        self.fail_writes = False
        self.fail_reads = False
        self.reading = threading.Event()
        self.closed_while_reading = False
        FakeHid.instances.append(self)

    def open_path(self, path):
        pass

    def write(self, report):
        if self.fail_writes:
            return -1
        self.writes.append(bytes(report))
        return len(report)

    def read(self, size, timeout_ms):
        if self.fail_reads:
            raise OSError("read error")
        self.reading.set()
        time.sleep(timeout_ms / 1000 / 5)
        self.reading.clear()
        return []

    def get_feature_report(self, *a):
        raise OSError

    get_input_report = get_feature_report

    def error(self):
        return "The device is not connected." if self.fail_writes else "Success"

    def close(self):
        if self.reading.is_set():
            self.closed_while_reading = True
        self.closed += 1


@pytest.fixture(autouse=True)
def fake_hid(monkeypatch):
    FakeHid.instances.clear()
    monkeypatch.setattr(n4.hid, "device", FakeHid)
    yield


def names(writes):
    """Command names of CRT reports, '.' for raw data."""
    out = []
    for w in writes:
        out.append(w[6:9].decode("ascii", "replace") if w[1:4] == b"CRT" else ".")
    return out


def test_clean_close_hands_the_panel_back_and_releases_the_handle():
    dev = N4Device(INFO, lambda e: None, lambda: None)
    hidd = FakeHid.instances[-1]
    dev.close()
    assert hidd.closed == 1 and not hidd.closed_while_reading
    assert names(hidd.writes)[-3:] == ["CLE", "STP", "CLE"]          # clear, commit, hand back (CLE..DC)
    assert hidd.writes[-1] == protocol.shutdown()
    dev.close()                                                       # idempotent
    assert hidd.closed == 1


def test_a_lost_device_still_gets_its_handle_closed():
    lost = threading.Event()
    dev = N4Device(INFO, lambda e: None, lost.set)
    hidd = FakeHid.instances[-1]
    hidd.fail_reads = True
    assert lost.wait(2)
    before = len(hidd.writes)
    dev.close(hand_back=False)
    assert hidd.closed == 1                       # used to stay open forever (close() returned early)
    assert len(hidd.writes) == before             # and nothing is written to a dead device


def test_failed_handshake_does_not_leak_the_handle(monkeypatch):
    class Refusing(FakeHid):
        def __init__(self):
            super().__init__()
            self.fail_writes = True

    monkeypatch.setattr(n4.hid, "device", Refusing)
    with pytest.raises(OSError, match="short write.*not connected"):
        N4Device(INFO, lambda e: None, lambda: None)
    assert FakeHid.instances[-1].closed == 1


def test_hand_back_is_never_sent_in_the_middle_of_an_image():
    dev = N4Device(INFO, lambda e: None, lambda: None)
    hidd = FakeHid.instances[-1]
    handshake = len(hidd.writes)                                      # CONNECT, blank resync reports...
    slow = hidd.write

    def slow_write(report):
        time.sleep(0.01)
        return slow(report)

    hidd.write = slow_write
    for image_id in N4.key_image_ids:
        dev.set_image(image_id, b"\xff\xd8" + b"x" * 5000)           # 5 data reports each
    time.sleep(0.03)                                                  # the writer is now mid-upload
    dev.close()
    seq = names(hidd.writes[handshake:])
    # every BAT header is followed by all of its data reports before any other command
    i = 0
    while i < len(seq):
        if seq[i] == "BAT":
            assert seq[i + 1:i + 6] == ["."] * 5, f"image interrupted at {i}: {seq[i:i + 8]}"
            i += 6
        else:
            assert seq[i] != ".", f"stray data report at {i}"
            i += 1
    assert hidd.writes[-1] == protocol.shutdown()


def test_watcher_reports_a_stuck_device_once_and_recovers(monkeypatch):
    monkeypatch.setattr(n4, "HOTPLUG_SECONDS", 0.01)
    monkeypatch.setattr(n4, "enumerate_devices", lambda: [INFO])
    refuse = {"on": True}

    class Flaky(FakeHid):
        def __init__(self):
            super().__init__()
            self.fail_writes = refuse["on"]

    monkeypatch.setattr(n4.hid, "device", Flaky)
    stuck, connected = [], threading.Event()
    w = DeviceWatcher(lambda d: connected.set(), lambda: None, lambda e: None,
                      on_stuck=lambda info, why: stuck.append(why))
    w.start()
    deadline = time.time() + 2
    while len(FakeHid.instances) < 6 and time.time() < deadline:
        time.sleep(0.01)
    assert len(stuck) == 1 and "short write" in stuck[0] and w.stuck == INFO
    assert all(h.closed == 1 for h in FakeHid.instances)             # no handle left behind per retry
    refuse["on"] = False
    assert connected.wait(2) and w.stuck is None
    w.stop()
    assert FakeHid.instances[-1].closed == 1
