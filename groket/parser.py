"""Trace parser — reads session directories into structured data."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import Future
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from .bounded_cache import BoundedCache
from .constants import (
    INCOMPLETE_STALE_SECONDS,
    INTERRUPTED_MARKER_FILENAME,
    LIST_RUNTIME_CACHE_MAXSIZE,
    RUNTIME_MARKERS_CACHE_MAXSIZE,
    SYSTEM_PROMPT_CACHE_MAXSIZE,
    TIMELINE_CACHE_MAX_ENV,
    TIMELINE_CACHE_MAXSIZE,
)
from .core_scan import keep_updates_line as keep_updates_line_native
from .core_scan import keep_updates_line_py
from .models import (
    ChatMessage,
    JsonObject,
    JsonValue,
    SessionMeta,
    ToolCall,
    ToolInput,
    ToolInputBag,
    TraceEvent,
    as_json_object,
    json_as_str,
)
from .native import find_sessions as native_find_sessions
from .native import skip_dir_name
from .paths import RUN_PREFIXES, is_run_dir_name, strip_run_prefix
from .tool_display import web_search_from_raw_output

logger = logging.getLogger(__name__)

try:
    import orjson as _orjson

    def json_loads(data: str | bytes) -> JsonValue:
        """Parse one JSON document (orjson on the timeline hot path)."""
        if isinstance(data, str):
            raw = _orjson.loads(data.encode("utf-8"))
        else:
            raw = _orjson.loads(data)
        return cast(JsonValue, raw)

except ImportError:  # pragma: no cover — orjson is a hard dependency

    def json_loads(data: str | bytes) -> JsonValue:
        if isinstance(data, bytes):
            return cast(JsonValue, json.loads(data.decode("utf-8")))
        return cast(JsonValue, json.loads(data))


def json_object_line(line: str | bytes) -> JsonObject | None:
    """Parse a JSONL line that must be an object; None if invalid or not a map."""
    try:
        val = json_loads(line)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not isinstance(val, dict):
        return None
    return as_json_object(val)


# Live timeline cache: session key -> (stamp, finalized events, updates scan).
# Stamp excludes signals.json so context-meter heartbeats do not force a
# multi‑MB re-read of updates.jsonl; sizes catch growth within one mtime tick.
type TimelineStamp = tuple[float, int, int, int]


class _UpdatesScanState:
    """Incremental cursor over ``updates.jsonl`` (pre-marker, pre-finalize)."""

    __slots__ = (
        "byte_pos",
        "events",
        "idx",
        "line_no",
        "pending_tools",
        "result_by_call",
        "size",
    )

    def __init__(
        self,
        *,
        size: int = 0,
        byte_pos: int = 0,
        line_no: int = 0,
        events: list[TraceEvent] | None = None,
        idx: int = 0,
        pending_tools: dict[str, TraceEvent] | None = None,
        result_by_call: dict[str, int] | None = None,
    ) -> None:
        self.size = size
        self.byte_pos = byte_pos
        self.line_no = line_no
        self.events = events if events is not None else []
        self.idx = idx
        self.pending_tools = pending_tools if pending_tools is not None else {}
        self.result_by_call = result_by_call if result_by_call is not None else {}


_timeline_cache: BoundedCache[tuple[TimelineStamp, list[TraceEvent], _UpdatesScanState | None]] = (
    BoundedCache(TIMELINE_CACHE_MAXSIZE, env_var=TIMELINE_CACHE_MAX_ENV)
)
# In-flight parse keyed by session cache_key only (not stamp). Incremental
# scans mutate shared ``_UpdatesScanState`` in place; only one body may run
# per session at a time. Waiters re-check stamp after the flight finishes.
_timeline_inflight: dict[str, Future[list[TraceEvent]]] = {}
_timeline_inflight_lock = threading.Lock()
# events.jsonl path -> (mtime_ns, size, markers, turn_outcome, loop_count)
_runtime_markers_cache: BoundedCache[tuple[int, int, list[TraceEvent], str, int]] = BoundedCache(
    RUNTIME_MARKERS_CACHE_MAXSIZE
)
# events.jsonl path -> (mtime_ns, size, turn_outcome, loop_count, open_after_completed)
_list_runtime_cache: BoundedCache[tuple[int, int, str, int, bool]] = BoundedCache(
    LIST_RUNTIME_CACHE_MAXSIZE
)
_LIST_MARKER_NEEDLES = (
    '"turn_started"',
    '"turn_ended"',
    '"loop_started"',
    '"session_error"',
    '"turn_error"',
    '"fatal_error"',
    '"type":"error"',
    '"type": "error"',
)

# Wrapper tool ids whose real target lives in ``rawInput.tool_name`` (MCP bridge).
# Compare with :func:`_tool_id_key` so ``use-tool`` / ``UseTool`` match ``use_tool``.
_MCP_WRAPPER_TOOL_IDS = frozenset(
    {
        "use_tool",
        "call_mcp",
        "call_mcp_tool",
        "mcp_tool",
    }
)

# Finite map from observed Grok *human titles* → stable tool ids (case-insensitive;
# trailing ``:`` stripped before lookup). Do not guess unknown titles.
_HUMAN_TITLE_TO_TOOL_ID: dict[str, str] = {
    "web search": "web_search",
}


def _tool_id_key(name: str) -> str:
    """Case-fold and unify ``-`` / ``_`` for wrapper-id membership checks."""
    return (name or "").strip().lower().replace("-", "_")


def normalize_tool_id(name: str) -> str:
    """Stable tool id for timeline storage and usage attribution.

    - Known human titles (e.g. ``Web search:``) map via :data:`_HUMAN_TITLE_TO_TOOL_ID`.
    - Otherwise the string is kept as-is (host ``grep``, MCP ``server__method``).
    """
    s = (name or "").strip()
    if not s:
        return "unknown"
    key = s.lower().rstrip(":").strip()
    return _HUMAN_TITLE_TO_TOOL_ID.get(key, s)


def resolve_tool_display_name(title: str, raw_input: ToolInput | None = None) -> str:
    """Resolve the tool id stored on a timeline event.

    Contract:

    1. If ``title`` is an MCP wrapper (``use_tool`` / ``call_mcp`` / …) and
       ``rawInput`` has ``tool_name`` or ``name``, use that nested id.
    2. Otherwise use ``title``.
    3. Run the result through :func:`normalize_tool_id` (human-title map only).

    :param title: Grok update / tool_call title field.
    :param raw_input: Structured tool arguments when present (dict or bag).
    :returns: Canonical tool id for :attr:`~groket.models.TraceEvent.tool_name`.
    """
    title_s = (title or "").strip() or "unknown"
    bag = ToolInputBag.ensure(raw_input)
    nested_s = (bag.as_str("tool_name") or bag.as_str("name")).strip()

    if nested_s and _tool_id_key(title_s) in {_tool_id_key(w) for w in _MCP_WRAPPER_TOOL_IDS}:
        return normalize_tool_id(nested_s)
    return normalize_tool_id(title_s)


def _as_epoch_ts(value: str | int | float | bool | None) -> int | None:
    """Coerce event timestamps to epoch seconds."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _parse_runtime_ts(ev: dict) -> int | None:
    """Epoch seconds from an events.jsonl row (ISO string or numeric)."""
    ts_raw = ev.get("ts") or ev.get("timestamp")
    if ts_raw is None:
        return None
    if isinstance(ts_raw, (int, float)):
        v = int(ts_raw)
        if v > 10_000_000_000:
            v = v // 1000
        return v
    try:
        dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, TypeError, OverflowError):
        return None


