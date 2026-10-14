"""Indeed adapter. All selectors and texts come from config/portals/indeed.toml."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

from ..models import ApplyAction, ApplyKind, JobCard, PageState
from .base import PortalAdapter

log = logging.getLogger(__name__)

# Counts visible, enabled, required fields that are still empty (text-like inputs,
# selects, textareas, and radio/checkbox groups). A heuristic: a miss is harmless
# because Indeed's own validation then stops the step from advancing.
_UNANSWERED_REQUIRED_JS = """
() => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const required = el => el.required || el.getAttribute('aria-required') === 'true';
  let missing = 0;
  document.querySelectorAll(
    'input:not([type=hidden]):not([type=radio]):not([type=checkbox]):not([type=file]), textarea, select'
  ).forEach(el => {
    if (visible(el) && !el.disabled && required(el) && !String(el.value || '').trim()) missing++;
  });
  const groups = {};
  document.querySelectorAll('input[type=radio], input[type=checkbox]').forEach((el, i) => {
    if (!visible(el) || el.disabled) return;
    const id = (el.type + ':' + (el.name || ('#' + i)));
    const g = groups[id] || (groups[id] = {required: false, checked: false});
    g.required = g.required || required(el);
    g.checked = g.checked || el.checked;
  });
  Object.values(groups).forEach(g => { if (g.required && !g.checked) missing++; });
  return missing;
}
"""


def _rx(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _title_matches(wanted: str, got: str) -> bool:
    """Loose match: Indeed pane titles often add suffixes or punctuation."""
    if not wanted or not got:
        return False
    if wanted in got or got in wanted:
        return True
    wanted_head = wanted.split(" - ")[0].strip()
    got_head = got.split(" - ")[0].strip()
    return bool(wanted_head and got_head and (wanted_head in got_head or got_head in wanted_head))


def _jk_in_url(url: str, jk: str) -> bool:
    if not jk or jk.startswith("h-"):
        return False
    url = url or ""
    if f"jk={jk}" in url or f"vjk={jk}" in url:
        return True
    try:
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(url).query)
        for param in ("jk", "vjk"):
            if jk in qs.get(param, []):
                return True
    except Exception:
        pass
    return False


# Prefer structured fields on the card; inner_text alone loses newlines on some Indeed layouts.
_CARD_TITLE_SELECTORS = [
    "h2.jobTitle a span[title]",
    "h2.jobTitle span[title]",
    "h2.jobTitle a span",
    "h3.jobTitle a span[title]",
    "h3.jobTitle span[title]",
    "h3.jobTitle a span",
    "a.jcs-JobTitle span",
    "h2.jobTitle",
    "h3.jobTitle",
]
_CARD_COMPANY_SELECTORS = ['[data-testid="company-name"]', "span.companyName", ".companyName"]
_CARD_LOCATION_SELECTORS = ['[data-testid="text-location"]', "div.companyLocation", ".companyLocation"]


def _first_inner_text(scope: Any, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            locator = scope.locator(selector)
            if locator.count():
                text = locator.first.inner_text(timeout=500).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


def _visible(locator: Any) -> bool:
    """Non-waiting visibility check; any error means 'not visible'."""
    try:
        return bool(locator.first.is_visible())
    except Exception:
        return False


def parse_card_text(text: str, badge_patterns: list[re.Pattern]) -> tuple[str, str, str]:
    """(title, company, location) from a card's text. Order matches the screenshots."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not any(p.search(ln) for p in badge_patterns)]
    title = lines[0] if lines else ""
    company = lines[1] if len(lines) > 1 else ""
    location = lines[2] if len(lines) > 2 else ""
    return title, company, location


