"""Shared key action catalog for TUI bindings and HUD help rows."""

from __future__ import annotations

from .catalog import (
    ACTIONS,
    ACTIONS_BY_ID,
    RESERVED_KEYS,
    ActionScope,
    ActionSurface,
    KeyAction,
    action_by_id,
    chord_is_reserved,
    normalize_chord,
)

__all__ = [
    "ACTIONS",
    "ACTIONS_BY_ID",
    "RESERVED_KEYS",
    "ActionScope",
    "ActionSurface",
    "KeyAction",
    "action_by_id",
    "chord_is_reserved",
    "normalize_chord",
]
