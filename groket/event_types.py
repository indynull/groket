"""Grok-aligned timeline event types (1:1 with harness signals).

``TraceEvent.event_type`` uses Grok names from:

* ``updates.jsonl`` → ``params.update.sessionUpdate``
* ``events.jsonl`` → top-level ``type`` (turn markers / errors)

The only non-Grok value is ``system`` (injected ``system_prompt.txt`` chrome).
"""

from __future__ import annotations

# ── sessionUpdate (updates.jsonl) ─────────────────────────────────────────
USER_MESSAGE_CHUNK = "user_message_chunk"
AGENT_MESSAGE_CHUNK = "agent_message_chunk"
AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
TOOL_CALL = "tool_call"
TOOL_CALL_UPDATE = "tool_call_update"
PLAN = "plan"
TASK_BACKGROUNDED = "task_backgrounded"
TASK_COMPLETED = "task_completed"
SCHEDULED_TASK_CREATED = "scheduled_task_created"
SCHEDULED_TASK_UPDATED = "scheduled_task_updated"
SCHEDULED_TASK_FIRED = "scheduled_task_fired"
SCHEDULED_TASK_DELETED = "scheduled_task_deleted"
TURN_COMPLETED = "turn_completed"
SUBAGENT_SPAWNED = "subagent_spawned"
SUBAGENT_FINISHED = "subagent_finished"
CURRENT_MODE_UPDATE = "current_mode_update"
RETRY_STATE = "retry_state"
GOAL_UPDATED = "goal_updated"
SESSION_RECAP = "session_recap"
AUTO_COMPACT_STARTED = "auto_compact_started"
AUTO_COMPACT_COMPLETED = "auto_compact_completed"
COMPACTION_CHECKPOINT = "compaction_checkpoint"
HOOK_EXECUTION = "hook_execution"
HOOK_ANNOTATION = "hook_annotation"

# ── events.jsonl runtime markers ──────────────────────────────────────────
TURN_STARTED = "turn_started"
TURN_ENDED = "turn_ended"
SESSION_ERROR = "session_error"
ERROR = "error"
TURN_ERROR = "turn_error"
FATAL_ERROR = "fatal_error"

# ── Groket-only (not emitted by Grok) ─────────────────────────────────────
SYSTEM = "system"

# Sets for filters / stats / segmentation
USER_TYPES = frozenset({USER_MESSAGE_CHUNK})
AGENT_TYPES = frozenset({AGENT_MESSAGE_CHUNK})
THOUGHT_TYPES = frozenset({AGENT_THOUGHT_CHUNK})
MESSAGE_TYPES = USER_TYPES | AGENT_TYPES | THOUGHT_TYPES
TOOL_CALL_TYPES = frozenset({TOOL_CALL})
TOOL_UPDATE_TYPES = frozenset({TOOL_CALL_UPDATE})
TOOL_TYPES = TOOL_CALL_TYPES | TOOL_UPDATE_TYPES
PLAN_TYPES = frozenset({PLAN})
SCHEDULED_TASK_TYPES = frozenset(
    {
        SCHEDULED_TASK_CREATED,
        SCHEDULED_TASK_UPDATED,
        SCHEDULED_TASK_FIRED,
        SCHEDULED_TASK_DELETED,
    }
)
TASK_TYPES = frozenset({TASK_BACKGROUNDED, TASK_COMPLETED}) | SCHEDULED_TASK_TYPES
SUBAGENT_TYPES = frozenset({SUBAGENT_SPAWNED, SUBAGENT_FINISHED})
TURN_BOUNDARY_TYPES = frozenset({TURN_STARTED, TURN_ENDED, TURN_COMPLETED})
TURN_STARTED_TYPES = frozenset({TURN_STARTED})
TURN_ENDED_TYPES = frozenset({TURN_ENDED})
ERROR_TYPES = frozenset({SESSION_ERROR, ERROR, TURN_ERROR, FATAL_ERROR})
MODE_TYPES = frozenset({CURRENT_MODE_UPDATE, RETRY_STATE})
GOAL_TYPES = frozenset({GOAL_UPDATED})
RECAP_TYPES = frozenset({SESSION_RECAP})
COMPACT_TYPES = frozenset({AUTO_COMPACT_STARTED, AUTO_COMPACT_COMPLETED, COMPACTION_CHECKPOINT})
HOOK_TYPES = frozenset({HOOK_EXECUTION, HOOK_ANNOTATION})
# Session chrome in the Turn / Session filter
SESSION_CHROME_TYPES = (
    TURN_BOUNDARY_TYPES
    | ERROR_TYPES
    | MODE_TYPES
    | GOAL_TYPES
    | RECAP_TYPES
    | COMPACT_TYPES
    | HOOK_TYPES
    | frozenset({SYSTEM})
)

