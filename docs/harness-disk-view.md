# Groket multi-harness disk view — rewrite brief

This document is the sole brief for a later full-parity rewrite: groket
becomes a catalog, timeline, and (where the product allows)
launch/control tool for every listed coding-agent harness. Disk-based view
is the first contract. Launch and mid-session control exist only when the
product exposes an equivalent. Automedon live `parse_line` is contrast only
— it is not the disk adapter.

Observed host trees and field names were taken from a Linux workstation
(2026-08-08) and a Darwin workstation (2026-08-09), plus groket source and
Automedon adapter recordings. Linux lacked Cursor transcripts; Darwin had
them. Schemas drift; adapters must tolerate extra keys.

**Harness ids:** `grok`, `claude`, `codex`, `gemini`, `opencode`, `cursor`,
`aider`, `pi`, `copilot`.

---

## 1. What groket does today

Groket is a Grok Build evaluation product. It discovers session directories,
parses a Grok on-disk dialect into `TraceEvent` / `SessionMeta`, watches
those files live, and launches isolated Grok evals in Docker. Operator
notes, flags, export, HUD,
and the Unix control socket all assume that same Grok directory.

After the rewrite, those outcomes stay. The Grok directory layout becomes
one adapter. Other harness stores implement the same outcomes.

---

## 2. Groket feature → Grok artifact inventory

Every operator-visible capability that reads or writes session or eval disk
state. Cite is the groket module that owns the path.

### 2.1 Catalog discovery

| Outcome | Grok artifact | Module |
|---|---|---|
| Find session dirs | Recurse; treat as session if `updates.jsonl` or `summary.json` present, or non-empty `events.jsonl`. Skip subagents, resume seeds, `*.stage`, `groket-plugins`. | `groket/parser.py` `find_sessions`, `_looks_like_session_dir` |
| Eval root | `<work>/runs/traces` | `groket/session/sources.py` `work_traces_root` |
| Host root | `~/.grok/sessions` (`H` / `show_host_sessions`) | `sources.host_grok_sessions_root`, `catalog.show_host_sessions_from_config` |
| Catalog row | `sessionId`, `path`, `title`, `model`, `status`, `origin` (`work`/`host`), times, context, event count | `session/catalog.py` `session_catalog_row` |
| List meta | Host: `summary.json` + `signals.json` only. Eval: those plus turn markers / gate. | `parser.load_session_meta_list` |

### 2.2 Timeline / events

| Outcome | Grok artifact | Module |
|---|---|---|
| Linear timeline | `updates.jsonl` rows `{method, params, timestamp}` with `params.update.sessionUpdate` | `parser.parse_timeline`, `_consume_updates_line` |
| Runtime markers | `events.jsonl` `type` (`turn_started`, `turn_ended`, …) | `parser.parse_runtime_markers` |
| Event type names | Grok `sessionUpdate` + events `type`; groket-only `system` from `system_prompt.txt` | `groket/event_types.py` |
| Tool coalesce | Streaming `tool_call_update` → one result row per `toolCallId` | `parser._coalesce_tool_result` |
| Live incremental scan | Cache keyed by mtime/size of `updates.jsonl` + `events.jsonl` (not `signals.json`) | `parser.session_timeline_stamp` |

Observed `sessionUpdate` values: `user_message_chunk`, `agent_thought_chunk`,
`agent_message_chunk`, `tool_call`, `tool_call_update`, `plan`,
`task_backgrounded`, `task_completed`, `scheduled_task_created`,
`scheduled_task_updated`, `scheduled_task_fired`, `scheduled_task_deleted`,
`turn_completed`, `subagent_spawned`, `subagent_finished`,
`current_mode_update`, `retry_state`.

`scheduled_task_created` has `task_id`, `prompt`, `human_schedule`,
`next_fire_at`. `scheduled_task_deleted` has `task_id`, `reason`.
`task_backgrounded` has `task_id`, `tool_call_id`, `command`, `cwd`,
`output_file`, `description` (and often `monitor_description`).
`task_completed` has `will_wake` plus `task_snapshot` (`task_id`, command,
cwd, output path, times, output excerpt, `kind`). Groket flattens those
onto `TraceEvent.raw_input` and does not map them to subagent types.

`user_message_chunk.content` is `{type: "text", text: "…"}`; `_meta` has
`modelId`, `promptIndex`. `tool_call` has `toolCallId`, `title` (tool id),
`rawInput`. `turn_completed` has `prompt_id`, `stop_reason`, `usage`
(`inputTokens`, `outputTokens`, `totalTokens`, `cachedReadTokens`,
`reasoningTokens`, `costUsdTicks`, `numTurns`).

### 2.3 Turn segmentation

| Outcome | Grok artifact | Module |
|---|---|---|
| Operator turns | One picker row per `turn_started.turn_number` (host-only: list position) | `session/turns.py` `segment_timeline_turns` |
| Chrome vs operator | Angle-bracket tags (`user_query`, `system-reminder`, …) | `session/tagged_blocks.py` |
| Display turn id | Trace `turn_started.turn_number` on each event (unlabeled when the harness omitted a start); unique `turn_index` is the list key | `turns.event_display_turn_map` / `TurnSegment.turn_index` |

### 2.4 Live status

Home-list labels (`SessionMeta.list_status_label`): `running` | `ending` |
`awaiting` | `cancelled` | `complete` | `—`.

| Input | Artifact | Module |
|---|---|---|
| Harness outcome | Last `events.jsonl` `turn_ended.outcome` (`completed`, …) | `parser.parse_runtime_markers` |
| Incomplete | Fresh mtime of trace files vs `INCOMPLETE_STALE_SECONDS` (20 min); `groket-interrupted.json` | `parser._infer_incomplete_turn_outcome`, `constants.INTERRUPTED_MARKER_FILENAME` |
| Interactive override | `.groket-turn/status.json` `state` (`awaiting_follow_up`, `done`, …) | `parser._gate_override_turn_outcome`, `session/turn_gate.py` |
| Live watch | inotify on `updates.jsonl`, `events.jsonl`, `summary.json`, `signals.json`, `chat_history.jsonl`, `groket-interrupted.json`, `status.json`, `command`, `operator_notes.toml` | `groket/fs_watch.py` `_TRACE_NAME_HINTS` |
| Heartbeat | Re-read `signals.json` every 60s without re-parsing timeline | `constants.LIVE_POLL_HEARTBEAT_INTERVAL` |

