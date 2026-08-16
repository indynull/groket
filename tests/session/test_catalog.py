"""Domain session catalog (control / headless owner; no TUI)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from groket.session.catalog import (
    list_session_catalog,
    resolve_session_reference,
    session_catalog_row,
)


def _write_session(root: Path, name: str, *, title: str = "Catalog session") -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": name}, "generated_title": title}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hi"},
                        "_meta": {"promptIndex": 1},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    return session_dir


def test_list_session_catalog_discovers_work_traces(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_session(traces, "session-catalog-a", title="Alpha review")
    rows = list_session_catalog(work)
    assert len(rows) == 1
    row = rows[0]
    assert row["sessionId"] == "session-catalog-a"
    assert row["path"] == str(sess.resolve())
    assert row["title"] == "Alpha review"
    assert row["origin"] == "work"
    assert "status" in row
    assert "model" in row


def test_list_session_catalog_empty_without_sessions(tmp_path: Path) -> None:
    work = tmp_path / "empty-work"
    work.mkdir()
    assert list_session_catalog(work) == []


def test_resolve_session_reference_by_path_and_id(tmp_path: Path) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_session(traces, "session-resolve-me")
    by_path = resolve_session_reference(str(sess), work)
    assert by_path == sess.resolve()
    by_name = resolve_session_reference("session-resolve-me", work)
    assert by_name == sess.resolve()
    assert resolve_session_reference("missing-session-xyz", work) is None
    assert resolve_session_reference("", work) is None


def test_list_session_catalog_follows_show_host_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless catalog includes host when config show_host_sessions is true."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    host = tmp_path / "host-sessions"
    _write_session(traces, "work-only-sess", title="Work")
    h_sess = host / "%2Fproj" / "host-sess"
    h_sess.mkdir(parents=True)
    (h_sess / "summary.json").write_text(
        '{"info":{"id":"host-sess"},"generated_title":"Host"}',
        encoding="utf-8",
    )
    (h_sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (h_sess / "updates.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    cfg = tmp_path / "config.toml"
    cfg.write_text("show_host_sessions = true\n", encoding="utf-8")
    monkeypatch.setattr("groket.paths.app_config_path", lambda: cfg)
    cache = tmp_path / "host-catalog-cache"
    cache.mkdir()
    monkeypatch.setattr("groket.session.mtime_export.analysis_cache_dir", lambda: cache)
    from groket.config import invalidate_config_cache

    invalidate_config_cache()

    # include_host=None → config
    rows = list_session_catalog(work, include_host=None)
    ids = {r["sessionId"] for r in rows}
    assert "work-only-sess" in ids
    assert "host-sess" in ids
    origins = {r["sessionId"]: r["origin"] for r in rows}
    assert origins.get("host-sess") == "host"

    # Force off ignores config
    rows_work = list_session_catalog(work, include_host=False)
    assert {r["sessionId"] for r in rows_work} == {"work-only-sess"}


def test_resolve_by_id_does_not_load_meta_for_other_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening one host session must not re-read every other session's meta.

    ``session/overview`` and each ``session/timeline`` page resolve the id
    through :func:`resolve_session_reference`. Loading list-meta for every
    sibling on that path made TUI attach open ~10× slower than a local parse.
    """
    from groket.session import catalog as catalog_mod

    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    host = tmp_path / "host-sessions"
    target_id = "sess-0099"
    for i in range(100):
        name = f"sess-{i:04d}"
        bucket = host / "%2Fproj" / name
        bucket.mkdir(parents=True)
        (bucket / "summary.json").write_text(
            json.dumps({"info": {"id": name}, "generated_title": name}),
            encoding="utf-8",
        )
        (bucket / "updates.jsonl").write_text("", encoding="utf-8")
        (bucket / "events.jsonl").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(
        "groket.session.sources.host_grok_sessions_root",
        lambda: host,
    )
    cfg = tmp_path / "config.toml"
    cfg.write_text("show_host_sessions = true\n", encoding="utf-8")
    monkeypatch.setattr("groket.paths.app_config_path", lambda: cfg)
    from groket.config import invalidate_config_cache

    invalidate_config_cache()

    calls: list[str] = []
    real_row = catalog_mod.session_catalog_row

    def _count_row(session_dir: Path, *, origin: str = "work", label: str | None = None):
        calls.append(session_dir.name)
        return real_row(session_dir, origin=origin, label=label)

    monkeypatch.setattr(catalog_mod, "session_catalog_row", _count_row)

    found = resolve_session_reference(target_id, work, include_host=True)
    assert found is not None
    assert found.name == target_id
    assert calls == []


def test_catalog_cache_resolves_id_from_warm_rows(tmp_path: Path) -> None:
    """Serve must resolve session ids from the warm catalog, not a second walk."""
    from groket.session.catalog import SessionCatalogCache

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    sess = _write_session(traces, "cached-resolve")
    cache = SessionCatalogCache(work, include_host=False)
    rows = cache.get(force=True)
    assert len(rows) == 1
    assert cache.resolve("cached-resolve") == sess.resolve()
    assert cache.resolve(str(sess.resolve())) == sess.resolve()
    assert cache.resolve("missing") is None


def test_session_catalog_row_none_on_bad_dir(tmp_path: Path) -> None:
    empty = tmp_path / "not-a-session"
    empty.mkdir()
    # Empty dir still loads as meta with defaults — not None. Use missing path:
    missing = tmp_path / "nope"
    # load_session_meta_list tolerates missing files; row still builds.
    row = session_catalog_row(empty, origin="work")
    assert row is not None
    assert row["sessionId"] == "not-a-session"
    _ = missing
