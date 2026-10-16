"""LinkedIn role filter and Easy Apply dialog classification."""
import pytest

from autoapply.config import PROJECT_ROOT, load_portal_config
from autoapply.models import ApplyKind, JobCard, Outcome, PageState as S, Reason
from autoapply.portals.linkedin import (
    LinkedInAdapter,
    _requirements_text,
    card_says_applied,
    experience_skip_reason,
    title_skip_reason,
)


class Loc:
    """A Playwright-like locator. When `page` is set, role/text queries search that page."""

    def __init__(self, visible=False, enabled=True, page=None):
        self._visible = visible
        self._enabled = enabled
        self._page = page

    @property
    def first(self):
        return self

    def is_visible(self, **kw):
        return self._visible

    def count(self):
        return 1 if self._visible else 0

    def is_enabled(self):
        return self._enabled

    def all_inner_texts(self):
        return []

    def get_by_role(self, role, name=None):
        if self._page is None or not self._visible:
            return Loc(False)
        return self._page.get_by_role(role, name)

    def get_by_text(self, rx):
        if self._page is None or not self._visible:
            return Loc(False)
        return self._page.get_by_text(rx)

    def locator(self, sel):
        if self._page is None:
            return Loc(False)
        return self._page.locator(sel)


class FakePage:
    def __init__(self, url="https://www.linkedin.com/jobs/search/?currentJobId=1", buttons=(),
                 texts=(), visible=(), unanswered=0):
        self.url = url
        self._buttons = list(buttons)
        self._texts = list(texts)
        self._visible = set(visible)
        self._unanswered = unanswered

    def locator(self, sel):
        return Loc(sel in self._visible, page=self)

    def evaluate(self, js):
        return self._unanswered

    def get_by_text(self, rx):
        return Loc(any(rx.search(t) for t in self._texts))

    def get_by_role(self, role, name=None):
        return Loc(any(name.search(n) for n in self._buttons))


MODAL = "[data-test-modal-id='easy-apply-modal']"


@pytest.fixture
def adapter():
    cfg = load_portal_config("linkedin", PROJECT_ROOT)
    cfg["apply"]["apply_button_wait_s"] = 0
    return LinkedInAdapter(cfg)


def test_target_titles_from_resume_are_eligible(adapter):
    include, exclude = adapter._title_include, adapter._title_exclude
    for title in (
        "Software Engineer",
        "Software Development Engineer",
        "Software Dev Engineer I",
        "SDE I",
        "AI Engineer",
        "Junior AI Developer",
        "Frontend Development Intern",
        "Machine Learning Engineer",
        "Backend Developer",
        "SDE",
        "SDE 1",
        "SDE-1",
        "Android - SDE 1",
        "AI Engineer - Fresher",
        "Artificial Intelligence Engineer",
        "GenAI Engineer",
        "Software Development Engineer 1",
    ):
        assert title_skip_reason(title, include, exclude) is None, title


def test_java_cloud_devops_and_level_three_are_not_targets(adapter):
    include, exclude = adapter._title_include, adapter._title_exclude
    for title in (
        "SDE 3",
        "SDE-3",
        "SDE III",
        "Software Engineer III",
        "Software Development Engineer 2",
        "Java Developer",
        "Java Software Engineer",
        "Associate Java Frontend Developer",
        "Cloud Engineer",
        "DevOps Engineer",
        "DevOps Engineer - AI Trainer",
    ):
        assert title_skip_reason(title, include, exclude) == Reason.ROLE, title
    assert not any(pattern.search("javascript developer") for pattern in exclude)


def test_result_pages_are_addressable_for_each_tab(adapter):
    assert adapter.parallel_tabs() == 3
    assert "start=0" in adapter.result_page_url(0)
    assert "start=25" in adapter.result_page_url(1)
    assert "start=50" in adapter.result_page_url(2)
    assert "start=75" in adapter.result_page_url(3)
    assert "software" in adapter.result_page_url(1).lower()


