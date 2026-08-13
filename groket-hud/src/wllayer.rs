//! Layer-shell overlay contract for the Wayland HUD.
//!
//! The overlay is still an xdg-toplevel placed by Sway IPC. These values are
//! the surface the layer-shell host must keep: namespace, size, no exclusive
//! zone.

/// ``zwlr_layer_shell_v1`` namespace.
pub const NAMESPACE: &str = "groket-hud";

/// Overlay width in logical pixels.
pub const WIDTH: u32 = 780;

/// Overlay height in logical pixels.
pub const HEIGHT: u32 = 560;

/// ``exclusive_zone``: 0 so tiled windows are not pushed aside.
pub const EXCLUSIVE_ZONE: i32 = 0;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn overlay_contract() {
        assert_eq!(NAMESPACE, "groket-hud");
        assert_eq!((WIDTH, HEIGHT), (780, 560));
        assert_eq!(EXCLUSIVE_ZONE, 0);
    }
}
