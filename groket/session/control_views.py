"""Wire-shaped session views for the control plane (HUD / web / editors).

Pure domain loaders → JSON-RPC payloads. No Textual. Used by
:class:`~groket.integrations.control.ControlServer` handlers.
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import ClassVar

from .. import event_types as et
from ..analysis.base import AnalysisResult, Finding
from ..bounded_cache import BoundedCache
from ..constants import OVERVIEW_CACHE_MAXSIZE, TURN_VIEW_CACHE_MAXSIZE
from ..models import JsonObject, JsonValue, SessionMeta, ToolInputBag, TraceEvent, as_json_object
from ..notes import load_schema, notes_snapshot
from ..parser import (
    TimelineStamp,
    load_session_meta,
    parse_timeline,
    session_timeline_stamp,
)
from ..session.sources import (
    classify_session_origin,
    is_under_host_grok_sessions,
    work_traces_root,
)
from ..session.tagged_blocks import unwrap_for_display
from ..session.turns import (
    TurnSegment,
    event_display_turn_map,
    event_matches_timeline_kind,
    harness_user_chrome_heading,
    segment_timeline_turns,
)
from ..session.usage_stats import SessionUsageStats, collect_session_usage
from ..tool_display import (
    display_tool_output,
    image_result_path,
    job_list_preview,
    list_event_preview,
    preserve_primary_raw_input,
    tool_family,
    tool_input_fields,
)
from .catalog import session_catalog_row
from .jobs import SessionJobs, job_input_stamp
from .subagents import (
    SubagentRun,
    event_child_session_id,
    event_subagent_fields,
    spawn_fields,
    subagent_list_preview,
    subagent_run_mapping,
    subagent_runs_for_session,
)
from .workflows import workflow_list_preview

DEFAULT_FINDINGS_LIMIT = 80

# Concurrent HUD open + live poll + notifies were double-building the same
# multi‑MB session overview (~12–30s each). Join one flight per path and cache
# by timeline/notes/findings inputs so warm re-polls stay cheap.
_JobFilesStamp = tuple[tuple[str, int, int], ...]
_MonitorStatusStamp = tuple[tuple[str, str], ...]
_OverviewStamp = tuple[
    TimelineStamp,
    str,
    tuple[tuple[str, int, int], ...],
    _JobFilesStamp,
    _MonitorStatusStamp,
]
_TurnViewCache = tuple[TimelineStamp, list[TurnSegment], dict[int, int]]

logger = logging.getLogger(__name__)

DEFAULT_TIMELINE_LIMIT = 300
MAX_TIMELINE_LIMIT = 2000
DEFAULT_CONTENT_CHARS = 4000
MAX_CONTENT_CHARS = 50_000


def session_meta_mapping(
    meta: SessionMeta,
    *,
    path: Path | None = None,
    origin: str | None = None,
) -> JsonObject:
    """Serialize :class:`SessionMeta` for ``session/get`` / enriched list rows."""
    try:
        path_str = str((path or meta.session_dir).resolve())
    except OSError:
        path_str = str(path or meta.session_dir)
    origin_key = (origin or meta.origin or "work").strip() or "work"
    from .subagents import read_session_kind

    kind_path = path or meta.session_dir
    return {
        "sessionId": (meta.session_id or meta.session_dir.name).strip(),
        "path": path_str,
        "title": meta.title or "",
        "summary": meta.summary_text or "",
        "label": meta.label,
        "model": meta.model_display,
        "modelId": meta.model_id or "",
        "reasoningEffort": meta.reasoning_effort or "",
        "status": meta.list_status_label(),
        "outcome": meta.turn_outcome or "",
        "origin": origin_key,
        "createdAt": meta.created_at or "",
        "updatedAt": meta.updated_at or "",
        "numMessages": int(meta.num_messages or 0),
        "numEvents": int(meta.num_events or 0),
        "durationSeconds": float(meta.duration_seconds or 0),
        "duration": meta.duration_str,
        "toolCallCount": int(meta.tool_call_count or 0),
        "toolFailureCount": int(meta.tool_failure_count or 0),
        "errorCount": int(meta.error_count or 0),
        "doomLoopWarnings": int(meta.doom_loop_warnings or 0),
        "linesAdded": int(meta.lines_added or 0),
        "linesRemoved": int(meta.lines_removed or 0),
        "contextWindowUsagePct": meta.context_window_usage_pct,
        "contextTokensUsed": meta.context_tokens_used,
        "contextWindowTokens": meta.context_window_tokens,
        "contextUsage": meta.context_usage_str,
        "contextUsageCompact": meta.context_usage_compact,
        "compactionCount": int(meta.compaction_count or 0),
        "gitRepo": meta.git_repo or "",
        "gitBranch": meta.git_branch or "",
        "gitCommit": meta.git_commit or "",
        "taskId": meta.task_id or "",
        "runId": meta.run_id or "",
        "loopCount": int(meta.loop_count or 0),
        "turnCount": int(meta.turn_count or 0),
        "turnInProgress": bool(meta.turn_in_progress),
        "turnFailed": bool(meta.turn_failed),
        "sessionKind": read_session_kind(kind_path) if kind_path else "",
    }


def timeline_event_mapping(
    event: TraceEvent,
    *,
    content_chars: int = DEFAULT_CONTENT_CHARS,
    turn_index: int | None = None,
) -> JsonObject:
    """Serialize one timeline event for ``session/timeline`` / overview.

    Includes ``kind`` / ``toolFamily`` so palette clients can color and unpack
    the same way as the TUI without re-implementing taxonomy. Optional
    *turn_index* is the trace ``turn_started.turn_number`` for this event.
    """
    cap = max(0, min(int(content_chars), MAX_CONTENT_CHARS))
    content_raw = event.content if isinstance(event.content, str) else str(event.content or "")
    # Strip outer harness tags for display (keep raw length for truncation meta).
    content = unwrap_for_display(content_raw)
    tname = (event.tool_name or "").strip()
    content = display_tool_output(content, tool_name=tname)
    truncated = len(content) > cap
    body = content[:cap] if cap else ""
    raw: JsonValue = {}
    if isinstance(event.raw_input, ToolInputBag):
        inner = event.raw_input.raw()
        raw = as_json_object(inner) if isinstance(inner, dict) else {}
    elif isinstance(event.raw_input, dict):
        raw = as_json_object(event.raw_input)
    if not raw or cap <= 0 or not isinstance(raw, dict):
        raw = {}
    else:
        raw = preserve_primary_raw_input(as_json_object(raw), cap)
    kind = et.event_kind(event.event_type)
    family = tool_family(tname) if kind in ("tool", "tool_result") or tname else ""
    chrome_heading = (
        harness_user_chrome_heading(content_raw)
        if kind == "user" or event.event_type in et.USER_TYPES
        else None
    )
    # Harness injects system-reminder / background-task bodies as user_message_chunk;
    # re-label so TUI/HUD do not present them as operator "User" rows.
    if chrome_heading is not None:
        kind = "system"
    # Prefer structured tool headline when available.
    if kind == "tool" and tname:
        heading = tname if not family else f"{tname}"
    elif kind == "tool_result" and tname:
        heading = f"{tname} result"
    elif chrome_heading is not None:
        heading = chrome_heading
    elif kind == "user":
        heading = "User"
    elif kind == "agent":
        heading = "Assistant"
    elif kind == "thought":
        heading = "Thought"
    elif kind == "error":
        heading = "Error"
    elif kind == "system":
        heading = "System"
    else:
        heading = event.type_label
    type_label = chrome_heading.lower() if chrome_heading else event.type_label
    raw_map = as_json_object(raw) if isinstance(raw, dict) else {}
    if event.event_type in et.TASK_TYPES or event.event_type.startswith("scheduled_task_"):
        preview = job_list_preview(event.event_type, raw_map, event.content)[:200]
    elif event.event_type in et.SUBAGENT_TYPES:
        preview = subagent_list_preview(event.event_type, raw_map, event.content)[:200]
    elif tname == "workflow":
        type_label = "workflow done" if event.event_type in et.TOOL_UPDATE_TYPES else "workflow"
        heading = type_label
        preview = (workflow_list_preview(raw_map) or list_event_preview(event.summary_line, tname))[
            :200
        ]
    else:
        preview = list_event_preview(event.summary_line, tname)[:200]
    fields = tool_input_fields(tname, raw_map, max_chars=cap) if raw_map else []
    tool_fields: list[JsonValue] = list(fields)
    img_path = image_result_path(content_raw, None) if tname in ("image_gen", "image_edit") else ""
    if not img_path and tname in ("image_gen", "image_edit"):
        img_path = image_result_path(body)
    row: JsonObject = {
        "index": int(event.index),
        "type": event.event_type or "",
        "typeLabel": type_label,
        "kind": kind,
        "toolFamily": family,
        "heading": heading,
        "harnessChrome": chrome_heading is not None,
        "timestamp": event.timestamp,
        "time": event.time_str,
        "content": body,
        "contentTruncated": truncated,
        "contentLength": len(content),
        "toolName": tname,
        "toolCallId": event.tool_call_id or "",
        "isError": bool(event.is_error),
        "updateIndex": int(event.update_index or 0),
        "promptIndex": event.prompt_index,
        "turnIndex": int(turn_index) if turn_index is not None else None,
        "preview": preview,
        "rawInput": raw,
        "toolFields": tool_fields,
        "imagePath": img_path,
    }
    extra = event_subagent_fields(event)
    if extra:
        row.update(extra)
        raw_out = row.get("rawInput")
        if isinstance(raw_out, dict):
            merged = dict(raw_out)
            for key, val in extra.items():
                merged.setdefault(key, val)
            row["rawInput"] = merged
    return row


def turn_segment_mapping(
    seg: TurnSegment,
    *,
    include_event_indexes: bool = True,
    assistant_max_chars: int = 12_000,
    subagent_runs: list[SubagentRun] | None = None,
) -> JsonObject:
    """Serialize one turn segment for ``session/turns`` / overview turns.

    :param assistant_max_chars: Cap on assistant wrap-up text. Overview uses a
        short cap so large sessions stay small; full ``session/turns`` keeps
        the default.
    """
    summary, user_index = seg.user_prompt_preview()
    assistant, assistant_index = seg.assistant_preview(max_chars=assistant_max_chars)
    row: JsonObject = {
        "turnIndex": int(seg.turn_index),
        "turnNumber": seg.turn_number,
        "promptIndex": seg.prompt_index,
        "outcome": seg.outcome or "",
        "open": bool(seg.open),
        "label": seg.label,
        "summary": summary,
        "userEventIndex": user_index,
        "assistantSummary": assistant,
        "assistantEventIndex": assistant_index,
        "eventCount": int(seg.event_count),
        "toolCallCount": int(seg.tool_call_count),
        "toolErrorCount": int(seg.tool_error_count),
        "userCount": int(seg.user_count),
        "assistantCount": int(seg.assistant_count),
        "errorEventCount": int(seg.error_event_count),
        "firstIndex": seg.first_index,
        "lastIndex": seg.last_index,
        "durationSeconds": seg.duration_seconds(),
    }
    if include_event_indexes:
        row["eventIndexes"] = [int(e.index) for e in seg.events]
    if subagent_runs is not None:
        row["subagentRuns"] = [
            subagent_run_mapping(run)
            for run in subagent_runs
            if run.parent_turn_index == seg.turn_index
        ]
    return row


def usage_stats_mapping(usage: SessionUsageStats) -> JsonObject:
    """Compact usage summary for ``session/usage``."""
    host: list[JsonValue] = [
        {
            "name": t.name,
            "calls": int(t.calls),
            "errors": int(t.errors),
            "category": t.category,
        }
        for t in (usage.host_tools or usage.tools or [])[:40]
    ]
    mcp: list[JsonValue] = [
        {
            "serverId": s.server_id,
            "useToolCalls": int(s.use_tool_calls),
            "errors": int(s.errors),
            "configured": bool(s.configured),
        }
        for s in (usage.mcp_servers or [])[:40]
    ]
    skills: list[JsonValue] = [
        {
            "skillId": s.skill_id,
            "skillMdReads": int(s.skill_md_reads),
            "nameInTranscript": bool(s.name_in_transcript),
            "engaged": bool(s.engaged),
            "configured": bool(s.configured),
        }
        for s in (usage.skills or [])[:40]
    ]
    tools_invoked: list[JsonValue] = [
        str(x) for x in (getattr(usage, "mcp_tools_invoked", None) or [])[:40]
    ]
    return {
        "hostTools": host,
        "mcpServers": mcp,
        "skills": skills,
        "mcpBridgeCalls": int(getattr(usage, "mcp_bridge_calls", 0) or 0),
        "mcpToolsInvoked": tools_invoked,
    }


def build_session_get(
    session_dir: Path,
    *,
    work_dir: Path | None = None,
    include_notes_revision: bool = True,
    include_timeline_count: bool = False,
) -> JsonObject:
    """Full ``session/get`` payload for *session_dir*.

    *include_timeline_count* defaults False so HUD-style clients stay fast
    (avoid a full ``parse_timeline`` just for the events column).
    """
    sd = Path(session_dir)
    origin = SessionOverview.origin(sd, work_dir)
    meta = load_session_meta(sd, include_timeline_count=include_timeline_count)
    meta.origin = origin
    out = session_meta_mapping(meta, path=sd, origin=origin)
    cat = session_catalog_row(sd, origin=origin)
    if cat is not None:
        out["catalog"] = cat
    if include_notes_revision:
        try:
            snap = notes_snapshot(sd)
            out["notesRevision"] = snap.revision
            out["notesCount"] = len(snap.doc.notes)
        except Exception:
            logger.debug("notes snapshot for session/get %s", sd, exc_info=True)
            out["notesRevision"] = ""
            out["notesCount"] = 0
    return out


def finding_mapping(
    finding: Finding,
    *,
    segs: list[TurnSegment],
    plugin_id: str = "",
) -> JsonObject:
    """Serialize one analysis :class:`~groket.analysis.base.Finding` for palette clients."""
    plug = finding.plugin_id or plugin_id or ""
    detail = finding.detail or ""
    if len(detail) > 2000:
        detail = detail[:1997] + "…"
    event_indices = [int(x) for x in finding.event_indices]
    update_indices = [int(x) for x in finding.update_indices]
    turn_indices = TurnSegment.turn_indices_for(segs, event_indices)
    extras: JsonObject = {}
    for key in (
        "what_model_did",
        "what_should_have_happened",
        "where",
        "why",
        "pattern",
    ):
        val = finding.extras.get(key)
        if val not in (None, ""):
            text = str(val)
            extras[key] = text[:1200] + ("…" if len(text) > 1200 else "")
    return {
        "id": finding.id or "",
        "pluginId": plug,
        "severity": finding.severity.value,
        "title": finding.title or "",
        "detail": detail,
        "category": finding.category or "",
        "eventIndices": list(event_indices[:40]),
        "updateIndices": list(update_indices[:40]),
        "turnIndices": list(turn_indices),
        "primaryEventIndex": event_indices[0] if event_indices else None,
        "primaryTurnIndex": turn_indices[0] if turn_indices else None,
        "extras": extras,
    }


def build_session_findings(
    session_dir: Path,
    *,
    segs: list[TurnSegment] | None = None,
    limit: int = DEFAULT_FINDINGS_LIMIT,
) -> JsonObject:
    """Load cached analysis findings and attach turn/event references.

    Reads ``~/.groket/cache/analysis/<session_id>/*.json`` (same layout as the
    TUI analysis cache). Does not re-run analyzers. Stale/mismatched plugin
    versions are still served so palette clients can show last known findings.
    """
    from ..paths import analysis_cache_dir

    sd = Path(session_dir)
    sid = (sd.name or "").strip()
    cap = max(0, min(int(limit), 200))
    if segs is None:
        segs = segment_timeline_turns(parse_timeline(sd))

    cache_dir = analysis_cache_dir() / "analysis" / sid
    collected: list[JsonObject] = []
    plugins: list[str] = []
    if cache_dir.is_dir():
        # Stable order: plugin file name, then finding order within the file.
        for path in sorted(cache_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                logger.debug("skip findings cache %s", path, exc_info=True)
                continue
            if not isinstance(raw, dict):
                continue
            result_raw = raw.get("result")
            if not isinstance(result_raw, dict):
                if "findings" in raw or "analyzer_id" in raw:
                    result_raw = raw
                else:
                    continue
            try:
                result = AnalysisResult.from_dict(as_json_object(result_raw))
            except (TypeError, ValueError, KeyError):
                logger.debug("skip findings parse %s", path, exc_info=True)
                continue
            plug = (result.analyzer_id or path.stem or "").strip()
            if plug and plug not in plugins:
                plugins.append(plug)
            findings: list[Finding] = list(result.findings or [])
            for f in findings:
                collected.append(finding_mapping(f, segs=segs, plugin_id=plug))

    rows: list[JsonValue] = list(collected[:cap])
    plugins_out: list[JsonValue] = list(plugins)
    return {
        "sessionId": sid,
        "total": len(collected),
        "count": len(rows),
        "truncated": len(collected) > len(rows),
        "plugins": plugins_out,
        "findings": rows,
    }


class SessionOverview:
    """Cached ``session/overview`` payload and stamp-keyed turn view."""

    _cache: ClassVar[BoundedCache[tuple[_OverviewStamp, JsonObject]]] = BoundedCache(
        OVERVIEW_CACHE_MAXSIZE
    )
    _inflight: ClassVar[dict[str, Future[JsonObject]]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()
    _turn_cache: ClassVar[BoundedCache[_TurnViewCache]] = BoundedCache(TURN_VIEW_CACHE_MAXSIZE)
    _turn_lock: ClassVar[threading.Lock] = threading.Lock()

    @staticmethod
    def cache_key(session_dir: Path) -> str:
        """Stable cache key for a session directory."""
        sd = Path(session_dir)
        try:
            return str(sd.expanduser().resolve())
        except OSError:
            return str(sd.expanduser())

    @staticmethod
    def findings_stamp(session_dir: Path) -> tuple[tuple[str, int, int], ...]:
        """Fingerprint analysis-cache JSON files (name, mtime_ns, size)."""
        from ..paths import analysis_cache_dir

        sid = (Path(session_dir).name or "").strip()
        if not sid:
            return ()
        cache_dir = analysis_cache_dir() / "analysis" / sid
        if not cache_dir.is_dir():
            return ()
        out: list[tuple[str, int, int]] = []
        try:
            paths = sorted(cache_dir.glob("*.json"))
        except OSError:
            return ()
        for path in paths:
            try:
                st = path.stat()
            except OSError:
                continue
            out.append((path.name, int(st.st_mtime_ns), int(st.st_size)))
        return tuple(out)

    @staticmethod
    def origin(session_dir: Path, work_dir: Path | None) -> str:
        """``host`` or ``work`` for this session directory."""
        sd = Path(session_dir)
        if work_dir is not None:
            return classify_session_origin(sd, work_traces=work_traces_root(work_dir))
        if is_under_host_grok_sessions(sd):
            return "host"
        return "work"

    @staticmethod
    def notes_schema() -> JsonObject:
        """Operator notes schema for HUD/TUI forms (same shape as notes/list)."""
        schema = load_schema()
        return {
            "id": schema.schema_id,
            "fields": [
                {
                    "id": field.id,
                    "label": field.label or field.id,
                    "choices": list(field.choices),
                    "pick": field.pick,
                }
                for field in schema.fields
            ],
        }

    @classmethod
    def input_stamp(cls, session_dir: Path) -> _OverviewStamp:
        """Inputs that must match for a cached overview to be reused."""
        sd = Path(session_dir)
        notes_rev = ""
        try:
            notes_rev = notes_snapshot(sd).revision
        except Exception:
            logger.debug("notes stamp for overview %s", sd, exc_info=True)
        job_files, monitor_status = job_input_stamp(sd)
        return (
            session_timeline_stamp(sd),
            notes_rev,
            cls.findings_stamp(sd),
            job_files,
            monitor_status,
        )

    @classmethod
    def turn_view(
        cls,
        session_dir: Path,
        events: list[TraceEvent],
    ) -> tuple[list[TurnSegment], dict[int, int]]:
        """Return (segments, event_index→display_turn), stamp-cached.

        Full re-segmentation of multi‑thousand event lists is the thrash path
        for paged ``session/timeline``; reuse until the timeline stamp moves.
        """
        sd = Path(session_dir)
        key = cls.cache_key(sd)
        stamp = session_timeline_stamp(sd)
        with cls._turn_lock:
            cached = cls._turn_cache.get(key)
            if cached is not None and cached[0] == stamp:
                return cached[1], cached[2]
        segs = segment_timeline_turns(events)
        turn_by_index = event_display_turn_map(segs)
        with cls._turn_lock:
            cls._turn_cache[key] = (stamp, segs, turn_by_index)
        return segs, turn_by_index

    @classmethod
    def uncached(cls, session_dir: Path, *, work_dir: Path | None = None) -> JsonObject:
        """Build overview without single-flight / result cache."""
        sd = Path(session_dir)
        origin = cls.origin(sd, work_dir)
        meta = load_session_meta(sd, include_timeline_count=False)
        meta.origin = origin
        events = parse_timeline(sd)
        meta.num_events = len(events)
        segs, turn_map = cls.turn_view(sd, events)
        runs = subagent_runs_for_session(sd, events, segs, turn_map)
        notes_rev = ""
        notes_count = 0
        notes_rows: list[JsonValue] = []
        try:
            snap = notes_snapshot(sd)
            notes_rev = snap.revision
            notes_count = len(snap.doc.notes)
            for note in snap.doc.sorted_notes()[:40]:
                notes_rows.append(
                    {
                        "id": note.id,
                        "turnIndex": note.turn_index,
                        "fields": dict(note.fields),
                        "eventIndices": list(note.event_indices),
                        "createdAt": note.created_at,
                        "updatedAt": note.updated_at,
                    }
                )
        except Exception:
            logger.debug("notes for session/overview %s", sd, exc_info=True)

        findings_block = build_session_findings(sd, segs=segs)
        jobs, schedules, workflows = SessionJobs.overview_rows(sd, events, cls.cache_key(sd))
        summary = (meta.summary_text or "").strip()
        if len(summary) > 1200:
            summary = summary[:1197] + "…"
        return {
            "sessionId": (meta.session_id or sd.name).strip(),
            "meta": session_meta_mapping(meta, path=sd, origin=origin),
            "summary": summary,
            "backgroundJobs": SessionJobs.json_rows(jobs),
            "schedules": SessionJobs.json_rows(schedules),
            "workflows": SessionJobs.json_rows(workflows),
            "turns": {
                "total": len(segs),
                # Short assistant preview for the list: full wrap-up is for open
                # cards / session/turns — 12k×N turns made overview multi‑100KB.
                "turns": [
                    turn_segment_mapping(
                        s,
                        include_event_indexes=False,
                        assistant_max_chars=400,
                        subagent_runs=runs,
                    )
                    for s in segs
                ],
                "subagentRuns": [subagent_run_mapping(r) for r in runs],
            },
            "timeline": {
                "total": len(events),
                "offset": 0,
                "limit": 0,
                "truncated": False,
                "events": [],
                "lazy": True,
            },
            "notes": {
                "revision": notes_rev,
                "count": notes_count,
                "notes": notes_rows,
                "schema": cls.notes_schema(),
            },
            "findings": findings_block,
        }

    @classmethod
    def build(cls, session_dir: Path, *, work_dir: Path | None = None) -> JsonObject:
        """Meta + turns + notes + findings (timeline lazy); one in-flight per path."""
        sd = Path(session_dir)
        cache_key = cls.cache_key(sd)

        while True:
            stamp = cls.input_stamp(sd)
            cached = cls._cache.get(cache_key)
            if cached is not None and cached[0] == stamp:
                return cached[1]

            owner = False
            with cls._lock:
                fut = cls._inflight.get(cache_key)
                if fut is None:
                    fut = Future()
                    cls._inflight[cache_key] = fut
                    owner = True

            if not owner:
                fut.result()
                continue

            try:
                out = cls.uncached(sd, work_dir=work_dir)
                # Stamp after build so a growth mid-flight forces a recheck.
                done_stamp = cls.input_stamp(sd)
                cls._cache[cache_key] = (done_stamp, out)
                if not fut.done():
                    fut.set_result(out)
                return out
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)
                raise
            finally:
                with cls._lock:
                    if cls._inflight.get(cache_key) is fut:
                        del cls._inflight[cache_key]


def overview_input_stamp(session_dir: Path) -> _OverviewStamp:
    """Inputs that must match for a cached overview to be reused."""
    return SessionOverview.input_stamp(session_dir)


def build_session_overview(
    session_dir: Path,
    *,
    work_dir: Path | None = None,
) -> JsonObject:
    """Meta + turns + notes + findings for palette clients (timeline lazy).

    Parses the timeline once for turn segmentation and ``numEvents``. Does
    **not** embed event rows — clients call ``session/timeline`` with
    offset/limit (and optional type filter) so large sessions stay cheap.

    Concurrent callers for the same session **join one in-flight build** and
    reuse a stamp-keyed result so dual open+live-poll does not thrash multi‑MB
    host sessions.
    """
    return SessionOverview.build(session_dir, work_dir=work_dir)


def timeline_query_hit(event: TraceEvent, query: str) -> tuple[str, str] | None:
    """First field that contains *query*, plus a snippet that includes the needle."""
    needle = (query or "").strip().casefold()
    if not needle:
        return None
    body = event.content if isinstance(event.content, str) else str(event.content or "")
    fields = (
        ("type", event.event_type or ""),
        ("type_label", event.type_label or ""),
        ("tool", event.tool_name or ""),
        ("heading", event.summary_line or ""),
        ("preview", list_event_preview(event.summary_line, event.tool_name)[:200]),
        ("content", body[:8_000]),
    )

    def snippet(text: str, start: int) -> str:
        lo = max(0, start - 40)
        hi = min(len(text), start + max(len(needle), 1) + 40)
        chunk = text[lo:hi].replace("\n", " ").replace("\r", " ")
        if lo > 0:
            chunk = f"…{chunk}"
        if hi < len(text):
            chunk = f"{chunk}…"
        return chunk

    for field, text in fields:
        pos = text.casefold().find(needle)
        if pos >= 0:
            return field, snippet(text, pos)
    return None


def build_session_timeline(
    session_dir: Path,
    *,
    offset: int = 0,
    limit: int | None = None,
    event_type: str = "",
    kind: str = "",
    query: str = "",
    prompt_index: int | None = None,
    around_index: int | None = None,
    at_index: int | None = None,
    content_chars: int = DEFAULT_CONTENT_CHARS,
) -> JsonObject:
    """Paged timeline for ``session/timeline``."""
    sd = Path(session_dir)
    events = parse_timeline(sd)
    # Enclosing turn_started.turn_number on each event (HUD/TUI column).
    _segs, turn_by_index = SessionOverview.turn_view(sd, events)
    prompt_indexes: set[int] | None = None
    if prompt_index is not None:
        prompt_indexes = TurnSegment.indexes_for_prompt(_segs, int(prompt_index))
    type_filter = (event_type or "").strip().casefold()
    filtered: list[TraceEvent] = []
    for ev in events:
        if type_filter and type_filter not in (ev.event_type or "").casefold():
            if type_filter not in (ev.type_label or "").casefold():
                continue
        if not event_matches_timeline_kind(ev, kind):
            continue
        if query.strip() and timeline_query_hit(ev, query) is None:
            continue
        if prompt_indexes is not None and int(ev.index) not in prompt_indexes:
            continue
        filtered.append(ev)
    total = len(filtered)
    off = max(0, int(offset))
    lim = DEFAULT_TIMELINE_LIMIT if limit is None else max(0, min(int(limit), MAX_TIMELINE_LIMIT))
    if at_index is not None:
        target = int(at_index)
        hit = next((i for i, ev in enumerate(filtered) if int(ev.index) == target), None)
        if hit is None:
            off = 0
            lim = 0
        else:
            off = hit
            lim = 1
    elif around_index is not None:
        target = int(around_index)
        hit = next((i for i, ev in enumerate(filtered) if int(ev.index) >= target), None)
        if hit is None and filtered:
            hit = len(filtered) - 1
        if hit is not None:
            off = max(0, hit - 8)
    page = filtered[off : off + lim] if lim else []
    q = (query or "").strip()
    spawn_ident: dict[str, tuple[str, str]] = {}
    for ev in events:
        if ev.event_type != "subagent_spawned":
            continue
        child = event_child_session_id(ev)
        if not child:
            continue
        bag = ev.raw_input.raw() if isinstance(ev.raw_input, ToolInputBag) else {}
        fields = spawn_fields(bag if isinstance(bag, dict) else {})
        spawn_ident[child] = (fields.get("subagent_type") or "", fields.get("description") or "")
    events_out: list[JsonValue] = []
    for ev in page:
        row = timeline_event_mapping(
            ev,
            content_chars=content_chars,
            turn_index=turn_by_index.get(int(ev.index)),
        )
        child = event_child_session_id(ev)
        ident = spawn_ident.get(child) if child else None
        if ident is not None:
            typ, desc = ident
            raw_out = row.get("rawInput")
            if isinstance(raw_out, dict):
                if typ:
                    raw_out.setdefault("subagentType", typ)
                if desc:
                    raw_out.setdefault("description", desc)
            if typ and not row.get("subagentType"):
                row["subagentType"] = typ
            if desc and not row.get("description"):
                row["description"] = desc
            if ev.event_type in et.SUBAGENT_TYPES and desc:
                row["preview"] = desc[:200]
        if q:
            # Distinct name: earlier branches bind ``hit`` as a page index.
            match = timeline_query_hit(ev, q)
            if match is not None:
                field, snippet = match
                row["matchField"] = field
                row["matchSnippet"] = snippet
        events_out.append(row)
    return {
        "sessionId": sd.name,
        "total": total,
        "offset": off,
        "limit": lim,
        "events": events_out,
    }


def build_session_turns(session_dir: Path) -> JsonObject:
    """Turn segments for ``session/turns``."""
    sd = Path(session_dir)
    events = parse_timeline(sd)
    segs, turn_map = SessionOverview.turn_view(sd, events)
    runs = subagent_runs_for_session(sd, events, segs, turn_map)
    return {
        "sessionId": sd.name,
        "total": len(segs),
        "turns": [turn_segment_mapping(s, subagent_runs=runs) for s in segs],
        "subagentRuns": [subagent_run_mapping(r) for r in runs],
    }


def build_session_diff(session_dir: Path) -> JsonObject:
    """Rewind snapshots or approximate edits for ``session/diff``."""
    from .workspace_diff import load_workspace_diff_doc

    sd = Path(session_dir)
    doc = load_workspace_diff_doc(sd)
    points: list[JsonValue] = []
    for point in doc.points:
        files: list[JsonValue] = [
            as_json_object(
                {
                    "path": hunk.path,
                    "kind": hunk.kind,
                    "added": hunk.added,
                    "removed": hunk.removed,
                    "unified": hunk.unified,
                }
            )
            for hunk in point.files
        ]
        points.append(
            as_json_object(
                {
                    "key": point.key,
                    "source": point.source,
                    "promptIndex": point.prompt_index,
                    "createdAt": point.created_at,
                    "prompt": point.prompt_text,
                    "assistant": point.assistant_text,
                    "filesChanged": point.files_changed,
                    "linesAdded": point.lines_added,
                    "linesRemoved": point.lines_removed,
                    "files": files,
                }
            )
        )
    return {
        "sessionId": sd.name,
        "source": doc.source,
        "points": points,
    }


def build_session_usage(session_dir: Path) -> JsonObject:
    """Usage summary for ``session/usage``."""
    events = parse_timeline(Path(session_dir))
    usage = collect_session_usage(Path(session_dir), events)
    out = usage_stats_mapping(usage)
    out["sessionId"] = Path(session_dir).name
    return out


__all__ = [
    "DEFAULT_CONTENT_CHARS",
    "DEFAULT_FINDINGS_LIMIT",
    "DEFAULT_TIMELINE_LIMIT",
    "MAX_CONTENT_CHARS",
    "MAX_TIMELINE_LIMIT",
    "build_session_diff",
    "build_session_findings",
    "build_session_get",
    "build_session_overview",
    "build_session_timeline",
    "build_session_turns",
    "build_session_usage",
    "SessionOverview",
    "timeline_query_hit",
    "finding_mapping",
    "session_meta_mapping",
    "timeline_event_mapping",
    "turn_segment_mapping",
    "usage_stats_mapping",
]
