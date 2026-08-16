"""Classify session titles at draw time (uLogMe render_settings regex).

Raw titles stay on disk. Short labels are computed when painting a row.
"""

from __future__ import annotations

import re

# (pattern, label). First match wins. Keep the list short; unknown titles pass through.
_DEFAULT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"Google Chrome|Chromium", re.I), "Chrome"),
    (re.compile(r"Mozilla Firefox", re.I), "Firefox"),
    (re.compile(r"grokos-agent|\bgrokos\b", re.I), "seat"),
    (re.compile(r"\bgrok\b", re.I), "grok"),
)


def classify_title(raw: str, extra: list[tuple[re.Pattern[str], str]] | None = None) -> str:
    """Return a short draw label for *raw*, or the stripped title if no rule matches."""
    text = (raw or "").strip()
    if not text:
        return ""
    rules = tuple(extra or ()) + _DEFAULT_RULES
    for pat, label in rules:
        if pat.search(text):
            return label
    return text
