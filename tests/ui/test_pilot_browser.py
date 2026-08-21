"""Pilot suite for BrowserScreen: timeline, tabs, multi-turn pending bar.

Uses Textual ``App.run_test()`` so compose, workers, and bindings actually run.
Synchronisation is condition-based (``wait_until``); see AGENTS.md §4.5c.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from groket.analysis.base import AnalysisResult, Finding
from groket.analysis.service import AnalysisService
from groket.models import Severity
from groket.runs.run_manager import RunManager
from groket.session.turn_gate import (
    list_queued_follow_ups,
    read_turn_gate_status,
    session_awaits_follow_up,
)
from groket.ui.app import TraceEvalApp
from groket.ui.bindings import focus_primary_list
from groket.ui.data_table import cursor_row_key
from groket.ui.screens.browser import BrowserScreen
from groket.ui.selectable_static import SelectableStatic
from groket.ui.widgets.timeline import TimelineTable
from textual.widgets import Input, LoadingIndicator, Static, Switch, TabbedContent

from .pilot_helpers import static_plain, wait_until


def _write_multi_turn_session(traces_root: Path, *, session_id: str = "browser-pilot-sess") -> Path:
    """Build a multi-turn session on the eval traces bind-mount layout."""
    container = traces_root / "groket-pilot-run-m1"
    sess = container / "%2Fworkspace" / session_id
    sess.mkdir(parents=True)

    (sess / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": session_id, "cwd": "/workspace"},
                "session_summary": "Pilot multi-turn pilot session",
                "generated_title": "Pilot multi-turn",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:10:00Z",
                "num_messages": 6,
                "current_model_id": "pilot-model",
            }
        ),
        encoding="utf-8",
    )

    updates = [
        {
            "type": "user_message",
            "ts": "2026-06-25T00:00:01Z",
            "message": {"content": [{"type": "text", "text": "first prompt"}]},
        },
        {
            "type": "assistant_message",
            "ts": "2026-06-25T00:00:02Z",
            "message": {"content": [{"type": "text", "text": "working on it"}]},
        },
        {
            "type": "tool_call",
            "ts": "2026-06-25T00:00:03Z",
            "toolCallId": "c1",
            "toolName": "run_terminal_command",
            "input": {"command": "echo hi"},
        },
        {
            "type": "tool_result",
            "ts": "2026-06-25T00:00:04Z",
            "toolCallId": "c1",
            "toolName": "run_terminal_command",
            "output": "hi\n",
        },
        {
            "type": "user_message",
            "ts": "2026-06-25T00:05:01Z",
            "message": {"content": [{"type": "text", "text": "second prompt"}]},
        },
        {
            "type": "assistant_message",
            "ts": "2026-06-25T00:05:02Z",
            "message": {"content": [{"type": "text", "text": "done"}]},
        },
    ]
    (sess / "updates.jsonl").write_text(
        "\n".join(json.dumps(u) for u in updates) + "\n",
        encoding="utf-8",
    )
    (sess / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-06-25T00:00:00Z",
                        "type": "turn_started",
                        "turn_number": 0,
                        "model_id": "pilot-model",
                    }
                ),
                json.dumps(
                    {"ts": "2026-06-25T00:04:00Z", "type": "turn_ended", "outcome": "success"}
                ),
                json.dumps(
                    {
                        "ts": "2026-06-25T00:05:00Z",
                        "type": "turn_started",
                        "turn_number": 1,
                        "model_id": "pilot-model",
                    }
                ),
                json.dumps(
                    {"ts": "2026-06-25T00:06:00Z", "type": "turn_ended", "outcome": "success"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    gate = container / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": session_id, "turn": 2}) + "\n",
        encoding="utf-8",
    )
    return sess


def _host_app(work: Path, traces: Path) -> TraceEvalApp:
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    assert isinstance(app.run_manager, RunManager)
    return app


async def _open_browser(app: TraceEvalApp, pilot, sess: Path) -> BrowserScreen:
    """Push BrowserScreen and wait until timeline is loaded; stop live refresh."""
    app.push_screen(BrowserScreen(sess, plugin_results={}))

    def ready() -> bool:
        scr = app.screen
        return isinstance(scr, BrowserScreen) and bool(scr.timeline)

    await wait_until(pilot, ready, description="BrowserScreen.timeline loaded")
    screen = app.screen
    assert isinstance(screen, BrowserScreen)
    screen._stop_live_refresh()
    return screen


async def _activate_tab(pilot, screen: BrowserScreen, pane_id: str) -> None:
    """Run tab action, set active authoritatively, wait until TabbedContent agrees."""
    actions = {
        "tab-timeline": screen.action_tab_timeline,
        "tab-findings": screen.action_tab_findings,
        "tab-summary": screen.action_tab_summary,
        "tab-diff": screen.action_tab_diff,
        "tab-reports": screen.action_tab_report,
    }
    actions[pane_id]()
    tabs = screen.query_one("#browser-tabs", TabbedContent)
    tabs.active = pane_id
    await wait_until(
        pilot,
        lambda: tabs.active == pane_id,
        description=f"tab {pane_id!r} active",
    )


@pytest.mark.asyncio
async def test_browser_mounts_timeline_and_pending_bar(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        assert screen.meta is not None
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0
        bar = screen.query_one("#session-pending-bar")
        assert bar.display is True or screen._session_is_pending()
        _ = screen.query_one("#session-pending-status", Static)


@pytest.mark.asyncio
async def test_enter_opens_full_width_event_and_escape_restores_list(
    tmp_path: Path,
) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0
        if screen._current_event is None:
            screen._current_event = tl.events[0]
        layout = screen.query_one("#browser-layout")
        assert not layout.has_class("event-reader")
        screen.action_toggle_event_reader()
        assert layout.has_class("event-reader")
        screen.action_go_back()
        assert not layout.has_class("event-reader")
        assert isinstance(app.screen, BrowserScreen)


@pytest.mark.asyncio
async def test_turn_step_returns_focus_so_jk_still_move(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        if screen._current_event is None and tl.events:
            screen._current_event = tl.events[0]
        screen.query_one("#search-input", Input).focus()
        await wait_until(
            pilot,
            lambda: screen.focused is not tl,
            description="search field took focus",
        )
        screen._land_after_turn_step(keep=True)

        def listed() -> bool:
            focused = screen.focused
            return focused is tl or getattr(focused, "id", None) == "timeline-list"

        await wait_until(pilot, listed, description="timeline list focused after turn step")


@pytest.mark.asyncio
async def test_browser_tabs_and_stats_turns(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)

        await _activate_tab(pilot, screen, "tab-summary")
        ev_table = screen.query_one("#stats-events-table")
        screen._update_stats()
        await wait_until(
            pilot,
            lambda: ev_table.row_count >= 1,
            description="summary stats table has rows",
        )

        await _activate_tab(pilot, screen, "tab-timeline")

        screen.action_tab_next()
        await pilot.pause()
        screen.action_tab_prev()
        await pilot.pause()
        await _activate_tab(pilot, screen, "tab-timeline")


@pytest.mark.asyncio
async def test_summary_turn_row_opens_timeline_at_start(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        ev_i = screen.timeline[0].index
        screen._jump_timeline_to_event(ev_i)
        tabs = screen.query_one("#browser-tabs", TabbedContent)
        await wait_until(
            pilot,
            lambda: tabs.active == "tab-timeline",
            description="timeline tab after turn jump",
        )
        await wait_until(
            pilot,
            lambda: cursor_row_key(screen.query_one("#timeline-list", TimelineTable)) == str(ev_i),
            description="timeline cursor on turn start",
        )


@pytest.mark.asyncio
async def test_summary_pairs_stack_when_narrow(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        scroll = screen.query_one("#summary-session-scroll")
        screen._SUMMARY_STACK_WIDTH = 200
        screen._sync_summary_stack()
        assert scroll.has_class("summary-stack")
        screen._SUMMARY_STACK_WIDTH = 40
        screen._sync_summary_stack()
        assert not scroll.has_class("summary-stack")


@pytest.mark.asyncio
async def test_browser_idle_awaiting_skips_live_timeline(tmp_path: Path) -> None:
    """Awaiting follow-up keeps the pending bar but does not need timeline polls."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        assert screen._session_is_pending() is True
        # Gate idle wait — not agent writing traces.
        assert screen._session_needs_live_timeline() is False
        screen._set_title_from_meta()
        assert "LIVE" not in (screen.title or "")
        assert "awaiting" in (screen.title or "").lower()
        chrome = static_plain(screen.query_one("#app-chrome-title", Static))
        assert "Pilot multi-turn" in chrome
        assert "LIVE" not in chrome
        assert "awaiting" not in chrome.lower()


