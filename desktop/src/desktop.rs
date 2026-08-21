//! Desktop notifications for session transitions.
//!
//! Linux uses the freedesktop Notifications bus (dunst, mako, fnott, swaync,
//! notification-daemon). macOS uses Notification Center. Windows uses toasts.

use std::collections::HashMap;
use std::thread;

use crate::brand::notify_icon_png;
use crate::format::list_status_label;

pub const APP_NAME: &str = "groket";
pub const ENV_NAME: &str = "GROKET_HUD_NOTIFY";

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

/// Seen-map key so host and work copies of the same id do not fight.
pub fn notice_row_key(origin: &str, sid: &str) -> String {
    if origin.is_empty() {
        sid.to_string()
    } else {
        format!("{origin}:{sid}")
    }
}

/// Host Grok already posts turn / session bubbles; groket must not repeat them.
fn is_host_notice_key(key: &str) -> bool {
    key.split_once(':')
        .is_some_and(|(origin, _)| origin == "host")
}

/// Record catalog rows. When *seed* is true, remember statuses without posting.
///
/// Hydrate flicker (complete → running → complete) is stopped in
/// ``merge_catalog_rows``. ``ending`` → ``complete`` stays silent (Done
/// already acknowledged). ``running`` → ``complete`` still posts.
pub fn notices_from_rows(
    seen: &mut HashMap<String, String>,
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
            }
            Observe::Same => {}
            Observe::Changed { from, to } => {
                let skip = seed
                    || is_host_notice_key(sid)
                    || crate::format::is_blank_status(&from)
                    || (normalize(&from) == "ending" && normalize(&to) == "complete");
                seen.insert(sid.clone(), to.clone());
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
    #[cfg(target_os = "macos")]
    {
        send_macos(notice)
    }
    #[cfg(not(target_os = "macos"))]
    {
        send_other(notice)
    }
}

/// macOS: ``notify-rust`` ``icon()`` is a no-op. The left face is
/// ``_identityImage`` (``app_icon``). ``set_application`` claims our bundle
/// when ``groket.app`` is registered so Notification Center does not
/// fall back to Finder.
#[cfg(target_os = "macos")]
fn send_macos(notice: &DesktopNotice) -> Result<(), String> {
    let _ = notify_rust::set_application(crate::install_desktop::APP_ID);
    let icon = icon_file();
    let mut n = mac_notification_sys::Notification::new();
    n.title(&notice.summary).message(&notice.body);
    if let Some(ref path) = icon {
        n.app_icon(path);
    }
    n.send().map(|_| ()).map_err(|e| e.to_string())
}

#[cfg(not(target_os = "macos"))]
fn send_other(notice: &DesktopNotice) -> Result<(), String> {
    let mut n = notify_rust::Notification::new();
    n.appname(APP_NAME)
        .summary(&notice.summary)
        .body(&notice.body);
    if let Some(path) = icon_file() {
        n.icon(&path);
        // Windows toasts use this image; Linux ``icon()`` is the small slot.
        #[cfg(target_os = "windows")]
        {
            n.image_path(&path);
        }
    }
    n.urgency(match notice.urgency {
        UrgencyKind::Low => notify_rust::Urgency::Low,
        UrgencyKind::Normal => notify_rust::Urgency::Normal,
        UrgencyKind::Critical => notify_rust::Urgency::Critical,
    });
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
        // Rewrite when the packaged asset changes (tray tile or app icon).
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
        let rows = vec![("abc".into(), "Demo".into(), "running".into())];
        assert!(notices_from_rows(&mut seen, &rows, false).is_empty());
        assert_eq!(seen.get("abc").map(String::as_str), Some("running"));
    }

    #[test]
    fn awaiting_transition_notifies() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let rows = vec![("abc".into(), "Demo".into(), "awaiting".into())];
        let notes = notices_from_rows(&mut seen, &rows, false);
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].summary, "Awaiting a reply");
        assert!(notes[0].body.contains("Demo"));
    }

    #[test]
    fn seed_pass_swallows_transitions() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &rows, true).is_empty());
        assert_eq!(seen.get("abc").map(String::as_str), Some("complete"));
    }

    #[test]
    fn blank_placeholder_to_complete_is_silent() {
        let mut seen = HashMap::from([("abc".into(), "—".into())]);
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &rows, false).is_empty());
        assert_eq!(seen.get("abc").map(String::as_str), Some("complete"));
    }

    #[test]
    fn host_and_work_same_id_do_not_refire_complete() {
        let work = notice_row_key("work", "s1");
        let host = notice_row_key("host", "s1");
        let mut seen = HashMap::new();
        let rows = vec![
            (work.clone(), "Feedback Analysis".into(), "complete".into()),
            (host.clone(), "Feedback Analysis".into(), "running".into()),
        ];
        assert!(notices_from_rows(&mut seen, &rows, false).is_empty());
        assert!(notices_from_rows(&mut seen, &rows, false).is_empty());
        assert_eq!(seen.get(&work).map(String::as_str), Some("complete"));
        assert_eq!(seen.get(&host).map(String::as_str), Some("running"));
    }

    #[test]
    fn running_to_complete_notifies() {
        let mut seen = HashMap::from([("abc".into(), "running".into())]);
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        let notes = notices_from_rows(&mut seen, &rows, false);
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].summary, "Session complete");
    }

    #[test]
    fn host_running_to_complete_is_silent() {
        let key = notice_row_key("host", "abc");
        let mut seen = HashMap::from([(key.clone(), "running".into())]);
        let rows = vec![(key, "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &rows, false).is_empty());
    }

    #[test]
    fn host_running_to_awaiting_is_silent() {
        let key = notice_row_key("host", "abc");
        let mut seen = HashMap::from([(key.clone(), "running".into())]);
        let rows = vec![(key, "Demo".into(), "awaiting".into())];
        assert!(notices_from_rows(&mut seen, &rows, false).is_empty());
    }

    #[test]
    fn eval_running_to_complete_still_notifies() {
        let key = notice_row_key("work", "abc");
        let mut seen = HashMap::from([(key.clone(), "running".into())]);
        let rows = vec![(key, "Demo".into(), "complete".into())];
        let notes = notices_from_rows(&mut seen, &rows, false);
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].summary, "Session complete");
    }

    #[test]
    fn ending_to_complete_is_silent() {
        let mut seen = HashMap::from([("abc".into(), "ending".into())]);
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        assert!(notices_from_rows(&mut seen, &rows, false).is_empty());
    }

    #[test]
    fn awaiting_to_complete_notifies() {
        let mut seen = HashMap::from([("abc".into(), "awaiting".into())]);
        let rows = vec![("abc".into(), "Demo".into(), "complete".into())];
        let notes = notices_from_rows(&mut seen, &rows, false);
        assert_eq!(notes.len(), 1);
        assert_eq!(notes[0].summary, "Session complete");
    }

    #[test]
    fn running_is_silent() {
        assert!(session_notice("t", "s", "pending", "running").is_none());
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
    fn notify_png_matches_host_slot() {
        #[cfg(target_os = "linux")]
        assert_eq!(notify_icon_png(), crate::brand::TRAY_64_PNG);
        #[cfg(not(target_os = "linux"))]
        assert_eq!(notify_icon_png(), crate::brand::APP_ICON_256_PNG);
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
