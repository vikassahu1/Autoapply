"""Listing tabs must each receive the page slice they own."""
from autoapply.parallel import run_tabs


class _Controller:
    def checkpoint(self):
        pass

    def finish(self, error=""):
        self.error = error


class _App:
    def __init__(self):
        self.controller = _Controller()
        self.settings = type("S", (), {"browser": None, "flow": None, "run": None})()
        self.adapter = None
        self.store = None


def test_each_tab_starts_on_its_own_page(monkeypatch):
    started = []

    class FakeBrowser:
        def __init__(self, cfg):
            pass

        def connect(self):
            pass

        def close(self):
            pass

    class FakeEngine:
        def __init__(self, *args, page_index=0, page_stride=1, claims=None, **kwargs):
            started.append((page_index, page_stride))

        def run(self, own_lifecycle=True):
            return ""

    monkeypatch.setattr("autoapply.parallel.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("autoapply.parallel.BrowserSession", FakeBrowser)
    monkeypatch.setattr("autoapply.parallel.ClickThroughFlow", lambda *args, **kwargs: None)
    monkeypatch.setattr("autoapply.parallel.Engine", FakeEngine)

    run_tabs(_App(), 3)
    assert sorted(started) == [(0, 3), (1, 3), (2, 3)]