class IndeedAdapter(PortalAdapter):
    name = "indeed"

    def __init__(self, cfg: dict):
        self.start_url = cfg["start_url"]
        self._trusted = tuple(d.lower() for d in cfg["trusted_domains"])
        listing, apply_, pages = cfg["listing"], cfg["apply"], cfg["pages"]

        self._card_selectors: list[str] = listing["card_selectors"]
        self._key_attr: str = listing.get("key_attribute", "")
        self._badges = _rx(listing.get("badge_lines", []))
        self._pane_selectors: list[str] = listing.get("pane_selectors", [])
        self._pane_title_selectors: list[str] = listing.get("pane_title_selectors", [])
        self._pane_timeout = float(listing.get("pane_switch_timeout_s", 6))
        self._click_timeout = int(listing.get("click_timeout_ms", 5000))
        self._load_more = _rx(listing.get("load_more_names", []))
        self._load_more_wait = float(listing.get("load_more_wait_s", 4))

        self._apply = _rx(apply_["apply_button_names"])
        self._apply_selectors: list[str] = list(apply_.get("apply_button_selectors", []))
        self._apply_button_wait_s = float(apply_.get("apply_button_wait_s", 12))
        self._external = _rx(apply_["external_button_names"])
        self._applied_text = _rx(apply_["already_applied_text"])

        self._continue = _rx(pages["continue_button_names"])
        self._submit = _rx(pages["submit_button_names"])
        self._submit_selectors: list[str] = list(pages.get("submit_button_selectors", []))
        self._application_urls = _rx(pages.get("application_url_markers", ["smartapply", "indeedapply"]))
        self._review_text = _rx(pages.get("review_page_text", ["review your application"]))
        self._success_urls = _rx(pages["success_url_markers"])
        self._success_text = _rx(pages["success_text"])
        self._question_urls = _rx(pages["question_url_markers"])
        self._login_urls = _rx(pages["login_url_markers"])
        self._captcha_selectors: list[str] = pages["captcha_selectors"]
        self._captcha_text = _rx(pages["captcha_text"])

    # --- helpers -----------------------------------------------------------
    def _is_trusted(self, host: str) -> bool:
        host = (host or "").lower()
        return any(host == d or host.endswith("." + d) for d in self._trusted)

    def _first_visible(self, scope: Any, patterns: list[re.Pattern]) -> Any | None:
        return self._locate_action(scope, patterns, ())

    def _locate_action(
        self, scope: Any, patterns: list[re.Pattern], css_selectors: list[str]
    ) -> Any | None:
        for selector in css_selectors:
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
        for pattern in patterns:
            try:
                locator = scope.get_by_text(pattern).first
                if _visible(locator):
                    return locator
            except Exception:
                continue
        return None

    def _text_visible(self, scope: Any, patterns: list[re.Pattern]) -> bool:
        return any(_visible(scope.get_by_text(p)) for p in patterns)

    def _pane(self, page: Any) -> Any:
        for selector in self._pane_selectors:
            try:
                locator = page.locator(selector)
                if locator.count():
                    return locator.first
            except Exception:
                continue
        return page

    def _cards(self, page: Any) -> Any:
        locator = page.locator(self._card_selectors[0])
        for selector in self._card_selectors:
            candidate = page.locator(selector)
            if candidate.count():
                return candidate
        return locator

    def _card_key(self, handle: Any, title: str, company: str, location: str) -> str:
        if self._key_attr:
            try:
                value = handle.get_attribute(self._key_attr, timeout=500)
                if not value:
                    inner = handle.locator(f"[{self._key_attr}]")
                    if inner.count():
                        value = inner.first.get_attribute(self._key_attr, timeout=500)
                if value:
                    return value
            except Exception:
                pass
        digest = hashlib.sha1(f"{title}|{company}|{location}".lower().encode()).hexdigest()[:16]
        return f"h-{digest}"

    # --- listing -------------------------------------------------------------
    def job_view_url(self, card: JobCard) -> str:
        if card.key.startswith("h-"):
            return ""
        parsed = urlparse(self.start_url)
        return f"{parsed.scheme}://{parsed.netloc}/viewjob?jk={card.key}"

    def on_listing(self, page: Any) -> bool:
        current, start = urlparse(page.url), urlparse(self.start_url)
        if current.hostname != start.hostname:
            return False
        path = current.path.rstrip("/") or "/"
        if path == start.path.rstrip("/") or path == "/":
            return True
        # Same-site job feeds (do not treat as "left the listing").
        return path.startswith(("/jobs", "/recommended", "/m/jobs", "/homepage"))

    def blocker(self, page: Any) -> PageState | None:
        """Listing pages: strict checks only (avoid pausing on reCAPTCHA footer text)."""
        if self.on_listing(page):
            url = page.url or ""
            if any(p.search(url) for p in self._login_urls):
                return PageState.LOGIN
            if any(_visible(page.locator(sel)) for sel in self._captcha_selectors):
                return PageState.CAPTCHA
            return None
        state = self.classify(page)
        return state if state in (PageState.CAPTCHA, PageState.LOGIN) else None

    def list_jobs(self, page: Any) -> list[JobCard]:
        cards = self._cards(page)
        jobs: list[JobCard] = []
        seen: set[str] = set()
        for index in range(cards.count()):
            handle = cards.nth(index)
            try:
                text = handle.inner_text(timeout=2000)
            except Exception:
                text = ""
            title = _first_inner_text(handle, _CARD_TITLE_SELECTORS)
            company = _first_inner_text(handle, _CARD_COMPANY_SELECTORS)
            location = _first_inner_text(handle, _CARD_LOCATION_SELECTORS)
            if not title:
                title, parsed_co, parsed_loc = parse_card_text(text, self._badges)
                company = company or parsed_co
                location = location or parsed_loc
            elif not company or not location:
                _, parsed_co, parsed_loc = parse_card_text(text, self._badges)
                company = company or parsed_co
                location = location or parsed_loc
            if not title:
                continue
            key = self._card_key(handle, title, company, location)
            if key in seen:
                continue
            seen.add(key)
            jobs.append(JobCard(key, title, company, location, handle))
        return jobs

    def load_more(self, page: Any) -> bool:
        cards = self._cards(page)
        before = cards.count()
        if before:
            try:
                cards.nth(before - 1).scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
        button = self._first_visible(page, self._load_more)
        if button is not None:
            try:
                button.click(timeout=self._click_timeout)
            except Exception:
                log.debug("Load-more click failed", exc_info=True)
        deadline = time.monotonic() + self._load_more_wait
        while time.monotonic() < deadline:
            if self._cards(page).count() > before:
                return True
            time.sleep(0.3)
        return False

    def select_job(self, page: Any, card: JobCard) -> bool:
        target = card.handle
        if not self._click_job_card(target, card.title):
            return False
        return self._pane_shows(page, card.title, card.key)

    def _click_job_card(self, target: Any, title: str) -> bool:
        try:
            target.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            log.debug("Scroll into view failed for %s", title, exc_info=True)
        for selector in (
            "a.jcs-JobTitle",
            "h2.jobTitle a",
            "h3.jobTitle a",
            "h2.jobTitle",
            "h3.jobTitle",
        ):
            locator = target.locator(selector).first
            if not _visible(locator):
                continue
            try:
                locator.click(timeout=self._click_timeout)
                return True
            except Exception:
                log.debug("Click via %s failed for %s", selector, title, exc_info=True)
        for click in (
            lambda: target.click(timeout=self._click_timeout),
            lambda: target.click(timeout=self._click_timeout, force=True),
        ):
            try:
                click()
                return True
            except Exception:
                continue
        log.warning("Could not click card: %s", title, exc_info=True)
        return False

    def _pane_shows(self, page: Any, title: str, job_key: str = "") -> bool:
        """Only apply when the detail pane's heading matches the card we clicked."""
        if not self._pane_title_selectors:
            time.sleep(1.0)  # verification disabled by config
            return True
        wanted = _norm(title)
        scope = self._pane(page)
        deadline = time.monotonic() + self._pane_timeout
        while True:
            if _jk_in_url(page.url or "", job_key):
                return True
            texts: list[str] = []
            for selector in self._pane_title_selectors:
                try:
                    texts.extend(scope.locator(selector).all_inner_texts())
                except Exception:
                    pass
            if any(_title_matches(wanted, _norm(t)) for t in texts if t.strip()):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.3)

    def find_apply(self, page: Any) -> ApplyAction:
        deadline = time.monotonic() + max(0.0, self._apply_button_wait_s)
        while True:
            action = self._find_apply_once(page)
            if action.kind is not ApplyKind.NONE:
                return action
            if time.monotonic() >= deadline:
                return ApplyAction(ApplyKind.NONE)
            time.sleep(0.35)

    def _find_apply_once(self, page: Any) -> ApplyAction:
        # Apply is often outside the narrow description pane (sticky header/footer).
        scopes = [self._pane(page), page]
        seen: set[int] = set()
        for scope in scopes:
            sid = id(scope)
            if sid in seen:
                continue
            seen.add(sid)
            if self._first_visible(scope, self._external) is not None:
                return ApplyAction(ApplyKind.EXTERNAL)
            if self._text_visible(scope, self._applied_text):
                return ApplyAction(ApplyKind.ALREADY_APPLIED)
            button = self._locate_action(scope, self._apply, self._apply_selectors)
            if button is None:
                continue
            try:
                href = button.get_attribute("href", timeout=500)
            except Exception:
                href = None
            if href and href.startswith("http") and not self._is_trusted(urlparse(href).hostname or ""):
                return ApplyAction(ApplyKind.EXTERNAL)
            return ApplyAction(ApplyKind.EASY, button)
        return ApplyAction(ApplyKind.NONE)

    def _on_application_tab(self, url: str) -> bool:
        return any(p.search(url) for p in self._application_urls)

    def _application_still_loading(self, page: Any) -> bool:
        url = page.url or ""
        if not self._on_application_tab(url):
            return False
        if self._locate_action(page, self._submit, self._submit_selectors) is not None:
            return False
        if any(p.search(url) for p in self._success_urls):
            return False
        if self._first_visible(page, self._continue) is not None:
            return False
        if self._text_visible(page, self._review_text):
            return True
        return True

    # --- application pages ---------------------------------------------------
    def classify(self, page: Any) -> PageState:
        url = page.url or ""
        if self._captcha_present(page):
            return PageState.CAPTCHA
        if url in ("", "about:blank"):
            return PageState.UNKNOWN
        if not self._is_trusted(urlparse(url).hostname or ""):
            return PageState.EXTERNAL
        if any(p.search(url) for p in self._login_urls):
            return PageState.LOGIN
        if any(p.search(url) for p in self._success_urls) or self._text_visible(page, self._success_text):
            return PageState.SUCCESS
        if self._text_visible(page, self._applied_text):
            return PageState.ALREADY_APPLIED
        if any(p.search(url) for p in self._question_urls):
            return PageState.QUESTIONS
        if self._application_still_loading(page):
            return PageState.UNKNOWN
        if self._locate_action(page, self._submit, self._submit_selectors) is not None:
            return PageState.SUBMIT
        if self._unanswered_required(page) > 0:
            return PageState.QUESTIONS
        if self._first_visible(page, self._continue) is not None:
            return PageState.CONTINUE
        return PageState.UNKNOWN

    def _captcha_present(self, page: Any) -> bool:
        if any(_visible(page.locator(sel)) for sel in self._captcha_selectors):
            return True
        try:
            title = page.title()
        except Exception:
            title = ""
        return any(p.search(title) for p in self._captcha_text) or self._text_visible(page, self._captcha_text)

    def _unanswered_required(self, page: Any) -> int:
        try:
            return int(page.evaluate(_UNANSWERED_REQUIRED_JS))
        except Exception:
            return 0

    def signature(self, page: Any) -> str:
        heading = ""
        try:
            locator = page.locator("h1, h2")
            if locator.count():
                heading = locator.first.inner_text(timeout=1000).strip()
        except Exception:
            pass
        return f"{page.url}|{heading}"

    def click_continue(self, page: Any) -> None:
        button = self._first_visible(page, self._continue)
        if button is None:
            raise RuntimeError("Continue button not found")
        button.click(timeout=self._click_timeout)

    def click_submit(self, page: Any) -> None:
        button = self._locate_action(page, self._submit, self._submit_selectors)
        if button is None:
            raise RuntimeError("Submit button not found")
        deadline = time.monotonic() + max(self._pane_timeout, 25)
        while time.monotonic() < deadline:
            try:
                if _visible(button) and button.is_enabled():
                    button.click(timeout=self._click_timeout)
                    return
            except Exception:
                pass
            time.sleep(0.35)
        raise RuntimeError("Submit button stayed disabled (page still loading?)")

    # --- diagnostics ---------------------------------------------------------
    def diagnose(self, page: Any) -> list[str]:
        out = [f"page: {page.url}", f"on listing page: {self.on_listing(page)}"]
        out.append(f"blocker (captcha/login): {self.blocker(page)}")
        for selector in self._card_selectors:
            out.append(f"card selector {selector!r}: {page.locator(selector).count()} match(es)")
        jobs = self.list_jobs(page)
        out.append(f"jobs parsed: {len(jobs)}")
        for job in jobs[:5]:
            out.append(f"  - [{job.key}] {job.title} | {job.company} | {job.location}")
        for selector in self._pane_selectors:
            out.append(f"pane selector {selector!r}: {page.locator(selector).count()} match(es)")
        pane = self._pane(page)
        for selector in self._pane_title_selectors:
            texts = pane.locator(selector).all_inner_texts()
            out.append(f"pane title selector {selector!r}: {texts[:2]}")
        out.append(f"page url (jk/vjk check): {page.url}")
        out.append(f"apply action for the job currently shown: {self.find_apply(page).kind.value}")
        return out
