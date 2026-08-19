"""Timeline widget: load, filter, duration, pairing."""

from __future__ import annotations

import pytest
from conftest import make_trace_event
from groket.analysis.base import Finding
from groket.models import Flag, FlagVerdict, Severity, TraceEvent
from groket.ui.widgets.timeline import TimelineTable
from textual.app import App, ComposeResult


class _TimelineApp(App):
    def compose(self) -> ComposeResult:
        yield TimelineTable(id="timeline-list")


def _basic_events() -> list[TraceEvent]:
    return [
        make_trace_event(index=0, event_type="user_message_chunk", content="hello", timestamp=1000),
        make_trace_event(
            index=1,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
            tool_call_id="c1",
            timestamp=1001,
        ),
        make_trace_event(
            index=2,
            event_type="tool_call_update",
            tool_name="read_file",
            content="content",
            tool_call_id="c1",
            timestamp=1003,
        ),
        make_trace_event(
            index=3,
            event_type="agent_message_chunk",
            content="done",
            timestamp=1005,
        ),
        make_trace_event(
            index=4,
            event_type="turn_started",
            content="turn started  turn_number=0",
            timestamp=1006,
        ),
        make_trace_event(
            index=5,
            event_type="turn_started",
            content="turn ended  outcome=success",
            timestamp=1010,
        ),
        make_trace_event(
            index=6,
            event_type="session_error",
            content="error",
            is_error=True,
            timestamp=1011,
        ),
        make_trace_event(
            index=7,
            event_type="tool_call",
            tool_name="run_terminal_command",
            raw_input={"command": "echo"},
            is_error=True,
            tool_call_id="c2",
            timestamp=1012,
        ),
        make_trace_event(
            index=8,
            event_type="subagent_spawned",
            content="spawned",
            timestamp=1013,
        ),
        make_trace_event(
            index=9,
            event_type="agent_thought_chunk",
            content="thinking...",
            timestamp=1014,
        ),
        make_trace_event(
            index=10,
            event_type="plan",
            content="plan text",
            timestamp=1015,
        ),
    ]


@pytest.mark.asyncio
async def test_timeline_load_and_row_count() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        assert tl.row_count == len(events)
        assert len(tl.events) == len(events)


@pytest.mark.asyncio
async def test_timeline_add_row_existing_key_updates_not_raises() -> None:
    """Re-adding an event index must not raise Textual DuplicateKey (crash)."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        evs = [
            make_trace_event(index=0, event_type="user_message_chunk", content="a", timestamp=1),
            make_trace_event(index=1, event_type="agent_message_chunk", content="b", timestamp=2),
        ]
        tl.load_events(evs)
        assert tl.row_count == 2
        # Simulate desync: append path tries to add an index already on the table.
        tl._add_event_row(
            make_trace_event(index=1, event_type="agent_message_chunk", content="b2", timestamp=3)
        )
        assert tl.row_count == 2
        # Growth append with overlapping keys still safe.
        grown = [
            *evs,
            make_trace_event(index=1, event_type="agent_message_chunk", content="dup", timestamp=4),
            make_trace_event(index=2, event_type="user_message_chunk", content="c", timestamp=5),
        ]
        tl.load_events(grown)
        assert tl.row_count >= 2


@pytest.mark.asyncio
async def test_timeline_turn_index_for_maps_events() -> None:
    """turn_index_for exposes the enclosing trace turn_number."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="turn_started",
                content="turn started  turn_number=0",
                timestamp=1000,
            ),
            make_trace_event(
                index=1,
                event_type="user_message_chunk",
                content="hello",
                timestamp=1001,
            ),
            make_trace_event(
                index=2,
                event_type="turn_ended",
                content="turn ended  outcome=success",
                timestamp=1002,
            ),
            make_trace_event(
                index=3,
                event_type="turn_started",
                content="turn started  turn_number=1",
                timestamp=1003,
            ),
            make_trace_event(
                index=4,
                event_type="user_message_chunk",
                content="again",
                timestamp=1004,
            ),
        ]
        tl.load_events(events)
        # Open/rebuild builds the map once before paint (not per selection).
        assert tl._turn_map_stale is False
        assert tl.turn_index_for(1) == 0
        assert tl.turn_index_for(4) == 1
        # Turn column (index 1) shows the trace turn_number.
        cells_t0 = tl._row_cell_values(events[1])
        cells_t1 = tl._row_cell_values(events[4])
        assert cells_t0[0] == "1" and cells_t0[1] == "0"
        assert cells_t1[0] == "4" and cells_t1[1] == "1"


