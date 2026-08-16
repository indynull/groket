"""Stamp-gated host catalog snapshot.

Host ``session/list`` rows are summary.json + signals.json. Rebuild the
JSON snapshot only when a source stamp (path + file mtimes) changes.
Serve and ``groket export-host`` share this file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..models import JsonObject
from ..paths import analysis_cache_dir
from .sources import collect_host_session_dirs, host_grok_sessions_root

_STAMP_FILES = ("summary.json", "signals.json", "updates.jsonl")


def _mtime_ns(path: Path) -> int:
    try:
        st = path.stat()
    except OSError:
        return 0
    return int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))


def host_source_stamp(session_dir: Path) -> tuple[str, int, int, int]:
    """Identity for one host session: path plus summary/signals/updates mtimes."""
    return (
        str(session_dir),
        _mtime_ns(session_dir / "summary.json"),
        _mtime_ns(session_dir / "signals.json"),
        _mtime_ns(session_dir / "updates.jsonl"),
    )


def default_host_catalog_cache(host_root: Path) -> Path:
    """Per-root snapshot under ``~/.groket/cache``."""
    key = hashlib.sha256(str(Path(host_root).expanduser()).encode()).hexdigest()[:16]
    return analysis_cache_dir() / f"host-catalog-{key}.json"


def export_is_stale(stamps: list[tuple[str, int, int, int]], dest: Path) -> bool:
    """True when *dest* is missing or its stored stamps do not match *stamps*."""
    cached = _read_payload(dest)
    if cached is None:
        return True
    raw = cached.get("stamps")
    if not isinstance(raw, list):
        return True
    got: list[tuple[str, int, int, int]] = []
    for item in raw:
        if not (isinstance(item, list) and len(item) == 4):
            return True
        got.append((str(item[0]), int(item[1]), int(item[2]), int(item[3])))
    return got != stamps


def _read_payload(dest: Path) -> dict[str, Any] | None:
    try:
        if not dest.is_file():
            return None
        data = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def load_or_rebuild_host_catalog(
    host_root: Path,
    *,
    dest: Path | None = None,
    build_row: Callable[[Path], JsonObject | None],
) -> list[JsonObject]:
    """Return host catalog rows, rebuilding the snapshot when stamps change."""
    root = Path(host_root).expanduser()
    dest_path = Path(dest).expanduser() if dest is not None else default_host_catalog_cache(root)
    dirs = collect_host_session_dirs(root)
    stamps = [host_source_stamp(sd) for sd in dirs]
    if not export_is_stale(stamps, dest_path):
        cached = _read_payload(dest_path)
        sessions = cached.get("sessions") if cached else None
        if isinstance(sessions, list):
            rows = [row for row in sessions if isinstance(row, dict)]
            return rows
    rows: list[JsonObject] = []
    for sd in dirs:
        row = build_row(sd)
        if row is not None:
            rows.append(row)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_text(
        json.dumps(
            {"root": str(root), "stamps": [list(s) for s in stamps], "sessions": rows},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return rows


def write_host_catalog_export(
    dest: Path,
    *,
    host_root: Path | None = None,
    build_row: Callable[[Path], JsonObject | None] | None = None,
) -> Path:
    """Write the host catalog snapshot to *dest*. Does not start serve."""
    from .catalog import session_catalog_row

    root = Path(host_root).expanduser() if host_root is not None else host_grok_sessions_root()
    builder = build_row or (lambda sd: session_catalog_row(sd, origin="host"))
    load_or_rebuild_host_catalog(root, dest=dest, build_row=builder)
    return Path(dest).expanduser()
