"""Locate, auto-build, and launch the iced groket-hud binary."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SOURCE_GLOBS = (
    "src/**/*",
    "assets/**/*",
    "Cargo.toml",
    "Cargo.lock",
)


def _repo_root() -> Path:
    # groket/hud/launch.py → parents[2] = repo root when editable checkout
    return Path(__file__).resolve().parents[2]


def hud_checkout_dir() -> Path | None:
    """Return the ``groket-hud`` crate dir in an editable checkout, if present."""
    cand = _repo_root() / "groket-hud"
    if (cand / "Cargo.toml").is_file() and (cand / "src" / "main.rs").is_file():
        return cand
    return None


def _debug_binary(checkout: Path) -> Path:
    return checkout / "target" / "debug" / "groket-hud"


def _release_binary(checkout: Path) -> Path:
    return checkout / "target" / "release" / "groket-hud"


def _prune_target(checkout: Path, *, keep_debug: bool) -> None:
    """Remove Cargo trees the current HUD profile does not need.

    Coverage always goes. Debug goes after a release build so a normal
    ``groket hud`` does not keep a second iced graph next to release.
    """
    target = checkout / "target"
    for name in ("llvm-cov-target", "llvm-cov"):
        path = target / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    if not keep_debug:
        debug = target / "debug"
        if debug.is_dir():
            shutil.rmtree(debug, ignore_errors=True)


def find_hud_binary(*, debug: bool = False) -> Path | None:
    """Return path to a built ``groket-hud`` binary, if any.

    Preference: ``GROKET_HUD_BIN``, then ``PATH``, then the checkout binary for
    the requested profile (**release** by default; debug only when *debug*).
    """
    env = os.environ.get("GROKET_HUD_BIN", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p
    which = shutil.which("groket-hud")
    if which:
        return Path(which)
    checkout = hud_checkout_dir()
    if checkout is None:
        return None
    candidate = _debug_binary(checkout) if debug else _release_binary(checkout)
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def _source_mtimes(checkout: Path) -> list[float]:
    times: list[float] = []
    for pattern in _SOURCE_GLOBS:
        for path in checkout.glob(pattern):
            if path.is_file():
                try:
                    times.append(path.stat().st_mtime)
                except OSError:
                    continue
    return times


def hud_binary_is_stale(binary: Path, checkout: Path) -> bool:
    """True when *binary* is older than any tracked HUD source file."""
    if not binary.is_file():
        return True
    try:
        bin_mtime = binary.stat().st_mtime
    except OSError:
        return True
    sources = _source_mtimes(checkout)
    if not sources:
        return False
    return max(sources) > bin_mtime


def build_hud(checkout: Path | None = None, *, debug: bool = False) -> Path | None:
    """Build ``groket-hud`` with cargo; **release** by default, debug when *debug*.

    :param checkout: ``groket-hud`` crate root (editable checkout).
    :param debug: When True, ``cargo build`` (unoptimized). When False (default),
        ``cargo build --release``.
    :returns: Path to the built binary, or None when cargo is missing / build fails.
    """
    root = checkout or hud_checkout_dir()
    if root is None:
        return None
    cargo = shutil.which("cargo")
    if cargo is None:
        sys.stderr.write("error: cargo not found on PATH; install Rust to auto-build groket-hud\n")
        return None
    cmd = [cargo, "build", "--manifest-path", str(root / "Cargo.toml")]
    if not debug:
        cmd.append("--release")
    profile = "debug" if debug else "release"
    sys.stderr.write(f"groket hud: building {profile} groket-hud ({' '.join(cmd[1:])})…\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run(cmd, cwd=str(root), check=False)
    except OSError as exc:
        sys.stderr.write(f"error: cargo build failed to start: {exc}\n")
        return None
    if proc.returncode != 0:
        sys.stderr.write(f"error: cargo build exited {proc.returncode}\n")
        return None
    binary = _debug_binary(root) if debug else _release_binary(root)
    if binary.is_file() and os.access(binary, os.X_OK):
        _prune_target(root, keep_debug=debug)
        return binary
    sys.stderr.write(f"error: build finished but binary missing: {binary}\n")
    return None


def build_hud_debug(checkout: Path | None = None) -> Path | None:
    """Build the unoptimized debug binary (``groket hud --debug``)."""
    return build_hud(checkout, debug=True)


def ensure_hud_binary(*, rebuild: bool = False, debug: bool = False) -> Path | None:
    """Return a runnable HUD binary for the requested profile.

    Default profile is **release**. Pass *debug* for the unoptimized binary.
    Rebuild when *rebuild* is true, the profile binary is missing, or HUD
    sources are newer than that binary (editable checkout only).
    """
    env = os.environ.get("GROKET_HUD_BIN", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p
        sys.stderr.write(f"error: GROKET_HUD_BIN not executable: {p}\n")
        return None

    checkout = hud_checkout_dir()
    if checkout is None:
        return find_hud_binary(debug=debug)

    expected = _debug_binary(checkout) if debug else _release_binary(checkout)
    found = expected if expected.is_file() and os.access(expected, os.X_OK) else None
    need_build = rebuild or found is None or hud_binary_is_stale(found, checkout)
    if not need_build:
        _prune_target(checkout, keep_debug=debug)
        return found

    built = build_hud(checkout, debug=debug)
    return built or found


def launch_hud_dev(
    *,
    socket_path: Path,
    extra_env: dict[str, str] | None = None,
) -> int:
    """Run ``groket hud --dev`` (``cargo run`` debug) in the checkout.

    :returns: Process exit code, or 127 when the checkout or cargo is unavailable.
    """
    checkout = hud_checkout_dir()
    if checkout is None:
        sys.stderr.write("error: groket-hud checkout not found (editable install only)\n")
        return 127
    cargo = shutil.which("cargo")
    if cargo is None:
        sys.stderr.write("error: cargo not found on PATH\n")
        return 127
    env = os.environ.copy()
    env["GROKET_CONTROL_SOCKET"] = str(socket_path)
    env.update(_hud_shortcut_env())
    if extra_env:
        env.update(extra_env)
    if hud_process_running() or summon_socket_accepts():
        sys.stderr.write(
            "groket hud: already running (use groket hud --restart to replace)\n"
        )
        return 0
    sys.stderr.write(f"groket hud: cargo run (debug) in {checkout}\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run(
            [cargo, "run", "--manifest-path", str(checkout / "Cargo.toml")],
            cwd=str(checkout),
            env=env,
            check=False,
        )
    except OSError as exc:
        sys.stderr.write(f"error: could not start cargo run: {exc}\n")
        return 1
    if proc.returncode == 0:
        _prune_target(checkout, keep_debug=True)
    return int(proc.returncode)


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _hud_shortcut_env() -> dict[str, str]:
    """Pass config shortcut to the binary unless already set in the environment."""
    if os.environ.get("GROKET_HUD_SHORTCUT", "").strip():
        return {}
    try:
        from ..ui.prefs import hud_global_shortcut
    except Exception:
        return {}
    chord = hud_global_shortcut()
    if not chord:
        return {}
    return {"GROKET_HUD_SHORTCUT": chord}


def hud_process_running() -> bool:
    """True when a ``groket-hud`` process is already alive on this machine."""
    try:
        proc = subprocess.run(
            ["pgrep", "-x", "groket-hud"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def default_summon_socket_path() -> Path:
    """Per-user summon socket (matches ``groket-hud`` ``summon::default_socket_path``).

    ``$GROKET_HUD_SUMMON_SOCKET`` overrides. Else
    ``$XDG_RUNTIME_DIR/groket/hud-summon.sock``, else
    ``~/.groket/run/hud-summon.sock``.
    """
    env = os.environ.get("GROKET_HUD_SUMMON_SOCKET", "").strip()
    if env:
        return Path(env).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return Path(runtime) / "groket" / "hud-summon.sock"
    return Path.home() / ".groket" / "run" / "hud-summon.sock"


def summon_socket_accepts(path: Path | None = None) -> bool:
    """True when a HUD summon listener is bound on *path*."""
    sock = Path(path or default_summon_socket_path()).expanduser()
    if not sock.exists():
        return False
    try:
        import socket

        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(0.4)
            client.connect(str(sock))
        finally:
            client.close()
        return True
    except OSError:
        return False


def send_summon_command(action: str, *, path: Path | None = None) -> int:
    """Send ``show`` / ``hide`` / ``toggle`` to a running HUD.

    :param action: One of ``show``, ``hide``, ``toggle``.
    :returns: Process exit code from the binary, or 1 on failure, 127 missing.
    """
    word = action.strip().lower()
    if word not in {"show", "hide", "toggle"}:
        sys.stderr.write(f"error: unknown summon action {action!r}\n")
        return 1
    flag = f"--{word}"
    binary = find_hud_binary() or ensure_hud_binary()
    if binary is None:
        return 127
    env = os.environ.copy()
    if path is not None:
        env["GROKET_HUD_SUMMON_SOCKET"] = str(Path(path).expanduser())
    try:
        proc = subprocess.run([str(binary), flag], env=env, check=False)
    except OSError as exc:
        sys.stderr.write(f"error: could not run {binary} {flag}: {exc}\n")
        return 1
    return int(proc.returncode)


def stop_hud_processes(*, wait_s: float = 1.5) -> int:
    """SIGTERM then SIGKILL any ``groket-hud`` processes. Return how many were seen."""
    try:
        listed = subprocess.run(
            ["pgrep", "-x", "groket-hud"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return 0
    pids = [p for p in (listed.stdout or "").split() if p.isdigit()]
    if not pids:
        return 0
    subprocess.run(["kill"] + pids, check=False, capture_output=True)
    deadline = time.monotonic() + max(0.1, wait_s)
    while time.monotonic() < deadline and hud_process_running():
        time.sleep(0.05)
    if hud_process_running():
        subprocess.run(["kill", "-9"] + pids, check=False, capture_output=True)
        time.sleep(0.05)
    return len(pids)


def install_desktop(*, rebuild: bool = False, debug: bool = False) -> int:
    """Run ``groket-hud --install-desktop`` (user-local icons + launcher).

    Ensures a binary first (same profile rules as launch). Does not start the
    control owner or the HUD process.

    :returns: Process exit code, or 127 when the binary is missing.
    """
    binary = ensure_hud_binary(rebuild=rebuild, debug=debug)
    if binary is None:
        return 127
    sys.stderr.write(f"groket hud: install-desktop via {binary}\n")
    sys.stderr.flush()
    try:
        proc = subprocess.run([str(binary), "--install-desktop"], check=False)
    except OSError as exc:
        sys.stderr.write(f"error: could not run {binary} --install-desktop: {exc}\n")
        return 1
    return int(proc.returncode)


def launch_tauri_hud(
    *,
    socket_path: Path,
    extra_env: dict[str, str] | None = None,
    dev: bool = False,
    debug: bool = False,
    rebuild: bool = False,
    foreground: bool | None = None,
    restart: bool = False,
) -> int:
    """Launch the iced palette (built binary, or ``cargo run`` when *dev*).

    When not *dev*, ensures a **release** binary for an editable checkout
    (auto ``cargo build --release`` if missing or sources are newer). Pass
    *debug* for an unoptimized ``cargo build`` binary instead.

    By default the binary is **detached**. Use *foreground* /
    ``GROKET_HUD_FOREGROUND=1`` to attach the terminal to the process.

    *restart* stops any existing ``groket-hud`` first, then starts a new one.

    :returns: Process exit code when the child exits (or 0 after detach),
        or 127 if unavailable.
    """
    if dev or _truthy_env("GROKET_HUD_DEV"):
        return launch_hud_dev(socket_path=socket_path, extra_env=extra_env)

    want_debug = bool(debug) or _truthy_env("GROKET_HUD_DEBUG")
    binary = ensure_hud_binary(rebuild=rebuild, debug=want_debug)
    if binary is None:
        return 127
    env = os.environ.copy()
    env["GROKET_CONTROL_SOCKET"] = str(socket_path)
    env.update(_hud_shortcut_env())
    if extra_env:
        env.update(extra_env)

    attach = bool(foreground) if foreground is not None else _truthy_env("GROKET_HUD_FOREGROUND")
    chord_hint = env.get("GROKET_HUD_SHORTCUT", "").strip() or "Cmd+Shift+G / Ctrl+Shift+G"
    summon_hint = "groket hud --toggle (Wayland/Sway); tray Show HUD"

    if restart:
        n = stop_hud_processes()
        if n:
            sys.stderr.write(f"groket hud: stopped {n} running process(es)\n")
    elif hud_process_running() or summon_socket_accepts():
        sys.stderr.write(
            "groket hud: already running "
            f"(summon: {summon_hint}; X11/macOS/Windows hotkey {chord_hint}; "
            "use --restart to replace)\n"
        )
        return 0

    logger.info("launching HUD binary %s (foreground=%s)", binary, attach)
    sys.stderr.write(f"groket hud: {binary}\n")
    hud_log = Path.home() / ".groket" / "hud.log"
    sys.stderr.write(f"groket hud: errors → {hud_log}\n")
    if env.get("GROKET_HUD_SHORTCUT"):
        sys.stderr.write(f"groket hud: GROKET_HUD_SHORTCUT={env['GROKET_HUD_SHORTCUT']}\n")
    sys.stderr.flush()

    if attach:
        try:
            proc = subprocess.run([str(binary)], env=env, check=False)
        except OSError as exc:
            sys.stderr.write(f"error: could not launch {binary}: {exc}\n")
            return 1
        return int(proc.returncode)

    try:
        child = subprocess.Popen(
            [str(binary)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        sys.stderr.write(f"error: could not launch {binary}: {exc}\n")
        return 1
    sys.stderr.write(
        f"groket hud: background pid {child.pid} "
        f"(summon: {summon_hint}; hotkey {chord_hint}; not in Dock or Cmd+Tab)\n"
    )
    return 0


__all__ = [
    "build_hud",
    "build_hud_debug",
    "default_summon_socket_path",
    "ensure_hud_binary",
    "find_hud_binary",
    "hud_binary_is_stale",
    "hud_checkout_dir",
    "hud_process_running",
    "install_desktop",
    "launch_hud_dev",
    "launch_tauri_hud",
    "send_summon_command",
    "stop_hud_processes",
    "summon_socket_accepts",
]
