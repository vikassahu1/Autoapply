"""End-to-end: real Chromium over CDP, driven through AutoApplyApp, against tests/mock_site.py."""
import csv
import dataclasses
import json
import socket
import subprocess
import tempfile
import time
import urllib.request

import pytest

from autoapply.app import AutoApplyApp
from autoapply.config import FlowSettings, RunSettings, StorageSettings, PROJECT_ROOT, load_portal_config, load_settings
from autoapply.core.control import RunState
from autoapply.core.store import CsvJobStore
from autoapply.portals.indeed import IndeedAdapter
from mock_site import MockSite

pytestmark = pytest.mark.browser


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(predicate, timeout=60.0, interval=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def env(tmp_path):
    from playwright.sync_api import sync_playwright

    site_port, cdp_port = free_port(), free_port()
    site = MockSite(site_port, external_origin=f"http://localhost:{site_port}")
    site.start()
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
    except Exception as exc:
        pytest.skip(f"no Chromium available: {exc}")

    profile = tempfile.mkdtemp(prefix="autoapply-e2e-")
    proc = subprocess.Popen(
        [exe, "--headless=new", "--no-sandbox", f"--remote-debugging-port={cdp_port}",
         f"--user-data-dir={profile}", f"http://127.0.0.1:{site_port}/unrelated"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not wait_for(lambda: _cdp_up(cdp_port), 20):
        proc.kill()
        pytest.skip("Chromium did not expose CDP")

    cfg = load_portal_config("indeed", PROJECT_ROOT)
    cfg["start_url"] = f"http://127.0.0.1:{site_port}/"
    cfg["trusted_domains"] = ["127.0.0.1"]          # 'localhost' therefore counts as a company site
    cfg["listing"]["pane_switch_timeout_s"] = 1.0
    cfg["listing"]["load_more_wait_s"] = 0.5
    adapter = IndeedAdapter(cfg)

    base = load_settings()
    settings = dataclasses.replace(
        base,
        browser=dataclasses.replace(base.browser, debug_port=cdp_port),
        flow=FlowSettings(max_steps=6, poll_interval_s=0.1, step_timeout_s=5, unknown_page_grace_s=1.5,
                          submit_verify_timeout_s=2, pause_on_unknown_page=True),
        run=RunSettings(max_applications=0, delay_between_jobs_s=(0.0, 0.0), max_consecutive_errors=3),
        storage=StorageSettings(path=str(tmp_path / "apps.csv")),
    )
    store = CsvJobStore(tmp_path / "apps.csv")
    app = AutoApplyApp(settings, adapter, store)
    yield app, site, cdp_port, tmp_path / "apps.csv"
    app.stop()
    proc.kill()
    site.stop()


def _cdp_up(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1).read()
        return True
    except Exception:
        return False


def _tabs(port):
    return [t for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json")) if t["type"] == "page"]


def test_full_run_against_mock_portal(env):
    app, site, cdp_port, csv_path = env
    assert app.start()

    # Run pauses on j5's CAPTCHA and does NOT go on to open j6/j7.
    assert wait_for(lambda: app.controller.state is RunState.PAUSED), app.status()
    status = app.status()
    assert "captcha" in status["pause_reason"] and "ML Engineer" in status["pause_reason"]
    assert not any("/apply/j6" in h for h in site.hits)
    assert status["counts"]["submitted"] == 1, app.store._latest  # j1 only so far

    # The human solves it and resumes; the same application completes.
    site.captcha_solved = True
    time.sleep(1.5)  # a human takes a moment too: let the page notice and clear the challenge
    app.resume()
    assert wait_for(lambda: app.controller.state is RunState.STOPPED), app.status()
    final = app.status()
    assert final["error"] == "", final["error"]

    rows = {r["job_key"]: r for r in csv.DictReader(csv_path.open(encoding="utf-8-sig"))}
    # last row per key wins (interim rows exist), exactly as the store does
    latest = {k: (v["outcome"], v["reason"]) for k, v in app.store._latest.items()}
    print("SITE HITS:", site.hits)
    assert latest == {
        "j1": ("submitted", "submission_confirmed"),
        "j2": ("skipped", "requires_questions"),
        "j3": ("skipped", "external_company_site"),
        "j4": ("skipped", "external_company_site"),
        "j5": ("submitted", "submission_confirmed"),
        "j6": ("needs_manual", "submit_unverified"),
        "j7": ("needs_manual", "job_detail_did_not_match_card"),
    }
    assert app.store._latest["j1"]["company"] == "Kognito Kube"
    assert final["counts"] == {"attempted": 7, "submitted": 2, "skipped": 3, "needs_manual": 2}

    # j7's stale pane meant we never even opened an application for it
    assert not any("/apply/j7" in h for h in site.hits)
    # Applications happen in tabs we opened and then closed; the unrelated tab is untouched.
    urls = [t["url"] for t in _tabs(cdp_port)]
    assert any(u.endswith("/unrelated") for u in urls)
    assert not any("/apply/" in u or "/company" in u for u in urls), urls