# sessionUpdate values we materialize as timeline rows (1:1 identity map).
SESSION_UPDATE_TIMELINE_TYPES = frozenset(
    {
        USER_MESSAGE_CHUNK,
        AGENT_MESSAGE_CHUNK,
        AGENT_THOUGHT_CHUNK,
        TOOL_CALL,
        TOOL_CALL_UPDATE,
        PLAN,
        TASK_BACKGROUNDED,
        TASK_COMPLETED,
        SCHEDULED_TASK_CREATED,
        SCHEDULED_TASK_UPDATED,
        SCHEDULED_TASK_FIRED,
        SCHEDULED_TASK_DELETED,
        TURN_COMPLETED,
        SUBAGENT_SPAWNED,
        SUBAGENT_FINISHED,
        CURRENT_MODE_UPDATE,
        RETRY_STATE,
        GOAL_UPDATED,
        SESSION_RECAP,
        AUTO_COMPACT_STARTED,
        AUTO_COMPACT_COMPLETED,
        COMPACTION_CHECKPOINT,
        HOOK_EXECUTION,
        HOOK_ANNOTATION,
    }
)


def type_label(event_type: str) -> str:
    """Display label: Grok identifier with underscores → spaces."""
    et = (event_type or "").strip()
    if not et:
        return "?"
    return et.replace("_", " ")


def job_event_label(event_type: str, *, kind: str = "") -> str:
    """Honest timeline words for task / schedule bookends (not “subagent”)."""
    et = (event_type or "").strip()
    monitor = kind == "monitor"
    if et == TASK_BACKGROUNDED:
        return "monitor" if monitor else "background start"
    if et == TASK_COMPLETED:
        return "monitor done" if monitor else "background done"
    if et == SCHEDULED_TASK_CREATED:
        return "schedule created"
    if et == SCHEDULED_TASK_UPDATED:
        return "schedule updated"
    if et == SCHEDULED_TASK_FIRED:
        return "schedule fired"
    if et == SCHEDULED_TASK_DELETED:
        return "schedule deleted"
    if et.startswith("scheduled_task_"):
        return et.replace("_", " ")
    return ""


def event_kind(event_type: str) -> str:
    """Coarse role for UI color/layout: user|agent|thought|tool|tool_result|plan|error|session|system|other."""
    et = (event_type or "").strip()
    if et in USER_TYPES or et == "user":
        return "user"
    if et in AGENT_TYPES or et == "assistant":
        return "agent"
    if et in THOUGHT_TYPES or et == "thought":
        return "thought"
    if et in TOOL_CALL_TYPES:
        return "tool"
    if et in TOOL_UPDATE_TYPES or et == "tool_result":
        return "tool_result"
    if et in PLAN_TYPES:
        return "plan"
    if et in ERROR_TYPES:
        return "error"
    if et in SESSION_CHROME_TYPES - ERROR_TYPES - {SYSTEM} or et == "session":
        return "session"
    if et == SYSTEM:
        return "system"
    if et in SUBAGENT_TYPES or et == "subagent":
        return "subagent"
    if et in TASK_TYPES or et.startswith("scheduled_task_"):
        return "task"
    return "other"
