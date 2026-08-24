"""TraceEvalApp import, construction, populate, and session-loading tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.parser import find_sessions, load_session_meta
from groket.ui.data_table import cursor_row_key
from textual.widgets import DataTable

from .pilot_helpers import wait_until


def _write_minimal_session(traces_root: Path, session_id: str = "sess-launch-001") -> Path:
    """Create a tiny session dir that :func:`find_sessions` / meta load accept."""
    sd = traces_root / session_id
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": session_id, "cwd": "/workspace"},
                "session_summary": "Launch smoke session",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "test-model",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "turn_ended",
                "ts": "2026-06-25T00:01:00Z",
                "outcome": "success",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return sd


def test_trace_eval_app_importable():
    """Main app module must import (catches broken Textual / package imports)."""
    from groket.ui import app as app_mod
    from groket.ui.app import TraceEvalApp
    from textual.app import App, ComposeResult, SystemCommand
    from textual.timer import Timer

    assert issubclass(TraceEvalApp, App)
    # Guard against the regression that imported ComposeResult from textual.timer.
    assert ComposeResult is not None
    assert SystemCommand is not None
    assert Timer is not None
    assert hasattr(app_mod, "TraceEvalApp")


def test_trace_eval_app_constructs(tmp_path: Path):
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    assert app.work_dir == work.resolve()
    assert app.traces_path == traces.resolve()


def test_populate_session_table_adds_row(tmp_path: Path):
    """A session with meta must still render the home table.

    Populate must not swallow programming errors — they should fail the test.
    """
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sd = _write_minimal_session(traces)
    meta = load_session_meta(sd)
    assert meta is not None

    app = TraceEvalApp(work_dir=work, traces_path=traces)
    app._meta_only = [(meta, "lab")]
    app._selected = set()
    app._populate_busy = False

    # Drive populate without full Textual run: install a minimal DataTable host.
    # Prefer run_test for realism (below); this unit path asserts the row logic.
    rows_added: list[tuple] = []

    class _FakeTable:
        def clear(self) -> None:
            rows_added.clear()

        def add_row(self, *cells, key=None):
            rows_added.append((cells, key))

        @property
        def cursor_coordinate(self):
            return None

    class _FakeStatic:
        def update(self, _content) -> None:
            return None

    class _FakeApp(TraceEvalApp):
        def query_one(self, selector, expect_type=None):  # type: ignore[no-untyped-def]  # test stub
            if selector == "#session-table":
                return _FakeTable()
            if selector == "#session-summary":
                return _FakeStatic()
            raise KeyError(selector)

    host = _FakeApp(work_dir=work, traces_path=traces)
    host._meta_only = [(meta, "lab")]
    host._selected = set()
    host._populate_busy = False
    # Avoid focus side-effects on a non-mounted widget tree.
    host._populate_session_table = (  # type: ignore[method-assign]  # test stub
        lambda **kw: host._populate_session_table_inner(**kw)
    )
    # Still exercise the real inner path (summary update included).
    host._update_summary_lazy = lambda *a, **k: None  # type: ignore[method-assign]  # test stub
    host._populate_session_table_inner()
    assert len(rows_added) == 1
    cells, key = rows_added[0]
    # sel, src, title, model, status, duration, context, events
    assert len(cells) == 8
    assert str(cells[6]) == "—"  # no context telemetry on this stub meta
    assert key == str(meta.session_dir)


def test_home_summary_is_selection_only(tmp_path: Path) -> None:
    """Home chrome lists the selection, not a session count."""
    from groket.ui.app import TraceEvalApp
    from groket.ui.i18n import t

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    seen: list[str] = []

    class _FakeStatic:
        display = True

        def update(self, content) -> None:
            seen.append(str(content))

    class _FakeApp(TraceEvalApp):
        def query_one(self, selector, expect_type=None):  # type: ignore[no-untyped-def]  # test stub
            if selector == "#session-summary":
                return _FakeStatic()
            raise KeyError(selector)

    host = _FakeApp(work_dir=work, traces_path=traces)
    host._selected = set()
    host._update_summary_lazy()
    assert seen[-1] == ""

    host._selected = {"/tmp/a", "/tmp/b"}
    host._update_summary_lazy()
    assert t("sessions-selected-count", n=2) in seen[-1]
    assert "sessions" not in seen[-1].replace("selected", "")


@pytest.mark.asyncio
async def test_home_table_omits_task_id_and_path_label(tmp_path: Path):
    """Home list shows status, not batch task id or path label."""
    from groket.ui.app import TraceEvalApp
    from groket.ui.i18n import t

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_minimal_session(traces, "sess-cols")
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(140, 30)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="session rows populated")
        headers = [str(col.label) for col in table.columns.values()]
        assert t("ui-task") not in headers
        assert t("ui-label") not in headers
        assert t("ui-session-id") not in headers
        assert t("ui-title") in headers
        assert t("ui-status") in headers
        assert t("ui-high-1") not in headers
        assert t("ui-med") not in headers


@pytest.mark.asyncio
async def test_home_table_populate_keeps_horizontal_scroll(tmp_path: Path):
    """Live repaint must not snap scroll_x back to 0."""
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_minimal_session(traces, "sess-scroll")
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(50, 24)) as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 1, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="session rows populated")
        table.scroll_x = 9
        key = cursor_row_key(table)
        meta, label = app._meta_only[0]
        meta.duration_seconds = float(meta.duration_seconds or 0) + 12
        app._meta_only[0] = (meta, label)
        app._populate_session_table()
        await pilot.pause()
        assert table.scroll_x == 9
        assert cursor_row_key(table) == key


@pytest.mark.asyncio
async def test_app_launch_lists_sessions(tmp_path: Path):
    """Full Textual pilot: mount TraceEvalApp and expect session rows."""
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_minimal_session(traces, "sess-a")
    _write_minimal_session(traces, "sess-b")
    assert len(find_sessions(traces)) >= 2

    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test() as pilot:
        await wait_until(pilot, lambda: len(app._meta_only) >= 2, description="sessions loaded")
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="session rows populated")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "key", "want"),
    [
        (0, "j", 1),
        (1, "j", 2),
        (2, "k", 1),
        (1, "k", 0),
    ],
)
async def test_session_list_click_cursor_then_jk_steps(
    tmp_path: Path, start: int, key: str, want: int
) -> None:
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_minimal_session(traces, "sess-a")
    _write_minimal_session(traces, "sess-b")
    _write_minimal_session(traces, "sess-c")
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 30)) as pilot:
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 3, description="three session rows")
        table.focus()
        table.move_cursor(row=start, animate=False)
        assert table.cursor_row == start
        clicked = cursor_row_key(table)
        assert clicked is not None
        await pilot.press(key)
        await wait_until(
            pilot,
            lambda: table.cursor_row == want,
            description=f"{key} from row {start} to {want}",
        )
        assert cursor_row_key(table) != clicked


@pytest.mark.asyncio
async def test_app_launch_empty_traces_notifies(tmp_path: Path):
    """Empty traces dir should not crash; table stays empty."""
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)

    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test() as pilot:
        # Worker thread runs and finds zero sessions; wait for it to finish.
        await wait_until(
            pilot,
            lambda: hasattr(app, "_meta_only"),
            description="worker finished (empty traces)",
        )
        table = app.query_one("#session-table", DataTable)
        assert table.row_count == 0


def test_fill_timeline_counts_ignores_stale_indices(tmp_path: Path) -> None:
    """Timeline fill must not IndexError when need_idx is out of range."""
    from groket.models import SessionMeta
    from groket.ui.app import TraceEvalApp

    meta = SessionMeta(session_id="s", session_dir=tmp_path / "s", origin="work")
    rows = [(meta, "lab")]
    # Out-of-range and valid indices — only valid apply.
    assert TraceEvalApp._fill_timeline_counts(rows, [0, 99, -1]) is True
    assert len(rows) == 1


def test_fill_timeline_counts_skips_host_origin(tmp_path: Path) -> None:
    """Host rows never trigger multi-MB parse_timeline on catalog load."""
    from groket.models import SessionMeta
    from groket.ui.app import TraceEvalApp

    meta = SessionMeta(
        session_id="h",
        session_dir=tmp_path / "h",
        origin="host",
        num_events=0,
        num_messages=12,
    )
    rows = [(meta, "lab")]
    assert TraceEvalApp._fill_timeline_counts(rows, [0]) is False
    assert rows[0][0].num_events == 0


def test_sessions_load_gen_supersedes(tmp_path: Path) -> None:
    """A newer catalog load must supersede an older apply."""
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    work.mkdir()
    app = TraceEvalApp(work_dir=work, traces_path=work / "runs" / "traces")
    g1 = app._begin_sessions_load()
    g2 = app._begin_sessions_load()
    assert g2 > g1
    assert not app._sessions_load_current(g1)
    assert app._sessions_load_current(g2)
    from groket.models import SessionMeta

    rows = [(SessionMeta(session_id="a", session_dir=tmp_path / "a"), "a")]
    assert app._apply_session_meta_rows(g1, rows) is False
    assert app._meta_only == []
    assert app._apply_session_meta_rows(g2, rows) is True
    assert len(app._meta_only) == 1


def test_drop_host_session_rows(tmp_path: Path) -> None:
    """Hiding host drops origin=host rows without waiting for a full rescan."""
    from groket.models import SessionMeta
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    work.mkdir()
    app = TraceEvalApp(work_dir=work, traces_path=work / "runs" / "traces")
    app._meta_only = [
        (SessionMeta(session_id="w", session_dir=tmp_path / "w", origin="work"), "w"),
        (SessionMeta(session_id="h", session_dir=tmp_path / "h", origin="host"), "h"),
    ]
    app._drop_host_session_rows()
    assert len(app._meta_only) == 1
    assert app._meta_only[0][0].origin == "work"


def test_load_sessions_sync_clears_when_empty(tmp_path: Path) -> None:
    """Empty catalog must clear a prior list (not leave arbitrary rows)."""
    from groket.models import SessionMeta
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    work.mkdir()
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    app._meta_only = [
        (SessionMeta(session_id="stale", session_dir=tmp_path / "stale", origin="host"), "x"),
    ]
    n = app._load_sessions_sync()
    assert n == 0
    assert app._meta_only == []


def test_tui_control_client_uses_heavy_rpc_timeout(tmp_path: Path) -> None:
    from groket.integrations.control_client import HEAVY_RPC_TIMEOUT
    from groket.ui.app import TraceEvalApp

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = tmp_path / "control.sock"
    app = TraceEvalApp(
        work_dir=work,
        traces_path=traces,
        control_socket=sock,
        control_attach_only=True,
    )
    client = app.control_client()
    assert client is not None
    assert client.timeout == HEAVY_RPC_TIMEOUT
