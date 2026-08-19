"""Turn filter dropdown rebuilds when a follow-up turn appears live."""

from __future__ import annotations

from pathlib import Path

from groket.models import TraceEvent
from groket.ui.screens.browser import BrowserScreen
from groket.ui.widgets.timeline import TimelineTable


def _ev(index: int, event_type: str, content: str = "", **kw) -> TraceEvent:
    return TraceEvent(
        index=index,
        timestamp=float(1000 + index),
        event_type=event_type,
        content=content,
        **kw,
    )


def test_rebuild_turn_select_discovers_follow_up_mid_batch(tmp_path: Path) -> None:
    """turn_started + later tool events in one load must still show multi-turn UI."""
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "agent_message_chunk", "reply"),
        _ev(3, "turn_ended", "outcome=completed"),
    ]
    screen._last_turn_segment_count = 1
    screen._turn_segments = []  # pretend already segmented once
    screen._turn_rebuild_sig = None
    screen._turn_filter = "all"

    # Capture set_options / display without mounting.
    calls: list[object] = []

    class _Sel:
        display = False
        value = "all"

        def set_options(self, options):
            calls.append(list(options))

    sel = _Sel()
    screen.query_one = lambda _q, _t=None: sel  # type: ignore[method-assign]

    # Follow-up arrived with turn_started buried under newer tool rows.
    screen.timeline = [
        *screen.timeline,
        _ev(4, "turn_started", "turn_number=1"),
        _ev(5, "user_message_chunk", "follow up please"),
        _ev(6, "tool_call", "bash"),
        _ev(7, "tool_call_update", "ok"),
    ]
    screen._rebuild_turn_select()
    assert sel.display is True
    assert screen._last_turn_segment_count == 2
    assert calls, "set_options should run when becoming multi-turn"
    values = [v for _, v in calls[-1]]
    assert "0" in values and "1" in values and "all" in values


def test_rebuild_turn_select_discovers_next_turn_when_already_multi(tmp_path: Path) -> None:
    """Already multi-turn: a new turn whose batch ends on tool_call must appear.

    Regression: early-return on non-boundary tail left turn N stuck missing
    until a full refresh (user-reported for turn 42).
    """
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    # Two completed turns already segmented.
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "turn_ended", "outcome=completed"),
        _ev(2, "turn_started", "turn_number=1"),
        _ev(3, "turn_ended", "outcome=completed"),
    ]
    screen._last_turn_segment_count = 2
    screen._turn_rebuild_sig = (4, 3)
    screen._turn_segments = [object(), object()]  # non-None placeholders
    screen._turn_filter = "all"
    calls: list[object] = []

    class _Sel:
        display = True
        value = "all"

        def set_options(self, options):
            calls.append(list(options))

    sel = _Sel()
    screen.query_one = lambda _q, _t=None: sel  # type: ignore[method-assign]

    # Turn 2 starts; live batch ends on a tool row (not turn_started).
    screen.timeline = [
        *screen.timeline,
        _ev(4, "turn_started", "turn_number=2"),
        _ev(5, "user_message_chunk", "keep going"),
        _ev(6, "tool_call", "bash"),
    ]
    screen._rebuild_turn_select()
    assert screen._last_turn_segment_count == 3
    assert calls, "set_options must run when a new turn appears"
    values = [v for _, v in calls[-1]]
    assert "0" in values and "1" in values and "2" in values
    labels = [lab for lab, _ in calls[-1]]
    # Same id as the Turn column: trace turn_number.
    assert any("2" in str(lab) for lab in labels)


def test_rebuild_turn_select_keeps_restamped_turns_distinct(tmp_path: Path) -> None:
    """Two operator turns with the same turn_number stay two picker rows."""
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=23"),
        _ev(1, "user_message_chunk", "first prompt"),
        _ev(2, "turn_ended", "outcome=completed"),
        _ev(3, "turn_started", "turn_number=23"),
        _ev(4, "user_message_chunk", "second prompt"),
        _ev(5, "turn_ended", "outcome=completed"),
    ]
    screen._last_turn_segment_count = -1
    screen._turn_segments = None
    screen._turn_rebuild_sig = None
    screen._turn_filter = "all"
    calls: list[object] = []

    class _Sel:
        display = False
        value = "all"

        def set_options(self, options):
            calls.append(list(options))

    sel = _Sel()
    screen.query_one = lambda _q, _t=None: sel  # type: ignore[method-assign]
    screen._rebuild_turn_select()
    assert calls
    labeled = [(lab, val) for lab, val in calls[-1] if val != "all"]
    assert labeled == [("Turn 23", "0"), ("Turn 23", "1")]


def test_rebuild_turn_select_labels_startless_unnumbered(tmp_path: Path) -> None:
    """A follow-up with no turn_started is Unnumbered, not an invented id."""
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "user_message_chunk", "first"),
        _ev(2, "turn_ended", "outcome=completed"),
        _ev(3, "user_message_chunk", "orphan follow-up"),
        _ev(4, "agent_message_chunk", "reply"),
        _ev(5, "turn_completed", "turn_completed  prompt_id=b"),
        _ev(6, "turn_started", "turn_number=2"),
        _ev(7, "user_message_chunk", "later"),
        _ev(8, "turn_ended", "outcome=completed"),
    ]
    screen._last_turn_segment_count = -1
    screen._turn_segments = None
    screen._turn_rebuild_sig = None
    screen._turn_filter = "all"
    calls: list[object] = []

    class _Sel:
        display = False
        value = "all"

        def set_options(self, options):
            calls.append(list(options))

    sel = _Sel()
    screen.query_one = lambda _q, _t=None: sel  # type: ignore[method-assign]
    screen._rebuild_turn_select()
    assert calls
    labeled = [(lab, val) for lab, val in calls[-1] if val != "all"]
    assert labeled == [("Turn 0", "0"), ("Unnumbered", "1"), ("Turn 2", "2")]


