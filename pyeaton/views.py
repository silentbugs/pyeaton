"""
Shared presentation logic — the single source of truth for the "dashboard
summary". Both the web server (/api/summary) and the Python interface
(UpsClient.summary / the CLI) call build_summary(), so there is no duplicated
logic between the UI and the API.
"""
from __future__ import annotations

from typing import Any

from . import controls as _controls

SEVERITY_NAME = {0: "info", 1: "normal", 2: "warning", 3: "critical", 4: "comlost"}

# AudibleAlarmControl (the beeper): HID PDC values.
BEEPER_STATE = {1: "disabled", 2: "enabled", 3: "muted"}


def beeper_state(value) -> str:
    try:
        return BEEPER_STATE.get(int(value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _model_name(g) -> str:
    mfr, model = g("UPS.PowerSummary.iManufacturer"), g("UPS.PowerSummary.iModel")
    product = g("UPS.PowerSummary.iProduct")
    parts = [p for p in (mfr, model or product)
             if isinstance(p, str) and p.strip()]  # skip unresolved numeric indices
    return " ".join(parts).strip() or "UPS"


def _alerts(g, severity: int) -> list[dict]:
    out = []
    def add(cond, level, msg):
        if cond:
            out.append({"level": level, "message": msg})
    if severity == 4:
        add(True, 4, "Communication lost")
    add(g("UPS.PowerSummary.PresentStatus.NeedReplacement") == 1, 3, "Replace battery")
    add(g("UPS.PowerSummary.PresentStatus.ShutdownImminent") == 1, 3, "Shutdown imminent")
    add(g("UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit") == 1, 3, "Battery low")
    add(g("UPS.PowerSummary.PresentStatus.Overload") == 1, 3, "Output overload")
    add(g("UPS.PowerSummary.PresentStatus.InternalFailure") == 1, 3, "Internal failure")
    add(g("Local.ShutdownReached") == 1, 3, "Shutdown criteria reached")
    add(g("UPS.PowerSummary.PresentStatus.ACPresent") == 0, 2, "On battery")
    return out


def _state_label(g, severity: int) -> tuple[str, str]:
    if severity == 4:
        return "Communication lost", ""
    ac = g("UPS.PowerSummary.PresentStatus.ACPresent")
    charging = g("UPS.PowerSummary.PresentStatus.Charging")
    detail = "shutdown criteria met" if g("Local.ShutdownReached") == 1 else ""
    if ac == 0:
        return "On battery", detail
    if charging == 1:
        return "Online — charging", detail
    return "Online", detail


def build_summary(node, config) -> dict[str, Any]:
    """Build the dashboard-equivalent summary from a NodeManager + Config."""
    s = node.status
    g = s.get
    severity = node.severity()

    beeper_val = g("UPS.PowerSummary.AudibleAlarmControl")
    try:
        beeper_supported = any(
            c["name"].startswith("alarm") for c in _controls.available(node.driver))
    except Exception:  # noqa: BLE001
        beeper_supported = beeper_val is not None

    temp = next((g(k) for k in s.values if k.endswith("Temperature")), None)

    return {
        "model": _model_name(g),
        "manufacturer": g("UPS.PowerSummary.iManufacturer"),
        "serial": g("UPS.PowerSummary.iSerialNumber"),
        "severity": severity,
        "severityName": SEVERITY_NAME.get(severity, "normal"),
        "state": _state_label(g, severity)[0],
        "stateDetail": _state_label(g, severity)[1],
        "alerts": _alerts(g, severity),
        "battery": {
            "charge": g("UPS.PowerSummary.RemainingCapacity"),
            "fullChargeCapacity": g("UPS.PowerSummary.FullChargeCapacity"),
            "designCapacity": g("UPS.PowerSummary.DesignCapacity"),
            "runtimeSeconds": g("UPS.PowerSummary.RunTimeToEmpty"),
            "voltage": g("UPS.PowerSummary.Voltage"),
            "needsReplacement": g("UPS.PowerSummary.PresentStatus.NeedReplacement") == 1,
            "charging": g("UPS.PowerSummary.PresentStatus.Charging") == 1,
        },
        "power": {
            "loadPercent": g("UPS.PowerSummary.PercentLoad"),
            "inputVoltage": g("UPS.PowerConverter.Input[1].Voltage") or g("UPS.PowerSummary.Voltage"),
            "outputVoltage": g("UPS.PowerConverter.Output.Voltage"),
            "outputFrequency": g("UPS.PowerConverter.Output.Frequency"),
            "activePower": g("UPS.PowerConverter.Output.ActivePower"),
        },
        "temperature": temp,
        "beeper": {
            "supported": bool(beeper_supported),
            "value": beeper_val,
            "state": beeper_state(beeper_val),
        },
        "energy": {
            "kWh": g("System.Energy.kWh"),
            "cost": g("System.Energy.cost"),
            "currency": g("System.Energy.currency"),
        },
        "lastDate": s.last_date,
        "data": s.snapshot(),
    }
