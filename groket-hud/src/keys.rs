//! Load `keys.toml` overlays for HUD dispatch and shortcut tables.
//!
//! Same path family as the TUI (`GROKET_KEYS` or `~/.groket/keys.toml`).
//! A missing or refused file keeps catalog defaults. Leader sequences are
//! accepted at parse time and refused here (not dispatched in this phase).

use iced::keyboard::{key::Named, Key, Modifiers as KeyMods};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

const KEYS_ENV: &str = "GROKET_KEYS";

/// Catalog row used to validate and merge overlays (mirrors `groket.keys.catalog`).
#[derive(Clone, Copy)]
struct CatalogRow {
    id: &'static str,
    scope: &'static str,
    default: &'static str,
    remappable: bool,
}

// id, scope, default (Textual notation), remappable
const ACTIONS: &[CatalogRow] = &[
    CatalogRow {
        id: "help.toggle",
        scope: "global",
        default: "?",
        remappable: false,
    },
    CatalogRow {
        id: "overlay.hide",
        scope: "global",
        default: "escape",
        remappable: false,
    },
    CatalogRow {
        id: "session.open",
        scope: "home",
        default: "enter",
        remappable: false,
    },
    CatalogRow {
        id: "list.down",
        scope: "home",
        default: "j",
        remappable: true,
    },
    CatalogRow {
        id: "list.up",
        scope: "home",
        default: "k",
        remappable: true,
    },
    CatalogRow {
        id: "search.focus",
        scope: "home",
        default: "slash",
        remappable: true,
    },
    CatalogRow {
        id: "edit.copy",
        scope: "browser",
        default: "y",
        remappable: true,
    },
    CatalogRow {
        id: "edit.copy_chord",
        scope: "browser",
        default: "ctrl+shift+c",
        remappable: true,
    },
    CatalogRow {
        id: "session.follow",
        scope: "home",
        default: "n",
        remappable: true,
    },
    CatalogRow {
        id: "session.done",
        scope: "home",
        default: "e",
        remappable: true,
    },
    CatalogRow {
        id: "pane.notes",
        scope: "browser",
        default: "N",
        remappable: true,
    },
    CatalogRow {
        id: "pane.next",
        scope: "browser",
        default: "tab",
        remappable: false,
    },
    CatalogRow {
        id: "pane.prev",
        scope: "browser",
        default: "shift+tab",
        remappable: false,
    },
    CatalogRow {
        id: "pane.1",
        scope: "browser",
        default: "ctrl+1",
        remappable: true,
    },
    CatalogRow {
        id: "pane.2",
        scope: "browser",
        default: "ctrl+2",
        remappable: true,
    },
    CatalogRow {
        id: "pane.3",
        scope: "browser",
        default: "ctrl+3",
        remappable: true,
    },
    CatalogRow {
        id: "pane.4",
        scope: "browser",
        default: "ctrl+4",
        remappable: true,
    },
    CatalogRow {
        id: "pane.5",
        scope: "browser",
        default: "ctrl+5",
        remappable: true,
    },
    CatalogRow {
        id: "events.next_turn",
        scope: "browser",
        default: "right_square_bracket",
        remappable: true,
    },
    CatalogRow {
        id: "events.all_turns",
        scope: "browser",
        default: "left_square_bracket",
        remappable: true,
    },
    CatalogRow {
        id: "turns.timeline",
        scope: "browser",
        default: "g",
        remappable: true,
    },
    CatalogRow {
        id: "app.refresh",
        scope: "global",
        default: "f5,ctrl+r",
        remappable: true,
    },
    CatalogRow {
        id: "app.jobs",
        scope: "global",
        default: "J",
        remappable: true,
    },
    CatalogRow {
        id: "app.self_test",
        scope: "global",
        default: "ctrl+t",
        remappable: true,
    },
    CatalogRow {
        id: "app.quit",
        scope: "global",
        default: "q",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.prev",
        scope: "browser",
        default: "left_square_bracket",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.next",
        scope: "browser",
        default: "right_square_bracket",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.1",
        scope: "browser",
        default: "1",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.2",
        scope: "browser",
        default: "2",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.3",
        scope: "browser",
        default: "3",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.4",
        scope: "browser",
        default: "4",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.5",
        scope: "browser",
        default: "5",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.6",
        scope: "browser",
        default: "6",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.7",
        scope: "browser",
        default: "7",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.8",
        scope: "browser",
        default: "8",
        remappable: true,
    },
    CatalogRow {
        id: "app.pane.9",
        scope: "browser",
        default: "9",
        remappable: true,
    },
    CatalogRow {
        id: "list.select",
        scope: "home",
        default: "s,space",
        remappable: true,
    },
    CatalogRow {
        id: "list.select_all",
        scope: "home",
        default: "S",
        remappable: true,
    },
    CatalogRow {
        id: "home.runner",
        scope: "home",
        default: "r",
        remappable: true,
    },
    CatalogRow {
        id: "home.configs",
        scope: "home",
        default: "C",
        remappable: true,
    },
    CatalogRow {
        id: "home.personas",
        scope: "home",
        default: "P",
        remappable: true,
    },
    CatalogRow {
        id: "session.rerun",
        scope: "home",
        default: "R",
        remappable: true,
    },
    CatalogRow {
        id: "session.resume",
        scope: "home",
        default: "f",
        remappable: true,
    },
    CatalogRow {
        id: "session.save_config",
        scope: "home",
        default: "ctrl+s",
        remappable: true,
    },
    CatalogRow {
        id: "session.delete",
        scope: "home",
        default: "x,delete",
        remappable: true,
    },
    CatalogRow {
        id: "home.model_filter",
        scope: "home",
        default: "m",
        remappable: true,
    },
    CatalogRow {
        id: "session.analyze",
        scope: "home",
        default: "a",
        remappable: true,
    },
    CatalogRow {
        id: "home.rules",
        scope: "home",
        default: "d",
        remappable: true,
    },
    CatalogRow {
        id: "session.export",
        scope: "home",
        default: "E",
        remappable: true,
    },
    CatalogRow {
        id: "home.host_show",
        scope: "home",
        default: "H",
        remappable: true,
    },
    CatalogRow {
        id: "home.host_hide",
        scope: "home",
        default: "H",
        remappable: true,
    },
    CatalogRow {
        id: "browser.view_filter",
        scope: "browser",
        default: "v",
        remappable: true,
    },
    CatalogRow {
        id: "event.flag",
        scope: "browser",
        default: "f",
        remappable: true,
    },
    CatalogRow {
        id: "session.note_edit",
        scope: "browser",
        default: "O",
        remappable: true,
    },
    CatalogRow {
        id: "browser.clear_filters",
        scope: "browser",
        default: "c",
        remappable: true,
    },
    CatalogRow {
        id: "browser.findings",
        scope: "browser",
        default: "i",
        remappable: true,
    },
    CatalogRow {
        id: "session.share",
        scope: "browser",
        default: "s",
        remappable: true,
    },
    CatalogRow {
        id: "runner.launch",
        scope: "runner",
        default: "ctrl+enter,ctrl+j",
        remappable: true,
    },
    CatalogRow {
        id: "edit.save",
        scope: "modal",
        default: "ctrl+s",
        remappable: true,
    },
    CatalogRow {
        id: "runner.export_task",
        scope: "runner",
        default: "T",
        remappable: true,
    },
    CatalogRow {
        id: "runner.new_persona",
        scope: "runner",
        default: "n",
        remappable: true,
    },
    CatalogRow {
        id: "runner.personas",
        scope: "runner",
        default: "p",
        remappable: true,
    },
    CatalogRow {
        id: "runner.docker",
        scope: "runner",
        default: "d",
        remappable: true,
    },
    CatalogRow {
        id: "configs.launch",
        scope: "configs",
        default: "l",
        remappable: true,
    },
    CatalogRow {
        id: "configs.launch_selected",
        scope: "configs",
        default: "w",
        remappable: true,
    },
    CatalogRow {
        id: "configs.delete",
        scope: "configs",
        default: "x",
        remappable: true,
    },
    CatalogRow {
        id: "configs.new",
        scope: "configs",
        default: "n",
        remappable: true,
    },
    CatalogRow {
        id: "personas.new",
        scope: "personas",
        default: "n",
        remappable: true,
    },
    CatalogRow {
        id: "personas.edit",
        scope: "personas",
        default: "e",
        remappable: true,
    },
    CatalogRow {
        id: "personas.delete",
        scope: "personas",
        default: "x,delete",
        remappable: true,
    },
    CatalogRow {
        id: "rules.toggle",
        scope: "rules",
        default: "t",
        remappable: true,
    },
    CatalogRow {
        id: "rules.enable_all",
        scope: "rules",
        default: "a",
        remappable: true,
    },
    CatalogRow {
        id: "rules.disable_all",
        scope: "rules",
        default: "A",
        remappable: true,
    },
    CatalogRow {
        id: "jobs.close",
        scope: "jobs",
        default: "J",
        remappable: true,
    },
    CatalogRow {
        id: "jobs.open_alt",
        scope: "jobs",
        default: "o",
        remappable: true,
    },
    CatalogRow {
        id: "jobs.clear_logs",
        scope: "jobs",
        default: "c",
        remappable: true,
    },
    CatalogRow {
        id: "modal.done",
        scope: "modal",
        default: "ctrl+s",
        remappable: true,
    },
    CatalogRow {
        id: "modal.submit",
        scope: "modal",
        default: "ctrl+r",
        remappable: true,
    },
    CatalogRow {
        id: "modal.submit_enter",
        scope: "modal",
        default: "enter",
        remappable: false,
    },
    CatalogRow {
        id: "mcp.registry",
        scope: "modal",
        default: "r",
        remappable: true,
    },
    CatalogRow {
        id: "mcp.local",
        scope: "modal",
        default: "l",
        remappable: true,
    },
    CatalogRow {
        id: "confirm.discard",
        scope: "modal",
        default: "enter,y",
        remappable: false,
    },
    CatalogRow {
        id: "confirm.keep",
        scope: "modal",
        default: "n",
        remappable: true,
    },
    CatalogRow {
        id: "help.dismiss",
        scope: "modal",
        default: "enter",
        remappable: false,
    },
];

