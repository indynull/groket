"""Host catalog snapshot: summary+signals rows, stamp-gated rebuild."""

from __future__ import annotations

import json
from pathlib import Path

import groket.parser as parser_mod
from groket.session.catalog import list_session_catalog, session_catalog_row
from groket.session.mtime_export import write_host_catalog_export


def _host_session(root: Path, name: str, *, title: str, messages: int = 3) -> Path:
    sd = root / name
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps(
            {
                "info": {"id": name},
                "generated_title": title,
                "num_messages": messages,
            }
        ),
        encoding="utf-8",
    )
    (sd / "signals.json").write_text(
        json.dumps({"toolCallCount": 2, "turnCount": 4, "sessionDurationSeconds": 12.0}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    (sd / "events.jsonl").write_text('{"type":"turn_started"}\n', encoding="utf-8")
    return sd


def test_host_catalog_row_skips_events_and_updates(tmp_path: Path, monkeypatch) -> None:
    sd = _host_session(tmp_path / "host", "019aaaa", title="Host title", messages=9)

    def _boom(*_a, **_k):
        raise AssertionError("host list must not read events.jsonl or updates.jsonl")

    monkeypatch.setattr(parser_mod, "_list_runtime_status", _boom)
    monkeypatch.setattr(parser_mod, "_last_session_update_type", _boom)
    monkeypatch.setattr(parser_mod, "_list_timeline_event_count", _boom)
    monkeypatch.setattr(parser_mod, "parse_timeline", _boom)
    row = session_catalog_row(sd, origin="host")
    assert row is not None
    assert row["title"] == "Host title"
    assert row["numEvents"] == 9
    assert row["origin"] == "host"
    assert row["toolCallCount"] == 2
    assert row["turnCount"] == 4


def test_host_export_is_stamp_gated(tmp_path: Path) -> None:
    host = tmp_path / "host"
    _host_session(host, "019cccc-1111-2222-3333-444444444444", title="Host title")
    dest = tmp_path / "out" / "host.json"
    first = write_host_catalog_export(dest, host_root=host)
    assert first == dest
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["sessionId"] == "019cccc-1111-2222-3333-444444444444"
    assert payload["sessions"][0]["title"] == "Host title"
    assert payload["sessions"][0]["numEvents"] == 3
    assert "stamps" in payload
    mtime1 = dest.stat().st_mtime
    second = write_host_catalog_export(dest, host_root=host)
    assert second == dest
    assert dest.stat().st_mtime == mtime1


def test_list_session_catalog_reuses_host_snapshot(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    (work / "runs" / "traces").mkdir(parents=True)
    host = tmp_path / "host"
    _host_session(host, "019dddd-1111-2222-3333-444444444444", title="Snap")
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr("groket.paths.analysis_cache_dir", lambda: cache)
    monkeypatch.setattr("groket.session.mtime_export.analysis_cache_dir", lambda: cache)

    rows1 = list_session_catalog(work, include_host=True, host_root=host)
    assert rows1[0]["sessionId"] == "019dddd-1111-2222-3333-444444444444"
    snaps = list(cache.glob("host-catalog-*.json"))
    assert len(snaps) == 1
    mtime1 = snaps[0].stat().st_mtime

    opened: list[str] = []
    real_open = Path.open

    def track_open(self, *args, **kwargs):
        opened.append(self.name)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    rows2 = list_session_catalog(work, include_host=True, host_root=host)
    assert rows2[0]["title"] == "Snap"
    assert snaps[0].stat().st_mtime == mtime1
    assert "events.jsonl" not in opened