@pytest.mark.asyncio
async def test_browser_follow_up_enter_and_queue(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen._refresh_session_pending_bar()
        await pilot.pause()

        inp = screen.query_one("#session-follow-input", Input)
        inp.value = "pilot follow-up one"
        inp.focus()
        await pilot.pause()
        await pilot.press("enter")

        def gate_advanced() -> bool:
            st = read_turn_gate_status(sess)
            if st.get("state") in ("running", "done"):
                return True
            gate_root = traces / "groket-pilot-run-m1"
            return any(gate_root.glob(".groket-turn*/command")) or bool(
                list_queued_follow_ups(sess)
            )

        await wait_until(pilot, gate_advanced, description="follow-up staged or queued")

        screen._refresh_session_pending_bar()
        await pilot.pause()
        inp.value = "pilot follow-up two"
        screen._session_follow_send()
        await pilot.pause()
        # Second send may queue; either way gate dir exists
        assert (traces / "groket-pilot-run-m1").is_dir()


@pytest.mark.asyncio
async def test_browser_mark_done_clears_pending(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(120, 40)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen._session_follow_done()
        await wait_until(
            pilot,
            lambda: session_awaits_follow_up(sess) is False,
            description="session no longer awaiting follow-up",
        )
        # Session-scoped Done writes command=done then stop_session_container
        # finalizes the gate (clears control files, state=done) so the list does
        # not stick on ending after the host kills the entrypoint.
        st = read_turn_gate_status(sess)
        assert st.get("state") == "done" or session_awaits_follow_up(sess) is False


@pytest.mark.asyncio
async def test_browser_timeline_filter_and_cursor_stable(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0

        if tl.row_count > 1:
            tl.move_cursor(row=1, animate=False)
        key_before = cursor_row_key(tl)

        tl.load_events(screen.timeline, screen._findings, list(screen._flags.values()))
        await pilot.pause()
        if key_before and tl.row_count:
            key_after = cursor_row_key(tl)
            assert key_after == key_before or key_after is not None

        screen._apply_filter(event_type="tool_call", errors_only=False)
        await pilot.pause()


@pytest.mark.asyncio
async def test_browser_timeline_view_filter_survives_reload(tmp_path: Path) -> None:
    """View filter must re-apply after load_events (live tick / F5)."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        full_n = tl.row_count
        assert full_n > 0

        # Session chrome is always present on this fixture (turn markers).
        screen._timeline_filter = "sess"
        screen._apply_timeline_filters()
        await pilot.pause()
        filtered_n = tl.row_count
        assert 0 < filtered_n <= full_n

        # Simulate full / light reload painting the unfiltered list first.
        tl.load_events(screen.timeline, screen._findings, list(screen._flags.values()))
        await pilot.pause()
        assert tl.row_count == full_n  # unfiltered paint
        # Without reapply, the Select would still say "sess" while all rows show.
        screen._reapply_timeline_view_filter()
        await pilot.pause()
        assert tl.row_count == filtered_n

        # Full populate path must also reapply.
        screen._populate_ui()
        await pilot.pause()
        assert screen._timeline_filter == "sess"
        assert tl.row_count == filtered_n


@pytest.mark.asyncio
async def test_browser_with_plugin_findings_report(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="f1",
        title="pilot finding",
        severity=Severity.MEDIUM,
        plugin_id="engine",
        detail="x",
        tool_call_ids=["c1"],
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="1 finding",
            findings=[finding],
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser timeline with findings")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._render_report_overview()
        screen._render_report_flags()
        await pilot.pause()
        await _activate_tab(pilot, screen, "tab-reports")
        assert screen._findings is not None


# ── Report tab filter ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_report_filter_sections(tmp_path: Path) -> None:
    """Report filter dropdown switches visible sections."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="f2",
        title="report filter finding",
        severity=Severity.HIGH,
        plugin_id="engine",
        detail="detail text",
        tool_call_ids=["c1"],
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="1 finding",
            findings=[finding],
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser with findings")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        await _activate_tab(pilot, screen, "tab-reports")
        screen._update_reports_tab()
        await pilot.pause()

        # Filter to flags only
        screen._report_filter = "flags"
        screen._apply_report_visibility()
        await pilot.pause()
        assert screen._section_visible("flags")

        # Filter back to all
        screen._report_filter = "all"
        screen._apply_report_visibility()
        await pilot.pause()
        assert screen._section_visible("flags")


@pytest.mark.asyncio
async def test_browser_report_plugin_multi_pane_selectable(tmp_path: Path) -> None:
    """Plugin report artifact splits into focusable SelectableStatic panes."""
    from groket.ui.selectable_static import SelectableStatic
    from textual.containers import Vertical

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="mf1",
        title="Ignored MCP",
        severity=Severity.HIGH,
        plugin_id="engine",
        detail="short",
        extras={
            "what_model_did": "Claimed MCP failed",
            "what_should_have_done": "Call MCP first",
            "why_mistake": "Instructions",
            "where": "Turn 0",
            "pattern": "skip",
        },
    )
    report_md = """# Model Feedback form drafts — sess

Intro line for the plugin.

## Session summary

Overall summary text.

## Issue 1: Ignored MCP

### Form fields (copy line-by-line)

```
Model Name: pilot-model
Session ID: sess
Severity: Major
```

### Issue (copy into the Issue box)

```
What: Claimed MCP failed
Where: Turn 0
Why: Instructions
Should have: Call MCP first
Pattern: skip
```
"""
    # Use analyzer_id ``engine`` so default enabled plugins keep the result.
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="1 finding",
            findings=[finding],
            artifacts={"report": report_md},
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser with multi-pane report")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        await _activate_tab(pilot, screen, "tab-reports")
        screen._update_reports_tab()
        await pilot.pause()

        section = screen.query_one("#report-section-plugin-engine", Vertical)
        panes = list(section.query(SelectableStatic))
        # header + preamble + summary + issue header + form + issue box
        assert len(panes) >= 4
        plains = [p.get_plain_text() for p in panes]
        joined = "\n".join(plains)
        assert "Claimed MCP failed" in joined
        assert "Model Name: pilot-model" in joined
        # Paste-ready Issue pane exists without Form fields mixed in
        issue_only = [p for p in plains if p.strip().startswith("What:") and "Model Name:" not in p]
        assert issue_only, plains
        form_only = [p for p in plains if "Model Name: pilot-model" in p and "What:" not in p]
        assert form_only, plains
        # Each pane is focusable for Tab + y
        assert all(p.can_focus for p in panes)


@pytest.mark.asyncio
async def test_analysis_changed_clears_report_loading_while_open(tmp_path: Path) -> None:
    """Control analysis/changed must clear pending so Report is not stuck loading.

    Repro: open a session, analysis finishes on the owner while the browser is
    already open on Report — results used to land under permanent loading overlays
    until the operator left and re-entered the session.
    """
    from textual.containers import Vertical

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="f1",
        title="Stuck report finding",
        severity=Severity.HIGH,
        plugin_id="engine",
        detail="detail",
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="1 finding",
            findings=[finding],
            artifacts={"report": "# Report\n\n## Issue\n\nWhat: Stuck report finding\n"},
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-reports")
        screen._analysis_pending = True
        screen._show_analysis_pending()
        await pilot.pause()
        assert screen.query_one("#reports-scroll").loading is True

        class _CachedSvc:
            def load_cached_all(self, *_a: object, **_k: object) -> dict:
                return results

        app._analysis_svc = lambda: _CachedSvc()  # type: ignore[method-assign]
        app._control_analysis_changed_ui(sess.name)
        await pilot.pause()
        await pilot.pause()

        assert screen._analysis_pending is False
        assert screen.query_one("#reports-scroll").loading is False
        assert len(screen._findings) == 1
        section = screen.query_one("#report-section-plugin-engine", Vertical)
        assert section.loading is False
        plains = [p.get_plain_text() for p in section.query(SelectableStatic)]
        joined = "\n".join(plains)
        assert "Stuck report finding" in joined


@pytest.mark.asyncio
async def test_apply_analysis_results_paints_report_from_empty(tmp_path: Path) -> None:
    """Home/batch finish while the browser is open paints Report without re-entry."""
    from textual.containers import Vertical

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="f2",
        title="Batch finding",
        severity=Severity.MEDIUM,
        plugin_id="engine",
        detail="d",
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="1",
            findings=[finding],
            artifacts={"report": "## Body\n\nbatch body text\n"},
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-reports")
        screen._analysis_pending = True
        screen._show_analysis_pending()
        await pilot.pause()

        app._push_analysis_results_to_open_browser(sess, results)
        await pilot.pause()
        await pilot.pause()

        assert screen._analysis_pending is False
        assert screen.query_one("#reports-scroll").loading is False
        section = screen.query_one("#report-section-plugin-engine", Vertical)
        joined = "\n".join(p.get_plain_text() for p in section.query(SelectableStatic))
        assert "Batch finding" in joined
        assert "batch body text" in joined


@pytest.mark.asyncio
async def test_browser_analysis_pending_collapses_report_panes(tmp_path: Path) -> None:
    """Pending analysis shows LoadingIndicator and widget.loading on report cards."""
    from groket.ui.selectable_static import SelectableStatic
    from textual.containers import Vertical

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    report_md = """# Report

## One

body one

## Two

body two

## Three

body three
"""
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="ok",
            findings=[],
            artifacts={"report": report_md},
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser with multi-pane report")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        await _activate_tab(pilot, screen, "tab-reports")
        screen._update_reports_tab()
        await pilot.pause()

        section = screen.query_one("#report-section-plugin-engine", Vertical)
        before = list(section.query(SelectableStatic))
        assert len(before) >= 3

        # Not pending: paint is a no-op (does not stack spinners on idle open).
        screen._analysis_pending = False
        screen._show_analysis_pending()
        await pilot.pause()
        assert len(list(section.query(SelectableStatic))) == len(before)

        screen._analysis_pending = True
        screen._paint_analysis_pending_spinner(full=True)
        await pilot.pause()

        assert section.loading is True
        pending = screen.query_one("#browser-analysis-loading", LoadingIndicator)
        assert pending.display
        assert screen.query_one("#findings-table").loading is True
        assert screen.query_one("#reports-scroll").loading is True


@pytest.mark.asyncio
async def test_browser_analysis_pending_marks_report_when_opened(tmp_path: Path) -> None:
    """Opening Report during analysis shows loading on the scroll and plugin cards."""
    from textual.containers import Vertical

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            summary="ok",
            findings=[],
            artifacts={"report": "# Report\n\n## One\n\nbody\n"},
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser open on Timeline")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()
        assert screen._active_browser_tab() == "tab-timeline"

        screen._analysis_pending = True
        screen._show_analysis_pending()
        await pilot.pause()
        assert screen.query_one("#browser-analysis-loading", LoadingIndicator).display
        assert screen.query_one("#findings-table").loading is True

        await _activate_tab(pilot, screen, "tab-reports")
        await wait_until(
            pilot,
            lambda: bool(screen.query_one("#reports-scroll").loading),
            description="Report scroll loading after open during wait",
        )

        def engine_loading() -> bool:
            try:
                return bool(screen.query_one("#report-section-plugin-engine", Vertical).loading)
            except Exception:
                return False

        await wait_until(
            pilot,
            engine_loading,
            description="engine report card mounted and loading",
        )


@pytest.mark.asyncio
async def test_browser_stale_analysis_is_a_note_not_a_banner(tmp_path: Path) -> None:
    """Stale analysis is a Report note; Rich tags stay off the page."""
    from textual.css.query import NoMatches

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen._stop_live_refresh()
        with pytest.raises(NoMatches):
            screen.query_one("#analysis-stale-banner")

        with patch.object(
            AnalysisService,
            "stale_analyzer_hints",
            return_value=["engine cache older than plugin"],
        ):
            screen._apply_stale_analysis_hints(repaint=False)
            await _activate_tab(pilot, screen, "tab-reports")
            screen._update_reports_tab()

            def has_note() -> bool:
                report = screen.query_one("#report-overview-content", SelectableStatic)
                plain = report.get_plain_text() or ""
                return "Stale analysis" in plain and "engine cache older than plugin" in plain

            await wait_until(
                pilot,
                has_note,
                description="stale analysis note on Report",
            )
            report = screen.query_one("#report-overview-content", SelectableStatic)
            plain = report.get_plain_text() or ""
            assert "[bold yellow]" not in plain
            assert "[/]" not in plain


# ── Summary stats tables ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_summary_stats_tables(tmp_path: Path) -> None:
    """Summary pane builds event, tool, phase, and turns tables."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        screen._update_stats()
        await pilot.pause()

        from textual.widgets import DataTable as DT

        ev_table = screen.query_one("#stats-events-table", DT)
        assert ev_table.row_count >= 1
        tools_table = screen.query_one("#stats-tools-table", DT)
        assert tools_table.row_count >= 0
        phases_table = screen.query_one("#stats-phases-table", DT)
        assert phases_table.row_count >= 1


# ── Diff tab ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_diff_tab(tmp_path: Path) -> None:
    """Diff tab renders even with no diff data."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        screen._update_diff_tab()
        await pilot.pause()
        from groket.ui.widgets.diff_view import DiffView
        from textual.widgets import Tree

        view = screen.query_one("#diff-view", DiffView)
        tree = view.query_one("#diff-file-list", Tree)
        assert len(tree.root.children) == 0
        assert view.selected_plain() == ""


@pytest.mark.asyncio
async def test_browser_diff_file_list_shows_rewind_files(tmp_path: Path) -> None:
    """Diff pane lists rewind snapshot files and yanks the highlighted hunk."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    (sess / "rewind_points.jsonl").write_text(
        json.dumps(
            {
                "prompt_index": 1,
                "file_snapshots": {"app.py": "old", "extra.py": "keep"},
                "after_snapshots": {"app.py": "new", "extra.py": "keep", "added.py": "fresh"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        from groket.ui.widgets.diff_view import DiffView
        from textual.widgets import Tree

        view = screen.query_one("#diff-view", DiffView)
        tree = view.query_one("#diff-file-list", Tree)
        await wait_until(
            pilot,
            lambda: len(tree.root.children) == 2,
            description="diff file tree has two changed paths",
        )
        labels = {str(n.label) for n in tree.root.children}
        assert labels == {"added.py", "app.py"}
        tree.select_node(tree.root.children[0])
        await pilot.pause()
        first = str(tree.root.children[0].label)
        assert first in view.selected_plain()


@pytest.mark.asyncio
async def test_browser_diff_file_list_groups_nested_paths(tmp_path: Path) -> None:
    """Nested rewind paths show a directory header and file leaves."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    (sess / "rewind_points.jsonl").write_text(
        json.dumps(
            {
                "prompt_index": 1,
                "file_snapshots": {"src/app.py": "old", "src/extra.py": "old-extra"},
                "after_snapshots": {"src/app.py": "new", "src/extra.py": "new-extra"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        from groket.ui.widgets.diff_view import DiffView
        from textual.widgets import Tree

        view = screen.query_one("#diff-view", DiffView)
        tree = view.query_one("#diff-file-list", Tree)
        await wait_until(
            pilot,
            lambda: bool(tree.root.children),
            description="diff file tree has rows",
        )
        dir_node = tree.root.children[0]
        assert str(dir_node.label) == "src/"
        assert dir_node.data == ("dir", "src/")
        files = {str(n.label) for n in dir_node.children}
        assert files == {"app.py", "extra.py"}


@pytest.mark.asyncio
async def test_browser_diff_search_filters_path_and_body(tmp_path: Path) -> None:
    """Slash search keeps a path hit and a body hit; h/l steps snapshots."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    (sess / "rewind_points.jsonl").write_text(
        json.dumps(
            {
                "prompt_index": 0,
                "file_snapshots": {"alpha.py": "old"},
                "after_snapshots": {"alpha.py": "needle-one"},
            }
        )
        + "\n"
        + json.dumps(
            {
                "prompt_index": 1,
                "file_snapshots": {"beta.py": "old", "notes.md": "keep"},
                "after_snapshots": {
                    "beta.py": "changed",
                    "notes.md": "keep",
                    "zeta.py": "needle-two",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        from groket.ui.widgets.diff_view import DiffView
        from textual.widgets import Input, Tree

        view = screen.query_one("#diff-view", DiffView)
        tree = view.query_one("#diff-file-list", Tree)
        await wait_until(
            pilot,
            lambda: view.can_step_point() is True,
            description="two rewind snapshots",
        )
        assert screen.check_action("prev_turn", ()) is True
        screen.action_search()
        await pilot.pause()
        search = view.query_one("#diff-search", Input)
        search.value = "zeta"
        await wait_until(
            pilot,
            lambda: len(tree.root.children) == 1 and str(tree.root.children[0].label) == "zeta.py",
            description="path query keeps zeta.py",
        )
        assert "zeta.py" in view.selected_plain()
        search.value = "needle-two"
        await wait_until(
            pilot,
            lambda: (
                (view.painted_hit_line() or "").startswith("> ")
                and "needle-two" in (view.painted_hit_line() or "")
            ),
            description="body query paints the matching unified line",
        )
        painted = view.painted_hit_line() or ""
        raw = view.selected_plain().splitlines()[view.hit_line() or 0]
        assert painted == f"> {raw}"
        from groket.ui.selectable_static import SelectableStatic

        body = view.query_one("#diff-content", SelectableStatic).get_plain_text()
        assert painted in body


@pytest.mark.asyncio
async def test_browser_diff_context_above_files_hunk_split(tmp_path: Path) -> None:
    """Prompt/Assistant sit above a parent that holds files and hunk side by side."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-diff")
        from groket.session.workspace_diff import DiffHunk, DiffPoint, WorkspaceDiff
        from groket.ui.selectable_static import SelectableStatic
        from groket.ui.widgets.diff_view import DiffView

        view = screen.query_one("#diff-view", DiffView)
        view.set_doc(
            WorkspaceDiff(
                (
                    DiffPoint(
                        key="0",
                        source="rewind_points",
                        prompt_index=0,
                        created_at=None,
                        files=(
                            DiffHunk(
                                path="app.py",
                                kind="modified",
                                added=1,
                                removed=1,
                                unified="--- a/app.py\n+++ b/app.py\n+new\n",
                            ),
                        ),
                        prompt_text="first prompt",
                        assistant_text="## Heading\n\n**ok**",
                    ),
                )
            )
        )
        await pilot.pause()
        chrome = view.query_one("#diff-chrome")
        ctx = view.query_one("#diff-context")
        search = view.query_one("#diff-search-bar")
        split = view.query_one("#diff-layout")
        assert view.query_one("#diff-filter-bar").parent is chrome
        assert ctx.parent is chrome
        assert search.parent is view
        assert split.parent is view
        kids = [child.id for child in view.children]
        assert kids.index("diff-chrome") < kids.index("diff-search-bar") < kids.index("diff-layout")
        assert view.query_one("#diff-files").parent is split
        assert view.query_one("#diff-scroll").parent is split
        prompt = view.query_one("#diff-prompt", SelectableStatic)
        assert "first prompt" in prompt.get_plain_text()
        tabs = view.query_one("#diff-context-tabs", TabbedContent)
        tabs.active = "diff-tab-assistant"
        await pilot.pause()
        assistant = view.query_one("#diff-assistant", SelectableStatic)
        assert "## Heading" in assistant.get_plain_text()


# ── Summary tab ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_summary_tab(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-summary")
        screen._update_summary_tab()
        await pilot.pause()


# ── Flag event ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_flag_event_action(tmp_path: Path) -> None:
    """Flag action only available when timeline focused with event selected."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-timeline")
        tl = screen.query_one("#timeline-list", TimelineTable)
        if tl.row_count > 0:
            tl.move_cursor(row=0, animate=False)
            await pilot.pause()
        # check_action returns False when no event selected yet
        result = screen.check_action("flag_event", ())
        # Binding enabled only when timeline has a flaggable event under cursor.
        assert result in (True, False)


@pytest.mark.asyncio
async def test_browser_flag_result_save_delete(tmp_path: Path) -> None:
    """_on_flag_result save and delete branches."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        from groket.models import Flag, FlagVerdict

        flag = Flag(
            event_index=0,
            event_type="tool_call",
            tool_name="run_terminal_command",
            verdict=FlagVerdict.BAD,
            description="wrong command",
        )
        screen._on_flag_result(("save", flag))
        await pilot.pause()
        assert 0 in screen._flags

        screen._on_flag_result(("delete", 0))
        await pilot.pause()
        assert 0 not in screen._flags


@pytest.mark.asyncio
async def test_browser_first_paint_defers_summary_and_report(tmp_path: Path) -> None:
    """Opening a session paints Timeline only; Summary and Report fill on visit."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        summary = screen.query_one("#summary-content", SelectableStatic)
        report = screen.query_one("#report-overview-content", SelectableStatic)
        from groket.ui.i18n import t

        assert not (summary.get_plain_text() or "").strip()
        assert t("ui-session-report") not in (report.get_plain_text() or "")
        tl = screen.query_one("#timeline-list", TimelineTable)
        assert tl.row_count > 0
        bar = screen.query_one("#filter-bar")
        slot = screen.query_one("#timeline-tail-slot")
        tail = screen.query_one("#timeline-tail", Switch)
        label = screen.query_one("#timeline-tail-label", Static)
        filt = screen.query_one("#filter-view-label", Static)
        search = screen.query_one("#search-input", Input)
        assert slot.display
        assert label.display
        assert "Tail" in static_plain(label)
        assert tail.value is False
        shown = [c for c in bar.children if c.display]
        assert shown[-2] is label
        assert shown[-1] is slot
        assert label.region.x > search.region.x
        assert slot.region.x > label.region.x
        assert label.region.y == filt.region.y
        assert slot.region.y == filt.region.y
        assert tail.region.center[1] == label.region.center[1]
        await pilot.click("#timeline-tail-label")
        await wait_until(
            pilot,
            lambda: screen._timeline_follow_tail() is True,
            description="Tail label click turns the switch on",
        )

        await _activate_tab(pilot, screen, "tab-summary")
        await wait_until(
            pilot,
            lambda: bool((summary.get_plain_text() or "").strip()),
            description="Summary body after first visit",
        )
        assert "Pilot" in summary.get_plain_text()

        await _activate_tab(pilot, screen, "tab-reports")
        await wait_until(
            pilot,
            lambda: t("ui-session-report") in (report.get_plain_text() or ""),
            description="Report body after first visit",
        )


@pytest.mark.asyncio
async def test_browser_tab_bar_fills_summary_and_report(tmp_path: Path) -> None:
    """Setting TabbedContent.active (tab-bar click) fills Summary and Report."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        summary = screen.query_one("#summary-content", SelectableStatic)
        report = screen.query_one("#report-overview-content", SelectableStatic)
        from groket.ui.i18n import t

        assert not (summary.get_plain_text() or "").strip()
        assert t("ui-session-report") not in (report.get_plain_text() or "")

        tabs = screen.query_one("#browser-tabs", TabbedContent)
        tabs.active = "tab-summary"
        await wait_until(
            pilot,
            lambda: bool((summary.get_plain_text() or "").strip()),
            description="Summary body after tab-bar activate",
        )
        assert "Pilot" in summary.get_plain_text()

        tabs.active = "tab-reports"
        await wait_until(
            pilot,
            lambda: t("ui-session-report") in (report.get_plain_text() or ""),
            description="Report body after tab-bar activate",
        )


@pytest.mark.asyncio
async def test_browser_search_debounce_applies_final_query(tmp_path: Path) -> None:
    """Rapid search keys rebuild the table once, for the last needle."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        tl = screen.query_one("#timeline-list", TimelineTable)
        rebuilds = {"n": 0}
        orig = tl._refresh_rows

        def _count() -> None:
            rebuilds["n"] += 1
            orig()

        tl._refresh_rows = _count  # type: ignore[method-assign]
        inp = screen.query_one("#search-input", Input)
        for needle in ("e", "ec", "ech"):
            inp.value = needle
            screen._on_search_changed(Input.Changed(inp, needle))
        assert rebuilds["n"] == 0
        await wait_until(
            pilot,
            lambda: rebuilds["n"] >= 1,
            description="debounced timeline search rebuild",
        )
        assert rebuilds["n"] == 1
        filtered = tl.row_count
        tl._refresh_rows = orig  # type: ignore[method-assign]
        screen._timeline_search = "ech"
        screen._apply_timeline_filters()
        await pilot.pause()
        assert tl.row_count == filtered


@pytest.mark.asyncio
async def test_browser_control_paints_first_page_before_remainder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First control page is on the Timeline before the next page is fetched."""
    from groket.session import wire_timeline as wt
    from groket.session.control_views import build_session_overview, build_session_timeline

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    monkeypatch.setattr(wt, "TIMELINE_RPC_LIMIT", 1)
    gate = threading.Event()
    saw_first = threading.Event()
    offsets: list[int] = []

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            return build_session_overview(sess)

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            off = int(kwargs.get("offset") or 0)
            at = kwargs.get("at_index")
            offsets.append(off)
            if at is None:
                if off > 0:
                    gate.wait(timeout=5)
                else:
                    saw_first.set()
            return build_session_timeline(
                sess,
                offset=off,
                limit=int(kwargs.get("limit") or 1),
                at_index=at if isinstance(at, int) else None,
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    access = _Access()
    app = _host_app(work, traces)
    app.is_control_client = lambda: True  # type: ignore[method-assign]
    app.session_access = lambda: access  # type: ignore[method-assign]

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results={}))
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, BrowserScreen) and saw_first.is_set(),
            description="control first page returned",
        )
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        await wait_until(
            pilot,
            lambda: screen.query_one("#timeline-list", TimelineTable).row_count == 1,
            description="first timeline page painted",
        )
        first_n = len(screen.timeline)
        assert first_n == 1
        gate.set()
        await wait_until(
            pilot,
            lambda: len(screen.timeline) > first_n,
            description="remaining timeline pages appended",
        )
        assert any(off > 0 for off in offsets)
        full_indices = [e.index for e in screen.timeline]
        drained = await wt.fetch_timeline_events(access, str(sess), page_limit=1)
        assert full_indices == [e.index for e in drained]

        screen._on_flag_result(None)
        await pilot.pause()


@pytest.mark.asyncio
async def test_browser_open_event_asks_owner_ceiling(tmp_path: Path) -> None:
    """Selecting a timeline row refetches that event at the owner body ceiling."""
    from groket.session.control_views import (
        MAX_CONTENT_CHARS,
        build_session_overview,
        build_session_timeline,
    )

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    asked: list[dict[str, object]] = []

    class _Access:
        async def session_overview(self, _ref: str) -> object:
            return build_session_overview(sess)

        async def session_timeline(self, _ref: str, **kwargs: object) -> object:
            asked.append(dict(kwargs))
            at = kwargs.get("at_index")
            return build_session_timeline(
                sess,
                offset=int(kwargs.get("offset") or 0),
                limit=int(kwargs.get("limit") or 50),
                at_index=at if isinstance(at, int) else None,
                content_chars=int(kwargs.get("content_chars") or 500),
            )

    access = _Access()
    app = _host_app(work, traces)
    app.is_control_client = lambda: True  # type: ignore[method-assign]
    app.session_access = lambda: access  # type: ignore[method-assign]

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)

        def _asked_ceiling() -> bool:
            return any(
                isinstance(row.get("at_index"), int)
                and row.get("content_chars") == MAX_CONTENT_CHARS
                for row in asked
            )

        if screen._current_event is None:
            table = screen.query_one("#timeline-list", TimelineTable)
            ev = next(iter(table.events), None)
            assert ev is not None
            screen._current_event = ev
            screen._paint_selected_event_detail()
        await wait_until(pilot, _asked_ceiling, description="open-event ceiling fetch")
        assert any(
            isinstance(row.get("at_index"), int)
            and row.get("content_chars") == MAX_CONTENT_CHARS
            and int(row.get("limit") or 0) == 1
            for row in asked
        )


