"""Asset path resolution (repo assets/ vs embedded)."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.assets_loader import asset_path, assets_root
from groket.docker.resources import entrypoint_sh, share_once_py


def test_assets_root_and_docker_entrypoint():
    root = assets_root()
    assert root.is_dir()
    assert (root / "docker" / "entrypoint.sh").is_file() or asset_path(
        "docker", "entrypoint.sh"
    ).is_file()
    text = entrypoint_sh()
    assert "GROKET" in text or "entrypoint" in text.lower() or len(text) > 100
    assert "RESUME_SESSION_ID" in text
    assert "--resume" in text
    assert "--fork-session" in text
    assert "FORK_SESSION_ID" in text
    # Fork is not soft-skipped: capability gate + always pass flags in fork mode.
    assert "lacks --fork-session" in text
    assert "_gte_is_resume_seed_path" in text
    # CLI effort mapping (product xhigh/max → high).
    assert "xhigh|max" in text
    assert "REPO_COMMIT" in text
    assert "--restore-code" in text
    assert "host-mounted /workspace" in text
    share = share_once_py()
    assert "grok" in share.lower()
    assert "share" in share.lower()


def test_assets_root_fallback_to_embedded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """When repo assets/ absent, falls back to _embedded_assets (lines 18-20)."""
    import groket.assets_loader as loader

    # Clear the LRU cache so we can test fresh
    loader.assets_root.cache_clear()
    try:
        # Patch __file__ to a location without assets/ or _embedded_assets
        pkg_dir = tmp_path / "groket"
        pkg_dir.mkdir()
        emb = pkg_dir / "_embedded_assets"
        emb.mkdir()
        (emb / "docker").mkdir()
        (emb / "docker" / "test.txt").write_text("x", encoding="utf-8")

        monkeypatch.setattr(loader, "__file__", str(pkg_dir / "assets_loader.py"))
        # Clear cache and re-call
        loader.assets_root.cache_clear()
        root = loader.assets_root()
        assert root == emb
    finally:
        loader.assets_root.cache_clear()


def test_assets_root_missing_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """FileNotFoundError when neither layout exists (line 20)."""
    import groket.assets_loader as loader

    loader.assets_root.cache_clear()
    try:
        pkg_dir = tmp_path / "groket"
        pkg_dir.mkdir()
        monkeypatch.setattr(loader, "__file__", str(pkg_dir / "assets_loader.py"))
        loader.assets_root.cache_clear()
        with pytest.raises(FileNotFoundError):
            loader.assets_root()
    finally:
        loader.assets_root.cache_clear()
