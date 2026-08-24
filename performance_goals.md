# Groket performance goals

Groket is a keyboard-first monitor for Grok Build sessions: a home list of
sessions, live status, and inspection of **one** open session (timeline,
overview, notes, report). It should feel light to open and to leave
running. Heavy work belongs on `groket serve` and on the one session the
operator clicked.

This file is the current diagnosis and the remaining cuts. Numbers below
were measured on this laptop against the live `~/.groket` / `~/.grok`
trees (2026-08-09), not a copy of those trees in continuous integration.

## Target

| Moment | Feel |
|--------|------|
| Launch with serve already warm | Window/list usable in a couple of seconds, keyboard live immediately |
| Launch when serve is cold | List may fill late, but keys still work; no multi-minute freeze |
| Quiet live poll | No full catalog rescan or full table rebuild |
| Open one session | Only that session's overview/timeline is parsed |

Host `~/.grok/sessions` stays on the home list. `is:host` filters it.

## What `session/list` returns

Each catalog row is a small JSON object: id, path, title, model, status,
duration, context meter, tool/error counts, timestamps. Enough to paint
the home table and the HUD list.

`load_session_meta_list` reads `summary.json`, `signals.json`, and turn
markers in `events.jsonl`. It does **not** parse `updates.jsonl` or send
timeline events.

Timeline is a second path: open one session → `session/overview` + paged
`session/timeline`. Sibling sessions stay as those cheap rows.

## Why launch is still slow

The list payload is cheap. Launch pays for **discovery** and **process
start**, not for shipping timelines.

On this machine (host sessions loaded):

| Step | Time | Notes |
|------|------|--------|
| First `import` of `TraceEvalApp` | ~19 s | Later runs ~2–4 s once Python's cache is warm. Textual + `groket.ui`. |
| Top-level `~/.grok/sessions` | 62 dirs | Almost no `events.jsonl` at the top level |
| Cold `list_session_catalog` (host on) | **163 s, 696 sessions** | |
| Same scan again | 23 s | |
| `SessionCatalogCache.get()` hit | 0.001 s | |

Top-level host names include encoded cwd paths (`%2Fvar%2Ffolders%2F…`,
`workspace`, …). They are not tidy one-session folders.
`find_sessions` / `collect_session_dirs` walk those trees until they see
`summary.json` or `updates.jsonl`. That walk found **696** session dirs
and dominated the 163 s. Comment in `parser.find_sessions` already
calls descending into session workspaces the old dominant cost; here
the walker still spends minutes on junk/nested trees *before* a dir
qualifies as a session.

Launch then stacks:

1. Import Textual + groket UI (~2–19 s).
2. Start or attach to `groket serve`.
3. If serve's catalog cache is cold, the first `session/list` **waits on
   that 20–160 s walk**.
4. TUI drains all matched rows once and paints the table on the UI thread.

After serve is warm, a later `session/list` is a cache hit. Quit and
reopen while serve stays up: catalog I/O should not be the delay — import
+ attach + first paint should. Kill serve every time: pay the walk again.

## Already done

- List rows do not call `parse_timeline` / do not read `updates.jsonl`.
- Serve catalog cache: no forced full rebuild every 15 s; fingerprint is
  root `stat` (not `iterdir` of every child).
- Quiet TUI/HUD polls send `sinceRevision`; unchanged owner → no row
  transfer, no table rebuild. Revision is owner-scoped (restart is a gap,
  full snapshot).
- First TUI attach paints one `session/list` page (`drain=False`) and
  does not drain `matched` on that first paint. Quiet poll after a serve
  restart still drains. HUD first fetch is one page; remaining rows
  fill in the background.
- Timeline inspect is paged (200 events, 12 k chars); TUI control timeout
  is 45 s; timeout is a toast, not a worker crash.
- Catalog I/O for the home list runs on `@work(thread=True)`, not the
  Textual message pump. First table paint is the first page only.
- Parse caches are bounded (`groket/bounded_cache.py`). Entries are keyed
  per session, so an owner left open over a large bucket used to pin every
  session it ever parsed: measured over a 554-session bucket, 200 sessions
  cost +132 MB and were still climbing linearly. Bounded, the same 200 cost
  +49 MB and 400 plateau at +71 MB. Recency updates on read and on write,
  so the sessions a live refresh keeps touching stay resident.

## Leaving it running

`groket serve` and the TUI are meant to sit open for a working day. What
bounds memory in that state:

| Cache | Cap | Entry weight |
|-------|-----|--------------|
| `_timeline_cache` | 32 | Finalized timeline + incremental scan state (two event lists) |
| `_turn_view_cache` | 32 | Rendered turn segments |
| `_overview_cache` | 64 | Overview payload |
| `_runtime_markers_cache` | 256 | Marker events |
| `_system_prompt_cache` | 128 | One string |
| `_list_runtime_cache` | 2048 | Scalars; sized to cover a whole bucket |

Raise the heaviest one with `GROKET_TIMELINE_CACHE_MAX` when an operator
browses far more than 32 sessions at a time and has the memory to spare.
The floor is 2 so a live session and the fork parent it merges always fit.
In-flight parses (`_timeline_inflight`, `_overview_inflight`) are still
plain maps: a stampede of new sessions can hold every result until those
jobs finish, then the cap applies.

Measure it the way the rest of this file is measured — current `VmRSS`
from `/proc/self/status`, not `ru_maxrss`, which is a high-water mark and
never falls.

## Remaining goals

1. **Session discovery.** Stop walking encoded-cwd / workspace junk under
   `~/.grok/sessions`. A host session should be a shallow child (or a
   known layout), not “any `summary.json` anywhere under the root.”
   Measure: cold `list_session_catalog` with host on stays near “number
   of real session dirs × cheap meta,” not minutes.
2. **Cold serve must not block first paint.** Serve can warm the catalog
   in the background; `session/list` should return the warm cache (or an
   honest empty/partial page) instead of stalling the client on a 160 s
   walk.
3. **First table paint.** First attach no longer drains `matched` before
   the home table is shown. Remaining work is virtualizing a large local
   filter set, not a second drain on first paint.
4. **Import / mount.** Remaining cost is Textual itself.
5. **Keep serve up.** Document and default: TUI/HUD attach to a long-lived
   `groket serve`; quitting the TUI does not stop the owner (already
   true). Launch advice should not imply restarting serve each time.

## How to measure

Prefer “did we walk this tree / parse this file” over flaky wall-clock in
CI. Synthetic catalogs of hundreds of **tiny** session dirs stay in
`tests/session/test_catalog_perf.py` and friends.

On a real laptop, time:

```text
import TraceEvalApp
list_session_catalog(work, include_host=True)   # cold and second
SessionCatalogCache.get()                       # force vs warm
session/list  (warm owner vs just-started owner)
```

Do not copy `~/.grok/sessions` into CI.

## Non-goals

- Requiring emacs/vim to drain the full catalog or send `sinceRevision`.
- Virtualizing every browser pane (Summary/Report).
- Timing a multi-GB host tree in continuous integration.
