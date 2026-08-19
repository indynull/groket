"""Merge session background jobs and durable schedules from disk + timeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..models import JsonObject, JsonValue, ToolInputBag, TraceEvent, as_json_object, json_as_str
from ..parser import parse_timeline
from ..tool_display import task_fields_from_content
from .workflows import WorkflowRun, load_session_workflows, workflows_from_overview

_MONITOR_LINE = {"DONE": "done", "FAILED": "failed", "CANCELLED": "cancelled"}


@dataclass
class BackgroundJob:
    """One background shell or monitor row."""

    job_id: str
    kind: str
    status: str
    description: str
    command: str
    cwd: str
    started_at: int | None
    ended_at: int | None
    output_path: str
    reported: bool
    tool_call_id: str = ""


@dataclass
class ScheduleTask:
    """One durable / recurring scheduler row."""

    task_id: str
    interval_secs: int | None
    human_schedule: str
    next_fire_at: str
    last_fired_at: str
    last_subagent_id: str
    prompt_preview: str
    durable: bool
    recurring: bool
    created_at: str = ""


@dataclass
class SessionJobs:
    """Jobs, schedules, and workflow runs for one session directory."""

    jobs: list[BackgroundJob]
    schedules: list[ScheduleTask]
    workflows: list[WorkflowRun] = field(default_factory=list)


def session_jobs_for_view(
    overview: JsonObject | None,
    session_dir: Path,
    events: list[TraceEvent] | None,
) -> SessionJobs:
    """Owner payload when attached; disk merge when inspecting offline."""
    if overview is not None:
        return jobs_from_overview(overview)
    return load_session_jobs(session_dir, events)


def job_input_stamp(
    session_dir: Path,
) -> tuple[tuple[tuple[str, int, int], ...], tuple[tuple[str, str], ...]]:
    """Cache identity for scheduler files and monitor last-line status.

    ``resources_state.json`` / ``background_tasks_manifest.json`` use
    mtime+size. ``terminal/monitor-call-*.log`` uses only the last-line
    status class so growth while a monitor is still running does not
    rebuild overview. ``signals.json`` and ``call-*.log`` are omitted.
    """
    sd = Path(session_dir)
    files: list[tuple[str, int, int]] = []
    for name in ("resources_state.json", "background_tasks_manifest.json"):
        path = sd / name
        try:
            st = path.stat()
        except OSError:
            continue
        files.append((name, int(st.st_mtime_ns), int(st.st_size)))
    monitors: list[tuple[str, str]] = []
    term = sd / "terminal"
    if term.is_dir():
        try:
            paths = sorted(term.glob("monitor-call-*.log"))
        except OSError:
            paths = []
        for path in paths:
            monitors.append((path.name, _last_line_status_class(path)))
    wf_root = sd / "workflows"
    if wf_root.is_dir():
        try:
            states = sorted(wf_root.glob("wf_*/state.json"))
        except OSError:
            states = []
        for path in states:
            try:
                st = path.stat()
            except OSError:
                continue
            rel = str(path.relative_to(sd))
            files.append((rel, int(st.st_mtime_ns), int(st.st_size)))
    return (tuple(files), tuple(monitors))


def load_session_jobs(
    session_dir: Path,
    events: list[TraceEvent] | None = None,
) -> SessionJobs:
    """Merge timeline bookends, resources_state, manifest, and terminal logs.

    :param session_dir: Session directory.
    :param events: Optional pre-parsed timeline (avoids a second read).
    :returns: Stable job and schedule lists (id order).
    """
    sd = Path(session_dir)
    evs = events if events is not None else parse_timeline(sd)
    resources = _read_json_object(sd / "resources_state.json")
    state = _resources_state(resources)
    reported = _reported_ids(state)
    jobs: dict[str, BackgroundJob] = {}
    for ev in evs:
        if ev.event_type not in {"task_backgrounded", "task_completed"}:
            continue
        job = _job_from_event(ev, sd)
        if not job.job_id:
            continue
        prev = jobs.get(job.job_id)
        jobs[job.job_id] = _merge_job(prev, job) if prev else job
    for row in _read_json_list(sd / "background_tasks_manifest.json"):
        job = _job_from_mapping(row, sd)
        if not job.job_id:
            continue
        prev = jobs.get(job.job_id)
        jobs[job.job_id] = _merge_job(prev, job) if prev else job
    for job_id, job in list(jobs.items()):
        log_status = _status_from_log(job.output_path)
        status = log_status or job.status
        jobs[job_id] = replace(job, status=status, reported=job_id in reported)
    schedules = _merge_schedules(evs, state)
    return SessionJobs(
        jobs=sorted(jobs.values(), key=lambda j: (j.started_at or 0, j.job_id)),
        schedules=sorted(schedules, key=lambda s: (s.created_at, s.task_id)),
        workflows=load_session_workflows(sd),
    )


def job_event_index(job: BackgroundJob, events: list[TraceEvent]) -> int | None:
    """First Timeline bookend for *job*, or None."""
    wanted = (job.job_id or "").strip()
    call = (job.tool_call_id or "").strip()
    for ev in events:
        ev_id = event_task_id(ev)
        ev_call = ev.tool_call_id or ""
        if (wanted and ev_id == wanted) or (call and ev_call == call):
            return int(ev.index)
    return None


def job_mapping(job: BackgroundJob, *, events: list[TraceEvent] | None = None) -> JsonObject:
    """CamelCase overview row for one background or monitor job."""
    ev_i = job_event_index(job, events) if events else None
    return {
        "id": job.job_id,
        "kind": job.kind,
        "status": job.status,
        "description": job.description,
        "command": job.command,
        "cwd": job.cwd,
        "startedAt": job.started_at,
        "endedAt": job.ended_at,
        "outputPath": job.output_path,
        "reported": job.reported,
        "toolCallId": job.tool_call_id,
        "eventIndex": ev_i,
    }


def schedule_mapping(task: ScheduleTask) -> JsonObject:
    """CamelCase overview row for one scheduler task."""
    return {
        "id": task.task_id,
        "intervalSecs": task.interval_secs,
        "humanSchedule": task.human_schedule,
        "nextFireAt": task.next_fire_at,
        "lastFiredAt": task.last_fired_at,
        "lastSubagentId": task.last_subagent_id,
        "promptPreview": task.prompt_preview,
        "durable": task.durable,
        "recurring": task.recurring,
        "createdAt": task.created_at,
    }


def schedule_for_event(event: TraceEvent, schedules: list[ScheduleTask]) -> ScheduleTask | None:
    """Match a schedule bookend to the merged ``ScheduleTask`` (last fire lives there)."""
    if not event.event_type.startswith("scheduled_task_"):
        return None
    tid = event_task_id(event)
    if not tid:
        return None
    for row in schedules:
        if row.task_id == tid:
            return row
    return None


def event_task_id(event: TraceEvent) -> str:
    """Stable job id from a start/finish bookend."""
    raw_in = event.raw_input
    bag = raw_in.raw() if isinstance(raw_in, ToolInputBag) else {}
    mapping = as_json_object(bag) if isinstance(bag, dict) else {}
    tid = json_as_str(mapping.get("task_id") or mapping.get("id")).strip()
    if not tid:
        tid = task_fields_from_content(event.content or "").get("task_id", "").strip()
    return tid or (event.tool_call_id or "").strip()


def job_status_for_event(event: TraceEvent, *, mate: TraceEvent | None = None) -> str:
    """Merged status for one bookend: log last-line class, else finish fields."""
    finish = event if event.event_type == "task_completed" else mate
    raw_in = event.raw_input
    bag = raw_in.raw() if isinstance(raw_in, ToolInputBag) else {}
    mapping = as_json_object(bag) if isinstance(bag, dict) else {}
    dump = task_fields_from_content(event.content or "")
    path = json_as_str(mapping.get("output_file")) or dump.get("output_file", "")
    if finish is not None and finish is not event:
        fbag = finish.raw_input.raw() if isinstance(finish.raw_input, ToolInputBag) else {}
        fmap = as_json_object(fbag) if isinstance(fbag, dict) else {}
        path = path or json_as_str(fmap.get("output_file"))
        mapping = {**mapping, **fmap}
    log_st = _status_from_log(path)
    if (
        event.event_type == "task_completed"
        or (finish is not None and finish.event_type == "task_completed")
        or mapping.get("completed") is True
    ):
        return log_st or _completed_status(mapping)
    return log_st or "running"


def job_duration_seconds(event: TraceEvent, *, mate: TraceEvent | None = None) -> float | None:
    """Run length from start/finish timestamps (not the next-event gap)."""
    start = event if event.event_type == "task_backgrounded" else mate
    finish = event if event.event_type == "task_completed" else mate
    started = start.timestamp if start is not None else None
    ended = finish.timestamp if finish is not None else None
    if started is None and start is not None and isinstance(start.raw_input, ToolInputBag):
        raw = start.raw_input.raw().get("start_time")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            started = int(raw)
    if ended is None and finish is not None and isinstance(finish.raw_input, ToolInputBag):
        raw = finish.raw_input.raw().get("end_time")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            ended = int(raw)
    if started is None or ended is None or ended < started:
        return None
    return float(ended - started)


def event_job_kind(event: TraceEvent) -> str:
    """``monitor`` or ``background`` from a task bookend (empty otherwise)."""
    if event.event_type not in {"task_backgrounded", "task_completed"}:
        if event.event_type.startswith("scheduled_task_"):
            return "schedule"
        return ""
    raw_in = event.raw_input
    bag = raw_in.raw() if isinstance(raw_in, ToolInputBag) else {}
    mapping = as_json_object(bag) if isinstance(bag, dict) else {}
    output = json_as_str(mapping.get("output_file"))
    desc = json_as_str(mapping.get("description") or mapping.get("monitor_description"))
    return _job_kind(mapping, output, desc)


def jobs_from_overview(overview: JsonObject) -> SessionJobs:
    """Hydrate domain rows from a ``session/overview`` payload."""
    jobs: list[BackgroundJob] = []
    raw_jobs = overview.get("backgroundJobs")
    if isinstance(raw_jobs, list):
        for item in raw_jobs:
            if not isinstance(item, dict):
                continue
            row = as_json_object(item)
            jobs.append(
                BackgroundJob(
                    job_id=json_as_str(row.get("id")),
                    kind=json_as_str(row.get("kind")) or "background",
                    status=json_as_str(row.get("status")) or "running",
                    description=json_as_str(row.get("description")),
                    command=json_as_str(row.get("command")),
                    cwd=json_as_str(row.get("cwd")),
                    started_at=_as_int(row.get("startedAt")),
                    ended_at=_as_int(row.get("endedAt")),
                    output_path=json_as_str(row.get("outputPath")),
                    reported=row.get("reported") is True,
                    tool_call_id=json_as_str(row.get("toolCallId")),
                )
            )
    schedules: list[ScheduleTask] = []
    raw_sch = overview.get("schedules")
    if isinstance(raw_sch, list):
        for item in raw_sch:
            if not isinstance(item, dict):
                continue
            row = as_json_object(item)
            schedules.append(
                ScheduleTask(
                    task_id=json_as_str(row.get("id")),
                    interval_secs=_as_int(row.get("intervalSecs")),
                    human_schedule=json_as_str(row.get("humanSchedule")),
                    next_fire_at=json_as_str(row.get("nextFireAt")),
                    last_fired_at=json_as_str(row.get("lastFiredAt")),
                    last_subagent_id=json_as_str(row.get("lastSubagentId")),
                    prompt_preview=json_as_str(row.get("promptPreview")),
                    durable=row.get("durable") is True,
                    recurring=row.get("recurring") is True,
                    created_at=json_as_str(row.get("createdAt")),
                )
            )
    return SessionJobs(
        jobs=jobs,
        schedules=schedules,
        workflows=workflows_from_overview(overview),
    )


def read_log_tail(path: Path, *, max_chars: int = 8_000) -> str:
    """Last *max_chars* of a terminal or monitor log (empty when missing)."""
    if max_chars <= 0 or not path.is_file():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    chunk = data[-max_chars:]
    return chunk.decode("utf-8", errors="replace")


def monitor_line_status(text: str) -> str | None:
    """Map the last DONE/FAILED/CANCELLED token on its own line, if any."""
    for line in reversed(text.splitlines()):
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if token in _MONITOR_LINE:
            return _MONITOR_LINE[token]
    return None


def _last_line_status_class(path: Path) -> str:
    """``done`` / ``failed`` / ``cancelled`` / ``running`` from the log tail."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - 512))
            chunk = handle.read()
    except OSError:
        return "running"
    text = chunk.decode("utf-8", errors="replace")
    return monitor_line_status(text) or "running"


