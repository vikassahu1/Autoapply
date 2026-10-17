"""Compare and filter job titles. Patterns come from each portal's [filters] table."""
from __future__ import annotations

import re
from collections.abc import Sequence

from ..core.models import Reason

_STOP = frozenset("a an the and or of for to in on with at by from".split())


def words(text: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return {word for word in cleaned.split() if len(word) > 1 and word not in _STOP}


def title_skip_reason(
    title: str, include: Sequence[re.Pattern], exclude: Sequence[re.Pattern]
) -> str | None:
    """None when the title is a role we are willing to open. Checked before any click."""
    text = re.sub(r"\s+", " ", title or "").strip().lower()
    if not text:
        return Reason.ROLE
    if any(pattern.search(text) for pattern in exclude):
        return Reason.ROLE
    if include and not any(pattern.search(text) for pattern in include):
        return Reason.ROLE
    return None


def titles_close(wanted: str, got: str) -> bool:
    """True when `got` is the same role as `wanted`, not necessarily the same string."""
    left, right = words(wanted), words(got)
    if not left or not right:
        return False
    if left <= right or right <= left:
        return True
    return len(left & right) / len(left) >= 0.6
