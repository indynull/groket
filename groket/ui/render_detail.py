"""Rich renderables for trace event / tool detail (ported from tools/trace_viewer.py)."""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from pathlib import Path

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.rule import Rule
from rich.style import Style
from rich.syntax import Syntax, SyntaxTheme
from rich.text import Text
from textual.app import App

from .. import event_types as et
from ..analysis.base import Finding
from ..models import Flag, JsonObject, JsonValue, ToolInputBag, TraceEvent
from ..session.jobs import (
    ScheduleTask,
    event_job_kind,
    job_duration_seconds,
    job_status_for_event,
    read_log_tail,
)
from ..session.subagents import (
    SubagentInspect,
    SubagentRun,
    subagent_duration_seconds,
    subagent_inspect,
)
from ..session.workflows import WorkflowRun, workflow_name_from_raw
from ..tool_display import (
    display_tool_output,
    format_tool_display,
    image_result_message,
    image_result_path,
    job_list_preview,
    task_fields_from_content,
)
from ..utils import fmt_duration
from .i18n import t
from .panel_render import looks_like_markdown
from .styles import (
    COMPLETE,
    FAILED,
    RUNNING,
    SYNTAX_THEME_DARK,
    severity_style,
    syntax_theme_for_app,
    tool_style,
)
from .styles import EVENT_TYPE_STYLE as KIND_STYLES

logger = logging.getLogger(__name__)
# Regexes stay in Python (not Fluent — catalogs are for UI copy only).
_RE_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_RE_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_RE_ANSI_ESC = re.compile(r"\x1b[@-Z\\-_]")
_RE_C0_NOISE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RE_CR = re.compile(r"\r\n?")
_RE_REPEATED_FFFD = re.compile(r"\ufffd{2,}")


