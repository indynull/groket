//! Desktop notifications for session and analysis transitions.
//!
//! Linux uses the freedesktop Notifications bus (dunst, mako, fnott, swaync,
//! notification-daemon). macOS uses Notification Center. Windows uses toasts.

use std::collections::{HashMap, HashSet};
use std::thread;

use serde_json::Value;

use crate::format::list_status_label;

pub const APP_NAME: &str = "Groket HUD";
pub const ENV_NAME: &str = "GROKET_HUD_NOTIFY";

/// Three-bar favicon (same family as window / tray).
fn notify_icon_png() -> &'static [u8] {
    crate::brand::tray_icon_png()
}

/// Urgency the host daemon maps to its own levels.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UrgencyKind {
    Low,
    Normal,
    Critical,
}

/// One bubble to post to the host notification daemon.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DesktopNotice {
    pub summary: String,
    pub body: String,
    pub urgency: UrgencyKind,
}

/// How a status observation relates to the last value for that session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Observe {
    First,
    Same,
    Changed { from: String, to: String },
}

/// Env override: ``1``/``true``/``yes`` on, ``0``/``false``/``no`` off.
pub fn env_flag(value: Option<&str>) -> Option<bool> {
    let v = value.map(str::trim).filter(|s| !s.is_empty())?;
    match v.to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" => Some(true),
        "0" | "false" | "no" => Some(false),
        _ => None,
    }
}

/// True when desktop notifications should be posted.
pub fn notifications_enabled() -> bool {
    if let Some(flag) = env_flag(std::env::var(ENV_NAME).ok().as_deref()) {
        return flag;
    }
    crate::prefs::desktop_notifications()
}

pub fn observe_status(prev: Option<&str>, new: &str) -> Observe {
    let to = normalize(new);
    match prev {
        None => Observe::First,
        Some(from) if normalize(from) == to => Observe::Same,
        Some(from) => Observe::Changed {
            from: normalize(from),
            to,
        },
    }
}

/// Notice for a session status transition. First sightings produce none.
pub fn session_notice(title: &str, sid: &str, from: &str, to: &str) -> Option<DesktopNotice> {
    let kind = notice_kind(to)?;
    if normalize(from) == normalize(to) {
        return None;
    }
    let label = display_name(title, sid);
    Some(match kind {
        "awaiting" => DesktopNotice {
            summary: "Awaiting a reply".into(),
            body: format!("{label} is waiting for follow-up or Done"),
            urgency: UrgencyKind::Normal,
        },
        "complete" => DesktopNotice {
            summary: "Session complete".into(),
            body: label,
            urgency: UrgencyKind::Low,
        },
        "cancelled" => DesktopNotice {
            summary: "Session cancelled".into(),
            body: label,
            urgency: UrgencyKind::Normal,
        },
        "error" => DesktopNotice {
            summary: "Session failed".into(),
            body: label,
            urgency: UrgencyKind::Critical,
        },
        _ => return None,
    })
}

/// Notice for an analysis job that left the running state.
pub fn analysis_notice(
    title: &str,
    sid: &str,
    state: &str,
    finding_count: i64,
    error: &str,
) -> Option<DesktopNotice> {
    let label = display_name(title, sid);
    let st = normalize(state);
    match st.as_str() {
        "done" | "complete" => Some(DesktopNotice {
            summary: "Analysis finished".into(),
            body: if finding_count > 0 {
                format!("{label} · {finding_count} findings")
            } else {
                format!("{label} · no findings")
            },
            urgency: UrgencyKind::Low,
        }),
        "error" | "failed" => Some(DesktopNotice {
            summary: "Analysis failed".into(),
            body: if error.is_empty() {
                label
            } else {
                format!("{label} · {error}")
            },
            urgency: UrgencyKind::Critical,
        }),
        _ => None,
    }
}

/// Decode an ``analysis/changed`` payload.
pub fn analysis_from_params(params: &Value, title: &str) -> Option<DesktopNotice> {
    let sid = params
        .get("sessionId")
        .and_then(Value::as_str)
        .unwrap_or("");
    if sid.is_empty() {
        return None;
    }
    let state = params.get("state").and_then(Value::as_str).unwrap_or("");
    let findings = params
        .get("findingCount")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let error = params.get("error").and_then(Value::as_str).unwrap_or("");
    analysis_notice(title, sid, state, findings, error)
}

