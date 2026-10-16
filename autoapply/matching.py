"""Loose text matching shared by portal adapters.

A job pane often adds punctuation, a seniority word, or a company suffix.
The card and the pane still refer to the same role when most of the words overlap.
"""
from __future__ import annotations

import re

_STOP = frozenset("a an the and or of for to in on with at by from".split())


def words(text: str) -> set[str]:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return {word for word in cleaned.split() if len(word) > 1 and word not in _STOP}


def titles_close(wanted: str, got: str) -> bool:
    """True when `got` is the same role as `wanted`, not necessarily the same string."""
    left, right = words(wanted), words(got)
    if not left or not right:
        return False
    if left <= right or right <= left:
        return True
    return len(left & right) / len(left) >= 0.6
