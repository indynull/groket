"""TUI chrome and help brand.

The header is one row: folders when wide, wordmark, activity.
Help has room for the three-slat small mark.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widget import Widget
from textual.widgets import Footer, Static

from .i18n import t

# Same hex as brand/build.py.
INK = "#282828"
COMPLETE = "#98971A"
FAILED = "#CC241D"
RUNNING = "#D79921"

_BAR = "█████"
_CAP = "█"
_PATHS_MIN_WIDTH = 100


def small_mark() -> Text:
    """Three equal slats with failed / complete / running tips.

    :returns: 6×3 Rich text.
    """
    out = Text()
    for i, color in enumerate((FAILED, COMPLETE, RUNNING)):
        if i:
            out.append("\n")
        out.append(_BAR, style=INK)
        out.append(_CAP, style=color)
    return out


def paths_banner(work: Path, host: Path | None = None) -> str:
    """Eval / optional Host folder line.

    :param work: Eval traces root.
    :param host: Optional host Grok sessions root.
    :returns: Rich markup for the banner Static.
    """
    eval_bit = t("chrome-folder", label=t("ui-origin-work"), path=str(work))
    if host is None:
        return f"[dim]{eval_bit}[/dim]"
    host_bit = t("chrome-folder", label=t("ui-origin-host"), path=str(host))
    return f"[dim]{eval_bit}[/dim] │ [dim]{host_bit}[/dim]"


def help_mark() -> Text:
    """Small mark for the help panel.

    :returns: Same grid as :func:`small_mark`.
    """
    return small_mark()


class AppChrome(Widget):
    """One-row bar: folders, centered wordmark, activity."""

    DEFAULT_CSS = """
    AppChrome {
        dock: top;
        width: 100%;
        height: 1;
        layout: horizontal;
        background: $panel;
        color: $text;
    }
    AppChrome #session-paths {
        width: 1fr;
        height: 1;
        padding: 0 1 0 1;
        color: $text-muted;
        text-overflow: ellipsis;
        overflow-x: hidden;
        content-align: left middle;
    }
    AppChrome .chrome-rule {
        width: 1;
        height: 1;
        color: $text-muted;
        content-align: center middle;
    }
    #app-chrome-title {
        width: auto;
        height: 1;
        padding: 0 1;
        text-style: bold;
        content-align: center middle;
    }
    AppChrome ActivityBar,
    AppChrome #activity-bar {
        dock: none;
        width: 1fr;
        height: 1;
        background: $panel;
        content-align: right middle;
        text-align: right;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        from .widgets.activity_bar import ActivityBar

        yield Static("", id="session-paths")
        yield Static("│", classes="chrome-rule")
        yield Static(t("help-brand-name"), id="app-chrome-title")
        yield Static("│", classes="chrome-rule")
        yield ActivityBar()

    def on_mount(self) -> None:
        self._sync_paths()

    def on_resize(self) -> None:
        self._sync_paths()

    def set_wordmark(self, text: str) -> None:
        """Set the centered location line (brand on home, session when open)."""
        self.query_one("#app-chrome-title", Static).update(text)

    def _sync_paths(self) -> None:
        with suppress(NoMatches):
            slot = self.query_one("#session-paths", Static)
            if self.size.width < _PATHS_MIN_WIDTH:
                slot.update("")
                return
            update = getattr(self.app, "_update_session_paths_banner", None)
            if callable(update):
                update()


class AppFooter(Footer):
    """One-row compact key rail, same panel as the header."""

    def __init__(self) -> None:
        super().__init__(compact=True)


class HelpBrand(Widget):
    """Stacked favicon plus wordmark for the help panel."""

    DEFAULT_CSS = """
    HelpBrand {
        width: 100%;
        height: auto;
        min-height: 3;
        margin: 0 0 1 0;
        padding: 0 0 1 0;
        layout: horizontal;
        border-bottom: solid $boost;
    }
    #help-brand-mark {
        width: 8;
        height: 3;
        padding: 0 2 0 0;
        content-align: left middle;
    }
    #help-brand-copy {
        width: 1fr;
        height: 3;
        content-align: left middle;
        padding: 0 0 0 1;
    }
    #help-brand-name {
        text-style: bold;
    }
    #help-brand-tagline {
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(help_mark(), id="help-brand-mark")
        with Vertical(id="help-brand-copy"):
            yield Static(t("help-brand-name"), id="help-brand-name")
            yield Static(t("help-brand-tagline"), id="help-brand-tagline")