/// Notice for an analysis transition, once per job (or session) and state.
///
/// A second ``analysis/changed`` with the same ``jobId`` and terminal state
/// is ignored. A later job on the same session posts again.
pub fn take_analysis_notice(
    seen: &mut HashMap<String, String>,
    params: &Value,
    title: &str,
) -> Option<DesktopNotice> {
    let notice = analysis_from_params(params, title)?;
    let id = analysis_seen_id(params)?;
    let state = analysis_terminal_state(params)?;
    if seen.get(&id).map(String::as_str) == Some(state) {
        return None;
    }
    seen.insert(id, state.to_string());
    Some(notice)
}

fn analysis_seen_id(params: &Value) -> Option<String> {
    let job = params
        .get("jobId")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty());
    if let Some(job) = job {
        return Some(job.to_string());
    }
    let sid = params
        .get("sessionId")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())?;
    Some(sid.to_string())
}

fn analysis_terminal_state(params: &Value) -> Option<&'static str> {
    let raw = params.get("state").and_then(Value::as_str).unwrap_or("");
    match normalize(raw).as_str() {
        "done" | "complete" => Some("done"),
        "error" | "failed" => Some("error"),
        _ => None,
    }
}

/// Record catalog rows. When *seed* is true, remember statuses without posting.
///
/// ``stable`` holds ids whose last status was seen on two consecutive
/// catalogs. A one-shot ``running``/``ending`` → ``complete`` is hydrate
/// flicker and stays silent; ``awaiting`` → ``complete`` still posts.
pub fn notices_from_rows(
    seen: &mut HashMap<String, String>,
    stable: &mut HashSet<String>,
    rows: &[(String, String, String)],
    seed: bool,
) -> Vec<DesktopNotice> {
    let mut out = Vec::new();
    for (sid, title, status) in rows {
        if sid.is_empty() {
            continue;
        }
        let to = list_status_label(status, "");
        match observe_status(seen.get(sid).map(String::as_str), &to) {
            Observe::First => {
                seen.insert(sid.clone(), normalize(&to));
                stable.remove(sid);
            }
            Observe::Same => {
                stable.insert(sid.clone());
            }
            Observe::Changed { from, to } => {
                let skip = seed
                    || crate::format::is_blank_status(&from)
                    || settle_complete_is_silent(&from, &to, stable.contains(sid));
                seen.insert(sid.clone(), to.clone());
                stable.remove(sid);
                if skip {
                    continue;
                }
                if let Some(n) = session_notice(title, sid, &from, &to) {
                    out.push(n);
                }
            }
        }
    }
    out
}

fn settle_complete_is_silent(from: &str, to: &str, was_stable: bool) -> bool {
    if normalize(to) != "complete" {
        return false;
    }
    match normalize(from).as_str() {
        "ending" => true,
        "running" | "pending" | "in_progress" => !was_stable,
        _ => false,
    }
}

/// Post on a worker thread. Host failure is ignored (no daemon is fine).
pub fn post(notice: DesktopNotice) {
    if !notifications_enabled() {
        return;
    }
    let _ = thread::Builder::new()
        .name("groket-notify".into())
        .spawn(move || {
            if let Err(err) = send_blocking(&notice) {
                crate::log::error(&format!("desktop notify: {err}"));
            }
        });
}

fn send_blocking(notice: &DesktopNotice) -> Result<(), String> {
    let mut n = notify_rust::Notification::new();
    n.appname(APP_NAME)
        .summary(&notice.summary)
        .body(&notice.body);
    if let Some(path) = icon_file() {
        n.icon(&path);
    }
    // macOS notify-rust only exposes urgency with the preview-macos-un feature.
    #[cfg(not(target_os = "macos"))]
    {
        n.urgency(match notice.urgency {
            UrgencyKind::Low => notify_rust::Urgency::Low,
            UrgencyKind::Normal => notify_rust::Urgency::Normal,
            UrgencyKind::Critical => notify_rust::Urgency::Critical,
        });
    }
    #[cfg(target_os = "macos")]
    {
        let _ = notice.urgency;
    }
    n.show().map(|_| ()).map_err(|e| e.to_string())
}

