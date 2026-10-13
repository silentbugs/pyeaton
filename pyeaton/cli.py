"""
pyeaton command-line interface — the dashboard, with no UI.

  python -m pyeaton.cli summary [--usb|--sim|--hid]     # full summary (status, alerts, battery, beeper)
  python -m pyeaton.cli beeper                           # show beeper status
  python -m pyeaton.cli beeper on|off|mute               # enable / disable / mute the beeper
  python -m pyeaton.cli help                             # what this + the Python API expose

Backend defaults to --usb; use --sim for no hardware. On Windows the bundled
64-bit libusb0.dll is auto-loaded (pass --libusb0-dll to override).
"""
from __future__ import annotations

import argparse
import json
import sys

from .client import UpsClient


def _add_backend_args(p):
    g = p.add_mutually_exclusive_group()
    g.add_argument("--usb", action="store_true", help="real UPS via libusb (default)")
    g.add_argument("--sim", action="store_true", help="simulator, no hardware")
    g.add_argument("--hid", action="store_true", help="real UPS via hidapi")
    p.add_argument("--libusb0-dll", default=None)
    p.add_argument("--vid", type=lambda x: int(x, 0), default=None)
    p.add_argument("--pid", type=lambda x: int(x, 0), default=0xFFFF)
    p.add_argument("--json", action="store_true", help="machine-readable JSON output")


def _backend(args) -> str:
    return "sim" if args.sim else "hid" if args.hid else "usb"


def _client(args) -> UpsClient:
    return UpsClient(_backend(args), libusb0_dll=args.libusb0_dll,
                     vid=args.vid, pid=args.pid).connect()


def _print_summary(s: dict) -> None:
    b, p, bat = s["beeper"], s["power"], s["battery"]
    rt = bat["runtimeSeconds"]
    rt_s = f"{rt//60}m {rt%60}s" if isinstance(rt, (int, float)) else "–"
    print(f"{s['model']}   [{s['severityName'].upper()}] {s['state']}"
          + (f"  ({s['stateDetail']})" if s["stateDetail"] else ""))
    if s["alerts"]:
        for a in s["alerts"]:
            print(f"  ! {a['message']}")
    print(f"  Battery : {bat['charge']}%  (full {bat['fullChargeCapacity']} / "
          f"design {bat['designCapacity']})   runtime {rt_s}   "
          f"{'NEEDS REPLACEMENT' if bat['needsReplacement'] else 'ok'}")
    print(f"  Power   : load {p['loadPercent']}%   in {p['inputVoltage']}V   "
          f"out {p['outputVoltage']}V {p['outputFrequency']}Hz   {p['activePower']}W")
    if s["temperature"] is not None:
        print(f"  Temp    : {s['temperature']} °C")
    print(f"  Beeper  : {b['state']}" + ("" if b["supported"] else " (not supported)"))
    e = s["energy"]
    if e.get("kWh") is not None:
        print(f"  Energy  : {e['kWh']} kWh   cost {e['cost']} {e['currency']}")


def cmd_summary(args) -> int:
    with _client(args) as ups:
        s = ups.summary()
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        _print_summary(s)
    return 0


def cmd_beeper(args) -> int:
    with _client(args) as ups:
        if args.state:
            res = ups.set_beeper(args.state)
        else:
            res = ups.beeper()
    print(json.dumps(res) if args.json else
          f"beeper: {res['state']}" + ("" if res["supported"] else " (not supported)"))
    return 0


HELP_TEXT = """\
pyeaton — view and control your Eaton UPS without the web UI.

CLI:
  python -m pyeaton.cli summary        full dashboard summary (status, alerts,
                                       battery health, power, temperature, beeper, energy)
  python -m pyeaton.cli beeper         show the beeper (audible alarm) status
  python -m pyeaton.cli beeper off     set the beeper: on | off | mute
  add --sim for no hardware, --json for machine-readable output.

Python API (same logic the web dashboard uses):
  from pyeaton.client import UpsClient
  with UpsClient("usb") as ups:        # or "sim" / "hid"
      ups.summary()                    # dict: dashboard-equivalent summary
      ups.beeper()                     # dict: {'supported','value','state'}
      ups.set_beeper("off")            # enable/disable/mute -> new beeper status
      ups.status()                     # raw {path: value} of every object
      ups.controls()                   # named controls the device supports

The web server (python -m pyeaton.app) exposes the same summary at GET /api/summary.
"""


def cmd_help(_args) -> int:
    print(HELP_TEXT)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pyeaton.cli", description="View/control the UPS without the UI.")
    sub = p.add_subparsers(dest="cmd")

    ps = sub.add_parser("summary", help="print the full dashboard summary")
    _add_backend_args(ps); ps.set_defaults(func=cmd_summary)

    pb = sub.add_parser("beeper", help="show or set the beeper (on|off|mute)")
    pb.add_argument("state", nargs="?", choices=["on", "off", "mute", "enabled", "disabled", "muted"],
                    help="omit to show status; otherwise set it")
    _add_backend_args(pb); pb.set_defaults(func=cmd_beeper)

    sub.add_parser("help", help="what the CLI and Python API expose").set_defaults(func=cmd_help)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        return cmd_help(args)
    try:
        return args.func(args)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
