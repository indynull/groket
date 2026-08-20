"""Domain session catalog for control plane and headless owners.

Builds wire-shaped catalog rows and resolves session references from disk
without Textual app state. Shared by the control daemon and any client that
needs the same discovery rules as the TUI home list.
"""

from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..models import JsonObject, JsonValue, SessionMeta
from ..parser import load_host_list_meta, load_session_meta_list, session_trace_mtime
from .mtime_export import default_host_catalog_cache, load_or_rebuild_host_catalog
from .sources import (
    ORIGIN_HOST,
    ORIGIN_WORK,
    SessionScanRoot,
    classify_session_origin,
    collect_session_dirs,
    is_under_host_grok_sessions,
    session_scan_roots,
    work_traces_root,
)
from .subagents import (
    drop_subagent_sessions,
    is_subagent_session_dir,
    nested_child_ids,
)

logger = logging.getLogger(__name__)


def _parse_iso_epoch(raw: object) -> float:
    """Parse ISO-ish timestamps to epoch seconds; 0 when missing/invalid."""
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if not s:
        return 0.0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return float(dt.timestamp())
    except (TypeError, ValueError, OSError):
        return 0.0


def catalog_row_sort_epoch(row: JsonObject, *, session_dir: Path | None = None) -> float:
    """Best-effort “latest activity” epoch for newest-first catalog order."""
    for key in ("sortEpoch", "updatedAt", "createdAt", "updated_at", "created_at"):
        if key == "sortEpoch":
            raw = row.get(key)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return float(raw)
            continue
        ts = _parse_iso_epoch(row.get(key))
        if ts > 0:
            return ts
    path = session_dir
    if path is None:
        path_raw = str(row.get("path") or "").strip()
        if path_raw:
            path = Path(path_raw)
    if path is not None:
        try:
            mt = session_trace_mtime(path)
            if mt > 0:
                return float(mt)
        except OSError:
            pass
        try:
            return float(path.stat().st_mtime)
        except OSError:
            pass
    return 0.0


def show_host_sessions_from_config() -> bool:
    """Whether operator config includes host Grok sessions in the catalog.

    Reads ``show_host_sessions`` from ``~/.groket/config.toml`` (same key as
    the TUI ``H`` toggle). Used by the headless control owner so editor
    ``session/list`` matches the TUI home list without importing the UI package.
    """
    from ..config import load_app_config

    try:
        return load_app_config().show_host_sessions
    except OSError:
        logger.debug("catalog: could not read config for show_host_sessions", exc_info=True)
        return False


def effective_include_host(include_host: bool | None) -> bool:
    """Resolve catalog host inclusion: explicit flag, else config pref."""
    if include_host is not None:
        return bool(include_host)
    return show_host_sessions_from_config()


