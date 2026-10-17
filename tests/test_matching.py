from autoapply.config import PROJECT_ROOT, load_portal_config
from autoapply.core.models import Reason
from autoapply.matching import TitleFilters, titles_close


def test_hyphen_and_suffix_still_match():
    assert titles_close("Full Stack Developer", "Full-Stack Developer")
    assert titles_close("Full Stack Developer", "Full Stack Developer at SAMS DIGITAL")


def test_unrelated_title_does_not_match():
    assert not titles_close("Full Stack Developer", "Quality Assurance Engineer")


def test_portal_title_filters_load_from_toml():
    filters = TitleFilters.from_config(load_portal_config("indeed", PROJECT_ROOT)["filters"])
    assert filters.skip_reason("Consultant Radiologist") == Reason.ROLE
    assert filters.skip_reason("Software Engineer") is None
