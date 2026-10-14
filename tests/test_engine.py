import threading
import time

from autoapply.control import RunState
from autoapply.engine import Engine
from autoapply.models import ApplyKind, Outcome, PageState as S, Reason
from conftest import FAST_RUN, FakeBrowser, ScriptedAdapter, ScriptedPage, card


def make_engine(adapter, browser, store, flow, controller):
    return Engine(adapter, browser, store, flow, controller, FAST_RUN, sleep=lambda s: None)


def latest(store, key):
    return store._latest[key]


def test_mixed_batch_each_job_gets_the_right_outcome(store, controller):
    jobs = [card(k) for k in "abcdef"]
    adapter = ScriptedAdapter(jobs, apply_kinds={"c": ApplyKind.EXTERNAL, "e": ApplyKind.NONE})
    pages = {
        "a": ScriptedPage([S.CONTINUE, S.CONTINUE, S.SUBMIT, S.SUCCESS]),
        "b": ScriptedPage([S.CONTINUE, S.QUESTIONS]),
        "d": ScriptedPage([S.EXTERNAL]),  # Apply click opened a company-site tab
        "f": ScriptedPage([S.CONTINUE, S.SUBMIT, S.UNKNOWN]),  # unverified submit
    }
    browser = FakeBrowser(pages)
    from autoapply.flows import ClickThroughFlow
    from conftest import FAST_FLOW
    flow = ClickThroughFlow(adapter, FAST_FLOW, controller)
    make_engine(adapter, browser, store, flow, controller).run()

    got = {k: (latest(store, k)["outcome"], latest(store, k)["reason"]) for k in "abcdef"}
    assert got == {
        "a": ("submitted", Reason.CONFIRMED),
        "b": ("skipped", Reason.QUESTIONS),
        "c": ("skipped", Reason.EXTERNAL),
        "d": ("skipped", Reason.EXTERNAL),
        "e": ("skipped", Reason.NO_APPLY_BUTTON),
        "f": ("needs_manual", Reason.SUBMIT_UNVERIFIED),
    }
    # new tabs are closed afterwards; jobs that never opened a tab close nothing
    assert set(map(id, browser.closed)) == {id(pages[k]) for k in "abdf"}
    snap = controller.snapshot()
    assert snap["counts"] == {"attempted": 6, "submitted": 1, "skipped": 4, "needs_manual": 1}
    assert snap["state"] == "stopped"


def test_processed_jobs_are_never_applied_to_again_even_after_restart(tmp_path, controller):
    from autoapply.control import Controller
    from autoapply.flows import ClickThroughFlow
    from autoapply.store import CsvJobStore
    from conftest import FAST_FLOW

    path = tmp_path / "applications.csv"
    adapter = ScriptedAdapter([card("a")])
    browser = FakeBrowser({"a": ScriptedPage([S.SUBMIT, S.SUCCESS])})
    flow = ClickThroughFlow(adapter, FAST_FLOW, controller)
    make_engine(adapter, browser, CsvJobStore(path), flow, controller).run()
    assert browser.clicks == ["a"]

    # "restart": brand-new store object reading the same file, new controller
    c2 = Controller()
    browser2 = FakeBrowser({"a": ScriptedPage([S.SUBMIT, S.SUCCESS])})
    flow2 = ClickThroughFlow(adapter, FAST_FLOW, c2)
    make_engine(adapter, browser2, CsvJobStore(path), flow2, c2).run()
    assert browser2.clicks == []


def test_company_and_job_are_saved_to_the_file(store, controller):
    from autoapply.flows import ClickThroughFlow
    from conftest import FAST_FLOW

    adapter = ScriptedAdapter([card("a", "Frontend Developer", "Kognito Kube")])
    browser = FakeBrowser({"a": ScriptedPage([S.SUBMIT, S.SUCCESS])})
    make_engine(adapter, browser, store, ClickThroughFlow(adapter, FAST_FLOW, controller), controller).run()
    text = store.path.read_text(encoding="utf-8")
    assert "Kognito Kube" in text and "Frontend Developer" in text and "submitted" in text


