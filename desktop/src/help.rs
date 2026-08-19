//! Keyboard shortcut tables for the HUD footer and help cheatsheet.
//!
//! The footer prints [`ActionTable::footer_hints`] on one line and status
//! on the next. [`pattern::cheatsheet`] lists the full table.

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
    pub child_open: bool,
    pub compact_child: bool,
    /// Events turn pick is shown (more than one turn).
    pub turn_pick: bool,
    /// A specific Timeline turn is selected; h/l/] do not change it.
    pub turn_locked: bool,
    /// Diff snapshot pick (more than one rewind record).
    pub diff_pick: bool,
    pub tab: Tab,
    pub leader_armed: bool,
}

fn scope_tabs(scope: KeyScope) -> &'static [Tab] {
    if scope.compact_child {
        Tab::CHILD
    } else {
        &Tab::ALL
    }
}

fn shortcut_label(overlay: &KeyOverlay, id: &str, spec: &str) -> String {
    if let Some(label) = overlay.sequence_display(id, spec) {
        return label;
    }
    overlay
        .hud_spec(id, spec)
        .split(',')
        .map(str::trim)
        .filter(|part| !part.is_empty())
        .filter_map(Shortcut::parse)
        .map(|s| s.to_string())
        .collect::<Vec<_>>()
        .join(" / ")
}

