"""Canonical keyboard-shortcut labels for footer, tips, HUD, and docs.

Same shape as VS Code / Chrome / GitHub: ``Ctrl+S``, ``Cmd+Shift+G``,
``Ctrl+Enter / Ctrl+J``. No unicode glyphs (⌘⇧⌥) and no caret (``^s``).
Binding strings stay Textual/wire form (``ctrl+s``); this module is display only.
"""

from __future__ import annotations

_MOD_CANON: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "opt": "alt",
    "shift": "shift",
    "cmd": "cmd",
    "command": "cmd",
    "super": "super",
    "meta": "cmd",
    "win": "super",
    "windows": "super",
}

_MOD_LABEL: dict[str, str] = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "cmd": "Cmd",
    "super": "Super",
}

_MOD_ORDER: tuple[str, ...] = ("ctrl", "alt", "cmd", "super", "shift")

_NAMED_KEYS: dict[str, str] = {
    "enter": "Enter",
    "return": "Enter",
    "escape": "Esc",
    "esc": "Esc",
    "space": "Space",
    "tab": "Tab",
    "delete": "Delete",
    "backspace": "Backspace",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "slash": "/",
    "question_mark": "?",
    "left_square_bracket": "[",
    "right_square_bracket": "]",
    "full_stop": ".",
    "comma": ",",
    "minus": "-",
    "equals": "=",
    "semicolon": ";",
}


def format_key_chord(raw: str) -> str:
    """Turn a Textual key string (or comma alternatives) into a display chord.

    :param raw: e.g. ``ctrl+s``, ``ctrl+enter,ctrl+j``, ``slash``.
    :returns: ``Ctrl+S``, ``Ctrl+Enter / Ctrl+J``, ``/``; ``?`` when empty.
    """
    text = (raw or "").strip()
    if not text:
        return "?"
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        return "?"
    return " / ".join(_format_one(p) for p in parts)


def _format_one(token: str) -> str:
    bits = [b.strip() for b in token.replace("-", "+").split("+") if b.strip()]
    mods: list[str] = []
    key = ""
    for bit in bits:
        low = bit.casefold()
        canon = _MOD_CANON.get(low)
        if canon is not None:
            if canon not in mods:
                mods.append(canon)
            continue
        key = bit
    ordered = [_MOD_LABEL[m] for m in _MOD_ORDER if m in mods]
    key_label = _key_label(key, with_mods=bool(ordered))
    if not ordered:
        return key_label
    if not key_label:
        return "+".join(ordered)
    return "+".join([*ordered, key_label])


def _key_label(key: str, *, with_mods: bool) -> str:
    if not key:
        return ""
    low = key.casefold().replace(" ", "")
    if low in _NAMED_KEYS:
        return _NAMED_KEYS[low]
    if len(low) >= 2 and low[0] == "f" and low[1:].isdigit():
        return f"F{low[1:]}"
    if len(key) == 1 and key.isalpha():
        return key.upper() if with_mods else key
    return key