### 2.5 Turn-gate follow-up and Done

Eval-only. Volume `…/runs/traces/<container>/`. Gate dir `.groket-turn/`.

| File | Role |
|---|---|
| `status.json` | `{state, session_id, turn}` written by Docker entrypoint |
| `command` | Host writes `follow_up` or `done` |
| `next-prompt.txt` | Staged follow-up text |
| `final_turn` | Marker: this follow-up is the last turn |
| `pending-prompts.jsonl` | Queue while a turn is in flight |
| `primary-session-id` | Resume id for the container |

Modules: `session/turn_gate.py`, `runs/run_manager.py` `submit_follow_up`,
`assets/docker/entrypoint.sh`. TUI keys `n` / `e`. Host native sessions have
no gate.

### 2.6 Fork / resume

| Outcome | Artifact | Module |
|---|---|---|
| Can resume? | `chat_history.jsonl` or `summary.json` or `events.jsonl` | `session/resume.py` `can_resume_session` |
| Seed parent | `.groket-resume-seed/<cwd-token>/<parent_id>/` + symlink for `grok --resume --fork-session` | `resume.py` |
| Launch record | `groket-launch.json` `resume_parent_session_id`, `resume_fork_session_id` | eval volume; `runs/run_manager.py` |
| Task YAML | `resume_session_dir` | `runs/task_schema.py` |

Requires the `grok` binary `--resume` / `--fork-session`.

### 2.7 Workspace / diff

| Outcome | Artifact | Module |
|---|---|---|
| Preferred diff | Each `rewind_points.jsonl` object: `prompt_index`, `created_at`, `file_snapshots`, `after_snapshots` (path → `{path, content}`) | `session/workspace_diff.py` |
| Fallback | `updates.jsonl` `tool_call` / `tool_call_update` with Grok `search_replace` | same |
| Line counts | `signals.json` `agentLinesAdded` / `agentLinesRemoved` | `parser._load_signals` |

### 2.8 Usage and context meter

| Outcome | Artifact | Module |
|---|---|---|
| Context fill | `signals.json`: `contextWindowUsage`, `contextTokensUsed`, `contextWindowTokens`, `compactionCount`, `totalTokensBeforeCompaction` | `parser._load_signals`, `SessionMeta.context_usage_*` |
| Tool table | Coalesced timeline tool names + MCP `use_tool` unwrap; skill loads via `~/.grok/skills/<id>/SKILL.md` paths | `session/usage_stats.py` |
| Turn usage | `turn_completed.usage` on updates | `usage_stats` + timeline |
| Duration / errors | `signals.json` `sessionDurationSeconds`, `toolCallCount`, `toolFailureCount`, `errorCount`, `doomLoopWarnings` | `_load_signals` |

Grok does not persist a full per-turn context series; browser samples are
in-memory (`session/context_samples.py`).

### 2.9 Notes and flags (operator overlay)

| Overlay | Path | Module |
|---|---|---|
| Notes | `<session_dir>/operator_notes.toml`; host Grok always uses `~/.groket/notes/<session_id>/` | `groket/notes.py` |
| Schema | `~/.groket/notes_schema.toml` | same |
| Flags | `<session_dir>/flags.json` or `~/.groket/flags/<session_id>/flags.json` | `groket/flags.py` |

These are groket-owned. A rewrite keeps them keyed by harness + session id,
never written into a foreign host tree unless the operator opts in.

### 2.10 Export

`session/export_bundle.py`: outer tarball / dir under `~/.groket/reports/`.
Units: official `grok trace --local` archive (core files
`export_metadata.json`, `trace_config.json`, `summary.json`, `events.jsonl`,
`chat_history.jsonl`, `prompt_context.json`, `system_prompt.txt`), eval
`run/` volume, `flags.json`, notes. Export of non-Grok
sessions cannot call `grok trace --local`.

### 2.11 Control plane

JSON-RPC on the per-user Unix socket (`integrations/control.py`):
`session/list`, `session/get`, `session/overview`, `session/timeline`,
`session/turns`, `session/usage`, `session/open`,
`session/render`, plus notes notifications. Payloads built in
`session/control_views.py` from the same parser/turns/usage/notes loaders.
HUD and editors consume this. A rewrite keeps the method names; rows gain
`harness`.

### 2.12 Docker / batch eval traces

| Artifact | Role | Module |
|---|---|---|
| `<work>/runs/traces/<container>/` | Bind-mounted Grok sessions home + gate | `docker/orchestrator.py` |
| `%2Fworkspace/<session_id>/` | Primary session (cwd token) | `session/resume.py` `_DEFAULT_CWD_TOKEN` |
| `groket-launch.json`, `run.json`, `groket-prompt.txt`, `groket-config.toml` | Recipe / prompt / persona / MCP / skills / plugins | orchestrator + `runs/batch.py` |
| `session_search.sqlite` | Grok search index (skip on export) | `export_bundle._RUN_SKIP_NAMES` |
| Task YAML | `runs/task_schema.py`, `schemas/tasks.schema.json` | batch |

Entrypoint installs Grok marketplace plugins and skills into `~/.grok`
inside the container (`assets/docker/entrypoint.sh`).

### 2.14 Host `~/.grok/sessions`

Layout: `<root>/<url-encoded-cwd>/<session_id>/` with the files in §3.1.
Subagent dirs are hidden. `H` toggles inclusion.

---

## 3. Abstract disk-view contract

A harness adapter implements these outcomes. Missing product data is
`absent`, not faked.

### 3.1 Session identity

- `harness`: one of the nine ids.
- `session_id`: stable string the product uses to resume when it can.
- `session_ref`: locator the adapter can reopen (directory, jsonl path,
  sqlite row id, markdown path).
