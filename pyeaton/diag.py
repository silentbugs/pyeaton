r"""
USB detection tool — run FIRST on the machine with the UPS.

The Eaton app binds the UPS to its OWN libusb-win32 driver (etn_libusb0.inf),
so the device lives on libusb, NOT the Windows HID stack. This tool therefore
scans libusb (pyusb) first; it also lists HID devices for completeness.

  python -m pyeaton.diag                          # scan libusb + HID, flag Eaton
  python -m pyeaton.diag --libusb0-dll C:\path\to\bin\libusb0.dll   # Windows
  python -m pyeaton.diag --parse                  # also fetch+parse report descriptor

Eaton vendor IDs (from etn_libusb0.inf): 0x0463, 0x0592, 0x06da.
"""
from __future__ import annotations

import argparse
import sys

EATON_VIDS = (0x0463, 0x0592, 0x06DA)


def _scan_libusb(libusb0_dll):
    print("== libusb / USB devices (where the Eaton driver puts the UPS) ==")
    import sys
    from .usb_backend import load_backend, python_bits
    print(f"  (python={sys.executable})")
    print(f"  (platform={sys.platform}, {python_bits()}-bit)")
    if sys.platform != "win32":
        print("  WARNING: this is NOT Windows Python. A USB UPS attached to Windows is")
        print("  invisible to Linux/WSL unless forwarded with usbipd-win. Run this with")
        print("  your WINDOWS Python instead, or usbipd attach the device into WSL.")
    try:
        import usb.core
        import usb.util
    except ImportError:
        print("  pyusb not installed.  pip install pyusb")
        return None
    backend, notes = load_backend(libusb0_dll)
    if notes:
        print(f"  backend notes: {notes}")
    if backend is None:
        print("  no usable libusb backend loaded.")
    try:
        devs = list(usb.core.find(find_all=True, backend=backend))
    except Exception as e:  # noqa: BLE001
        print(f"  enumeration failed: {e}")
        print("  On Windows pass --libusb0-dll pointing at Eaton's bin\\libusb0.dll,")
        print("  or install a libusb-1.0 runtime.")
        return None

    print(f"  libusb enumerated {len(devs)} device(s).")
    if backend is not None and len(devs) == 0:
        print("  NOTE: 0 devices via libusb-1.0 on Windows is expected when the UPS is on")
        print("  Eaton's libusb0.sys driver — libusb-1.0 can't see it. Use the matching")
        print("  libusb0.dll (64-bit, e.g. C:\\Windows\\System32\\libusb0.dll) via --libusb0-dll,")
        print("  or 32-bit Python + Eaton's bin\\libusb0.dll, or switch the device to the HID driver.")

    eaton = []
    for d in devs:
        mark = "  <-- EATON" if d.idVendor in EATON_VIDS else ""
        try:
            name = usb.util.get_string(d, d.iProduct) if d.iProduct else ""
        except Exception:  # noqa: BLE001
            name = ""
        print(f"  vid=0x{d.idVendor:04x} pid=0x{d.idProduct:04x}  {name or '?'}{mark}")
        if d.idVendor in EATON_VIDS:
            eaton.append(d)
    if not eaton:
        print("  no Eaton VID (0x0463/0x0592/0x06da) on libusb.")
    return eaton


