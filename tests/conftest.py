"""Scripted fakes so flow/engine logic is tested without a browser."""
from __future__ import annotations

import pytest

from autoapply.config import FlowSettings, RunSettings
from autoapply.control import Controller
from autoapply.flows import ClickThroughFlow
from autoapply.models import ApplyAction, ApplyKind, JobCard, PageState
from autoapply.portals.base import PortalAdapter
from autoapply.store import CsvJobStore

S = PageState


class ScriptedPage:
    """A page that walks through `steps`. Continue/Submit clicks advance it."""

    def __init__(self, steps, url="https://smartapply.indeed.com/apply", stuck=False):
        self.steps = list(steps)
        self.i = 0
        self.url = url
        self.stuck = stuck  # Continue does nothing (e.g. validation error)
        self.solved = False  # a CAPTCHA step stays until the "human" solves it
        self.closed = False
        self.continue_clicks = 0
        self.submit_clicks = 0

    def is_closed(self):
        return self.closed


class ScriptedAdapter(PortalAdapter):
    name = "fake"
    start_url = "https://fake.test/"

    def __init__(self, jobs=(), apply_kinds=None, fail_on=()):
        self.jobs = list(jobs)
        self.apply_kinds = apply_kinds or {}
        self.fail_on = set(fail_on)
        self.current = None

    # listing
    def on_listing(self, page): return True
    def list_jobs(self, page): return list(self.jobs)
    def load_more(self, page): return False
    def select_job(self, page, card):
        self.current = card.key
        return True
    def find_apply(self, page):
        if self.current in self.fail_on:
            raise RuntimeError("boom")
        return ApplyAction(self.apply_kinds.get(self.current, ApplyKind.EASY), self.current)

    # application pages
    def classify(self, page):
        state = page.steps[min(page.i, len(page.steps) - 1)]
        if state is S.CAPTCHA and page.solved:
            page.i += 1
            state = page.steps[min(page.i, len(page.steps) - 1)]
        return state
    def signature(self, page): return str(page.i)
    def click_continue(self, page):
        page.continue_clicks += 1
        if not page.stuck:
            page.i += 1
    def click_submit(self, page):
        page.submit_clicks += 1
        page.i += 1


class FakeBrowser:
    """click_and_follow returns the scripted page registered for the clicked job key."""

    def __init__(self, pages):
        self.pages = pages  # job key -> ScriptedPage | None
        self.closed = []
        self.fronted = []
        self.clicks = []
        self.listing = ScriptedPage([S.UNKNOWN], url="https://fake.test/")

    def open_worker_tab(self, url): return self.listing
    def go_to(self, page, url): page.url = url
    def bring_to_front(self, page): self.fronted.append(page)
    def close_page(self, page): self.closed.append(page)
    def click_and_follow(self, page, locator, ready=None):
        self.clicks.append(locator)
        if ready is not None and ready():
            return page
        return self.pages.get(locator)


FAST_FLOW = FlowSettings(
    max_steps=5, poll_interval_s=0.01, step_timeout_s=0.1,
    unknown_page_grace_s=0.05, submit_verify_timeout_s=0.1, pause_on_unknown_page=True,
)
FAST_RUN = RunSettings(max_applications=0, delay_between_jobs_s=(0.0, 0.0), max_consecutive_errors=2)


def card(key, title=None, company="Acme"):
    return JobCard(key=key, title=title or f"Job {key}", company=company, location="Pune", handle=key)


@pytest.fixture
def controller():
    c = Controller()
    c.begin()
    return c


@pytest.fixture
def adapter():
    return ScriptedAdapter()


@pytest.fixture
def flow(adapter, controller):
    return ClickThroughFlow(adapter, FAST_FLOW, controller)


@pytest.fixture
def store(tmp_path):
    return CsvJobStore(tmp_path / "applications.csv")
