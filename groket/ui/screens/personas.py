"""Persona builder — list / create / edit / delete personas (structured UI only).

MCP (tools) and Skills (SKILL.md packages) are separate persona capabilities.
"""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    Select,
    Static,
    TabbedContent,
    TabPane,
    TextArea,
)

from ...runs.personas import Persona, PersonaStore, personas_dir
from .. import text as U
from ..bindings import (
    CAPABILITY_PICKER,
    FORM_SAVE,
    PERSONA_EDITOR,
    PERSONAS,
    ChromeActions,
    focus_primary_list,
)
from ..data_table import (
    cursor_row_key,
    preserving_cursor,
    restore_cursor,
    selection_mark,
    set_selection_marker,
    style_data_table,
)
from ..forms import PERSONA_NONE, docker_select_options, docker_select_value_or_default
from ..i18n import join_ui, t
from ..panel_render import TipSurface
from ..quit_actions import QuitActions
from ..tab_panes import TabPaneNavigation
from ..widgets.key_value_editor import KeyValueEditor


def _slug_id(text: str) -> str:
    s = re.sub("[^a-zA-Z0-9._-]+", "-", (text or "").strip().lower())
    s = s.strip("-")[:48].strip("-")
    return s or "persona"


_PERSONA_DOCKER_OPTIONS: list[tuple[str, str]] = [
    (t("ui-inherit-from-runner-run-config"), PERSONA_NONE),
    *docker_select_options(),
]


def _persona_docker_value(stored: str) -> str:
    s = (stored or "").strip()
    if not s:
        return PERSONA_NONE
    return docker_select_value_or_default(s)


def _persona_docker_stored(select_val: str) -> str:
    s = (select_val or "").strip()
    if not s or s == PERSONA_NONE:
        return ""
    return docker_select_value_or_default(s)


def _ids_to_text(ids: list[str]) -> str:
    return "\n".join(i for i in ids or [] if (i or "").strip())


def _ids_from_text(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for part in re.split(t("ui-msg-2"), s):
            p = part.strip()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
    return out


McpPickerResult = tuple[list[str], list[dict], dict[str, str], list[str]]


class McpConfigureModal(QuitActions, ModalScreen[dict | None]):
    """Interactive configure for one MCP server (registry/catalog hit) before adding to persona."""

    BINDINGS = list(FORM_SAVE)

    def __init__(self, definition: dict, *, title: str = "Configure MCP") -> None:
        super().__init__()
        self._defn = dict(definition or {})
        self._title = title

    def compose(self) -> ComposeResult:
        d = self._defn
        reg = str(d.get("registry_name") or "")
        desc = str(d.get("description") or "")[:600]
        transport = str(d.get("transport") or "http")
        url = str(d.get("url") or "")
        command = str(d.get("command") or "")
        needs = d.get("needs_env") or []
        headers = d.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}
        hdr_lines = "\n".join((f"{k}={v}" for k, v in headers.items()))
        env_hint_lines = "\n".join(f"{k}=" for k in (needs if isinstance(needs, list) else []))
        ver = str(d.get("version") or "").strip()
        status = str(d.get("status") or "").strip()
        repo = str(d.get("repository_url") or "").strip()
        reg_url = str(d.get("registry_url") or "").strip()
        docs_links = d.get("docs_links") or []
        with Vertical(id="mcp-cfg-modal"):
            with VerticalScroll(id="mcp-cfg-body"):
                yield Static(f"[bold]{self._title}[/bold]")
                if reg:
                    meta_bits = [reg]
                    if ver:
                        meta_bits.append(f"v{ver}")
                    if status:
                        meta_bits.append(f"[{status}]")
                    yield Static(join_ui(t("ui-registry-3").strip(), " ".join(meta_bits)))
                if desc:
                    yield Static(f"{desc}")
                link_lines: list[str] = []
                if repo:
                    link_lines.append(join_ui(t("ui-repository"), repo))
                if reg_url:
                    link_lines.append(join_ui(t("ui-registry-2"), reg_url))
                if isinstance(docs_links, list):
                    for item in docs_links:
                        if not isinstance(item, dict):
                            continue
                        lab = str(item.get("label") or "link").strip()
                        u = str(item.get("url") or "").strip()
                        if not u:
                            continue
                        if repo and u == repo:
                            continue
                        if reg_url and u == reg_url:
                            continue
                        link_lines.append(f"[cyan]{lab}[/cyan]  {u}")
                if link_lines:
                    yield Static(
                        t("ui-docs-source-copy-url-open-on-host-browser") + "\n".join(link_lines)
                    )
                else:
                    yield Static(t("ui-no-docs-repo-url-from-registry-search-the-server"))
                yield Label(U.server_id_hint())
                yield Input(value=str(d.get("id") or "mcp"), id="mcp-cfg-id")
                yield Label(U.transport_endpoint())
                yield Static(
                    "\n".join(
                        (
                            join_ui("transport=", transport),
                            join_ui("url=", url or "—"),
                            join_ui(
                                "command=",
                                command or "—",
                                t("ui-args"),
                                d.get("args") or [],
                            ),
                        )
                    )
                )
                yield Label(U.headers_hint())
                yield TextArea(hdr_lines, id="mcp-cfg-headers")
                yield Label(U.env_vars_on_persona())
                yield TextArea(env_hint_lines, id="mcp-cfg-env")
                yield Checkbox(t("ui-create-companion-skill"), value=True, id="mcp-cfg-make-skill")
                yield Static(t("ui-stdio-needs-tools-in-image"), classes="pe-field-hint")
            with Horizontal(id="mcp-cfg-footer", classes="modal-footer"):
                yield Button(U.save(), variant="primary", id="mcp-cfg-save")
                yield Button(U.cancel(), id="mcp-cfg-cancel")

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self._do_save()

    @on(Button.Pressed, "#mcp-cfg-cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#mcp-cfg-save")
    def _save_btn(self) -> None:
        self._do_save()

    def _do_save(self) -> None:
        sid = self.query_one("#mcp-cfg-id", Input).value.strip()
        sid = re.sub("[^a-zA-Z0-9._-]+", "-", sid).strip("-")[:48]
        if not sid:
            self.notify(U.server_id_required(), severity="error")
            return
        headers: dict[str, str] = {}
        for line in self.query_one("#mcp-cfg-headers", TextArea).text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k:
                headers[k] = v.strip()
        env_add: dict[str, str] = {}
        for line in self.query_one("#mcp-cfg-env", TextArea).text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k:
                env_add[k] = v.strip()
        try:
            make_skill = bool(self.query_one("#mcp-cfg-make-skill", Checkbox).value)
        except Exception:
            make_skill = True
        out = dict(self._defn)
        out["id"] = sid
        out["headers"] = headers
        out["_env_add"] = env_add
        out["_make_skill"] = make_skill
        self.dismiss(out)


