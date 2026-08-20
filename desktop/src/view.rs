//! Palette layout.

use iced::widget::{column, container, image, mouse_area, responsive, row, stack, text, Space};
use iced::{Alignment, Color, Element, Length, Padding};
use icedtea::a11y::{A11y, Role};
use icedtea::toast::ToastKind;
use icedtea::variant::Variant;

use crate::app::{ExtractKey, Hud, Message};
use crate::brand;
use crate::format::{
    body_paint_for, capped_display, display_tool_output, event_brand_role, event_is_monitor,
    fmt_duration, format_note_time, format_tool_display, human_event_type_label, image_result_path,
    is_chat_message, is_tool_identity, job_command, job_description, job_event_id, job_event_label,
    job_exit_code, job_inspect_blocks, job_inspect_log, job_list_preview, job_output_path,
    job_status, list_event_detail, list_status_label, looks_like_markdown, note_fields_view,
    origin_label, overview_fields, overview_subagent_rows, overview_task_rows,
    overview_workflow_rows, path_hint_from_raw, sanitize_console_text, schedule_inspect_blocks,
    schedule_last_fire, session_duration_chip, status_tone, subagent_inspect_blocks,
    subagent_list_preview, syntax_for_tool_field, syntax_for_tool_output, timeline_body_text,
    timeline_count_caption, timeline_query_hit, tool_brand_role, tool_fields_from_raw,
    workflow_for_event, workflow_name_from_raw, BodyPaint, BrandRole, ToolField,
};
use crate::kit;
use crate::live::{
    context_fraction, finding_severity_rank, finding_severity_title, CardMark,
    OVERVIEW_LIST_OVERSCAN, STATS_ROW_H, TIMELINE_OVERSCAN, TURNS_OVERSCAN,
};
use crate::model::{DiffContext, KindFilter, OverviewSection, Tab};
use crate::motion::PageLayer;
use crate::typo;
use crate::wire::{FindingRow, NoteRow, TimelineEvent, TurnRow};

fn rule(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    icedtea::widget::rule_h(tea, A11y::new("rule", Role::Separator))
}

fn empty_sessions(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty("No sessions", "Is groket serve running?", tea)
}

fn no_session_matches(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty(
        "No matches",
        "Try another query, or clear search for recent sessions.",
        tea,
    )
}

fn busy_pane() -> Element<'static, Message> {
    Space::new().width(Length::Fill).height(Length::Fill).into()
}

fn select_session(tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    kit::status_empty(
        "Search for a session",
        "Type above, then Enter or click a match. Search again to switch.",
        tea,
    )
}

fn awaiting_banner(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    column![
        icedtea::widget::banner(
            "Session is awaiting a follow-up",
            Some(("Done".into(), Message::MarkDone)),
            tea,
            A11y::new("awaiting", Role::Status),
        ),
        container(icedtea::widget::themed_text_input(
            "Follow-up prompt",
            hud.follow_draft(),
            Message::FollowDraft,
            Some(Message::SendFollow),
            icedtea::widget::FieldOpts::NONE,
            tea,
            A11y::new("follow-up", Role::TextBox),
            Some(hud.follow_id()),
        ))
        .width(Length::Fill),
        icedtea::widget::themed_button(
            "Send follow-up",
            Some(Message::SendFollow),
            tea,
            Variant::Primary,
            icedtea::icon::Icons::NONE,
            A11y::button("Send follow-up"),
        ),
    ]
    .spacing(tea.density.gap())
    .into()
}

#[allow(dead_code)] // kept for footer status chrome; exercised in tests
fn status_copy(text: &str, err: bool, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let a11y = A11y::new(text.to_string(), Role::Status);
    if err {
        icedtea::widget::info_bar(ToastKind::Danger, text.to_string(), tea, a11y)
    } else {
        icedtea::widget::meta(text.to_string(), tea, a11y)
    }
}

fn tone_variant(tone: &str) -> Variant {
    match tone {
        "complete" => Variant::Success,
        "running" => Variant::Warning,
        "cancelled" => Variant::Danger,
        _ => Variant::Quiet,
    }
}

fn brand_variant(role: BrandRole) -> Variant {
    match role {
        BrandRole::Complete => Variant::Success,
        BrandRole::Running => Variant::Warning,
        BrandRole::Failed => Variant::Danger,
        BrandRole::Cream => Variant::Primary,
        BrandRole::Cancelled => Variant::Quiet,
    }
}

/// Small icedtea badge for a type or tool name (same face as session status).
fn label_badge(
    label: impl Into<String>,
    role: BrandRole,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    paint_badge(label.into(), brand_variant(role), tea)
}

/// Session / turn / severity status — same readable badge face everywhere.
fn status_chip(
    label: impl Into<String>,
    tone: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    paint_badge(label.into(), tone_variant(tone), tea)
}

fn paint_badge(
    label: String,
    variant: Variant,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let a11y = A11y::new(label.clone(), Role::Status);
    icedtea::widget::badge(
        label,
        None,
        tea,
        variant,
        icedtea::widget::BadgeSize::Small,
        a11y,
    )
}

fn severity_tone(sev: &str) -> &'static str {
    match finding_severity_rank(sev) {
        0 => "cancelled",
        1 => "running",
        2 => "complete",
        _ => "",
    }
}

/// Status plus identity chips — Overview, Recent cards, and the browse bar.
fn session_state_row(
    status: &str,
    model: &str,
    origin: &str,
    duration: &str,
    subagent: bool,
    tea: icedtea::theme::Tokens,
    context: &str,
) -> Element<'static, Message> {
    let status_label = list_status_label(status, "");
    let mut chips = row![status_chip(
        status_label.clone(),
        status_tone(&status_label),
        tea,
    )]
    .spacing(8)
    .align_y(Alignment::Center);
    if subagent {
        chips = chips.push(status_chip(String::from("subagent"), "", tea));
    }
    if !model.trim().is_empty() {
        chips = chips.push(status_chip(model.trim().to_string(), "", tea));
    }
    let origin = origin_label(origin);
    if origin != "—" {
        chips = chips.push(status_chip(origin.to_string(), "", tea));
    }
    if !duration.trim().is_empty() && duration != "—" {
        chips = chips.push(status_chip(duration.trim().to_string(), "", tea));
    }
    if !context.trim().is_empty() {
        chips = chips.push(status_chip(context.trim().to_string(), "", tea));
    }
    chips.into()
}

fn session_state_from_row(
    row: &crate::model::SessionRow,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let taken = session_duration_chip(row.duration_seconds, "");
    session_state_row(
        &row.status_label(),
        &row.model,
        &row.origin,
        &taken,
        false,
        tea,
        row.context_usage_compact.trim(),
    )
}

fn session_state_from_meta(
    meta: &crate::wire::SessionMeta,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let taken = session_duration_chip(meta.duration_seconds, &meta.duration);
    session_state_row(
        &meta.status_label(),
        &meta.model,
        &meta.origin,
        &taken,
        meta.is_subagent(),
        tea,
        "",
    )
}

fn tool_image(path: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let a11y = A11y::new(path.to_string(), Role::Image);
    if std::path::Path::new(path).is_file() {
        icedtea::widget::image_slot(
            icedtea::widget::ImageSlot::Ready {
                handle: iced::widget::image::Handle::from_path(path),
                fit: iced::ContentFit::Contain,
            },
            Length::Fill,
            Length::Fixed(240.0),
            tea,
            a11y,
        )
    } else {
        icedtea::widget::image_slot(
            icedtea::widget::ImageSlot::Error(path.to_string()),
            Length::Fill,
            Length::Fixed(80.0),
            tea,
            a11y,
        )
    }
}

fn select_bound<'a>(
    hud: &'a Hud,
    id: String,
    fallback: &str,
    tea: icedtea::theme::Tokens,
    face: icedtea::typo::FontFace,
) -> Element<'a, Message> {
    let Some(buf) = hud.field(&id) else {
        return text(fallback.to_string())
            .size(tea.body())
            .font(face.font())
            .into();
    };
    let a11y_id = id.clone();
    icedtea::widget::selectable(
        buf,
        move |action| Message::Select {
            id: id.clone(),
            action,
        },
        tea,
        face,
        A11y::new(a11y_id, Role::TextBox),
    )
}

fn markdown_bound<'a>(
    hud: &'a Hud,
    id: String,
    fallback: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    let Some(doc) = hud.markdown(&id) else {
        return select_bound(hud, id, fallback, tea, icedtea::typo::FontFace::Ui);
    };
    let Some(slot) = hud.markdown_slot(&id) else {
        return select_bound(hud, id, fallback, tea, icedtea::typo::FontFace::Ui);
    };
    let span = hud.markdown_span(&id);
    icedtea::widget::markdown_view(
        &doc.items,
        span,
        move |ev| Message::MdPointer { slot, ev },
        tea,
        |_| Message::Noop,
        A11y::new(id, Role::Group),
    )
}

fn code_inset<'a>(
    hud: &'a Hud,
    id: &str,
    fallback: &str,
    syntax: &str,
    wrap: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    // Prefer the selectable bind buffer; fall back to *fallback* so a missing
    // bind (e.g. first paint before extract) does not paint an empty Code pane.
    let Some(buf) = hud.field(id) else {
        if fallback.is_empty() {
            return text(String::new()).size(tea.meta()).font(typo::MONO).into();
        }
        return text(fallback.to_string())
            .size(tea.meta())
            .font(typo::MONO)
            .into();
    };
    let id = id.to_string();
    // Real iced highlighter (syntect) — not plain mono ``code_block``.
    let lang = if syntax.is_empty() { "txt" } else { syntax };
    icedtea::widget::highlighted_code(
        buf,
        lang,
        move |action| Message::Select {
            id: id.clone(),
            action,
        },
        tea,
        hud.theme_name(),
        Length::Shrink,
        wrap,
        A11y::new("code", Role::TextBox),
    )
}

pub fn layout(hud: &Hud) -> Element<'_, Message> {
    let tok = hud.tokens();
    let tea = hud.tokens();
    let mut search = row![
        icedtea::widget::tooltip_wrap(
            mouse_area(
                image(brand::chrome_handle(crate::theme::canvas_is_dark(tok)))
                    .width(brand::chrome_width())
                    .height(brand::chrome_height()),
            )
            .on_press(Message::SessionsHome)
            .into(),
            "Session list",
            icedtea::widget::TooltipAnchor::Follow,
            tea,
            A11y::button("Session list"),
        ),
        icedtea::widget::search_input(
            hud.query(),
            Message::SearchChanged,
            Some(Message::ActivateSelected),
            tea,
            A11y::new("Search sessions", Role::TextBox),
            Some(hud.search_id()),
        ),
    ]
    .spacing(12)
    .align_y(Alignment::Center);
    if !hud.window_mode() {
        search = search.push(pop_out_control(tok, tea));
    }
    let search = search.padding(Padding::from([tea.density.gap(), tea.density.inset()]));

    // Spotlight: search → pick → full-width browse. Type again to switch.
    let body: Element<'_, Message> = {
        let inner = if hud.browse_mode() {
            detail_pane(hud)
        } else {
            session_picker(hud)
        };
        if hud.page_layer() == PageLayer::Browse && hud.page_moving() {
            icedtea::motion::overlay(
                inner,
                hud.page_progress(),
                hud.page_slide(),
                tea,
                A11y::new("browse", Role::Group),
            )
        } else {
            inner
        }
    };

    let foot = footer(hud, tea);

    let mut stack = column![search, rule(tea), body, rule(tea), foot];
    for t in hud.toasts().iter() {
        let id = t.id;
        stack = stack.push(icedtea::widget::toast_view(
            t,
            Message::ToastDismiss(id),
            tea,
            A11y::new(t.text.clone(), Role::Status),
        ));
    }
    let shell = container(stack)
        .width(Length::Fill)
        .height(Length::Fill)
        .padding(1)
        .style(move |_| icedtea::style::shell(tea));
    let busy = icedtea::widget::busy_overlay(
        shell.into(),
        hud.page_busy(),
        hud.spin_phase(),
        tea,
        A11y::new("Loading", Role::Progress),
    );
    let chrome: Element<'_, Message> = if hud.look_open() {
        icedtea::pattern::drawer(true, look_pane(hud, tea), busy, 1.0, tea)
    } else {
        busy
    };
    // Always stack the shell so opening the context menu does not remount
    // selectable editors (iced only paints a selection while they stay focused).
    let mut layers = stack![chrome];
    if let Some(origin) = hud.context_origin() {
        layers = layers.push(icedtea::pattern::context_menu(
            hud.context_actions(),
            origin,
            hud.window_size(),
            Message::ContextDismiss,
            1.0,
            tea,
        ));
    }
    let scene = layers.into();
    if hud.help_open() {
        return fade_palette(
            kit::help_modal(
                scene,
                &crate::help::help_table_for(hud.key_scope(), hud.key_overlay()),
                tea,
            ),
            hud,
            tea,
        );
    }
    fade_palette(scene, hud, tea)
}

fn look_pane(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let look = hud.look();
    let pick = |label: &'static str,
                options: &[&str],
                current: &'static str,
                on: fn(String) -> Message| {
        let opts: Vec<String> = options.iter().map(|s| (*s).to_string()).collect();
        let el: Element<'_, Message> = column![
            icedtea::widget::meta(label, tea, A11y::new(label, Role::Status)),
            icedtea::widget::themed_pick_list(
                opts,
                Some(current.to_string()),
                on,
                tea,
                icedtea::widget::ControlSize::Default,
                A11y::new(label, Role::ComboBox),
            ),
        ]
        .spacing(4)
        .into();
        el
    };
    column![
        icedtea::widget::meta("Look", tea, A11y::new("Look", Role::Header)),
        pick(
            "Density",
            &["Compact", "Default", "Comfortable"],
            look.density_label(),
            Message::LookDensity,
        ),
        pick(
            "Type",
            &["90%", "100%", "110%", "125%"],
            look.scale_label(),
            Message::LookScale,
        ),
        pick(
            "Shape",
            &["Desktop", "Tight", "Soft", "Pill", "Material"],
            look.shape_label(),
            Message::LookShape,
        ),
        pick(
            "Elevation",
            &["Desktop", "Flat"],
            look.elevation_label(),
            Message::LookElevation,
        ),
    ]
    .spacing(10)
    .padding(12)
    .into()
}

