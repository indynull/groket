//! Linux input / focus helpers for the palette overlay.
//!
//! **X11 (no Wayland):** raise, focus, and grab the keyboard so typing reaches
//! an override-redirect palette.
//!
//! **Wayland (Sway, etc.):** the compositor owns focus. We never grab via
//! Xwayland (a Wayland surface id is not a valid X11 window and retries stack).
//! Summon uses tray / compositor binds; keyboard focus is
//! ``xdg_activation_v1.activate`` on the iced surface when a token is present.
//!
//! The in-process global hotkey crate is X11-only on Linux — same gate as grab.

use x11rb::connection::Connection;
use x11rb::protocol::xproto::{
    ConfigureWindowAux, ConnectionExt, GrabMode, GrabStatus, InputFocus, StackMode,
};

fn window_id(xid: u64) -> Option<u32> {
    let win = u32::try_from(xid).unwrap_or(0);
    (win != 0).then_some(win)
}

fn nonempty(value: Option<&str>) -> bool {
    value.is_some_and(|s| !s.is_empty())
}

/// Native Wayland session (``WAYLAND_DISPLAY`` set), including under Xwayland.
pub fn wayland_session_from_env(wayland_display: Option<&str>) -> bool {
    nonempty(wayland_display)
}

/// True only on a real X11 session. A Wayland compositor owns focus;
/// grabbing via Xwayland with a Wayland surface id fails and the retry
/// queue stacks on every click.
pub fn x11_grab_applies(wayland_display: Option<&str>, x11_display: Option<&str>) -> bool {
    !nonempty(wayland_display) && nonempty(x11_display)
}

/// Whether this process should use the X11 keyboard grab.
pub fn x11_grab_needed() -> bool {
    x11_grab_applies(
        std::env::var("WAYLAND_DISPLAY").ok().as_deref(),
        std::env::var("DISPLAY").ok().as_deref(),
    )
}

/// Whether the in-process global-hotkey crate can see keys on this host.
///
/// Linux: X11 only (same condition as the keyboard grab). macOS / Windows:
/// always (caller still handles register errors).
pub fn global_hotkey_supported() -> bool {
    #[cfg(target_os = "linux")]
    {
        x11_grab_needed()
    }
    #[cfg(not(target_os = "linux"))]
    {
        true
    }
}

/// One-line operator hint when in-process summon is unavailable on Linux.
pub fn wayland_summon_hint(chord_label: &str) -> String {
    format!(
        "Wayland session — in-process global hotkey is X11-only \
         (skipped {chord_label}). Summon with tray Show HUD, \
         `groket hud --toggle`, or a compositor bind \
         (bindsym $mod+Shift+g exec groket hud --toggle). Float: \
         include ~/.config/groket/sway-hud.conf \
         (app_id {overlay})",
        overlay = crate::install_desktop::OVERLAY_APP_ID,
    )
}

/// Raise, focus, and grab the keyboard so typing goes to the palette.
///
/// Returns false when the window is not viewable yet (caller should retry).
/// Returns true without contacting X11 on a Wayland session.
pub fn focus_window(xid: u64) -> bool {
    if !x11_grab_needed() {
        return true;
    }
    let Some(win) = window_id(xid) else {
        return false;
    };
    let Ok((conn, _screen)) = x11rb::connect(None) else {
        return false;
    };
    let _ = conn.configure_window(win, &ConfigureWindowAux::new().stack_mode(StackMode::ABOVE));
    let _ = conn.set_input_focus(InputFocus::PARENT, win, x11rb::CURRENT_TIME);
    let Ok(cookie) = conn.grab_keyboard(
        false,
        win,
        x11rb::CURRENT_TIME,
        GrabMode::ASYNC,
        GrabMode::ASYNC,
    ) else {
        let _ = conn.flush();
        return false;
    };
    let ok = match cookie.reply() {
        Ok(reply) => {
            reply.status == GrabStatus::SUCCESS || reply.status == GrabStatus::ALREADY_GRABBED
        }
        Err(_) => false,
    };
    let _ = conn.flush();
    ok
}

/// Drop the palette keyboard grab (hide / window mode).
pub fn release_keyboard() {
    if !x11_grab_needed() {
        return;
    }
    let Ok((conn, _screen)) = x11rb::connect(None) else {
        return;
    };
    let _ = conn.ungrab_keyboard(x11rb::CURRENT_TIME);
    let _ = conn.flush();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wayland_session_skips_x11_grab() {
        assert!(!x11_grab_applies(Some("wayland-1"), Some(":0")));
        assert!(!x11_grab_applies(Some("wayland-0"), None));
        assert!(x11_grab_applies(None, Some(":0")));
        assert!(x11_grab_applies(Some(""), Some(":1")));
        assert!(!x11_grab_applies(None, None));
        assert!(!x11_grab_applies(None, Some("")));
    }

    #[test]
    fn wayland_env_detects_nonempty_display() {
        assert!(wayland_session_from_env(Some("wayland-1")));
        assert!(!wayland_session_from_env(None));
        assert!(!wayland_session_from_env(Some("")));
    }

    #[test]
    fn summon_hint_names_app_id() {
        let h = wayland_summon_hint("Ctrl+Shift+G");
        assert!(h.contains(crate::install_desktop::OVERLAY_APP_ID));
        assert!(h.contains("Ctrl+Shift+G"));
        assert!(h.contains("sway-hud.conf"));
        assert!(h.contains("groket hud --toggle"));
    }

    #[test]
    fn focus_window_is_noop_success_without_x11_grab() {
        if x11_grab_needed() {
            return;
        }
        assert!(focus_window(0));
        assert!(focus_window(42));
    }
}
