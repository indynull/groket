"""The multi-harness disk-view brief stays complete and matches shipped groket."""

from __future__ import annotations

from pathlib import Path

import groket.event_types as et
from groket.constants import INTERRUPTED_MARKER_FILENAME
from groket.flags import load_flags
from groket.notes import NOTES_FILENAME
from groket.parser import find_sessions
from groket.session.catalog import session_catalog_row
from groket.session.export_bundle import _GROK_TRACE_CORE_FILES
from groket.session.resume import can_resume_session
from groket.session.sources import host_grok_sessions_root, work_traces_root
from groket.session.turn_gate import TURN_GATE_NAME
from groket.session.turns import segment_timeline_turns
from groket.session.workspace_diff import load_workspace_diff

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ROOT / "docs" / "harness-disk-view.md"

HARNESS_IDS = (
    "grok",
    "claude",
    "codex",
    "gemini",
    "opencode",
    "cursor",
    "aider",
    "pi",
    "copilot",
)

FEATURE_FAMILIES = (
    "Catalog discovery",
    "Timeline / events",
    "Turn segmentation",
    "Live status",
    "Turn-gate follow-up and Done",
    "Fork / resume",
    "Workspace / diff",
    "Usage and context meter",
    "Notes and flags",
    "Export",
    "Control plane",
    "Docker / batch eval traces",
    "Host `~/.grok/sessions`",
)

REQUIRED_HEADINGS = (
    "## 1. What groket does today",
    "## 2. Groket feature → Grok artifact inventory",
    "## 3. Abstract disk-view contract",
    "## 4. Per-harness disk specifications",
    "## 5. Feature × harness matrix",
    "## 6. Gaps a rewrite must not paper over",
    "## 7. Extra stores (out of the nine ids)",
)


def _brief() -> str:
    assert BRIEF.is_file(), f"missing rewrite brief {BRIEF}"
    text = BRIEF.read_text(encoding="utf-8")
    assert "TBD" not in text
    return text


def test_brief_has_required_sections_and_harness_ids() -> None:
    text = _brief()
    for heading in REQUIRED_HEADINGS:
        assert heading in text, f"missing heading {heading!r}"
    for hid in HARNESS_IDS:
        assert "### 4." in text
        assert f"`{hid}`" in text, f"harness id {hid} not specified"
        # Each harness section names a discover root or ABSENT with evidence.
        assert hid in text
    for family in FEATURE_FAMILIES:
        assert family in text, f"inventory missing {family!r}"
    assert "parse_line" in text
    assert "signals.json" in text
    assert "rewind_points.jsonl" in text
    assert ".groket-turn" in text


def test_brief_matrix_does_not_mark_grok_only_control_full_elsewhere() -> None:
    text = _brief()
    start = text.index("| Feature | grok |")
    end = text.index("## 6. Gaps", start)
    table = text[start:end]
    rows = [
        ln
        for ln in table.splitlines()
        if ln.startswith("| ") and "Feature" not in ln and "---" not in ln
    ]

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip("|").split("|")]

    parsed = {cells(ln)[0]: cells(ln)[1:] for ln in rows if len(cells(ln)) == 10}
    assert "Follow-up `n`" in parsed
    assert "Done `e`" in parsed
    assert "Fork `f`" in parsed
    assert "Context meter (`signals.json` shape)" in parsed
    assert "Export `grok trace --local`" in parsed
    assert "Docker personas/plugins entrypoint" in parsed

    grok_only = (
        "Done `e`",
        "Fork `f`",
        "Export `grok trace --local`",
        "Docker personas/plugins entrypoint",
    )
    for feature in grok_only:
        vals = parsed[feature]
        assert vals[0].startswith("full"), feature
        for hid, val in zip(HARNESS_IDS[1:], vals[1:], strict=True):
            assert not val.startswith("full"), f"{feature} marked {val!r} for {hid}"

    awaiting = parsed["Status `awaiting`"]
    assert awaiting[0].startswith("full")
    for hid, val in zip(HARNESS_IDS[1:], awaiting[1:], strict=True):
        assert not val.startswith("full"), f"awaiting marked {val!r} for {hid}"

    cursor_catalog = parsed["Catalog discover"][HARNESS_IDS.index("cursor")]
    assert "jsonl" in cursor_catalog or cursor_catalog.startswith("full")
    assert "Linux" in cursor_catalog or "absent" in cursor_catalog


def test_cited_groket_disk_symbols_still_exist(tmp_path: Path) -> None:
    """Brief cites real modules — import and touch the disk entry points."""
    assert host_grok_sessions_root().name == "sessions"
    assert work_traces_root(tmp_path) == tmp_path / "runs" / "traces"
    assert TURN_GATE_NAME == ".groket-turn"
    assert NOTES_FILENAME == "operator_notes.toml"
    assert INTERRUPTED_MARKER_FILENAME == "groket-interrupted.json"
    assert "summary.json" in _GROK_TRACE_CORE_FILES
    assert "events.jsonl" in _GROK_TRACE_CORE_FILES
    assert et.USER_MESSAGE_CHUNK == "user_message_chunk"
    assert et.TOOL_CALL == "tool_call"
    assert callable(find_sessions)
    assert callable(session_catalog_row)
    assert callable(segment_timeline_turns)
    assert callable(load_workspace_diff)
    assert callable(can_resume_session)
    assert callable(load_flags)
    from groket.fs_watch import _TRACE_NAME_HINTS

    assert "updates.jsonl" in _TRACE_NAME_HINTS
    assert "signals.json" in _TRACE_NAME_HINTS
    assert "operator_notes.toml" in _TRACE_NAME_HINTS


def test_brief_discover_paths_match_probed_layout() -> None:
    text = _brief()
    assert "`~/.grok/sessions/<url-encoded-cwd>/<session_id>/`" in text
    assert "`~/.claude/projects/<cwd-token>/<sessionId>.jsonl`" in text
    assert "`~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<ISO>-<uuid>.jsonl`" in text
    assert "`~/.gemini/tmp/<project-slug>/chats/session-<ISO>-<shortid>.jsonl`" in text
    assert "`~/.local/share/opencode/opencode.db`" in text
    assert "`~/.pi/agent/sessions/<cwd-token>/*.jsonl`" in text
    assert "`~/.copilot/session-store.db`" in text
    assert "`~/.copilot/session-state/<session_id>/`" in text
    assert "`.aider.chat.history.md`" in text
    assert "`~/.cursor/projects/<cwd-token>/agent-transcripts/<uuid>/<uuid>.jsonl`" in text
    assert "`~/.kimi-code/`" in text
