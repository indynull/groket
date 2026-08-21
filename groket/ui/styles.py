"""Semantic style constants for Rich markup and Text objects.

All Python-side color choices live here.  Screens and widgets import from
this module instead of hardcoding color names.  The TCSS layer (``app.tcss``)
uses Textual ``$`` design tokens and is maintained separately.

One-off structural markup (``[dim]``, ``[bold]``) is fine inline — only
*semantic concepts* that repeat across files belong in this module.
"""

from __future__ import annotations

from contextlib import suppress

from textual.app import App

from ..tool_display import format_tool_display, tool_family

# Brand hex (same as brand/build.py). Caps = complete / failed / running.
COMPLETE = "#98971A"
FAILED = "#CC241D"
RUNNING = "#D79921"
CANCELLED = "#928374"
CREAM = "#FBF1C7"
# Olive / gold / gray that hold 4.5:1 on cream paper.
COMPLETE_ON_LIGHT = "#5C5B12"
RUNNING_ON_LIGHT = "#7A5410"
CANCELLED_ON_LIGHT = "#6B6358"
INK_ON_LIGHT = "#3C3836"

SEVERITY_STYLE: dict[str, str] = {
    "high": f"{FAILED} bold",
    "medium": f"{RUNNING} bold",
    "low": RUNNING,
}

SEVERITY_LABEL: dict[str, str] = {
    "high": f"[{FAILED} bold]High[/]",
    "medium": f"[{RUNNING} bold]Medium[/]",
    "low": f"[{RUNNING}]Low[/]",
}


# Small palette by *role* (not a rainbow per label):
#   cream  = human input / model stream
#   complete green = tools / writes
#   running yellow = session runtime
#   failed red = error

# Grok sessionUpdate / events.jsonl type → Rich style (identity keys).
EVENT_TYPE_STYLE: dict[str, str] = {
    "user_message_chunk": f"bold {CREAM}",
    "agent_message_chunk": CREAM,
    "agent_thought_chunk": f"dim {CREAM} italic",
    "plan": CREAM,
    "tool_call": f"bold {COMPLETE}",
    "tool_call_update": f"dim {COMPLETE}",
    "task_backgrounded": f"bold {RUNNING}",
    "task_completed": RUNNING,
    "scheduled_task_created": RUNNING,
    "scheduled_task_updated": RUNNING,
    "scheduled_task_fired": RUNNING,
    "scheduled_task_deleted": RUNNING,
    "turn_completed": RUNNING,
    "subagent_spawned": CREAM,
    "subagent_finished": CREAM,
    "current_mode_update": f"dim {RUNNING}",
    "retry_state": f"dim {RUNNING}",
    "goal_updated": RUNNING,
    "session_recap": RUNNING,
    "auto_compact_started": RUNNING,
    "auto_compact_completed": RUNNING,
    "compaction_checkpoint": RUNNING,
    "hook_execution": RUNNING,
    "hook_annotation": RUNNING,
    "turn_started": RUNNING,
    "turn_ended": RUNNING,
    "session_error": f"bold {FAILED}",
    "error": f"bold {FAILED}",
    "turn_error": f"bold {FAILED}",
    "fatal_error": f"bold {FAILED}",
    "system": CANCELLED,
    # Short event-type names stored in traces.
    "user": f"bold {CREAM}",
    "assistant": CREAM,
    "thought": f"dim {CREAM} italic",
    "tool_result": f"dim {COMPLETE}",
    "subagent": CREAM,
    "session": RUNNING,
}

# Type column uses Grok identifiers (spaces from underscores in type_label).
EVENT_TYPE_LABEL: dict[str, str] = {
    k: f"[{v}]{k.replace('_', ' ')}[/]" for k, v in EVENT_TYPE_STYLE.items()
}

EVENT_TYPE_STYLE_LIGHT: dict[str, str] = {
    k: v.replace(CREAM, INK_ON_LIGHT) for k, v in EVENT_TYPE_STYLE.items()
}

# Color by *action family*, not per-tool identity (keeps the column scannable):
#   cream  = read / search / inspect
#   complete green = write / edit / mutate workspace
#   running yellow = shell / process / wait
#   cream  = agent / plan
#   cancelled gray = marketplace

TOOL_FAMILY_STYLE: dict[str, str] = {
    "read": CREAM,
    "write": COMPLETE,
    "shell": RUNNING,
    "agent": CREAM,
    "mcp": CANCELLED,
    "other": "dim",
}

TOOL_FAMILY_STYLE_LIGHT: dict[str, str] = {
    k: (INK_ON_LIGHT if v == CREAM else v) for k, v in TOOL_FAMILY_STYLE.items()
}


