"""Findings for failed workflow runs and background jobs."""

from __future__ import annotations

from pathlib import Path

from ..analysis.base import Finding
from ..models import TraceEvent
from .jobs import load_session_jobs

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

        evs = parse_timeline(sd)
    packed = load_session_jobs(sd, evs)
    out: list[Finding] = []
    for run in packed.workflows:
        if (run.status or "").strip().lower() in _FAILED:
            out.append(run.finding(evs))
    for job in packed.jobs:
        if (job.status or "").strip().lower() in _FAILED:
            out.append(job.finding(evs))
    return out