@pytest.mark.asyncio
async def test_timeline_same_length_live_tick_keeps_turn_map_warm() -> None:
    """Content-only live ticks must not stale the turn map (selection speed)."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        assert tl._turn_map_stale is False
        warm = dict(tl._turn_by_index)
        # Same structure, rewritten content — early return path.
        rewritten = [
            make_trace_event(
                index=e.index,
                event_type=e.event_type,
                content=(e.content or "") + "x",
                timestamp=e.timestamp,
                tool_name=e.tool_name,
                tool_call_id=e.tool_call_id,
                raw_input=dict(e.raw_input.raw())
                if hasattr(e.raw_input, "raw")
                else (e.raw_input if isinstance(e.raw_input, dict) else {}),
            )
            for e in events
        ]
        tl.load_events(rewritten)
        assert tl._turn_map_stale is False
        assert tl._turn_by_index == warm


@pytest.mark.asyncio
async def test_timeline_pair_rebinds_after_same_length_reparse() -> None:
    """read_file body is on tool_call_update; pairs must track re-parsed objects."""
    from groket.ui.render_detail import render_tool_detail_from_event
    from rich.syntax import Syntax

    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        call = make_trace_event(
            index=1,
            event_type="tool_call",
            tool_name="read_file",
            tool_call_id="c-read",
            raw_input={"target_file": "src/app.py"},
            content="",
            timestamp=1000,
        )
        # Empty body first (incomplete stream)
        empty_upd = make_trace_event(
            index=2,
            event_type="tool_call_update",
            tool_name="read_file",
            tool_call_id="c-read",
            content="",
            timestamp=1001,
        )
        tl.load_events([call, empty_upd])
        assert tl.get_paired_result(call) is empty_upd

        full_body = "import os\n\ndef main():\n    return 0\n"
        call2 = make_trace_event(
            index=1,
            event_type="tool_call",
            tool_name="read_file",
            tool_call_id="c-read",
            raw_input={"target_file": "src/app.py"},
            content="",
            timestamp=1000,
        )
        full_upd = make_trace_event(
            index=2,
            event_type="tool_call_update",
            tool_name="read_file",
            tool_call_id="c-read",
            content=full_body,
            timestamp=1002,
        )
        # Same length re-parse (new objects, body now filled)
        tl.load_events([call2, full_upd])
        paired = tl.get_paired_result(call2)
        assert paired is full_upd
        assert paired is not None
        assert full_body in (paired.content or "")

        g = render_tool_detail_from_event(call2, paired_result=paired)
        syn = [
            p for p in g.renderables if isinstance(p, Syntax) and full_body[:10] in (p.code or "")
        ]
        assert syn, "expected Syntax-highlighted file body in Output"
        lex = (getattr(syn[-1].lexer, "name", None) or type(syn[-1].lexer).__name__).lower()
        assert "python" in lex


@pytest.mark.asyncio
async def test_timeline_append_mid_turn_extends_map_without_resegment() -> None:
    """Live tool rows inside an open turn inherit turn id without full segment."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="turn_started",
                content="turn started  turn_number=0",
                timestamp=1000,
            ),
            make_trace_event(
                index=1,
                event_type="user_message_chunk",
                content="hello",
                timestamp=1001,
            ),
            make_trace_event(
                index=2,
                event_type="tool_call",
                content="ls",
                tool_name="run_terminal_command",
                tool_call_id="c1",
                timestamp=1002,
            ),
        ]
        tl.load_events(events)
        assert tl.turn_index_for(2) == 0
        from unittest.mock import patch

        import groket.session.turns as turns

        calls = {"n": 0}
        real = turns.segment_timeline_turns

        def counting(timeline: object) -> object:
            calls["n"] += 1
            return real(timeline)

        extra = make_trace_event(
            index=3,
            event_type="tool_result",
            content="ok",
            tool_name="run_terminal_command",
            tool_call_id="c1",
            timestamp=1003,
        )
        with patch.object(turns, "segment_timeline_turns", side_effect=counting):
            tl.load_events([*events, extra])
        assert calls["n"] == 0
        assert tl.turn_index_for(3) == 0
        assert tl._row_cell_values(extra)[1] == "0"


