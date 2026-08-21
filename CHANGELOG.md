# Changelog

Notable product state for groket. One first-release section until 0.1.0
is tagged.

## Unreleased

### Feature

- groket no longer ships an analyzer or a rules engine. The
  ``groket analyzer`` command, plugin pipeline, detectors, rules YAML,
  and the Rules screen are gone. Session export writes the trace, notes,
  and flags. Leftover analysis-cache export units and unused findings
  copy are gone too.

### Chore

- Desktop palette uses icedtea 0.13. Stats table passes a scroll id so
  clip jumps stay on the body.

### Feature

- HUD notes form uses a pick list for one-of schema fields (severity)
  and filter chips for many-select. Tab / Shift+Tab walk the text
  fields while composing; Ctrl+Tab or Ctrl+1–6 still change panes.
- HUD uses icedtea 0.13.0: search, badges, tabs, selectable bodies,
  Diff hunks, an F12 Look drawer, and `virtual_clip` pixel scroll on
  Turns, Timeline, Overview lists, Stats, and the session picker.
  Keyboard jumps use `scroll_to` on the named clip. A pixel wheel
  redraws in the clip; layout runs when the mounted range changes.
- HUD Turns and Timeline closed rows use the same title-plus-badge
  tile as Recent.
- HUD growing lists scroll on icedtea `virtual_column` / `data_table`:
  Recent, closed Turns, closed Timeline, Overview Tasks / Workflows /
  Subagents, Overview Stats, Findings, Notes cards, and workflow-event
  agent children. One-document panes stay `themed_scroll`: open
  Timeline event body (Asked / Happened), Diff hunk, Overview Session.
- Workflow inspect uses the same facts on TUI and HUD: Asked,
  Happened, Failed, and an Agents list. Child status is `complete`,
  `failed`, `cancelled`, or `running`. A child without a session
  directory is dim and does not open. Overview glance children include
  the session path when it exists.
- `session/overview` includes event-type and tool counts. HUD and TUI
  Stats read those fields for the whole session.
- TUI Session glance puts status, model, Host or Eval, and duration on
  one badge row. Last-turn says `complete`.
- HUD loading uses the same spinner overlay for the catalog, an opening
  session, Timeline, and Stats.
- Serve watches membership directories and four session files with
  watchfiles. Workspace is not subscribed. An open session tails new
  ``updates.jsonl`` bytes. Catalog warms once at start. The HUD list
  follows socket notifications.
- Session Overview and Summary share Session, Tasks, Workflows,
  Subagents, and Stats tabs (click the strip). Session is the glance.
  Tasks is shells, monitors, and schedules. Timeline filter Background
  / Workflows. Summary and HUD rows jump to that bookend. Enter on a
  job bookend shows the host ``terminal/`` log (up to 50,000
  characters). A workflow child or subagent opens that session.
- ``groket analyzer`` attaches to ``groket serve`` for the session
  catalog and overview, then runs plugins in its own process. Those
  reads use the same 45-second budget as the terminal app. ``/``
  searches the session list. An open session shows plugin report
  panes (Filter, split markdown, Issue box). ``E`` exports the session
  bundle (trace, notes, cached plugin reports). Review packs include
  overview workflow, background-job, and child-session rows. The
  session browser and desktop palette have no Findings pane. Report
  keeps flags and notes.
  ``analysis/run``, ``analysis/status``, ``analysis/changed``, and
  ``session/findings`` are gone. Overview no longer embeds findings.
  Notes are the operator attach on a session.
  Session eval and the HUD drop leftover analysis settings,
  auto-analyze, timeline finding marks, Jobs analysis counts, and
  ``analysis/changed`` notices. ``E`` export stays.
- Session `?` help lists eval keys (including export). Plugin keys live
  on `groket analyzer` help.
- Failed workflows and background jobs become Findings with
  paste-ready What/Where/Why/Should extras.
- Timeline Filter and Turn stack. Flags paint on the row.
  Live append keeps the filter. HUD turn pick keeps Filter and search.
- Clicking a Turns card focuses that turn. Overview footer stays on
  one row.
- Host catalog list uses a stamp-gated snapshot. ``groket export-host``
  writes that snapshot.
- Control methods and notifications generate from
  ``control_contract.py`` (``just schema``).
- Diff lists rewind snapshots, Prompt/Assistant tabs, and a files/hunk
  split on both surfaces. ``/`` finds path or hunk text.
- Live Timeline has a Tail switch. Opening an event asks for the
  50,000-character body, including the paired tool result.
- Session walk uses ``groket._scan``. ``GROKET_SCAN=0`` uses the
  Python body.
- ``examples/keys`` ships with the other reference packs.

### Bug fix

- The session list summary is the session count. It does not say
  pending analysis or findings totals.
- Summary and HUD Tasks open a schedule the same way as a job: Enter
  or a second click jumps to the Timeline bookend. A row with no
  bookend is dim and stays put.
