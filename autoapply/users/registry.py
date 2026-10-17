"""Load applicants from config/users/*.toml.

Switch: config/settings.toml  →  user = "vikas"
Add:    copy config/users/vikas.toml to config/users/<key>.toml
Remove: delete that file (and do not leave user= pointing at it)
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from ..config import PROJECT_ROOT
from .contract import User

_USER_FIELDS = {"name", "email", "phone", "location", "resume", "applications", "chrome"}


def users_dir(root: Path = PROJECT_ROOT) -> Path:
    return root / "config" / "users"


def list_users(root: Path = PROJECT_ROOT) -> list[str]:
    folder = users_dir(root)
    if not folder.is_dir():
        return []
    return sorted(path.stem for path in folder.glob("*.toml"))


def load_user(key: str, root: Path = PROJECT_ROOT) -> User:
    path = users_dir(root) / f"{key}.toml"
    if not path.is_file():
        available = ", ".join(list_users(root)) or "(none)"
        raise FileNotFoundError(
            f"No user {key!r} in {users_dir(root)}. Available: {available}. "
            "Add config/users/<key>.toml or change user= in config/settings.toml."
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    chrome = data.pop("chrome", {}) or {}
    if not isinstance(chrome, dict):
        raise ValueError(f"users.{key}: [chrome] must be a table of portal = profile_dir")
    unknown = set(data) - _USER_FIELDS
    if unknown:
        raise ValueError(f"Unknown key(s) in {path.name}: {sorted(unknown)}")
    return User(
        key=key,
        name=str(data.get("name") or key),
        email=str(data.get("email") or ""),
        phone=str(data.get("phone") or ""),
        location=str(data.get("location") or ""),
        resume=str(data.get("resume") or ""),
        applications=str(data.get("applications") or ""),
        chrome={str(portal): str(folder) for portal, folder in chrome.items()},
    )
