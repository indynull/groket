"""Shared key action catalog: ids, reserved chords, binding/HUD coverage."""

from __future__ import annotations

import importlib
import pkgutil
import re
from pathlib import Path

import pytest
from groket.keys.catalog import (
    ACTIONS,
    ACTIONS_BY_ID,
    RESERVED_KEYS,
    ActionScope,
    ActionSurface,
    KeyAction,
    action_by_id,
    chord_is_reserved,
    normalize_chord,
)
from groket.ui import bindings as B
from groket.ui.tab_panes import tab_nav_bindings
from textual.binding import Binding
from textual.screen import Screen

_REPO = Path(__file__).resolve().parents[2]
_HELP_RS = _REPO / "groket-hud" / "src" / "help.rs"
_KEYS_RS = _REPO / "groket-hud" / "src" / "keys.rs"
_PUSH = re.compile(
    r'push(?:_mapped)?\(\s*&mut table,\s*(?:overlay,\s*)?"([^"]+)",\s*"[^"]*",\s*"([^"]+)"',
    re.S,
)

_BINDING_TUPLES: tuple[tuple[Binding, ...], ...] = (
    B.APP_GLOBAL_PRIORITY,
    B.GLOBAL_ALWAYS,
    B.LIST_SELECT,
    B.LIST_SELECT_ALL,
    B.APP_SESSIONS,
    B.SCREEN_CHROME,
    B.BROWSER,
    B.RUNNER,
    B.RUN_CONFIGS,
    B.CAPABILITY_PICKER,
    B.PERSONAS,
    B.PERSONA_EDITOR,
    B.MODAL_CANCEL_QUIT,
    B.FORM_SAVE,
    B.RULES,
    B.MODAL_DISMISS,
    B.JOBS_MODAL,
)


def _hud_push_rows(source: str) -> list[tuple[str, str]]:
    rows = list(_PUSH.findall(source))
    if 'format!("pane.{n}")' in source:
        rows.extend((f"pane.{i}", f"ctrl+{i}") for i in range(1, 6))
    return rows


def _key_matches_default(key: str, default: str) -> bool:
    key_parts = {normalize_chord(p) for p in key.split(",") if p.strip()}
    default_parts = {normalize_chord(p) for p in default.split(",") if p.strip()}
    return bool(key_parts) and key_parts <= default_parts


def _declared_bindings(raw: object) -> list[Binding]:
    if not raw:
        return []
    out: list[Binding] = []
    for item in raw:
        if isinstance(item, Binding):
            out.append(item)
        elif isinstance(item, tuple) and 2 <= len(item) <= 3:
            out.extend(Binding.make_bindings([item]))
    return out


def _import_ui_package() -> None:
    import groket.ui as ui_pkg

    for info in pkgutil.walk_packages(ui_pkg.__path__, ui_pkg.__name__ + "."):
        importlib.import_module(info.name)