fn fade_palette<'a>(
    child: Element<'a, Message>,
    hud: &Hud,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    // OverlayLayer still does not implement Widget::overlay, so pick lists
    // never open while this wrapper is mounted. Tokens::fade already paints
    // the show/hide. Keep the layer only while the fade is running.
    if !hud.overlay_moving() {
        return child;
    }
    icedtea::motion::overlay(
        child,
        hud.overlay_progress(),
        icedtea::motion::Slide::Up,
        tea,
        A11y::new("palette", Role::Group),
    )
}

fn page_body<'a>(
    child: Element<'a, Message>,
    hud: &Hud,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    if hud.page_layer() != PageLayer::Pane || !hud.page_moving() {
        return container(child)
            .width(Length::Fill)
            .height(Length::Fill)
            .into();
    }
    container(icedtea::motion::overlay(
        child,
        hud.page_progress(),
        hud.page_slide(),
        tea,
        A11y::new("page", Role::Group),
    ))
    .width(Length::Fill)
    .height(Length::Fill)
    .clip(true)
    .into()
}

/// Full-width session matches (Spotlight results). No permanent left rail.
fn session_picker(hud: &Hud) -> Element<'_, Message> {
    responsive(move |size| session_picker_at(hud, size.height.max(1.0))).into()
}

fn session_picker_at(hud: &Hud, viewport: f32) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let idle = hud.query().trim().is_empty();
    if hud.sessions().is_empty() {
        if idle {
            // Catalog empty vs still loading — same honest empty; no full dump.
            return if hud.catalog_busy() {
                busy_pane()
            } else {
                empty_sessions(tea)
            };
        }
        return no_session_matches(tea);
    }
    let mut window = hud.list_window();
    window.viewport = viewport.max(1.0);
    let gap = tea.density.gap();
    let inset = tea.density.inset();
    let rows = hud.sessions();
    let selected = hud.list_selection().primary();
    let list = icedtea::widget::virtual_column(
        hud.session_heights(),
        window,
        1,
        selected,
        Message::ListScroll,
        Some(hud.list_scroll_id()),
        tea,
        move |i| {
            let Some(row) = rows.get(i) else {
                return Space::new().height(0).into();
            };
            session_list_card(row, i, selected == Some(i), tea)
        },
        A11y::new("Sessions", Role::List),
    );
    if idle {
        return column![
            icedtea::widget::meta("Recent", tea, A11y::new("Recent", Role::Header),),
            list,
        ]
        .spacing(gap)
        .padding(Padding::from([gap, inset]))
        .height(Length::Fill)
        .into();
    }
    container(list)
        .padding(Padding::from([gap, inset]))
        .height(Length::Fill)
        .into()
}

fn session_list_card(
    row: &crate::model::SessionRow,
    index: usize,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let title = text(row.display_title().to_string())
        .size(tea.body())
        .font(if selected { typo::UI_BOLD } else { typo::UI })
        .color(tea.text)
        .width(Length::Fill);
    let body = column![title, session_state_from_row(row, tea)]
        .spacing(4)
        .width(Length::Fill);
    column![
        mouse_area(
            container(body)
                .padding(tea.density.inset())
                .width(Length::Fill)
                .style(move |_| icedtea::style::card(tea, selected)),
        )
        .on_press(Message::SelectSession(index)),
        Space::new().height(crate::live::LIST_CARD_GAP),
    ]
    .into()
}

fn detail_pane(hud: &Hud) -> Element<'_, Message> {
    let session_ready = hud.overview().is_some() || !hud.overview_pending().is_empty();
    let tea = hud.tokens();
    let tabs = container(kit::pane_tabs(
        hud.tab(),
        session_ready,
        hud.visible_tabs(),
        tea,
    ))
    .padding(Padding::from([tea.density.gap(), tea.density.inset()]));

    let mut stack = column![].spacing(0).height(Length::Fill);
    if let Some(bar) = browse_session_bar(hud, tea) {
        stack = stack.push(bar);
    }
    stack = stack.push(tabs);
    // List filters stay off while reading a full-pane event.
    if hud.tab() == Tab::Overview && hud.overview().is_some() {
        stack = stack.push(
            container(kit::overview_section_tabs(hud.overview_section(), tea)).padding(Padding {
                top: 0.0,
                right: tea.density.inset(),
                bottom: tea.density.gap(),
                left: tea.density.inset(),
            }),
        );
    }
    if hud.tab() == Tab::Timeline && hud.overview().is_some() && hud.timeline_open().is_none() {
        stack = stack.push(timeline_filter(hud));
    }
    let body: Element<'_, Message> = if hud.overview().is_none() {
        if !hud.overview_pending().is_empty() {
            busy_pane()
        } else {
            select_session(hud.body_tokens())
        }
    } else {
        match hud.tab() {
            Tab::Overview => overview_tab(hud),
            Tab::Turns | Tab::Timeline | Tab::Diff => column![].into(),
            Tab::Findings => findings_tab(hud),
            Tab::Notes => notes_tab(hud),
        }
    };
    if hud.tab() == Tab::Timeline && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(timeline_tab(hud))
                .padding([tea.density.gap(), tea.density.inset()])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Turns && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(turns_tab(hud))
                .padding([tea.density.gap(), tea.density.inset()])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Diff && hud.overview().is_some() {
        stack = stack.push(page_body(
            container(diff_tab(hud))
                .padding(Padding {
                    top: 0.0,
                    right: tea.density.inset(),
                    bottom: tea.density.gap(),
                    left: tea.density.inset(),
                })
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else if hud.tab() == Tab::Overview
        && hud.overview().is_some()
        && overview_virtual_body(hud.overview_section())
    {
        stack = stack.push(page_body(
            container(overview_tab(hud))
                .padding([tea.density.gap(), tea.density.inset()])
                .width(Length::Fill)
                .height(Length::Fill)
                .into(),
            hud,
            tea,
        ));
    } else {
        stack = stack.push(page_body(
            icedtea::widget::themed_scroll(
                container(body)
                    .padding(tea.density.sheet())
                    .width(Length::Fill)
                    .into(),
                tea,
                A11y::new("Detail", Role::Group),
                false,
                None,
                None::<fn(f32) -> Message>,
            ),
            hud,
            tea,
        ));
    }
    container(stack)
        .width(Length::Fill)
        .height(Length::Fill)
        .into()
}

/// Session identity under the search bar while browsing (no left rail).
fn browse_session_bar<'a>(
    hud: &'a Hud,
    tea: icedtea::theme::Tokens,
) -> Option<Element<'a, Message>> {
    let title = if let Some(o) = hud.overview() {
        let t = o.meta.title.trim();
        if t.is_empty() {
            let l = o.meta.label.trim();
            if l.is_empty() {
                hud.overview_sid().to_string()
            } else {
                l.to_string()
            }
        } else {
            t.to_string()
        }
    } else if !hud.overview_pending().is_empty() {
        hud.overview_pending().to_string()
    } else {
        return None;
    };
    let status = if let Some(o) = hud.overview() {
        let s = o.meta.status_label();
        if s.is_empty() {
            String::new()
        } else {
            s
        }
    } else {
        String::new()
    };
    let mut row = row![text(title)
        .size(tea.body())
        .font(typo::UI_BOLD)
        .color(tea.text),]
    .spacing(10)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    if let Some(o) = hud.overview() {
        row = row.push(session_state_from_meta(&o.meta, tea));
    } else if !status.is_empty() {
        row = row.push(session_state_row(&status, "", "", "", false, tea, ""));
    }
    row = row.push(Space::new().width(Length::Fill));
    row = row.push(icedtea::widget::meta(
        "Search again to switch",
        tea,
        A11y::new("Search again to switch", Role::Status),
    ));
    Some(
        container(row)
            .padding(Padding::from([tea.density.gap(), tea.density.inset()]))
            .width(Length::Fill)
            .into(),
    )
}

fn timeline_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    // Two rows: picks + optional range; full-width search below so it never
    // shares width with Turn/Filter (one-row bar clipped or overlapped the field).
    let mut picks = row![].spacing(tea.density.gap()).align_y(Alignment::Center);
    if !hud.hide_events_turn_pick() {
        picks = picks.push(icedtea::widget::meta(
            "Turn",
            tea,
            A11y::new("Turn", Role::Header),
        ));
        picks = picks.push(icedtea::widget::themed_pick_list(
            hud.events_turn_options(),
            Some(hud.events_turn_selected()),
            Message::EventsTurnPicked,
            tea,
            icedtea::widget::ControlSize::Default,
            A11y::new("Turn", Role::ComboBox),
        ));
    }
    picks = picks.push(icedtea::widget::meta(
        "Filter",
        tea,
        A11y::new("Filter", Role::Header),
    ));
    picks = picks.push(icedtea::widget::themed_pick_list(
        &KindFilter::ALL[..],
        Some(hud.timeline_kind()),
        Message::TimelineKind,
        tea,
        icedtea::widget::ControlSize::Default,
        A11y::new("Filter", Role::ComboBox),
    ));
    if hud.show_timeline_tail() {
        picks = picks.push(icedtea::widget::themed_switch(
            "Tail",
            hud.timeline_follow_tail(),
            Message::TimelineTail,
            tea,
            A11y::new("Tail", Role::Switch).with_checked(hud.timeline_follow_tail()),
        ));
    }
    picks = picks
        .push(Space::new().width(Length::Fill))
        .width(Length::Fill);
    if let Some(cap) = timeline_count_caption(&hud.timeline_meta()) {
        picks = picks.push(icedtea::widget::meta(
            cap.to_string(),
            tea,
            A11y::new(cap.to_string(), Role::Status),
        ));
    }
    let search = container(icedtea::widget::search_input(
        hud.timeline_query_draft(),
        Message::TimelineQuery,
        None,
        tea,
        A11y::new("Search events…", Role::TextBox),
        Some(hud.tl_search_id()),
    ))
    .width(Length::Fill);
    column![picks, search]
        .spacing(tea.density.gap())
        .width(Length::Fill)
        .padding(Padding::from([tea.density.gap(), tea.density.inset()]))
        .into()
}

fn overview_virtual_body(section: OverviewSection) -> bool {
    matches!(
        section,
        OverviewSection::Tasks
            | OverviewSection::Workflows
            | OverviewSection::Subagents
            | OverviewSection::Stats
    )
}

fn overview_tab(hud: &Hud) -> Element<'_, Message> {
    match hud.overview_section() {
        OverviewSection::Session => overview_session(hud),
        OverviewSection::Tasks => overview_tasks(hud),
        OverviewSection::Workflows => overview_workflows(hud),
        OverviewSection::Subagents => overview_subagents(hud),
        OverviewSection::Stats => overview_stats(hud),
    }
}

fn overview_session(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let o = hud.overview().unwrap();
    let meta = &o.meta;
    let mut title = meta.title.clone();
    if title.is_empty() {
        title = hud.overview_sid().to_string();
    }
    if meta.is_subagent() && !title.to_ascii_lowercase().starts_with("subagent") {
        title = format!("Subagent · {title}");
    }
    let mut summary = o.summary.clone();
    if summary.is_empty() {
        summary = meta.summary.clone();
    }
    if summary.is_empty() {
        summary = "No summary text for this session.".into();
    }
    let ctx_frac = context_fraction(meta.context_window_usage_pct, meta.context_compact());
    let status_row = session_state_from_meta(meta, tea);
    // Title lives on the browse bar. Status is badges only.
    let mut col = column![status_row].spacing(8);
    // Progress only where context matters (session detail), and only when known.
    if ctx_frac > 0.0 {
        col = col.push(kit::context_progress(ctx_frac, tea));
    }
    if hud.selected_awaiting() {
        col = col.push(awaiting_banner(hud, tea));
    }
    if o.findings.count > 0 || o.findings.total > 0 {
        let n = if o.findings.total > 0 {
            o.findings.total
        } else {
            o.findings.count
        };
        col = col.push(icedtea::widget::banner(
            format!("{n} findings — open the Findings pane"),
            Some(("Findings".into(), Message::SetTab(Tab::Findings))),
            tea,
            A11y::new("findings", Role::Status),
        ));
    }
    if o.notes.count > 0 {
        col = col.push(icedtea::widget::banner(
            format!("{} notes — open the Notes pane", o.notes.count),
            Some(("Notes".into(), Message::SetTab(Tab::Notes))),
            tea,
            A11y::new("notes", Role::Status),
        ));
    }
    if summary != title && summary != "No summary text for this session." {
        // One selectable buffer. markdown_view lays out every item on each
        // wheel tick (same tax as Turns cards before they went plain).
        col = col.push(select_bound(
            hud,
            "overview.summary".into(),
            &summary,
            hud.tokens(),
            icedtea::typo::FontFace::Ui,
        ));
    } else if summary == "No summary text for this session." {
        col = col.push(icedtea::widget::meta(
            summary,
            hud.tokens(),
            A11y::new("summary", Role::Status),
        ));
    }
    for field in overview_fields(meta, &o.turns) {
        col = col.push(kv(hud, field.key, field.label, field.value, field.copyable));
    }
    col.into()
}

fn overview_tasks(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    overview_run_list(
        hud,
        overview_task_rows(&o.background_jobs, &o.schedules),
        "No tasks",
        "No background jobs or schedules.",
    )
}

fn overview_workflows(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    overview_run_list(
        hud,
        overview_workflow_rows(&o.workflows),
        "No workflows",
        "No workflow runs in this session.",
    )
}

fn overview_subagents(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    overview_run_list(
        hud,
        overview_subagent_rows(&o.turns.subagent_runs),
        "No subagents",
        "No subagent runs in this session.",
    )
}

