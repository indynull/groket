"""Mount major screens and modals; assert user-visible chrome."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from .pilot_helpers import static_plain


@pytest.mark.asyncio
async def test_run_configs_screen_mount(tmp_path: Path):
    from groket.runs.run_manager import RunManager
    from groket.ui.screens.run_configs import RunConfigsScreen

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(RunConfigsScreen(tmp_path, RunManager(tmp_path)))

    async with H().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        from groket.ui.screens.run_configs import RunConfigsScreen as RCS

        assert isinstance(pilot.app.screen, RCS)
        table = pilot.app.screen.query_one("#rc-table", DataTable)
        assert table.row_count == 0


@pytest.mark.asyncio
async def test_flag_modal_mount():
    from groket.models import TraceEvent
    from groket.ui.widgets.flag_panel import FlagModal

    ev = TraceEvent(index=0, event_type="tool_call", content="x", tool_name="grep")

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(FlagModal(ev))

    async with H().run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        from groket.ui.widgets.flag_panel import FlagModal as FM

        assert isinstance(pilot.app.screen, FM)
        # Modal shows the event under review (tool name and/or content).
        body = "\n".join(static_plain(w) for w in pilot.app.screen.query(Static))
        assert "grep" in body.lower() or "tool" in body.lower() or "x" in body


@pytest.mark.asyncio
async def test_help_modal_mount():
    from groket.ui.widgets.help_modal import HelpModal

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(HelpModal())

    async with H().run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        from groket.ui.widgets.help_modal import HelpModal as HM

        assert isinstance(pilot.app.screen, HM)
        body = "\n".join(static_plain(w) for w in pilot.app.screen.query(Static))
        # Help content is non-empty prose (bindings / overview).
        assert len(body.strip()) > 20


@pytest.mark.asyncio
async def test_personas_screen_mount(tmp_path: Path):
    from groket.ui.screens.personas import PersonasScreen

    class H(App[None]):
        async def on_mount(self) -> None:
            self.push_screen(PersonasScreen(tmp_path))

    async with H().run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        from groket.ui.screens.personas import PersonasScreen as PS

        assert isinstance(pilot.app.screen, PS)
        table = pilot.app.screen.query_one("#pb-table", DataTable)
        # Defaults are ensured on mount.
        assert table.row_count >= 1


@pytest.mark.asyncio
async def test_detail_view_mount():
    from groket.ui.widgets.detail_view import DetailView

    class H(App[None]):
        def compose(self):
            yield DetailView(id="dv")

    async with H().run_test() as pilot:
        await pilot.pause()
        dv = pilot.app.query_one("#dv", DetailView)
        body = dv.query_one("#detail-body", Static)
        assert static_plain(body).strip() == ""
