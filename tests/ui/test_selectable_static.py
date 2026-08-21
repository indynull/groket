"""SelectableStatic plain-text cache, partial selection, and browser yank."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import make_trace_event
from groket.ui.selectable_static import (
    SelectableStatic,
    plain_from_renderable,
    to_display_content,
)
from groket.ui.widgets.detail_view import DetailView
from rich.console import Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.content import Content
from textual.geometry import Offset
from textual.selection import Selection


def test_plain_from_renderable_str() -> None:
    assert plain_from_renderable("hello") == "hello"
    assert plain_from_renderable(None) == ""


def test_plain_from_renderable_text() -> None:
    assert plain_from_renderable(Text("styled", style="bold")) == "styled"


def test_plain_from_renderable_markdown() -> None:
    plain = plain_from_renderable(Markdown("**bold** and `code`"), full=True)
    assert "bold" in plain
    assert "code" in plain


def test_plain_from_renderable_markdown_fences_not_width_padded() -> None:
    """Rich Console pads Markdown to width with spaces — yank must use source."""
    import re

    from groket.ui.panel_render import content_block

    body = (
        "Where do you see it?\n\n"
        "```bash\n"
        "mkdir -p …/grokos-agent-pi/{src,bin,scripts,test}\n"
        "```\n\n"
        "```text\n"
        "…/grokos-agent-pi/package.json\n"
        '+ "name": "@grokos/agent-pi"\n'
        "```\n"
    )
    for obj in (Markdown(body), content_block(body)):
        plain = plain_from_renderable(obj, width=80, full=True)
        max_spaces = max((len(m.group(0)) for m in re.finditer(r" +", plain)), default=0)
        assert max_spaces < 20, max_spaces
        assert "mkdir -p" in plain
        assert '"name": "@grokos/agent-pi"' in plain
        assert len(plain) < 500


def test_plain_from_renderable_syntax() -> None:
    plain = plain_from_renderable(Syntax("print(1)", "python"), full=True)
    assert "print" in plain


def test_plain_from_renderable_syntax_long_line_not_cropped_at_narrow_width() -> None:
    """Rich Syntax crops to console width; yank must use raw .code instead."""
    long_line = "print('" + ("z" * 400) + "END')"
    plain = plain_from_renderable(Syntax(long_line, "python"), width=40, full=True)
    assert "END" in plain
    assert len(plain) >= 400
    assert plain.endswith("')")


def test_plain_from_renderable_group_preserves_syntax_code() -> None:
    long_line = "payload_" + ("x" * 300) + "_TAIL"
    group = Group(Text("hdr"), Syntax(long_line, "text"), Text("foot"))
    plain = plain_from_renderable(group, width=30, full=True)
    assert "hdr" in plain
    assert "foot" in plain
    assert "_TAIL" in plain


def test_plain_from_renderable_full_markdown_keeps_markup() -> None:
    assert "**world**" in plain_from_renderable(Markdown("Hello **world**"), full=True)


def test_to_display_content_markdown_is_content() -> None:
    disp = to_display_content(Markdown("Hello **world** uniquely"))
    assert isinstance(disp, Content)
    assert "world" in disp.plain
    assert "uniquely" in disp.plain
    # Emphasis must survive as style spans (not plain dump).
    assert len(disp.spans) >= 1


def test_to_display_content_syntax_keeps_highlight_spans() -> None:
    """Tool Input/Output uses product Syntax highlight colors (not plain dump)."""
    from groket.ui.render_detail import _syntax

    code = "def hello():\n    return 42\n"
    # Same constructor the detail pane uses (theme + word_wrap).
    syn = _syntax(code, "python")
    disp = to_display_content(syn)
    assert isinstance(disp, Content)
    assert "def hello" in disp.plain
    assert "return 42" in disp.plain
    assert len(disp.spans) >= 5
    # Multiple distinct foreground colors (keyword vs name vs number).
    fgs = {
        str(getattr(s.style, "foreground", None) or getattr(s.style, "color", None))
        for s in disp.spans
    }
    assert len(fgs) >= 2
    # Still selectable by unwrapped character offsets.
    lines = disp.plain.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "return" in ln)
    sel = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
    body = SelectableStatic(syn)
    got = body.get_selection(sel)
    assert got is not None
    assert "return 42" in got[0]


def test_prepare_body_syntax_keeps_line_longer_than_former_4k_cap() -> None:
    """Shipped prepare_body must not crop Syntax lines at a 4000-cell bake width."""
    from groket.ui.selectable_static import prepare_body

    tail = "END_MARKER_LONG_SYNTAX_LINE"
    code = ("x" * 4500) + tail
    assert len(code) > 4000
    syn = Syntax(code, "text", word_wrap=False)
    display, select_plain, yank_plain = prepare_body(syn)
    assert isinstance(display, Content)
    assert tail in select_plain
    assert tail in yank_plain
    assert tail in display.plain
    assert len(yank_plain) >= len(code) or code in yank_plain

    body = SelectableStatic(syn)
    assert tail in body.get_plain_text()
    # Selection extract of the full logical line includes the tail.
    line = body._select_plain.splitlines()[0]
    sel = Selection(Offset(0, 0), Offset(len(line), 0))
    got = body.get_selection(sel)
    assert got is not None
    assert tail in got[0]


@pytest.mark.asyncio
async def test_tool_detail_selectable_static_keeps_long_syntax_tail() -> None:
    """Tool-detail body via SelectableStatic retains long command/output tails."""
    from groket.ui.widgets.detail_view import DetailView

    tail = "END_TOOL_CMD_TAIL_9911"
    long_cmd = ("z" * 4500) + tail
    ev = make_trace_event(
        index=2,
        event_type="tool_call",
        tool_name="run_terminal_command",
        content="ok",
        raw_input={"command": long_cmd},
    )

    class _DetailApp(App):
        def compose(self) -> ComposeResult:
            yield DetailView(id="detail")

    app = _DetailApp()
    async with app.run_test(size=(100, 40)):
        dv = app.query_one("#detail", DetailView)
        dv.show_event(ev)
        body = dv.query_one("#detail-body", SelectableStatic)
        plain = body.get_plain_text()
        assert tail in plain
        assert isinstance(body.content, Content)


def test_session_summary_chrome_keeps_short_rule_not_dash_flood() -> None:
    """Summary uses Rich Rule + Table — must not bake Rule at 10k width."""
    from pathlib import Path

    from groket.models import SessionMeta
    from groket.ui.selectable_static import prepare_body
    from groket.ui.session_summary import render_session_summary

    meta = SessionMeta(
        session_id="s1",
        session_dir=Path("/tmp/x"),
        title="Coredis project logo and brand kits",
        origin="host",
    )
    renderable = render_session_summary(meta, [])
    display, select_plain, yank_plain = prepare_body(renderable)
    assert isinstance(display, Content)
    for plain in (select_plain, yank_plain):
        assert plain.count("─") < 200
        assert max((len(ln) for ln in plain.splitlines()), default=0) <= 120
        assert "Coredis" in plain


@pytest.mark.asyncio
async def test_tool_detail_input_output_is_drag_selectable() -> None:
    """Tool Input/Output must be Content so partial drag-select works."""
    from groket.ui.render_detail import render_event_detail
    from groket.ui.widgets.detail_view import DetailView

    ev = make_trace_event(
        index=1,
        event_type="tool_call",
        tool_name="run_terminal_command",
        content="line one of output\nline two UNIQUE_OUT\nline three",
        raw_input={"command": "echo hello UNIQUE_IN"},
    )

    class _DetailApp(App):
        def compose(self) -> ComposeResult:
            yield DetailView(id="detail")

    # Chrome path would leave RichVisual Group — selection offsets empty.
    assert any(type(c).__name__ == "Rule" for c in (render_event_detail(ev).renderables or ()))

    app = _DetailApp()
    async with app.run_test(size=(100, 40)) as pilot:
        dv = app.query_one("#detail", DetailView)
        dv.show_event(ev)
        await pilot.pause()
        body = dv.query_one("#detail-body", SelectableStatic)
        assert isinstance(body.content, Content)
        plain = body.get_plain_text()
        assert "UNIQUE_IN" in plain
        assert "UNIQUE_OUT" in plain
        # Selection coords use display plain (may differ from source yank plain).
        lines = body._select_plain.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "UNIQUE_OUT" in ln)
        sel = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
        got = body.get_selection(sel)
        assert got is not None
        assert "UNIQUE_OUT" in got[0]
        assert "UNIQUE_IN" not in got[0]

        # Real mouse drag should produce a non-empty selection extract.
        region = body.content_region
        lx = 2
        y0 = 1
        y1 = min(region.height - 1, 8)
        await pilot.mouse_down(body, offset=(lx, y0))
        for y in range(y0, y1 + 1):
            await pilot.hover(body, offset=(lx, y))
        await pilot.mouse_up(body, offset=(lx, y1))
        await pilot.pause()
        selected = app.screen.get_selected_text()
        assert selected is not None and selected.strip() != ""
        # Partial span — not forced SELECT_ALL of the whole body unless drag covers it.
        assert body.ALLOW_SELECT is True


def test_group_plain_preserves_line_for_partial_extract() -> None:
    """Group display plain keeps lines so Selection.extract can take one word.

    Product Groups use trailing newlines on Text parts (like detail heads);
    bare Text children without ``\\n`` glue the same way Rich Group does.
    """
    body = Group(
        Text("#0 user message\n"),
        Text("\n"),
        Text("first line of the prompt\n"),
        Text("second line ONLY_THIS_LINE\n"),
        Text("third line\n"),
    )
    plain = plain_from_renderable(body)
    lines = plain.splitlines()
    idx = next(i for i, ln in enumerate(lines) if "ONLY_THIS_LINE" in ln)
    full_line = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
    assert "ONLY_THIS_LINE" in full_line.extract(plain)
    assert "first line" not in full_line.extract(plain)
    start = lines[idx].index("ONLY")
    word = Selection(Offset(start, idx), Offset(start + 4, idx))
    assert word.extract(plain) == "ONLY"


class _SelApp(App):
    def compose(self) -> ComposeResult:
        yield SelectableStatic("line one\nline two\nline three", id="body")


@pytest.mark.asyncio
async def test_selectable_static_plain_cache_and_partial_selection() -> None:
    app = _SelApp()
    async with app.run_test():
        body = app.query_one("#body", SelectableStatic)
        assert "line one" in body.get_plain_text()
        # First line, columns 0–4 → "line"
        sel = Selection(Offset(0, 0), Offset(4, 0))
        got = body.get_selection(sel)
        assert got is not None
        text, _end = got
        assert text == "line"
        # Whole second line
        sel2 = Selection(Offset(0, 1), Offset(8, 1))
        got2 = body.get_selection(sel2)
        assert got2 is not None
        assert got2[0] == "line two"


def _narrow_long_line_app(width: int) -> type[App]:
    """Narrow pane with one long logical line that soft-wraps, then Z_MARKER."""

    class _WrapApp(App):
        CSS = f"""
        #body {{ width: {width}; height: auto; border: solid green; }}
        """

        def compose(self) -> ComposeResult:
            # One logical line longer than the pane (soft-wraps to many paint rows).
            long = ("WORD_A " * 30).strip() + " END_MARKER"
            yield SelectableStatic(long + "\nZ_MARKER", id="body")

    return _WrapApp


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [40, 20])
async def test_selectable_static_soft_wrap_selection_keeps_full_logical_span(width: int) -> None:
    """Mouse offsets are unwrapped character positions — not paint-row indices.

    A long line that soft-wraps must still extract past the first visible row
    when the selection end x is far into the logical line (as Textual records).
    """
    app = _narrow_long_line_app(width)()
    async with app.run_test(size=(max(width + 10, 50), 24)):
        body = app.query_one("#body", SelectableStatic)
        plain = body.get_plain_text()
        lines = plain.splitlines()
        assert len(lines) >= 2
        long_line = lines[0]
        assert "END_MARKER" in long_line
        assert len(long_line) > width  # must soft-wrap in the pane
        # Real Textual drag on a wrapped line: y stays 0, x runs along logical chars.
        sel = Selection(Offset(0, 0), Offset(len(long_line), 0))
        got = body.get_selection(sel)
        assert got is not None
        text, _end = got
        assert "END_MARKER" in text
        assert "WORD_A" in text
        assert "Z_MARKER" not in text
        # Must not stop at the first paint row (~width cells).
        assert len(text) == len(long_line)
        assert len(text) > width


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [40, 20])
async def test_action_copy_detail_yanks_soft_wrap_selection(width: int) -> None:
    """Browser copy path keeps the full logical span of a soft-wrapped line."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    app = _narrow_long_line_app(width)()
    async with app.run_test(size=(max(width + 10, 50), 24)):
        body = app.query_one("#body", SelectableStatic)
        long_line = body.get_plain_text().splitlines()[0]
        sel = Selection(Offset(10, 0), Offset(len(long_line), 0))
        got = body.get_selection(sel)
        assert got is not None
        selected = got[0]
        assert "END_MARKER" in selected
        assert len(selected) > width

        copied: list[str] = []
        host = SimpleNamespace(
            get_selected_text=lambda: selected,
            focused=body,
            _selected_finding=None,
            _active_browser_tab=lambda: "tab-timeline",
            _collect_active_tab_plain_text=lambda: (body.get_plain_text(), "detail"),
            app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
            notify=lambda msg, **kwargs: None,
        )
        BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
        assert copied == [selected]
        assert "END_MARKER" in copied[0]
        assert "Z_MARKER" not in copied[0]


