"""How a job is kept or skipped.

Adapters only call these two methods. To change matching later, edit
titles.py / experience.py, or replace DefaultJobFilter and keep this shape.
Rules themselves live in config/portals/<portal>.toml [filters].
"""
from __future__ import annotations

from typing import Protocol


class JobFilter(Protocol):
    def skip_title(self, title: str) -> str | None:
        """None = try this job. A reason string = skip before clicking."""
        ...

    def skip_experience(self, text: str) -> str | None:
        """None = experience is fine. A reason string = skip after opening details."""
        ...
