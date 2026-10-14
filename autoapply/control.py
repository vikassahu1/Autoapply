"""Run state shared between the automation thread and the UI.

The engine calls `checkpoint()` at safe points (between steps, never mid-click).
Pause therefore takes effect at the next checkpoint, and stop raises StopRequested there.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict
from enum import Enum

from .models import JobRecord, Outcome


class RunState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


class StopRequested(Exception):
    """Raised at a checkpoint after the user pressed Stop."""


def _empty_counts() -> dict[str, int]:
    return {"attempted": 0, "submitted": 0, "skipped": 0, "needs_manual": 0}


class Controller:
    def __init__(self, on_pause=None) -> None:
        self._on_pause = on_pause
        self._cond = threading.Condition(threading.RLock())
        self._state = RunState.STOPPED
        self._stop_requested = False
        self._pause_reason = ""
        self._current = ""
        self._error = ""
        self._counts = _empty_counts()
        self._results: deque[dict] = deque(maxlen=200)

    @property
    def state(self) -> RunState:
        with self._cond:
            return self._state

    @property
    def pause_reason(self) -> str:
        with self._cond:
            return self._pause_reason

    # --- lifecycle -------------------------------------------------------
    def begin(self) -> None:
        """Idempotent: a no-op if a run is already active."""
        with self._cond:
            if self._state is not RunState.STOPPED:
                return
            self._state = RunState.RUNNING
            self._stop_requested = False
            self._pause_reason = ""
            self._current = ""
            self._error = ""
            self._counts = _empty_counts()
            self._results.clear()

    def finish(self, error: str = "") -> None:
        with self._cond:
            self._state = RunState.STOPPED
            self._pause_reason = ""
            self._current = ""
            self._error = error
            self._cond.notify_all()

    # --- user / engine controls -------------------------------------------
    def pause(self, reason: str) -> None:
        with self._cond:
            if self._state in (RunState.RUNNING, RunState.PAUSED):
                self._state = RunState.PAUSED
                self._pause_reason = reason
                self._cond.notify_all()
        if self._on_pause:
            try:
                self._on_pause(reason)
            except Exception:
                pass

    def resume(self) -> None:
        with self._cond:
            if self._state is RunState.PAUSED:
                self._state = RunState.RUNNING
                self._pause_reason = ""
                self._cond.notify_all()

    def request_stop(self) -> None:
        with self._cond:
            if self._state is not RunState.STOPPED:
                self._stop_requested = True
                self._cond.notify_all()

    def checkpoint(self) -> None:
        """Block while paused; raise StopRequested if stop was requested."""
        with self._cond:
            while True:
                if self._stop_requested:
                    raise StopRequested()
                if self._state is not RunState.PAUSED:
                    return
                self._cond.wait(timeout=0.5)

    # --- reporting --------------------------------------------------------
    def set_current(self, text: str) -> None:
        with self._cond:
            self._current = text

    def add_result(self, record: JobRecord) -> None:
        with self._cond:
            self._counts["attempted"] += 1
            key = {
                Outcome.SUBMITTED: "submitted",
                Outcome.SKIPPED: "skipped",
                Outcome.NEEDS_MANUAL: "needs_manual",
            }[record.outcome]
            self._counts[key] += 1
            entry = asdict(record)
            entry["outcome"] = record.outcome.value
            self._results.appendleft(entry)

    def snapshot(self) -> dict:
        with self._cond:
            return {
                "state": self._state.value,
                "pause_reason": self._pause_reason,
                "current_job": self._current,
                "error": self._error,
                "counts": dict(self._counts),
                "results": list(self._results),
            }
