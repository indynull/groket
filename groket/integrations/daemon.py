"""Headless control-plane owner: long-lived process serving the editor socket."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from ..models import JsonObject
from ..paths import default_work_dir, resolve_work_and_traces
from ..session.catalog import (
    SessionCatalogCache,
    catalog_scan_roots,
    resolve_session_reference,
)
from ..session.sources import session_dir_for_watch_path
from ..session.subagents import session_changed_targets
from .control import (
    ControlError,
    ControlServer,
    ControlSocketInUse,
    NotesChanged,
    OpenSession,
    default_socket_path,
    protocol_compatible,
)
from .control_client import ControlClient, is_transient_unix_connect_error
from .control_contract import NOTIFY_SESSION_CHANGED

logger = logging.getLogger(__name__)

# Background catalog refresh while the headless owner is alive (seconds).
CATALOG_WARM_INTERVAL = 15.0
# Coalesce live jsonl/signals writes on the serve loop. Still notifies
# session/changed so Emacs, Neovim, and the terminal list refresh the trace.
CONTROL_FS_DEBOUNCE_S = 3.0
# Observer-thread coalesce only. Catalog apply debounce is CONTROL_FS_DEBOUNCE_S.
_WATCH_PATH_COALESCE_S = 0.05


def configure_serve_logging() -> None:
    """Send ``groket.*`` logs to stderr (and thus the detached serve ``.log`` file).

    Level from ``GROKET_SERVE_LOG_LEVEL`` (default ``INFO``). Use ``DEBUG`` for
    full param-level RPC lines; ``INFO`` logs each method with timing and status.
    """
    level_name = (os.environ.get("GROKET_SERVE_LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, None)
    if not isinstance(level, int):
        level = logging.INFO
    root = logging.getLogger("groket")
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        handler.setLevel(level)
        root.addHandler(handler)
    # Detached serve: avoid duplicate lastResort noise on unconfigured root.
    logging.captureWarnings(True)


@dataclass(frozen=True)
class ControlDaemonStatus:
    """Snapshot of headless control ownership for ``serve status``."""

    socket_path: str
    socket_exists: bool
    pid: int | None
    pid_alive: bool
    live: bool
    pid_path: str
    #: Pid recorded in the advisory lock file (may differ from ``.pid``).
    lock_pid: int | None = None
    #: True when a process still holds the lock but the socket is not accepting
    #: (zombie owner — ``serve stop`` / restart should clear it).
    stale_lock: bool = False

    def as_mapping(self) -> JsonObject:
        """JSON-serializable mapping for ``--json`` output."""
        return {
            "socket_path": self.socket_path,
            "socket_exists": self.socket_exists,
            "pid": self.pid,
            "pid_alive": self.pid_alive,
            "live": self.live,
            "pid_path": self.pid_path,
            "lock_pid": self.lock_pid,
            "stale_lock": self.stale_lock,
        }


def control_pid_path(socket_path: Path) -> Path:
    """Return the PID file path paired with *socket_path*."""
    return Path(socket_path).expanduser().with_name(Path(socket_path).name + ".pid")


def control_lock_path(socket_path: Path) -> Path:
    """Return the exclusive ownership lock path next to *socket_path*."""
    return Path(socket_path).expanduser().with_name(Path(socket_path).name + ".lock")


def write_control_pid(socket_path: Path, pid: int | None = None) -> Path:
    """Write the owner PID next to the control socket.

    :param socket_path: Control Unix socket path.
    :param pid: Process id to record (default: current process).
    :returns: Path of the written PID file.
    """
    path = control_pid_path(socket_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid if pid is not None else os.getpid()}\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        logger.debug("could not chmod pid file %s", path, exc_info=True)
    return path


def read_control_pid(socket_path: Path) -> int | None:
    """Read the recorded owner PID, or None when missing/invalid."""
    path = control_pid_path(socket_path)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw.split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def remove_control_pid(socket_path: Path) -> None:
    """Remove the PID file if present (best-effort)."""
    try:
        control_pid_path(socket_path).unlink(missing_ok=True)
    except OSError:
        logger.debug("could not remove pid file for %s", socket_path, exc_info=True)


def read_control_lock_pid(socket_path: Path) -> int | None:
    """Read the owner pid written into the advisory lock file, if any."""
    path = control_lock_path(socket_path)
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return int(raw.split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def lock_holder_pids(socket_path: Path) -> list[int]:
    """Return live pids that appear to hold the control ownership lock.

    Prefer the pid recorded in the lock file (written on flock). Fall back to
    ``lsof`` when the file is empty (owners from builds that predate lock pids).
    """
    import shutil
    import subprocess

    found: list[int] = []
    seen: set[int] = set()
    me = os.getpid()

    def _add(pid: int | None) -> None:
        if pid is None or pid <= 0 or pid == me or pid in seen:
            return
        if not pid_is_alive(pid):
            return
        seen.add(pid)
        found.append(pid)

    _add(read_control_lock_pid(socket_path))
    lock_path = control_lock_path(socket_path)
    if not found and lock_path.is_file() and shutil.which("lsof"):
        try:
            proc = subprocess.run(
                ["lsof", "-t", str(lock_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=2.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None:
            for line in (proc.stdout or "").split():
                try:
                    _add(int(line.strip()))
                except ValueError:
                    continue
    return found


def pid_is_alive(pid: int) -> bool:
    """True when *pid* refers to a running (non-zombie) process (POSIX)."""
    import subprocess

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    # ``kill(0)`` succeeds on zombies; treat Z as not alive for stop/status.
    try:
        proc = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    stat = (proc.stdout or "").strip()
    if not stat:
        return False
    # macOS/BSD: leading Z; Linux: contains Z in the state field.
    return "Z" not in stat.upper()


def build_domain_control_server(
    *,
    socket_path: Path,
    work_dir: Path,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
    open_session: OpenSession | None = None,
    notes_changed: NotesChanged | None = None,
) -> ControlServer:
    """Build a :class:`ControlServer` with domain catalog handlers (no TUI).

    :param socket_path: Unix socket path to own.
    :param work_dir: Work root for session discovery.
    :param traces_path: Optional traces path override.
    :param include_host: Host inclusion for ``session/list`` (True/False force;
        None = re-read ``show_host_sessions`` from config on each call so
        editor clients match the TUI ``H`` pref without restarting serve).
    :param host_root: Host root override for tests.
    :param open_session: Optional async open callback (TUI only).
    :param notes_changed: Optional notes-changed callback.
    :returns: Configured but not-yet-started server.
    """
    wd = Path(work_dir).expanduser()
    tr = Path(traces_path).expanduser() if traces_path is not None else None
    catalog_cache = SessionCatalogCache(
        wd,
        traces_path=tr,
        include_host=include_host,
        host_root=host_root,
    )

    def resolve_session(reference: str) -> Path | None:
        found = catalog_cache.resolve(reference)
        if found is not None:
            return found
        return resolve_session_reference(
            reference,
            wd,
            traces_path=tr,
            include_host=include_host,
            host_root=host_root,
        )

    server = ControlServer(
        socket_path=socket_path,
        resolve_session=resolve_session,
        list_sessions=catalog_cache,
        open_session=open_session,
        notes_changed=notes_changed,
        work_dir=wd,
        analysis_traces=tr,
    )
    server._catalog_cache = catalog_cache  # type: ignore[attr-defined]
    return server


async def _catalog_warm_loop(
    cache: SessionCatalogCache,
    *,
    interval: float = CATALOG_WARM_INTERVAL,
) -> None:
    """Background refresh: keep session/list warm while the owner is alive."""
    try:
        rows = await asyncio.to_thread(lambda: cache.get(force=True))
        logger.info("control catalog warm complete rows=%s", len(rows))
    except Exception:
        logger.debug("control catalog warm failed", exc_info=True)
    while True:
        try:
            await asyncio.sleep(max(5.0, float(interval)))

            def _refresh() -> None:
                cache.get()
                cache.drop_subagent_rows()

            await asyncio.to_thread(_refresh)
            logger.debug("control catalog refresh complete")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("control catalog refresh failed", exc_info=True)


def control_watch_roots(cache: SessionCatalogCache) -> list[Path]:
    """Directories the owner watches for catalog / ``session/changed`` events.

    Follows the same scan roots as ``session/list`` (work traces plus host
    when that catalog is included).
    """
    roots = catalog_scan_roots(
        cache._work_dir,
        traces_path=cache._traces_path,
        include_host=cache._include_host,
        host_root=cache._host_root,
    )
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        path = Path(root.path).expanduser()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            out.append(path)
    return out


# Catalog list rows only care about these names (and notes). Workspace,
# images, compaction, and events.jsonl must not rebuild the list.
_CATALOG_LIST_FILE_NAMES = frozenset(
    {
        "summary.json",
        "signals.json",
        "updates.jsonl",
        "chat_history.jsonl",
        "operator_notes.toml",
        "status.json",
        "groket-interrupted.json",
    }
)
_CATALOG_NOISE_DIR_NAMES = frozenset(
    {
        "workspace",
        "images",
        "compaction",
        "attachments",
    }
)


def _catalog_ignore_watch_path(path: Path) -> bool:
    """True for workspace / image / compaction trees (not list or timeline)."""
    return any(part.casefold() in _CATALOG_NOISE_DIR_NAMES for part in path.parts)


def _catalog_list_rebuild_path(path: Path) -> bool:
    """True when a watch path can change a painted session/list field."""
    if _catalog_ignore_watch_path(path) or path.name == "events.jsonl":
        return False
    if path.is_dir() or not path.name:
        return True
    return path.name in _CATALOG_LIST_FILE_NAMES


def apply_fs_catalog_events(
    cache: SessionCatalogCache,
    paths: list[str],
    roots: list[Path],
) -> tuple[list[Path], list[Path], dict[str, bool]]:
    """Patch dirty catalog rows after a coalesced filesystem watch fire.

    :param cache: Warm session catalog.
    :param paths: Absolute paths from the watch callback.
    :param roots: Catalog roots being watched.
    :returns: ``(changed_sessions, notes_sessions, list_changed)``.
    """
    sessions = _session_dirs_from_event_paths(paths, roots=roots)
    notes_sessions = [
        s
        for s in sessions
        if any(Path(p).name == "operator_notes.toml" for p in paths)
        or any("operator_notes.toml" in p for p in paths)
    ]
    list_changed: dict[str, bool] = {}
    list_sessions = _session_dirs_from_event_paths(
        [p for p in paths if _catalog_list_rebuild_path(Path(p))],
        roots=roots,
    )
    if list_sessions:
        try:
            _rows, list_changed = cache.refresh_rows(list_sessions)
        except Exception:
            logger.debug("catalog row refresh after FS event failed", exc_info=True)
            try:
                cache.get(force=True)
            except Exception:
                logger.debug("catalog force refresh after FS event failed", exc_info=True)
            # Fingerprint unknown after a failed incremental patch: clients
            # must treat every dirty session as a list change.
            list_changed = {session.name: True for session in list_sessions}
    for session in sessions:
        list_changed.setdefault(session.name, False)
    return sessions, notes_sessions, list_changed


class _CatalogWatchApply:
    """Coalesce watch paths on the serve loop; run catalog disk I/O off it."""

    def __init__(
        self,
        *,
        server: ControlServer,
        cache: SessionCatalogCache | None,
        roots: list[Path],
        loop: asyncio.AbstractEventLoop,
        debounce_s: float,
    ) -> None:
        self._server = server
        self._cache = cache
        self._roots = roots
        self._loop = loop
        self._debounce_s = max(0.0, float(debounce_s))
        self._pending: set[str] = set()
        self._handle: asyncio.TimerHandle | None = None
        self._task: asyncio.Task[None] | None = None

    def enqueue(self, paths: list[str]) -> None:
        """Watch-thread entry: marshal paths onto the serve loop."""
        if not paths:
            return
        self._loop.call_soon_threadsafe(self._accept, tuple(paths))

    def close(self) -> None:
        """Cancel a pending debounce and in-flight apply."""
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        if self._task is not None:
            self._task.cancel()

    def _accept(self, paths: tuple[str, ...]) -> None:
        self._pending.update(paths)
        if self._handle is not None:
            self._handle.cancel()
        if self._debounce_s <= 0:
            self._kick()
            return
        self._handle = self._loop.call_later(self._debounce_s, self._kick)

    def _kick(self) -> None:
        self._handle = None
        if self._task is not None and not self._task.done():
            return
        self._task = self._loop.create_task(self._run(), name="control-fs-apply")

    async def _run(self) -> None:
        try:
            while self._pending:
                paths = sorted(self._pending)
                self._pending.clear()
                sessions, notes, list_changed = await asyncio.to_thread(self._apply_disk, paths)
                await self._publish(sessions, notes, list_changed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("control FS apply failed", exc_info=True)
        if self._pending:
            self._kick()

    def _apply_disk(self, paths: list[str]) -> tuple[list[Path], list[Path], dict[str, bool]]:
        if isinstance(self._cache, SessionCatalogCache):
            return apply_fs_catalog_events(self._cache, paths, self._roots)
        sessions = _session_dirs_from_event_paths(paths, roots=self._roots)
        notes = [
            session
            for session in sessions
            if any(Path(p).name == "operator_notes.toml" for p in paths)
            or any("operator_notes.toml" in p for p in paths)
        ]
        return sessions, notes, {}

    async def _publish(
        self,
        sessions: list[Path],
        notes_sessions: list[Path],
        list_changed: dict[str, bool],
    ) -> None:
        seen: set[str] = set()
        for session in sessions:
            for target in session_changed_targets(session):
                key = str(target)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    await self._server.publish_session_changed(
                        target,
                        list_changed=bool(list_changed.get(target.name, True)),
                    )
                except Exception:
                    logger.debug("publish session/changed failed", exc_info=True)
        for session in notes_sessions:
            try:
                await self._server.publish_notes_changed(session)
            except Exception:
                logger.debug("publish notes/changed failed", exc_info=True)


def _session_dirs_from_event_paths(
    paths: list[str],
    *,
    roots: list[Path],
) -> list[Path]:
    """Map FS event paths to session directories under *roots*."""
    found: dict[str, Path] = {}
    root_resolved: list[Path] = []
    for root in roots:
        try:
            root_resolved.append(Path(root).expanduser().resolve())
        except OSError:
            root_resolved.append(Path(root).expanduser())
    for raw in paths:
        try:
            p = Path(raw).expanduser().resolve()
        except OSError:
            p = Path(raw).expanduser()
        if _catalog_ignore_watch_path(p):
            continue
        for root in root_resolved:
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if not rel.parts:
                continue
            session = session_dir_for_watch_path(p, root)
            if session is not None:
                found[str(session)] = session
            break
    return list(found.values())


async def serve_control_forever(
    server: ControlServer,
    *,
    write_pid: bool = True,
    warm_interval: float = CATALOG_WARM_INTERVAL,
) -> None:
    """Start *server*, optionally write a PID file, and serve until cancelled.

    :param server: Control server instance.
    :param write_pid: When true, write/remove the paired PID file.
    :param warm_interval: Seconds between background catalog rebuilds.
    :raises ControlSocketInUse: When another live owner holds the socket.
    """
    from ..fs_watch import TraceTreeWatch

    await server.start()
    if write_pid:
        write_control_pid(server.socket_path)
    warm_task: asyncio.Task[None] | None = None
    cache = getattr(server, "_catalog_cache", None)
    if isinstance(cache, SessionCatalogCache):
        warm_task = asyncio.create_task(
            _catalog_warm_loop(cache, interval=warm_interval),
            name="control-catalog-warm",
        )
    elif server._list_sessions is not None:
        lister = server._list_sessions

        def _warm() -> None:
            try:
                lister()
            except Exception:
                logger.debug("catalog warm failed", exc_info=True)

        asyncio.create_task(asyncio.to_thread(_warm))

    # FS watch: incremental catalog row refresh + session/changed.
    watches: list[TraceTreeWatch] = []
    loop = asyncio.get_running_loop()
    uniq_roots: list[Path] = []
    if isinstance(cache, SessionCatalogCache):
        uniq_roots = control_watch_roots(cache)

        def _catalog_ready() -> None:
            async def _pub() -> None:
                try:
                    await server.notify(NOTIFY_SESSION_CHANGED, {"sessionId": ""})
                except Exception:
                    logger.debug("publish catalog ready failed", exc_info=True)

            asyncio.run_coroutine_threadsafe(_pub(), loop)

        cache._on_rebuilt = _catalog_ready

    apply = _CatalogWatchApply(
        server=server,
        cache=cache if isinstance(cache, SessionCatalogCache) else None,
        roots=uniq_roots,
        loop=loop,
        debounce_s=CONTROL_FS_DEBOUNCE_S,
    )
    for root in uniq_roots:
        watch = TraceTreeWatch(
            root,
            on_change=lambda: None,
            debounce_s=_WATCH_PATH_COALESCE_S,
            on_paths=apply.enqueue,
        )
        if watch.start():
            watches.append(watch)
            logger.info("control FS watch on %s", root)

    try:
        assert server._server is not None
        await server._server.serve_forever()
    finally:
        apply.close()
        for watch in watches:
            with suppress(Exception):
                watch.stop()
        if warm_task is not None:
            warm_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await warm_task
        await server.close()
        if write_pid:
            remove_control_pid(server.socket_path)


def run_control_daemon(
    *,
    socket_path: Path | None = None,
    work_dir: Path | None = None,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    host_root: Path | None = None,
) -> int:
    """Blocking entry: own the control socket with domain handlers until signal.

    :param socket_path: Socket path (default: :func:`default_socket_path`).
    :param work_dir: Work root; resolved with *traces_path* when omitted.
    :param traces_path: Optional traces path (also used to derive work root).
    :param include_host: Host inclusion (True/False force; None = config pref).
    :param host_root: Host root override for tests.
    :returns: Process exit code (0 clean stop, 1 ownership conflict / error).
    """
    sock = Path(socket_path or default_socket_path()).expanduser()
    if work_dir is not None:
        wd = Path(work_dir).expanduser().resolve()
        tr = (
            Path(traces_path).expanduser().resolve()
            if traces_path is not None
            else wd / "runs" / "traces"
        )
    else:
        wd, tr = resolve_work_and_traces(traces_path)

    configure_serve_logging()

    server = build_domain_control_server(
        socket_path=sock,
        work_dir=wd,
        traces_path=tr,
        include_host=include_host,
        host_root=host_root,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows / restricted environments: rely on KeyboardInterrupt.
            signal.signal(sig, lambda *_a: _request_stop())

    async def _run() -> None:
        task = asyncio.create_task(serve_control_forever(server, write_pid=True))
        stopper = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            {task, stopper},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for item in pending:
            item.cancel()
        if task in done:
            exc = task.exception()
            if exc is not None:
                raise exc
        else:
            await server.close()
            remove_control_pid(server.socket_path)
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    try:
        logger.info(
            "groket serve: control socket %s work_dir=%s traces=%s pid=%s log_level=%s",
            sock,
            wd,
            tr,
            os.getpid(),
            logging.getLevelName(logging.getLogger("groket").level),
        )
        # Operator-facing banner on stderr (CLI entry only uses this process path).
        sys.stderr.write(f"groket serve: control socket {sock}\n")
        sys.stderr.write(f"  work_dir={wd}\n")
        sys.stderr.write(f"  traces={tr}\n")
        sys.stderr.write(f"  pid={os.getpid()}\n")
        sys.stderr.write(
            f"  log_level={logging.getLevelName(logging.getLogger('groket').level)} "
            f"(GROKET_SERVE_LOG_LEVEL)\n"
        )
        sys.stderr.flush()
        loop.run_until_complete(_run())
        return 0
    except ControlSocketInUse as exc:
        sys.stderr.write(f"error: control socket already in use: {exc.socket_path}\n")
        sys.stderr.flush()
        return 1
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        logger.exception("control daemon failed")
        sys.stderr.write(f"error: {exc}\n")
        sys.stderr.flush()
        return 1
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


def control_socket_accepts(socket_path: Path, *, timeout: float = 0.5) -> bool:
    """True when *socket_path* accepts a Unix connection (live owner).

    Synchronous probe for CLI status/stop. Never unlinks the path.
    Retries transient connect errors (macOS EAGAIN / refused) briefly so a
    single flaky probe does not report a live owner as dead.
    """
    import time

    path = Path(socket_path).expanduser()
    if not path.exists():
        return False
    # Keep total wait small: callers often poll (wait_until_control_accepts).
    budget = min(1.0, max(0.15, timeout * 2))
    deadline = time.monotonic() + budget
    delay = 0.02
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(str(path))
            return True
        except TimeoutError:
            return False
        except OSError as exc:
            if not is_transient_unix_connect_error(exc) or time.monotonic() >= deadline:
                return False
            time.sleep(delay)
            delay = min(delay * 2, 0.15)


def control_daemon_status(socket_path: Path | None = None) -> ControlDaemonStatus:
    """Return a status snapshot for the headless control owner.

    ``live`` is true only when a recorded daemon pid is alive, or (without a
    pid file) a connect probe shows a process still accepting on the socket.
    Path existence alone is not enough. ``stale_lock`` means a process still
    holds the ownership flock but nothing is accepting — start will fail
    until stop clears the holder.
    """
    sock = Path(socket_path or default_socket_path()).expanduser()
    pid = read_control_pid(sock)
    path_exists = sock.exists()
    alive = pid_is_alive(pid) if pid is not None else False
    accepts = control_socket_accepts(sock) if path_exists else False
    if alive:
        live = True
    elif pid is not None:
        # Stale pid file: only "live" if something still answers on the socket.
        live = accepts
    else:
        # No pid (external owner without pid file): live when socket accepts.
        live = accepts
    holders = lock_holder_pids(sock) if not live else []
    lock_pid = read_control_lock_pid(sock)
    if lock_pid is None and holders:
        lock_pid = holders[0]
    stale_lock = (not live) and bool(holders)
    return ControlDaemonStatus(
        socket_path=str(sock),
        socket_exists=path_exists,
        pid=pid,
        pid_alive=alive,
        live=live,
        pid_path=str(control_pid_path(sock)),
        lock_pid=lock_pid,
        stale_lock=stale_lock,
    )


def _unlink_stale_socket_only(sock: Path) -> bool:
    """Remove *sock* only when it does not accept connections.

    :returns: True when the path was unlinked (or already missing).
    """
    if not sock.exists():
        return True
    if control_socket_accepts(sock):
        return False
    try:
        sock.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _signal_control_pid(pid: int, sig: int) -> None:
    """Signal *pid* (process group first when it is the session leader).

    Detached owners use ``start_new_session`` so ``killpg(pid)`` works.
    Foreground ``serve`` is not a session leader: ``killpg`` returns ESRCH
    even while the process is alive — fall through to ``kill(pid)``.
    """
    try:
        os.killpg(pid, sig)
        return
    except OSError:
        pass
    os.kill(pid, sig)


def _wait_pids_gone(pids: list[int], *, timeout: float) -> bool:
    """Wait until none of *pids* are alive (or *timeout*)."""
    import time

    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if not any(pid_is_alive(p) for p in pids):
            return True
        time.sleep(0.05)
    return not any(pid_is_alive(p) for p in pids)


def _stop_pids(pids: list[int], *, timeout: float, label: str) -> int:
    """SIGTERM then SIGKILL *pids*. Return 0 when all have exited."""
    if not pids:
        return 1
    for pid in pids:
        try:
            _signal_control_pid(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except OSError as exc:
            sys.stderr.write(f"error: could not signal pid {pid}: {exc}\n")
            sys.stderr.flush()
            return 1
    if _wait_pids_gone(pids, timeout=timeout):
        sys.stderr.write(f"stopped {label} pid={','.join(str(p) for p in pids)}\n")
        sys.stderr.flush()
        return 0
    for pid in pids:
        try:
            _signal_control_pid(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    if _wait_pids_gone(pids, timeout=min(2.0, max(0.2, timeout))):
        sys.stderr.write(f"stopped {label} pid={','.join(str(p) for p in pids)} (SIGKILL)\n")
        sys.stderr.flush()
        return 0
    sys.stderr.write(
        f"error: pid {','.join(str(p) for p in pids)} did not exit within {timeout}s\n"
    )
    sys.stderr.flush()
    return 1


def stop_control_daemon(
    socket_path: Path | None = None,
    *,
    timeout: float = 5.0,
) -> int:
    """Signal the headless owner to stop (SIGTERM) and wait briefly.

    Only unlinks the socket path when a connect probe shows it is dead.
    Never removes a path that still accepts clients (TUI or other owners
    without a pid file).

    When the socket is not accepting but a process still holds the ownership
    lock (zombie owner — missing ``.pid`` / socket), that holder is signalled
    so a subsequent ``serve -d`` can bind.

    :param socket_path: Control socket path.
    :param timeout: Seconds to wait for the process to exit.
    :returns: 0 on success or already stopped, 1 when kill failed / live
        non-daemon owner.
    """
    sock = Path(socket_path or default_socket_path()).expanduser()
    pid = read_control_pid(sock)
    accepts = control_socket_accepts(sock)

    if accepts and (pid is None or not pid_is_alive(pid)):
        # Live owner without a manageable daemon pid. Do not unlink the
        # public path; leave editors connected.
        if pid is None:
            sys.stderr.write(
                "error: control socket is live but no daemon pid file "
                "(owner is not a groket serve process; not stopping)\n"
            )
        else:
            sys.stderr.write(
                f"error: pid {pid} is not running but socket still accepts "
                "connections; not unlinking live socket\n"
            )
            remove_control_pid(sock)
        sys.stderr.flush()
        return 1

    targets: list[int] = []
    if pid is not None and pid_is_alive(pid):
        targets = [pid]
    elif not accepts:
        # Zombie / crashed owner: flock held, no accepting socket.
        targets = lock_holder_pids(sock)

    if targets:
        code = _stop_pids(targets, timeout=timeout, label="control daemon")
        if not control_socket_accepts(sock):
            _unlink_stale_socket_only(sock)
            remove_control_pid(sock)
        return code

    # Nothing alive to signal — clear dead leftovers.
    _unlink_stale_socket_only(sock)
    remove_control_pid(sock)
    sys.stderr.write(f"already stopped  socket={sock}\n")
    sys.stderr.flush()
    return 0


def resolve_daemon_work(
    path: Path | None,
) -> tuple[Path, Path]:
    """Resolve work/traces for serve CLI (same rules as TUI)."""
    if path is None:
        wd = default_work_dir()
        try:
            wd = wd.resolve()
        except OSError:
            pass
        return wd, wd / "runs" / "traces"
    return resolve_work_and_traces(path)


def control_log_path(socket_path: Path) -> Path:
    """Stderr/stdout log for a detached control owner (next to the socket)."""
    return Path(socket_path).expanduser().with_name(Path(socket_path).name + ".log")


def wait_until_control_accepts(
    socket_path: Path,
    *,
    timeout: float = 10.0,
    interval: float = 0.05,
) -> bool:
    """Poll until the socket accepts connections or *timeout* elapses."""
    import time

    sock = Path(socket_path).expanduser()
    deadline = time.monotonic() + max(0.1, timeout)
    while time.monotonic() < deadline:
        if control_socket_accepts(sock):
            return True
        time.sleep(max(0.01, interval))
    return control_socket_accepts(sock)


@dataclass(frozen=True)
class EnsureDaemonResult:
    """Outcome of ensuring a detached control owner is running."""

    ok: bool
    already_running: bool
    spawned: bool
    pid: int | None
    socket_path: Path
    error: str = ""

    @property
    def live(self) -> bool:
        return self.ok and control_socket_accepts(self.socket_path)


def _detached_child_argv(
    *,
    socket_path: Path,
    work_dir: Path | None,
    traces_path: Path | None,
    include_host: bool | None,
) -> list[str]:
    """Build argv for a foreground child that owns the control socket.

    Uses the same interpreter and an inline entry so tests and editable
    installs do not require ``groket`` on ``PATH``.
    """
    sock = str(Path(socket_path).expanduser())
    parts = [
        "from pathlib import Path",
        "from groket.integrations.daemon import run_control_daemon",
        f"sock = Path({sock!r})",
    ]
    if work_dir is not None:
        parts.append(f"wd = Path({str(Path(work_dir).expanduser())!r})")
    else:
        parts.append("wd = None")
    if traces_path is not None:
        parts.append(f"tr = Path({str(Path(traces_path).expanduser())!r})")
    else:
        parts.append("tr = None")
    # None → follow show_host_sessions in config on each session/list.
    host_lit = "None" if include_host is None else repr(bool(include_host))
    parts.append(
        f"raise SystemExit(run_control_daemon("
        f"socket_path=sock, work_dir=wd, traces_path=tr, "
        f"include_host={host_lit}))"
    )
    return [sys.executable, "-c", "; ".join(parts)]


def start_control_daemon_detached(
    *,
    socket_path: Path | None = None,
    work_dir: Path | None = None,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    timeout: float = 10.0,
) -> EnsureDaemonResult:
    """Start a background control owner and wait until the socket accepts.

    Like ``gpg-agent --daemon`` / ``redis-server --daemonize``: the caller
    returns after the service is ready (or fails). The child is session-led
    (``start_new_session``) so it outlives the parent shell/TUI.

    :param socket_path: Control socket path.
    :param work_dir: Work root for catalog discovery.
    :param traces_path: Traces path (also used to resolve work when *work_dir* omitted).
    :param include_host: Host inclusion (True/False force; None = config pref).
    :param timeout: Seconds to wait for the socket to accept.
    :returns: Structured result; ``ok`` when the socket accepts after return.
    """
    import subprocess
    import time

    sock = Path(socket_path or default_socket_path()).expanduser()
    if control_socket_accepts(sock):
        return EnsureDaemonResult(
            ok=True,
            already_running=True,
            spawned=False,
            pid=read_control_pid(sock),
            socket_path=sock,
        )

    # Zombie owner holds the flock but is not accepting — clear before spawn.
    if lock_holder_pids(sock):
        stop_control_daemon(sock, timeout=min(5.0, timeout))
        if control_socket_accepts(sock):
            return EnsureDaemonResult(
                ok=True,
                already_running=True,
                spawned=False,
                pid=read_control_pid(sock),
                socket_path=sock,
            )

    if work_dir is None and traces_path is None:
        wd, tr = resolve_daemon_work(None)
    elif work_dir is not None:
        wd = Path(work_dir).expanduser()
        tr = Path(traces_path).expanduser() if traces_path is not None else wd / "runs" / "traces"
    else:
        wd, tr = resolve_work_and_traces(traces_path)

    sock.parent.mkdir(parents=True, exist_ok=True)
    log_path = control_log_path(sock)
    argv = _detached_child_argv(
        socket_path=sock,
        work_dir=wd,
        traces_path=tr,
        include_host=include_host,
    )
    try:
        log_file = log_path.open("a", encoding="utf-8")
    except OSError as exc:
        return EnsureDaemonResult(
            ok=False,
            already_running=False,
            spawned=False,
            pid=None,
            socket_path=sock,
            error=f"could not open log {log_path}: {exc}",
        )
    try:
        log_file.write(
            f"\n--- groket serve --daemon spawn pid_parent={os.getpid()} "
            f"at {time.strftime('%Y-%m-%dT%H:%M:%S')} ---\n"
        )
        log_file.flush()
        # start_new_session → setsid: child not killed when parent TUI exits.
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        log_file.close()
        return EnsureDaemonResult(
            ok=False,
            already_running=False,
            spawned=False,
            pid=None,
            socket_path=sock,
            error=f"spawn failed: {exc}",
        )
    finally:
        # Parent no longer needs the fd; child keeps its dup.
        try:
            log_file.close()
        except OSError:
            pass

    if not wait_until_control_accepts(sock, timeout=timeout):
        # Another process may have raced us (TUI owner, prior serve, or a
        # child that lost the bind). If anything accepts, treat as success —
        # callers are clients and only need a live socket.
        if control_socket_accepts(sock):
            return EnsureDaemonResult(
                ok=True,
                already_running=True,
                spawned=True,
                pid=read_control_pid(sock) or proc.pid,
                socket_path=sock,
            )
        # Child may have exited with "already in use" while the winner is
        # still binding; brief re-probe before failing.
        if wait_until_control_accepts(sock, timeout=min(2.0, timeout)):
            return EnsureDaemonResult(
                ok=True,
                already_running=True,
                spawned=True,
                pid=read_control_pid(sock) or proc.pid,
                socket_path=sock,
            )
        # Child failed to come up — best-effort terminate.
        if proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGTERM)
            except OSError:
                pass
        child_rc = proc.poll()
        err = f"control socket did not accept within {timeout}s"
        if child_rc is not None:
            err = f"{err} (spawned pid={proc.pid} exited {child_rc})"
        if log_path.is_file():
            try:
                tail = log_path.read_text(encoding="utf-8")[-800:]
                if tail.strip():
                    # Surface the common case clearly for operators.
                    if "already in use" in tail:
                        err = (
                            f"control socket {sock} is held by another process "
                            f"that is not accepting connections (stale owner?). "
                            f"Run: groket serve stop  then  groket serve -d"
                        )
                    err = f"{err}\nlog tail:\n{tail}"
            except OSError:
                pass
        return EnsureDaemonResult(
            ok=False,
            already_running=False,
            spawned=True,
            pid=proc.pid,
            socket_path=sock,
            error=err,
        )

    return EnsureDaemonResult(
        ok=True,
        already_running=False,
        spawned=True,
        pid=read_control_pid(sock) or proc.pid,
        socket_path=sock,
    )


def owner_protocol_probe(socket_path: Path, *, timeout: float = 2.0) -> bool | None:
    """Whether the live owner speaks this client's protocol.

    :returns: ``True`` when ``protocolVersion`` shares this client's major,
        ``False`` when initialize succeeded on another major or rejected this
        client as unsupported, ``None`` when the probe failed (do not replace).
    """

    async def _probe() -> bool:
        client = ControlClient(socket_path, client_name="groket-protocol-probe", timeout=timeout)
        result = await client.initialize()
        return protocol_compatible(result.get("protocolVersion"))

    try:
        return bool(asyncio.run(_probe()))
    except ControlError as exc:
        msg = (exc.message or "").lower()
        if exc.code == -32602 and "protocol" in msg:
            return False
        return None
    except Exception:
        return None


def owner_protocol_current(socket_path: Path, *, timeout: float = 2.0) -> bool:
    """True when the live owner shares this client's protocol major."""
    return owner_protocol_probe(socket_path, timeout=timeout) is True