@pytest.mark.asyncio
async def test_timeline_load_events_appends_without_clear() -> None:
    """Live multi-turn growth appends rows instead of full clear+rebuild."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        assert tl.row_count == len(events)
        extra = make_trace_event(
            index=99,
            event_type="user_message_chunk",
            content="follow-up turn",
            timestamp=2000,
        )
        grown = [*events, extra]
        tl.load_events(grown)
        assert tl.row_count == len(grown)
        assert tl.events[-1].index == 99


@pytest.mark.asyncio
async def test_timeline_load_events_keeps_streaming_cells() -> None:
    """Same-length streaming keeps table cells; in-memory content still updates."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        base = [
            make_trace_event(
                index=0,
                event_type="agent_message_chunk",
                content="hello",
                timestamp=1000,
            ),
            make_trace_event(
                index=1,
                event_type="tool_call",
                tool_name="read_file",
                tool_call_id="c1",
                raw_input={"target_file": "a.py"},
                timestamp=1001,
            ),
        ]
        tl.load_events(base)
        assert tl.row_count == 2
        # Simulate streaming assistant text on the first row only.
        streamed = [
            make_trace_event(
                index=0,
                event_type="agent_message_chunk",
                content="hello world, still streaming…",
                timestamp=1000,
            ),
            base[1],
        ]
        tl.load_events(streamed)
        assert tl.row_count == 2
        # Live path keeps in-memory content; table cells are not rewritten mid-stream.
        assert "still streaming" in tl.events[0].content
        # Growth after stream: append only.
        grown = [
            *streamed,
            make_trace_event(
                index=2,
                event_type="tool_call_update",
                tool_name="read_file",
                tool_call_id="c1",
                content="ok",
                timestamp=1005,
            ),
        ]
        tl.load_events(grown)
        assert tl.row_count == 3


