"""Classify, hide, resolve, and turn-link Grok subagent sessions."""

from __future__ import annotations

import json
from pathlib import Path

from groket.models import ToolInputBag, TraceEvent
from groket.parser import find_sessions, parse_timeline
from groket.session.sources import (
    ORIGIN_HOST,
    ORIGIN_WORK,
    SessionScanRoot,
    collect_host_session_dirs,
    collect_session_dirs,
)
from groket.session.subagents import (
    compact_child_chrome,
    drop_subagent_sessions,
    is_nested_subagent_stub,
    is_subagent_kind,
    is_subagent_session_dir,
    normalize_run_status,
    parent_session_dir,
    resolve_child_session_path,
    session_changed_targets,
    subagent_runs_for_session,
)
from groket.session.turns import TurnSegment


def _write_session(
    path: Path,
    *,
    kind: str = "",
    title: str = "sess",
    updates: str = "{}\n",
) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {
        "info": {"id": path.name},
        "generated_title": title,
        "session_kind": kind,
    }
    (path / "summary.json").write_text(json.dumps(body), encoding="utf-8")
    (path / "updates.jsonl").write_text(updates, encoding="utf-8")
    return path


def test_compact_child_chrome_only_for_single_turn_subagent() -> None:
    assert compact_child_chrome("subagent", 1)
    assert compact_child_chrome("subagent_fork", 1)
    assert not compact_child_chrome("subagent", 2)
    assert not compact_child_chrome("subagent", 0)
    assert not compact_child_chrome("fork", 1)
    assert not compact_child_chrome("", 1)


def test_is_subagent_kind_excludes_operator_fork() -> None:
    assert is_subagent_kind("subagent")
    assert is_subagent_kind("subagent_fork")
    assert is_subagent_kind("subagent_resume")
    assert is_subagent_kind("Subagent_Fork")
    assert not is_subagent_kind("fork")
    assert not is_subagent_kind("")
    assert not is_subagent_kind("interactive")


def test_drop_nested_same_token_and_cross_token(tmp_path: Path) -> None:
    host = tmp_path / "sessions"
    token_a = host / "%2Fproj-a"
    token_b = host / "%2Fproj-b"
    parent = _write_session(token_a / "parent-1", title="parent")
    nested = token_a / "parent-1" / "subagents" / "child-1"
    nested.mkdir(parents=True)
    (nested / "meta.json").write_text("{}", encoding="utf-8")
    same = _write_session(token_a / "child-1", kind="", title="same-token mirror")
    cross = _write_session(token_b / "child-1", kind="", title="cross-token mirror")
    fork = _write_session(token_a / "op-fork", kind="fork", title="operator fork")
    kept = drop_subagent_sessions([parent, nested, same, cross, fork])
    names = {p.name for p in kept}
    assert names == {"parent-1", "op-fork"}


def test_drop_by_session_kind_without_nested_stub(tmp_path: Path) -> None:
    root = tmp_path / "sessions" / "%2Fws"
    primary = _write_session(root / "main", kind="")
    sub = _write_session(root / "child-kind", kind="subagent")
    fork = _write_session(root / "child-fork", kind="subagent_fork")
    resume = _write_session(root / "child-resume", kind="subagent_resume")
    kept = {p.name for p in drop_subagent_sessions([primary, sub, fork, resume])}
    assert kept == {"main"}