- `origin`: `work` (groket-launched) or `host` (native product store).
- `cwd` / workspace path when known.

### 3.2 Catalog row

Must fill: `harness`, `sessionId`, `path` (or ref string), `title`/`label`,
`model` (may be `unknown`), `status` (same six labels; `—` if unknown),
`origin`, `createdAt` / `updatedAt` / `sortEpoch`, `numEvents` (best
effort), `toolCallCount`, `errorCount`. Optional: context compact string,
`taskId`, duration, git repo/branch.

Discovery: adapter lists default roots and returns `session_ref`s. Groket
does not assume one directory = one session.

### 3.3 Linear timeline

A list of `TraceEvent`-shaped rows after adapt:

| Field | Meaning |
|---|---|
| `index` | 0-based after coalesce |
| `event_type` | Neutral kind (below) **or** harness-native name with `event_kind()` mapping |
| `timestamp` | Unix seconds when known |
| `content` | Display text |
| `tool_name`, `tool_call_id`, `raw_input`, `is_error` | Tools |

**Neutral kinds** the UI already colors via `event_types.event_kind`:
`user`, `agent`, `thought`, `tool`, `tool_result`, `plan`, `error`,
`session`, `system`, `subagent`, `task`, `other`.

Adapters coalesce streaming deltas the way `parse_timeline` coalesces
`agent_message_chunk` and `tool_call_update`.

### 3.4 Turns

Segments with `turn_index`, optional native `turn_number`, `outcome`,
`open`, event list. Prefer product turn markers. If none, split on operator
user messages. Chrome-only injections are not new turns (Grok
`tagged_blocks`; other harnesses: skip `system` / `queue-operation` /
session-meta rows).

### 3.5 Status

Map product signals onto `list_status_label` values. Rules:

- Growing transcript / open turn + fresh mtime → `running`
- Product waiting for next user prompt → `awaiting` **only** if the product
  actually waits (Grok gate `awaiting_follow_up`; most host CLIs exit per
  turn → `complete`)
- Shutdown in progress → `ending`
- Interrupted / killed / auth error → `cancelled`
- Successful terminal turn → `complete`
- Unknown → `—`

### 3.6 Tools

Paired call/result with stable `tool_call_id`, name, arguments, output,
error flag. MCP/bridge wrappers unwrap to the inner name when present.

### 3.7 Workspace changes

In order: product snapshot/diff → reconstructed edit-tool patches →
`absent`. Never invent a git diff of the live cwd unless the product stored
one.

### 3.8 Usage

Token in/out/cache/reasoning, cost, context window fill, duration, tool
counts. Any missing field stays unset (Grok context meter is
`signals.json`-shaped; do not fake percent from nothing).

### 3.9 Operator overlay

Notes and flags attach to `(harness, session_id)` in groket config-home
when the host tree is not writable or is a database. Same TUI keys.

### 3.10 Live watch

Adapter declares watch roots + filename/table hints. SQLite stores watch
the db file mtime (and `-wal`). Markdown watches the history file.

### 3.11 Launch / control (optional per harness)

Only when the product has a real equivalent:

| Groket action | Required product capability |
|---|---|
| New eval | Headless prompt + writable workspace |
| Follow-up (`n`) | Multi-turn resume by session id **or** a gate file |
| Done (`e`) | A real stop signal; otherwise no-op / leave `complete` |
| Fork (`f`) | Product fork or “new session seeded from history” |
| Docker isolation | Image + auth + home layout for that binary |

Capability bits stay fail-closed (same idea as Automedon `Capabilities`).

### 3.12 Adapter interface (rewrite shape)

One Python module per harness id under something like
`groket/harness/<id>.py`:

- `discover(roots) -> list[SessionRef]`
- `looks_like(ref) -> bool`
- `load_meta(ref) -> SessionMeta` (+ `harness`)
- `parse_timeline(ref) -> list[TraceEvent]`
- `watch_hints() -> filenames / globs`
- optional: `workspace_diff`, `usage`, `resume_id`, `launch`, `follow_up`,
  `done`

Grok’s current `parser.py` becomes the `grok` adapter. Shared catalog/UI
never import `updates.jsonl` by name.

---

## 4. Per-harness disk specifications

Live stream (Automedon `parse_line` / `tests/recordings/<id>/stream.ndjson`)
is listed only to forbid treating it as the disk parser.

### 4.1 `grok`

**Discover roots**

- Host: `~/.grok/sessions/<url-encoded-cwd>/<session_id>/`
- Eval: `<work>/runs/traces/<container>/%2Fworkspace/<session_id>/`

**Identity:** directory name is UUID `session_id`. Cwd parent is percent-
encoded path.

**Layout (host sample
`~/.grok/sessions/%2Fmnt%2Fdev%2F_git%2Fgrokos/019fc4be-…/`):**
`summary.json`, `signals.json`, `updates.jsonl`, `events.jsonl`,
`chat_history.jsonl`, `rewind_points.jsonl`, `system_prompt.txt`,
`prompt_context.json`, `resources_state.json`, `announcement_state.json`,
`terminal/` (`call-*.log`, `monitor-call-*.log`), optional
`background_tasks_manifest.json`, `*.lock`. `resources_state.json`
`state["grok_build.Scheduler"].tasks` is the durable scheduler
(`id`, `intervalSecs`, `prompt`, `recurring`, `durable`, `lastFiredAt`,
`lastSubagentId`). `state["grok_build.ReportedTaskCompletions"].reported`
is task ids already pushed to the agent. Manifest rows have `task_id`,
`kind` (`bash` or `monitor`), `command`, `cwd`, `output_file`,
`description`. Groket merges those in `session/jobs.py` for Summary and
HUD Overview (not the TUI Jobs `J` modal). Long Darwin sessions also
had `plan.json` (`todos`), `plan_mode.json` (`state`,
`awaiting_plan_approval`), `goal/` (`state.json`, `plan.md`),
`compaction/` (`INDEX.md`, `segment_*.md`), and `subagents/<id>/`
(`meta.json`: `parent_session_id`, `child_session_id`, `subagent_type`,
`status`, `tool_calls`, `turns`, `effective_model_id`; `output.json`).