fn push(
    table: &mut ActionTable<Message>,
    overlay: &KeyOverlay,
    id: &str,
    title: &str,
    spec: &str,
    msg: Message,
) {
    let label = shortcut_label(overlay, id, spec);
    if let Some(seq) = overlay.sequence_display(id, spec) {
        table.insert(
            Action::new(id, title, msg)
                .with_shortcut(Shortcut::new(
                    iced::keyboard::Modifiers::empty(),
                    Key::Character(seq.into()),
                ))
                .with_tooltip(label),
        );
        return;
    }
    let resolved = overlay.hud_spec(id, spec);
    let first = resolved.split(',').next().unwrap_or(resolved.as_str());
    let parsed = Shortcut::parse(first).expect("HUD shortcut spec");
    table.insert(
        Action::new(id, title, msg)
            .with_shortcut(parsed)
            .with_tooltip(label),
    );
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
        push(
            &mut table,
            overlay,
            "help.toggle",
            "Help",
            "?",
            Message::ToggleHelp,
        );
        push(
            &mut table,
            overlay,
            "overlay.hide",
            "Close",
            "escape",
            Message::Hide,
        );
        return table;
    }
    push(
        &mut table,
        overlay,
        "help.toggle",
        "Help",
        "?",
        Message::ToggleHelp,
    );
    let hide = if scope.timeline_detail {
        "Timeline"
    } else if scope.child_open {
        "Parent"
    } else {
        "Hide"
    };
    push(
        &mut table,
        overlay,
        "overlay.hide",
        hide,
        "escape",
        Message::Hide,
    );
    if !scope.browse {
        push(
            &mut table,
            overlay,
            "session.open",
            "Open",
            "enter",
            Message::ActivateSelected,
        );
        push(&mut table, overlay, "list.down", "Down", "j", Message::Noop);
        push(
            &mut table,
            overlay,
            "search.focus",
            "Search",
            "/",
            Message::Noop,
        );
        return table;
    }
    push(
        &mut table,
        overlay,
        "search.focus",
        "Search",
        "/",
        Message::Noop,
    );
    push(
        &mut table,
        overlay,
        "sessions.home",
        "Sessions",
        "u",
        Message::SessionsHome,
    );
    push(
        &mut table,
        overlay,
        "pane.next",
        "Panes",
        "tab",
        Message::Noop,
    );
    if scope.timeline_detail {
        push(&mut table, overlay, "list.down", "Step", "j", Message::Noop);
    } else if matches!(scope.tab, Tab::Turns | Tab::Timeline | Tab::Diff) {
        push(&mut table, overlay, "list.down", "Down", "j", Message::Noop);
    }
    if matches!(scope.tab, Tab::Overview | Tab::Turns | Tab::Timeline) {
        push(
            &mut table,
            overlay,
            "session.open",
            "Next",
            "enter",
            Message::ActivateSelected,
        );
    }
    push(&mut table, overlay, "edit.copy", "Copy", "y", Message::Yank);
    if scope.tab == Tab::Turns && !scope.compact_child {
        push(
            &mut table,
            overlay,
            "turns.timeline",
            "Timeline",
            "g",
            Message::Noop,
        );
    }
    if (scope.tab == Tab::Timeline && scope.turn_pick)
        || (scope.tab == Tab::Diff && scope.diff_pick)
    {
        push(
            &mut table,
            overlay,
            "events.prev_turn",
            "Previous",
            "h,left",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "events.next_turn",
            "Next turn",
            "l,right",
            Message::Noop,
        );
    }
    if scope.tab != Tab::Notes {
        push(
            &mut table,
            overlay,
            "pane.notes",
            "Notes",
            "shift+n",
            Message::SetTab(Tab::Notes),
        );
    }
    if scope.awaiting {
        push(
            &mut table,
            overlay,
            "session.follow",
            "Follow-up",
            "n",
            Message::Noop,
        );
        push(
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

/// Shortcut list for the `?` cheatsheet (keys that apply in *scope*).
pub fn help_table(scope: KeyScope) -> ActionTable<Message> {
    help_table_for(scope, &KeyOverlay::default())
}

/// Cheatsheet using a resolved overlay (production HUD path).
pub fn help_table_for(scope: KeyScope, overlay: &KeyOverlay) -> ActionTable<Message> {
    let mut table = ActionTable::new();
    if overlay.leader().is_some() {
        push_leader(&mut table, overlay);
    }
    push(
        &mut table,
        overlay,
        "help.toggle",
        "Help",
        "?",
        Message::ToggleHelp,
    );
    push(
        &mut table,
        overlay,
        "overlay.hide",
        "Hide overlay",
        "escape",
        Message::Hide,
    );
    if !scope.browse || matches!(scope.tab, Tab::Overview | Tab::Turns | Tab::Timeline) {
        push(
            &mut table,
            overlay,
            "session.open",
            "Open or next",
            "enter",
            Message::ActivateSelected,
        );
    }
    if !scope.browse || matches!(scope.tab, Tab::Turns | Tab::Timeline | Tab::Diff) {
        push(
            &mut table,
            overlay,
            "list.down",
            "Move down",
            "j",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "list.up",
            "Move up",
            "k",
            Message::Noop,
        );
    }
    if scope.browse {
        push(
            &mut table,
            overlay,
            "pane.next",
            "Next pane",
            "tab",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "pane.prev",
            "Previous pane",
            "shift+tab",
            Message::Noop,
        );
    }
    for (i, tab) in scope_tabs(scope).iter().enumerate() {
        let n = i + 1;
        push(
            &mut table,
            overlay,
            &format!("pane.{n}"),
            tab.label(),
            &format!("ctrl+{n}"),
            Message::SetTab(*tab),
        );
    }
    push(&mut table, overlay, "edit.copy", "Copy", "y", Message::Yank);
    push(
        &mut table,
        overlay,
        "edit.copy_chord",
        "Copy",
        "ctrl+shift+c",
        Message::Yank,
    );
    push(
        &mut table,
        overlay,
        "search.focus",
        "Search",
        "/",
        Message::Noop,
    );
    if scope.browse {
        push(
            &mut table,
            overlay,
            "sessions.home",
            "Session list",
            "u",
            Message::SessionsHome,
        );
    }
    if scope.browse && scope.awaiting {
        push(
            &mut table,
            overlay,
            "session.follow",
            "Follow-up",
            "n",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "session.done",
            "Done",
            "e",
            Message::MarkDone,
        );
    }
    if scope.browse && scope.tab != Tab::Notes {
        push(
            &mut table,
            overlay,
            "pane.notes",
            "Notes",
            "shift+n",
            Message::SetTab(Tab::Notes),
        );
    }
    if scope.tab == Tab::Timeline && scope.turn_pick {
        if !scope.turn_locked {
            push(
                &mut table,
                overlay,
                "events.prev_turn",
                "Previous turn",
                "h,left",
                Message::Noop,
            );
            push(
                &mut table,
                overlay,
                "events.next_turn",
                "Next turn",
                "l,right",
                Message::Noop,
            );
            push(
                &mut table,
                overlay,
                "events.scope_next",
                "Next turn",
                "]",
                Message::Noop,
            );
        }
        push(
            &mut table,
            overlay,
            "events.all_turns",
            "All turns",
            "[",
            Message::Noop,
        );
    }
    if scope.tab == Tab::Diff && scope.diff_pick {
        push(
            &mut table,
            overlay,
            "events.prev_turn",
            "Previous snapshot",
            "h,left",
            Message::Noop,
        );
        push(
            &mut table,
            overlay,
            "events.next_turn",
            "Next snapshot",
            "l,right",
            Message::Noop,
        );
    }
    if scope.tab == Tab::Turns && !scope.compact_child {
        push(
            &mut table,
            overlay,
            "turns.timeline",
            "Timeline for turn",
            "g",
            Message::Noop,
        );
    }
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
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
        }
    }

    #[test]
    fn help_table_lists_unique_shortcuts() {
        let table = help_table(picker());
        assert!(table.conflicts().is_empty());
        assert!(table.get("help.toggle").is_some());
        assert!(table.get("overlay.hide").is_some());
        assert!(table.get("list.down").is_some());
        assert!(table.get("list.up").is_some());
        assert!(table.get("pane.1").is_some());
        assert!(table.get("pane.5").is_some());
        assert!(table.get("pane.6").is_some());
        assert!(table.get("session.done").is_none());
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
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
        });
        let blob = browse.footer_hints().join("  ·  ");
        assert!(blob.contains("tab panes"));
        assert!(blob.contains("u sessions"));
        assert!(blob.contains("y copy"));
        assert!(blob.contains("enter next"));
        assert!(
            blob.contains("shift+n notes") || blob.contains("n notes"),
            "{blob}"
        );
        assert!(!blob.contains("j down"), "{blob}");
        assert!(!blob.contains("g timeline"), "{blob}");
        assert!(
            !browse.footer_hints().iter().any(|h| h.starts_with("h ")),
            "{blob}"
        );

        let diff = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: true,
            tab: Tab::Diff,
            leader_armed: false,
        });
        let dblob = diff.footer_hints().join("  ·  ");
        assert!(dblob.contains("/ search"), "{dblob}");
        assert!(dblob.contains("j down"), "{dblob}");
        assert!(
            diff.footer_hints().iter().any(|h| h.starts_with("h ")),
            "{dblob}"
        );

        let turns = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Turns,
            leader_armed: false,
        });
        let tblob = turns.footer_hints().join("  ·  ");
        assert!(tblob.contains("j down"), "{tblob}");
        assert!(tblob.contains("g timeline"), "{tblob}");

        let findings = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Findings,
            leader_armed: false,
        });
        let fblob = findings.footer_hints().join("  ·  ");
        assert!(!fblob.contains("enter next"), "{fblob}");
        assert!(!fblob.contains("j down"), "{fblob}");
        assert!(
            !findings.footer_hints().iter().any(|h| h.starts_with("h ")),
            "{fblob}"
        );

        let detail = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: true,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
        });
        let blob = detail.footer_hints().join("  ·  ");
        assert!(blob.contains("esc timeline"));
        assert!(blob.contains("j step"));

        let scoped = footer_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
        });
        let sblob = scoped.footer_hints().join("  ·  ");
        assert!(
            scoped.footer_hints().iter().any(|h| h.starts_with("h ")),
            "{sblob}"
        );
        assert!(sblob.contains("l ") || sblob.contains("right"), "{sblob}");
    }

    #[test]
    fn footer_table_help_open_is_close_only() {
        let hints = footer_table(KeyScope {
            browse: true,
            help_open: true,
            timeline_detail: true,
            awaiting: true,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
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
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
        })
        .footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("n follow"));
        assert!(blob.contains("e done"));
        let sheet = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: true,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
        });
        assert!(sheet.get("session.follow").is_some());
        assert!(sheet.get("session.done").is_some());
        assert!(sheet.get("pane.notes").is_some());
        assert!(sheet.get("events.next_turn").is_some());
        assert!(sheet.get("events.scope_next").is_some());
        assert!(sheet.get("events.all_turns").is_some());
        assert!(sheet.get("turns.timeline").is_none());
        assert!(sheet.conflicts().is_empty());
        let turns_help = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Turns,
            leader_armed: false,
        });
        assert!(turns_help.get("turns.timeline").is_some());
        assert!(turns_help.get("events.next_turn").is_none());
        assert!(help_table(picker()).get("pane.notes").is_none());
    }

    #[test]
    fn help_table_lists_arrow_keys_for_turn_step() {
        let sheet = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
        });
        let prev = sheet.get("events.prev_turn").expect("prev turn");
        let prev_keys = prev.tooltip.as_deref().unwrap_or("");
        assert!(prev_keys.contains("h"), "{prev_keys}");
        assert!(prev_keys.contains("left"), "{prev_keys}");
        let next = sheet.get("events.next_turn").expect("next turn");
        let next_keys = next.tooltip.as_deref().unwrap_or("");
        assert!(next_keys.contains('l'), "{next_keys}");
        assert!(next_keys.contains("right"), "{next_keys}");
        let locked = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: true,
            diff_pick: false,
            tab: Tab::Timeline,
            leader_armed: false,
        });
        assert!(locked.get("events.next_turn").is_none());
        assert!(locked.get("events.all_turns").is_some());
    }

    #[test]
    fn help_table_lists_keys_the_pane_can_run() {
        let overview = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
        });
        assert!(overview.get("session.open").is_some());
        assert!(overview.get("list.down").is_none());
        assert!(overview.get("pane.next").is_some());

        let findings = help_table(KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Findings,
            leader_armed: false,
        });
        assert!(findings.get("session.open").is_none());
        assert!(findings.get("list.down").is_none());
        assert!(findings.get("list.up").is_none());
        assert!(findings.get("events.next_turn").is_none());
        assert!(findings.get("pane.next").is_some());

        let sheet = help_table(picker());
        assert!(sheet.get("session.open").is_some());
        assert!(sheet.get("list.down").is_some());
        assert!(sheet.get("pane.next").is_none());
        let awaiting_picker = help_table(KeyScope {
            browse: false,
            help_open: false,
            timeline_detail: false,
            awaiting: true,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
        });
        assert!(awaiting_picker.get("session.follow").is_none());
        assert!(awaiting_picker.get("session.done").is_none());
    }

    #[test]
    fn armed_leader_shows_in_footer() {
        let overlay = crate::keys::KeyOverlay::parse(
            "leader = \";\"\n[home]\n\"session.follow\" = \"leader+n\"\n\"list.down\" = \"n\"\n",
        )
        .expect("leader overlay");
        let scope = KeyScope {
            browse: false,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: true,
        };
        let hints = footer_table_for(scope, &overlay).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(
            blob.contains(';') || blob.to_lowercase().contains("leader"),
            "{blob}"
        );
        let help = help_table_for(scope, &overlay);
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
        let scope = KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: true,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
        };
        let hints = footer_table_for(scope, &overlay).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("; n"), "{blob}");
        assert!(blob.contains("; e"), "{blob}");
        let help = help_table_for(scope, &overlay);
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
        let awaiting = KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: true,
            child_open: false,
            compact_child: false,
            turn_pick: false,
            turn_locked: false,
            diff_pick: false,
            tab: Tab::Overview,
            leader_armed: false,
        };
        let help = help_table_for(awaiting, &overlay);
        let hints = help.footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("n "), "{blob}");
        assert!(blob.contains("z "), "{blob}");
    }
}