@pytest.mark.asyncio
async def test_timeline_live_skips_content_only_stream_patches() -> None:
    """Live path ignores content-only rewrites (streaming) to keep UI usable."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=i,
                event_type="agent_message_chunk",
                content=f"chunk-{i}",
                timestamp=1000 + i,
            )
            for i in range(80)
        ]
        tl.load_events(events)
        assert tl.row_count == 80
        # Mutate only the last event (streaming) — table must not thrash.
        streamed = list(events)
        streamed[-1] = make_trace_event(
            index=79,
            event_type="agent_message_chunk",
            content="chunk-79 streamed further text",
            timestamp=1079,
        )
        tl.load_events(streamed)
        assert tl.row_count == 80
        # In-memory events update for later F5; display cells stay put (no patch).
        assert "streamed further" in tl.events[-1].content
        # Append still works without full rebuild.
        streamed2 = [
            *streamed,
            make_trace_event(
                index=80,
                event_type="tool_call",
                tool_name="read_file",
                tool_call_id="c-tail",
                timestamp=1080,
            ),
        ]
        tl.load_events(streamed2)
        assert tl.row_count == 81


@pytest.mark.asyncio
async def test_timeline_durations_computed() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        # tool_call c1 at 1001, tool_result c1 at 1003 -> duration=2
        assert 1 in tl.durations
        assert tl.durations[1] == 2


@pytest.mark.asyncio
async def test_timeline_tool_pairs() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        call_ev = events[1]
        result_ev = events[2]
        assert tl.get_paired_result(call_ev) is result_ev
        assert tl.get_paired_call(result_ev) is call_ev
        assert tl.get_paired_result(events[0]) is None
        assert tl.get_paired_call(events[0]) is None


@pytest.mark.asyncio
async def test_timeline_with_findings_and_flags() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        finding = Finding(
            id="f1",
            title="test",
            severity=Severity.HIGH,
            plugin_id="engine",
            detail="x",
            tool_call_ids=["c1"],
        )
        flag = Flag(event_index=0, verdict=FlagVerdict.BAD, description="bad")
        tl.load_events(events, findings=[finding], flags=[flag])
        assert tl.findings_by_call.get("c1") is finding
        assert tl.flags_by_index.get(0) is flag


@pytest.mark.asyncio
async def test_timeline_filter_by_type() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(event_type="tool_call")
        assert tl.row_count < len(events)


@pytest.mark.asyncio
async def test_timeline_filter_by_types_set() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(event_types={"user_message_chunk", "agent_message_chunk"})
        assert tl.row_count == 2


@pytest.mark.asyncio
async def test_timeline_task_bookends_are_not_labeled_subagent() -> None:
    from groket import event_types as et

    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="task_backgrounded",
                content="Watch board",
                raw_input={
                    "task_id": "j1",
                    "description": "Watch board",
                    "output_file": "/tmp/monitor-call.log",
                },
            ),
            make_trace_event(
                index=1,
                event_type="scheduled_task_created",
                content="every 1 hour",
                raw_input={"task_id": "s1", "human_schedule": "every 1 hour"},
            ),
            make_trace_event(index=2, event_type="subagent_spawned", content="worker"),
        ]
        tl.load_events(events)
        bg = " ".join(tl._row_cell_values(events[0])).lower()
        sched = " ".join(tl._row_cell_values(events[1])).lower()
        sub = " ".join(tl._row_cell_values(events[2])).lower()
        assert "background" in bg or "monitor" in bg
        assert "subagent" not in bg
        assert "schedule" in sched
        assert "subagent" in sub
        tl.apply_filter(event_types=set(et.TASK_TYPES))
        assert tl.row_count == 2


@pytest.mark.asyncio
async def test_timeline_background_row_shows_command_once() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        ev = make_trace_event(
            index=0,
            event_type="task_backgrounded",
            content=(
                "task_backgrounded  tool_call_id=call-1  command=cd /tmp && just lint  cwd=/tmp"
            ),
            raw_input={},
        )
        cells = tl._row_cell_values(ev)
        type_cell, tool_cell, summary = cells[4], cells[5], cells[6]
        assert "background start" in type_cell.lower()
        assert "background start" not in tool_cell.lower()
        assert tool_cell == ""
        assert "$ cd /tmp && just lint" in summary
        assert "task_backgrounded" not in summary


@pytest.mark.asyncio
async def test_timeline_subagent_finish_summary_is_not_the_dump() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        spawn = make_trace_event(
            index=100,
            event_type="subagent_spawned",
            timestamp=900,
            content="Investigate the bug",
            raw_input={
                "childSessionId": "01a016d1-4df7-7d30-b99f-65289aa0b417",
                "subagentType": "coder",
                "description": "Investigate the bug",
            },
        )
        ev = make_trace_event(
            index=206,
            event_type="subagent_finished",
            timestamp=1000,
            content=(
                "Subagent finished  01a016d1-4df7-7d30-b99f-65289aa0b417  "
                "completed  duration_ms=96555"
            ),
            raw_input={
                "childSessionId": "01a016d1-4df7-7d30-b99f-65289aa0b417",
                "status": "completed",
                "durationMs": 96555,
            },
        )
        nxt = make_trace_event(
            index=207,
            event_type="agent_message_chunk",
            content="ok",
            timestamp=1000,
        )
        tl.load_events([spawn, ev, nxt])
        cells = tl._row_cell_values(ev)
        joined = " ".join(cells)
        assert "Investigate the bug" in cells[6]
        assert "coder" in cells[5]
        assert "1m36s" in joined
        assert "duration_ms" not in joined
        assert "01a016d1" not in cells[6]


@pytest.mark.asyncio
async def test_timeline_filter_errors_only() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(errors_only=True)
        assert tl.row_count >= 1


@pytest.mark.asyncio
async def test_timeline_filter_flagged_only() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        flag = Flag(event_index=0, verdict=FlagVerdict.GOOD, description="ok")
        tl.load_events(events, flags=[flag])
        tl.apply_filter(flagged_only=True)
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_filter_search_query() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(search_query="hello")
        assert tl.row_count >= 1


@pytest.mark.asyncio
async def test_timeline_filter_tool_name() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(tool_name="read_file")
        assert tl.row_count >= 1


@pytest.mark.asyncio
async def test_timeline_filter_workflow_tool() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events() + [
            make_trace_event(
                index=99,
                event_type="tool_call",
                tool_name="workflow",
                raw_input={"script_path": "/repo/.grok/workflows/sprint.rhai"},
            )
        ]
        tl.load_events(events)
        tl.apply_filter(tool_name="workflow")
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_filter_call_ids() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(call_ids={"c1"})
        assert tl.row_count >= 1


@pytest.mark.asyncio
async def test_timeline_event_selected_message() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.move_cursor(row=0, animate=False)
        # Row highlight triggers EventSelected


@pytest.mark.asyncio
async def test_timeline_long_duration_formatting() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="run_terminal_command",
                tool_call_id="slow",
                timestamp=1000,
            ),
            make_trace_event(
                index=1,
                event_type="tool_call_update",
                tool_name="run_terminal_command",
                tool_call_id="slow",
                timestamp=1070,  # 70s duration
            ),
        ]
        tl.load_events(events)
        assert 0 in tl.durations
        assert tl.durations[0] == 70


@pytest.mark.asyncio
async def test_timeline_no_timestamp() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(index=0, event_type="user_message_chunk", content="x", timestamp=None),
        ]
        tl.load_events(events)
        assert tl.row_count == 1
        assert 0 not in tl.durations


@pytest.mark.asyncio
async def test_timeline_tool_result_no_call_id() -> None:
    """tool_result without call_id is skipped in pairing."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="tool_call_update",
                tool_name="grep",
                content="result",
                tool_call_id="",
                timestamp=1000,
            ),
        ]
        tl.load_events(events)
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_subagent_tool_column() -> None:
    """Subagent events populate the tool column."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0, event_type="subagent_spawned", content="spawned", timestamp=1000
            ),
        ]
        tl.load_events(events)
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_medium_duration_yellow() -> None:
    """30-60 second duration falls in the medium (yellow) range."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="run_terminal_command",
                tool_call_id="med",
                timestamp=1000,
            ),
            make_trace_event(
                index=1,
                event_type="tool_call_update",
                tool_name="run_terminal_command",
                tool_call_id="med",
                timestamp=1045,  # 45s
            ),
        ]
        tl.load_events(events)
        assert 0 in tl.durations
        assert 30 <= tl.durations[0] < 60


