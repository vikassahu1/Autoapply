"""Experience lines from a job posting. Patterns come from [filters] experience_exclude."""
from __future__ import annotations

import re
from collections.abc import Sequence

from ..core.models import Reason


def experience_skip_reason(text: str, patterns: Sequence[re.Pattern]) -> str | None:
    """None when the description does not ask for more than about a year."""
    if any(pattern.search(text or "") for pattern in patterns):
        return Reason.EXPERIENCE
    return None
