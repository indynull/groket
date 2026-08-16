"""Draw-time title classify and mtime-gated host catalog export."""

from __future__ import annotations

import json
from pathlib import Path

from groket.session.classify import classify_title
from groket.session.mtime_export import write_host_catalog_export


def test_classify_title_maps_known_and_passes_unknown() -> None:
    assert classify_title("Google Chrome - docs") == "Chrome"
    assert classify_title("grokos-agent [seat]") == "seat"
    assert classify_title("weird-unique-title-xyz") == "weird-unique-title-xyz"
    assert classify_title("  ") == ""


def test_host_export_is_names_and_mtimes(tmp_path: Path) -> None:
    host = tmp_path / "host"
    sd = host / "019cccc-1111-2222-3333-444444444444"
    sd.mkdir(parents=True)
    (sd / "summary.json").write_text(
        json.dumps({"generated_title": "Google Chrome - work", "info": {"id": sd.name}}),
        encoding="utf-8",
    )
    (sd / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    dest = tmp_path / "out" / "host.json"
    first = write_host_catalog_export(dest, host_root=host)
    assert first == dest
    payload = json.loads(dest.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["sessionId"] == sd.name
    assert payload["sessions"][0]["label"] == "Chrome"
    mtime1 = dest.stat().st_mtime
    second = write_host_catalog_export(dest, host_root=host)
    assert second == dest
    assert dest.stat().st_mtime == mtime1