@pytest.mark.asyncio
async def test_timeline_tool_error_non_tool_column() -> None:
    """Tool error with empty tool name renders without markup prefix."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = [
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="",  # Empty tool name → empty tool_col
                is_error=True,
                timestamp=1000,
            ),
        ]
        tl.load_events(events)
        assert tl.row_count == 1


@pytest.mark.asyncio
async def test_timeline_row_highlighted_non_digit_key() -> None:
    """row_highlighted handles non-digit row key gracefully."""
    from textual.widgets import DataTable

    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        # Manually trigger with a non-digit key
        from textual.widgets._data_table import RowKey

        event = DataTable.RowHighlighted(
            tl,
            tl.cursor_coordinate,
            RowKey("not-a-digit"),
        )
        tl.on_data_table_row_highlighted(event)


@pytest.mark.asyncio
async def test_timeline_append_keeps_highlight_unless_follow_tail() -> None:
    """Tail off keeps the cursor; Tail on jumps to the last row."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()[:3]
        tl.load_events(events)
        tl.move_cursor(row=0, animate=False, scroll=False)
        extra = make_trace_event(
            index=90,
            event_type="user_message_chunk",
            content="later",
            timestamp=3000,
        )
        tl.load_events([*events, extra], follow_tail=False)
        assert tl.cursor_row == 0
        assert tl.row_count == 4
        more = make_trace_event(
            index=91,
            event_type="user_message_chunk",
            content="newest",
            timestamp=3001,
        )
        tl.load_events([*events, extra, more], follow_tail=True)
        assert tl.cursor_row == tl.row_count - 1


