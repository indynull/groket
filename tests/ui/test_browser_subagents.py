"""Parent browser lists subagent runs and opens the child session."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.models import TraceEvent
from groket.ui.screens.browser import BrowserScreen
from groket.ui.widgets.timeline import TimelineTable
from textual.app import App, ComposeResult
from textual.widgets import DataTable, Static
from textual.widgets.data_table import RowKey

from .pilot_helpers import static_plain, wait_until


def _write_parent_with_child(root: Path) -> tuple[Path, Path]:
    parent = root / "parent-sub"
    child = root / "child-sub"
    parent.mkdir(parents=True)
    child.mkdir(parents=True)
    (parent / "summary.json").write_text(
        json.dumps({"info": {"id": "parent-sub"}, "generated_title": "Parent"}),
        encoding="utf-8",
    )
    (child / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "child-sub"},
                "generated_title": "Child",
                "session_kind": "subagent",
            }
        ),
        encoding="utf-8",
    )
    (child / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    (parent / "subagents" / "child-sub").mkdir(parents=True)
    (parent / "subagents" / "child-sub" / "meta.json").write_text(
        json.dumps(
            {
                "child_session_id": "child-sub",
                "subagent_type": "coder",
                "description": "worker",
                "status": "completed",
            }
        ),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "go"},
                    "_meta": {"promptIndex": 1},
                }
            },
        },
        {
            "timestamp": 2,
            "params": {
                "update": {
                    "sessionUpdate": "subagent_spawned",
                    "description": "worker",
                    "subagentType": "coder",
                    "childSessionId": "child-sub",
                    "parentPromptId": "1",
                }
            },
        },
        {
            "timestamp": 3,
            "params": {
                "update": {
                    "sessionUpdate": "subagent_finished",
                    "childSessionId": "child-sub",
                    "status": "completed",
                    "durationMs": 250,
                }
            },
        },
    ]
    (parent / "updates.jsonl").write_text(
        "\n".join(json.dumps(u) for u in updates) + "\n",
        encoding="utf-8",
    )
    return parent, child


class _Host(App[None]):
    opened: Path | None = None

    def __init__(self, parent: Path) -> None:
        super().__init__()
        self._session = parent
        self.opened = None

    def compose(self) -> ComposeResult:
        yield BrowserScreen(self._session, plugin_results={})

    def open_session_path(self, session_dir: Path | str, **_kwargs: object) -> None:
        self.opened = Path(session_dir)


@pytest.mark.asyncio
async def test_browser_lists_runs_and_opens_child(tmp_path: Path) -> None:
    parent, child = _write_parent_with_child(tmp_path)
    app = _Host(parent)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        await wait_until(
            pilot,
            lambda: any(
                r.child_session_id == "child-sub" and r.openable for r in screen._subagent_runs
            ),
            description="openable child-sub run",
        )
        screen._stop_live_refresh()
        screen.action_tab_summary()
        await wait_until(
            pilot,
            lambda: screen.query_one("#stats-subagents-table").row_count >= 1,
            description="subagent table filled",
        )
        table = screen.query_one("#stats-subagents-table")
        cells = [str(cell) for cell in table.get_row_at(0)]
        assert any("worker" in c or "coder" in c for c in cells)
        spawn = next(e for e in screen.timeline if e.event_type == "subagent_spawned")
        assert spawn.raw_input.as_str("childSessionId") == "child-sub"
        screen._current_event = spawn
        screen.action_open_subagent()
        assert app.opened == child


@pytest.mark.asyncio
async def test_one_turn_child_hides_summary_turns_card(tmp_path: Path) -> None:
    child = tmp_path / "child-one"
    child.mkdir()
    (child / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "child-one"},
                "generated_title": "Child",
                "session_kind": "subagent",
            }
        ),
        encoding="utf-8",
    )
    (child / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "do it"},
                        "_meta": {"promptIndex": 1},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (child / "events.jsonl").write_text(
        json.dumps({"type": "turn_started", "turn_number": 1, "ts": 1}) + "\n",
        encoding="utf-8",
    )
    app = _Host(child)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="child timeline")
        screen._stop_live_refresh()
        screen._set_title_from_meta()
        chrome = static_plain(screen.query_one("#app-chrome-title", Static))
        assert "Subagent" in chrome
        assert "Child" in chrome
        screen.action_tab_summary()
        glance = screen.query_one("#summary-content")
        assert glance is not None


@pytest.mark.asyncio
async def test_child_chrome_includes_parent_title(tmp_path: Path) -> None:
    _parent, child = _write_parent_with_child(tmp_path)
    app = _Host(child)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: screen.meta is not None, description="child meta")
        screen._stop_live_refresh()
        screen._set_title_from_meta()
        chrome = static_plain(screen.query_one("#app-chrome-title", Static))
        assert "Parent" in chrome
        assert "Subagent" in chrome
        assert "Child" in chrome


def _write_parent_with_two_children(root: Path) -> tuple[Path, Path, Path]:
    parent = root / "parent-two"
    child_a = root / "child-a"
    child_b = root / "child-b"
    parent.mkdir(parents=True)
    for child, title in ((child_a, "Child A"), (child_b, "Child B")):
        child.mkdir(parents=True)
        (child / "summary.json").write_text(
            json.dumps(
                {
                    "info": {"id": child.name},
                    "generated_title": title,
                    "session_kind": "subagent",
                }
            ),
            encoding="utf-8",
        )
        (child / "updates.jsonl").write_text("{}\n", encoding="utf-8")
        (parent / "subagents" / child.name).mkdir(parents=True)
        (parent / "subagents" / child.name / "meta.json").write_text(
            json.dumps(
                {
                    "child_session_id": child.name,
                    "subagent_type": "coder",
                    "description": title,
                    "status": "completed",
                }
            ),
            encoding="utf-8",
        )
    (parent / "summary.json").write_text(
        json.dumps({"info": {"id": "parent-two"}, "generated_title": "Parent"}),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "go"},
                    "_meta": {"promptIndex": 1},
                }
            },
        },
        {
            "timestamp": 2,
            "params": {
                "update": {
                    "sessionUpdate": "subagent_spawned",
                    "description": "Child A",
                    "subagentType": "coder",
                    "childSessionId": "child-a",
                    "parentPromptId": "1",
                }
            },
        },
        {
            "timestamp": 3,
            "params": {
                "update": {
                    "sessionUpdate": "subagent_finished",
                    "childSessionId": "child-a",
                    "status": "completed",
                    "durationMs": 100,
                }
            },
        },
        {
            "timestamp": 4,
            "params": {
                "update": {
                    "sessionUpdate": "subagent_spawned",
                    "description": "Child B",
                    "subagentType": "coder",
                    "childSessionId": "child-b",
                    "parentPromptId": "1",
                }
            },
        },
        {
            "timestamp": 5,
            "params": {
                "update": {
                    "sessionUpdate": "subagent_finished",
                    "childSessionId": "child-b",
                    "status": "completed",
                    "durationMs": 200,
                }
            },
        },
    ]
    (parent / "updates.jsonl").write_text(
        "\n".join(json.dumps(u) for u in updates) + "\n",
        encoding="utf-8",
    )
    return parent, child_a, child_b


def _spawn_for(screen: BrowserScreen, child_id: str) -> TraceEvent:
    return next(
        e
        for e in screen.timeline
        if e.event_type == "subagent_spawned" and e.raw_input.as_str("childSessionId") == child_id
    )


@pytest.mark.asyncio
async def test_timeline_row_selected_opens_clicked_child(tmp_path: Path) -> None:
    """Enter/click uses the selected row, not the stale highlighted event."""
    parent, child_a, child_b = _write_parent_with_two_children(tmp_path)
    app = _Host(parent)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        await wait_until(
            pilot,
            lambda: screen.query("#timeline-list") and bool(screen._subagent_runs),
            description="timeline table and runs ready",
        )
        screen._stop_live_refresh()
        spawn_a = _spawn_for(screen, "child-a")
        spawn_b = _spawn_for(screen, "child-b")
        screen._current_event = spawn_a
        table = screen.query_one("#timeline-list", TimelineTable)
        table.post_message(
            DataTable.RowSelected(table, cursor_row=0, row_key=RowKey(str(spawn_b.index)))
        )
        await wait_until(pilot, lambda: app.opened == child_b, description="opened child B")
        assert app.opened != child_a


@pytest.mark.asyncio
async def test_timeline_row_selected_skips_non_bookend(tmp_path: Path) -> None:
    """A non-bookend click must not open the stale bookend's child."""
    parent, child = _write_parent_with_child(tmp_path)
    app = _Host(parent)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        spawn = next(e for e in screen.timeline if e.event_type == "subagent_spawned")
        user = next(e for e in screen.timeline if e.event_type == "user_message_chunk")
        screen._current_event = spawn
        table = screen.query_one("#timeline-list", TimelineTable)
        table.post_message(
            DataTable.RowSelected(table, cursor_row=0, row_key=RowKey(str(user.index)))
        )
        await pilot.pause()
        assert app.opened is None
