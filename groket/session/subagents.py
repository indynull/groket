"""Classify subagent sessions, resolve child paths, and build turn-linked runs."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..models import JsonObject, JsonValue, ToolInputBag, TraceEvent, as_json_object, json_as_str
from ..tool_display import clip_preview
from .turns import TurnSegment

_KIND_KEYS = ("session_kind", "sessionKind")
_SPAWN_CHILD_KEYS = ("child_session_id", "childSessionId")
_SPAWN_ID_KEYS = ("subagent_id", "subagentId")
_SPAWN_PROMPT_KEYS = ("parent_prompt_id", "parentPromptId")
_SPAWN_TYPE_KEYS = ("subagent_type", "subagentType")
_SPAWN_DESC_KEYS = ("description",)
_FINISH_STATUS_KEYS = ("status",)
_FINISH_DUR_KEYS = ("duration_ms", "durationMs")
_FINISH_TOOLS_KEYS = ("tool_calls", "toolCalls")
_FINISH_TURNS_KEYS = ("turns",)
_FINISH_TOKENS_KEYS = ("tokens_used", "tokensUsed")
_FINISH_OUTPUT_KEYS = ("output",)


def is_subagent_kind(kind: str) -> bool:
    """True when *kind* is a harness subagent (not an operator fork)."""
    return (kind or "").strip().lower().startswith("subagent")


def compact_child_chrome(kind: str, turn_count: int) -> bool:
    """True when a subagent session has exactly one operator turn.

    Hide the Turns pane and the Events turn picker. If a child later has
    more than one turn, the full chrome comes back.
    """
    return is_subagent_kind(kind) and turn_count == 1


def read_session_kind(path: Path) -> str:
    """Return ``session_kind`` from *path*/``summary.json``, or empty."""
    sj = path / "summary.json"
    if not sj.is_file():
        return ""
    try:
        raw = json.loads(sj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(raw, dict):
        return ""
    return SubagentRun.first_str(raw, _KIND_KEYS)


def is_nested_subagent_stub(path: Path) -> bool:
    """True when *path* is ``<parent-session>/subagents/<child-id>``."""
    return path.parent.name == "subagents"


def is_subagent_session_dir(path: Path) -> bool:
    """True when *path* is a nested stub or a subagent-kind session."""
    if is_nested_subagent_stub(path):
        return True
    return is_subagent_kind(read_session_kind(path))


def nested_child_ids(sessions: list[Path]) -> set[str]:
    """Basenames listed under any *sessions* entry's ``subagents/``."""
    ids: set[str] = set()
    for session in sessions:
        sub_root = session / "subagents"
        if not sub_root.is_dir():
            continue
        try:
            with os.scandir(sub_root) as it:
                for ent in it:
                    if ent.is_dir(follow_symlinks=False):
                        ids.add(ent.name)
        except OSError:
            continue
    return ids


def drop_subagent_sessions(sessions: list[Path]) -> list[Path]:
    """Keep primary sessions only (path, kind, or known nested child id)."""
    if not sessions:
        return sessions
    child_ids = nested_child_ids(sessions)
    kept: list[Path] = []
    for session in sessions:
        if is_nested_subagent_stub(session):
            continue
        if is_subagent_kind(read_session_kind(session)):
            continue
        if session.name in child_ids:
            continue
        kept.append(session)
    return kept


def parent_session_dir(child: Path) -> Path | None:
    """Parent session for a nested stub or a sibling listed under ``subagents/``."""
    if is_nested_subagent_stub(child):
        parent = child.parent.parent
        return parent if parent.is_dir() else None
    name = child.name
    try:
        with os.scandir(child.parent) as it:
            siblings = list(it)
    except OSError:
        return None
    for ent in siblings:
        if not ent.is_dir(follow_symlinks=False) or ent.name == name:
            continue
        if (Path(ent.path) / "subagents" / name).is_dir():
            return Path(ent.path)
    return None


