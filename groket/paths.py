"""Application paths: config under ``~/.groket``, runs under a work dir.

**Config home** (``APP_HOME`` / ``~/.groket``) holds identity and extensions —
config.toml, personas, tasks scaffolds, exported reports, flag fallbacks,
notes schema / notes fallbacks, optional ``models.yaml``, optional ``keys.toml``.

**Work dir** holds only session / run data — traces, run configs, feedback
cache, Docker build contexts for launches, batch result log. Default work dir
is ``~/.groket/work``. Pass a CLI path to open another work root, traces tree,
or session. The default is never the process cwd.

Passing a path to ``groket`` sets what is loaded and, when that path is a work
root, where new runs go — see :func:`resolve_work_and_traces`.
"""

from __future__ import annotations

from pathlib import Path

# App-global state and user extensions (not per-workspace run data).
APP_HOME = Path.home() / ".groket"

# Default work root for traces / recipes / docker builds (under APP_HOME).
DEFAULT_WORK_DIR = APP_HOME / "work"


def app_home() -> Path:
    """Return the app-global home directory, creating it if needed."""
    APP_HOME.mkdir(parents=True, exist_ok=True)
    return APP_HOME


def analysis_cache_dir() -> Path:
    """``~/.groket/cache`` — host catalog snapshot and other local cache."""
    d = APP_HOME / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def mcp_registry_cache_dir() -> Path:
    """``~/.groket/cache/mcp-registry`` — official MCP registry search responses."""
    d = APP_HOME / "cache" / "mcp-registry"
    d.mkdir(parents=True, exist_ok=True)
    return d


def personas_home() -> Path:
    """``~/.groket/personas`` — app-global persona store."""
    d = APP_HOME / "personas"
    d.mkdir(parents=True, exist_ok=True)
    return d


def app_config_path() -> Path:
    """``~/.groket/config.toml`` — app-global prefs file."""
    return APP_HOME / "config.toml"


def user_models_path() -> Path:
    """``~/.groket/models.yaml`` — optional preferred model ordering for batch."""
    return APP_HOME / "models.yaml"


def user_keys_path() -> Path:
    """``~/.groket/keys.toml`` — optional key overlay (diffs over the catalog)."""
    return APP_HOME / "keys.toml"


def reports_dir() -> Path:
    """``~/.groket/reports`` — finding Markdown and session export tarballs."""
    d = APP_HOME / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def flags_fallback_file(session_id: str) -> Path:
    """``~/.groket/flags/<session_id>/flags.json`` — path only, no mkdir."""
    return APP_HOME / "flags" / session_id / "flags.json"


def flags_fallback_dir(session_id: str) -> Path:
    """``~/.groket/flags/<session_id>`` — created when saving a fallback flag."""
    d = flags_fallback_file(session_id).parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def notes_fallback_dir(session_id: str) -> Path:
    """``~/.groket/notes/<session_id>`` — operator notes when session dir is not writable."""
    d = APP_HOME / "notes" / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_tasks_dir() -> Path:
    """``~/.groket/tasks`` — optional task YAML (pass explicitly to ``batch --tasks``)."""
    d = APP_HOME / "tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_export_profiles_dir() -> Path:
    """``~/.groket/export_profiles`` — user session-export profile YAML."""
    d = APP_HOME / "export_profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_user_extension_dirs() -> dict[str, Path]:
    """Create standard user extension directories; return a name → path map."""
    return {
        "tasks": user_tasks_dir(),
        "export_profiles": user_export_profiles_dir(),
    }


# On-disk prefix for run/trace/container names.
RUN_PREFIX = "groket-"
RUN_PREFIXES = (RUN_PREFIX,)
# Session-side eval config filename (also embedded in images as groket-config.toml).
CONFIG_FILENAME = "groket-config.toml"


def is_run_dir_name(name: str) -> bool:
    """True for runner/batch trace or container names (``groket-*`` prefix)."""
    return bool(name) and name.startswith(RUN_PREFIX)