fn overview_run_list<'a>(
    hud: &'a Hud,
    rows: Vec<crate::format::OverviewTaskRow>,
    empty_title: &'static str,
    empty_detail: &'static str,
) -> Element<'a, Message> {
    let tea = hud.body_tokens();
    if rows.is_empty() {
        return kit::status_empty(empty_title, empty_detail, tea);
    }
    let focus = hud.tasks_focus();
    icedtea::widget::virtual_column(
        hud.overview_heights(),
        hud.overview_window(),
        OVERVIEW_LIST_OVERSCAN,
        focus,
        Message::OverviewScroll,
        Some(hud.overview_scroll_id()),
        tea,
        move |i| {
            let Some(row) = rows.get(i) else {
                return Space::new().height(0).into();
            };
            let selected = focus == Some(i);
            let status = list_status_label(&row.status, "");
            let kind = format_tool_display(&row.kind);
            let ink = if row.openable { tea.text } else { tea.muted };
            let chips = row![
                status_chip(status.clone(), status_tone(&status), tea),
                status_chip(kind, "", tea),
            ]
            .spacing(8)
            .align_y(Alignment::Center);
            let name = text(row.label.clone())
                .size(tea.body())
                .font(typo::UI)
                .color(ink)
                .wrapping(iced::widget::text::Wrapping::None);
            let mut header = row![chips, Space::new().width(Length::Fill)]
                .spacing(6)
                .align_y(Alignment::Center)
                .width(Length::Fill);
            if row.openable {
                header = header.push(text("›").size(tea.meta()).color(tea.muted));
            }
            let face = column![header, name].spacing(4).width(Length::Fill);
            let card = container(face)
                .padding(tea.density.inset())
                .width(Length::Fill)
                .style(move |_| icedtea::style::card(tea, selected));
            mouse_area(card)
                .on_press(Message::FocusOverviewRow(i))
                .into()
        },
        A11y::new(empty_title, Role::List),
    )
}

fn overview_stats(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    if hud.timeline_loading() && hud.stats_table().rows.is_empty() {
        return busy_pane();
    }
    if hud.stats_table().rows.is_empty() {
        return kit::status_empty(
            "No stats yet",
            "Open Timeline to fill event and tool counts.",
            tea,
        );
    }
    icedtea::widget::data_table(
        hud.stats_table(),
        hud.stats_selection(),
        hud.stats_cursor(),
        hud.stats_cols(),
        true,
        hud.stats_window(),
        STATS_ROW_H,
        2,
        Message::StatsCell,
        Message::StatsSort,
        Message::StatsScroll,
        Message::StatsHScroll,
        |_| Message::Noop,
        tea,
        A11y::new("Stats", Role::Table),
    )
}

/// One Overview meta row via icedtea value_field / plain labeled readout.
fn kv<'a>(
    hud: &'a Hud,
    key: &'static str,
    label: &'static str,
    v: String,
    copy: bool,
) -> Element<'a, Message> {
    let _ = (v, copy);
    glance_row(hud, &ExtractKey::Overview(key).id(), label)
}

fn glance_row<'a>(hud: &'a Hud, id: &str, label: &str) -> Element<'a, Message> {
    let tea = hud.tokens();
    if let Some(buf) = hud.field(id) {
        let id = id.to_string();
        return kit::labeled_value(
            label,
            buf,
            move |action| Message::Select {
                id: id.clone(),
                action,
            },
            icedtea::typo::FontFace::Ui,
            tea,
            A11y::new(label, Role::Group),
        );
    }
    kit::labeled_plain(label, "", tea)
}

fn footer(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let tone = if hud.status_err() {
        Some(ToastKind::Danger)
    } else {
        None
    };
    icedtea::pattern::status_bar(
        hud.status(),
        tone,
        None,
        &crate::help::footer_table_for(hud.key_scope(), hud.key_overlay()),
        tea,
        tea.direction,
    )
}

/// Filled-black mark; icedtea recolors it from tokens.
const DIFF_MARK: &[u8] = br#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path fill="black" d="M2 3h5v2H2zm7 0h5v2H9zM2 7h12v2H2zm0 4h5v2H2zm7 0h5v2H9z"/></svg>"#;

fn chip_btn(label: String, msg: Message, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    chip_btn_icons(label, msg, tea, icedtea::icon::Icons::NONE)
}

fn chip_btn_icons(
    label: String,
    msg: Message,
    tea: icedtea::theme::Tokens,
    icons: icedtea::icon::Icons,
) -> Element<'static, Message> {
    icedtea::widget::chip(
        label.clone(),
        Some(msg),
        None,
        tea,
        Variant::Chip,
        icedtea::widget::ChipKind::Assist,
        icons,
        A11y::button(label),
    )
}

fn command_end(child: Element<'static, Message>) -> Element<'static, Message> {
    row![Space::new().width(Length::Fill), child]
        .width(Length::Fill)
        .align_y(Alignment::Center)
        .into()
}

fn card_chips(
    hud: &Hud,
    mark: Option<CardMark>,
    note: Option<Message>,
    jump: Option<Message>,
) -> Element<'static, Message> {
    // Full-width bar (open cards / forms): marks left, commands right.
    row![
        card_marks_row(hud, mark),
        Space::new().width(Length::Fill),
        card_cmds_row(hud, note, jump, None),
    ]
    .spacing(8)
    .align_y(Alignment::Center)
    .width(Length::Fill)
    .into()
}

/// Compact chips for closed-card title rows (no flex fill — sits beside title).
fn card_chips_inline(
    hud: &Hud,
    mark: Option<CardMark>,
    note: Option<Message>,
    jump: Option<Message>,
    diff: Option<Message>,
) -> Element<'static, Message> {
    row![
        card_marks_row(hud, mark),
        card_cmds_row(hud, note, jump, diff),
    ]
    .spacing(4)
    .align_y(Alignment::Center)
    .into()
}

fn card_marks_row(hud: &Hud, mark: Option<CardMark>) -> Element<'static, Message> {
    let tea = hud.tokens();
    let mut marks = row![].spacing(4);
    if let Some(m) = mark {
        if m.findings > 0 {
            let ev = m.first_finding_event;
            marks = marks.push(chip_btn(
                format!("f{}", m.findings),
                if let Some(ix) = ev {
                    Message::JumpTimeline(ix)
                } else {
                    Message::SetTab(Tab::Findings)
                },
                tea,
            ));
        }
        if m.notes > 0 {
            let nid = m.first_note_id;
            marks = marks.push(chip_btn(
                format!("n{}", m.notes),
                if nid.is_empty() {
                    Message::SetTab(Tab::Notes)
                } else {
                    Message::OpenNote(nid)
                },
                tea,
            ));
        }
        // Tool-error counts live on the turn stats badge row.
    }
    marks.into()
}

fn card_cmds_row(
    hud: &Hud,
    note: Option<Message>,
    jump: Option<Message>,
    diff: Option<Message>,
) -> Element<'static, Message> {
    let tea = hud.tokens();
    let tok = hud.tokens();
    let mut cmds = row![].spacing(4);
    if let Some(msg) = note {
        cmds = cmds.push(chip_btn("Add note".into(), msg, tea));
    }
    if let Some(msg) = diff {
        cmds = cmds.push(icedtea::widget::tooltip_wrap(
            chip_btn_icons(
                "Diff".into(),
                msg,
                tea,
                icedtea::icon::Icons::leading(icedtea::icon::Glyph::Bytes(DIFF_MARK)),
            ),
            "Go to Diff",
            icedtea::widget::TooltipAnchor::Follow,
            tea,
            A11y::button("Go to Diff"),
        ));
    }
    if let Some(msg) = jump {
        cmds = cmds.push(jump_control(msg, tok.muted, tea));
    }
    cmds.into()
}

fn card_actions(
    actions: Vec<icedtea::action::Action<Message>>,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    icedtea::pattern::command_bar(actions, tea, tea.direction)
}

fn expand_card<'a>(
    title: String,
    child: Element<'a, Message>,
    open: bool,
    progress: f32,
    on_toggle: impl Fn(bool) -> Message + 'a,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    icedtea::widget::expander(
        title.clone(),
        None,
        child,
        icedtea::widget::Peek::Lines(2),
        open,
        progress,
        on_toggle,
        tea,
        A11y::new(title, Role::Group),
    )
}

/// Closed Timeline row: flat card. Click opens full-pane detail (not expand).
///
/// Chips share the title row so the virtual height only needs title + face
/// (a third chips row was clipped by ``TIMELINE_ROW_H`` under ``clip(true)``).
fn closed_list_card<'a>(
    title: Element<'a, Message>,
    face: Element<'a, Message>,
    chips: Element<'a, Message>,
    on_open: Message,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    let header = row![
        title,
        Space::new().width(Length::Fill),
        chips,
        text("›").size(tea.meta()).color(tea.muted),
    ]
    .spacing(6)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    let body = column![header, face].spacing(4).width(Length::Fill);
    mouse_area(
        container(body)
            .padding(tea.density.inset())
            .width(Length::Fill)
            .style(move |_| icedtea::style::card(tea, selected)),
    )
    .on_press(on_open)
    .into()
}

fn turn_title(t: &TurnRow) -> String {
    t.face_caption()
}

/// Outcome plus duration / counts as the same small badges as session chrome.
fn turn_stats_row(t: &TurnRow, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let status = if t.open {
        "open".to_string()
    } else {
        list_status_label(&t.outcome, &t.outcome)
    };
    let tone = if t.open {
        "running"
    } else {
        status_tone(&status)
    };
    let mut chips = row![status_chip(status, tone, tea)]
        .spacing(8)
        .align_y(Alignment::Center);
    if let Some(taken) = t.duration_seconds.filter(|s| *s > 0.0).map(fmt_duration) {
        chips = chips.push(status_chip(taken, "", tea));
    }
    if t.event_count > 0 {
        chips = chips.push(status_chip(format!("{} events", t.event_count), "", tea));
    }
    if t.tool_call_count > 0 {
        chips = chips.push(status_chip(format!("{} tools", t.tool_call_count), "", tea));
    }
    if t.tool_error_count > 0 {
        chips = chips.push(status_chip(
            format!("{} tool errors", t.tool_error_count),
            "error",
            tea,
        ));
    }
    if let Some(n) = t.prompt_index {
        chips = chips.push(status_chip(format!("prompt {n}"), "", tea));
    }
    chips.into()
}

fn turn_run_chips(t: &TurnRow, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    if t.subagent_runs.is_empty() {
        return Space::new().height(0).into();
    }
    let mut col = column![].spacing(4);
    for run in &t.subagent_runs {
        let kind = if run.subagent_type.is_empty() {
            "subagent".to_string()
        } else {
            run.subagent_type.clone()
        };
        let desc = if run.description.is_empty() {
            run.child_session_id.clone()
        } else {
            run.description.clone()
        };
        let mut chips = row![
            status_chip(kind, "", tea),
            status_chip(
                list_status_label(&run.status, &run.status),
                status_tone(&run.status),
                tea,
            ),
        ]
        .spacing(8)
        .align_y(Alignment::Center);
        if !desc.is_empty() {
            chips = chips.push(icedtea::widget::meta(
                desc.clone(),
                tea,
                A11y::new(desc, Role::Status),
            ));
        }
        let row: Element<'static, Message> = chips.into();
        if run.openable {
            col = col.push(mouse_area(row).on_press(Message::OpenChild {
                path: run.child_path.clone(),
                sid: run.child_session_id.clone(),
            }));
        } else {
            col = col.push(row);
        }
    }
    col.into()
}

fn turn_note(t: &TurnRow) -> Message {
    Message::StartNote {
        turn: t.face_id().map(|n| n.to_string()).unwrap_or_default(),
        event: String::new(),
    }
}

/// Open Timeline with this turn’s events only (list, not a single-event detail).
fn turn_diff(t: &TurnRow) -> Message {
    Message::OpenTurnDiff {
        prompt_index: t.prompt_index,
    }
}

fn turn_jump(t: &TurnRow) -> Message {
    use crate::model::EventsTurnPick;
    let label = t.face_caption();
    Message::EventsTurnPicked(EventsTurnPick {
        turn_index: Some(t.turn_index),
        label,
    })
}

fn event_note(ev: &TimelineEvent) -> Message {
    Message::StartNote {
        turn: ev.turn_index.map(|n| n.to_string()).unwrap_or_default(),
        event: ev.index.to_string(),
    }
}

fn event_type_human(ev: &TimelineEvent) -> String {
    human_event_type_label(
        &ev.event_type,
        &ev.type_label,
        &ev.kind,
        event_is_monitor(&ev.raw_input),
    )
}

/// Human type + brand role for the heading badge next to ``#index``.
fn event_type_paint(ev: &TimelineEvent) -> Option<(String, BrandRole)> {
    let human = event_type_human(ev);
    if human.is_empty() {
        return None;
    }
    Some((
        human,
        event_brand_role(&ev.event_type, &ev.kind, ev.is_error),
    ))
}

fn event_tool_role(ev: &TimelineEvent) -> BrandRole {
    if ev.is_error {
        BrandRole::Failed
    } else {
        tool_brand_role(&ev.tool_name, false).unwrap_or(BrandRole::Cancelled)
    }
}

/// ``#index`` + type badge on one row (turn / time muted after).
fn event_list_heading(
    ev: &TimelineEvent,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let mut head = row![status_chip(format!("#{}", ev.index), "", tea),]
        .spacing(8)
        .align_y(Alignment::Center);
    if let Some((human, role)) = event_type_paint(ev) {
        head = head.push(label_badge(human, role, tea));
    }
    if let Some(turn) = ev.turn_index {
        head = head.push(status_chip(format!("turn {turn}"), "", tea));
    }
    let time = ev.time.trim();
    if !time.is_empty() {
        head = head.push(status_chip(time.to_string(), "", tea));
    }
    head.into()
}