@pytest.mark.asyncio
async def test_pilot_mouse_drag_soft_wrap_copies_past_first_paint_row() -> None:
    """Real Pilot drag down a narrow pane: clipboard/extract past first wrap row."""
    width = 30
    app = _narrow_long_line_app(width)()
    async with app.run_test(size=(50, 30)) as pilot:
        body = app.query_one("#body", SelectableStatic)
        await pilot.pause()
        region = body.content_region
        assert region.height >= 3  # soft-wrap produces multiple paint rows
        x = min(5, max(0, region.width // 2))
        # Local offsets relative to widget origin (region includes gutter).
        local_x = (region.x - body.region.x) + x
        y0 = (region.y - body.region.y) + 0
        y1 = (region.y - body.region.y) + max(2, region.height - 1)
        await pilot.mouse_down(body, offset=(local_x, y0))
        for y in range(y0, y1 + 1):
            await pilot.hover(body, offset=(local_x, y))
        await pilot.mouse_up(body, offset=(local_x, y1))
        await pilot.pause()
        selected = app.screen.get_selected_text()
        assert selected is not None and selected != ""
        # Truncation bug: only first paint row (~width cells, no END_MARKER).
        assert "END_MARKER" in selected or len(selected) > width
        assert "Z_MARKER" not in selected or "END_MARKER" in selected


class _MdApp(App):
    def compose(self) -> ComposeResult:
        yield SelectableStatic("", id="body")


@pytest.mark.asyncio
async def test_selectable_static_markdown_partial_line_selection() -> None:
    """Unwrapped display plain tracks rendered Markdown for line-scoped selection."""
    app = _MdApp()
    async with app.run_test(size=(80, 30)):
        body = app.query_one("#body", SelectableStatic)
        body.update(
            Group(
                Text("meta header"),
                Markdown("## Section\n\nAgent said hello world uniquely"),
            )
        )
        # Full yank keeps source (markdown markup / text children).
        full = body.get_plain_text()
        assert "## Section" in full
        assert "hello world uniquely" in full
        assert "meta header" in full
        # Selection offsets index display plain (rendered, no ##).
        plain = body._select_plain
        assert plain
        assert "## Section" not in plain or "Section" in plain
        lines = plain.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "hello world" in ln or "uniquely" in ln)
        sel = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
        got = body.get_selection(sel)
        assert got is not None
        text, end = got
        assert "uniquely" in text or "hello world" in text
        assert "meta header" not in text
        assert end == "\n"


def test_markdown_body_full_yank_keeps_source_heading() -> None:
    """Diff assistant / report MD: y pastes source ``##`` not rendered Heading only."""
    from groket.ui.panel_render import md_content

    body = SelectableStatic(md_content("## Heading\n\nok", indent=0))
    assert "## Heading" in body.get_plain_text()
    assert "ok" in body.get_plain_text()
    # Display/select plain is rendered (no ##).
    assert "##" not in body._select_plain
    assert "Heading" in body._select_plain


def test_system_detail_keeps_yellow_body_style() -> None:
    """System chrome body is Text(..., style=yellow) — base style must survive bake."""
    from groket.ui.render_detail import render_event_detail
    from groket.ui.styles import EVENT_TYPE_STYLE

    ev = make_trace_event(
        index=0,
        event_type="system",
        content="You are Grok.\n\n* Rule one",
    )
    renderable = render_event_detail(ev)
    body = SelectableStatic(renderable)
    assert isinstance(body.content, Content)
    assert "You are Grok" in body.get_plain_text()
    # Yellow (or themed body color) on the system text — not plain default.
    styles = list(body.content.spans)
    assert styles, "expected styled spans on system detail"
    # At least one non-dim colored span (body yellow or type gray).
    colored = [
        s
        for s in styles
        if getattr(s.style, "foreground", None) is not None
        or getattr(s.style, "color", None) is not None
    ]
    assert colored, f"no foreground styles: {styles!r}"
    # Type label still uses event style map.
    assert "system" in EVENT_TYPE_STYLE


class _DetailApp(App):
    def compose(self) -> ComposeResult:
        yield DetailView(id="detail")


@pytest.mark.asyncio
async def test_detail_view_get_plain_text_yanks_message() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="user_message_chunk",
            content="please copy this exact phrase XYZ123",
        )
        dv.show_event(ev)
        plain = dv.get_plain_text()
        assert "XYZ123" in plain
        assert "please copy" in plain


