"""Shared fixtures built from real trace data patterns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.models import (
    ToolCall,
    TraceEvent,
)

# ── Atomic model factories ────────────────────────────────────────────────


def make_tool_call(
    *,
    call_id: str = "call-001",
    tool_name: str = "run_terminal_command",
    raw_input: dict | None = None,
    timestamp: int | None = 1782347263,
    result_content: str = "",
    is_error: bool = False,
    update_index: int = 0,
    exit_code: int | None = None,
    signal: str | None = None,
) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_name=tool_name,
        raw_input=raw_input or {"command": "echo hello"},
        timestamp=timestamp,
        result_content=result_content,
        is_error=is_error,
        update_index=update_index,
        exit_code=exit_code,
        signal=signal,
    )


def make_trace_event(
    *,
    index: int = 0,
    event_type: str = "tool_call",
    timestamp: int | None = 1782347263,
    content: str = "",
    tool_name: str = "",
    tool_call_id: str = "",
    raw_input: dict | None = None,
    is_error: bool = False,
    update_index: int = 0,
) -> TraceEvent:
    return TraceEvent(
        index=index,
        event_type=event_type,
        timestamp=timestamp,
        content=content,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        raw_input=raw_input or {},
        is_error=is_error,
        update_index=update_index,
    )


# ── Session directory fixtures ────────────────────────────────────────────

SAMPLE_SUMMARY = {
    "info": {
        "id": "019efc2c-cc09-7483-84de-5dbbed3c91bb",
        "cwd": "/workspace",
        "git_repo_url": "https://github.com/example/repo",
        "git_branch": "main",
    },
    "session_summary": "Test session summary text",
    "created_at": "2026-06-25T00:27:34.801479824Z",
    "updated_at": "2026-06-25T00:27:50.604344768Z",
    "num_messages": 9,
    "current_model_id": "v9-dietcoke",
    "generated_title": "Fix unit tests for auth module",
}

SAMPLE_SIGNALS = {
    "toolCallCount": 5,
    "toolFailureCount": 1,
    "errorCount": 1,
    "doomLoopWarnings": 0,
    "sessionDurationSeconds": 155,
    "agentLinesAdded": 42,
    "agentLinesRemoved": 10,
    "contextWindowUsage": 35,
    "contextTokensUsed": 178996,
    "contextWindowTokens": 500000,
    "compactionCount": 0,
    "totalTokensBeforeCompaction": 0,
}

SAMPLE_RUN = {
    "run_id": "82bbd2e26e89",
    "created_at": "2026-06-25T00:28:02.192520+00:00",
    "prompt": "Fix the unit tests",
    "repo_url": "https://github.com/example/repo",
    "repo_branch": "main",
    "docker_image": "fully-loaded",
    "setup_instructions": "",
    "models": ["v9-dietcoke"],
    "sessions": {},
}


def _write_updates_jsonl(session_dir: Path) -> None:
    """Write a minimal updates.jsonl with a few tool calls."""
    updates = [
        {
            "timestamp": 1782347263,
            "method": "session/update",
            "params": {
                "sessionId": session_dir.name,
                "update": {
                    "sessionUpdate": "user_message_chunk",
                    "content": {"type": "text", "text": "Fix the tests"},
                },
            },
        },
        {
            "timestamp": 1782347270,
            "method": "session/update",
            "params": {
                "sessionId": session_dir.name,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-aaa",
                    "title": "run_terminal_command",
                    "rawInput": {"command": "pytest tests/"},
                },
            },
        },
        {
            "timestamp": 1782347275,
            "method": "session/update",
            "params": {
                "sessionId": session_dir.name,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-aaa",
                    "status": "completed",
                    "content": "FAILED 2 tests",
                    "isError": True,
                },
            },
        },
        {
            "timestamp": 1782347280,
            "method": "session/update",
            "params": {
                "sessionId": session_dir.name,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-bbb",
                    "title": "read_file",
                    "rawInput": {"target_file": "tests/test_auth.py"},
                },
            },
        },
        {
            "timestamp": 1782347285,
            "method": "session/update",
            "params": {
                "sessionId": session_dir.name,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-bbb",
                    "status": "completed",
                    "content": "def test_login():\n    assert True",
                },
            },
        },
        {
            "timestamp": 1782347290,
            "method": "session/update",
            "params": {
                "sessionId": session_dir.name,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "call-ccc",
                    "title": "search_replace",
                    "rawInput": {
                        "file_path": "tests/test_auth.py",
                        "old_string": "assert True",
                        "new_string": "assert response.status_code == 200",
                    },
                },
            },
        },
        {
            "timestamp": 1782347295,
            "method": "session/update",
            "params": {
                "sessionId": session_dir.name,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": "call-ccc",
                    "status": "completed",
                    "content": "File updated successfully",
                },
            },
        },
    ]
    with open(session_dir / "updates.jsonl", "w") as f:
        for u in updates:
            f.write(json.dumps(u) + "\n")


def _write_events_jsonl(session_dir: Path, *, outcome: str = "success") -> None:
    """Write events.jsonl with turn lifecycle markers."""
    events = [
        {
            "ts": "2026-06-25T00:27:35.281Z",
            "type": "turn_started",
            "session_id": session_dir.name,
            "turn_number": 0,
            "model_id": "v9-dietcoke",
        },
        {"ts": "2026-06-25T00:27:35.283Z", "type": "loop_started", "loop_index": 0},
        {"ts": "2026-06-25T00:27:43.144Z", "type": "first_token"},
        {
            "ts": "2026-06-25T00:27:50.342Z",
            "type": "turn_ended",
            "outcome": outcome,
        },
    ]
    with open(session_dir / "events.jsonl", "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _write_chat_history(session_dir: Path) -> None:
    """Write minimal chat_history.jsonl."""
    messages = [
        {"type": "system", "content": "You are a helpful assistant."},
        {
            "type": "user",
            "content": [{"type": "text", "text": "<user_query>Fix the unit tests</user_query>"}],
        },
        {
            "type": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "I'll fix the failing tests by updating assertions.",
                }
            ],
        },
    ]
    with open(session_dir / "chat_history.jsonl", "w") as f:
        for m in messages:
            f.write(json.dumps(m) + "\n")


@pytest.fixture()
def session_dir(tmp_path: Path) -> Path:
    """A realistic session directory with all trace files."""
    sd = tmp_path / "019efc2c-session-test"
    sd.mkdir()
    (sd / "summary.json").write_text(json.dumps(SAMPLE_SUMMARY))
    (sd / "signals.json").write_text(json.dumps(SAMPLE_SIGNALS))
    (sd / "run.json").write_text(json.dumps(SAMPLE_RUN))
    _write_updates_jsonl(sd)
    _write_events_jsonl(sd)
    _write_chat_history(sd)
    return sd


@pytest.fixture()
def empty_session_dir(tmp_path: Path) -> Path:
    """A session directory with only summary.json (minimal)."""
    sd = tmp_path / "empty-session"
    sd.mkdir()
    (sd / "summary.json").write_text(json.dumps(SAMPLE_SUMMARY))
    return sd


@pytest.fixture()
def error_session_dir(tmp_path: Path) -> Path:
    """A session directory where the turn ended with an error."""
    sd = tmp_path / "error-session"
    sd.mkdir()
    (sd / "summary.json").write_text(json.dumps(SAMPLE_SUMMARY))
    (sd / "signals.json").write_text(
        json.dumps({**SAMPLE_SIGNALS, "errorCount": 3, "toolFailureCount": 2})
    )
    _write_updates_jsonl(sd)
    _write_events_jsonl(sd, outcome="error")
    _write_chat_history(sd)
    return sd


@pytest.fixture()
def traces_root(tmp_path: Path, session_dir: Path) -> Path:
    """A traces root containing one session.

    Real directory tree (not a symlink): ``find_sessions`` walks with
    ``followlinks=False`` so host symlink cycles never explode the catalog.
    """
    import shutil

    root = tmp_path / "runs" / "traces"
    root.mkdir(parents=True)
    target = root / session_dir.name
    shutil.copytree(session_dir, target)
    return root


@pytest.fixture()
def work_dir(tmp_path: Path) -> Path:
    """A work directory with runs/traces structure."""
    wd = tmp_path / "work"
    traces = wd / "runs" / "traces"
    traces.mkdir(parents=True)
    return wd


@pytest.fixture()
def sample_tool_calls() -> list[ToolCall]:
    """A small list of representative tool calls."""
    return [
        make_tool_call(
            call_id="call-1",
            tool_name="read_file",
            raw_input={"target_file": "src/main.py"},
            update_index=0,
        ),
        make_tool_call(
            call_id="call-2",
            tool_name="search_replace",
            raw_input={"file_path": "src/main.py", "old_string": "old", "new_string": "new"},
            update_index=1,
        ),
        make_tool_call(
            call_id="call-3",
            tool_name="run_terminal_command",
            raw_input={"command": "pytest"},
            result_content="1 passed",
            update_index=2,
        ),
        make_tool_call(
            call_id="call-4",
            tool_name="grep",
            raw_input={"pattern": "def main", "path": "src/"},
            update_index=3,
        ),
        make_tool_call(
            call_id="call-5",
            tool_name="run_terminal_command",
            raw_input={"command": "make build"},
            result_content="Error: undefined reference",
            is_error=True,
            exit_code=2,
            update_index=4,
        ),
    ]


@pytest.fixture(autouse=True)
def _isolate_all_config_dirs(tmp_path_factory, monkeypatch):
    """Every test uses a fresh temp tree for app + Grok CLI config (never ~/.groket / ~/groket).

    Isolates personas, rules, detectors, analysis plugins, tasks, prefs, work dir,
    and ``Path.home()``-based ``~/.grok`` (models cache, installed-plugins, auth)
    so the suite cannot pollute the developer's real stores.
    """
    from pathlib import Path as _Path

    root = tmp_path_factory.mktemp("groket_test_root")
    user_home = root / "home"
    app_home = user_home / ".groket"
    work_root = root / "work"
    grok_home = user_home / ".grok"
    for d in (
        user_home,
        app_home,
        app_home / "personas",
        app_home / "rules",
        app_home / "detectors",
        app_home / "plugins",
        app_home / "tasks",
        app_home / "cache",
        work_root,
        grok_home,
        grok_home / "installed-plugins",
    ):
        d.mkdir(parents=True, exist_ok=True)

    # Isolate home / git prompts; work root is patched on paths.DEFAULT_WORK_DIR.
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))  # Windows no-op on Linux
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_ASKPASS", "echo")
    monkeypatch.setenv("GCM_INTERACTIVE", "never")
    monkeypatch.delenv("GROKET_KEYS", raising=False)

    # pathlib.Path.home() — used widely for ~/.grok and fallbacks
    monkeypatch.setattr(_Path, "home", classmethod(lambda cls: user_home))

    from groket import paths

    monkeypatch.setattr(paths, "APP_HOME", app_home)
    monkeypatch.setattr(paths, "DEFAULT_WORK_DIR", work_root)

    def _app_home() -> _Path:
        app_home.mkdir(parents=True, exist_ok=True)
        return app_home

    monkeypatch.setattr(paths, "app_home", _app_home)
    monkeypatch.setattr(paths, "app_config_path", lambda: app_home / "config.json")

    def _subdir(name: str):
        def _fn() -> _Path:
            d = app_home / name
            d.mkdir(parents=True, exist_ok=True)
            return d

        return _fn

    monkeypatch.setattr(paths, "user_detectors_dir", _subdir("detectors"))
    monkeypatch.setattr(paths, "user_rules_dir", _subdir("rules"))
    monkeypatch.setattr(paths, "user_analysis_plugins_dir", _subdir("plugins"))
    monkeypatch.setattr(paths, "user_tasks_dir", _subdir("tasks"))
    monkeypatch.setattr(paths, "analysis_cache_dir", _subdir("cache"))

    def _mcp_registry_cache() -> _Path:
        d = app_home / "cache" / "mcp-registry"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(paths, "mcp_registry_cache_dir", _mcp_registry_cache)
    monkeypatch.setattr(paths, "personas_home", _subdir("personas"))
    monkeypatch.setattr(paths, "reports_dir", _subdir("reports"))
    monkeypatch.setattr(paths, "user_models_path", lambda: app_home / "models.yaml")

    import groket.ui.app as ui_app

    if hasattr(ui_app, "APP_HOME"):
        monkeypatch.setattr(ui_app, "APP_HOME", app_home)
    if hasattr(ui_app.TraceEvalApp, "_CONFIG_PATH"):
        monkeypatch.setattr(ui_app.TraceEvalApp, "_CONFIG_PATH", app_home / "config.json")

    import groket.ui.prefs as prefs_mod

    for attr in ("_PREFS_PATH", "PREFS_PATH", "_path"):
        if hasattr(prefs_mod, attr):
            monkeypatch.setattr(prefs_mod, attr, app_home / "prefs.json")

    import groket.runs.batch as batch_mod

    monkeypatch.setattr(batch_mod, "WORK_DIR", work_root)
    monkeypatch.setattr(batch_mod, "AUTH_JSON", grok_home / "auth.json")
    monkeypatch.setattr(batch_mod, "GROK_CONFIG", grok_home / "config.toml")
    if hasattr(batch_mod, "_GROK_MODELS_CACHE"):
        monkeypatch.setattr(batch_mod, "_GROK_MODELS_CACHE", grok_home / "models_cache.json")
    if hasattr(batch_mod, "_USER_MODELS_PATH"):
        monkeypatch.setattr(batch_mod, "_USER_MODELS_PATH", app_home / "models.yaml")