def _status_from_log(output_path: str) -> str | None:
    if not output_path:
        return None
    return monitor_line_status(read_log_tail(Path(output_path), max_chars=4_000))


def _job_from_event(event: TraceEvent, session_dir: Path) -> BackgroundJob:
    raw_in = event.raw_input
    bag = raw_in.raw() if isinstance(raw_in, ToolInputBag) else {}
    mapping = as_json_object(bag) if isinstance(bag, dict) else {}
    job = _job_from_mapping(mapping, session_dir)
    started = job.started_at
    ended = job.ended_at
    if event.event_type == "task_backgrounded" and started is None:
        started = event.timestamp
    if event.event_type == "task_completed":
        if ended is None:
            ended = event.timestamp
        if job.status == "running":
            job = replace(job, status=_completed_status(mapping))
    if not job.tool_call_id:
        job = replace(job, tool_call_id=event.tool_call_id)
    return replace(job, started_at=started, ended_at=ended)


def _job_from_mapping(row: JsonObject, session_dir: Path) -> BackgroundJob:
    job_id = json_as_str(row.get("task_id") or row.get("id")).strip()
    tool_call_id = json_as_str(row.get("tool_call_id")).strip()
    command = json_as_str(row.get("command") or row.get("display_command"))
    cwd = json_as_str(row.get("cwd"))
    desc = json_as_str(row.get("description") or row.get("monitor_description")).strip()
    output = json_as_str(row.get("output_file")).strip()
    if output and not Path(output).is_absolute():
        output = str(session_dir / output)
    kind = _job_kind(row, output, desc)
    status = "running"
    if row.get("completed") is True:
        status = _completed_status(row)
    return BackgroundJob(
        job_id=job_id or tool_call_id,
        kind=kind,
        status=status,
        description=desc,
        command=command,
        cwd=cwd,
        started_at=_epoch(row.get("start_time")),
        ended_at=_epoch(row.get("end_time")),
        output_path=output,
        reported=False,
        tool_call_id=tool_call_id,
    )


