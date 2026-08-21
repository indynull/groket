"""Typer CLI: TUI launch, serve, doctor, gen, batch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from groket.cli import TOOL_COMMANDS, app, launch_tui, main
from typer.testing import CliRunner

runner = CliRunner()


def test_version_prints_product_version() -> None:
    from groket import __version__

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    out = (result.stdout or result.output or "").strip()
    assert out == f"groket {__version__}"
    assert runner.invoke(app, ["-V"]).exit_code == 0


def test_help_lists_main_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout or result.output or ""
    assert "-V" in out
    assert "product version" in out
    assert "gen" in out
    assert "doctor" in out
    assert "batch" in out
    assert "serve" in out
    assert "hud" in out
    assert "tui" in out
    assert "editor" in out
    assert "config" in out
    assert "keys" in out
    assert "export-host" in out
    assert "self-test" not in out
    assert "emacs-path" not in out or "editor" in out
    assert "generator" not in out
    assert "audit" not in out
    assert runner.invoke(app, ["gen", "--help"]).exit_code == 0
    assert runner.invoke(app, ["batch", "--help"]).exit_code == 0
    assert runner.invoke(app, ["serve", "--help"]).exit_code == 0
    assert runner.invoke(app, ["editor", "--help"]).exit_code == 0
    assert runner.invoke(app, ["keys", "--help"]).exit_code == 0
    analyzer_help = runner.invoke(app, ["analyzer", "--help"])
    assert analyzer_help.exit_code != 0


def test_tool_commands() -> None:
    assert TOOL_COMMANDS == frozenset(
        {
            "gen",
            "batch",
            "serve",
            "hud",
            "tui",
            "doctor",
            "editor",
            "keys",
            "config",
            "export-host",
        }
    )


def test_export_host_writes_snapshot_without_serve(tmp_path: Path) -> None:
    host = tmp_path / "host"
    sess = host / "export-sess"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text(
        '{"info":{"id":"export-sess"},"generated_title":"Exported","num_messages":2}',
        encoding="utf-8",
    )
    (sess / "signals.json").write_text("{}", encoding="utf-8")
    (sess / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    dest = tmp_path / "out" / "host.json"
    result = runner.invoke(
        app,
        ["export-host", "-o", str(dest), "--host-root", str(host)],
    )
    assert result.exit_code == 0, result.output
    assert dest.is_file()
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["sessionId"] == "export-sess"
    assert payload["sessions"][0]["title"] == "Exported"
    assert "serve" not in (result.output or "").lower()


def test_serve_help_has_lifecycle_not_start() -> None:
    result = runner.invoke(app, ["serve", "--help"])
    assert result.exit_code == 0
    out = (result.stdout or result.output or "").lower()
    assert "stop" in out
    assert "restart" in out
    assert "status" in out
    # Bare serve starts; no separate start subcommand.
    assert "Commands" in (result.stdout or result.output or "")
    # Click lists commands; start should not appear as a verb.
    lines = (result.stdout or result.output or "").splitlines()
    cmd_block = False
    for line in lines:
        if "Commands" in line:
            cmd_block = True
            continue
        if cmd_block and line.strip().startswith("start"):
            pytest.fail("serve start should not be a subcommand")
        if cmd_block and line.startswith("╭─") and "Commands" not in line:
            break


def test_serve_restart_stop_then_start(tmp_path: Path) -> None:
    """restart = stop + start -d by default."""
    sock = tmp_path / "ctl.sock"
    calls: list[str] = []

    def fake_stop(socket_path, timeout=5.0):  # noqa: ANN001
        calls.append(f"stop:{socket_path}")
        return 0

    def fake_status(socket_path):  # noqa: ANN001
        from groket.integrations.daemon import ControlDaemonStatus

        return ControlDaemonStatus(
            socket_path=str(socket_path),
            socket_exists=True,
            pid=1,
            pid_alive=True,
            live=True,
            pid_path=str(socket_path) + ".pid",
        )

    class FakeResult:
        ok = True
        already_running = False
        pid = 42
        error = ""
        socket_path = sock

    def fake_detached(**kwargs):  # noqa: ANN003
        calls.append(f"start:{kwargs.get('socket_path')}")
        return FakeResult()

    with (
        patch("groket.integrations.daemon.stop_control_daemon", fake_stop),
        patch("groket.integrations.daemon.control_daemon_status", fake_status),
        patch("groket.integrations.daemon.start_control_daemon_detached", fake_detached),
    ):
        result = runner.invoke(
            app,
            ["serve", "restart", "-s", str(sock), "-P", str(tmp_path)],
        )
    assert result.exit_code == 0
    assert any(c.startswith("stop:") for c in calls)
    assert any(c.startswith("start:") for c in calls)


def test_serve_status_running_line(tmp_path: Path) -> None:
    sock = tmp_path / "s.sock"

    def fake_status(socket_path):  # noqa: ANN001
        from groket.integrations.daemon import ControlDaemonStatus

        return ControlDaemonStatus(
            socket_path=str(socket_path),
            socket_exists=True,
            pid=99,
            pid_alive=True,
            live=True,
            pid_path=str(socket_path) + ".pid",
        )

    with patch("groket.integrations.daemon.control_daemon_status", fake_status):
        result = runner.invoke(app, ["serve", "status", "-s", str(sock)])
    assert result.exit_code == 0
    out = result.stdout or result.output or ""
    assert "running" in out
    assert "pid=99" in out


def test_editor_emacs_path_prints_packaged_integration() -> None:
    result = runner.invoke(app, ["editor", "emacs-path"])
    assert result.exit_code == 0
    path = Path(result.stdout.strip())
    assert path.name == "groket.el"
    assert path.is_file()


def test_editor_vim_path_prints_packaged_neovim_runtime() -> None:
    result = runner.invoke(app, ["editor", "vim-path"])
    assert result.exit_code == 0
    path = Path(result.stdout.strip())
    assert path.name == "vim"
    assert (path / "lua" / "groket" / "init.lua").is_file()
    assert (path / "plugin" / "groket.lua").is_file()


class TestDoctorCommand:
    def test_doctor_json_no_tui(self, tmp_path: Path) -> None:
        from groket.diagnostics.self_test import CheckResult, SelfTestReport

        report = SelfTestReport(
            checks=[
                CheckResult(
                    id="x",
                    name="X",
                    ok=True,
                    required=True,
                    detail="fine",
                )
            ]
        )
        with (
            patch("groket.diagnostics.run_self_test", return_value=report) as mock_run,
            patch("groket.ui.app.TraceEvalApp") as mock_app,
        ):
            result = runner.invoke(app, ["doctor", "-P", str(tmp_path), "--json"])
            mock_run.assert_called_once()
            mock_app.assert_not_called()
            assert result.exit_code == 0
            out = result.stdout or result.output or ""
            assert '"ok"' in out

    def test_doctor_text(self, tmp_path: Path) -> None:
        from groket.diagnostics.self_test import CheckResult, SelfTestReport

        report = SelfTestReport(
            checks=[
                CheckResult(
                    id="x",
                    name="X",
                    ok=True,
                    required=True,
                    detail="fine",
                )
            ]
        )
        with patch("groket.diagnostics.run_self_test", return_value=report):
            result = runner.invoke(app, ["doctor", "-P", str(tmp_path)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "X" in out or "fine" in out

    def test_doctor_not_rewritten_as_path(self) -> None:
        with patch("groket.cli.app") as mock_app:
            main(argv=["doctor", "--json"])
            args = mock_app.call_args.kwargs.get("args") or mock_app.call_args[1].get("args", [])
            assert args[0] == "doctor"


class TestLaunchTui:
    def test_launch_resolves_path(self, tmp_path: Path) -> None:
        captured_calls: list[dict] = []

        class FakeApp:
            def __init__(self, **kw: object):
                captured_calls.append(kw)

            def run(self) -> None:
                pass

        import groket.ui.app as ui_app_mod

        orig = ui_app_mod.TraceEvalApp
        ui_app_mod.TraceEvalApp = FakeApp  # type: ignore[assignment,misc]
        try:
            launch_tui(path=tmp_path, config=None, ensure_serve=False)
            assert len(captured_calls) == 1
            assert captured_calls[0]["work_dir"] == tmp_path.resolve()
            assert captured_calls[0]["control_socket"].name == "control.sock"
            assert captured_calls[0]["control_attach_only"] is True

            captured_calls.clear()
            launch_tui(path=None, config=None, ensure_serve=False)
            assert len(captured_calls) == 1

            cfg = tmp_path / "config.toml"
            cfg.write_text("", encoding="utf-8")
            captured_calls.clear()
            launch_tui(path=tmp_path, config=cfg, ensure_serve=False)
            assert captured_calls[0]["config_path"] == cfg.expanduser()
        finally:
            ui_app_mod.TraceEvalApp = orig  # type: ignore[assignment,misc]

    def test_launch_opens_explicit_session_and_prompt(self, tmp_path: Path) -> None:
        captured_calls: list[dict] = []

        class FakeApp:
            def __init__(self, **kw: object):
                captured_calls.append(kw)

            def run(self) -> None:
                pass

        session = tmp_path / "runs" / "traces" / "session-cli-open"
        session.mkdir(parents=True)
        (session / "summary.json").write_text("{}", encoding="utf-8")
        socket_path = tmp_path / "editor.sock"

        with patch("groket.ui.app.TraceEvalApp", FakeApp):
            launch_tui(
                path=session,
                config=None,
                socket=socket_path,
                prompt_index=17,
                ensure_serve=False,
            )

        assert captured_calls == [
            {
                "traces_path": session.parent,
                "work_dir": tmp_path,
                "config_path": None,
                "control_socket": socket_path,
                "control_attach_only": True,
                "initial_session": session,
                "initial_prompt_index": 17,
            }
        ]

    def test_launch_can_disable_control_socket(self, tmp_path: Path) -> None:
        captured_calls: list[dict] = []

        class FakeApp:
            def __init__(self, **kw: object):
                captured_calls.append(kw)

            def run(self) -> None:
                pass

        with patch("groket.ui.app.TraceEvalApp", FakeApp):
            launch_tui(path=tmp_path, config=None, socket=False, ensure_serve=False)

        assert captured_calls[0]["control_socket"] is None
        assert captured_calls[0]["control_attach_only"] is False

    def test_launch_is_silent_when_control_is_fine(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class FakeApp:
            def __init__(self, **kw: object):
                del kw

            def run(self) -> None:
                pass

        with patch("groket.ui.app.TraceEvalApp", FakeApp):
            launch_tui(path=tmp_path, config=None, ensure_serve=False)
        err = capsys.readouterr().err
        assert "work_dir=" not in err
        assert "already live" not in err
        assert "started control owner" not in err


class TestMainEntryArgv:
    def test_main_path_positional_rewrite(self) -> None:
        with patch("groket.cli.app") as mock_app:
            main(argv=["/some/path"])
            mock_app.assert_called_once()
            call_kwargs = mock_app.call_args
            args = call_kwargs.kwargs.get("args") or call_kwargs[1].get("args", [])
            assert args[0] == "-P"
            assert args[1] == "/some/path"

    def test_main_no_argv(self) -> None:
        import sys

        with (
            patch.object(sys, "argv", ["groket", "--help"]),
            patch("groket.cli.app") as mock_app,
        ):
            main(argv=None)
            mock_app.assert_called_once()


class TestBatchCommands:
    def test_batch_validate_ok(self, tmp_path: Path) -> None:
        demo = Path("examples/tasks/demo_tasks.yaml")
        if not demo.is_file():
            pytest.skip("demo tasks missing")
        result = runner.invoke(app, ["batch", "validate", str(demo)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "OK" in out

    def test_batch_validate_bad(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("tasks: []\n", encoding="utf-8")
        result = runner.invoke(app, ["batch", "validate", str(bad)])
        assert result.exit_code == 2

    def test_batch_schema_stdout(self) -> None:
        result = runner.invoke(app, ["batch", "schema"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "tasks.schema.json" in out or "TaskDefinition" in out or "$id" in out

    def test_config_schema_stdout(self) -> None:
        result = runner.invoke(app, ["config", "schema"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "config.schema.json" in out
        assert "show_host_sessions" in out

    def test_config_validate_default(self) -> None:
        result = runner.invoke(app, ["config", "validate"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "OK" in out
        assert "theme=" in out

    def test_config_validate_example(self) -> None:
        example = Path("examples/config/config.toml")
        result = runner.invoke(app, ["config", "validate", str(example)])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "OK" in out
        assert str(example) in out

    def test_config_validate_bad_toml(self, tmp_path: Path) -> None:
        bad = tmp_path / "config.toml"
        bad.write_text("not = [toml", encoding="utf-8")
        result = runner.invoke(app, ["config", "validate", str(bad)])
        assert result.exit_code == 2
        err = result.stderr or result.output or ""
        assert "error" in err.lower() or "invalid" in err.lower()

    def test_batch_not_rewritten_as_path(self) -> None:
        with patch("groket.cli.app") as mock_app:
            main(argv=["batch", "validate", "x.yaml"])
            args = mock_app.call_args.kwargs.get("args") or mock_app.call_args[1].get("args", [])
            assert args[0] == "batch"


class TestGenCommands:
    def test_gen_tasks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        dest = tmp_path / "example_tasks.yaml"
        result = runner.invoke(app, ["gen", "tasks", str(dest), "-f"])
        assert result.exit_code == 0
        out = result.stdout or result.output or ""
        assert "Wrote tasks file" in out
        assert dest.is_file()
