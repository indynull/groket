//! Display helpers for notes, status, and errors.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::model::KindFilter;

/// Open event / copy buffer ceiling. Same number as [`crate::live::TIMELINE_OPEN_CHARS`].
pub const EXTRACT_CHARS: usize = 50_000;

/// Cut *s* to *max_chars* (Unicode scalar values) and mark truncation.
pub fn capped_display(s: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }
    let count = s.chars().count();
    if count <= max_chars {
        return s.to_string();
    }
    let mut out: String = s.chars().take(max_chars).collect();
    out.push('…');
    out
}

/// Compact JSON (or the string itself), then apply [`capped_display`].
pub fn capped_json(value: &Value, max_chars: usize) -> String {
    let s = match value {
        Value::String(s) => s.clone(),
        other => serde_json::to_string(other).unwrap_or_default(),
    };
    capped_display(&s, max_chars)
}

/// Same labels as TUI ``ui-origin-work`` / ``ui-origin-host``.
pub fn origin_label(origin: &str) -> &'static str {
    match origin.trim().to_ascii_lowercase().as_str() {
        "host" => "Host",
        "work" | "eval" => "Eval",
        "" => "—",
        _ => "Eval",
    }
}

pub fn is_blank_status(status: &str) -> bool {
    let t = status.trim();
    t.is_empty() || t == "—" || t == "-" || t == "–"
}

/// Home-list terminal labels. A later live label without a live outcome is hydrate flicker.
pub fn is_terminal_status(status: &str) -> bool {
    matches!(
        list_status_label(status, "").to_ascii_lowercase().as_str(),
        "complete" | "cancelled" | "canceled" | "failed" | "error"
    )
}

/// Same short labels as :meth:`SessionMeta.list_status_label`.
pub fn list_status_label(status: &str, outcome: &str) -> String {
    let raw = if !is_blank_status(status) {
        status.trim().to_string()
    } else {
        let oc = outcome
            .trim()
            .to_ascii_lowercase()
            .replace(char::is_whitespace, "_");
        return match oc.as_str() {
            "ending" | "finishing" => "ending".into(),
            "awaiting_follow_up" | "awaiting" => "awaiting".into(),
            "running" | "in_progress" | "pending" => "running".into(),
            "cancelled" | "canceled" | "interrupted" | "aborted" => "cancelled".into(),
            "success" | "ok" | "completed" | "complete" | "done" => "complete".into(),
            "error" | "failed" | "failure" | "timeout" => "cancelled".into(),
            "" => "—".into(),
            _ => "complete".into(),
        };
    };
    match raw
        .to_ascii_lowercase()
        .replace(char::is_whitespace, "_")
        .as_str()
    {
        "completed" | "success" | "ok" | "done" => "complete".into(),
        "canceled" | "interrupted" | "aborted" | "failed" | "error" | "failure" | "timeout" => {
            "cancelled".into()
        }
        "in_progress" | "pending" => "running".into(),
        "finishing" => "ending".into(),
        "awaiting_follow_up" => "awaiting".into(),
        _ => raw,
    }
}

pub fn status_tone(status: &str) -> &'static str {
    let s = status.to_ascii_lowercase();
    if s == "awaiting" || s.contains("await") {
        "awaiting"
    } else if s.contains("run") {
        "running"
    } else if s.contains("complete") || s == "ok" {
        "complete"
    } else if s.contains("end") {
        "ending"
    } else if s.contains("cancel")
        || s.contains("interrupt")
        || s.contains("abort")
        || s.contains("fail")
        || s == "error"
    {
        "cancelled"
    } else {
        ""
    }
}

/// Same compact duration as the TUI (`<1s`, `12s`, `2m05s`, `1h04m`).
pub fn fmt_duration(secs: f64) -> String {
    let s = secs.max(0.0) as u64;
    if s < 1 {
        return "<1s".into();
    }
    if s < 60 {
        return format!("{s}s");
    }
    let (m, s) = (s / 60, s % 60);
    if m < 60 {
        return format!("{m}m{s:02}s");
    }
    let (h, m) = (m / 60, m % 60);
    format!("{h}h{m:02}m")
}

/// Duration chip for session chrome. Hidden when the session has no elapsed time.
pub fn session_duration_chip(seconds: f64, display: &str) -> String {
    if seconds <= 0.0 {
        return String::new();
    }
    let shown = display.trim();
    if !shown.is_empty() && shown != "—" {
        return shown.to_string();
    }
    fmt_duration(seconds)
}

/// ISO-ish created stamp for Overview (TUI Summary style).
pub fn short_created(iso: &str) -> String {
    let s = iso.trim();
    if s.is_empty() {
        return String::new();
    }
    if s.contains('T') && s.len() >= 19 {
        return s[..19].replace('T', " ");
    }
    s.to_string()
}

/// One Overview meta row — fixed label column in the view.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OverviewField {
    pub key: &'static str,
    pub label: &'static str,
    pub value: String,
    pub copyable: bool,
}

/// Operator-facing Overview rows (TUI Summary-shaped glance, not chrome counts).
///
/// Title, status, model/duration, context meter, summary prose, and banners
/// are painted above this stack. Findings/notes totals live on those banners.
pub fn overview_fields(
    meta: &crate::wire::SessionMeta,
    turns: &crate::wire::TurnsBlock,
) -> Vec<OverviewField> {
    let mut out = Vec::new();
    if !meta.session_id.is_empty() {
        out.push(OverviewField {
            key: "session",
            label: "session",
            value: meta.session_id.clone(),
            copyable: true,
        });
    }
    if meta.tool_call_count > 0 || meta.error_count > 0 || meta.tool_failure_count > 0 {
        let value = if meta.error_count > 0 || meta.tool_failure_count > 0 {
            let errs = meta.error_count.max(meta.tool_failure_count);
            format!("{} · {} errors", meta.tool_call_count, errs)
        } else {
            meta.tool_call_count.to_string()
        };
        out.push(OverviewField {
            key: "tools",
            label: "tools",
            value,
            copyable: true,
        });
    }
    if turns.turns.len() > 1 {
        if let Some(last) = turns.turns.last() {
            let mut value = last.label.clone();
            if value.is_empty() {
                value = last.face_caption();
            }
            match (last.first_index, last.last_index) {
                (Some(a), Some(b)) => {
                    value = format!("{value} · #{a}–#{b}");
                }
                (Some(a), None) | (None, Some(a)) => {
                    value = format!("{value} · #{a}");
                }
                (None, None) => {}
            }
            out.push(OverviewField {
                key: "last_turn",
                label: "last turn",
                value,
                copyable: true,
            });
        }
    }
    if meta.num_messages > 0 {
        out.push(OverviewField {
            key: "messages",
            label: "messages",
            value: meta.num_messages.to_string(),
            copyable: true,
        });
    }
    if meta.loop_count > 0 {
        out.push(OverviewField {
            key: "loops",
            label: "loops",
            value: meta.loop_count.to_string(),
            copyable: true,
        });
    }
    if !meta.run_id.is_empty() {
        out.push(OverviewField {
            key: "run",
            label: "run",
            value: meta.run_id.clone(),
            copyable: true,
        });
    }
    if !meta.task_id.is_empty() {
        out.push(OverviewField {
            key: "task",
            label: "task",
            value: meta.task_id.clone(),
            copyable: true,
        });
    }
    if !meta.git_repo.is_empty() {
        out.push(OverviewField {
            key: "repo",
            label: "repo",
            value: meta.git_repo.clone(),
            copyable: true,
        });
    }
    if !meta.git_branch.is_empty() {
        out.push(OverviewField {
            key: "branch",
            label: "branch",
            value: meta.git_branch.clone(),
            copyable: true,
        });
    }
    let created = short_created(&meta.created_at);
    if !created.is_empty() {
        out.push(OverviewField {
            key: "created",
            label: "created",
            value: created,
            copyable: true,
        });
    }
    if !meta.path.is_empty() {
        out.push(OverviewField {
            key: "path",
            label: "path",
            value: meta.path.clone(),
            copyable: true,
        });
    }
    out
}

/// One Stats-tab count row (human label, table cell value).
pub struct OverviewStatRow {
    pub section: &'static str,
    pub label: String,
    pub value: String,
}

/// Event-type and tool counts with the same labels as Timeline.
pub fn overview_stat_rows(events: &[crate::wire::TimelineEvent]) -> Vec<OverviewStatRow> {
    let mut types: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
    let mut tools: std::collections::BTreeMap<String, usize> = std::collections::BTreeMap::new();
    for ev in events {
        let key = if ev.event_type.is_empty() {
            ev.kind.clone()
        } else {
            ev.event_type.clone()
        };
        if !key.is_empty() {
            *types.entry(key).or_insert(0) += 1;
        }
        if ev.event_type == "tool_call" && !ev.tool_name.is_empty() {
            *tools.entry(ev.tool_name.clone()).or_insert(0) += 1;
        }
    }
    let mut out = Vec::new();
    for (raw, n) in types {
        let label = human_event_type_label(&raw, "", "", false);
        out.push(OverviewStatRow {
            section: "Event types",
            label,
            value: n.to_string(),
        });
    }
    for (raw, n) in tools {
        out.push(OverviewStatRow {
            section: "Tools",
            label: format_tool_display(&raw),
            value: n.to_string(),
        });
    }
    out
}

/// Sort Stats rows. Column 0 is Kind, 1 is Name, 2 is Count (numeric).
pub fn sort_stat_rows(rows: &mut [OverviewStatRow], col: usize, asc: bool) {
    rows.sort_by(|a, b| {
        let cmp = match col {
            0 => a.section.cmp(b.section),
            2 => {
                let an: usize = a.value.parse().unwrap_or(0);
                let bn: usize = b.value.parse().unwrap_or(0);
                an.cmp(&bn)
            }
            _ => a.label.cmp(&b.label),
        };
        if asc {
            cmp
        } else {
            cmp.reverse()
        }
    });
}

/// Glance counts for Overview — not the job list or log tails.
pub fn overview_job_fields(
    jobs: &[crate::wire::BackgroundJobRow],
    schedules: &[crate::wire::ScheduleRow],
    workflows: &[crate::wire::WorkflowRow],
) -> Vec<OverviewField> {
    let mut out = Vec::new();
    if !jobs.is_empty() {
        let running = jobs.iter().filter(|j| j.status == "running").count();
        let done = jobs
            .iter()
            .filter(|j| j.status == "done" || j.status == "completed")
            .count();
        let failed = jobs
            .iter()
            .filter(|j| j.status == "failed" || j.status == "cancelled")
            .count();
        let value = overview_job_count_value(jobs.len(), running, done, failed);
        out.push(OverviewField {
            key: "background",
            label: "background",
            value,
            copyable: false,
        });
    }
    if !schedules.is_empty() {
        let value = if schedules.len() == 1 {
            let s = &schedules[0];
            if !s.human_schedule.is_empty() {
                s.human_schedule.clone()
            } else if !s.prompt_preview.is_empty() {
                s.prompt_preview.clone()
            } else {
                "1".into()
            }
        } else {
            schedules.len().to_string()
        };
        out.push(OverviewField {
            key: "schedules",
            label: "schedules",
            value,
            copyable: false,
        });
    }
    if !workflows.is_empty() {
        let complete = workflows
            .iter()
            .filter(|w| w.status == "complete" || w.status == "done")
            .count();
        let failed = workflows.iter().filter(|w| w.status == "failed").count();
        let cancelled = workflows.iter().filter(|w| w.status == "cancelled").count();
        let interrupted = workflows
            .iter()
            .filter(|w| w.status == "interrupted")
            .count();
        out.push(OverviewField {
            key: "workflows",
            label: "workflows",
            value: overview_workflow_count_value(
                workflows.len(),
                complete,
                failed,
                cancelled,
                interrupted,
            ),
            copyable: false,
        });
    }
    out
}

/// One Tasks-tab row: kind, status, short label, optional Timeline bookend.
pub struct OverviewTaskRow {
    pub kind: String,
    pub status: String,
    pub label: String,
    pub event_index: Option<i64>,
}

/// Jobs, then schedules — Workflows have their own tab.
pub fn overview_task_rows(
    jobs: &[crate::wire::BackgroundJobRow],
    schedules: &[crate::wire::ScheduleRow],
) -> Vec<OverviewTaskRow> {
    let mut out = Vec::new();
    for job in jobs {
        let label = if !job.description.is_empty() {
            job.description.clone()
        } else if !job.command.is_empty() {
            job.command.clone()
        } else {
            job.id.clone()
        };
        let kind = if job.kind.is_empty() {
            "background".into()
        } else {
            job.kind.clone()
        };
        out.push(OverviewTaskRow {
            kind,
            status: job.status.clone(),
            label,
            event_index: job.event_index,
        });
    }
    for sch in schedules {
        let label = if !sch.prompt_preview.is_empty() {
            sch.prompt_preview.clone()
        } else if !sch.human_schedule.is_empty() {
            sch.human_schedule.clone()
        } else {
            sch.id.clone()
        };
        out.push(OverviewTaskRow {
            kind: "schedule".into(),
            status: "scheduled".into(),
            label,
            event_index: None,
        });
    }
    out
}

