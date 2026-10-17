"""The filter used by Indeed and LinkedIn today.

Swap this class (or pass another JobFilter) if you change matching logic.
Do not copy regexes into the adapters.
"""
from __future__ import annotations

from .filters import ExperienceFilters, TitleFilters


class DefaultJobFilter:
    def __init__(self, titles: TitleFilters, experience: ExperienceFilters):
        self.titles = titles
        self.experience = experience

    @classmethod
    def from_config(cls, filters: dict | None) -> DefaultJobFilter:
        data = filters or {}
        return cls(TitleFilters.from_config(data), ExperienceFilters.from_config(data))

    def skip_title(self, title: str) -> str | None:
        return self.titles.skip_reason(title)

    def skip_experience(self, text: str) -> str | None:
        return self.experience.skip_reason(text)
