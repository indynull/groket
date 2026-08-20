"""Trace browser screen — interactive timeline with detail view and feedback."""

from __future__ import annotations

import logging
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult

from ..data_table import cursor_row_key, restore_cursor, style_data_table
from ..i18n import join_ui, t

logger = logging.getLogger(__name__)
from collections import Counter, defaultdict

from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Click
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    LoadingIndicator,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from ... import event_types as et
from ...analysis.base import AnalysisResult, Finding
from ...analysis.order import order_report_markdown_by_turn, sort_findings_by_turn
from ...flags import load_flags, save_flags
from ...integrations.control import ControlError
from ...models import Flag, JsonObject, SessionMeta, ToolInputBag, TraceEvent, as_json_object
from ...notes import (
    NoteEntry,
    NotesConflict,
    NotesDoc,
    delete_note,
    load_notes,
    load_schema,
    notes_snapshot,
    upsert_note,
)
from ...parser import load_session_meta, parse_timeline
from ...session.jobs import SessionJobs, schedule_for_event, session_jobs_for_view
from ...session.subagents import (
    SubagentRun,
    compact_child_chrome,
    is_subagent_session_dir,
    parent_session_dir,
    read_session_kind,
    resolve_child_session_path,
    subagent_runs_for_view,
)
from ...session.turns import (
    event_display_turn_map,
    event_matches_timeline_kind,
    segment_timeline_turns,
)
from ...session.workflows import (
    WorkflowRun,
    workflow_event_index,
    workflow_for_event,
)
from ...session.workspace_diff import WorkspaceDiff, load_workspace_diff_doc
from ...utils import fmt_duration
from .. import text as U
from ..bindings import BROWSER, ChromeActions, focus_primary_list
from ..control_notice import control_operator_text
from ..panel_render import (
    EmptyState,
    bullet,
    content_block,
    dim_rule,
    kv_line,
    panel_group,
    section_header,
    status_chip,
)
from ..report_panes import split_report_markdown_panes
from ..selectable_static import SelectableStatic, is_extractable_static
from ..session_summary import render_session_summary
from ..styles import SEVERITY_LABEL, severity_style
from ..tab_panes import TabPaneNavigation
from ..threads import call_ui, resolve_ui_app
from ..widgets.controls import FILTER_BAR_CLASS, FILTER_LABEL_CLASS
from ..widgets.detail_view import DetailView
from ..widgets.diff_view import DiffView
from ..widgets.flag_panel import FlagModal
from ..widgets.notes_modal import NotesModal, NotesPickModal
from ..widgets.timeline import TimelineTable

_CHROME_LABEL_MAX = 48


def _clip_chrome_label(text: str) -> str:
    """Fit a session name in the one-row header wordmark."""
    one = text.replace("\n", " ").strip()
    if len(one) <= _CHROME_LABEL_MAX:
        return one
    return one[: _CHROME_LABEL_MAX - 1] + "…"