def strip_run_prefix(name: str) -> str:
    for pfx in RUN_PREFIXES:
        if name.startswith(pfx):
            return name[len(pfx) :]
    return name


def run_name(*parts: str) -> str:
    """Build a canonical run/container name with the on-disk prefix."""
    body = "-".join(str(p) for p in parts if p is not None and str(p) != "")
    return f"{RUN_PREFIX}{body}"


def default_work_dir() -> Path:
    """Default work root: ``~/.groket/work`` (never cwd; CLI path overrides)."""
    return DEFAULT_WORK_DIR


def default_traces_root(work_dir: Path | None = None) -> Path:
    wd = work_dir or default_work_dir()
    return Path(wd).expanduser() / "runs" / "traces"


def eval_results_path(work_dir: Path | None = None) -> Path:
    """Batch summary log under the work dir (``runs/eval_results.json``)."""
    wd = work_dir or default_work_dir()
    return Path(wd).expanduser() / "runs" / "eval_results.json"


def resolve_work_and_traces(path: Path | str | None = None) -> tuple[Path, Path]:
    """Return ``(work_dir, traces_root)`` for TUI / runner / batch / feedback.

    *path* may be a work root, ``…/runs/traces``, a session directory, or
    omitted (defaults to :func:`default_work_dir`). ``work_dir`` owns session/run
    data: ``runs/traces``, ``runs/run_configs``, ``runs/feedback_cache``,
    ``docker-build``. App config lives under :data:`APP_HOME`, not here.
    """
    if path is None:
        wd = default_work_dir()
        try:
            wd = wd.resolve()
        except OSError:
            pass
        return wd, default_traces_root(wd)

    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = Path(path).expanduser()

    parts = p.parts

    # …/runs/traces  or  …/runs/traces/<session>
    if len(parts) >= 2 and parts[-1] == "traces" and parts[-2] == "runs":
        wd = p.parent.parent
        return wd, p
    if len(parts) >= 3 and parts[-2] == "traces" and parts[-3] == "runs":
        wd = p.parent.parent.parent
        return wd, p.parent

    # …/runs/feedback_cache  or under it
    if len(parts) >= 2 and parts[-1] == "feedback_cache" and parts[-2] == "runs":
        wd = p.parent.parent
        return wd, default_traces_root(wd)
    if len(parts) >= 3 and parts[-2] == "feedback_cache" and parts[-3] == "runs":
        wd = p.parent.parent.parent
        return wd, default_traces_root(wd)

    # …/runs  (batch/orchestrator style work root)
    if parts and parts[-1] == "runs":
        wd = p.parent
        return wd, p / "traces"

    # …/traces (standalone traces folder, not under runs/)
    if parts and parts[-1] == "traces":
        wd = p.parent
        return wd, p

    if p.is_dir():
        if (p / "runs" / "traces").is_dir() or (p / "runs").is_dir():
            return p, p / "runs" / "traces"
        if (p / "traces").is_dir():
            return p, p / "traces"
        session_markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl", "summary.json")
        if any((p / m).exists() for m in session_markers):
            parent = p.parent
            if parent.name == "traces":
                return parent.parent, parent
            return parent, parent

        # Host Grok sessions tree: browse in place; runner still uses default work root.
        if p.name == "sessions" and p.parent.name == ".grok":
            wd = default_work_dir()
            try:
                wd = wd.resolve()
            except OSError:
                pass
            return wd, p

        # Explicit directory path used as work root (opt-in via CLI), never implicit cwd
        return p, p / "runs" / "traces"

    if not p.suffix:
        return p, p / "runs" / "traces"

    wd = default_work_dir()
    return wd, default_traces_root(wd)


def traces_root_for_reload(work_dir: Path, traces_path: Path | None) -> Path:
    """Path to rescan after a Docker run finishes."""
    if traces_path is not None:
        tp = Path(traces_path)
        if tp.is_dir():
            markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl")
            if any((tp / m).exists() for m in markers):
                return tp.parent
            return tp
    return default_traces_root(work_dir)
