# Changelog

Notable product state for groket. One first-release section until 0.1.0
is tagged.

## Unreleased

- Timeline parses ``scheduled_task_*`` bookends and keeps structured
  ``task_backgrounded`` / ``task_completed`` fields (id, command, cwd,
  log path, schedule, output excerpt) instead of a stuffed content line.
- Session Overview lists background shells, monitors, and durable
  schedules on TUI Summary. HUD Overview shows glance counts and named
  rows that jump Timeline to that bookend. Timeline filter Background
  is the inspect path. ``J`` is still Docker / serve logs.
- Failed workflow runs and failed background jobs become Findings with
  paste-ready What/Where/Why/Should extras. Inspect labels Asked,
  Happened, and Failed. Activating a workflow child opens that session.
- Review runtime no longer treats a ``search_tool`` query as proof an
  MCP server was available. A catalog search for ``gitlab …`` plus
  ``glab`` is not unused-MCP.
- Host catalog list uses a stamp-gated snapshot (summary, signals, and
  updates-tail status). ``groket export-host -o FILE`` writes that
  snapshot without starting serve.
- Live host appends no longer rebuild catalog revision or session
  overview. `session/changed` carries `listChanged`; the HUD tails the
  open timeline instead of calling `session/overview`. Host list rows
  use `summary.json`, `signals.json`, and the updates tail for status.
- HUD search uses icedtea `search_input`. Diff Prompt/Assistant are
  inner tabs (same bar as the pane strip). Diff hunks pass
  `highlighted_code` wrap `false`. Pane tabs freeze with
  `Tabs::with_disabled` until a session is loaded.
- HUD default density is Default (gap 8, inset 12). Status tags use
  the small badge face; actions stay filled chips. Compact remains in
  the F12 Look drawer.
- HUD footer is keys and transient notices. Session id and run state
  stay on the session chrome.
- HUD assistant, user chat, Diff Prompt/Assistant, and markdown
  findings paint through icedtea `markdown_view`. Tool bodies stay
  code or plain.
- HUD context Copy snapshots the live editor range. Copy path is the
  session folder, on the session list and Overview only.
- HUD pins icedtea 0.11 from the crate index (full-row tree selection,
  drag-select outside the pane, badge shape family, expander title trail).
- HUD badges, pane tabs, Diff hunks, help, and the Turns Diff chip use
  icedtea constructors (`badge`, `tab_bar`, `highlighted_code`,
  `cheatsheet`, `Glyph::Bytes`).
- HUD type scale is 100% (Material body). F12 Look type 110% is iced 16.
- F12 opens a hidden Look drawer with the icedtea gallery knobs
  (density, type, shape, elevation). Esc closes it. Not persisted.
- Timeline and Turns card previews use body type. Pane tabs use
  icedtea `tab_bar` meta (caption step).
- The `examples/keys` pack is listed with the other reference packs.
  Colemak notes that `y` copy, `/` search, and `h`/`l` plus Left/Right
  turns stay on catalog defaults.
- Desktop analysis-failed notices use the analysis job state. A failed
  job is not treated as a session `cancelled` label.
- HUD Diff hunks are selectable (drag or `y`). Search marks the matching
  line with ``> `` and scrolls the hunk pane to it.
- Session analysis wait uses the toolkit LoadingIndicator in the
  browser chrome (visible on Timeline) and overlays Findings and
  Report. It is not hidden on a pane the operator has not opened.
- HUD badges lift status ink off the wash so complete / running /
  cancelled stay readable. Session context, event index, finding
  severity, and note counts use the same chips. ``completed`` displays
  as ``complete``.
- HUD Timeline event detail steps with Previous on the start edge and
  Next on the end. Left and Right stay previous / next turn.
- Stale analysis is a warning toast plus a short Report/Findings note
  (plain copy, yellow style). There is no full-width banner, and Rich
  tags no longer appear as literal ``[bold yellow]`` in Report.
