"""Merge Grok workflow runs from ``workflows/wf_*`` on disk."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..models import JsonObject, JsonValue, ToolInputBag, TraceEvent, as_json_object, json_as_str

_MAX_CHILDREN = 24
_NAME_IN_SCRIPT = re.compile(r"name:\s*\"([^\"]+)\"")


@dataclass(frozen=True)
class WorkflowChild:
    """One child agent from ``state.agents`` or a journal spawn line."""

    agent_id: str
    label: str
    success: bool


@dataclass
class WorkflowRun:
    """One ``workflows/wf_*`` run after merge."""

    run_id: str
    name: str
    status: str
    phase: str
    objective: str
    agents_used: int | None
    agent_budget: int | None
    elapsed_ms: int | None
    pause_message: str
    children: list[WorkflowChild]


def load_session_workflows(session_dir: Path) -> list[WorkflowRun]:
    """Load each ``workflows/wf_*`` directory (state + capped children)."""
    root = Path(session_dir) / "workflows"
    if not root.is_dir():
        return []
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("wf_"))
    except OSError:
        return []
    runs: list[WorkflowRun] = []
    for path in dirs:
        run = _run_from_dir(path)
        if run is not None:
            runs.append(run)
    return runs


def workflows_from_overview(overview: JsonObject) -> list[WorkflowRun]:
    """Hydrate domain rows from a ``session/overview`` payload."""
    raw = overview.get("workflows")
    if not isinstance(raw, list):
        return []
    out: list[WorkflowRun] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = as_json_object(item)
        children = _children_from_payload(row.get("children"))
        out.append(
            WorkflowRun(
                run_id=json_as_str(row.get("id")),
                name=json_as_str(row.get("name")),
                status=json_as_str(row.get("status")) or "running",
                phase=json_as_str(row.get("phase")),
                objective=json_as_str(row.get("objective")),
                agents_used=_as_int(row.get("agentsUsed")),
                agent_budget=_as_int(row.get("agentBudget")),
                elapsed_ms=_as_int(row.get("elapsedMs")),
                pause_message=json_as_str(row.get("pauseMessage")),
                children=children,
            )
        )
    return out


def workflow_event_index(run: WorkflowRun, events: list[TraceEvent]) -> int | None:
    """First Timeline bookend for *run*, or None.

    Prefers any event whose bag or body carries ``run_id``. Name / script-path
    stem is only used when no id hit exists (an earlier ``sprint.rhai`` stem
    must not steal a later ``sprint-11`` result).
    """
    named: int | None = None
    for ev in events:
        if (ev.tool_name or "") != "workflow":
            continue
        rid = workflow_run_id_from_raw(ev.raw_input) or workflow_run_id_from_raw(ev.content)
        if rid == run.run_id:
            return int(ev.index)
        name = workflow_name_from_raw(ev.raw_input)
        if name and named is None and (run.name == name or run.name.startswith(f"{name}-")):
            named = int(ev.index)
    return named


def workflow_mapping(
    run: WorkflowRun,
    *,
    events: list[TraceEvent] | None = None,
    parent_dir: Path | None = None,
) -> JsonObject:
    """CamelCase overview row, including bookend index and openable children."""
    from .subagents import resolve_child_session_path

    ev_i = workflow_event_index(run, events) if events else None
    children: list[JsonValue] = []
    for child in run.children:
        row: JsonObject = {
            "id": child.agent_id,
            "label": child.label,
            "success": child.success,
            "sessionId": child.agent_id,
        }
        if parent_dir is not None:
            path = resolve_child_session_path(parent_dir, child.agent_id)
            if path is not None:
                row["path"] = str(path)
        children.append(row)
    return {
        "id": run.run_id,
        "name": run.name,
        "status": run.status,
        "phase": run.phase,
        "objective": run.objective,
        "agentsUsed": run.agents_used,
        "agentBudget": run.agent_budget,
        "elapsedMs": run.elapsed_ms,
        "pauseMessage": run.pause_message,
        "eventIndex": ev_i,
        "children": children,
    }


def workflow_name_from_raw(raw: JsonObject | ToolInputBag | None) -> str:
    """Name / script-path stem / ``name: \"…\"`` in an inline script."""
    mapping = _raw_map(raw)
    name = json_as_str(mapping.get("name")).strip()
    if name and name.lower() not in {"none", "null"}:
        return name
    path = json_as_str(mapping.get("script_path")).strip()
    if path:
        stem = Path(path).stem.strip()
        if stem:
            return stem
    script = json_as_str(mapping.get("script"))
    match = _NAME_IN_SCRIPT.search(script)
    if match:
        return match.group(1).strip()
    return ""


def workflow_run_id_from_raw(raw: JsonObject | ToolInputBag | str | None) -> str:
    """``run_id`` from a tool bag or a JSON result body."""
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ""
        try:
            blob = json.loads(text)
        except json.JSONDecodeError:
            return ""
        if isinstance(blob, dict):
            return json_as_str(blob.get("run_id")).strip()
        return ""
    mapping = _raw_map(raw)
    for key in ("run_id", "resume_from_run_id"):
        rid = json_as_str(mapping.get(key)).strip()
        if rid:
            return rid
    return ""


