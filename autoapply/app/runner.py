"""Wires settings, adapter, store, browser and engine together; owns the worker thread."""
from __future__ import annotations

import logging
import threading

from ..browser import BrowserSession, BrowserUnavailable
from ..config import Settings
from ..core.control import Controller
from ..core.engine import Engine
from ..core.flows import ClickThroughFlow
from ..core.notify import notify_attention
from ..core.store import JobStore
from ..portals.base import PortalAdapter

log = logging.getLogger(__name__)


class AutoApplyApp:
    def __init__(self, settings: Settings, adapter: PortalAdapter, store: JobStore):
        self.settings = settings
        self.adapter = adapter
        self.store = store
        self.controller = Controller(on_pause=notify_attention)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # --- controls (called from the UI thread) ----------------------------------
    def start(self) -> bool:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self.controller.begin()
            self._thread = threading.Thread(target=self._worker, name="autoapply", daemon=True)
            self._thread.start()
            return True

    def pause(self) -> None:
        self.controller.pause("Paused by you")

    def resume(self) -> None:
        self.controller.resume()

    def stop(self) -> None:
        self.controller.request_stop()

    def status(self) -> dict:
        data = self.controller.snapshot()
        data["portal"] = self.adapter.name
        data["all_time"] = self.store.counts(self.adapter.name)
        data["tabs"] = self.adapter.parallel_tabs()
        data["debug_port"] = self.settings.browser.debug_port
        data["storage_path"] = str(getattr(self.store, "path", ""))
        data["export_path"] = str(getattr(self.store, "export_path", ""))
        return data

    # --- worker thread (all Playwright objects live and die here) --------------
    def _worker(self) -> None:
        if self.adapter.parallel_tabs() > 1:
            from ..core.parallel import run_tabs

            run_tabs(self, self.adapter.parallel_tabs())
            return
        browser = BrowserSession(self.settings.browser)
        try:
            browser.connect()
        except BrowserUnavailable as exc:
            log.error("%s", exc)
            self.controller.finish(str(exc))
            return
        try:
            flow = ClickThroughFlow(self.adapter, self.settings.flow, self.controller)
            engine = Engine(self.adapter, browser, self.store, flow, self.controller, self.settings.run)
            engine.run()
        finally:
            browser.close()