def session_changed_targets(session_dir: Path) -> list[Path]:
    """Sessions that should hear ``session/changed`` for a write under *session_dir*.

    Hidden children notify the parent (open parent buffers reload) and the
    child itself when it is a full on-disk session (someone may have it open).
    Ordinary catalog sessions notify themselves only.
    """
    if not is_subagent_session_dir(session_dir):
        return [session_dir]
    out: list[Path] = []
    seen: set[str] = set()
    parent = parent_session_dir(session_dir)
    if parent is not None:
        out.append(parent)
        seen.add(str(parent))
    if is_full_session_mirror(session_dir) and str(session_dir) not in seen:
        out.append(session_dir)
    return out


def is_full_session_mirror(path: Path) -> bool:
    """True when *path* has a full child timeline (not only a nested stub)."""
    if not path.is_dir():
        return False
    return (path / "updates.jsonl").is_file() or (path / "summary.json").is_file()


def resolve_child_session_path(
    parent_dir: Path,
    child_session_id: str,
    *,
    search_roots: list[Path] | None = None,
) -> Path | None:
    """Return a full child session dir, preferring a mirror over a nested stub.

    :param parent_dir: Parent session directory.
    :param child_session_id: Child session basename.
    :param search_roots: Extra roots (token dir, host/work sessions). Token
        parent and sessions root are always probed.
    :returns: Path with ``updates.jsonl`` or ``summary.json``, or None.
    """
    cid = (child_session_id or "").strip()
    if not cid:
        return None
    sibling = parent_dir.parent / cid
    if is_full_session_mirror(sibling):
        return sibling
    roots: list[Path] = []
    token = parent_dir.parent
    sessions_root = token.parent
    for root in (token, sessions_root, *(search_roots or ())):
        if root not in roots:
            roots.append(root)
    for root in roots:
        hit = SubagentRun.named_under(root, cid)
        if hit is not None and is_full_session_mirror(hit):
            return hit
    return None


