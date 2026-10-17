"""Buster for the Indeed window only.

The extension is downloaded from its public GitHub release the first time Indeed
starts, then loaded when that Chrome window opens. A copy already installed from
the Chrome Web Store is left as-is.
"""
from __future__ import annotations

import io
import logging
import os
import signal
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from ..config import Settings
from .launcher import launch_browser, port_open

log = logging.getLogger(__name__)

BUSTER_ID = "mpbjkejclgfgadiemmefgebjfooflfhl"
BUSTER_ZIP_URL = (
    "https://github.com/dessant/buster/releases/download/v3.4.0/"
    "buster_captcha_solver_for_humans-3.4.0-chrome.zip"
)


def buster_dir(root: Path) -> Path:
    return root / "extensions" / "buster"


def store_install(profile: Path) -> Path:
    return profile / "Default" / "Extensions" / BUSTER_ID


def installed(root: Path) -> bool:
    return (buster_dir(root) / "manifest.json").is_file()


def ensure_buster(root: Path, fetch=None) -> Path:
    """Return the unpacked extension, downloading it when the folder is empty."""
    dest = buster_dir(root)
    if (dest / "manifest.json").is_file():
        return dest
    log.info("Downloading Buster for the Indeed window")
    payload = fetch(BUSTER_ZIP_URL) if fetch is not None else _download(BUSTER_ZIP_URL)
    _unpack(payload, dest)
    if not (dest / "manifest.json").is_file():
        raise RuntimeError("Buster downloaded, but the archive has no manifest.json")
    return dest


def prepare_indeed_browser(settings: Settings, url: str) -> str:
    """Download Buster if needed, then open the Indeed window with it loaded."""
    profile = settings.resolve(settings.browser.profile_dir)
    port = settings.browser.debug_port
    if port_open(port):
        return "Indeed Chrome is already running."
    if store_install(profile).is_dir():
        message = launch_browser(settings, url)
        if not _wait_until_open(port):
            raise RuntimeError(f"Indeed Chrome did not start on port {port}")
        return message
    extension = ensure_buster(settings.root)
    message = launch_browser(settings, url, extension_dir=extension)
    if not _wait_until_open(port):
        raise RuntimeError(f"Indeed Chrome did not start on port {port}")
    return f"Buster is loaded. {message}"


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "autoapply"})
    with urlopen(request, timeout=180) as response:
        return response.read()


def _unpack(payload: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
            raise RuntimeError("Buster archive has an unsafe path")
        archive.extractall(dest)
    if (dest / "manifest.json").is_file():
        return
    folders = [path for path in dest.iterdir() if path.is_dir()]
    if len(folders) == 1 and (folders[0] / "manifest.json").is_file():
        for item in folders[0].iterdir():
            item.rename(dest / item.name)
        folders[0].rmdir()


def _command_loads(port: int, extension: Path) -> bool:
    try:
        command = _listener_command(port)
    except Exception:
        log.debug("Could not read the Indeed Chrome command", exc_info=True)
        return False
    return str(extension) in command


def _listener_command(port: int) -> str:
    pids = _listener_pids(port)
    if not pids:
        return ""
    return subprocess.check_output(["ps", "-p", str(pids[0]), "-o", "command="], text=True)


def _listener_pids(port: int) -> list[int]:
    try:
        output = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return []
    return [int(line) for line in output.split() if line.strip().isdigit()]


def _quit_listener(port: int, profile: Path) -> None:
    """Close only the Chrome that is listening on this portal's debug port."""
    command = _listener_command(port)
    if not command or (str(profile) not in command and f"--remote-debugging-port={port}" not in command):
        raise RuntimeError(
            "Indeed Chrome is open without Buster. Quit that window, then start Indeed again."
        )
    if "chrome" not in command.lower() and "chromium" not in command.lower():
        raise RuntimeError("Port is in use by something other than Chrome. Quit it, then start Indeed again.")
    for pid in _listener_pids(port):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if not port_open(port):
            return
        time.sleep(0.2)
    raise RuntimeError("Indeed Chrome did not close. Quit that window, then start Indeed again.")


def _wait_until_open(port: int, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        time.sleep(0.25)
    return False