**Eval volume extras:** `.groket-turn/`, `groket-launch.json`, `run.json`,
`groket-prompt.txt`, `groket-config.toml`, `session_search.sqlite`.

**`summary.json` keys:** `agent_name`, `chat_format_version`, `created_at`,
`updated_at`, `last_active_at`, `current_model_id`, `reasoning_effort`,
`generated_title`, `session_summary`, `num_messages`, `num_chat_messages`,
`next_trace_turn`, `request_id`, `grok_home`, `sandbox_profile`,
`info` (`id`, `cwd`). Git when present: `head_branch`, `head_commit`,
`git_remotes` (list of URLs or `{url}`).

**`signals.json`:** see §2.8. Sample: `contextWindowUsage` 6,
`contextTokensUsed` 34525, `contextWindowTokens` 500000, `toolCallCount`,
`toolsUsed`, `primaryModelId`, `sessionDurationSeconds`, line add/remove.

**`updates.jsonl`:** `{timestamp, method: "session/update"| "_x.ai/session/update", params: {update: {sessionUpdate, …}}}`. Kinds §2.2.

**`events.jsonl` `type`:** `turn_started` (`session_id`, `turn_number`,
`model_id`, `yolo_mode`, `conversation_message_count`,
`session_relationship`, `schema_version`, `ts`), `turn_ended` (`outcome`,
`ts`), `loop_started`, `phase_changed`, `first_token`, `tool_started`,
`tool_completed`, `permission_requested`, `permission_resolved`. Groket
timeline uses turn/error markers; phases are telemetry.

**`chat_history.jsonl` `type`:** `system` | `user` | `assistant` |
`reasoning` | `tool_result`. `user.content` is a list of `{type, text}`
(often `<user_info>` / `<user_query>` chrome).

**`rewind_points.jsonl`:** `{prompt_index, created_at, file_snapshots,
after_snapshots}`.

**Live vs disk:** Automedon grok stream is `{type: text|thought|tool_call|
tool_call_update|usage|end}` — **not** `session/update` envelopes. Disk
parser stays `parser.py`. Stream parser is Automedon `adapters/grok.py`.

**Subagents:** `session_relationship`, `subagents/` dirs, `subagent_*`
updates. Catalog hides them (`parser._drop_subagent_mirror_sessions`).

**Resume:** session id + `chat_history.jsonl`; fork via groket seed +
`grok --resume --fork-session`.

**Watch:** §2.4 filename list.

### 4.2 `claude`

**Discover roots:** `~/.claude/projects/<cwd-token>/<sessionId>.jsonl`
where cwd-token is the workspace path with `/` → `-` (e.g.
`-tmp-automedon-cwd-ol1z5d2a`). One file = one session. Also
`~/.claude/history.jsonl` rows `{display, project, sessionId, timestamp}`
(catalog preview). `~/.claude/sessions/` was empty on both hosts.

**Identity:** `sessionId` / filename UUID. `cwd` on user/assistant rows.

**Disk row `type` (Linux auth-fail samples + Darwin successful samples):**

| type | keys | role |
|---|---|---|
| `queue-operation` | `operation` (`enqueue`/`dequeue`), `sessionId`, `timestamp`, optional `content` | chrome, not a turn |
| `user` | `uuid`, `parentUuid`, `sessionId`, `timestamp`, `cwd`, `gitBranch`, `permissionMode`, `promptId`, `promptSource`, `userType`, `entrypoint`, `version`, `isSidechain`, `message` | operator prompt |
| `attachment` | `attachment` (`type`: `deferred_tools_delta` / `agent_listing_delta` / `skill_listing` / `command_permissions`, …) | chrome |
| `ai-title` | `aiTitle`, `sessionId` | catalog title |
| `assistant` | locators + `effort`, `requestId`, optional `error` / `isApiErrorMessage`, `message` | reply / tools / error |
| `last-prompt` | `lastPrompt`, `sessionId`, optional `leafUuid` | duplicate of last user text |

**`user.message`:** `{role: "user", content: <str or content-block list>}`.
**`assistant.message`:** Anthropic message: `id`, `role`, `model` (e.g.
`claude-opus-5`), `content` (list of `{type: "text"|"thinking"|"tool_use"}`),
`stop_reason` (`tool_use` / `end_turn` / …), `usage` (`input_tokens`,
`output_tokens`, `cache_*`). Darwin sample `tool_use`: `{id, name: "Skill",
input, caller}`. Linux-only samples were `error: authentication_failed`
with `model: "<synthetic>"`.

**`isSidechain`:** subagent / side chain — hide from primary catalog when
true.

**Meta:** title = `ai-title.aiTitle` when present, else first user text;
model from last assistant `message.model` if not `<synthetic>`; times from
row `timestamp`; git branch field `gitBranch`.

**Live vs disk:** live `-p --output-format stream-json` uses `type`
`system` / `assistant` / `user` / `result` / hook events and
`content_block_delta`. Disk is append-only jsonl with `queue-operation` +
full messages — **do not** run Automedon `parse_line` on the project file.

**Resume:** `claude --resume <sessionId>`. No groket turn gate. No
`signals.json`. No rewind snapshots. Workspace: reconstruct from
`tool_use` (`Write` / `Edit` / `Bash`) when present.

**Watch:** the session jsonl path; new files under `~/.claude/projects`.

**Status:** process gone + last assistant/result → `complete` or
`cancelled` if `isApiErrorMessage` / `error`. No `awaiting` on host disk
(headless `-p` exits).

### 4.3 `codex`

**Discover roots:** `~/.codex/sessions/<YYYY>/<MM>/<DD>/rollout-<ISO>-<uuid>.jsonl`.
Also `~/.codex/config.toml`. Darwin also has `~/.codex/state_5.sqlite`
table `threads` (`id`, `rollout_path`, `created_at`, `updated_at`, `cwd`,
`title`, `model_provider`, `tokens_used`, `archived`) — a catalog index
over rollouts. Sibling sqlite (`goals_1`, `logs_2`, `memories_1`) is
product telemetry, not the timeline. No `history.jsonl` /
`session_index.json` on Linux.

