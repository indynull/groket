<p align="center">
  <img src="brand/png/groket-lockup-horizontal.png#gh-light-mode-only" alt="groket" width="520" />
  <img src="brand/png/groket-lockup-horizontal-reverse.png#gh-dark-mode-only" alt="groket" width="520" />
</p>

# groket

**groket** evaluates [Grok Build](https://docs.x.ai/build/overview)
sessions: timeline, findings, workspace diffs, Docker evals, personas, and
pluggable detectors / analysis plugins.

Four clients, all talking to [`groket serve`](#control):

| Client | What it does |
|--------|----------------|
| [Terminal app](#terminal-app) | Browse sessions, launch evals, analysis, export |
| [Desktop HUD](#desktop-hud) | Summonable session palette |
| [Emacs](#emacs) | Org buffer |
| [Neovim](#neovim-09) | Markdown buffer |

## Install

```bash
make install    # clone: .venv + `groket` on PATH (editable)
groket          # terminal app
```

```bash
uv tool install git+https://github.com/indynull/groket
groket
uv tool upgrade groket
```

## Paths

| Root | Default | Holds |
|------|---------|--------|
| Config home | `~/.groket` | `config.json`, personas, detectors, rules, plugins, prefs, optional `keys.toml` |
| Work root | `~/.groket/work` | `runs/traces/`, recipes, Docker contexts, batch results |

```bash
groket                      # default work root
groket /path/to/work        # work root, traces tree, or one session dir
```

Optional key diffs: `~/.groket/keys.toml` (`GROKET_KEYS` overrides the path).
A missing file keeps the catalog defaults. Esc, Enter, Tab, Shift+Tab, and
`?` are not remappable.

```bash
groket keys              # resolved table (scope, id, chord, surface)
groket keys --occupancy  # taken chords per scope
groket keys --check      # exit 1 on overlay errors
```

## Eval and Host

**Eval** sessions are Docker launches under `work/runs/traces`. **Host**
sessions are native Grok trees at `~/.grok/sessions` (real paths; `H`
shows or hides them). `groket -P ~/.grok/sessions` browses Host while
keeping the default work root for new runs. Notes on Host sessions write
under `~/.groket/notes/<session_id>/`.

## Terminal app

`groket` (or `groket tui`) is the full eval client: session list, browser
panes, runner, recipes, personas, analysis, and export. The footer lists
the keys that apply now; `?` is the full list.

| Key | Where | Action |
|-----|-------|--------|
| Tab | everywhere | Next control |
| Shift+Tab | everywhere | Previous control |
| Arrows | everywhere | Move in a list |
| j / k | everywhere | Move down / up in a list |
| Enter | everywhere | Open or activate |
| Esc | everywhere | Back or close |
| ? | everywhere | This panel |
| Ctrl+P | everywhere | Command palette for this screen |
| F5 | everywhere | Refresh (also Ctrl+R) |
| J | everywhere | Jobs and logs |
| q | everywhere | Quit when no field is focused |
| / | sessions | Search |
| r | sessions | New run |
| C | sessions | Recipes |
| P | sessions | Personas |
| d | sessions | Rules |
| a | sessions | Analyze the selection |
| s / Space | sessions | Select (also Space) |
| S | sessions | Select all |
| R | sessions | Re-run the highlighted recipe as a new session |
| f | sessions | Fork an ended session into a new multi-turn |
| Ctrl+S | sessions | Save the row as a recipe |
| E | sessions | Export a session bundle |
| H | sessions | Show native Host sessions beside Eval |
| n | sessions | Follow-up while awaiting |
| e | sessions | Done while awaiting |
| x | sessions | Delete (press twice) |
| [ ]  1-5 | browser | Timeline, Summary, Diff, Findings, Report |
| v | browser | Timeline filter |
| i | browser | Jump to Findings |
| f | browser | Flag this event |
| N | browser | New note |
| O | browser | Edit or delete note |
| n | browser | Follow-up while awaiting |
| e | browser | Done while awaiting |
| y | browser | Copy the selection, the finding, or the pane |
| s | browser | Open the share link when the session has one |
| E | browser | Export a session bundle |
| x | browser | Delete (press twice) |
| Ctrl+Enter | runner | Launch |
| Ctrl+S | runner | Save recipe |
| T | runner | Export this form as task YAML |
| [ ]  1-3 | runner | Recipe, Runtime, Extras |
| Enter | recipes | Open in the runner |
| l | recipes | Launch |
| s | recipes | Select |
| n | recipes | New |
| x | recipes | Delete |
| T | recipes | Export as task YAML |
| Enter | personas | Edit |
| n | personas | New |
| x | personas | Delete |
| Ctrl+S | personas | Save in the editor |
| s | pickers | Select |
| Ctrl+S | pickers | Apply the selection |
| Esc | pickers | Cancel |

The [Desktop HUD](#desktop-hud) shares `?` / `Esc` / `/` / `y` / `j` `k`
/ `n` `e` (awaiting) / `N`. HUD panes are Tab and Ctrl+1–5; `[` `]` scope
Timeline turns.

### Follow-up, fork, and re-run

| Path | When | What happens |
|------|------|----------------|
| Follow-up (`n`) | Container still running and awaiting | Same Grok session; next prompt on the turn gate |
| Done (`e`) | Awaiting | Mark done; list may show **ending** until shutdown finishes |
| Fork (`f`) | Session has *ended* | New Docker launch; parent history seeded; new session id |
| Re-run (`R`) | Any listed session | New launch from the same recipe fields |

While a session is live, use follow-up, not fork.

### Runner

Docker evals from a recipe (prompt, models, persona, repo, extras).
**Ctrl+Enter** launches, **Ctrl+S** saves, `[` / `]` switches panes.
Optional git URL clones into `runs/checkouts/`; a local path bind-mounts
as `/workspace` (one model). Default permission is `--always-approve`;
YOLO mode uses `grok --yolo`. Max turns (default **50**) is Grok
`--max-turns` per prompt.

### Export

`E` on the list or browser writes a session bundle under
`~/.groket/reports/` (profile in `export.default_profile`, or pick once).
`T` on the runner or recipes writes a batch task YAML under
`~/.groket/tasks/`.

## Desktop HUD

Summonable palette: search sessions, then Overview / Turns / Timeline /
Findings / Notes. Follow-up and Done when awaiting. It does not launch
evals. Details: [`groket-hud/README.md`](groket-hud/README.md).

```bash
groket serve -d        # or let the client start serve
groket hud             # one process + tray; second start is a no-op
groket hud --toggle    # show or hide (Wayland bind this)
groket hud --restart   # replace the running palette
```

Default hotkey **Cmd+Shift+G** (macOS) / **Ctrl+Shift+G** (Windows and
X11 Linux). Override with `hud.global_shortcut` in
`~/.groket/config.json` or `GROKET_HUD_SHORTCUT`. On Wayland bind
`groket hud --toggle`: a compositor bind forwards an activation token so
you can type immediately; tray **Show HUD** or a terminal `--toggle`
does not steal the keyboard. Sway places the overlay (float/center);
focus is that token. While the overlay is on screen, a live poll
re-reads overview about every **3 seconds** (idle sessions slower).
An unfocused pop-out or hidden overlay waits on control notifies instead.

`groket hud --install-desktop` writes user-local icons and a launcher.
**Quit Groket HUD** exits the palette only. [Emacs](#emacs) and
[Neovim](#neovim-09) attach to the same [control](#control) socket.

## Control

`groket serve` owns the per-user Unix socket. The four clients attach.

```bash
groket serve -d
groket serve status
groket serve stop
```

Bare `groket` and `groket hud` detach-start serve when the socket is
free (`--no-serve` attaches only). Quitting a client leaves serve
running. Methods, framing, and notifications:
[docs/control.md](docs/control.md).

## Emacs

```elisp
(load (string-trim (shell-command-to-string "groket editor emacs-path")))
```

Sessions open as Org. Same [control](#control) socket as the
[terminal app](#terminal-app) and [HUD](#desktop-hud).

## Neovim (0.9+)

```lua
vim.opt.rtp:prepend(vim.fn.trim(vim.fn.system({ "groket", "vim-path" })))
require("groket").setup()
```

Sessions open as Markdown. Start serve (or the terminal app) so the
socket exists.

## Batch

Headless Docker from task YAML (`examples/tasks/`).

| Field | Role |
|-------|------|
| `repo_url` / `repo_branch` | Git clone into `runs/checkouts/` |
| `repo_path:` | Host directory bind-mounted as `/workspace` |
| `yolo:` | `grok --yolo` when true |
| `turns:` | Scripted follow-ups on a **new** session |
| `resume_session_dir:` | Fork an *ended* session (same as terminal `f`) |
| `resume_session_id:` | Optional parent id (directory basename by default) |
| `max_turns:` | Grok `--max-turns` per prompt (default **50**) |

```bash
groket batch validate examples/tasks/demo_tasks.yaml
groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
```

Schemas: [tasks](https://indynull.github.io/groket/schemas/tasks.schema.json),
[rules](https://indynull.github.io/groket/schemas/rules.schema.json).

## Examples

Supported packs under [`examples/`](examples/README.md) — copy into
`~/.groket/` or pass paths. Not auto-loaded.

| Goal | Start here |
|------|------------|
| Smallest detector + rule | [`examples/detection/minimal/`](examples/detection/minimal/) |
| Full detector catalog | [`examples/detection/catalog/`](examples/detection/catalog/) |
| Analysis plugin | [`examples/analysis/plugins/session_event_count.py`](examples/analysis/plugins/session_event_count.py) |
| Batch tasks | [`examples/tasks/demo_tasks.yaml`](examples/tasks/demo_tasks.yaml) |

```bash
make examples-check
```

## Development

```bash
make install
make lint
make test
make ci              # lint + schema-check + hud-check + examples-check + test
```

Also: `groket doctor`, `groket gen …`, `groket rules validate`, `groket keys`.
