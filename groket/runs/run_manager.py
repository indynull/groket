"""App-level background evaluation run manager.

Keeps Docker orchestration alive after the Runner screen is dismissed (and after
the whole TUI exits): each launch is a daemon thread + `docker run -d`, so
closing the app does not cancel in-flight containers.

Multiple runs may be active at once. On each new launch we best-effort prune
exited containers (and optionally orphaned ones) so relaunch stays clean.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..capabilities import merge_capabilities
from ..constants import LOG_BUFFER_MAXLEN, LOG_TAIL_MAXLEN, MAX_RUN_HISTORY
from ..docker.orchestrator import ContainerConfig, ContainerStatus, DockerOrchestrator
from ..models import EvalRun, JsonObject, JsonValue, json_as_mapping_list, json_as_str_list
from ..session.models_catalog import split_model_effort
from .batch import resolve_model_ids, validate_models_for_launch
from .personas import PersonaStore
from .run_configs import RunConfigStore
from .services import LogBuffer

logger = logging.getLogger(__name__)

# Callback types (invoked from worker threads — consumers must marshal to UI thread)
StatusCallback = Callable[[ContainerStatus], None]
LogCallback = Callable[[str, str], None]
FinishedCallback = Callable[["BackgroundRun"], None]


@dataclass
class BackgroundRun:
    """State for one evaluation launch (may outlive the Runner screen / app)."""

    run_id: str
    eval_run: EvalRun
    configs: list[ContainerConfig]
    statuses: dict[str, ContainerStatus] = field(default_factory=dict)
    # Captured in background always; UI viewers call log_buffer.snapshot() when shown.
    log_buffer: LogBuffer = field(default_factory=lambda: LogBuffer(maxlen=LOG_BUFFER_MAXLEN))
    interactive: bool = False
    # Host path to sessions volume for this run (turn gate lives under ``.groket-turn/``).
    traces_vol: Path | None = None
    # Ring buffer mirror; UI viewers prefer log_buffer.snapshot().
    log_lines: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=LOG_TAIL_MAXLEN))
    results: list[ContainerStatus] = field(default_factory=list)
    persona_id: str = ""
    error: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    elapsed_s: float = 0.0
    # Quiet runs (e.g. multi-config batch members): no per-run UI toasts; progress in Jobs.
    quiet: bool = False
    batch_id: str = ""

    @property
    def is_running(self) -> bool:
        return self.eval_run.status == "running"

    @property
    def is_finished(self) -> bool:
        return self.eval_run.status in ("completed", "failed")

    @property
    def container_names(self) -> list[str]:
        return [c.container_name for c in self.configs]

    def append_log(self, source: str, text: str) -> None:
        """Thread-safe capture; does not require a UI listener."""
        self.log_buffer.append(source, text)
        self.log_lines.append((source, text))


class RunManager:
    """Owns orchestrator work on daemon threads; UI screens subscribe optionally.

    Supports **multiple concurrent runs**. Threads are daemon=True so exiting
    the TUI never blocks on Docker; containers keep running under dockerd.
    """

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.orchestrator = DockerOrchestrator(work_dir / "runs")
        self._lock = threading.Lock()
        # All in-flight runs keyed by run_id
        self._active: dict[str, BackgroundRun] = {}
        self._history: list[BackgroundRun] = []  # finished runs (newest last)
        self._threads: dict[str, threading.Thread] = {}
        # Live listeners (RunnerScreen registers while mounted)
        self._status_listeners: list[StatusCallback] = []
        self._log_listeners: list[LogCallback] = []
        self._finished_listeners: list[FinishedCallback] = []
        # Active multi-config batch ids; UI suppresses per-run toast spam.
        self._active_batches: set[str] = set()
        # Set on TUI quit so workers stop calling into the UI (avoids hang on exit).
        self._ui_detached = False
        # Cache for Docker-backed active run/session counts (TUI restart).
        self._docker_runs_cache_at: float = 0.0
        self._docker_runs_cache_n: int = 0
        self._docker_sessions_cache_at: float = 0.0
        self._docker_sessions_cache_n: int = 0
        # Best-effort prune of exited eval containers at manager creation
        try:
            self.orchestrator.prune_eval_containers(remove_exited=True, remove_running=False)
        except Exception:
            logger.debug("Failed to prune containers at startup", exc_info=True)

    def detach_ui(self) -> None:
        """Drop all UI listeners; in-flight Docker work continues under dockerd.

        Call from the app quit path so daemon workers do not block on
        ``call_from_thread`` while Textual is shutting down. Interactive
        containers keep running so the session can be resumed after reopen.
        Also aborts orchestrator wait loops so non-daemon Docker waits cannot
        pin the Python process after Textual exits.
        """
        with self._lock:
            self._ui_detached = True
            self._status_listeners.clear()
            self._log_listeners.clear()
            self._finished_listeners.clear()
            active = list(self._active.values())
        try:
            self.orchestrator.request_abort()
        except Exception:
            logger.debug("orchestrator request_abort on detach failed", exc_info=True)
        for bg in active:
            try:
                bg.log_buffer.clear_listeners()
            except Exception:
                try:
                    bg.log_buffer.enable_live_notify(False)
                except Exception:
                    pass

    @property
    def ui_detached(self) -> bool:
        with self._lock:
            return self._ui_detached

    @property
    def current(self) -> BackgroundRun | None:
        """Most recently started still-active run, else latest in history."""
        return self.latest()

    @property
    def is_running(self) -> bool:
        return self.active_count > 0

    @property
    def batch_active(self) -> bool:
        with self._lock:
            return bool(self._active_batches)

    @property
    def active_batch_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._active_batches)

    @property
    def active_count(self) -> int:
        """In-process running *launches*, or Docker-backed run count after restart."""
        with self._lock:
            in_proc = sum(1 for r in self._active.values() if r.is_running)
        if in_proc:
            return in_proc
        return self._docker_active_run_count()

    @property
    def active_session_count(self) -> int:
        """Running eval *sessions* (containers), not launches.

        One run with three models → three sessions while containers are up.
        Falls back to Docker container count after TUI restart.
        """
        with self._lock:
            names: set[str] = set()
            for bg in self._active.values():
                if bg.is_running:
                    names.update(bg.container_names)
            in_proc = len(names)
        if in_proc:
            return in_proc
        return self._docker_active_session_count()

    def active_status_counts(self) -> dict[str, int]:
        """Tally container lifecycle phases across in-flight launches.

        Keys match :class:`~groket.docker.orchestrator.ContainerStatus` /
        Jobs status column: ``pending``, ``building``, ``running``,
        ``extracting``, plus any other status string seen. Terminal states
        (``completed`` / ``failed``) are omitted. When a launch has no
        status entries yet, each configured container counts as ``pending``.
        """
        out: dict[str, int] = {}
        with self._lock:
            active = [bg for bg in self._active.values() if bg.is_running]
        for bg in active:
            statuses = bg.statuses or {}
            if statuses:
                for st in statuses.values():
                    key = (st.status or "pending").strip().lower() or "pending"
                    if key in ("completed", "failed", "idle"):
                        continue
                    out[key] = out.get(key, 0) + 1
            else:
                n = len(bg.configs) if bg.configs else 1
                out["pending"] = out.get("pending", 0) + n
        return out

    def _docker_active_run_count(self) -> int:
        """Cached count of distinct ``groket-<run_id>-*`` launches still running."""
        import time

        now = time.monotonic()
        if now - self._docker_runs_cache_at < 2.0:
            return self._docker_runs_cache_n
        n = 0
        try:
            count_fn = getattr(self.orchestrator, "count_running_eval_runs", None)
            if callable(count_fn):
                n = int(count_fn() or 0)
        except Exception:
            logger.debug("docker active run count failed", exc_info=True)
            n = 0
        self._docker_runs_cache_at = now
        self._docker_runs_cache_n = max(0, n)
        return self._docker_runs_cache_n

    def _docker_active_session_count(self) -> int:
        """Cached count of running eval containers (sessions)."""
        import time

        # Share TTL clock with run cache; store sessions in a parallel field.
        now = time.monotonic()
        cache_at = getattr(self, "_docker_sessions_cache_at", 0.0)
        if now - cache_at < 2.0:
            return int(getattr(self, "_docker_sessions_cache_n", 0) or 0)
        n = 0
        try:
            count_fn = getattr(self.orchestrator, "count_running_eval_containers", None)
            if callable(count_fn):
                n = int(count_fn() or 0)
            else:
                list_fn = getattr(self.orchestrator, "list_running_eval_container_names", None)
                if callable(list_fn):
                    n = len(list_fn() or [])
        except Exception:
            logger.debug("docker active session count failed", exc_info=True)
            n = 0
        self._docker_sessions_cache_at = now
        self._docker_sessions_cache_n = max(0, n)
        return self._docker_sessions_cache_n

    def list_active(self) -> list[BackgroundRun]:
        with self._lock:
            return [r for r in self._active.values() if r.is_running]

    def list_all_known(self) -> list[BackgroundRun]:
        """Active runs plus recent history (for Runner restore)."""
        with self._lock:
            active = list(self._active.values())
            hist = list(self._history)
        # Active first (newest last within each group)
        return hist + active

    def clear_captured_logs(self) -> None:
        """Drop retained container log lines for active and history runs (Jobs Clear)."""
        with self._lock:
            runs = list(self._active.values()) + list(self._history)
        for bg in runs:
            bg.log_buffer.clear()
            bg.log_lines.clear()

    def latest(self) -> BackgroundRun | None:
        """Most recent run (prefer still-active, else newest finished)."""
        with self._lock:
            if self._active:
                # Insertion order in Py3.7+ dict preserves start order; last = newest
                return next(reversed(self._active.values()))
            return self._history[-1] if self._history else None

    def active_container_names(self) -> set[str]:
        with self._lock:
            names: set[str] = set()
            for bg in self._active.values():
                names.update(bg.container_names)
            return names

    def add_status_listener(self, cb: StatusCallback) -> None:
        with self._lock:
            if self._ui_detached:
                return
            if cb not in self._status_listeners:
                self._status_listeners.append(cb)

    def remove_status_listener(self, cb: StatusCallback) -> None:
        with self._lock:
            try:
                self._status_listeners.remove(cb)
            except ValueError:
                pass

    def add_log_listener(self, cb: LogCallback) -> None:
        """Register a live log listener (log viewer / Jobs only).

        Prefer polling ``BackgroundRun.log_buffer.snapshot()`` when the viewer is
        mounted; listeners are for incremental tail while the viewer is open.
        """
        with self._lock:
            if self._ui_detached:
                return
            if cb not in self._log_listeners:
                self._log_listeners.append(cb)
        # Enable live notify on any active run buffers
        with self._lock:
            runs = list(self._active.values())
        for bg in runs:
            bg.log_buffer.enable_live_notify(True)
            bg.log_buffer.add_listener(cb)

    def remove_log_listener(self, cb: LogCallback) -> None:
        with self._lock:
            try:
                self._log_listeners.remove(cb)
            except ValueError:
                pass

    def add_finished_listener(self, cb: FinishedCallback) -> None:
        with self._lock:
            if self._ui_detached:
                return
            if cb not in self._finished_listeners:
                self._finished_listeners.append(cb)

    def remove_finished_listener(self, cb: FinishedCallback) -> None:
        with self._lock:
            try:
                self._finished_listeners.remove(cb)
            except ValueError:
                pass

    def start_run(
        self,
        *,
        prompt: str,
        setup_instructions: str,
        docker_image: str,
        models: list[str],
        parallelism: int,
        repo_url: str,
        repo_branch: str,
        auth_json: Path,
        grok_config: Path,
        run_id: str | None = None,
        prune_exited: bool = True,
        save_config: bool = True,
        config_name: str = "",
        existing_config_id: str | None = None,
        quiet: bool = False,
        batch_id: str = "",
        github_write: bool = False,
        github_token: str = "",
        persona_id: str = "",
        env_vars: dict[str, str] | None = None,
        # Per-run extras (merged onto persona; do not mutate the persona on disk).
        run_mcp_servers: list[str] | None = None,
        run_mcp_definitions: list | None = None,
        run_skills: list[str] | None = None,
        run_skills_disabled: list[str] | None = None,
        run_plugins: list[str] | None = None,
        run_env_vars: dict[str, str] | None = None,
        run_inline_skills: list[tuple[str, str]] | None = None,
        interactive: bool = False,
        follow_up_prompts: list[str] | None = None,
        resume_session_id: str = "",
        resume_source_dir: str = "",
        max_turns: object | None = None,
        repo_commit: str = "",
        restore_code: bool = False,
        # Host directory bind-mounted as /workspace (no CoW); empty = clone/empty.
        repo_path: str = "",
        yolo: bool = False,
    ) -> BackgroundRun:
        # github_write arg is deprecated: effective write comes only from the persona.
        """Start a background evaluation. Multiple runs may execute concurrently.

        Does **not** block if other runs are in flight. Before start, optionally
        prunes exited containers from prior launches (safe leftovers from app
        exits); never removes containers belonging to currently tracked runs.

        When *save_config* is True, also persists a reusable recipe under
        ``work_dir/runs/run_configs/`` (separate from trace sessions).

        *quiet* / *batch_id*: mark run as part of a multi-config batch so the app
        can skip per-run toast notifications (Jobs modal still has full detail).
        """
        if prune_exited:
            protect = self.active_container_names()
            try:
                self.orchestrator.prune_eval_containers(
                    remove_exited=True,
                    remove_running=False,
                    protect_names=protect,
                )
            except Exception:
                logger.debug("Failed to prune containers before run", exc_info=True)

        run_id = run_id or uuid.uuid4().hex[:12]
        # Resolve against ~/.grok/models_cache.json (same catalog as `grok models`).
        # Drop retired/inactive ids (e.g. bottlerocket) so we don't launch doomed containers.
        skip_msgs: list[str] = []
        try:
            models, skip_msgs = validate_models_for_launch(list(models))
        except Exception:
            try:
                models = resolve_model_ids(list(models))
            except Exception:
                models = [m for m in list(models) if (m or "").strip()]

        # Preserve order, drop blanks/dupes — each remaining model gets its own container.
        # (parallelism is only for optional N copies of the *same* model; runner UI always passes 1.)
        seen_m: set[str] = set()
        models_unique: list[str] = []
        for m in models:
            key = (m or "").strip()
            if not key or key in seen_m:
                continue
            seen_m.add(key)
            models_unique.append(key)
        models = models_unique
        parallelism = max(1, int(parallelism or 1))

        # Resolve persona (optional): env + github_write + MCP/skills (persona-owned).
        resolved_persona_id = (persona_id or "").strip()
        merged_env: dict[str, str] = dict(env_vars or {})
        # Run-level env extras (MCP configure on runner); applied after persona env.
        for k, v in (run_env_vars or {}).items():
            kk = (k or "").strip()
            if kk:
                merged_env[kk] = str(v if v is not None else "")
        effective_github_write = False  # only True when persona opts in
        effective_docker = docker_image
        # Token is persona-owned only (explicit github_token / github_write args ignored).
        effective_github_token = ""
        persona_mcp_servers: list[str] = []
        persona_mcp_definitions: list[JsonObject] = []
        persona_mcp_replace_host = True
        persona_mcp_extra_toml = ""
        persona_skills: list[str] = []
        persona_skills_disabled: list[str] = []
        persona_plugins: list[str] = []
        _ = github_write  # ignored; kept so old callers/configs do not break signatures
        _ = github_token  # ignored; use persona.github_token / persona.github_token_env
        if resolved_persona_id:
            try:
                persona = PersonaStore(self.work_dir).get(resolved_persona_id)
                if persona:
                    merged_env = persona.apply_to_env(merged_env)
                    effective_github_write = persona.merge_github_write()
                    effective_github_token = persona.resolve_github_token()
                    if persona.docker_image and not (docker_image or "").strip():
                        effective_docker = persona.docker_image
                    persona_mcp_servers = list(persona.mcp_servers or [])
                    persona_mcp_definitions = list(persona.mcp_definitions or [])
                    persona_mcp_replace_host = bool(persona.mcp_replace_host)
                    persona_mcp_extra_toml = str(persona.mcp_extra_toml or "")
                    persona_skills = list(persona.skills or [])
                    persona_skills_disabled = list(persona.skills_disabled or [])
                    persona_plugins = list(persona.plugins or [])
            except Exception:
                logger.warning("Failed to load persona %r", resolved_persona_id, exc_info=True)

        try:
            merged_caps = merge_capabilities(
                persona_mcp_servers=persona_mcp_servers,
                persona_mcp_definitions=persona_mcp_definitions,
                persona_skills=persona_skills,
                persona_skills_disabled=persona_skills_disabled,
                persona_plugins=persona_plugins,
                run_mcp_servers=run_mcp_servers,
                run_mcp_definitions=run_mcp_definitions,
                run_skills=run_skills,
                run_skills_disabled=run_skills_disabled,
                run_plugins=run_plugins,
            )
            persona_mcp_servers = json_as_str_list(merged_caps.get("mcp_servers"))
            persona_mcp_definitions = json_as_mapping_list(merged_caps.get("mcp_definitions"))
            persona_skills = json_as_str_list(merged_caps.get("skills"))
            persona_skills_disabled = json_as_str_list(merged_caps.get("skills_disabled"))
            persona_plugins = json_as_str_list(merged_caps.get("plugins"))
        except Exception:
            logger.debug("Failed to merge capabilities; using persona-only lists", exc_info=True)

        resume_sid = (resume_session_id or "").strip()
        resume_src = (resume_source_dir or "").strip()
        if resume_sid or resume_src:
            interactive = True
        # Fork/resume: restore parent workspace commit when known.
        effective_commit = (repo_commit or "").strip()
        effective_restore = bool(restore_code) or bool(resume_sid or resume_src)
        if (resume_sid or resume_src) and (not effective_commit) and resume_src:
            try:
                from ..parser import load_session_meta

                parent_meta = load_session_meta(Path(resume_src), include_timeline_count=False)
                effective_commit = (parent_meta.git_commit or "").strip()
                if not (repo_url or "").strip() and (parent_meta.git_repo or "").strip():
                    repo_url = parent_meta.git_repo.strip()
                if not (repo_branch or "").strip() and (parent_meta.git_branch or "").strip():
                    repo_branch = parent_meta.git_branch.strip()
            except Exception:
                logger.debug("parent session git meta for resume failed", exc_info=True)

        effective_repo_path = (repo_path or "").strip()
        if effective_repo_path:
            from ..session.workspace import resolve_repo_path

            try:
                effective_repo_path = str(resolve_repo_path(effective_repo_path))
            except (OSError, ValueError, FileNotFoundError) as exc:
                raise RuntimeError(f"repo_path invalid: {exc}") from exc
            # Live mount: one container only (no concurrent writers on one tree).
            n_containers = len(models) * max(1, int(parallelism or 1))
            if n_containers > 1:
                raise RuntimeError(
                    "repo_path mounts a live host directory — use a single model "
                    "and parallelism=1 (got "
                    f"{len(models)} model(s) × parallelism={parallelism})"
                )
            # External tree is as-is; do not force commit restore onto it.
            if not (repo_commit or "").strip():
                effective_commit = ""
                effective_restore = bool(resume_sid or resume_src)

        pending_skip_logs = [f">>> SKIP model: {msg}" for msg in skip_msgs]

        if not models:
            detail = "; ".join(skip_msgs[:3]) if skip_msgs else "empty model list"
            raise RuntimeError(
                f"No active models to run ({detail}). "
                "Check Runner models field vs `grok models` / ~/.grok/models_cache.json"
            )

        eval_run = EvalRun(
            run_id=run_id,
            prompt=prompt,
            setup_instructions=setup_instructions,
            docker_image=effective_docker,
            models=models,
            parallelism=parallelism,
            repo_url=repo_url,
            repo_branch=repo_branch,
            repo_path=effective_repo_path,
            status="running",
            created_at=datetime.now(UTC).isoformat(),
        )

        from ..constants import DEFAULT_MAX_TURNS, normalize_max_turns
        from .batch import eval_container_model_tag

        effective_max_turns = normalize_max_turns(
            DEFAULT_MAX_TURNS if max_turns is None else max_turns,
            default=DEFAULT_MAX_TURNS,
        )

        configs: list[ContainerConfig] = []
        used_names: set[str] = set()
        for model in models:
            for i in range(parallelism):
                mid, effort = split_model_effort(model)
                short = eval_container_model_tag(model)
                base = f"groket-{run_id}-{short}"
                if parallelism > 1:
                    base = f"{base}-{i}"
                name = base
                n = 2
                while name in used_names:
                    name = f"{base}x{n}"
                    n += 1
                used_names.add(name)
                configs.append(
                    ContainerConfig(
                        model=mid or model,
                        reasoning_effort=effort,
                        prompt=prompt,
                        docker_image=effective_docker,
                        repo_url=repo_url,
                        repo_branch=repo_branch,
                        repo_path=effective_repo_path,
                        setup_instructions=setup_instructions,
                        container_name=name,
                        github_write=bool(effective_github_write),
                        github_token=effective_github_token,
                        persona_id=resolved_persona_id,
                        mcp_servers=list(persona_mcp_servers),
                        mcp_definitions=list(persona_mcp_definitions),
                        mcp_replace_host=bool(persona_mcp_replace_host),
                        mcp_extra_toml=persona_mcp_extra_toml,
                        skills=list(persona_skills),
                        skills_disabled=list(persona_skills_disabled),
                        plugins=list(persona_plugins),
                        run_plugins=[s for s in (run_plugins or []) if str(s).strip()],
                        run_skills=[s for s in (run_skills or []) if str(s).strip()],
                        run_mcp_servers=[s for s in (run_mcp_servers or []) if str(s).strip()],
                        inline_skills=list(run_inline_skills or []),
                        env_vars=dict(merged_env),
                        interactive=bool(interactive),
                        follow_up_prompts=list(follow_up_prompts or []),
                        run_id=run_id,
                        resume_session_id=resume_sid,
                        resume_source_dir=resume_src,
                        repo_commit=effective_commit,
                        restore_code=effective_restore,
                        max_turns=effective_max_turns,
                        yolo=bool(yolo),
                    )
                )

        if not configs:
            raise RuntimeError("Internal error: no container configs built from models")

        traces_hint = self.work_dir / "traces"
        if configs:
            traces_hint = self.work_dir / "traces" / configs[0].container_name
        bg = BackgroundRun(
            run_id=run_id,
            eval_run=eval_run,
            configs=configs,
            quiet=bool(quiet or batch_id),
            batch_id=str(batch_id or ""),
            persona_id=resolved_persona_id,
            interactive=bool(interactive),
            traces_vol=traces_hint,
        )
        # Wire buffer listeners only when RunManager has live log subscribers (Jobs/log view).
        if self._log_listeners:
            bg.log_buffer.enable_live_notify(True)
            for cb in list(self._log_listeners):
                bg.log_buffer.add_listener(cb)
        for msg in pending_skip_logs:
            bg.append_log("_models", msg)
        for cfg in configs:
            bg.statuses[cfg.container_name] = ContainerStatus(
                container_name=cfg.container_name,
                model=cfg.model,
                status="pending",
            )

        if save_config:
            try:
                RunConfigStore(self.work_dir).save_from_launch(
                    prompt=prompt,
                    setup_instructions=setup_instructions,
                    docker_image=effective_docker,
                    repo_url=repo_url,
                    repo_branch=repo_branch,
                    repo_path=effective_repo_path,
                    models=models,
                    parallelism=parallelism,
                    run_id=run_id,
                    name=config_name,
                    update_existing_id=existing_config_id,
                    github_write=False,  # not stored as a run property; persona drives launch
                    persona_id=resolved_persona_id,
                    run_mcp_servers=list(run_mcp_servers or []),
                    run_mcp_definitions=list(run_mcp_definitions or []),
                    run_skills=list(run_skills or []),
                    run_plugins=list(run_plugins or []),
                    run_env_vars=dict(run_env_vars or {}),
                    # Tuples from runner or maps — RunConfigStore normalizes.
                    run_inline_skills=list(run_inline_skills or []),
                    max_turns=effective_max_turns,
                    yolo=bool(yolo),
                )
            except Exception:
                logger.warning("Failed to save run config", exc_info=True)

        with self._lock:
            self._active[run_id] = bg

        # New launch after a prior quit-abort in the same process.
        try:
            self.orchestrator.clear_abort()
        except Exception:
            pass

        thread = threading.Thread(
            target=self._worker,
            args=(bg, auth_json, grok_config),
            name=f"grok-run-{run_id}",
            daemon=True,  # exit TUI without waiting; docker keeps running
        )
        with self._lock:
            self._threads[run_id] = thread
        thread.start()
        return bg

    def start_batch(
        self,
        items: list[dict],
        *,
        auth_json: Path,
        grok_config: Path,
        max_parallel: int = 2,
        prune_exited: bool = True,
        save_config: bool = True,
        batch_id: str | None = None,
        on_item_started: Callable[[str, BackgroundRun], None] | None = None,
        on_item_error: Callable[[str, str], None] | None = None,
        on_batch_done: Callable[[str, list[BackgroundRun], list[tuple[str, str]]], None]
        | None = None,
    ) -> str:
        """Launch many configs with bounded parallelism (daemon thread + semaphore).

        *items* is a list of dicts with keys matching :meth:`start_run` kwargs
        (``prompt``, ``setup_instructions``, ``docker_image``, ``models``,
        ``repo_url``, ``repo_branch``, ``config_name``, ``existing_config_id``,
        optional ``parallelism`` default 1, optional ``label`` for callbacks).

        At most *max_parallel* configs are **started** concurrently; each config
        may still spawn multiple containers (one per model) inside its own run.

        Returns a batch_id string; work continues in a background thread.
        """
        batch_id = batch_id or f"batch-{uuid.uuid4().hex[:10]}"
        max_parallel = max(1, int(max_parallel or 1))
        items = list(items or [])
        if not items:
            raise RuntimeError("Batch has no configs to launch")

        with self._lock:
            self._active_batches.add(batch_id)

        def _run_batch() -> None:
            sem = threading.Semaphore(max_parallel)
            started: list[BackgroundRun] = []
            errors: list[tuple[str, str]] = []
            lock = threading.Lock()
            item_threads: list[threading.Thread] = []

            def _one(idx: int, item: dict) -> None:
                label = str(
                    item.get("label")
                    or item.get("config_name")
                    or item.get("existing_config_id")
                    or f"item-{idx}"
                )
                sem.acquire()
                try:
                    bg = self.start_run(
                        prompt=str(item.get("prompt") or ""),
                        setup_instructions=str(item.get("setup_instructions") or ""),
                        docker_image=str(item.get("docker_image") or "fully-loaded"),
                        models=list(item.get("models") or []),
                        parallelism=max(1, int(item.get("parallelism") or 1)),
                        repo_url=str(item.get("repo_url") or ""),
                        repo_branch=str(item.get("repo_branch") or ""),
                        repo_path=str(item.get("repo_path") or ""),
                        github_token=str(item.get("github_token") or ""),
                        persona_id=str(item.get("persona_id") or ""),
                        run_mcp_servers=list(item.get("run_mcp_servers") or []),
                        run_mcp_definitions=list(item.get("run_mcp_definitions") or []),
                        run_skills=list(item.get("run_skills") or []),
                        run_plugins=list(item.get("run_plugins") or []),
                        run_env_vars=dict(item.get("run_env_vars") or {}),
                        run_inline_skills=list(item.get("run_inline_skills") or []),
                        auth_json=auth_json,
                        grok_config=grok_config,
                        prune_exited=prune_exited and idx == 0,
                        save_config=save_config,
                        config_name=str(item.get("config_name") or ""),
                        existing_config_id=item.get("existing_config_id"),
                        quiet=True,
                        batch_id=batch_id,
                        max_turns=item.get("max_turns"),
                        yolo=bool(item.get("yolo")),
                    )
                    with lock:
                        started.append(bg)
                    if on_item_started:
                        try:
                            on_item_started(label, bg)
                        except Exception:
                            logger.debug("on_item_started callback failed", exc_info=True)
                    # Hold the slot until this run finishes so max_parallel bounds
                    # in-flight *configs*, not just how fast we call start_run.
                    while bg.is_running:
                        threading.Event().wait(0.5)
                except Exception as exc:
                    err = str(exc)
                    with lock:
                        errors.append((label, err))
                    if on_item_error:
                        try:
                            on_item_error(label, err)
                        except Exception:
                            logger.debug("on_item_error callback failed", exc_info=True)
                finally:
                    sem.release()

            for idx, item in enumerate(items):
                th = threading.Thread(
                    target=_one,
                    args=(idx, item),
                    name=f"grok-batch-{batch_id}-{idx}",
                    daemon=True,
                )
                item_threads.append(th)
                th.start()

            for th in item_threads:
                th.join()

            with self._lock:
                self._active_batches.discard(batch_id)

            if on_batch_done:
                try:
                    on_batch_done(batch_id, started, errors)
                except Exception:
                    logger.debug("on_batch_done callback failed", exc_info=True)

        threading.Thread(
            target=_run_batch,
            name=f"grok-batch-{batch_id}",
            daemon=True,
        ).start()
        return batch_id

    def _worker(
        self,
        bg: BackgroundRun,
        auth_json: Path,
        grok_config: Path,
    ) -> None:
        start = bg.started_at

        def on_status(status: ContainerStatus) -> None:
            snap = copy.copy(status)
            with self._lock:
                bg.statuses[status.container_name] = snap
                if self._ui_detached:
                    return
                listeners = list(self._status_listeners)
            if self.ui_detached:
                return
            for cb in listeners:
                try:
                    cb(snap)
                except Exception:
                    logger.debug("Status listener callback failed", exc_info=True)

        def on_log(name: str, line: str) -> None:
            # Always capture; skip UI fan-out when TUI has detached (quit without hang).
            bg.append_log(name, line)

        try:
            results = self.orchestrator.run_parallel_evaluations(
                bg.configs,
                auth_json,
                grok_config,
                on_status=on_status,
                on_log=on_log,
            )
            self._save_run_manifest(bg, results)
            bg.results = results
            bg.eval_run.status = "completed"
        except Exception as exc:
            bg.error = str(exc)
            bg.eval_run.status = "failed"
            bg.results = []
        finally:
            if bg.interactive:
                self._finalize_interactive_gates(bg)
            bg.finished_at = datetime.now(UTC)
            bg.elapsed_s = (bg.finished_at - start).total_seconds()
            with self._lock:
                self._active.pop(bg.run_id, None)
                self._threads.pop(bg.run_id, None)
                self._history.append(bg)
                if len(self._history) > MAX_RUN_HISTORY:
                    self._history = self._history[-MAX_RUN_HISTORY:]
                detached = self._ui_detached
                finished_cbs = [] if detached else list(self._finished_listeners)
            for cb in finished_cbs:
                try:
                    cb(bg)
                except Exception:
                    logger.debug("Finished listener callback failed", exc_info=True)

    def _finalize_interactive_gates(self, bg: BackgroundRun) -> None:
        """Write ``state=done`` on turn gates after interactive containers exit."""
        from ..session.turn_gate import finalize_gate_dir, finalize_session_gate

        for cfg in bg.configs:
            base = self.work_dir / "traces" / cfg.container_name
            if not base.is_dir():
                continue
            for gate in base.glob(".groket-turn*"):
                if gate.is_dir():
                    try:
                        finalize_gate_dir(gate)
                    except Exception:
                        logger.debug("finalize gate failed under %s", gate, exc_info=True)
        for r in bg.results or []:
            if r.session_dir is not None:
                try:
                    finalize_session_gate(r.session_dir)
                except Exception:
                    logger.debug(
                        "finalize session gate failed for %s", r.session_dir, exc_info=True
                    )

    @staticmethod
    def _save_run_manifest(bg: BackgroundRun, results: list[ContainerStatus]) -> None:
        from .run_recipe import recipe_from_background, write_run_recipe

        ev = bg.eval_run
        sessions: dict[str, str] = {}
        for r in results:
            if r.session_dir:
                sessions[r.container_name] = str(r.session_dir)
        # Capabilities from first container config (same persona/MCP/skills for all models).
        cfg0 = bg.configs[0] if bg.configs else None
        manifest = recipe_from_background(
            run_id=bg.run_id,
            eval_run=ev,
            config=cfg0,
            persona_id=bg.persona_id,
            sessions=sessions,
        )
        # Keep traces-volume recipe current (written at start; refresh sessions map).
        if bg.traces_vol is not None:
            try:
                write_run_recipe(bg.traces_vol, manifest)
            except OSError:
                logger.debug("Failed to write run.json to %s", bg.traces_vol, exc_info=True)
        for r in results:
            if r.session_dir and r.session_dir.is_dir():
                try:
                    write_run_recipe(r.session_dir, manifest)
                except OSError:
                    logger.debug("Failed to write run.json to %s", r.session_dir, exc_info=True)

    def _active_run(self, run_id: str = "") -> BackgroundRun | None:
        rid = (run_id or "").strip()
        with self._lock:
            if rid and rid in self._active:
                return self._active[rid]
            if not rid and self._active:
                # Prefer interactive runs.
                for bg in self._active.values():
                    if bg.interactive:
                        return bg
                return next(iter(self._active.values()))
        return None

    def turn_gate_dirs(self, run_id: str = "") -> list[Path]:
        """Host dirs the entrypoint polls (one ``.groket-turn`` per container volume).

        Containers mount ``work_dir/traces/<container_name>`` at
        ``/root/.grok/sessions``.
        """
        dirs: list[Path] = []
        seen: set[Path] = set()

        def _add(p: Path) -> None:
            rp = p.resolve() if p.exists() else p
            if rp not in seen:
                seen.add(rp)
                dirs.append(p)

        bg = self._active_run(run_id)
        traces_root = self.work_dir / "traces"
        if not traces_root.is_dir():
            traces_root = self.work_dir / "runs" / "traces"

        container_names: list[str] = []
        if bg is not None:
            container_names = [c.container_name for c in bg.configs if c.container_name]
            if bg.traces_vol is not None:
                _add(Path(bg.traces_vol) / ".groket-turn")

        for cname in container_names:
            _add(traces_root / cname / ".groket-turn")
        return dirs

    def interactive_status(self, run_id: str = "") -> dict[str, JsonValue]:
        """Read entrypoint turn gate ``status.json`` (``awaiting_follow_up``, …)."""
        for turn_dir in self.turn_gate_dirs(run_id):
            status_path = turn_dir / "status.json"
            if not status_path.is_file():
                continue
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("state"):
                    return data
            except (OSError, json.JSONDecodeError):
                continue
        return {"state": "unknown"}

    def submit_follow_up(self, prompt: str, *, run_id: str = "", final: bool = False) -> None:
        """Queue a follow-up on **all** turn gates for *run_id* (every container).

        Prefer :func:`~groket.session.turn_gate.write_follow_up_for_session` for
        UI actions on one session — this method fans out across a multi-model
        run and will affect siblings.

        When *final* is true, write ``final_turn`` so the entrypoint treats this
        as the last interactive turn (no further await).
        """
        text = (prompt or "").strip()
        if not text:
            raise ValueError("follow-up prompt is empty")
        written = False
        for turn_dir in self.turn_gate_dirs(run_id):
            try:
                turn_dir.mkdir(parents=True, exist_ok=True)
                (turn_dir / "next-prompt.txt").write_text(text, encoding="utf-8")
                (turn_dir / "command").write_text("follow_up\n", encoding="utf-8")
                final_path = turn_dir / "final_turn"
                if final:
                    final_path.write_text("1\n", encoding="utf-8")
                else:
                    try:
                        final_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                written = True
            except OSError:
                logger.debug("turn gate write failed under %s", turn_dir, exc_info=True)
        if not written:
            raise RuntimeError("could not write follow-up to any turn gate directory")

    def stop_session_container(self, session_dir: Path) -> None:
        """Best-effort docker stop/remove for the container that owns *session_dir*.

        Used after session-scoped ``command=done`` so a multi-model run does not
        stop sibling containers. Finalizes the turn gate to ``state=done`` so
        the list does not stay on ending after the host kills the entrypoint.
        """
        from ..session.turn_gate import finalize_session_gate, traces_volume_for_session

        base = traces_volume_for_session(session_dir)
        if base is None:
            return
        name = base.name
        if not name or name.startswith("."):
            return
        try:
            self.orchestrator._docker.stop(name)
        except Exception:
            logger.debug("docker stop failed for %s", name, exc_info=True)
        try:
            self.orchestrator._docker.remove(name)
        except Exception:
            logger.debug("docker remove failed for %s", name, exc_info=True)
        try:
            finalize_session_gate(session_dir)
        except Exception:
            logger.debug("finalize gate failed for %s", session_dir, exc_info=True)

    def complete_interactive(self, run_id: str = "") -> None:
        """Signal **all** containers for *run_id* to exit, stop them, finalize gates.

        Prefer session-scoped :func:`~groket.session.turn_gate.write_done_for_session`
        plus :meth:`stop_session_container` for one session in a multi-model run.
        """
        from ..session.turn_gate import finalize_gate_dir

        turn_dirs = self.turn_gate_dirs(run_id)
        for turn_dir in turn_dirs:
            try:
                turn_dir.mkdir(parents=True, exist_ok=True)
                (turn_dir / "command").write_text("done\n", encoding="utf-8")
            except OSError:
                logger.debug("turn gate done write failed under %s", turn_dir, exc_info=True)
        bg = self._active_run(run_id)
        names: list[str] = []
        if bg is not None:
            names = [c.container_name for c in bg.configs if c.container_name]
        # Best-effort stop so Done does not leave a waiting entrypoint forever
        # (e.g. if the gate path was wrong on an older image).
        for name in names:
            try:
                self.orchestrator._docker.stop(name)
            except Exception:
                logger.debug("docker stop failed for %s", name, exc_info=True)
            try:
                self.orchestrator._docker.remove(name)
            except Exception:
                logger.debug("docker remove failed for %s", name, exc_info=True)
        # Host owns terminal status when it kills the container (entrypoint
        # cannot rewrite status after docker stop).
        for turn_dir in turn_dirs:
            try:
                finalize_gate_dir(turn_dir)
            except Exception:
                logger.debug("finalize gate failed under %s", turn_dir, exc_info=True)

    def is_awaiting_follow_up(self, run_id: str = "") -> bool:
        st = self.interactive_status(run_id)
        return str(st.get("state") or "") == "awaiting_follow_up"
