"""
NUT (Network UPS Tools) `upsd` protocol server — a read-oriented subset so
`upsc`, Home Assistant's nut integration, and networked `upsmon` clients can
read this UPS over TCP 3493.

Implements: VER / NETVER, LIST UPS, LIST VAR <ups>, GET VAR <ups> <var>,
GET DESC/TYPE, GET NUMLOGINS, LOGIN/LOGOUT, USERNAME/PASSWORD (accepted),
GET UPSDESC. Maps HID-PDC paths -> NUT variable names.

Reference for the variable names: NUT docs + the HID-PDC usage model we
recovered (hid_usages.js). A real `upsmon` FSD/shutdown handshake is out of
scope here (that needs authenticated upsd state machine); reading works.
"""
from __future__ import annotations

import logging
import socketserver
import threading

log = logging.getLogger("nut")


def to_nut_vars(status) -> dict[str, str]:
    """Translate the PDC status snapshot into NUT variable names."""
    g = status.get
    out: dict[str, str] = {}

    def put(name, value, fmt=None):
        if value is not None:
            out[name] = fmt(value) if fmt else str(value)

    ac = g("UPS.PowerSummary.PresentStatus.ACPresent")
    charging = g("UPS.PowerSummary.PresentStatus.Charging")
    discharging = g("UPS.PowerSummary.PresentStatus.Discharging")
    below = g("UPS.PowerSummary.PresentStatus.BelowRemainingCapacityLimit")

    flags = []
    flags.append("OL" if ac else "OB")
    if below:
        flags.append("LB")
    if charging:
        flags.append("CHRG")
    if discharging and not ac:
        flags.append("DISCHRG")
    if g("UPS.PowerSummary.PresentStatus.Overload"):
        flags.append("OVER")
    if g("UPS.PowerSummary.PresentStatus.NeedReplacement"):
        flags.append("RB")
    if g("UPS.PowerSummary.PresentStatus.ShutdownImminent"):
        flags.append("FSD")
    put("ups.status", " ".join(flags))

    put("ups.mfr", g("UPS.PowerSummary.iManufacturer") or "Eaton")
    put("ups.model", g("UPS.PowerSummary.iProduct"))
    put("ups.serial", g("UPS.PowerSummary.iSerialNumber"))
    put("ups.load", g("UPS.PowerSummary.PercentLoad"))
    put("ups.realpower", g("UPS.PowerConverter.Output.ActivePower"))
    put("ups.delay.shutdown", g("UPS.PowerSummary.DelayBeforeShutdown"))
    put("ups.delay.start", g("UPS.PowerSummary.DelayBeforeStartup"))

    put("battery.charge", g("UPS.PowerSummary.RemainingCapacity"), lambda v: str(int(v)))
    put("battery.runtime", g("UPS.PowerSummary.RunTimeToEmpty"))
    put("battery.charge.low", 20)

    put("input.voltage", g("UPS.PowerConverter.Input[1].Voltage") or g("UPS.PowerSummary.Voltage"))
    put("output.voltage", g("UPS.PowerConverter.Output.Voltage"))
    put("output.frequency", g("UPS.PowerConverter.Output.Frequency"))
    put("device.type", "ups")

    # Temperature (only on models that report it; absent on e.g. the 5E).
    for src, dst in (("UPS.PowerSummary.Temperature", "ups.temperature"),
                     ("Environment.Temperature", "ambient.temperature")):
        put(dst, g(src))
    bt = g("UPS.Battery.Temperature") or g("Battery.Temperature")
    put("battery.temperature", bt)
    # Fan status is read-only in HID (FanFailure); there is no fan-speed control.
    ff = g("UPS.PowerSummary.PresentStatus.FanFailure")
    if ff is not None:
        put("ups.alarm", "Fan failure!" if ff else "")
    return out


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        peer = self.client_address
        log.debug("NUT client %s connected", peer)
        try:
            for raw in self.rfile:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if not self._dispatch(line):
                    break
        except Exception as e:  # noqa: BLE001
            log.debug("NUT client %s error: %s", peer, e)

    def _w(self, text: str):
        self.wfile.write((text + "\n").encode())

    def _dispatch(self, line: str) -> bool:
        parts = line.split()
        cmd = parts[0].upper()
        srv = self.server  # type: ignore[attr-defined]
        name = srv.ups_name
        vars_ = to_nut_vars(srv.node.status)

        if cmd in ("VER", "NETVER"):
            self._w("pyeaton NUT-compatible 1.0")
        elif cmd == "LOGIN" or cmd in ("USERNAME", "PASSWORD"):
            self._w("OK")
        elif cmd == "LOGOUT":
            self._w("OK Goodbye")
            return False
        elif cmd == "GET" and len(parts) >= 2:
            sub = parts[1].upper()
            if sub == "NUMLOGINS":
                self._w(f"NUMLOGINS {name} 1")
            elif sub == "UPSDESC":
                self._w(f'UPSDESC {name} "Eaton UPS (pyeaton)"')
            elif sub == "VAR" and len(parts) >= 4:
                var = parts[3]
                if var in vars_:
                    self._w(f'VAR {name} {var} "{vars_[var]}"')
                else:
                    self._w("ERR VAR-NOT-SUPPORTED")
            elif sub in ("DESC", "TYPE") and len(parts) >= 4:
                self._w(f'{sub} {name} {parts[3]} "{"RW" if sub=="TYPE" else ""}"')
            else:
                self._w("ERR INVALID-ARGUMENT")
        elif cmd == "LIST" and len(parts) >= 2:
            sub = parts[1].upper()
            if sub == "UPS":
                self._w("BEGIN LIST UPS")
                self._w(f'UPS {name} "Eaton UPS (pyeaton)"')
                self._w("END LIST UPS")
            elif sub == "VAR" and len(parts) >= 3:
                self._w(f"BEGIN LIST VAR {name}")
                for k, v in vars_.items():
                    self._w(f'VAR {name} {k} "{v}"')
                self._w(f"END LIST VAR {name}")
            elif sub == "RW" and len(parts) >= 3:
                self._w(f"BEGIN LIST RW {name}")
                self._w(f"END LIST RW {name}")
            else:
                self._w("ERR INVALID-ARGUMENT")
        elif cmd == "STARTTLS":
            self._w("ERR FEATURE-NOT-CONFIGURED")
        else:
            self._w("ERR UNKNOWN-COMMAND")
        return True


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_nut(node, ups_name: str, host: str = "0.0.0.0", port: int = 3493):
    srv = _Server((host, port), _Handler)
    srv.node = node
    srv.ups_name = ups_name
    t = threading.Thread(target=srv.serve_forever, name="nut", daemon=True)
    t.start()
    log.info("NUT server on %s:%d (ups=%s)", host, port, ups_name)
    return srv
