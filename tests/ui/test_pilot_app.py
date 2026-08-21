"""Headless Textual tests via App.run_test() + Pilot.

See https://textual.textualize.io/guide/testing/ and AGENTS.md §4.5c.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.diagnostics.self_test import CheckResult, SelfTestReport
from groket.ui.app import TraceEvalApp
from groket.ui.widgets.activity_bar import (
    ActivityBar,
    activity_counters_from_app,
    build_activity_line,
)
from groket.ui.widgets.self_test_modal import SelfTestModal

from .pilot_helpers import wait_until


def _minimal_traces(work: Path) -> Path:
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sd = traces / "pilot-sess-1"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-sess-1", "cwd": "/workspace"},
                "session_summary": "pilot",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "ts": "2026-06-25T00:01:00Z", "outcome": "success"})
        + "\n",
        encoding="utf-8",
    )
    return traces


@pytest.mark.asyncio
async def test_app_mounts_activity_bar(tmp_path: Path) -> None:
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: bool(list(app.query(ActivityBar))),
            description="ActivityBar mounted",
        )
        bar = list(app.query(ActivityBar))[0]
        bar.refresh_activity()
        await pilot.pause()
        counts = activity_counters_from_app(app)
        assert "analyze" not in counts
        line = build_activity_line(
            pending=counts["pending"],
            building=counts["building"],
            running=counts["running"],
            extracting=counts["extracting"],
            awaiting=counts["awaiting"],
            refresh_active=counts["refresh"],
            sessions_loaded=counts["sessions"],
        )
        plain = line.plain.lower()
        assert "sessions" in plain
        assert "analysis" not in plain
        assert "live" not in plain
        assert "lib" not in plain


@pytest.mark.asyncio
async def test_self_test_modal_applies_report(tmp_path: Path) -> None:
    work = tmp_path / "w"
    work.mkdir()
    (work / "runs" / "traces").mkdir(parents=True)
    app = TraceEvalApp(work_dir=work, traces_path=work / "runs" / "traces")
    report = SelfTestReport(
        checks=[
            CheckResult("docker", "Docker daemon", True, "reachable"),
            CheckResult("grok_auth", "Grok auth", True, "ok"),
        ]
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(SelfTestModal(work_dir=work))
        await wait_until(
            pilot,
            lambda: any(isinstance(s, SelfTestModal) for s in app.screen_stack),
            description="SelfTestModal on stack",
        )
        modal = next(s for s in app.screen_stack if isinstance(s, SelfTestModal))
        modal._apply_report(report)
        await wait_until(
            pilot,
            lambda: bool(getattr(app, "_self_test_summary", "")),
            description="self-test summary cached on app",
        )
        assert str(app._self_test_summary).startswith("self-test")
