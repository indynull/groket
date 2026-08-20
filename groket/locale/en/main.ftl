# Groket UI — Fluent (Project Fluent)
# https://projectfluent.org/
# Language: en (source)

added-to-this-run = Added to this run

all-events = All events

all-models = All models

all-rules-disabled = All rules disabled

all-rules-enabled = All rules enabled

all-sections = All sections

all-tab = All


analysis-pipeline-title = Analysis pipeline

analysis-settings-saved = Analysis settings saved

analyze = Analyze

assistant-messages = Assistant messages

auto-analyze-on-open = Auto-analyze completed sessions
analysis-when-help = Runs when you open a completed session. Force: Ctrl+P → Analyze this session. Progress: J → Activity.
analysis-workers-help = Pools: analysis ×{$analysis} · refresh ×{$refresh}
ui-analysis-idle = [dim]No analysis yet — Ctrl+P → Analyze this session[/]
ui-analysis-idle-report = [dim]No report yet — runs after analysis[/]
ui-running-analysis-spinner = [dim italic]{$spin} Running analysis…[/]
ui-running-analysis-plain = Running analysis…
analysis-stale-toast = Stale analysis — {$detail}. Ctrl+P → Analyze this session.
analysis-stale-findings-row = Stale — {$detail} · Ctrl+P → Analyze this session
analysis-stale-report = Stale analysis — {$detail}. Ctrl+P → Analyze this session.

bind-analyze = Analyze

bind-back = Back

bind-cancel = Cancel

bind-clear-logs = Clear logs

bind-clear-view = Clear view

bind-close = Close

bind-configs = Configs

bind-delete = Delete

bind-disable-all = Disable all

bind-docker = Docker

bind-done = Done

bind-edit = Edit

bind-enable-all = Enable all

bind-export-bundle = Export

bind-findings = 4 Findings

bind-flag = Flag
bind-copy-detail = Copy

bind-help = Help

ui-leader = Leader

bind-jobs = Jobs

bind-launch = Launch

bind-launch-selected = Launch selected

bind-model = Model

bind-new = New

bind-new-persona = New persona

bind-note = Note

bind-edit-note = Edit note

bind-next-pane = Next pane

bind-next-turn = Next turn

bind-prev-turn = Prev turn

bind-event-down = Next event

bind-event-up = Prev event

bind-open = Open

bind-event-reader = Event page

bind-personas = Personas

bind-prev-pane = Prev pane
bind-pane-digit = Pane

bind-quit = Quit

bind-refresh = Refresh

bind-rerun = Re-run

bind-resume = Fork

bind-rules = Rules

bind-runner = Runner

bind-save = Save

bind-save-cfg = Save cfg

bind-search = Search

bind-select = Select

bind-select-all = Select all

bind-share = Share

bind-toggle = Toggle

bind-view = View

branch-placeholder = branch (default: main)

cancel = Cancel

clear-btn = Clear

clear-logs-btn = clear logs

close = Close

close-btn = close

cmd-analysis-settings = Analysis settings

cmd-analysis-settings-help = Configure analysis plugins (config.toml)

cmd-analyze = Analyze selected sessions

cmd-analyze-help = Home list: selected sessions, or all if none selected

cmd-analyze-session = Analyze this session

cmd-analyze-session-help = Force re-run plugins for the open session only

notify-analyzing-this-session = Analyzing this session…

cmd-back = Back

cmd-back-help = Return

cmd-back-sessions = Back to sessions

cmd-back-sessions-help = Leave browser

cmd-clear-timeline-view = Clear timeline view

cmd-clear-timeline-view-help = c — All events + clear search

cmd-delete-config = Delete config

cmd-delete-config-help = Remove recipe (not sessions)

cmd-delete-persona = Delete persona

cmd-delete-persona-help = Remove selected

cmd-delete-sessions = Delete sessions

cmd-delete-sessions-help = Delete selected traces (confirm twice)

follow-up-empty = Enter a follow-up prompt

follow-up-placeholder = Follow-up prompt…

follow-up-placeholder-send = Follow-up prompt — Enter / Ctrl+Enter to send…

follow-up-placeholder-awaiting = Follow-up — Enter sends now…

follow-up-placeholder-queue = Follow-up — Enter queues until the agent is ready (Ctrl+D = done)…

follow-up-btn-send = Send follow-up (Enter)

follow-up-btn-done = Mark session done (Ctrl+D)

follow-up-last-turn = Last turn (do not await after this)

follow-up-queued = Follow-up queued (agent busy — will send when it awaits the next turn)

follow-up-queued-final = Last-turn follow-up queued (no further awaits after it runs)

follow-up-sent = Follow-up sent to eval container

follow-up-sent-final = Last-turn follow-up sent (session ends after this turn)

follow-up-failed = Follow-up failed: { $exc }

mark-done-requested = Done requested — session stays live until the current turn finishes

mark-sessions-done-requested = Done requested for { $n } session(s) — ending until the current turn finishes

follow-up-sent-final-n = Last-turn follow-up sent for { $n } session(s) — ending after this turn

mark-session-done-failed = Mark done failed: { $exc }

interactive-modal-title = Follow-up ({ $n } awaiting)

no-awaiting-sessions = No awaiting sessions in selection

send = Send

self-test-rerun = Re-run

self-test-close = Close

filter-label = Filter

cmd-edit-persona = Edit persona

cmd-edit-persona-help = Edit selected

cmd-enable-all-rules = Enable all rules

cmd-enable-all-rules-help = Turn all rules on

cmd-export-finding = Export finding

cmd-export-finding-help = Export selected finding

cmd-export-bundle = Export session

cmd-export-bundle-help = Export with your default profile, or pick one if none is configured

cmd-export-choose-profile = Export with profile…

cmd-export-choose-profile-help = Pick a profile for this export only (does not change the default)

cmd-operator-note = New note

cmd-operator-note-help = Add a note on the current turn

cmd-edit-operator-note = Edit note

cmd-edit-operator-note-help = Edit or delete a note (O)

cmd-event-reader = Full-width event

cmd-event-reader-help = Hide the Timeline list and read the event; j / k step; Esc returns

cmd-copy-detail = Copy selection, finding, or pane
cmd-copy-detail-help = Browser: yank selection (drag release also copies); Findings row Issue box; focused body; else tab primary — y / Ctrl+Shift+C; Ctrl+C selection or focused body

