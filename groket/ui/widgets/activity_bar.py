"""One-line activity strip (top right): run lifecycle + sessions catalog."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from ..i18n import t
from ..styles import status_rich_style, theme_is_light

logger = logging.getLogger(__name__)

# Hold a non-zero lifecycle count this long after it drops to 0 so brief status
# gaps (container exit vs meta update) do not flash Running ↔ Awaiting.
_COUNT_DROP_HOLD_S = 0.75
_STABLE_COUNT_KEYS = (
    "pending",
    "building",
    "running",
    "ending",
    "extracting",
    "awaiting",
    "sessions",
)

# Order and styling for in-flight container/session phases (Jobs status column).
# Spinner frames when the phase is actively working (not waiting on the operator).
_ACTIVITY_PHASES: tuple[tuple[str, str, bool], ...] = (
    ("pending", "activity-pending", True),
    ("building", "activity-building", True),
    ("running", "activity-running", True),
    ("ending", "activity-ending", True),
    ("extracting", "activity-extracting", True),
    ("awaiting", "activity-awaiting", False),
)


def build_activity_line(
    *,
    pending: int = 0,
    building: int = 0,
    running: int = 0,
    ending: int = 0,
    extracting: int = 0,
    awaiting: int = 0,
    refresh_active: int = 0,
    sessions_loaded: int = 0,
    spinner: str = "",
    light: bool = False,
) -> Text:
    """Right-aligned strip using Jobs/session vocabulary.

    Phases with count > 0 (same labels as the Jobs status column)::

        Building │ Running │ Ending │ Extracting │ Awaiting │ Analysis │ Sessions

    * **Pending / Building / Running / Extracting** — eval containers in that phase.
    * **Ending** — interactive sessions shutting down after Done / last turn.
    * **Awaiting** — interactive sessions waiting for a follow-up prompt.
    * **Analysis / Refresh** — background pools (only when inflight > 0).
    * **Sessions** — rows loaded on the home list (always shown; catalog size).

    Active work phases use a braille *spinner* prefix when *spinner* is set.
    """
    idle = status_rich_style("idle", light=light)
    counts = {
        "pending": pending,
        "building": building,
        "running": running,
        "ending": ending,
        "extracting": extracting,
        "awaiting": awaiting,
    }
    line = Text()
    first = True

    def _sep() -> None:
        nonlocal first
        if not first:
            line.append(" │ ", style="dim")
        first = False

    for phase, msg_id, use_spin in _ACTIVITY_PHASES:
        n = int(counts.get(phase, 0) or 0)
        if n <= 0:
            continue
        _sep()
        style_key = "pending" if phase == "pending" else phase
        if phase == "pending":
            style_key = "building"
        prefix = f"{spinner} " if (use_spin and spinner) else ""
        line.append(prefix + t(msg_id, n=n), style=status_rich_style(style_key, light=light))

    # Intentional: omit short-lived live-refresh pool counts. FS-watch scans
    # pulse inflight every tick and flashed cyan "Refresh N" beside Running.

    _sep()
    line.append(t("activity-sessions", n=sessions_loaded), style=idle)
    return line


def stabilize_activity_counts(
    raw: dict[str, int],
    *,
    prev: dict[str, int] | None,
    hold_until: dict[str, float] | None,
    now: float | None = None,
    hold_s: float = _COUNT_DROP_HOLD_S,
) -> tuple[dict[str, int], dict[str, float]]:
    """Debounce drops to zero for lifecycle keys; return (display, hold_until)."""
    ts = time.monotonic() if now is None else float(now)
    prev_counts = dict(prev or {})
    holds = dict(hold_until or {})
    out = dict(raw)
    for key in _STABLE_COUNT_KEYS:
        cur = int(raw.get(key, 0) or 0)
        was = int(prev_counts.get(key, 0) or 0)
        if cur > 0:
            holds.pop(key, None)
            out[key] = cur
        elif was > 0:
            until = holds.get(key)
            if until is None:
                holds[key] = ts + hold_s
                out[key] = was
            elif ts < until:
                out[key] = was
            else:
                holds.pop(key, None)
                out[key] = 0
        else:
            holds.pop(key, None)
            out[key] = 0
    out["refresh"] = 0
    return out, holds


def activity_line_signature(counts: dict[str, int]) -> tuple[int, ...]:
    """Stable identity for the strip ignoring spinner frames."""
    return tuple(int(counts.get(k, 0) or 0) for k in _STABLE_COUNT_KEYS)


if TYPE_CHECKING:
    from textual.app import App


def _status_counts_from_run_manager(rm: object) -> dict[str, int]:
    """Tally container phases from active launches (or a status_counts() helper)."""
    fn = getattr(rm, "active_status_counts", None)
    if callable(fn):
        raw = fn()
        if isinstance(raw, dict):
            return {str(k).lower(): int(v or 0) for k, v in raw.items()}
    # Fallback: walk BackgroundRun.statuses when the helper is absent (tests).
    out: dict[str, int] = {}
    active = getattr(rm, "list_active", None)
    runs = active() if callable(active) else []
    for bg in runs or []:
        statuses = getattr(bg, "statuses", None) or {}
        if statuses:
            for st in statuses.values():
                key = str(getattr(st, "status", None) or "pending").lower()
                out[key] = out.get(key, 0) + 1
        else:
            configs = getattr(bg, "configs", None) or []
            n = len(configs) if configs else 1
            out["pending"] = out.get("pending", 0) + n
    return out


def activity_counters_from_app(app: App) -> dict[str, int]:
    """Lifecycle and catalog counters for the activity bar.

    Keys: ``pending``, ``building``, ``running``, ``ending``, ``extracting``,
    ``awaiting``, ``refresh``, ``sessions``.
    """
    counts: dict[str, int] = {
        "pending": 0,
        "building": 0,
        "running": 0,
        "ending": 0,
        "extracting": 0,
        "awaiting": 0,
        "refresh": 0,
        "sessions": 0,
    }
    rm = getattr(app, "run_manager", None)
    if rm is not None:
        for key, n in _status_counts_from_run_manager(rm).items():
            if key in counts:
                counts[key] = max(counts[key], int(n or 0))
            elif key in ("completed", "failed", "idle", "awaiting_follow_up"):
                if key == "awaiting_follow_up":
                    counts["awaiting"] = counts["awaiting"] + int(n or 0)
                continue
            else:
                # Unknown non-terminal phase → pending (not running) to avoid
                # yellow/cyan flicker from mis-mapped statuses.
                counts["pending"] = counts["pending"] + int(n or 0)

    # Sessions home Turn column (running / ending / awaiting) — authoritative
    # for interactive wait so we do not flash Running from stale launch statuses.
    meta_only = getattr(app, "_meta_only", None) or []
    meta_running = 0
    meta_ending = 0
    meta_awaiting = 0
    for item in meta_only:
        meta = item[0] if isinstance(item, tuple) and item else item
        label_fn = getattr(meta, "list_status_label", None)
        if callable(label_fn):
            st = label_fn()
            if st == "running":
                meta_running += 1
            elif st == "ending":
                meta_ending += 1
            elif st == "awaiting":
                meta_awaiting += 1
        elif getattr(meta, "turn_in_progress", False):
            meta_running += 1

    launch_active = (
        counts["pending"] + counts["building"] + counts["running"] + counts["extracting"]
    )
    if meta_awaiting:
        counts["awaiting"] = max(counts["awaiting"], meta_awaiting)
    if meta_ending:
        counts["ending"] = max(counts["ending"], meta_ending)
    if launch_active == 0:
        counts["running"] = meta_running
    elif meta_only and meta_running == 0 and (meta_awaiting > 0 or meta_ending > 0):
        # List shows only awaiting/ending/complete: suppress ghost Running from
        # in-flight statuses while the operator is in follow-up wait or shutdown.
        counts["running"] = 0

    # Never surface refresh pool pulses in the strip (see build_activity_line).
    counts["refresh"] = 0
    counts["sessions"] = len(meta_only) if hasattr(meta_only, "__len__") else 0
    return counts


def activity_is_busy(counts: dict[str, int]) -> bool:
    """True when a *short* spinner phase is active (fast poll).

    Do **not** treat ``running`` alone as busy — live evals stay running for
    minutes and an 80–500ms activity-bar timer reflow freezes the TUI.
    """
    return any(int(counts.get(k, 0) or 0) > 0 for k in ("pending", "building", "extracting"))


class ActivityBar(Static):
    """Right side of the one-row chrome: run lifecycle + catalog count."""

    DEFAULT_CSS = """
    ActivityBar {
        dock: none;
        height: 1;
        width: 1fr;
        background: $panel;
        color: $text;
        padding: 0 1;
        content-align: right middle;
        text-align: right;
    }
    """

    def __init__(self) -> None:
        super().__init__("", id="activity-bar")
        self._timer: Timer | None = None
        self._busy_timer: Timer | None = None
        self._display_counts: dict[str, int] = {}
        self._hold_until: dict[str, float] = {}
        self._last_signature: tuple[int, ...] | None = None
        self._last_spinner: str = ""

    def on_mount(self) -> None:
        from ...constants import ACTIVITY_BAR_INTERVAL

        self._timer = self.set_interval(ACTIVITY_BAR_INTERVAL, self.refresh_activity)
        self.refresh_activity()

    def on_unmount(self) -> None:
        for attr in ("_timer", "_busy_timer"):
            timer = getattr(self, attr, None)
            setattr(self, attr, None)
            if timer is not None:
                timer.stop()

    def _ensure_busy_timer(self, busy: bool) -> None:
        """Poll fast while runs/pools are busy so the spinner is smooth."""
        from ...constants import ACTIVITY_BAR_BUSY_INTERVAL

        if busy:
            if self._busy_timer is None:
                self._busy_timer = self.set_interval(
                    ACTIVITY_BAR_BUSY_INTERVAL, self.refresh_activity
                )
        elif self._busy_timer is not None:
            self._busy_timer.stop()
            self._busy_timer = None

    def refresh_activity(self) -> None:
        try:
            from ...job_pools import get_activity_log

            raw = activity_counters_from_app(self.app)
            counts, self._hold_until = stabilize_activity_counts(
                raw,
                prev=self._display_counts,
                hold_until=self._hold_until,
            )
            self._display_counts = dict(counts)
            busy = activity_is_busy(counts)
            self._ensure_busy_timer(busy)
            spin = get_activity_log().spinner_frame() if busy else ""
            sig = activity_line_signature(counts)
            if sig == self._last_signature and spin == self._last_spinner:
                return
            self._last_signature = sig
            self._last_spinner = spin
            self.update(
                build_activity_line(
                    pending=counts["pending"],
                    building=counts["building"],
                    running=counts["running"],
                    ending=counts["ending"],
                    extracting=counts["extracting"],
                    awaiting=counts["awaiting"],
                    refresh_active=0,
                    sessions_loaded=counts["sessions"],
                    spinner=spin,
                    light=theme_is_light(str(self.app.theme or "")),
                )
            )
        except Exception:
            logger.exception("activity bar refresh failed")