const KNOWN_SCOPES: &[&str] = &[
    "global",
    "home",
    "browser",
    "runner",
    "personas",
    "persona_edit",
    "configs",
    "rules",
    "jobs",
    "modal",
];

const RESERVED: &[&str] = &["escape", "esc", "enter", "tab", "shift+tab", "?"];

/// Resolved overlay: remapped catalog ids only. Empty = catalog defaults.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct KeyOverlay {
    remaps: HashMap<String, String>,
}

static PROCESS: OnceLock<KeyOverlay> = OnceLock::new();

/// Overlay installed for chrome capture (`j`/`k` while a field is focused).
pub fn process_overlay() -> &'static KeyOverlay {
    PROCESS.get_or_init(KeyOverlay::default)
}

/// Record the process overlay once (HUD boot). Later calls are ignored.
pub fn install_process_overlay(overlay: KeyOverlay) {
    let _ = PROCESS.set(overlay);
}

impl KeyOverlay {
    /// Load `GROKET_KEYS` or `~/.groket/keys.toml`. Missing/refused → defaults.
    pub fn load() -> Self {
        Self::load_from(&resolve_keys_path())
    }

    /// Load one overlay file. Missing or refused keeps defaults.
    pub fn load_from(path: &Path) -> Self {
        if path.is_dir() {
            return Self::default();
        }
        if !path.is_file() {
            return Self::default();
        }
        match std::fs::read_to_string(path) {
            Ok(text) => Self::parse(&text).unwrap_or_default(),
            Err(_) => Self::default(),
        }
    }

