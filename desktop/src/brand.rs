//! groket identity for the HUD (window icon, tray, search chrome).
//!
//! - **Search chrome** → full mark at 32px (colour / reverse), brand guidelines.
//! - **Window / tray** → dual-contrast badge (``groket-tray-*.png``):
//!   cream plate, ink rim, **7×3 three-bar small mark**. Cream reads on dark
//!   panels; rim + bars read on light. Full rocket icons mud at 16–22px.
//! - **Desktop notify** → tray tile on Linux (small freedesktop slot);
//!   square app icon on macOS / Windows (Notification Center / toast face).
//! - **Dock / desktop install** → square app icons (``groket-app-icon-*.png``).
//!
//! Small tray PNGs keep X11 and StatusNotifier happy; large tiles are for
//! ``--install-desktop`` and the macOS application image.

use std::sync::OnceLock;

use iced::widget::image;
use iced::window::icon;
use iced::Length;

/// Taskbar / Alt-Tab / tray: cream + three bars (built by brand/build.py).
pub const TRAY_32_PNG: &[u8] = include_bytes!("../../brand/png/groket-tray-32.png");
pub const TRAY_48_PNG: &[u8] = include_bytes!("../../brand/png/groket-tray-48.png");
pub const TRAY_64_PNG: &[u8] = include_bytes!("../../brand/png/groket-tray-64.png");
pub const TRAY_128_PNG: &[u8] = include_bytes!("../../brand/png/groket-tray-128.png");

/// Browser-tab / 16px theme slot (favicon art).
pub const FAVICON_16_PNG: &[u8] = include_bytes!("../../brand/png/groket-favicon-16.png");

/// Square dock / desktop tiles (rocket on plate).
pub const APP_ICON_256_PNG: &[u8] = include_bytes!("../../brand/png/groket-app-icon-256.png");
pub const APP_ICON_512_PNG: &[u8] = include_bytes!("../../brand/png/groket-app-icon-512.png");
pub const APP_ICON_1024_PNG: &[u8] = include_bytes!("../../brand/png/groket-app-icon-1024.png");

/// macOS ``NSApplication`` / Dock tile (512 is enough without a 1024 decode hit).
pub const APP_ICON_PNG: &[u8] = APP_ICON_512_PNG;

/// Colour mark (transparent). Light ``$surface``. Guidelines: search chrome 32px.
pub const MARK_PNG: &[u8] = include_bytes!("../../brand/png/groket-mark.png");

/// Reverse mark (cream rocket on ink). Dark chrome knocks the field out.
pub const MARK_REVERSE_PNG: &[u8] = include_bytes!("../../brand/png/groket-mark-reverse.png");

/// Mark viewBox 900×380. Search chrome preferred height from brand guidelines.
pub const MARK_H: f32 = 32.0;
pub const MARK_W: f32 = MARK_H * 900.0 / 380.0;

/// Window / Alt-Tab icon (128 three-bar tray tile — still small on the X11 wire).
pub fn window_icon() -> Option<iced::window::Icon> {
    icon::from_file_data(TRAY_128_PNG, None).ok()
}

/// Tray pixmap (64px three-bar tile).
pub fn tray_icon_png() -> &'static [u8] {
    TRAY_64_PNG
}

/// Desktop-notify pixmap for the host slot.
///
/// Linux uses the 64px tray tile (small freedesktop icon). macOS Notification
/// Center and Windows toasts show a large face, so those hosts use the 256px
/// square app icon. ``notify-rust`` ``icon()`` is a no-op on macOS.
pub fn notify_icon_png() -> &'static [u8] {
    #[cfg(target_os = "linux")]
    {
        TRAY_64_PNG
    }
    #[cfg(not(target_os = "linux"))]
    {
        APP_ICON_256_PNG
    }
}

/// Theme / installer sizes: (pixel edge, PNG bytes).
pub fn desktop_icon_pngs() -> &'static [(u32, &'static [u8])] {
    &[
        (16, FAVICON_16_PNG),
        (32, TRAY_32_PNG),
        (48, TRAY_48_PNG),
        (64, TRAY_64_PNG),
        (128, TRAY_128_PNG),
        (256, APP_ICON_256_PNG),
        (512, APP_ICON_512_PNG),
        (1024, APP_ICON_1024_PNG),
    ]
}

