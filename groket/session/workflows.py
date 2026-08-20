"""Merge Grok workflow runs from ``workflows/wf_*`` on disk."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..analysis.base import Finding
from ..models import (
    JsonObject,
    JsonValue,
    Severity,
    ToolInputBag,
    TraceEvent,
    as_json_object,
    json_as_str,
)

_MAX_CHILDREN = 24
_NAME_IN_SCRIPT = re.compile(r"name:\s*\"([^\"]+)\"")


@dataclass(frozen=True)
class WorkflowChild:
    """One child agent from ``state.agents`` or a journal spawn line."""

    agent_id: str
    label: str
    success: bool

    @classmethod
    def from_state(cls, raw: JsonValue) -> list[WorkflowChild]:
        """Children listed on ``state.agents``."""
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
                cls(
                    agent_id=agent_id,
                    label=json_as_str(row.get("label")).strip() or agent_id,
                    success=state not in {"failed", "error", "cancelled"},
                )
            )
        return out

    @classmethod
    def from_overview(cls, raw: JsonValue) -> list[WorkflowChild]:
        """Children on a ``session/overview`` workflow row."""
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
                cls(
                    agent_id=agent_id,
                    label=json_as_str(row.get("label")).strip() or agent_id,
                    success=row.get("success") is True,
                )
            )
        return out

    @classmethod
    def from_journal_line(cls, line: str) -> WorkflowChild | None:
        """One ``spawn_agent`` journal line, or None."""
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
        return cls(
            agent_id=agent_id,
            label=label[:80] or agent_id,
            success=result.get("success") is True and result.get("cancelled") is not True,
        )

    @classmethod
    def from_journal(cls, path: Path) -> list[WorkflowChild]:
        """Capped children from ``journal.jsonl`` when ``state.agents`` is empty."""
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out: list[WorkflowChild] = []
        for line in lines:
            child = cls.from_journal_line(line)
            if child is not None:
                out.append(child)
        return out[-_MAX_CHILDREN:]


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

    @staticmethod
    def optional_int(value: JsonValue) -> int | None:
        """Int from JSON, or None when missing or not numeric."""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def raw_map(raw: JsonObject | ToolInputBag | None) -> JsonObject:
        """Tool bag or mapping as a plain JSON object."""
        if isinstance(raw, ToolInputBag):
            bag = raw.raw()
            return as_json_object(bag) if isinstance(bag, dict) else {}
        if isinstance(raw, dict):
            return as_json_object(raw)
        return {}

    @classmethod
    def from_directory(cls, path: Path) -> WorkflowRun | None:
        """Load ``state.json`` (and journal children if agents are empty)."""
        state_path = path / "state.json"
        if not state_path.is_file():
            return None
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        blob = as_json_object(data) if isinstance(data, dict) else {}
        inner = blob.get("state")
        mapping = as_json_object(inner if isinstance(inner, dict) else blob)
        run_id = json_as_str(mapping.get("run_id")).strip() or path.name
        name = json_as_str(mapping.get("name")).strip() or run_id
        children = WorkflowChild.from_state(mapping.get("agents"))
        if not children:
            children = WorkflowChild.from_journal(path / "journal.jsonl")
        return cls(
            run_id=run_id,
            name=name,
            status=json_as_str(mapping.get("status")).strip() or "running",
            phase=json_as_str(mapping.get("current_phase")).strip(),
            objective=json_as_str(mapping.get("objective")).strip(),
            agents_used=cls.optional_int(mapping.get("agents_used")),
            agent_budget=cls.optional_int(mapping.get("agent_budget")),
            elapsed_ms=cls.optional_int(mapping.get("elapsed_ms_floor")),
            pause_message=json_as_str(mapping.get("pause_message")).strip(),
            children=children[:_MAX_CHILDREN],
        )

    @classmethod
    def from_overview(cls, row: JsonObject) -> WorkflowRun:
        """Hydrate one ``session/overview`` workflow row."""
        return cls(
            run_id=json_as_str(row.get("id")),
            name=json_as_str(row.get("name")),
            status=json_as_str(row.get("status")) or "running",
            phase=json_as_str(row.get("phase")),
            objective=json_as_str(row.get("objective")),
            agents_used=cls.optional_int(row.get("agentsUsed")),
            agent_budget=cls.optional_int(row.get("agentBudget")),
            elapsed_ms=cls.optional_int(row.get("elapsedMs")),
            pause_message=json_as_str(row.get("pauseMessage")),
            children=WorkflowChild.from_overview(row.get("children")),
        )

    @classmethod
    def name_from_raw(cls, raw: JsonObject | ToolInputBag | None) -> str:
        """Name / script-path stem / ``name: \"…\"`` in an inline script."""
        mapping = cls.raw_map(raw)
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

    @classmethod
    def id_from_raw(cls, raw: JsonObject | ToolInputBag | str | None) -> str:
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
        mapping = cls.raw_map(raw)
        for key in ("run_id", "resume_from_run_id"):
            rid = json_as_str(mapping.get(key)).strip()
            if rid:
                return rid
        return ""

    @classmethod
    def merge_output_ids(
        cls, bag: ToolInputBag, raw_output: object, *, tool_name: str = ""
    ) -> ToolInputBag:
        """Copy ``run_id`` / ``name`` from a Workflow ``rawOutput`` onto *bag*."""
        if not isinstance(raw_output, dict):
            return bag
        kind = json_as_str(raw_output.get("type")).strip()
        if (tool_name or "") != "workflow" and kind != "Workflow":
            return bag
        mapping = as_json_object(raw_output)
        rid = json_as_str(mapping.get("run_id")).strip()
        if not rid:
            return bag
        data = dict(bag.raw())
        if not json_as_str(data.get("run_id")).strip():
            data["run_id"] = rid
        name = json_as_str(mapping.get("name")).strip()
        if name and not json_as_str(data.get("name")).strip():
            data["name"] = name
        return ToolInputBag(data)

    @classmethod
    def latest_named(cls, runs: list[WorkflowRun], name: str) -> WorkflowRun | None:
        """Newest run whose name is *name* or ``{name}-…``."""
        if not name:
            return None
        hits = [run for run in runs if run.name == name or run.name.startswith(f"{name}-")]
        if not hits:
            return None
        return sorted(hits, key=lambda run: run.run_id)[-1]

    def event_index(self, events: list[TraceEvent]) -> int | None:
        """First Timeline bookend: ``run_id`` wins, else name / script stem."""
        named: int | None = None
        for ev in events:
            if (ev.tool_name or "") != "workflow":
                continue
            rid = self.id_from_raw(ev.raw_input) or self.id_from_raw(ev.content)
            if rid == self.run_id:
                return int(ev.index)
            name = self.name_from_raw(ev.raw_input)
            if name and named is None and (self.name == name or self.name.startswith(f"{name}-")):
                named = int(ev.index)
        return named

    def mapping(
        self,
        *,
        events: list[TraceEvent] | None = None,
        parent_dir: Path | None = None,
    ) -> JsonObject:
        """CamelCase overview row, including bookend index and openable children."""
        from .subagents import resolve_child_session_path

        ev_i = self.event_index(events) if events else None
        children: list[JsonValue] = []
        for child in self.children:
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
            "id": self.run_id,
            "name": self.name,
            "status": self.status,
            "phase": self.phase,
            "objective": self.objective,
            "agentsUsed": self.agents_used,
            "agentBudget": self.agent_budget,
            "elapsedMs": self.elapsed_ms,
            "pauseMessage": self.pause_message,
            "eventIndex": ev_i,
            "children": children,
        }

    def matches_name(self, event: TraceEvent) -> bool:
        """True when *event* has this run's name or a script-stem prefix."""
        name = self.name_from_raw(event.raw_input)
        return bool(name) and (self.name == name or self.name.startswith(f"{name}-"))

    def evidence(self, events: list[TraceEvent]) -> tuple[list[int], list[str]]:
        """Timeline indexes and tool-call ids for this run."""
        by_id: list[TraceEvent] = []
        by_name: list[TraceEvent] = []
        for ev in events:
            if (ev.tool_name or "") != "workflow":
                continue
            rid = self.id_from_raw(ev.raw_input) or self.id_from_raw(ev.content)
            if rid == self.run_id:
                by_id.append(ev)
                continue
            if rid:
                continue
            if self.matches_name(ev):
                by_name.append(ev)
        chosen = by_id or by_name
        return [int(ev.index) for ev in chosen], [
            ev.tool_call_id for ev in chosen if ev.tool_call_id
        ]

    def finding(self, events: list[TraceEvent]) -> Finding:
        """Paste-ready Finding for a failed or interrupted run."""
        name = self.name or self.run_id
        indices, calls = self.evidence(events)
        fail = (self.pause_message or "").strip()
        asked = (self.objective or "").strip() or name
        bits = [p for p in ((self.status or "").strip(), (self.phase or "").strip()) if p]
        happened = " · ".join(bits)
        why = fail or f"Workflow {name} ended {self.status}."
        where = (
            f"Timeline {', '.join(f'#{i}' for i in indices[:8])}" if indices else f"workflow {name}"
        )
        issue = (
            f"What: Ran workflow {name}.\n"
            f"Where: {where}\n"
            f"Why: {why}\n"
            f"Should have: Complete the workflow without a failed or interrupted status.\n"
            f"Pattern: failed session run\n"
        )
        extras: JsonObject = {
            "what_model_did": f"Ran workflow {name}.",
            "where": where,
            "why_mistake": why,
            "what_should_have_done": "Complete the workflow without a failed or interrupted status.",
            "issue_box": issue,
            "asked": asked,
            "happened": happened,
            "failed": fail,
        }
        cancelled = (self.status or "").strip().lower() in {"cancelled", "interrupted"}
        return Finding(
            id=f"workflow:{self.run_id}",
            plugin_id="basic",
            severity=Severity.MEDIUM if cancelled else Severity.HIGH,
            title=f"Workflow {name} failed",
            detail=fail or why,
            category="workflow",
            tool_call_ids=calls,
            event_indices=indices,
            extras=extras,
        )


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
        run = WorkflowRun.from_directory(path)
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
        if isinstance(item, dict):
            out.append(WorkflowRun.from_overview(as_json_object(item)))
    return out


