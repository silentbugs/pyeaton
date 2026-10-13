"""
HID Power Device Class (PDC) object model.

This mirrors the path namespace used by the original Eaton UPS Companion
(recovered from scripts/drivers/ups_pdc.js and scripts/libs/hid_usages.js):
dotted paths like `UPS.PowerSummary.RemainingCapacity`. Keeping the exact
paths means our JSON data contract matches the original web client.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Event severity levels, as used by NodeManager in the original app.
LEVEL_INFO = 0
LEVEL_NORMAL = 1
LEVEL_WARNING = 2
LEVEL_CRITICAL = 3
LEVEL_COMLOST = 4

# The "summary" objects the original getSummary() polls every cycle.
SUMMARY_PATHS = [
    "UPS.PowerSummary.PresentStatus.ACPresent",
    "UPS.PowerSummary.PresentStatus.Charging",
    "UPS.PowerSummary.PresentStatus.Discharging",
    "UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit",
    "UPS.PowerSummary.PresentStatus.ShutdownImminent",
    "UPS.PowerSummary.PresentStatus.Good",
    "UPS.PowerSummary.PresentStatus.InternalFailure",
    "UPS.PowerSummary.PresentStatus.Overload",
    "UPS.PowerSummary.PercentLoad",
    "UPS.PowerSummary.RemainingCapacity",
    "UPS.PowerSummary.RunTimeToEmpty",
    "UPS.PowerSummary.Voltage",
    "UPS.PowerSummary.DelayBeforeShutdown",
    "UPS.PowerSummary.DelayBeforeStartup",
    "UPS.PowerConverter.Input[1].Voltage",
    "UPS.PowerConverter.Output.Voltage",
    "UPS.PowerConverter.Output.ActivePower",
]


@dataclass
class UpsStatus:
    """Live status store: a flat {path: value} map, like the JS node.status."""

    values: dict[str, Any] = field(default_factory=dict)
    last_date: int = 0  # ms timestamp of last change, matches UIManager.lastDate

    def get(self, path: str, default: Any = None) -> Any:
        return self.values.get(path, default)

    def set(self, path: str, value: Any, now_ms: int) -> bool:
        """Set a value; return True if it changed."""
        if self.values.get(path) != value:
            self.values[path] = value
            self.last_date = now_ms
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        return dict(self.values)
