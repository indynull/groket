//! Keyboard shortcut tables for the HUD footer and help cheatsheet.
//!
//! icedtea [`pattern::status_bar`] prints [`ActionTable::footer_hints`];
//! [`pattern::cheatsheet`] lists the full table. One table shape, two views.

use iced::keyboard::Key;
use icedtea::action::{Action, ActionTable};
use icedtea::shortcut::Shortcut;

use crate::app::Message;
use crate::keys::KeyOverlay;
use crate::model::Tab;

/// What the footer should advertise right now.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct KeyScope {
    pub browse: bool,
    pub help_open: bool,
    pub timeline_detail: bool,
    pub awaiting: bool,
    pub tab: Tab,
    pub leader_armed: bool,
}

fn push_mapped(
    table: &mut ActionTable<Message>,
    overlay: &KeyOverlay,
    id: &str,
    title: &str,
    spec: &str,
    msg: Message,
) {
    let resolved = overlay.hud_spec(id, spec);
    if let Some(label) = overlay.sequence_display(id, spec) {
        table.insert(Action::new(id, title, msg).with_shortcut(Shortcut::new(
            iced::keyboard::Modifiers::empty(),
            Key::Character(label.into()),
        )));
        return;
    }
    let parsed = Shortcut::parse(&resolved)
        .or_else(|| Shortcut::parse(spec))
        .expect("static HUD shortcut spec");
    table.insert(Action::new(id, title, msg).with_shortcut(parsed));
}

fn push_leader(table: &mut ActionTable<Message>, overlay: &KeyOverlay) {
    let Some(leader) = overlay.leader() else {
        return;
    };
    let spec = overlay.hud_spec("leader.prefix", leader);
    let Some(parsed) = Shortcut::parse(&spec).or_else(|| Shortcut::parse(leader)) else {
        return;
    };
    table.insert(Action::new("leader.prefix", "Leader", Message::Noop).with_shortcut(parsed));
}

/// Primary keys for the status-bar footer (short, context-filtered).
pub fn footer_table(scope: KeyScope) -> ActionTable<Message> {
    footer_table_for(scope, &KeyOverlay::default())
}

/// Footer table using a resolved overlay (production HUD path).
pub fn footer_table_for(scope: KeyScope, overlay: &KeyOverlay) -> ActionTable<Message> {
    let mut table = ActionTable::new();
    if scope.leader_armed {
        push_leader(&mut table, overlay);
    }
    if scope.help_open {
        push_mapped(
            &mut table,
            overlay,
            "help.toggle",
            "Help",
            "?",
            Message::ToggleHelp,
        );
        push_mapped(
            &mut table,
            overlay,
            "overlay.hide",
            "Close",
            "escape",
            Message::Hide,
        );
        return table;
    }
    push_mapped(
        &mut table,
        overlay,
        "help.toggle",
        "Help",
        "?",
        Message::ToggleHelp,
    );
    let hide = if scope.timeline_detail {
        "Timeline"
    } else {
        "Hide"
    };
    push_mapped(
        &mut table,
        overlay,
        "overlay.hide",
        hide,
        "escape",
        Message::Hide,
    );
    if !scope.browse {
        push_mapped(
            &mut table,
            overlay,
            "session.open",
            "Open",
            "enter",
            Message::ActivateSelected,
        );
        push_mapped(&mut table, overlay, "list.down", "Down", "j", Message::Noop);
        push_mapped(
            &mut table,
            overlay,
            "search.focus",
            "Search",
            "/",
            Message::Noop,
        );
        return table;
    }
    push_mapped(
        &mut table,
        overlay,
        "search.focus",
        "Search",
        "/",
        Message::Noop,
    );
    push_mapped(
        &mut table,
        overlay,
        "pane.next",
        "Panes",
        "tab",
        Message::Noop,
    );
    if scope.timeline_detail {
        push_mapped(&mut table, overlay, "list.down", "Step", "j", Message::Noop);
    } else if matches!(scope.tab, Tab::Turns | Tab::Timeline) {
        push_mapped(&mut table, overlay, "list.down", "Down", "j", Message::Noop);
    }
    push_mapped(
        &mut table,
        overlay,
        "session.open",
        "Next",
        "enter",
        Message::ActivateSelected,
    );
    push_mapped(&mut table, overlay, "edit.copy", "Copy", "y", Message::Yank);
    if scope.awaiting {
        push_mapped(
            &mut table,
            overlay,
            "session.follow",
            "Follow-up",
            "n",
            Message::Noop,
        );
        push_mapped(
            &mut table,
            overlay,
            "session.done",
            "Done",
            "e",
            Message::MarkDone,
        );
    }
    table
}

