from autoapply.matching import titles_close


def test_hyphen_and_suffix_still_match():
    assert titles_close("Full Stack Developer", "Full-Stack Developer")
    assert titles_close("Full Stack Developer", "Full Stack Developer at SAMS DIGITAL")


def test_unrelated_title_does_not_match():
    assert not titles_close("Full Stack Developer", "Quality Assurance Engineer")
