"""Headless control daemon: ownership, domain handlers, CLI serve path."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from importlib import import_module
from pathlib import Path

import pytest
from async_wait import wait_until, wait_until_sync


def _short_sock(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="groket-daemon-"))
    return root / name


def _write_session(traces: Path, name: str) -> Path:
    session_dir = traces / name
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": "Daemon session"}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "review"},
                        "_meta": {"promptIndex": 3},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return session_dir


@pytest.mark.asyncio
async def test_domain_control_server_list_render_notes(tmp_path: Path) -> None:
    daemon = import_module("groket.integrations.daemon")
    client_mod = import_module("groket.integrations.control_client")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    session_dir = _write_session(traces, "session-daemon-1")
    sock = _short_sock("domain.sock")
    server = daemon.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
    )
    await server.start()
    try:
        getattr(server, "_catalog_cache").get(force=True)
        client = client_mod.ControlClient(sock, client_name="test-daemon")
        init = await client.initialize()
        assert (
            init["protocolVersion"] == import_module("groket.integrations.control").PROTOCOL_VERSION
        )
        assert "session/list" in init["capabilities"]
        assert "notes/upsert" in init["capabilities"]

        listed = await client.session_list()
        assert listed["matched"] >= 1
        ids = {row["sessionId"] for row in listed["sessions"]}
        assert "session-daemon-1" in ids
        paths = {row["path"] for row in listed["sessions"]}
        assert str(session_dir.resolve()) in paths

        rendered = await client.session_render(session_dir.name, format="markdown")
        assert rendered["format"] == "markdown"
        assert rendered["text"]
        assert "Prompt 3" in rendered["text"] or "prompt" in rendered["text"].lower()

        notes = await client.notes_list(session_dir.name)
        rev = notes["revision"]
        saved = await client.notes_upsert(
            session_dir.name,
            {
                "id": "n-daemon",
                "turnIndex": 0,
                "fields": {"summary": "Daemon note"},
                "eventIndices": [],
            },
            expected_revision=rev,
        )
        assert any(n["id"] == "n-daemon" for n in saved["notes"])
        again = await client.notes_list(session_dir.name)
        assert any(n["id"] == "n-daemon" for n in again["notes"])
        assert again["revision"] == saved["revision"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_second_domain_server_raises_in_use() -> None:
    daemon = import_module("groket.integrations.daemon")
    control = import_module("groket.integrations.control")
    sock = _short_sock("singleton-domain.sock")
    work = Path(tempfile.mkdtemp(prefix="groket-wd-"))
    first = daemon.build_domain_control_server(socket_path=sock, work_dir=work)
    second = daemon.build_domain_control_server(socket_path=sock, work_dir=work)
    await first.start()
    try:
        with pytest.raises(control.ControlSocketInUse):
            await second.start()
    finally:
        await first.close()


@pytest.mark.asyncio
async def test_serve_control_forever_writes_and_clears_pid(tmp_path: Path) -> None:
    daemon = import_module("groket.integrations.daemon")
    sock = _short_sock("pid.sock")
    work = tmp_path / "work"
    work.mkdir()
    server = daemon.build_domain_control_server(socket_path=sock, work_dir=work)
    task = asyncio.create_task(daemon.serve_control_forever(server, write_pid=True))
    try:
        await wait_until(
            lambda: sock.exists() and daemon.control_pid_path(sock).exists(),
            description="serve wrote socket and pid file",
        )
        assert sock.exists()
        pid = daemon.read_control_pid(sock)
        assert pid == os.getpid()
        assert daemon.pid_is_alive(pid)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await wait_until(
            lambda: not sock.exists() and not daemon.control_pid_path(sock).exists(),
            description="serve teardown removed socket and pid",
        )
    assert not sock.exists()
    assert not daemon.control_pid_path(sock).exists()


def test_control_daemon_status_and_stop_helpers(tmp_path: Path) -> None:
    daemon = import_module("groket.integrations.daemon")
    sock = tmp_path / "status.sock"
    status = daemon.control_daemon_status(sock)
    assert status.live is False
    assert status.pid is None
    assert status.as_mapping()["live"] is False
    # stop with no pid and no socket → already stopped (exit 0)
    code = daemon.stop_control_daemon(sock, timeout=0.5)
    assert code == 0


@pytest.mark.asyncio
async def test_stop_kills_zombie_lock_holder_without_pid_or_socket() -> None:
    """serve stop must clear a process that holds the lock after the socket died."""
    import subprocess
    import sys

    daemon = import_module("groket.integrations.daemon")
    control = import_module("groket.integrations.control")
    sock = _short_sock("zombie.sock")
    lock = daemon.control_lock_path(sock)
    # Child holds exclusive flock like ControlServer, then drops the socket path
    # (simulates crashed owner that still holds the lock file).
    child_src = f"""