def _scan_hid():
    print("\n== HID devices (the UPS will NOT be here if Eaton's libusb driver is bound) ==")
    try:
        import hid
    except ImportError:
        print("  hid not installed (ok if you use --usb).  pip install hidapi")
        return
    devs = hid.enumerate()
    if not devs:
        print("  (none)")
    for d in devs:
        mark = "  <-- EATON" if d["vendor_id"] in EATON_VIDS else ""
        print(f"  vid=0x{d['vendor_id']:04x} pid=0x{d['product_id']:04x}  "
              f"{d.get('product_string') or '?'}{mark}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pyeaton.diag")
    p.add_argument("--libusb0-dll", default=None, help=r"path to Eaton's bin\libusb0.dll (Windows)")
    p.add_argument("--parse", action="store_true", help="fetch + parse the report descriptor")
    p.add_argument("--dump", action="store_true",
                   help="read EVERY feature report (raw bytes + decoded values); read-only")
    p.add_argument("--watch", type=float, default=0, metavar="SECS",
                   help="live-print charge/charging/runtime/load every SECS seconds (read-only)")
    args = p.parse_args(argv)

    eaton = _scan_libusb(args.libusb0_dll)
    _scan_hid()

    if not eaton:
        print("\nUPS not found on libusb. Checklist:")
        print("  1. Is it plugged in and powered?")
        print("  2. STOP the 'Eaton UPS Companion' service (it holds the device open).")
        print("  3. In Device Manager, confirm it appears under 'Eaton USB Devices'")
        print("     and note its VID/PID (Properties > Details > Hardware Ids).")
        print(r"  4. On Windows pass --libusb0-dll C:\...\UPS Companion\bin\libusb0.dll")
        return 1

    dev = eaton[0]
    print(f"\nFound Eaton UPS on libusb: vid=0x{dev.idVendor:04x} pid=0x{dev.idProduct:04x}")
    if args.watch:
        import time
        from .driver_usb import UsbPdcDriver
        drv = UsbPdcDriver(libusb0_dll=args.libusb0_dll)
        if not drv.open():
            print("could not open device (is the Eaton service still holding it?)")
            return 1
        print(f"watching every {args.watch}s (Ctrl+C to stop)\n")
        print(f"{'time':8}  {'charge%':>7}  {'charging':>8}  {'runtime_s':>9}  {'load%':>5}  {'needRepl':>8}")
        try:
            while True:
                g = drv.get
                row = (time.strftime("%H:%M:%S"),
                       g("UPS.PowerSummary.RemainingCapacity"),
                       g("UPS.PowerSummary.PresentStatus.Charging"),
                       g("UPS.PowerSummary.RunTimeToEmpty"),
                       g("UPS.PowerSummary.PercentLoad"),
                       g("UPS.PowerSummary.PresentStatus.NeedReplacement"))
                print(f"{row[0]:8}  {str(row[1]):>7}  {str(row[2]):>8}  {str(row[3]):>9}  {str(row[4]):>5}  {str(row[5]):>8}")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nstopped.")
        finally:
            drv.close()
        return 0
    if args.parse:
        from . import hidparser as H
        from .driver_usb import fetch_report_descriptor
        try:
            rd = fetch_report_descriptor(dev)
            objs, reports, maxlen = H.parse_report_descriptor(rd)
            print(f"report descriptor: {len(rd)} bytes -> {len(objs)} feature objects, "
                  f"{len(reports)} reports, max report len {maxlen}\n")
            for path in sorted(objs):
                print("   ", path)
        except Exception as e:  # noqa: BLE001
            print(f"could not read report descriptor: {e}")
            print("(stop the Eaton service so it isn't holding the device, then retry)")
            return 1
    elif args.dump:
        # Read-only: build the driver (parses the descriptor), then GET every
        # feature report and show raw bytes + each decoded object. This is the
        # complete, authoritative picture of what the device exposes.
        from . import hidparser as H
        from .driver_usb import UsbPdcDriver
        drv = UsbPdcDriver(libusb0_dll=args.libusb0_dll)
        if not drv.open():
            print("could not open device (is the Eaton service still holding it?)")
            return 1
        print(f"{len(drv.objects)} objects across {len(drv.reports)} reports:\n")
        for rid in sorted(drv.reports):
            if not rid.startswith("F"):
                continue  # feature reports only (I/O reports aren't GET-able this way)
            num = int(rid[1:])
            try:
                raw = drv._get_report(num)
            except Exception as e:  # noqa: BLE001
                print(f"  {rid}: <read failed: {e}>")
                continue
            hexs = " ".join(f"{b:02x}" for b in raw)
            print(f"  report {rid}: [{hexs}]")
            for o in drv.reports[rid]:
                if o.get("path"):
                    try:
                        val = H.extract(raw, o)
                    except Exception:  # noqa: BLE001
                        val = "?"
                    unit = o.get("unit", "")
                    print(f"      {o['path']} = {val} {unit}".rstrip())
        drv.close()
        print("\n(If nothing above mentions Fan/Cooling/Temperature, the firmware")
        print(" exposes no such control — there is no hidden fan register to set.)")
    else:
        print("Run with --parse (object names) or --dump (every value) to inspect it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
