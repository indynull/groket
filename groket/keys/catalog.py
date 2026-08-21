"""Typed catalog of TUI and HUD key actions.

Each row is one action. Textual ``Binding.id`` and HUD ``help.rs`` push ids
use :attr:`KeyAction.id`. :attr:`KeyAction.default` is Textual notation
(``slash``, ``N``, ``left_square_bracket``); compare HUD specs with
:func:`normalize_chord`. Overlay merge uses id, scope, default, and
remappable only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Overlay cannot steal dismiss, activate, focus-traversal, or ``?``.
# Canonical Textual names only; ``esc`` becomes ``escape`` in normalize.
RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "escape",
        "enter",
        "tab",
        "shift+tab",
        "?",
    }
)

_KEY_ALIASES: dict[str, str] = {
    "/": "slash",
    "[": "left_square_bracket",
    "]": "right_square_bracket",
    "esc": "escape",
}


class ActionScope(str, Enum):
    """Screen family for an action (overlay table name, not a user mode)."""

    GLOBAL = "global"
    HOME = "home"
    BROWSER = "browser"
    RUNNER = "runner"
    PERSONAS = "personas"
    CONFIGS = "configs"
    JOBS = "jobs"
    MODAL = "modal"


class ActionSurface(str, Enum):
    """Which product surfaces expose the action."""

    SHARED = "shared"
    TUI = "tui"
    HUD = "hud"


@dataclass(frozen=True)
class KeyAction:
    """One key action shared by the TUI, the HUD, or both.

    :ivar id: Dotted id (HUD-shaped); Textual ``Binding.id`` uses this value.
    :ivar scope: Screen family the default lives in.
    :ivar default: Textual notation; comma-list for alternatives.
    :ivar surfaces: ``shared``, ``tui``, or ``hud``.
    :ivar remappable: False when :func:`chord_is_reserved` is true for *default*.
    :ivar overlay_scopes: Overlay tables that may remap this id (includes *scope*).
    """

    id: str
    scope: ActionScope
    default: str
    surfaces: ActionSurface
    remappable: bool
    overlay_scopes: frozenset[ActionScope] = field(default_factory=frozenset)


def _alias(token: str) -> str:
    low = token.lower()
    return _KEY_ALIASES.get(token, _KEY_ALIASES.get(low, low if token.isalpha() else token))


def _normalize_part(part: str) -> str:
    raw = part.strip()
    if not raw:
        return ""
    if "+" not in raw:
        if len(raw) == 1 and raw.isalpha() and raw.isupper():
            return f"shift+{raw.lower()}"
        return _alias(raw)
    bits = [b.strip() for b in raw.split("+") if b.strip()]
    if not bits:
        return ""
    *mods, key = bits
    mods_l = [m.lower() for m in mods]
    if len(key) == 1 and key.isalpha() and key.isupper() and "shift" not in mods_l:
        mods_l.append("shift")
        key = key.lower()
    return "+".join([*mods_l, _alias(key)])


def normalize_chord(chord: str) -> str:
    """Canonical Textual notation for one chord or a comma-list.

    HUD specs use ``/``, ``shift+n``, and ``[``; catalog defaults use
    ``slash``, ``N``, and ``left_square_bracket``. Overlay merge compares
    these forms after this function.

    :param chord: Textual or HUD chord (comma-list allowed).
    :returns: Canonical comma-list.
    """
    parts = [_normalize_part(p) for p in chord.split(",")]
    return ",".join(p for p in parts if p)


def chord_is_reserved(chord: str) -> bool:
    """True when *chord* is Esc, Enter, Tab, Shift+Tab, or ``?``.

    Alternatives (``ctrl+enter,ctrl+j``) are reserved only when a bare
    reserved key appears as one of the comma-separated parts.

    :param chord: Textual-style chord or comma-list.
    :returns: Whether the chord is reserved.
    """
    return any(part in RESERVED_KEYS for part in normalize_chord(chord).split(",") if part)


def _row(
    action_id: str,
    scope: ActionScope,
    default: str,
    surfaces: ActionSurface,
    *,
    overlay_scopes: frozenset[ActionScope] | None = None,
) -> KeyAction:
    scopes = frozenset({scope}) if overlay_scopes is None else frozenset(overlay_scopes) | {scope}
    return KeyAction(
        id=action_id,
        scope=scope,
        default=default,
        surfaces=surfaces,
        remappable=not chord_is_reserved(default),
        overlay_scopes=scopes,
    )


_NAV = frozenset({ActionScope.HOME, ActionScope.BROWSER})

ACTIONS: tuple[KeyAction, ...] = (
    # Shared TUI + HUD.
    _row("help.toggle", ActionScope.GLOBAL, "?", ActionSurface.SHARED),
    _row("overlay.hide", ActionScope.GLOBAL, "escape", ActionSurface.SHARED),
    _row("session.open", ActionScope.HOME, "enter", ActionSurface.SHARED),
    _row("list.down", ActionScope.HOME, "j", ActionSurface.SHARED, overlay_scopes=_NAV),
    _row("list.up", ActionScope.HOME, "k", ActionSurface.SHARED, overlay_scopes=_NAV),
    _row(
        "search.focus",
        ActionScope.HOME,
        "slash",
        ActionSurface.SHARED,
        overlay_scopes=frozenset({ActionScope.HOME}),
    ),
    _row("edit.copy", ActionScope.BROWSER, "y", ActionSurface.SHARED),
    _row("edit.copy_chord", ActionScope.BROWSER, "ctrl+shift+c", ActionSurface.SHARED),
    _row(
        "session.follow",
        ActionScope.HOME,
        "n",
        ActionSurface.SHARED,
        overlay_scopes=_NAV,
    ),
    _row(
        "session.done",
        ActionScope.HOME,
        "e",
        ActionSurface.SHARED,
        overlay_scopes=_NAV,
    ),
    _row("pane.notes", ActionScope.BROWSER, "N", ActionSurface.SHARED),
    _row("events.prev_turn", ActionScope.BROWSER, "h,left", ActionSurface.SHARED),
    _row("events.next_turn", ActionScope.BROWSER, "l,right", ActionSurface.SHARED),
    # HUD-only (Tab / Shift+Tab / Ctrl+1–5 panes; [ ] turn scope; g).
    _row("pane.next", ActionScope.BROWSER, "tab", ActionSurface.HUD),
    _row("pane.prev", ActionScope.BROWSER, "shift+tab", ActionSurface.HUD),
    *(_row(f"pane.{i}", ActionScope.BROWSER, f"ctrl+{i}", ActionSurface.HUD) for i in range(1, 6)),
    _row("events.all_turns", ActionScope.BROWSER, "left_square_bracket", ActionSurface.HUD),
    _row("events.scope_next", ActionScope.BROWSER, "right_square_bracket", ActionSurface.HUD),
    _row("turns.timeline", ActionScope.BROWSER, "g", ActionSurface.HUD),
    _row("sessions.home", ActionScope.HOME, "u", ActionSurface.HUD),
    # TUI chrome (every screen).
    _row("app.refresh", ActionScope.GLOBAL, "f5,ctrl+r", ActionSurface.TUI),
    _row("app.jobs", ActionScope.GLOBAL, "J", ActionSurface.TUI),
    _row("app.self_test", ActionScope.GLOBAL, "ctrl+t", ActionSurface.TUI),
    _row("app.quit", ActionScope.GLOBAL, "q", ActionSurface.TUI),
    # TUI pane digits / [ ] (not HUD pane.* / events.*).
    _row("app.pane.prev", ActionScope.BROWSER, "left_square_bracket", ActionSurface.TUI),
    _row("app.pane.next", ActionScope.BROWSER, "right_square_bracket", ActionSurface.TUI),
    *(_row(f"app.pane.{i}", ActionScope.BROWSER, str(i), ActionSurface.TUI) for i in range(1, 10)),
    # Sessions home.
    _row("list.select", ActionScope.HOME, "s,space", ActionSurface.TUI),
    _row("list.select_all", ActionScope.HOME, "S", ActionSurface.TUI),
    _row("home.runner", ActionScope.HOME, "r", ActionSurface.TUI),
    _row("home.configs", ActionScope.HOME, "C", ActionSurface.TUI),
    _row("home.personas", ActionScope.HOME, "P", ActionSurface.TUI),
    _row("session.rerun", ActionScope.HOME, "R", ActionSurface.TUI),
    _row("session.resume", ActionScope.HOME, "f", ActionSurface.TUI),
    _row("session.save_config", ActionScope.HOME, "ctrl+s", ActionSurface.TUI),
    _row("session.delete", ActionScope.HOME, "x,delete", ActionSurface.TUI),
    _row("home.model_filter", ActionScope.HOME, "m", ActionSurface.TUI),
    _row(
        "session.export",
        ActionScope.HOME,
        "E",
        ActionSurface.TUI,
        overlay_scopes=frozenset({ActionScope.HOME, ActionScope.BROWSER}),
    ),
    _row("home.host", ActionScope.HOME, "H", ActionSurface.TUI),
    # Session browser.
    _row("browser.view_filter", ActionScope.BROWSER, "v", ActionSurface.TUI),
    _row("browser.event_reader", ActionScope.BROWSER, "enter", ActionSurface.TUI),
    _row("event.flag", ActionScope.BROWSER, "f", ActionSurface.TUI),
    _row("session.note_edit", ActionScope.BROWSER, "O", ActionSurface.TUI),
    _row("browser.clear_filters", ActionScope.BROWSER, "c", ActionSurface.TUI),
    _row("session.share", ActionScope.BROWSER, "s", ActionSurface.TUI),
    # Runner.
    _row("runner.launch", ActionScope.RUNNER, "ctrl+enter,ctrl+j", ActionSurface.TUI),
    _row("edit.save", ActionScope.MODAL, "ctrl+s", ActionSurface.TUI),
    _row("runner.export_task", ActionScope.RUNNER, "T", ActionSurface.TUI),
    _row("runner.new_persona", ActionScope.RUNNER, "n", ActionSurface.TUI),
    _row("runner.personas", ActionScope.RUNNER, "p", ActionSurface.TUI),
    _row("runner.docker", ActionScope.RUNNER, "d", ActionSurface.TUI),
    # Run configs.
    _row("configs.open", ActionScope.CONFIGS, "enter", ActionSurface.TUI),
    _row("configs.launch", ActionScope.CONFIGS, "l", ActionSurface.TUI),
    _row("configs.launch_selected", ActionScope.CONFIGS, "w", ActionSurface.TUI),
    _row("configs.delete", ActionScope.CONFIGS, "x", ActionSurface.TUI),
    _row("configs.new", ActionScope.CONFIGS, "n", ActionSurface.TUI),
    # Personas.
    _row("personas.open", ActionScope.PERSONAS, "enter", ActionSurface.TUI),
    _row("personas.new", ActionScope.PERSONAS, "n", ActionSurface.TUI),
    _row("personas.edit", ActionScope.PERSONAS, "e", ActionSurface.TUI),
    _row("personas.delete", ActionScope.PERSONAS, "x,delete", ActionSurface.TUI),
    # Jobs modal.
    _row("jobs.close", ActionScope.JOBS, "J", ActionSurface.TUI),
    _row("jobs.open", ActionScope.JOBS, "o", ActionSurface.TUI),
    _row("jobs.clear_logs", ActionScope.JOBS, "c", ActionSurface.TUI),
    # Generic modal / picker.
    _row("modal.submit", ActionScope.MODAL, "ctrl+r", ActionSurface.TUI),
    _row("modal.submit_enter", ActionScope.MODAL, "enter", ActionSurface.TUI),
    _row("mcp.registry", ActionScope.MODAL, "r", ActionSurface.TUI),
    _row("mcp.local", ActionScope.MODAL, "l", ActionSurface.TUI),
    _row("confirm.discard", ActionScope.MODAL, "enter,y", ActionSurface.TUI),
    _row("confirm.keep", ActionScope.MODAL, "escape,n", ActionSurface.TUI),
    _row("help.dismiss", ActionScope.MODAL, "enter", ActionSurface.TUI),
)


def _index_by_id(rows: tuple[KeyAction, ...]) -> dict[str, KeyAction]:
    out: dict[str, KeyAction] = {}
    dups: list[str] = []
    for row in rows:
        if row.id in out:
            dups.append(row.id)
        out[row.id] = row
    if dups:
        raise ValueError(f"duplicate catalog ids: {', '.join(dups)}")
    return out


ACTIONS_BY_ID: dict[str, KeyAction] = _index_by_id(ACTIONS)


def action_by_id(action_id: str) -> KeyAction:
    """Return the catalog row for *action_id*.

    :param action_id: Dotted id (``session.follow``, ``help.toggle``, …).
    :returns: The matching row.
    :raises KeyError: If *action_id* is not in the catalog.
    """
    return ACTIONS_BY_ID[action_id]
