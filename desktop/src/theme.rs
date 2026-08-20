//! Map Textual theme tokens (``config.toml`` ``theme``) onto iced.

use std::sync::OnceLock;

use iced::Color;
use iced::Theme;
use serde_json::Value;

use crate::format::BrandRole;

pub use icedtea::theme::{mix, relative_luma, Tokens};

use icedtea::density::DensityName;
use icedtea::m3::{ElevationPolicy, ShapePolicy};

/// Live look knobs (same set as the icedtea gallery strip).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Look {
    pub density: DensityName,
    pub font_scale: f32,
    pub shape: ShapePolicy,
    pub elevation: ElevationPolicy,
}

impl Default for Look {
    fn default() -> Self {
        Self {
            density: DensityName::Default,
            font_scale: 1.0,
            shape: ShapePolicy::Desktop,
            elevation: ElevationPolicy::Desktop,
        }
    }
}

impl Look {
    pub fn density_label(self) -> &'static str {
        match self.density {
            DensityName::Compact => "Compact",
            DensityName::Default => "Default",
            DensityName::Comfortable => "Comfortable",
        }
    }

    pub fn scale_label(self) -> &'static str {
        match self.font_scale {
            x if (x - 0.875).abs() < 0.01 => "90%",
            x if (x - 1.125).abs() < 0.01 => "110%",
            x if (x - 1.25).abs() < 0.01 => "125%",
            _ => "100%",
        }
    }

    pub fn shape_label(self) -> &'static str {
        match self.shape {
            ShapePolicy::Tight => "Tight",
            ShapePolicy::Soft => "Soft",
            ShapePolicy::Pill => "Pill",
            ShapePolicy::Material => "Material",
            ShapePolicy::Desktop => "Desktop",
        }
    }

    pub fn elevation_label(self) -> &'static str {
        match self.elevation {
            ElevationPolicy::Flat => "Flat",
            ElevationPolicy::Desktop => "Desktop",
        }
    }

    pub fn with_density_label(mut self, name: &str) -> Self {
        self.density = match name {
            "Compact" => DensityName::Compact,
            "Comfortable" => DensityName::Comfortable,
            _ => DensityName::Default,
        };
        self
    }

    pub fn with_scale_label(mut self, name: &str) -> Self {
        self.font_scale = match name {
            "90%" => 0.875,
            "110%" => 1.125,
            "125%" => 1.25,
            _ => 1.0,
        };
        self
    }

    pub fn with_shape_label(mut self, name: &str) -> Self {
        self.shape = match name {
            "Tight" => ShapePolicy::Tight,
            "Soft" => ShapePolicy::Soft,
            "Pill" => ShapePolicy::Pill,
            "Material" => ShapePolicy::Material,
            _ => ShapePolicy::Desktop,
        };
        self
    }

    pub fn with_elevation_label(mut self, name: &str) -> Self {
        self.elevation = match name {
            "Flat" => ElevationPolicy::Flat,
            _ => ElevationPolicy::Desktop,
        };
        self
    }
}

/// TUI brand hex (``COMPLETE`` / ``FAILED`` / ``RUNNING`` / ``CANCELLED`` / ``CREAM``).
pub const BRAND_CREAM: Color = Color::from_rgb8(0xFB, 0xF1, 0xC7);
pub const BRAND_COMPLETE: Color = Color::from_rgb8(0x98, 0x97, 0x1A);
pub const BRAND_RUNNING: Color = Color::from_rgb8(0xD7, 0x99, 0x21);
pub const BRAND_FAILED: Color = Color::from_rgb8(0xCC, 0x24, 0x1D);
pub const BRAND_CANCELLED: Color = Color::from_rgb8(0x92, 0x83, 0x74);

/// Color for a TUI ``EVENT_TYPE_STYLE`` brand role.
pub fn brand_role_color(role: BrandRole) -> Color {
    match role {
        BrandRole::Cream => BRAND_CREAM,
        BrandRole::Complete => BRAND_COMPLETE,
        BrandRole::Running => BRAND_RUNNING,
        BrandRole::Failed => BRAND_FAILED,
        BrandRole::Cancelled => BRAND_CANCELLED,
    }
}