**Identity:** `payload.session_id` / `payload.id` on first `session_meta`
row; filename UUID suffix matches.

**Outer row:** `{timestamp, type, payload}`.

| `type` / `payload.type` | Role | Notable fields |
|---|---|---|
| `session_meta/` | Catalog | `session_id`, `cwd`, `originator` (`codex_exec`), `cli_version`, `model_provider`, `source`, `thread_source` |
| `turn_context/` | Turn | `turn_id`, `cwd`, `workspace_roots`, `model`, `approval_policy`, `sandbox_policy`, `personality` |
| `world_state/` | Session chrome | `state.model`, skills, permissions, git_attribution |
| `event_msg/task_started` | Turn open | `turn_id`, `model_context_window` |
| `event_msg/user_message` | User | `message` (string) |
| `event_msg/agent_message` | Agent text | `message`, `phase` (`commentary`) |
| `event_msg/token_count` | Usage | `info.last_token_usage`, `total_token_usage`, `model_context_window` |
| `event_msg/task_complete` | Turn end | `turn_id`, `last_agent_message`, `duration_ms`, `time_to_first_token_ms` |
| `event_msg/patch_apply_end` | Workspace | `success`, `stdout`, `changes` (path map), `call_id` |
| `response_item/message` | Chat item | `role` (`developer`/`assistant`/…), `content` |
| `response_item/custom_tool_call` | Tool | `call_id`, `name` (`exec`), `input`, `status` |
| `response_item/custom_tool_call_output` | Tool result | `call_id`, `output` |

**Live vs disk:** Automedon drives `codex exec --json` (shared JSON parse).
Disk rollout is a **different envelope** (`session_meta` / `event_msg` /
`response_item`). Need a dedicated disk reader.

**Resume:** `codex exec resume <session_id> --json`. Context window on
`task_started.model_context_window` and `token_count` (not Grok
`signals.json` percent). Diff: `patch_apply_end.changes` is first-class.

**Watch:** new `rollout-*.jsonl` under date dirs; append to current file.

**Subagents:** `world_state.state.multi_agent_mode` exists; no separate
session dirs observed.

### 4.4 `gemini`

**Discover roots**

- Chats: `~/.gemini/tmp/<project-slug>/chats/session-<ISO>-<shortid>.jsonl`
- Project pointer: `~/.gemini/tmp/<slug>/.project_root` and
  `~/.gemini/history/<slug>/.project_root`
- Settings: `~/.gemini/settings.json`, `projects.json`, `state.json`

**Identity:** first row `sessionId` (UUID); `projectHash`; `kind: "main"`.

**File shape (two rows typical):**

1. Header: `{sessionId, projectHash, startTime, lastUpdated, kind}`
2. Mutation: `{"$set": {"messages": [...], "lastUpdated": "…"}}`

**Message:** `{id, timestamp, type: "user"|"model"|…, content}` where
`content` is a string **or** a list of `{text: "…"}` (session_context
preamble). Host samples were short automedon runs (often a single user
message). Tool rows were not present on disk here.

**Live vs disk:** live `-p -o stream-json` types `system`, `text`,
`tool_use`, `tool_result`, `result`. Disk is a tiny jsonl “document
mutation” log, not that stream. Parse `$set.messages`; do not use
Automedon `parse_line` on the chat file.

**Resume:** product `-r` / session id from header. Usage/context: absent on
these files; may appear inside later `$set` keys — treat extra keys as
optional. Watch the session jsonl.

**Status:** last `$set.lastUpdated` + whether a model message exists.

### 4.5 `opencode`

**Discover roots:** SQLite `~/.local/share/opencode/opencode.db`
(+ `-wal`/`-shm`). Also `~/.local/share/opencode/{log,repos,snapshot}` and
`~/.config/opencode/opencode.jsonc`. **Not** a session directory tree.

**Tables (product):**

| Table | Role | Columns (abbrev.) |
|---|---|---|
| `session` | Catalog | `id`, `project_id`, `parent_id`, `slug`, `directory`, `path`, `title`, `version`, `model` (JSON), `agent`, `cost`, `tokens_input/output/reasoning/cache_*`, `summary_additions/deletions/files`, `summary_diffs`, `time_created/updated/compacting/archived` |
| `message` | Turn messages | `id`, `session_id`, `time_*`, `data` JSON (`role`, `agent`, `model`, `time`, `summary.diffs`) |
| `part` | Timeline atoms | `id`, `message_id`, `session_id`, `time_*`, `data` JSON |
| `event` | Append log | `id`, `aggregate_id` (session id), `seq`, `type`, `data` |
| `event_sequence` | Cursor | `aggregate_id`, `seq` |
| `project` / `project_directory` | Workspace | `worktree`, `directory`, `vcs` |
| `todo` | Plan-like | `session_id`, `content`, `status`, `priority`, `position` |
| `workspace` | Branch/dir | `branch`, `directory`, `project_id` |

Observed `event.type`: `session.created.1`, `session.updated.1`,
`message.updated.1`, `message.part.updated.1`.

Observed `part.data.type`: `text` (`text`), `reasoning` (`text`, `time`),
`tool` (`tool`, `callID`, `state`), `step-start` / `step-finish`
(`snapshot` git hash, `reason`, `tokens`, `cost`).

**Tool `state`:** `{status, input, output, metadata, title, time}`. Sample
`tool: "bash"`, `status: "completed"`.

**Identity:** `session.id` like `ses_028645352ffeyueNAXQSWVVcqq`.
`parent_id` is the subagent link (none populated here). Title and
directory are first-class. Tokens and cost live on `session`.

**Workspace:** `summary_*` + `part` `step-start.snapshot` (git hash) +
`message.data.summary.diffs`. Prefer those over live git.

**Live vs disk:** live `opencode run --format json` types `step_start`,
`text`, `tool_use`, `step_finish`. Disk is relational + JSON blobs.
Query SQLite; do not parse live NDJSON as the host store.

