"""Timeline turn segmentation for multi-turn stats/summary/report."""

from __future__ import annotations

from groket.models import TraceEvent
from groket.session.turns import (
    TurnSegment,
    event_display_turn_map,
    format_turns_plain,
    segment_timeline_turns,
    turn_index_for_event,
    turn_summary_rows,
)


def _ev(index: int, etype: str, content: str = "", **kw) -> TraceEvent:
    return TraceEvent(
        index=index,
        event_type=etype,
        content=content,
        timestamp=kw.get("ts", 1_000_000 + index * 10),
        tool_name=kw.get("tool", ""),
        is_error=kw.get("err", False),
    )


def test_no_markers_single_open_segment():
    tl = [_ev(0, "user_message_chunk", "hi"), _ev(1, "agent_message_chunk", "yo")]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].open is True
    assert segs[0].event_count == 2
    assert segs[0].turn_index == 0
    assert segs[0].turn_number == 0


def test_host_turn_completed_splits_turns():
    """Host live traces close turns with turn_completed, not turn_ended."""
    tl = [
        _ev(0, "user_message_chunk", "<user_query>first</user_query>"),
        _ev(1, "agent_message_chunk", "ok"),
        _ev(2, "turn_completed", "turn_completed  prompt_id=a"),
        _ev(3, "user_message_chunk", "<user_query>second</user_query>"),
        _ev(4, "tool_call", tool="grep"),
        _ev(5, "turn_completed", "turn_completed  prompt_id=b"),
    ]
    tl[4] = TraceEvent(index=4, event_type="tool_call", tool_name="grep", timestamp=1_000_040)
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].user_count == 1
    assert segs[1].user_count == 1
    assert segs[0].open is False
    assert segs[1].open is False
    assert [s.turn_number for s in segs] == [0, 1]
    mapped = event_display_turn_map(segs)
    assert mapped[0] == 0
    assert mapped[3] == 1


def test_host_turn_completed_leaves_outcome_empty():
    """Host turn_completed has no outcome= field; Summary shows an em dash."""
    tl = [
        _ev(0, "user_message_chunk", "<user_query>first</user_query>"),
        _ev(1, "agent_message_chunk", "ok"),
        _ev(2, "turn_completed", "turn_completed  prompt_id=a"),
        _ev(3, "user_message_chunk", "<user_query>second</user_query>"),
        _ev(4, "turn_completed", "turn_completed  prompt_id=b"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.outcome for s in segs] == ["", ""]
    rows = turn_summary_rows(segs)
    assert rows[0]["outcome"] == "—"
    assert rows[1]["outcome"] == "—"
    assert segs[0].label == "turn 0"
    assert segs[1].label == "turn 1"


def test_turn_completed_then_turn_ended_is_one_turn():
    """Host updates and events.jsonl both close the same turn."""
    tl = [
        _ev(0, "user_message_chunk", "<user_query>first</user_query>"),
        _ev(1, "agent_message_chunk", "ok"),
        _ev(2, "turn_completed", "turn_completed  prompt_id=a"),
        _ev(3, "turn_ended", "turn ended  outcome=completed"),
        _ev(4, "user_message_chunk", "<user_query>second</user_query>"),
        _ev(5, "turn_completed", "turn_completed  prompt_id=b"),
        _ev(6, "turn_ended", "turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].outcome == "completed"
    assert segs[1].outcome == "completed"
    assert segs[0].open is False
    assert segs[1].open is False


def test_turn_ended_without_outcome_is_unknown():
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "turn_ended", "turn ended"),
    ]
    segs = segment_timeline_turns(tl)
    assert segs[0].outcome == "unknown"


def test_two_turns_with_markers():
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "tool_call", tool="grep"),
        _ev(3, "turn_ended", "turn ended  outcome=success"),
        _ev(4, "turn_started", "turn started  turn_number=1"),
        _ev(5, "user_message_chunk", "second"),
        _ev(6, "tool_call", tool="bash", err=True),
        _ev(7, "turn_ended", "turn ended  outcome=success"),
    ]
    # fix tool_name kw - TraceEvent uses tool_name
    tl[2] = TraceEvent(index=2, event_type="tool_call", tool_name="grep", timestamp=1_000_020)
    tl[6] = TraceEvent(
        index=6, event_type="tool_call", tool_name="bash", is_error=True, timestamp=1_000_060
    )
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].outcome == "success"
    assert segs[0].tool_call_count == 1
    assert segs[1].tool_error_count == 1
    assert segs[1].user_count == 1
    rows = turn_summary_rows(segs)
    # Chronological: turn 0 first, turn 1 last.
    assert rows[0]["turn"] == 0
    assert rows[0]["tools"] == 1
    assert rows[-1]["turn"] == 1
    assert rows[-1]["tool_errors"] == 1
    assert "turn" in format_turns_plain(segs).lower()


def test_open_second_turn():
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "turn_ended", "turn ended  outcome=success"),
        _ev(2, "turn_started", "turn started  turn_number=1"),
        _ev(3, "agent_message_chunk", "still going"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].open is False
    assert segs[1].open is True


