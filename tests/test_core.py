"""Stdlib-only tests (run: python -m unittest -v) — no pip deps needed."""
import time
import unittest

from pyeaton.config import Config
from pyeaton.driver_sim import SimDriver
from pyeaton.energy import EnergyMeter
from pyeaton.events import EventEngine
from pyeaton.model import UpsStatus
from pyeaton.nodemanager import NodeManager
from pyeaton.nut_server import to_nut_vars
from pyeaton.rules import DEFAULT_EVENT_RULES
from pyeaton.shutdown import ShutdownConfig, ShutdownManager


def _status(d):
    s = UpsStatus()
    for k, v in d.items():
        s.set(k, v, 1)
    return s


class TestSim(unittest.TestCase):
    def test_discharge_and_recharge(self):
        d = SimDriver(capacity_wh=10, load_w=600)  # ~1 min full runtime
        d.set_mains(False)
        d._last -= 5  # pretend 5s elapsed
        snap = d.poll()
        self.assertEqual(snap["UPS.PowerSummary.PresentStatus.ACPresent"], 0)
        self.assertEqual(snap["UPS.PowerSummary.PresentStatus.Discharging"], 1)
        self.assertLess(snap["UPS.PowerSummary.RemainingCapacity"], 100)

    def test_set_delay(self):
        d = SimDriver()
        self.assertTrue(d.set("UPS.PowerSummary.DelayBeforeShutdown", 120))
        # Once armed the delay counts down (like the real device), so just
        # confirm it is set and counting, not idle (-1).
        self.assertGreaterEqual(d.poll()["UPS.PowerSummary.DelayBeforeShutdown"], 118)


class TestShutdown(unittest.TestCase):
    def test_policy_fires_but_does_not_execute(self):
        d = SimDriver()
        sd = ShutdownManager(ShutdownConfig(run_time_to_empty_limit=300, execute=False), d)
        frag = sd.evaluate(_status({
            "UPS.PowerSummary.PresentStatus.ACPresent": 0,
            "UPS.PowerSummary.RunTimeToEmpty": 100,
        }))
        self.assertEqual(frag["Local.ShutdownReached"], 1)
        self.assertTrue(sd.triggered)

    def test_rearm_on_mains(self):
        d = SimDriver()
        sd = ShutdownManager(ShutdownConfig(run_time_to_empty_limit=300), d)
        sd.evaluate(_status({"UPS.PowerSummary.PresentStatus.ACPresent": 0,
                             "UPS.PowerSummary.RunTimeToEmpty": 10}))
        self.assertTrue(sd.triggered)
        sd.evaluate(_status({"UPS.PowerSummary.PresentStatus.ACPresent": 1,
                             "UPS.PowerSummary.RunTimeToEmpty": 1800}))
        self.assertFalse(sd.triggered)


class TestEvents(unittest.TestCase):
    def test_power_loss_logs_and_levels(self):
        e = EventEngine(DEFAULT_EVENT_RULES)
        e.evaluate(_status({"UPS.PowerSummary.PresentStatus.ACPresent": 1}))
        lvl = e.evaluate(_status({"UPS.PowerSummary.PresentStatus.ACPresent": 0}))
        self.assertEqual(lvl, 2)  # warning
        self.assertTrue(any("battery" in ev["message"].lower() for ev in e.recent()))


class TestEnergy(unittest.TestCase):
    def test_accumulates_kwh(self):
        cfg = Config()
        cfg.energy.cost = 0.5
        m = EnergyMeter(cfg)
        s = _status({"UPS.PowerConverter.Output.ActivePower": 3_600_000})  # 3.6 MW -> 1 kWh/s
        m.update(s)
        m._last_date -= 1  # pretend 1s passed
        view = m.update(s)
        self.assertGreater(view["System.Energy.kWh"], 0.5)


class TestNutMapping(unittest.TestCase):
    def test_status_flags(self):
        v = to_nut_vars(_status({
            "UPS.PowerSummary.PresentStatus.ACPresent": 0,
            "UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit": 1,
            "UPS.PowerSummary.RemainingCapacity": 17,
            "UPS.PowerSummary.RunTimeToEmpty": 240,
        }))
        self.assertIn("OB", v["ups.status"].split())
        self.assertIn("LB", v["ups.status"].split())
        self.assertEqual(v["battery.charge"], "17")
        self.assertEqual(v["battery.runtime"], "240")


class TestHidParser(unittest.TestCase):
    """Synthetic PDC report descriptor: one read measure (PercentLoad) and one
    writable control (AudibleAlarmControl) in feature report 1. Proves the
    parser + extract/inject (shared by the --usb and --hid drivers) handle both
    reading values AND writing controls like alarm on/off — no hardware."""

    DESC = bytes([
        0x05, 0x84,        # Usage Page (Power Device)
        0x09, 0x04,        # Usage (UPS)
        0xA1, 0x01,        # Collection (Application)
        0x85, 0x01,        #   Report ID (1)
        0x09, 0x24,        #   Usage (PowerSummary)
        0xA1, 0x00,        #   Collection (Physical)
        0x75, 0x08,        #     Report Size (8)
        0x95, 0x01,        #     Report Count (1)
        0x15, 0x00,        #     Logical Min (0)
        0x25, 0x64,        #     Logical Max (100)
        0x09, 0x35,        #     Usage (PercentLoad)
        0xB1, 0x02,        #     Feature (Data,Var,Abs)  -> readable value
        0x25, 0x03,        #     Logical Max (3)
        0x09, 0x5A,        #     Usage (AudibleAlarmControl)
        0xB1, 0x02,        #     Feature (Data,Var,Abs)  -> writable control
        0xC0,              #   End Collection
        0xC0,              # End Collection
    ])

    def test_parse_and_roundtrip(self):
        from pyeaton import hidparser as H
        objects, reports, maxlen = H.parse_report_descriptor(self.DESC)
        load = "UPS.PowerSummary.PercentLoad"
        alarm = "UPS.PowerSummary.AudibleAlarmControl"
        self.assertIn(load, objects)
        self.assertIn(alarm, objects)
        self.assertTrue(objects[alarm]["writable"])   # control is settable
        self.assertEqual(maxlen, 3)                    # [id][load][alarm]

        # A feature report payload: report id 1, load=42, alarm=0.
        content = bytearray([1, 42, 0])
        self.assertEqual(H.extract(content, objects[load]), 42)
        self.assertEqual(H.extract(content, objects[alarm]), 0)

        # Turn the audible alarm on (write control) and read it back.
        H.inject(content, objects[alarm], 1)
        self.assertEqual(content[2], 1)
        self.assertEqual(H.extract(content, objects[alarm]), 1)


class TestNodeManager(unittest.TestCase):
    def test_loop_populates_status(self):
        d = SimDriver()
        cfg = Config()
        sd = ShutdownManager(ShutdownConfig(), d)
        nm = NodeManager(d, sd, cfg, poll_ms=100)
        nm.start()
        time.sleep(0.4)
        try:
            self.assertIn("UPS.PowerSummary.RemainingCapacity", nm.status.values)
            self.assertEqual(nm.severity() in (0, 1), True)
        finally:
            nm.stop()


if __name__ == "__main__":
    unittest.main()
