"""Failed workflow and job runs become Findings with paste-ready extras."""

from __future__ import annotations

import json
from pathlib import Path

from groket.analysis.basic import BasicAnalyzer
from groket.parser import parse_timeline
from groket.session.failures import findings_for_failed_runs


def _write_failed_session(root: Path) -> Path:
    sd = root / "sess-fail"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-fail"}, "generated_title": "fail"}),
        encoding="utf-8",
    )
    d = sd / "workflows" / "wf_sprint8"
    d.mkdir(parents=True)
    (d / "state.json").write_text(
        json.dumps(
            {
                "version": 4,
                "state": {
                    "run_id": "wf_sprint8",
                    "name": "sprint-8",
                    "status": "failed",
                    "current_phase": "Kickoff",
                    "objective": "Engineering sprint",
                    "pause_message": "Variable not found: vissue_root",
                    "agents": [
                        {"agent_id": "01aaa-aik", "label": "aik", "state": "failed"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    term = sd / "terminal"
    term.mkdir()
    mon = term / "monitor-call-fail.log"
    mon.write_text("FAILED\n", encoding="utf-8")
    updates = [
        {
            "timestamp": 1,
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "run it"},
                }
            },
        },
        {
            "timestamp": 2,
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
            "timestamp": 3,
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
                    },
                }
            },
        },
        {
            "timestamp": 4,
            "params": {
                "update": {
                    "sessionUpdate": "task_backgrounded",
                    "task_id": "job-fail",
                    "command": "bash watch.sh",
                    "cwd": "/tmp",
                    "output_file": str(mon),
                    "description": "Watch board",
                }
            },
        },
        {
            "timestamp": 5,
            "params": {
                "update": {
                    "sessionUpdate": "task_completed",
                    "task_id": "job-fail",
                    "output_file": str(mon),
                }
            },
        },
    ]
    (sd / "updates.jsonl").write_text("\n".join(json.dumps(u) for u in updates) + "\n")
    return sd


def test_failed_workflow_and_job_are_findings(tmp_path: Path) -> None:
    sd = _write_failed_session(tmp_path)
    found = findings_for_failed_runs(sd)
    titles = [f.title for f in found]
    assert any("sprint-8" in t for t in titles)
    assert any("Watch board" in t or "watch.sh" in t for t in titles)
    wf = next(f for f in found if f.category == "workflow")
    assert wf.event_indices
    extras = wf.extras
    assert extras.get("what_model_did")
    assert extras.get("where")
    assert "vissue_root" in str(extras.get("why_mistake"))
    assert extras.get("what_should_have_done")
    box = str(extras.get("issue_box") or "")
    assert box.startswith("What:")
    assert "Where:" in box
    assert "Why:" in box
    assert "Should have:" in box
    job = next(f for f in found if f.category == "job")
    assert job.event_indices
    assert job.extras.get("issue_box")


def test_failed_workflow_evidence_skips_earlier_named_sprint(tmp_path: Path) -> None:
    """Finding for wf_sprint8 does not cite an earlier script-path-only sprint."""
    sd = tmp_path / "sess-two-sprints"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-two-sprints"}, "generated_title": "two"}),
        encoding="utf-8",
    )
    d = sd / "workflows" / "wf_sprint8"
    d.mkdir(parents=True)
    (d / "state.json").write_text(
        json.dumps(
            {
                "version": 4,
                "state": {
                    "run_id": "wf_sprint8",
                    "name": "sprint-8",
                    "status": "failed",
                    "current_phase": "Kickoff",
                    "objective": "Engineering sprint",
                    "pause_message": "Variable not found: vissue_root",
                    "agents": [],
                },
            }
        ),
        encoding="utf-8",
    )
    updates = [
        {
            "timestamp": 1,
            "params": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-old",
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
                    "toolCallId": "call-wf8",
                    "title": "workflow",
                    "rawInput": {
                        "script_path": "/repo/.grok/workflows/sprint.rhai",
                    },
                }
            },
        },
        {
            "timestamp": 3,
            "params": {
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-wf8",
                    "status": "completed",
                    "title": "Workflow: sprint-8",
                    "rawOutput": {
                        "type": "Workflow",
                        "run_id": "wf_sprint8",
                        "name": "sprint-8",
                    },
                }
            },
        },
    ]
    (sd / "updates.jsonl").write_text("\n".join(json.dumps(u) for u in updates) + "\n")
    found = findings_for_failed_runs(sd)
    wf = next(f for f in found if f.category == "workflow")
    assert wf.id == "workflow:wf_sprint8"
    assert len(wf.event_indices) == 1
    evs = parse_timeline(sd)
    hit = next(e for e in evs if e.tool_call_id == "call-wf8")
    assert wf.event_indices == [hit.index]


def test_basic_analyzer_emits_failed_run_findings(tmp_path: Path) -> None:
    sd = _write_failed_session(tmp_path)
    result = BasicAnalyzer().analyze(sd)
    assert result.ok
    titles = [f.title for f in result.findings]
    assert any("sprint-8" in t for t in titles)
    assert any(f.category == "job" for f in result.findings)
    wf = next(f for f in result.findings if f.category == "workflow")
    assert wf.plugin_id == "basic"
    assert wf.event_indices
    assert "What:" in str(wf.extras.get("issue_box") or "")