def test_captcha_pauses_then_resume_finishes_the_same_application(store, controller):
    from autoapply.flows import ClickThroughFlow
    from conftest import FAST_FLOW

    adapter = ScriptedAdapter([card("a"), card("b")])
    page_a = ScriptedPage([S.CONTINUE, S.CAPTCHA, S.SUBMIT, S.SUCCESS])
    page_b = ScriptedPage([S.SUBMIT, S.SUCCESS])
    browser = FakeBrowser({"a": page_a, "b": page_b})
    engine = make_engine(adapter, browser, store, ClickThroughFlow(adapter, FAST_FLOW, controller), controller)

    thread = threading.Thread(target=engine.run)
    thread.start()
    deadline = time.time() + 5
    while controller.state is not RunState.PAUSED and time.time() < deadline:
        time.sleep(0.01)

    assert controller.state is RunState.PAUSED
    assert "captcha" in controller.pause_reason
    assert latest(store, "a")["reason"] == Reason.CAPTCHA      # honest while waiting
    assert browser.clicks == ["a"]                              # no further applications opened
    assert page_a not in browser.closed and page_a in browser.fronted  # tab left open and surfaced

    page_a.solved = True
    controller.resume()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert latest(store, "a")["outcome"] == "submitted"
    assert latest(store, "b")["outcome"] == "submitted"
    assert page_a.submit_clicks == 1


def test_stop_while_paused_ends_run_and_leaves_tab_open(store, controller):
    from autoapply.flows import ClickThroughFlow
    from conftest import FAST_FLOW

    adapter = ScriptedAdapter([card("a"), card("b")])
    page_a = ScriptedPage([S.CAPTCHA])
    browser = FakeBrowser({"a": page_a, "b": ScriptedPage([S.SUBMIT, S.SUCCESS])})
    engine = make_engine(adapter, browser, store, ClickThroughFlow(adapter, FAST_FLOW, controller), controller)

    thread = threading.Thread(target=engine.run)
    thread.start()
    deadline = time.time() + 5
    while controller.state is not RunState.PAUSED and time.time() < deadline:
        time.sleep(0.01)
    controller.request_stop()
    thread.join(timeout=5)

    assert not thread.is_alive() and controller.state is RunState.STOPPED
    assert browser.clicks == ["a"] and page_a not in browser.closed
    assert latest(store, "a")["reason"] == Reason.CAPTCHA
    assert not store.seen("b")


def test_unexpected_error_on_one_job_does_not_stop_the_rest(store, controller):
    from autoapply.flows import ClickThroughFlow
    from conftest import FAST_FLOW

    adapter = ScriptedAdapter([card("a"), card("b")], fail_on={"a"})
    browser = FakeBrowser({"b": ScriptedPage([S.SUBMIT, S.SUCCESS])})
    make_engine(adapter, browser, store, ClickThroughFlow(adapter, FAST_FLOW, controller), controller).run()
    assert latest(store, "a")["reason"].startswith(Reason.ERROR)
    assert latest(store, "b")["outcome"] == "submitted"


def test_pane_mismatch_never_applies(store, controller):
    from autoapply.flows import ClickThroughFlow
    from conftest import FAST_FLOW

    adapter = ScriptedAdapter([card("a")])
    adapter.select_job = lambda page, c: False
    browser = FakeBrowser({"a": ScriptedPage([S.SUBMIT, S.SUCCESS])})
    make_engine(adapter, browser, store, ClickThroughFlow(adapter, FAST_FLOW, controller), controller).run()
    assert browser.clicks == []
    assert latest(store, "a")["reason"] == Reason.PANE_MISMATCH


def test_apply_click_with_no_effect_is_flagged(store, controller):
    from autoapply.flows import ClickThroughFlow
    from conftest import FAST_FLOW

    adapter = ScriptedAdapter([card("a")])
    browser = FakeBrowser({"a": None})
    make_engine(adapter, browser, store, ClickThroughFlow(adapter, FAST_FLOW, controller), controller).run()
    assert latest(store, "a")["reason"] == Reason.APPLY_NO_EFFECT


def test_max_applications_limits_a_run(store, controller):
    from autoapply.config import RunSettings
    from autoapply.flows import ClickThroughFlow
    from conftest import FAST_FLOW

    adapter = ScriptedAdapter([card(k) for k in "abc"])
    browser = FakeBrowser({k: ScriptedPage([S.SUBMIT, S.SUCCESS]) for k in "abc"})
    cfg = RunSettings(max_applications=2, delay_between_jobs_s=(0, 0), max_consecutive_errors=3)
    Engine(adapter, browser, store, ClickThroughFlow(adapter, FAST_FLOW, controller), controller, cfg,
           sleep=lambda s: None).run()
    assert browser.clicks == ["a", "b"]
