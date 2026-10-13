"""
libusb backend selection for pyusb, with clear diagnostics and a bundled
64-bit libusb0.dll so the Eaton (libusb-win32) device works on 64-bit Python
with no flags and no driver changes.

Order tried:
  1. explicit libusb0.dll passed by the caller
  2. bundled vendor/libusb0-x64/libusb0.dll (Windows 64-bit) — matches the
     libusb-win32 1.2.6.0 driver the Eaton app installs
  3. libusb_package (pip-installable libusb-1.0, Linux/mac or clean HID setups)
  4. pyusb auto-detect (system libusb-1.0 / libusb0)
Returns (backend_or_None, human_readable_notes).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path


def python_bits() -> int:
    return struct.calcsize("P") * 8


def bundled_libusb0_dll() -> str | None:
    """The 64-bit libusb0.dll shipped under vendor/, for Windows 64-bit only."""
    if sys.platform != "win32" or python_bits() != 64:
        return None
    p = Path(__file__).resolve().parent.parent / "vendor" / "libusb0-x64" / "libusb0.dll"
    return str(p) if p.exists() else None


def _try_libusb0(dll_path: str, notes: list[str]):
    try:
        import usb.backend.libusb0 as libusb0
        be = libusb0.get_backend(find_library=lambda _n: dll_path)
        if be is not None:
            notes.append(f"using libusb0 backend: {dll_path}")
            return be
        notes.append(
            f"libusb0.dll at {dll_path!r} failed to load. Python is "
            f"{python_bits()}-bit; this DLL must be the same bitness.")
    except Exception as e:  # noqa: BLE001
        notes.append(f"libusb0 backend error ({dll_path}): {e}")
    return None


def load_backend(libusb0_dll: str | None = None):
    notes: list[str] = []

    # 1. explicit path from the caller
    if libusb0_dll:
        be = _try_libusb0(libusb0_dll, notes)
        if be is not None:
            return be, "; ".join(notes)

    # 2. bundled 64-bit DLL (Windows) — zero-config path for the Eaton device
    bundled = bundled_libusb0_dll()
    if bundled:
        be = _try_libusb0(bundled, notes)
        if be is not None:
            return be, "; ".join(notes)

    # 3. pip-provided libusb-1.0 (correct bitness)
    try:
        import libusb_package
        import usb.backend.libusb1 as libusb1
        be = libusb1.get_backend(find_library=libusb_package.find_library)
        if be is not None:
            notes.append("using libusb_package (libusb-1.0) backend")
            return be, "; ".join(notes)
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        notes.append(f"libusb_package backend error: {e}")

    # 4. system default
    try:
        import usb.backend.libusb1 as libusb1
        be = libusb1.get_backend()
        if be is not None:
            notes.append("using system libusb-1.0 backend")
            return be, "; ".join(notes)
    except Exception as e:  # noqa: BLE001
        notes.append(f"default libusb1 backend error: {e}")

    return None, "; ".join(notes)
