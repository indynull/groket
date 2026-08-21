"""Built-in export renderers (human file dialects).

Profiles select a renderer by id (``ExportSpec.renderer``). Collectors still own
raw units; this module shapes session summaries and other synthesised text.

Built-ins: ``markdown`` (default), ``plain``, ``org``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Product-backed dialect ids (profiles may set renderer to one of these).
BUILTIN_RENDERERS: frozenset[str] = frozenset({"markdown", "plain", "org"})
DEFAULT_RENDERER = "markdown"


@dataclass(frozen=True)
class SessionSummaryData:
    """Domain facts for a human session summary (export; not UI chrome)."""

    session_id: str
    title: str = ""
    model: str = ""
    outcome: str = ""
    duration_label: str = ""
    summary_text: str = ""
    event_count: int = 0
    tool_call_count: int = 0
    tool_error_count: int = 0
    turn_count: int = 0
    context_label: str = ""
    task_id: str = ""
    run_id: str = ""
    git_repo: str = ""
    git_branch: str = ""
    created_at: str = ""
    persona_id: str = ""
    # Extra capability / MCP / skills block (already formatted, light markup OK).
    usage_block: str = ""
    fields: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def normalize_renderer_id(renderer: str | None) -> str:
    """Return a known builtin id, or *renderer* stripped (for future plugins)."""
    rid = (renderer or DEFAULT_RENDERER).strip() or DEFAULT_RENDERER
    return rid


def is_builtin_renderer(renderer: str | None) -> bool:
    """True when *renderer* is a product builtin dialect."""
    return normalize_renderer_id(renderer) in BUILTIN_RENDERERS


def report_file_extension(renderer: str | None) -> str:
    """Filename suffix for one summary human file."""
    rid = normalize_renderer_id(renderer)
    if rid == "org":
        return ".org"
    if rid == "plain":
        return ".txt"
    return ".md"


def session_summary_body(
    data: SessionSummaryData,
    *,
    renderer: str | None = None,
) -> str:
    """Render *data* as a human session summary in the given dialect."""
    rid = normalize_renderer_id(renderer)
    if rid == "org":
        return _session_summary_org(data)
    if rid == "plain":
        return _session_summary_plain(data)
    return _session_summary_markdown(data)


def _summary_kv_rows(data: SessionSummaryData) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("Session", data.session_id or "—")]
    if data.title and data.title != data.session_id:
        rows.append(("Title", data.title))
    if data.model:
        rows.append(("Model", data.model))
    if data.outcome:
        rows.append(("Outcome", data.outcome))
    if data.duration_label:
        rows.append(("Duration", data.duration_label))
    if data.event_count:
        rows.append(("Events", str(data.event_count)))
    if data.tool_call_count or data.tool_error_count:
        tools = str(data.tool_call_count)
        if data.tool_error_count:
            tools += f" ({data.tool_error_count} errors)"
        rows.append(("Tool calls", tools))
    if data.turn_count:
        rows.append(("Turns", str(data.turn_count)))
    if data.context_label:
        rows.append(("Context", data.context_label))
    if data.task_id:
        rows.append(("Task", data.task_id))
    if data.run_id:
        rows.append(("Run", data.run_id))
    if data.persona_id:
        rows.append(("Persona", data.persona_id))
    if data.git_repo:
        rows.append(("Repo", data.git_repo))
    if data.git_branch:
        rows.append(("Branch", data.git_branch))
    if data.created_at:
        rows.append(("Created", data.created_at))
    for k, v in data.fields:
        if k and v:
            rows.append((k, v))
    return rows


def _session_summary_markdown(data: SessionSummaryData) -> str:
    title = (data.title or data.session_id or "session").strip()
    lines: list[str] = [f"# {title}", ""]
    for key, val in _summary_kv_rows(data):
        lines.append(f"- **{key}:** {val}")
    lines.append("")
    if data.summary_text.strip():
        lines.extend(["## Session summary", "", data.summary_text.strip(), ""])
    if data.usage_block.strip():
        lines.extend(["## Usage", "", data.usage_block.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _session_summary_org(data: SessionSummaryData) -> str:
    title = (data.title or data.session_id or "session").strip()
    lines: list[str] = [
        f"#+TITLE: {title}",
        "#+AUTHOR: groket",
        "",
        "* Meta",
        "",
    ]
    for key, val in _summary_kv_rows(data):
        lines.append(f"- {key}: {val}")
    lines.append("")
    if data.summary_text.strip():
        lines.extend(["* Session summary", "", data.summary_text.strip(), ""])
    if data.usage_block.strip():
        usage = _adapt_markdownish_report_to_org(data.usage_block.strip() + "\n")
        # usage block may already be section-like; nest under Usage
        lines.extend(["* Usage", "", usage.rstrip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _session_summary_plain(data: SessionSummaryData) -> str:
    title = (data.title or data.session_id or "session").strip()
    lines: list[str] = [title, ""]
    for key, val in _summary_kv_rows(data):
        lines.append(f"{key}: {val}")
    lines.append("")
    if data.summary_text.strip():
        lines.extend(["Session summary", data.summary_text.strip(), ""])
    if data.usage_block.strip():
        lines.extend(["Usage", _strip_light_markdown(data.usage_block).rstrip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _adapt_markdownish_report_to_org(text: str) -> str:
    """Best-effort map of common markdown headings to Org for usage blocks."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("# "):
            out.append(f"#+TITLE: {line[2:].strip()}")
        elif line.startswith("### "):
            out.append(f"** {line[4:].strip()}")
        elif line.startswith("## "):
            out.append(f"* {line[3:].strip()}")
        elif line.startswith("#"):
            # Other ATX depths → level-1 Org heading
            stripped = line.lstrip("#").strip()
            out.append(f"* {stripped}" if stripped else line)
        else:
            # Drop bold markers lightly
            out.append(line.replace("**", ""))
    body = "\n".join(out)
    return body if body.endswith("\n") else body + "\n"


def _strip_light_markdown(text: str) -> str:
    """Remove common ATX/bold markers for plain reports."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.lstrip("#").strip() if line.startswith("#") else line
        out.append(s.replace("**", ""))
    body = "\n".join(out)
    return body if body.endswith("\n") else body + "\n"


__all__ = [
    "BUILTIN_RENDERERS",
    "DEFAULT_RENDERER",
    "SessionSummaryData",
    "is_builtin_renderer",
    "normalize_renderer_id",
    "report_file_extension",
    "session_summary_body",
]