cmd-focus-timeline-view = Focus timeline view

cmd-focus-timeline-view-help = v — View dropdown (visible selection)

cmd-full-refresh = Full refresh (sessions + detectors + feedback)

cmd-full-refresh-help = Rescan traces, re-run detectors, force feedback analyze+draft

cmd-help = Help

cmd-help-help = Show key / workflow help

cmd-jobs-logs = Jobs / logs

cmd-jobs-logs-help = Docker runs, TUI pool activity, serve log tail, and container logs (J)

jobs-activity-tab = Activity
jobs-activity-help = TUI analysis/refresh pools, plus a tail of the detached serve log when attached.
jobs-activity-status = {$spin} analysis {$analysis}/{$analysis_workers} · refresh {$refresh}/{$refresh_workers}
jobs-activity-control-path = Serve log: {$path}
jobs-activity-no-control = No control owner log (offline TUI, or serve not detached).
jobs-activity-control-header = — serve log —

cmd-launch-config = Launch config

cmd-launch-config-help = Launch with model override modal

cmd-launch-evaluation = Launch evaluation

cmd-launch-evaluation-help = Ctrl+Enter or Ctrl+J — start Docker run

cmd-launch-selected = Launch selected

cmd-launch-selected-help = Launch marked configs (or highlighted row) with parallelism

cmd-new-blank-runner = New blank runner

cmd-new-blank-runner-help = Open empty runner

cmd-new-persona = New persona

cmd-new-persona-help = Create persona

cmd-new-persona-runner = New persona

cmd-new-persona-runner-help = Create persona from runner (modal)

cmd-open-config-runner = Open config in runner

cmd-open-config-runner-help = Edit / launch from runner

cmd-open-configs = Open configs

cmd-open-configs-help = Browse saved run configs (recipes)

cmd-open-personas = Open personas

cmd-open-personas-help = Persona builder (env / gh-write / git identity)

cmd-open-rules = Open rules

cmd-open-rules-help = Detector / rule toggles

cmd-open-runner = Open runner

cmd-open-runner-help = Launch eval runner

cmd-persona-manager = Persona manager

cmd-persona-manager-help = Full persona list/editor

cmd-quit = Quit

cmd-quit-help = Quit the application

cmd-refresh = Refresh

cmd-refresh-help = Refresh the current screen / context (F5)

cmd-rerun-session = Re-run session

cmd-rerun-session-help = Open runner prefilled from session

cmd-resume-session = Fork session

cmd-resume-session-help = New interactive multi-turn forked from this ended session (f · grok --resume --fork-session)

resume-session-no-artifacts = Session has no chat/events to resume

resume-session-still-live = Session is still live — use follow-up (n) instead of resume

runner-resume-session-hint = [bold]Resume (fork)[/] from [cyan]{$sid}[/] — write the next user message as the prompt. History is seeded; Grok gets a [bold]new session id[/] via --fork-session. Multi-turn stays on. Workspace starts fresh (clone/setup).

cmd-save-config-only = Save config only

cmd-save-config-only-help = Save recipe without launching

cmd-save-session-config = Save session as config

cmd-save-session-config-help = Persist recipe without deleting session

cmd-search-sessions = Search sessions

cmd-search-sessions-help = Filter sessions as you type

cmd-select-all-configs = Select all / none

cmd-select-all-configs-help = Toggle all configs in selection (S)

cmd-select-all-none = Select all / none

cmd-select-all-none-help = Toggle select all sessions

cmd-toggle-rule = Toggle rule

cmd-toggle-rule-help = Enable/disable highlighted rule

cmd-toggle-select = Toggle select

cmd-toggle-select-help = Select/deselect current session row

cmd-toggle-select-config = Toggle select

cmd-toggle-select-config-help = Mark/unmark row for multi-config launch (s/space)

col-activity = Activity

col-category = Category

col-count = Count

col-description = Description

col-event-type = Event type

col-events = Events

col-percent = Percent

col-plugin = Plugin

col-rule-id = Rule ID

col-severity = Severity

col-status = Status

col-time = Time

col-title = Title

col-file = File

col-kind = Kind

col-log = Log

col-started = Started

col-added = +

col-removed = -

config-name-label = Config name

config-name-placeholder = e.g. redis-memory-leak / scratch-python-cli

configs = Configs

container-image-label = Container image

max-turns-label = Max turns (per prompt)

max-turns-placeholder = 50

could-not-open-share = Could not open share: { $exc }

default-docker-image-label = Default Docker image

delete = Delete

delete-failed = Delete failed

deleted-persona = Deleted persona { $pid }

description-field-label = Description

description-label = Description:

disable-all = Disable all

disable-all-btn = disable all

disabled-skills = Disabled skills

display-name-label = Display name

docker = Docker

done = Done

edit = Edit

edit-flag-title = Edit Flag

edit-persona-title = Edit persona · { $pid }

em-dash-dim = [dim]—[/dim]

enable-all = Enable all

enable-all-btn = Enable all

enabled-skill-names = Enabled skill names

env-vars-on-persona = Env vars to set on persona  [dim]key/value rows; empty value = you fill later in persona env[/dim]

errors-only = Errors only

event-types = Event types

extra-env-vars-label = Extra environment variables

env-btn = Env

env-editor-title = Environment variables

env-editor-run-title = Run environment variables

kv-editor-hint = One row per variable — key and value in separate fields (not KEY=value text).

kv-editor-add = Add variable

kv-editor-key-placeholder = KEY

kv-editor-value-placeholder = value

inline-skill-btn = Inline skill

inline-skill-title = Inline skill (this run)

inline-skill-hint = Run-only Grok skill pack (same shape as ~/.grok/skills/<id>/SKILL.md). Not saved on the persona.

inline-skill-name-label = Skill id

inline-skill-name-placeholder = my-run-skill

inline-skill-name-hint = Lowercase letters, digits, hyphens (2–64 chars). Becomes the skill folder and frontmatter name.

inline-skill-description-label = Description (when to use)

inline-skill-description-placeholder = Use when the agent should … (triggers auto-invoke; be specific)

inline-skill-description-hint = One or two sentences plus keywords. Written to YAML frontmatter description.

inline-skill-body-label = Instructions (markdown body)

inline-skill-body-hint = Body only — frontmatter is built from id + description on save.

inline-skill-name-required = Skill id is required

inline-skill-description-required = Description is required (controls when Grok uses the skill)

