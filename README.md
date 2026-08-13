<p align="center">
  <img src="brand/png/groket-lockup-horizontal.png" alt="groket" width="520" />
</p>

# groket

**groket** (grok evaluation tool) is a [Textual](https://github.com/Textualize/textual)
TUI for evaluating [Grok Build](https://docs.x.ai/build/overview) agent sessions:
timeline, findings, workspace diffs, Docker launches, personas, and pluggable
detectors / analysis plugins.

It is **not** the Grok coding agent. That CLI is `grok`
([install](https://docs.x.ai/build/overview),
[headless](https://docs.x.ai/build/cli/headless-scripting#cli)).
Groket runs alongside it: open traces, score runs, and drive Docker-based evals.

## Install

### Development (clone)

```bash
make install    # .venv + `groket` on PATH via uv tool (editable)
groket          # interactive TUI
```

### Users (uv tool)

```bash
uv tool install git+https://github.com/indynull/groket
groket
uv tool upgrade groket   # later
```

Optional `groket._listwalk` Limited API extension accelerates session discovery; the Python walk remains when the module is absent.

### Paths

| Root | Default | Holds |
|------|---------|--------|
| Config home | `~/.groket` | `config.json`, `hud.log`, personas, detectors, rules, analysis plugins, prefs |
| Work root | `~/.groket/work` | `runs/traces/`, recipes, Docker build contexts, batch results |

```bash
groket                      # default work root
groket /path/to/work        # work root, traces tree, or a single session dir
```

## Using the TUI

### Sessions home

Browse past and **live** runs (filesystem watch + periodic read-only refresh).

| Key | Action |
|-----|--------|
| `Enter` | Open session browser |
| `/` | Search sessions |
| `s` / `Space` | Select row (multi-select); `S` select all / clear |
| `r` | New run (runner) |
| `R` | Re-run recipe from the highlighted session (fresh session, same launch fields) |
| `f` | **Fork** an *ended* session into a new interactive multi-turn (see below) |
| `C` | Run configs (recipes) |
| `P` | Personas |
| `d` | Rules (enable/disable detectors) |
| `a` | Analyze selection |
| `H` | **Show host** / **Hide host** sessions (`~/.grok/sessions`; footer label flips) |
| `E` | Export session bundle (tarball) |
| `n` | Follow-up prompt (awaiting sessions; optional **last turn** in the modal) |
| `e` | End session (Done) while awaiting — list shows **ending** until shutdown finishes |
| `x` / `Delete` | Delete session(s) — press twice to confirm |
| `j` | Jobs / logs |
| `F5` / `Ctrl+R` | Refresh |
| `?` | Help |
| `Ctrl+P` | Command palette (theme, tips, analysis, …) |
| `q` | Quit |

Columns: select, **Origin** (`Eval` = groket launches under `work/runs/traces`,
`Host` = native Grok under `~/.grok/sessions`), title, model, status
(`running` / `awaiting` / `ending` / `complete` / `cancelled`), duration,
context fill from `signals.json` when present, event count, and findings
(count plus high/medium after analysis). Task id from batch YAML is still
searchable (`/`) but is not a list column. Subagent session dirs are hidden
so each eval is one row. Prefs live in `~/.groket/config.json`
(`show_host_sessions` for the host catalog).

### Session browser

Panes (`[` / `]` or digits **1–5**):

1. **Timeline** — events + detail (selected event shows sequential operator turn); `v` filter, `f` flag, `N` new operator note, `O` / palette edit or delete note, `/` search  
2. **Summary** — overview, usage, turns (context samples when live)  
3. **Diff** — workspace changes  
4. **Findings** — detector / analyzer hits (`i` jumps here)  
5. **Report** — analysis panels, flags, and operator notes  

**Copy from extractable panes:** the TUI owns the mouse, so OS highlight-to-copy
does not work. Body text uses ``SelectableStatic`` (detail, summary, diff,
findings header, Report sub-panes). Drag to highlight, then **`y`** /
**Ctrl+Shift+C** / **Ctrl+C**. Tab focuses a body pane for full-pane yank.

- **One Model Feedback issue:** Findings tab (`4` / `i`) → highlight the row
  → **`y`** copies the **Issue (copy into the Issue box)** text
  (`What` / `Where` / `Why` / `Should have` / `Pattern`) when the analyzer
  filled those fields (e.g. `mf_form_feedback`). On **Report**, the same
  Issue fence (and Form fields) are separate Tab-focusable panes under the
  plugin card — focus the pane, then **`y`**. Palette “export finding”
  still writes a full markdown file under `~/.groket/reports`.
- **No selection:** focused body, else the whole active pane (all visible
  Report sub-panes, Summary, Diff, or Timeline detail).

OSC 52 clipboard. tmux over SSH: `set -g set-clipboard on` so OSC 52 reaches
the local pasteboard.

Multi-turn live bar: follow-up input, optional **Last turn**, `n` focus, `e` Done.
`x` deletes (double-press). `Esc` back to the list.

**Eval vs host sessions:** Docker launches always land under
``work/runs/traces`` (**Eval** in the list). Press **`H`** (footer **Show host**
/ **Hide host**) to also list native sessions from ``~/.grok/sessions`` at
their real paths — no copy into the work tree. ``groket -P ~/.grok/sessions``
browses that tree while keeping the default work root for new runs. Operator
notes on host sessions write under ``~/.groket/notes/<session_id>/`` so the
live Grok tree stays clean.

### Local editor control

Groket serves a **versioned JSON-RPC control plane** on a private per-user Unix
socket for Emacs, Neovim, HUD, scripts, and the TUI. Default path:
``$XDG_RUNTIME_DIR/groket/control.sock`` (else ``~/.groket/run/control.sock``).
``-s`` / ``--socket PATH`` selects another path on any client or on ``serve``.

**Control owner** (only ``groket serve`` binds the socket)

```bash
groket serve                 # foreground (Ctrl-C / SIGTERM)
groket serve -d              # background; return when the socket accepts
groket serve stop            # SIGTERM recorded daemon pid
groket serve restart         # stop then start -d (background by default)
groket serve restart --foreground
groket serve status          # exit 0 if live; "running pid=… socket=…"
groket serve status --json

groket serve -d -P ~/.groket/work
groket serve -d -s /path/to/control.sock
groket serve -d --host       # force host sessions in list
```

The headless process answers ``initialize``, session/notes/analysis methods, and
publishes change notifications. A second ``serve -d`` reports already running.

**Serve log** (detached): next to the socket, e.g. ``~/.groket/run/control.sock.log``.
HUD control and decode errors: ``~/.groket/hud.log`` (override ``GROKET_HUD_LOG``).
Successful RPCs log at **DEBUG** (method, status, ms, session/query summary).
Errors and conflicts log at **INFO**. Set ``GROKET_SERVE_LOG_LEVEL=DEBUG``
before ``groket serve restart`` for a full click-around trace.

**TUI / HUD (clients — never own the socket)**

```bash
groket                       # TUI; starts serve -d if socket free, then attaches
groket tui                   # same as bare groket
groket --no-serve            # do not spawn; attach only if owner already live
groket --no-socket           # TUI offline (no control plane)
groket hud                   # palette; same serve auto-start by default
```

Quitting the TUI or HUD does **not** stop the control owner. Leave
``groket serve -d`` running across TUI/HUD launches. A warm owner serves
the catalog from RAM; killing serve on every client start pays the cold
walk again. First TUI/HUD paint uses one ``session/list`` page; do not
restart serve to “refresh” the list.

**What clients can do**

| Method | Role |
|--------|------|
| `initialize` | Protocol version, capabilities, `renderFormats` |
| `session/list` | Catalog rows (`query`, `limit`, `offset`, optional `sinceRevision`) from the owner’s warm catalog. Omitting `limit`/`offset` is the first page. `sinceRevision` matching the owner’s `revision` returns no rows (`unchanged`). |
| `session/get` | Rich session meta (status, context, git, counts, notes revision) |
| `session/overview` | Meta + turns (with user `summary`) + lazy timeline stub + notes |
| `session/timeline` | Paged timeline events (`offset`, `limit`, `type`, `promptIndex`, `contentChars`; each row includes sequential `turnIndex`) |
| `session/turns` | Turn segments (`summary`, `userEventIndex`, counts, prompt indexes) |
| `session/usage` | Tool / MCP / skill usage summary |
| `session/findings` | Cached analysis findings with turn/event links |
| `session/open` | Resolve session and broadcast `session/selected` to all clients |
| `session/render` | Project a session document (`format`: see below) |
| `notes/list` / `notes/upsert` / `notes/delete` | Operator notes with revision checks |
| `analysis/run` | Start background analysis on the owner (`force` optional) |
| `analysis/status` | Poll job state (`idle` / `running` / `done` / `error`) |
| notifications | `session/selected`, `session/changed`, `notes/changed`, `analysis/changed` |

**Desktop HUD** (Sol-style palette — iced)

```bash
uv run groket serve -d        # or rely on client auto-start
uv run groket hud             # one process + tray; second start is a no-op
uv run groket hud --restart   # replace the running HUD
uv run groket hud --rebuild   # force cargo rebuild then start
uv run groket hud --dev       # cargo run (debug)
uv run groket hud --debug     # unoptimized binary
uv run groket hud --install-desktop  # user-local icons + launcher (no package)
uv run groket hud --toggle    # show/hide (Wayland summon; forwards XDG_ACTIVATION_TOKEN)
uv run groket hud --show      # show palette (starts HUD if needed)
# Override binary: GROKET_HUD_BIN=/path/to/groket-hud
```

``--install-desktop`` writes icons and a launcher for the **current user**
only (no DMG/MSI/system package). Linux: ``~/.local/share/applications`` +
hicolor icons. macOS: ``~/Applications/Groket HUD.app`` (wrapper to this
binary). Windows: Start Menu shortcut under the user profile. The launcher
``Exec`` path is the binary used at install time — re-run after moving or
rebuilding it. Runtime tray and iced window icons work without this step.
Details: ``groket-hud/README.md``.

**Linux build deps** (iced graphics; once per machine — not shipped by the
Python package)::

```bash
sudo apt install libxkbcommon-dev libwayland-dev pkg-config
```

See ``groket-hud/README.md`` for the full prerequisite list.

Floating, frameless, always-on-top session palette (search sessions; overview,
turns, timeline, findings, notes). Awaiting sessions get a follow-up field and Done. Timeline pages stay
capped so a large host session remains usable. Launch is always the overlay; the pop-out icon in the
search bar opens a decorated desktop window. Close that window to keep the
HUD process running; the summon hotkey or the tray **Show HUD** item brings
the overlay back. A tray icon is registered when the host supports it:
StatusNotifier on Linux (Swaybar / Waybar), menu bar on macOS, notification
area on Windows. Left-click shows the palette; **Quit Groket HUD** exits the
HUD only. Create, edit, and delete operator notes the same way as the TUI.
While the palette is open, a **live poll** re-reads overview and the timeline
tail for running/awaiting turns (~3s; idle sessions slower) so a mid-turn
Timeline tab updates without reopening the HUD. The HUD does not start evals,
recipes, or Docker. ``groket hud`` **detaches** like Sol
(background agent); on macOS the overlay starts as an accessory process (**not**
in the Dock or **Cmd+Tab**) until you pop out to a normal window. Default
hotkey **Cmd+Shift+G** (macOS) / **Ctrl+Shift+G** (Windows and X11 Linux;
Wayland: ``groket hud --toggle``, tray Show HUD, or a Sway bind — see
``groket-hud/README.md``);
override in ``~/.groket/config.json``::

```json
{
  "hud": {
    "global_shortcut": "Cmd+Shift+Space"
  }
}
```

(``+``-separated modifiers: ``Cmd``/``Super``, ``Control``, ``Alt``, ``Shift``,
plus one key. Env ``GROKET_HUD_SHORTCUT`` wins over config. Restart the HUD
after changing.)

While the HUD process is running it posts desktop notifications when a
session becomes awaiting, completes, is cancelled, or fails, and when
analysis finishes. Linux uses the freedesktop Notifications bus (dunst,
mako, fnott, swaync). macOS uses Notification Center. Windows uses
toasts. The cream three-bar tray tile (64px) is written under
``~/.groket/hud-notify.png`` when possible. Disable with
``GROKET_HUD_NOTIFY=0`` or::

```json
{
  "hud": {
    "desktop_notifications": false
  }
}
```

Focuses the search field on show. The overlay hides on **Esc** or the summon
hotkey (not on focus loss). Use ``groket hud --foreground`` to attach for
debugging, or ``--restart`` to replace a running agent. Client of the control
plane only — quitting the agent (tray **Quit Groket HUD**, or killing the
process) leaves ``groket serve`` running. Build details: ``groket-hud/README.md``.

**List search contract.** ``session/list`` ``query`` is a **case-insensitive
substring** over id/title/label/model/status/outcome/origin (not the filesystem
path — ``gro`` must not match every ``~/.grok/sessions`` row). Applied on the
control owner after the warm domain catalog is built. First HUD/TUI paint
drains ``session/list`` pages until ``matched``; later quiet polls send
``sinceRevision`` and skip the table when the owner reports ``unchanged``.
Editors that pass ``query`` use the same server filter. Omitting ``offset``
still returns the first page.

**Daemon catalog warm.** Headless ``serve`` rebuilds the session catalog in the
background at start and on a short interval (and when scan roots change) so
attach clients get a fast ``session/list`` without a cold full-tree walk on the
hot path. Each row reads ``summary.json``, ``signals.json``, and turn markers
in ``events.jsonl`` (not ``updates.jsonl``). Analysis plugins load on the first
``analysis/run``, not at serve start.

Canonical notes remain ``operator_notes.toml``; the socket never invents a
second store. **One process owns the default socket** — additional TUIs attach
as clients rather than crashing or stealing ownership.

**Document formats** (`session/render` → `format`)

| `format` | `contentType` | Typical client |
|----------|---------------|----------------|
| `org` (default) | `text/org` | Emacs |
| `markdown` | `text/markdown` | Neovim |
| `json` | `application/json` | Scripts, export, tooling |

Markdown uses YAML front matter plus ``<!-- groket:… -->`` machine tags.
Org uses property drawers. JSON is a structured snapshot for tools (not the
primary interactive edit buffer).

**Useful for**

- Reading user/assistant turns and writing schema-backed operator notes in a
  real editor while the TUI stays on timeline, status, and Docker.
- Revision-safe concurrent note edits (expected revision on every mutation).
- Jumping TUI selection from a prompt in the buffer (and the reverse via
  notifications).
- Catalog browse and pick from the editor without re-scanning the filesystem.
- Scripted dump of a session as Markdown or JSON for reports, tickets, or CI.
- A second-screen companion that listens for session/note change notifications.

Packaged clients (same protocol):

#### Emacs

```elisp
(load (string-trim (shell-command-to-string "groket editor emacs-path")))
;; optional: (setq groket-auto-refresh t)  ; default on
```

| Command / key | Action |
|---------------|--------|
| `M-x groket-find-session` | Completing-read over the catalog (prefix arg → filter) |
| `M-x groket-list-sessions` | Buttonized `*groket-sessions*` buffer |
| `M-x groket-open-session` | Open path/id as Org buffer |
| `C-c C-c` | Save note at point |
| `C-x C-s` | Save all notes |
| `C-c C-n` / `C-c C-k` | New / delete note |
| `C-c C-o` | Select prompt in the TUI |
| `C-c C-r` (Evil: `gr`) | Refresh projection |

User/assistant turns are `#+begin_src markdown` blocks; property drawers hold
machine ids (folded on load). Trace is read-only; only note field bodies
edit. Remote notes/trace changes auto-reload when `groket-auto-refresh` is
non-nil and the buffer is unmodified. When the socket is missing, opening a
**session directory** can start the TUI in a terminal buffer.

#### Neovim (0.9+)

Classic Vim without Lua is not supported. Sessions open as **Markdown**.

```lua
vim.opt.rtp:prepend(vim.fn.trim(vim.fn.system({ "groket", "vim-path" })))
require("groket").setup({
  -- socket, executable, picker, keys, highlight, auto_refresh …
  -- auto_start = false by default (start `groket` yourself; socket must exist)
})
```

(`groket vim-path` prints the runtimepath root. Global maps use ``<leader>``
— set ``vim.g.mapleader`` **before** `setup`. Session note maps use
``<LocalLeader>`` (often ``\``; set ``vim.g.maplocalleader`` before
`setup`). Projection is always Markdown.)

| Command / key | Action |
|---------------|--------|
| `:GroketFindSession` / `<leader>gs` | Pick catalog session and open |
| `:GroketSessions` / `<leader>gl` | Browser (`f` filter, `<CR>` open, `P` fuzzy pick) |
| `:GroketOpenSession {path-or-id}` | Open Markdown projection; select in TUI |
| `:GroketRefresh` / `R` / `<LocalLeader>r` / `<leader>gR` | Reload projection |
| `<LocalLeader>c` / `<C-s>` | Save note under cursor |
| `<LocalLeader>s` / `:w` | Save **all** notes in the buffer |
| `<LocalLeader>n` | New note (cursor must be inside a **Prompt**) |
| `<LocalLeader>k` | Delete note under cursor |
| `<LocalLeader>o` | Select this prompt in the TUI |
| `<LocalLeader>O` / `:GroketOutline` | Location-list outline (prompts / notes) |
| `<LocalLeader>h` / `:GroketHighlight` | Cycle highlight: soft → calm → full → none |
| `<LocalLeader>?` | Show key reminder |

User/assistant turns are fenced `` ```markdown `` blocks (outer fence length
outruns nested code fences). Note field bodies stay **indented** for
edit/save. Machine tags (`<!-- groket:… -->`) are concealed in the buffer but
still present for parsing. Default `highlight = "soft"` (treesitter + quieter
chrome); `auto_refresh = true` reloads when notes/trace change if the buffer
is unmodified. After **new note**, the cursor jumps to the first field.

Session pick uses stock ``vim.ui.select`` (or Telescope / fzf-lua / mini.pick /
snacks when installed; `picker = "auto"`). Overrides such as dressing.nvim or
telescope-ui-select improve filtering without a custom float. Start the TUI
first so the control socket exists.

**Export as task** (`T` on Runner or Recipes): write a batch tasks YAML
(prompt, repo/local path, persona, models, max_turns, yolo, …). A modal asks
for the file path (default `~/.groket/tasks/<task_id>.yaml`). Run extras
(plugins/skills/MCP) are noted in comments — put those on the persona for
batch, or re-apply as runner extras.

**Export** (`E` in the browser or on the sessions list): builds a session
bundle under `~/.groket/reports/` using an **export profile** (default
``archive-full``). Profiles control packaging, which content units to
include, and (later) the human renderer id.

**Config**

- Default profile: ``export.default_profile`` in ``~/.groket/config.json``
  (built-ins: ``archive-full``, ``archive-org``, ``trace-only``).
- User profiles: ``~/.groket/export_profiles/*.yaml`` (same id overrides a
  built-in). Fields: ``id``, ``name``, ``packaging`` (`tar.gz` | `dir`),
  ``include`` (unit list), ``renderer``, ``renderer_options``.

**Built-in units** (`include`): ``grok_trace``, ``run``, ``summary``,
``analysis``, ``analysis_reports``, ``flags``, ``notes``, ``readme``,
``manifest``.

**Built-in renderers** (`renderer`): ``markdown`` (default for
``archive-full``), ``org`` (Org mode human files; use profile
``archive-org``), ``plain`` (``.txt``). Human files: ``human/summary.*``
and analysis reports next to cache JSON as ``*.md`` / ``*.org`` / ``*.txt``.

**Always written for `archive-full` when selected and data exists**

- **`grok-trace.tar.gz`** — nested archive that is **only** the output of
  ``grok trace --local $session_id`` (exact CLI bytes; no groket repack).
  Requires ``grok`` on ``PATH``. Grok session files only
  (``export_metadata.json``, `events.jsonl`, `chat_history.jsonl`, …). Does
  **not** contain groket flags, notes, analysis cache, or eval `run/` extras.
- **`manifest.json`** — inventory (profile, packaging, include, members)
- **`README.txt`** — short layout notes
- **`human/summary.*`** — session overview (title, model, outcome, counts,
  Grok session summary text, usage) in the profile renderer dialect
- **`run/`** — launch recipe / prompt / config / turn gate under a work volume
- **`analysis/`** — analysis cache (``*.json``); ``analysis_reports`` adds
  a human report per analyzer (plugin ``artifacts["report"]`` or findings)
- **`flags.json`** — operator flags (session or `~/.groket/flags/…`; outer only)
- **`notes/`** — `operator_notes.toml` from the notes store. Schema is
  `~/.groket/notes_schema.toml` (not bundled). Author with the TUI (`N` / `O`)
  or the Emacs Org buffer. Host notes live under `~/.groket/notes/`.

``trace-only`` is nest + readme + manifest. Host sessions often have no
``run/``. Packaging ``dir`` writes a folder instead of a tarball.

**TUI:** **`E`** exports with ``export.default_profile`` when that is set in
``~/.groket/config.json``; if unset, **`E` asks once** (profile picker) and
saves the choice as the default. Command palette **Export with profile…**
picks a profile for that export only (does not change the default).

### Multi-turn and forking

| Path | When | What happens |
|------|------|----------------|
| **Live follow-up** (`n` / browser bar) | Session container still running and awaiting | Same Grok session; host writes the next prompt on the turn gate |
| **End** (`e`) | Awaiting | Mark done; status may show **ending** until the container finishes |
| **Fork** (`f` / palette **Fork session**) | Session has *ended* (chat/events present) | New Docker launch: parent history seeded under `.groket-resume-seed/`; first turn is `grok --resume --fork-session` (new session id). Type the continuation prompt; multi-turn on. The browser **timeline inherits parent turns** (Grok often writes only the new turn into the child dir). **Workspace** usually lives under `runs/checkouts/<container>/` (git clone or CoW from parent) and is bind-mounted as `/workspace`. If the recipe has **`repo_path`**, fork remounts that same host directory (live tree; no CoW). On **reflink** filesystems (btrfs, xfs) managed-checkout forks use CoW (`cp --reflink=always`). On **ext4** (no reflink) groket does **not** silently full-copy multi‑GB trees: it falls back to a fresh git clone / empty workspace (parent dirt not preserved). Set `GROKET_ALLOW_FULL_WORKSPACE_COPY=1` to force a full recursive copy when you need dirt on non-reflink volumes. |
| **Re-run** (`R`) | Any listed session | New launch from the same recipe fields — **not** a conversation continue |

While a session is still live, use follow-up (`n`), not fork. Each fork is an
independent branch of the parent snapshot (parent files are not overwritten).

**Fork is a new container**, not the same disk layout as the parent eval:

- **Persona MCP / plugins / skills** are re-applied from the runner prefill.
  The launch recipe (`run.json`) is written on the **traces volume at container
  start** (not only when the container exits), so interactive / still-running
  sessions and re-forks keep `persona_id`, plugins, skills, and MCP. Prefill
  also falls back to the **fork parent seed** recipe when the child dir has
  none. Plugins are **staged on the host** into `*.stage/plugins/checkouts/`
  and installed in-container with `grok plugin install --trust` only (no git
  inside the image). Plugin skill packs are also staged under
  `~/.grok/skills/<id>/` so skill reads stay stable when Grok’s install dir is
  a new `src-<hash>/`.
- Parent chat that hardcodes `/root/.grok/installed-plugins/<old-id>/…` is
  bridged by recreating those directory names as symlinks after install
  (`RESUME_PLUGIN_DIR_ALIASES`).
- **Share** is a new `grok share <child-id>` for the forked session (parent
  share JSON is not copied into the seed). The TUI shows a share as ready only
  when the last write has a URL and an empty error field.

**Batch / task YAML:**

| Field | Role |
|-------|------|
| `repo_url` / `repo_branch` | Git clone into a managed checkout under `runs/checkouts/` |
| `repo_path:` | Host directory bind-mounted as `/workspace` (live tree; **no** clone/CoW). Single model only. Agent edits that directory; root-owned new files are reclaimed for the host user on exit |
| `yolo:` | When true, launch with `grok --yolo` (default false → `--always-approve`) |
| `turns:` | Scripted follow-ups after the primary `prompt` on a **new** session |
| `resume_session_dir:` | Host path to an *ended* session dir — **fork** (same as TUI `f`): seed history, first turn `grok --resume --fork-session`; `prompt` is the continuation; optional `turns` after that |
| `resume_session_id:` | Optional parent id (defaults to the directory basename) |
| `max_turns:` | Grok `--max-turns` per prompt (default **50**); also allowed under `defaults:` |

Example: see commented `demo-fork-resume` / `repo_path` notes in [`examples/tasks/demo_tasks.yaml`](examples/tasks/demo_tasks.yaml).

### Runner

Docker evals from a recipe (prompt, models, persona, repo, extras).
**Ctrl+Enter** / **Ctrl+J** launch, **Ctrl+S** save, `[` / `]` panes.
`Esc` back (discard prompt if dirty).

**Workspace:** optional git **Repository** URL (cloned into `runs/checkouts/`),
or **Local path** (bind-mount that host directory as `/workspace` — edits are
permanent; one model only). Managed checkouts are chowned to the host user on
exit; **local path** only reclaims **root-owned** paths the agent created (so
your existing tree is not fully re-chowned).

**Permissions:** by default the agent runs with ``--always-approve``. Check
**YOLO mode** on the runner (or set task YAML ``yolo: true``) to use
``grok --yolo`` instead (same auto-approve family; sets `yolo` in container
config / session telemetry).

Pick reasoning effort with the model token (`model:effort`, e.g. `…:xhigh` or
`…:max`) when selecting models for a run.

**Max turns** (Runtime pane, default **50**) sets Grok `--max-turns`: how many
agent tool/plan steps are allowed **per prompt** (not host multi-turn count).
Batch task YAML may set `max_turns:` (or document-level `defaults.max_turns`).

Runtime shows **persona + effective** plugins / skills / MCP / inline skills /
env (persona base merged with this-run extras). Extras pane still has the full
breakdown.

### Personas & configs

**P** / **C** — tabbed editors (`[` / `]` + digits), **Ctrl+S** save, **Esc** cancel.
Env key/value editing and Grok marketplace plugins/skills/MCP are supported.

## CLI (beyond the TUI)

| Command | Purpose |
|---------|---------|
| `groket serve` | Control owner (`-d` detach; `stop` / `restart` / `status`) |
| `groket hud` | Desktop palette client |
| `groket doctor` | Host checks (Docker, Grok auth, paths) — no TUI |
| `groket editor emacs-path` / `vim-path` | Packaged editor client paths |
| `groket gen …` | Scaffold detectors, rules, analysis plugins, tasks under `~/.groket/` |
| `groket batch …` | Headless Docker runs from a task YAML catalog |
| `groket rules …` | Validate rules / composites YAML |

```bash
groket doctor
groket serve -d
groket gen detector my_check
groket rules validate examples/detection/minimal/rules/demo_rule.yaml
groket batch validate examples/tasks/demo_tasks.yaml
groket batch run -t examples/tasks/demo_tasks.yaml -m <model-id>
# Browse host Grok sessions (default work root still used for Docker launches):
groket -P ~/.grok/sessions
```

Schemas (editors / Pages):

- Tasks: https://indynull.github.io/groket/schemas/tasks.schema.json  
- Rules: https://indynull.github.io/groket/schemas/rules.schema.json  

Prefer the TUI runner for interactive work.

## Examples (reference packs)

Supported, CI-gated packs under [`examples/`](examples/README.md) — copy into
`~/.groket/` or pass paths. **Not** auto-loaded.

| Goal | Start here |
|------|------------|
| Smallest detector + rule | [`examples/detection/minimal/`](examples/detection/minimal/) |
| Full detector catalog | [`examples/detection/catalog/`](examples/detection/catalog/) |
| Analysis plugin | [`examples/analysis/plugins/session_event_count.py`](examples/analysis/plugins/session_event_count.py) |
| Batch tasks | [`examples/tasks/demo_tasks.yaml`](examples/tasks/demo_tasks.yaml) |

```bash
make examples-check   # hard gate: schemas, imports, rule↔detector links
```

## Development

```bash
make install
make lint            # ruff, mypy, fluent + typing policy scripts
make test            # pytest (daemon-free)
make examples-check  # examples/ contract
make test-cov        # pytest + coverage report
make ci              # lint + schema-check + hud-check + examples-check + test
# GitHub Actions splits Python lint/test from HUD Rust (Linux/macOS/Windows)
```
