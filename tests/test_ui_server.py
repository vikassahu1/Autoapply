import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from autoapply.app import AutoApplyApp
from autoapply.config import PROJECT_ROOT, load_settings
from autoapply.portals import build_adapter
from autoapply.store import CsvJobStore
from autoapply.ui.server import make_server


@pytest.fixture
def port(tmp_path):
    app = AutoApplyApp(load_settings(), build_adapter("indeed", PROJECT_ROOT), CsvJobStore(tmp_path / "a.csv"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    server = make_server(app, "127.0.0.1", free)
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
    assert status == 200 and b"Auto-apply" in body
    status, body = req(port, "/api/status")
    data = json.loads(body)
    assert status == 200 and data["state"] == "stopped" and data["counts"]["attempted"] == 0


def test_post_without_custom_header_is_refused(port):
    # what a malicious web page could send cross-origin
    assert req(port, "/api/start", "POST")[0] == 403


def test_unknown_action_and_bad_host_are_refused(port):
    assert req(port, "/api/format-disk", "POST", {"X-AutoApply": "1"})[0] == 404
    assert req(port, "/api/status", headers={"Host": "evil.example"})[0] == 403


def test_controls_on_an_idle_app_are_harmless(port):
    for action in ("pause", "resume", "stop"):
        assert req(port, f"/api/{action}", "POST", {"X-AutoApply": "1"})[0] == 200
