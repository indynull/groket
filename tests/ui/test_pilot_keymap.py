"""Pilot: keys.toml remaps drive set_keymap and footer display."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.ui.app import TraceEvalApp
from groket.ui.screens.runner import RunnerScreen
from textual.widgets import DataTable

from .pilot_helpers import wait_until


def _minimal_traces(work: Path) -> Path:
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sd = traces / "pilot-keymap-1"
    sd.mkdir()
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-keymap-1", "cwd": "/workspace"},
                "session_summary": "pilot",
                "created_at": "2026-06-25T00:00:00Z",
                "updated_at": "2026-06-25T00:01:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    (sd / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "ts": "2026-06-25T00:01:00Z", "outcome": "success"})
        + "\n",
        encoding="utf-8",
    )
    return traces


def _binding_keys(app: TraceEvalApp, binding_id: str) -> set[str]:
    return {
        key
        for key, ab in app.active_bindings.items()
        if getattr(ab.binding, "id", None) == binding_id
    }


def _footer_key_displays(app: TraceEvalApp) -> list[str]:
    footer = app.query_one("Footer")
    return [str(w.key_display).lower() for w in footer.query("FooterKey")]


@pytest.mark.asyncio
async def test_overlay_remap_updates_footer_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"home.runner" = "z"\n', encoding="utf-8")
    monkeypatch.setenv("GROKET_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: (
                "z" in _binding_keys(app, "home.runner")
                and any(d == "z" for d in _footer_key_displays(app))
            ),
            description="home.runner remapped to z in bindings and footer",
        )
        assert _binding_keys(app, "home.runner") == {"z"}
        displays = _footer_key_displays(app)
        assert any(d == "z" for d in displays)
        assert not any(d == "r" for d in displays)
        await pilot.press("z")
        await wait_until(
            pilot,
            lambda: any(isinstance(s, RunnerScreen) for s in app.screen_stack),
            description="remapped z opens runner",
        )


@pytest.mark.asyncio
async def test_refused_overlay_keeps_default_follow_and_list_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"list.down" = "n"\n', encoding="utf-8")
    monkeypatch.setenv("GROKET_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    gate = traces / ".groket-turn"
    gate.mkdir(parents=True, exist_ok=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "pilot-keymap-1", "turn": 1})
        + "\n",
        encoding="utf-8",
    )
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: (
                app._keymap.get("session.follow") == "n" and app._keymap.get("list.down") == "j"
            ),
            description="refused overlay leaves follow=n and list.down=j",
        )
        assert app._keymap["session.follow"] == "n"
        assert app._keymap["list.down"] == "j"
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 1, description="session row")
        table.focus()
        await wait_until(
            pilot,
            lambda: _binding_keys(app, "list.down") == {"j"},
            description="focused table still uses default j",
        )
        start = table.cursor_row
        await pilot.press("n")
        await pilot.pause()
        assert table.cursor_row == start


@pytest.mark.asyncio
async def test_default_keymap_footer_unchanged(tmp_path: Path) -> None:
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        await wait_until(
            pilot,
            lambda: "r" in _binding_keys(app, "home.runner"),
            description="home.runner default r",
        )
        assert _binding_keys(app, "home.runner") == {"r"}
        help_keys = _binding_keys(app, "help.toggle")
        assert help_keys, "help.toggle stays bound"
        assert "z" not in help_keys


@pytest.mark.asyncio
async def test_list_down_remap_moves_table_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = tmp_path / "keys.toml"
    keys.write_text('[home]\n"list.down" = "h"\n', encoding="utf-8")
    monkeypatch.setenv("GROKET_KEYS", str(keys))
    work = tmp_path / "w"
    traces = _minimal_traces(work)
    extra = traces / "pilot-keymap-2"
    extra.mkdir()
    (extra / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "pilot-keymap-2", "cwd": "/workspace"},
                "session_summary": "pilot-2",
                "created_at": "2026-06-25T00:02:00Z",
                "updated_at": "2026-06-25T00:03:00Z",
                "num_messages": 1,
                "current_model_id": "m1",
            }
        ),
        encoding="utf-8",
    )
    app = TraceEvalApp(work_dir=work, traces_path=traces)
    async with app.run_test(size=(120, 40)) as pilot:
        table = app.query_one("#session-table", DataTable)
        await wait_until(pilot, lambda: table.row_count >= 2, description="two session rows")
        table.focus()
        await wait_until(
            pilot,
            lambda: "h" in _binding_keys(app, "list.down"),
            description="list.down remapped to h",
        )
        table.move_cursor(row=0, animate=False)
        start = table.cursor_row
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_row == start
        await pilot.press("h")
        await wait_until(
            pilot,
            lambda: table.cursor_row != start,
            description="remapped h moves the list",
        )