    /// Parse overlay text. `None` means refuse (use defaults).
    pub fn parse(text: &str) -> Option<Self> {
        let doc = parse_document(text)?;
        merge_document(&doc)
    }

    /// Resolved Textual chord for *id*, or *default* when unmapped.
    pub fn chord(&self, id: &str, default: &str) -> String {
        self.remaps
            .get(id)
            .cloned()
            .unwrap_or_else(|| normalize_chord(default))
    }

    /// icedtea / help-table spec for *id* (HUD notation).
    pub fn hud_spec(&self, id: &str, default: &str) -> String {
        to_hud_spec(&self.chord(id, default))
    }

    /// True when *key*+*mods* matches the resolved chord for *id*.
    pub fn matches(&self, id: &str, default: &str, key: &Key, mods: KeyMods) -> bool {
        spec_matches(&self.chord(id, default), key, mods)
    }
}

fn expand_user(raw: &str) -> PathBuf {
    if raw == "~" {
        return PathBuf::from(std::env::var_os("HOME").unwrap_or_default());
    }
    if let Some(rest) = raw.strip_prefix("~/") {
        return PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(rest);
    }
    PathBuf::from(raw)
}

fn resolve_keys_path() -> PathBuf {
    if let Ok(raw) = std::env::var(KEYS_ENV) {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            return expand_user(trimmed);
        }
    }
    let home = std::env::var_os("HOME").unwrap_or_default();
    PathBuf::from(home).join(".groket").join("keys.toml")
}