def _completed_status(row: JsonObject) -> str:
    signal = json_as_str(row.get("signal")).casefold()
    if row.get("explicitly_killed") is True or signal in {"killed", "sigkill", "sigterm"}:
        return "cancelled"
    code = row.get("exit_code")
    if isinstance(code, int) and code != 0:
        return "failed"
    return "done"


def _job_kind(row: JsonObject, output_path: str, description: str) -> str:
    raw = json_as_str(row.get("kind")).casefold()
    if raw == "monitor" or "monitor-call" in output_path.replace("\\", "/"):
        return "monitor"
    if json_as_str(row.get("monitor_description")).strip():
        return "monitor"
    if description.casefold().startswith("live ") and "watch" in description.casefold():
        return "monitor"
    return "background"


def _merge_job(base: BackgroundJob, extra: BackgroundJob) -> BackgroundJob:
    return BackgroundJob(
        job_id=extra.job_id or base.job_id,
        kind=extra.kind if extra.kind == "monitor" else base.kind,
        status=_prefer_status(base.status, extra.status),
        description=extra.description or base.description,
        command=extra.command or base.command,
        cwd=extra.cwd or base.cwd,
        started_at=base.started_at if base.started_at is not None else extra.started_at,
        ended_at=extra.ended_at if extra.ended_at is not None else base.ended_at,
        output_path=extra.output_path or base.output_path,
        reported=base.reported or extra.reported,
        tool_call_id=extra.tool_call_id or base.tool_call_id,
    )