class McpPickerModal(QuitActions, ModalScreen[McpPickerResult | None]):
    """Browse local catalog + live MCP registry; configure interactively before add."""

    BINDINGS = [
        *CAPABILITY_PICKER,
        Binding("r", "registry_mode", t("ui-registry"), id="mcp.registry", show=True),
        Binding("l", "local_mode", t("ui-local"), id="mcp.local", show=False),
    ]

    def __init__(
        self,
        work_dir: Path,
        selected: list[str] | None = None,
        definitions: list[dict] | None = None,
        *,
        initial_query: str = "",
        auto_registry: bool = False,
        heading: str = "",
        keep_hint: str = t("ui-save-persona-to-keep"),
    ) -> None:
        super().__init__()
        self.work_dir = Path(work_dir)
        self._selected: set[str] = set(selected or [])
        self._definitions: dict[str, dict] = {}
        for d in definitions or []:
            if isinstance(d, dict) and str(d.get("id") or "").strip():
                self._definitions[str(d["id"]).strip()] = dict(d)
        self._env_add: dict[str, str] = {}
        self._skills_add: list[str] = []
        self._row_meta: dict[str, tuple[str, object]] = {}  # Textual-free; catalog/registry row
        self._registry_hits: list = []
        self._status = ""
        self._initial_query = (initial_query or "").strip()
        self._auto_registry = bool(auto_registry) or bool(self._initial_query)
        self._mode: str = "registry" if self._auto_registry else "local"
        self._heading = (heading or "").strip()
        self._keep_hint = (keep_hint or t("ui-save-persona-to-keep")).strip()
        self._search_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-picker-modal"):
            title = self._heading or t("ui-mcp")
            yield Static(f"[bold]{title}[/bold]", id="mcp-pick-title")
            yield TipSurface(U.tip_mcp_pick(), id="mcp-pick-keys")
            yield Input(
                value=self._initial_query,
                placeholder=U.mcp_search_placeholder(),
                id="mcp-pick-search",
            )
            with Horizontal(id="mcp-pick-search-row"):
                yield Button(U.registry_btn(), variant="primary", id="mcp-pick-reg")
                yield Button(U.local_btn(), id="mcp-pick-local")
            yield Static("", id="mcp-pick-status")
            yield DataTable(id="mcp-pick-table", cursor_type="row")
            yield Static("", id="mcp-pick-detail")
            yield Static("", id="mcp-pick-sel")
            with Horizontal(id="mcp-pick-footer"):
                yield Button(U.done(), variant="primary", id="mcp-pick-done")
                yield Button(U.cancel(), id="mcp-pick-cancel")

    def on_mount(self) -> None:
        table = self.query_one("#mcp-pick-table", DataTable)
        style_data_table(table)
        table.add_columns(" ", "id/name", "title", "src", "transport")
        with suppress(Exception):
            self.query_one("#mcp-pick-search", Input).focus()
        self._refresh_current_view()
        self._update_sel_label()
        self.call_after_refresh(self._update_detail_from_cursor)

    def _set_detail(self, text: str) -> None:
        with suppress(Exception):
            self.query_one("#mcp-pick-detail", Static).update(text or t("ui-no-row-selected"))

    def _detail_for_key(self, key: str | None) -> str:
        if not key or key not in self._row_meta:
            return t("ui-select-a-row-for-description-endpoint-env-needs")
        kind, payload = self._row_meta[key]
        if kind == "registry":
            hit = payload
            try:
                summary = getattr(hit, "detail_summary", None)
                if callable(summary):
                    return str(summary())
                return str(hit)
            except Exception as exc:
                return join_ui(t("ui-could-not-render-details"), exc)
        from ...capabilities.catalog import McpCatalogEntry

        if not isinstance(payload, McpCatalogEntry):
            return str(payload)
        e = payload
        lines = [
            f"[bold]{e.title or e.id}[/bold]",
            join_ui("id=", e.id, t("ui-source-1"), e.source, t("ui-transport"), e.transport),
        ]
        if e.description:
            lines.append((e.description or "")[:500])
        if e.url:
            lines.append(f"[dim]url=[/dim]{e.url}")
        if e.command:
            args = " ".join(str(a) for a in e.args or [])
            lines.append(f"[dim]command=[/dim]{e.command} {args}".rstrip())
        if e.needs_env:
            lines.append(join_ui(t("ui-needs-env"), ", ".join(e.needs_env)))
        if e.source == "host":
            lines.append(
                t("ui-host-pass-through-uses-mcp-servers")
                + e.id
                + t("ui-from-your-grok-config-toml-no-extra-def-unless-y")
            )
        else:
            lines.append(t("ui-local-catalog-entry-configure-to-set-headers-env"))
        return "\n".join(lines)

    def _update_detail_from_cursor(self) -> None:
        self._set_detail(self._detail_for_key(self._cursor_key()))

    @on(DataTable.RowHighlighted, "#mcp-pick-table")
    def _row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._update_detail_from_cursor()

    def _set_status(self, msg: str) -> None:
        self._status = msg
        with suppress(Exception):
            self.query_one("#mcp-pick-status", Static).update(f"[dim]{msg}[/dim]" if msg else "")

    def _search_query(self) -> str:
        try:
            return (self.query_one("#mcp-pick-search", Input).value or "").strip()
        except Exception:
            return ""

    def _refresh_current_view(self) -> None:
        if self._mode == "registry":
            # Capture query on the UI thread; the worker must not touch widgets.
            self._refresh_registry(self._search_query())
        else:
            self._refresh_local()

    def _refresh_local(self) -> None:
        from ...capabilities import search_mcp_catalog

        self._mode = "local"
        q = self._search_query()
        table = self.query_one("#mcp-pick-table", DataTable)
        with preserving_cursor(table):
            table.clear()
            self._row_meta.clear()
            self._registry_hits = []
            for e in search_mcp_catalog(q, work_dir=self.work_dir, limit=80):
                key = f"local:{e.id}"
                table.add_row(
                    selection_mark(e.id in self._selected),
                    e.id,
                    (e.title or "")[:36],
                    e.source[:8],
                    e.transport[:8],
                    key=key,
                )
                self._row_meta[key] = ("local", e)
        self._set_status(t("persona-local-count", n=len(self._row_meta)))
        self.call_after_refresh(self._update_detail_from_cursor)

    @work(thread=True, exclusive=True, group="mcp-registry")
    def _refresh_registry(self, query: str = "") -> None:
        """Live registry search; *query* is captured on the UI thread before schedule."""
        from ...capabilities import search_registry
        from ..threads import call_ui

        q = (query or "").strip()
        if not q:

            def _empty() -> None:
                self._mode = "registry"
                table = self.query_one("#mcp-pick-table", DataTable)
                table.clear()
                self._row_meta.clear()
                self._registry_hits = []
                self._set_status(t("ui-registry-type-a-query-enter"))

            call_ui(self.app, _empty)
            return

        def _searching() -> None:
            self._mode = "registry"
            self._set_status(t("persona-registry-searching", query=repr(q)))

        call_ui(self.app, _searching)
        hits, err = search_registry(q, limit=40)

        def _apply_results() -> None:
            self._mode = "registry"
            table = self.query_one("#mcp-pick-table", DataTable)
            with preserving_cursor(table):
                table.clear()
                self._row_meta.clear()
                self._registry_hits = list(hits or [])
                if err and (not hits):
                    self._set_status(t("persona-registry-error", error=str(err)))
                    return
                for i, hit in enumerate(hits or []):
                    entry = hit.to_catalog_entry()
                    key = f"reg:{i}:{hit.name}"
                    sid = entry.id
                    table.add_row(
                        selection_mark(sid in self._selected),
                        hit.name[:48],
                        (hit.title or "")[:32],
                        "registry",
                        entry.transport[:10],
                        key=key,
                    )
                    self._row_meta[key] = ("registry", hit)
            extra = f" · {err}" if err else ""
            self._set_status(
                t(
                    "persona-registry-hits",
                    n=len(hits or []),
                    query=repr(q),
                    extra=extra or "",
                )
            )
            self.call_after_refresh(self._update_detail_from_cursor)

        call_ui(self.app, _apply_results)

    def _update_sel_label(self) -> None:
        ids = sorted(self._selected)
        n_def = len(self._definitions)
        with suppress(Exception):
            self.query_one("#mcp-pick-sel", Static).update(
                t(
                    "ui-mcp-pick-sel",
                    n=len(ids),
                    configured=n_def,
                    ids=(", ".join(ids[:10]) + ("…" if len(ids) > 10 else "") if ids else "—"),
                )
            )

    def _schedule_search(self) -> None:
        """Debounce while typing (mode decides local filter vs live registry)."""
        with suppress(Exception):
            if self._search_timer is not None:
                self._search_timer.stop()
        self._search_timer = self.set_timer(0.35, self._refresh_current_view)

    @on(Input.Changed, "#mcp-pick-search")
    def _search_changed(self, _event: Input.Changed) -> None:
        self._schedule_search()

    @on(Input.Submitted, "#mcp-pick-search")
    def _search_submitted(self, _event: Input.Submitted) -> None:
        self._mode = "registry"
        self._refresh_registry(self._search_query())

    @on(Button.Pressed, "#mcp-pick-reg")
    def _reg_btn(self) -> None:
        self._mode = "registry"
        self._refresh_registry(self._search_query())

    @on(Button.Pressed, "#mcp-pick-local")
    def _local_btn(self) -> None:
        self._mode = "local"
        self._refresh_local()

    def action_registry_mode(self) -> None:
        self._mode = "registry"
        self._refresh_registry(self._search_query())

    def action_local_mode(self) -> None:
        self._mode = "local"
        self._refresh_local()

    def action_registry_search(self) -> None:
        self.action_registry_mode()

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_done(self) -> None:
        self.dismiss(
            (
                sorted(self._selected),
                list(self._definitions.values()),
                dict(self._env_add),
                list(dict.fromkeys(self._skills_add)),
            )
        )

    def action_toggle_select(self) -> None:
        """Same Select key as sessions / configs (`s` / `space`)."""
        self._add_or_remove_cursor()

    @on(Button.Pressed, "#mcp-pick-cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#mcp-pick-done")
    def _done_btn(self) -> None:
        self.dismiss(
            (
                sorted(self._selected),
                list(self._definitions.values()),
                dict(self._env_add),
                list(dict.fromkeys(self._skills_add)),
            )
        )

    def _apply_configure_result(self, result: dict | None, *, default_sid: str = "") -> None:
        """Merge configure modal output: definition, env, optional companion skill."""
        if not result:
            return
        rid = str(result.get("id") or default_sid or "mcp").strip()
        make_skill = bool(result.pop("_make_skill", True))
        env_add = result.pop("_env_add", None) or {}
        if isinstance(env_add, dict):
            for k, v in env_add.items():
                if k:
                    self._env_add[k] = v
        self._definitions[rid] = result
        self._selected.add(rid)
        if make_skill:
            try:
                from ...capabilities.skill_gen import write_mcp_companion_skill

                written = write_mcp_companion_skill(self.work_dir, result, overwrite=True)
                if written:
                    skill_name, _path = written
                    if skill_name not in self._skills_add:
                        self._skills_add.append(skill_name)
                    self._set_status(
                        join_ui(
                            t("ui-added-mcp"),
                            rid,
                            t("ui-skill"),
                            skill_name,
                            "` (",
                            self._keep_hint,
                            ").",
                        )
                    )
                else:
                    self._set_status(join_ui(t("ui-added-mcp"), rid, t("ui-skill-not-written")))
            except Exception as exc:
                self._set_status(join_ui(t("ui-added-mcp"), rid, t("ui-skill-write-failed"), exc))
        else:
            self._set_status(join_ui(t("ui-added-mcp"), rid, t("ui-no-companion-skill")))
        self._rerender_selection_marks()
        self._update_sel_label()

    @on(DataTable.RowSelected, "#mcp-pick-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        _ = event
        self._add_or_remove_cursor()

    def _cursor_key(self) -> str | None:
        return cursor_row_key(self.query_one("#mcp-pick-table", DataTable))

    def _add_or_remove_cursor(self) -> None:
        key = self._cursor_key()
        if not key or key not in self._row_meta:
            return
        kind, payload = self._row_meta[key]
        if kind == "local":
            from ...capabilities.catalog import McpCatalogEntry
            from ...capabilities.registry import registry_hit_to_definition

            e = payload
            assert isinstance(e, McpCatalogEntry)
            sid = e.id
            if sid in self._selected and sid in self._definitions:
                self._selected.discard(sid)
                self._definitions.pop(sid, None)
                self._rerender_selection_marks()
                self._update_sel_label()
                return
            if sid in self._selected and sid not in self._definitions:
                self._selected.discard(sid)
                self._rerender_selection_marks()
                self._update_sel_label()
                return
            defn = {
                "id": sid,
                "title": e.title,
                "description": e.description,
                "transport": e.transport,
                "url": e.url,
                "command": e.command,
                "args": list(e.args),
                "headers": {},
                "needs_env": list(e.needs_env),
                "source": e.source,
            }
            if e.transport == "host":
                self._selected.add(sid)
                self._rerender_selection_marks()
                self._update_sel_label()
                return
            self.app.push_screen(
                McpConfigureModal(defn, title=t("persona-configure-title", name=sid)),
                lambda r: self._apply_configure_result(r, default_sid=sid),
            )
            return
        if kind == "registry":
            from ...capabilities.registry import RegistryServerHit, registry_hit_to_definition

            hit = payload
            if not isinstance(hit, RegistryServerHit):
                return
            defn_obj = registry_hit_to_definition(hit)
            reg_defn: dict = {str(k): v for k, v in defn_obj.items()}
            hit_name = str(hit.name or reg_defn.get("id") or "mcp")
            self.app.push_screen(
                McpConfigureModal(reg_defn, title=t("persona-configure-title", name=hit_name)),
                lambda r: self._apply_configure_result(
                    r, default_sid=str(reg_defn.get("id") or "mcp")
                ),
            )

    def _rerender_selection_marks(self) -> None:
        """Update leading green ``*`` in place — never clear (preserves cursor)."""
        table = self.query_one("#mcp-pick-table", DataTable)
        for key, (kind, payload) in list(self._row_meta.items()):
            if kind == "local":
                sid = getattr(payload, "id", None) or ""
            else:
                try:
                    to_entry = getattr(payload, "to_catalog_entry", None)
                    sid = to_entry().id if callable(to_entry) else ""
                except Exception:
                    sid = ""
            set_selection_marker(table, key, bool(sid and sid in self._selected))


class PluginPickerModal(QuitActions, ModalScreen[list[str] | None]):
    """Pick marketplace plugins for the persona (names stored on the persona)."""

    BINDINGS = list(CAPABILITY_PICKER)

    def __init__(self, work_dir: Path, selected: list[str] | None = None) -> None:
        super().__init__()
        self.work_dir = Path(work_dir)
        self._selected: set[str] = set(selected or [])
        from ...capabilities.marketplace import PluginPickRow

        self._rows_by_name: dict[str, PluginPickRow] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="plugins-picker-modal"):
            yield Static(f"[bold]{U.plugins_title()}[/bold]", id="plugins-pick-title")
            yield TipSurface(U.tip_plugins_pick(), id="plugins-pick-keys")
            yield Input(placeholder=U.plugins_search_placeholder(), id="plugins-pick-search")
            yield DataTable(id="plugins-pick-table", cursor_type="row")
            yield Static("", id="plugins-pick-detail")
            yield Static("", id="plugins-pick-sel")
            with Horizontal(id="plugins-pick-footer"):
                yield Button(U.done(), variant="primary", id="plugins-pick-done")
                yield Button(U.cancel(), id="plugins-pick-cancel")

    def on_mount(self) -> None:
        table = self.query_one("#plugins-pick-table", DataTable)
        style_data_table(table)
        table.add_columns(" ", "name", "status", "category", "components")
        self._refresh_table()
        self._update_sel_label()
        self._update_detail()

    def _refresh_table(self) -> None:
        from ...capabilities import list_plugins_for_picker

        q = ""
        with suppress(Exception):
            q = self.query_one("#plugins-pick-search", Input).value.strip().lower()
        table = self.query_one("#plugins-pick-table", DataTable)
        with preserving_cursor(table):
            table.clear()
            self._rows_by_name = {}
            for row in list_plugins_for_picker(self.work_dir):
                if q and q not in row.search_blob() and (q != row.name.lower()):
                    continue
                self._rows_by_name[row.name] = row
                status = {
                    "installed": "installed",
                    "fetch": "fetch@launch",
                    "catalog": "catalog",
                }.get(row.status, row.status)
                cat = (row.category or "—")[:14]
                comp = (row.components or "—")[:16]
                table.add_row(
                    selection_mark(row.name in self._selected),
                    row.name,
                    status,
                    cat,
                    comp,
                    key=row.name,
                )
        self._update_detail()

    def _cursor_plugin_name(self) -> str | None:
        return cursor_row_key(self.query_one("#plugins-pick-table", DataTable))

    def _update_detail(self) -> None:
        name = self._cursor_plugin_name()
        row = self._rows_by_name.get(name) if name else None
        try:
            detail = self.query_one("#plugins-pick-detail", Static)
        except Exception:
            return
        if row is None:
            detail.update("[dim]—[/dim]")
            return
        markup = getattr(row, "detail_markup", None)
        detail.update(str(markup()) if callable(markup) else str(row))

    def _update_sel_label(self) -> None:
        ids = sorted(self._selected)
        with suppress(Exception):
            self.query_one("#plugins-pick-sel", Static).update(
                t(
                    "ui-plugins-pick-sel",
                    n=len(ids),
                    ids=", ".join(ids) if ids else "—",
                )
            )

    @on(Input.Changed, "#plugins-pick-search")
    def _search_changed(self, _event: Input.Changed) -> None:
        self._refresh_table()

    @on(DataTable.RowHighlighted, "#plugins-pick-table")
    def _row_highlighted(self, _event: DataTable.RowHighlighted) -> None:
        self._update_detail()

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_done(self) -> None:
        self.dismiss(sorted(self._selected))

    def action_toggle_select(self) -> None:
        """Same Select key as sessions / configs (`s` / `space`)."""
        name = self._cursor_plugin_name()
        if not name:
            return
        if name in self._selected:
            self._selected.discard(name)
        else:
            self._selected.add(name)
            row = self._rows_by_name.get(name)
            if row is not None and getattr(row, "status", "") == "fetch":
                self.notify(
                    join_ui("`", name, t("ui-will-be-git-fetched-into-the-container-volume-at")),
                    severity="information",
                    timeout=5,
                )
        set_selection_marker(
            self.query_one("#plugins-pick-table", DataTable), name, name in self._selected
        )
        self._update_sel_label()
        self._update_detail()

    @on(Button.Pressed, "#plugins-pick-done")
    def _done_btn(self) -> None:
        self.action_done()

    @on(Button.Pressed, "#plugins-pick-cancel")
    def _cancel_btn(self) -> None:
        self.action_cancel()

    @on(DataTable.RowSelected, "#plugins-pick-table")
    def _row_selected(self, _event: DataTable.RowSelected) -> None:
        self.action_toggle_select()


