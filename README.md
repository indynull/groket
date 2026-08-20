<p align="center">
  <img src="brand/png/groket-mark.png#gh-light-mode-only" alt="groket" width="400" />
  <img src="brand/png/groket-mark-reverse.png#gh-dark-mode-only" alt="groket" width="400" />
</p>

# groket

[![CI](https://github.com/indynull/groket/actions/workflows/ci.yml/badge.svg)](https://github.com/indynull/groket/actions/workflows/ci.yml)
[![Codecov](https://codecov.io/gh/indynull/groket/graph/badge.svg)](https://codecov.io/gh/indynull/groket)
[![Docs](https://img.shields.io/badge/docs-pages-0A66C2)](https://indynull.github.io/groket/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

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
uv tool install --editable .    # clone: groket + groket-hud on PATH (needs Rust)
groket                          # terminal app
groket hud                      # desktop palette
```

```bash
uv tool install git+https://github.com/indynull/groket
groket
groket hud
uv tool upgrade groket
```

```bash
uv tool install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ groket
groket --version
```

Wheels for Linux, macOS, and Windows (Intel and ARM) are on
[TestPyPI](https://test.pypi.org/project/groket/).

## Paths

| Root | Default | Holds |
|------|---------|--------|
| Config home | `~/.groket` | `config.toml`, personas, detectors, rules, plugins, optional `keys.toml` |
| Work root | `~/.groket/work` | `runs/traces/`, recipes, Docker contexts, batch results |

```bash
groket                      # default work root
groket /path/to/work        # work root, traces tree, or one session dir
```

`~/.groket/config.toml` is the only prefs file (terminal app and desktop HUD).
Missing keys use defaults. Saves keep comments on keys they do not change.
Schema: [config](https://indynull.github.io/groket/schemas/config.schema.json)
(`groket config validate`, `just schema`). Copy
[`examples/config/config.toml`](examples/config/config.toml).

```toml
#:schema https://indynull.github.io/groket/schemas/config.schema.json

theme = "groket"
follow_os = false
show_host_sessions = false
auto_serve = true

[analysis]
plugins = []
auto_analyze_when = "session_complete"
analysis_workers = 1
live_refresh_workers = 1

[hud]
window_mode = false
global_shortcut = ""
desktop_notifications = true

[export]
default_profile = ""
```

Key remaps stay in `keys.toml` (below), not in this file.

Optional key diffs: `~/.groket/keys.toml` (`GROKET_KEYS` overrides the path).
A missing file keeps the catalog defaults. Esc, Enter, Tab, Shift+Tab, and
`?` are not remappable. The product default has no leader. An overlay may
set one printable leader (recommended Colemak: `;`) and bind `leader+X`
for one extra letter. Copy [`examples/keys/colemak.toml`](examples/keys/colemak.toml)
to `~/.groket/keys.toml` for home-row `n`/`e` nav with follow-up and Done
behind the leader. The TUI and HUD both use the resolved map for footer,
help, and dispatch. The footer shows the leader while it is armed.

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
under `~/.groket/notes/<session_id>/`. Subagent runs stay off the top
list; open them from the parent (Summary run table, or Timeline
Subagents filter — Enter, or click the tile in the desktop HUD). Esc
returns to that Timeline or Turns place. Background shells, monitors, and schedules live on Summary **Tasks**.
Workflows and subagent runs have their own Summary tabs. Timeline
filters Background / Workflows / Subagents list the bookends. Open a
row or a bookend to inspect the merged run (Asked / Happened / Failed).
Enter on a workflow child or subagent opens that child session. The
desktop Overview uses the same tabs.
Failed runs also appear on Findings. Those tables are not Jobs (`J`),
which is Docker / serve / container logs.

## Terminal app

`groket` (or `groket tui`) is the full eval client: session list, browser
panes, runner, recipes, personas, analysis, and export. Diff lists Grok
rewind snapshots (or approximate `search_replace` edits) with Prompt and
Assistant tabs above a files and hunk split.
The footer lists the keys that apply now; `?` is the full list.

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
| J | everywhere | Jobs and logs (Docker runs, TUI pool activity, serve log tail, container logs) |
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
| Diff | browser / HUD | Rewind snapshots (or approximate search_replace edits); Prompt/Assistant tabs above a files and hunk split; / fuzzy-finds path or hunk; h/l steps snapshots; y copies the highlighted file. HUD Turns cards show a Diff chip when that turn has a snapshot. |
| h / l / Left / Right | browser | Previous / next turn on the Timeline |
| j / k | browser | Previous / next event on the Timeline (also Up / Down) |
| v | browser | Timeline filter (Subagents lists spawn/finish; Background lists task and schedule bookends; Workflows lists workflow tool bookends) |
| Summary | browser / HUD | Session glance; Tasks, Workflows, Subagents, and Stats tabs (click the strip) |
| Tail | browser / HUD | Follow new events to the end while a turn is open (terminal and HUD). Off keeps the highlight still. |
| Enter | browser | Full-width event (Esc back to the list); or open a child from a spawn/finish row |
| i | browser | Jump to Findings |
| f | browser | Flag this event |
| N | browser | New note |
| O | browser | Edit or delete note |
| n | browser | Follow-up while awaiting |
| e | browser | Done while awaiting |
| y | browser / HUD | Copy the selection, the finding, or the focused / primary pane body |
| Ctrl+Shift+C | browser | Same as y |
| Ctrl+C | browser | Copy the selection or focused body; quit hint when neither applies |
| mouse drag | browser | Select text; release copies the selection (multi-line OK); y still works |
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
/ `h` `l` (Timeline turns while All turns is selected) / `n` `e` (awaiting) / `N`.
HUD panes are Tab and Ctrl+1–6 except on Notes, where Tab walks the note
fields and Ctrl+Tab or Ctrl+1–6 change panes. On Timeline, `[` is All
turns (Filter stays). `]` / `h` `l` jump to the next or previous turn
that still matches Filter, only while All turns is selected.
`u` or the logo leaves the open session for the session list.

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
A parent bundle includes `children/<id>/grok-trace.tar.gz` for each
openable child. Exporting an opened child is that child only.
`T` on the runner or recipes writes a batch task YAML under
`~/.groket/tasks/`.

## Desktop HUD

Summonable palette: Recent sessions (scroll or `j` for more), search,
then Overview / Turns / Timeline / Findings / Notes. `u` or the logo
returns to the session list. Follow-up and Done
when awaiting. It does not launch evals. Desktop notices are for eval
sessions and analysis; Host Grok chats already notify on their own.
Details: [`desktop/README.md`](desktop/README.md).

```bash
groket serve -d        # or let the client start serve
groket hud             # PATH binary from uv tool install; one process + tray
groket hud --toggle    # show or hide (Wayland bind this)
groket hud --restart   # replace the running palette
groket hud --rebuild   # cargo-build this checkout, then launch
```

`groket hud` runs `groket-hud` from `GROKET_HUD_BIN` or `PATH`. From a
checkout, `--rebuild` builds this tree; `--debug` is the unoptimized
binary; `--dev` is `cargo run`.

Default hotkey **Cmd+Shift+G** (macOS) / **Ctrl+Shift+G** (Windows and
X11 Linux). Override with `hud.global_shortcut` in
`~/.groket/config.toml` or `GROKET_HUD_SHORTCUT`. On Wayland bind
`groket hud --toggle`: a compositor bind forwards an activation token so
you can type immediately; tray **Show** or a terminal `--toggle`
does not steal the keyboard. Sway places the overlay (float/center);
focus is that token. While the overlay is on screen, a live poll
re-reads overview about every **3 seconds** (idle sessions slower).
An unfocused pop-out or hidden overlay waits on control notifies instead.

`groket hud --install-desktop` writes user-local icons and a launcher
named **groket** (Linux `.desktop` `Exec` uses `--show`, macOS
`~/Applications/groket.app`, Windows Start Menu). Re-run after moving
the binary or to refresh the launcher. Tray **Quit groket** exits the
palette only. [Emacs](#emacs) and
[Neovim](#neovim-09) attach to the same [control](#control) socket.

## Control

`groket serve` owns the per-user Unix socket. The four clients attach.

```bash
groket serve -d
groket serve status
groket serve stop
groket export-host -o host-catalog.json
```

`export-host` writes the host catalog snapshot serve uses (summary,
signals, and list status from the updates tail). It does not start serve.

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
[rules](https://indynull.github.io/groket/schemas/rules.schema.json),
[config](https://indynull.github.io/groket/schemas/config.schema.json),
[control](https://indynull.github.io/groket/schemas/control.schema.json).

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
just examples-check
```

## Development

```bash
just install
just lint
just test
just ci              # lint + schema-check + hud-check + examples-check + test
just bump 0.1.1      # version strings + CHANGELOG.md
```

Also: `groket doctor`, `groket gen …`, `groket rules validate`, `groket keys`.
