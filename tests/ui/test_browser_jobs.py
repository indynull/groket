"""Summary Background table and timeline filter for session jobs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from groket.session.control_views import build_session_overview, build_session_timeline
from groket.ui.data_table import cursor_row_key
from groket.ui.screens.browser import BrowserScreen
from groket.ui.widgets.detail_view import DetailView
from groket.ui.widgets.timeline import TimelineTable
from textual.app import App, ComposeResult
from textual.widgets import DataTable
from textual.widgets.data_table import RowKey

from .pilot_helpers import wait_until


def _write_jobs_session(root: Path) -> Path:
    sd = root / "sess-bg"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-bg"}, "generated_title": "Jobs sess"}),
        encoding="utf-8",
    )
    term = sd / "terminal"
    term.mkdir()
    mon = term / "monitor-call.log"
    mon.write_text("DONE\n", encoding="utf-8")
    updates = [
        {
            "timestamp": 1,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "watch"},
                    "_meta": {"promptIndex": 1},
                }
            },
        },
        {
            "timestamp": 2,
            "params": {
                "update": {
                    "sessionUpdate": "task_backgrounded",
                    "task_id": "job-1",
                    "command": "bash watch.sh",
                    "cwd": "/tmp",
                    "output_file": str(mon),
                    "description": "Watch board",
                }
            },
        },
        {
            "timestamp": 3,
            "params": {
                "update": {
                    "sessionUpdate": "scheduled_task_created",
                    "task_id": "sched-1",
                    "prompt": "hourly ping",
                    "human_schedule": "every 1 hour",
                    "next_fire_at": "2026-08-18T23:00:00Z",
                }
            },
        },
    ]
    (sd / "updates.jsonl").write_text("\n".join(json.dumps(u) for u in updates) + "\n")
    return sd


class _Host(App[None]):
    def __init__(self, session: Path) -> None:
        super().__init__()
        self._session = session

    def compose(self) -> ComposeResult:
        yield BrowserScreen(self._session, plugin_results={})


@pytest.mark.asyncio
async def test_summary_background_table_and_filter(tmp_path: Path) -> None:
    sd = _write_jobs_session(tmp_path)
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        await wait_until(
            pilot,
            lambda: bool(screen._session_jobs.jobs and screen._session_jobs.schedules),
            description="session jobs merged",
        )
        screen._stop_live_refresh()
        screen.action_tab_summary()
        await wait_until(
            pilot,
            lambda: screen.query_one("#stats-jobs-table").row_count >= 2,
            description="jobs table filled",
        )
        table = screen.query_one("#stats-jobs-table")
        cells = [str(cell) for i in range(table.row_count) for cell in table.get_row_at(i)]
        joined = " ".join(cells).lower()
        assert "watch board" in joined or "monitor" in joined
        assert "hourly ping" in joined or "schedule" in joined
        assert "subagent" not in joined
        from groket import event_types as et

        screen._apply_filter(event_types=set(et.TASK_TYPES))
        tl = screen.query_one("#timeline-list")
        assert tl.row_count == 2


@pytest.mark.asyncio
async def test_summary_job_click_opens_that_job_log_tail(tmp_path: Path) -> None:
    sd = _write_jobs_session(tmp_path)
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        job_ev = next(e for e in screen.timeline if e.event_type == "task_backgrounded")
        first_ev = screen.timeline[0]
        assert first_ev.event_type != "task_backgrounded"
        screen.action_tab_summary()
        await wait_until(
            pilot,
            lambda: screen.query_one("#stats-jobs-table").row_count >= 2,
            description="jobs table filled",
        )
        table = screen.query_one("#stats-jobs-table", DataTable)
        table.post_message(DataTable.RowSelected(table, cursor_row=0, row_key=RowKey("job-0")))
        await wait_until(
            pilot,
            lambda: (
                screen._current_event is not None
                and screen._current_event.event_type == "task_backgrounded"
                and cursor_row_key(screen.query_one("#timeline-list", TimelineTable))
                == str(job_ev.index)
            ),
            description="timeline cursor on the job bookend",
        )
        ev = screen._current_event
        assert ev is not None
        assert ev.index == job_ev.index
        assert ev.raw_input.as_str("task_id") == "job-1"
        detail = screen.query_one("#detail-panel", DetailView)
        await wait_until(
            pilot,
            lambda: "DONE" in detail.get_plain_text(),
            description="detail shows monitor log tail",
        )
        plain = detail.get_plain_text()
        assert "bash watch.sh" in plain
        assert "DONE" in plain


@pytest.mark.asyncio
async def test_summary_schedule_click_opens_timeline_bookend(tmp_path: Path) -> None:
    sd = _write_jobs_session(tmp_path)
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        sched_ev = next(e for e in screen.timeline if e.event_type == "scheduled_task_created")
        screen.action_tab_summary()
        await wait_until(
            pilot,
            lambda: screen.query_one("#stats-jobs-table").row_count >= 2,
            description="jobs table filled",
        )
        table = screen.query_one("#stats-jobs-table", DataTable)
        table.post_message(DataTable.RowSelected(table, cursor_row=1, row_key=RowKey("sched-0")))
        await wait_until(
            pilot,
            lambda: (
                screen._current_event is not None
                and screen._current_event.event_type == "scheduled_task_created"
                and cursor_row_key(screen.query_one("#timeline-list", TimelineTable))
                == str(sched_ev.index)
            ),
            description="timeline cursor on the schedule bookend",
        )
        ev = screen._current_event
        assert ev is not None
        assert ev.index == sched_ev.index
        assert ev.raw_input.as_str("task_id") == "sched-1"


@pytest.mark.asyncio
async def test_status_only_overview_refresh_paints_jobs_table(tmp_path: Path) -> None:
    sd = tmp_path / "sess-live"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-live"}, "generated_title": "live"}),
        encoding="utf-8",
    )
    term = sd / "terminal"
    term.mkdir()
    mon = term / "monitor-call-live.log"
    mon.write_text("still going\n", encoding="utf-8")
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 2,
                "params": {
                    "update": {
                        "sessionUpdate": "task_backgrounded",
                        "task_id": "job-live",
                        "command": "watch",
                        "cwd": "/tmp",
                        "output_file": str(mon),
                        "description": "live watch",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            return build_session_overview(sd)

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            return build_session_timeline(
                sd,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 50),
                at_index=kwargs.get("at_index")
                if isinstance(kwargs.get("at_index"), int)
                else None,
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    class _Attached(_Host):
        def is_control_client(self) -> bool:
            return True

        def session_access(self) -> _Access:
            return _Access()

    app = _Attached(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        screen.action_tab_summary()
        await wait_until(
            pilot,
            lambda: screen.query_one("#stats-jobs-table").row_count >= 1,
            description="jobs table filled",
        )
        before = " ".join(
            str(c) for c in screen.query_one("#stats-jobs-table").get_row_at(0)
        ).lower()
        assert "running" in before
        mon.write_text("still going\nDONE\n", encoding="utf-8")
        # Light refresh is a pool worker: asyncio.run(overview) cannot run
        # on the Textual loop.
        await asyncio.to_thread(screen._load_data_light_job)
        await wait_until(
            pilot,
            lambda: (
                "complete"
                in " ".join(
                    str(c) for c in screen.query_one("#stats-jobs-table").get_row_at(0)
                ).lower()
            ),
            description="jobs table shows complete after status-only overview",
        )
        assert screen._session_jobs.jobs[0].status == "done"


@pytest.mark.asyncio
async def test_summary_t_cycles_session_and_tasks(tmp_path: Path) -> None:
    from textual.widgets import TabbedContent

    sd = _write_jobs_session(tmp_path)
    app = _Host(sd)
    async with app.run_test(size=(140, 48)) as pilot:
        screen = app.query_one(BrowserScreen)
        await wait_until(pilot, lambda: bool(screen.timeline), description="timeline loaded")
        screen._stop_live_refresh()
        screen.action_tab_summary()
        await wait_until(
            pilot,
            lambda: screen.query_one("#summary-tabs", TabbedContent).active == "summary-session",
            description="session section",
        )
        await wait_until(
            pilot,
            lambda: screen.query_one("#stats-jobs-table").row_count >= 2,
            description="jobs table filled",
        )
        screen.action_overview_section()
        await wait_until(
            pilot,
            lambda: screen.query_one("#summary-tabs", TabbedContent).active == "summary-tasks",
            description="tasks section",
        )
        assert screen.query_one("#stats-jobs-table").row_count >= 2
        screen.action_overview_section()
        await wait_until(
            pilot,
            lambda: screen.query_one("#summary-tabs", TabbedContent).active == "summary-workflows",
            description="workflows section",
        )
