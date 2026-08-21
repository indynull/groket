"""Built-in export renderers (markdown / org / plain)."""

from __future__ import annotations

from groket.session.export_render import (
    BUILTIN_RENDERERS,
    SessionSummaryData,
    report_file_extension,
    session_summary_body,
)


def test_builtin_ids_include_org() -> None:
    assert "org" in BUILTIN_RENDERERS
    assert "markdown" in BUILTIN_RENDERERS
    assert "plain" in BUILTIN_RENDERERS
    assert report_file_extension("org") == ".org"
    assert report_file_extension("markdown") == ".md"
    assert report_file_extension("plain") == ".txt"


def test_session_summary_org_and_markdown() -> None:
    data = SessionSummaryData(
        session_id="sid-1",
        title="Hello session",
        model="grok-test",
        outcome="complete",
        duration_label="12s",
        summary_text="Agent did things.",
        event_count=10,
        tool_call_count=3,
        tool_error_count=1,
        turn_count=2,
    )
    md = session_summary_body(data, renderer="markdown")
    assert md.startswith("# Hello session")
    assert "**Session:** sid-1" in md
    assert "Agent did things." in md
    org = session_summary_body(data, renderer="org")
    assert "#+TITLE: Hello session" in org
    assert "* Meta" in org
    assert "- Session: sid-1" in org
    assert "* Session summary" in org