def _prefer_status(old: str, new: str) -> str:
    rank = {"running": 0, "done": 1, "failed": 2, "cancelled": 2}
    return new if rank.get(new, 0) >= rank.get(old, 0) else old


def _merge_schedules(events: list[TraceEvent], state: JsonObject) -> list[ScheduleTask]:
    by_id: dict[str, ScheduleTask] = {}
    for ev in events:
        if not ev.event_type.startswith("scheduled_task_"):
            continue
        raw_in = ev.raw_input
        bag = raw_in.raw() if isinstance(raw_in, ToolInputBag) else {}
        mapping = as_json_object(bag) if isinstance(bag, dict) else {}
        task_id = json_as_str(mapping.get("task_id")).strip()
        if not task_id:
            continue
        prompt = json_as_str(mapping.get("prompt")).replace("\n", " ").strip()
        by_id[task_id] = ScheduleTask(
            task_id=task_id,
            interval_secs=None,
            human_schedule=json_as_str(mapping.get("human_schedule")).strip(),
            next_fire_at=json_as_str(mapping.get("next_fire_at")).strip(),
            last_fired_at="",
            last_subagent_id="",
            prompt_preview=prompt[:200],
            durable=False,
            recurring=False,
        )
    scheduler = state.get("grok_build.Scheduler")
    tasks_raw = scheduler.get("tasks") if isinstance(scheduler, dict) else None
    if isinstance(tasks_raw, list):
        for item in tasks_raw:
            if not isinstance(item, dict):
                continue
            row = as_json_object(item)
            task_id = json_as_str(row.get("id")).strip()
            if not task_id:
                continue
            prompt = json_as_str(row.get("prompt")).replace("\n", " ").strip()
            prev = by_id.get(task_id)
            by_id[task_id] = ScheduleTask(
                task_id=task_id,
                interval_secs=_as_int(row.get("intervalSecs")),
                human_schedule=(prev.human_schedule if prev else "")
                or _human_interval(_as_int(row.get("intervalSecs"))),
                next_fire_at=prev.next_fire_at if prev else "",
                last_fired_at=json_as_str(row.get("lastFiredAt")).strip(),
                last_subagent_id=json_as_str(row.get("lastSubagentId")).strip(),
                prompt_preview=(prev.prompt_preview if prev else "") or prompt[:200],
                durable=row.get("durable") is True,
                recurring=row.get("recurring") is True,
                created_at=json_as_str(row.get("createdAt")).strip(),
            )
    return list(by_id.values())


