//! StatusNotifier / menu-bar tray for the long-lived HUD process.
//!
//! Left-click toggles the overlay (same as ``groket hud --toggle``).
//! Menu **Show** always shows. **Quit groket** exits this process only.

use std::sync::mpsc::{self, Receiver, RecvError, SyncSender};
use std::sync::{Mutex, OnceLock};

use thiserror::Error;

/// Three-bar small-mark favicon (brand: tab / tray).
pub fn tray_png() -> &'static [u8] {
    crate::brand::tray_icon_png()
}

pub const TRAY_ID: &str = "dev.indynull.groket-hud";
pub const TRAY_TOOLTIP: &str = "groket";
pub const MENU_SHOW_ID: &str = "show";
pub const MENU_QUIT_ID: &str = "quit";
pub const MENU_SHOW_LABEL: &str = "Show";
pub const MENU_QUIT_LABEL: &str = "Quit groket";
pub const SHOW_ON_START_ENV: &str = "GROKET_HUD_SHOW_ON_START";

/// Operator action the iced loop should run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayAction {
    Show,
    Toggle,
    Quit,
}

/// Mouse button on a tray click (host-independent).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayButton {
    Left,
    Right,
    Other,
}

/// Button edge for a tray click.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayClickState {
    Down,
    Up,
}

#[derive(Debug, Error)]
pub enum TrayError {
    #[error("embedded tray icon is not a usable PNG")]
    BadIcon,
    #[error("{0}")]
    Host(String),
}

/// Keeps the platform tray service alive for the HUD process.
pub struct HudTray {
    #[cfg(target_os = "linux")]
    _handle: linux::Handle,
    #[cfg(not(target_os = "linux"))]
    _icon: other::Handle,
}

/// Map a menu item id to an action.
pub fn action_from_menu_id(id: &str) -> Option<TrayAction> {
    match id {
        MENU_SHOW_ID => Some(TrayAction::Show),
        MENU_QUIT_ID => Some(TrayAction::Quit),
        _ => None,
    }
}

/// Left-button release toggles the palette. Other clicks do not.
pub fn action_from_click(button: TrayButton, state: TrayClickState) -> Option<TrayAction> {
    match (button, state) {
        (TrayButton::Left, TrayClickState::Up) => Some(TrayAction::Toggle),
        _ => None,
    }
}

/// True for ``1`` / ``true`` / ``yes`` (case-insensitive).
pub fn env_flag_enabled(value: Option<&str>) -> bool {
    matches!(
        value.map(str::trim),
        Some(v) if matches!(v.to_ascii_lowercase().as_str(), "1" | "true" | "yes")
    )
}

/// True when ``GROKET_HUD_SHOW_ON_START`` requests a boot show.
pub fn show_on_start() -> bool {
    env_flag_enabled(std::env::var(SHOW_ON_START_ENV).ok().as_deref())
}

/// Decode the packaged tray PNG to RGBA.
pub fn decode_tray_rgba() -> Result<(Vec<u8>, u32, u32), TrayError> {
    let icon =
        iced::window::icon::from_file_data(tray_png(), None).map_err(|_| TrayError::BadIcon)?;
    let (rgba, size) = icon.into_raw();
    if size.width == 0 || size.height == 0 || rgba.len() != (size.width * size.height * 4) as usize
    {
        return Err(TrayError::BadIcon);
    }
    Ok((rgba, size.width, size.height))
}

/// RGBA bytes to network-order ARGB32 (StatusNotifier pixmap).
pub fn rgba_to_argb(rgba: &[u8]) -> Vec<u8> {
    let mut out = rgba.to_vec();
    for px in out.as_chunks_mut::<4>().0 {
        px.rotate_right(1);
    }
    out
}

/// Register the tray. Linux treats construction failure as fatal at the caller.
pub fn install() -> Result<HudTray, TrayError> {
    let _ = action_sender();
    #[cfg(target_os = "linux")]
    {
        Ok(HudTray {
            _handle: linux::install()?,
        })
    }
    #[cfg(not(target_os = "linux"))]
    {
        Ok(HudTray {
            _icon: other::install()?,
        })
    }
}

/// End this process. Tray and notify threads otherwise keep it alive
/// after the iced loop returns.
pub fn quit_process() -> ! {
    std::process::exit(0)
}

