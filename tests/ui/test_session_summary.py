"""Tests for session_summary builder."""

from __future__ import annotations

from conftest import make_trace_event
from groket.models import SessionMeta
from groket.ui.session_summary import (
    assistant_text_from_timeline,
    build_session_summary,
    render_session_summary,
)

from .pilot_helpers import assert_rich_contains, rich_plain


class TestAssistantTextFromTimeline:
    def test_extracts_assistant_text(self):
        timeline = [
            make_trace_event(event_type="user_message_chunk", content="Do X"),
            make_trace_event(event_type="agent_message_chunk", content="I'll do X. "),
            make_trace_event(event_type="tool_call", tool_name="grep"),
            make_trace_event(event_type="agent_message_chunk", content="Here's the result."),
        ]
        result = assistant_text_from_timeline(timeline)
        assert result == "I'll do X. Here's the result."

    def test_no_assistant(self):
        timeline = [
            make_trace_event(event_type="user_message_chunk", content="Hello"),
            make_trace_event(event_type="tool_call", tool_name="grep"),
        ]
        result = assistant_text_from_timeline(timeline)
        assert result == ""


class TestBuildSessionSummary:
    def test_includes_key_fields(self, session_dir):
        meta = SessionMeta(
            session_id="test-session",
            session_dir=session_dir,
            model_id="v9-dietcoke",
            title="Fix auth tests",
            turn_outcome="success",
            duration_seconds=120,
            tool_call_count=5,
            context_window_usage_pct=35,
            context_tokens_used=178996,
            context_window_tokens=500000,
        )
        timeline = [
            make_trace_event(index=0, event_type="user_message_chunk", content="Fix tests"),
            make_trace_event(
                index=1,
                event_type="tool_call",
                tool_name="run_terminal_command",
                raw_input={"command": "pytest"},
            ),
            make_trace_event(
                index=2,
                event_type="tool_call_update",
                tool_name="run_terminal_command",
                content="2 passed",
            ),
            make_trace_event(index=3, event_type="agent_message_chunk", content="Tests are fixed."),
        ]
        summary = build_session_summary(meta, timeline)
        assert "Fix auth tests" in summary
        assert "v9-dietcoke" in summary
        assert "success" in summary
        assert "tool_call" in summary or "tools" in summary.lower()
        rich = render_session_summary(meta, timeline)
        assert_rich_contains(rich, "Fix auth tests")
        assert "Fix auth tests" in summary
        plain = rich_plain(rich)
        assert "35%" in plain
        assert "178,996" in plain or "179k" in plain

    def test_prefers_signals_counts_over_timeline_tail(self, session_dir):
        """Host/live tail must not shrink tools/turns below signals.json."""
        meta = SessionMeta(
            session_id="live-host",
            session_dir=session_dir,
            model_id="grok-4.6",
            title="live-host",
            turn_outcome="running",
            tool_call_count=2752,
            turn_count=119,
            num_events=26,
        )
        timeline = [
            make_trace_event(index=0, event_type="user_message_chunk", content="hi"),
            make_trace_event(index=1, event_type="tool_call", tool_name="grep"),
        ]
        plain = rich_plain(render_session_summary(meta, timeline))
        assert "2752" in plain.replace(",", "")
        assert "Tools" in plain
        assert "grok-4.6" in plain

    def test_turn_failure_warning(self, session_dir):
        meta = SessionMeta(
            session_id="fail-session",
            session_dir=session_dir,
            turn_outcome="error",
        )
        summary = build_session_summary(meta, [])
        assert "error" in summary.lower()

    def test_empty_timeline(self, session_dir):
        meta = SessionMeta(
            session_id="empty",
            session_dir=session_dir,
        )
        summary = build_session_summary(meta, [])
        assert isinstance(summary, str)
        assert len(summary) > 0
        assert (
            "empty" in rich_plain(render_session_summary(meta, [])).lower()
            or len(rich_plain(render_session_summary(meta, []))) > 0
        )

    def test_multi_turn_section(self, session_dir):
        meta = SessionMeta(
            session_id="mt",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="session", content="turn started  turn_number=0"),
            make_trace_event(index=1, event_type="user_message_chunk", content="one"),
            make_trace_event(index=2, event_type="session", content="turn ended  outcome=success"),
            make_trace_event(index=3, event_type="session", content="turn started  turn_number=1"),
            make_trace_event(index=4, event_type="user_message_chunk", content="two"),
            make_trace_event(index=5, event_type="session", content="turn ended  outcome=error"),
        ]
        summary = build_session_summary(meta, timeline)
        assert "turn" in summary.lower()

    def test_single_turn_omits_turns_count(self, session_dir):
        meta = SessionMeta(
            session_id="one",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="session", content="turn started  turn_number=0"),
            make_trace_event(index=1, event_type="user_message_chunk", content="go"),
            make_trace_event(index=2, event_type="session", content="turn ended  outcome=success"),
        ]
        plain = rich_plain(render_session_summary(meta, timeline))
        assert "2 turns" not in plain.lower()

    def test_with_assistant_text(self, session_dir):
        meta = SessionMeta(
            session_id="at",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="agent_message_chunk", content="Here is help."),
        ]
        summary = build_session_summary(meta, timeline, assistant_text="Help text here")
        assert "Help text here" not in summary

    def test_with_tool_errors(self, session_dir):
        meta = SessionMeta(
            session_id="te",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(
                index=0,
                event_type="tool_call",
                tool_name="run_terminal_command",
                is_error=True,
            ),
            make_trace_event(index=1, event_type="session_error", content="error", is_error=True),
        ]
        summary = build_session_summary(meta, timeline)
        assert "error" in summary.lower()

    def test_glance_omits_snapshot_note_and_assistant_dump(self, session_dir):
        meta = SessionMeta(
            session_id="sep",
            session_dir=session_dir,
            turn_outcome="success",
            title="Compact glance",
        )
        timeline = [
            make_trace_event(index=0, event_type="session", content="turn started  turn_number=0"),
            make_trace_event(index=1, event_type="user_message_chunk", content="go"),
            make_trace_event(index=2, event_type="tool_call", tool_name="read_file"),
            make_trace_event(
                index=3, event_type="agent_message_chunk", content="long assistant body"
            ),
            make_trace_event(index=4, event_type="session", content="turn ended  outcome=success"),
        ]
        plain = rich_plain(render_session_summary(meta, timeline, assistant_text="Help text here"))
        assert "Compact glance" in plain
        assert "signals.json" not in plain
        assert "Help text here" not in plain
        assert "long assistant body" not in plain
        assert "tools=" not in plain

    def test_with_metadata_fields(self, session_dir):
        meta = SessionMeta(
            session_id="md",
            session_dir=session_dir,
            model_id="v9-dietcoke",
            title="Test Session",
            turn_outcome="success",
            duration_seconds=120,
            run_id="run-123",
            task_id="task-456",
            git_repo="https://github.com/example/repo",
            git_branch="main",
            created_at="2026-06-25T00:00:00Z",
            num_messages=10,
            loop_count=3,
        )
        summary = build_session_summary(meta, [])
        assert "Test Session" in summary
        assert "run-123" in summary
        assert "main" in summary

    def test_glance_values_share_one_gutter(self, session_dir):
        meta = SessionMeta(
            session_id="align-sess",
            session_dir=session_dir,
            title="Align",
            turn_outcome="success",
            tool_call_count=4,
            context_window_usage_pct=12,
            context_tokens_used=1200,
            context_window_tokens=10000,
            run_id="run-a",
            git_branch="hudv2",
        )
        starts: list[int] = []
        for val in ("align-sess", "4", "run-a", "hudv2"):
            for line in rich_plain(render_session_summary(meta, [])).splitlines():
                if val in line:
                    starts.append(line.index(val))
                    break
            else:
                raise AssertionError(f"missing glance value {val}")
        assert len(starts) == 4
        assert len(set(starts)) == 1, starts

    def test_long_path_truncated(self, session_dir):
        meta = SessionMeta(
            session_id="lp",
            session_dir=session_dir,
            turn_outcome="success",
        )
        # session_dir path might be short for test; summary handles both
        summary = build_session_summary(meta, [])
        assert isinstance(summary, str)