@pytest.mark.asyncio
async def test_detail_view_yank_keeps_full_tool_output_past_display_cap() -> None:
    """Display mid-caps tool output; y must still include the whole body."""
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        # Mid-cap keeps head+tail; middle unique marker must survive full yank only.
        middle = "UNIQUE_MIDDLE_CHUNK_991177"
        out = ("A" * 9000) + middle + ("B" * 9000)
        ev = make_trace_event(
            index=1,
            event_type="tool_call",
            tool_name="run_terminal_command",
            content=out,
            raw_input={"command": "echo hi"},
        )
        dv.show_event(ev)
        body = dv.query_one("#detail-body", SelectableStatic)
        display_plain = body.get_plain_text()
        yank = dv.get_plain_text()
        assert middle in yank
        # Display path may omit the middle when mid-truncated.
        from groket.ui.i18n import t

        if t("truncate-marker") in display_plain:
            assert middle not in display_plain


@pytest.mark.asyncio
async def test_detail_view_multiline_message_partial_line() -> None:
    """Multi-line user prompts keep lines distinct for drag-select."""
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="user_message_chunk",
            content="line alpha\nline bravo UNIQUE99\nline charlie",
        )
        dv.show_event(ev)
        body = dv.query_one("#detail-body", SelectableStatic)
        plain = body.get_plain_text()
        assert "UNIQUE99" in plain
        lines = body._select_plain.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "UNIQUE99" in ln)
        sel = Selection(Offset(0, idx), Offset(len(lines[idx]), idx))
        got = body.get_selection(sel)
        assert got is not None
        assert "UNIQUE99" in got[0]
        assert "charlie" not in got[0]


