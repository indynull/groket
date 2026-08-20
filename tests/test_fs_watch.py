"""Plane watch subscription and journal tail (no recursive tree watch)."""

from __future__ import annotations

import time
from pathlib import Path

from async_wait import wait_until_sync
from groket.fs_watch import TraceTreeWatch
from groket.session.watch import (
    JournalTail,
    catalog_subscribe_paths,
    plane_file_paths,
    session_dirs_under,
)


def _write_session(root: Path, name: str) -> Path:
    session = root / name
    session.mkdir(parents=True)
    (session / "summary.json").write_text("{}", encoding="utf-8")
    (session / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    workspace = session / "workspace" / "src"
    workspace.mkdir(parents=True)
    (workspace / "a.py").write_text("print(1)\n", encoding="utf-8")
    return session


def test_subscribe_paths_are_membership_and_plane_files(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    session = _write_session(traces, "sess")
    paths = catalog_subscribe_paths([traces], [session])
    names = {p.name for p in paths}
    assert traces in paths
    assert session in paths
    assert {"summary.json", "signals.json", "updates.jsonl", "operator_notes.toml"} <= names
    assert not any("workspace" in p.parts for p in paths)


def test_path_relevant_ignores_workspace() -> None:
    sess = "/home/ali/.grok/sessions/%2Fproj/sid"
    assert not TraceTreeWatch.path_relevant(f"{sess}/workspace/src/a.py")
    assert not TraceTreeWatch.path_relevant(f"{sess}/workspace/updates.jsonl")
    assert TraceTreeWatch.path_relevant(f"{sess}/updates.jsonl")
    assert TraceTreeWatch.path_relevant(f"{sess}/summary.json")
    assert TraceTreeWatch.path_relevant(f"{sess}/signals.json")
    assert not TraceTreeWatch.path_relevant("/x/random.bin")


def test_watch_start_stop_fires_on_plane_write(tmp_path: Path) -> None:
    hits: list[int] = []
    session = _write_session(tmp_path, "sess")
    w = TraceTreeWatch(tmp_path, lambda: hits.append(1), session_dir=session)
    assert w.start() is True
    try:
        assert not any("workspace" in p.parts for p in w.subscribed_paths())
        (session / "summary.json").write_text('{"title": "x"}\n', encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="FS watch callback after write")
    finally:
        w.stop()
    assert hits


def test_watch_workspace_write_does_not_fire(tmp_path: Path) -> None:
    hits: list[list[str]] = []
    session = _write_session(tmp_path, "sess")
    w = TraceTreeWatch(
        tmp_path,
        lambda: None,
        session_dir=session,
        on_paths=lambda paths: hits.append(paths),
    )
    assert w.start() is True
    try:
        (session / "workspace" / "src" / "a.py").write_text("print(2)\n", encoding="utf-8")
        time.sleep(0.3)
        assert hits == []
        (session / "summary.json").write_text("{}\n", encoding="utf-8")
        wait_until_sync(lambda: bool(hits), description="summary write still fires")
    finally:
        w.stop()
    assert hits
    assert all("workspace" not in Path(p).parts for batch in hits for p in batch)


def test_journal_tail_second_append_does_not_reread(tmp_path: Path) -> None:
    path = tmp_path / "updates.jsonl"
    path.write_text("one\n", encoding="utf-8")
    tail = JournalTail(path)
    first = tail.consume()
    assert first == b"one\n"
    offset = tail.offset
    assert offset > 0
    with path.open("a", encoding="utf-8") as fh:
        fh.write("two\n")
    second = tail.consume()
    assert second == b"two\n"
    assert tail.offset > offset


def test_owner_serve_source_has_no_watchdog_or_warm_timer() -> None:
    from groket.integrations import daemon

    src = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "watchdog" not in src
    assert "inotify_c" not in src
    assert "CATALOG_WARM_INTERVAL" not in src
    assert "CONTROL_FS_DEBOUNCE_S" not in src


def test_session_dirs_under_skips_workspace(tmp_path: Path) -> None:
    traces = tmp_path / "traces"
    session = _write_session(traces, "sess")
    found = session_dirs_under([traces])
    assert [p.resolve() for p in found] == [session.resolve()]
    assert plane_file_paths(session)[-1].name == "operator_notes.toml"
