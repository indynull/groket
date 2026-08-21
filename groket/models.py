"""Core data models and extension contracts.

Serialised models use Pydantic v2 (:class:`Flag`, :class:`EvalRun`). Hot-path
trace types use dataclasses (:class:`ToolCall`, :class:`TraceEvent`). Shared
aliases (:data:`JsonValue`, :data:`JsonObject`, :class:`ChatMessage`,
:data:`ToolInput`) define JSON/YAML boundaries.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import NotRequired, TypedDict

from pydantic import BaseModel, Field

from .utils import fmt_context_usage, fmt_duration, strip_control_chars

#
# Recursive JSON types use PEP 695 ``type`` statements (Python 3.12+), same
# pattern as coredis :data:`coredis.typing.JsonType` / ``ResponseType`` in
# ``_py_312_typing.py``. On 3.11 and older, recursive unions need ``Any`` in
# containers; we require 3.13+ so the real recursive form is always used.
# Order matters for readability: scalars, then containers, then ``None``.

type JsonPrimitive = str | int | float | bool | None

#: Any JSON-serialisable value (object / array / scalar / null).
type JsonValue = str | int | float | bool | dict[str, JsonValue] | list[JsonValue] | None

#: JSON object (string keys). Prefer this over ``dict[str, JsonValue]`` at APIs
#: so call sites share one alias (invariance still applies to concrete dicts —
#: use :func:`as_json_object` when assembling heterogeneous mappings).
type JsonObject = dict[str, JsonValue]

# Tool inputs use :class:`ToolInputBag` (alias :data:`ToolInput`).


def json_as_str(value: JsonValue | None, default: str = "") -> str:
    """Coerce a JSON value to ``str``.

    :param value: Value from JSON/YAML or tool input.
    :param default: Used when *value* is ``None``.
    :returns: String form (containers use :func:`json.dumps`).
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(value)


def json_as_int(value: JsonValue | None, default: int = 0) -> int:
    """Coerce a JSON value to ``int``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def json_as_float(value: JsonValue | None, default: float = 0.0) -> float:
    """Coerce a JSON value to ``float``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def json_as_bool(value: JsonValue | None, default: bool = False) -> bool:
    """Coerce a JSON value to ``bool``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def json_as_str_list(value: JsonValue | None, default: Sequence[str] | None = None) -> list[str]:
    """Coerce a JSON value to ``list[str]``."""
    if value is None:
        return list(default or ())
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [json_as_str(item) for item in value]
    return list(default or ())


def json_as_object(value: JsonValue | None) -> JsonObject:
    """Coerce a JSON value to a mapping (empty if not a dict)."""
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {}


def json_as_list(value: JsonValue | None) -> list[JsonValue]:
    """Coerce a JSON value to a list (empty if not a list)."""
    if isinstance(value, list):
        return list(value)
    return []


def json_as_mapping_list(value: JsonValue | None) -> list[JsonObject]:
    """Coerce a JSON value to a list of objects (skip non-dicts)."""
    return [json_as_object(item) for item in json_as_list(value) if isinstance(item, dict)]


def json_value_from_unknown(value: object) -> JsonValue:
    """Best-effort coerce an arbitrary Python value into :data:`JsonValue`.

    Used when assembling audit/stats dicts so mypy accepts them as
    :data:`JsonObject` (plain ``dict[str, str]`` is invariant and will not
    type-check as ``dict[str, JsonValue]``).
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): json_value_from_unknown(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value_from_unknown(v) for v in value]
    return str(value)


def as_json_object(data: Mapping[str, object]) -> JsonObject:  # JSON boundary coerce
    """Build a :data:`JsonObject` from a heterogeneous mapping (audit helpers)."""
    return {str(k): json_value_from_unknown(v) for k, v in data.items()}


