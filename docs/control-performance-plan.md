# Control owner performance plan

This document is the diagnosis of what `groket serve` does today, what is
native Rust versus Python, how the four clients attach, freshly measured
trees on this machine (2026-08-17), a live-owner CPU trace (2026-08-16),
and a sequenced plan whose success is a **stable performance contract** —
not another round of one-off speed patches.

JSON-RPC 2.0 on the per-user Unix socket stays. A full rewrite of
`parse_timeline` in Rust is deferred.

---

## 1. Owner work (current call paths)

One process owns `$XDG_RUNTIME_DIR/groket/control.sock` (or
`~/.groket/run/control.sock`). Built by
`groket.integrations.daemon.build_control_server` →
`ControlServer` in `groket/integrations/control.py`. Domain work goes
through `LocalSessionAccess` in `groket/session/access.py`.

Disk-heavy methods (`session/list`, `session/overview`, `session/timeline`,
`session/render`, and `_access_call` for get/turns/usage/diff)
share `HEAVY_IO_CONCURRENCY = 4` (`asyncio.Semaphore`) and run in
`asyncio.to_thread`. Per-session `parse_timeline` and
`build_session_overview` are also **single-flight** (one in-process parse
per path; waiters join).

### 1.1 Catalog (`session/list`)

```
ControlServer._rpc_session_list
  → LocalSessionAccess.list_sessions
  → SessionCatalogCache.list_for_rpc
```

`list_for_rpc` (`groket/session/catalog.py`) **never waits** for a cold
full-tree scan. It kicks `_kick_rebuild` on a daemon thread and returns
the current snapshot, possibly empty, with `incomplete` / `building` set.
Warm polls with matching `sinceRevision` return no rows (`unchanged`).

Rebuild:

```
list_session_catalog
  → catalog_scan_roots / session_scan_roots
  → collect_session_dirs
       host: collect_host_session_dirs  (shallow: top-level or one level
             under percent-encoded cwd buckets; no workspace descent)
       work: parser.find_sessions       (walk via groket.scan / groket._scan)
  → session_catalog_row × N (thread pool, max 4)
       today: load_session_meta_list for every origin
```

`load_session_meta_list` (`groket/parser.py`) reads `summary.json`,
`signals.json`, one pass of `events.jsonl` for turn status
(`_list_runtime_status`), and may **tail 64 KiB of `updates.jsonl`**
(`_last_session_update_type`) when there is no `turn_ended`. If
`summary.json` has no event count it calls `_list_timeline_event_count` →
**full `parse_timeline`**. That last path is rare when summary is honest,
but it is a landmine.

Serve start (`serve_control_forever`) starts `_catalog_warm_loop`:
`cache.get(force=True)` immediately, then every
`CATALOG_WARM_INTERVAL` (15 s) a cheap `get()` + `drop_subagent_rows`.
Fingerprint is root `stat` mtime, not `iterdir` of every child. In-place
writes inside a session do not rebuild the tree; they go through
filesystem watch → `refresh_rows`.

### 1.2 Session open (`session/get` / `session/overview` / `session/timeline`)

| Method | Function | Reads |
|--------|----------|--------|
| `session/get` | `build_session_get` | `load_session_meta(..., include_timeline_count=False)` — **no** `parse_timeline`. Notes revision optional. |
| `session/overview` | `build_session_overview` → `_build_session_overview_uncached` | **Always `parse_timeline`**, then `segment_timeline_turns`, subagent runs, notes. Timeline `events` is empty (`lazy: true`). |
| `session/timeline` | `build_session_timeline` | **Always `parse_timeline`**, then filter/page. Default 300 events / 4000 content characters (caps 2000 / 50_000). |
| `session/turns` | `build_session_turns` | Another parse (or cache hit) + full turn mapping (long assistant previews). |

`parse_timeline` (`groket/parser.py`):

1. Stamp-keyed cache (`BoundedCache`, cap 32, env `GROKET_TIMELINE_CACHE_MAX`).
2. Incremental `_scan_updates_jsonl` when the file only grew.
3. Per line: `keep_updates_line` (native or Python) skips non-terminal
   `tool_call_update` **before** JSON parse.
