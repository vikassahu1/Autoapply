"""Typed settings loaded from TOML. Unknown keys are rejected so typos are caught."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build(cls: type, data: dict[str, Any]):
    unknown = set(data) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"Unknown setting(s) in [{cls.__name__}]: {sorted(unknown)}")
    return cls(**data)


@dataclass(frozen=True)
class BrowserSettings:
    debug_port: int = 9222
    chrome_path: str = ""
    profile_dir: str = ".browser-profile"

    @property
    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}"


@dataclass(frozen=True)
class RunSettings:
    max_applications: int = 25  # jobs handled per run; 0 = unlimited
    delay_between_jobs_s: tuple[float, float] = (4.0, 9.0)
    max_consecutive_errors: int = 3


@dataclass(frozen=True)
class FlowSettings:
    max_steps: int = 8
    poll_interval_s: float = 0.5
    step_timeout_s: float = 15.0
    unknown_page_grace_s: float = 8.0
    submit_verify_timeout_s: float = 25.0
    application_load_timeout_s: float = 30.0  # smartapply tab progress bar / review page
    pause_on_unknown_page: bool = True


@dataclass(frozen=True)
class StorageSettings:
    path: str = "data/applications.csv"


@dataclass(frozen=True)
class UiSettings:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(frozen=True)
class Settings:
    portal: str
    browser: BrowserSettings
    run: RunSettings
    flow: FlowSettings
    storage: StorageSettings
    ui: UiSettings
    root: Path
    browsers: dict[str, BrowserSettings] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.browsers is None:
            object.__setattr__(self, "browsers", {})

    def resolve(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.root / path

    def browser_for(self, portal: str) -> BrowserSettings:
        """Each portal gets its own Chrome window so the two runs do not share tabs."""
        custom = self.browsers.get(portal)
        if custom is not None:
            return custom
        if portal == "indeed":
            return BrowserSettings(
                debug_port=self.browser.debug_port + 1,
                chrome_path=self.browser.chrome_path,
                profile_dir=".browser-profile-indeed",
            )
        return self.browser


def load_settings(path: str | Path | None = None) -> Settings:
    path = Path(path) if path else PROJECT_ROOT / "config" / "settings.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    root = path.resolve().parent.parent

    portal = data.pop("portal", "indeed")
    raw_browsers = data.pop("browsers", {})
    run = dict(data.pop("run", {}))
    if "delay_between_jobs_s" in run:
        low, high = run["delay_between_jobs_s"]
        if not 0 <= low <= high:
            raise ValueError("run.delay_between_jobs_s must be [min, max] with 0 <= min <= max")
        run["delay_between_jobs_s"] = (float(low), float(high))

    browser = _build(BrowserSettings, data.pop("browser", {}))
    browsers: dict[str, BrowserSettings] = {}
    if not isinstance(raw_browsers, dict):
        raise ValueError("browsers must be a table of portal names")
    for name, block in raw_browsers.items():
        if not isinstance(block, dict):
            raise ValueError(f"browsers.{name} must be a table")
        browsers[str(name)] = _build(BrowserSettings, {**{
            "debug_port": browser.debug_port,
            "chrome_path": browser.chrome_path,
            "profile_dir": browser.profile_dir,
        }, **block})

    settings = Settings(
        portal=portal,
        browser=browser,
        browsers=browsers,
        run=_build(RunSettings, run),
        flow=_build(FlowSettings, data.pop("flow", {})),
        storage=_build(StorageSettings, data.pop("storage", {})),
        ui=_build(UiSettings, data.pop("ui", {})),
        root=root,
    )
    if data:
        raise ValueError(f"Unknown top-level setting(s): {sorted(data)}")
    return settings


def load_portal_config(name: str, root: Path = PROJECT_ROOT) -> dict[str, Any]:
    path = root / "config" / "portals" / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"No portal config at {path}")
    return tomllib.loads(path.read_text(encoding="utf-8"))
