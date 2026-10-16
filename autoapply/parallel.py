"""Run one portal across several listing tabs.

Playwright's sync API is bound to the thread that created it, and LinkedIn
keeps Easy Apply in a dialog on that listing page. Each tab therefore gets
its own browser connection and its own dialog. Pages are split so the tabs
do not apply to the same results: with 2 tabs, tab 1 takes pages 1, 3, 5…,
tab 2 takes 2, 4, 6….
"""
from __future__ import annotations

import logging
import threading
import time

from .browser import BrowserSession, BrowserUnavailable
from .control import StopRequested
from .engine import Engine, JobClaims
from .flows import ClickThroughFlow

log = logging.getLogger(__name__)


def run_tabs(app, slots: int) -> None:
    """Apply on `slots` listing tabs until every tab has stopped."""
    slots = max(1, slots)
    claims = JobClaims()
    errors: list[str] = []
    finished_clean: list[int] = []
    lock = threading.Lock()
    log.info("Applying on %d tabs, splitting result pages across them", slots)

    def work(slot: int) -> None:
        if slot:
            time.sleep(slot * 8)
        try:
            app.controller.checkpoint()
        except StopRequested:
            return
        browser = BrowserSession(app.settings.browser)
        try:
            browser.connect()
        except BrowserUnavailable as exc:
            log.error("Tab %s could not attach to Chrome: %s", slot + 1, exc)
            with lock:
                errors.append(str(exc))
            return
        try:
            flow = ClickThroughFlow(app.adapter, app.settings.flow, app.controller)
            engine = Engine(
                app.adapter,
                browser,
                app.store,
                flow,
                app.controller,
                app.settings.run,
                page_index=slot,
                page_stride=slots,
                claims=claims,
            )
            error = engine.run(own_lifecycle=False)
            with lock:
                if error:
                    errors.append(error)
                else:
                    finished_clean.append(slot)
        finally:
            browser.close()

    threads = [
        threading.Thread(target=work, args=(index,), name=f"autoapply-tab-{index + 1}", daemon=True)
        for index in range(slots)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        while thread.is_alive():
            thread.join(timeout=0.5)
    # One tab dying should not hide a run the others finished. Surface an error
    # only when every tab failed to start or aborted.
    app.controller.finish("" if finished_clean else (errors[0] if errors else ""))
