"""What every applicant must provide.

Add a person: copy config/users/vikas.toml to config/users/<key>.toml.
Switch:       set user = "<key>" in config/settings.toml.

To add a new field later, put it on User and in registry._USER_FIELDS.
Unknown TOML keys are rejected so typos fail fast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Applicant(Protocol):
    """Contract the rest of the app uses. Do not import TOML from engine/portals."""

    key: str
    name: str
    email: str
    phone: str
    location: str
    resume: str

    def chrome_profile(self, portal: str, fallback: str) -> str:
        """Chrome user-data-dir for this portal."""
        ...

    def applications_path(self) -> str:
        """CSV path for this person's application log."""
        ...


@dataclass(frozen=True)
class User:
    """Default Applicant, loaded from one TOML file."""

    key: str
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    resume: str = ""
    applications: str = ""
    chrome: dict[str, str] = field(default_factory=dict)

    def chrome_profile(self, portal: str, fallback: str) -> str:
        return self.chrome.get(portal) or fallback

    def applications_path(self) -> str:
        return self.applications or f"data/{self.key}/applications.csv"
