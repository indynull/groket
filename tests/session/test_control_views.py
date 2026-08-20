"""Domain control views: session/get, timeline, turns, usage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.session.control_views import (
    build_session_diff,
    build_session_findings,
    build_session_get,
    build_session_overview,
    build_session_timeline,
    build_session_turns,
    build_session_usage,
)


def _write_session(root: Path, name: str) -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": "View session",
                "model": "grok-test",
            }
        ),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1000,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "hello user"},
                    "_meta": {"promptIndex": 2},
                }
            },
        },
        {
            "timestamp": 1001,
            "params": {
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "hello agent"},
                }
            },
        },
        {
            "timestamp": 1002,
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "c1",
                    "title": "read_file",
                    "kind": "read",
                    "status": "completed",
                    "rawInput": {"target_file": "/tmp/x"},
                }
            },
        },
    ]
    (session_dir / "updates.jsonl").write_text(
        "".join(json.dumps(u) + "\n" for u in updates),
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return session_dir


def test_build_session_get_meta(tmp_path: Path) -> None:
    sd = _write_session(tmp_path / "runs" / "traces", "sess-get")
    got = build_session_get(sd)
    assert got["sessionId"] == "sess-get"
    assert got["title"] == "View session"
    assert "status" in got
    assert got["path"]
    assert "notesRevision" in got
    assert "numEvents" in got


def test_build_session_timeline_pages(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "sess-tl")
    full = build_session_timeline(sd, offset=0, limit=10)
    assert full["total"] >= 1
    assert full["events"]
    assert "content" in full["events"][0]
    assert "type" in full["events"][0]
    page = build_session_timeline(sd, offset=0, limit=1)
    assert len(page["events"]) == 1
    assert page["limit"] == 1
    # Offset advances through the same ordered list (HUD scroll/fill).
    if full["total"] >= 2:
        second = build_session_timeline(sd, offset=1, limit=1)
        assert len(second["events"]) == 1
        assert second["offset"] == 1
        assert second["events"][0]["index"] != page["events"][0]["index"]
    tools = build_session_timeline(sd, offset=0, limit=50, kind="tools")
    assert tools["total"] >= 1
    assert all(ev.get("kind") in {"tool", "tool_result"} for ev in tools["events"])
    subs = build_session_timeline(sd, offset=0, limit=50, kind="subagents")
    assert all(ev.get("kind") == "subagent" for ev in subs["events"])
    users = build_session_timeline(sd, offset=0, limit=50, kind="user")
    assert users["total"] >= 1
    assert all(ev.get("kind") == "user" for ev in users["events"])
    hit = build_session_timeline(sd, offset=0, limit=50, query="hello user")
    assert hit["total"] >= 1
    assert any("hello" in str(ev.get("content") or "").lower() for ev in hit["events"])
    for ev in hit["events"]:
        assert ev.get("matchField")
        assert "hello" in str(ev.get("matchSnippet") or "").casefold()
    around = build_session_timeline(sd, offset=0, limit=1, around_index=1)
    assert around["events"]
    at = build_session_timeline(sd, offset=0, limit=50, at_index=1, content_chars=4000)
    assert len(at["events"]) == 1
    assert at["events"][0]["index"] == 1
    short = build_session_timeline(sd, offset=0, limit=50, content_chars=4)
    for ev in short["events"]:
        body = str(ev.get("content") or "")
        if int(ev.get("contentLength") or 0) > 4:
            assert len(body) <= 4
            assert ev.get("contentTruncated") is True


def test_timeline_prompt_index_returns_whole_turn(tmp_path: Path) -> None:
    """promptIndex scopes to the turn segment, not only the user meta row."""
    sd = _write_session(tmp_path, "sess-prompt-tl")
    full = build_session_timeline(sd, offset=0, limit=50)
    assert full["total"] >= 3
    by_prompt = build_session_timeline(sd, offset=0, limit=50, prompt_index=2)
    # Fixture: user (promptIndex 2) + agent + tool in one turn.
    assert by_prompt["total"] == full["total"]
    assert len(by_prompt["events"]) == full["total"]
    kinds = {ev.get("kind") for ev in by_prompt["events"]}
    assert "user" in kinds
    assert "assistant" in kinds or any(
        "agent" in str(ev.get("type") or "").casefold() for ev in by_prompt["events"]
    )
    assert any(ev.get("kind") in {"tool", "tool_result"} for ev in by_prompt["events"])
    # Only the operator row carries _meta.promptIndex; others must still appear.
    with_meta = [ev for ev in by_prompt["events"] if ev.get("promptIndex") == 2]
    assert len(with_meta) == 1
    missing = build_session_timeline(sd, offset=0, limit=50, prompt_index=99)
    assert missing["total"] == 0
    assert missing["events"] == []


def test_build_session_turns(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "sess-turns")
    turns = build_session_turns(sd)
    assert turns["sessionId"] == "sess-turns"
    assert turns["total"] >= 1
    assert turns["turns"]
    row = turns["turns"][0]
    assert "eventCount" in row
    assert row.get("summary") == "hello user"
    assert row.get("userEventIndex") is not None
    assert row.get("assistantSummary") == "hello agent"
    assert row.get("assistantEventIndex") is not None


def test_build_session_usage(tmp_path: Path) -> None:
    sd = _write_session(tmp_path, "sess-usage")
    usage = build_session_usage(sd)
    assert usage["sessionId"] == "sess-usage"
    assert "hostTools" in usage
    assert "mcpServers" in usage


def test_build_session_overview_one_shot(tmp_path: Path) -> None:
    from groket.session.control_views import build_session_overview

    sd = _write_session(tmp_path, "sess-ov")
    ov = build_session_overview(sd)
    assert ov["sessionId"] == "sess-ov"
    assert "meta" in ov
    assert ov["turns"]["total"] >= 1
    assert ov["turns"]["turns"]
    t0 = ov["turns"]["turns"][0]
    assert "eventCount" in t0
    assert t0.get("summary") == "hello user"
    assert "eventIndexes" not in t0
    # Timeline is lazy: total only; clients call session/timeline for rows.
    assert ov["timeline"]["total"] >= 1
    assert ov["timeline"]["events"] == []
    assert ov["timeline"].get("lazy") is True
    assert "notes" in ov
    assert "schema" in ov["notes"]
    assert ov["notes"]["schema"]["fields"]
    assert {f["id"] for f in ov["notes"]["schema"]["fields"]} >= {"summary", "detail"}
    assert "findings" in ov
    assert ov["findings"]["total"] == 0
    page = build_session_timeline(sd, offset=0, limit=50)
    assert page["events"]
    kinds = {e.get("kind") for e in page["events"]}
    assert kinds & {"user", "agent", "tool", "other", "thought", "session"}
    for e in page["events"]:
        assert "heading" in e
        assert "kind" in e
        assert "turnIndex" in e
    # Operator conversation rows belong to sequential turn 0 in the fixture.
    conv = [e for e in page["events"] if e.get("kind") in ("user", "agent", "tool")]
    assert conv
    assert all(e.get("turnIndex") == 0 for e in conv)


def test_overview_includes_background_jobs_and_schedules(tmp_path: Path) -> None:
    sd = tmp_path / "sess-jobs-ov"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-jobs-ov"}, "generated_title": "jobs"}),
        encoding="utf-8",
    )
    term = sd / "terminal"
    term.mkdir()
    mon = term / "monitor-call-ov.log"
    mon.write_text("DONE\n", encoding="utf-8")
    updates = [
        {
            "timestamp": 10,
            "params": {
                "update": {
                    "sessionUpdate": "task_backgrounded",
                    "task_id": "job-ov",
                    "command": "watch",
                    "cwd": "/tmp",
                    "output_file": str(mon),
                    "description": "Watch board",
                }
            },
        },
        {
            "timestamp": 11,
            "params": {
                "update": {
                    "sessionUpdate": "scheduled_task_created",
                    "task_id": "sched-ov",
                    "prompt": "hourly ping",
                    "human_schedule": "every 1 hour",
                    "next_fire_at": "2026-08-18T23:00:00Z",
                }
            },
        },
    ]
    (sd / "updates.jsonl").write_text(
        "".join(json.dumps(u) + "\n" for u in updates), encoding="utf-8"
    )
    (sd / "resources_state.json").write_text(
        json.dumps(
            {
                "state": {
                    "grok_build.Scheduler": {
                        "tasks": [
                            {
                                "id": "sched-ov",
                                "intervalSecs": 3600,
                                "prompt": "hourly ping",
                                "recurring": True,
                                "durable": True,
                            }
                        ]
                    },
                    "grok_build.ReportedTaskCompletions": {"reported": []},
                }
            }
        ),
        encoding="utf-8",
    )
    ov = build_session_overview(sd)
    jobs = ov["backgroundJobs"]
    schedules = ov["schedules"]
    assert len(jobs) == 1
    assert jobs[0]["id"] == "job-ov"
    assert jobs[0]["kind"] == "monitor"
    assert jobs[0]["status"] == "done"
    assert jobs[0]["outputPath"] == str(mon)
    assert "logTail" not in jobs[0]
    assert jobs[0]["command"] == "watch"
    assert len(schedules) == 1
    assert schedules[0]["id"] == "sched-ov"
    assert schedules[0]["humanSchedule"] == "every 1 hour"
    assert schedules[0]["intervalSecs"] == 3600
    assert ov.get("workflows") == []
    page = build_session_timeline(sd, offset=0, limit=50)
    types = {e.get("type") for e in page["events"]}
    assert "task_backgrounded" in types
    assert "scheduled_task_created" in types
    assert "subagent_spawned" not in types


def test_overview_includes_workflows_without_script_or_journal_body(tmp_path: Path) -> None:
    sd = tmp_path / "sess-wf-ov"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-wf-ov"}, "generated_title": "wf"}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("", encoding="utf-8")
    d = sd / "workflows" / "wf_failed"
    d.mkdir(parents=True)
    (d / "state.json").write_text(
        json.dumps(
            {
                "version": 4,
                "state": {
                    "run_id": "wf_failed",
                    "name": "sprint-8",
                    "status": "failed",
                    "current_phase": "Kickoff",
                    "objective": "Engineering sprint",
                    "agents_used": 1,
                    "agent_budget": 64,
                    "elapsed_ms_floor": 150198,
                    "pause_message": "Variable not found: vissue_root",
                    "agents": [
                        {"agent_id": "ag-1", "label": "aik", "state": "done"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    (d / "journal.jsonl").write_text(
        json.dumps(
            {
                "kind": "spawn_agent",
                "result": {
                    "agent_id": "ag-1",
                    "success": True,
                    "output": {"summary": "Seated on existing sprint heading aik-7ao0."},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ov = build_session_overview(sd)
    rows = ov["workflows"]
    assert len(rows) == 1
    assert rows[0]["name"] == "sprint-8"
    assert rows[0]["status"] == "failed"
    assert rows[0]["phase"] == "Kickoff"
    assert "vissue_root" in rows[0]["pauseMessage"]
    assert rows[0]["children"][0]["label"] == "aik"
    dumped = json.dumps(ov)
    assert "Seated on existing sprint heading" not in dumped
    assert "let meta" not in dumped
    assert "fn gathering" not in dumped


def test_overview_does_not_resolve_workflow_child_paths(tmp_path: Path) -> None:
    """Glance leaves child ids; open still finds the sibling directory."""
    from unittest.mock import patch

    from groket.session.subagents import resolve_child_session_path

    sd = tmp_path / "sess-wf-child"
    sd.mkdir()
    child = tmp_path / "ag-1"
    child.mkdir()
    (child / "summary.json").write_text(
        json.dumps({"info": {"id": "ag-1"}, "generated_title": "aik"}),
        encoding="utf-8",
    )
    (child / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-wf-child"}, "generated_title": "wf"}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("", encoding="utf-8")
    d = sd / "workflows" / "wf_child"
    d.mkdir(parents=True)
    (d / "state.json").write_text(
        json.dumps(
            {
                "version": 4,
                "state": {
                    "run_id": "wf_child",
                    "name": "sprint",
                    "status": "complete",
                    "agents": [{"agent_id": "ag-1", "label": "aik", "state": "done"}],
                },
            }
        ),
        encoding="utf-8",
    )
    calls = 0
    real = resolve_child_session_path

    def counting(parent_dir: Path, child_session_id: str, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real(parent_dir, child_session_id, **kwargs)

    with patch("groket.session.subagents.resolve_child_session_path", side_effect=counting):
        ov = build_session_overview(sd)
    assert calls == 0
    kids = ov["workflows"][0]["children"]
    assert kids[0]["id"] == "ag-1"
    assert "path" not in kids[0]
    found = resolve_child_session_path(sd, "ag-1")
    assert found is not None
    assert found.resolve() == child.resolve()


def test_timeline_kind_workflows_keeps_workflow_tools(tmp_path: Path) -> None:
    sd = tmp_path / "sess-wf-kind"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-wf-kind"}, "generated_title": "wf"}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {
                    "timestamp": 1,
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "call-wf",
                            "title": "workflow",
                            "rawInput": {"script_path": "/repo/.grok/workflows/sprint.rhai"},
                        }
                    },
                },
                {
                    "timestamp": 2,
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "call-read",
                            "title": "read_file",
                            "rawInput": {"target_file": "x.py"},
                        }
                    },
                },
            )
        ),
        encoding="utf-8",
    )
    page = build_session_timeline(sd, offset=0, limit=50, kind="workflows")
    names = [e.get("toolName") for e in page["events"]]
    assert names == ["workflow"]


def test_overview_caps_assistant_preview_for_list(tmp_path: Path) -> None:
    """session/overview keeps short assistant previews; session/turns keeps long."""
    from groket.parser import parse_timeline
    from groket.session.control_views import (
        build_session_overview,
        build_session_turns,
        turn_segment_mapping,
    )
    from groket.session.turns import segment_timeline_turns

    sd = _write_session(tmp_path, "sess-asst-cap")
    long_agent = "A" * 2000
    # Append a long agent chunk so both paths see the same wrap-up.
    updates = (sd / "updates.jsonl").read_text(encoding="utf-8")
    extra = {
        "timestamp": 2000,
        "params": {
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": long_agent},
            }
        },
    }
    (sd / "updates.jsonl").write_text(updates + json.dumps(extra) + "\n", encoding="utf-8")
    ov = build_session_overview(sd)
    asst = ov["turns"]["turns"][0].get("assistantSummary") or ""
    assert len(asst) <= 401
    assert asst.endswith("…")
    turns = build_session_turns(sd)
    full = turns["turns"][0].get("assistantSummary") or ""
    assert len(full) >= 2000
    segs = segment_timeline_turns(parse_timeline(sd))
    short = turn_segment_mapping(segs[0], include_event_indexes=False, assistant_max_chars=400)
    assert len(str(short.get("assistantSummary") or "")) <= 401


def test_build_session_findings_maps_events_to_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Findings from analysis cache get sequential turnIndices via eventIndices."""
    sd = _write_session(tmp_path, "sess-find")
    page = build_session_timeline(sd, offset=0, limit=50)
    assert page["events"]
    # Pick a real event index from the parsed timeline.
    ev_idx = int(page["events"][0]["index"])

    cache_root = tmp_path / "cache"
    plugin_dir = cache_root / "analysis" / sd.name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "engine.json").write_text(
        json.dumps(
            {
                "_schema": 1,
                "_plugin_version": "0",
                "_trace_mtime": 0,
                "result": {
                    "analyzer_id": "engine",
                    "findings": [
                        {
                            "id": "f1",
                            "plugin_id": "engine",
                            "severity": "high",
                            "title": "Linked finding",
                            "detail": "points at an event",
                            "category": "test",
                            "event_indices": [ev_idx],
                            "update_indices": [],
                            "tool_call_ids": [],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "groket.paths.analysis_cache_dir",
        lambda: cache_root,
    )
    found = build_session_findings(sd)
    assert found["total"] == 1
    row = found["findings"][0]
    assert row["title"] == "Linked finding"
    assert row["eventIndices"] == [ev_idx]
    assert row["primaryEventIndex"] == ev_idx
    assert isinstance(row["turnIndices"], list)
    assert row["turnIndices"]  # resolved into at least one turn
    assert row["primaryTurnIndex"] == row["turnIndices"][0]

    ov = build_session_overview(sd)
    assert ov["findings"]["total"] == 1
    assert ov["findings"]["findings"][0]["primaryTurnIndex"] is not None


def test_finding_mapping_uses_typed_finding_fields() -> None:
    from groket.analysis.base import Finding
    from groket.models import Severity, TraceEvent
    from groket.session.control_views import finding_mapping
    from groket.session.turns import segment_timeline_turns

    ev = TraceEvent(
        index=3,
        event_type="user_message_chunk",
        content="<user_query>x</user_query>",
    )
    segs = segment_timeline_turns([ev])
    finding = Finding(
        id="f-issue",
        plugin_id="basic",
        severity=Severity.HIGH,
        title="Broke it",
        detail="missed a check",
        category="workflow",
        event_indices=[3],
        extras={"what_model_did": "ran", "where": "t0", "why": "missed", "pattern": "x"},
    )
    row = finding_mapping(finding, segs=segs)
    assert row["id"] == "f-issue"
    assert row["severity"] == "high"
    assert row["pluginId"] == "basic"
    assert row["extras"]["what_model_did"] == "ran"
    assert row["turnIndices"]


def test_timeline_event_kind_and_tool_family() -> None:
    from groket.models import TraceEvent
    from groket.session.control_views import timeline_event_mapping, tool_family

    assert tool_family("read_file") == "read"
    assert tool_family("run_terminal_command") == "shell"
    assert tool_family("search_tool") == "read"
    assert tool_family("use_tool") == "mcp"
    assert tool_family("foo__bar") == "mcp"
    ev = TraceEvent(
        index=1,
        event_type="tool_call",
        content="",
        tool_name="read_file",
        raw_input={"target_file": "/tmp/x"},
    )
    m = timeline_event_mapping(ev)
    assert m["kind"] == "tool"
    assert m["toolFamily"] == "read"
    assert m["heading"]
    assert m["preview"] == "read file /tmp/x"
    assert m["rawInput"] == {"target_file": "/tmp/x"}


def test_timeline_event_mapping_caps_huge_raw_input() -> None:
    from groket.models import TraceEvent
    from groket.session.control_views import timeline_event_mapping

    ev = TraceEvent(
        index=1,
        event_type="tool_call",
        tool_name="read_file",
        raw_input={"blob": "x" * 80_000},
    )
    m = timeline_event_mapping(ev, content_chars=200)
    raw = m["rawInput"]
    assert isinstance(raw, dict)
    assert raw.get("_truncated") is True
    assert len(str(raw.get("preview") or "")) <= 200


def test_build_session_timeline_reuses_turn_view_on_warm_pages(tmp_path: Path) -> None:
    """Second paged timeline call does not re-run full segment/map work."""
    from unittest.mock import patch

    import groket.session.control_views as cv

    sd = _write_session(tmp_path, "sess-warm-tl")
    cv.SessionOverview._turn_cache.clear()
    cv.SessionOverview._cache.clear()

    real_segment = cv.segment_timeline_turns
    real_map = cv.event_display_turn_map
    segment_calls = 0
    map_calls = 0

    def counting_segment(events):  # type: ignore[no-untyped-def]
        nonlocal segment_calls
        segment_calls += 1
        return real_segment(events)

    def counting_map(segs):  # type: ignore[no-untyped-def]
        nonlocal map_calls
        map_calls += 1
        return real_map(segs)

    with (
        patch.object(cv, "segment_timeline_turns", side_effect=counting_segment),
        patch.object(cv, "event_display_turn_map", side_effect=counting_map),
    ):
        page0 = build_session_timeline(sd, offset=0, limit=1)
        page1 = build_session_timeline(sd, offset=1, limit=1)
    assert page0["events"]
    assert page1["total"] == page0["total"]
    assert segment_calls == 1
    assert map_calls == 1
    # Cache entry present for this session.
    assert any(sd.name in k or str(sd) in k for k in cv.SessionOverview._turn_cache)


def test_build_session_overview_single_flight_and_cache(tmp_path: Path) -> None:
    """Parallel overview for one session builds once; warm re-call is cached."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch

    import groket.session.control_views as cv

    sd = _write_session(tmp_path, "sess-flight")
    cv.SessionOverview._cache.clear()
    cv.SessionOverview._inflight.clear()

    body_calls = 0
    orig = cv.SessionOverview.uncached
    gate = threading.Event()
    entered = threading.Event()

    def slow_body(session_dir, *, work_dir=None):  # type: ignore[no-untyped-def]
        nonlocal body_calls
        body_calls += 1
        entered.set()
        assert gate.wait(timeout=5.0)
        return orig(session_dir, work_dir=work_dir)

    try:
        with (
            ThreadPoolExecutor(max_workers=4) as pool,
            patch.object(cv.SessionOverview, "uncached", side_effect=slow_body),
        ):
            futs = [pool.submit(build_session_overview, sd) for _ in range(4)]
            assert entered.wait(timeout=5.0)
            gate.set()
            results = [f.result(timeout=15.0) for f in futs]
        assert body_calls == 1
        assert all(r["sessionId"] == "sess-flight" for r in results)
        assert all(r is results[0] for r in results)
        # Warm hit: no second body call when inputs unchanged.
        warm = build_session_overview(sd)
        assert body_calls == 1
        assert warm is results[0]
    finally:
        cv.SessionOverview._inflight.clear()
        cv.SessionOverview._cache.clear()


def test_overview_stamp_monitor_done_not_signals_or_shell_log(tmp_path: Path) -> None:
    """Monitor last-line status busts cache; signals.json and call-*.log do not."""
    from unittest.mock import patch

    import groket.session.control_views as cv

    sd = tmp_path / "sess-stamp"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-stamp"}, "generated_title": "stamp"}),
        encoding="utf-8",
    )
    term = sd / "terminal"
    term.mkdir()
    mon = term / "monitor-call-live.log"
    mon.write_text("still going\n", encoding="utf-8")
    (term / "call-shell.log").write_text("partial\n", encoding="utf-8")
    (sd / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 10,
                "params": {
                    "update": {
                        "sessionUpdate": "task_backgrounded",
                        "task_id": "job-mon",
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
    cv.SessionOverview._cache.clear()
    cv.SessionOverview._inflight.clear()
    parses = 0
    real = cv.parse_timeline

    def counting_parse(session_dir, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal parses
        parses += 1
        return real(session_dir, **kwargs)

    with patch.object(cv, "parse_timeline", side_effect=counting_parse):
        first = build_session_overview(sd)
        assert first["backgroundJobs"][0]["status"] == "running"
        assert parses == 1
        (sd / "signals.json").write_text('{"contextWindowUsage": 1}', encoding="utf-8")
        (term / "call-shell.log").write_text("partial\n" + ("x" * 4000), encoding="utf-8")
        warm = build_session_overview(sd)
        assert warm is first
        assert parses == 1
        mon.write_text("still going\nDONE\n", encoding="utf-8")
        done = build_session_overview(sd)
        assert done is not first
        assert done["backgroundJobs"][0]["status"] == "done"
        assert parses == 2


def _write_jobs_workflows_session(root: Path, name: str = "sess-reuse") -> Path:
    """Session with two jobs, two workflow runs, and many non-matching events."""
    sd = root / name
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": "reuse"}),
        encoding="utf-8",
    )
    term = sd / "terminal"
    term.mkdir()
    mon_a = term / "monitor-call-a.log"
    mon_a.write_text("DONE\n", encoding="utf-8")
    mon_b = term / "monitor-call-b.log"
    mon_b.write_text("FAILED\n", encoding="utf-8")
    updates: list[dict[str, object]] = []
    for i in range(40):
        updates.append(
            {
                "timestamp": i,
                "params": {
                    "update": {
                        "sessionUpdate": "tool_call",
                        "toolCallId": f"fill-{i}",
                        "title": "read_file",
                        "rawInput": {"target_file": f"/tmp/f{i}"},
                    }
                },
            }
        )
    updates.append(
        {
            "timestamp": 100,
            "params": {
                "update": {
                    "sessionUpdate": "task_backgrounded",
                    "task_id": "job-a",
                    "tool_call_id": "call-a",
                    "command": "watch a",
                    "cwd": "/tmp",
                    "output_file": str(mon_a),
                    "description": "Watch a",
                }
            },
        }
    )
    updates.append(
        {
            "timestamp": 101,
            "params": {
                "update": {
                    "sessionUpdate": "task_backgrounded",
                    "task_id": "job-b",
                    "tool_call_id": "call-b",
                    "command": "watch b",
                    "cwd": "/tmp",
                    "output_file": str(mon_b),
                    "description": "Watch b",
                }
            },
        }
    )
    updates.append(
        {
            "timestamp": 102,
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-wf-a",
                    "title": "workflow",
                    "rawInput": {"run_id": "wf_a", "name": "sprint-a"},
                }
            },
        }
    )
    updates.append(
        {
            "timestamp": 103,
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-wf-b",
                    "title": "workflow",
                    "rawInput": {"run_id": "wf_b", "name": "sprint-b"},
                }
            },
        }
    )
    (sd / "updates.jsonl").write_text(
        "".join(json.dumps(u) + "\n" for u in updates),
        encoding="utf-8",
    )
    for run_id, wf_name, status in (
        ("wf_a", "sprint-a", "complete"),
        ("wf_b", "sprint-b", "failed"),
    ):
        d = sd / "workflows" / run_id
        d.mkdir(parents=True)
        (d / "state.json").write_text(
            json.dumps(
                {
                    "version": 4,
                    "state": {
                        "run_id": run_id,
                        "name": wf_name,
                        "status": status,
                        "current_phase": "Kickoff",
                        "objective": wf_name,
                        "agents": [],
                    },
                }
            ),
            encoding="utf-8",
        )
    return sd


def test_overview_reuses_jobs_when_only_timeline_grows(tmp_path: Path) -> None:
    """Timeline-only append keeps prior job/workflow rows; bookends stay first hits."""
    import groket.session.control_views as cv
    from groket.parser import parse_timeline
    from groket.session.jobs import SessionJobs, job_event_index, load_session_jobs
    from groket.session.workflows import workflow_event_index

    sd = _write_jobs_workflows_session(tmp_path)
    cv.SessionOverview._cache.clear()
    cv.SessionOverview._inflight.clear()
    SessionJobs._row_cache.clear()

    loads = 0
    real = SessionJobs.load

    def counting_load(session_dir: Path, events: object = None) -> SessionJobs:
        nonlocal loads
        loads += 1
        return real(session_dir, events)

    setattr(SessionJobs, "load", counting_load)
    try:
        first = build_session_overview(sd)
        assert loads == 1
        assert {j["id"] for j in first["backgroundJobs"]} == {"job-a", "job-b"}
        assert {w["id"] for w in first["workflows"]} == {"wf_a", "wf_b"}
        (sd / "updates.jsonl").write_text(
            (sd / "updates.jsonl").read_text(encoding="utf-8")
            + json.dumps(
                {
                    "timestamp": 200,
                    "params": {
                        "update": {
                            "sessionUpdate": "user_message_chunk",
                            "content": {"type": "text", "text": "later"},
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        second = build_session_overview(sd)
        assert loads == 1
        assert [j["id"] for j in second["backgroundJobs"]] == [
            j["id"] for j in first["backgroundJobs"]
        ]
        assert [j["status"] for j in second["backgroundJobs"]] == [
            j["status"] for j in first["backgroundJobs"]
        ]
        assert [w["id"] for w in second["workflows"]] == [w["id"] for w in first["workflows"]]
        assert [w["status"] for w in second["workflows"]] == [
            w["status"] for w in first["workflows"]
        ]
        events = parse_timeline(sd)
        packed = load_session_jobs(sd, events)
        by_job = {j["id"]: j["eventIndex"] for j in second["backgroundJobs"]}
        by_wf = {w["id"]: w["eventIndex"] for w in second["workflows"]}
        for job in packed.jobs:
            assert by_job[job.job_id] == job_event_index(job, events)
        for run in packed.workflows:
            assert by_wf[run.run_id] == workflow_event_index(run, events)
    finally:
        setattr(SessionJobs, "load", real)
        cv.SessionOverview._cache.clear()
        cv.SessionOverview._inflight.clear()
        SessionJobs._row_cache.clear()


def test_overview_includes_new_timeline_job_after_first_build(tmp_path: Path) -> None:
    """A later task_backgrounded bookend must appear even when job files are still."""
    import groket.session.control_views as cv

    sd = _write_session(tmp_path, "sess-new-job")
    term = sd / "terminal"
    term.mkdir()
    bg_log = term / "call-shell.log"
    bg_log.write_text("hello from bg\n", encoding="utf-8")
    cv.SessionOverview._cache.clear()
    cv.SessionOverview._inflight.clear()
    if hasattr(cv, "_job_payload_cache"):
        cv._job_payload_cache.clear()
    try:
        first = build_session_overview(sd)
        assert first["backgroundJobs"] == []
        (sd / "updates.jsonl").write_text(
            (sd / "updates.jsonl").read_text(encoding="utf-8")
            + json.dumps(
                {
                    "timestamp": 2000,
                    "params": {
                        "update": {
                            "sessionUpdate": "task_backgrounded",
                            "task_id": "job-bg-1",
                            "tool_call_id": "call-bg",
                            "command": "sleep 30",
                            "cwd": "/tmp/work",
                            "output_file": str(bg_log),
                            "description": "long sleep",
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        second = build_session_overview(sd)
        assert [j["id"] for j in second["backgroundJobs"]] == ["job-bg-1"]
        assert second["backgroundJobs"][0]["status"] == "running"
    finally:
        cv.SessionOverview._cache.clear()
        cv.SessionOverview._inflight.clear()
        if hasattr(cv, "_job_payload_cache"):
            cv._job_payload_cache.clear()


def test_overview_updates_job_status_when_timeline_gains_completed(
    tmp_path: Path,
) -> None:
    """A later task_completed bookend must refresh status when job files are still."""
    import groket.session.control_views as cv

    sd = _write_session(tmp_path, "sess-finish-job")
    term = sd / "terminal"
    term.mkdir()
    bg_log = term / "call-shell.log"
    bg_log.write_text("hello from bg\n", encoding="utf-8")
    (sd / "updates.jsonl").write_text(
        (sd / "updates.jsonl").read_text(encoding="utf-8")
        + json.dumps(
            {
                "timestamp": 2000,
                "params": {
                    "update": {
                        "sessionUpdate": "task_backgrounded",
                        "task_id": "job-bg-1",
                        "tool_call_id": "call-bg",
                        "command": "sleep 30",
                        "cwd": "/tmp/work",
                        "output_file": str(bg_log),
                        "description": "long sleep",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cv.SessionOverview._cache.clear()
    cv.SessionOverview._inflight.clear()
    if hasattr(cv, "_job_payload_cache"):
        cv._job_payload_cache.clear()
    try:
        first = build_session_overview(sd)
        assert first["backgroundJobs"][0]["id"] == "job-bg-1"
        assert first["backgroundJobs"][0]["status"] == "running"
        (sd / "updates.jsonl").write_text(
            (sd / "updates.jsonl").read_text(encoding="utf-8")
            + json.dumps(
                {
                    "timestamp": 2001,
                    "params": {
                        "update": {
                            "sessionUpdate": "task_completed",
                            "will_wake": False,
                            "task_snapshot": {
                                "task_id": "job-bg-1",
                                "command": "sleep 30",
                                "cwd": "/tmp/work",
                                "output_file": str(bg_log),
                                "description": "long sleep",
                                "kind": "bash",
                                "completed": True,
                                "start_time": {"secs_since_epoch": 1_700_000_000},
                                "end_time": {"secs_since_epoch": 1_700_000_010},
                            },
                        }
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        second = build_session_overview(sd)
        assert [j["id"] for j in second["backgroundJobs"]] == ["job-bg-1"]
        assert second["backgroundJobs"][0]["status"] == "done"
    finally:
        cv.SessionOverview._cache.clear()
        cv.SessionOverview._inflight.clear()
        if hasattr(cv, "_job_payload_cache"):
            cv._job_payload_cache.clear()


def test_overview_bookend_indexes_one_event_walk(tmp_path: Path) -> None:
    """Overview sets bookend indexes from one shared walk, not one scan per row."""
    import groket.session.control_views as cv
    from groket.session import jobs as jobs_mod

    sd = _write_jobs_workflows_session(tmp_path)
    cv.SessionOverview._cache.clear()
    cv.SessionOverview._inflight.clear()
    if hasattr(cv, "_job_payload_cache"):
        cv._job_payload_cache.clear()

    walks = 0
    real_set = jobs_mod.set_bookend_indexes

    def counting_set(
        events: object,
        jobs: object,
        workflows: object,
    ) -> None:
        nonlocal walks
        walks += 1
        real_set(events, jobs, workflows)  # type: ignore[arg-type]

    per_row = 0
    real_job_idx = jobs_mod.job_event_index
    from groket.session import workflows as wf_mod

    real_wf_idx = wf_mod.workflow_event_index

    def counting_job(*args: object, **kwargs: object) -> object:
        nonlocal per_row
        per_row += 1
        return real_job_idx(*args, **kwargs)

    def counting_wf(*args: object, **kwargs: object) -> object:
        nonlocal per_row
        per_row += 1
        return real_wf_idx(*args, **kwargs)

    jobs_mod.set_bookend_indexes = counting_set  # type: ignore[assignment]
    jobs_mod.job_event_index = counting_job  # type: ignore[assignment]
    wf_mod.workflow_event_index = counting_wf  # type: ignore[assignment]
    if hasattr(cv, "set_bookend_indexes"):
        cv.set_bookend_indexes = counting_set  # type: ignore[assignment]
    try:
        ov = build_session_overview(sd)
        assert walks == 1
        assert per_row == 0
        by_job = {j["id"]: j["eventIndex"] for j in ov["backgroundJobs"]}
        by_wf = {w["id"]: w["eventIndex"] for w in ov["workflows"]}
        assert by_job["job-a"] == 40
        assert by_job["job-b"] == 41
        assert by_wf["wf_a"] == 42
        assert by_wf["wf_b"] == 43
    finally:
        jobs_mod.set_bookend_indexes = real_set  # type: ignore[assignment]
        jobs_mod.job_event_index = real_job_idx  # type: ignore[assignment]
        wf_mod.workflow_event_index = real_wf_idx  # type: ignore[assignment]
        cv.SessionOverview._cache.clear()
        cv.SessionOverview._inflight.clear()
        if hasattr(cv, "_job_payload_cache"):
            cv._job_payload_cache.clear()


def test_timeline_system_reminder_not_labeled_user() -> None:
    """Harness user_message_chunk chrome must not paint as operator User."""
    from groket.models import TraceEvent
    from groket.session.control_views import timeline_event_mapping

    bg = (
        "<system-reminder>\nBackground task "
        '"call-0001a5e8-7301-4869-8c16-deaadffea580-51" completed (exit code: 0).\n'
        "Command: /bin/chmod +x /tmp/x\n"
        "Use get_command_or_subagent_output(...) to see the full output.\n"
        "</system-reminder>"
    )
    m = timeline_event_mapping(TraceEvent(index=252, event_type="user_message_chunk", content=bg))
    assert m["kind"] == "system"
    assert m["heading"] == "Background task"
    assert m["harnessChrome"] is True
    assert m["heading"] != "User"
    assert "<system-reminder>" not in str(m["content"])
    assert "Background task" in str(m["content"])
    assert "chmod" in str(m["content"])

    skills = (
        "<system-reminder>\nThe following skills are available for use:\n"
        "- check-work\n</system-reminder>"
    )
    m2 = timeline_event_mapping(
        TraceEvent(index=3, event_type="user_message_chunk", content=skills)
    )
    assert m2["kind"] == "system"
    assert m2["heading"] == "System reminder"

    real = timeline_event_mapping(
        TraceEvent(index=4, event_type="user_message_chunk", content="please fix the flaky test")
    )
    assert real["kind"] == "user"
    assert real["heading"] == "User"
    assert real["harnessChrome"] is False


def test_timeline_query_page_matches_full_fixture_prefix(tmp_path: Path) -> None:
    """Server query: first page of hits is a prefix of the complete match set."""
    sd = tmp_path / "big-tl"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "big-tl"}, "generated_title": "Big"}),
        encoding="utf-8",
    )
    lines: list[str] = []
    for i in range(6000):
        text = f"row {i} carries needle-token in the body" if i % 19 == 0 else f"ordinary row {i}"
        kind = "user_message_chunk" if i % 2 == 0 else "agent_message_chunk"
        lines.append(
            json.dumps(
                {
                    "timestamp": 1000 + i,
                    "params": {
                        "update": {
                            "sessionUpdate": kind,
                            "content": {"type": "text", "text": text},
                        }
                    },
                }
            )
        )
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (sd / "events.jsonl").write_text("{}\n", encoding="utf-8")
    full = build_session_timeline(sd, offset=0, limit=8000, query="needle-token")
    page = build_session_timeline(sd, offset=0, limit=40, query="needle-token")
    full_ids = [ev["index"] for ev in full["events"]]
    page_ids = [ev["index"] for ev in page["events"]]
    assert full["total"] == len(full_ids)
    assert full["total"] > 40
    assert page_ids == full_ids[:40]
    for ev in full["events"]:
        assert ev.get("matchField")
        assert "needle-token" in str(ev.get("matchSnippet") or "").casefold()


def test_build_session_diff_lists_rewind_files(tmp_path: Path) -> None:
    sd = tmp_path / "sess-diff"
    sd.mkdir()
    (sd / "rewind_points.jsonl").write_text(
        json.dumps(
            {
                "prompt_index": 1,
                "file_snapshots": {"app.py": "old"},
                "after_snapshots": {"app.py": "new"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    payload = build_session_diff(sd)
    assert payload["source"] == "rewind_points"
    assert len(payload["points"]) == 1
    files = payload["points"][0]["files"]
    assert files[0]["path"] == "app.py"
    assert "app.py" in files[0]["unified"]


def test_timeline_kind_chrome_is_session_not_user() -> None:
    """Harness user-chrome is Session on both the TUI view check and the wire filter."""
    from conftest import make_trace_event
    from groket.session.turns import event_matches_timeline_kind
    from groket.ui.screens.browser import BrowserScreen

    chrome = make_trace_event(
        index=0,
        event_type="user_message_chunk",
        content="<system-reminder>do not mention</system-reminder>",
    )
    user = make_trace_event(index=1, event_type="user_message_chunk", content="please fix tests")
    tool = make_trace_event(index=2, event_type="tool_call", tool_name="read_file")
    wf = make_trace_event(index=3, event_type="tool_call", tool_name="workflow")
    bg = make_trace_event(index=4, event_type="task_backgrounded")
    err = make_trace_event(index=5, event_type="session_error", is_error=True)
    screen = BrowserScreen.__new__(BrowserScreen)
    for ev, mode, want in (
        (chrome, "user", False),
        (user, "user", True),
        (chrome, "sess", True),
        (tool, "tools", True),
        (wf, "workflows", True),
        (bg, "background", True),
        (err, "errors", True),
        (user, "tools", False),
    ):
        assert event_matches_timeline_kind(ev, mode) is want
        assert screen._event_matches_timeline_view(ev, mode) is want