def test_find_sessions_hides_kinds_and_cross_token(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    token_a = traces / "run-1" / "%2Fworkspace"
    token_b = traces / "run-1" / "%2Fother"
    parent = _write_session(token_a / "019f-parent")
    (parent / "subagents" / "019f-child").mkdir(parents=True)
    _write_session(token_a / "019f-child")
    _write_session(token_b / "019f-kind", kind="subagent")
    _write_session(token_b / "019f-forked", kind="subagent_fork")
    _write_session(token_b / "019f-resume", kind="subagent_resume")
    op = _write_session(token_a / "019f-op-fork", kind="fork")
    found = find_sessions(traces)
    names = {p.name for p in found}
    assert names == {"019f-parent", "019f-op-fork"}
    assert op in found
    assert parent in found


def test_collect_host_and_catalog_hide_subagents(tmp_path: Path) -> None:
    host = tmp_path / "host"
    token_a = host / "%2Fhome%2Fali"
    token_b = host / "%2Fmnt%2Fdev"
    parent = _write_session(token_a / "p1")
    (parent / "subagents" / "c1").mkdir(parents=True)
    _write_session(token_b / "c1", kind="subagent")
    _write_session(token_a / "op-fork", kind="fork")
    host_found = collect_host_session_dirs(host)
    assert {p.name for p in host_found} == {"p1", "op-fork"}
    work = tmp_path / "work" / "runs" / "traces"
    _write_session(work / "eval-1")
    roots = [
        SessionScanRoot(origin=ORIGIN_WORK, path=work),
        SessionScanRoot(origin=ORIGIN_HOST, path=host),
    ]
    rows = collect_session_dirs(roots)
    names = {p.name for p, _ in rows}
    assert "c1" not in names
    assert "eval-1" in names
    assert "p1" in names
    assert "op-fork" in names


def test_parent_and_changed_targets_for_nested_and_sibling(tmp_path: Path) -> None:
    token = tmp_path / "%2Fws"
    parent = _write_session(token / "parent-1")
    nested = token / "parent-1" / "subagents" / "child-1"
    nested.mkdir(parents=True)
    (nested / "meta.json").write_text("{}", encoding="utf-8")
    sibling = _write_session(token / "child-1", kind="subagent")
    assert parent_session_dir(nested) == parent
    assert parent_session_dir(sibling) == parent
    nested_targets = {p.name for p in session_changed_targets(nested)}
    assert nested_targets == {"parent-1"}
    sibling_targets = {p.name for p in session_changed_targets(sibling)}
    assert sibling_targets == {"parent-1", "child-1"}
    assert [p.name for p in session_changed_targets(parent)] == ["parent-1"]


def test_is_subagent_session_dir_path_and_kind(tmp_path: Path) -> None:
    nested = tmp_path / "p" / "subagents" / "c"
    nested.mkdir(parents=True)
    assert is_subagent_session_dir(nested)
    kinded = _write_session(tmp_path / "k", kind="subagent_fork")
    assert is_subagent_session_dir(kinded)
    primary = _write_session(tmp_path / "ok", kind="fork")
    assert not is_subagent_session_dir(primary)
    under_named = _write_session(tmp_path / "subagents" / "run" / "sess", kind="fork")
    assert not is_nested_subagent_stub(under_named)
    assert not is_subagent_session_dir(under_named)


def test_normalize_run_status_keeps_harness_words() -> None:
    assert normalize_run_status("completed", finished=True) == "completed"
    assert normalize_run_status("cancelled", finished=True) == "cancelled"
    assert normalize_run_status("canceled", finished=True) == "cancelled"
    assert normalize_run_status("error", finished=True) == "failed"
    assert normalize_run_status("", finished=False) == "running"


def test_resolve_child_prefers_cross_cwd_mirror(tmp_path: Path) -> None:
    token_a = tmp_path / "%2Fa"
    token_b = tmp_path / "%2Fb"
    parent = _write_session(token_a / "parent")
    stub = parent / "subagents" / "child-x"
    stub.mkdir(parents=True)
    (stub / "meta.json").write_text("{}", encoding="utf-8")
    mirror = _write_session(token_b / "child-x")
    (mirror / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    hit = resolve_child_session_path(parent, "child-x", search_roots=[tmp_path])
    assert hit == mirror


def test_resolve_child_missing_returns_none(tmp_path: Path) -> None:
    parent = _write_session(tmp_path / "parent")
    (parent / "subagents" / "ghost").mkdir(parents=True)
    assert resolve_child_session_path(parent, "ghost") is None


def test_parse_spawn_finish_keeps_child_id_and_stats(tmp_path: Path) -> None:
    sd = tmp_path / "parent"
    sd.mkdir()
    lines = [
        json.dumps(
            {
                "timestamp": 10,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_spawned",
                        "description": "worker",
                        "subagentType": "coder",
                        "childSessionId": "child-99",
                        "subagentId": "sa-1",
                        "parentPromptId": "2",
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 11,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_finished",
                        "childSessionId": "child-99",
                        "status": "completed",
                        "durationMs": 1500,
                        "toolCalls": 4,
                        "turns": 2,
                        "tokensUsed": 80,
                    }
                },
            }
        ),
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    events = parse_timeline(sd)
    spawn = next(e for e in events if e.event_type == "subagent_spawned")
    finish = next(e for e in events if e.event_type == "subagent_finished")
    assert spawn.content == "worker"
    assert spawn.raw_input.as_str("childSessionId") == "child-99"
    assert "completed" in finish.content
    assert "duration_ms" not in finish.content
    assert finish.raw_input.as_str("status") == "completed"
    assert finish.raw_input.raw().get("durationMs") == 1500


def test_runs_share_parent_prompt_and_running_vs_done(tmp_path: Path) -> None:
    parent = _write_session(tmp_path / "parent")
    child_a = _write_session(tmp_path / "child-a")
    spawn_a = TraceEvent(
        index=2,
        event_type="subagent_spawned",
        raw_input=ToolInputBag(
            {
                "childSessionId": "child-a",
                "subagentId": "sa-a",
                "parentPromptId": "7",
                "subagentType": "explore",
                "description": "look",
            }
        ),
    )
    spawn_b = TraceEvent(
        index=3,
        event_type="subagent_spawned",
        raw_input=ToolInputBag(
            {
                "childSessionId": "child-b",
                "subagentId": "sa-b",
                "parentPromptId": "7",
                "subagentType": "coder",
                "description": "edit",
            }
        ),
    )
    finish_a = TraceEvent(
        index=8,
        event_type="subagent_finished",
        raw_input=ToolInputBag(
            {
                "childSessionId": "child-a",
                "status": "completed",
                "durationMs": 900,
            }
        ),
    )
    segs = [
        TurnSegment(turn_index=0, turn_number=0, prompt_index=7),
    ]
    runs = subagent_runs_for_session(
        parent,
        [spawn_a, spawn_b, finish_a],
        segs,
        {2: 0, 3: 0, 8: 0},
        search_roots=[tmp_path],
    )
    assert len(runs) == 2
    assert all(r.parent_turn_index == 0 for r in runs)
    by_id = {r.child_session_id: r for r in runs}
    assert by_id["child-a"].status == "completed"
    assert by_id["child-a"].openable
    assert by_id["child-a"].child_path == child_a
    assert by_id["child-b"].status == "running"
    assert not by_id["child-b"].openable


def test_control_turns_and_timeline_expose_runs(tmp_path: Path) -> None:
    from groket.session.control_views import build_session_timeline, build_session_turns

    parent = _write_session(tmp_path / "parent")
    _write_session(tmp_path / "child-99")
    lines = [
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "go"},
                        "_meta": {"promptIndex": 3},
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 2,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_spawned",
                        "description": "worker",
                        "subagentType": "coder",
                        "childSessionId": "child-99",
                        "subagentId": "sa-1",
                        "parentPromptId": "3",
                    }
                },
            }
        ),
        json.dumps(
            {
                "timestamp": 3,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_finished",
                        "childSessionId": "child-99",
                        "status": "completed",
                        "durationMs": 400,
                        "toolCalls": 2,
                    }
                },
            }
        ),
    ]
    (parent / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    turns = build_session_turns(parent)
    runs = turns.get("subagentRuns")
    assert isinstance(runs, list) and len(runs) == 1
    assert runs[0]["childSessionId"] == "child-99"
    assert runs[0]["openable"] is True
    assert turns["turns"][0]["subagentRuns"][0]["childSessionId"] == "child-99"
    tl = build_session_timeline(parent, offset=0, limit=50)
    subs = build_session_timeline(parent, offset=0, limit=50, kind="subagents")
    assert subs["total"] == 2
    assert {e["type"] for e in subs["events"]} == {"subagent_spawned", "subagent_finished"}
    spawn = next(e for e in tl["events"] if e["type"] == "subagent_spawned")
    finish = next(e for e in tl["events"] if e["type"] == "subagent_finished")
    assert spawn["childSessionId"] == "child-99"
    assert finish["durationMs"] == 400
    assert finish["toolCalls"] == 2
    assert finish["subagentStatus"] == "completed"


def test_subagent_runs_from_overview_skip_disk(tmp_path: Path) -> None:
    from unittest.mock import patch

    from groket.session.control_views import build_session_overview
    from groket.session.subagents import subagent_runs_for_view

    parent = _write_session(tmp_path / "parent-ov")
    _write_session(tmp_path / "child-99")
    (parent / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 2,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_spawned",
                        "childSessionId": "child-99",
                        "subagentType": "coder",
                        "description": "worker",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ov = build_session_overview(parent)
    with patch(
        "groket.session.subagents.subagent_runs_for_session",
        side_effect=AssertionError("disk merge"),
    ):
        runs = subagent_runs_for_view(ov, parent, [], [], {})
    assert len(runs) == 1
    assert runs[0].child_session_id == "child-99"
    assert runs[0].openable is True


def test_subagent_list_preview_is_not_the_dump() -> None:
    from groket.session.subagents import subagent_list_preview

    dump = "Subagent finished  01a016d1-4df7-7d30-b99f-65289aa0b417  completed  duration_ms=96555"
    preview = subagent_list_preview("subagent_finished", {}, dump)
    assert preview == "completed"
    assert "01a016d1" not in preview
    assert "duration_ms" not in preview
    spawn = subagent_list_preview(
        "subagent_spawned",
        {},
        "Spawned general-purpose: Investigate the bug",
    )
    assert spawn == "Investigate the bug"
