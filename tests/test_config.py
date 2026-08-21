"""Canonical ``~/.groket/config.toml`` load, save, and comment keep."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit
from groket.config import (
    SCHEMA_ID,
    config_dump,
    emit_config_schema,
    invalidate_config_cache,
    load_app_config,
    parse_app_config,
    save_app_config,
    update_app_config,
    validate_config_file,
)


@pytest.fixture(autouse=True)
def _iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("groket.paths.app_config_path", lambda: tmp_path / "config.toml")
    invalidate_config_cache()
    yield
    invalidate_config_cache()


def test_defaults_when_missing() -> None:
    cfg = load_app_config()
    assert cfg.theme == "groket"
    assert cfg.follow_os is False
    assert cfg.show_host_sessions is False
    assert cfg.auto_serve is True
    assert cfg.analysis.live_refresh_workers == 1
    assert cfg.hud.window_mode is False
    assert cfg.hud.global_shortcut == ""
    assert cfg.hud.desktop_notifications is True
    assert cfg.export.default_profile == ""


def test_save_writes_toml_tables(tmp_path: Path) -> None:
    save_app_config(parse_app_config({"theme": "nord", "hud": {"global_shortcut": "Ctrl+K"}}))
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert SCHEMA_ID in text
    data = tomlkit.parse(text)
    assert data["theme"] == "nord"
    assert data["hud"]["global_shortcut"] == "Ctrl+K"
    assert "analysis" in data
    assert "export" in data


def test_update_keeps_comment(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '# keep me\ntheme = "nord"\n[analysis]\nnote = "keep"\n',
        encoding="utf-8",
    )
    invalidate_config_cache()
    update_app_config(theme="gruvbox")
    text = path.read_text(encoding="utf-8")
    assert "keep me" in text
    data = tomlkit.parse(text)
    assert data["theme"] == "gruvbox"
    assert data["analysis"]["note"] == "keep"


def test_invalid_toml_returns_defaults(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("not = [toml", encoding="utf-8")
    invalidate_config_cache()
    cfg = load_app_config()
    assert cfg.theme == "groket"


def test_dump_roundtrip() -> None:
    cfg = parse_app_config({"show_host_sessions": True, "hud": {"window_mode": True}})
    restored = parse_app_config(config_dump(cfg))
    assert restored.show_host_sessions is True
    assert restored.hud.window_mode is True


def test_imports_json_when_toml_missing(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        '{"theme": "nord", "show_host_sessions": true, '
        '"hud_global_shortcut": "Ctrl+K", '
        '"analysis": {"live_refresh_workers": 1}}\n',
        encoding="utf-8",
    )
    invalidate_config_cache()
    cfg = load_app_config()
    assert cfg.theme == "nord"
    assert cfg.show_host_sessions is True
    assert cfg.hud.global_shortcut == "Ctrl+K"
    assert cfg.analysis.live_refresh_workers == 1
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "nord" in text
    assert not (tmp_path / "config.json").exists()


def test_save_keeps_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('theme = "nord"\nextra_pref = true\n', encoding="utf-8")
    invalidate_config_cache()
    save_app_config(load_app_config())
    text = path.read_text(encoding="utf-8")
    assert "extra_pref" in text
    assert load_app_config().theme == "nord"


def test_toml_wins_over_sibling_json(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text('theme = "gruvbox"\n', encoding="utf-8")
    (tmp_path / "config.json").write_text('{"theme": "nord"}\n', encoding="utf-8")
    invalidate_config_cache()
    assert load_app_config().theme == "gruvbox"


def test_schema_has_published_id() -> None:
    text = emit_config_schema()
    assert SCHEMA_ID in text
    for key in (
        "theme",
        "follow_os",
        "show_host_sessions",
        "auto_serve",
        "analysis",
        "hud",
        "export",
        "global_shortcut",
        "default_profile",
    ):
        assert key in text


def test_validate_example_file() -> None:
    path = Path("examples/config/config.toml")
    cfg = validate_config_file(path)
    assert cfg.theme == "groket"
    assert cfg.follow_os is False
    assert cfg.show_host_sessions is False
    assert cfg.auto_serve is True
    assert cfg.analysis.live_refresh_workers == 1
    assert cfg.hud.desktop_notifications is True
    assert cfg.export.default_profile == ""


def test_validate_rejects_invalid_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("not = [toml", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid TOML"):
        validate_config_file(path)


def test_validate_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="is not a file"):
        validate_config_file(tmp_path / "missing.toml")