def _local_screens() -> list[type[Screen]]:
    _import_ui_package()
    seen: set[type[Screen]] = set()
    stack: list[type[Screen]] = list(Screen.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return [cls for cls in seen if cls.__module__.startswith("groket.")]


def test_catalog_ids_are_globally_unique() -> None:
    ids = [row.id for row in ACTIONS]
    assert len(ids) == len(set(ids))
    assert set(ACTIONS_BY_ID) == set(ids)


def test_catalog_ids_are_dotted() -> None:
    for row in ACTIONS:
        assert "." in row.id, row.id
        assert row.id == row.id.strip()
        assert row.scope in ActionScope
        assert row.surfaces in ActionSurface


def test_reserved_keys_are_not_remappable() -> None:
    assert action_by_id("help.toggle").remappable is False
    assert action_by_id("overlay.hide").remappable is False
    assert action_by_id("session.open").remappable is False
    assert action_by_id("pane.next").remappable is False
    assert action_by_id("pane.prev").remappable is False
    for row in ACTIONS:
        if chord_is_reserved(row.default):
            assert row.remappable is False, row.id


def test_reserved_set_covers_spec_keys() -> None:
    for key in ("escape", "enter", "tab", "shift+tab", "?"):
        assert key in RESERVED_KEYS
        assert chord_is_reserved(key)
    assert chord_is_reserved("esc")
    assert not chord_is_reserved("ctrl+enter,ctrl+j")
    assert not chord_is_reserved("n")
    assert not chord_is_reserved("")


def test_normalize_chord_textual_and_hud() -> None:
    assert normalize_chord("slash") == normalize_chord("/")
    assert normalize_chord("N") == normalize_chord("shift+n")
    assert normalize_chord("left_square_bracket") == normalize_chord("[")
    assert normalize_chord("right_square_bracket") == normalize_chord("]")
    assert normalize_chord("esc") == "escape"
    assert normalize_chord("f5,ctrl+r") == "f5,ctrl+r"
    assert normalize_chord("s,space") == "s,space"


def test_default_chords_match_today() -> None:
    assert action_by_id("session.follow").default == "n"
    assert action_by_id("session.done").default == "e"
    assert action_by_id("edit.copy").default == "y"
    assert action_by_id("help.toggle").default == "?"
    assert action_by_id("list.down").default == "j"
    assert action_by_id("list.up").default == "k"
    assert action_by_id("edit.copy_chord").default == "ctrl+shift+c"


def test_tuple_bindings_match_catalog_defaults() -> None:
    missing: list[str] = []
    drifted: list[str] = []
    for tup in _BINDING_TUPLES:
        for binding in tup:
            bid = binding.id
            if not bid or bid not in ACTIONS_BY_ID:
                missing.append(f"{binding.key}->{binding.action}:{bid!r}")
                continue
            default = ACTIONS_BY_ID[bid].default
            if not _key_matches_default(binding.key, default):
                drifted.append(f"{bid}: key={binding.key!r} default={default!r}")
    assert missing == []
    assert drifted == []


def test_every_screen_binding_has_catalog_id() -> None:
    missing: list[str] = []
    drifted: list[str] = []
    for cls in _local_screens():
        if "BINDINGS" not in cls.__dict__:
            continue
        for binding in _declared_bindings(cls.__dict__["BINDINGS"]):
            bid = binding.id
            label = f"{cls.__module__}.{cls.__name__} {binding.key}->{binding.action}"
            if not bid or bid not in ACTIONS_BY_ID:
                missing.append(f"{label}:{bid!r}")
                continue
            default = ACTIONS_BY_ID[bid].default
            if not _key_matches_default(binding.key, default):
                drifted.append(f"{label}: key={binding.key!r} default={default!r}")
    assert missing == []
    assert drifted == []


def test_same_id_alternatives_are_one_binding() -> None:
    for name in ("app.refresh", "list.select", "session.delete", "personas.delete"):
        hits = [b for tup in _BINDING_TUPLES for b in tup if b.id == name]
        keys = {b.key for b in hits}
        assert all("," in k or k == ACTIONS_BY_ID[name].default for k in keys), (name, keys)


def test_copy_keeps_two_ids() -> None:
    assert "edit.copy" in ACTIONS_BY_ID
    assert "edit.copy_chord" in ACTIONS_BY_ID
    assert action_by_id("edit.copy").default == "y"
    assert action_by_id("edit.copy_chord").default == "ctrl+shift+c"


def test_every_hud_push_id_and_spec_matches_catalog() -> None:
    source = _HELP_RS.read_text(encoding="utf-8")
    rows = _hud_push_rows(source)
    assert rows, "help.rs should declare push ids"
    missing = sorted({action_id for action_id, _ in rows} - set(ACTIONS_BY_ID))
    assert missing == []
    drifted: list[str] = []
    for action_id, spec in rows:
        default = ACTIONS_BY_ID[action_id].default
        if normalize_chord(spec) != normalize_chord(default):
            drifted.append(f"{action_id}: hud={spec!r} default={default!r}")
    assert drifted == []


def test_hud_named_ids_present() -> None:
    for action_id in (
        "help.toggle",
        "overlay.hide",
        "session.open",
        "list.down",
        "list.up",
        "search.focus",
        "pane.next",
        "pane.prev",
        "pane.1",
        "pane.5",
        "edit.copy",
        "edit.copy_chord",
        "session.follow",
        "session.done",
        "pane.notes",
        "events.next_turn",
        "events.all_turns",
        "turns.timeline",
    ):
        assert action_id in ACTIONS_BY_ID


def test_list_nav_is_shared_table_binding() -> None:
    from groket.ui.data_table import style_data_table
    from textual.widgets import DataTable

    down = action_by_id("list.down")
    up = action_by_id("list.up")
    assert down.surfaces is ActionSurface.SHARED
    assert up.surfaces is ActionSurface.SHARED
    assert down.tui_action is None
    assert up.tui_action is None
    table = DataTable()
    style_data_table(table)
    ids = {binding.id for _key, binding in table._bindings}
    assert "list.down" in ids
    assert "list.up" in ids


def test_tab_nav_bindings_use_catalog_ids() -> None:
    nav = tab_nav_bindings(9)
    assert {b.id for b in nav} == {
        "app.pane.prev",
        "app.pane.next",
        *(f"app.pane.{i}" for i in range(1, 10)),
    }
    for binding in nav:
        assert binding.id in ACTIONS_BY_ID
        assert _key_matches_default(binding.key, ACTIONS_BY_ID[binding.id].default)


def test_hud_overlay_catalog_matches_python() -> None:
    """Rust overlay catalog must stay aligned with ACTIONS."""
    text = _KEYS_RS.read_text(encoding="utf-8")
    rows = re.findall(
        r'id:\s*"([^"]+)",\s*scope:\s*"([^"]+)",\s*default:\s*"([^"]+)",\s*remappable:\s*(true|false)',
        text,
    )
    found = {action_id: (scope, default, rem == "true") for action_id, scope, default, rem in rows}
    missing = sorted(set(ACTIONS_BY_ID) - set(found))
    extra = sorted(set(found) - set(ACTIONS_BY_ID))
    assert missing == []
    assert extra == []
    drifted = []
    for row in ACTIONS:
        scope, default, remappable = found[row.id]
        if scope != row.scope.value or default != row.default or remappable is not row.remappable:
            drifted.append(
                f"{row.id}: rust=({scope!r},{default!r},{remappable}) "
                f"py=({row.scope.value!r},{row.default!r},{row.remappable})"
            )
    assert drifted == []


def test_action_by_id_roundtrip() -> None:
    row = action_by_id("session.follow")
    assert isinstance(row, KeyAction)
    assert row is ACTIONS_BY_ID["session.follow"]
    with pytest.raises(KeyError):
        action_by_id("not.an.action")
