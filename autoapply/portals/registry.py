"""Portal registry. Add a portal by subclassing PortalAdapter and calling register()."""
from __future__ import annotations

from ..config import PROJECT_ROOT, load_portal_config
from .base import PortalAdapter
from .indeed import IndeedAdapter
from .linkedin import LinkedInAdapter

REGISTRY: dict[str, type[PortalAdapter]] = {}


def register(name: str, adapter_cls: type[PortalAdapter]) -> None:
    REGISTRY[name] = adapter_cls


def build_adapter(name: str, root=PROJECT_ROOT) -> PortalAdapter:
    if name not in REGISTRY:
        raise ValueError(f"Unknown portal {name!r}. Available: {sorted(REGISTRY)}")
    return REGISTRY[name](load_portal_config(name, root))


register("indeed", IndeedAdapter)
register("linkedin", LinkedInAdapter)
