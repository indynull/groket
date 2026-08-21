"""Named constants used across the application."""

from __future__ import annotations

DEFAULT_DOCKER_IMAGE = "fully-loaded"
DEFAULT_MODEL_ID = "unknown"
# Grok CLI ``--max-turns`` for one agent invocation (tool/plan loop per prompt).
DEFAULT_MAX_TURNS = 50
CONFIG_FILENAME = "config.toml"
META_CACHE_FILENAME = "_meta_cache.json"

INTERRUPTED_MARKER_FILENAME = "groket-interrupted.json"  # on-disk marker

LOG_BUFFER_MAXLEN = 8000
LOG_TAIL_MAXLEN = 4000
MAX_RUN_HISTORY = 20
# Parse-cache caps (:mod:`groket.bounded_cache`). Entries are keyed per session,
# so a long-lived owner over a large bucket pins every session it ever parsed
# unless the cold tail is dropped. Sized by entry weight, heaviest first.
# Finalized timeline plus incremental scan state: two event lists per session.
# One host session can be hundreds of MiB; keep live + fork parent only.
TIMELINE_CACHE_MAXSIZE = 2
TIMELINE_CACHE_MAX_ENV = "GROKET_TIMELINE_CACHE_MAX"
# Rendered turn segments per session.
TURN_VIEW_CACHE_MAXSIZE = 2
# Overview payload per session.
OVERVIEW_CACHE_MAXSIZE = 2
# Marker events per session.
RUNTIME_MARKERS_CACHE_MAXSIZE = 256
# One system prompt string per session.
SYSTEM_PROMPT_CACHE_MAXSIZE = 128
# Scalars only; sized to cover a whole bucket so the session list stays cheap.
LIST_RUNTIME_CACHE_MAXSIZE = 2048
# Activity bar (cheap counters — not a traces poller).
ACTIVITY_BAR_INTERVAL = 5.0
# Spinner poll while *build/extract* busy — never for mere "running".
# 80ms caused continuous layout thrash during every live eval.
ACTIVITY_BAR_BUSY_INTERVAL = 0.5
# Full traces-tree walk only when idle and FS events were sparse (rare).
LIVE_POLL_FULL_WALK_INTERVAL = 60.0
# Min gap between FS-triggered session list scans (debounce beyond FS watch).
LIVE_POLL_ACTIVE_INTERVAL = 3.0
# Timer interval when TraceTreeWatch cannot start (no inotify / missing root).
LIVE_POLL_WATCH_FALLBACK_INTERVAL = 5.0
# Read-only heartbeat while live (signals.json / context meter); no trace writes.
LIVE_POLL_HEARTBEAT_INTERVAL = 60.0
# Browser live timeline: FS-debounced + timer backup. Cadence is deliberately
# moderate — fast enough that new tool rows appear, slow enough not to freeze.
LIVE_BROWSER_SNAPSHOT_INTERVAL = 3.0
LIVE_BROWSER_FS_DEBOUNCE_S = 1.5
# Live table path: only the last N rows checked for structural continuity.
LIVE_TIMELINE_TAIL_CHECK = 32
# Timeline search-as-you-type: apply the last query after this idle gap.
TIMELINE_SEARCH_DEBOUNCE_S = 0.28
# Back-compat aliases (older tests / callers).
LIVE_BROWSER_TIMELINE_MIN_INTERVAL = LIVE_BROWSER_SNAPSHOT_INTERVAL
LIVE_BROWSER_TIMELINE_MIN_INTERVAL_LARGE = 5.0
LIVE_BROWSER_TIMELINE_MIN_INTERVAL_HUGE = 8.0
_UPDATES_LARGE_BYTES = 5 * 1024 * 1024
_UPDATES_HUGE_BYTES = 20 * 1024 * 1024
# Treat updates.jsonl as "still writing" for this many seconds after last mtime.
LIVE_UPDATES_FRESH_SECONDS = 45.0


def live_browser_timeline_min_interval(updates_bytes: int | float = 0) -> float:
    """Min seconds between browser timeline snapshots while live.

    :param updates_bytes: Current size of ``updates.jsonl`` (0 = default gap).
    :returns: Seconds to wait between re-parses.
    """
    size = float(updates_bytes or 0)
    if size >= _UPDATES_HUGE_BYTES:
        return float(LIVE_BROWSER_TIMELINE_MIN_INTERVAL_HUGE)
    if size >= _UPDATES_LARGE_BYTES:
        return float(LIVE_BROWSER_TIMELINE_MIN_INTERVAL_LARGE)
    return float(LIVE_BROWSER_TIMELINE_MIN_INTERVAL)


def normalize_max_turns(value: object, *, default: int = DEFAULT_MAX_TURNS) -> int:
    """Clamp Grok ``--max-turns`` to a positive int (default :data:`DEFAULT_MAX_TURNS`)."""
    if value is None or isinstance(value, bool):
        return int(default)
    if isinstance(value, int):
        n = value
    elif isinstance(value, float):
        n = int(value)
    elif isinstance(value, str):
        try:
            n = int(value.strip())
        except ValueError:
            return int(default)
    else:
        return int(default)
    if n < 1:
        return int(default)
    return n


DIFF_TRUNCATE_THRESHOLD = 120_000
DIFF_TRUNCATE_HEAD = 60_000
DIFF_TRUNCATE_TAIL = 40_000
INCOMPLETE_STALE_SECONDS = 20 * 60
# Host Grok at a prompt writes nothing; 20 minutes left dead sessions on running
# after a dangling turn_started. Eight minutes still covers a long think.
HOST_INCOMPLETE_STALE_SECONDS = 8 * 60
