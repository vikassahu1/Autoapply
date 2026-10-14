"""Indeed classification/decision logic against a minimal Playwright-like fake page."""
import re

import pytest

from autoapply.config import PROJECT_ROOT, load_portal_config
from autoapply.models import ApplyKind, PageState as S
from autoapply.portals.indeed import IndeedAdapter, _jk_in_url, _title_matches, parse_card_text


class FakeLocator:
    def __init__(self, visible=False, attrs=None, texts=()):
        self._visible, self._attrs, self._texts = visible, attrs or {}, list(texts)

    @property
    def first(self): return self
    def nth(self, i): return self
    def is_visible(self, **kw): return self._visible
    def count(self): return 1 if self._visible else 0
    def get_attribute(self, name, **kw): return self._attrs.get(name)
    def all_inner_texts(self): return self._texts
    def click(self, **kw): self.clicked = True


class FakePage:
    def __init__(self, url="https://smartapply.indeed.com/beta/indeedapply/form/review", buttons=(),
                 links=(), texts=(), visible_selectors=(), title="", unanswered=0, apply_href=None):
        self.url, self._buttons, self._links = url, list(buttons), list(links)
        self._texts, self._visible_selectors = list(texts), set(visible_selectors)
        self._title, self._unanswered, self._href = title, unanswered, apply_href

    def title(self): return self._title
    def evaluate(self, js): return self._unanswered
    def locator(self, sel): return FakeLocator(sel in self._visible_selectors)
    def get_by_text(self, rx): return FakeLocator(any(rx.search(t) for t in self._texts))

    def get_by_role(self, role, name=None):
        names = self._buttons if role == "button" else self._links
        hit = any(name.search(n) for n in names)
        return FakeLocator(hit, attrs={"href": self._href} if self._href else {})


@pytest.fixture(scope="module")
def adapter():
    cfg = load_portal_config("indeed", PROJECT_ROOT)
    cfg["apply"]["apply_button_wait_s"] = 0
    return IndeedAdapter(cfg)


# ---- classification ------------------------------------------------------------
def test_review_page_with_submit_button(adapter):
    assert adapter.classify(FakePage(buttons=["Submit your application"])) is S.SUBMIT


def test_smartapply_review_without_submit_is_still_loading(adapter):
    page = FakePage(
        url="https://smartapply.indeed.com/beta/indeedapply/form/review",
        texts=["Review your application"],
    )
    assert adapter.classify(page) is S.UNKNOWN
    assert adapter.classify(FakePage(url=page.url, texts=["Review your application"], buttons=["Submit your application"])) is S.SUBMIT


def test_continue_and_next_buttons(adapter):
    assert adapter.classify(FakePage(buttons=["Continue"])) is S.CONTINUE
    assert adapter.classify(FakePage(buttons=["Next"])) is S.CONTINUE


def test_unrelated_button_text_is_not_a_continue(adapter):
    assert adapter.classify(FakePage(buttons=["Continue shopping", "Next steps guide"])) is S.UNKNOWN


def test_required_empty_fields_mean_questions_even_with_a_continue_button(adapter):
    assert adapter.classify(FakePage(buttons=["Continue"], unanswered=2)) is S.QUESTIONS


def test_questions_url_marker(adapter):
    page = FakePage(url="https://smartapply.indeed.com/beta/indeedapply/form/questions/1", buttons=["Continue"])
    assert adapter.classify(page) is S.QUESTIONS


def test_company_site_is_external(adapter):
    assert adapter.classify(FakePage(url="https://careers.acme.com/apply/123", buttons=["Submit"])) is S.EXTERNAL


def test_lookalike_domain_is_not_trusted(adapter):
    assert adapter.classify(FakePage(url="https://indeed.com.evil.example/x", buttons=["Continue"])) is S.EXTERNAL
    assert adapter.classify(FakePage(url="https://notindeed.com/x", buttons=["Continue"])) is S.EXTERNAL


def test_blank_page_is_unknown_not_external(adapter):
    assert adapter.classify(FakePage(url="about:blank")) is S.UNKNOWN


def test_success_by_url_and_by_text(adapter):
    assert adapter.classify(FakePage(url="https://smartapply.indeed.com/beta/indeedapply/form/post-apply")) is S.SUCCESS
    assert adapter.classify(FakePage(texts=["Your application has been submitted!"])) is S.SUCCESS


def test_login_wall(adapter):
    assert adapter.classify(FakePage(url="https://secure.indeed.com/auth?hl=en")) is S.LOGIN


