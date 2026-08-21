"""Session export embeds nested official grok-trace.tar.gz (CLI only)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from groket.session.export_bundle import (
    GROK_TRACE_ARCHIVE_NAME,
    assert_grok_trace_archive_shape,
    build_grok_trace_archive,
    export_session_bundle,
    grok_trace_member_paths,
    run_volume_for_session,
)

# Official core members from a real ``grok trace`` export.
_ACTUAL_CORE = frozenset(
    {
        "export_metadata.json",
        "trace_config.json",
        "summary.json",
        "events.jsonl",
        "chat_history.jsonl",
        "prompt_context.json",
        "system_prompt.txt",
    }
)

SID = "019f-test-session"


def _seed_session(root: Path) -> Path:
    """Layout: runs/traces/groket-abc/%2Fworkspace/<sid>/…"""
    run = root / "runs" / "traces" / "groket-abc-model"
    sess = run / "%2Fworkspace" / SID
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text('{"type":"x"}\n', encoding="utf-8")
    (sess / "summary.json").write_text('{"ok":true}\n', encoding="utf-8")
    (sess / "chat_history.jsonl").write_text("{}\n", encoding="utf-8")
    (sess / "prompt_context.json").write_text("{}\n", encoding="utf-8")
    (sess / "system_prompt.txt").write_text("sys\n", encoding="utf-8")
    (run / "run.json").write_text('{"run_id":"r1"}\n', encoding="utf-8")
    (run / "groket-prompt.txt").write_text("hello\n", encoding="utf-8")
    (run / "groket-launch.json").write_text("{}\n", encoding="utf-8")
    (run / "%2Fworkspace" / "prompt_history.jsonl").write_text("p\n", encoding="utf-8")
    turn = run / ".groket-turn"
    turn.mkdir()
    (turn / "scripted-turns.json").write_text("[]\n", encoding="utf-8")
    return sess


def _fake_cli_archive_bytes(session_id: str = SID) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name in _ACTUAL_CORE:
            data = b"{}\n" if name.endswith(".json") else b""
            if name == "export_metadata.json":
                data = json.dumps(
                    {
                        "session_id": session_id,
                        "grok_version": "0.2.106",
                        "os": "linux",
                        "arch": "x86_64",
                        "exported_at": "2026-07-20T00:00:00+00:00",
                    }
                ).encode()
            if name == "trace_config.json":
                data = json.dumps({"trace_upload_enabled": False}).encode()
            info = tarfile.TarInfo(name=f"{session_id}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _patch_cli(monkeypatch: pytest.MonkeyPatch, payload: bytes | None = None) -> None:
    expected = payload if payload is not None else _fake_cli_archive_bytes()

    def _fake_cli(_session_dir: Path, out_tar: Path) -> None:
        out_tar.parent.mkdir(parents=True, exist_ok=True)
        out_tar.write_bytes(expected)

    monkeypatch.setattr(
        "groket.session.export_bundle.build_grok_trace_archive",
        _fake_cli,
    )


def test_run_volume_for_session(tmp_path: Path) -> None:
    sess = _seed_session(tmp_path)
    vol = run_volume_for_session(sess)
    assert vol is not None
    assert vol.name == "groket-abc-model"


def test_build_grok_trace_uses_cli_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nested archive is the CLI file as-is (exact bytes)."""
    sess = _seed_session(tmp_path)
    expected = _fake_cli_archive_bytes()

    def _fake_run(cmd: list[str], **_kwargs: object) -> CompletedProcess[str]:
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_bytes(expected)
        return CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        "groket.session.export_bundle.which",
        lambda _name: "/usr/bin/grok",
    )
    monkeypatch.setattr(
        "groket.session.export_bundle.subprocess.run",
        _fake_run,
    )
    out = tmp_path / "from-cli.tar.gz"
    build_grok_trace_archive(sess, out)
    assert out.read_bytes() == expected
    assert set(assert_grok_trace_archive_shape(out, SID)) >= grok_trace_member_paths(SID)


def test_build_grok_trace_no_fallback_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _seed_session(tmp_path)
    monkeypatch.setattr("groket.session.export_bundle.which", lambda _name: None)
    with pytest.raises(RuntimeError, match="grok CLI not found"):
        build_grok_trace_archive(sess, tmp_path / "x.tar.gz")