- Summary tables keep their size when focused, so a click lands on the
  row under the pointer. Click highlights; Enter or a second click
  opens, same as the session list and Timeline.
- Report clears loading when analysis finishes on an open session.
- Jobs Clear resets logs, activity, and buffers. The banner reads
  analysis cache and inflight.
- Marketplace list previews use ``server · method``. Paths keep
  underscores and hyphens. Light themes keep type and tool faces
  readable.
- Control serve applies catalog watch on the serve loop, with disk
  work off that loop, so a live write does not stall RPC.
- Filesystem watch does not subscribe ``workspace/``, ``images/``, or
  ``compaction/`` trees, and ignores read open/close so a catalog apply
  does not retrigger itself. The list stays still when only
  ``events.jsonl`` or ``workspace/`` files grow.
- A catalog ``search_tool`` query is a search, not proof an MCP
  server was available.
- Analysis-failed notices use the analysis job. Stale analysis is a
  warning toast and a Report/Findings note.
- An older ``groket serve`` missing a method asks the operator to
  restart serve.
- ``groket config validate`` rejects missing or invalid TOML.
- A monitor log inspect reads only the tail of a large file.

### Chore

- Removing a control method or handshake field without a protocol
  major bump fails the contract inventory check.
- Continuous integration uploads Python and HUD coverage to Codecov.
- Platform wheels and the source distribution build on ``main``,
  tags, and workflow dispatch.

## 0.1.0

First release. Groket evaluates Grok Build sessions: timeline, findings,
workspace diffs, Docker evals, personas, and pluggable detectors /
analysis plugins.

### Install

- `uv tool install --editable .` builds `groket` and `groket-hud` (needs Rust).
- `uv tool install git+https://github.com/indynull/groket` installs from git.
- `uv tool install groket` is the package name on the Python package index.
- `groket --version` (`-V`) prints the product version (`0.1.0`).
- The same version appears on the terminal `?` heading, the desktop
  palette window and `?` sheet, and `groket-hud --version`.
- One product version across the Python package, `groket-hud`, and
  `groket-scan`.
- Pushes to `main`, version tags, and workflow dispatch build Linux,
  macOS, and Windows wheels plus a source distribution.
- A version tag or manual workflow dispatch uploads those files to
  TestPyPI.

### Paths and config

- Config home is `~/.groket` (`config.toml`, personas, detectors, rules,
  plugins, optional `keys.toml`).
- Work root is `~/.groket/work` (`runs/traces/`, recipes, Docker
  contexts, batch results).
- `~/.groket/config.toml` is the only prefs file (terminal app and
  desktop HUD). Schema at the published config schema URL.
- Optional `~/.groket/keys.toml` remaps chords (`groket keys`).

### Sessions

- Eval sessions are Docker launches under `work/runs/traces`.
- Host sessions are native Grok trees at `~/.grok/sessions` (`H` shows
  or hides them).
- Subagent runs stay off the top list; open them from the parent.
- Follow-up (`n`) and Done (`e`) apply while a session is awaiting.
- Fork (`f`) continues an ended session as a new Docker launch.
- Re-run (`R`) launches again from the same recipe fields.

### Terminal app

- `groket` / `groket tui` is the full eval client: session list,
  browser, runner, recipes, personas, analysis, and export.
- Browser panes are Timeline, Summary, Diff, Findings, and Report.
- `y` copies the selection, the finding, or the pane body.
- `E` writes a session bundle under `~/.groket/reports/`.
- Runner launches Docker evals from a recipe (Ctrl+Enter).

### Desktop HUD

- `groket hud` is the summonable session palette (Overview, Turns,
  Timeline, Diff, Findings, Notes).
- It runs `groket-hud` from `GROKET_HUD_BIN` or `PATH`; `--rebuild`
  cargo-builds this checkout.
- Default hotkey is Cmd+Shift+G (macOS) / Ctrl+Shift+G (Windows and
  X11). On Wayland bind `groket hud --toggle`.
- `--install-desktop` writes user-local icons and a launcher named
  groket.

### Control

- `groket serve` owns the per-user Unix socket. The four clients
  attach: terminal app, desktop HUD, Emacs, and Neovim.
- Bare `groket` and `groket hud` detach-start serve when the socket is
  free. Quitting a client leaves serve running.
- `protocolVersion` is semver (`1.0.0`), independent of the product
  version. Same major keeps a live owner; a major bump is the only
  incompatible handshake change.
- Emacs opens sessions as Org; Neovim opens them as Markdown.

### Batch, rules, and examples

- `groket batch` runs headless Docker from task YAML
  (`examples/tasks/`).
- `groket rules validate` checks detection rules and composites.
- `groket gen` scaffolds detectors, rules, plugins, and task lists
  under `~/.groket/`.
- Supported packs live in `examples/` (not auto-loaded).

### Development

- `just` is the public development verb (`just lint`, `just test`,
  `just ci`).
- `just bump 0.1.1` sets every product version declaration and
  promotes this file.
- `groket doctor` checks the host (Docker, Grok auth, paths).
