"""Session data façade: one implementation for serve and all clients.

``LocalSessionAccess`` runs domain loaders in-process (the serve owner).
``RemoteSessionAccess`` wraps :class:`~groket.integrations.control_client.ControlClient`
with async methods for TUI/tests.

Control JSON-RPC is the multi-process binding of this façade — not a second
catalog/timeline stack.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..integrations.editor import SUPPORTED_FORMATS, render_editor_document
from ..models import JsonObject, JsonValue, json_as_str
from ..notes import (
    NoteEntry,
    NotesSnapshot,
    delete_note,
    load_schema,
    notes_snapshot,
    upsert_note,
)
from ..session.control_views import (
    DEFAULT_CONTENT_CHARS,
    DEFAULT_TIMELINE_LIMIT,
    MAX_CONTENT_CHARS,
    MAX_TIMELINE_LIMIT,
    build_session_diff,
    build_session_get,
    build_session_overview,
    build_session_timeline,
    build_session_turns,
    build_session_usage,
)
from ..session.turn_gate import write_done_for_session, write_follow_up_for_session

if TYPE_CHECKING:
    from ..integrations.control_client import ControlClient

type SessionResolver = Callable[[str], Path | None]
type SessionLister = Callable[[], list[JsonObject]]

DEFAULT_SESSION_LIST_LIMIT = 200


def catalog_list_next_offset(
    offset: int,
    batch_len: int,
    page: int,
    matched: int,
    *,
    stalled: bool = False,
) -> int | None:
    """Next ``session/list`` offset, or ``None`` when the drain is done.

    Stops on an empty or short page, a repeated first row (owner ignored
    ``offset``), or when accumulated rows cover ``matched``.
    """
    if stalled or batch_len <= 0 or page <= 0:
        return None
    nxt = offset + batch_len
    if batch_len < page:
        return None
    if matched > 0 and nxt >= matched:
        return None
    return nxt


def _session_list_haystack(entry: JsonObject) -> str:
    parts = (
        json_as_str(entry.get("sessionId")),
        json_as_str(entry.get("title")),
        json_as_str(entry.get("label")),
        json_as_str(entry.get("model")),
        json_as_str(entry.get("status")),
        json_as_str(entry.get("outcome")),
        json_as_str(entry.get("origin")),
    )
    return " ".join(part for part in parts if part).casefold()


def filter_session_catalog(
    sessions: list[JsonObject],
    *,
    query: str = "",
    limit: int | None = None,
    offset: int = 0,
) -> JsonObject:
    """Filter and page a catalog snapshot for ``session/list``.

    Casefold substring over session id/title/label/model/status/outcome/origin.
    HUD client fuzzy ranking is presentation-only on the returned page.

    :param sessions: Full catalog rows (already shaped for the wire).
    :param query: Case-insensitive substring across list haystack fields.
    :param limit: Page size after filtering; ``None`` means default cap.
    :param offset: Rows to skip after filtering (default 0). Unknown to
        older clients; omitting it keeps the first page.
    :returns: Mapping with ``sessions``, ``total``, and ``matched``.
    """
    needle = (query or "").strip().casefold()
    if needle:
        matched = [row for row in sessions if needle in _session_list_haystack(row)]
    else:
        matched = list(sessions)
    # Preserve catalog newest-first order (do not re-rank by path/id here).
    cap = DEFAULT_SESSION_LIST_LIMIT if limit is None else max(0, limit)
    start = max(0, int(offset))
    sessions_out: list[JsonValue] = list(matched[start : start + cap])
    return {
        "sessions": sessions_out,
        "total": len(sessions),
        "matched": len(matched),
    }


def notes_snapshot_mapping(snapshot: NotesSnapshot) -> JsonObject:
    """Wire mapping for a notes snapshot (shared by access + control)."""
    schema = load_schema()
    return {
        "revision": snapshot.revision,
        "schema": {
            "id": schema.schema_id,
            "fields": [
                {
                    "id": field.id,
                    "label": field.label,
                    "choices": list(field.choices),
                    "pick": field.pick,
                }
                for field in schema.fields
            ],
        },
        "notes": [
            {
                "id": note.id,
                "turnIndex": note.turn_index,
                "fields": dict(note.fields),
                "eventIndices": list(note.event_indices),
                "createdAt": note.created_at,
                "updatedAt": note.updated_at,
            }
            for note in snapshot.doc.sorted_notes()
        ],
    }


class LocalSessionAccess:
    """In-process domain façade (control owner / unit tests)."""

    def __init__(
        self,
        *,
        resolve_session: SessionResolver,
        list_sessions: SessionLister | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self._resolve = resolve_session
        self._list = list_sessions
        self._work_dir = Path(work_dir).expanduser() if work_dir is not None else None

    def resolve_session(self, reference: str) -> Path | None:
        """Map a session id or path to a directory, or None."""
        return self._resolve(reference)

    def require_session(self, reference: str) -> Path:
        """Resolve *reference* or raise :class:`FileNotFoundError`."""
        session = self._resolve((reference or "").strip())
        if session is None or not session.is_dir():
            raise FileNotFoundError(f"session not found: {reference}")
        return session

    def list_sessions(
        self,
        *,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
        since_revision: int | None = None,
    ) -> JsonObject:
        """Catalog snapshot (``sessions`` / ``total`` / ``matched``)."""
        list_for_rpc = getattr(self._list, "list_for_rpc", None)
        if callable(list_for_rpc):
            return list_for_rpc(
                query=query,
                limit=limit,
                offset=offset,
                since_revision=since_revision,
            )
        catalog = list(self._list()) if self._list is not None else []
        out = filter_session_catalog(catalog, query=query, limit=limit, offset=offset)
        return {
            "sessions": out["sessions"],
            "total": out["total"],
            "matched": out["matched"],
            "revision": 0,
            "unchanged": False,
            "removed": [],
            "delta": False,
        }

    def session_get(self, session: str) -> JsonObject:
        """Rich session metadata."""
        path = self.require_session(session)
        return build_session_get(path, work_dir=self._work_dir)

    def session_overview(
        self,
        session: str,
    ) -> JsonObject:
        """Meta + turns + notes (timeline rows via session/timeline)."""
        path = self.require_session(session)
        return build_session_overview(path, work_dir=self._work_dir)

    def session_timeline(
        self,
        session: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        event_type: str = "",
        kind: str = "",
        query: str = "",
        prompt_index: int | None = None,
        around_index: int | None = None,
        at_index: int | None = None,
        content_chars: int | None = None,
    ) -> JsonObject:
        """Paged timeline events."""
        path = self.require_session(session)
        lim = (
            DEFAULT_TIMELINE_LIMIT if limit is None else max(0, min(int(limit), MAX_TIMELINE_LIMIT))
        )
        cc = (
            DEFAULT_CONTENT_CHARS
            if content_chars is None
            else max(0, min(int(content_chars), MAX_CONTENT_CHARS))
        )
        return build_session_timeline(
            path,
            offset=max(0, int(offset)),
            limit=lim,
            event_type=event_type,
            kind=kind,
            query=query,
            prompt_index=prompt_index,
            around_index=around_index,
            at_index=at_index,
            content_chars=cc,
        )

    def session_turns(self, session: str) -> JsonObject:
        """Turn segments."""
        return build_session_turns(self.require_session(session))

    def session_usage(self, session: str) -> JsonObject:
        """Usage summary."""
        return build_session_usage(self.require_session(session))

    def session_diff(self, session: str) -> JsonObject:
        """Rewind snapshots or approximate ``search_replace`` edits."""
        return build_session_diff(self.require_session(session))

    def session_follow_up(self, session: str, prompt: str, *, final: bool = False) -> JsonObject:
        """Stage or queue a follow-up prompt on the session gate."""
        path = self.require_session(session)
        how = write_follow_up_for_session(path, prompt, final=final)
        return {"ok": True, "how": how}

    def session_done(self, session: str) -> JsonObject:
        """Ask a live entrypoint to stop."""
        path = self.require_session(session)
        write_done_for_session(path)
        return {"ok": True}

    def session_render(self, session: str, *, format: str = "org") -> JsonObject:
        """Editor projection document."""
        fmt = (format or "org").strip().lower() or "org"
        if fmt not in SUPPORTED_FORMATS:
            raise ValueError(f"unsupported editor format: {fmt}")
        path = self.require_session(session)
        document = render_editor_document(path, format=fmt)
        return {
            "sessionId": document.session_id,
            "notesRevision": document.notes_revision,
            "promptIndexes": list(document.prompt_indexes),
            "format": document.format,
            "contentType": document.content_type,
            "text": document.text,
        }

    def notes_list(self, session: str) -> JsonObject:
        """Notes snapshot mapping."""
        return notes_snapshot_mapping(notes_snapshot(self.require_session(session)))

    def notes_upsert(
        self,
        session: str,
        note: NoteEntry,
        *,
        expected_revision: str,
    ) -> JsonObject:
        """Upsert a note; return new snapshot mapping."""
        path = self.require_session(session)
        snap = upsert_note(path, note, expected_revision=expected_revision)
        return notes_snapshot_mapping(snap)

    def notes_delete(
        self,
        session: str,
        note_id: str,
        *,
        expected_revision: str,
    ) -> JsonObject:
        """Delete a note; return new snapshot mapping."""
        path = self.require_session(session)
        snap = delete_note(path, note_id, expected_revision=expected_revision)
        return notes_snapshot_mapping(snap)


class RemoteSessionAccess:
    """Async façade over :class:`~groket.integrations.control_client.ControlClient`."""

    def __init__(self, client: ControlClient) -> None:
        self._client = client

    async def list_sessions(
        self,
        *,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
        since_revision: int | None = None,
    ) -> JsonObject:
        return await self._client.session_list(
            query=query,
            limit=limit,
            offset=offset,
            since_revision=since_revision,
        )

    async def session_get(self, session: str) -> JsonObject:
        return await self._client.session_get(session)

    async def session_overview(
        self,
        session: str,
    ) -> JsonObject:
        return await self._client.session_overview(session)

    async def session_timeline(
        self,
        session: str,
        *,
        offset: int = 0,
        limit: int | None = None,
        event_type: str = "",
        kind: str = "",
        query: str = "",
        prompt_index: int | None = None,
        around_index: int | None = None,
        at_index: int | None = None,
        content_chars: int | None = None,
    ) -> JsonObject:
        return await self._client.session_timeline(
            session,
            offset=offset,
            limit=limit,
            event_type=event_type,
            kind=kind,
            query=query,
            prompt_index=prompt_index,
            around_index=around_index,
            at_index=at_index,
            content_chars=content_chars,
        )

    async def session_turns(self, session: str) -> JsonObject:
        return await self._client.session_turns(session)

    async def session_usage(self, session: str) -> JsonObject:
        return await self._client.session_usage(session)

    async def session_diff(self, session: str) -> JsonObject:
        return await self._client.session_diff(session)

    async def session_follow_up(
        self, session: str, prompt: str, *, final: bool = False
    ) -> JsonObject:
        return await self._client.session_follow_up(session, prompt, final=final)

    async def session_done(self, session: str) -> JsonObject:
        return await self._client.session_done(session)

    async def session_render(self, session: str, *, format: str = "org") -> JsonObject:
        return await self._client.session_render(session, format=format)

    async def notes_list(self, session: str) -> JsonObject:
        return await self._client.notes_list(session)

    async def notes_upsert(
        self,
        session: str,
        note: JsonObject,
        *,
        expected_revision: str,
    ) -> JsonObject:
        return await self._client.notes_upsert(session, note, expected_revision=expected_revision)

    async def notes_delete(
        self,
        session: str,
        note_id: str,
        *,
        expected_revision: str,
    ) -> JsonObject:
        return await self._client.notes_delete(
            session, note_id, expected_revision=expected_revision
        )


__all__ = [
    "DEFAULT_SESSION_LIST_LIMIT",
    "LocalSessionAccess",
    "RemoteSessionAccess",
    "SessionLister",
    "SessionResolver",
    "catalog_list_next_offset",
    "filter_session_catalog",
    "notes_snapshot_mapping",
]