fn icon_file() -> Option<String> {
    let home = std::env::var_os("HOME").or_else(|| std::env::var_os("USERPROFILE"))?;
    let path = std::path::PathBuf::from(home)
        .join(".groket")
        .join("hud-notify.png");
    match ensure_icon_file(&path) {
        Ok(()) => Some(path.to_string_lossy().into_owned()),
        Err(err) => {
            crate::log::error(&format!("desktop notify icon: {err}"));
            None
        }
    }
}

fn ensure_icon_file(path: &std::path::Path) -> std::io::Result<()> {
    if path.is_file() {
        // Rewrite when the packaged asset changes (favicon → cream dock tile).
        if let Ok(existing) = std::fs::read(path) {
            if existing.as_slice() == notify_icon_png() {
                return Ok(());
            }
        }
    }
    let parent = path.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "notify icon path has no parent",
        )
    })?;
    std::fs::create_dir_all(parent)?;
    std::fs::write(path, notify_icon_png())
}

fn display_name(title: &str, sid: &str) -> String {
    let t = title.trim();
    if !t.is_empty() {
        return t.to_string();
    }
    let s = sid.trim();
    if s.len() > 12 {
        format!("{}…", &s[..12])
    } else {
        s.to_string()
    }
}

fn normalize(status: &str) -> String {
    let s = list_status_label(status, "")
        .trim()
        .to_ascii_lowercase()
        .replace(char::is_whitespace, "_");
    if s.contains("await") {
        return "awaiting".into();
    }
    if s.contains("fail") || s == "error" || s.contains("timeout") {
        return "error".into();
    }
    if s.contains("cancel") || s.contains("interrupt") || s.contains("abort") {
        return "cancelled".into();
    }
    if s.contains("complete") || s == "ok" || s == "success" {
        return "complete".into();
    }
    s
}

