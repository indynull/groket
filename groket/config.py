"""App-global ``~/.groket/config.toml``.

One file, one shape. Tables are ``analysis``, ``hud``, and ``export``.
Top-level keys are the prefs the terminal app and the desktop HUD share.
Saves use tomlkit so comments on untouched keys stay put.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import cast

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, field_validator
from tomlkit.items import Table

from . import paths
from .models import JsonObject, JsonValue, as_json_object

logger = logging.getLogger(__name__)

SCHEMA_TITLE = "groket-config"
SCHEMA_ID = "https://indynull.github.io/groket/schemas/config.schema.json"
SCHEMA_COMMENT = f":schema {SCHEMA_ID}"

_CACHE: AppConfig | None = None
_CACHE_PATH: Path | None = None
_DOC_CACHE: tomlkit.TOMLDocument | None = None


class AnalysisPrefs(BaseModel):
    """``[analysis]``: worker pools."""

    model_config = ConfigDict(extra="ignore")

    live_refresh_workers: int = Field(
        default=1, ge=1, description="Live timeline refresh pool size."
    )

    @field_validator("live_refresh_workers", mode="before")
    @classmethod
    def _workers(cls, value: object) -> int:
        if isinstance(value, bool):
            return 1
        if isinstance(value, int):
            return max(1, value)
        if isinstance(value, str):
            try:
                return max(1, int(value.strip()))
            except ValueError:
                return 1
        return 1


class HudPrefs(BaseModel):
    """``[hud]``: desktop palette only."""

    model_config = ConfigDict(extra="ignore")

    window_mode: bool = Field(default=False, description="Start the HUD as a normal window.")
    global_shortcut: str = Field(
        default="",
        description="Summon chord (Cmd+Shift+G / Ctrl+Shift+G when empty).",
    )
    desktop_notifications: bool = Field(
        default=True,
        description="Desktop notifications for session status changes.",
    )

    @field_validator("global_shortcut", mode="before")
    @classmethod
    def _shortcut(cls, value: object) -> str:
        return str(value).strip() if isinstance(value, str) else ""


class ExportPrefs(BaseModel):
    """``[export]``: default session-export profile id."""

    model_config = ConfigDict(extra="ignore")

    default_profile: str = Field(
        default="",
        description="Export profile id. Empty uses archive-full.",
    )

    @field_validator("default_profile", mode="before")
    @classmethod
    def _profile(cls, value: object) -> str:
        return str(value).strip() if isinstance(value, str) else ""


class AppConfig(BaseModel):
    """Canonical ``config.toml`` body."""

    model_config = ConfigDict(extra="ignore")

    theme: str = Field(default="groket", description="Textual / HUD theme name.")
    follow_os: bool = Field(
        default=False,
        description="Paired colorways follow the host light/dark setting.",
    )
    show_host_sessions: bool = Field(
        default=False,
        description="List native ~/.grok/sessions next to Docker eval traces.",
    )
    auto_serve: bool = Field(
        default=True,
        description="Detach-start groket serve when the control socket is free.",
    )
    analysis: AnalysisPrefs = Field(default_factory=AnalysisPrefs)
    hud: HudPrefs = Field(default_factory=HudPrefs)
    export: ExportPrefs = Field(default_factory=ExportPrefs)

    @field_validator("theme", mode="before")
    @classmethod
    def _theme(cls, value: object) -> str:
        raw = str(value).strip() if isinstance(value, str) else ""
        return raw or "groket"


def _to_plain(value: object) -> JsonValue:
    unwrap = getattr(value, "unwrap", None)
    if callable(unwrap):
        value = unwrap()
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


def _read_doc(path: Path) -> tomlkit.TOMLDocument:
    if not path.is_file():
        return _blank_document()
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomlkit.exceptions.TOMLKitError):
        logger.debug("config: could not read %s", path, exc_info=True)
        return _blank_document()


def _blank_document() -> tomlkit.TOMLDocument:
    doc = tomlkit.document()
    doc.add(tomlkit.comment(SCHEMA_COMMENT))
    doc.add(tomlkit.nl())
    doc.add(tomlkit.comment("Prefs for the terminal app and the desktop HUD."))
    doc.add(tomlkit.nl())
    return doc


def _ensure_table(container: tomlkit.TOMLDocument | Table, key: str) -> Table:
    item = container.get(key)
    if isinstance(item, Table):
        return item
    table = tomlkit.table()
    container[key] = table
    return table


def _apply_cfg(doc: tomlkit.TOMLDocument, cfg: AppConfig) -> None:
    doc["theme"] = cfg.theme
    doc["follow_os"] = cfg.follow_os
    doc["show_host_sessions"] = cfg.show_host_sessions
    doc["auto_serve"] = cfg.auto_serve
    analysis = _ensure_table(doc, "analysis")
    analysis["live_refresh_workers"] = cfg.analysis.live_refresh_workers
    hud = _ensure_table(doc, "hud")
    hud["window_mode"] = cfg.hud.window_mode
    hud["global_shortcut"] = cfg.hud.global_shortcut
    hud["desktop_notifications"] = cfg.hud.desktop_notifications
    export = _ensure_table(doc, "export")
    export["default_profile"] = cfg.export.default_profile


def parse_app_config(raw: JsonObject) -> AppConfig:
    """Build :class:`AppConfig` from a mapping (TOML unwrapped or test dict).

    :param raw: Decoded object (may be empty).
    :returns: Canonical config (unknown keys dropped from the model).
    """
    hud_section = raw.get("hud")
    hud_raw: JsonObject = as_json_object(hud_section) if isinstance(hud_section, dict) else {}
    if not str(hud_raw.get("global_shortcut") or "").strip():
        flat = raw.get("hud_global_shortcut")
        if isinstance(flat, str) and flat.strip():
            hud_raw = {**hud_raw, "global_shortcut": flat.strip()}
    payload: JsonObject = {
        "analysis": raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {},
        "hud": hud_raw,
        "export": raw.get("export") if isinstance(raw.get("export"), dict) else {},
    }
    for key in ("theme", "follow_os", "show_host_sessions", "auto_serve"):
        if key in raw:
            payload[key] = raw[key]
    return AppConfig.model_validate(payload)


def config_dump(cfg: AppConfig) -> JsonObject:
    """Canonical mapping (tests and the terminal app in-memory copy)."""
    return as_json_object(cfg.model_dump(mode="json"))


def config_json_schema() -> JsonObject:
    """JSON Schema for ``config.toml`` (draft 2020-12 via Pydantic)."""
    schema = AppConfig.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = SCHEMA_ID
    schema["title"] = SCHEMA_TITLE
    schema["description"] = (
        "Prefs file ~/.groket/config.toml. Authoring format is TOML; "
        "this schema describes the same keys for editors and groket config validate."
    )
    return cast(JsonObject, schema)


def emit_config_schema(out: Path | None = None) -> str:
    """Serialize schema JSON; optionally write *out*. Returns the JSON text."""
    text = json.dumps(config_json_schema(), indent=2) + "\n"
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return text


def invalidate_config_cache() -> None:
    """Drop the in-process config cache."""
    global _CACHE, _CACHE_PATH, _DOC_CACHE
    _CACHE = None
    _CACHE_PATH = None
    _DOC_CACHE = None


def load_app_config(path: Path | None = None) -> AppConfig:
    """Load ``~/.groket/config.toml`` (or *path*). Missing or invalid → defaults.

    :param path: Explicit file; default :func:`paths.app_config_path`.
    :returns: Canonical config.
    """
    global _CACHE, _CACHE_PATH, _DOC_CACHE
    fp = Path(path).expanduser() if path is not None else paths.app_config_path()
    if _CACHE is not None and _CACHE_PATH == fp:
        return _CACHE
    _import_json_if_needed(fp)
    doc = _read_doc(fp)
    raw = _to_plain(doc)
    cfg = parse_app_config(as_json_object(raw) if isinstance(raw, dict) else {})
    _CACHE = cfg
    _CACHE_PATH = fp
    _DOC_CACHE = doc
    return cfg


def save_app_config(cfg: AppConfig, path: Path | None = None) -> None:
    """Write known keys into the TOML document (comments on other keys stay).

    :param cfg: Config to persist.
    :param path: Explicit file; default :func:`paths.app_config_path`.
    """
    global _CACHE, _CACHE_PATH, _DOC_CACHE
    fp = Path(path).expanduser() if path is not None else paths.app_config_path()
    if _DOC_CACHE is not None and _CACHE_PATH == fp:
        doc = _DOC_CACHE
    else:
        doc = _read_doc(fp)
    _apply_cfg(doc, cfg)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    _CACHE = cfg
    _CACHE_PATH = fp
    _DOC_CACHE = doc


def update_app_config(path: Path | None = None, **changes: object) -> AppConfig:
    """Load, apply top-level field *changes*, save, return the new config.

    Nested sections pass a full replacement object (``analysis=…``, ``hud=…``).

    :param path: Explicit file; default :func:`paths.app_config_path`.
    :param changes: Field names on :class:`AppConfig`.
    :returns: Saved config.
    """
    cfg = load_app_config(path)
    dumped = cfg.model_dump()
    for key, value in changes.items():
        dumped[key] = value.model_dump() if isinstance(value, BaseModel) else value
    next_cfg = AppConfig.model_validate(dumped)
    save_app_config(next_cfg, path)
    return next_cfg


def leftover_json_config_path(toml_path: Path | None = None) -> Path:
    """Sibling ``config.json`` next to the TOML prefs file."""
    base = Path(toml_path).expanduser() if toml_path is not None else paths.app_config_path()
    return base.with_name("config.json")


def validate_config_file(path: Path) -> AppConfig:
    """Parse *path* as prefs TOML. Raises ``ValueError`` if missing or invalid.

    Unlike :func:`load_app_config`, this does not fall back to defaults or
    import leftover JSON. The CLI ``groket config validate`` uses this path.

    :param path: Prefs file to check.
    :returns: Canonical config.
    :raises ValueError: Missing file, unreadable, or invalid TOML.
    """
    fp = Path(path).expanduser()
    if not fp.is_file():
        raise ValueError(f"{fp} is not a file")
    try:
        text = fp.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"could not read {fp}: {exc}") from exc
    try:
        doc = tomlkit.parse(text)
    except tomlkit.exceptions.TOMLKitError as exc:
        raise ValueError(f"invalid TOML: {exc}") from exc
    raw = _to_plain(doc)
    if not isinstance(raw, dict):
        raise ValueError("config root must be a table")
    return parse_app_config(as_json_object(raw))


def _import_json_if_needed(toml_path: Path) -> None:
    """If ``config.toml`` is missing, copy a sibling ``config.json`` once."""
    if toml_path.is_file():
        return
    src = leftover_json_config_path(toml_path)
    if not src.is_file():
        return
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        logger.debug("config: leftover %s unreadable", src, exc_info=True)
        return
    if not isinstance(data, dict):
        return
    cfg = parse_app_config(as_json_object(data))
    try:
        save_app_config(cfg, toml_path)
    except OSError:
        logger.warning("config: could not write %s from leftover JSON", toml_path, exc_info=True)
        return
    if not toml_path.is_file():
        return
    try:
        src.unlink()
    except OSError:
        logger.warning("config: wrote %s but could not remove leftover %s", toml_path, src)
        return
    logger.info("config: wrote %s and removed leftover %s", toml_path, src)
