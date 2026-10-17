"""Shared, portal-independent data types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    """Final result of one job. Exactly one of these is recorded per job."""

    SUBMITTED = "submitted"  # submission was *confirmed* on the page
    SKIPPED = "skipped"  # deliberately not applied (questions, company site, ...)
    NEEDS_MANUAL = "needs_manual"  # something unexpected; a human should look


class PageState(str, Enum):
    """What an application page looks like, as classified by a portal adapter."""

    CAPTCHA = "captcha"
    LOGIN = "login"
    EXTERNAL = "external"  # left the portal (company site)
    ALREADY_APPLIED = "already_applied"
    QUESTIONS = "questions"  # needs answers we cannot provide
    CONTINUE = "continue"  # a Next/Continue step
    SUBMIT = "submit"  # the final submit step
    SUCCESS = "success"  # confirmation page
    UNKNOWN = "unknown"


class ApplyKind(str, Enum):
    EASY = "easy"
    EXTERNAL = "external"
    ALREADY_APPLIED = "already_applied"
    NONE = "none"


class Reason:
    """Machine-readable reasons stored in the CSV and shown in the dashboard."""

    IN_PROGRESS = "in_progress"
    CONFIRMED = "submission_confirmed"
    EXTERNAL = "external_company_site"
    QUESTIONS = "requires_questions"
    ALREADY_APPLIED = "already_applied"
    NO_APPLY_BUTTON = "no_apply_button"
    CAPTCHA = "captcha"
    LOGIN = "login_required"
    UNRECOGNIZED_PAGE = "unrecognized_page"
    STEP_STUCK = "step_did_not_advance"
    TOO_MANY_STEPS = "too_many_steps"
    SUBMIT_UNVERIFIED = "submit_unverified"
    SUBMIT_REJECTED = "submit_rejected"
    TAB_CLOSED = "tab_closed_before_confirmation"
    APPLY_NO_EFFECT = "apply_click_had_no_effect"
    PANE_MISMATCH = "job_detail_did_not_match_card"
    ROLE = "role_not_in_target"
    EXPERIENCE = "experience_above_target"
    ERROR = "error"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class JobCard:
    """A job found on a listing page. `handle` is adapter-private."""

    key: str
    title: str
    company: str = ""
    location: str = ""
    handle: Any = field(default=None, repr=False, compare=False)


@dataclass
class ApplyAction:
    kind: ApplyKind
    locator: Any = None  # adapter-private; passed back to the browser to click


@dataclass(frozen=True)
class FlowResult:
    outcome: Outcome
    reason: str
    pause: bool = False  # True => stop and wait for the human before continuing


@dataclass
class JobRecord:
    """One row in the applications file."""

    job_key: str
    title: str
    company: str
    location: str
    outcome: Outcome
    reason: str
    portal: str = ""
    job_url: str = ""  # view job on the portal (open in browser to apply manually)
    apply_url: str = ""  # Indeed Apply link or smartapply tab URL when we had one
    url: str = ""  # same as apply_url when set, else job_url (kept for older exports)
    timestamp: str = field(default_factory=_now)
