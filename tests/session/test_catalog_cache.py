"""SessionCatalogCache: single-flight, force refresh, fingerprint."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from groket.session.catalog import (
    SessionCatalogCache,
    session_meta_from_catalog_row,
)


def _write_sess(root: Path, name: str, title: str, *, kind: str = "") -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    body: dict[str, object] = {"info": {"id": name}, "generated_title": title}
    if kind:
        body["session_kind"] = kind
    (sd / "summary.json").write_text(json.dumps(body), encoding="utf-8")
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    return sd


def test_list_for_rpc_cold_returns_without_joining_scan(tmp_path: Path, monkeypatch) -> None:
    """First session/list must not wait for a cold full tree scan."""
    import groket.session.catalog as catalog_mod

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    for i in range(5):
        _write_sess(traces, f"s{i}", f"S{i}")
    release = threading.Event()
    started = threading.Event()
    real = catalog_mod.list_session_catalog

    def blocked(*args: object, **kwargs: object) -> object:
        started.set()
        if not release.wait(timeout=8):
            raise AssertionError("scan still blocked")
        return real(*args, **kwargs)

    monkeypatch.setattr(catalog_mod, "list_session_catalog", blocked)
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    done: dict[str, object] = {}

    def call() -> None:
        done["out"] = cache.list_for_rpc(limit=50)

    th = threading.Thread(target=call)
    th.start()
    assert started.wait(timeout=2)
    th.join(0.4)
    assert not th.is_alive(), "list_for_rpc joined the in-flight catalog scan"
    out = done["out"]
    assert isinstance(out, dict)
    assert out.get("incomplete") is True or out.get("building") is True
    assert out.get("sessions") == []
    release.set()
    th.join(timeout=5)
    finished = cache.get()
    assert len(finished) == 5
    later = cache.list_for_rpc(limit=50)
    assert later["matched"] == 5
    assert later.get("incomplete") is not True
    assert {str(r["sessionId"]) for r in later["sessions"]} == {f"s{i}" for i in range(5)}


def test_catalog_rebuild_invokes_on_rebuilt(tmp_path: Path) -> None:
    """Owner can notify attach clients when a cold scan finishes."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "one", "One")
    hits: list[int] = []
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache._on_rebuilt = lambda: hits.append(1)
    rows = cache.get(force=True)
    assert len(rows) == 1
    assert hits == [1]


def test_catalog_rebuild_skips_on_rebuilt_when_ids_unchanged(tmp_path: Path) -> None:
    """TTL/force rescan of the same sessions must not wake every client."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "one", "One")
    hits: list[int] = []
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache._on_rebuilt = lambda: hits.append(1)
    cache.get(force=True)
    cache.get(force=True)
    assert hits == [1]


def test_catalog_cache_second_get_is_cached(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    for i in range(12):
        _write_sess(traces, f"s{i:03d}", f"Title {i}")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=60.0)
    t0 = time.perf_counter()
    a = cache.get(force=True)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    b = cache.get()
    warm = time.perf_counter() - t0
    assert len(a) == 12
    assert len(b) == 12
    assert warm < cold
    assert warm < 0.05


def test_catalog_cache_force_rebuilds(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    assert len(cache.get(force=True)) == 1
    _write_sess(traces, "two", "Two")
    # Within TTL without force may still see fingerprint change (entry count).
    rows = cache.get(force=True)
    assert len(rows) == 2


def test_catalog_cache_single_flight(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    for i in range(8):
        _write_sess(traces, f"x{i}", f"X{i}")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=60.0)
    results: list[int] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(len(cache.get(force=True)))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)
    assert results == [8, 8, 8, 8]


def test_apply_fs_catalog_events_patches_dirty_row(tmp_path: Path) -> None:
    """Watch callback patches the dirty session instead of a full catalog scan."""
    from groket.integrations.daemon import apply_fs_catalog_events

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    _write_sess(traces, "two", "Two")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    (one / "summary.json").write_text(
        json.dumps({"info": {"id": "one"}, "generated_title": "One live"}),
        encoding="utf-8",
    )
    sessions, notes, changed = apply_fs_catalog_events(cache, [str(one / "summary.json")], [traces])
    assert one.resolve() in [p.resolve() for p in sessions]
    assert notes == []
    assert changed.get("one") is True
    by_id = {str(r["sessionId"]): r for r in cache.get()}
    assert by_id["one"]["title"] == "One live"


def test_open_journal_second_apply_reads_from_offset(tmp_path: Path) -> None:
    """An open session's second updates.jsonl apply tails from the first offset."""
    import asyncio

    from groket.integrations.daemon import CatalogWatchApply

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    loop = asyncio.new_event_loop()

    class _Server:
        async def publish_session_changed(self, *_a: object, **_k: object) -> None:
            return None

        async def publish_notes_changed(self, *_a: object) -> None:
            return None

    apply = CatalogWatchApply(server=_Server(), cache=cache, roots=[traces], loop=loop)
    apply.mark_open(one)
    tail = apply.tail_for(one)
    assert tail is not None
    first_offset = tail.offset
    assert first_offset > 0
    with (one / "updates.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{}\n")
    apply._apply_disk([str(one / "updates.jsonl")])
    assert tail.offset > first_offset
    loop.close()