@pytest.mark.asyncio
async def test_timeline_unknown_type_uses_human_label() -> None:
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        ev = make_trace_event(index=0, event_type="brand_new_signal", content="x")
        tl.load_events([ev])
        cells = tl._row_cell_values(ev)
        assert "BRAND_NEW_SIGNAL" not in cells[4]
        assert "brand new signal" in cells[4]


def _summary_cell(tl: TimelineTable, key: str) -> str:
    cols = list(tl.columns.keys())
    return str(tl.get_cell(key, cols[6]))


@pytest.mark.asyncio
async def test_timeline_same_length_reload_paints_new_flag() -> None:
    """Flag/finding chrome must update Summary when the list length is unchanged."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        assert "⚑" not in _summary_cell(tl, "1")
        flag = Flag(event_index=1, verdict=FlagVerdict.BAD, description="bad")
        tl.load_events(events, flags=[flag])
        assert "⚑" in _summary_cell(tl, "1")
        finding = Finding(
            id="f1",
            title="test",
            severity=Severity.HIGH,
            plugin_id="engine",
            detail="x",
            tool_call_ids=["c1"],
        )
        tl.load_events(events, findings=[finding], flags=[flag])
        marked = _summary_cell(tl, "1")
        assert "⚑" in marked
        assert "⚠" in marked


@pytest.mark.asyncio
async def test_timeline_filter_append_keeps_visible_rows() -> None:
    """Live append under a type filter must not paint the unfiltered list."""
    app = _TimelineApp()
    async with app.run_test():
        tl = app.query_one("#timeline-list", TimelineTable)
        events = _basic_events()
        tl.load_events(events)
        tl.apply_filter(event_type="tool_call")
        before_keys = {str(k.value) for k in tl.rows.keys()}
        before_n = tl.row_count
        assert before_n < len(events)
        extra_tool = make_trace_event(
            index=99,
            event_type="tool_call",
            tool_name="grep",
            raw_input={"pattern": "x"},
            tool_call_id="c99",
            timestamp=2000,
        )
        extra_user = make_trace_event(
            index=100,
            event_type="user_message_chunk",
            content="later",
            timestamp=2001,
        )
        tl.load_events([*events, extra_tool, extra_user])
        after_keys = {str(k.value) for k in tl.rows.keys()}
        assert tl.row_count == before_n + 1
        assert before_keys <= after_keys
        assert "99" in after_keys
        assert "100" not in after_keys
        assert len(tl.events) == len(events) + 2
