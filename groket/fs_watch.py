"""Non-recursive plane watch for live session / timeline refresh.

Uses :mod:`watchfiles` on membership directories and the four session-plane
files. ``workspace/`` is never subscribed.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from .session.watch import (
    PLANE_FILE_NAMES,
    plane_event_path,
    plane_file_paths,
    session_dirs_under,
    watch_target_paths,
)

logger = logging.getLogger(__name__)


class TraceTreeWatch:
    """Watch *root* (or one *session_dir*) without descending ``workspace/``.

    *on_change* is called from the watch thread — callers must marshal to
    the UI thread themselves (``call_from_thread`` / ``post_message``).

    When *on_paths* is set, it receives the changed absolute paths.
    """

    def __init__(
        self,
        root: Path,
        on_change: Callable[[], None],
        *,
        debounce_s: float = 0.05,
        on_paths: Callable[[list[str]], None] | None = None,
        session_dir: Path | None = None,
    ) -> None:
        self._root = Path(root)
        self._session_dir = Path(session_dir) if session_dir is not None else None
        self._on_change = on_change
        self._on_paths = on_paths
        self._debounce_s = max(0.0, float(debounce_s))
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._paths: list[Path] = []

    @property
    def root(self) -> Path:
        return self._root

    def subscribed_paths(self) -> list[Path]:
        """Paths currently handed to watchfiles (no ``workspace/``)."""
        return list(self._paths)

    @staticmethod
    def path_relevant(path: str) -> bool:
        """True when *path* is a plane file outside ``workspace/``."""
        return path_relevant(path)

    def _collect_paths(self) -> list[Path]:
        if self._session_dir is not None:
            sessions = [self._session_dir]
            dirs = watch_target_paths([self._session_dir], sessions)
        else:
            sessions = session_dirs_under([self._root])
            dirs = watch_target_paths([self._root], sessions)
        files = [p for session in sessions for p in plane_file_paths(session) if p.is_file()]
        return dirs + files

    def start(self) -> bool:
        """Start watching. Returns False if *root* is missing or watch fails."""
        if not self._root.is_dir() and self._session_dir is None:
            return False
        self._paths = [p for p in self._collect_paths() if p.exists() or p.is_dir()]
        if not self._paths:
            if self._root.is_dir():
                self._paths = [self._root]
            else:
                return False
        self._stop.clear()
        self._ready.clear()
        thread = threading.Thread(target=self._run, name="groket-plane-watch", daemon=True)
        self._thread = thread
        thread.start()
        return self._ready.wait(2.0)

    def _run(self) -> None:
        try:
            from watchfiles import watch
        except ImportError:
            logger.warning("watchfiles not installed; live FS watch disabled")
            self._ready.set()
            return
        debounce_ms = int(self._debounce_s * 1000)
        while not self._stop.is_set():
            paths = [p for p in self._collect_paths() if p.exists()]
            if not paths:
                self._ready.set()
                if self._stop.wait(0.25):
                    return
                continue
            self._paths = paths
            try:
                armed = False
                for changes in watch(
                    *paths,
                    recursive=False,
                    debounce=debounce_ms,
                    stop_event=self._stop,
                    yield_on_timeout=True,
                    rust_timeout=200,
                    step=50,
                ):
                    if not armed:
                        self._ready.set()
                        armed = True
                    if self._stop.is_set():
                        return
                    if not changes:
                        continue
                    fired = [
                        path
                        for _kind, path in changes
                        if plane_event_path(Path(path)) and "workspace" not in Path(path).parts
                    ]
                    if fired:
                        self._emit(fired)
                    nxt = [p for p in self._collect_paths() if p.exists()]
                    if {str(p) for p in nxt} != {str(p) for p in paths}:
                        # New or gone session: drop this watch() and resubscribe.
                        break
            except Exception:
                logger.debug("FS watch iteration failed", exc_info=True)
                if self._stop.wait(0.25):
                    return

    def _emit(self, paths: list[str]) -> None:
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
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)


def path_relevant(path: str) -> bool:
    """True when *path* is a plane file (not under ``workspace/``)."""
    p = Path(path)
    if any(part.casefold() == "workspace" for part in p.parts):
        return False
    return p.name in PLANE_FILE_NAMES
