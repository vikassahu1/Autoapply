import io
import zipfile
from pathlib import Path

from autoapply.browser import BUSTER_ID, chrome_args, ensure_buster, prepare_indeed_browser
from autoapply.config import BrowserSettings, FlowSettings, RunSettings, Settings, StorageSettings, UiSettings


def _settings(root: Path, port: int = 9223) -> Settings:
    return Settings(
        portal="indeed",
        user="vikas",
        browser=BrowserSettings(debug_port=port, chrome_path="", profile_dir="profile"),
        run=RunSettings(),
        flow=FlowSettings(),
        storage=StorageSettings(),
        ui=UiSettings(),
        root=root,
    )


def _zip(nested: bool) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        name = "inner/manifest.json" if nested else "manifest.json"
        archive.writestr(name, '{"name": "Buster"}')
    return buffer.getvalue()


def test_ensure_buster_downloads_once(tmp_path):
    calls = {"n": 0}

    def fetch(_url):
        calls["n"] += 1
        return _zip(nested=False)

    first = ensure_buster(tmp_path, fetch=fetch)
    second = ensure_buster(tmp_path, fetch=fetch)
    assert first == second
    assert calls["n"] == 1
    assert (first / "manifest.json").is_file()


def test_ensure_buster_unwraps_a_single_folder(tmp_path):
    dest = ensure_buster(tmp_path, fetch=lambda _url: _zip(nested=True))
    assert (dest / "manifest.json").is_file()
    assert not (dest / "inner").exists()


def test_indeed_launch_loads_the_extension(tmp_path):
    chrome = tmp_path / "Chrome"
    chrome.write_text("")
    settings = _settings(tmp_path)
    settings = Settings(
        portal=settings.portal,
        user=settings.user,
        browser=BrowserSettings(debug_port=9223, chrome_path=str(chrome), profile_dir="profile"),
        run=settings.run,
        flow=settings.flow,
        storage=settings.storage,
        ui=settings.ui,
        root=settings.root,
    )
    extension = tmp_path / "extensions" / "buster"
    args = chrome_args(settings, "https://in.indeed.com/", extension)
    assert f"--load-extension={extension}" in args
    assert "--disable-features=DisableLoadExtensionCommandLineSwitch" in args
    assert args[-1] == "https://in.indeed.com/"


def test_open_window_skips_a_second_download_when_buster_is_already_loaded(tmp_path, monkeypatch):
    extension = tmp_path / "extensions" / "buster"
    extension.mkdir(parents=True)
    (extension / "manifest.json").write_text("{}")
    monkeypatch.setattr("autoapply.browser.buster.ensure_buster", lambda root, fetch=None: extension)
    monkeypatch.setattr("autoapply.browser.buster.port_open", lambda _port: True)
    monkeypatch.setattr("autoapply.browser.buster._command_loads", lambda _port, _extension: True)
    message = prepare_indeed_browser(_settings(tmp_path), "https://in.indeed.com/")
    assert message == "Indeed Chrome is already running."


def test_store_install_is_not_replaced(tmp_path, monkeypatch):
    profile = tmp_path / "profile" / "Default" / "Extensions" / BUSTER_ID / "1.0"
    profile.mkdir(parents=True)
    monkeypatch.setattr("autoapply.browser.buster.ensure_buster", lambda root, fetch=None: tmp_path / "missing")
    monkeypatch.setattr("autoapply.browser.buster.port_open", lambda _port: False)
    monkeypatch.setattr("autoapply.browser.buster._wait_until_open", lambda _port, timeout=15: True)
    seen = {}

    def launch(settings, url, extension_dir=None):
        seen["extension"] = extension_dir
        return "started"

    monkeypatch.setattr("autoapply.browser.buster.launch_browser", launch)
    prepare_indeed_browser(_settings(tmp_path), "https://in.indeed.com/")
    assert seen["extension"] is None