/// Ask iced to quit, then end the process if the loop does not return.
pub fn schedule_quit_process() {
    let _ = std::thread::Builder::new()
        .name("groket-hud-quit".into())
        .spawn(|| {
            std::thread::sleep(std::time::Duration::from_millis(150));
            quit_process();
        });
}

/// Block until the next tray action (used by the iced subscription).
///
/// The lock is not held across a blocking recv, so a remounted iced
/// subscription cannot deadlock Quit behind the previous waiter.
pub fn recv_action() -> Result<TrayAction, RecvError> {
    loop {
        let outcome = {
            let guard = action_pair().1.lock().expect("tray action mutex");
            guard.try_recv()
        };
        match outcome {
            Ok(action) => return Ok(action),
            Err(std::sync::mpsc::TryRecvError::Disconnected) => return Err(RecvError),
            Err(std::sync::mpsc::TryRecvError::Empty) => {
                std::thread::sleep(std::time::Duration::from_millis(25));
            }
        }
    }
}

fn action_pair() -> &'static (SyncSender<TrayAction>, Mutex<Receiver<TrayAction>>) {
    static PAIR: OnceLock<(SyncSender<TrayAction>, Mutex<Receiver<TrayAction>>)> = OnceLock::new();
    PAIR.get_or_init(|| {
        let (tx, rx) = mpsc::sync_channel(16);
        (tx, Mutex::new(rx))
    })
}

fn action_sender() -> SyncSender<TrayAction> {
    action_pair().0.clone()
}

#[cfg(not(target_os = "linux"))]
fn emit(action: TrayAction) {
    let _ = action_sender().send(action);
}

#[cfg(target_os = "linux")]
mod linux {
    use super::{
        action_sender, decode_tray_rgba, rgba_to_argb, TrayAction, TrayError, MENU_QUIT_LABEL,
        MENU_SHOW_LABEL, TRAY_ID, TRAY_TOOLTIP,
    };
    use ksni::blocking::TrayMethods;
    use ksni::{Icon, Tray};

    pub type Handle = ksni::blocking::Handle<GroketTray>;

    pub struct GroketTray {
        tx: std::sync::mpsc::SyncSender<TrayAction>,
        icon: Icon,
    }

    impl Tray for GroketTray {
        fn id(&self) -> String {
            TRAY_ID.into()
        }

        fn title(&self) -> String {
            TRAY_TOOLTIP.into()
        }

        fn category(&self) -> ksni::Category {
            ksni::Category::ApplicationStatus
        }

        fn icon_pixmap(&self) -> Vec<Icon> {
            vec![self.icon.clone()]
        }

        fn activate(&mut self, _x: i32, _y: i32) {
            let _ = self.tx.send(TrayAction::Toggle);
        }

        fn menu(&self) -> Vec<ksni::MenuItem<Self>> {
            use ksni::menu::*;
            vec![
                StandardItem {
                    label: MENU_SHOW_LABEL.into(),
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(TrayAction::Show);
                    }),
                    ..Default::default()
                }
                .into(),
                StandardItem {
                    label: MENU_QUIT_LABEL.into(),
                    activate: Box::new(|this: &mut Self| {
                        let _ = this.tx.send(TrayAction::Quit);
                        super::schedule_quit_process();
                    }),
                    ..Default::default()
                }
                .into(),
            ]
        }
    }

    pub fn install() -> Result<Handle, TrayError> {
        let (rgba, width, height) = decode_tray_rgba()?;
        let tray = GroketTray {
            tx: action_sender(),
            icon: Icon {
                width: i32::try_from(width).map_err(|_| TrayError::BadIcon)?,
                height: i32::try_from(height).map_err(|_| TrayError::BadIcon)?,
                data: rgba_to_argb(&rgba),
            },
        };
        tray.spawn().map_err(|err| TrayError::Host(err.to_string()))
    }
}

