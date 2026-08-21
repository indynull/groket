"""Bindings module: ChromeActions, focus_primary_list, open_jobs_on_app."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from groket.ui.bindings import (
    APP_SESSIONS,
    BROWSER,
    CAPABILITY_PICKER,
    FORM_SAVE,
    GLOBAL_ALWAYS,
    JOBS_MODAL,
    LIST_SELECT,
    MODAL_CANCEL_QUIT,
    MODAL_DISMISS,
    PERSONA_EDITOR,
    PERSONAS,
    RUN_CONFIGS,
    RUNNER,
    SCREEN_CHROME,
    SESSION_HOME_ACTIONS,
    ChromeActions,
    focus_primary_list,
    open_jobs_on_app,
)
from textual.app import App, ComposeResult
from textual.widgets import Static


def _shown_actions(bindings: tuple) -> list[str]:
    return [b.action for b in bindings if b.show]


class TestBindingTuples:
    def test_all_tuples_nonempty(self) -> None:
        for name, tup in [
            ("GLOBAL_ALWAYS", GLOBAL_ALWAYS),
            ("APP_SESSIONS", APP_SESSIONS),
            ("BROWSER", BROWSER),
            ("RUNNER", RUNNER),
            ("RUN_CONFIGS", RUN_CONFIGS),
            ("CAPABILITY_PICKER", CAPABILITY_PICKER),
            ("PERSONAS", PERSONAS),
            ("PERSONA_EDITOR", PERSONA_EDITOR),
            ("FORM_SAVE", FORM_SAVE),
            ("MODAL_CANCEL_QUIT", MODAL_CANCEL_QUIT),
            ("MODAL_DISMISS", MODAL_DISMISS),
            ("JOBS_MODAL", JOBS_MODAL),
            ("LIST_SELECT", LIST_SELECT),
        ]:
            assert len(tup) > 0, f"{name} should not be empty"

    def test_footer_chrome_order_sessions_home(self) -> None:
        """Help first among chrome; Quit in global chrome; no Refresh in footer."""
        shown = _shown_actions(APP_SESSIONS)
        assert shown[0] == "show_help"
        assert "quit" in shown
        assert "refresh_context" not in shown
        assert "open_session" in shown
        assert "open_runner" in shown

    def test_footer_chrome_order_pushed_screens(self) -> None:
        """Pushed screens: Help then Back then Quit. Jobs stays bound, off the rail."""
        shown = _shown_actions(SCREEN_CHROME)
        assert shown == ["show_help", "go_back", "quit"]
        assert "open_jobs" not in shown
        assert "refresh_context" not in shown
        assert any(b.action == "open_jobs" for b in SCREEN_CHROME)

    def test_browser_footer_is_session_actions(self) -> None:
        """Session rail: flag, note, copy, export — not jobs, delete, or analyze."""
        shown = set(_shown_actions(BROWSER))
        assert "open_jobs" not in shown
        assert "delete_session" not in shown
        assert "edit_operator_note" not in shown
        assert "analyze" not in shown
        assert "show_help" in shown
        assert "go_back" in shown
        assert "flag_event" in shown
        assert "operator_note" in shown
        assert "copy_detail" in shown
        assert "export_bundle" in shown

    def test_global_always_includes_quit(self) -> None:
        assert "quit" in _shown_actions(GLOBAL_ALWAYS)

    def test_runner_footer_has_no_session_list_actions(self) -> None:
        shown = set(_shown_actions(RUNNER))
        for action in (
            "open_session",
            "search_sessions",
            "open_runner",
            "toggle_select",
        ):
            assert action not in shown
        assert "show_help" in shown
        assert "go_back" in shown
        assert "run_evaluation" in shown
        assert "open_jobs" not in shown

    def test_launch_priority_keys_include_ctrl_j(self) -> None:
        """Ctrl+Enter often arrives as ctrl+j in terminals — both must be bound."""
        from groket.ui.bindings import APP_GLOBAL_PRIORITY

        keys = " ".join(b.key for b in APP_GLOBAL_PRIORITY)
        assert "ctrl+enter" in keys
        assert "ctrl+j" in keys
        launch = [b for b in RUNNER if b.action == "run_evaluation"]
        assert launch
        assert "ctrl+j" in launch[0].key

    def test_session_home_actions_covers_list_bindings(self) -> None:
        assert "quit" not in SESSION_HOME_ACTIONS  # global, not home-gated
        assert "open_runner" in SESSION_HOME_ACTIONS
        assert "open_session" in SESSION_HOME_ACTIONS
        assert "show_help" not in SESSION_HOME_ACTIONS
        assert "open_jobs" not in SESSION_HOME_ACTIONS


class TestFocusPrimaryList:
    def test_with_none(self) -> None:
        focus_primary_list(None)  # type: ignore[arg-type]  # deliberate wrong type

    def test_with_unfocusable(self) -> None:
        w = SimpleNamespace(can_focus=False, parent=None)
        focus_primary_list(w)  # type: ignore[arg-type]  # stub for test

    def test_with_focusable_parent(self) -> None:
        parent = SimpleNamespace(can_focus=True, focus=MagicMock())
        child = SimpleNamespace(can_focus=False, parent=parent)
        focus_primary_list(child)  # type: ignore[arg-type]  # stub for test

    def test_with_data_table_like(self) -> None:
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=3,
            move_cursor=MagicMock(),
            cursor_row=0,
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test
        widget.focus.assert_called_once()
        assert widget.cursor_type == "row"

    def test_with_empty_table(self) -> None:
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=0,
            move_cursor=MagicMock(),
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test

    def test_focus_not_callable(self) -> None:
        widget = SimpleNamespace(can_focus=True, focus="not_callable")
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test

    def test_negative_cursor_row(self) -> None:
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=5,
            move_cursor=MagicMock(),
            cursor_row=-1,
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test
        widget.move_cursor.assert_called()

    def test_cursor_row_beyond_count(self) -> None:
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=2,
            move_cursor=MagicMock(),
            cursor_row=5,
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test
        widget.move_cursor.assert_called()


class TestOpenJobsOnApp:
    def test_with_action(self) -> None:
        mock_fn = MagicMock()
        screen = SimpleNamespace(app=SimpleNamespace(action_open_jobs=mock_fn))
        open_jobs_on_app(screen)  # type: ignore[arg-type]  # stub for test
        mock_fn.assert_called_once()

    def test_without_action(self) -> None:
        screen = SimpleNamespace(app=SimpleNamespace())
        open_jobs_on_app(screen)  # type: ignore[arg-type]  # stub for test


class TestChromeActions:
    @pytest.mark.asyncio
    async def test_action_show_help(self) -> None:
        class HelpApp(App):
            def compose(self) -> ComposeResult:
                yield Static("hi")

        app = HelpApp()
        async with app.run_test():
            screen = app.screen
            ca = ChromeActions.__dict__["action_show_help"]
            ca(screen)

    @pytest.mark.asyncio
    async def test_action_self_test_no_op(self) -> None:
        class STApp(App):
            def compose(self) -> ComposeResult:
                yield Static("hi")

        app = STApp()
        async with app.run_test():
            screen = app.screen
            ca = ChromeActions.__dict__["action_self_test"]
            ca(screen)

    @pytest.mark.asyncio
    async def test_action_self_test_callable(self) -> None:
        """action_self_test delegates to app when callable is found."""
        called = []

        class STApp2(App):
            def compose(self) -> ComposeResult:
                yield Static("hi")

            def action_self_test(self) -> None:
                called.append(True)

        app = STApp2()
        async with app.run_test():
            screen = app.screen
            ca = ChromeActions.__dict__["action_self_test"]
            ca(screen)
            assert called

    @pytest.mark.asyncio
    async def test_action_open_jobs_callable(self) -> None:
        """action_open_jobs delegates to app."""
        called = []

        class JobsApp(App):
            def compose(self) -> ComposeResult:
                yield Static("hi")

            def action_open_jobs(self) -> None:
                called.append(True)

        app = JobsApp()
        async with app.run_test():
            screen = app.screen
            ca = ChromeActions.__dict__["action_open_jobs"]
            ca(screen)
            assert called


class TestFocusPrimaryListCursorReassert:
    def test_valid_cursor_reasserted(self) -> None:
        """Valid cursor_row is re-asserted with move_cursor."""
        widget = SimpleNamespace(
            can_focus=True,
            focus=MagicMock(),
            cursor_type="cell",
            row_count=5,
            move_cursor=MagicMock(),
            cursor_row=2,
        )
        focus_primary_list(widget)  # type: ignore[arg-type]  # stub for test
        widget.move_cursor.assert_called_with(row=2, column=0)


@pytest.mark.asyncio
async def test_session_home_bindings_hidden_when_runner_pushed(tmp_path: Path) -> None:
    """App session-list bindings must not appear while Runner is the top screen."""
    from groket.ui.app import TraceEvalApp
    from groket.ui.screens.runner import RunnerScreen

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        assert app._sessions_home_active() is True
        assert app.check_action("open_runner", ()) is not False
        assert app.check_action("quit", ()) is not False
        app.push_screen(RunnerScreen(work, run_manager=app.run_manager))
        await pilot.pause()
        assert app._sessions_home_active() is False
        for action in ("open_runner", "open_session", "search_sessions"):
            assert app.check_action(action, ()) is False
        # Quit stays available on pushed screens.
        assert app.check_action("quit", ()) is not False
        shown = {
            ab.binding.action
            for ab in app.screen.active_bindings.values()
            if ab.binding.show and ab.enabled
        }
        for action in ("open_runner", "open_session", "search_sessions"):
            assert action not in shown
        # Focus may sit in a TextArea (consumes some keys); chrome still includes Back.
        assert "go_back" in shown
        # Quit must remain actionable on pushed screens (footer may hide some keys).
        assert app.check_action("quit", ()) is not False
        assert hasattr(app.screen, "action_quit")


@pytest.mark.asyncio
async def test_ctrl_enter_launches_from_prompt_textarea(tmp_path: Path) -> None:
    """Priority launch fires while focus is in the runner prompt TextArea."""
    from groket.ui.app import TraceEvalApp
    from groket.ui.screens.runner import RunnerScreen
    from textual.widgets import TextArea

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    launched: list[int] = []

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(RunnerScreen(work, run_manager=app.run_manager))
        await pilot.pause()
        scr = app.screen
        assert isinstance(scr, RunnerScreen)
        orig = scr.action_run_evaluation

        def _track() -> None:
            launched.append(1)

        scr.action_run_evaluation = _track  # type: ignore[method-assign]
        try:
            ta = scr.query_one("#prompt-input", TextArea)
            ta.focus()
            await pilot.pause()
            assert app.check_action("launch_from_runner", ()) is True
            await pilot.press("ctrl+enter")
            await pilot.pause()
            assert launched == [1]
            launched.clear()
            await pilot.press("ctrl+j")
            await pilot.pause()
            assert launched == [1]
        finally:
            scr.action_run_evaluation = orig  # type: ignore[method-assign]