inline-skill-name-invalid = Skill id must be 2–64 chars: start/end with letter or digit; only a–z, 0–9, hyphens

extra-mcp-toml = Extra MCP TOML

extras = Extras

filter-label = Filter

findings-heading = Findings

flag-event-title = Flag Event

flag-removed = Flag removed from event #{ $index }

flag-saved = Flag saved on event #{ $index }

flags-blurb = Your annotations on timeline events (verdict + note). Not detector findings.

flags-heading = Flags

flags-only = Flags only

new-note-title = New note

edit-note-title = Edit note

note-saved = Operator note saved (turn { $turn })

note-updated = Operator note updated (turn { $turn })

note-deleted = Operator note deleted

note-none-to-edit = No notes to edit — press N

note-save-failed = Could not save operator note: { $msg }

note-turn-invalid = Choose a valid turn before saving

notes-blurb = N add · O edit or delete

notes-empty-preview = (empty)

notes-events-hint = Linked events: { $indices }

notes-field-detail = Detail

notes-field-summary = Summary

notes-heading = Notes

notes-only = Notes only

pick-note-title = Choose a note to edit

tip-no-notes = No notes yet — press N

turn-label = Turn:

friendly-name-placeholder = Friendly name


git-user-email = Git user.email

git-user-name = Git user.name

github-token-label = GitHub token (PAT)

headers-hint = Headers  [dim]NAME=value per line; use ${ $ENV_VAR } for secrets[/dim]


history-label = History

jobs = Jobs

jobs-tab = Jobs

keyboard-help-title = groket {$version}

help-brand-name = groket

help-brand-tagline = Evaluate Grok Build sessions

chrome-folder = { $label }  { $path }

launch = Launch

launch-btn = launch

launch-selected = Launch selected

load = load

load-sessions-first = Load sessions first

local-btn = Local

logs-tab = Logs

marked-count = Marked: { $n }

mcp-btn = MCP

mcp-search-placeholder = search registry or local…

mcp-server-ids-label = MCP server ids

model-filter-notify = Model filter: { $label }

models-heading = Models

new-label = New

new-persona = New persona

new-persona-title = New persona

no-session-to-save = No session to save

notes-label = Notes

open-folder-path = open folder path

open-in-runner = open in runner

optional-blurb-placeholder = Optional short blurb

optional-env-var-name = Optional env var name

optional-git-user-email = Optional → GIT_AUTHOR_EMAIL / GIT_COMMITTER_EMAIL

optional-git-user-name = Optional → GIT_AUTHOR_NAME / GIT_COMMITTER_NAME

pe-tab-env = 3 Environment

pe-tab-env-title = 3 Environment

pe-tab-github = 2 GitHub

pe-tab-github-title = 2 GitHub & Git

pe-tab-identity = 1 Identity

pe-tab-mcp = 4 MCP

pe-tab-mcp-title = 4 MCP tools

pe-tab-plugins = 6 Plugins

pe-tab-plugins-title = 6 Plugins

pe-tab-skills = 5 Skills

pe-tab-skills-title = 5 Skills

persona-exists = Persona '{ $pid }' already exists — pick another ID

persona-id-invalid = Persona ID is invalid

persona-id-label = Persona ID

persona-id-placeholder = e.g. marvin-reviewer

persona-id-required = Persona ID is required

persona-id-reserved = That ID is reserved

persona-label = Persona

persona-saved = Persona saved

personas = Personas

personas-refreshed = Personas refreshed

pick-mcp = Pick MCP…

pick-plugins = Pick plugins…

pick-skills = Pick skills…

plugins-btn = Plugins

plugins-search-placeholder = search chrome, vercel, sentry, superpowers…

plugins-title = Plugins

prompt-label = Prompt

prompt-required = Prompt is required

prompt-required-save = Prompt is required to save a config

refresh = Refresh

refresh-btn = refresh

refreshing-session-view = Refreshing session view…

registry-btn = Registry

repo-url-placeholder = https://github.com/org/repo

repo-path-label = Local path (bind-mount as /workspace)

repo-path-placeholder = ~/src/my-project (optional; live directory, no clone)

report-failed = Report failed: { $exc }

report-saved = Report saved: { $path }

export-bundle-saved = Export saved: { $path } [{ $profile }]

export-bundle-failed = Export failed: { $exc }

export-bundle-no-session = No session to export

export-bundle-working = Building export tarball…

export-profile-title = Export profile

export-profile-hint = Choose archive-full, archive-org, trace-only, or a user profile. Esc cancels.

export-profile-export = Export

repository-label = Repository

rule-detail =
    Category: { $category }
    Enabled: { $enabled }
    Detector: { $detector }
    
    Description:
    { $description }
    

rule-toggled = Rule '{ $rule_id }' { $state }

rules = Rules

rules-list-refreshed = Rules list refreshed

rules-title = Rules

discard-unsaved-title = Discard unsaved changes?

discard-unsaved-body = Leave this form and lose your edits?

ui-discard = Discard

ui-keep-editing = Keep editing

runner = Runner

runner-tab-extras = 3 Extras

runner-tab-recipe = 1 Recipe

runner-tab-runtime = 2 Runtime

runner-title = Runner

runs-label = Runs

save = Save

save-config = Save config

save-failed = Save failed: { $exc }

search = Search

search-events-placeholder = Search events…  (/)

search-sessions-placeholder = Search sessions…  (/)

search-mcp-placeholder = search MCP…

select-all = Select all

select-at-least-one-model = Select at least one model

select-container-row = Select a container row first

select-finding-first = Select a finding on the Findings tab first

select-persona-first = Select a persona first

select-session-first = Select a session first

server-id-hint = Server id  [dim][mcp_servers.THIS] key on persona[/dim]

server-id-required = Server id required

session-markers = Session markers

session-not-found = Session not found

setup-commands-label = Setup commands

share = Share

share-failed = Share failed: { $exc }

skills-btn = Skills

skills-search-placeholder = search review, implement, docx…

skills-title = Skills

tab-diff = 3 Diff

tab-findings = 4 Findings

tab-report = 5 Report

tab-summary = 2 Summary

tab-session = Session

tab-tasks = Tasks

tab-workflows = Workflows

tab-subagents = Subagents

tab-stats = Stats

tab-timeline = 1 Timeline

cmd-overview-section = Summary section

cmd-overview-section-help = Switch Session, Tasks, Workflows, Subagents, and Stats