import fcntl, os, time
from pathlib import Path
lock = Path({str(lock)!r})
lock.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
os.ftruncate(fd, 0)
os.write(fd, f"{{os.getpid()}}\\n".encode())
os.fsync(fd)
# no listen socket — only the lock remains
time.sleep(30)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", child_src],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_until_sync(
            lambda: daemon.read_control_lock_pid(sock) == proc.pid,
            description="stale lock child pid recorded",
        )
        assert daemon.read_control_lock_pid(sock) == proc.pid
        assert daemon.control_socket_accepts(sock) is False
        st = daemon.control_daemon_status(sock)
        assert st.live is False
        assert st.stale_lock is True
        assert st.lock_pid == proc.pid

        code = daemon.stop_control_daemon(sock, timeout=3.0)
        assert code == 0
        assert proc.poll() is not None
        # New owner can acquire the lock / bind.
        server = control.ControlServer(socket_path=sock)
        await server.start()
        try:
            assert sock.exists()
            assert daemon.control_socket_accepts(sock)
        finally:
            await server.close()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.asyncio
async def test_stop_does_not_unlink_live_socket_without_pid() -> None:
    """TUI-as-owner (no pid file): serve stop must not destroy the public path."""
    daemon = import_module("groket.integrations.daemon")
    control = import_module("groket.integrations.control")
    sock = _short_sock("tui-owner.sock")
    owner = control.ControlServer(socket_path=sock)
    await owner.start()
    try:
        assert sock.exists()
        assert daemon.control_socket_accepts(sock)
        status = daemon.control_daemon_status(sock)
        assert status.pid is None
        assert status.live is True
        assert status.socket_exists is True

        code = daemon.stop_control_daemon(sock, timeout=0.5)
        assert code == 1
        # Path still owned and reachable for editors.
        assert sock.exists()
        assert daemon.control_socket_accepts(sock)
        reader, writer = await asyncio.open_unix_connection(sock)
        writer.close()
        await writer.wait_closed()
        _ = reader
    finally:
        await owner.close()
    assert not sock.exists()


@pytest.mark.asyncio
async def test_stop_unlinks_only_dead_stale_socket_file() -> None:
    """Stale socket file (closed bind, no listen): stop may clean the path."""
    daemon = import_module("groket.integrations.daemon")
    sock = _short_sock("stale-file.sock")
    holder = __import__("socket").socket(
        __import__("socket").AF_UNIX, __import__("socket").SOCK_STREAM
    )
    holder.bind(str(sock))
    holder.close()  # leftover path, nothing accepts
    assert sock.exists()
    assert daemon.control_socket_accepts(sock) is False
    status = daemon.control_daemon_status(sock)
    assert status.live is False
    code = daemon.stop_control_daemon(sock, timeout=0.5)
    assert code == 0
    assert not sock.exists()


@pytest.mark.asyncio
async def test_status_live_requires_accept_not_mere_path() -> None:
    daemon = import_module("groket.integrations.daemon")
    sock = _short_sock("dead-path.sock")
    sock.write_text("", encoding="utf-8")  # file exists but is not a live AF_UNIX server
    status = daemon.control_daemon_status(sock)
    assert status.socket_exists is True
    assert status.pid is None
    assert status.live is False


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    # Prefer the project interpreter for subprocess CLI launches.
    return env


def _scratch() -> Path:
    """Writable log dir for subprocess CLI tests.

    Honors ``GROK_GOAL_SCRATCH`` when set to an existing-or-creatable path.
    Otherwise uses a fresh temp directory (not a Darwin-only ``/var/folders``
    default).
    """
    override = os.environ.get("GROK_GOAL_SCRATCH", "").strip()
    if override:
        root = Path(override)
        root.mkdir(parents=True, exist_ok=True)
        return root
    return Path(tempfile.mkdtemp(prefix="groket-daemon-scratch-"))


