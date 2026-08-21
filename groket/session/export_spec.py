"""Export profiles: packaging, include units, renderer id, config defaults.

Profiles are the serialisable “what this export is” recipe. Built-ins ship in
code; user YAML under ``~/.groket/export_profiles/`` overrides by id.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import JsonObject, as_json_object
from ..paths import user_export_profiles_dir

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
DEFAULT_PROFILE_ID = "archive-full"


class Packaging(StrEnum):
    """How the staged tree is written to disk."""

    TAR_GZ = "tar.gz"
    DIR = "dir"


class IncludeUnit(StrEnum):
    """Content units the exporter may collect into the staging tree."""

    GROK_TRACE = "grok_trace"
    RUN = "run"
    SUMMARY = "summary"
    FLAGS = "flags"
    NOTES = "notes"
    README = "readme"
    MANIFEST = "manifest"


# Units available for profiles (extensible later; renderers consume human units).
ALL_INCLUDE_UNITS: frozenset[IncludeUnit] = frozenset(IncludeUnit)

# Default archive profile ≈ cleaned export behaviour after the hygiene pass.
ARCHIVE_FULL_INCLUDE: tuple[IncludeUnit, ...] = (
    IncludeUnit.GROK_TRACE,
    IncludeUnit.RUN,
    IncludeUnit.SUMMARY,
    IncludeUnit.FLAGS,
    IncludeUnit.NOTES,
    IncludeUnit.README,
    IncludeUnit.MANIFEST,
)

TRACE_ONLY_INCLUDE: tuple[IncludeUnit, ...] = (
    IncludeUnit.GROK_TRACE,
    IncludeUnit.README,
    IncludeUnit.MANIFEST,
)


class ExportProfileDoc(BaseModel):
    """YAML profile document (user or built-in serialisation shape)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    id: str = Field(..., min_length=1)
    name: str = ""
    description: str = ""
    packaging: Packaging = Packaging.TAR_GZ
    include: list[str] = Field(default_factory=list)
    renderer: str = "markdown"
    renderer_options: JsonObject = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id_slug(cls, v: str) -> str:
        s = (v or "").strip()
        if not s:
            raise ValueError("profile id must be non-empty")
        return s

    @field_validator("include")
    @classmethod
    def _include_known(cls, v: list[str]) -> list[str]:
        known = {u.value for u in IncludeUnit}
        out: list[str] = []
        for raw in v:
            key = str(raw).strip()
            if not key:
                continue
            if key not in known:
                raise ValueError(f"unknown include unit: {key!r} (known: {sorted(known)})")
            if key not in out:
                out.append(key)
        return out


@dataclass(frozen=True)
class ExportSpec:
    """Resolved export recipe for one run."""

    profile_id: str = DEFAULT_PROFILE_ID
    name: str = ""
    description: str = ""
    packaging: Packaging = Packaging.TAR_GZ
    include: frozenset[IncludeUnit] = field(default_factory=lambda: frozenset(ARCHIVE_FULL_INCLUDE))
    renderer: str = "markdown"
    renderer_options: JsonObject = field(default_factory=dict)

    def includes(self, unit: IncludeUnit) -> bool:
        """True when *unit* is selected."""
        return unit in self.include

    def with_include(self, *units: IncludeUnit) -> Self:
        """Return a copy with *units* added."""
        return replace(self, include=self.include | frozenset(units))

    def without_include(self, *units: IncludeUnit) -> Self:
        """Return a copy with *units* removed."""
        return replace(self, include=self.include - frozenset(units))

    @classmethod
    def from_profile_doc(cls, doc: ExportProfileDoc) -> ExportSpec:
        """Build a spec from a validated profile document."""
        units = (
            frozenset(IncludeUnit(u) for u in doc.include)
            if doc.include
            else frozenset(ARCHIVE_FULL_INCLUDE)
        )
        opts = as_json_object(doc.renderer_options) if doc.renderer_options else {}
        return cls(
            profile_id=doc.id,
            name=doc.name or doc.id,
            description=doc.description,
            packaging=doc.packaging,
            include=units,
            renderer=(doc.renderer or "markdown").strip() or "markdown",
            renderer_options=opts,
        )

    def to_profile_doc(self) -> ExportProfileDoc:
        """Serialise this spec for YAML / forms."""
        return ExportProfileDoc(
            schema_version=SCHEMA_VERSION,
            id=self.profile_id,
            name=self.name or self.profile_id,
            description=self.description,
            packaging=self.packaging,
            include=sorted(u.value for u in self.include),
            renderer=self.renderer,
            renderer_options=dict(self.renderer_options),
        )


