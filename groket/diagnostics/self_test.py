"""Host dependency checks for evals and the TUI.

Probes work-directory writability, Docker reachability, Grok host auth/config,
optional CLI and models cache. Used by ``groket self-test`` and the in-app
self-test modal.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CheckResult:
    """One self-test row."""

    id: str
    name: str
    ok: bool
    detail: str = ""
    required: bool = True  # False = advisory (warn, not fail overall)

    @property
    def level(self) -> str:
        if self.ok:
            return "ok"
        return "error" if self.required else "warn"


@dataclass
class SelfTestReport:
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.required)

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if not c.ok and not c.required)

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if not c.ok and c.required)

    def lines(self) -> list[str]:
        out: list[str] = []
        for c in self.checks:
            mark = "OK" if c.ok else ("WARN" if not c.required else "FAIL")
            line = f"[{mark}] {c.name}"
            if c.detail:
                line += f" — {c.detail}"
            out.append(line)
        summary = "PASS" if self.ok else "FAIL"
        out.append(
            f"Result: {summary}  (required fails={self.fail_count}, warnings={self.warn_count})"
        )
        return out


def _check_docker(work_dir: Path | None) -> CheckResult:
    """Docker info can hang if the socket is wedged — bound with a short timeout."""
    import threading

    result_box: list[CheckResult | None] = [None]

    def _run() -> None:
        try:
            from ..docker.orchestrator import DockerOrchestrator
            from ..paths import default_work_dir

            root = Path(work_dir).expanduser() if work_dir else default_work_dir()
            orch = DockerOrchestrator(root / "runs")
            if orch.check_docker_available():
                result_box[0] = CheckResult(
                    id="docker",
                    name="Docker daemon",
                    ok=True,
                    detail="reachable (docker info)",
                )
            else:
                result_box[0] = CheckResult(
                    id="docker",
                    name="Docker daemon",
                    ok=False,
                    detail="not reachable — start Docker or fix DOCKER_HOST",
                    required=True,
                )
        except Exception as exc:
            result_box[0] = CheckResult(
                id="docker",
                name="Docker daemon",
                ok=False,
                detail=str(exc)[:200],
                required=True,
            )

    th = threading.Thread(target=_run, name="groket-selftest-docker", daemon=True)
    th.start()
    th.join(timeout=8.0)
    if th.is_alive() or result_box[0] is None:
        return CheckResult(
            id="docker",
            name="Docker daemon",
            ok=False,
            detail="timed out after 8s — daemon stuck or socket blocked",
            required=True,
        )
    return result_box[0]


def _check_auth_json() -> CheckResult:
    auth = Path.home() / ".grok" / "auth.json"
    if not auth.is_file():
        return CheckResult(
            id="grok_auth",
            name="Grok auth (~/.grok/auth.json)",
            ok=False,
            detail="missing — run `grok` login / auth on the host",
            required=True,
        )
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            id="grok_auth",
            name="Grok auth (~/.grok/auth.json)",
            ok=False,
            detail=f"unreadable: {exc}",
            required=True,
        )
    if not isinstance(data, dict) or not data:
        return CheckResult(
            id="grok_auth",
            name="Grok auth (~/.grok/auth.json)",
            ok=False,
            detail="empty or not an object",
            required=True,
        )
    # Shape varies; any non-empty object with common token-ish keys is fine.
    keys = set(data.keys())
    interesting = keys & {
        "accessToken",
        "access_token",
        "token",
        "apiKey",
        "api_key",
        "turn_started",
        "user_message_chunk",
        "accounts",
        "credentials",
    }
    size = auth.stat().st_size
    hint = f"{size} bytes"
    if interesting:
        hint += f", keys include {', '.join(sorted(interesting)[:5])}"
    else:
        hint += f", keys={len(keys)}"
    return CheckResult(
        id="grok_auth",
        name="Grok auth (~/.grok/auth.json)",
        ok=True,
        detail=hint,
        required=True,
    )


def _check_grok_config() -> CheckResult:
    cfg = Path.home() / ".grok" / "config.toml"
    if not cfg.is_file():
        return CheckResult(
            id="grok_config",
            name="Grok config (~/.grok/config.toml)",
            ok=False,
            detail="missing — optional for some flows but evals usually mount it",
            required=False,
        )
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult(
            id="grok_config",
            name="Grok config (~/.grok/config.toml)",
            ok=False,
            detail=str(exc)[:160],
            required=False,
        )
    return CheckResult(
        id="grok_config",
        name="Grok config (~/.grok/config.toml)",
        ok=True,
        detail=f"{len(text)} bytes",
        required=False,
    )


def _check_grok_cli() -> CheckResult:
    path = shutil.which("grok")
    if not path:
        return CheckResult(
            id="grok_cli",
            name="Grok CLI on PATH",
            ok=False,
            detail="not found — containers use image-bundled grok; host CLI optional",
            required=False,
        )
    return CheckResult(
        id="grok_cli",
        name="Grok CLI on PATH",
        ok=True,
        detail=path,
        required=False,
    )


def _check_work_dir(work_dir: Path | None) -> CheckResult:
    from ..paths import default_work_dir

    root = Path(work_dir).expanduser() if work_dir else default_work_dir()

    try:
        root.mkdir(parents=True, exist_ok=True)
        runs = root / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        probe = runs / ".groket-write-probe"
        probe.write_text("ok\n", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return CheckResult(
            id="work_dir",
            name="Work directory writable",
            ok=True,
            detail=str(root),
            required=True,
        )
    except OSError as exc:
        return CheckResult(
            id="work_dir",
            name="Work directory writable",
            ok=False,
            detail=f"{root}: {exc}",
            required=True,
        )


def _check_models_cache() -> CheckResult:
    cache = Path.home() / ".grok" / "models_cache.json"
    if not cache.is_file():
        return CheckResult(
            id="models_cache",
            name="Models cache (~/.grok/models_cache.json)",
            ok=False,
            detail="missing — run `grok models` once for offline model lists",
            required=False,
        )
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        n = len(data) if isinstance(data, (list, dict)) else 0
        return CheckResult(
            id="models_cache",
            name="Models cache (~/.grok/models_cache.json)",
            ok=True,
            detail=f"present ({n} entries)" if n else "present",
            required=False,
        )
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            id="models_cache",
            name="Models cache (~/.grok/models_cache.json)",
            ok=False,
            detail=str(exc)[:160],
            required=False,
        )


def _check_share_capability() -> CheckResult:
    """Advisory: whether ``grok share`` is entitled on this host account."""
    try:
        from ..runs.live_share import probe_host_share_capability

        cap = probe_host_share_capability()
    except Exception as exc:
        return CheckResult(
            id="grok_share",
            name="Grok session sharing",
            ok=False,
            detail=f"probe failed: {exc}"[:160],
            required=False,
        )
    if cap.available:
        return CheckResult(
            id="grok_share",
            name="Grok session sharing",
            ok=True,
            detail=f"available ({cap.reason})",
            required=False,
        )
    return CheckResult(
        id="grok_share",
        name="Grok session sharing",
        ok=False,
        detail=f"unavailable ({cap.reason}) — eval containers set SHARE_DISABLE=1"[:200],
        required=False,
    )


def _check_session_display() -> CheckResult:
    """Advisory: which display protocol the seat is using."""
    import os

    wayland = (os.environ.get("WAYLAND_DISPLAY") or "").strip()
    x11 = (os.environ.get("DISPLAY") or "").strip()
    if wayland:
        detail = f"Wayland ({wayland})"
        if x11:
            detail += f"; Xwayland DISPLAY={x11}"
        detail += (
            " — HUD summon: groket hud --toggle (forwards XDG_ACTIVATION_TOKEN) "
            "/ tray (no X11 hotkey)"
        )
        return CheckResult(
            id="session_display",
            name="Session display",
            ok=True,
            detail=detail,
            required=False,
        )
    if x11:
        return CheckResult(
            id="session_display",
            name="Session display",
            ok=True,
            detail=f"X11 ({x11}) — in-process global hotkey available",
            required=False,
        )
    return CheckResult(
        id="session_display",
        name="Session display",
        ok=False,
        detail="neither WAYLAND_DISPLAY nor DISPLAY set — HUD needs a graphical seat",
        required=False,
    )


def _check_sway_socket() -> CheckResult:
    """Advisory: Sway IPC socket when on a Sway seat."""
    import os

    sock = (os.environ.get("SWAYSOCK") or "").strip()
    if not sock:
        return CheckResult(
            id="sway_socket",
            name="Sway IPC (SWAYSOCK)",
            ok=True,
            detail="unset (not a Sway session, or nested shell without env)",
            required=False,
        )
    path = Path(sock)
    if path.exists():
        return CheckResult(
            id="sway_socket",
            name="Sway IPC (SWAYSOCK)",
            ok=True,
            detail=f"{path} — overlay place (float/center); focus is xdg-activation",
            required=False,
        )
    return CheckResult(
        id="sway_socket",
        name="Sway IPC (SWAYSOCK)",
        ok=False,
        detail=f"SWAYSOCK set but missing: {path}",
        required=False,
    )


def _check_hud_summon_socket() -> CheckResult:
    """Advisory: whether a long-lived HUD is accepting compositor summon commands."""
    from ..hud.launch import default_summon_socket_path, summon_socket_accepts

    path = default_summon_socket_path()
    if summon_socket_accepts(path):
        return CheckResult(
            id="hud_summon",
            name="HUD summon socket",
            ok=True,
            detail=f"listening at {path} (groket hud --toggle)",
            required=False,
        )
    return CheckResult(
        id="hud_summon",
        name="HUD summon socket",
        ok=False,
        detail=f"not listening ({path}) — start with: groket hud",
        required=False,
    )


def run_self_test(*, work_dir: Path | None = None) -> SelfTestReport:
    """Run all host checks. Safe to call from UI worker threads."""
    checks = [
        _check_work_dir(work_dir),
        _check_docker(work_dir),
        _check_auth_json(),
        _check_grok_config(),
        _check_grok_cli(),
        _check_models_cache(),
        _check_share_capability(),
        _check_session_display(),
        _check_sway_socket(),
        _check_hud_summon_socket(),
    ]
    return SelfTestReport(checks=checks)
