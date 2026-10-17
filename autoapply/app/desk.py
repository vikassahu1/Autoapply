"""One dashboard, two portals. Each portal has its own Chrome window and run."""
from __future__ import annotations

import dataclasses
import logging

from ..browser import launch_browser, port_open, prepare_indeed_browser
from ..config import Settings, StorageSettings
from ..core.store import CsvJobStore
from ..portals import REGISTRY, build_adapter
from ..users import load_user
from .runner import AutoApplyApp

log = logging.getLogger(__name__)

PORTALS = tuple(REGISTRY)


class PortalDesk:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.user = load_user(settings.user, settings.root)
        self.notices: dict[str, str] = {}
        self.store = CsvJobStore(settings.resolve(self.user.applications_path()))
        self.apps = {
            name: AutoApplyApp(
                dataclasses.replace(
                    settings,
                    portal=name,
                    browser=settings.browser_for(name, self.user),
                    storage=StorageSettings(path=self.user.applications_path()),
                ),
                build_adapter(name, settings.root),
                self.store,
            )
            for name in PORTALS
        }

    def start(self, name: str) -> bool:
        app = self.apps[name]
        if app._thread and app._thread.is_alive():
            return False
        if not self.ensure_browser(name, navigate=False):
            return True
        return app.start()

    def start_all(self) -> None:
        for name in self.apps:
            self.ensure_browser(name, navigate=False)
        for name in self.apps:
            self.start(name)

    def pause(self, name: str) -> None:
        self.apps[name].pause()

    def pause_all(self) -> None:
        for app in self.apps.values():
            app.pause()

    def resume(self, name: str) -> None:
        self.apps[name].resume()

    def resume_all(self) -> None:
        for app in self.apps.values():
            app.resume()

    def stop(self, name: str) -> None:
        self.apps[name].stop()

    def stop_all(self) -> None:
        for app in self.apps.values():
            app.stop()

    def launch(self, name: str) -> str:
        self.ensure_browser(name, navigate=True)
        return self.notices.get(name, "")

    def ensure_browser(self, name: str, navigate: bool) -> bool:
        """True when this portal's Chrome is listening. Starts it in the background if needed."""
        app = self.apps[name]
        url = app.adapter.start_url if navigate else ""
        try:
            if name == "indeed":
                self.notices[name] = prepare_indeed_browser(app.settings, url)
            elif port_open(app.settings.browser.debug_port):
                self.notices[name] = f"Chrome is already listening on port {app.settings.browser.debug_port}."
            else:
                self.notices[name] = launch_browser(app.settings, url)
            return True
        except Exception as exc:
            log.exception("Could not start Chrome for %s", name)
            self.notices[name] = str(exc)
            return False

    def launch_all(self) -> None:
        for name in self.apps:
            self.launch(name)

    def status(self) -> dict:
        return {
            "portals": {name: app.status() for name, app in self.apps.items()},
            "notices": dict(self.notices),
        }
