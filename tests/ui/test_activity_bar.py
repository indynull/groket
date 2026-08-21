"""Activity bar line builder and app counter reads."""

from __future__ import annotations

from types import SimpleNamespace

from groket.ui.widgets.activity_bar import (
    activity_counters_from_app,
    activity_is_busy,
    activity_line_signature,
    build_activity_line,
    stabilize_activity_counts,
)


def test_build_activity_line_idle():
    text = build_activity_line(sessions_loaded=5)
    plain = text.plain
    assert "Sessions 5" in plain
    assert "Building" not in plain
    assert "Running" not in plain
    assert "Live" not in plain
    assert "Lib" not in plain
    assert "Analysis" not in plain


def test_build_activity_line_lifecycle_and_spinner():
    from groket.ui.styles import status_rich_style

    text = build_activity_line(
        building=1,
        running=2,
        extracting=1,
        awaiting=1,
        sessions_loaded=10,
        spinner="⠋",
    )
    plain = text.plain
    assert "Building 1" in plain
    assert "Running 2" in plain
    assert "Extracting 1" in plain
    assert "Awaiting 1" in plain
    assert "Analysis" not in plain
    assert "Sessions 10" in plain
    assert "│" in plain
    assert "⠋" in plain
    # Awaiting does not get a spinner prefix (operator wait).
    assert "⠋ Awaiting" not in plain
    assert "⠋ Building" in plain or plain.index("⠋") < plain.index("Building")
    assert status_rich_style("running") == "bold #D79921"
    styles = {str(span.style) for span in text.spans}
    assert any("D79921" in s for s in styles)
    light = build_activity_line(running=1, sessions_loaded=1, light=True)
    light_styles = {str(span.style) for span in light.spans}
    assert any("7A5410" in s for s in light_styles)
    assert not any("cyan" in s for s in styles)


def test_activity_is_busy():
    assert not activity_is_busy({"sessions": 3, "awaiting": 1})
    assert activity_is_busy({"building": 1, "sessions": 0})
    # Live "running" alone must not enable the fast spinner timer (UI thrash).
    assert not activity_is_busy({"running": 2})
    assert not activity_is_busy({"ending": 1})
    assert not activity_is_busy({"analyze": 1})
    assert activity_is_busy({"extracting": 1})


def test_build_activity_line_ending():
    from groket.ui.styles import status_rich_style

    text = build_activity_line(ending=2, sessions_loaded=4, spinner="⠋")
    plain = text.plain
    assert "Ending 2" in plain
    assert "⠋" in plain
    assert status_rich_style("ending") == "bold #928374"


def test_activity_counters_meta_ending():
    meta_ending = SimpleNamespace(list_status_label=lambda: "ending")
    app = SimpleNamespace(
        run_manager=SimpleNamespace(active_status_counts=lambda: {"running": 1}),
        _meta_only=[(meta_ending, "x")],
    )
    counts = activity_counters_from_app(app)
    assert counts["ending"] == 1
    assert counts["running"] == 0


def test_activity_counters_from_app_status_counts():
    st_build = SimpleNamespace(status="building")
    st_run = SimpleNamespace(status="running")
    bg = SimpleNamespace(
        is_running=True,
        statuses={"a": st_build, "b": st_run},
        configs=[1, 2],
    )
    rm = SimpleNamespace(
        active_status_counts=lambda: {"building": 1, "running": 1},
        list_active=lambda: [bg],
    )
    meta_await = SimpleNamespace(list_status_label=lambda: "awaiting")
    meta_done = SimpleNamespace(list_status_label=lambda: "complete")
    app = SimpleNamespace(
        run_manager=rm,
        _meta_only=[(meta_await, "x"), (meta_done, "y")],
    )
    counts = activity_counters_from_app(app)
    assert counts["building"] == 1
    # List is awaiting-only → suppress ghost Running from launch statuses.
    assert counts["running"] == 0
    assert counts["awaiting"] == 1
    assert "analyze" not in counts
    assert counts["sessions"] == 2
    assert counts["refresh"] == 0


def test_activity_counters_meta_running_when_no_docker():
    meta_running = SimpleNamespace(list_status_label=lambda: "running")
    app = SimpleNamespace(
        run_manager=SimpleNamespace(active_status_counts=lambda: {}),
        _meta_only=[(meta_running, "x")],
    )
    counts = activity_counters_from_app(app)
    assert counts["running"] == 1
    assert counts["sessions"] == 1


def test_activity_counters_fallback_walk_statuses():
    """When active_status_counts is missing, walk list_active statuses."""
    bg = SimpleNamespace(
        statuses={"c": SimpleNamespace(status="extracting")},
        configs=[],
    )
    rm = SimpleNamespace(list_active=lambda: [bg])
    app = SimpleNamespace(run_manager=rm, _meta_only=[])
    counts = activity_counters_from_app(app)
    assert counts["extracting"] == 1


def test_activity_counters_suppress_ghost_running_when_only_awaiting():
    """Stale launch statuses must not flash Running beside list Awaiting."""
    rm = SimpleNamespace(active_status_counts=lambda: {"running": 2})
    meta_await = SimpleNamespace(list_status_label=lambda: "awaiting")
    app = SimpleNamespace(
        run_manager=rm,
        _meta_only=[(meta_await, "a"), (meta_await, "b")],
    )
    counts = activity_counters_from_app(app)
    assert counts["running"] == 0
    assert counts["awaiting"] == 2
    assert counts["refresh"] == 0


def test_activity_counters_unknown_status_is_pending_not_running():
    rm = SimpleNamespace(active_status_counts=lambda: {"weird_phase": 1})
    app = SimpleNamespace(run_manager=rm, _meta_only=[])
    counts = activity_counters_from_app(app)
    assert counts["pending"] == 1
    assert counts["running"] == 0


def test_stabilize_activity_counts_holds_drop():
    prev = {"running": 2, "awaiting": 0, "sessions": 3}
    raw = {"running": 0, "awaiting": 2, "sessions": 3}
    held, holds = stabilize_activity_counts(raw, prev=prev, hold_until={}, now=100.0, hold_s=0.75)
    assert held["running"] == 2
    assert held["awaiting"] == 2
    assert "running" in holds
    cleared, holds2 = stabilize_activity_counts(
        raw, prev=held, hold_until=holds, now=100.8, hold_s=0.75
    )
    assert cleared["running"] == 0
    assert cleared["awaiting"] == 2
    assert "running" not in holds2


def test_build_activity_line_omits_refresh():
    text = build_activity_line(running=1, refresh_active=3, sessions_loaded=1)
    assert "Refresh" not in text.plain
    assert "Running 1" in text.plain


def test_activity_line_signature_ignores_refresh_key():
    a = activity_line_signature({"running": 1, "sessions": 2, "refresh": 9})
    b = activity_line_signature({"running": 1, "sessions": 2, "refresh": 0})
    assert a == b


def test_activity_is_busy_ignores_refresh_key():
    assert not activity_is_busy({"refresh": 3, "sessions": 1, "awaiting": 1})
