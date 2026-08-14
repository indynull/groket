"""Modal: single path (or free text) input with save/cancel."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from .i18n import t
from .quit_actions import QuitActions


class PathInputModal(QuitActions, ModalScreen[str | None]):
    """Ask for a filesystem path. Result is the raw input string, or None if cancelled."""

    BINDINGS = [
        Binding("escape", "cancel", t("ui-cancel"), id="overlay.hide", show=True),
        Binding("ctrl+s", "submit", t("ui-save"), id="edit.save", show=True),
        Binding("enter", "submit", t("ui-save"), id="modal.submit_enter", show=False),
    ]

    def __init__(
        self,
        *,
        title: str,
        initial: str = "",
        placeholder: str = "",
        hint: str = "",
    ) -> None:
        super().__init__()
        self._title = title
        self._initial = initial
        self._placeholder = placeholder
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="path-input-modal"):
            yield Static(self._title, id="path-input-title")
            if self._hint:
                yield Static(self._hint, id="path-input-hint")
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                id="path-input-field",
            )
            with Horizontal(id="path-input-actions", classes="modal-footer"):
                yield Button(t("ui-save"), variant="primary", id="path-input-save")
                yield Button(t("ui-cancel"), id="path-input-cancel")

    def on_mount(self) -> None:
        self.query_one("#path-input-field", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        self._submit()

    @on(Button.Pressed, "#path-input-cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#path-input-save")
    def _save_btn(self) -> None:
        self._submit()

    def _submit(self) -> None:
        raw = self.query_one("#path-input-field", Input).value.strip()
        if not raw:
            self.notify(t("path-input-empty"), severity="error")
            return
        # Expand ~ for a better confirmation path display later; keep as typed.
        try:
            Path(raw).expanduser()
        except Exception:
            pass
        self.dismiss(raw)