ui-messages = Messages

ui-loops = Loops

theme = Theme

this-launch-only-dim = [dim]this launch only[/dim]

time-breakdown = Time breakdown

tip-no-analysis = No analysis yet — run analysis with `a` on the sessions list

tip-no-flags = No flags yet — select a Timeline event, then press `f`

tip-no-personas = No personas — press `n` to create one.

toggle = Toggle

token-from-host-env = Token from host environment

tool-timing = Tool timing

tools-only = Tools only

transport-endpoint = Transport / endpoint


user-messages = User messages

verdict-label = Verdict:

col-index = Index

col-turn = Turn

col-dur = Duration

col-type = Type

col-tool = Tool

col-summary = Summary

parameters-label = Parameters:

recommendation-label = Recommendation:

state-enabled = enabled

state-disabled = disabled


# --- UI strings (no hardcoded English in groket/ui) ---

ui-added-mcp = Added MCP `

ui-already-analyzed = Already analyzed
notify-analysis-in-flight = Analysis already running for { $n } session(s)

ui-already-running =  already running)

ui-analysis = Analysis:

ui-analysis-failed-for-s-s = Analysis failed for %s: %s

ui-appended-into-the-container-mcp-config = [dim]Appended into the container MCP config[/dim]

ui-args = args

ui-auth-file-not-found = Auth file not found:

ui-auth-missing = Auth missing:

ui-avg = Avg

ui-awaiting-check-failed-for-s = awaiting check failed for %s

ui-basic = Basic

ui-batch-1 = Batch

ui-branch = branch:

ui-branch-1 = Branch

ui-cache-self-test-summary-failed = cache self-test summary failed

ui-calls = Calls

ui-cancel = Cancel

ui-category-1 = Category

ui-category-2 = category:

ui-changes = Changes

ui-chars-blank-keeps-current-enter-a-value-to-repla =  chars). Blank keeps current; enter a value to replace.[/dim]

ui-cleared-run-only-mcp-skills-plugins-extras = Cleared run-only MCP/skills/plugins extras

ui-close = Close

ui-completed-in = [/bold] completed in

ui-config-s-in-flight =  config(s) in flight

ui-config-s-max = config(s), max

ui-configs-reloaded = Configs reloaded (

ui-container = Container

ui-containers = Containers

ui-could-not-reach-docker-is-the-daemon-installed-a = Could not reach Docker. Is the daemon installed and running?

ui-could-not-render-details = [red]Could not render details:

ui-create-companion-skill = Create companion skill

ui-created = Created

ui-debian-bookworm-fully-loaded = debian:bookworm @ fully-loaded

ui-delete-config = delete config

ui-delete-failed = Delete failed

ui-deleted = Deleted

ui-detach-ui-on-quit-failed = detach_ui on quit failed

ui-detector-analysis-no-sessions-loaded = [bold]Detector analysis:[/bold] [dim]no sessions loaded[/dim]

ui-detectors = Detectors

ui-diff = diff

ui-diff-1 =
    Diff
    

diff-filter = Snapshot

diff-point-prompt = Prompt { $n }

diff-point-rewind = Snapshot { $n }

diff-point-edits = Approximate edits

diff-empty-files = No file changes in this snapshot

diff-empty-session = No rewind snapshots or search_replace edits

diff-search-placeholder = Search files and hunks

diff-context-prompt = Prompt

diff-context-assistant = Assistant

diff-empty-context = (empty)
 

ui-docker-1 = Docker

ui-docker-is-available-and-running = Docker is available and running

ui-docker-is-not-available-install-docker-or-start = Docker is not available. Install Docker or start the daemon.

ui-docker-is-not-running-start-the-docker-daemon-an = Docker is not running. Start the Docker daemon and try again.

ui-docs-source-copy-url-open-on-host-browser =
    [bold]Docs / source[/bold]  [dim]copy URL · open on host browser[/dim]
    

ui-dur = Duration

ui-duration = Duration

ui-context = Context

ui-context-usage = Context usage

ui-context-tokens = Context tokens

ui-compactions = Compactions

ui-edit = Edit…

ui-elapsed = Elapsed


ui-env = Env

ui-error-1 =   ERROR

ui-error-3 = Error

ui-errors-2 = Errors

ui-event = [dim]Event #

ui-events = Events

ui-fail = FAIL

ui-failed-2 =
     failed[/red]
    [dim]

ui-failed-to-add-row-for-s = Failed to add row for %s

ui-failed-to-apply-saved-theme-r = Failed to apply saved theme %r

ui-failed-to-ensure-persona-defaults = Failed to ensure persona defaults

ui-failed-to-load-persona-capabilities-for-s = Failed to load persona capabilities for %s

ui-failed-to-load-persona-s = Failed to load persona %s

ui-failed-to-load-session-meta-for-s = Failed to load session meta for %s

ui-failed-to-mount-report-section-s = failed to mount report section %s

ui-failed-to-refresh-rules-table = Failed to refresh rules table

ui-failed-to-resolve-docker-base-for-s = Failed to resolve docker base for %s

ui-failed-to-set-docker-image-prefill = Failed to set docker image prefill

ui-failed-to-set-persona-prefill = Failed to set persona prefill

ui-failed-to-update-widget-with-renderable = Failed to update widget with renderable

ui-failed-to-validate-resolve-models-for-launch = Failed to validate/resolve models for launch

ui-failed-to-write-prefs-to-s = failed to write prefs to %s

ui-feedback = Feedback

ui-feedback-batch = [bold]Feedback batch:[/bold]

ui-field-select-report-view-select = field-select report-view-select

ui-field-select-session-filter-select = field-select session-filter-select

ui-filter = Filter

ui-timeline-tail = Tail

ui-finding =
    FINDING
    

ui-findings-1 = Findings


    

ui-finished = Finished

ui-finished-1 = finished:

ui-finished-in-1 = [/bold] finished in

ui-flagged = FLAGGED

ui-flags-2 = Flags

ui-follow-up-failed-for-s = follow-up failed for %s

ui-from-your-grok-config-toml-no-extra-def-unless-y = from your ~/.grok/config.toml (no extra def unless you configure)).[/dim]

ui-fully-loaded-full-tools-share-loop = fully-loaded (full tools + share loop)

ui-gh-write = GH Write

runner-persona-status-gh-on = {$pid} · [yellow]gh on[/] · {$token} · {$caps}

runner-persona-status-gh-off = {$pid} · gh off · {$caps}

runner-persona-caps-counts = mcp={$mcp} · skills={$skills} · plugins={$plugins}

runner-github-write-hint-on = [yellow]GitHub write ON[/] (persona) · token={$token}

runner-caps-effective-heading = [bold]Effective launch config[/] [dim](persona + this run)[/]

runner-caps-section-mcp = [bold]MCP[/] ({$n})

runner-caps-section-skills = [bold]Skills[/] ({$n})

runner-caps-section-plugins = [bold]Plugins[/] ({$n})

runner-caps-section-inline = [bold]Inline skills[/] ({$n})

runner-caps-section-env = [bold]Env[/] ({$n}): {$keys}

runner-caps-item = [green]•[/] {$name}

runner-caps-none = [dim]—[/]

runner-caps-persona-heading = [bold]Persona base[/]

runner-caps-run-heading = [bold]This run only[/]

runner-caps-persona-hint = persona={$pid} · mcp={$mcp} · skills={$skills} · plugins={$plugins}

runner-runtime-panel-heading = [bold]This launch[/] [dim](persona + extras, effective)[/]

run-config-plugins = Plugins: {$list}

run-config-inline-skills = Inline skills: {$list}

run-config-mcp = MCP: {$list}

run-config-skills = Skills: {$list}

runner-save-extras-summary = · mcp={$mcp} skills={$skills} plugins={$plugins} inline={$inline} env={$env}

runner-token-resolved = resolved

runner-token-none = none

ui-github-mcp-skills-footer-for-keys =
     · GitHub / MCP / skills · Footer + ? for keys[/dim]
    

ui-github-pat-stored-on-this-persona = github_pat_… stored on this persona

ui-github-write-is-on-but-repo-url-is-empty-set-htt = GitHub write is ON but repo URL is empty — set HTTPS repo_url matching your token scope

ui-github-write-push = GitHub write / push

ui-high-1 = High

ui-highlight-a-config-or-select-with-s-then-x-to-de = Highlight a config (or select with s), then x to delete

ui-host-env = host env

ui-host-pass-through-uses-mcp-servers = Host pass-through: uses mcp_servers.

ui-host-tools = Host tools

ui-id = ID

ui-id-2 = Id

ui-image = image:

ui-in-flight-running-in-background-j-jobs-logs-no-p =  in flight — running in background ([bold]J[/bold] = jobs/logs; no per-run popups)

ui-inactive-model-s-not-in-grok-models-models-cache = inactive model(s) (not in `grok models` / models_cache.json). Launching:

ui-inherit-from-runner-run-config = (inherit from runner / run config)

ui-input = Input

ui-interactive-follow-ups-open-the-session-in-the-b = [dim]Interactive follow-ups: open the session in the browser (pending bar while the eval is not finished).[/dim]

ui-interactive-multi-turn-follow-ups-until-done = Interactive multi-turn (follow-ups until Done)

ui-yolo-auto-approve-tools = YOLO mode (grok --yolo; more aggressive auto-approve)

ui-jobs-for-logs-esc-closes-jobs-run-keeps-going =  — Jobs for logs (Esc closes Jobs; run keeps going)

ui-label = Label

ui-label-1 = label:

ui-last = last:

ui-last-outcome = Last outcome

ui-last-turn = Last turn

ui-launch = Launch:

ui-launch-error-s-open-j-for-jobs-logs =  launch error(s). Open [bold]J[/bold] for jobs/logs.

ui-launch-pick-models = launch (pick models)

ui-launch-selected = Launch selected

ui-launch-selected-2 = launch selected

ui-launched = Launched

ui-launches = Launches

ui-launches-1 = launches:

ui-leave-blank-to-keep-existing-token = (leave blank to keep existing token)

ui-local = Local

ui-local-catalog-entry-configure-to-set-headers-env = [dim]Local/catalog entry — configure to set headers/env; no registry docs page unless you added one.[/dim]

ui-mark-done-failed-for-s = mark done failed for %s

ui-max-configs-in-flight-at-once-each-config-still = Max configs in flight at once (each config still runs all its models together):

ui-mcp = MCP

ui-mcp-2 = MCP ·

ui-mcp-bridge-calls = mcp bridge calls

ui-med = Med

ui-minimal-baseline-share-loop-setup-sh-for-rest = minimal (baseline + share loop; setup.sh for rest)

ui-model = Model

ui-model-s =  model(s)

ui-model-s-from-config = model(s) from config

ui-models = Models

ui-models-1 = models:

ui-models-optional-override-leave-none-selected-to = Models (optional override). Leave none selected to use each config's saved models (falls back to app defaults):

ui-models-select-one-or-more-one-container-each = Models (select one or more — one container each):

ui-more-2 =  more)

ui-msg-2 = [\s,]+

ui-name = Name

ui-needs-env = [dim]needs env:[/dim]

ui-new = New

ui-new-in-runner = new in runner

ui-no-active-models-check-config-models-vs-grok-mod = No active models (check config models vs `grok models`)

ui-no-active-models-to-launch-edit-the-models-field = No active models to launch — edit the models field to match `grok models`

ui-no-active-runs-fill-the-form-and-press-launch = No active runs — fill the form and press Launch

ui-no-companion-skill = ` (no companion skill).

