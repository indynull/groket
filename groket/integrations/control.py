"""Versioned local control protocol over a private Unix socket."""

from __future__ import annotations

import asyncio
import errno
import fcntl
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from ..models import JsonObject, JsonValue, as_json_object, json_as_int, json_as_str
from ..notes import (
    NoteEntry,
    NotesConflict,
    NotesSnapshot,
    notes_snapshot,
)
from ..session.access import LocalSessionAccess, notes_snapshot_mapping

# Re-export catalog filter for existing importers (TUI, tests).
from ..session.access import filter_session_catalog as filter_session_catalog
from .control_contract import (
    MIN_PROTOCOL_VERSION,
    NOTIFY_ANALYSIS_CHANGED,
    NOTIFY_NOTES_CHANGED,
    NOTIFY_SESSION_CHANGED,
    NOTIFY_SESSION_SELECTED,
    PROTOCOL_VERSION,
    capability_names,
)
from .editor import SUPPORTED_FORMATS

logger = logging.getLogger(__name__)

# Re-export handshake constants and the advertised method list so existing
# ``from groket.integrations.control import PROTOCOL_VERSION`` callers stay.
CAPABILITIES = capability_names()
_PROTOCOL_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")
type _RpcFn = Callable[..., Awaitable[JsonValue]]
_RPC_DISPATCH: dict[str, _RpcFn] = {}


def _rpc(name: str) -> Callable[[_RpcFn], _RpcFn]:
    """Register an owner method. Keys must match the contract inventory."""

    def wrap(fn: _RpcFn) -> _RpcFn:
        _RPC_DISPATCH[name] = fn
        return fn

    return wrap


def dispatched_method_names() -> frozenset[str]:
    """Method names ``ControlServer._dispatch`` implements."""
    return frozenset(_RPC_DISPATCH)


