//! HUD motion roles: timings, easing, and interruptible page/overlay clocks.

use std::time::{Duration, Instant};

use iced::Animation;

use crate::model::Tab;

/// Palette present (hotkey show).
pub const PRESENT_MS: u64 = 220;
/// Palette dismiss (Esc hide). Shorter than present.
pub const DISMISS_MS: u64 = 180;
/// Sibling tab / turn-scope fade.
pub const SIBLING_MS: u64 = 180;
/// Hierarchical enter (session pick, open event).
pub const PUSH_MS: u64 = 240;
/// Hierarchical leave (event close, back to session list).
pub const POP_MS: u64 = 200;
/// Next / previous event cover.
pub const STEP_MS: u64 = 180;

/// What the operator is doing. Each job has one duration, ease, and slide rule.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MotionRole {
    Present,
    Dismiss,
    Sibling,
    Push,
    Pop,
    Step,
    Disclose,
    None,
}

/// Which body layer a page job paints.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PageLayer {
    /// Picker ↔ browse (search and footer stay).
    Browse,
    /// Tab / event body (tabs stay).
    Pane,
}

impl MotionRole {
    /// Full-motion length, or zero when reduced motion is on.
    pub fn duration(self, reduced: bool) -> Duration {
        if reduced || matches!(self, Self::None) {
            return Duration::ZERO;
        }
        Duration::from_millis(match self {
            Self::Present => PRESENT_MS,
            Self::Dismiss => DISMISS_MS,
            Self::Sibling => SIBLING_MS,
            Self::Push => PUSH_MS,
            Self::Pop => POP_MS,
            Self::Step => STEP_MS,
            Self::Disclose => icedtea::m3::motion::EXPAND.millis(),
            Self::None => 0,
        })
    }

    /// Enter jobs decelerate; dismiss accelerates.
    pub fn ease(self) -> icedtea::m3::Ease {
        match self {
            Self::Dismiss => icedtea::m3::Ease::EmphasizedAccelerate,
            _ => icedtea::m3::Ease::EmphasizedDecelerate,
        }
    }

    /// iced [`Animation`] easing for this role.
    pub fn easing(self) -> iced::animation::Easing {
        self.ease().lilt()
    }
}

/// Slide to paint, if any. Sibling and reduced motion never translate.
pub fn visual_slide(
    role: MotionRole,
    slide: icedtea::motion::Slide,
    reduced: bool,
) -> icedtea::motion::Slide {
    if reduced || matches!(role, MotionRole::Sibling | MotionRole::None) {
        icedtea::motion::Slide::None
    } else {
        slide
    }
}

/// True when a new page job should start from 0 instead of keeping progress.
pub fn page_restarts(progress: f32, animating: bool) -> bool {
    !animating || progress >= 0.999
}

/// Tab change is a sibling fade.
pub fn tab_role(from: Tab, to: Tab) -> MotionRole {
    if from == to {
        MotionRole::None
    } else {
        MotionRole::Sibling
    }
}

/// First open is push; stepping to another event is a vertical cover.
pub fn event_open_role(already_open: bool) -> MotionRole {
    if already_open {
        MotionRole::Step
    } else {
        MotionRole::Push
    }
}

/// Leave full-pane event detail.
pub fn event_close_role() -> MotionRole {
    MotionRole::Pop
}

/// Vertical cover for next / previous event.
pub fn event_step_slide(delta: i32) -> icedtea::motion::Slide {
    if delta > 0 {
        icedtea::motion::Slide::Up
    } else if delta < 0 {
        icedtea::motion::Slide::Down
    } else {
        icedtea::motion::Slide::None
    }
}

/// Pick a session into browse.
pub fn session_enter_role() -> MotionRole {
    MotionRole::Push
}

/// Leave browse for the session list.
pub fn session_leave_role() -> MotionRole {
    MotionRole::Pop
}

/// Animation parked at `open` with this role's duration and ease.
pub fn role_animation(role: MotionRole, open: bool, reduced: bool) -> Animation<bool> {
    Animation::new(open)
        .duration(role.duration(reduced))
        .easing(role.easing())
}

/// Note expander height.
pub fn disclose_animation(open: bool, reduced: bool) -> Animation<bool> {
    icedtea::motion::expand_animation(open, reduced)
}

/// Continue an in-flight page fade or start a new 0→1 job.
pub fn continue_or_restart(
    page: Animation<bool>,
    role: MotionRole,
    current: f32,
    animating: bool,
    reduced: bool,
    now: Instant,
) -> Animation<bool> {
    if reduced {
        return role_animation(role, true, true);
    }
    if page_restarts(current, animating) {
        let mut next = role_animation(role, false, false);
        next.go_mut(true, now);
        next
    } else {
        let mut next = page.duration(role.duration(false)).easing(role.easing());
        if !next.is_animating(now) {
            next.go_mut(true, now);
        }
        next
    }
}

/// Retune show/hide from the current overlay value (interruptible).
pub fn retune_overlay(
    overlay: Animation<bool>,
    open: bool,
    reduced: bool,
    now: Instant,
) -> Animation<bool> {
    let role = if open {
        MotionRole::Present
    } else {
        MotionRole::Dismiss
    };
    if reduced {
        return role_animation(role, open, true);
    }
    let mut next = overlay.duration(role.duration(false)).easing(role.easing());
    next.go_mut(open, now);
    next
}