def test_apply_fs_catalog_events_append_marks_list_unchanged(tmp_path: Path) -> None:
    """An updates.jsonl append that leaves painted fields sets listChanged false."""
    from groket.integrations.daemon import apply_fs_catalog_events

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    (one / "updates.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    _sessions, _notes, changed = apply_fs_catalog_events(
        cache, [str(one / "updates.jsonl")], [traces]
    )
    assert changed.get("one") is False


def test_apply_fs_catalog_events_refresh_error_marks_list_changed(
    tmp_path: Path,
) -> None:
    """A failed incremental refresh reports list change without a second scan."""
    from unittest.mock import patch

    from groket.integrations.daemon import apply_fs_catalog_events

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    (one / "summary.json").write_text(
        json.dumps({"info": {"id": "one"}, "generated_title": "One live"}),
        encoding="utf-8",
    )
    with patch.object(cache, "refresh_rows", side_effect=OSError("disk")):
        _sessions, _notes, changed = apply_fs_catalog_events(
            cache, [str(one / "summary.json")], [traces]
        )
    assert changed == {"one": True}
    by_id = {str(r["sessionId"]): r for r in cache.get()}
    assert by_id["one"]["title"] == "One"


def test_event_paths_skip_encoded_cwd_bucket(tmp_path: Path) -> None:
    from groket.integrations.daemon import CatalogWatchApply

    traces = tmp_path / "sessions"
    bucket = traces / "%2FUsers%2Fali%2F_dev%2F_git%2Fgroket"
    sess = bucket / "019abc"
    sess.mkdir(parents=True)
    (sess / "summary.json").write_text("{}", encoding="utf-8")
    (sess / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    found = CatalogWatchApply.session_dirs([str(sess / "updates.jsonl")], roots=[traces])
    assert [p.resolve() for p in found] == [sess.resolve()]
    assert CatalogWatchApply.session_dirs([str(bucket)], roots=[traces]) == []


def test_catalog_cache_refresh_rows_updates_one_status(tmp_path: Path) -> None:
    """FS watch must patch the dirty session instead of rescanning the tree."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    _write_sess(traces, "two", "Two")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    first = cache.get(force=True)
    by_id = {str(r["sessionId"]): r for r in first}
    assert "one" in by_id
    (one / "events.jsonl").write_text(
        json.dumps({"ts": 1, "type": "turn_started", "turn_number": 0})
        + "\n"
        + json.dumps({"ts": 2, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    updated, _changed = cache.refresh_rows([one])
    by_id = {str(r["sessionId"]): r for r in updated}
    assert by_id["one"]["status"] == "complete"
    assert by_id["two"]["sessionId"] == "two"
    assert len(updated) == 2
    cached = cache.get()
    assert {str(r["sessionId"]): r["status"] for r in cached}["one"] == "complete"


def test_refresh_rows_does_not_list_subagent_sibling(tmp_path: Path) -> None:
    """A watch on a new subagent mirror must not append it to session/list."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    parent = _write_sess(traces, "parent", "Parent")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    assert {str(r["sessionId"]) for r in cache.get(force=True)} == {"parent"}
    child = _write_sess(traces, "child", "Adversarial verifier Grok Build harness", kind="subagent")
    (parent / "subagents" / "child").mkdir(parents=True)
    updated, _changed = cache.refresh_rows([child])
    assert {str(r["sessionId"]) for r in updated} == {"parent"}


def test_refresh_rows_does_not_list_child_id_mirror(tmp_path: Path) -> None:
    """Basename listed under a catalog parent's subagents/ is not operator-facing."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    parent = _write_sess(traces, "parent", "Parent")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    (parent / "subagents" / "child").mkdir(parents=True)
    child = _write_sess(traces, "child", "Adversarial verifier")
    updated, _changed = cache.refresh_rows([child])
    assert {str(r["sessionId"]) for r in updated} == {"parent"}


def test_refresh_rows_drops_cached_row_after_subagent_kind(tmp_path: Path) -> None:
    """A sibling listed before summary.json has a kind is removed on the next watch."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "parent", "Parent")
    child = _write_sess(traces, "child", "Adversarial verifier")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    assert {str(r["sessionId"]) for r in cache.get(force=True)} == {"parent", "child"}
    (child / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "child"},
                "generated_title": "Adversarial verifier",
                "session_kind": "subagent",
            }
        ),
        encoding="utf-8",
    )
    updated, _changed = cache.refresh_rows([child])
    assert {str(r["sessionId"]) for r in updated} == {"parent"}