**Resume:** `opencode --session <id>` / `--continue`. Watch `opencode.db`
mtime/WAL.

**Caveat:** DB may hold credentials in `account` / `credential` — never
export those tables.

### 4.6 `cursor`

**Discover roots**

- Darwin (present): `~/.cursor/projects/<cwd-token>/agent-transcripts/<uuid>/<uuid>.jsonl`
  plus `repo.json` (`id`). Companion chat blobs:
  `~/.cursor/chats/<hash>/<uuid>/{meta.json, store.db}` —
  `meta.json` `{schemaVersion, createdAtMs, updatedAtMs, hasConversation, cwd}`.
  Sample `store.db` had no tables (empty shell; transcript is the jsonl).
- Linux (this groket host): `~/.cursor/projects/<slug>/` were **empty**
  automedon temp dirs; no `agent-transcripts`.
- Config only: `~/.cursor/cli-config.json`, `agent-cli-state.json`.
- `~/.local/share/cursor-agent/` is the binary install (`versions/<ver>/`),
  not sessions.

**Identity:** transcript directory / filename UUID. `meta.json.cwd` when
the chats blob exists.

**Disk jsonl rows (Darwin sample, tools present):**

| Row shape | Role |
|---|---|
| `{role: "user", message: {content: [blocks]}}` | operator prompt |
| `{role: "assistant", message: {content: [blocks]}}` | text + tools |
| `{type: "turn_ended", status: "success"}` | turn close |

Content blocks: `{type: "text", text}` (often `<timestamp>` +
`<user_query>` chrome) and `{type: "tool_use", name, input}`. Observed
tool names: `Shell`, `Write`, `Read`, `StrReplace`. No separate
`tool_result` rows in that file (results folded into later assistant
text).

**Live vs disk:** live `cursor-agent --print --output-format stream-json`
types `system`, `thinking`, `assistant`, `tool_call`, `result` (with
`session_id`). Disk is a small role/message jsonl without `session_id` on
each row. Separate parsers.

**Resume:** `--resume <session_id>` on the binary. Watch
`agent-transcripts/**/*.jsonl`. `cli-config.json` `rewind: true` is not
`rewind_points.jsonl`.

**Status:** last `turn_ended.status` (`success`) → `complete`. Linux
without transcripts: catalog empty.

### 4.7 `aider`

**Discover roots**

- Per-workspace markdown: `.aider.chat.history.md` (also
  `aider.chat.history.md`) in the git/work tree
- Optional `~/.aider/` (analytics/caches — not the transcript)
- Automedon launch can set `--chat-history-file` to a dedicated path
  (session id = that path)

**Observed file** (`automedon/.aider.chat.history.md`):

```
# aider chat started at 2026-08-06 08:20:33
> … command / chrome lines …
#### Reply with exactly: AIDER_XAI_OK and nothing else
```

User turns are `#### <prompt>`. Assistant / tool text follows as markdown
until the next `####` or `# aider chat started`. No structured tool ids.
`--message` path has **no tool stream** (Automedon aider adapter
`stream_tools` false).

**Live vs disk:** live stdout is banner + model + reply text (recording:
`Aider v0.82.0` / `Model: gpt-4o` / `AUTOMEDON_T1`). Disk is the markdown
history. Parse the file; do not reuse `parse_line`.

**Resume:** `--restore-chat-history` + `--chat-history-file`. Watch that
file. Status: file mtime; no awaiting gate. Diff: git in the workspace if
aider was allowed to commit; otherwise `absent` (Automedon uses
`--no-git`).

### 4.8 `pi`

**Discover roots:** `~/.pi/agent/sessions/<cwd-token>/*.jsonl`
cwd-token example: `--mnt-dev-_git-xai-grokos-…--`. Filename
`<ISO>_<session-uuid>.jsonl`.

**Row types:**

| type | keys | role |
|---|---|---|
| `session` | `id`, `cwd`, `timestamp`, `version` (`"3"`) | header / identity |
| `model_change` | `id`, `parentId`, `provider`, `modelId`, `timestamp` | model |
| `thinking_level_change` | `thinkingLevel`, `parentId` | effort |
| `message` | `id`, `parentId`, `timestamp`, `message` | body |

**`message.message` by `role`:**

- `user`: `{role, content, timestamp}` — `content` string or list
- `assistant`: `{role, content: [blocks], api, provider, model, usage,
  stopReason, responseId, rawStopReason, timestamp}`
- `toolResult`: `{role, toolCallId, toolName, content, details, isError,
  timestamp}`

**Assistant `content` blocks:** `thinking` (`thinking`,
`thinkingSignature`), `text`, `toolCall` (`id`, `name`, `arguments` dict).
Observed tools: `bash`. Observed content item counts on one file:
text 24, thinking 12, toolCall 8.

**Chain:** `parentId` links rows (session → model_change → messages).

**Live vs disk:** live `pi -p --mode json` types `session`,
`message_update`, `tool_execution_start`, `tool_execution_end`,
`agent_settled`. Disk is the persisted message tree with full blocks —
related but not the same. Write a disk reader for the jsonl file.

**Resume:** `--session-id` / `--continue`. Usage on assistant `usage`.
No context-window percent observed. Watch the session jsonl. Subagents:
not seen as separate files.

### 4.9 `copilot`

Two complementary stores:

**A. Index DB** `~/.copilot/session-store.db` (schema_version 6)

| Table | Columns | Role |
|---|---|---|
| `sessions` | `id`, `cwd`, `repository`, `host_type`, `branch`, `summary`, `created_at`, `updated_at` | catalog |
| `turns` | `id`, `session_id`, `turn_index`, `user_message`, `assistant_response`, `timestamp` | coarse transcript |
| `assistant_usage_events` | `session_id`, `turn_index`, `model`, `input_tokens`, `output_tokens`, `cache_*`, `reasoning_tokens`, `duration_ms`, `time_to_first_token_ms`, `reasoning_effort`, `finish_reason`, `token_details_json`, `created_at` | usage |
| `session_files` | `session_id`, `file_path`, `tool_name`, `turn_index`, `first_seen_at` | touched files |
| `session_refs` | `ref_type`, `ref_value` | links |
| `forge_trajectory_events` | `tool_call_id`, `event_type`, `command`, `output`, `exit_code` | tools (0 rows here) |
| `checkpoints` | title/overview/history/work_done/… | optional |

