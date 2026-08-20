"""Merge session background jobs and durable schedules from disk + timeline."""

from __future__ import annotations

import json
from pathlib import Path

from groket.parser import parse_timeline
from groket.session.jobs import job_mapping, load_session_jobs, schedule_mapping


def _write_updates(sd: Path, updates: list[dict[str, object]]) -> None:
    lines = [
        json.dumps({"timestamp": 1_700_000_000 + i, "params": {"update": upd}})
        for i, upd in enumerate(updates)
    ]
    (sd / "updates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _session(root: Path) -> Path:
    sd = root / "sess-jobs"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps({"info": {"id": "sess-jobs"}, "generated_title": "jobs"}),
        encoding="utf-8",
    )
    return sd


def test_load_session_jobs_merges_shell_monitor_and_schedule(tmp_path: Path) -> None:
    sd = _session(tmp_path)
    term = sd / "terminal"
    term.mkdir()
    mon_log = term / "monitor-call-watch.log"
    mon_log.write_text("120\nDONE\n", encoding="utf-8")
    bg_log = term / "call-shell.log"
    bg_log.write_text("hello from bg\n", encoding="utf-8")
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "task_backgrounded",
                "task_id": "job-bg-1",
                "tool_call_id": "call-bg",
                "command": "sleep 30",
                "cwd": "/tmp/work",
                "output_file": str(bg_log),
                "description": "long sleep",
            },
            {
                "sessionUpdate": "task_completed",
                "will_wake": False,
                "task_snapshot": {
                    "task_id": "job-bg-1",
                    "command": "sleep 30",
                    "cwd": "/tmp/work",
                    "output_file": str(bg_log),
                    "description": "long sleep",
                    "output": "hello from bg\n",
                    "kind": "bash",
                    "completed": True,
                    "start_time": {"secs_since_epoch": 1_700_000_000},
                    "end_time": {"secs_since_epoch": 1_700_000_010},
                },
            },
            {
                "sessionUpdate": "task_backgrounded",
                "task_id": "job-mon-1",
                "tool_call_id": "call-mon",
                "command": "while :; do sleep 5; done",
                "cwd": "/tmp/work",
                "output_file": str(mon_log),
                "description": "Watch events",
                "monitor_description": "Watch events",
            },
            {
                "sessionUpdate": "scheduled_task_created",
                "task_id": "sched-1",
                "prompt": "Watch the groket board every hour.",
                "human_schedule": "every 1 hour",
                "next_fire_at": "2026-08-18T23:05:45Z",
            },
        ],
    )
    (sd / "resources_state.json").write_text(
        json.dumps(
            {
                "state": {
                    "grok_build.Scheduler": {
                        "tasks": [
                            {
                                "id": "sched-1",
                                "intervalSecs": 3600,
                                "prompt": "Watch the groket board every hour.",
                                "recurring": True,
                                "durable": True,
                                "lastFiredAt": "2026-08-18T22:05:45Z",
                                "lastSubagentId": "sub-1",
                            }
                        ]
                    },
                    "grok_build.ReportedTaskCompletions": {"reported": ["job-bg-1"]},
                }
            }
        ),
        encoding="utf-8",
    )
    (sd / "background_tasks_manifest.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "job-mon-1",
                    "kind": "monitor",
                    "command": "while :; do sleep 5; done",
                    "cwd": "/tmp/work",
                    "output_file": str(mon_log),
                    "description": "Watch events",
                }
            ]
        ),
        encoding="utf-8",
    )

    events = parse_timeline(sd)
    assert any(e.event_type == "scheduled_task_created" for e in events)
    assert all(e.event_type != "subagent_spawned" for e in events)

    packed = load_session_jobs(sd, events)
    kinds = {j.job_id: j for j in packed.jobs}
    assert set(kinds) == {"job-bg-1", "job-mon-1"}

    bg = kinds["job-bg-1"]
    assert bg.kind == "background"
    assert bg.status == "done"
    assert bg.command == "sleep 30"
    assert bg.cwd == "/tmp/work"
    assert bg.output_path == str(bg_log)
    assert bg.reported is True
    assert bg.started_at == 1_700_000_000
    assert bg.ended_at == 1_700_000_010

    mon = kinds["job-mon-1"]
    assert mon.kind == "monitor"
    assert mon.status == "done"
    assert mon.output_path == str(mon_log)
    assert "Watch events" in mon.description

    assert len(packed.schedules) == 1
    sch = packed.schedules[0]
    assert sch.task_id == "sched-1"
    assert sch.interval_secs == 3600
    assert sch.human_schedule == "every 1 hour"
    assert sch.next_fire_at.startswith("2026-08-18T23:05:45")
    assert sch.last_fired_at.startswith("2026-08-18T22:05:45")
    assert sch.last_subagent_id == "sub-1"
    assert "Watch the groket board" in sch.prompt_preview
    assert sch.durable is True
    assert sch.recurring is True


