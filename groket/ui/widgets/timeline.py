"""Timeline widget showing trace events in a scrollable list."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from rich.markup import escape as rich_escape
from textual.message import Message
from textual.widgets import DataTable

from ... import event_types as et
from ...analysis.base import Finding
from ...constants import LIVE_TIMELINE_TAIL_CHECK
from ...models import Flag, ToolInputBag, TraceEvent
from ...session.jobs import event_job_kind, event_task_id
from ...session.subagents import (
    event_child_session_id,
    subagent_duration_seconds,
    subagent_inspect,
    subagent_list_preview,
)
from ...session.turns import event_matches_timeline_kind
from ...session.workflows import workflow_list_preview
from ...tool_display import job_list_preview, list_event_preview
from ...utils import fmt_duration
from ..data_table import (
    cursor_row_key,
    preserving_cursor,
    restore_cursor,
    style_data_table,
    update_row_cell,
)
from ..i18n import t
from ..styles import (
    EVENT_TYPE_STYLE,
    EVENT_TYPE_STYLE_LIGHT,
    active_theme_is_light,
    event_type_markup,
    finding_mark,
)
from ..styles import tool_label as tool_markup


@dataclass
class _ViewFilter:
    """Last exclusive View / Turn / search applied to this table."""

    event_type: str | None = None
    event_types: frozenset[str] | None = None
    tool_name: str | None = None
    errors_only: bool = False
    flagged_only: bool = False
    search_query: str = ""
    call_ids: frozenset[str] | None = None
    update_indices: frozenset[int] | None = None
    event_indices: frozenset[int] | None = None
    kind: str | None = None

    def restricts(self) -> bool:
        return bool(
            self.event_type
            or self.event_types
            or self.tool_name
            or self.errors_only
            or self.flagged_only
            or self.search_query.strip()
            or self.call_ids
            or self.update_indices
            or self.event_indices
            or (self.kind and self.kind not in {"", "all"})
        )


class TimelineTable(DataTable):
    """DataTable specialized for trace event timelines."""

    class EventSelected(Message):
        def __init__(self, event: TraceEvent) -> None:
            super().__init__()
            self.event = event

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.events: list[TraceEvent] = []
        self.findings_by_call: dict[str, Finding] = {}
        self.flags_by_index: dict[int, Flag] = {}
        self._durations: dict[int, float] = {}
        self._call_by_id: dict[str, TraceEvent] = {}
        self._result_by_id: dict[str, TraceEvent] = {}
        self._turn_by_index: dict[int, int] = {}
        self._turn_map_stale: bool = True
        self._subagent_mate: dict[int, TraceEvent] = {}
        self._job_mate: dict[int, TraceEvent] = {}
        self._visible: list[TraceEvent] | None = None
        self._filter_spec: _ViewFilter | None = None

    @property
    def durations(self) -> dict[int, float]:
        """Computed per-event durations (event index -> seconds)."""
        return self._durations

    def on_mount(self) -> None:
        style_data_table(self)
        self.add_columns(
            t("col-index"),
            t("col-turn"),
            t("col-time"),
            t("col-dur"),
            t("col-type"),
            t("col-tool"),
            t("col-summary"),
        )

    def load_events(
        self,
        events: list[TraceEvent],
        findings: list[Finding] | None = None,
        flags: list[Flag] | None = None,
        *,
        follow_tail: bool = False,
    ) -> None:
        """Load timeline rows.

        Live refresh paths (cheapest first):

        1. **Same-length** — structure unchanged. Rebind tool pairs. Patch
           flag/finding chrome when those maps change. Streaming body text
           is not rewritten (that froze the TUI).
        2. **Append** — previous events are a structural prefix. Add only
           new rows that pass the current View/Turn/search filter.
        3. **Full rebuild** — order/identity changed.

        ``self.events`` is always the full list. ``_visible`` is the filtered
        paint set (or None when every event is shown).
        """
        prev = self.events
        new_events = events or []
        prev_n = len(prev)
        new_n = len(new_events)
        painted_n = len(self._paint_list())
        row_ok = self.row_count == painted_n and painted_n > 0
        old_flags = set(self.flags_by_index)
        old_finds = set(self.findings_by_call)

        self.events = new_events
        self._set_chrome(findings, flags)
        new_visible = self._compute_visible(new_events)

        if not row_ok or not prev:
            self._build_tool_pairs()
            self._compute_durations()
            self._rebuild_turn_map()
            self._visible = new_visible
            self._refresh_rows()
            if follow_tail:
                self.scroll_to_end()
            return

        if new_n >= prev_n and self._live_tail_struct_ok(prev, new_events):
            if new_n == prev_n:
                self._build_tool_pairs()
                if old_flags != set(self.flags_by_index) or old_finds != set(self.findings_by_call):
                    self._patch_chrome_rows(old_flags, old_finds)
                self._visible = new_visible
                return
            self._index_new_events(new_events[prev_n:])
            self._extend_turn_map_from(prev_n)
            added = new_events[prev_n:]
            if self._filter_spec is not None and self._filter_spec.restricts():
                old_keys = {int(e.index) for e in (self._visible or [])}
                self._visible = new_visible
                for ev in self._visible or []:
                    if int(ev.index) not in old_keys:
                        self._add_event_row(ev)
            else:
                self._visible = None
                self._append_live_rows(added, follow_tail=follow_tail)
                self._patch_paired_call_durations(added)
            if old_flags != set(self.flags_by_index) or old_finds != set(self.findings_by_call):
                self._patch_chrome_rows(old_flags, old_finds)
            return

        self._build_tool_pairs()
        self._compute_durations()
        self._rebuild_turn_map()
        self._visible = new_visible
        self._refresh_rows()
        if follow_tail:
            self.scroll_to_end()

    @staticmethod
    def _live_tail_struct_ok(prev: list[TraceEvent], new: list[TraceEvent]) -> bool:
        """True when the live tail of *prev* is a structural prefix of *new*."""
        prev_n = len(prev)
        if len(new) < prev_n or prev_n == 0:
            return False
        tail = min(LIVE_TIMELINE_TAIL_CHECK, prev_n)
        start = prev_n - tail
        for i in range(start, prev_n):
            a, b = prev[i], new[i]
            if a.index != b.index or a.event_type != b.event_type:
                return False
            if a.tool_call_id != b.tool_call_id:
                return False
        return True

    def _set_chrome(self, findings: list[Finding] | None, flags: list[Flag] | None) -> None:
        self.findings_by_call = {}
        if findings:
            for f in findings:
                for cid in f.all_tool_call_ids:
                    self.findings_by_call[cid] = f
        self.flags_by_index = {}
        if flags:
            for fl in flags:
                self.flags_by_index[fl.event_index] = fl

    def _paint_list(self) -> list[TraceEvent]:
        if self._visible is not None:
            return self._visible
        return self.events

    def visible_events(self) -> list[TraceEvent]:
        """Events currently painted (the filtered set, or the full list)."""
        return list(self._paint_list())

    def _patch_chrome_rows(self, old_flags: set[int], old_finds: set[str]) -> None:
        idxs = set(old_flags) | set(self.flags_by_index)
        call_ids = old_finds | set(self.findings_by_call)
        if call_ids:
            for row in self.events:
                if row.tool_call_id and row.tool_call_id in call_ids:
                    idxs.add(int(row.index))
        by_idx = {int(e.index): e for e in self.events}
        for i in idxs:
            hit = by_idx.get(i)
            if hit is not None:
                self._update_event_row(hit)

    def _compute_visible(self, events: list[TraceEvent]) -> list[TraceEvent] | None:
        spec = self._filter_spec
        if spec is None or not spec.restricts():
            return None
        out = [ev for ev in events if self._event_matches_spec(ev, spec)]
        return out

    def _event_matches_spec(self, ev: TraceEvent, spec: _ViewFilter) -> bool:
        if spec.kind and spec.kind not in {"", "all"}:
            if not event_matches_timeline_kind(ev, spec.kind):
                return False
        if spec.event_type and ev.event_type != spec.event_type:
            return False
        if spec.event_types is not None and ev.event_type not in spec.event_types:
            return False
        if spec.tool_name and ev.tool_name != spec.tool_name:
            return False
        if spec.errors_only and not (ev.is_error or ev.event_type in et.ERROR_TYPES):
            return False
        if spec.flagged_only and ev.index not in self.flags_by_index:
            return False
        if spec.search_query:
            q = spec.search_query.lower()
            hay = f"{ev.content} {ev.tool_name} {ev.raw_input}".lower()
            if q not in hay:
                return False
        if spec.call_ids or spec.update_indices or spec.event_indices:
            if spec.call_ids and ev.tool_call_id in spec.call_ids:
                return True
            if spec.update_indices and ev.update_index in spec.update_indices:
                return True
            if spec.event_indices and ev.index in spec.event_indices:
                return True
            return False
        return True

    def scroll_to_end(self) -> None:
        """Put the cursor on the last row and scroll it into view."""
        if self.row_count <= 0:
            return
        with suppress(Exception):
            self.move_cursor(row=self.row_count - 1, animate=False, scroll=True)

    def _append_live_rows(self, new_events: list[TraceEvent], *, follow_tail: bool) -> None:
        """Append rows; keep highlight/scroll still unless Tail is on."""
        if follow_tail:
            self._append_rows(new_events)
            self.scroll_to_end()
            return
        key = cursor_row_key(self)
        x = getattr(self, "scroll_x", 0)
        y = getattr(self, "scroll_y", 0)
        self._append_rows(new_events)
        if key:
            restore_cursor(self, key, scroll=False)
        with suppress(Exception):
            self.scroll_to(x, y, animate=False)

    def _append_rows(self, new_events: list[TraceEvent]) -> None:
        """Add only *new_events* (already assigned into ``self.events``)."""
        for ev in new_events:
            self._add_event_row(ev)

    def _row_key_exists(self, key: str) -> bool:
        """True when *key* is already a row key in this table."""
        try:
            return key in self.rows
        except Exception:
            return False

    def _patch_paired_call_durations(self, events: list[TraceEvent]) -> None:
        """When a tool_result updates, refresh the earlier tool_call duration cell."""
        seen: set[int] = set()
        for ev in events:
            if ev.event_type not in et.TOOL_UPDATE_TYPES or not ev.tool_call_id:
                continue
            call = self._call_by_id.get(ev.tool_call_id)
            if call is None or call.index in seen:
                continue
            seen.add(call.index)
            self._update_event_row(call)

    def _index_new_events(self, new_events: list[TraceEvent]) -> None:
        """Update tool pairs + durations for appended/patched events (live path)."""
        for ev in new_events:
            if not ev.tool_call_id:
                continue
            if ev.event_type == "tool_call":
                self._call_by_id[ev.tool_call_id] = ev
            elif ev.event_type in et.TOOL_UPDATE_TYPES:
                self._result_by_id[ev.tool_call_id] = ev
        # Durations: tool_call ↔ result, or next-event gap for new non-tool rows.
        for ev in new_events:
            if ev.timestamp is None:
                continue
            if ev.event_type == "tool_call" and ev.tool_call_id in self._result_by_id:
                res = self._result_by_id[ev.tool_call_id]
                if res.timestamp is not None and res.timestamp >= ev.timestamp:
                    self._durations[ev.index] = res.timestamp - ev.timestamp
            elif ev.event_type in et.TOOL_UPDATE_TYPES and ev.tool_call_id:
                call = self._call_by_id.get(ev.tool_call_id)
                if (
                    call is not None
                    and call.timestamp is not None
                    and ev.timestamp >= call.timestamp
                ):
                    self._durations[call.index] = ev.timestamp - call.timestamp
        self._index_subagent_mates()
        self._index_job_mates()
        self._apply_subagent_run_durations()

    def _build_tool_pairs(self) -> None:
        """Index tool_call / tool_result by call_id (trace_viewer merges these)."""
        self._call_by_id = {}
        self._result_by_id = {}
        for ev in self.events:
            if not ev.tool_call_id:
                continue
            if ev.event_type == "tool_call":
                self._call_by_id[ev.tool_call_id] = ev
            elif ev.event_type in et.TOOL_UPDATE_TYPES:
                self._result_by_id[ev.tool_call_id] = ev

    def get_paired_call(self, ev: TraceEvent) -> TraceEvent | None:
        if ev.event_type not in et.TOOL_UPDATE_TYPES or not ev.tool_call_id:
            return None
        call = self._call_by_id.get(ev.tool_call_id)
        if call is not None and self.events and id(call) not in {id(e) for e in self.events}:
            self._build_tool_pairs()
            call = self._call_by_id.get(ev.tool_call_id)
        return call

    def get_paired_result(self, ev: TraceEvent) -> TraceEvent | None:
        """Return the tool_call_update for a tool_call (file body lives here).

        ``read_file`` and similar host tools leave ``tool_call.content`` empty;
        the dump is only on the paired update. Maps must track *current*
        timeline objects after re-parse, not stale instances.
        """
        if ev.event_type != "tool_call" or not ev.tool_call_id:
            return None
        cid = ev.tool_call_id
        res = self._result_by_id.get(cid)
        live = self.events
        if live and (res is None or id(res) not in {id(e) for e in live}):
            self._build_tool_pairs()
            res = self._result_by_id.get(cid)
        return res

    def _compute_durations(self) -> None:
        """Compute per-event durations from timestamps.

        For tool_call events, duration = time until the matching tool_result.
        For other events, duration = time until the next event.
        """
        self._durations = {}
        if not self.events:
            return
        result_ts: dict[str, int] = {}
        for ev in self.events:
            if ev.event_type in et.TOOL_UPDATE_TYPES and ev.tool_call_id and ev.timestamp:
                result_ts[ev.tool_call_id] = ev.timestamp
        for i, ev in enumerate(self.events):
            if ev.timestamp is None:
                continue
            if ev.event_type == "tool_call" and ev.tool_call_id in result_ts:
                dur = result_ts[ev.tool_call_id] - ev.timestamp
                if dur >= 0:
                    self._durations[ev.index] = dur
            elif ev.event_type in et.TOOL_UPDATE_TYPES:
                continue
            else:
                ev_ts = ev.timestamp
                for j in range(i + 1, len(self.events)):
                    next_ts = self.events[j].timestamp
                    if next_ts is not None and ev_ts is not None:
                        dur = next_ts - ev_ts
                        if dur >= 0:
                            self._durations[ev.index] = dur
                        break
            own = subagent_duration_seconds(ev)
            if own is not None:
                self._durations[ev.index] = own
        self._index_subagent_mates()
        self._index_job_mates()
        self._apply_subagent_run_durations()

    def _index_subagent_mates(self) -> None:
        by_child: dict[str, list[TraceEvent]] = {}
        for ev in self.events:
            if ev.event_type not in et.SUBAGENT_TYPES:
                continue
            child = event_child_session_id(ev)
            if child:
                by_child.setdefault(child, []).append(ev)
        mates: dict[int, TraceEvent] = {}
        for group in by_child.values():
            spawn = next((e for e in group if e.event_type == "subagent_spawned"), None)
            finish = next((e for e in group if e.event_type == "subagent_finished"), None)
            if spawn is not None and finish is not None:
                mates[spawn.index] = finish
                mates[finish.index] = spawn
        self._subagent_mate = mates

    def _index_job_mates(self) -> None:
        by_id: dict[str, list[TraceEvent]] = {}
        for ev in self.events:
            if ev.event_type not in {"task_backgrounded", "task_completed"}:
                continue
            tid = event_task_id(ev)
            if tid:
                by_id.setdefault(tid, []).append(ev)
        mates: dict[int, TraceEvent] = {}
        for group in by_id.values():
            start = next((e for e in group if e.event_type == "task_backgrounded"), None)
            finish = next((e for e in group if e.event_type == "task_completed"), None)
            if start is not None and finish is not None:
                mates[start.index] = finish
                mates[finish.index] = start
        self._job_mate = mates

    def job_mate(self, ev: TraceEvent) -> TraceEvent | None:
        return self._job_mate.get(ev.index)

    def _apply_subagent_run_durations(self) -> None:
        for ev in self.events:
            if ev.event_type != "subagent_spawned":
                continue
            mate = self._subagent_mate.get(ev.index)
            if mate is None:
                continue
            own = subagent_duration_seconds(mate)
            if own is not None:
                self._durations[ev.index] = own

    @staticmethod
    def _fmt_dur(seconds: float) -> str:
        return fmt_duration(seconds)

    def _tool_column(self, ev: TraceEvent) -> str:
        """Tool / runtime label — same family palette as ``tool_label`` (not per-tool rainbow)."""
        if (ev.tool_name or "") == "workflow":
            # Type already carries the honest label; do not repeat it here.
            return ""
        if ev.event_type in et.TOOL_TYPES and ev.tool_name:
            return tool_markup(ev.tool_name, light=active_theme_is_light())
        if ev.event_type in et.ERROR_TYPES:
            return t("ui-session-error-1")
        if ev.event_type in et.TURN_BOUNDARY_TYPES:
            label = ev.type_label
            c = (ev.content or "").lower()
            if t("ui-turn-ended") in c:
                label = t("ui-turn-ended")
            elif t("ui-turn-started") in c:
                label = t("ui-turn-started")
            return f"[yellow]{label}[/]"
        if ev.event_type in et.SUBAGENT_TYPES:
            mate = self._subagent_mate.get(ev.index)
            info = subagent_inspect(ev, mate=mate)
            if info.kind:
                return f"[cyan]{rich_escape(info.kind)}[/]"
            return ""
        if ev.event_type in et.TASK_TYPES or ev.event_type.startswith("scheduled_task_"):
            # Type already carries the honest label; do not repeat it here.
            return ""
        if ev.event_type in (et.MESSAGE_TYPES | et.PLAN_TYPES):
            return ""
        return ""

    def _rebuild_turn_map(self) -> None:
        """Map each loaded event index to its enclosing trace turn id."""
        from ...session.turns import event_display_turn_map, segment_timeline_turns

        if not self.events:
            self._turn_by_index = {}
            self._turn_map_stale = False
            return
        self._turn_by_index = event_display_turn_map(segment_timeline_turns(self.events))
        self._turn_map_stale = False

    def _extend_turn_map_from(self, start_offset: int) -> None:
        """Assign turn ids for a live-appended tail without full resegment.

        Most live growth is tools/agent stream inside the open turn — inherit
        the previous event's turn. Boundary markers (turn_started/ended or a
        new operator user message) trigger one full :func:`segment_timeline_turns`.
        """
        if self._turn_map_stale or not self._turn_by_index:
            self._rebuild_turn_map()
            return
        if start_offset <= 0 or start_offset >= len(self.events):
            return
        from ...session.turns import is_harness_user_chrome, is_session_level_timeline_event

        prev = self.events[start_offset - 1]
        cur = self._turn_by_index.get(int(prev.index))
        if cur is None:
            self._rebuild_turn_map()
            return
        tail = self.events[start_offset:]
        for ev in tail:
            if is_session_level_timeline_event(ev):
                continue
            etype = ev.event_type or ""
            head = (ev.content or "")[:48].lower()
            if etype in et.TURN_BOUNDARY_TYPES or "turn started" in head or "turn ended" in head:
                self._rebuild_turn_map()
                return
            if etype in et.USER_TYPES and not is_harness_user_chrome(ev.content or ""):
                self._rebuild_turn_map()
                return
        for ev in tail:
            if is_session_level_timeline_event(ev):
                continue
            self._turn_by_index[int(ev.index)] = int(cur)

    def turn_index_for(self, event_index: int) -> int | None:
        """Trace turn id for *event_index*, if the event is in a turn.

        Does **not** re-segment on a cold/stale map during selection — that made
        every live tick + arrow-key pay a full ``segment_timeline_turns``. The
        map is built on open/rebuild and extended on append.
        """
        if self._turn_map_stale:
            return None
        return self._turn_by_index.get(int(event_index))

    def _row_cell_values(self, ev: TraceEvent) -> tuple[str, str, str, str, str, str, str]:
        """Visible cell values for one event (Index, Turn, Time, Dur, Type, Tool, Summary)."""
        from ...session.turns import harness_user_chrome_heading

        light = active_theme_is_light()
        chrome_heading = harness_user_chrome_heading(ev.content or "")
        if chrome_heading is not None:
            # Harness injects system-reminder / background-task as user_message_chunk.
            type_style = f"[bold magenta]{chrome_heading.lower()}[/]"
        else:
            honest = et.job_event_label(ev.event_type, kind=event_job_kind(ev))
            if honest:
                faces = EVENT_TYPE_STYLE_LIGHT if light else EVENT_TYPE_STYLE
                face = faces.get(ev.event_type, "yellow")
                type_style = f"[{face}]{honest}[/]"
            elif (ev.tool_name or "") == "workflow":
                faces = EVENT_TYPE_STYLE_LIGHT if light else EVENT_TYPE_STYLE
                face = faces.get("task_backgrounded", "yellow")
                label = (
                    t("ui-workflow-done")
                    if ev.event_type in et.TOOL_UPDATE_TYPES
                    else t("ui-workflow")
                )
                type_style = f"[{face}]{label}[/]"
            else:
                type_style = event_type_markup(ev.event_type, light=light) or ev.type_label
        tool_err = ev.is_error and ev.event_type not in et.SESSION_CHROME_TYPES
        if tool_err and chrome_heading is None:
            type_style = f"[red bold underline]{ev.type_label}[/]"
        elif ev.event_type in et.ERROR_TYPES and chrome_heading is None:
            type_style = f"[red bold underline]{ev.type_label}[/]"
        dur_str = ""
        if ev.index in self._durations:
            dur = self._durations[ev.index]
            dur_str = self._fmt_dur(dur)
            if dur >= 60:
                dur_str = f"[red bold]{dur_str}[/]"
            elif dur >= 30:
                dur_str = f"[yellow]{dur_str}[/]"
        tool_col = self._tool_column(ev)
        if tool_err and tool_col and (not tool_col.startswith("[")):
            tool_col = f"[red]{tool_col}[/]"
        prefix = ""
        if ev.index in self.flags_by_index:
            prefix += "[magenta bold]⚑[/] "
        if ev.tool_call_id and ev.tool_call_id in self.findings_by_call:
            finding = self.findings_by_call[ev.tool_call_id]
            sev = getattr(finding.severity, "value", None) or "low"
            prefix += finding_mark(sev) + " "
        if ev.event_type in et.TASK_TYPES or ev.event_type.startswith("scheduled_task_"):
            bag = ev.raw_input.raw() if isinstance(ev.raw_input, ToolInputBag) else {}
            raw_sum = job_list_preview(ev.event_type, bag, ev.content)
        elif (ev.tool_name or "") == "workflow":
            bag = ev.raw_input.raw() if isinstance(ev.raw_input, ToolInputBag) else {}
            raw_sum = workflow_list_preview(bag) or ev.summary_line
        elif ev.event_type in et.SUBAGENT_TYPES:
            mate = self._subagent_mate.get(ev.index)
            info = subagent_inspect(ev, mate=mate)
            raw_sum = info.description or info.kind or info.status
            if not raw_sum:
                bag = ev.raw_input.raw() if isinstance(ev.raw_input, ToolInputBag) else {}
                raw_sum = subagent_list_preview(ev.event_type, bag, ev.content) or ev.summary_line
        else:
            raw_sum = ev.summary_line
        shown = (
            raw_sum
            if (ev.tool_name or "") == "workflow"
            else list_event_preview(raw_sum, ev.tool_name)
        )
        summary = prefix + rich_escape(shown[: 56 if prefix else 60])
        # Prefer the warm map (built on open / extended on append). Avoid
        # turn_index_for side effects during bulk paint.
        turn = self._turn_by_index.get(int(ev.index))
        turn_str = str(turn) if turn is not None else ""
        return (str(ev.index), turn_str, ev.time_str, dur_str, type_style, tool_col, summary)

    def _add_event_row(self, ev: TraceEvent) -> None:
        """Append one timeline row for *ev*.

        If the key already exists (table/self.events desync after filter, live
        append, or a failed partial rebuild), update in place instead of
        raising Textual ``DuplicateKey`` and crashing the app.
        """
        key = str(ev.index)
        if self._row_key_exists(key):
            self._update_event_row(ev)
            return
        cells = self._row_cell_values(ev)
        self.add_row(*cells, key=key)

    def _update_event_row(self, ev: TraceEvent) -> None:
        """Patch an existing row's cells in place (streaming live refresh)."""
        key = str(ev.index)
        if not self._row_key_exists(key):
            # Row missing (e.g. filtered view) — skip rather than inventing a row.
            return
        cells = self._row_cell_values(ev)
        for col_i, value in enumerate(cells):
            update_row_cell(self, key, col_i, value)

    def _refresh_rows(self) -> None:
        with preserving_cursor(self, scroll=False):
            self.clear()
            seen: set[int] = set()
            for ev in self._paint_list():
                ix = int(ev.index)
                if ix in seen:
                    continue
                seen.add(ix)
                self._add_event_row(ev)

    def apply_filter(
        self,
        event_type: str | None = None,
        event_types: set[str] | None = None,
        tool_name: str | None = None,
        errors_only: bool = False,
        flagged_only: bool = False,
        search_query: str = "",
        call_ids: set[str] | None = None,
        update_indices: set[int] | None = None,
        event_indices: set[int] | None = None,
        kind: str | None = None,
    ) -> None:
        """Re-filter the displayed events without replacing ``self.events``."""
        self._filter_spec = _ViewFilter(
            event_type=event_type,
            event_types=frozenset(event_types) if event_types is not None else None,
            tool_name=tool_name,
            errors_only=errors_only,
            flagged_only=flagged_only,
            search_query=search_query or "",
            call_ids=frozenset(call_ids) if call_ids is not None else None,
            update_indices=frozenset(update_indices) if update_indices is not None else None,
            event_indices=frozenset(event_indices) if event_indices is not None else None,
            kind=kind,
        )
        if self._turn_map_stale:
            self._rebuild_turn_map()
        self._visible = self._compute_visible(self.events)
        self._refresh_rows()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        row_key = event.row_key
        if row_key is None:
            return
        raw = str(row_key.value).strip()
        if not raw.isdigit():
            return
        idx = int(raw)
        matching = [e for e in self.events if e.index == idx]
        if matching:
            self.post_message(self.EventSelected(matching[0]))