**B. Per-session dir** `~/.copilot/session-state/<session_id>/`

- `workspace.yaml` — `id`, `cwd`, `client_name`, `name`, `created_at`,
  `updated_at`
- `events.jsonl` — `{type, data, id, parentId, timestamp}`
- `session.db` — `inbox_entries`, `todos`, `todo_deps`
- `files/`, `checkpoints/`, `research/`

**`events.jsonl` types:** `session.start` (`sessionId`, `copilotVersion`,
`startTime`, `context`), `session.model_change` (`newModel`),
`session.auto_mode_resolved` (`chosenModel`, `reasoningBucket`),
`system.message`, `user.message` (`content`, `transformedContent`,
`attachments`, `interactionId`), `assistant.turn_start` (`turnId`),
`assistant.message` (`messageId`, `model`, `content`, `toolRequests`,
`outputTokens`, …), `assistant.turn_end`, `session.usage_checkpoint`.

Prefer **events.jsonl** for timeline (includes system chrome and
`toolRequests`); `turns` table is a flattened user/assistant pair (good
catalog preview, lossy for tools).

**Live vs disk:** live `copilot -p --output-format json` types
`assistant.message_delta`, `assistant.message`, `tool.execution_start`,
`tool.execution_end`, `assistant.turn_end`, `result`. Disk events.jsonl
uses dotted names and a `data` envelope. Separate parsers.

**Resume:** `--resume=<id>` / `--continue`. Context: usage events +
`usage_checkpoint`, not Grok percent. Watch both the index db and
`session-state/<id>/events.jsonl`.

---

## 5. Feature × harness matrix

Legend: **full** = store or documented product control can supply the
outcome. **partial** = usable subset / needs launch-time capture.
**absent** = do not mark parity.

| Feature | grok | claude | codex | gemini | opencode | cursor | aider | pi | copilot |
|---|---|---|---|---|---|---|---|---|---|
| Catalog discover | full | full | full | full | full | full Darwin jsonl; absent on Linux host | partial | full | full |
| Title / summary | full `generated_title` | full `ai-title` / first user | partial first user / task_complete / `threads.title` | partial | full `session.title` | partial first `<user_query>` | partial first `####` | partial first user | full `sessions.summary` / yaml `name` |
| Model id | full | partial (skip `<synthetic>`) | full `turn_context.model` | absent on disk samples | full `session.model` | absent disk; live `system.model` | partial banner in md | full `model_change.modelId` | full usage + `auto_mode_resolved` |
| Linear timeline | full | full (incl. queue chrome filter) | full | partial messages `$set` | full parts+events | full Darwin jsonl | partial md turns | full messages | full events.jsonl |
| Tool calls/results | full | full Darwin `tool_use`; Linux samples auth-failed | full `custom_tool_call*` + patch_apply | absent on disk samples; live has tool_use | full `part.type=tool` | partial `tool_use` (no result rows) | absent structured | full `toolCall` + `toolResult` | partial `toolRequests` + `forge_trajectory` / live tool.execution_* |
| Turn segments | full markers | partial user/assistant pairs | full `turn_id` / task_* | partial | partial step-start/finish + messages | full `turn_ended` | partial `####` | partial user→settled | full turn_index + turn_start/end |
| Status running/complete/cancelled | full | partial | partial | partial | partial (`time_archived`, compacting) | partial `turn_ended.status` | partial | partial | partial |
| Status `awaiting` | full eval gate only | absent (headless exits) | absent | absent | absent | absent | absent | absent | absent |
| Status `ending` | full gate `command=done` | absent | absent | absent | absent | absent | absent | absent | absent |
| Follow-up `n` | full eval `.groket-turn` | partial product `--resume` if groket launches | partial `exec resume` | partial `-r` | partial `--session` | partial `--resume` if launching | partial restore history | partial `--session-id` | partial `--resume=` |
| Done `e` | full gate `command=done` | absent | absent | absent | absent | absent | absent | absent | absent |
| Fork `f` | full `grok --resume --fork-session` + seed | absent (new session only) | absent | absent | absent (`parent_id` unused here) | absent | absent | absent | absent |
| Workspace diff | full rewind + search_replace | partial reconstruct tools | full `patch_apply_end.changes` | absent | partial `summary_diffs` / git snapshot | partial reconstruct Write/StrReplace | partial git if enabled | partial bash/file tools | partial `session_files` / rewind-file-snapshots stub |
| Context meter (`signals.json` shape) | full | absent | partial `model_context_window` + token_count | absent | partial session token columns | absent | absent | partial assistant `usage` | partial usage_events / checkpoint |
| Compaction count | full signals | absent | absent | absent | partial `time_compacting` | absent | absent | absent | absent |
| Git repo/branch | full summary remotes | partial `gitBranch` | partial world_state git_attribution | partial `.project_root` | full project.worktree / workspace.branch | partial `meta.json.cwd` | partial | `cwd` only | `sessions.repository/branch` |
| Subagents | full hide/filter + `subagents/meta.json` | partial `isSidechain` | partial multi_agent flags / `thread_spawn_edges` | `kind: main` only | partial `parent_id` | absent | absent | absent | absent |
| Live watch | full named files | full jsonl | full jsonl + `state_5.sqlite` | full jsonl | full sqlite mtime | full Darwin jsonl mtime | full md mtime | full jsonl | full db + events.jsonl |
| Notes / flags overlay | full | full (groket side store) | full | full | full | full | full | full | full |
| Export `grok trace --local` | full | absent | absent | absent | absent | absent | absent | absent | absent |
| Export groket bundle (timeline+notes) | full | full after adapter | full | full | full | full after adapter | full | full | full |
| Docker personas/plugins entrypoint | full Grok marketplace | absent | absent | absent | absent | absent | absent | absent | absent |
| Control `session/*` views | full | full once adapter fills timeline | full | full | full | partial | full | full | full |

