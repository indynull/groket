"""Command palette helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from groket.ui.commands import (
    invoke_app_action,
    invoke_screen_action,
    palette_command,
    yield_app_commands,
)


class TestInvokeActions:
    def test_invoke_app_action_calls(self) -> None:
        fn = MagicMock()
        app = SimpleNamespace(action_quit=fn)
        invoke_app_action(app, "action_quit")  # type: ignore[arg-type]  # stub for test
        fn.assert_called_once()

    def test_invoke_app_action_missing(self) -> None:
        app = SimpleNamespace()
        invoke_app_action(app, "action_quit")  # type: ignore[arg-type]  # stub for test

    def test_invoke_screen_action_calls(self) -> None:
        fn = MagicMock()
        screen = SimpleNamespace(action_go_back=fn)
        invoke_screen_action(screen, "action_go_back")  # type: ignore[arg-type]  # stub for test
        fn.assert_called_once()

    def test_invoke_screen_action_missing(self) -> None:
        screen = SimpleNamespace()
        invoke_screen_action(screen, "nope")  # type: ignore[arg-type]  # stub for test


class TestPaletteCommand:
    def test_returns_triple(self) -> None:
        cb = MagicMock()
        title, desc, callback = palette_command(("Title", "Help text"), cb)
        assert title == "Title"
        assert desc == "Help text"
        assert callback is cb


class TestYieldAppCommands:
    def _make_app(self) -> SimpleNamespace:
        return SimpleNamespace(
            action_refresh_context=MagicMock(),
            action_open_jobs=MagicMock(),
            action_self_test=MagicMock(),
            action_show_help=MagicMock(),
            action_quit=MagicMock(),
            action_open_runner=MagicMock(),
            action_open_run_configs=MagicMock(),
            action_open_personas=MagicMock(),
            action_refresh_everything=MagicMock(),
            action_search_sessions=MagicMock(),
            action_toggle_select=MagicMock(),
            action_select_all=MagicMock(),
            action_rerun_session=MagicMock(),
            action_resume_session=MagicMock(),
            action_save_session_config=MagicMock(),
            action_delete_sessions=MagicMock(),
        )

    def test_default_screen_yields_app_commands(self) -> None:
        app = self._make_app()
        screen = SimpleNamespace()
        cmds = list(yield_app_commands(app, screen))  # type: ignore[arg-type]  # stub for test
        assert len(cmds) > 5
        titles = [c[0] for c in cmds]
        assert any("Refresh" in t or "refresh" in t.lower() for t in titles)
        assert not any("analysis" in t.lower() for t in titles)

    def test_browser_screen_commands(self) -> None:
        from groket.ui.screens.browser import BrowserScreen

        app = self._make_app()
        screen = BrowserScreen.__new__(BrowserScreen)
        cmds = list(yield_app_commands(app, screen))  # type: ignore[arg-type]  # stub for test
        assert len(cmds) > 5
        titles = [c[0] for c in cmds]
        assert any("export" in t.lower() for t in titles)
        assert not any("analyze" in t.lower() for t in titles)
        assert not any("analysis" in t.lower() for t in titles)

    def test_runner_screen_commands(self) -> None:
        from groket.ui.screens.runner import RunnerScreen

        app = self._make_app()
        screen = RunnerScreen.__new__(RunnerScreen)
        cmds = list(yield_app_commands(app, screen))  # type: ignore[arg-type]  # stub for test
        assert len(cmds) > 5

    def test_personas_screen_commands(self) -> None:
        from groket.ui.screens.personas import PersonasScreen

        app = self._make_app()
        screen = PersonasScreen.__new__(PersonasScreen)
        cmds = list(yield_app_commands(app, screen))  # type: ignore[arg-type]  # stub for test
        assert len(cmds) > 5

    def test_run_configs_screen_commands(self) -> None:
        from groket.ui.screens.run_configs import RunConfigsScreen

        app = self._make_app()
        screen = RunConfigsScreen.__new__(RunConfigsScreen)
        cmds = list(yield_app_commands(app, screen))  # type: ignore[arg-type]  # stub for test
        assert len(cmds) > 5

    def test_callbacks_are_callable(self) -> None:
        app = self._make_app()
        screen = SimpleNamespace()
        cmds = list(yield_app_commands(app, screen))  # type: ignore[arg-type]  # stub for test
        for _title, _desc, cb in cmds:
            assert callable(cb)
            cb()

    def test_screen_action_callback_invoked(self) -> None:
        """Screen-action callback invokes invoke_screen_action."""
        from groket.ui.screens.browser import BrowserScreen

        app = self._make_app()
        screen = BrowserScreen.__new__(BrowserScreen)
        screen.action_focus_timeline_filter = MagicMock()
        cmds = list(yield_app_commands(app, screen))  # type: ignore[arg-type]  # stub for test
        # Find the focus_timeline_filter command
        for _title, _desc, cb in cmds:
            if "timeline" in _title.lower() or "view" in _title.lower():
                cb()
                break
