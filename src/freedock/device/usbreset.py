"""Software replug: ask the OS to restart the deck's USB device node.

Sometimes the N4 stays on the bus but refuses every transfer (its USB link drops under marginal
power or cabling - see docs/PROTOCOL.md). Restarting FreeDock cannot fix that; pulling the cable
does, because the port gets reset. Restarting the device node does the same without touching the
cable. Windows only lets an administrator do it, so the user sees one UAC prompt.
"""
from __future__ import annotations

import base64
import logging
import sys

from .layout import Model

log = logging.getLogger(__name__)


def instance_pattern(model: Model) -> str:
    """PnP instance-id pattern of the composite USB device (the parent of both HID interfaces)."""
    return f"USB\\VID_{model.vendor_id:04X}&PID_{model.product_id:04X}\\*"


def restart_script(model: Model) -> str:
    """PowerShell that restarts every present device node matching the model."""
    return ("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like '%s' } | "
            "ForEach-Object { pnputil.exe /restart-device $_.InstanceId }" % instance_pattern(model))


def restart(model: Model) -> bool:
    """Start the (elevated) restart. True when it was launched - the user may still decline the prompt.

    The deck disappears for a couple of seconds and comes back; the hotplug loop reconnects by itself.
    """
    if sys.platform != "win32":
        log.info("USB restart is only implemented on Windows; replug the deck")
        return False
    import ctypes
    encoded = base64.b64encode(restart_script(model).encode("utf-16-le")).decode("ascii")
    args = f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}"
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", args, None, 0)
    if rc <= 32:
        log.warning("USB restart not started (ShellExecute returned %s - prompt declined?)", rc)
        return False
    log.info("USB restart requested for %s", instance_pattern(model))
    return True
