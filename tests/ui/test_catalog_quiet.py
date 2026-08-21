"""TUI attach list: quiet poll does not re-drain or rebuild on unchanged revision."""

from __future__ import annotations

from pathlib import Path

from groket.models import SessionMeta
from groket.ui.app import TraceEvalApp


def test_importing_trace_eval_app_does_not_import_scoring_packages() -> None:
    """Cold ``import groket.ui.app`` must not pull deleted scoring packages."""
    import sys

    ui_app = sys.modules.pop("groket.ui.app", None)
    try:
        import groket.ui.app as app_mod

        assert "groket.analysis" not in sys.modules
        assert "groket.engine" not in sys.modules
        assert "groket.analyzer" not in sys.modules
        assert app_mod.TraceEvalApp is not None
    finally:
        if ui_app is not None:
            sys.modules["groket.ui.app"] = ui_app


def test_tui_on_mount_does_not_construct_scoring_services() -> None:
    """Opening the TUI must not construct deleted scoring services."""
    source = Path(__file__).resolve().parents[2] / "groket" / "ui" / "app.py"
    text = source.read_text(encoding="utf-8")
    chunk = text.split("class TraceEvalApp", 1)[1]
    on_mount = chunk.split("def on_mount(self)", 1)[1].split("\n    def ", 1)[0]
    assert "AnalysisService(" not in on_mount
    assert "set_analysis_service" not in on_mount
    assert "load_config_plugins" not in on_mount
    assert "RulesScreen" not in on_mount


def test_first_control_catalog_paint_requests_a_page() -> None:
    """Initial attach paint must request one page, not drain matched."""
    from groket.session.access import DEFAULT_SESSION_LIST_LIMIT
    from groket.ui.app import first_home_list_fetch

    fetch = first_home_list_fetch()
    assert fetch["drain"] is False
    assert int(fetch["limit"]) == DEFAULT_SESSION_LIST_LIMIT
    assert int(fetch["offset"]) == 0
    assert int(fetch["since_revision"]) == 0


def test_initial_control_load_fetches_first_page_only(tmp_path: Path, monkeypatch) -> None:
    """First attach paint must not drain matched before the table is shown."""
    from groket.session.access import DEFAULT_SESSION_LIST_LIMIT

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = tmp_path / "control.sock"
    app = TraceEvalApp(
        work_dir=work,
        traces_path=traces,
        control_socket=sock,
        control_attach_only=True,
    )
    fetches: list[dict[str, object]] = []

    def fake_fetch(
        *,
        query: str = "",
        since_revision: int = 0,
        drain: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, object]:
        fetches.append(
            {
                "drain": drain,
                "limit": limit,
                "offset": offset,
                "since_revision": since_revision,
            }
        )
        row = {
            "sessionId": "one",
            "path": str(traces / "one"),
            "title": "One",
            "label": "One",
            "origin": "work",
        }
        if drain:
            return {
                "sessions": [row],
                "total": 1,
                "matched": 1,
                "revision": 3,
                "unchanged": False,
                "removed": [],
                "delta": False,
            }
        return {
            "sessions": [row],
            "total": 1,
            "matched": 1,
            "revision": 3,
            "unchanged": False,
            "removed": [],
            "delta": False,
            "incomplete": False,
        }

    monkeypatch.setattr(app, "_fetch_control_catalog_sync", fake_fetch)
    monkeypatch.setattr("groket.ui.app.call_ui", lambda *_a, **_k: None)
    gen = app._begin_sessions_load()
    app._load_sessions_via_control(gen, quiet=False)
    assert fetches
    assert fetches[0]["drain"] is False
    assert fetches[0]["limit"] == DEFAULT_SESSION_LIST_LIMIT
    assert fetches[0]["offset"] == 0
    assert not any(f.get("drain") is True for f in fetches)


def test_first_attach_does_not_drain_when_matched_exceeds_page(tmp_path: Path, monkeypatch) -> None:
    """First paint stays on one page even when ``matched`` is larger."""
    from groket.session.access import DEFAULT_SESSION_LIST_LIMIT

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = tmp_path / "control.sock"
    app = TraceEvalApp(
        work_dir=work,
        traces_path=traces,
        control_socket=sock,
        control_attach_only=True,
    )
    fetches: list[dict[str, object]] = []
    page_rows = [
        {
            "sessionId": f"s{i}",
            "path": str(traces / f"s{i}"),
            "title": f"S{i}",
            "label": f"S{i}",
            "origin": "work",
        }
        for i in range(3)
    ]

    def fake_fetch(
        *,
        query: str = "",
        since_revision: int = 0,
        drain: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, object]:
        fetches.append({"drain": drain, "limit": limit, "offset": offset})
        if drain:
            return {
                "sessions": page_rows + page_rows,
                "total": 450,
                "matched": 450,
                "revision": 4,
                "unchanged": False,
                "removed": [],
                "delta": False,
            }
        return {
            "sessions": page_rows,
            "total": 450,
            "matched": 450,
            "revision": 4,
            "unchanged": False,
            "removed": [],
            "delta": False,
            "incomplete": False,
        }

    monkeypatch.setattr(app, "_fetch_control_catalog_sync", fake_fetch)
    monkeypatch.setattr("groket.ui.app.call_ui", lambda *_a, **_k: None)
    gen = app._begin_sessions_load()
    app._load_sessions_via_control(gen, quiet=False)
    assert fetches == [
        {"drain": False, "limit": DEFAULT_SESSION_LIST_LIMIT, "offset": 0},
    ]
    ids = {meta.session_id for meta, _label in app._meta_only}
    assert ids == {"s0", "s1", "s2"}