def catalog_scan_roots(
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> list[SessionScanRoot]:
    """Scan roots for the control/domain session catalog.

    :param work_dir: Work root (``runs/traces`` lives under this).
    :param traces_path: Optional extra traces path (CLI ``-P`` override).
    :param include_host: When true, include host Grok sessions; when false,
        work only; when None, follow ``show_host_sessions`` in config.
    :param host_root: Override for the host sessions root (tests).
    :returns: Ordered scan roots (work first).
    """
    return session_scan_roots(
        work_dir,
        traces_path=traces_path,
        include_host=effective_include_host(include_host),
        host_root=host_root,
    )


def session_catalog_row(
    session_dir: Path,
    *,
    origin: str = "work",
    label: str | None = None,
) -> JsonObject | None:
    """Build one ``session/list`` wire row for *session_dir*, or None on failure.

    :param session_dir: Session directory on disk.
    :param origin: Catalog origin (``work`` / ``host``).
    :param label: Optional display label; defaults to meta label.
    :returns: Wire row mapping, or None when meta cannot be loaded.
    """
    try:
        if origin == ORIGIN_HOST:
            meta = load_host_list_meta(session_dir)
        else:
            meta = load_session_meta_list(session_dir, origin=origin)
    except Exception:
        logger.debug("catalog meta failed for %s", session_dir, exc_info=True)
        return None
    meta.origin = origin
    session_id = (meta.session_id or session_dir.name).strip()
    try:
        path_str = str(session_dir.resolve())
    except OSError:
        path_str = str(session_dir)
    created = str(meta.created_at or "").strip()
    updated = str(meta.updated_at or "").strip()
    sort_epoch = _parse_iso_epoch(updated) or _parse_iso_epoch(created)
    if sort_epoch <= 0:
        try:
            sort_epoch = float(session_trace_mtime(session_dir))
        except OSError:
            sort_epoch = 0.0
    if sort_epoch <= 0:
        try:
            sort_epoch = float(session_dir.stat().st_mtime)
        except OSError:
            sort_epoch = 0.0
    return {
        "sessionId": session_id,
        "path": path_str,
        "title": (meta.title or "").strip(),
        "label": label if label is not None else meta.label,
        "model": meta.model_display,
        "status": meta.list_status_label(),
        "outcome": meta.turn_outcome or "",
        "origin": meta.origin or origin,
        # Home-list columns for attach-mode TUI (and any rich client).
        "taskId": meta.task_id or "",
        "durationSeconds": float(meta.duration_seconds or 0),
        "numEvents": int(meta.num_events or 0),
        "contextUsageCompact": meta.context_usage_compact or "",
        # Structured context so attach hydrate rebuilds context_usage_compact.
        "contextWindowUsagePct": meta.context_window_usage_pct,
        "contextTokensUsed": meta.context_tokens_used,
        "contextWindowTokens": meta.context_window_tokens,
        "toolCallCount": int(meta.tool_call_count or 0),
        "turnCount": int(meta.turn_count or 0),
        "errorCount": int(meta.error_count or 0),
        # Newest-first list ordering for all control clients.
        "createdAt": created,
        "updatedAt": updated,
        "sortEpoch": sort_epoch,
    }


def list_session_catalog(
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
    host_catalog_cache: Path | None = None,
) -> list[JsonObject]:
    """Scan catalog roots and return wire-shaped rows for ``session/list``.

    Work/eval rows load list-meta in a small thread pool. Host rows use
    :func:`load_host_list_meta` (summary, signals, updates tail) and a
    stamp-gated snapshot so a second list does not reopen those files.

    :param work_dir: Work root owning eval traces.
    :param traces_path: Optional traces path override.
    :param include_host: Host inclusion (True/False force; None = config pref).
    :param host_root: Optional host root override (tests).
    :param host_catalog_cache: Optional host snapshot path (tests).
    :returns: Catalog rows sorted newest activity first (``sortEpoch`` desc).
    """
    roots = catalog_scan_roots(
        work_dir,
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    work_roots = [root for root in roots if root.origin != ORIGIN_HOST]
    host_paths = [root.path for root in roots if root.origin == ORIGIN_HOST]
    work_dirs = list(collect_session_dirs(work_roots)) if work_roots else []
    rows: list[JsonObject] = []
    if work_dirs:
        workers = min(4, max(1, len(work_dirs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            built = list(
                pool.map(
                    lambda item: session_catalog_row(item[0], origin=item[1]),
                    work_dirs,
                )
            )
        rows.extend(row for row in built if row is not None)
    seen_host: set[str] = set()
    for hroot in host_paths:
        key = str(hroot)
        if key in seen_host:
            continue
        seen_host.add(key)
        dest = (
            host_catalog_cache
            if host_catalog_cache is not None
            else default_host_catalog_cache(hroot)
        )
        rows.extend(
            load_or_rebuild_host_catalog(
                hroot,
                dest=dest,
                build_row=lambda sd: session_catalog_row(sd, origin=ORIGIN_HOST),
            )
        )
    rows.sort(
        key=lambda r: (
            -catalog_row_sort_epoch(r),
            str(r.get("sessionId") or ""),
        )
    )
    return rows


# List-visible fields. Exclude ``sortEpoch`` / ``path`` so an ``updates.jsonl``
# append that only moves mtime does not bump the catalog revision.
_LIST_ROW_SIG_KEYS: tuple[str, ...] = (
    "sessionId",
    "title",
    "label",
    "model",
    "status",
    "outcome",
    "origin",
    "taskId",
    "durationSeconds",
    "numEvents",
    "contextUsageCompact",
    "contextWindowUsagePct",
    "contextTokensUsed",
    "contextWindowTokens",
    "toolCallCount",
    "turnCount",
    "errorCount",
    "createdAt",
    "updatedAt",
)


def list_row_fingerprint(row: JsonObject) -> tuple[JsonValue, ...]:
    """Stable identity of the fields a catalog client paints."""
    return tuple(row.get(key) for key in _LIST_ROW_SIG_KEYS)


def _keep_list_status(old: JsonObject, new: JsonObject) -> JsonObject:
    """Keep ``complete`` when cheap host meta only has ``—``.

    ``running`` must be allowed to become ``—`` after the stale window so
    the HUD drops the live list poll.
    """
    old_st = str(old.get("status") or "")
    new_st = str(new.get("status") or "")
    if new_st != "—" or old_st != "complete":
        return new
    kept = dict(new)
    kept["status"] = "complete"
    if old.get("outcome") not in (None, ""):
        kept["outcome"] = old.get("outcome")
    return kept


def list_refresh_delta(
    current: list[JsonObject],
    replacements: dict[str, JsonObject],
    appended: list[JsonObject],
    drop: set[str],
) -> tuple[list[JsonObject], list[str], dict[str, bool]]:
    """Compare painted fields. Return upserts, removed ids, and per-id change flags."""
    old_by_path = {str(row.get("path") or "").strip(): row for row in current}
    upserts: list[JsonObject] = []
    list_changed: dict[str, bool] = {}
    for path, new in replacements.items():
        old = old_by_path.get(path)
        if old is not None:
            new = _keep_list_status(old, new)
            replacements[path] = new
        sid = str(new.get("sessionId") or "").strip()
        moved = old is None or list_row_fingerprint(old) != list_row_fingerprint(new)
        if sid:
            list_changed[sid] = moved
        if moved:
            upserts.append(new)
    for new in appended:
        sid = str(new.get("sessionId") or "").strip()
        if sid:
            list_changed[sid] = True
        upserts.append(new)
    removed_ids = [
        str(row.get("sessionId") or "").strip()
        for row in current
        if str(row.get("path") or "").strip() in drop
    ]
    for sid in removed_ids:
        if sid:
            list_changed[sid] = True
    return upserts, removed_ids, list_changed


def _watch_session_hidden(session_dir: Path, child_ids: set[str]) -> bool:
    """True when a filesystem-watch hit is a harness child, not a catalog row."""
    return is_subagent_session_dir(session_dir) or session_dir.name in child_ids


def catalog_roots_fingerprint(
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> tuple[tuple[str, int], ...]:
    """Cheap identity for catalog roots (path, mtime_ns).

    Directory mtime changes when children are added or removed. In-place file
    writes inside a session dir do not bump the root; those use FS-watch
    :meth:`SessionCatalogCache.refresh_rows` instead of a full rescan.
    """
    roots = catalog_scan_roots(
        work_dir,
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    parts: list[tuple[str, int]] = []
    for root in roots:
        path = Path(root.path)
        try:
            st = path.stat()
            mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        except OSError:
            parts.append((str(path), 0))
            continue
        parts.append((str(path), mtime_ns))
    return tuple(parts)


@dataclass
class _CatalogDelta:
    """One catalog revision: upserted rows and removed session ids."""

    revision: int
    upserted: dict[str, JsonObject] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)


class SessionCatalogCache:
    """Single-flight TTL + root-fingerprint cache for ``session/list`` rows.

    Shared by the headless control owner so warm-on-start, periodic refresh, and
    client RPCs share one scan instead of serial full walks.
    """

    DEFAULT_TTL = 300.0
    _DELTA_KEEP = 48

    def __init__(
        self,
        work_dir: Path,
        *,
        traces_path: Path | None = None,
        include_host: bool | None = None,
        host_root: Path | None = None,
        ttl: float = DEFAULT_TTL,
    ) -> None:
        import secrets
        import threading
        import time

        self._work_dir = Path(work_dir).expanduser()
        self._traces_path = Path(traces_path).expanduser() if traces_path is not None else None
        self._include_host = include_host
        self._host_root = host_root
        self._ttl = max(1.0, float(ttl))
        self._lock = threading.Lock()
        self._rows: list[JsonObject] | None = None
        self._mono = 0.0
        self._host_key: bool | None = None
        self._fingerprint: tuple[tuple[str, int], ...] | None = None
        self._building = False
        self._build_done = threading.Event()
        self._build_done.set()
        self._time = time
        # High 31 bits identify this owner instance so a restarted serve cannot
        # treat a client's leftover sinceRevision as "unchanged".
        self._gen = secrets.randbits(31)
        self._seq = 0
        self._revision = 0
        self._deltas: deque[_CatalogDelta] = deque(maxlen=self._DELTA_KEEP)
        self._on_rebuilt: object | None = None

    def __call__(self) -> list[JsonObject]:
        """Return the warm catalog snapshot (``SessionLister``)."""
        return self.get()

    @property
    def revision(self) -> int:
        """Monotonic catalog revision; bumps on full rebuild or row patch."""
        with self._lock:
            return int(self._revision)

    def _host_key_now(self) -> bool:
        return effective_include_host(self._include_host)

    def _fp_now(self) -> tuple[tuple[str, int], ...]:
        return catalog_roots_fingerprint(
            self._work_dir,
            traces_path=self._traces_path,
            include_host=self._include_host,
            host_root=self._host_root,
        )

    def _bump_locked(
        self,
        *,
        upserted: list[JsonObject] | None = None,
        removed: list[str] | None = None,
        clear_deltas: bool = False,
    ) -> int:
        self._seq += 1
        self._revision = (int(self._gen) << 32) | int(self._seq)
        if clear_deltas:
            self._deltas.clear()
        else:
            by_id: dict[str, JsonObject] = {}
            for row in upserted or []:
                sid = str(row.get("sessionId") or "").strip()
                if sid:
                    by_id[sid] = row
            self._deltas.append(
                _CatalogDelta(
                    revision=self._revision,
                    upserted=by_id,
                    removed=[sid for sid in (removed or []) if sid],
                )
            )
        return self._revision

    def delta_since(self, since_revision: int) -> tuple[list[JsonObject], list[str]] | None:
        """Rows upserted and ids removed after *since_revision*, or None if gapped."""
        with self._lock:
            rev = self._revision
            if since_revision <= 0:
                return None
            if (int(since_revision) >> 32) != int(self._gen):
                return None
            if since_revision > rev:
                return None
            if since_revision == rev:
                return [], []
            if not self._deltas or self._deltas[0].revision > since_revision + 1:
                return None
            upserted: dict[str, JsonObject] = {}
            removed: set[str] = set()
            for delta in self._deltas:
                if delta.revision <= since_revision:
                    continue
                for sid in delta.removed:
                    removed.add(sid)
                    upserted.pop(sid, None)
                for sid, row in delta.upserted.items():
                    removed.discard(sid)
                    upserted[sid] = row
            return list(upserted.values()), list(removed)

    def invalidate(self) -> None:
        """Drop cached rows so the next :meth:`get` rebuilds."""
        with self._lock:
            self._rows = None
            self._mono = 0.0
            self._fingerprint = None
            self._deltas.clear()

    def _is_fresh_locked(
        self,
        *,
        force: bool,
        host_key: bool,
        fp: tuple[tuple[str, int], ...],
        now: float,
    ) -> bool:
        return (
            not force
            and self._rows is not None
            and self._host_key is host_key
            and self._fingerprint == fp
            and (now - self._mono) < self._ttl
        )

    def _kick_rebuild(self, *, force: bool = False) -> None:
        """Start a single-flight rebuild if the snapshot is missing or stale."""
        import threading

        host_key = self._host_key_now()
        fp = self._fp_now()
        now = self._time.monotonic()
        with self._lock:
            if self._is_fresh_locked(force=force, host_key=host_key, fp=fp, now=now):
                return
            if self._building:
                return
            self._building = True
            self._build_done.clear()
        worker = threading.Thread(
            target=self._run_rebuild,
            args=(host_key, fp),
            name="groket-catalog-rebuild",
            daemon=True,
        )
        worker.start()

    def _run_rebuild(
        self,
        host_key: bool,
        fp: tuple[tuple[str, int], ...],
    ) -> None:
        try:
            rows = list_session_catalog(
                self._work_dir,
                traces_path=self._traces_path,
                include_host=self._include_host,
                host_root=self._host_root,
            )
            with self._lock:
                prev_ids = (
                    {str(row.get("sessionId") or "").strip() for row in self._rows}
                    if self._rows is not None
                    else None
                )
                self._rows = rows
                self._mono = self._time.monotonic()
                self._host_key = host_key
                self._fingerprint = fp
                self._bump_locked(clear_deltas=True)
            new_ids = {str(row.get("sessionId") or "").strip() for row in rows}
            cb = self._on_rebuilt
            if callable(cb) and (prev_ids is None or prev_ids != new_ids):
                cb()
        finally:
            with self._lock:
                self._building = False
            self._build_done.set()

    def get(self, *, force: bool = False) -> list[JsonObject]:
        """Return catalog rows, rebuilding when stale, forced, or roots changed.

        Callers that must not stall (``session/list``) use :meth:`list_for_rpc`.
        """
        self._kick_rebuild(force=force)
        deadline = self._time.monotonic() + 120.0
        while self._time.monotonic() < deadline:
            with self._lock:
                if not self._building:
                    if self._rows is not None:
                        return list(self._rows)
                    break
            remaining = deadline - self._time.monotonic()
            if remaining <= 0:
                break
            self._build_done.wait(timeout=min(0.25, remaining))
        with self._lock:
            return list(self._rows or [])

    def resolve(self, reference: str) -> Path | None:
        """Map a session id or path to a directory using the warm snapshot.

        Does not wait for a rebuild and does not load session meta. Missing
        cache or unknown id returns None so callers can fall back to a
        name-only directory walk.
        """
        ref = (reference or "").strip()
        if not ref:
            return None
        candidate = Path(ref).expanduser()
        if candidate.is_dir():
            try:
                return candidate.resolve()
            except OSError:
                return candidate
        with self._lock:
            rows = list(self._rows) if self._rows is not None else []
        for row in rows:
            sid = str(row.get("sessionId") or "").strip()
            path_raw = str(row.get("path") or "").strip()
            if not path_raw:
                continue
            if sid != ref and path_raw != ref and Path(path_raw).name != ref:
                continue
            path = Path(path_raw)
            if path.is_dir():
                try:
                    return path.resolve()
                except OSError:
                    return path
        return None

    def refresh_rows(self, session_dirs: list[Path]) -> tuple[list[JsonObject], dict[str, bool]]:
        """Rebuild catalog rows for *session_dirs* without a full tree scan.

        Used on filesystem watches so a live ``updates.jsonl`` write updates
        that session's status immediately. Missing dirs are dropped; new dirs
        are appended. Falls back to a full :meth:`get` when the cache is empty.

        :param session_dirs: Session directories that changed.
        :returns: Updated catalog snapshot (newest-first) and a map of
            session id → whether painted list fields changed.
        """
        dirs = [Path(p).expanduser() for p in session_dirs if str(p).strip()]
        if not dirs:
            return self.get(), {}
        with self._lock:
            if self._building or self._rows is None:
                current = None
                snap_rev = -1
            else:
                current = list(self._rows)
                snap_rev = self._revision
        if current is None:
            return self.get(force=True), {}
        work = (
            Path(self._traces_path).expanduser()
            if self._traces_path is not None
            else work_traces_root(self._work_dir)
        )
        known_paths = {str(row.get("path") or "").strip() for row in current}
        catalog_paths = [Path(p) for p in known_paths if p]
        child_ids = nested_child_ids([*catalog_paths, *dirs])
        drop: set[str] = set()
        replacements: dict[str, JsonObject] = {}
        appended: list[JsonObject] = []
        for session_dir in dirs:
            try:
                resolved = str(session_dir.resolve())
            except OSError:
                resolved = str(session_dir)
            if _watch_session_hidden(session_dir, child_ids):
                drop.add(resolved)
                continue
            origin = classify_session_origin(
                session_dir,
                work_traces=work,
                host_root=self._host_root,
            )
            row = session_catalog_row(session_dir, origin=origin)
            if row is None:
                drop.add(resolved)
                continue
            if resolved in known_paths:
                replacements[resolved] = row
            else:
                appended.append(row)
                known_paths.add(resolved)
        upserts, removed_ids, list_changed = list_refresh_delta(
            current, replacements, appended, drop
        )
        rows = [
            replacements.get(str(row.get("path") or "").strip(), row)
            for row in current
            if str(row.get("path") or "").strip() not in drop
        ]
        rows.extend(appended)
        rows.sort(
            key=lambda r: (
                -catalog_row_sort_epoch(r),
                str(r.get("sessionId") or ""),
            )
        )
        with self._lock:
            if self._building or self._revision != snap_rev:
                return list(self._rows or rows), list_changed
            self._rows = rows
            self._mono = self._time.monotonic()
            if upserts or removed_ids:
                self._bump_locked(upserted=upserts, removed=removed_ids)
        return list(rows), list_changed

    def drop_subagent_rows(self) -> list[JsonObject]:
        """Remove harness child sessions from the warm snapshot.

        Full scans already omit these. A filesystem watch can still append a
        sibling mirror (``session_kind: subagent``, or a basename listed under
        a parent's ``subagents/``). The owner warm loop calls this so those
        rows leave ``session/list`` without a full tree walk.
        """
        with self._lock:
            if self._building or self._rows is None:
                return list(self._rows or [])
            current = list(self._rows)
            snap_rev = self._revision
        paths = [Path(p) for row in current if (p := str(row.get("path") or "").strip())]
        kept = {str(path) for path in drop_subagent_sessions(paths)}
        rows = [row for row in current if str(row.get("path") or "").strip() in kept]
        if len(rows) == len(current):
            return rows
        removed_ids = [
            str(row.get("sessionId") or "").strip()
            for row in current
            if str(row.get("path") or "").strip() not in kept
        ]
        with self._lock:
            if self._building or self._revision != snap_rev:
                return list(self._rows or rows)
            self._rows = rows
            self._mono = self._time.monotonic()
            self._bump_locked(removed=removed_ids)
        return list(rows)

    def list_for_rpc(
        self,
        *,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
        since_revision: int | None = None,
    ) -> JsonObject:
        """Page or delta ``session/list`` from the current snapshot.

        Never waits for a cold full-tree scan. When the cache is empty or
        stale, a background rebuild is started and this call returns the
        current rows (possibly empty) with ``incomplete`` / ``building`` set.

        When *since_revision* matches :attr:`revision`, no rows are transferred.
        When the client is one or more tracked revisions behind, return only
        upserted/removed rows (``delta`` true). Older clients omit
        *since_revision* and get the usual paged snapshot.
        """
        from .access import filter_session_catalog

        self._kick_rebuild(force=False)
        with self._lock:
            rows = list(self._rows) if self._rows is not None else []
            rev = int(self._revision)
            building = bool(self._building)
            incomplete = self._rows is None or building
        if since_revision is not None and int(since_revision) > 0:
            if int(since_revision) == rev:
                full = filter_session_catalog(rows, query=query, limit=0)
                return {
                    "sessions": [],
                    "total": full["total"],
                    "matched": full["matched"],
                    "revision": rev,
                    "unchanged": True,
                    "removed": [],
                    "delta": True,
                    "building": building,
                    "incomplete": incomplete,
                }
            delta = self.delta_since(int(since_revision))
            if delta is not None:
                upserted, removed = delta
                page = filter_session_catalog(
                    upserted,
                    query=query,
                    limit=max(len(upserted), 1),
                    offset=0,
                )
                full = filter_session_catalog(rows, query=query, limit=0)
                removed_vals: list[JsonValue] = [sid for sid in removed]
                return {
                    "sessions": page["sessions"],
                    "total": full["total"],
                    "matched": full["matched"],
                    "revision": rev,
                    "unchanged": False,
                    "removed": removed_vals,
                    "delta": True,
                    "building": building,
                    "incomplete": incomplete,
                }
        out = filter_session_catalog(rows, query=query, limit=limit, offset=offset)
        return {
            "sessions": out["sessions"],
            "total": out["total"],
            "matched": out["matched"],
            "revision": rev,
            "unchanged": False,
            "removed": [],
            "delta": False,
            "building": building,
            "incomplete": incomplete,
        }


def session_meta_from_catalog_row(row: JsonObject) -> SessionMeta | None:
    """Hydrate a minimal :class:`~groket.models.SessionMeta` from a list wire row.

    Used when the TUI attaches as a control client and must not re-scan disk for
    the home list. Status strings map back to outcomes so
    :meth:`~groket.models.SessionMeta.list_status_label` stays consistent.
    """
    path_raw = str(row.get("path") or "").strip()
    sid = str(row.get("sessionId") or "").strip()
    if not path_raw and not sid:
        return None
    session_dir = Path(path_raw) if path_raw else Path(sid)
    raw_origin = str(row.get("origin") or "").strip().lower()
    if is_under_host_grok_sessions(session_dir):
        origin = ORIGIN_HOST
    elif raw_origin == ORIGIN_HOST:
        origin = ORIGIN_HOST
    else:
        origin = raw_origin or ORIGIN_WORK
    meta = SessionMeta(
        session_id=sid or session_dir.name,
        session_dir=session_dir,
        origin=origin,
    )
    title = str(row.get("title") or "").strip()
    if title:
        meta.title = title
    model = str(row.get("model") or "").strip()
    if model:
        if ":" in model:
            mid, _, eff = model.partition(":")
            meta.model_id = mid or "unknown"
            meta.reasoning_effort = eff
        else:
            meta.model_id = model
    outcome = str(row.get("outcome") or "").strip()
    status = str(row.get("status") or "").strip().lower()
    if outcome:
        meta.turn_outcome = outcome
    elif status == "awaiting":
        meta.turn_outcome = "awaiting_follow_up"
    elif status == "running":
        meta.turn_outcome = "running"
    elif status == "ending":
        meta.turn_outcome = "ending"
    elif status == "cancelled":
        meta.turn_outcome = "cancelled"
    elif status == "complete":
        meta.turn_outcome = "success"
    task_id = str(row.get("taskId") or "").strip()
    if task_id:
        meta.task_id = task_id
    created = str(row.get("createdAt") or row.get("created_at") or "").strip()
    if created:
        meta.created_at = created
    updated = str(row.get("updatedAt") or row.get("updated_at") or "").strip()
    if updated:
        meta.updated_at = updated

    def _as_float(value: object, default: float = 0.0) -> float:
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return default
        return default

    def _as_int(value: object, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default

    def _opt_int(key: str) -> int | None:
        raw = row.get(key)
        if raw is None or raw == "":
            return None
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw)
            except ValueError:
                return None
        return None

    meta.duration_seconds = _as_float(row.get("durationSeconds"), 0.0)
    meta.num_events = _as_int(row.get("numEvents"), 0)
    meta.tool_call_count = _as_int(row.get("toolCallCount"), 0)
    meta.turn_count = _as_int(row.get("turnCount"), 0)
    meta.error_count = _as_int(row.get("errorCount"), 0)

    pct = _opt_int("contextWindowUsagePct")
    if pct is not None:
        meta.context_window_usage_pct = max(0, pct)
    used = _opt_int("contextTokensUsed")
    if used is not None:
        meta.context_tokens_used = max(0, used)
    window = _opt_int("contextWindowTokens")
    if window is not None and window > 0:
        meta.context_window_tokens = window
    return meta


def resolve_session_reference(
    reference: str,
    work_dir: Path,
    *,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> Path | None:
    """Resolve a path or catalog session id to an existing session directory.

    Matches an existing directory path, ``root / id``, or a collected
    session directory **name**. Does not load list-meta for siblings.

    :param reference: Absolute/relative path, or a session directory name / id.
    :param work_dir: Work root for catalog roots.
    :param traces_path: Optional traces path override.
    :param include_host: Host inclusion (True/False force; None = config pref).
    :param host_root: Optional host root override (tests).
    :returns: Resolved directory path, or None when not found.
    """
    ref = (reference or "").strip()
    if not ref:
        return None
    candidate = Path(ref).expanduser()
    if candidate.is_dir():
        try:
            return candidate.resolve()
        except OSError:
            return candidate
    roots = catalog_scan_roots(
        work_dir,
        traces_path=traces_path,
        include_host=include_host,
        host_root=host_root,
    )
    for root in roots:
        direct = root.path / ref
        if direct.is_dir():
            try:
                return direct.resolve()
            except OSError:
                return direct
    # Directory name only. List-meta for every sibling is a multi-second tax on
    # each session/overview and session/timeline call. Id≠dirname uses the
    # warm catalog on the control owner (SessionCatalogCache.resolve).
    for session_dir, _origin in collect_session_dirs(roots):
        if session_dir.name == ref:
            try:
                return session_dir.resolve()
            except OSError:
                return session_dir
    return None


__all__ = [
    "SessionCatalogCache",
    "catalog_roots_fingerprint",
    "catalog_scan_roots",
    "effective_include_host",
    "list_session_catalog",
    "resolve_session_reference",
    "session_catalog_row",
    "catalog_row_sort_epoch",
    "session_meta_from_catalog_row",
    "show_host_sessions_from_config",
]
