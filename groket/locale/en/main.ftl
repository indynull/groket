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

all-tasks = All tasks

all-turns = All turns


analysis-complete = Analysis complete -- { $n } sessions

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
analysis-stale-banner = [bold yellow]Stale analysis[/] — {$detail}. Ctrl+P → Analyze this session (J → Activity).
analysis-stale-findings-row = [yellow]Stale[/] — {$detail} · Ctrl+P → Analyze this session
analysis-stale-report = [bold yellow]Stale analysis[/] — {$detail}. Ctrl+P → Analyze this session.

back = Back

bind-analyze = Analyze

bind-back = Back

bind-cancel = Cancel

bind-clear-logs = Clear logs

bind-clear-view = Clear view

bind-close = Close

bind-configs = Configs

bind-delete = Delete

bind-diff = 3 Diff

bind-disable-all = Disable all

bind-docker = Docker

bind-done = Done

bind-edit = Edit

bind-enable-all = Enable all

bind-environment = 3 Environment

bind-export-finding = Export finding

bind-export-bundle = Export

bind-extras = Extras

bind-findings = 4 Findings

bind-flag = Flag
bind-copy-detail = Copy

bind-github = 2 GitHub

bind-help = Help

ui-leader = Leader

bind-identity = 1 Identity

bind-jobs = Jobs

bind-launch = Launch

bind-launch-selected = Launch selected

bind-mcp = 4 MCP

bind-model = Model

bind-models = Models

bind-new = New

bind-new-persona = New persona

bind-note = Note

bind-edit-note = Edit note

bind-next-pane = Next pane

bind-next-tab = Next tab

bind-open = Open

bind-personas = Personas

bind-plugins = 6 Plugins

bind-prev-pane = Prev pane
bind-pane-digit = Pane

bind-prev-tab = Previous tab

bind-quit = Quit

bind-recipe = Recipe

bind-refresh = Refresh

bind-report = 5 Report

bind-rerun = Re-run

bind-resume = Fork

bind-rules = Rules

bind-runner = Runner

bind-runtime = Runtime

bind-save = Save

bind-save-cfg = Save cfg

bind-search = Search

bind-select = Select

bind-select-all = Select all

bind-send-follow-up = Send follow-up

bind-mark-session-done = Mark done

bind-focus-follow-up = Follow-up input

bind-share = Share

bind-skills = 5 Skills

bind-summary = 2 Summary

bind-task = Task

bind-timeline = 1 Timeline

bind-toggle = Toggle

bind-view = View

branch-placeholder = branch (default: main)

cancel = Cancel

clear-btn = Clear

clear-logs-btn = clear logs

clear-view = Clear view

close = Close

close-btn = close

cmd-analysis-settings = Analysis settings

cmd-analysis-settings-help = Configure analysis plugins (config.json)

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

cmd-follow-up-sessions = Send follow-up

cmd-follow-up-sessions-help = Prompt and send follow-up to selected awaiting sessions

cmd-mark-sessions-done = Mark done

cmd-mark-sessions-done-help = Mark selected awaiting sessions done

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

mark-session-done-ok = Session marked done — stopping eval container

mark-done-requested = Done requested — session stays live until the current turn finishes

mark-sessions-done-requested = Done requested for { $n } session(s) — ending until the current turn finishes

follow-up-sent-final-n = Last-turn follow-up sent for { $n } session(s) — ending after this turn

mark-session-done-failed = Mark done failed: { $exc }

interactive-modal-title = Follow-up ({ $n } awaiting)

mark-done = Done

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

cmd-copy-detail = Copy selection, finding, or pane
cmd-copy-detail-help = Browser: yank selection; Findings row Issue box; focused Report/detail body; else whole pane — y / Ctrl+Shift+C

cmd-filter-model = Filter by model

cmd-filter-model-help = Cycle sessions Model Select (same as Filter bar)

cmd-filter-task = Filter by task

cmd-filter-task-help = Cycle sessions Task Select (same as Filter bar)

cmd-focus-search = Focus search

cmd-focus-search-help = Focus timeline search (/)

cmd-focus-timeline-view = Focus timeline view

cmd-focus-timeline-view-help = v — View dropdown (visible selection)

cmd-full-refresh = Full refresh (sessions + detectors + feedback)

cmd-full-refresh-help = Rescan traces, re-run detectors, force feedback analyze+draft

cmd-help = Help

cmd-help-help = Show key / workflow help

cmd-jobs-logs = Jobs / logs

cmd-jobs-logs-help = Runs, analysis/refresh activity log, and container logs (J)

jobs-activity-tab = Activity
jobs-activity-help = Analysis and refresh pool log.
jobs-activity-status = {$spin} analysis {$analysis}/{$analysis_workers} · refresh {$refresh}/{$refresh_workers}

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

cmd-next-tab = Next tab

cmd-next-tab-help = ] — cycle session tabs

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

cmd-prev-tab = Previous tab

