"""
UpsClient — a no-UI Python interface to the UPS.

It drives the device directly (same driver + NodeManager + shared view/control
logic the web dashboard uses), so you get the exact dashboard-equivalent
summary and beeper control from plain Python, no HTTP server and no HTML:

    from pyeaton.client import UpsClient

    with UpsClient("usb") as ups:            # or "sim", "hid"
        print(ups.summary())                 # full dashboard summary (status, alerts, battery, beeper)
        print(ups.beeper())                  # {'supported': True, 'value': 1, 'state': 'disabled'}
        ups.set_beeper("off")                # enable/disable/mute the beeper: on | off | mute

Backends: "sim" (no hardware), "usb" (libusb/pyusb), "hid" (hidapi).
"""
from __future__ import annotations

from typing import Any

from . import controls as _controls
from . import views as _views
from .config import Config
from .nodemanager import NodeManager
from .shutdown import ShutdownConfig, ShutdownManager


def _make_driver(backend: str, *, libusb0_dll=None, vid=None, pid=0xFFFF, load=60.0):
    backend = backend.lower()
    if backend == "sim":
        from .driver_sim import SimDriver
        return SimDriver(load_w=load)
    if backend == "usb":
        from .driver_usb import UsbPdcDriver
        return UsbPdcDriver(vendor_id=vid, product_id=pid, libusb0_dll=libusb0_dll)
    if backend == "hid":
        from .driver_hid import HidPdcDriver
        return HidPdcDriver(vendor_id=vid or 0x0463,
                            product_id=(0 if pid in (0, 0xFFFF) else pid))
    raise ValueError(f"unknown backend {backend!r} (use sim/usb/hid)")


class UpsClient:
    """Open a UPS, read its dashboard summary, and control the beeper — no UI.

    Methods: summary(), status(), beeper(), set_beeper(state), controls(),
    refresh(), close(). Usable as a context manager.
    """

    def __init__(self, backend: str = "sim", *, config: Config | None = None,
                 libusb0_dll: str | None = None, vid: int | None = None,
                 pid: int = 0xFFFF, load: float = 60.0) -> None:
        self.config = config or Config()
        self.driver = _make_driver(backend, libusb0_dll=libusb0_dll, vid=vid, pid=pid, load=load)
        ss = self.config.shutdownSettings
        sd = ShutdownManager(ShutdownConfig(
            run_time_to_empty_limit=ss.runTimeToEmptyLimit,
            shutdown_duration=ss.shutdownDuration,
            shutdown_type=ss.shutdownType, execute=ss.execute), self.driver)
        self.node = NodeManager(self.driver, sd, self.config)
        self._open = False

    # --- lifecycle --------------------------------------------------------
    def connect(self) -> "UpsClient":
        if not self.node.open_driver():
            raise RuntimeError("could not open the UPS (is another program holding it?)")
        self.node.poll_now()
        self._open = True
        return self

    def close(self) -> None:
        if self._open:
            self.driver.close()
            self._open = False

    def __enter__(self) -> "UpsClient":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def refresh(self) -> "UpsClient":
        """Re-read the device (one acquisition cycle)."""
        self.node.poll_now()
        return self

    # --- reads ------------------------------------------------------------
    def summary(self, refresh: bool = True) -> dict[str, Any]:
        """The full dashboard-equivalent summary (status, severity, alerts,
        battery health, power, temperature, beeper, energy, raw data)."""
        if refresh:
            self.node.poll_now()
        return _views.build_summary(self.node, self.config)

    def status(self, refresh: bool = True) -> dict[str, Any]:
        """Raw {path: value} snapshot of every acquired object."""
        if refresh:
            self.node.poll_now()
        return self.node.status.snapshot()

    def controls(self) -> list[dict]:
        """Named controls this device supports (alarm/test/delays/outlets)."""
        return _controls.available(self.driver)

    # --- beeper (AudibleAlarmControl) ------------------------------------
    def beeper(self, refresh: bool = True) -> dict[str, Any]:
        """Current beeper status: {'supported', 'value', 'state'} where state
        is enabled | disabled | muted | unknown."""
        return self.summary(refresh=refresh)["beeper"]

    def set_beeper(self, state: str) -> dict[str, Any]:
        """Set the beeper: 'on' | 'off' | 'mute' (aliases enabled/disabled/muted).
        Returns the new beeper status."""
        ok, detail = _controls.set_beeper(self.driver, state)
        if not ok:
            raise RuntimeError(f"could not set beeper: {detail}")
        return self.beeper(refresh=True)


def connect(backend: str = "sim", **kw) -> UpsClient:
    """Shortcut: `ups = pyeaton.client.connect('usb'); ups.summary()`."""
    return UpsClient(backend, **kw).connect()
