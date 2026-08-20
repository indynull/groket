"""Command palette helpers — thin wiring to app/screen actions."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from . import text as U
from .i18n import t

if TYPE_CHECKING:
    from textual.app import App
    from textual.screen import Screen
PaletteItem = tuple[str, str, Callable[[], None]]


def invoke_app_action(app: App, method: str) -> None:
    if callable(fn := getattr(app, method, None)):
        fn()


def invoke_screen_action(screen: Screen, method: str) -> None:
    if callable(fn := getattr(screen, method, None)):
        fn()


def palette_command(cmd: tuple[str, str], callback: Callable[[], None]) -> PaletteItem:
    return (cmd[0], cmd[1], callback)


def _app(app: App, method: str, title_help: tuple[str, str]) -> PaletteItem:

    def cb(m: str = method) -> None:
        invoke_app_action(app, m)

    return palette_command(title_help, cb)


def _scr(screen: Screen, method: str, title_help: tuple[str, str]) -> PaletteItem:

    def cb(m: str = method) -> None:
        invoke_screen_action(screen, m)

    return palette_command(title_help, cb)


def yield_app_commands(app: App, screen: Screen) -> Iterator[PaletteItem]:
    """Yield ``(title, help, callback)`` for the command palette."""
    from .screens.browser import BrowserScreen
    from .screens.personas import PersonasScreen
    from .screens.rules import RulesScreen
    from .screens.run_configs import RunConfigsScreen
    from .screens.runner import RunnerScreen

    for method, th in (
        ("action_refresh_context", U.cmd_refresh()),
        ("action_open_jobs", U.cmd_jobs_logs()),
        (
            "action_self_test",
            (t("ui-self-test"), "Check Docker, Grok auth, work dir, and related host deps"),
        ),
        ("action_show_help", U.cmd_help()),
        ("action_quit", U.cmd_quit()),
        ("action_open_runner", U.cmd_open_runner()),
        ("action_open_run_configs", U.cmd_open_configs()),
        ("action_open_personas", U.cmd_open_personas()),
        ("action_open_rules", U.cmd_open_rules()),
        ("action_refresh_everything", U.cmd_full_refresh()),
    ):
        yield _app(app, method, th)
    match screen:
        case BrowserScreen():
            for method, th in (
                ("action_analyze", U.cmd_analyze_session()),
                ("action_focus_timeline_filter", U.cmd_focus_timeline_view()),
                ("action_overview_section", U.cmd_overview_section()),
                ("action_clear_filters", U.cmd_clear_timeline_view()),
                ("action_copy_detail", U.cmd_copy_detail()),
                ("action_delete_session", U.cmd_delete_sessions()),
                ("action_export_finding", U.cmd_export_finding()),
                ("action_export_bundle", U.cmd_export_bundle()),
                ("action_export_choose_profile", U.cmd_export_choose_profile()),
                ("action_operator_note", U.cmd_operator_note()),
                ("action_edit_operator_note", U.cmd_edit_operator_note()),
                ("action_toggle_event_reader", U.cmd_event_reader()),
                ("action_go_back", U.cmd_back_sessions()),
            ):
                yield _scr(screen, method, th)
        case PersonasScreen():
            for method, th in (
                ("action_new_persona", U.cmd_new_persona()),
                ("action_edit_persona", U.cmd_edit_persona()),
                ("action_delete_persona", U.cmd_delete_persona()),
                ("action_go_back", U.cmd_back()),
            ):
                yield _scr(screen, method, th)
        case RunConfigsScreen():
            for method, th in (
                ("action_open_in_runner", U.cmd_open_config_runner()),
                ("action_toggle_select", U.cmd_toggle_select_config()),
                ("action_select_all_toggle", U.cmd_select_all_configs()),
                ("action_launch_config", U.cmd_launch_config()),
                ("action_launch_selected", U.cmd_launch_selected()),
                ("action_export_task_yaml", U.cmd_export_task()),
                ("action_new_blank", U.cmd_new_blank_runner()),
                ("action_delete_config", U.cmd_delete_config()),
                ("action_go_back", U.cmd_back()),
            ):
                yield _scr(screen, method, th)
        case RunnerScreen():
            for method, th in (
                ("action_run_evaluation", U.cmd_launch_evaluation()),
                ("action_save_config_only", U.cmd_save_config_only()),
                ("action_export_task_yaml", U.cmd_export_task()),
                ("action_new_persona_from_runner", U.cmd_new_persona_runner()),
                ("action_open_persona_builder", U.cmd_persona_manager()),
                ("action_go_back", U.cmd_back()),
            ):
                yield _scr(screen, method, th)
        case RulesScreen():
            for method, th in (
                ("action_toggle_rule", U.cmd_toggle_rule()),
                ("action_enable_all", U.cmd_enable_all_rules()),
                ("action_go_back", U.cmd_back()),
            ):
                yield _scr(screen, method, th)
        case _:
            for method, th in (
                ("action_search_sessions", U.cmd_search_sessions()),
                ("action_toggle_select", U.cmd_toggle_select()),
                ("action_select_all", U.cmd_select_all_none()),
                ("action_rerun_session", U.cmd_rerun_session()),
                ("action_resume_session", U.cmd_resume_session()),
                ("action_save_session_config", U.cmd_save_session_config()),
                ("action_delete_sessions", U.cmd_delete_sessions()),
                ("action_follow_up_sessions", U.cmd_next_prompt()),
                ("action_mark_sessions_done", U.cmd_end_session()),
                ("action_analyze", U.cmd_analyze()),
                ("action_export_session_bundle", U.cmd_export_bundle()),
                ("action_export_session_choose_profile", U.cmd_export_choose_profile()),
                ("action_show_host_sessions", U.cmd_show_host_sessions()),
                ("action_hide_host_sessions", U.cmd_hide_host_sessions()),
                ("action_analysis_settings", U.cmd_analysis_settings()),
            ):
                if hasattr(app, method):
                    yield _app(app, method, th)