# Run / container lifecycle — one palette for tables, activity bar, labels.
STATUS_RICH_STYLE: dict[str, str] = {
    "pending": "dim",
    "building": f"bold {RUNNING}",
    "running": f"bold {RUNNING}",
    "ending": f"bold {CANCELLED}",
    "awaiting": f"bold {CANCELLED}",
    "extracting": f"bold {RUNNING}",
    "completed": f"bold {COMPLETE}",
    "failed": f"bold {FAILED}",
    "idle": "dim",
}

STATUS_RICH_STYLE_LIGHT: dict[str, str] = {
    "pending": "dim",
    "building": f"bold {RUNNING_ON_LIGHT}",
    "running": f"bold {RUNNING_ON_LIGHT}",
    "ending": f"bold {CANCELLED_ON_LIGHT}",
    "awaiting": f"bold {CANCELLED_ON_LIGHT}",
    "extracting": f"bold {RUNNING_ON_LIGHT}",
    "completed": f"bold {COMPLETE_ON_LIGHT}",
    "failed": f"bold {FAILED}",
    "idle": "dim",
}


def theme_is_light(name: str) -> bool:
    """True when a Textual theme name is a light paper colorway."""
    n = (name or "").strip().lower()
    return any(tok in n for tok in ("light", "latte", "dawn"))


def active_theme_is_light() -> bool:
    """True when the running Textual app is on a light paper theme."""
    with suppress(Exception):
        app = getattr(App, "get_running_app", lambda: None)()
        if app is not None:
            return theme_is_light(getattr(app, "theme", "") or "")
    return False


def event_type_markup(event_type: str, *, light: bool = False) -> str:
    """Styled Type-column label, or empty when *event_type* has no palette entry."""
    styles = EVENT_TYPE_STYLE_LIGHT if light else EVENT_TYPE_STYLE
    style = styles.get(event_type)
    if not style:
        return ""
    return f"[{style}]{event_type.replace('_', ' ')}[/]"


STATUS_LABEL: dict[str, str] = {
    "pending": f"[{STATUS_RICH_STYLE['pending']}]Pending[/]",
    "building": f"[{STATUS_RICH_STYLE['building']}]Building…[/]",
    "running": f"[{STATUS_RICH_STYLE['running']}]Running…[/]",
    "ending": f"[{STATUS_RICH_STYLE['ending']}]Ending…[/]",
    "extracting": f"[{STATUS_RICH_STYLE['extracting']}]Extracting…[/]",
    "completed": f"[{STATUS_RICH_STYLE['completed']}]Completed[/]",
    "failed": f"[{STATUS_RICH_STYLE['failed']}]Failed[/]",
}


def status_rich_style(status: str, *, light: bool = False) -> str:
    """Rich style for a container/run status name (``running``, ``failed``, …).

    :param light: Use darker brand inks that hold contrast on cream paper.
    """
    table = STATUS_RICH_STYLE_LIGHT if light else STATUS_RICH_STYLE
    return table.get((status or "").strip().lower(), table["idle"])


SYNTAX_THEME_LIGHT = "friendly"
SYNTAX_THEME_DARK = "monokai"

# Textual theme name substring → Pygments style (no code-block background).
_SYNTAX_BY_THEME: tuple[tuple[str, str], ...] = (
    ("solarized-light", "solarized-light"),
    ("solarized", "solarized-dark"),
    ("gruvbox", "gruvbox-dark"),
    ("nord", "nord"),
    ("groket-light", "gruvbox-light"),
    ("groket", "gruvbox-dark"),
    ("textual-light", SYNTAX_THEME_LIGHT),
    ("catppuccin", "dracula"),
)


def syntax_theme_for_app(app: App) -> str:
    """Pick a Pygments style that follows the active Textual theme name."""
    with suppress(Exception):
        name = (getattr(app, "theme", "") or "").lower()
        for needle, pygments in _SYNTAX_BY_THEME:
            if needle in name:
                return pygments
        if "light" in name:
            return SYNTAX_THEME_LIGHT
    return SYNTAX_THEME_DARK


def severity_style(value: str) -> str:
    """Rich style string for a severity value (``"high"`` / ``"medium"`` / ``"low"``)."""
    return SEVERITY_STYLE.get(value, "white")


def tool_style(name: str, *, light: bool = False) -> str:
    """Rich style for a tool name (family palette)."""
    table = TOOL_FAMILY_STYLE_LIGHT if light else TOOL_FAMILY_STYLE
    return table.get(tool_family(name or ""), table["other"])


def tool_label(name: str, *, max_len: int = 32, light: bool = False) -> str:
    """Rich markup label for a tool name in tables (MCP shown as server · method)."""
    display = format_tool_display(name or "?")
    if len(display) > max_len:
        display = display[: max_len - 1] + "…"
    style = tool_style(name, light=light)
    safe = display.replace("[", "\\[").replace("]", "\\]")
    return f"[{style}]{safe}[/]"
