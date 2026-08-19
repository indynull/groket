"""DetailView widget tests."""

from __future__ import annotations

import pytest
from conftest import make_trace_event
from groket.analysis.base import Finding
from groket.models import Flag, FlagVerdict, Severity
from groket.ui.widgets.detail_view import DetailView
from textual.app import App, ComposeResult
from textual.widgets import Static


class _DetailApp(App):
    def compose(self) -> ComposeResult:
        yield DetailView(id="detail")


@pytest.mark.asyncio
async def test_detail_view_show_event() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
        )
        dv.show_event(ev)
        body = dv.query_one("#detail-body", Static)
        from .pilot_helpers import assert_static_contains

        assert_static_contains(body, "read file")
        assert dv._current_event is ev


@pytest.mark.asyncio
async def test_detail_same_event_skips_scroll_home() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
        )
        homes: list[int] = []
        orig = dv.scroll_home

        def _home(*_a: object, **_k: object) -> None:
            homes.append(1)
            orig(animate=False)

        dv.scroll_home = _home  # type: ignore[method-assign]
        dv.show_event(ev)
        assert homes == [1]
        dv.show_event(ev)
        assert homes == [1]


@pytest.mark.asyncio
async def test_detail_view_show_event_with_finding_and_flag() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="grep",
            raw_input={"pattern": "x"},
            tool_call_id="c1",
        )
        finding = Finding(
            id="f1",
            title="test",
            severity=Severity.HIGH,
            plugin_id="engine",
            detail="x",
        )
        flag = Flag(event_index=0, verdict=FlagVerdict.BAD, description="bad")
        dv.show_event(ev, finding=finding, flag=flag, duration=3.0)
        from .pilot_helpers import assert_static_contains

        body = dv.query_one("#detail-body", Static)
        assert_static_contains(body, "grep", "test", "bad")
        assert dv._current_finding is finding
        assert dv._current_flag is flag
        assert dv._current_duration == 3.0


@pytest.mark.asyncio
async def test_detail_view_show_event_with_pairs() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        call = make_trace_event(
            index=0,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "x.py"},
            tool_call_id="c1",
        )
        result = make_trace_event(
            index=1,
            event_type="tool_call_update",
            tool_name="read_file",
            content="file content",
            tool_call_id="c1",
        )
        dv.show_event(call, paired_call=call, paired_result=result)
        from .pilot_helpers import assert_static_contains

        body = dv.query_one("#detail-body", Static)
        assert_static_contains(body, "read file")
        assert dv._paired_call is call
        assert dv._paired_result is result


@pytest.mark.asyncio
async def test_detail_view_clear() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(index=0, event_type="user_message_chunk", content="hello")
        dv.show_event(ev)
        assert dv._current_event is not None
        dv.clear_detail()
        assert dv._current_event is None
        assert dv._current_finding is None
        assert dv._current_flag is None


@pytest.mark.asyncio
async def test_detail_view_no_event() -> None:
    app = _DetailApp()
    async with app.run_test() as pilot:
        from .pilot_helpers import wait_until

        await wait_until(
            pilot,
            lambda: bool(list(app.query("#detail-body"))),
            description="detail-body mounted",
        )
        dv = app.query_one("#detail", DetailView)
        dv._current_event = None
        dv._refresh_content()
        body = dv.query_one("#detail-body", Static)
        from .pilot_helpers import static_plain

        # No event selected: body is cleared / empty placeholder.
        assert static_plain(body).strip() == ""


def test_flag_requested_message() -> None:
    """FlagRequested message carries the originating event."""
    ev = make_trace_event(index=5, event_type="tool_call", tool_name="grep")
    msg = DetailView.FlagRequested(ev)
    assert msg.event is ev
