from autoapply.browser import quiet_click, quiet_scroll


class _DomLocator:
    def __init__(self):
        self.calls = []

    def evaluate(self, js):
        self.calls.append(("dom", js))

    def click(self, **kw):
        self.calls.append(("native", kw))


class _FakeLocator:
    def click(self, **kw):
        self.kw = kw


def test_quiet_click_uses_a_dom_click_when_the_locator_can_evaluate():
    locator = _DomLocator()
    quiet_click(locator, timeout=1000)
    assert locator.calls == [("dom", "el => el.click()")]


def test_quiet_click_falls_back_for_test_doubles_without_evaluate():
    locator = _FakeLocator()
    quiet_click(locator, timeout=1000, force=True)
    assert locator.kw["timeout"] == 1000
    assert locator.kw["force"] is True


def test_quiet_scroll_uses_dom_scroll_when_possible():
    locator = _DomLocator()
    quiet_scroll(locator)
    assert locator.calls[0][0] == "dom"
    assert "scrollIntoView" in locator.calls[0][1]


def test_native_click_is_only_used_when_asked():
    locator = _DomLocator()
    quiet_click(locator, timeout=500, native=True)
    assert locator.calls[0][0] == "native"
    assert locator.calls[0][1]["force"] is True