fn event_face(ev: &TimelineEvent, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let tool_row = is_tool_identity(&ev.kind, &ev.event_type, &ev.tool_name);
    let raw_preview = if ev.preview.is_empty() {
        ev.content.as_str()
    } else {
        ev.preview.as_str()
    };
    let raw_preview = if raw_preview.is_empty() {
        ev.heading.as_str()
    } else {
        raw_preview
    };
    let preview = if job_event_label(&ev.event_type, event_is_monitor(&ev.raw_input)).is_some() {
        job_list_preview(&ev.event_type, &ev.raw_input, raw_preview)
    } else if ev.tool_name == "workflow" {
        let name = workflow_name_from_raw(&ev.raw_input);
        if name.is_empty() {
            raw_preview.to_string()
        } else {
            name
        }
    } else if ev.event_type.starts_with("subagent_") {
        subagent_list_preview(&ev.event_type, &ev.raw_input, raw_preview)
    } else if tool_row {
        list_event_detail(raw_preview, &ev.tool_name)
    } else {
        raw_preview.to_string()
    };
    // One scannable line (TUI type + summary columns), not a markdown stack.
    let preview = capped_display(&plain_card_text(&preview), 160);
    if !tool_row {
        if preview.is_empty() {
            return text("—").size(tea.body()).color(tea.muted).into();
        }
        return text(preview).size(tea.body()).color(tea.text).into();
    }
    let name = label_badge(format_tool_display(&ev.tool_name), event_tool_role(ev), tea);
    if preview.is_empty() {
        return name;
    }
    row![name, text(preview).size(tea.body()).color(tea.text)]
        .spacing(8)
        .align_y(Alignment::Center)
        .into()
}

fn event_body<'a>(
    hud: &'a Hud,
    ev: &'a TimelineEvent,
    mark: Option<CardMark>,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let mut col = column![].spacing(6);
    if ev.event_type.starts_with("subagent_") || !ev.child_session_id.is_empty() {
        let typ = ev
            .raw_input
            .get("subagentType")
            .and_then(|v| v.as_str())
            .or_else(|| ev.raw_input.get("subagent_type").and_then(|v| v.as_str()))
            .unwrap_or("")
            .trim()
            .to_string();
        let preview = subagent_list_preview(&ev.event_type, &ev.raw_input, &ev.content);
        let mut chips = row![].spacing(8).align_y(Alignment::Center);
        if !typ.is_empty() {
            chips = chips.push(status_chip(typ.clone(), "", tok));
        }
        if !ev.subagent_status.is_empty() {
            chips = chips.push(status_chip(
                list_status_label(&ev.subagent_status, &ev.subagent_status),
                status_tone(&ev.subagent_status),
                tok,
            ));
        }
        if let Some(ms) = ev.duration_ms {
            chips = chips.push(status_chip(fmt_duration(ms as f64 / 1000.0), "", tok));
        }
        col = col.push(chips);
        let happened = {
            let mut bits = Vec::new();
            if !typ.is_empty() {
                bits.push(typ.as_str());
            }
            if !ev.subagent_status.is_empty() {
                bits.push(ev.subagent_status.as_str());
            }
            bits.join("  ·  ")
        };
        let failed = if matches!(
            ev.subagent_status.as_str(),
            "failed" | "error" | "cancelled"
        ) {
            ev.subagent_status.as_str()
        } else {
            ""
        };
        for block in subagent_inspect_blocks(&preview, &happened, failed) {
            col = col.push(icedtea::widget::meta(
                block.label,
                tok,
                A11y::new(block.label, Role::Header),
            ));
            col = col.push(select_bound(
                hud,
                format!("subagent.{}.{}", ev.index, block.label.to_ascii_lowercase()),
                &block.body,
                tok,
                icedtea::typo::FontFace::Ui,
            ));
        }
    }
    if let Some(hit) = timeline_query_hit(ev, hud.timeline_query()) {
        col = col.push(icedtea::widget::meta(
            format!("matched in {}: {}", hit.field, hit.snippet),
            tok,
            A11y::new("search hit", Role::Status),
        ));
    }
    if ev.child_session_id.is_empty() && !ev.event_type.starts_with("subagent_") {
        col = col.push(event_payload(ev, true, hud));
    }
    if ev.content_truncated {
        col = col.push(icedtea::widget::info_bar(
            ToastKind::Warning,
            "Content truncated by control",
            tok,
            A11y::new("Content truncated by control", Role::Status),
        ));
    }
    col.push(card_chips(hud, mark, Some(event_note(ev)), None))
        .into()
}

pub(crate) fn finding_jump(f: &FindingRow) -> Message {
    f.primary_event_index
        .or_else(|| f.event_indices.first().copied())
        .map(Message::JumpTimeline)
        .unwrap_or(Message::SetTab(Tab::Overview))
}

fn note_when(n: &NoteRow) -> String {
    if n.updated_at.is_empty() {
        format_note_time(&n.created_at)
    } else {
        format_note_time(&n.updated_at)
    }
}

fn note_body<'a>(
    hud: &'a Hud,
    n: &'a NoteRow,
    body: &str,
    extras: Vec<(String, String)>,
) -> Element<'a, Message> {
    let tea = hud.tokens();
    let turn = n.turn_index.map(|i| i.to_string()).unwrap_or_default();
    let place = if turn.is_empty() || turn == "null" {
        "Session".to_string()
    } else {
        format!("Turn {turn}")
    };
    let mut chips = row![status_chip(place, "", tea)]
        .spacing(8)
        .align_y(Alignment::Center);
    let when = note_when(n);
    if !when.is_empty() {
        chips = chips.push(status_chip(when, "", tea));
    }
    let mut card = column![chips].spacing(8);
    if !body.is_empty() {
        card = card.push(select_bound(
            hud,
            format!("note.{}", n.id),
            body,
            tea,
            icedtea::typo::FontFace::Ui,
        ));
    }
    for (k, v) in extras.into_iter().take(8) {
        card = card.push(kit::labeled_plain(&k, v, tea));
    }
    card.push(
        row![
            Space::new().width(Length::Fill),
            card_actions(note_commands(&n.id, hud.note_delete_armed()), tea),
        ]
        .spacing(8)
        .align_y(Alignment::Center),
    )
    .into()
}

fn note_commands(id: &str, delete_armed: &str) -> Vec<icedtea::action::Action<Message>> {
    vec![
        icedtea::action::Action::new("note.edit", "Edit", Message::OpenNote(id.to_string())),
        icedtea::action::Action::new(
            "note.delete",
            if delete_armed == id {
                "Delete?"
            } else {
                "Delete"
            },
            Message::RequestDelete(id.to_string()),
        ),
    ]
}

fn closed_turn_face(summary: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    // ~2 lines at typical detail width; keeps closed-card height honest.
    plain_face(summary, "No user prompt in this turn", 180, tea)
}

/// Closed-card preview only. Markdown parse/layout per visible row was the
/// Turns/Timeline scroll tax; open bodies use selectable text when needed.
fn prompt_face(summary: &str, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    plain_face(summary, "—", 280, tea)
}

/// Strip light markdown so closed cards do not show raw ``**bold**`` markers.
fn plain_card_text(summary: &str) -> String {
    let mut out = String::with_capacity(summary.len());
    let mut chars = summary.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' | '_' | '`' => {
                // Drop run of the same marker (**, __, ``` fence ticks).
                while chars.peek() == Some(&c) {
                    chars.next();
                }
            }
            _ => out.push(c),
        }
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn plain_face(
    summary: &str,
    empty: &'static str,
    max_chars: usize,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    if summary.is_empty() {
        return text(empty).size(tea.body()).color(tea.muted).into();
    }
    text(capped_display(&plain_card_text(summary), max_chars))
        .size(tea.body())
        .font(typo::UI)
        .color(tea.text)
        .into()
}

/// Fixed Turns card: prompt + light meta + jump/note (no expander / assistant body).
///
/// Title row carries chips so the 2-line prompt is not pushed under the
/// virtual clip (``CLOSED_TURN_CARD_H``).
fn turn_list_card(
    hud: &Hud,
    t: &TurnRow,
    mark: Option<CardMark>,
    selected: bool,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let jump = turn_jump(t);
    let title = text(turn_title(t))
        .size(tea.body())
        .font(typo::UI_BOLD)
        .color(tea.text);
    let header = row![
        title,
        Space::new().width(Length::Fill),
        card_chips_inline(
            hud,
            mark,
            Some(turn_note(t)),
            Some(jump.clone()),
            hud.turn_has_diff(t.prompt_index).then(|| turn_diff(t)),
        ),
    ]
    .spacing(6)
    .align_y(Alignment::Center)
    .width(Length::Fill);
    let body = column![
        header,
        turn_stats_row(t, tea),
        turn_run_chips(t, tea),
        closed_turn_face(&t.summary, tea),
    ]
    .spacing(4)
    .width(Length::Fill);
    mouse_area(
        container(body)
            .padding(tea.density.inset())
            .width(Length::Fill)
            .style(move |_| icedtea::style::card(tea, selected)),
    )
    .on_press(Message::FocusTurn(t.turn_index))
    .into()
}

fn turns_filter(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    container(icedtea::widget::search_input(
        hud.turns_query(),
        Message::TurnsQuery,
        None,
        tea,
        A11y::new("Search turns", Role::TextBox),
        Some(hud.turns_search_id()),
    ))
    .width(Length::Fill)
    .padding(Padding {
        top: 0.0,
        right: 0.0,
        bottom: 8.0,
        left: 0.0,
    })
    .into()
}

fn turns_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let turns: &[TurnRow] = &o.turns.turns;
    let (turn_marks, _) = hud.card_marks();
    let tea = hud.body_tokens();
    if turns.is_empty() {
        return kit::status_empty("No turns", "Nothing segmented yet.", tea);
    }
    let idxs = hud.filtered_turn_indices();
    let list = if idxs.is_empty() {
        kit::status_empty("No matches", "No turns match this search.", tea)
    } else {
        let heights = hud.turn_heights();
        icedtea::widget::virtual_column(
            heights,
            hud.turn_window(),
            TURNS_OVERSCAN,
            None,
            Message::TurnScroll,
            Some(hud.turn_scroll_id()),
            tea,
            move |i| {
                let Some(&src) = idxs.get(i) else {
                    return Space::new().height(0).into();
                };
                let Some(t) = turns.get(src) else {
                    return Space::new().height(0).into();
                };
                let mark = turn_marks.get(&t.turn_index).cloned();
                let selected = hud.turns_focus() == Some(t.turn_index);
                column![
                    turn_list_card(hud, t, mark, selected, tea),
                    Space::new().height(crate::live::LIST_GAP),
                ]
                .into()
            },
            A11y::new("Turns", Role::List),
        )
    };
    column![turns_filter(hud), list]
        .spacing(0)
        .height(Length::Fill)
        .into()
}

fn timeline_tab(hud: &Hud) -> Element<'_, Message> {
    if let Some(ix) = hud.timeline_open() {
        return event_detail_pane(hud, ix);
    }
    if hud.timeline_query().trim().is_empty()
        && hud.last_timeline().is_none()
        && hud.filtered_indices().is_empty()
        && !hud.timeline_loading()
    {
        // Should be rare: SetTab/All turns loads immediately. Honest fallback.
        return busy_pane();
    }
    if hud.timeline_loading() && hud.filtered_indices().is_empty() {
        return busy_pane();
    }
    let idxs = hud.filtered_indices();
    if idxs.is_empty() {
        if hud.timeline_loading() || !hud.timeline_complete() {
            return busy_pane();
        }
        return kit::status_empty("No events", "Nothing matches this filter.", hud.tokens());
    }
    let (_, ev_marks) = hud.card_marks();
    let tea = hud.tokens();
    let source = hud.timeline_events();
    let list = icedtea::widget::virtual_column(
        hud.timeline_heights(),
        hud.timeline_window(),
        TIMELINE_OVERSCAN,
        None,
        Message::TimelineScroll,
        Some(hud.timeline_scroll_id()),
        tea,
        move |i| {
            let Some(&src_i) = idxs.get(i) else {
                return Space::new().height(0).into();
            };
            let Some(ev) = source.get(src_i) else {
                return Space::new().height(0).into();
            };
            let ix = ev.index;
            let mark = ev_marks.get(&ix).cloned();
            let selected = hud.timeline_focus() == Some(ix);
            let card = closed_list_card(
                event_list_heading(ev, tea),
                event_face(ev, tea),
                card_chips_inline(hud, mark, Some(event_note(ev)), None, None),
                Message::SelectTimeline(ix),
                selected,
                tea,
            );
            column![card, Space::new().height(crate::live::LIST_GAP)].into()
        },
        A11y::new("Timeline", Role::List),
    );
    let more = crate::live::timeline_more_caption(
        hud.timeline_complete(),
        hud.timeline_at_live_end(),
        hud.timeline_loading(),
    );
    let Some(caption) = more else {
        return list;
    };
    column![
        list,
        text(caption).size(tea.meta()).color(hud.tokens().muted),
    ]
    .spacing(8)
    .height(Length::Fill)
    .into()
}

/// Full-area event body (click a list row; Esc returns to the list at this event).
///
/// Chrome (title + adjacent cards) stays **above** the scroll pane.
fn event_detail_pane(hud: &Hud, ix: i64) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    let Some(ev) = hud.timeline_events().iter().find(|e| e.index == ix) else {
        return column![event_detail_chrome(hud, ix, None, tea), busy_pane(),]
            .spacing(10)
            .height(Length::Fill)
            .into();
    };
    let (_, ev_marks) = hud.card_marks();
    let mark = ev_marks.get(&ix).cloned();
    let scroll = icedtea::widget::themed_scroll(
        container(event_body(hud, ev, mark))
            .width(Length::Fill)
            .padding(Padding {
                top: 0.0,
                right: icedtea::chrome::SCROLL_RAIL_WIDTH,
                bottom: 8.0,
                left: 0.0,
            })
            .into(),
        tea,
        A11y::new(format!("Event {ix}"), Role::Group),
        false,
        None,
        None::<fn(f32) -> Message>,
    );
    column![event_detail_chrome(hud, ix, Some(ev), tea), scroll]
        .spacing(10)
        .height(Length::Fill)
        .into()
}

fn event_detail_chrome(
    hud: &Hud,
    ix: i64,
    ev: Option<&TimelineEvent>,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    let head = ev.map(|e| event_list_heading(e, tea)).unwrap_or_else(|| {
        text(format!("#{ix}"))
            .size(tea.meta())
            .font(typo::UI_BOLD)
            .color(tea.text)
            .into()
    });
    column![head, event_pager(hud, tea)]
        .spacing(4)
        .width(Length::Fill)
        .into()
}