@dataclass
class SubagentRun:
    """One subagent run linked to a parent turn."""

    subagent_id: str
    child_session_id: str
    child_path: Path | None
    subagent_type: str
    description: str
    status: str
    parent_turn_index: int | None
    parent_prompt_id: str
    spawn_event_index: int | None
    finish_event_index: int | None
    duration_ms: int | None
    tool_calls: int | None
    turns: int | None
    tokens_used: int | None
    output_preview: str

    @property
    def openable(self) -> bool:
        return self.child_path is not None and is_full_session_mirror(self.child_path)

    @staticmethod
    def first_str(data: Mapping[str, JsonValue], keys: tuple[str, ...]) -> str:
        """First non-empty string among *keys*."""
        for key in keys:
            val = data.get(key)
            if val is None:
                continue
            text = str(val).strip()
            if text:
                return text
        return ""

    @staticmethod
    def first_int(data: Mapping[str, JsonValue], keys: tuple[str, ...]) -> int | None:
        """First integer among *keys*."""
        for key in keys:
            val = data.get(key)
            if isinstance(val, bool):
                continue
            if isinstance(val, int):
                return val
            if isinstance(val, float) and val.is_integer():
                return int(val)
            if isinstance(val, str) and val.strip().isdigit():
                return int(val.strip())
        return None

    @staticmethod
    def optional_int(value: JsonValue) -> int | None:
        """Int from JSON, or None when missing or not numeric."""
        from .workflows import WorkflowRun

        return WorkflowRun.optional_int(value)

    @staticmethod
    def named_under(root: Path, name: str) -> Path | None:
        """Full session named *name* under *root* or one encoded-cwd child."""
        if not root.is_dir():
            return None
        direct = root / name
        if is_full_session_mirror(direct):
            return direct
        try:
            with os.scandir(root) as it:
                for ent in it:
                    if not ent.is_dir(follow_symlinks=False):
                        continue
                    cand = Path(ent.path) / name
                    if is_full_session_mirror(cand):
                        return cand
        except OSError:
            return None
        return None

    @staticmethod
    def key(child_id: str, sub_id: str) -> str:
        """Dict key: child session id, else subagent id."""
        return child_id or sub_id

    @staticmethod
    def bag(event: TraceEvent) -> dict[str, JsonValue]:
        """Tool bag from a spawn/finish bookend."""
        raw = event.raw_input.raw() if isinstance(event.raw_input, ToolInputBag) else {}
        return raw if isinstance(raw, dict) else {}

    @classmethod
    def empty(cls, *, child_id: str, sub_id: str) -> SubagentRun:
        """Bare running row for *child_id* / *sub_id*."""
        return cls(
            subagent_id=sub_id,
            child_session_id=child_id,
            child_path=None,
            subagent_type="",
            description="",
            status="running",
            parent_turn_index=None,
            parent_prompt_id="",
            spawn_event_index=None,
            finish_event_index=None,
            duration_ms=None,
            tool_calls=None,
            turns=None,
            tokens_used=None,
            output_preview="",
        )

    @classmethod
    def spawn_from_content(cls, content: str) -> dict[str, str]:
        """Parse a ``spawned type: desc`` dump line."""
        text = (content or "").strip()
        if not text.lower().startswith("spawned "):
            return {}
        rest = text[8:].strip()
        typ, sep, after = rest.partition(":")
        if not sep:
            return {"description": rest} if rest else {}
        return {"subagent_type": typ.strip(), "description": after.strip()}

    @classmethod
    def finish_from_content(cls, content: str) -> dict[str, int | str]:
        """Parse a ``subagent finished … duration_ms=`` dump line."""
        text = (content or "").strip()
        if not text.lower().startswith("subagent finished"):
            return {}
        rest = text[len("subagent finished") :].strip()
        out: dict[str, int | str] = {}
        if "duration_ms=" in rest:
            head, _, tail = rest.partition("duration_ms=")
            num = tail.split()[0] if tail else ""
            if num.isdigit():
                out["duration_ms"] = int(num)
            rest = head.strip()
        for part in rest.split():
            if "-" in part and len(part) >= 8:
                out["child_session_id"] = part
            elif part.isalpha():
                out["status"] = part
        return out

    @classmethod
    def apply_spawn(
        cls,
        by_key: dict[str, SubagentRun],
        ev: TraceEvent,
        parent_dir: Path,
        search_roots: list[Path] | None,
    ) -> None:
        """Merge a spawn bookend into *by_key*."""
        bag = cls.bag(ev)
        child = cls.first_str(bag, _SPAWN_CHILD_KEYS)
        sub_id = cls.first_str(bag, _SPAWN_ID_KEYS)
        key = cls.key(child, sub_id)
        if not key:
            return
        run = by_key.get(key) or cls.empty(child_id=child, sub_id=sub_id)
        run.child_session_id = child or run.child_session_id
        run.subagent_id = sub_id or run.subagent_id
        run.subagent_type = cls.first_str(bag, _SPAWN_TYPE_KEYS) or run.subagent_type
        run.description = cls.first_str(bag, _SPAWN_DESC_KEYS) or run.description
        run.parent_prompt_id = cls.first_str(bag, _SPAWN_PROMPT_KEYS) or run.parent_prompt_id
        run.spawn_event_index = ev.index
        if run.child_path is None and run.child_session_id:
            run.child_path = resolve_child_session_path(
                parent_dir, run.child_session_id, search_roots=search_roots
            )
        by_key[key] = run

    @classmethod
    def apply_finish(
        cls,
        by_key: dict[str, SubagentRun],
        ev: TraceEvent,
        parent_dir: Path,
        search_roots: list[Path] | None,
    ) -> None:
        """Merge a finish bookend into *by_key*."""
        bag = cls.bag(ev)
        child = cls.first_str(bag, _SPAWN_CHILD_KEYS)
        sub_id = cls.first_str(bag, _SPAWN_ID_KEYS)
        key = cls.key(child, sub_id)
        if not key:
            return
        run = by_key.get(key) or cls.empty(child_id=child, sub_id=sub_id)
        run.child_session_id = child or run.child_session_id
        run.subagent_id = sub_id or run.subagent_id
        raw_st = cls.first_str(bag, _FINISH_STATUS_KEYS)
        run.status = normalize_run_status(raw_st, finished=True)
        run.duration_ms = cls.first_int(bag, _FINISH_DUR_KEYS)
        run.tool_calls = cls.first_int(bag, _FINISH_TOOLS_KEYS)
        run.turns = cls.first_int(bag, _FINISH_TURNS_KEYS)
        run.tokens_used = cls.first_int(bag, _FINISH_TOKENS_KEYS)
        out = cls.first_str(bag, _FINISH_OUTPUT_KEYS)
        if out:
            run.output_preview = out[:240]
        run.finish_event_index = ev.index
        if run.child_path is None and run.child_session_id:
            run.child_path = resolve_child_session_path(
                parent_dir, run.child_session_id, search_roots=search_roots
            )
        by_key[key] = run

    @classmethod
    def merge_meta(
        cls,
        by_key: dict[str, SubagentRun],
        parent_dir: Path,
        search_roots: list[Path] | None,
    ) -> None:
        """Fill gaps from ``subagents/*/meta.json``."""
        sub_root = parent_dir / "subagents"
        if not sub_root.is_dir():
            return
        try:
            names = [ent.name for ent in os.scandir(sub_root) if ent.is_dir(follow_symlinks=False)]
        except OSError:
            return
        for name in names:
            meta = cls.read_meta(sub_root / name / "meta.json")
            child = cls.first_str(meta, _SPAWN_CHILD_KEYS) or name
            sub_id = cls.first_str(meta, _SPAWN_ID_KEYS) or name
            key = cls.key(child, sub_id)
            run = by_key.get(key) or cls.empty(child_id=child, sub_id=sub_id)
            run.child_session_id = child or run.child_session_id
            run.subagent_id = sub_id or run.subagent_id
            run.subagent_type = cls.first_str(meta, _SPAWN_TYPE_KEYS) or run.subagent_type
            run.description = cls.first_str(meta, _SPAWN_DESC_KEYS) or run.description
            if run.finish_event_index is None:
                raw_st = cls.first_str(meta, _FINISH_STATUS_KEYS)
                if raw_st:
                    run.status = normalize_run_status(raw_st, finished=raw_st.lower() != "running")
            if run.child_path is None:
                run.child_path = resolve_child_session_path(
                    parent_dir, run.child_session_id, search_roots=search_roots
                )
            by_key[key] = run

    @staticmethod
    def read_meta(path: Path) -> dict[str, JsonValue]:
        """``meta.json`` mapping, or empty when missing or not JSON."""
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def link_turn(
        self,
        segments: list[TurnSegment],
        turn_by_index: Mapping[int, int],
    ) -> None:
        """Set parent turn from prompt id or spawn event index."""
        needle = (self.parent_prompt_id or "").strip()
        if needle:
            for seg in segments:
                if seg.prompt_index is not None and str(seg.prompt_index) == needle:
                    self.parent_turn_index = seg.turn_index
                    return
        if self.spawn_event_index is not None:
            self.parent_turn_index = turn_by_index.get(self.spawn_event_index)

    @classmethod
    def from_overview(cls, row: JsonObject) -> SubagentRun:
        """Hydrate one ``session/overview`` subagent row."""
        path_s = json_as_str(row.get("childPath")).strip()
        return cls(
            subagent_id=json_as_str(row.get("subagentId")),
            child_session_id=json_as_str(row.get("childSessionId")),
            child_path=Path(path_s) if path_s else None,
            subagent_type=json_as_str(row.get("subagentType")),
            description=json_as_str(row.get("description")),
            status=json_as_str(row.get("status")) or "running",
            parent_turn_index=cls.optional_int(row.get("turnIndex")),
            parent_prompt_id=json_as_str(row.get("parentPromptId")),
            spawn_event_index=cls.optional_int(row.get("spawnEventIndex")),
            finish_event_index=cls.optional_int(row.get("finishEventIndex")),
            duration_ms=cls.optional_int(row.get("durationMs")),
            tool_calls=cls.optional_int(row.get("toolCalls")),
            turns=cls.optional_int(row.get("turns")),
            tokens_used=cls.optional_int(row.get("tokensUsed")),
            output_preview=json_as_str(row.get("outputPreview")),
        )


