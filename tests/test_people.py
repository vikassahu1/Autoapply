from pathlib import Path

from autoapply.browser.launcher import spawn_chrome
from autoapply.config import PROJECT_ROOT, load_settings
from autoapply.users import list_users, load_user


def test_active_user_comes_from_settings():
    settings = load_settings()
    user = load_user(settings.user, PROJECT_ROOT)
    assert user.key == "vikas"
    assert user.name
    assert user.chrome_profile("linkedin", ".browser-profile") == ".browser-profile"
    assert user.applications_path() == "data/applications.csv"
    assert "vikas" in list_users(PROJECT_ROOT)


def test_a_new_user_file_is_selected_by_key(tmp_path: Path):
    folder = tmp_path / "config" / "users"
    folder.mkdir(parents=True)
    (folder / "ada.toml").write_text(
        'name = "Ada Lovelace"\nemail = "ada@example.com"\nresume = "ada.pdf"\n'
        '[chrome]\nindeed = ".browser-profiles/ada/indeed"\n',
        encoding="utf-8",
    )
    user = load_user("ada", tmp_path)
    assert user.key == "ada"
    assert user.email == "ada@example.com"
    assert user.chrome_profile("indeed", "fallback") == ".browser-profiles/ada/indeed"
    assert user.applications_path() == "data/ada/applications.csv"


def test_mac_chrome_is_a_new_background_process():
    cmd = spawn_chrome(
        ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--remote-debugging-port=9222"]
    )
    import sys

    if sys.platform == "darwin":
        assert cmd[:4] == ["open", "-g", "-n", "-a"]
        assert cmd[4] == "/Applications/Google Chrome.app"
        assert "--remote-debugging-port=9222" in cmd
    else:
        assert cmd[0].endswith("Google Chrome") or "chrome" in cmd[0].lower()
