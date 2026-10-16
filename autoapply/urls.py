"""Small URL helpers shared by portal adapters."""
from __future__ import annotations

from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit


def set_query(url: str, **params: object) -> str:
    """Return `url` with the given query parameters set or replaced."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    for key, value in params.items():
        query[key] = str(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def same_results_page(left: str, right: str) -> bool:
    """True when both URLs are the same results page, ignoring tracking params."""
    a, b = urlsplit(left or ""), urlsplit(right or "")
    if (a.scheme, a.netloc, a.path.rstrip("/")) != (b.scheme, b.netloc, b.path.rstrip("/")):
        return False
    start_a = parse_qs(a.query).get("start", ["0"])[0] or "0"
    start_b = parse_qs(b.query).get("start", ["0"])[0] or "0"
    return start_a == start_b