def workflow_for_event(
    event: TraceEvent,
    runs: list[WorkflowRun],
    *,
    mate: TraceEvent | None = None,
) -> WorkflowRun | None:
    """Match a Timeline ``workflow`` tool to a merged run.

    Prefers ``run_id`` on the bookend or its mate (copied from
    ``rawOutput``). Name / script-path stem is fallback only.
    """
    if (event.tool_name or "") != "workflow":
        return None
    bags: list[JsonObject | ToolInputBag | str | None] = [event.raw_input, event.content]
    if mate is not None:
        bags.extend((mate.raw_input, mate.content))
    for bag in bags:
        rid = workflow_run_id_from_raw(bag)
        if not rid:
            continue
        for run in runs:
            if run.run_id == rid:
                return run
    name = workflow_name_from_raw(event.raw_input)
    if not name and mate is not None:
        name = workflow_name_from_raw(mate.raw_input)
    return _latest_named(runs, name)


def workflow_list_preview(raw: JsonObject | ToolInputBag | None) -> str:
    """Timeline summary: run name, not the Rhai script."""
    return workflow_name_from_raw(raw)


def _latest_named(runs: list[WorkflowRun], name: str) -> WorkflowRun | None:
    if not name:
        return None
    hits = [run for run in runs if run.name == name or run.name.startswith(f"{name}-")]
    if not hits:
        return None
    return sorted(hits, key=lambda r: r.run_id)[-1]


def _run_from_dir(path: Path) -> WorkflowRun | None:
    blob = _read_json(path / "state.json")
    inner = blob.get("state")
    mapping = as_json_object(inner if isinstance(inner, dict) else blob)
    run_id = json_as_str(mapping.get("run_id")).strip() or path.name
    name = json_as_str(mapping.get("name")).strip() or run_id
    children = _children_from_state(mapping.get("agents"))
    if not children:
        children = _journal_children(path / "journal.jsonl")
    return WorkflowRun(
        run_id=run_id,
        name=name,
        status=json_as_str(mapping.get("status")).strip() or "running",
        phase=json_as_str(mapping.get("current_phase")).strip(),
        objective=json_as_str(mapping.get("objective")).strip(),
        agents_used=_as_int(mapping.get("agents_used")),
        agent_budget=_as_int(mapping.get("agent_budget")),
        elapsed_ms=_as_int(mapping.get("elapsed_ms_floor")),
        pause_message=json_as_str(mapping.get("pause_message")).strip(),
        children=children[:_MAX_CHILDREN],
    )


def _children_from_state(raw: JsonValue) -> list[WorkflowChild]:
    if not isinstance(raw, list):
        return []
    out: list[WorkflowChild] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = as_json_object(item)
        agent_id = json_as_str(row.get("agent_id")).strip()
        if not agent_id:
            continue
        state = json_as_str(row.get("state")).strip().lower()
        out.append(
            WorkflowChild(
                agent_id=agent_id,
                label=json_as_str(row.get("label")).strip() or agent_id,
                success=state not in {"failed", "error", "cancelled"},
            )
        )
    return out


def _children_from_payload(raw: JsonValue) -> list[WorkflowChild]:
    if not isinstance(raw, list):
        return []
    out: list[WorkflowChild] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = as_json_object(item)
        agent_id = json_as_str(row.get("id")).strip()
        if not agent_id:
            continue
        out.append(
            WorkflowChild(
                agent_id=agent_id,
                label=json_as_str(row.get("label")).strip() or agent_id,
                success=row.get("success") is True,
            )
        )
    return out


def _journal_children(path: Path) -> list[WorkflowChild]:
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[WorkflowChild] = []
    for line in lines:
        child = _journal_line(line)
        if child is not None:
            out.append(child)
    return out[-_MAX_CHILDREN:]


def _journal_line(line: str) -> WorkflowChild | None:
    try:
        blob = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(blob, dict) or blob.get("kind") != "spawn_agent":
        return None
    result = blob.get("result")
    if not isinstance(result, dict):
        return None
    agent_id = json_as_str(result.get("agent_id")).strip()
    if not agent_id:
        return None
    output = result.get("output")
    label = ""
    if isinstance(output, dict):
        label = json_as_str(output.get("summary")).strip()
        if not label:
            seat = output.get("seat")
            if isinstance(seat, list) and seat:
                label = str(seat[0])
    return WorkflowChild(
        agent_id=agent_id,
        label=label[:80] or agent_id,
        success=result.get("success") is True and result.get("cancelled") is not True,
    )


def _raw_map(raw: JsonObject | ToolInputBag | None) -> JsonObject:
    if isinstance(raw, ToolInputBag):
        bag = raw.raw()
        return as_json_object(bag) if isinstance(bag, dict) else {}
    if isinstance(raw, dict):
        return as_json_object(raw)
    return {}


def _read_json(path: Path) -> JsonObject:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return as_json_object(data) if isinstance(data, dict) else {}


def _as_int(value: JsonValue) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None