def test_load_session_jobs_running_until_completed_or_monitor_line(tmp_path: Path) -> None:
    sd = _session(tmp_path)
    mon_log = sd / "terminal"
    mon_log.mkdir()
    (mon_log / "monitor-call-live.log").write_text("still going\n", encoding="utf-8")
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "task_backgrounded",
                "task_id": "job-run",
                "command": "sleep 99",
                "cwd": "/tmp",
                "output_file": str(mon_log / "call-run.log"),
                "description": "not done yet",
            },
            {
                "sessionUpdate": "task_backgrounded",
                "task_id": "job-live-mon",
                "command": "watch",
                "cwd": "/tmp",
                "output_file": str(mon_log / "monitor-call-live.log"),
                "description": "live watch",
            },
        ],
    )
    packed = load_session_jobs(sd)
    by_id = {j.job_id: j for j in packed.jobs}
    assert by_id["job-run"].status == "running"
    assert by_id["job-run"].kind == "background"
    assert by_id["job-live-mon"].status == "running"
    assert by_id["job-live-mon"].kind == "monitor"


def test_load_session_jobs_failed_from_monitor_last_line(tmp_path: Path) -> None:
    sd = _session(tmp_path)
    term = sd / "terminal"
    term.mkdir()
    path = term / "monitor-call-fail.log"
    path.write_text("oops\nFAILED\n", encoding="utf-8")
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "task_backgrounded",
                "task_id": "job-fail",
                "command": "watch",
                "cwd": "/tmp",
                "output_file": str(path),
                "description": "will fail",
            }
        ],
    )
    packed = load_session_jobs(sd)
    assert packed.jobs[0].status == "failed"
    assert packed.jobs[0].kind == "monitor"


def test_job_and_schedule_mappings_use_camel_case(tmp_path: Path) -> None:
    sd = _session(tmp_path)
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "task_backgrounded",
                "task_id": "job-map",
                "command": "echo hi",
                "cwd": "/tmp",
                "output_file": "/tmp/out.log",
                "description": "echo",
            },
            {
                "sessionUpdate": "scheduled_task_created",
                "task_id": "sched-map",
                "prompt": "hourly ping",
                "human_schedule": "every 1 hour",
                "next_fire_at": "2026-08-18T23:00:00Z",
            },
        ],
    )
    packed = load_session_jobs(sd)
    job = job_mapping(packed.jobs[0])
    assert job["id"] == "job-map"
    assert job["outputPath"] == "/tmp/out.log"
    assert job["startedAt"] is not None
    assert "task_id" not in job
    sch = schedule_mapping(packed.schedules[0])
    assert sch["id"] == "sched-map"
    assert sch["humanSchedule"] == "every 1 hour"
    assert sch["nextFireAt"].startswith("2026-08-18T23:00:00")
    assert sch["promptPreview"] == "hourly ping"


def test_job_status_for_event_uses_log_then_finish(tmp_path: Path) -> None:
    from groket.models import ToolInputBag, TraceEvent
    from groket.session.jobs import job_status_for_event

    log = tmp_path / "monitor-call-live.log"
    log.write_text("still\nDONE\n", encoding="utf-8")
    running = TraceEvent(
        index=0,
        event_type="task_backgrounded",
        raw_input=ToolInputBag({"task_id": "j1", "output_file": str(tmp_path / "missing.log")}),
    )
    assert job_status_for_event(running) == "running"
    done = TraceEvent(
        index=1,
        event_type="task_backgrounded",
        raw_input=ToolInputBag({"task_id": "j1", "output_file": str(log)}),
    )
    assert job_status_for_event(done) == "done"
    failed = TraceEvent(
        index=2,
        event_type="task_completed",
        raw_input=ToolInputBag({"task_id": "j1", "completed": True, "exit_code": 2}),
    )
    assert job_status_for_event(failed) == "failed"
    start = TraceEvent(
        index=3,
        event_type="task_backgrounded",
        raw_input=ToolInputBag({"task_id": "j2", "command": "false"}),
    )
    finish = TraceEvent(
        index=4,
        event_type="task_completed",
        raw_input=ToolInputBag({"task_id": "j2", "completed": True, "exit_code": 1}),
    )
    assert job_status_for_event(start, mate=finish) == "failed"