4. Kept lines: `orjson.loads`.
5. Merge `events.jsonl` runtime markers, coalesce streaming tool rows,
   order, optional fork-parent merge.

**First paint today still waits on a full parse.** The desktop HUD
(`Hud::activate_session` / `load_session_ref` in `desktop/src/app.rs`)
sends `session/overview` and shows a loading placeholder until
`OverviewLoaded`. Only then does it fetch timeline page 0
(`TIMELINE_CHUNK = 80`, `TIMELINE_PREVIEW_CHARS = 720` in
`desktop/src/live.rs`).

The terminal browser (`BrowserScreen._load_control_first_page` in
`groket/ui/screens/browser.py`) calls **overview first**, then one
`session/timeline` page (`TIMELINE_RPC_LIMIT = 200`,
`TIMELINE_RPC_CHARS = 12_000` in `groket/session/wire_timeline.py`), then
paints (`_commit_loaded_session`), then remainder pages. Offline
(`--no-socket`) calls `parse_timeline` in-process before any paint.

So “paged timeline” only shrinks the **reply**. The owner still walks the
whole `updates.jsonl` to know total and to assign turn indexes.

### 1.3 Live notify

`TraceTreeWatch` (`groket/fs_watch.py`) on the same roots as the catalog.
Coalesced path list → `apply_fs_catalog_events` → `refresh_rows` for dirty
sessions, then:

| Notification | When |
|--------------|------|
| `session/changed` | Session files or catalog rebuild (`sessionId` empty when the whole list is new) |
| `notes/changed` | `operator_notes.toml` |
| `session/selected` | After `session/open` |

Broadcast is JSON-RPC **notifications** (no `id`). The owner adds a
writer to the broadcast set only **after** the current request finishes,
so a one-shot HUD `session/timeline` does not eat a notify as its result.

Clients: HUD keeps a persistent Unix stream (`desktop/src/control.rs`
`RPC_STREAM`) and wakes iced on notify. Emacs `jsonrpc.el` and Neovim
`vim.uv` keep a long-lived connection. Python `ControlClient.request`
usually opens a **new** connection per call (does not hear notifies
unless `connect()` is used). The terminal live path is
`session/overview` + growth fetch on a worker, plus filesystem watch
when attached.

HUD `plan_tick` (`desktop/src/live.rs`) treats `session/changed` as
`fetch_list` and, when that `sessionId` is the open overview, also
`load_overview`. Any catalog row in a live status (`running`, …) sets
`any_live` and a 3 s poll (`LIVE_POLL_MS`) that repeats list + overview
+ timeline tail. Four host sessions appending therefore rebuild
overview on every coalesced write. See §4.4 and moment I.

### 1.4 Overview extras

`session/overview` carries notes.

---

## 2. Native Rust versus Python

### 2.1 Owner-side native (`groket._scan`, crate `scan/`)

Shipped as the wheel extension (`pyproject.toml` `target = "groket._scan"`).
`GROKET_SCAN=0` forces the Python twin in `groket/scan.py`. This process
loaded the extension (`using_scan True`).

| Symbol | Role | Not |
|--------|------|-----|
| `find_sessions` | Directory walk; stop at first session dir; skip `workspace`, `subagents`, resume-seed, `.git`, … | Does not load meta |
| `looks_like_session_dir` | `updates.jsonl` / `summary.json` / nonempty `events.jsonl` | |
| `keep_updates_line` | Byte needles: skip non-terminal `tool_call_update` | Does not parse JSON |
| `filter_updates` | Split buffer, keep those lines | |

Host **catalog** discovery does **not** use this walk. It uses
`collect_host_session_dirs` (pure Python, shallow). Work traces use
`parser.find_sessions` → `groket.scan.find_sessions` → `_scan` plus
Python `_scan_hit_is_listed` (drop resume-seed / subagent).

### 2.2 Still Python (owner)

- `orjson` parse of **kept** `updates.jsonl` lines and all overview/turn
  construction (`parse_timeline`, `segment_timeline_turns`,
  `control_views`).