/// Full shortcut list for the `?` cheatsheet.
pub fn help_table() -> ActionTable<Message> {
    help_table_for(&KeyOverlay::default())
}

/// Cheatsheet using a resolved overlay (production HUD path).
pub fn help_table_for(overlay: &KeyOverlay) -> ActionTable<Message> {
    let mut table = ActionTable::new();
    if overlay.leader().is_some() {
        push_leader(&mut table, overlay);
    }
    push_mapped(
        &mut table,
        overlay,
        "help.toggle",
        "Help",
        "?",
        Message::ToggleHelp,
    );
    push_mapped(
        &mut table,
        overlay,
        "overlay.hide",
        "Hide overlay",
        "escape",
        Message::Hide,
    );
    push_mapped(
        &mut table,
        overlay,
        "session.open",
        "Open or next",
        "enter",
        Message::ActivateSelected,
    );
    push_mapped(
        &mut table,
        overlay,
        "list.down",
        "Move down",
        "j",
        Message::Noop,
    );
    push_mapped(
        &mut table,
        overlay,
        "list.up",
        "Move up",
        "k",
        Message::Noop,
    );
    push_mapped(
        &mut table,
        overlay,
        "pane.next",
        "Next pane",
        "tab",
        Message::Noop,
    );
    push_mapped(
        &mut table,
        overlay,
        "pane.prev",
        "Previous pane",
        "shift+tab",
        Message::Noop,
    );
    for (i, tab) in Tab::ALL.iter().enumerate() {
        let n = i + 1;
        push_mapped(
            &mut table,
            overlay,
            &format!("pane.{n}"),
            tab.label(),
            &format!("ctrl+{n}"),
            Message::SetTab(*tab),
        );
    }
    push_mapped(&mut table, overlay, "edit.copy", "Copy", "y", Message::Yank);
    push_mapped(
        &mut table,
        overlay,
        "edit.copy_chord",
        "Copy",
        "ctrl+shift+c",
        Message::Yank,
    );
    push_mapped(
        &mut table,
        overlay,
        "search.focus",
        "Search",
        "/",
        Message::Noop,
    );
    push_mapped(
        &mut table,
        overlay,
        "session.follow",
        "Follow-up",
        "n",
        Message::Noop,
    );
    push_mapped(
        &mut table,
        overlay,
        "session.done",
        "Done",
        "e",
        Message::MarkDone,
    );
    push_mapped(
        &mut table,
        overlay,
        "pane.notes",
        "Notes",
        "shift+n",
        Message::SetTab(Tab::Notes),
    );
    push_mapped(
        &mut table,
        overlay,
        "events.next_turn",
        "Next turn",
        "]",
        Message::Noop,
    );
    push_mapped(
        &mut table,
        overlay,
        "events.all_turns",
        "All turns",
        "[",
        Message::Noop,
    );
    push_mapped(
        &mut table,
        overlay,
        "turns.timeline",
        "Timeline for turn",
        "g",
        Message::Noop,
    );
    table
}

#[cfg(test)]
mod tests {
    use super::*;

    fn picker() -> KeyScope {
        KeyScope {
            browse: false,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            tab: Tab::Overview,
            leader_armed: false,
        }
    }

    #[test]
    fn help_table_lists_unique_shortcuts() {
        let table = help_table();
        assert!(table.conflicts().is_empty());
        assert!(table.get("help.toggle").is_some());
        assert!(table.get("overlay.hide").is_some());
        assert!(table.get("list.down").is_some());
        assert!(table.get("list.up").is_some());
        assert!(table.get("pane.1").is_some());
        assert!(table.get("pane.5").is_some());
        assert!(table.get("edit.copy").is_some());
        assert!(table.get("search.focus").is_some());
        let hints = table.footer_hints();
        assert!(hints.iter().any(|h| h.starts_with("? ")));
        assert!(hints.iter().any(|h| h.contains("esc")));
    }

