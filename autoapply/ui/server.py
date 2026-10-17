"""Tiny local dashboard server (standard library only).

Security: binds to localhost, rejects unexpected Host headers (DNS rebinding) and
requires a custom header on POST, which browsers will not send cross-origin
without a preflight we never answer.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..app import PORTALS, PortalDesk

_HTML = Path(__file__).with_name("dashboard.html")
_ACTIONS = {"start", "pause", "resume", "stop", "launch"}


def make_server(desk: PortalDesk, host: str, port: int) -> ThreadingHTTPServer:
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console clean; the app has its own logging
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._json(403, {"error": "bad host"})
            if self.path in ("/", "/index.html"):
                return self._send(200, _HTML.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/status":
                return self._json(200, desk.status())
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-AutoApply") != "1":
                return self._json(403, {"error": "forbidden"})
            parts = [part for part in self.path.removeprefix("/api/").split("/") if part]
            if len(parts) != 2:
                return self._json(404, {"error": "unknown action"})
            portal, action = parts
            if action not in _ACTIONS or portal not in (*PORTALS, "all"):
                return self._json(404, {"error": "unknown action"})
            if portal == "all":
                getattr(desk, f"{action}_all")()
            elif action == "start":
                started = desk.start(portal)
                if not started:
                    return self._json(409, {"error": "already running", **desk.status()})
            else:
                getattr(desk, action)(portal)
            self._json(200, desk.status())

    return ThreadingHTTPServer((host, port), Handler)