/// Env / GTK animation off. Tests set [`crate::app::Hud`] directly.
pub fn detect_reduced_motion() -> bool {
    match std::env::var("GROKET_HUD_REDUCED_MOTION") {
        Ok(v) => matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes"),
        Err(_) => std::env::var("GTK_ENABLE_ANIMATIONS").is_ok_and(|v| v == "0"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn present_is_short_ease_out_dismiss_is_shorter_ease_in() {
        assert_eq!(PRESENT_MS, 220);
        assert_eq!(DISMISS_MS, 180);
        const { assert!(DISMISS_MS < PRESENT_MS) };
        assert_eq!(
            MotionRole::Present.ease(),
            icedtea::m3::Ease::EmphasizedDecelerate
        );
        assert_eq!(
            MotionRole::Dismiss.ease(),
            icedtea::m3::Ease::EmphasizedAccelerate
        );
        assert_eq!(
            MotionRole::Present.duration(false),
            Duration::from_millis(PRESENT_MS)
        );
        assert_eq!(
            MotionRole::Dismiss.duration(false),
            Duration::from_millis(DISMISS_MS)
        );
        assert_eq!(MotionRole::Present.duration(true), Duration::ZERO);
        assert_eq!(MotionRole::Dismiss.duration(true), Duration::ZERO);
    }

    #[test]
    fn tab_change_is_fade_not_slide() {
        assert_eq!(tab_role(Tab::Turns, Tab::Timeline), MotionRole::Sibling);
        assert_eq!(
            visual_slide(MotionRole::Sibling, icedtea::motion::Slide::End, false),
            icedtea::motion::Slide::None
        );
        assert_eq!(tab_role(Tab::Turns, Tab::Turns), MotionRole::None);
    }

    #[test]
    fn event_open_close_and_step_map_to_push_pop_cover() {
        assert_eq!(event_open_role(false), MotionRole::Push);
        assert_eq!(event_open_role(true), MotionRole::Step);
        assert_eq!(event_close_role(), MotionRole::Pop);
        assert_eq!(event_step_slide(1), icedtea::motion::Slide::Up);
        assert_eq!(event_step_slide(-1), icedtea::motion::Slide::Down);
        assert_eq!(event_step_slide(0), icedtea::motion::Slide::None);
        assert_eq!(
            visual_slide(MotionRole::Push, icedtea::motion::Slide::End, false),
            icedtea::motion::Slide::End
        );
        assert_eq!(
            visual_slide(MotionRole::Pop, icedtea::motion::Slide::Start, false),
            icedtea::motion::Slide::Start
        );
        assert_eq!(
            visual_slide(MotionRole::Push, icedtea::motion::Slide::End, true),
            icedtea::motion::Slide::None
        );
    }

    #[test]
    fn session_pick_is_push_and_home_is_pop() {
        assert_eq!(session_enter_role(), MotionRole::Push);
        assert_eq!(session_leave_role(), MotionRole::Pop);
    }

    #[test]
    fn mid_flight_page_does_not_restart_from_zero() {
        assert!(page_restarts(1.0, false));
        assert!(page_restarts(0.0, false));
        assert!(!page_restarts(0.4, true));
        let started = Instant::now() - Duration::from_millis(80);
        let mut page = role_animation(MotionRole::Sibling, false, false);
        page.go_mut(true, started);
        let now = Instant::now();
        let mid = page.interpolate(0.0, 1.0, now);
        assert!(
            mid > 0.1 && mid < 0.95,
            "expected mid-flight progress, got {mid}"
        );
        let next = continue_or_restart(page, MotionRole::Sibling, mid, true, false, now);
        let after = next.interpolate(0.0, 1.0, now);
        assert!(
            after > 0.1,
            "second job reset progress to {after} (was {mid})"
        );
    }

    #[test]
    fn reduced_motion_page_snaps_open() {
        let page = role_animation(MotionRole::Push, false, false);
        let next = continue_or_restart(page, MotionRole::Push, 0.0, false, true, Instant::now());
        assert!(!next.is_animating(Instant::now()));
        assert!((next.interpolate(0.0, 1.0, Instant::now()) - 1.0).abs() < 0.01);
    }

    #[test]
    fn overlay_present_and_dismiss_use_role_durations() {
        let now = Instant::now();
        let show = retune_overlay(
            role_animation(MotionRole::Present, false, false),
            true,
            false,
            now,
        );
        let hide = retune_overlay(
            role_animation(MotionRole::Present, true, false),
            false,
            false,
            now,
        );
        let show_left = show.remaining(now).as_millis() as u64;
        assert!(
            (200..=PRESENT_MS).contains(&show_left),
            "present remaining {show_left}"
        );
        assert!(hide.is_animating(now));
        assert!(
            hide.interpolate(0.0, 1.0, now) > 0.9,
            "dismiss starts from open"
        );
        let snapped = retune_overlay(show, false, true, now);
        assert!(!snapped.is_animating(now));
    }

    #[test]
    fn disclose_progress_is_between_ends_while_opening() {
        let started = Instant::now() - Duration::from_millis(80);
        let mut anim = disclose_animation(false, false);
        anim.go_mut(true, started);
        let p = anim.interpolate(0.0, 1.0, Instant::now());
        assert!(p > 0.0 && p < 1.0, "expander progress {p}");
        let mut snap = disclose_animation(false, true);
        snap.go_mut(true, Instant::now());
        assert!((snap.interpolate(0.0, 1.0, Instant::now()) - 1.0).abs() < 0.01);
    }
}