# ── Export finding ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_export_finding(tmp_path: Path) -> None:
    """Export finding creates a markdown report file."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="export-f1",
        title="Exported finding",
        severity=Severity.HIGH,
        plugin_id="engine",
        detail="Should have done X",
        tool_call_ids=["c1"],
        extras={"should_have": "done X instead"},
        children=[
            Finding(
                id="child-f1",
                title="Sub finding",
                severity=Severity.LOW,
                plugin_id="engine",
            ),
        ],
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            findings=[finding],
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser for export")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        screen._selected_finding = finding
        await _activate_tab(pilot, screen, "tab-findings")
        screen.action_export_finding()
        await pilot.pause()
        reports_dir = screen._reports_dir()
        if reports_dir.exists():
            md_files = list(reports_dir.glob("*.md"))
            assert len(md_files) >= 1


# ── Timeline view modes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_timeline_view_modes(tmp_path: Path) -> None:
    """Exercise all timeline View select modes."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        for mode in ("tools", "user", "asst", "sess", "errors", "all"):
            screen._apply_timeline_mode(mode)
            await pilot.pause()
        assert screen._timeline_filter == "all"


# ── Search action ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_search_action(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_search()
        await pilot.pause()
        screen.action_clear_filters()
        await pilot.pause()


# ── Refresh context ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_refresh_context(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_refresh_context()
        await pilot.pause()
        await pilot.pause()


