"""Export profiles and ExportSpec resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from groket.session.export_spec import (
    DEFAULT_PROFILE_ID,
    ExportSpec,
    IncludeUnit,
    Packaging,
    get_export_profile,
    list_export_profiles,
    save_export_profile,
    set_default_export_profile_id,
)


def test_builtin_archive_full_and_trace_only() -> None:
    profiles = list_export_profiles(profiles_dir=Path("/nonexistent-export-profiles"))
    assert DEFAULT_PROFILE_ID in profiles
    assert "trace-only" in profiles
    assert "archive-org" in profiles
    full = profiles[DEFAULT_PROFILE_ID]
    assert full.packaging is Packaging.TAR_GZ
    assert full.renderer == "markdown"
    assert IncludeUnit.GROK_TRACE in full.include
    assert IncludeUnit.SUMMARY in full.include
    org = profiles["archive-org"]
    assert org.renderer == "org"
    assert IncludeUnit.SUMMARY in org.include
    trace = profiles["trace-only"]
    assert IncludeUnit.GROK_TRACE in trace.include


def test_user_profile_overrides_builtin(tmp_path: Path) -> None:
    path = tmp_path / "archive-full.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "id": "archive-full",
                "name": "Custom full",
                "packaging": "dir",
                "include": ["grok_trace", "manifest"],
                "renderer": "markdown",
            }
        ),
        encoding="utf-8",
    )
    profiles = list_export_profiles(profiles_dir=tmp_path)
    spec = profiles["archive-full"]
    assert spec.name == "Custom full"
    assert spec.packaging is Packaging.DIR
    assert spec.include == frozenset({IncludeUnit.GROK_TRACE, IncludeUnit.MANIFEST})


def test_get_export_profile_unknown() -> None:
    with pytest.raises(KeyError, match="unknown export profile"):
        get_export_profile("no-such-profile", profiles_dir=Path("/nope"))


def test_save_and_reload_profile(tmp_path: Path) -> None:
    spec = ExportSpec(
        profile_id="my-review",
        name="Review",
        packaging=Packaging.DIR,
        include=frozenset({IncludeUnit.NOTES, IncludeUnit.MANIFEST, IncludeUnit.README}),
        renderer="markdown",
    )
    written = save_export_profile(spec, profiles_dir=tmp_path)
    assert written.is_file()
    loaded = get_export_profile("my-review", profiles_dir=tmp_path)
    assert loaded.packaging is Packaging.DIR
    assert IncludeUnit.NOTES in loaded.include
    assert IncludeUnit.GROK_TRACE not in loaded.include


def test_default_profile_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from groket.session.export_spec import configured_export_profile_id

    cfg = tmp_path / "config.toml"
    cfg.write_text('[export]\ndefault_profile = "trace-only"\n', encoding="utf-8")
    monkeypatch.setattr("groket.paths.app_config_path", lambda: cfg)
    from groket.config import invalidate_config_cache

    invalidate_config_cache()
    assert configured_export_profile_id() == "trace-only"
    assert get_export_profile(profiles_dir=tmp_path / "empty").profile_id == "trace-only"


def test_configured_profile_none_without_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from groket.session.export_spec import configured_export_profile_id

    cfg = tmp_path / "config.toml"
    cfg.write_text("", encoding="utf-8")
    monkeypatch.setattr("groket.paths.app_config_path", lambda: cfg)
    from groket.config import invalidate_config_cache

    invalidate_config_cache()
    assert configured_export_profile_id() is None


def test_set_default_export_profile_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr("groket.paths.app_config_path", lambda: cfg)
    from groket.config import invalidate_config_cache

    invalidate_config_cache()
    set_default_export_profile_id("trace-only")
    data = cfg.read_text(encoding="utf-8")
    assert "trace-only" in data
    assert "[export]" in data
