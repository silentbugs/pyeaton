"""
Default event rules, ported from the eventList in scripts/drivers/ups_pdc.js
(the HID-PDC driver). Shared by the sim and usb backends since both speak the
same PDC path namespace. level: 0 info, 1 normal, 2 warning, 3 critical, 4 comlost.
"""

DEFAULT_EVENT_RULES = [
    {"obj": "System.CommunicationLost", "op": "eq", "val": 1, "level": 4},

    {"obj": "UPS.PowerSummary.PresentStatus.ShutdownImminent", "op": "eq", "val": 1, "level": 3},
    {"obj": "UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit", "op": "eq", "val": 1, "level": 3},
    {"obj": "UPS.PowerSummary.PresentStatus.InternalFailure", "op": "eq", "val": 1, "level": 3},
    {"obj": "UPS.PowerSummary.PresentStatus.Overload", "op": "eq", "val": 1, "level": 3},
    {"obj": "UPS.PowerSummary.PresentStatus.NeedReplacement", "op": "eq", "val": 1, "level": 3},

    {"obj": "UPS.PowerSummary.PresentStatus.ACPresent", "op": "eq", "val": 0, "level": 2},
    {"obj": "Local.ShutdownReached", "op": "eq", "val": 1, "level": 3},

    # Measures (var ops: notable change thresholds, for the log/graphs)
    {"obj": "UPS.PowerSummary.PercentLoad", "op": "var", "val": 5, "level": 0},
    {"obj": "UPS.PowerSummary.RemainingCapacity", "op": "var", "val": 2, "level": 0},
    {"obj": "UPS.PowerSummary.RunTimeToEmpty", "op": "var", "val": 60, "level": 0},
    {"obj": "UPS.PowerSummary.Voltage", "op": "var", "val": 2, "level": 0},
]