fn action_by_id(id: &str) -> Option<&'static CatalogRow> {
    ACTIONS.iter().find(|row| row.id == id)
}

struct Remap {
    scope: String,
    id: String,
    chord: String,
}

struct Document {
    remaps: Vec<Remap>,
}

fn parse_document(text: &str) -> Option<Document> {
    let mut remaps = Vec::new();
    let mut scope: Option<String> = None;
    for raw in text.lines() {
        let stripped = strip_comment(raw);
        let line = stripped.trim();
        if line.is_empty() {
            continue;
        }
        if let Some(name) = parse_table_header(line) {
            if !KNOWN_SCOPES.contains(&name.as_str()) {
                return None;
            }
            scope = Some(name);
            continue;
        }
        let (key, quoted, val_raw) = parse_assign(line)?;
        if scope.is_none() {
            match key.as_str() {
                "leader" => {
                    let leader = parse_quoted_string(&val_raw)?;
                    validate_leader(&leader)?;
                }
                "leader_timeout_ms" => {
                    parse_positive_timeout(&val_raw)?;
                }
                _ => return None,
            }
            continue;
        }
        if !quoted && key.contains('.') {
            return None;
        }
        let chord = parse_quoted_string(&val_raw)?;
        remaps.push(Remap {
            scope: scope.clone().unwrap(),
            id: key,
            chord,
        });
    }
    Some(Document { remaps })
}

fn strip_comment(line: &str) -> String {
    let mut out = String::new();
    let mut quote: Option<char> = None;
    let mut chars = line.chars().peekable();
    while let Some(c) = chars.next() {
        if quote.is_none() && (c == '"' || c == '\'') {
            quote = Some(c);
            out.push(c);
            continue;
        }
        if quote == Some(c) {
            quote = None;
            out.push(c);
            continue;
        }
        if c == '#' && quote.is_none() {
            break;
        }
        if c == '\\' && quote == Some('"') {
            out.push(c);
            if let Some(n) = chars.next() {
                out.push(n);
            }
            continue;
        }
        out.push(c);
    }
    out
}

fn parse_table_header(line: &str) -> Option<String> {
    let t = line.trim();
    if !t.starts_with('[') || !t.ends_with(']') || t.starts_with("[[") {
        return None;
    }
    let name = t[1..t.len() - 1].trim();
    if name.is_empty() || name.contains('.') || name.contains('[') {
        return None;
    }
    Some(name.to_string())
}

fn parse_assign(line: &str) -> Option<(String, bool, String)> {
    let eq = line.find('=')?;
    let key_raw = line[..eq].trim();
    let val_raw = line[eq + 1..].trim();
    let (key, quoted) = parse_key_token(key_raw)?;
    if key.is_empty() || val_raw.is_empty() {
        return None;
    }
    Some((key, quoted, val_raw.to_string()))
}

fn parse_key_token(raw: &str) -> Option<(String, bool)> {
    if let Some(inner) = parse_quoted_string(raw) {
        return Some((inner, true));
    }
    if raw
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Some((raw.to_string(), false));
    }
    None
}

fn parse_quoted_string(raw: &str) -> Option<String> {
    let t = raw.trim();
    if t.len() < 2 {
        return None;
    }
    let bytes = t.as_bytes();
    let quote = bytes[0];
    if (quote == b'"' || quote == b'\'') && bytes[t.len() - 1] == quote {
        let inner = &t[1..t.len() - 1];
        if inner.is_empty() {
            return None;
        }
        return Some(inner.to_string());
    }
    None
}

fn parse_positive_timeout(raw: &str) -> Option<i64> {
    let t = raw.trim();
    if t.is_empty() || !t.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let n: i64 = t.parse().ok()?;
    if n <= 0 {
        return None;
    }
    Some(n)
}

