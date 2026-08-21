# groket-hud

Summonable session palette for groket. The idle list is **Recent**
(latest eight). Scroll down or press `j` at the bottom for the next
eight. Type `/` to search the whole catalog. Open a session for Overview,
Turns, Timeline, Diff, and Notes. Follow-up and Done when a session
is awaiting. Notes use the same schema as the [terminal
app](../README.md#terminal-app). The palette does not launch evals,
recipes, or Docker.

It attaches to [`groket serve`](../docs/control.md) — same socket as the
[terminal app](../README.md#terminal-app), [Emacs](../README.md#emacs),
and [Neovim](../README.md#neovim-09). See [Desktop
HUD](../README.md#desktop-hud) in the main README.

## Run

```bash
groket serve -d
groket hud
```

`groket hud` detaches and starts serve when the socket is free. One
process, one tray tile: a second `groket hud` shows the palette. It runs
the `groket-hud` on `PATH` from `uv tool install` (or `GROKET_HUD_BIN`).
`--rebuild` cargo-builds this checkout, then launches that binary.
`--restart` replaces a running palette (including `--dev --restart`).
`--dev` / `--debug` keep a debug binary. `--foreground` attaches to this
terminal. `groket --version` and `groket-hud --version` (`-V`) print the
product version.

```bash
groket hud --toggle    # show or hide
groket hud --show
groket hud --hide
```

`--install-desktop` writes user-local icons and a launcher named
**groket** (Linux `.desktop`, macOS `~/Applications/groket.app`,
Windows Start Menu). Re-run after moving the binary.

## Hotkey

Default **Cmd+Shift+G** (macOS) / **Ctrl+Shift+G** (Windows and X11
Linux). Override with `hud.global_shortcut` in `~/.groket/config.toml`
or `GROKET_HUD_SHORTCUT`. On Wayland bind `groket hud --toggle` (the
compositor sends an activation token so you can type). Tray **Show**
and a terminal `--toggle` do not steal the keyboard. Sway
places the overlay; focus is the token.

While the overlay is on screen, a live poll re-reads overview about
every **3 seconds** (idle sessions slower). An unfocused pop-out or
hidden overlay does not poll; control notifies still refresh the
catalog and fire desktop notifications. Press **?** for the shortcut
cheatsheet. Shared keys match the terminal app (`?` `Esc` `/` `y` `j`/`k`
`h`/`l` for Timeline turns and Diff snapshots, `n`/`e` `N`); panes are Tab and Ctrl+1–5 except on Notes, where Tab walks the note fields. `u` or the logo leaves an
open session for the session list (`Esc` still hides, or steps out of
Timeline detail / a child first). A `keys.toml` remap applies on both
surfaces. A configured leader (Colemak example: `;`) then one letter
runs `leader+X`; Esc or timeout cancels. Copy
`examples/keys/colemak.toml` to `~/.groket/keys.toml`. Subagent runs
stay off the session list; open them from the parent Turns chips or by
clicking a Timeline spawn/finish tile (Enter does the same). Esc
returns to that Timeline or Turns place.

## Overlay, pop-out, tray, notify

Launch is a centered, always-on-top overlay. The pop-out icon in the
search bar opens a decorated desktop window. Close that window to leave
the HUD running; the hotkey or tray **Show** brings the overlay
back. **Esc** hides the overlay.

A tray icon appears when the host has one (Linux StatusNotifier, macOS
menu bar, Windows notification area). Left-click toggles the overlay
without taking keyboard focus. **Quit groket** exits the palette
only; serve stays up.

Desktop notifications fire for eval sessions groket launched (awaiting,
complete, cancelled, failed). Host Grok chats already notify on their
own; groket does not repeat those.
Linux uses the 64px
tray tile; macOS and Windows use the square app icon
(`~/.groket/hud-notify.png`). Disable with `GROKET_HUD_NOTIFY=0` or
`hud.desktop_notifications: false`.

## Env

| Variable | Role |
|----------|------|
| `GROKET_CONTROL_SOCKET` | Control Unix socket path |
| `GROKET_HUD_BIN` | Use this binary instead of building |
| `GROKET_HUD_SHORTCUT` | Override global summon chord |
| `GROKET_HUD_FOREGROUND` | Attach the HUD to this terminal |
| `GROKET_HUD_DEV` | Same as `--dev` |
| `GROKET_HUD_DEBUG` | Same as `--debug` |
| `GROKET_HUD_LOG` | Error log (default `~/.groket/hud.log`) |
| `GROKET_HUD_SHOW_ON_START` | Show the palette when the process starts |
| `GROKET_HUD_NOTIFY` | `0` disables desktop notifications |
| `GROKET_HUD_SUMMON_SOCKET` | Override the show/hide summon socket |

## Develop

```bash
uv run groket hud             # PATH / GROKET_HUD_BIN, else checkout release
uv run groket hud --restart
uv run groket hud --rebuild   # cargo release of this tree
just hud-check                # from the repo root
```

Linux build packages: `libxkbcommon-dev`, plus Wayland or X11 for your
session.
