"""Shared TabbedContent pane navigation (``[`` / ``]`` / digit keys).

Screens and modals set :attr:`TAB_CONTENT_ID` and :attr:`TAB_PANES`, mix in
:class:`TabPaneNavigation`, and include :func:`tab_nav_bindings` in BINDINGS.
Do not reimplement ``action_tab_prev`` / ``action_tab_next`` per screen.
"""

from __future__ import annotations

from contextlib import suppress
from typing import ClassVar

from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import TabbedContent

from . import text as U
from .i18n import t


def _bind(key: str, action: str, description: str, *, id: str, show: bool = True) -> Binding:
    return Binding(key, action, description, show=show, id=id)


def tab_nav_bindings(n_panes: int) -> tuple[Binding, ...]:
    """``[`` / ``]`` plus ``1``…``N`` (max 9) mapped to ``tab_pane_k`` actions."""
    n = max(0, min(9, int(n_panes)))
    digits = tuple(
        _bind(
            str(i),
            f"tab_pane_{i}",
            t("bind-pane-digit"),
            id=f"app.pane.{i}",
            show=False,
        )
        for i in range(1, n + 1)
    )
    return (
        _bind(
            "left_square_bracket",
            "tab_prev",
            U.bind_prev_pane(),
            id="app.pane.prev",
            show=True,
        ),
        _bind(
            "right_square_bracket",
            "tab_next",
            U.bind_next_pane(),
            id="app.pane.next",
            show=True,
        ),
    ) + digits


class TabPaneNavigation:
    """Mixin: consistent ``[`` / ``]`` / ``1``…``N`` for a primary TabbedContent.

    Mix into a Textual :class:`~textual.widget.Widget` subclass (Screen /
    ModalScreen). Set on the screen/modal class:

    * ``TAB_CONTENT_ID`` — widget id of the TabbedContent (e.g. ``browser-tabs``)
    * ``TAB_PANES`` — ordered pane ids, or ``(pane_id, focus_selector | None)`` pairs
    """

    TAB_CONTENT_ID: ClassVar[str] = ""
    TAB_PANES: ClassVar[tuple[str | tuple[str, str | None], ...]] = ()

    def _tab_host(self) -> Widget:
        if not isinstance(self, Widget):
            raise TypeError("TabPaneNavigation requires a Textual Widget host")
        return self

    def _tab_pane_specs(self) -> list[tuple[str, str | None]]:
        specs: list[tuple[str, str | None]] = []
        for item in self.TAB_PANES:
            if isinstance(item, tuple):
                pid, sel = item[0], item[1] if len(item) > 1 else None
                specs.append((str(pid), sel))
            else:
                specs.append((str(item), None))
        return specs

    def _tab_pane_index(self) -> int:
        specs = self._tab_pane_specs()
        if not specs:
            return 0
        host = self._tab_host()
        try:
            active = host.query_one(f"#{self.TAB_CONTENT_ID}", TabbedContent).active
        except Exception:
            return 0
        for i, (pid, _) in enumerate(specs):
            if pid == active:
                return i
        return 0

    def activate_tab_pane(self, pane_id: str, *, focus_selector: str | None = None) -> None:
        """Switch to *pane_id* and optionally focus a child after layout."""
        host = self._tab_host()
        specs = self._tab_pane_specs()
        if focus_selector is None:
            for pid, sel in specs:
                if pid == pane_id:
                    focus_selector = sel
                    break
        try:
            tabs = host.query_one(f"#{self.TAB_CONTENT_ID}", TabbedContent)
            tabs.active = pane_id
        except Exception:
            try:
                host.query_one(TabbedContent).active = pane_id
            except Exception:
                return

        if focus_selector:
            sel = focus_selector

            def _focus_sel() -> None:
                from .bindings import focus_primary_list

                with suppress(Exception):
                    focus_primary_list(host.query_one(sel))

            host.call_after_refresh(lambda: host.call_after_refresh(_focus_sel))
            return

        def _focus_first() -> None:
            from .bindings import focus_primary_list

            with suppress(Exception):
                pane = host.query_one(f"#{pane_id}")
                children = [c for c in pane.walk_children() if isinstance(c, Widget)]
                for w in children:
                    if w.can_focus and not w.disabled:
                        name = type(w).__name__
                        if name in ("DataTable", "ListDataTable", "TimelineTable"):
                            focus_primary_list(w)
                            return
                for w in children:
                    if w.can_focus and not w.disabled:
                        with suppress(Exception):
                            w.focus()
                        return

        host.call_after_refresh(lambda: host.call_after_refresh(_focus_first))

    def _activate_tab_index(self, index: int) -> None:
        specs = self._tab_pane_specs()
        if not specs:
            return
        i = index % len(specs)
        pid, sel = specs[i]
        self.activate_tab_pane(pid, focus_selector=sel)

    def action_tab_next(self) -> None:
        self._activate_tab_index(self._tab_pane_index() + 1)

    def action_tab_prev(self) -> None:
        self._activate_tab_index(self._tab_pane_index() - 1)

    def action_tab_pane_1(self) -> None:
        self._activate_tab_index(0)

    def action_tab_pane_2(self) -> None:
        self._activate_tab_index(1)

    def action_tab_pane_3(self) -> None:
        self._activate_tab_index(2)

    def action_tab_pane_4(self) -> None:
        self._activate_tab_index(3)

    def action_tab_pane_5(self) -> None:
        self._activate_tab_index(4)

    def action_tab_pane_6(self) -> None:
        self._activate_tab_index(5)

    def action_tab_pane_7(self) -> None:
        self._activate_tab_index(6)

    def action_tab_pane_8(self) -> None:
        self._activate_tab_index(7)

    def action_tab_pane_9(self) -> None:
        self._activate_tab_index(8)