ui-no-config-selected = No config selected

ui-no-config-under-cursor = No config under cursor

ui-no-description = (no description)

ui-no-diff-data =
      (no diff data)
    

ui-no-diff-data-1 = (no diff data)

ui-no-docs-repo-url-from-registry-search-the-server = [dim]No docs/repo URL from registry — search the server name on registry.modelcontextprotocol.io[/dim]

ui-no-findings =
      (no findings)
    

ui-no-models = : no models

ui-no-models-for = No models for

ui-no-note = no note

ui-no-prompt-extracted = (no prompt extracted)

ui-no-row-selected = [dim]No row selected.[/dim]

ui-no-saved-configs-yet-save-a-recipe-from-the-runn = [dim]No saved configs yet. Save a recipe from the Runner, or create with New.[/dim]

ui-no-timeline = (no timeline)

ui-no-token-stored-yet = [dim]No token stored yet.[/dim]

ui-none =
      (none)
    

ui-none-host-groket-gh-token-gh-token-only-if-orche = none (host GH_TOKEN only if orchestrator allows)

ui-none-run-defaults-only = none (run defaults only)

ui-none-saved = (none saved)

ui-noop = Noop

ui-note-1 = Note

ui-nothing-to-launch-no-models = Nothing to launch (no models)

ui-copied-selection = Copied selection to clipboard

