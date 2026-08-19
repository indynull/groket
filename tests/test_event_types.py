"""Grok-aligned timeline event type taxonomy."""

from __future__ import annotations

import json
from pathlib import Path

from groket import event_types as et
from groket.parser import parse_timeline


def test_session_update_types_are_identity_mapped(tmp_path: Path) -> None:
    sd = tmp_path / "s"
    sd.mkdir()
    lines = [
        {
            "timestamp": 1,
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "task_backgrounded", "tool_call_id": "c1"}},
        },
        {
            "timestamp": 2,
            "method": "session/update",
            "params": {
                "update": {"sessionUpdate": "task_completed", "task_snapshot": {"ok": True}}
            },
        },
        {
            "timestamp": 3,
            "method": "session/update",
            "params": {"update": {"sessionUpdate": "turn_completed", "prompt_id": "p1"}},
        },
        {
            "timestamp": 4,
            "method": "session/update",
            "params": {
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "hi"},
                }
            },
        },
    ]
    (sd / "updates.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )
    types = {e.event_type for e in parse_timeline(sd)}
    assert et.TASK_BACKGROUNDED in types
    assert et.TASK_COMPLETED in types
    assert et.TURN_COMPLETED in types
    assert et.USER_MESSAGE_CHUNK in types


def test_goal_updated_is_session_chrome() -> None:
    assert et.GOAL_UPDATED in et.SESSION_UPDATE_TIMELINE_TYPES
    assert et.GOAL_UPDATED in et.SESSION_CHROME_TYPES
    assert et.event_kind(et.GOAL_UPDATED) == "session"


def test_hooks_are_session_chrome() -> None:
    for name in (et.HOOK_EXECUTION, et.HOOK_ANNOTATION):
        assert name in et.SESSION_UPDATE_TIMELINE_TYPES
        assert name in et.SESSION_CHROME_TYPES
        assert et.event_kind(name) == "session"


def test_recap_and_compact_are_session_chrome() -> None:
    for name in (
        et.SESSION_RECAP,
        et.AUTO_COMPACT_STARTED,
        et.AUTO_COMPACT_COMPLETED,
        et.COMPACTION_CHECKPOINT,
    ):
        assert name in et.SESSION_UPDATE_TIMELINE_TYPES
        assert name in et.SESSION_CHROME_TYPES
        assert et.event_kind(name) == "session"


def test_type_label_uses_grok_names() -> None:
    assert et.type_label("user_message_chunk") == "user message chunk"
    assert et.type_label("tool_call_update") == "tool call update"
    assert et.type_label("subagent_spawned") == "subagent spawned"
    assert et.type_label("subagent_finished") == "subagent finished"


def test_job_event_label_is_honest_not_subagent() -> None:
    assert et.job_event_label("task_backgrounded") == "background start"
    assert et.job_event_label("task_backgrounded", kind="monitor") == "monitor"
    assert et.job_event_label("task_completed") == "background done"
    assert et.job_event_label("scheduled_task_created") == "schedule created"
    assert et.type_label("task_backgrounded") == "task backgrounded"
    assert et.job_event_label("scheduled_task_created") == "schedule created"
    assert "subagent" not in et.job_event_label("task_backgrounded")


def test_scheduled_task_types_are_timeline_task_kind() -> None:
    for name in (
        et.SCHEDULED_TASK_CREATED,
        et.SCHEDULED_TASK_UPDATED,
        et.SCHEDULED_TASK_FIRED,
        et.SCHEDULED_TASK_DELETED,
    ):
        assert name in et.SESSION_UPDATE_TIMELINE_TYPES
        assert name in et.TASK_TYPES
        assert et.event_kind(name) == "task"
        assert et.event_kind(name) != "subagent"
    assert et.event_kind("scheduled_task_paused") == "task"
    assert et.SCHEDULED_TASK_CREATED in et.SESSION_UPDATE_TIMELINE_TYPES