/// Event step: Previous on the start edge, Next on the end. Turn is on the heading.
fn event_pager(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'static, Message> {
    let (at, n) = hud.timeline_detail_pos().unwrap_or((0, 0));
    if n == 0 {
        return Space::new().height(0).into();
    }
    let has_prev = at > 1;
    let has_next = at < n;
    row![
        icedtea::widget::themed_button(
            "Previous",
            has_prev.then_some(Message::TimelineDetailStep(-1)),
            tea,
            Variant::Quiet,
            icedtea::icon::Icons::NONE,
            A11y::button("Previous event").with_disabled(!has_prev),
        ),
        Space::new().width(Length::Fill),
        icedtea::widget::themed_button(
            "Next",
            has_next.then_some(Message::TimelineDetailStep(1)),
            tea,
            Variant::Quiet,
            icedtea::icon::Icons::NONE,
            A11y::button("Next event").with_disabled(!has_next),
        ),
    ]
    .width(Length::Fill)
    .align_y(Alignment::Center)
    .into()
}

fn diff_tab(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.body_tokens();
    column![
        diff_chrome(hud, tea),
        diff_search(hud),
        diff_split(hud, tea),
    ]
    .spacing(6)
    .height(Length::Fill)
    .into()
}

fn diff_split(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let files = hud.visible_diff_files();
    // tree_view already scrolls; do not nest another themed_scroll.
    let files_body: Element<'_, Message> = if files.is_empty() {
        kit::status_empty(
            "No file changes",
            "Grok rewind snapshots or search_replace edits for this session.",
            tea,
        )
    } else {
        let paths: Vec<&str> = files.iter().map(|f| f.path.as_str()).collect();
        let root = crate::diff_tree::file_tree(paths, hud.diff_tree_collapsed());
        let selected = if hud.diff_file().is_empty() {
            None
        } else {
            Some(crate::diff_tree::path_id(hud.diff_file()))
        };
        icedtea::widget::tree_view(
            &root,
            selected,
            None,
            Message::DiffTreeToggle,
            |click| Message::DiffTreeSelect(click.id),
            icedtea::widget::TreeFace::Files,
            tea,
            A11y::new("Diff files", Role::Tree),
        )
    };
    let unified = hud
        .current_diff_point()
        .and_then(|p| p.files.iter().find(|f| f.path == hud.diff_file()))
        .map(|f| f.unified.as_str())
        .unwrap_or("");
    let files_pane = container(files_body)
        .width(Length::Fixed(248.0))
        .height(Length::Fill)
        .padding(tea.density.gap())
        .style(move |_| icedtea::style::card(tea, false));
    let hunk_pane = container(icedtea::widget::themed_scroll(
        paint_unified(hud, unified, tea),
        tea,
        A11y::new("Diff hunk", Role::Group),
        false,
        Some(hud.diff_hunk_scroll_id()),
        None::<fn(_) -> Message>,
    ))
    .padding(tea.density.inset())
    .width(Length::Fill)
    .height(Length::Fill)
    .style(move |_| icedtea::style::card(tea, false));
    row![files_pane, hunk_pane]
        .spacing(tea.density.gap())
        .height(Length::Fill)
        .into()
}

fn diff_chrome(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let mut header = row![].spacing(tea.density.gap()).align_y(Alignment::Center);
    if !hud.diff_point_options().is_empty() {
        header = header.push(icedtea::widget::meta(
            "Snapshot",
            tea,
            A11y::new("Snapshot", Role::Header),
        ));
        header = header.push(icedtea::widget::themed_pick_list(
            hud.diff_point_options(),
            hud.diff_point_selected(),
            Message::DiffPointPicked,
            tea,
            icedtea::widget::ControlSize::Default,
            A11y::new("Snapshot", Role::ComboBox),
        ));
    }
    header = header.push(diff_context_tabs(hud, tea));
    container(
        column![
            header,
            icedtea::widget::themed_scroll(
                diff_context_body(hud, tea),
                tea,
                A11y::new("Diff context body", Role::Group),
                false,
                None,
                None::<fn(_) -> Message>,
            )
        ]
        .spacing(tea.density.gap())
        .height(Length::Fill),
    )
    .padding(Padding::from([tea.density.gap(), tea.density.inset()]))
    .height(Length::Fixed(112.0))
    .width(Length::Fill)
    .style(move |_| icedtea::style::card(tea, false))
    .into()
}

fn diff_context_tabs(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    let active = match hud.diff_context() {
        DiffContext::Prompt => 0,
        DiffContext::Assistant => 1,
    };
    let mut bar = icedtea::collection::Tabs::new(["Prompt", "Assistant"]);
    bar.select(active);
    bar.closable = false;
    icedtea::widget::tab_bar(
        &bar,
        |i| {
            Message::DiffContext(if i == 0 {
                DiffContext::Prompt
            } else {
                DiffContext::Assistant
            })
        },
        |_| Message::Noop,
        0.0,
        true,
        tea,
        A11y::new("Diff context", Role::Tab),
    )
}

fn diff_search(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    icedtea::widget::search_input(
        hud.diff_query(),
        Message::DiffQuery,
        None,
        tea,
        A11y::new("Search files and hunks", Role::TextBox),
        Some(hud.diff_search_id()),
    )
}

fn diff_context_body(hud: &Hud, tea: icedtea::theme::Tokens) -> Element<'_, Message> {
    match hud.diff_context() {
        DiffContext::Prompt => {
            let src = hud
                .current_diff_point()
                .map(|p| p.prompt.as_str())
                .unwrap_or("");
            if src.trim().is_empty() {
                text("(empty)").size(tea.meta()).color(tea.muted).into()
            } else {
                markdown_bound(hud, "diff.prompt".into(), src, tea)
            }
        }
        DiffContext::Assistant => {
            let src = hud
                .current_diff_point()
                .map(|p| p.assistant.as_str())
                .unwrap_or("");
            if src.trim().is_empty() {
                text("(empty)").size(tea.meta()).color(tea.muted).into()
            } else {
                markdown_bound(hud, "diff.assistant".into(), src, tea)
            }
        }
    }
}

fn paint_unified<'a>(
    hud: &'a Hud,
    unified: &str,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    if unified.trim().is_empty() {
        return text("(empty)").size(tea.meta()).color(tea.muted).into();
    }
    code_inset(hud, "diff.hunk", unified, "diff", false, tea)
}

/// Title and hint for an empty Findings pane (one-line empty state).
pub fn findings_empty_copy() -> (&'static str, &'static str) {
    (
        "No findings",
        "Run analysis in the TUI so results land in the analysis cache.",
    )
}

fn findings_tab(hud: &Hud) -> Element<'_, Message> {
    let o = hud.overview().unwrap();
    let findings: &[FindingRow] = &o.findings.findings;
    let tea = hud.body_tokens();
    if findings.is_empty() {
        let (title, hint) = findings_empty_copy();
        return kit::status_empty(title, hint, tea);
    }
    let mut buckets: [Vec<&FindingRow>; 4] = [vec![], vec![], vec![], vec![]];
    for f in findings {
        let r = finding_severity_rank(&f.severity) as usize;
        buckets[r.min(3)].push(f);
    }
    let mut col = column![status_chip(format!("{} findings", findings.len()), "", tea,)].spacing(8);
    for (rank, group) in buckets.iter().enumerate() {
        if group.is_empty() {
            continue;
        }
        let title = finding_severity_title(rank as u8);
        col = col.push(
            row![
                status_chip(title, severity_tone(title), tea),
                status_chip(format!("{}", group.len()), "", tea),
            ]
            .spacing(8)
            .align_y(Alignment::Center),
        );
        for f in group {
            let id = finding_key(f);
            let open = hud.finding_expanded(&id);
            let progress = hud.finding_expand_progress(&id);
            let title = if f.title.is_empty() {
                "Finding".into()
            } else {
                f.title.clone()
            };
            let child = if open || progress > 0.0 {
                finding_body(hud, f, tea)
            } else {
                column![
                    prompt_face(&f.detail, tea),
                    command_end(jump_control(finding_jump(f), hud.tokens().muted, tea)),
                ]
                .spacing(6)
                .into()
            };
            col = col.push(expand_card(
                title,
                child,
                open,
                progress,
                {
                    let id = id.clone();
                    move |next| Message::FindingExpand {
                        id: id.clone(),
                        open: next,
                    }
                },
                tea,
            ));
        }
    }
    col.into()
}

fn finding_key(f: &FindingRow) -> String {
    if !f.id.is_empty() {
        return f.id.clone();
    }
    format!(
        "{}|{}|{}",
        f.severity,
        f.title,
        f.primary_event_index.unwrap_or(-1)
    )
}

fn finding_body<'a>(
    hud: &'a Hud,
    f: &'a FindingRow,
    tea: icedtea::theme::Tokens,
) -> Element<'a, Message> {
    let mut chips = row![status_chip(
        f.severity.clone(),
        severity_tone(&f.severity),
        tea,
    )]
    .spacing(8)
    .align_y(Alignment::Center);
    if !f.plugin_id.is_empty() {
        chips = chips.push(status_chip(f.plugin_id.clone(), "", tea));
    }
    if !f.category.is_empty() {
        chips = chips.push(status_chip(f.category.clone(), "", tea));
    }
    let mut card = column![chips].spacing(8);
    if !f.detail.is_empty() {
        let fid = format!("finding.{}", finding_key(f));
        card = card.push(if hud.markdown(&fid).is_some() {
            markdown_bound(hud, fid, &f.detail, tea)
        } else {
            select_bound(hud, fid, &f.detail, tea, icedtea::typo::FontFace::Ui)
        });
    }
    card.push(command_end(jump_control(finding_jump(f), tea.muted, tea)))
        .into()
}

fn notes_tab(hud: &Hud) -> Element<'_, Message> {
    let tea = hud.tokens();
    let o = hud.overview().unwrap();
    let mut notes: Vec<&NoteRow> = o.notes.notes.iter().collect();
    notes.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    let specs = hud.notes_schema();
    let editing = !hud.note_draft().id.is_empty();
    let mut form = column![icedtea::widget::label(
        if editing { "Edit note" } else { "Add note" },
        tea,
        A11y::new("Note form", Role::Header),
    )]
    .spacing(8);
    for spec in specs {
        let id = spec.id;
        let label = spec.label;
        let val = hud.note_draft().field(&id);
        form = form.push(icedtea::widget::meta(
            label.clone(),
            tea,
            A11y::new(label.clone(), Role::Status),
        ));
        form = form.push(icedtea::widget::themed_text_input(
            label.as_str(),
            val,
            move |v| Message::NoteField {
                id: id.clone(),
                value: v,
            },
            Some(Message::SaveNote),
            icedtea::widget::FieldOpts::NONE,
            hud.tokens(),
            A11y::new(label.clone(), Role::TextBox),
            None,
        ));
    }
    form = form.push(icedtea::widget::meta(
        "Turn",
        tea,
        A11y::new("Turn", Role::Status),
    ));
    form = form.push(
        container(icedtea::widget::themed_text_input(
            "session",
            &hud.note_draft().turn_index,
            Message::NoteTurn,
            Some(Message::SaveNote),
            icedtea::widget::FieldOpts::NONE,
            hud.tokens(),
            A11y::new("Turn", Role::TextBox),
            None,
        ))
        .width(Length::Fixed(120.0)),
    );
    if !hud.note_draft().event_index.is_empty() {
        form = form.push(icedtea::widget::meta(
            format!("Event #{}", hud.note_draft().event_index),
            tea,
            A11y::new("Event", Role::Status),
        ));
    }
    let save_label = if hud.note_saving() {
        "Saving…"
    } else if editing {
        "Save"
    } else {
        "Save note"
    };
    // Single Save is a chip (command_bar always paints a leading hairline that
    // reads as a stray "|" with one action). Multi-action edit keeps the bar.
    if editing {
        let nid = hud.note_draft().id.clone();
        let del = if hud.note_delete_armed() == nid {
            "Delete?"
        } else {
            "Delete"
        };
        form = form.push(card_actions(
            vec![
                icedtea::action::Action::new("note.save", save_label, Message::SaveNote),
                icedtea::action::Action::new("note.delete", del, Message::RequestDelete(nid)),
                icedtea::action::Action::new("note.new", "New note", Message::ResetDraft),
            ],
            hud.tokens(),
        ));
    } else {
        form = form.push(chip_btn(save_label.into(), Message::SaveNote, hud.tokens()));
    }

    let rev = o.notes.revision.clone();
    let n_notes = notes.len();
    let notes_label = if n_notes == 1 {
        "1 note".to_string()
    } else {
        format!("{n_notes} notes")
    };
    let mut note_chrome = row![status_chip(notes_label, "", hud.tokens())]
        .spacing(8)
        .align_y(Alignment::Center);
    if !rev.is_empty() {
        note_chrome = note_chrome.push(status_chip(
            format!("rev {}", rev.chars().take(12).collect::<String>()),
            "",
            hud.tokens(),
        ));
    }
    let mut col = column![form, note_chrome].spacing(12);
    if notes.is_empty() {
        col = col.push(icedtea::widget::meta(
            "No notes yet.",
            tea,
            A11y::new("No notes yet.", Role::Status),
        ));
    } else {
        for n in notes {
            let id = n.id.clone();
            let (title, body, extras) = note_fields_view(&n.fields);
            let heading = if title.is_empty() {
                "Empty note".into()
            } else {
                title
            };
            let open = hud.note_expanded(&id);
            let progress = hud.note_expand_progress(&id);
            let child = if open || progress > 0.0 {
                note_body(hud, n, &body, extras)
            } else {
                prompt_face(&body, hud.tokens())
            };
            col = col.push(expand_card(
                heading,
                child,
                open,
                progress,
                {
                    let id = id.clone();
                    move |next| Message::NoteExpand {
                        id: id.clone(),
                        open: next,
                    }
                },
                hud.tokens(),
            ));
        }
    }
    col.into()
}

