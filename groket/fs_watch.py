"""Debounced filesystem watches for live session / timeline refresh.

Uses :mod:`watchdog` (inotify on Linux). Groket runs on the **host** against
``runs/traces`` bind-mounts, so container writes normally produce events here.

Not a poller: the callback runs only when the OS reports create/modify/move/delete
under the watched tree (coalesced by *debounce_s*).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import cast

logger = logging.getLogger(__name__)

# Names that matter for session list / timeline (others ignored to cut noise).
_TRACE_NAME_HINTS = (
    "updates.jsonl",
    "events.jsonl",
    "summary.json",
    "signals.json",
    "chat_history.jsonl",
    "groket-interrupted.json",
    "status.json",
    "command",
    # Operator notes (TUI may write disk-local; clients need a change signal).
    "operator_notes.toml",
)
_NOISE_DIR_NAMES = frozenset({"workspace", "images", "compaction"})
# Read-only open/close must not count as a catalog change (that retriggers apply).
_IGNORE_EVENT_TYPES = frozenset({"opened", "closed_no_write"})


class TraceTreeWatch:
    """Watch *root* recursively; invoke *on_change* (debounced) on relevant events.

    *on_change* is called from the watchdog observer thread — callers must
    marshal to the UI thread themselves (``call_from_thread`` / ``post_message``).

    When *on_paths* is set, it receives the coalesced absolute paths that
    triggered the fire (best-effort; may be empty if only dir events).
    """

    def __init__(
        self,
        root: Path,
        on_change: Callable[[], None],
        *,
        debounce_s: float = 0.4,
        on_paths: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._root = Path(root)
        self._on_change = on_change
        self._on_paths = on_paths
        self._debounce_s = max(0.05, float(debounce_s))
        self._observer: object | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._pending = False
        self._pending_paths: set[str] = set()

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def path_relevant(path: str) -> bool:
        """True when *path* can change the session list or timeline."""
        p = Path(path)
        if any(part.casefold() in _NOISE_DIR_NAMES for part in p.parts):
            return False
        name = p.name
        if name in _TRACE_NAME_HINTS:
            return True
        if name.startswith("019") or name.startswith("groket-"):
            return True
        if ".groket-turn" in path or "prompt_history" in name:
            return True
        return False

    @staticmethod
    def noise_name(name: str | bytes) -> bool:
        """True for a workspace / images / compaction directory name."""
        text = os.fsdecode(name) if isinstance(name, (bytes, bytearray)) else name
        return text.casefold() in _NOISE_DIR_NAMES

    @staticmethod
    def noise_path(path: str | bytes) -> bool:
        """True when any path part is a noise directory."""
        text = os.fsdecode(path) if isinstance(path, (bytes, bytearray)) else path
        return any(part.casefold() in _NOISE_DIR_NAMES for part in Path(text).parts)

    @classmethod
    def walk_without_noise(
        cls,
        top: str | bytes,
        topdown: bool = True,
        onerror: Callable[[OSError], object] | None = None,
        followlinks: bool = False,
    ) -> Iterator[tuple[bytes, list[bytes], list[bytes]]]:
        """``os.walk`` that does not enter workspace / images / compaction."""
        raw = top if isinstance(top, bytes) else os.fsencode(top)
        if cls.noise_path(raw):
            return
        for root, dirnames, filenames in os.walk(
            raw, topdown=topdown, onerror=onerror, followlinks=followlinks
        ):
            dirnames[:] = [name for name in dirnames if not cls.noise_name(name)]
            yield root, dirnames, filenames

    _noise_skip = False

    @classmethod
    def install_noise_skip(cls) -> None:
        """Prune watchdog inotify so noise trees never get a watch descriptor.

        watchdog's Linux walker uses ``os.walk`` on its own ``os`` binding.
        Replacing that module name (not process-global ``os.walk``) is the
        one hook that stops it descending ``workspace/``.
        """
        try:
            from watchdog.observers import inotify_c
        except ImportError:
            return
        if cls._noise_skip:
            return

        class _InotifyOs:
            path = os.path
            sep = os.sep
            pipe = staticmethod(os.pipe)
            write = staticmethod(os.write)
            read = staticmethod(os.read)
            close = staticmethod(os.close)
            strerror = staticmethod(os.strerror)
            fsdecode = staticmethod(os.fsdecode)
            walk = staticmethod(cls.walk_without_noise)

        orig_add_watch = inotify_c.Inotify._add_watch

        def _add_watch(self: object, path: bytes | str, mask: int) -> int:
            if cls.noise_path(path):
                return -1
            raw = path if isinstance(path, bytes) else os.fsencode(path)
            return int(orig_add_watch(cast(inotify_c.Inotify, self), raw, mask))

        inotify_c.os = cast(ModuleType, _InotifyOs())
        setattr(inotify_c.Inotify, "_add_watch", _add_watch)
        cls._noise_skip = True

    def start(self) -> bool:
        """Start watching. Returns False if *root* is missing or observer fails."""
        if not self._root.is_dir():
            return False
        try:
            from watchdog.events import FileSystemEvent, FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logger.warning("watchdog not installed; live FS watch disabled")
            return False

        TraceTreeWatch.install_noise_skip()
        watch = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event: FileSystemEvent) -> None:
                if event.event_type in _IGNORE_EVENT_TYPES:
                    return
                if event.is_directory and event.event_type == "modified":
                    return
                src = str(event.src_path or "")
                dest = str(event.dest_path or "")
                if not (TraceTreeWatch.path_relevant(src) or TraceTreeWatch.path_relevant(dest)):
                    return
                paths: list[str] = []
                if src:
                    paths.append(src)
                if dest:
                    paths.append(dest)
                watch._schedule_fire(paths)

        try:
            obs = Observer()
            obs.schedule(_Handler(), str(self._root), recursive=True)
            obs.daemon = True
            obs.start()
            self._observer = obs
            logger.debug("FS watch started on %s", self._root)
            return True
        except Exception:
            logger.warning("FS watch failed for %s", self._root, exc_info=True)
            self._observer = None
            return False

    def _schedule_fire(self, paths: list[str] | None = None) -> None:
        with self._lock:
            self._pending = True
            if paths:
                self._pending_paths.update(paths)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_s, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            if not self._pending:
                return
            self._pending = False
            self._timer = None
            paths = sorted(self._pending_paths)
            self._pending_paths.clear()
        try:
            self._on_change()
        except Exception:
            logger.debug("FS watch callback failed", exc_info=True)
        if self._on_paths is not None:
            try:
                self._on_paths(paths)
            except Exception:
                logger.debug("FS watch path callback failed", exc_info=True)

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._pending = False
        obs = self._observer
        self._observer = None
        if obs is not None:
            try:
                stop = getattr(obs, "stop", None)
                join = getattr(obs, "join", None)
                if callable(stop):
                    stop()
                if callable(join):
                    join(timeout=2.0)
            except Exception:
                logger.debug("FS watch stop failed", exc_info=True)