- Catalog row meta (`load_session_meta_list` / future `load_host_list_meta`).
- JSON-RPC encode: stdlib `json.dumps` in `ControlServer._send` (disk
  parse already uses `orjson`; the socket does not).
- Notes, diff, editor `session/render`.

### 2.3 Client-side Rust (not the owner)

`groket-hud` (`desktop/`) is a Rust client: `serde_json` over the same
JSON-RPC Unix socket. It does not parse `updates.jsonl`.

---

## 3. How the four clients talk

Same protocol (`docs/control.md`): JSON-RPC 2.0, version `1.0.0`, newline
or Language Server Protocol `Content-Length` frames.

| Client | Code | Stream | Open path | List |
|--------|------|--------|-----------|------|
| Terminal | `groket/integrations/control_client.py`; attach in `ui/app.py` | Usually one-shot per request | `BrowserScreen._load_control_first_page`: overview **then** timeline page 200×12k | First page `drain=False`; later pages background (`_fill_remaining_catalog_pages`) |
| Desktop HUD | `desktop/src/control.rs` | Persistent `UnixStream` | `session_overview` then lazy `session_timeline` 80×720 | First `SESSION_LIST_PAGE` (200); `session_list_all` drains in the background |
| Emacs | `integrations/emacs/groket.el` + stock `jsonrpc.el` | Persistent | `session/render` format `org` (full document; **full parse** on the owner) | Optional picker; not required to drain |
| Neovim | `integrations/vim/lua/groket/init.lua` + `vim.json` / `vim.uv` | Persistent | `session/render` format `markdown` | Same |

Editors do not need `session/overview`. They still pay a full timeline
parse inside `render_editor_document` (`groket/integrations/editor.py`).

Protocol replacement is out of scope: Emacs `jsonrpc.el` only speaks
JSON. See the earlier protocol note: the shared language of all four is
this envelope.

---

## 4. Measured trees (2026-08-17)

Captured in `{scratch}/session-sizes.txt` from `du` / `find` and a
`uv run` of `find_sessions` / `collect_session_dirs` on this machine.
`performance_goals.md` (2026-08-09) is **not** reused as current truth.

### 4.1 Host `~/.grok/sessions`

| Quantity | Value |
|----------|--------|
| Tree size | **2.8 GiB** |
| Top-level entries | 52 directories |
| Dirs with `summary.json` or `updates.jsonl` | **575** |
| `groket._scan.find_sessions` hits | 576 |
| **Listed** host sessions (`collect_host_session_dirs` / `parser.find_sessions`) | **138** |
| `updates.jsonl` files | 557, **1292.4 MiB** total |
| `events.jsonl` files | 574, **108.5 MiB** total |

Largest immediate children are **percent-encoded cwd buckets**, not
sessions: `icedtea` 877 MiB, `groket` 426 MiB, `coredis` 224 MiB.

Largest session directories (contain `summary.json`):

| MiB | Path (under `~/.grok/sessions`) |
|-----|----------------------------------|
| 358.5 | `…/icedtea/019ffeeb-6c33-78c1-9107-ab0cf050cd29` |
| 264.0 | `…/groket/019ffeeb-1650-7321-a5b4-720381bd3787` |
| 131.0 | `…/penguins_place/01a00702-6c28-7943-8f3e-9860b0c46554` |
| 119.8 | `…/icedtea/01a00738-af72-7e42-94db-fe2177384e84` |
| 95.4 | `…/grok-inside/019ff299-bbb6-7f11-9de2-1b72fe0e1b0c` |

Largest `updates.jsonl`:

| MiB | Session |
|-----|---------|
| **155.1** | icedtea `019ffeeb-6c33-78c1-9107-ab0cf050cd29` |
| 85.9 | groket `019ffeeb-1650-7321-a5b4-720381bd3787` |
| 71.4 | penguins_place `01a00702-6c28-7943-8f3e-9860b0c46554` |
| 61.8 | icedtea `01a00738-af72-7e42-94db-fe2177384e84` |
| 41.8 | az `01a003b3-687c-7622-b925-eeff9e934632` |

Largest `events.jsonl`: 10.9 MiB (same groket session), then 6.3 / 6.2 MiB.

**Which owner path pays for which file**

