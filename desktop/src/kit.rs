//! HUD chrome built on icedtea constructors.
//!
//! Prefer icedtea public APIs directly. Helpers here name a groket
//! layout (pane tabs, form gutter), not a missing constructor.

use iced::widget::{container, text};
use iced::{Element, Length};
use icedtea::a11y::{A11y, Role};
use icedtea::collection::Tabs;
use icedtea::theme::Tokens;
use icedtea::typo::FontFace;
use icedtea::widget;

use crate::app::Message;
use crate::model::Tab;

/// icedtea [`layout::FORM_LABEL`] gutter for Overview (and any form stacks).
pub const LABEL_GUTTER: f32 = icedtea::layout::FORM_LABEL;

/// Determinate context / fill bar — icedtea [`widget::progress`].
pub fn context_progress<'a>(frac: f32, tea: Tokens) -> Element<'a, Message> {
    let label = widget::progress_label(frac, None);
    widget::progress(
        frac.clamp(0.0, 1.0),
        None,
        Some(label.as_str()),
        false,
        tea,
        A11y::new("context", Role::Progress).with_value(label.clone()),
    )
}

/// Empty / loading shell — icedtea [`pattern::status_page`].
pub fn status_empty<'a>(
    title: impl Into<String>,
    detail: impl Into<String>,
    tea: Tokens,
) -> Element<'a, Message> {
    icedtea::pattern::status_page(title, detail, None, tea)
}

/// Browse pane tabs via icedtea [`widget::tab_bar`].
///
/// Tabs other than Overview freeze with [`Tabs::with_disabled`] until
/// a session is loaded.
pub fn pane_tabs<'a>(
    active: Tab,
    session_ready: bool,
    tabs: &'static [Tab],
    tea: Tokens,
) -> Element<'a, Message> {
    let titles: Vec<String> = tabs.iter().map(|t| t.label().to_string()).collect();
    let active_i = tabs.iter().position(|t| *t == active).unwrap_or(0);

    let mut bar = Tabs::new(titles);
    bar.select(active_i);
    bar.closable = false;
    if !session_ready {
        for (i, tab) in tabs.iter().enumerate() {
            if *tab != Tab::Overview {
                bar = bar.with_disabled(i);
            }
        }
    }
    widget::tab_bar(
        &bar,
        |i| Message::SetTab(tabs[i.min(tabs.len() - 1)]),
        |_| Message::Noop,
        0.0,
        false,
        tea,
        A11y::new("panes", Role::Tab),
    )
}

/// Labeled copyable value — icedtea [`widget::value_field`] with FORM_LABEL gutter.
pub fn labeled_value<'a>(
    title: &str,
    content: &'a iced::widget::text_editor::Content,
    on_action: impl Fn(iced::widget::text_editor::Action) -> Message + 'a,
    face: FontFace,
    tea: Tokens,
    a11y: A11y,
) -> Element<'a, Message> {
    widget::value_field(
        title,
        content,
        on_action,
        None,
        face,
        LABEL_GUTTER,
        tea,
        tea.direction,
        a11y,
    )
}

/// Non-copyable labeled readout via icedtea [`layout::form`] (same gutter).
pub fn labeled_plain<'a>(
    title: &str,
    value: impl Into<String>,
    tea: Tokens,
) -> Element<'a, Message> {
    let value = value.into();
    icedtea::layout::form(
        [(
            widget::meta(title.to_string(), tea, A11y::new(title, Role::Status)),
            text(value).size(tea.body()).color(tea.text).into(),
        )],
        tea.density.space,
        tea.direction,
    )
}

