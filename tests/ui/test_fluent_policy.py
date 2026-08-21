"""Fluent policy: composed messages exist; check_fluent hard rules pass."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from groket.ui.i18n import setup_i18n, t

ROOT = Path(__file__).resolve().parents[2]


def test_composed_tool_messages_format() -> None:
    setup_i18n("en")
    assert "grep" in t("tool-detail-heading", index=5, name="grep")
    assert "Output (12 chars)" == t("tool-output-rule", n=12)
    assert "cleaned from" in t("tool-output-rule-cleaned", n=3, raw=10)
    assert "context7" in t("tool-mcp-label", name="context7__x")


def test_notify_messages() -> None:
    setup_i18n("en")
    assert "/tmp" in t("notify-scanning", path="/tmp")
    assert "3" in t("notify-loaded-sessions", n=3)


def test_check_fluent_script_exits_zero() -> None:
    script = ROOT / "scripts" / "check_fluent.py"
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _check_fluent():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_fluent", ROOT / "scripts" / "check_fluent.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_fluent_flags_rich_tags_fed_to_text_append() -> None:
    """Rich tags in FTL become literal when Text.append(..., style=) is used."""
    cf = _check_fluent()
    marked = cf.marked_fluent_ids("x = [bold yellow]Stale[/] — {$d}\nplain = hello\n")
    assert marked == {"x"}
    bad = cf.check_markup_into_text(
        'head.append(t("x", d="e"), style="bold yellow")\n',
        marked,
        "groket/ui/screens/browser.py",
    )
    assert bad and "x" in bad[0]
    ok_static = cf.check_markup_into_text(
        'widget.update(t("x", d="e"))\n',
        marked,
        "groket/ui/screens/browser.py",
    )
    assert ok_static == []
    ok_list = cf.check_markup_into_text(
        'lines.append(t("x", d="e"))\n',
        marked,
        "groket/ui/screens/jobs.py",
    )
    assert ok_list == []
    ok_plain = cf.check_markup_into_text(
        'head.append(t("plain"), style="bold")\n',
        marked,
        "groket/ui/screens/browser.py",
    )
    assert ok_plain == []
