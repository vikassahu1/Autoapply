"""Portal adapters and the registry that wires them into the rest of the app."""
from .base import PortalAdapter
from .registry import REGISTRY, build_adapter, register

__all__ = ["PortalAdapter", "REGISTRY", "build_adapter", "register"]