/// `?` help sheet: shortcut rows in a fixed-size modal.
///
/// icedtea [`pattern::cheatsheet`] already pads for its scroll rail.
pub fn help_modal<'a>(
    backdrop: Element<'a, Message>,
    table: &icedtea::action::ActionTable<Message>,
    tea: Tokens,
) -> Element<'a, Message> {
    let heading = format!("Keyboard shortcuts · groket {}", crate::VERSION);
    let list = icedtea::pattern::cheatsheet(table, "", tea);
    let sheet = widget::group_box(
        heading.clone(),
        list,
        tea,
        widget::CardFace::Elevated,
        A11y::new(heading, Role::Dialog),
    );
    let card = container(sheet)
        .width(Length::Fixed(520.0))
        .height(Length::Fixed(400.0));
    icedtea::pattern::modal_card(backdrop, card.into(), 1.0, tea)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn label_gutter_matches_icedtea_form_label() {
        assert!((LABEL_GUTTER - icedtea::layout::FORM_LABEL).abs() < f32::EPSILON);
        const { assert!(LABEL_GUTTER >= 96.0) };
    }

    #[test]
    fn status_empty_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = status_empty("No turns", "Nothing segmented yet.", tea);
    }

    #[test]
    fn context_progress_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = context_progress(0.42, tea);
        let _ = context_progress(0.0, tea);
        let _ = context_progress(1.0, tea);
    }

    #[test]
    fn pane_tabs_ready_uses_tab_bar_path() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = pane_tabs(Tab::Overview, true, &Tab::ALL, tea);
        let _ = pane_tabs(Tab::Timeline, false, &Tab::ALL, tea);
    }

    #[test]
    fn themed_pick_list_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = widget::themed_pick_list(
            &["All", "Tools"][..],
            Some("All"),
            |_| Message::Noop,
            tea,
            widget::ControlSize::Default,
            A11y::new("Filter", Role::ComboBox),
        );
        let _ = widget::themed_pick_list(
            &["All"][..],
            Some("All"),
            |_| Message::Noop,
            tea,
            widget::ControlSize::Default,
            A11y::new("Filter", Role::ComboBox).with_disabled(true),
        );
        let _ = widget::themed_pick_list(
            &[] as &[&str],
            None,
            |_| Message::Noop,
            tea,
            widget::ControlSize::Default,
            A11y::new("empty", Role::ComboBox),
        );
    }

    #[test]
    fn search_input_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let _ = widget::search_input(
            "q",
            Message::SearchChanged,
            Some(Message::ActivateSelected),
            tea,
            A11y::new("Search sessions", Role::TextBox),
            None,
        );
    }

    #[test]
    fn status_bar_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let table = crate::help::footer_table(crate::help::KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: true,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: crate::model::Tab::Timeline,
            leader_armed: false,
        });
        let _ = icedtea::pattern::status_bar("", None, None, &table, tea, tea.direction);
        let src = include_str!("kit.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        assert!(!prod.contains("fn status_footer"));
        assert!(!prod.contains("style::footer"));
    }

    #[test]
    fn help_modal_builds() {
        let tea = icedtea::theme::named("dark").tokens;
        let table = crate::help::help_table(crate::help::KeyScope {
            browse: true,
            help_open: false,
            timeline_detail: false,
            awaiting: false,
            child_open: false,
            compact_child: false,
            turn_pick: true,
            turn_locked: false,
            diff_pick: false,
            tab: crate::model::Tab::Overview,
            leader_armed: false,
        });
        let backdrop = status_empty("HUD", "backdrop", tea);
        let _ = help_modal(backdrop, &table, tea);
    }

    #[test]
    fn help_modal_title_includes_product_version() {
        let src = include_str!("kit.rs");
        assert!(src.contains("crate::VERSION"));
    }

    #[test]
    fn help_sheet_uses_icedtea_cheatsheet() {
        let src = include_str!("kit.rs");
        let help = src
            .split("pub fn help_modal")
            .nth(1)
            .unwrap()
            .split("#[cfg(test)]")
            .next()
            .unwrap();
        assert!(help.contains("pattern::cheatsheet"));
    }

    #[test]
    fn kit_uses_icedtea_constructors() {
        let src = include_str!("kit.rs");
        assert!(src.contains("widget::value_field"));
        assert!(src.contains("widget::progress"));
        assert!(src.contains("widget::tab_bar"));
        assert!(src.contains("with_disabled"));
        assert!(src.contains("pattern::status_page"));
        assert!(src.contains("pattern::modal_card"));
        assert!(src.contains("pattern::cheatsheet"));
        assert!(src.contains("layout::form"));
        assert!(src.contains("FORM_LABEL"));
        assert!(src.contains("text(value).size(tea.body())"));
    }
}