/// Search-bar mark: colour on light canvas, reverse (knocked-out ink) on dark.
pub fn chrome_handle(dark_canvas: bool) -> image::Handle {
    if dark_canvas {
        reverse_chrome_handle()
    } else {
        colour_chrome_handle()
    }
}

pub fn chrome_width() -> Length {
    Length::Fixed(MARK_W)
}

pub fn chrome_height() -> Length {
    Length::Fixed(MARK_H)
}

fn colour_chrome_handle() -> image::Handle {
    static HANDLE: OnceLock<image::Handle> = OnceLock::new();
    HANDLE
        .get_or_init(|| image::Handle::from_bytes(MARK_PNG))
        .clone()
}

fn reverse_chrome_handle() -> image::Handle {
    static HANDLE: OnceLock<image::Handle> = OnceLock::new();
    HANDLE.get_or_init(knocked_out_reverse).clone()
}

/// Reverse PNG is cream + caps on an ink field. Drop ink so the rocket sits
/// on ``$surface`` (gruvbox grey, not only true black). Ink is ``#282828``.
fn knocked_out_reverse() -> image::Handle {
    let decoded = icon::from_file_data(MARK_REVERSE_PNG, None).expect("groket-mark-reverse.png");
    let (mut rgba, size) = decoded.into_raw();
    for px in rgba.as_chunks_mut::<4>().0 {
        if px[0] < 48 && px[1] < 48 && px[2] < 48 {
            px[3] = 0;
        }
    }
    image::Handle::from_rgba(size.width, size.height, rgba)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_and_tray_use_dual_contrast_three_bar_tiles() {
        let win = window_icon().expect("tray-128");
        let (rgba, size) = win.into_raw();
        assert_eq!((size.width, size.height), (128, 128));
        let cream = rgba
            .as_chunks::<4>()
            .0
            .iter()
            .filter(|p| p[3] > 200 && p[0] > 200 && p[1] > 180)
            .count();
        assert!(cream * 2 > rgba.len() / 4, "cream plate should dominate");
        // Ink rim (dark opaque near full alpha) — light desktop edge.
        let ink = rgba
            .as_chunks::<4>()
            .0
            .iter()
            .filter(|p| p[3] > 200 && p[0] < 60 && p[1] < 60 && p[2] < 60)
            .count();
        assert!(ink > 80, "ink rim should frame the cream plate");
        // Status caps present (red / green / yellow).
        let red = rgba
            .as_chunks::<4>()
            .0
            .iter()
            .filter(|p| p[0] > 180 && p[1] < 80 && p[2] < 80 && p[3] > 200)
            .count();
        assert!(red > 10, "failed (red) cap should be visible");
        assert_eq!(tray_icon_png()[1..4], *b"PNG");
        assert_eq!(tray_icon_png().len(), TRAY_64_PNG.len());
        assert_eq!(notify_icon_png()[1..4], *b"PNG");
        #[cfg(target_os = "linux")]
        assert_eq!(notify_icon_png().len(), TRAY_64_PNG.len());
        #[cfg(not(target_os = "linux"))]
        assert_eq!(notify_icon_png().len(), APP_ICON_256_PNG.len());
    }

    #[test]
    fn chrome_mark_is_wide_rocket_at_32px() {
        assert_eq!(MARK_PNG[1..4], *b"PNG");
        assert_eq!(MARK_REVERSE_PNG[1..4], *b"PNG");
        assert!((MARK_W - MARK_H * 900.0 / 380.0).abs() < 0.01);
        assert_eq!(MARK_H, 32.0);
        let _ = (chrome_width(), chrome_height());
        let h = chrome_handle(true);
        match h {
            image::Handle::Rgba {
                width,
                height,
                pixels,
                ..
            } => {
                assert_eq!((width, height), (1200, 507));
                let clear = pixels
                    .as_chunks::<4>()
                    .0
                    .iter()
                    .filter(|p| p[3] == 0)
                    .count();
                assert!(
                    clear * 2 > pixels.len() / 4,
                    "reverse chrome knocks out ink field"
                );
            }
            _ => panic!("reverse chrome should be decoded RGBA"),
        }
    }
}
