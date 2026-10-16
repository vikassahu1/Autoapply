"""Browser layer: attaches to the dedicated Chrome over CDP and manages tabs.

Only this module imports Playwright. It never touches tabs it did not open
itself: the listing tab (`open_worker_tab`) and tabs spawned by its own clicks.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .config import BrowserSettings

log = logging.getLogger(__name__)


class BrowserUnavailable(RuntimeError):
    pass


class BrowserSession:
    def __init__(self, cfg: BrowserSettings, follow_timeout_s: float = 20.0):
        self.cfg = cfg
        self.follow_timeout_s = follow_timeout_s
        self._pw = None
        self._browser = None
        self.context = None

    def connect(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(self.cfg.cdp_url)
        except Exception as exc:
            self._pw.stop()
            self._pw = None
            raise BrowserUnavailable(
                f"Could not attach to Chrome at {self.cfg.cdp_url}. "
                "Start it with: python -m autoapply launch-browser"
            ) from exc
        self.context = self._browser.contexts[0]

    def close(self) -> None:
        """Disconnect only. The Chrome window stays open."""
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass

    # --- tabs ----------------------------------------------------------------
    def open_worker_tab(self, url: str) -> Any:
        page = self.context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        return page

    def go_to(self, page: Any, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded")

    def bring_to_front(self, page: Any) -> None:
        try:
            page.bring_to_front()
        except Exception:
            pass

    def close_page(self, page: Any) -> None:
        try:
            if not page.is_closed():
                page.close()
        except Exception:
            log.debug("Could not close page", exc_info=True)

    def click_and_follow(self, page: Any, locator: Any, ready=None) -> Any | None:
        """Click `locator` and return the page that now shows the result.

        Returns a newly opened tab, `page` itself if it navigated or `ready()`
        became true (in-page dialog), or None if nothing happened in time.
        """
        before_pages = set(self.context.pages)
        before_url = page.url
        locator.click(timeout=5000)
        deadline = time.monotonic() + self.follow_timeout_s
        while time.monotonic() < deadline:
            # Prefer the page we clicked. Another listing tab can open while we
            # wait, and that tab is not this application's result.
            if ready is not None:
                try:
                    if ready():
                        return page
                except Exception:
                    log.debug("Apply-ready check failed", exc_info=True)
            if page.url != before_url:
                return page
            new_pages = [p for p in self.context.pages if p not in before_pages]
            if new_pages:
                new_page = new_pages[-1]
                try:
                    new_page.wait_for_load_state("domcontentloaded", timeout=10000)
                except Exception:
                    pass
                return new_page
            # NOT time.sleep(): Playwright's sync API only processes browser events (new tabs,
            # navigations) while a Playwright call is running, so a plain sleep can miss a popup.
            page.wait_for_timeout(200)
        log.warning(
            "Click produced no new tab or navigation. Tabs now: %s",
            [p.url for p in self.context.pages],
        )
        return None