@pytest.mark.asyncio
async def test_detail_view_get_plain_text_empty_when_cleared() -> None:
    app = _DetailApp()
    async with app.run_test():
        dv = app.query_one("#detail", DetailView)
        assert dv.get_plain_text().strip() == ""
        dv.clear_detail()
        assert dv.get_plain_text().strip() == ""


@pytest.mark.asyncio
async def test_action_copy_detail_yanks_full_body() -> None:
    """Browser y (copy_detail) puts detail plain text on the clipboard."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    class _BrowserCopyApp(App):
        def compose(self) -> ComposeResult:
            yield DetailView(id="detail-panel")

    app = _BrowserCopyApp()
    async with app.run_test():
        dv = app.query_one("#detail-panel", DetailView)
        ev = make_trace_event(
            index=0,
            event_type="agent_message_chunk",
            content="clipboard-target-phrase-99",
        )
        dv.show_event(ev)

        copied: list[str] = []
        notes: list[str] = []
        host = SimpleNamespace(
            get_selected_text=lambda: None,
            focused=None,
            _selected_finding=None,
            _active_browser_tab=lambda: "tab-timeline",
            _collect_active_tab_plain_text=lambda: (
                app.query_one("#detail-panel", DetailView).get_plain_text(),
                "detail",
            ),
            app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
            notify=lambda msg, **kwargs: notes.append(str(msg)),
        )
        BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
        assert copied
        assert "clipboard-target-phrase-99" in copied[0]
        assert notes


def test_action_copy_detail_report_without_focus_is_nothing() -> None:
    """On Report with no selection and no focused pane, y does not join siblings."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    copied: list[str] = []
    notes: list[str] = []
    host = SimpleNamespace(
        get_selected_text=lambda: None,
        focused=None,
        _selected_finding=None,
        _active_browser_tab=lambda: "tab-reports",
        _collect_active_tab_plain_text=lambda: ("", "none"),
        app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
    )
    BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
    assert copied == []
    assert notes