def test_export_parent_packs_openable_child_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _seed_session(tmp_path)
    token = parent.parent
    child = token / "child-exp"
    child.mkdir()
    (child / "summary.json").write_text(
        json.dumps({"info": {"id": "child-exp"}, "session_kind": "subagent"}),
        encoding="utf-8",
    )
    (child / "updates.jsonl").write_text("{}\n", encoding="utf-8")
    (parent / "subagents" / "child-exp").mkdir(parents=True)
    (parent / "subagents" / "child-exp" / "meta.json").write_text(
        json.dumps({"child_session_id": "child-exp", "subagent_type": "coder"}),
        encoding="utf-8",
    )
    (parent / "updates.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 1,
                "params": {
                    "update": {
                        "sessionUpdate": "subagent_spawned",
                        "childSessionId": "child-exp",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _patch_cli(monkeypatch)
    dest = tmp_path / "with-child.tar.gz"
    result = export_session_bundle(parent, dest=dest)
    child_member = f"children/child-exp/{GROK_TRACE_ARCHIVE_NAME}"
    assert child_member in result.arcnames
    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
        assert child_member in names
        manifest = json.loads(tf.extractfile("manifest.json").read().decode())  # type: ignore[union-attr]
    assert manifest["schema"] == 8
    assert manifest["children"][0]["sessionId"] == "child-exp"
    assert manifest["children"][0]["member"] == child_member


def test_export_session_bundle_embeds_nested_grok_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _seed_session(tmp_path)
    expected = _fake_cli_archive_bytes()
    _patch_cli(monkeypatch, expected)

    from groket.notes import NoteEntry, NotesDoc, save_notes

    notes_doc = NotesDoc(schema_id="default", session_id=SID)
    notes_doc.upsert(
        NoteEntry.new(
            turn_index=0,
            fields={"summary": "export me", "detail": "turn note"},
            event_indices=[2],
            note_id="n-export",
        )
    )
    save_notes(sess, notes_doc)

    dest = tmp_path / "out" / "bundle.tar.gz"
    result = export_session_bundle(
        sess,
        dest=dest,
    )
    assert result.path == dest.resolve()
    assert result.session_id == SID
    assert dest.is_file()

    with tarfile.open(dest, "r:gz") as tf:
        names = set(tf.getnames())
        manifest = json.loads(tf.extractfile("manifest.json").read().decode())  # type: ignore[union-attr]
        nested_f = tf.extractfile(GROK_TRACE_ARCHIVE_NAME)
        assert nested_f is not None
        nested_bytes = nested_f.read()
        notes_f = tf.extractfile("notes/operator_notes.toml")
        assert notes_f is not None
        notes_text = notes_f.read().decode()

    assert nested_bytes == expected
    assert "manifest.json" in names
    assert "README.txt" in names
    assert GROK_TRACE_ARCHIVE_NAME in names
    assert [n for n in names if n.endswith(".tar.gz")] == [GROK_TRACE_ARCHIVE_NAME]
    assert not any(n == SID or n.startswith(f"{SID}/") for n in names)
    assert not any(n == "feedback" or n.startswith("feedback/") for n in names)

    nested_path = tmp_path / "extracted-nested.tar.gz"
    nested_path.write_bytes(nested_bytes)
    nested_names = set(assert_grok_trace_archive_shape(nested_path, SID))
    for core in _ACTUAL_CORE:
        assert f"{SID}/{core}" in nested_names

    assert "run/run.json" in names
    assert "run/groket-prompt.txt" in names
    assert "run/prompt_history.jsonl" in names
    assert "run/.groket-turn/scripted-turns.json" in names
    assert "human/summary.md" in names
    assert not any(n == "analysis" or n.startswith("analysis/") for n in names)
    assert "notes/operator_notes.toml" in names
    assert "notes/schema.toml" not in names
    assert "export me" in notes_text
    assert "n-export" in notes_text
    assert manifest["session_id"] == SID
    assert manifest["grok_trace"] == GROK_TRACE_ARCHIVE_NAME
    assert manifest["schema"] == 8
    assert manifest["children"] == []
    assert manifest["profile"] == "archive-full"
    assert manifest["packaging"] == "tar.gz"
    assert "grok_trace" in manifest["include"]
    assert "session_dir" not in manifest
    assert "run_volume" not in manifest
    assert GROK_TRACE_ARCHIVE_NAME in manifest["members"]
    assert "notes/operator_notes.toml" in manifest["members"]
    assert set(manifest["members"]) == names
    assert set(result.arcnames) == names
    assert result.profile_id == "archive-full"
    assert result.packaging == "tar.gz"


def test_export_cli_failure_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sess = _seed_session(tmp_path)

    def _fail(_session_dir: Path, out_tar: Path) -> None:
        raise RuntimeError("grok trace --local failed (rc=1): boom")

    monkeypatch.setattr(
        "groket.session.export_bundle.build_grok_trace_archive",
        _fail,
    )
    with pytest.raises(RuntimeError, match="grok trace --local failed"):
        export_session_bundle(sess, dest=tmp_path / "out.tar.gz")


def test_export_missing_session_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        export_session_bundle(tmp_path / "nope", dest=tmp_path / "x.tar.gz")


def test_export_fallback_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sess = _seed_session(tmp_path)
    _patch_cli(monkeypatch)
    flags_dir = tmp_path / "flag-fallback" / SID
    flags_dir.mkdir(parents=True)
    (flags_dir / "flags.json").write_text(
        json.dumps([{"event_index": 1, "verdict": "bad", "description": "fallback flag"}]) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "groket.flags.flags_fallback_file",
        lambda sid: tmp_path / "flag-fallback" / sid / "flags.json",
    )
    dest = tmp_path / "with-flags.tar.gz"
    result = export_session_bundle(sess, dest=dest)
    with tarfile.open(result.path, "r:gz") as tf:
        names = set(tf.getnames())
        raw = tf.extractfile("flags.json")
        assert raw is not None
        body = raw.read().decode()
    assert "flags.json" in names
    assert "fallback flag" in body


def test_export_session_local_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Session-local flags go in the outer bundle (not inside nested grok-trace)."""
    sess = _seed_session(tmp_path)
    (sess / "flags.json").write_text(
        json.dumps([{"event_index": 2, "verdict": "good", "description": "session flag"}]) + "\n",
        encoding="utf-8",
    )
    _patch_cli(monkeypatch)
    dest = tmp_path / "session-flags.tar.gz"
    result = export_session_bundle(sess, dest=dest)
    with tarfile.open(result.path, "r:gz") as tf:
        names = set(tf.getnames())
        raw = tf.extractfile("flags.json")
        assert raw is not None
        body = raw.read().decode()
    assert "flags.json" in names
    assert "session flag" in body


def test_export_trace_only_profile_skips_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _seed_session(tmp_path)
    _patch_cli(monkeypatch)
    dest = tmp_path / "trace-only.tar.gz"
    result = export_session_bundle(
        sess,
        dest=dest,
        profile="trace-only",
    )
    with tarfile.open(result.path, "r:gz") as tf:
        names = set(tf.getnames())
    assert GROK_TRACE_ARCHIVE_NAME in names
    assert "manifest.json" in names
    assert "README.txt" in names
    assert not any(n.startswith("analysis/") for n in names)
    assert not any(n.startswith("run/") for n in names)
    assert result.profile_id == "trace-only"


def test_export_dir_packaging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from groket.session.export_spec import ExportSpec, IncludeUnit, Packaging

    sess = _seed_session(tmp_path)
    _patch_cli(monkeypatch)
    dest = tmp_path / "out-dir"
    spec = ExportSpec(
        profile_id="dir-full",
        packaging=Packaging.DIR,
        include=frozenset(
            {
                IncludeUnit.GROK_TRACE,
                IncludeUnit.RUN,
                IncludeUnit.MANIFEST,
                IncludeUnit.README,
            }
        ),
    )
    result = export_session_bundle(sess, dest=dest, spec=spec)
    assert result.path.is_dir()
    assert (result.path / GROK_TRACE_ARCHIVE_NAME).is_file()
    assert (result.path / "manifest.json").is_file()
    assert (result.path / "run" / "run.json").is_file()
    assert result.packaging == "dir"


def test_export_archive_org_writes_org_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sess = _seed_session(tmp_path)
    _patch_cli(monkeypatch)
    dest = tmp_path / "org-bundle.tar.gz"
    result = export_session_bundle(
        sess,
        dest=dest,
        profile="archive-org",
    )
    assert result.profile_id == "archive-org"
    with tarfile.open(result.path, "r:gz") as tf:
        names = set(tf.getnames())
        sum_f = tf.extractfile("human/summary.org")
        assert sum_f is not None
        sum_text = sum_f.read().decode()
    assert not any(n == "analysis" or n.startswith("analysis/") for n in names)
    assert "human/summary.org" in names
    assert "human/summary.md" not in names
    assert "#+TITLE:" in sum_text
    assert SID in sum_text