fn paired_tool<'a>(hud: &'a Hud, ev: &'a TimelineEvent) -> (&'a TimelineEvent, &'a TimelineEvent) {
    let id = ev.tool_call_id.trim();
    if id.is_empty() {
        return (ev, ev);
    }
    let mut call = ev;
    let mut result = ev;
    for other in hud.timeline_events() {
        if other.tool_call_id != ev.tool_call_id {
            continue;
        }
        if other.kind == "tool" || other.event_type == "tool_call" {
            call = other;
        }
        if other.kind == "tool_result"
            || other.event_type == "tool_call_update"
            || other.event_type == "tool_result"
        {
            result = other;
        }
    }
    (call, result)
}

fn inspect_fields(call: &TimelineEvent) -> Vec<ToolField> {
    if !call.tool_fields.is_empty() {
        return call
            .tool_fields
            .iter()
            .map(|f| ToolField {
                id: f.id.clone(),
                label: f.label.clone(),
                value: f.value.clone(),
            })
            .collect();
    }
    tool_fields_from_raw(
        &call.tool_name,
        &call.raw_input,
        crate::format::EXTRACT_CHARS,
    )
}

fn workflow_event_inspect<'a>(hud: &'a Hud, ev: &'a TimelineEvent) -> Element<'a, Message> {
    let tok = hud.tokens();
    let empty: [crate::wire::WorkflowRow; 0] = [];
    let runs = hud
        .overview()
        .map(|o| o.workflows.as_slice())
        .unwrap_or(&empty);
    let run = workflow_for_event(runs, &ev.raw_input);
    let mut col = column![].spacing(10);
    let name = run
        .map(|r| r.name.as_str())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| workflow_name_from_raw(&ev.raw_input));
    if !name.is_empty() {
        col = col.push(
            text(name)
                .size(tok.title())
                .font(typo::UI_BOLD)
                .color(tok.text),
        );
    }
    let Some(run) = run else {
        col = col.push(icedtea::widget::meta(
            "No workflow run on disk",
            tok,
            A11y::new("workflow missing", Role::Status),
        ));
        return col.into();
    };
    if !run.objective.is_empty() {
        col = col.push(icedtea::widget::meta(
            "Asked",
            tok,
            A11y::new("Asked", Role::Header),
        ));
        col = col.push(select_bound(
            hud,
            format!("event.{}.wf.obj", ev.index),
            &run.objective,
            tok,
            icedtea::typo::FontFace::Ui,
        ));
    }
    let mut happen_bits = vec![list_status_label(&run.status, &run.status)];
    if !run.phase.is_empty() {
        happen_bits.push(run.phase.clone());
    }
    if let Some(ms) = run.elapsed_ms {
        if ms > 0 {
            happen_bits.push(fmt_duration(ms as f64 / 1000.0));
        }
    }
    col = col.push(icedtea::widget::meta(
        "Happened",
        tok,
        A11y::new("Happened", Role::Header),
    ));
    col = col.push(select_bound(
        hud,
        format!("event.{}.wf.happened", ev.index),
        &happen_bits.join("  ·  "),
        tok,
        icedtea::typo::FontFace::Ui,
    ));
    if run.agents_used.is_some() || run.agent_budget.is_some() {
        let used = run
            .agents_used
            .map(|n| n.to_string())
            .unwrap_or_else(|| "—".into());
        let budget = run
            .agent_budget
            .map(|n| n.to_string())
            .unwrap_or_else(|| "—".into());
        col = col.push(select_bound(
            hud,
            format!("event.{}.wf.agents", ev.index),
            &format!("{used}/{budget} agents"),
            tok,
            icedtea::typo::FontFace::Ui,
        ));
    }
    if !run.pause_message.is_empty() {
        col = col.push(icedtea::widget::meta(
            "Failed",
            tok,
            A11y::new("Failed", Role::Header),
        ));
        col = col.push(select_bound(
            hud,
            format!("event.{}.wf.pause", ev.index),
            &run.pause_message,
            tok,
            icedtea::typo::FontFace::Ui,
        ));
    }
    if !run.children.is_empty() {
        col = col.push(icedtea::widget::meta(
            "Agents",
            tok,
            A11y::new("Agents", Role::Header),
        ));
        for (i, child) in run.children.iter().enumerate() {
            let mark = if child.success { "ok" } else { "fail" };
            let label = if child.label.is_empty() {
                child.id.as_str()
            } else {
                child.label.as_str()
            };
            let line = format!("{mark}  {label}");
            let body = select_bound(
                hud,
                format!("event.{}.wf.child.{i}", ev.index),
                &line,
                tok,
                icedtea::typo::FontFace::Ui,
            );
            let sid = if child.session_id.is_empty() {
                child.id.clone()
            } else {
                child.session_id.clone()
            };
            if !sid.is_empty() {
                col = col.push(mouse_area(body).on_press(Message::OpenChild {
                    path: child.path.clone(),
                    sid,
                }));
            } else {
                col = col.push(body);
            }
        }
    }
    col.into()
}

fn job_event_inspect<'a>(hud: &'a Hud, ev: &'a TimelineEvent) -> Element<'a, Message> {
    let tok = hud.tokens();
    let mut col = column![].spacing(8);
    if ev.event_type.starts_with("scheduled_task_") {
        let human = ev
            .raw_input
            .get("human_schedule")
            .and_then(|v| v.as_str())
            .or_else(|| ev.raw_input.get("humanSchedule").and_then(|v| v.as_str()))
            .unwrap_or("")
            .trim();
        let prompt = ev
            .raw_input
            .get("prompt")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim();
        let next = ev
            .raw_input
            .get("next_fire_at")
            .and_then(|v| v.as_str())
            .or_else(|| ev.raw_input.get("nextFireAt").and_then(|v| v.as_str()))
            .unwrap_or("")
            .trim();
        let tid = job_event_id(&ev.raw_input, &ev.tool_call_id);
        let (last, child) = hud
            .overview()
            .and_then(|o| schedule_last_fire(&o.schedules, &tid))
            .unwrap_or(("", ""));
        let last = last.trim();
        let child = child.trim();
        for block in schedule_inspect_blocks(prompt, human, next, last, child) {
            col = col.push(icedtea::widget::meta(
                block.label,
                tok,
                A11y::new(block.label, Role::Header),
            ));
            let key = format!(
                "event.{}.sched.{}",
                ev.index,
                block.label.to_ascii_lowercase()
            );
            if block.label == "Asked" && looks_like_markdown(&block.body) {
                col = col.push(markdown_bound(hud, key, &block.body, tok));
            } else {
                col = col.push(select_bound(
                    hud,
                    key,
                    &block.body,
                    tok,
                    icedtea::typo::FontFace::Ui,
                ));
            }
        }
        return col.into();
    }
    let desc = job_description(&ev.raw_input);
    let cmd = job_command(&ev.raw_input, &ev.content);
    let mut path = job_output_path(&ev.raw_input);
    let tid = job_event_id(&ev.raw_input, &ev.tool_call_id);
    let want = match ev.event_type.as_str() {
        "task_backgrounded" => "task_completed",
        "task_completed" => "task_backgrounded",
        _ => "",
    };
    let mate = if tid.is_empty() || want.is_empty() {
        None
    } else {
        hud.timeline_events().iter().find(|other| {
            other.index != ev.index
                && other.event_type == want
                && job_event_id(&other.raw_input, &other.tool_call_id) == tid
        })
    };
    if path.is_empty() {
        if let Some(m) = mate {
            path = job_output_path(&m.raw_input);
        }
    }
    let tail = job_inspect_log(&hud.session_path(), &path);
    let status_raw = if ev.event_type == "task_completed" {
        &ev.raw_input
    } else {
        mate.map(|m| &m.raw_input).unwrap_or(&ev.raw_input)
    };
    let status = job_status(status_raw, &ev.content, &tail);
    let asked = if !desc.is_empty() {
        desc.clone()
    } else {
        cmd.clone()
    };
    let kind = if event_is_monitor(&ev.raw_input) {
        "monitor"
    } else {
        "background"
    };
    let mut happen_bits = vec![
        kind.to_string(),
        list_status_label(status, status).to_string(),
    ];
    if let Some(code) = job_exit_code(&ev.event_type, &ev.raw_input, mate.map(|m| &m.raw_input)) {
        happen_bits.push(format!("exit {code}"));
    }
    let ts = |v: &serde_json::Value| v.as_i64().or_else(|| v.as_u64().map(|n| n as i64));
    let start_ts = if ev.event_type == "task_backgrounded" {
        ts(&ev.timestamp)
    } else {
        mate.and_then(|m| ts(&m.timestamp))
    };
    let end_ts = if ev.event_type == "task_completed" {
        ts(&ev.timestamp)
    } else {
        mate.and_then(|m| ts(&m.timestamp))
    };
    if let (Some(start), Some(end)) = (start_ts, end_ts) {
        if end >= start {
            happen_bits.push(fmt_duration((end - start) as f64));
        }
    }
    let happened = happen_bits.join("  ·  ");
    let failed = if matches!(status, "failed" | "error" | "cancelled" | "interrupted") {
        tail.lines().last().unwrap_or("").trim().to_string()
    } else {
        String::new()
    };
    for block in job_inspect_blocks(&asked, &happened, &failed) {
        col = col.push(icedtea::widget::meta(
            block.label,
            tok,
            A11y::new(block.label, Role::Header),
        ));
        if block.label == "Asked" && !cmd.is_empty() {
            if !desc.is_empty() {
                col = col.push(select_bound(
                    hud,
                    format!("event.{}.desc", ev.index),
                    &desc,
                    tok,
                    icedtea::typo::FontFace::Ui,
                ));
            }
            col = col.push(code_inset(
                hud,
                &format!("event.{}.cmd", ev.index),
                &cmd,
                "bash",
                true,
                tok,
            ));
        } else {
            col = col.push(select_bound(
                hud,
                format!("event.{}.{}", ev.index, block.label.to_ascii_lowercase()),
                &block.body,
                tok,
                icedtea::typo::FontFace::Ui,
            ));
        }
    }
    let cwd = ev
        .raw_input
        .get("cwd")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if !cwd.is_empty() {
        col = col.push(text(cwd.to_string()).size(tok.meta()).color(tok.muted));
    }
    if !tail.trim().is_empty() {
        col = col.push(icedtea::widget::meta(
            "Log",
            tok,
            A11y::new("Log", Role::Header),
        ));
        col = col.push(code_inset(
            hud,
            &format!("event.{}.log", ev.index),
            &tail,
            "txt",
            true,
            tok,
        ));
    }
    if desc.is_empty() && cmd.is_empty() && tail.trim().is_empty() {
        col = col.push(text("—").size(tok.body()).color(tok.muted));
    }
    col.into()
}

fn event_payload<'a>(ev: &'a TimelineEvent, selected: bool, hud: &'a Hud) -> Element<'a, Message> {
    let kind = ev.kind.clone();
    let event_type = ev.event_type.clone();
    let tool = ev.tool_name.clone();
    let preview = ev.preview.clone();
    let content = ev.content.clone();
    let raw_body = timeline_body_text(&preview, &content, selected, 240);
    let body = sanitize_console_text(&display_tool_output(&raw_body, &tool));
    let tok = hud.tokens();
    let field_id = ExtractKey::Event(ev.index).id();
    if !selected {
        return render_payload_text(&body, &kind, &event_type, hud, false, &field_id, "");
    }
    if ev.tool_name == "workflow" {
        return workflow_event_inspect(hud, ev);
    }
    if job_event_label(&event_type, event_is_monitor(&ev.raw_input)).is_some() {
        return job_event_inspect(hud, ev);
    }
    let mut col = column![].spacing(8);
    let call_id = ev.tool_call_id.clone();
    if !tool.is_empty() || !call_id.is_empty() {
        let mut chips = row![].spacing(8).align_y(Alignment::Center);
        if !tool.is_empty() {
            chips = chips.push(label_badge(
                format_tool_display(&tool),
                event_tool_role(ev),
                tok,
            ));
        }
        if !call_id.is_empty() {
            chips = chips.push(icedtea::widget::meta(
                call_id,
                tok,
                A11y::new("tool call id", Role::Status),
            ));
        }
        col = col.push(chips);
    }
    if kind == "tool" || kind == "tool_result" {
        let (call, result) = paired_tool(hud, ev);
        let path_hint = {
            let mut p = path_hint_from_raw(&call.raw_input);
            if p.is_empty() {
                p = path_hint_from_raw(&result.raw_input);
            }
            if p.is_empty() {
                p = path_hint_from_raw(&ev.raw_input);
            }
            p
        };
        let fields = inspect_fields(call);
        if !fields.is_empty() {
            col = col.push(icedtea::widget::meta(
                "Input",
                tok,
                A11y::new("Input", Role::Header),
            ));
            for field in fields {
                col = col.push(icedtea::widget::meta(
                    field.label.clone(),
                    tok,
                    A11y::new(field.label.clone(), Role::Header),
                ));
                col = col.push(field_body(
                    hud,
                    &format!("event.{}.in.{}", ev.index, field.id),
                    &field.id,
                    &field.value,
                    &path_hint,
                ));
            }
        }
        let out_tool = if result.tool_name.is_empty() {
            tool.as_str()
        } else {
            result.tool_name.as_str()
        };
        let out_body = sanitize_console_text(&display_tool_output(&result.content, out_tool));
        let img = if !result.image_path.is_empty() {
            result.image_path.clone()
        } else {
            image_result_path(&result.content)
        };
        if !img.is_empty() {
            col = col.push(icedtea::widget::meta(
                "Output",
                tok,
                A11y::new("Output", Role::Header),
            ));
            col = col.push(icedtea::widget::meta(
                img.clone(),
                hud.tokens(),
                A11y::new(img.clone(), Role::Status),
            ));
            col = col.push(tool_image(&img, hud.tokens()));
        } else if !out_body.trim().is_empty() {
            let out_syn = syntax_for_tool_output(out_tool, &path_hint, &out_body);
            col = col.push(icedtea::widget::meta(
                "Output",
                tok,
                A11y::new("Output", Role::Header),
            ));
            col = col.push(render_payload_text(
                &out_body,
                &result.kind,
                &result.event_type,
                hud,
                true,
                &format!("event.{}.out", ev.index),
                out_syn,
            ));
        }
    } else {
        // Chat / thought / plan: same paint path as TUI detail (markdown for messages).
        col = col.push(render_payload_text(
            &body,
            &kind,
            &event_type,
            hud,
            true,
            &field_id,
            "",
        ));
    }
    col.into()
}

