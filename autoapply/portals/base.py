"""The contract between the portal-independent engine and a job portal.

To add a portal: subclass `PortalAdapter`, put its selectors/texts in
`config/portals/<name>.toml`, and register it in `portals/registry.py`.
The engine and flow runner never import portal-specific code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..core.models import ApplyAction, FlowResult, JobCard, Outcome, PageState, Reason


class PortalAdapter(ABC):
    name: str
    start_url: str

    # --- listing page ----------------------------------------------------
    @abstractmethod
    def on_listing(self, page: Any) -> bool:
        """True if `page` is currently showing the job listing we iterate over."""

    @abstractmethod
    def list_jobs(self, page: Any) -> list[JobCard]:
        """Jobs currently rendered on the listing, in display order."""

    @abstractmethod
    def load_more(self, page: Any) -> bool:
        """Try to reveal more jobs (scroll / next page). True only if new jobs appeared."""

    @abstractmethod
    def select_job(self, page: Any, card: JobCard) -> bool:
        """Open the job's details. Return True only if the details shown belong to `card`."""

    @abstractmethod
    def find_apply(self, page: Any) -> ApplyAction:
        """Inspect the open job's details and say what applying would do."""

    # --- application pages -----------------------------------------------
    @abstractmethod
    def classify(self, page: Any) -> PageState:
        """Classify the current page of an application (or the listing, for blockers)."""

    @abstractmethod
    def signature(self, page: Any) -> str:
        """A string that changes when the application advances to a new step."""

    @abstractmethod
    def click_continue(self, page: Any) -> None: ...

    @abstractmethod
    def click_submit(self, page: Any) -> None: ...

    # --- shared helpers ----------------------------------------------------
    def blocker(self, page: Any) -> PageState | None:
        """CAPTCHA / login wall on any page, else None."""
        state = self.classify(page)
        return state if state in (PageState.CAPTCHA, PageState.LOGIN) else None

    def diagnose(self, page: Any) -> list[str]:
        """Human-readable selector health report for the `doctor` command."""
        return []

    def job_view_url(self, card: JobCard) -> str:
        """Stable link to the job posting on this portal (for manual follow-up)."""
        return ""

    def eligibility(self, card: JobCard) -> str | None:
        """Skip reason if this card should not be opened. None means try it."""
        return None

    def waits_for_user(self, state: PageState) -> bool:
        """True to pause the run until Resume. False records the job and moves on."""
        return state in (PageState.CAPTCHA, PageState.LOGIN, PageState.UNKNOWN)

    def keeps_going(self) -> bool:
        """True when a listing error should not abort or pause the whole run."""
        return False

    def detail_skip_reason(self, page: Any) -> str | None:
        """Skip reason after the job details are open (for example years of experience)."""
        return None

    def opened_application(self, page: Any) -> bool:
        """True when Apply opened an in-page dialog instead of a new tab."""
        return False

    def dismiss_application(self, page: Any) -> None:
        """Close an in-page application dialog. Default: nothing to close."""

    def parallel_tabs(self) -> int:
        """How many listing tabs to run at once. 1 keeps a single tab."""
        return 1

    def result_page_url(self, page_index: int) -> str | None:
        """URL for a 0-based results page. None means use `load_more` instead."""
        return None

    def reveal_more(self, page: Any) -> bool:
        """Scroll the current page. True only when new cards appeared."""
        return False

    def listing_status(self, page: Any) -> str:
        """'jobs' when cards are visible, 'empty' when the feed says so, else 'loading'."""
        try:
            return "jobs" if self.list_jobs(page) else "loading"
        except Exception:
            return "loading"

    def page_settle_timeout_s(self) -> float:
        """How long to wait for a results page to show cards or an empty state."""
        return 12.0

    def keep_searching(self) -> bool:
        """True to keep the run alive when a page has no new jobs, until the user presses Stop."""
        return False

    def keep_listing_after_apply(self, page: Any) -> bool:
        """True to stay on the current feed after a dialog apply. Indeed reloads instead."""
        return False

    def captcha_blocking_submit(self, page: Any) -> bool:
        """True when a CAPTCHA is sitting on the submit step and must be cleared first."""
        return False

    def clear_captcha(self, page: Any) -> bool:
        """True when this portal got past the CAPTCHA. False leaves the run paused."""
        return False

    def questions_result(self) -> FlowResult:
        """What to do when the application asks something we will not guess.

        Both Indeed and LinkedIn skip, save the link, and move on.
        """
        return FlowResult(Outcome.SKIPPED, Reason.QUESTIONS)
