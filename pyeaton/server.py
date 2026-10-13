"""
HTTP server: the original local API contract (recovered from
html/server/data_srv.js) + modern JSON endpoints + a tabbed dashboard.
Pure stdlib so the core runs with no pip installs.

Original contract (port 4679):
  server/data_srv.js?action=init|getData|sendData|ping
Modern endpoints:
  GET  /api/status            flat status + severity
  GET  /api/events            recent event log
  GET  /api/settings          current config
  PUT  /api/settings          update config (shutdown/energy/nut/...) -> saved
  POST /api/mains {on}        (sim) toggle mains
  POST /api/command {object,value}  set a UPS object (e.g. arm shutdown)
If config.api_token is set, /api/* and sendData require header
`X-Auth-Token` (or ?token=) to match.
"""
from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("http")
WEB = Path(__file__).resolve().parent.parent / "web"
SEVERITY_NAME = {0: "info", 1: "normal", 2: "warning", 3: "critical", 4: "comlost"}


class _Handler(BaseHTTPRequestHandler):
    node = None
    driver = None
    config = None

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    # --- io helpers -------------------------------------------------------
    def _send(self, code, body: bytes, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {k: v[0] for k, v in parse_qs(raw.decode(errors="replace")).items()}

    def _authed(self, q) -> bool:
        token = self.config.api_token
        if not token:
            return True
        return (self.headers.get("X-Auth-Token") == token) or (q.get("token") == token)

    def _status_payload(self) -> dict:
        s = self.node.status
        return {"lastDate": s.last_date, "data": s.snapshot(), "cmd": []}

    # --- routing ----------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path
        if p in ("/", "/index.html", "/default.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if p == "/favicon.ico":
            return self._send(204, b"", "image/x-icon")
        if p == "/healthz":
            return self._json({"ok": True})
        if p.startswith("/api/"):
            if not self._authed(q):
                return self._json({"error": "unauthorized"}, 401)
            if p == "/api/status":
                pl = self._status_payload()
                pl["severity"] = self.node.severity()
                pl["severityName"] = SEVERITY_NAME[self.node.severity()]
                return self._json(pl)
            if p == "/api/summary":
                from . import views
                return self._json(views.build_summary(self.node, self.config))
            if p == "/api/events":
                return self._json({"events": self.node.events.recent(int(q.get("limit", 100)))})
            if p == "/api/settings":
                return self._json(self.config.to_dict())
            if p == "/api/controls":
                from . import controls
                return self._json({"controls": controls.available(self.driver)})
            return self._json({"error": "not found"}, 404)
        if p.endswith("data_srv.js"):
            return self._data_srv(q, {})
        return self._send(404, b"not found", "text/plain")

    def do_PUT(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        if u.path == "/api/settings":
            body = self._read_body()
            self.config.apply(body)
            self.config.save()
            # Re-sync live shutdown policy with new settings.
            sd = self.node.shutdown.cfg
            ss = self.config.shutdownSettings
            sd.run_time_to_empty_limit = ss.runTimeToEmptyLimit
            sd.shutdown_duration = ss.shutdownDuration
            sd.shutdown_type = ss.shutdownType
            sd.shutdown_script = ss.shutdownScript
            sd.execute = ss.execute
            return self._json({"ok": True, "settings": self.config.to_dict()})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        body = self._read_body()
        if u.path.startswith("/api/") and not self._authed(q):
            return self._json({"error": "unauthorized"}, 401)
        if u.path == "/api/mains":
            if hasattr(self.driver, "set_mains"):
                on = bool(body.get("on", True))
                self.driver.set_mains(on)
                return self._json({"ok": True, "acPresent": on})
            return self._json({"ok": False, "error": "no mains control (real device)"}, 400)
        if u.path == "/api/command":
            ok = self.driver.set(body.get("object"), body.get("value"))
            return self._json({"ok": bool(ok)})
        if u.path == "/api/control":
            from . import controls
            ok, detail = controls.apply(self.driver, body.get("name"), body.get("value"))
            return self._json({"ok": ok, "detail": detail}, 200 if ok else 400)
        if u.path.endswith("data_srv.js"):
            return self._data_srv(q, body)
        return self._send(404, b"not found", "text/plain")

    # --- recovered data_srv.js contract -----------------------------------
    def _data_srv(self, q, body):
        action = q.get("action", "")
        if action == "ping":
            return self._send(200, b"", "text/plain")
        if action == "init":
            return self._json({
                "getDataURL": "server/data_srv.js?action=getData&from=webUI",
                "setDataURL": "server/data_srv.js?action=sendData",
                "labels": {"productName": "UPS Companion (Python)"},
                "ui": {"pollMs": self.config.acquisition.pollingRate},
                **self._status_payload(),
            })
        if action == "getData":
            from_date = int(body.get("fromDate", q.get("from", 0)) or 0)
            deadline = time.time() + 2.0
            while time.time() < deadline and self.node.status.last_date <= from_date:
                time.sleep(0.2)
            return self._json(self._status_payload())
        if action == "sendData":
            if not self._authed(q):
                return self._json({"error": "unauthorized"}, 401)
            obj, val = body.get("object"), body.get("value")
            ok = self.driver.set(obj, val) if obj is not None else False
            return self._json({"ok": ok})
        return self._json({"error": f"unknown action {action!r}"}, 400)

    def _file(self, name, ctype):
        fp = WEB / name
        if not fp.exists():
            return self._send(404, b"missing", "text/plain")
        return self._send(200, fp.read_bytes(), ctype)


def serve(node, driver, config, host="0.0.0.0", port=4679):
    _Handler.node = node
    _Handler.driver = driver
    _Handler.config = config
    httpd = ThreadingHTTPServer((host, port), _Handler)
    log.info("HTTP server on http://%s:%d", host, port)
    return httpd