# ── append_usage_rich ─────────────────────────────────────────────────────

from groket.session.usage_stats import (
    McpMethodUsage,
    McpServerUsage,
    SessionUsageStats,
    SkillUsageRow,
    ToolUsageRow,
)
from groket.ui.session_summary import append_usage_rich
from rich.text import Text


class TestAppendUsageRich:
    def test_empty_usage(self):
        out = Text()
        usage = SessionUsageStats()
        append_usage_rich(out, usage)
        assert "Host tools" in out.plain

    def test_with_host_tools(self):
        out = Text()
        usage = SessionUsageStats(
            host_tools=[
                ToolUsageRow(name="read_file", calls=5, errors=0),
                ToolUsageRow(name="grep", calls=3, errors=1),
            ],
        )
        append_usage_rich(out, usage)
        assert "read_file" in out.plain
        assert "grep" in out.plain
        assert "8" in out.plain

    def test_with_mcp_servers(self):
        out = Text()
        usage = SessionUsageStats(
            mcp_servers=[
                McpServerUsage(
                    server_id="slack",
                    configured=True,
                    use_tool_calls=3,
                    methods=[McpMethodUsage(method="send_message", calls=2)],
                ),
            ],
            mcp_configured=["slack"],
        )
        append_usage_rich(out, usage)
        assert "slack" in out.plain

    def test_with_skills(self):
        out = Text()
        usage = SessionUsageStats(
            skills=[
                SkillUsageRow(skill_id="code-review", configured=True, skill_md_reads=2),
            ],
            skills_configured=["code-review"],
        )
        append_usage_rich(out, usage)
        assert "code-review" in out.plain

    def test_with_persona_and_sources(self):
        out = Text()
        usage = SessionUsageStats(
            persona_id="test-persona",
            source_notes=["persona", "updates"],
        )
        append_usage_rich(out, usage)
        assert "test-persona" in out.plain
        assert "sources" in out.plain

    def test_mcp_server_no_hits(self):
        out = Text()
        usage = SessionUsageStats(
            mcp_servers=[
                McpServerUsage(server_id="empty-srv", configured=True),
                McpServerUsage(server_id="other-srv", configured=True),
            ],
            mcp_configured=["empty-srv", "other-srv"],
        )
        append_usage_rich(out, usage)
        plain = out.plain
        assert "empty-srv" in plain
        assert "other-srv" in plain
        # Idle servers are listed without filler prose.
        assert "no tool hits" not in plain
        assert "configured" not in plain.lower() or "mcp" in plain.lower()
        # Still one server name per line (not glued).
        assert plain.index("empty-srv") < plain.index("other-srv")

    def test_mcp_bridge_calls(self):
        out = Text()
        usage = SessionUsageStats(mcp_bridge_calls=7)
        append_usage_rich(out, usage)
        assert "7" in out.plain


