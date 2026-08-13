"""Locate / stale-detect iced HUD binary (no GUI, no cargo)."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

from groket.hud import launch as launch_mod
from groket.hud.launch import (
    find_hud_binary,
    hud_binary_is_stale,
    hud_checkout_dir,
)


class _Proc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


def test_find_hud_binary_release_or_none() -> None:
    """When the release binary is built, it is discoverable."""
    found = find_hud_binary()
    release = (
        Path(__file__).resolve().parents[2] / "groket-hud" / "target" / "release" / "groket-hud"
    )
    if release.is_file():
        assert found is not None
        assert found.name == "groket-hud"
        assert found.is_file()
    else:
        assert found is None or found.is_file()


def test_hud_checkout_dir_in_repo() -> None:
    checkout = hud_checkout_dir()
    assert checkout is not None
    assert (checkout / "Cargo.toml").is_file()
    assert (checkout / "src" / "main.rs").is_file()


def test_hud_binary_is_stale_when_source_newer(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src"
    src.mkdir(parents=True)
    (src / "main.rs").write_text("// old\n", encoding="utf-8")
    binary = checkout / "target" / "debug" / "groket-hud"
    binary.parent.mkdir(parents=True)
    binary.write_text("bin", encoding="utf-8")
    binary.chmod(0o755)
    time.sleep(0.05)
    (src / "main.rs").write_text("// new\n", encoding="utf-8")
    assert hud_binary_is_stale(binary, checkout) is True
    time.sleep(0.05)
    binary.write_text("bin2", encoding="utf-8")
    assert hud_binary_is_stale(binary, checkout) is False


def test_ensure_hud_binary_rebuilds_release_when_stale(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src"
    src.mkdir(parents=True)
    (checkout / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    (src / "main.rs").write_text("x", encoding="utf-8")
    built = checkout / "target" / "release" / "groket-hud"
    built.parent.mkdir(parents=True)

    def fake_build(root: Path | None = None, *, debug: bool = False) -> Path | None:
        assert debug is False
        built.write_text("fresh", encoding="utf-8")
        built.chmod(0o755)
        return built

    with (
        patch.object(launch_mod, "hud_checkout_dir", return_value=checkout),
        patch.object(launch_mod, "build_hud", side_effect=fake_build) as mock_build,
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("GROKET_HUD_BIN", None)
        out = launch_mod.ensure_hud_binary()
    assert out == built
    mock_build.assert_called_once()
    assert mock_build.call_args.kwargs.get("debug") is False


def test_ensure_hud_binary_debug_profile(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src"
    src.mkdir(parents=True)
    (checkout / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    (src / "main.rs").write_text("x", encoding="utf-8")
    built = checkout / "target" / "debug" / "groket-hud"
    built.parent.mkdir(parents=True)

    def fake_build(root: Path | None = None, *, debug: bool = False) -> Path | None:
        assert debug is True
        built.write_text("dbg", encoding="utf-8")
        built.chmod(0o755)
        return built

    with (
        patch.object(launch_mod, "hud_checkout_dir", return_value=checkout),
        patch.object(launch_mod, "build_hud", side_effect=fake_build) as mock_build,
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("GROKET_HUD_BIN", None)
        out = launch_mod.ensure_hud_binary(debug=True)
    assert out == built
    mock_build.assert_called_once()
    assert mock_build.call_args.kwargs.get("debug") is True


def test_install_desktop_runs_binary_flag(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary) as mock_ensure,
        patch.object(launch_mod.subprocess, "run", return_value=_Proc(0)) as mock_run,
    ):
        code = launch_mod.install_desktop(rebuild=True, debug=False)
    assert code == 0
    mock_ensure.assert_called_once_with(rebuild=True, debug=False)
    mock_run.assert_called_once()
    assert mock_run.call_args.args[0] == [str(binary), "--install-desktop"]


def test_install_desktop_missing_binary() -> None:
    with patch.object(launch_mod, "ensure_hud_binary", return_value=None):
        assert launch_mod.install_desktop() == 127


def test_default_summon_socket_path_runtime(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.delenv("GROKET_HUD_SUMMON_SOCKET", raising=False)
    p = launch_mod.default_summon_socket_path()
    assert p == tmp_path / "run" / "groket" / "hud-summon.sock"


def test_default_summon_socket_path_override(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom.sock"
    monkeypatch.setenv("GROKET_HUD_SUMMON_SOCKET", str(custom))
    assert launch_mod.default_summon_socket_path() == custom


def test_send_summon_command_runs_binary_flag(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "find_hud_binary", return_value=binary),
        patch.object(launch_mod.subprocess, "run", return_value=_Proc(0)) as mock_run,
    ):
        code = launch_mod.send_summon_command("toggle")
    assert code == 0
    assert mock_run.call_args.args[0] == [str(binary), "--toggle"]


def test_send_summon_command_rejects_unknown() -> None:
    assert launch_mod.send_summon_command("explode") == 1


def test_run_hud_summon_show_starts_when_not_running(tmp_path: Path) -> None:
    from groket.hud import app as hud_app
    from groket.integrations.daemon import EnsureDaemonResult

    sock = tmp_path / "control.sock"
    with (
        patch("groket.hud.launch.summon_socket_accepts", return_value=False),
        patch("groket.hud.launch.hud_process_running", return_value=False),
        patch.object(hud_app, "ensure_control_daemon") as mock_daemon,
        patch.object(hud_app, "control_socket_accepts", return_value=True),
        patch.object(hud_app, "wait_until_control_accepts", return_value=True),
        patch.object(hud_app.asyncio, "run", return_value=None),
        patch.object(hud_app, "launch_hud", return_value=0) as mock_launch,
        patch.dict(os.environ, {}, clear=False),
    ):
        mock_daemon.return_value = EnsureDaemonResult(
            ok=True,
            already_running=True,
            spawned=False,
            pid=1,
            socket_path=sock,
            error="",
        )
        os.environ.pop("GROKET_HUD_SHOW_ON_START", None)
        code = hud_app.run_hud(summon="show", auto_serve=True, socket_path=sock)
        assert code == 0
        assert os.environ.get("GROKET_HUD_SHOW_ON_START") == "1"
        mock_launch.assert_called_once()


def test_run_hud_summon_toggle_when_socket_live() -> None:
    from groket.hud import app as hud_app

    with (
        patch("groket.hud.launch.summon_socket_accepts", return_value=True),
        patch("groket.hud.launch.send_summon_command", return_value=0) as mock_send,
        patch.object(hud_app, "ensure_control_daemon") as mock_daemon,
        patch.object(hud_app, "launch_hud") as mock_launch,
    ):
        code = hud_app.run_hud(summon="toggle", auto_serve=False)
    assert code == 0
    mock_send.assert_called_once_with("toggle")
    mock_daemon.assert_not_called()
    mock_launch.assert_not_called()


def test_run_hud_install_desktop_skips_serve(tmp_path: Path) -> None:
    from groket.hud import app as hud_app

    with (
        patch.object(hud_app, "ensure_control_daemon") as mock_daemon,
        patch("groket.hud.launch.install_desktop", return_value=0) as mock_install,
        patch.object(hud_app, "launch_hud") as mock_launch,
    ):
        code = hud_app.run_hud(install_desktop=True, rebuild=False, debug=True)
    assert code == 0
    mock_daemon.assert_not_called()
    mock_launch.assert_not_called()
    mock_install.assert_called_once_with(rebuild=False, debug=True)


def test_launch_hud_passes_debug_to_ensure(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary) as mock_ensure,
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod, "summon_socket_accepts", return_value=False),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 1
        code = launch_mod.launch_hud(
            socket_path=tmp_path / "c.sock",
            debug=True,
        )
    assert code == 0
    mock_ensure.assert_called_once_with(rebuild=False, debug=True)


def test_launch_hud_detaches_by_default(tmp_path: Path) -> None:
    """Default path spawns the binary in a new session and returns without waiting."""
    binary = tmp_path / "groket-hud"
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    sock = tmp_path / "control.sock"

    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod, "summon_socket_accepts", return_value=False),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4242
        code = launch_mod.launch_hud(socket_path=sock)
    assert code == 0
    mock_popen.assert_called_once()
    kwargs = mock_popen.call_args.kwargs
    assert kwargs.get("start_new_session") is True


def test_launch_hud_skips_when_summon_socket_live(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod, "summon_socket_accepts", return_value=True),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
        patch.object(launch_mod.subprocess, "run") as mock_run,
    ):
        code = launch_mod.launch_hud(socket_path=tmp_path / "c.sock")
    assert code == 0
    mock_popen.assert_not_called()
    mock_run.assert_not_called()


def test_launch_hud_skips_when_already_running(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "hud_process_running", return_value=True),
        patch.object(launch_mod, "summon_socket_accepts", return_value=True),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
        patch.object(launch_mod.subprocess, "run") as mock_run,
    ):
        code = launch_mod.launch_hud(socket_path=tmp_path / "c.sock")
    assert code == 0
    mock_popen.assert_not_called()
    mock_run.assert_not_called()


def test_launch_hud_replaces_process_when_summon_socket_dead(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "hud_process_running", return_value=True),
        patch.object(launch_mod, "summon_socket_accepts", return_value=False),
        patch.object(launch_mod, "stop_hud_processes", return_value=1) as mock_stop,
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 7
        code = launch_mod.launch_hud(socket_path=tmp_path / "c.sock")
    assert code == 0
    mock_stop.assert_called_once()
    mock_popen.assert_called_once()


def test_launch_hud_foreground_waits(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod, "summon_socket_accepts", return_value=False),
        patch.object(
            launch_mod.subprocess, "run", return_value=type("R", (), {"returncode": 0})()
        ) as mock_run,
    ):
        code = launch_mod.launch_hud(
            socket_path=tmp_path / "c.sock",
            foreground=True,
        )
    assert code == 0
    mock_run.assert_called_once()


def test_launch_hud_restart_stops_then_spawns(tmp_path: Path) -> None:
    binary = tmp_path / "groket-hud"
    binary.write_text("x", encoding="utf-8")
    binary.chmod(0o755)
    with (
        patch.object(launch_mod, "ensure_hud_binary", return_value=binary),
        patch.object(launch_mod, "stop_hud_processes", return_value=1) as mock_stop,
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod, "summon_socket_accepts", return_value=False),
        patch.object(launch_mod.subprocess, "Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 99
        code = launch_mod.launch_hud(
            socket_path=tmp_path / "c.sock",
            restart=True,
        )
    assert code == 0
    mock_stop.assert_called_once()
    mock_popen.assert_called_once()


def test_launch_hud_dev_runs_cargo(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    checkout.mkdir()
    (checkout / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    (checkout / "src").mkdir()
    (checkout / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    sock = tmp_path / "c.sock"
    with (
        patch.object(launch_mod, "hud_checkout_dir", return_value=checkout),
        patch.object(launch_mod, "_hud_shortcut_env", return_value={}),
        patch.object(launch_mod, "hud_process_running", return_value=False),
        patch.object(launch_mod, "summon_socket_accepts", return_value=False),
        patch.object(launch_mod.shutil, "which", return_value="/usr/bin/cargo"),
        patch.object(launch_mod.subprocess, "run", return_value=_Proc(0)) as mock_run,
    ):
        code = launch_mod.launch_hud_dev(socket_path=sock)
    assert code == 0
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "/usr/bin/cargo"
    assert cmd[1:3] == ["run", "--manifest-path"]
    env = mock_run.call_args.kwargs["env"]
    assert env["GROKET_CONTROL_SOCKET"] == str(sock)


def test_build_hud_runs_cargo_only(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src"
    src.mkdir(parents=True)
    (checkout / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    binary = checkout / "target" / "release" / "groket-hud"
    binary.parent.mkdir(parents=True)

    def fake_cargo(cmd: list[str], **kwargs: object) -> _Proc:
        del kwargs
        assert "cargo" in cmd[0]
        binary.write_text("bin", encoding="utf-8")
        binary.chmod(0o755)
        return _Proc(0)

    with (
        patch.object(launch_mod.shutil, "which", return_value="/usr/bin/cargo"),
        patch.object(launch_mod.subprocess, "run", side_effect=fake_cargo),
    ):
        out = launch_mod.build_hud(checkout, debug=False)
    assert out == binary


def test_build_hud_release_drops_debug_and_coverage_trees(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src"
    src.mkdir(parents=True)
    (checkout / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    binary = checkout / "target" / "release" / "groket-hud"
    binary.parent.mkdir(parents=True)
    debug_obj = checkout / "target" / "debug" / "deps" / "old.rlib"
    debug_obj.parent.mkdir(parents=True)
    debug_obj.write_text("old", encoding="utf-8")
    cov = checkout / "target" / "llvm-cov-target" / "debug"
    cov.mkdir(parents=True)
    (cov / "junk").write_text("c", encoding="utf-8")

    def fake_cargo(cmd: list[str], **kwargs: object) -> _Proc:
        del kwargs
        binary.write_text("bin", encoding="utf-8")
        binary.chmod(0o755)
        return _Proc(0)

    with (
        patch.object(launch_mod.shutil, "which", return_value="/usr/bin/cargo"),
        patch.object(launch_mod.subprocess, "run", side_effect=fake_cargo),
    ):
        out = launch_mod.build_hud(checkout, debug=False)
    assert out == binary
    assert binary.is_file()
    assert not (checkout / "target" / "debug").exists()
    assert not (checkout / "target" / "llvm-cov-target").exists()


def test_build_hud_debug_keeps_debug_drops_coverage(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src"
    src.mkdir(parents=True)
    (checkout / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    binary = checkout / "target" / "debug" / "groket-hud"
    binary.parent.mkdir(parents=True)
    cov = checkout / "target" / "llvm-cov-target" / "debug"
    cov.mkdir(parents=True)
    (cov / "junk").write_text("c", encoding="utf-8")

    def fake_cargo(cmd: list[str], **kwargs: object) -> _Proc:
        del kwargs
        binary.write_text("bin", encoding="utf-8")
        binary.chmod(0o755)
        return _Proc(0)

    with (
        patch.object(launch_mod.shutil, "which", return_value="/usr/bin/cargo"),
        patch.object(launch_mod.subprocess, "run", side_effect=fake_cargo),
    ):
        out = launch_mod.build_hud(checkout, debug=True)
    assert out == binary
    assert binary.is_file()
    assert not (checkout / "target" / "llvm-cov-target").exists()


def test_ensure_hud_binary_prunes_when_release_is_fresh(tmp_path: Path) -> None:
    checkout = tmp_path / "groket-hud"
    src = checkout / "src"
    src.mkdir(parents=True)
    (checkout / "Cargo.toml").write_text("[package]\nname='x'\n", encoding="utf-8")
    (src / "main.rs").write_text("x", encoding="utf-8")
    built = checkout / "target" / "release" / "groket-hud"
    built.parent.mkdir(parents=True)
    built.write_text("fresh", encoding="utf-8")
    built.chmod(0o755)
    debug_obj = checkout / "target" / "debug" / "deps" / "old.rlib"
    debug_obj.parent.mkdir(parents=True)
    debug_obj.write_text("old", encoding="utf-8")

    with (
        patch.object(launch_mod, "hud_checkout_dir", return_value=checkout),
        patch.object(launch_mod, "build_hud") as mock_build,
        patch.dict(os.environ, {}, clear=False),
    ):
        os.environ.pop("GROKET_HUD_BIN", None)
        out = launch_mod.ensure_hud_binary()
    assert out == built
    mock_build.assert_not_called()
    assert not (checkout / "target" / "debug").exists()


def test_summon_socket_accepts_live_unix_listener(tmp_path: Path) -> None:
    import socket
    import threading

    path = tmp_path / "hud-summon.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def _accept() -> None:
        conn, _ = server.accept()
        conn.close()

    th = threading.Thread(target=_accept, daemon=True)
    th.start()
    assert launch_mod.summon_socket_accepts(path) is True
    th.join(timeout=1)
    server.close()
    path.unlink(missing_ok=True)
    assert launch_mod.summon_socket_accepts(path) is False


def test_summon_socket_accepts_rejects_stale_path(tmp_path: Path) -> None:
    missing = tmp_path / "gone.sock"
    assert launch_mod.summon_socket_accepts(missing) is False
    plain = tmp_path / "plain.sock"
    plain.write_text("not a socket", encoding="utf-8")
    assert launch_mod.summon_socket_accepts(plain) is False


def test_summon_protocol_line_received_by_fake_server(tmp_path: Path) -> None:
    import socket
    import threading

    path = tmp_path / "hud-summon.sock"
    got: list[str] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)

    def _accept() -> None:
        conn, _ = server.accept()
        data = conn.recv(64).decode("utf-8")
        got.append(data.strip())
        conn.close()

    th = threading.Thread(target=_accept, daemon=True)
    th.start()
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(path))
    client.sendall(b"toggle\n")
    client.close()
    th.join(timeout=1)
    server.close()
    assert got == ["toggle"]
