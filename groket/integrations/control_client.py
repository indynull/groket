"""Async JSON-RPC client for the local groket control Unix socket."""

from __future__ import annotations

import asyncio
import errno
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Self

from ..models import JsonObject, JsonValue, as_json_object, json_as_str
from ..session.access import DEFAULT_SESSION_LIST_LIMIT, catalog_list_next_offset
from .control import PROTOCOL_VERSION, ControlError, default_socket_path

logger = logging.getLogger(__name__)

DEFAULT_CLIENT_TIMEOUT = 5.0
# Catalog / timeline / overview pages can take 12–30s on cold disk (HUD I/O
# budget is 45s). TUI attach uses this for those RPCs; liveness stays short.
HEAVY_RPC_TIMEOUT = 45.0
# Budget for connect-only retries (EAGAIN / refused while owner binds).
CONNECT_RETRY_BUDGET = 3.0
CONNECT_RETRY_INITIAL = 0.02
CONNECT_RETRY_MAX = 0.2
# Default asyncio StreamReader limit is 64 KiB — too small for session/list
# with hundreds of rich catalog rows. One JSON line can be several MB; keep
# a hard ceiling for runaway peers.
STREAM_READ_LIMIT = 16 * 1024 * 1024

# Transient errno values for Unix-domain connect (Linux EAGAIN=11, macOS=35).
_TRANSIENT_ERRNOS = frozenset(
    {
        errno.EAGAIN,
        errno.EWOULDBLOCK,
        errno.EINTR,
        errno.ECONNREFUSED,
        errno.ENOENT,
    }
)


def is_transient_unix_connect_error(exc: BaseException) -> bool:
    """True when *exc* is a connect failure that often succeeds on short retry.

    Covers macOS ``Resource temporarily unavailable`` (os error 35 / EAGAIN),
    connection refused while the control owner is still binding, and missing
    path during auto-serve handoff.
    """
    if isinstance(exc, TimeoutError):
        return False
    if isinstance(exc, FileNotFoundError):
        return True
    if isinstance(exc, ConnectionRefusedError):
        return True
    if isinstance(exc, InterruptedError):
        return True
    if isinstance(exc, BlockingIOError):
        return True
    if isinstance(exc, OSError):
        if exc.errno in _TRANSIENT_ERRNOS:
            return True
        msg = str(exc).lower()
        return (
            "resource temporarily unavailable" in msg
            or "connection refused" in msg
            or "no such file" in msg
        )
    return False


