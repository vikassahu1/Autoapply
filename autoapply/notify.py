"""Desktop alerts when the run needs you (CAPTCHA, login, etc.)."""
from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)


def notify_attention(reason: str) -> None:
    reason_lower = (reason or "").lower()
    if "captcha" in reason_lower:
        title = "Auto-apply: CAPTCHA"
        body = (
            "Solve the CAPTCHA in the Chrome window, then click Resume on the dashboard. "
            "The run is paused, not stopped."
        )
    elif "login" in reason_lower:
        title = "Auto-apply: sign in required"
        body = "Sign in to Indeed in the Chrome window, then click Resume on the dashboard."
    else:
        title = "Auto-apply needs you"
        body = reason[:240] if reason else "Check the dashboard and browser, then Resume."

    if sys.platform == "darwin":
        safe = lambda text: (text or "").replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe(body)}" with title "{safe(title)}" sound name "Glass"'
        try:
            subprocess.run(["osascript", "-e", script], check=False, timeout=5)
        except Exception:
            log.debug("Could not show macOS notification", exc_info=True)
    else:
        log.warning("ATTENTION: %s — %s", title, body)