    #[test]
    fn footer_table_picker_is_short() {
        let hints = footer_table(picker()).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("? help"));
        assert!(blob.contains("esc hide"));
        assert!(blob.contains("enter open"));
        assert!(blob.contains("j down"));
        assert!(blob.contains("/ search"));
        assert!(!blob.contains("tab "));
        assert!(!blob.contains("y copy"));
    }

    #[test]
    fn footer_table_browse_and_timeline_detail() {
        let browse = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            tab: Tab::Overview,
            leader_armed: false,
        });
        let blob = browse.footer_hints().join("  ·  ");
        assert!(blob.contains("tab panes"));
        assert!(blob.contains("y copy"));
        assert!(!blob.contains("j "));
        assert!(!blob.contains("n "));

        let turns = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            tab: Tab::Turns,
            leader_armed: false,
        });
        assert!(turns.footer_hints().iter().any(|h| h.contains("j down")));

        let detail = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: true,
            awaiting: false,
            tab: Tab::Timeline,
            leader_armed: false,
        });
        let blob = detail.footer_hints().join("  ·  ");
        assert!(blob.contains("esc timeline"));
        assert!(blob.contains("j step"));
    }

    #[test]
    fn footer_table_help_open_is_close_only() {
        let hints = footer_table(KeyScope {
            browse: true,
            help_open: true,
            timeline_detail: true,
            awaiting: true,
            tab: Tab::Timeline,
            leader_armed: false,
        })
        .footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("? help"));
        assert!(blob.contains("esc close"));
        assert_eq!(hints.len(), 2);
    }

    #[test]
    fn footer_table_awaiting_shows_follow_up_and_done() {
        let hints = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: true,
            tab: Tab::Overview,
            leader_armed: false,
        })
        .footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("n follow"));
        assert!(blob.contains("e done"));
        assert!(help_table().get("session.follow").is_some());
        assert!(help_table().get("session.done").is_some());
        assert!(help_table().get("pane.notes").is_some());
    }

    #[test]
    fn armed_leader_shows_in_footer() {
        let overlay = crate::keys::KeyOverlay::parse(
            "leader = \";\"\n[home]\n\"session.follow\" = \"leader+n\"\n\"list.down\" = \"n\"\n",
        )
        .expect("leader overlay");
        let hints = footer_table_for(
            KeyScope {
                browse: false,
                help_open: false,
                timeline_detail: false,
                awaiting: false,
                tab: Tab::Overview,
                leader_armed: true,
            },
            &overlay,
        )
        .footer_hints();
        let blob = hints.join("  ·  ");
        assert!(
            blob.contains(';') || blob.to_lowercase().contains("leader"),
            "{blob}"
        );
        let help = help_table_for(&overlay);
        assert!(help.get("leader.prefix").is_some());
    }

    #[test]
    fn sequence_actions_show_leader_plus_letter() {
        let overlay = crate::keys::KeyOverlay::parse(concat!(
            "leader = \";\"\n",
            "[home]\n",
            "\"list.down\" = \"n\"\n",
            "\"list.up\" = \"e\"\n",
            "\"session.follow\" = \"leader+n\"\n",
            "\"session.done\" = \"leader+e\"\n",
        ))
        .expect("colemak");
        let hints = footer_table_for(
            KeyScope {
                browse: true,
                help_open: false,
                timeline_detail: false,
                awaiting: true,
                tab: Tab::Overview,
                leader_armed: false,
            },
            &overlay,
        )
        .footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("; n"), "{blob}");
        assert!(blob.contains("; e"), "{blob}");
        let help = help_table_for(&overlay);
        let follow = help.get("session.follow").expect("follow");
        assert_eq!(
            follow.shortcut.as_ref().map(ToString::to_string).as_deref(),
            Some("; n")
        );
        let done = help.get("session.done").expect("done");
        assert_eq!(
            done.shortcut.as_ref().map(ToString::to_string).as_deref(),
            Some("; e")
        );
    }

    #[test]
    fn overlay_remap_shows_in_footer_and_help() {
        let overlay = crate::keys::KeyOverlay::parse(
            "[home]\n\"list.down\" = \"n\"\n\"session.follow\" = \"z\"\n",
        )
        .expect("valid overlay");
        let hints = footer_table_for(picker(), &overlay).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("n down"), "{blob}");
        assert!(!blob.contains("j down"), "{blob}");
        let help = help_table_for(&overlay);
        let hints = help.footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("n "), "{blob}");
        assert!(blob.contains("z "), "{blob}");
    }
}