def test_first_attach_fills_later_pages_without_drain(tmp_path: Path, monkeypatch) -> None:
    """After first paint, remaining pages load with offset; never drain."""
    from groket.session.access import DEFAULT_SESSION_LIST_LIMIT

    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = tmp_path / "control.sock"
    app = TraceEvalApp(
        work_dir=work,
        traces_path=traces,
        control_socket=sock,
        control_attach_only=True,
    )
    page = int(DEFAULT_SESSION_LIST_LIMIT)
    matched = page + 50
    fetches: list[dict[str, object]] = []

    def row(i: int) -> dict[str, object]:
        return {
            "sessionId": f"p{i}",
            "path": str(traces / f"p{i}"),
            "title": f"P{i}",
            "label": f"P{i}",
            "origin": "work",
        }

    def fake_fetch(
        *,
        query: str = "",
        since_revision: int = 0,
        drain: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, object]:
        fetches.append({"drain": drain, "limit": limit, "offset": offset})
        start = int(offset)
        stop = start + int(limit or page)
        rows = [row(i) for i in range(start, min(stop, matched))]
        return {
            "sessions": rows,
            "total": matched,
            "matched": matched,
            "revision": 5,
            "unchanged": False,
            "removed": [],
            "delta": False,
            "incomplete": False,
        }

    monkeypatch.setattr(app, "_fetch_control_catalog_sync", fake_fetch)
    monkeypatch.setattr("groket.ui.app.call_ui", lambda *_a, **_k: None)
    gen = app._begin_sessions_load()
    app._load_sessions_via_control(gen, quiet=False)
    assert not any(f.get("drain") is True for f in fetches)
    assert fetches[0] == {
        "drain": False,
        "limit": page,
        "offset": 0,
    }
    assert {"drain": False, "limit": page, "offset": page} in fetches
    ids = {meta.session_id for meta, _label in app._meta_only}
    assert len(ids) == matched
    assert "p0" in ids
    assert f"p{matched - 1}" in ids


