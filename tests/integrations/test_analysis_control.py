"""Control owner does not run analysis."""

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
        json.dumps({"info": {"id": session_dir.name}, "generated_title": "Analyze me"}),
        encoding="utf-8",
    )
    (session_dir / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1000,
                "params": {
                    "update": {
                        "sessionUpdate": "user_message_chunk",
                        "content": {"type": "text", "text": "hi"},
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (session_dir / "events.jsonl").write_text(
        json.dumps({"type": "turn_ended", "timestamp": 1001}) + "\n",
        encoding="utf-8",
    )


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
        response = json.loads(await asyncio.wait_for(reader.readline(), timeout=8))
        if response.get("id") == request_id:
            return response


@pytest.mark.asyncio
async def test_owner_does_not_advertise_or_run_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Serve initialize omits analysis methods; analysis/run is unknown."""
    daemon = import_module("groket.integrations.daemon")
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    session = traces / "session-analysis"
    _write_session(session)
    sock = _short_sock("analysis.sock")
    server = daemon.build_domain_control_server(
        socket_path=sock,
        work_dir=work,
        traces_path=traces,
    )
    assert not hasattr(server, "_analysis_jobs")
    assert not hasattr(server, "_analysis_pool")
    await server.start()
    try:
        reader, writer = await asyncio.open_unix_connection(sock)
        init = await _request(
            reader, writer, 1, "initialize", {"protocolVersion": PROTOCOL_VERSION}
        )
        caps = init["result"]["capabilities"]
        assert "analysis/run" not in caps
        assert "analysis/status" not in caps
        assert init["result"]["protocolVersion"] == PROTOCOL_VERSION

        missing = await _request(
            reader,
            writer,
            2,
            "analysis/run",
            {"session": session.name, "force": True},
        )
        assert "error" in missing
        assert missing["error"]["code"] == -32601

        status = await _request(reader, writer, 3, "analysis/status", {"session": session.name})
        assert "error" in status
        assert status["error"]["code"] == -32601

        writer.close()
        await writer.wait_closed()
    finally:
        await server.close()