/// Workflow list rows for the Overview Workflows tab.
pub fn overview_workflow_rows(workflows: &[crate::wire::WorkflowRow]) -> Vec<OverviewTaskRow> {
    workflows
        .iter()
        .map(|run| OverviewTaskRow {
            kind: "workflow".into(),
            status: run.status.clone(),
            label: if run.name.is_empty() {
                run.id.clone()
            } else {
                run.name.clone()
            },
            event_index: run.event_index,
        })
        .collect()
}

/// Subagent list rows for the Overview Subagents tab.
pub fn overview_subagent_rows(runs: &[crate::wire::SubagentRunRow]) -> Vec<OverviewTaskRow> {
    runs.iter()
        .map(|run| {
            let label = if !run.description.is_empty() {
                run.description.clone()
            } else if !run.subagent_type.is_empty() {
                run.subagent_type.clone()
            } else {
                run.child_session_id.clone()
            };
            OverviewTaskRow {
                kind: if run.subagent_type.is_empty() {
                    "subagent".into()
                } else {
                    run.subagent_type.clone()
                },
                status: run.status.clone(),
                label,
                event_index: run.spawn_event_index,
            }
        })
        .collect()
}

/// One labeled inspect section: heading immediately above a non-empty body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InspectBlock {
    pub label: &'static str,
    pub body: String,
}

fn inspect_block(label: &'static str, body: impl Into<String>) -> Option<InspectBlock> {
    let body = body.into();
    if body.trim().is_empty() {
        return None;
    }
    Some(InspectBlock { label, body })
}

fn join_happen(parts: &[&str]) -> String {
    parts
        .iter()
        .map(|p| p.trim())
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join("  ·  ")
}

/// Schedule inspect: Asked is the prompt; Happened is the cadence / last fire.
pub fn schedule_inspect_blocks(
    prompt: &str,
    human: &str,
    next: &str,
    last: &str,
    child: &str,
) -> Vec<InspectBlock> {
    [
        inspect_block("Asked", prompt),
        inspect_block("Happened", join_happen(&[human, next, last, child])),
    ]
    .into_iter()
    .flatten()
    .collect()
}

/// Job inspect: Asked is what was launched; Happened is status; Failed is the last log line.
pub fn job_inspect_blocks(asked: &str, happened: &str, failed: &str) -> Vec<InspectBlock> {
    [
        inspect_block("Asked", asked),
        inspect_block("Happened", happened),
        inspect_block("Failed", failed),
    ]
    .into_iter()
    .flatten()
    .collect()
}

/// Subagent inspect: Asked is the assignment; Happened is kind/status; Failed has a body.
pub fn subagent_inspect_blocks(asked: &str, happened: &str, failed: &str) -> Vec<InspectBlock> {
    job_inspect_blocks(asked, happened, failed)
}

fn overview_workflow_count_value(
    total: usize,
    complete: usize,
    failed: usize,
    cancelled: usize,
    interrupted: usize,
) -> String {
    let mut parts: Vec<String> = Vec::new();
    if complete > 0 {
        parts.push(format!("{complete} complete"));
    }
    if failed > 0 {
        parts.push(format!("{failed} failed"));
    }
    if cancelled > 0 {
        parts.push(format!("{cancelled} cancelled"));
    }
    if interrupted > 0 {
        parts.push(format!("{interrupted} interrupted"));
    }
    if parts.is_empty() {
        return total.to_string();
    }
    if complete + failed + cancelled + interrupted < total {
        parts.push(total.to_string());
    }
    parts.join(" · ")
}

/// Name from a Timeline ``workflow`` tool bag (not the Rhai body).
pub fn workflow_name_from_raw(raw: &Value) -> String {
    let name = json_str_field(raw, "name");
    if !name.is_empty() && name != "none" && name != "null" {
        return name;
    }
    let path = json_str_field(raw, "script_path");
    if !path.is_empty() {
        let stem = path
            .rsplit(['/', '\\'])
            .next()
            .unwrap_or("")
            .trim_end_matches(".rhai");
        if !stem.is_empty() {
            return stem.to_string();
        }
    }
    let script = json_str_field(raw, "script");
    if let Some(idx) = script.find("name:") {
        let rest = &script[idx + 5..];
        if let Some(start) = rest.find('"') {
            let after = &rest[start + 1..];
            if let Some(end) = after.find('"') {
                return after[..end].to_string();
            }
        }
    }
    String::new()
}

/// Match a Timeline workflow tool call to an overview run.
pub fn workflow_for_event<'a>(
    runs: &'a [crate::wire::WorkflowRow],
    raw: &Value,
) -> Option<&'a crate::wire::WorkflowRow> {
    let rid = {
        let a = json_str_field(raw, "resume_from_run_id");
        if a.is_empty() {
            json_str_field(raw, "run_id")
        } else {
            a
        }
    };
    if !rid.is_empty() {
        if let Some(hit) = runs.iter().find(|r| r.id == rid) {
            return Some(hit);
        }
    }
    let name = workflow_name_from_raw(raw);
    if name.is_empty() {
        return None;
    }
    let mut hits: Vec<&crate::wire::WorkflowRow> = runs
        .iter()
        .filter(|r| r.name == name || r.name.starts_with(&format!("{name}-")))
        .collect();
    hits.sort_by(|a, b| a.id.cmp(&b.id));
    hits.pop()
}

fn overview_job_count_value(total: usize, running: usize, done: usize, failed: usize) -> String {
    let mut parts: Vec<String> = Vec::new();
    if running > 0 {
        parts.push(format!("{running} running"));
    }
    if done > 0 {
        parts.push(format!("{done} complete"));
    }
    if failed > 0 {
        parts.push(format!("{failed} failed"));
    }
    if parts.is_empty() {
        return total.to_string();
    }
    if running + done + failed < total {
        parts.push(total.to_string());
    }
    parts.join(" · ")
}

pub fn format_note_time(iso: &str) -> String {
    let s = iso.trim();
    if s.is_empty() {
        return String::new();
    }
    if s.len() >= 16 && s.as_bytes()[4] == b'-' {
        // 2026-08-08T18:02:00 → Aug 8, 18:02
        let day: u32 = s[8..10].parse().unwrap_or(0);
        let month = match &s[5..7] {
            "01" => "Jan",
            "02" => "Feb",
            "03" => "Mar",
            "04" => "Apr",
            "05" => "May",
            "06" => "Jun",
            "07" => "Jul",
            "08" => "Aug",
            "09" => "Sep",
            "10" => "Oct",
            "11" => "Nov",
            "12" => "Dec",
            _ => &s[5..7],
        };
        let hm = if s.len() >= 16 { &s[11..16] } else { "" };
        return format!("{month} {day}, {hm}");
    }
    s.to_string()
}

pub fn note_fields_view(fields: &Value) -> (String, String, Vec<(String, String)>) {
    let obj = fields.as_object();
    let get = |k: &str| {
        obj.and_then(|m| m.get(k))
            .map(|v| match v {
                Value::String(s) => s.trim().to_string(),
                other => other.to_string(),
            })
            .unwrap_or_default()
    };
    let mut title = {
        let s = get("summary");
        if !s.is_empty() {
            s
        } else {
            let t = get("title");
            if !t.is_empty() {
                t
            } else {
                get("issue")
            }
        }
    };
    let mut body = {
        let d = get("detail");
        if !d.is_empty() {
            d
        } else {
            let b = get("body");
            if !b.is_empty() {
                b
            } else {
                let n = get("notes");
                if !n.is_empty() {
                    n
                } else {
                    get("description")
                }
            }
        }
    };
    let skip = [
        "summary",
        "title",
        "issue",
        "detail",
        "body",
        "notes",
        "description",
    ];
    let mut extras = Vec::new();
    if let Some(map) = obj {
        for (k, v) in map {
            if skip.contains(&k.as_str()) {
                continue;
            }
            let val = match v {
                Value::String(s) => s.trim().to_string(),
                other => other.to_string(),
            };
            if !val.is_empty() {
                extras.push((k.clone(), val));
            }
        }
    }
    if title.is_empty() && body.is_empty() && !extras.is_empty() {
        title = extras.remove(0).1;
    }
    if title.is_empty() && !body.is_empty() {
        if let Some(line) = body.lines().find(|l| !l.trim().is_empty()) {
            title = line.trim().chars().take(120).collect();
            if body.trim() == line.trim() {
                body.clear();
            }
        }
    }
    (title, body, extras)
}

pub fn new_note_id() -> String {
    let raw = uuid::Uuid::new_v4().simple().to_string();
    format!("n-{}", &raw[..12])
}

/// TUI timeline role (white / cyan / dim cyan / yellow / red / magenta).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventRole {
    User,
    Model,
    ModelDim,
    Session,
    Error,
    System,
    Other,
}

/// TUI ``EVENT_TYPE_STYLE`` brand role (cream / complete / running / failed / cancelled).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrandRole {
    Cream,
    Complete,
    Running,
    Failed,
    Cancelled,
}

const TOOL_FAMILY_READ: &[&str] = &[
    "read_file",
    "grep",
    "list_dir",
    "web_search",
    "read_resource",
    "list_resources",
    "search_tool",
    "search_mcp",
];
const TOOL_FAMILY_WRITE: &[&str] = &[
    "search_replace",
    "write_file",
    "create_file",
    "todo_write",
    "update_goal",
    "image_gen",
    "image_edit",
    "image_to_video",
    "reference_to_video",
];
const TOOL_FAMILY_SHELL: &[&str] = &[
    "run_terminal_command",
    "get_command_or_subagent_output",
    "kill_command_or_subagent",
    "wait_commands_or_subagents",
    "monitor",
    "scheduler_create",
    "scheduler_delete",
    "scheduler_list",
];
const TOOL_FAMILY_AGENT: &[&str] = &[
    "spawn_subagent",
    "ask_user_question",
    "enter_plan_mode",
    "exit_plan_mode",
];
const TOOL_FAMILY_MCP_WRAPPER: &[&str] = &["use_tool", "call_mcp", "call_mcp_tool", "mcp_tool"];

/// Map a tool id to read | write | shell | agent | mcp | other.
pub fn tool_family(name: &str) -> &'static str {
    let n = name.trim();
    if n.contains("__") || n.starts_with("mcp_") || TOOL_FAMILY_MCP_WRAPPER.contains(&n) {
        return "mcp";
    }
    if TOOL_FAMILY_READ.contains(&n) {
        return "read";
    }
    if TOOL_FAMILY_WRITE.contains(&n) {
        return "write";
    }
    if TOOL_FAMILY_SHELL.contains(&n) {
        return "shell";
    }
    if TOOL_FAMILY_AGENT.contains(&n) {
        return "agent";
    }
    let low = n.to_ascii_lowercase();
    if ["read", "get", "list", "search", "grep", "find"]
        .iter()
        .any(|k| low.contains(k))
    {
        return "read";
    }
    if ["write", "edit", "create", "update", "delete", "save"]
        .iter()
        .any(|k| low.contains(k))
    {
        return "write";
    }
    if ["run", "exec", "shell", "terminal", "wait", "kill"]
        .iter()
        .any(|k| low.contains(k))
    {
        return "shell";
    }
    "other"
}

fn human_tool_token(part: &str) -> String {
    part.replace(['_', '-'], " ").trim().to_string()
}

/// Operator tool label: spaces, not snake_case; marketplace ``server · method``.
pub fn format_tool_display(name: &str) -> String {
    let n = name.trim();
    if n.is_empty() {
        return "?".into();
    }
    if let Some((server, method)) = n.split_once("__") {
        let server = human_tool_token(server);
        let method = human_tool_token(method);
        if !server.is_empty() && !method.is_empty() {
            return format!("{server} · {method}");
        }
    }
    if let Some(rest) = n.strip_prefix("mcp_") {
        if !rest.is_empty() {
            return format!("mcp · {}", human_tool_token(rest));
        }
    }
    human_tool_token(n)
}

/// Timeline summary remainder after the tool label (name already shown beside it).
pub fn list_event_detail(summary: &str, tool_name: &str) -> String {
    let s = summary.trim();
    let label = if tool_name.trim().is_empty() {
        String::new()
    } else {
        format_tool_display(tool_name)
    };
    if !label.is_empty() {
        if let Some(rest) = s.strip_prefix(&label) {
            return rest.trim().to_string();
        }
        let raw = tool_name.trim();
        if let Some(rest) = s.strip_prefix(raw) {
            return rest.trim().to_string();
        }
    }
    s.to_string()
}