fn validate_leader(raw: &str) -> Option<()> {
    let s = raw.trim();
    if s.is_empty() || s.contains(',') || chord_has_sequence(s) {
        return None;
    }
    let canon = normalize_chord(s);
    if canon.is_empty() || chord_is_reserved(&canon) {
        return None;
    }
    Some(())
}

fn merge_document(doc: &Document) -> Option<KeyOverlay> {
    let mut chords: HashMap<&str, String> = ACTIONS
        .iter()
        .map(|row| (row.id, normalize_chord(row.default)))
        .collect();
    for remap in &doc.remaps {
        let row = action_by_id(&remap.id)?;
        if row.scope != remap.scope {
            return None;
        }
        if !row.remappable {
            return None;
        }
        let canon = normalize_chord(&remap.chord);
        if canon.is_empty() {
            return None;
        }
        if chord_is_reserved(&canon) {
            return None;
        }
        if chord_has_sequence(&remap.chord) || chord_has_sequence(&canon) {
            return None;
        }
        chords.insert(row.id, canon);
    }
    if has_new_clash(&chords) {
        return None;
    }
    let remaps = ACTIONS
        .iter()
        .filter_map(|row| {
            let got = chords.get(row.id)?;
            let default = normalize_chord(row.default);
            if *got == default {
                None
            } else {
                Some((row.id.to_string(), got.clone()))
            }
        })
        .collect();
    Some(KeyOverlay { remaps })
}

fn has_new_clash(chords: &HashMap<&str, String>) -> bool {
    let default_occ = occupancy(
        &ACTIONS
            .iter()
            .map(|r| (r.id, normalize_chord(r.default)))
            .collect(),
    );
    let resolved_occ = occupancy(chords);
    for (scope, parts) in resolved_occ {
        for (part, ids) in parts {
            if ids.len() < 2 {
                continue;
            }
            let default_ids = default_occ
                .get(scope)
                .and_then(|m| m.get(&part))
                .cloned()
                .unwrap_or_default();
            if ids.iter().any(|id| !default_ids.iter().any(|d| d == id)) {
                return true;
            }
        }
    }
    false
}

fn occupancy(
    chords: &HashMap<&str, String>,
) -> HashMap<&'static str, HashMap<String, Vec<String>>> {
    let mut out: HashMap<&'static str, HashMap<String, Vec<String>>> = HashMap::new();
    for row in ACTIONS {
        let Some(chord) = chords.get(row.id) else {
            continue;
        };
        let bucket = out.entry(row.scope).or_default();
        for part in chord_parts(chord) {
            bucket.entry(part).or_default().push(row.id.to_string());
        }
    }
    out
}

fn chord_parts(chord: &str) -> Vec<String> {
    normalize_chord(chord)
        .split(',')
        .filter(|p| !p.is_empty())
        .map(str::to_string)
        .collect()
}

fn chord_has_sequence(chord: &str) -> bool {
    chord.split(',').any(|part| {
        part.split('+')
            .map(|b| b.trim().to_ascii_lowercase())
            .any(|b| b == "leader")
    })
}

fn chord_is_reserved(chord: &str) -> bool {
    chord_parts(chord)
        .iter()
        .any(|p| RESERVED.contains(&p.as_str()))
}

fn normalize_part(part: &str) -> String {
    let raw = part.trim();
    if raw.is_empty() {
        return String::new();
    }
    if raw.contains('+') {
        let bits: Vec<&str> = raw
            .split('+')
            .map(str::trim)
            .filter(|b| !b.is_empty())
            .collect();
        if bits.is_empty() {
            return String::new();
        }
        let (key, mods) = bits.split_last().unwrap();
        let mut mods_l: Vec<String> = mods.iter().map(|m| m.to_ascii_lowercase()).collect();
        let mut key_s = (*key).to_string();
        if key_s.len() == 1
            && key_s
                .chars()
                .all(|c| c.is_ascii_alphabetic() && c.is_ascii_uppercase())
            && !mods_l.iter().any(|m| m == "shift")
        {
            mods_l.push("shift".into());
            key_s = key_s.to_ascii_lowercase();
        }
        let key_c = alias_key(&key_s);
        mods_l.push(key_c);
        return mods_l.join("+");
    }
    if raw.len() == 1
        && raw
            .chars()
            .all(|c| c.is_ascii_alphabetic() && c.is_ascii_uppercase())
    {
        return format!("shift+{}", raw.to_ascii_lowercase());
    }
    alias_key(raw)
}