def ensure_control_daemon(
    *,
    socket_path: Path | None = None,
    work_dir: Path | None = None,
    traces_path: Path | None = None,
    include_host: bool | None = None,
    timeout: float = 10.0,
) -> EnsureDaemonResult:
    """Ensure a live control owner exists (attach if up, else detach-start).

    Shared by ``groket serve -d`` and TUI/HUD auto-start. If the socket already
    accepts but ``initialize`` reports a different protocol major (or rejects
    this client), stop that owner and start a current one. Same major keeps
    the live owner. A failed probe does not stop an accepting owner.
    """
    sock = Path(socket_path or default_socket_path()).expanduser()
    if control_socket_accepts(sock):
        probe = owner_protocol_probe(sock)
        if probe is not False:
            return EnsureDaemonResult(
                ok=True,
                already_running=True,
                spawned=False,
                pid=read_control_pid(sock),
                socket_path=sock,
            )
        stop_control_daemon(sock, timeout=min(5.0, timeout))
        if control_socket_accepts(sock):
            return EnsureDaemonResult(
                ok=False,
                already_running=True,
                spawned=False,
                pid=read_control_pid(sock),
                socket_path=sock,
                error="stale control owner could not be replaced",
            )
    return start_control_daemon_detached(
        socket_path=sock,
        work_dir=work_dir,
        traces_path=traces_path,
        include_host=include_host,
        timeout=timeout,
    )


__all__ = [
    "ControlDaemonStatus",
    "EnsureDaemonResult",
    "build_domain_control_server",
    "configure_serve_logging",
    "control_daemon_status",
    "control_lock_path",
    "control_log_path",
    "control_pid_path",
    "control_socket_accepts",
    "ensure_control_daemon",
    "owner_protocol_current",
    "owner_protocol_probe",
    "lock_holder_pids",
    "pid_is_alive",
    "read_control_lock_pid",
    "read_control_pid",
    "remove_control_pid",
    "resolve_daemon_work",
    "run_control_daemon",
    "serve_control_forever",
    "start_control_daemon_detached",
    "stop_control_daemon",
    "wait_until_control_accepts",
    "write_control_pid",
]
