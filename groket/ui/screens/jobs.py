"""Background jobs + container logs modal (opt-in; runner keeps the main UI quiet)."""

from __future__ import annotations

import copy
from contextlib import suppress
from pathlib import Path

from rich.markup import escape as rich_escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, RichLog, Static, TabbedContent, TabPane

from ...docker.orchestrator import ContainerStatus
from ...runs.run_manager import BackgroundRun, RunManager
from ...utils import fmt_duration as _format_duration
from ...utils import widget_id
from .. import text as U
from ..bindings import JOBS_MODAL, focus_primary_list, notify_help
from ..data_table import style_data_table
from ..i18n import join_ui, t
from ..quit_actions import QuitActions
from ..tab_panes import TabPaneNavigation


class JobsModal(TabPaneNavigation, QuitActions, ModalScreen[None]):
    TAB_CONTENT_ID = "jobs-tabs"
    TAB_PANES = (
        ("jobs-tab-status", "#jobs-status-table"),
        ("jobs-tab-activity", "#jobs-activity-log"),
        ("jobs-tab-logs", "#jobs-logs-all"),
    )

    """Modal: Jobs, Activity (heavy pools), and Logs."""

    BINDINGS = list(JOBS_MODAL)
    _CONTAINER_COLORS = ["cyan", "green", "yellow", "magenta", "blue", "red"]

    class LogLine(Message):
        def __init__(self, container_name: str, line: str) -> None:
            super().__init__()
            self.container_name = container_name
            self.line = line

    class StatusUpdate(Message):
        def __init__(self, status: ContainerStatus) -> None:
            super().__init__()
            self.status = status

    class RunFinished(Message):
        def __init__(self, run: BackgroundRun) -> None:
            super().__init__()
            self.run = run

    def __init__(self, run_manager: RunManager, work_dir: Path | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.run_manager = run_manager
        self.work_dir = work_dir
        self._subscribed = False
        self._container_color_map: dict[str, str] = {}
        self._log_tabs: set[str] = set()
        self._log_buffer: dict[str, list[Text]] = {}
        self._tabs_mounted: set[str] = set()
        self._known_rows: set[str] = set()
        self._hydrating = False
        self._activity_seq = -1
        self._control_log_sig: tuple[str, int, int] = ("", 0, 0)
        # Byte offset in the detached serve log after Clear (only show newer lines).
        self._control_log_skip_bytes = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="jobs-modal"):
            yield Static(id="jobs-modal-title")
            with TabbedContent(id="jobs-tabs"):
                with TabPane(U.jobs_tab(), id="jobs-tab-status"):
                    yield Static("", id="jobs-app-status")
                    yield Label(f"[bold]{U.runs_label()}[/bold]")
                    yield DataTable(id="jobs-status-table")
                    yield Label(f"[dim]{U.history_label()}[/dim]")
                    yield DataTable(id="jobs-history-table")
                    yield Static(
                        t("ui-interactive-follow-ups-open-the-session-in-the-b"),
                        id="jobs-follow-hint",
                    )
                with TabPane(t("jobs-activity-tab"), id="jobs-tab-activity"):
                    yield Static(t("jobs-activity-help"), id="jobs-activity-help")
                    yield RichLog(id="jobs-activity-log", wrap=True, max_lines=500)
                with TabPane(U.logs_tab(), id="jobs-tab-logs"):
                    with TabbedContent(id="jobs-logs-tabs"):
                        with TabPane(U.all_tab(), id="jobs-log-tab-all"):
                            yield RichLog(id="jobs-logs-all", wrap=True, max_lines=4000)
            with Horizontal(id="jobs-modal-actions", classes="modal-footer"):
                yield Button(U.refresh_btn(), id="jobs-refresh-btn")
                yield Button(U.clear_logs_btn(), id="jobs-clear-btn")
                yield Button(U.close_btn(), variant="primary", id="jobs-close-btn")

    def on_mount(self) -> None:
        with suppress(Exception):
            # Markup on Static (not Text()); Fluent value stays plain.
            title = t("jobs")
            self.query_one("#jobs-modal-title", Static).update("[bold]" + title + "[/bold]")
        st = self.query_one("#jobs-status-table", DataTable)
        style_data_table(st)
        st.add_columns(
            t("ui-container"),
            t("ui-model"),
            t("ui-status"),
            t("ui-session-2"),
            t("ui-share"),
            t("ui-run-1"),
            t("ui-started"),
            t("ui-finished"),
        )
        ht = self.query_one("#jobs-history-table", DataTable)
        style_data_table(ht)
        ht.add_columns(
            t("ui-run-1"), t("ui-status"), t("ui-containers"), t("ui-elapsed"), t("ui-error-3")
        )
        self._activity_seq = -1
        self._control_log_sig = ("", 0, 0)
        self._subscribe()
        self._hydrate_from_manager()
        self._refresh_app_jobs()
        self._refresh_activity_log()
        self.set_interval(0.25, self._refresh_activity_log)
        focus_primary_list(st)

    def on_unmount(self) -> None:
        self._unsubscribe()

    def _control_log_path(self) -> Path | None:
        """Detached ``groket serve`` log next to the control socket, if any."""
        sock = getattr(self.app, "_control_socket", None)
        if sock is None:
            return None
        try:
            from ...integrations.daemon import control_log_path

            path = control_log_path(Path(sock))
        except Exception:
            return None
        return path if path.is_file() else None

    def _control_log_signature(self) -> tuple[str, int, int]:
        """``(path, mtime_ns, size)`` for the serve log, or empty."""
        path = self._control_log_path()
        if path is None:
            return ("", 0, 0)
        try:
            st = path.stat()
            return (
                str(path),
                int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
                int(st.st_size),
            )
        except OSError:
            return ("", 0, 0)

    def _read_control_log_tail(self, *, max_lines: int = 120) -> list[str]:
        """Lines from the serve log after the last Clear offset."""
        path = self._control_log_path()
        if path is None:
            return []
        try:
            data = path.read_bytes()
        except OSError:
            return []
        skip = max(0, int(self._control_log_skip_bytes or 0))
        if skip >= len(data):
            return []
        text = data[skip:].decode("utf-8", errors="replace")
        lines = text.splitlines()
        if max_lines > 0:
            return lines[-max_lines:]
        return lines

    def _refresh_activity_header(self) -> None:
        """Inflight counts on the Activity help line (cheap; every tick)."""
        from ...job_pools import (
            get_activity_log,
            get_live_refresh_pool,
            refresh_inflight,
        )

        log = get_activity_log()
        try:
            help_w = self.query_one("#jobs-activity-help", Static)
        except Exception:
            return
        ctrl = self._control_log_path()
        base = t(
            "jobs-activity-status",
            refresh=refresh_inflight(),
            refresh_workers=get_live_refresh_pool().max_workers,
            spin=log.spinner_frame() if refresh_inflight() else "",
        )
        if ctrl is not None:
            help_w.update(join_ui(base, t("jobs-activity-control-path", path=str(ctrl)), sep="\n"))
        else:
            help_w.update(join_ui(base, t("jobs-activity-no-control"), sep="\n"))

    def _refresh_activity_log(self) -> None:
        """Paint TUI pool ActivityLog + optional serve log tail into Activity."""
        from datetime import datetime

        from ...job_pools import get_activity_log

        log = get_activity_log()
        seq = log.seq
        ctrl_sig = self._control_log_signature()
        self._refresh_activity_header()
        if seq == self._activity_seq and ctrl_sig == self._control_log_sig:
            return
        self._activity_seq = seq
        self._control_log_sig = ctrl_sig
        try:
            rich = self.query_one("#jobs-activity-log", RichLog)
        except Exception:
            return
        rich.clear()
        for entry in log.snapshot(limit=200):
            ts = datetime.fromtimestamp(entry.ts).strftime("%H:%M:%S")
            kind = entry.kind
            style = {
                "analysis": "cyan",
                "refresh": "yellow",
                "system": "dim",
                "control": "magenta",
            }.get(kind, "")
            line = Text()
            line.append(f"{ts} ", style="dim")
            line.append(f"[{kind}] ", style=style or "bold")
            line.append(entry.message)
            rich.write(line)
        ctrl_lines = self._read_control_log_tail(max_lines=120)
        if ctrl_lines:
            sep = Text()
            sep.append(t("jobs-activity-control-header"), style="bold magenta")
            rich.write(sep)
            for raw in ctrl_lines:
                line = Text()
                line.append("[control] ", style="magenta")
                line.append(raw.rstrip("\n"))
                rich.write(line)

    def action_show_help(self) -> None:
        notify_help(self)

    def action_dismiss_modal(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_refresh(self) -> None:
        try:
            from ...runs.live_share import refresh_share_from_disk

            orch = self.run_manager.orchestrator
            for bg in self.run_manager.list_active():
                for cfg in bg.configs:
                    st = bg.statuses.get(cfg.container_name)
                    if st is None:
                        continue
                    if st.session_dir is None:
                        try:
                            sd = orch.peek_session_dir(cfg.container_name)
                        except Exception:
                            sd = None
                        if sd is not None:
                            st.session_dir = sd
                    if st.session_dir is not None:
                        url = refresh_share_from_disk(st.session_dir)
                        if url:
                            st.share_url = url
                    self._update_status_row(st, run_id=bg.run_id)
        except Exception:
            pass
        self._refresh_app_jobs()
        self._hydrate_history_table()
        self._activity_seq = -1
        self._refresh_activity_log()

    def action_open_session(self) -> None:
        """Open the highlighted container's session in the trace browser (live ok)."""
        try:
            st_table = self.query_one("#jobs-status-table", DataTable)
        except Exception:
            return
        try:
            row_key = st_table.coordinate_to_cell_key(st_table.cursor_coordinate).row_key
            cname = str(row_key.value)
        except Exception:
            self.notify(U.select_container_row(), severity="warning")
            return
        session_dir: Path | None = None
        with suppress(Exception):
            for bg in self.run_manager.list_all_known():
                st = bg.statuses.get(cname)
                if st is not None and st.session_dir is not None:
                    session_dir = Path(st.session_dir)
                    break
                for r in bg.results or []:
                    if r.container_name == cname and r.session_dir is not None:
                        session_dir = Path(r.session_dir)
                        break
                if session_dir is not None:
                    break
        if session_dir is None:
            with suppress(Exception):
                sd = self.run_manager.orchestrator.peek_session_dir(cname)
                if sd is not None:
                    session_dir = Path(sd)
        if session_dir is None or not session_dir.is_dir():
            self.notify(
                t("notify-no-session-yet", container=cname),
                severity="warning",
                timeout=5,
            )
            return
        app = self.app
        self.dismiss(None)
        try:
            open_fn = getattr(app, "open_session_path", None)
            if callable(open_fn):
                open_fn(session_dir, live=True)
            else:
                from .browser import BrowserScreen

                app.push_screen(BrowserScreen(session_dir))
        except Exception as exc:
            with suppress(Exception):
                app.notify(t("notify-open-session-failed", exc=str(exc)), severity="error")

    def action_clear_logs(self) -> None:
        """Clear Logs panes, Activity ring, retained run buffers, and serve-log view offset."""
        from ...job_pools import get_activity_log

        with suppress(Exception):
            self.query_one("#jobs-logs-all", RichLog).clear()
        for name in list(self._log_buffer.keys()):
            self._log_buffer[name] = []
        for tab_id in list(self._log_tabs):
            log_id = "jobs-log-" + tab_id.removeprefix("jobs-log-tab-")
            with suppress(Exception):
                self.query_one(f"#{log_id}", RichLog).clear()
        with suppress(Exception):
            self.query_one("#jobs-activity-log", RichLog).clear()
        get_activity_log().clear()
        self._activity_seq = -1
        with suppress(Exception):
            self.run_manager.clear_captured_logs()
        path = self._control_log_path()
        if path is not None:
            with suppress(OSError):
                self._control_log_skip_bytes = int(path.stat().st_size)
        else:
            self._control_log_skip_bytes = 0
        self._control_log_sig = ("", 0, 0)
        self._refresh_activity_log()

    @on(Button.Pressed, "#jobs-close-btn")
    def _btn_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#jobs-refresh-btn")
    def _btn_refresh(self) -> None:
        self.action_refresh()

    @on(Button.Pressed, "#jobs-clear-btn")
    def _btn_clear(self) -> None:
        self.action_clear_logs()

    def _subscribe(self) -> None:
        if self._subscribed:
            return
        rm = self.run_manager
        rm.add_status_listener(self._on_bg_status)
        rm.add_log_listener(self._on_bg_log)
        rm.add_finished_listener(self._on_bg_finished)
        self._subscribed = True

    def _unsubscribe(self) -> None:
        if not self._subscribed:
            return
        rm = self.run_manager
        rm.remove_status_listener(self._on_bg_status)
        rm.remove_log_listener(self._on_bg_log)
        rm.remove_finished_listener(self._on_bg_finished)
        self._subscribed = False

    def _on_bg_status(self, status: ContainerStatus) -> None:
        with suppress(Exception):
            self.post_message(self.StatusUpdate(copy.copy(status)))

    def _on_bg_log(self, name: str, line: str) -> None:
        with suppress(Exception):
            self.post_message(self.LogLine(name, line))

    def _on_bg_finished(self, run: BackgroundRun) -> None:
        with suppress(Exception):
            self.post_message(self.RunFinished(run))

    def on_jobs_modal_log_line(self, event: LogLine) -> None:
        self._append_log(event.container_name, event.line)

    def on_jobs_modal_status_update(self, event: StatusUpdate) -> None:
        self._update_status_row(event.status, run_id="")

    def on_jobs_modal_run_finished(self, event: RunFinished) -> None:
        self._hydrate_history_table()
        self._refresh_app_jobs()

    def _hydrate_from_manager(self) -> None:
        if self._hydrating:
            return
        self._hydrating = True
        try:
            known = self.run_manager.list_all_known()
            names: list[str] = []
            for bg in known:
                for c in bg.configs:
                    names.append(c.container_name)
            seen: set[str] = set()
            uniq: list[str] = []
            for n in names:
                if n not in seen:
                    seen.add(n)
                    uniq.append(n)
            if uniq:
                self._ensure_log_tabs(uniq)
            seen_st: set[str] = set()
            for bg in known:
                for st in bg.statuses.values():
                    if st.container_name in seen_st:
                        continue
                    seen_st.add(st.container_name)
                    self._update_status_row(st, run_id=bg.run_id)
            log_budget = 400
            for bg in reversed(known):
                if log_budget <= 0:
                    break
                if hasattr(bg, "log_buffer") and bg.log_buffer is not None:
                    snap = bg.log_buffer.snapshot(max_lines=800)
                    lines = [(ln.source, ln.text) for ln in snap]
                else:
                    lines = list(bg.log_lines)
                if not lines:
                    continue
                take = lines[-min(len(lines), log_budget) :]
                log_budget -= len(take)
                for name, line in take:
                    self._append_log(name, line)
            self._hydrate_history_table()
        finally:
            self._hydrating = False

    def _hydrate_history_table(self) -> None:
        ht = self.query_one("#jobs-history-table", DataTable)
        with suppress(Exception):
            ht.clear()
        known = self.run_manager.list_all_known()
        seen_hist: set[str] = set()
        for bg in reversed(known[-20:]):
            hkey = f"hist-{bg.run_id}"
            if hkey in seen_hist:
                continue
            seen_hist.add(hkey)
            err = (bg.error or "")[:60]
            with suppress(Exception):
                ht.add_row(
                    bg.run_id[:12],
                    bg.eval_run.status,
                    str(len(bg.configs)),
                    _format_duration(bg.elapsed_s) if bg.elapsed_s else "—",
                    err or "—",
                    key=hkey,
                )

    def _refresh_app_jobs(self) -> None:
        app = self.app
        lines: list[str] = []
        n_runs = self.run_manager.active_count
        latest = self.run_manager.latest()
        rid = latest.run_id if latest else "—"
        batches = self.run_manager.active_batch_ids
        batch_bit = f" batch={batches[0][:14]}" if batches else ""
        lines.append(
            t(
                "jobs-banner-runs",
                n=n_runs,
                latest=(f" · latest {rid}" if n_runs and rid else ""),
            )
            + batch_bit
        )
        with suppress(Exception):
            if getattr(app, "is_control_client", lambda: False)():
                sock = getattr(app, "_control_socket", None)
                lines.append(t("jobs-control-attached", path=str(sock) if sock else "—"))
            else:
                lines.append(t("jobs-control-offline"))
        with suppress(Exception):
            wd = getattr(app, "work_dir", None) or self.work_dir
            if wd:
                lines.append(t("jobs-work-dir", path=str(wd)))
        self.query_one("#jobs-app-status", Static).update("\n".join(lines))

    @staticmethod
    def _fmt_ts(value: object) -> str:
        """Format ContainerStatus timestamps (ISO str or datetime) for the status table."""
        if value is None or value == "":
            return "—"
        if hasattr(value, "strftime"):
            with suppress(Exception):
                return value.strftime("%H:%M:%S")
        s = str(value).strip()
        if not s:
            return "—"
        if "T" in s:
            with suppress(Exception):
                tpart = s.split("T", 1)[1]
                return tpart[:8]
        return s[:19]

    def _session_cell(self, status: ContainerStatus) -> str:
        sd = getattr(status, "session_dir", None)
        if sd is None:
            return "—"
        try:
            p = Path(sd)
            return p.name[:16] if p.name else str(p)[:16]
        except Exception:
            return "yes"

    def _share_cell(self, status: ContainerStatus) -> str:
        """Short share indicator; full URL via s key / notify."""
        url = getattr(status, "share_url", "") or ""
        sd = getattr(status, "session_dir", None)
        if not url and sd is not None:
            try:
                from ...runs.live_share import get_share_url

                url = get_share_url(sd)
                if url:
                    with suppress(Exception):
                        status.share_url = url
            except Exception:
                pass
        if url:
            try:
                tail = url.rstrip("/").split("/")[-1][:8]
            except Exception:
                tail = "ok"
            return join_ui(t("ui-ok-2"), tail)
        if sd is not None:
            return "…"
        return "—"

    def _status_for_container(self, cname: str) -> ContainerStatus | None:
        with suppress(Exception):
            for bg in self.run_manager.list_all_known():
                st = bg.statuses.get(cname)
                if st is not None:
                    return st
                for r in bg.results or []:
                    if r.container_name == cname:
                        return r
        return None

    def action_open_share(self) -> None:
        """Open Grok /share URL for the highlighted container (browser or copy toast)."""
        try:
            st_table = self.query_one("#jobs-status-table", DataTable)
        except Exception:
            return
        try:
            row_key = st_table.coordinate_to_cell_key(st_table.cursor_coordinate).row_key
            cname = str(row_key.value)
        except Exception:
            self.notify(U.select_container_row(), severity="warning")
            return
        st = self._status_for_container(cname)
        url = ""
        session_dir: Path | None = None
        if st is not None:
            url = getattr(st, "share_url", "") or ""
            if st.session_dir is not None:
                session_dir = Path(st.session_dir)
        if not url and session_dir is not None:
            try:
                from ...runs.live_share import refresh_share_from_disk

                url = refresh_share_from_disk(session_dir)
                if url and st is not None:
                    with suppress(Exception):
                        st.share_url = url
            except Exception:
                pass
        if not url:
            return
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception as exc:
            self.notify(
                t("notify-share-open-failed", name=cname, exc=str(exc)),
                severity="error",
                timeout=10,
            )

    def _update_status_row(self, status: ContainerStatus, *, run_id: str = "") -> None:
        table = self.query_one("#jobs-status-table", DataTable)
        name = status.container_name
        started = self._fmt_ts(status.started_at)
        finished = self._fmt_ts(status.finished_at)
        from ..styles import status_rich_style

        st = status.status
        st_disp = Text(st, style=status_rich_style(st))
        rid = run_id
        if not rid:
            for bg in self.run_manager.list_all_known():
                if name in bg.statuses or name in bg.container_names:
                    rid = bg.run_id
                    break
        sess = self._session_cell(status)
        share = self._share_cell(status)
        cols = list(table.columns.keys())
        exists = name in self._known_rows
        if not exists:
            try:
                table.get_row_index(name)
                exists = True
                self._known_rows.add(name)
            except Exception:
                exists = False
        if exists and len(cols) >= 8:
            try:
                table.update_cell(name, cols[0], name[:28])
                table.update_cell(name, cols[1], (status.model or "")[:24])
                table.update_cell(name, cols[2], st_disp)
                table.update_cell(name, cols[3], sess)
                table.update_cell(name, cols[4], share)
                table.update_cell(name, cols[5], (rid or "—")[:12])
                table.update_cell(name, cols[6], started)
                table.update_cell(name, cols[7], finished)
            except Exception:
                exists = False
        elif exists and len(cols) >= 7:
            try:
                table.update_cell(name, cols[0], name[:28])
                table.update_cell(name, cols[1], (status.model or "")[:24])
                table.update_cell(name, cols[2], st_disp)
                table.update_cell(name, cols[3], sess)
                table.update_cell(name, cols[4], (rid or "—")[:12])
                table.update_cell(name, cols[5], started)
                table.update_cell(name, cols[6], finished)
            except Exception:
                exists = False
        elif exists and len(cols) >= 6:
            try:
                table.update_cell(name, cols[0], name[:28])
                table.update_cell(name, cols[1], (status.model or "")[:24])
                table.update_cell(name, cols[2], st_disp)
                table.update_cell(name, cols[3], (rid or "—")[:12])
                table.update_cell(name, cols[4], started)
                table.update_cell(name, cols[5], finished)
            except Exception:
                exists = False
        if not exists:
            try:
                table.add_row(
                    name[:28],
                    (status.model or "")[:24],
                    st_disp,
                    sess,
                    share,
                    (rid or "—")[:12],
                    started,
                    finished,
                    key=name,
                )
                self._known_rows.add(name)
            except Exception:
                with suppress(Exception):
                    if len(cols) >= 8:
                        table.update_cell(name, cols[2], st_disp)
                        table.update_cell(name, cols[3], sess)
                        table.update_cell(name, cols[4], share)
                        table.update_cell(name, cols[5], (rid or "—")[:12])
                        table.update_cell(name, cols[6], started)
                        table.update_cell(name, cols[7], finished)
                    elif len(cols) >= 7:
                        table.update_cell(name, cols[2], st_disp)
                        table.update_cell(name, cols[3], sess)
                        table.update_cell(name, cols[4], (rid or "—")[:12])
                        table.update_cell(name, cols[5], started)
                        table.update_cell(name, cols[6], finished)
                    elif len(cols) >= 6:
                        table.update_cell(name, cols[2], st_disp)
                        table.update_cell(name, cols[3], (rid or "—")[:12])
                        table.update_cell(name, cols[4], started)
                        table.update_cell(name, cols[5], finished)
                    self._known_rows.add(name)
        self._ensure_log_tabs([name])

    @staticmethod
    def _log_ids(container_name: str) -> tuple[str, str]:
        """Return ``(tab_pane_id, rich_log_id)`` safe for Textual identifiers."""
        safe = widget_id(container_name, max_len=80, fallback="container")
        return f"jobs-log-tab-{safe}", f"jobs-log-{safe}"

    def _ensure_log_tabs(self, container_names: list[str]) -> None:
        try:
            tabs = self.query_one("#jobs-logs-tabs", TabbedContent)
        except Exception:
            return
        for i, name in enumerate(container_names):
            tab_id, log_id = self._log_ids(name)
            if tab_id in self._log_tabs:
                continue
            short = name.split("-", 2)[-1][:15] if "-" in name else name[:15]
            color = self._CONTAINER_COLORS[
                (len(self._container_color_map) + i) % len(self._CONTAINER_COLORS)
            ]
            self._container_color_map.setdefault(name, color)
            self._log_buffer.setdefault(name, [])
            pane = TabPane(short, id=tab_id)
            pane.compose_add_child(RichLog(id=log_id, wrap=True, max_lines=3000))
            with suppress(Exception):
                tabs.add_pane(pane)
                self._log_tabs.add(tab_id)
        self.set_timer(0.4, self._flush_log_buffers)

    def _flush_log_buffers(self) -> None:
        for name, lines in list(self._log_buffer.items()):
            if not lines:
                continue
            _tab_id, log_id = self._log_ids(name)
            try:
                log = self.query_one(f"#{log_id}", RichLog)
            except Exception:
                continue
            for line in lines:
                log.write(line)
            self._log_buffer[name] = []
            self._tabs_mounted.add(name)

    def _append_log(self, container_name: str, line: str) -> None:
        self._ensure_log_tabs([container_name])
        color = self._container_color_map.get(container_name, "white")
        _tab_id, log_id = self._log_ids(container_name)
        styled = Text()
        styled.append(f"[{container_name.split('-')[-1][:10]}] ", style=f"bold {color}")
        text = line.rstrip("\n")
        if len(text) > 400:
            text = text[:397] + "…"
        try:
            styled.append(text)
        except Exception:
            styled.append(rich_escape(text))
        with suppress(Exception):
            self.query_one("#jobs-logs-all", RichLog).write(styled)
        if container_name in self._tabs_mounted:
            try:
                self.query_one(f"#{log_id}", RichLog).write(styled)
            except Exception:
                self._log_buffer.setdefault(container_name, []).append(styled)
        else:
            self._log_buffer.setdefault(container_name, []).append(styled)
