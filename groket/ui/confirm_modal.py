"""Confirm discarding unsaved form edits (Esc / Cancel on editors)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from . import text as U
from .i18n import t
from .quit_actions import QuitActions


class DiscardConfirmModal(QuitActions, ModalScreen[bool]):
    """Ask whether to discard edits. Result ``True`` = discard and leave."""

    BINDINGS = [
        Binding("escape", "keep", U.bind_cancel(), id="overlay.hide", show=True),
        Binding("enter,y", "discard", t("ui-discard"), id="confirm.discard", show=False),
        Binding("n", "keep", U.bind_cancel(), id="confirm.keep", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="discard-confirm-modal"):
            yield Static(t("discard-unsaved-title"), id="discard-confirm-title")
            yield Static(t("discard-unsaved-body"), id="discard-confirm-body")
            with Horizontal(id="discard-confirm-actions", classes="modal-footer"):
                yield Button(t("ui-discard"), variant="error", id="discard-confirm-yes")
                yield Button(t("ui-keep-editing"), variant="primary", id="discard-confirm-no")

    def action_discard(self) -> None:
        self.dismiss(True)

    def action_keep(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "discard-confirm-yes":
            self.dismiss(True)
        elif event.button.id == "discard-confirm-no":
            self.dismiss(False)
