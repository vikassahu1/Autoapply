"""Portal job filters, loaded from config/portals/<name>.toml [filters].

To change which jobs a portal attempts, edit that table. To add a portal, copy
the [filters] block and pass it to TitleFilters.from_config / ExperienceFilters.from_config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .experience import experience_skip_reason
from .titles import title_skip_reason


def compile_patterns(patterns: list[str]) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(pattern, re.I) for pattern in patterns)


@dataclass(frozen=True)
class TitleFilters:
    include: tuple[re.Pattern, ...]
    exclude: tuple[re.Pattern, ...]

    @classmethod
    def from_config(cls, filters: dict | None) -> TitleFilters:
        data = filters or {}
        return cls(
            include=compile_patterns(list(data.get("title_include", []))),
            exclude=compile_patterns(list(data.get("title_exclude", []))),
        )

    def skip_reason(self, title: str) -> str | None:
        return title_skip_reason(title, self.include, self.exclude)


@dataclass(frozen=True)
class ExperienceFilters:
    exclude: tuple[re.Pattern, ...]

    @classmethod
    def from_config(cls, filters: dict | None) -> ExperienceFilters:
        data = filters or {}
        return cls(exclude=compile_patterns(list(data.get("experience_exclude", []))))

    def skip_reason(self, text: str) -> str | None:
        return experience_skip_reason(text, self.exclude)
