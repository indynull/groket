"""Tests for core data models."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from groket.models import (
    EvalRun,
    Flag,
    FlagVerdict,
    SessionMeta,
    ToolCall,
    TraceEvent,
)

# ── FlagVerdict ───────────────────────────────────────────────────────────


class TestFlagVerdict:
    def test_all_values(self):
        assert FlagVerdict.BAD.value == "bad"
        assert FlagVerdict.ACCEPTABLE.value == "acceptable"
        assert FlagVerdict.GOOD.value == "good"
        assert FlagVerdict.NEEDS_REVIEW.value == "needs_review"


# ── ToolCall ──────────────────────────────────────────────────────────────


class TestToolCall:
    def test_construction(self):
        tc = ToolCall(
            call_id="abc",
            tool_name="grep",
            raw_input={"pattern": "foo"},
            timestamp=1000,
        )
        assert tc.call_id == "abc"
        assert tc.tool_name == "grep"
        assert tc.raw_input.raw() == {"pattern": "foo"}
        assert tc.result_content == ""
        assert tc.is_error is False
        assert tc.exit_code is None
        assert tc.signal is None

    def test_error_tool_call(self):
        tc = ToolCall(
            call_id="err",
            tool_name="run_terminal_command",
            raw_input={"command": "false"},
            is_error=True,
            exit_code=1,
            signal="SIGTERM",
        )
        assert tc.is_error is True
        assert tc.exit_code == 1
        assert tc.signal == "SIGTERM"


# ── TraceEvent ────────────────────────────────────────────────────────────


class TestTraceEvent:
    def test_time_str_none(self):
        ev = TraceEvent(index=0, event_type="user_message_chunk")
        assert ev.time_str == ""

    def test_time_str_valid(self):
        ts = int(datetime(2026, 6, 25, 12, 30, 45, tzinfo=UTC).timestamp())
        ev = TraceEvent(index=0, event_type="user_message_chunk", timestamp=ts)
        assert ev.time_str == "12:30:45"

    def test_time_str_bad_value(self):
        ev = TraceEvent(index=0, event_type="user_message_chunk", timestamp=-9999999999999)
        # Should not raise, returns string form
        assert ev.time_str != ""

    def test_type_label(self):
        assert (
            TraceEvent(index=0, event_type="user_message_chunk").type_label == "user message chunk"
        )
        assert (
            TraceEvent(index=0, event_type="agent_message_chunk").type_label
            == "agent message chunk"
        )
        assert TraceEvent(index=0, event_type="tool_call").type_label == "tool call"
        assert TraceEvent(index=0, event_type="tool_call_update").type_label == "tool call update"
        assert TraceEvent(index=0, event_type="session_error").type_label == "session error"

    def test_type_label_unknown(self):
        ev = TraceEvent(index=0, event_type="custom_type")
        assert ev.type_label == "custom type"

    def test_summary_line_tool_call_command(self):
        ev = TraceEvent(
            index=0,
            event_type="tool_call",
            tool_name="run_terminal_command",
            raw_input={"command": "pytest tests/"},
        )
        assert "run_terminal_command" in ev.summary_line
        assert "pytest" in ev.summary_line

    def test_summary_line_tool_call_grep(self):
        ev = TraceEvent(
            index=0,
            event_type="tool_call",
            tool_name="grep",
            raw_input={"pattern": "def main", "path": "src/"},
        )
        assert "grep" in ev.summary_line
        assert "def main" in ev.summary_line

    def test_summary_line_tool_call_file(self):
        ev = TraceEvent(
            index=0,
            event_type="tool_call",
            tool_name="read_file",
            raw_input={"target_file": "src/main.py"},
        )
        assert "src/main.py" in ev.summary_line

    def test_summary_line_tool_result(self):
        ev = TraceEvent(
            index=0,
            event_type="tool_call_update",
            tool_name="grep",
            content="line1\nline2\nline3",
        )
        assert "grep" in ev.summary_line
        # Should contain char count
        assert "chars" in ev.summary_line

    def test_summary_line_session(self):
        ev = TraceEvent(
            index=0,
            event_type="turn_started",
            content="turn started  model=v9-dietcoke",
        )
        assert "turn started" in ev.summary_line

    def test_summary_line_assistant(self):
        ev = TraceEvent(
            index=0, event_type="agent_message_chunk", content="I'll help you fix that."
        )
        assert "fix" in ev.summary_line


# ── SessionMeta ───────────────────────────────────────────────────────────


class TestSessionMeta:
    def test_label_with_title(self):
        meta = SessionMeta(session_id="abc123", session_dir=Path("/tmp/s"), title="My session")
        assert meta.label == "My session"

    def test_label_without_title(self):
        meta = SessionMeta(session_id="abcdef1234567890abcd", session_dir=Path("/tmp/s"))
        assert meta.label == "abcdef1234567890abcd"

    def test_duration_str(self):
        meta = SessionMeta(session_id="x", session_dir=Path("/tmp"), duration_seconds=155)
        assert meta.duration_str == "2m35s"

    def test_context_usage_str(self):
        meta = SessionMeta(
            session_id="x",
            session_dir=Path("/tmp"),
            context_window_usage_pct=35,
            context_tokens_used=178996,
            context_window_tokens=500000,
        )
        assert meta.has_context_usage is True
        assert meta.context_usage_str == "35% (178,996 / 500,000)"
        assert "35%" in meta.context_usage_compact
        assert "500k" in meta.context_usage_compact
        empty = SessionMeta(session_id="y", session_dir=Path("/tmp"))
        assert empty.has_context_usage is False
        assert empty.context_usage_str == ""
        assert empty.context_usage_compact == ""

    def test_turn_in_progress(self):
        meta = SessionMeta(session_id="x", session_dir=Path("/tmp"), turn_outcome="running")
        assert meta.turn_in_progress is True
        meta2 = SessionMeta(session_id="x", session_dir=Path("/tmp"), turn_outcome="success")
        assert meta2.turn_in_progress is False

    def test_turn_failed_success(self):
        meta = SessionMeta(session_id="x", session_dir=Path("/tmp"), turn_outcome="success")
        assert meta.turn_failed is False

    def test_turn_failed_error(self):
        meta = SessionMeta(session_id="x", session_dir=Path("/tmp"), turn_outcome="error")
        assert meta.turn_failed is True

    def test_turn_failed_empty(self):
        meta = SessionMeta(session_id="x", session_dir=Path("/tmp"), turn_outcome="")
        assert meta.turn_failed is False

    def test_turn_failed_running_is_not_failure(self):
        meta = SessionMeta(session_id="x", session_dir=Path("/tmp"), turn_outcome="running")
        assert meta.turn_failed is False

    def test_ending_status_is_in_progress_not_failed(self):
        meta = SessionMeta(session_id="x", session_dir=Path("/tmp"), turn_outcome="ending")
        assert meta.turn_in_progress is True
        assert meta.turn_failed is False
        assert meta.list_status_label() == "ending"


# ── Flag (Pydantic) ──────────────────────────────────────────────────────


class TestFlag:
    def test_construction(self):
        flag = Flag(event_index=5, verdict=FlagVerdict.BAD, description="Wrong tool used")
        assert flag.event_index == 5
        assert flag.verdict == FlagVerdict.BAD
        assert flag.description == "Wrong tool used"

    def test_serialization_roundtrip(self):
        flag = Flag(
            event_index=3,
            verdict=FlagVerdict.GOOD,
            description="Good approach",
            tool_name="read_file",
        )
        data = flag.model_dump()
        restored = Flag.model_validate(data)
        assert restored.event_index == flag.event_index
        assert restored.verdict == flag.verdict
        assert restored.description == flag.description
        assert restored.tool_name == flag.tool_name


# ── EvalRun (Pydantic) ───────────────────────────────────────────────────


class TestEvalRun:
    def test_defaults(self):
        run = EvalRun(run_id="r1", prompt="do stuff")
        assert run.docker_image == "fully-loaded"
        assert run.models == []
        assert run.status == "pending"

    def test_with_models(self):
        run = EvalRun(
            run_id="r2",
            prompt="test",
            models=["v9-dietcoke", "v9-bottlerocket"],
            parallelism=2,
        )
        assert len(run.models) == 2
        assert run.parallelism == 2


# --- merged ---


from groket.models import (
    ParamBag,
    ToolInputBag,
    as_json_object,
    json_as_bool,
    json_as_float,
    json_as_int,
    json_as_list,
    json_as_mapping_list,
    json_as_object,
    json_as_str,
    json_as_str_list,
    json_value_from_unknown,
)


class TestJsonAsHelpers:
    def test_json_as_str_variants(self):
        assert json_as_str(None, "d") == "d"
        assert json_as_str("hi") == "hi"
        assert json_as_str(True) == "true"
        assert json_as_str(False) == "false"
        assert json_as_str(42) == "42"
        assert json_as_str(3.5) == "3.5"
        assert '"a"' in json_as_str({"a": 1}) or "a" in json_as_str({"a": 1})

    def test_json_as_int_variants(self):
        assert json_as_int(None, 9) == 9
        assert json_as_int(True) == 1
        assert json_as_int(7) == 7
        assert json_as_int(3.9) == 3
        assert json_as_int("12") == 12
        assert json_as_int("nope", 5) == 5
        assert json_as_int([1], 0) == 0

    def test_json_as_float_variants(self):
        assert json_as_float(None, 1.5) == 1.5
        assert json_as_float(True) == 1.0
        assert json_as_float(2) == 2.0
        assert json_as_float(2.5) == 2.5
        assert json_as_float("3.25") == 3.25
        assert json_as_float("x", 0.1) == 0.1
        assert json_as_float({}, 0.0) == 0.0

    def test_json_as_bool_variants(self):
        assert json_as_bool(None, True) is True
        assert json_as_bool(True) is True
        assert json_as_bool(0) is False
        assert json_as_bool(2) is True
        assert json_as_bool("YES") is True
        assert json_as_bool("on") is True
        assert json_as_bool("no") is False
        assert json_as_bool([]) is False

    def test_json_as_str_list(self):
        assert json_as_str_list(None) == []
        assert json_as_str_list(None, ["a"]) == ["a"]
        assert json_as_str_list("solo") == ["solo"]
        assert json_as_str_list(["x", 1]) == ["x", "1"]
        assert json_as_str_list(99, ["d"]) == ["d"]

    def test_json_as_object_list_mapping(self):
        assert json_as_object(None) == {}
        assert json_as_object({"k": 1}) == {"k": 1}
        assert json_as_list(None) == []
        assert json_as_list([1, "a"]) == [1, "a"]
        assert json_as_list("x") == []
        assert json_as_mapping_list([{"a": 1}, "skip", 3]) == [{"a": 1}]

    def test_json_value_from_unknown_and_as_json_object(self):
        assert json_value_from_unknown(None) is None
        assert json_value_from_unknown("s") == "s"
        assert json_value_from_unknown(Path("/tmp/x")) == "/tmp/x"
        nested = json_value_from_unknown({"p": Path("/a"), "t": (1, "b")})
        assert isinstance(nested, dict)
        assert nested["p"] == "/a"
        assert nested["t"] == [1, "b"]
        assert json_value_from_unknown(object())  # falls back to str
        obj = as_json_object({"n": 1, "path": Path("/z")})
        assert obj["n"] == 1
        assert obj["path"] == "/z"


class TestParamBag:
    def test_ensure_and_accessors(self):
        p = ParamBag({"s": "hi", "i": "3", "f": "2.5", "b": "yes", "lst": ["a", 2], "m": {"x": 1}})
        assert ParamBag.ensure(p) is p
        wrapped = ParamBag.ensure({"k": 1})
        assert wrapped.as_int("k") == 1
        assert "s" in p
        assert 1 not in p
        assert p.has("s")
        assert p.get("missing") is None
        assert p.get("s") == "hi"
        assert set(p.keys()) == {"s", "i", "f", "b", "lst", "m"}
        assert "hi" in list(p.values())
        assert ("s", "hi") in list(p.items())
        assert p.raw()["s"] == "hi"
        assert p.as_str("s") == "hi"
        assert p.as_str_opt("s") == "hi"
        assert p.as_str_opt("nope") is None
        assert p.as_int("i") == 3
        assert p.as_int_opt("i") == 3
        assert p.as_int_opt("nope") is None
        assert p.as_float("f") == 2.5
        assert p.as_bool("b") is True
        assert p.as_str_list("lst") == ["a", "2"]
        assert p.as_str_dict("m") == {"x": "1"}
        assert p.mapping("m").as_int("x") == 1
        assert p.as_int_list("lst") == [0, 2]
        assert p.as_int_list("missing", [9]) == [9]
        assert p.as_int_list("s", [1]) == [1]

    def test_tool_input_bag(self):
        t = ToolInputBag({"command": "echo", "n": 2})
        assert t.as_str("command") == "echo"
        assert t.as_int("n") == 2


class TestToolCallAndTraceEventEdges:
    def test_tool_call_with_bag(self):
        tc = ToolCall(call_id="c", tool_name="t", raw_input={"a": 1})
        assert tc.raw_input.as_int("a") == 1

    def test_tool_call_inputs_method(self):
        """ToolCall.inputs() returns ToolInputBag from dict (lines 350-352)."""
        tc = ToolCall(call_id="c", tool_name="t", raw_input={"x": 1})
        bag = tc.inputs()
        assert bag.as_int("x") == 1
        # Already a ToolInputBag — returns it directly
        bag2 = tc.inputs()
        assert bag2.as_int("x") == 1

    def test_tool_call_inputs_dict_and_fallback(self):
        """inputs() dict and non-dict branches via __setattr__ bypass (lines 350-352)."""
        from groket.models import ToolInputBag

        tc = ToolCall(call_id="c", tool_name="t", raw_input={})
        # Force raw_input to a plain dict (bypass __post_init__)
        object.__setattr__(tc, "raw_input", {"k": 5})
        bag = tc.inputs()
        assert bag.as_int("k") == 5
        # Force to non-dict/non-ToolInputBag
        object.__setattr__(tc, "raw_input", "bad")
        bag2 = tc.inputs()
        assert isinstance(bag2, ToolInputBag)

    def test_trace_event_summary_edges(self):
        ev = TraceEvent(index=0, event_type="tool_call", tool_name="x", raw_input={})
        assert "x" in ev.summary_line
        ev2 = TraceEvent(index=0, event_type="user_message_chunk", content="hello world")
        assert "hello" in ev2.summary_line
        ev3 = TraceEvent(index=0, event_type="session_error", content="boom")
        assert ev3.type_label

    def test_trace_event_summary_file_path(self):
        """Summary line with file_path input key (line 422)."""
        ev = TraceEvent(
            index=0,
            event_type="tool_call",
            tool_name="search_replace",
            raw_input={"file_path": "src/app.py", "old_string": "x", "new_string": "y"},
        )
        assert "src/app.py" in ev.summary_line

    def test_trace_event_summary_prompt(self):
        """Summary line with prompt input key."""
        ev = TraceEvent(
            index=0,
            event_type="tool_call",
            tool_name="image_gen",
            raw_input={"prompt": "A beautiful landscape with mountains"},
        )
        assert "image_gen" in ev.summary_line
        assert "landscape" in ev.summary_line