class BrowserScreen(TabPaneNavigation, ChromeActions):
    """Interactive trace browser with timeline, detail view, and findings."""

    BINDINGS = list(BROWSER)
    TAB_CONTENT_ID = "browser-tabs"
    TAB_PANES = (
        ("tab-timeline", "#timeline-list"),
        ("tab-summary", "#summary-scroll"),
        ("tab-diff", "#diff-file-list"),
        ("tab-findings", "#findings-table"),
        ("tab-reports", "#reports-scroll"),
    )
    _diff_doc: WorkspaceDiff

    def activate_tab_pane(self, pane_id: str, *, focus_selector: str | None = None) -> None:
        if pane_id != "tab-timeline" and self._event_reader:
            self._set_event_reader(False)
        super().activate_tab_pane(pane_id, focus_selector=focus_selector)
        if pane_id == "tab-diff":
            # Paint the doc loaded on the worker. Do not re-parse the timeline
            # here — that froze the UI on every Diff tab switch.
            self._update_diff_tab()
        self.refresh_bindings()

    @on(TabbedContent.TabActivated, "#browser-tabs")
    def _on_browser_tab_activated(self, _event: TabbedContent.TabActivated) -> None:
        """Fill the showing pane (tab bar click or digit key) and refresh footer keys."""
        self._paint_visible_secondary_panes()
        self.refresh_bindings()

    def action_tab_timeline(self) -> None:
        self.activate_tab_pane("tab-timeline")

    def action_tab_summary(self) -> None:
        self.activate_tab_pane("tab-summary")

    def action_tab_diff(self) -> None:
        self.activate_tab_pane("tab-diff")

    def action_tab_findings(self) -> None:
        self.activate_tab_pane("tab-findings")

    def action_tab_report(self) -> None:
        self.activate_tab_pane("tab-reports")

    def __init__(
        self,
        session_dir: Path,
        plugin_results: dict[str, AnalysisResult] | None = None,
        *,
        prompt_index: int | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.session_dir = session_dir
        self.meta: SessionMeta | None = None
        self.timeline: list[TraceEvent] = []
        self.plugin_results: dict[str, AnalysisResult] = plugin_results or {}
        self._analysis_stale_hints: list[str] = []
        self._analysis_pending: bool = False
        self._findings: list[Finding] = []
        self._findings_by_call: dict[str, Finding] = {}
        self._errors_only = False
        self._current_event: TraceEvent | None = None
        self._findings_table_entries: list[Finding] = []
        self._selected_finding: Finding | None = None
        self._flags: dict[int, Flag] = {}
        self._notes_doc: NotesDoc = NotesDoc()
        self._notes_loaded: bool = False
        self._load_started = False
        self._diff_doc = WorkspaceDiff(())
        self._timeline_filter: str = "all"
        self._timeline_search: str = ""
        self._requested_prompt_index = prompt_index
        self._report_section_keys: set[str] = set()
        self._report_filter: str = "all"
        self._report_select_options_key: tuple[str, ...] = ()
        self._report_updating: bool = False
        self._live_refresh_timer: Timer | None = None
        self._live_heartbeat_timer: Timer | None = None
        # Slow probe while the session looks idle so a resumed agent re-arms hot live.
        self._live_recheck_timer: Timer | None = None
        self._analysis_spinner_timer: Timer | None = None
        self._trace_watch: object | None = None  # fs_watch stop handle
        self._last_light_fp: tuple[str | int | float | bool | None, ...] | None = None
        # session_timeline_stamp() when set (mtime + sizes); not signals.json.
        self._last_trace_mtime: tuple[float, int, int, int] | None = None
        self._last_overview_stamp: object | None = None
        self._last_signals_mtime: float | None = None
        self._delete_pending: bool = False
        self._live_refresh_busy = False
        self._live_refresh_pending = False
        self._light_refresh_heartbeat = False
        self._last_timeline_parse_at: float = 0.0
        self._last_light_submit_at: float = 0.0
        self._live_refresh_deferred: Timer | None = None
        # Cached for check_action — never re-scan gates/events on every key.
        self._pending_actions_enabled: bool = False
        self._pending_cache_valid: bool = False
        # Cached live-timeline need (FS ticks hit this on the UI thread).
        self._needs_live_timeline: bool = False
        self._needs_live_timeline_valid: bool = False
        self._last_turn_segment_count: int = -1
        # (timeline_len, last_event_index) — skip re-segment only when tail unchanged.
        self._turn_rebuild_sig: tuple[int, int | None] | None = None
        self._detail_debounce: Timer | None = None
        self._search_debounce: Timer | None = None
        self._detail_expanded: set[int] = set()
        self._detail_expanding: set[int] = set()
        from ...session.context_samples import ContextSampleStore

        self._context_samples = ContextSampleStore()
        self._subagent_runs: list[SubagentRun] = []
        self._session_jobs = SessionJobs(jobs=[], schedules=[])
        self._overview_payload: JsonObject | None = None
        self._summary_turn_first: dict[int, int] = {}
        self._event_reader: bool = False

    def _analysis_svc(self):
        """Use the app's analysis service (work_dir / config), not a bare default."""
        app = getattr(self, "_app", None)
        if app is None:
            try:
                app = self.app
            except Exception:
                app = None
        getter = getattr(app, "_analysis_svc", None) if app is not None else None
        if callable(getter):
            return getter()
        from ...analysis.service import get_analysis_service

        return get_analysis_service()

    def compose(self) -> ComposeResult:
        from ..brand_mark import AppChrome, AppFooter

        yield AppChrome()
        analysis_wait = LoadingIndicator(id="browser-analysis-loading")
        analysis_wait.display = False
        yield analysis_wait
        with Vertical(id="session-pending-bar"):
            yield Static("", id="session-pending-status")
            yield Static("", id="session-pending-queue")
            yield Input(placeholder=U.follow_up_placeholder_send(), id="session-follow-input")
            yield Checkbox(
                t("follow-up-last-turn"),
                id="session-follow-last-turn",
                value=False,
            )
            with Horizontal(id="session-pending-actions"):
                yield Button(
                    U.follow_up_btn_send(), id="session-follow-send-btn", variant="primary"
                )
                yield Button(U.follow_up_btn_done(), id="session-follow-done-btn")
        with TabbedContent(id="browser-tabs"):
            with TabPane(U.tab_timeline(), id="tab-timeline"):
                with Horizontal(id="browser-layout"):
                    with Vertical(id="timeline-panel"):
                        with Horizontal(id="filter-bar", classes=FILTER_BAR_CLASS):
                            yield Static(
                                t("ui-filter"), id="filter-view-label", classes=FILTER_LABEL_CLASS
                            )
                            yield Select(
                                [
                                    (U.all_events(), "all"),
                                    (U.tools_only(), "tools"),
                                    (U.user_messages(), "user"),
                                    (U.assistant_messages(), "asst"),
                                    (U.session_markers(), "sess"),
                                    (t("ui-subagents-filter"), "subagents"),
                                    (t("ui-background-filter"), "background"),
                                    (t("ui-workflows-filter"), "workflows"),
                                    (U.errors_only(), "errors"),
                                ],
                                value="all",
                                id="timeline-view-select",
                                allow_blank=False,
                                classes="field-select",
                            )
                            turn_sel = Select(
                                [(t("turn-filter-all"), "all")],
                                value="all",
                                id="timeline-turn-select",
                                allow_blank=False,
                                classes="field-select",
                            )
                            turn_sel.display = False  # shown only when multi-turn
                            yield turn_sel
                            yield Input(
                                placeholder=U.search_events_placeholder(), id="search-input"
                            )
                            tail_label = Static(t("ui-timeline-tail"), id="timeline-tail-label")
                            tail_label.display = False
                            yield tail_label
                            with Vertical(id="timeline-tail-slot") as tail_slot:
                                tail_slot.display = False
                                yield Switch(
                                    id="timeline-tail",
                                    value=False,
                                    animate=False,
                                )
                        yield TimelineTable(id="timeline-list")
                    with Vertical(id="detail-column"):
                        yield DetailView(id="detail-panel")
            with TabPane(U.tab_summary(), id="tab-summary"):
                with VerticalScroll(id="summary-scroll"):
                    with Vertical(classes="panel-card"):
                        yield SelectableStatic(id="summary-content")
                    with Horizontal(id="summary-turns-pair", classes="summary-pair"):
                        with Vertical(id="summary-turns-card", classes="panel-card"):
                            yield Static(t("ui-turns-1"), classes="panel-card-title")
                            yield DataTable(id="stats-turns-table")
                        with Vertical(id="summary-subagents-card", classes="panel-card"):
                            yield Static(t("ui-subagent-runs"), classes="panel-card-title")
                            yield DataTable(id="stats-subagents-table")
                    with Vertical(id="summary-jobs-card", classes="panel-card"):
                        yield Static(t("ui-background-jobs"), classes="panel-card-title")
                        yield DataTable(id="stats-jobs-table")
                    with Vertical(id="summary-workflows-card", classes="panel-card"):
                        yield Static(t("ui-workflows"), classes="panel-card-title")
                        yield DataTable(id="stats-workflows-table")
                    with Horizontal(id="summary-stats-pair", classes="summary-pair"):
                        with Vertical(classes="panel-card"):
                            yield Static(U.event_types(), classes="panel-card-title")
                            yield DataTable(id="stats-events-table")
                        with Vertical(classes="panel-card"):
                            yield Static(U.tool_timing(), classes="panel-card-title")
                            yield DataTable(id="stats-tools-table")
                    with Vertical(classes="panel-card"):
                        yield Static(U.time_breakdown(), classes="panel-card-title")
                        yield DataTable(id="stats-phases-table")
            with TabPane(U.tab_diff(), id="tab-diff"):
                yield DiffView(id="diff-view")
            with TabPane(U.tab_findings(), id="tab-findings"):
                with Vertical(id="findings-panel"):
                    with Vertical(classes="panel-card"):
                        yield SelectableStatic("", id="findings-header")
                        # Row→timeline focus is on ``?`` / footer — no permanent tip box.
                    with Vertical(classes=t("ui-panel-card-panel-card-grow")):
                        yield DataTable(id="findings-table")
            with TabPane(U.tab_report(), id="tab-reports"):
                with Vertical(id="reports-panel"):
                    with Horizontal(id="report-filter-bar", classes=FILTER_BAR_CLASS):
                        yield Static(U.filter_label(), classes=FILTER_LABEL_CLASS)
                        yield Select(
                            [
                                (U.all_sections(), "all"),
                                (U.flags_only(), "flags"),
                                (U.notes_only(), "notes"),
                            ],
                            value="all",
                            id="report-view-select",
                            allow_blank=False,
                            classes=t("ui-field-select-report-view-select"),
                        )
                    with VerticalScroll(id="reports-scroll"):
                        with Vertical(classes="panel-card", id="report-section-overview"):
                            yield SelectableStatic(id="report-overview-content")
                            # Filter usage is on the filter bar itself — no tip box.
                            yield EmptyState("", id="report-analysis-empty")
                        with Vertical(
                            classes=t("ui-panel-card-report-section"), id="report-section-flags"
                        ):
                            yield SelectableStatic(id="report-flags-content")
                            yield EmptyState(U.tip_no_flags(), id="report-flags-empty")
                        with Vertical(
                            classes=t("ui-panel-card-report-section"), id="report-section-notes"
                        ):
                            yield SelectableStatic(id="report-notes-content")
                            yield EmptyState(U.tip_no_notes(), id="report-notes-empty")
                        yield Vertical(id="report-sections-host")
        yield AppFooter()

    def on_mount(self) -> None:
        if self._load_started:
            return
        self._load_started = True
        try:
            style_data_table(self.query_one("#findings-table", DataTable))
            for tid in (
                "#stats-turns-table",
                "#stats-subagents-table",
                "#stats-jobs-table",
                "#stats-workflows-table",
                "#stats-events-table",
                "#stats-tools-table",
                "#stats-phases-table",
            ):
                style_data_table(self.query_one(tid, DataTable))
        except Exception:
            pass
        self._load_data()

    def on_unmount(self) -> None:
        self._stop_analysis_spinner_timer()
        self._stop_live_refresh()
        if self._detail_debounce is not None:
            try:
                self._detail_debounce.stop()
            except Exception:
                pass
            self._detail_debounce = None
        if self._search_debounce is not None:
            try:
                self._search_debounce.stop()
            except Exception:
                pass
            self._search_debounce = None
        # Resume home-list FS watch paused while this browser owned the tree.
        pause = getattr(self.app, "_pause_home_traces_watch", None)
        if callable(pause):
            with suppress(Exception):
                pause(pause=False)

    def _stop_live_refresh(self) -> None:
        for attr in (
            "_live_refresh_timer",
            "_live_heartbeat_timer",
            "_live_refresh_deferred",
            "_live_recheck_timer",
        ):
            t = getattr(self, attr, None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
            setattr(self, attr, None)
        w = self._trace_watch
        self._trace_watch = None
        stop = getattr(w, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    def _stop_hot_live_refresh(self) -> None:
        """Stop FS watch + fast snapshot/heartbeat; leave slow recheck alone."""
        for attr in (
            "_live_refresh_timer",
            "_live_heartbeat_timer",
            "_live_refresh_deferred",
        ):
            t = getattr(self, attr, None)
            if t is not None:
                try:
                    t.stop()
                except Exception:
                    pass
            setattr(self, attr, None)
        w = self._trace_watch
        self._trace_watch = None
        stop = getattr(w, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    def _session_is_pending(self) -> bool:
        """True only for interactive multi-turn follow-up / Done UI.

        Single-turn evals still create a turn gate with ``state=running`` and never
        set ``awaiting_follow_up``; do not treat that as a follow-up bar.
        Stale / finalized gates never show the bar (see settle_stale_session_gates).

        Uses a short-lived cache so Textual ``check_action`` (every key / footer
        refresh) does not re-walk gates and ``events.jsonl`` on the UI thread.
        """
        if self._pending_cache_valid:
            return self._pending_actions_enabled
        return self._recompute_session_pending()

    def _recompute_session_pending(self) -> bool:
        """Disk probe for follow-up bar / action enablement; updates the cache."""
        from ...session.turn_gate import (
            final_turn_requested,
            host_requested_done,
            list_queued_follow_ups,
            read_staged_follow_up,
            read_turn_gate_status,
            session_activity_stale,
            session_awaits_follow_up,
            settle_stale_session_gates,
        )

        try:
            settle_stale_session_gates(self.session_dir)
        except Exception:
            pass

        pending = False
        try:
            st = read_turn_gate_status(self.session_dir)
        except Exception:
            st = {}
        gstate = str(st.get("state") or "")

        if gstate != "done":
            stale = False
            try:
                stale = bool(session_activity_stale(self.session_dir))
            except Exception:
                stale = False
            if not stale:
                if host_requested_done(self.session_dir) or final_turn_requested(self.session_dir):
                    pending = True
                else:
                    try:
                        if read_staged_follow_up(self.session_dir) is not None:
                            pending = True
                    except Exception:
                        pass
                    if not pending and session_awaits_follow_up(self.session_dir):
                        pending = True
                    if not pending:
                        try:
                            if list_queued_follow_ups(self.session_dir):
                                pending = True
                        except Exception:
                            pass

        self._pending_actions_enabled = pending
        self._pending_cache_valid = True
        return pending

    def _invalidate_pending_cache(self) -> None:
        """Drop cached follow-up enablement (call after gate / FS updates)."""
        self._pending_cache_valid = False
        self._needs_live_timeline_valid = False

    def _session_needs_live_timeline(self) -> bool:
        """True while the agent may still append traces (not idle follow-up wait).

        Domain rule: :func:`~groket.session.turn_gate.session_needs_live_timeline`.
        Orphan ``final_turn`` / stale ``status=running`` do **not** keep reloading.

        Cached between light refreshes so debounced FS events do not re-walk
        gates on the UI thread every few hundred ms during a live turn.
        """
        if self._needs_live_timeline_valid:
            return self._needs_live_timeline
        return self._recompute_needs_live_timeline()

    def _recompute_needs_live_timeline(self) -> bool:
        """Disk probe for live-timeline need; updates the cache."""
        from ...session.turn_gate import session_needs_live_timeline

        try:
            need = bool(session_needs_live_timeline(self.session_dir))
        except Exception:
            need = False
        self._needs_live_timeline = need
        self._needs_live_timeline_valid = True
        return need

    def _invalidate_live_timeline_cache(self) -> None:
        self._needs_live_timeline_valid = False

    def _refresh_session_pending_bar(self) -> None:
        from ...session.turn_gate import (
            drain_queued_follow_up,
            final_turn_requested,
            host_requested_done,
            list_queued_follow_ups,
            read_staged_follow_up,
            read_turn_gate_status,
            session_pending_label,
        )
        from ..session_status import localize_session_pending_label

        # Always re-probe gates when painting the bar (not the check_action cache).
        self._invalidate_pending_cache()
        show = self._session_is_pending()
        if show:
            try:
                drained = drain_queued_follow_up(self.session_dir)
                if drained:
                    preview = drained if len(drained) <= 48 else drained[:48] + "…"
                    self.notify(t("notify-queued-follow-up-sent", preview=preview))
            except Exception:
                pass

        meta = self.meta
        label = ""
        if show:
            try:
                label = session_pending_label(
                    self.session_dir,
                    turn_in_progress=bool(meta and meta.turn_in_progress),
                )
            except Exception:
                label = ""
            if not label and meta and meta.turn_in_progress:
                oc = (meta.turn_outcome or "").lower().replace(" ", "_")
                label = "ending_done" if oc in ("ending", "finishing") else "turn in progress"

        st: dict = {}
        try:
            st = read_turn_gate_status(self.session_dir)
        except Exception:
            pass
        queued: list[str] = []
        staged: tuple[str, bool] | None = None
        if show:
            try:
                queued = list_queued_follow_ups(self.session_dir)
            except Exception:
                queued = []
            try:
                staged = read_staged_follow_up(self.session_dir)
            except Exception:
                staged = None

        staged_fp: tuple[str, bool] | None = (
            (staged[0][:80], staged[1]) if staged is not None else None
        )

        try:
            bar = self.query_one("#session-pending-bar")
            bar.display = show
        except Exception:
            pass
        if not show:
            try:
                self.refresh_bindings()
            except Exception:
                pass
            return

        try:
            status = self.query_one("#session-pending-status", Static)
            if label:
                chip_label, chip_kind = localize_session_pending_label(label)
            else:
                chip_label, chip_kind = t("browser-status-idle"), "unknown"
            chip = status_chip(chip_label, kind=chip_kind)
            sid = str(st.get("session_id") or (meta.session_id if meta else ""))
            turn = st.get("turn", "")
            bits: list[str] = []
            if sid:
                bits.append(t("ui-session-prefix", id=sid))
            if turn != "" and turn is not None:
                bits.append(t("ui-turn-number", turn=turn))
            if staged is not None:
                bits.append(t("ui-staged-last-turn") if staged[1] else t("ui-staged-follow-up"))
            if queued:
                bits.append(t("ui-queued-count", n=len(queued)))
            extra = ("  ·  " + "  ·  ".join(bits)) if bits else ""
            status_fp = (chip_label, extra, staged_fp, tuple(queued[:5]), len(queued))
            if status_fp != getattr(self, "_pending_status_fp", None):
                self._pending_status_fp = status_fp
                if not self._widget_has_text_selection(status):
                    status.update(Text.assemble(chip, Text(extra, style="dim")))
        except Exception:
            pass
        try:
            q_widget = self.query_one("#session-pending-queue", Static)
            q_fp = (staged_fp, tuple(queued))
            if q_fp != getattr(self, "_pending_queue_fp", None):
                self._pending_queue_fp = q_fp
                lines: list[str] = []
                if staged is not None:
                    preview = staged[0].replace("\n", " ")
                    if len(preview) > 72:
                        preview = preview[:69] + "…"
                    head = (
                        t("browser-follow-up-staged-final")
                        if staged[1]
                        else t("browser-follow-up-staged")
                    )
                    lines.append(head)
                    lines.append(f"  {preview}")
                if queued:
                    lines.append(t("browser-follow-ups-pending", n=len(queued)))
                    for i, p in enumerate(queued[:5], start=1):
                        preview = p.replace("\n", " ")
                        if len(preview) > 72:
                            preview = preview[:69] + "…"
                        lines.append(f"  {i}. {preview}")
                    if len(queued) > 5:
                        lines.append(t("browser-more-queued", n=len(queued) - 5))
                if lines:
                    if not self._widget_has_text_selection(q_widget):
                        q_widget.update("\n".join(lines))
                    q_widget.display = True
                else:
                    if not self._widget_has_text_selection(q_widget):
                        q_widget.update("")
                    q_widget.display = False
        except Exception:
            pass

        # Host already requested stop / last turn: Done is inert; still allow
        # viewing queue but do not accept new follow-ups.
        finishing = host_requested_done(self.session_dir) or final_turn_requested(self.session_dir)
        meta_ending = bool(
            meta and (meta.turn_outcome or "").lower().replace(" ", "_") in ("ending", "finishing")
        )
        finishing = finishing or meta_ending
        awaiting = str(st.get("state") or "") == "awaiting_follow_up" and not finishing
        can_send = show and not finishing
        try:
            self.query_one("#session-follow-send-btn", Button).disabled = not can_send
            self.query_one("#session-follow-done-btn", Button).disabled = not show or finishing
        except Exception:
            pass
        try:
            hint = self.query_one("#session-follow-input", Input)
            if finishing:
                hint.placeholder = t("status-ending")
            elif awaiting:
                hint.placeholder = U.follow_up_placeholder_awaiting()
            elif can_send:
                hint.placeholder = U.follow_up_placeholder_queue()
            hint.disabled = not can_send
        except Exception:
            pass
        try:
            self.query_one("#session-follow-last-turn", Checkbox).disabled = not can_send
        except Exception:
            pass
        self._sync_timeline_tail_checkbox()
        try:
            self.refresh_bindings()
        except Exception:
            pass

    def _session_follow_send(self) -> None:
        from ...session.turn_gate import write_follow_up_for_session

        try:
            text = self.query_one("#session-follow-input", Input).value.strip()
        except Exception:
            text = ""
        if not text:
            self.notify(U.follow_up_empty(), severity="warning")
            return
        final = False
        with suppress(Exception):
            final = bool(self.query_one("#session-follow-last-turn", Checkbox).value)
        try:
            how = write_follow_up_for_session(self.session_dir, text, final=final)
            self.query_one("#session-follow-input", Input).value = ""
            with suppress(Exception):
                self.query_one("#session-follow-last-turn", Checkbox).value = False
            if how == "queued":
                self.notify(t("follow-up-queued-final") if final else U.follow_up_queued())
            else:
                self.notify(t("follow-up-sent-final") if final else U.follow_up_sent())
            # Session-scoped only: write_follow_up_for_session targets this
            # session's traces volume. Do not call RunManager.submit_follow_up
            # (run_id fans out to every container in a multi-model run).
        except Exception as exc:
            self.notify(U.follow_up_failed(exc), severity="error")
        self._invalidate_pending_cache()
        self._refresh_session_pending_bar()
        self._schedule_live_refresh()

    def _session_follow_done(self) -> None:
        from ...session.turn_gate import write_done_for_session

        try:
            write_done_for_session(self.session_dir)
            rm = getattr(self.app, "run_manager", None)
            if rm is not None and hasattr(rm, "stop_session_container"):
                try:
                    rm.stop_session_container(self.session_dir)
                except Exception:
                    pass
            # Do not imply the agent finished — only that stop was requested.
            # Do not complete_interactive(run_id): that stops every sibling
            # container in a multi-session run.
            self.notify(t("mark-done-requested"))
        except Exception as exc:
            self.notify(U.mark_session_done_failed(exc), severity="error")
        self._invalidate_pending_cache()
        self._refresh_session_pending_bar()
        self._schedule_live_refresh()

    @on(Button.Pressed, "#session-follow-send-btn")
    def _on_session_follow_send_btn(self) -> None:
        self._session_follow_send()

    @on(Button.Pressed, "#session-follow-done-btn")
    def _on_session_follow_done_btn(self) -> None:
        self._session_follow_done()

    @on(Input.Submitted, "#session-follow-input")
    def _on_session_follow_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._session_follow_send()

    def action_send_follow_up(self) -> None:
        """Send or queue follow-up from the pending bar (when interactive)."""
        if not self.check_action("send_follow_up", ()):
            return
        self._session_follow_send()

    def action_mark_session_done(self) -> None:
        """``e`` — end interactive session when awaiting / multi-turn."""
        if not self.check_action("mark_session_done", ()):
            return
        self._session_follow_done()

    def action_focus_follow_up(self) -> None:
        """``n`` — focus the next-prompt field when the session supports it."""
        if not self.check_action("focus_follow_up", ()):
            return
        try:
            inp = self.query_one("#session-follow-input", Input)
            if not inp.display and (not self._pending_bar_visible()):
                return
            inp.focus()
        except Exception:
            pass

    def _pending_bar_visible(self) -> bool:
        try:
            return bool(self.query_one("#session-pending-bar").display)
        except Exception:
            return False

    def _live_watch_root(self) -> Path:
        """Directory to watch for live refresh.

        Watch the **session dir only** (``updates.jsonl`` / ``events.jsonl`` /
        ``signals.json``). Watching the whole traces volume doubles FS noise
        (sibling sessions, seed trees) and freezes the TUI mid-run. Turn-gate
        transitions are polled on the snapshot / pending-bar path instead.

        Host Grok session dirs (and any symlink) are resolved so the OS watch
        sees real file writes under ``~/.grok/sessions``.
        """
        root = Path(self.session_dir)
        try:
            if root.is_symlink() or root.exists():
                return root.resolve()
        except OSError:
            pass
        return root

    def _schedule_live_refresh(self) -> None:
        """Arm session-dir FS watch + timer backup while the session is live.

        A running agent rewrites ``updates.jsonl`` many times per second.
        Debounced FS events (and a slow timer backup) drive light reloads;
        the job always re-parses when the timeline stamp changes so new tool
        rows appear without exiting the screen. Content-only stream rewrites
        are ignored by :meth:`TimelineTable.load_events` (append-only).

        When the session looks idle (common for **host** sessions with no turn
        gate, once ``updates.jsonl`` ages past the fresh window), keep a slow
        recheck so a resumed agent re-arms hot live without F5.
        """
        pending_ui = self._session_is_pending()
        live_traces = self._session_needs_live_timeline()
        if not pending_ui and not live_traces:
            self._downgrade_live_refresh_to_recheck()
            self._refresh_session_pending_bar()
            return
        self._stop_live_recheck_timer()
        self._refresh_session_pending_bar()
        from ...constants import (
            LIVE_BROWSER_FS_DEBOUNCE_S,
            LIVE_BROWSER_SNAPSHOT_INTERVAL,
            LIVE_POLL_HEARTBEAT_INTERVAL,
        )
        from ...fs_watch import TraceTreeWatch

        if self._trace_watch is None:

            def _on_fs() -> None:
                try:
                    if self.app is not None and self.app.is_running:
                        self.app.call_from_thread(self._live_refresh_from_fs)
                except Exception:
                    pass

            watch = TraceTreeWatch(
                self._live_watch_root(),
                _on_fs,
                debounce_s=LIVE_BROWSER_FS_DEBOUNCE_S,
            )
            if watch.start():
                self._trace_watch = watch
            # If inotify fails, the snapshot timer below is the sole driver.

        if self._live_refresh_timer is None:
            self._live_refresh_timer = self.set_interval(
                LIVE_BROWSER_SNAPSHOT_INTERVAL,
                self._live_refresh_snapshot,
            )
        if self._live_heartbeat_timer is None:
            self._live_heartbeat_timer = self.set_interval(
                LIVE_POLL_HEARTBEAT_INTERVAL,
                self._live_refresh_heartbeat,
            )

    def _stop_live_recheck_timer(self) -> None:
        t = getattr(self, "_live_recheck_timer", None)
        self._live_recheck_timer = None
        if t is not None:
            try:
                t.stop()
            except Exception:
                pass

    def _downgrade_live_refresh_to_recheck(self) -> None:
        """Drop hot FS/snapshot polling; keep a slow re-arm probe.

        Imported live sessions often have no ``.groket-turn`` gate, so
        :func:`~groket.session.turn_gate.session_needs_live_timeline` is only
        true while ``updates.jsonl`` is fresh. Fully stopping live left the
        browser frozen after a quiet gap until the operator hit refresh.
        """
        self._stop_hot_live_refresh()
        if self._live_recheck_timer is not None:
            return
        from ...constants import LIVE_POLL_WATCH_FALLBACK_INTERVAL

        self._live_recheck_timer = self.set_interval(
            LIVE_POLL_WATCH_FALLBACK_INTERVAL,
            self._live_recheck_tick,
        )

    def _live_recheck_tick(self) -> None:
        """UI thread: cheap probe; re-arm hot live if the session woke up."""
        self._invalidate_live_timeline_cache()
        self._invalidate_pending_cache()
        if self._session_is_pending() or self._session_needs_live_timeline():
            self._stop_live_recheck_timer()
            self._schedule_live_refresh()
            # Pull immediately so the first new rows are not delayed a full interval.
            self._live_refresh_from_fs(heartbeat=False)

    def _live_refresh_heartbeat(self) -> None:
        """UI thread: periodic read-only refresh (context meter / gate status)."""
        self._live_refresh_from_fs(heartbeat=True)

    def _live_refresh_snapshot(self) -> None:
        """UI thread: timer backup — re-probe live need and pull new rows."""
        self._invalidate_live_timeline_cache()
        self._live_refresh_from_fs(heartbeat=False)

    def _live_refresh_from_fs(self, *, heartbeat: bool = False) -> None:
        """UI thread: debounced FS event, snapshot timer, or heartbeat."""
        if not self._session_is_pending() and not self._session_needs_live_timeline():
            self._downgrade_live_refresh_to_recheck()
            self._refresh_session_pending_bar()
            return
        if not self._session_needs_live_timeline() and not heartbeat:
            self._refresh_session_pending_bar()
            return
        import time

        from ...constants import live_browser_timeline_min_interval
        from ...parser import updates_jsonl_size
        from ...session_inflight import KIND_REFRESH, request_rerun, try_begin

        # Coalesce FS storms: one light job per min gap (not a second parse
        # throttle inside the job — that skipped new rows until full reload).
        size_hint = len(getattr(self, "timeline", None) or []) * 4096
        if not self._uses_control_data():
            size_hint = updates_jsonl_size(self.session_dir)
        min_gap = live_browser_timeline_min_interval(size_hint)
        now = time.monotonic()
        last_submit = float(getattr(self, "_last_light_submit_at", 0.0) or 0.0)
        if not heartbeat and last_submit > 0 and (now - last_submit) < min_gap:
            self._arm_live_refresh_deferred(min_gap - (now - last_submit))
            return
        if not try_begin(KIND_REFRESH, self.session_dir):
            request_rerun(KIND_REFRESH, self.session_dir)
            self._live_refresh_pending = True
            if heartbeat:
                self._light_refresh_heartbeat = True
            return
        self._live_refresh_busy = True
        self._live_refresh_pending = False
        self._last_light_submit_at = now
        if heartbeat:
            self._light_refresh_heartbeat = True
        try:
            self._submit_load_data_light()
        except Exception:
            from ...session_inflight import end

            self._light_refresh_heartbeat = False
            end(KIND_REFRESH, self.session_dir)
            self._live_refresh_busy = False
            raise

    def _arm_live_refresh_deferred(self, delay_s: float) -> None:
        """One-shot catch-up after submit throttle (no pending-spin loop)."""
        self._live_refresh_pending = True
        if getattr(self, "_live_refresh_deferred", None) is not None:
            return
        wait = max(0.05, float(delay_s))
        set_timer = getattr(self, "set_timer", None)
        if not callable(set_timer):
            return

        def _fire() -> None:
            self._live_refresh_deferred = None
            if getattr(self, "_live_refresh_busy", False):
                self._live_refresh_pending = True
                return
            self._live_refresh_from_fs(heartbeat=False)

        self._live_refresh_deferred = set_timer(wait, _fire)

    def _live_refresh_worker_done(self) -> None:
        """Release refresh inflight; schedule one deferred catch-up if needed."""
        import time

        from ...constants import live_browser_timeline_min_interval
        from ...parser import updates_jsonl_size
        from ...session_inflight import KIND_REFRESH, end

        again = end(KIND_REFRESH, self.session_dir)
        pending = bool(getattr(self, "_live_refresh_pending", False) or again)
        self._live_refresh_busy = False
        self._live_refresh_pending = False
        pending_heartbeat = bool(getattr(self, "_light_refresh_heartbeat", False))
        self._light_refresh_heartbeat = False
        if not pending:
            return
        size_hint = len(getattr(self, "timeline", None) or []) * 4096
        if not self._uses_control_data():
            size_hint = updates_jsonl_size(self.session_dir)
        min_gap = live_browser_timeline_min_interval(size_hint)
        last_submit = float(getattr(self, "_last_light_submit_at", 0.0) or 0.0)
        elapsed = time.monotonic() - last_submit if last_submit > 0 else min_gap
        if not pending_heartbeat and elapsed < min_gap:
            self._arm_live_refresh_deferred(min_gap - elapsed)
            return
        self._live_refresh_from_fs(heartbeat=pending_heartbeat)

    def _submit_load_data_light(self) -> None:
        """Queue a read-only light reload on the serial live-refresh pool.

        Caller must hold the :data:`~groket.session_inflight.KIND_REFRESH` lock
        via :func:`~groket.session_inflight.try_begin`. Does not write traces.
        """
        from ...job_pools import get_live_refresh_pool

        get_live_refresh_pool().submit(
            f"refresh {self.session_dir.name}",
            self._load_data_light_job,
        )

    def _current_turn_index(self) -> int:
        try:
            from ...session.turns import segment_timeline_turns

            segs = segment_timeline_turns(self.timeline or [])
            if segs:
                return int(segs[-1].turn_index)
        except Exception:
            pass
        return 0

    def _default_note_turn_index(self) -> int:
        """Turn for a new note: selected event's turn, else turn filter, else last."""
        from ...session.turns import segment_timeline_turns, turn_index_for_event

        segs = getattr(self, "_turn_segments", None) or []
        if not segs:
            try:
                segs = segment_timeline_turns(self.timeline or [])
            except Exception:
                segs = []
        if self._current_event is not None and segs:
            found = turn_index_for_event(segs, self._current_event.index)
            if found is not None:
                return found
        tf = getattr(self, "_turn_filter", "all") or "all"
        if tf != "all":
            try:
                key = int(str(tf))
            except (TypeError, ValueError):
                key = None
            if key is not None:
                from ...session.turns import display_turn_number

                for seg in segs:
                    if int(seg.turn_index) == key:
                        if (n := display_turn_number(seg)) is not None:
                            return n
                        break
        if segs:
            from ...session.turns import display_turn_number

            for seg in reversed(segs):
                if (n := display_turn_number(seg)) is not None:
                    return n
        return self._current_turn_index()

    def _signals_mtime(self) -> float:
        fp = Path(self.session_dir) / "signals.json"
        try:
            return float(fp.stat().st_mtime) if fp.is_file() else 0.0
        except OSError:
            return 0.0

    def _uses_control_data(self) -> bool:
        """True when this browser must load timeline/meta via the control owner."""
        app = resolve_ui_app(self)
        is_client = getattr(app, "is_control_client", None)
        return bool(callable(is_client) and is_client())

    def _session_control_ref(self) -> str:
        """Session path for control RPCs (id lookup is a host-tree walk)."""
        try:
            return str(Path(self.session_dir).expanduser().resolve())
        except OSError:
            return str(self.session_dir)

    def _control_access(self) -> object:
        """Attached session access, or raise if the owner is missing."""
        app = resolve_ui_app(self)
        access = getattr(app, "session_access", lambda: None)()
        if access is None:
            raise RuntimeError("control session access unavailable")
        return access

    def _load_control_first_page(self) -> int:
        """Overview + first ``session/timeline`` page. Returns the owner total."""
        import asyncio

        from ...session.wire_timeline import (
            TIMELINE_RPC_LIMIT,
            fetch_timeline_page,
            session_meta_from_overview,
        )

        access = self._control_access()
        ref = self._session_control_ref()
        session_overview = getattr(access, "session_overview", None)
        if not callable(session_overview):
            raise RuntimeError("control session access unavailable")

        async def _ov() -> object:
            return await session_overview(ref)

        from ...session.control_views import overview_input_stamp

        overview_stamp = overview_input_stamp(self.session_dir)
        overview = asyncio.run(_ov())
        self._last_overview_stamp = overview_stamp
        ov = as_json_object(overview) if isinstance(overview, dict) else {}
        self._overview_payload = ov
        meta = session_meta_from_overview(ov, fallback_dir=Path(self.session_dir))
        first, total = asyncio.run(fetch_timeline_page(access, ref, page_limit=TIMELINE_RPC_LIMIT))
        self.meta = meta
        self.timeline = first
        if self.meta is not None:
            self.meta.num_events = int(total or len(first))
        return int(total or len(first))

    def _load_control_remainder(self, first_len: int, total: int) -> None:
        """Fetch remaining timeline pages and paint them as an append."""
        if total <= first_len:
            return
        import asyncio

        from ...session.wire_timeline import TIMELINE_RPC_LIMIT, fetch_timeline_events

        access = self._control_access()
        ref = self._session_control_ref()
        rest = asyncio.run(
            fetch_timeline_events(access, ref, offset=first_len, page_limit=TIMELINE_RPC_LIMIT)
        )
        if not rest:
            return
        self.timeline = list(self.timeline or []) + rest
        if self.meta is not None:
            self.meta.num_events = len(self.timeline)
        self._rebuild_indices()
        try:
            self._diff_doc = load_workspace_diff_doc(
                self.session_dir, timeline=self.timeline or None
            )
        except Exception:
            pass
        if self.is_mounted:
            call_ui(resolve_ui_app(self), self._apply_timeline_remainder)

    def _load_offline_session(self) -> None:
        """Parse the session from disk (``--no-socket``)."""
        from ...parser import session_timeline_stamp

        self._overview_payload = None
        self.meta = load_session_meta(self.session_dir, include_timeline_count=False)
        self.timeline = parse_timeline(self.session_dir)
        if self.meta is not None:
            self.meta.num_events = len(self.timeline or [])
        try:
            self._last_trace_mtime = session_timeline_stamp(self.session_dir)
        except Exception:
            self._last_trace_mtime = None
        self._last_signals_mtime = self._signals_mtime()

    def _commit_loaded_session(self) -> None:
        """Flags, notes, Diff, then first Timeline paint."""
        if self.meta is not None:
            self.meta.num_events = len(self.timeline or [])
        self._record_context_sample()
        self._load_flags()
        self._load_notes()
        self._rebuild_indices()
        try:
            self._diff_doc = load_workspace_diff_doc(
                self.session_dir, timeline=self.timeline or None
            )
        except Exception:
            self._diff_doc = WorkspaceDiff(())
        if not self.is_mounted:
            return
        app = resolve_ui_app(self)
        call_ui(app, self._populate_ui)
        call_ui(app, self._schedule_live_refresh)
        call_ui(app, self._schedule_analysis)

    def _on_control_browser_error(self, exc: BaseException, *, notify: bool) -> None:
        """Log a failed control hydrate; toast only on the full browser load."""
        if notify:
            logger.exception("control browser load failed")
        else:
            logger.warning("control browser refresh failed: %s", exc)
        if notify and getattr(self, "is_mounted", False):
            call_ui(
                resolve_ui_app(self),
                self.notify,
                control_operator_text(exc, fallback_id="notify-control-session-failed"),
                severity="error",
            )

    def _load_data_light_job(self) -> None:
        """Reload meta (+ timeline when changed). Control path when attached.

        Attached: re-fetch overview; full timeline only when event total moves.
        Offline (no control): disk stamp + parse_timeline as before.
        """
        import time

        try:
            if self._uses_control_data():
                prev_n = len(self.timeline or [])
                prev_status = self.meta.list_status_label() if self.meta is not None else ""
                prev_jobs = self._jobs_status_key()
                from ...session.control_views import overview_input_stamp

                overview_stamp = overview_input_stamp(self.session_dir)
                prev_stamp = getattr(self, "_last_overview_stamp", None)
                if (
                    prev_stamp is not None
                    and prev_stamp == overview_stamp
                    and self.meta is not None
                ):
                    return
                app = resolve_ui_app(self)
                access = getattr(app, "session_access", lambda: None)()
                if access is None:
                    return
                import asyncio

                ref = self._session_control_ref()

                async def _ov() -> object:
                    return await access.session_overview(ref)

                overview = asyncio.run(_ov())
                self._last_overview_stamp = overview_stamp
                from ...session.wire_timeline import session_meta_from_overview

                ov = as_json_object(overview) if isinstance(overview, dict) else {}
                self._overview_payload = ov
                meta = session_meta_from_overview(
                    ov,
                    fallback_dir=Path(self.session_dir),
                )
                self.meta = meta
                self._rebuild_session_jobs()
                self._rebuild_subagent_runs()
                new_n = int(meta.num_events or 0)
                new_status = meta.list_status_label()
                timeline_updated = False
                if new_n != prev_n or not self.timeline:
                    from ...session.wire_timeline import fetch_timeline_growth

                    self.timeline = asyncio.run(
                        fetch_timeline_growth(
                            access,
                            ref,
                            held=list(self.timeline or []),
                            new_total=new_n,
                        )
                    )
                    if self.meta is not None:
                        self.meta.num_events = len(self.timeline or [])
                    self._last_timeline_parse_at = time.monotonic()
                    self._rebuild_indices()
                    timeline_updated = True
                need_ui = (
                    timeline_updated
                    or new_status != prev_status
                    or prev_jobs != self._jobs_status_key()
                    or bool(getattr(self, "_light_refresh_heartbeat", False))
                )
                if not need_ui:
                    return
                call_ui(app, self._populate_ui_light)
                return

            from ...parser import session_timeline_stamp

            # Offline: Timeline stamp (not signals.json): heartbeats must not re-parse.
            stamp = session_timeline_stamp(self.session_dir)
            signals_mtime = self._signals_mtime()
            timeline_unchanged = (
                self._last_trace_mtime is not None
                and stamp == self._last_trace_mtime
                and bool(self.timeline)
            )
            timeline_updated = False
            if not timeline_unchanged:
                self.timeline = parse_timeline(self.session_dir)
                self._last_trace_mtime = stamp
                self._last_timeline_parse_at = time.monotonic()
                self._rebuild_indices()
                timeline_updated = True
            need_meta = (
                not timeline_unchanged
                or signals_mtime != getattr(self, "_last_signals_mtime", None)
                or bool(getattr(self, "_light_refresh_heartbeat", False))
                or self.meta is None
            )
            if need_meta:
                meta = load_session_meta(self.session_dir, include_timeline_count=False)
                self.meta = meta
            if self.meta is not None:
                self.meta.num_events = len(self.timeline or [])
            self._last_signals_mtime = signals_mtime
            if not timeline_updated and not need_meta:
                return
            app = resolve_ui_app(self)
            call_ui(app, self._populate_ui_light)
        except (TimeoutError, OSError, ConnectionError, ControlError) as exc:
            self._on_control_browser_error(exc, notify=False)
        finally:
            try:
                call_ui(resolve_ui_app(self), self._live_refresh_worker_done)
            except Exception:
                from ...session_inflight import KIND_REFRESH, end

                self._light_refresh_heartbeat = False
                end(KIND_REFRESH, self.session_dir)

    def _light_refresh_fingerprint(self) -> tuple[str | int | float | bool | None, ...]:
        """Identity for live poll — skip full timeline rebuild when unchanged.

        Uses length + tail identity only (not full last-content slices) so
        streaming does not force expensive equality work every tick. Content
        growth still changes ``len`` or last ``event_type``/``tool_call_id`` or
        the short content fingerprint below.
        """
        tl = self.timeline or []
        last = tl[-1] if tl else None
        meta = self.meta
        last_content = last.content or "" if last is not None else ""
        # Cheap content fingerprint: length + edges (not a full 80-char copy).
        return (
            len(tl),
            last.index if last is not None else None,
            last.timestamp if last is not None else None,
            last.event_type if last is not None else None,
            last.tool_call_id if last is not None else None,
            len(last_content),
            last_content[:24] if last is not None else "",
            last_content[-24:] if last is not None else "",
            meta.turn_outcome if meta else None,
            meta.turn_in_progress if meta else None,
            meta.duration_seconds if meta else None,
            meta.context_usage_compact if meta else None,
            meta.context_tokens_used if meta else None,
            self._jobs_status_key(),
        )

    def _reapply_timeline_view_filter(self) -> None:
        """Re-apply type / turn / search filters after a timeline reload."""
        self._apply_timeline_filters()

    def _apply_timeline_filters(self) -> None:
        """Apply View + Turn + search-as-you-type without moving focus."""
        mode = getattr(self, "_timeline_filter", "all") or "all"
        self._errors_only = mode == "errors"
        search = getattr(self, "_timeline_search", "") or ""
        if mode == "errors":
            self._apply_filter(kind="errors", errors_only=True, search_query=search)
        else:
            self._apply_filter(kind=mode, search_query=search)

    def _record_context_sample(self) -> bool:
        """Record read-only context snapshot against the current turn index."""
        store = getattr(self, "_context_samples", None)
        if store is None:
            return False
        return bool(store.record(self._current_turn_index(), self.meta))

    def _populate_ui_light(self) -> None:
        """Update title + timeline + share/stats without rebuilding analysis tabs.

        Skips clearing/rebuilding the timeline table when the light fingerprint
        is unchanged so live polling does not flicker mid-turn. Context-only
        changes still refresh Summary stats (read-only signals heartbeat).

        **Never** rebuilds Summary while the Timeline tab is active — that was a
        multi-hundred-ms freeze during live turns.
        """
        if not self.is_mounted:
            return
        sampled = self._record_context_sample()
        fp = self._light_refresh_fingerprint()
        prev_fp = getattr(self, "_last_light_fp", None)
        unchanged = fp == prev_fp
        # Title only when outcome bits may have changed (slots 8–9).
        if prev_fp is None or prev_fp[8:10] != fp[8:10]:
            self._set_title_from_meta()
        active = ""
        with suppress(Exception):
            active = str(self.query_one("#browser-tabs", TabbedContent).active or "")
        if not unchanged:
            self._last_light_fp = fp
            # Turn dropdown must track follow-ups even when Timeline is not focused.
            with suppress(Exception):
                self._rebuild_turn_select()
            # Skip DataTable work when Timeline is not visible (still keep data).
            if active in ("", "tab-timeline"):
                try:
                    timeline_table = self.query_one("#timeline-list", TimelineTable)
                    timeline_table.load_events(
                        self.timeline,
                        self._findings,
                        list(self._flags.values()),
                        follow_tail=self._timeline_follow_tail(),
                    )
                    # load_events paints the full list (and row_count mismatches
                    # after a prior filter force a full rebuild). Always restore
                    # View/Turn/search so the Select state matches visible rows.
                    if self._timeline_filters_active():
                        self._reapply_timeline_view_filter()
                except Exception:
                    pass
        # Summary is expensive — only while that tab is focused, never every tick.
        if active == "tab-summary" and (not unchanged or sampled):
            try:
                self._update_summary_tab()
            except Exception:
                pass
            if not getattr(self, "selections", None):
                try:
                    self._update_stats()
                except Exception:
                    pass
        # Pending bar only when turn_outcome / turn_in_progress flipped — not on
        # every streaming content update of the last event (that was the freeze).
        # Fingerprint slots 8–9 are turn_outcome, turn_in_progress.
        if prev_fp is None or prev_fp[8:10] != fp[8:10]:
            self._invalidate_pending_cache()
            self._refresh_session_pending_bar()

    @work(thread=True)
    def _load_data(self) -> None:
        from ...session_inflight import KIND_REFRESH, end, request_rerun, try_begin

        if not try_begin(KIND_REFRESH, self.session_dir):
            # Light refresh (or another full load) owns the session; coalesce.
            request_rerun(KIND_REFRESH, self.session_dir)
            return

        try:
            self._last_light_fp = None
            self._last_trace_mtime = None
            self._last_signals_mtime = None
            self._detail_expanded.clear()
            self._detail_expanding.clear()
            store = getattr(self, "_context_samples", None)
            if store is not None:
                store.clear()
            import time

            remainder = (0, 0)
            if self._uses_control_data():
                total = self._load_control_first_page()
                remainder = (len(self.timeline or []), total)
                self._last_timeline_parse_at = time.monotonic()
                self._last_trace_mtime = None
            else:
                self._load_offline_session()
                self._last_timeline_parse_at = time.monotonic()
            self._commit_loaded_session()
            if remainder[1] > remainder[0]:
                self._load_control_remainder(remainder[0], remainder[1])
        except (TimeoutError, OSError, ConnectionError, ControlError) as exc:
            self._on_control_browser_error(exc, notify=True)
        finally:

            def _release_refresh_lock() -> None:
                again = end(KIND_REFRESH, self.session_dir)
                self._live_refresh_busy = False
                self._live_refresh_pending = False
                pending_heartbeat = self._light_refresh_heartbeat
                self._light_refresh_heartbeat = False
                if again and self.is_mounted:
                    self._live_refresh_from_fs(heartbeat=pending_heartbeat)

            try:
                call_ui(resolve_ui_app(self), _release_refresh_lock)
            except Exception:
                self._light_refresh_heartbeat = False
                end(KIND_REFRESH, self.session_dir)

    def _should_auto_analyze(self) -> bool:
        """Whether policy says to run analyzers for this session now."""
        svc = self._analysis_svc()
        when = (svc.config.auto_analyze_when or "session_complete").strip().lower()
        if when == "never":
            return False
        is_live = bool(self.meta and self.meta.turn_in_progress)
        if is_live:
            return False
        large = len(self.timeline or []) > 2_500
        if large:
            # Explicit palette analyze still works; auto skips mega timelines.
            return False
        # session_complete: settled turn outcome (not running / awaiting).
        if self.meta and (self.meta.turn_outcome or "").strip():
            oc = (self.meta.turn_outcome or "").lower().replace(" ", "_")
            if oc in (
                "running",
                "in_progress",
                "pending",
                "awaiting_follow_up",
                "ending",
                "finishing",
            ):
                return False
            return True
        return True

    def _auto_needs_background_job(self) -> bool:
        """True when auto-open should enqueue *non-deferred* analyzers only.

        Deferred plugins are never started from open — they are
        cache-only until the operator force-analyzes.
        """
        try:
            svc = self._analysis_svc()
            plugins = [p for p in svc.list_plugins() if p.id != "noop"]
        except Exception:
            return False
        have: set[str] = set()
        for key, result in self.plugin_results.items():
            aid = getattr(result, "analyzer_id", None) or key
            have.add(str(aid))
            have.add(str(key))
        for info in plugins:
            if info.defer:
                continue
            if info.id not in have:
                return True
        return False

    def _schedule_analysis(self, *, force: bool = False) -> None:
        """Queue analysis on the serial analysis pool (UI thread).

        Non-force (open / auto): paint disk cache immediately (including stale
        deferred results), show the stale banner when versions diverge, and
        **do not** re-run multi-minute deferred plugins unless cache is missing.
        Force (palette Analyze): always re-run on the background pool.
        """
        from ...analysis.inflight import (
            analysis_session_key,
            end_session_analysis,
            session_analysis_inflight,
            try_begin_session_analysis,
        )

        if self._analysis_pending or session_analysis_inflight(self.session_dir):
            # Already queued/running for this session — keep spinner, no second job.
            self._analysis_pending = True
            self._show_analysis_pending()
            return

        if not force:
            # Instant paint from disk so opening a session never waits on deferred work.
            try:
                cached = self._analysis_svc().load_cached_all(self.session_dir, allow_stale=True)
            except Exception:
                cached = {}
            if cached:
                self.plugin_results = cached
                self._collect_findings()
                self._rebuild_indices()
                try:
                    self._populate_analysis_ui()
                except Exception:
                    pass
                self._apply_stale_analysis_hints()
            elif not self._should_auto_analyze():
                self._show_analysis_idle()
                self._apply_stale_analysis_hints(repaint=False)
                return

            if not self._should_auto_analyze():
                if not cached:
                    self._show_analysis_idle()
                    self._apply_stale_analysis_hints(repaint=False)
                return

            # Auto-open never queues deferred work. Cheap analyzers may
            # still run if cache is incomplete; deferred is cache-only until
            # the operator explicitly Analyze (force=True).
            if not self._auto_needs_background_job():
                return

        if not try_begin_session_analysis(self.session_dir):
            self._analysis_pending = True
            self._show_analysis_pending()
            return

        self._analysis_pending = True
        self._show_analysis_pending()
        # Drop stale hints while a fresh run is queued; show progress instead.
        self._note_stale_analysis([])
        from ...job_pools import get_activity_log, get_analysis_pool
        from ..threads import call_ui

        label = self.session_dir.name
        app = self.app
        session_dir = self.session_dir
        force_run = force
        result_key = analysis_session_key(session_dir)
        use_control = bool(getattr(app, "is_control_client", lambda: False)())

        # Bump activity-bar counter on the UI thread so spinner shows immediately.
        try:
            app._analysis_jobs_active = (  # type: ignore[attr-defined]
                int(getattr(app, "_analysis_jobs_active", 0) or 0) + 1
            )
        except Exception:
            pass
        try:
            host_results = getattr(app, "_plugin_results", None)
            if isinstance(host_results, dict):
                host_results.pop(result_key, None)
        except Exception:
            pass

        def _finish_with(results: dict) -> None:
            try:
                if self.is_mounted:
                    self.apply_analysis_results(results)
                try:
                    host_results = getattr(app, "_plugin_results", None)
                    if isinstance(host_results, dict):
                        host_results[result_key] = results
                except Exception:
                    pass
            finally:
                end_session_analysis(session_dir)
                try:
                    n = int(getattr(app, "_analysis_jobs_active", 0) or 0)
                    app._analysis_jobs_active = max(0, n - 1)  # type: ignore[attr-defined]
                except Exception:
                    pass

        def _job() -> None:
            results: dict = {}
            try:
                if use_control:
                    results = self._analyze_via_control(session_dir, force=force_run)
                else:
                    svc = self._analysis_svc()
                    results = svc.analyze_all(session_dir, force=force_run)
            except Exception as exc:
                get_activity_log().log("analysis", f"failed {label}: {exc}")
                results = {}
            try:
                call_ui(app, lambda: _finish_with(results))
            except Exception:
                end_session_analysis(session_dir)
                try:
                    n = int(getattr(app, "_analysis_jobs_active", 0) or 0)
                    app._analysis_jobs_active = max(0, n - 1)  # type: ignore[attr-defined]
                except Exception:
                    pass

        get_analysis_pool().submit(f"session {label}", _job)

    def _analyze_via_control(self, session_dir: Path, *, force: bool) -> dict:
        """Run analysis on the control owner; load results from the shared cache."""
        import asyncio
        import time as time_mod

        app = self.app
        access = getattr(app, "session_access", lambda: None)()
        if access is None:
            return self._analysis_svc().analyze_all(session_dir, force=force)

        async def _run() -> dict:
            # One long-lived client for run + status polls (not a connect per tick).
            client = getattr(access, "_client", None)
            if client is not None and hasattr(client, "connect"):
                await client.connect()
            try:
                await access.analysis_run(session_dir.name, force=force)
                deadline = time_mod.monotonic() + 600.0
                delay = 0.4
                while time_mod.monotonic() < deadline:
                    status = await access.analysis_status(session_dir.name)
                    state = str(status.get("state") or "")
                    if state in {"done", "error", "idle"}:
                        if state == "error":
                            logger.warning(
                                "control analysis error for %s: %s",
                                session_dir.name,
                                status.get("error"),
                            )
                        break
                    await asyncio.sleep(delay)
                    delay = min(1.0, delay * 1.25)
            finally:
                if client is not None and hasattr(client, "close"):
                    await client.close()
            return self._analysis_svc().load_cached_all(session_dir, allow_stale=True)

        return asyncio.run(_run())

    def _apply_stale_analysis_hints(self, *, repaint: bool = True) -> None:
        """Load stale hints, toast once, optionally repaint findings/report."""
        try:
            hints = self._analysis_svc().stale_analyzer_hints(self.session_dir)
        except Exception:
            hints = []
        self._note_stale_analysis(hints)
        if repaint and self.is_mounted and not getattr(self, "_analysis_pending", False):
            try:
                self._populate_analysis_ui()
            except Exception:
                pass

    def _stale_detail(self, hints: list[str]) -> str:
        detail = "; ".join(hints[:6])
        if len(hints) > 6:
            detail += "…"
        return detail

    def _note_stale_analysis(self, hints: list[str]) -> None:
        """Remember stale hints and toast the first time they appear."""
        prev = list(self._analysis_stale_hints)
        self._analysis_stale_hints = list(hints)
        if not hints or hints == prev or not self.is_mounted:
            return
        self.notify(
            t("analysis-stale-toast", detail=self._stale_detail(hints)),
            severity="warning",
            timeout=6,
        )

    def _show_analysis_idle(self) -> None:
        """Findings/report idle when auto-analyze is off or deferred."""
        try:
            findings_table = self.query_one("#findings-table", DataTable)
            findings_table.clear(columns=True)
            findings_table.add_columns("", "")
            findings_table.add_row("", t("ui-analysis-idle"))
        except Exception:
            pass
        try:
            self.query_one("#report-overview-content", Static).update(t("ui-analysis-idle-report"))
        except Exception:
            pass

    def _run_analysis(self) -> None:
        """Force analysis for this session (palette / full refresh)."""
        self.action_analyze()

    def action_analyze(self) -> None:
        """Command palette: re-run analysis for **this session only** (force)."""
        # Drop in-memory results so force actually re-runs plugins.
        self.plugin_results = {}
        self._findings = []
        self._schedule_analysis(force=True)
        self.notify(t("notify-analyzing-this-session"), severity="information", timeout=4)

    def _load_flags(self) -> None:
        """Load user flags from disk into a dict keyed by event_index."""
        self._flags = {fl.event_index: fl for fl in load_flags(self.session_dir)}

    def _load_notes(self) -> None:
        """Load turn-linked operator notes for this session."""
        # Disk is canonical; control notes/* also reads the same files.
        # Keep direct load so open paints without an extra RPC round-trip.
        self._notes_doc = load_notes(self.session_dir)
        self._notes_loaded = True

    def _enabled_analyzer_ids(self) -> set[str] | None:
        """Ids enabled in the process analysis service (None if unavailable)."""
        try:
            return set(self._analysis_svc().enabled_ids)
        except Exception:
            return None

    def _active_plugin_results(self) -> dict[str, AnalysisResult]:
        """Results for analyzers enabled under the current config only."""
        enabled = self._enabled_analyzer_ids()
        if not enabled:
            return dict(self.plugin_results)
        out: dict[str, AnalysisResult] = {}
        for key, result in self.plugin_results.items():
            aid = key
            if result is not None and getattr(result, "analyzer_id", None):
                aid = result.analyzer_id
            if aid in enabled or key in enabled:
                out[key] = result
        return out

    def _collect_findings(self) -> None:
        """Collect findings from **enabled** plugin results only.

        Order is turn → earliest evidence → severity (Findings + Report lists).
        """
        all_findings: list[Finding] = []
        for result in self._active_plugin_results().values():
            if result is not None and result.ok:
                all_findings.extend(result.findings)
        self._findings = sort_findings_by_turn(all_findings, self.timeline)

    def _rebuild_indices(self) -> None:
        self._findings_by_call = {}
        for finding in self._findings:
            for cid in finding.all_tool_call_ids:
                if cid not in self._findings_by_call:
                    self._findings_by_call[cid] = finding
        self._rebuild_subagent_runs()
        self._rebuild_session_jobs()

    def _jobs_status_key(self) -> str:
        """Stable job/schedule identity for light-refresh paint and fingerprint."""
        jobs = ",".join(f"{j.job_id}:{j.status}" for j in self._session_jobs.jobs)
        scheds = ",".join(
            f"{s.task_id}:{s.next_fire_at}:{s.last_fired_at}" for s in self._session_jobs.schedules
        )
        wfs = ",".join(f"{w.run_id}:{w.status}:{w.phase}" for w in self._session_jobs.workflows)
        return f"{jobs}|{scheds}|{wfs}"

    def _rebuild_session_jobs(self) -> None:
        """Refresh jobs from the owner overview when attached, else disk."""
        if not self.session_dir:
            self._session_jobs = SessionJobs(jobs=[], schedules=[])
            return
        self._session_jobs = session_jobs_for_view(
            self._overview_payload, self.session_dir, self.timeline
        )

    def _rebuild_subagent_runs(self) -> None:
        """Refresh runs from the owner overview when attached, else disk."""
        if self._overview_payload is not None:
            self._subagent_runs = subagent_runs_for_view(
                self._overview_payload, self.session_dir, self.timeline or [], [], {}
            )
            return
        if not self.timeline:
            self._subagent_runs = []
            return
        segs = segment_timeline_turns(self.timeline)
        turn_by_index = event_display_turn_map(segs)
        self._subagent_runs = subagent_runs_for_view(
            None, self.session_dir, self.timeline, segs, turn_by_index
        )

    def _status_label(self, status: str) -> str:
        key = {
            "running": "ui-status-running",
            "completed": "status-complete",
            "complete": "status-complete",
            "done": "status-complete",
            "cancelled": "status-cancelled",
            "failed": "ui-status-failed",
            "interrupted": "ui-status-interrupted",
        }.get(status, "")
        return t(key) if key else (status or "—")

    def _update_subagents_table(self) -> None:
        try:
            table = self.query_one("#stats-subagents-table", DataTable)
        except Exception:
            return
        style_data_table(table)
        table.clear(columns=True)
        table.add_columns(
            t("col-index"),
            t("col-type"),
            t("ui-label"),
            t("col-status"),
            t("ui-dur"),
            t("ui-tools"),
        )
        if not self._subagent_runs:
            table.add_row("—", "—", t("ui-subagent-none"), "—", "—", "—")
            return
        for i, run in enumerate(self._subagent_runs):
            dur = self._fmt_dur(run.duration_ms / 1000.0) if run.duration_ms is not None else "—"
            tools = str(run.tool_calls) if run.tool_calls is not None else "—"
            turn = str(run.parent_turn_index) if run.parent_turn_index is not None else "—"
            table.add_row(
                turn,
                run.subagent_type or "—",
                run.description or run.child_session_id or run.subagent_id or "—",
                self._status_label(run.status),
                dur,
                tools,
                key=f"sub-{i}",
            )

    def _update_jobs_table(self) -> None:
        try:
            table = self.query_one("#stats-jobs-table", DataTable)
        except Exception:
            return
        style_data_table(table)
        table.clear(columns=True)
        table.add_columns(
            t("col-kind"),
            t("col-status"),
            t("ui-label"),
            t("col-started"),
            t("ui-dur"),
            t("col-log"),
        )
        jobs = self._session_jobs.jobs
        schedules = self._session_jobs.schedules
        if not jobs and not schedules:
            table.add_row("—", "—", t("ui-background-none"), "—", "—", "—")
            return
        for i, job in enumerate(jobs):
            dur = "—"
            if job.started_at is not None and job.ended_at is not None:
                dur = self._fmt_dur(float(job.ended_at - job.started_at))
            log_name = Path(job.output_path).name if job.output_path else "—"
            table.add_row(
                job.kind or "—",
                self._status_label(job.status),
                job.description or job.command or job.job_id,
                str(job.started_at) if job.started_at is not None else "—",
                dur,
                log_name,
                key=f"job-{i}",
            )
        for i, sch in enumerate(schedules):
            table.add_row(
                t("ui-schedule"),
                t("ui-scheduled"),
                sch.prompt_preview or sch.human_schedule or sch.task_id,
                sch.next_fire_at or "—",
                sch.human_schedule or "—",
                "—",
                key=f"sched-{i}",
            )

    def _update_workflows_table(self) -> None:
        try:
            table = self.query_one("#stats-workflows-table", DataTable)
        except Exception:
            return
        style_data_table(table)
        table.clear(columns=True)
        table.add_columns(
            t("ui-label"),
            t("col-status"),
            t("ui-phase"),
            t("ui-agents"),
            t("ui-dur"),
        )
        runs = self._session_jobs.workflows
        if not runs:
            table.add_row(t("ui-workflows-none"), "—", "—", "—", "—")
            return
        for i, run in enumerate(runs):
            used = "—" if run.agents_used is None else str(run.agents_used)
            budget = "—" if run.agent_budget is None else str(run.agent_budget)
            dur = (
                self._fmt_dur(run.elapsed_ms / 1000.0)
                if run.elapsed_ms is not None and run.elapsed_ms > 0
                else "—"
            )
            table.add_row(
                run.name or run.run_id,
                self._status_label(run.status),
                run.phase or "—",
                f"{used}/{budget}",
                dur,
                key=f"wf-{i}",
            )

    def _focused_subagent_run(self) -> SubagentRun | None:
        focused = getattr(self, "focused", None)
        if isinstance(focused, DataTable) and focused.id == "stats-subagents-table":
            raw = cursor_row_key(focused) or ""
            if raw.startswith("sub-"):
                try:
                    idx = int(raw.split("-", 1)[1])
                except ValueError:
                    idx = -1
                if 0 <= idx < len(self._subagent_runs):
                    return self._subagent_runs[idx]
        ev = self._current_event
        if ev is None:
            return None
        return self._run_for_bookend_event(ev)

    def _run_for_bookend_event(self, ev: TraceEvent) -> SubagentRun | None:
        if ev.event_type not in ("subagent_spawned", "subagent_finished"):
            return None
        child = ""
        if isinstance(ev.raw_input, ToolInputBag):
            child = ev.raw_input.as_str("childSessionId") or ev.raw_input.as_str("child_session_id")
        if not child:
            return None
        for run in self._subagent_runs:
            if run.child_session_id == child:
                return run
        return None

    def _open_subagent_run(self, run: SubagentRun | None) -> None:
        if run is None:
            return
        if not run.openable or run.child_path is None:
            self.notify(t("ui-subagent-missing"))
            return
        opener = getattr(self.app, "open_session_path", None)
        if not callable(opener):
            return
        opener(run.child_path)
        self.notify(t("ui-subagent-opened"))

    def _open_workflow_child(self, agent_id: str) -> None:
        """Open a workflow child session (same return path as a subagent bookend)."""
        cid = (agent_id or "").strip()
        if not cid:
            self.notify(t("ui-subagent-missing"))
            return
        path = resolve_child_session_path(self.session_dir, cid)
        if path is None:
            self.notify(t("ui-subagent-missing"))
            return
        opener = getattr(self.app, "open_session_path", None)
        if not callable(opener):
            return
        opener(path)
        self.notify(t("ui-subagent-opened"))

    @on(DetailView.ChildActivated)
    def _on_workflow_child_activated(self, event: DetailView.ChildActivated) -> None:
        self._open_workflow_child(event.child.agent_id)

    def _focused_subagent_path(self) -> Path | None:
        run = self._focused_subagent_run()
        if run is None or not run.openable or run.child_path is None:
            return None
        return run.child_path

    def action_open_subagent(self) -> None:
        """Open the highlighted subagent run as a normal session."""
        self._open_subagent_run(self._focused_subagent_run())

    @on(DataTable.RowSelected, "#stats-subagents-table")
    def _on_subagent_row_selected(self, event: DataTable.RowSelected) -> None:
        raw = str(event.row_key.value) if event.row_key is not None else ""
        if not raw.startswith("sub-"):
            return
        try:
            idx = int(raw.split("-", 1)[1])
        except ValueError:
            return
        if not (0 <= idx < len(self._subagent_runs)):
            return
        run = self._subagent_runs[idx]
        if not run.openable or run.child_path is None:
            self.notify(t("ui-subagent-missing"))
            return
        opener = getattr(self.app, "open_session_path", None)
        if callable(opener):
            opener(run.child_path)

    def _focus_task_event(self, task_id: str, tool_call_id: str = "") -> None:
        """Select the matching task / schedule bookend on Timeline."""
        wanted = (task_id or "").strip()
        call = (tool_call_id or "").strip()
        hit: TraceEvent | None = None
        for ev in self.timeline:
            if ev.event_type not in et.TASK_TYPES and not ev.event_type.startswith(
                "scheduled_task_"
            ):
                continue
            bag = ev.raw_input if isinstance(ev.raw_input, ToolInputBag) else None
            ev_id = bag.as_str("task_id") if bag is not None else ""
            ev_call = ev.tool_call_id or (bag.as_str("tool_call_id") if bag is not None else "")
            if wanted and ev_id == wanted:
                hit = ev
                if ev.event_type in {"task_backgrounded", "scheduled_task_created"}:
                    break
            elif call and ev_call == call:
                hit = ev
        if hit is None:
            return
        self._jump_timeline_to_event(hit.index)

    @on(DataTable.RowSelected, "#stats-jobs-table")
    def _on_jobs_row_selected(self, event: DataTable.RowSelected) -> None:
        raw = str(event.row_key.value) if event.row_key is not None else ""
        if raw.startswith("job-"):
            try:
                idx = int(raw.split("-", 1)[1])
            except ValueError:
                return
            if 0 <= idx < len(self._session_jobs.jobs):
                job = self._session_jobs.jobs[idx]
                self._focus_task_event(job.job_id, job.tool_call_id)
            return
        if raw.startswith("sched-"):
            try:
                idx = int(raw.split("-", 1)[1])
            except ValueError:
                return
            if 0 <= idx < len(self._session_jobs.schedules):
                self._focus_task_event(self._session_jobs.schedules[idx].task_id)

    @on(DataTable.RowSelected, "#stats-workflows-table")
    def _on_workflows_row_selected(self, event: DataTable.RowSelected) -> None:
        raw = str(event.row_key.value) if event.row_key is not None else ""
        if not raw.startswith("wf-"):
            return
        try:
            idx = int(raw.split("-", 1)[1])
        except ValueError:
            return
        if 0 <= idx < len(self._session_jobs.workflows):
            self._focus_workflow_run(self._session_jobs.workflows[idx])

    def _timeline_event_for_workflow(self, run: WorkflowRun) -> TraceEvent | None:
        """Bookend for *run*: ``run_id`` on the bag or body, else name / stem."""
        idx = workflow_event_index(run, self.timeline or [])
        if idx is None:
            return None
        return next((ev for ev in (self.timeline or []) if int(ev.index) == idx), None)

    def _focus_workflow_run(self, run: WorkflowRun) -> None:
        """Inspect this merged run: jump to its bookend, or paint the row."""
        hit = self._timeline_event_for_workflow(run)
        if hit is not None:
            self._jump_timeline_to_event(hit.index)
            return
        self._ensure_timeline_tab()
        try:
            self.query_one("#detail-panel", DetailView).show_workflow(run)
        except Exception:
            return

    @on(DataTable.RowSelected, "#stats-turns-table")
    def _on_turn_row_selected(self, event: DataTable.RowSelected) -> None:
        raw = str(event.row_key.value) if event.row_key is not None else ""
        if not raw.startswith("turn-"):
            return
        try:
            turn_i = int(raw.split("-")[1])
        except (IndexError, ValueError):
            return
        first = self._summary_turn_first.get(turn_i)
        if first is None:
            return
        self._jump_timeline_to_event(first, turn_index=turn_i)

    def _reveal_timeline_event(self, event_index: int) -> None:
        """Drop Turn / View filters that would hide *event_index*."""
        idxs = self._turn_event_indices()
        if idxs is not None and int(event_index) not in idxs:
            self._turn_filter = "all"
            with suppress(Exception):
                sel = self.query_one("#timeline-turn-select", Select)
                if sel.display:
                    sel.value = "all"
        ev = next((e for e in (self.timeline or []) if int(e.index) == int(event_index)), None)
        if ev is None:
            return
        mode = getattr(self, "_timeline_filter", "all") or "all"
        if mode in ("all", "") or self._event_matches_timeline_view(ev, mode):
            return
        self._timeline_filter = "all"
        self._sync_timeline_view_select("all")

    def _event_matches_timeline_view(self, ev: TraceEvent, mode: str) -> bool:
        """True when *ev* stays visible under Timeline View *mode*."""
        return event_matches_timeline_kind(ev, mode)

    def _jump_timeline_to_event(self, event_index: int, *, turn_index: int | None = None) -> None:
        """Open Timeline and place the cursor on *event_index*."""
        if turn_index is not None:
            self._turn_filter = str(turn_index)
            with suppress(Exception):
                sel = self.query_one("#timeline-turn-select", Select)
                if sel.display:
                    sel.value = str(turn_index)
        else:
            self._reveal_timeline_event(event_index)
        self._ensure_timeline_tab(focus_list=False)
        self._apply_timeline_filters()

        def _place() -> None:
            try:
                tl = self.query_one("#timeline-list", TimelineTable)
                restore_cursor(tl, str(event_index), scroll=True)
                focus_primary_list(tl)
                ev = next((e for e in tl.events if int(e.index) == int(event_index)), None)
                if ev is not None:
                    self._current_event = ev
                    self._paint_selected_event_detail()
            except Exception:
                logger.debug("jump to turn start", exc_info=True)

        self.call_after_refresh(lambda: self.call_after_refresh(_place))

    _SUMMARY_STACK_WIDTH = 88

    def on_resize(self) -> None:
        self._sync_summary_stack()

    def _sync_summary_stack(self) -> None:
        try:
            scroll = self.query_one("#summary-scroll")
        except Exception:
            return
        scroll.set_class(self.size.width < self._SUMMARY_STACK_WIDTH, "summary-stack")

    def _set_title_from_meta(self) -> None:
        label = self.meta.label if self.meta else self.session_dir.name
        model = self.meta.model_display if self.meta else "unknown"
        # Full Fluent extras (not edge-space fragments). LIVE only while the agent
        # is writing traces — not for idle awaiting_follow_up or settled outcomes.
        outcome_bit = ""
        if is_subagent_session_dir(self.session_dir):
            outcome_bit = t("title-browser-extra-subagent")
        if self.meta and self.meta.turn_outcome:
            oc = (self.meta.turn_outcome or "").strip()
            oc_key = oc.lower().replace(" ", "_")
            if oc_key == "awaiting_follow_up":
                outcome_bit = join_ui(outcome_bit, t("title-browser-extra-awaiting"))
            elif oc_key in ("ending", "finishing"):
                outcome_bit = join_ui(outcome_bit, t("title-browser-extra-ending"))
            elif oc_key in ("running", "in_progress", "pending"):
                outcome_bit = join_ui(outcome_bit, t("title-browser-extra-live-turn", outcome=oc))
            else:
                outcome_bit = join_ui(outcome_bit, t("title-browser-extra-turn", outcome=oc))
        self.title = t(
            "title-browser-session",
            label=label,
            model=model,
            extra=outcome_bit or "",
        )
        self._sync_chrome_title()

    def _chrome_location_label(self) -> str:
        """Short name for the header wordmark (title, else kind, else id)."""
        title = (self.meta.title if self.meta else "").strip()
        if title:
            return _clip_chrome_label(title)
        if is_subagent_session_dir(self.session_dir):
            kind = read_session_kind(self.session_dir).strip()
            if kind and kind.casefold() not in {"subagent", "subagent_child"}:
                return _clip_chrome_label(kind.replace("_", " "))
        if self.meta:
            return _clip_chrome_label(self.meta.label)
        return _clip_chrome_label(self.session_dir.name)

    def _chrome_parent_label(self) -> str:
        parent = parent_session_dir(self.session_dir)
        if parent is None:
            return ""
        pmeta = load_session_meta(parent, include_timeline_count=False)
        return _clip_chrome_label((pmeta.title or "").strip())

    def _sync_chrome_title(self) -> None:
        """Put session location on AppChrome; leave LIVE/awaiting on screen.title."""
        from ..brand_mark import AppChrome

        brand = t("help-brand-name")
        label = self._chrome_location_label()
        if is_subagent_session_dir(self.session_dir):
            parent = self._chrome_parent_label()
            if parent:
                text = t(
                    "title-chrome-subagent-under",
                    brand=brand,
                    parent=parent,
                    kind=t("ui-subagent"),
                    label=label,
                )
            else:
                text = t(
                    "title-chrome-subagent",
                    brand=brand,
                    kind=t("ui-subagent"),
                    label=label,
                )
        else:
            text = t("title-chrome-session", brand=brand, label=label)
        try:
            self.query_one(AppChrome).set_wordmark(text)
        except Exception:
            return

    def _populate_ui(self) -> None:
        """Phase 1 UI: title and Timeline. Summary / Report wait until visited."""
        if not self.is_mounted:
            return
        self._set_title_from_meta()
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        timeline_table.load_events(
            self.timeline,
            self._findings,
            list(self._flags.values()),
            follow_tail=self._timeline_follow_tail(),
        )
        # load_events paints the current list; restore View/Turn/search.
        self._reapply_timeline_view_filter()
        self._rebuild_turn_select()
        self._sync_compact_child_chrome()
        self._sync_timeline_tail_checkbox()
        if self._requested_prompt_index is not None:
            self.select_prompt_index(self._requested_prompt_index)
        self._update_diff_tab()
        self._show_analysis_pending()
        self._paint_visible_secondary_panes()
        timeline_table.focus()
        if self.meta and self.meta.turn_failed:
            self.notify(
                t("notify-turn-ended-outcome", outcome=str(self.meta.turn_outcome)),
                severity="warning",
                timeout=8,
            )

    def _active_browser_tab(self) -> str:
        """Id of the showing browser pane, or empty."""
        with suppress(Exception):
            return str(self.query_one("#browser-tabs", TabbedContent).active or "")
        return ""

    def _timeline_follow_tail(self) -> bool:
        """True when the live Tail switch is showing and on."""
        with suppress(Exception):
            slot = self.query_one("#timeline-tail-slot", Vertical)
            box = self.query_one("#timeline-tail", Switch)
            return bool(slot.display and box.value)
        return False

    def _sync_timeline_tail_checkbox(self) -> None:
        """Show Tail only while a turn is still open."""
        live = False
        with suppress(Exception):
            live = bool(self._session_is_pending() or self._session_needs_live_timeline())
        try:
            label = self.query_one("#timeline-tail-label", Static)
            slot = self.query_one("#timeline-tail-slot", Vertical)
            box = self.query_one("#timeline-tail", Switch)
        except Exception:
            return
        label.display = live
        slot.display = live
        if not live:
            box.value = False

    def _paint_visible_secondary_panes(self) -> None:
        """Fill Summary or Report only when that pane is already showing."""
        active = self._active_browser_tab()
        if active == "tab-summary":
            self._update_summary_tab()
            self._update_stats()
        elif active == "tab-reports":
            self._update_reports_tab()

    def _maybe_refresh_reports(self) -> None:
        """Rebuild Report when the operator is looking at it."""
        if self._active_browser_tab() == "tab-reports":
            self._update_reports_tab()

    def _apply_timeline_remainder(self) -> None:
        """Append later control pages and restore View/Turn/search."""
        if not self.is_mounted:
            return
        try:
            timeline_table = self.query_one("#timeline-list", TimelineTable)
            timeline_table.load_events(
                self.timeline,
                self._findings,
                list(self._flags.values()),
                follow_tail=self._timeline_follow_tail(),
            )
            if self._timeline_filters_active():
                self._reapply_timeline_view_filter()
        except Exception:
            pass
        self._rebuild_turn_select()
        self._update_diff_tab()
        self._paint_visible_secondary_panes()

    def _show_analysis_pending(self) -> None:
        """Show toolkit loading readouts while analysis is in flight."""
        if not self._analysis_pending:
            return
        self._paint_analysis_pending_spinner(full=True)

    def _paint_analysis_pending_spinner(self, *, full: bool = False) -> None:
        """Show the chrome LoadingIndicator and pane loading overlays."""
        self._set_analysis_loading(True)
        if not full:
            return
        try:
            findings_table = self.query_one("#findings-table", DataTable)
            findings_table.clear(columns=True)
            style_data_table(findings_table)
            findings_table.add_columns(
                U.col_severity(), U.col_plugin(), U.col_category(), U.col_title(), U.col_events()
            )
        except Exception:
            pass

    def _set_analysis_loading(self, on: bool) -> None:
        """Toggle the visible loading control (chrome + Findings + Report)."""
        with suppress(Exception):
            self.query_one("#browser-analysis-loading", LoadingIndicator).display = on
        with suppress(Exception):
            self.query_one("#findings-table").loading = on
        if on:
            self._apply_report_loading_overlays()
            return
        with suppress(Exception):
            self.query_one("#reports-scroll").loading = False
        for aid in list(getattr(self, "_report_section_keys", ()) or ()):
            if aid in ("flags", "notes"):
                continue
            with suppress(Exception):
                self.query_one(f"#{self._report_section_dom_id(aid)}", Vertical).loading = False

    def _apply_report_loading_overlays(self) -> None:
        """Mark Report scroll and plugin cards loading while analysis is in flight."""
        if not self._analysis_pending:
            return
        with suppress(Exception):
            self.query_one("#reports-scroll").loading = True
        self._paint_report_plugin_pending_spinners()

    def _paint_report_plugin_pending_spinners(self, *, full: bool = False) -> None:
        """One loading overlay per plugin report card."""
        _ = full
        for aid in list(getattr(self, "_report_section_keys", ()) or ()):
            if aid in ("flags", "notes"):
                continue
            with suppress(Exception):
                section = self.query_one(f"#{self._report_section_dom_id(aid)}", Vertical)
                section.loading = True

    def _tick_analysis_pending(self) -> None:
        if not self._analysis_pending or not self.is_mounted:
            self._stop_analysis_spinner_timer()
            return
        self._paint_analysis_pending_spinner(full=False)

    def _stop_analysis_spinner_timer(self) -> None:
        timer = self._analysis_spinner_timer
        self._analysis_spinner_timer = None
        if timer is not None:
            timer.stop()
        self._set_analysis_loading(False)

    def apply_analysis_results(self, results: dict[str, AnalysisResult]) -> None:
        """Apply finished plugin results on the UI thread.

        Used by the browser analysis job, control ``analysis/changed``, and
        home-batch completion while this session is open. Always clears the
        in-flight spinner so Report is not left covered by loading overlays
        after results land.
        """
        if not self.is_mounted:
            return
        self.plugin_results = dict(results or {})
        self._analysis_pending = False
        self._stop_analysis_spinner_timer()
        self._collect_findings()
        self._rebuild_indices()
        self._apply_stale_analysis_hints(repaint=False)
        try:
            self._populate_analysis_ui()
        except Exception:
            logger.exception("analysis results UI update failed")

    def _populate_analysis_ui(self) -> None:
        """Phase 2 UI: findings + reports — after analysis plugins finish."""
        if not self._analysis_pending:
            # Results path: never leave Report under a stale loading overlay.
            self._set_analysis_loading(False)
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        timeline_table.load_events(
            self.timeline,
            self._findings,
            list(self._flags.values()),
            follow_tail=self._timeline_follow_tail(),
        )
        if self._timeline_filters_active():
            self._reapply_timeline_view_filter()
        self._rebuild_turn_select()
        findings_table = self.query_one("#findings-table", DataTable)
        findings_table.clear(columns=True)
        style_data_table(findings_table)
        findings_table.add_columns(
            U.col_severity(), U.col_plugin(), U.col_category(), U.col_title(), U.col_events()
        )
        self._findings_table_entries = []
        try:
            self._update_findings_header()
        except Exception:
            pass
        # First row: re-analyze needed (stale plugin cache) — not a Finding entry.
        stale_hints = getattr(self, "_analysis_stale_hints", None) or []
        if stale_hints:
            findings_table.add_row(
                "!",
                "stale",
                "",
                Text(
                    t("analysis-stale-findings-row", detail=self._stale_detail(stale_hints)),
                    style="yellow",
                ),
                "",
                key="__analysis_stale__",
            )
        for row_idx, finding in enumerate(self._findings):
            sev_display = SEVERITY_LABEL.get(finding.severity.value, finding.severity.value)
            n_events = len(
                {
                    *finding.all_tool_call_ids,
                    *(f"u{i}" for i in finding.all_update_indices),
                    *(f"e{i}" for i in finding.all_event_indices),
                }
            )
            title = finding.title[:60]
            if finding.children:
                title = f"{title} (+)"
            findings_table.add_row(
                sev_display,
                finding.plugin_id,
                finding.category,
                title,
                str(n_events),
                key=str(row_idx),
            )
            self._findings_table_entries.append(finding)
        self._maybe_refresh_reports()

    @staticmethod
    def _fmt_dur(seconds: float) -> str:
        return fmt_duration(seconds)

    def _sync_report_empty_states(self) -> None:
        """Sync Report empty-states from session data."""
        try:
            analysis_empty = self.query_one("#report-analysis-empty", EmptyState)
            if not self._report_plugin_ids() and (not self._active_plugin_results()):
                analysis_empty.set_message(U.tip_no_analysis())
            else:
                analysis_empty.clear_message()
        except Exception:
            pass
        try:
            flags_empty = self.query_one("#report-flags-empty", EmptyState)
            if self._flags:
                flags_empty.clear_message()
            else:
                flags_empty.set_message(U.tip_no_flags())
        except Exception:
            pass
        try:
            notes_empty = self.query_one("#report-notes-empty", EmptyState)
            if self._notes_doc.notes:
                notes_empty.clear_message()
            else:
                notes_empty.set_message(U.tip_no_notes())
        except Exception:
            pass

    def _update_findings_header(self) -> None:
        """Findings tab counts only (keyboard focus is footer / ``?``)."""
        fh = Text()
        fh.append(U.findings_heading() + "\n", style="bold")
        n = len(self._findings)
        high = sum(1 for f in self._findings if f.severity.value == "high")
        fh.append("\n  ")
        if high:
            fh.append_text(status_chip(t("browser-high-chip", n=high), kind="bad"))
        elif n:
            fh.append_text(status_chip(t("browser-findings-chip", n=n), kind="unknown"))
        else:
            fh.append_text(status_chip(t("browser-status-none"), kind="ok"))
        fh.append(t("browser-findings-dim", n=n), style="dim")
        header = self.query_one("#findings-header", Static)
        if not self._widget_has_text_selection(header):
            header.update(fh)

    def _widget_has_text_selection(self, widget: object) -> bool:
        """True when the operator has a mouse/text selection on *widget*.

        Live refresh must not ``update()`` that widget or the selection vanishes
        before they can copy.
        """
        sels = getattr(self, "selections", None)
        return bool(sels and widget in sels)

    def _update_summary_tab(self) -> None:
        if not self.meta:
            return
        renderable = render_session_summary(self.meta, self.timeline)
        try:
            widget = self.query_one("#summary-content", Static)
            if self._widget_has_text_selection(widget):
                return
            widget.update(renderable)
        except Exception:
            pass
        try:
            self._sync_report_empty_states()
        except Exception:
            pass

    def _update_diff_tab(self) -> None:
        """Push the loaded rewind / edit doc into the Diff pane."""
        with suppress(Exception):
            self.query_one("#diff-view", DiffView).set_doc(self._diff_doc)

    def _session_id(self) -> str:
        if self.meta and self.meta.session_id:
            return self.meta.session_id
        return self.session_dir.name

    def _reports_dir(self) -> Path:
        """Export dir for finding reports (``~/.groket/reports``)."""
        from ...paths import reports_dir

        return reports_dir()

    _PLUGIN_TITLES: dict[str, str] = {
        "engine": t("ui-detectors"),
        "basic": t("ui-basic"),
        "feedback": t("ui-feedback"),
        "noop": t("ui-noop"),
    }

    @classmethod
    def _plugin_title(cls, aid: str) -> str:
        return cls._PLUGIN_TITLES.get(aid, aid.replace("_", " ").title())

    def _plugin_has_report_content(self, aid: str, result: AnalysisResult | None) -> bool:
        """True if the Report filter / section is worth listing (not an empty ok run)."""
        if aid == "noop":
            return False
        if any((f.plugin_id or "") == aid for f in self._findings):
            return True
        if result is None:
            return False
        if (result.error or "").strip():
            return True
        for val in (result.artifacts or {}).values():
            if str(val).strip():
                return True
        return False

    def _report_plugin_ids(self) -> list[str]:
        """Analyzer ids with real report content (enabled only; skips empty runs)."""
        ids: set[str] = set()
        for aid, result in self._active_plugin_results().items():
            if self._plugin_has_report_content(aid, result):
                ids.add(aid)
        for f in self._findings:
            pid = (f.plugin_id or "").strip()
            if pid and pid != "noop":
                ids.add(pid)
        return sorted(ids)

    @staticmethod
    def _report_plugin_slug(aid: str) -> str:
        """DOM-safe fragment for section / widget ids."""
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in aid) or "plugin"

    def _report_section_dom_id(self, key: str) -> str:
        if key == "flags":
            return "report-section-flags"
        if key == "notes":
            return "report-section-notes"
        return f"report-section-plugin-{self._report_plugin_slug(key)}"

    def _report_filter_options(self) -> list[tuple[str, str]]:
        """Select options: All, Flags/Notes (if any), then plugins that have content."""
        opts: list[tuple[str, str]] = [(U.all_sections(), "all")]
        n_flags = len(self._flags)
        if n_flags:
            opts.append((t("browser-flags-count", n=n_flags), "flags"))
        n_notes = len(self._notes_doc.notes)
        if n_notes:
            opts.append((t("browser-notes-count", n=n_notes), "notes"))
        for aid in self._report_plugin_ids():
            n = sum(1 for f in self._findings if (f.plugin_id or "") == aid)
            label = self._plugin_title(aid)
            if n:
                label = f"{label} ({n})"
            else:
                label = join_ui(label, t("ui-report"))
            opts.append((label, f"plugin:{aid}"))
        return opts

    def _sync_report_view_select(self) -> None:
        """Refresh Report Filter dropdown options when plugins/findings change."""
        options = self._report_filter_options()
        key = tuple((f"{lab}\x00{val}" for lab, val in options))
        if key == self._report_select_options_key:
            return
        self._report_select_options_key = key
        try:
            sel = self.query_one("#report-view-select", Select)
        except Exception:
            return
        current = self._report_filter or "all"
        valid = {v for _, v in options}
        if current not in valid:
            current = "all"
            self._report_filter = "all"
        prev = self._report_updating
        self._report_updating = True
        try:
            sel.set_options(options)
            if sel.value != current:
                sel.value = current
        except Exception:
            logger.debug(t("ui-report-view-select-sync-failed"), exc_info=True)
        finally:
            self._report_updating = prev

    def _ensure_report_sections(self) -> None:
        """Mount inline panel-cards per plugin (idempotent); no checkbox row."""
        try:
            host = self.query_one("#report-sections-host", Vertical)
        except Exception:
            return
        self._report_section_keys.add("flags")
        self._report_section_keys.add("notes")
        for aid in self._report_plugin_ids():
            if aid in self._report_section_keys:
                continue
            section_id = self._report_section_dom_id(aid)
            # Empty card; panes mount as direct SelectableStatic children.
            card = Vertical(classes=t("ui-panel-card-report-section"), id=section_id)
            if self._analysis_pending:
                card.loading = True
            try:
                host.mount(card)
                self._report_section_keys.add(aid)
            except Exception:
                logger.debug(t("ui-failed-to-mount-report-section-s"), aid, exc_info=True)
        self._sync_report_view_select()
        self._apply_report_visibility()

    def _section_visible(self, key: str) -> bool:
        """Whether section *key* (flags | notes | plugin id) is shown for current filter."""
        mode = self._report_filter or "all"
        if mode == "all":
            return True
        if mode == "flags":
            return key == "flags"
        if mode == "notes":
            return key == "notes"
        if mode.startswith("plugin:"):
            return key == mode[7:]
        return True

    def _apply_report_visibility(self) -> None:
        """Show/hide inline sections from exclusive ``_report_filter`` (display only)."""
        for key in self._report_section_keys:
            section_id = self._report_section_dom_id(key)
            try:
                section = self.query_one(f"#{section_id}")
                section.display = self._section_visible(key)
            except Exception:
                pass

    def _set_static_content(self, widget_id: str, renderable) -> None:
        try:
            # SelectableStatic subclasses Static — query Static matches both.
            widget = self.query_one(f"#{widget_id}", Static)
            if self._widget_has_text_selection(widget):
                return
            widget.update(renderable)
        except Exception:
            logger.debug(t("ui-report-static-s-missing"), widget_id, exc_info=True)

    def _plain_from_widget_id(self, widget_id: str) -> str:
        with suppress(Exception):
            widget = self.query_one(f"#{widget_id}", SelectableStatic)
            return (widget.get_plain_text() or "").strip()
        return ""

    def _collect_active_tab_plain_text(self) -> tuple[str, str]:
        """Primary body for the active browser tab (no selection / no focus).

        Report has many sibling panes — without focus there is no single
        primary body (operator Tabs to a pane, then ``y``). Timeline,
        Summary, Diff, and Findings each have one obvious body.

        :returns: ``(text, kind)`` where *kind* is ``detail`` / ``content`` /
            ``none``.
        """
        tab = self._active_browser_tab()
        if tab == "tab-reports":
            # Multipane: require focused SelectableStatic (handled earlier).
            return ("", "none")
        if tab == "tab-summary":
            text = self._plain_from_widget_id("summary-content")
            return (text, "content" if text else "none")
        if tab == "tab-diff":
            with suppress(Exception):
                text = self.query_one("#diff-view", DiffView).selected_plain()
                return (text, "content" if text else "none")
            return ("", "none")
        if tab == "tab-findings":
            text = self._plain_from_widget_id("findings-header")
            return (text, "content" if text else "none")
        # Timeline (default): full detail pane.
        with suppress(Exception):
            detail = self.query_one("#detail-panel", DetailView)
            text = (detail.get_plain_text() or "").strip()
            return (text, "detail" if text else "none")
        return ("", "none")

    def _update_reports_tab(self) -> None:
        """Fill overview + each inline section; Filter Select controls display."""
        if self._report_updating:
            return
        self._report_updating = True
        try:
            self._ensure_report_sections()
            self._render_report_overview()
            self._render_report_flags()
            self._render_report_notes()
            for aid in self._report_plugin_ids():
                if aid in self._report_section_keys:
                    self._render_report_plugin(aid)
        finally:
            self._report_updating = False
        self._apply_report_loading_overlays()

    def _render_report_overview(self) -> None:
        sid = self._session_id()
        model = self.meta.model_display if self.meta else "unknown"
        flags = self._flags
        total = len(self._findings)
        high = sum(1 for f in self._findings if f.severity.value == "high")
        med = sum(1 for f in self._findings if f.severity.value == "medium")
        blocks: list = []
        head = Text()
        head.append(t("ui-session-report"), style="bold")
        head.append("\n")
        stale_hints = getattr(self, "_analysis_stale_hints", None) or []
        if stale_hints:
            head.append(
                t("analysis-stale-report", detail=self._stale_detail(stale_hints)),
                style="bold yellow",
            )
            head.append("\n\n")
        # Severity chips use the same Rich styles as Findings tab (not status_chip).
        if high:
            head.append(t("browser-high-chip", n=high), style=severity_style("high"))
            head.append("  ")
        if med:
            head.append(t("browser-medium-chip", n=med), style=severity_style("medium"))
            head.append("  ")
        if total and not high and not med:
            head.append(t("browser-findings-chip", n=total), style="dim")
            head.append("  ")
        if not total:
            head.append_text(status_chip(t("browser-status-clean"), kind="ok"))
            head.append("  ")
        head.append(t("browser-flags-dim", n=len(flags)), style="dim")
        head.append(" │ ", style="dim")
        head.append(
            t("browser-report-counts", total=total, high=high, med=med),
            style="dim",
        )
        head.append("\n")
        blocks.append(head)
        blocks.append(dim_rule())
        meta_t = Text()
        meta_t.append_text(kv_line(t("ui-session-2"), sid))
        meta_t.append_text(kv_line(t("ui-model"), model or "—"))
        if self.meta and (self.meta.turn_outcome or "").strip():
            meta_t.append_text(
                kv_line(
                    t("ui-last-outcome"),
                    t(
                        "browser-last-turn-outcome-note",
                        outcome=(self.meta.turn_outcome or "").strip(),
                    ),
                )
            )
        blocks.append(meta_t)
        if self._report_filter and self._report_filter != "all":
            mode = self._report_filter
            if mode == "flags":
                focus = t("ui-flags-2")
            elif mode.startswith("plugin:"):
                focus = self._plugin_title(mode[7:])
            else:
                focus = mode
            blocks.append(Text(t("browser-viewing-focus", focus=focus) + "\n", style="dim"))
        try:
            self._set_static_content("report-overview-content", panel_group(*blocks))
        except Exception:
            self._set_static_content("report-overview-content", t("ui-report-unavailable"))
        try:
            self._sync_report_empty_states()
        except Exception:
            pass

    def _render_report_flags(self) -> None:
        flags = sorted(self._flags.values(), key=lambda fl: fl.event_index)
        fl_t = Text()
        fl_t.append_text(section_header(U.flags_heading()))
        fl_t.append(f"  {U.flags_blurb()}\n", style="dim")
        if flags:
            for fl in flags:
                ver = fl.verdict.value.replace("_", " ")
                tool = fl.tool_name or fl.event_type or "event"
                note = fl.description or t("ui-no-note")
                fl_t.append_text(bullet(f"#{fl.event_index}  {tool}  ·  {ver}  — {note}"))
                if fl.created_at:
                    fl_t.append(f"      {fl.created_at}\n", style="dim")
        self._set_static_content("report-flags-content", fl_t)
        try:
            self._sync_report_empty_states()
        except Exception:
            pass

    def _render_report_notes(self) -> None:
        notes = self._notes_doc.sorted_notes()
        preferred_ids = [f.id for f in load_schema().fields]
        nt = Text()
        nt.append_text(section_header(U.notes_heading()))
        nt.append(f"  {U.notes_blurb()}\n", style="dim")
        for note in notes:
            summary = next(
                (
                    (note.fields.get(fid) or "").strip()
                    for fid in preferred_ids
                    if (note.fields.get(fid) or "").strip()
                ),
                "",
            )
            if not summary:
                summary = next(
                    (str(v).strip() for v in note.fields.values() if str(v).strip()),
                    U.notes_empty_preview(),
                )
            preview = summary.replace("\n", " ")
            if len(preview) > 100:
                preview = preview[:97] + "…"
            ev = ""
            if note.event_indices:
                ev = "  ·  #" + ",".join(str(i) for i in note.event_indices)
            turn_lab = t("turn-filter-n", n=note.turn_index)
            nt.append_text(bullet(f"{turn_lab}{ev}  — {preview}"))
            if note.updated_at or note.created_at:
                nt.append(
                    f"      {note.updated_at or note.created_at}\n",
                    style="dim",
                )
        self._set_static_content("report-notes-content", nt)
        try:
            self._sync_report_empty_states()
        except Exception:
            pass

    def _render_report_plugin(self, aid: str) -> None:
        """Fill one plugin card as multiple focusable/copyable panes."""
        plugin_findings = [f for f in self._findings if (f.plugin_id or "") == aid]
        result = self._active_plugin_results().get(aid)
        if result is None and aid not in self._active_plugin_results():
            return
        header_blocks: list = []
        title = self._plugin_title(aid)
        if result is not None and result.summary:
            title = f"{title}  ({result.summary})"
        header_blocks.append(section_header(title))
        report_artifact = None
        if result is not None and result.ok:
            report_artifact = (result.artifacts or {}).get("report")
        if plugin_findings:
            header_blocks.append(self._findings_report_block(plugin_findings))

        renderables: list = [panel_group(*header_blocks)]
        if report_artifact and str(report_artifact).strip():
            # Reorder issue blocks by Turn because source reports may use another order.
            report_md = order_report_markdown_by_turn(str(report_artifact).strip())
            for chunk in split_report_markdown_panes(report_md):
                renderables.append(content_block(chunk, max_chars=12000))
        elif not plugin_findings and result is not None:
            if result.summary:
                renderables.append(Text(f"  {result.summary}\n", style="dim"))
            elif not result.ok and result.error:
                renderables.append(
                    Text(
                        t("browser-report-error", msg=result.error) + "\n",
                        style="red",
                    )
                )
            else:
                renderables.append(Text(t("ui-no-findings"), style="dim"))
        elif not plugin_findings:
            renderables.append(Text(t("ui-no-findings"), style="dim"))

        try:
            self._sync_report_plugin_panes(aid, renderables)
        except Exception:
            logger.debug(t("ui-report-static-s-missing"), aid, exc_info=True)

    def _sync_report_plugin_panes(self, aid: str, renderables: list) -> None:
        """Update/mount SelectableStatic panes under a plugin section card.

        Reuses widgets by index when possible so active text selection on a
        pane is not cleared mid-drag. Drops extras only when they have no
        selection. Panes are direct children of the section (same pattern as
        flags/notes), not a nested host that races on deferred mount.
        """
        section_id = self._report_section_dom_id(aid)
        try:
            section = self.query_one(f"#{section_id}", Vertical)
        except Exception:
            return

        existing = list(section.query(SelectableStatic))
        slug = self._report_plugin_slug(aid)
        n = len(renderables)
        for i, renderable in enumerate(renderables):
            if i < len(existing):
                widget = existing[i]
                if not self._widget_has_text_selection(widget):
                    widget.update(renderable)
                continue
            widget = SelectableStatic(
                renderable,
                id=f"report-pane-{slug}-{i}",
                classes="report-pane",
            )
            section.mount(widget)
        for j in range(len(existing) - 1, n - 1, -1):
            widget = existing[j]
            if self._widget_has_text_selection(widget):
                continue
            with suppress(Exception):
                widget.remove()

    @on(Select.Changed, "#report-view-select")
    def _on_report_view_changed(self, event: Select.Changed) -> None:
        """Exclusive section filter — same pattern as Timeline View Select."""
        if self._report_updating:
            return
        val = event.value
        if val is Select.BLANK or val is None:
            return
        mode = str(val)
        if mode == self._report_filter:
            return
        self._report_filter = mode
        self._apply_report_visibility()
        try:
            self._render_report_overview()
        except Exception:
            pass

    @staticmethod
    def _findings_report_block(findings: list) -> Text:
        """Structured finding list (severity + title + detail) — not raw markdown dump.

        Severity colors use :func:`~groket.ui.styles.severity_style` — same as
        Findings tab / timeline marks (high=red, medium=dark_orange, low=yellow).
        """
        out = Text()
        # Caller passes turn-ordered findings (see _collect_findings); keep order.
        for f in findings:
            sev_key = (f.severity.value if f.severity else "low").lower()
            sev = sev_key.upper()
            sev_style = severity_style(sev_key)
            out.append("  ")
            out.append(f"{sev:<7}", style=sev_style)
            out.append("  ")
            out.append(f.title or f.id or "(untitled)")
            cat = getattr(f, "category", None) or ""
            if cat:
                out.append(f"  ·  {cat}", style="dim")
            n_ev = len(
                {
                    *(getattr(f, "all_tool_call_ids", None) or []),
                    *(f"u{i}" for i in (getattr(f, "all_update_indices", None) or [])),
                    *(f"e{i}" for i in (getattr(f, "all_event_indices", None) or [])),
                }
            )
            if n_ev:
                out.append("  ·  " + t("browser-finding-events", n=n_ev), style="dim")
            out.append("\n")
            detail = (getattr(f, "detail", None) or "").strip()
            if detail:
                # Preserve structure for multi-line reviews; only collapse tiny blurbs.
                if "\n" in detail or len(detail) > 280:
                    for i, dl in enumerate(detail.splitlines()[:24]):
                        out.append(f"           {dl}\n", style="dim")
                    if detail.count("\n") >= 24:
                        out.append("           …\n", style="dim")
                else:
                    one_line = " ".join(detail.split())
                    if len(one_line) > 220:
                        one_line = one_line[:217] + "…"
                    out.append(f"           {one_line}\n", style="dim")
            children = getattr(f, "children", None) or []
            for ch in children[:8]:
                ch_title = getattr(ch, "title", None) or getattr(ch, "id", "") or ""
                out.append(f"           - {ch_title}\n", style="dim")
            if len(children) > 8:
                out.append(
                    t("browser-more-children", n=len(children) - 8),
                    style="dim",
                )
        if not findings:
            out.append(t("ui-none"), style="dim")
        return out

    def _update_stats(self) -> None:
        """Fill Summary-pane tables (turns, event mix, tool timing, phases)."""
        if not self.meta:
            return
        m = self.meta
        type_counts = Counter(e.event_type for e in self.timeline)
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        durations = timeline_table.durations
        tool_call_events = [e for e in self.timeline if e.event_type == "tool_call" and e.tool_name]
        tool_counts: Counter[str] = Counter(e.tool_name for e in tool_call_events)
        tool_errors: Counter[str] = Counter(e.tool_name for e in tool_call_events if e.is_error)
        tool_durations: dict[str, list[float]] = defaultdict(list)
        for e in tool_call_events:
            dur = durations.get(e.index)
            if dur is not None:
                tool_durations[e.tool_name].append(dur)
        try:
            from ...session.turns import segment_timeline_turns, turn_summary_rows

            turn_segments = segment_timeline_turns(self.timeline)
            samples = {}
            store = getattr(self, "_context_samples", None)
            if store is not None:
                samples = store.compact_by_turn()
            turn_rows = turn_summary_rows(
                turn_segments,
                durations=durations,
                session_context_compact=m.context_usage_compact,
                context_by_turn=samples,
            )
        except Exception:
            turn_rows = []
        self._update_subagents_table()
        self._update_jobs_table()
        self._update_workflows_table()
        try:
            turns_table = self.query_one("#stats-turns-table", DataTable)
            style_data_table(turns_table)
            turns_table.clear(columns=True)
            turns_table.add_columns(
                t("col-index"),
                t("ui-label"),
                t("ui-outcome"),
                t("ui-events"),
                t("ui-tools"),
                t("ui-dur"),
            )
            self._summary_turn_first = {}
            if turn_rows:
                seen_turn_keys: set[str] = set()
                for i, row in enumerate(turn_rows):
                    dur_raw = row.get("duration_s")
                    dur_s = (
                        self._fmt_dur(float(dur_raw)) if isinstance(dur_raw, (int, float)) else "—"
                    )
                    turn_i = row.get("turn_index", row.get("turn", i))
                    first = row.get("first_index")
                    if isinstance(turn_i, int) and isinstance(first, int):
                        self._summary_turn_first[turn_i] = first
                    tkey = f"turn-{turn_i}-{i}"
                    if tkey in seen_turn_keys:
                        continue
                    seen_turn_keys.add(tkey)
                    turns_table.add_row(
                        str(row.get("turn", "")),
                        str(row.get("label", "")),
                        str(row.get("outcome", "—")),
                        str(row.get("events", 0)),
                        str(row.get("tools", 0)),
                        dur_s,
                        key=tkey,
                    )
            else:
                turns_table.add_row(
                    "—",
                    t("ui-no-timeline"),
                    "—",
                    "0",
                    "0",
                    "—",
                )
        except Exception:
            pass
        show_turns = len(turn_rows) > 1
        show_subs = bool(self._subagent_runs)
        show_jobs = bool(self._session_jobs.jobs or self._session_jobs.schedules)
        show_wfs = bool(self._session_jobs.workflows)
        try:
            self.query_one("#summary-turns-card").display = show_turns
            self.query_one("#summary-subagents-card").display = show_subs
            self.query_one("#summary-turns-pair").display = show_turns or show_subs
            self.query_one("#summary-jobs-card").display = show_jobs
            self.query_one("#summary-workflows-card").display = show_wfs
        except Exception:
            pass
        ev_table = self.query_one("#stats-events-table", DataTable)
        style_data_table(ev_table)
        ev_table.clear(columns=True)
        ev_table.add_columns(U.col_event_type(), U.col_count())
        for etype, count in type_counts.most_common():
            ev_table.add_row(et.type_label(etype), str(count))
        if not type_counts:
            ev_table.add_row("(none)", "0")
        tool_cat: dict[str, str] = {}
        try:
            from ...session.usage_stats import collect_session_usage

            usage = collect_session_usage(self.session_dir, self.timeline, durations=durations)
            tool_cat = {r.name: r.category for r in usage.tools}
        except Exception:
            pass

        def _tool_sort_key(item: tuple[str, int]) -> tuple[int, int, str]:
            name, cnt = item
            cat = tool_cat.get(name, "")
            tier = 0 if cat == "builtin" or not cat else 1 if cat == "mcp_bridge" else 2
            return (tier, -cnt, name)

        tools_table = self.query_one("#stats-tools-table", DataTable)
        style_data_table(tools_table)
        tools_table.clear(columns=True)
        tools_table.add_columns(
            t("ui-tool-1"),
            t("ui-calls"),
            t("ui-errors-2"),
            t("ui-total-1"),
            t("ui-avg"),
        )
        for tool, count in sorted(tool_counts.items(), key=_tool_sort_key):
            errs = tool_errors.get(tool, 0)
            durs = tool_durations.get(tool, [])
            if durs:
                total_s = self._fmt_dur(sum(durs))
                avg_s = self._fmt_dur(sum(durs) / len(durs))
            else:
                total_s = avg_s = "—"
            tools_table.add_row(tool, str(count), str(errs) if errs else "—", total_s, avg_s)
        all_durs = [d for dlist in tool_durations.values() for d in dlist]
        if all_durs:
            tools_table.add_row(
                t("ui-total-2"),
                str(len(tool_call_events)),
                str(sum(tool_errors.values()) or "—"),
                self._fmt_dur(sum(all_durs)),
                "—",
            )
        phase_durations: dict[str, float] = defaultdict(float)
        phase_labels = {
            "agent_thought_chunk": t("ui-thinking"),
            "agent_message_chunk": t("ui-writing"),
            "tool_call": t("ui-tool-execution"),
            "tool_call_update": t("ui-tool-execution"),
            "user_message_chunk": t("ui-user-input"),
            "plan": t("ui-planning"),
            "subagent_spawned": t("ui-subagent"),
            "subagent_finished": t("ui-subagent"),
            "task_backgrounded": t("ui-background-jobs"),
            "task_completed": t("ui-background-jobs"),
            "scheduled_task_created": t("ui-schedule"),
            "scheduled_task_updated": t("ui-schedule"),
            "scheduled_task_fired": t("ui-schedule"),
            "scheduled_task_deleted": t("ui-schedule"),
        }
        for ev in self.timeline:
            dur = durations.get(ev.index)
            if dur is None:
                continue
            label = phase_labels.get(ev.event_type, t("ui-other"))
            phase_durations[label] += dur
        phases_table = self.query_one("#stats-phases-table", DataTable)
        style_data_table(phases_table)
        phases_table.clear(columns=True)
        phases_table.add_columns(U.col_activity(), U.col_time(), U.col_percent())
        if phase_durations:
            total_accounted = sum(phase_durations.values())
            for label, secs in sorted(phase_durations.items(), key=lambda x: -x[1]):
                pct = secs / total_accounted * 100 if total_accounted else 0
                phases_table.add_row(label, self._fmt_dur(secs), f"{pct:.1f}%")
            phases_table.add_row("total", self._fmt_dur(total_accounted), "100%")
            if m.duration_seconds and total_accounted < m.duration_seconds:
                unaccounted = m.duration_seconds - total_accounted
                phases_table.add_row("overhead", self._fmt_dur(unaccounted), "—")
        else:
            phases_table.add_row("(none)", "—", "—")

    @on(DataTable.RowSelected, "#timeline-list")
    def _on_timeline_row_selected(self, event: DataTable.RowSelected) -> None:
        """Enter or click on a spawn/finish bookend opens that child."""
        raw = str(event.row_key.value) if event.row_key is not None else ""
        if not raw.isdigit():
            return
        idx = int(raw)
        ev = next((e for e in self.timeline if int(e.index) == idx), None)
        if ev is None:
            return
        self._open_subagent_run(self._run_for_bookend_event(ev))

    @on(TimelineTable.EventSelected)
    def _on_event_selected(self, message: TimelineTable.EventSelected) -> None:
        """Update selection; debounce detail paint while the operator scrolls."""
        ev = message.event
        self._current_event = ev
        self.refresh_bindings()
        # Coalesce rapid RowHighlighted events (hold-down / wheel) so Rich/Textual
        # do not reflow the detail pane on every intermediate row.
        if self._detail_debounce is not None:
            self._detail_debounce.stop()
            self._detail_debounce = None
        self._detail_debounce = self.set_timer(0.04, self._paint_selected_event_detail)

    def _paint_selected_event_detail(self) -> None:
        """Flush debounced detail panel for :attr:`_current_event`."""
        self._detail_debounce = None
        table = self._show_selected_event_detail()
        ev = self._current_event
        if ev is None or table is None:
            return
        self._request_event_body_expand(ev, table)

    def _show_selected_event_detail(self) -> TimelineTable | None:
        """Paint the open-event pane from the current row (no owner fetch)."""
        ev = self._current_event
        if ev is None or not self.is_mounted:
            return None
        try:
            detail = self.query_one("#detail-panel", DetailView)
            timeline_table = self.query_one("#timeline-list", TimelineTable)
        except Exception:
            return None
        finding = self._findings_by_call.get(ev.tool_call_id)
        duration = timeline_table.durations.get(ev.index)
        flag = self._flags.get(ev.index)
        detail.show_event(
            ev,
            finding,
            flag,
            duration=duration,
            paired_call=timeline_table.get_paired_call(ev),
            paired_result=timeline_table.get_paired_result(ev),
            turn_index=timeline_table.turn_index_for(ev.index),
            subagent_run=self._run_for_bookend_event(ev),
            job_mate=timeline_table.job_mate(ev),
            schedule=schedule_for_event(ev, self._session_jobs.schedules),
            workflow=workflow_for_event(
                ev,
                self._session_jobs.workflows,
                mate=timeline_table.get_paired_result(ev) or timeline_table.get_paired_call(ev),
            ),
        )
        return timeline_table

    def _request_event_body_expand(self, ev: TraceEvent, table: TimelineTable) -> None:
        """Ask the owner for the open-event body ceiling (one row, plus pair)."""
        if not self._uses_control_data():
            return
        want = {int(ev.index)}
        partner = table.get_paired_result(ev) or table.get_paired_call(ev)
        if partner is not None:
            want.add(int(partner.index))
        for index in want:
            if index in self._detail_expanded or index in self._detail_expanding:
                continue
            self._detail_expanding.add(index)
            self._expand_event_body(index)

    @work(thread=True)
    def _expand_event_body(self, index: int) -> None:
        import asyncio

        from ...session.wire_timeline import fetch_timeline_event

        ev: TraceEvent | None = None
        try:
            access = self._control_access()
            ref = self._session_control_ref()
            ev = asyncio.run(fetch_timeline_event(access, ref, index))
        except (TimeoutError, OSError, ConnectionError, ControlError, RuntimeError, TypeError):
            logger.debug("open-event body expand failed", exc_info=True)
        finally:
            self._detail_expanding.discard(index)
        if ev is None or not self.is_mounted:
            return
        call_ui(resolve_ui_app(self), self._apply_expanded_event, ev)

    def _apply_expanded_event(self, ev: TraceEvent) -> None:
        """Replace one timeline row with the larger open-event body."""
        if not self.is_mounted:
            return
        idx = int(ev.index)
        self._detail_expanded.add(idx)
        for i, old in enumerate(self.timeline or []):
            if int(old.index) == idx:
                self.timeline[i] = ev
                break
        table: TimelineTable | None
        try:
            table = self.query_one("#timeline-list", TimelineTable)
            for i, old in enumerate(table.events):
                if int(old.index) == idx:
                    table.events[i] = ev
                    break
            table._build_tool_pairs()
        except Exception:
            table = None
        cur = self._current_event
        if cur is None:
            return
        if int(cur.index) == idx:
            self._current_event = ev
            self._show_selected_event_detail()
            return
        if table is None:
            return
        partner = table.get_paired_result(cur) or table.get_paired_call(cur)
        if partner is not None and int(partner.index) == idx:
            self._show_selected_event_detail()

    def on_descendant_focus(self, _event) -> None:
        self.refresh_bindings()

    def on_descendant_blur(self, _event) -> None:
        self.refresh_bindings()

    def action_refresh_context(self) -> None:
        """Reload timeline/meta for this session and re-run analysis."""
        self.notify(U.refreshing_session_view(), severity="information", timeout=3)
        self._load_data()

    def action_open_share(self) -> None:
        """Open Grok share URL for this session (from groket-share.json) in the browser."""
        try:
            from ...runs.live_share import get_share_display, refresh_share_from_disk

            url = refresh_share_from_disk(self.session_dir)
            info = get_share_display(self.session_dir)
            if not url:
                _ = info
                return
            try:
                import webbrowser

                webbrowser.open(url)
            except Exception as exc:
                self.notify(U.could_not_open_share(str(exc)), severity="error", timeout=10)
        except Exception as exc:
            self.notify(U.share_failed(str(exc)), severity="error")

    _TIMELINE_VIEWS: tuple[str, ...] = (
        "all",
        "tools",
        "user",
        "asst",
        "sess",
        "subagents",
        "background",
        "workflows",
        "errors",
    )

    def _sync_timeline_view_select(self, mode: str) -> None:
        try:
            sel = self.query_one("#timeline-view-select", Select)
            if sel.value != mode:
                sel.value = mode
        except Exception:
            pass

    def _ensure_timeline_tab(self, *, focus_list: bool = True) -> None:
        """Timeline view only applies on pane 1 — switch there first.

        Jump callers pass ``focus_list=False`` so tab activation does not
        focus row 0 before the cursor lands on the target event.
        """
        selector = "#timeline-list" if focus_list else ""
        try:
            tabs = self.query_one("#browser-tabs", TabbedContent)
            if tabs.active != "tab-timeline":
                self.activate_tab_pane("tab-timeline", focus_selector=selector)
        except Exception:
            self.activate_tab_pane("tab-timeline", focus_selector=selector)

    def _apply_timeline_mode(self, mode: str) -> None:
        """Apply View dropdown mode; keyboard and Select stay aligned."""
        if mode not in self._TIMELINE_VIEWS:
            mode = "all"
        self._ensure_timeline_tab()
        self._timeline_filter = mode
        self._sync_timeline_view_select(mode)
        self._apply_timeline_filters()

        def _focus_tl() -> None:
            try:
                focus_primary_list(self.query_one("#timeline-list", TimelineTable))
            except Exception:
                pass

        self.call_after_refresh(lambda: self.call_after_refresh(_focus_tl))

    def action_focus_timeline_filter(self) -> None:
        """``v`` — focus the View select (open with Enter / arrows)."""
        self._ensure_timeline_tab()

        def _focus() -> None:
            try:
                self.query_one("#timeline-view-select", Select).focus()
            except Exception:
                pass

        self.call_after_refresh(lambda: self.call_after_refresh(_focus))

    @on(Select.Changed, "#timeline-view-select")
    def _on_timeline_view_changed(self, event: Select.Changed) -> None:
        val = event.value
        if val is Select.BLANK or val is None:
            return
        mode = str(val)
        if mode == self._timeline_filter:
            return
        self._timeline_filter = mode
        self._apply_timeline_filters()

    def _finding_row_index(
        self, event: DataTable.RowHighlighted | DataTable.RowSelected
    ) -> int | None:
        """Map findings-table row event → index in _findings_table_entries.

        Row keys are normally ``"0"``, ``"1"``, … (index into ``_findings_table_entries``).
        Older/in-flight TUI builds briefly used ``i-{rule_id}-{n}`` / ``c-{id}-{n}``; never
        ``int()`` those blindly — fall back to cursor row or suffix digit.
        """
        if event.row_key is not None:
            raw = str(event.row_key.value).strip()
            if raw.isdigit():
                idx = int(raw)
                if 0 <= idx < len(self._findings_table_entries):
                    return idx
            if "-" in raw:
                tail = raw.rsplit("-", 1)[-1]
                if tail.isdigit():
                    idx = int(tail)
                    if 0 <= idx < len(self._findings_table_entries):
                        return idx
        try:
            table = self.query_one("#findings-table", DataTable)
            cr = getattr(table, "cursor_row", None)
            if cr is not None and 0 <= int(cr) < len(self._findings_table_entries):
                return int(cr)
        except Exception:
            pass
        return None

    @on(DataTable.RowHighlighted, "#findings-table")
    def _on_finding_highlighted(self, event: DataTable.RowHighlighted) -> None:
        try:
            idx = self._finding_row_index(event)
            if idx is not None and idx < len(self._findings_table_entries):
                self._selected_finding = self._findings_table_entries[idx]
        except Exception:
            pass

    @on(DataTable.RowSelected, "#findings-table")
    def _on_finding_selected(self, event: DataTable.RowSelected) -> None:
        try:
            idx = self._finding_row_index(event)
        except Exception:
            return
        if idx is None or idx >= len(self._findings_table_entries):
            return
        finding = self._findings_table_entries[idx]
        self._selected_finding = finding
        call_ids = set(finding.all_tool_call_ids)
        update_indices = set(finding.all_update_indices)
        event_indices = set(finding.all_event_indices)
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        timeline_table.apply_filter(
            call_ids=call_ids or None,
            update_indices=update_indices or None,
            event_indices=event_indices or None,
        )
        tabbed = self.query_one(TabbedContent)
        tabbed.active = "tab-timeline"

    @on(Switch.Changed, "#timeline-tail")
    def _on_timeline_tail_changed(self, event: Switch.Changed) -> None:
        """Tail on: jump to the last event. Off: leave the highlight where it is."""
        if not event.value:
            return
        try:
            tl = self.query_one("#timeline-list", TimelineTable)
        except Exception:
            return
        tl.scroll_to_end()

    @on(Click, "#timeline-tail-label")
    def _on_timeline_tail_label_clicked(self, event: Click) -> None:
        """The Tail word toggles the switch (same as clicking the slider)."""
        event.stop()
        try:
            self.query_one("#timeline-tail", Switch).toggle()
        except Exception:
            return

    @on(Input.Changed, "#search-input")
    def _on_search_changed(self, event: Input.Changed) -> None:
        """Filter timeline after a short idle so each key does not rebuild the table."""
        self._timeline_search = event.value or ""
        if self._search_debounce is not None:
            try:
                self._search_debounce.stop()
            except Exception:
                pass
            self._search_debounce = None
        from ...constants import TIMELINE_SEARCH_DEBOUNCE_S

        self._search_debounce = self.set_timer(
            TIMELINE_SEARCH_DEBOUNCE_S, self._apply_debounced_timeline_search
        )

    def _apply_debounced_timeline_search(self) -> None:
        self._search_debounce = None
        if not self.is_mounted:
            return
        self._apply_timeline_filters()

    @on(Input.Submitted, "#search-input")
    def _on_search_submitted(self, event: Input.Submitted) -> None:
        """Enter applies the filter now and moves focus to the timeline list."""
        if self._search_debounce is not None:
            try:
                self._search_debounce.stop()
            except Exception:
                pass
            self._search_debounce = None
        self._timeline_search = event.value or ""
        self._apply_timeline_filters()
        try:
            focus_primary_list(self.query_one("#timeline-list", TimelineTable))
        except Exception:
            pass

    def _turn_event_indices(self) -> set[int] | None:
        """Event indices for the Turn filter, or None for all turns.

        Session-level timeline rows (e.g. system prompt) are not part of any
        turn segment but stay visible when a specific turn is selected.
        """
        tf = getattr(self, "_turn_filter", "all")
        if tf in (None, "", "all"):
            return None
        try:
            ti = int(tf)
        except (TypeError, ValueError):
            return None
        from ...session.turns import (
            event_display_turn_map,
            events_on_display_turn,
            is_session_level_timeline_event,
        )

        segs = getattr(self, "_turn_segments", None) or []
        turn_by = event_display_turn_map(segs)
        indices: set[int] = set()
        found = False
        for seg in segs:
            if seg.turn_index == ti:
                found = True
                indices.update(e.index for e in events_on_display_turn(seg, turn_by))
        if not found:
            return None
        for ev in self.timeline:
            if is_session_level_timeline_event(ev):
                indices.add(ev.index)
        return indices

    def _timeline_filters_active(self) -> bool:
        """True when View / Turn / search would hide some timeline rows."""
        mode = getattr(self, "_timeline_filter", "all") or "all"
        search = getattr(self, "_timeline_search", "") or ""
        turn = getattr(self, "_turn_filter", "all") or "all"
        return mode != "all" or bool(search.strip()) or str(turn) != "all"

    def _rebuild_turn_select(self) -> None:
        """Refresh Turn dropdown; hide it for single-turn (or empty) sessions.

        Always re-segment when the timeline grows (or the tail event identity
        changes). A previous optimization skipped re-segment when already
        multi-turn and the last row was a tool/agent event — that dropped new
        turns whose live batch ended on a tool_call after ``turn_started``
        (e.g. turn 42 never appeared until F5).
        """
        from ...session.turns import segment_timeline_turns

        tl = self.timeline or []
        last = tl[-1] if tl else None
        sig = (len(tl), last.index if last is not None else None)
        if (
            sig == getattr(self, "_turn_rebuild_sig", None)
            and getattr(self, "_turn_segments", None) is not None
            and self._last_turn_segment_count >= 0
        ):
            return
        self._turn_rebuild_sig = sig

        self._turn_segments = segment_timeline_turns(tl)
        n_segs = len(self._turn_segments)
        multi = n_segs > 1
        try:
            sel = self.query_one("#timeline-turn-select", Select)
        except Exception:
            self._last_turn_segment_count = n_segs
            return
        if not multi:
            # No choice to make — keep filter off and hide the control.
            self._turn_filter = "all"
            sel.display = False
            self._last_turn_segment_count = n_segs
            self._sync_compact_child_chrome()
            return
        # Skip set_options when turn count is unchanged (live ticks mid-turn).
        if n_segs == self._last_turn_segment_count and sel.display:
            self._last_turn_segment_count = n_segs
            return
        # Label is the trace turn_number. Value is unique turn_index.
        from ...session.turns import display_turn_number

        options: list[tuple[str, str]] = [(t("turn-filter-all"), "all")]
        for seg in self._turn_segments:
            n = display_turn_number(seg)
            label = t("turn-filter-n", n=n) if n is not None else t("turn-filter-unnumbered")
            options.append((label, str(seg.turn_index)))
        sel.display = True
        sel.set_options(options)
        if getattr(self, "_turn_filter", "all") not in {v for _, v in options}:
            self._turn_filter = "all"
        sel.value = getattr(self, "_turn_filter", "all")
        self._last_turn_segment_count = n_segs
        self._sync_compact_child_chrome()

    def _sync_compact_child_chrome(self) -> None:
        """Hide the Summary turns card for a one-turn subagent child."""
        n = len(getattr(self, "_turn_segments", None) or [])
        compact = compact_child_chrome(read_session_kind(self.session_dir), n)
        try:
            self.query_one("#summary-turns-card").display = not compact
        except Exception:
            return

    def _apply_filter(self, **kwargs) -> None:
        timeline_table = self.query_one("#timeline-list", TimelineTable)
        if "event_indices" not in kwargs:
            kwargs["event_indices"] = self._turn_event_indices()
        if "search_query" not in kwargs:
            kwargs["search_query"] = getattr(self, "_timeline_search", "") or ""
        timeline_table.apply_filter(**kwargs)

    @on(Select.Changed, "#timeline-turn-select")
    def _on_timeline_turn_changed(self, event: Select.Changed) -> None:
        val = event.value
        if val is Select.BLANK or val is None:
            return
        self._turn_filter = str(val)
        self._apply_timeline_filters()

    @property
    def selected_prompt_index(self) -> int | None:
        """Source prompt index selected by the active timeline turn filter."""
        turn_filter = getattr(self, "_turn_filter", "all")
        if turn_filter in (None, "", "all"):
            return None
        try:
            turn_index = int(turn_filter)
        except (TypeError, ValueError):
            return None
        for segment in getattr(self, "_turn_segments", None) or []:
            if segment.turn_index == turn_index:
                return (
                    segment.prompt_index if segment.prompt_index is not None else segment.turn_index
                )
        return None

    def select_prompt_index(self, prompt_index: int) -> bool:
        """Select the timeline segment carrying *prompt_index*."""
        target = int(prompt_index)
        self._requested_prompt_index = target
        for segment in getattr(self, "_turn_segments", None) or []:
            source_index = (
                segment.prompt_index if segment.prompt_index is not None else segment.turn_index
            )
            if source_index != target:
                continue
            self._turn_filter = str(segment.turn_index)
            self._requested_prompt_index = None
            self._ensure_timeline_tab()
            try:
                self.query_one("#timeline-turn-select", Select).value = self._turn_filter
            except Exception:
                pass
            self._apply_timeline_filters()
            return True
        return False

    def _operator_turn_ids(self) -> list[int]:
        """Chronological unique trace turn ids for the Turn picker."""
        segs = getattr(self, "_turn_segments", None) or []
        out: list[int] = []
        seen: set[int] = set()
        for seg in segs:
            n = int(seg.turn_index)
            if n in seen:
                continue
            seen.add(n)
            out.append(n)
        return out

    def _turn_step_available(self) -> bool:
        """True when h/l / arrows should step Timeline turns."""
        if len(self._operator_turn_ids()) < 2:
            return False
        return self._active_browser_tab() == "tab-timeline"

    def _set_turn_filter(self, value: str) -> None:
        """Scope Timeline to *value* (``all`` or a trace turn id)."""
        keep = value == "all" and (getattr(self, "_turn_filter", "all") or "all") != "all"
        self._turn_filter = value
        with suppress(Exception):
            sel = self.query_one("#timeline-turn-select", Select)
            if sel.display:
                sel.value = value
        self._ensure_timeline_tab()
        self._apply_timeline_filters()
        self._land_after_turn_step(keep=keep)

    def _diff_view(self) -> DiffView | None:
        with suppress(Exception):
            return self.query_one("#diff-view", DiffView)
        return None

    def action_next_turn(self) -> None:
        """Next Timeline turn, or next Diff snapshot when that pane is showing."""
        if self._active_browser_tab() == "tab-diff":
            view = self._diff_view()
            if view is not None:
                view.step_point(1)
            return
        ids = self._operator_turn_ids()
        if len(ids) < 2:
            return
        cur = getattr(self, "_turn_filter", "all") or "all"
        if cur == "all":
            self._set_turn_filter(str(ids[0]))
            return
        try:
            ti = int(cur)
        except (TypeError, ValueError):
            self._set_turn_filter(str(ids[0]))
            return
        if ti not in ids:
            self._set_turn_filter(str(ids[0]))
            return
        i = ids.index(ti)
        if i + 1 < len(ids):
            self._set_turn_filter(str(ids[i + 1]))

    def action_prev_turn(self) -> None:
        """Previous Timeline turn, or previous Diff snapshot when that pane is showing."""
        if self._active_browser_tab() == "tab-diff":
            view = self._diff_view()
            if view is not None:
                view.step_point(-1)
            return
        ids = self._operator_turn_ids()
        if len(ids) < 2:
            return
        cur = getattr(self, "_turn_filter", "all") or "all"
        if cur == "all":
            self._set_turn_filter(str(ids[-1]))
            return
        try:
            ti = int(cur)
        except (TypeError, ValueError):
            self._set_turn_filter(str(ids[-1]))
            return
        if ti not in ids:
            self._set_turn_filter(str(ids[-1]))
            return
        i = ids.index(ti)
        if i == 0:
            self._set_turn_filter("all")
            return
        self._set_turn_filter(str(ids[i - 1]))

    def _land_target(self, tl: TimelineTable, *, keep: bool) -> TraceEvent | None:
        """First visible event to land on, or the current one when it is still shown."""
        visible = tl.visible_events()
        if keep and self._current_event is not None:
            cur = int(self._current_event.index)
            hit = next((e for e in visible if int(e.index) == cur), None)
            if hit is not None:
                return hit
        return visible[0] if visible else None

    def _land_after_turn_step(self, *, keep: bool) -> None:
        """Put the cursor on a real event and give the list focus so j/k work."""

        def _go() -> None:
            try:
                tl = self.query_one("#timeline-list", TimelineTable)
            except Exception:
                return
            target = self._land_target(tl, keep=keep)
            if target is None:
                focus_primary_list(tl)
                self.refresh_bindings()
                return
            if not restore_cursor(tl, str(target.index), scroll=True):
                focus_primary_list(tl)
                self.refresh_bindings()
                return
            self._current_event = target
            self._paint_selected_event_detail()
            focus_primary_list(tl)
            self.refresh_bindings()

        self.call_after_refresh(lambda: self.call_after_refresh(_go))

    def action_timeline_down(self) -> None:
        """j — next event when the list is not the focused widget."""
        with suppress(Exception):
            self.query_one("#timeline-list", TimelineTable).action_cursor_down()

    def action_timeline_up(self) -> None:
        """k — previous event when the list is not the focused widget."""
        with suppress(Exception):
            self.query_one("#timeline-list", TimelineTable).action_cursor_up()

    def action_search(self) -> None:
        if self._active_browser_tab() == "tab-diff":
            view = self._diff_view()
            if view is not None:
                view.focus_search()
            return
        self._ensure_timeline_tab()

        def _focus_search() -> None:
            try:
                self.query_one("#search-input", Input).focus()
            except Exception:
                pass

        self.call_after_refresh(lambda: self.call_after_refresh(_focus_search))

    def action_clear_filters(self) -> None:
        self._timeline_search = ""
        try:
            self.query_one("#search-input", Input).value = ""
        except Exception:
            pass
        self._apply_timeline_mode("all")

    def action_show_findings(self) -> None:
        """Jump to Findings (same as tab 4 / ``i``)."""
        self.activate_tab_pane("tab-findings")

    def _timeline_event_actionable(self) -> bool:
        """True when Flag (etc.) should be enabled: Timeline pane + focused list + event."""
        if self._current_event is None:
            return False
        try:
            tabs = self.query_one("#browser-tabs", TabbedContent)
            if tabs.active != "tab-timeline":
                return False
        except Exception:
            return False
        try:
            tl = self.query_one("#timeline-list", TimelineTable)
            focused = self.app.focused
            if focused is None:
                return False
            if focused is tl:
                return True
            parent = getattr(focused, "parent", None)
            while parent is not None:
                if parent is tl:
                    return True
                parent = getattr(parent, "parent", None)
        except Exception:
            return False
        return False

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],  # Textual Screen.check_action
    ) -> bool | None:
        """Hide Flag in the footer/bindings unless a timeline event is selected+focused.

        Follow-up / Done actions use the cached pending flag from
        :meth:`_session_is_pending` — never re-scan ``events.jsonl`` here.
        """
        if action == "flag_event":
            return True if self._timeline_event_actionable() else False
        if action == "operator_note":
            # Always available once the browser is open (turn from event/filter/last).
            return True
        if action == "edit_operator_note":
            if not self._notes_loaded:
                return True
            return bool(self._notes_doc.notes)
        if action in ("send_follow_up", "mark_session_done", "focus_follow_up"):
            # O(1) cache; refreshed by pending bar / live poll / gate writes.
            if not self._pending_cache_valid:
                self._recompute_session_pending()
            return self._pending_actions_enabled
        if action in ("prev_turn", "next_turn"):
            focused = self.focused
            if isinstance(focused, (Input, Select)):
                return False
            if self._active_browser_tab() == "tab-diff":
                view = self._diff_view()
                return bool(view is not None and view.can_step_point())
            return self._turn_step_available()
        if action == "toggle_event_reader":
            focused = self.focused
            if isinstance(focused, (Input, Select)):
                return False
            if self._active_browser_tab() != "tab-timeline":
                return False
            return self._current_event is not None
        if action in ("timeline_down", "timeline_up"):
            focused = self.focused
            if isinstance(focused, (Input, Select)):
                return False
            if self._active_browser_tab() != "tab-timeline":
                return False
            with suppress(Exception):
                if focused is self.query_one("#timeline-list", TimelineTable):
                    return False
            return True
        return True

    def action_go_back(self) -> None:
        """Esc: leave the event page, then the browser."""
        from ..bindings import blur_focused_edit

        if blur_focused_edit(self):
            return
        if self._event_reader:
            self._set_event_reader(False)
            return
        self._leave_screen()

    def action_toggle_event_reader(self) -> None:
        """Enter: full-width event page, or open a spawn/finish child."""
        if self._active_browser_tab() != "tab-timeline":
            return
        ev = self._current_event
        if ev is None:
            return
        run = self._run_for_bookend_event(ev)
        if run is not None:
            self._open_subagent_run(run)
            return
        self._set_event_reader(not self._event_reader)

    def _set_event_reader(self, on: bool) -> None:
        self._event_reader = on
        with suppress(Exception):
            self.query_one("#browser-layout").set_class(on, "event-reader")
            if on:
                focus_primary_list(self.query_one("#timeline-list", TimelineTable))
            self.refresh_bindings()

    def action_copy_detail(self) -> None:
        """Copy selection, one finding, focused body, or the tab primary body.

        Textual owns the mouse, so OS drag-to-select does not work. Operators
        drag to select, then ``y`` / Ctrl+Shift+C (and Ctrl+C for a live
        selection). Priority:

        1. **Live selection** — exact selected plain (not stripped)
        2. **Findings tab** + highlighted finding → Issue box when extras
           support it, else export-style markdown for that finding
        3. focused :class:`SelectableStatic` body only (detail, one Report
           sub-pane, summary, …)
        4. tab primary body (Timeline detail, Summary, Diff hunk, Findings
           header) — never a silent join of every Report sibling pane

        On Report, Tab to a sub-pane then ``y`` yanks that body only.
        """
        text = ""
        kind = "none"
        selection_payload = False
        with suppress(Exception):
            selected = self.get_selected_text()
            if selected is not None and selected != "":
                text = selected
                kind = "selection"
                selection_payload = True
        # One finding: Findings table selection → Issue box (MF form) when present.
        if not selection_payload and self._active_browser_tab() == "tab-findings":
            finding = getattr(self, "_selected_finding", None)
            if isinstance(finding, Finding):
                text = self._finding_clipboard_text(finding).strip()
                if text:
                    kind = "finding"
        if not text and not selection_payload:
            with suppress(Exception):
                focused = self.focused
                if is_extractable_static(focused):
                    plain = focused.get_plain_text() or ""  # type: ignore[union-attr]
                    plain = plain.strip()
                    if plain:
                        text = plain
                        fid = str(getattr(focused, "id", "") or "")
                        if fid.startswith("report-"):
                            kind = "report"
                        elif fid == "detail-body":
                            kind = "detail"
                        else:
                            kind = "content"
        if not text and not selection_payload:
            text, kind = self._collect_active_tab_plain_text()
            text = (text or "").strip()
        if not text:
            self.notify(t("ui-nothing-to-copy"), severity="warning")
            return
        self.app.copy_to_clipboard(text)
        if kind == "selection":
            msg = t("ui-copied-selection")
        elif kind == "finding":
            msg = t("ui-copied-finding")
        elif kind == "report":
            msg = t("ui-copied-report")
        elif kind == "detail":
            msg = t("ui-copied-detail")
        else:
            msg = t("ui-copied-content")
        notify_copied = getattr(self.app, "notify_copied", None)
        if callable(notify_copied):
            notify_copied(msg)
        else:
            self.notify(msg, severity="information", timeout=2.0)

    def action_flag_event(self) -> None:
        """Open the flag modal for the currently selected timeline event."""
        if not self._timeline_event_actionable():
            return
        assert self._current_event is not None
        existing = self._flags.get(self._current_event.index)
        self.app.push_screen(
            FlagModal(self._current_event, existing_flag=existing), callback=self._on_flag_result
        )

    def action_operator_note(self) -> None:
        """Open modal to add a turn-linked operator note (schema fields)."""
        # Ensure disk notes are loaded before the modal (avoid empty-doc wipe).
        if not self._notes_loaded:
            self._load_notes()
        schema = load_schema()
        turn_options = self._note_turn_options()
        default_turn = self._default_note_turn_index()
        event_indices: list[int] = []
        if self._current_event is not None:
            event_indices = [self._current_event.index]
        self.app.push_screen(
            NotesModal(
                schema=schema,
                turn_options=turn_options,
                default_turn=default_turn,
                event_indices=event_indices,
            ),
            callback=self._on_note_result,
        )

    def action_edit_operator_note(self) -> None:
        """Edit or delete an existing turn-linked operator note."""
        if not self._notes_loaded:
            self._load_notes()
        notes = list(self._notes_doc.sorted_notes())
        if not notes:
            self.notify(U.note_none_to_edit())
            return
        if len(notes) == 1:
            self._open_edit_note_modal(notes[0])
            return
        self.app.push_screen(
            NotesPickModal(notes=notes),
            callback=self._on_note_pick_for_edit,
        )

    def _on_note_pick_for_edit(self, note: NoteEntry | None) -> None:
        """Open the edit modal after :class:`NotesPickModal` selection."""
        if note is None:
            return
        self._open_edit_note_modal(note)

    def _open_edit_note_modal(self, note: NoteEntry) -> None:
        """Push :class:`NotesModal` for an existing note."""
        schema = load_schema()
        self.app.push_screen(
            NotesModal(
                schema=schema,
                turn_options=self._note_turn_options(),
                default_turn=note.turn_index,
                event_indices=list(note.event_indices),
                existing=note,
            ),
            callback=self._on_note_result,
        )

    def _note_turn_options(self) -> list[tuple[str, str]]:
        """Turn select options for the notes modal (trace turn_number)."""
        from ...session.turns import display_turn_number

        segs = getattr(self, "_turn_segments", None) or []
        if not segs:
            ti = self._current_turn_index()
            return [(t("turn-filter-n", n=ti), str(ti))]
        out: list[tuple[str, str]] = []
        seen: set[int] = set()
        for seg in segs:
            if (n := display_turn_number(seg)) is None:
                continue
            if n in seen:
                continue
            seen.add(n)
            out.append((t("turn-filter-n", n=n), str(n)))
        return out

    def _on_note_result(
        self,
        result: tuple[str, NoteEntry | str] | None,
    ) -> None:
        """Handle save/delete from :class:`NotesModal` (reload-from-disk first)."""
        if result is None:
            return
        action, payload = result
        current = notes_snapshot(self.session_dir)
        current.doc.schema_id = load_schema().schema_id
        notify = ""
        try:
            if action == "save":
                if not isinstance(payload, NoteEntry):
                    return
                was_update = any(n.id == payload.id for n in current.doc.notes)
                self._persist_note_mutation(
                    "upsert",
                    note=payload,
                    expected_revision=current.revision,
                )
                notify = (
                    U.note_updated(payload.turn_index)
                    if was_update
                    else U.note_saved(payload.turn_index)
                )
            elif action == "delete":
                self._persist_note_mutation(
                    "delete",
                    note_id=str(payload),
                    expected_revision=current.revision,
                )
                notify = U.note_deleted()
            else:
                return
        except NotesConflict as exc:
            self.notify(U.note_save_failed(str(exc)), severity="error")
            return
        except OSError as exc:
            self.notify(U.note_save_failed(str(exc)), severity="error")
            return
        # Canonical store is on disk; re-read for UI.
        self._notes_doc = load_notes(self.session_dir)
        self._notes_loaded = True
        self.notify(notify)
        self._maybe_refresh_reports()

    def _persist_note_mutation(
        self,
        action: str,
        *,
        note: NoteEntry | None = None,
        note_id: str = "",
        expected_revision: str,
    ) -> None:
        """Persist a note mutation: control first (broadcast), else disk.

        Disk is always the last-resort success path so a flaky control socket
        never drops an operator note. Real revision conflicts still raise.
        """
        access = getattr(self.app, "session_access", lambda: None)()
        if access is not None:
            try:
                self._notes_mutate_via_control(
                    action,
                    note=note,
                    note_id=note_id,
                    expected_revision=expected_revision,
                )
                return
            except NotesConflict:
                raise
            except Exception as exc:
                logger.warning(
                    "control notes %s failed for %s; writing disk: %s",
                    action,
                    self.session_dir.name,
                    exc,
                )
        if action == "upsert":
            if note is None:
                raise RuntimeError("note required")
            upsert_note(
                self.session_dir,
                note,
                expected_revision=expected_revision,
            )
            return
        delete_note(
            self.session_dir,
            note_id,
            expected_revision=expected_revision,
        )

    def _notes_mutate_via_control(
        self,
        action: str,
        *,
        note: NoteEntry | None = None,
        note_id: str = "",
        expected_revision: str,
    ) -> None:
        """Upsert/delete notes through the control owner (shared revision).

        Runs the async client on a worker thread so Textual's running event
        loop is not nested with ``asyncio.run``.
        """
        import asyncio
        import concurrent.futures

        from ...integrations.control import ControlError

        access = getattr(self.app, "session_access", lambda: None)()
        if access is None:
            raise RuntimeError("control session access unavailable")
        # Prefer absolute path: name-only resolve can miss host/fallback trees.
        sid = str(self.session_dir)

        async def _run() -> None:
            if action == "upsert":
                if note is None:
                    raise RuntimeError("note required")
                body: dict = {
                    "id": note.id,
                    "turnIndex": note.turn_index,
                    "fields": dict(note.fields),
                    "eventIndices": list(note.event_indices),
                }
                if note.created_at:
                    body["createdAt"] = note.created_at
                if note.updated_at:
                    body["updatedAt"] = note.updated_at
                await access.notes_upsert(sid, body, expected_revision=expected_revision)
            else:
                await access.notes_delete(sid, note_id, expected_revision=expected_revision)

        def _thread_main() -> None:
            asyncio.run(_run())

        try:
            asyncio.get_running_loop()
            has_loop = True
        except RuntimeError:
            has_loop = False

        try:
            if has_loop:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(_thread_main)
                    fut.result(timeout=60)
            else:
                _thread_main()
        except ControlError as exc:
            if exc.code == 409:
                rev = ""
                if isinstance(exc.data, dict):
                    rev = str(exc.data.get("currentRevision") or "")
                raise NotesConflict(rev) from exc
            raise RuntimeError(exc.message) from exc
        except concurrent.futures.TimeoutError as exc:
            raise RuntimeError("control notes request timed out") from exc

    def _refresh_event_chrome(self) -> None:
        """Re-paint timeline Flags column + detail for the current event."""
        try:
            tl = self.query_one("#timeline-list", TimelineTable)
            tl.load_events(
                self.timeline,
                self._findings,
                list(self._flags.values()),
                follow_tail=self._timeline_follow_tail(),
            )
            if self._timeline_filters_active():
                self._reapply_timeline_view_filter()
        except Exception:
            pass
        ev = self._current_event
        if ev is None:
            return
        try:
            detail = self.query_one("#detail-panel", DetailView)
            timeline_table = self.query_one("#timeline-list", TimelineTable)
            finding = self._findings_by_call.get(ev.tool_call_id)
            duration = timeline_table.durations.get(ev.index)
            detail.show_event(
                ev,
                finding,
                self._flags.get(ev.index),
                duration=duration,
                paired_call=timeline_table.get_paired_call(ev),
                paired_result=timeline_table.get_paired_result(ev),
                turn_index=timeline_table.turn_index_for(ev.index),
                subagent_run=self._run_for_bookend_event(ev),
                job_mate=timeline_table.job_mate(ev),
                schedule=schedule_for_event(ev, self._session_jobs.schedules),
                workflow=workflow_for_event(
                    ev,
                    self._session_jobs.workflows,
                    mate=timeline_table.get_paired_result(ev) or timeline_table.get_paired_call(ev),
                ),
            )
        except Exception:
            pass

    def _on_flag_result(self, result: tuple | None) -> None:
        """Handle save/delete from the FlagModal."""
        if result is None:
            return
        action, payload = result
        if action == "save":
            flag = payload
            self._flags[flag.event_index] = flag
            self.notify(U.flag_saved(flag.event_index))
        elif action == "delete":
            event_index = payload
            self._flags.pop(event_index, None)
            self.notify(U.flag_removed(event_index))
        save_flags(self.session_dir, list(self._flags.values()))
        self._refresh_event_chrome()
        self._maybe_refresh_reports()

    def _format_finding_issue_box(self, finding: Finding) -> str | None:
        """MF form \"Issue (copy into the Issue box)\" when extras support it.

        Prefers a pre-rendered ``extras[\"issue_box\"]`` from the analyzer
        (full question layout). Falls back to structured What / Where / Why /
        Should have / Pattern fields. Returns None when those are absent.
        """
        extras = finding.extras or {}
        pre = str(extras.get("issue_box") or "").strip()
        if pre:
            return pre if pre.endswith("\n") else pre + "\n"
        what = str(extras.get("what_model_did") or "").strip()
        should = str(extras.get("what_should_have_done") or extras.get("should_have") or "").strip()
        why = str(extras.get("why_mistake") or "").strip()
        where = str(extras.get("where") or "").strip()
        pattern = str(extras.get("pattern") or "").strip()
        if not (what or should or why or pattern):
            return None
        if not where and finding.event_indices:
            where = "Timeline events " + ", ".join(f"#{i}" for i in finding.event_indices[:8])
            if finding.tool_call_ids:
                where += " · tools " + ", ".join(f"`{t}`" for t in finding.tool_call_ids[:4])
        return (
            f"What: {what or finding.title or '(see title)'}\n"
            f"Where: {where or '(see evidence)'}\n"
            f"Why: {why or '(not specified)'}\n"
            f"Should have: {should or '(not specified)'}\n"
            f"Pattern: {pattern or '(none)'}\n"
        )

    def _finding_clipboard_text(self, finding: Finding) -> str:
        """Best plain text for ``y``: Issue box when available, else export markdown."""
        box = self._format_finding_issue_box(finding)
        if box:
            return box
        return self._finding_plain_text(finding)

    def _finding_plain_text(self, finding: Finding) -> str:
        """Markdown-ish plain text for one finding (export file + generic clipboard)."""
        model = self.meta.model_display if self.meta else "unknown"
        session_id = self.meta.session_id if self.meta else "unknown"
        lines = [
            t("report-md-model", model=model),
            t("report-md-session", id=session_id),
            t("report-md-plugin", id=finding.plugin_id),
            t("report-md-finding", id=finding.id),
            t("report-md-severity", sev=finding.severity.value.upper()),
            t("report-md-category", cat=finding.category),
            "",
            f"**{finding.title}**",
        ]
        if finding.detail:
            lines.append("")
            for dl in finding.detail.strip().splitlines():
                lines.append(f"> {dl}")
        if finding.children:
            lines.append("")
            lines.append(t("report-md-sub-findings", n=len(finding.children)))
            for child in finding.children:
                lines.append(f"> [{child.severity.value.upper()}] `{child.id}`: {child.title[:80]}")
        should = finding.extras.get("what_should_have_done") or finding.extras.get("should_have")
        if should:
            lines.append("")
            lines.append(t("ui-what-the-model-should-have-done"))
            lines.append(f"> {should}")
        return "\n".join(lines).rstrip() + "\n"

    def _report_finding(self, finding: Finding) -> None:
        """Write a markdown report file for *finding* under ``~/.groket/reports``."""
        filename = f"finding-{finding.plugin_id}-{finding.id}"
        report_text = self._finding_plain_text(finding)
        try:
            reports_dir = self._reports_dir()
            reports_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            report_file = reports_dir / f"{filename}-{ts}.md"
            with open(report_file, "w") as f:
                f.write(report_text)
            self.notify(U.report_saved(str(report_file)), severity="information")
        except Exception as exc:
            self.notify(U.report_failed(str(exc)), severity="error")

    def action_delete_session(self) -> None:
        """Double-press ``x`` deletes this session from disk and leaves the browser."""
        from ..delete_confirm import second_press_armed

        key = str(self.session_dir)
        pending = [key] if self._delete_pending else []
        commit, _pending = second_press_armed(pending, [key])
        if not commit:
            self._delete_pending = True
            self.notify(
                t("notify-delete-session-arm"),
                severity="warning",
                timeout=10,
            )
            return
        self._delete_pending = False
        self._stop_live_refresh()
        from ...runs.run_configs import delete_session_dirs, session_dirs_for_delete

        paths = session_dirs_for_delete([self.session_dir])
        traces_root = getattr(self.app, "traces_path", None)
        stats = delete_session_dirs(paths, traces_root=traces_root, prune_empty_parents=True)
        gone = {str(p) for p in paths}
        app = self.app
        # Drop from home-screen caches while we still hold the app ref.
        meta_only = getattr(app, "_meta_only", None)
        if isinstance(meta_only, list):
            setattr(
                app,
                "_meta_only",
                [(m, lab) for m, lab in meta_only if str(m.session_dir) not in gone],
            )
        selected = getattr(app, "_selected", None)
        if isinstance(selected, set):
            selected -= gone
        plugin_results = getattr(app, "_plugin_results", None)
        if isinstance(plugin_results, dict):
            for k in list(plugin_results):
                if k in gone:
                    del plugin_results[k]
        err_n = 0
        errors_raw = stats.get("errors")
        if isinstance(errors_raw, list):
            err_n = len(errors_raw)
        err_suffix = t("notify-deleted-sessions-errors", n=err_n) if err_n else ""
        self.notify(
            t(
                "notify-deleted-sessions",
                deleted=stats.get("deleted", 0),
                requested=stats.get("requested", 0),
                err_suffix=err_suffix,
            ),
            severity="warning" if err_n else "information",
            timeout=10,
        )
        self.app.pop_screen()
        populate = getattr(app, "_populate_session_table", None)
        if callable(populate):
            with suppress(Exception):
                populate()

    def action_export_finding(self) -> None:
        """Export the selected finding to a markdown file (command palette)."""
        tabbed = self.query_one(TabbedContent)
        if tabbed.active != "tab-findings" or self._selected_finding is None:
            self.notify(U.select_finding_first(), severity="warning")
            return
        self._report_finding(self._selected_finding)

    def action_export_bundle(self) -> None:
        """Export this session: configured profile, or ask if none is set."""
        from ..export_session import start_export_smart

        start_export_smart(self, self.session_dir)

    def action_export_choose_profile(self) -> None:
        """Palette: pick a profile for this export only (does not change default)."""
        from ..export_session import start_export_with_profile_picker

        start_export_with_profile_picker(self, self.session_dir, remember_as_default=False)
