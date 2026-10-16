"""Portal registry. Add new portals here."""
from __future__ import annotations

from ..config import PROJECT_ROOT, load_portal_config
from .base import PortalAdapter
from .indeed import IndeedAdapter
from .linkedin import LinkedInAdapter

REGISTRY: dict[str, type[PortalAdapter]] = {
    "indeed": IndeedAdapter,
    "linkedin": LinkedInAdapter,
}


def build_adapter(name: str, root=PROJECT_ROOT) -> PortalAdapter:
    if name not in REGISTRY:
        raise ValueError(f"Unknown portal {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](load_portal_config(name, root))