def sanitize_console_text(text: str, *, for_display: bool = True) -> str:
    """Strip ANSI / control characters from terminal-like tool output.

    Preserves normal newlines and tabs; normalizes CR/CRLF; collapses spinner
    overwrite lines into readable plain text suitable for the detail pane.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    s = text
    s = s.replace("\r\n", "\n")
    s = _RE_ANSI_OSC.sub("", s)
    s = _RE_ANSI_CSI.sub("", s)
    s = _RE_ANSI_ESC.sub("", s)
    s = s.replace("\x1b", "")
    s = s.replace("\r", "\n")
    s = _RE_C0_NOISE.sub("", s)
    s = _RE_REPEATED_FFFD.sub("", s)
    if for_display:
        lines_out: list[str] = []
        for ln in s.split("\n"):
            t = ln.rstrip()
            if not t:
                if lines_out and lines_out[-1] == "":
                    continue
                lines_out.append("")
                continue
            printable = sum(1 for ch in t if ch.isprintable() or ch in "\t")
            if printable < max(1, len(t) // 4) and len(t) > 4:
                continue
            lines_out.append(t)
        s = "\n".join(lines_out)
        s = re.sub(r"\n{4,}", "\n\n\n", s)
    return s


def _looks_like_console_output(text: str, tool_name: str = "") -> bool:
    """Heuristic: treat as terminal stream (aggressive sanitize + text lexer)."""
    if tool_name in (
        "run_terminal_command",
        "get_command_or_subagent_output",
        "monitor",
        "wait_commands_or_subagents",
    ):
        return True
    if not text:
        return False
    sample = text[:4000]
    if "\x1b[" in sample or "\x1b]" in sample or "\r" in sample:
        return True
    noisy = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\n")
    return noisy > 8


_EXT_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".json": "json",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".css": "css",
    ".html": "html",
    ".xml": "xml",
    ".sql": "sql",
    ".rb": "ruby",
    ".diff": "diff",
    ".patch": "diff",
}


def set_static_renderable(widget, renderable: RenderableType) -> None:
    """Update a Static with Markdown/Group/Text safely.

    Skips the update when the operator has an active text selection on *widget*
    so live refresh / re-render does not clear the selection before copy.
    """
    screen = getattr(widget, "screen", None)
    sels = getattr(screen, "selections", None) if screen is not None else None
    if sels and widget in sels:
        return
    try:
        widget.update(renderable)
    except Exception:
        logger.debug(t("ui-failed-to-update-widget-with-renderable"), exc_info=True)
        widget.update(Text(str(renderable)))


class _SurfaceSyntaxTheme(SyntaxTheme):
    """Pygments token colors without the style's code-block background.

    Pygments paints ``bgcolor`` on every token. With ``background_color=
    "default"`` Rich then adds ``on default`` (often black) only behind
    characters, so the panel color shows in the rest of the line.
    """

    def __init__(self, name: str) -> None:
        self._inner = Syntax.get_theme(name)

    def get_style_for_token(self, token_type: tuple[str, ...]) -> Style:
        st = self._inner.get_style_for_token(token_type)
        return Style(
            color=st.color,
            bold=st.bold,
            italic=st.italic,
            underline=st.underline,
        )

    def get_background_style(self) -> Style:
        return Style.null()


def _syntax(code: str, lexer: str, line_numbers: bool = False, *, app: App | None = None) -> Syntax:
    name = syntax_theme_for_app(app) if app is not None else SYNTAX_THEME_DARK
    return Syntax(
        code or "",
        lexer,
        theme=_SurfaceSyntaxTheme(name),
        line_numbers=line_numbers,
        word_wrap=True,
    )


def _looks_json(s: str) -> bool:
    s = (s or "").lstrip()
    return bool(s) and s[0] in "{[" and (s.rstrip()[-1:] in "}]")


def _looks_diff(s: str) -> bool:
    if not s:
        return False
    hits = sum(
        1
        for ln in s.splitlines()[:40]
        if ln[:1] in "+-" or ln.startswith(("@@", t("ui-diff"), "--- ", "+++ "))
    )
    return hits >= 3


def _lang_from_path(path: str) -> str:
    p = (path or "").lower().split("?")[0]
    if p.rsplit("/", 1)[-1] == "dockerfile":
        return "dockerfile"
    return _EXT_LANG.get(Path(p).suffix, "")


def _path_hint(ri: dict) -> str:
    for k in ("target_file", "file_path", "path", "target_directory"):
        v = ri.get(k)
        if isinstance(v, str) and v:
            return v
    return ""


def _shell_command_text(ri: dict) -> str:
    """Primary shell command string from tool input (if any)."""
    for k in ("command", "cmd", "script"):
        v = ri.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _is_shell_tool(tname: str) -> bool:
    """Host shell / process tools whose primary body is a bash command."""
    return tname in (
        "run_terminal_command",
        "get_command_or_subagent_output",
        "monitor",
        "wait_commands_or_subagents",
        "kill_command_or_subagent",
    )


def _is_file_body_tool(tname: str) -> bool:
    """Tools whose *output* is usually a file dump (prefer path lexer)."""
    return tname in (
        "read_file",
        "search_replace",
        "write_file",
        "create_file",
        "edit_file",
        "apply_patch",
    )


def _looks_like_source_code(text: str) -> bool:
    """True when *text* looks like source (not prose / not a terminal dump).

    Used so ``tool_call_update`` bodies that are code still get a monospaced
    Syntax pane even without a path extension.
    """
    if not text or not text.strip():
        return False
    sample = text[:6000]
    lines = sample.splitlines()
    if len(lines) < 2:
        # Single-line snippets still count when clearly code-shaped.
        s = sample.lstrip()
        return bool(
            s.startswith(
                (
                    "def ",
                    "class ",
                    "fn ",
                    "func ",
                    "import ",
                    "package ",
                    "const ",
                    "let ",
                    "var ",
                    "#!/",
                )
            )
            or (" => " in s and ("{" in s or ";" in s))
        )
    code_hits = 0
    for ln in lines[:80]:
        st = ln.strip()
        if not st or st.startswith(("#", "//", "/*", "*", "--")):
            continue
        if st.endswith(("{", "}", ");", "};", "]:", ":")):
            code_hits += 1
        elif st.startswith(
            (
                "def ",
                "class ",
                "async ",
                "import ",
                "from ",
                "fn ",
                "func ",
                "pub ",
                "package ",
                "const ",
                "let ",
                "var ",
                "export ",
                "function ",
                "type ",
                "interface ",
                "impl ",
                "struct ",
                "enum ",
                "return ",
                "if ",
                "for ",
                "while ",
                "match ",
                "use ",
                "mod ",
                "#!",
            )
        ):
            code_hits += 1
        elif re.match(r"^(pub\s+)?(async\s+)?fn\s+\w+", st):
            code_hits += 1
        elif re.match(r"^[A-Za-z_][\w.]*\s*=\s*.+", st) and ("(" in st or st.endswith((";", ","))):
            code_hits += 1
    # Indentation density (source almost always indents).
    indented = sum(1 for ln in lines[:80] if ln[:1] in " \t" and ln.strip())
    if indented >= 3 and code_hits >= 2:
        return True
    return code_hits >= 4


def _guess_source_lexer(text: str) -> str:
    """Best-effort language from content when path is missing."""
    sample = (text or "")[:8000]
    head = sample.lstrip()
    if head.startswith("#!"):
        first = head.split("\n", 1)[0].lower()
        if "python" in first:
            return "python"
        if any(x in first for x in ("bash", "sh", "zsh")):
            return "bash"
        return "bash"
    # Prefer strong multi-signal checks over single keyword hits.
    py = sum(
        1
        for tok in (
            "def ",
            "class ",
            "import ",
            "from ",
            "async def ",
            "self.",
            "None",
            "True",
            "False",
        )
        if tok in sample
    )
    if py >= 3 or (
        "def " in sample and ":" in sample and ("self" in sample or "import " in sample)
    ):
        return "python"
    rs = sum(1 for tok in ("fn ", "impl ", "pub ", "let mut ", "use ", "::") if tok in sample)
    if rs >= 3 or ("fn " in sample and "->" in sample and "{" in sample):
        return "rust"
    go = sum(1 for tok in ("func ", "package ", ":=", "fmt.") if tok in sample)
    if go >= 3 or (sample.lstrip().startswith("package ") and "func " in sample):
        return "go"
    ts = sum(
        1
        for tok in ("interface ", "type ", ": string", ": number", "export ", "const ", "=>")
        if tok in sample
    )
    if ts >= 3 and ("=>" in sample or "export " in sample):
        if "interface " in sample or ": string" in sample or ": number" in sample:
            return "typescript"
        return "javascript"
    if "function " in sample and ("const " in sample or "=>" in sample or "export " in sample):
        return "javascript"
    if (
        head.startswith("<?xml")
        or head.startswith("<!DOCTYPE")
        or (head.startswith("<") and "</" in sample[:500])
    ):
        return "xml" if "html" not in head[:40].lower() else "html"
    if _looks_json(sample):
        return "json"
    if _looks_diff(sample):
        return "diff"
    return ""


def _guess_lexer(text: str, tool_name: str = "", path_hint: str = "") -> str:
    if path_hint:
        lang = _lang_from_path(path_hint)
        if lang:
            return lang
    if _looks_json(text):
        return "json"
    if _looks_diff(text):
        return "diff"
    if tool_name == "run_terminal_command":
        return "bash"
    head = (text or "").lstrip()
    if head.startswith("#!"):
        return "bash"
    if head.startswith("<?xml") or head.startswith("<!DOCTYPE"):
        return "xml"
    src = _guess_source_lexer(text)
    if src:
        return src
    if tool_name == "read_file" or _looks_like_source_code(text):
        # Unknown language but clearly code — monospaced Syntax ("text" lexer).
        return "text"
    return "text"


def _content_str(
    content: str | list[JsonValue] | JsonObject | None,
    *,
    sanitize: bool = False,
    tool_name: str = "",
) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        s = content
    else:
        try:
            s = json.dumps(content, indent=2, ensure_ascii=False)
        except Exception:
            s = str(content)
    if sanitize or _looks_like_console_output(s, tool_name):
        s = sanitize_console_text(s)
    return s


def _truncate_mid(
    s: str,
    head: int = 7000,
    tail: int = 5000,
    limit: int = 14000,
    *,
    truncate: bool = True,
) -> str:
    """Mid-body cap for *display*; when *truncate* is False keep the full string (yank)."""
    if not truncate or len(s) <= limit:
        return s
    return s[:head] + t("truncate-marker") + s[-tail:]


def _cap_str(s: str, limit: int, *, truncate: bool, marker: str | None = None) -> str:
    """Prefix cap for long fields; no-op when *truncate* is False.

    :param marker: Suffix after the cut. ``None`` uses the Fluent truncated
        marker; ``""`` means hard cut with no suffix (legacy search_replace).
    """
    if not truncate or len(s) <= limit:
        return s
    suffix = t("ui-truncated-1") if marker is None else marker
    return s[:limit] + suffix


def _render_tool_input(tname: str, ri: dict, *, truncate: bool = True) -> list:
    """Syntax-highlighted tool input sections (trace_viewer render_tool_detail)."""
    parts: list = []
    path_hint = _path_hint(ri)
    cmd = _shell_command_text(ri)
    if cmd and (_is_shell_tool(tname) or tname in ("run_terminal_command",) or "command" in ri):
        # Shell commands always bash-highlight (not plain Text / not JSON dump).
        parts.append(_syntax(cmd, "bash"))
        extra = {k: v for k, v in ri.items() if k not in ("command", "cmd", "script")}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "search_replace":
        fp = ri.get("file_path") or ri.get("target_file") or path_hint or ""
        if fp:
            parts.append(Text(t("tool-input-file", path=str(fp)), style="cyan"))
        lang = (
            _lang_from_path(str(fp))
            or _guess_source_lexer(str(ri.get("new_string") or ri.get("old_string") or ""))
            or "text"
        )
        old_s, new_s = (str(ri.get("old_string") or ""), str(ri.get("new_string") or ""))
        if old_s:
            parts.append(Text(t("tool-field-old-string"), style="red"))
            parts.append(
                _syntax(
                    _cap_str(old_s, 8000, truncate=truncate, marker=""), lang, line_numbers=True
                )
            )
        if new_s:
            parts.append(Text(t("tool-field-new-string"), style="green"))
            parts.append(
                _syntax(
                    _cap_str(new_s, 8000, truncate=truncate, marker=""), lang, line_numbers=True
                )
            )
        extra = {
            k: v
            for k, v in ri.items()
            if k not in ("file_path", "target_file", "old_string", "new_string")
        }
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "read_file":
        tf = ri.get("target_file") or ri.get("file_path") or path_hint
        if tf:
            parts.append(Text(t("tool-input-target-file", path=str(tf)), style="cyan"))
        extra = {k: v for k, v in ri.items() if k not in ("target_file", "file_path")}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        elif not tf:
            try:
                parts.append(_syntax(json.dumps(ri, indent=2, ensure_ascii=False), "json"))
            except Exception:
                parts.append(Text(str(ri)))
        return parts
    if tname == "grep":
        pat = ri.get("pattern")
        if pat is not None:
            parts.append(Text(t("tool-field-pattern"), style="magenta"))
            parts.append(_syntax(str(pat), "text"))
        extra = {k: v for k, v in ri.items() if k != "pattern"}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "list_dir":
        td = ri.get("target_directory") or path_hint
        if td:
            parts.append(Text(t("tool-input-target-directory", path=str(td)), style="blue"))
        extra = {k: v for k, v in ri.items() if k != "target_directory"}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "todo_write":
        try:
            parts.append(_syntax(json.dumps(ri, indent=2, ensure_ascii=False), "json"))
        except Exception:
            parts.append(Text(str(ri)))
        return parts
    # MCP via use_tool (or resolved name with tool_input payload).
    if tname in ("use_tool", "use-tool") or (
        isinstance(ri.get("tool_input"), dict) and ri.get("tool_name")
    ):
        mcp = str(ri.get("tool_name") or tname)
        parts.append(Text(t("tool-mcp-label", name=mcp), style="cyan"))
        ti = ri.get("tool_input")
        if isinstance(ti, dict):
            parts.append(Text(t("tool-input-section"), style="bright_blue"))
            try:
                parts.append(_syntax(json.dumps(ti, indent=2, ensure_ascii=False), "json"))
            except Exception:
                parts.append(Text(str(ti)))
        extra = {k: v for k, v in ri.items() if k not in ("tool_name", "tool_input")}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname == "search_tool":
        q = ri.get("query")
        if q is not None:
            parts.append(Text(t("tool-field-query"), style="bright_blue"))
            parts.append(Text(str(q)))
        extra = {k: v for k, v in ri.items() if k != "query"}
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    if tname in ("web_search", "spawn_subagent", "ask_user_question"):
        for key in ("query", "url", "prompt", "description", "question"):
            if key in ri and isinstance(ri[key], str) and ri[key].strip():
                parts.append(Text(f"{key}:", style="bright_blue"))
                val = _cap_str(str(ri[key]), 4000, truncate=truncate)
                if key in ("prompt", "description") and "\n" in val:
                    parts.append(Markdown(val))
                else:
                    parts.append(Text(val))
        extra = {
            k: v
            for k, v in ri.items()
            if k
            not in (
                "query",
                "url",
                "prompt",
                "description",
                "question",
                "variant",
                "backend",
            )
        }
        if extra:
            with suppress(Exception):
                parts.append(_syntax(json.dumps(extra, indent=2, ensure_ascii=False), "json"))
        return parts
    try:
        inp_s = json.dumps(ri, indent=2, ensure_ascii=False)
    except Exception:
        inp_s = str(ri)
    parts.append(_syntax(inp_s, "json" if _looks_json(inp_s) else "text"))
    return parts


def _render_image_result(out: str) -> list:
    """Path + message only (no pixel render in the TUI)."""
    path = image_result_path(out)
    message = image_result_message(out)
    parts: list = [Rule(t("tool-output-rule", n=len(out or "")), style="bright_black")]
    if path:
        parts.append(Text(t("tool-image-path", path=path), style="cyan"))
    if message:
        parts.append(Text(message))
    if not path and not message:
        parts.append(Text(out or t("tool-empty-output"), style="dim italic"))
    return parts


def _prefer_syntax_output(tname: str, lexer: str, body: str, *, console_like: bool) -> bool:
    """Whether tool output should use Rich Syntax (code) vs plain Text.

    Console streams stay plain (sanitize + speed). Source / structured bodies
    use Syntax so ``tool_call_update`` file dumps read as code, not prose.
    Display bodies are already mid-truncated — do not drop Syntax solely for
    length after that cap (the old 12k gate forced plain Text on most reads).
    """
    if console_like:
        return False
    if not (body or "").strip():
        return False
    if lexer and lexer != "text":
        return True
    if _is_file_body_tool(tname):
        return True
    return _looks_like_source_code(body)


def _output_lexer(out_disp: str, tname: str, path_hint: str, *, console_like: bool) -> str:
    """Pick a Pygments lexer for tool *output* (not the shell command input)."""
    if console_like:
        # Terminal streams: no fake bash highlight on mixed stdout/stderr.
        return "text"
    path_lang = _lang_from_path(path_hint) if path_hint else ""
    if path_lang:
        # read_file / edits: path wins (python file → python, not markdown guess).
        return path_lang
    if _looks_json(out_disp):
        return "json"
    if _looks_diff(out_disp):
        return "diff"
    guessed = _guess_source_lexer(out_disp)
    if guessed:
        return guessed
    if _is_file_body_tool(tname) or _looks_like_source_code(out_disp):
        return "text"
    return _guess_lexer(out_disp, tname, path_hint) or "text"


def _render_tool_output(out: str, tname: str, path_hint: str, *, truncate: bool = True) -> list:
    """Syntax-highlighted tool output (trace_viewer output block)."""
    if tname in ("image_gen", "image_edit") and (
        image_result_path(out) or image_result_message(out)
    ):
        return _render_image_result(out)
    parts: list = []
    source = display_tool_output(out or "", tool_name=tname)
    raw_len = len(out or "")
    cleaned = sanitize_console_text(source)
    if not cleaned and source:
        cleaned = sanitize_console_text(source, for_display=False) or t("tool-binary-output")
    n_out = len(cleaned)
    out_disp = _truncate_mid(cleaned, truncate=truncate)
    if raw_len and n_out < raw_len * 0.9:
        out_label = t("tool-output-rule-cleaned", n=n_out, raw=raw_len)
    else:
        out_label = t("tool-output-rule", n=n_out)
    parts.append(Rule(out_label, style="bright_black"))
    if not out_disp.strip():
        parts.append(Text(t("tool-empty-output"), style="dim italic"))
        return parts
    # Shell tools: stdout/stderr is a console stream. File tools never are.
    console_like = (not _is_file_body_tool(tname)) and (
        _looks_like_console_output(out or "", tname) or _is_shell_tool(tname)
    )
    lexer = _output_lexer(out_disp, tname, path_hint, console_like=console_like)
    if lexer == "json" or _looks_json(out_disp):
        with suppress(Exception):
            out_disp = json.dumps(json.loads(out_disp), indent=2, ensure_ascii=False)
            lexer = "json"
    if not _prefer_syntax_output(tname, lexer or "text", out_disp, console_like=console_like):
        # Console streams still use Syntax("text") so tails share tool code chrome.
        parts.append(_syntax(out_disp, "text"))
        return parts
    # Unknown language still uses Syntax("text") for monospaced code chrome.
    use_lexer = lexer or "text"
    # Line numbers double Pygments work; skip on large dumps (still Syntax).
    ln = (
        use_lexer
        in (
            "python",
            "javascript",
            "typescript",
            "rust",
            "go",
            "tsx",
            "jsx",
            "bash",
            "c",
            "cpp",
            "java",
        )
        and out_disp.count("\n") > 3
        and len(out_disp) < 6000
    )
    parts.append(_syntax(out_disp, use_lexer, line_numbers=ln))
    return parts


def render_tool_detail(
    *,
    index: int,
    tool_name: str,
    raw_input: dict | None = None,
    output: str = "",
    is_error: bool = False,
    tool_call_id: str = "",
    exit_code: int | None = None,
    signal: str = "",
    time_str: str = "",
    update_index: int | None = None,
    event_type: str = "tool",
    duration: float | None = None,
    truncate: bool = True,
    turn_index: int | None = None,
) -> Group:
    """Unified tool detail (trace_viewer render_tool_detail), call+result merged.

    :param truncate: When True (display), mid-cap huge tool bodies. When False
        (clipboard yank), keep full input/output text.
    :param turn_index: Sequential operator turn id (0-based) for orientation.
    """
    _ = (tool_call_id, update_index)
    ri = raw_input or {}
    path_hint = _path_hint(ri)
    tname = tool_name or "?"
    style = tool_style(tname)
    # Heading uses explicit separators (Fluent strips message edge whitespace).
    head = Text()
    head.append(f"#{index} ", style="dim")
    head.append("tool ", style="dim")
    head.append(format_tool_display(tname), style=style if not is_error else "bold red")
    if is_error:
        head.append(" ✗ ERROR", style="bold red")
    # Avoid redundant "(tool_call)" / "(tool_result)" after the tool name.
    if event_type and event_type not in ("tool", *et.TOOL_TYPES, ""):
        head.append(f"  ({event_type})", style="dim")
    head.append("\n")
    meta = Text()
    meta_bits: list[str] = []
    if turn_index is not None:
        meta_bits.append(t("ui-turn-number", turn=int(turn_index)))
    if time_str:
        meta_bits.append(time_str)
    if duration is not None:
        meta_bits.append(fmt_duration(duration))
    if exit_code is not None:
        meta_bits.append(f"exit {exit_code}")
    if signal:
        meta_bits.append(f"signal {signal}")
    if meta_bits:
        meta.append("  ·  ".join(meta_bits), style="dim" if not is_error else "red")
        meta.append("\n")
    if path_hint:
        meta.append(path_hint, style="cyan")
        meta.append("\n")
    parts: list = [head, meta]
    if ri:
        parts += [Text(""), Rule(t("ui-input"), style="bright_black")]
        parts.extend(_render_tool_input(tname, ri, truncate=truncate))
    else:
        parts += [
            Text(""),
            Rule(t("ui-input"), style="bright_black"),
            Text(t("tool-no-input"), style="dim italic"),
        ]
    parts.append(Text(""))
    parts.extend(_render_tool_output(output, tname, path_hint, truncate=truncate))
    return Group(*parts)


def render_tool_detail_from_event(
    ev: TraceEvent,
    *,
    paired_call: TraceEvent | None = None,
    paired_result: TraceEvent | None = None,
    duration: float | None = None,
    truncate: bool = True,
    turn_index: int | None = None,
) -> Group:
    """Render tool_call / tool_result, merging pair when available (trace_viewer style).

    Host ``read_file`` leaves the call body empty — the file dump is on the
    paired ``tool_call_update``. Prefer *paired_result* content, then the
    selected event's own content.
    """
    call = ev if ev.event_type == "tool_call" else paired_call
    result = ev if ev.event_type in et.TOOL_UPDATE_TYPES else paired_result
    ri: dict[str, JsonValue] = {}
    src_ev = call if call is not None else ev
    bag = (
        src_ev.raw_input
        if isinstance(src_ev.raw_input, ToolInputBag)
        else ToolInputBag(src_ev.raw_input if isinstance(src_ev.raw_input, dict) else {})
    )
    ri = dict(bag.raw())
    # Path may only appear on the call; still surface it when viewing the update.
    if not _path_hint(ri) and result is not None:
        rbag = (
            result.raw_input
            if isinstance(result.raw_input, ToolInputBag)
            else ToolInputBag(result.raw_input if isinstance(result.raw_input, dict) else {})
        )
        for k, v in rbag.raw().items():
            if k not in ri or ri.get(k) in (None, "", [], {}):
                ri[k] = v
    tname = (
        (call.tool_name if call else "")
        or (result.tool_name if result else "")
        or ev.tool_name
        or "?"
    )
    out = ""
    if result is not None:
        out = _content_str(result.content, sanitize=True, tool_name=tname)
    if not (out or "").strip():
        out = _content_str(ev.content, sanitize=True, tool_name=tname)
    if not (out or "").strip() and call is not None and call is not ev:
        out = _content_str(call.content, sanitize=True, tool_name=tname)
    is_err = bool(
        (result is not None and result.is_error)
        or ev.is_error
        or (call is not None and call.is_error)
    )
    exit_code = None
    signal = ""
    for src in (result, call, ev):
        if src is None:
            continue
        ec = getattr(src, "exit_code", None)
        sig = getattr(src, "signal", None) or ""
        if exit_code is None and ec is not None:
            exit_code = ec
        if not signal and sig:
            signal = sig
        ri_src = getattr(src, "raw_input", None) or {}
        if exit_code is None and isinstance(ri_src, dict) and ("exit_code" in ri_src):
            try:
                exit_code = int(ri_src["exit_code"])
            except (TypeError, ValueError):
                pass
    idx = ev.index
    time_str = ev.time_str
    update_index = ev.update_index
    call_id = (
        ev.tool_call_id
        or (call.tool_call_id if call else "")
        or (result.tool_call_id if result else "")
    )
    return render_tool_detail(
        index=idx,
        tool_name=tname,
        raw_input=ri,
        output=out,
        is_error=is_err,
        tool_call_id=call_id,
        exit_code=exit_code,
        signal=signal,
        time_str=time_str,
        update_index=update_index,
        event_type=ev.event_type,
        duration=duration,
        truncate=truncate,
        turn_index=turn_index,
    )


def _prose_or_code(text: str) -> Text | Markdown | Syntax:
    """Markdown, source Syntax, or plain text — same cues as tool/message bodies."""
    body = text or ""
    if looks_like_markdown(body):
        return Markdown(body)
    if _looks_like_source_code(body) or _looks_json(body):
        lexer = "json" if _looks_json(body) else (_guess_source_lexer(body) or "text")
        return _syntax(body, lexer)
    return Text(body)


def _line(text: str = "", *, style: str | None = None) -> Text:
    """One selectable body line (Group children are concatenated without newlines)."""
    body = text if text.endswith("\n") else f"{text}\n"
    return Text(body, style=style) if style else Text(body)


def _section(label: str) -> Text:
    """Bold inspect label on its own line."""
    return Text(f"{label}\n", style="bold")


def _append_block(parts: list, block: Text | Markdown | Syntax) -> None:
    """Append a body block and ensure a trailing newline for selectable layout."""
    if isinstance(block, Text):
        if block.plain and not block.plain.endswith("\n"):
            block.append("\n")
        parts.append(block)
        return
    parts.append(block)
    parts.append(Text("\n"))


def _task_detail_parts(
    ev: TraceEvent,
    *,
    mate: TraceEvent | None = None,
    schedule: ScheduleTask | None = None,
) -> list:
    """Status, command (bash Syntax), log tail (code chrome) — not a dump."""
    command = ""
    cwd = ""
    output_path = ""
    desc = ""
    prompt = ""
    human = ""
    last_fire = schedule.last_fired_at if schedule is not None else ""
    last_child = schedule.last_subagent_id if schedule is not None else ""
    if isinstance(ev.raw_input, ToolInputBag):
        command = ev.raw_input.as_str("command") or ev.raw_input.as_str("display_command")
        cwd = ev.raw_input.as_str("cwd")
        output_path = ev.raw_input.as_str("output_file")
        desc = ev.raw_input.as_str("description") or ev.raw_input.as_str("monitor_description")
        prompt = ev.raw_input.as_str("prompt")
        human = ev.raw_input.as_str("human_schedule")
    dump = task_fields_from_content(ev.content or "")
    command = command or dump.get("command", "")
    cwd = cwd or dump.get("cwd", "")
    output_path = output_path or dump.get("output_file", "")
    parts: list = [Text("\n")]
    asked = prompt.strip() or desc.strip() or command.strip()
    if asked:
        parts.append(_section(t("ui-inspect-asked")))
        if prompt.strip():
            _append_block(parts, _prose_or_code(prompt))
        elif desc.strip() and desc.strip() != command.strip():
            _append_block(parts, _prose_or_code(desc))
        if command.strip() and command.strip() != asked:
            _append_block(parts, _syntax(command, "bash"))
        elif command.strip() and not prompt.strip() and not desc.strip():
            _append_block(parts, _syntax(command, "bash"))
    status = job_status_for_event(ev, mate=mate)
    happen_bits: list[str] = []
    if ev.event_type in {"task_backgrounded", "task_completed"}:
        happen_bits.append(_subagent_status_word(status))
    if human.strip():
        happen_bits.append(human.strip())
    finish = ev if ev.event_type == "task_completed" else mate
    exit_s = ""
    if finish is not None and isinstance(finish.raw_input, ToolInputBag):
        code = finish.raw_input.raw().get("exit_code")
        if isinstance(code, int):
            exit_s = f"exit {code}"
    if last_fire.strip():
        happen_bits.append(last_fire.strip())
    if last_child.strip():
        happen_bits.append(last_child.strip())
    if happen_bits:
        parts.append(_section(t("ui-inspect-happened")))
        parts.append(_line("  ·  ".join(happen_bits)))
    if exit_s:
        parts.append(_line(exit_s, style="dim"))
    if cwd.strip():
        parts.append(_line(cwd, style="dim"))
    failed = (status or "").strip().lower() in {"failed", "error", "cancelled", "interrupted"}
    if output_path:
        tail = read_log_tail(Path(output_path), max_chars=2_000)
        lines = tail.splitlines()
        if len(lines) > 24:
            tail = "\n".join(lines[-24:])
        tail = sanitize_console_text(tail)
        if failed and tail.strip():
            parts.append(Text("\n"))
            parts.append(_section(t("ui-inspect-failed")))
            last = tail.splitlines()[-1] if tail.splitlines() else tail
            parts.append(_line(last, style=f"bold {FAILED}"))
        parts.append(Text("\n"))
        parts.append(Rule(t("col-log"), style="bright_black"))
        if tail.strip():
            _append_block(parts, _syntax(tail, "text"))
        else:
            parts.append(_line(output_path, style="dim"))
    elif failed:
        parts.append(_section(t("ui-inspect-failed")))
        parts.append(_line(_subagent_status_word(status), style=f"bold {FAILED}"))
    if len(parts) == 1:
        bag = ev.raw_input.raw() if isinstance(ev.raw_input, ToolInputBag) else {}
        preview = job_list_preview(ev.event_type, bag, ev.content)
        parts.append(_line(preview) if preview else _line("(empty)", style="dim"))
    return parts


def render_workflow_detail(run: WorkflowRun | None, *, ev: TraceEvent | None = None) -> Group:
    """Merged workflow inspect: status, phase, fail text, children — not the script.

    Each line ends with ``\\n`` so :func:`~groket.ui.selectable_static.to_display_content`
    keeps fields on separate rows (Group children are concatenated).
    """
    head = Text()
    if ev is not None:
        head.append(f"#{ev.index} ", style="dim")
    name = (
        run.name
        if run is not None
        else (workflow_name_from_raw(ev.raw_input) if ev is not None else "")
    )
    head.append(name or t("ui-workflow"), style=f"bold {RUNNING}")
    head.append("\n")
    if run is None:
        head.append(t("ui-workflow-missing"), style="dim")
        head.append("\n")
        return Group(head)

    parts: list = [head]
    if run.objective.strip():
        parts.append(_section(t("ui-inspect-asked")))
        obj = _prose_or_code(run.objective)
        if isinstance(obj, Text) and not obj.plain.endswith("\n"):
            obj.append("\n")
        parts.append(obj)
        parts.append(Text("\n"))
    happened = _subagent_status_word(run.status)
    if run.phase:
        happened = f"{happened}  ·  {run.phase}"
    if run.elapsed_ms is not None and run.elapsed_ms > 0:
        happened = f"{happened}  ·  {fmt_duration(run.elapsed_ms / 1000.0)}"
    parts.append(_section(t("ui-inspect-happened")))
    st_style = {
        "complete": COMPLETE,
        "running": RUNNING,
        "failed": f"bold {FAILED}",
        "cancelled": "dim",
        "interrupted": "dim",
    }.get(run.status, "")
    parts.append(_line(happened, style=st_style or None))
    if run.agents_used is not None or run.agent_budget is not None:
        used = "—" if run.agents_used is None else str(run.agents_used)
        budget = "—" if run.agent_budget is None else str(run.agent_budget)
        parts.append(
            Text(
                t("ui-workflow-agent-count", used=used, budget=budget) + "\n",
                style="dim",
            )
        )
    if run.pause_message.strip():
        parts.append(Text("\n"))
        parts.append(_section(t("ui-inspect-failed")))
        pause_style = f"bold {FAILED}" if run.status == "failed" else "dim"
        parts.append(Text(run.pause_message.rstrip() + "\n", style=pause_style))
    if run.children:
        parts.append(Text("\n"))
        parts.append(_section(t("ui-agents")))
        kids = Text()
        for child in run.children:
            if child.success:
                kids.append("ok", style=COMPLETE)
            else:
                kids.append("fail", style=f"bold {FAILED}")
            kids.append(f"  {child.label}\n")
        parts.append(kids)
    return Group(*parts)


def _subagent_status_word(status: str) -> str:
    key = {
        "running": "ui-status-running",
        "completed": "status-complete",
        "complete": "status-complete",
        "done": "status-complete",
        "cancelled": "status-cancelled",
        "failed": "ui-status-failed",
        "interrupted": "ui-status-interrupted",
    }.get(status, "")
    return t(key) if key else status


def _subagent_detail_parts(
    ev: TraceEvent, *, run: SubagentRun | None = None, mate: TraceEvent | None = None
) -> list:
    """Type, what it was asked to do, outcome. Duration stays on the heading."""
    info: SubagentInspect = subagent_inspect(ev, mate=mate, run=run)
    parts: list = [Text("\n")]
    asked = info.description
    if (
        not asked
        and (ev.content or "").strip()
        and not (ev.content or "").lower().startswith(("subagent finished", "spawned "))
    ):
        asked = ev.content
    if asked:
        parts.append(_section(t("ui-inspect-asked")))
        _append_block(parts, _prose_or_code(asked))
    happen_bits: list[str] = []
    if info.kind:
        happen_bits.append(info.kind)
    if ev.event_type == "subagent_finished" and info.status:
        happen_bits.append(_subagent_status_word(info.status))
    if happen_bits:
        parts.append(_section(t("ui-inspect-happened")))
        parts.append(_line("  ·  ".join(happen_bits)))
    if ev.event_type == "subagent_finished" and (info.status or "").lower() in {
        "failed",
        "error",
        "cancelled",
    }:
        parts.append(_section(t("ui-inspect-failed")))
        parts.append(_line(_subagent_status_word(info.status), style=f"bold {FAILED}"))
    if len(parts) == 1:
        parts.append(_line("(empty)", style="dim"))
    return parts


def render_event_detail(
    ev: TraceEvent,
    *,
    finding: Finding | None = None,
    flag: Flag | None = None,
    duration: float | None = None,
    paired_call: TraceEvent | None = None,
    paired_result: TraceEvent | None = None,
    truncate: bool = True,
    turn_index: int | None = None,
    subagent_run: SubagentRun | None = None,
    job_mate: TraceEvent | None = None,
    schedule: ScheduleTask | None = None,
    workflow: WorkflowRun | None = None,
) -> RenderableType:
    """Full detail pane for any TraceEvent (trace_viewer render_event_detail + banners).

    :param truncate: Display caps for huge bodies (default). Pass False for
        clipboard yank so the operator gets the full event text.
    :param turn_index: Sequential operator turn id (0-based) for orientation.
    """
    banners: list = []
    if flag:
        ft = Text()
        ft.append(t("ui-flagged"), style="red bold")
        ft.append(f"[{flag.verdict.value}] {flag.description}\n", style="red")
        ft.append(t("flagged-at-when", when=flag.created_at), style="dim")
        banners.append(ft)
    if finding:
        sc = severity_style(finding.severity.value)
        it = Text()
        it.append(t("ui-finding"), style=f"{sc} bold")
        it.append(f"  [{finding.plugin_id}] {finding.category}: {finding.title}\n", style=sc)
        detail = finding.detail or ""
        if truncate and len(detail) > 400:
            detail = detail[:400]
        it.append(f"  {detail}", style="dim")
        banners.append(it)
    if ev.event_type in et.TOOL_TYPES:
        if ev.tool_name == "workflow":
            core = render_workflow_detail(workflow, ev=ev)
        else:
            core = render_tool_detail_from_event(
                ev,
                paired_call=paired_call,
                paired_result=paired_result,
                duration=duration,
                truncate=truncate,
                turn_index=turn_index,
            )
        if banners:
            return Group(*banners, Text(""), core)
        return core
    from ..session.tagged_blocks import unwrap_for_display
    from ..session.turns import harness_user_chrome_heading

    chrome_heading = harness_user_chrome_heading(ev.content or "")
    style = KIND_STYLES.get(ev.event_type, "white")
    if chrome_heading is not None:
        style = "bold magenta"
    if ev.is_error and ev.event_type in et.SESSION_CHROME_TYPES:
        style = "bold red"
    job_title = et.job_event_label(ev.event_type, kind=event_job_kind(ev))
    head = Text()
    head.append(f"#{ev.index} ", style="dim")
    head.append(
        chrome_heading
        if chrome_heading is not None
        else (job_title or ev.type_label or ev.event_type),
        style=style,
    )
    if ev.is_error:
        head.append(t("ui-error-1"), style="bold red")
    head.append("\n")
    meta_parts: list[str] = []
    if turn_index is not None:
        meta_parts.append(t("ui-turn-number", turn=int(turn_index)))
    if ev.time_str:
        meta_parts.append(ev.time_str)
    info = subagent_inspect(ev, run=subagent_run) if ev.event_type in et.SUBAGENT_TYPES else None
    own_dur = info.duration_s if info is not None else subagent_duration_seconds(ev)
    if ev.event_type in et.TASK_TYPES or ev.event_type.startswith("scheduled_task_"):
        shown_dur = job_duration_seconds(ev, mate=job_mate)
    else:
        shown_dur = own_dur if own_dur is not None else duration
    if shown_dur is not None:
        meta_parts.append(fmt_duration(shown_dur))
    if meta_parts:
        head.append("  ·  ".join(meta_parts), style="dim")
        head.append("\n")
    body = _content_str(
        unwrap_for_display(ev.content or ""),
        sanitize=True,
        tool_name=ev.tool_name or "",
    )
    if truncate and len(body) > 20000:
        body = body[:10000] + t("truncate-marker") + body[-8000:]
    chunks: list = []
    if banners:
        chunks.extend(banners)
        chunks.append(Text(""))
    chunks.append(head)
    if ev.event_type in et.THOUGHT_TYPES and body.strip():
        chunks += [
            Text(""),
            Rule(t("ui-thought"), style="bright_black"),
            Text(body, style="dim italic"),
        ]
    elif ev.event_type in et.MESSAGE_TYPES and body.strip():
        # Soft newlines → Markdown hard breaks so each prompt line stays its own
        # visual line (selectable for partial copy). Blank lines stay paragraphs.
        md_body = "  \n".join(body.split("\n"))
        chunks += [Text(""), Markdown(md_body)]
    elif ev.event_type == "plan":
        chunks += [Text(""), Rule(t("ui-plan"), style="bright_black"), _prose_or_code(body)]
    elif ev.event_type in et.TASK_TYPES or ev.event_type.startswith("scheduled_task_"):
        chunks.extend(_task_detail_parts(ev, mate=job_mate, schedule=schedule))
    elif ev.event_type in et.SUBAGENT_TYPES:
        chunks.extend(_subagent_detail_parts(ev, run=subagent_run))
    elif ev.event_type == "subagent" and body.strip():
        chunks += [
            Text(""),
            Rule(t("ui-subagent"), style="bright_black"),
            _prose_or_code(body),
        ]
    elif ev.event_type in et.SESSION_CHROME_TYPES:
        chunks += [
            Text(""),
            Text(body or "(empty)", style="bold red" if ev.is_error else "yellow"),
        ]
    elif body.strip():
        chunks += [Text(""), _prose_or_code(body)]
    else:
        chunks += [Text(""), Text("(empty)", style="dim")]
    return Group(*chunks)


def render_markdown_doc(
    text: str, *, max_chars: int = 120000, truncate: bool = True
) -> RenderableType:
    """Markdown document for Summary / Feedback tabs.

    :param truncate: Cap huge docs for display. False keeps full text (yank).
    """
    body = text or "_empty_"
    if truncate and len(body) > max_chars:
        body = body[: max_chars // 2] + t("truncate-for-display") + body[-(max_chars // 3) :]
    try:
        return Markdown(body)
    except Exception:
        return Text(body)
