libusb0.dll (64-bit) — third-party component, NOT part of pyeaton.

Origin   : libusb-win32, release 1.2.6.0 (bin/amd64/libusb0.dll), unmodified.
Upstream : https://sourceforge.net/projects/libusb-win32/
License  : GNU LGPL (the libusb-win32 user-space library is LGPL; redistribution
           in source or binary form is permitted under the LGPL, including in a
           public repository).

Why it's bundled: on 64-bit Windows this is the matching user-space library for
the libusb-win32 (libusb0.sys) driver that Eaton UPS Companion installs for the
UPS. pyeaton loads it so `--usb` works without extra setup. Eaton ships the
32-bit build of the same release; this is its 64-bit counterpart.

LGPL notice: this binary is unmodified and may be replaced with a compatible
build. For full LGPL compliance when redistributing, keep this notice and
include the LGPL license text (see the upstream project). pyeaton itself is
MIT-licensed and merely loads this library at runtime.
