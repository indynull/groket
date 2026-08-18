"""Main Textual application for groket.

UI entry point only: domain work goes through ``services``, ``analysis``,
``run_manager``, ``personas`` — not embedded business logic in screens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    Select,
    Static,
    TextArea,
)

from ..analysis.base import AnalysisResult, Finding
from ..constants import META_CACHE_FILENAME

if TYPE_CHECKING:
    from ..analysis.service import AnalysisService
    from ..keys import Keymap
from ..integrations.control_client import (
    HEAVY_RPC_TIMEOUT,
    ControlClient,
    listen_control_notifications,
)
from ..models import JsonObject, JsonValue, SessionMeta, as_json_object, json_as_str
from ..parser import extract_prompt, find_sessions, load_session_meta
from ..paths import app_config_path
from ..runs.run_manager import BackgroundRun, RunManager
from ..session.access import (
    DEFAULT_SESSION_LIST_LIMIT,
    RemoteSessionAccess,
    catalog_list_next_offset,
)
from . import text as U
from .appearance import Appearance, appearance
from .bindings import (
    APP_GLOBAL_PRIORITY,
    APP_SESSIONS,
    FORM_SAVE,
    SESSION_HOME_ACTIONS,
    focus_primary_list,
)
from .brand_mark import AppChrome, AppFooter, paths_banner
from .control_notice import control_operator_text
from .data_table import (
    cursor_row_key,
    preserving_scroll,
    restore_cursor,
    set_marker_column,
    style_data_table,
    update_row_cell,
)
from .i18n import join_ui, setup_i18n, t
from .keys import format_key_chord
from .quit_actions import QuitActions
from .screens.browser import BrowserScreen
from .screens.rules import RulesScreen
from .screens.run_configs import RunConfigsScreen
from .screens.runner import RunnerPrefill, RunnerScreen
from .theme import register_brand_themes, resolve_theme
from .threads import call_ui
from .widgets.controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS

logger = logging.getLogger(__name__)
_SESSION_FILTER_ALL = "all"


def _coerce_select_value(value, *, default=None):
    """Normalize Textual Select values (sentinel / None) to a plain choice."""
    if value is None:
        return default
    try:
        from textual.widgets import Select as _Select

        if value == getattr(_Select, "BLANK", object()):
            return default
    except Exception:
        pass
    name = type(value).__name__
    if name in ("NoSelection", "MissingValue", "Null", "_NoSelection"):
        return default
    if not isinstance(value, (str, int, float, bool)):
        return default
    return value


class InteractiveSessionsModal(QuitActions, ModalScreen[tuple[str, bool] | None]):
    """Prompt for a follow-up on awaiting sessions (sessions home).

    Dismisses with ``(prompt, final_turn)`` or ``None`` on cancel. When
    *final_turn* is true, the gate runs this turn then stops awaiting
    (same as the browser pending bar). Mark-done (``e``) remains separate.
    """

    BINDINGS = list(FORM_SAVE)

    def __init__(self, *, n_awaiting: int) -> None:
        super().__init__()
        self._n = max(1, int(n_awaiting))

    def compose(self) -> ComposeResult:
        with Container(id="interactive-sessions-modal"):
            yield Label(U.interactive_modal_title(self._n), id="interactive-modal-title")
            yield Input(placeholder=U.follow_up_placeholder(), id="interactive-follow-input")
            yield Checkbox(
                t("follow-up-last-turn"),
                id="interactive-follow-last-turn",
                value=False,
            )
            with Horizontal(id="interactive-modal-actions", classes="modal-footer"):
                yield Button(U.send(), variant="primary", id="interactive-send")
                yield Button(U.cancel(), id="interactive-cancel")

    def on_mount(self) -> None:
        with suppress(Exception):
            self.query_one("#interactive-follow-input", Input).focus()

    def action_save(self) -> None:
        self._submit_follow()

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    @on(Button.Pressed, "#interactive-send")
    def _on_send(self) -> None:
        self._submit_follow()

    @on(Button.Pressed, "#interactive-cancel")
    def _on_cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#interactive-follow-input")
    def _on_submit_input(self) -> None:
        self._submit_follow()

    def _submit_follow(self) -> None:
        try:
            text = self.query_one("#interactive-follow-input", Input).value.strip()
        except Exception:
            text = ""
        if not text:
            with suppress(Exception):
                self.notify(U.follow_up_empty(), severity="warning", timeout=2)
            return
        final = False
        with suppress(Exception):
            final = bool(self.query_one("#interactive-follow-last-turn", Checkbox).value)
        self.dismiss((text, final))


class AnalysisSettingsModal(QuitActions, ModalScreen[bool]):
    """Configure analysis behaviour (all registered plugins run on analyze)."""

    BINDINGS = list(FORM_SAVE)

    def __init__(self, work_dir: Path) -> None:
        super().__init__()
        self._work_dir = Path(work_dir)

    def compose(self) -> ComposeResult:
        from ..analysis import list_analyzers, load_pipeline_config
        from .i18n import t

        app = self.app
        config_path = getattr(app, "_config_path", None)
        cfg = load_pipeline_config(self._work_dir, config_path=config_path)
        try:
            getter = getattr(app, "_analysis_svc", None)
            svc = getter() if callable(getter) else None
            if svc is None:
                raise RuntimeError("no analysis service")
            plugin_list = ", ".join(p.id for p in svc.list_plugins() if p.id != "noop") or "(none)"
        except Exception:
            plugin_list = ", ".join(p.id for p in list_analyzers() if p.id != "noop") or "(none)"
        with Container(id="analysis-settings-modal"):
            yield Label(U.analysis_pipeline_title(), id="analysis-settings-title")
            yield Static(
                t(
                    "analysis-settings-help",
                    list=plugin_list,
                    config=str(config_path or "~/.groket/config.toml"),
                ),
                id="analysis-settings-help",
            )
            yield Checkbox(
                U.auto_analyze_on_open(),
                value=(cfg.auto_analyze_when != "never"),
                id="as-auto-analyze",
            )
            yield Static(t("analysis-when-help"), id="as-when-help")
            yield Static(
                t(
                    "analysis-workers-help",
                    analysis=cfg.analysis_workers,
                    refresh=cfg.live_refresh_workers,
                ),
                id="as-workers-help",
            )
            with Horizontal(id="analysis-settings-actions", classes="modal-footer"):
                yield Button(U.save(), variant="primary", id="as-save")
                yield Button(U.cancel(), id="as-cancel")

    def action_cancel(self) -> None:
        from .bindings import dismiss_after_blur

        dismiss_after_blur(self, False)

    def action_save(self) -> None:
        self._persist()

    @on(Button.Pressed, "#as-cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#as-save")
    def _save(self) -> None:
        self._persist()

    def _persist(self) -> None:
        from ..analysis import AnalysisPipelineConfig, load_pipeline_config, save_pipeline_config
        from ..analysis.service import AnalysisService, set_analysis_service

        auto = self.query_one("#as-auto-analyze", Checkbox).value
        app = self.app
        config_path = getattr(app, "_config_path", None)
        prev = load_pipeline_config(self._work_dir, config_path=config_path)
        cfg = AnalysisPipelineConfig(
            plugins=list(prev.plugins),
            auto_analyze_when=("session_complete" if auto else "never"),
            analysis_workers=prev.analysis_workers,
            live_refresh_workers=prev.live_refresh_workers,
        )
        save_pipeline_config(cfg=cfg, config_path=config_path)
        from ..paths import analysis_cache_dir

        svc = AnalysisService(
            self._work_dir, config=cfg, config_path=config_path, cache_root=analysis_cache_dir()
        )
        set_analysis_service(svc)
        self.dismiss(True)


def _session_search_haystack(meta: SessionMeta, label: str) -> str:
    """Plain text used for as-you-type session list filtering (case-insensitive)."""
    parts = [
        meta.session_id or "",
        meta.model_display or "",
        (meta.label or "")[:80],
        label or "",
        meta.origin or "",
        meta.task_id or "",
        meta.git_repo or "",
        (meta.summary_text or "")[:120],
        meta.turn_outcome or "",
        meta.list_status_label() or "",
    ]
    if meta.git_repo:
        parts.append(meta.git_repo.rstrip("/").rsplit("/", 1)[-1])
    return " ".join(parts).casefold()


def first_home_list_fetch() -> dict[str, int | bool]:
    """First attach ``session/list``: one page, no matched drain."""
    return {
        "drain": False,
        "limit": int(DEFAULT_SESSION_LIST_LIMIT),
        "offset": 0,
        "since_revision": 0,
    }


class TraceEvalApp(App):
    """groket — Trace evaluation TUI for hunting bad model behaviors."""

    TITLE = "groket"
    SUB_TITLE = ""
    CSS_PATH = "app.tcss"
    BINDINGS = [*APP_GLOBAL_PRIORITY, *APP_SESSIONS]
    COMMAND_PALETTE_DISPLAY = "Ctrl+P"
    # Textual text selection (drag) + OSC 52 copy; default is True but be explicit.
    ALLOW_SELECT = True
    # Debounce for copy toasts (see notify_copied).
    _copy_notify_at: float = 0.0
    _copy_notify_msg: str = ""

    def get_key_display(self, binding: Binding) -> str:
        """Footer / key panel: Ctrl+S, not caret ^s or unicode glyphs."""
        if binding.key_display:
            return binding.key_display
        bid = getattr(binding, "id", None)
        keymap = getattr(self, "_resolved_keymap", None)
        if bid and keymap is not None:
            from groket.keys import chord_has_sequence, format_leader_chord

            try:
                chord = keymap.binding(bid).chord
            except KeyError:
                chord = ""
            if chord and chord_has_sequence(chord):
                raw = format_leader_chord(keymap.leader, chord)
                return " ".join(format_key_chord(part) for part in raw.split())
        return format_key_chord(binding.key)

    def get_system_commands(self, screen: Screen):
        """Populate Ctrl+P palette with context-aware actions."""
        yield from super().get_system_commands(screen)
        from .commands import yield_app_commands

        for title, help_text, callback in yield_app_commands(self, screen):
            yield SystemCommand(title, help_text, callback)

    def notify_copied(self, message: str) -> None:
        """Show a copy toast, suppressing rapid repeats of the same message.

        Drag-end auto-copy and ``y`` can fire often; stacked toasts are noise.
        """
        now = time.monotonic()
        last_at = float(getattr(self, "_copy_notify_at", 0.0) or 0.0)
        last_msg = str(getattr(self, "_copy_notify_msg", "") or "")
        if message == last_msg and (now - last_at) < 1.5:
            return
        self._copy_notify_at = now
        self._copy_notify_msg = message
        self.notify(message, severity="information", timeout=2.0)

    def _copy_live_selection(self) -> bool:
        """Copy screen text selection when non-empty.

        :returns: True when text was placed on the clipboard.
        """
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            return False
        if selected is None or selected == "":
            return False
        self.copy_to_clipboard(selected)
        self.notify_copied(t("ui-copied-selection"))
        return True

    def on_text_selected(self, event: events.TextSelected) -> None:
        """Auto-copy when a mouse drag selection ends (Textual posts on mouse-up).

        Pure clicks clear the selection before this event, so they no-op.
        Extract uses unwrapped Content plain (soft-wrap spans stay complete).
        """
        self._copy_live_selection()

    def action_help_quit(self) -> None:
        """Ctrl+C: copy live selection when present, else Textual's quit hint.

        Textual binds Ctrl+C on the app to ``help_quit`` (system). That shadows
        the screen's ``copy_text`` binding. With a drag selection, copy that
        plain text (same as ``y`` / Ctrl+Shift+C for selections). Without a
        selection, show the quit hint — full-pane yank stays on ``y``.
        """
        if self._copy_live_selection():
            return
        # Prefer screen copy_detail when the focused browser can yank a body
        # via the same path as ``y`` (no selection). Keeps Ctrl+C aligned with
        # multipane polish without always fighting the quit chord.
        screen = self.screen
        copy_detail = getattr(screen, "action_copy_detail", None)
        if callable(copy_detail):
            try:
                focused = getattr(screen, "focused", None)
                from .selectable_static import is_extractable_static

                if is_extractable_static(focused):
                    copy_detail()
                    return
            except Exception:
                logger.debug("Ctrl+C focused-body copy failed", exc_info=True)
        for key, active_binding in self.active_bindings.items():
            if active_binding.binding.action in ("quit", "app.quit"):
                self.notify(
                    t("ui-press-key-to-quit", key=key),
                    title=t("ui-want-to-quit-title"),
                )
                return

    class _BgStatus(Message):
        """Worker → UI: container status with a session_dir (thread-safe via post_message)."""

        def __init__(self, status: object) -> None:
            super().__init__()
            self.status = status

    class _BgFinished(Message):
        """Worker → UI: background run finished."""

        def __init__(self, run: BackgroundRun) -> None:
            super().__init__()
            self.run = run

    def __init__(
        self,
        traces_path: Path | None = None,
        work_dir: Path | None = None,
        *,
        config_path: Path | None = None,
        control_socket: Path | None = None,
        control_attach_only: bool = False,
        initial_session: Path | None = None,
        initial_prompt_index: int | None = None,
        **kwargs,
    ) -> None:
        setup_i18n()
        super().__init__(**kwargs)
        from ..paths import default_work_dir, resolve_work_and_traces, traces_root_for_reload

        self.traces_path: Path | None
        if work_dir is not None:
            self.work_dir = Path(work_dir).expanduser().resolve()
            self.traces_path = (
                Path(traces_path).expanduser().resolve()
                if traces_path is not None
                else self.work_dir / "runs" / "traces"
            )
        elif traces_path is not None:
            self.work_dir, self.traces_path = resolve_work_and_traces(traces_path)
        else:
            self.work_dir = default_work_dir()
            self.traces_path = None
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.run_manager = RunManager(self.work_dir)
        self.run_manager.add_finished_listener(self._on_background_run_finished)
        self.run_manager.add_status_listener(self._on_background_run_status)
        self._run_status_timer: Timer | None = None
        self._live_sessions_timer: Timer | None = None
        self._live_sessions_heartbeat_timer: Timer | None = None
        self._traces_watch: object | None = None
        self._live_sessions_busy = False
        self._live_meta_heartbeat_busy = False
        self._live_sessions_last_scan: float = 0.0
        # session_dir key → last session_trace_mtime seen on a live poll.
        self._session_mtimes: dict[str, float] = {}
        self._live_full_walk_last: float = 0.0
        self._share_notified: set[str] = set()
        self._populate_busy = False
        self._sessions_table_primed = False
        self._session_row_fp: dict[str, str] = {}
        self._exiting = False
        self._config_path = Path(config_path).expanduser() if config_path else None
        self._control_socket = (
            Path(control_socket).expanduser() if control_socket is not None else None
        )
        # True when attached to a live control owner (TUI never owns the socket).
        self._control_attached: bool = False
        # When true, load catalog via session/list and never bind the socket.
        self._control_attach_only: bool = bool(control_attach_only)
        self._control_notify_stop: asyncio.Event | None = None
        self._catalog_revision: int = 0
        self._initial_session = (
            Path(initial_session).expanduser().resolve() if initial_session is not None else None
        )
        self._initial_prompt_index = initial_prompt_index
        self._analysis_jobs_active: int = 0
        self._self_test_summary: str = ""
        self._copy_notify_at = 0.0
        self._copy_notify_msg = ""
        self._meta_only: list[tuple[SessionMeta, str]] = []
        self._plugin_results: dict[str, dict[str, AnalysisResult]] = {}
        # Keys are analysis_session_key(session_dir) (resolved path).
        # Bumps when a sessions catalog load starts; stale workers skip applying.
        self._sessions_load_gen: int = 0
        self._sessions_catalog_busy: bool = False
        self._sessions_reload_timer: Timer | None = None
        self._appearance_timer: Timer | None = None
        self._desktop_appearance: Appearance = "dark"
        self._applying_saved_theme = False
        self._pending_include_host: bool | None = None
        self._pending_sessions_reload_quiet: bool = False
        self._selected: set[str] = set()
        self._filter_model: str = ""
        self._session_search: str = ""
        self._delete_pending_paths: list[Path] | None = None
        self._delete_cursor_key: str | None = None
        self._delete_row_keys_snapshot: list[str] | None = None
        self._config: JsonObject = self._load_config()
        self._theme_persist = False
        register_brand_themes(self)
        early = str(self._config.get("theme") or "").strip() or "groket"
        self._desktop_appearance = appearance()
        try:
            self.theme = self._resolved_theme(early)
        except Exception:
            logger.debug(t("ui-failed-to-apply-saved-theme-r"), early)
        self._traces_root_for_reload = traces_root_for_reload
        self._resolved_keymap: Keymap | None = None
        self._leader_armed = False
        self._leader_timer: Timer | None = None
        self._apply_resolved_keymap()

    def compose(self) -> ComposeResult:
        yield AppChrome()
        with Vertical():
            yield Static("", id="session-summary")
            with Horizontal(id="session-filter-bar", classes=FILTER_BAR_CLASS):
                yield Static(U.filter_label(), classes=FILTER_LABEL_CLASS)
                yield Select(
                    [(U.all_models(), _SESSION_FILTER_ALL)],
                    value=_SESSION_FILTER_ALL,
                    id="session-model-select",
                    allow_blank=False,
                    classes=t("ui-field-select-session-filter-select"),
                )
                yield Input(
                    placeholder=U.search_sessions_placeholder(),
                    id="session-search-input",
                )
            yield DataTable(id="session-table")
        yield AppFooter()

    def _session_traces_root(self) -> Path:
        """Traces directory fixed for this process (CLI / constructor only)."""
        if self.traces_path:
            return Path(self.traces_path).expanduser()
        return Path(self.work_dir).expanduser() / "runs" / "traces"

    def _update_session_paths_banner(self) -> None:
        """Work traces always; host Grok sessions when the pref is on."""
        try:
            banner = self.query_one("#session-paths", Static)
        except Exception:
            return
        from .prefs import show_host_sessions_enabled

        work = self._runner_traces_root()
        # Rich markup stays in Python (Fluent treats [...] as variants).
        if show_host_sessions_enabled():
            from ..session.sources import host_grok_sessions_root

            banner.update(paths_banner(work, host_grok_sessions_root()))
        else:
            banner.update(paths_banner(work))

    def _load_config(self) -> JsonObject:
        """Load the canonical app config (defaults when the file is missing)."""
        from ..config import config_dump, load_app_config

        return config_dump(load_app_config(self._config_path))

    def _save_config(self) -> None:
        """Write shared prefs through :mod:`groket.config` (canonical object)."""
        from ..config import config_dump, load_app_config, update_app_config

        try:
            update_app_config(
                self._config_path,
                theme=str(self._config.get("theme") or "groket"),
                follow_os=self._config.get("follow_os") is True,
                show_host_sessions=bool(self._config.get("show_host_sessions")),
                auto_serve=self._config.get("auto_serve") is not False,
            )
        except OSError:
            logger.warning(
                t("ui-failed-to-write-prefs-to-s"),
                app_config_path(),
                exc_info=True,
            )
            return
        self._config = config_dump(load_app_config(self._config_path))

    def _theme_names(self) -> list[str]:
        reg = getattr(self, "available_themes", None) or {}
        try:
            return sorted(reg.keys())
        except Exception:
            return []

    def _follow_os(self) -> bool:
        return self._config.get("follow_os") is True

    def _resolved_theme(self, pref: str) -> str:
        if self._follow_os():
            return resolve_theme(pref, self._desktop_appearance)
        return pref

    def apply_saved_theme(self, *, save: bool = False) -> str | None:
        """Restore theme from config.toml (or keep current). Re-applied after refresh.

        Textual can reset ``self.theme`` during App/mount; setting only once in
        ``on_mount`` is unreliable. ``follow_os`` may pick a pair member;
        an explicit theme pick writes ``follow_os: false`` and is left alone.
        """
        pref = str(self._config.get("theme") or "").strip() or self.theme
        names = set(self._theme_names())
        self._desktop_appearance = appearance()
        name = self._resolved_theme(pref)
        if name not in names:
            if not names:
                return None
            name = self.theme if self.theme in names else next(iter(sorted(names)))
        self._applying_saved_theme = True
        try:
            self.theme = name
        except Exception:
            return None
        finally:
            self._applying_saved_theme = False
        if save:
            self._save_config()
        return name

    def _enable_theme_persist(self) -> None:
        """Re-apply saved theme, then persist any later theme changes to disk.

        Covers Ctrl+P → Change theme and any other path that sets ``App.theme``.
        Subscribe only while the app is running — ``call_after_refresh`` can
        fire after a short Pilot unmount.
        """
        self.apply_saved_theme(save=False)
        if self._theme_persist:
            return
        if not getattr(self, "is_running", False):
            return
        self._theme_persist = True
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        if self._follow_os() and self._appearance_timer is None:
            self._appearance_timer = self.set_interval(2.0, self._follow_desktop_appearance)

    def _follow_desktop_appearance(self) -> None:
        """Repaint when the host light/dark setting changes (``follow_os`` only)."""
        if not self._follow_os():
            return
        if appearance() != self._desktop_appearance:
            self.apply_saved_theme(save=False)

    def _on_theme_changed(self, theme: Theme) -> None:
        """Persist an explicit theme pick. Clears ``follow_os`` so the OS cannot override it."""
        if not self._theme_persist or self._applying_saved_theme:
            return
        name = (theme.name or self.theme or "").strip()
        if not name:
            return
        if self._config.get("theme") == name and self._config.get("follow_os") is False:
            return
        self._config["theme"] = name
        self._config["follow_os"] = False
        self._save_config()

    def _apply_resolved_keymap(self) -> None:
        """Apply ``keys.toml`` remaps via Textual ``set_keymap``.

        A refused or missing overlay leaves catalog defaults (``load_keymap``).
        Sequence chords are unbound here and dispatched by the leader prefix.
        """
        from groket.keys import load_keymap, textual_keymap

        keymap = load_keymap()
        self._resolved_keymap = keymap
        self.set_keymap(textual_keymap(keymap))
        if keymap.leader:
            self.bind(
                keymap.leader,
                "leader_idle",
                description=t("ui-leader"),
                show=True,
                key_display=keymap.leader,
            )

    def _leader_editing_focus(self) -> bool:
        """True when a typing field owns the key (Input / TextArea / notes)."""
        focused = self.focused
        return isinstance(focused, (Input, TextArea))

    def _leader_disarm(self) -> None:
        if self._leader_timer is not None:
            self._leader_timer.stop()
            self._leader_timer = None
        if self._leader_armed:
            self._leader_armed = False
            self.refresh_bindings()

    def _leader_arm(self) -> None:
        keymap = self._resolved_keymap
        timeout_ms = 800
        if keymap is not None and keymap.leader_timeout_ms:
            timeout_ms = keymap.leader_timeout_ms
        self._leader_disarm()
        self._leader_armed = True
        self._leader_timer = self.set_timer(timeout_ms / 1000.0, self._leader_disarm)
        self.refresh_bindings()

    def _leader_event_suffix(self, event: object) -> str:
        character = getattr(event, "character", None)
        if isinstance(character, str) and character:
            return character
        key = str(getattr(event, "key", "") or "")
        if key.startswith("shift+") and len(key) > 6:
            return key
        return key

    def _leader_is_leader_key(self, event: object) -> bool:
        keymap = self._resolved_keymap
        if keymap is None or not keymap.leader:
            return False
        leader = keymap.leader
        character = getattr(event, "character", None)
        if isinstance(character, str) and character == leader:
            return True
        key = str(getattr(event, "key", "") or "")
        if key == leader:
            return True
        punct = {";": "semicolon", "semicolon": ";"}
        if punct.get(key) == leader or punct.get(leader) == key:
            return True
        from groket.keys import normalize_chord

        return normalize_chord(key) == normalize_chord(leader)

    async def _run_binding_id(self, action_id: str) -> None:
        """Dispatch *action_id* from the screen chain (home vs browser action)."""
        chain = getattr(self.screen, "_modal_binding_chain", ())
        for namespace, bindings in chain:
            for _key, binding in bindings:
                if getattr(binding, "id", None) != action_id:
                    continue
                if await self.run_action(binding.action, namespace):
                    return

    async def _handle_leader_key(self, event: object) -> bool:
        """Consume a leader prefix or ``leader+X`` dispatch. True when handled."""
        keymap = self._resolved_keymap
        if keymap is None or not keymap.leader:
            return False
        if self._leader_editing_focus():
            if self._leader_armed:
                self._leader_disarm()
            return False
        key = str(getattr(event, "key", "") or "")
        if key in {"escape", "esc"}:
            if self._leader_armed:
                self._leader_disarm()
                return True
            return False
        if self._leader_armed:
            self._leader_disarm()
            if self._leader_is_leader_key(event):
                return True
            suffix = self._leader_event_suffix(event)
            action_id = keymap.lookup_sequence(suffix)
            if action_id is not None:
                await self._run_binding_id(action_id)
            return True
        if self._leader_is_leader_key(event):
            self._leader_arm()
            return True
        return False

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.Key) and await self._handle_leader_key(event):
            event.stop()
            event.prevent_default()
            return
        await super().on_event(event)

    def action_leader_idle(self) -> None:
        """Footer slot while the leader is armed; dispatch is in on_event."""
        return

    def on_mount(self) -> None:
        self._apply_resolved_keymap()
        try:
            from ..runs.personas import PersonaStore

            PersonaStore(self.work_dir).ensure_defaults()
        except Exception:
            logger.debug(t("ui-personastore-initialization-failed"), exc_info=True)
        self.apply_saved_theme(save=False)
        self.call_after_refresh(self._enable_theme_persist)
        table = self.query_one("#session-table", DataTable)
        style_data_table(table)
        table.add_columns(
            " ",
            t("ui-origin"),
            t("ui-title"),
            t("ui-model"),
            t("ui-status"),
            t("ui-duration"),
            t("ui-context"),
            t("ui-events"),
            t("ui-findings-1"),
        )
        self.sub_title = ""
        try:
            (self.work_dir / "runs" / "traces").mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._update_session_paths_banner()
        work = self._runner_traces_root()
        try:
            work.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        # Attach-only: start control client first so home list loads via RPC.
        self._start_control_service()
        self._load_sessions(include_host=None)
        table.focus()
        self._schedule_live_sessions_poll()
        if self._initial_session is not None:
            self.call_after_refresh(
                self.open_session_path,
                self._initial_session,
                prompt_index=self._initial_prompt_index,
            )

    def _start_control_service(self) -> None:
        """Try attach to the control owner; the TUI never binds the socket.

        Does **not** mark attached until :meth:`_attach_control_client` succeeds
        at ``initialize``. Catalog load uses control only after that.
        """
        if self._control_socket is None:
            return
        # Intent: prefer control catalog when attach succeeds (never own socket).
        self._control_attach_only = True
        self._control_attached = False
        self.run_worker(
            self._attach_control_client(),
            name="editor-control-attach",
            group="editor-control-service",
            exclusive=True,
        )

    async def _attach_control_client(self) -> None:
        """Confirm the live owner, then start notify + switch catalog to control.

        On initialize failure, leave ``_control_attached`` false and toast.
        Catalog stays empty until attach succeeds (no disk fallback).
        """
        if self._control_socket is None:
            return
        ok = await self._confirm_control_attach()
        if not ok:
            self._control_attached = False
            with suppress(Exception):
                self.notify(
                    t("ui-control-socket-attach-failed"),
                    severity="error",
                    timeout=8,
                )
            return
        self._control_attached = True
        stop = asyncio.Event()
        self._control_notify_stop = stop
        # Separate long-lived worker so attach itself can finish cleanly.
        self.run_worker(
            self._control_notify_loop(stop),
            name="editor-control-notify",
            group="editor-control-notify",
            exclusive=True,
        )
        # First on_mount catalog is empty until attach; reload quietly.
        self._load_sessions(include_host=None, quiet=True)

    async def _control_notify_loop(self, stop: asyncio.Event) -> None:
        """Background: stay connected for session/notes/analysis notifies."""
        if self._control_socket is None:
            return
        await listen_control_notifications(
            self._control_socket,
            self._on_control_notification,
            client_name="groket-tui-notify",
            stop=stop,
        )

    async def _on_control_notification(self, method: str, params: JsonObject) -> None:
        """Handle serve-side notify (session/selected, changed, notes, analysis)."""
        from ..models import json_as_int

        if self._exiting:
            return
        if method == "session/selected":
            sid = json_as_str(params.get("sessionId")).strip()
            if not sid:
                return
            raw_pi = params.get("promptIndex")
            prompt_index = None if raw_pi is None else json_as_int(raw_pi)
            # Resolve id → path via catalog rows or traces root.
            path = self._resolve_session_id_for_control(sid)
            if path is None:
                return
            self.call_later(
                self.open_session_path,
                path,
                prompt_index=prompt_index,
                notify_control=False,
            )
            return
        if method == "session/changed":
            sid = json_as_str(params.get("sessionId")).strip()
            self.call_later(self._control_session_changed_ui, sid)
            return
        if method == "notes/changed":
            sid = json_as_str(params.get("sessionId")).strip()
            self.call_later(self._control_notes_changed_ui, sid)
            return
        if method == "analysis/changed":
            sid = json_as_str(params.get("sessionId")).strip()
            self.call_later(self._control_analysis_changed_ui, sid)
            return

    def _resolve_session_id_for_control(self, session_id: str) -> Path | None:
        """Map a session id from control notify to a local directory."""
        for meta, _label in self._meta_only:
            if session_id in (meta.session_id, meta.session_dir.name):
                return meta.session_dir
        for root in self._session_catalog_roots():
            candidate = root.path / session_id
            if candidate.is_dir():
                return candidate
        return None

    def _control_session_changed_ui(self, session_id: str) -> None:
        """Refresh home list and open browser when the changed session is open."""
        # Empty sessionId: catalog rebuild finished (cold serve warm).
        self._schedule_sessions_reload(quiet=True)
        screen = self.screen
        if isinstance(screen, BrowserScreen) and session_id:
            try:
                if screen.session_dir.name == session_id:
                    screen._live_refresh_from_fs(heartbeat=False)
            except Exception:
                logger.debug("browser refresh on session/changed failed", exc_info=True)

    def _control_notes_changed_ui(self, session_id: str) -> None:
        screen = self.screen
        if not isinstance(screen, BrowserScreen) or not session_id:
            return
        try:
            if screen.session_dir.name == session_id:
                screen._load_notes()
                screen._update_reports_tab()
        except Exception:
            logger.debug("notes refresh on notes/changed failed", exc_info=True)

    def _control_analysis_changed_ui(self, session_id: str) -> None:
        """Reload cache into the open browser when analysis finishes on the owner."""
        screen = self.screen
        if not isinstance(screen, BrowserScreen) or not session_id:
            return
        try:
            if screen.session_dir.name != session_id:
                return
            cached = self._analysis_svc().load_cached_all(screen.session_dir, allow_stale=True)
            if not cached:
                return
            from ..analysis.inflight import analysis_session_key

            self._plugin_results[analysis_session_key(screen.session_dir)] = cached
            # Clears pending/loading; Report was left blank under overlays before.
            screen.apply_analysis_results(cached)
        except Exception:
            logger.debug("analysis refresh on analysis/changed failed", exc_info=True)

    def _push_analysis_results_to_open_browser(
        self, session_dir: Path, results: dict[str, AnalysisResult]
    ) -> None:
        """If the open browser is *session_dir*, apply finished plugin results."""
        screen = self.screen
        if not isinstance(screen, BrowserScreen):
            return
        try:
            from ..analysis.inflight import analysis_session_key

            if analysis_session_key(screen.session_dir) != analysis_session_key(session_dir):
                return
            screen.apply_analysis_results(results)
        except Exception:
            logger.debug("push analysis results to open browser failed", exc_info=True)

    def is_control_client(self) -> bool:
        """True only after successful control ``initialize`` against a live owner."""
        return bool(self._control_attached and self._control_socket is not None)

    def is_control_owner(self) -> bool:
        """Always false: headless ``groket serve`` is the sole socket owner."""
        return False

    def control_client(self) -> ControlClient | None:
        """Return a client for the control socket when configured."""
        if self._control_socket is None:
            return None
        return ControlClient(
            self._control_socket,
            client_name="groket-tui",
            timeout=HEAVY_RPC_TIMEOUT,
        )

    def session_access(self) -> RemoteSessionAccess | None:
        """Remote façade over the control owner (None when socket disabled)."""
        client = self.control_client()
        if client is None:
            return None
        return RemoteSessionAccess(client)

    async def control_session_list(
        self,
        *,
        query: str = "",
        limit: int | None = None,
    ) -> JsonObject:
        """Session catalog via control ``session/list`` (same path as HUD/editors)."""
        access = self.session_access()
        if access is None:
            return {"sessions": [], "total": 0, "matched": 0}
        return await access.list_sessions(query=query, limit=limit)

    async def _confirm_control_attach(self) -> bool:
        """Verify the live owner speaks our protocol.

        :returns: True when ``initialize`` succeeds; False when the socket is
            missing, dead, or the RPC fails.
        """
        client = self.control_client()
        if client is None:
            return False
        try:
            from ..integrations.control import PROTOCOL_VERSION, protocol_compatible

            result = await client.initialize()
            ver = result.get("protocolVersion")
            if not protocol_compatible(ver):
                logger.warning(
                    "Control owner at %s speaks protocol %s (need major of %s)",
                    self._control_socket,
                    ver,
                    PROTOCOL_VERSION,
                )
                return False
            logger.info(
                "Attached to control owner at %s (protocol %s)",
                self._control_socket,
                ver,
            )
            return True
        except Exception:
            logger.warning(
                "Control attach initialize failed at %s",
                self._control_socket,
                exc_info=True,
            )
            return False

    def control_session_selected(
        self,
        session_dir: Path,
        prompt_index: int | None,
    ) -> None:
        """TUI selection notify (serve broadcasts when notify RPC lands)."""
        _ = (session_dir, prompt_index)

    def control_session_changed(self, session_dir: Path) -> None:
        """TUI change notify (serve owns broadcast; no-op as client)."""
        _ = session_dir

    def control_notes_changed(self, session_dir: Path) -> None:
        """TUI notes notify (serve owns broadcast; no-op as client)."""
        _ = session_dir

    _CACHE_FILE = META_CACHE_FILENAME

    def _session_catalog_roots(self):
        """Work traces always; host Grok tree when the pref (or CLI host path) says so."""
        return self._catalog_roots_for_load(include_host=None)

    def _cache_roots_key(self) -> str:
        parts = [f"{r.origin}:{r.path}" for r in self._session_catalog_roots()]
        return "|".join(parts)

    def _load_meta_cache(self) -> dict[str, dict]:
        """Load cached session metadata keyed by resolved session_dir path."""
        cache_file = self.work_dir / self._CACHE_FILE
        if not cache_file.exists():
            return {}
        try:
            data = json.loads(cache_file.read_text())
            if data.get("roots") != self._cache_roots_key():
                return {}
            raw = data.get("sessions", {})
            return raw if isinstance(raw, dict) else {}
        except (json.JSONDecodeError, KeyError, TypeError):
            return {}

    def _save_meta_cache(self, entries: list[tuple[SessionMeta, str]]) -> None:
        """Write session metadata cache to disk."""
        from ..parser import session_trace_mtime

        sessions_cache: dict[str, JsonValue] = {}
        cache: JsonObject = {"roots": self._cache_roots_key(), "sessions": sessions_cache}
        for meta, label in entries:
            key = str(meta.session_dir.resolve())
            try:
                tm = float(session_trace_mtime(Path(meta.session_dir)))
            except Exception:
                tm = 0.0
            sessions_cache[key] = {
                "session_id": meta.session_id,
                "model_id": meta.model_id,
                "reasoning_effort": meta.reasoning_effort,
                "title": meta.title,
                "created_at": meta.created_at,
                "num_events": meta.num_events,
                "trace_mtime": tm,
                "duration_seconds": meta.duration_seconds,
                "context_window_usage_pct": meta.context_window_usage_pct,
                "context_tokens_used": meta.context_tokens_used,
                "context_window_tokens": meta.context_window_tokens,
                "compaction_count": meta.compaction_count,
                "total_tokens_before_compaction": meta.total_tokens_before_compaction,
                "task_id": meta.task_id,
                "run_id": meta.run_id,
                "origin": meta.origin,
                "git_repo": meta.git_repo,
                "git_branch": meta.git_branch,
                "label": label,
            }
        try:
            (self.work_dir / self._CACHE_FILE).write_text(json.dumps(cache, indent=2))
        except Exception:
            pass

    def _origin_for_dir(self, session_dir: Path) -> str:
        """Eval vs Host from the directory, not a stored default."""
        from ..session.sources import classify_session_origin, work_traces_root

        return classify_session_origin(
            session_dir,
            work_traces=work_traces_root(self.work_dir),
        )

    def _label_for_session(self, session_dir: Path, origin: str) -> str:
        """Display path fragment relative to the catalog root for *origin*."""
        from ..session.sources import ORIGIN_HOST, host_grok_sessions_root, work_traces_root

        root = (
            host_grok_sessions_root() if origin == ORIGIN_HOST else work_traces_root(self.work_dir)
        )
        return self._derive_label(session_dir, root)

    def _begin_sessions_load(self) -> int:
        """Mark a new catalog load; return generation for stale-worker checks."""
        self._sessions_load_gen += 1
        self._sessions_catalog_busy = True
        return self._sessions_load_gen

    def _sessions_load_current(self, gen: int) -> bool:
        return gen == self._sessions_load_gen

    def _finish_sessions_load(self, gen: int) -> None:
        """Clear the catalog-loading flag when *gen* is still the active load."""
        if self._sessions_load_current(gen):
            self._sessions_catalog_busy = False

    def _schedule_sessions_reload(self, *, delay: float = 0.15, quiet: bool = False) -> None:
        """Debounce catalog reloads; snapshot host-pref for the pending fire.

        :param quiet: Skip scan/loaded toasts. A later loud request wins.
        """
        pending_quiet = True
        if self._sessions_reload_timer is not None:
            with suppress(Exception):
                self._sessions_reload_timer.stop()
            self._sessions_reload_timer = None
            pending_quiet = bool(self._pending_sessions_reload_quiet)
        self._pending_sessions_reload_quiet = bool(quiet and pending_quiet)
        from .prefs import show_host_sessions_enabled

        self._pending_include_host = show_host_sessions_enabled()
        self._sessions_reload_timer = self.set_timer(delay, self._fire_sessions_reload)

    def _fire_sessions_reload(self) -> None:
        self._sessions_reload_timer = None
        if self._exiting:
            return
        quiet = bool(self._pending_sessions_reload_quiet)
        self._pending_sessions_reload_quiet = False
        include_host = self._pending_include_host
        if include_host is None:
            from .prefs import show_host_sessions_enabled

            include_host = show_host_sessions_enabled()
        self._load_sessions(include_host=bool(include_host), quiet=quiet)

    def _drop_host_session_rows(self) -> None:
        """Drop host-origin rows without waiting for a full rescan."""
        from ..session.sources import ORIGIN_HOST, ORIGIN_WORK

        kept = [
            (m, lab)
            for m, lab in self._meta_only
            if (m.origin or ORIGIN_WORK).strip().lower() != ORIGIN_HOST
        ]
        if len(kept) == len(self._meta_only):
            return
        self._meta_only = kept
        with suppress(Exception):
            self._rebuild_session_filters()
            self._populate_session_table(force=True)

    def _build_session_meta_rows(
        self,
        unique: list[tuple[Path, str]],
        cache: dict[str, dict],
        *,
        gen: int | None = None,
    ) -> tuple[list[tuple[SessionMeta, str]], list[int]]:
        """Build list metas for *unique* dirs; host rows skip events parse.

        :returns: ``(rows, need_timeline_indices)`` for eval rows only.
        """
        from ..parser import load_session_meta_list, session_trace_mtime
        from ..session.sources import ORIGIN_HOST

        rows: list[tuple[SessionMeta, str]] = []
        need_idx: list[int] = []
        for sd, origin in unique:
            if gen is not None and not self._sessions_load_current(gen):
                return rows, need_idx
            try:
                key = str(sd.resolve())
            except OSError:
                key = str(sd)
            try:
                mtime = float(session_trace_mtime(sd))
            except Exception:
                mtime = 0.0
            cached = cache.get(key) if isinstance(cache.get(key), dict) else None
            cached_n: int | None = None
            if isinstance(cached, dict):
                try:
                    if float(cached.get("trace_mtime") or -1) == mtime:
                        ne = cached.get("num_events")
                        cached_n = int(ne) if ne is not None else None
                        if cached_n is None:
                            raise ValueError("missing num_events")
                except (TypeError, ValueError, KeyError):
                    cached_n = None
            try:
                meta = load_session_meta_list(sd, origin=origin)
                if cached_n is not None:
                    meta.num_events = cached_n
                    need_count = False
                else:
                    need_count = origin != ORIGIN_HOST
            except Exception:
                logger.debug(t("ui-failed-to-load-session-meta-for-s"), sd, exc_info=True)
                continue
            meta.origin = origin
            label = self._label_for_session(sd, origin)
            rows.append((meta, label))
            if need_count:
                need_idx.append(len(rows) - 1)
        return rows, need_idx

    @staticmethod
    def _fill_timeline_counts(rows: list[tuple[SessionMeta, str]], need_idx: list[int]) -> bool:
        """Set ``num_events`` on *rows* at *need_idx* via parse_timeline. Returns if any updated."""
        from ..parser import parse_timeline
        from ..session.sources import ORIGIN_HOST

        updated = False
        for idx in need_idx:
            if idx < 0 or idx >= len(rows):
                continue
            meta, label = rows[idx]
            if (meta.origin or "").strip().lower() == ORIGIN_HOST:
                continue
            try:
                meta.num_events = len(parse_timeline(Path(meta.session_dir)))
            except Exception:
                meta.num_events = 0
            rows[idx] = (meta, label)
            updated = True
        return updated

    def _apply_session_meta_rows(
        self, gen: int, rows: list[tuple[SessionMeta, str]], *, clear_plugins: bool = True
    ) -> bool:
        """Install *rows* if *gen* is still current. Returns False when superseded."""
        if not self._sessions_load_current(gen):
            return False
        self._meta_only = rows
        if clear_plugins:
            self._plugin_results = {}
        return True

    def _load_sessions_sync(self, root: Path | None = None) -> int:
        """Load session metas into ``_meta_only`` (any thread; no UI calls).

        Avoids parsing every ``updates.jsonl`` on launch when a mtime-matching
        ``num_events`` is already in the meta cache (still coalesced timeline
        counts — not file-size estimates). Cache misses get a deferred
        :func:`~groket.parser.parse_timeline` pass so the UI can paint first.

        *root* is ignored (catalog roots come from work + optional host).

        :returns: Number of sessions loaded (0 if none found after a full scan).
        """
        _ = root
        from ..session.sources import collect_session_dirs

        gen = self._begin_sessions_load()
        try:
            unique = collect_session_dirs(self._session_catalog_roots())
            if not unique:
                if self._apply_session_meta_rows(gen, []):
                    self._save_meta_cache([])
                return 0
            cache = self._load_meta_cache()
            rows, need_idx = self._build_session_meta_rows(unique, cache, gen=gen)
            if not self._sessions_load_current(gen):
                return 0
            # List paint first; work event counts optional for sync path.
            if not self._apply_session_meta_rows(gen, rows):
                return 0
            self._fill_timeline_counts(rows, need_idx)
            if self._sessions_load_current(gen):
                self._save_meta_cache(rows)
            return len(rows)
        finally:
            self._finish_sessions_load(gen)

    def _catalog_roots_for_load(self, *, include_host: bool | None = None):
        """Build scan roots; *include_host* overrides the pref when set (toggle path)."""
        from ..session.sources import is_host_grok_sessions_root, session_scan_roots
        from .prefs import show_host_sessions_enabled

        if include_host is None:
            include_host = show_host_sessions_enabled()
        traces = self.traces_path
        if traces is not None and is_host_grok_sessions_root(Path(traces)):
            include_host = True
        return session_scan_roots(
            self.work_dir,
            traces_path=Path(traces) if traces is not None else None,
            include_host=bool(include_host),
        )

    def _fetch_control_catalog_sync(
        self,
        *,
        query: str = "",
        since_revision: int = 0,
        drain: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> JsonObject:
        """Blocking ``session/list`` (one page, delta poll, or full drain)."""

        from ..integrations.control_client import ControlClient

        sock = self._control_socket
        if sock is None:
            return {
                "sessions": [],
                "total": 0,
                "matched": 0,
                "revision": 0,
                "unchanged": False,
                "removed": [],
                "delta": False,
            }

        async def _run() -> JsonObject:
            client = ControlClient(sock, client_name="groket-tui", timeout=HEAVY_RPC_TIMEOUT)
            if since_revision > 0 and not drain:
                return await client.session_list(
                    query=query,
                    limit=limit if limit is not None else 10_000,
                    offset=offset,
                    since_revision=since_revision,
                )
            if drain:
                return await client.session_list_all(query=query)
            return await client.session_list(
                query=query,
                limit=DEFAULT_SESSION_LIST_LIMIT if limit is None else limit,
                offset=offset,
            )

        return asyncio.run(_run())

    def _rows_from_catalog_wire(self, wire_rows: list[JsonObject]) -> list[tuple[SessionMeta, str]]:
        from ..session.catalog import session_meta_from_catalog_row

        rows: list[tuple[SessionMeta, str]] = []
        for raw in wire_rows:
            meta = session_meta_from_catalog_row(raw)
            if meta is None:
                continue
            label = str(raw.get("label") or meta.label)
            rows.append((meta, label))
        return rows

    def _merge_control_catalog_rows(
        self,
        incoming: list[JsonObject],
        removed: list[str],
    ) -> list[tuple[SessionMeta, str]]:
        drop = {sid for sid in removed if sid}
        merged = [
            (meta, label)
            for meta, label in self._meta_only
            if meta.session_id not in drop and meta.session_dir.name not in drop
        ]
        by_id = {meta.session_id: i for i, (meta, _label) in enumerate(merged)}
        for meta, label in self._rows_from_catalog_wire(incoming):
            idx = by_id.get(meta.session_id)
            if idx is None:
                by_id[meta.session_id] = len(merged)
                merged.append((meta, label))
            else:
                merged[idx] = (meta, label)
        return merged

    def _await_complete_catalog(self, gen: int, first: JsonObject) -> JsonObject:
        """Poll ``session/list`` until the owner scan finishes (or timeout).

        First paint already happened. This runs on the catalog worker so the
        UI stays interactive while serve warms a cold tree.
        """
        result = first
        deadline = time.monotonic() + 120.0
        while bool(result.get("incomplete") or result.get("building")):
            if not self._sessions_load_current(gen):
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return result
            time.sleep(min(0.15, remaining))
            page = first_home_list_fetch()
            result = self._fetch_control_catalog_sync(
                since_revision=int(page["since_revision"]),
                drain=bool(page["drain"]),
                limit=int(page["limit"]),
                offset=int(page["offset"]),
            )
        return result

    def _fill_remaining_catalog_pages(self, gen: int, listed: JsonObject, offset: int) -> None:
        """Fetch later ``session/list`` pages after first paint. Never drains."""
        page = int(DEFAULT_SESSION_LIST_LIMIT)
        raw = listed.get("sessions")
        batch_len = len(raw) if isinstance(raw, list) else 0
        matched_raw = listed.get("matched")
        matched = matched_raw if isinstance(matched_raw, int) else 0
        stalled = bool(listed.get("incomplete") or listed.get("building"))
        while True:
            nxt = catalog_list_next_offset(offset, batch_len, page, matched, stalled=stalled)
            if nxt is None or not self._sessions_load_current(gen):
                return
            nxt_listed = self._fetch_control_catalog_sync(drain=False, limit=page, offset=nxt)
            nxt_raw = nxt_listed.get("sessions")
            wire = (
                [as_json_object(r) for r in nxt_raw if isinstance(r, dict)]
                if isinstance(nxt_raw, list)
                else []
            )
            if not wire:
                return
            rows = self._merge_control_catalog_rows(wire, [])
            if not self._apply_session_meta_rows(gen, rows, clear_plugins=False):
                return
            call_ui(self, self._rebuild_session_filters)
            call_ui(self, self._populate_session_table, force=True)
            rev_raw = nxt_listed.get("revision")
            if isinstance(rev_raw, int) and rev_raw > 0:
                self._catalog_revision = rev_raw
            offset = nxt
            batch_len = len(wire)
            nxt_matched = nxt_listed.get("matched")
            if isinstance(nxt_matched, int):
                matched = nxt_matched
            stalled = bool(nxt_listed.get("incomplete") or nxt_listed.get("building"))

    def _load_sessions_via_control(
        self,
        gen: int,
        *,
        quiet: bool = False,
        clear_plugins: bool = True,
    ) -> None:
        """Populate home list from control ``session/list`` (attach client path).

        Quiet/live polls send ``sinceRevision`` so an unchanged owner returns no
        rows and the table is not rebuilt.

        :param quiet: Skip loaded/error notifications (live refresh / attach).
        :param clear_plugins: When false, keep analysis results for known paths.
        """
        try:
            since = int(self._catalog_revision or 0)
            use_delta = bool(quiet and since > 0)
            if use_delta:
                result = self._fetch_control_catalog_sync(
                    since_revision=since,
                    drain=False,
                )
            else:
                first = first_home_list_fetch()
                result = self._fetch_control_catalog_sync(
                    since_revision=int(first["since_revision"]),
                    drain=bool(first["drain"]),
                    limit=int(first["limit"]),
                    offset=int(first["offset"]),
                )
            if not self._sessions_load_current(gen):
                return
            rev_raw = result.get("revision")
            same_rev = isinstance(rev_raw, int) and rev_raw == since
            if result.get("unchanged") and same_rev:
                return
            is_delta = bool(result.get("delta")) and isinstance(rev_raw, int) and rev_raw > 0
            if use_delta and not is_delta:
                result = self._fetch_control_catalog_sync(drain=True)
                if not self._sessions_load_current(gen):
                    return
                rev_raw = result.get("revision")
                is_delta = False
            if isinstance(rev_raw, int) and rev_raw > 0:
                self._catalog_revision = rev_raw
            raw = result.get("sessions")
            wire_rows = (
                [as_json_object(r) for r in raw if isinstance(r, dict)]
                if isinstance(raw, list)
                else []
            )
            removed_raw = result.get("removed")
            removed = (
                [str(x) for x in removed_raw if str(x)] if isinstance(removed_raw, list) else []
            )
            if is_delta:
                rows = self._merge_control_catalog_rows(wire_rows, removed)
                replace_plugins = False
            else:
                rows = self._rows_from_catalog_wire(wire_rows)
                replace_plugins = clear_plugins
            if not self._apply_session_meta_rows(gen, rows, clear_plugins=replace_plugins):
                return
            n = len(rows)
            call_ui(self, self._rebuild_session_filters)
            call_ui(self, self._populate_session_table, force=True)
            if bool(result.get("incomplete") or result.get("building")):
                result = self._await_complete_catalog(gen, result)
                if not self._sessions_load_current(gen):
                    return
                rev_raw = result.get("revision")
                if isinstance(rev_raw, int) and rev_raw > 0:
                    self._catalog_revision = rev_raw
                raw = result.get("sessions")
                wire_rows = (
                    [as_json_object(r) for r in raw if isinstance(r, dict)]
                    if isinstance(raw, list)
                    else []
                )
                rows = self._rows_from_catalog_wire(wire_rows)
                if not self._apply_session_meta_rows(gen, rows, clear_plugins=False):
                    return
                n = len(rows)
                call_ui(self, self._rebuild_session_filters)
                call_ui(self, self._populate_session_table, force=True)
            if not use_delta:
                self._fill_remaining_catalog_pages(gen, result, int(first["offset"]))
                n = len(self._meta_only)
            if not quiet:
                call_ui(
                    self,
                    self.notify,
                    t("notify-loaded-sessions", n=n),
                    severity="information",
                )
        except Exception as exc:
            logger.exception("control session/list failed for attach catalog")
            if not quiet:
                call_ui(
                    self,
                    self.notify,
                    control_operator_text(exc, fallback_id="notify-control-list-failed"),
                    severity="error",
                )
        finally:
            call_ui(self, self._finish_sessions_load, gen)

    @work(thread=True, exclusive=True, group="sessions-catalog")
    def _load_sessions(
        self,
        root: Path | None = None,
        *,
        include_host: bool | None = None,
        quiet: bool = False,
    ) -> None:
        """Load the home session list.

        Normal product path (control socket configured): only ``session/list``
        after a successful attach. Offline (``control_socket`` None / --no-serve):
        walk local work/traces. No silent dual path when attach is intended.

        :param quiet: Skip scan/loaded toasts (live refresh / attach).
        """
        _ = root
        gen = self._begin_sessions_load()
        if self._control_socket is not None:
            if self._control_attached:
                self._load_sessions_via_control(gen, quiet=quiet)
                return
            # Socket configured but not yet attached: do not scan disk (would
            # reintroduce a second catalog stack). Empty list until attach or error.
            if not self._sessions_load_current(gen):
                return
            if self._apply_session_meta_rows(gen, []):
                call_ui(self, self._rebuild_session_filters)
                call_ui(self, self._populate_session_table, force=True)
            call_ui(self, self._finish_sessions_load, gen)
            return
        roots = self._catalog_roots_for_load(include_host=include_host)
        scan_desc = ", ".join(str(r.path) for r in roots)
        try:
            if not quiet:
                call_ui(
                    self,
                    self.notify,
                    t("notify-scanning", path=scan_desc),
                    severity="information",
                )
            from ..session.sources import collect_session_dirs

            unique = collect_session_dirs(roots)
            if not self._sessions_load_current(gen):
                return
            if not unique:
                if self._apply_session_meta_rows(gen, []):
                    self._save_meta_cache([])
                    call_ui(self, self._rebuild_session_filters)
                    call_ui(self, self._populate_session_table, force=True)
                    if not quiet:
                        call_ui(
                            self,
                            self.notify,
                            t("notify-no-sessions", path=scan_desc),
                            severity="warning",
                        )
                return

            cache = self._load_meta_cache()
            rows, need_idx = self._build_session_meta_rows(unique, cache, gen=gen)
            if not self._sessions_load_current(gen):
                return
            if not self._apply_session_meta_rows(gen, rows):
                return
            n = len(rows)
            call_ui(self, self._rebuild_session_filters)
            call_ui(self, self._populate_session_table, force=True)
            if not quiet:
                call_ui(
                    self,
                    self.notify,
                    t("notify-loaded-sessions", n=n),
                    severity="information",
                )

            if need_idx and self._sessions_load_current(gen):
                if self._fill_timeline_counts(rows, need_idx) and self._sessions_load_current(gen):
                    self._save_meta_cache(rows)
                    call_ui(self, self._populate_session_table, force=True)
                elif self._sessions_load_current(gen):
                    self._save_meta_cache(rows)
            elif self._sessions_load_current(gen):
                self._save_meta_cache(rows)
        finally:
            call_ui(self, self._finish_sessions_load, gen)

    def _analysis_svc(self) -> AnalysisService:
        """Lazy process-wide service; constructed on first Analyze / settings."""
        from ..analysis.service import get_analysis_service

        return get_analysis_service(
            self.work_dir,
            traces=Path(self.traces_path) if self.traces_path else None,
            config_path=self._config_path,
        )

    def _analyze_one(
        self,
        meta: SessionMeta,
        label: str,
        *,
        hold_inflight: bool = False,
        force: bool = False,
    ) -> None:
        """Analyze a single session with all plugins. Must be called from a worker thread.

        :param hold_inflight: When True, caller already called
            :func:`~groket.analysis.inflight.try_begin_session_analysis` and will
            :func:`~groket.analysis.inflight.end_session_analysis` in its ``finally``.
        :param force: When True, re-run even when cache is warm (and run deferred
            plugins). Default False avoids slow deferred work on bulk refresh.
        """
        from ..analysis.inflight import (
            analysis_session_key,
            end_session_analysis,
            try_begin_session_analysis,
        )

        _ = label
        sd_key = analysis_session_key(meta.session_dir)
        if not force and sd_key in self._plugin_results:
            return
        acquired = hold_inflight
        if not acquired and not try_begin_session_analysis(meta.session_dir):
            return
        try:
            self._plugin_results[sd_key] = self._analysis_svc().analyze_all(
                meta.session_dir, force=force
            )
        except Exception as exc:
            logger.warning(t("ui-analysis-failed-for-s-s"), sd_key, exc)
            self._plugin_results[sd_key] = {}
        else:
            # Offline home/batch path: control does not emit analysis/changed.
            call_ui(
                self,
                self._push_analysis_results_to_open_browser,
                meta.session_dir,
                self._plugin_results[sd_key],
            )
        finally:
            if not hold_inflight:
                end_session_analysis(meta.session_dir)

    def action_self_test(self) -> None:
        """Open dependency self-test (Docker, Grok auth, work dir, …) on the UI thread."""
        from .widgets.self_test_modal import SelfTestModal

        self.push_screen(SelfTestModal(work_dir=self.work_dir))

    @work(thread=True)
    def _analyze_targets(self, targets: list[tuple[SessionMeta, str]] | None = None) -> None:
        """Analyze (meta, label) pairs on a worker thread; UI updates via call_ui."""
        if (
            not targets
            or isinstance(targets, (str, Path))
            or (not isinstance(targets, (list, tuple)))
        ):
            return
        from ..analysis.inflight import (
            analysis_session_key,
            end_session_analysis,
            try_begin_session_analysis,
        )

        pending: list[tuple[SessionMeta, str]] = []
        skipped_done = 0
        skipped_inflight = 0
        for item in targets:
            if not isinstance(item, tuple) or len(item) != 2:
                continue
            meta, label = item
            key = analysis_session_key(meta.session_dir)
            if key in self._plugin_results:
                skipped_done += 1
                continue
            if not try_begin_session_analysis(meta.session_dir):
                skipped_inflight += 1
                continue
            pending.append((meta, str(label)))
        if not pending:
            msg = (
                t("notify-analysis-in-flight", n=skipped_inflight)
                if skipped_inflight
                else t("ui-already-analyzed")
            )
            call_ui(self, self.notify, msg, severity="information")
            return
        n_plugins = 0
        try:
            n_plugins = len([p for p in self._analysis_svc().list_plugins() if p.id != "noop"])
        except Exception:
            pass
        call_ui(
            self,
            self.notify,
            t("notify-analyzing", n=len(pending), plugins=n_plugins),
            severity="information",
        )
        if skipped_inflight:
            call_ui(
                self,
                self.notify,
                t("notify-analysis-in-flight", n=skipped_inflight),
                severity="information",
            )
        # Serial analysis pool (default 1 worker) avoids stampeding plugins.
        from ..job_pools import get_analysis_pool

        self._analysis_jobs_active = max(0, int(self._analysis_jobs_active)) + len(pending)
        pending_n = len(pending)

        def _run_all() -> None:
            try:
                for idx, (meta, label) in enumerate(pending):
                    try:
                        # Explicit Analyze action includes deferred plugins.
                        self._analyze_one(meta, label, hold_inflight=True, force=True)
                    finally:
                        end_session_analysis(meta.session_dir)
                        self._analysis_jobs_active = max(0, self._analysis_jobs_active - 1)
                    if (idx + 1) % 5 == 0 or idx == pending_n - 1:
                        call_ui(self, self._populate_session_table)
            except Exception:
                for meta, _label in pending:
                    end_session_analysis(meta.session_dir)
                self._analysis_jobs_active = 0
                raise
            call_ui(
                self,
                self.notify,
                t("notify-analysis-complete", n=pending_n),
                severity="information",
            )

        get_analysis_pool().submit(f"batch {pending_n} session(s)", _run_all)

    def _derive_label(self, session_dir: Path, root: Path) -> str:
        """Derive a display label from directory path."""
        try:
            rel = session_dir.relative_to(root)
            parts = list(rel.parts)
            meaningful = [p for p in parts if p != "sessions" and (not p.startswith("%"))]
            if meaningful:
                return "/".join(meaningful[:2])
        except ValueError:
            pass
        return session_dir.name[:20]

    def _session_model_options(self) -> list[tuple[str, str]]:
        models = sorted(
            {
                meta.model_display
                for meta, _ in self._meta_only
                if meta.model_id and meta.model_id != "unknown"
            }
        )
        return [(U.all_models(), _SESSION_FILTER_ALL), *[(m, m) for m in models]]

    @staticmethod
    def _select_value_to_filter(value: object) -> str:
        """Map Select value to internal filter (``all`` / blank → no filter)."""
        if value is Select.BLANK or value is None:
            return ""
        s = str(value)
        return "" if s == _SESSION_FILTER_ALL else s

    @staticmethod
    def _filter_to_select_value(filt: str) -> str:
        return filt if filt else _SESSION_FILTER_ALL

    def _rebuild_session_filters(self) -> None:
        """Refresh Model Select options from loaded session metadata."""
        model_opts = self._session_model_options()
        model_vals = {v for _, v in model_opts}
        model_sel_val = self._filter_to_select_value(self._filter_model)
        if model_sel_val not in model_vals:
            self._filter_model = ""
            model_sel_val = _SESSION_FILTER_ALL
        try:
            model_sel = self.query_one("#session-model-select", Select)
            model_sel.set_options(model_opts)
            model_sel.value = model_sel_val
        except Exception:
            logger.debug(t("ui-session-model-select-update-failed"), exc_info=True)

    def _set_session_filter_selects(self) -> None:
        """Push ``_filter_model`` into the Model Select (keyboard cycle)."""
        try:
            self.query_one("#session-model-select", Select).value = self._filter_to_select_value(
                self._filter_model
            )
        except Exception:
            pass

    @on(Select.Changed, "#session-model-select")
    def _on_session_model_filter(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        self._filter_model = self._select_value_to_filter(event.value)
        self._populate_session_table(force=True)

    @staticmethod
    def _cursor_key_after_deletes(
        row_keys_in_order: list[str], cursor_key: str | None, gone: set[str]
    ) -> str | None:
        """Pick a sensible post-delete cursor: next row, else previous, else first remaining."""
        if not row_keys_in_order:
            return None
        remaining = [k for k in row_keys_in_order if k not in gone]
        if not remaining:
            return None
        if cursor_key and cursor_key not in gone and (cursor_key in remaining):
            return cursor_key
        try:
            idx = row_keys_in_order.index(cursor_key) if cursor_key else 0
        except ValueError:
            idx = 0
        for k in row_keys_in_order[idx + 1 :]:
            if k not in gone:
                return k
        for k in reversed(row_keys_in_order[:idx]):
            if k not in gone:
                return k
        return remaining[0]

    def _session_row_keys_in_order(self, table: DataTable | None = None) -> list[str]:
        table = table or self.query_one("#session-table", DataTable)
        try:
            return [str(rk.value) for rk in table.rows.keys()]
        except Exception:
            return []

    @staticmethod
    def _session_sort_ts(meta: SessionMeta) -> float:
        """Best-effort epoch seconds for newest-first session ordering."""
        for raw in (meta.updated_at, meta.created_at):
            if not raw:
                continue
            try:
                s = str(raw).replace("Z", "+00:00")
                from datetime import datetime

                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.timestamp()
            except Exception:
                pass
        try:
            from ..parser import session_trace_mtime

            mt = session_trace_mtime(Path(meta.session_dir))
            if mt > 0:
                return mt
        except Exception:
            pass
        try:
            return Path(meta.session_dir).stat().st_mtime
        except OSError:
            return 0.0

    def _populate_session_table(
        self, *, restore_key: str | None = None, force: bool = False
    ) -> None:
        """Rebuild sessions table on the UI thread.

        *force* is accepted for call sites that used debounce; ignored — if a
        rebuild is already in progress we skip (no timer storm).
        """
        _ = force
        if self._populate_busy:
            return
        self._populate_busy = True
        try:
            self._populate_session_table_inner(restore_key=restore_key)
        finally:
            self._populate_busy = False

    def _filtered_session_rows(
        self,
    ) -> list[tuple[SessionMeta, str, dict[str, AnalysisResult] | None]]:
        from ..analysis.inflight import analysis_session_key

        search_q = (self._session_search or "").strip().casefold()
        seen_keys: set[str] = set()
        rows: list[tuple[SessionMeta, str, dict[str, AnalysisResult] | None]] = []
        for meta, label in self._meta_only:
            if self._filter_model and meta.model_display != self._filter_model:
                continue
            if search_q and search_q not in _session_search_haystack(meta, label):
                continue
            sd_key = analysis_session_key(meta.session_dir)
            if sd_key in seen_keys:
                continue
            seen_keys.add(sd_key)
            rows.append((meta, label, self._plugin_results.get(sd_key)))

        def sort_key(
            item: tuple[SessionMeta, str, dict[str, AnalysisResult] | None],
        ) -> tuple[float, str, str, str]:
            meta, _label, _results = item
            return (
                -self._session_sort_ts(meta),
                meta.model_display,
                meta.task_id or "",
                meta.session_id or "",
            )

        rows.sort(key=sort_key)
        return rows

    @staticmethod
    def _session_home_fp(cells: tuple[str | Text, ...]) -> str:
        return "\u0001".join(str(c) for c in cells)

    def _session_status_cell(self, meta: SessionMeta) -> Text:
        from .styles import status_rich_style, theme_is_light

        light = theme_is_light(str(self.theme or ""))
        status = meta.list_status_label()
        if status == "awaiting":
            return Text(
                t("status-waiting-prompt"), style=status_rich_style("awaiting", light=light)
            )
        if status == "ending":
            return Text(t("status-ending"), style=status_rich_style("ending", light=light))
        if status == "running":
            return Text(t("status-running"), style=status_rich_style("running", light=light))
        if status == "cancelled":
            return Text(t("status-cancelled"), style=status_rich_style("failed", light=light))
        if status == "complete":
            return Text(t("status-complete"), style=status_rich_style("completed", light=light))
        return Text(
            status if status != "—" else t("status-unknown"),
            style=status_rich_style("idle", light=light),
        )

    @staticmethod
    def _session_findings_cell(
        results: dict[str, AnalysisResult] | None,
    ) -> tuple[Text, int, int]:
        if results is None:
            return Text("--", style="dim"), 0, 0
        high = sum(r.high_count for r in results.values())
        med = sum(r.medium_count for r in results.values())
        count = sum(r.finding_count for r in results.values())
        cell = Text(str(count))
        if high:
            cell.append(f" {high}H", style="bold red")
        if med:
            cell.append(f" {med}M", style="yellow")
        return cell, count, high

    def _session_home_cells(
        self,
        meta: SessionMeta,
        results: dict[str, AnalysisResult] | None,
        *,
        selected: bool,
    ) -> tuple[str | Text, ...]:
        from ..session.sources import ORIGIN_HOST

        origin = self._origin_for_dir(Path(meta.session_dir))
        origin_text = (
            Text(t("ui-origin-host"), style="magenta")
            if origin == ORIGIN_HOST
            else Text(t("ui-origin-work"), style="dim")
        )
        findings, _count, _high = self._session_findings_cell(results)
        return (
            Text("*", style="bold green") if selected else Text(" "),
            origin_text,
            (meta.label or meta.session_id)[:40],
            meta.model_display[:40],
            self._session_status_cell(meta),
            meta.duration_str,
            (meta.context_usage_compact or "—")[:24],
            str(meta.num_events),
            findings,
        )

    def _table_row_keys(self, table: DataTable) -> list[str]:
        with suppress(Exception):
            return [str(k.value) for k in table.rows.keys()]
        return []

    def _patch_session_table_rows(
        self, table: DataTable, painted: list[tuple[str, tuple[str | Text, ...]]]
    ) -> None:
        for key, cells in painted:
            fp = self._session_home_fp(cells)
            if self._session_row_fp.get(key) == fp:
                continue
            for i, cell in enumerate(cells):
                update_row_cell(table, key, i, cell)
            self._session_row_fp[key] = fp

    def _rebuild_session_table_rows(
        self,
        table: DataTable,
        painted: list[tuple[str, tuple[str | Text, ...]]],
        restore_key: str | None,
    ) -> None:
        with preserving_scroll(table):
            table.clear()
            self._session_row_fp.clear()
            for key, cells in painted:
                try:
                    table.add_row(*cells, key=key)
                    self._session_row_fp[key] = self._session_home_fp(cells)
                except Exception:
                    logger.debug(t("ui-failed-to-add-row-for-s"), key, exc_info=True)
            if restore_key:
                restore_cursor(table, restore_key, scroll=False)

    def _populate_session_table_inner(self, *, restore_key: str | None = None) -> None:
        try:
            table = self.query_one("#session-table", DataTable)
        except NoMatches:
            return
        if restore_key is None:
            restore_key = self._session_row_key_at_cursor(table)
        rows = self._filtered_session_rows()
        painted: list[tuple[str, tuple[str | Text, ...]]] = []
        total_findings = 0
        total_high = 0
        analyzed_count = 0
        for meta, _label, results in rows:
            sd_key = str(meta.session_dir)
            if results is not None:
                analyzed_count += 1
                _cell, count, high = self._session_findings_cell(results)
                total_findings += count
                total_high += high
            painted.append(
                (
                    sd_key,
                    self._session_home_cells(meta, results, selected=sd_key in self._selected),
                )
            )
        existing = self._table_row_keys(table)
        new_keys = [key for key, _cells in painted]
        with preserving_scroll(table):
            if existing == new_keys and existing:
                self._patch_session_table_rows(table, painted)
                if restore_key:
                    restore_cursor(table, restore_key, scroll=False)
            else:
                self._rebuild_session_table_rows(table, painted, restore_key)
        if restore_key or existing:
            self._sessions_table_primed = True
        elif not self._sessions_table_primed:
            focus_primary_list(table)
            self._sessions_table_primed = True
        pending = len(self._meta_only) - analyzed_count
        self._update_summary_lazy(
            len(self._meta_only), analyzed_count, total_findings, total_high, pending
        )
        with suppress(Exception):
            self.refresh_bindings()

    def _update_summary_lazy(
        self, total: int, analyzed: int, total_findings: int, total_high: int, pending: int
    ) -> None:
        from .i18n import join_ui

        sel_count = len(self._selected)
        extras: list[str] = []
        if sel_count:
            extras.append(t("sessions-selected-count", n=sel_count))
            scope = t("ui-report-uses-selected")
            if scope.strip():
                extras.append(scope.strip())
        if pending > 0:
            extras.append(t("sessions-pending-analysis", n=pending))
        core = t(
            "sessions-home-summary",
            total=total,
            findings=total_findings,
            high=total_high,
        )
        summary = f"[bold]{join_ui(core, *extras, sep=' · ')}"
        self.query_one("#session-summary", Static).update(summary)

    def _restore_cursor(self, table: DataTable, row_key_value: str) -> None:
        """Move cursor back to the row with the given key after a table repopulate."""
        restore_cursor(table, row_key_value)

    def _session_row_key_at_cursor(self, table: DataTable | None = None) -> str | None:
        """Stable row key for the highlighted session row (session_dir path)."""
        table = table or self.query_one("#session-table", DataTable)
        return cursor_row_key(table)

    def _set_session_sel_cell(self, table: DataTable, row_key: str, selected: bool) -> None:
        """Update only the selection marker column (avoids table.clear / cursor jump)."""
        from rich.text import Text

        mark = Text("*", style="bold green") if selected else Text(" ")
        try:
            cols = list(table.columns.keys())
            if not cols:
                return
            table.update_cell(row_key, cols[0], mark)
        except Exception:
            set_marker_column(table, row_key, selected, on="*", off=" ")

    def _refresh_session_selection_markers(self, table: DataTable | None = None) -> None:
        """Refresh all Sel cells from ``self._selected`` without rebuilding rows."""
        table = table or self.query_one("#session-table", DataTable)
        for rk in table.rows.keys():
            key = str(rk.value)
            self._set_session_sel_cell(table, key, key in self._selected)

    def action_cycle_model_filter(self) -> None:
        """Cycle model Select: all -> model1 -> … -> all (``m`` / command palette)."""
        models = [v for _, v in self._session_model_options() if v != _SESSION_FILTER_ALL]
        if not models:
            return
        if self._filter_model and self._filter_model in models:
            idx = models.index(self._filter_model)
            self._filter_model = models[idx + 1] if idx + 1 < len(models) else ""
        else:
            self._filter_model = models[0]
        self._set_session_filter_selects()
        self.notify(t("notify-model-filter", label=self._filter_model or "all"))
        self._populate_session_table(force=True)

    def action_toggle_select(self) -> None:
        """Toggle selection on the current row (in-place; cursor stays put)."""
        table = self.query_one("#session-table", DataTable)
        cursor_key = self._session_row_key_at_cursor(table)
        if not cursor_key:
            return
        if cursor_key in self._selected:
            self._selected.discard(cursor_key)
            now_on = False
        else:
            self._selected.add(cursor_key)
            now_on = True
        self._set_session_sel_cell(table, cursor_key, now_on)
        self._refresh_selection_summary_only()
        with suppress(Exception):
            self.refresh_bindings()

    def action_select_all(self) -> None:
        """Select all or deselect all (in-place markers; no cursor jump)."""
        table = self.query_one("#session-table", DataTable)
        preserve = self._session_row_key_at_cursor(table)
        if self._selected:
            self._selected.clear()
        else:
            for meta, _ in self._meta_only:
                self._selected.add(str(meta.session_dir))
        self._refresh_session_selection_markers(table)
        if preserve:
            self._restore_cursor(table, preserve)
        self._refresh_selection_summary_only()
        with suppress(Exception):
            self.refresh_bindings()

    def _refresh_selection_summary_only(self) -> None:
        """Recompute summary counts from in-memory analysis (no table rebuild)."""
        total = len(self._meta_only)
        analyzed_count = 0
        total_findings = 0
        total_high = 0
        from ..analysis.inflight import analysis_session_key

        for meta, _ in self._meta_only:
            results = self._plugin_results.get(analysis_session_key(meta.session_dir))
            if results is None:
                continue
            analyzed_count += 1
            total_findings += sum(r.finding_count for r in results.values())
            total_high += sum(r.high_count for r in results.values())
        pending = max(0, total - analyzed_count)
        try:
            self._update_summary_lazy(total, analyzed_count, total_findings, total_high, pending)
        except Exception:
            pass

    def action_search_sessions(self) -> None:
        """Focus the sessions search field (filter as you type, same as Timeline)."""

        def _focus_search() -> None:
            with suppress(Exception):
                self.query_one("#session-search-input", Input).focus()

        self.call_after_refresh(lambda: self.call_after_refresh(_focus_search))

    @on(Input.Changed, "#session-search-input")
    def _on_session_search_changed(self, event: Input.Changed) -> None:
        """Filter the sessions table as you type (no Enter required)."""
        self._session_search = event.value or ""
        self._populate_session_table(force=True)

    @on(Input.Submitted, "#session-search-input")
    def _on_session_search_submitted(self, event: Input.Submitted) -> None:
        """Keep filter on Enter; move focus back to the session list."""
        self._session_search = event.value or ""
        self._populate_session_table(force=True)
        with suppress(Exception):
            focus_primary_list(self.query_one("#session-table", DataTable))

    def _cursor_session_meta(self) -> SessionMeta | None:
        """SessionMeta for the sessions-home table cursor, or None."""
        table = self.query_one("#session-table", DataTable)
        if table.cursor_row is None:
            return None
        try:
            row_key = list(table.rows.keys())[table.cursor_row]
            cursor_key = row_key.value
        except (IndexError, KeyError):
            return None
        for m, _label in self._meta_only:
            if str(m.session_dir) == cursor_key:
                return m
        return None

    def action_rerun_session(self) -> None:
        """Open the runner pre-filled with the current session's details."""
        meta = self._cursor_session_meta()
        if meta is None:
            self.notify(U.select_session_first(), severity="warning")
            return
        self._do_rerun(meta)

    def action_resume_session(self) -> None:
        """Open runner to continue an ended session as a new interactive multi-turn."""
        meta = self._cursor_session_meta()
        if meta is None:
            self.notify(U.select_session_first(), severity="warning")
            return
        from ..session.resume import can_resume_session
        from ..session.turn_gate import read_turn_gate_status

        if not can_resume_session(meta.session_dir):
            self.notify(t("resume-session-no-artifacts"), severity="warning")
            return
        try:
            st = read_turn_gate_status(meta.session_dir)
            state = str(st.get("state") or "").strip().lower()
        except Exception:
            state = ""
        if state in ("awaiting_follow_up", "running"):
            self.notify(t("resume-session-still-live"), severity="warning")
            return
        self._do_resume(meta)

    def _extract_session_launch_params(self, meta: SessionMeta) -> dict:
        """Extract launch parameters from a session's run recipe and task catalog.

        Prefers ``run.json`` on the session, its traces volume (written at
        container start), or the fork parent seed when the child never got a
        recipe. Returns keys: prompt, setup_instructions, docker_image,
        repo_url, repo_branch, repo_path, models, persona_id, run_plugins,
        run_skills, run_mcp_servers.
        """
        from ..constants import DEFAULT_DOCKER_IMAGE, DEFAULT_MODEL_ID
        from ..runs.run_recipe import load_run_recipe

        prompt = extract_prompt(meta.session_dir)
        setup = ""
        docker_image = DEFAULT_DOCKER_IMAGE
        repo_url = meta.git_repo
        repo_branch = meta.git_branch
        repo_path = ""
        persona_id = ""
        run_plugins: list[str] = []
        run_skills: list[str] = []
        run_mcp: list[str] = []
        models_list: list[str] = []
        run_data = load_run_recipe(meta.session_dir)
        if run_data:
            repo_url = repo_url or str(run_data.get("repo_url") or "")
            repo_branch = repo_branch or str(run_data.get("repo_branch") or "")
            repo_path = str(run_data.get("repo_path") or "").strip()
            setup = str(run_data.get("setup_instructions") or setup or "")
            docker_image = str(run_data.get("docker_image") or docker_image)
            persona_id = str(run_data.get("persona_id") or "").strip()
            # Run-only extras (not merged persona caps).
            plugins = run_data.get("run_plugins") or []
            if isinstance(plugins, list):
                run_plugins = [str(x) for x in plugins if str(x).strip()]
            skills = run_data.get("run_skills") or []
            if isinstance(skills, list):
                run_skills = [str(x) for x in skills if str(x).strip()]
            mcps = run_data.get("run_mcp_servers") or []
            if isinstance(mcps, list):
                run_mcp = [str(x) for x in mcps if str(x).strip()]
            models_from_run = run_data.get("models") or []
            if isinstance(models_from_run, list) and models_from_run:
                models_list = [str(x) for x in models_from_run if str(x).strip()]
        if not models_list:
            models_list = (
                [meta.model_id] if meta.model_id and meta.model_id != DEFAULT_MODEL_ID else []
            )
        # Prefer launch meta model:effort when present on the traces volume.
        try:
            from ..runs.launch_meta import read_launch_meta

            lm = read_launch_meta(meta.session_dir)
            if lm is not None and (lm.display_token or "").strip():
                models_list = [lm.display_token]
        except Exception:
            logger.debug("launch meta lookup failed for resume/rerun", exc_info=True)
        # Summary often has remotes/branch when run.json never stored repo_url.
        if not (repo_url or "").strip():
            repo_url = (meta.git_repo or "").strip()
        if not (repo_branch or "").strip():
            repo_branch = (meta.git_branch or "").strip()
        repo_commit = (getattr(meta, "git_commit", None) or "").strip()
        return {
            "prompt": prompt,
            "setup_instructions": setup,
            "docker_image": docker_image,
            "repo_url": repo_url,
            "repo_branch": repo_branch,
            "repo_path": repo_path,
            "models": models_list,
            "persona_id": persona_id,
            "run_plugins": run_plugins,
            "run_skills": run_skills,
            "run_mcp_servers": run_mcp,
            "repo_commit": repo_commit,
        }

    @work(thread=True)
    def _do_rerun(self, meta: SessionMeta | None = None) -> None:
        """Extract session details and open runner with prefill."""
        if not isinstance(meta, SessionMeta):
            return
        params = self._extract_session_launch_params(meta)
        prefill = RunnerPrefill(**params)
        call_ui(self, self._push_runner_with_prefill, prefill)

    @work(thread=True)
    def _do_resume(self, meta: SessionMeta | None = None) -> None:
        """Open runner to continue *meta* via grok --resume in a new interactive run."""
        if not isinstance(meta, SessionMeta):
            return
        from ..session.resume import resume_session_id

        params = self._extract_session_launch_params(meta)
        # First message is the continuation, not a replay of the original prompt.
        params["prompt"] = ""
        sid = resume_session_id(meta.session_dir)
        repo_commit = str(params.pop("repo_commit", "") or meta.git_commit or "").strip()
        prefill = RunnerPrefill(
            **params,
            interactive=True,
            resume_session_id=sid,
            resume_source_dir=str(meta.session_dir),
            repo_commit=repo_commit,
            restore_code=True,
        )
        call_ui(self, self._push_runner_with_prefill, prefill)

    def action_save_session_config(self) -> None:
        """Save the highlighted (or first selected) session as a reusable run config."""
        meta = None
        if self._selected:
            key = next(iter(self._selected))
            for m, _ in self._meta_only:
                if str(m.session_dir) == key:
                    meta = m
                    break
        if meta is None:
            table = self.query_one("#session-table", DataTable)
            if table.row_count == 0:
                self.notify(U.no_session_to_save(), severity="warning")
                return
            try:
                row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
                path = str(row_key.value) if row_key else ""
            except Exception:
                path = ""
            for m, _ in self._meta_only:
                if str(m.session_dir) == path:
                    meta = m
                    break
        if meta is None:
            self.notify(U.session_not_found(), severity="error")
            return
        self._do_save_session_config(meta)

    @work(thread=True)
    def _do_save_session_config(self, meta: SessionMeta | None = None) -> None:
        if not isinstance(meta, SessionMeta):
            return
        from ..runs.run_configs import RunConfigStore

        params = self._extract_session_launch_params(meta)
        try:
            store = RunConfigStore(self.work_dir)
            cfg = store.from_session_fields(
                prompt=params["prompt"] or t("ui-no-prompt-extracted"),
                setup_instructions=params["setup_instructions"],
                docker_image=params["docker_image"],
                repo_url=params["repo_url"],
                repo_branch=params["repo_branch"],
                repo_path=str(params.get("repo_path") or ""),
                models=params["models"],
                session_id=meta.session_id,
                session_dir=str(meta.session_dir),
                name=meta.task_id or meta.label or meta.session_id[:12],
            )
            call_ui(
                self,
                self.notify,
                t(
                    "notify-saved-run-config",
                    id=cfg.config_id,
                    name=cfg.display_name(),
                ),
                severity="information",
                timeout=10,
            )
        except Exception as exc:
            call_ui(
                self,
                self.notify,
                t("notify-save-config-failed", exc=str(exc)),
                severity="error",
            )

    def _toast(
        self,
        message: str,
        *,
        severity: str = "information",
        timeout: float = 2.0,
        replace: bool = True,
    ) -> None:
        """Short status toast (optionally clearing prior notifications)."""
        from typing import Literal, cast

        sev = cast(Literal["information", "warning", "error"], severity)

        def _show() -> None:
            if replace:
                with suppress(Exception):
                    self.clear_notifications()
            self.notify(message, severity=sev, timeout=timeout)

        call_ui(self, _show)

    def _session_action_targets(self) -> list[Path]:
        """Selected session dirs, or the cursor row if nothing is selected."""
        if self._selected:
            return [Path(p) for p in self._selected]
        table = self.query_one("#session-table", DataTable)
        cursor_key = self._session_row_key_at_cursor(table)
        if cursor_key:
            return [Path(cursor_key)]
        return []

    def _refresh_session_meta_rows(self, paths: list[Path]) -> None:
        """Reload meta for *paths* and repaint the session table."""
        if not paths:
            return
        want = {str(p) for p in paths}
        updated: list[tuple[SessionMeta, str]] = []
        for meta, label in self._meta_only:
            key = str(meta.session_dir)
            if key in want:
                try:
                    reloaded = load_session_meta(meta.session_dir)
                    if reloaded is not None:
                        meta = reloaded
                except Exception:
                    logger.debug(t("ui-reload-meta-failed-for-s"), key, exc_info=True)
            updated.append((meta, label))
        self._meta_only = updated
        with suppress(Exception):
            self._populate_session_table()

    def _awaiting_session_targets(self) -> list[Path]:
        """Subset of action targets that are awaiting a follow-up."""
        from ..session.turn_gate import session_awaits_follow_up

        targets = self._session_action_targets()
        if not targets:
            return []
        by_path = {str(m.session_dir): m for m, _ in self._meta_only}
        out: list[Path] = []
        for path in targets:
            meta = by_path.get(str(path))
            if meta is not None and meta.turn_in_progress:
                out.append(path)
                continue
            try:
                if session_awaits_follow_up(path):
                    out.append(path)
            except Exception:
                logger.debug(t("ui-awaiting-check-failed-for-s"), path, exc_info=True)
        return out

    def _apply_follow_up_to_paths(
        self, paths: list[Path], prompt: str, *, final: bool = False
    ) -> int:
        from ..session.turn_gate import write_follow_up_for_session

        errors = 0
        for path in paths:
            try:
                write_follow_up_for_session(path, prompt, final=final)
            except Exception:
                errors += 1
                logger.debug(t("ui-follow-up-failed-for-s"), path, exc_info=True)
                continue
            # Per-session gate only. Multi-select applies once per path; do not
            # also submit_follow_up(run_id=…) which fans out to all siblings.
        return errors

    def _apply_done_to_paths(self, paths: list[Path]) -> int:
        from ..session.turn_gate import write_done_for_session

        errors = 0
        for path in paths:
            try:
                write_done_for_session(path)
            except Exception:
                errors += 1
                logger.debug(t("ui-mark-done-failed-for-s"), path, exc_info=True)
                continue
            rm = self.run_manager
            if hasattr(rm, "stop_session_container"):
                try:
                    rm.stop_session_container(path)
                except Exception:
                    pass
        return errors

    def _awaiting_targets_or_toast(self) -> list[Path]:
        targets = self._awaiting_session_targets()
        if targets:
            return targets
        if not self._session_action_targets():
            self._toast(U.select_session_first(), severity="warning", timeout=2.0)
        else:
            self._toast(U.no_awaiting_sessions(), severity="warning", timeout=2.5)
        return []

    def _sessions_home_active(self) -> bool:
        """True when the sessions list screen is on top (not a pushed screen/modal)."""
        try:
            return self.screen is self.screen_stack[0]
        except Exception:
            return True

    def _runner_active(self) -> bool:
        """True when the evaluation runner form is the top screen."""
        from .screens.runner import RunnerScreen

        return isinstance(self.screen, RunnerScreen)

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],  # Textual Screen.check_action
    ) -> bool | None:
        """Gate session-home bindings so they do not leak into pushed-screen footers.

        ``n`` / ``e`` need an awaiting multi-turn target. ``H`` is two actions
        (show / hide host); only the matching one is enabled.
        """
        if action == "leader_idle":
            return bool(self._leader_armed)
        if action == "launch_from_runner":
            return self._runner_active()
        if action in SESSION_HOME_ACTIONS and not self._sessions_home_active():
            return False
        if action in ("follow_up_sessions", "mark_sessions_done"):
            return bool(self._awaiting_session_targets())
        if action == "show_host_sessions":
            from .prefs import show_host_sessions_enabled

            return not show_host_sessions_enabled()
        if action == "hide_host_sessions":
            from .prefs import show_host_sessions_enabled

            return show_host_sessions_enabled()
        return True

    def action_launch_from_runner(self) -> None:
        """Priority hotkey: launch eval when Runner is the active screen."""
        from .screens.runner import RunnerScreen

        screen = self.screen
        if isinstance(screen, RunnerScreen):
            screen.action_run_evaluation()

    def action_mark_sessions_done(self) -> None:
        """``e`` — end awaiting sessions (mark done)."""
        targets = self._awaiting_targets_or_toast()
        if not targets:
            return
        errors = self._apply_done_to_paths(targets)
        self._refresh_session_meta_rows(targets)
        self.refresh_bindings()
        if errors:
            self._toast(
                t("notify-failed-for", errors=errors, total=len(targets)),
                severity="warning",
                timeout=3.0,
            )
        else:
            self._toast(
                t("mark-sessions-done-requested", n=len(targets)),
                severity="information",
                timeout=3.0,
            )

    def action_follow_up_sessions(self) -> None:
        """``n`` — next prompt for awaiting selection."""
        targets = self._awaiting_targets_or_toast()
        if not targets:
            return

        def _apply(result: tuple[str, bool] | None) -> None:
            if not result:
                return
            prompt, final = result
            errors = self._apply_follow_up_to_paths(targets, prompt, final=final)
            self._refresh_session_meta_rows(targets)
            self.refresh_bindings()
            if errors:
                self._toast(
                    t("notify-failed-for", errors=errors, total=len(targets)),
                    severity="warning",
                    timeout=3.0,
                )
            elif final:
                self._toast(
                    t("follow-up-sent-final-n", n=len(targets)),
                    severity="information",
                    timeout=2.5,
                )

        self.push_screen(InteractiveSessionsModal(n_awaiting=len(targets)), _apply)

    def action_delete_sessions(self) -> None:
        """Delete selected sessions (or current row if none selected). Removes traces only."""
        targets: list[Path] = []
        table = self.query_one("#session-table", DataTable)
        cursor_key = self._session_row_key_at_cursor(table)
        if self._selected:
            targets = [Path(p) for p in self._selected]
        elif cursor_key:
            targets = [Path(cursor_key)]
        if not targets:
            self.notify(
                t("ui-select-sessions-with-s-or-highlight-a-row-then-p"), severity="warning"
            )
            return
        from .delete_confirm import second_press_armed

        n = len(targets)
        commit, pending = second_press_armed(
            [str(p) for p in (self._delete_pending_paths or [])],
            [str(p) for p in targets],
        )
        if not commit:
            self._delete_pending_paths = [Path(p) for p in pending]
            self._delete_cursor_key = cursor_key
            self._delete_row_keys_snapshot = self._session_row_keys_in_order(table)
            self.notify(
                t("notify-press-again-delete-sessions", n=n),
                severity="warning",
                timeout=10,
            )
            return
        gone_preview = {str(p) for p in targets}
        snap = self._delete_row_keys_snapshot or self._session_row_keys_in_order(table)
        cur = self._delete_cursor_key or cursor_key
        restore_key = self._cursor_key_after_deletes(snap, cur, gone_preview)
        self._delete_pending_paths = None
        self._delete_cursor_key = None
        self._delete_row_keys_snapshot = None
        self._do_delete_sessions(targets, restore_key=restore_key)

    @work(thread=True)
    def _do_delete_sessions(
        self, targets: list[Path] | None = None, *, restore_key: str | None = None
    ) -> None:
        if not targets:
            return
        from ..runs.run_configs import delete_session_dirs, session_dirs_for_delete

        paths = session_dirs_for_delete(targets)
        stats = delete_session_dirs(paths, traces_root=self.traces_path, prune_empty_parents=True)
        gone = {str(p) for p in paths}

        def _refresh() -> None:
            self._selected -= gone
            self._meta_only = [
                (m, lab) for m, lab in self._meta_only if str(m.session_dir) not in gone
            ]
            for g in gone:
                self._session_mtimes.pop(g, None)
                # Also drop resolve()-style keys that contain the path
                for mk in list(self._session_mtimes):
                    if mk == g or mk.endswith(g) or g.endswith(mk):
                        self._session_mtimes.pop(mk, None)

            for key in list(self._plugin_results.keys()):
                if key in gone:
                    del self._plugin_results[key]
            try:
                self._populate_session_table(restore_key=restore_key)
            except Exception:
                pass
            errors_raw = stats.get("errors")
            errors_list = list(errors_raw) if isinstance(errors_raw, list) else []
            err_n = len(errors_list)
            err_hint = ""
            if err_n:
                sample = str(errors_list[0]) if errors_list else ""
                err_hint = f" — {sample[:120]}"
            self.notify(
                (
                    t(
                        "notify-deleted-sessions-errors",
                        deleted=stats["deleted"],
                        requested=stats["requested"],
                        errors=err_n,
                        hint=err_hint or "",
                    )
                    if err_n
                    else t(
                        "notify-deleted-sessions",
                        deleted=stats["deleted"],
                        requested=stats["requested"],
                    )
                ),
                severity="warning" if err_n else "information",
                timeout=12,
            )

        call_ui(self, _refresh)

    def action_open_run_configs(self) -> None:
        """Browse reusable run configs (launch again with new models)."""
        self.push_screen(RunConfigsScreen(self.work_dir, run_manager=self.run_manager))

    def _findings_for_session(self, sd_key: str) -> list[Finding]:
        """All findings across all plugins for a session."""
        results = self._plugin_results.get(sd_key, {})
        out: list[Finding] = []
        for r in results.values():
            out.extend(r.findings)
        return out

    def action_refresh_everything(self) -> None:
        """Full refresh: rescan + run all analysis plugins."""
        from ..paths import traces_root_for_reload

        traces = traces_root_for_reload(self.work_dir, self.traces_path)
        runner_traces = self.work_dir / "runs" / "traces"
        root = runner_traces if runner_traces.exists() else traces
        if not root.exists():
            self.notify(t("notify-no-traces-refresh", path=str(root)), severity="error")
            return
        self._meta_only = []
        self._session_mtimes.clear()
        self._plugin_results = {}
        self._selected = set()
        try:
            cf = self.work_dir / self._CACHE_FILE
            if cf.exists():
                cf.unlink()
        except OSError:
            pass
        self.notify(
            t("notify-full-refresh", path=str(root)),
            severity="warning",
            timeout=12,
        )
        self._run_refresh_everything(root)

    @work(thread=True)
    def _run_refresh_everything(self, traces_root: Path | None = None) -> None:
        if traces_root is None:
            return
        summary: dict = {"sessions_loaded": 0, "analysis_ok": 0, "analysis_err": 0, "error": ""}
        try:
            # Sync load — do not nest @work _load_sessions (would not run inline).
            summary["sessions_loaded"] = self._load_sessions_sync(traces_root)
            from ..analysis.inflight import analysis_session_key

            targets = list(self._meta_only)
            for meta, label in targets:
                self._analyze_one(meta, label)
                sd_key = analysis_session_key(meta.session_dir)
                results = self._plugin_results.get(sd_key, {})
                if results and all(r.ok for r in results.values()):
                    summary["analysis_ok"] += 1
                else:
                    summary["analysis_err"] += 1
            call_ui(self, self._populate_session_table)
        except Exception as exc:
            summary["error"] = str(exc)

        def _done() -> None:
            try:
                self.traces_path = traces_root
                self._update_session_paths_banner()
            except Exception:
                pass
            try:
                self._populate_session_table()
            except Exception:
                pass
            if summary.get("error"):
                self.notify(
                    t("notify-refresh-all-failed", error=str(summary["error"])),
                    severity="error",
                    timeout=15,
                )
                return
            err_n = int(summary.get("analysis_err") or 0)
            self.notify(
                t(
                    "notify-refresh-done",
                    sessions=summary.get("sessions_loaded", 0),
                    analyzed=summary.get("analysis_ok", 0),
                    errors=err_n,
                ),
                severity="warning" if err_n else "information",
                timeout=16,
            )

        call_ui(self, _done)

    def action_analyze(self) -> None:
        """Run configured session analyzer on selected sessions (or all if none selected)."""
        if not self._meta_only:
            self.notify(U.load_sessions_first(), severity="warning")
            return
        if self._selected:
            targets = [
                (meta, label)
                for meta, label in self._meta_only
                if str(meta.session_dir) in self._selected
            ]
        else:
            targets = list(self._meta_only)
        self._analyze_targets(targets)

    def _session_meta_for_export(self) -> SessionMeta | None:
        """Highlighted or first selected session for export actions."""
        meta = None
        if self._selected:
            key = next(iter(self._selected))
            for m, _ in self._meta_only:
                if str(m.session_dir) == key:
                    meta = m
                    break
        if meta is None:
            cursor_key = self._session_row_key_at_cursor()
            if cursor_key:
                for m, _ in self._meta_only:
                    if str(m.session_dir) == cursor_key:
                        meta = m
                        break
        return meta

    def action_export_session_bundle(self) -> None:
        """Export session: use configured profile, or ask if none is set."""
        meta = self._session_meta_for_export()
        if meta is None:
            self.notify(t("export-bundle-no-session"), severity="warning")
            return
        from .export_session import start_export_smart

        start_export_smart(self, meta.session_dir)

    def action_export_session_choose_profile(self) -> None:
        """Palette: pick an export profile for this export only (does not change default)."""
        meta = self._session_meta_for_export()
        if meta is None:
            self.notify(t("export-bundle-no-session"), severity="warning")
            return
        from .export_session import start_export_with_profile_picker

        start_export_with_profile_picker(self, meta.session_dir, remember_as_default=False)

    def _set_host_sessions_visible(self, on: bool) -> None:
        """Turn host catalog on or off; update footer via check_action + refresh_bindings."""
        from .prefs import set_show_host_sessions, show_host_sessions_enabled

        on = bool(on)
        if show_host_sessions_enabled() is on:
            self.refresh_bindings()
            return
        set_show_host_sessions(on)
        self._config["show_host_sessions"] = on
        self._update_session_paths_banner()
        self.refresh_bindings()
        self.notify(
            t("notify-host-sessions-on") if on else t("notify-host-sessions-off"),
            severity="information",
            timeout=4,
        )
        if not on:
            self._drop_host_session_rows()
        self._schedule_sessions_reload()

    def action_show_host_sessions(self) -> None:
        """``H`` when host is hidden — include ``~/.grok/sessions`` on the list."""
        self._set_host_sessions_visible(True)

    def action_hide_host_sessions(self) -> None:
        """``H`` when host is shown — drop host rows from the list."""
        self._set_host_sessions_visible(False)

    @staticmethod
    def _extract_task_and_model(trace_dir_name: str) -> tuple[str, str]:
        """Extract (task_id, model_suffix) from a trace directory name.

        Convention: groket-{run_id}-{model_suffix}. The model_suffix is only used
        as a fallback for grouping when the full model_id is unavailable.
        """
        from ..paths import strip_run_prefix

        name = strip_run_prefix(trace_dir_name)
        for suffix in ("-build", "-s80", "-s140"):
            if name.endswith(suffix):
                return (name[: -len(suffix)], suffix[1:])
        if "-" in name:
            parts = name.rsplit("-", 1)
            return (parts[0], parts[1])
        return (trace_dir_name, "unknown")

    @on(DataTable.RowHighlighted, "#session-table")
    def _on_session_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Refresh footer ``n`` / ``e`` when the cursor moves."""
        _ = event
        with suppress(Exception):
            self.refresh_bindings()

    @on(DataTable.RowSelected, "#session-table")
    def _on_session_selected(self, event: DataTable.RowSelected) -> None:
        row_key = str(event.row_key.value)
        self._open_session(row_key)

    def open_session_path(
        self,
        session_dir: Path | str,
        *,
        live: bool | None = None,
        prompt_index: int | None = None,
        notify_control: bool = True,
    ) -> None:
        """Open a session path in the trace browser (main list, Jobs modal, etc.)."""
        self._open_session(
            str(session_dir),
            live=live,
            prompt_index=prompt_index,
            notify_control=notify_control,
        )

    def _open_session(
        self,
        row_key: str,
        live: bool | None = None,
        prompt_index: int | None = None,
        notify_control: bool = True,
    ) -> None:
        """Open a session in the browser immediately.

        Analysis runs inside BrowserScreen._load_data on its own worker
        so the screen appears without delay.
        """
        from ..analysis.inflight import analysis_session_key

        plugin_results = self._plugin_results.get(analysis_session_key(row_key))
        session_path = Path(row_key)
        self._push_browser(session_path, plugin_results, prompt_index=prompt_index)
        if notify_control:
            self.control_session_selected(session_path, prompt_index)

    def _push_runner_with_prefill(self, prefill: RunnerPrefill) -> None:
        """Construct and push RunnerScreen on the main thread."""
        self.push_screen(RunnerScreen(self.work_dir, run_manager=self.run_manager, prefill=prefill))

    def _push_browser(
        self,
        session_path: Path,
        plugin_results: dict[str, AnalysisResult] | None,
        *,
        prompt_index: int | None = None,
    ) -> None:
        """Construct and push BrowserScreen on the main thread."""
        self._pause_home_traces_watch(pause=True)
        self.push_screen(BrowserScreen(session_path, plugin_results, prompt_index=prompt_index))

    def action_open_runner(self) -> None:
        self.push_screen(RunnerScreen(self.work_dir, run_manager=self.run_manager))

    def action_open_personas(self) -> None:
        """Persona builder: create/edit/delete personas under runs/personas/."""
        from .screens.personas import PersonasScreen

        self.push_screen(PersonasScreen(self.work_dir))

    def action_open_jobs(self) -> None:
        """Open background jobs + container logs modal (runner stays quiet by default)."""
        from .screens.jobs import JobsModal

        self.push_screen(JobsModal(self.run_manager, work_dir=self.work_dir))

    def _run_manager_batch_ids(self) -> list[str]:
        return self.run_manager.active_batch_ids

    def _subtitle_run_status(self) -> None:
        """Alias used by run-config screens to refresh the header run badge."""
        self.update_run_status()

    def update_run_status(self) -> None:
        """Keep the window title as the wordmark; the activity strip owns status."""
        self.title = t("help-brand-name")

    def _schedule_run_status_update(self) -> None:
        """Debounce title updates (batch runs finish containers rapidly)."""
        if self._run_status_timer is not None:
            self._run_status_timer.stop()
        self._run_status_timer = self.set_timer(0.6, self.update_run_status)

    def _runner_traces_root(self) -> Path:
        """Host path where eval containers write sessions (always shareable mid-run)."""
        return self.work_dir / "runs" / "traces"

    def _schedule_live_sessions_poll(self) -> None:
        """Watch ``runs/traces`` and arm a read-only 60s meta heartbeat.

        FS events discover sessions / turn status. The heartbeat reloads
        ``signals.json`` context fields for in-progress rows without writing
        the meta cache or traces tree.
        """
        root = self._runner_traces_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        from ..constants import (
            LIVE_POLL_HEARTBEAT_INTERVAL,
            LIVE_POLL_WATCH_FALLBACK_INTERVAL,
        )
        from ..fs_watch import TraceTreeWatch

        if self._traces_watch is None and self._live_sessions_timer is None:

            def _on_fs() -> None:
                if self._exiting:
                    return
                try:
                    if self.is_running:
                        self.call_from_thread(self._live_sessions_tick)
                except Exception:
                    pass

            watch = TraceTreeWatch(root, _on_fs, debounce_s=0.5)
            if watch.start():
                self._traces_watch = watch
            else:
                self._live_sessions_timer = self.set_interval(
                    LIVE_POLL_WATCH_FALLBACK_INTERVAL,
                    self._live_sessions_tick,
                )
        if self._live_sessions_heartbeat_timer is None:
            self._live_sessions_heartbeat_timer = self.set_interval(
                LIVE_POLL_HEARTBEAT_INTERVAL,
                self._live_sessions_heartbeat,
            )

    def _browser_live_screen_open(self) -> bool:
        """True when a session browser is top of stack (live refresh owns the tree)."""
        with suppress(Exception):
            top = self.screen
            # Browser screens always expose session_dir + live refresh.
            if getattr(top, "session_dir", None) is not None and hasattr(
                top, "_live_refresh_from_fs"
            ):
                return True
        return False

    def _pause_home_traces_watch(self, *, pause: bool) -> None:
        """Stop or restart the home-list FS observer (not just skip ticks).

        ``call_from_thread`` on every traces write still floods the UI loop even
        when the tick handler returns immediately. Fully stop the observer while
        a browser is open.
        """
        if pause:
            w = self._traces_watch
            self._traces_watch = None
            stop = getattr(w, "stop", None)
            if callable(stop):
                with suppress(Exception):
                    stop()
            if self._live_sessions_timer is not None:
                with suppress(Exception):
                    self._live_sessions_timer.stop()
                self._live_sessions_timer = None
            return
        if not self._exiting:
            self._schedule_live_sessions_poll()

    def _live_sessions_tick(self) -> None:
        """UI thread: at most one background scan at a time (from FS events)."""
        if self._live_sessions_busy or self._exiting:
            return
        if self._browser_live_screen_open():
            return
        self._live_sessions_busy = True
        self._scan_live_sessions_worker()

    def _live_sessions_heartbeat(self) -> None:
        """UI thread: periodic read-only reload of live row metas (context meter)."""
        if self._exiting or self._live_meta_heartbeat_busy:
            return
        if self._browser_live_screen_open():
            return
        live_rows = [
            (meta, label)
            for meta, label in list(self._meta_only)
            if meta.turn_in_progress
            or meta.list_status_label()
            in (
                "running",
                "ending",
                "awaiting",
            )
        ]
        if not live_rows:
            return
        self._live_meta_heartbeat_busy = True
        self._live_meta_heartbeat_worker(live_rows)

    def _dispatch_refresh_rerun(self, session_dir: Path) -> None:
        """UI thread: hand a coalesced refresh back to an open browser, if any."""
        from ..session_inflight import session_dir_key

        target = session_dir_key(session_dir)
        try:
            stack = list(self.screen_stack)
        except Exception:
            stack = []
        for screen in stack:
            browser_sd = getattr(screen, "session_dir", None)
            refresh = getattr(screen, "_live_refresh_from_fs", None)
            if browser_sd is None or not callable(refresh):
                continue
            if session_dir_key(browser_sd) == target:
                refresh(heartbeat=True)
                return

    @work(thread=True)
    def _live_meta_heartbeat_worker(self, live_rows: list[tuple[SessionMeta, str]]) -> None:
        """Read-only ``load_session_meta`` for in-progress sessions.

        Uses per-session inflight locks so browser light reloads coalesce safely.
        Never writes ``_meta_cache.json`` or session artifacts.
        """
        from .. import parser as parser_mod
        from ..session_inflight import KIND_REFRESH, end, request_rerun, try_begin

        updates: list[tuple[str, SessionMeta, str]] = []
        pending_reruns: list[Path] = []
        try:
            for meta, label in live_rows:
                sd = Path(meta.session_dir)
                if not try_begin(KIND_REFRESH, sd):
                    request_rerun(KIND_REFRESH, sd)
                    continue
                try:
                    fresh = parser_mod.load_session_meta(sd, include_timeline_count=False)
                    fresh.num_events = meta.num_events
                    try:
                        key = str(sd.resolve())
                    except OSError:
                        key = str(sd)
                    if (
                        fresh.context_usage_compact != meta.context_usage_compact
                        or fresh.turn_outcome != meta.turn_outcome
                        or fresh.list_status_label() != meta.list_status_label()
                        or fresh.duration_seconds != meta.duration_seconds
                        # Grok fills generated_title after start; list must refresh.
                        or (fresh.title or "") != (meta.title or "")
                        or (fresh.summary_text or "") != (meta.summary_text or "")
                    ):
                        updates.append((key, fresh, label))
                finally:
                    if end(KIND_REFRESH, sd):
                        pending_reruns.append(sd)
        finally:

            def _apply() -> None:
                self._live_meta_heartbeat_busy = False
                if self._exiting:
                    return
                if updates:
                    by_key: dict[str, int] = {}
                    for idx, (m, _lab) in enumerate(self._meta_only):
                        try:
                            by_key[str(Path(m.session_dir).resolve())] = idx
                        except OSError:
                            by_key[str(m.session_dir)] = idx
                    changed = False
                    for key, fresh, label in updates:
                        row_idx = by_key.get(key)
                        if row_idx is None:
                            continue
                        self._meta_only[row_idx] = (fresh, label)
                        changed = True
                    if changed:
                        with suppress(Exception):
                            self._populate_session_table()
                for sd in pending_reruns:
                    self._dispatch_refresh_rerun(sd)

            try:
                call_ui(self, _apply)
            except Exception:
                self._live_meta_heartbeat_busy = False

    @work(thread=True)
    def _scan_live_sessions_worker(self) -> None:
        """Find/peek session dirs off the UI thread."""
        try:
            self._scan_live_sessions_into_table()
        except Exception:
            logger.debug("live sessions scan failed", exc_info=True)
        finally:
            self._live_sessions_busy = False

    def _on_background_run_status(self, status) -> None:
        """Worker-thread status callback: session_dir may appear mid-run."""
        if self._exiting or self.run_manager.ui_detached:
            return
        try:
            if getattr(status, "session_dir", None) is None:
                return
            if not self.is_running:
                return
            # post_message is thread-safe; call_from_thread raises if already on the app thread
            # (e.g. quit/cancel races).
            self.post_message(self._BgStatus(status))
        except Exception:
            pass

    def on_trace_eval_app__bg_status(self, event: _BgStatus) -> None:
        if self._exiting or self.run_manager.ui_detached:
            return
        with suppress(Exception):
            self._on_live_session_discovered(event.status)

    def _on_live_session_discovered(self, status) -> None:
        """UI-thread: ensure a mid-run session is in the sessions list."""
        self._schedule_run_status_update()
        sd = getattr(status, "session_dir", None)
        if sd is None:
            return
        try:
            sd_path = Path(sd)
        except Exception:
            return
        if not sd_path.is_dir():
            return
        runner_traces = self._runner_traces_root()
        try:
            if not self.traces_path or not Path(self.traces_path).exists():
                self.traces_path = runner_traces
                self._update_session_paths_banner()
        except Exception:
            pass
        # Coalesce with the interval scan — do not spawn extra workers per status.
        if not self._live_sessions_busy:
            self._live_sessions_busy = True
            self._scan_live_sessions_worker()
        try:
            self._request_live_share(sd_path, status=status)
        except Exception:
            pass

    def _request_live_share(self, session_dir: Path, *, status=None, force: bool = False) -> None:
        """Re-read groket-share.json (written in-container via ``grok share`` only)."""
        from ..runs.live_share import get_share_url, refresh_share_from_disk

        sd = Path(session_dir)
        _ = force
        url = refresh_share_from_disk(sd) or get_share_url(sd)
        if not url:
            return
        if status is not None:
            try:
                status.share_url = url
            except Exception:
                pass
        self._maybe_notify_share_url(sd, url)

    def _maybe_notify_share_url(self, session_dir: Path, share_url: str) -> None:
        """Share updates are normal workflow (Jobs/Browser/s key); no toast spam."""
        _ = (session_dir, share_url)

    def _scan_live_sessions_into_table(self) -> None:
        """Background-only: discover new sessions + refresh turn status for live ones.

        Rules (keep this boring and cheap):
        - While runs are active: only dirs we already know from ``BackgroundRun``
          statuses (or one shallow peek per container). **No** full traces walk.
        - Idle: full ``find_sessions`` at most every ``LIVE_POLL_FULL_WALK_INTERVAL``.
        - Known sessions: update ``turn_outcome`` only (gate files), never re-parse
          ``updates.jsonl`` on the list poll.
        - New sessions only: ``load_session_meta`` once.
        - UI: one ``call_ui`` apply if anything actually changed — no share spam.
        - Attach client: quiet ``session/list`` refresh (min_gap, keep analysis).
        - Skip entirely while a catalog reload is in flight (toggle/F5 owns the list).
        """
        import time

        from .. import parser as parser_mod
        from ..constants import LIVE_POLL_ACTIVE_INTERVAL, LIVE_POLL_FULL_WALK_INTERVAL
        from ..parser import session_trace_mtime

        if self._sessions_catalog_busy:
            return

        now = time.time()
        active_n = int(self.run_manager.active_count or 0)
        # Product path: quiet session/list only (no local full-walk thrash).
        if self._control_socket is not None:
            if not self._control_attached:
                return
            min_gap = LIVE_POLL_FULL_WALK_INTERVAL
            if now - self._live_sessions_last_scan < min_gap:
                return
            self._live_sessions_last_scan = now
            gen = self._begin_sessions_load()
            try:
                self._load_sessions_via_control(gen, quiet=True, clear_plugins=False)
            finally:
                pass
            return

        # Offline (--no-serve): local traces scan only.
        min_gap = LIVE_POLL_ACTIVE_INTERVAL
        if now - self._live_sessions_last_scan < min_gap:
            return
        self._live_sessions_last_scan = now

        runner_traces = self._runner_traces_root()
        if not runner_traces.exists():
            return

        found: list[Path] = []
        if active_n:
            try:
                for bg in self.run_manager.list_active():
                    for cfg in bg.configs:
                        sd: Path | None = None
                        try:
                            st = bg.statuses.get(cfg.container_name)
                            if st is not None and st.session_dir is not None:
                                p = Path(st.session_dir)
                                if p.is_dir():
                                    sd = p
                        except Exception:
                            sd = None
                        if sd is None:
                            try:
                                traces_dir = (
                                    self.run_manager.orchestrator.work_dir
                                    / "traces"
                                    / cfg.container_name
                                )
                                if traces_dir.is_dir():
                                    # Pruned walk for one container (skips *.stage / plugins).
                                    sessions = find_sessions(traces_dir)
                                    if sessions:
                                        from ..parser import session_trace_mtime

                                        sd = max(sessions, key=session_trace_mtime)
                            except Exception:
                                sd = None
                        if sd is not None:
                            found.append(sd)
                            try:
                                st = bg.statuses.get(cfg.container_name)
                                if st is not None and st.session_dir is None:
                                    st.session_dir = sd
                            except Exception:
                                pass
            except Exception:
                pass
        elif now - self._live_full_walk_last >= LIVE_POLL_FULL_WALK_INTERVAL:
            self._live_full_walk_last = now
            try:
                found.extend(find_sessions(runner_traces))
            except Exception:
                pass

        if not found:
            return

        # Snapshot previous outcomes by path key (read-only).
        prev_outcome: dict[str, str] = {}
        existing_keys: set[str] = set()
        for meta, _lab in list(self._meta_only):
            try:
                k = str(Path(meta.session_dir).resolve())
            except Exception:
                k = str(meta.session_dir)
            existing_keys.add(k)
            prev_outcome[k] = meta.turn_outcome or ""

        new_metas: list[tuple[str, SessionMeta, str]] = []
        outcome_updates: list[tuple[str, str]] = []  # key, new outcome
        changed_sessions: dict[str, Path] = {}

        for sd in found:
            try:
                sd_res = sd if sd.is_absolute() else runner_traces / sd
                if not sd_res.is_dir():
                    continue
                key = str(sd_res.resolve())
            except Exception:
                continue
            try:
                mtime = session_trace_mtime(sd_res)
            except Exception:
                mtime = 0.0

            if key not in existing_keys:
                try:
                    meta = load_session_meta(sd_res)
                except Exception:
                    continue
                origin = self._origin_for_dir(sd_res)
                meta.origin = origin
                label = self._label_for_session(sd_res, origin)
                self._session_mtimes[key] = mtime
                new_metas.append((key, meta, label))
                continue

            # Known session: gate probe + light meta for live rows (title, status).
            # Always allow live outcomes even when the row was ``completed`` —
            # multi-turn harness marks each closed turn complete, then the next
            # follow-up is running / awaiting again. Never apply non-live probe
            # results (that would invent interrupted/cancelled).
            if mtime > 0:
                previous_mtime = self._session_mtimes.get(key)
                if previous_mtime is not None and mtime > previous_mtime:
                    changed_sessions[key] = sd_res
                self._session_mtimes[key] = mtime
            try:
                outcome = parser_mod.list_turn_outcome_for_dir(sd_res)
            except Exception:
                continue
            oc = (outcome or "").strip().lower().replace(" ", "_")
            live_oc = oc in (
                "running",
                "ending",
                "in_progress",
                "pending",
                "awaiting_follow_up",
            )
            # While live, light meta reload so generated_title / status update
            # without restarting the app (outcome-only probe skips summary.json).
            prev = (prev_outcome.get(key) or "").strip().lower().replace(" ", "_")
            prev_live = prev in (
                "running",
                "ending",
                "in_progress",
                "pending",
                "awaiting_follow_up",
            )
            if not live_oc and prev_live:
                try:
                    origin = self._origin_for_dir(sd_res)
                    fresh = parser_mod.load_session_meta_list(sd_res, origin=origin)
                    label = self._label_for_session(sd_res, origin)
                    new_metas.append((key, fresh, label))
                except Exception:
                    logger.debug("settle list row %s", sd_res, exc_info=True)
                continue
            if live_oc:
                try:
                    fresh = parser_mod.load_session_meta(sd_res, include_timeline_count=False)
                    # List probe is authoritative for live turn status (gate/freshness).
                    if outcome:
                        fresh.turn_outcome = outcome
                    for meta0, _lab0 in list(self._meta_only):
                        try:
                            if str(Path(meta0.session_dir).resolve()) == key:
                                fresh.num_events = meta0.num_events
                                break
                        except OSError:
                            if str(meta0.session_dir) == key:
                                fresh.num_events = meta0.num_events
                                break
                    origin = self._origin_for_dir(sd_res)
                    fresh.origin = origin
                    label = self._label_for_session(sd_res, origin)
                    new_metas.append((key, fresh, label))  # replace existing row in _apply
                except Exception:
                    if outcome != prev_outcome.get(key):
                        outcome_updates.append((key, outcome))
                continue

        if not new_metas and not outcome_updates and not changed_sessions:
            return

        def _apply() -> None:
            by_key: dict[str, int] = {}
            for idx, (meta, _lab) in enumerate(self._meta_only):
                try:
                    by_key[str(Path(meta.session_dir).resolve())] = idx
                except Exception:
                    by_key[str(meta.session_dir)] = idx
            changed = False
            for key, meta, label in new_metas:
                idx_opt = by_key.get(key)
                if idx_opt is not None:
                    # Known live row: replace meta (title / status / context).
                    prev_m, prev_lab = self._meta_only[idx_opt]
                    if not (meta.origin or "").strip():
                        meta.origin = prev_m.origin or "work"
                    if (
                        prev_m.title != meta.title
                        or prev_m.turn_outcome != meta.turn_outcome
                        or prev_m.list_status_label() != meta.list_status_label()
                        or prev_m.context_usage_compact != meta.context_usage_compact
                        or prev_m.duration_seconds != meta.duration_seconds
                        or prev_m.summary_text != meta.summary_text
                    ):
                        self._meta_only[idx_opt] = (meta, prev_lab)
                        changed = True
                    continue
                self._meta_only.append((meta, label))
                by_key[key] = len(self._meta_only) - 1
                changed = True
            for key, outcome in outcome_updates:
                idx_opt = by_key.get(key)
                if idx_opt is None:
                    continue
                idx = idx_opt
                meta, label = self._meta_only[idx]
                if meta.turn_outcome != outcome:
                    meta.turn_outcome = outcome
                    self._meta_only[idx] = (meta, label)
                    changed = True
            if changed:
                with suppress(Exception):
                    self._populate_session_table()
            for session_dir in changed_sessions.values():
                self.control_session_changed(session_dir)

        try:
            call_ui(self, _apply)
        except Exception:
            with suppress(Exception):
                _apply()

    def _merge_session_dirs(
        self, session_dirs: list[Path], *, traces_root: Path | None = None
    ) -> None:
        """Add new session dirs (full meta once). Safe from tests / one-off callers.

        Live polling uses :meth:`_scan_live_sessions_into_table` instead.
        """
        if not session_dirs:
            return
        root = traces_root or self._runner_traces_root()
        existing: set[str] = set()
        for meta, _lab in list(self._meta_only):
            try:
                existing.add(str(Path(meta.session_dir).resolve()))
            except Exception:
                existing.add(str(meta.session_dir))
        added = False
        for sd in session_dirs:
            try:
                sd_res = sd if sd.is_absolute() else root / sd
                if not sd_res.is_dir():
                    continue
                key = str(sd_res.resolve())
            except Exception:
                continue
            if key in existing:
                continue
            try:
                meta = load_session_meta(sd_res)
            except Exception:
                continue
            origin = self._origin_for_dir(sd_res)
            meta.origin = origin
            label = self._label_for_session(sd_res, origin)
            self._meta_only.append((meta, label))
            existing.add(key)
            added = True
        if added:
            with suppress(Exception):
                self._populate_session_table(force=True)

    def _on_background_run_finished(self, run: BackgroundRun) -> None:
        """Notify from worker thread when a backgrounded eval completes."""
        if self._exiting or self.run_manager.ui_detached:
            return
        try:
            if not self.is_running:
                return
            self.post_message(self._BgFinished(run))
        except Exception:
            pass

    def on_trace_eval_app__bg_finished(self, event: _BgFinished) -> None:
        if self._exiting or self.run_manager.ui_detached:
            return
        with suppress(Exception):
            self._notify_run_finished(event.run)

    def _prepare_clean_exit(self) -> None:
        """Detach UI from background jobs so ``q`` returns promptly.

        Docker containers and daemon worker threads keep running under dockerd
        (interactive sessions stay resumable on reopen). We only stop timers and
        UI callbacks that would block Textual shutdown via ``call_from_thread``.
        """
        self._exiting = True
        stop = self._control_notify_stop
        if stop is not None:
            stop.set()
        for attr in (
            "_run_status_timer",
            "_live_sessions_timer",
            "_live_sessions_heartbeat_timer",
        ):
            timer = getattr(self, attr, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
                setattr(self, attr, None)
        w = self._traces_watch
        self._traces_watch = None
        stop = getattr(w, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
        try:
            for screen in list(self.screen_stack):
                stop = getattr(screen, "_stop_live_refresh", None)
                if callable(stop):
                    stop()
        except Exception:
            logger.debug(t("ui-stop-live-refresh-on-quit-failed"), exc_info=True)
        try:
            self.run_manager.detach_ui()
        except Exception:
            logger.debug(t("ui-detach-ui-on-quit-failed"), exc_info=True)
        try:
            workers_cancel = getattr(self, "workers", None)
            if workers_cancel is not None and hasattr(workers_cancel, "cancel_all"):
                workers_cancel.cancel_all()
        except Exception:
            logger.debug(t("ui-workers-cancel-on-quit-failed"), exc_info=True)

    async def action_quit(self) -> None:
        """Quit the TUI without waiting for in-flight eval containers."""
        self._prepare_clean_exit()
        self.exit()

    def _notify_run_finished(self, run: BackgroundRun) -> None:
        from ..utils import fmt_duration

        self._schedule_run_status_update()
        try:
            self._scan_live_sessions_into_table()
        except Exception:
            pass
        if run.quiet or run.batch_id:
            return
        if self.run_manager.batch_active:
            return
        if self._run_manager_batch_ids():
            return
        elapsed = fmt_duration(run.elapsed_s)
        if run.error:
            self.notify(
                t(
                    "notify-run-failed",
                    id=run.run_id,
                    elapsed=elapsed,
                    error=run.error[:120],
                ),
                severity="error",
                timeout=12,
            )
            return
        completed = sum(1 for r in run.results if r.status == "completed")
        failed = sum(1 for r in run.results if r.status == "failed")
        total = len(run.results) or len(run.configs)
        if failed:
            self.notify(
                t(
                    "notify-run-finished",
                    id=run.run_id,
                    elapsed=elapsed,
                    ok=completed,
                    total=total,
                    failed=failed,
                ),
                severity="error",
                timeout=12,
            )
        try:
            self._load_sessions(quiet=True)
            self._update_session_paths_banner()
        except Exception:
            pass

    def action_open_rules(self) -> None:
        self.push_screen(RulesScreen())

    def action_analysis_settings(self) -> None:
        """Open modal to configure session/feedback analyzer plugins."""

        def _done(saved: bool | None) -> None:
            if saved:
                try:
                    svc = self._analysis_svc()
                    n = len([p for p in svc.list_plugins() if p.id != "noop"])
                    self.notify(
                        join_ui(t("ui-analysis"), n, t("ui-plugin-s-1")),
                        severity="information",
                        timeout=8,
                    )
                except Exception:
                    self.notify(U.analysis_settings_saved(), severity="information")

        self.push_screen(AnalysisSettingsModal(self.work_dir), _done)

    def action_refresh_context(self) -> None:
        """Refresh whatever screen/context is active (F5 / Ctrl+R globally)."""
        from .screens.browser import BrowserScreen
        from .screens.personas import PersonasScreen
        from .screens.rules import RulesScreen
        from .screens.run_configs import RunConfigsScreen
        from .screens.runner import RunnerScreen

        screen = self.screen
        if isinstance(screen, BrowserScreen):
            screen.action_refresh_context()
            return
        if isinstance(screen, RunConfigsScreen):
            screen.action_refresh_context()
            return
        if isinstance(screen, PersonasScreen):
            screen.action_refresh_context()
            return
        if isinstance(screen, RunnerScreen):
            screen.action_refresh_context()
            return
        if isinstance(screen, RulesScreen):
            screen.action_refresh_context()
            return
        self._refresh_sessions_list()

    def _refresh_sessions_list(self) -> None:
        """Reload the sessions table from work traces (+ host when enabled).

        Debounced + exclusive catalog worker — do not race populate here.
        """
        roots = self._session_catalog_roots()
        desc = ", ".join(str(r.path) for r in roots)
        if not any(r.path.exists() for r in roots):
            self.notify(t("notify-nothing-to-refresh", path=desc), severity="warning")
            return
        self._update_session_paths_banner()
        self._schedule_sessions_reload(delay=0.05)

    def action_open_session(self) -> None:
        """Open the highlighted session (same as Enter on sessions table)."""
        try:
            table = self.query_one("#session-table", DataTable)
            if not table.row_count:
                return
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            if row_key and row_key.value:
                self._open_session(str(row_key.value))
        except Exception:
            pass

    def action_show_help(self) -> None:
        from .bindings import notify_help

        notify_help(self.screen)
