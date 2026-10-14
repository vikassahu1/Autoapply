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
from pathlib import Path

from .config import Settings


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


def launch_browser(settings: Settings, url: str) -> str:
    port = settings.browser.debug_port
    if port_open(port):
        return f"Chrome is already listening on port {port}. Nothing to do."
    profile = settings.resolve(settings.browser.profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
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
        url,
    ]
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)
    return f"Started Chrome with profile {profile}. Log in to the portal in that window."
