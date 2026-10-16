"""Application flows: what to do with one opened application tab.

`ApplicationFlow` is the extension point. `ClickThroughFlow` handles only
Continue/Next/Submit pages and *skips* anything needing answers. A future
question-answering flow would subclass or replace it and be passed to the Engine;
it can reuse everything else (tabs, store, CAPTCHA pausing, verification).
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from .config import FlowSettings
from .control import Controller, StopRequested
from .models import FlowResult, Outcome, PageState, Reason
from .portals.base import PortalAdapter

log = logging.getLogger(__name__)

_BLOCKING = (PageState.CAPTCHA, PageState.LOGIN)


class ApplicationFlow(ABC):
    @abstractmethod
    def run(self, page: Any) -> FlowResult:
        """Drive `page` to a final result. May be called again on the same page after a pause."""


class ClickThroughFlow(ApplicationFlow):
    def __init__(self, adapter: PortalAdapter, cfg: FlowSettings, controller: Controller):
        self.adapter = adapter
        self.cfg = cfg
        self.controller = controller

    def run(self, page: Any) -> FlowResult:
        self._wait_for_application_ready(page)
        self._captcha_tries = 0
        steps = 0
        while True:
            self.controller.checkpoint()
            try:
                state = self._settled_state(page)
                if state is PageState.CONTINUE:
                    if steps >= self.cfg.max_steps:
                        return FlowResult(Outcome.NEEDS_MANUAL, Reason.TOO_MANY_STEPS)
                    if not self._advance(page):
                        return FlowResult(Outcome.NEEDS_MANUAL, Reason.STEP_STUCK)
                    steps += 1
                    continue
                if state is PageState.CAPTCHA:
                    if self._captcha_tries < 2 and self.adapter.clear_captcha(page):
                        self._captcha_tries += 1
                        continue
                    return self._terminal(state)
                self._captcha_tries = 0
                if state is PageState.SUBMIT:
                    return self._submit(page)
                return self._terminal(state)
            except StopRequested:
                raise
            except Exception:
                if self._closed(page):
                    return FlowResult(Outcome.NEEDS_MANUAL, Reason.TAB_CLOSED)
                raise

    # --- steps -------------------------------------------------------------
    def _wait_for_application_ready(self, page: Any) -> None:
        """Smart Apply opens a tab with a progress bar before Submit appears."""
        deadline = time.monotonic() + self.cfg.application_load_timeout_s
        while time.monotonic() < deadline:
            self.controller.checkpoint()
            state = self.adapter.classify(page)
            if state is not PageState.UNKNOWN:
                return
            time.sleep(self.cfg.poll_interval_s)

    def _settled_state(self, page: Any) -> PageState:
        """Classify, giving a loading/redirecting page a grace period before calling it UNKNOWN."""
        deadline = time.monotonic() + self.cfg.unknown_page_grace_s
        while True:
            state = self.adapter.classify(page)
            if state is not PageState.UNKNOWN or time.monotonic() >= deadline:
                return state
            time.sleep(self.cfg.poll_interval_s)

    def _advance(self, page: Any) -> bool:
        """Click Continue and wait for the page to move on. False if it did not."""
        before = self.adapter.signature(page)
        self.adapter.click_continue(page)
        deadline = time.monotonic() + self.cfg.step_timeout_s
        while time.monotonic() < deadline:
            if self.adapter.signature(page) != before:
                return True
            state = self.adapter.classify(page)
            if state in _BLOCKING or state is PageState.QUESTIONS:
                return True  # the loop top will pause or skip it
            time.sleep(self.cfg.poll_interval_s)
        return False

    def _submit(self, page: Any) -> FlowResult:
        """Click submit exactly once, then require positive confirmation."""
        if self.adapter.captcha_blocking_submit(page) and not self.adapter.clear_captcha(page):
            return FlowResult(Outcome.NEEDS_MANUAL, Reason.CAPTCHA, pause=True)
        log.info("Submitting application")
        self.adapter.click_submit(page)
        deadline = time.monotonic() + self.cfg.submit_verify_timeout_s
        while time.monotonic() < deadline:
            state = self.adapter.classify(page)
            if state is PageState.SUCCESS:
                return FlowResult(Outcome.SUBMITTED, Reason.CONFIRMED)
            if state in _BLOCKING:
                return self._terminal(state)
            if state is PageState.QUESTIONS:
                return FlowResult(Outcome.NEEDS_MANUAL, Reason.SUBMIT_REJECTED)
            time.sleep(self.cfg.poll_interval_s)
        return FlowResult(Outcome.NEEDS_MANUAL, Reason.SUBMIT_UNVERIFIED)

    def _terminal(self, state: PageState) -> FlowResult:
        if state is PageState.SUCCESS:
            return FlowResult(Outcome.SUBMITTED, Reason.CONFIRMED)
        if state is PageState.EXTERNAL:
            return FlowResult(Outcome.SKIPPED, Reason.EXTERNAL)
        if state is PageState.QUESTIONS:
            return self.adapter.questions_result()
        if state is PageState.ALREADY_APPLIED:
            return FlowResult(Outcome.SKIPPED, Reason.ALREADY_APPLIED)
        if state is PageState.CAPTCHA:
            return FlowResult(Outcome.NEEDS_MANUAL, Reason.CAPTCHA, pause=True)
        if state is PageState.LOGIN:
            return FlowResult(Outcome.NEEDS_MANUAL, Reason.LOGIN, pause=True)
        return FlowResult(Outcome.NEEDS_MANUAL, Reason.UNRECOGNIZED_PAGE, pause=self.cfg.pause_on_unknown_page)

    @staticmethod
    def _closed(page: Any) -> bool:
        try:
            return bool(page.is_closed())
        except Exception:
            return True