class ParamBag:
    """Typed accessors for rule YAML ``params`` (JSON-shaped open key set).

    Detectors receive this instead of a bare mapping so call sites use
    :meth:`as_str` / :meth:`as_int` / … instead of untyped ``.get`` soup.
    Supports ``key in params`` via :meth:`__contains__`.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, JsonValue] | None = None) -> None:
        self._data: JsonObject = {str(k): v for k, v in dict(data or {}).items()}

    @classmethod
    def ensure(cls, params: ParamBag | Mapping[str, JsonValue] | None) -> ParamBag:
        """Return *params* as :class:`ParamBag` (wrap mappings)."""
        if isinstance(params, ParamBag):
            return params
        return cls(params)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self._data

    def raw(self) -> JsonObject:
        """Return a shallow copy of the underlying mapping."""
        return dict(self._data)

    def get(self, key: str, default: JsonValue | None = None) -> JsonValue | None:
        """Return the raw JSON value for *key*, or *default*."""
        if key in self._data:
            return self._data[key]
        return default

    def has(self, key: str) -> bool:
        return key in self._data

    def keys(self) -> Iterator[str]:
        return iter(self._data.keys())

    def values(self) -> Iterator[JsonValue]:
        return iter(self._data.values())

    def items(self) -> Iterator[tuple[str, JsonValue]]:
        return iter(self._data.items())

    def as_str(self, key: str, default: str = "") -> str:
        return json_as_str(self.get(key), default)

    def as_str_opt(self, key: str) -> str | None:
        if key not in self._data:
            return None
        return json_as_str(self._data[key])

    def as_int(self, key: str, default: int = 0) -> int:
        return json_as_int(self.get(key), default)

    def as_int_opt(self, key: str) -> int | None:
        if key not in self._data:
            return None
        return json_as_int(self._data[key])

    def as_float(self, key: str, default: float = 0.0) -> float:
        return json_as_float(self.get(key), default)

    def as_bool(self, key: str, default: bool = False) -> bool:
        return json_as_bool(self.get(key), default)

    def as_str_list(self, key: str, default: Sequence[str] | None = None) -> list[str]:
        return json_as_str_list(self.get(key), default)

    def as_str_dict(self, key: str) -> dict[str, str]:
        obj = json_as_object(self.get(key))
        return {k: json_as_str(v) for k, v in obj.items()}

    def mapping(self, key: str) -> ParamBag:
        return ParamBag(json_as_object(self.get(key)))

    def as_int_list(self, key: str, default: Sequence[int] | None = None) -> list[int]:
        raw = self.get(key)
        if raw is None:
            return list(default or ())
        if isinstance(raw, list):
            return [json_as_int(item) for item in raw]
        return list(default or ())


class ToolInputBag(ParamBag):
    """Tool / MCP argument bag with the same accessors as :class:`ParamBag`."""


type ToolInput = ToolInputBag


class ChatContentBlock(TypedDict, total=False):
    """One block inside a multimodal chat ``content`` list."""

    type: str
    text: str


class ChatMessage(TypedDict, total=False):
    """One line from ``chat_history.jsonl`` (role + content, optional extras)."""

    role: str
    content: str | list[ChatContentBlock]
    # Harness may attach timestamps / ids; keep open for forward-compat keys.
    timestamp: NotRequired[int | float | str]
    id: NotRequired[str]


type ChatHistory = Sequence[ChatMessage]


class FlagVerdict(str, Enum):
    BAD = "bad"
    ACCEPTABLE = "acceptable"
    GOOD = "good"
    NEEDS_REVIEW = "needs_review"


@dataclass
class ToolCall:
    """A single tool invocation extracted from a trace."""

    call_id: str
    tool_name: str
    raw_input: ToolInput
    timestamp: int | None = None
    result_content: str = ""
    is_error: bool = False
    update_index: int = 0
    exit_code: int | None = None
    signal: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw_input, ToolInputBag):
            data = self.raw_input if isinstance(self.raw_input, dict) else {}
            object.__setattr__(self, "raw_input", ToolInputBag(data))

    def input_str(self, key: str, default: str = "", *, max_len: int | None = None) -> str:
        """Return tool input field *key* as a string.

        :param key: Input argument name.
        :param default: Fallback when missing or null.
        :param max_len: Optional maximum length.
        :returns: Coerced string value.
        """
        bag = (
            self.raw_input
            if isinstance(self.raw_input, ToolInputBag)
            else ToolInputBag(self.raw_input if isinstance(self.raw_input, dict) else {})
        )
        text = bag.as_str(key, default)
        return text if max_len is None else text[:max_len]

    def inputs(self) -> ToolInputBag:
        """Return tool arguments as :class:`ToolInputBag`."""
        if isinstance(self.raw_input, ToolInputBag):
            return self.raw_input
        if isinstance(self.raw_input, dict):
            return ToolInputBag(self.raw_input)
        return ToolInputBag()


@dataclass
class TraceEvent:
    """A single event in the conversation timeline."""

    index: int
    event_type: str  # Grok sessionUpdate / events.jsonl type (see groket.event_types)
    timestamp: int | None = None
    content: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    raw_input: ToolInput = field(default_factory=ToolInputBag)
    is_error: bool = False
    update_index: int = 0
    prompt_index: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw_input, ToolInputBag):
            data = self.raw_input if isinstance(self.raw_input, dict) else {}
            object.__setattr__(self, "raw_input", ToolInputBag(data))

    @property
    def time_str(self) -> str:
        if self.timestamp is None:
            return ""
        try:
            dt = datetime.fromtimestamp(self.timestamp, tz=UTC)
            return dt.strftime("%H:%M:%S")
        except (OSError, ValueError):
            return str(self.timestamp)

    @property
    def type_label(self) -> str:
        """Grok-aligned event kind for tables (sessionUpdate / events type)."""
        from .event_types import type_label as grok_type_label

        return grok_type_label(self.event_type)

    @property
    def summary_line(self) -> str:
        if self.event_type == "system":
            text = self.content if isinstance(self.content, str) else str(self.content)
            one = text.replace("\n", " ").strip()
            return (one[:100] + "…") if len(one) > 100 else (one or "system prompt")
        from . import event_types as et

        if self.event_type in et.SESSION_CHROME_TYPES - {et.SYSTEM}:
            text = self.content if isinstance(self.content, str) else str(self.content)
            return text[:80].replace("\n", " ") or self.event_type
        if self.event_type == et.TOOL_CALL:
            bag = (
                self.raw_input
                if isinstance(self.raw_input, ToolInputBag)
                else ToolInputBag(self.raw_input if isinstance(self.raw_input, dict) else {})
            )

            def _s(key: str, n: int | None = None) -> str:
                text = bag.as_str(key)
                return text if n is None else text[:n]

            # MCP / meta-tool: outer tool name + inner id (preview humanizes both).
            if bag.has("tool_input") or (
                self.tool_name in ("use_tool", "use-tool") and bag.has("tool_name")
            ):
                inner = _s("tool_name")
                server = _s("server_name") or _s("server")
                if server and inner:
                    inner = f"{server}__{inner}"
                ti = bag.raw().get("tool_input")
                val = ""
                if isinstance(ti, dict) and ti:
                    for key in (
                        "query",
                        "libraryName",
                        "libraryId",
                        "url",
                        "path",
                        "prompt",
                        "name",
                    ):
                        if key in ti and ti[key] is not None:
                            val = str(ti[key]).replace("\n", " ").strip()
                            if len(val) > 48:
                                val = val[:45] + "…"
                            break
                bits = [p for p in (self.tool_name, inner, val) if p]
                return " ".join(bits) or self.tool_name
            if bag.has("command"):
                return f"{self.tool_name} $ {_s('command', 60)}"
            if bag.has("pattern"):
                return f"{self.tool_name} /{_s('pattern', 30)}/ in {_s('path') or '.'}"
            if bag.has("target_file"):
                return f"{self.tool_name} {_s('target_file')}"
            if bag.has("file_path"):
                return f"{self.tool_name} {_s('file_path')}"
            if bag.has("prompt"):
                return f'{self.tool_name} "{_s("prompt", 40)}..."'
            if bag.has("query"):
                return f'{self.tool_name} "{_s("query", 40)}..."'
            return self.tool_name
        elif self.event_type == et.TOOL_CALL_UPDATE:
            rlen = len(self.content)
            snippet = strip_control_chars(self.content[:200])[:60].replace("\n", " ")
            return f"{self.tool_name} ({rlen} chars) {snippet}"
        else:
            text = self.content if isinstance(self.content, str) else json.dumps(self.content)
            text = strip_control_chars(text[:200])
            return text[:80].replace("\n", " ")


class Flag(BaseModel):
    """A user flag on a trace event."""

    event_index: int
    verdict: FlagVerdict = FlagVerdict.BAD
    description: str = ""
    event_type: str = ""
    tool_name: str = ""
    tool_call_id: str = ""
    timestamp: int | None = None
    created_at: str = ""


@dataclass
class SessionMeta:
    """Metadata about a session from summary.json and signals.json."""

    session_id: str
    session_dir: Path
    model_id: str = "unknown"
    # From launch token ``model:effort``, run-dir slug, or run config.toml when set.
    reasoning_effort: str = ""
    title: str = ""
    summary_text: str = ""
    created_at: str = ""
    updated_at: str = ""
    num_messages: int = 0
    num_events: int = 0
    duration_seconds: float = 0
    tool_call_count: int = 0
    tool_failure_count: int = 0
    error_count: int = 0
    doom_loop_warnings: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    # From signals.json context meter (session snapshot, not per-turn series).
    context_window_usage_pct: int | None = None
    context_tokens_used: int | None = None
    context_window_tokens: int | None = None
    compaction_count: int = 0
    total_tokens_before_compaction: int = 0
    git_repo: str = ""
    git_branch: str = ""
    # From summary ``head_commit`` (for fork restore / checkout after clone).
    git_commit: str = ""
    task_id: str = ""
    run_id: str = ""
    # Catalog origin: ``work`` (Docker/eval under work traces) or ``host`` (native Grok).
    origin: str = "work"
    # From events.jsonl runtime telemetry
    # success | error | cancelled | interrupted | running | ""
    turn_outcome: str = ""
    loop_count: int = 0
    # From signals.json ``turnCount`` (host/live aggregates; timeline may be a tail).
    turn_count: int = 0

    @property
    def model_display(self) -> str:
        """Model id with effort when present (``model:effort``)."""
        mid = (self.model_id or "").strip() or "unknown"
        eff = (self.reasoning_effort or "").strip()
        if eff:
            return f"{mid}:{eff}"
        return mid

    @property
    def label(self) -> str:
        return self.title or self.session_id[:20]

    @property
    def duration_str(self) -> str:
        return fmt_duration(self.duration_seconds)

    @property
    def has_context_usage(self) -> bool:
        """True when signals.json provided context window telemetry."""
        return (
            self.context_window_usage_pct is not None
            or self.context_tokens_used is not None
            or (self.context_window_tokens is not None and self.context_window_tokens > 0)
        )

    @property
    def context_usage_str(self) -> str:
        """Full context fill label, e.g. ``35% (178,996 / 500,000)``."""
        return fmt_context_usage(
            self.context_window_usage_pct,
            self.context_tokens_used,
            self.context_window_tokens,
        )

    @property
    def context_usage_compact(self) -> str:
        """Narrow list/table label, e.g. ``35% 179k/500k``."""
        return fmt_context_usage(
            self.context_window_usage_pct,
            self.context_tokens_used,
            self.context_window_tokens,
            compact=True,
        )

    @property
    def turn_in_progress(self) -> bool:
        oc = (self.turn_outcome or "").lower().replace(" ", "_")
        return oc in (
            "running",
            "in_progress",
            "pending",
            "awaiting_follow_up",
            "ending",
            "finishing",
        )

    @property
    def turn_failed(self) -> bool:
        oc = (self.turn_outcome or "").lower().replace(" ", "_")
        if not oc or oc in ("success", "ok", "completed", "complete"):
            return False
        # In-progress / interactive wait / shutdown are not failures
        if oc in (
            "running",
            "in_progress",
            "pending",
            "awaiting_follow_up",
            "ending",
            "finishing",
        ):
            return False
        return True

    def list_status_label(self) -> str:
        """Main session list Turn column (short labels — narrow column).

        Values: ``running`` | ``ending`` | ``awaiting`` | ``cancelled`` |
        ``complete`` | ``—``.
        """
        oc = (self.turn_outcome or "").strip().lower().replace(" ", "_")
        if oc in ("ending", "finishing"):
            return "ending"
        if oc == "awaiting_follow_up":
            return "awaiting"
        if oc in ("running", "in_progress", "pending") or (not oc and self.turn_in_progress):
            return "running"
        if oc in ("cancelled", "canceled", "interrupted", "aborted"):
            return "cancelled"
        if oc in ("success", "ok", "completed", "complete"):
            return "complete"
        if self.turn_failed or oc in ("error", "failed", "failure", "timeout"):
            # Single-turn failures surface as cancelled on the home list.
            return "cancelled"
        if not oc:
            return "—"
        return "complete"


class EvalRun(BaseModel):
    """A single evaluation run: prompt + config + resulting sessions."""

    run_id: str
    prompt: str
    setup_instructions: str = ""
    docker_image: str = "fully-loaded"
    models: list[str] = Field(default_factory=list)
    parallelism: int = 1
    repo_url: str = ""
    repo_branch: str = ""
    # Host directory bind-mounted as /workspace (no clone); empty when unused.
    repo_path: str = ""
    status: str = "pending"  # pending, running, completed, failed
    created_at: str = ""