fn alias_key(raw: &str) -> String {
    let low = raw.to_ascii_lowercase();
    match (raw, low.as_str()) {
        ("/", _) | (_, "slash") => "slash".into(),
        ("[", _) | (_, "left_square_bracket") => "left_square_bracket".into(),
        ("]", _) | (_, "right_square_bracket") => "right_square_bracket".into(),
        (_, "esc" | "escape") => "escape".into(),
        _ if raw.chars().all(|c| c.is_ascii_alphabetic()) => low,
        _ => low,
    }
}

fn normalize_chord(chord: &str) -> String {
    chord
        .split(',')
        .map(normalize_part)
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join(",")
}

fn to_hud_spec(textual: &str) -> String {
    textual
        .split(',')
        .map(|p| hud_part(p.trim()))
        .filter(|p| !p.is_empty())
        .collect::<Vec<_>>()
        .join(",")
}

fn hud_part(part: &str) -> String {
    let bits: Vec<&str> = part.split('+').collect();
    let Some((key, mods)) = bits.split_last() else {
        return String::new();
    };
    let key_h = match *key {
        "slash" => "/",
        "left_square_bracket" => "[",
        "right_square_bracket" => "]",
        "escape" => "escape",
        other => other,
    };
    if mods.is_empty() {
        return key_h.to_string();
    }
    let mut out = mods.join("+");
    out.push('+');
    out.push_str(key_h);
    out
}

struct ParsedChord {
    ctrl: bool,
    shift: bool,
    alt: bool,
    named: Option<Named>,
    ch: Option<char>,
}

fn parse_spec(part: &str) -> Option<ParsedChord> {
    let norm = normalize_part(part);
    if norm.is_empty() {
        return None;
    }
    let bits: Vec<&str> = norm.split('+').collect();
    let (key, mods) = bits.split_last()?;
    let mut parsed = ParsedChord {
        ctrl: false,
        shift: false,
        alt: false,
        named: None,
        ch: None,
    };
    for m in mods {
        match *m {
            "ctrl" | "control" | "cmd" | "command" | "super" | "logo" => parsed.ctrl = true,
            "shift" => parsed.shift = true,
            "alt" | "option" => parsed.alt = true,
            _ => {}
        }
    }
    match *key {
        "escape" => parsed.named = Some(Named::Escape),
        "enter" => parsed.named = Some(Named::Enter),
        "tab" => parsed.named = Some(Named::Tab),
        "space" => parsed.named = Some(Named::Space),
        "delete" => parsed.named = Some(Named::Delete),
        "slash" => parsed.ch = Some('/'),
        "left_square_bracket" => parsed.ch = Some('['),
        "right_square_bracket" => parsed.ch = Some(']'),
        "?" => parsed.ch = Some('?'),
        other if other.len() == 1 => parsed.ch = other.chars().next(),
        other if other.starts_with('f') && other[1..].chars().all(|c| c.is_ascii_digit()) => {
            parsed.named = match other {
                "f5" => Some(Named::F5),
                _ => None,
            };
        }
        _ => return None,
    }
    Some(parsed)
}

fn spec_matches(spec: &str, key: &Key, mods: KeyMods) -> bool {
    spec.split(',')
        .any(|part| one_matches(part.trim(), key, mods))
}

