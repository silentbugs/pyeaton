"""
Real Eaton/MGE USB UPS driver via the OS HID stack (hidapi) — the recommended
path on Windows and macOS: no driver replacement (no Zadig), usually no admin.

It talks to the device through HID feature reports, which is exactly what the
recovered hid_layer.js does over the wire (GET_REPORT/SET_REPORT, feature).
The field layout still comes from the parsed report descriptor (hidparser.py).

Requires: pip install hidapi   (the `hid` module; hidapi >= 0.14 gives us the
report descriptor on every platform). On Linux you also need hidraw access
(a udev rule, or run as root).
"""
from __future__ import annotations

import logging
from typing import Any

from . import hidparser as H
from .driver_base import Driver

log = logging.getLogger("hid")


class HidPdcDriver(Driver):
    id = "ups_pdc_hid"

    def __init__(self, vendor_id: int = 0x0463, product_id: int = 0, path: bytes | None = None) -> None:
        self.vid = vendor_id
        self.pid = product_id
        self.path = path
        self.dev = None
        self.objects: dict[str, dict] = {}
        self.reports: dict[str, list] = {}
        self.max_report_len = 1

    def open(self) -> bool:
        import hid

        # Find the device (first match on VID, optionally PID).
        if self.path is None:
            matches = [d for d in hid.enumerate(self.vid, self.pid or 0)]
            if not matches:
                log.error("no HID device for vid=0x%04x pid=0x%04x", self.vid, self.pid)
                return False
            self.path = matches[0]["path"]
            log.info("opening %s %s (%s)", matches[0].get("manufacturer_string", ""),
                     matches[0].get("product_string", ""), matches[0]["path"])

        self.dev = hid.device()
        self.dev.open_path(self.path)
        self.dev.set_nonblocking(0)
        self._init_object_list()
        return True

    def close(self) -> None:
        if self.dev:
            try:
                self.dev.close()
            except Exception:  # noqa: BLE001
                pass

    # --- descriptor ------------------------------------------------------
    def _report_descriptor(self) -> bytes:
        # hidapi >= 0.14 exposes this on all platforms.
        if hasattr(self.dev, "get_report_descriptor"):
            return bytes(self.dev.get_report_descriptor())
        # Linux fallback: read it straight from sysfs via the hidraw path.
        import glob
        import os
        for rd in glob.glob("/sys/class/hidraw/*/device/report_descriptor"):
            try:
                with open(rd, "rb") as f:
                    return f.read()
            except OSError:
                continue
        raise RuntimeError(
            "hidapi has no get_report_descriptor() (need hidapi>=0.14) and no "
            "hidraw fallback found; upgrade hidapi or use --usb")

    def _init_object_list(self) -> None:
        rd = self._report_descriptor()
        self.objects, self.reports, self.max_report_len = H.parse_report_descriptor(rd)
        log.info("parsed %d objects, max report len %d", len(self.objects), self.max_report_len)

    # --- feature-report transport ----------------------------------------
    def _get_report(self, report_num: int) -> bytes:
        # hidapi expects the report-id as the first byte of the request buffer.
        data = self.dev.get_feature_report(report_num, self.max_report_len + 1)
        return bytes(data)

    def _set_report(self, report_num: int, content: bytes) -> None:
        # content already starts with the report id byte.
        self.dev.send_feature_report(content)

    # --- public api (same shape as UsbPdcDriver) --------------------------
    def product_info(self) -> dict[str, Any]:
        return {p: self.get(p) for p in (
            "UPS.PowerSummary.iManufacturer",
            "UPS.PowerSummary.iProduct",
            "UPS.PowerSummary.iSerialNumber",
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
                return H.extract(content, o, index)
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

    def poll(self) -> dict[str, Any]:
        out = {}
        for path in self.objects:
            if path.startswith(("UPS.", "Battery")):
                try:
                    v = self.get(path)
                    if v is not None:
                        out[path] = v
                except Exception as e:  # noqa: BLE001
                    log.debug("get %s failed: %s", path, e)
        return out