/// Brand color for a tool name. ``None`` is dim / muted (unknown family).
pub fn tool_brand_role(name: &str, is_error: bool) -> Option<BrandRole> {
    if is_error {
        return Some(BrandRole::Failed);
    }
    match tool_family(name) {
        "read" | "agent" => Some(BrandRole::Cream),
        "write" => Some(BrandRole::Complete),
        "shell" => Some(BrandRole::Running),
        "mcp" => Some(BrandRole::Cancelled),
        _ => None,
    }
}

pub fn is_tool_identity(kind: &str, event_type: &str, tool_name: &str) -> bool {
    !tool_name.trim().is_empty()
        && (kind == "tool" || kind == "tool_result" || event_type.contains("tool"))
}

/// How an expanded body should paint.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BodyPaint {
    Empty,
    Plain,
    Markdown,
    Json,
    /// Monospaced code chrome (file dumps, shell commands) — not Markdown.
    Code,
    Image,
}

/// One timeline search hit: event index, field name, snippet containing the needle.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TimelineHit {
    pub index: i64,
    pub field: String,
    pub snippet: String,
}

/// Identity keys from TUI ``EVENT_TYPE_STYLE``.
pub fn event_type_brand_role(event_type: &str) -> BrandRole {
    match event_type.trim() {
        "user_message_chunk"
        | "agent_message_chunk"
        | "agent_thought_chunk"
        | "plan"
        | "subagent_spawned"
        | "subagent_finished"
        | "user"
        | "assistant"
        | "thought"
        | "subagent" => BrandRole::Cream,
        "tool_call" | "tool_call_update" | "tool_result" => BrandRole::Complete,
        "task_backgrounded"
        | "task_completed"
        | "scheduled_task_created"
        | "scheduled_task_updated"
        | "scheduled_task_fired"
        | "scheduled_task_deleted"
        | "turn_completed"
        | "current_mode_update"
        | "retry_state"
        | "goal_updated"
        | "session_recap"
        | "auto_compact_started"
        | "auto_compact_completed"
        | "compaction_checkpoint"
        | "hook_execution"
        | "hook_annotation"
        | "turn_started"
        | "turn_ended"
        | "session" => BrandRole::Running,
        "session_error" | "error" | "turn_error" | "fatal_error" => BrandRole::Failed,
        "system" => BrandRole::Cancelled,
        other => kind_brand_role(other),
    }
}

fn kind_brand_role(kind: &str) -> BrandRole {
    match kind {
        "user" | "agent" | "thought" | "plan" | "subagent" => BrandRole::Cream,
        "tool" | "tool_result" => BrandRole::Complete,
        "session" | "task" => BrandRole::Running,
        "error" => BrandRole::Failed,
        "system" => BrandRole::Cancelled,
        _ => BrandRole::Cancelled,
    }
}

/// Brand role for a timeline row: identity type, then kind, then error flag.
pub fn event_brand_role(event_type: &str, kind: &str, is_error: bool) -> BrandRole {
    if is_error || kind == "error" || event_type.ends_with("_error") || event_type == "error" {
        return BrandRole::Failed;
    }
    if !event_type.trim().is_empty() {
        return event_type_brand_role(event_type);
    }
    kind_brand_role(kind)
}

/// Honest TUI words for task / schedule bookends (not “subagent”).
pub fn event_is_monitor(raw_input: &Value) -> bool {
    if raw_input.get("kind").and_then(|v| v.as_str()) == Some("monitor") {
        return true;
    }
    if raw_input
        .get("monitor_description")
        .and_then(|v| v.as_str())
        .is_some_and(|s| !s.is_empty())
    {
        return true;
    }
    if raw_input
        .get("output_file")
        .and_then(|v| v.as_str())
        .is_some_and(|p| p.contains("monitor-call"))
    {
        return true;
    }
    let desc = json_str_field(raw_input, "description");
    let low = desc.to_ascii_lowercase();
    low.starts_with("live ") && low.contains("watch")
}

pub fn job_event_label(event_type: &str, is_monitor: bool) -> Option<&'static str> {
    match event_type.trim() {
        "task_backgrounded" => Some(if is_monitor {
            "monitor"
        } else {
            "background start"
        }),
        "task_completed" => Some(if is_monitor {
            "monitor done"
        } else {
            "background done"
        }),
        "scheduled_task_created" => Some("schedule created"),
        "scheduled_task_updated" => Some("schedule updated"),
        "scheduled_task_fired" => Some("schedule fired"),
        "scheduled_task_deleted" => Some("schedule deleted"),
        _ => None,
    }
}

/// Recover command/cwd from the origin/main ``key=value`` bookend dump.
pub fn task_fields_from_content(content: &str) -> Vec<(String, String)> {
    let text = content.trim();
    if !text.starts_with("task_backgrounded") && !text.starts_with("task_completed") {
        return Vec::new();
    }
    const KEYS: [&str; 6] = [
        "tool_call_id",
        "task_id",
        "command",
        "cwd",
        "prompt_id",
        "mode",
    ];
    let mut marks: Vec<(usize, &str)> = Vec::new();
    for key in KEYS {
        let token = format!("{key}=");
        if let Some(idx) = text.find(&token) {
            if idx == 0
                || text
                    .as_bytes()
                    .get(idx - 1)
                    .is_some_and(|b| b.is_ascii_whitespace())
            {
                marks.push((idx, key));
            }
        }
    }
    marks.sort_by_key(|(i, _)| *i);
    let mut out: Vec<(String, String)> = Vec::new();
    for (i, (start, key)) in marks.iter().enumerate() {
        let val_start = start + key.len() + 1;
        let val_end = marks.get(i + 1).map(|(n, _)| *n).unwrap_or(text.len());
        let val = text.get(val_start..val_end).unwrap_or("").trim();
        if !val.is_empty() {
            out.push(((*key).to_string(), val.to_string()));
        }
    }
    if let Some(brace) = text.find('{') {
        if let Ok(extra) = serde_json::from_str::<Value>(&text[brace..]) {
            for key in [
                "command",
                "cwd",
                "description",
                "output_file",
                "display_command",
            ] {
                if out.iter().any(|(k, _)| k == key) {
                    continue;
                }
                if let Some(val) = extra.get(key).and_then(|v| v.as_str()) {
                    if !val.trim().is_empty() {
                        out.push((key.to_string(), val.to_string()));
                    }
                }
            }
        }
    }
    out
}

/// Summary remainder for a background / monitor / schedule bookend.
pub fn job_list_preview(event_type: &str, raw: &Value, content: &str) -> String {
    let dump = task_fields_from_content(content);
    let dump_get = |key: &str| {
        dump.iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
            .unwrap_or("")
    };
    let command = {
        let from_raw = json_str_field(raw, "command");
        let display = json_str_field(raw, "display_command");
        if !from_raw.is_empty() {
            from_raw
        } else if !display.is_empty() {
            display
        } else if !dump_get("command").is_empty() {
            dump_get("command").to_string()
        } else {
            dump_get("display_command").to_string()
        }
    };
    let desc = {
        let d = json_str_field(raw, "description");
        if d.is_empty() {
            json_str_field(raw, "monitor_description")
        } else {
            d
        }
    };
    if event_type.starts_with("scheduled_task_") {
        let human = json_str_field(raw, "human_schedule");
        let prompt: String = json_str_field(raw, "prompt").chars().take(48).collect();
        let mut bits: Vec<&str> = Vec::new();
        if !human.is_empty() {
            bits.push(human.as_str());
        }
        if !prompt.is_empty() {
            bits.push(prompt.as_str());
        }
        let joined = bits.join(" · ");
        let id = json_str_field(raw, "task_id");
        let text = if joined.is_empty() { id } else { joined };
        return capped_display(&text, 80);
    }
    if !command.is_empty() {
        let one = command.replace('\n', " ");
        return capped_display(&format!("$ {}", one.trim()), 80);
    }
    if !desc.is_empty() {
        return capped_display(&desc, 80);
    }
    let one = content.replace('\n', " ");
    let one = one.trim();
    if one.is_empty() || one.starts_with(event_type) || one.starts_with('{') {
        return String::new();
    }
    capped_display(one, 80)
}

/// Job id from a bookend (`task_id` / `id` / tool call id).
pub fn job_event_id(raw: &Value, tool_call_id: &str) -> String {
    let tid = json_str_field(raw, "task_id");
    if !tid.is_empty() {
        return tid;
    }
    let id = json_str_field(raw, "id");
    if !id.is_empty() {
        return id;
    }
    tool_call_id.trim().to_string()
}

/// Opposite bookend for the same `task_id` (start ↔ finish).
pub fn job_mate_index(
    event_type: &str,
    raw: &Value,
    tool_call_id: &str,
    others: &[(i64, String, Value, String)],
) -> Option<i64> {
    let tid = job_event_id(raw, tool_call_id);
    if tid.is_empty() {
        return None;
    }
    let want = if event_type == "task_backgrounded" {
        "task_completed"
    } else if event_type == "task_completed" {
        "task_backgrounded"
    } else {
        return None;
    };
    others.iter().find_map(|(idx, et, oraw, ocid)| {
        if et == want && job_event_id(oraw, ocid) == tid {
            Some(*idx)
        } else {
            None
        }
    })
}

/// Exit code from the finish bookend (self when this *is* the finish).
pub fn job_exit_code(event_type: &str, raw: &Value, mate: Option<&Value>) -> Option<i64> {
    let finish = if event_type == "task_completed" {
        raw
    } else {
        mate?
    };
    finish.get("exit_code").and_then(|v| v.as_i64())
}

/// Last fire / last child from the merged overview row (not the bookend bag).
pub fn schedule_last_fire<'a>(
    schedules: &'a [crate::wire::ScheduleRow],
    task_id: &str,
) -> Option<(&'a str, &'a str)> {
    let tid = task_id.trim();
    if tid.is_empty() {
        return None;
    }
    let row = schedules.iter().find(|s| s.id == tid)?;
    Some((row.last_fired_at.as_str(), row.last_subagent_id.as_str()))
}

/// Last-line log class or finish fields — same words as TUI job status.
pub fn job_status(raw: &Value, content: &str, log_tail: &str) -> &'static str {
    for line in log_tail.lines().rev() {
        let token = line.split_whitespace().next().unwrap_or("");
        match token {
            "DONE" => return "done",
            "FAILED" => return "failed",
            "CANCELLED" => return "cancelled",
            _ => {}
        }
    }
    if raw.get("completed").and_then(|v| v.as_bool()) == Some(true)
        || content.contains("task_completed")
    {
        if raw.get("explicitly_killed").and_then(|v| v.as_bool()) == Some(true) {
            return "cancelled";
        }
        if raw.get("exit_code").and_then(|v| v.as_i64()) == Some(0) {
            return "done";
        }
        if raw
            .get("exit_code")
            .and_then(|v| v.as_i64())
            .is_some_and(|c| c != 0)
        {
            return "failed";
        }
        return "done";
    }
    "running"
}

/// Full command for a Timeline inspect card (not the list preview).
pub fn job_command(raw: &Value, content: &str) -> String {
    let dump = task_fields_from_content(content);
    let dump_cmd = dump
        .iter()
        .find(|(k, _)| k == "command" || k == "display_command")
        .map(|(_, v)| v.as_str())
        .unwrap_or("");
    let from_raw = json_str_field(raw, "command");
    let display = json_str_field(raw, "display_command");
    let cmd = if !from_raw.is_empty() {
        from_raw
    } else if !display.is_empty() {
        display
    } else {
        dump_cmd.to_string()
    };
    cmd.trim().to_string()
}

pub fn job_description(raw: &Value) -> String {
    let d = json_str_field(raw, "description");
    if !d.is_empty() {
        return d;
    }
    json_str_field(raw, "monitor_description")
}

pub fn job_output_path(raw: &Value) -> String {
    let p = json_str_field(raw, "output_file");
    if !p.is_empty() {
        return p;
    }
    json_str_field(raw, "outputPath")
}

/// Host file for a job log: ``session/terminal/<name>`` when that file exists.
pub fn job_log_file(session_dir: &str, output_path: &str) -> Option<PathBuf> {
    let text = output_path.trim();
    if text.is_empty() {
        return None;
    }
    let recorded = Path::new(text);
    if let Some(name) = recorded.file_name() {
        if !session_dir.trim().is_empty() && !name.is_empty() {
            let local = Path::new(session_dir).join("terminal").join(name);
            if local.is_file() {
                return Some(local);
            }
        }
    }
    let resolved = if recorded.is_absolute() || session_dir.trim().is_empty() {
        recorded.to_path_buf()
    } else {
        Path::new(session_dir).join(recorded)
    };
    if resolved.is_file() {
        Some(resolved)
    } else {
        None
    }
}

fn job_log_read(path: &Path, max_chars: usize) -> String {
    let Ok(data) = std::fs::read(path) else {
        return String::new();
    };
    let start = data.len().saturating_sub(max_chars);
    String::from_utf8_lossy(&data[start..]).into_owned()
}

