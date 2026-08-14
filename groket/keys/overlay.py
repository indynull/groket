"""Load optional ``keys.toml`` diffs over the catalog.

Missing file keeps catalog defaults. A bad overlay is refused in full.
Parse accepts ``leader`` / ``leader+X``; ``load_keymap`` and ``--check``
refuse those chords with ``sequence_not_wired``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from groket.keys.catalog import (
    ACTIONS,
    ACTIONS_BY_ID,
    ActionScope,
    ActionSurface,
    KeyAction,
    chord_is_reserved,
    normalize_chord,
)
from groket.models import JsonObject, JsonValue, as_json_object
from groket.paths import user_keys_path

KEYS_ENV = "GROKET_KEYS"
DEFAULT_LEADER_TIMEOUT_MS = 800

_TOP_LEADER = "leader"
_TOP_TIMEOUT = "leader_timeout_ms"
_SCOPES = {scope.value: scope for scope in ActionScope}


class OverlayErrorKind(str, Enum):
    """Why an overlay was refused."""

    INVALID_TOML = "invalid_toml"
    UNKNOWN_ID = "unknown_id"
    UNKNOWN_SCOPE = "unknown_scope"
    RESERVED_STEAL = "reserved_steal"
    CLASH = "clash"
    SEQUENCE_NOT_WIRED = "sequence_not_wired"
    INVALID_VALUE = "invalid_value"


@dataclass(frozen=True)
class OverlayError:
    """One overlay refusal.

    :ivar kind: Machine-stable error class.
    :ivar message: Operator-facing sentence.
    :ivar scope: Scope table name when known.
    :ivar action_id: Catalog id when known.
    :ivar chord: Chord that triggered the error, when known.
    """

    kind: OverlayErrorKind
    message: str
    scope: str | None = None
    action_id: str | None = None
    chord: str | None = None


@dataclass(frozen=True)
class OverlayRemap:
    """One overlay assignment (``[scope]`` / ``\"id\" = \"chord\"``)."""

    scope: ActionScope
    action_id: str
    chord: str


@dataclass(frozen=True)
class OverlayDocument:
    """Parsed overlay. Sequence chords are valid here; resolve may still fail."""

    leader: str | None
    leader_timeout_ms: int | None
    remaps: tuple[OverlayRemap, ...]
    errors: tuple[OverlayError, ...]

    @property
    def ok(self) -> bool:
        """True when the document has no parse/validation errors."""
        return not self.errors


@dataclass(frozen=True)
class ResolvedBinding:
    """One catalog row after overlay merge (or catalog default)."""

    id: str
    scope: ActionScope
    chord: str
    surfaces: ActionSurface


@dataclass(frozen=True)
class Keymap:
    """Resolved action table plus overlay status.

    :ivar bindings: Full catalog, overlay chords when the file applied.
    :ivar leader: Overlay leader key, or None.
    :ivar leader_timeout_ms: Overlay timeout, or None when no leader.
    :ivar path: Path that was consulted (``GROKET_KEYS`` or APP_HOME).
    :ivar loaded_overlay: True when a file existed and merged.
    :ivar errors: Empty when the active map is valid.
    """

    bindings: tuple[ResolvedBinding, ...]
    leader: str | None
    leader_timeout_ms: int | None
    path: Path
    loaded_overlay: bool
    errors: tuple[OverlayError, ...]

    @property
    def ok(self) -> bool:
        """True when defaults plus overlay are valid."""
        return not self.errors

    def binding(self, action_id: str) -> ResolvedBinding:
        """Return the resolved row for *action_id*.

        :param action_id: Catalog id.
        :returns: The matching row.
        :raises KeyError: If *action_id* is not in the map.
        """
        for row in self.bindings:
            if row.id == action_id:
                return row
        raise KeyError(action_id)


def resolve_keys_path() -> Path:
    """Return the overlay path: ``GROKET_KEYS`` if set, else APP_HOME/keys.toml.

    :returns: Path to consult (may not exist).
    """
    raw = (os.environ.get(KEYS_ENV) or "").strip()
    if raw:
        return Path(raw).expanduser()
    return user_keys_path()


def chord_has_sequence(chord: str) -> bool:
    """True when *chord* uses the ``leader`` token (``leader+n``, ``leader``).

    :param chord: Textual-style chord or comma-list.
    :returns: Whether any alternative is a leader sequence.
    """
    for part in chord.split(","):
        bits = [b.strip().lower() for b in part.split("+") if b.strip()]
        if "leader" in bits:
            return True
    return False


def _err(
    kind: OverlayErrorKind,
    message: str,
    *,
    scope: str | None = None,
    action_id: str | None = None,
    chord: str | None = None,
) -> OverlayError:
    return OverlayError(
        kind=kind,
        message=message,
        scope=scope,
        action_id=action_id,
        chord=chord,
    )


def _chord_parts(chord: str) -> tuple[str, ...]:
    normalized = normalize_chord(chord)
    return tuple(p for p in normalized.split(",") if p)


def _as_table(value: JsonValue) -> JsonObject | None:
    if isinstance(value, dict):
        return value
    return None


def _validate_leader(value: JsonValue) -> tuple[str | None, OverlayError | None]:
    if not isinstance(value, str) or not value.strip():
        return None, _err(
            OverlayErrorKind.INVALID_VALUE,
            "leader must be a single non-empty key",
            chord=str(value) if value is not None else None,
        )
    raw = value.strip()
    if "," in raw or chord_has_sequence(raw):
        return None, _err(
            OverlayErrorKind.INVALID_VALUE,
            "leader must be a single key",
            chord=raw,
        )
    canon = normalize_chord(raw)
    if not canon or chord_is_reserved(canon):
        return None, _err(
            OverlayErrorKind.RESERVED_STEAL,
            f"leader key {raw!r} is reserved",
            chord=raw,
        )
    return canon, None


def _validate_timeout(value: JsonValue) -> tuple[int | None, OverlayError | None]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None, _err(
            OverlayErrorKind.INVALID_VALUE,
            "leader_timeout_ms must be a positive integer",
        )
    if value <= 0:
        return None, _err(
            OverlayErrorKind.INVALID_VALUE,
            "leader_timeout_ms must be a positive integer",
        )
    return value, None


def _lookup_overlay_action(scope: ActionScope, action_id: str) -> KeyAction | OverlayError:
    row = ACTIONS_BY_ID.get(action_id)
    if row is None:
        return _err(
            OverlayErrorKind.UNKNOWN_ID,
            f"unknown action id {action_id!r} in [{scope.value}]",
            scope=scope.value,
            action_id=action_id,
        )
    if row.scope is not scope:
        return _err(
            OverlayErrorKind.UNKNOWN_ID,
            f"action {action_id!r} belongs to [{row.scope.value}], not [{scope.value}]",
            scope=scope.value,
            action_id=action_id,
        )
    return row


def _validate_remap_chord(
    scope: ActionScope, row: KeyAction, chord: JsonValue
) -> OverlayError | None:
    if not isinstance(chord, str) or not chord.strip():
        return _err(
            OverlayErrorKind.INVALID_VALUE,
            f"[{scope.value}] {row.id!r} must be a non-empty chord string",
            scope=scope.value,
            action_id=row.id,
        )
    raw = chord.strip()
    if not row.remappable:
        return _err(
            OverlayErrorKind.RESERVED_STEAL,
            f"cannot remap reserved action {row.id!r}",
            scope=scope.value,
            action_id=row.id,
            chord=raw,
        )
    if chord_is_reserved(raw) or any(chord_is_reserved(p) for p in _chord_parts(raw)):
        return _err(
            OverlayErrorKind.RESERVED_STEAL,
            f"cannot bind {row.id!r} to reserved key {raw!r}",
            scope=scope.value,
            action_id=row.id,
            chord=raw,
        )
    if not _chord_parts(raw):
        return _err(
            OverlayErrorKind.INVALID_VALUE,
            f"[{scope.value}] {row.id!r} has an empty chord",
            scope=scope.value,
            action_id=row.id,
            chord=raw,
        )
    return None


def _validate_remap(scope: ActionScope, action_id: str, chord: JsonValue) -> OverlayError | None:
    found = _lookup_overlay_action(scope, action_id)
    if isinstance(found, OverlayError):
        return found
    return _validate_remap_chord(scope, found, chord)


def _parse_scope_table(
    scope: ActionScope,
    table: JsonObject,
) -> tuple[list[OverlayRemap], list[OverlayError]]:
    remaps: list[OverlayRemap] = []
    errors: list[OverlayError] = []
    for action_id, chord in table.items():
        if isinstance(chord, dict):
            errors.append(
                _err(
                    OverlayErrorKind.UNKNOWN_ID,
                    f"unknown action id {action_id!r} in [{scope.value}] "
                    f'(quote dotted ids: "{action_id}.…")',
                    scope=scope.value,
                    action_id=action_id,
                )
            )
            continue
        problem = _validate_remap(scope, action_id, chord)
        if problem is not None:
            errors.append(problem)
            continue
        remaps.append(OverlayRemap(scope=scope, action_id=action_id, chord=str(chord).strip()))
    return remaps, errors


def parse_overlay(text: str) -> OverlayDocument:
    """Parse overlay TOML. Sequence chords are accepted.

    :param text: keys.toml contents.
    :returns: Document plus any parse/validation errors.
    """
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return OverlayDocument(
            leader=None,
            leader_timeout_ms=None,
            remaps=(),
            errors=(_err(OverlayErrorKind.INVALID_TOML, f"invalid TOML: {exc}"),),
        )
    if not isinstance(raw, dict):
        return OverlayDocument(
            leader=None,
            leader_timeout_ms=None,
            remaps=(),
            errors=(_err(OverlayErrorKind.INVALID_TOML, "invalid TOML: root must be a table"),),
        )
    return _document_from_mapping(as_json_object(raw))


def _document_from_mapping(raw: JsonObject) -> OverlayDocument:
    errors: list[OverlayError] = []
    remaps: list[OverlayRemap] = []
    leader: str | None = None
    timeout: int | None = None
    for key, value in raw.items():
        if key == _TOP_LEADER:
            parsed, err = _validate_leader(value)
            if err is not None:
                errors.append(err)
            else:
                leader = parsed
            continue
        if key == _TOP_TIMEOUT:
            parsed_t, err = _validate_timeout(value)
            if err is not None:
                errors.append(err)
            else:
                timeout = parsed_t
            continue
        scope = _SCOPES.get(key)
        if scope is None:
            errors.append(
                _err(
                    OverlayErrorKind.UNKNOWN_SCOPE,
                    f"unknown scope {key!r}",
                    scope=key,
                )
            )
            continue
        table = _as_table(value)
        if table is None:
            errors.append(
                _err(
                    OverlayErrorKind.INVALID_VALUE,
                    f"[{key}] must be a table of action id = chord",
                    scope=key,
                )
            )
            continue
        got, scope_errs = _parse_scope_table(scope, table)
        remaps.extend(got)
        errors.extend(scope_errs)
    if leader is not None and timeout is None:
        timeout = DEFAULT_LEADER_TIMEOUT_MS
    return OverlayDocument(
        leader=leader,
        leader_timeout_ms=timeout,
        remaps=tuple(remaps),
        errors=tuple(errors),
    )


def _bindings_from_actions(
    chords: dict[str, str],
) -> tuple[ResolvedBinding, ...]:
    return tuple(
        ResolvedBinding(
            id=row.id,
            scope=row.scope,
            chord=chords[row.id],
            surfaces=row.surfaces,
        )
        for row in ACTIONS
    )


def _default_chords() -> dict[str, str]:
    return {row.id: row.default for row in ACTIONS}


def default_keymap(path: Path | None = None) -> Keymap:
    """Catalog defaults with no overlay applied.

    :param path: Path that was consulted (missing or refused).
    :returns: Default keymap.
    """
    return Keymap(
        bindings=_bindings_from_actions(_default_chords()),
        leader=None,
        leader_timeout_ms=None,
        path=path if path is not None else resolve_keys_path(),
        loaded_overlay=False,
        errors=(),
    )


def _occupancy(
    chords: dict[str, str],
    rows: tuple[KeyAction, ...] = ACTIONS,
) -> dict[ActionScope, dict[str, list[str]]]:
    out: dict[ActionScope, dict[str, list[str]]] = {}
    for row in rows:
        chord = chords[row.id]
        bucket = out.setdefault(row.scope, {})
        for part in _chord_parts(chord):
            bucket.setdefault(part, []).append(row.id)
    return out


def _clash_errors(chords: dict[str, str]) -> list[OverlayError]:
    default_occ = _occupancy(_default_chords())
    resolved_occ = _occupancy(chords)
    errors: list[OverlayError] = []
    for scope, parts in resolved_occ.items():
        for part, ids in parts.items():
            if len(ids) < 2:
                continue
            default_ids = set(default_occ.get(scope, {}).get(part, []))
            extra = [i for i in ids if i not in default_ids]
            if not extra:
                continue
            listed = ", ".join(ids)
            errors.append(
                _err(
                    OverlayErrorKind.CLASH,
                    f"[{scope.value}] {part} is bound to {listed}",
                    scope=scope.value,
                    action_id=extra[0],
                    chord=part,
                )
            )
    return errors


def _sequence_errors(remaps: tuple[OverlayRemap, ...]) -> list[OverlayError]:
    errors: list[OverlayError] = []
    for remap in remaps:
        if not chord_has_sequence(remap.chord):
            continue
        errors.append(
            _err(
                OverlayErrorKind.SEQUENCE_NOT_WIRED,
                f"sequence chord {remap.chord!r} is not wired yet "
                f"({remap.action_id} in [{remap.scope.value}])",
                scope=remap.scope.value,
                action_id=remap.action_id,
                chord=remap.chord,
            )
        )
    return errors


def _merge_document(doc: OverlayDocument, path: Path) -> Keymap:
    if not doc.ok:
        failed = default_keymap(path)
        return Keymap(
            bindings=failed.bindings,
            leader=None,
            leader_timeout_ms=None,
            path=path,
            loaded_overlay=False,
            errors=doc.errors,
        )
    chords = _default_chords()
    for remap in doc.remaps:
        chords[remap.action_id] = normalize_chord(remap.chord)
    errors = (*_clash_errors(chords), *_sequence_errors(doc.remaps))
    if errors:
        failed = default_keymap(path)
        return Keymap(
            bindings=failed.bindings,
            leader=None,
            leader_timeout_ms=None,
            path=path,
            loaded_overlay=False,
            errors=tuple(errors),
        )
    return Keymap(
        bindings=_bindings_from_actions(chords),
        leader=doc.leader,
        leader_timeout_ms=doc.leader_timeout_ms,
        path=path,
        loaded_overlay=True,
        errors=(),
    )


def load_keymap(path: Path | None = None) -> Keymap:
    """Load catalog defaults plus optional ``keys.toml``.

    Missing file is defaults, not an error. Any overlay error refuses the
    whole file and returns defaults plus :attr:`Keymap.errors`.

    :param path: Overlay path; ``None`` uses :func:`resolve_keys_path`.
    :returns: Resolved map (defaults when the overlay is missing or refused).
    """
    target = path if path is not None else resolve_keys_path()
    if target.is_dir():
        return Keymap(
            bindings=default_keymap(target).bindings,
            leader=None,
            leader_timeout_ms=None,
            path=target,
            loaded_overlay=False,
            errors=(
                _err(
                    OverlayErrorKind.INVALID_TOML,
                    f"overlay path is a directory: {target}",
                ),
            ),
        )
    if not target.is_file():
        return default_keymap(target)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Keymap(
            bindings=default_keymap(target).bindings,
            leader=None,
            leader_timeout_ms=None,
            path=target,
            loaded_overlay=False,
            errors=(_err(OverlayErrorKind.INVALID_TOML, f"cannot read {target}: {exc}"),),
        )
    return _merge_document(parse_overlay(text), target)


def occupancy_rows(keymap: Keymap) -> list[tuple[str, str, str]]:
    """Taken normalized chords per scope.

    :param keymap: Resolved (or default) map.
    :returns: ``(scope, chord, comma-separated ids)`` sorted by scope then chord.
    """
    chords = {row.id: row.chord for row in keymap.bindings}
    occ = _occupancy(chords)
    rows: list[tuple[str, str, str]] = []
    for scope in ActionScope:
        parts = occ.get(scope, {})
        for part in sorted(parts):
            rows.append((scope.value, part, ", ".join(parts[part])))
    return rows


def format_keymap_table(keymap: Keymap) -> str:
    """Plain table: scope, id, chord, surface.

    :param keymap: Map to print.
    :returns: Multi-line text without a trailing newline.
    """
    header = ("scope", "id", "chord", "surface")
    cells = [(row.scope.value, row.id, row.chord, row.surfaces.value) for row in keymap.bindings]
    widths = [len(h) for h in header]
    for row in cells:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(header))]
    for row in cells:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def format_occupancy(keymap: Keymap) -> str:
    """Taken chords grouped by scope.

    :param keymap: Map to print.
    :returns: Multi-line text without a trailing newline.
    """
    rows = occupancy_rows(keymap)
    if not rows:
        return ""
    width = max(len(chord) for _, chord, _ in rows)
    lines: list[str] = []
    current = ""
    for scope, chord, ids in rows:
        if scope != current:
            if lines:
                lines.append("")
            lines.append(f"{scope}")
            current = scope
        lines.append(f"  {chord.ljust(width)}  {ids}")
    return "\n".join(lines)


def format_errors(keymap: Keymap) -> str:
    """One ``error: …`` line per overlay refusal.

    :param keymap: Map whose :attr:`Keymap.errors` to print.
    :returns: Multi-line text without a trailing newline.
    """
    return "\n".join(f"error: {err.message}" for err in keymap.errors)
