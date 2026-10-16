"""One dashboard, two portals. Each portal has its own Chrome window and run."""
from __future__ import annotations

import dataclasses
import logging

from .app import AutoApplyApp
from .buster import prepare_indeed_browser
from .config import Settings
from .launcher import launch_browser
from .portals import REGISTRY, build_adapter
from .store import CsvJobStore

log = logging.getLogger(__name__)

PORTALS = tuple(REGISTRY)


class PortalDesk:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = CsvJobStore(settings.resolve(settings.storage.path))
        self.notices: dict[str, str] = {}
        self.apps = {
            name: AutoApplyApp(
                dataclasses.replace(settings, portal=name, browser=settings.browser_for(name)),
                build_adapter(name, settings.root),
                self.store,
            )
            for name in PORTALS
        }

    def start(self, name: str) -> bool:
        app = self.apps[name]
        if app._thread and app._thread.is_alive():
            return False
        if name == "indeed" and not self._prepare_indeed(app):
            return True
        return app.start()

    def start_all(self) -> None:
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
        app = self.apps[name]
        if name == "indeed":
            self._prepare_indeed(app)
            return self.notices.get(name, "")
        message = launch_browser(app.settings, app.adapter.start_url)
        self.notices[name] = message
        return message

    def _prepare_indeed(self, app: AutoApplyApp) -> bool:
        """True when Buster is in place. A failure is shown on the Indeed panel."""
        try:
            self.notices["indeed"] = prepare_indeed_browser(app.settings, app.adapter.start_url)
            return True
        except Exception as exc:
            log.exception("Could not prepare Buster for Indeed")
            self.notices["indeed"] = str(exc)
            return False

    def launch_all(self) -> None:
        for name in self.apps:
            self.launch(name)

    def status(self) -> dict:
        return {
            "portals": {name: app.status() for name, app in self.apps.items()},
            "notices": dict(self.notices),
        }
