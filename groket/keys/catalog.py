"""Typed catalog of TUI and HUD key actions.

Each row is one action. Textual ``Binding.id`` and HUD ``help.rs`` push ids
use :attr:`KeyAction.id`. :attr:`KeyAction.default` is Textual notation
(``slash``, ``N``, ``left_square_bracket``); compare HUD specs with
:func:`normalize_chord`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Overlay cannot steal dismiss, activate, focus-traversal, or ``?``.
RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "escape",
        "esc",
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
    PERSONA_EDIT = "persona_edit"
    CONFIGS = "configs"
    RULES = "rules"
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
    :ivar remappable: False for reserved chords (Esc, Enter, Tab, Shift+Tab, ``?``).
    :ivar tui_action: Textual action name, or None when the TUI has no Binding.
    :ivar hud_message: Stable HUD dispatch token, or None when the HUD does not dispatch.
    """

    id: str
    scope: ActionScope
    default: str
    surfaces: ActionSurface
    remappable: bool
    tui_action: str | None
    hud_message: str | None


def _normalize_part(part: str) -> str:
    raw = part.strip()
    if not raw:
        return ""
    if "+" in raw:
        bits = [b.strip() for b in raw.split("+") if b.strip()]
        if not bits:
            return ""
        *mods, key = bits
        mods_l = [m.lower() for m in mods]
        if len(key) == 1 and key.isalpha() and key.isupper() and "shift" not in mods_l:
            mods_l.append("shift")
            key = key.lower()
        key_l = key.lower()
        key_c = _KEY_ALIASES.get(key, _KEY_ALIASES.get(key_l, key_l if key.isalpha() else key))
        return "+".join([*mods_l, key_c])
    if len(raw) == 1 and raw.isalpha() and raw.isupper():
        return f"shift+{raw.lower()}"
    low = raw.lower()
    return _KEY_ALIASES.get(raw, _KEY_ALIASES.get(low, low if raw.isalpha() else raw))


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
    return any(part.strip().lower() in RESERVED_KEYS for part in chord.split(",") if part.strip())


def _a(
    action_id: str,
    scope: ActionScope,
    default: str,
    surfaces: ActionSurface,
    *,
    tui_action: str | None = None,
    hud_message: str | None = None,
    remappable: bool = True,
) -> KeyAction:
    if chord_is_reserved(default):
        remappable = False
    return KeyAction(
        id=action_id,
        scope=scope,
        default=default,
        surfaces=surfaces,
        remappable=remappable,
        tui_action=tui_action,
        hud_message=hud_message,
    )