| File | Catalog list (main today) | `session/get` | `session/overview` / `session/timeline` / `session/render` |
|------|---------------------------|---------------|--------------------------------------------------------------|
| `summary.json` / `signals.json` | every listed row | yes | yes (via meta) |
| `events.jsonl` | `_list_runtime_status` (full file, skip non-marker lines) | `parse_runtime_markers` | merge into timeline |
| `updates.jsonl` | 64 KiB tail sometimes; **full parse** only if no event count | no | **full scan** (`keep_updates_line` then JSON of kept lines) |
| workspace / images under the session | not listed (walk stops) | no | image paths only |

### 4.2 Work `~/.groket/work/runs/traces`

| Quantity | Value |
|----------|--------|
| Tree size | **214 MiB** |
| Dirs with artifacts | 13 (many `.groket-resume-seed`) |
| **Listed** work sessions | **5** |
| Combined catalog (`collect_session_dirs` work+host) | **143** (5 work + 138 host) |
| Largest listed-ish `updates.jsonl` | 11.7 MiB imported algor; resume-seed copies ~5 MiB (not listed) |

Work traces are not the performance problem. Host is.

### 4.3 Fresh timings (warm disk, cold process caches)

`{scratch}/open-timing.txt`.

**Catalog** (this process, no serve cache):

- `list_session_catalog(include_host=True)` first: **1908.8 ms**, 143 rows
- second call: **210.4 ms** (list-runtime cache hits on `events.jsonl`)

**Open, mid session** (28.6 MiB `updates.jsonl`, 5.5 MiB `events.jsonl`, 3006 events, 62 turns):

| Call | Time |
|------|------|
| `load_session_meta_list` | 67.9 ms |
| `load_session_meta(include_timeline_count=False)` | 339.9 ms |
| `build_session_get` | 88.0 ms |
| `parse_timeline` cold | **617.0 ms** |
| `parse_timeline` warm | 0.2 ms |
| `build_session_overview` after parse | 100.8 ms |
| overview warm | 0.2 ms |
| `session/timeline` 80×720 after parse | 13.4 ms |

**Open, large session** (155.1 MiB `updates.jsonl`, 6.2 MiB `events.jsonl`, 9473 events, 131 turns):

| Call | Time |
|------|------|
| `load_session_meta_list` | 71.2 ms |
| `load_session_meta(include_timeline_count=False)` | 364.7 ms |
| `build_session_get` | 118.8 ms |
| `parse_timeline` cold | **1454.8 ms** |
| `parse_timeline` warm | 0.3 ms |
| `build_session_overview` after parse | 135.8 ms |
| `session/timeline` 80×720 after parse | 69.2 ms |

Keep/skip on the 155 MiB file: 15_958 lines, **11_233 kept**, 4_725 skipped,
256.8 ms to only run `keep_updates_line`. JSON of kept lines is the rest of
the 1.45 s. The 12–30 s figure in `control_views.py` comments is a
**cold-disk / concurrent double-build** story; on a warm page cache the
fattest session here is about **1.6 s** of CPU+read before first HUD
paint (overview includes that parse).

Implication: swapping JSON-RPC cannot matter. Unblocking first paint
from `parse_timeline` can take **1.5 s** off the worst click, and
**catalog** is still ~2 s because every host row still reads
`events.jsonl`.

### 4.4 Live owner (2026-08-16)

Same machine, `groket serve` pid 24108 up ~2 h, HUD attached, four host
sessions appending. Quiet 3 s sample was almost idle. Lifetime average
was **49 % of one core**.

| Counter | Value |
|---------|--------|
| `utime+stime` | 3297 s CPU / 6660 s wall (~49 %) |
| `rchar` | **4.88 GiB** userspace reads |
| `read_bytes` | 118 MiB from disk (same files, page cache) |
| `syscr` | 7.1 million |
| RSS | 274 MiB |

Hot files at the sample (still growing):

| Session | `updates.jsonl` | `events.jsonl` |
|---------|-----------------|----------------|
| groket `019ffeeb-1650-…` | 90 MiB | 11.4 MiB |
| penguins_place `01a00702-6c28-…` | 75 MiB | 3.8 MiB |
| icedtea `01a00b24-2afb-…` | 45 MiB | 1.6 MiB |
| icedtea `01a00d2f-5d0d-…` | 0.8 MiB | 89 KiB |

