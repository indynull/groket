"""Merge session background jobs and durable schedules from disk + timeline."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import ClassVar

from ..analysis.base import Finding
from ..bounded_cache import BoundedCache
from ..constants import OVERVIEW_CACHE_MAXSIZE
from ..models import (
    JsonObject,
    JsonValue,
    Severity,
    ToolInputBag,
    TraceEvent,
    as_json_object,
    json_as_str,
)
from ..parser import parse_timeline
from ..tool_display import task_fields_from_content
from .workflows import (
    WorkflowRun,
    load_session_workflows,
    workflow_mapping,
    workflow_name_from_raw,
    workflow_run_id_from_raw,
    workflows_from_overview,
)

_MONITOR_LINE = {"DONE": "done", "FAILED": "failed", "CANCELLED": "cancelled"}
JOB_INSPECT_LOG_CHARS = 50_000

type _JobFileStamp = tuple[tuple[str, int, int], ...]
type _MonitorStamp = tuple[tuple[str, str], ...]
type _BookendStamp = tuple[tuple[str, str, str], ...]
type _JobReuseKey = tuple[tuple[_JobFileStamp, _MonitorStamp], _BookendStamp]
type _JobRowLists = tuple[list[JsonObject], list[JsonObject], list[JsonObject]]


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

    @staticmethod
    def completed_status(row: JsonObject) -> str:
        """``done`` / ``failed`` / ``cancelled`` from a finish mapping."""
        signal = json_as_str(row.get("signal")).casefold()
        if row.get("explicitly_killed") is True or signal in {"killed", "sigkill", "sigterm"}:
            return "cancelled"
        code = row.get("exit_code")
        if isinstance(code, int) and code != 0:
            return "failed"
        return "done"

    @staticmethod
    def kind_from(row: JsonObject, output_path: str, description: str) -> str:
        """``monitor`` or ``background`` from a mapping and log path."""
        raw = json_as_str(row.get("kind")).casefold()
        if raw == "monitor" or "monitor-call" in output_path.replace("\\", "/"):
            return "monitor"
        if json_as_str(row.get("monitor_description")).strip():
            return "monitor"
        if description.casefold().startswith("live ") and "watch" in description.casefold():
            return "monitor"
        return "background"

    @staticmethod
    def last_line_class(path: Path) -> str:
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

    @staticmethod
    def log_file(session_dir: Path | None, output_path: str) -> Path | None:
        """Host file for a job log: ``session/terminal/<name>`` when that file exists."""
        text = (output_path or "").strip()
        if not text:
            return None
        recorded = Path(text)
        name = recorded.name
        if session_dir is not None and name:
            local = Path(session_dir) / "terminal" / name
            try:
                if local.is_file():
                    return local
            except OSError:
                pass
        if not recorded.is_absolute() and session_dir is not None:
            recorded = Path(session_dir) / recorded
        try:
            if recorded.is_file():
                return recorded
        except OSError:
            return None
        return None

    @classmethod
    def inspect_log(
        cls,
        session_dir: Path | None,
        output_path: str,
        *,
        max_chars: int = JOB_INSPECT_LOG_CHARS,
    ) -> str:
        """Tail of the host job log for Timeline inspect (empty when missing)."""
        path = cls.log_file(session_dir, output_path)
        if path is None:
            return ""
        return read_log_tail(path, max_chars=max_chars)

    @staticmethod
    def status_from_log(output_path: str) -> str | None:
        """Last-line monitor class from *output_path*, if any."""
        if not output_path:
            return None
        return monitor_line_status(read_log_tail(Path(output_path), max_chars=4_000))

    @classmethod
    def from_mapping(cls, row: JsonObject, session_dir: Path) -> BackgroundJob:
        """One job from a manifest or timeline bag."""
        job_id = json_as_str(row.get("task_id") or row.get("id")).strip()
        tool_call_id = json_as_str(row.get("tool_call_id")).strip()
        command = json_as_str(row.get("command") or row.get("display_command"))
        cwd = json_as_str(row.get("cwd"))
        desc = json_as_str(row.get("description") or row.get("monitor_description")).strip()
        output = json_as_str(row.get("output_file")).strip()
        if output and not Path(output).is_absolute():
            output = str(session_dir / output)
        status = "running"
        if row.get("completed") is True:
            status = cls.completed_status(row)
        return cls(
            job_id=job_id or tool_call_id,
            kind=cls.kind_from(row, output, desc),
            status=status,
            description=desc,
            command=command,
            cwd=cwd,
            started_at=SessionJobs.epoch(row.get("start_time")),
            ended_at=SessionJobs.epoch(row.get("end_time")),
            output_path=output,
            reported=False,
            tool_call_id=tool_call_id,
        )

    @classmethod
    def from_event(cls, event: TraceEvent, session_dir: Path) -> BackgroundJob:
        """One job from a start/finish bookend."""
        raw_in = event.raw_input
        bag = raw_in.raw() if isinstance(raw_in, ToolInputBag) else {}
        mapping = as_json_object(bag) if isinstance(bag, dict) else {}
        job = cls.from_mapping(mapping, session_dir)
        started = job.started_at
        ended = job.ended_at
        if event.event_type == "task_backgrounded" and started is None:
            started = event.timestamp
        if event.event_type == "task_completed":
            if ended is None:
                ended = event.timestamp
            if job.status == "running":
                job = replace(job, status=cls.completed_status(mapping))
        if not job.tool_call_id:
            job = replace(job, tool_call_id=event.tool_call_id)
        return replace(job, started_at=started, ended_at=ended)

    @classmethod
    def from_overview(cls, row: JsonObject) -> BackgroundJob:
        """Hydrate one ``session/overview`` job row."""
        return cls(
            job_id=json_as_str(row.get("id")),
            kind=json_as_str(row.get("kind")) or "background",
            status=json_as_str(row.get("status")) or "running",
            description=json_as_str(row.get("description")),
            command=json_as_str(row.get("command")),
            cwd=json_as_str(row.get("cwd")),
            started_at=SessionJobs.optional_int(row.get("startedAt")),
            ended_at=SessionJobs.optional_int(row.get("endedAt")),
            output_path=json_as_str(row.get("outputPath")),
            reported=row.get("reported") is True,
            tool_call_id=json_as_str(row.get("toolCallId")),
        )

    def merge(self, extra: BackgroundJob) -> BackgroundJob:
        """Combine two records for the same id (later finish wins status)."""
        rank = {"running": 0, "done": 1, "failed": 2, "cancelled": 2}
        status = (
            extra.status if rank.get(extra.status, 0) >= rank.get(self.status, 0) else self.status
        )
        return BackgroundJob(
            job_id=extra.job_id or self.job_id,
            kind=extra.kind if extra.kind == "monitor" else self.kind,
            status=status,
            description=extra.description or self.description,
            command=extra.command or self.command,
            cwd=extra.cwd or self.cwd,
            started_at=self.started_at if self.started_at is not None else extra.started_at,
            ended_at=extra.ended_at if extra.ended_at is not None else self.ended_at,
            output_path=extra.output_path or self.output_path,
            reported=self.reported or extra.reported,
            tool_call_id=extra.tool_call_id or self.tool_call_id,
        )

    def mapping(self, *, events: list[TraceEvent] | None = None) -> JsonObject:
        """CamelCase overview row."""
        ev_i = self.event_index(events) if events else None
        return {
            "id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "description": self.description,
            "command": self.command,
            "cwd": self.cwd,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "outputPath": self.output_path,
            "reported": self.reported,
            "toolCallId": self.tool_call_id,
            "eventIndex": ev_i,
        }

    def event_index(self, events: list[TraceEvent]) -> int | None:
        """First Timeline bookend, or None."""
        wanted = (self.job_id or "").strip()
        call = (self.tool_call_id or "").strip()
        for ev in events:
            ev_id = event_task_id(ev)
            ev_call = ev.tool_call_id or ""
            if (wanted and ev_id == wanted) or (call and ev_call == call):
                return int(ev.index)
        return None

    def evidence(self, events: list[TraceEvent]) -> tuple[list[int], list[str]]:
        """Timeline indexes and tool-call ids for this job."""
        indices: list[int] = []
        calls: list[str] = []
        wanted = (self.job_id or "").strip()
        call = (self.tool_call_id or "").strip()
        for ev in events:
            ev_id = event_task_id(ev)
            ev_call = ev.tool_call_id or ""
            if (wanted and ev_id == wanted) or (call and ev_call == call):
                indices.append(int(ev.index))
                if ev.tool_call_id:
                    calls.append(ev.tool_call_id)
        return indices, calls

    def finding(self, events: list[TraceEvent]) -> Finding:
        """Paste-ready Finding for a failed or cancelled job."""
        label = self.description or self.command or self.job_id
        indices, calls = self.evidence(events)
        asked = (self.command or self.description or "").strip() or label
        why = f"Background {self.kind or 'job'} {label} ended {self.status}."
        bits = [p for p in ((self.status or "").strip(), (self.kind or "").strip()) if p]
        happened = " · ".join(bits)
        where = f"Timeline {', '.join(f'#{i}' for i in indices[:8])}" if indices else f"job {label}"
        issue = (
            f"What: Backgrounded {asked}.\n"
            f"Where: {where}\n"
            f"Why: {why}\n"
            f"Should have: Finish the background job or monitor without a failed status.\n"
            f"Pattern: failed session run\n"
        )
        extras: JsonObject = {
            "what_model_did": f"Backgrounded {asked}.",
            "where": where,
            "why_mistake": why,
            "what_should_have_done": "Finish the background job or monitor without a failed status.",
            "issue_box": issue,
            "asked": asked,
            "happened": happened,
            "failed": self.status,
        }
        cancelled = (self.status or "").strip().lower() in {"cancelled", "interrupted"}
        return Finding(
            id=f"job:{self.job_id}",
            plugin_id="basic",
            severity=Severity.MEDIUM if cancelled else Severity.HIGH,
            title=f"Background job {label} failed",
            detail=why,
            category="job",
            tool_call_ids=calls,
            event_indices=indices,
            extras=extras,
        )


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
    event_index: int | None = None

    @classmethod
    def from_overview(cls, row: JsonObject) -> ScheduleTask:
        """Hydrate one ``session/overview`` schedule row."""
        return cls(
            task_id=json_as_str(row.get("id")),
            interval_secs=SessionJobs.optional_int(row.get("intervalSecs")),
            human_schedule=json_as_str(row.get("humanSchedule")),
            next_fire_at=json_as_str(row.get("nextFireAt")),
            last_fired_at=json_as_str(row.get("lastFiredAt")),
            last_subagent_id=json_as_str(row.get("lastSubagentId")),
            prompt_preview=json_as_str(row.get("promptPreview")),
            durable=row.get("durable") is True,
            recurring=row.get("recurring") is True,
            created_at=json_as_str(row.get("createdAt")),
            event_index=SessionJobs.optional_int(row.get("eventIndex")),
        )

    def mapping(self) -> JsonObject:
        """CamelCase overview row."""
        return {
            "id": self.task_id,
            "intervalSecs": self.interval_secs,
            "humanSchedule": self.human_schedule,
            "nextFireAt": self.next_fire_at,
            "lastFiredAt": self.last_fired_at,
            "lastSubagentId": self.last_subagent_id,
            "promptPreview": self.prompt_preview,
            "durable": self.durable,
            "recurring": self.recurring,
            "createdAt": self.created_at,
            "eventIndex": self.event_index,
        }


@dataclass
class SessionJobs:
    """Jobs, schedules, and workflow runs for one session directory."""

    jobs: list[BackgroundJob]
    schedules: list[ScheduleTask]
    workflows: list[WorkflowRun] = field(default_factory=list)

    @staticmethod
    def optional_int(val: JsonValue) -> int | None:
        """Int from JSON, or None when missing or not numeric."""
        return WorkflowRun.optional_int(val)

    @staticmethod
    def epoch(val: JsonValue) -> int | None:
        """Unix seconds from an int or ``{secs_since_epoch}`` object."""
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

    @staticmethod
    def read_json(path: Path) -> JsonValue:
        """JSON file contents, or None when missing or not JSON."""
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    @classmethod
    def load(cls, session_dir: Path, events: list[TraceEvent] | None = None) -> SessionJobs:
        """Merge timeline bookends, resources_state, manifest, and terminal logs."""
        sd = Path(session_dir)
        evs = events if events is not None else parse_timeline(sd)
        raw = cls.read_json(sd / "resources_state.json")
        mapping = as_json_object(raw) if isinstance(raw, dict) else {}
        inner = mapping.get("state")
        state = as_json_object(inner) if isinstance(inner, dict) else {}
        reported = cls.reported_ids(state)
        jobs: dict[str, BackgroundJob] = {}
        for ev in evs:
            if ev.event_type not in {"task_backgrounded", "task_completed"}:
                continue
            job = BackgroundJob.from_event(ev, sd)
            if not job.job_id:
                continue
            prev = jobs.get(job.job_id)
            jobs[job.job_id] = prev.merge(job) if prev else job
        raw_list = cls.read_json(sd / "background_tasks_manifest.json")
        rows = (
            [as_json_object(item) for item in raw_list if isinstance(item, dict)]
            if isinstance(raw_list, list)
            else []
        )
        for row in rows:
            job = BackgroundJob.from_mapping(row, sd)
            if not job.job_id:
                continue
            prev = jobs.get(job.job_id)
            jobs[job.job_id] = prev.merge(job) if prev else job
        for job_id, job in list(jobs.items()):
            log_status = BackgroundJob.status_from_log(job.output_path)
            status = log_status or job.status
            jobs[job_id] = replace(job, status=status, reported=job_id in reported)
        return cls(
            jobs=sorted(jobs.values(), key=lambda j: (j.started_at or 0, j.job_id)),
            schedules=sorted(
                cls.schedules_from(evs, state),
                key=lambda s: (s.created_at, s.task_id),
            ),
            workflows=load_session_workflows(sd),
        )

    @classmethod
    def from_overview(cls, overview: JsonObject) -> SessionJobs:
        """Hydrate domain rows from a ``session/overview`` payload."""
        jobs: list[BackgroundJob] = []
        raw_jobs = overview.get("backgroundJobs")
        if isinstance(raw_jobs, list):
            for item in raw_jobs:
                if isinstance(item, dict):
                    jobs.append(BackgroundJob.from_overview(as_json_object(item)))
        schedules: list[ScheduleTask] = []
        raw_sch = overview.get("schedules")
        if isinstance(raw_sch, list):
            for item in raw_sch:
                if isinstance(item, dict):
                    schedules.append(ScheduleTask.from_overview(as_json_object(item)))
        return cls(
            jobs=jobs,
            schedules=schedules,
            workflows=workflows_from_overview(overview),
        )

    @staticmethod
    def reported_ids(state: JsonObject) -> set[str]:
        """Task ids listed under ``ReportedTaskCompletions``."""
        block = state.get("grok_build.ReportedTaskCompletions")
        if not isinstance(block, dict):
            return set()
        rows = block.get("reported")
        if not isinstance(rows, list):
            return set()
        return {json_as_str(item).strip() for item in rows if json_as_str(item).strip()}

    @staticmethod
    def human_interval(secs: int | None) -> str:
        """``every N minutes`` from interval seconds."""
        if secs is None or secs <= 0:
            return ""
        if secs % 3600 == 0:
            hours = secs // 3600
            return f"every {hours} hour" if hours == 1 else f"every {hours} hours"
        if secs % 60 == 0:
            mins = secs // 60
            return f"every {mins} minute" if mins == 1 else f"every {mins} minutes"
        return f"every {secs} seconds"

    @classmethod
    def schedules_from(cls, events: list[TraceEvent], state: JsonObject) -> list[ScheduleTask]:
        """Merge timeline bookends with ``grok_build.Scheduler`` state."""
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
            prev = by_id.get(task_id)
            ev_i = int(ev.index)
            if (
                prev is None
                or prev.event_index is None
                or ev.event_type == "scheduled_task_created"
            ):
                bookend = ev_i
            else:
                bookend = prev.event_index
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
                event_index=bookend,
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
                interval = cls.optional_int(row.get("intervalSecs"))
                by_id[task_id] = ScheduleTask(
                    task_id=task_id,
                    interval_secs=interval,
                    human_schedule=(prev.human_schedule if prev else "")
                    or cls.human_interval(interval),
                    next_fire_at=prev.next_fire_at if prev else "",
                    last_fired_at=json_as_str(row.get("lastFiredAt")).strip(),
                    last_subagent_id=json_as_str(row.get("lastSubagentId")).strip(),
                    prompt_preview=(prev.prompt_preview if prev else "") or prompt[:200],
                    durable=row.get("durable") is True,
                    recurring=row.get("recurring") is True,
                    created_at=json_as_str(row.get("createdAt")).strip(),
                    event_index=prev.event_index if prev else None,
                )
        return list(by_id.values())

    @staticmethod
    def bookend_key(events: list[TraceEvent]) -> tuple[tuple[str, str, str], ...]:
        """Identity of timeline job / schedule bookends already in *events*."""
        rows: list[tuple[str, str, str]] = []
        for ev in events:
            kind = ev.event_type or ""
            if kind not in {"task_backgrounded", "task_completed"} and not kind.startswith(
                "scheduled_task_"
            ):
                continue
            rows.append((kind, event_task_id(ev), ev.tool_call_id or ""))
        return tuple(rows)

    _row_cache: ClassVar[BoundedCache[tuple[_JobReuseKey, _JobRowLists]]] = BoundedCache(
        OVERVIEW_CACHE_MAXSIZE
    )

    @staticmethod
    def copy_rows(
        jobs: list[JsonObject],
        schedules: list[JsonObject],
        workflows: list[JsonObject],
    ) -> tuple[list[JsonObject], list[JsonObject], list[JsonObject]]:
        """Shallow-copy overview job rows so later index writes stay local."""
        return (
            [dict(row) for row in jobs],
            [dict(row) for row in schedules],
            [dict(row) for row in workflows],
        )

    @staticmethod
    def json_rows(rows: list[JsonObject]) -> list[JsonValue]:
        """Overview job rows as a JSON list value."""
        return list(rows)

    @classmethod
    def overview_rows(
        cls,
        session_dir: Path,
        events: list[TraceEvent],
        cache_key: str,
    ) -> tuple[list[JsonObject], list[JsonObject], list[JsonObject]]:
        """Jobs / schedules / workflows, reused when files and bookends match."""
        sd = Path(session_dir)
        reuse_key = (job_input_stamp(sd), cls.bookend_key(events))
        cached = cls._row_cache.get(cache_key)
        if cached is not None and cached[0] == reuse_key:
            jobs, schedules, workflows = cls.copy_rows(*cached[1])
        else:
            packed = cls.load(sd, events)
            jobs = [job.mapping() for job in packed.jobs]
            schedules = [task.mapping() for task in packed.schedules]
            workflows = [workflow_mapping(run, parent_dir=sd) for run in packed.workflows]
            cls._row_cache[cache_key] = (
                reuse_key,
                cls.copy_rows(jobs, schedules, workflows),
            )
        set_bookend_indexes(events, jobs, workflows, schedules)
        return jobs, schedules, workflows


def session_jobs_for_view(
    overview: JsonObject | None,
    session_dir: Path,
    events: list[TraceEvent] | None,
) -> SessionJobs:
    """Owner payload when attached; disk merge when inspecting offline."""
    if overview is not None:
        return SessionJobs.from_overview(overview)
    return SessionJobs.load(session_dir, events)


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
            monitors.append((path.name, BackgroundJob.last_line_class(path)))
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
    return SessionJobs.load(session_dir, events)


def job_event_index(job: BackgroundJob, events: list[TraceEvent]) -> int | None:
    """First Timeline bookend for *job*, or None."""
    return job.event_index(events)


def set_bookend_indexes(
    events: list[TraceEvent],
    jobs: list[JsonObject],
    workflows: list[JsonObject],
    schedules: list[JsonObject] | None = None,
) -> None:
    """Set ``eventIndex`` on each job, workflow, and schedule row from one walk."""
    job_id_first: dict[str, int] = {}
    call_first: dict[str, int] = {}
    wf_id_first: dict[str, int] = {}
    wf_name_first: dict[str, int] = {}
    sched_first: dict[str, int] = {}
    for ev in events:
        ev_id = event_task_id(ev)
        if ev_id and ev_id not in job_id_first:
            job_id_first[ev_id] = int(ev.index)
        ev_call = (ev.tool_call_id or "").strip()
        if ev_call and ev_call not in call_first:
            call_first[ev_call] = int(ev.index)
        if ev.event_type.startswith("scheduled_task_") and ev_id:
            prev = sched_first.get(ev_id)
            if prev is None or ev.event_type == "scheduled_task_created":
                sched_first[ev_id] = int(ev.index)
        if (ev.tool_name or "") != "workflow":
            continue
        rid = workflow_run_id_from_raw(ev.raw_input) or workflow_run_id_from_raw(ev.content)
        if rid and rid not in wf_id_first:
            wf_id_first[rid] = int(ev.index)
        name = workflow_name_from_raw(ev.raw_input)
        if name and name not in wf_name_first:
            wf_name_first[name] = int(ev.index)
    for row in jobs:
        hits: list[int] = []
        jid = json_as_str(row.get("id")).strip()
        call = json_as_str(row.get("toolCallId")).strip()
        if jid and jid in job_id_first:
            hits.append(job_id_first[jid])
        if call and call in call_first:
            hits.append(call_first[call])
        row["eventIndex"] = min(hits) if hits else None
    for row in workflows:
        rid = json_as_str(row.get("id")).strip()
        name = json_as_str(row.get("name")).strip()
        if rid and rid in wf_id_first:
            row["eventIndex"] = wf_id_first[rid]
            continue
        named: int | None = None
        for ev_name, idx in wf_name_first.items():
            if name == ev_name or name.startswith(f"{ev_name}-"):
                if named is None or idx < named:
                    named = idx
        row["eventIndex"] = named
    for row in schedules or []:
        sid = json_as_str(row.get("id")).strip()
        row["eventIndex"] = sched_first.get(sid)


def job_mapping(job: BackgroundJob, *, events: list[TraceEvent] | None = None) -> JsonObject:
    """CamelCase overview row for one background or monitor job."""
    return job.mapping(events=events)


def schedule_mapping(task: ScheduleTask) -> JsonObject:
    """CamelCase overview row for one scheduler task."""
    return task.mapping()


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
    log_st = BackgroundJob.status_from_log(path)
    if (
        event.event_type == "task_completed"
        or (finish is not None and finish.event_type == "task_completed")
        or mapping.get("completed") is True
    ):
        return log_st or BackgroundJob.completed_status(mapping)
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
    return BackgroundJob.kind_from(mapping, output, desc)


def jobs_from_overview(overview: JsonObject) -> SessionJobs:
    """Hydrate domain rows from a ``session/overview`` payload."""
    return SessionJobs.from_overview(overview)


def read_log_tail(path: Path, *, max_chars: int = 8_000) -> str:
    """Last *max_chars* of a terminal or monitor log (empty when missing)."""
    if max_chars <= 0:
        return ""
    try:
        if not path.is_file():
            return ""
    except OSError:
        return ""
    # Extra bytes so a multi-byte UTF-8 sequence at the cut is not lost.
    byte_cap = max_chars + 4
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - byte_cap))
            chunk = handle.read(byte_cap)
    except OSError:
        return ""
    return chunk.decode("utf-8", errors="replace")[-max_chars:]


def monitor_line_status(text: str) -> str | None:
    """Map the last DONE/FAILED/CANCELLED token on its own line, if any."""
    for line in reversed(text.splitlines()):
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if token in _MONITOR_LINE:
            return _MONITOR_LINE[token]
    return None