/// Last bytes of a monitor/shell log (status / small preview).
pub fn job_log_tail(path: &str) -> String {
    const MAX_CHARS: usize = 2_000;
    if path.trim().is_empty() {
        return String::new();
    }
    job_log_read(Path::new(path), MAX_CHARS)
}

/// Open-event job log: session ``terminal/`` first, open-event character ceiling.
pub fn job_inspect_log(session_dir: &str, output_path: &str) -> String {
    let Some(path) = job_log_file(session_dir, output_path) else {
        return String::new();
    };
    job_log_read(&path, EXTRACT_CHARS)
}

/// Summary remainder for a spawn/finish bookend (not the dump line).
pub fn subagent_list_preview(event_type: &str, raw: &Value, content: &str) -> String {
    if event_type == "subagent_spawned" {
        let typ = json_str_field(raw, "subagentType");
        let typ = if typ.is_empty() {
            json_str_field(raw, "subagent_type")
        } else {
            typ
        };
        let desc = json_str_field(raw, "description");
        let (typ, desc) = if typ.is_empty() && desc.is_empty() {
            spawn_from_content(content)
        } else {
            (typ, desc)
        };
        let text = if !desc.is_empty() { desc } else { typ };
        return capped_display(&text, 80);
    }
    if event_type == "subagent_finished" {
        let desc = json_str_field(raw, "description");
        if !desc.is_empty() {
            return capped_display(&desc, 80);
        }
        let mut status = json_str_field(raw, "status");
        if status.is_empty() {
            status = finish_from_content(content).0;
        }
        return capped_display(&status, 80);
    }
    String::new()
}

fn spawn_from_content(content: &str) -> (String, String) {
    let text = content.trim();
    let rest = text
        .strip_prefix("Spawned ")
        .or_else(|| text.strip_prefix("spawned "))
        .unwrap_or("");
    if rest.is_empty() {
        return (String::new(), String::new());
    }
    if let Some((typ, desc)) = rest.split_once(':') {
        (typ.trim().to_string(), desc.trim().to_string())
    } else {
        (String::new(), rest.to_string())
    }
}

fn finish_from_content(content: &str) -> (String, Option<i64>) {
    let text = content.trim();
    let Some(rest) = text
        .strip_prefix("Subagent finished")
        .or_else(|| text.strip_prefix("subagent finished"))
    else {
        return (String::new(), None);
    };
    let mut rest = rest.trim();
    let mut ms = None;
    if let Some(idx) = rest.find("duration_ms=") {
        let tail = &rest[idx + "duration_ms=".len()..];
        let num = tail.split_whitespace().next().unwrap_or("");
        ms = num.parse().ok();
        rest = rest[..idx].trim();
    }
    let status = rest
        .split_whitespace()
        .find(|p| p.chars().all(|c| c.is_ascii_alphabetic()))
        .unwrap_or("")
        .to_string();
    (status, ms)
}

/// TUI-style human type label: Grok wire id with underscores → spaces.
///
/// Prefers honest job labels, then control `type_label`, then `event_type`.
pub fn human_event_type_label(
    event_type: &str,
    type_label: &str,
    kind: &str,
    is_monitor: bool,
) -> String {
    if let Some(honest) = job_event_label(event_type, is_monitor) {
        return honest.to_string();
    }
    let raw = if !type_label.trim().is_empty() {
        type_label.trim()
    } else if !event_type.trim().is_empty() {
        event_type.trim()
    } else {
        kind.trim()
    };
    if raw.is_empty() {
        return String::new();
    }
    raw.replace('_', " ")
}

/// Caption for filter range meta: never empty (avoids a11y name paint).
///
/// Returns `None` when there is nothing honest to show.
pub fn timeline_count_caption(meta: &str) -> Option<&str> {
    let t = meta.trim();
    if t.is_empty() {
        None
    } else {
        Some(t)
    }
}

/// Snippet around *start* (char index) that includes the needle.
pub fn snippet_around(text: &str, start: usize, needle_chars: usize, radius: usize) -> String {
    let chars: Vec<char> = text.chars().collect();
    if chars.is_empty() {
        return String::new();
    }
    let lo = start.saturating_sub(radius);
    let hi = (start + needle_chars.saturating_add(radius)).min(chars.len());
    let mut chunk: String = chars[lo..hi].iter().collect();
    chunk = chunk.replace(['\n', '\r'], " ");
    if lo > 0 {
        chunk.insert(0, '…');
    }
    if hi < chars.len() {
        chunk.push('…');
    }
    chunk
}

fn field_hit(text: &str, needle: &str) -> Option<String> {
    if text.is_empty() || needle.is_empty() {
        return None;
    }
    let lower = text.to_ascii_lowercase();
    let pos = lower.find(needle)?;
    // ``find`` is byte-based on the lowercased UTF-8; map back to char index.
    let start = lower[..pos].chars().count();
    Some(snippet_around(text, start, needle.chars().count(), 40))
}

/// First field on *ev* that contains *query* (casefold substring).
pub fn timeline_query_hit(ev: &crate::wire::TimelineEvent, query: &str) -> Option<TimelineHit> {
    let needle = query.trim().to_ascii_lowercase();
    if needle.is_empty() {
        return None;
    }
    if !ev.match_field.is_empty()
        && !ev.match_snippet.is_empty()
        && ev.match_snippet.to_ascii_lowercase().contains(&needle)
    {
        return Some(TimelineHit {
            index: ev.index,
            field: ev.match_field.clone(),
            snippet: ev.match_snippet.clone(),
        });
    }
    let preview = ev.preview.clone();
    let fields: [(&str, &str); 6] = [
        ("type", ev.event_type.as_str()),
        ("type_label", ev.type_label.as_str()),
        ("tool", ev.tool_name.as_str()),
        ("heading", ev.heading.as_str()),
        ("preview", preview.as_str()),
        ("content", ev.content.as_str()),
    ];
    for (field, text) in fields {
        if let Some(snippet) = field_hit(text, &needle) {
            return Some(TimelineHit {
                index: ev.index,
                field: field.to_string(),
                snippet,
            });
        }
    }
    None
}

/// Complete match set for *query* over *events* (order preserved).
pub fn timeline_search<'a>(
    events: impl IntoIterator<Item = &'a crate::wire::TimelineEvent>,
    query: &str,
) -> Vec<TimelineHit> {
    if query.trim().is_empty() {
        return Vec::new();
    }
    events
        .into_iter()
        .filter_map(|ev| timeline_query_hit(ev, query))
        .collect()
}

/// User / agent chat rows (TUI ``MESSAGE_TYPES`` minus thought).
///
/// Thought stays plain italic in the TUI; chat always goes through Markdown.
pub fn is_chat_message(kind: &str, event_type: &str) -> bool {
    let et = event_type.trim();
    if et.contains("message_chunk")
        || et == "user_message"
        || et == "agent_message"
        || et == "user"
        || et == "assistant"
    {
        return true;
    }
    matches!(kind.trim(), "user" | "agent" | "assistant")
}

/// Paint path for an expanded timeline / turn body.
///
/// Chat message types match the TUI: always Markdown when open (hard breaks
/// via [`message_markdown_source`]). Tools stay JSON / plain / code.
pub fn body_paint(kind: &str, body: &str, expanded: bool) -> BodyPaint {
    body_paint_for(kind, "", body, expanded)
}

/// Like [`body_paint`] with wire ``event_type`` for chat detection.
pub fn body_paint_for(kind: &str, event_type: &str, body: &str, expanded: bool) -> BodyPaint {
    if body.trim().is_empty() {
        return BodyPaint::Empty;
    }
    if !expanded {
        return BodyPaint::Plain;
    }
    if looks_like_json(body) {
        return BodyPaint::Json;
    }
    // TUI: MESSAGE_TYPES always Markdown(hard-breaks), not cue-gated.
    if is_chat_message(kind, event_type) {
        return BodyPaint::Markdown;
    }
    // Tool bodies: never Markdown (python `#` comments would false-positive).
    // File dumps → Code chrome; shell streams stay Plain (mono at paint site).
    if kind == "tool" || kind == "tool_result" {
        if looks_like_source_code(body) || looks_like_diff_text(body) {
            return BodyPaint::Code;
        }
        return BodyPaint::Plain;
    }
    if looks_like_markdown(body) {
        return BodyPaint::Markdown;
    }
    if looks_like_source_code(body) {
        return BodyPaint::Code;
    }
    BodyPaint::Plain
}

/// Lightweight source cue aligned with TUI ``_looks_like_source_code``.
pub fn looks_like_source_code(text: &str) -> bool {
    let sample = text.chars().take(6000).collect::<String>();
    if sample.trim().is_empty() {
        return false;
    }
    let lines: Vec<&str> = sample.lines().collect();
    if lines.len() < 2 {
        let s = sample.trim_start();
        return s.starts_with("def ")
            || s.starts_with("class ")
            || s.starts_with("fn ")
            || s.starts_with("func ")
            || s.starts_with("import ")
            || s.starts_with("package ")
            || s.starts_with("const ")
            || s.starts_with("let ")
            || s.starts_with("var ")
            || s.starts_with("#!/");
    }
    let mut hits = 0u32;
    let mut indented = 0u32;
    for ln in lines.iter().take(80) {
        if (ln.starts_with(' ') || ln.starts_with('\t')) && !ln.trim().is_empty() {
            indented += 1;
        }
        let st = ln.trim();
        // Skip comment-only lines (incl. python `#`) — same as TUI.
        if st.is_empty()
            || st.starts_with('#')
            || st.starts_with("//")
            || st.starts_with("/*")
            || st.starts_with('*')
            || st.starts_with("--")
        {
            continue;
        }
        // Shape first (TUI): braces / block colons, then keywords.
        if st.ends_with('{')
            || st.ends_with('}')
            || st.ends_with(");")
            || st.ends_with("};")
            || st.ends_with("]:")
            || st.ends_with(':')
            || st.starts_with("def ")
            || st.starts_with("class ")
            || st.starts_with("async ")
            || st.starts_with("import ")
            || st.starts_with("from ")
            || st.starts_with("fn ")
            || st.starts_with("func ")
            || st.starts_with("pub ")
            || st.starts_with("package ")
            || st.starts_with("const ")
            || st.starts_with("let ")
            || st.starts_with("var ")
            || st.starts_with("export ")
            || st.starts_with("function ")
            || st.starts_with("type ")
            || st.starts_with("interface ")
            || st.starts_with("impl ")
            || st.starts_with("struct ")
            || st.starts_with("enum ")
            || st.starts_with("return ")
            || st.starts_with("if ")
            || st.starts_with("for ")
            || st.starts_with("while ")
            || st.starts_with("match ")
            || st.starts_with("use ")
            || st.starts_with("mod ")
            || st.starts_with("#!")
        {
            hits += 1;
        }
    }
    // Short dumps (import + def + return) need hits>=3; denser indent keeps 2.
    if indented >= 2 && hits >= 2 {
        return true;
    }
    hits >= 3
}

fn looks_like_diff_text(text: &str) -> bool {
    let mut hits = 0u32;
    for ln in text.lines().take(40) {
        if ln.starts_with("@@")
            || ln.starts_with("--- ")
            || ln.starts_with("+++ ")
            || ln.starts_with('+')
            || ln.starts_with('-')
        {
            hits += 1;
        }
    }
    hits >= 3
}

/// Assistant turn body: markdown when the source has markdown cues or is chat.
pub fn turn_assistant_paint(body: &str) -> BodyPaint {
    body_paint_for("agent", "agent_message_chunk", body, true)
}

/// Plain text for a turn card (paste into a report).
pub fn extract_turn(label: &str, user: &str, assistant: &str) -> String {
    let mut out = String::new();
    if !label.is_empty() {
        out.push_str(label.trim());
        out.push('\n');
    }
    if !user.trim().is_empty() {
        out.push_str("User\n");
        out.push_str(user.trim());
        out.push('\n');
    }
    if !assistant.trim().is_empty() {
        if !out.ends_with('\n') {
            out.push('\n');
        }
        out.push_str("Assistant\n");
        out.push_str(assistant.trim());
        out.push('\n');
    }
    out
}

/// Plain text for a timeline event (heading, tool fields, body).
pub fn extract_event(ev: &crate::wire::TimelineEvent) -> String {
    let mut out = String::new();
    let head = if ev.heading.is_empty() {
        ev.type_label.as_str()
    } else {
        ev.heading.as_str()
    };
    if let Some(turn) = ev.turn_index {
        out.push_str(&format!("#{} · turn {turn} {head}\n", ev.index));
    } else {
        out.push_str(&format!("#{} {head}\n", ev.index));
    }
    if !ev.tool_name.is_empty() {
        out.push_str(&ev.tool_name);
        out.push('\n');
    }
    let fields = if ev.tool_fields.is_empty() {
        tool_fields_from_raw(&ev.tool_name, &ev.raw_input, EXTRACT_CHARS)
    } else {
        ev.tool_fields
            .iter()
            .map(|f| ToolField {
                id: f.id.clone(),
                label: f.label.clone(),
                value: f.value.clone(),
            })
            .collect()
    };
    for field in fields {
        if field.value.is_empty() {
            continue;
        }
        out.push_str(&field.label);
        out.push_str(": ");
        out.push_str(&field.value);
        out.push('\n');
    }
    let body = if ev.content.is_empty() {
        ev.preview.as_str()
    } else {
        ev.content.as_str()
    };
    let body = sanitize_console_text(&display_tool_output(body, &ev.tool_name));
    if !body.trim().is_empty() {
        out.push('\n');
        out.push_str(body.trim());
        out.push('\n');
    }
    out
}

