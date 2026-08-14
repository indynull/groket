"""keys.toml overlay: merge, refuse-on-error, occupancy, CLI."""

from __future__ import annotations

from pathlib import Path

from groket.keys.catalog import action_by_id, normalize_chord
from groket.keys.overlay import (
    KEYS_ENV,
    Keymap,
    OverlayErrorKind,
    chord_has_sequence,
    format_keymap_table,
    format_occupancy,
    load_keymap,
    occupancy_rows,
    parse_overlay,
    resolve_keys_path,
    textual_keymap,
)
from groket.paths import user_keys_path
from typer.testing import CliRunner

runner = CliRunner()


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _follow_chord(keymap: Keymap) -> str:
    return keymap.binding("session.follow").chord


def test_missing_file_keeps_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(KEYS_ENV, raising=False)
    missing = tmp_path / "no-such-keys.toml"
    monkeypatch.setenv(KEYS_ENV, str(missing))
    keymap = load_keymap()
    assert keymap.ok
    assert keymap.loaded_overlay is False
    assert _follow_chord(keymap) == action_by_id("session.follow").default
    assert keymap.binding("home.host_show").chord == "H"
    assert keymap.binding("home.host_hide").chord == "H"
    assert keymap.binding("edit.save").chord == "ctrl+s"
    assert keymap.binding("modal.done").chord == "ctrl+s"


def test_missing_app_home_file_is_defaults() -> None:
    keymap = load_keymap()
    assert keymap.ok
    assert keymap.path == user_keys_path()
    assert keymap.loaded_overlay is False
    assert resolve_keys_path() == user_keys_path()


def test_empty_and_unset_groket_keys_use_app_home(monkeypatch) -> None:
    monkeypatch.delenv(KEYS_ENV, raising=False)
    assert resolve_keys_path() == user_keys_path()
    monkeypatch.setenv(KEYS_ENV, "")
    assert resolve_keys_path() == user_keys_path()
    monkeypatch.setenv(KEYS_ENV, "   ")
    assert resolve_keys_path() == user_keys_path()


