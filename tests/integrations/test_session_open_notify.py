"""session/open broadcasts session/selected on headless serve."""

from __future__ import annotations

import asyncio
import json
import tempfile
from importlib import import_module
from pathlib import Path

import pytest
from groket.integrations.control import PROTOCOL_VERSION


def _short_sock(name: str) -> Path:
    root = Path(tempfile.mkdtemp(prefix="groket-ctl-"))
    return root / name


def _write_session(session_dir: Path) -> None:
    session_dir.mkdir(parents=True)
    (session_dir / "summary.json").write_text(
        json.dumps({"info": {"id": session_dir.name}}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text("{}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_session_open_notifies_without_open_callback(tmp_path: Path) -> None:
    daemon = import_module("groket.integrations.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    session = traces / "sess-open"
    _write_session(session)
    sock = _short_sock("open-notify.sock")
    server = daemon.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
    )
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        # Initialize so framing is known for broadcasts.
        writer.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": PROTOCOL_VERSION},
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        init = json.loads(await asyncio.wait_for(reader.readline(), timeout=3))
        assert init["result"]["protocolVersion"] == PROTOCOL_VERSION

        writer.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "session/open",
                    "params": {"session": session.name, "promptIndex": 3},
                }
            ).encode()
            + b"\n"
        )
        await writer.drain()
        # Response first, then session/selected notify.
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=3))
        assert response["id"] == 2
        assert response["result"] == {"opened": True}
        notify = json.loads(await asyncio.wait_for(reader.readline(), timeout=3))
        assert notify["method"] == "session/selected"
        assert notify["params"]["sessionId"] == session.name
        assert notify["params"]["promptIndex"] == 3
        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
