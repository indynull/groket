"""Process-wide per-session inflight locks for heavy background work.

One lock table keyed by ``(kind, resolved session_dir)`` so live
refresh single-flights per session across UI entry points. A coalesced
rerun bit records that another pass was requested while the lock was held;
:func:`end` reports that so the owner can enqueue exactly one follow-up.
"""

from __future__ import annotations

import threading
from pathlib import Path

KIND_REFRESH = "refresh"

_lock = threading.Lock()
_inflight: set[tuple[str, str]] = set()
_rerun: set[tuple[str, str]] = set()


def session_dir_key(session_dir: Path | str) -> str:
    """Stable identity for a session directory."""
    p = Path(session_dir)
    try:
        return str(p.expanduser().resolve())
    except OSError:
        return str(p.expanduser())


def _entry(kind: str, session_dir: Path | str) -> tuple[str, str]:
    return (kind, session_dir_key(session_dir))


def try_begin(kind: str, session_dir: Path | str) -> bool:
    """Mark *session_dir* inflight for *kind*. False if already in the pipeline."""
    ent = _entry(kind, session_dir)
    with _lock:
        if ent in _inflight:
            return False
        _inflight.add(ent)
        _rerun.discard(ent)
        return True


def request_rerun(kind: str, session_dir: Path | str) -> None:
    """If *kind* is inflight for *session_dir*, request one follow-up run.

    No-op when nothing is inflight (caller should ``try_begin`` instead).
    """
    ent = _entry(kind, session_dir)
    with _lock:
        if ent in _inflight:
            _rerun.add(ent)


def end(kind: str, session_dir: Path | str) -> bool:
    """Release the lock. Return True when a coalesced rerun was requested."""
    ent = _entry(kind, session_dir)
    with _lock:
        _inflight.discard(ent)
        again = ent in _rerun
        _rerun.discard(ent)
        return again


def is_inflight(kind: str, session_dir: Path | str) -> bool:
    """True when *kind* work is queued or running for *session_dir*."""
    ent = _entry(kind, session_dir)
    with _lock:
        return ent in _inflight


def inflight_count(kind: str) -> int:
    """Number of sessions with *kind* inflight."""
    with _lock:
        return sum(1 for k, _ in _inflight if k == kind)


def clear(kind: str | None = None) -> None:
    """Drop inflight and rerun bits (tests / process teardown).

    :param kind: When set, clear only that kind; otherwise clear all kinds.
    """
    global _inflight, _rerun
    with _lock:
        if kind is None:
            _inflight = set()
            _rerun = set()
            return
        _inflight = {ent for ent in _inflight if ent[0] != kind}
        _rerun = {ent for ent in _rerun if ent[0] != kind}