def workflow_event_index(run: WorkflowRun, events: list[TraceEvent]) -> int | None:
    """First Timeline bookend for *run*, or None."""
    return run.event_index(events)


def workflow_mapping(
    run: WorkflowRun,
    *,
    events: list[TraceEvent] | None = None,
    parent_dir: Path | None = None,
) -> JsonObject:
    """CamelCase overview row, including bookend index and openable children."""
    return run.mapping(events=events, parent_dir=parent_dir)


def workflow_name_from_raw(raw: JsonObject | ToolInputBag | None) -> str:
    """Name / script-path stem / ``name: \"…\"`` in an inline script."""
    return WorkflowRun.name_from_raw(raw)


def workflow_run_id_from_raw(raw: JsonObject | ToolInputBag | str | None) -> str:
    """``run_id`` from a tool bag or a JSON result body."""
    return WorkflowRun.id_from_raw(raw)


def workflow_for_event(
    event: TraceEvent,
    runs: list[WorkflowRun],
    *,
    mate: TraceEvent | None = None,
) -> WorkflowRun | None:
    """Match a Timeline ``workflow`` tool to a merged run.

    Prefers ``run_id`` on the bookend or its mate (copied from
    ``rawOutput``). Name / script-path stem is used when no id is present.
    """
    if (event.tool_name or "") != "workflow":
        return None
    bags: list[JsonObject | ToolInputBag | str | None] = [event.raw_input, event.content]
    if mate is not None:
        bags.extend((mate.raw_input, mate.content))
    for bag in bags:
        rid = WorkflowRun.id_from_raw(bag)
        if not rid:
            continue
        for run in runs:
            if run.run_id == rid:
                return run
    name = WorkflowRun.name_from_raw(event.raw_input)
    if not name and mate is not None:
        name = WorkflowRun.name_from_raw(mate.raw_input)
    return WorkflowRun.latest_named(runs, name)


def workflow_list_preview(raw: JsonObject | ToolInputBag | None) -> str:
    """Timeline summary: run name, not the Rhai script."""
    return WorkflowRun.name_from_raw(raw)
