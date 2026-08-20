"""Grok Build on-disk session adapter.

Public harness contract for harness id ``grok``. Implementation lives in
:mod:`groket.parser` and :mod:`groket.session.sources`; this module wraps
those APIs. :class:`~groket.models.SessionMeta` has no ``harness`` field
and no extras bag; catalog row ``harness`` is not set here.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from ..fs_watch import TRACE_FILE_HINTS
from ..models import SessionMeta, TraceEvent
from ..parser import _looks_like_session_dir, find_sessions, load_session_meta_list
from ..parser import parse_timeline as parse_grok_timeline
from ..session.sources import (
    ORIGIN_HOST,
    ORIGIN_WORK,
    collect_host_session_dirs,
    is_host_grok_sessions_root,
    is_under_host_grok_sessions,
)

GROK_HARNESS_ID = "grok"


def discover(roots: Sequence[Path | str]) -> list[Path]:
    """List unique Grok session directories under *roots*.

    Host ``~/.grok/sessions`` uses
    :func:`~groket.session.sources.collect_host_session_dirs`. Every other
    root uses :func:`groket.parser.find_sessions`. Duplicate resolved paths
    are dropped (first-seen wins).

    :param roots: Trees to scan.
    :returns: Session directories in first-seen order.
    """
    found: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        if is_host_grok_sessions_root(root):
            dirs = collect_host_session_dirs(root)
        else:
            dirs = find_sessions(root)
        for sd in dirs:
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            if key in seen:
                continue
            seen.add(key)
            found.append(sd)
    return found


def looks_like(ref: Path | str) -> bool:
    """True when *ref* is a Grok session directory.

    Wraps :func:`groket.parser._looks_like_session_dir`.

    :param ref: Session directory path.
    :returns: True when the directory has Grok session artifacts.
    """
    path = Path(ref).expanduser()
    if not path.is_dir():
        return False
    names: set[str] = set()
    try:
        with os.scandir(path) as it:
            for ent in it:
                if ent.is_file(follow_symlinks=False):
                    names.add(ent.name)
    except OSError:
        return False
    return _looks_like_session_dir(path, names)


def load_meta(ref: Path | str) -> SessionMeta:
    """Load list-grade metadata for a Grok session directory.

    Wraps :func:`groket.parser.load_session_meta_list`. Sets ``origin`` to
    ``host`` when *ref* lives under the host Grok sessions tree, else
    ``work``.

    :param ref: Session directory path.
    :returns: Populated :class:`~groket.models.SessionMeta`.
    """
    path = Path(ref).expanduser()
    origin = ORIGIN_HOST if is_under_host_grok_sessions(path) else ORIGIN_WORK
    return load_session_meta_list(path, origin=origin)


def parse_timeline(ref: Path | str) -> list[TraceEvent]:
    """Parse a Grok session directory into a linear timeline.

    Wraps :func:`groket.parser.parse_timeline`.

    :param ref: Session directory path.
    :returns: Coalesced :class:`~groket.models.TraceEvent` rows.
    """
    return parse_grok_timeline(Path(ref).expanduser())


def watch_hints() -> tuple[str, ...]:
    """Filenames that should trigger a live reload for Grok sessions.

    Same names as :data:`groket.fs_watch.TRACE_FILE_HINTS`.

    :returns: Basename hints (``updates.jsonl``, ``events.jsonl``, …).
    """
    return TRACE_FILE_HINTS


__all__ = [
    "GROK_HARNESS_ID",
    "discover",
    "load_meta",
    "looks_like",
    "parse_timeline",
    "watch_hints",
]
