"""CLI entry point for groket — Typer (Click) app.

Default: interactive TUI. Optional path (``-P`` or leading argument) selects
work root, traces tree, or session (default ``~/.groket/work``).

Commands: ``serve`` (control owner), ``hud``, ``batch``, ``rules``, ``gen``,
``doctor``, ``editor``, ``keys``.

Shell completion: ``uv run groket --install-completion``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from .ui.i18n import setup_i18n

setup_i18n()

app = typer.Typer(
    name="groket",
    help=(
        "Session eval for Grok traces.\n\n"
        "With no command: open the TUI "
        "([cyan]PATH[/cyan] or [cyan]-P PATH[/cyan] = work root, traces, or session; "
        "default [cyan]~/.groket/work[/cyan]).\n\n"
        "[cyan]serve[/cyan] owns the control socket · "
        "[cyan]hud[/cyan] palette · "
        "[cyan]batch[/cyan] headless tasks · "
        "[cyan]rules[/cyan] / [cyan]gen[/cyan] detection · "
        "[cyan]doctor[/cyan] host checks · "
        "[cyan]editor[/cyan] Emacs/Neovim pack paths · "
        "[cyan]keys[/cyan] resolved bindings."
    ),
    no_args_is_help=False,
    add_completion=True,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

gen_app = typer.Typer(
    name="gen",
    help=(
        "Scaffold user extensions under [cyan]~/.groket/[/cyan] "
        "(detectors, rules, analysis plugins, example tasks)."
    ),
    no_args_is_help=True,
)
app.add_typer(gen_app, name="gen")

batch_app = typer.Typer(
    name="batch",
    help=(
        "Run task YAML catalogs through Docker (headless). "
        "See [cyan]examples/tasks/[/cyan] and "
        "[cyan]https://indynull.github.io/groket/schemas/tasks.schema.json[/cyan]."
    ),
    no_args_is_help=True,
)
app.add_typer(batch_app, name="batch")

rules_app = typer.Typer(
    name="rules",
    help=(
        "Validate detection rules / composites YAML "
        "([cyan]~/.groket/rules[/cyan], example packs). "
        "Schema: [cyan]https://indynull.github.io/groket/schemas/rules.schema.json[/cyan]."
    ),
    no_args_is_help=True,
)
app.add_typer(rules_app, name="rules")

serve_app = typer.Typer(
    name="serve",
    help=(
        "Control owner: owns the local JSON-RPC Unix socket.\n\n"
        "With no subcommand: start in the foreground. "
        "[cyan]-d[/cyan] detaches. "
        "Lifecycle: [cyan]stop[/cyan] · [cyan]restart[/cyan] · [cyan]status[/cyan]. "
        "TUI and HUD attach as clients; leave serve running across launches."
    ),
    no_args_is_help=False,
    invoke_without_command=True,
)
app.add_typer(serve_app, name="serve")

editor_app = typer.Typer(
    name="editor",
    help="Packaged Emacs / Neovim client paths for install snippets.",
    no_args_is_help=True,
)
app.add_typer(editor_app, name="editor")

# Subcommand names — must not be consumed as a TUI path positional.
TOOL_COMMANDS = frozenset(
    {
        "gen",
        "batch",
        "rules",
        "serve",
        "hud",
        "tui",
        "doctor",
        "editor",
        "keys",
    }
)


def launch_tui(
    path: Path | None,
    config: Path | None,
    *,
    socket: Path | bool | None = None,
    prompt_index: int | None = None,
    ensure_serve: bool = True,
) -> None:
    """Start the TUI for *path* (work root, traces dir, or session) or default work root.

    The TUI never owns the control socket. When *ensure_serve* is true and a
    socket path is configured, detach-start a headless owner if the socket is
    free, then attach as a client. When *ensure_serve* is false, attach only if
    an owner is already live. Pass *socket* ``False`` to run without control.
    """
    from .integrations.control import default_socket_path
    from .integrations.daemon import ensure_control_daemon
    from .paths import resolve_work_and_traces
    from .ui.app import TraceEvalApp

    cfg = config.expanduser() if config is not None else None
    wd, tr = resolve_work_and_traces(path)
    session: Path | None = None
    if path is not None:
        candidate = Path(path).expanduser()
        markers = ("updates.jsonl", "events.jsonl", "chat_history.jsonl", "summary.json")
        if candidate.is_dir() and any((candidate / marker).is_file() for marker in markers):
            session = candidate.resolve()
    socket_path = (
        None
        if socket is False
        else Path(socket).expanduser()
        if isinstance(socket, Path)
        else default_socket_path()
    )
    if socket_path is not None and ensure_serve:
        result = ensure_control_daemon(
            socket_path=socket_path,
            work_dir=wd,
            traces_path=tr,
        )
        if result.ok:
            if result.spawned:
                typer.echo(
                    f"groket: started control owner pid={result.pid} socket={socket_path}",
                    err=True,
                )
            elif result.already_running:
                typer.echo(f"groket: control owner already live at {socket_path}", err=True)
        else:
            typer.echo(
                f"groket: warning: could not start control owner: {result.error}",
                err=True,
            )
    typer.echo(f"groket: work_dir={wd}", err=True)
    typer.echo(f"  traces: {tr}", err=True)
    typer.echo(f"  runner writes: {wd / 'runs' / 'traces'}", err=True)
    TraceEvalApp(
        traces_path=tr,
        work_dir=wd,
        config_path=cfg,
        control_socket=socket_path,
        control_attach_only=socket_path is not None,
        initial_session=session,
        initial_prompt_index=prompt_index,
    ).run()


@app.command("hud")
def cmd_hud(
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Work root for catalog discovery when starting serve (default ~/.groket/work).",
            show_default=False,
        ),
    ] = None,
    socket: Annotated[
        Path | None,
        typer.Option(
            "-s",
            "--socket",
            help="Control Unix socket (default: runtime control.sock).",
            show_default=False,
        ),
    ] = None,
    ensure_serve: Annotated[
        bool,
        typer.Option(
            "--serve/--no-serve",
            help="Detach-start control owner when the socket is free (default: serve).",
        ),
    ] = True,
    dev: Annotated[
        bool,
        typer.Option(
            "--dev",
            help="Run cargo run (debug) in the checkout instead of a built binary.",
        ),
    ] = False,
    debug: Annotated[
        bool,
        typer.Option(
            "--debug",
            help="Use unoptimized cargo debug binary (default: release).",
        ),
    ] = False,
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help="Force Rust rebuild for the selected profile before launch.",
        ),
    ] = False,
    foreground: Annotated[
        bool,
        typer.Option(
            "--foreground",
            help="Keep the HUD attached to this terminal (default: detach).",
        ),
    ] = False,
    restart: Annotated[
        bool,
        typer.Option(
            "--restart",
            help="Stop any running groket-hud process, then start a new one.",
        ),
    ] = False,
    install_desktop: Annotated[
        bool,
        typer.Option(
            "--install-desktop",
            help=(
                "Write user-local icons and a launcher (Linux .desktop, "
                "macOS ~/Applications app, Windows Start Menu). Does not start the HUD."
            ),
        ),
    ] = False,
    show: Annotated[
        bool,
        typer.Option(
            "--show",
            help="Show the palette (running HUD). Starts the HUD if needed (Wayland/Sway).",
        ),
    ] = False,
    hide: Annotated[
        bool,
        typer.Option(
            "--hide",
            help="Hide the overlay (running HUD via summon socket).",
        ),
    ] = False,
    toggle: Annotated[
        bool,
        typer.Option(
            "--toggle",
            help="Show or hide (running HUD). Preferred Sway bindsym target.",
        ),
    ] = False,
) -> None:
    """Desktop session palette (control client).

    Starts in the background by default (macOS: no Dock, no Cmd+Tab). Summon with
    Cmd+Shift+G on macOS / X11; on Wayland use ``--toggle``, tray Show HUD, or a
    compositor bind. Launches the iced ``groket-hud`` binary (rebuilds from an
    editable checkout when missing or stale). ``--debug`` for unoptimized;
    ``--dev`` cargo run; ``--restart`` replaces a running HUD.
    ``--install-desktop`` only installs icons/launcher entries for this user.
    """
    from .hud.app import run_hud
    from .integrations.control import default_socket_path

    summon_flags = sum(1 for f in (show, hide, toggle) if f)
    if summon_flags > 1:
        typer.echo("error: use only one of --show, --hide, --toggle", err=True)
        raise typer.Exit(1)
    summon: str | None = None
    if show:
        summon = "show"
    elif hide:
        summon = "hide"
    elif toggle:
        summon = "toggle"

    sock = Path(socket).expanduser() if socket is not None else default_socket_path()
    code = run_hud(
        socket_path=sock,
        work_dir=path,
        auto_serve=ensure_serve,
        dev=dev,
        debug=debug,
        rebuild=rebuild,
        foreground=foreground,
        restart=restart,
        install_desktop=install_desktop,
        summon=summon,
    )
    raise typer.Exit(code)


@editor_app.command("emacs-path")
def editor_emacs_path() -> None:
    """Print the packaged groket.el path."""
    typer.echo(Path(__file__).parent / "integrations" / "emacs" / "groket.el")


@editor_app.command("vim-path")
def editor_vim_path() -> None:
    """Print the packaged Neovim runtimepath directory."""
    typer.echo(Path(__file__).parent / "integrations" / "vim")


# Shared serve option types.
_ServePath = Annotated[
    Path | None,
    typer.Option(
        "-P",
        "--path",
        help="Work root or traces tree (default ~/.groket/work).",
        show_default=False,
    ),
]
_ServeSocket = Annotated[
    Path | None,
    typer.Option(
        "-s",
        "--socket",
        help="Control Unix socket (default: runtime control.sock).",
        show_default=False,
    ),
]
_ServeHost = Annotated[
    bool | None,
    typer.Option(
        "--host/--no-host",
        help=(
            "Include ~/.grok/sessions in session/list. Default: show_host_sessions in config.json."
        ),
    ),
]
_ServeDaemon = Annotated[
    bool,
    typer.Option(
        "-d",
        "--daemon/--foreground",
        help="Run in the background; return when the socket accepts.",
    ),
]
_ServeTimeout = Annotated[
    float,
    typer.Option(
        "-t",
        "--timeout",
        help="Seconds to wait for stop/restart.",
    ),
]


def _serve_socket_option(control_socket: Path | None) -> Path:
    from .integrations.control import default_socket_path

    return (
        Path(control_socket).expanduser() if control_socket is not None else default_socket_path()
    )


def _run_serve_start(
    *,
    path: Path | None,
    control_socket: Path | None,
    include_host: bool | None,
    daemonize: bool,
) -> int:
    """Start the control owner (foreground or detached)."""
    from .integrations.daemon import (
        run_control_daemon,
        start_control_daemon_detached,
    )

    sock = _serve_socket_option(control_socket)
    if daemonize:
        result = start_control_daemon_detached(
            socket_path=sock,
            work_dir=None,
            traces_path=path,
            include_host=include_host,
        )
        if result.already_running and result.ok:
            typer.echo(f"already running  pid={result.pid}  socket={sock}", err=True)
            return 0
        if not result.ok:
            typer.echo(f"failed to start: {result.error}", err=True)
            return 1
        typer.echo(f"started  pid={result.pid}  socket={sock}", err=True)
        return 0
    return run_control_daemon(
        socket_path=sock,
        work_dir=None,
        traces_path=path,
        include_host=include_host,
    )


def _run_serve_stop(*, control_socket: Path | None, timeout: float) -> int:
    from .integrations.daemon import stop_control_daemon

    sock = _serve_socket_option(control_socket)
    return stop_control_daemon(sock, timeout=timeout)


def _run_serve_restart(
    *,
    path: Path | None,
    control_socket: Path | None,
    include_host: bool | None,
    daemonize: bool,
    timeout: float,
) -> int:
    """Stop if running, then start (default background for service restart)."""
    from .integrations.daemon import control_daemon_status

    sock = _serve_socket_option(control_socket)
    st = control_daemon_status(sock)
    # Stop live owners, recorded pids, and zombie lock holders (no socket).
    if st.live or st.pid is not None or st.stale_lock or st.lock_pid is not None:
        code = _run_serve_stop(control_socket=control_socket, timeout=timeout)
        if code != 0 and st.live:
            # Still try start if stop only failed for non-daemon owner messaging.
            typer.echo("warning: stop returned non-zero; attempting start", err=True)
    return _run_serve_start(
        path=path,
        control_socket=control_socket,
        include_host=include_host,
        daemonize=daemonize,
    )


@serve_app.callback(invoke_without_command=True)
def serve_callback(
    ctx: typer.Context,
    path: _ServePath = None,
    control_socket: _ServeSocket = None,
    include_host: _ServeHost = None,
    daemonize: _ServeDaemon = False,
) -> None:
    """With no subcommand: start the control owner (foreground unless ``-d``)."""
    if ctx.invoked_subcommand is not None:
        return
    raise typer.Exit(
        _run_serve_start(
            path=path,
            control_socket=control_socket,
            include_host=include_host,
            daemonize=daemonize,
        )
    )


@serve_app.command("stop")
def serve_stop(
    control_socket: _ServeSocket = None,
    timeout: _ServeTimeout = 5.0,
) -> None:
    """Stop the control owner (pid file and/or stale lock holders)."""
    raise typer.Exit(_run_serve_stop(control_socket=control_socket, timeout=timeout))


@serve_app.command("restart")
def serve_restart(
    path: _ServePath = None,
    control_socket: _ServeSocket = None,
    include_host: _ServeHost = None,
    daemonize: _ServeDaemon = True,
    timeout: _ServeTimeout = 5.0,
) -> None:
    """Stop then start (``-d`` by default)."""
    raise typer.Exit(
        _run_serve_restart(
            path=path,
            control_socket=control_socket,
            include_host=include_host,
            daemonize=daemonize,
            timeout=timeout,
        )
    )


@serve_app.command("status")
def serve_status(
    control_socket: _ServeSocket = None,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Machine-readable status."),
    ] = False,
) -> None:
    """Print owner status (exit 0 if live and accepting)."""
    from .integrations.daemon import control_daemon_status

    sock = _serve_socket_option(control_socket)
    status = control_daemon_status(sock)
    if as_json:
        typer.echo(json.dumps(status.as_mapping(), indent=2, sort_keys=True))
    elif status.live:
        pid = status.pid if status.pid is not None else "?"
        typer.echo(f"running  pid={pid}  socket={status.socket_path}")
    else:
        typer.echo(f"stopped  socket={status.socket_path}")
        if status.pid is not None and not status.pid_alive:
            typer.echo(f"  stale pid file  pid={status.pid}", err=True)
        if status.stale_lock:
            lp = status.lock_pid if status.lock_pid is not None else "?"
            typer.echo(
                f"  stale lock  pid={lp}  (run: groket serve stop)",
                err=True,
            )
    raise typer.Exit(0 if status.live else 1)


def _tui_options(
    path: Path | None,
    config: Path | None,
    socket: Path | None,
    use_socket: bool,
    ensure_serve: bool,
    prompt_index: int | None,
) -> None:
    """Shared TUI launch (root default and ``tui`` command)."""
    launch_tui(
        path=path,
        config=config,
        socket=socket if use_socket else False,
        prompt_index=prompt_index,
        ensure_serve=ensure_serve if use_socket else False,
    )


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help=(
                "Work root, runs/traces, or a session directory "
                "(or pass as the first argument). Default: ~/.groket/work."
            ),
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            "-c",
            "--config",
            help="Path to config.json (default: ~/.groket/config.json).",
            show_default=False,
        ),
    ] = None,
    socket: Annotated[
        Path | None,
        typer.Option(
            "-s",
            "--socket",
            help="Control Unix socket (default: runtime control.sock).",
            show_default=False,
        ),
    ] = None,
    no_socket: Annotated[
        bool,
        typer.Option(
            "--no-socket",
            help="Run the TUI without the control plane (no serve attach).",
        ),
    ] = False,
    ensure_serve: Annotated[
        bool,
        typer.Option(
            "--serve/--no-serve",
            help=(
                "When the control socket is free, detach-start the owner before attach "
                "(default: serve). --no-serve only attaches if an owner is already live."
            ),
        ),
    ] = True,
    prompt_index: Annotated[
        int | None,
        typer.Option(
            "--prompt-index",
            help="Prompt index when PATH is a session directory.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Start the TUI when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    _tui_options(path, config, socket, not no_socket, ensure_serve, prompt_index)


@app.command("tui")
def cmd_tui(
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Work root, traces, or session (default ~/.groket/work).",
            show_default=False,
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option("-c", "--config", help="Path to config.json.", show_default=False),
    ] = None,
    socket: Annotated[
        Path | None,
        typer.Option("-s", "--socket", help="Control Unix socket.", show_default=False),
    ] = None,
    no_socket: Annotated[
        bool,
        typer.Option(
            "--no-socket",
            help="Run without the control plane.",
        ),
    ] = False,
    ensure_serve: Annotated[
        bool,
        typer.Option(
            "--serve/--no-serve",
            help="Detach-start control owner when free (default: serve).",
        ),
    ] = True,
    prompt_index: Annotated[
        int | None,
        typer.Option("--prompt-index", help="Prompt index for a session path.", show_default=False),
    ] = None,
) -> None:
    """Open the interactive TUI (same as bare ``groket``)."""
    _tui_options(path, config, socket, not no_socket, ensure_serve, prompt_index)


@batch_app.command("run")
def cmd_batch_run(
    tasks: Annotated[
        Path,
        typer.Option(
            "-t",
            "--tasks",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Tasks YAML file (schema: schemas/tasks.schema.json).",
        ),
    ],
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Work root for traces and Docker builds (default ~/.groket/work).",
            show_default=False,
        ),
    ] = None,
    category: Annotated[
        str | None,
        typer.Option(
            "-C",
            "--category",
            help="Only tasks with this category field.",
        ),
    ] = None,
    task_id: Annotated[
        list[str] | None,
        typer.Option("-i", "--task-id", help="Only these task ids (repeatable)."),
    ] = None,
    models: Annotated[
        list[str] | None,
        typer.Option(
            "-m",
            "--models",
            help=(
                "Model ids (default: host Grok models catalog). "
                "Tasks that set models: in YAML use that list instead."
            ),
        ),
    ] = None,
    parallelism: Annotated[
        int,
        typer.Option("-p", "--parallelism", min=1, help="Concurrent (task, model) jobs."),
    ] = 1,
) -> None:
    """Validate tasks YAML and run each task × model through Docker."""
    from .paths import resolve_work_and_traces
    from .runs.batch import load_models, load_tasks, run_batch
    from .runs.task_schema import load_task_file

    try:
        load_task_file(tasks)  # fail fast with Pydantic errors before Docker
        loaded = load_tasks(tasks, category)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except (ValueError, Exception) as exc:
        # Pydantic ValidationError subclasses Exception
        typer.echo(f"error: invalid tasks file: {exc}", err=True)
        raise typer.Exit(2) from exc

    if task_id:
        wanted = set(task_id)
        loaded = [t for t in loaded if t.task_id in wanted]
    if not loaded:
        typer.echo("No tasks matched filters.", err=True)
        raise typer.Exit(0)

    wd, _tr = resolve_work_and_traces(path)
    typer.echo(f"batch: work_dir={wd}", err=True)
    batch_models = models or load_models()
    typer.echo(
        f"  tasks={len(loaded)}  batch_models={batch_models} "
        f"(per-task models: in YAML override when set)",
        err=True,
    )
    results = run_batch(
        loaded,
        work_dir=wd,
        models=batch_models,
        parallelism=parallelism,
    )
    failed = sum(1 for r in results if (r.get("status") or "") != "completed")
    raise typer.Exit(1 if failed else 0)


@batch_app.command("validate")
def cmd_batch_validate(
    tasks: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Tasks YAML file to validate.",
        ),
    ],
) -> None:
    """Validate a tasks YAML file against the Pydantic / JSON Schema model."""
    from .runs.task_schema import load_task_file

    try:
        doc = load_task_file(tasks)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        f"OK  {tasks}  ({len(doc.resolved_tasks())} task(s), schema_version={doc.schema_version})"
    )


@batch_app.command("schema")
def cmd_batch_schema(
    out: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--out",
            help="Write JSON Schema to this path (default: stdout).",
        ),
    ] = None,
) -> None:
    """Emit JSON Schema for tasks YAML (same as ``make schema`` / Pages publish)."""
    from .runs.task_schema import emit_tasks_schema

    text = emit_tasks_schema(out)
    if out is None:
        typer.echo(text, nl=False)
    else:
        typer.echo(f"Wrote {out}")


@rules_app.command("validate")
def cmd_rules_validate(
    rules: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Rules / composites YAML file to validate.",
        ),
    ],
) -> None:
    """Validate a rules YAML file against the Pydantic / JSON Schema model."""
    from .engine.rule_schema import load_rules_file

    try:
        doc = load_rules_file(rules)
    except FileNotFoundError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(
        f"OK  {rules}  ({len(doc.rules)} rule(s), {len(doc.composites)} composite(s), "
        f"schema_version={doc.schema_version})"
    )


@rules_app.command("schema")
def cmd_rules_schema(
    out: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--out",
            help="Write JSON Schema to this path (default: stdout).",
        ),
    ] = None,
) -> None:
    """Emit JSON Schema for rules YAML (same as ``make schema`` / Pages publish)."""
    from .engine.rule_schema import emit_rules_schema

    text = emit_rules_schema(out)
    if out is None:
        typer.echo(text, nl=False)
    else:
        typer.echo(f"Wrote {out}")


@app.command("keys")
def cmd_keys(
    occupancy: Annotated[
        bool,
        typer.Option(
            "--occupancy",
            help="List taken chords per scope (normalized).",
        ),
    ] = False,
    check: Annotated[
        bool,
        typer.Option(
            "--check",
            help="Load the overlay and exit 1 on error or conflict.",
        ),
    ] = False,
) -> None:
    """Print the resolved key table (catalog defaults plus optional keys.toml)."""
    from .keys.overlay import (
        format_errors,
        format_keymap_table,
        format_occupancy,
        load_keymap,
    )

    keymap = load_keymap()
    if check:
        if keymap.ok:
            label = str(keymap.path) if keymap.loaded_overlay else "defaults"
            typer.echo(f"OK  {label}")
            raise typer.Exit(0)
        typer.echo(format_errors(keymap), err=True)
        raise typer.Exit(1)
    if not keymap.ok:
        typer.echo(format_errors(keymap), err=True)
    if occupancy:
        typer.echo(format_occupancy(keymap))
    else:
        typer.echo(format_keymap_table(keymap))
    if not keymap.ok:
        raise typer.Exit(1)


@app.command("doctor")
def cmd_doctor(
    path: Annotated[
        Path | None,
        typer.Option(
            "-P",
            "--path",
            help="Work root to probe (default ~/.groket/work).",
            show_default=False,
        ),
    ] = None,
    json_out: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of text lines."),
    ] = False,
) -> None:
    """Host checks: Docker, Grok auth, work dir, and related deps (no TUI)."""
    from .diagnostics import run_self_test
    from .paths import resolve_work_and_traces

    wd, _tr = resolve_work_and_traces(path)
    report = run_self_test(work_dir=wd)
    if json_out:
        payload = {
            "ok": report.ok,
            "fail_count": report.fail_count,
            "warn_count": report.warn_count,
            "checks": [
                {
                    "id": c.id,
                    "name": c.name,
                    "ok": c.ok,
                    "required": c.required,
                    "detail": c.detail,
                    "level": c.level,
                }
                for c in report.checks
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        for line in report.lines():
            typer.echo(line)
    raise typer.Exit(0 if report.ok else 1)


@gen_app.command("detector")
def gen_detector(
    name: Annotated[str, typer.Argument(help="Detector name / file stem.")],
    force: Annotated[
        bool,
        typer.Option("-f", "--force", help="Overwrite if exists."),
    ] = False,
) -> None:
    """Create ~/.groket/detectors/<name>.py with @detector stub."""
    from .extensions.scaffold import slug_name, write_detector
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        path = write_detector(name, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote detector module: {path}")
    typer.echo(f"  @detector name: {slug_name(name).replace('-', '_')}")
    typer.echo("  Pair with: uv run groket gen rule <id> --detector <name>")


@gen_app.command("rule")
def gen_rule(
    rule_id: Annotated[str, typer.Argument(help="Rule id (e.g. my-custom-rule).")],
    detector: Annotated[
        str,
        typer.Option(
            "-d",
            "--detector",
            help="Detector name (default: from rule id).",
        ),
    ] = "",
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Create ~/.groket/rules/<id>.yaml merged with bundled rules."""
    from .extensions.scaffold import slug_name, write_rule
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    det = detector or slug_name(rule_id).replace("-", "_")
    try:
        path = write_rule(rule_id, detector=det, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote rule YAML: {path}")
    typer.echo(f"  detector: {det}")


@gen_app.command("plugin")
def gen_plugin(
    name: Annotated[str, typer.Argument(help="Module stem (e.g. my_session_stats).")],
    register: Annotated[
        bool,
        typer.Option(
            "-r",
            "--register",
            help="Append module:ClassName to ~/.groket/config.json analysis.plugins.",
        ),
    ] = False,
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Create ~/.groket/plugins/<name>.py analysis Analyzer class."""
    from .extensions.scaffold import (
        append_analysis_plugin_to_config,
        slug_name,
        snake_to_pascal,
        write_analysis_plugin,
    )
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        path = write_analysis_plugin(name, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    stem = slug_name(name).replace("-", "_")
    cls = snake_to_pascal(stem) + "Analyzer"
    typer.echo(f"Wrote analysis plugin: {path}")
    typer.echo(f"  config entry: {stem}:{cls}")
    if register:
        cfg = append_analysis_plugin_to_config(stem, cls)
        typer.echo(f"  updated {cfg}")
    else:
        typer.echo(f'  enable with analysis.plugins: ["{stem}:{cls}"] or pass --register')


@gen_app.command("tasks")
def gen_tasks(
    path: Annotated[
        Path | None,
        typer.Argument(help="Output path (default: ~/.groket/tasks/example_tasks.yaml)."),
    ] = None,
    force: Annotated[bool, typer.Option("-f", "--force")] = False,
) -> None:
    """Write an example tasks YAML under ``~/.groket/tasks/`` (or *path*)."""
    from .extensions.scaffold import write_tasks_file
    from .paths import ensure_user_extension_dirs

    ensure_user_extension_dirs()
    try:
        out = write_tasks_file(path, force=force)
    except FileExistsError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote tasks file: {out}")


def main(argv: list[str] | None = None) -> None:
    """Console script entry (``groket = groket.cli:main``)."""
    args = list(sys.argv[1:] if argv is None else argv)

    # ``groket PATH …`` → ``groket -P PATH …`` (not a subcommand name).
    if args and not args[0].startswith("-") and args[0] not in TOOL_COMMANDS:
        args = ["-P", args[0], *args[1:]]

    app(args=args, prog_name="groket")


if __name__ == "__main__":
    main()