const CATALOG: &str = include_str!("../assets/textual-themes.json");

/// True when ``$surface`` is a dark canvas (gruvbox, nord, …).
pub fn canvas_is_dark(tok: Tokens) -> bool {
    relative_luma(tok.canvas) < 0.45
}

fn srgb_lin(c: f32) -> f32 {
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

fn wcag_luma(c: Color) -> f32 {
    0.2126 * srgb_lin(c.r) + 0.7152 * srgb_lin(c.g) + 0.0722 * srgb_lin(c.b)
}

/// WCAG relative-luminance contrast between two sRGB colors.
pub fn contrast_ratio(a: Color, b: Color) -> f32 {
    let (l1, l2) = (wcag_luma(a), wcag_luma(b));
    let (hi, lo) = if l1 > l2 { (l1, l2) } else { (l2, l1) };
    (hi + 0.05) / (lo + 0.05)
}

/// Mix ``ink`` toward black or white until it holds 4.5:1 on ``canvas``.
pub fn ink_on(ink: Color, canvas: Color) -> Color {
    if contrast_ratio(ink, canvas) >= 4.5 {
        return ink;
    }
    let toward = if relative_luma(canvas) < 0.45 {
        Color::WHITE
    } else {
        Color::BLACK
    };
    let mut lo = 0.0f32;
    let mut hi = 1.0f32;
    let mut best = toward;
    for _ in 0..12 {
        let mid = (lo + hi) * 0.5;
        let candidate = mix(toward, ink, mid);
        if contrast_ratio(candidate, canvas) >= 4.5 {
            best = candidate;
            hi = mid;
        } else {
            lo = mid;
        }
    }
    best
}

fn parse_hex(s: &str) -> Option<Color> {
    let t = s.trim().trim_start_matches('#');
    if t.len() < 6 || !t.as_bytes()[..6].iter().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    let r = u8::from_str_radix(&t[0..2], 16).ok()?;
    let g = u8::from_str_radix(&t[2..4], 16).ok()?;
    let b = u8::from_str_radix(&t[4..6], 16).ok()?;
    Some(Color::from_rgb8(r, g, b))
}

fn color_of(colors: &Value, key: &str, fallback: Color) -> Color {
    colors
        .get(key)
        .and_then(Value::as_str)
        .and_then(parse_hex)
        .unwrap_or(fallback)
}

fn catalog_colors(name: &str) -> Option<Value> {
    let root = serde_json::from_str::<Value>(CATALOG).ok()?;
    let key = name.trim();
    if key.is_empty() {
        return None;
    }
    root.get(key)?.get("colors").cloned()
}

/// Config ``theme``. ``follow`` may pick the pair member; a pinned name stays.
pub fn resolve_name(pref: &str, appearance: icedtea::theme::Appearance, follow: bool) -> String {
    if !follow {
        return pref.to_string();
    }
    match icedtea::theme::family_of_name(pref) {
        Some(_) => icedtea::theme::resolve_pref(pref, None, true, appearance),
        None => pref.to_string(),
    }
}

/// Tokens for ``theme`` in ``~/.groket/config.toml``.
///
/// Default density is pad and control height. Type scale is 1.0
/// (Material body). The F12 Look drawer changes scale live.
pub fn tokens(name: &str) -> Tokens {
    tokens_with(name, Look::default())
}

/// Theme colors with live look knobs (density, type scale, shape, elevation).
pub fn tokens_with(name: &str, look: Look) -> Tokens {
    let key = name.trim();
    let tok = if catalog_colors(key).is_some() {
        textual_tokens(key)
    } else if key.is_empty() {
        textual_tokens("textual-dark")
    } else {
        icedtea::theme::named(key).tokens
    };
    tok.with_density(icedtea::m3::Density::named(look.density))
        .with_font_scale(look.font_scale)
        .with_shape(look.shape)
        .with_elevation(look.elevation)
}

fn textual_tokens(name: &str) -> Tokens {
    let colors = catalog_colors(name).unwrap_or(Value::Null);
    let fallback_bg = Color::from_rgb8(18, 18, 20);
    let canvas = color_of(&colors, "surface", fallback_bg);
    let text = color_of(&colors, "foreground", Color::from_rgb8(224, 224, 224));
    let muted = color_of(
        &colors,
        "foreground-darken-2",
        color_of(&colors, "foreground-muted", Color::from_rgb8(160, 160, 160)),
    );
    let primary = color_of(&colors, "primary", Color::from_rgb8(1, 120, 212));
    let accent = color_of(&colors, "accent", Color::from_rgb8(254, 166, 43));
    let highlight = color_of(&colors, "primary-background", mix(primary, canvas, 0.35));
    let success = color_of(
        &colors,
        "text-success",
        color_of(&colors, "success", Color::from_rgb8(78, 191, 113)),
    );
    let warning = color_of(
        &colors,
        "text-warning",
        color_of(&colors, "warning", Color::from_rgb8(254, 166, 43)),
    );
    let danger = color_of(
        &colors,
        "text-error",
        color_of(&colors, "error", Color::from_rgb8(185, 60, 91)),
    );
    let panel = color_of(&colors, "panel", mix(text, canvas, 0.10));
    Tokens::from_aliases(
        canvas, canvas, panel, text, muted, primary, accent, success, warning, danger, highlight,
    )
}

/// Textual theme names registered on icedtea's catalog.
pub fn catalog() -> &'static icedtea::theme::ThemeCatalog {
    static CATALOG_MAP: OnceLock<icedtea::theme::ThemeCatalog> = OnceLock::new();
    CATALOG_MAP.get_or_init(|| {
        let mut cat = icedtea::theme::ThemeCatalog::new();
        let Ok(root) = serde_json::from_str::<Value>(CATALOG) else {
            return cat;
        };
        let Some(obj) = root.as_object() else {
            return cat;
        };
        for key in obj.keys() {
            let tok = tokens(key);
            cat.register(key.clone(), tok, canvas_is_dark(tok));
        }
        cat
    })
}