def test_captcha_challenge_iframe_or_text(adapter):
    assert adapter.classify(FakePage(visible_selectors=["iframe[src*='recaptcha/api2/bframe']"],
                                     buttons=["Continue"])) is S.CAPTCHA
    assert adapter.classify(FakePage(texts=["Additional Verification Required"])) is S.CAPTCHA
    assert adapter.classify(FakePage(title="Just a moment...")) is S.CAPTCHA


def test_captcha_wins_over_everything_else(adapter):
    page = FakePage(texts=["verify you are human"], buttons=["Submit your application"])
    assert adapter.classify(page) is S.CAPTCHA


def test_blocker_helper_only_reports_captcha_and_login(adapter):
    assert adapter.blocker(FakePage(buttons=["Continue"])) is None
    assert adapter.blocker(FakePage(texts=["verify you are human"])) is S.CAPTCHA


# ---- apply button decisions ---------------------------------------------------------
def test_apply_now_is_easy_apply(adapter):
    assert adapter.find_apply(FakePage(url="https://in.indeed.com/", buttons=["Apply now"])).kind is ApplyKind.EASY


def test_apply_on_company_site_is_external(adapter):
    page = FakePage(url="https://in.indeed.com/", buttons=["Apply on company site"])
    assert adapter.find_apply(page).kind is ApplyKind.EXTERNAL


def test_apply_now_linking_off_site_is_external_without_clicking(adapter):
    page = FakePage(url="https://in.indeed.com/", links=["Apply now"], apply_href="https://jobs.acme.com/apply")
    assert adapter.find_apply(page).kind is ApplyKind.EXTERNAL


def test_apply_now_linking_to_indeed_is_easy(adapter):
    page = FakePage(url="https://in.indeed.com/", links=["Apply now"], apply_href="https://smartapply.indeed.com/x")
    assert adapter.find_apply(page).kind is ApplyKind.EASY


def test_no_button_and_already_applied(adapter):
    assert adapter.find_apply(FakePage(url="https://in.indeed.com/")).kind is ApplyKind.NONE
    assert adapter.find_apply(FakePage(url="https://in.indeed.com/", texts=["Applied"])).kind is ApplyKind.ALREADY_APPLIED


# ---- listing helpers -------------------------------------------------------------------
BADGES = [re.compile(p, re.I) for p in ["^easily apply$"]]


def test_parse_card_with_badge_from_screenshot():
    text = "Easily apply\nDevOps Engineer - AI Trainer\nDataAnnotation\nPune, Maharashtra\n₹7,195.34 - ₹14,390.68 an hour"
    assert parse_card_text(text, BADGES) == ("DevOps Engineer - AI Trainer", "DataAnnotation", "Pune, Maharashtra")


def test_parse_card_without_badge_from_screenshot():
    assert parse_card_text("Software Engineer\nCargill\nBengaluru, Karnataka", BADGES) == (
        "Software Engineer", "Cargill", "Bengaluru, Karnataka")


def test_parse_card_empty():
    assert parse_card_text("", BADGES) == ("", "", "")


def test_title_matches_allows_pane_suffix():
    assert _title_matches("software engineer", "software engineer - job post")
    assert _title_matches("ai solutions engineer", "ai solutions engineer")


def test_jk_in_url():
    assert _jk_in_url("https://in.indeed.com/?vjk=abc123def4567890", "abc123def4567890")
    assert _jk_in_url("https://in.indeed.com/viewjob?jk=abc123def4567890", "abc123def4567890")
    assert not _jk_in_url("https://in.indeed.com/", "abc123def4567890")
    assert not _jk_in_url("https://in.indeed.com/?vjk=abc", "h-deadbeef")


def test_on_listing(adapter):
    assert adapter.on_listing(FakePage(url="https://in.indeed.com/"))
    assert adapter.on_listing(FakePage(url="https://in.indeed.com/?from=gnav"))
    assert adapter.on_listing(FakePage(url="https://in.indeed.com/jobs?q=engineer&l=India"))
    assert not adapter.on_listing(FakePage(url="https://in.indeed.com/viewjob?jk=1"))
    assert not adapter.on_listing(FakePage(url="https://smartapply.indeed.com/"))


def test_listing_footer_recaptcha_text_does_not_block(adapter):
    page = FakePage(url="https://in.indeed.com/", texts=["This site is protected by reCAPTCHA"])
    assert adapter.blocker(page) is None


def test_config_file_is_loadable_and_settings_reject_typos(tmp_path):
    from autoapply.config import load_settings
    assert load_settings().portal == "indeed"
    bad = tmp_path / "config"
    bad.mkdir()
    (bad / "settings.toml").write_text("[run]\nmax_aplications = 3\n")
    with pytest.raises(ValueError):
        load_settings(bad / "settings.toml")
