"""Export a session via an :class:`~groket.session.export_spec.ExportSpec`.

Default profile ``archive-full`` writes under ``~/.groket/reports/`` as
``.tar.gz`` (or a directory when packaging is ``dir``).

Selected units (only written when the data exists)::

    grok-trace.tar.gz   # exact ``grok trace --local`` (CLI only; no fallback)
    run/                # eval volume (recipe, launch, prompt, turn gate, …)
    flags.json          # operator flags (session or config-home fallback)
    notes/              # operator_notes.toml from the notes store
    README.txt
    manifest.json

Profiles: built-ins plus ``~/.groket/export_profiles/*.yaml``. See
:mod:`groket.session.export_spec`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from shutil import which

from ..models import JsonObject, as_json_object
from ..notes import collect_notes_for_export
from ..parser import parse_timeline
from ..paths import is_run_dir_name, reports_dir
from .export_render import (
    SessionSummaryData,
    report_file_extension,
    session_summary_body,
)
from .export_spec import (
    DEFAULT_PROFILE_ID,
    ExportSpec,
    IncludeUnit,
    Packaging,
    get_export_profile,
)
from .subagents import subagent_runs_for_session
from .turns import event_display_turn_map, segment_timeline_turns

logger = logging.getLogger(__name__)

# Nested member: official grok-trace archive inside the operator export bundle.
GROK_TRACE_ARCHIVE_NAME = "grok-trace.tar.gz"

# Top-level names under a run volume that are not exported (noise / huge / other sessions).
_RUN_SKIP_NAMES = frozenset(
    {
        "session_search.sqlite",
        "session_search.sqlite-wal",
        "session_search.sqlite-shm",
    }
)

# Core members always present in official ``grok trace`` archives (even if empty).
_GROK_TRACE_CORE_FILES = frozenset(
    {
        "export_metadata.json",
        "trace_config.json",
        "summary.json",
        "events.jsonl",
        "chat_history.jsonl",
        "prompt_context.json",
        "system_prompt.txt",
    }
)

# Manifest schema for this outer bundle layout (bump when fields/layout change).
_MANIFEST_SCHEMA = 8


@dataclass
class ExportBundleResult:
    """Outcome of :func:`export_session_bundle`."""

    path: Path
    session_id: str
    arcnames: list[str] = field(default_factory=list)
    profile_id: str = DEFAULT_PROFILE_ID
    packaging: str = Packaging.TAR_GZ.value


def run_volume_for_session(session_dir: Path) -> Path | None:
    """Return the ``runs/traces/<container>/`` volume for *session_dir*, if any."""
    p = Path(session_dir).expanduser().resolve()
    for anc in p.parents:
        if is_run_dir_name(anc.name):
            return anc
        if anc.name == "traces":
            break
    return None


def default_bundle_path(
    session_id: str,
    *,
    dest_dir: Path | None = None,
    packaging: Packaging = Packaging.TAR_GZ,
) -> Path:
    """Default outer path under reports dir for *packaging*."""
    root = Path(dest_dir) if dest_dir is not None else reports_dir()
    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in (session_id or "session"))
    base = f"{safe}-{ts}"
    if packaging is Packaging.DIR:
        return root / base
    return root / f"{base}.tar.gz"


def _session_id(session_dir: Path) -> str:
    return Path(session_dir).name.strip() or "session"


def build_grok_trace_archive(session_dir: Path, out_tar: Path) -> None:
    """Write *out_tar* via ``grok trace --local`` only (exact CLI bytes).

    Links *session_dir* into ``~/.grok/sessions`` so the CLI can resolve the
    session id, then copies the CLI output as-is.

    :raises RuntimeError: ``grok`` missing, link failure, CLI error, or empty output.
    """
    grok = which("grok")
    if not grok:
        raise RuntimeError(
            "grok CLI not found on PATH; session export requires "
            "`grok trace --local` (no fallback packer)"
        )
    sid = _session_id(session_dir)
    session_dir = Path(session_dir).expanduser().resolve()
    if not session_dir.is_dir():
        raise RuntimeError(f"session directory not found: {session_dir}")
    out_tar = Path(out_tar)
    out_tar.parent.mkdir(parents=True, exist_ok=True)

    sessions_root = Path.home() / ".grok" / "sessions"
    probe = sessions_root / f"%2Ftmp%2Fgroket-export-{os.getpid()}-{sid[:8]}"
    link = probe / sid
    try:
        probe.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            if link.is_symlink() or link.is_file():
                link.unlink()
            else:
                shutil.rmtree(link)
        link.symlink_to(session_dir, target_is_directory=True)
        proc = subprocess.run(
            [grok, "trace", "--local", sid, "-o", str(out_tar)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        err = (proc.stderr or proc.stdout or "").strip()
        if proc.returncode != 0 or not out_tar.is_file() or out_tar.stat().st_size <= 0:
            if out_tar.is_file():
                try:
                    out_tar.unlink()
                except OSError:
                    pass
            detail = err[:500] if err else "empty output"
            raise RuntimeError(f"grok trace --local failed (rc={proc.returncode}): {detail}")
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as exc:
        if out_tar.is_file():
            try:
                out_tar.unlink()
            except OSError:
                pass
        raise RuntimeError(f"grok trace --local error: {exc}") from exc
    finally:
        try:
            if link.is_symlink() or link.is_file():
                link.unlink(missing_ok=True)
            elif link.is_dir():
                shutil.rmtree(link, ignore_errors=True)
            if probe.is_dir():
                try:
                    probe.rmdir()
                except OSError:
                    shutil.rmtree(probe, ignore_errors=True)
        except OSError:
            logger.debug("cleanup of grok sessions probe failed", exc_info=True)


def grok_trace_member_paths(session_id: str) -> frozenset[str]:
    """Archive member paths for the official core grok-trace files."""
    sid = (session_id or "").strip() or "session"
    return frozenset(f"{sid}/{name}" for name in _GROK_TRACE_CORE_FILES)


def assert_grok_trace_archive_shape(trace_tar: Path, session_id: str) -> list[str]:
    """Validate *trace_tar* is an official-shaped grok-trace archive.

    :returns: Sorted member names inside the archive.
    :raises RuntimeError: Layout does not match ``grok trace`` output.
    """
    sid = (session_id or "").strip() or "session"
    path = Path(trace_tar)
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError(f"grok-trace archive missing or empty: {path}")
    try:
        with tarfile.open(path, "r:gz") as tf:
            names = [m.name for m in tf.getmembers() if m.name]
    except tarfile.TarError as exc:
        raise RuntimeError(f"invalid grok-trace archive: {path}: {exc}") from exc

    prefix = f"{sid}/"
    if not any(n == sid or n.startswith(prefix) for n in names):
        raise RuntimeError(
            f"grok-trace archive must root under {sid}/ (got tops: "
            f"{sorted({n.split('/')[0] for n in names})[:8]})"
        )
    foreign = sorted(
        {n.split("/")[0] for n in names if n and n != sid and not n.startswith(prefix)}
    )
    if foreign:
        raise RuntimeError(f"grok-trace archive has unexpected top-level members: {foreign}")
    missing = sorted(grok_trace_member_paths(sid) - set(names))
    if missing:
        raise RuntimeError(f"grok-trace archive missing official core files: {missing}")
    return sorted(names)


def _add_tree(tf: tarfile.TarFile, src: Path, arc_prefix: str) -> list[str]:
    """Add *src* file or directory under *arc_prefix*; return arcnames."""
    names: list[str] = []
    src = Path(src)
    if not src.exists():
        return names
    if src.is_file():
        arc = arc_prefix.rstrip("/")
        tf.add(src, arcname=arc)
        names.append(arc)
        return names
    for path in sorted(src.rglob("*")):
        if path.is_symlink() or path.is_file():
            rel = path.relative_to(src).as_posix()
            arc = f"{arc_prefix.rstrip('/')}/{rel}"
            tf.add(path, arcname=arc)
            names.append(arc)
        elif path.is_dir() and not any(path.iterdir()):
            rel = path.relative_to(src).as_posix()
            arc = f"{arc_prefix.rstrip('/')}/{rel}"
            tf.add(path, arcname=arc)
            names.append(arc)
    return names


def _staging_member_paths(staging: Path, *, skip: frozenset[str] | set[str]) -> list[str]:
    """Relative paths under *staging* that will be packed (same rules as :func:`_add_tree`)."""
    names: list[str] = []
    for path in sorted(staging.iterdir()):
        if path.name in skip:
            continue
        if path.is_file():
            names.append(path.name)
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_symlink() or child.is_file():
                    rel = child.relative_to(staging).as_posix()
                    names.append(rel)
                elif child.is_dir() and not any(child.iterdir()):
                    rel = child.relative_to(staging).as_posix()
                    names.append(rel)
    return names


def _collect_run_volume_files(run_vol: Path, staging: Path) -> None:
    """Copy run-volume artifacts into *staging* (excludes nested session trees)."""
    dest = staging / "run"
    copied = False
    for child in sorted(run_vol.iterdir()):
        name = child.name
        if name in _RUN_SKIP_NAMES:
            continue
        if name.endswith(".stage"):
            continue
        if name.startswith("%2F") or name in ("workspace",):
            ph = child / "prompt_history.jsonl"
            if ph.is_file():
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ph, dest / "prompt_history.jsonl")
                copied = True
            continue
        if child.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, dest / name)
            copied = True
        elif child.is_dir() and name.startswith("."):
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(child, dest / name, symlinks=True, dirs_exist_ok=True)
            copied = True
    if not copied and dest.is_dir():
        try:
            dest.rmdir()
        except OSError:
            pass


def _collect_flags(session_dir: Path, staging: Path) -> None:
    """Write operator flags to outer ``flags.json`` when any exist.

    Flags are a groket annotation, not part of the official nested
    ``grok-trace.tar.gz`` (``grok trace`` does not pack them). Load from the
    session file or config-home fallback via :func:`~groket.flags.load_flags`.
    """
    from ..flags import load_flags

    flags = load_flags(session_dir)
    if not flags:
        return
    payload = [fl.model_dump() for fl in flags]
    (staging / "flags.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _collect_operator_notes(session_dir: Path, staging: Path) -> None:
    """Embed turn-linked operator notes under ``notes/``."""
    collect_notes_for_export(session_dir, staging / "notes")


def _gather_session_summary_data(session_dir: Path) -> SessionSummaryData:
    """Load meta / timeline / usage into :class:`SessionSummaryData`."""
    from ..parser import load_session_meta, parse_timeline
    from ..utils import fmt_duration
    from .turns import segment_timeline_turns
    from .usage_stats import collect_session_usage, format_usage_markdown

    meta = load_session_meta(session_dir)
    timeline = parse_timeline(session_dir)
    tool_calls = [e for e in timeline if e.event_type == "tool_call"]
    tool_errs = sum(1 for e in tool_calls if e.is_error)
    turn_count = 0
    try:
        turn_count = len(segment_timeline_turns(timeline))
    except Exception:
        logger.debug("turn segmentation failed for export summary", exc_info=True)
    dur = ""
    if meta.duration_seconds:
        dur = fmt_duration(meta.duration_seconds)
    context = ""
    if meta.has_context_usage:
        context = (meta.context_usage_str or meta.context_usage_compact or "").strip()
    created = (meta.created_at or "").strip()
    if "T" in created and len(created) > 19:
        created = created[:19].replace("T", " ")
    usage_block = ""
    persona = ""
    try:
        usage = collect_session_usage(session_dir, timeline)
        persona = (usage.persona_id or "").strip()
        usage_block = format_usage_markdown(usage).strip()
    except Exception:
        logger.debug("usage stats failed for export summary", exc_info=True)
    return SessionSummaryData(
        session_id=(meta.session_id or session_dir.name or "").strip(),
        title=(meta.title or "").strip(),
        model=(meta.model_display or meta.model_id or "").strip(),
        outcome=(meta.turn_outcome or "").strip(),
        duration_label=dur,
        summary_text=(meta.summary_text or "").strip(),
        event_count=len(timeline),
        tool_call_count=len(tool_calls),
        tool_error_count=tool_errs,
        turn_count=turn_count,
        context_label=context,
        task_id=(meta.task_id or "").strip(),
        run_id=(meta.run_id or "").strip(),
        git_repo=(meta.git_repo or "").strip(),
        git_branch=(meta.git_branch or "").strip(),
        created_at=created,
        persona_id=persona,
        usage_block=usage_block,
    )


def _collect_summary(session_dir: Path, staging: Path, *, renderer: str) -> None:
    """Write ``human/summary.<ext>`` via the active builtin renderer."""
    data = _gather_session_summary_data(session_dir)
    if not data.session_id and not data.summary_text and data.event_count == 0:
        return
    ext = report_file_extension(renderer)
    body = session_summary_body(data, renderer=renderer)
    dest_dir = staging / "human"
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / f"summary{ext}").write_text(body, encoding="utf-8")


def _write_readme(staging: Path, *, sid: str, spec: ExportSpec) -> None:
    units = ", ".join(sorted(u.value for u in spec.include)) or "(none)"
    text = (
        f"groket session export\n"
        f"=====================\n\n"
        f"session_id: {sid}\n"
        f"profile: {spec.profile_id}\n"
        f"packaging: {spec.packaging.value}\n"
        f"include: {units}\n"
        f"renderer: {spec.renderer}\n\n"
        f"Contents (when selected and present)\n"
        f"------------------------------------\n"
        f"{GROK_TRACE_ARCHIVE_NAME}\n"
        f"                 Nested archive from: grok trace --local {sid}\n"
        f"                 (exact CLI bytes). Grok session files only — not\n"
        f"                 groket flags/notes/run extras.\n\n"
        f"run/             Eval launch artifacts under a work volume.\n"
        f"human/summary.*  Session overview (meta, counts, usage) in the\n"
        f"                 profile renderer dialect (.md / .org / .txt).\n"
        f"flags.json       Operator flags (session or ~/.groket/flags fallback).\n"
        f"notes/           operator_notes.toml when notes exist.\n"
        f"                 Schema: ~/.groket/notes_schema.toml (not bundled).\n"
        f"children/<id>/{GROK_TRACE_ARCHIVE_NAME}\n"
        f"                 Official grok-trace of each openable child.\n"
        f"manifest.json    Inventory of this outer bundle.\n\n"
        f"To recover the pure grok-trace archive (when included)::\n"
        f"  tar -xzf <this-bundle>.tar.gz {GROK_TRACE_ARCHIVE_NAME}\n"
        f"  # or copy from a dir export\n"
    )
    (staging / "README.txt").write_text(text, encoding="utf-8")


def _collect_child_traces(session_dir: Path, staging: Path) -> list[JsonObject]:
    """Write ``children/<id>/grok-trace.tar.gz`` for each openable child."""
    timeline = parse_timeline(session_dir)
    segs = segment_timeline_turns(timeline)
    runs = subagent_runs_for_session(session_dir, timeline, segs, event_display_turn_map(segs))
    written: list[JsonObject] = []
    parent = Path(session_dir).resolve()
    for run in runs:
        if not run.openable or run.child_path is None:
            continue
        child = Path(run.child_path).resolve()
        if child == parent:
            continue
        cid = run.child_session_id or run.subagent_id or child.name
        dest = staging / "children" / cid / GROK_TRACE_ARCHIVE_NAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        build_grok_trace_archive(child, dest)
        written.append(
            {
                "sessionId": cid,
                "member": f"children/{cid}/{GROK_TRACE_ARCHIVE_NAME}",
            }
        )
    return written


def _assert_outer_layout(arcnames: list[str], *, sid: str, want_grok_trace: bool) -> None:
    """Fail if the outer package drifts from the nested-grok-trace contract."""
    if want_grok_trace:
        if GROK_TRACE_ARCHIVE_NAME not in arcnames:
            raise RuntimeError(f"export bundle missing {GROK_TRACE_ARCHIVE_NAME}")
        nested_tars = [n for n in arcnames if n.endswith(".tar.gz")]
        if GROK_TRACE_ARCHIVE_NAME not in nested_tars:
            raise RuntimeError(f"export must embed {GROK_TRACE_ARCHIVE_NAME}")
        extra = [n for n in nested_tars if n != GROK_TRACE_ARCHIVE_NAME]
        prefix = "children/"
        suffix = f"/{GROK_TRACE_ARCHIVE_NAME}"
        bad = [n for n in extra if not (n.startswith(prefix) and n.endswith(suffix))]
        if bad:
            raise RuntimeError(f"unexpected nested archives: {bad}")
    if any(n == sid or n.startswith(f"{sid}/") for n in arcnames):
        raise RuntimeError(
            f"session files must live inside {GROK_TRACE_ARCHIVE_NAME}, not outer {sid}/"
        )


def _pack_tar_gz(staging: Path, out: Path) -> list[str]:
    """Write staging tree to *out* as ``.tar.gz``; return arcnames."""
    arcnames: list[str] = []
    tmp_out = staging / "bundle.tar.gz"
    with tarfile.open(tmp_out, "w:gz") as tf:
        for path in sorted(staging.iterdir()):
            if path.name == "bundle.tar.gz":
                continue
            if path.is_file():
                tf.add(path, arcname=path.name)
                arcnames.append(path.name)
            elif path.is_dir():
                arcnames.extend(_add_tree(tf, path, path.name))
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        if out.is_dir():
            shutil.rmtree(out)
        else:
            out.unlink()
    shutil.move(str(tmp_out), str(out))
    return arcnames


def _pack_dir(staging: Path, out: Path) -> list[str]:
    """Copy staging tree to *out* directory; return relative member paths."""
    out = Path(out)
    if out.exists():
        if out.is_dir() and any(out.iterdir()):
            raise RuntimeError(f"export directory not empty: {out}")
        if out.is_file():
            raise RuntimeError(f"export path is a file, expected directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    members = _staging_member_paths(staging, skip=frozenset())
    for name in members:
        src = staging / name
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        else:
            shutil.copy2(src, dest)
    return members


def export_session_bundle(
    session_dir: Path,
    *,
    dest: Path | None = None,
    spec: ExportSpec | None = None,
    profile: str | None = None,
) -> ExportBundleResult:
    """Build a session export from *session_dir* using an :class:`ExportSpec`.

    Default profile is :data:`~groket.session.export_spec.DEFAULT_PROFILE_ID`
    (``archive-full``): nested official ``grok-trace.tar.gz`` plus optional
    groket extras. Fails hard if the profile requests ``grok_trace`` and the
    Grok CLI is unavailable.

    :param session_dir: Grok session directory (…/%2Fworkspace/<session_id>/).
    :param dest: Output path (``.tar.gz`` file or directory); default under
        :func:`~groket.paths.reports_dir` according to packaging.
    :param spec: Explicit export recipe (wins over *profile*).
    :param profile: Profile id from built-ins / ``~/.groket/export_profiles/``.
    :returns: :class:`ExportBundleResult` with the path written.
    :raises FileNotFoundError: Session directory missing.
    :raises KeyError: Unknown *profile*.
    :raises RuntimeError: ``grok`` missing when required, CLI export failed,
        archive invalid, or destination conflicts.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    if not session_dir.is_dir():
        raise FileNotFoundError(f"session directory not found: {session_dir}")
    resolved = spec if spec is not None else get_export_profile(profile)
    sid = _session_id(session_dir)
    out = Path(dest) if dest is not None else default_bundle_path(sid, packaging=resolved.packaging)
    if resolved.packaging is Packaging.TAR_GZ:
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)

    want_trace = resolved.includes(IncludeUnit.GROK_TRACE)
    nested_members: list[str] = []

    with tempfile.TemporaryDirectory(prefix="groket-bundle-") as tmp:
        staging = Path(tmp)

        child_members: list[JsonObject] = []
        if want_trace:
            nested = staging / GROK_TRACE_ARCHIVE_NAME
            build_grok_trace_archive(session_dir, nested)
            nested_members = assert_grok_trace_archive_shape(nested, sid)
            child_members = _collect_child_traces(session_dir, staging)

        if resolved.includes(IncludeUnit.RUN):
            run_vol = run_volume_for_session(session_dir)
            if run_vol is not None:
                try:
                    _collect_run_volume_files(run_vol, staging)
                except OSError:
                    logger.warning(
                        "Failed to collect run volume files from %s", run_vol, exc_info=True
                    )

        if resolved.includes(IncludeUnit.SUMMARY):
            try:
                _collect_summary(session_dir, staging, renderer=resolved.renderer)
            except OSError:
                logger.debug("session summary collect failed", exc_info=True)
            except Exception:
                logger.warning("session summary collect failed", exc_info=True)

        if resolved.includes(IncludeUnit.FLAGS):
            try:
                _collect_flags(session_dir, staging)
            except OSError:
                logger.debug("flags collect failed", exc_info=True)

        if resolved.includes(IncludeUnit.NOTES):
            try:
                _collect_operator_notes(session_dir, staging)
            except OSError:
                logger.debug("operator notes collect failed", exc_info=True)

        if resolved.includes(IncludeUnit.README):
            _write_readme(staging, sid=sid, spec=resolved)

        skip_pack = frozenset({"bundle.tar.gz"})
        want_manifest = resolved.includes(IncludeUnit.MANIFEST)
        if want_manifest:
            (staging / "manifest.json").write_text("{}\n", encoding="utf-8")

        members = _staging_member_paths(staging, skip=skip_pack)
        if want_trace and GROK_TRACE_ARCHIVE_NAME not in members:
            raise RuntimeError(f"export missing nested {GROK_TRACE_ARCHIVE_NAME}")

        if want_manifest:
            manifest: JsonObject = as_json_object(
                {
                    "schema": _MANIFEST_SCHEMA,
                    "kind": "groket-session-export",
                    "session_id": sid,
                    "exported_at": datetime.now(UTC).isoformat(),
                    "profile": resolved.profile_id,
                    "packaging": resolved.packaging.value,
                    "include": sorted(u.value for u in resolved.include),
                    "renderer": resolved.renderer,
                    "renderer_options": resolved.renderer_options,
                    "grok_trace": GROK_TRACE_ARCHIVE_NAME if want_trace else None,
                    "grok_trace_members": nested_members if want_trace else [],
                    "children": child_members,
                    "members": members,
                }
            )
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
            )
            members = _staging_member_paths(staging, skip=skip_pack)

        try:
            if resolved.packaging is Packaging.DIR:
                arcnames = _pack_dir(staging, out)
            else:
                arcnames = _pack_tar_gz(staging, out)
        except OSError as exc:
            raise RuntimeError(f"failed to write export bundle: {out}: {exc}") from exc

        _assert_outer_layout(arcnames, sid=sid, want_grok_trace=want_trace)

    return ExportBundleResult(
        path=out.resolve(),
        session_id=sid,
        arcnames=arcnames,
        profile_id=resolved.profile_id,
        packaging=resolved.packaging.value,
    )


__all__ = [
    "GROK_TRACE_ARCHIVE_NAME",
    "ExportBundleResult",
    "assert_grok_trace_archive_shape",
    "build_grok_trace_archive",
    "default_bundle_path",
    "export_session_bundle",
    "grok_trace_member_paths",
    "run_volume_for_session",
]
