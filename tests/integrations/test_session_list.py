"""``session/list`` catalog filtering and control dispatch."""

from __future__ import annotations

import asyncio
import json
import tempfile
from importlib import import_module
from pathlib import Path

import pytest
from groket.integrations.control import PROTOCOL_VERSION


def _catalog() -> list[dict]:
    return [
        {
            "sessionId": "alpha-1",
            "path": "/tmp/alpha-1",
            "title": "Socket review",
            "label": "Socket review",
            "model": "grok-4",
            "status": "complete",
            "outcome": "success",
            "origin": "work",
        },
        {
            "sessionId": "beta-host",
            "path": "/tmp/beta-host",
            "title": "Host debug",
            "label": "Host debug",
            "model": "grok-3",
            "status": "running",
            "outcome": "running",
            "origin": "host",
        },
    ]


def test_emacs_and_vim_list_helpers_keep_query_limit() -> None:
    """Editors still call session/list with query/limit only (first page)."""
    root = Path(__file__).resolve().parents[2]
    el = (root / "groket/integrations/emacs/groket.el").read_text(encoding="utf-8")
    assert "(defun groket--session-list (&optional query limit)" in el
    assert ":limit limit" in el
    lua = (root / "groket/integrations/vim/lua/groket/init.lua").read_text(encoding="utf-8")
    assert "function M.list_sessions(query, limit)" in lua
    assert "params.limit = limit" in lua


def test_filter_session_catalog_query_and_limit() -> None:
    from groket.session.access import filter_session_catalog

    full = filter_session_catalog(_catalog())
    assert full["total"] == 2
    assert full["matched"] == 2
    assert len(full["sessions"]) == 2

    host_only = filter_session_catalog(_catalog(), query="host")
    assert host_only["total"] == 2
    assert host_only["matched"] == 1
    assert host_only["sessions"][0]["sessionId"] == "beta-host"

    # Case-insensitive substring (HUD/TUI list query contract).
    casefold = filter_session_catalog(_catalog(), query="SOCKET")
    assert casefold["matched"] == 1
    assert casefold["sessions"][0]["sessionId"] == "alpha-1"

    empty_q = filter_session_catalog(_catalog(), query="")
    assert empty_q["matched"] == 2
    assert len(empty_q["sessions"]) == 2

    limited = filter_session_catalog(_catalog(), limit=1)
    assert limited["matched"] == 2
    assert len(limited["sessions"]) == 1

    paged = filter_session_catalog(_catalog(), limit=1, offset=1)
    assert paged["matched"] == 2
    assert paged["sessions"][0]["sessionId"] == "beta-host"


async def _request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request_id: int,
    method: str,
    params: dict | None = None,
) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    writer.write(json.dumps(payload).encode("utf-8") + b"\n")
    await writer.drain()
    while True:
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=2))
        if response.get("id") == request_id:
            return response


def _short_sock(name: str) -> Path:
    """Short unique AF_UNIX path (macOS path limit + multi-user / xdist safe)."""
    root = Path(tempfile.mkdtemp(prefix="groket-ctl-"))
    return root / name


@pytest.mark.asyncio
async def test_control_server_session_list() -> None:
    control = import_module("groket.integrations.control")
    sock = _short_sock("session-list")
    server = control.ControlServer(
        socket_path=sock,
        list_sessions=_catalog,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        init = await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {"name": "test"}},
        )
        assert "session/list" in init["result"]["capabilities"]

        listed = await _request(reader, writer, 2, "session/list", {"query": "review"})
        assert listed["result"]["matched"] == 1
        assert listed["result"]["sessions"][0]["sessionId"] == "alpha-1"
        assert listed["result"]["sessions"][0]["path"] == "/tmp/alpha-1"

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_render_formats_and_markdown(tmp_path: Path) -> None:
    control = import_module("groket.integrations.control")
    session_dir = tmp_path / "groket-test-render-session"
    session_dir.mkdir()
    (session_dir / "summary.json").write_text(
        '{"info":{"id":"groket-test-render-session"},"generated_title":"Fmt"}',
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        '{"timestamp":1,"params":{"update":{"sessionUpdate":"user_message_chunk",'
        '"content":{"type":"text","text":"hi"},"_meta":{"promptIndex":2}}}}\n',
        encoding="utf-8",
    )
    sock = _short_sock("render-fmt")
    server = control.ControlServer(
        socket_path=sock,
        resolve_session=lambda ref: (
            session_dir if ref in {session_dir.name, str(session_dir)} else None
        ),
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        init = await _request(
            reader,
            writer,
            1,
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "clientInfo": {"name": "test"}},
        )
        assert "markdown" in init["result"]["renderFormats"]
        assert "json" in init["result"]["renderFormats"]
        assert "session/list" in init["result"]["capabilities"]

        md = await _request(
            reader,
            writer,
            2,
            "session/render",
            {"session": session_dir.name, "format": "markdown"},
        )
        assert md["result"]["format"] == "markdown"
        assert md["result"]["contentType"] == "text/markdown"
        assert "## Prompt 2" in md["result"]["text"]
        assert "<!-- groket:prompt-index=2" in md["result"]["text"]

        bad = await _request(
            reader,
            writer,
            3,
            "session/render",
            {"session": session_dir.name, "format": "rtf"},
        )
        assert bad.get("error", {}).get("code") == -32602

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_session_list_empty_without_lister() -> None:
    control = import_module("groket.integrations.control")
    server = control.ControlServer(socket_path=_short_sock("session-list-empty"))
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        listed = await _request(reader, writer, 1, "session/list", {})
        assert listed["result"]["sessions"] == []
        assert listed["result"]["total"] == 0
        assert listed["result"]["matched"] == 0
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_control_server_session_list_rejects_bad_limit() -> None:
    control = import_module("groket.integrations.control")
    server = control.ControlServer(
        socket_path=_short_sock("session-list-limit"),
        list_sessions=_catalog,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        bad = await _request(reader, writer, 1, "session/list", {"limit": "abc"})
        assert bad.get("error", {}).get("code") == -32602
        assert "limit" in bad["error"]["message"]

        ok = await _request(reader, writer, 2, "session/list", {"limit": 1})
        assert len(ok["result"]["sessions"]) == 1
        assert ok["result"]["matched"] == 2

        page2 = await _request(reader, writer, 3, "session/list", {"limit": 1, "offset": 1})
        assert page2["result"]["sessions"][0]["sessionId"] == "beta-host"
        assert page2["result"]["matched"] == 2

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
