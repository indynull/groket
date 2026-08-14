"""Modals for run/persona env maps and inline skills."""

from __future__ import annotations

import re

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static, TextArea

from .bindings import FORM_SAVE
from .i18n import t
from .quit_actions import QuitActions
from .widgets.key_value_editor import KeyValueEditor

# Grok skill id: create-skill rules (a–z, 0–9, hyphens; 2–64; start/end alnum).
_SKILL_ID_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,62}[a-z0-9])$")


def sanitize_skill_id(raw: str) -> str:
    """Normalize user input toward a valid skill id (may still need validation)."""
    s = (raw or "").strip().lower().replace("_", "-")
    s = re.sub(r"[^a-z0-9-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:64]


def validate_skill_id(skill_id: str) -> bool:
    """Return True if *skill_id* matches the Grok skill name contract."""
    return bool(_SKILL_ID_RE.fullmatch(skill_id or ""))


def build_skill_md(*, skill_id: str, description: str, body: str) -> str:
    """Assemble canonical ``SKILL.md`` (YAML frontmatter + markdown body).

    Format matches create-skill / user-guide skills::

        ---
        name: <id>
        description: <when to use>
        ---

        <markdown instructions>
    """
    sid = (skill_id or "").strip()
    desc = (description or "").strip() or "Inline run skill"
    # Multi-line descriptions use YAML folded style for readability.
    if "\n" in desc:
        desc_block = ">\n  " + "\n  ".join(line for line in desc.splitlines())
        fm_desc = f"description: {desc_block}"
    elif any(c in desc for c in (":", "#", '"', "'")) or desc.startswith("{"):
        # Escape quotes in a single-line scalar when needed.
        safe = desc.replace("\\", "\\\\").replace('"', '\\"')
        fm_desc = f'description: "{safe}"'
    else:
        fm_desc = f"description: {desc}"
    text = (body or "").strip()
    if not text:
        text = f"# {sid}\n\nWrite instructions for the agent here."
    if not text.endswith("\n"):
        text += "\n"
    return f"---\nname: {sid}\n{fm_desc}\n---\n\n{text}"


def parse_skill_md(content: str) -> tuple[str, str, str]:
    """Split a SKILL.md into ``(name, description, body)`` when possible."""
    text = (content or "").strip()
    if not text.startswith("---"):
        return ("", "", text)
    parts = text.split("---", 2)
    if len(parts) < 3:
        return ("", "", text)
    fm, body = parts[1], parts[2].lstrip("\n")
    name = ""
    desc_lines: list[str] = []
    in_desc = False
    for line in fm.splitlines():
        if in_desc:
            if line.startswith("  ") or line.startswith("\t"):
                desc_lines.append(line.strip())
                continue
            in_desc = False
        if line.startswith("name:"):
            name = line[5:].strip().strip("\"'")
            continue
        if line.startswith("description:"):
            rest = line[12:].strip()
            if rest in (">", "|") or rest.startswith(">"):
                in_desc = True
                if rest.startswith(">") and len(rest) > 1:
                    desc_lines.append(rest.lstrip(">").strip())
            else:
                if (rest.startswith('"') and rest.endswith('"')) or (
                    rest.startswith("'") and rest.endswith("'")
                ):
                    rest = rest[1:-1]
                desc_lines.append(rest)
    return (name, "\n".join(desc_lines).strip(), body)


class EnvEditorModal(QuitActions, ModalScreen[dict[str, str] | None]):
    """Edit a string map as key/value rows (not KEY=value free text)."""

    BINDINGS = list(FORM_SAVE)

    def __init__(self, initial: dict[str, str] | None = None, *, title: str = "") -> None:
        super().__init__()
        self._initial = dict(initial or {})
        self._title = title or t("env-editor-title")

    def compose(self) -> ComposeResult:
        with Vertical(id="env-editor-modal"):
            with VerticalScroll(id="env-editor-body"):
                yield Label(self._title, id="env-editor-title")
                yield KeyValueEditor(self._initial, id="env-kv")
            with Horizontal(id="env-editor-footer", classes="modal-footer"):
                yield Button(t("ui-save"), variant="primary", id="env-save")
                yield Button(t("ui-cancel"), id="env-cancel")

    def action_save(self) -> None:
        self.dismiss(self.query_one("#env-kv", KeyValueEditor).get_values())

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "env-save":
            self.action_save()
        elif event.button.id == "env-cancel":
            self.action_cancel()


class InlineSkillModal(QuitActions, ModalScreen[tuple[str, str] | None]):
    """Author a one-off skill (id + description + instructions → SKILL.md)."""

    BINDINGS = list(FORM_SAVE)

    def __init__(self, *, name: str = "", body: str = "") -> None:
        super().__init__()
        parsed_name, parsed_desc, parsed_body = parse_skill_md(body) if body else ("", "", "")
        self._name = (name or parsed_name or "").strip()
        self._description = parsed_desc
        self._body = parsed_body if body else ""

    def compose(self) -> ComposeResult:
        with Vertical(id="inline-skill-modal"):
            with VerticalScroll(id="inline-skill-body-scroll"):
                yield Label(t("inline-skill-title"), id="inline-skill-title")
                yield Static(t("inline-skill-hint"), classes="pe-field-hint")
                yield Label(t("inline-skill-name-label"))
                yield Input(
                    value=self._name,
                    placeholder=t("inline-skill-name-placeholder"),
                    id="inline-skill-name",
                )
                yield Static(t("inline-skill-name-hint"), classes="pe-field-hint")
                yield Label(t("inline-skill-description-label"))
                yield Input(
                    value=self._description,
                    placeholder=t("inline-skill-description-placeholder"),
                    id="inline-skill-description",
                )
                yield Static(t("inline-skill-description-hint"), classes="pe-field-hint")
                yield Label(t("inline-skill-body-label"))
                yield TextArea(
                    self._body or "",
                    id="inline-skill-body",
                    language="markdown",
                    classes="pe-tall",
                )
                yield Static(t("inline-skill-body-hint"), classes="pe-field-hint")
            with Horizontal(id="inline-skill-footer", classes="modal-footer"):
                yield Button(t("ui-save"), variant="primary", id="inline-skill-save")
                yield Button(t("ui-cancel"), id="inline-skill-cancel")

    def action_save(self) -> None:
        raw_id = self.query_one("#inline-skill-name", Input).value
        skill_id = sanitize_skill_id(raw_id)
        if not skill_id:
            self.notify(t("inline-skill-name-required"), severity="warning")
            return
        if not validate_skill_id(skill_id):
            self.notify(t("inline-skill-name-invalid"), severity="warning")
            return
        description = self.query_one("#inline-skill-description", Input).value.strip()
        if not description:
            self.notify(t("inline-skill-description-required"), severity="warning")
            return
        body = self.query_one("#inline-skill-body", TextArea).text
        content = build_skill_md(skill_id=skill_id, description=description, body=body)
        self.dismiss((skill_id, content))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "inline-skill-save":
            self.action_save()
        elif event.button.id == "inline-skill-cancel":
            self.action_cancel()