async def open_unix_connection_retrying(
    path: Path,
    *,
    timeout: float = DEFAULT_CLIENT_TIMEOUT,
    budget: float = CONNECT_RETRY_BUDGET,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a Unix connection, retrying transient errors within *budget*.

    :param path: Socket filesystem path.
    :param timeout: Per-attempt ``open_unix_connection`` timeout.
    :param budget: Wall-clock seconds for the whole retry loop.
    :returns: Reader/writer pair for a live connection.
    :raises OSError: Last non-success error after the budget elapses.
    :raises TimeoutError: When a single attempt times out (not retried).
    """
    sock = Path(path).expanduser()
    deadline = time.monotonic() + max(0.05, budget)
    delay = CONNECT_RETRY_INITIAL
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(sock, limit=STREAM_READ_LIMIT),
                timeout=timeout,
            )
        except TimeoutError:
            raise
        except (OSError, ConnectionError) as exc:
            last = exc
            if not is_transient_unix_connect_error(exc):
                raise
            await asyncio.sleep(delay)
            delay = min(delay * 2, CONNECT_RETRY_MAX)
    if last is not None:
        raise last
    raise OSError(errno.ETIMEDOUT, f"control socket connect budget exceeded: {sock}")


class ControlClient:
    """Newline-framed JSON-RPC client for editor / TUI attach traffic.

    Each :meth:`request` opens a short-lived connection so concurrent callers
    do not share a half-read stream. Use :meth:`connect` / instance methods
    only when a single exclusive stream is required.

    Connect retries cover macOS EAGAIN (os error 35) and brief refused races
    while the control owner binds after auto-serve.
    """

    def __init__(
        self,
        socket_path: Path | None = None,
        *,
        timeout: float = DEFAULT_CLIENT_TIMEOUT,
        client_name: str = "groket",
    ) -> None:
        self.socket_path = Path(socket_path or default_socket_path()).expanduser()
        self.timeout = timeout
        self.client_name = client_name
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open a long-lived connection (optional; prefer :meth:`request`)."""
        if self._writer is not None:
            return
        self._reader, self._writer = await open_unix_connection_retrying(
            self.socket_path,
            timeout=self.timeout,
        )

    async def close(self) -> None:
        """Close a long-lived connection if open."""
        writer = self._writer
        self._writer = None
        self._reader = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def request(
        self,
        method: str,
        params: JsonObject | None = None,
        *,
        request_id: int | None = None,
    ) -> JsonValue:
        """Send one request and return the ``result`` value.

        :param method: JSON-RPC method name.
        :param params: Optional params object.
        :param request_id: Optional fixed id (tests); auto-increments otherwise.
        :returns: The RPC result payload.
        :raises ControlError: When the peer returns a JSON-RPC error object.
        :raises TimeoutError: When the peer does not answer in time.
        :raises OSError: On socket failures.
        """
        if self._writer is not None and self._reader is not None:
            async with self._lock:
                return await self._exchange(
                    self._reader,
                    self._writer,
                    method,
                    params,
                    request_id=request_id,
                )
        reader, writer = await open_unix_connection_retrying(
            self.socket_path,
            timeout=self.timeout,
        )
        try:
            return await self._exchange(
                reader,
                writer,
                method,
                params,
                request_id=request_id,
            )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _exchange(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        params: JsonObject | None,
        *,
        request_id: int | None,
    ) -> JsonValue:
        rid = self._next_id if request_id is None else request_id
        if request_id is None:
            self._next_id += 1
        payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params or {},
        }
        writer.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
        await writer.drain()
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            if not line:
                raise ConnectionError(f"control socket closed: {self.socket_path}")
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            message = as_json_object(raw)
            if message.get("id") != rid:
                # Notification or other response; skip for one-shot clients.
                continue
            if "error" in message:
                err = message.get("error")
                err_obj = as_json_object(err) if isinstance(err, dict) else {}
                code = err_obj.get("code")
                code_i = int(code) if isinstance(code, int) else -32603
                data_raw = err_obj.get("data")
                data = as_json_object(data_raw) if isinstance(data_raw, dict) else None
                raise ControlError(
                    code_i,
                    json_as_str(err_obj.get("message")) or "control error",
                    data,
                )
            return message.get("result")

    async def initialize(self) -> JsonObject:
        """Call ``initialize`` and return protocol capabilities."""
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientInfo": {"name": self.client_name},
            },
        )
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_list(
        self,
        *,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
        since_revision: int | None = None,
    ) -> JsonObject:
        """Call ``session/list`` and return one catalog page or a delta."""
        params: JsonObject = {}
        if query:
            params["query"] = query
        if limit is not None:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        if since_revision:
            params["sinceRevision"] = since_revision
        result = await self.request("session/list", params)
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_list_all(
        self,
        *,
        query: str = "",
        page: int = DEFAULT_SESSION_LIST_LIMIT,
    ) -> JsonObject:
        """Drain ``session/list`` pages until ``matched`` (or a stalled owner)."""
        sessions: list[JsonValue] = []
        offset = 0
        total = 0
        matched = 0
        revision = 0
        first_id = ""
        while True:
            result = await self.session_list(query=query, limit=page, offset=offset)
            raw = result.get("sessions")
            batch = [row for row in raw if isinstance(row, dict)] if isinstance(raw, list) else []
            rev_raw = result.get("revision")
            if isinstance(rev_raw, int):
                revision = rev_raw
            if not batch:
                break
            total_raw = result.get("total")
            matched_raw = result.get("matched")
            total = int(total_raw) if isinstance(total_raw, int) else total
            matched = int(matched_raw) if isinstance(matched_raw, int) else matched
            batch_first = str(batch[0].get("sessionId") or "")
            stalled = bool(offset and first_id and batch_first == first_id)
            if stalled:
                break
            if offset == 0:
                first_id = batch_first
            sessions.extend(batch)
            nxt = catalog_list_next_offset(offset, len(batch), page, matched)
            if nxt is None:
                break
            offset = nxt
        return {
            "sessions": sessions,
            "total": total,
            "matched": matched,
            "revision": revision,
            "unchanged": False,
            "removed": [],
            "delta": False,
        }

    async def session_render(
        self,
        session: str,
        *,
        format: str = "org",
    ) -> JsonObject:
        """Call ``session/render`` for *session*."""
        result = await self.request(
            "session/render",
            {"session": session, "format": format},
        )
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_get(self, session: str) -> JsonObject:
        """Call ``session/get`` for rich session metadata."""
        result = await self.request("session/get", {"session": session})
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_overview(
        self,
        session: str,
    ) -> JsonObject:
        """Call ``session/overview`` (meta + turns + lazy timeline stub + notes)."""
        result = await self.request(
            "session/overview",
            {"session": session},
        )
        return as_json_object(result) if isinstance(result, dict) else {}

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
        """Call ``session/timeline`` (paged events)."""
        params: JsonObject = {"session": session, "offset": offset}
        if limit is not None:
            params["limit"] = limit
        if event_type:
            params["type"] = event_type
        if kind:
            params["kind"] = kind
        if query:
            params["query"] = query
        if prompt_index is not None:
            params["promptIndex"] = prompt_index
        if around_index is not None:
            params["aroundIndex"] = around_index
        if at_index is not None:
            params["atIndex"] = at_index
        if content_chars is not None:
            params["contentChars"] = content_chars
        result = await self.request("session/timeline", params)
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_turns(self, session: str) -> JsonObject:
        """Call ``session/turns`` for turn segments."""
        result = await self.request("session/turns", {"session": session})
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_diff(self, session: str) -> JsonObject:
        """Call ``session/diff`` for rewind snapshots or approximate edits."""
        result = await self.request("session/diff", {"session": session})
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_usage(self, session: str) -> JsonObject:
        """Call ``session/usage`` for tool/MCP/skill summary."""
        result = await self.request("session/usage", {"session": session})
        return as_json_object(result) if isinstance(result, dict) else {}

    async def notes_list(self, session: str) -> JsonObject:
        """Call ``notes/list`` for *session*."""
        result = await self.request("notes/list", {"session": session})
        return as_json_object(result) if isinstance(result, dict) else {}

    async def notes_upsert(
        self,
        session: str,
        note: JsonObject,
        *,
        expected_revision: str,
    ) -> JsonObject:
        """Call ``notes/upsert`` for *session*."""
        result = await self.request(
            "notes/upsert",
            {
                "session": session,
                "expectedRevision": expected_revision,
                "note": note,
            },
        )
        return as_json_object(result) if isinstance(result, dict) else {}

    async def notes_delete(
        self,
        session: str,
        note_id: str,
        *,
        expected_revision: str,
    ) -> JsonObject:
        """Call ``notes/delete`` for *session*."""
        result = await self.request(
            "notes/delete",
            {
                "session": session,
                "noteId": note_id,
                "expectedRevision": expected_revision,
            },
        )
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_follow_up(
        self, session: str, prompt: str, *, final: bool = False
    ) -> JsonObject:
        """Call ``session/follow_up``."""
        result = await self.request(
            "session/follow_up",
            {"session": session, "prompt": prompt, "final": bool(final)},
        )
        return as_json_object(result) if isinstance(result, dict) else {}

    async def session_done(self, session: str) -> JsonObject:
        """Call ``session/done``."""
        result = await self.request("session/done", {"session": session})
        return as_json_object(result) if isinstance(result, dict) else {}