Call path per coalesced write (`CONTROL_FS_DEBOUNCE_S` = 3 s):

```
TraceTreeWatch
  → apply_fs_catalog_events → refresh_rows
       session_catalog_row → load_session_meta_list
         → _list_runtime_status   # full events.jsonl when size/mtime miss
  → notify session/changed {sessionId}
  → HUD plan_tick: fetch_list + load_overview
       build_session_overview     # stamp includes updates.jsonl
         → parse_timeline (incremental bytes) + segment_timeline_turns
```

`sinceRevision` does not save the list here: `refresh_rows` calls
`_bump_locked` on every dirty session, so the catalog revision moves
even when title and counts are unchanged.

Incremental `parse_timeline` already avoids a byte-0 rescan. The heat
is (1) host list re-reading `events.jsonl` from the start, (2)
`session/overview` on every append, (3) HUD 3 s poll while **any** row
is `running`. Moments A–H do not name this. Moment I does.

---

## 5. Baseline already shipped / open

### Shipped on `main`

| Work | What it locked |
|------|----------------|
| `performance_goals.md` | Feel targets; list must not parse `updates.jsonl` (mostly true; tail + rare full parse remain) |
| Pull request 6 | First catalog **page** paints without draining `matched` |
| Pull request 8 | HUD does not clone catalog/timeline on every paint |
| Pull request 27 + `BoundedCache` | Parse caches evict (timeline 32, overview 64, …). Title said “one session”; **code cap is 32** (`groket/constants.py`) |
| `list_for_rpc` | Cold `session/list` returns immediately with `incomplete`/`building` |
| `collect_host_session_dirs` | Host discovery is shallow (fixes the old 163 s junk walk in the 2026-08-09 table) |
| Incremental `parse_timeline` | Growth of an already-open session does not rescan from byte 0 |
| Single-flight parse/overview | HUD open + live poll share one build |

### Open pull request 29

https://github.com/indynull/groket/pull/29 — **host catalog list only**.

Does:

- `load_host_list_meta`: `summary.json` + `signals.json`. No `events.jsonl`,
  no `updates.jsonl` tail, no title infer from the trace.
- Stamp-gated snapshot (`mtime_export.py`) under the host catalog cache dir.
  Stamp is path + mtimes of `summary.json`, `signals.json`, **and**
  `updates.jsonl` (mtime only).
- `groket export-host -o FILE` writes that snapshot without starting serve.
- Opening a session still uses the full parser (stated in the pull request).

Does not:

- Session-open first paint.
- Work/eval rows (`load_session_meta_list` remains).
- Tail-first or indexed `parse_timeline`.
- Changing JSON-RPC.

Landing 29 makes a host list rebuild “143 × two small JSON files”
(plus a walk of 52 buckets). Second list becomes a stamp compare.

---

## 6. Goals (what “stable” means)

Named moments. Continuous integration keeps **structural** tests only
(did we open this file / call this function), never wall-clock on
`~/.grok`.

| Moment | Done when |
|--------|-----------|
| A. First catalog page, serve warm | Keys live immediately; first page of rows is visible |
| B. First catalog page, serve cold | Keys live immediately; page may be empty then fill (`incomplete` / `building`) |
| C. Quiet live poll | No full catalog rebuild; no table rebuild if `sinceRevision` matches |
| D. Click one session: chrome (title, status, context) | Comes from `session/get` (and the catalog row already on screen). Body may still be loading. |
| E. Click one session: first Timeline / Events viewport | Does **not** wait for a full `updates.jsonl` parse. Turns may still be loading. |
| F. Turns list + overview extras | Arrive after E, via `session/overview-ready` when the full parse finishes |
| G. Leave serve up a working day | Resident set plateaus at documented cache caps; no linear climb with catalog size |
| H. Editors `session/render` | Full parse is acceptable for a whole-document projection |
| I. N sessions appending, HUD up | Owner near idle between writes. `rchar` tracks **new** bytes, not file size × poll rate. No `session/overview` and no full `events.jsonl` read on a host append. Catalog revision moves only when a list field changes. |