fn notice_kind(status: &str) -> Option<&'static str> {
    match normalize(status).as_str() {
        "awaiting" => Some("awaiting"),
        "complete" => Some("complete"),
        "cancelled" => Some("cancelled"),
        "error" => Some("error"),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn env_flag_parses() {
        assert_eq!(env_flag(Some("1")), Some(true));
        assert_eq!(env_flag(Some("NO")), Some(false));
        assert_eq!(env_flag(None), None);
        assert_eq!(env_flag(Some("")), None);
    }

    #[test]
    fn first_sighting_is_silent() {
        let mut seen = HashMap::new();
        let mut stable = HashSet::new();
        let rows = vec![("abc".into(), "Demo".into(), "running".into())];
        assert!(notices_from_rows(&mut seen, &mut stable, &rows, false).is_empty());
        assert_eq!(seen.get("abc").map(String::as_str), Some("running"));
    }

    #[test]
    fn awaiting_transition_notifies() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let mut stable = HashSet::new();
        let rows = vec![("abc".into(), "Demo".into(), "awaiting".into())];
        let notes = notices_from_rows(&mut seen, &mut stable, &rows, false);
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].summary, "Awaiting a reply");
        assert!(notes[0].body.contains("Demo"));
    }

    #[test]
    fn seed_pass_swallows_transitions() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let mut stable = HashSet::new();
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &mut stable, &rows, true).is_empty());
        assert_eq!(seen.get("abc").map(String::as_str), Some("complete"));
    }

    #[test]
    fn blank_placeholder_to_complete_is_silent() {
        let mut seen = HashMap::from([("abc".into(), "—".into())]);
        let mut stable = HashSet::new();
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &mut stable, &rows, false).is_empty());
        assert_eq!(seen.get("abc").map(String::as_str), Some("complete"));
    }

    #[test]
    fn one_shot_running_to_complete_is_silent() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let mut stable = HashSet::new();
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &mut stable, &rows, false).is_empty());
    }

    #[test]
    fn stable_running_to_complete_notifies() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let mut stable = HashSet::from(["abc".into()]);
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        let notes = notices_from_rows(&mut seen, &mut stable, &rows, false);
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].summary, "Session complete");
    }

    #[test]
    fn ending_to_complete_is_silent() {
        let mut seen = HashMap::from([("abc".into(), "ending".into())]);
        let mut stable = HashSet::from(["abc".into()]);
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &mut stable, &rows, false).is_empty());
    }

    #[test]
    fn awaiting_to_complete_notifies() {
        let mut seen = HashMap::from([("abc".into(), "awaiting".into())]);
        let mut stable = HashSet::new();
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        let notes = notices_from_rows(&mut seen, &mut stable, &rows, false);
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].summary, "Session complete");
    }

    #[test]
    fn running_is_silent() {
        assert!(session_notice("t", "s", "pending", "running").is_none());
    }

    #[test]
    fn analysis_done_and_error() {
        let done = analysis_notice("Pack", "sid", "done", 3, "").unwrap();
        assert_eq!(done.summary, "Analysis finished");
        assert!(done.body.contains("3 findings"));
        let err = analysis_notice("Pack", "sid", "error", 0, "boom").unwrap();
        assert_eq!(err.summary, "Analysis failed");
        assert!(err.body.contains("boom"));
        assert!(analysis_notice("Pack", "sid", "running", 0, "").is_none());
    }

    #[test]
    fn analysis_params_need_session() {
        assert!(analysis_from_params(&serde_json::json!({"state": "done"}), "T").is_none());
        let n = analysis_from_params(
            &serde_json::json!({
                "sessionId": "s1",
                "state": "done",
                "findingCount": 1
            }),
            "T",
        )
        .unwrap();
        assert_eq!(n.summary, "Analysis finished");
    }

    #[test]
    fn analysis_changed_posts_once_per_job_state() {
        let mut seen = HashMap::new();
        let first = serde_json::json!({
            "sessionId": "s1",
            "jobId": "job-a",
            "state": "done",
            "findingCount": 1
        });
        assert!(take_analysis_notice(&mut seen, &first, "T").is_some());
        assert!(take_analysis_notice(&mut seen, &first, "T").is_none());
        let again = serde_json::json!({
            "sessionId": "s1",
            "jobId": "job-b",
            "state": "done",
            "findingCount": 0
        });
        assert!(take_analysis_notice(&mut seen, &again, "T").is_some());
    }

    #[test]
    fn analysis_changed_without_job_dedupes_on_session_state() {
        let mut seen = HashMap::new();
        let done = serde_json::json!({"sessionId": "s1", "state": "done"});
        assert!(take_analysis_notice(&mut seen, &done, "T").is_some());
        assert!(take_analysis_notice(&mut seen, &done, "T").is_none());
        let err = serde_json::json!({"sessionId": "s1", "state": "error", "error": "boom"});
        assert!(take_analysis_notice(&mut seen, &err, "T").is_some());
    }

    #[test]
    fn ensure_icon_writes_then_skips() {
        let dir =
            std::env::temp_dir().join(format!("groket-hud-notify-icon-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("hud-notify.png");
        ensure_icon_file(&path).unwrap();
        assert!(path.is_file());
        let first_len = std::fs::metadata(&path).unwrap().len();
        ensure_icon_file(&path).unwrap();
        assert_eq!(std::fs::metadata(&path).unwrap().len(), first_len);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn ensure_icon_rewrites_stale_bytes() {
        let dir = std::env::temp_dir().join(format!(
            "groket-hud-notify-icon-stale-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("hud-notify.png");
        std::fs::write(&path, b"not-the-packaged-tile").unwrap();
        ensure_icon_file(&path).unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), notify_icon_png());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn ensure_icon_fails_when_parent_is_a_file() {
        let dir =
            std::env::temp_dir().join(format!("groket-hud-notify-icon-bad-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let blocker = dir.join("not-a-dir");
        std::fs::write(&blocker, b"x").unwrap();
        let path = blocker.join("hud-notify.png");
        assert!(ensure_icon_file(&path).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
