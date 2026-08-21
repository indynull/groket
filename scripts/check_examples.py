#!/usr/bin/env python3
"""Hard gate: ``examples/`` must stay loadable and schema-valid.

Run via ``just examples-check`` or CI. Exit 0 only when every pack is sound.
Nothing under ``examples/`` is auto-loaded by the product; this script is the
contract that copy/paste references do not rot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class _Fail(Exception):
    """Single validation failure with a path and message."""


def _ok(msg: str) -> None:
    print(f"OK  {msg}")


def _err(path: Path | str, msg: str) -> None:
    raise _Fail(f"{path}: {msg}")


def _repo_rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# ── tasks ────────────────────────────────────────────────────────────────────


def check_tasks() -> None:
    from groket.runs.task_schema import load_task_file

    tasks_dir = EXAMPLES / "tasks"
    files = sorted(tasks_dir.glob("*.yaml")) + sorted(tasks_dir.glob("*.yml"))
    if not files:
        _err(tasks_dir, "no task YAML files")
    for path in files:
        try:
            doc = load_task_file(path)
        except Exception as exc:
            _err(path, f"schema invalid: {exc}")
        n = len(doc.resolved_tasks())
        if n < 1:
            _err(path, "no resolved tasks")
        _ok(f"{_repo_rel(path)}  ({n} task(s), schema_version={doc.schema_version})")


# ── personas ─────────────────────────────────────────────────────────────────


def check_personas() -> None:
    from groket.runs.personas import Persona

    personas_dir = EXAMPLES / "personas"
    if not personas_dir.is_dir():
        return
    files = sorted(personas_dir.glob("*.json"))
    if not files:
        _err(personas_dir, "empty personas dir")
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _err(path, f"invalid JSON: {exc}")
        if not isinstance(raw, dict):
            _err(path, "root must be an object")
        try:
            p = Persona.from_dict(raw)  # type: ignore[arg-type]
        except Exception as exc:
            _err(path, f"Persona.from_dict failed: {exc}")
        if not (p.persona_id or "").strip():
            _err(path, "persona_id empty")
        _ok(f"{_repo_rel(path)}  persona_id={p.persona_id}")


# ── docs presence ────────────────────────────────────────────────────────────


def check_readmes() -> None:
    required = [
        EXAMPLES / "README.md",
        EXAMPLES / "tasks" / "README.md",
        EXAMPLES / "notes" / "README.md",
        EXAMPLES / "keys" / "README.md",
        EXAMPLES / "config" / "README.md",
    ]
    for path in required:
        if not path.is_file() or path.stat().st_size < 40:
            _err(path, "missing or empty README")
        text = path.read_text(encoding="utf-8")
        for stale in (
            "all-plugins.json",
            "security-only.json",
            "code-quality.json",
            "teachx-v2-mf.json",
        ):
            if stale in text:
                _err(path, f"prefs samples are .toml; found {stale}")
        _ok(f"{_repo_rel(path)}")


def check_keys_overlay() -> None:
    """Validate examples/keys overlays load cleanly."""
    from groket.keys import load_keymap

    keys_dir = EXAMPLES / "keys"
    files = sorted(keys_dir.glob("*.toml"))
    if not files:
        _err(keys_dir, "no keys.toml overlays")
    for path in files:
        keymap = load_keymap(path)
        if not keymap.ok:
            msgs = "; ".join(err.message for err in keymap.errors) or "refused"
            _err(path, msgs)
        if not keymap.loaded_overlay:
            _err(path, "overlay did not apply")
        _ok(f"{_repo_rel(path)}  leader={keymap.leader or '-'} bindings={len(keymap.bindings)}")


def check_app_config() -> None:
    """Validate examples/config/config.toml against AppConfig."""
    from groket.config import SCHEMA_ID, validate_config_file

    path = EXAMPLES / "config" / "config.toml"
    if not path.is_file():
        _err(path, "missing prefs example")
    text = path.read_text(encoding="utf-8")
    if SCHEMA_ID not in text:
        _err(path, f"missing schema comment {SCHEMA_ID}")
    try:
        cfg = validate_config_file(path)
    except ValueError as exc:
        _err(path, str(exc))
    if cfg.theme != "groket":
        _err(path, f"expected default theme groket, got {cfg.theme!r}")
    _ok(f"{_repo_rel(path)}  theme={cfg.theme}")


def check_notes_schema() -> None:
    """Validate examples/notes schema example loads with non-empty fields."""
    from groket.notes import load_schema

    path = EXAMPLES / "notes" / "notes_schema.example.toml"
    if not path.is_file():
        _err(path, "missing notes schema example")
    schema = load_schema(path=path)
    if not schema.fields:
        _err(path, "schema has no fields")
    for spec in schema.fields:
        if not (spec.id or "").strip() or not (spec.label or "").strip():
            _err(path, f"empty field id/label in {spec!r}")
    _ok(f"{_repo_rel(path)}  schema_id={schema.schema_id} fields={len(schema.fields)}")


def main() -> int:
    if not EXAMPLES.is_dir():
        print(f"error: missing {EXAMPLES}", file=sys.stderr)
        return 1
    try:
        check_readmes()
        check_tasks()
        check_personas()
        check_notes_schema()
        check_keys_overlay()
        check_app_config()
    except _Fail as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("check_examples: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
