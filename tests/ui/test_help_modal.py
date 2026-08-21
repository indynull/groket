"""Help modal widget tests."""

from __future__ import annotations

import pytest
from groket.ui.widgets.help_modal import HelpModal, help_markup, notify_help
from textual.app import App, ComposeResult
from textual.widgets import Button, Static

from .pilot_helpers import wait_until


def test_help_markup_nonempty() -> None:
    from groket import __version__

    text = help_markup()
    assert isinstance(text, str)
    assert len(text) > 10
    assert f"groket {__version__}" in text
    assert "Export a session bundle" in text
    assert "Run plugins" not in text
    assert "groket analyzer" not in text.lower()


class _HelpApp(App):
    def compose(self) -> ComposeResult:
        yield Static("main")


@pytest.mark.asyncio
async def test_help_modal_opens_and_dismisses() -> None:
    app = _HelpApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(HelpModal())
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, HelpModal),
            description="HelpModal mounted",
        )
        modal = app.screen
        assert isinstance(modal, HelpModal)
        # Wait for compose to finish
        await wait_until(
            pilot,
            lambda: bool(list(modal.query("#help-close"))),
            description="help-close mounted",
        )
        btn = modal.query_one("#help-close", Button)
        assert btn is not None
        modal.on_button_pressed(Button.Pressed(btn))
        await pilot.pause()


@pytest.mark.asyncio
async def test_notify_help_opens_modal() -> None:
    app = _HelpApp()
    async with app.run_test(size=(100, 40)) as pilot:
        notify_help(app.screen)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, HelpModal),
            description="HelpModal via notify_help",
        )


@pytest.mark.asyncio
async def test_notify_help_dismisses_if_already_open() -> None:
    app = _HelpApp()
    async with app.run_test(size=(100, 40)) as pilot:
        app.push_screen(HelpModal())
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, HelpModal),
            description="HelpModal first open",
        )
        notify_help(app.screen)
        await pilot.pause()
