"""LocalSessionAccess domain façade (in-process, no control socket)."""

from __future__ import annotations

from pathlib import Path

import pytest
from groket.session.access import (
    LocalSessionAccess,
    catalog_list_next_offset,
    filter_session_catalog,
)


def test_filter_session_catalog_query_and_limit() -> None:
    rows = [
        {
            "sessionId": "a",
            "path": "/tmp/a",
            "title": "Alpha Rocket",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
            "origin": "eval",
        },
        {
            "sessionId": "b",
            "path": "/tmp/b",
            "title": "Host session",
            "label": "",
            "model": "grok",
            "status": "running",
            "outcome": "",
            "origin": "host",
        },
    ]
    full = filter_session_catalog(rows)
    assert full["total"] == 2
    assert full["matched"] == 2
    assert len(full["sessions"]) == 2

    host_only = filter_session_catalog(rows, query="host")
    assert host_only["matched"] == 1
    assert host_only["sessions"][0]["sessionId"] == "b"

    casefold = filter_session_catalog(rows, query="ROCKET")
    assert casefold["matched"] == 1
    assert casefold["sessions"][0]["sessionId"] == "a"

    limited = filter_session_catalog(rows, limit=1)
    assert len(limited["sessions"]) == 1
    assert limited["matched"] == 2


def test_filter_session_catalog_offset_pages() -> None:
    rows = [
        {
            "sessionId": "a",
            "title": "Alpha",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
            "origin": "eval",
        },
        {
            "sessionId": "b",
            "title": "Beta",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
            "origin": "eval",
        },
        {
            "sessionId": "c",
            "title": "Gamma",
            "label": "",
            "model": "grok",
            "status": "complete",
            "outcome": "",
            "origin": "eval",
        },
    ]
    first = filter_session_catalog(rows, limit=2, offset=0)
    assert [r["sessionId"] for r in first["sessions"]] == ["a", "b"]
    assert first["matched"] == 3
    second = filter_session_catalog(rows, limit=2, offset=2)
    assert [r["sessionId"] for r in second["sessions"]] == ["c"]
    assert second["matched"] == 3
    past = filter_session_catalog(rows, limit=2, offset=9)
    assert past["sessions"] == []
    assert past["matched"] == 3


def test_catalog_list_next_offset() -> None:
    assert catalog_list_next_offset(0, 200, 200, 450) == 200
    assert catalog_list_next_offset(200, 200, 200, 450) == 400
    assert catalog_list_next_offset(400, 50, 200, 450) is None
    assert catalog_list_next_offset(0, 200, 200, 200) is None
    assert catalog_list_next_offset(200, 200, 200, 450, stalled=True) is None
    assert catalog_list_next_offset(0, 0, 200, 10) is None


def test_filter_session_catalog_query_ignores_path() -> None:
    """Substring search must not match the filesystem path (``~/.grok/sessions``)."""
    rows = [
        {
            "sessionId": "019abc",
            "path": "/home/ali/.grok/sessions/019abc",
            "title": "Fix the palette",
            "label": "",
            "model": "other",
            "status": "complete",
            "outcome": "success",
            "origin": "host",
        },
        {
            "sessionId": "work-1",
            "path": "/tmp/work/runs/traces/work-1",
            "title": "Review",
            "label": "",
            "model": "other",
            "status": "running",
            "outcome": "",
            "origin": "work",
        },
    ]
    by_path = filter_session_catalog(rows, query=".grok/sessions")
    assert by_path["matched"] == 0
    by_title = filter_session_catalog(rows, query="palette")
    assert by_title["matched"] == 1
    assert by_title["sessions"][0]["sessionId"] == "019abc"
    by_id = filter_session_catalog(rows, query="019abc")
    assert by_id["matched"] == 1
    assert by_id["sessions"][0]["sessionId"] == "019abc"


def test_local_access_list_and_missing_session(tmp_path: Path) -> None:
    session = tmp_path / "sess-one"
    session.mkdir()
    (session / "signals.json").write_text("{}", encoding="utf-8")

    def resolve(ref: str) -> Path | None:
        if ref in {session.name, str(session)}:
            return session
        p = Path(ref)
        return p if p.is_dir() else None

    access = LocalSessionAccess(
        resolve_session=resolve,
        list_sessions=lambda: [
            {
                "sessionId": session.name,
                "path": str(session),
                "title": "One",
                "origin": "eval",
            }
        ],
        work_dir=tmp_path,
    )
    listed = access.list_sessions(query="one")
    assert listed["matched"] == 1
    assert listed["sessions"][0]["sessionId"] == session.name

    with pytest.raises(FileNotFoundError):
        access.session_get("missing-id")

    got = access.session_get(session.name)
    assert got.get("sessionId") == session.name or "path" in got
    assert not hasattr(access, "analysis_run")


def test_local_access_follow_up_and_done(tmp_path: Path) -> None:
    import json

    vol = tmp_path / "traces" / "run"
    sess = vol / "%2Fworkspace" / "sess-follow"
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    gate = vol / ".groket-turn"
    gate.mkdir(parents=True)
    (gate / "status.json").write_text(
        json.dumps({"state": "awaiting_follow_up", "session_id": "sess-follow", "turn": 1}) + "\n",
        encoding="utf-8",
    )

    access = LocalSessionAccess(
        resolve_session=lambda ref: sess if ref in {sess.name, str(sess)} else None,
        list_sessions=lambda: [],
        work_dir=tmp_path,
    )
    sent = access.session_follow_up(str(sess), "continue")
    assert sent["ok"] is True
    assert sent["how"] in {"sent", "queued"}
    done = access.session_done(str(sess))
    assert done["ok"] is True
    assert (gate / "command").read_text(encoding="utf-8").strip() == "done"
