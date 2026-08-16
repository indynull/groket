"""Mtime-gated catalog export. Rebuild only when a source file is newer.

List rows stay names and mtimes. This is the uLogMe export_events.py shape:
write JSON when the source is newer than the last export, otherwise reuse.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..parser import session_timeline_mtime
from .classify import classify_title
from .sources import collect_host_session_dirs, host_grok_sessions_root


def export_is_stale(source_mtime: float, dest: Path) -> bool:
    """True when *dest* is missing or older than *source_mtime*."""
    try:
        return (not dest.is_file()) or dest.stat().st_mtime < source_mtime
    except OSError:
        return True


def write_host_catalog_export(
    dest: Path,
    *,
    host_root: Path | None = None,
) -> Path:
    """Write a names+mtimes JSON for host sessions. Does not start serve."""
    root = Path(host_root).expanduser() if host_root is not None else host_grok_sessions_root()
    rows: list[dict[str, object]] = []
    newest = 0.0
    for sd in collect_host_session_dirs(root):
        try:
            mtime = float(session_timeline_mtime(sd))
        except OSError:
            mtime = 0.0
        newest = max(newest, mtime)
        title = ""
        summary = sd / "summary.json"
        if summary.is_file():
            try:
                data = json.loads(summary.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    title = str(data.get("generated_title") or data.get("title") or "")
            except (OSError, json.JSONDecodeError):
                title = ""
        rows.append(
            {
                "sessionId": sd.name,
                "path": str(sd),
                "mtime": mtime,
                "title": title,
                "label": classify_title(title),
            }
        )
    dest = Path(dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not export_is_stale(newest, dest):
        return dest
    dest.write_text(json.dumps({"root": str(root), "sessions": rows}, indent=2) + "\n", encoding="utf-8")
    return dest