def _human_interval(secs: int | None) -> str:
    if secs is None or secs <= 0:
        return ""
    if secs % 3600 == 0:
        hours = secs // 3600
        return f"every {hours} hour" if hours == 1 else f"every {hours} hours"
    if secs % 60 == 0:
        mins = secs // 60
        return f"every {mins} minute" if mins == 1 else f"every {mins} minutes"
    return f"every {secs} seconds"


def _resources_state(raw: JsonObject) -> JsonObject:
    state = raw.get("state")
    return as_json_object(state) if isinstance(state, dict) else {}


def _reported_ids(state: JsonObject) -> set[str]:
    block = state.get("grok_build.ReportedTaskCompletions")
    if not isinstance(block, dict):
        return set()
    rows = block.get("reported")
    if not isinstance(rows, list):
        return set()
    return {json_as_str(item).strip() for item in rows if json_as_str(item).strip()}


def _read_json_object(path: Path) -> JsonObject:
    raw = _read_json(path)
    return as_json_object(raw) if isinstance(raw, dict) else {}


def _read_json_list(path: Path) -> list[JsonObject]:
    raw = _read_json(path)
    if not isinstance(raw, list):
        return []
    return [as_json_object(item) for item in raw if isinstance(item, dict)]


def _read_json(path: Path) -> JsonValue:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _epoch(val: JsonValue) -> int | None:
    if isinstance(val, dict):
        secs = val.get("secs_since_epoch")
        if isinstance(secs, bool):
            return None
        if isinstance(secs, (int, float)):
            return int(secs)
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return int(val)
    return None


def _as_int(val: JsonValue) -> int | None:
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return int(val)
    return None
