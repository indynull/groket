"""Segment a session timeline into harness / interactive turns."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .. import event_types as et
from ..models import JsonValue, TraceEvent
from .tagged_blocks import (  # noqa: F401 — re-export for session.turns callers
    harness_user_chrome_heading,
    is_harness_user_chrome,
    operator_prompt_text,
)

_TURN_NUM_RE = re.compile(r"turn_number\s*=\s*(\d+)", re.I)
_OUTCOME_RE = re.compile(r"outcome\s*=\s*(\S+)", re.I)


@dataclass
class TurnSegment:
    """One agent turn (between turn_started and turn_ended, or open-ended)."""

    turn_index: int  # unique list position (0..n-1); picker / wire identity
    turn_number: int | None  # events.jsonl turn_started.turn_number; labels and reports
    prompt_index: int | None = None  # source _meta.promptIndex from the operator message
    outcome: str = ""  # last turn_ended outcome for this segment ("" if open)
    open: bool = False  # no turn_ended yet
    events: list[TraceEvent] = field(default_factory=list)

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def tool_calls(self) -> list[TraceEvent]:
        return [e for e in self.events if e.event_type in et.TOOL_CALL_TYPES]

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def tool_error_count(self) -> int:
        return sum(1 for e in self.tool_calls if e.is_error)

    @property
    def user_count(self) -> int:
        return sum(1 for e in self.events if e.event_type in et.USER_TYPES)

    @property
    def assistant_count(self) -> int:
        return sum(1 for e in self.events if e.event_type in et.AGENT_TYPES)

    @property
    def error_event_count(self) -> int:
        return sum(1 for e in self.events if e.is_error or e.event_type in et.ERROR_TYPES)

    @property
    def first_index(self) -> int | None:
        return self.events[0].index if self.events else None

    @property
    def last_index(self) -> int | None:
        return self.events[-1].index if self.events else None

    @property
    def label(self) -> str:
        n = display_turn_number(self)
        head = f"turn {n}" if n is not None else "unnumbered"
        if self.open:
            return f"{head} (open)"
        if self.outcome:
            return f"{head} ({self.outcome})"
        return head

    def duration_seconds(self, durations: dict[int, float] | None = None) -> float | None:
        """Span from first to last event timestamp (seconds), else sum of durations map."""
        ts = [int(e.timestamp) for e in self.events if e.timestamp is not None]
        if len(ts) >= 2:
            delta = max(ts) - min(ts)
            # Trace timestamps are typically unix seconds; large deltas in ms are rare for a turn.
            if delta > 86_400 * 365:  # absurd as seconds → treat as ms
                return delta / 1000.0
            return float(delta)
        if durations:
            total = 0.0
            any_d = False
            for e in self.events:
                d = durations.get(e.index)
                if d is not None:
                    total += float(d)
                    any_d = True
            return total if any_d else None
        return None


def _is_turn_started(ev: TraceEvent) -> bool:
    if ev.event_type == et.TURN_STARTED:
        return True
    if ev.event_type in ("session", "session_error"):
        return "turn started" in (ev.content or "").lower()
    return False


def _is_turn_ended(ev: TraceEvent) -> bool:
    if ev.event_type == et.TURN_ENDED:
        return True
    # Host ``updates.jsonl`` closes a turn with ``turn_completed`` (no events.jsonl).
    if ev.event_type == et.TURN_COMPLETED:
        return True
    if ev.event_type in ("session", "session_error"):
        return "turn ended" in (ev.content or "").lower()
    return False


def _turn_number_from_event(ev: TraceEvent) -> int | None:
    m = _TURN_NUM_RE.search(ev.content or "")
    if not m:
        return None
    return int(m.group(1))


def _outcome_from_event(ev: TraceEvent) -> str:
    m = _OUTCOME_RE.search(ev.content or "")
    if m:
        return m.group(1)
    if ev.is_error:
        return "error"
    # Host ``turn_completed`` has no ``outcome=`` field.
    if ev.event_type == et.TURN_COMPLETED:
        return ""
    return "unknown"


def _is_events_jsonl_turn_end(ev: TraceEvent) -> bool:
    """True for ``events.jsonl`` ``turn_ended`` (not host ``turn_completed``)."""
    if ev.event_type == et.TURN_ENDED:
        return True
    if ev.event_type in ("session", "session_error"):
        return "turn ended" in (ev.content or "").lower()
    return False


def _append_turn_end(
    ev: TraceEvent,
    current: TurnSegment | None,
    segments: list[TurnSegment],
    display_i: int,
) -> tuple[TurnSegment | None, int]:
    """Close *current*, or fold a second end marker into the previous turn.

    A follow-up user message often lands after host ``turn_completed`` and
    before the matching ``events.jsonl`` ``turn_ended``. That late end belongs
    on the previous harness turn — do not close the new operator turn.
    Host-only traces close with ``turn_completed`` and still end *current*.
    """
    outcome = _outcome_from_event(ev)
    if (
        current is not None
        and current.open
        and current.events
        and _is_events_jsonl_turn_end(ev)
        and not any(_is_turn_started(e) for e in current.events)
        and segments
    ):
        prev = segments[-1]
        prev.events.append(ev)
        if outcome:
            prev.outcome = outcome
        prev.open = False
        return current, display_i
    if current is not None:
        current.events.append(ev)
        if outcome:
            current.outcome = outcome
        current.open = False
        segments.append(current)
        return None, display_i
    if segments:
        prev = segments[-1]
        prev.events.append(ev)
        if outcome:
            prev.outcome = outcome
        prev.open = False
        return None, display_i
    display_i += 1
    segments.append(
        TurnSegment(
            turn_index=display_i,
            turn_number=None,
            open=False,
            outcome=outcome,
            events=[ev],
        )
    )
    return None, display_i


def is_session_level_timeline_event(ev: TraceEvent) -> bool:
    """Return True for timeline rows that are not part of any agent turn.

    The parser may inject session-scoped chrome (e.g. ``system_prompt.txt`` as
    ``event_type=system``) onto the timeline for display. Turn segmentation and
    per-turn stats use only harness / conversation events; these stay visible
    on the full timeline and when filtering by turn (see browser turn filter).
    """
    return ev.event_type == et.SYSTEM


# Back-compat alias used by older call sites / tests.
_user_is_background_task_completion = is_harness_user_chrome


def is_operator_user_event(ev: TraceEvent) -> bool:
    """True for a real host/operator user message (not harness chrome).

    Messages that are only harness tags (``system-reminder``, preamble, …) are
    false. Payloads that embed ``<user_query>`` are operator even if wrapped.
    """
    if ev.event_type not in et.USER_TYPES and ev.event_type != "user":
        return False
    return bool(operator_prompt_text(ev.content or ""))


def event_matches_timeline_kind(event: TraceEvent, kind: str) -> bool:
    """True when *event* belongs in Timeline View *kind* after chrome relabel.

    Harness user-chrome (system-reminder, background-task) is Session, not User.
    """
    mode = (kind or "").strip().casefold()
    if not mode or mode == "all":
        return True
    chrome = None
    if event.event_type in et.USER_TYPES or event.event_type == "user":
        chrome = harness_user_chrome_heading(event.content or "")
    mapped = "system" if chrome is not None else et.event_kind(event.event_type)
    if mode == "tools":
        return mapped in {"tool", "tool_result"} or event.event_type in et.TOOL_TYPES
    if mode == "user":
        return mapped == "user"
    if mode in {"asst", "assistant", "agent"}:
        return mapped in {"agent", "thought"}
    if mode in {"sess", "session"}:
        return (
            mapped in {"system", "session", "error"} or event.event_type in et.SESSION_CHROME_TYPES
        )
    if mode in {"errors", "error"}:
        return bool(event.is_error) or mapped == "error" or event.event_type in et.ERROR_TYPES
    if mode in {"subagents", "subagent"}:
        return mapped == "subagent" or event.event_type in et.SUBAGENT_TYPES
    if mode in {"background", "jobs"}:
        return mapped == "task" or event.event_type in et.TASK_TYPES
    if mode in {"workflows", "workflow"}:
        return (event.tool_name or "") == "workflow"
    return True


def _segment_has_operator_user(seg: TurnSegment) -> bool:
    """True when the segment includes a real host/operator user prompt."""
    return any(is_operator_user_event(ev) for ev in seg.events)


def _stamp_trace_turn_ids(segments: list[TurnSegment]) -> list[TurnSegment]:
    """Unique ``turn_index`` 0..n-1. Keep only trace ``turn_number`` values.

    Host-only sessions (no ``turn_started.turn_number`` on any segment) fill
    ``turn_number`` from the list position so labels stay 0..n-1. A mixed
    session that omitted a start marker keeps ``turn_number`` unset — never
    invent ``prev + 1``.
    """
    has_trace = any(seg.turn_number is not None for seg in segments)
    for i, seg in enumerate(segments):
        seg.turn_index = i
        if not has_trace:
            seg.turn_number = i
    return segments


def _assign_prompt_indexes(segments: list[TurnSegment]) -> list[TurnSegment]:
    for seg in segments:
        # Prefer operator user rows; harness chrome may still carry a promptIndex.
        seg.prompt_index = next(
            (
                event.prompt_index
                for event in seg.events
                if is_operator_user_event(event) and event.prompt_index is not None
            ),
            next(
                (
                    event.prompt_index
                    for event in seg.events
                    if event.event_type in et.USER_TYPES and event.prompt_index is not None
                ),
                None,
            ),
        )
    return segments


def display_turn_number(seg: TurnSegment) -> int | None:
    """Trace ``turn_started.turn_number``, or host list position.

    Host-only sessions (no start markers) stamp list position into
    ``turn_number``. A segment that sits in a numbered session but never
    received a ``turn_started`` returns ``None`` — do not invent a face id.
    :attr:`TurnSegment.turn_index` stays the unique list key so a restamp
    that repeats ``turn_number`` still has two picker rows.
    """
    if seg.turn_number is not None:
        return int(seg.turn_number)
    return None


def events_on_display_turn(seg: TurnSegment, turn_by_event: dict[int, int]) -> list[TraceEvent]:
    """Events in *seg* whose enclosing marker is this segment's display id.

    Events whose enclosing ``turn_started`` is a different number (if any
    were ever grouped here) stay off this filter.
    """
    primary = display_turn_number(seg)
    if primary is None:
        return [ev for ev in seg.events if int(ev.index) not in turn_by_event]
    return [ev for ev in seg.events if turn_by_event.get(int(ev.index)) == primary]


def event_display_turn_map(segments: list[TurnSegment]) -> dict[int, int]:
    """Map timeline event index → enclosing ``turn_started.turn_number``.

    A follow-up user row that lands before the next ``turn_started`` inherits
    that upcoming marker. Events with no enclosing start stay off the map.

    :param segments: Output of :func:`segment_timeline_turns`.
    :returns: ``event.index`` → trace turn id.
    """
    out: dict[int, int] = {}
    for seg in segments:
        pending: list[int] = []
        current: int | None = None
        for ev in seg.events:
            if _is_turn_started(ev):
                tn = _turn_number_from_event(ev)
                if tn is not None:
                    current = tn
                    for idx in pending:
                        out[idx] = tn
                    pending.clear()
            idx = int(ev.index)
            if current is not None:
                out[idx] = current
            else:
                pending.append(idx)
        # Host-only fill (no start markers in the session) stamps turn_number
        # from list position. A start-less segment in a numbered session stays
        # unmapped — do not invent a face id.
        if pending and (fallback := seg.turn_number) is not None:
            for idx in pending:
                out[idx] = int(fallback)
    return out


def turn_index_for_event(segments: list[TurnSegment], event_index: int) -> int | None:
    """Return the trace turn id of the event, if it sits in a segment."""
    return event_display_turn_map(segments).get(int(event_index))


def segment_timeline_turns(timeline: list[TraceEvent]) -> list[TurnSegment]:
    """Split *timeline* into turns using session turn_started / turn_ended markers.

    Session-level timeline events (see :func:`is_session_level_timeline_event`)
    are omitted from segments entirely — they are not a turn and must not
    create an extra segment before the first ``turn started``.

    Remaining events before the first turn_started form an unnumbered
    segment when present (e.g. user messages). Labels and the event column
    use the trace ``turn_number`` on ``turn_started``. ``turn_index`` is the
    unique list position so a restamp can repeat ``turn_number``. A segment
    with no start marker in a numbered session stays unlabeled. Every
    ``turn_started`` keeps its own picker row.
    """
    turn_events = [e for e in timeline if not is_session_level_timeline_event(e)]
    if not turn_events:
        return []

    has_markers = any(_is_turn_started(e) or _is_turn_ended(e) for e in turn_events)
    if not has_markers:
        return _assign_prompt_indexes(
            _stamp_trace_turn_ids(
                [
                    TurnSegment(
                        turn_index=0,
                        turn_number=None,
                        outcome="",
                        open=True,
                        events=list(turn_events),
                    )
                ]
            )
        )

    segments: list[TurnSegment] = []
    current: TurnSegment | None = None
    display_i = -1

    for ev in turn_events:
        if _is_turn_started(ev):
            tn = _turn_number_from_event(ev)
            # Follow-up user message(s) often land *before* the next turn_started.
            # Keep that open segment and add the harness marker — do not split.
            if (
                current is not None
                and current.events
                and current.open
                and not any(_is_turn_started(e) for e in current.events)
            ):
                current.events.append(ev)
                if tn is not None:
                    current.turn_number = tn
                continue
            if current is not None and current.events:
                # Previous turn had no explicit end — close as open=False unknown
                if current.open and not current.outcome:
                    current.outcome = "unknown"
                current.open = False
                segments.append(current)
            display_i += 1
            current = TurnSegment(
                turn_index=display_i,
                turn_number=tn,
                open=True,
                events=[ev],
            )
            continue

        if _is_turn_ended(ev):
            current, display_i = _append_turn_end(ev, current, segments, display_i)
            continue

        if current is None:
            if segments:
                # Between turns: late *agent* stream chunks belong to the prior
                # turn; a new *operator* user message starts the next interactive
                # turn (often arrives before the next turn_started marker).
                # Harness user chrome (system-reminder / background completion)
                # is not an operator follow-up — attach to the previous segment.
                if ev.event_type in et.USER_TYPES or ev.event_type == "user":
                    if is_harness_user_chrome(ev.content or ""):
                        segments[-1].events.append(ev)
                    else:
                        display_i = len(segments)
                        current = TurnSegment(
                            turn_index=display_i,
                            turn_number=None,
                            open=True,
                            events=[ev],
                        )
                else:
                    segments[-1].events.append(ev)
            else:
                # True preamble before the first turn_started
                display_i = 0
                current = TurnSegment(
                    turn_index=0,
                    turn_number=None,
                    open=True,
                    events=[ev],
                )
        else:
            current.events.append(ev)

    if current is not None and current.events:
        segments.append(current)

    return _assign_prompt_indexes(_stamp_trace_turn_ids(segments))


def turn_summary_rows(
    segments: list[TurnSegment],
    *,
    durations: dict[int, float] | None = None,
    session_context_compact: str = "",
    context_by_turn: dict[int, str] | None = None,
) -> list[dict[str, JsonValue]]:
    """Tabular rows for the Summary turns table (chronological, turn 0 first).

    Grok writes context fill only as a session snapshot in ``signals.json``.
    *context_by_turn* holds read-only samples observed during live refresh.
    When absent, *session_context_compact* is shown on the latest segment only.
    """
    rows: list[dict[str, JsonValue]] = []
    last_idx = len(segments) - 1
    fallback = (session_context_compact or "").strip()
    samples = context_by_turn or {}
    for i, seg in enumerate(segments):
        dur = seg.duration_seconds(durations)
        tools = Counter(e.tool_name for e in seg.tool_calls if e.tool_name)
        top_tools = ", ".join(f"{n}×{c}" for n, c in tools.most_common(3)) or "—"
        ctx = (samples.get(seg.turn_index) or "").strip()
        if not ctx and fallback and i == last_idx:
            ctx = fallback
        rows.append(
            {
                "turn": display_turn_number(seg),
                "turn_index": int(seg.turn_index),
                "label": seg.label,
                "outcome": seg.outcome or ("open" if seg.open else "—"),
                "open": seg.open,
                "events": seg.event_count,
                "tools": seg.tool_call_count,
                "tool_errors": seg.tool_error_count,
                "users": seg.user_count,
                "assistants": seg.assistant_count,
                "errors": seg.error_event_count,
                "duration_s": dur,
                "context": ctx,
                "top_tools": top_tools,
                "first_index": seg.first_index,
                "last_index": seg.last_index,
            }
        )
    return rows


def format_turns_plain(
    segments: list[TurnSegment], *, durations: dict[int, float] | None = None
) -> str:
    """Plain multi-line turn breakdown (CLI / debug)."""
    if not segments:
        return "(no turns)"
    lines = [f"Turns: {len(segments)}"]
    for row in turn_summary_rows(segments, durations=durations):
        dur = row["duration_s"]
        dur_s = f"{dur:.1f}s" if isinstance(dur, (int, float)) else "—"
        lines.append(
            f"  {row['label']}: events={row['events']} tools={row['tools']} "
            f"errs={row['tool_errors']} dur={dur_s}  [{row['top_tools']}]"
        )
    return "\n".join(lines)