---

## 6. Gaps a rewrite must not paper over

1. **Live stream ≠ disk store.** Automedon `parse_line` is for child
   stdout / Agent Client Protocol. Every id except maybe a future
   groket-canonical jsonl needs a **second** reader. Evidence: grok live
   `{type:text}` vs disk `method: session/update`; claude live
   `stream-json` vs disk `queue-operation`; copilot live
   `tool.execution_start` vs disk `events.jsonl` `user.message`; opencode
   live NDJSON vs SQLite `part.data`. Cursor live `tool_call` vs disk
   `role`/`tool_use` blocks.

2. **SQLite and markdown are first-class stores.** `opencode` and
   `copilot` are databases (+ copilot `events.jsonl`). `aider` is
   markdown. Codex `state_5.sqlite` `threads` is a catalog index beside
   jsonl rollouts. Catalog `find_sessions` directory walk cannot stay the
   only discovery algorithm.

3. **Cursor disk is host-dependent.** Darwin:
   `~/.cursor/projects/<cwd>/agent-transcripts/<uuid>/<uuid>.jsonl` with
   tools. Linux groket host: empty project dirs — catalog empty there,
   not a missing schema. Probe before marking the machine empty.

4. **Grok-only control and telemetry** — never mark **full** on others
   without an equivalent:
   - `.groket-turn` follow-up / Done / `ending` / `awaiting`
   - `signals.json` context percent + doom-loop + line counts
   - `rewind_points.jsonl` file snapshots
   - `grok --resume --fork-session` + `.groket-resume-seed`
   - Docker entrypoint Grok plugin/skill marketplace
   - `grok trace --local` export
   - `tagged_blocks` Grok XML chrome (other harnesses have their own
     chrome: Claude `queue-operation`, Gemini `session_context`, Copilot
     `transformedContent` / `<system_reminder>`)

5. **Claude Linux samples were auth failures; Darwin has successful
   `tool_use`.** Prefer Darwin fixtures (`claude-opus-5`, `Skill` /
   write tools, `ai-title`, `attachment`) for adapter tests. Linux-only
   trees are not enough.

6. **Gemini `$set` chat files here are thin** (header + one user
   message). Tool and model rows must be handled when they appear; do not
   assume the two-row shape is complete.

7. **Operator overlay must not corrupt host DBs.** Write notes/flags next
   to a jsonl/dir when safe; for SQLite/host trees use
   `~/.groket/notes/<harness>/<session_id>/` (same reason host Grok
   already uses the fallback).

8. **Secrets.** OpenCode `account.access_token` / `credential.value` and
   Copilot encrypted reasoning blobs must never enter export bundles.

9. **Tool name maps.** Timeline labels that hard-code `read_file` /
   `search_replace` / `run_terminal_command` are Grok-shaped. Parity
   needs a per-harness alias table (Claude `Read`/`Edit`/`Bash` /
   `Skill`, Cursor `Write`/`Read`/`Shell`/`StrReplace`, Codex `exec` +
   patch, OpenCode/Pi `bash`, …).

10. **Eval Docker is a Grok profile.** Multi-harness eval on the host
    binary (cwd + yolo flag) is the path that can actually launch Claude /
    Codex / … . Copying `entrypoint.sh` and expecting `~/.grok` is wrong.

---

## 7. Extra stores (out of the nine ids)

| Path | Status |
|---|---|
| `~/.kimi-code/` (Darwin) | Real session tree: `session_index.jsonl` `{sessionId, sessionDir, workDir}`; `sessions/<wd>/<sessionId>/state.json` + `agents/main/wire.jsonl` (`metadata`, `config.update`, `tools.set_active_tools`). Thin sample (no user turns). Defer as a tenth id until a tool-bearing fixture exists. |
| `~/.cagent/store/` | Content-addressed blobs (`sha256:….json/.tar`), not a chat catalog. |
| `~/.junie/mcp` | MCP config only. |
| `~/.rune/` | Product runtime (`run/`, `reports/`), not a coding-session store. |
| `~/.continue`, `~/.config/continue` | missing |
| `~/.windsurf`, `~/.config/windsurf` | missing |
| `~/.codeium` | missing |
| `~/.factory`, `~/.local/share/amp` | missing |
| `~/.claude.json` | Claude Code config file (not a session store) |
| `~/.config/opencode/` | config only (`opencode.jsonc`) |

Defer Continue / Cline / Windsurf / Langfuse / Inspect. Do not add ids
without a probed store. Kimi is the only extra with a session index.

---

## 8. Suggested rewrite slices (for the implementing agent)

1. Introduce `harness` on `SessionMeta` / catalog rows / control payloads.
   Extract `grok` disk code from `parser.py` into `groket/harness/grok.py`
   with the interface in §3.12. UI and `session/list` keep working.

2. Generic discovery: list of `(harness, root)` instead of only work+host
   Grok. Prefs: which host roots to scan.

3. JSONL adapters first (highest fidelity): `claude` (use a successful
   Darwin fixture with `tool_use` / `ai-title`), `codex`, `pi`,
   `cursor` (`agent-transcripts`), then `gemini`. Tests: committed
   sanitized fixtures copied from real first-line keys (no tokens).

4. SQLite adapters: `opencode` (`session`/`message`/`part`), `copilot`
   (index + `session-state/.../events.jsonl`), Codex `state_5.sqlite`
   `threads` as catalog only. Read-only.

5. `aider` markdown splitter (`####`, `# aider chat started`).

6. `cursor` Linux machines without `agent-transcripts` show an empty
   catalog — do not invent a layout.

7. Neutral `event_kind` + tool alias table so timeline labels match
   each harness. Grok chrome/tag names stay Grok-only.

8. Launch/control: host-local resume flags per matrix **partial** cells.
   Keep turn-gate + Docker personas as the Grok eval profile.

9. Export: groket bundle from adapted timeline for all; `grok trace
   --local` remains Grok-only.

10. Watch hints per adapter; SQLite WAL included.

Do not implement this document in the same change as the brief. The brief
is the contract.