class SkillsPickerModal(QuitActions, ModalScreen[list[str] | None]):
    """Search/add skills from host/work_dir/bundled (separate from MCP)."""

    BINDINGS = list(CAPABILITY_PICKER)

    def __init__(self, work_dir: Path, selected: list[str] | None = None) -> None:
        super().__init__()
        self.work_dir = Path(work_dir)
        self._selected: set[str] = set(selected or [])

    def compose(self) -> ComposeResult:
        with Vertical(id="skills-picker-modal"):
            yield Static(f"[bold]{U.skills_title()}[/bold]", id="skills-pick-title")
            yield TipSurface(U.tip_skills_pick(), id="skills-pick-keys")
            yield Input(placeholder=U.skills_search_placeholder(), id="skills-pick-search")
            yield DataTable(id="skills-pick-table", cursor_type="row")
            yield Static("", id="skills-pick-sel")
            with Horizontal(id="skills-pick-footer"):
                yield Button(U.done(), variant="primary", id="skills-pick-done")
                yield Button(U.cancel(), id="skills-pick-cancel")

    def on_mount(self) -> None:
        table = self.query_one("#skills-pick-table", DataTable)
        style_data_table(table)
        table.add_columns(" ", "name", "source", "description")
        self._refresh_table()
        self._update_sel_label()

    def _refresh_table(self) -> None:
        from ...capabilities import search_skills

        q = ""
        with suppress(Exception):
            q = self.query_one("#skills-pick-search", Input).value
        table = self.query_one("#skills-pick-table", DataTable)
        with preserving_cursor(table):
            table.clear()
            for e in search_skills(q, work_dir=self.work_dir, limit=100):
                desc = (e.description or "")[:48]
                src = e.source if len(e.source) <= 18 else e.source[:15] + "…"
                table.add_row(
                    selection_mark(e.name in self._selected), e.name, src, desc, key=e.name
                )

    def _update_sel_label(self) -> None:
        ids = sorted(self._selected)
        with suppress(Exception):
            self.query_one("#skills-pick-sel", Static).update(
                t(
                    "ui-skills-pick-sel",
                    n=len(ids),
                    ids=(", ".join(ids[:8]) + ("…" if len(ids) > 8 else "") if ids else "—"),
                )
            )

    @on(Input.Changed, "#skills-pick-search")
    def _search_changed(self, _event: Input.Changed) -> None:
        self._refresh_table()

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_done(self) -> None:
        self.dismiss(sorted(self._selected))

    def action_toggle_select(self) -> None:
        """Same Select key as sessions / configs (`s` / `space`)."""
        table = self.query_one("#skills-pick-table", DataTable)
        name = cursor_row_key(table)
        if not name:
            return
        if name in self._selected:
            self._selected.discard(name)
        else:
            self._selected.add(name)
        set_selection_marker(table, name, name in self._selected)
        self._update_sel_label()

    @on(Button.Pressed, "#skills-pick-cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#skills-pick-done")
    def _done_btn(self) -> None:
        self.dismiss(sorted(self._selected))

    @on(DataTable.RowSelected, "#skills-pick-table")
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        _ = event
        self.action_toggle_select()