def test_set_bookend_indexes_matches_per_row_first_hits() -> None:
    """One walk agrees with per-row job and workflow bookend indexes."""
    from conftest import make_trace_event
    from groket.session.jobs import (
        BackgroundJob,
        job_event_index,
        job_mapping,
        set_bookend_indexes,
    )
    from groket.session.workflows import WorkflowRun, workflow_event_index, workflow_mapping

    job = BackgroundJob(
        job_id="job-a",
        kind="monitor",
        status="done",
        description="Watch",
        command="watch",
        cwd="/tmp",
        started_at=1,
        ended_at=2,
        output_path="",
        reported=False,
        tool_call_id="call-a",
    )
    run = WorkflowRun(
        run_id="wf_b",
        name="sprint-11",
        status="complete",
        phase="",
        objective="",
        agents_used=None,
        agent_budget=None,
        elapsed_ms=None,
        pause_message="",
        children=[],
    )
    events = [
        make_trace_event(index=0, tool_name="read_file", raw_input={"target_file": "/tmp/x"}),
        make_trace_event(
            index=1,
            tool_name="workflow",
            raw_input={"script_path": "/repo/.grok/workflows/sprint.rhai"},
        ),
        make_trace_event(
            index=5,
            event_type="task_backgrounded",
            tool_call_id="call-a",
            raw_input={"task_id": "job-a"},
        ),
        make_trace_event(
            index=8,
            tool_name="workflow",
            raw_input={"run_id": "wf_b", "name": "sprint-11"},
        ),
    ]
    jobs = [job_mapping(job)]
    workflows = [workflow_mapping(run)]
    set_bookend_indexes(events, jobs, workflows)
    assert jobs[0]["eventIndex"] == job_event_index(job, events)
    assert workflows[0]["eventIndex"] == workflow_event_index(run, events)
    assert jobs[0]["eventIndex"] == 5
    assert workflows[0]["eventIndex"] == 8


def test_jobs_from_overview_does_not_parse_timeline(tmp_path: Path) -> None:
    from unittest.mock import patch

    from groket.parser import parse_timeline
    from groket.session.control_views import build_session_overview
    from groket.session.jobs import jobs_from_overview, session_jobs_for_view

    sd = _session(tmp_path)
    term = sd / "terminal"
    term.mkdir()
    mon = term / "monitor-call.log"
    mon.write_text("DONE\n", encoding="utf-8")
    _write_updates(
        sd,
        [
            {
                "sessionUpdate": "task_backgrounded",
                "task_id": "job-ov",
                "command": "watch",
                "cwd": "/tmp",
                "output_file": str(mon),
                "description": "Watch board",
            },
            {
                "sessionUpdate": "scheduled_task_created",
                "task_id": "sched-ov",
                "prompt": "hourly ping",
                "human_schedule": "every 1 hour",
                "next_fire_at": "2026-08-18T23:00:00Z",
            },
        ],
    )
    ov = build_session_overview(sd)
    with patch("groket.parser.parse_timeline", side_effect=AssertionError("disk parse")):
        with patch("groket.session.jobs.parse_timeline", side_effect=AssertionError("disk parse")):
            packed = session_jobs_for_view(ov, sd, None)
    assert [j.kind for j in packed.jobs] == ["monitor"]
    assert packed.jobs[0].status == "done"
    assert packed.schedules[0].task_id == "sched-ov"
    again = jobs_from_overview(ov)
    assert again.jobs[0].job_id == packed.jobs[0].job_id
    # Offline (no overview) still uses the disk merge.
    offline = session_jobs_for_view(None, sd, parse_timeline(sd))
    assert offline.jobs[0].job_id == "job-ov"


def test_read_log_tail_reads_only_a_suffix(tmp_path: Path) -> None:
    """A fat monitor log is tailed from the end, not read in full."""
    from unittest.mock import patch

    from groket.session.jobs import read_log_tail

    path = tmp_path / "monitor-call-fat.log"
    suffix = "UNIQUE_TAIL_XYZ\nDONE\n"
    cap = 200
    payload = ("head-line\n" * 8_000) + suffix
    raw = payload.encode("utf-8")
    path.write_bytes(raw)
    read_n = 0
    real_open = Path.open

    def counting_open(self: Path, *args: object, **kwargs: object) -> object:
        handle = real_open(self, *args, **kwargs)

        def counted_read(*rargs: object, **rkwargs: object) -> bytes:
            nonlocal read_n
            chunk = orig_read(*rargs, **rkwargs)
            read_n += len(chunk)
            return chunk

        orig_read = handle.read
        handle.read = counted_read  # type: ignore[method-assign]
        return handle

    with patch.object(Path, "open", counting_open):
        text = read_log_tail(path, max_chars=cap)
    assert text.endswith(suffix)
    assert text == payload[-cap:]
    assert read_n > 0
    assert read_n <= cap * 4
    assert read_n < len(raw)


def test_log_file_prefers_session_terminal_basename(tmp_path: Path) -> None:
    from groket.session.jobs import BackgroundJob

    term = tmp_path / "terminal"
    term.mkdir()
    host = term / "call-bg.log"
    host.write_text("line 0\nDONE\n", encoding="utf-8")
    found = BackgroundJob.log_file(tmp_path, "/root/.grok/sessions/x/terminal/call-bg.log")
    assert found == host
    text = BackgroundJob.inspect_log(tmp_path, "/root/.grok/sessions/x/terminal/call-bg.log")
    assert "line 0" in text
    assert "DONE" in text
    assert BackgroundJob.log_file(tmp_path, "") is None
    assert BackgroundJob.log_file(tmp_path, "/no/such/call-missing.log") is None
