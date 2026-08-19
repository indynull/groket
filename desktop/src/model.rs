//! Shared HUD value types.

pub use crate::wire::SessionListItem as SessionRow;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    Overview,
    Turns,
    Timeline,
    Diff,
    Findings,
    Notes,
}

impl Tab {
    pub const ALL: [Tab; 6] = [
        Tab::Overview,
        Tab::Turns,
        Tab::Timeline,
        Tab::Diff,
        Tab::Findings,
        Tab::Notes,
    ];

    /// Subagent session with one operator turn — no Turns pane.
    pub const CHILD: &'static [Tab] = &[
        Tab::Overview,
        Tab::Timeline,
        Tab::Diff,
        Tab::Findings,
        Tab::Notes,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Tab::Overview => "Overview",
            Tab::Turns => "Turns",
            Tab::Timeline => "Timeline",
            Tab::Diff => "Diff",
            Tab::Findings => "Findings",
            Tab::Notes => "Notes",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum KindFilter {
    #[default]
    All,
    Tools,
    User,
    Asst,
    Sess,
    Subagents,
    Background,
    Workflows,
    Errors,
}

impl KindFilter {
    pub const ALL: [KindFilter; 9] = [
        KindFilter::All,
        KindFilter::Tools,
        KindFilter::User,
        KindFilter::Asst,
        KindFilter::Sess,
        KindFilter::Subagents,
        KindFilter::Background,
        KindFilter::Workflows,
        KindFilter::Errors,
    ];

    pub fn label(self) -> &'static str {
        match self {
            KindFilter::All => "All events",
            KindFilter::Tools => "Tools only",
            KindFilter::User => "User messages",
            KindFilter::Asst => "Assistant messages",
            KindFilter::Sess => "Session markers",
            KindFilter::Subagents => "Subagents",
            KindFilter::Background => "Background",
            KindFilter::Workflows => "Workflows",
            KindFilter::Errors => "Errors only",
        }
    }

    pub fn short_label(self) -> &'static str {
        match self {
            KindFilter::All => "All",
            KindFilter::Tools => "Tools",
            KindFilter::User => "User",
            KindFilter::Asst => "Assistant",
            KindFilter::Sess => "Session",
            KindFilter::Subagents => "Subagents",
            KindFilter::Background => "Background",
            KindFilter::Workflows => "Workflows",
            KindFilter::Errors => "Errors",
        }
    }

    pub fn wire_name(self) -> &'static str {
        match self {
            KindFilter::All => "",
            KindFilter::Tools => "tools",
            KindFilter::User => "user",
            KindFilter::Asst => "asst",
            KindFilter::Sess => "sess",
            KindFilter::Subagents => "subagents",
            KindFilter::Background => "background",
            KindFilter::Workflows => "workflows",
            KindFilter::Errors => "errors",
        }
    }
}

impl std::fmt::Display for KindFilter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.label())
    }
}

/// Events pane turn scope (pick list). `turn_index == None` is search-all.
#[derive(Debug, Clone)]
pub struct EventsTurnPick {
    pub turn_index: Option<i64>,
    pub label: String,
}

impl PartialEq for EventsTurnPick {
    fn eq(&self, other: &Self) -> bool {
        self.turn_index == other.turn_index
    }
}

impl Eq for EventsTurnPick {}

impl std::fmt::Display for EventsTurnPick {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.label)
    }
}

/// Diff pane snapshot pick list. `key` matches ``DiffPointRow.key``.
#[derive(Debug, Clone)]
pub struct DiffPointPick {
    pub key: String,
    pub label: String,
}

impl PartialEq for DiffPointPick {
    fn eq(&self, other: &Self) -> bool {
        self.key == other.key
    }
}

impl Eq for DiffPointPick {}

impl std::fmt::Display for DiffPointPick {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.label)
    }
}

/// Prompt vs assistant in the Diff context bar.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DiffContext {
    #[default]
    Prompt,
    Assistant,
}

#[derive(Debug, Clone, Default)]
pub struct NoteDraft {
    pub id: String,
    pub turn_index: String,
    pub event_index: String,
    pub fields: Vec<(String, String)>,
}

impl NoteDraft {
    pub fn field(&self, id: &str) -> &str {
        self.fields
            .iter()
            .find(|(k, _)| k == id)
            .map(|(_, v)| v.as_str())
            .unwrap_or("")
    }

    pub fn set_field(&mut self, id: &str, value: String) {
        if let Some(slot) = self.fields.iter_mut().find(|(k, _)| k == id) {
            slot.1 = value;
        } else {
            self.fields.push((id.to_string(), value));
        }
    }

    pub fn has_content(&self) -> bool {
        self.fields.iter().any(|(_, v)| !v.trim().is_empty())
    }
}

#[derive(Debug, Clone)]
pub struct SchemaField {
    pub id: String,
    pub label: String,
    #[allow(dead_code)]
    pub choices: Vec<String>,
    #[allow(dead_code)]
    pub pick: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tab_and_kind_labels() {
        assert_eq!(Tab::Overview.label(), "Overview");
        assert_eq!(Tab::Turns.label(), "Turns");
        assert_eq!(Tab::Timeline.label(), "Timeline");
        assert_eq!(KindFilter::User.short_label(), "User");
        assert_eq!(KindFilter::Asst.short_label(), "Assistant");
        assert_eq!(KindFilter::Errors.short_label(), "Errors");
        assert_eq!(Tab::Diff.label(), "Diff");
        assert_eq!(Tab::Findings.label(), "Findings");
        assert_eq!(Tab::Notes.label(), "Notes");
        assert_eq!(Tab::ALL.len(), 6);
        assert_eq!(Tab::ALL[3], Tab::Diff);
        assert_eq!(Tab::ALL[4], Tab::Findings);
        assert_eq!(Tab::ALL[5], Tab::Notes);
        let walk =
            include_str!("../../.grok/skills/hud-visual-walkthrough/scripts/hud_walkthrough.py");
        assert!(
            walk.contains("walk.key(\"ctrl+5\")"),
            "Findings is pane 5 (Diff is 4)"
        );
        assert!(walk.contains("walk.key(\"ctrl+6\")"), "Notes is pane 6");
        assert!(walk.contains("bracketleft"), "Timeline All turns is [");
        assert!(!walk.contains("walk.key(\"ctrl+4\")"));
        assert_eq!(KindFilter::Tools.wire_name(), "tools");
        assert_eq!(KindFilter::All.wire_name(), "");
        assert_eq!(KindFilter::All.label(), "All events");
        assert_eq!(KindFilter::Asst.to_string(), "Assistant messages");
        assert_eq!(KindFilter::Sess.label(), "Session markers");
        let mut draft = NoteDraft::default();
        assert!(!draft.has_content());
        draft.set_field("summary", "hi".into());
        assert_eq!(draft.field("summary"), "hi");
        assert!(draft.has_content());
        draft.set_field("summary", "yo".into());
        assert_eq!(draft.field("summary"), "yo");
        assert_eq!(draft.field("missing"), "");
    }
}
