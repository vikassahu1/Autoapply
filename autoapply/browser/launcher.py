"""Starts the dedicated Chrome (own profile, remote debugging on) for the tool to drive.

Why a dedicated profile: you log into Indeed once in it and the session persists,
so no credentials are stored anywhere; it is a separate window from your daily
browsing; and Chrome 136+ refuses remote debugging on the default profile anyway.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from ..config import Settings


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def find_chrome(configured: str = "") -> str:
    if configured:
        if Path(configured).exists():
            return configured
        raise FileNotFoundError(f"chrome_path does not exist: {configured}")
    candidates = {
        "darwin": ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
        "win32": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ],
    }.get(sys.platform, [])
    for path in candidates:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("Chrome not found. Set browser.chrome_path in config/settings.toml")


def chrome_app_bundle(binary: str) -> str:
    path = Path(binary)
    for parent in path.parents:
        if parent.suffix == ".app":
            return str(parent)
    return binary


def chrome_args(settings: Settings, url: str = "", extension_dir: Path | None = None) -> list[str]:
    """Command used to open this portal's Chrome window."""
    port = settings.browser.debug_port
    profile = settings.resolve(settings.browser.profile_dir)
    args = [
        find_chrome(settings.browser.chrome_path),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        # Keep background tabs responsive while you work in other windows.
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
    ]
    if extension_dir is not None:
        # Chrome 137+ ignores --load-extension unless this feature is turned back on.
        args.append("--disable-features=DisableLoadExtensionCommandLineSwitch")
        args.append(f"--load-extension={extension_dir}")
    if url:
        args.append(url)
    return args


def spawn_chrome(args: list[str]) -> list[str]:
    """A new Chrome process in the background, not the already-open daily window."""
    binary, flags = args[0], args[1:]
    if sys.platform == "darwin":
        return ["open", "-g", "-n", "-a", chrome_app_bundle(binary), "--args", *flags]
    return args


def wait_until_open(port: int, timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        time.sleep(0.25)
    return False


def launch_browser(settings: Settings, url: str = "", extension_dir: Path | None = None) -> str:
    port = settings.browser.debug_port
    if port_open(port):
        return f"Chrome is already listening on port {port}. Nothing to do."
    profile = settings.resolve(settings.browser.profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    args = spawn_chrome(chrome_args(settings, url, extension_dir))
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)
    if not wait_until_open(port):
        raise RuntimeError(
            f"Chrome did not start on port {port}. Quit extra Chrome windows and try Open window again."
        )
    return f"Started Chrome with profile {profile}. Log in to the portal in that window if asked."
