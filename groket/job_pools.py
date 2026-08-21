"""Fixed-size background pools for analysis and live session refresh.

Serial-by-default (``max_workers=1``) so heavy work does not stampede CPUs.
Both pools share an :class:`ActivityLog` ring buffer for the debug / jobs UI.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


@dataclass(frozen=True)
class ActivityEntry:
    """One logged heavy action."""

    ts: float
    kind: str  # analysis | refresh | system
    message: str


class ActivityLog:
    """Thread-safe ring buffer of recent heavy work (for Jobs / debug pane)."""

    def __init__(self, maxlen: int = 200) -> None:
        self._entries: deque[ActivityEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0

    def log(self, kind: str, message: str) -> None:
        entry = ActivityEntry(ts=time.time(), kind=kind, message=message)
        with self._lock:
            self._entries.append(entry)
            self._seq += 1
        logger.info("[%s] %s", kind, message)

    def clear(self) -> None:
        """Drop all entries (Jobs Clear); bump *seq* so viewers repaint."""
        with self._lock:
            self._entries.clear()
            self._seq += 1

    def snapshot(self, limit: int = 100) -> list[ActivityEntry]:
        with self._lock:
            items = list(self._entries)
        if limit > 0:
            return items[-limit:]
        return items

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def spinner_frame(self) -> str:
        """Rotate a braille spinner based on wall clock."""
        return _SPINNER_FRAMES[int(time.time() * 8) % len(_SPINNER_FRAMES)]


class JobPool:
    """Named :class:`ThreadPoolExecutor` with activity logging."""

    def __init__(self, name: str, max_workers: int, log: ActivityLog) -> None:
        workers = max(1, int(max_workers))
        self.name = name
        self.max_workers = workers
        self._log = log
        self._executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f"groket-{name}",
        )
        self._inflight = 0
        self._lock = threading.Lock()

    @property
    def inflight(self) -> int:
        with self._lock:
            return self._inflight

    def submit(self, label: str, fn: Callable[[], T]) -> Future[T]:
        """Run *fn* on the pool; log start/end."""

        def _wrapped() -> T:
            with self._lock:
                self._inflight += 1
            self._log.log(self.name, f"start: {label}")
            try:
                return fn()
            except Exception as exc:
                self._log.log(self.name, f"error: {label}: {exc}")
                raise
            finally:
                with self._lock:
                    self._inflight = max(0, self._inflight - 1)
                self._log.log(self.name, f"done: {label}")

        return self._executor.submit(_wrapped)

    def shutdown(self, *, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=not wait)


# Process-wide pools (reset on config reload via :func:`configure_job_pools`).
_activity_log = ActivityLog()
_live_refresh_pool = JobPool("refresh", 1, _activity_log)
_pools_lock = threading.Lock()


def get_activity_log() -> ActivityLog:
    return _activity_log


def get_live_refresh_pool() -> JobPool:
    return _live_refresh_pool


def configure_job_pools(
    *,
    live_refresh_workers: int = 1,
) -> None:
    """Replace the live-refresh pool (call when prefs change)."""
    global _live_refresh_pool
    with _pools_lock:
        old_r = _live_refresh_pool
        _live_refresh_pool = JobPool("refresh", live_refresh_workers, _activity_log)
        _activity_log.log(
            "system",
            f"pools reconfigured refresh={max(1, live_refresh_workers)}",
        )
        old_r.shutdown(wait=False)


def refresh_inflight() -> int:
    return _live_refresh_pool.inflight
