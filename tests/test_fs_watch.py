"""TraceTreeWatch relevance and debounce (no long-lived Observer in CI)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from async_wait import wait_until_sync
from groket.fs_watch import TraceTreeWatch, _path_looks_relevant


def _inotify_watch_paths(watch: TraceTreeWatch) -> list[str]:
    obs = watch._observer
    if obs is None:
        return []
    found: list[str] = []
    for emitter in getattr(obs, "_emitters", ()):
        buf = getattr(emitter, "_inotify", None)
        inner = getattr(buf, "_inotify", None)
        mapping = getattr(inner, "_wd_for_path", None) or {}
        for raw in mapping:
            found.append(os.fsdecode(raw) if isinstance(raw, (bytes, bytearray)) else str(raw))
    return found


def test_path_looks_relevant() -> None:
    assert _path_looks_relevant("/x/updates.jsonl")
    assert _path_looks_relevant("/x/events.jsonl")
    assert _path_looks_relevant("/runs/traces/groket-abc")
    assert not _path_looks_relevant("/x/random.bin")


def test_path_looks_relevant_ignores_workspace_images_compaction() -> None:
    """Workspace, images, and compaction trees are not watch hits."""
    sess = "/home/ali/.grok/sessions/%2Fproj/sid"
    assert not _path_looks_relevant(f"{sess}/workspace/src/a.py")
    assert not _path_looks_relevant(f"{sess}/images/x.png")
    assert not _path_looks_relevant(f"{sess}/compaction/y")
    assert not _path_looks_relevant(f"{sess}/workspace/updates.jsonl")
    assert _path_looks_relevant(f"{sess}/updates.jsonl")
    assert _path_looks_relevant(f"{sess}/summary.json")
    assert _path_looks_relevant(f"{sess}/signals.json")


def test_watch_start_stop(tmp_path: Path) -> None:
    hits: list[int] = []

    def on_change() -> None:
        hits.append(1)

    w = TraceTreeWatch(tmp_path, on_change, debounce_s=0.05)
    assert w.start() is True
    (tmp_path / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    try:
        wait_until_sync(lambda: bool(hits), description="FS watch callback after write")
    finally:
        w.stop()
    assert hits, "expected FS callback after writing updates.jsonl"


def test_watch_survives_runtime_workspace_create(tmp_path: Path) -> None:
    """Creating a workspace tree after start must not crash the observer."""
    session = tmp_path / "sess"
    session.mkdir()
    (session / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    hits: list[int] = []
    w = TraceTreeWatch(tmp_path, lambda: hits.append(1), debounce_s=0.05)
    assert w.start() is True
    try:
        workspace = session / "workspace" / "src"
        workspace.mkdir(parents=True)
        (workspace / "main.py").write_text("print(1)\n", encoding="utf-8")
        (session / "updates.jsonl").write_text("{}\n{}\n", encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="updates.jsonl still fires")
        paths = _inotify_watch_paths(w)
        assert not any("workspace" in Path(p).parts for p in paths)
    finally:
        w.stop()
    assert hits


def test_watch_read_does_not_fire_callback(tmp_path: Path) -> None:
    """Opening a watched file for read must not schedule a catalog apply."""
    hits: list[int] = []
    target = tmp_path / "updates.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    w = TraceTreeWatch(tmp_path, lambda: hits.append(1), debounce_s=0.05)
    assert w.start() is True
    try:
        target.read_text(encoding="utf-8")
        time.sleep(0.25)
        assert hits == []
        target.write_text("{}\n{}\n", encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="write still fires")
    finally:
        w.stop()
    assert hits


def test_watch_does_not_install_inotify_on_workspace(tmp_path: Path) -> None:
    """Noise trees must not receive inotify watches (large workspaces)."""
    session = tmp_path / "sess"
    workspace = session / "workspace" / "src"
    workspace.mkdir(parents=True)
    (workspace / "main.py").write_text("print(1)\n", encoding="utf-8")
    (session / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    w = TraceTreeWatch(tmp_path, lambda: None, debounce_s=0.05)
    assert w.start() is True
    try:
        paths = _inotify_watch_paths(w)
        assert paths, "expected at least the traces root watch"
        assert not any("workspace" in Path(p).parts for p in paths)
        assert any(Path(p) == tmp_path or Path(p) == session for p in paths)
    finally:
        w.stop()
