"""Detail view widget for displaying event details (Rich/Markdown/Syntax)."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import DataTable

from ...analysis.base import Finding
from ...models import Flag, TraceEvent
from ...session.jobs import ScheduleTask
from ...session.subagents import SubagentRun
from ...session.workflows import WorkflowChild, WorkflowRun
from ..data_table import ListDataTable, style_data_table
from ..i18n import t
from ..render_detail import render_event_detail, render_workflow_detail, set_static_renderable
from ..selectable_static import SelectableStatic, plain_from_renderable


class DetailView(VerticalScroll):
    """Shows detailed information about a selected trace event.

    Uses a single :class:`SelectableStatic` child whose content is replaced on
    each selection, avoiding the remove_children/mount race that causes
    'NoneType' render_strips errors in Textual. SelectableStatic enables mouse
    text selection and plain-text clipboard yank for Markdown/Syntax bodies.
    """

    class FlagRequested(Message):
        def __init__(self, event: TraceEvent) -> None:
            super().__init__()
            self.event = event

    class ChildActivated(Message):
        """Operator activated a workflow child row."""

        def __init__(self, child: WorkflowChild) -> None:
            super().__init__()
            self.child = child

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_event: TraceEvent | None = None
        self._current_finding: Finding | None = None
        self._current_flag: Flag | None = None
        self._current_duration: float | None = None
        self._paired_call: TraceEvent | None = None
        self._paired_result: TraceEvent | None = None
        self._current_turn_index: int | None = None
        self._subagent_run: SubagentRun | None = None
        self._job_mate: TraceEvent | None = None
        self._schedule: ScheduleTask | None = None
        self._workflow: WorkflowRun | None = None
        self.session_dir: Path | None = None

    def compose(self) -> ComposeResult:
        yield SelectableStatic("", id="detail-body")
        yield ListDataTable(id="workflow-children-table")

    def show_event(
        self,
        event: TraceEvent,
        finding: Finding | None = None,
        flag: Flag | None = None,
        duration: float | None = None,
        *,
        paired_call: TraceEvent | None = None,
        paired_result: TraceEvent | None = None,
        turn_index: int | None = None,
        subagent_run: SubagentRun | None = None,
        job_mate: TraceEvent | None = None,
        schedule: ScheduleTask | None = None,
        workflow: WorkflowRun | None = None,
    ) -> None:
        same_event = self._current_event is not None and int(self._current_event.index) == int(
            event.index
        )
        self._current_event = event
        self._current_finding = finding
        self._current_flag = flag
        self._current_duration = duration
        self._paired_call = paired_call
        self._paired_result = paired_result
        self._current_turn_index = turn_index
        self._subagent_run = subagent_run
        self._job_mate = job_mate
        self._schedule = schedule
        self._workflow = workflow
        self._refresh_content(scroll_home=not same_event)
        self._sync_workflow_children()

    def show_workflow(self, run: WorkflowRun) -> None:
        """Inspect a Summary row when no Timeline bookend can be paired."""
        self._current_event = None
        self._current_finding = None
        self._current_flag = None
        self._current_duration = None
        self._paired_call = None
        self._paired_result = None
        self._current_turn_index = None
        self._subagent_run = None
        self._job_mate = None
        self._schedule = None
        self._workflow = run
        body = self.query_one("#detail-body", SelectableStatic)
        set_static_renderable(body, render_workflow_detail(run))
        self._sync_workflow_children()
        self.scroll_home(animate=False)

    def _refresh_content(self, *, scroll_home: bool = True) -> None:
        ev = self._current_event
        body = self.query_one("#detail-body", SelectableStatic)
        if ev is None:
            if self._workflow is not None:
                set_static_renderable(body, render_workflow_detail(self._workflow))
                self._sync_workflow_children()
                return
            body.update("")
            self._sync_workflow_children()
            return
        renderable = render_event_detail(
            ev,
            finding=self._current_finding,
            flag=self._current_flag,
            duration=self._current_duration,
            paired_call=self._paired_call,
            paired_result=self._paired_result,
            turn_index=self._current_turn_index,
            subagent_run=self._subagent_run,
            job_mate=self._job_mate,
            schedule=self._schedule,
            workflow=self._workflow,
            session_dir=self.session_dir,
        )
        set_static_renderable(body, renderable)
        self._sync_workflow_children()
        if scroll_home:
            self.scroll_home(animate=False)

    def on_mount(self) -> None:
        table = self.query_one("#workflow-children-table", DataTable)
        style_data_table(table)
        table.add_columns(t("ui-agents"), t("col-status"))
        table.display = False

    def _sync_workflow_children(self) -> None:
        try:
            table = self.query_one("#workflow-children-table", DataTable)
        except Exception:
            return
        run = self._workflow
        kids = list(run.children) if run is not None else []
        table.clear()
        if not kids:
            table.display = False
            return
        table.display = True
        for i, child in enumerate(kids):
            mark = t("status-complete") if child.success else t("ui-status-failed")
            table.add_row(child.label or child.agent_id, mark, key=f"wfchild-{i}")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "workflow-children-table":
            return
        raw = str(event.row_key.value) if event.row_key is not None else ""
        if not raw.startswith("wfchild-"):
            return
        try:
            idx = int(raw.split("-", 1)[1])
        except ValueError:
            return
        run = self._workflow
        if run is None or not (0 <= idx < len(run.children)):
            return
        self.post_message(self.ChildActivated(run.children[idx]))

    def clear_detail(self) -> None:
        self._current_event = None
        self._current_finding = None
        self._current_flag = None
        self._current_duration = None
        self._paired_call = None
        self._paired_result = None
        self._current_turn_index = None
        self._subagent_run = None
        self._job_mate = None
        self._schedule = None
        self._workflow = None
        self.query_one("#detail-body", SelectableStatic).update("")
        self._sync_workflow_children()

    def get_plain_text(self) -> str:
        """Plain text of the current detail body (for clipboard yank).

        Rebuilds the event without display mid-caps so ``y`` is not limited to
        the truncated on-screen tool/message bodies. Falls back to the widget
        full plain cache when no event is loaded.
        """
        ev = self._current_event
        if ev is None and self._workflow is not None:
            return plain_from_renderable(render_workflow_detail(self._workflow), full=True)
        if ev is not None:
            renderable = render_event_detail(
                ev,
                finding=self._current_finding,
                flag=self._current_flag,
                duration=self._current_duration,
                paired_call=self._paired_call,
                paired_result=self._paired_result,
                turn_index=self._current_turn_index,
                subagent_run=self._subagent_run,
                job_mate=self._job_mate,
                schedule=self._schedule,
                workflow=self._workflow,
                truncate=False,
            )
            return plain_from_renderable(renderable, full=True)
        try:
            body = self.query_one("#detail-body", SelectableStatic)
        except Exception:
            return ""
        return body.get_plain_text()