Stop changing the system for speed when A–G and I hold and the structural
tests exist. A later regression opens a ticket against **a named moment**,
not “make it faster.” H is an accepted full-parse path, not a freeze
blocker.

---

## 7. Approach (in order)

Finish each item before starting the next, or write down why it is deferred.

### Host list

Land pull request 29. Host `session/list` rows are `summary.json` +
`signals.json` plus a stamp snapshot. Work/eval rows stay on
`load_session_meta_list`. Host catalog never reads `events.jsonl` or the
body of `updates.jsonl`. Opening a session still may.

`session_catalog_row` (and therefore `refresh_rows`) must use
`load_host_list_meta` for host origin. A test that grows only
`events.jsonl` on a host session and calls `refresh_rows` must not open
that file.

### Live append (moment I)

Do this immediately after pull request 29. First-paint work (D–F) does
not cool a serve process that rebuilds overview on every write.

One path:

1. **Host live status without `events.jsonl`.** Running chip from
   `_traces_are_fresh` plus the existing 64 KiB `_last_session_update_type`
   tail (`turn_completed` closes the row). Work/eval rows keep
   `_list_runtime_status`.
2. **Honest catalog revision.** `refresh_rows` compares the new wire row
   to the cached one. Call `_bump_locked` only when a list field changed
   (title, status, counts, timestamps). An append that only grows
   `updates.jsonl` leaves `sinceRevision` matching.
3. **Narrow `session/changed`.** Always notify for a dirty session so an
   open timeline can tail, with `sessionId` set. Add
   `listChanged: bool`. HUD: `fetch_list` only when `listChanged` or
   `sessionId` is empty; `refresh_timeline` when that id is open;
   **never** `load_overview` from this notify.
4. **Overview-ready is its own notify.** Moment F uses
   `session/overview-ready` `{sessionId}` after the background parse.
   Do not reuse `session/changed` for “turns are ready.”
5. **HUD poll.** `any_live` may keep a 3 s `session/list` (`sinceRevision`).
   `selected_live` refreshes `session/get` + timeline tail (moment E).
   `load_overview` runs on first open (background) and on
   `session/overview-ready`. Rebuild turns when the incremental scan
   sees a turn boundary (`turn_started` / `turn_ended` in new bytes),
   not on every `tool_call_update` line.

Structural tests (no wall-clock on `~/.grok`):

- `refresh_rows` on a host session whose `events.jsonl` grew does not
  open `events.jsonl`.
- `refresh_rows` on an `updates.jsonl` append that leaves list fields
  unchanged does not bump the catalog revision.
- HUD `plan_tick` given `session/changed` for the open live session
  sets `refresh_timeline` and does not set `load_overview`.
- A synthetic growing `updates.jsonl` of skipped `tool_call_update`
  lines does not rebuild the overview cache until a turn marker lands.

Laptop check (same counters as §4.4): 60 s with 3–4 live fat sessions
and the HUD visible. Done when owner CPU is a few percent and `rchar`
is in the same order as bytes actually appended.

### Catalog contract

Write moments A–C and I into `performance_goals.md` (replace the
2026-08-09 walk table with the §4 counts). Keep: `list_for_rpc` does
not join a cold scan; terminal first fetch is `drain=False`; HUD first
fetch is one page; serve stays up when the terminal quits. No further
catalog speed work unless a test shows `list_session_catalog` or
`refresh_rows` opening `updates.jsonl` / host `events.jsonl` again.

### Session chrome without overview

HUD and terminal first paint of an open session use `session/get`.
`session/overview` runs in the background. Lock with tests:
`build_session_get` does not call `parse_timeline`; clients do not call
`session/overview` before painting chrome.

### First Timeline viewport without a full parse

`session/timeline` with a small `limit` (HUD tail of ~80) answers from an
**append-only line index** or a **tail scan** of `updates.jsonl`, without
building the full `list[TraceEvent]`.

One path:

1. Sidecar index next to the session (byte offset + kept-line flag +
   coarse type), rebuilt incrementally like today’s scan state.
