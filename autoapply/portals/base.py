"""The contract between the portal-independent engine and a job portal.

To add a portal: subclass `PortalAdapter`, put its selectors/texts in
`config/portals/<name>.toml`, and register it in `portals/__init__.py`.
The engine and flow runner never import portal-specific code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import ApplyAction, JobCard, PageState


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