ui-copied-detail = Copied detail to clipboard

ui-copied-report = Copied report to clipboard

ui-copied-content = Copied to clipboard

ui-copied-finding = Copied finding (Issue box) to clipboard

ui-nothing-to-copy = Nothing to copy

ui-press-key-to-quit = Press [b]{$key}[/b] to quit the app

ui-want-to-quit-title = Do you want to quit?

ui-ok-2 = ok

ui-ok-3 = OK

ui-open-in-runner = open in runner

ui-other = Other

ui-outcome = Outcome

ui-overall-fail =   Overall: FAIL  (

ui-overall-pass-required-checks-ok =
      Overall: PASS (required checks ok)
    

ui-panel-card-panel-card-grow = panel-card panel-card-grow

ui-panel-card-report-section = panel-card report-section

ui-parallelism = parallelism:

ui-path = Path

ui-persona-1 =  · persona=

ui-persona-2 = Persona

ui-persona-token-stored = stored ({$n} chars)

ui-persona-token-host-env = host env {$name}

ui-persona-github-line = github_write={$write} token={$token} docker={$docker}

ui-persona-git-line = git: {$name} <{$email}>

ui-persona-env-line = env keys: {$keys}

ui-persona-mcp-line = MCP servers ({$n}): {$ids} replace_host={$replace}

ui-persona-skills-line = Skills ({$n}): {$ids}

ui-persona-notes-line = notes: {$notes}


ui-persona-builder = [bold]Persona builder[/bold]  [dim]

ui-persona-has-github-write-on-but-no-token-set-pat = Persona has GitHub write ON but no token — set PAT (or token env) on the persona (new / manage…), or export GH_TOKEN/GITHUB_TOKEN on the host

ui-persona-ready = Persona ready:

ui-personastore-initialization-failed = PersonaStore initialization failed

ui-plan = Plan

ui-planning = Planning

ui-plugin-s-1 =  plugin(s)

ui-plugins-persona-unchanged =  plugins (persona unchanged)

ui-press-again-to-delete = Press [x] again to DELETE

ui-prompt = prompt:


ui-recipes =  recipes).

ui-registry = Registry

ui-registry-2 = [cyan]registry[/cyan]

ui-registry-3 = [dim]registry:[/dim]

ui-registry-type-a-query-enter = registry · type a query · enter

ui-reload-meta-failed-for-s = reload meta failed for %s

ui-replace-host-mcp-persona-only = Replace host MCP (persona only)

ui-repo = Repo

ui-repo-1 = repo:

ui-repo-path-1 = path:

ui-repo-path-requires-single-model = Local path mounts a live host directory — select a single model only

ui-report =  (report)

ui-report-static-s-missing = report static %s missing

ui-report-unavailable = report unavailable

ui-report-uses-selected =  [dim](report uses selected)[/dim]

ui-report-view-select-sync-failed = report view select sync failed

ui-repository = [cyan]repository[/cyan]

ui-required = required,

ui-run-1 = Run

ui-run-2 = Run [bold]

ui-run-config-s =  run config(s)

ui-run-config-s-recipes-only-sessions-traces-kept = run config(s) (recipes only — sessions/traces kept):

ui-run-crashed = Run crashed:

ui-run-env-saved = Saved {$count} run env var(s)

ui-inline-skill-saved = Inline skill saved: {$name}

ui-run-extras = Run extras:

ui-run-s-active-1 = [/bold] run(s) active —

ui-run-s-keep-going-in-docker-j-jobs-logs-quit-anyt =  run(s) keep going in docker — [bold]J[/bold] jobs/logs; quit anytime (relaunch prunes finished eval containers)

ui-run-s-started = run(s) started,

ui-running-checks = Running checks…

ui-save = Save

ui-save-failed = Save failed:

ui-save-persona-to-keep = save persona to keep

ui-save-run-config-launch-to-keep-persona-unchanged = save run config / launch to keep (persona unchanged)

ui-saved-config = Saved config

ui-saved-run-configs-recipes-in-runs-run-configs-no =
    [bold]Saved run configs[/bold]  [dim]recipes in runs/run_configs/ (not sessions). [bold]s[/bold]/space toggle select · [bold]S[/bold] select all · [bold]w[/bold] launch selected (multi) · [bold]l[/bold] launch one · [bold]x[/bold] twice to delete · [bold]Enter[/bold] edit in runner.[/dim]
    

ui-sel = Sel

ui-select-a-row-for-description-endpoint-env-needs = [dim]Select a row (↑↓) for description, endpoint, env needs, and doc links.[/dim]

ui-select-at-least-one-model = Select at least one model

ui-select-configs-with-s-space-or-cursor-on-one-the = Select configs with s/space (or cursor on one) then w

ui-select-sessions-with-s-or-highlight-a-row-then-p = Select sessions with [s]/S] or highlight a row, then press [x] to delete

ui-selected-4 =  [green]● selected[/green]

ui-selected-5 =  (selected)

ui-selection-none-s-toggle-row-s-all-none-w-launche = [dim]Selection: none — [bold]s[/bold] toggle row · [bold]S[/bold] all/none · [bold]w[/bold] launches cursor config only if nothing selected.[/dim]

ui-self-test = Self-test

ui-self-test-external-dependencies = Self-test — external dependencies

ui-self-test-fail = self-test FAIL×

ui-self-test-ok-warns = self-test OK ({ $n } warn)
ui-blm-title = Launch selected ({ $n } config(s)): { $names }
ui-selection-bar = Selection: { $n }  { $labels }{ $extra } — press w to launch selected
ui-config-detail-title = { $name }  id={ $id }{ $sel }

ui-self-test-pass = self-test PASS

ui-session-2 = Session

ui-session-error-1 = [bold red]session error[/]

ui-session-id = Session ID

ui-session-model-select-update-failed = session model select update failed

ui-session-report =
    Session report
    

ui-set-models-above = ; set models above

ui-setup = setup:

ui-share = Share

ui-share-meta-failed = share meta failed

ui-skill = ` + skill `

ui-skill-not-written = ` (skill not written).

ui-skill-write-failed = `; skill write failed:

ui-skills-1 = Skills

