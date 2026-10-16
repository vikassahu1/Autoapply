from autoapply.models import Outcome, PageState as S, Reason
from conftest import ScriptedPage


def run(flow, steps, **kw):
    page = ScriptedPage(steps, **kw)
    return flow.run(page), page


def test_click_through_to_confirmed_submit(flow):
    result, page = run(flow, [S.CONTINUE, S.CONTINUE, S.SUBMIT, S.SUCCESS])
    assert (result.outcome, result.reason) == (Outcome.SUBMITTED, Reason.CONFIRMED)
    assert page.continue_clicks == 2 and page.submit_clicks == 1


def test_questions_page_is_skipped_without_submitting(flow):
    result, page = run(flow, [S.CONTINUE, S.QUESTIONS])
    assert (result.outcome, result.reason) == (Outcome.SKIPPED, Reason.QUESTIONS)
    assert page.submit_clicks == 0


def test_leaving_the_portal_is_skipped(flow):
    result, _ = run(flow, [S.EXTERNAL])
    assert (result.outcome, result.reason) == (Outcome.SKIPPED, Reason.EXTERNAL)


def test_already_applied_is_skipped(flow):
    result, _ = run(flow, [S.ALREADY_APPLIED])
    assert result.reason == Reason.ALREADY_APPLIED


def test_a_cleared_captcha_continues_to_submit(flow):
    def clear(page):
        page.solved = True
        return True

    flow.adapter.clear_captcha = clear
    result, page = run(flow, [S.CAPTCHA, S.SUBMIT, S.SUCCESS])
    assert (result.outcome, result.reason) == (Outcome.SUBMITTED, Reason.CONFIRMED)
    assert page.submit_clicks == 1


def test_captcha_requests_a_pause_not_a_skip(flow):
    result, _ = run(flow, [S.CONTINUE, S.CAPTCHA])
    assert result.outcome is Outcome.NEEDS_MANUAL and result.reason == Reason.CAPTCHA and result.pause


def test_captcha_appearing_right_after_a_click_is_noticed(flow):
    # signature does not change (same step), but the page now shows a CAPTCHA
    page = ScriptedPage([S.CONTINUE, S.CAPTCHA], stuck=True)
    original = flow.adapter.click_continue

    def click_then_captcha(p):
        original(p)
        p.i = 1  # CAPTCHA appears; signature "1" differs, so also fine either way

    flow.adapter.click_continue = click_then_captcha
    result = flow.run(page)
    assert result.reason == Reason.CAPTCHA and result.pause


def test_login_wall_requests_a_pause(flow):
    result, _ = run(flow, [S.LOGIN])
    assert result.reason == Reason.LOGIN and result.pause


def test_unrecognized_page_needs_manual_and_pauses(flow):
    result, _ = run(flow, [S.UNKNOWN])
    assert result.outcome is Outcome.NEEDS_MANUAL and result.reason == Reason.UNRECOGNIZED_PAGE and result.pause


def test_step_that_does_not_advance_is_not_retried_forever(flow):
    result, page = run(flow, [S.CONTINUE], stuck=True)
    assert result.reason == Reason.STEP_STUCK
    assert page.continue_clicks == 1


def test_too_many_steps(flow):
    result, _ = run(flow, [S.CONTINUE] * 50)
    assert result.reason == Reason.TOO_MANY_STEPS


def test_submit_without_confirmation_is_never_reported_as_submitted(flow):
    result, page = run(flow, [S.SUBMIT, S.UNKNOWN])
    assert (result.outcome, result.reason) == (Outcome.NEEDS_MANUAL, Reason.SUBMIT_UNVERIFIED)
    assert page.submit_clicks == 1  # clicked once, never retried


def test_submit_bounced_back_to_questions_is_rejected(flow):
    result, _ = run(flow, [S.SUBMIT, S.QUESTIONS])
    assert result.reason == Reason.SUBMIT_REJECTED


def test_captcha_after_submit_click_pauses(flow):
    result, _ = run(flow, [S.SUBMIT, S.CAPTCHA])
    assert result.reason == Reason.CAPTCHA and result.pause


def test_tab_closed_mid_flow_is_reported_not_crashed(flow):
    page = ScriptedPage([S.SUBMIT, S.UNKNOWN])

    def boom(p):
        p.closed = True
        raise RuntimeError("Target closed")

    flow.adapter.click_submit = boom
    result = flow.run(page)
    assert (result.outcome, result.reason) == (Outcome.NEEDS_MANUAL, Reason.TAB_CLOSED)