def test_action_copy_detail_yanks_focused_report_pane() -> None:
    """Focused Report sub-pane (e.g. Issue box) yanks only that pane body."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    issue_body = (
        "What: Claimed MCP failed\n"
        "Where: Turn 0\n"
        "Why: Instruction required MCP-first.\n"
        "Should have: Call preferred MCP tools first.\n"
        "Pattern: none\n"
    )
    focused = SelectableStatic(issue_body, id="report-pane-feedback-3")
    copied: list[str] = []
    notes: list[str] = []
    host = SimpleNamespace(
        get_selected_text=lambda: None,
        focused=focused,
        _selected_finding=None,
        _active_browser_tab=lambda: "tab-reports",
        _collect_active_tab_plain_text=lambda: ("whole report", "report"),
        app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
    )
    BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
    assert len(copied) == 1
    assert copied[0].startswith("What: Claimed MCP failed")
    assert "whole report" not in copied[0]
    assert notes


@pytest.mark.asyncio
async def test_action_copy_detail_selection_exact_not_stripped() -> None:
    """Live selection is copied exactly (leading/trailing spaces kept)."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    copied: list[str] = []
    notes: list[str] = []
    host = SimpleNamespace(
        get_selected_text=lambda: "  keep pads  ",
        focused=None,
        _selected_finding=None,
        _active_browser_tab=lambda: "tab-timeline",
        _collect_active_tab_plain_text=lambda: ("nope", "detail"),
        app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
    )
    BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
    assert copied == ["  keep pads  "]