class PersonaEditorModal(TabPaneNavigation, QuitActions, ModalScreen[Persona | None]):
    TAB_CONTENT_ID = "pe-tabs"
    TAB_PANES = (
        "pe-tab-identity",
        "pe-tab-github",
        "pe-tab-env",
        "pe-tab-mcp",
        "pe-tab-skills",
        "pe-tab-plugins",
    )

    def action_tab_identity(self) -> None:
        self.activate_tab_pane("pe-tab-identity")

    def action_tab_github(self) -> None:
        self.activate_tab_pane("pe-tab-github")

    def action_tab_env(self) -> None:
        self.activate_tab_pane("pe-tab-env")

    def action_tab_mcp(self) -> None:
        self.activate_tab_pane("pe-tab-mcp")

    def action_tab_skills(self) -> None:
        self.activate_tab_pane("pe-tab-skills")

    def action_tab_plugins(self) -> None:
        self.activate_tab_pane("pe-tab-plugins")

    """Create or edit one persona — tabbed sections, app-standard pane keys."""

    BINDINGS = list(PERSONA_EDITOR)

    def __init__(
        self, work_dir: Path, persona: Persona | None = None, *, is_new: bool = False
    ) -> None:
        super().__init__()
        self.work_dir = Path(work_dir)
        self._store = PersonaStore(self.work_dir)
        self._original = persona
        self._is_new = is_new or persona is None
        self._persona = persona or Persona(persona_id="", name="")
        self._mcp_definitions: list[dict] = list(self._persona.mcp_definitions or [])
        self._clean_snapshot: tuple[str, ...] | None = None

    def compose(self) -> ComposeResult:
        p = self._persona
        title = U.new_persona_title() if self._is_new else U.edit_persona_title(p.persona_id)
        tok_hint = (
            t("ui-leave-blank-to-keep-existing-token")
            if not self._is_new and (p.github_token or "").strip()
            else t("ui-github-pat-stored-on-this-persona")
        )
        tok_status = (
            join_ui(
                t("ui-token-on-file-yes"),
                len(p.github_token.strip()),
                t("ui-chars-blank-keeps-current-enter-a-value-to-repla"),
            )
            if not self._is_new and (p.github_token or "").strip()
            else t("ui-no-token-stored-yet")
        )
        with Vertical(id="persona-editor-shell"):
            yield Static(f"[bold]{title}[/bold]", id="pe-title")
            yield TipSurface(U.tip_persona_editor(), id="pe-hint")
            with TabbedContent(id="pe-tabs"):
                with TabPane(U.pe_tab_identity(), id="pe-tab-identity"):
                    with VerticalScroll(classes="pe-pane"):
                        with Horizontal(classes="pe-inline-row"):
                            with Vertical():
                                yield Label(U.persona_id_label())
                                yield Input(
                                    value=p.persona_id if not self._is_new else "",
                                    placeholder=U.persona_id_placeholder(),
                                    id="pe-id",
                                    disabled=not self._is_new,
                                )
                            with Vertical():
                                yield Label(U.display_name_label())
                                yield Input(
                                    value=p.name or "",
                                    placeholder=U.friendly_name_placeholder(),
                                    id="pe-name",
                                )
                        yield Label(U.description_field_label())
                        yield Input(
                            value=p.description or "",
                            placeholder=U.optional_blurb_placeholder(),
                            id="pe-desc",
                        )
                        yield Label(U.default_docker_image_label())
                        yield Select(
                            options=_PERSONA_DOCKER_OPTIONS,
                            value=_persona_docker_value(p.docker_image),
                            id="pe-docker",
                            allow_blank=False,
                            classes="field-select",
                        )
                        yield Label(U.notes_label())
                        yield TextArea(p.notes or "", id="pe-notes")
                with TabPane(U.pe_tab_github_title(), id="pe-tab-github"):
                    with VerticalScroll(classes="pe-pane"):
                        yield Checkbox(
                            t("ui-github-write-push"), value=bool(p.github_write), id="pe-gh-write"
                        )
                        yield Label(U.github_token_label())
                        yield Input(value="", placeholder=tok_hint, id="pe-gh-token", password=True)
                        yield Static(tok_status, id="pe-gh-token-status")
                        yield Label(U.token_from_host_env())
                        yield Input(
                            value=p.github_token_env or "",
                            placeholder=U.optional_env_var_name(),
                            id="pe-gh-token-env",
                        )
                        with Horizontal(classes="pe-inline-row"):
                            with Vertical():
                                yield Label(U.git_user_name())
                                yield Input(
                                    value=p.git_user_name or "",
                                    placeholder=U.optional_git_user_name(),
                                    id="pe-git-name",
                                )
                            with Vertical():
                                yield Label(U.git_user_email())
                                yield Input(
                                    value=p.git_user_email or "",
                                    placeholder=U.optional_git_user_email(),
                                    id="pe-git-email",
                                )
                with TabPane(U.pe_tab_env_title(), id="pe-tab-env"):
                    with VerticalScroll(classes="pe-pane"):
                        yield Label(U.extra_env_vars_label())
                        yield KeyValueEditor(p.env_vars or {}, id="pe-env")
                with TabPane(U.pe_tab_mcp_title(), id="pe-tab-mcp"):
                    with VerticalScroll(classes="pe-pane"):
                        yield Checkbox(
                            t("ui-replace-host-mcp-persona-only"),
                            value=bool(p.mcp_replace_host),
                            id="pe-mcp-replace",
                        )
                        yield Label(U.mcp_server_ids_label())
                        yield TextArea(
                            _ids_to_text(p.mcp_servers or []), id="pe-mcp-ids", classes="pe-medium"
                        )
                        with Horizontal(id="pe-mcp-actions", classes="pe-btn-row"):
                            yield Button(U.pick_mcp(), id="pe-mcp-pick")
                        yield Label(U.extra_mcp_toml())
                        yield Static(
                            t("ui-appended-into-the-container-mcp-config"), classes="pe-field-hint"
                        )
                        yield TextArea(
                            p.mcp_extra_toml or "",
                            id="pe-mcp-extra",
                            language="toml",
                            classes="pe-medium",
                        )
                with TabPane(U.pe_tab_skills_title(), id="pe-tab-skills"):
                    with VerticalScroll(classes="pe-pane"):
                        yield Label(U.enabled_skill_names())
                        yield TextArea(
                            _ids_to_text(p.skills or []), id="pe-skills-ids", classes="pe-medium"
                        )
                        with Horizontal(id="pe-skills-actions", classes="pe-btn-row"):
                            yield Button(U.pick_skills(), id="pe-skills-pick")
                        yield Label(U.disabled_skills())
                        yield TextArea(
                            _ids_to_text(p.skills_disabled or []),
                            id="pe-skills-disabled",
                            classes="pe-medium",
                        )
                with TabPane(U.pe_tab_plugins_title(), id="pe-tab-plugins"):
                    with VerticalScroll(classes="pe-pane"):
                        yield Label(U.plugins_title())
                        yield TextArea(
                            _ids_to_text(p.plugins or []), id="pe-plugins-ids", classes="pe-medium"
                        )
                        with Horizontal(id="pe-plugins-actions", classes="pe-btn-row"):
                            yield Button(U.pick_plugins(), id="pe-plugins-pick")
            with Horizontal(id="pe-actions"):
                yield Button(U.save(), variant="primary", id="pe-save")
                yield Button(U.cancel(), id="pe-cancel")

    def on_mount(self) -> None:
        self.call_after_refresh(self._capture_clean_snapshot)

    def _pe_form_snapshot(self) -> tuple[str, ...]:
        with suppress(Exception):
            pid = self.query_one("#pe-id", Input).value
            name = self.query_one("#pe-name", Input).value
            desc = self.query_one("#pe-desc", Input).value
            notes = self.query_one("#pe-notes", TextArea).text
            try:
                docker = str(self.query_one("#pe-docker", Select).value)
            except Exception:
                docker = ""
            try:
                gh_write = bool(self.query_one("#pe-gh-write", Checkbox).value)
            except Exception:
                gh_write = False
            gh_token = self.query_one("#pe-gh-token", Input).value
            gh_env = self.query_one("#pe-gh-token-env", Input).value
            git_name = self.query_one("#pe-git-name", Input).value
            git_email = self.query_one("#pe-git-email", Input).value
            try:
                env_snap = tuple(
                    sorted(self.query_one("#pe-env", KeyValueEditor).get_values().items())
                )
            except Exception:
                env_snap = ()
            try:
                mcp_replace = bool(self.query_one("#pe-mcp-replace", Checkbox).value)
            except Exception:
                mcp_replace = True
            mcp_ids = self.query_one("#pe-mcp-ids", TextArea).text
            mcp_extra = self.query_one("#pe-mcp-extra", TextArea).text
            skills = self.query_one("#pe-skills-ids", TextArea).text
            skills_dis = self.query_one("#pe-skills-disabled", TextArea).text
            plugins = self.query_one("#pe-plugins-ids", TextArea).text
            mcp_defs = tuple(
                sorted(
                    (str(d.get("id") or ""), str(d.get("transport") or ""))
                    for d in (self._mcp_definitions or [])
                    if isinstance(d, dict)
                )
            )
            return tuple(
                str(x)
                for x in (
                    pid,
                    name,
                    desc,
                    notes,
                    docker,
                    gh_write,
                    gh_token,
                    gh_env,
                    git_name,
                    git_email,
                    env_snap,
                    mcp_replace,
                    mcp_ids,
                    mcp_extra,
                    skills,
                    skills_dis,
                    plugins,
                    mcp_defs,
                )
            )
        return ()

    def _capture_clean_snapshot(self) -> None:
        self._clean_snapshot = self._pe_form_snapshot()

    def form_is_dirty(self) -> bool:
        if self._clean_snapshot is None:
            return False
        return self._pe_form_snapshot() != self._clean_snapshot

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_save(self) -> None:
        self._do_save()

    @on(Button.Pressed, "#pe-cancel")
    def _cancel_btn(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    @on(Button.Pressed, "#pe-save")
    def _save_btn(self) -> None:
        self._do_save()

    @on(Button.Pressed, "#pe-mcp-pick")
    def _mcp_pick_btn(self) -> None:
        current = _ids_from_text(self.query_one("#pe-mcp-ids", TextArea).text)

        def _done(result: McpPickerResult | None) -> None:
            if result is None:
                return
            ids, defs, env_add, skills_add = result
            self._mcp_definitions = list(defs or [])
            with suppress(Exception):
                self.query_one("#pe-mcp-ids", TextArea).load_text(_ids_to_text(ids))
            if env_add:
                with suppress(Exception):
                    env_ed = self.query_one("#pe-env", KeyValueEditor)
                    existing = env_ed.get_values()
                    for k, v in env_add.items():
                        if not k:
                            continue
                        if k not in existing or not (existing.get(k) or "").strip():
                            existing[k] = v
                    env_ed.set_values(existing)
            if skills_add:
                with suppress(Exception):
                    sk_area = self.query_one("#pe-skills-ids", TextArea)
                    existing_sk = _ids_from_text(sk_area.text)
                    for s in skills_add:
                        if s and s not in existing_sk:
                            existing_sk.append(s)
                    sk_area.load_text(_ids_to_text(existing_sk))

        self.app.push_screen(McpPickerModal(self.work_dir, current, self._mcp_definitions), _done)

    @on(Button.Pressed, "#pe-skills-pick")
    def _skills_pick_btn(self) -> None:
        current = _ids_from_text(self.query_one("#pe-skills-ids", TextArea).text)

        def _done(result: list[str] | None) -> None:
            if result is None:
                return
            with suppress(Exception):
                self.query_one("#pe-skills-ids", TextArea).load_text(_ids_to_text(result))

        self.app.push_screen(SkillsPickerModal(self.work_dir, current), _done)

    @on(Button.Pressed, "#pe-plugins-pick")
    def _pe_plugins_pick(self) -> None:
        current = _ids_from_text(self.query_one("#pe-plugins-ids", TextArea).text)

        def _done(result: list[str] | None) -> None:
            if result is None:
                return
            self.query_one("#pe-plugins-ids", TextArea).load_text("\n".join(result))

        self.app.push_screen(PluginPickerModal(self.work_dir, current), _done)

    @on(Input.Changed, "#pe-id")
    def _id_changed(self, event: Input.Changed) -> None:
        if not self._is_new:
            return
        with suppress(Exception):
            name_in = self.query_one("#pe-name", Input)
            if not (name_in.value or "").strip():
                slug = _slug_id(event.value)
                if slug and slug != "persona":
                    name_in.placeholder = slug

    def _do_save(self) -> None:
        if self._is_new:
            raw_id = self.query_one("#pe-id", Input).value.strip()
            if not raw_id:
                self.notify(U.persona_id_required(), severity="error")
                return
            pid = _slug_id(raw_id)
            if not pid:
                self.notify(U.persona_id_invalid(), severity="error")
                return
            if pid == PERSONA_NONE or pid.startswith("__"):
                self.notify(U.persona_id_reserved(), severity="error")
                return
            existing = self._store.get(pid)
            if existing:
                self.notify(U.persona_exists(pid), severity="error")
                return
        else:
            pid = self._persona.persona_id
        name = self.query_one("#pe-name", Input).value.strip() or pid
        desc = self.query_one("#pe-desc", Input).value.strip()
        try:
            gh = bool(self.query_one("#pe-gh-write", Checkbox).value)
        except Exception:
            gh = False
        new_tok = self.query_one("#pe-gh-token", Input).value.strip()
        if new_tok:
            gh_token = new_tok
        elif not self._is_new:
            gh_token = (self._persona.github_token or "").strip()
        else:
            gh_token = ""
        gh_token_env = self.query_one("#pe-gh-token-env", Input).value.strip()
        try:
            docker_sel = str(self.query_one("#pe-docker", Select).value)
        except Exception:
            docker_sel = PERSONA_NONE
        docker_image = _persona_docker_stored(docker_sel)
        git_name = self.query_one("#pe-git-name", Input).value.strip()
        git_email = self.query_one("#pe-git-email", Input).value.strip()
        env_vars = self.query_one("#pe-env", KeyValueEditor).get_values()
        notes = self.query_one("#pe-notes", TextArea).text.strip()
        try:
            mcp_replace = bool(self.query_one("#pe-mcp-replace", Checkbox).value)
        except Exception:
            mcp_replace = True
        mcp_servers = _ids_from_text(self.query_one("#pe-mcp-ids", TextArea).text)
        mcp_extra = self.query_one("#pe-mcp-extra", TextArea).text.strip()
        skills = _ids_from_text(self.query_one("#pe-skills-ids", TextArea).text)
        skills_disabled = _ids_from_text(self.query_one("#pe-skills-disabled", TextArea).text)
        plugins = _ids_from_text(self.query_one("#pe-plugins-ids", TextArea).text)
        persona = Persona(
            persona_id=pid,
            name=name,
            description=desc,
            github_write=gh,
            github_token=gh_token,
            github_token_env=gh_token_env,
            env_vars=env_vars,
            docker_image=docker_image,
            git_user_name=git_name,
            git_user_email=git_email,
            mcp_servers=mcp_servers,
            mcp_definitions=list(self._mcp_definitions),
            mcp_replace_host=mcp_replace,
            mcp_extra_toml=mcp_extra,
            skills=skills,
            skills_disabled=skills_disabled,
            plugins=plugins,
            notes=notes,
            created_at=self._persona.created_at if not self._is_new else "",
        )
        try:
            self._store.save(persona)
        except Exception as exc:
            self.notify(U.save_failed(str(exc)), severity="error")
            return
        self.dismiss(persona)


class PersonasScreen(ChromeActions):
    """Browse and manage personas for the current work_dir."""

    BINDINGS = list(PERSONAS)

    class PersonasChanged(Message):
        """Bubble when store changes so Runner can refresh its dropdown."""

        pass

    def __init__(self, work_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.work_dir = Path(work_dir)
        self._store = PersonaStore(self.work_dir)
        self._rows: list[Persona] = []
        self._selected_id: str | None = None
        self._delete_pending_ids: list[str] | None = None

    def compose(self) -> ComposeResult:
        root = personas_dir(self.work_dir)
        from ..brand_mark import AppChrome, AppFooter

        yield AppChrome()
        with Vertical(id="personas-screen"):
            yield Static(
                join_ui(t("ui-persona-builder"), root, t("ui-github-mcp-skills-footer-for-keys")),
                id="pb-banner",
            )
            yield DataTable(id="pb-table")
            yield TipSurface(U.tip_no_personas(), id="pb-empty-tip")
            yield Static("", id="pb-detail")
            with Horizontal(id="pb-actions"):
                yield Button(U.new_label(), variant="primary", id="pb-new")
                yield Button(U.edit(), id="pb-edit")
                yield Button(U.delete(), variant="error", id="pb-delete")
                yield Button(U.open_folder_path(), id="pb-path")
        yield AppFooter()

    def on_mount(self) -> None:
        self._store.ensure_defaults()
        table = self.query_one("#pb-table", DataTable)
        style_data_table(table)
        table.add_columns(
            t("ui-id"),
            t("ui-name"),
            t("ui-gh-write"),
            t("ui-mcp"),
            t("ui-skills-1"),
            t("ui-docker-1"),
            t("ui-env"),
        )
        self._reload_table()

    def action_refresh_context(self) -> None:
        self._reload_table()
        self.notify(U.personas_refreshed(), timeout=2)

    def _reload_table(self) -> None:
        table = self.query_one("#pb-table", DataTable)
        prefer = self._selected_id or cursor_row_key(table)
        with preserving_cursor(table):
            table.clear()
            self._rows = self._store.list()
            for p in self._rows:
                env_n = len(p.env_vars or {})
                mcp_n = len(getattr(p, "mcp_servers", None) or [])
                sk_n = len(getattr(p, "skills", None) or [])
                table.add_row(
                    p.persona_id,
                    (p.name or p.persona_id)[:32],
                    "yes" if p.github_write else "no",
                    str(mcp_n) if mcp_n else "—",
                    str(sk_n) if sk_n else "—",
                    (p.docker_image or "—")[:24],
                    str(env_n),
                    key=p.persona_id,
                )
        if self._rows:
            if prefer and any(r.persona_id == prefer for r in self._rows):
                self._selected_id = prefer
                restore_cursor(table, prefer)
            elif self._selected_id and any(r.persona_id == self._selected_id for r in self._rows):
                restore_cursor(table, self._selected_id)
            else:
                self._selected_id = self._rows[0].persona_id
                restore_cursor(table, self._selected_id)
            self._show_detail(self._selected_id)
        else:
            self._selected_id = None
            self.query_one("#pb-detail", Static).update("")
            with suppress(Exception):
                self.query_one("#pb-empty-tip", TipSurface).set_tip(U.tip_no_personas())
        if self._rows:
            with suppress(Exception):
                self.query_one("#pb-empty-tip", TipSurface).clear_message()
        focus_primary_list(table)

    def _persona_at_cursor(self) -> Persona | None:
        table = self.query_one("#pb-table", DataTable)
        pid = cursor_row_key(table) or self._selected_id or ""
        if not pid:
            return None
        return self._store.get(pid)

    def _show_detail(self, persona_id: str | None) -> None:
        if not persona_id:
            self.query_one("#pb-detail", Static).update("")
            return
        p = self._store.get(persona_id)
        if not p:
            self.query_one("#pb-detail", Static).update("")
            return
        env_preview = ", ".join(sorted((p.env_vars or {}).keys())[:8]) or "—"
        if len(p.env_vars or {}) > 8:
            env_preview += "…"
        if (p.github_token or "").strip():
            tok_info = t(
                "ui-persona-token-stored",
                n=len(p.github_token.strip()),
            )
        elif (p.github_token_env or "").strip():
            tok_info = t("ui-persona-token-host-env", name=p.github_token_env)
        else:
            tok_info = t("ui-none-host-groket-gh-token-gh-token-only-if-orche")
        mcp_ids = getattr(p, "mcp_servers", None) or []
        skill_ids = getattr(p, "skills", None) or []
        lines = [
            f"[bold]{p.name}[/bold]  [dim]{p.persona_id}[/dim]",
            p.description or t("ui-no-description"),
            t(
                "ui-persona-github-line",
                write=("on" if p.github_write else "off"),
                token=tok_info,
                docker=p.docker_image or "inherit",
            ),
            t(
                "ui-persona-git-line",
                name=p.git_user_name or "—",
                email=p.git_user_email or "—",
            ),
            t("ui-persona-env-line", keys=env_preview),
            t(
                "ui-persona-mcp-line",
                n=len(mcp_ids),
                ids=", ".join(mcp_ids) or "—",
                replace=getattr(p, "mcp_replace_host", True),
            ),
            t(
                "ui-persona-skills-line",
                n=len(skill_ids),
                ids=", ".join(skill_ids) or "—",
            ),
        ]
        if p.notes:
            lines.append(t("ui-persona-notes-line", notes=p.notes[:200]))
        self.query_one("#pb-detail", Static).update("\n".join(lines))

    @on(DataTable.RowHighlighted, "#pb-table")
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        try:
            rk = event.row_key
            pid = str(rk.value if hasattr(rk, "value") else rk)
        except Exception:
            return
        self._selected_id = pid
        self._show_detail(pid)

    @on(DataTable.RowSelected, "#pb-table")
    def _row_selected(self, _event: DataTable.RowSelected) -> None:
        self.action_edit_persona()

    def action_new_persona(self) -> None:
        self._open_editor(None, is_new=True)

    def action_edit_persona(self) -> None:
        p = self._persona_at_cursor()
        if not p:
            self.notify(U.select_persona_first(), severity="warning")
            return
        self._open_editor(p, is_new=False)

    def action_delete_persona(self) -> None:
        """Delete persona at cursor. Requires second ``x`` (same as sessions/configs)."""
        from ..delete_confirm import second_press_armed

        p = self._persona_at_cursor()
        if not p:
            self.notify(U.select_persona_first(), severity="warning")
            return
        commit, pending = second_press_armed(self._delete_pending_ids, [p.persona_id])
        if not commit:
            self._delete_pending_ids = pending
            self.notify(
                t("ui-press-again-to-delete-persona") + p.persona_id,
                severity="warning",
                timeout=10,
            )
            return
        self._delete_pending_ids = None
        ok = self._store.delete(p.persona_id)
        if ok:
            self.notify(U.deleted_persona(p.persona_id), severity="information")
            self.post_message(self.PersonasChanged())
            self._reload_table()
        else:
            self.notify(U.delete_failed(), severity="error")

    def _open_editor(self, persona: Persona | None, *, is_new: bool) -> None:

        def _done(result: Persona | None) -> None:
            if result is None:
                return
            self.notify(
                t("persona-saved", pid=result.persona_id), severity="information", timeout=5
            )
            self._selected_id = result.persona_id
            self.post_message(self.PersonasChanged())
            self._reload_table()

        self.app.push_screen(
            PersonaEditorModal(self.work_dir, persona=persona, is_new=is_new), _done
        )

    @on(Button.Pressed, "#pb-new")
    def _btn_new(self) -> None:
        self.action_new_persona()

    @on(Button.Pressed, "#pb-edit")
    def _btn_edit(self) -> None:
        self.action_edit_persona()

    @on(Button.Pressed, "#pb-delete")
    def _btn_delete(self) -> None:
        self.action_delete_persona()

    @on(Button.Pressed, "#pb-path")
    def _btn_path(self) -> None:
        self.notify(str(personas_dir(self.work_dir)), severity="information", timeout=12)