#[cfg(not(target_os = "linux"))]
mod other {
    use super::{
        action_from_click, action_from_menu_id, decode_tray_rgba, emit, TrayButton, TrayClickState,
        TrayError, MENU_QUIT_ID, MENU_QUIT_LABEL, MENU_SHOW_ID, MENU_SHOW_LABEL, TRAY_TOOLTIP,
    };
    use tray_icon::menu::{Menu, MenuEvent, MenuItem};
    use tray_icon::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};

    pub type Handle = TrayIcon;

    pub fn install() -> Result<Handle, TrayError> {
        let menu = Menu::new();
        let show = MenuItem::with_id(MENU_SHOW_ID, MENU_SHOW_LABEL, true, None);
        let quit = MenuItem::with_id(MENU_QUIT_ID, MENU_QUIT_LABEL, true, None);
        menu.append(&show)
            .map_err(|err| TrayError::Host(err.to_string()))?;
        menu.append(&quit)
            .map_err(|err| TrayError::Host(err.to_string()))?;

        let (rgba, width, height) = decode_tray_rgba()?;
        let icon = tray_icon::Icon::from_rgba(rgba, width, height)
            .map_err(|err| TrayError::Host(err.to_string()))?;
        let tray = TrayIconBuilder::new()
            .with_tooltip(TRAY_TOOLTIP)
            .with_icon(icon)
            .with_menu(Box::new(menu))
            .with_menu_on_left_click(false)
            .build()
            .map_err(|err| TrayError::Host(err.to_string()))?;

        std::thread::Builder::new()
            .name("groket-tray-click".into())
            .spawn(|| {
                while let Ok(ev) = TrayIconEvent::receiver().recv() {
                    if let Some(action) = map_click(&ev) {
                        emit(action);
                    }
                }
            })
            .map_err(|err| TrayError::Host(err.to_string()))?;
        std::thread::Builder::new()
            .name("groket-tray-menu".into())
            .spawn(|| {
                while let Ok(ev) = MenuEvent::receiver().recv() {
                    if let Some(action) = action_from_menu_id(ev.id.as_ref()) {
                        emit(action);
                        if action == super::TrayAction::Quit {
                            super::schedule_quit_process();
                        }
                    }
                }
            })
            .map_err(|err| TrayError::Host(err.to_string()))?;
        Ok(tray)
    }

    fn map_click(ev: &TrayIconEvent) -> Option<super::TrayAction> {
        match ev {
            TrayIconEvent::Click {
                button,
                button_state,
                ..
            } => action_from_click(map_button(*button), map_state(*button_state)),
            _ => None,
        }
    }

    fn map_button(button: MouseButton) -> TrayButton {
        match button {
            MouseButton::Left => TrayButton::Left,
            MouseButton::Right => TrayButton::Right,
            _ => TrayButton::Other,
        }
    }

    fn map_state(state: MouseButtonState) -> TrayClickState {
        match state {
            MouseButtonState::Up => TrayClickState::Up,
            MouseButtonState::Down => TrayClickState::Down,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn menu_ids_route_show_and_quit() {
        assert_eq!(action_from_menu_id(MENU_SHOW_ID), Some(TrayAction::Show));
        assert_eq!(action_from_menu_id(MENU_QUIT_ID), Some(TrayAction::Quit));
        assert_eq!(action_from_menu_id("other"), None);
    }

    #[test]
    fn left_click_release_toggles() {
        assert_eq!(
            action_from_click(TrayButton::Left, TrayClickState::Up),
            Some(TrayAction::Toggle)
        );
        assert_eq!(
            action_from_click(TrayButton::Left, TrayClickState::Down),
            None
        );
        assert_eq!(
            action_from_click(TrayButton::Right, TrayClickState::Up),
            None
        );
    }

    #[test]
    fn show_on_start_flag_parses() {
        assert!(env_flag_enabled(Some("1")));
        assert!(env_flag_enabled(Some("true")));
        assert!(env_flag_enabled(Some("YES")));
        assert!(!env_flag_enabled(Some("0")));
        assert!(!env_flag_enabled(Some("")));
        assert!(!env_flag_enabled(None));
    }

    #[test]
    fn packaged_icon_is_png_square() {
        assert_eq!(&tray_png()[1..4], b"PNG");
        let (rgba, width, height) = decode_tray_rgba().expect("tray icon");
        assert_eq!(width, height);
        assert!(
            width >= 32,
            "tray icon should be at least 32px before host scale"
        );
        assert_eq!(rgba.len(), (width * height * 4) as usize);
    }

    #[test]
    fn rgba_to_argb_rotates_channels() {
        let argb = rgba_to_argb(&[0x11, 0x22, 0x33, 0x44]);
        assert_eq!(argb, vec![0x44, 0x11, 0x22, 0x33]);
    }

    #[test]
    fn quit_menu_schedules_process_exit() {
        let src = include_str!("tray.rs");
        assert!(src.contains("schedule_quit_process"));
        assert!(src.contains("fn quit_process"));
        let main = include_str!("main.rs");
        assert!(main.contains("std::process::exit"));
    }
}