fn field_body<'a>(
    hud: &'a Hud,
    bind_id: &str,
    field_id: &str,
    value: &str,
    path_hint: &str,
) -> Element<'a, Message> {
    let tea = hud.tokens();
    let syntax = syntax_for_tool_field(field_id, path_hint, value);
    let body = if field_id == "old_string"
        || field_id == "new_string"
        || field_id == "command"
        || field_id == "pattern"
        || crate::format::looks_like_json(value)
        || !syntax.is_empty()
    {
        let syn = if syntax.is_empty() { "txt" } else { syntax };
        code_inset(hud, bind_id, value, syn, true, tea)
    } else {
        select_bound(
            hud,
            bind_id.to_string(),
            value,
            tea,
            icedtea::typo::FontFace::Mono,
        )
    };
    container(body)
        .padding(8)
        .width(Length::Fill)
        .style(move |_| icedtea::style::card(tea, false))
        .into()
}

fn render_payload_text<'a>(
    body: &str,
    kind: &str,
    event_type: &str,
    hud: &'a Hud,
    expanded: bool,
    field_id: &str,
    syntax: &str,
) -> Element<'a, Message> {
    let tok = hud.tokens();
    let trimmed = body.trim();
    let paint = body_paint_for(kind, event_type, trimmed, expanded);
    if paint == BodyPaint::Empty {
        return text("empty").size(tok.meta()).color(tok.muted).into();
    }
    let max = if expanded {
        crate::format::EXTRACT_CHARS
    } else {
        400
    };
    let cut = capped_display(body, max);
    if !expanded {
        return text(cut)
            .size(tok.meta())
            .font(typo::UI)
            .color(tok.muted)
            .into();
    }
    match paint {
        BodyPaint::Json => code_inset(hud, field_id, &cut, "json", true, hud.tokens()),
        BodyPaint::Code => {
            let syn = if syntax.is_empty() {
                syntax_for_tool_output("", "", &cut)
            } else {
                syntax
            };
            let syn = if syn.is_empty() { "txt" } else { syn };
            code_inset(hud, field_id, &cut, syn, true, hud.tokens())
        }
        BodyPaint::Image => tool_image(trimmed, hud.tokens()),
        BodyPaint::Markdown => {
            let md = markdown_bound(hud, field_id.to_string(), &cut, hud.tokens());
            if is_chat_message(kind, event_type) || kind == "subagent" {
                inset_body(md, hud)
            } else {
                md
            }
        }
        BodyPaint::Plain | BodyPaint::Empty => {
            // Prefer real highlighting when we still know a language (e.g. file path).
            if !syntax.is_empty() && (kind == "tool" || kind == "tool_result") {
                return code_inset(hud, field_id, &cut, syntax, true, hud.tokens());
            }
            let plain = if kind == "thought" {
                select_bound(
                    hud,
                    field_id.to_string(),
                    &cut,
                    tok,
                    icedtea::typo::FontFace::Ui,
                )
            } else if kind == "tool" || kind == "tool_result" {
                // Shell stdout / non-source tool bodies: monospaced like TUI.
                select_bound(
                    hud,
                    field_id.to_string(),
                    &cut,
                    tok,
                    icedtea::typo::FontFace::Mono,
                )
            } else {
                select_bound(
                    hud,
                    field_id.to_string(),
                    &cut,
                    tok,
                    icedtea::typo::FontFace::Ui,
                )
            };
            plain
        }
    }
}

fn inset_body<'a>(inner: Element<'a, Message>, hud: &'a Hud) -> Element<'a, Message> {
    let tea = hud.tokens();
    container(inner)
        .padding(tea.density.inset())
        .width(Length::Fill)
        .style(move |_| icedtea::style::card(tea, false))
        .into()
}

fn jump_control(
    msg: Message,
    _color: Color,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    // Chip, not Canvas: one 16px canvas program per closed card is still
    // more draw work than a text chip.
    icedtea::widget::tooltip_wrap(
        chip_btn("→".into(), msg, tea),
        "Go to Timeline",
        icedtea::widget::TooltipAnchor::Follow,
        tea,
        A11y::button("Go to Timeline"),
    )
}

const POP_OUT_MARK: &[u8] = br#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><path fill="black" d="M3 6h7v7H3zM7 3h6v6h-2V5H7z"/></svg>"#;

