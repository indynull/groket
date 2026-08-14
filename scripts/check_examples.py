#!/usr/bin/env python3
"""Hard gate: ``examples/`` must stay loadable and schema-valid.

Run via ``make examples-check`` or CI. Exit 0 only when every pack is sound.
Nothing under ``examples/`` is auto-loaded by the product; this script is the
contract that copy/paste references do not rot.
"""

from __future__ import annotations

import ast
import importlib.util
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


# ── detection packs ──────────────────────────────────────────────────────────


def _load_detector_dir(det_dir: Path) -> None:
    """Import all detector modules in *det_dir* (sibling imports via sys.path)."""
    root = det_dir.resolve()
    path_s = str(root)
    inserted = path_s not in sys.path
    if inserted:
        sys.path.insert(0, path_s)
    try:
        for py_file in sorted(root.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            stem = py_file.stem
            try:
                if stem in sys.modules:
                    importlib.reload(sys.modules[stem])
                else:
                    importlib.import_module(stem)
            except Exception as exc:
                _err(py_file, f"import failed: {exc}")
    finally:
        if inserted:
            try:
                sys.path.remove(path_s)
            except ValueError:
                pass


def _check_detection_pack(pack: Path) -> None:
    from groket.engine.detectors import clear_detectors, get_all_detectors
    from groket.engine.rule_schema import load_rules_file

    det_dir = pack / "detectors"
    rules_dir = pack / "rules"
    if not det_dir.is_dir() or not rules_dir.is_dir():
        _err(pack, "pack must contain detectors/ and rules/")

    clear_detectors()
    det_files = sorted(p for p in det_dir.glob("*.py") if not p.name.startswith("_"))
    if not det_files:
        _err(det_dir, "no detector modules")
    _load_detector_dir(det_dir)
    registered = get_all_detectors()
    if not registered:
        _err(det_dir, "no @detector registrations after import")

    rule_files = sorted(rules_dir.glob("*.yaml")) + sorted(rules_dir.glob("*.yml"))
    if not rule_files:
        _err(rules_dir, "no rules YAML")
    for yml in rule_files:
        try:
            doc = load_rules_file(yml)
        except Exception as exc:
            _err(yml, f"schema invalid: {exc}")
        for rule in doc.rules:
            det_name = (rule.detector or "").strip()
            if not det_name:
                _err(yml, f"rule {rule.id!r} missing detector")
            if det_name not in registered:
                _err(
                    yml,
                    f"rule {rule.id!r} detector {det_name!r} not registered "
                    f"(have: {', '.join(sorted(registered))})",
                )
        _ok(f"{_repo_rel(yml)}  ({len(doc.rules)} rule(s), {len(doc.composites)} composite(s))")
    _ok(f"{_repo_rel(pack)}  detectors={len(registered)} files={len(det_files)}")
    clear_detectors()


def check_detection() -> None:
    root = EXAMPLES / "detection"
    packs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not packs:
        _err(root, "no detection packs")
    for pack in packs:
        if not (pack / "detectors").is_dir():
            continue
        _check_detection_pack(pack)


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


# ── analysis plugins + configs ───────────────────────────────────────────────


def _import_plugin_module(py_file: Path) -> object:
    module_name = f"_examples_plugin_{py_file.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if spec is None or spec.loader is None:
        _err(py_file, "could not build import spec")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        _err(py_file, f"import failed: {exc}")
    return module


def _analyzer_classes(module: object) -> list[type]:
    from groket.analysis.base import Analyzer

    found: list[type] = []
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name, None)
        if not isinstance(obj, type):
            continue
        if obj is Analyzer:
            continue
        # Structural: has info + analyze (protocol); prefer subclasses when typed.
        if hasattr(obj, "analyze") and hasattr(obj, "info"):
            found.append(obj)
            continue
        # Instantiable class with those attrs as properties after construct.
        try:
            inst = obj()
        except Exception:
            continue
        if hasattr(inst, "analyze") and hasattr(inst, "info"):
            found.append(obj)
    return found


def check_analysis_plugins() -> dict[str, type]:
    """Import every plugin module; return stem → Analyzer class (primary)."""
    plugins_dir = EXAMPLES / "analysis" / "plugins"
    py_files = sorted(p for p in plugins_dir.glob("*.py") if not p.name.startswith("_"))
    if not py_files:
        _err(plugins_dir, "no plugin modules")
    by_stem: dict[str, type] = {}
    for py in py_files:
        # Syntax gate (catches syntax errors without full import path issues).
        try:
            ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as exc:
            _err(py, f"syntax error: {exc}")
        mod = _import_plugin_module(py)
        classes = _analyzer_classes(mod)
        if not classes:
            _err(py, "no Analyzer-like class (need analyze + info)")
        # Prefer ClassName ending in Analyzer
        primary = next((c for c in classes if c.__name__.endswith("Analyzer")), classes[0])
        try:
            inst = primary()
            info = inst.info
            _ = info.id, info.name
        except Exception as exc:
            _err(py, f"instantiate {primary.__name__} failed: {exc}")
        by_stem[py.stem] = primary
        _ok(f"{_repo_rel(py)}  → {primary.__name__}")
    return by_stem


def check_analysis_configs(plugin_classes: dict[str, type]) -> None:
    cfg_dir = EXAMPLES / "analysis" / "configs"
    files = sorted(cfg_dir.glob("*.json"))
    if not files:
        _err(cfg_dir, "no config JSON files")
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _err(path, f"invalid JSON: {exc}")
        if not isinstance(data, dict):
            _err(path, "root must be an object")
        analysis = data.get("analysis")
        if not isinstance(analysis, dict):
            _err(path, "missing analysis object")
        plugins = analysis.get("plugins")
        if not isinstance(plugins, list) or not plugins:
            _err(path, "analysis.plugins must be a non-empty list")
        for entry in plugins:
            if not isinstance(entry, str) or ":" not in entry:
                _err(path, f"plugin entry must be 'module:Class' got {entry!r}")
            stem, cls_name = entry.split(":", 1)
            stem, cls_name = stem.strip(), cls_name.strip()
            if stem not in plugin_classes:
                _err(
                    path, f"unknown plugin module {stem!r} (no examples/analysis/plugins/{stem}.py)"
                )
            cls = plugin_classes[stem]
            if cls.__name__ != cls_name:
                # Allow alternate class in same module
                mod_path = EXAMPLES / "analysis" / "plugins" / f"{stem}.py"
                mod = _import_plugin_module(mod_path)
                if not hasattr(mod, cls_name):
                    _err(path, f"{entry}: class {cls_name!r} not in {stem}.py")
                alt = getattr(mod, cls_name)
                try:
                    alt()
                except Exception as exc:
                    _err(path, f"{entry}: instantiate failed: {exc}")
            else:
                try:
                    cls()
                except Exception as exc:
                    _err(path, f"{entry}: instantiate failed: {exc}")
        _ok(f"{_repo_rel(path)}  ({len(plugins)} plugin(s))")


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
        EXAMPLES / "detection" / "README.md",
        EXAMPLES / "analysis" / "README.md",
        EXAMPLES / "tasks" / "README.md",
        EXAMPLES / "notes" / "README.md",
        EXAMPLES / "keys" / "README.md",
    ]
    for path in required:
        if not path.is_file() or path.stat().st_size < 40:
            _err(path, "missing or empty README")
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
        check_detection()
        check_tasks()
        plugins = check_analysis_plugins()
        check_analysis_configs(plugins)
        check_personas()
        check_notes_schema()
        check_keys_overlay()
    except _Fail as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("check_examples: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
