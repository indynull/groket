//! groket-hud library: control decode, domain helpers, and the iced palette.

pub mod app;
pub mod brand;
pub mod control;
pub mod desktop;
pub mod format;
pub mod fuzzy;
pub mod install_desktop;
pub mod kit;
pub mod live;
pub mod log;
pub mod macoswin;
pub mod model;
pub mod place;
#[cfg(target_os = "linux")]
pub mod place_linux;
pub mod prefs;
pub mod shortcut;
pub mod summon;
pub mod theme;
pub mod tray;
pub mod typo;
pub mod view;
pub mod wire;

#[cfg(target_os = "linux")]
pub mod wlactivate;
#[cfg(target_os = "linux")]
pub mod x11focus;

pub use app::run;