def test_rebuild_turn_select_skips_set_options_when_count_unchanged(tmp_path: Path) -> None:
    """Mid-turn append re-segments but does not thrash Select options."""
    sd = tmp_path / "sess"
    sd.mkdir()
    screen = BrowserScreen.__new__(BrowserScreen)
    screen.session_dir = sd
    screen.timeline = [
        _ev(0, "turn_started", "turn_number=0"),
        _ev(1, "turn_ended", "outcome=completed"),
        _ev(2, "turn_started", "turn_number=1"),
        _ev(3, "tool_call", "bash"),
    ]
    screen._last_turn_segment_count = 2
    screen._turn_rebuild_sig = (4, 3)
    screen._turn_segments = [object(), object()]
    screen._turn_filter = "all"
    calls: list[object] = []

    class _Sel:
        display = True
        value = "all"

        def set_options(self, options):
            calls.append(options)

    screen.query_one = lambda _q, _t=None: _Sel()  # type: ignore[method-assign]
    screen.timeline.append(_ev(4, "tool_call_update", "more"))
    screen._rebuild_turn_select()
    assert calls == []
    assert screen._last_turn_segment_count == 2
    # Same tail again: full no-op (no re-segment needed).
    screen._rebuild_turn_select()
    assert calls == []


def test_turn_step_available_only_on_timeline() -> None:
    """h / l belong on Timeline when there is more than one turn."""
    screen = BrowserScreen.__new__(BrowserScreen)
    screen._operator_turn_ids = lambda: [0, 4]  # type: ignore[method-assign]
    screen._active_browser_tab = lambda: "tab-timeline"  # type: ignore[method-assign]
    assert screen._turn_step_available() is True
    screen._active_browser_tab = lambda: "tab-summary"  # type: ignore[method-assign]
    assert screen._turn_step_available() is False
    screen._active_browser_tab = lambda: ""  # type: ignore[method-assign]
    assert screen._turn_step_available() is False
    screen._operator_turn_ids = lambda: [0]  # type: ignore[method-assign]
    screen._active_browser_tab = lambda: "tab-timeline"  # type: ignore[method-assign]
    assert screen._turn_step_available() is False


def test_next_prev_turn_steps_from_all_and_back() -> None:
    """l scopes the first turn; h from the first turn returns to All."""
    screen = BrowserScreen.__new__(BrowserScreen)
    screen._turn_segments = [
        type("S", (), {"turn_index": 0})(),
        type("S", (), {"turn_index": 4})(),
    ]
    screen._turn_filter = "all"
    applied: list[str] = []

    def _set(value: str) -> None:
        screen._turn_filter = value
        applied.append(value)

    screen._set_turn_filter = _set  # type: ignore[method-assign]
    assert screen._operator_turn_ids() == [0, 4]
    screen.action_next_turn()
    assert screen._turn_filter == "0"
    screen.action_next_turn()
    assert screen._turn_filter == "4"
    screen.action_next_turn()
    assert screen._turn_filter == "4"
    screen.action_prev_turn()
    assert screen._turn_filter == "0"
    screen.action_prev_turn()
    assert screen._turn_filter == "all"
    assert applied == ["0", "4", "0", "all"]


def test_prev_turn_from_all_opens_last() -> None:
    screen = BrowserScreen.__new__(BrowserScreen)
    screen._turn_segments = [
        type("S", (), {"turn_index": 0})(),
        type("S", (), {"turn_index": 2})(),
        type("S", (), {"turn_index": 5})(),
    ]
    screen._turn_filter = "all"
    screen._set_turn_filter = (  # type: ignore[method-assign]
        lambda value: setattr(screen, "_turn_filter", value)
    )
    screen.action_prev_turn()
    assert screen._turn_filter == "5"


def test_timeline_table_does_not_override_column_arrows() -> None:
    """h / Left step turns via the screen catalog binding, not the table."""
    from groket.ui.widgets.timeline import TimelineTable
    from textual.widgets import DataTable

    assert TimelineTable.action_cursor_left is DataTable.action_cursor_left
    assert TimelineTable.action_cursor_right is DataTable.action_cursor_right


def test_land_target_is_visible_row_not_hidden_first_event() -> None:
    """Turn land uses the filtered paint set, not the unfiltered first event."""
    tl = TimelineTable()
    tl.events = [
        _ev(0, "user_message_chunk", "prompt"),
        _ev(1, "tool_call", "read", tool_name="read_file"),
        _ev(3, "tool_call", "grep", tool_name="grep"),
        _ev(5, "agent_message_chunk", "done"),
    ]
    tl._visible = [tl.events[1], tl.events[2]]
    screen = BrowserScreen.__new__(BrowserScreen)
    screen._current_event = tl.events[0]
    hit = screen._land_target(tl, keep=True)
    assert hit is not None
    assert hit.index == 1
    hidden = screen._land_target(tl, keep=False)
    assert hidden is not None
    assert hidden.index in {1, 3}
    from groket.ui.data_table import restore_cursor

    assert restore_cursor(tl, "0", scroll=False) is False
    prev = screen._current_event
    # Failed restore must not be applied by _land_target itself.
    assert prev.index == 0
