"""Driver interface. Concrete drivers: SimDriver (no hardware) and UsbPdcDriver."""
from __future__ import annotations

from typing import Any


class Driver:
    """A device driver produces/consumes HID-PDC paths.

    Mirrors the shape of the original driver objects (ups_pdc.js / sim.js):
    poll() refreshes the status store; set() pushes a value to the device.
    """

    id = "base"

    def open(self) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def product_info(self) -> dict[str, Any]:
        """Return identity fields (iManufacturer, iProduct, iSerialNumber)."""
        return {}

    def poll(self) -> dict[str, Any]:
        """Return a {path: value} dict of the current device state."""
        raise NotImplementedError

    def set(self, path: str, value: Any) -> bool:
        """Write a settable object (e.g. DelayBeforeShutdown). Return success."""
        return False