def test_incomplete_first_page_replaced_when_scan_finishes(tmp_path: Path, monkeypatch) -> None:
    """Cold attach paints immediately, then applies the finished snapshot."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = tmp_path / "control.sock"
    app = TraceEvalApp(
        work_dir=work,
        traces_path=traces,
        control_socket=sock,
        control_attach_only=True,
    )
    fetches: list[str] = []
    row = {
        "sessionId": "ready-1",
        "path": str(traces / "ready-1"),
        "title": "Ready",
        "label": "Ready",
        "origin": "work",
    }

    def fake_fetch(
        *,
        query: str = "",
        since_revision: int = 0,
        drain: bool = True,
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, object]:
        _ = (query, since_revision, drain, limit, offset)
        fetches.append("hit")
        if len(fetches) == 1:
            return {
                "sessions": [],
                "total": 0,
                "matched": 0,
                "revision": 0,
                "unchanged": False,
                "removed": [],
                "delta": False,
                "incomplete": True,
                "building": True,
            }
        return {
            "sessions": [row],
            "total": 1,
            "matched": 1,
            "revision": 11,
            "unchanged": False,
            "removed": [],
            "delta": False,
            "incomplete": False,
            "building": False,
        }

    monkeypatch.setattr(app, "_fetch_control_catalog_sync", fake_fetch)
    monkeypatch.setattr("groket.ui.app.call_ui", lambda *_a, **_k: None)
    monkeypatch.setattr("groket.ui.app.time.sleep", lambda _s: None)
    gen = app._begin_sessions_load()
    app._load_sessions_via_control(gen, quiet=False)
    assert len(fetches) >= 2
    ids = {meta.session_id for meta, _label in app._meta_only}
    assert ids == {"ready-1"}
    assert app._catalog_revision == 11


def test_browser_screen_init_sets_pending_and_live_attrs(tmp_path: Path) -> None:
    """BrowserScreen.__init__ must finish (check_action / pending bar need these)."""
    from groket.session.context_samples import ContextSampleStore
    from groket.ui.screens.browser import BrowserScreen

    sess = tmp_path / "sess-init"
    sess.mkdir()
    screen = BrowserScreen(sess)
    assert screen._pending_cache_valid is False
    assert screen._pending_actions_enabled is False
    assert screen._needs_live_timeline is False
    assert screen._needs_live_timeline_valid is False
    assert screen._detail_debounce is None
    assert screen._live_refresh_deferred is None
    assert isinstance(screen._context_samples, ContextSampleStore)
    assert screen._session_is_pending() is False


def test_load_sessions_is_threaded_worker() -> None:
    source = Path(__file__).resolve().parents[2] / "groket" / "ui" / "app.py"
    text = source.read_text(encoding="utf-8")
    assert '@work(thread=True, exclusive=True, group="sessions-catalog")' in text
    assert "def _load_sessions(" in text
    assert "def _fetch_control_catalog_sync(" in text


def test_quiet_unchanged_catalog_skips_table_rebuild(tmp_path: Path, monkeypatch) -> None:
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = tmp_path / "control.sock"
    app = TraceEvalApp(
        work_dir=work,
        traces_path=traces,
        control_socket=sock,
        control_attach_only=True,
    )
    keep = SessionMeta(session_id="keep", session_dir=traces / "keep", title="Keep")
    app._meta_only = [(keep, "Keep")]
    app._catalog_revision = 7
    fetches: list[dict[str, object]] = []
    ui: list[str] = []

    def fake_fetch(
        *,
        query: str = "",
        since_revision: int = 0,
        drain: bool = True,
    ) -> dict[str, object]:
        fetches.append({"query": query, "since_revision": since_revision, "drain": drain})
        return {
            "sessions": [],
            "total": 1,
            "matched": 1,
            "revision": 7,
            "unchanged": True,
            "removed": [],
            "delta": True,
        }

    monkeypatch.setattr(app, "_fetch_control_catalog_sync", fake_fetch)
    monkeypatch.setattr(
        "groket.ui.app.call_ui",
        lambda _app, cb, *a, **k: ui.append(getattr(cb, "__name__", str(cb))),
    )
    gen = app._begin_sessions_load()
    app._load_sessions_via_control(gen, quiet=True)
    assert fetches == [{"query": "", "since_revision": 7, "drain": False}]
    assert app._meta_only[0][0].session_id == "keep"
    assert " _populate_session_table" not in ui
    assert all("populate" not in name for name in ui)


def test_quiet_poll_drains_when_owner_returns_full_page(tmp_path: Path, monkeypatch) -> None:
    """After serve restart the owner cannot delta; quiet poll must drain."""
    work = tmp_path / "work"
    traces = work / "runs" / "traces"
    traces.mkdir(parents=True)
    sock = tmp_path / "control.sock"
    app = TraceEvalApp(
        work_dir=work,
        traces_path=traces,
        control_socket=sock,
        control_attach_only=True,
    )
    app._meta_only = [
        (SessionMeta(session_id="keep", session_dir=traces / "keep", title="Keep"), "Keep")
    ]
    app._catalog_revision = 1
    fetches: list[dict[str, object]] = []

    def fake_fetch(
        *,
        query: str = "",
        since_revision: int = 0,
        drain: bool = True,
    ) -> dict[str, object]:
        fetches.append({"since_revision": since_revision, "drain": drain})
        rows = [
            {
                "sessionId": "keep",
                "path": str(traces / "keep"),
                "title": "Keep",
                "label": "Keep",
                "origin": "work",
            },
            {
                "sessionId": "gamma",
                "path": str(traces / "gamma"),
                "title": "Gamma",
                "label": "Gamma",
                "origin": "work",
            },
        ]
        if drain:
            return {
                "sessions": rows,
                "total": 2,
                "matched": 2,
                "revision": 99,
                "unchanged": False,
                "removed": [],
                "delta": False,
            }
        return {
            "sessions": rows[:1],
            "total": 2,
            "matched": 2,
            "revision": 99,
            "unchanged": False,
            "removed": [],
            "delta": False,
        }

    monkeypatch.setattr(app, "_fetch_control_catalog_sync", fake_fetch)
    monkeypatch.setattr("groket.ui.app.call_ui", lambda *_a, **_k: None)
    gen = app._begin_sessions_load()
    app._load_sessions_via_control(gen, quiet=True)
    assert fetches[0] == {"since_revision": 1, "drain": False}
    assert any(f.get("drain") is True for f in fetches)
    ids = {meta.session_id for meta, _label in app._meta_only}
    assert ids == {"keep", "gamma"}
    assert app._catalog_revision == 99