def parse_runtime_markers(session_dir: Path) -> tuple[list[TraceEvent], str, int]:
    """Parse events.jsonl for turn/session markers.

    Returns (marker_events_without_index, turn_outcome, loop_count).
    Marker events are not yet indexed; caller assigns indices.

    Cached by ``events.jsonl`` ``(mtime_ns, size)`` so live light refresh does
    not re-scan multi‑100KB events files on every tick when only updates.jsonl
    changed (or when meta is re-probed with an unchanged events file).
    """
    events_file = session_dir / "events.jsonl"
    try:
        if not events_file.is_file():
            return [], "", 0
        st = events_file.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        size = int(st.st_size)
        cache_key = str(events_file.resolve())
    except OSError:
        return [], "", 0

    cached = _runtime_markers_cache.get(cache_key)
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[2], cached[3], cached[4]

    markers: list[TraceEvent] = []
    turn_outcome = ""
    loop_count = 0
    started: list[TraceEvent] = []
    ended: list[TraceEvent] = []

    try:
        with open(events_file) as f:
            for line_no, line in enumerate(f):
                ev = json_object_line(line)

                if ev is None:
                    continue
                et = ev.get("type") or ""
                ts = _parse_runtime_ts(ev)

                if et == "turn_started":
                    mid = ev.get("model_id") or ""
                    tn = ev.get("turn_number")
                    parts = ["turn started"]
                    if tn is not None:
                        parts.append(f"turn_number={tn}")
                    if mid:
                        parts.append(f"model={mid}")
                    started.append(
                        TraceEvent(
                            index=0,
                            event_type="turn_started",
                            timestamp=ts,
                            content="  ".join(parts),
                            update_index=line_no,
                        )
                    )

                elif et == "turn_ended":
                    outcome = str(ev.get("outcome") or ev.get("status") or "unknown")
                    turn_outcome = outcome
                    is_err = outcome.lower() not in (
                        "",
                        "success",
                        "ok",
                        "completed",
                        "complete",
                    )
                    extra = []
                    for k in ("error", "message", "reason", "detail"):
                        if ev.get(k):
                            extra.append(f"{k}={ev[k]}")
                    body = f"turn ended  outcome={outcome}"
                    if extra:
                        body += "  " + "  ".join(str(x) for x in extra)
                    ended.append(
                        TraceEvent(
                            index=0,
                            event_type="turn_ended",
                            timestamp=ts,
                            content=body,
                            is_error=is_err,
                            update_index=line_no,
                        )
                    )

                elif et == "loop_started":
                    try:
                        li = ev.get("loop_index", 0)
                        loop_count = max(
                            loop_count,
                            int(li) + 1 if isinstance(li, (int, float, str)) else 0,
                        )
                    except (TypeError, ValueError):
                        pass

                elif et in ("error", "session_error", "turn_error", "fatal_error"):
                    msg = ev.get("message") or ev.get("error") or ev.get("detail") or str(ev)[:200]
                    if not turn_outcome:
                        turn_outcome = "error"
                    ended.append(
                        TraceEvent(
                            index=0,
                            event_type="session_error",
                            timestamp=ts,
                            content=f"{et}: {msg}"[:500],
                            is_error=True,
                            update_index=line_no,
                        )
                    )
    except OSError:
        return [], "", 0

    markers = started + ended
    _runtime_markers_cache[cache_key] = (mtime_ns, size, markers, turn_outcome, loop_count)
    return markers, turn_outcome, loop_count


def _line_may_be_list_marker(line: str) -> bool:
    """True when *line* might be a turn/loop/error marker (skip fat tool JSON)."""
    return any(needle in line for needle in _LIST_MARKER_NEEDLES)


def _apply_list_runtime_event(
    ev: JsonObject,
    turn_outcome: str,
    loop_count: int,
    open_starts: int,
    ended: int,
) -> tuple[str, int, int, int]:
    """Fold one events.jsonl object into list-status counters."""
    et = ev.get("type") or ""
    if et == "turn_started":
        return turn_outcome, loop_count, open_starts + 1, ended
    if et == "turn_ended":
        return (
            str(ev.get("outcome") or ev.get("status") or "unknown"),
            loop_count,
            max(0, open_starts - 1),
            ended + 1,
        )
    if et == "loop_started":
        try:
            li = ev.get("loop_index", 0)
            n = int(li) + 1 if isinstance(li, (int, float, str)) else 0
        except (TypeError, ValueError):
            n = 0
        return turn_outcome, max(loop_count, n), open_starts, ended
    if et in ("error", "session_error", "turn_error", "fatal_error") and not turn_outcome:
        return "error", loop_count, open_starts, ended
    return turn_outcome, loop_count, open_starts, ended


def _list_runtime_status(session_dir: Path) -> tuple[str, int, bool]:
    """Home-list turn status from ``events.jsonl`` in one pass.

    Returns ``(turn_outcome, loop_count, open_after_completed)``. Does not
    build marker events. Lines that cannot be turn/loop/error markers are
    not JSON-parsed (those are the multi‑MB tool payloads).
    """
    events_file = session_dir / "events.jsonl"
    try:
        if not events_file.is_file():
            return "", 0, False
        st = events_file.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        size = int(st.st_size)
        cache_key = str(events_file.resolve())
    except OSError:
        return "", 0, False

    cached = _list_runtime_cache.get(cache_key)
    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
        return cached[2], cached[3], cached[4]

    turn_outcome = ""
    loop_count = 0
    open_starts = 0
    ended = 0
    try:
        with events_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not _line_may_be_list_marker(line):
                    continue
                ev = json_object_line(line)
                if ev is None:
                    continue
                turn_outcome, loop_count, open_starts, ended = _apply_list_runtime_event(
                    ev, turn_outcome, loop_count, open_starts, ended
                )
    except OSError:
        return "", 0, False

    open_after = ended > 0 and open_starts > 0
    _list_runtime_cache[cache_key] = (mtime_ns, size, turn_outcome, loop_count, open_after)
    return turn_outcome, loop_count, open_after


def _session_has_turn_gate(session_dir: Path) -> bool:
    """True when this session's traces volume has a ``.groket-turn`` directory."""
    from .session.turn_gate import turn_gate_dirs_for_session

    return bool(turn_gate_dirs_for_session(session_dir))