pub fn iced_theme(name: &str) -> Theme {
    icedtea::theme::iced_theme(name, tokens(name))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn textual_dark_uses_screen_surface_not_void_background() {
        let t = tokens("textual-dark");
        assert_eq!(t.canvas, Color::from_rgb8(0x1E, 0x1E, 0x1E));
        assert_ne!(t.canvas, Color::from_rgb8(0x12, 0x12, 0x12));
        assert_eq!(t.primary, Color::from_rgb8(0x01, 0x78, 0xD4));
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selection, icedtea::theme::mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selection_text, t.text);
        assert_eq!(t.border, Color::from_rgb8(0x33, 0x42, 0x4E));
    }

    #[test]
    fn gruvbox_is_in_catalog() {
        let t = tokens("gruvbox");
        assert_ne!(t.canvas, tokens("textual-dark").canvas);
        assert_ne!(t.canvas, tokens("nord").canvas);
        assert!(canvas_is_dark(t));
        assert!(canvas_is_dark(tokens("textual-dark")));
        assert!(!canvas_is_dark(tokens("solarized-light")));
        assert!(catalog().get("gruvbox").is_some());
        assert!(catalog().get("textual-dark").is_some());
    }

    #[test]
    fn flexoki_matches_tui_screen_surface() {
        let t = tokens("flexoki");
        assert_eq!(t.canvas, Color::from_rgb8(0x1C, 0x1B, 0x1A));
        assert_ne!(t.canvas, Color::from_rgb8(0x10, 0x0F, 0x0F));
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.selection_text, t.text);
        assert_eq!(t.panel, Color::from_rgb8(0x28, 0x27, 0x26));
    }

    #[test]
    fn solarized_light_selected_row_keeps_readable_ink() {
        let t = tokens("solarized-light");
        assert_ne!(t.selection, t.accent);
        assert_eq!(t.selection_text, t.text);
        assert!((relative_luma(t.selection) - relative_luma(t.text)).abs() > 0.20);
        assert_eq!(t.selection, mix(t.primary, t.canvas, 0.28));
    }

    #[test]
    fn catalog_hex_drops_textual_alpha_suffix() {
        let t = tokens("gruvbox");
        assert_eq!(t.text, Color::from_rgb8(0xFB, 0xF1, 0xC7));
        assert_eq!(t.text.a, 1.0);
        assert_eq!(t.muted.a, 1.0);
        assert_eq!(t.muted, Color::from_rgb8(0xD0, 0xC6, 0x9E));
        assert_eq!(t.accent, Color::from_rgb8(0xF9, 0xBD, 0x2F));
        assert_eq!(t.warning, Color::from_rgb8(0xFE, 0xAB, 0x67));
        assert_eq!(t.danger, Color::from_rgb8(0xFC, 0x86, 0x79));
    }

    #[test]
    fn gruvbox_follows_desktop_to_icedtea_light_pair() {
        use icedtea::theme::Appearance;
        assert_eq!(
            resolve_name("gruvbox", Appearance::Light, true),
            "gruvbox-light"
        );
        assert_eq!(resolve_name("gruvbox", Appearance::Dark, true), "gruvbox");
        assert_eq!(
            resolve_name("gruvbox-light", Appearance::Dark, false),
            "gruvbox-light"
        );
        assert_eq!(
            resolve_name("textual-dark", Appearance::Light, true),
            "textual-dark"
        );
        assert!(!canvas_is_dark(tokens("gruvbox-light")));
        assert_eq!(tokens("gruvbox").text, Color::from_rgb8(0xFB, 0xF1, 0xC7));
    }

    #[test]
    fn mix_is_opaque_between_endpoints() {
        let a = Color::from_rgb8(255, 0, 0);
        let b = Color::from_rgb8(0, 0, 0);
        let m = mix(a, b, 0.5);
        assert!((m.r - 0.5).abs() < 0.01);
        assert_eq!(m.a, 1.0);
    }

    #[test]
    fn brand_role_colors_match_tui_hex() {
        use crate::format::BrandRole;
        assert_eq!(brand_role_color(BrandRole::Cream), BRAND_CREAM);
        assert_eq!(brand_role_color(BrandRole::Complete), BRAND_COMPLETE);
        assert_eq!(brand_role_color(BrandRole::Running), BRAND_RUNNING);
        assert_eq!(brand_role_color(BrandRole::Failed), BRAND_FAILED);
        assert_eq!(brand_role_color(BrandRole::Cancelled), BRAND_CANCELLED);
        assert_eq!(BRAND_CREAM, Color::from_rgb8(0xFB, 0xF1, 0xC7));
        assert_eq!(BRAND_COMPLETE, Color::from_rgb8(0x98, 0x97, 0x1A));
        assert_eq!(BRAND_RUNNING, Color::from_rgb8(0xD7, 0x99, 0x21));
        assert_eq!(BRAND_FAILED, Color::from_rgb8(0xCC, 0x24, 0x1D));
        assert_eq!(BRAND_CANCELLED, Color::from_rgb8(0x92, 0x83, 0x74));
    }

    #[test]
    fn named_theme_tokens_are_icedtea_tokens() {
        let t = tokens("textual-dark");
        assert_eq!(t.selection, icedtea::theme::mix(t.primary, t.canvas, 0.28));
        assert_eq!(t.surface, t.canvas);
        let registered = catalog().resolve("textual-dark");
        assert_eq!(registered.selection, t.selection);
        assert_eq!(registered.primary, t.primary);
        let light = tokens("solarized-light");
        assert_ne!(light.canvas, t.canvas);
        assert_eq!(
            light.selection,
            icedtea::theme::mix(light.primary, light.canvas, 0.28)
        );
    }

    #[test]
    fn textual_catalog_scheme_matches_short_fields() {
        for name in ["textual-dark", "gruvbox", "solarized-light", "flexoki"] {
            let t = tokens(name);
            let s = t.scheme();
            assert_eq!(s.surface, t.canvas, "{name} canvas");
            assert_eq!(s.surface_container, t.surface, "{name} surface");
            assert_eq!(s.surface_container_high, t.panel, "{name} panel");
            assert_eq!(s.on_surface, t.text, "{name} text");
            assert_eq!(s.on_surface_variant, t.muted, "{name} muted");
            assert_eq!(s.primary, t.primary, "{name} primary");
            assert_eq!(s.secondary, t.accent, "{name} accent");
            assert_eq!(s.success, t.success, "{name} success");
            assert_eq!(s.warning, t.warning, "{name} warning");
            assert_eq!(s.error, t.danger, "{name} danger");
            assert_eq!(s.outline, t.border, "{name} border");
            assert_eq!(s.secondary_container, t.selection, "{name} selection");
            assert_eq!(
                s.on_secondary_container, t.selection_text,
                "{name} sel text"
            );
        }
    }

    #[test]
    fn ink_on_lifts_brand_olive_off_cream_and_keeps_it_on_ink() {
        let cream = Color::from_rgb8(0xFB, 0xF1, 0xC7);
        let ink = Color::from_rgb8(0x28, 0x28, 0x28);
        let olive = BRAND_COMPLETE;
        let gold = BRAND_RUNNING;
        assert!(contrast_ratio(olive, cream) < 4.5);
        assert!(contrast_ratio(ink_on(olive, cream), cream) >= 4.5);
        assert!(contrast_ratio(ink_on(gold, cream), cream) >= 4.5);
        assert_eq!(ink_on(olive, ink), olive);
        assert!(contrast_ratio(ink_on(olive, ink), ink) >= 4.5);
    }

    #[test]
    fn hud_tokens_use_default_density_and_type_steps_match_roles() {
        let t = tokens("textual-dark");
        assert_eq!(t.density.name, icedtea::m3::DensityName::Default);
        assert!((t.font_scale - 1.0).abs() < f32::EPSILON);
        let scale = t.font_scale;
        let step = |role: icedtea::typo::TypeRole| (role.size() as f32 * scale).round();
        assert_eq!(t.meta(), step(icedtea::typo::TypeRole::Meta));
        assert_eq!(t.body(), step(icedtea::typo::TypeRole::Body));
        assert_eq!(t.title(), step(icedtea::typo::TypeRole::Title));
        assert_eq!(t.code(), step(icedtea::typo::TypeRole::Code));
        assert_eq!(crate::live::diff_hunk_line_h(), t.code() * 1.3);
    }

    #[test]
    fn painted_faces_use_token_type_steps() {
        let view = include_str!("view.rs");
        let kit = include_str!("kit.rs");
        let app = include_str!("app.rs");
        let prod_view = view.split("#[cfg(test)]").next().expect("view");
        let prod_kit = kit.split("#[cfg(test)]").next().expect("kit");
        let prod_app = app.split("#[cfg(test)]").next().expect("app");
        for src in [prod_view, prod_kit] {
            assert!(
                !src.contains("typo::META")
                    && !src.contains("typo::BODY")
                    && !src.contains("typo::TITLE")
                    && !src.contains("typo::CODE"),
                "paint sizes must be Tokens type steps"
            );
        }
        assert!(prod_view.contains(".size(tea.meta())") || prod_view.contains(".size(tok.meta())"));
        assert!(prod_view.contains(".size(tea.body())") || prod_view.contains(".size(tok.body())"));
        assert!(
            prod_view.contains(".size(tea.title())") || prod_view.contains(".size(tok.title())")
        );
        assert!(prod_app.contains("tokens(\"textual-dark\").body()"));
    }

    #[test]
    fn look_knobs_match_gallery_steps() {
        let d = Look::default();
        assert_eq!(d.density_label(), "Default");
        assert_eq!(d.scale_label(), "100%");
        assert_eq!(d.shape_label(), "Desktop");
        assert_eq!(d.elevation_label(), "Desktop");
        assert_eq!(
            tokens_with("textual-dark", d.with_density_label("Comfortable"))
                .density
                .name,
            DensityName::Comfortable
        );
        let scaled = tokens_with("textual-dark", d.with_scale_label("100%"));
        assert!((scaled.font_scale - 1.0).abs() < f32::EPSILON);
        assert_eq!(
            tokens_with("textual-dark", d.with_shape_label("Pill")).shape,
            ShapePolicy::Pill
        );
        assert_eq!(
            tokens_with("textual-dark", d.with_elevation_label("Flat")).elevation,
            ElevationPolicy::Flat
        );
    }
}