- HUD event type, tool name, turn, time, counts, findings, and notes
  chrome use the same small badges as session status. Open tool detail
  uses a tool-name badge (not colored prose).
- HUD prompts, assistant replies, tool input/output, Diff Prompt/Assistant,
  Overview summary, findings, and notes are selectable. `y` copies the
  selection or the open body, up to the 50,000-character open-event ceiling.
- HUD Timeline event types and tool names use the same small badges as
  session status (color by type family / tool family). Overview, Recent
  session cards, and the browse bar share one status, model, origin, and
  duration row. Opening a session copies origin and duration back onto
  the Recent card.
- The control JSON-RPC contract (version, methods, notifications) lives
  in `groket/integrations/control_contract.py`. `docs/control.md` and
  `schemas/control.schema.json` are generated from that inventory
  (`just schema`).
- Terminal session open paints Timeline first; Summary and Report fill
  when those panes are opened. Attached control loads the first event
  page, then appends the rest. Timeline search applies after a short
  idle. Live control refresh fetches only new events.
- A Tail switch on a live Timeline follows the last event; off leaves
  the highlight still. The word Tail toggles it. The HUD jumps to the
  last event page so a large session is not stuck on the first window.
- Session waits use the toolkit loading readout (Textual LoadingIndicator
  and widget loading; HUD indeterminate progress) instead of a lone
  sentence.
- Diff file lists use the toolkit tree (Textual Tree; HUD tree_view),
  not an indented path dump.
- Opening a Timeline event (terminal and HUD) asks for the owner’s
  50,000-character ceiling, including the paired tool result.
- HUD palette show is a 220 ms ease-out, hide a 180 ms ease-in; tab
  changes fade; opening and closing a session or event push and pop;
  expanders animate height. Motion ticks at display refresh.
- Overlay summon fades the card in (clear window fill + short rise).
- HUD launch stays on Recent; a catalog refresh leaves the list unpicked.
- HUD `?` and the terminal browser footer follow the current pane: Enter
  and list motion on Overview, Turns, and Timeline; turn step on Timeline.
  The HUD footer is keys only. Session running/complete labels are
  badges.
- HUD `?` lists Left and Right next to h and l for Timeline turn step.
- HUD dropdowns (Snapshot, Timeline turn, Filter) use 12px type and
  tighter padding. Diff Prompt/Assistant tabs are compact in-pane
  buttons. Overlay type is 12px for reading, 14px for card titles,
  16px only for the Overview session name. Markdown headings follow
  that scale.
- HUD Turns cards have a Diff chip when that turn has a snapshot.
- An older `groket serve` that lacks a method shows
  `control owner is older · run: groket serve restart` (terminal and HUD).
  The raw error goes to the log.
- Terminal Diff lists rewind snapshots and changed files. Prompt and
  Assistant tabs sit above a files and hunk split; the assistant is
  markdown. Nested paths group as a directory tree. Without rewind
  points it lists approximate `search_replace` edits. `/` fuzzy-finds
  path or hunk text; `h`/`l` step snapshots. The HUD Diff pane uses
  the same layout, with a snapshot dropdown. Switching to the terminal
  Diff tab paints the snapshot already loaded with the session.
- Session walk and `updates.jsonl` keep/skip share one `groket._scan`
  extension (`groket.scan`, setuptools-rust, same install as the HUD
  binary). `GROKET_SCAN=0` uses the Python body. Continuous integration
  runs both.
- Example analysis READMEs point at `.toml` prefs samples.
- `groket config validate` rejects missing or invalid TOML. Load uses defaults when the file is absent or unreadable.
- Continuous integration uploads Python and HUD coverage to Codecov (OIDC).
- README badges: Actions, Codecov, Python 3.13, MIT license.
- Platform wheels and the source distribution build on `main`, tags, and
  workflow dispatch.
- README / HUD dark mark is cream on a transparent field.

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
  `groket-core`.
- Every Actions run builds Linux, macOS, and Windows wheels plus a
  source distribution (artifacts on the run).
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
  Timeline, Findings, Notes).
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
