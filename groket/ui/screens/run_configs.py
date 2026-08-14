"""Browse / run / delete saved evaluation run configs (recipes, not sessions)."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Label, Select, SelectionList, Static

from groket.models import JsonValue

from ...runs.batch import load_models
from ...runs.run_configs import RunConfig, RunConfigStore
from ...runs.run_manager import BackgroundRun, RunManager
from ..bindings import MODAL_CANCEL_QUIT, RUN_CONFIGS, ChromeActions
from ..data_table import (
    cursor_row_key,
    restore_cursor,
    selection_mark,
    set_selection_marker,
    style_data_table,
)
from ..forms import (
    batch_parallel_options,
    load_active_model_ids,
    model_selection_items,
    normalize_batch_parallel,
    select_value_str,
    selection_list_selected_ids,
)
from ..i18n import join_ui, t
from ..quit_actions import QuitActions
from .runner import RunnerScreen

_DEFAULT_BATCH_PARALLEL = 2


class _ModelsOverrideModal(QuitActions, ModalScreen[object]):
    """Pick models (multi-select) before launching a saved config."""

    BINDINGS = [
        *MODAL_CANCEL_QUIT,
        Binding("ctrl+r", "submit", t("ui-run-1"), id="modal.submit"),
    ]

    def __init__(self, default_models: list[str], title: str = "Models for this launch") -> None:
        super().__init__()
        self._default = default_models
        self._title = title

    def compose(self) -> ComposeResult:
        initial = self._default if self._default else load_active_model_ids()
        with Vertical(id="models-override-modal"):
            yield Static(f"[bold]{self._title}[/bold]\n", id="mom-title")
            yield Label(t("ui-models-select-one-or-more-one-container-each"))
            yield SelectionList[str](*model_selection_items(initial), id="mom-models")
            with Horizontal(id="mom-footer", classes="modal-footer"):
                yield Button("launch", variant="primary", id="mom-ok")
                yield Button("cancel", id="mom-cancel")

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_submit(self) -> None:
        self._ok()

    @on(Button.Pressed, "#mom-cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#mom-ok")
    def _ok_btn(self) -> None:
        self._ok()

    def _ok(self) -> None:
        models = selection_list_selected_ids(self.query_one("#mom-models", SelectionList))
        if not models:
            self.notify(t("ui-select-at-least-one-model"), severity="error")
            return
        self.dismiss(models)


class _BatchLaunchModal(QuitActions, ModalScreen[object]):
    """Configure models + max in-flight configs for a multi-config launch."""

    BINDINGS = [
        *MODAL_CANCEL_QUIT,
        Binding("ctrl+r", "submit", t("ui-launch-selected"), id="modal.submit"),
        Binding("enter", "submit", t("ui-launch-selected"), id="modal.submit_enter", show=False),
    ]

    def __init__(
        self,
        configs: list[RunConfig],
        default_models: list[str],
        default_parallel: int = _DEFAULT_BATCH_PARALLEL,
    ) -> None:
        super().__init__()
        self._configs = list(configs)
        self._default_models = default_models
        self._default_parallel = max(1, int(default_parallel or _DEFAULT_BATCH_PARALLEL))

    def compose(self) -> ComposeResult:
        n = len(self._configs)
        names = ", ".join((c.task_id or c.display_name())[:24] for c in self._configs[:6])
        if n > 6:
            names += f" … (+{n - 6})"
        with Vertical(id="batch-launch-modal"):
            yield Static(t("ui-blm-title", n=n, names=names), id="blm-title")
            yield Label(t("ui-models-optional-override-leave-none-selected-to"))
            yield SelectionList[str](
                *model_selection_items(
                    self._default_models or [],
                    catalog=load_active_model_ids(),
                    default_select_all=False,
                ),
                id="blm-models",
            )
            yield Static(
                t("ui-space-click-to-select-no-selection-per-config-mo"), id="blm-models-hint"
            )
            yield Label(t("ui-max-configs-in-flight-at-once-each-config-still"))
            par_val = str(normalize_batch_parallel(self._default_parallel))
            yield Select(
                options=batch_parallel_options(),
                value=par_val,
                id="blm-parallel",
                allow_blank=False,
            )
            with Horizontal(id="blm-footer", classes="modal-footer"):
                yield Button(t("ui-launch-selected-2"), variant="primary", id="blm-ok")
                yield Button("cancel", id="blm-cancel")

    def action_cancel(self) -> None:
        from ..bindings import dismiss_after_blur

        dismiss_after_blur(self, None)

    def action_submit(self) -> None:
        self._ok()

    @on(Button.Pressed, "#blm-cancel")
    def _cancel_btn(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#blm-ok")
    def _ok_btn(self) -> None:
        self._ok()

    def _ok(self) -> None:
        models_override = selection_list_selected_ids(self.query_one("#blm-models", SelectionList))
        try:
            par_raw = select_value_str(
                self.query_one("#blm-parallel", Select).value, default=str(self._default_parallel)
            )
            parallel = normalize_batch_parallel(par_raw, self._default_parallel)
        except Exception:
            parallel = normalize_batch_parallel(self._default_parallel)
        defaults = load_models()
        for c in self._configs:
            models = models_override or list(c.models) or defaults
            if not models:
                self.notify(
                    join_ui(
                        t("ui-no-models-for"),
                        c.task_id or c.display_name(),
                        t("ui-set-models-above"),
                    ),
                    severity="error",
                )
                return
        self.dismiss({"models_override": models_override, "max_parallel": parallel})


class RunConfigsScreen(ChromeActions):
    """List saved run configs; multi-select + batch launch; single launch/edit/delete."""

    BINDINGS = list(RUN_CONFIGS)

    def __init__(self, work_dir: Path, run_manager: RunManager | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.work_dir = Path(work_dir)
        self._run_manager = run_manager
        self._store = RunConfigStore(self.work_dir)
        self._rows: list[RunConfig] = []
        self._selected_id: str | None = None
        self._selected: set[str] = set()
        self._delete_pending_ids: list[str] | None = None

    @property
    def run_manager(self) -> RunManager:
        if self._run_manager is not None:
            return self._run_manager
        app_rm = getattr(self.app, "run_manager", None)
        if app_rm is not None:
            return app_rm
        return RunManager(self.work_dir)

    def _app_config_get(self, key: str, default: JsonValue = None) -> JsonValue:
        cfg = getattr(self.app, "_config", None)
        if isinstance(cfg, dict):
            return cfg.get(key, default)
        return default

    def _app_config_set(self, key: str, value: JsonValue) -> None:
        app = self.app
        cfg = getattr(app, "_config", None)
        if not isinstance(cfg, dict):
            return
        cfg[key] = value
        save = getattr(app, "_save_config", None)
        if callable(save):
            with suppress(Exception):
                save()

    def compose(self) -> ComposeResult:
        from ..brand_mark import AppChrome, AppFooter

        yield AppChrome()
        with Vertical():
            yield Static(t("ui-saved-run-configs-recipes-in-runs-run-configs-no"), id="rc-banner")
            yield Static("", id="rc-selection-bar")
            yield DataTable(id="rc-table")
            yield Static("", id="rc-detail")
            with Horizontal(id="rc-actions"):
                yield Button(t("ui-open-in-runner"), variant="primary", id="rc-open")
                yield Button(t("ui-launch-pick-models"), variant="success", id="rc-launch")
                yield Button(t("ui-launch-selected-2"), variant="warning", id="rc-batch")
                yield Button(t("ui-delete-config"), variant="error", id="rc-delete")
                yield Button(t("ui-new-in-runner"), id="rc-new")
        yield AppFooter()

    def on_mount(self) -> None:
        table = self.query_one("#rc-table", DataTable)
        style_data_table(table)
        table.add_columns(
            t("ui-sel"),
            t("ui-label"),
            t("ui-name"),
            t("ui-category-1"),
            t("ui-repo"),
            t("ui-models"),
            t("ui-launches"),
            t("ui-id-2"),
        )
        self._reload_table()

    def action_refresh_context(self) -> None:
        """Reload configs from disk only."""
        self._reload_table()
        self.notify(
            join_ui(t("ui-configs-reloaded"), len(self._rows), t("ui-recipes")),
            severity="information",
            timeout=5,
        )

    def _cursor_config_id(self) -> str | None:
        return cursor_row_key(self.query_one("#rc-table", DataTable))

    def _refresh_selection_markers(self, table: DataTable | None = None) -> None:
        table = table or self.query_one("#rc-table", DataTable)
        for rk in table.rows.keys():
            cid = str(rk.value)
            set_selection_marker(table, cid, cid in self._selected)

    def _reload_table(self, *, preserve_cursor: bool = True) -> None:
        table = self.query_one("#rc-table", DataTable)
        keep_key = cursor_row_key(table) if preserve_cursor else None
        table.clear()
        self._rows = self._store.list_configs()
        live_ids = {c.config_id for c in self._rows}
        self._selected &= live_ids

        def _cfg_ts(c) -> str:
            return c.updated_at or c.last_launched_at or c.created_at or ""

        self._rows.sort(
            key=lambda c: (_cfg_ts(c), c.task_id or c.name or c.config_id), reverse=True
        )
        for cfg in self._rows:
            models = ", ".join(cfg.models[:2])
            if len(cfg.models) > 2:
                models += "…"
            if (cfg.repo_path or "").strip():
                repo = Path(cfg.repo_path).expanduser().name or "local"
            else:
                repo = (cfg.repo_url or "").split("/")[-1] if cfg.repo_url else "—"
            table.add_row(
                selection_mark(cfg.config_id in self._selected),
                cfg.catalog_label()[:18],
                (cfg.task_id or cfg.display_name())[:32],
                (cfg.category or "—")[:16],
                repo[:16],
                models[:36] or "—",
                str(cfg.launch_count),
                cfg.config_id[:14],
                key=cfg.config_id,
            )
        if keep_key:
            restore_cursor(table, keep_key)
        self._update_selection_bar()
        if not self._rows:
            self.query_one("#rc-detail", Static).update(
                t("ui-no-saved-configs-yet-save-a-recipe-from-the-runn")
            )
        else:
            self._show_detail_for_cursor()

    def _update_selection_bar(self) -> None:
        n = len(self._selected)
        try:
            bar = self.query_one("#rc-selection-bar", Static)
        except Exception:
            return
        if n == 0:
            bar.update(t("ui-selection-none-s-toggle-row-s-all-none-w-launche"))
        else:
            labels: list[str] = []
            by_id = {c.config_id: c for c in self._rows}
            for cid in list(self._selected)[:8]:
                c = by_id.get(cid)
                labels.append((c.task_id or c.display_name())[:20] if c else cid[:10])
            extra = f" …+{n - 8}" if n > 8 else ""
            bar.update(
                t(
                    "ui-selection-bar",
                    n=n,
                    labels=", ".join(labels),
                    extra=extra,
                )
            )

    def _configs_for_batch(self) -> list[RunConfig]:
        """Selected configs in table order; if none selected, cursor row only."""
        if self._selected:
            by_id = {c.config_id: c for c in self._rows}
            out: list[RunConfig] = []
            for c in self._rows:
                if c.config_id in self._selected and c.config_id in by_id:
                    out.append(c)
            return out
        cur = self._current_config()
        return [cur] if cur else []

    def _current_config(self) -> RunConfig | None:
        table = self.query_one("#rc-table", DataTable)
        if not self._rows:
            return None
        with suppress(Exception):
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
            if row_key is not None and row_key.value:
                cid = str(row_key.value)
                for c in self._rows:
                    if c.config_id == cid:
                        return c
        with suppress(Exception):
            idx = table.cursor_row
            if 0 <= idx < len(self._rows):
                return self._rows[idx]
        return self._rows[0] if self._rows else None

    def _show_detail_for_cursor(self) -> None:
        cfg = self._current_config()
        if not cfg:
            return
        self._selected_id = cfg.config_id
        sel = t("ui-selected-4") if cfg.config_id in self._selected else ""
        lines = [
            t(
                "ui-config-detail-title",
                name=cfg.display_name(),
                id=cfg.config_id,
                sel=sel,
            )
        ]
        if cfg.task_id or cfg.category or cfg.label:
            lines.append(
                join_ui(
                    t("ui-label-1"),
                    cfg.catalog_label(),
                    t("ui-task-1"),
                    cfg.task_id or "—",
                    t("ui-category-2"),
                    cfg.category or "—",
                )
            )
        lines.extend(
            [
                join_ui(
                    t("ui-repo-1"),
                    cfg.repo_url or "(none)",
                    t("ui-branch"),
                    cfg.repo_branch or "—",
                    t("ui-repo-path-1"),
                    cfg.repo_path or "—",
                ),
                join_ui(
                    t("ui-image"),
                    cfg.docker_image,
                    t("ui-models-1"),
                    ", ".join(cfg.models) or t("ui-none-saved"),
                ),
                join_ui(
                    t("ui-parallelism"),
                    cfg.parallelism,
                    t("ui-launches-1"),
                    cfg.launch_count,
                    t("ui-last"),
                    cfg.last_launched_at or "never",
                ),
            ]
        )
        if cfg.source_session_id:
            lines.append(join_ui(t("ui-source-session"), cfg.source_session_id))
        if cfg.source_run_id:
            lines.append(join_ui(t("ui-source-run-id"), cfg.source_run_id))
        if (cfg.persona_id or "").strip():
            lines.append(join_ui(t("ui-persona-2"), cfg.persona_id))
        if cfg.run_plugins:
            lines.append(t("run-config-plugins", list=", ".join(cfg.run_plugins) or "—"))
        if cfg.run_skills:
            lines.append(t("run-config-skills", list=", ".join(cfg.run_skills) or "—"))
        if cfg.run_mcp_servers:
            lines.append(t("run-config-mcp", list=", ".join(cfg.run_mcp_servers) or "—"))
        inline_ids = [
            str(x.get("id") or "").strip()
            for x in (cfg.run_inline_skills or [])
            if isinstance(x, dict) and str(x.get("id") or "").strip()
        ]
        if inline_ids:
            lines.append(t("run-config-inline-skills", list=", ".join(inline_ids)))
        lines.append(join_ui(t("ui-prompt"), cfg.prompt_preview(120)))
        if cfg.setup_instructions:
            su = cfg.setup_instructions.replace("\n", " ")[:100]
            lines.append(join_ui(t("ui-setup"), su))
        self.query_one("#rc-detail", Static).update("\n".join(lines))

    @on(DataTable.RowHighlighted, "#rc-table")
    def _on_highlight(self, event: DataTable.RowHighlighted) -> None:
        self._show_detail_for_cursor()

    @on(DataTable.RowSelected, "#rc-table")
    def _on_select(self, event: DataTable.RowSelected) -> None:
        self.action_open_in_runner()

    def action_toggle_select(self) -> None:
        """Toggle multi-select in-place (no table rebuild → cursor stays)."""
        cfg = self._current_config()
        if not cfg:
            self.notify(t("ui-no-config-under-cursor"), severity="warning")
            return
        cid = cfg.config_id
        table = self.query_one("#rc-table", DataTable)
        if cid in self._selected:
            self._selected.discard(cid)
            now_on = False
        else:
            self._selected.add(cid)
            now_on = True
        set_selection_marker(table, cid, now_on)
        self._update_selection_bar()
        self._show_detail_for_cursor()

    def action_select_all_toggle(self) -> None:
        if not self._rows:
            return
        table = self.query_one("#rc-table", DataTable)
        keep_key = self._cursor_config_id()
        all_ids = {c.config_id for c in self._rows}
        if self._selected >= all_ids:
            self._selected.clear()
        else:
            self._selected = set(all_ids)
        self._refresh_selection_markers(table)
        if keep_key:
            restore_cursor(table, keep_key)
        self._update_selection_bar()
        self._show_detail_for_cursor()

    def action_export_task_yaml(self) -> None:
        """Export the highlighted recipe as a batch tasks YAML (choose path)."""
        cfg = self._current_config()
        if cfg is None:
            self.notify(t("export-task-no-config"), severity="warning")
            return
        if not (cfg.prompt or "").strip():
            self.notify(t("export-task-no-prompt"), severity="error")
            return
        from ...runs.task_export import (
            default_task_export_path,
            source_from_run_config,
            write_task_export,
        )
        from ..path_input_modal import PathInputModal

        src = source_from_run_config(cfg)
        initial = str(default_task_export_path(src.task_id))

        def _done(path_raw: str | None) -> None:
            if not path_raw:
                return
            try:
                written = write_task_export(Path(path_raw), src)
            except Exception as exc:
                self.notify(t("export-task-failed", exc=str(exc)), severity="error")
                return
            self.notify(t("export-task-saved", path=str(written)), severity="information")

        self.app.push_screen(
            PathInputModal(
                title=t("export-task-title"),
                initial=initial,
                placeholder=t("export-task-placeholder"),
                hint=t("export-task-hint"),
            ),
            _done,
        )

    def action_new_blank(self) -> None:
        self.app.pop_screen()
        self.app.push_screen(RunnerScreen(self.work_dir, run_manager=self.run_manager))

    def action_open_in_runner(self) -> None:
        cfg = self._current_config()
        if not cfg:
            self.notify(t("ui-no-config-selected"), severity="warning")
            return
        prefill = cfg.to_runner_prefill()
        self.app.pop_screen()
        self.app.push_screen(
            RunnerScreen(
                self.work_dir,
                run_manager=self.run_manager,
                prefill=prefill,
                config_id=cfg.config_id,
                config_name=cfg.name,
            )
        )

    def action_launch_config(self) -> None:
        cfg = self._current_config()
        if not cfg:
            self.notify(t("ui-no-config-selected"), severity="warning")
            return
        defaults = list(cfg.models) if cfg.models else load_models()

        def _after(result: object) -> None:
            if result is None:
                return
            if isinstance(result, list):
                models = result
            else:
                return
            self._do_launch(cfg, list(models))

        self.app.push_screen(
            _ModelsOverrideModal(defaults, join_ui(t("ui-launch"), cfg.display_name())), _after
        )

    def action_launch_selected(self) -> None:
        configs = self._configs_for_batch()
        if not configs:
            self.notify(
                t("ui-select-configs-with-s-space-or-cursor-on-one-the"), severity="warning"
            )
            return
        default_models: list[str] = []
        for c in configs:
            if c.models:
                default_models = list(c.models)
                break
        if not default_models:
            default_models = load_models()
        par_raw = self._app_config_get("batch_parallelism", _DEFAULT_BATCH_PARALLEL)
        default_par = (
            int(par_raw) if isinstance(par_raw, (int, float, str)) else _DEFAULT_BATCH_PARALLEL
        )

        def _after(result: object) -> None:
            if result is None or not isinstance(result, dict):
                return
            models_override = list(result.get("models_override") or [])
            max_parallel = int(result.get("max_parallel") or _DEFAULT_BATCH_PARALLEL)
            self._app_config_set("batch_parallelism", max_parallel)
            self._do_launch_batch(configs, models_override, max_parallel)

        self.app.push_screen(
            _BatchLaunchModal(configs, default_models, default_parallel=default_par), _after
        )

    def _do_launch(self, cfg: RunConfig, models: list[str]) -> None:
        auth_json = Path.home() / ".grok" / "auth.json"
        grok_config = Path.home() / ".grok" / "config.toml"
        if not auth_json.exists():
            self.notify(join_ui(t("ui-auth-missing"), auth_json), severity="error")
            return
        try:
            from ...runs.batch import validate_models_for_launch

            models, skips = validate_models_for_launch(list(models))
            for msg in skips[:4]:
                self.notify(msg[:200], severity="warning", timeout=10)
            if not models:
                self.notify(
                    t("ui-no-active-models-check-config-models-vs-grok-mod"), severity="error"
                )
                return
        except Exception:
            pass
        try:
            bg = self.run_manager.start_run(
                prompt=cfg.prompt,
                setup_instructions=cfg.setup_instructions,
                docker_image=cfg.docker_image or "fully-loaded",
                models=models,
                parallelism=1,
                repo_url=cfg.repo_url,
                repo_branch=cfg.repo_branch,
                repo_path=str(getattr(cfg, "repo_path", "") or ""),
                auth_json=auth_json,
                grok_config=grok_config,
                prune_exited=True,
                save_config=True,
                config_name=cfg.name,
                existing_config_id=cfg.config_id,
                persona_id=str(getattr(cfg, "persona_id", "") or ""),
                run_mcp_servers=list(cfg.run_mcp_servers or []),
                run_mcp_definitions=list(cfg.run_mcp_definitions or []),
                run_skills=list(cfg.run_skills or []),
                run_plugins=list(cfg.run_plugins or []),
                run_env_vars=dict(cfg.run_env_vars or {}),
                run_inline_skills=[
                    (str(x.get("id") or ""), str(x.get("content") or ""))
                    for x in (cfg.run_inline_skills or [])
                    if isinstance(x, dict) and str(x.get("id") or "").strip()
                ],
                max_turns=getattr(cfg, "max_turns", None),
                yolo=bool(getattr(cfg, "yolo", False)),
            )
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(
            join_ui(
                t("ui-launched"),
                bg.run_id,
                t("ui-with"),
                len(models),
                t("ui-model-s-from-config"),
                cfg.config_id,
            ),
            severity="information",
            timeout=8,
        )
        self._reload_table()

    def _do_launch_batch(
        self, configs: list[RunConfig], models_override: list[str], max_parallel: int
    ) -> None:
        auth_json = Path.home() / ".grok" / "auth.json"
        grok_config = Path.home() / ".grok" / "config.toml"
        if not auth_json.exists():
            self.notify(join_ui(t("ui-auth-missing"), auth_json), severity="error")
            return
        defaults = load_models()
        items: list[dict] = []
        for c in configs:
            models = list(models_override) if models_override else list(c.models) or list(defaults)
            if not models:
                self.notify(
                    join_ui(t("ui-skip"), c.config_id, t("ui-no-models")), severity="warning"
                )
                continue
            items.append(
                {
                    "prompt": c.prompt,
                    "setup_instructions": c.setup_instructions,
                    "docker_image": c.docker_image or "fully-loaded",
                    "models": models,
                    "parallelism": 1,
                    "repo_url": c.repo_url,
                    "repo_branch": c.repo_branch,
                    "repo_path": str(getattr(c, "repo_path", "") or ""),
                    "persona_id": str(getattr(c, "persona_id", "") or ""),
                    "run_mcp_servers": list(c.run_mcp_servers or []),
                    "run_mcp_definitions": list(c.run_mcp_definitions or []),
                    "run_skills": list(c.run_skills or []),
                    "run_plugins": list(c.run_plugins or []),
                    "run_env_vars": dict(c.run_env_vars or {}),
                    "run_inline_skills": list(c.run_inline_skills or []),
                    "config_name": c.name,
                    "existing_config_id": c.config_id,
                    "label": c.task_id or c.display_name() or c.config_id,
                    "max_turns": getattr(c, "max_turns", None),
                    "yolo": bool(getattr(c, "yolo", False)),
                }
            )
        if not items:
            self.notify(t("ui-nothing-to-launch-no-models"), severity="error")
            return
        screen = self
        batch_errors: list[tuple[str, str]] = []

        def _on_err(label: str, err: str) -> None:
            batch_errors.append((label, err))

        def _on_done(
            batch_id: str, started: list[BackgroundRun], errors: list[tuple[str, str]]
        ) -> None:
            all_err = list(errors) + list(batch_errors)

            def _ui() -> None:
                with suppress(Exception):
                    fn = getattr(screen.app, "_subtitle_run_status", None)
                    if callable(fn):
                        fn()
                with suppress(Exception):
                    screen._reload_table()
                msg = join_ui(
                    t("ui-batch-1"),
                    batch_id,
                    t("ui-finished-1"),
                    len(started),
                    t("ui-run-s-started"),
                    len(all_err),
                    t("ui-launch-error-s-open-j-for-jobs-logs"),
                )
                if all_err:
                    sample = all_err[0][1][:80]
                    # Build example without nested Rich tags (notify markup is fragile).
                    msg += "\n" + t(
                        "ui-batch-err-example",
                        id=all_err[0][0],
                        sample=sample,
                    )
                screen.notify(msg, severity="warning" if all_err else "information", timeout=14)

            from ..threads import call_ui

            call_ui(screen.app, _ui)

        try:
            batch_id = self.run_manager.start_batch(
                items,
                auth_json=auth_json,
                grok_config=grok_config,
                max_parallel=max_parallel,
                prune_exited=True,
                save_config=True,
                on_item_started=None,
                on_item_error=_on_err,
                on_batch_done=_on_done,
            )
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        with suppress(Exception):
            fn = getattr(self.app, "_subtitle_run_status", None)
            if callable(fn):
                fn()
        self.notify(
            join_ui(
                t("ui-batch-1"),
                batch_id,
                len(items),
                t("ui-config-s-max"),
                max_parallel,
                t("ui-in-flight-running-in-background-j-jobs-logs-no-p"),
            ),
            severity="information",
            timeout=8,
        )

    @staticmethod
    def _cursor_key_after_deletes(
        row_keys_in_order: list[str], cursor_key: str | None, gone: set[str]
    ) -> str | None:
        if not row_keys_in_order:
            return None
        remaining = [k for k in row_keys_in_order if k not in gone]
        if not remaining:
            return None
        if cursor_key and cursor_key not in gone and (cursor_key in remaining):
            return cursor_key
        try:
            idx = row_keys_in_order.index(cursor_key) if cursor_key else 0
        except ValueError:
            idx = 0
        for k in row_keys_in_order[idx + 1 :]:
            if k not in gone:
                return k
        for k in reversed(row_keys_in_order[:idx]):
            if k not in gone:
                return k
        return remaining[0]

    def action_delete_config(self) -> None:
        """Delete config(s): selected set if any, else cursor row. Requires second x."""
        targets: list[RunConfig] = []
        if self._selected:
            by_id = {c.config_id: c for c in self._rows}
            for c in self._rows:
                if c.config_id in self._selected and c.config_id in by_id:
                    targets.append(c)
        else:
            cur = self._current_config()
            if cur:
                targets = [cur]
        if not targets:
            self.notify(
                t("ui-highlight-a-config-or-select-with-s-then-x-to-de"), severity="warning"
            )
            return
        from ..delete_confirm import second_press_armed

        commit, pending = second_press_armed(
            self._delete_pending_ids, [c.config_id for c in targets]
        )
        if not commit:
            self._delete_pending_ids = pending
            names = ", ".join((c.task_id or c.display_name())[:24] for c in targets[:5])
            more = f" …+{len(targets) - 5}" if len(targets) > 5 else ""
            self.notify(
                join_ui(
                    t("ui-press-again-to-delete"),
                    len(targets),
                    t("ui-run-config-s-recipes-only-sessions-traces-kept"),
                    names,
                    more,
                ),
                severity="warning",
                timeout=10,
            )
            return
        self._delete_pending_ids = None
        table = self.query_one("#rc-table", DataTable)
        try:
            row_keys = [str(rk.value) for rk in table.rows.keys()]
        except Exception:
            row_keys = [c.config_id for c in self._rows]
        cursor_key = self._cursor_config_id() or targets[0].config_id
        gone = {c.config_id for c in targets}
        restore_key = self._cursor_key_after_deletes(row_keys, cursor_key, gone)
        deleted = 0
        failed = 0
        for c in targets:
            if self._store.delete(c.config_id):
                deleted += 1
                self._selected.discard(c.config_id)
            else:
                failed += 1
        if deleted:
            msg = join_ui(t("ui-deleted"), deleted, t("ui-run-config-s"))
            if failed:
                msg += t("ui-failed-paren", n=failed)
            self.notify(msg, severity="warning" if failed else "information", timeout=10)
            self._reload_table(preserve_cursor=False)
            if restore_key:
                restore_cursor(self.query_one("#rc-table", DataTable), restore_key)
            self._update_selection_bar()
            self._show_detail_for_cursor()
        else:
            self.notify(t("ui-delete-failed"), severity="error")

    @on(Button.Pressed, "#rc-open")
    def _btn_open(self) -> None:
        self.action_open_in_runner()

    @on(Button.Pressed, "#rc-launch")
    def _btn_launch(self) -> None:
        self.action_launch_config()

    @on(Button.Pressed, "#rc-batch")
    def _btn_batch(self) -> None:
        self.action_launch_selected()

    @on(Button.Pressed, "#rc-delete")
    def _btn_delete(self) -> None:
        self.action_delete_config()

    @on(Button.Pressed, "#rc-new")
    def _btn_new(self) -> None:
        self.action_new_blank()
