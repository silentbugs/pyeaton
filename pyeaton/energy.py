"""
Energy accumulator, ported from NodeManager.computeEnergyConsumption
(node_manager.js). Energy is stored as accumulated watt-seconds in
config.energy.consumption; we expose kWh and a cost figure, and reset on the
configured period (never|daily|weekly|monthly).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta


class EnergyMeter:
    def __init__(self, config) -> None:
        self.cfg = config
        self._last_date: float | None = None
        self._last_save = 0.0

    def update(self, status) -> dict:
        e = self.cfg.energy
        now = time.time()

        # Period reset.
        if e.resetPeriod != "never" and e.resetDate and now >= e.resetDate:
            e.consumption = 0.0
        if e.resetPeriod != "never" and not e.resetDate:
            e.resetDate = self._next_reset(e.resetPeriod)

        active_power = status.get("UPS.PowerConverter.Output.ActivePower")
        if not active_power:
            load = status.get("UPS.PowerSummary.PercentLoad")
            apparent = status.get("UPS.Flow[4].ConfigApparentPower")
            if e.powerFactor and load is not None and apparent:
                active_power = round(load * 0.01 * apparent * e.powerFactor)
            else:
                return self._view()

        last = self._last_date or now
        if now - last > 180:       # came back from sleep/com-loss; don't integrate the gap
            last = now
        e.consumption = (e.consumption or 0.0) + active_power * (now - last)
        self._last_date = now

        if now - self._last_save > 60:
            self.cfg.save()
            self._last_save = now
        return self._view()

    def _view(self) -> dict:
        e = self.cfg.energy
        kwh = (e.consumption or 0.0) / 3_600_000.0  # watt-seconds -> kWh
        return {
            "System.Energy.kWh": round(kwh, 4),
            "System.Energy.cost": round(kwh * (e.cost or 0.0), 4),
            "System.Energy.currency": e.currency,
            "System.Energy.resetPeriod": e.resetPeriod,
            "System.Energy.nextReset": int((e.resetDate or 0) * 1000),
        }

    @staticmethod
    def _next_reset(period: str) -> float:
        d = datetime.now()
        base = d.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "daily":
            return (base + timedelta(days=1)).timestamp()
        if period == "weekly":
            return (base + timedelta(days=7 - d.weekday())).timestamp()
        if period == "monthly":
            nxt = (base.replace(day=1) + timedelta(days=32)).replace(day=1)
            return nxt.timestamp()
        return 0.0