def test_live_feed_noise_is_not_a_target(adapter):
    for title in (
        "Mobile Application Developer Intern",
        "Quality Assurance Engineer - Level 1 (Manual + Automation)",
        "QA Engineer / App Tester Fresher / Junior",
        "SAS Developer_Immediate Joiners",
        "AI Search & Growth Specialist (GTM Engineer)",
    ):
        assert adapter.eligibility(JobCard("1", title)) == Reason.ROLE


def test_applied_card_is_detected_without_opening_the_job(adapter):
    patterns = adapter._applied_on_card
    assert card_says_applied("Software Engineer\nAcme\nApplied", patterns)
    assert card_says_applied("Software Engineer\nApplied 2 days ago", patterns)
    assert not card_says_applied("Software Engineer\nEasy Apply\nBengaluru", patterns)


def test_senior_and_unrelated_titles_are_skipped(adapter):
    include, exclude = adapter._title_include, adapter._title_exclude
    assert title_skip_reason("Senior Software Engineer", include, exclude) == Reason.ROLE
    assert title_skip_reason("Engineering Manager", include, exclude) == Reason.ROLE
    assert title_skip_reason("Data Analyst", include, exclude) == Reason.ROLE
    assert adapter.eligibility(JobCard("1", "Staff AI Engineer")) == Reason.ROLE


def test_experience_above_one_year_is_skipped(adapter):
    patterns = adapter._experience_exclude
    assert experience_skip_reason("We want 0-1 years or freshers.", patterns) is None
    assert experience_skip_reason("Looking for 1 year of internship experience.", patterns) is None
    assert experience_skip_reason("Minimum 2+ years of experience.", patterns) == Reason.EXPERIENCE
    assert experience_skip_reason("1-3 years building backend services.", patterns) == Reason.EXPERIENCE


def test_company_history_is_not_treated_as_required_experience(adapter):
    text = _requirements_text(
        "Freshers 2026 welcome.\nAbout the company\nWe have shipped products for 10 years."
    )
    assert experience_skip_reason(text, adapter._experience_exclude) is None


def test_questions_are_skipped_so_the_next_job_can_start(adapter):
    result = adapter.questions_result()
    assert result.pause is False
    assert result.outcome is Outcome.SKIPPED
    assert result.reason == Reason.QUESTIONS


def test_easy_apply_dialog_steps(adapter):
    review = FakePage(buttons=["Submit application"], visible={MODAL}, texts=["Review your application"])
    assert adapter.classify(review) is S.SUBMIT
    assert adapter.opened_application(review)

    nxt = FakePage(buttons=["Next"], visible={MODAL})
    assert adapter.classify(nxt) is S.CONTINUE

    questions = FakePage(buttons=["Next"], visible={MODAL}, unanswered=1)
    assert adapter.classify(questions) is S.QUESTIONS


def test_sent_dialog_counts_as_success_and_still_open(adapter):
    page = FakePage(
        texts=["Your application was sent to Recollia!"],
        visible={".artdeco-modal-overlay--is-top-layer"},
        buttons=["Not now", "Update profile"],
    )
    assert adapter.classify(page) is S.SUCCESS
    assert adapter.opened_application(page)


def test_success_and_login(adapter):
    sent = FakePage(texts=["Your application was sent to Acme"])
    assert adapter.classify(sent) is S.SUCCESS
    login = FakePage(url="https://www.linkedin.com/login")
    assert adapter.classify(login) is S.LOGIN
    assert adapter.blocker(login) is S.LOGIN


def test_search_page_wording_does_not_pause(adapter):
    page = FakePage(texts=["This site uses a security verification provider"])
    assert adapter.on_listing(page)
    assert adapter.blocker(page) is None


def test_easy_apply_beats_a_plain_apply_label(adapter):
    page = FakePage(buttons=["Easy Apply"])
    assert adapter.find_apply(page).kind is ApplyKind.EASY
    external = FakePage(buttons=["Apply"])
    assert adapter.find_apply(external).kind is ApplyKind.EXTERNAL


def test_job_link_uses_numeric_id(adapter):
    assert adapter.job_view_url(JobCard("4321", "SDE")) == "https://www.linkedin.com/jobs/view/4321/"
    assert adapter.job_view_url(JobCard("h-abc", "SDE")) == ""
