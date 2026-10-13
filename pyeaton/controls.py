"""
Named UPS controls -> HID-PDC object writes.

Every control the original app offers over USB is just a SET on a writable
feature object (the recovered ups_pdc.js / hid_layer.js do nothing more). This
maps friendly names to (object path, value) so the API/UI can offer them, and
so we only advertise the controls the connected device actually implements
(i.e. that appear in its parsed report descriptor).

Values follow the HID Power Device Class usage semantics (hid_usages.js):
  AudibleAlarmControl: 1=disabled, 2=enabled, 3=muted
  Test (UPS.PowerSummary.Test): 1=quick, 2=deep, 3=abort  (model dependent)
"""
from __future__ import annotations

# name -> {path, value, label}. `value=None` means "caller supplies a number".
CONTROLS: dict[str, dict] = {
    "alarm_enable":  {"path": "UPS.PowerSummary.AudibleAlarmControl", "value": 2, "label": "Enable audible alarm"},
    "alarm_disable": {"path": "UPS.PowerSummary.AudibleAlarmControl", "value": 1, "label": "Disable audible alarm"},
    "alarm_mute":    {"path": "UPS.PowerSummary.AudibleAlarmControl", "value": 3, "label": "Mute audible alarm"},
    "test_quick":    {"path": "UPS.PowerSummary.Test", "value": 1, "label": "Start quick battery test"},
    "test_deep":     {"path": "UPS.PowerSummary.Test", "value": 2, "label": "Start deep battery test"},
    "test_abort":    {"path": "UPS.PowerSummary.Test", "value": 3, "label": "Abort battery test"},
    "delay_shutdown": {"path": "UPS.PowerSummary.DelayBeforeShutdown", "value": None, "label": "Set shutdown delay (s)"},
    "delay_startup":  {"path": "UPS.PowerSummary.DelayBeforeStartup", "value": None, "label": "Set startup delay (s)"},
    # Outlet (load-segment) control — present only on multi-outlet models.
    "outlet2_on":  {"path": "UPS.OutletSystem.Outlet[2].DelayBeforeStartup", "value": 0, "label": "Outlet 2 on"},
    "outlet2_off": {"path": "UPS.OutletSystem.Outlet[2].DelayBeforeShutdown", "value": 0, "label": "Outlet 2 off"},
    "outlet3_on":  {"path": "UPS.OutletSystem.Outlet[3].DelayBeforeStartup", "value": 0, "label": "Outlet 3 on"},
    "outlet3_off": {"path": "UPS.OutletSystem.Outlet[3].DelayBeforeShutdown", "value": 0, "label": "Outlet 3 off"},
}


def available(driver) -> list[dict]:
    """Which named controls the connected device actually supports.

    Real drivers expose `objects` (parsed from the report descriptor); we only
    list controls whose object is present and writable. The simulator advertises
    whatever it declares it can set.
    """
    objs = getattr(driver, "objects", None)
    sim_set = getattr(driver, "settable_paths", None)
    out = []
    for name, c in CONTROLS.items():
        ok = False
        if objs is not None:
            # These are known control usages; if the device exposes the object at
            # all, offer it (some descriptors mark controls non-writable even
            # though they accept a SET). A failed SET surfaces as an API error.
            ok = c["path"] in objs
        elif sim_set is not None:
            ok = c["path"] in sim_set
        if ok:
            out.append({"name": name, "label": c["label"],
                        "path": c["path"], "needsValue": c["value"] is None})
    return out


# Beeper (AudibleAlarmControl) convenience: map a friendly state to a control.
BEEPER_SET = {"on": "alarm_enable", "enabled": "alarm_enable",
              "off": "alarm_disable", "disabled": "alarm_disable",
              "mute": "alarm_mute", "muted": "alarm_mute"}


def set_beeper(driver, state: str) -> tuple[bool, str]:
    """state: on/off/mute (aliases enabled/disabled/muted)."""
    name = BEEPER_SET.get(str(state).lower())
    if not name:
        return False, f"unknown beeper state {state!r} (use on/off/mute)"
    return apply(driver, name)


def apply(driver, name: str, value=None) -> tuple[bool, str]:
    c = CONTROLS.get(name)
    if not c:
        return False, f"unknown control {name!r}"
    v = value if c["value"] is None else c["value"]
    if v is None:
        return False, "this control requires a numeric value"
    ok = driver.set(c["path"], v)
    return bool(ok), c["path"]
