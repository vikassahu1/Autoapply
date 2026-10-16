"""The portal-independent automation loop.

It depends only on: a PortalAdapter (what the pages mean), a browser object
(open/close/click-and-follow tabs), a JobStore, an ApplicationFlow and a Controller.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Callable

from .browser import target_closed
from .config import RunSettings
from .control import Controller, StopRequested
from .flows import ApplicationFlow
from .models import ApplyKind, FlowResult, JobCard, JobRecord, Outcome, Reason
from .portals.base import PortalAdapter
from .store import JobStore
from .urls import same_results_page

log = logging.getLogger(__name__)


def _page_closed(page: Any) -> bool:
    try:
        return bool(page.is_closed())
    except Exception:
        return True


def _navigation_refused(exc: BaseException) -> bool:
    """True when the site answered the navigation with an HTTP error page."""
    text = str(exc)
    return "ERR_HTTP_RESPONSE_CODE_FAILURE" in text or "net::ERR_" in text


class JobClaims:
    """Keys a run has already taken, shared safely across listing tabs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: set[str] = set()

    def take(self, key: str) -> bool:
        with self._lock:
            if key in self._keys:
                return False
            self._keys.add(key)
            return True


class Engine:
    def __init__(
        self,
        adapter: PortalAdapter,
        browser: Any,
        store: JobStore,
        flow: ApplicationFlow,
        controller: Controller,
        cfg: RunSettings,
        sleep: Callable[[float], None] = time.sleep,
        page_index: int = 0,
        page_stride: int = 1,
        claims: JobClaims | None = None,
    ):
        self.adapter = adapter
        self.browser = browser
        self.store = store
        self.flow = flow
        self.controller = controller
        self.cfg = cfg
        self._sleep = sleep
        self._page_index = page_index
        self._page_start = page_index
        self._page_stride = max(1, page_stride)
        self._label = f"Tab {page_index + 1}: " if page_stride > 1 else ""
        self._claims = claims or JobClaims()
        self._watch_first_page = False
        self._caught_up = False
        self._cooldown_s = 0
        self._done: set[str] = set()  # in-memory guard in case the store ever fails
        self._consecutive_errors = 0

    # --- entry point -------------------------------------------------------
    def run(self, *, own_lifecycle: bool = True) -> str:
        if own_lifecycle:
            self.controller.begin()
        error = ""
        try:
            paged_url = self.adapter.result_page_url(self._page_index)
            url = paged_url or self.adapter.start_url
            if self._label:
                log.info("%sstarting on results page %d", self._label, self._page_index + 1)
            listing = self.browser.open_worker_tab(url)
            if paged_url:
                self._page_has_cards(listing)
            self._loop(listing)
        except StopRequested:
            log.info("%sStopped by user", self._label)
        except Exception as exc:  # unrecoverable: listing tab gone, browser died, ...
            log.exception("%sAutomation aborted", self._label)
            error = f"{type(exc).__name__}: {exc}"
        finally:
            if own_lifecycle:
                self.controller.finish(error)
        return error

    # --- main loop -----------------------------------------------------------
    def _loop(self, listing: Any) -> None:
        handled = 0
        while True:
            self.controller.checkpoint()
            try:
                if _page_closed(listing):
                    listing = self._replace_listing(listing)
                    continue
                self._ensure_on_listing(listing)
                self._clear_blockers(listing)
                card = self._next_pending(listing)
            except Exception as exc:
                if not target_closed(exc):
                    raise
                log.warning("%sListing tab closed. Opening it again.", self._label)
                listing = self._replace_listing(listing)
                self._idle(10)
                continue
            if card is None:
                # Once the older pages are done, do not scroll or reload them.
                # Already-applied cards are skipped from the snapshot, not reopened.
                if not self._watch_first_page and self.adapter.reveal_more(listing):
                    continue
                if self._advance_listing(listing):
                    continue
                if self.adapter.keep_searching():
                    if self._cooldown_s:
                        log.warning(
                            "%sLinkedIn asked us to slow down (429). Waiting %.0fs before the next page.",
                            self._label,
                            self._cooldown_s,
                        )
                        self._idle(self._cooldown_s)
                        self._cooldown_s = 0
                    if not self._caught_up:
                        log.info(
                            "%sCaught up. Already-applied jobs are skipped. Watching the newest page for new ones.",
                            self._label,
                        )
                        self._caught_up = True
                    self._rewind(listing)
                    self._idle(12)
                    continue
                log.info("%sNo more eligible jobs found. Done.", self._label)
                return
            if self.cfg.max_applications and handled >= self.cfg.max_applications:
                log.info("Reached max_applications=%d. Stopping.", self.cfg.max_applications)
                return
            self._process(listing, card)
            handled += 1
            self._pace()

    def _replace_listing(self, listing: Any) -> Any:
        """The listing tab is gone. Open a fresh one on the page this worker owns."""
        try:
            self.browser.close_page(listing)
        except Exception:
            pass
        return self.browser.open_worker_tab(self._listing_url())

    def _listing_url(self) -> str:
        return self.adapter.result_page_url(self._page_index) or self.adapter.start_url

    def _ensure_on_listing(self, listing: Any) -> None:
        if not self.adapter.on_listing(listing):
            log.info("%sListing tab moved away; returning to the job feed", self._label)
            self._try_go(listing, self._listing_url())

    def _advance_listing(self, listing: Any) -> bool:
        """Open the next results page this tab owns. False when that page has no jobs.

        After the first full pass, stay on this tab's first page. New posts show up
        there, and walking the older pages again only re-reads jobs already saved.
        """
        if self._watch_first_page:
            return False
        nxt = self._page_index + self._page_stride
        url = self.adapter.result_page_url(nxt)
        if not url:
            return self.adapter.load_more(listing)
        log.info("%sOpening results page %d", self._label, nxt + 1)
        if not self._try_go(listing, url):
            return False
        self._page_index = nxt
        return self._page_has_cards(listing)

    def _try_go(self, listing: Any, url: str) -> bool:
        """Open `url`. A refused response is a skip, not a dead tab."""
        try:
            self.browser.go_to(listing, url)
            return True
        except Exception as exc:
            if not _navigation_refused(exc):
                raise
            log.warning(
                "%sLinkedIn refused that page (often HTTP 429). This tab will pause, then stay with newer jobs.",
                self._label,
            )
            self._cooldown_s = 45
            return False

    def _page_has_cards(self, listing: Any) -> bool:
        deadline = time.monotonic() + self.adapter.page_settle_timeout_s()
        while True:
            self.controller.checkpoint()
            status = self.adapter.listing_status(listing)
            if status == "jobs":
                return True
            if status == "empty":
                return False
            if time.monotonic() >= deadline:
                return False
            self._sleep(0.4)

    def _rewind(self, listing: Any) -> None:
        self._watch_first_page = True
        self._page_index = self._page_start
        url = self._listing_url()
        current = ""
        try:
            current = listing.url or ""
        except Exception:
            current = ""
        if same_results_page(current, url):
            return
        self._try_go(listing, url)

    def _clear_blockers(self, listing: Any) -> None:
        """Pause while the listing itself shows a CAPTCHA or login wall."""
        while (blocker := self.adapter.blocker(listing)) is not None:
            self._pause_for(listing, f"{blocker.value} on the job listing page")
            self.controller.checkpoint()

    def _next_pending(self, listing: Any) -> JobCard | None:
        for card in self.adapter.list_jobs(listing):
            if card.key in self._done or self.store.seen(card.key):
                continue
            if not self._claims.take(card.key):
                continue
            return card
        return None

    # --- one job -------------------------------------------------------------
    def _process(self, listing: Any, card: JobCard) -> None:
        self.controller.set_current(f"{card.title} · {card.company}")
        log.info("Processing: %s @ %s [%s]", card.title, card.company, card.key)
        self._done.add(card.key)
        # Written first so a crash mid-application can never lead to a second attempt.
        self.store.record(self._build_record(card, Outcome.NEEDS_MANUAL, Reason.IN_PROGRESS))

        page = None
        apply_link = ""
        try:
            result, page, apply_link = self._attempt(listing, card)
        except StopRequested:
            raise
        except Exception as exc:
            log.exception("Error while processing %s", card.title)
            result = FlowResult(Outcome.NEEDS_MANUAL, f"{Reason.ERROR}: {type(exc).__name__}: {exc}")

        record = self._build_record(
            card, result.outcome, result.reason, page_url=self._url(page), apply_link=apply_link
        )
        self.store.record(record)
        self.controller.add_result(record)
        log.info("Result: %s (%s) - %s", result.outcome.value, result.reason, card.title)

        self._cleanup(listing, page)
        self._track_errors(result)

    def _attempt(self, listing: Any, card: JobCard) -> tuple[FlowResult, Any, str]:
        skip = self.adapter.eligibility(card)
        if skip:
            return FlowResult(Outcome.SKIPPED, skip), None, ""

        if not self.adapter.select_job(listing, card):
            return FlowResult(Outcome.NEEDS_MANUAL, Reason.PANE_MISMATCH), None, ""

        skip = self.adapter.detail_skip_reason(listing)
        if skip:
            return FlowResult(Outcome.SKIPPED, skip), None, ""

        action = self.adapter.find_apply(listing)
        apply_link = self._apply_link_from_action(action)
        if action.kind is ApplyKind.EXTERNAL:
            return FlowResult(Outcome.SKIPPED, Reason.EXTERNAL), None, apply_link
        if action.kind is ApplyKind.ALREADY_APPLIED:
            return FlowResult(Outcome.SKIPPED, Reason.ALREADY_APPLIED), None, apply_link
        if action.kind is ApplyKind.NONE:
            return FlowResult(Outcome.SKIPPED, Reason.NO_APPLY_BUTTON), None, apply_link

        page = self.browser.click_and_follow(
            listing, action.locator, ready=lambda: self.adapter.opened_application(listing)
        )
        if page is None and self.adapter.opened_application(listing):
            page = listing
        if page is None:
            return FlowResult(Outcome.NEEDS_MANUAL, Reason.APPLY_NO_EFFECT), None, apply_link
        return self._run_flow_with_pauses(card, page, apply_link), page, apply_link

    def _run_flow_with_pauses(self, card: JobCard, page: Any, apply_link: str) -> FlowResult:
        while True:
            result = self.flow.run(page)
            if not result.pause:
                return result
            # Keep the file honest if the user stops while we wait.
            self.store.record(
                self._build_record(
                    card, result.outcome, result.reason, page_url=self._url(page), apply_link=apply_link
                )
            )
            self.browser.bring_to_front(page)
            self._pause_for(page, f"{result.reason} - {card.title} @ {card.company}")
            self.controller.checkpoint()  # blocks until Resume; raises on Stop

    def _pause_for(self, page: Any, reason: str) -> None:
        log.warning("PAUSED: %s. Resolve it in the browser, then press Resume.", reason)
        self.controller.pause(reason)

    # --- helpers ---------------------------------------------------------------
    def _cleanup(self, listing: Any, page: Any) -> None:
        try:
            if page is None:
                return
            if page is listing:  # same tab: in-page dialog, or the feed navigated away
                self.adapter.dismiss_application(page)
                if not self.adapter.keep_listing_after_apply(page):
                    self.browser.go_to(listing, self.adapter.start_url)
            else:
                # A tab opened by this listing is the application. Another worker's
                # listing has no opener and must stay open.
                if not hasattr(page, "opener") or page.opener is listing:
                    self.browser.close_page(page)
        except Exception:
            log.exception("Cleanup failed (continuing)")

    def _track_errors(self, result: FlowResult) -> None:
        if result.reason.startswith(Reason.ERROR):
            self._consecutive_errors += 1
        else:
            self._consecutive_errors = 0
        if self._consecutive_errors >= self.cfg.max_consecutive_errors:
            self._consecutive_errors = 0
            self.controller.pause("Several unexpected errors in a row - check the log and the browser")
            self.controller.checkpoint()

    def _idle(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            self.controller.checkpoint()
            step = min(0.25, remaining)
            self._sleep(step)
            remaining -= step

    def _pace(self) -> None:
        low, high = self.cfg.delay_between_jobs_s
        remaining = random.uniform(low, high)
        while remaining > 0:
            self.controller.checkpoint()
            step = min(0.25, remaining)
            self._sleep(step)
            remaining -= step

    def _build_record(
        self,
        card: JobCard,
        outcome: Outcome,
        reason: str,
        page_url: str = "",
        apply_link: str = "",
    ) -> JobRecord:
        job_url = self.adapter.job_view_url(card)
        apply_url = page_url or apply_link or job_url
        return JobRecord(
            card.key,
            card.title,
            card.company,
            card.location,
            outcome,
            reason,
            portal=self.adapter.name,
            job_url=job_url,
            apply_url=apply_url,
            url=apply_url,
        )

    @staticmethod
    def _apply_link_from_action(action) -> str:
        if action.locator is None:
            return ""
        try:
            href = action.locator.get_attribute("href", timeout=500)
        except Exception:
            return ""
        if href and href.startswith("http"):
            return href
        return ""

    @staticmethod
    def _url(page: Any) -> str:
        try:
            return page.url if page is not None else ""
        except Exception:
            return ""