def test_cli_serve_owns_socket_and_second_fails(tmp_path: Path) -> None:
    """Real shipped CLI: first serve owns socket; second exits non-zero."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    session_dir = _write_session(traces, "session-cli-serve")
    sock = _short_sock("cli-serve.sock")
    scratch = _scratch()
    log1 = scratch / "serve-lifecycle.log"
    log2 = scratch / "daemon-second.log"

    # Console script entry (same as installed ``groket``).
    cmd_base = [
        sys.executable,
        "-c",
        "from groket.cli import main; main()",
        "serve",
        "-P",
        str(work),
        "-s",
        str(sock),
    ]
    with log1.open("w", encoding="utf-8") as out1:
        proc1 = subprocess.Popen(
            cmd_base,
            stdout=out1,
            stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=_cli_env(),
        )
    try:
        wait_until_sync(
            lambda: sock.exists() or proc1.poll() is not None,
            timeout=8.0,
            description="cli serve socket or early exit",
        )
        assert sock.exists(), log1.read_text(encoding="utf-8")
        assert proc1.poll() is None

        # RPC smoke against live CLI process
        rpc_log = scratch / "control-rpc.log"
        transcript: list[dict] = []

        async def _rpc_roundtrip() -> None:
            client_mod = import_module("groket.integrations.control_client")
            client = client_mod.ControlClient(sock, client_name="verify")
            init = await client.initialize()
            transcript.append({"initialize": init})
            listed: dict = {}

            async def _has_session() -> bool:
                nonlocal listed
                listed = await client.session_list()
                rows = listed.get("sessions")
                return isinstance(rows, list) and any(
                    isinstance(r, dict) and r.get("sessionId") == "session-cli-serve" for r in rows
                )

            await wait_until(
                _has_session,
                timeout=8.0,
                description="session-cli-serve in session/list",
            )
            transcript.append({"session/list": listed})
            rendered = await client.session_render(session_dir.name, format="org")
            transcript.append(
                {
                    "session/render": {
                        "keys": list(rendered.keys()),
                        "text_len": len(rendered.get("text") or ""),
                    }
                }
            )
            notes = await client.notes_list(session_dir.name)
            transcript.append({"notes/list": notes})
            saved = await client.notes_upsert(
                session_dir.name,
                {
                    "id": "n-cli",
                    "turnIndex": 0,
                    "fields": {"summary": "cli note"},
                    "eventIndices": [],
                },
                expected_revision=notes["revision"],
            )
            transcript.append({"notes/upsert": saved})
            listed2 = await client.notes_list(session_dir.name)
            transcript.append({"notes/list2": listed2})

        asyncio.run(_rpc_roundtrip())
        rpc_log.write_text(json.dumps(transcript, indent=2), encoding="utf-8")
        assert (
            transcript[0]["initialize"]["protocolVersion"]
            == import_module("groket.integrations.control").PROTOCOL_VERSION
        )
        assert any(
            row["sessionId"] == "session-cli-serve"
            for row in transcript[1]["session/list"]["sessions"]
        )
        assert transcript[2]["session/render"]["text_len"] > 0
        assert any(n["id"] == "n-cli" for n in transcript[5]["notes/list2"]["notes"])

        with log2.open("w", encoding="utf-8") as out2:
            proc2 = subprocess.run(
                cmd_base,
                stdout=out2,
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).resolve().parents[2]),
                env=_cli_env(),
                timeout=8,
                check=False,
            )
        assert proc2.returncode != 0
        second_text = log2.read_text(encoding="utf-8")
        assert "already" in second_text.lower() or "in use" in second_text.lower()
    finally:
        if proc1.poll() is None:
            proc1.send_signal(signal.SIGTERM)
            try:
                proc1.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc1.kill()
                proc1.wait(timeout=2)


@pytest.mark.asyncio
async def test_tui_attaches_as_client_to_daemon_owner(tmp_path: Path) -> None:
    daemon = import_module("groket.integrations.daemon")
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    session_dir = _write_session(traces, "session-attach")
    sock = _short_sock("attach.sock")
    owner = daemon.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
    )
    await owner.start()
    try:
        app = TraceEvalApp(
            work_dir=work,
            traces_path=traces,
            control_socket=sock,
            control_attach_only=True,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            for _ in range(200):
                await pilot.pause()
                if app.is_control_client():
                    break
            assert app.is_running
            assert app.is_control_client()
            assert not app.is_control_owner()
            assert sock.exists()
            # Catalog via control path (shipped client + owner handlers)
            listed = await app.control_session_list()
            assert listed["matched"] >= 1
            assert any(
                row.get("sessionId") == session_dir.name for row in listed.get("sessions", [])
            )
        # Owner still holds socket after TUI exit
        assert sock.exists()
    finally:
        await owner.close()


def test_stop_terminates_foreground_pid_file_owner(tmp_path: Path) -> None:
    """serve stop must kill a pid-file owner that is NOT a session leader.

    Foreground ``groket serve`` writes a pid file but does not call
    start_new_session; killpg(pid) raises ESRCH for that process. Stop must
    fall through to kill(pid) and actually terminate the owner.
    """
    daemon = import_module("groket.integrations.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = _short_sock("fg-stop.sock")
    scratch = _scratch()
    log = scratch / "serve-foreground-stop.log"
    # Child without start_new_session — same process model as foreground serve.
    argv = daemon._detached_child_argv(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
        include_host=False,
    )
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=False,
        close_fds=True,
    )
    lines = [f"spawned_pid={proc.pid}"]
    try:
        assert daemon.wait_until_control_accepts(sock, timeout=12.0), "socket never accepted"
        # Ensure pid file matches the non-session-leader child.
        written = daemon.read_control_pid(sock)
        lines.append(f"pid_file={written}")
        assert written == proc.pid
        assert daemon.pid_is_alive(proc.pid)
        assert daemon.control_socket_accepts(sock)

        code = daemon.stop_control_daemon(sock, timeout=8.0)
        lines.append(f"stop_code={code}")
        assert code == 0, "stop must succeed for foreground pid-file owner"
        wait_until_sync(
            lambda: not daemon.pid_is_alive(proc.pid) and not daemon.control_socket_accepts(sock),
            timeout=8.0,
            description="foreground owner stopped",
        )
        lines.append(
            f"alive_after={daemon.pid_is_alive(proc.pid)} "
            f"accepts_after={daemon.control_socket_accepts(sock)}"
        )
        assert not daemon.control_socket_accepts(sock)
        assert not daemon.pid_is_alive(proc.pid)
        # Process must not still be the live owner with a cleared pid file.
        assert daemon.read_control_pid(sock) is None
    finally:
        if proc.poll() is None:
            try:
                os.kill(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait(timeout=3)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_detached_start_status_stop_lifecycle(tmp_path: Path) -> None:
    """Detached start returns; status live; stop tears down only that owner."""
    daemon = import_module("groket.integrations.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_session(traces, "session-detach")
    sock = _short_sock("detach.sock")
    scratch = _scratch()
    log = scratch / "serve-detach.log"
    lines: list[str] = []

    result = daemon.start_control_daemon_detached(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
        timeout=12.0,
    )
    lines.append(f"detached_result={result}")
    try:
        assert result.ok, result.error
        assert result.spawned
        assert daemon.control_socket_accepts(sock)
        status = daemon.control_daemon_status(sock)
        lines.append(f"status={status.as_mapping()}")
        assert status.live
        assert status.pid_alive
        pid = status.pid
        assert pid is not None

        # Second detached start: already running (exit success path).
        again = daemon.ensure_control_daemon(
            socket_path=sock,
            work_dir=work,
            traces_path=traces,
        )
        lines.append(f"ensure_again={again}")
        assert again.ok
        assert again.already_running

        from unittest.mock import patch

        old_pid = again.pid
        with patch.object(daemon, "owner_protocol_probe", return_value=False):
            replaced = daemon.ensure_control_daemon(
                socket_path=sock,
                work_dir=work,
                traces_path=traces,
            )
        lines.append(f"ensure_replaced={replaced}")
        assert replaced.ok, replaced.error
        assert replaced.spawned
        assert replaced.pid != old_pid

        code = daemon.stop_control_daemon(sock, timeout=5.0)
        lines.append(f"stop_code={code}")
        assert code == 0
        wait_until_sync(
            lambda: not daemon.control_socket_accepts(sock),
            timeout=5.0,
            description="socket closed after stop",
        )
        status2 = daemon.control_daemon_status(sock)
        lines.append(f"status_after_stop={status2.as_mapping()}")
        assert not status2.live
        assert not daemon.control_socket_accepts(sock)
    finally:
        # Safety: kill if still up
        pid = daemon.read_control_pid(sock)
        if pid and daemon.pid_is_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cli_serve_daemon_flag(tmp_path: Path) -> None:
    """CLI ``serve -d`` detaches and status is live."""
    daemon = import_module("groket.integrations.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_session(traces, "session-cli-d")
    sock = _short_sock("cli-d.sock")
    scratch = _scratch()
    out_path = scratch / "serve-detach-cli.log"
    cmd = [
        sys.executable,
        "-c",
        "from groket.cli import main; main()",
        "serve",
        "-d",
        "-P",
        str(work),
        "-s",
        str(sock),
    ]
    with out_path.open("w", encoding="utf-8") as out:
        proc = subprocess.run(
            cmd,
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=_cli_env(),
            timeout=20,
            check=False,
        )
    try:
        assert proc.returncode == 0, out_path.read_text(encoding="utf-8")
        assert daemon.control_socket_accepts(sock)
        status_cmd = [
            sys.executable,
            "-c",
            "from groket.cli import main; main()",
            "serve",
            "status",
            "-s",
            str(sock),
        ]
        st = subprocess.run(
            status_cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=_cli_env(),
            timeout=10,
            check=False,
        )
        assert st.returncode == 0, st.stdout + st.stderr
        stop = subprocess.run(
            [
                sys.executable,
                "-c",
                "from groket.cli import main; main()",
                "serve",
                "stop",
                "-s",
                str(sock),
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=_cli_env(),
            timeout=10,
            check=False,
        )
        assert stop.returncode == 0, stop.stdout + stop.stderr
    finally:
        pid = daemon.read_control_pid(sock)
        if pid and daemon.pid_is_alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


@pytest.mark.asyncio
async def test_tui_autostart_attaches_and_leaves_daemon(tmp_path: Path) -> None:
    """Auto-start ensures detached owner; TUI is client; exit leaves owner live."""
    daemon = import_module("groket.integrations.daemon")
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    session_dir = _write_session(traces, "session-autostart")
    sock = _short_sock("autostart.sock")
    scratch = _scratch()
    log = scratch / "tui-autostart.log"
    lines: list[str] = []

    result = daemon.ensure_control_daemon(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
        timeout=12.0,
    )
    lines.append(f"ensure={result}")
    assert result.ok, result.error
    try:
        app = TraceEvalApp(
            work_dir=work,
            traces_path=traces,
            control_socket=sock,
            control_attach_only=True,
        )
        async with app.run_test(size=(100, 30)) as pilot:
            for _ in range(200):
                await pilot.pause()
                if app.is_control_client():
                    break
            lines.append(
                f"client={app.is_control_client()} owner={app.is_control_owner()} "
                f"running={app.is_running}"
            )
            assert app.is_control_client()
            assert not app.is_control_owner()
            listed = await app.control_session_list()
            lines.append(f"list={listed}")
            assert any(r.get("sessionId") == session_dir.name for r in listed.get("sessions", []))
        # After TUI exit, detached owner still accepts
        assert daemon.control_socket_accepts(sock)
        status = daemon.control_daemon_status(sock)
        lines.append(f"after_tui={status.as_mapping()}")
        assert status.live
    finally:
        code = daemon.stop_control_daemon(sock, timeout=5.0)
        lines.append(f"stop={code}")
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_launch_tui_ensure_serve_sets_attach_only(tmp_path: Path) -> None:
    """Shipped launch_tui with ensure_serve starts daemon and attaches."""
    from groket.cli import launch_tui
    from groket.integrations import daemon as daemon_mod

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = _short_sock("launch-auto.sock")
    captured: list[dict] = []

    class FakeApp:
        def __init__(self, **kw: object):
            captured.append(kw)

        def run(self) -> None:
            pass

    try:
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "groket.ui.app.TraceEvalApp", FakeApp
        ):
            launch_tui(
                path=work,
                config=None,
                socket=sock,
                ensure_serve=True,
            )
        assert captured
        assert captured[0]["control_attach_only"] is True
        assert captured[0]["control_socket"] == sock
        assert daemon_mod.control_socket_accepts(sock)
    finally:
        daemon_mod.stop_control_daemon(sock, timeout=5.0)


def test_launch_tui_no_serve_does_not_spawn(tmp_path: Path) -> None:
    from groket.cli import launch_tui
    from groket.integrations import daemon as daemon_mod

    work = tmp_path / "work"
    work.mkdir()
    sock = _short_sock("launch-noserve.sock")
    captured: list[dict] = []

    class FakeApp:
        def __init__(self, **kw: object):
            captured.append(kw)

        def run(self) -> None:
            pass

    with __import__("unittest.mock", fromlist=["patch"]).patch(
        "groket.ui.app.TraceEvalApp", FakeApp
    ):
        launch_tui(
            path=work,
            config=None,
            socket=sock,
            ensure_serve=False,
        )
    # No spawn; still attaches as client when a socket path is set.
    assert captured[0]["control_attach_only"] is True
    assert not sock.exists()
    assert not daemon_mod.control_socket_accepts(sock)


def test_domain_server_has_no_analysis_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serve must bind without an analysis service or worker pool."""
    daemon = import_module("groket.integrations.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_session(traces, "s1")
    sock = _short_sock("defer-analysis.sock")
    server = daemon.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
    )
    assert not hasattr(server, "_analysis_service")
    assert not hasattr(server, "_analysis_pool")
    assert server._work_dir == work


