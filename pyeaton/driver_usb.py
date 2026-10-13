"""
Real Eaton/MGE USB UPS driver via raw libusb (pyusb) control transfers — the
transport the original musb.dll used. Shares the report-descriptor parser in
hidparser.py with the hidapi driver.

On Windows this talks to the device through Eaton's libusb-win32 driver using a
bundled 64-bit libusb0.dll (see usb_backend.py); on Linux it uses the system
libusb-1.0 (root needed to detach hid-generic).

Requires: pip install pyusb.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from . import hidparser as H
from .driver_base import Driver

log = logging.getLogger("usb")

# Vendor IDs the Eaton libusb driver (etn_libusb0.inf) binds.
EATON_VIDS = (0x0463, 0x0592, 0x06DA)


def ctrl_retry(dev, bmRequestType, bRequest, wValue, wIndex, data_or_length,
               tries: int = 6, timeout: int = 5000):
    """A control transfer that retries libusb-win32's transient error 995
    ("I/O operation has been aborted"), which commonly hits the first/next
    control transfer and succeeds on retry."""
    import usb.core
    last = None
    for i in range(tries):
        try:
            return dev.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex,
                                     data_or_length, timeout=timeout)
        except usb.core.USBError as e:  # noqa: PERF203
            last = e
            msg = str(e).lower()
            transient = ("aborted" in msg or "abort" in msg
                         or "timed out" in msg or "timeout" in msg
                         or getattr(e, "errno", None) in (None, 110))
            log.debug("ctrl_transfer try %d/%d failed: %s", i + 1, tries, e)
            if not transient:
                raise
            time.sleep(0.3)
    raise last


def fetch_report_descriptor(dev, logger=None) -> bytes:
    """Robustly read the HID REPORT descriptor over libusb (Windows-safe)."""
    import usb.util

    lg = logger or log
    try:
        dev.set_configuration()
    except Exception as e:  # noqa: BLE001
        lg.debug("set_configuration: %s (ok if already configured)", e)
    try:
        usb.util.claim_interface(dev, 0)
    except Exception as e:  # noqa: BLE001
        lg.debug("claim_interface: %s (often unnecessary)", e)
    time.sleep(0.15)

    # Warm-up: a standard GET_DESCRIPTOR(DEVICE) + the product string, like the
    # original app does before reading the report descriptor. On some Eaton units
    # the control endpoint won't answer the interface-level HID/REPORT request
    # until it has served a device-level request first.
    try:
        dd = bytes(ctrl_retry(dev, 0x80, 0x06, 0x0100, 0, 18, tries=4))
        if len(dd) >= 16 and dd[15]:  # iProduct string index
            try:
                usb.util.get_string(dev, dd[15])
            except Exception:  # noqa: BLE001
                pass
        time.sleep(0.1)
    except Exception as e:  # noqa: BLE001
        lg.debug("device-descriptor warm-up failed (continuing): %s", e)

    # HID descriptor (0x21) holds the report-descriptor length at bytes [7:9].
    rd_len = 0
    try:
        hid_desc = bytes(ctrl_retry(dev, 0x81, 0x06, 0x21 << 8, 0, 9))
        if len(hid_desc) >= 9:
            rd_len = int.from_bytes(hid_desc[7:9], "little")
            lg.debug("HID descriptor says report-descriptor length = %d", rd_len)
    except Exception as e:  # noqa: BLE001
        lg.debug("HID descriptor read failed, will request REPORT directly: %s", e)

    for length in ([rd_len] if rd_len else []) + [4096, 2048, 1024, 512]:
        try:
            rd = bytes(ctrl_retry(dev, 0x81, 0x06, 0x22 << 8, 0, length))
            if rd:
                return rd
        except Exception as e:  # noqa: BLE001
            lg.debug("REPORT descriptor read (len=%d) failed: %s", length, e)
    raise RuntimeError(
        "device did not return a HID report descriptor — it is most likely held "
        "by another program (stop the 'Eaton UPS Companion' service and close any "
        "tray app / other pyeaton instance), or the handle is stale (unplug/replug "
        "the UPS USB cable). Re-run with -v to see the exact control-transfer errors.")


class UsbPdcDriver(Driver):
    id = "ups_pdc"

    def __init__(self, vendor_id: int | None = None, product_id: int = 0xFFFF,
                 libusb0_dll: str | None = None) -> None:
        self.vid = vendor_id  # None -> search all known Eaton VIDs
        self.pid = product_id
        self.libusb0_dll = libusb0_dll
        self.dev = None
        self.objects: dict[str, dict] = {}
        self.reports: dict[str, list] = {}
        self.max_report_len = 1

    def open(self) -> bool:
        import usb.core
        from .usb_backend import load_backend
        backend, notes = load_backend(self.libusb0_dll)
        if notes:
            log.info("usb backend: %s", notes)
        if backend is None:
            log.error("no usable libusb backend; see notes above")
            return False
        vids = (self.vid,) if self.vid else EATON_VIDS
        for vid in vids:
            kwargs = {"idVendor": vid, "backend": backend}
            if self.pid not in (0, 0xFFFF):
                kwargs["idProduct"] = self.pid
            self.dev = usb.core.find(**kwargs)
            if self.dev is not None:
                self.vid = vid
                break
        if self.dev is None:
            log.error("no Eaton USB device found (searched VIDs %s)",
                      ", ".join(f"0x{v:04x}" for v in vids))
            return False
        log.info("found device vid=0x%04x pid=0x%04x", self.dev.idVendor, self.dev.idProduct)
        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)  # Linux only
        except Exception:  # noqa: BLE001
            pass
        self._init_object_list()
        return True

    # --- control transfers (all retrying) --------------------------------
    def _get_report(self, report_num: int) -> bytes:
        return bytes(ctrl_retry(self.dev, 0xA1, 0x01, 0x0300 + report_num, 0, self.max_report_len))

    def _set_report(self, report_num: int, content: bytes) -> None:
        ctrl_retry(self.dev, 0x21, 0x09, 0x0300 + report_num, 0, content)

    def _init_object_list(self) -> None:
        report_desc = fetch_report_descriptor(self.dev, log)
        self.objects, self.reports, self.max_report_len = H.parse_report_descriptor(report_desc)
        log.info("parsed %d objects, max report len %d", len(self.objects), self.max_report_len)

    # --- string descriptors (iProduct, iModel, ...) ----------------------
    @staticmethod
    def _is_string_index(path: str) -> bool:
        # HID PDC string objects are named iName/iProduct/iModel/iSerialNumber/...
        seg = path.rsplit(".", 1)[-1]
        return len(seg) >= 2 and seg[0] == "i" and seg[1].isupper()

    def _get_string(self, index):
        import usb.util
        try:
            index = int(index)
        except (TypeError, ValueError):
            return None
        if index <= 0:
            return None
        for _ in range(3):
            try:
                s = usb.util.get_string(self.dev, index)
                return s.strip() if s else None
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        return None

    # --- public api -------------------------------------------------------
    def product_info(self) -> dict[str, Any]:
        return {p: self.get(p) for p in (
            "UPS.PowerSummary.iManufacturer",
            "UPS.PowerSummary.iProduct",
            "UPS.PowerSummary.iModel",
            "UPS.PowerSummary.iVersion",
            "UPS.PowerSummary.iSerialNumber",
            "UPS.PowerSummary.iDeviceChemistery",
            # Slow-changing battery config (read once; poll() skips constants):
            "UPS.PowerSummary.DesignCapacity",
            "UPS.PowerSummary.FullChargeCapacity",
        ) if p in self.objects}

    def get(self, path: str, index: int = 0):
        obj = self.objects.get(path)
        if not obj:
            return None
        num = int(obj["reportID"][1:])
        content = self._get_report(num)
        if not content or content[0] != num:
            return None
        for o in self.reports[obj["reportID"]]:
            if o.get("path") == path:
                val = H.extract(content, o, index)
                # Resolve string-index objects (iProduct, iModel, ...) to text.
                if self._is_string_index(path) and isinstance(val, (int, float)):
                    return self._get_string(val) or val
                return val
        return None

    def set(self, path: str, value: Any, index: int = 0) -> bool:
        obj = self.objects.get(path)
        if not obj:
            return False
        num = int(obj["reportID"][1:])
        content = bytearray(self._get_report(num))
        H.inject(content, obj, value, index)
        self._set_report(num, bytes(content))
        return True

    # Always-read measures/status even if a descriptor marks them constant.
    _ALWAYS = {
        "UPS.PowerSummary.RemainingCapacity", "UPS.PowerSummary.RunTimeToEmpty",
        "UPS.PowerSummary.PercentLoad", "UPS.PowerSummary.Voltage",
        "UPS.PowerSummary.AudibleAlarmControl",
    }

    def poll(self) -> dict[str, Any]:
        out = {}
        for path, obj in self.objects.items():
            if not path.startswith(("UPS.", "Battery")):
                continue
            # Only re-read volatile values (measures + PresentStatus flags) or the
            # curated always-read set; skip constants like DesignCapacity/identity.
            volatile = not obj.get("constant", False)
            if not (volatile or ".PresentStatus." in path or path in self._ALWAYS):
                continue
            try:
                v = self.get(path)
                if v is not None:
                    out[path] = v
            except Exception as e:  # noqa: BLE001
                log.debug("get %s failed: %s", path, e)
        return out
