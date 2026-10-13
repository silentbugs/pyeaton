"""Entry point: wire up config + driver + node manager + shutdown + HTTP (+ NUT).

Examples:
  python -m pyeaton.app --sim
  python -m pyeaton.app --usb --config /etc/pyeaton/config.json
  python -m pyeaton.app --sim --nut --nut-port 3493
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys

from .config import Config
from .nodemanager import NodeManager
from .server import serve
from .shutdown import ShutdownConfig, ShutdownManager


def build_driver(args):
    if args.hid:
        from .driver_hid import HidPdcDriver
        return HidPdcDriver(vendor_id=args.vid or 0x0463,
                            product_id=(0 if args.pid in (0, 0xFFFF) else args.pid))
    if args.usb:
        from .driver_usb import UsbPdcDriver
        return UsbPdcDriver(vendor_id=args.vid, product_id=args.pid, libusb0_dll=args.libusb0_dll)
    from .driver_sim import SimDriver
    return SimDriver(load_w=args.load)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pyeaton")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--sim", action="store_true", help="simulator backend (default)")
    mode.add_argument("--hid", action="store_true", help="real Eaton USB UPS via hidapi (recommended on Windows/macOS)")
    mode.add_argument("--usb", action="store_true", help="real Eaton USB UPS via raw libusb/pyusb (Linux, or Windows+Zadig)")
    p.add_argument("--config", help="path to JSON config (loaded + saved)")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--load", type=float, default=60.0, help="sim: connected load in W")
    p.add_argument("--vid", type=lambda x: int(x, 0), default=None,
                   help="USB vendor id; default searches Eaton 0x0463/0x0592/0x06da")
    p.add_argument("--pid", type=lambda x: int(x, 0), default=0xFFFF)
    p.add_argument("--libusb0-dll", default=None,
                   help=r"path to Eaton's libusb0.dll (Windows --usb), e.g. C:\...\bin\libusb0.dll")
    p.add_argument("--poll-ms", type=int, default=None)
    p.add_argument("--nut", action="store_true", help="also serve the NUT upsd protocol")
    p.add_argument("--nut-port", type=int, default=None)
    p.add_argument("--token", default=None, help="require this token on /api/* and sendData")
    p.add_argument("--rtte-limit", type=int, default=None)
    p.add_argument("--shutdown-type", default=None, choices=["none", "hibernate", "shutdown", "script"])
    p.add_argument("--shutdown-script", default=None)
    p.add_argument("--execute", action="store_true", help="actually power off host when policy fires")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    cfg = Config.load(args.config)
    # CLI overrides config file.
    if args.host is not None: cfg.http_host = args.host
    if args.port is not None: cfg.http_port = args.port
    if args.poll_ms is not None: cfg.acquisition.pollingRate = args.poll_ms
    if args.token is not None: cfg.api_token = args.token
    if args.nut: cfg.nut_enabled = True
    if args.nut_port is not None: cfg.nut_port = args.nut_port
    if args.rtte_limit is not None: cfg.shutdownSettings.runTimeToEmptyLimit = args.rtte_limit
    if args.shutdown_type is not None: cfg.shutdownSettings.shutdownType = args.shutdown_type
    if args.shutdown_script is not None: cfg.shutdownSettings.shutdownScript = args.shutdown_script
    if args.execute: cfg.shutdownSettings.execute = True

    driver = build_driver(args)
    ss = cfg.shutdownSettings
    sd = ShutdownManager(ShutdownConfig(
        run_time_to_empty_limit=ss.runTimeToEmptyLimit,
        shutdown_duration=ss.shutdownDuration,
        shutdown_type=ss.shutdownType,
        shutdown_script=ss.shutdownScript,
        execute=ss.execute,
    ), driver)
    node = NodeManager(driver, sd, cfg, poll_ms=cfg.acquisition.pollingRate)
    node.start()

    nut_srv = None
    if cfg.nut_enabled:
        from .nut_server import start_nut
        nut_srv = start_nut(node, cfg.nut_name, host=cfg.http_host, port=cfg.nut_port)

    httpd = serve(node, driver, cfg, host=cfg.http_host, port=cfg.http_port)

    def _stop(*_):
        logging.getLogger("app").info("shutting down")
        # shutdown() must run off the serve_forever() thread or it deadlocks.
        import threading as _t
        _t.Thread(target=httpd.shutdown, daemon=True).start()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    logging.getLogger("app").info(
        "ready: http://localhost:%d  mode=%s  nut=%s", cfg.http_port,
        "usb" if args.usb else "sim", "on" if cfg.nut_enabled else "off")
    try:
        httpd.serve_forever()
    finally:
        node.stop()
        if nut_srv:
            nut_srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
