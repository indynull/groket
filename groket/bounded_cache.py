"""Bounded least-recently-used mapping for process-wide parse caches.

Parse caches are keyed by session path and validated by an mtime/size stamp, so
re-reading one session replaces its entry. Growth comes from the **number of
distinct sessions** a long-lived process touches: a control owner or TUI left
open over a bucket of several hundred sessions pins every timeline it ever
parsed. This mapping keeps the hit rate that matters (live heartbeats re-read
the same few sessions) and drops the cold tail.

Recency updates on read and on write, so the sessions a live refresh keeps
touching stay resident regardless of how many cold sessions scroll past.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Iterator, MutableMapping
from typing import TypeVar

V = TypeVar("V")

#: Floor for any cap, including an operator override, so a live session and the
#: fork parent it merges always fit.
MIN_MAXSIZE = 2


def resolve_maxsize(default: int, env_var: str | None = None) -> int:
    """Cap from *env_var* when it parses as a positive int, else *default*."""
    if env_var:
        raw = (os.environ.get(env_var) or "").strip()
        if raw:
            try:
                parsed = int(raw)
            except ValueError:
                parsed = 0
            if parsed > 0:
                return max(parsed, MIN_MAXSIZE)
    return max(default, MIN_MAXSIZE)


class BoundedCache(MutableMapping[str, V]):
    """Thread-safe LRU mapping that evicts the coldest entry past ``maxsize``.

    Drop-in for the plain ``dict`` caches: ``get`` / ``[]`` / ``in`` / ``len`` /
    iteration / ``clear`` all behave the same, and eviction is the only added
    behaviour. Iteration yields coldest-first and does **not** count as use, so
    walking the cache cannot reorder it.
    """

    __slots__ = ("_data", "_evictions", "_lock", "_maxsize")

    def __init__(self, maxsize: int, *, env_var: str | None = None) -> None:
        self._maxsize = resolve_maxsize(maxsize, env_var)
        self._data: OrderedDict[str, V] = OrderedDict()
        self._lock = threading.Lock()
        self._evictions = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    @property
    def evictions(self) -> int:
        """Entries dropped for capacity since process start (diagnostics)."""
        return self._evictions

    def __getitem__(self, key: str) -> V:
        with self._lock:
            value = self._data[key]
            self._data.move_to_end(key)
            return value

    def __setitem__(self, key: str, value: V) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)
                self._evictions += 1

    def __delitem__(self, key: str) -> None:
        with self._lock:
            del self._data[key]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            return iter(list(self._data))

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