def _stringify_tool_payload(value: object) -> str:
    """Turn a tool output field into display text (str, MCP wrappers, JSON)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        # MCP / plugin wrappers: prefer success then error then nested content.
        for key in (
            "OkayOutput",
            "okay_output",
            "ErrorOutput",
            "error_output",
            "output",
            "content",
            "text",
            "FileContent",
            "Content",
            "stdout",
        ):
            if key in value:
                inner = _stringify_tool_payload(value.get(key))
                if inner:
                    return inner
        try:
            return json.dumps(value, indent=2)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, list):
        parts = [_stringify_tool_payload(item) for item in value]
        return "\n".join(p for p in parts if p)
    return str(value)


def _extract_raw_output_text(raw_output: object) -> str:
    """Body text from ``tool_call_update.rawOutput`` (host tools + MCP).

    Host tools often set ``output_for_prompt`` / ``output`` / ``FileContent``.
    MCP tools set ``type=MCP`` with ``output`` as ``{OkayOutput: ...}`` (or
    ``ErrorOutput``) and leave ``content`` empty — we must read that path or
    the timeline shows no result for context7 / playwright / etc.
    """
    if not isinstance(raw_output, dict):
        return _stringify_tool_payload(raw_output) if raw_output is not None else ""
    for key in (
        "output_for_prompt",
        "output",
        "content",
        "FileContent",
        "Content",
        "stdout",
        "stderr",
    ):
        if key not in raw_output:
            continue
        text = _stringify_tool_payload(raw_output.get(key))
        if text:
            return text
    action_text, _query, _url = web_search_from_raw_output(raw_output)
    return action_text


def _merge_search_into_bag(bag: ToolInputBag, query: str, url: str = "") -> ToolInputBag:
    """Copy *bag* and set ``query`` / ``url`` when the host left rawInput empty."""
    data = bag.raw()
    changed = False
    if query.strip() and not json_as_str(data.get("query")).strip():
        data["query"] = query.strip()
        changed = True
    if url.strip() and not json_as_str(data.get("url")).strip():
        data["url"] = url.strip()
        changed = True
    return ToolInputBag(data) if changed else bag


def _apply_tool_result_meta(tc: ToolCall, update: dict) -> None:
    """Apply rawOutput metadata and error status from a tool_call_update."""
    raw_output = update.get("rawOutput")
    if isinstance(raw_output, dict):
        body = _extract_raw_output_text(raw_output)
        _action_body, query, page_url = web_search_from_raw_output(raw_output)
        if query or page_url:
            tc.raw_input = _merge_search_into_bag(tc.inputs(), query, page_url)
        if body:
            ofp = (
                raw_output.get("output_for_prompt")
                if isinstance(raw_output.get("output_for_prompt"), str)
                else ""
            )
            if (
                ofp
                and ofp.startswith("exit:")
                and not (tc.result_content or "").startswith("exit:")
            ):
                tc.result_content = ofp
            elif not tc.result_content or len(body) >= len(tc.result_content):
                tc.result_content = body
        exit_code = raw_output.get("exit_code")
        signal = raw_output.get("signal")
        if exit_code is not None:
            tc.exit_code = exit_code
        if signal:
            tc.signal = signal

    is_error = update.get("isError")
    status = update.get("status", "")
    if is_error is True or status == "failed":
        tc.is_error = True

    # exit_code=1 is often benign (grep no-match, diff differences);
    # only treat exit_code >= 2 or signals as errors for terminal commands.
    if not tc.is_error and tc.tool_name == "run_terminal_command":
        if tc.signal:
            tc.is_error = True
        elif tc.exit_code is not None and tc.exit_code not in (0, 1):
            tc.is_error = True


def parse_tool_calls(session_dir: Path) -> list[ToolCall]:
    """Parse updates.jsonl to extract tool calls with their results."""
    updates_file = session_dir / "updates.jsonl"
    if not updates_file.exists():
        return []

    tool_calls: dict[str, ToolCall] = {}
    call_order: list[ToolCall] = []

    with open(updates_file) as f:
        for idx, line in enumerate(f):
            event = json_object_line(line)

            if event is None:
                continue

            params = event.get("params")
            update_raw = params.get("update") if isinstance(params, dict) else None
            update: JsonObject = as_json_object(update_raw) if isinstance(update_raw, dict) else {}
            event_type = str(update.get("sessionUpdate") or "")
            timestamp = _as_epoch_ts(
                event.get("timestamp")  # type: ignore[arg-type]  # JsonValue; narrowed below
                if isinstance(event.get("timestamp"), (str, int, float))
                or event.get("timestamp") is None
                else None
            )

            if event_type == "tool_call":
                call_id = json_as_str(update.get("toolCallId"))
                raw_input = update.get("rawInput", {})
                tool_name = resolve_tool_display_name(
                    json_as_str(update.get("title")) or "unknown",
                    ToolInputBag(raw_input) if isinstance(raw_input, dict) else None,
                )
                tc = ToolCall(
                    call_id=call_id,
                    tool_name=tool_name,
                    raw_input=ToolInputBag(raw_input)
                    if isinstance(raw_input, dict)
                    else ToolInputBag(),
                    timestamp=timestamp,
                    update_index=idx,
                )
                tool_calls[call_id] = tc
                call_order.append(tc)

            elif event_type == "tool_call_update":
                call_id = json_as_str(update.get("toolCallId"))
                if call_id in tool_calls:
                    tc = tool_calls[call_id]
                    tc.result_content += _extract_tool_update_text(update.get("content", ""))
                    _apply_tool_result_meta(tc, update)

    return call_order


def _extract_tool_update_text(content) -> str:
    """Pull display text out of a tool_call_update content payload."""
    if not content:
        return ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            inner = item.get("content", {})
            if isinstance(inner, dict):
                parts.append(inner.get("text", "") or "")
            elif isinstance(inner, str):
                parts.append(inner)
        return "".join(parts)
    if isinstance(content, str):
        return content
    return ""


# Identity map: event_type == Grok sessionUpdate (1:1).
_MESSAGE_TYPE_MAP = {
    "user_message_chunk": "user_message_chunk",
    "agent_message_chunk": "agent_message_chunk",
    "agent_thought_chunk": "agent_thought_chunk",
}


def _extract_message_text(content) -> str:
    """Normalize a message chunk content payload to plain text."""
    if isinstance(content, dict) and content.get("type") == "text":
        return content.get("text", "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.append(json.dumps(item))
        return "".join(parts)
    if isinstance(content, str):
        return content
    return json.dumps(content)


def _message_prompt_index(update: JsonObject) -> int | None:
    meta = update.get("_meta")
    if not isinstance(meta, dict):
        return None
    value = meta.get("promptIndex")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _coalesce_tool_result(
    update: dict,
    ts: int | str | None,
    line_no: int,
    events: list[TraceEvent],
    idx: int,
    pending_tools: dict[str, TraceEvent],
    result_by_call: dict[str, int],
) -> int:
    """Coalesce streaming tool_call_update rows into a single tool_result event.

    Returns the (possibly incremented) event index.
    """
    epoch_ts = _as_epoch_ts(ts) if not isinstance(ts, int) else ts
    call_id = update.get("toolCallId", "")
    is_error = update.get("isError")
    status = update.get("status", "")
    result_text = _extract_tool_update_text(update.get("content", ""))
    raw_output = update.get("rawOutput")
    # MCP and some host tools put the body only in rawOutput (content is null).
    if not result_text:
        result_text = _extract_raw_output_text(raw_output)
    search_body, search_query, search_url = web_search_from_raw_output(raw_output)
    if search_body and (not result_text or result_text.strip() in ("", "{}")):
        result_text = search_body
    failed = is_error is True or status == "failed"
    terminal = failed or status in ("completed", "failed")

    if not result_text and not failed and not terminal:
        return idx

    tool_name = ""
    call_input = ToolInputBag()
    if call_id in pending_tools:
        pending = pending_tools[call_id]
        tool_name = pending.tool_name
        if search_query or search_url:
            pending.raw_input = _merge_search_into_bag(
                pending.raw_input
                if isinstance(pending.raw_input, ToolInputBag)
                else ToolInputBag(),
                search_query,
                search_url,
            )
        call_input = (
            pending.raw_input if isinstance(pending.raw_input, ToolInputBag) else ToolInputBag()
        )
        if failed:
            pending.is_error = True

    if call_id in result_by_call:
        ev = events[result_by_call[call_id]]
        if result_text and (len(result_text) >= len(ev.content or "") or terminal):
            ev.content = result_text
        if epoch_ts is not None:
            ev.timestamp = epoch_ts
        ev.update_index = line_no
        if failed:
            ev.is_error = True
        if tool_name and not ev.tool_name:
            ev.tool_name = tool_name
        if call_input.raw() and not ev.raw_input.raw():
            ev.raw_input = call_input
    elif result_text or failed:
        ev = TraceEvent(
            index=idx,
            event_type="tool_call_update",
            timestamp=epoch_ts,
            content=result_text,
            tool_call_id=call_id,
            tool_name=tool_name,
            raw_input=call_input,
            is_error=failed,
            update_index=line_no,
        )
        result_by_call[call_id] = len(events)
        events.append(ev)
        idx += 1
    return idx


def _fork_event_signature(ev: TraceEvent) -> tuple[str, str, str, str]:
    """Stable identity for matching re-stamped parent replay in a fork child."""
    return (
        ev.event_type,
        ev.tool_name or "",
        ev.tool_call_id or "",
        (ev.content or "")[:240],
    )


def _fork_is_lifecycle_chrome(ev: TraceEvent) -> bool:
    """Turn lifecycle rows that sit between matched parent work in a replay."""
    return ev.event_type in ("turn_started", "turn_ended", "turn_completed") or (
        ev.event_type in ("session", "session_error")
        and (
            "turn started" in (ev.content or "").lower()
            or "turn ended" in (ev.content or "").lower()
        )
    )


def _fork_child_timeline_suffix(
    parent_tl: list[TraceEvent],
    child_tl: list[TraceEvent],
) -> list[TraceEvent]:
    """Select child events that continue after inherited parent history.

    Grok ``--fork-session`` often writes the child ``updates.jsonl`` as a
    **re-timestamped full replay** of the parent conversation plus the new
    continuation, with a single ``turn_number=N`` marker wrapping all of it.
    The seeded parent timeline is authoritative for history; only the child's
    **new** work (after the parent content prefix) is appended.
    """
    child = [e for e in child_tl if e.event_type != "system"]
    if not child:
        return []

    parent_work = [
        _fork_event_signature(e)
        for e in parent_tl
        if e.event_type != "system" and not _fork_is_lifecycle_chrome(e)
    ]
    if not parent_work:
        return child

    # Walk the child in order: consume a sequential match of parent work,
    # skipping lifecycle chrome that Grok injects around the restamped block.
    pi = 0
    split_at = 0
    for i, ev in enumerate(child):
        if _fork_is_lifecycle_chrome(ev):
            if pi < len(parent_work):
                split_at = i + 1
            continue
        if pi < len(parent_work) and _fork_event_signature(ev) == parent_work[pi]:
            pi += 1
            split_at = i + 1
            continue
        # First non-matching substantive event — start of the fork continuation.
        split_at = i
        break
    else:
        # Exhausted child while still matching (or only chrome after match).
        if pi >= len(parent_work):
            return child[split_at:] if split_at < len(child) else []
        return []

    if pi == 0:
        # No parent prefix — child is only the new branch (or unrelated).
        return child

    return child[split_at:]


def _merge_fork_parent_timeline(
    session_dir: Path,
    local: list[TraceEvent],
) -> list[TraceEvent]:
    """Prepend seeded parent timeline for forked child sessions."""
    from .session.resume import fork_parent_session_dir

    parent = fork_parent_session_dir(session_dir)
    if parent is None:
        return local
    parent_tl = parse_timeline(parent)
    if not parent_tl:
        return local
    suffix = _fork_child_timeline_suffix(parent_tl, local)
    if not suffix:
        # Parent-only: reindex copies so cache entries stay independent.
        return [replace(e, index=i) for i, e in enumerate(parent_tl)]
    # Parent then continuation in order — do not re-sort by timestamp, or a
    # restamped child prefix (if any slips through) could interleave wrongly.
    merged = [replace(e, index=i) for i, e in enumerate([*parent_tl, *suffix])]
    return merged


def _timeline_stamp_for(session_dir: Path) -> tuple[str, TimelineStamp]:
    """Return ``(cache_key, stamp)`` for *session_dir* (includes fork parent)."""
    sd = Path(session_dir)
    cache_key = str(sd.resolve()) if sd.exists() else str(sd)
    from .session.resume import fork_parent_session_dir

    parent = fork_parent_session_dir(sd)
    stamp = session_timeline_stamp(sd)
    if parent is not None:
        parent_stamp = session_timeline_stamp(parent)
        stamp = (
            max(stamp[0], parent_stamp[0]),
            stamp[1],
            stamp[2],
            stamp[3],
        )
    return cache_key, stamp


def _parse_timeline_body(
    session_dir: Path, cache_key: str, stamp: TimelineStamp
) -> list[TraceEvent]:
    """Run the uncached parse path and store the result (caller owns single-flight)."""
    sd = Path(session_dir)
    cached = _timeline_cache.get(cache_key)
    # Another flight may have filled the cache while we waited for the lock.
    if cached is not None and cached[0] == stamp:
        return cached[1]

    prev_scan = cached[2] if cached is not None else None
    scan = _scan_updates_jsonl(sd, prev_scan)
    runtime_markers, _outcome, _loops = parse_runtime_markers(session_dir)

    events = list(scan.events)
    idx = scan.idx
    for m in runtime_markers:
        m.index = idx
        events.append(m)
        idx += 1

    out = _prepend_system_prompt(session_dir, _finalize_timeline_order(events))
    out = _merge_fork_parent_timeline(sd, out)
    _timeline_cache[cache_key] = (stamp, out, scan)
    return out


def parse_timeline(session_dir: Path) -> list[TraceEvent]:
    """Parse updates.jsonl (+ events.jsonl turn markers) into a linear timeline.

    Streaming ``tool_call_update`` events (e.g. every ``CC`` line from a long
    ``make``) are coalesced into a **single** ``tool_result`` row per
    ``toolCallId``.  Earlier versions appended one row per update, which made
    builds look like hundreds of separate terminal runs in the TUI.

    Runtime markers from ``events.jsonl`` (``turn_started`` / ``turn_ended`` /
    errors) are merged with update rows and **ordered by timestamp** (then
    original index) so multi-turn sessions do not pile all starts at the top
    and all ends at the bottom.

    Fork-resume children also inherit the seeded parent timeline (see
    :func:`~groket.session.resume.fork_parent_session_dir`): Grok often writes
    only the new turn into the child session dir.

    Results are cached by :func:`session_timeline_stamp` (not signals.json) so
    live context heartbeats do not re-read multi‑MB ``updates.jsonl``. When the
    file only grows, new lines are scanned incrementally.

    Concurrent callers for the same session **join one in-flight parse**
    (single-flight) so the control owner does not thrash the same multi‑MB file
    in parallel under HUD overview+timeline+poll pile-ups. Flight is per
    session path (not stamp) because the incremental scan mutates shared
    scan state; after a flight completes, waiters re-check the stamp.
    """
    # Loop: join any in-flight body, then re-check stamp (file may have grown).
    while True:
        cache_key, stamp = _timeline_stamp_for(session_dir)
        cached = _timeline_cache.get(cache_key)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        owner = False
        with _timeline_inflight_lock:
            fut = _timeline_inflight.get(cache_key)
            if fut is None:
                fut = Future()
                _timeline_inflight[cache_key] = fut
                owner = True

        if not owner:
            # Wait for the owner, then re-check cache/stamp (may need another pass).
            fut.result()
            continue

        try:
            out = _parse_timeline_body(session_dir, cache_key, stamp)
        except Exception as exc:
            if not fut.done():
                fut.set_exception(exc)
            raise
        else:
            fut.set_result(out)
            return out
        finally:
            with _timeline_inflight_lock:
                if _timeline_inflight.get(cache_key) is fut:
                    del _timeline_inflight[cache_key]


# Streaming tool_call_update lines often *are* the multi‑100MB file (cumulative
# shell output). Skip full JSON parse unless the line looks terminal.
# Needles live in :mod:`groket.core_scan` (Python twin + optional Rust leaf).


def _keep_updates_line(line: bytes) -> bool:
    """True when *line* should be JSON-parsed."""
    native = keep_updates_line_native(line)
    if native is not None:
        return native
    return keep_updates_line_py(line)


def _scan_updates_jsonl(session_dir: Path, previous: _UpdatesScanState | None) -> _UpdatesScanState:
    """Parse ``updates.jsonl`` into pre-marker events, resuming when the file grows.

    :param session_dir: Session directory.
    :param previous: Prior scan cursor (mutated in place on growth).
    :returns: Scan state (may be *previous* when only appending).
    """
    updates_file = session_dir / "updates.jsonl"
    if not updates_file.is_file():
        return _UpdatesScanState()

    try:
        size = int(updates_file.stat().st_size)
    except OSError:
        return _UpdatesScanState()

    # Truncation / rewrite — full rescan.
    if previous is None or size < previous.byte_pos:
        state = _UpdatesScanState()
        start_pos = 0
    elif size == previous.byte_pos:
        return previous
    else:
        state = previous
        start_pos = previous.byte_pos

    try:
        with open(updates_file, "rb") as f:
            if start_pos:
                f.seek(start_pos)
            line_no = state.line_no
            while True:
                pos = f.tell()
                line = f.readline()
                if not line:
                    break
                # Incomplete trailing line: leave for the next growth tick.
                if not line.endswith(b"\n") and f.tell() >= size:
                    f.seek(pos)
                    break
                _consume_updates_line(line, line_no, state)
                line_no += 1
            state.byte_pos = f.tell()
            state.line_no = line_no
            state.size = size
    except OSError:
        return _UpdatesScanState()
    return state


def _consume_updates_line(line: bytes, line_no: int, state: _UpdatesScanState) -> None:
    """Apply one complete ``updates.jsonl`` line into *state*."""
    if not _keep_updates_line(line):
        return

    raw = json_object_line(line)
    if raw is None:
        return

    params = raw.get("params")
    update_raw = params.get("update") if isinstance(params, dict) else None
    update: JsonObject = as_json_object(update_raw) if isinstance(update_raw, dict) else {}
    etype = str(update.get("sessionUpdate") or "")
    ts_raw = raw.get("timestamp")
    if ts_raw is None:
        ts_raw = raw.get("ts")
    ts = _as_epoch_ts(ts_raw if isinstance(ts_raw, (str, int, float)) else None)

    events = state.events
    idx = state.idx
    pending_tools = state.pending_tools
    result_by_call = state.result_by_call

    if etype in _MESSAGE_TYPE_MAP:
        content = _extract_message_text(update.get("content", ""))
        mapped = _MESSAGE_TYPE_MAP[etype]
        prompt_index = _message_prompt_index(update)
        # Agent thought/message streams are append-only deltas → one row.
        # User chunks are different: Grok often re-emits the *full* draft
        # (partial then complete). Merge only when one is a prefix of the other
        # so the detail pane shows the finished prompt. Keep distinct when the
        # texts diverge (background-task chrome immediately before a real prompt).
        if events and events[-1].event_type == mapped:
            prev = events[-1]
            if mapped != "user_message_chunk":
                prev.content += content
                if ts is not None:
                    prev.timestamp = ts
                prev.update_index = line_no
            else:
                old = prev.content or ""
                if not content:
                    pass
                elif not old or content.startswith(old) or old.startswith(content):
                    if len(content) >= len(old):
                        prev.content = content
                    if ts is not None:
                        prev.timestamp = ts
                    prev.update_index = line_no
                    if prompt_index is not None:
                        prev.prompt_index = prompt_index
                else:
                    events.append(
                        TraceEvent(
                            index=idx,
                            event_type=mapped,
                            timestamp=ts,
                            content=content,
                            update_index=line_no,
                            prompt_index=prompt_index,
                        )
                    )
                    state.idx = idx + 1
        else:
            events.append(
                TraceEvent(
                    index=idx,
                    event_type=mapped,
                    timestamp=ts,
                    content=content,
                    update_index=line_no,
                    prompt_index=prompt_index,
                )
            )
            state.idx = idx + 1

    elif etype == "tool_call":
        call_id = json_as_str(update.get("toolCallId"))
        raw_input = update.get("rawInput", {})
        tool_name = resolve_tool_display_name(
            json_as_str(update.get("title")) or "unknown",
            ToolInputBag(raw_input) if isinstance(raw_input, dict) else None,
        )
        ev = TraceEvent(
            index=idx,
            event_type="tool_call",
            timestamp=ts,
            tool_name=tool_name,
            tool_call_id=call_id,
            raw_input=ToolInputBag(raw_input) if isinstance(raw_input, dict) else ToolInputBag(),
            update_index=line_no,
        )
        events.append(ev)
        pending_tools[call_id] = ev
        state.idx = idx + 1

    elif etype == "tool_call_update":
        state.idx = _coalesce_tool_result(
            update,
            ts,
            line_no,
            events,
            idx,
            pending_tools,
            result_by_call,
        )

    elif etype == "plan":
        content = json.dumps(update.get("todos", update), indent=2)[:500]
        events.append(
            TraceEvent(
                index=idx,
                event_type="plan",
                timestamp=ts,
                content=content,
                update_index=line_no,
            )
        )
        state.idx = idx + 1

    elif etype in (
        "task_backgrounded",
        "task_completed",
        "turn_completed",
        "current_mode_update",
        "retry_state",
    ):
        bits: list[str] = [etype]
        for key in (
            "tool_call_id",
            "task_id",
            "command",
            "cwd",
            "prompt_id",
            "mode",
            "state",
        ):
            val = update.get(key)
            if val is not None and str(val).strip():
                bits.append(f"{key}={val}")
        snap = update.get("task_snapshot")
        if isinstance(snap, dict) and snap:
            bits.append(json.dumps(snap)[:400])
        events.append(
            TraceEvent(
                index=idx,
                event_type=etype,
                timestamp=ts,
                content="  ".join(str(b) for b in bits),
                tool_call_id=json_as_str(update.get("tool_call_id")),
                update_index=line_no,
            )
        )
        state.idx = idx + 1

    elif etype == "subagent_spawned":
        desc = update.get("description", "")
        agent_type = update.get("subagentType", "")
        events.append(
            TraceEvent(
                index=idx,
                event_type="subagent_spawned",
                timestamp=ts,
                update_index=line_no,
                content=f"Spawned {agent_type}: {desc}",
            )
        )
        state.idx = idx + 1

    elif etype == "subagent_finished":
        events.append(
            TraceEvent(
                index=idx,
                event_type="subagent_finished",
                timestamp=ts,
                update_index=line_no,
                content="Subagent finished",
            )
        )
        state.idx = idx + 1


def _is_turn_started_marker(ev: TraceEvent) -> bool:
    if ev.event_type == "turn_started":
        return True
    # Legacy timelines (pre Grok-aligned types)
    if ev.event_type in ("session", "session_error"):
        return "turn started" in (ev.content or "").lower()
    return False


def _is_turn_marker(ev: TraceEvent) -> bool:
    if ev.event_type in ("turn_started", "turn_ended", "turn_completed"):
        return True
    if ev.event_type in ("session", "session_error"):
        c = (ev.content or "").lower()
        return "turn started" in c or "turn ended" in c
    return False


def _is_substantive_timeline_event(ev: TraceEvent) -> bool:
    """True when the event is real agent activity (not a turn lifecycle marker)."""
    if _is_turn_marker(ev):
        return False
    return True


def _is_turn_ended_marker(ev: TraceEvent) -> bool:
    if ev.event_type == "turn_ended":
        return True
    if ev.event_type in ("session", "session_error"):
        return "turn ended" in (ev.content or "").lower()
    return False


def _drop_empty_turn_starts(events: list[TraceEvent]) -> list[TraceEvent]:
    """Remove ``turn started`` markers with no agent activity after them.

    Grok often emits a trailing ``turn_started`` when interactive mode opens the
    next turn (or the harness awaits follow-up) with no user/assistant/tools yet.
    That shows up as a stray final \"turn started\" on otherwise single-turn
    timelines. Keep starts that bracket real work. Keep a sole open
    ``turn_started`` when the session has not ended any turn yet (live / incomplete).
    """
    if not events:
        return events
    has_completed_turn = any(_is_turn_ended_marker(e) for e in events)
    drop: set[int] = set()
    n = len(events)
    for i, ev in enumerate(events):
        if not _is_turn_started_marker(ev):
            continue
        has_work = False
        for j in range(i + 1, n):
            nxt = events[j]
            if _is_turn_started_marker(nxt):
                break
            if _is_turn_marker(nxt):
                # ``turn ended`` with nothing in between → empty turn; drop start
                break
            if _is_substantive_timeline_event(nxt):
                has_work = True
                break
        if has_work:
            continue
        # Live session: only a start marker so far — keep it.
        if not has_completed_turn:
            continue
        drop.add(i)
    if not drop:
        return events
    return [ev for i, ev in enumerate(events) if i not in drop]


_system_prompt_cache: BoundedCache[tuple[float, str]] = BoundedCache(SYSTEM_PROMPT_CACHE_MAXSIZE)


def load_system_prompt_text(session_dir: Path) -> str:
    """Return ``system_prompt.txt`` for the session, or empty if missing.

    Cached by path + mtime so live timeline reloads do not re-read multi‑KB
    prompts on every poll.
    """
    fp = Path(session_dir) / "system_prompt.txt"
    if not fp.is_file():
        return ""
    key = str(fp)
    try:
        mtime = fp.stat().st_mtime
    except OSError:
        return ""
    hit = _system_prompt_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    try:
        text = fp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    _system_prompt_cache[key] = (mtime, text)
    return text


def _prepend_system_prompt(session_dir: Path, events: list[TraceEvent]) -> list[TraceEvent]:
    """Put the session system prompt first in the timeline when the file exists."""
    text = load_system_prompt_text(session_dir).strip()
    if not text:
        return events
    head = TraceEvent(index=0, event_type="system", content=text)
    out = [head, *events]
    for i, ev in enumerate(out):
        ev.index = i
    return out


def _finalize_timeline_order(events: list[TraceEvent]) -> list[TraceEvent]:
    """Sort by epoch timestamp (stable for ties), drop empty starts, reindex."""
    if not events:
        return events

    def _sort_key(ev: TraceEvent) -> tuple[int, int, int, int]:
        ts = ev.timestamp
        # Missing timestamps sort after dated events but keep relative order via index.
        ts_key = int(ts) if ts is not None else 2**62
        ui = ev.update_index if ev.update_index is not None else 10**9
        # Prefer turn_ended before turn_started on identical timestamps so a
        # completed turn closes before the next turn opens in the UI.
        kind = 1 if _is_turn_started_marker(ev) else 0
        return (ts_key, ui, kind, ev.index)

    ordered = sorted(events, key=_sort_key)
    ordered = _drop_empty_turn_starts(ordered)
    for i, ev in enumerate(ordered):
        ev.index = i
    return ordered


def parse_chat_history(session_dir: Path) -> list[ChatMessage]:
    """Parse chat_history.jsonl for message-level data."""
    chat_file = session_dir / "chat_history.jsonl"
    if not chat_file.exists():
        return []

    messages: list[ChatMessage] = []
    with open(chat_file) as f:
        for line in f:
            row = json_object_line(line)

            if row is None:
                continue
            if isinstance(row, dict):
                messages.append(row)  # type: ignore[arg-type]  # json.loads → dict; ChatMessage is TypedDict
    return messages


def extract_prompt(session_dir: Path) -> str:
    """Extract the user prompt from the chat history (the <user_query> block)."""
    messages = parse_chat_history(session_dir)
    for msg in messages:
        content = msg.get("content", "")
        texts: list[str] = []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "")
                    if isinstance(text, str):
                        texts.append(text)
        elif isinstance(content, str):
            texts = [content]
        for text in texts:
            start = text.find("<user_query>")
            if start < 0:
                continue
            end = text.find("</user_query>", start)
            if end < 0:
                continue
            return text[start + len("<user_query>") : end].strip()
    return ""


def _newest_mtime(session_dir: Path, names: tuple[str, ...]) -> float:
    """Newest mtime among named files under *session_dir* (0 if none)."""
    newest = 0.0
    for name in names:
        fp = session_dir / name
        try:
            if fp.is_file():
                newest = max(newest, fp.stat().st_mtime)
        except OSError:
            continue
    return newest


def session_timeline_mtime(session_dir: Path) -> float:
    """Newest mtime of artifacts that affect :func:`parse_timeline` output.

    Excludes ``signals.json`` / ``summary.json`` so live context-meter updates
    do not invalidate the timeline cache or force multi‑MB re-parses.
    """
    newest = _newest_mtime(
        session_dir,
        (
            "events.jsonl",
            "chat_history.jsonl",
            "updates.jsonl",
            "system_prompt.txt",
        ),
    )
    if newest <= 0:
        try:
            newest = session_dir.stat().st_mtime
        except OSError:
            pass
    return newest


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size) if path.is_file() else 0
    except OSError:
        return 0


def session_timeline_stamp(session_dir: Path) -> TimelineStamp:
    """Identity for timeline cache / live refresh (mtime + sizes).

    Sizes catch ``updates.jsonl`` growth when mtime resolution is coarse
    (same second). Signals/summary are intentionally omitted.
    """
    sd = Path(session_dir)
    return (
        session_timeline_mtime(sd),
        _file_size(sd / "updates.jsonl"),
        _file_size(sd / "events.jsonl"),
        _file_size(sd / "chat_history.jsonl") + _file_size(sd / "system_prompt.txt"),
    )


def session_trace_mtime(session_dir: Path) -> float:
    """Newest mtime among trace artifacts (0 if none).

    Includes ``signals.json`` / ``summary.json`` for session-list freshness.
    Prefer :func:`session_timeline_mtime` when deciding whether to re-parse
    the timeline.
    """
    newest = _newest_mtime(
        session_dir,
        (
            "events.jsonl",
            "chat_history.jsonl",
            "updates.jsonl",
            "summary.json",
            "signals.json",
        ),
    )
    if newest <= 0:
        try:
            newest = session_dir.stat().st_mtime
        except OSError:
            pass
    return newest


def updates_jsonl_size(session_dir: Path) -> int:
    """Byte size of ``updates.jsonl`` (0 if missing)."""
    fp = Path(session_dir) / "updates.jsonl"
    try:
        return int(fp.stat().st_size) if fp.is_file() else 0
    except OSError:
        return 0


_LIVE_TURN_OUTCOMES = frozenset(
    {"", "running", "in_progress", "pending", "awaiting_follow_up", "ending"}
)
_SUCCESS_TURN_OUTCOMES = frozenset({"success", "ok", "completed", "complete"})


def _normalize_terminal_turn_outcome(outcome: str) -> str:
    """Map a harness outcome to a stable terminal label, or ``""`` if still live."""
    oc = (outcome or "").strip().lower().replace(" ", "_")
    if not oc or oc in _LIVE_TURN_OUTCOMES:
        return ""
    if oc in _SUCCESS_TURN_OUTCOMES:
        return "completed"
    return oc


def _events_open_turn_after_completed(session_dir: Path) -> bool:
    """True when a later turn has started after at least one turn completed.

    That means the agent is **running** the next turn (not waiting for a
    follow-up prompt). Awaiting is only from the interactive turn gate.
    """
    events_file = session_dir / "events.jsonl"
    if not events_file.is_file():
        return False
    open_starts = 0
    ended = 0
    try:
        with events_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    et = (json_object_line(line) or {}).get("type")
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if et == "turn_started":
                    open_starts += 1
                elif et == "turn_ended":
                    open_starts = max(0, open_starts - 1)
                    ended += 1
    except OSError:
        return False
    return ended > 0 and open_starts > 0


def _events_turn_balance(session_dir: Path) -> tuple[int, str]:
    """Return ``(open_starts, last_turn_event_type)`` from ``events.jsonl``."""
    events_file = session_dir / "events.jsonl"
    if not events_file.is_file():
        return 0, ""
    open_starts = 0
    last_turn = ""
    try:
        with events_file.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    et = (json_object_line(line) or {}).get("type")
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if et == "turn_started":
                    open_starts += 1
                    last_turn = "turn_started"
                elif et == "turn_ended":
                    open_starts = max(0, open_starts - 1)
                    last_turn = "turn_ended"
    except OSError:
        return 0, ""
    return open_starts, last_turn


def _events_have_open_turn(session_dir: Path) -> bool:
    """True when the agent is mid-turn according to ``events.jsonl``.

    Delegates to :func:`~groket.session.turn_gate.events_have_open_turn` so
    gate lifecycle and parser share one harness-turn definition.
    """
    from .session.turn_gate import events_have_open_turn

    return events_have_open_turn(session_dir)


def _settle_idle_gate_outcome(marker_outcome: str) -> str:
    """Outcome when the gate looks live but the container is idle."""
    terminal = _normalize_terminal_turn_outcome(marker_outcome)
    return terminal or "completed"


def _gate_override_turn_outcome(session_dir: Path, marker_outcome: str) -> str | None:
    """Map interactive gate lifecycle onto turn_outcome, or ``None``.

    Gate ownership: :mod:`groket.session.turn_gate` (see
    :func:`~groket.session.turn_gate.lifecycle_state`). This only translates
    that lifecycle into list/browser outcomes.
    """
    from .session.turn_gate import lifecycle_state

    life = lifecycle_state(session_dir)
    if life == "done":
        return _settle_idle_gate_outcome(marker_outcome)
    if life == "ending":
        return "ending"
    if life == "awaiting_follow_up":
        return "awaiting_follow_up"
    if life == "running":
        traces_live = _infer_incomplete_turn_outcome(session_dir) == "running"
        turns_open = _events_have_open_turn(session_dir)
        if turns_open:
            if not traces_live:
                return _settle_idle_gate_outcome(marker_outcome)
            return "running"
        if traces_live:
            return "running"
        terminal = _normalize_terminal_turn_outcome(marker_outcome)
        if terminal:
            # Entrypoint left status=running after the last turn_ended.
            return terminal
        # Leftover last-turn flag with idle/closed harness: settle so the list
        # is not stuck on "running". Plain state=running with no markers is
        # still "about to start".
        from .session.turn_gate import final_turn_requested

        if final_turn_requested(session_dir):
            return _settle_idle_gate_outcome(marker_outcome)
        return "running"
    if life == "timeout":
        return "timeout"
    if life:
        # Unknown gate state: only force when a turn is actively open and live.
        traces_live = _infer_incomplete_turn_outcome(session_dir) == "running"
        turns_open = _events_have_open_turn(session_dir)
        if turns_open and traces_live:
            return "running"
        return None
    return None


def _infer_incomplete_turn_outcome(session_dir: Path) -> str:
    """Outcome when harness never wrote turn_ended.

    Live eval containers write traces incrementally; those sessions should show
    ``running``, not ``interrupted``. Only mark interrupted when an explicit
    marker exists, or trace data is present but has gone stale.
    """
    marker_path = session_dir / INTERRUPTED_MARKER_FILENAME
    if marker_path.is_file():
        return "interrupted"

    has_body = any(
        (session_dir / n).is_file() and (session_dir / n).stat().st_size > 200
        for n in ("events.jsonl", "chat_history.jsonl", "updates.jsonl")
    )
    if not has_body:
        return ""

    mtime = session_trace_mtime(session_dir)
    if mtime <= 0:
        return "interrupted"

    age = datetime.now(UTC).timestamp() - mtime
    if age < INCOMPLETE_STALE_SECONDS:
        return "running"
    return "interrupted"


def _git_remote_url(raw: object) -> str:
    """Normalize a ``git_remotes`` entry to an HTTPS/SSH clone URL string."""
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, dict):
        for key in ("url", "fetch", "push", "origin"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _load_summary(meta: SessionMeta, session_dir: Path) -> None:
    """Populate meta from summary.json."""
    summary_file = session_dir / "summary.json"
    if not summary_file.exists():
        return
    try:
        with open(summary_file) as f:
            data = json.load(f)
        meta.model_id = data.get("current_model_id", "unknown")
        meta.title = data.get("generated_title", "") or data.get("session_summary", "")
        meta.summary_text = data.get("session_summary", "")
        meta.created_at = data.get("created_at", "")
        meta.updated_at = data.get("updated_at", "")
        meta.num_messages = data.get("num_messages", 0)
        info = data.get("info", {})
        if isinstance(info, dict):
            meta.git_repo = str(info.get("git_repo_url") or meta.git_repo or "").strip()
            meta.git_branch = str(info.get("git_branch") or meta.git_branch or "").strip()
        # Top-level Grok fields (often present when info.* is empty).
        if not meta.git_branch:
            meta.git_branch = str(data.get("head_branch") or "").strip()
        if not meta.git_commit:
            meta.git_commit = str(data.get("head_commit") or "").strip()
        if not meta.git_repo:
            remotes = data.get("git_remotes")
            if isinstance(remotes, list):
                for item in remotes:
                    url = _git_remote_url(item)
                    if url:
                        meta.git_repo = url
                        break
    except (json.JSONDecodeError, KeyError, TypeError, OSError):
        pass


def _load_signals(meta: SessionMeta, session_dir: Path) -> None:
    """Populate meta from signals.json."""
    signals_file = session_dir / "signals.json"
    if not signals_file.exists():
        return
    try:
        with open(signals_file) as f:
            sig = json.load(f)
        meta.tool_call_count = sig.get("toolCallCount", 0)
        meta.tool_failure_count = sig.get("toolFailureCount", 0)
        meta.error_count = sig.get("errorCount", 0)
        meta.doom_loop_warnings = sig.get("doomLoopWarnings", 0)
        meta.duration_seconds = sig.get("sessionDurationSeconds", 0)
        meta.lines_added = sig.get("agentLinesAdded", 0)
        meta.lines_removed = sig.get("agentLinesRemoved", 0)
        usage = sig.get("contextWindowUsage")
        if isinstance(usage, bool):
            pass
        elif isinstance(usage, int):
            meta.context_window_usage_pct = max(0, usage)
        elif isinstance(usage, float):
            meta.context_window_usage_pct = max(0, int(usage))
        used = sig.get("contextTokensUsed")
        if isinstance(used, bool):
            pass
        elif isinstance(used, int):
            meta.context_tokens_used = max(0, used)
        elif isinstance(used, float):
            meta.context_tokens_used = max(0, int(used))
        window = sig.get("contextWindowTokens")
        if isinstance(window, bool):
            pass
        elif isinstance(window, int) and window > 0:
            meta.context_window_tokens = window
        elif isinstance(window, float) and window > 0:
            meta.context_window_tokens = int(window)
        compactions = sig.get("compactionCount")
        if isinstance(compactions, int) and compactions >= 0:
            meta.compaction_count = compactions
        elif isinstance(compactions, float) and compactions >= 0:
            meta.compaction_count = int(compactions)
        before = sig.get("totalTokensBeforeCompaction")
        if isinstance(before, int) and before >= 0:
            meta.total_tokens_before_compaction = before
        elif isinstance(before, float) and before >= 0:
            meta.total_tokens_before_compaction = int(before)
    except (json.JSONDecodeError, KeyError):
        pass


def _load_run_meta(meta: SessionMeta, session_dir: Path) -> None:
    """Populate meta from launch record, then legacy run.json / path hints.

    Prefer ``groket-launch.json`` on the traces volume (written at container
    start with the operator-selected model and effort). Older traces without
    that file fall back to ``run.json`` mapping and directory-name inference.
    """
    from .runs.launch_meta import apply_launch_meta, read_launch_meta

    launch = read_launch_meta(session_dir)
    if launch is not None:
        apply_launch_meta(meta, launch)

    run_json = session_dir / "run.json"
    if not run_json.exists():
        for ancestor in session_dir.parents:
            if is_run_dir_name(ancestor.name):
                run_json = ancestor / "run.json"
                break
            if ancestor.name == "traces":
                break

    if run_json.exists():
        try:
            with open(run_json) as f:
                run_data = json.load(f)
            if not meta.run_id:
                meta.run_id = str(run_data.get("run_id") or "")
            if not meta.task_id:
                meta.task_id = str(run_data.get("task_id") or "")
            if not meta.git_repo:
                meta.git_repo = run_data.get("repo_url", "")
            if not meta.git_branch:
                meta.git_branch = run_data.get("repo_branch", "")
            if launch is None:
                resolved = _model_from_run_json(session_dir, run_data)
                if resolved:
                    from .runs.batch import split_model_effort

                    mid, eff = split_model_effort(resolved)
                    meta.model_id = mid or resolved
                    if eff:
                        meta.reasoning_effort = eff
        except (json.JSONDecodeError, KeyError):
            pass

    if launch is not None:
        return

    # Legacy traces: infer from groket-* parent slug / config.toml.
    if not meta.model_id or meta.model_id in ("unknown", "v9", "grok-build"):
        inferred = _model_from_run_parent(session_dir)
        if inferred and inferred not in ("unknown",) and inferred != meta.model_id:
            if meta.model_id in ("unknown", "v9") or len(inferred) > len(meta.model_id):
                meta.model_id = inferred

    if not meta.reasoning_effort:
        meta.reasoning_effort = _reasoning_effort_from_run_dir(session_dir)
    if not meta.reasoning_effort:
        meta.reasoning_effort = _reasoning_effort_from_run_config(session_dir)


def load_session_meta_list(
    session_dir: Path,
    *,
    origin: str = "work",
) -> SessionMeta:
    """Metadata for the sessions home list.

    Reads ``summary.json`` / ``signals.json`` and one cheap ``events.jsonl``
    pass for turn status. Does not parse ``updates.jsonl``, does not build
    marker events, and does not consult the turn gate unless a gate directory
    exists (eval sessions). Missing ``events.jsonl`` is fine.
    """
    origin_key = (origin or "work").strip().lower() or "work"
    meta = SessionMeta(session_id=session_dir.name, session_dir=session_dir)
    _load_summary(meta, session_dir)
    _load_signals(meta, session_dir)
    outcome, loop_count, open_after = _list_runtime_status(session_dir)
    if outcome:
        meta.turn_outcome = outcome
    if loop_count:
        meta.loop_count = loop_count
    if not meta.turn_outcome:
        inferred = _infer_incomplete_turn_outcome(session_dir)
        if inferred:
            meta.turn_outcome = inferred
    if _session_has_turn_gate(session_dir):
        try:
            override = _gate_override_turn_outcome(session_dir, meta.turn_outcome)
            if override is not None:
                meta.turn_outcome = override
            elif open_after:
                meta.turn_outcome = "running"
        except Exception:
            logger.debug("turn gate status for list %s", session_dir, exc_info=True)
            if open_after:
                meta.turn_outcome = "running"
    elif open_after:
        meta.turn_outcome = "running"
    if meta.turn_failed and not meta.error_count:
        meta.error_count = max(meta.error_count, 1)
    if not meta.num_events and meta.num_messages:
        meta.num_events = int(meta.num_messages)
    _load_run_meta(meta, session_dir)
    meta.origin = origin_key
    return meta


def load_session_meta(
    session_dir: Path,
    *,
    include_timeline_count: bool = True,
    timeline_count: int | None = None,
) -> SessionMeta:
    """Load session metadata from trace artifacts and run.json.

    :param include_timeline_count: When True (default) and *timeline_count* is
        None, set ``num_events`` via :func:`parse_timeline` (coalesced length,
        mtime-cached). Set False for fast list loads; pass *timeline_count*
        when a trusted cached value is available for the current trace mtime.
    :param timeline_count: Explicit coalesced event count (skip parse when set).
    """
    session_id = session_dir.name

    meta = SessionMeta(session_id=session_id, session_dir=session_dir)

    _load_summary(meta, session_dir)
    _load_signals(meta, session_dir)

    # events.jsonl — turn/loop outcome (harness-level; not in updates.jsonl)
    _markers, turn_outcome, loop_count = parse_runtime_markers(session_dir)
    if turn_outcome:
        meta.turn_outcome = turn_outcome
    if loop_count:
        meta.loop_count = loop_count

    # Incomplete / in-progress: no turn_ended yet (live jobs vs killed runs)
    if not meta.turn_outcome:
        inferred = _infer_incomplete_turn_outcome(session_dir)
        if inferred:
            meta.turn_outcome = inferred

    # Interactive gate overrides while the eval is open. Awaiting only when the
    # gate is awaiting_follow_up. Host ``command=done`` / stuck ``final_turn``
    # mean *finishing* only while traces are still fresh; if the container never
    # rewrote ``state=done``, settle to the harness outcome rather than ``running``.
    try:
        override = _gate_override_turn_outcome(session_dir, meta.turn_outcome)
        if override is not None:
            meta.turn_outcome = override
        elif _events_open_turn_after_completed(session_dir):
            meta.turn_outcome = "running"
    except Exception:
        logger.debug("turn gate status for %s", session_dir, exc_info=True)
        if _events_have_open_turn(session_dir):
            if _infer_incomplete_turn_outcome(session_dir) == "running":
                meta.turn_outcome = "running"

    if meta.turn_failed and not meta.error_count:
        # Surface harness failure even when signals.json tool errors are zero
        meta.error_count = max(meta.error_count, 1)

    # Events column = coalesced timeline length (same as the browser). Prefer an
    # explicit count (mtime-validated cache) so list loads do not re-parse every
    # multi‑MB updates.jsonl on launch.
    if timeline_count is not None:
        meta.num_events = max(0, int(timeline_count))
    elif include_timeline_count:
        try:
            meta.num_events = len(parse_timeline(session_dir))
        except Exception:
            logger.debug("Failed to count timeline events for %s", session_dir, exc_info=True)
            meta.num_events = 0
    else:
        meta.num_events = 0

    _load_run_meta(meta, session_dir)

    return meta


def list_turn_outcome_for_dir(session_dir: Path) -> str:
    """Live-only turn status for the sessions list poll (gate + freshness).

    Returns ``running`` / ``ending`` / ``awaiting_follow_up`` / ``""``. Does
    **not** return ``interrupted`` — that inference is for full
    :func:`load_session_meta` only (overwriting finished sessions with
    interrupted made the list show "cancelled" for old successful runs).

    Fresh file mtimes alone must not override a finished harness turn: a
    completed session stays "complete" even within the incomplete-stale window.
    """
    sd = Path(session_dir)
    try:
        override = _gate_override_turn_outcome(sd, "")
        if override in ("awaiting_follow_up", "running", "ending"):
            return override
        if override is not None:
            return ""
    except Exception:
        logger.debug("list turn outcome gate for %s", sd, exc_info=True)

    # Terminal turn_ended (and no open turn) → settled, even if traces are young.
    open_turn = _events_have_open_turn(sd)
    if not open_turn:
        try:
            _markers, marker_outcome, _loop = parse_runtime_markers(sd)
        except Exception:
            marker_outcome = ""
        if _normalize_terminal_turn_outcome(marker_outcome):
            return ""

    # Live only while an open turn is still being written, or no terminal
    # marker yet but traces are still fresh (session just starting).
    inferred = _infer_incomplete_turn_outcome(sd)
    if inferred == "running":
        return "running"
    return ""


def _find_container_for_session(
    session_dir: Path,
    sessions_map: dict,
) -> str:
    """Match a session directory to its container name from run.json sessions map."""

    sd_res: Path
    try:
        sd_res = session_dir.resolve()
    except OSError:
        sd_res = session_dir

    sid = session_dir.name

    for cname, spath in sessions_map.items():
        try:
            p = Path(str(spath)).expanduser()
            try:
                p_res = p.resolve()
            except OSError:
                p_res = p
            if p_res == sd_res or sid == p.name:
                return str(cname)
            try:
                if sd_res.is_relative_to(p_res) or p_res.is_relative_to(sd_res):
                    return str(cname)
            except (ValueError, AttributeError):
                pass
            if sid in str(spath):
                return str(cname)
        except (OSError, ValueError, TypeError):
            continue

    # Walk parents for groket-* container dir name
    for anc in [session_dir, *session_dir.parents]:
        if is_run_dir_name(anc.name):
            return anc.name
    return ""


def _model_from_run_json(session_dir: Path, run_data: dict) -> str:
    """Map this session to the model launched for its groket-* container.

    Returns a bare model id or ``model:effort`` launch token when the recipe
    stored effort-qualified models.
    """

    models = [str(m) for m in (run_data.get("models") or []) if m]
    sessions_map = run_data.get("sessions") or {}
    if not models and not sessions_map:
        return ""

    matched = _find_container_for_session(session_dir, sessions_map)
    if not matched:
        return ""

    if models:
        picked = _match_model_to_container(matched, models)
        if picked:
            return picked

    # Fall back: suffix after run_id segment in groket-{run_id}-{suffix}
    run_id = str(run_data.get("run_id") or "")
    if run_id:
        for pfx in RUN_PREFIXES:
            head = f"{pfx}{run_id}-"
            if matched.startswith(head):
                suffix = re.sub(r"-\d+$", "", matched[len(head) :])
                if suffix:
                    return suffix
    return ""


def _match_model_to_container(container_name: str, models: list[str]) -> str:
    """Match container name to a launch token (bare model or ``model:effort``).

    Runner names containers ``groket-{run_id}-{modelTail}`` or
    ``groket-{run_id}-{modelTail}-{effortPrefix}`` when effort is set.
    """
    from .runs.batch import split_model_effort

    cname = _strip_container_name_disambiguator(container_name)
    best = ""
    best_score = 0
    for model in models:
        token = model.strip()
        if not token:
            continue
        mid, effort = split_model_effort(token)
        m = mid or token
        short = m.split("-")[-1][:10].lower()
        full_l = m.lower()
        score = 0
        if cname.endswith(short) or f"-{short}" in cname:
            score = 10 + len(short)
        elif short and short in cname:
            score = 5 + len(short)
        elif full_l in cname:
            score = 8 + len(full_l)
        # Also match v9-bottlerocket style ids against container ...-bottlerock
        if m.startswith("v9-") and short:
            alias = m[3:]
            alias_short = alias[:10].lower()
            if cname.endswith(alias_short) or f"-{alias_short}" in cname:
                score = max(score, 12 + len(alias_short))
        # Prefer effort-qualified tokens when the container embeds the effort tag
        if effort and effort[:4].lower() in cname:
            score += 20
        elif effort:
            # Slight preference for qualified tokens over bare when scores tie
            score += 1
        if score > best_score:
            best_score = score
            best = token
    return best


def _strip_container_name_disambiguator(name: str) -> str:
    """Remove trailing ``x2`` / ``-2`` collision suffixes from eval container names."""
    cleaned = re.sub(r"x\d+$", "", name.lower())
    cleaned = re.sub(r"-\d+$", "", cleaned)
    return cleaned.rstrip("-")


def _reasoning_effort_from_run_dir(session_dir: Path) -> str:
    """Infer effort from a ``groket-{run_id}-{slug}`` parent (effort suffix in slug)."""
    from .runs.batch import REASONING_EFFORTS

    # Longest names first so ``xhigh`` wins over ``high``.
    efforts = sorted(REASONING_EFFORTS, key=len, reverse=True)
    for anc in [session_dir, *session_dir.parents]:
        if not is_run_dir_name(anc.name):
            if anc.name == "traces":
                break
            continue
        name = _strip_container_name_disambiguator(anc.name)
        for eff in efforts:
            if name.endswith(f"-{eff}") or name == eff:
                return eff
            # Embedded ``-{effort}`` before a truncated tail (``…-tomato-xhigh``).
            if f"-{eff}" in name:
                return eff
        # Container slugs may truncate (e.g. ``…-xhig``); match effort prefixes.
        for eff in efforts:
            prefix = eff[:4] if len(eff) >= 4 else eff
            if prefix and (
                name.endswith(f"-{prefix}") or f"-{prefix}-" in name or name.endswith(prefix)
            ):
                hits = [e for e in efforts if e.startswith(prefix) or e[:4] == prefix]
                if len(hits) == 1:
                    return hits[0]
        break
    return ""


def _reasoning_effort_from_run_config(session_dir: Path) -> str:
    """Read ``default_reasoning_effort`` from a run ``*config.toml`` if present."""
    from .runs.batch import REASONING_EFFORTS

    names = ("gte-config.toml", "groket-config.toml", "config.toml")
    candidates: list[Path] = [session_dir / n for n in names]
    for ancestor in session_dir.parents:
        for n in names:
            candidates.append(ancestor / n)
        if ancestor.name == "traces" or is_run_dir_name(ancestor.name):
            # Include the run dir itself, then stop climbing past traces
            if ancestor.name == "traces":
                break
    seen: set[Path] = set()
    for fp in candidates:
        try:
            key = fp.resolve()
        except OSError:
            key = fp
        if key in seen or not fp.is_file():
            continue
        seen.add(key)
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("default_reasoning_effort"):
                continue
            if "=" not in stripped:
                continue
            val = stripped.split("=", 1)[1].strip().strip("\"'")
            if val.lower() in REASONING_EFFORTS:
                return val.lower()
    return ""


def _model_from_run_parent(session_dir: Path) -> str:

    for anc in [session_dir, *session_dir.parents]:
        name = anc.name
        if not is_run_dir_name(name):
            continue
        # groket-{12hex}-{model_suffix} or groket-{task}-{model_short}
        body = strip_run_prefix(name)
        parts = body.split("-")
        if len(parts) >= 2:
            # Prefer last segment(s) as model tag (may be truncated)
            suffix = parts[-1]
            if suffix.isdigit() and len(parts) >= 3:
                suffix = parts[-2]
            if suffix and suffix not in ("build", "traces", "workspace"):
                return suffix
        break
    return ""


def _prune_session_walk_dirs(dirnames: list[str]) -> None:
    """In-place: do not descend into eval staging, subagent trees, or VCS noise.

    Container dirs are named ``groket-<id>-<model>`` and **must** be walked;
    only explicit staging folder names are skipped.
    """
    dirnames[:] = [d for d in dirnames if not skip_dir_name(d)]


def _native_hit_is_listed(root: Path, path: Path) -> bool:
    """Apply the Python walk policy to one native ``find_sessions`` hit."""
    # session/__init__ imports sources, which import find_sessions.
    from .session.resume import is_resume_seed_path

    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = path
    if any(skip_dir_name(part) for part in rel.parts):
        return False
    if _is_subagent_session_dir(path):
        return False
    return not is_resume_seed_path(path)


def _is_subagent_session_dir(path: Path) -> bool:
    """True when *path* is under a ``subagents`` segment (nested Grok subagent)."""
    return "subagents" in path.parts


def _drop_subagent_mirror_sessions(sessions: list[Path]) -> list[Path]:
    """Remove workspace sibling mirrors of ``parent/subagents/<id>`` trees.

    Nested ``…/subagents/<id>`` dirs are already skipped during the walk. Mirrors
    are full session dirs next to the parent with the same id — drop those by
    collecting ids under each kept session's ``subagents/`` (O(sessions), not
    O(sessions²) sibling probing during the walk).
    """
    if not sessions:
        return sessions
    drop: set[Path] = set()
    for session in sessions:
        sub_root = session / "subagents"
        if not sub_root.is_dir():
            continue
        try:
            with os.scandir(sub_root) as it:
                for ent in it:
                    if ent.is_dir(follow_symlinks=False):
                        drop.add(session.parent / ent.name)
        except OSError:
            continue
    if not drop:
        return sessions
    return [s for s in sessions if s not in drop]


def _looks_like_session_dir(path: Path, filenames: set[str]) -> bool:
    """Whether *path* has session artifacts worth listing."""
    if filenames & {"updates.jsonl", "summary.json"}:
        return True
    if "events.jsonl" in filenames:
        try:
            return (path / "events.jsonl").stat().st_size > 0
        except OSError:
            return False
    return False


def find_sessions(root: Path) -> list[Path]:
    """Recursively find operator-facing session directories.

    A session directory is identified by updates.jsonl / summary.json (stable)
    or a non-empty events.jsonl (live mid-run).

    Skips eval staging trees (``groket-plugins``, ``groket-skills``, ``*.stage``,
    ``.groket-resume-seed``), Grok subagent sessions, and live paths that are
    only symlinks into resume substrate (see :mod:`groket.session.resume`).

    Once a session dir is recognized, the walk does **not** descend into it
    (workspaces under a session are not nested sessions). Descending was the
    dominant cost on large ``~/.grok/sessions`` trees (tens of seconds).
    """
    sessions: list[Path] = []
    if not root.exists():
        return sessions
    native = native_find_sessions(root)
    if native is not None:
        sessions = [path for path in native if _native_hit_is_listed(root, path)]
        return _drop_subagent_mirror_sessions(sessions)
    # session/__init__ imports sources, which import find_sessions.
    from .session.resume import is_resume_seed_path

    # followlinks=False avoids symlink cycles into huge trees from host sessions.
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        _prune_session_walk_dirs(dirnames)
        path = Path(dirpath)
        if _is_subagent_session_dir(path):
            dirnames.clear()
            continue
        # Resume substrate (.groket-resume-seed/…) or live symlink into it.
        if is_resume_seed_path(path):
            dirnames.clear()
            continue
        if _looks_like_session_dir(path, set(filenames)):
            sessions.append(path)
            # Do not walk workspace / build trees inside a session.
            dirnames.clear()
    return _drop_subagent_mirror_sessions(sessions)
