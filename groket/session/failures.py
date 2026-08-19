"""Findings for failed workflow runs and background jobs."""

from __future__ import annotations

from pathlib import Path

from ..analysis.base import Finding
from ..models import JsonObject, Severity, TraceEvent
from .jobs import BackgroundJob, event_task_id, load_session_jobs
from .workflows import WorkflowRun, workflow_name_from_raw, workflow_run_id_from_raw

PLUGIN_ID = "basic"
_FAILED = frozenset({"failed", "error", "cancelled", "interrupted"})


def findings_for_failed_runs(
    session_dir: Path, events: list[TraceEvent] | None = None
) -> list[Finding]:
    """One Finding per failed workflow or background/monitor job.

    :param session_dir: Session directory (``workflows/`` + timeline).
    :param events: Optional pre-parsed timeline.
    :returns: Findings with event indices and paste-ready extras.
    """
    sd = Path(session_dir)
    evs = events
    if evs is None:
        from ..parser import parse_timeline

        try:
            evs = parse_timeline(sd)
        except OSError:
            evs = []
    try:
        packed = load_session_jobs(sd, evs)
    except OSError:
        return []
    out: list[Finding] = []
    for run in packed.workflows:
        if (run.status or "").strip().lower() in _FAILED:
            out.append(_workflow_finding(run, evs))
    for job in packed.jobs:
        if (job.status or "").strip().lower() in _FAILED:
            out.append(_job_finding(job, evs))
    return out


def _workflow_finding(run: WorkflowRun, events: list[TraceEvent]) -> Finding:
    name = run.name or run.run_id
    indices, call_ids = _workflow_evidence(run, events)
    fail = (run.pause_message or "").strip()
    asked = (run.objective or "").strip() or name
    happened = _happened_line(run.status, run.phase)
    why = fail or f"Workflow {name} ended {run.status}."
    extras = _extras(
        what=f"Ran workflow {name}.",
        where=_where(indices, f"workflow {name}"),
        why=why,
        should="Complete the workflow without a failed or interrupted status.",
        asked=asked,
        happened=happened,
        failed=fail,
    )
    return Finding(
        id=f"workflow:{run.run_id}",
        plugin_id=PLUGIN_ID,
        severity=_severity(run.status),
        title=f"Workflow {name} failed",
        detail=fail or why,
        category="workflow",
        tool_call_ids=call_ids,
        event_indices=indices,
        extras=extras,
    )


def _job_finding(job: BackgroundJob, events: list[TraceEvent]) -> Finding:
    label = job.description or job.command or job.job_id
    indices, call_ids = _job_evidence(job, events)
    asked = (job.command or job.description or "").strip() or label
    why = f"Background {job.kind or 'job'} {label} ended {job.status}."
    extras = _extras(
        what=f"Backgrounded {asked}.",
        where=_where(indices, f"job {label}"),
        why=why,
        should="Finish the background job or monitor without a failed status.",
        asked=asked,
        happened=_happened_line(job.status, job.kind),
        failed=job.status,
    )
    return Finding(
        id=f"job:{job.job_id}",
        plugin_id=PLUGIN_ID,
        severity=_severity(job.status),
        title=f"Background job {label} failed",
        detail=why,
        category="job",
        tool_call_ids=call_ids,
        event_indices=indices,
        extras=extras,
    )


def _workflow_evidence(run: WorkflowRun, events: list[TraceEvent]) -> tuple[list[int], list[str]]:
    by_id: list[TraceEvent] = []
    by_name: list[TraceEvent] = []
    for ev in events:
        if (ev.tool_name or "") != "workflow":
            continue
        rid = workflow_run_id_from_raw(ev.raw_input) or workflow_run_id_from_raw(ev.content)
        if rid == run.run_id:
            by_id.append(ev)
            continue
        if rid:
            continue
        if _name_matches(ev, run):
            by_name.append(ev)
    chosen = by_id or by_name
    indices = [int(ev.index) for ev in chosen]
    calls = [ev.tool_call_id for ev in chosen if ev.tool_call_id]
    return indices, calls


def _job_evidence(job: BackgroundJob, events: list[TraceEvent]) -> tuple[list[int], list[str]]:
    indices: list[int] = []
    calls: list[str] = []
    wanted = (job.job_id or "").strip()
    call = (job.tool_call_id or "").strip()
    for ev in events:
        ev_id = event_task_id(ev)
        ev_call = ev.tool_call_id or ""
        if (wanted and ev_id == wanted) or (call and ev_call == call):
            indices.append(int(ev.index))
            if ev.tool_call_id:
                calls.append(ev.tool_call_id)
    return indices, calls


def _name_matches(ev: TraceEvent, run: WorkflowRun) -> bool:
    name = workflow_name_from_raw(ev.raw_input)
    return bool(name) and (run.name == name or run.name.startswith(f"{name}-"))


def _severity(status: str) -> Severity:
    if (status or "").strip().lower() in {"cancelled", "interrupted"}:
        return Severity.MEDIUM
    return Severity.HIGH


def _happened_line(status: str, extra: str) -> str:
    bits = [p for p in ((status or "").strip(), (extra or "").strip()) if p]
    return " · ".join(bits)


def _where(indices: list[int], fallback: str) -> str:
    if indices:
        shown = ", ".join(f"#{i}" for i in indices[:8])
        return f"Timeline {shown}"
    return fallback


def _extras(
    *,
    what: str,
    where: str,
    why: str,
    should: str,
    asked: str,
    happened: str,
    failed: str,
) -> JsonObject:
    issue = (
        f"What: {what}\n"
        f"Where: {where}\n"
        f"Why: {why}\n"
        f"Should have: {should}\n"
        f"Pattern: failed session run\n"
    )
    return {
        "what_model_did": what,
        "where": where,
        "why_mistake": why,
        "what_should_have_done": should,
        "issue_box": issue,
        "asked": asked,
        "happened": happened,
        "failed": failed,
    }
