"""
Polling engine — the role of scripts/managers/node_manager.js:
run the driver on an interval, update the status store, evaluate events,
accumulate energy, and drive the shutdown policy. Runs in a background thread.
"""
from __future__ import annotations

import logging
import threading
import time

from .energy import EnergyMeter
from .events import EventEngine
from .model import LEVEL_COMLOST, LEVEL_NORMAL, UpsStatus
from .rules import DEFAULT_EVENT_RULES
from .shutdown import ShutdownManager

log = logging.getLogger("node")


def _now_ms() -> int:
    return int(time.time() * 1000)


class NodeManager:
    def __init__(self, driver, shutdown: ShutdownManager, config, poll_ms: int = 2000) -> None:
        self.driver = driver
        self.shutdown = shutdown
        self.config = config
        self.poll_ms = poll_ms
        self.status = UpsStatus()
        self.events = EventEngine(getattr(driver, "event_rules", None) or DEFAULT_EVENT_RULES)
        self.energy = EnergyMeter(config)
        self._peak_level = LEVEL_NORMAL
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.com_lost = True

    def open_driver(self) -> bool:
        """Open the device and read identity — without starting the poll thread."""
        ok = self.driver.open()
        if not ok:
            log.error("driver %s failed to open", self.driver.id)
            return False
        now = _now_ms()
        for k, v in self.driver.product_info().items():
            self.status.set(k, v, now)
        return True

    def start(self) -> None:
        self.open_driver()
        self._thread = threading.Thread(target=self._loop, name="nodemanager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.driver.close()

    def poll_now(self) -> None:
        """Run exactly one acquisition cycle synchronously (no thread)."""
        self._poll_once()

    def _poll_once(self) -> None:
        try:
            data = self.driver.poll()
            now = _now_ms()
            for path, value in data.items():
                self.status.set(path, value, now)
            self.com_lost = False
            self.status.set("System.CommunicationLost", 0, now)

            frag = self.shutdown.evaluate(self.status)
            for path, value in frag.items():
                self.status.set(path, value, now)

            for path, value in self.energy.update(self.status).items():
                self.status.set(path, value, now)

            self._peak_level = self.events.evaluate(self.status)
        except Exception as e:  # noqa: BLE001
            self.com_lost = True
            self.status.set("System.CommunicationLost", 1, _now_ms())
            log.warning("poll error: %s", e)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._poll_once()
            self._stop.wait(self.poll_ms / 1000.0)

    def severity(self) -> int:
        if self.com_lost:
            return LEVEL_COMLOST
        s = self.status
        if any(
            s.get(p) == 1
            for p in (
                "UPS.PowerSummary.PresentStatus.ShutdownImminent",
                "UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit",
                "UPS.PowerSummary.PresentStatus.InternalFailure",
                "UPS.PowerSummary.PresentStatus.Overload",
                "Local.ShutdownReached",
            )
        ):
            return 3
        if s.get("UPS.PowerSummary.PresentStatus.ACPresent") == 0:
            return 2
        if s.get("UPS.PowerSummary.PresentStatus.Charging") == 1:
            return 0
        return 1