@pytest.mark.asyncio
async def test_multipane_focused_only_via_real_widgets() -> None:
    """Two extractable panes: yank uses the focused body only."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    class _Multi(App):
        def compose(self) -> ComposeResult:
            yield SelectableStatic("PANE_A_ONLY unique-aaa", id="report-overview-content")
            yield SelectableStatic("PANE_B_ONLY unique-bbb", id="report-flags-content")

    app = _Multi()
    async with app.run_test():
        a = app.query_one("#report-overview-content", SelectableStatic)
        b = app.query_one("#report-flags-content", SelectableStatic)
        a.focus()
        copied: list[str] = []
        host = SimpleNamespace(
            get_selected_text=lambda: None,
            focused=a,
            _selected_finding=None,
            _active_browser_tab=lambda: "tab-reports",
            _collect_active_tab_plain_text=lambda: (
                f"{a.get_plain_text()}\n\n{b.get_plain_text()}",
                "report",
            ),
            app=SimpleNamespace(copy_to_clipboard=lambda text: copied.append(text)),
            notify=lambda msg, **kwargs: None,
        )
        BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
        assert len(copied) == 1
        assert "unique-aaa" in copied[0]
        assert "unique-bbb" not in copied[0]

        copied.clear()
        host.focused = b
        BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
        assert "unique-bbb" in copied[0]
        assert "unique-aaa" not in copied[0]


@pytest.mark.asyncio
async def test_drag_selection_stays_inside_one_extractable_pane() -> None:
    """A drag on pane A cannot include pane B's body."""
    from textual.selection import Selection

    class _Multi(App):
        def compose(self) -> ComposeResult:
            yield SelectableStatic("ALPHA_PANE only-here", id="pane-a")
            yield SelectableStatic("BETA_PANE other-body", id="pane-b")

    app = _Multi()
    async with app.run_test():
        a = app.query_one("#pane-a", SelectableStatic)
        b = app.query_one("#pane-b", SelectableStatic)
        a_plain = a._select_plain
        a_lines = a_plain.splitlines() or [a_plain]
        span = Selection(Offset(0, 0), Offset(len(a_lines[0]), 0))
        extracted = a.get_selection(span)
        assert extracted is not None
        text, _ = extracted
        assert "ALPHA_PANE" in text
        assert "BETA_PANE" not in text
        b_plain = b._select_plain
        b_lines = b_plain.splitlines() or [b_plain]
        other = b.get_selection(Selection(Offset(0, 0), Offset(len(b_lines[0]), 0)))
        assert other is not None
        assert "BETA_PANE" in other[0]
        assert "ALPHA_PANE" not in other[0]