fn pop_out_control(
    _tok: icedtea::theme::Tokens,
    tea: icedtea::theme::Tokens,
) -> Element<'static, Message> {
    icedtea::widget::tooltip_wrap(
        icedtea::widget::icon_button(
            icedtea::icon::Glyph::Bytes(POP_OUT_MARK),
            Some(Message::PopOutWindow),
            tea,
            Variant::Ghost,
            icedtea::widget::ControlSize::Default,
            A11y::button("Pop out"),
        ),
        "Open a desktop window",
        icedtea::widget::TooltipAnchor::Follow,
        tea,
        A11y::button("Pop out"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pop_out_uses_icedtea_icon_bytes() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        assert!(prod.contains("Glyph::Bytes(POP_OUT_MARK)"));
        assert!(!prod.contains("struct PopOutIcon"));
        let _ = pop_out_control(tea(), tea());
    }

    #[test]
    fn plain_card_text_strips_markdown_markers() {
        assert_eq!(
            plain_card_text("You are an **adversarial** verifier"),
            "You are an adversarial verifier"
        );
        assert_eq!(plain_card_text("see `code` and __x__"), "see code and x");
    }

    #[test]
    fn closed_faces_are_plain_text_not_markdown() {
        let _ = prompt_face("# heading\n\n**bold**", tea());
        let _ = prompt_face("plain sentence", tea());
        let _ = prompt_face("", tea());
        let _ = closed_turn_face("user said hello", tea());
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let face = prod
            .split("fn prompt_face")
            .nth(1)
            .expect("prompt_face")
            .split("fn plain_face")
            .next()
            .expect("body");
        assert!(
            !face.contains("md_body"),
            "closed faces must not parse markdown per row"
        );
        assert!(prod.contains("fn plain_face"));
    }

    fn event_title_meta(ev: &TimelineEvent) -> String {
        let mut bits: Vec<String> = Vec::new();
        if let Some(turn) = ev.turn_index {
            bits.push(format!("turn {turn}"));
        }
        let time = ev.time.trim();
        if !time.is_empty() {
            bits.push(time.to_string());
        }
        bits.join(" · ")
    }

    #[test]
    fn event_heading_has_type_turn_and_time() {
        let ev = TimelineEvent {
            index: 12,
            event_type: "user_message_chunk".into(),
            type_label: "user message chunk".into(),
            kind: "user".into(),
            time: "10:32".into(),
            turn_index: Some(2),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&ev), "turn 2 · 10:32");
        let no_turn = TimelineEvent {
            index: 12,
            kind: "user".into(),
            time: "10:32".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&no_turn), "10:32");
        let bare = TimelineEvent {
            index: 3,
            kind: "user".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_title_meta(&bare), "");
        assert_eq!(
            event_type_human(&ev),
            "user message chunk",
            "human type sits on the heading with #index"
        );
        assert_eq!(event_title_meta(&ev), "turn 2 · 10:32");
        let painted = event_type_paint(&ev).expect("type");
        assert_eq!(painted.0, "user message chunk");
        assert_eq!(painted.1, BrandRole::Cream);
        let _ = event_list_heading(&ev, tea());
        let tool = TimelineEvent {
            index: 4,
            event_type: "tool_call".into(),
            type_label: "tool call".into(),
            kind: "tool".into(),
            tool_name: "read_file".into(),
            preview: "src/app.rs".into(),
            ..TimelineEvent::default()
        };
        assert_eq!(event_tool_role(&tool), BrandRole::Cream);
        let _ = event_face(&tool, tea());
        let prod = include_str!("view.rs")
            .split("#[cfg(test)]")
            .next()
            .expect("prod");
        let body = prod
            .split("fn event_body")
            .nth(1)
            .expect("event_body")
            .split("fn finding_jump")
            .next()
            .expect("body");
        assert!(
            !body.contains("event_type_human"),
            "detail body must not repeat the chrome type line"
        );
        assert!(prod.contains("fn event_detail_chrome"));
    }

    #[test]
    fn turns_tab_is_fixed_cards_with_search() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod");
        let turns = prod
            .split("fn turns_tab")
            .nth(1)
            .expect("turns_tab")
            .split("fn timeline_tab")
            .next()
            .expect("turns body");
        assert!(turns.contains("turn_list_card"));
        assert!(turns.contains("turns_filter"));
        assert!(!turns.contains("expand_card"));
        assert!(!turns.contains("fn turn_body"));
    }

    #[test]
    fn chip_btn_builds_unsized_chip_buttons() {
        let hud = Hud::default();
        let _ = chip_btn("Add note".into(), Message::ResetDraft, tea());
        let _ = chip_btn("f2".into(), Message::SetTab(Tab::Findings), tea());
        let _ = card_chips(
            &hud,
            Some(CardMark {
                findings: 2,
                notes: 1,
                errors: 0,
                first_finding_event: Some(3),
                first_note_id: "n1".into(),
            }),
            Some(Message::ResetDraft),
            None,
        );
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod source");
        let chip = prod
            .split("fn chip_btn_icons")
            .nth(1)
            .expect("chip_btn_icons")
            .split("fn command_end")
            .next()
            .expect("chip_btn body");
        assert!(chip.contains("widget::chip"));
        assert!(prod.contains("Glyph::Bytes"));
        assert!(chip.contains("Some(msg)"));
        assert!(chip.contains("Variant::Chip"));
        assert!(!chip.contains("themed_button"));
        assert!(!chip.contains("Fixed(22"));
        assert!(!chip.contains("mouse_area"));
    }

    fn tea() -> icedtea::theme::Tokens {
        icedtea::theme::named("dark").tokens
    }

    #[test]
    fn empty_loading_and_select_use_icedtea_status() {
        let _ = empty_sessions(tea());
        let _ = busy_pane();
        let _ = select_session(tea());
        let _ = status_copy("control socket down · run: groket serve -d", true, tea());
        let _ = status_copy("12 sessions · ready", false, tea());
        let prod = include_str!("view.rs")
            .split("#[cfg(test)]")
            .next()
            .expect("prod");
        assert!(prod.contains("fn busy_pane"));
        assert!(prod.contains("page_busy()"));
        assert!(prod.contains("busy_overlay"));
        assert!(!prod.contains("fn loading_session"));
        assert!(!prod.contains("Loading events…"));
        assert!(!prod.contains("Loading matching events…"));
        assert!(!prod.contains("Loading event…"));
        assert!(!prod.contains("\"Loading…\""));
    }

    #[test]
    fn timeline_filter_and_empty_list_build_from_hud() {
        let hud = Hud::default();
        assert!(hud.sessions().is_empty());
        let _ = timeline_filter(&hud);
        let _ = session_picker_at(&hud, 400.0);
        let _ = layout(&hud);
        let src = include_str!("view.rs");
        assert!(
            src.contains("Search events…"),
            "timeline filter keeps search"
        );
        // Search is a second row so pick lists cannot crush the field.
        let filter_src = src
            .split("fn timeline_filter")
            .nth(1)
            .unwrap_or("")
            .split("fn overview_tab")
            .next()
            .unwrap_or("");
        assert!(
            filter_src.contains("column![picks, search]"),
            "search must not share the picks row"
        );
        assert!(
            filter_src.contains("timeline_count_caption"),
            "empty range must not paint a11y name"
        );
        assert!(
            filter_src.contains("themed_switch"),
            "live Tail switch sits on the Timeline filter bar"
        );
        assert!(src.contains("kit::pane_tabs"), "session-gated tabs");
    }

    #[test]
    fn code_inset_pretty_prints_json_through_icedtea() {
        let mut hud = Hud::default();
        hud.bind_field("code.json", r#"{ "a": 1 }"#);
        hud.bind_field("code.plain", "not json");
        let _ = code_inset(&hud, "code.json", "", "json", true, tea());
        let _ = code_inset(&hud, "code.plain", "", "py", true, tea());
        let _ = code_inset(&hud, "missing", "fallback body", "txt", true, tea());
    }

    #[test]
    fn tool_image_uses_slot_for_missing_and_present_files() {
        let missing = tool_image("/no/such/groket-hud-image.png", tea());
        let _ = missing;
        let path = std::env::temp_dir().join("groket-hud-tool-image.txt");
        std::fs::write(&path, b"px").expect("temp image stand-in");
        let _ = tool_image(path.to_str().expect("utf8 path"), tea());
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn session_status_tones_stay_distinct_and_readable() {
        assert_eq!(tone_variant("complete"), Variant::Success);
        assert_eq!(tone_variant("running"), Variant::Warning);
        assert_eq!(tone_variant("awaiting"), Variant::Quiet);
        assert_eq!(tone_variant("ending"), Variant::Quiet);
        assert_eq!(tone_variant("cancelled"), Variant::Danger);
        let _ = status_chip("complete", "complete", tea());
        let _ = status_chip("running", "running", tea());
    }

    #[test]
    fn virtual_column_sub_row_wheel_does_not_publish_scroll() {
        use iced::advanced::clipboard;
        use iced::advanced::layout::{Layout, Limits};
        use iced::advanced::widget::Tree;
        use iced::mouse;
        use iced::widget::Id;
        use iced::{Event, Font, Pixels, Point, Rectangle, Size};
        use icedtea::collection::VisibleWindow;
        use icedtea::widget::{label, virtual_column};

        let tok = tea();
        let row_h = crate::live::CLOSED_TURN_CARD_H;
        let viewport = 400.0;
        let heights: Vec<f32> = (0..40).map(|_| row_h).collect();
        let window = VisibleWindow::new(viewport);
        let mut el: iced::Element<'_, VisibleWindow> = virtual_column(
            &heights,
            window,
            TURNS_OVERSCAN,
            None,
            |w| w,
            Some(Id::new("hud-turns")),
            tok,
            |i| label(format!("turn {i}"), tok, A11y::new("r", Role::ListItem)),
            A11y::new("Turns", Role::List),
        );
        let mut tree = Tree::new(el.as_widget());
        let renderer = iced::Renderer::Secondary(iced_tiny_skia::Renderer::new(
            Font::DEFAULT,
            Pixels::from(16u32),
        ));
        let limits = Limits::new(Size::ZERO, Size::new(320.0, viewport));
        let node = el.as_widget_mut().layout(&mut tree, &renderer, &limits);
        let layout = Layout::new(&node);
        let origin = layout.bounds();
        let over = Point::new(origin.x + 20.0, origin.center_y());
        let vp = Rectangle::new(Point::ORIGIN, Size::new(320.0, viewport));
        let mut clipboard = clipboard::Null;
        let mut messages = Vec::new();
        {
            let mut shell = iced::advanced::Shell::new(&mut messages);
            el.as_widget_mut().update(
                &mut tree,
                &Event::Mouse(mouse::Event::WheelScrolled {
                    delta: mouse::ScrollDelta::Pixels { x: 0.0, y: -4.0 },
                }),
                layout,
                mouse::Cursor::Available(over),
                &renderer,
                &mut clipboard,
                &mut shell,
                &vp,
            );
        }
        assert!(
            messages.is_empty(),
            "a 4px wheel must stay in virtual_clip, got {messages:?}"
        );
    }

    #[test]
    fn session_picker_is_spotlight_not_list_detail_rail() {
        let src = include_str!("view.rs");
        let prod = src.split("#[cfg(test)]").next().expect("prod source");
        assert!(prod.contains("fn session_picker"));
        assert!(prod.contains("browse_mode()"));
        assert!(prod.contains("fn browse_session_bar"));
        assert!(prod.contains("Message::SessionsHome"));
        assert!(prod.contains("fn status_chip"));
        assert!(prod.contains("widget::badge"));
        assert!(prod.contains("BadgeSize::Small"));
        assert!(prod.contains("fn session_state_row"));
        assert!(prod.contains("fn session_state_from_meta"));
        assert!(prod.contains("widget::virtual_column"));
        assert!(!prod.contains("QuietColumn"));
        assert!(!prod.contains("fn tea_two_line"));
        assert!(!prod.contains("fn tea_list_view"));
        assert!(!prod.contains("SESSION_LIST_W"));
        assert!(!prod.contains("pattern::list_detail"));
        assert!(prod.contains("widget::rule_h"));
        assert!(prod.contains("widget::tooltip_wrap"));
        assert!(prod.contains("ControlSize::Default"));
        assert!(prod.contains("themed_pick_list"));
        assert!(prod.contains("TreeFace::Files"));
        assert!(prod.contains("icedtea::widget::themed_text_input"));
        assert!(
            prod.contains("icedtea::widget::highlighted_code"),
            "tool code panes must use iced highlighter, not plain mono code_block"
        );
        assert!(prod.contains("icedtea::widget::selectable"));
        // Overview KV via kit (icedtea value_field + FORM_LABEL gutter).
        assert!(prod.contains("kit::labeled_value"));
        assert!(prod.contains("kit::labeled_plain"));
        assert!(prod.contains("kit::context_progress"));
        assert!(prod.contains("kit::pane_tabs"));
        assert!(prod.contains("icedtea::widget::search_input"));
        assert!(!prod.contains("kit::search_field"));
        assert!(prod.contains("pattern::status_bar"));
        assert!(!prod.contains("kit::status_footer"));
        assert!(!prod.contains("Message::ShowJobLog"));
        assert!(!prod.contains("job.inspect"));
        assert!(prod.contains("kit::help_modal"));
        assert!(prod.contains("pattern::drawer"));
        assert!(prod.contains("kit::status_empty"));
        assert!(prod.contains("help_open()"));
        assert!(prod.contains("overview_fields"));
        let overview = prod
            .split("fn overview_tab")
            .nth(1)
            .expect("overview_tab")
            .split("fn kv")
            .next()
            .expect("overview body");
        assert!(prod.contains("overview_section_tabs"));
        assert!(!overview.contains("overview_section_tabs"));
        assert!(overview.contains("session_state_from_meta("));
        assert!(overview.contains("overview.summary"));
        assert!(overview.contains("select_bound"));
        assert!(!overview.contains("markdown_bound"));
        assert!(overview.contains("overview_task_rows"));
        assert!(overview.contains("overview_workflow_rows"));
        assert!(overview.contains("overview_subagent_rows"));
        assert!(overview.contains("widget::virtual_column"));
        assert!(overview.contains("widget::data_table"));
        assert!(overview.contains("OVERVIEW_LIST_OVERSCAN"));
        assert!(!overview.contains("overview_run_jumps"));
        assert!(!overview.contains("\"{} · {} · {}\""));
        let picker = prod
            .split("fn session_picker_at")
            .nth(1)
            .expect("session_picker_at")
            .split("fn detail_pane")
            .next()
            .expect("picker body");
        assert!(picker.contains("widget::virtual_column"));
        assert!(picker.contains("session_list_card("));
        let detail = prod
            .split("fn detail_pane")
            .nth(1)
            .expect("detail_pane")
            .split("fn timeline_filter")
            .next()
            .expect("detail body");
        assert!(detail.contains("overview_virtual_body"));
        assert!(detail.contains("turns_tab(hud)"));
        let bar = prod
            .split("fn browse_session_bar")
            .nth(1)
            .expect("browse_session_bar")
            .split("fn timeline_filter")
            .next()
            .expect("bar body");
        assert!(bar.contains("session_state_from_meta("));
        assert!(prod.contains("fn select_bound"));
        assert!(prod.contains("event.{}.in.{}"));
        assert!(prod.contains("icedtea::widget::image_slot"));
        assert!(prod.contains("icedtea::widget::busy_overlay"));
        assert!(prod.contains("kit::status_empty"));
        assert!(prod.contains("icedtea::widget::info_bar"));
        assert!(prod.contains("fn diff_chrome"));
        assert!(prod.contains("fn diff_context_body"));
        assert!(prod.contains("fn diff_context_tabs"));
        assert!(prod.contains("fn diff_split"));
        assert!(prod.contains("widget::tree_view"));
        assert!(prod.contains("fn diff_search"));
        assert!(prod.contains("Message::DiffPointPicked"));
        assert!(prod.contains("\"diff.prompt\""));
        assert!(prod.contains("\"diff.assistant\""));
        assert!(prod.contains("BodyPaint::Markdown =>"));
        assert!(!prod.contains("chat_md_body"));
        assert!(!prod.contains("iced::widget::markdown::view"));
        assert!(prod.contains("widget::markdown_view"));
        assert!(prod.contains("icedtea::motion::overlay"));
        assert!(prod.contains("Slide::Up"));
        assert!(prod.contains("page_slide()"));
        assert!(prod.contains("fn page_body"));
        assert!(prod.contains("overlay_moving()"));
        assert!(prod.contains("page_moving()"));
        assert!(prod.contains("fn expand_card"));
        assert!(prod.contains("fn card_actions"));
        assert!(prod.contains("fn card_chips"));
        assert!(prod.contains("fn command_end"));
        assert!(prod.contains("Add note"));
        // Overview path is selectable; no in-pane Copy path button.
        assert!(!prod.contains("fn overview_commands"));
        assert!(prod.contains("format!(\"f{}\""));
        assert!(prod.contains("format!(\"n{}\""));
        assert!(!prod.contains("Tab fields"));
        assert!(!prod.contains("Ctrl+1–5"));
        assert!(!prod.contains("hotkey_hint()"));
        assert!(prod.contains("themed_button("));
        assert!(prod.contains("Variant::Chip"));
        assert!(!prod.contains("themed_button_sized"));
        assert!(prod.contains("fn jump_control"));
        assert!(prod.contains("Go to Timeline"));
        assert!(!prod.contains("struct JumpIcon"));
        assert!(!prod.contains("chip_btn(\"Timeline\""));
        assert!(prod.contains("pattern::command_bar"));
        assert!(prod.contains("TURNS_OVERSCAN"));
        assert!(prod.contains("widget::virtual_column"));
        assert!(prod.contains("turns_tab(hud)"));
        assert!(!prod.contains("context_progress") || prod.contains("kit::context_progress"));
        assert!(prod.contains("pattern::context_menu"));
        assert!(prod.contains("stack![chrome]"));
        assert!(prod.contains("fn turn_note"));
        assert!(!prod.contains("command_palette_view"));
        assert!(prod.contains("fn event_body"));
        assert!(!prod.contains("time_picker"));
        assert!(!prod.contains("fn drawer"));
        assert!(!prod.contains("fn disclosure"));
        assert!(prod.contains("fn select_bound"));
        assert!(prod.contains("fn turn_list_card"));
        assert!(prod.contains("fn closed_turn_face"));
        assert!(prod.contains("Search events…"));
        assert!(prod.contains("Search turns"));
        assert!(!prod.contains("Session events"));
        assert!(prod.contains("fn prompt_face"));
        assert!(!prod.contains("visual_lines("));
        assert!(!prod.contains(".height(height)"));
        assert!(prod.contains("matched in {}:"));
        assert!(prod.contains("fn brand_variant"));
        assert!(!prod.contains("accordion_view"));
        assert!(prod.contains("widget::expander"));
        assert!(prod.contains("finding_expand_progress"));
        assert!(prod.contains("note_expand_progress"));
        assert!(!prod.contains("if open { 1.0 } else { 0.0 }"));
        assert!(prod.contains("Peek::Lines(2)"));
        assert!(prod.contains("fn closed_list_card"));
        assert!(prod.contains("fn event_detail_pane"));
        assert!(prod.contains("fn event_pager"));
        let pager = prod
            .split("fn event_pager")
            .nth(1)
            .expect("event_pager")
            .split("fn diff_tab")
            .next()
            .expect("pager body");
        assert!(pager.contains("\"Previous\""));
        assert!(pager.contains("\"Next\""));
        assert!(pager.contains("Space::new().width(Length::Fill)"));
        assert!(!pager.contains("{at} of {n}"));
        assert!(!prod.contains("fn neighbor_link"));
        assert!(!prod.contains("‹ {name}"));
        assert!(!prod.contains("{name} ›"));
        assert!(prod.contains("fn event_list_heading"));
        assert!(prod.contains("fn event_type_paint"));
        assert!(prod.contains("fn label_badge"));
        assert!(prod.contains("fn brand_variant"));
        let heading = prod
            .split("fn event_list_heading")
            .nth(1)
            .expect("heading")
            .split("fn event_face")
            .next()
            .expect("heading body");
        assert!(heading.contains("label_badge"));
        assert!(heading.contains("status_chip(format!(\"turn {turn}\")"));
        let payload = prod
            .split("fn event_payload")
            .nth(1)
            .expect("event_payload")
            .split("fn field_body")
            .next()
            .expect("payload body");
        assert!(payload.contains("job_event_inspect"));
        assert!(payload.contains("workflow_event_inspect"));
        assert!(payload.contains("label_badge("));
        let wf_card = prod
            .split("fn workflow_event_inspect")
            .nth(1)
            .expect("workflow_event_inspect")
            .split("fn job_event_inspect")
            .next()
            .expect("workflow card");
        assert!(wf_card.contains("wf.child"));
        assert!(wf_card.contains("Asked"));
        assert!(wf_card.contains("Happened"));
        assert!(wf_card.contains("Failed"));
        assert!(wf_card.contains("select_bound"));
        assert!(wf_card.contains("OpenChild"));
        let job_card = prod
            .split("fn job_event_inspect")
            .nth(1)
            .expect("job_event_inspect")
            .split("fn event_payload")
            .next()
            .expect("job card");
        assert!(job_card.contains("code_inset"));
        assert!(job_card.contains("\"bash\""));
        assert!(job_card.contains("job_status"));
        assert!(job_card.contains("job_event_id"));
        assert!(job_card.contains("job_exit_code"));
        assert!(job_card.contains("job_inspect_blocks"));
        assert!(job_card.contains("schedule_inspect_blocks"));
        let sub_card = prod
            .split("fn event_body")
            .nth(1)
            .expect("event_body")
            .split("fn event_payload")
            .next()
            .expect("event_body slice");
        assert!(sub_card.contains("subagent_inspect_blocks"));
        assert!(job_card.contains("schedule_last_fire"));
        assert!(!job_card.contains("get(\"last_fired_at\")"));
        assert!(payload.contains("\"Input\""));
        assert!(!payload.contains("text(format_tool_display"));
        let stats = prod
            .split("fn turn_stats_row")
            .nth(1)
            .expect("turn_stats_row")
            .split("fn turn_run_chips")
            .next()
            .expect("stats body");
        assert!(stats.contains("status_chip("));
        assert!(!stats.contains("tools ·"));
        let face = prod
            .split("fn event_face")
            .nth(1)
            .expect("face")
            .split("fn event_body")
            .next()
            .expect("face body");
        assert!(face.contains("label_badge"));
        assert!(face.contains(".size(tea.body())"));
        assert!(!face.contains(".size(tea.meta())"));
        assert!(!face.contains("id_font"));
        assert!(prod.contains("footer_table_for(hud.key_scope(), hud.key_overlay())"));
        assert!(!prod.contains("chip_btn(\"Back\""));
        assert!(!prod.contains("is_timeline_expanded"));
        assert!(!prod.contains("TurnExpand"));
        assert!(!prod.contains("fn turn_body"));
        assert!(prod.contains("FindingExpand"));
        assert!(prod.contains("NoteExpand"));
    }

    #[test]
    fn empty_findings_copy_is_visible_one_line() {
        let (title, hint) = findings_empty_copy();
        assert_eq!(title, "No findings");
        assert!(!hint.is_empty());
        let src = include_str!("view.rs");
        let body = src.split("fn findings_tab").nth(1).unwrap_or("");
        assert!(
            body.contains("kit::status_empty(title, hint, tea)"),
            "empty Findings uses the one-line empty state"
        );
        assert!(
            !body
                .split("let mut buckets")
                .next()
                .unwrap_or(body)
                .contains("status_page"),
            "empty Findings is not a blank status_page"
        );
    }
}
