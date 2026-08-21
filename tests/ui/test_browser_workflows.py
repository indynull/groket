"""Summary Workflows table jumps Timeline to that bookend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.ui.data_table import cursor_row_key
from groket.ui.screens.browser import BrowserScreen
from groket.ui.widgets.detail_view import DetailView
from groket.ui.widgets.timeline import TimelineTable
from textual.app import App, ComposeResult
from textual.widgets import DataTable
from textual.widgets.data_table import RowKey

from .pilot_helpers import wait_until


def _write_run(
    sd: Path,
    run_id: str,
    *,
    name: str,
    status: str = "complete",
    pause_message: str = "",
    agents: list[dict[str, object]] | None = None,
) -> None:
    d = sd / "workflows" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(
        json.dumps(
            {
                "version": 4,
                "state": {
                    "run_id": run_id,
                    "name": name,
                    "status": status,
                    "current_phase": "Kickoff",
                    "objective": "Engineering sprint",
                    "agents_used": 1,
                    "agent_budget": 64,
                    "elapsed_ms_floor": 1500,
                    "pause_message": pause_message,
                    "agents": agents or [],
                },
            }
        ),
        encoding="utf-8",
    )


def _write_workflow_session(root: Path, *, include_result: bool) -> Path:
    sd = root / "sess-wf"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-wf"}, "generated_title": "Workflow sess"}),
        encoding="utf-8",
    )
    updates: list[dict[str, object]] = [
        {
            "timestamp": 1,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "run the sprint"},
                    "_meta": {"promptIndex": 1},
                }
            },
        },
        {
            "timestamp": 2,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "starting"},
                }
            },
        },
        {
            "timestamp": 3,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "now workflow"},
                    "_meta": {"promptIndex": 2},
                }
            },
        },
        {
            "timestamp": 4,
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-wf",
                    "title": "workflow",
                    "rawInput": {
                        "script_path": "/repo/.grok/workflows/sprint.rhai",
                        "args": {"sprint_goal": "ship it"},
                    },
                }
            },
        },
    ]
    if include_result:
        updates.append(
            {
                "timestamp": 5,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": "call-wf",
                        "status": "completed",
                        "title": "Workflow: sprint-8",
                        "rawOutput": {
                            "type": "Workflow",
                            "run_id": "wf_sprint8",
                            "name": "sprint-8",
                            "script_path": "/repo/.grok/workflows/sprint.rhai",
                        },
                    }
                },
            }
        )
    (sd / "updates.jsonl").write_text("\n".join(json.dumps(u) for u in updates) + "\n")
    _write_run(sd, "wf_sprint8", name="sprint-8")
    return sd


class _Host(App[None]):
    def __init__(self, session: Path) -> None:
        super().__init__()
        self._session = session
        self.opened: Path | None = None

    def compose(self) -> ComposeResult:
        yield BrowserScreen(self._session)

    def open_session_path(self, path: Path) -> None:
        self.opened = path


async def _click_first_workflow(pilot, screen: BrowserScreen) -> None:
    screen.action_tab_summary()
    await wait_until(
        pilot,
        lambda: screen.query_one("#stats-workflows-table").row_count >= 1,
        description="workflows table filled",
    )
    table = screen.query_one("#stats-workflows-table", DataTable)
    table.post_message(DataTable.RowSelected(table, cursor_row=0, row_key=RowKey("wf-0")))


@pytest.mark.asyncio
async def test_summary_workflow_click_lands_cursor_on_bookend(tmp_path: Path) -> None:
    sd = _write_workflow_session(tmp_path, include_result=True)
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        first_ev = screen.timeline[0]
        wf_ev = next(e for e in screen.timeline if e.tool_name == "workflow")
        assert first_ev.event_type == "user_message_chunk"
        assert int(wf_ev.index) != int(first_ev.index)
        await _click_first_workflow(pilot, screen)
        await wait_until(
            pilot,
            lambda: (
                screen._current_event is not None
                and (screen._current_event.tool_name or "") == "workflow"
                and cursor_row_key(screen.query_one("#timeline-list", TimelineTable))
                == str(wf_ev.index)
            ),
            description="timeline cursor on the workflow bookend",
        )
        screen.action_timeline_up()
        await wait_until(
            pilot,
            lambda: (
                screen._current_event is not None
                and int(screen._current_event.index) != int(first_ev.index)
            ),
            description="up stays near the workflow, not the first prompt",
        )
        detail = screen.query_one("#detail-panel", DetailView)
        plain = detail.get_plain_text()
        assert "sprint" in plain.lower()
        assert "Asked" in plain
        assert "Happened" in plain


@pytest.mark.asyncio
async def test_summary_workflow_click_pairs_name_when_run_id_missing(tmp_path: Path) -> None:
    sd = _write_workflow_session(tmp_path, include_result=False)
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        wf_ev = next(e for e in screen.timeline if e.tool_name == "workflow")
        assert not wf_ev.raw_input.as_str("run_id")
        await _click_first_workflow(pilot, screen)
        await wait_until(
            pilot,
            lambda: (
                screen._current_event is not None
                and cursor_row_key(screen.query_one("#timeline-list", TimelineTable))
                == str(wf_ev.index)
            ),
            description="name match still lands the timeline cursor",
        )


@pytest.mark.asyncio
async def test_summary_workflow_click_clears_view_that_hides_bookend(tmp_path: Path) -> None:
    sd = _write_workflow_session(tmp_path, include_result=True)
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        wf_ev = next(e for e in screen.timeline if e.tool_name == "workflow")
        screen._timeline_filter = "user"
        screen._apply_timeline_filters()
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert cursor_row_key(tl) != str(wf_ev.index) or tl.row_count == 0
        await _click_first_workflow(pilot, screen)
        await wait_until(
            pilot,
            lambda: (
                cursor_row_key(screen.query_one("#timeline-list", TimelineTable))
                == str(wf_ev.index)
            ),
            description="jump reveals the workflow row",
        )
        assert screen._timeline_filter == "all"


@pytest.mark.asyncio
async def test_workflow_inspect_yank_has_labeled_fields(tmp_path: Path) -> None:
    sd = _write_workflow_session(tmp_path, include_result=True)
    _write_run(
        sd,
        "wf_sprint8",
        name="sprint-8",
        status="failed",
        pause_message="Variable not found: vissue_root",
    )
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        await _click_first_workflow(pilot, screen)
        detail = screen.query_one("#detail-panel", DetailView)
        await wait_until(
            pilot,
            lambda: "Asked" in detail.get_plain_text(),
            description="inspect shows Asked",
        )
        plain = detail.get_plain_text()
        assert "Asked" in plain
        assert "Happened" in plain
        assert "Failed" in plain
        assert "vissue_root" in plain


@pytest.mark.asyncio
async def test_workflow_child_row_opens_child_session(tmp_path: Path) -> None:
    sd = _write_workflow_session(tmp_path, include_result=True)
    child = tmp_path / "01aaa-aik"
    child.mkdir()
    (child / "summary.json").write_text(
        json.dumps({"info": {"id": "01aaa-aik"}, "generated_title": "aik"}),
        encoding="utf-8",
    )
    (child / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    _write_run(
        sd,
        "wf_sprint8",
        name="sprint-8",
        agents=[{"agent_id": "01aaa-aik", "label": "aik", "state": "done"}],
    )
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        await _click_first_workflow(pilot, screen)
        kids = screen.query_one("#workflow-children-table", DataTable)
        await wait_until(pilot, lambda: kids.row_count >= 1, description="child rows")
        kids.post_message(DataTable.RowSelected(kids, cursor_row=0, row_key=RowKey("wfchild-0")))
        await wait_until(pilot, lambda: app.opened == child, description="opened workflow child")


@pytest.mark.asyncio
async def test_workflow_child_without_session_stays_put(tmp_path: Path) -> None:
    sd = _write_workflow_session(tmp_path, include_result=True)
    _write_run(
        sd,
        "wf_sprint8",
        name="sprint-8",
        agents=[{"agent_id": "01aaa-missing", "label": "ghost", "state": "done"}],
    )
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        await _click_first_workflow(pilot, screen)
        kids = screen.query_one("#workflow-children-table", DataTable)
        await wait_until(pilot, lambda: kids.row_count >= 1, description="child rows")
        assert "complete" in str(kids.get_row_at(0))
        kids.post_message(DataTable.RowSelected(kids, cursor_row=0, row_key=RowKey("wfchild-0")))
        await wait_until(pilot, lambda: True, description="row selected")
        assert app.opened is None


@pytest.mark.asyncio
async def test_workflow_child_esc_returns_to_parent_browser(tmp_path: Path) -> None:
    from .test_pilot_app_coverage import _make_app

    app, _work, traces = _make_app(tmp_path, n_sessions=1)
    parent = traces / "sess-000"
    child = traces / "01aaa-aik"
    child.mkdir()
    (child / "summary.json").write_text(
        json.dumps({"info": {"id": "01aaa-aik"}, "generated_title": "aik"}),
        encoding="utf-8",
    )
    (child / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        app.open_session_path(parent)
        await wait_until(
            pilot,
            lambda: sum(1 for s in app.screen_stack if isinstance(s, BrowserScreen)) == 1,
            description="parent browser",
        )
        parent_screen = next(s for s in app.screen_stack if isinstance(s, BrowserScreen))
        app.open_session_path(child)
        await wait_until(
            pilot,
            lambda: sum(1 for s in app.screen_stack if isinstance(s, BrowserScreen)) == 2,
            description="child browser pushed",
        )
        top = app.screen
        assert isinstance(top, BrowserScreen)
        assert top.session_dir == child
        for _ in range(3):
            if app.screen is parent_screen:
                break
            await pilot.press("escape")
        await wait_until(
            pilot,
            lambda: app.screen is parent_screen,
            description="Esc pops to parent browser",
        )
        assert parent_screen.session_dir == parent


@pytest.mark.asyncio
async def test_workflow_summary_uses_product_status_words(tmp_path: Path) -> None:
    sd = _write_workflow_session(tmp_path, include_result=True)
    _write_run(sd, "wf_sprint8", name="sprint-8", status="failed")
    _write_run(sd, "wf_between", name="between", status="interrupted")
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        screen.action_tab_summary()
        table = screen.query_one("#stats-workflows-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="workflow rows")
        face = " ".join(str(table.get_row_at(i)) for i in range(table.row_count))
        assert "failed" in face
        assert "cancelled" in face
        assert "interrupted" not in face
