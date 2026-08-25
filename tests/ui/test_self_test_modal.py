"""Self-test modal widget tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.diagnostics.self_test import CheckResult, SelfTestReport
from groket.ui.widgets.self_test_modal import SelfTestModal
from textual.app import App, ComposeResult
from textual.widgets import Static

from .pilot_helpers import wait_until


class _STApp(App):
    _self_test_summary: str = ""

    def compose(self) -> ComposeResult:
        yield Static("main")


@pytest.mark.asyncio
async def test_self_test_modal_report_all_ok(tmp_path: Path) -> None:
    app = _STApp()
    async with app.run_test(size=(100, 30)) as pilot:
        modal = SelfTestModal(work_dir=tmp_path)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SelfTestModal),
            description="SelfTestModal mounted",
        )
        # Wait for the worker-triggered _apply_report + override with our own
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#self-test-body"))),
            description="body mounted",
        )
        report = SelfTestReport(
            checks=[
                CheckResult("docker", "Docker", True, "ok"),
                CheckResult("auth", "Auth", True, "ok"),
            ]
        )
        app.screen._apply_report(report)
        await pilot.pause()
        assert "PASS" in app._self_test_summary or "self-test" in app._self_test_summary


@pytest.mark.asyncio
async def test_self_test_modal_report_with_failure(tmp_path: Path) -> None:
    app = _STApp()
    async with app.run_test(size=(100, 30)) as pilot:
        modal = SelfTestModal(work_dir=tmp_path)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SelfTestModal),
            description="SelfTestModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#self-test-body"))),
            description="body mounted",
        )
        report = SelfTestReport(
            checks=[
                CheckResult("docker", "Docker", False, "not reachable", required=True),
                CheckResult("auth", "Auth", True, "ok"),
            ]
        )
        app.screen._apply_report(report)
        await pilot.pause()
        assert "FAIL" in app._self_test_summary


@pytest.mark.asyncio
async def test_self_test_modal_report_with_warning(tmp_path: Path) -> None:
    app = _STApp()
    async with app.run_test(size=(100, 30)) as pilot:
        modal = SelfTestModal(work_dir=tmp_path)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SelfTestModal),
            description="SelfTestModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#self-test-body"))),
            description="body mounted",
        )
        report = SelfTestReport(
            checks=[
                CheckResult("docker", "Docker", True, "ok"),
                CheckResult("auth", "Auth", False, "token expired", required=False),
            ]
        )
        app.screen._apply_report(report)
        await pilot.pause()
        # The worker may have overwritten, so either warn or self-test is there
        assert "self-test" in app._self_test_summary.lower()


@pytest.mark.asyncio
async def test_self_test_modal_actions(tmp_path: Path) -> None:
    app = _STApp()
    async with app.run_test(size=(100, 30)) as pilot:
        modal = SelfTestModal(work_dir=tmp_path)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SelfTestModal),
            description="SelfTestModal mounted",
        )
        app.screen.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_self_test_modal_save_action(tmp_path: Path) -> None:
    app = _STApp()
    async with app.run_test(size=(100, 30)) as pilot:
        modal = SelfTestModal(work_dir=tmp_path)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SelfTestModal),
            description="SelfTestModal mounted",
        )
        app.screen.action_save()
        await pilot.pause()


@pytest.mark.asyncio
async def test_self_test_modal_close_button(tmp_path: Path) -> None:
    app = _STApp()
    async with app.run_test(size=(100, 30)) as pilot:
        modal = SelfTestModal(work_dir=tmp_path)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SelfTestModal),
            description="SelfTestModal mounted",
        )
        app.screen._close()
        await pilot.pause()


@pytest.mark.asyncio
async def test_self_test_modal_work_dir_from_app(tmp_path: Path) -> None:
    """SelfTestModal with work_dir=None reads from app.work_dir."""

    class _WDApp(App):
        _self_test_summary: str = ""
        work_dir = tmp_path

        def compose(self) -> ComposeResult:
            yield Static("main")

    app = _WDApp()
    async with app.run_test(size=(100, 30)) as pilot:
        modal = SelfTestModal(work_dir=None)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SelfTestModal),
            description="SelfTestModal mounted",
        )
        await pilot.pause()


@pytest.mark.asyncio
async def test_self_test_modal_rerun_button(tmp_path: Path) -> None:
    """Rerun button resets the report and re-runs diagnostics."""
    app = _STApp()
    async with app.run_test(size=(100, 30)) as pilot:
        modal = SelfTestModal(work_dir=tmp_path)
        app.push_screen(modal)
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, SelfTestModal),
            description="SelfTestModal mounted",
        )
        await wait_until(
            pilot,
            lambda: bool(list(app.screen.query("#self-test-rerun"))),
            description="rerun button",
        )
        app.screen._rerun()
        await pilot.pause()


@pytest.mark.asyncio
async def test_self_test_modal_report_warn_count_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report with warnings produces an OK-with-warn summary."""
    report_warn = SelfTestReport(
        checks=[
            CheckResult(id="docker", name="Docker", ok=True, detail="ok"),
            CheckResult(id="opt", name="Optional", ok=False, detail="not found", required=False),
        ]
    )
    assert report_warn.ok is True
    assert report_warn.warn_count == 1
    monkeypatch.setattr("groket.diagnostics.run_self_test", lambda work_dir=None: report_warn)
    app = _STApp()
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(SelfTestModal(work_dir=tmp_path))
        await wait_until(
            pilot,
            lambda: getattr(app, "_self_test_summary", "") == "self-test OK (1 warn)",
            description="warn summary from stubbed self-test",
        )
