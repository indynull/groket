//! Sway IPC placement for the Linux overlay.
//!
//! iced ``move_to`` is a no-op on Wayland. The single Linux path is: when
//! ``SWAYSOCK`` is set, query outputs, pick focused (else first active), then
//! float + ``move to output`` + ``move position center`` for the overlay
//! ``app_id``. Scale and transform stay compositor-owned.

use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::install_desktop::OVERLAY_APP_ID;
use crate::place::{pick_focused_or_active, OutputPick, Rect};

const MAGIC: &[u8] = b"i3-ipc";
const RUN_COMMAND: u32 = 0;
const GET_OUTPUTS: u32 = 3;

/// Place the floating HUD on the focused (or first active) Sway output.
///
/// Returns true when IPC applied a command. False when ``SWAYSOCK`` is unset,
/// the socket is dead, or no usable output exists.
pub fn place_overlay(_win_w: f32, _win_h: f32) -> bool {
    let Some(sock) = sway_sock() else {
        return false;
    };
    let Some(outputs) = ipc_get_outputs(&sock) else {
        return false;
    };
    let Some(ix) = pick_focused_or_active(&outputs) else {
        return false;
    };
    let name = &outputs[ix].name;
    ipc_run(&sock, &place_command(name))
}

/// ``SWAYSOCK`` when set and non-empty.
pub fn sway_sock() -> Option<PathBuf> {
    let raw = std::env::var("SWAYSOCK").ok()?;
    let t = raw.trim();
    if t.is_empty() {
        return None;
    }
    Some(PathBuf::from(t))
}

/// One Sway criteria command: float, move to output, center.
pub fn place_command(output_name: &str) -> String {
    format!(
        "[app_id=\"{OVERLAY_APP_ID}\"] floating enable, \
         move to output \"{}\", move position center",
        escape_quotes(output_name)
    )
}

fn escape_quotes(name: &str) -> String {
    name.replace('\\', "\\\\").replace('"', "\\\"")
}

fn ipc_get_outputs(sock: &Path) -> Option<Vec<OutputPick>> {
    let raw = ipc_roundtrip(sock, GET_OUTPUTS, b"")?;
    parse_outputs(&raw)
}

fn ipc_run(sock: &Path, cmd: &str) -> bool {
    ipc_roundtrip(sock, RUN_COMMAND, cmd.as_bytes()).is_some()
}

fn ipc_roundtrip(sock: &Path, msg_type: u32, payload: &[u8]) -> Option<Vec<u8>> {
    let mut stream = UnixStream::connect(sock).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(400)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));
    let mut header = Vec::with_capacity(MAGIC.len() + 8);
    header.extend_from_slice(MAGIC);
    header.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    header.extend_from_slice(&msg_type.to_le_bytes());
    stream.write_all(&header).ok()?;
    stream.write_all(payload).ok()?;
    stream.flush().ok()?;
    let mut rh = [0u8; 14];
    stream.read_exact(&mut rh).ok()?;
    if &rh[..6] != MAGIC {
        return None;
    }
    let len = u32::from_le_bytes(rh[6..10].try_into().ok()?) as usize;
    if len > 1_000_000 {
        return None;
    }
    let mut body = vec![0u8; len];
    stream.read_exact(&mut body).ok()?;
    Some(body)
}

fn json_f64(v: &serde_json::Value) -> Option<f64> {
    v.as_f64()
        .or_else(|| v.as_i64().map(|n| n as f64))
        .or_else(|| v.as_u64().map(|n| n as f64))
}

fn parse_outputs(raw: &[u8]) -> Option<Vec<OutputPick>> {
    let v: serde_json::Value = serde_json::from_slice(raw).ok()?;
    let arr = v.as_array()?;
    let mut out = Vec::with_capacity(arr.len());
    for item in arr {
        let name = item.get("name")?.as_str()?.to_string();
        let rect = item.get("rect")?;
        let pick = OutputPick {
            name,
            rect: Rect::new(
                json_f64(rect.get("x")?)?,
                json_f64(rect.get("y")?)?,
                json_f64(rect.get("width")?)?,
                json_f64(rect.get("height")?)?,
            ),
            focused: item
                .get("focused")
                .and_then(|x| x.as_bool())
                .unwrap_or(false),
            active: item
                .get("active")
                .and_then(|x| x.as_bool())
                .unwrap_or(false),
        };
        out.push(pick);
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn place_command_names_overlay_app_id() {
        let cmd = place_command("DP-1");
        assert!(cmd.contains(OVERLAY_APP_ID));
        assert!(cmd.contains("move to output \"DP-1\""));
        assert!(cmd.contains("move position center"));
        assert!(cmd.contains("floating enable"));
    }

    #[test]
    fn parse_outputs_fixture() {
        let raw = br#"[
          {"name":"eDP-1","active":true,"focused":false,
           "rect":{"x":0,"y":0,"width":1920,"height":1200}},
          {"name":"DP-1","active":true,"focused":true,
           "rect":{"x":1920,"y":0,"width":2560,"height":1440}}
        ]"#;
        let outs = parse_outputs(raw).unwrap();
        assert_eq!(outs.len(), 2);
        assert_eq!(pick_focused_or_active(&outs), Some(1));
        assert_eq!(outs[1].rect.w, 2560.0);
    }

    #[test]
    fn parse_outputs_rejects_garbage() {
        assert!(parse_outputs(b"not-json").is_none());
        assert!(parse_outputs(b"{}").is_none());
    }
}
