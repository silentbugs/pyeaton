"""
Simulator driver — lets you run the whole app with no UPS attached.

The original ships scripts/drivers/sim.js driven by recorded .sim files.
This is a live physical model instead: a battery that discharges on mains
loss and recharges on restore, with derived PercentLoad / RunTimeToEmpty /
status flags. It is enough to exercise the dashboard, the shutdown policy,
and the API exactly as a real device would.
"""
from __future__ import annotations

import time
from typing import Any

from .driver_base import Driver


class SimDriver(Driver):
    id = "sim"

    def __init__(
        self,
        *,
        capacity_wh: float = 45.0,   # usable battery energy
        load_w: float = 60.0,        # connected load
        nominal_v: float = 230.0,
        full_charge_min: float = 30.0,
    ) -> None:
        self.capacity_wh = capacity_wh
        self.load_w = load_w
        self.nominal_v = nominal_v
        self.full_charge_min = full_charge_min

        self.ac_present = True       # mains OK
        self.charge_pct = 100.0      # RemainingCapacity (%)
        self.delay_before_shutdown = -1  # -1 = idle, >=0 = countdown armed
        self.delay_before_startup = -1
        self._last = time.monotonic()
        # Arbitrary writable PDC objects the sim accepts (controls land here and
        # are echoed back in poll()), so named controls work without hardware.
        self.extra: dict[str, float] = {
            "UPS.PowerSummary.AudibleAlarmControl": 2,  # 2=enabled
            "UPS.PowerSummary.Test": 0,
        }

    # Paths controls.available() checks against for the simulator.
    settable_paths = {
        "UPS.PowerSummary.DelayBeforeShutdown",
        "UPS.PowerSummary.DelayBeforeStartup",
        "UPS.PowerSummary.AudibleAlarmControl",
        "UPS.PowerSummary.Test",
    }

    # --- controls the UI / tests can toggle -------------------------------
    def set_mains(self, present: bool) -> None:
        self.ac_present = present

    def product_info(self) -> dict[str, Any]:
        return {
            "UPS.PowerSummary.iManufacturer": "Eaton",
            "UPS.PowerSummary.iProduct": "5E 850i (simulated)",
            "UPS.PowerSummary.iModel": "5E 850i",
            "UPS.PowerSummary.iSerialNumber": "SIM00000001",
            "UPS.PowerSummary.DesignCapacity": 100,
            "UPS.PowerSummary.FullChargeCapacity": 100,
        }

    def open(self) -> bool:
        return True

    # --- physics ----------------------------------------------------------
    def _advance(self) -> None:
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        if dt <= 0:
            return

        if self.ac_present:
            # Recharge toward 100%.
            rate = 100.0 / (self.full_charge_min * 60.0)  # %/s
            self.charge_pct = min(100.0, self.charge_pct + rate * dt)
        else:
            # Discharge: usable Wh depletes at load_w.
            drain_wh = self.load_w * (dt / 3600.0)
            drained_pct = 100.0 * drain_wh / self.capacity_wh
            self.charge_pct = max(0.0, self.charge_pct - drained_pct)

        # Count down an armed shutdown timer.
        if self.delay_before_shutdown >= 0:
            self.delay_before_shutdown = max(0, self.delay_before_shutdown - dt)

    def poll(self) -> dict[str, Any]:
        self._advance()
        pct = round(self.charge_pct, 1)
        # Runtime (s) left at current load given remaining energy.
        remaining_wh = self.capacity_wh * pct / 100.0
        rtte = int(3600.0 * remaining_wh / self.load_w) if self.load_w > 0 else 0
        load_pct = int(round(100.0 * self.load_w / max(self.load_w, 90.0)))

        below_limit = 1 if pct <= 20.0 else 0
        shutdown_imminent = 1 if (not self.ac_present and (pct <= 5.0 or rtte <= 120)) else 0

        return {
            "UPS.PowerSummary.PresentStatus.ACPresent": 1 if self.ac_present else 0,
            "UPS.PowerSummary.PresentStatus.Charging": 1 if (self.ac_present and pct < 100) else 0,
            "UPS.PowerSummary.PresentStatus.Discharging": 0 if self.ac_present else 1,
            "UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit": below_limit,
            "UPS.PowerSummary.PresentStatus.ShutdownImminent": shutdown_imminent,
            "UPS.PowerSummary.PresentStatus.Good": 1,
            "UPS.PowerSummary.PresentStatus.NeedReplacement": 0,
            "UPS.PowerSummary.PresentStatus.InternalFailure": 0,
            "UPS.PowerSummary.PresentStatus.Overload": 0,
            "UPS.PowerSummary.PercentLoad": load_pct,
            "UPS.PowerSummary.RemainingCapacity": pct,
            "UPS.PowerSummary.RunTimeToEmpty": rtte if not self.ac_present else rtte,
            "UPS.PowerSummary.Voltage": self.nominal_v if self.ac_present else 0,
            "UPS.PowerSummary.DelayBeforeShutdown": int(self.delay_before_shutdown),
            "UPS.PowerSummary.DelayBeforeStartup": int(self.delay_before_startup),
            "UPS.PowerConverter.Input[1].Voltage": self.nominal_v if self.ac_present else 0,
            "UPS.PowerConverter.Output.Voltage": self.nominal_v,
            "UPS.PowerConverter.Output.Frequency": 50.0,
            "UPS.PowerConverter.Output.ActivePower": int(self.load_w),
            **{k: v for k, v in self.extra.items()},
        }

    def set(self, path: str, value: Any) -> bool:
        if path == "UPS.PowerSummary.DelayBeforeShutdown":
            self.delay_before_shutdown = float(value)
            return True
        if path == "UPS.PowerSummary.DelayBeforeStartup":
            self.delay_before_startup = float(value)
            return True
        if path in self.settable_paths or path in self.extra:
            self.extra[path] = float(value)
            return True
        return False
