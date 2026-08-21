"""Non-recursive catalog watch: membership dirs and four plane files.

``workspace/`` is never subscribed. ``groket serve`` and the TUI share this path set.
"""

from __future__ import annotations

import os
from pathlib import Path

from .sources import (
    is_encoded_cwd_name,
    is_host_skip_dir_name,
    list_host_session_dirs,
)

PLANE_FILE_NAMES: tuple[str, ...] = (
    "summary.json",
    "signals.json",
    "updates.jsonl",
    "operator_notes.toml",
)


def plane_file_paths(session_dir: Path) -> list[Path]:
    """The four session-plane files under *session_dir*."""
    root = Path(session_dir)
    return [root / name for name in PLANE_FILE_NAMES]


def membership_watch_dirs(roots: list[Path]) -> list[Path]:
    """Directories whose direct children appearing or vanishing change membership."""
    out: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        if not root.is_dir():
            continue
        key = str(root)
        if key not in seen:
            seen.add(key)
            out.append(root)
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            if is_host_skip_dir_name(child.name):
                continue
            if is_encoded_cwd_name(child.name):
                bucket = str(child)
                if bucket not in seen:
                    seen.add(bucket)
                    out.append(child)
    return out


def session_dirs_under(roots: list[Path]) -> list[Path]:
    """Listed session directories under catalog *roots* (no workspace descent)."""
    found: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        root = Path(raw).expanduser()
        if not root.is_dir():
            continue
        for session in list_host_session_dirs(root):
            key = str(session)
            if key in seen:
                continue
            seen.add(key)
            found.append(session)
    return found


def _no_workspace(path: Path) -> bool:
    return all(part.casefold() != "workspace" for part in path.parts)


def watch_target_paths(roots: list[Path], session_dirs: list[Path]) -> list[Path]:
    """Directories passed to watchfiles (non-recursive). Never ``workspace/``."""
    out: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        if not _no_workspace(path):
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    for path in membership_watch_dirs(roots):
        _add(path)
        try:
            children = list(path.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.is_dir() and not is_host_skip_dir_name(child.name):
                _add(child)
    for session in session_dirs:
        _add(Path(session))
    return out


def catalog_subscribe_paths(roots: list[Path], session_dirs: list[Path]) -> list[Path]:
    """Membership dirs plus four plane files. Never includes ``workspace/``."""
    out = watch_target_paths(roots, session_dirs)
    seen = {str(p) for p in out}
    for session in session_dirs:
        sd = Path(session)
        if not _no_workspace(sd):
            continue
        for plane in plane_file_paths(sd):
            key = str(plane)
            if key in seen:
                continue
            seen.add(key)
            out.append(plane)
    return out


def plane_event_path(path: Path) -> bool:
    """True when *path* is a plane file or a membership directory event."""
    if not _no_workspace(path):
        return False
    if path.name.casefold() == "workspace":
        return False
    if path.name in PLANE_FILE_NAMES:
        return True
    return path.is_dir() or not path.suffix


class JournalTail:
    """Byte offset into one ``updates.jsonl``. Second consume does not seek 0."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.inode: int | None = None
        self.offset: int = 0

    def consume(self) -> bytes:
        """Return bytes after the last offset. Updates :attr:`offset`."""
        try:
            fd = os.open(self.path, os.O_RDONLY)
        except OSError:
            return b""
        try:
            st = os.fstat(fd)
            inode = int(st.st_ino)
            if self.inode is not None and inode != self.inode:
                self.offset = 0
            self.inode = inode
            if self.offset > st.st_size:
                self.offset = 0
            os.lseek(fd, self.offset, os.SEEK_SET)
            data = os.read(fd, max(0, st.st_size - self.offset))
            self.offset = int(os.lseek(fd, 0, os.SEEK_CUR))
            return data
        finally:
            os.close(fd)
