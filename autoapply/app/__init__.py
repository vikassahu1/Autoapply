"""Dashboard wiring: one AutoApplyApp per portal, one PortalDesk for both."""
from .desk import PORTALS, PortalDesk
from .runner import AutoApplyApp

__all__ = ["AutoApplyApp", "PORTALS", "PortalDesk"]