def test_overlay_remaps_one_id(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"session.follow" = "z"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert keymap.ok
    assert keymap.loaded_overlay is True
    assert _follow_chord(keymap) == "z"
    assert keymap.binding("session.done").chord == "e"


def test_unknown_id_refuses_overlay(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"session.follow" = "z"
"not.an.action" = "q"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert keymap.loaded_overlay is False
    assert _follow_chord(keymap) == "n"
    assert any(err.kind is OverlayErrorKind.UNKNOWN_ID for err in keymap.errors)


def test_unknown_scope_refuses_overlay(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[not_a_scope]
"session.follow" = "z"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert _follow_chord(keymap) == "n"
    assert any(err.kind is OverlayErrorKind.UNKNOWN_SCOPE for err in keymap.errors)


def test_wrong_scope_table_is_unknown_id(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[browser]
"session.follow" = "z"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert any(err.kind is OverlayErrorKind.UNKNOWN_ID for err in keymap.errors)
    assert _follow_chord(keymap) == "n"


def test_reserved_steal_refuses_overlay(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"session.follow" = "?"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert keymap.loaded_overlay is False
    assert _follow_chord(keymap) == "n"
    assert any(err.kind is OverlayErrorKind.RESERVED_STEAL for err in keymap.errors)


def test_cannot_remap_reserved_action(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[global]
"help.toggle" = "f1"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert keymap.binding("help.toggle").chord == "?"
    assert any(err.kind is OverlayErrorKind.RESERVED_STEAL for err in keymap.errors)


def test_overlay_clash_refuses_and_keeps_defaults(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"list.down" = "n"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert keymap.loaded_overlay is False
    assert keymap.binding("list.down").chord == "j"
    assert _follow_chord(keymap) == "n"
    assert any(err.kind is OverlayErrorKind.CLASH for err in keymap.errors)


def test_third_occupant_on_dual_home_h_clashes(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"session.follow" = "H"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert keymap.loaded_overlay is False
    assert any(err.kind is OverlayErrorKind.CLASH for err in keymap.errors)
    assert keymap.binding("home.host_show").chord == "H"
    assert keymap.binding("home.host_hide").chord == "H"
    assert _follow_chord(keymap) == "n"


def test_slash_and_slash_name_are_the_same_key(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"session.follow" = "/"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert any(err.kind is OverlayErrorKind.CLASH for err in keymap.errors)
    assert any(err.chord == "slash" for err in keymap.errors)


def test_default_dual_bindings_are_not_clashes() -> None:
    keymap = load_keymap()
    assert keymap.ok
    assert keymap.binding("home.host_show").chord == keymap.binding("home.host_hide").chord
    assert keymap.binding("edit.save").chord == keymap.binding("modal.done").chord


def test_leader_sequence_parses_but_resolve_fails(tmp_path: Path, monkeypatch) -> None:
    text = """
leader = ";"
leader_timeout_ms = 800

[home]
"list.down" = "n"
"list.up" = "e"
"session.follow" = "leader+n"
"session.done" = "leader+e"
"""
    doc = parse_overlay(text)
    assert doc.ok
    assert doc.leader == ";"
    assert doc.leader_timeout_ms == 800
    assert any(r.action_id == "session.follow" and r.chord == "leader+n" for r in doc.remaps)
    assert chord_has_sequence("leader+n")
    path = _write(tmp_path / "keys.toml", text)
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert keymap.loaded_overlay is False
    assert _follow_chord(keymap) == "n"
    assert keymap.binding("list.down").chord == "j"
    assert any(err.kind is OverlayErrorKind.SEQUENCE_NOT_WIRED for err in keymap.errors)


def test_invalid_toml_refuses_overlay(tmp_path: Path, monkeypatch) -> None:
    path = _write(tmp_path / "keys.toml", "[[[")
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert any(err.kind is OverlayErrorKind.INVALID_TOML for err in keymap.errors)
    assert _follow_chord(keymap) == "n"


def test_non_utf8_file_refuses_overlay(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "keys.toml"
    path.write_bytes(b"\xff\xfe[home]\n")
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    assert keymap.loaded_overlay is False
    assert any(err.kind is OverlayErrorKind.INVALID_TOML for err in keymap.errors)
    assert _follow_chord(keymap) == "n"


def test_occupancy_lists_remapped_keys(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"session.follow" = "z"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert keymap.ok
    rows = occupancy_rows(keymap)
    home_z = [r for r in rows if r[0] == "home" and r[1] == "z"]
    assert home_z == [("home", "z", "session.follow")]
    home_n = [r for r in rows if r[0] == "home" and r[1] == "n"]
    assert home_n == []
    text = format_occupancy(keymap)
    assert "z" in text
    assert "session.follow" in text


def test_default_check_is_clean() -> None:
    keymap = load_keymap()
    assert keymap.ok
    from groket.cli import app

    result = runner.invoke(app, ["keys", "--check"])
    assert result.exit_code == 0
    assert "OK" in (result.stdout or result.output or "")
    assert "defaults" in (result.stdout or result.output or "")


def test_cli_prints_resolved_table() -> None:
    from groket.cli import app

    result = runner.invoke(app, ["keys"])
    assert result.exit_code == 0
    out = result.stdout or result.output or ""
    assert "session.follow" in out
    assert "scope" in out
    assert "chord" in out
    assert "surface" in out
    table = format_keymap_table(load_keymap())
    assert "session.follow" in table


def test_cli_occupancy_and_check_conflict(tmp_path: Path, monkeypatch) -> None:
    from groket.cli import app

    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"list.down" = "n"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    check = runner.invoke(app, ["keys", "--check"])
    assert check.exit_code == 1
    check_out = check.stdout or ""
    assert "OK" not in check_out
    assert "session.follow" not in check_out
    assert "error:" in (check.stderr or check.output or "")
    occ = runner.invoke(app, ["keys", "--occupancy"])
    assert occ.exit_code == 1
    refused = load_keymap()
    assert not refused.ok
    rows = occupancy_rows(refused)
    assert ("home", "n", "session.follow") in rows
    assert ("home", "j", "list.down") in rows
    occ_out = occ.stdout or occ.output or ""
    assert "session.follow" in occ_out
    assert "list.down" in occ_out


def test_cli_check_ok_after_remap(tmp_path: Path, monkeypatch) -> None:
    from groket.cli import app

    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"session.follow" = "z"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    result = runner.invoke(app, ["keys", "--check"])
    assert result.exit_code == 0
    assert str(path) in (result.stdout or result.output or "")
    listed = runner.invoke(app, ["keys"])
    assert listed.exit_code == 0
    assert "session.follow" in (listed.stdout or listed.output or "")
    assert "z" in (listed.stdout or listed.output or "")


def test_normalize_chord_used_for_overlay_slash() -> None:
    assert normalize_chord("/") == normalize_chord("slash")


def test_textual_keymap_is_remappable_resolved_chords(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"session.follow" = "z"
"list.down" = "h"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert keymap.ok
    mapped = textual_keymap(keymap)
    assert mapped["session.follow"] == "z"
    assert mapped["list.down"] == "h"
    assert mapped["session.done"] == "e"
    assert mapped["home.runner"] == "r"
    assert "help.toggle" not in mapped
    assert "overlay.hide" not in mapped
    assert "session.open" not in mapped
    assert set(mapped) == {row.id for row in keymap.bindings if action_by_id(row.id).remappable}


def test_textual_keymap_defaults_when_overlay_refused(tmp_path: Path, monkeypatch) -> None:
    path = _write(
        tmp_path / "keys.toml",
        """
[home]
"list.down" = "n"
""",
    )
    monkeypatch.setenv(KEYS_ENV, str(path))
    keymap = load_keymap()
    assert not keymap.ok
    mapped = textual_keymap(keymap)
    assert mapped["list.down"] == "j"
    assert mapped["session.follow"] == "n"
    assert mapped["home.runner"] == "r"


def test_textual_keymap_defaults_without_overlay() -> None:
    mapped = textual_keymap(load_keymap())
    assert mapped["list.down"] == action_by_id("list.down").default
    assert mapped["session.follow"] == action_by_id("session.follow").default
    assert "help.toggle" not in mapped


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_PARSER_PARITY: tuple[tuple[str, bool], ...] = (
    ("overlay_integer_remap.toml", False),
    ("overlay_single_quote.toml", True),
    ("overlay_reserved_leader.toml", False),
    ("overlay_timeout_zero.toml", False),
    ("overlay_valid_timeout.toml", True),
)


def test_parser_parity_fixtures_match_load_keymap(tmp_path: Path, monkeypatch) -> None:
    """Same file body: load_keymap().ok matches HUD KeyOverlay::parse is Some."""
    for name, expect_ok in _PARSER_PARITY:
        text = (_FIXTURES / name).read_text(encoding="utf-8")
        path = _write(tmp_path / name, text)
        monkeypatch.setenv(KEYS_ENV, str(path))
        keymap = load_keymap()
        assert keymap.ok is expect_ok, name
        if name == "overlay_single_quote.toml":
            assert _follow_chord(keymap) == "z"
        if not expect_ok:
            assert keymap.loaded_overlay is False
            assert _follow_chord(keymap) == "n"