def test_is_extractable_static() -> None:
    from groket.ui.selectable_static import SelectableStatic, is_extractable_static
    from textual.widgets import Static

    assert is_extractable_static(SelectableStatic("x")) is True
    assert is_extractable_static(Static("x")) is False
    assert is_extractable_static(None) is False


@pytest.mark.asyncio
async def test_multiline_selection_extract_and_copy() -> None:
    """Multi-line drag extract keeps newlines; y copies the full span."""
    from types import SimpleNamespace

    from groket.ui.screens.browser import BrowserScreen

    class _MultiLine(App):
        def compose(self) -> ComposeResult:
            yield SelectableStatic(
                "line one AAA\nline two BBB\nline three CCC",
                id="body",
            )

    app = _MultiLine()
    async with app.run_test(size=(60, 20)):
        body = app.query_one("#body", SelectableStatic)
        lines = body.get_plain_text().splitlines()
        assert len(lines) >= 3
        # From start of line 0 through end of line 2 (logical lines).
        sel = Selection(Offset(0, 0), Offset(len(lines[2]), 2))
        got = body.get_selection(sel)
        assert got is not None
        text, _end = got
        assert "line one AAA" in text
        assert "line two BBB" in text
        assert "line three CCC" in text
        assert text.count("\n") >= 2

        # Screen joins (text, end) the same way get_selected_text does.
        joined = "".join(got).rstrip("\n")
        copied: list[str] = []
        host = SimpleNamespace(
            get_selected_text=lambda: joined,
            focused=body,
            _selected_finding=None,
            _active_browser_tab=lambda: "tab-timeline",
            _collect_active_tab_plain_text=lambda: ("nope", "detail"),
            app=SimpleNamespace(copy_to_clipboard=lambda t: copied.append(t)),
            notify=lambda *a, **k: None,
        )
        BrowserScreen.action_copy_detail(host)  # type: ignore[arg-type]
        assert copied == [joined]
        assert "\n" in copied[0]


def _app_copy_host(**kwargs: object) -> SimpleNamespace:
    """Minimal host with TraceEvalApp copy helpers bound for unit tests."""
    from groket.ui.app import TraceEvalApp

    host = SimpleNamespace(**kwargs)
    if not hasattr(host, "_copy_notify_at"):
        host._copy_notify_at = 0.0
        host._copy_notify_msg = ""
    host.notify_copied = lambda msg: TraceEvalApp.notify_copied(host, msg)  # type: ignore[arg-type]
    host._copy_live_selection = lambda: TraceEvalApp._copy_live_selection(host)  # type: ignore[arg-type]
    return host


