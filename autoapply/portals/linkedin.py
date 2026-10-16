"""LinkedIn Easy Apply adapter.

All selectors, button names, and role/experience filters come from
config/portals/linkedin.toml. Easy Apply opens a dialog on the jobs page;
Next and Submit are clicked inside the Easy Apply dialog. A real unanswered
question pauses and leaves that dialog open.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from ..models import ApplyAction, ApplyKind, FlowResult, JobCard, Outcome, PageState, Reason
from .base import PortalAdapter

log = logging.getLogger(__name__)

_JOB_ID_IN_HREF = re.compile(r"/jobs/view/(\d+)")

# Required fields still empty mean we should not guess. LinkedIn questions are often
# custom widgets (not <input required>), and a chosen resume uses a "Selected" card.
_UNANSWERED_REQUIRED_JS = """
() => {
  const root = document.querySelector("[data-test-modal-id='easy-apply-modal']")
    || document.querySelector(".jobs-easy-apply-modal")
    || document;
  const visible = el => {
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const required = el => el.required || el.getAttribute("aria-required") === "true";
  const answered = group => !!(
    group.querySelector("input:checked, textarea, select") && (
      group.querySelector("input:checked")
      || String((group.querySelector("textarea, select, input[type=text]") || {}).value || "").trim()
    )
    || group.querySelector("[aria-checked='true'], [aria-pressed='true'], [aria-label='Selected'], [class*='--selected']")
  );
  let missing = 0;
  root.querySelectorAll(
    "input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=file]), textarea, select"
  ).forEach(el => {
    if (visible(el) && !el.disabled && required(el) && !String(el.value || "").trim()) missing++;
  });
  const groups = {};
  root.querySelectorAll("input[type=radio], input[type=checkbox]").forEach((el, i) => {
    if (!visible(el) || el.disabled) return;
    const id = el.type + ":" + (el.name || ("#" + i));
    const g = groups[id] || (groups[id] = {required: false, checked: false});
    g.required = g.required || required(el);
    g.checked = g.checked || el.checked;
  });
  Object.values(groups).forEach(g => { if (g.required && !g.checked) missing++; });
  root.querySelectorAll(".fb-dash-form-element__label-title--is-required, [class*='label-title--is-required']").forEach(label => {
    const group = label.closest(".fb-dash-form-element, fieldset, .jobs-easy-apply-form-section__grouping") || label.parentElement;
    if (group && visible(label) && !answered(group)) missing++;
  });
  return missing;
}
"""


def _rx(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def card_says_applied(text: str, patterns: list[re.Pattern]) -> bool:
    """True when the job card itself says Applied, before the posting is opened."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line and any(pattern.search(line) for pattern in patterns):
            return True
    return False


def title_skip_reason(title: str, include: list[re.Pattern], exclude: list[re.Pattern]) -> str | None:
    """None when the title is an SDE / AI-style role we are willing to open."""
    text = _norm(title)
    if not text:
        return Reason.ROLE
    if any(p.search(text) for p in exclude):
        return Reason.ROLE
    if include and not any(p.search(text) for p in include):
        return Reason.ROLE
    return None


def experience_skip_reason(text: str, patterns: list[re.Pattern]) -> str | None:
    """None when the description does not ask for more than about a year."""
    if any(p.search(text or "") for p in patterns):
        return Reason.EXPERIENCE
    return None


def _visible(locator: Any) -> bool:
    try:
        return bool(locator.first.is_visible())
    except Exception:
        return False


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _requirements_text(text: str) -> str:
    """Ignore the company blurb, which often mentions years that are not the job requirement."""
    raw = text or ""
    lower = raw.lower()
    cut = len(raw)
    for marker in ("about the company", "about company"):
        index = lower.find(marker)
        if index > 0:
            cut = min(cut, index)
    return raw[:cut]


def _first_text(scope: Any, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = scope.locator(selector)
            if locator.count():
                text = _first_line(locator.first.inner_text(timeout=500))
                if text:
                    return text
        except Exception:
            continue
    return ""


class LinkedInAdapter(PortalAdapter):
    name = "linkedin"

    def __init__(self, cfg: dict):
        self.start_url = cfg["start_url"]
        self._trusted = tuple(d.lower() for d in cfg["trusted_domains"])
        filters, listing, apply_, dialog = cfg["filters"], cfg["listing"], cfg["apply"], cfg["dialog"]

        self._title_include = _rx(filters.get("title_include", []))
        self._title_exclude = _rx(filters.get("title_exclude", []))
        self._experience_exclude = _rx(filters.get("experience_exclude", []))

        self._card_selectors: list[str] = listing["card_selectors"]
        self._key_attr: str = listing.get("key_attribute", "")
        self._card_title: list[str] = listing.get("card_title_selectors", [])
        self._card_company: list[str] = listing.get("card_company_selectors", [])
        self._card_location: list[str] = listing.get("card_location_selectors", [])
        self._card_click: list[str] = listing.get("card_click_selectors", [])
        self._pane_selectors: list[str] = listing.get("pane_selectors", [])
        self._insight_selectors: list[str] = listing.get("insight_selectors", [])
        self._pane_title_selectors: list[str] = listing.get("pane_title_selectors", [])
        self._results_list: list[str] = listing.get("results_list_selectors", [])
        self._pagination_selectors: list[str] = listing.get("pagination_selectors", [])
        self._page_next_selectors: list[str] = listing.get("pagination_next_selectors", [])
        self._page_next = _rx(listing.get("pagination_next_names", []))
        self._pane_timeout = float(listing.get("pane_switch_timeout_s", 8))
        self._click_timeout = int(listing.get("click_timeout_ms", 5000))
        self._load_more_wait = float(listing.get("load_more_wait_s", 4))
        self._page_turn_wait = float(listing.get("page_turn_wait_s", 15))

        self._apply = _rx(apply_["apply_button_names"])
        self._apply_selectors: list[str] = list(apply_.get("apply_button_selectors", []))
        self._external = _rx(apply_["external_button_names"])
        self._applied = _rx(apply_["already_applied_text"])
        self._applied_on_card = _rx(apply_.get("applied_card_text", apply_["already_applied_text"]))
        self._apply_wait = float(apply_.get("apply_button_wait_s", 8))

        self._modal_selectors: list[str] = dialog["modal_selectors"]
        self._continue = _rx(dialog["continue_button_names"])
        self._continue_selectors: list[str] = list(dialog.get("continue_button_selectors", []))
        self._loader_selectors: list[str] = list(dialog.get("loader_selectors", [".jobs-loader"]))
        self._submit = _rx(dialog["submit_button_names"])
        self._submit_selectors: list[str] = list(dialog.get("submit_button_selectors", []))
        self._dismiss = _rx(dialog.get("dismiss_button_names", []) + dialog.get("followup_button_names", []))
        self._dismiss_selectors: list[str] = list(dialog.get("dismiss_button_selectors", []))
        self._success = _rx(dialog["success_text"])
        self._invalid = _rx(dialog.get("invalid_answer_text", []))
        self._login_urls = _rx(dialog["login_url_markers"])
        self._captcha_selectors: list[str] = dialog["captcha_selectors"]
        self._captcha_text = _rx(dialog["captcha_text"])

    # --- filters -------------------------------------------------------------
    def eligibility(self, card: JobCard) -> str | None:
        return title_skip_reason(card.title, self._title_include, self._title_exclude)

    def detail_skip_reason(self, page: Any) -> str | None:
        """Skip only when LinkedIn's own insight line asks for more than about a year."""
        pane = self._pane(page)
        if pane is page or not self._insight_selectors:
            return None
        chunks: list[str] = []
        for selector in self._insight_selectors:
            try:
                chunks.extend(pane.locator(selector).all_inner_texts())
            except Exception:
                continue
        return experience_skip_reason(" ".join(chunks), self._experience_exclude)

    def job_view_url(self, card: JobCard) -> str:
        if not card.key or not card.key.isdigit():
            return ""
        return f"https://www.linkedin.com/jobs/view/{card.key}/"

    # --- listing -------------------------------------------------------------
    def on_listing(self, page: Any) -> bool:
        parsed = urlparse(page.url or "")
        host = (parsed.hostname or "").lower()
        if not (host == "linkedin.com" or host.endswith(".linkedin.com")):
            return False
        path = parsed.path or ""
        return path.startswith("/jobs") and "/jobs/view/" not in path

    def list_jobs(self, page: Any) -> list[JobCard]:
        """Only roles worth opening. Other titles and Applied cards are ignored."""
        jobs = [job for job in self._all_cards(page) if self._worth_opening(job)]
        jobs.sort(key=lambda job: int(job.key) if str(job.key).isdigit() else 0, reverse=True)
        return jobs

    def _all_cards(self, page: Any) -> list[JobCard]:
        cards = self._cards(page)
        jobs: list[JobCard] = []
        seen: set[str] = set()
        for index in range(cards.count()):
            handle = cards.nth(index)
            title = _first_text(handle, self._card_title)
            company = _first_text(handle, self._card_company)
            location = _first_text(handle, self._card_location)
            if not title:
                continue
            key = self._card_key(handle, title, company)
            if not key or key in seen:
                continue
            seen.add(key)
            jobs.append(JobCard(key, title, company, location, handle))
        return jobs

    def _worth_opening(self, card: JobCard) -> bool:
        if self.eligibility(card):
            return False
        try:
            text = card.handle.inner_text(timeout=800)
        except Exception:
            text = ""
        if card_says_applied(text, self._applied_on_card):
            return False
        return True

    def load_more(self, page: Any) -> bool:
        """Scroll the list, then click the pager's Next. A new page replaces the cards."""
        if self.opened_application(page):
            self.dismiss_application(page)
        before = self._visible_job_keys(page)
        self._scroll_results(page)
        if self._wait_for_new_jobs(page, before):
            return True
        return self._open_next_results_page(page, before)

    def _visible_job_keys(self, page: Any) -> set[str]:
        try:
            return {job.key for job in self._all_cards(page)}
        except Exception:
            return set()

    def _scroll_results(self, page: Any) -> None:
        cards = self._cards(page)
        count = 0
        try:
            count = cards.count()
        except Exception:
            return
        if count:
            try:
                cards.nth(count - 1).scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
        for selector in self._results_list:
            locator = page.locator(selector)
            if not locator.count():
                continue
            try:
                locator.first.evaluate("el => el.scrollBy(0, el.clientHeight || 700)")
                return
            except Exception:
                continue

    def _wait_for_new_jobs(self, page: Any, before: set[str]) -> bool:
        deadline = time.monotonic() + self._load_more_wait
        while time.monotonic() < deadline:
            if self._visible_job_keys(page) - before:
                return True
            time.sleep(0.3)
        return False

    def _open_next_results_page(self, page: Any, before: set[str]) -> bool:
        scope = page
        for selector in self._pagination_selectors:
            locator = page.locator(selector)
            try:
                if locator.count() and _visible(locator.first):
                    scope = locator.first
                    break
            except Exception:
                continue
        button = self._locate(scope, self._page_next, self._page_next_selectors)
        if button is None or scope is page:
            # Never click a bare "Next" on the whole page; that is the Easy Apply button.
            button = None
            for selector in self._page_next_selectors:
                try:
                    candidate = page.locator(selector).first
                    if _visible(candidate):
                        button = candidate
                        break
                except Exception:
                    continue
        if button is None:
            log.info("No further results pages")
            return False
        try:
            if not button.is_enabled():
                log.info("Last results page reached")
                return False
        except Exception:
            pass
        before_url = page.url or ""
        try:
            log.info("Opening the next results page")
            button.scroll_into_view_if_needed(timeout=2000)
            button.click(timeout=self._click_timeout)
        except Exception:
            log.info("Could not open the next results page")
            return False
        deadline = time.monotonic() + self._page_turn_wait
        while time.monotonic() < deadline:
            if self._visible_job_keys(page) - before:
                return True
            if (page.url or "") != before_url:
                deadline = max(deadline, time.monotonic() + 8)
            time.sleep(0.4)
        log.info("Next results page did not load; will try again")
        return False

    def select_job(self, page: Any, card: JobCard) -> bool:
        if self.opened_application(page):
            self.dismiss_application(page)
        if not self._click_card(card):
            return False
        return self._pane_shows(page, card)

    def find_apply(self, page: Any) -> ApplyAction:
        deadline = time.monotonic() + max(0.0, self._apply_wait)
        while True:
            action = self._find_apply_once(page)
            if action.kind is not ApplyKind.NONE:
                return action
            if time.monotonic() >= deadline:
                return ApplyAction(ApplyKind.NONE)
            time.sleep(0.35)

    # --- dialog --------------------------------------------------------------
    def opened_application(self, page: Any) -> bool:
        if self._modal(page) is not None:
            return True
        # The "application was sent" dialog is a different overlay. It still blocks the list.
        return _visible(page.locator(".artdeco-modal-overlay--is-top-layer"))

    def dismiss_application(self, page: Any) -> None:
        """Close Easy Apply, the Discard confirm, and the post-submit "Not now" dialog."""
        for _ in range(5):
            if not self.opened_application(page):
                return
            button = self._locate(page, self._dismiss, self._dismiss_selectors)
            try:
                if button is not None:
                    button.click(timeout=2000, force=True)
                else:
                    page.keyboard.press("Escape")
            except Exception:
                log.debug("Could not close the LinkedIn dialog", exc_info=True)
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    return
            time.sleep(0.4)

    def keep_searching(self) -> bool:
        return True

    def keep_listing_after_apply(self, page: Any) -> bool:
        return self.on_listing(page) and not self.opened_application(page)

    def questions_result(self) -> FlowResult:
        """Record the job and close the dialog so the next application can start."""
        return FlowResult(Outcome.SKIPPED, Reason.QUESTIONS)

    def classify(self, page: Any) -> PageState:
        url = page.url or ""
        if self._captcha_present(page):
            return PageState.CAPTCHA
        if any(p.search(url) for p in self._login_urls):
            return PageState.LOGIN
        if not self._is_trusted(urlparse(url).hostname or ""):
            return PageState.EXTERNAL
        if self._text_visible(page, self._success):
            return PageState.SUCCESS
        modal = self._modal(page)
        if modal is None:
            return PageState.UNKNOWN
        if self._unanswered(page) > 0 or self._text_visible(modal, self._invalid):
            return PageState.QUESTIONS
        if self._locate(modal, self._submit, self._submit_selectors) is not None:
            return PageState.SUBMIT
        if self._locate(modal, self._continue, self._continue_selectors) is not None:
            return PageState.CONTINUE
        return PageState.UNKNOWN

    def signature(self, page: Any) -> str:
        """Changes when Next moves the dialog (step title or progress), not only the job title."""
        modal = self._modal(page) or page
        parts = [page.url or ""]
        try:
            headings = modal.locator("h3")
            if headings.count():
                parts.append(headings.first.inner_text(timeout=800).strip())
        except Exception:
            pass
        try:
            text = modal.inner_text(timeout=800)
            match = re.search(r"\b\d{1,3}%", text or "")
            if match:
                parts.append(match.group(0))
        except Exception:
            pass
        return "|".join(parts)

    def click_continue(self, page: Any) -> None:
        self._click_dialog_button(page, self._continue, self._continue_selectors, "Next/Review")

    def click_submit(self, page: Any) -> None:
        self._click_dialog_button(page, self._submit, self._submit_selectors, "Submit application")

    def blocker(self, page: Any) -> PageState | None:
        """On the search page itself, ignore footer wording. A real challenge still pauses."""
        if self.on_listing(page) and not self.opened_application(page):
            url = page.url or ""
            if any(p.search(url) for p in self._login_urls):
                return PageState.LOGIN
            if any(_visible(page.locator(sel)) for sel in self._captcha_selectors):
                return PageState.CAPTCHA
            return None
        state = self.classify(page)
        return state if state in (PageState.CAPTCHA, PageState.LOGIN) else None

    def diagnose(self, page: Any) -> list[str]:
        out = [f"page: {page.url}", f"on listing page: {self.on_listing(page)}"]
        out.append(f"easy apply dialog open: {self.opened_application(page)}")
        out.append(f"blocker (captcha/login): {self.blocker(page)}")
        for selector in self._card_selectors:
            out.append(f"card selector {selector!r}: {page.locator(selector).count()} match(es)")
        jobs = self.list_jobs(page)
        out.append(f"jobs parsed: {len(jobs)}")
        for job in jobs[:8]:
            reason = self.eligibility(job) or "eligible"
            out.append(f"  - [{job.key}] {job.title} | {job.company} | {reason}")
        out.append(f"apply action for the job currently shown: {self.find_apply(page).kind.value}")
        return out

    # --- internals -----------------------------------------------------------
    def _is_trusted(self, host: str) -> bool:
        host = (host or "").lower()
        return any(host == d or host.endswith("." + d) for d in self._trusted)

    def _cards(self, page: Any) -> Any:
        locator = page.locator(self._card_selectors[0])
        for selector in self._card_selectors:
            candidate = page.locator(selector)
            if candidate.count():
                return candidate
        return locator

    def _card_key(self, handle: Any, title: str, company: str) -> str:
        if self._key_attr:
            try:
                value = handle.get_attribute(self._key_attr, timeout=500)
                if value and value.isdigit():
                    return value
            except Exception:
                pass
        for selector in ("a[href*='/jobs/view/']", "a"):
            try:
                href = handle.locator(selector).first.get_attribute("href", timeout=500) or ""
            except Exception:
                href = ""
            match = _JOB_ID_IN_HREF.search(href)
            if match:
                return match.group(1)
        return ""

    def _pane(self, page: Any) -> Any:
        for selector in self._pane_selectors:
            try:
                locator = page.locator(selector)
                if locator.count():
                    return locator.first
            except Exception:
                continue
        return page

    def _modal(self, page: Any) -> Any | None:
        for selector in self._modal_selectors:
            try:
                locator = page.locator(selector).first
                if _visible(locator):
                    return locator
            except Exception:
                continue
        return None

    def _click_card(self, card: JobCard) -> bool:
        target = card.handle
        try:
            target.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        for selector in self._card_click:
            locator = target.locator(selector).first
            if not _visible(locator):
                continue
            try:
                locator.click(timeout=self._click_timeout)
                return True
            except Exception:
                log.debug("Click via %s failed for %s", selector, card.title, exc_info=True)
        try:
            target.click(timeout=self._click_timeout)
            return True
        except Exception:
            log.warning("Could not click card: %s", card.title, exc_info=True)
            return False

    def _pane_shows(self, page: Any, card: JobCard) -> bool:
        wanted = _norm(card.title)
        pane = self._pane(page)
        deadline = time.monotonic() + self._pane_timeout
        while True:
            url = page.url or ""
            if card.key and (f"currentJobId={card.key}" in url or f"/jobs/view/{card.key}" in url):
                return True
            texts: list[str] = []
            for selector in self._pane_title_selectors:
                try:
                    texts.extend(pane.locator(selector).all_inner_texts())
                except Exception:
                    pass
            if any(wanted and (wanted in _norm(t) or _norm(t) in wanted) for t in texts if t.strip()):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.3)

    def _find_apply_once(self, page: Any) -> ApplyAction:
        scope = self._pane(page)
        if self._text_visible(scope, self._applied):
            return ApplyAction(ApplyKind.ALREADY_APPLIED)
        button = self._locate(scope, self._apply, self._apply_selectors)
        if button is not None:
            return ApplyAction(ApplyKind.EASY, button)
        if self._locate(scope, self._external, []) is not None:
            return ApplyAction(ApplyKind.EXTERNAL)
        return ApplyAction(ApplyKind.NONE)

    def _locate(self, scope: Any, patterns: list[re.Pattern], css: list[str]) -> Any | None:
        for selector in css:
            try:
                locator = scope.locator(selector).first
                if _visible(locator):
                    return locator
            except Exception:
                continue
        for role in ("button", "link"):
            for pattern in patterns:
                locator = scope.get_by_role(role, name=pattern).first
                if _visible(locator):
                    return locator
        return None

    def _text_visible(self, scope: Any, patterns: list[re.Pattern]) -> bool:
        return any(_visible(scope.get_by_text(p)) for p in patterns)

    def _loader_visible(self, page: Any) -> bool:
        script = """
        sel => {
          const el = document.querySelector(sel);
          if (!el) return false;
          const s = getComputedStyle(el);
          if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0) return false;
          const r = el.getBoundingClientRect();
          return r.width > 40 && r.height > 8;
        }
        """
        for selector in self._loader_selectors:
            try:
                if page.evaluate(script, selector):
                    return True
            except Exception:
                if _visible(page.locator(selector)):
                    return True
        return False

    def _wait_for_loader(self, page: Any, timeout_s: float = 2) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and self._loader_visible(page):
            time.sleep(0.2)

    def _click_dialog_button(self, page: Any, patterns: list[re.Pattern], css: list[str], label: str) -> None:
        """Click Next/Review/Submit on the Easy Apply overlay, not a covered inner node."""
        deadline = time.monotonic() + max(self._pane_timeout, 12)
        last_error = "not found"
        while time.monotonic() < deadline:
            self._wait_for_loader(page)
            scope = self._modal(page) or page
            button = self._locate(scope, patterns, css)
            if button is None:
                time.sleep(0.25)
                continue
            try:
                enabled = True
                try:
                    enabled = bool(button.is_enabled())
                except Exception:
                    enabled = True
                if not enabled:
                    time.sleep(0.25)
                    continue
                log.info("Clicking %s", label)
                button.click(timeout=2500)
                return
            except Exception as exc:
                last_error = str(exc).splitlines()[0]
                try:
                    button.click(timeout=2500, force=True)
                    log.info("Clicking %s", label)
                    return
                except Exception as forced:
                    last_error = str(forced).splitlines()[0]
                log.debug("Click %s failed, retrying", label, exc_info=True)
                time.sleep(0.3)
        raise RuntimeError(f"{label} button could not be clicked ({last_error})")

    def _captcha_present(self, page: Any) -> bool:
        if any(_visible(page.locator(sel)) for sel in self._captcha_selectors):
            return True
        url = page.url or ""
        if "/checkpoint/challenge" in url:
            return True
        if self.opened_application(page) and self._text_visible(page, self._captcha_text):
            return True
        return False

    def _unanswered(self, page: Any) -> int:
        try:
            return int(page.evaluate(_UNANSWERED_REQUIRED_JS))
        except Exception:
            return 0
