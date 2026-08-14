"""Shared DataTable UX practices — row cursor, zebra, stable selection.

Screens must not reimplement cursor capture/restore. Prefer:

1. ``style_data_table(table)`` on every list-style table at mount.
2. ``preserving_cursor(table)`` around any ``clear()`` + ``add_row()`` rebuild
   so highlight does not jump to row 0 after toggle/search/refresh.
3. ``selection_mark`` / ``set_selection_marker`` for multi-select (same green
   ``*`` as the session list) — update in place, never clear on toggle.
4. ``focus_primary_list`` from ``bindings`` after first populate (not after every
   in-place toggle — that steals focus and can reset the row).

See also session list in ``app.py`` and run configs — both delegate here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress

from rich.text import Text
from textual.binding import Binding
from textual.widgets import DataTable

from groket.keys import load_keymap


def style_data_table(table: DataTable, *, zebra: bool = True) -> DataTable:
    """Apply consistent list-table behaviour used across the app."""
    table.cursor_type = "row"
    table.zebra_stripes = zebra
    keymap = load_keymap()
    down = keymap.binding("list.down").chord
    up = keymap.binding("list.up").chord
    table._bindings._add_binding(Binding(down, "cursor_down", "Down", show=False, id="list.down"))
    table._bindings._add_binding(Binding(up, "cursor_up", "Up", show=False, id="list.up"))
    return table


def selection_mark(selected: bool) -> Text:
    """Session-list style indicator: bold green ``*`` vs space."""
    return Text("*", style="bold green") if selected else Text(" ")


def cursor_row_key(table: DataTable) -> str | None:
    """Stable row key for the highlighted row (``add_row(..., key=...)`` value)."""
    with suppress(Exception):
        if not table.row_count:
            return None
        row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        if row_key is not None and row_key.value is not None:
            return str(row_key.value)
    with suppress(Exception):
        row = table.cursor_row
        if row is None or row < 0:
            return None
        keys = list(table.rows.keys())
        if 0 <= row < len(keys):
            return str(keys[row].value)
    return None


def restore_cursor(table: DataTable, row_key_value: str | None, *, scroll: bool = True) -> bool:
    """Move cursor to the row with the given key after a table rebuild.

    Returns True if the cursor was placed on a matching row.
    """
    if not row_key_value or not table.row_count:
        return False
    target = str(row_key_value)
    with suppress(Exception):
        idx = table.get_row_index(target)
        table.move_cursor(row=idx, animate=False, scroll=scroll)
        return True
    with suppress(Exception):
        for idx, rk in enumerate(table.rows.keys()):
            if str(rk.value) == target:
                table.move_cursor(row=idx, animate=False, scroll=scroll)
                return True
    return False


@contextmanager
def preserving_cursor(table: DataTable, *, scroll: bool = True) -> Iterator[str | None]:
    """Capture row key before a rebuild; restore after the ``with`` block.

    Usage::

        with preserving_cursor(table) as _key:
            table.clear()
            for ...:
                table.add_row(..., key=...)
    """
    key = cursor_row_key(table)
    try:
        yield key
    finally:
        if key:
            restore_cursor(table, key, scroll=scroll)


@contextmanager
def preserving_scroll(table: DataTable) -> Iterator[None]:
    """Keep ``scroll_x`` / ``scroll_y`` across ``clear()`` + ``add_row()``.

    Live home-list paints used to snap the viewport back to the left whenever
    duration or status ticked.
    """
    x = getattr(table, "scroll_x", 0)
    y = getattr(table, "scroll_y", 0)

    def _restore() -> None:
        with suppress(Exception):
            cur_x = getattr(table, "scroll_x", 0)
            cur_y = getattr(table, "scroll_y", 0)
            # A stale call_after_refresh from an earlier paint must not
            # overwrite a newer explicit scroll (Pilot / live tick race).
            if (cur_x and cur_x != x) or (cur_y and cur_y != y):
                return
            if hasattr(table, "scroll_to"):
                table.scroll_to(x, y, animate=False)
            else:
                table.scroll_x = x
                table.scroll_y = y

    try:
        yield
    finally:
        _restore()
        # clear()+add_row leaves max_scroll_x at 0 until the next layout pass.
        cb = getattr(table, "call_after_refresh", None)
        if callable(cb):
            cb(_restore)


def update_row_cell(
    table: DataTable,
    row_key_value: str,
    column_index: int,
    value: str | int | float | bool | None | Text,
) -> bool:
    """Update one cell by row key + column index (no clear, no cursor jump)."""
    if not row_key_value:
        return False
    try:
        cols = list(table.columns.keys())
        if column_index < 0 or column_index >= len(cols):
            return False
        table.update_cell(row_key_value, cols[column_index], value)
        return True
    except Exception:
        return False


def set_selection_marker(
    table: DataTable, row_key_value: str, selected: bool, *, column_index: int = 0
) -> bool:
    """Set column 0 (default) to the green ``*`` session-style selection mark."""
    return update_row_cell(table, row_key_value, column_index, selection_mark(selected))


def set_marker_column(
    table: DataTable,
    row_key_value: str,
    selected: bool,
    *,
    column_index: int = 0,
    on: str | None = None,
    off: str | None = None,
) -> bool:
    """Set a selection-marker column.

    Default is session-style green ``*`` (``selection_mark``). Pass ``on``/``off``
    for a custom marker glyph (e.g. run-config ``●``).
    """
    if on is None and off is None:
        return set_selection_marker(table, row_key_value, selected, column_index=column_index)
    on_v: str | Text = selection_mark(True) if on is None else on
    off_v: str | Text = selection_mark(False) if off is None else off
    if on == "*" and off in (" ", None) and (not isinstance(on, Text)):
        return set_selection_marker(table, row_key_value, selected, column_index=column_index)
    return update_row_cell(table, row_key_value, column_index, on_v if selected else off_v)


class ListDataTable(DataTable):
    """DataTable with app defaults and cursor helpers on the instance."""

    def on_mount(self) -> None:
        style_data_table(self)

    def cursor_key(self) -> str | None:
        return cursor_row_key(self)

    def restore_row(self, row_key_value: str | None, *, scroll: bool = True) -> bool:
        return restore_cursor(self, row_key_value, scroll=scroll)

    def preserving(self, *, scroll: bool = True):
        return preserving_cursor(self, scroll=scroll)