ACTIONS: tuple[KeyAction, ...] = (
    # Shared TUI + HUD.
    _a(
        "help.toggle",
        ActionScope.GLOBAL,
        "?",
        ActionSurface.SHARED,
        tui_action="show_help",
        hud_message="toggle_help",
    ),
    _a(
        "overlay.hide",
        ActionScope.GLOBAL,
        "escape",
        ActionSurface.SHARED,
        tui_action="go_back",
        hud_message="hide",
    ),
    _a(
        "session.open",
        ActionScope.HOME,
        "enter",
        ActionSurface.SHARED,
        tui_action="open_session",
        hud_message="activate_selected",
    ),
    _a(
        "list.down",
        ActionScope.HOME,
        "j",
        ActionSurface.SHARED,
        hud_message="noop",
    ),
    _a(
        "list.up",
        ActionScope.HOME,
        "k",
        ActionSurface.SHARED,
        hud_message="noop",
    ),
    _a(
        "search.focus",
        ActionScope.HOME,
        "slash",
        ActionSurface.SHARED,
        tui_action="search_sessions",
        hud_message="noop",
    ),
    _a(
        "edit.copy",
        ActionScope.BROWSER,
        "y",
        ActionSurface.SHARED,
        tui_action="copy_detail",
        hud_message="yank",
    ),
    _a(
        "edit.copy_chord",
        ActionScope.BROWSER,
        "ctrl+shift+c",
        ActionSurface.SHARED,
        tui_action="copy_detail",
        hud_message="yank",
    ),
    _a(
        "session.follow",
        ActionScope.HOME,
        "n",
        ActionSurface.SHARED,
        tui_action="follow_up_sessions",
        hud_message="noop",
    ),
    _a(
        "session.done",
        ActionScope.HOME,
        "e",
        ActionSurface.SHARED,
        tui_action="mark_sessions_done",
        hud_message="mark_done",
    ),
    _a(
        "pane.notes",
        ActionScope.BROWSER,
        "N",
        ActionSurface.SHARED,
        tui_action="operator_note",
        hud_message="set_tab",
    ),
    # HUD-only (Tab / Shift+Tab / Ctrl+1–5 panes; [ ] turn scope; g).
    _a(
        "pane.next",
        ActionScope.BROWSER,
        "tab",
        ActionSurface.HUD,
        hud_message="noop",
    ),
    _a(
        "pane.prev",
        ActionScope.BROWSER,
        "shift+tab",
        ActionSurface.HUD,
        hud_message="noop",
    ),
    *(
        _a(
            f"pane.{i}",
            ActionScope.BROWSER,
            f"ctrl+{i}",
            ActionSurface.HUD,
            hud_message="set_tab",
        )
        for i in range(1, 6)
    ),
    _a(
        "events.next_turn",
        ActionScope.BROWSER,
        "right_square_bracket",
        ActionSurface.HUD,
        hud_message="noop",
    ),
    _a(
        "events.all_turns",
        ActionScope.BROWSER,
        "left_square_bracket",
        ActionSurface.HUD,
        hud_message="noop",
    ),
    _a(
        "turns.timeline",
        ActionScope.BROWSER,
        "g",
        ActionSurface.HUD,
        hud_message="noop",
    ),
    # TUI chrome (every screen).
    _a(
        "app.refresh",
        ActionScope.GLOBAL,
        "f5,ctrl+r",
        ActionSurface.TUI,
        tui_action="refresh_context",
    ),
    _a(
        "app.jobs",
        ActionScope.GLOBAL,
        "J",
        ActionSurface.TUI,
        tui_action="open_jobs",
    ),
    _a(
        "app.self_test",
        ActionScope.GLOBAL,
        "ctrl+t",
        ActionSurface.TUI,
        tui_action="self_test",
    ),
    _a(
        "app.quit",
        ActionScope.GLOBAL,
        "q",
        ActionSurface.TUI,
        tui_action="quit",
    ),
    # TUI pane digits / [ ] (not HUD pane.* / events.*).
    _a(
        "app.pane.prev",
        ActionScope.BROWSER,
        "left_square_bracket",
        ActionSurface.TUI,
        tui_action="tab_prev",
    ),
    _a(
        "app.pane.next",
        ActionScope.BROWSER,
        "right_square_bracket",
        ActionSurface.TUI,
        tui_action="tab_next",
    ),
    *(
        _a(
            f"app.pane.{i}",
            ActionScope.BROWSER,
            str(i),
            ActionSurface.TUI,
            tui_action=f"tab_pane_{i}",
        )
        for i in range(1, 10)
    ),
    # Sessions home.
    _a(
        "list.select",
        ActionScope.HOME,
        "s,space",
        ActionSurface.TUI,
        tui_action="toggle_select",
    ),
    _a(
        "list.select_all",
        ActionScope.HOME,
        "S",
        ActionSurface.TUI,
        tui_action="select_all",
    ),
    _a(
        "home.runner",
        ActionScope.HOME,
        "r",
        ActionSurface.TUI,
        tui_action="open_runner",
    ),
    _a(
        "home.configs",
        ActionScope.HOME,
        "C",
        ActionSurface.TUI,
        tui_action="open_run_configs",
    ),
    _a(
        "home.personas",
        ActionScope.HOME,
        "P",
        ActionSurface.TUI,
        tui_action="open_personas",
    ),
    _a(
        "session.rerun",
        ActionScope.HOME,
        "R",
        ActionSurface.TUI,
        tui_action="rerun_session",
    ),
    _a(
        "session.resume",
        ActionScope.HOME,
        "f",
        ActionSurface.TUI,
        tui_action="resume_session",
    ),
    _a(
        "session.save_config",
        ActionScope.HOME,
        "ctrl+s",
        ActionSurface.TUI,
        tui_action="save_session_config",
    ),
    _a(
        "session.delete",
        ActionScope.HOME,
        "x,delete",
        ActionSurface.TUI,
        tui_action="delete_sessions",
    ),
    _a(
        "home.model_filter",
        ActionScope.HOME,
        "m",
        ActionSurface.TUI,
        tui_action="cycle_model_filter",
    ),
    _a(
        "session.analyze",
        ActionScope.HOME,
        "a",
        ActionSurface.TUI,
        tui_action="analyze",
    ),
    _a(
        "home.rules",
        ActionScope.HOME,
        "d",
        ActionSurface.TUI,
        tui_action="open_rules",
    ),
    _a(
        "session.export",
        ActionScope.HOME,
        "E",
        ActionSurface.TUI,
        tui_action="export_session_bundle",
    ),
    _a(
        "home.host_show",
        ActionScope.HOME,
        "H",
        ActionSurface.TUI,
        tui_action="show_host_sessions",
    ),
    _a(
        "home.host_hide",
        ActionScope.HOME,
        "H",
        ActionSurface.TUI,
        tui_action="hide_host_sessions",
    ),
    # Session browser.
    _a(
        "browser.view_filter",
        ActionScope.BROWSER,
        "v",
        ActionSurface.TUI,
        tui_action="focus_timeline_filter",
    ),
    _a(
        "event.flag",
        ActionScope.BROWSER,
        "f",
        ActionSurface.TUI,
        tui_action="flag_event",
    ),
    _a(
        "session.note_edit",
        ActionScope.BROWSER,
        "O",
        ActionSurface.TUI,
        tui_action="edit_operator_note",
    ),
    _a(
        "browser.clear_filters",
        ActionScope.BROWSER,
        "c",
        ActionSurface.TUI,
        tui_action="clear_filters",
    ),
    _a(
        "browser.findings",
        ActionScope.BROWSER,
        "i",
        ActionSurface.TUI,
        tui_action="tab_pane_4",
    ),
    _a(
        "session.share",
        ActionScope.BROWSER,
        "s",
        ActionSurface.TUI,
        tui_action="open_share",
    ),
    # Runner.
    _a(
        "runner.launch",
        ActionScope.RUNNER,
        "ctrl+enter,ctrl+j",
        ActionSurface.TUI,
        tui_action="run_evaluation",
    ),
    _a(
        "edit.save",
        ActionScope.MODAL,
        "ctrl+s",
        ActionSurface.TUI,
        tui_action="save",
    ),
    _a(
        "runner.export_task",
        ActionScope.RUNNER,
        "T",
        ActionSurface.TUI,
        tui_action="export_task_yaml",
    ),
    _a(
        "runner.new_persona",
        ActionScope.RUNNER,
        "n",
        ActionSurface.TUI,
        tui_action="new_persona_from_runner",
    ),
    _a(
        "runner.personas",
        ActionScope.RUNNER,
        "p",
        ActionSurface.TUI,
        tui_action="open_persona_builder",
    ),
    _a(
        "runner.docker",
        ActionScope.RUNNER,
        "d",
        ActionSurface.TUI,
        tui_action="check_docker",
    ),
    # Run configs.
    _a(
        "configs.launch",
        ActionScope.CONFIGS,
        "l",
        ActionSurface.TUI,
        tui_action="launch_config",
    ),
    _a(
        "configs.launch_selected",
        ActionScope.CONFIGS,
        "w",
        ActionSurface.TUI,
        tui_action="launch_selected",
    ),
    _a(
        "configs.delete",
        ActionScope.CONFIGS,
        "x",
        ActionSurface.TUI,
        tui_action="delete_config",
    ),
    _a(
        "configs.new",
        ActionScope.CONFIGS,
        "n",
        ActionSurface.TUI,
        tui_action="new_blank",
    ),
    # Personas.
    _a(
        "personas.new",
        ActionScope.PERSONAS,
        "n",
        ActionSurface.TUI,
        tui_action="new_persona",
    ),
    _a(
        "personas.edit",
        ActionScope.PERSONAS,
        "e",
        ActionSurface.TUI,
        tui_action="edit_persona",
    ),
    _a(
        "personas.delete",
        ActionScope.PERSONAS,
        "x,delete",
        ActionSurface.TUI,
        tui_action="delete_persona",
    ),
    # Rules.
    _a(
        "rules.toggle",
        ActionScope.RULES,
        "t",
        ActionSurface.TUI,
        tui_action="toggle_rule",
    ),
    _a(
        "rules.enable_all",
        ActionScope.RULES,
        "a",
        ActionSurface.TUI,
        tui_action="enable_all",
    ),
    _a(
        "rules.disable_all",
        ActionScope.RULES,
        "A",
        ActionSurface.TUI,
        tui_action="disable_all",
    ),
    # Jobs modal.
    _a(
        "jobs.close",
        ActionScope.JOBS,
        "J",
        ActionSurface.TUI,
        tui_action="dismiss_modal",
    ),
    _a(
        "jobs.open_alt",
        ActionScope.JOBS,
        "o",
        ActionSurface.TUI,
        tui_action="open_session",
    ),
    _a(
        "jobs.clear_logs",
        ActionScope.JOBS,
        "c",
        ActionSurface.TUI,
        tui_action="clear_logs",
    ),
    # Generic modal / picker.
    _a(
        "modal.done",
        ActionScope.MODAL,
        "ctrl+s",
        ActionSurface.TUI,
        tui_action="done",
    ),
    _a(
        "modal.submit",
        ActionScope.MODAL,
        "ctrl+r",
        ActionSurface.TUI,
        tui_action="submit",
    ),
    _a(
        "modal.submit_enter",
        ActionScope.MODAL,
        "enter",
        ActionSurface.TUI,
        tui_action="submit",
    ),
    _a(
        "mcp.registry",
        ActionScope.MODAL,
        "r",
        ActionSurface.TUI,
        tui_action="registry_mode",
    ),
    _a(
        "mcp.local",
        ActionScope.MODAL,
        "l",
        ActionSurface.TUI,
        tui_action="local_mode",
    ),
    _a(
        "confirm.discard",
        ActionScope.MODAL,
        "enter,y",
        ActionSurface.TUI,
        tui_action="discard",
    ),
    _a(
        "confirm.keep",
        ActionScope.MODAL,
        "n",
        ActionSurface.TUI,
        tui_action="keep",
    ),
    _a(
        "help.dismiss",
        ActionScope.MODAL,
        "enter",
        ActionSurface.TUI,
        tui_action="dismiss",
    ),
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