def test_ensure_does_not_stop_owner_when_protocol_probe_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed initialize must not SIGTERM an accepting owner."""
    daemon = import_module("groket.integrations.daemon")
    sock = tmp_path / "probe.sock"
    stopped: list[Path] = []

    monkeypatch.setattr(daemon, "control_socket_accepts", lambda _p: True)
    monkeypatch.setattr(daemon, "owner_protocol_probe", lambda _p, **_k: None)
    monkeypatch.setattr(daemon, "read_control_pid", lambda _p: 4242)
    monkeypatch.setattr(
        daemon,
        "stop_control_daemon",
        lambda p, **_k: stopped.append(p) or 0,
    )
    monkeypatch.setattr(
        daemon,
        "start_control_daemon_detached",
        lambda **_k: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )
    result = daemon.ensure_control_daemon(socket_path=sock)
    assert result.ok
    assert result.already_running
    assert not result.spawned
    assert stopped == []


@pytest.mark.asyncio
async def test_serve_watch_apply_runs_off_observer_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live watch fire must apply catalog rows on a worker, not Timer."""
    daemon = import_module("groket.integrations.daemon")
    client_mod = import_module("groket.integrations.control_client")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    session_dir = _write_session(traces, "watch-apply")
    sock = _short_sock("watch-apply.sock")
    apply_hits: list[dict[str, object]] = []
    orig_apply = daemon.apply_fs_catalog_events
    loop_thread = threading.current_thread()

    def _recording_apply(cache: object, paths: list[str], roots: list[Path]) -> object:
        apply_hits.append(
            {
                "thread": threading.current_thread().name,
                "ident": threading.current_thread().ident,
                "loop_ident": loop_thread.ident,
                "stack": "".join(traceback.format_stack()),
            }
        )
        time.sleep(0.25)
        return orig_apply(cache, paths, roots)

    monkeypatch.setattr(daemon, "apply_fs_catalog_events", _recording_apply)
    server = daemon.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
        include_host=False,
    )
    task = asyncio.create_task(
        daemon.serve_control_forever(server, write_pid=False, warm_interval=3600.0)
    )
    try:
        await wait_until(sock.exists, description="control socket accepts")
        client = client_mod.ControlClient(sock, client_name="watch-apply", timeout=15)
        await client.initialize()
        (session_dir / "updates.jsonl").write_text("{}\n{}\n", encoding="utf-8")
        await wait_until(
            lambda: bool(apply_hits),
            timeout=8.0,
            description="watch fire applied catalog events",
        )
        started = time.monotonic()
        listed = await client.session_list()
        elapsed = time.monotonic() - started
        assert listed["matched"] >= 1
        assert elapsed < 0.2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert apply_hits
    stack = str(apply_hits[0]["stack"])
    assert "TraceTreeWatch._fire" not in stack
    assert "fs_watch.py" not in stack
    assert apply_hits[0]["ident"] != loop_thread.ident