/// Map control ``kind`` (+ error flag) onto the same roles as the TUI type column.
pub fn event_role(kind: &str, is_error: bool) -> EventRole {
    if is_error || kind == "error" {
        return EventRole::Error;
    }
    match kind {
        "user" => EventRole::User,
        "agent" | "plan" | "tool" | "subagent" => EventRole::Model,
        "thought" | "tool_result" => EventRole::ModelDim,
        "session" | "task" => EventRole::Session,
        "system" => EventRole::System,
        _ => EventRole::Other,
    }
}

/// Open-card body only (no heading). Yank uses [`extract_event`].
pub fn event_body_text(ev: &crate::wire::TimelineEvent) -> String {
    let raw = if ev.content.is_empty() {
        ev.preview.as_str()
    } else {
        ev.content.as_str()
    };
    sanitize_console_text(&display_tool_output(raw, &ev.tool_name))
}

/// Collapsed cards use the one-line preview; the open card uses full ``content``.
pub fn timeline_body_text(
    preview: &str,
    content: &str,
    selected: bool,
    max_collapsed: usize,
) -> String {
    if selected {
        if !content.is_empty() {
            content.to_string()
        } else {
            preview.to_string()
        }
    } else if !preview.is_empty() {
        preview.to_string()
    } else {
        content.chars().take(max_collapsed).collect()
    }
}

/// Same cues as TUI ``panel_render.looks_like_markdown``.
pub fn looks_like_markdown(text: &str) -> bool {
    let s = text.trim_start();
    if s.is_empty() {
        return false;
    }
    if s.starts_with('#') || s.contains("```") {
        return true;
    }
    if s.starts_with("- ") || s.starts_with("* ") || s.starts_with("> ") {
        return true;
    }
    if s.contains("**") || s.contains("__") || s.contains("](http") || s.contains("](/") {
        return true;
    }
    s.contains("\n## ") || s.contains("\n# ")
}

/// iced ``text_editor::highlight`` language token (syntect short name).
///
/// Prefer path extension, then tool field id, then light content cues.
/// Empty string means plain mono (caller may still use a non-highlight path).
pub fn syntax_for_path(path: &str) -> &'static str {
    let p = path.trim().to_ascii_lowercase();
    let base = p.rsplit('/').next().unwrap_or(p.as_str());
    if base == "dockerfile" || base.ends_with(".dockerfile") {
        return "dockerfile";
    }
    match p.rsplit('.').next().unwrap_or("") {
        "py" | "pyi" => "py",
        "rs" => "rs",
        "js" | "mjs" | "cjs" => "js",
        "jsx" => "jsx",
        "ts" => "ts",
        "tsx" => "tsx",
        "go" => "go",
        "java" => "java",
        "c" | "h" => "c",
        "cpp" | "cc" | "cxx" | "hpp" | "hh" => "cpp",
        "json" | "jsonl" => "json",
        "md" | "markdown" => "md",
        "toml" => "toml",
        "yml" | "yaml" => "yaml",
        "sh" | "bash" | "zsh" => "bash",
        "css" => "css",
        "html" | "htm" => "html",
        "xml" => "xml",
        "sql" => "sql",
        "rb" => "rb",
        "diff" | "patch" => "diff",
        _ => "",
    }
}

/// Language for a tool input field (`command` → bash, path-backed edits → path lang).
pub fn syntax_for_tool_field(field_id: &str, path_hint: &str, value: &str) -> &'static str {
    match field_id {
        "command" | "cmd" | "script" => "bash",
        "pattern" => "regexp",
        "old_string" | "new_string" => {
            let from_path = syntax_for_path(path_hint);
            if !from_path.is_empty() {
                return from_path;
            }
            syntax_for_source_body(value)
        }
        _ if looks_like_json(value) => "json",
        _ => {
            let from_path = syntax_for_path(path_hint);
            if !from_path.is_empty() {
                return from_path;
            }
            if looks_like_json(value) {
                "json"
            } else {
                ""
            }
        }
    }
}

/// Language for tool *output* body (read_file dump, JSON result, …).
pub fn syntax_for_tool_output(tool_name: &str, path_hint: &str, body: &str) -> &'static str {
    let from_path = syntax_for_path(path_hint);
    if !from_path.is_empty() {
        return from_path;
    }
    if looks_like_json(body) {
        return "json";
    }
    let t = tool_name.trim();
    if t == "run_terminal_command" || t == "get_command_or_subagent_output" || t == "monitor" {
        // Shell stream: monospaced syntax pane (txt), not a dead plain dump.
        return "txt";
    }
    if t == "read_file" || t == "search_replace" {
        return syntax_for_source_body(body);
    }
    syntax_for_source_body(body)
}

fn syntax_for_source_body(body: &str) -> &'static str {
    let sample: String = body.chars().take(4000).collect();
    let head = sample.trim_start();
    if head.starts_with("#!") && head.contains("python") {
        return "py";
    }
    if head.starts_with("#!") {
        return "bash";
    }
    let py = ["def ", "class ", "import ", "from ", "async def "]
        .iter()
        .filter(|t| sample.contains(*t))
        .count();
    if py >= 2 {
        return "py";
    }
    let rs = ["fn ", "impl ", "pub ", "let mut ", "use "]
        .iter()
        .filter(|t| sample.contains(*t))
        .count();
    if rs >= 2 {
        return "rs";
    }
    if sample.contains("package ") && sample.contains("func ") {
        return "go";
    }
    if sample.contains("function ") || sample.contains("const ") && sample.contains("=>") {
        return "js";
    }
    if looks_like_json(&sample) {
        return "json";
    }
    ""
}

/// Path hint from tool rawInput (target_file / file_path / path).
pub fn path_hint_from_raw(raw: &Value) -> String {
    let obj = match raw {
        Value::Object(m) => m,
        _ => return String::new(),
    };
    for key in ["target_file", "file_path", "path", "target_directory"] {
        if let Some(Value::String(s)) = obj.get(key) {
            let t = s.trim();
            if !t.is_empty() {
                return t.to_string();
            }
        }
    }
    String::new()
}

/// Same cue as TUI ``render_detail._looks_json``.
pub fn looks_like_json(text: &str) -> bool {
    let s = text.trim();
    if s.is_empty() {
        return false;
    }
    let first = s.as_bytes()[0];
    let last = *s.as_bytes().last().unwrap_or(&0);
    (first == b'{' || first == b'[') && (last == b'}' || last == b']')
}

/// Pretty-print JSON when valid; otherwise return the original string.
pub fn pretty_json(text: &str) -> String {
    let s = text.trim();
    match serde_json::from_str::<Value>(s) {
        Ok(v) => serde_json::to_string_pretty(&v).unwrap_or_else(|_| s.to_string()),
        Err(_) => s.to_string(),
    }
}

/// TUI message bodies: soft newlines become Markdown hard breaks.
pub fn message_md_hard_breaks(body: &str) -> String {
    body.split('\n').collect::<Vec<_>>().join("  \n")
}

/// Sanitize + hard-break a chat message so iced markdown keeps lists and lines.
pub fn message_markdown_source(body: &str) -> String {
    message_md_hard_breaks(&sanitize_console_text(body))
}

/// Strip ANSI / C0 noise like TUI ``sanitize_console_text`` (display mode).
pub fn sanitize_console_text(text: &str) -> String {
    if text.is_empty() {
        return String::new();
    }
    let mut out = String::with_capacity(text.len());
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        if c == '\u{1b}' {
            i += 1;
            if i >= chars.len() {
                break;
            }
            match chars[i] {
                '[' => {
                    i += 1;
                    while i < chars.len() {
                        let ch = chars[i];
                        i += 1;
                        if ('@'..='~').contains(&ch) {
                            break;
                        }
                    }
                }
                ']' => {
                    i += 1;
                    while i < chars.len() {
                        let ch = chars[i];
                        i += 1;
                        if ch == '\u{07}' {
                            break;
                        }
                        if ch == '\u{1b}' && i < chars.len() && chars[i] == '\\' {
                            i += 1;
                            break;
                        }
                    }
                }
                _ => i += 1,
            }
            continue;
        }
        if c == '\r' {
            out.push('\n');
            i += 1;
            continue;
        }
        if c.is_control() && c != '\n' && c != '\t' {
            i += 1;
            continue;
        }
        out.push(c);
        i += 1;
    }
    let mut lines: Vec<&str> = Vec::new();
    for ln in out.split('\n') {
        let t = ln.trim_end();
        if t.is_empty() {
            if lines.last().is_some_and(|p| p.is_empty()) {
                continue;
            }
            lines.push("");
            continue;
        }
        lines.push(t);
    }
    let joined = lines.join("\n");
    let mut collapsed = String::new();
    let mut nl = 0;
    for ch in joined.chars() {
        if ch == '\n' {
            nl += 1;
            if nl <= 3 {
                collapsed.push('\n');
            }
        } else {
            nl = 0;
            collapsed.push(ch);
        }
    }
    collapsed
}

pub fn event_matches_kind(kind: &str, is_error: bool, mode: KindFilter) -> bool {
    if mode == KindFilter::All {
        return true;
    }
    let kind = kind.to_ascii_lowercase();
    match mode {
        KindFilter::All => true,
        KindFilter::Tools => kind == "tool" || kind == "tool_result",
        KindFilter::User => kind == "user",
        KindFilter::Asst => kind == "agent" || kind == "thought",
        KindFilter::Sess => {
            // TUI sess = SESSION_CHROME_TYPES → kind session | system | error.
            matches!(kind.as_str(), "system" | "session" | "error")
        }
        KindFilter::Subagents => kind == "subagent",
        KindFilter::Background => kind == "task",
        KindFilter::Workflows => false,
        KindFilter::Errors => is_error || kind == "error",
    }
}

/// Kind filter plus tool name (Workflows is ``tool_name == workflow``).
pub fn event_matches_filter(kind: &str, tool_name: &str, is_error: bool, mode: KindFilter) -> bool {
    if mode == KindFilter::Workflows {
        return tool_name == "workflow";
    }
    event_matches_kind(kind, is_error, mode)
}

pub fn is_unknown_method(err: &str) -> bool {
    let low = err.to_ascii_lowercase();
    low.contains("method not found") || low.contains("-32601")
}

pub fn control_down_message(err: &str) -> String {
    let s = err.trim();
    if is_unknown_method(s) {
        return "control owner is older · run: groket serve restart".into();
    }
    let short = if s.len() > 140 {
        format!("{}…", &s[..137])
    } else {
        s.to_string()
    };
    let low = short.to_ascii_lowercase();
    if short.is_empty()
        || low.contains("no such file")
        || low.contains("connection refused")
        || low.contains("not found")
        || low.contains("os error 2")
        || low.contains("broken pipe")
        || low.contains("timed out")
        || low.contains("econnrefused")
        || low.contains("enoent")
        || low.contains("resource temporarily unavailable")
        || low.contains("os error 35")
    {
        "control socket down · run: groket serve -d".into()
    } else {
        format!("control error · {short}")
    }
}

/// One TUI-aligned tool input field (HUD inspect, not a JSON dump).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolField {
    pub id: String,
    pub label: String,
    pub value: String,
}

/// Strip Grok ``N→`` / ``N->`` prefixes from a ``read_file`` body.
pub fn strip_inline_line_prefixes(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for (i, line) in text.split('\n').enumerate() {
        if i > 0 {
            out.push('\n');
        }
        out.push_str(&strip_one_line_prefix(line));
    }
    out
}

fn strip_one_line_prefix(line: &str) -> String {
    let rest = line.trim_start();
    let indent_len = line.len() - rest.len();
    let indent = &line[..indent_len];
    let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
    if digits == 0 {
        return line.to_string();
    }
    let after = &rest[digits..];
    let stripped = if let Some(s) = after.strip_prefix('→') {
        s.strip_prefix(' ').unwrap_or(s)
    } else if let Some(s) = after.strip_prefix("->") {
        s.strip_prefix(' ').unwrap_or(s)
    } else {
        return line.to_string();
    };
    format!("{indent}{stripped}")
}

