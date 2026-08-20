"""Cursor-stable DataTable helpers (shared UX)."""

from __future__ import annotations

import pytest
from groket.ui.data_table import (
    ListDataTable,
    cursor_row_key,
    preserving_cursor,
    preserving_scroll,
    restore_cursor,
    selection_mark,
    set_selection_marker,
    style_data_table,
)
from textual.app import App, ComposeResult
from textual.widgets import DataTable


class _TableApp(App):
    def compose(self) -> ComposeResult:
        yield DataTable(id="t")


@pytest.mark.asyncio
async def test_preserving_cursor_keeps_row_after_clear_rebuild():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name", "on")
        table.add_row("a", "", key="a")
        table.add_row("b", "", key="b")
        table.add_row("c", "", key="c")
        table.move_cursor(row=1, animate=False)
        assert cursor_row_key(table) == "b"

        with preserving_cursor(table):
            table.clear()
            table.add_row("a", "yes", key="a")
            table.add_row("b", "", key="b")
            table.add_row("c", "yes", key="c")

        assert cursor_row_key(table) == "b"
        assert table.cursor_row == 1


@pytest.mark.asyncio
async def test_set_selection_marker_no_row_jump():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns(" ", "name")
        table.add_row(selection_mark(False), "x", key="x")
        table.add_row(selection_mark(False), "y", key="y")
        table.move_cursor(row=1, animate=False)
        assert set_selection_marker(table, "y", True)
        assert cursor_row_key(table) == "y"
        cell = table.get_row_at(1)[0]
        assert "*" in str(cell)


@pytest.mark.asyncio
async def test_restore_cursor_missing_key_is_safe():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name")
        table.add_row("only", key="only")
        assert restore_cursor(table, "gone") is False
        assert restore_cursor(table, "only") is True


@pytest.mark.asyncio
async def test_style_data_table_list_nav_follows_overlay(tmp_path, monkeypatch):
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"list.down" = "h"\n', encoding="utf-8")
    monkeypatch.setenv("GROKET_KEYS", str(keys))
    app = _TableApp()
    async with app.run_test() as pilot:
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name")
        table.add_row("a", key="a")
        table.add_row("b", key="b")
        table.focus()
        table.move_cursor(row=0, animate=False)
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_row == 0
        await pilot.press("h")
        await pilot.pause()
        assert table.cursor_row == 1


@pytest.mark.asyncio
async def test_preserving_scroll_keeps_horizontal_offset():
    app = _TableApp()
    async with app.run_test(size=(40, 12)) as pilot:
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name", "wide")
        table.add_row("a", "x" * 80, key="a")
        table.add_row("b", "y" * 80, key="b")
        await pilot.pause()
        table.scroll_x = 14
        table.scroll_y = 0
        with preserving_scroll(table):
            table.clear()
            table.add_row("a", "x" * 80, key="a")
            table.add_row("b", "y" * 80, key="b")
        await pilot.pause()
        assert table.scroll_x == 14


@pytest.mark.asyncio
async def test_list_data_table_click_highlights_then_activates() -> None:
    """Same as the session list: first click moves the cursor, second activates."""
    from rich.style import Style
    from textual.events import Click

    class _ListApp(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.hits: list[str] = []

        def compose(self) -> ComposeResult:
            yield ListDataTable(id="j")

        def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
            self.hits.append(str(event.row_key.value))

    app = _ListApp()
    async with app.run_test() as pilot:
        table = app.query_one("#j", ListDataTable)
        style_data_table(table)
        table.add_columns("name")
        table.add_row("one", key="r0")
        table.add_row("two", key="r1")
        table.move_cursor(row=0, animate=False)
        click = Click(
            table,
            1,
            1,
            0,
            0,
            1,
            False,
            False,
            False,
            style=Style(meta={"row": 1, "column": 0}),
        )
        await table._on_click(click)
        await pilot.pause()
        assert cursor_row_key(table) == "r1"
        assert app.hits == []
        await table._on_click(click)
        await pilot.pause()
        assert app.hits == ["r1"]


# ── Cell updates and marker columns ──────────────────────────────────────

from groket.ui.data_table import (
    set_marker_column,
    update_row_cell,
)


@pytest.mark.asyncio
async def test_update_row_cell_basic():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("mark", "name")
        table.add_row("", "alice", key="a")
        table.add_row("", "bob", key="b")
        assert update_row_cell(table, "a", 0, "X") is True
        assert update_row_cell(table, "a", 5, "Y") is False  # out of range
        assert update_row_cell(table, "", 0, "Z") is False  # empty key


@pytest.mark.asyncio
async def test_update_row_cell_negative_index():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("mark", "name")
        table.add_row("", "alice", key="a")
        assert update_row_cell(table, "a", -1, "X") is False


@pytest.mark.asyncio
async def test_set_marker_column_custom():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("mark", "name")
        table.add_row("", "alice", key="a")
        assert set_marker_column(table, "a", True, on="●", off="○") is True
        assert set_marker_column(table, "a", False, on="●", off="○") is True


@pytest.mark.asyncio
async def test_set_marker_column_default_uses_selection_mark():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns(" ", "name")
        table.add_row(selection_mark(False), "x", key="x")
        assert set_marker_column(table, "x", True) is True


@pytest.mark.asyncio
async def test_set_marker_column_star_on():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns(" ", "name")
        table.add_row(selection_mark(False), "x", key="x")
        assert set_marker_column(table, "x", True, on="*", off=" ") is True


@pytest.mark.asyncio
async def test_cursor_row_key_empty_table():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name")
        assert cursor_row_key(table) is None


@pytest.mark.asyncio
async def test_restore_cursor_empty_table():
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name")
        assert restore_cursor(table, "any") is False
        assert restore_cursor(table, None) is False


class _ListTableApp(App):
    def compose(self) -> ComposeResult:
        yield ListDataTable(id="lt")


@pytest.mark.asyncio
async def test_list_data_table_helpers():
    app = _ListTableApp()
    async with app.run_test():
        table = app.query_one("#lt", ListDataTable)
        table.add_columns("name")
        table.add_row("a", key="a")
        table.add_row("b", key="b")
        table.move_cursor(row=1, animate=False)
        assert table.cursor_key() == "b"
        assert table.restore_row("a") is True
        with table.preserving():
            table.clear()
            table.add_row("a", key="a")
            table.add_row("b", key="b")
        assert table.cursor_key() is not None


@pytest.mark.asyncio
async def test_cursor_row_key_fallback_via_rows_keys():
    """cursor_row_key falls back via rows.keys() iteration."""
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name")
        table.add_row("a", key="a")
        table.add_row("b", key="b")
        table.move_cursor(row=1, animate=False)
        # Primary path (coordinate_to_cell_key) should work
        key = cursor_row_key(table)
        assert key == "b"


@pytest.mark.asyncio
async def test_restore_cursor_fallback_loop():
    """restore_cursor returns False when get_row_index fails."""
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name")
        table.add_row("a", key="a")
        table.add_row("b", key="b")
        # Normal restore works
        assert restore_cursor(table, "b") is True
        # Missing key falls back to False
        assert restore_cursor(table, "missing") is False


@pytest.mark.asyncio
async def test_update_row_cell_exception_returns_false():
    """update_row_cell returns False on nonexistent key."""
    app = _TableApp()
    async with app.run_test():
        table = app.query_one("#t", DataTable)
        style_data_table(table)
        table.add_columns("name")
        table.add_row("a", key="a")
        # Invalid key should return False via exception path
        assert update_row_cell(table, "nonexistent", 0, "X") is False