def spawn_fields(update: Mapping[str, JsonValue]) -> dict[str, str]:
    """Pick spawn identity fields from a Grok ``subagent_spawned`` update."""
    return {
        "child_session_id": SubagentRun.first_str(update, _SPAWN_CHILD_KEYS),
        "subagent_id": SubagentRun.first_str(update, _SPAWN_ID_KEYS),
        "parent_prompt_id": SubagentRun.first_str(update, _SPAWN_PROMPT_KEYS),
        "subagent_type": SubagentRun.first_str(update, _SPAWN_TYPE_KEYS),
        "description": SubagentRun.first_str(update, _SPAWN_DESC_KEYS),
    }


def finish_fields(update: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Pick finish stats from a Grok ``subagent_finished`` update."""
    return {
        "child_session_id": SubagentRun.first_str(update, _SPAWN_CHILD_KEYS),
        "subagent_id": SubagentRun.first_str(update, _SPAWN_ID_KEYS),
        "status": SubagentRun.first_str(update, _FINISH_STATUS_KEYS),
        "duration_ms": SubagentRun.first_int(update, _FINISH_DUR_KEYS),
        "tool_calls": SubagentRun.first_int(update, _FINISH_TOOLS_KEYS),
        "turns": SubagentRun.first_int(update, _FINISH_TURNS_KEYS),
        "tokens_used": SubagentRun.first_int(update, _FINISH_TOKENS_KEYS),
        "output": SubagentRun.first_str(update, _FINISH_OUTPUT_KEYS),
    }


def normalize_run_status(raw: str, *, finished: bool) -> str:
    """Map harness status to ``running`` | ``completed`` | ``cancelled`` | ``failed``."""
    s = (raw or "").strip().lower()
    if "cancel" in s:
        return "cancelled"
    if any(tok in s for tok in ("fail", "error", "abort")):
        return "failed"
    if finished or s in {"done", "complete", "completed", "success", "ok"}:
        return "completed"
    return "running"


def subagent_runs_for_session(
    parent_dir: Path,
    events: list[TraceEvent],
    segments: list[TurnSegment],
    turn_by_index: Mapping[int, int],
    *,
    search_roots: list[Path] | None = None,
) -> list[SubagentRun]:
    """Build turn-linked runs from spawn/finish events and ``subagents/*/meta.json``."""
    by_key: dict[str, SubagentRun] = {}
    for ev in events:
        if ev.event_type == "subagent_spawned":
            SubagentRun.apply_spawn(by_key, ev, parent_dir, search_roots)
        elif ev.event_type == "subagent_finished":
            SubagentRun.apply_finish(by_key, ev, parent_dir, search_roots)
    SubagentRun.merge_meta(by_key, parent_dir, search_roots)
    for run in by_key.values():
        run.link_turn(segments, turn_by_index)
    return sorted(
        by_key.values(),
        key=lambda r: (
            r.parent_turn_index if r.parent_turn_index is not None else 10_000,
            r.spawn_event_index if r.spawn_event_index is not None else 10_000,
            r.child_session_id or r.subagent_id,
        ),
    )


def subagent_run_mapping(run: SubagentRun) -> JsonObject:
    """Wire mapping for one run (turns / get / overview)."""
    path = str(run.child_path) if run.child_path is not None else ""
    return {
        "subagentId": run.subagent_id,
        "childSessionId": run.child_session_id,
        "childPath": path,
        "openable": run.openable,
        "subagentType": run.subagent_type,
        "description": run.description,
        "status": run.status,
        "turnIndex": run.parent_turn_index,
        "parentPromptId": run.parent_prompt_id,
        "spawnEventIndex": run.spawn_event_index,
        "finishEventIndex": run.finish_event_index,
        "durationMs": run.duration_ms,
        "toolCalls": run.tool_calls,
        "turns": run.turns,
        "tokensUsed": run.tokens_used,
        "outputPreview": run.output_preview,
    }


def event_subagent_fields(event: TraceEvent) -> JsonObject:
    """Child id / stats for a spawn or finish timeline row."""
    bag = event.raw_input.raw() if isinstance(event.raw_input, ToolInputBag) else {}
    if not isinstance(bag, dict):
        return {}
    out: JsonObject = {}
    child = SubagentRun.first_str(bag, _SPAWN_CHILD_KEYS)
    if child:
        out["childSessionId"] = child
    sid = SubagentRun.first_str(bag, _SPAWN_ID_KEYS)
    if sid:
        out["subagentId"] = sid
    typ = SubagentRun.first_str(bag, _SPAWN_TYPE_KEYS)
    if typ:
        out["subagentType"] = typ
    desc = SubagentRun.first_str(bag, _SPAWN_DESC_KEYS)
    if desc:
        out["description"] = desc
    st = SubagentRun.first_str(bag, _FINISH_STATUS_KEYS)
    if st:
        out["subagentStatus"] = normalize_run_status(
            st, finished=event.event_type == "subagent_finished"
        )
    dur = SubagentRun.first_int(bag, _FINISH_DUR_KEYS)
    if dur is not None:
        out["durationMs"] = dur
    tools = SubagentRun.first_int(bag, _FINISH_TOOLS_KEYS)
    if tools is not None:
        out["toolCalls"] = tools
    tokens = SubagentRun.first_int(bag, _FINISH_TOKENS_KEYS)
    if tokens is not None:
        out["tokensUsed"] = tokens
    return out


def event_child_session_id(event: TraceEvent) -> str:
    """Child session id from a spawn/finish bookend, or empty."""
    bag = event.raw_input.raw() if isinstance(event.raw_input, ToolInputBag) else {}
    child = SubagentRun.first_str(bag, _SPAWN_CHILD_KEYS) if isinstance(bag, dict) else ""
    if child:
        return child
    dumped = SubagentRun.finish_from_content(event.content or "").get("child_session_id")
    return dumped if isinstance(dumped, str) else ""


@dataclass(frozen=True)
class SubagentInspect:
    """What the operator should read on a spawn/finish bookend."""

    kind: str
    description: str
    status: str
    duration_s: float | None


def subagent_inspect(
    event: TraceEvent,
    *,
    mate: TraceEvent | None = None,
    run: SubagentRun | None = None,
) -> SubagentInspect:
    """Identity from the spawn/run; outcome from the finish bookend."""
    bags: list[Mapping[str, JsonValue]] = []
    if run is not None:
        bags.append(
            {
                "subagentType": run.subagent_type,
                "description": run.description,
                "status": run.status,
                "duration_ms": run.duration_ms,
            }
        )
    for ev in (event, mate):
        if ev is None:
            continue
        bag = ev.raw_input.raw() if isinstance(ev.raw_input, ToolInputBag) else {}
        if isinstance(bag, dict) and bag:
            bags.append(bag)
        if ev.event_type == "subagent_spawned":
            rec = SubagentRun.spawn_from_content(ev.content or "")
            if rec:
                bags.append(rec)
        elif ev.event_type == "subagent_finished":
            rec_f = SubagentRun.finish_from_content(ev.content or "")
            if rec_f:
                bags.append(rec_f)
    kind = ""
    description = ""
    status = ""
    duration_s: float | None = None
    for item in bags:
        if not kind:
            kind = SubagentRun.first_str(item, _SPAWN_TYPE_KEYS)
        if not description:
            description = SubagentRun.first_str(item, _SPAWN_DESC_KEYS)
        if not status:
            raw_st = SubagentRun.first_str(item, _FINISH_STATUS_KEYS)
            if raw_st:
                status = normalize_run_status(
                    raw_st, finished=event.event_type == "subagent_finished"
                )
        if duration_s is None:
            ms = SubagentRun.first_int(item, _FINISH_DUR_KEYS)
            if ms is not None and ms >= 0:
                duration_s = ms / 1000.0
    return SubagentInspect(kind=kind, description=description, status=status, duration_s=duration_s)


def subagent_duration_seconds(event: TraceEvent) -> float | None:
    """Harness run length for a finish bookend, or None."""
    bag = event.raw_input.raw() if isinstance(event.raw_input, ToolInputBag) else {}
    ms = SubagentRun.first_int(bag, _FINISH_DUR_KEYS) if isinstance(bag, dict) else None
    if ms is None:
        dumped = SubagentRun.finish_from_content(event.content or "").get("duration_ms")
        ms = dumped if isinstance(dumped, int) else None
    if isinstance(ms, int) and ms >= 0:
        return ms / 1000.0
    return None


def subagent_list_preview(
    event_type: str,
    raw: Mapping[str, JsonValue] | None,
    content: str = "",
    *,
    max_chars: int = 80,
) -> str:
    """Summary text for a spawn/finish bookend (not the dump line)."""
    bag = as_json_object(raw) if isinstance(raw, dict) else {}
    if event_type == "subagent_spawned":
        fields = spawn_fields(bag)
        if not fields["subagent_type"] and not fields["description"]:
            rec = SubagentRun.spawn_from_content(content)
            fields = {**fields, **{k: v for k, v in rec.items() if v}}
        text = fields.get("description") or fields.get("subagent_type") or ""
        return clip_preview(text, max_chars)
    if event_type == "subagent_finished":
        desc = json_as_str(bag.get("description")).strip()
        if desc:
            return clip_preview(desc, max_chars)
        fin = finish_fields(bag)
        status = json_as_str(fin.get("status"))
        if not status:
            dumped_fin = SubagentRun.finish_from_content(content)
            raw_status = dumped_fin.get("status")
            status = raw_status if isinstance(raw_status, str) else ""
        if status:
            return clip_preview(normalize_run_status(status, finished=True), max_chars)
        return ""
    return ""


def as_run_list(value: JsonValue) -> list[JsonObject]:
    """Coerce a wire ``subagentRuns`` value to mappings."""
    if not isinstance(value, list):
        return []
    out: list[JsonObject] = []
    for item in value:
        if isinstance(item, dict):
            out.append(as_json_object(item))
    return out


def subagent_runs_from_overview(overview: JsonObject) -> list[SubagentRun]:
    """Hydrate turn-linked runs from a ``session/overview`` payload."""
    turns = overview.get("turns")
    if not isinstance(turns, dict):
        return []
    out: list[SubagentRun] = []
    for row in as_run_list(turns.get("subagentRuns")):
        out.append(SubagentRun.from_overview(row))
    return out


def subagent_runs_for_view(
    overview: JsonObject | None,
    parent_dir: Path,
    events: list[TraceEvent],
    segments: list[TurnSegment],
    turn_by_index: Mapping[int, int],
) -> list[SubagentRun]:
    """Owner payload when attached; disk merge when inspecting offline."""
    if overview is not None:
        return subagent_runs_from_overview(overview)
    return subagent_runs_for_session(parent_dir, events, segments, turn_by_index)