/// True when *text* looks like a numbered Grok ``read_file`` dump.
pub fn looks_like_numbered_file(text: &str) -> bool {
    let mut hits = 0;
    let mut lines = 0;
    for line in text.lines().take(12) {
        lines += 1;
        let rest = line.trim_start();
        let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
        if digits == 0 {
            continue;
        }
        let after = &rest[digits..];
        if after.starts_with('→') || after.starts_with("->") {
            hits += 1;
            if hits >= 2 {
                return true;
            }
        }
    }
    hits == 1 && lines <= 1
}

/// Display body: strip numbered prefixes on file dumps.
pub fn display_tool_output(text: &str, tool_name: &str) -> String {
    if tool_name.trim() == "read_file" || looks_like_numbered_file(text) {
        strip_inline_line_prefixes(text)
    } else {
        text.to_string()
    }
}

/// Path from ``image_gen`` / ``image_edit`` result JSON.
pub fn image_result_path(content: &str) -> String {
    let s = content.trim();
    if s.is_empty() || !(s.starts_with('{') || s.starts_with('[')) {
        return String::new();
    }
    let Ok(v) = serde_json::from_str::<Value>(s) else {
        return String::new();
    };
    v.get("path")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .unwrap_or("")
        .to_string()
}

fn json_str_field(v: &Value, key: &str) -> String {
    match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(other) if !other.is_null() => other.to_string(),
        _ => String::new(),
    }
}

fn take_field(map: &serde_json::Map<String, Value>, keys: &[&str]) -> String {
    for key in keys {
        let s = json_str_field(&Value::Object(map.clone()), key);
        if !s.is_empty() {
            return s;
        }
    }
    String::new()
}

