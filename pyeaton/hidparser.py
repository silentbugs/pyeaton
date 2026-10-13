"""
HID report-descriptor parser + value codecs, extracted from the recovered
scripts/libs/hid_layer.js so it can be shared by both real-device transports
(libusb control transfers in driver_usb.py, and hidapi feature reports in
driver_hid.py) and unit-tested without hardware.

Given a raw HID report descriptor it produces:
  objects  : {dotted_path -> field descriptor}   (FEATURE fields, settable values)
  reports  : {reportID -> [field descriptors]}    (field layout within each report)
  max_report_len : largest report length in bytes (incl. the report-id byte)

Each field descriptor: {path, reportID, offset(bits), size(bits), count,
unit, exponent, min, max, writable, constant}.
"""
from __future__ import annotations

import math

from .hid_usages import UNITS, USAGE_PAGES

# Report-descriptor item tags (HIDLayer.items in hid_layer.js).
_MASK = 0xFC
PAGE, LOG_MIN, LOG_MAX = 0x04, 0x14, 0x24
EXPONENT, UNIT, REP_SIZE, REP_ID, REP_COUNT = 0x54, 0x64, 0x74, 0x84, 0x94
USAGE, COLLECTION, END_COLLECTION = 0x08, 0xA0, 0xC0
FEATURE, INPUT, OUTPUT = 0xB0, 0x80, 0x90
ATTR_DATA_CST, ATTR_NVOL_VOL = 0x01, 0x80
REPORT_TYPE = {FEATURE: "F", INPUT: "I", OUTPUT: "O"}


def _item_size(item: int) -> int:
    return (0, 1, 2, 4)[item & 0x03]


def fmt_signed(value: int, nbytes: int) -> int:
    bits = nbytes * 8
    if bits and (value & (1 << (bits - 1))):
        return value - (1 << bits)
    return value


def usage_name(page: int, usage: int, index=None) -> str:
    node = USAGE_PAGES.get(page, {}).get(usage)
    if node is None:
        node = f"(0x{page:04x}/0x{usage:02x})"
    if index is not None:
        node += f"[{index}]"
    return node


def parse_report_descriptor(d: bytes):
    objects: dict[str, dict] = {}
    reports: dict[str, list] = {}
    max_report_len = 1

    usage_stack: list[dict] = []
    hid_path: list[str] = []
    page = report_id = rep_size = rep_count = 0
    unit = ("-", 0)
    exponent = log_min = log_max = 0

    pos = 0
    while pos < len(d):
        item = d[pos]; pos += 1
        size = _item_size(item)
        value = int.from_bytes(d[pos:pos + size], "little"); pos += size
        tag = item & _MASK

        if tag == PAGE:
            page = value
        elif tag == USAGE:
            if size > 2:
                usage_stack.append({"page": value >> 16, "usage": value & 0xFFFF})
            else:
                usage_stack.append({"page": page, "usage": value})
        elif tag == COLLECTION:
            pn = usage_stack.pop(0) if usage_stack else {"page": page, "usage": 0}
            if value >= 0x80:
                idx = value & 0x7F
                pn["index"] = chr(idx - 5) if idx > 100 else idx
            hid_path.append(usage_name(pn["page"], pn["usage"], pn.get("index")))
        elif tag == END_COLLECTION:
            if hid_path:
                hid_path.pop()
        elif tag == REP_ID:
            report_id = value
        elif tag == REP_SIZE:
            rep_size = value
        elif tag == REP_COUNT:
            rep_count = value
        elif tag == UNIT:
            unit = UNITS.get(value, ("??", 0))
        elif tag == EXPONENT:
            exponent = value
        elif tag == LOG_MIN:
            log_min = fmt_signed(value, size)
        elif tag == LOG_MAX:
            log_max = fmt_signed(value, size)
        elif tag in (FEATURE, INPUT, OUTPUT):
            max_report_len = _emit(
                tag, value, report_id, rep_size, rep_count, unit, exponent,
                log_min, log_max, usage_stack, hid_path, objects, reports, max_report_len)
            # Global items (Report Count/Size) persist per the HID spec; do not reset.

    return objects, reports, max_report_len


def _emit(tag, flags, report_id, rep_size, count, unit, exponent, lmin, lmax,
          usage_stack, hid_path, objects, reports, max_report_len):
    rid = REPORT_TYPE[tag] + f"{report_id:03d}"
    if rid not in reports:
        reports[rid] = []
        offset = 0
    else:
        last = reports[rid][-1]
        offset = last["offset"] + last["size"] * last["count"]

    remaining = count
    while remaining > 0:
        rep_len = math.ceil((offset + rep_size * remaining) / 8) + 1
        max_report_len = max(max_report_len, rep_len)
        exp = exponent - unit[1]
        if exp > 7:
            exp -= 16
        pn = usage_stack.pop(0) if usage_stack else {"usage": 0}
        n = 1
        if not usage_stack and remaining > 1:
            n = remaining
            remaining = 0
        obj = {
            "reportID": rid, "offset": offset, "size": rep_size, "count": n,
            "unit": unit[0], "exponent": exp, "min": lmin, "max": lmax,
            "writable": not (flags & ATTR_DATA_CST),
            "constant": not (flags & ATTR_NVOL_VOL),
        }
        if pn["usage"] > 0:
            hid_path.append(usage_name(pn.get("page", 0), pn["usage"], pn.get("index")))
            path = ".".join(hid_path)
            hid_path.pop()
            obj["path"] = path
            if tag == FEATURE:
                objects[path] = obj
            reports[rid].append(obj)
        else:
            reports[rid].append(obj)
        offset += rep_size * obj["count"]
        remaining -= 1
    return max_report_len


def extract(content: bytes, obj: dict, index: int = 0):
    """Pull obj's value out of a report payload (report-id byte included)."""
    bit = obj["offset"] + 8 + index * obj["size"]
    value = 0
    for w in range(obj["size"]):
        if content[bit >> 3] & (1 << (bit % 8)):
            value += 1 << w
        bit += 1
    if value > obj["max"]:
        value = fmt_signed(value, math.ceil(obj["size"] / 8))
    if obj["exponent"] > 0:
        value *= 10 ** obj["exponent"]
    elif obj["exponent"] < 0:
        value /= 10 ** (-obj["exponent"])
    return value


def inject(content: bytearray, obj: dict, value, index: int = 0) -> bytearray:
    """Write value into a report payload (report-id byte included)."""
    bit = obj["offset"] + 8 + index * obj["size"]
    v = int(value)
    if obj["exponent"] < 0:
        v = int(v * 10 ** (-obj["exponent"]))
    elif obj["exponent"] > 0:
        v = int(v / 10 ** obj["exponent"])
    for w in range(obj["size"]):
        if v & (1 << w):
            content[bit >> 3] |= 1 << (bit % 8)
        else:
            content[bit >> 3] &= ~(1 << (bit % 8))
        bit += 1
    return content