cmd-prev-tab-help = [ — cycle session tabs

cmd-quit = Quit

cmd-quit-help = Quit the application

cmd-refresh = Refresh

cmd-refresh-help = Refresh the current screen / context (F5)

cmd-reload-traces = Reload traces path

cmd-reload-traces-help = Re-load from traces input path

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

cmd-tab-diff = Tab: Diff

cmd-tab-diff-help = 3 — workspace diff

cmd-tab-findings = Tab: Findings

cmd-tab-findings-help = 4 / i — detector findings

cmd-tab-report = Tab: Report

cmd-tab-report-help = 5 — analysis report

cmd-tab-summary = Tab: Summary

cmd-tab-summary-help = 2 — session summary and usage tables

cmd-tab-timeline = Tab: Timeline

cmd-tab-timeline-help = 1 — timeline + detail

cmd-toggle-rule = Toggle rule

cmd-toggle-rule-help = Enable/disable highlighted rule

cmd-toggle-select = Toggle select

cmd-toggle-select-help = Select/deselect current session row

cmd-toggle-select-config = Toggle select

cmd-toggle-select-config-help = Mark/unmark row for multi-config launch (s/space)

cmd-toggle-tips = Toggle tips / callouts

cmd-toggle-tips-help = Show or hide tip/info/warning frames app-wide

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

config-name-label = Config name

config-name-placeholder = e.g. redis-memory-leak / scratch-python-cli

configs = Configs

configs-reloaded = Configs reloaded ({ $n } recipes).

configs-title = Configs

container-image-label = Container image

max-turns-label = Max turns (per prompt)

max-turns-placeholder = 50

containers-heading = Containers

could-not-open-share = Could not open share: { $exc }

default-docker-image-label = Default Docker image

delete = Delete

delete-config-btn = delete config

delete-failed = Delete failed

deleted-persona = Deleted persona { $pid }

description-field-label = Description

description-label = Description:

disable-all = Disable all

disable-all-btn = disable all

disabled-skills = Disabled skills

display-name-label = Display name

docker = Docker

docker-unavailable = Docker is not available. Install Docker or start the daemon.

done = Done

edit = Edit

edit-flag-title = Edit Flag

edit-persona-title = Edit persona · { $pid }

em-dash-dim = [dim]—[/dim]

enable-all = Enable all

enable-all-btn = Enable all

enabled-no = No

enabled-skill-names = Enabled skill names

enabled-yes = Yes

enter-traces-path = Enter a path to traces directory

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

inline-skill-body-placeholder = Steps and guidance for the agent (not docs — actionable prompt text).

inline-skill-body-hint = Body only — frontmatter is built from id + description on save.

inline-skill-name-required = Skill id is required

inline-skill-description-required = Description is required (controls when Grok uses the skill)

inline-skill-name-invalid = Skill id must be 2–64 chars: start/end with letter or digit; only a–z, 0–9, hyphens

extra-mcp-toml = Extra MCP TOML

extras = Extras

filter-label = Filter

turn-filter-label = Turn

findings-heading = Findings

flag-event-title = Flag Event

flag-label = Flag

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

help-label = Help


highlight-config-first = Highlight a config row first

highlight-then-x-delete = Highlight a config (or select with s), then x to delete

history-label = History

jobs = Jobs

jobs-tab = Jobs

keyboard-help-title = Help

help-brand-name = groket

help-brand-tagline = Evaluate Grok Build sessions

chrome-folder = { $label }  { $path }

launch = Launch

launch-btn = launch

launch-pick-models = launch (pick models)

launch-selected = Launch selected

launch-selected-btn = launch selected

launch-selected-title = Launch selected

load = load

load-groket-home = ~/.groket

load-sessions-first = Load sessions first

local-btn = Local

logs-tab = Logs

mark-or-highlight-then-w = Mark rows with s/space, or highlight one row, then w

marked-count = Marked: { $n }

max-configs-in-flight = Max configs in flight at once (each config still runs all its models together):

mcp-btn = MCP

mcp-search-placeholder = search registry or local…

mcp-server-ids-label = MCP server ids

model-filter = Model

model-filter-notify = Model filter: { $label }

models-heading = Models

models-optional-override = Models (optional override for this launch)

models-select-one-or-more = Models (select one or more — one container each):

new-in-runner = new in runner

new-label = New

new-persona = New persona

new-persona-title = New persona

next-pane = Next pane

next-tab = Next tab

no-config-selected = No config selected

no-configs-hint = no configs — n new

no-session-to-save = No session to save

notes-label = Notes

nothing-to-launch-no-models = Nothing to launch (no models)

open-folder-path = open folder path

open-in-runner = open in runner

open-label = Open

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

persona-builder-banner =
    [bold]Persona builder[/bold]  [dim]{ $root } · GitHub / MCP / skills · Footer + ? for keys[/dim]
    

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

prev-pane = Prev pane

prev-tab = Previous tab

prompt-label = Prompt

prompt-required = Prompt is required

prompt-required-save = Prompt is required to save a config

quit-label = Quit

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

save-cfg = Save cfg

save-config = Save config

save-failed = Save failed: { $exc }

saved-persona = Saved persona { $pid }

search = Search

search-events-placeholder = Search events…  (/)

search-sessions-placeholder = Search sessions…  (/)

search-mcp-placeholder = search MCP…

select-all = Select all

select-at-least-one-model = Select at least one model

select-configs-then-w = Select configs with s/space (or highlight one) then w

select-container-row = Select a container row first

select-event-timeline = Select an event from the timeline

select-finding-first = Select a finding on the Findings tab first

select-label = Select

select-persona-first = Select a persona first

select-session-first = Select a session first

server-id-hint = Server id  [dim][mcp_servers.THIS] key on persona[/dim]

server-id-required = Server id required

session-markers = Session markers

session-not-found = Session not found

setup-commands-label = Setup commands

share = Share

share-failed = Share failed: { $exc }

show-tips-checkbox = Show tips & callouts (tip / info / note / warning …)

skills-btn = Skills

skills-search-placeholder = search review, implement, docx…

skills-title = Skills

tab-diff = 3 Diff

tab-findings = 4 Findings

tab-report = 5 Report

tab-summary = 2 Summary

tab-timeline = 1 Timeline

task-filter = Task

task-filter-notify = Task filter: { $label }

theme = Theme

this-launch-only = this launch only

this-launch-only-dim = [dim]this launch only[/dim]

time-breakdown = Time breakdown

tip-blm-models = `space` mark models · none marked = each recipe's saved models

tip-findings-row = Select a row to focus the matching timeline events

tip-mcp-pick = Search; `s` / `space` select · `r` registry · `l` local · `Ctrl+S` done

tip-no-analysis = No analysis yet — run analysis with `a` on the sessions list

tip-no-flags = No flags yet — select a Timeline event, then press `f`

tip-no-personas = No personas — press `n` to create one.

tip-persona-editor = `[` `]` panes · `1`–`6` jump · `Ctrl+S` save · `Esc` cancel

tip-plugins-pick = `s` / `space` select · `Up` / `Down` detail · `Ctrl+S` done · `Esc` cancel

tip-rc-has-marked = `w` launches the marked recipes

tip-rc-none-marked = `s` / `space` mark rows · `S` all/none · `w` runs marked recipes (or the highlighted row if none marked)

tip-report-filter = Use Filter above (same as Timeline) — All, Flags, or one plugin

tip-runner-models = `space` toggle models · `F5` refresh catalog

tip-runner-toolbar = `Ctrl+Enter` / `Ctrl+J` launch · `Ctrl+S` save · `[` `]` panes · `J` jobs · `p` personas · `Esc` back

tip-share-url = Press `s` to open the share URL

tip-skills-pick = `s` / `space` select · `Ctrl+S` done · `Esc` cancel

tips-off = Tips & callouts: off (hidden)

tips-on = Tips & callouts: on

toggle = Toggle

token-from-host-env = Token from host environment

tool-timing = Tool timing

tools-only = Tools only

traces-path-placeholder = Path to traces directory...

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

ui-a-analyze =

ui-active =  active

ui-activity-bar-refresh-failed = activity bar refresh failed

    
        ActivityBar {'{'{'}'}
            dock: top;
            height: 1;
            width: 100%;
            background: $boost;
            color: $text;
            padding: 0 1;
            text-style: none;
        {'}'}
        

ui-added-mcp = Added MCP `

ui-already-analyzed = Already analyzed
ui-analysis-in-flight = Analysis already in progress for this session
notify-analysis-in-flight = Analysis already running for { $n } session(s)

ui-already-running =  already running)

ui-analysis = Analysis:

ui-analysis-complete = Analysis complete --

ui-analysis-failed-for-s-s = Analysis failed for %s: %s

ui-analysis-service-initialization-failed = Analysis service initialization failed

ui-analyze = analyze

ui-analyze-0 = analyze 0

ui-analyzed =  analyzed=

ui-analyzed-1 = analyzed

ui-analyzing = Analyzing

ui-appended-into-the-container-mcp-config = [dim]Appended into the container MCP config[/dim]

ui-args = args

ui-assistant = Assistant

ui-asst = Assistant

ui-asst-1 =   asst=

ui-auth-file-not-found = Auth file not found:

ui-auth-missing = Auth missing:

ui-avg = Avg

ui-await-follow = await follow

ui-awaiting-check-failed-for-s = awaiting check failed for %s

ui-background =  (background)…

ui-background-runs-finished-batch-quiet-press-j-for = Background runs finished (batch/quiet). Press [bold]j[/bold] for jobs/logs; F5 refresh; launch another anytime.

ui-basic = Basic

ui-batch =   batch=

ui-batch-1 = Batch

ui-batch-2 = batch [bold]

ui-binary-control-only-output = (binary / control-only output)

ui-bold-black-on-cyan = bold black on cyan

ui-bold-black-on-green = bold black on green

ui-bold-black-on-white = bold black on white

ui-bold-black-on-yellow = bold black on yellow

ui-bold-white-on-blue = bold white on blue

ui-bold-white-on-red = bold white on red

ui-branch = branch:

ui-branch-1 = Branch

ui-browser = Browser —

ui-building = [cyan]Building…[/]

ui-cache-self-test-summary-failed = cache self-test summary failed

ui-calls = Calls

ui-cancel = Cancel

ui-category = *Category:*

ui-category-1 = Category

ui-category-2 = category:

ui-changes = Changes

ui-chars =  chars

ui-chars-1 = chars

ui-chars-blank-keeps-current-enter-a-value-to-repla =  chars). Blank keeps current; enter a value to replace.[/dim]

ui-cleaned-from = , cleaned from

ui-cleared-run-only-mcp-skills-plugins-extras = Cleared run-only MCP/skills/plugins extras

ui-close = Close

ui-completed-in = [/bold] completed in

ui-config-s =
     config(s)[/dim]
    [dim]

ui-config-s-in-flight =  config(s) in flight

ui-config-s-max = config(s), max

ui-configs-reloaded = Configs reloaded (

ui-configure = Configure

ui-configured = configured

ui-container = Container

ui-containers = Containers

ui-content = Content

ui-could-not-open-share-for = Could not open share for

ui-could-not-reach-docker-is-the-daemon-installed-a = Could not reach Docker. Is the daemon installed and running?

ui-could-not-render-details = [red]Could not render details:

ui-crashed-after = [/bold] crashed after

ui-create-companion-skill = Create companion skill

ui-created = Created

ui-ctrl-t-self-test = Ctrl+T self-test

ui-danger = danger

ui-debian-bookworm-fully-loaded = debian:bookworm @ fully-loaded

ui-default-image = [dim]default image[/dim]

ui-delete-config = delete config

ui-delete-failed = Delete failed

ui-deleted = Deleted

ui-detach-ui-on-quit-failed = detach_ui on quit failed

ui-detector-analysis = [bold]Detector analysis:[/bold] [yellow]

ui-detector-analysis-1 = [bold]Detector analysis:[/bold] [green]

ui-detector-analysis-no-sessions-loaded = [bold]Detector analysis:[/bold] [dim]no sessions loaded[/dim]

ui-detectors = Detectors

ui-diff = diff

ui-diff-1 =
    Diff
    

ui-diff-2 = Diff

ui-docker = docker

ui-docker-1 = Docker

ui-docker-eval-runs = [bold]Docker eval runs:[/bold]

ui-docker-is-available-and-running = Docker is available and running

ui-docker-is-not-available-install-docker-or-start = Docker is not available. Install Docker or start the daemon.

ui-docker-is-not-running-start-the-docker-daemon-an = Docker is not running. Start the Docker daemon and try again.

ui-docs-source-copy-url-open-on-host-browser =
    [bold]Docs / source[/bold]  [dim]copy URL · open on host browser[/dim]
    

ui-doctype = <!DOCTYPE

ui-done = [/green] done

ui-done-1 = [black on green bold]  DONE [/]

ui-down = Down

ui-dur = Duration

ui-duration = Duration

ui-context = Context

ui-context-usage = Context usage

ui-context-tokens = Context tokens

ui-context-window = Context window

ui-compactions = Compactions

ui-context-session-snapshot-note = From signals.json snapshots (read-only live samples every 60s; Grok does not export a per-turn series)

ui-context-session-end = session end

ui-context-session-end-paren = (session end)

ui-context-on-last-turn = {$usage} (session end)

ui-duration-1 = Duration:

ui-edit = Edit…

ui-elapsed = Elapsed

ui-enabled-analyzers = Enabled analyzers:

ui-enabled-no-tool-hits = enabled; no tool hits


ui-env = Env

ui-env-keys = env keys:

ui-err =  err)

ui-error =   ✗ ERROR

ui-error-1 =   ERROR

ui-error-2 = Error:

ui-error-3 = Error

ui-errors = , errors=

ui-errors-1 =  errors

ui-errors-2 = Errors

ui-errors-3 =  errors)

ui-errs =  errs=

ui-event = [dim]Event #

ui-event-mix-session = Event mix (session)

ui-events = Events

ui-events-1 =  events

ui-events-2 = events,

ui-events-3 =   events=

ui-exit =   exit=

ui-extracting = [cyan]Extracting…[/]

ui-f-flag-edit-flag-modal-delete-flag-from-modal = f = flag / edit flag (modal)  ·  delete flag from modal

ui-fail = FAIL

ui-failed =  failed

ui-failed-1 = [red]Failed:

ui-failed-2 =
     failed[/red]
    [dim]

ui-failed-3 = [white on red bold]  FAILED [/]

ui-failed-after = failed after

ui-failed-for = Failed for

ui-failed-to-add-row-for-s = Failed to add row for %s

ui-failed-to-apply-saved-theme-r = Failed to apply saved theme %r

ui-failed-to-ensure-persona-defaults = Failed to ensure persona defaults

ui-failed-to-load = Failed to load

ui-failed-to-load-persona-capabilities-for-s = Failed to load persona capabilities for %s

ui-failed-to-load-persona-s = Failed to load persona %s

ui-failed-to-load-session-meta-for-s = Failed to load session meta for %s

ui-failed-to-mount-report-section-s = failed to mount report section %s

ui-failed-to-read-prefs-from-s = failed to read prefs from %s

ui-failed-to-refresh-rules-table = Failed to refresh rules table

ui-failed-to-resolve-docker-base-for-s = Failed to resolve docker base for %s

ui-failed-to-set-docker-image-prefill = Failed to set docker image prefill

ui-failed-to-set-persona-prefill = Failed to set persona prefill

ui-failed-to-update-title-bar = Failed to update title bar

ui-failed-to-update-widget-with-renderable = Failed to update widget with renderable

ui-failed-to-validate-resolve-models-for-launch = Failed to validate/resolve models for launch

ui-failed-to-write-prefs-to-s = failed to write prefs to %s

ui-feedback = Feedback

ui-feedback-batch = [bold]Feedback batch:[/bold]

ui-field-select-report-view-select = field-select report-view-select

ui-field-select-session-filter-select = field-select session-filter-select

ui-file = file:

ui-filter = Filter

ui-finding =
    FINDING
    

ui-finding-1 = *Finding:* `

ui-findings = [/bold] findings | [red bold]

ui-findings-1 = Findings

ui-findings-2 =  findings

ui-findings-3 =  findings (


    

ui-finished = Finished

ui-finished-1 = finished:

ui-finished-in = finished in

ui-finished-in-1 = [/bold] finished in

ui-flagged = FLAGGED

ui-flagged-at = Flagged at:

ui-flags =  flags

ui-flags-1 = Flags (

ui-flags-2 = Flags

ui-follow-up-failed-for-s = follow-up failed for %s

ui-follow-up-s-pending =  follow-up(s) pending[/]

ui-for = for

ui-from = from

ui-from-calls = from calls

ui-from-events-jsonl-runtime-orchestrator-telemetry = From `events.jsonl` (runtime/orchestrator telemetry), not the agent `updates.jsonl` stream.

ui-from-your-grok-config-toml-no-extra-def-unless-y = from your ~/.grok/config.toml (no extra def unless you configure)).[/dim]

ui-full-refresh-from = Full refresh from

ui-fully-loaded-full-tools-share-loop = fully-loaded (full tools + share loop)

ui-gh-off = gh off

ui-gh-on = [yellow]gh on[/]

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

ui-git = git:

ui-github-mcp-skills-footer-for-keys =
     · GitHub / MCP / skills · Footer + ? for keys[/dim]
    

ui-github-pat-stored-on-this-persona = github_pat_… stored on this persona

ui-github-write-is-on-but-repo-url-is-empty-set-htt = GitHub write is ON but repo URL is empty — set HTTPS repo_url matching your token scope

ui-github-write-push = GitHub write / push

ui-grok-packages-this-run-only = )[/bold green]  [dim]Grok packages · this run only[/dim]

ui-groket = groket  [

ui-groket-batch = groket  [batch

    
        HelpModal {'{'{'}'}
            align: center middle;
            background: $background 55%;
        {'}'}
    
        #help-modal {'{'{'}'}
            width: 80%;
            height: 80%;
            max-width: 100;
            max-height: 100%;
            min-width: 40;
            min-height: 12;
            layout: vertical;
            border: tall $accent;
            background: $panel;
            padding: 1 2;
        {'}'}
    
        #help-modal-title {'{'{'}'}
            height: auto;
            dock: top;
            text-style: bold;
            color: $text;
            margin: 0 0 1 0;
        {'}'}
    
        #help-modal-body {'{'{'}'}
            height: 1fr;
            width: 100%;
            min-height: 4;
            overflow-y: auto;
            scrollbar-gutter: stable;
        {'}'}
    
        #help-modal-text {'{'{'}'}
            width: 100%;
            height: auto;
            padding: 0 1 0 0;
        {'}'}
    
        #help-modal-actions {'{'{'}'}
            height: auto;
            dock: bottom;
            width: 100%;
            align: right middle;
            margin-top: 1;
            padding-top: 0;
        {'}'}
    
        #help-modal-actions Button {'{'{'}'}
            min-width: 10;
        {'}'}
        

ui-high = [/red bold] high

ui-high-1 = High

ui-high-2 = high,

ui-high-3 = HIGH

ui-high-4 =  high

ui-high-5 = [red bold]High[/]

ui-highlight-a-config-or-select-with-s-then-x-to-de = Highlight a config (or select with s), then x to delete

ui-host-env = host env

ui-host-env-fallback = [dim]host env fallback[/dim]

ui-host-pass-through-uses-mcp-servers = Host pass-through: uses mcp_servers.

ui-host-tools = Host tools

ui-id = ID

ui-id-1 = [/bold]  [dim]id=

ui-id-2 = Id

ui-image = image:

ui-image-profile = [dim]image profile[/dim]

ui-in-flight-running-in-background-j-jobs-logs-no-p =  in flight — running in background ([bold]j[/bold] = jobs/logs; no per-run popups)

ui-inactive-model-s-not-in-grok-models-models-cache = inactive model(s) (not in `grok models` / models_cache.json). Launching:

ui-info = info

ui-inherit-from-runner-run-config = (inherit from runner / run config)

ui-input = Input

ui-input-select-textarea-checkbox-button = Input, Select, TextArea, Checkbox, Button

ui-input-select-textarea-selectionlist-switch-butto = Input, Select, TextArea, SelectionList, Switch, Button

ui-interactive-follow-ups-open-the-session-in-the-b = [dim]Interactive follow-ups: open the session in the browser (pending bar while the eval is not finished).[/dim]

ui-interactive-multi-turn-follow-ups-until-done = Interactive multi-turn (follow-ups until Done)

ui-yolo-auto-approve-tools = YOLO mode (grok --yolo; more aggressive auto-approve)

ui-j-jobs = … · j=jobs]

ui-jobs-for-logs-esc-closes-jobs-run-keeps-going =  — Jobs for logs (Esc closes Jobs; run keeps going)

ui-kind = Kind

ui-label = Label

ui-label-1 = label:

ui-last = last:

ui-last-outcome = Last outcome

ui-last-run = Last run [bold]

ui-last-turn = Last turn

ui-last-turn-gate-see-turns-table =   (last turn outcome — see Turns table)

ui-last-turn-outcome =   Last turn outcome=

ui-latest =   latest=

ui-launch = Launch:

ui-launch-error-s-open-j-for-jobs-logs =  launch error(s). Open [bold]j[/bold] for jobs/logs.

ui-launch-pick-models = launch (pick models)

ui-launch-selected = Launch selected

ui-launch-selected-1 = [bold]Launch selected[/bold]  [dim]

ui-launch-selected-2 = launch selected

ui-launched = Launched

ui-launches = Launches

ui-launches-1 = launches:

ui-leave-blank-to-keep-existing-token = (leave blank to keep existing token)

ui-live =  · LIVE

ui-live-turn =  · LIVE turn=

ui-loaded = Loaded

ui-loaded-1 = loaded

ui-local = Local

ui-local-1 = local ·

ui-local-catalog-entry-configure-to-set-headers-env = [dim]Local/catalog entry — configure to set headers/env; no registry docs page unless you added one.[/dim]

ui-loops = Loops

ui-mark-done-failed-for-s = mark done failed for %s

ui-max = Max

ui-max-configs-in-flight-at-once-each-config-still = Max configs in flight at once (each config still runs all its models together):

ui-mcp = MCP

ui-mcp-0-none-added-for-this-run = [dim]MCP (0) — none added for this run[/dim]

ui-mcp-1 = [bold green]MCP (

ui-mcp-2 = MCP ·

ui-mcp-3 = : mcp=

ui-mcp-bridge = mcp bridge

ui-mcp-bridge-calls = mcp bridge calls

ui-mcp-servers = MCP servers

ui-med = Med

ui-med-1 =  med)

ui-medium = MEDIUM

ui-medium-1 = [dark_orange bold]Medium[/]

ui-messages = Messages

ui-min = Min

ui-minimal-baseline-share-loop-setup-sh-for-rest = minimal (baseline + share loop; setup.sh for rest)

ui-model = Model

ui-model-1 = *Model:* `

ui-model-filter = Model filter:

ui-model-s =  model(s)

ui-model-s-from-config = model(s) from config

ui-models = Models

ui-models-1 = models:

ui-models-optional-override-leave-none-selected-to = Models (optional override). Leave none selected to use each config's saved models (falls back to app defaults):

ui-models-select-one-or-more-one-container-each = Models (select one or more — one container each):

ui-more =
     more
    

ui-more-1 =  more

ui-more-2 =  more)

ui-msg =  | [green bold]

ui-msg-1 =  | [yellow]

ui-msg-2 = [\s,]+

ui-msg-3 = )

ui-msg-4 =   [green]•[/green] [bold]

ui-msg-5 = [green]•[/green]

ui-n-4 = \n{'{'{'}'}4,{'}'}

ui-name = Name

ui-needs-env = [dim]needs env:[/dim]

ui-new = New

ui-new-in-runner = new in runner

ui-no-active-models-check-config-models-vs-grok-mod = No active models (check config models vs `grok models`)

ui-no-active-models-to-launch-edit-the-models-field = No active models to launch — edit the models field to match `grok models`

ui-no-active-runs-fill-the-form-and-press-launch = No active runs — fill the form and press Launch

ui-no-base-mcp-skills = : no base MCP/skills[/dim]

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
    

ui-no-input = (no input)

ui-no-models = : no models

ui-no-models-for = No models for

ui-no-note = no note

ui-no-prompt-extracted = (no prompt extracted)

ui-no-row-selected = [dim]No row selected.[/dim]

ui-no-saved-configs-yet-save-a-recipe-from-the-runn = [dim]No saved configs yet. Save a recipe from the Runner, or create with New.[/dim]

ui-no-session-yet-for = No session yet for

ui-no-sessions-found-in = No sessions found in

ui-no-timeline = (no timeline)

ui-no-token-stored-yet = [dim]No token stored yet.[/dim]

ui-no-traces-dir-to-refresh = No traces dir to refresh:

ui-none =
      (none)
    

ui-none-host-groket-gh-token-gh-token-only-if-orche = none (host GH_TOKEN only if orchestrator allows)

ui-none-run-defaults-only = none (run defaults only)

ui-none-saved = (none saved)

ui-noop = Noop

ui-note = note

ui-note-1 = Note

ui-notes = notes:

ui-nothing-to-launch-no-models = Nothing to launch (no models)

ui-nothing-to-refresh = Nothing to refresh:

ui-copied-selection = Copied selection to clipboard

ui-copied-detail = Copied detail to clipboard

ui-copied-report = Copied report to clipboard

ui-copied-content = Copied to clipboard

ui-copied-finding = Copied finding (Issue box) to clipboard

ui-nothing-to-copy = Nothing to copy

ui-press-key-to-quit = Press [b]{$key}[/b] to quit the app

ui-want-to-quit-title = Do you want to quit?

ui-ok = ok,

ui-ok-1 = ok

ui-ok-2 = ok

ui-ok-3 = OK

ui-one-id-per-line = [dim]One id per line[/dim]

ui-one-name-per-line = [dim]One name per line[/dim]

ui-one-per-line-pick-or-type-names = [dim]one per line · pick or type names[/dim]

ui-open-in-runner = open in runner

ui-open-session-failed = Open session failed:

ui-open-the-diff-tab-for-rewind-and-search-replace =
      Open the Diff tab for rewind and search_replace changes.
    

ui-open-with-c-configs-sessions-unchanged = ) — open with C (Configs); sessions unchanged

ui-other = Other

ui-outcome = Outcome

ui-output = Output (

ui-overall-fail =   Overall: FAIL  (

ui-overall-pass-required-checks-ok =
      Overall: PASS (required checks ok)
    

ui-panel-card-panel-card-grow = panel-card panel-card-grow

ui-panel-card-report-section = panel-card report-section

ui-parallelism = parallelism:

ui-pat-for-this-persona = [dim]PAT for this persona[/dim]

ui-path = Path

ui-paths-are-set-on-the-command-line-restart-groket = [dim]Paths are set on the command line (restart groket to change).[/dim]

ui-pending-analysis = [/yellow] pending analysis

ui-pending-in-background =  pending in background)

ui-pending-refresh-with-f5 = pending (refresh with F5)

ui-persona = [dim]persona

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

ui-plugin = *Plugin:* `

ui-plugin-s = plugin(s):

ui-plugin-s-1 =  plugin(s)

ui-plugins =  plugins)…

ui-plugins-0-none-added-for-this-run = [dim]Plugins (0) — none added for this run[/dim]

ui-plugins-1 =  plugins

ui-plugins-2 = [bold green]Plugins (

ui-plugins-3 =  plugins=

ui-plugins-persona-unchanged =  plugins (persona unchanged)

ui-pre-grok-shell = [dim]pre-grok shell[/dim]

ui-press-again-to-delete = Press [x] again to DELETE

ui-press-w-to-launch-selected = [/dim]  [dim]press [bold]w[/bold] to launch selected[/dim]

ui-prompt = prompt:

ui-prompt-1 = Prompt

ui-queued-follow-up-sent = Queued follow-up sent (

ui-quiet-mode-open-j-for-live-logs-status-f5-refres = ) — [dim]quiet mode: open [bold]j[/bold] for live logs/status · F5 refreshes this snapshot · Esc leaves docker running[/dim]


ui-r-registry =  · r registry

ui-recipes =  recipes).

ui-refresh-all-failed = Refresh all failed:

ui-refresh-catalog-run-grok-models-on-the-host = refresh catalog: run `grok models` on the host

ui-refresh-done-sessions = Refresh done: sessions=

ui-refresh-tip-surfaces-failed-on-s = refresh_tip_surfaces failed on %s

ui-refreshed = Refreshed:

ui-refreshing-sessions-from = Refreshing sessions from

ui-registry = Registry

ui-registry-1 = registry ·

ui-registry-2 = [cyan]registry[/cyan]

ui-registry-3 = [dim]registry:[/dim]

ui-registry-error = registry error ·

ui-registry-searching = registry · searching

ui-registry-type-a-query-enter = registry · type a query · enter

ui-reload-meta-failed-for-s = reload meta failed for %s

ui-replace-host = replace_host

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

ui-result = [dim cyan]Result[/]

ui-run = Run

ui-run-1 = Run

ui-run-2 = Run [bold]

ui-run-config-s =  run config(s)

ui-run-config-s-recipes-only-sessions-traces-kept = run config(s) (recipes only — sessions/traces kept):

ui-run-crashed = Run crashed:

ui-run-env-keys-from-mcp-configure = [dim]Run env keys from MCP configure:

ui-run-env-keys = [dim]Env vars (this run):

ui-run-env-0-none = [dim]Env: 0 (none)[/dim]

ui-run-env-saved = Saved {$count} run env var(s)

ui-inline-skills = [bold green]Inline skills (

ui-inline-skills-0-none = [dim]Inline skills: 0 (none)[/dim]

ui-inline-skill-saved = Inline skill saved: {$name}

ui-run-extras = Run extras:

ui-run-mcp =  · run+mcp=

ui-run-s-active = [/bold] run(s) active (

ui-run-s-active-1 = [/bold] run(s) active —

ui-run-s-j-jobs =  run(s) · j=jobs]

ui-run-s-keep-going-in-docker-j-jobs-logs-quit-anyt =  run(s) keep going in docker — [bold]j[/bold] jobs/logs; quit anytime (relaunch prunes finished eval containers)

ui-run-s-latest = run(s), latest

ui-run-s-started = run(s) started,

ui-running = [yellow bold]  RUNNING [/]

ui-running-1 = [yellow]Running…[/]

ui-running-analysis = [dim italic]Running analysis…[/]

ui-running-analysis-1 = [dim italic]Running analysis…[/]

ui-running-checks = Running checks…

ui-runs = runs →

ui-runs-0 = runs 0

ui-runs-1 = runs

ui-save = Save

ui-save-config-failed = Save config failed:

ui-save-failed = Save failed:

ui-save-persona-to-keep = save persona to keep

ui-save-run-config-launch-to-keep-persona-unchanged = save run config / launch to keep (persona unchanged)

ui-saved-config = Saved config

ui-saved-persona = Saved persona

ui-saved-run-config = Saved run config

ui-saved-run-configs-recipes-in-runs-run-configs-no =
    [bold]Saved run configs[/bold]  [dim]recipes in runs/run_configs/ (not sessions). [bold]s[/bold]/space toggle select · [bold]S[/bold] select all · [bold]w[/bold] launch selected (multi) · [bold]l[/bold] launch one · [bold]x[/bold] twice to delete · [bold]Enter[/bold] edit in runner.[/dim]
    

ui-scanning = Scanning

ui-see-summary-tab-or-session-session-error-timelin =  (see Summary tab or Session / Session error timeline rows)

ui-sel = Sel

ui-select-a-row-for-description-endpoint-env-needs = [dim]Select a row (↑↓) for description, endpoint, env needs, and doc links.[/dim]

ui-select-an-event-from-the-timeline = [dim]Select an event from the timeline[/dim]

ui-select-at-least-one-model = Select at least one model

ui-select-configs-with-s-space-or-cursor-on-one-the = Select configs with s/space (or cursor on one) then w

ui-select-sessions-with-s-or-highlight-a-row-then-p = Select sessions with [s]/S] or highlight a row, then press [x] to delete

ui-selected = [/green bold] selected

ui-selected-1 = selected ·

ui-selected-2 = Selected

ui-selected-3 = selected

ui-selected-4 =  [green]● selected[/green]

ui-selected-5 =  (selected)

ui-selection = [bold green]Selection:

ui-selection-none-s-toggle-row-s-all-none-w-launche = [dim]Selection: none — [bold]s[/bold] toggle row · [bold]S[/bold] all/none · [bold]w[/bold] launches cursor config only if nothing selected.[/dim]

ui-self-test = Self-test

ui-self-test-external-dependencies = Self-test — external dependencies

ui-self-test-fail = self-test FAIL×

ui-self-test-ok = self-test OK (
ui-self-test-ok-warns = self-test OK ({ $n } warn)
ui-blm-title = Launch selected ({ $n } config(s)): { $names }
ui-selection-bar = Selection: { $n }  { $labels }{ $extra } — press w to launch selected
ui-config-detail-title = { $name }  id={ $id }{ $sel }
ui-no-saved-configs = No saved configs yet. Save a recipe from the Runner, or create with New.


ui-self-test-pass = self-test PASS

ui-session = *Session:* `

ui-session-1 =   session=

ui-session-2 = Session

ui-session-error = [bold red]Session error[/]

ui-session-error-1 = [bold red]session error[/]

ui-session-errors =  session errors

ui-session-id = Session ID

ui-session-meta-is-last-turn-ended-gate-earlier-tur =
     (session meta reflects the last finished turn). Earlier turns may still be success — see Turns above. Stream may stop without a final assistant message.
    

ui-session-meta-last-turn-interactive-gate =   (session meta = last finished turn)

ui-session-model-select-update-failed = session model select update failed

ui-session-outcome-pending = session outcome pending

ui-session-report =
    Session report
    

ui-session-s =  session(s)

ui-session-s-from-disk-traces-feedback-cache-run-co =  session(s) from disk (traces + feedback_cache; run configs are kept)

ui-session-task-select-update-failed = session task select update failed

ui-session-turn-runtime = Session / turn runtime

ui-sessions = [/bold] sessions | [bold]

ui-sessions-1 =  sessions (

ui-sessions-2 =  sessions

ui-sessions-3 = sessions

ui-sessions-press-a-to-run-detectors =  sessions

ui-set-models-above = ; set models above

ui-setup = setup:

ui-severity = *Severity:*

ui-share = Share

ui-share-meta-failed = share meta failed

ui-system-prompt = System prompt

ui-shown-in-the-runner-dropdown = [dim]Shown in the Runner dropdown[/dim]

ui-signal =   signal=

ui-single-segment-no-turn-started-markers-in-timeli =
      Single segment (no turn_started markers in timeline yet).
    

ui-skill = ` + skill `

ui-skill-not-written = ` (skill not written).

ui-skill-packs = [dim]skill packs[/dim]

ui-skill-write-failed = `; skill write failed:

ui-skills = Skills

ui-skills-0-none-added-for-this-run = [dim]Skills (0) — none added for this run[/dim]

ui-skills-1 = Skills

ui-skills-2 =  skills=

ui-skills-3 = [bold green]Skills (

ui-skills-4 = skills ·

ui-skip = Skip

ui-skipping = Skipping

ui-snapshot = Snapshot

ui-solid-blue = solid blue

ui-solid-cyan = solid cyan

ui-solid-green = solid green

ui-solid-red = solid red

ui-solid-white = solid white

ui-solid-yellow = solid yellow

ui-source = Source

ui-source-1 = source

ui-source-run-id = source run_id:

ui-source-session = source session:

ui-sources =
    
      sources: 

ui-space-click-to-select-no-selection-per-config-mo = [dim]space/click to select · no selection = per-config models[/dim]

ui-span = Span

ui-started = Started

ui-statistics =
    Statistics
    

ui-status = Status

ui-stdio-needs-tools-in-image = [dim]stdio needs tools in image[/dim]

ui-stop-live-refresh-on-quit-failed = stop live refresh on quit failed

ui-stored = stored

ui-stored-on-persona = stored on persona

ui-sub-finding-s =  sub-finding(s):*

ui-subagent = Subagent

ui-succeeded =  succeeded[/green], [red]

ui-succeeded-1 =  succeeded[/green]

ui-sync-browser-tip-messages-failed = sync browser tip messages failed

ui-target-directory = target_directory:

ui-target-file = target_file:

ui-task = Task

ui-task-1 = task:

ui-task-catalog-lookup-failed-for-s = Task catalog lookup failed for %s

ui-task-filter = Task filter:

ui-thinking = Thinking

ui-this-run-only = )[/bold green]  [dim]this run only[/dim]

ui-thought = Thought

ui-thought-1 = [dim cyan italic]Thought[/]

ui-time = Time:

ui-timeline = , timeline #

ui-tip = tip

ui-tips-callouts-off-hidden = Tips & callouts: off (hidden)

ui-tips-callouts-on = Tips & callouts: on

    
        TipSurface {'{'{'}'}
            height: auto;
            width: 100%;
            max-width: 100%;
            margin: 0 0 1 0;
            padding: 0 1;
            border: solid cyan;
            background: $boost;
        {'}'}
        TipSurface.tip-surface-empty {'{'{'}'}
            display: none;
            height: 0;
            margin: 0;
            padding: 0;
            border: none;
        {'}'}
        

ui-title = Title

ui-token = token

ui-token-on-file-yes = [dim]Token on file: yes (

ui-token-status-unknown = token status unknown

ui-tool = tool

ui-tool-1 = Tool

ui-tool-2 = [bold cyan]Tool[/]

ui-tool-err = Tool errors

ui-tool-err-1 =  tool err)

ui-tool-errors = tool errors,

ui-tool-execution = Tool execution

ui-tool-servers = [dim]tool servers[/dim]

ui-tools = Tools

ui-tools-1 =  tools
ui-tokens = tokens
ui-tools-2 =   tools=

ui-tools-3 = tools:

ui-top-tools = Top tools

ui-total =
     total
    

ui-total-1 = Total

ui-total-2 = TOTAL

ui-trace-evaluation-error-hunting = Trace Evaluation & Error Hunting

ui-traces = [bold]Traces[/bold]

ui-traces-path-not-found-yet-runner-writes-to = Traces path not found yet — Runner writes to

ui-transport = transport

ui-truncated =
    
    
    … truncated …
    
    

ui-truncated-1 =
    
    … truncated …

ui-truncated-for-display =
    
    
    … *(truncated for display)* …
    
    

ui-truncated-see-rewind-points-jsonl-updates =
    
    
    … (truncated; see rewind_points.jsonl / updates)
    
    

ui-turn = Turn

ui-turn-1 =  ⚠ turn=

ui-turn-2 =  · turn=

ui-turn-ended = turn ended

ui-turn-ended-with-outcome = Turn ended with outcome=

ui-turn-in-progress = turn in progress

ui-turn-segmentation-failed = turn segmentation failed

ui-turn-started = turn started

ui-turn-stat-events = events={$n}

ui-turn-stat-tools = tools={$n}

ui-turn-stat-err = {$n} err

ui-turn-stat-user = user={$n}

ui-turn-stat-asst = asst={$n}

ui-turn-stat-span = #{$first}–#{$last}

ui-turn-tools-mix = tools: {$mix}

ui-turns =  turns

ui-turns-1 = Turns

ui-host-tool-errors = ({$n} errors)

ui-host-tool-calls = {$n}×

ui-host-tool-total = total {$n}

ui-err-count = {$n} err

ui-err-count-paren = ({$n} err)

ui-mcp-method-line = · {$method} {$calls}×

ui-mcp-search-count = · {$n} search_tool query(ies)

ui-skill-line = {$id} — {$bits}

ui-use-tool-count = use_tool {$n}×

ui-skill-loaded = loaded {$n}×

ui-sources-line = sources: {$notes}

ui-ubuntu-24-04-fully-loaded = ubuntu:24.04 @ fully-loaded

ui-ubuntu-24-04-raw-os-still-share-loop-via-entrypo = ubuntu:24.04 (raw OS; still share loop via entrypoint)


ui-up = Up

ui-updated-config = Updated config

ui-url = URL

ui-usage-summary-failed = usage summary failed

ui-use-tool = use_tool

ui-user = User

ui-user-1 =   user=

ui-user-2 = [bold white]User[/]

ui-user-input = User input

ui-viewing = Viewing:

ui-wait-for-traces-to-appear =  — wait for traces to appear

ui-warn = warn

ui-warn-1 =  warn)

ui-warn-2 = WARN

ui-warnings =
     warnings)
    

ui-what-the-model-should-have-done = *What the model should have done:*

ui-will-be-git-fetched-into-the-container-volume-at = ` will be git-fetched into the container volume at launch

ui-with = with

ui-work = [bold]Work[/bold]

ui-work-1 = work:

ui-work-dir = [dim]work_dir:

ui-workers-cancel-on-quit-failed = workers cancel on quit failed

ui-workspace = Workspace

ui-writing = Writing



ui-xml = <?xml

# interpolating fragments / templates
ui-plugins-count = plugins: { $n }
ui-turn-number = Turn { $turn }
ui-queued-count = { $n } queued
ui-staged-follow-up = follow-up staged
ui-staged-last-turn = last turn staged
ui-batches-count =  · batches { $n }
ui-version-prefix =   v{ $version }
ui-failed-suffix =  failed)
ui-eg-prefix = 
    e.g. { $example }
ui-session-prefix = Session { $id }
ui-runs-active = runs { $n }
ui-analyze-active = analyze { $n }
ui-sessions-count = sessions { $n }
ui-analyzed-count = analyzed { $n }
ui-failed-paren =  ({ $n } failed)
ui-batch-err-example = 
    e.g. { $id }: { $sample }
ui-version-tag =   v{ $ver }
ui-status-bracket =   [{ $status }]

# Activity bar (compact)
activity-pending = Pending { $n }
activity-building = Building { $n }
activity-running = Running { $n }
activity-ending = Ending { $n }
activity-extracting = Extracting { $n }
activity-awaiting = Awaiting { $n }
activity-analysis = Analysis { $n }
activity-refresh = Refresh { $n }
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

ui-press-again-to-delete-persona = Press [x] again to DELETE persona
ui-press-again-to-delete-personas = Press [x] again to DELETE { $n } persona(s)

# --- Composed UI (prefer these over fragment glue; no edge whitespace) ---
tool-detail-heading = #{ $index } tool { $name }
tool-detail-heading-error = #{ $index } tool { $name } ✗ ERROR
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
notify-failed-plugins = Failed to load { $n } plugin(s)
notify-model-filter = Model filter: { $label }
work-path-line = work: { $path }
runs-path-line = runs → { $path }
flagged-at-when = Flagged at { $when }
truncate-marker = …truncated…
truncate-for-display = …truncated for display…
sessions-home-summary = { $total } sessions · { $findings } findings · { $high } high
sessions-selected-count = { $n } selected
sessions-pending-analysis = { $n } pending analysis

# --- Session / jobs / personas notifies (composed) ---
notify-traces-path-pending = Traces path not found yet — runner writes to { $path }
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
notify-refreshing-sessions = Refreshing sessions from { $path }…
notify-refreshed-sessions = Refreshed { $n } session(s)
notify-run-failed = Run { $id } failed after { $elapsed }: { $error }
notify-run-finished = Run { $id } finished in { $elapsed }: { $ok }/{ $total } ok, { $failed } failed
title-groket-batch = groket batch { $batch }… · { $n } run(s) · j=jobs
title-groket-runs = groket · { $n } run(s) · latest { $id } · j=jobs
title-browser-session = Browser — { $label } ({ $model }){ $extra }
notify-turn-ended-outcome = Turn ended with outcome { $outcome } — see Summary tab or session/session_error timeline events
notify-queued-follow-up-sent = Queued follow-up sent: { $preview }
notify-open-session-failed = Open session failed: { $exc }
notify-no-session-yet = No session yet for { $container } — wait for traces to appear
notify-share-open-failed = Could not open share for { $name }: { $exc }
jobs-banner-runs = Docker eval runs: { $n } active{ $latest }
jobs-detector-progress = Detector analysis { $done }/{ $total } ({ $pend } pending in background)
jobs-detector-done = Detector analysis { $done }/{ $total } done
jobs-work-dir = work dir: { $path }
persona-registry-searching = Registry searching { $query }…
persona-registry-error = Registry error: { $error }
persona-registry-hits = Registry: { $n } for { $query }{ $extra }
persona-local-count = Local { $n } · r=registry
persona-saved = Saved persona { $pid }
persona-configure-title = Configure { $name }
persona-added-mcp = Added MCP { $id }
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
browser-mcp-configured = configured
browser-skill-mounted = mounted
browser-skill-seen = seen
browser-last-turn-outcome-note = Last turn outcome={ $outcome } (session meta = last finished turn)
title-browser-extra-turn = · turn={ $outcome }
title-browser-extra-live-turn = · LIVE turn={ $outcome }
title-browser-extra-live = · LIVE
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

ui-control-socket-attached = Attached to existing control owner (catalog and notes via control socket)
ui-control-socket-attach-failed = Control owner not reachable; loading sessions from local disk
ui-control-socket-start-failed = Editor control socket could not start; this instance continues without the socket

notify-host-sessions-on = Host sessions shown (native ~/.grok/sessions)
notify-host-sessions-off = Host sessions hidden