fn one_matches(part: &str, key: &Key, mods: KeyMods) -> bool {
    let Some(want) = parse_spec(part) else {
        return false;
    };
    let ctrl = mods.control() || mods.command();
    let shift = mods.shift();
    let alt = mods.alt();
    if want.ctrl != ctrl || want.shift != shift || want.alt != alt {
        return false;
    }
    if let Some(named) = want.named {
        return matches!(key, Key::Named(n) if *n == named);
    }
    if let Some(ch) = want.ch {
        let Key::Character(got) = key else {
            return false;
        };
        let g = got.as_str();
        if g.len() != 1 {
            return false;
        }
        let gc = g.chars().next().unwrap();
        if ch.is_ascii_alphabetic() {
            if want.shift {
                return gc.eq_ignore_ascii_case(&ch);
            }
            return gc == ch;
        }
        return gc == ch;
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_file_is_defaults() {
        let overlay = KeyOverlay::load_from(Path::new("/no/such/keys.toml"));
        assert!(overlay.remaps.is_empty());
        assert_eq!(overlay.chord("list.down", "j"), "j");
    }

    #[test]
    fn remap_list_down_requires_moving_follow() {
        let refused = KeyOverlay::parse("[home]\n\"list.down\" = \"n\"\n");
        assert!(refused.is_none());
        let ok = KeyOverlay::parse("[home]\n\"list.down\" = \"n\"\n\"session.follow\" = \"z\"\n")
            .expect("valid swap");
        assert_eq!(ok.chord("list.down", "j"), "n");
        assert_eq!(ok.chord("session.follow", "n"), "z");
        assert_eq!(ok.chord("list.up", "k"), "k");
    }

    #[test]
    fn reserved_and_sequence_refuse() {
        assert!(KeyOverlay::parse("[global]\n\"help.toggle\" = \"x\"\n").is_none());
        assert!(KeyOverlay::parse("[home]\n\"list.down\" = \"escape\"\n").is_none());
        assert!(KeyOverlay::parse("[home]\n\"session.follow\" = \"leader+n\"\n").is_none());
        assert!(KeyOverlay::parse("[nope]\n\"list.down\" = \"h\"\n").is_none());
        assert!(KeyOverlay::parse("[home]\n\"not.an.id\" = \"h\"\n").is_none());
    }

    #[test]
    fn matches_remapped_n_like_default_j() {
        let overlay =
            KeyOverlay::parse("[home]\n\"list.down\" = \"n\"\n\"session.follow\" = \"z\"\n")
                .unwrap();
        assert!(overlay.matches(
            "list.down",
            "j",
            &Key::Character("n".into()),
            KeyMods::empty()
        ));
        assert!(!overlay.matches(
            "list.down",
            "j",
            &Key::Character("j".into()),
            KeyMods::empty()
        ));
        assert!(KeyOverlay::default().matches(
            "list.down",
            "j",
            &Key::Character("j".into()),
            KeyMods::empty()
        ));
    }

    #[test]
    fn hud_spec_converts_textual_aliases() {
        let overlay = KeyOverlay::parse("[home]\n\"search.focus\" = \"slash\"\n").unwrap();
        assert_eq!(overlay.hud_spec("search.focus", "/"), "/");
        let overlay = KeyOverlay::parse("[browser]\n\"events.next_turn\" = \"]\"\n").unwrap();
        assert_eq!(overlay.hud_spec("events.next_turn", "]"), "]");
    }

    #[test]
    fn catalog_ids_cover_hud_shared() {
        for id in [
            "list.down",
            "list.up",
            "session.follow",
            "session.done",
            "edit.copy",
            "pane.notes",
            "search.focus",
        ] {
            assert!(action_by_id(id).is_some(), "{id}");
        }
    }

    #[test]
    fn parse_parity_with_python_fixtures() {
        let cases: &[(&str, bool)] = &[
            (
                include_str!("../../tests/keys/fixtures/overlay_integer_remap.toml"),
                false,
            ),
            (
                include_str!("../../tests/keys/fixtures/overlay_single_quote.toml"),
                true,
            ),
            (
                include_str!("../../tests/keys/fixtures/overlay_reserved_leader.toml"),
                false,
            ),
            (
                include_str!("../../tests/keys/fixtures/overlay_timeout_zero.toml"),
                false,
            ),
            (
                include_str!("../../tests/keys/fixtures/overlay_valid_timeout.toml"),
                true,
            ),
        ];
        for (text, expect_ok) in cases {
            assert_eq!(
                KeyOverlay::parse(text).is_some(),
                *expect_ok,
                "body:\n{text}"
            );
        }
        let quoted = KeyOverlay::parse(include_str!(
            "../../tests/keys/fixtures/overlay_single_quote.toml"
        ))
        .expect("single-quoted remap");
        assert_eq!(quoted.chord("session.follow", "n"), "z");
    }

    #[test]
    fn groket_keys_expands_tilde() {
        let home = std::env::var_os("HOME").unwrap_or_default();
        assert_eq!(
            expand_user("~/.groket/keys.toml"),
            PathBuf::from(&home).join(".groket").join("keys.toml")
        );
        assert_eq!(expand_user("~"), PathBuf::from(&home));
        assert_eq!(
            expand_user("/abs/keys.toml"),
            PathBuf::from("/abs/keys.toml")
        );
    }
}