/// Fields for HUD inspect from ``rawInput`` (same keys as the TUI).
pub fn tool_fields_from_raw(tool_name: &str, raw: &Value, max_chars: usize) -> Vec<ToolField> {
    let Some(obj) = raw.as_object() else {
        return Vec::new();
    };
    let mut fields = Vec::new();
    let cut = |s: String| -> String {
        if max_chars > 0 && s.chars().count() > max_chars {
            s.chars().take(max_chars).collect()
        } else {
            s
        }
    };
    let push = |fields: &mut Vec<ToolField>, id: &str, label: &str, value: String| {
        if !value.is_empty() {
            fields.push(ToolField {
                id: id.into(),
                label: label.into(),
                value: cut(value),
            });
        }
    };
    let extra_except = |skip: &[&str]| -> String {
        let mut leftover = serde_json::Map::new();
        for (k, v) in obj {
            if !skip.contains(&k.as_str()) {
                leftover.insert(k.clone(), v.clone());
            }
        }
        if leftover.is_empty() {
            return String::new();
        }
        serde_json::to_string_pretty(&Value::Object(leftover)).unwrap_or_default()
    };
    match tool_name.trim() {
        "search_replace" => {
            push(
                &mut fields,
                "file_path",
                "File",
                take_field(obj, &["file_path", "target_file"]),
            );
            push(
                &mut fields,
                "old_string",
                "old_string",
                json_str_field(raw, "old_string"),
            );
            push(
                &mut fields,
                "new_string",
                "new_string",
                json_str_field(raw, "new_string"),
            );
            push(
                &mut fields,
                "extra",
                "extra",
                extra_except(&["file_path", "target_file", "old_string", "new_string"]),
            );
        }
        "run_terminal_command" => {
            push(
                &mut fields,
                "command",
                "command",
                json_str_field(raw, "command"),
            );
            push(&mut fields, "extra", "extra", extra_except(&["command"]));
        }
        "read_file" => {
            push(
                &mut fields,
                "target_file",
                "target_file",
                take_field(obj, &["target_file", "file_path"]),
            );
            push(
                &mut fields,
                "extra",
                "extra",
                extra_except(&["target_file", "file_path"]),
            );
        }
        "list_dir" => {
            push(
                &mut fields,
                "target_directory",
                "target_directory",
                take_field(obj, &["target_directory", "path"]),
            );
            push(
                &mut fields,
                "extra",
                "extra",
                extra_except(&["target_directory", "path"]),
            );
        }
        "grep" => {
            push(
                &mut fields,
                "pattern",
                "pattern",
                json_str_field(raw, "pattern"),
            );
            push(&mut fields, "extra", "extra", extra_except(&["pattern"]));
        }
        "web_search" => {
            push(&mut fields, "query", "query", json_str_field(raw, "query"));
            push(&mut fields, "url", "url", json_str_field(raw, "url"));
            push(
                &mut fields,
                "extra",
                "extra",
                extra_except(&["query", "url", "variant", "backend"]),
            );
        }
        _ => {
            for key in [
                "command",
                "query",
                "pattern",
                "target_file",
                "file_path",
                "path",
                "prompt",
            ] {
                push(&mut fields, key, key, json_str_field(raw, key));
            }
        }
    }
    fields
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_chars_matches_open_event_ceiling() {
        assert_eq!(EXTRACT_CHARS, crate::live::TIMELINE_OPEN_CHARS as usize);
    }

    #[test]
    fn duration_matches_tui_thresholds() {
        assert_eq!(fmt_duration(0.4), "<1s");
        assert_eq!(fmt_duration(12.0), "12s");
        assert_eq!(fmt_duration(125.0), "2m05s");
        assert_eq!(fmt_duration(3840.0), "1h04m");
    }

    #[test]
    fn session_duration_chip_hides_zero_and_prefers_display() {
        assert_eq!(session_duration_chip(0.0, "<1s"), "");
        assert_eq!(session_duration_chip(0.0, ""), "");
        assert_eq!(session_duration_chip(12.0, ""), "12s");
        assert_eq!(session_duration_chip(12.0, "—"), "12s");
        assert_eq!(session_duration_chip(125.0, "2m05s"), "2m05s");
    }

    #[test]
    fn soft_control_down_copy() {
        assert_eq!(
            control_down_message("connection refused"),
            "control socket down · run: groket serve -d"
        );
        assert_eq!(
            control_down_message("method not found"),
            "control owner is older · run: groket serve restart"
        );
        assert_ne!(
            control_down_message("method not found"),
            "control socket down · run: groket serve -d"
        );
    }

    #[test]
    fn list_status_prefers_status_then_outcome() {
        assert_eq!(list_status_label("complete", ""), "complete");
        assert_eq!(list_status_label("completed", ""), "complete");
        assert_eq!(list_status_label("—", "completed"), "complete");
        assert_eq!(list_status_label("", "awaiting_follow_up"), "awaiting");
        assert_eq!(list_status_label("", "cancelled"), "cancelled");
        assert_eq!(list_status_label("", "done"), "complete");
        assert_eq!(list_status_label("", "running"), "running");
        assert_eq!(list_status_label("", ""), "—");
        assert_eq!(status_tone("cancelled"), "cancelled");
        assert_eq!(status_tone("complete"), "complete");
        assert!(is_blank_status("—"));
        assert!(!is_blank_status("complete"));
        assert!(is_terminal_status("complete"));
        assert!(is_terminal_status("cancelled"));
        assert!(!is_terminal_status("running"));
        assert!(!is_terminal_status("incomplete"));
    }

    #[test]
    fn sess_filter_matches_tui_session_chrome_kinds() {
        assert!(event_matches_kind("session", false, KindFilter::Sess));
        assert!(event_matches_kind("system", false, KindFilter::Sess));
        assert!(event_matches_kind("error", false, KindFilter::Sess));
        assert!(!event_matches_kind("plan", false, KindFilter::Sess));
        assert!(!event_matches_kind("subagent", false, KindFilter::Sess));
        assert!(event_matches_kind("subagent", false, KindFilter::Subagents));
        assert!(!event_matches_kind("agent", false, KindFilter::Subagents));
        assert!(event_matches_kind("task", false, KindFilter::Background));
        assert!(!event_matches_kind(
            "subagent",
            false,
            KindFilter::Background
        ));
        assert!(!event_matches_kind("agent", false, KindFilter::Sess));
        assert!(event_matches_kind("agent", false, KindFilter::Asst));
        assert!(event_matches_kind("thought", false, KindFilter::Asst));
        assert!(event_matches_filter(
            "tool",
            "workflow",
            false,
            KindFilter::Workflows
        ));
        assert!(!event_matches_filter(
            "tool",
            "read_file",
            false,
            KindFilter::Workflows
        ));
        assert!(!event_matches_kind("tool", false, KindFilter::Workflows));
    }

    #[test]
    fn short_created_strips_iso_fraction() {
        assert_eq!(
            short_created("2026-08-08T18:02:00.123Z"),
            "2026-08-08 18:02:00"
        );
        assert_eq!(short_created("  "), "");
        assert_eq!(short_created("already plain"), "already plain");
    }

    #[test]
    fn overview_fields_are_glance_not_chrome_counts() {
        use crate::wire::{SessionMeta, TurnRow, TurnsBlock};

        let meta = SessionMeta {
            session_id: "s1".into(),
            path: "/tmp/s1".into(),
            tool_call_count: 4,
            error_count: 1,
            git_repo: "groket".into(),
            git_branch: "hudv2".into(),
            created_at: "2026-08-08T12:00:00Z".into(),
            context_usage_compact: "12%".into(),
            context_tokens_used: Some(1_200),
            context_window_tokens: Some(10_000),
            num_events: 99,
            num_messages: 3,
            run_id: "run-a".into(),
            ..SessionMeta::default()
        };
        let turns = TurnsBlock {
            total: 2,
            turns: vec![
                TurnRow {
                    turn_index: 0,
                    label: "turn 0".into(),
                    first_index: Some(0),
                    last_index: Some(10),
                    ..TurnRow::default()
                },
                TurnRow {
                    turn_index: 1,
                    label: "turn 1".into(),
                    first_index: Some(11),
                    last_index: Some(20),
                    ..TurnRow::default()
                },
            ],
            ..TurnsBlock::default()
        };
        let rows = overview_fields(&meta, &turns);
        let keys: Vec<&str> = rows.iter().map(|r| r.key).collect();
        assert_eq!(
            keys,
            [
                "session",
                "tools",
                "last_turn",
                "messages",
                "run",
                "repo",
                "branch",
                "created",
                "path",
            ]
        );
        assert!(!keys.contains(&"events"));
        assert!(!keys.contains(&"context"));
        assert!(!keys.contains(&"findings"));
        assert!(!keys.contains(&"notes"));
        assert!(rows.iter().all(|r| r.copyable));
        assert_eq!(
            rows.iter()
                .find(|r| r.key == "last_turn")
                .map(|r| r.value.as_str()),
            Some("turn 1 · #11–#20")
        );
        assert_eq!(
            rows.iter()
                .find(|r| r.key == "tools")
                .map(|r| r.value.as_str()),
            Some("4 · 1 errors")
        );
        assert!(rows
            .iter()
            .find(|r| r.key == "path")
            .is_some_and(|r| r.copyable));
        assert!(rows
            .iter()
            .find(|r| r.key == "session")
            .is_some_and(|r| r.copyable));
        // Labels share display width budget with fixed-column view (longest here).
        let max_label = rows.iter().map(|r| r.label.len()).max().unwrap_or(0);
        assert!(
            max_label <= 12,
            "label too wide for overview kv gutter: {max_label}"
        );
    }

    #[test]
    fn human_event_type_label_spaces_underscores() {
        assert_eq!(
            human_event_type_label("user_message_chunk", "", "user", false),
            "user message chunk"
        );
        assert_eq!(
            human_event_type_label("tool_call", "tool call", "tool", false),
            "tool call"
        );
        assert_eq!(human_event_type_label("", "", "agent", false), "agent");
        assert_eq!(
            human_event_type_label("subagent_spawned", "", "subagent", false),
            "subagent spawned"
        );
        assert_eq!(
            human_event_type_label("subagent_finished", "subagent finished", "subagent", false),
            "subagent finished"
        );
        assert_eq!(
            human_event_type_label("task_backgrounded", "", "task", false),
            "background start"
        );
        assert_eq!(
            human_event_type_label("task_backgrounded", "", "task", true),
            "monitor"
        );
        assert_eq!(
            human_event_type_label("scheduled_task_created", "", "task", false),
            "schedule created"
        );
        assert_eq!(job_event_label("task_backgrounded", true), Some("monitor"));
        assert_eq!(timeline_count_caption(""), None);
        assert_eq!(
            timeline_count_caption("  1-40 of 100  "),
            Some("1-40 of 100")
        );
    }

    #[test]
    fn subagent_list_preview_is_not_the_dump() {
        let dump =
            "Subagent finished  01a016d1-4df7-7d30-b99f-65289aa0b417  completed  duration_ms=96555";
        let preview = subagent_list_preview("subagent_finished", &serde_json::json!({}), dump);
        assert_eq!(preview, "completed");
        assert!(!preview.contains("01a016d1"));
        assert_eq!(
            subagent_list_preview(
                "subagent_spawned",
                &serde_json::json!({}),
                "Spawned general-purpose: Investigate the bug"
            ),
            "Investigate the bug"
        );
    }

    #[test]
    fn overview_stat_rows_use_timeline_labels() {
        use crate::wire::TimelineEvent;

        let rows = overview_stat_rows(&[
            TimelineEvent {
                event_type: "tool_call".into(),
                tool_name: "read_file".into(),
                ..TimelineEvent::default()
            },
            TimelineEvent {
                event_type: "tool_call".into(),
                tool_name: "read_file".into(),
                ..TimelineEvent::default()
            },
            TimelineEvent {
                event_type: "task_backgrounded".into(),
                ..TimelineEvent::default()
            },
        ]);
        let labels: Vec<&str> = rows.iter().map(|r| r.label.as_str()).collect();
        assert!(labels.contains(&"tool call"));
        assert!(labels.contains(&"read file"));
        assert!(labels
            .iter()
            .any(|l| *l == "background start" || *l == "monitor"));
        assert!(!labels.iter().any(|l| l.contains('_')));
        let mut sorted = rows;
        sort_stat_rows(&mut sorted, 2, false);
        assert_eq!(sorted[0].label, "tool call");
        assert_eq!(sorted[0].value, "2");
    }

    #[test]
    fn overview_task_rows_are_named_not_prompt_dump() {
        use crate::wire::{BackgroundJobRow, ScheduleRow, WorkflowRow};

        let rows = overview_task_rows(
            &[BackgroundJobRow {
                id: "job-1".into(),
                kind: "monitor".into(),
                status: "done".into(),
                description: "Watch board".into(),
                event_index: Some(4),
                ..BackgroundJobRow::default()
            }],
            &[ScheduleRow {
                id: "sch-1".into(),
                human_schedule: "every 1 hour".into(),
                prompt_preview: "hourly ping".into(),
                ..ScheduleRow::default()
            }],
        );
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].kind, "monitor");
        assert_eq!(rows[0].label, "Watch board");
        let wfs = overview_workflow_rows(&[WorkflowRow {
            id: "wf_sprint8".into(),
            name: "sprint-8".into(),
            status: "complete".into(),
            event_index: Some(12),
            ..WorkflowRow::default()
        }]);
        assert_eq!(wfs.len(), 1);
        assert_eq!(wfs[0].kind, "workflow");
        assert_eq!(wfs[0].label, "sprint-8");
        assert_eq!(wfs[0].event_index, Some(12));
        assert_eq!(rows[1].kind, "schedule");
        assert_eq!(rows[1].label, "hourly ping");
    }

    #[test]
    fn overview_job_fields_are_counts_not_tails() {
        use crate::wire::{BackgroundJobRow, ScheduleRow};

        let jobs = [
            BackgroundJobRow {
                status: "running".into(),
                command: "cargo test".into(),
                output_path: "/tmp/monitor-call.log".into(),
                ..BackgroundJobRow::default()
            },
            BackgroundJobRow {
                status: "done".into(),
                ..BackgroundJobRow::default()
            },
        ];
        let rows = overview_job_fields(
            &jobs,
            &[ScheduleRow {
                human_schedule: "every 1 hour".into(),
                ..ScheduleRow::default()
            }],
            &[],
        );
        assert_eq!(rows[0].key, "background");
        assert_eq!(rows[0].value, "1 running · 1 complete");
        assert_eq!(rows[1].key, "schedules");
        assert_eq!(rows[1].value, "every 1 hour");
        assert!(!rows[0].value.contains("cargo"));
        assert!(!rows[0].value.contains("monitor-call"));
    }

    #[test]
    fn job_inspect_reads_command_and_caps_log() {
        let dir = std::env::temp_dir();
        let path = dir.join("groket-hud-job-inspect.log");
        let mut body = String::new();
        for i in 0..400 {
            body.push_str(&format!("line {i} xxxxxxxxxxxxxxxxxxxxxxxxx\n"));
        }
        body.push_str("DONE\n");
        std::fs::write(&path, &body).expect("log");
        let raw = serde_json::json!({
            "description": "Watch board",
            "command": "bash watch.sh",
            "output_file": path.to_string_lossy(),
        });
        assert_eq!(job_command(&raw, ""), "bash watch.sh");
        assert_eq!(job_description(&raw), "Watch board");
        let tail = job_log_tail(path.to_str().expect("utf8"));
        assert_eq!(job_status(&raw, "", &tail), "done");
        assert!(tail.contains("DONE"));
        assert!(tail.contains("line 399"));
        assert!(!tail.contains("line 0"));
        let sess = dir.join("groket-hud-job-sess");
        let term = sess.join("terminal");
        std::fs::create_dir_all(&term).expect("terminal");
        let host = term.join("call-inspect.log");
        std::fs::write(&host, body).expect("host log");
        let inspect = job_inspect_log(
            sess.to_str().expect("utf8"),
            "/root/.grok/sessions/x/terminal/call-inspect.log",
        );
        assert!(inspect.contains("line 0"));
        assert!(inspect.contains("DONE"));
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_dir_all(&sess);
    }

    #[test]
    fn job_list_preview_is_command_not_event_type() {
        let dump = "task_backgrounded  tool_call_id=call-1  command=cd /tmp && just lint  cwd=/tmp";
        let preview = job_list_preview("task_backgrounded", &serde_json::json!({}), dump);
        assert!(preview.starts_with("$ cd /tmp && just lint"));
        assert!(!preview.contains("task_backgrounded"));
        assert_eq!(
            job_list_preview(
                "task_backgrounded",
                &serde_json::json!({"command": "bash watch.sh"}),
                ""
            ),
            "$ bash watch.sh"
        );
    }

    #[test]
    fn overview_workflow_fields_are_counts_not_journal() {
        use crate::wire::WorkflowRow;

        let rows = overview_job_fields(
            &[],
            &[],
            &[
                WorkflowRow {
                    name: "sprint-9".into(),
                    status: "complete".into(),
                    pause_message: "should not appear".into(),
                    ..WorkflowRow::default()
                },
                WorkflowRow {
                    name: "sprint-8".into(),
                    status: "failed".into(),
                    pause_message: "Variable not found: vissue_root".into(),
                    ..WorkflowRow::default()
                },
            ],
        );
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].key, "workflows");
        assert_eq!(rows[0].value, "1 complete · 1 failed");
        assert!(!rows[0].value.contains("vissue_root"));
        assert!(!rows[0].value.contains("sprint"));
    }

    #[test]
    fn inspect_blocks_put_each_label_on_its_body() {
        let sched = schedule_inspect_blocks(
            "hourly ping",
            "every 1 hour",
            "2026-08-18T23:00:00Z",
            "",
            "",
        );
        assert_eq!(
            sched
                .iter()
                .map(|b| (b.label, b.body.as_str()))
                .collect::<Vec<_>>(),
            vec![
                ("Asked", "hourly ping"),
                ("Happened", "every 1 hour  ·  2026-08-18T23:00:00Z"),
            ]
        );
        let job = job_inspect_blocks("bash watch.sh", "failed  ·  exit 1", "FAILED");
        assert_eq!(job[0].label, "Asked");
        assert_eq!(job[0].body, "bash watch.sh");
        assert_eq!(job[1].label, "Happened");
        assert_eq!(job[1].body, "failed  ·  exit 1");
        assert_eq!(job[2].label, "Failed");
        assert_eq!(job[2].body, "FAILED");
        let sub = subagent_inspect_blocks("Investigate the bug", "coder  ·  failed", "failed");
        assert_eq!(sub[0].label, "Asked");
        assert_eq!(sub[0].body, "Investigate the bug");
        assert_eq!(sub[1].label, "Happened");
        assert!(sub[1].body.contains("failed"));
        assert_eq!(sub[2].label, "Failed");
        assert!(!sub[2].body.trim().is_empty());
        assert!(schedule_inspect_blocks("", "", "", "", "").is_empty());
        assert!(job_inspect_blocks("", "running", "").len() == 1);
        assert_eq!(job_inspect_blocks("", "running", "")[0].label, "Happened");
    }

    #[test]
    fn workflow_pairs_result_run_id_not_latest_name() {
        use crate::wire::WorkflowRow;

        let runs = [
            WorkflowRow {
                id: "wf_failed".into(),
                name: "sprint-8".into(),
                status: "failed".into(),
                phase: "Kickoff".into(),
                pause_message: "Variable not found: vissue_root".into(),
                ..WorkflowRow::default()
            },
            WorkflowRow {
                id: "wf_later".into(),
                name: "sprint-11".into(),
                status: "complete".into(),
                phase: "Retrospective".into(),
                ..WorkflowRow::default()
            },
        ];
        let raw = serde_json::json!({
            "script_path": "/repo/.grok/workflows/sprint.rhai",
            "run_id": "wf_failed"
        });
        let hit = workflow_for_event(&runs, &raw).expect("run");
        assert_eq!(hit.id, "wf_failed");
        assert_eq!(hit.status, "failed");
        assert!(hit.pause_message.contains("vissue_root"));
    }

    #[test]
    fn workflow_pairs_script_path_to_latest_named_run() {
        use crate::wire::WorkflowRow;

        let runs = [
            WorkflowRow {
                id: "wf_a".into(),
                name: "sprint".into(),
                status: "complete".into(),
                ..WorkflowRow::default()
            },
            WorkflowRow {
                id: "wf_b".into(),
                name: "sprint-11".into(),
                status: "complete".into(),
                phase: "Retrospective".into(),
                ..WorkflowRow::default()
            },
        ];
        let raw = serde_json::json!({
            "script_path": "/repo/.grok/workflows/sprint.rhai"
        });
        let hit = workflow_for_event(&runs, &raw).expect("run");
        assert_eq!(hit.name, "sprint-11");
        assert_eq!(workflow_name_from_raw(&raw), "sprint");
        assert_eq!(
            workflow_name_from_raw(&serde_json::json!({
                "script": "let meta = #{ name: \"between\", description: \"x\" };"
            })),
            "between"
        );
    }

    #[test]
    fn job_mate_index_pairs_start_and_finish_both_ways() {
        let start = serde_json::json!({"task_id": "job-x"});
        let finish = serde_json::json!({"task_id": "job-x", "exit_code": 1});
        let others_from_start = vec![(2, "task_completed".into(), finish.clone(), "call-x".into())];
        let others_from_finish = vec![(
            1,
            "task_backgrounded".into(),
            start.clone(),
            "call-x".into(),
        )];
        assert_eq!(
            job_mate_index("task_backgrounded", &start, "call-x", &others_from_start),
            Some(2)
        );
        assert_eq!(
            job_mate_index("task_completed", &finish, "call-x", &others_from_finish),
            Some(1)
        );
        assert_eq!(job_exit_code("task_completed", &finish, None), Some(1));
        assert_eq!(
            job_exit_code("task_backgrounded", &start, Some(&finish)),
            Some(1)
        );
        assert_eq!(job_exit_code("task_backgrounded", &start, None), None);
    }

    #[test]
    fn schedule_last_fire_comes_from_overview_row() {
        use crate::wire::ScheduleRow;

        let rows = [ScheduleRow {
            id: "sched-1".into(),
            last_fired_at: "2026-08-18T22:05:45Z".into(),
            last_subagent_id: "sub-1".into(),
            ..ScheduleRow::default()
        }];
        let (last, child) = schedule_last_fire(&rows, "sched-1").expect("row");
        assert_eq!(last, "2026-08-18T22:05:45Z");
        assert_eq!(child, "sub-1");
        assert!(schedule_last_fire(&rows, "").is_none());
        assert!(schedule_last_fire(&rows, "other").is_none());
    }

    #[test]
    fn event_is_monitor_from_kind_and_path() {
        assert!(event_is_monitor(&serde_json::json!({
            "kind": "monitor",
            "output_file": "/tmp/monitor-call.log"
        })));
        assert!(event_is_monitor(&serde_json::json!({
            "monitor_description": "watch board",
        })));
        assert!(!event_is_monitor(&serde_json::json!({
            "kind": "bash",
            "output_file": "/tmp/call-shell.log"
        })));
        assert!(event_is_monitor(&serde_json::json!({
            "description": "live watch of the board",
            "output_file": "/tmp/call-watch.log"
        })));
    }

    #[test]
    fn event_role_matches_tui_type_column() {
        assert_eq!(event_role("user", false), EventRole::User);
        assert_eq!(event_role("agent", false), EventRole::Model);
        assert_eq!(event_role("tool", false), EventRole::Model);
        assert_eq!(event_role("plan", false), EventRole::Model);
        assert_eq!(event_role("subagent", false), EventRole::Model);
        assert_eq!(event_role("thought", false), EventRole::ModelDim);
        assert_eq!(event_role("tool_result", false), EventRole::ModelDim);
        assert_eq!(event_role("session", false), EventRole::Session);
        assert_eq!(event_role("task", false), EventRole::Session);
        assert_eq!(event_role("system", false), EventRole::System);
        assert_eq!(event_role("agent", true), EventRole::Error);
        assert_eq!(event_role("error", false), EventRole::Error);
        assert_eq!(event_role("other", false), EventRole::Other);
    }

    #[test]
    fn event_type_style_keys_map_to_brand_roles() {
        let cream = [
            "user_message_chunk",
            "agent_message_chunk",
            "agent_thought_chunk",
            "plan",
            "subagent_spawned",
            "subagent_finished",
            "user",
            "assistant",
            "thought",
            "subagent",
        ];
        let complete = ["tool_call", "tool_call_update", "tool_result"];
        let running = [
            "task_backgrounded",
            "task_completed",
            "scheduled_task_created",
            "scheduled_task_updated",
            "scheduled_task_fired",
            "scheduled_task_deleted",
            "turn_completed",
            "current_mode_update",
            "retry_state",
            "goal_updated",
            "session_recap",
            "auto_compact_started",
            "auto_compact_completed",
            "compaction_checkpoint",
            "hook_execution",
            "hook_annotation",
            "turn_started",
            "turn_ended",
            "session",
        ];
        let failed = ["session_error", "error", "turn_error", "fatal_error"];
        let cancelled = ["system"];
        for k in cream {
            assert_eq!(event_type_brand_role(k), BrandRole::Cream, "{k}");
        }
        for k in complete {
            assert_eq!(event_type_brand_role(k), BrandRole::Complete, "{k}");
        }
        for k in running {
            assert_eq!(event_type_brand_role(k), BrandRole::Running, "{k}");
        }
        for k in failed {
            assert_eq!(event_type_brand_role(k), BrandRole::Failed, "{k}");
        }
        for k in cancelled {
            assert_eq!(event_type_brand_role(k), BrandRole::Cancelled, "{k}");
        }
        assert_eq!(
            event_brand_role("tool_call", "tool", false),
            BrandRole::Complete
        );
        assert_eq!(
            event_brand_role("agent_message_chunk", "agent", true),
            BrandRole::Failed
        );
    }

    #[test]
    fn tool_family_and_display_match_tui() {
        assert_eq!(tool_family("read_file"), "read");
        assert_eq!(tool_family("search_tool"), "read");
        assert_eq!(tool_family("search_replace"), "write");
        assert_eq!(tool_family("run_terminal_command"), "shell");
        assert_eq!(tool_family("spawn_subagent"), "agent");
        assert_eq!(tool_family("use_tool"), "mcp");
        assert_eq!(tool_family("tasks__list"), "mcp");
        assert_eq!(tool_family("mystery"), "other");
        assert_eq!(format_tool_display("read_file"), "read file");
        assert_eq!(
            format_tool_display("playwright__browser_navigate"),
            "playwright · browser navigate"
        );
        assert_eq!(list_event_detail("read file /tmp/x", "read_file"), "/tmp/x");
        assert_eq!(list_event_detail("read_file /tmp/x", "read_file"), "/tmp/x");
        assert_eq!(tool_brand_role("read_file", false), Some(BrandRole::Cream));
        assert_eq!(
            tool_brand_role("run_terminal_command", false),
            Some(BrandRole::Running)
        );
        assert_eq!(
            tool_brand_role("use_tool", false),
            Some(BrandRole::Cancelled)
        );
        assert_eq!(tool_brand_role("xyz", false), None);
        assert_eq!(tool_brand_role("read_file", true), Some(BrandRole::Failed));
    }

    #[test]
    fn extract_turn_and_event_are_paste_ready() {
        let turn = extract_turn("turn 3", "please fix it", "# Done\n\n**ok**");
        assert!(turn.contains("turn 3"));
        assert!(turn.contains("User\nplease fix it"));
        assert!(turn.contains("Assistant\n# Done"));
        assert!(turn.contains("**ok**"));
        let ev = crate::wire::TimelineEvent {
            index: 12,
            heading: "read_file".into(),
            kind: "tool".into(),
            tool_name: "read_file".into(),
            content: "1→fn main() {}\n".into(),
            raw_input: serde_json::json!({"target_file": "src/main.rs"}),
            turn_index: Some(1),
            ..crate::wire::TimelineEvent::default()
        };
        let got = extract_event(&ev);
        assert!(got.contains("#12 · turn 1 read_file"));
        assert!(got.contains("src/main.rs"));
        assert!(got.contains("fn main()"));
        assert!(!got.contains('→'));
    }

    #[test]
    fn assistant_markdown_cues_take_the_markdown_path() {
        let md = "# Heading\n\nSee **bold** and a fence:\n```\ncode\n```";
        assert_eq!(turn_assistant_paint(md), BodyPaint::Markdown);
        assert_eq!(body_paint("agent", md, true), BodyPaint::Markdown);
        assert_eq!(body_paint("agent", md, false), BodyPaint::Plain);
        assert_ne!(turn_assistant_paint(md), BodyPaint::Plain);
        // TUI always Markdown for chat rows (hard breaks), even without cues.
        assert_eq!(
            body_paint_for("agent", "agent_message_chunk", "plain sentence", true),
            BodyPaint::Markdown
        );
        assert_eq!(
            body_paint_for("user", "user_message_chunk", "hello\nworld", true),
            BodyPaint::Markdown
        );
        assert_eq!(
            body_paint("tool_result", "{\"a\":1}", true),
            BodyPaint::Json
        );
        // Python with # comments must not paint as Markdown.
        let py = "# header\nimport os\n\ndef main():\n    return 0\n";
        assert_eq!(body_paint("tool_result", py, true), BodyPaint::Code);
        assert_eq!(
            body_paint_for("tool", "tool_call_update", py, true),
            BodyPaint::Code
        );
        assert_eq!(syntax_for_path("src/app.py"), "py");
        assert_eq!(syntax_for_path("groket-hud/src/app.rs"), "rs");
        assert_eq!(syntax_for_tool_field("command", "", "echo hi"), "bash");
        assert_eq!(
            syntax_for_tool_field("old_string", "pkg/main.py", "x = 1"),
            "py"
        );
        assert_eq!(
            syntax_for_tool_output("read_file", "lib/x.rs", "fn main() {}"),
            "rs"
        );
        assert_eq!(
            syntax_for_tool_output("run_terminal_command", "", "ok\n"),
            "txt"
        );
        assert_eq!(
            body_paint_for("thought", "agent_thought_chunk", "hmm", true),
            BodyPaint::Plain
        );
    }

    fn synth_event(index: i64, content: &str) -> crate::wire::TimelineEvent {
        crate::wire::TimelineEvent {
            index,
            event_type: "agent_message_chunk".into(),
            kind: "agent".into(),
            content: content.into(),
            preview: content
                .lines()
                .next()
                .unwrap_or("")
                .chars()
                .take(200)
                .collect(),
            ..crate::wire::TimelineEvent::default()
        }
    }

    #[test]
    fn timeline_search_page_matches_full_fixture_prefix() {
        let mut events = Vec::with_capacity(8000);
        for i in 0..8000 {
            let content = if i % 19 == 0 {
                format!("row {i} carries needle-token in the body")
            } else {
                format!("ordinary row {i}")
            };
            events.push(synth_event(i, &content));
        }
        let full = timeline_search(&events, "needle-token");
        assert!(full.len() > 40, "expected many hits, got {}", full.len());
        assert!(full.len() < 8000);
        let page = full.iter().take(40).cloned().collect::<Vec<_>>();
        assert_eq!(
            page.iter().map(|h| h.index).collect::<Vec<_>>(),
            full.iter().take(40).map(|h| h.index).collect::<Vec<_>>()
        );
        for hit in &full {
            assert!(
                hit.field == "preview" || hit.field == "content",
                "{}",
                hit.field
            );
            assert!(
                hit.snippet.to_ascii_lowercase().contains("needle-token"),
                "{}",
                hit.snippet
            );
        }
        let first_page_only = timeline_search(&events[..40], "needle-token");
        assert!(
            first_page_only.len() < full.len(),
            "searching a raw first page must not be the complete set"
        );
    }

    #[test]
    fn timeline_open_card_uses_full_content_not_preview_line() {
        let preview = "first line only";
        let content = "first line only\nrest of the tool output\nand more";
        assert_eq!(
            timeline_body_text(preview, content, false, 80),
            "first line only"
        );
        assert_eq!(timeline_body_text(preview, content, true, 80), content);
        assert_eq!(timeline_body_text("", "abcdef", false, 3), "abc");
    }

    #[test]
    fn capped_display_and_json() {
        assert_eq!(capped_display("abcd", 10), "abcd");
        assert_eq!(capped_display("abcdef", 3), "abc…");
        assert_eq!(capped_display("x", 0), "");
        let v = serde_json::json!({"a": "bbbb"});
        let s = capped_json(&v, 8);
        assert!(s.chars().count() <= 9);
        assert!(s.ends_with('…') || s.len() <= 8);
    }

    #[test]
    fn origin_label_matches_tui() {
        assert_eq!(origin_label("host"), "Host");
        assert_eq!(origin_label("work"), "Eval");
        assert_eq!(origin_label("eval"), "Eval");
        assert_eq!(origin_label(""), "—");
    }

    #[test]
    fn looks_like_markdown_matches_tui_cues() {
        assert!(looks_like_markdown("# heading"));
        assert!(looks_like_markdown("- item\n- two"));
        assert!(looks_like_markdown("see **bold**"));
        assert!(looks_like_markdown("```\ncode\n```"));
        assert!(!looks_like_markdown("plain sentence"));
        assert!(!looks_like_markdown(""));
    }

    #[test]
    fn looks_like_json_and_pretty() {
        assert!(looks_like_json("{\"a\":1}"));
        assert!(looks_like_json(" [1, 2] "));
        assert!(!looks_like_json("not json"));
        let pretty = pretty_json("{\"a\":1}");
        assert!(pretty.contains('\n'));
        assert!(pretty.contains("\"a\""));
    }

    #[test]
    fn message_md_hard_breaks_preserves_lines() {
        assert_eq!(message_md_hard_breaks("a\nb"), "a  \nb");
    }

    #[test]
    fn message_markdown_source_keeps_numbered_lists() {
        let src = message_markdown_source("Intro\n\n1. first\n2. second\n\n**bold**");
        assert!(src.contains("1. first"));
        assert!(src.contains("2. second"));
        assert!(src.contains("**bold**"));
        assert!(src.contains("  \n") || src.contains("Intro"));
    }

    #[test]
    fn strip_inline_line_prefixes_drops_grok_arrows() {
        let raw = "1→from pathlib import Path\n10→x = 1\n";
        let out = strip_inline_line_prefixes(raw);
        assert!(!out.contains('→'));
        assert!(out.starts_with("from pathlib"));
        assert!(out.contains("x = 1"));
        assert_eq!(strip_inline_line_prefixes("plain"), "plain");
    }

    #[test]
    fn display_tool_output_strips_read_file() {
        let raw = "1→fn main() {}\n2→// hi\n";
        let out = display_tool_output(raw, "read_file");
        assert_eq!(out, "fn main() {}\n// hi\n");
        assert!(looks_like_numbered_file(raw));
    }

    #[test]
    fn image_result_path_reads_json() {
        let body = r#"{"path":"/tmp/img.jpg","filename":"img.jpg","message":"saved"}"#;
        assert_eq!(image_result_path(body), "/tmp/img.jpg");
        assert_eq!(image_result_path("not json"), "");
    }

    #[test]
    fn tool_fields_search_replace_and_command_not_one_json_bag() {
        let raw = serde_json::json!({
            "file_path": "a.py",
            "old_string": "aaa",
            "new_string": "bbb extra long would still be a field",
        });
        let fields = tool_fields_from_raw("search_replace", &raw, 8_000);
        let ids: Vec<&str> = fields.iter().map(|f| f.id.as_str()).collect();
        assert_eq!(ids, ["file_path", "old_string", "new_string"]);
        assert_eq!(fields[1].value, "aaa");
        let cmd = tool_fields_from_raw(
            "run_terminal_command",
            &serde_json::json!({"command": "ls -la", "timeout": 30}),
            100,
        );
        assert_eq!(cmd[0].id, "command");
        assert_eq!(cmd[0].value, "ls -la");
        assert!(cmd.iter().all(|f| f.id != "json"));
        let ws = tool_fields_from_raw(
            "web_search",
            &serde_json::json!({
                "variant": "WebSearch",
                "backend": true,
                "query": "xAI logo",
                "url": "https://x.ai/"
            }),
            200,
        );
        let ids: Vec<&str> = ws.iter().map(|f| f.id.as_str()).collect();
        assert_eq!(ids, ["query", "url"]);
        assert_eq!(ws[0].value, "xAI logo");
    }

    #[test]
    fn sanitize_strips_csi_and_cr() {
        let s = sanitize_console_text("ok\x1b[31mred\x1b[0m\r\nnext");
        assert!(!s.contains('\u{1b}'));
        assert!(s.contains("ok"));
        assert!(s.contains("red"));
        assert!(s.contains("next"));
    }
}
