<p align="center">
  <img src="brand/png/groket-mark.png#gh-light-mode-only" alt="groket" width="400" />
  <img src="brand/png/groket-mark-reverse.png#gh-dark-mode-only" alt="groket" width="400" />
</p>

# groket

[![CI](https://github.com/indynull/groket/actions/workflows/ci.yml/badge.svg)](https://github.com/indynull/groket/actions/workflows/ci.yml)
[![Codecov](https://codecov.io/gh/indynull/groket/graph/badge.svg)](https://codecov.io/gh/indynull/groket)
[![Schemas](https://img.shields.io/badge/schemas-pages-0A66C2)](https://indynull.github.io/groket/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**groket** evaluates [Grok Build](https://docs.x.ai/build/overview)
sessions: timeline, workspace diffs, Docker evals, and personas.

Four clients talk to [`groket serve`](#control).

| Client | What it does |
|--------|----------------|
| [Terminal app](#terminal-app) | Browse sessions, launch evals, export |
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
| Config home | `~/.groket` | `config.toml`, personas, optional `keys.toml` |
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

theme = "auto"
follow_os = false
auto_serve = true
live_refresh_workers = 1

[hud]
window_mode = false
global_shortcut = ""
desktop_notifications = true

[export]
default_profile = ""
```

`theme = "auto"`: the terminal app follows the terminal (`COLORFGBG`,
then the desktop) and paints the host pair paper (`ansi-light` /
`ansi-dark`). The desktop palette follows the system light/dark pair
and, when the OS reports it, system paper and ink. Picking any member
of a named pair (`gruvbox` or `gruvbox-light`) stores the family and
sets `follow_os = true`; both clients apply the desktop member. An
unpaired name (`nord`) pins both clients. Aliases `groket` and
`groket-light` mean `auto`. Drop a TOML file in `~/.groket/themes/`
(see [`examples/themes/`](examples/themes/)) and point `theme` at its
stem.

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
sessions are native Grok trees at `~/.grok/sessions` (always loaded;
`is:host` filters the list). `groket -P ~/.grok/sessions` browses Host while
keeping the default work root for new runs. Notes on Host sessions write
under `~/.groket/notes/<session_id>/`. Every note has a `source` (who
wrote it). Control `notes/upsert` accepts any field bag plus that
source. A new note uses `~/.groket/notes_schema.toml`. Editing a note
also shows extra stored fields as free-text. Notes, the edit form,
and HUD Notes show a source badge plus the stored fields. Subagent runs stay off the top
list; open them from the parent (Summary run table, or Timeline
Subagents filter — Enter, or click the tile in the desktop HUD). Esc
returns to that Timeline or Turns place. Background shells, monitors, and schedules live on Summary **Tasks**.
Workflows and subagent runs have their own Summary tabs. Timeline
filters Background / Workflows / Subagents list the bookends. Open a
row or a bookend to inspect the merged run (Asked / Happened / Failed).
Enter on a workflow child or subagent opens that child session. The
desktop Overview uses the same tabs.
Failed runs are listed on Summary. Those tables are not Jobs (`J`),
which is Docker / serve / container logs.

## Terminal app

`groket` (or `groket tui`) is the eval client: session list, browser
panes, runner, recipes, personas, and export. Diff lists Grok
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
| J | everywhere | Jobs and logs |
| q | everywhere | Quit when no field is focused |
| / | sessions | Search (Tab completes the last token) |
| r | sessions | New run |
| C | sessions | Recipes |
| P | sessions | Personas |
| s / Space | sessions | Select (also Space) |
| S | sessions | Select all |
| R | sessions | Re-run the highlighted recipe as a new session |
| f | sessions | Fork an ended session into a new multi-turn |
| Ctrl+S | sessions | Save the row as a recipe |
| E | sessions | Export a session bundle |
| n | sessions | Follow-up while awaiting |
| e | sessions | Done while awaiting |
| x | sessions | Delete (press twice) |
| [ ]  1-4 | browser | Timeline, Summary, Diff, Notes |
| h / l / Left / Right | browser | Previous / next turn on the Timeline |
| j / k | browser | Previous / next Timeline event, or previous / next note |
| v | browser | Filter (Subagents, Background, Workflows) |
| Enter | browser / HUD | Open a Timeline event or child; edit the focused note |

| N | browser / HUD | New note (TUI Notes); Notes pane (HUD) |
| n | browser | Follow-up while awaiting |
| e | browser | Done while awaiting |
| y | browser / HUD | Copy the selection or the focused / primary pane body |
| Ctrl+Shift+C | browser | Same as y |
| s | browser | Open the share link when the session has one |
| E | browser | Export a session bundle |
| x | browser / HUD | Delete the focused note (press twice); on the session list, delete the session |
| Ctrl+Enter | runner | Launch |
| Ctrl+S | runner | Save recipe |
| T | runner | Export this form as task YAML |
| [ ]  1-3 | runner | Recipe, Runtime, Extras |
| Enter | recipes | Open in the runner |
| l | recipes | Launch |
| L | recipes | Launch selected |
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
/ `h` `l` (Timeline turns while All turns is selected) / `n` `e` (awaiting) / `N`. HUD panes are Tab
and Ctrl+1–5 except on Notes, where Tab walks the note fields and
Ctrl+Tab or Ctrl+1–5 change panes. `[` is All turns (Filter stays).
`]` / `h` `l` jump to the next or previous turn that still matches
Filter, only while All turns is selected. `u` or the logo leaves the
open session for the session list. `g` on Turns opens Timeline for that
turn. Enter opens (or edits the focused note).

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

### Catalog search

`/` on the session list. Last-token completions appear while you type. `?` notes that. Bare words match title, id, and label. Space is AND. `AND`, `OR`, and `NOT` must be that spelling (`and` is a word in the title). The list updates after a short pause (same 0.28s idle on the terminal and the desktop palette) so each key does not walk the catalog. The palette sends the committed query to `groket serve`.

| Token | Matches |
|-------|---------|
| `is:running` `is:awaiting` `is:ending` `is:complete` `is:cancelled` | Status |
| `is:host` `is:eval` | Origin |
| `has:workflow` `has:note` `has:goal` `has:plan` `has:subagent` `has:task` `has:job` `has:schedule` `has:error` `has:failure` `has:diff` `has:git` `has:context` `has:compaction` `has:doom` | Presence (`has:plan` is at least one). Counts use the written pair (`plans:>=2`, `errors:>=5`, `goals:1`). Both words are listed in the schema; nothing is pluralized. `has:goal` is ``goal/state.json``. `has:plan` is ``plan.json`` or ``plan_mode.json``. `has:task` is Overview Tasks (jobs or schedules). `task:` is still the batch task id. Git stays yes/no. |
| `workflows:` `notes:` `goals:` `plans:` `errors:` `turns:` `tools:` `events:` | Counts, with `>` `>=` `<` `<=` `=` |
| `duration:` | Session length (`1h`, `2d`, `30m`), same compares |
| `in:~/path` | Directory the session was run in |
| `model:` `task:` | Substring |
| `after:` `before:` | `updatedAt` (ISO, `yesterday`, `2d`, `2 days ago`) |
| `OR` `NOT` `-` `( )` | Compose |

| Query | Meaning |
|-------|---------|
| `has:note AND is:awaiting` | Waiting on a reply, and you already wrote notes |
| `is:complete AND NOT has:note` | Finished sessions you have not written up |
| `has:error OR has:failure` | Tool errors or a failed child |
| `workflows:>=2 AND NOT is:complete` | Multi-workflow sessions still going |
| `is:eval AND errors:>=5 AND NOT has:note` | Noisy evals you have not written up |
| `notes:>=2 AND after:yesterday` | Recently updated, more than one note |
| `has:subagent OR has:workflow` | Spawned a child or a workflow |
| `in:~/src/app AND after:yesterday` | This repo, updated since yesterday |

Timeline search (same `AND` / `OR` / `NOT`) also takes `is:tool` (or `user`, `assistant`, `error`, `session`, `subagent`, `background`, `workflow`), `has:error`, `tool:read_file`, `turn:2`, `user:hello`, and `duration:>=2` (the Dur column: tool call to result, or time to the next event). Turns search (desktop) takes `has:error`, `has:subagent`, `tools:>=5`, `errors:>=2`, `events:>=20`, and `duration:>1m` (turn wall time). Last-token hints appear under the box. The Filter and Turn dropdowns stay. The Timeline search box is a full-width row under Filter / Turn / Tail.

## Desktop HUD

Summonable palette: Recent sessions (scroll or `j` for more), catalog
search (same query language as the terminal list), then Overview /
Turns / Timeline / Diff / Notes. `u` or the logo
returns to the session list. Follow-up and Done
when awaiting. It does not launch evals. Desktop notices are for eval
sessions; Host Grok chats already notify on their own.
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
vim.opt.rtp:prepend(vim.fn.trim(vim.fn.system({ "groket", "editor", "vim-path" })))
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
[config](https://indynull.github.io/groket/schemas/config.schema.json),
[control](https://indynull.github.io/groket/schemas/control.schema.json).

## Examples

Supported packs under [`examples/`](examples/README.md) — copy into
`~/.groket/` or pass paths. Not auto-loaded.

| Goal | Start here |
|------|------------|
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

Also: `groket doctor`, `groket gen …`, `groket keys`.
