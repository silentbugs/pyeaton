# pyeaton

A small Python library and local dashboard for Eaton UPS devices, reverse-engineered
from Eaton UPS Companion. It talks to the UPS over USB (HID Power Device Class) with
no vendor software, and ships a simulator so it also runs with no hardware.

## Install

```sh
poetry install        # or: pip install .
```

## Use

```sh
python -m pyeaton.cli summary --sim        # no hardware
python -m pyeaton.cli summary              # real UPS (USB)
python -m pyeaton.cli beeper off           # beeper: on | off | mute
python -m pyeaton.app --sim                # dashboard at http://localhost:4679
python -m pyeaton.app --usb --nut          # real UPS + NUT (upsd) server on 3493
python -m pyeaton.cli help                 # full CLI + API reference
```

```python
from pyeaton.client import UpsClient
with UpsClient("usb") as ups:              # "sim" | "usb" | "hid"
    print(ups.summary())                   # status, alerts, battery health, power, energy
    ups.set_beeper("off")
```

Backends: `sim` (no hardware), `usb` (libusb/pyusb — a 64-bit `libusb0.dll` is bundled
for Windows), `hid` (hidapi). See `python -m pyeaton.diag` to detect a connected UPS.

## Layout

```
pyeaton/     the library (drivers, HID parser, server, NUT, CLI, client)
web/         the dashboard served by `pyeaton.app`
tests/       unit tests  (python -m unittest discover -s tests)
```

pyeaton is MIT licensed. Not affiliated with Eaton. The bundled
`vendor/libusb0-x64/libusb0.dll` is a third-party LGPL component (libusb-win32,
unmodified) — see its README; it's fine to redistribute here.