def test_empty_timeline_returns_empty():
    assert segment_timeline_turns([]) == []


def test_turn_label_with_outcome():
    seg = segment_timeline_turns(
        [
            _ev(0, "turn_started", "turn started  turn_number=0"),
            _ev(1, "turn_ended", "turn ended  outcome=success"),
        ]
    )
    assert "success" in seg[0].label
    assert "0" in seg[0].label


def test_label_uses_trace_turn_number():
    """Visible ids are events.jsonl turn_started.turn_number, not a remade list."""
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=83"),
        _ev(1, "user_message_chunk", "a"),
        _ev(2, "turn_ended", "turn ended  outcome=success"),
        _ev(3, "turn_started", "turn started  turn_number=80"),
        _ev(4, "user_message_chunk", "b"),
        _ev(5, "turn_ended", "turn ended  outcome=success"),
        _ev(6, "turn_started", "turn started  turn_number=87"),
        _ev(7, "user_message_chunk", "c"),
        _ev(8, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 3
    assert [s.turn_index for s in segs] == [0, 1, 2]
    assert [s.turn_number for s in segs] == [83, 80, 87]
    assert segs[0].label.startswith("turn 83")
    assert segs[1].label.startswith("turn 80")
    assert segs[2].label.startswith("turn 87")


def test_turn_label_plain_number():
    """When no markers, label uses turn_index 0."""
    tl = [_ev(0, "user_message_chunk", "hi")]
    seg = segment_timeline_turns(tl)[0]
    assert "0" in seg.label


def test_duration_seconds_large_delta_treated_as_ms():
    """Timestamps with absurd seconds delta should be treated as milliseconds."""
    seg = segment_timeline_turns(
        [
            _ev(0, "user_message_chunk", "hi", ts=0),
            _ev(1, "agent_message_chunk", "bye", ts=86_400 * 365 + 1),
        ]
    )
    dur = seg[0].duration_seconds()
    assert dur is not None
    # delta > 86400*365 triggers ms conversion
    assert dur == (86_400 * 365 + 1) / 1000.0


def test_duration_seconds_from_durations_map():
    """When fewer than 2 timestamps available, use the durations dict fallback."""
    tl = [_ev(0, "user_message_chunk", "hi")]
    tl[0].timestamp = None
    seg = segment_timeline_turns(tl)[0]
    # No timestamps, no durations map → None
    assert seg.duration_seconds() is None
    # With durations map for the event
    assert seg.duration_seconds(durations={0: 2.5}) == 2.5


def test_duration_seconds_durations_map_no_match():
    """Durations map present but no matching indices → None."""
    tl = [_ev(0, "user_message_chunk", "hi")]
    tl[0].timestamp = None
    seg = segment_timeline_turns(tl)[0]
    assert seg.duration_seconds(durations={99: 1.0}) is None


def test_turn_number_from_event_no_match():
    """Turn number regex returns None when content has no turn_number=N."""
    tl = [
        _ev(0, "turn_started", "turn started"),
        _ev(1, "turn_ended", "turn ended  outcome=unknown"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    # turn_number assigned from sequential 0-based index
    assert segs[0].turn_number == 0
    assert segs[0].turn_index == 0


def test_turn_ended_before_started_creates_segment():
    """A turn_ended appearing before any turn_started produces a segment."""
    tl = [_ev(0, "turn_ended", "turn ended  outcome=error")]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].outcome == "error"
    assert segs[0].open is False
    assert segs[0].turn_index == 0


def test_preamble_events_before_first_start():
    """User/agent events before first turn_started merge into turn 0 with the marker."""
    tl = [
        _ev(0, "user_message_chunk", "preamble question"),
        _ev(1, "turn_started", "turn started  turn_number=0"),
        _ev(2, "agent_message_chunk", "reply"),
        _ev(3, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].turn_index == 0
    assert segs[0].turn_number == 0
    assert segs[0].user_count == 1
    assert segs[0].outcome == "success"


def test_system_prompt_is_session_level_not_a_turn():
    """Parser-injected system event is outside turn segments (not merged, not counted)."""
    from groket.session.turns import is_session_level_timeline_event

    tl = [
        _ev(0, "system", "You are Grok…"),
        _ev(1, "turn_started", "turn started  turn_number=0"),
        _ev(2, "user_message_chunk", "hi"),
        _ev(3, "agent_message_chunk", "hello"),
        _ev(4, "turn_ended", "turn ended  outcome=success"),
    ]
    assert is_session_level_timeline_event(tl[0])
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    assert segs[0].turn_number == 0
    assert all(e.event_type != "system" for e in segs[0].events)
    assert segs[0].user_count == 1
    assert segs[0].outcome == "success"


def test_system_prompt_does_not_affect_multi_turn_count():
    """Session-level system chrome leaves harness turn count unchanged."""
    tl = [
        _ev(0, "system", "You are Grok…"),
        _ev(1, "turn_started", "turn started  turn_number=0"),
        _ev(2, "turn_ended", "turn ended  outcome=success"),
        _ev(3, "turn_started", "turn started  turn_number=1"),
        _ev(4, "user_message_chunk", "again"),
        _ev(5, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_number == 0
    assert segs[1].turn_number == 1
    assert segs[1].user_count == 1
    assert all(e.event_type != "system" for seg in segs for e in seg.events)


def test_system_only_timeline_has_no_turns():
    """A timeline that is only session-level chrome yields no turn segments."""
    tl = [_ev(0, "system", "You are Grok…")]
    assert segment_timeline_turns(tl) == []


def test_previous_turn_closed_on_new_start():
    """Turn started while previous is open → close previous."""
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "tool_call"),
        _ev(2, "turn_started", "turn started  turn_number=1"),
        _ev(3, "agent_message_chunk", "done"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert segs[0].open is False
    assert segs[0].outcome == "unknown"
    assert segs[1].open is True


def test_error_event_count():
    tl = [_ev(0, "session_error", "boom", err=True), _ev(1, "user_message_chunk", "hi")]
    seg = segment_timeline_turns(tl)[0]
    assert seg.error_event_count == 1


def test_format_turns_plain_empty():
    assert format_turns_plain([]) == "(no turns)"


def test_format_turns_plain_with_duration():
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=1"),
        _ev(1, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    text = format_turns_plain(segs, durations={0: 1.0, 1: 2.0})
    assert "turn" in text.lower()


def test_turn_summary_rows_structure():
    tl = [_ev(0, "user_message_chunk", "hi"), _ev(1, "agent_message_chunk", "bye")]
    segs = segment_timeline_turns(tl)
    rows = turn_summary_rows(segs)
    assert len(rows) == 1
    row = rows[0]
    assert "turn" in row
    assert "label" in row
    assert "events" in row
    assert row["users"] == 1
    assert row["assistants"] == 1
    assert row["context"] == ""


def test_turn_summary_rows_session_context_on_latest_only():
    tl = [
        _ev(0, "turn_started", "Turn started turn_number=0"),
        _ev(1, "user_message_chunk", "a"),
        _ev(2, "turn_ended", "Turn ended outcome=completed"),
        _ev(3, "turn_started", "Turn started turn_number=1"),
        _ev(4, "user_message_chunk", "b"),
        _ev(5, "turn_ended", "Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    rows = turn_summary_rows(segs, session_context_compact="35% 179k/500k")
    assert len(rows) >= 2
    # Chronological: session-level context attaches to the latest turn only.
    assert rows[-1]["context"] == "35% 179k/500k"
    assert rows[-1]["turn"] == segs[-1].turn_index
    assert rows[0]["context"] == ""
    assert rows[0]["turn"] == segs[0].turn_index


def test_turn_summary_rows_context_by_turn_samples():
    tl = [
        _ev(0, "turn_started", "Turn started turn_number=0"),
        _ev(1, "user_message_chunk", "a"),
        _ev(2, "turn_ended", "Turn ended outcome=completed"),
        _ev(3, "turn_started", "Turn started turn_number=1"),
        _ev(4, "user_message_chunk", "b"),
        _ev(5, "turn_ended", "Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    rows = turn_summary_rows(
        segs,
        session_context_compact="99% 1/1",
        context_by_turn={segs[0].turn_index: "10% 50k/500k", segs[-1].turn_index: "35% 179k/500k"},
    )
    # Chronological: latest turn is last.
    assert rows[-1]["context"] == "35% 179k/500k"
    assert rows[0]["context"] == "10% 50k/500k"


def test_first_last_index_empty():
    from groket.session.turns import TurnSegment

    seg = TurnSegment(turn_index=1, turn_number=1, events=[])
    assert seg.first_index is None
    assert seg.last_index is None


def test_turn_label_no_outcome_no_open():
    """Closed turn without outcome → plain label."""
    from groket.session.turns import TurnSegment

    seg = TurnSegment(turn_index=3, turn_number=3, open=False, outcome="")
    assert seg.label == "turn 3"


def test_harness_zero_based_preserved():
    """Harness turn_number=0 is not renumbered to 1."""
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "turn_ended", "turn ended  outcome=success"),
        _ev(2, "turn_started", "turn started  turn_number=1"),
        _ev(3, "turn_ended", "turn ended  outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_index for s in segs] == [0, 1]
    assert [s.turn_number for s in segs] == [0, 1]


def test_events_between_turns_attach_to_previous_segment() -> None:
    """Late assistant after turn_ended must not become a fake Turn 1 alone."""
    from groket.models import TraceEvent

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first"),
        TraceEvent(index=2, event_type="agent_message_chunk", content="reply A"),
        TraceEvent(index=3, event_type="turn_ended", content="Turn ended outcome=success"),
        # Late/out-of-order stream chunk after end — must stay with turn 0
        TraceEvent(index=4, event_type="agent_message_chunk", content="late chunk of turn 0"),
        TraceEvent(index=5, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=6, event_type="user_message_chunk", content="follow-up"),
        TraceEvent(index=7, event_type="agent_message_chunk", content="reply B"),
        TraceEvent(index=8, event_type="turn_ended", content="Turn ended outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert segs[0].turn_index == 0
    assert segs[1].turn_index == 1
    assert any(e.content == "late chunk of turn 0" for e in segs[0].events)
    assert not any(e.content == "late chunk of turn 0" for e in segs[1].events)
    assert any(e.content == "follow-up" for e in segs[1].events)


def test_follow_up_user_before_next_turn_started_is_own_turn() -> None:
    """Interactive follow-up user msg before turn_started must not merge into turn 0."""
    from groket.models import TraceEvent

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first"),
        TraceEvent(index=2, event_type="agent_message_chunk", content="reply A"),
        TraceEvent(index=3, event_type="turn_ended", content="Turn ended outcome=success"),
        TraceEvent(index=4, event_type="user_message_chunk", content="follow-up prompt"),
        TraceEvent(index=5, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=6, event_type="agent_message_chunk", content="reply B"),
        TraceEvent(index=7, event_type="turn_ended", content="Turn ended outcome=success"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert any(e.content == "follow-up prompt" for e in segs[1].events)
    assert not any(e.content == "follow-up prompt" for e in segs[0].events)
    assert segs[1].turn_number == 1


def test_segments_preserve_non_contiguous_prompt_indexes() -> None:
    events = [
        TraceEvent(index=0, event_type="turn_started", content="turn started  turn_number=1"),
        TraceEvent(
            index=1,
            event_type="user_message_chunk",
            content="first",
            prompt_index=4,
        ),
        TraceEvent(index=2, event_type="turn_ended", content="turn ended  outcome=success"),
        TraceEvent(index=3, event_type="turn_started", content="turn started  turn_number=2"),
        TraceEvent(
            index=4,
            event_type="user_message_chunk",
            content="second",
            prompt_index=9,
        ),
        TraceEvent(index=5, event_type="turn_ended", content="turn ended  outcome=success"),
    ]

    segments = segment_timeline_turns(events)

    assert [segment.turn_index for segment in segments] == [0, 1]
    assert [segment.turn_number for segment in segments] == [1, 2]
    assert [segment.prompt_index for segment in segments] == [4, 9]


def test_background_task_completion_turns_keep_trace_numbers() -> None:
    """Each ``turn_started`` is its own picker row, including completion chrome."""
    from groket.models import TraceEvent

    bg_user = (
        '<system-reminder>\nBackground task "call-05172712-9431-4be8-bdf0-6a58f7cdb30a-162" '
        "completed.\n</system-reminder>"
    )
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="refactor the module"),
        TraceEvent(index=2, event_type="tool_call", tool_name="spawn_subagent"),
        TraceEvent(index=3, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=4, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=5, event_type="user_message_chunk", content=bg_user),
        TraceEvent(index=6, event_type="agent_message_chunk", content="task summary"),
        TraceEvent(index=7, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=8, event_type="turn_started", content="Turn started turn_number=2"),
        TraceEvent(index=9, event_type="agent_message_chunk", content="more completion chrome"),
        TraceEvent(index=10, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [0, 1, 2]
    assert any(e.content == "refactor the module" for e in segs[0].events)
    assert any(e.content == bg_user for e in segs[1].events)
    assert any(e.content == "more completion chrome" for e in segs[2].events)


def test_task_completed_call_user_keeps_its_start_number() -> None:
    """``task-completed-call-…`` chrome still sits on its own ``turn_started``."""
    from groket.models import TraceEvent

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="operator"),
        TraceEvent(index=2, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(
            index=4, event_type="user_message_chunk", content="task-completed-call-abc-123 done"
        ),
        TraceEvent(index=5, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [0, 1]
    assert any("task-completed-call-" in (e.content or "") for e in segs[1].events)


def test_background_user_between_turns_attaches_to_previous() -> None:
    """Background-task user chrome between turn_ended and turn_started stays on parent."""
    from groket.models import TraceEvent

    bg = 'Background task "x" completed.'
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="operator"),
        TraceEvent(index=2, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=3, event_type="user_message_chunk", content=bg),
        TraceEvent(index=4, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=5, event_type="user_message_chunk", content="real follow-up"),
        TraceEvent(index=6, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert any(e.content == bg for e in segs[0].events)
    assert any(e.content == "real follow-up" for e in segs[1].events)


def test_system_reminder_turns_keep_trace_number() -> None:
    """A chrome-only ``turn_started`` still has its own picker row.

    Rules/skills/MCP status injections arrive as user_message_chunk inside their
    own turn_started/ended pair. The Turn card summary stays operator text.
    """
    from groket.models import TraceEvent
    from groket.session.control_views import turn_segment_mapping
    from groket.session.turns import is_harness_user_chrome, segment_timeline_turns

    skills = (
        "<system-reminder>\nThe following skills are available for use:\n"
        "- check-work: verify changes\n</system-reminder>"
    )
    rules = (
        "<system-reminder>\nAs you answer the user's questions, you can use "
        "the following context…\n</system-reminder>"
    )
    assert is_harness_user_chrome(skills)
    assert is_harness_user_chrome(rules)

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="fix the flaky test"),
        TraceEvent(index=2, event_type="agent_message_chunk", content="working"),
        TraceEvent(index=3, event_type="turn_ended", content="Turn ended outcome=completed"),
        # Mid-session system-reminder only turn (skills dump)
        TraceEvent(index=4, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=5, event_type="user_message_chunk", content=skills),
        TraceEvent(index=6, event_type="turn_ended", content="Turn ended outcome=completed"),
        # Between-turn system-reminder before real follow-up
        TraceEvent(index=7, event_type="user_message_chunk", content=rules),
        TraceEvent(index=8, event_type="turn_started", content="Turn started turn_number=2"),
        TraceEvent(index=9, event_type="user_message_chunk", content="and then push"),
        TraceEvent(index=10, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [0, 1, 2]
    assert any(e.content == "fix the flaky test" for e in segs[0].events)
    assert any(e.content == skills for e in segs[1].events)
    assert any(e.content == rules for e in segs[1].events)
    assert any(e.content == "and then push" for e in segs[2].events)
    # Turn card summary must be operator text, not the system-reminder body.
    row0 = turn_segment_mapping(segs[0])
    row2 = turn_segment_mapping(segs[2])
    assert row0["summary"] == "fix the flaky test"
    assert row0["assistantSummary"] == "working"
    assert row0["assistantEventIndex"] == 2
    assert row2["summary"] == "and then push"
    assert "<system-reminder>" not in str(row0["summary"])
    assert "<system-reminder>" not in str(row2["summary"])


def test_system_reminder_before_operator_in_same_turn_skipped_for_summary() -> None:
    """When reminder and operator share a turn, summary prefers the operator."""
    from groket.models import TraceEvent
    from groket.session.control_views import turn_segment_mapping
    from groket.session.turns import segment_timeline_turns

    reminder = "<system-reminder>\nMCP servers connected:\n- tasks\n</system-reminder>"
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content=reminder),
        TraceEvent(index=2, event_type="user_message_chunk", content="say meow"),
        TraceEvent(index=3, event_type="agent_message_chunk", content="meow"),
        TraceEvent(index=4, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 1
    row = turn_segment_mapping(segs[0])
    assert row["summary"] == "say meow"
    assert row["userEventIndex"] == 2
    assert row["assistantSummary"] == "meow"
    assert row["assistantEventIndex"] == 3


def test_assistant_summary_keeps_long_markdown() -> None:
    """Turn cards must keep enough assistant markdown to render lists/fences."""
    from groket.models import TraceEvent
    from groket.session.control_views import turn_segment_mapping
    from groket.session.turns import segment_timeline_turns

    body = "Intro paragraph\n\n1. first item\n2. second item\n\n```rust\nfn x() {}\n```\n\n" + (
        "word " * 400
    )
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="write it"),
        TraceEvent(index=2, event_type="agent_message_chunk", content=body),
        TraceEvent(index=3, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    row = turn_segment_mapping(segment_timeline_turns(tl)[0])
    summary = str(row["assistantSummary"])
    assert "1. first item" in summary
    assert "```rust" in summary
    assert len(summary) > 800


def test_open_background_tail_is_its_own_open_turn() -> None:
    """An open chrome-only ``turn_started`` stays a separate open row."""
    from groket.models import TraceEvent

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="operator"),
        TraceEvent(index=2, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(
            index=4, event_type="user_message_chunk", content='Background task "y" completed.'
        ),
        # no turn_ended — open completion tail
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [0, 1]
    assert segs[0].open is False
    assert segs[1].open is True


def test_blank_user_event_is_not_operator() -> None:
    """Whitespace-only user rows do not count as operator prompts."""
    from groket.session.turns import TurnSegment, _segment_has_operator_user

    seg = TurnSegment(
        turn_index=0,
        turn_number=0,
        events=[TraceEvent(index=0, event_type="user_message_chunk", content="   ")],
    )
    assert _segment_has_operator_user(seg) is False


def test_real_follow_up_after_background_turn_stays_separate() -> None:
    """A real operator follow-up after a chrome turn is still its own segment."""
    from groket.models import TraceEvent

    bg_user = 'Background task "call-abc" completed.'
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="Turn started turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first prompt"),
        TraceEvent(index=2, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="Turn started turn_number=1"),
        TraceEvent(index=4, event_type="user_message_chunk", content=bg_user),
        TraceEvent(index=5, event_type="turn_ended", content="Turn ended outcome=completed"),
        TraceEvent(index=6, event_type="user_message_chunk", content="real follow-up from host"),
        TraceEvent(index=7, event_type="turn_started", content="Turn started turn_number=2"),
        TraceEvent(index=8, event_type="agent_message_chunk", content="reply"),
        TraceEvent(index=9, event_type="turn_ended", content="Turn ended outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [0, 1, 2]
    assert any(e.content == "first prompt" for e in segs[0].events)
    assert any(e.content == bg_user for e in segs[1].events)
    assert any(e.content == "real follow-up from host" for e in segs[2].events)


def test_turn_index_for_event_mid_timeline() -> None:
    """Selected mid-session event maps to its segment, not the last turn."""
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "tool_call"),
        _ev(3, "turn_ended", "turn ended  outcome=success"),
        _ev(4, "turn_started", "turn started  turn_number=1"),
        _ev(5, "user_message_chunk", "second"),
        _ev(6, "tool_call"),
        _ev(7, "turn_ended", "turn ended  outcome=success"),
    ]
    tl[2] = TraceEvent(index=2, event_type="tool_call", tool_name="grep", timestamp=1_000_020)
    tl[6] = TraceEvent(index=6, event_type="tool_call", tool_name="bash", timestamp=1_000_060)
    segs = segment_timeline_turns(tl)
    assert turn_index_for_event(segs, 2) == 0
    assert turn_index_for_event(segs, 6) == 1
    assert turn_index_for_event(segs, 99) is None


def test_late_turn_ended_does_not_split_follow_up() -> None:
    """Follow-up user then leftover turn_ended stays one operator turn.

    Host ``turn_completed`` already closed the previous harness turn. The
    matching ``events.jsonl`` ``turn_ended`` can land after the next user
    message; that end must not create a two-event sliver turn.
    """
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="turn started  turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first"),
        TraceEvent(index=2, event_type="agent_message_chunk", content="done"),
        TraceEvent(index=3, event_type="turn_completed", content="turn_completed  prompt_id=a"),
        TraceEvent(index=4, event_type="user_message_chunk", content="add abuse examples"),
        TraceEvent(index=5, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=6, event_type="turn_started", content="turn started  turn_number=1"),
        TraceEvent(index=7, event_type="agent_message_chunk", content="updated the body"),
        TraceEvent(index=8, event_type="turn_completed", content="turn_completed  prompt_id=b"),
        TraceEvent(index=9, event_type="turn_ended", content="turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 2
    assert [s.turn_index for s in segs] == [0, 1]
    assert any(e.content == "add abuse examples" for e in segs[1].events)
    assert any(e.content == "updated the body" for e in segs[1].events)
    assert turn_index_for_event(segs, 4) == 1
    assert turn_index_for_event(segs, 7) == 1
    assert turn_index_for_event(segs, 5) == 0


def test_background_watcher_turn_keeps_trace_number() -> None:
    """A watcher ``turn_started`` is its own picker row with that number."""
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="turn started  turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="watch the merge request"),
        TraceEvent(index=2, event_type="turn_completed", content="turn_completed  prompt_id=a"),
        TraceEvent(index=3, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=4, event_type="turn_started", content="turn started  turn_number=1"),
        TraceEvent(
            index=5,
            event_type="tool_call",
            tool_name="get_command_or_subagent_output",
            timestamp=1_000_050,
        ),
        TraceEvent(index=6, event_type="agent_message_chunk", content="No change."),
        TraceEvent(
            index=7,
            event_type="turn_completed",
            content="turn_completed  prompt_id=subagent-completed-abc",
        ),
        TraceEvent(index=8, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=9, event_type="turn_started", content="turn started  turn_number=2"),
        TraceEvent(index=10, event_type="user_message_chunk", content="next question"),
        TraceEvent(index=11, event_type="turn_ended", content="turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [0, 1, 2]
    assert any(e.content == "watch the merge request" for e in segs[0].events)
    assert any(e.content == "No change." for e in segs[1].events)
    assert any(e.content == "next question" for e in segs[2].events)
    assert [s.turn_index for s in segs] == [0, 1, 2]


def test_late_end_plus_watcher_keeps_operator_turn_numbers() -> None:
    """Watcher tail + late turn_ended must not shift later operator ids."""
    reminder = (
        '<system-reminder> Background subagent "abc" '
        "(general-purpose: watch) completed.\n</system-reminder>"
    )
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="turn started  turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first"),
        TraceEvent(index=2, event_type="turn_completed", content="turn_completed  prompt_id=a"),
        TraceEvent(index=3, event_type="user_message_chunk", content=reminder),
        TraceEvent(index=4, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=5, event_type="turn_started", content="turn started  turn_number=1"),
        TraceEvent(
            index=6,
            event_type="tool_call",
            tool_name="get_command_or_subagent_output",
            timestamp=1_000_060,
        ),
        TraceEvent(index=7, event_type="agent_message_chunk", content="No change."),
        TraceEvent(
            index=8,
            event_type="turn_completed",
            content="turn_completed  prompt_id=subagent-completed-abc",
        ),
        TraceEvent(index=9, event_type="user_message_chunk", content="add abuse examples"),
        TraceEvent(index=10, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=11, event_type="turn_started", content="turn started  turn_number=2"),
        TraceEvent(index=12, event_type="agent_message_chunk", content="updated the body"),
        TraceEvent(index=13, event_type="turn_completed", content="turn_completed  prompt_id=b"),
        TraceEvent(index=14, event_type="turn_ended", content="turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_index for s in segs] == [0, 1, 2]
    assert [s.turn_number for s in segs] == [0, 1, 2]
    assert turn_index_for_event(segs, 9) == 2
    assert turn_index_for_event(segs, 12) == 2
    assert turn_index_for_event(segs, 6) == 1
    assert any(e.content == "add abuse examples" for e in segs[2].events)
    assert any(e.content == "updated the body" for e in segs[2].events)


def test_event_map_follows_enclosing_turn_started_number() -> None:
    """Every event's display id is the last turn_started.turn_number."""
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="turn started  turn_number=0"),
        TraceEvent(index=1, event_type="user_message_chunk", content="watch it"),
        TraceEvent(index=2, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="turn started  turn_number=1"),
        TraceEvent(
            index=4,
            event_type="tool_call",
            tool_name="get_command_or_subagent_output",
            timestamp=1_000_040,
        ),
        TraceEvent(index=5, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=6, event_type="turn_started", content="turn started  turn_number=4"),
        TraceEvent(index=7, event_type="user_message_chunk", content="next ask"),
        TraceEvent(index=8, event_type="turn_ended", content="turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_index for s in segs] == [0, 1, 2]
    assert [s.turn_number for s in segs] == [0, 1, 4]
    mapped = event_display_turn_map(segs)
    assert mapped[0] == 0
    assert mapped[1] == 0
    assert mapped[3] == 1
    assert mapped[4] == 1
    assert mapped[6] == 4
    assert mapped[7] == 4
    assert turn_index_for_event(segs, 4) == 1
    assert turn_index_for_event(segs, 7) == 4
    assert set(mapped.values()) == {0, 1, 4}


def test_restamp_keeps_unique_list_id_and_trace_label() -> None:
    """Two turn_started turn_number=23 rows stay two segments."""
    tl = [
        TraceEvent(index=0, event_type="turn_started", content="turn started  turn_number=23"),
        TraceEvent(index=1, event_type="user_message_chunk", content="first"),
        TraceEvent(index=2, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="turn started  turn_number=23"),
        TraceEvent(index=4, event_type="user_message_chunk", content="second"),
        TraceEvent(index=5, event_type="turn_ended", content="turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_index for s in segs] == [0, 1]
    assert [s.turn_number for s in segs] == [23, 23]
    assert segs[0].label.startswith("turn 23")
    assert segs[1].label.startswith("turn 23")
    mapped = event_display_turn_map(segs)
    assert mapped[1] == 23
    assert mapped[4] == 23


def test_display_filter_keeps_each_start_number() -> None:
    """Each picker row filters to its own ``turn_started`` number."""
    from groket.session.turns import events_on_display_turn

    tl = [
        TraceEvent(index=0, event_type="turn_started", content="turn started  turn_number=12"),
        TraceEvent(index=1, event_type="user_message_chunk", content="watch it"),
        TraceEvent(index=2, event_type="turn_ended", content="turn ended  outcome=completed"),
        TraceEvent(index=3, event_type="turn_started", content="turn started  turn_number=13"),
        TraceEvent(
            index=4,
            event_type="tool_call",
            tool_name="get_command_or_subagent_output",
            timestamp=1_000_040,
        ),
        TraceEvent(index=5, event_type="turn_ended", content="turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [12, 13]
    mapped = event_display_turn_map(segs)
    assert {e.index for e in events_on_display_turn(segs[0], mapped)} == {0, 1, 2}
    assert {e.index for e in events_on_display_turn(segs[1], mapped)} == {3, 4, 5}
    assert mapped[4] == 13


def test_host_only_stamps_list_position() -> None:
    """No turn_started: face ids are 0, 1 from list order."""
    from groket.session.turns import display_turn_number

    tl = [
        _ev(0, "user_message_chunk", "first ask"),
        _ev(1, "agent_message_chunk", "ok"),
        _ev(2, "turn_completed", "turn_completed  prompt_id=a"),
        _ev(3, "user_message_chunk", "second ask"),
        _ev(4, "turn_completed", "turn_completed  prompt_id=b"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_index for s in segs] == [0, 1]
    assert [s.turn_number for s in segs] == [0, 1]
    assert [display_turn_number(s) for s in segs] == [0, 1]
    mapped = event_display_turn_map(segs)
    assert mapped[0] == 0
    assert mapped[3] == 1
    assert set(mapped.values()) == {0, 1}


def test_startless_operator_turn_stays_unnumbered() -> None:
    """A follow-up with no turn_started must not invent prev+1."""
    from groket.session.turns import display_turn_number, events_on_display_turn

    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "user_message_chunk", "first ask"),
        _ev(2, "turn_ended", "turn ended  outcome=completed"),
        _ev(3, "user_message_chunk", "orphan follow-up"),
        _ev(4, "agent_message_chunk", "reply without a start"),
        _ev(5, "turn_completed", "turn_completed  prompt_id=b"),
        _ev(6, "turn_started", "turn started  turn_number=2"),
        _ev(7, "user_message_chunk", "later ask"),
        _ev(8, "turn_ended", "turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert len(segs) == 3
    assert [s.turn_index for s in segs] == [0, 1, 2]
    assert [s.turn_number for s in segs] == [0, None, 2]
    assert display_turn_number(segs[1]) is None
    assert segs[1].label.startswith("unnumbered")
    mapped = event_display_turn_map(segs)
    assert mapped[1] == 0
    assert mapped[7] == 2
    assert 3 not in mapped
    assert 4 not in mapped
    assert set(mapped.values()) == {0, 2}
    kept = {e.index for e in events_on_display_turn(segs[1], mapped)}
    assert kept == {3, 4, 5}


def test_fork_late_start_is_its_own_row() -> None:
    """Host fork: parent replay has no starts; late turn_started is turn 13."""
    from groket.session.turns import display_turn_number

    tl = [
        _ev(0, "user_message_chunk", "parent ask"),
        _ev(1, "agent_message_chunk", "parent reply"),
        _ev(2, "turn_completed", "turn_completed  prompt_id=a"),
        _ev(3, "user_message_chunk", "continuation ask"),
        _ev(4, "agent_message_chunk", "continuation reply"),
        _ev(5, "turn_completed", "turn_completed  prompt_id=b"),
        _ev(6, "session_recap", "session recap"),
        _ev(7, "turn_started", "turn started  turn_number=13"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [None, None, 13]
    assert display_turn_number(segs[0]) is None
    assert display_turn_number(segs[1]) is None
    assert display_turn_number(segs[2]) == 13
    mapped = event_display_turn_map(segs)
    assert 0 not in mapped
    assert 3 not in mapped
    assert mapped[7] == 13
    assert set(mapped.values()) == {13}


def test_gap_in_start_numbers_is_not_filled() -> None:
    """Starts 0 then 2: no invented face 1."""
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "turn_ended", "turn ended  outcome=completed"),
        _ev(3, "turn_started", "turn started  turn_number=2"),
        _ev(4, "user_message_chunk", "skipped one"),
        _ev(5, "turn_ended", "turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert [s.turn_number for s in segs] == [0, 2]
    assert set(event_display_turn_map(segs).values()) == {0, 2}


def test_user_prompt_preview_skips_harness_and_returns_operator() -> None:
    segs = segment_timeline_turns(
        [
            _ev(0, "user_message_chunk", "<system-reminder>ignore</system-reminder>"),
            _ev(1, "user_message_chunk", "<user_query>please fix</user_query>"),
            _ev(2, "agent_message_chunk", "ok"),
        ]
    )
    text, idx = segs[0].user_prompt_preview()
    assert text == "please fix"
    assert idx == 1


def test_user_prompt_preview_empty_when_no_operator() -> None:
    segs = segment_timeline_turns(
        [
            _ev(0, "agent_message_chunk", "only assistant"),
        ]
    )
    text, idx = segs[0].user_prompt_preview()
    assert text == ""
    assert idx is None


def test_assistant_preview_takes_last_nonempty() -> None:
    segs = segment_timeline_turns(
        [
            _ev(0, "user_message_chunk", "<user_query>hi</user_query>"),
            _ev(1, "agent_message_chunk", "first"),
            _ev(2, "agent_message_chunk", ""),
            _ev(3, "agent_message_chunk", "last wrap"),
        ]
    )
    text, idx = segs[0].assistant_preview()
    assert text == "last wrap"
    assert idx == 3


def test_assistant_preview_caps_long_text() -> None:
    long_text = "A" * 80
    segs = segment_timeline_turns(
        [
            _ev(0, "agent_message_chunk", long_text),
        ]
    )
    text, idx = segs[0].assistant_preview(max_chars=20)
    assert text.endswith("…")
    assert len(text) == 20
    assert idx == 0


def test_turn_indices_for_preserves_order_and_drops_unknown() -> None:
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=2"),
        _ev(1, "user_message_chunk", "<user_query>a</user_query>"),
        _ev(2, "turn_ended", "turn ended  outcome=completed"),
        _ev(3, "turn_started", "turn started  turn_number=5"),
        _ev(4, "user_message_chunk", "<user_query>b</user_query>"),
        _ev(5, "turn_ended", "turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert TurnSegment.turn_indices_for(segs, [4, 1, 4, 99]) == [5, 2]


def test_indexes_for_prompt_includes_same_turn_tools() -> None:
    u0 = _ev(1, "user_message_chunk", "<user_query>first</user_query>")
    u0.prompt_index = 2
    tool = _ev(2, "tool_call", tool="grep")
    u1 = _ev(5, "user_message_chunk", "<user_query>second</user_query>")
    u1.prompt_index = 3
    tl = [
        _ev(0, "turn_started", "turn started  turn_number=0"),
        u0,
        tool,
        _ev(3, "turn_ended", "turn ended  outcome=completed"),
        _ev(4, "turn_started", "turn started  turn_number=1"),
        u1,
        _ev(6, "turn_ended", "turn ended  outcome=completed"),
    ]
    segs = segment_timeline_turns(tl)
    assert 2 in TurnSegment.indexes_for_prompt(segs, 2)
    assert 5 not in TurnSegment.indexes_for_prompt(segs, 2)
