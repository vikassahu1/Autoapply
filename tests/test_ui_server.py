import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from autoapply.config import load_settings
from autoapply.desk import PortalDesk
from autoapply.ui.server import make_server


@pytest.fixture
def port():
    desk = PortalDesk(load_settings())
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    server = make_server(desk, "127.0.0.1", free)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield free
    server.shutdown()
    server.server_close()


def req(port, path, method="GET", headers=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def test_dashboard_and_status_are_served(port):
    status, body = req(port, "/")
    assert status == 200 and b"Auto-apply" in body and b"Indeed" in body and b"LinkedIn" in body
    status, body = req(port, "/api/status")
    data = json.loads(body)
    assert status == 200
    assert data["portals"]["indeed"]["state"] == "stopped"
    assert data["portals"]["linkedin"]["state"] == "stopped"
    assert data["portals"]["indeed"]["debug_port"] != data["portals"]["linkedin"]["debug_port"]


def test_post_without_custom_header_is_refused(port):
    assert req(port, "/api/indeed/start", "POST")[0] == 403


def test_unknown_action_and_bad_host_are_refused(port):
    assert req(port, "/api/indeed/format-disk", "POST", {"X-AutoApply": "1"})[0] == 404
    assert req(port, "/api/status", headers={"Host": "evil.example"})[0] == 403


def test_controls_on_an_idle_app_are_harmless(port):
    for action in ("pause", "resume", "stop"):
        assert req(port, f"/api/indeed/{action}", "POST", {"X-AutoApply": "1"})[0] == 200
        assert req(port, f"/api/all/{action}", "POST", {"X-AutoApply": "1"})[0] == 200
