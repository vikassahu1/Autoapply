"""Browser layer: attaches to the dedicated Chrome over CDP and manages tabs.

Only this module imports Playwright. It never touches tabs it did not open
itself: the listing tab (`open_worker_tab`) and tabs spawned by its own clicks.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..config import BrowserSettings

log = logging.getLogger(__name__)


def _click(locator: Any, timeout: int, force: bool = False, no_wait_after: bool = False) -> None:
    kwargs: dict = {"timeout": timeout}
    if force:
        kwargs["force"] = True
    if no_wait_after:
        kwargs["no_wait_after"] = True
    try:
        locator.click(**kwargs)
    except TypeError:
        locator.click(timeout=timeout)


def quiet_click(locator: Any, timeout: int = 5000, force: bool = False, native: bool = False) -> None:
    """Click in the page. Playwright's mouse click raises the Chrome window.

    Use native=True only when a widget ignores DOM clicks (reCAPTCHA / Buster).
    """
    evaluate = getattr(locator, "evaluate", None)
    if evaluate is None:
        _click(locator, timeout, force=force)
        return
    if native:
        _click(locator, timeout, force=True, no_wait_after=True)
        return
    wait = getattr(locator, "wait_for", None)
    if wait is not None:
        try:
            wait(state="attached", timeout=timeout)
        except Exception:
            pass
    locator.evaluate("el => el.click()")


def quiet_scroll(locator: Any, timeout: int = 2000) -> None:
    """Scroll in the page. Playwright's scroll_into_view_if_needed raises Chrome."""
    evaluate = getattr(locator, "evaluate", None)
    if evaluate is not None:
        try:
            locator.evaluate("el => el.scrollIntoView({block:'nearest', inline:'nearest'})")
            return
        except Exception:
            pass
    scroll = getattr(locator, "scroll_into_view_if_needed", None)
    if scroll is not None:
        try:
            locator.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            pass


class BrowserUnavailable(RuntimeError):
    pass


def target_closed(exc: BaseException) -> bool:
    """True when the page or browser this call was using has already gone away."""
    return type(exc).__name__ == "TargetClosedError" or "has been closed" in str(exc)


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

    def open_worker_tab(self, url: str) -> Any:
        page = self._new_background_page()
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                page.goto(url, wait_until="domcontentloaded")
                return page
            except Exception as exc:
                last_error = exc
                if "net::ERR_" not in str(exc):
                    raise
                log.warning("Listing page did not load; waiting before trying again")
                page.wait_for_timeout(20000)
        assert last_error is not None
        raise last_error

    def _new_background_page(self) -> Any:
        """Open a tab without asking Chrome to come to the front."""
        before = list(self.context.pages)
        try:
            if before:
                cdp = self.context.new_cdp_session(before[0])
            else:
                cdp = self._browser.new_browser_cdp_session()
            cdp.send("Target.createTarget", {"url": "about:blank", "background": True})
            try:
                cdp.detach()
            except Exception:
                pass
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                extra = [page for page in self.context.pages if page not in before]
                if extra:
                    return extra[-1]
                if before:
                    before[0].wait_for_timeout(50)
                else:
                    time.sleep(0.05)
        except Exception:
            log.debug("Could not open a background tab; using a normal tab", exc_info=True)
        return self.context.new_page()

    def go_to(self, page: Any, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded")

    def bring_to_front(self, page: Any) -> None:
        """Intentionally a no-op: raising the window interrupts other work."""
        return

    def close_page(self, page: Any) -> None:
        try:
            if not page.is_closed():
                page.close()
        except Exception:
            log.debug("Could not close page", exc_info=True)

    def click_and_follow(self, page: Any, locator: Any, ready=None, accept=None) -> Any | None:
        """Click `locator` and return the page that now shows the result.

        Returns a newly opened tab, `page` itself if it navigated or `ready()`
        became true (in-page dialog), or None if nothing happened in time.
        `accept(candidate)` claims a tab this click opened even when the site
        strips `window.opener` (Indeed Smart Apply does this).
        """
        before_pages = set(self.context.pages)
        before_urls = {id(candidate): candidate.url for candidate in before_pages}
        before_url = page.url
        quiet_click(locator, timeout=5000)
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
            opened = [candidate for candidate in self.context.pages if candidate not in before_pages]
            # A tab this click opened. Indeed Apply uses noopener, so opener is
            # empty; `accept` is what recognizes that application tab.
            owned = [candidate for candidate in opened if _opened_by(candidate, page)]
            if not owned:
                owned = [candidate for candidate in opened if _accepted(candidate, accept)]
            if not owned:
                for candidate in self.context.pages:
                    if candidate is page or candidate not in before_pages:
                        continue
                    if candidate.url != before_urls.get(id(candidate)) and _accepted(candidate, accept):
                        owned = [candidate]
                        break
            if owned:
                new_page = owned[-1]
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


def _opened_by(page: Any, opener: Any) -> bool:
    try:
        return page.opener is opener
    except Exception:
        return False


def _accepted(page: Any, accept) -> bool:
    if accept is None:
        return False
    try:
        return bool(accept(page))
    except Exception:
        return False