def builtin_profiles() -> dict[str, ExportSpec]:
    """Built-in profiles (code defaults; not on disk)."""
    archive = ExportSpec(
        profile_id=DEFAULT_PROFILE_ID,
        name="Archive (full)",
        description="Official grok-trace nest plus groket run/flags/notes extras.",
        packaging=Packaging.TAR_GZ,
        include=frozenset(ARCHIVE_FULL_INCLUDE),
        renderer="markdown",
    )
    archive_org = ExportSpec(
        profile_id="archive-org",
        name="Archive (Org mode reports)",
        description="Same units as archive-full; human reports as Org mode (.org).",
        packaging=Packaging.TAR_GZ,
        include=frozenset(ARCHIVE_FULL_INCLUDE),
        renderer="org",
    )
    trace = ExportSpec(
        profile_id="trace-only",
        name="Trace only",
        description="Nested grok-trace.tar.gz only (plus readme/manifest).",
        packaging=Packaging.TAR_GZ,
        include=frozenset(TRACE_ONLY_INCLUDE),
        renderer="markdown",
    )
    return {
        archive.profile_id: archive,
        archive_org.profile_id: archive_org,
        trace.profile_id: trace,
    }


def _load_profile_yaml(path: Path) -> ExportSpec | None:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        logger.warning("skip export profile %s: %s", path, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("skip export profile %s: root must be a mapping", path)
        return None
    try:
        doc = ExportProfileDoc.model_validate(raw)
    except Exception as exc:
        logger.warning("skip export profile %s: %s", path, exc)
        return None
    return ExportSpec.from_profile_doc(doc)


def load_user_profiles(*, profiles_dir: Path | None = None) -> dict[str, ExportSpec]:
    """Load ``*.yaml`` / ``*.yml`` from the user profiles directory."""
    root = Path(profiles_dir) if profiles_dir is not None else user_export_profiles_dir()
    if not root.is_dir():
        return {}
    out: dict[str, ExportSpec] = {}
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in (".yaml", ".yml") or not path.is_file():
            continue
        spec = _load_profile_yaml(path)
        if spec is None:
            continue
        out[spec.profile_id] = spec
    return out


def list_export_profiles(*, profiles_dir: Path | None = None) -> dict[str, ExportSpec]:
    """Built-ins merged with user profiles (user wins on same id)."""
    merged = dict(builtin_profiles())
    merged.update(load_user_profiles(profiles_dir=profiles_dir))
    return merged


def get_export_profile(
    profile_id: str | None = None,
    *,
    profiles_dir: Path | None = None,
) -> ExportSpec:
    """Resolve a profile by id (default from config or :data:`DEFAULT_PROFILE_ID`)."""
    pid = (profile_id or "").strip() or default_export_profile_id()
    profiles = list_export_profiles(profiles_dir=profiles_dir)
    if pid in profiles:
        return profiles[pid]
    raise KeyError(f"unknown export profile: {pid!r} (known: {sorted(profiles)})")


def save_export_profile(
    spec: ExportSpec,
    *,
    profiles_dir: Path | None = None,
) -> Path:
    """Write *spec* as YAML under the user profiles directory.

    :returns: Path written.
    """
    root = Path(profiles_dir) if profiles_dir is not None else user_export_profiles_dir()
    root.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in spec.profile_id) or "profile"
    path = root / f"{safe}.yaml"
    doc = spec.to_profile_doc()
    payload = doc.model_dump(mode="json")
    path.write_text(
        yaml.safe_dump(payload, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return path


def configured_export_profile_id() -> str | None:
    """Return ``export.default_profile`` when set in config.toml; else ``None``.

    Distinguishes “operator chose a default” from the built-in fallback used by
    :func:`default_export_profile_id`.
    """
    from ..config import load_app_config

    raw = load_app_config().export.default_profile
    return raw or None


def default_export_profile_id() -> str:
    """``export.default_profile`` from config.toml, else :data:`DEFAULT_PROFILE_ID`."""
    return configured_export_profile_id() or DEFAULT_PROFILE_ID


def set_default_export_profile_id(profile_id: str) -> None:
    """Persist ``export.default_profile`` in config.toml."""
    from ..config import ExportPrefs, update_app_config

    update_app_config(
        export=ExportPrefs(default_profile=(profile_id or DEFAULT_PROFILE_ID).strip())
    )


__all__ = [
    "ALL_INCLUDE_UNITS",
    "ARCHIVE_FULL_INCLUDE",
    "DEFAULT_PROFILE_ID",
    "ExportProfileDoc",
    "ExportSpec",
    "IncludeUnit",
    "Packaging",
    "SCHEMA_VERSION",
    "TRACE_ONLY_INCLUDE",
    "builtin_profiles",
    "configured_export_profile_id",
    "default_export_profile_id",
    "get_export_profile",
    "list_export_profiles",
    "load_user_profiles",
    "save_export_profile",
    "set_default_export_profile_id",
    "user_export_profiles_dir",
]