2. `aroundIndex` / live tail = last N kept terminal events.
3. Full `parse_timeline` stays the implementation for overview, turns,
   render, search, and `atIndex` until the next step.

Reuse `keep_updates_line` (already native) to skip bodies while seeking.
Do not rewrite the parser in Rust here. If a persistent index is not
ready, ship tail-only page 0 (read backward until N keepers) as that
one path.

### Overview / Turns as the second wave

Full `parse_timeline` + `segment_timeline_turns` run after first paint
(or join an in-flight parse). When done: fill the existing caches, notify
`session/overview-ready` (moment I step 4), hydrate HUD Turns and
terminal remainder pages. Pre-warm the last-opened session and the HUD
spotlight list on the warm loop so a typical click is a cache hit.
Pre-warm counts against the parse-cache cap and must not call
`session/overview` on a timer.

### Memory and live stability

Document the resident-set contract. Today’s cap is 32 timelines, not 1.
Keep 32 if a working day of browsing stays flat; drop to 2 (live + fork
parent) if resident set climbs on this host tree. Measure `VmRSS` from
`/proc/<serve-pid>/status` the way `performance_goals.md` already says
(not `ru_maxrss`).

### Analysis and editors off the click path

Editors keep `session/render` as a full-parse document. The click path
is get + first timeline page only.

### Freeze

Update `performance_goals.md` “Already done / Remaining” to match A–I.
Keep structural tests: host list and `refresh_rows` do not parse
timeline or read host `events.jsonl`; `session/get` does not parse
timeline; a synthetic fat `updates.jsonl` of skipped `tool_call_update`
lines plus keepers at the end is not JSON-parsed for those skipped
bodies; HUD live tick does not request `session/overview`.

No further performance pull requests unless a named moment regresses.

---

## 8. How to re-measure

Laptop only. Do not copy `~/.grok/sessions` into continuous integration.

```text
# Tree (same as {scratch}/session-sizes.txt)
du -sh ~/.grok/sessions ~/.groket/work/runs/traces
find ~/.grok/sessions -name updates.jsonl -printf '%s\t%p\n' | sort -nr | head

# Listed count
uv run python -c "from groket.paths import default_work_dir; from groket.session.sources import collect_session_dirs, session_scan_roots; print(len(list(collect_session_dirs(session_scan_roots(default_work_dir(), include_host=True)))))"

# Catalog
# time list_session_catalog(work, include_host=True)  # cold process + second

# Open (pick the current largest updates.jsonl)
# time build_session_get / parse_timeline / build_session_overview /
#      build_session_timeline(limit=80, content_chars=720)

# Serve RSS
# grep VmRSS /proc/$(pgrep -f 'groket serve')/status

# Live append (moment I) — 60 s, HUD visible, 3–4 sessions writing
# awk '/rchar|syscr/' /proc/<serve-pid>/io
# awk '{print $14+$15}' /proc/<serve-pid>/stat   # ticks; 100/s = one core
```

Prefer “did we open this file” tests in `tests/session/test_catalog_perf.py`
and friends.

---

## 9. Deferred (explicit)

- Replacing JSON-RPC 2.0 or the Unix socket.
- Porting `parse_timeline` / `TraceEvent` / turn segmentation to Rust.
- MessagePack, Cap’n Proto, gRPC, D-Bus.
- Virtualizing every terminal Report/Summary pane.
- Wall-clock continuous integration on live trees.
- Making Emacs/Neovim drain the catalog or send `sinceRevision`.

Native work that **is** in scope later, only if first Timeline page
cannot be answered from a Python tail scan: extend `groket._scan` with a
**line-offset index** builder (same crate, same keep/skip needles). That
is an increment of the existing leaf, not a second parser.

---

## 10. What not to do next

Do not open another “make catalog faster” pull request that still reads
`events.jsonl` for host rows — that is pull request 29. Do not add a
second encode path. Do not put `parse_timeline` on `session/get`. Do not
make overview embed event rows again. Do not raise
`TIMELINE_CACHE_MAXSIZE` to hide a leak. Do not call `session/overview`
from the HUD 3 s poll or from `session/changed`. Do not reuse
`session/changed` for “turns are ready.”
