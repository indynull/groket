"""Load Grok workflow runs from the on-disk ``workflows/wf_*`` tree."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_trace_event
from groket.models import ToolInputBag
from groket.session.jobs import load_session_jobs
from groket.session.workflows import (
    WorkflowRun,
    load_session_workflows,
    workflow_event_index,
    workflow_for_event,
    workflow_list_preview,
    workflow_name_from_raw,
)


def _write_run(
    root: Path,
    run_id: str,
    *,
    name: str,
    status: str,
    phase: str = "",
    objective: str = "",
    agents_used: int = 0,
    agent_budget: int = 64,
    elapsed_ms: int = 0,
    pause_message: str = "",
    agents: list[dict[str, object]] | None = None,
    journal: list[dict[str, object]] | None = None,
) -> Path:
    d = root / "workflows" / run_id
    d.mkdir(parents=True)
    state = {
        "version": 4,
        "state": {
            "run_id": run_id,
            "name": name,
            "status": status,
            "current_phase": phase,
            "objective": objective,
            "agents_used": agents_used,
            "agent_budget": agent_budget,
            "elapsed_ms_floor": elapsed_ms,
            "pause_message": pause_message,
            "agents": agents or [],
        },
    }
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    if journal:
        (d / "journal.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in journal),
            encoding="utf-8",
        )
    return d


def test_load_session_workflows_reads_complete_and_failed(tmp_path: Path) -> None:
    sd = tmp_path / "sess-wf"
    sd.mkdir()
    _write_run(
        sd,
        "wf_complete",
        name="sprint-9",
        status="complete",
        phase="Retrospective",
        objective="Engineering sprint: aik then seated trees",
        agents_used=22,
        agent_budget=64,
        elapsed_ms=4681613,
        agents=[
            {
                "agent_id": "01aaa-aik",
                "label": "aik",
                "phase": "Aik",
                "state": "done",
            }
        ],
    )
    _write_run(
        sd,
        "wf_failed",
        name="sprint-8",
        status="failed",
        phase="Kickoff",
        objective="Engineering sprint",
        agents_used=1,
        agent_budget=64,
        elapsed_ms=150198,
        pause_message="Variable not found: vissue_root (line 155, position 28)",
        journal=[
            {
                "seq": 0,
                "kind": "spawn_agent",
                "result": {
                    "agent_id": "01aaa-kick",
                    "success": True,
                    "cancelled": False,
                    "output": {"ok": True, "summary": "Seated on existing sprint heading"},
                },
            }
        ],
    )
    runs = load_session_workflows(sd)
    assert [r.name for r in runs] == ["sprint-9", "sprint-8"] or {r.name for r in runs} == {
        "sprint-9",
        "sprint-8",
    }
    by_name = {r.name: r for r in runs}
    done = by_name["sprint-9"]
    assert done.status == "complete"
    assert done.phase == "Retrospective"
    assert done.agents_used == 22
    assert done.agent_budget == 64
    assert done.elapsed_ms == 4681613
    assert done.children[0].label == "aik"
    assert done.children[0].success is True
    failed = by_name["sprint-8"]
    assert failed.status == "failed"
    assert failed.phase == "Kickoff"
    assert "vissue_root" in failed.pause_message
    assert failed.children[0].agent_id == "01aaa-kick"
    assert failed.children[0].success is True

    packed = load_session_jobs(sd, [])
    assert {w.name for w in packed.workflows} == {"sprint-9", "sprint-8"}


def test_load_session_workflows_skips_directory_without_state(tmp_path: Path) -> None:
    """A wf_* folder with no state.json is not an invented run."""
    sd = tmp_path / "sess-empty"
    empty = sd / "workflows" / "wf_ghost"
    empty.mkdir(parents=True)
    assert load_session_workflows(sd) == []
    assert WorkflowRun.from_directory(empty) is None


def test_workflow_pairs_script_path_stem_to_latest_named_run(tmp_path: Path) -> None:
    sd = tmp_path / "sess-pair"
    sd.mkdir()
    _write_run(sd, "wf_a", name="sprint", status="complete", phase="Executive")
    _write_run(sd, "wf_b", name="sprint-11", status="complete", phase="Retrospective")
    runs = load_session_workflows(sd)
    ev = make_trace_event(
        index=1,
        tool_name="workflow",
        raw_input={"script_path": "/repo/.grok/workflows/sprint.rhai", "validate_only": False},
    )
    hit = workflow_for_event(ev, runs)
    assert hit is not None
    assert hit.name == "sprint-11"
    assert workflow_name_from_raw(ev.raw_input) == "sprint"
    assert workflow_list_preview(ev.raw_input) == "sprint"
    script_ev = make_trace_event(
        index=2,
        tool_name="workflow",
        raw_input={"script": 'let meta = #{ name: "between", description: "idle" };'},
    )
    assert workflow_name_from_raw(script_ev.raw_input) == "between"


def test_workflow_event_index_prefers_run_id_over_earlier_name() -> None:
    """A later bookend with run_id wins over an earlier script-path stem."""
    run = WorkflowRun(
        run_id="wf_b",
        name="sprint-11",
        status="complete",
        phase="Retrospective",
        objective="",
        agents_used=None,
        agent_budget=None,
        elapsed_ms=None,
        pause_message="",
        children=[],
    )
    earlier = make_trace_event(
        index=1,
        tool_name="workflow",
        raw_input={"script_path": "/repo/.grok/workflows/sprint.rhai"},
    )
    later = make_trace_event(
        index=5,
        tool_name="workflow",
        raw_input={"run_id": "wf_b", "name": "sprint-11"},
    )
    assert workflow_event_index(run, [earlier, later]) == 5


def test_workflow_inspect_uses_result_run_id_not_later_sprint(tmp_path: Path) -> None:
    """Completed workflow rawOutput.run_id selects that run, not the latest name."""
    from groket.parser import parse_timeline
    from groket.ui.render_detail import render_event_detail
    from groket.ui.selectable_static import plain_from_renderable

    sd = tmp_path / "sess-wf-result-id"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-wf-result-id"}, "generated_title": "wf"}),
        encoding="utf-8",
    )
    _write_run(
        sd,
        "wf_01a01008f78f765381c4b06d16d3c1a7",
        name="sprint-8",
        status="failed",
        phase="Kickoff",
        objective="Engineering sprint",
        agents_used=1,
        pause_message="Variable not found: vissue_root (line 155, position 28)",
        agents=[{"agent_id": "ag-1", "label": "aik", "state": "done"}],
    )
    _write_run(
        sd,
        "wf_01a012da57a473c284b1bfa6afbe3986",
        name="sprint-11",
        status="complete",
        phase="Retrospective",
        objective="later sprint",
        agents_used=45,
    )
    (sd / "updates.jsonl").write_text(
        "".join(
            json.dumps(row) + "\n"
            for row in (
                {
                    "timestamp": 10,
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call",
                            "toolCallId": "call-wf-8",
                            "title": "workflow",
                            "rawInput": {
                                "script_path": "/repo/.grok/workflows/sprint.rhai",
                                "args": {"sprint_goal": "x"},
                            },
                        }
                    },
                },
                {
                    "timestamp": 11,
                    "params": {
                        "update": {
                            "sessionUpdate": "tool_call_update",
                            "toolCallId": "call-wf-8",
                            "status": "completed",
                            "title": "Workflow: sprint-8",
                            "rawOutput": {
                                "type": "Workflow",
                                "run_id": "wf_01a01008f78f765381c4b06d16d3c1a7",
                                "name": "sprint-8",
                                "script_path": "/repo/.grok/workflows/sprint.rhai",
                                "message": "failed",
                            },
                        }
                    },
                },
            )
        ),
        encoding="utf-8",
    )
    events = parse_timeline(sd)
    runs = load_session_workflows(sd)
    start = next(e for e in events if e.event_type == "tool_call" and e.tool_name == "workflow")
    assert start.raw_input.as_str("run_id") == "wf_01a01008f78f765381c4b06d16d3c1a7"
    hit = workflow_for_event(start, runs)
    assert hit is not None
    assert hit.name == "sprint-8"
    assert hit.status == "failed"
    plain = plain_from_renderable(render_event_detail(start, workflow=hit), full=True)
    assert "sprint-8" in plain
    assert "Kickoff" in plain
    assert "vissue_root" in plain
    assert "Retrospective" not in plain
    assert "sprint-11" not in plain


def test_merge_output_ids_copies_run_id_onto_empty_bag() -> None:
    bag = ToolInputBag()
    out = WorkflowRun.merge_output_ids(
        bag,
        {"type": "Workflow", "run_id": "wf_x", "name": "sprint"},
        tool_name="workflow",
    )
    assert out.as_str("run_id") == "wf_x"
    assert out.as_str("name") == "sprint"


def test_merge_output_ids_skips_non_workflow_tool() -> None:
    bag = ToolInputBag()
    out = WorkflowRun.merge_output_ids(
        bag,
        {"type": "Shell", "run_id": "wf_stolen"},
        tool_name="run_terminal_command",
    )
    assert out.as_str("run_id") == ""
    assert bag is out


def test_merge_output_ids_keeps_existing_ids() -> None:
    bag = ToolInputBag({"run_id": "wf_keep", "name": "old"})
    out = WorkflowRun.merge_output_ids(
        bag,
        {"type": "Workflow", "run_id": "wf_new", "name": "new"},
        tool_name="workflow",
    )
    assert out.as_str("run_id") == "wf_keep"
    assert out.as_str("name") == "old"


def test_merge_output_ids_accepts_type_without_tool_name() -> None:
    bag = ToolInputBag()
    out = WorkflowRun.merge_output_ids(
        bag,
        {"type": "Workflow", "run_id": "wf_typed"},
    )
    assert out.as_str("run_id") == "wf_typed"


def test_merge_output_ids_ignores_non_mapping_output() -> None:
    bag = ToolInputBag()
    assert WorkflowRun.merge_output_ids(bag, "not-a-map") is bag
    assert WorkflowRun.merge_output_ids(bag, None) is bag


def test_workflow_status_word_and_child_session_path(tmp_path: Path) -> None:
    from groket.session.workflows import WorkflowChild, workflow_status_word
    from groket.ui.render_detail import render_workflow_detail
    from groket.ui.selectable_static import plain_from_renderable

    assert workflow_status_word("failed") == "failed"
    assert workflow_status_word("completed") == "complete"
    assert workflow_status_word("interrupted") == "cancelled"
    interrupted = WorkflowRun(
        run_id="wf_int",
        name="between",
        status="interrupted",
        phase="",
        objective="scan",
        agents_used=None,
        agent_budget=None,
        elapsed_ms=None,
        pause_message="",
        children=[],
    )
    int_plain = plain_from_renderable(render_workflow_detail(interrupted), full=True)
    assert "cancelled" in int_plain
    assert "interrupted" not in int_plain
    run = WorkflowRun(
        run_id="wf_x",
        name="sprint",
        status="failed",
        phase="Kickoff",
        objective="do it",
        agents_used=1,
        agent_budget=8,
        elapsed_ms=10,
        pause_message="boom",
        children=[WorkflowChild("ag-1", "aik", True)],
    )
    assert run.status_word() == "failed"
    assert run.children[0].status_word() == "complete"
    parent = tmp_path / "sess"
    parent.mkdir()
    assert run.children[0].session_path(parent) is None
    sibling = tmp_path / "ag-1"
    sibling.mkdir()
    (sibling / "summary.json").write_text("{}", encoding="utf-8")
    assert run.children[0].session_path(parent) == sibling
    plain = plain_from_renderable(render_workflow_detail(run), full=True)
    assert "Asked" in plain
    assert "Happened" in plain
    assert "failed" in plain
    assert "ok  " not in plain
    assert "fail  " not in plain


def test_overview_rows_include_child_session_path(tmp_path: Path) -> None:
    from groket.session.jobs import SessionJobs

    sd = tmp_path / "sess"
    sd.mkdir()
    _write_run(
        sd,
        "wf_sprint",
        name="sprint",
        status="complete",
        agents=[{"agent_id": "ag-1", "label": "aik", "state": "done"}],
    )
    child = tmp_path / "ag-1"
    child.mkdir()
    (child / "summary.json").write_text("{}", encoding="utf-8")
    _jobs, _schedules, workflows = SessionJobs.overview_rows(sd, [], "wf-kids")
    assert workflows
    kids = workflows[0]["children"]
    assert isinstance(kids, list)
    assert kids[0]["id"] == "ag-1"
    assert kids[0]["path"] == str(child)