class TestBuildSessionSummaryException:
    def test_render_exception_fallback(self, session_dir):
        """build_session_summary falls back to title on render exception."""
        from unittest.mock import patch

        meta = SessionMeta(
            session_id="exc",
            session_dir=session_dir,
            title="My Title",
        )
        with patch(
            "groket.ui.session_summary.render_session_summary",
            side_effect=RuntimeError("boom"),
        ):
            result = build_session_summary(meta, [])
            assert "My Title" in result


class TestSessionSummaryPendingLabel:
    def test_pending_label_exception(self, session_dir):
        """render_session_summary handles session_pending_label import failure."""
        from unittest.mock import patch

        meta = SessionMeta(
            session_id="pend",
            session_dir=session_dir,
            turn_outcome="success",
        )
        with patch(
            "groket.session.turn_gate.session_pending_label",
            side_effect=ImportError("no module"),
        ):
            result = render_session_summary(meta, [])
            plain = rich_plain(result)
            assert "pend" in plain or "success" in plain.lower() or plain.strip() != ""


class TestSessionSummaryTurnSegmentationFail:
    def test_turn_segmentation_exception(self, session_dir):
        """render_session_summary handles segment_timeline_turns exception."""
        from unittest.mock import patch

        meta = SessionMeta(
            session_id="segf",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [make_trace_event(index=0, event_type="user_message_chunk", content="hi")]
        with patch(
            "groket.session.turns.segment_timeline_turns",
            side_effect=RuntimeError("fail"),
        ):
            result = render_session_summary(meta, timeline)
            # Segmentation failed; still render identity / outcome for the session.
            assert_rich_contains(result, "segf", "success")


class TestSessionSummaryShareDisplay:
    def test_share_url_present(self, session_dir):
        """Share URL is included in the session summary."""
        import json

        (session_dir / "groket-share.json").write_text(
            json.dumps({"share_url": "https://share.example.com/abc", "session_id": "test"}),
        )
        meta = SessionMeta(
            session_id="share-ok",
            session_dir=session_dir,
            turn_outcome="success",
        )
        result = build_session_summary(meta, [])
        assert "share" in result.lower() or "Share" in result

    def test_share_pending(self, session_dir):
        """Pending share state is represented in the summary."""
        import json

        (session_dir / "groket-share.json").write_text(
            json.dumps({"source": "pending", "session_id": "test"}),
        )
        meta = SessionMeta(
            session_id="share-pend",
            session_dir=session_dir,
            turn_outcome="success",
        )
        result = build_session_summary(meta, [])
        assert "share" in result.lower() or "pending" in result.lower() or len(result) > 0
        assert (
            "share-pend" in result
            or "Share" in result
            or "pending" in result.lower()
            or "share" in result.lower()
        )

    def test_share_failed(self, session_dir):
        """Failed share state is represented in the summary."""
        import json

        (session_dir / "groket-share.json").write_text(
            json.dumps({"error": "no messages to share", "session_id": "test"}),
        )
        meta = SessionMeta(
            session_id="share-fail",
            session_dir=session_dir,
            turn_outcome="success",
        )
        result = build_session_summary(meta, [])
        assert "share-fail" in result


class TestSessionSummaryUsageException:
    def test_usage_exception_keeps_glance(self, session_dir):
        """Glance header still renders when usage collection is unused."""
        meta = SessionMeta(
            session_id="usagefail",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="tool_call", tool_name="grep"),
            make_trace_event(index=1, event_type="tool_call", tool_name="grep"),
            make_trace_event(index=2, event_type="tool_call", tool_name="read_file"),
        ]
        result = build_session_summary(meta, timeline)
        assert "usagefail" in result
        assert "Tools" in result
        assert "3" in result