ui-skills-4 = skills ·

ui-skip = Skip

ui-skipping = Skipping

ui-source = Source

ui-source-1 = source

ui-source-run-id = source run_id:

ui-source-session = source session:

ui-space-click-to-select-no-selection-per-config-mo = [dim]space/click to select · no selection = per-config models[/dim]

ui-started = Started

ui-status = Status

ui-stdio-needs-tools-in-image = [dim]stdio needs tools in image[/dim]

ui-stop-live-refresh-on-quit-failed = stop live refresh on quit failed

ui-stored-on-persona = stored on persona

ui-background-filter = Background

ui-background-jobs = Background

ui-background-none = No background jobs

ui-workflows = Workflows

ui-workflows-filter = Workflows

ui-workflows-none = No workflow runs

ui-workflow = workflow

ui-workflow-done = workflow done

ui-workflow-missing = No workflow run on disk

ui-workflow-agent-count = {$used}/{$budget} agents

ui-inspect-asked = Asked

ui-inspect-happened = Happened

ui-inspect-failed = Failed

ui-phase = Phase

ui-agents = Agents

ui-status-interrupted = interrupted

ui-schedule = schedule

ui-scheduled = scheduled

ui-subagent = Subagent

ui-subagent-runs = Subagent runs

ui-subagent-none = No subagent runs

ui-subagent-missing = Child session is not on disk

ui-task-no-timeline = No Timeline bookend for this row

ui-subagent-opened = Opened subagent session

ui-status-running = running

ui-status-failed = failed

ui-subagents-filter = Subagents

ui-open-child-enter = Enter opens this child

title-browser-extra-subagent = · subagent

ui-succeeded =  succeeded[/green], [red]

ui-succeeded-1 =  succeeded[/green]

ui-task = Task

ui-task-1 = task:

ui-thinking = Thinking

ui-thought = Thought

ui-title = Title

ui-token-on-file-yes = [dim]Token on file: yes (

ui-token-status-unknown = token status unknown

ui-tool-1 = Tool

ui-tool-execution = Tool execution

ui-tools = Tools

ui-total-1 = Total

ui-total-2 = TOTAL

ui-transport = transport

ui-truncated-1 =
    
    … truncated …

ui-truncated-see-rewind-points-jsonl-updates =
    
    
    … (truncated; see rewind_points.jsonl / updates)
    
    

ui-turn-ended = turn ended

ui-turn-in-progress = turn in progress

ui-turn-segmentation-failed = turn segmentation failed

ui-turn-started = turn started

ui-turns-1 = Turns

ui-host-tool-errors = ({$n} errors)

ui-host-tool-calls = {$n}×

ui-host-tool-total = total {$n}

ui-err-count = {$n} err

ui-err-count-paren = ({$n} err)

ui-mcp-method-line = · {$method} {$calls}×

ui-mcp-search-count = · {$n} search_tool query(ies)

ui-skill-line = {$id} — {$bits}

ui-skill-loaded = loaded {$n}×

ui-sources-line = sources: {$notes}

ui-ubuntu-24-04-fully-loaded = ubuntu:24.04 @ fully-loaded

ui-ubuntu-24-04-raw-os-still-share-loop-via-entrypo = ubuntu:24.04 (raw OS; still share loop via entrypoint)


ui-updated-config = Updated config

ui-user-input = User input

ui-warn-2 = WARN

ui-warnings =
     warnings)
    

ui-what-the-model-should-have-done = *What the model should have done:*

