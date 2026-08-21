"""Product README is a start page that tracks shipped help and control docs."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
HUD_README = ROOT / "desktop" / "README.md"
HELP = ROOT / "groket" / "locale" / "en" / "help.rich.txt"
CONTROL = ROOT / "docs" / "control.md"
AGENTS = ROOT / "AGENTS.md"

_TOOLKIT = re.compile(r"Textual|icedtea|\biced\b")
_VOICE = re.compile(r"first path|operator first path|operator surface|operator protocol")
_METHODS = re.compile(r"session/list|session/follow_up|session/done")


def _help_rows() -> list[tuple[str, str]]:
    section = ""
    rows: list[tuple[str, str]] = []
    for line in HELP.read_text(encoding="utf-8").splitlines():
        if line.startswith("[bold]") and "[/bold]" in line:
            section = line.removeprefix("[bold]").split("[/bold]", 1)[0].strip()
            continue
        if not line.startswith("  ") or section == "":
            continue
        matched = re.match(r"^  (.+?)\s{2,}([A-Za-z].*)$", line)
        if matched is None:
            continue
        rows.append((matched.group(1).strip(), matched.group(2).strip()))
    return rows


def test_readme_opens_with_what_groket_does() -> None:
    text = README.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:20])
    assert "evaluates" in head
    assert "Grok Build" in head
    assert "it is not the Grok" not in head.lower()


def test_readme_has_four_client_headings() -> None:
    text = README.read_text(encoding="utf-8")
    for heading in (
        "## Terminal app",
        "## Desktop HUD",
        "## Emacs",
        "## Neovim (0.9+)",
        "## Control",
    ):
        assert heading in text, heading


def test_help_rich_actions_appear_in_readme_key_table() -> None:
    readme = README.read_text(encoding="utf-8")
    rows = _help_rows()
    assert rows, "help.rich.txt has no key rows"
    missing = [f"{key} {action}" for key, action in rows if action not in readme]
    assert missing == [], missing
    assert "| s / Space |" in readme
    assert "Open the share link when the session has one" in readme


def test_readmes_have_no_toolkit_or_process_labels() -> None:
    for path in (README, HUD_README):
        text = path.read_text(encoding="utf-8")
        assert _TOOLKIT.search(text) is None, path.name
        assert _VOICE.search(text) is None, path.name
    assert _VOICE.search(CONTROL.read_text(encoding="utf-8")) is None


def test_method_inventory_lives_only_in_control_doc() -> None:
    control = CONTROL.read_text(encoding="utf-8")
    for method in ("session/list", "session/follow_up", "session/done"):
        assert method in control, method
    assert "session/selected" in control
    assert "notes/changed" in control
    assert "session/changed" in control
    assert "analysis/run" not in control
    assert "analysis/changed" not in control
    assert "Content-Length" in control
    assert "GROKET_CONTROL_SOCKET" in control
    for path in (README, AGENTS):
        assert _METHODS.search(path.read_text(encoding="utf-8")) is None, path.name
    assert "docs/control.md" in AGENTS.read_text(encoding="utf-8")


def test_hud_readme_states_three_second_poll_and_links_clients() -> None:
    text = HUD_README.read_text(encoding="utf-8")
    assert "3 seconds" in text
    assert "../README.md#desktop-hud" in text
    assert "../docs/control.md" in text
    assert "../README.md#emacs" in text
    assert "../README.md#neovim-09" in text
    assert "fail-under" not in text
    assert "app_id" not in text


def test_readme_has_no_html_heading_anchors() -> None:
    assert "<a id" not in README.read_text(encoding="utf-8")
    assert "<a id" not in HUD_README.read_text(encoding="utf-8")


def test_readme_mark_switches_with_github_color_scheme() -> None:
    text = README.read_text(encoding="utf-8")
    light = ROOT / "brand" / "png" / "groket-mark.png"
    dark = ROOT / "brand" / "png" / "groket-mark-reverse.png"
    assert light.is_file()
    assert dark.is_file()
    assert "groket-mark.png#gh-light-mode-only" in text
    assert "groket-mark-reverse.png#gh-dark-mode-only" in text