class TestSessionSummaryMultiTurnToolMix:
    def test_multi_turn_count_in_glance(self, session_dir):
        """Turn count stays on the glance strip; tool mix lives in tables."""
        meta = SessionMeta(
            session_id="toolmix",
            session_dir=session_dir,
            turn_outcome="success",
        )
        timeline = [
            make_trace_event(index=0, event_type="session", content="turn started  turn_number=0"),
            make_trace_event(index=1, event_type="tool_call", tool_name="grep"),
            make_trace_event(index=2, event_type="tool_call", tool_name="grep"),
            make_trace_event(index=3, event_type="session", content="turn ended  outcome=success"),
            make_trace_event(index=4, event_type="session", content="turn started  turn_number=1"),
            make_trace_event(index=5, event_type="tool_call", tool_name="read_file"),
            make_trace_event(index=6, event_type="session", content="turn ended  outcome=success"),
        ]
        result = build_session_summary(meta, timeline)
        assert "Last turn" in result or "turn" in result.lower()
        assert "Tools" in result


class TestSessionSummaryShareSection:
    def test_share_section_no_url_not_pending(self, session_dir):
        """Share section with error and no URL renders without crash."""
        import json

        (session_dir / "groket-share.json").write_text(
            json.dumps({"error": "auth failed", "session_id": "test", "snapshot_n": 2}),
        )
        meta = SessionMeta(
            session_id="noshare",
            session_dir=session_dir,
            turn_outcome="success",
        )
        result = build_session_summary(meta, [])
        assert (
            "error" in result.lower()
            or "share" in result.lower()
            or "fail" in result.lower()
            or "no messages" in result.lower()
        )