ui-will-be-git-fetched-into-the-container-volume-at = ` will be git-fetched into the container volume at launch

ui-with = with

ui-workers-cancel-on-quit-failed = workers cancel on quit failed

ui-writing = Writing



# interpolating fragments / templates
ui-turn-number = Turn { $turn }
ui-queued-count = { $n } queued
ui-staged-follow-up = follow-up staged
ui-staged-last-turn = last turn staged
ui-session-prefix = Session { $id }
ui-failed-paren =  ({ $n } failed)
ui-batch-err-example = 
    e.g. { $id }: { $sample }
# Activity bar (compact)
activity-pending = Pending { $n }
activity-building = Building { $n }
activity-running = Running { $n }
activity-ending = Ending { $n }
activity-extracting = Extracting { $n }
activity-awaiting = Awaiting { $n }
activity-analysis = Analysis { $n }
activity-sessions = Sessions { $n }

# Session list turn / status column
status-running = running
status-ending = ending
status-ending-done = ending (stop requested)
status-ending-last-turn = ending (last turn)
status-cancelled = cancelled
status-complete = complete
status-waiting-prompt = awaiting
status-unknown = —

# Follow-up shortcut (sessions home + browser)
bind-next-prompt = Next
bind-end-session = Done
cmd-next-prompt = Next prompt
cmd-next-prompt-help = Send follow-up to awaiting sessions
cmd-end-session = End session
cmd-end-session-help = Mark awaiting sessions done
turn-filter-all = All turns
turn-filter-n = Turn { $n }
turn-filter-unnumbered = Unnumbered

ui-press-again-to-delete-persona = Press [x] again to DELETE persona
# --- Composed UI (prefer these over fragment glue; no edge whitespace) ---
tool-detail-heading = #{ $index } tool { $name }
tool-output-rule = Output ({ $n } chars)
tool-output-rule-cleaned = Output ({ $n } chars, cleaned from { $raw })
tool-input-file = File: { $path }
tool-input-target-file = target_file: { $path }
tool-input-target-directory = target_directory: { $path }
tool-mcp-label = MCP tool: { $name }
tool-input-section = tool_input:
tool-field-pattern = pattern:
tool-field-query = query:
tool-field-old-string = old_string:
tool-field-new-string = new_string:
tool-no-input = (no input)
tool-binary-output = (binary / control-only output)
tool-empty-output = (empty)
tool-image-path = Saved image: { $path }
notify-scanning = Scanning { $path }…
notify-no-sessions = No sessions found in { $path }
notify-control-list-failed = Control catalog failed: { $err }
notify-control-session-failed = Control session load failed: { $err }
notify-loaded-sessions = Loaded { $n } sessions
notify-analyzing = Analyzing { $n } sessions ({ $plugins } plugins)…
notify-analysis-complete = Analysis complete — { $n } sessions
notify-model-filter = Model filter: { $label }
flagged-at-when = Flagged at { $when }
truncate-marker = …truncated…
truncate-for-display = …truncated for display…
sessions-home-summary = { $total } sessions · { $findings } findings · { $high } high
sessions-selected-count = { $n } selected
sessions-pending-analysis = { $n } pending analysis

# --- Session / jobs / personas notifies (composed) ---
notify-saved-run-config = Saved run config { $id } ({ $name }) — open with C (configs); sessions unchanged
notify-save-config-failed = Save config failed: { $exc }
notify-failed-for = Failed for { $errors }/{ $total }
notify-press-again-delete-sessions = Press again to delete { $n } session(s) from disk (traces, feedback cache, run configs)
notify-deleted-sessions = Deleted { $deleted }/{ $requested } session(s)
notify-deleted-sessions-errors = Deleted { $deleted }/{ $requested } session(s) — { $errors } error(s){ $hint }
notify-no-traces-refresh = No traces dir to refresh: { $path }
notify-full-refresh = Full refresh from { $path } (background)…
notify-refresh-all-failed = Refresh all failed: { $error }
notify-refresh-done = Refresh done — sessions { $sessions }, analyzed { $analyzed }, errs { $errors }
notify-nothing-to-refresh = Nothing to refresh under { $path }
notify-run-failed = Run { $id } failed after { $elapsed }: { $error }
notify-run-finished = Run { $id } finished in { $elapsed }: { $ok }/{ $total } ok, { $failed } failed
title-browser-session = Browser — { $label } ({ $model }){ $extra }
title-chrome-session = { $brand } · { $label }
title-chrome-subagent = { $brand } · { $kind } · { $label }
title-chrome-subagent-under = { $brand } · { $parent } · { $kind } · { $label }
notify-turn-ended-outcome = Turn ended with outcome { $outcome } — see Summary tab or session/session_error timeline events
notify-queued-follow-up-sent = Queued follow-up sent: { $preview }
notify-open-session-failed = Open session failed: { $exc }
notify-no-session-yet = No session yet for { $container } — wait for traces to appear
notify-share-open-failed = Could not open share for { $name }: { $exc }
jobs-banner-runs = Docker eval runs: { $n } active{ $latest }
jobs-analysis-inflight = Analysis: { $n } in flight · { $cached } session(s) cached in this TUI
jobs-analysis-cached = Analysis: { $n } session(s) cached in this TUI
jobs-analysis-idle = Analysis: idle (no cached results in this TUI yet)
jobs-control-attached = Control: attached · { $path }
jobs-control-offline = Control: offline (this TUI only)
jobs-work-dir = work dir: { $path }
persona-registry-searching = Registry searching { $query }…
persona-registry-error = Registry error: { $error }
persona-registry-hits = Registry: { $n } for { $query }{ $extra }
persona-local-count = Local { $n } · r=registry
persona-saved = Saved persona { $pid }
persona-configure-title = Configure { $name }
analysis-settings-help = Enabled analyzers: { $list }. Optional plugins: analysis.plugins as module:ClassName (active config: { $config }).
browser-findings-chip = { $n } findings
browser-high-chip = { $n } high
browser-medium-chip = { $n } medium
browser-flags-count = Flags ({ $n })

browser-notes-count = Notes ({ $n })
browser-findings-dim = · { $n } findings
browser-status-none = none
browser-status-clean = clean
browser-status-idle = idle
browser-follow-ups-pending = { $n } follow-up(s) pending
browser-follow-up-staged = Follow-up staged (waiting for agent)
browser-follow-up-staged-final = Last turn staged (session ends after this turn)
browser-more-queued = … +{ $n } more
browser-report-counts = { $total } findings ({ $high } high, { $med } med)
browser-flags-dim = { $n } flags
browser-viewing-focus = Viewing: { $focus }
browser-finding-events = { $n } events
browser-more-children = … +{ $n } more
browser-report-error = Error: { $msg }
browser-skill-mounted = mounted
browser-skill-seen = seen
browser-last-turn-outcome-note = Last turn outcome={ $outcome } (session meta = last finished turn)
title-browser-extra-turn = · turn={ $outcome }
title-browser-extra-live-turn = · LIVE turn={ $outcome }
title-browser-extra-ending = · ending session
title-browser-extra-awaiting = · awaiting follow-up
report-md-model = *Model:* `{ $model }`
report-md-session = *Session:* `{ $id }`
report-md-plugin = *Plugin:* `{ $id }`
report-md-finding = *Finding:* `{ $id }`
report-md-severity = *Severity:* { $sev }
report-md-category = *Category:* { $cat }
report-md-sub-findings = *{ $n } sub-finding(s):*
notify-delete-session-arm = Press [x] again to DELETE 1 session(s) from disk (traces + feedback_cache; run configs are kept)
notify-deleted-sessions = Deleted { $deleted }/{ $requested } session(s){ $err_suffix }
notify-deleted-sessions-errors = , errors={ $n }
count-events = { $n } events
count-tools = { $n } tools
count-turns = { $n } turns
ui-mcp-pick-sel = {$n} selected · {$configured} configured · {$ids}
ui-plugins-pick-sel = Selected ({$n}): {$ids}
ui-skills-pick-sel = {$n} selected · {$ids}

bind-export-task = Export task

cmd-export-task = Export as task YAML
cmd-export-task-help = Write a batch tasks YAML from this recipe or form (choose path)

export-task-title = Export as task YAML
export-task-hint = Path for the tasks catalog file (default under ~/.groket/tasks/).
export-task-placeholder = ~/.groket/tasks/my-task.yaml
export-task-saved = Task YAML saved: { $path }
export-task-failed = Export task failed: { $exc }
export-task-no-prompt = Prompt is required to export a task
export-task-no-config = No recipe selected to export
path-input-empty = Enter a file path

bind-show-host = Show host
bind-hide-host = Hide host

cmd-show-host-sessions = Show host sessions
cmd-show-host-sessions-help = Include native ~/.grok/sessions on the sessions list
cmd-hide-host-sessions = Hide host sessions
cmd-hide-host-sessions-help = Hide native ~/.grok/sessions from the sessions list

ui-origin = Origin
ui-origin-work = Eval
ui-origin-host = Host

ui-control-socket-attach-failed = Control owner not reachable

ui-control-owner-stale = control owner is older · run: groket serve restart
notify-host-sessions-on = Host sessions shown (native ~/.grok/sessions)
notify-host-sessions-off = Host sessions hidden