def test_drop_subagent_rows_clears_cached_children(tmp_path: Path) -> None:
    """Periodic owner sweep removes kinded children without a full tree walk."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    _write_sess(traces, "parent", "Parent")
    child = _write_sess(traces, "child", "Adversarial verifier")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    assert {str(r["sessionId"]) for r in cache.get(force=True)} == {"parent", "child"}
    (child / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": "child"},
                "generated_title": "Adversarial verifier",
                "session_kind": "subagent",
            }
        ),
        encoding="utf-8",
    )
    updated = cache.drop_subagent_rows()
    assert {str(r["sessionId"]) for r in updated} == {"parent"}


def test_refresh_rows_host_does_not_read_events_jsonl(tmp_path: Path) -> None:
    """Host watch refresh must not open events.jsonl."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    host = tmp_path / "host"
    bucket = host / "%2Fproj" / "live-host"
    bucket.mkdir(parents=True)
    (bucket / "summary.json").write_text(
        json.dumps({"info": {"id": "live-host"}, "generated_title": "Live"}),
        encoding="utf-8",
    )
    (bucket / "signals.json").write_text("{}", encoding="utf-8")
    (bucket / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    events = bucket / "events.jsonl"
    events.write_text(
        json.dumps({"ts": 1, "type": "turn_started", "turn_number": 0}) + "\n",
        encoding="utf-8",
    )
    cache = SessionCatalogCache(
        work, traces_path=traces, include_host=True, host_root=host, ttl=3600.0
    )
    cache.get(force=True)
    events.write_text(
        json.dumps({"ts": 1, "type": "turn_started", "turn_number": 0})
        + "\n"
        + json.dumps({"ts": 2, "type": "turn_ended", "outcome": "completed"})
        + "\n",
        encoding="utf-8",
    )
    opened: list[str] = []
    real_open = Path.open

    def tracking_open(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    from unittest.mock import patch

    with patch.object(Path, "open", tracking_open):
        _rows, changed = cache.refresh_rows([bucket])
    assert not any(p.endswith("events.jsonl") for p in opened)
    assert changed.get("live-host") is False


def _age_host_traces(session_dir: Path, *, seconds: float = 9 * 60) -> None:
    """Push host stamp files behind ``HOST_INCOMPLETE_STALE_SECONDS``."""
    stamp = time.time() - seconds
    names = (
        "events.jsonl",
        "updates.jsonl",
        "summary.json",
        "signals.json",
        "chat_history.jsonl",
    )
    for name in names:
        path = session_dir / name
        if path.is_file():
            os.utime(path, (stamp, stamp))
    os.utime(session_dir, (stamp, stamp))


def test_refresh_rows_host_running_clears_when_stale(tmp_path: Path) -> None:
    """A finished host session must not stay ``running`` after traces go stale."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    host = tmp_path / "host"
    bucket = host / "%2Fproj" / "was-live"
    bucket.mkdir(parents=True)
    (bucket / "summary.json").write_text(
        json.dumps({"info": {"id": "was-live"}, "generated_title": "Live"}),
        encoding="utf-8",
    )
    (bucket / "signals.json").write_text("{}", encoding="utf-8")
    (bucket / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    cache = SessionCatalogCache(
        work, traces_path=traces, include_host=True, host_root=host, ttl=3600.0
    )
    first = cache.get(force=True)
    by_id = {str(r["sessionId"]): r for r in first}
    assert by_id["was-live"]["status"] == "running"
    _age_host_traces(bucket)
    rows, changed = cache.refresh_rows([bucket])
    by_id = {str(r["sessionId"]): r for r in rows}
    assert by_id["was-live"]["status"] == "—"
    assert changed.get("was-live") is True


def test_refresh_rows_host_keeps_complete_when_tail_loses_outcome(tmp_path: Path) -> None:
    """``complete`` survives a stale tail that is no longer ``turn_completed``."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    host = tmp_path / "host"
    bucket = host / "%2Fproj" / "done-host"
    bucket.mkdir(parents=True)
    (bucket / "summary.json").write_text(
        json.dumps({"info": {"id": "done-host"}, "generated_title": "Done"}),
        encoding="utf-8",
    )
    (bucket / "signals.json").write_text("{}", encoding="utf-8")
    (bucket / "updates.jsonl").write_text(
        json.dumps(
            {
                "params": {
                    "update": {"sessionUpdate": "turn_completed"},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cache = SessionCatalogCache(
        work, traces_path=traces, include_host=True, host_root=host, ttl=3600.0
    )
    first = cache.get(force=True)
    by_id = {str(r["sessionId"]): r for r in first}
    assert by_id["done-host"]["status"] == "complete"
    (bucket / "updates.jsonl").write_text(
        json.dumps(
            {
                "params": {
                    "update": {"sessionUpdate": "tool_call_update", "content": "x"},
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _age_host_traces(bucket)
    rows, changed = cache.refresh_rows([bucket])
    by_id = {str(r["sessionId"]): r for r in rows}
    assert by_id["done-host"]["status"] == "complete"
    assert changed.get("done-host") is False


def test_apply_fs_catalog_events_noise_does_not_open_events_or_bump(
    tmp_path: Path,
) -> None:
    """Growing events.jsonl or writing under workspace/ is not a list rebuild."""
    from unittest.mock import patch

    from groket.integrations.daemon import apply_fs_catalog_events

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    rev = cache.revision
    events = one / "events.jsonl"
    events.write_text(
        json.dumps({"ts": 1, "type": "turn_started", "turn_number": 0}) + "\n",
        encoding="utf-8",
    )
    ws = one / "workspace" / "src"
    ws.mkdir(parents=True)
    (ws / "main.py").write_text("print(1)\n", encoding="utf-8")
    opened: list[str] = []
    real_open = Path.open

    def tracking_open(self: Path, *args: object, **kwargs: object) -> object:
        opened.append(str(self))
        return real_open(self, *args, **kwargs)

    with patch.object(Path, "open", tracking_open):
        sessions, _notes, changed = apply_fs_catalog_events(
            cache,
            [str(events), str(ws / "main.py")],
            [traces],
        )
    assert [p.resolve() for p in sessions] == [one.resolve()]
    assert changed.get("one") is False
    assert cache.revision == rev
    assert not any(p.endswith("events.jsonl") for p in opened)
    poll = cache.list_for_rpc(since_revision=rev)
    assert poll["unchanged"] is True


def test_apply_fs_workflow_and_job_files_leave_list_still(tmp_path: Path) -> None:
    """Workflow state and job logs do not rebuild painted catalog fields."""
    from groket.integrations.daemon import apply_fs_catalog_events

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    rev = cache.revision
    state = one / "workflows" / "wf_1" / "state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    job_log = one / "tasks" / "bg_1" / "output.log"
    job_log.parent.mkdir(parents=True)
    job_log.write_text("ok\n", encoding="utf-8")
    sessions, notes, changed = apply_fs_catalog_events(
        cache,
        [str(state), str(job_log)],
        [traces],
    )
    assert [p.resolve() for p in sessions] == [one.resolve()]
    assert notes == []
    assert changed.get("one") is False
    assert cache.revision == rev


def test_refresh_rows_append_does_not_bump_revision(tmp_path: Path) -> None:
    """An updates.jsonl append that leaves list fields unchanged keeps sinceRevision."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    one = _write_sess(traces, "one", "One")
    cache = SessionCatalogCache(work, traces_path=traces, include_host=False, ttl=3600.0)
    cache.get(force=True)
    rev = cache.revision
    (one / "updates.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    _rows, changed = cache.refresh_rows([one])
    assert cache.revision == rev
    assert changed.get("one") is False
    poll = cache.list_for_rpc(since_revision=rev)
    assert poll["unchanged"] is True


def test_session_meta_from_catalog_row_status() -> None:
    meta = session_meta_from_catalog_row(
        {
            "sessionId": "abc",
            "path": "/tmp/abc",
            "title": "Hello",
            "model": "grok:high",
            "status": "awaiting",
            "origin": "host",
            "taskId": "task-9",
            "durationSeconds": 42.5,
            "numEvents": 17,
            "contextUsageCompact": "35% 1.2k/128k",
            "contextWindowUsagePct": 35,
            "contextTokensUsed": 1200,
            "contextWindowTokens": 128_000,
            "toolCallCount": 3,
            "errorCount": 1,
        }
    )
    assert meta is not None
    assert meta.session_id == "abc"
    assert meta.list_status_label() == "awaiting"
    assert meta.model_display == "grok:high"
    assert meta.task_id == "task-9"
    assert meta.duration_seconds == 42.5
    assert meta.num_events == 17
    assert meta.tool_call_count == 3
    assert meta.error_count == 1
    assert meta.context_window_usage_pct == 35
    assert meta.context_tokens_used == 1200
    assert meta.context_window_tokens == 128_000
    assert "35" in meta.context_usage_compact
    assert meta.origin == "host"


def test_session_meta_from_catalog_row_host_path_wins(tmp_path, monkeypatch) -> None:
    host = tmp_path / "sessions"
    sess = host / "%2Fproj" / "019fe503-d45c-7320-904e-cfa8836c361c"
    sess.mkdir(parents=True)
    monkeypatch.setattr("groket.session.sources.host_grok_sessions_root", lambda: host)
    meta = session_meta_from_catalog_row(
        {
            "sessionId": "019fe503-d45c-7320-904e-cfa8836c361c",
            "path": str(sess),
            "origin": "work",
        }
    )
    assert meta is not None
    assert meta.origin == "host"