async def listen_control_notifications(
    socket_path: Path,
    on_notify: Callable[[str, JsonObject], Awaitable[None] | None],
    *,
    client_name: str = "groket-listener",
    stop: asyncio.Event | None = None,
) -> None:
    """Stay connected and deliver control notifications until *stop* is set.

    Sends ``initialize`` once per connection so the peer records framing, then
    reads JSON-RPC lines. Messages with a ``method`` and no matching request
    ``id`` are treated as notifications.

    :param socket_path: Control Unix socket.
    :param on_notify: ``async (method, params)`` or sync callback.
    :param client_name: ``initialize`` clientInfo name.
    :param stop: Optional event to exit the loop cleanly.
    """
    sock = Path(socket_path).expanduser()
    halt = stop or asyncio.Event()
    while not halt.is_set():
        reader: asyncio.StreamReader | None = None
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await open_unix_connection_retrying(
                sock,
                timeout=DEFAULT_CLIENT_TIMEOUT,
            )
            init_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "clientInfo": {"name": client_name},
                },
            }
            writer.write(json.dumps(init_payload, separators=(",", ":")).encode("utf-8") + b"\n")
            await writer.drain()
            # Drain the initialize result (and any early notifies).
            while not halt.is_set():
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=1.0)
                except TimeoutError:
                    if halt.is_set():
                        break
                    continue
                if not line:
                    break
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                message = as_json_object(raw)
                method = json_as_str(message.get("method"))
                if not method:
                    continue
                # Skip initialize response (has id, no method) — already filtered.
                if "id" in message and message.get("id") is not None and "result" in message:
                    continue
                params_raw = message.get("params")
                params = as_json_object(params_raw) if isinstance(params_raw, dict) else {}
                try:
                    maybe = on_notify(method, params)
                    if inspect.isawaitable(maybe):
                        await maybe
                except Exception:
                    logger.debug("control notify handler failed for %s", method, exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("control notify listener disconnected", exc_info=True)
            if halt.is_set():
                break
            await asyncio.sleep(0.5)
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError):
                    await writer.wait_closed()


async def control_socket_is_live(socket_path: Path, *, timeout: float = 0.5) -> bool:
    """Return True when *socket_path* accepts a connection (live owner)."""
    path = Path(socket_path).expanduser()
    if not path.exists():
        return False
    try:
        _reader, writer = await open_unix_connection_retrying(
            path,
            timeout=timeout,
            budget=min(1.0, max(0.15, timeout * 2)),
        )
    except (TimeoutError, ConnectionRefusedError, FileNotFoundError, OSError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


__all__ = [
    "CONNECT_RETRY_BUDGET",
    "ControlClient",
    "DEFAULT_CLIENT_TIMEOUT",
    "HEAVY_RPC_TIMEOUT",
    "control_socket_is_live",
    "is_transient_unix_connect_error",
    "listen_control_notifications",
    "open_unix_connection_retrying",
]