def test_notify_copied_debounces_same_message() -> None:
    """Rapid same-message copy toasts must not stack."""
    from groket.ui.app import TraceEvalApp

    notes: list[str] = []
    host = SimpleNamespace(
        _copy_notify_at=0.0,
        _copy_notify_msg="",
        notify=lambda msg, **kwargs: notes.append(str(msg)),
    )
    TraceEvalApp.notify_copied(host, "Copied selection to clipboard")  # type: ignore[arg-type]
    TraceEvalApp.notify_copied(host, "Copied selection to clipboard")  # type: ignore[arg-type]
    assert notes == ["Copied selection to clipboard"]
    # Different message still notifies.
    TraceEvalApp.notify_copied(host, "Copied detail to clipboard")  # type: ignore[arg-type]
    assert notes == ["Copied selection to clipboard", "Copied detail to clipboard"]


def test_selectable_static_does_not_focus_on_click() -> None:
    """Click must not raise the body card (focus stays for Tab only)."""
    assert SelectableStatic("x").focus_on_click() is False


@pytest.mark.asyncio
async def test_action_help_quit_copies_selection() -> None:
    """Ctrl+C copies selection when present instead of only showing quit hint."""
    from groket.ui.app import TraceEvalApp

    copied: list[str] = []
    notes: list[str] = []
    host = _app_copy_host(
        screen=SimpleNamespace(get_selected_text=lambda: "selected-bit"),
        copy_to_clipboard=lambda text: copied.append(text),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
        active_bindings={},
    )
    # _copy_live_selection is used by Ctrl+C and drag-end TextSelected.
    assert host._copy_live_selection() is True
    assert copied == ["selected-bit"]
    assert notes
    TraceEvalApp.action_help_quit(host)  # type: ignore[arg-type]
    assert copied == ["selected-bit", "selected-bit"]


@pytest.mark.asyncio
async def test_text_selected_auto_copies_live_selection() -> None:
    """Mouse-up after a drag (TextSelected) copies without pressing y."""
    from groket.ui.app import TraceEvalApp
    from textual.events import TextSelected

    copied: list[str] = []
    notes: list[str] = []
    host = _app_copy_host(
        screen=SimpleNamespace(get_selected_text=lambda: "dragged multi\nline bit"),
        copy_to_clipboard=lambda text: copied.append(text),
        notify=lambda msg, **kwargs: notes.append(str(msg)),
    )
    TraceEvalApp.on_text_selected(host, TextSelected())  # type: ignore[arg-type]
    assert copied == ["dragged multi\nline bit"]
    assert notes

    # Empty selection (click / cleared) must not copy.
    copied.clear()
    notes.clear()
    host.screen = SimpleNamespace(get_selected_text=lambda: None)
    TraceEvalApp.on_text_selected(host, TextSelected())  # type: ignore[arg-type]
    assert copied == []
    assert notes == []


@pytest.mark.asyncio
async def test_action_help_quit_copies_focused_body() -> None:
    """Ctrl+C with no selection yanks the focused extractable body (like y)."""
    from groket.ui.app import TraceEvalApp

    class _FocusApp(App):
        def compose(self) -> ComposeResult:
            yield SelectableStatic("FOCUSED_BODY_PHRASE_42", id="body")

    app = _FocusApp()
    async with app.run_test():
        body = app.query_one("#body", SelectableStatic)
        body.focus()
        copied: list[str] = []
        notes: list[str] = []

        def _copy_detail() -> None:
            plain = body.get_plain_text()
            copied.append(plain)
            notes.append("copied")

        host = _app_copy_host(
            screen=SimpleNamespace(
                get_selected_text=lambda: None,
                focused=body,
                action_copy_detail=_copy_detail,
            ),
            copy_to_clipboard=lambda text: copied.append(text),
            notify=lambda msg, **kwargs: notes.append(str(msg)),
            active_bindings={},
        )
        TraceEvalApp.action_help_quit(host)  # type: ignore[arg-type]
        assert any("FOCUSED_BODY_PHRASE_42" in c for c in copied)