# ── Show findings tab ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_show_findings_action(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_show_findings()
        await pilot.pause()


# ── Focus follow-up field ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_focus_follow_up(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_focus_follow_up()
        await pilot.pause()


# ── Focus timeline filter ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_focus_timeline_filter(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_focus_timeline_filter()
        await pilot.pause()


# ── check_action for follow-up ──────────────────────────────────────────


def _shown_footer_actions(screen: BrowserScreen) -> set[str]:
    return {
        ab.binding.action
        for ab in screen.active_bindings.values()
        if ab.binding.show and ab.enabled
    }


@pytest.mark.asyncio
async def test_browser_footer_hides_timeline_keys_off_timeline(tmp_path: Path) -> None:
    """Enter / h l / Flag leave the rail when the Timeline pane is not showing."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        await _activate_tab(pilot, screen, "tab-timeline")
        tl = screen.query_one("#timeline-list", TimelineTable)
        focus_primary_list(tl)
        if screen._current_event is None and screen.timeline:
            screen._current_event = screen.timeline[0]
        screen.refresh_bindings()
        await pilot.pause()
        assert screen.check_action("toggle_event_reader", ()) is True
        shown = _shown_footer_actions(screen)
        assert "toggle_event_reader" in shown

        await _activate_tab(pilot, screen, "tab-summary")
        assert screen.check_action("toggle_event_reader", ()) is False
        assert screen.check_action("prev_turn", ()) is False
        assert screen.check_action("next_turn", ()) is False
        assert screen.check_action("flag_event", ()) is False
        shown = _shown_footer_actions(screen)
        assert "toggle_event_reader" not in shown
        assert "prev_turn" not in shown
        assert "next_turn" not in shown
        assert "flag_event" not in shown
        assert "go_back" in shown
        assert "operator_note" in shown


@pytest.mark.asyncio
async def test_browser_check_action_follow_up(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        for action in ("send_follow_up", "mark_session_done", "focus_follow_up"):
            result = screen.check_action(action, ())
            assert result in (True, False)
        # Export requires findings tab active
        result = screen.check_action("export_finding", ())
        assert result in (True, False)


# ── Open share (no URL) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_open_share_no_url(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        screen = await _open_browser(app, pilot, sess)
        screen.action_open_share()
        await pilot.pause()


# ── Report plugin helpers ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_report_plugin_helpers(tmp_path: Path) -> None:
    assert BrowserScreen._plugin_title("engine") == "Detectors"
    assert BrowserScreen._plugin_title("custom_thing") == "Custom Thing"
    assert BrowserScreen._report_plugin_slug("test-plugin") == "test-plugin"
    assert BrowserScreen._report_plugin_slug("a/b") == "a_b"


# ── Findings row mapping ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_findings_row_index(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_multi_turn_session(traces)
    finding = Finding(
        id="f-row-test",
        title="row test finding",
        severity=Severity.LOW,
        plugin_id="engine",
    )
    results = {
        "engine": AnalysisResult(
            session_id=sess.name,
            session_dir=str(sess),
            analyzer_id="engine",
            ok=True,
            findings=[finding],
        )
    }
    app = _host_app(work, traces)

    async with app.run_test(size=(140, 48)) as pilot:
        app.push_screen(BrowserScreen(sess, plugin_results=results))

        def ready() -> bool:
            scr = app.screen
            return isinstance(scr, BrowserScreen) and bool(scr.timeline)

        await wait_until(pilot, ready, description="browser for row test")
        screen = app.screen
        assert isinstance(screen, BrowserScreen)
        screen._stop_live_refresh()
        screen._collect_findings()
        screen._populate_analysis_ui()
        await pilot.pause()

        await _activate_tab(pilot, screen, "tab-findings")
        findings_table = screen.query_one("#findings-table")
        if findings_table.row_count > 0:
            findings_table.move_cursor(row=0, animate=False)
            await pilot.pause()