def parse_protocol_version(value: object) -> tuple[int, int, int] | None:
    """Parse a ``MAJOR.MINOR.PATCH`` control protocol version.

    :param value: ``initialize`` ``protocolVersion`` (string).
    :returns: ``(major, minor, patch)``, or ``None`` when the value is not
        a three-part numeric version string.
    """
    if not isinstance(value, str):
        return None
    match = _PROTOCOL_RE.fullmatch(value.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def protocol_compatible(requested: object, supported: str = PROTOCOL_VERSION) -> bool:
    """True when *requested* and *supported* share a major version.

    Minor and patch may differ. A different major is incompatible.

    :param requested: Client or owner ``protocolVersion``.
    :param supported: Version to compare against (this process).
    """
    left = parse_protocol_version(requested)
    right = parse_protocol_version(supported)
    if left is None or right is None:
        return False
    return left[0] == right[0]


MAX_MESSAGE_BYTES = 8 * 1024 * 1024
NOTIFY_TIMEOUT_SECONDS = 2.0
MAX_HEADER_LINES = 32
# JSON-RPC bodies always open with ``{``; anything shaped like ``Name:`` is an
# LSP-style framing header regardless of which header comes first.
_HEADER_LINE_RE = re.compile(rb"^[A-Za-z][A-Za-z0-9-]*:")
# Note ids and field ids are woven verbatim into the Org property drawers and
# ``<!-- groket:… -->`` machine tags of every projection; whitespace, ``-->``
# or newlines there corrupt the round trip, so reject them at the boundary.
_NOTE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TIMESTAMP_RE = re.compile(r"^[0-9][0-9T:+.Z-]{0,63}$")
CAPABILITIES = (
    "session/list",
    "session/get",
    "session/overview",
    "session/timeline",
    "session/turns",
    "session/usage",
    "session/findings",
    "session/diff",
    "session/open",
    "session/render",
    "notes/list",
    "notes/upsert",
    "notes/delete",
    "analysis/run",
    "analysis/status",
    "session/follow_up",
    "session/done",
)
# Concurrent disk-heavy RPCs (parse/catalog) share this bound so multi-client
# opens cannot stampede the owner beyond single-flight per session.
HEAVY_IO_CONCURRENCY = 4

type SessionResolver = Callable[[str], Path | None]
type SessionLister = Callable[[], list[JsonObject]]
type OpenSession = Callable[[Path, int | None], Awaitable[bool]]
type NotesChanged = Callable[[Path], Awaitable[None]]


@dataclass(frozen=True)
class ControlError(Exception):
    """JSON-RPC error with stable structured data."""

    code: int
    message: str
    data: JsonObject | None = None

    def __str__(self) -> str:
        return self.message


def is_unknown_method(exc: BaseException | str) -> bool:
    """True when the owner rejected the call as an unknown method (stale serve)."""
    if isinstance(exc, ControlError) and exc.code == -32601:
        return True
    text = exc if isinstance(exc, str) else str(exc)
    low = text.casefold()
    return "method not found" in low or "-32601" in low


@dataclass(frozen=True)
class ControlSocketInUse(Exception):
    """Another live process already owns the control socket (singleton)."""

    socket_path: Path

    def __str__(self) -> str:
        return f"control socket is active: {self.socket_path}"


def default_socket_path() -> Path:
    """Return the per-user runtime socket path."""
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    root = Path(runtime) if runtime else Path.home() / ".groket" / "run"
    return root / "groket" / "control.sock" if runtime else root / "control.sock"


def _default_resolve_session(reference: str) -> Path | None:
    path = Path(reference).expanduser()
    return path.resolve() if path.is_dir() else None


def _optional_int_param(value: JsonValue | None, *, name: str) -> int | None:
    """Parse an optional JSON-RPC integer param, or raise ``ControlError`` (-32602)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ControlError(-32602, f"{name} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ControlError(-32602, f"{name} must be an integer")
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ControlError(-32602, f"{name} must be an integer") from exc
    raise ControlError(-32602, f"{name} must be an integer")


def _note_mapping(note: NoteEntry) -> JsonObject:
    return {
        "id": note.id,
        "turnIndex": note.turn_index,
        "fields": dict(note.fields),
        "eventIndices": list(note.event_indices),
        "createdAt": note.created_at,
        "updatedAt": note.updated_at,
    }


def _snapshot_mapping(snapshot: NotesSnapshot) -> JsonObject:
    return notes_snapshot_mapping(snapshot)


def _rpc_params_summary(params: JsonObject) -> str:
    """Short, non-sensitive param summary for control RPC logs."""
    bits: list[str] = []
    for key in (
        "session",
        "sessionId",
        "query",
        "limit",
        "offset",
        "sinceRevision",
        "force",
        "format",
        "noteId",
        "promptIndex",
        "type",
        "eventType",
        "contentChars",
    ):
        if key not in params or params[key] is None:
            continue
        bits.append(f"{key}={params[key]!r}")
    note = params.get("note")
    if isinstance(note, dict):
        bits.append(f"note.id={note.get('id')!r}")
    elif note is not None:
        bits.append("note=…")
    return " ".join(bits) if bits else "-"


def _rpc_result_summary(result: JsonValue) -> str:
    """Compact result shape for control RPC logs (no large bodies)."""
    if result is None:
        return "null"
    if isinstance(result, bool):
        return str(result).lower()
    if isinstance(result, (int, float)):
        return repr(result)
    if isinstance(result, str):
        return f"str(len={len(result)})"
    if isinstance(result, list):
        return f"list(len={len(result)})"
    if isinstance(result, dict):
        keys = list(result.keys())
        head = ",".join(str(k) for k in keys[:8])
        more = f"+{len(keys) - 8}" if len(keys) > 8 else ""
        # Useful counters when present on catalog / status payloads.
        extra: list[str] = []
        for key in ("total", "matched", "state", "opened", "sessionId", "jobId"):
            if key in result and result[key] is not None:
                extra.append(f"{key}={result[key]!r}")
        base = f"dict[{head}{more}]"
        return f"{base} {' '.join(extra)}".strip() if extra else base
    return type(result).__name__


@dataclass
class AnalysisJobState:
    """In-flight or completed analysis job for one session (serve owner)."""

    session_id: str
    session_path: str
    state: str = "idle"
    force: bool = False
    job_id: str = ""
    error: str = ""
    started_at: float | None = None
    finished_at: float | None = None
    analyzer_ids: list[str] = field(default_factory=list)
    ok_count: int = 0
    error_count: int = 0
    finding_count: int = 0

    def as_mapping(self) -> JsonObject:
        """Wire mapping for ``analysis/status`` / ``analysis/run``."""
        return {
            "sessionId": self.session_id,
            "path": self.session_path,
            "state": self.state,
            "force": self.force,
            "jobId": self.job_id,
            "error": self.error,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "analyzerIds": list(self.analyzer_ids),
            "okCount": self.ok_count,
            "errorCount": self.error_count,
            "findingCount": self.finding_count,
        }


def _note_from_params(data: JsonObject) -> NoteEntry:
    note_id = json_as_str(data.get("id")).strip()
    if not note_id:
        raise ControlError(-32602, "note.id is required")
    if not _NOTE_TOKEN_RE.match(note_id):
        raise ControlError(-32602, "note.id must match [A-Za-z0-9][A-Za-z0-9._-]*")
    raw_fields = data.get("fields")
    fields = (
        {str(key): str(value) for key, value in raw_fields.items() if value is not None}
        if isinstance(raw_fields, dict)
        else {}
    )
    for key in fields:
        if not _NOTE_TOKEN_RE.match(key):
            raise ControlError(-32602, f"field id {key!r} must match [A-Za-z0-9][A-Za-z0-9._-]*")
    created_at = json_as_str(data.get("createdAt"))
    updated_at = json_as_str(data.get("updatedAt"))
    for stamp_name, stamp in (("createdAt", created_at), ("updatedAt", updated_at)):
        if stamp and not _TIMESTAMP_RE.match(stamp):
            raise ControlError(-32602, f"{stamp_name} must be a compact timestamp")
    raw_indices = data.get("eventIndices")
    event_indices = (
        [json_as_int(value) for value in raw_indices] if isinstance(raw_indices, list) else []
    )
    return NoteEntry(
        id=note_id,
        turn_index=json_as_int(data.get("turnIndex")),
        fields=fields,
        event_indices=event_indices,
        created_at=created_at,
        updated_at=updated_at,
    )


class ControlServer:
    """Async JSON-RPC server for local editor integrations."""

    def __init__(
        self,
        *,
        socket_path: Path | None = None,
        resolve_session: SessionResolver | None = None,
        list_sessions: SessionLister | None = None,
        open_session: OpenSession | None = None,
        notes_changed: NotesChanged | None = None,
        work_dir: Path | None = None,
        analysis_service: object | None = None,
        analysis_traces: Path | None = None,
    ) -> None:
        self.socket_path = Path(socket_path or default_socket_path()).expanduser()
        self._resolve_session = resolve_session or _default_resolve_session
        self._list_sessions = list_sessions
        self._open_session = open_session
        self._notes_changed = notes_changed
        self._work_dir = Path(work_dir).expanduser() if work_dir is not None else None
        self._analysis_service = analysis_service
        self._analysis_traces = (
            Path(analysis_traces).expanduser() if analysis_traces is not None else None
        )
        self._access = LocalSessionAccess(
            resolve_session=self._resolve_session,
            list_sessions=list_sessions,
            work_dir=self._work_dir,
        )
        self._server: asyncio.AbstractServer | None = None
        self._lock_fd: int | None = None
        self._writers: set[asyncio.StreamWriter] = set()
        self._writer_framing: dict[asyncio.StreamWriter, str] = {}
        self._analysis_jobs: dict[str, AnalysisJobState] = {}
        self._analysis_lock = threading.Lock()
        self._analysis_pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="groket-analysis",
        )
        self._analysis_futures: dict[str, Future[None]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        # Cap concurrent disk-heavy access work so many open clients cannot
        # stampede multi‑MB parses (single-flight still joins per session).
        self._heavy_sem = asyncio.Semaphore(HEAVY_IO_CONCURRENCY)

    async def start(self) -> None:
        """Bind the configured socket and begin accepting connections."""
        socket_parent = self.socket_path.parent
        try:
            socket_parent.mkdir(parents=True)
        except FileExistsError:
            if not socket_parent.is_dir():
                raise
        try:
            socket_parent.chmod(0o700)
        except OSError:
            logger.warning("could not tighten permissions on %s", socket_parent)
        self._acquire_lock()
        try:
            await self._takeover_or_fail()
            try:
                self._server = await asyncio.start_unix_server(
                    self._handle_client,
                    path=self.socket_path,
                    limit=MAX_MESSAGE_BYTES + 1,
                )
            except OSError as exc:
                if exc.errno in {errno.EADDRINUSE, errno.EEXIST}:
                    raise ControlSocketInUse(self.socket_path) from exc
                raise
            self.socket_path.chmod(0o600)
            self._loop = asyncio.get_running_loop()
        except BaseException:
            self._release_lock()
            raise

    def _acquire_lock(self) -> None:
        """Take the exclusive advisory lock that serializes socket ownership.

        The lock removes the probe/unlink/bind race between two starting
        instances and lets ``close()`` know the socket file is still ours to
        remove. The lock file itself is never unlinked: removing it would
        reopen the race for a third starter.

        After the flock is held, the owner pid is written into the lock file so
        ``groket serve stop`` can find a zombie that still holds the lock after
        the socket / ``.pid`` file were lost.
        """
        lock_path = self.socket_path.with_name(self.socket_path.name + ".lock")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(lock_fd)
            raise ControlSocketInUse(self.socket_path) from exc
        try:
            os.ftruncate(lock_fd, 0)
            os.lseek(lock_fd, 0, os.SEEK_SET)
            os.write(lock_fd, f"{os.getpid()}\n".encode())
            os.fsync(lock_fd)
        except OSError:
            logger.debug("could not write owner pid into %s", lock_path, exc_info=True)
        self._lock_fd = lock_fd

    def _release_lock(self) -> None:
        if self._lock_fd is not None:
            try:
                os.ftruncate(self._lock_fd, 0)
            except OSError:
                pass
            os.close(self._lock_fd)
            self._lock_fd = None

    async def _takeover_or_fail(self) -> None:
        """Remove a stale socket file, or refuse when a live owner answers.

        The probe still matters with the lock held: an owner from a build
        without the lock file may hold the path, and it must not be stolen.
        """
        if not self.socket_path.exists():
            return
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=0.5,
            )
        except TimeoutError as exc:
            # Ambiguous: prefer not to unlink a path that may still be owned.
            raise ControlSocketInUse(self.socket_path) from exc
        except (ConnectionRefusedError, FileNotFoundError):
            self.socket_path.unlink(missing_ok=True)
        except OSError as exc:
            if exc.errno in {errno.ECONNREFUSED, errno.ENOENT}:
                self.socket_path.unlink(missing_ok=True)
            else:
                # Do not unlink on unexpected errors (avoids stealing a live socket).
                raise ControlSocketInUse(self.socket_path) from exc
        else:
            writer.close()
            await writer.wait_closed()
            raise ControlSocketInUse(self.socket_path)

    async def serve_forever(self) -> None:
        """Serve until cancelled, then release the owned socket.

        :raises ControlSocketInUse: When another live owner holds the path.
        """
        if self._server is None:
            await self.start()
        assert self._server is not None
        try:
            await self._server.serve_forever()
        finally:
            await self.close()

    async def close(self) -> None:
        """Close listeners and connected streams."""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        writers = list(self._writers)
        self._writers.clear()
        self._writer_framing.clear()
        for writer in writers:
            writer.close()
        for writer in writers:
            try:
                await writer.wait_closed()
            except OSError:
                pass
        self._analysis_pool.shutdown(wait=False, cancel_futures=True)
        # Unlink only while holding the ownership lock; another instance may
        # have bound a fresh socket at this path since we lost or never had it.
        if self._lock_fd is not None:
            self.socket_path.unlink(missing_ok=True)
        self._release_lock()
        self._loop = None

    def _analysis_status_unlocked(self, session_id: str, session_path: str) -> AnalysisJobState:
        job = self._analysis_jobs.get(session_id)
        if job is not None:
            return job
        return AnalysisJobState(session_id=session_id, session_path=session_path, state="idle")

    def _enqueue_analysis(self, session: Path, *, force: bool) -> AnalysisJobState:
        """Start or return the in-flight job for *session* (thread-safe)."""
        sid = session.name
        path_str = str(session)
        with self._analysis_lock:
            existing = self._analysis_jobs.get(sid)
            if existing is not None and existing.state == "running":
                return existing
            job = AnalysisJobState(
                session_id=sid,
                session_path=path_str,
                state="running",
                force=bool(force),
                job_id=uuid.uuid4().hex[:12],
                started_at=time.time(),
            )
            self._analysis_jobs[sid] = job
            future = self._analysis_pool.submit(self._run_analysis_job, sid, path_str, bool(force))
            self._analysis_futures[sid] = future
            return job

    def _run_analysis_job(self, session_id: str, path_str: str, force: bool) -> None:
        """Worker: run analyzers and publish ``analysis/changed``."""
        access = self._access
        service = self._analysis_service
        try:
            summary = access.analysis_run(path_str, force=force, service=service)
        except FileNotFoundError as exc:
            summary = {
                "sessionId": session_id,
                "path": path_str,
                "state": "error",
                "force": force,
                "error": str(exc)[:500],
                "analyzerIds": [],
                "okCount": 0,
                "errorCount": 0,
                "findingCount": 0,
            }
        except Exception as exc:
            logger.exception("analysis job failed for %s", session_id)
            summary = {
                "sessionId": session_id,
                "path": path_str,
                "state": "error",
                "force": force,
                "error": str(exc)[:500],
                "analyzerIds": [],
                "okCount": 0,
                "errorCount": 0,
                "findingCount": 0,
            }
        finished = time.time()
        with self._analysis_lock:
            job = self._analysis_jobs.get(session_id)
            if job is None:
                job = AnalysisJobState(session_id=session_id, session_path=path_str)
                self._analysis_jobs[session_id] = job
            job.state = json_as_str(summary.get("state")) or "done"
            job.force = bool(summary.get("force", force))
            job.error = json_as_str(summary.get("error"))
            job.finished_at = finished
            if job.started_at is None:
                job.started_at = finished
            raw_ids = summary.get("analyzerIds")
            if isinstance(raw_ids, list):
                job.analyzer_ids = [str(x) for x in raw_ids]
            job.ok_count = json_as_int(summary.get("okCount"))
            job.error_count = json_as_int(summary.get("errorCount"))
            job.finding_count = json_as_int(summary.get("findingCount"))
            payload = job.as_mapping()
            self._analysis_futures.pop(session_id, None)
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self.notify(NOTIFY_ANALYSIS_CHANGED, payload),
                loop,
            )

    def _ensure_analysis_service(self) -> object | None:
        """Construct the analysis facade on first ``analysis/run``, not at serve start."""
        if self._analysis_service is not None:
            return self._analysis_service
        if self._work_dir is None:
            return None
        from ..analysis.service import AnalysisService
        from ..paths import analysis_cache_dir

        self._analysis_service = AnalysisService(
            self._work_dir,
            traces=self._analysis_traces,
            cache_root=analysis_cache_dir(),
        )
        return self._analysis_service

    async def _analysis_run(self, params: JsonObject) -> JsonObject:
        if self._ensure_analysis_service() is None:
            raise ControlError(501, "analysis is unavailable")
        session = self._session(params)
        force = bool(params.get("force"))
        job = await asyncio.to_thread(self._enqueue_analysis, session, force=force)
        return job.as_mapping()

    async def _analysis_status(self, params: JsonObject) -> JsonObject:
        """In-memory job table only — do not resolve/catalog-scan on the poll path.

        Status is polled while analysis holds the GIL on multi‑MB parses; any
        disk walk here (``resolve_session`` → ``collect_session_dirs``) turns a
        dict lookup into hundreds of milliseconds.
        """
        ref = self._session_ref(params)
        # Job keys are directory names; accept id or path basename.
        try:
            sid = Path(ref).name if ref else ""
        except (TypeError, ValueError):
            sid = ref
        sid = (sid or ref).strip()
        with self._analysis_lock:
            return self._analysis_status_unlocked(sid, ref).as_mapping()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = id(writer)
        logger.debug("control client connect id=%s", peer)
        try:
            while not reader.at_eof():
                try:
                    first_line = await reader.readline()
                except (ValueError, asyncio.LimitOverrunError):
                    await self._send_error(writer, None, -32600, "message exceeds size limit")
                    break
                if not first_line:
                    break
                if _HEADER_LINE_RE.match(first_line):
                    self._writer_framing[writer] = "headers"
                    length = await self._read_header_length(reader, writer, first_line)
                    if length is None:
                        break
                    try:
                        message = await reader.readexactly(length)
                    except asyncio.IncompleteReadError:
                        break
                else:
                    self._writer_framing.setdefault(writer, "newline")
                    message = first_line
                if len(message) > MAX_MESSAGE_BYTES:
                    await self._send_error(writer, None, -32600, "message exceeds size limit")
                    break
                # Framing is known. Add to the broadcast set only after this
                # request finishes so a slow one-shot (HUD session/timeline)
                # does not read session/changed as its JSON-RPC result.
                await self._handle_line(message, writer)
                self._writers.add(writer)
        finally:
            self._writers.discard(writer)
            self._writer_framing.pop(writer, None)
            logger.debug(
                "control client disconnect id=%s remaining_writers=%s",
                peer,
                len(self._writers),
            )
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError, ConnectionError, OSError):
                # Peer already gone; not an ownership fault.
                pass

    async def _read_header_length(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        first_line: bytes,
    ) -> int | None:
        """Consume an LSP-style header block and return the Content-Length.

        Accepts any header order (e.g. ``Content-Type`` first); replies with a
        -32600 error and returns ``None`` when the block is unusable.
        """
        length: int | None = None
        header = first_line
        header_count = 0
        while header not in (b"\r\n", b"\n", b""):
            header_count += 1
            if header_count > MAX_HEADER_LINES:
                await self._send_error(writer, None, -32600, "too many framing headers")
                return None
            name, _, value = header.partition(b":")
            if name.strip().lower() == b"content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    await self._send_error(writer, None, -32600, "invalid Content-Length")
                    return None
            header = await reader.readline()
        if length is None:
            await self._send_error(writer, None, -32600, "missing Content-Length")
            return None
        if length < 0 or length > MAX_MESSAGE_BYTES:
            await self._send_error(writer, None, -32600, "message exceeds size limit")
            return None
        return length

    async def _handle_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        try:
            raw = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.info("control rpc parse_error bytes=%s", len(line))
            await self._send_error(writer, None, -32700, "parse error")
            return
        if not isinstance(raw, dict):
            logger.info("control rpc invalid_request (not object)")
            await self._send_error(writer, None, -32600, "invalid request")
            return
        request = as_json_object(raw)
        request_id = request.get("id")
        method = json_as_str(request.get("method"))
        if request.get("jsonrpc") != "2.0" or not method:
            logger.info("control rpc invalid_request id=%s method=%r", request_id, method)
            await self._send_error(writer, request_id, -32600, "invalid request")
            return
        params_raw = request.get("params")
        params = as_json_object(params_raw) if isinstance(params_raw, dict) else {}
        after_send: list[tuple[str, JsonObject]] = []
        param_summary = _rpc_params_summary(params)
        logger.debug(
            "control rpc ← id=%s method=%s %s",
            request_id,
            method,
            param_summary,
        )
        t0 = time.perf_counter()
        try:
            result = await self._dispatch(method, params, after_send)
        except NotesConflict as exc:
            ms = (time.perf_counter() - t0) * 1000
            logger.info(
                "control rpc → id=%s method=%s status=409 notes_conflict %.1fms %s",
                request_id,
                method,
                ms,
                param_summary,
            )
            await self._send_error(
                writer,
                request_id,
                409,
                "operator notes changed",
                {"kind": "notes_conflict", "currentRevision": exc.current_revision},
            )
            return
        except ControlError as exc:
            ms = (time.perf_counter() - t0) * 1000
            logger.info(
                "control rpc → id=%s method=%s status=%s %.1fms %s err=%s",
                request_id,
                method,
                exc.code,
                ms,
                param_summary,
                exc.message,
            )
            await self._send_error(writer, request_id, exc.code, exc.message, exc.data)
            return
        except OSError as exc:
            ms = (time.perf_counter() - t0) * 1000
            logger.warning(
                "control rpc → id=%s method=%s status=-32603 %.1fms %s err=%s",
                request_id,
                method,
                ms,
                param_summary,
                exc,
            )
            await self._send_error(writer, request_id, -32603, str(exc))
            return
        except Exception as exc:
            ms = (time.perf_counter() - t0) * 1000
            logger.exception(
                "control rpc → id=%s method=%s status=-32603 %.1fms %s",
                request_id,
                method,
                ms,
                param_summary,
            )
            await self._send_error(writer, request_id, -32603, f"internal error: {exc}")
            return
        ms = (time.perf_counter() - t0) * 1000
        # All successful access RPCs at the same level (debug). INFO is for
        # conflicts / errors and operator-visible state changes elsewhere.
        # Polls (analysis/status, list, timeline) must not fill the serve log.
        logger.debug(
            "control rpc → id=%s method=%s status=ok %.1fms %s result=%s",
            request_id,
            method,
            ms,
            param_summary,
            _rpc_result_summary(result),
        )
        if request_id is not None:
            await self._send(writer, {"jsonrpc": "2.0", "id": request_id, "result": result})
        # Broadcasts go out after the response so the requesting client can update
        # its own revision first and recognize the notification as its own echo.
        for notify_method, notify_params in after_send:
            logger.debug(
                "control notify → method=%s %s",
                notify_method,
                _rpc_params_summary(notify_params),
            )
            await self.notify(notify_method, notify_params)

    def _session_ref(self, params: JsonObject) -> str:
        reference = json_as_str(params.get("session")).strip()
        if not reference:
            raise ControlError(-32602, "session is required")
        return reference

    def _session(self, params: JsonObject) -> Path:
        reference = self._session_ref(params)
        session = self._resolve_session(reference)
        if session is None or not session.is_dir():
            raise ControlError(404, "session not found", {"session": reference})
        return session

    def _mark_session_interest(self, session: Path) -> None:
        apply = getattr(self, "_catalog_apply", None)
        mark = getattr(apply, "mark_open", None)
        if callable(mark):
            mark(session)

    async def _access_call(
        self,
        ref: str,
        fn: Callable[..., JsonObject],
        *args: object,
        **kwargs: object,
    ) -> JsonObject:
        """Run a LocalSessionAccess method off the event loop; map missing sessions."""

        def _run() -> JsonObject:
            try:
                return fn(*args, **kwargs)
            except FileNotFoundError as exc:
                raise ControlError(404, "session not found", {"session": ref}) from exc

        async with self._heavy_sem:
            return await asyncio.to_thread(_run)

    async def _dispatch(
        self,
        method: str,
        params: JsonObject,
        after_send: list[tuple[str, JsonObject]],
    ) -> JsonValue:
        handler = _RPC_DISPATCH.get(method)
        if handler is None:
            raise ControlError(-32601, "method not found", {"method": method})
        return await handler(self, params, after_send)

    @_rpc("initialize")
    async def _rpc_initialize(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        requested = params.get("protocolVersion")
        if not protocol_compatible(requested):
            raise ControlError(
                -32602,
                "unsupported protocol version",
                {"supported": PROTOCOL_VERSION, "minimum": MIN_PROTOCOL_VERSION},
            )
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": list(capability_names()),
            "renderFormats": list(SUPPORTED_FORMATS),
        }

    @_rpc("session/list")
    async def _rpc_session_list(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        async with self._heavy_sem:
            return await asyncio.to_thread(
                self._access.list_sessions,
                query=json_as_str(params.get("query")),
                limit=_optional_int_param(params.get("limit"), name="limit"),
                offset=_optional_int_param(params.get("offset"), name="offset") or 0,
                since_revision=_optional_int_param(
                    params.get("sinceRevision"), name="sinceRevision"
                ),
            )

    @_rpc("session/get")
    async def _rpc_session_get(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        self._mark_session_interest(self._resolve_session(ref) or Path(ref))
        return await self._access_call(ref, self._access.session_get, ref)

    @_rpc("session/overview")
    async def _rpc_session_overview(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        self._mark_session_interest(self._resolve_session(ref) or Path(ref))
        return await self._access_call(ref, self._access.session_overview, ref)

    @_rpc("session/timeline")
    async def _rpc_session_timeline(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        self._mark_session_interest(self._resolve_session(ref) or Path(ref))
        return await self._access_call(
            ref,
            self._access.session_timeline,
            ref,
            offset=_optional_int_param(params.get("offset"), name="offset") or 0,
            limit=_optional_int_param(params.get("limit"), name="limit"),
            event_type=json_as_str(params.get("type") or params.get("eventType")),
            kind=json_as_str(params.get("kind")),
            query=json_as_str(params.get("query")),
            prompt_index=_optional_int_param(params.get("promptIndex"), name="promptIndex"),
            around_index=_optional_int_param(params.get("aroundIndex"), name="aroundIndex"),
            at_index=_optional_int_param(params.get("atIndex"), name="atIndex"),
            content_chars=_optional_int_param(params.get("contentChars"), name="contentChars"),
        )

    @_rpc("session/turns")
    async def _rpc_session_turns(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        return await self._access_call(ref, self._access.session_turns, ref)

    @_rpc("session/usage")
    async def _rpc_session_usage(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        return await self._access_call(ref, self._access.session_usage, ref)

    @_rpc("session/findings")
    async def _rpc_session_findings(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        raw_lim = _optional_int_param(params.get("limit"), name="limit")
        return await self._access_call(ref, self._access.session_findings, ref, limit=raw_lim)

    @_rpc("session/diff")
    async def _rpc_session_diff(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        return await self._access_call(ref, self._access.session_diff, ref)

    @_rpc("session/render")
    async def _rpc_session_render(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        fmt = json_as_str(params.get("format")).strip().lower() or "org"
        if fmt not in SUPPORTED_FORMATS:
            raise ControlError(
                -32602,
                "unsupported editor format",
                {"supported": list(SUPPORTED_FORMATS), "format": fmt},
            )
        ref = self._session_ref(params)

        def _render() -> JsonObject:
            try:
                return self._access.session_render(ref, format=fmt)
            except FileNotFoundError as exc:
                raise ControlError(404, "session not found", {"session": ref}) from exc
            except ValueError as exc:
                raise ControlError(
                    -32602,
                    str(exc),
                    {"supported": list(SUPPORTED_FORMATS), "format": fmt},
                ) from exc

        async with self._heavy_sem:
            return await asyncio.to_thread(_render)

    @_rpc("session/open")
    async def _rpc_session_open(
        self, params: JsonObject, after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        raw_prompt = params.get("promptIndex")
        prompt_index = None if raw_prompt is None else json_as_int(raw_prompt)
        session = self._session(params)
        self._mark_session_interest(session)
        opened = True
        if self._open_session is not None:
            opened = bool(await self._open_session(session, prompt_index))
        if opened:
            after_send.append(
                (
                    NOTIFY_SESSION_SELECTED,
                    {"sessionId": session.name, "promptIndex": prompt_index},
                )
            )
        return {"opened": bool(opened)}

    @_rpc("notes/list")
    async def _rpc_notes_list(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        return await self._access_call(ref, self._access.notes_list, ref)

    @_rpc("notes/upsert")
    async def _rpc_notes_upsert(
        self, params: JsonObject, after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        note_raw = params.get("note")
        if not isinstance(note_raw, dict):
            raise ControlError(-32602, "note is required")
        ref = self._session_ref(params)
        session = self._session(params)
        note = _note_from_params(as_json_object(note_raw))
        rev = json_as_str(params.get("expectedRevision"))
        result = await self._access_call(
            ref,
            self._access.notes_upsert,
            ref,
            note,
            expected_revision=rev,
        )
        if self._notes_changed is not None:
            await self._notes_changed(session)
        after_send.append(
            (
                NOTIFY_NOTES_CHANGED,
                {
                    "sessionId": session.name,
                    "revision": json_as_str(result.get("revision")),
                },
            )
        )
        return result

    @_rpc("notes/delete")
    async def _rpc_notes_delete(
        self, params: JsonObject, after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        note_id = json_as_str(params.get("noteId")).strip()
        if not note_id:
            raise ControlError(-32602, "noteId is required")
        ref = self._session_ref(params)
        session = self._session(params)
        rev = json_as_str(params.get("expectedRevision"))
        result = await self._access_call(
            ref,
            self._access.notes_delete,
            ref,
            note_id,
            expected_revision=rev,
        )
        if self._notes_changed is not None:
            await self._notes_changed(session)
        after_send.append(
            (
                NOTIFY_NOTES_CHANGED,
                {
                    "sessionId": session.name,
                    "revision": json_as_str(result.get("revision")),
                },
            )
        )
        return result

    @_rpc("analysis/run")
    async def _rpc_analysis_run(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        return await self._analysis_run(params)

    @_rpc("analysis/status")
    async def _rpc_analysis_status(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        return await self._analysis_status(params)

    @_rpc("session/follow_up")
    async def _rpc_session_follow_up(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        prompt = json_as_str(params.get("prompt"))
        final = bool(params.get("final"))
        return await self._access_call(
            ref, self._access.session_follow_up, ref, prompt, final=final
        )

    @_rpc("session/done")
    async def _rpc_session_done(
        self, params: JsonObject, _after_send: list[tuple[str, JsonObject]]
    ) -> JsonValue:
        ref = self._session_ref(params)
        return await self._access_call(ref, self._access.session_done, ref)

    async def notify(self, method: str, params: JsonObject) -> None:
        """Publish a notification to connected editor clients.

        Each send is time-bounded: a client that stopped reading (full pipe
        buffer) is dropped instead of blocking every later broadcast and the
        callers awaiting them.
        """
        message: JsonObject = {"jsonrpc": "2.0", "method": method, "params": params}
        writers = list(self._writers)
        logger.debug(
            "control notify → method=%s writers=%s %s",
            method,
            len(writers),
            _rpc_params_summary(params),
        )
        for writer in writers:
            try:
                await asyncio.wait_for(self._send(writer, message), timeout=NOTIFY_TIMEOUT_SECONDS)
            except (TimeoutError, ConnectionError, OSError):
                self._writers.discard(writer)
                self._writer_framing.pop(writer, None)
                writer.close()

    async def publish_session_changed(
        self, session_dir: Path, *, list_changed: bool = True
    ) -> None:
        """Notify editor clients that a session projection changed.

        :param list_changed: When false, catalog list fields are unchanged
            (an ``updates.jsonl`` append). Clients tail the open timeline
            and skip ``session/list`` / ``session/overview``.
        """
        session = Path(session_dir)
        await self.notify(
            NOTIFY_SESSION_CHANGED,
            {"sessionId": session.name, "listChanged": bool(list_changed)},
        )

    async def publish_session_selected(
        self,
        session_dir: Path,
        prompt_index: int | None,
    ) -> None:
        """Notify editor clients about the TUI's active session and prompt."""
        session = Path(session_dir)
        await self.notify(
            NOTIFY_SESSION_SELECTED,
            {"sessionId": session.name, "promptIndex": prompt_index},
        )

    async def publish_notes_changed(self, session_dir: Path) -> None:
        """Notify editor clients that canonical operator notes changed."""
        session = Path(session_dir)
        snapshot = await asyncio.to_thread(notes_snapshot, session)
        await self.notify(
            NOTIFY_NOTES_CHANGED,
            {"sessionId": session.name, "revision": snapshot.revision},
        )

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        request_id: JsonValue | None,
        code: int,
        message: str,
        data: JsonObject | None = None,
    ) -> None:
        error: JsonObject = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self._send(
            writer,
            {"jsonrpc": "2.0", "id": request_id, "error": error},
        )

    async def _send(self, writer: asyncio.StreamWriter, payload: JsonObject) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self._writer_framing.get(writer) == "headers":
            header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
            writer.write(header + encoded)
        else:
            writer.write(encoded + b"\n")
        try:
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, ConnectionError):
            # One-shot clients (HUD) often close after the first line; do not
            # escalate into an unhandled client_connected_cb exception.
            return
