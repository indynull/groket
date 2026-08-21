"""Centered, scrollable keyboard help modal (?)."""

from __future__ import annotations

from contextlib import suppress

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button

from .. import text as U
from ..i18n import t
from ..quit_actions import QuitActions
from ..selectable_static import SelectableStatic
from ..text import help_markup as _help_markup


def help_markup() -> str:
    """Rich markup for the unified ? help panel (identical on every screen)."""
    return _help_markup()


def notify_help(screen: Screen, markup: str | None = None) -> None:
    """Open the centered help modal (not a toast — full text must fit)."""
    if isinstance(screen.app.screen, HelpModal):
        screen.app.screen.dismiss(None)
        return
    screen.app.push_screen(HelpModal(markup=markup))


class HelpModal(QuitActions, ModalScreen[None]):
    """Full help text in a centered panel (Esc, ?, Enter, or Close).

    Sized with % / 1fr so the panel tracks terminal resize fluidly.
    """

    def __init__(self, markup: str | None = None) -> None:
        super().__init__()
        self._markup = markup if markup is not None else help_markup()

    DEFAULT_CSS = """
    /* Backdrop translucency comes from app.tcss ModalScreen; keep panel solid. */
    HelpModal {
        align: center middle;
    }

    #help-modal {
        width: 80%;
        height: 80%;
        max-width: 100;
        max-height: 100%;
        min-width: 40;
        min-height: 12;
        layout: vertical;
        border: tall $primary;
        background: $panel;
        padding: 1 2;
    }

    #help-modal-body {
        height: 1fr;
        width: 100%;
        min-height: 4;
        overflow-y: auto;
        scrollbar-gutter: stable;
    }

    #help-modal-text {
        width: 100%;
        height: auto;
        padding: 0;
    }

    #help-modal-actions {
        height: auto;
        min-height: 2;
        dock: bottom;
        width: 100%;
        align: right middle;
        margin: 0;
        padding: 1 0 0 0;
        background: $panel;
    }

    #help-modal-actions Button {
        min-width: 10;
    }
    """
    BINDINGS = [
        Binding("escape", "dismiss", t("ui-cancel"), id="overlay.hide", show=True),
        Binding("?", "dismiss", t("ui-close"), id="help.toggle", show=False),
        Binding("enter", "dismiss", t("ui-close"), id="help.dismiss", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-modal"):
            with VerticalScroll(id="help-modal-body"):
                yield SelectableStatic(self._markup, id="help-modal-text")
            with Horizontal(id="help-modal-actions"):
                yield Button(U.close(), variant="primary", id="help-close")

    def on_mount(self) -> None:
        with suppress(Exception):
            self.query_one("#help-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "help-close":
            self.dismiss(None)
