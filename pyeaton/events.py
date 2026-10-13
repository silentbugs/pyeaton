"""
Event engine + log, ported from the eventList mechanism in
scripts/managers/node_manager.js and the per-driver eventList tables in
ups_pdc.js / sim.js.

Each rule is {obj, op, val, level[, min, max]}. Ops:
  eq  -> fire at `level` when value == val; clear (level 1) when it leaves
  gt/lt -> fire when value >/< val
  var -> "measure changed by >= val" (used for logging notable swings)
When a rule fires it appends to a ring-buffer log and bumps severity.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Event:
    ts: float
    path: str
    value: object
    level: int
    message: str


# A small human-readable label map for common paths (extends as needed).
_LABELS = {
    "UPS.PowerSummary.PresentStatus.ACPresent": ("On battery", "Mains restored"),
    "UPS.PowerSummary.PresentStatus.ShutdownImminent": ("Shutdown imminent", None),
    "UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit": ("Battery low", "Battery OK"),
    "UPS.PowerSummary.PresentStatus.Overload": ("Output overload", "Overload cleared"),
    "UPS.PowerSummary.PresentStatus.InternalFailure": ("Internal failure", None),
    "UPS.PowerSummary.PresentStatus.NeedReplacement": ("Battery needs replacement", None),
    "Local.ShutdownReached": ("Shutdown criteria reached", None),
    "System.CommunicationLost": ("Communication lost", "Communication restored"),
}


def _message(path: str, value, level: int) -> str:
    on, off = _LABELS.get(path, (None, None))
    if path == "UPS.PowerSummary.PresentStatus.ACPresent":
        return off if value == 1 else on
    if on and value not in (0, False):
        return on
    if off and value in (0, False):
        return off
    return f"{path} = {value}"


class EventEngine:
    def __init__(self, rules: list[dict], maxlen: int = 500) -> None:
        self.rules = rules or []
        self.log: deque[Event] = deque(maxlen=maxlen)
        self._last: dict[str, object] = {}

    def evaluate(self, status) -> int:
        """Run all rules against the status; return the peak event level."""
        peak = 0
        for rule in self.rules:
            path = rule["obj"]
            if path not in status.values:
                continue
            value = status.get(path)
            last = self._last.get(path)
            level = rule.get("level", 0)
            op = rule.get("op", "eq")
            fired = False
            if op == "eq":
                fired = value == rule["val"]
            elif op == "gt":
                fired = _num(value) > rule["val"]
            elif op == "lt":
                fired = _num(value) < rule["val"]
            elif op == "var":
                if last is not None and abs(_num(value) - _num(last)) >= rule["val"]:
                    fired = True

            if callable(level):
                try:
                    level = level(status)
                except Exception:
                    level = 1

            changed = value != last
            if fired and changed and op != "var" and level and level > peak:
                peak = level
            # Log state transitions that matter (not noisy measures).
            if op in ("eq", "gt", "lt") and changed and level:
                if fired:
                    self.log.append(Event(time.time(), path, value, level, _message(path, value, level)))
                elif last is not None:
                    self.log.append(Event(time.time(), path, value, 1, _message(path, value, 1)))
            self._last[path] = value
        return peak

    def recent(self, limit: int = 100) -> list[dict]:
        items = list(self.log)[-limit:][::-1]
        return [
            {"ts": int(e.ts * 1000), "path": e.path, "value": e.value,
             "level": e.level, "message": e.message}
            for e in items
        ]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
