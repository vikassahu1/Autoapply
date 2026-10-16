"""Command line entry point.

  python -m autoapply launch-browser   start the dedicated Chrome (log in once)
  python -m autoapply doctor           check selectors against your logged-in page
  python -m autoapply run [--start]    open the dashboard (and optionally start)
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import logging.handlers
import sys
import webbrowser

from .app import AutoApplyApp
from .browser import BrowserSession, BrowserUnavailable
from .config import load_settings
from .launcher import launch_browser
from .portals import build_adapter
from .store import CsvJobStore
from .ui.server import make_server


def _setup_logging(settings) -> None:
    log_file = settings.resolve("logs/autoapply.log")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    rotating = logging.handlers.RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    rotating.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[console, rotating])


def cmd_launch(settings, adapter, args) -> int:
    print(launch_browser(settings, adapter.start_url))
    return 0


def cmd_doctor(settings, adapter, args) -> int:
    """Read-only: attaches to Chrome, opens the feed in a new tab, reports what matched."""
    session = BrowserSession(settings.browser)
    try:
        session.connect()
    except BrowserUnavailable as exc:
        print(exc)
        return 1
    try:
        page = session.open_worker_tab(adapter.start_url)
        page.wait_for_timeout(3000)
        print("\n".join(adapter.diagnose(page)))
        session.close_page(page)
    finally:
        session.close()
    return 0


def cmd_run(settings, adapter, args) -> int:
    store = CsvJobStore(settings.resolve(settings.storage.path))
    app = AutoApplyApp(settings, adapter, store)
    server = make_server(app, settings.ui.host, settings.ui.port)
    url = f"http://127.0.0.1:{settings.ui.port}/"
    print(f"Dashboard: {url}   (Ctrl+C to quit)")
    if not args.no_open:
        webbrowser.open(url)
    if args.start:
        app.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autoapply")
    parser.add_argument("--config", help="path to settings.toml")
    parser.add_argument("--portal", help="override portal in settings, for example linkedin")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("launch-browser", help="start the dedicated Chrome")
    sub.add_parser("doctor", help="check selectors on your live page (clicks nothing)")
    run = sub.add_parser("run", help="start the dashboard")
    run.add_argument("--start", action="store_true", help="begin applying immediately")
    run.add_argument("--no-open", action="store_true", help="do not open the dashboard in a browser")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    if args.portal:
        settings = dataclasses.replace(settings, portal=args.portal)
    _setup_logging(settings)
    adapter = build_adapter(settings.portal, settings.root)
    handler = {"launch-browser": cmd_launch, "doctor": cmd_doctor, "run": cmd_run}[args.command]
    return handler(settings, adapter, args)


if __name__ == "__main__":
    sys.exit(main())
