//! iced application: state, RPC, hotkey, live poll.

use std::collections::{HashSet, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use iced::keyboard::{key::Named, Key, Modifiers as KeyMods};
use iced::widget::operation;
use iced::widget::Id;
use iced::window::{self, Mode};
use iced::{event, keyboard, time, Element, Event, Pixels, Point, Size, Subscription, Task, Theme};
use serde_json::{json, Value};

use crate::control::{self, ControlError};
use crate::format::{
    control_down_message, event_body_text, extract_event, extract_turn, list_status_label,
    new_note_id, tool_fields_from_raw,
};
use crate::fuzzy::session_search_indices;
use crate::live::{
    card_marks_from_overview, clamp_scroll, filter_timeline_indices, filter_turn_indices,
    first_list_fetch, is_partial_list_page, is_soft_notes_save_error, list_scroll_to_cover,
    list_scroll_to_top, merge_catalog_rows, merge_timeline_by_index, next_list_offset,
    notes_schema_fields, patch_catalog_delta, patch_list_row_from_meta, plan_tick,
    previous_timeline_page, scroll_after_prepend, session_card_height, session_needs_live_poll,
    session_row_meta, session_rpc_ref, should_fetch_timeline, should_load_previous_timeline,
    spotlight_recent, timeline_coverage_complete, timeline_page_next, timeline_range_label,
    timeline_window_start, trim_timeline_buffer, CardMark, TickInput, CLOSED_TURN_CARD_H,
    IDLE_POLL_MS, LIVE_POLL_MS, LIVE_TAIL_LIMIT, SPOTLIGHT_RECENT, TIMELINE_BUFFER_CAP,
    TIMELINE_CHUNK, TIMELINE_OPEN_CHARS, TIMELINE_PREVIEW_CHARS, TIMELINE_ROW_H,
};
use crate::model::{EventsTurnPick, KindFilter, NoteDraft, SchemaField, SessionRow, Tab};
use crate::place;
use crate::prefs;
use crate::shortcut;
use crate::theme;
use crate::view;
use crate::wire::{
    decode_overview, decode_session_list, decode_session_list_response, decode_timeline_page,
    FindingRow, NotesBlock, Overview, TimelineEvent,
};

const HUD_W: f32 = 780.0;
const HUD_H: f32 = 560.0;
const APP_ID: &str = "dev.indynull.groket-hud";
const OVERLAY_APP_ID: &str = "dev.indynull.groket-hud.overlay";

/// Which edge event to open after a turn-scoped pager crosses a turn boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DetailTurnEdge {
    First,
    Last,
}

#[derive(Debug, Clone)]
pub enum Message {
    SearchChanged(String),
    SelectSession(usize),
    SetTab(Tab),
    TimelineQuery(String),
    TimelineKind(KindFilter),
    JumpTimeline(i64),
    /// Events pane turn pick list (`None` key = all turns / search).
    EventsTurnPicked(EventsTurnPick),
    SelectTimeline(i64),
    /// Leave full-pane event detail and return to the timeline list.
    CloseTimelineDetail,
    /// Step full-pane detail by *delta* rows in the current filter (−1 / +1).
    TimelineDetailStep(i32),
    /// Turns tab search (label / prompt substring).
    TurnsQuery(String),
    LoadMoreTimeline,
    StartNote {
        turn: String,
        event: String,
    },
    OpenNote(String),
    ResetDraft,
    NoteField {
        id: String,
        value: String,
    },
    NoteTurn(String),
    SaveNote,
    RequestDelete(String),
    PopOutWindow,
    Hotkey,
    Tick,
    FocusSearch(u8),
    RawEvent(Event),
    Inited(Result<String, String>),
    ListLoaded {
        quiet: bool,
        result: Result<Value, String>,
    },
    ListPage {
        offset: u32,
        result: Result<Value, String>,
    },
    OverviewLoaded {
        gen: u64,
        sid: String,
        quiet: bool,
        result: Result<Value, String>,
    },
    TimelineLoaded {
        gen: u64,
        sid: String,
        offset: u32,
        append: bool,
        advance: bool,
        result: Result<Value, String>,
    },
    NoteSaved(Result<Value, String>),
    NoteDeleted {
        id: String,
        result: Result<Value, String>,
    },
    WindowId(Option<window::Id>),
    WindowPos(Option<Point>),
    X11Focus {
        xid: u64,
        attempt: u8,
    },
    Hide,
    CloseRequested(window::Id),
    Tray(crate::tray::TrayAction),
    Summon(crate::summon::SummonRequest),
    ActivationApplied(bool),
    MdLink(String),
    ListScroll(icedtea::collection::VisibleWindow),
    TimelineScroll(icedtea::collection::VisibleWindow),
    TurnScroll(icedtea::collection::VisibleWindow),
    /// Enter: open the selected session (works while search is focused).
    ActivateSelected,
    TimelineSearchApply(u64),
    FindingExpand {
        id: String,
        open: bool,
    },
    NoteExpand {
        id: String,
        open: bool,
    },
    FollowDraft(String),
    SendFollow,
    MarkDone,
    CopyPath,
    CopyText(String),
    Yank,
    Cursor(icedtea::layout::CursorEvent),
    ContextDismiss,
    WindowSize(Size),
    Select {
        id: String,
        action: iced::widget::text_editor::Action,
    },
    ToastDismiss(u64),
    FollowDone(Result<Value, String>),
    /// Discard — close handlers and contribution-shaped tab chrome.
    Noop,
}

/// One selectable body buffer (expanded event or turn text).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ExtractKey {
    Event(i64),
    Overview(&'static str),
}

impl ExtractKey {
    /// Bind id for [`icedtea::field::Selectables`].
    pub fn id(self) -> String {
        match self {
            Self::Event(i) => format!("event.{i}"),
            Self::Overview(k) => format!("overview.{k}"),
        }
    }
}

pub struct Hud {
    query: String,
    all_sessions: Vec<SessionRow>,
    sessions: Vec<SessionRow>,
    active: usize,
    tab: Tab,
    overview: Option<Overview>,
    overview_sid: String,
    overview_pending: String,
    overview_gen: u64,
    timeline: Vec<TimelineEvent>,
    timeline_sid: String,
    timeline_total: u32,
    timeline_offset: u32,
    timeline_next: u32,
    timeline_gen: u64,
    timeline_loading: bool,
    timeline_query: String,
    timeline_query_draft: String,
    timeline_search_pending: bool,
    timeline_kind: KindFilter,
    timeline_focus: Option<i64>,
    /// Full-pane event detail on Timeline (not an in-list expander).
    timeline_open: Option<i64>,
    /// After a turn-boundary step, open first/last event once the page loads.
    detail_turn_edge: Option<DetailTurnEdge>,
    /// `session/timeline` promptIndex filter (operator meta on the turn).
    timeline_prompt: Option<i64>,
    /// Events pane turn pick (`None` = all turns / search-all).
    events_turn_index: Option<i64>,
    /// Options for the Events turn pick list (owned for iced pick_list).
    events_turn_options: Vec<EventsTurnPick>,
    last_timeline: Option<LastTimelineReq>,
    note_draft: NoteDraft,
    note_compose_lock: bool,
    note_saving: bool,
    note_delete_armed: String,
    note_delete_until: Option<Instant>,
    status: String,
    status_err: bool,
    hotkey_hint: String,
    window_mode: bool,
    visible: bool,
    palette_live: bool,
    palette_origin: Option<Point>,
    last_live: Instant,
    typing_notes: bool,
    search_id: Id,
    tl_search_id: Id,
    theme_name: String,
    _hotkeys: Option<GlobalHotKeyManager>,
    _tray: Option<crate::tray::HudTray>,
    _summon: Option<crate::summon::SummonServer>,
    notify_q: Arc<Mutex<VecDeque<(String, Value)>>>,
    window_id: Option<window::Id>,
    catalog_revision: i64,
    list_window: icedtea::collection::VisibleWindow,
    list_scroll_id: Id,
    list_selection: icedtea::collection::Selection,
    session_metas: Vec<String>,
    session_heights: Vec<f32>,
    tl_window: icedtea::collection::VisibleWindow,
    tl_heights: Vec<f32>,
    tl_scroll_id: Id,
    turn_window: icedtea::collection::VisibleWindow,
    turn_heights: Vec<f32>,
    turn_scroll_id: Id,
    tl_filter: Vec<usize>,
    turn_marks: std::collections::HashMap<i64, CardMark>,
    event_marks: std::collections::HashMap<i64, CardMark>,
    seen_status: std::collections::HashMap<String, String>,
    notice_stable: std::collections::HashSet<String>,
    notices_primed: bool,
    seen_analysis: std::collections::HashMap<String, String>,
    toasts: icedtea::toast::ToastQueue,
    last_tick: Instant,
    spin_phase: f32,
    catalog_busy: bool,
    findings_open: HashSet<String>,
    notes_open: HashSet<String>,
    /// Last Turns card focused (jump / `g` / yank).
    turns_focus: Option<i64>,
    turns_query: String,
    turns_filter: Vec<usize>,
    turns_search_id: Id,
    follow_draft: String,
    timeline_search_gen: u64,
    fields: icedtea::field::Selectables,
    pointer: Point,
    context: Option<Point>,
    window_size: Size,
    /// Last show/toggle xdg-activation token; cleared on hide or successful activate.
    pending_activation_token: Option<String>,
}

impl Default for Hud {
    fn default() -> Self {
        Self {
            query: String::new(),
            all_sessions: vec![],
            sessions: vec![],
            active: 0,
            tab: Tab::Overview,
            overview: None,
            overview_sid: String::new(),
            overview_pending: String::new(),
            overview_gen: 0,
            timeline: vec![],
            timeline_sid: String::new(),
            timeline_total: 0,
            timeline_offset: 0,
            timeline_next: 0,
            timeline_gen: 0,
            timeline_loading: false,
            timeline_query: String::new(),
            timeline_query_draft: String::new(),
            timeline_search_pending: false,
            timeline_kind: KindFilter::All,
            timeline_focus: None,
            timeline_open: None,
            detail_turn_edge: None,
            timeline_prompt: None,
            events_turn_index: None,
            events_turn_options: vec![EventsTurnPick {
                turn_index: None,
                label: "All turns".into(),
            }],
            last_timeline: None,
            note_draft: NoteDraft::default(),
            note_compose_lock: false,
            note_saving: false,
            note_delete_armed: String::new(),
            note_delete_until: None,
            status: "connecting…".into(),
            status_err: false,
            hotkey_hint: shortcut::default_shortcut_label().into(),
            window_mode: std::env::var_os("GROKET_HUD_WINDOW").is_some(),
            visible: true,
            palette_live: true,
            palette_origin: None,
            last_live: Instant::now(),
            typing_notes: false,
            search_id: Id::new("search"),
            tl_search_id: Id::new("tl-search"),
            theme_name: prefs::theme_name(),
            _hotkeys: None,
            _tray: None,
            _summon: None,
            notify_q: Arc::new(Mutex::new(VecDeque::new())),
            catalog_revision: 0,
            window_id: None,
            list_window: icedtea::collection::VisibleWindow::new(400.0),
            list_scroll_id: Id::new("hud-sessions"),
            list_selection: icedtea::collection::Selection::None,
            session_metas: vec![],
            session_heights: vec![],
            tl_window: icedtea::collection::VisibleWindow::new(400.0),
            tl_heights: vec![],
            tl_scroll_id: Id::new("hud-timeline"),
            turn_window: icedtea::collection::VisibleWindow::new(400.0),
            turn_heights: vec![],
            turn_scroll_id: Id::new("hud-turns"),
            tl_filter: vec![],
            turn_marks: std::collections::HashMap::new(),
            event_marks: std::collections::HashMap::new(),
            seen_status: std::collections::HashMap::new(),
            notice_stable: std::collections::HashSet::new(),
            notices_primed: false,
            seen_analysis: std::collections::HashMap::new(),
            toasts: icedtea::toast::ToastQueue::new(),
            last_tick: Instant::now(),
            spin_phase: 0.0,
            catalog_busy: false,
            findings_open: HashSet::new(),
            notes_open: HashSet::new(),
            turns_focus: None,
            turns_query: String::new(),
            turns_filter: vec![],
            turns_search_id: Id::new("turns-search"),
            follow_draft: String::new(),
            timeline_search_gen: 0,
            fields: icedtea::field::Selectables::new(),
            pointer: Point::ORIGIN,
            context: None,
            window_size: if std::env::var_os("GROKET_HUD_WINDOW").is_some() {
                Size::new(980.0, 700.0)
            } else {
                Size::new(HUD_W, HUD_H)
            },
            pending_activation_token: None,
        }
    }
}

fn write_os_clipboard(text: &str) {
    if text.is_empty() {
        return;
    }
    #[cfg(target_os = "linux")]
    {
        use std::io::Write;
        use std::process::{Command, Stdio};
        let jobs: &[(&str, &[&str])] = &[
            ("wl-copy", &[]),
            ("xclip", &["-selection", "clipboard", "-in"]),
            ("xclip", &["-selection", "primary", "-in"]),
            ("xsel", &["--clipboard", "--input"]),
        ];
        for (bin, args) in jobs {
            let Ok(mut child) = Command::new(bin)
                .args(*args)
                .stdin(Stdio::piped())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
            else {
                continue;
            };
            if let Some(mut stdin) = child.stdin.take() {
                let _ = stdin.write_all(text.as_bytes());
            }
            std::thread::spawn(move || {
                let _ = child.wait();
            });
        }
    }
    let _ = text;
}

fn apply_hud_chrome(prep: &mut icedtea::app::Prepared) {
    prep.window.icon = crate::brand::window_icon();
    prep.iced_settings.fonts = vec![
        std::borrow::Cow::Borrowed(crate::typo::UI_BYTES),
        std::borrow::Cow::Borrowed(crate::typo::MONO_BYTES),
    ];
    prep.iced_settings.default_font = crate::typo::UI;
    prep.iced_settings.default_text_size = Pixels::from(crate::typo::BODY);
}

fn overlay_prepared() -> icedtea::app::Prepared {
    let boot = icedtea::app::Boot::new("groket", OVERLAY_APP_ID)
        .overlay()
        .size(HUD_W, HUD_H)
        .min_size(HUD_W, HUD_H)
        .max_size(HUD_W, HUD_H)
        .theme(prefs::theme_name());
    let mut prep = icedtea::app::bootstrap_with_catalog(&boot, crate::theme::catalog());
    apply_hud_chrome(&mut prep);
    prep
}

fn desktop_prepared() -> icedtea::app::Prepared {
    let mut prep = overlay_prepared();
    prep.window.size = Size::new(980.0, 700.0);
    prep.window.min_size = Some(Size::new(640.0, 440.0));
    prep.window.max_size = None;
    icedtea::window::retarget(&mut prep.window, APP_ID);
    prep.window.exit_on_close_request = false;
    prep.window.icon = crate::brand::window_icon();
    prep
}

/// Overlay is already the mapped palette: do not remap, resize, or refetch.
pub fn overlay_already_mapped(visible: bool, window_mode: bool, has_window: bool) -> bool {
    visible && !window_mode && has_window
}

/// Window mode already opens a visible decorated surface. Summoning the
/// overlay on top would keep those decorations and paint a pop-out control.
pub fn boot_summons_overlay(window_mode: bool, show_on_start: bool) -> bool {
    show_on_start && !window_mode
}

pub fn palette_window_settings() -> window::Settings {
    overlay_prepared().window
}

pub fn app_window_settings() -> window::Settings {
    desktop_prepared().window
}

fn open_hud_window(window_mode: bool) -> (window::Id, Task<window::Id>) {
    if window_mode {
        desktop_prepared().open()
    } else {
        overlay_prepared().open()
    }
}

pub fn run() -> iced::Result {
    crate::log::info(&format!("hud start log={}", crate::log::path().display()));
    if crate::summon::already_running() {
        eprintln!(
            "groket-hud: already running — groket hud --toggle / tray \
             (use groket hud --restart to replace)"
        );
        return Ok(());
    }
    // icedtea::daemon! is equivalent; catalog + dual window modes stay manual
    // via Prepared + iced::daemon.
    iced::daemon(Hud::new, Hud::update, Hud::view)
        .title("groket")
        .subscription(Hud::subscription)
        .theme(|hud: &Hud, window| Some(hud.theme(window)))
        .settings(overlay_prepared().iced_settings)
        .run()
}

#[cfg(target_os = "macos")]
pub fn set_macos_accessory() {
    crate::macoswin::set_accessory_policy();
}

#[cfg(not(target_os = "macos"))]
pub fn set_macos_accessory() {}

impl Hud {
    fn new() -> (Self, Task<Message>) {
        let mut hud = Hud::default();
        let (hk, label) = shortcut::resolve_summon_shortcut();
        hud.hotkey_hint = label.clone();
        let skip_hotkey = hud.window_mode;
        if !skip_hotkey {
            // Linux: global-hotkey is X11-only. On Wayland (incl. Xwayland)
            // registering opens Xlib and either fails or grabs keys only for
            // X11 clients — skip and point at tray / compositor binds.
            #[cfg(target_os = "linux")]
            {
                if !crate::x11focus::global_hotkey_supported() {
                    let msg = crate::x11focus::wayland_summon_hint(&label);
                    crate::log::info(&msg);
                    eprintln!("groket-hud: {msg}");
                } else {
                    hud._hotkeys = register_global_hotkey(hk, &label);
                }
            }
            #[cfg(not(target_os = "linux"))]
            {
                hud._hotkeys = register_global_hotkey(hk, &label);
            }
        }
        match crate::summon::install() {
            Ok(server) => {
                if let Some(path) = crate::summon::default_socket_path() {
                    eprintln!("groket-hud: summon socket {}", path.display());
                } else {
                    eprintln!("groket-hud: summon socket ready");
                }
                hud._summon = Some(server);
            }
            Err(crate::summon::SummonError::AlreadyRunning(path)) => {
                eprintln!("groket-hud: already running ({path}) — groket hud --toggle / tray");
                std::process::exit(0);
            }
            Err(err) => {
                // Unix is expected to bind; non-unix is Unsupported (no log noise).
                if !matches!(err, crate::summon::SummonError::Unsupported) {
                    let msg = format!("summon socket: {err}");
                    crate::log::error(&msg);
                    eprintln!("groket-hud: {msg}");
                }
            }
        }
        match crate::tray::install() {
            Ok(tray) => {
                eprintln!("groket-hud: tray ready");
                hud._tray = Some(tray);
            }
            Err(err) => {
                let msg = format!("tray: {err}");
                crate::log::error(&msg);
                eprintln!("groket-hud: {msg}");
            }
        }
        let q = hud.notify_q.clone();
        let _ = control::spawn_notify_listener(move |method, params| {
            if let Ok(mut g) = q.lock() {
                g.push_back((method, params));
                if g.len() > 64 {
                    g.pop_front();
                }
            }
        });
        // iced daemon must own a window or subscriptions (summon/tray) stall.
        // Overlay maps once; Esc / --hide destroys it so Sway rematches later.
        let (id, open) = open_hud_window(hud.window_mode);
        hud.window_id = Some(id);
        let mut boot = vec![
            open.map(|id| Message::WindowId(Some(id))),
            Task::perform(rpc(control::initialize), |r| {
                Message::Inited(r.map(|_| String::new()))
            }),
            fetch_list(false, 0),
        ];
        if boot_summons_overlay(hud.window_mode, crate::tray::show_on_start()) {
            boot.push(hud.show_palette());
        }
        (hud, Task::batch(boot))
    }

    fn theme(&self, _window: window::Id) -> Theme {
        theme::iced_theme(&self.theme_name)
    }

    fn subscription(&self) -> Subscription<Message> {
        let mut subs = vec![
            event::listen_with(interesting_hud_event),
            hotkey_subscription(),
            summon_subscription(),
            notify_subscription(),
        ];
        if self.visible {
            let any_live = session_needs_live_poll(
                &self.selected_status(),
                self.overview.as_ref().map(|o| &o.turns),
            ) || self
                .all_sessions
                .iter()
                .any(|r| session_needs_live_poll(&r.status, None));
            let poll = if any_live { LIVE_POLL_MS } else { IDLE_POLL_MS };
            subs.push(time::every(Duration::from_millis(poll)).map(|_| Message::Tick));
        }
        if self.note_delete_until.is_some() {
            subs.push(time::every(Duration::from_millis(250)).map(|_| Message::Tick));
        }
        subs.push(tray_subscription());
        subs.push(icedtea::layout::listen_cursor().map(Message::Cursor));
        Subscription::batch(subs)
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::SearchChanged(q) => {
                // Capture identity before `query` changes which list `sessions()` returns.
                let keep = self.session_keep_id();
                self.query = q;
                self.rerank_visible_keeping(keep);
                Task::none()
            }
            Message::SelectSession(i) => {
                // Capture the row before clearing the query (index is into the
                // filtered list; browse mode uses the full catalog).
                let Some(row) = self.sessions().get(i).cloned() else {
                    return self.focus_picker();
                };
                let sid = row.session_id.clone();
                if sid.is_empty() {
                    return self.focus_picker();
                }
                let same = self.overview.is_some()
                    && !self.overview_sid.is_empty()
                    && sid == self.overview_sid;
                // Spotlight: pick → clear search → full-width browse.
                self.query.clear();
                self.rerank_visible_keeping(sid.clone());
                if let Some(idx) = self.sessions().iter().position(|r| r.session_id == sid) {
                    self.set_active(idx);
                } else {
                    self.set_active(0);
                }
                if same {
                    return self.focus_browse();
                }
                self.reset_detail_chrome();
                // Loading placeholder this frame; body fills via OverviewLoaded.
                // Do not yank keyboard into session search after a pick.
                Task::batch([self.load_overview(false), self.focus_browse()])
            }
            Message::SetTab(tab) => {
                // Without an overview, secondary panes only paint "Select a session".
                // Load the rail selection first, or refuse the tab flip.
                if self.overview.is_none() && tab != Tab::Overview {
                    if self.selected_sid().is_some() {
                        self.tab = tab;
                        return Task::batch([self.load_overview(false), self.focus_browse()]);
                    }
                    self.tab = Tab::Overview;
                    return Task::none();
                }
                // Leaving Timeline drops event detail so Esc/list state stays honest.
                if tab != Tab::Timeline {
                    self.drop_timeline_detail();
                }
                self.tab = tab;
                // Keep turn scope when returning to Events. Only an explicit
                // "All turns" pick or search-all clears the drawer.
                let load = match tab {
                    Tab::Timeline => {
                        if self.wants_events() {
                            if let Some(sid) = self.detail_sid() {
                                self.ensure_timeline(sid, false)
                            } else {
                                Task::none()
                            }
                        } else {
                            Task::none()
                        }
                    }
                    Tab::Overview => Task::none(),
                    _ => Task::none(),
                };
                // Turns/Timeline keep scroll on VisibleWindow (virtual_column).
                Task::batch([load, self.focus_browse()])
            }
            Message::TimelineQuery(q) => {
                self.timeline_query_draft = q;
                self.timeline_focus = None;
                self.drop_timeline_detail();
                self.timeline_search_gen = self.timeline_search_gen.wrapping_add(1);
                // Hold the last applied page until debounce. Bump gen so an
                // in-flight fill cannot merge a new-query slice onto it.
                self.timeline_gen = self.timeline_gen.wrapping_add(1);
                self.timeline_search_pending = true;
                self.timeline_loading = true;
                let gen = self.timeline_search_gen;
                Task::perform(
                    async {
                        tokio::time::sleep(Duration::from_millis(280)).await;
                    },
                    move |()| Message::TimelineSearchApply(gen),
                )
            }
            Message::TimelineSearchApply(gen) => {
                if gen != self.timeline_search_gen {
                    return Task::none();
                }
                self.timeline_query = self.timeline_query_draft.clone();
                self.timeline_search_pending = false;
                self.timeline_loading = false;
                if !self.timeline_query.trim().is_empty() {
                    // Search-all: leave the turn pick on All.
                    self.timeline_prompt = None;
                    self.events_turn_index = None;
                }
                if let Some(sid) = self.detail_sid() {
                    if self.wants_events() {
                        return self.ensure_timeline(sid, true);
                    }
                }
                Task::none()
            }
            Message::TimelineKind(k) => {
                self.timeline_kind = k;
                self.timeline_focus = None;
                self.drop_timeline_detail();
                if let Some(sid) = self.detail_sid() {
                    if self.wants_events() {
                        return self.ensure_timeline(sid, true);
                    }
                }
                Task::none()
            }
            Message::JumpTimeline(ix) => self.jump_timeline(ix),
            Message::EventsTurnPicked(pick) => self.select_events_turn(pick.turn_index),
            Message::SelectTimeline(ix) => self.open_timeline_detail(ix),
            Message::CloseTimelineDetail => self.close_timeline_detail(),
            Message::TimelineDetailStep(delta) => self.nav_timeline_detail_step(delta),
            Message::TurnsQuery(q) => {
                self.turns_query = q;
                self.rebuild_turns_filter();
                Task::none()
            }
            Message::LoadMoreTimeline => self.load_more_timeline(),
            Message::StartNote { turn, event } => {
                self.note_draft = NoteDraft {
                    id: String::new(),
                    turn_index: turn,
                    event_index: event,
                    fields: vec![],
                };
                self.note_compose_lock = true;
                self.typing_notes = true;
                self.tab = Tab::Notes;
                Task::none()
            }
            Message::OpenNote(nid) => {
                self.open_note(&nid);
                Task::none()
            }
            Message::ResetDraft => {
                self.note_draft = NoteDraft::default();
                self.note_compose_lock = false;
                self.typing_notes = false;
                self.note_saving = false;
                Task::none()
            }
            Message::NoteField { id, value } => {
                self.note_draft.set_field(&id, value);
                self.note_compose_lock = true;
                self.typing_notes = true;
                Task::none()
            }
            Message::NoteTurn(v) => {
                self.note_draft.turn_index = v;
                self.note_compose_lock = true;
                Task::none()
            }
            Message::SaveNote => self.save_note(),
            Message::RequestDelete(nid) => self.request_delete(nid),
            Message::PopOutWindow => self.pop_out_window(),
            Message::Hotkey => self.on_hotkey(),
            Message::ListScroll(win) => {
                self.list_window = win;
                Task::none()
            }
            Message::TimelineScroll(win) => {
                self.tl_window = win;
                if let Some(pos) = self.timeline_focus_pos() {
                    if !icedtea::collection::row_is_mounted(self.tl_window, pos) {
                        self.timeline_focus = None;
                    }
                }
                if should_load_previous_timeline(
                    self.tl_window.scroll,
                    self.timeline_offset,
                    self.timeline_loading,
                ) {
                    return self.load_previous_timeline();
                }
                let n = self.filtered_timeline().len();
                let shown = self.tl_window.end.saturating_add(4);
                if n > 0 && shown >= n && !self.timeline_complete() {
                    return self.load_more_timeline();
                }
                Task::none()
            }
            Message::TurnScroll(win) => {
                let empty = self
                    .overview
                    .as_ref()
                    .map(|o| o.turns.turns.is_empty())
                    .unwrap_or(true);
                self.turn_window = if empty {
                    icedtea::collection::VisibleWindow::new(win.viewport.max(1.0))
                } else {
                    win
                };
                Task::none()
            }
            Message::ActivateSelected => {
                if self.browse_mode() {
                    // Already in full-width browse with an empty search — no re-pick.
                    return Task::none();
                }
                self.ensure_rail_selection_for_activate();
                let Some(sid) = self.selected_sid() else {
                    return Task::none();
                };
                // Same as click: clear search so layout leaves the picker.
                self.query.clear();
                self.rerank_visible_keeping(sid.clone());
                if let Some(idx) = self.sessions().iter().position(|r| r.session_id == sid) {
                    self.set_active(idx);
                }
                Task::batch([self.load_overview(false), self.focus_browse()])
            }
            Message::FindingExpand { id, open } => {
                if open {
                    self.findings_open.insert(id);
                } else {
                    self.findings_open.remove(&id);
                }
                Task::none()
            }
            Message::NoteExpand { id, open } => {
                if open {
                    self.notes_open.insert(id);
                } else {
                    self.notes_open.remove(&id);
                }
                Task::none()
            }
            Message::FollowDraft(s) => {
                self.follow_draft = s;
                Task::none()
            }
            Message::SendFollow => self.send_follow(),
            Message::MarkDone => self.mark_done(),
            Message::CopyPath => self.copy_path(),
            Message::CopyText(s) => self.copy_text(s),
            Message::Select { id, action } => {
                self.fields.perform(&id, action);
                Task::none()
            }
            Message::ToastDismiss(id) => {
                self.toasts.dismiss(id);
                Task::none()
            }
            Message::Noop => Task::none(),
            Message::FollowDone(result) => {
                match result {
                    Ok(_) => {
                        self.follow_draft.clear();
                        self.toasts.push_success("Follow-up sent");
                    }
                    Err(e) => {
                        self.toasts.push_danger(e);
                    }
                }
                Task::none()
            }
            Message::Tick => self.on_tick(),
            Message::FocusSearch(attempt) => self.on_focus_search(attempt),
            Message::RawEvent(ev) => self.on_event(ev),
            Message::Inited(Ok(_)) => {
                self.mark_up();
                self.status = format!(
                    "ready · {}",
                    control::default_socket_path()
                        .file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or("control.sock")
                );
                Task::none()
            }
            Message::Inited(Err(e)) => {
                self.mark_down(&e);
                Task::none()
            }
            Message::ListLoaded { quiet, result } => match result {
                Ok(v) => {
                    if quiet {
                        if let Ok(page) = decode_session_list_response(&v) {
                            if !page.unchanged
                                && !page.delta
                                && page.matched > page.sessions.len() as i64
                            {
                                return fetch_list(quiet, 0);
                            }
                        }
                        self.apply_list(v, quiet);
                        return Task::none();
                    }
                    self.apply_list(v.clone(), quiet);
                    // Spotlight: catalog fill only — never auto-open a session.
                    self.continue_catalog_pages(&v)
                }
                Err(e) => {
                    self.mark_down(&e);
                    Task::none()
                }
            },
            Message::ListPage { offset: _, result } => match result {
                Ok(v) => {
                    let before = self.all_sessions.len();
                    self.apply_list(v.clone(), true);
                    if self.all_sessions.len() <= before {
                        Task::none()
                    } else {
                        self.continue_catalog_pages(&v)
                    }
                }
                Err(e) => {
                    self.mark_down(&e);
                    Task::none()
                }
            },
            Message::OverviewLoaded {
                gen,
                sid,
                quiet,
                result,
            } => {
                if gen != self.overview_gen {
                    return Task::none();
                }
                match result {
                    Ok(data) => {
                        let ov = match decode_overview(&data) {
                            Ok(o) => o,
                            Err(e) => {
                                self.mark_down(&e);
                                return Task::none();
                            }
                        };
                        patch_list_row_from_meta(&mut self.all_sessions, &sid, &ov.meta);
                        patch_list_row_from_meta(&mut self.sessions, &sid, &ov.meta);
                        self.emit_session_notices();
                        // Always paint footer identity (quiet ticks must not leave a
                        // stale session id while body shows another overview).
                        let st = ov.meta.status_label();
                        self.status = if st.is_empty() {
                            sid.clone()
                        } else {
                            format!("{sid} · {st}")
                        };
                        self.overview = Some(ov);
                        self.overview_sid = sid.clone();
                        self.overview_pending.clear();
                        // Pin open session into the Spotlight recent strip.
                        self.rerank_visible_keeping(sid.clone());
                        self.sync_rail_to_overview_sid();
                        let rail = self.ensure_active_visible();
                        self.rebuild_events_turn_options();
                        self.rebuild_marks();
                        // Quiet live ticks: avoid re-filtering the whole timeline and
                        // rebinding every open turn when the operator is not on Events.
                        if quiet {
                            self.bind_overview_fields();
                            self.rebuild_turns_filter();
                            // Timeline filter only matters on Events; skip the O(n)
                            // scan while the operator is on Turns/Overview.
                            if self.wants_events() {
                                self.rebuild_tl_filter();
                            }
                        } else {
                            self.rebuild_tl_filter();
                            self.bind_turn_extracts();
                        }
                        self.mark_up();
                        if self.wants_events() {
                            return Task::batch([
                                rail,
                                self.ensure_timeline(sid, false),
                                if quiet {
                                    Task::none()
                                } else {
                                    self.focus_browse()
                                },
                            ]);
                        }
                        if !quiet {
                            return Task::batch([rail, self.focus_browse()]);
                        }
                        return rail;
                    }
                    Err(e) => {
                        if !quiet {
                            self.overview = None;
                            self.overview_sid.clear();
                            self.overview_pending.clear();
                        }
                        self.mark_down(&e);
                    }
                }
                Task::none()
            }
            Message::TimelineLoaded {
                gen,
                sid,
                offset,
                append,
                advance,
                result,
            } => {
                if self.timeline_search_pending || gen != self.timeline_gen {
                    return Task::none();
                }
                self.timeline_loading = false;
                match result {
                    Ok(data) => {
                        let page = match decode_timeline_page(&data) {
                            Ok(p) => p,
                            Err(e) => {
                                self.mark_down(&e);
                                return Task::none();
                            }
                        };
                        let batch = page.events;
                        let total = if page.total > 0 {
                            page.total
                        } else {
                            self.timeline_total
                        };
                        let old_offset = self.timeline_offset;
                        let added =
                            if append && self.timeline_sid == sid && !self.timeline.is_empty() {
                                let merged = merge_timeline_by_index(&self.timeline, &batch);
                                let n = merged.added;
                                self.timeline = merged.events;
                                n
                            } else {
                                self.timeline = batch.clone();
                                batch.len()
                            };
                        self.timeline_sid = sid.clone();
                        self.timeline_total = total;
                        let page_off = if page.limit > 0 || !batch.is_empty() {
                            page.offset
                        } else {
                            offset
                        };
                        self.timeline_offset =
                            timeline_window_start(self.timeline_offset, page_off, !append, advance);
                        self.timeline_next = timeline_page_next(
                            page_off,
                            batch.len() as u32,
                            self.timeline_next,
                            advance,
                        );
                        if self.timeline_total > 0 {
                            self.timeline_next = self.timeline_next.min(self.timeline_total);
                        }
                        self.rebuild_tl_filter();
                        self.mark_up();
                        // Turn-boundary pager: open first/last of the newly loaded filter.
                        let edge_open = self
                            .detail_turn_edge
                            .take()
                            .and_then(|edge| self.edge_event_index(edge));
                        if edge_open.is_none() {
                            if let Some(ix) = self.timeline_open {
                                self.bind_event_extract(ix);
                            }
                        }
                        if self.timeline.len() > TIMELINE_BUFFER_CAP {
                            self.timeline = trim_timeline_buffer(
                                std::mem::take(&mut self.timeline),
                                self.timeline_focus,
                                TIMELINE_BUFFER_CAP,
                            );
                        }
                        let mut tasks = Vec::new();
                        if let Some(ix) = edge_open {
                            tasks.push(self.open_timeline_detail(ix));
                        } else if !append {
                            if self.timeline_focus_pos().is_some() {
                                tasks.push(self.scroll_focus_into_view());
                            }
                            if let Some(ix) = self.timeline_open {
                                tasks.push(self.fetch_open_event(ix));
                            }
                        }
                        if append && advance && added > 0 && page_off < old_offset {
                            self.tl_window.scroll =
                                scroll_after_prepend(self.tl_window.scroll, added, TIMELINE_ROW_H);
                        }
                        if should_load_previous_timeline(
                            self.tl_window.scroll,
                            self.timeline_offset,
                            false,
                        ) {
                            if let Some(next_sid) = self.detail_sid() {
                                tasks.push(self.fill_timeline_before(next_sid));
                            }
                        }
                        if !tasks.is_empty() {
                            return Task::batch(tasks);
                        }
                    }
                    Err(e) => self.mark_down(&e),
                }
                Task::none()
            }
            Message::NoteSaved(result) => {
                self.note_saving = false;
                match result {
                    Ok(snap) => {
                        self.apply_notes_snapshot(&snap);
                        self.note_draft = NoteDraft::default();
                        self.note_compose_lock = false;
                        self.typing_notes = false;
                        self.toasts.push_success("Note saved");
                        self.mark_up();
                        self.status = "Note saved".into();
                    }
                    Err(e) => {
                        if !is_soft_notes_save_error(&e) {
                            self.mark_down(&e);
                        } else {
                            crate::log::error(&format!("note save (soft): {e}"));
                        }
                        self.status = format!("Note save failed: {e}");
                        self.status_err = true;
                    }
                }
                Task::none()
            }
            Message::NoteDeleted { id, result } => {
                match result {
                    Ok(snap) => {
                        self.apply_notes_snapshot(&snap);
                        if self.note_draft.id == id {
                            self.note_draft = NoteDraft::default();
                            self.note_compose_lock = false;
                        }
                        self.mark_up();
                        self.status = "Note deleted".into();
                    }
                    Err(e) => {
                        if !is_soft_notes_save_error(&e) {
                            self.mark_down(&e);
                        } else {
                            crate::log::error(&format!("note delete (soft): {e}"));
                        }
                        self.status = format!("Note delete failed: {e}");
                        self.status_err = true;
                    }
                }
                Task::none()
            }
            Message::WindowId(id) => {
                let Some(id) = id else {
                    return Task::none();
                };
                self.window_id = Some(id);
                let mut tasks = vec![delayed_focus(0), self.apply_native_chrome(id)];
                if !self.window_mode {
                    tasks.push(self.place_overlay(id));
                }
                Task::batch(tasks)
            }
            Message::WindowPos(pos) => {
                if let Some(p) = pos {
                    if p.x.abs() > 8.0 || p.y.abs() > 8.0 {
                        self.palette_origin = Some(p);
                    }
                }
                Task::none()
            }
            Message::X11Focus { xid, attempt } => self.after_x11_focus(xid, attempt),
            Message::Hide => {
                if self.context.take().is_some() {
                    return Task::none();
                }
                // Chrome Escape maps here while a field is focused.
                if self.tab == Tab::Timeline && self.timeline_open.is_some() {
                    return self.close_timeline_detail();
                }
                // Overlay: Escape hides. hide_palette no-ops in window mode.
                if icedtea::window::should_hide(
                    icedtea::window::HidePolicy::Escape,
                    icedtea::window::HideEvent::Escape,
                    false,
                ) {
                    return self.hide_palette();
                }
                Task::none()
            }
            Message::CloseRequested(id) => self.on_close_requested(id),
            Message::Tray(action) => self.on_tray(action),
            Message::Summon(req) => self.on_summon(req),
            Message::ActivationApplied(ok) => {
                if ok {
                    self.pending_activation_token = None;
                }
                Task::none()
            }
            Message::Yank => self.yank_active(),
            Message::Cursor(ev) => self.on_cursor(ev),
            Message::ContextDismiss => {
                self.context = None;
                Task::none()
            }
            Message::WindowSize(size) => {
                if size.width > 1.0 && size.height > 1.0 {
                    // List viewport is only refreshed on rail scroll; seed from
                    // chrome-ish remainder so keyboard cover math is not stuck
                    // on the default 400px after resize.
                    let list_vp = (size.height - 140.0).max(80.0);
                    self.list_window.viewport = list_vp;
                    self.tl_window.viewport = list_vp;
                    self.turn_window.viewport = list_vp;
                    self.window_size = size;
                }
                Task::none()
            }
            Message::MdLink(url) => {
                self.status = url;
                self.status_err = false;
                Task::none()
            }
        }
    }

    fn view(&self, _window: window::Id) -> Element<'_, Message> {
        view::layout(self)
    }
}

impl Hud {
    pub fn query(&self) -> &str {
        &self.query
    }

    /// Full-width session browse (tabs + detail). False while the search field
    /// has text or no session is open — then the body is the session picker.
    pub fn browse_mode(&self) -> bool {
        self.query.trim().is_empty()
            && (self.overview.is_some() || !self.overview_pending.is_empty())
    }

    /// Visible Spotlight rows: recent when the query is empty, else search hits.
    /// Full catalog stays on ``all_sessions`` (not shown until the user types).
    pub fn sessions(&self) -> &[SessionRow] {
        &self.sessions
    }
    pub fn active(&self) -> usize {
        self.active
    }
    pub fn tab(&self) -> Tab {
        self.tab
    }
    pub fn overview(&self) -> Option<&Overview> {
        self.overview.as_ref()
    }
    pub fn overview_sid(&self) -> &str {
        &self.overview_sid
    }
    pub fn overview_pending(&self) -> &str {
        &self.overview_pending
    }
    pub fn timeline_query(&self) -> &str {
        &self.timeline_query
    }
    pub fn timeline_query_draft(&self) -> &str {
        &self.timeline_query_draft
    }
    pub fn timeline_kind(&self) -> KindFilter {
        self.timeline_kind
    }
    pub fn turns_focus(&self) -> Option<i64> {
        self.turns_focus
    }
    pub fn timeline_focus(&self) -> Option<i64> {
        self.timeline_focus
    }
    /// Event index for full-pane Timeline detail, if any.
    pub fn timeline_open(&self) -> Option<i64> {
        self.timeline_open
    }
    pub fn is_timeline_open(&self, index: i64) -> bool {
        self.timeline_open == Some(index)
    }

    /// 1-based position and length in the filtered timeline list for chrome.
    pub fn timeline_detail_pos(&self) -> Option<(usize, usize)> {
        let ix = self.timeline_open?;
        let n = self.tl_filter.len();
        if n == 0 {
            return None;
        }
        let pos = self
            .tl_filter
            .iter()
            .position(|&src| self.timeline.get(src).is_some_and(|e| e.index == ix))?;
        Some((pos + 1, n))
    }
    pub fn field(&self, id: &str) -> Option<&iced::widget::text_editor::Content> {
        self.fields.get(id)
    }
    pub fn extract(&self, key: ExtractKey) -> Option<&iced::widget::text_editor::Content> {
        self.field(&key.id())
    }
    pub fn extract_src(&self, key: ExtractKey) -> Option<String> {
        self.field(&key.id()).map(|c| c.text())
    }

    pub(crate) fn bind_field(&mut self, id: impl Into<String>, src: &str) {
        if src.is_empty() {
            return;
        }
        let id = id.into();
        self.fields.ensure(id, src);
    }

    fn unbind_event_fields(&mut self, index: i64) {
        let prefix = format!("event.{index}");
        self.fields.retain(|id| {
            if id == prefix.as_str() {
                return false;
            }
            let dot = format!("{prefix}.");
            !id.starts_with(&dot)
        });
    }

    fn rebuild_turns_filter(&mut self) {
        let turns = self
            .overview
            .as_ref()
            .map(|o| o.turns.turns.as_slice())
            .unwrap_or(&[]);
        self.turns_filter = filter_turn_indices(turns, &self.turns_query);
        self.rebuild_turn_heights();
    }

    fn rebuild_turn_heights(&mut self) {
        let n = self.turns_filter.len();
        self.turn_heights = vec![CLOSED_TURN_CARD_H; n];
        let view_h = self.turn_window.viewport.max(1.0);
        let content: f32 = self.turn_heights.iter().copied().sum();
        self.turn_window.scroll = clamp_scroll(self.turn_window.scroll, content, view_h);
    }

    fn rebuild_tl_heights(&mut self) {
        // List rows are uniform; open detail is a separate full-pane view.
        let n = self.tl_filter.len();
        self.tl_heights = vec![TIMELINE_ROW_H; n];
        let view_h = self.tl_window.viewport.max(1.0);
        let content: f32 = self.tl_heights.iter().copied().sum();
        self.tl_window.scroll = clamp_scroll(self.tl_window.scroll, content, view_h);
    }

    fn bind_extract_text(&mut self, key: ExtractKey, src: &str) {
        self.bind_field(key.id(), src);
    }

    fn bind_display(src: &str) -> String {
        if crate::format::looks_like_json(src) {
            crate::format::capped_display(&crate::format::pretty_json(src), 4_000)
        } else {
            crate::format::capped_display(src, 4_000)
        }
    }

    fn bind_event_extract(&mut self, index: i64) {
        let Some(pos) = self.timeline.iter().position(|e| e.index == index) else {
            return;
        };
        let mut keep: HashSet<String> = HashSet::new();
        let body_id = ExtractKey::Event(index).id();
        let src = event_body_text(&self.timeline[pos]);
        if !src.is_empty() {
            keep.insert(body_id.clone());
            self.bind_extract_text(ExtractKey::Event(index), &Self::bind_display(&src));
        }
        let call_pos = {
            let ev = &self.timeline[pos];
            let id = ev.tool_call_id.trim();
            if id.is_empty() {
                pos
            } else {
                self.timeline
                    .iter()
                    .position(|o| {
                        o.tool_call_id == ev.tool_call_id
                            && (o.kind == "tool" || o.event_type == "tool_call")
                    })
                    .unwrap_or(pos)
            }
        };
        let call = &self.timeline[call_pos];
        let fields = if !call.tool_fields.is_empty() {
            call.tool_fields
                .iter()
                .map(|f| (f.id.clone(), f.value.clone()))
                .collect::<Vec<_>>()
        } else {
            tool_fields_from_raw(&call.tool_name, &call.raw_input, 8_000)
                .into_iter()
                .map(|f| (f.id, f.value))
                .collect()
        };
        for (fid, val) in fields {
            if !val.is_empty() {
                let id = format!("event.{index}.in.{fid}");
                keep.insert(id.clone());
                self.bind_field(id, &Self::bind_display(&val));
            }
        }
        // Output body: pair tool_call → tool_call_update. read_file calls have
        // empty content; the dump lives on the result row only.
        let (out_tool, out_content) = {
            let ev = &self.timeline[pos];
            let call_id = ev.tool_call_id.clone();
            let ev_tool = ev.tool_name.clone();
            let ev_content = ev.content.clone();
            let result = if !call_id.trim().is_empty() {
                self.timeline.iter().find(|o| {
                    o.tool_call_id == call_id
                        && (o.kind == "tool_result"
                            || o.event_type == "tool_call_update"
                            || o.event_type == "tool_result")
                })
            } else {
                None
            };
            match result {
                Some(r) if !r.content.is_empty() => {
                    let tool = if r.tool_name.is_empty() {
                        ev_tool
                    } else {
                        r.tool_name.clone()
                    };
                    (tool, r.content.clone())
                }
                _ => (ev_tool, ev_content),
            }
        };
        let out = crate::format::sanitize_console_text(&crate::format::display_tool_output(
            &out_content,
            &out_tool,
        ));
        if !out.trim().is_empty() {
            let id = format!("event.{index}.out");
            keep.insert(id.clone());
            self.bind_field(id, &Self::bind_display(&out));
        }
        let prefix = format!("event.{index}");
        let prefix_dot = format!("{prefix}.");
        self.fields.retain(|id| {
            if id == prefix.as_str() || id.starts_with(&prefix_dot) {
                keep.contains(id)
            } else {
                true
            }
        });
    }

    fn bind_turn_extracts(&mut self) {
        self.bind_overview_fields();
        self.rebuild_turns_filter();
    }

    fn bind_overview_fields(&mut self) {
        let Some(o) = &self.overview else {
            return;
        };
        for field in crate::format::overview_fields(&o.meta, &o.turns) {
            if field.copyable && !field.value.is_empty() {
                self.bind_extract_text(ExtractKey::Overview(field.key), &field.value);
            }
        }
    }

    fn wants_events(&self) -> bool {
        should_fetch_timeline(
            self.tab == Tab::Timeline,
            &self.timeline_query,
            self.timeline_prompt,
        )
    }

    pub(crate) fn last_timeline(&self) -> Option<&LastTimelineReq> {
        self.last_timeline.as_ref()
    }

    fn copy_text(&mut self, text: String) -> Task<Message> {
        self.context = None;
        let text = text.trim().to_string();
        if text.is_empty() {
            self.toasts.push_warning("Nothing to copy");
            return Task::none();
        }
        write_os_clipboard(&text);
        Task::batch([
            icedtea::host::copy_text(text.clone()),
            iced::clipboard::write_primary(text),
        ])
    }

    fn yank_active(&mut self) -> Task<Message> {
        self.copy_text(self.copyable_text())
    }

    pub(crate) fn copyable_text(&self) -> String {
        if let Some(sel) = self.fields.first_selection() {
            if !sel.trim().is_empty() {
                return sel;
            }
        }
        match self.tab {
            Tab::Timeline => self
                .timeline_focus
                .and_then(|ix| self.timeline.iter().find(|e| e.index == ix))
                .map(extract_event)
                .filter(|s| !s.trim().is_empty())
                .or_else(|| {
                    self.timeline_focus.and_then(|ix| {
                        self.field(&format!("event.{ix}.out"))
                            .map(|c| c.text())
                            .filter(|s| !s.trim().is_empty())
                    })
                })
                .unwrap_or_default(),
            Tab::Turns => self
                .overview
                .as_ref()
                .and_then(|o| {
                    let idx = self.turns_focus?;
                    o.turns.turns.iter().find(|t| t.turn_index == idx)
                })
                .map(|t| extract_turn(&t.label, &t.summary, &t.assistant_summary))
                .unwrap_or_default(),
            Tab::Findings => self
                .overview
                .as_ref()
                .and_then(|o| {
                    o.findings
                        .findings
                        .iter()
                        .find(|f| self.findings_open.contains(&finding_menu_key(f)))
                })
                .map(|f| {
                    if f.detail.is_empty() {
                        f.title.clone()
                    } else {
                        f.detail.clone()
                    }
                })
                .unwrap_or_default(),
            Tab::Notes => self
                .overview
                .as_ref()
                .and_then(|o| {
                    o.notes
                        .notes
                        .iter()
                        .find(|n| self.notes_open.contains(&n.id))
                })
                .map(|n| crate::format::note_fields_view(&n.fields).1)
                .unwrap_or_default(),
            Tab::Overview => self
                .overview
                .as_ref()
                .map(|o| {
                    if !o.summary.is_empty() {
                        o.summary.clone()
                    } else {
                        o.meta.title.clone()
                    }
                })
                .unwrap_or_default(),
        }
    }

    fn session_path(&self) -> String {
        self.sessions
            .get(self.active)
            .map(|r| r.path.clone())
            .filter(|p| !p.is_empty())
            .or_else(|| self.overview.as_ref().map(|o| o.meta.path.clone()))
            .unwrap_or_default()
    }

    pub fn context_origin(&self) -> Option<Point> {
        self.context
    }

    pub fn window_size(&self) -> Size {
        self.window_size
    }

    pub fn context_actions(&self) -> Vec<icedtea::action::Action<Message>> {
        let mut copy = icedtea::action::Action::new("edit.copy", "Copy", Message::Yank);
        copy.enabled = !self.copyable_text().trim().is_empty();
        let mut path = icedtea::action::Action::new("session.copy", "Copy path", Message::CopyPath);
        path.enabled = !self.session_path().is_empty();
        vec![copy, path]
    }

    fn on_cursor(&mut self, ev: icedtea::layout::CursorEvent) -> Task<Message> {
        match ev {
            icedtea::layout::CursorEvent::Move(p) => {
                self.pointer = p;
            }
            icedtea::layout::CursorEvent::Context if self.visible => {
                self.context = Some(self.pointer);
            }
            icedtea::layout::CursorEvent::Context => {}
        }
        Task::none()
    }

    pub fn timeline_events(&self) -> &[crate::wire::TimelineEvent] {
        &self.timeline
    }
    pub fn timeline_loading(&self) -> bool {
        self.timeline_loading
    }
    pub fn note_draft(&self) -> &NoteDraft {
        &self.note_draft
    }
    pub fn note_saving(&self) -> bool {
        self.note_saving
    }
    pub fn note_delete_armed(&self) -> &str {
        &self.note_delete_armed
    }
    pub fn status(&self) -> &str {
        &self.status
    }
    pub fn status_err(&self) -> bool {
        self.status_err
    }
    pub fn hotkey_hint(&self) -> &str {
        &self.hotkey_hint
    }
    pub fn window_mode(&self) -> bool {
        self.window_mode
    }
    pub fn theme_name(&self) -> &str {
        &self.theme_name
    }
    pub fn tokens(&self) -> icedtea::theme::Tokens {
        crate::theme::tokens(&self.theme_name)
    }
    pub fn search_id(&self) -> Id {
        self.search_id.clone()
    }
    pub fn tl_search_id(&self) -> Id {
        self.tl_search_id.clone()
    }
    pub fn notes_schema(&self) -> Vec<SchemaField> {
        notes_schema_fields(self.overview.as_ref())
    }
    pub fn filtered_indices(&self) -> &[usize] {
        &self.tl_filter
    }
    pub fn filtered_timeline(&self) -> Vec<&TimelineEvent> {
        self.tl_filter
            .iter()
            .filter_map(|&i| self.timeline.get(i))
            .collect()
    }
    pub fn timeline_meta(&self) -> String {
        if self.overview_sid.is_empty() {
            return String::new();
        }
        timeline_range_label(
            self.timeline_offset,
            self.filtered_timeline().len(),
            self.timeline_total,
        )
    }
    pub fn card_marks(
        &self,
    ) -> (
        &std::collections::HashMap<i64, CardMark>,
        &std::collections::HashMap<i64, CardMark>,
    ) {
        (&self.turn_marks, &self.event_marks)
    }

    pub fn timeline_complete(&self) -> bool {
        !self.timeline_sid.is_empty()
            && timeline_coverage_complete(self.timeline.len(), self.timeline_total)
    }

    /// Rail selection identity for loads. `Selection::None` is none — never
    /// invent `active` while the filtered list cleared the highlight.
    fn selected_sid(&self) -> Option<String> {
        match self.list_selection {
            icedtea::collection::Selection::Single(i) => self
                .sessions()
                .get(i)
                .map(|r| r.session_id.clone())
                .filter(|s| !s.is_empty()),
            icedtea::collection::Selection::None | icedtea::collection::Selection::Multi(_) => None,
        }
    }

    fn selected_rpc_ref(&self) -> Option<String> {
        let i = match self.list_selection {
            icedtea::collection::Selection::Single(i) => i,
            _ => return None,
        };
        let row = self.sessions().get(i)?;
        let r = session_rpc_ref(&row.path, &row.session_id);
        if r.is_empty() {
            None
        } else {
            Some(r)
        }
    }

    /// Detail/timeline ops: rail highlight, else open overview (search may hide row).
    fn detail_sid(&self) -> Option<String> {
        if let Some(s) = self.selected_sid() {
            return Some(s);
        }
        if !self.overview_sid.is_empty() {
            return Some(self.overview_sid.clone());
        }
        if !self.overview_pending.is_empty() {
            return Some(self.overview_pending.clone());
        }
        None
    }

    fn detail_rpc_ref(&self) -> Option<String> {
        if let Some(r) = self.selected_rpc_ref() {
            return Some(r);
        }
        let r = self.overview_rpc_ref();
        if r.is_empty() {
            None
        } else {
            Some(r)
        }
    }

    /// Enter / Activate with no rail highlight: take the first visible match.
    fn ensure_rail_selection_for_activate(&mut self) {
        if !matches!(self.list_selection, icedtea::collection::Selection::None) {
            return;
        }
        if self.sessions().is_empty() {
            return;
        }
        self.set_active(0);
    }

    /// Keep rail highlight on the open overview when that row is visible.
    fn sync_rail_to_overview_sid(&mut self) {
        if self.overview_sid.is_empty() {
            return;
        }
        if let Some(i) = self
            .sessions()
            .iter()
            .position(|r| r.session_id == self.overview_sid)
        {
            self.set_active(i);
        }
    }

    fn overview_rpc_ref(&self) -> String {
        if let Some(o) = &self.overview {
            let r = session_rpc_ref(&o.meta.path, &self.overview_sid);
            if !r.is_empty() {
                return r;
            }
        }
        self.selected_rpc_ref()
            .or_else(|| self.selected_sid())
            .unwrap_or_default()
    }

    pub fn list_window(&self) -> icedtea::collection::VisibleWindow {
        self.list_window
    }

    pub fn list_scroll_id(&self) -> Id {
        self.list_scroll_id.clone()
    }

    pub fn timeline_window(&self) -> icedtea::collection::VisibleWindow {
        self.tl_window
    }

    pub fn timeline_heights(&self) -> &[f32] {
        &self.tl_heights
    }

    pub fn turn_window(&self) -> icedtea::collection::VisibleWindow {
        self.turn_window
    }

    pub fn turn_heights(&self) -> &[f32] {
        &self.turn_heights
    }

    pub fn turn_scroll_id(&self) -> Id {
        self.turn_scroll_id.clone()
    }

    pub fn timeline_scroll_id(&self) -> Id {
        self.tl_scroll_id.clone()
    }

    pub fn timeline_focus_pos(&self) -> Option<usize> {
        let focus = self.timeline_focus?;
        self.tl_filter
            .iter()
            .position(|&i| self.timeline.get(i).is_some_and(|ev| ev.index == focus))
    }

    pub fn session_heights(&self) -> &[f32] {
        &self.session_heights
    }

    pub fn list_selection(&self) -> &icedtea::collection::Selection {
        &self.list_selection
    }

    fn set_active(&mut self, i: usize) {
        self.active = i;
        self.list_selection = icedtea::collection::Selection::Single(i);
    }

    pub fn session_tile_height(&self, index: usize) -> f32 {
        self.session_heights
            .get(index)
            .copied()
            .unwrap_or_else(|| self.compute_session_height(index))
    }

    fn compute_session_height(&self, index: usize) -> f32 {
        let title = self
            .sessions()
            .get(index)
            .map(SessionRow::display_title)
            .unwrap_or("");
        let meta = self
            .session_metas
            .get(index)
            .map(String::as_str)
            .unwrap_or("");
        // Rail has no context meter; compact % is text-only in the meta line.
        session_card_height(title, meta, false)
    }

    fn refresh_session_rows(&mut self) {
        self.session_metas = self.sessions().iter().map(session_row_meta).collect();
        self.session_heights = (0..self.sessions().len())
            .map(|i| self.compute_session_height(i))
            .collect();
    }

    fn ensure_active_visible(&mut self) -> Task<Message> {
        let view_h = self.list_window.viewport.max(80.0);
        let y = list_scroll_to_cover(
            &self.session_heights,
            self.active,
            self.list_window.scroll,
            view_h,
        );
        // list_view virtual_clip reads VisibleWindow.scroll; no iced scrollable.
        self.list_window.scroll = y;
        Task::none()
    }

    fn selected_status(&self) -> String {
        if let Some(o) = &self.overview {
            let s = o.meta.status_label();
            if !s.is_empty() {
                return s;
            }
        }
        self.sessions()
            .get(self.active)
            .map(|r| r.status.clone())
            .unwrap_or_default()
    }

    fn mark_up(&mut self) {
        self.status_err = false;
    }

    fn mark_down(&mut self, err: &str) {
        crate::log::error(err);
        self.status_err = true;
        self.status = control_down_message(err);
        self.toasts.push_danger(self.status.clone());
    }

    fn reset_detail_chrome(&mut self) {
        self.tab = Tab::Overview;
        self.timeline_query.clear();
        self.timeline_query_draft.clear();
        self.timeline_search_pending = false;
        self.timeline_kind = KindFilter::All;
        self.timeline.clear();
        self.timeline_sid.clear();
        self.timeline_total = 0;
        self.timeline_offset = 0;
        self.timeline_next = 0;
        self.tl_window = icedtea::collection::VisibleWindow::new(self.tl_window.viewport.max(1.0));
        self.turn_window =
            icedtea::collection::VisibleWindow::new(self.turn_window.viewport.max(1.0));
        self.tl_heights.clear();
        self.turn_heights.clear();
        self.timeline_gen += 1;
        self.timeline_focus = None;
        self.timeline_open = None;
        self.detail_turn_edge = None;
        self.timeline_prompt = None;
        self.events_turn_index = None;
        self.last_timeline = None;
        self.turns_focus = None;
        self.turns_query.clear();
        self.turns_filter.clear();
        self.findings_open.clear();
        self.notes_open.clear();
        self.fields = icedtea::field::Selectables::new();
        self.note_draft = NoteDraft::default();
        self.note_compose_lock = false;
        self.typing_notes = false;
        self.overview = None;
        self.overview_sid.clear();
        self.tl_filter.clear();
        self.turn_marks.clear();
        self.event_marks.clear();
    }

    fn rebuild_tl_filter(&mut self) {
        if self.timeline_sid != self.overview_sid {
            self.tl_filter.clear();
            self.tl_heights.clear();
            return;
        }
        self.tl_filter =
            filter_timeline_indices(&self.timeline, self.timeline_kind, &self.timeline_query);
        self.rebuild_tl_heights();
    }

    fn rebuild_marks(&mut self) {
        match &self.overview {
            Some(o) => {
                let (turns, events) = card_marks_from_overview(o);
                self.turn_marks = turns;
                self.event_marks = events;
            }
            None => {
                self.turn_marks.clear();
                self.event_marks.clear();
            }
        }
    }

    fn apply_list(&mut self, listed: Value, quiet: bool) {
        let page = decode_session_list_response(&listed).ok();
        if let Some(ref page) = page {
            if page.revision > 0 {
                self.catalog_revision = page.revision;
            }
            if quiet && page.unchanged {
                self.mark_up();
                return;
            }
            if page.delta {
                let incoming = page.sessions.clone();
                self.all_sessions =
                    patch_catalog_delta(&self.all_sessions, incoming, &page.removed);
                self.rerank_visible();
                self.emit_session_notices();
                self.mark_up();
                if !quiet {
                    self.status = format!("{} sessions · ready", self.all_sessions.len());
                }
                return;
            }
        }
        let incoming = page
            .as_ref()
            .map(|p| p.sessions.clone())
            .unwrap_or_else(|| decode_session_list(&listed).unwrap_or_default());
        let matched = page.as_ref().map(|p| p.matched).unwrap_or(0);
        let incomplete = page.as_ref().is_some_and(|p| p.incomplete || p.building);
        self.catalog_busy = incomplete;
        let delta = page.as_ref().is_some_and(|p| p.delta);
        if !self.all_sessions.is_empty()
            && is_partial_list_page(
                incoming.len(),
                matched,
                delta,
                page.as_ref().is_some_and(|p| p.incomplete),
                page.as_ref().is_some_and(|p| p.building),
            )
        {
            if incoming.is_empty() {
                return;
            }
            self.all_sessions = patch_catalog_delta(&self.all_sessions, incoming, &[]);
            self.rerank_visible();
            self.emit_session_notices();
            self.mark_up();
            return;
        }
        let rows = merge_catalog_rows(&self.all_sessions, incoming);
        if (quiet || incomplete) && rows.is_empty() && !self.all_sessions.is_empty() {
            return;
        }
        self.all_sessions = rows;
        self.rerank_visible();
        self.emit_session_notices();
        self.mark_up();
        if !quiet {
            if self.sessions().is_empty() {
                self.status = if self.query.trim().is_empty() {
                    self.status_err = true;
                    crate::log::error("no sessions from control");
                    "No sessions from control · is groket serve running?".into()
                } else {
                    format!("No matches for “{}”", self.query.trim())
                };
            } else {
                self.status = format!("{} sessions · ready", self.all_sessions.len());
            }
        }
    }

    fn emit_session_notices(&mut self) {
        // Hold notices until the first complete catalog page so a sparse
        // first paint (blank / "—") does not fire "complete" for every row.
        let seed = !self.notices_primed;
        let rows: Vec<(String, String, String)> = self
            .all_sessions
            .iter()
            .map(|r| {
                (
                    r.session_id.clone(),
                    r.display_title().to_string(),
                    list_status_label(&r.status, &r.outcome),
                )
            })
            .collect();
        for notice in crate::desktop::notices_from_rows(
            &mut self.seen_status,
            &mut self.notice_stable,
            &rows,
            seed,
        ) {
            crate::desktop::post(notice);
        }
        if !self.catalog_busy && !self.all_sessions.is_empty() {
            self.notices_primed = true;
        }
    }

    fn continue_catalog_pages(&self, listed: &Value) -> Task<Message> {
        let Ok(page) = decode_session_list_response(listed) else {
            return Task::none();
        };
        if page.delta || page.unchanged {
            return Task::none();
        }
        match next_list_offset(
            self.all_sessions.len(),
            first_list_fetch().0,
            page.matched,
            page.incomplete || page.building,
        ) {
            Some(offset) => fetch_list_page(offset),
            None => Task::none(),
        }
    }

    /// Session id to keep selected across a list re-rank.
    ///
    /// Prefer the open overview (or in-flight pending load) so clearing search
    /// never maps a filtered `active` index onto a different catalog row.
    fn session_keep_id(&self) -> String {
        if !self.overview_sid.is_empty() {
            return self.overview_sid.clone();
        }
        if !self.overview_pending.is_empty() {
            return self.overview_pending.clone();
        }
        self.sessions()
            .get(self.active)
            .map(|r| r.session_id.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_default()
    }

    fn rerank_visible(&mut self) {
        let keep = self.session_keep_id();
        self.rerank_visible_keeping(keep);
    }

    fn rerank_visible_keeping(&mut self, keep: String) {
        if self.query.trim().is_empty() {
            // Idle Spotlight: latest few only (not the full catalog dump).
            self.sessions = spotlight_recent(&self.all_sessions, SPOTLIGHT_RECENT, &keep);
        } else {
            // Title-first scores (not flat haystack); keep score order — do not
            // re-sort by recency or a weak id match jumps above a title hit.
            let idxs = session_search_indices(self.query.trim(), &self.all_sessions);
            self.sessions = idxs
                .into_iter()
                .filter_map(|i| self.all_sessions.get(i).cloned())
                .collect();
        }
        self.refresh_session_rows();
        let n = self.sessions().len();
        let keep_at = if keep.is_empty() {
            None
        } else {
            self.sessions().iter().position(|r| r.session_id == keep)
        };
        if let Some(idx) = keep_at {
            self.set_active(idx);
        } else if !keep.is_empty() {
            // Open session not in the visible list: no highlight until re-pick.
            self.active = 0;
            self.list_selection = icedtea::collection::Selection::None;
        } else if n == 0 {
            self.active = 0;
            self.list_selection = icedtea::collection::Selection::None;
        } else if self.active >= n {
            self.set_active(n.saturating_sub(1));
        } else if matches!(self.list_selection, icedtea::collection::Selection::None) {
            // Idle Spotlight: no forced selection until arrows / click / Enter.
        } else {
            self.list_selection = icedtea::collection::Selection::Single(self.active);
        }
        let view_h = self.list_window.viewport.max(1.0);
        let content: f32 = self.session_heights.iter().copied().sum();
        self.list_window.scroll = clamp_scroll(self.list_window.scroll, content, view_h);
    }

    fn load_overview(&mut self, quiet: bool) -> Task<Message> {
        // Explicit activate needs a rail choice. Quiet refresh may target the
        // open overview while search cleared the highlight (never wipe body).
        let (sid, rpc_ref) =
            if let (Some(s), Some(r)) = (self.selected_sid(), self.selected_rpc_ref()) {
                (s, r)
            } else if quiet {
                let Some(s) = self.detail_sid() else {
                    return Task::none();
                };
                let Some(r) = self.detail_rpc_ref() else {
                    return Task::none();
                };
                (s, r)
            } else {
                // Explicit open with nothing selected and no open overview: clear.
                self.overview = None;
                self.overview_sid.clear();
                self.overview_pending.clear();
                return Task::none();
            };
        // Chrome: pending sid + loading placeholder this frame; body fills async.
        self.overview_pending = sid.clone();
        self.turns_focus = None;
        self.turns_query.clear();
        self.overview_gen += 1;
        let gen = self.overview_gen;
        Task::perform(
            rpc(move || control::session_overview(&rpc_ref)),
            move |result| Message::OverviewLoaded {
                gen,
                sid: sid.clone(),
                quiet,
                result,
            },
        )
    }

    fn scroll_focus_into_view(&mut self) -> Task<Message> {
        let Some(pos) = self.timeline_focus_pos() else {
            return Task::none();
        };
        let view_h = self.tl_window.viewport.max(1.0);
        let y = list_scroll_to_cover(&self.tl_heights, pos, self.tl_window.scroll, view_h);
        self.tl_window.scroll = y;
        Task::none()
    }

    /// Open full-pane event detail on Timeline (fetch full content).
    fn open_timeline_detail(&mut self, index: i64) -> Task<Message> {
        if self.timeline_open == Some(index) {
            return Task::none();
        }
        if let Some(prev) = self.timeline_open {
            self.unbind_event_fields(prev);
        }
        self.timeline_open = Some(index);
        self.timeline_focus = Some(index);
        self.bind_event_extract(index);
        self.fetch_open_event(index)
    }

    /// Drop full-pane detail without scrolling (filter / turn pick changes).
    fn drop_timeline_detail(&mut self) {
        if let Some(ix) = self.timeline_open.take() {
            self.unbind_event_fields(ix);
        }
        self.detail_turn_edge = None;
    }

    fn edge_event_index(&self, edge: DetailTurnEdge) -> Option<i64> {
        let src = match edge {
            DetailTurnEdge::First => *self.tl_filter.first()?,
            DetailTurnEdge::Last => *self.tl_filter.last()?,
        };
        self.timeline.get(src).map(|e| e.index)
    }

    /// Leave full-pane detail and scroll the list to the event you were on
    /// (after Next/Prev that is the last open index, not the first opened).
    fn close_timeline_detail(&mut self) -> Task<Message> {
        if let Some(ix) = self.timeline_open.take() {
            self.unbind_event_fields(ix);
            self.timeline_focus = Some(ix);
        }
        // Pin the focused row to the top so Esc always lands on a visible
        // selected card (cover-only can leave it below the fold after detail).
        let Some(pos) = self.timeline_focus_pos() else {
            return Task::none();
        };
        let view_h = self.tl_window.viewport.max(1.0);
        self.tl_window.scroll = list_scroll_to_top(&self.tl_heights, pos, view_h);
        Task::none()
    }

    fn turn_row_for_event(&self, index: i64) -> Option<&crate::wire::TurnRow> {
        if let Some(ti) = self
            .timeline
            .iter()
            .find(|e| e.index == index)
            .and_then(|e| e.turn_index)
        {
            if let Some(t) = self
                .overview
                .as_ref()
                .and_then(|o| o.turns.turns.iter().find(|t| t.turn_index == ti))
            {
                return Some(t);
            }
        }
        let o = self.overview.as_ref()?;
        o.turns.turns.iter().find(|t| {
            t.event_indexes.contains(&index)
                || t.user_event_index == Some(index)
                || t.assistant_event_index == Some(index)
                || t.first_index == Some(index)
        })
    }

    fn prompt_index_for_event(&self, index: i64) -> Option<i64> {
        if let Some(p) = self
            .timeline
            .iter()
            .find(|e| e.index == index)
            .and_then(|e| e.prompt_index)
        {
            return Some(p);
        }
        self.turn_row_for_event(index).and_then(|t| t.prompt_index)
    }

    /// Remember this turn for `g` / yank when Events scopes to it.
    fn focus_turn(&mut self, turn: i64) {
        self.turns_focus = Some(turn);
    }

    fn rebuild_events_turn_options(&mut self) {
        let mut out = vec![EventsTurnPick {
            turn_index: None,
            label: "All turns".into(),
        }];
        if let Some(o) = self.overview.as_ref() {
            for t in &o.turns.turns {
                let label = if t.label.is_empty() {
                    format!("Turn {}", t.turn_index)
                } else {
                    t.label.clone()
                };
                out.push(EventsTurnPick {
                    turn_index: Some(t.turn_index),
                    label,
                });
            }
        }
        self.events_turn_options = out;
    }

    pub fn events_turn_options(&self) -> &[EventsTurnPick] {
        &self.events_turn_options
    }

    pub fn events_turn_selected(&self) -> EventsTurnPick {
        let key = self.events_turn_index;
        self.events_turn_options
            .iter()
            .find(|p| p.turn_index == key)
            .cloned()
            .unwrap_or(EventsTurnPick {
                turn_index: None,
                label: "All turns".into(),
            })
    }

    /// Next turn after the current Events pick, if any.
    pub fn next_turn_after_events(&self) -> Option<&crate::wire::TurnRow> {
        let cur = self.events_turn_index?;
        let turns = &self.overview.as_ref()?.turns.turns;
        let i = turns.iter().position(|t| t.turn_index == cur)?;
        turns.get(i + 1)
    }

    fn select_events_turn(&mut self, turn_index: Option<i64>) -> Task<Message> {
        self.tab = Tab::Timeline;
        self.timeline_query.clear();
        self.timeline_query_draft.clear();
        self.timeline_search_pending = false;
        self.timeline_kind = KindFilter::All;
        // Stay on the list when stepping turns; Esc/open is per-event detail.
        self.drop_timeline_detail();
        self.tl_window = icedtea::collection::VisibleWindow::new(self.tl_window.viewport.max(1.0));
        match turn_index {
            None => {
                // All turns: full paginated timeline (not an empty shell).
                self.events_turn_index = None;
                self.timeline_prompt = None;
                self.timeline_focus = None;
                self.rebuild_tl_filter();
                if let Some(sid) = self.detail_sid() {
                    return self.ensure_timeline(sid, true);
                }
                Task::none()
            }
            Some(ti) => {
                let Some(t) = self
                    .overview
                    .as_ref()
                    .and_then(|o| o.turns.turns.iter().find(|row| row.turn_index == ti))
                    .cloned()
                else {
                    return Task::none();
                };
                self.events_turn_index = Some(ti);
                // Prefer the turn’s promptIndex (segment), not sparse event meta.
                self.timeline_prompt = t.prompt_index;
                self.focus_turn(ti);
                self.timeline_focus = t.user_event_index.or(t.first_index);
                self.rebuild_tl_filter();
                if let Some(sid) = self.detail_sid() {
                    return self.ensure_timeline(sid, true);
                }
                Task::none()
            }
        }
    }

    fn jump_next_turn(&mut self) -> Task<Message> {
        let Some(next) = self.next_turn_after_events().map(|t| t.turn_index) else {
            return Task::none();
        };
        self.select_events_turn(Some(next))
    }

    fn jump_timeline(&mut self, index: i64) -> Task<Message> {
        // Chrome first: Timeline tab + turn scope, then full-pane detail.
        self.tab = Tab::Timeline;
        self.timeline_query.clear();
        self.timeline_query_draft.clear();
        self.timeline_search_pending = false;
        self.timeline_kind = KindFilter::All;
        if let Some(t) = self.turn_row_for_event(index).cloned() {
            self.events_turn_index = Some(t.turn_index);
            self.timeline_prompt = t.prompt_index;
            self.focus_turn(t.turn_index);
        } else {
            self.events_turn_index = None;
            self.timeline_prompt = self.prompt_index_for_event(index);
        }
        self.rebuild_tl_filter();
        let open = self.open_timeline_detail(index);
        // Always reload the turn-scoped page.
        if let Some(sid) = self.detail_sid() {
            return Task::batch([self.ensure_timeline(sid, true), open]);
        }
        open
    }

    fn ensure_timeline(&mut self, sid: String, force: bool) -> Task<Message> {
        if !force && self.timeline_sid == sid && !self.timeline.is_empty() && !self.timeline_loading
        {
            return Task::none();
        }
        self.timeline_gen += 1;
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        if force || self.timeline_sid != sid {
            self.timeline.clear();
            self.timeline_total = 0;
            self.timeline_offset = 0;
            self.timeline_next = 0;
            self.tl_window =
                icedtea::collection::VisibleWindow::new(self.tl_window.viewport.max(1.0));
            self.tl_filter.clear();
            self.tl_heights.clear();
        }
        self.start_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset: 0,
            append: false,
            advance: true,
            gen,
            limit: 40,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: if self.timeline_query.trim().is_empty()
                && self.timeline_kind == KindFilter::All
            {
                self.timeline_focus
            } else {
                None
            },
            at_index: None,
            prompt_index: self.timeline_prompt,
            content_chars: TIMELINE_PREVIEW_CHARS,
        })
    }

    fn fill_timeline_before(&mut self, sid: String) -> Task<Message> {
        if self.timeline_search_pending || self.timeline_loading {
            return Task::none();
        }
        let Some((off, limit)) = previous_timeline_page(self.timeline_offset, TIMELINE_CHUNK)
        else {
            return Task::none();
        };
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        self.start_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset: off,
            append: true,
            advance: true,
            gen,
            limit,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: None,
            at_index: None,
            prompt_index: self.timeline_prompt,
            content_chars: TIMELINE_PREVIEW_CHARS,
        })
    }

    fn load_previous_timeline(&mut self) -> Task<Message> {
        if self.tab != Tab::Timeline {
            return Task::none();
        }
        let Some(sid) = self.detail_sid() else {
            return Task::none();
        };
        if self.timeline_sid != sid {
            return Task::none();
        }
        self.fill_timeline_before(sid)
    }

    fn fill_timeline(&mut self, sid: String) -> Task<Message> {
        if self.timeline_search_pending || self.timeline_complete() || self.timeline_loading {
            return Task::none();
        }
        let off = if self.timeline.is_empty() {
            0
        } else {
            self.timeline_next
        };
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        self.start_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset: off,
            append: true,
            advance: true,
            gen,
            limit: TIMELINE_CHUNK,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: None,
            at_index: None,
            prompt_index: self.timeline_prompt,
            content_chars: TIMELINE_PREVIEW_CHARS,
        })
    }

    fn fetch_open_event(&mut self, index: i64) -> Task<Message> {
        if self.overview_sid.is_empty() || self.timeline_search_pending {
            return Task::none();
        }
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        self.start_timeline(self.open_event_fetch(index, gen))
    }

    fn open_event_fetch(&self, index: i64, gen: u64) -> TimelineFetch {
        TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid: self.overview_sid.clone(),
            offset: 0,
            append: true,
            advance: false,
            gen,
            limit: 1,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: None,
            at_index: Some(index),
            prompt_index: self.timeline_prompt,
            content_chars: TIMELINE_OPEN_CHARS,
        }
    }

    fn load_more_timeline(&mut self) -> Task<Message> {
        if self.tab != Tab::Timeline || self.timeline_loading || self.timeline_complete() {
            return Task::none();
        }
        let Some(sid) = self.detail_sid() else {
            return Task::none();
        };
        if self.timeline_sid != sid {
            return Task::none();
        }
        self.fill_timeline(sid)
    }

    fn refresh_timeline_tail(&mut self, sid: String) -> Task<Message> {
        if self.timeline_search_pending || self.timeline_loading {
            return Task::none();
        }
        if self.timeline_sid.is_empty() {
            return self.ensure_timeline(sid, false);
        }
        if self.timeline_sid != sid {
            return Task::none();
        }
        let gen = self.timeline_gen;
        self.start_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset: self.timeline_next.saturating_sub(4),
            append: true,
            advance: true,
            gen,
            limit: LIVE_TAIL_LIMIT,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around: None,
            at_index: None,
            prompt_index: self.timeline_prompt,
            content_chars: TIMELINE_PREVIEW_CHARS,
        })
    }

    fn open_note(&mut self, nid: &str) {
        let Some(o) = &self.overview else {
            self.tab = Tab::Notes;
            return;
        };
        let Some(n) = o.notes.notes.iter().find(|r| r.id == nid) else {
            self.tab = Tab::Notes;
            return;
        };
        let mut fields = Vec::new();
        if let Some(map) = n.fields.as_object() {
            for (k, v) in map {
                fields.push((
                    k.clone(),
                    match v {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    },
                ));
            }
        }
        self.note_draft = NoteDraft {
            id: nid.to_string(),
            turn_index: n.turn_index.map(|i| i.to_string()).unwrap_or_default(),
            event_index: n
                .event_indices
                .first()
                .map(|x| x.to_string())
                .unwrap_or_default(),
            fields,
        };
        self.note_compose_lock = true;
        self.notes_open.insert(nid.to_string());
        self.tab = Tab::Notes;
    }

    fn save_note(&mut self) -> Task<Message> {
        let sid = self.overview_rpc_ref();
        let Some(o) = &self.overview else {
            self.status = "Select a session before saving a note".into();
            return Task::none();
        };
        if sid.is_empty() {
            self.status = "Select a session before saving a note".into();
            return Task::none();
        }
        if !self.note_draft.has_content() {
            self.status = "Enter a note field before saving".into();
            return Task::none();
        }
        let rev = o.notes.revision.clone();
        let mut id = self.note_draft.id.trim().to_string();
        if id.is_empty() {
            id = new_note_id();
        }
        let mut turn_index = 0i64;
        let turn_raw = self.note_draft.turn_index.trim();
        if !turn_raw.is_empty() {
            match turn_raw.parse::<i64>() {
                Ok(n) if n >= 0 => turn_index = n,
                _ => {
                    self.status = "Turn must be a non-negative integer".into();
                    return Task::none();
                }
            }
        }
        let prev = o.notes.notes.iter().find(|n| n.id == id);
        let mut fields = json!({});
        if let Some(p) = prev {
            if let Some(obj) = p.fields.as_object() {
                fields = Value::Object(obj.clone());
            }
        }
        if let Some(map) = fields.as_object_mut() {
            for (k, v) in &self.note_draft.fields {
                map.insert(k.clone(), json!(v));
            }
        }
        let mut event_indices = prev
            .map(|p| json!(p.event_indices.clone()))
            .unwrap_or_else(|| json!([]));
        if prev.is_none() && !self.note_draft.event_index.trim().is_empty() {
            if let Ok(n) = self.note_draft.event_index.trim().parse::<i64>() {
                event_indices = json!([n]);
            }
        }
        let note = json!({
            "id": id,
            "turnIndex": turn_index,
            "fields": fields,
            "eventIndices": event_indices,
        });
        self.note_saving = true;
        Task::perform(
            rpc(move || control::notes_upsert(&sid, note, &rev)),
            Message::NoteSaved,
        )
    }

    fn request_delete(&mut self, nid: String) -> Task<Message> {
        if nid.is_empty() {
            return Task::none();
        }
        if self.note_delete_armed == nid {
            self.note_delete_armed.clear();
            self.note_delete_until = None;
            return self.delete_note(nid);
        }
        self.note_delete_armed = nid;
        self.note_delete_until = Some(Instant::now() + Duration::from_millis(2500));
        self.status = "Press Delete again to confirm".into();
        Task::none()
    }

    fn delete_note(&mut self, nid: String) -> Task<Message> {
        let sid = self.overview_rpc_ref();
        let Some(o) = &self.overview else {
            return Task::none();
        };
        let rev = o.notes.revision.clone();
        Task::perform(
            rpc({
                let id = nid.clone();
                move || control::notes_delete(&sid, &id, &rev)
            }),
            move |result| Message::NoteDeleted {
                id: nid.clone(),
                result,
            },
        )
    }

    fn apply_notes_snapshot(&mut self, snap: &Value) {
        {
            let Some(o) = self.overview.as_mut() else {
                return;
            };
            if let Some(block) = NotesBlock::from_control_snapshot(snap, &o.notes) {
                o.notes = block;
            }
        }
        self.rebuild_marks();
    }

    fn win_task(&self, f: impl FnOnce(window::Id) -> Task<Message>) -> Task<Message> {
        match self.window_id {
            Some(id) => f(id),
            None => Task::none(),
        }
    }

    fn place_overlay(&self, id: window::Id) -> Task<Message> {
        if let Some(origin) = place::active_palette_origin(HUD_W, HUD_H) {
            return window::move_to(id, origin);
        }
        #[cfg(target_os = "linux")]
        {
            // Wayland: iced move_to is a no-op; Sway IPC is the place path.
            if crate::place_linux::place_overlay(HUD_W, HUD_H) {
                return Task::none();
            }
        }
        if let Some(origin) = self.palette_origin {
            window::move_to(id, origin)
        } else {
            window::position(id).map(Message::WindowPos)
        }
    }

    fn apply_native_chrome(&self, id: window::Id) -> Task<Message> {
        let overlay = !self.window_mode;
        window::run(id, move |handle| {
            if !crate::macoswin::apply(handle, overlay) {
                eprintln!("groket-hud: native chrome apply missed the window");
            }
        })
        .discard()
    }

    fn pop_out_window(&mut self) -> Task<Message> {
        if self.window_mode {
            return Task::none();
        }
        self.window_mode = true;
        self.visible = true;
        self.palette_live = true;
        crate::macoswin::set_desktop_app(true);
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        let old = self.window_id.take();
        let (id, open) = open_hud_window(true);
        self.window_id = Some(id);
        let close_old = match old {
            Some(prev) if prev != id => window::close(prev),
            _ => Task::none(),
        };
        Task::batch([open.map(|id| Message::WindowId(Some(id))), close_old])
    }

    fn dismiss_window(&mut self) -> Task<Message> {
        self.window_mode = false;
        self.visible = false;
        self.palette_live = false;
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        let close = match self.window_id.take() {
            Some(id) => window::close(id),
            None => Task::none(),
        };
        crate::macoswin::set_desktop_app(false);
        close
    }

    fn hide_palette(&mut self) -> Task<Message> {
        if self.window_mode {
            return Task::none();
        }
        self.visible = false;
        self.palette_live = false;
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        // Destroy the overlay so Sway rematches for_window on the next show.
        match self.window_id.take() {
            Some(id) => window::close(id),
            None => Task::none(),
        }
    }

    /// Summon lands on Spotlight (Recent + search), never the last open session.
    fn return_to_spotlight(&mut self) {
        self.query.clear();
        self.reset_detail_chrome();
        self.timeline_open = None;
        self.active = 0;
        self.list_selection = icedtea::collection::Selection::None;
        self.rerank_visible_keeping(String::new());
    }

    fn show_palette(&mut self) -> Task<Message> {
        if overlay_already_mapped(self.visible, self.window_mode, self.window_id.is_some()) {
            return self.focus_overlay();
        }
        self.window_mode = false;
        self.visible = true;
        self.palette_live = true;
        self.last_live = Instant::now();
        self.sync_theme();
        // Always open on the session list — pick is explicit (Enter / click).
        self.return_to_spotlight();
        if self.window_id.is_none() {
            let (id, open) = open_hud_window(false);
            self.window_id = Some(id);
            return Task::batch([
                open.map(|id| Message::WindowId(Some(id))),
                delayed_focus(0),
                fetch_list(true, self.catalog_revision),
            ]);
        }
        let chrome = match self.window_id {
            Some(id) => Task::batch([
                window::set_mode(id, Mode::Windowed),
                window::set_level(id, window::Level::AlwaysOnTop),
                window::resize(id, Size::new(HUD_W, HUD_H)),
                self.apply_native_chrome(id),
                self.place_overlay(id),
            ]),
            None => Task::none(),
        };
        Task::batch([
            chrome,
            delayed_focus(0),
            fetch_list(true, self.catalog_revision),
        ])
    }

    fn sync_theme(&mut self) {
        let name = prefs::theme_name();
        if name != self.theme_name {
            self.theme_name = name;
        }
    }

    fn x11_focus_only(&self, attempt: u8) -> Task<Message> {
        let Some(id) = self.window_id else {
            return Task::none();
        };
        #[cfg(target_os = "linux")]
        {
            if !crate::x11focus::x11_grab_needed() {
                let activate = match self.pending_activation_token.clone() {
                    Some(tok) if attempt == 0 => {
                        window::run(id, move |win| crate::wlactivate::activate(win, &tok))
                            .map(Message::ActivationApplied)
                    }
                    _ => Task::none(),
                };
                let gain = window::gain_focus(id);
                if attempt < 6 {
                    return Task::batch([activate, gain, delayed_focus(attempt.saturating_add(1))]);
                }
                return Task::batch([activate, gain]);
            }
            Task::batch([
                window::gain_focus(id),
                window::raw_id::<Message>(id).map(move |xid| Message::X11Focus { xid, attempt }),
            ])
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = attempt;
            window::gain_focus(id)
        }
    }

    /// Window focus + session search (Spotlight / switcher only).
    fn focus_picker(&self) -> Task<Message> {
        self.on_focus_search(0)
    }

    /// Drop iced text-input focus so browse keys (Enter, [, ]) are not Captured
    /// by session / turns / timeline search after a pick or pane change.
    fn blur_text_inputs() -> Task<Message> {
        iced::advanced::widget::operate(
            iced::advanced::widget::operation::focusable::unfocus::<()>(),
        )
        .discard()
    }

    /// Window focus without stealing into session search (browse panes).
    fn focus_browse(&self) -> Task<Message> {
        if !self.visible {
            return Task::none();
        }
        Task::batch([self.x11_focus_only(0), Self::blur_text_inputs()])
    }

    /// Summon / hotkey path: picker when no session open, else keep browse focus.
    fn focus_overlay(&self) -> Task<Message> {
        if self.browse_mode() {
            self.focus_browse()
        } else {
            self.focus_picker()
        }
    }

    fn on_focus_search(&self, attempt: u8) -> Task<Message> {
        if !self.visible {
            return Task::none();
        }
        let input = if self.note_compose_lock {
            Task::none()
        } else {
            operation::focus(self.search_id.clone())
        };
        Task::batch([self.x11_focus_only(attempt), input])
    }

    fn after_x11_focus(&self, xid: u64, attempt: u8) -> Task<Message> {
        #[cfg(target_os = "linux")]
        {
            if !crate::x11focus::focus_window(xid) && attempt < 8 {
                return delayed_focus(attempt.saturating_add(1));
            }
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (xid, attempt);
        }
        Task::none()
    }

    fn on_hotkey(&mut self) -> Task<Message> {
        if self.overlay_is_up() {
            self.hide_palette()
        } else if self.visible && self.window_mode {
            self.win_task(window::gain_focus)
        } else {
            self.show_palette()
        }
    }

    fn on_tray(&mut self, action: crate::tray::TrayAction) -> Task<Message> {
        match action {
            crate::tray::TrayAction::Show => self.show_palette(),
            crate::tray::TrayAction::Toggle => self.on_hotkey(),
            crate::tray::TrayAction::Quit => self.quit(),
        }
    }

    fn on_summon(&mut self, req: crate::summon::SummonRequest) -> Task<Message> {
        let crate::summon::SummonRequest { action, token } = req;
        self.store_summon_token(action, token);
        match action {
            crate::summon::SummonAction::Show => self.show_palette(),
            crate::summon::SummonAction::Hide => self.hide_palette(),
            crate::summon::SummonAction::Toggle => self.on_hotkey(),
        }
    }

    fn store_summon_token(&mut self, action: crate::summon::SummonAction, token: Option<String>) {
        if self.summon_hides(action) {
            self.pending_activation_token = None;
        } else {
            self.pending_activation_token = token;
        }
    }

    fn summon_hides(&self, action: crate::summon::SummonAction) -> bool {
        matches!(action, crate::summon::SummonAction::Hide)
            || (matches!(action, crate::summon::SummonAction::Toggle) && self.overlay_is_up())
    }

    fn overlay_is_up(&self) -> bool {
        self.visible && !self.window_mode
    }

    fn quit(&mut self) -> Task<Message> {
        // iced::exit closes every mapped surface. Do not window::close first:
        // that emits CloseRequested and can drop this exit on a pop-out.
        self.visible = false;
        self.palette_live = false;
        self.window_mode = false;
        self.window_id = None;
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        iced::exit()
    }

    fn on_close_requested(&mut self, id: window::Id) -> Task<Message> {
        if self.window_id != Some(id) {
            return Task::none();
        }
        if self.window_mode {
            self.dismiss_window()
        } else {
            self.hide_palette()
        }
    }

    fn on_tick(&mut self) -> Task<Message> {
        let now = Instant::now();
        let dt = now.saturating_duration_since(self.last_tick).as_millis() as u64;
        self.last_tick = now;
        self.toasts.tick(dt.max(1));
        self.spin_phase = (self.spin_phase + 0.05) % 1.0;
        self.sync_theme();
        if let Some(until) = self.note_delete_until {
            if Instant::now() >= until {
                self.note_delete_armed.clear();
                self.note_delete_until = None;
                self.status = "Delete cancelled".into();
            }
        }
        let mut cmds = Vec::new();
        let notifies: Vec<(String, Value)> = if let Ok(mut g) = self.notify_q.lock() {
            g.drain(..).collect()
        } else {
            vec![]
        };
        let notify_pairs: Vec<(String, String)> = notifies
            .iter()
            .map(|(method, params)| {
                let sid = params
                    .get("sessionId")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                (method.clone(), sid)
            })
            .collect();
        for (method, params) in &notifies {
            if method != "analysis/changed" {
                continue;
            }
            let sid = params
                .get("sessionId")
                .and_then(Value::as_str)
                .unwrap_or("");
            let title = self
                .all_sessions
                .iter()
                .find(|r| r.session_id == sid)
                .map(|r| r.display_title().to_string())
                .unwrap_or_default();
            if let Some(n) =
                crate::desktop::take_analysis_notice(&mut self.seen_analysis, params, &title)
            {
                crate::desktop::post(n);
            }
        }
        let selected = self.selected_sid().unwrap_or_default();
        let live = session_needs_live_poll(
            &self.selected_status(),
            self.overview.as_ref().map(|o| &o.turns),
        );
        let any_live = live
            || self
                .all_sessions
                .iter()
                .any(|r| session_needs_live_poll(&r.status, None));
        let elapsed = self.last_live.elapsed().as_millis() as u64;
        let plan = plan_tick(TickInput {
            notifies: &notify_pairs,
            selected_sid: &selected,
            overview_sid: &self.overview_sid,
            palette_live: self.palette_live && self.visible,
            list_elapsed_ms: elapsed,
            selected_live: live,
            any_live,
            on_timeline: self.wants_events(),
            notes_locked: self.note_compose_lock,
        });
        if plan.fetch_list {
            cmds.push(fetch_list(true, self.catalog_revision));
        }
        if plan.load_overview {
            cmds.push(self.load_overview(true));
        }
        if plan.refresh_timeline {
            if let Some(sid) = self.detail_sid() {
                cmds.push(self.refresh_timeline_tail(sid));
            }
        }
        if plan.fetch_list || plan.load_overview || plan.refresh_timeline {
            self.last_live = Instant::now();
        }
        Task::batch(cmds)
    }

    pub fn toasts(&self) -> &icedtea::toast::ToastQueue {
        &self.toasts
    }
    pub fn catalog_busy(&self) -> bool {
        self.catalog_busy
    }
    pub fn spin_phase(&self) -> f32 {
        self.spin_phase
    }
    pub fn finding_expanded(&self, id: &str) -> bool {
        self.findings_open.contains(id)
    }
    pub fn note_expanded(&self, id: &str) -> bool {
        self.notes_open.contains(id)
    }
    pub fn turns_query(&self) -> &str {
        &self.turns_query
    }

    pub fn filtered_turn_indices(&self) -> &[usize] {
        &self.turns_filter
    }

    pub fn turns_search_id(&self) -> Id {
        self.turns_search_id.clone()
    }
    pub fn follow_draft(&self) -> &str {
        &self.follow_draft
    }
    pub fn selected_awaiting(&self) -> bool {
        crate::live::is_live_status(&self.selected_status())
            && self
                .selected_status()
                .to_ascii_lowercase()
                .contains("await")
    }

    fn send_follow(&mut self) -> Task<Message> {
        let prompt = self.follow_draft.trim().to_string();
        if prompt.is_empty() {
            self.toasts.push_warning("Follow-up is empty");
            return Task::none();
        }
        let Some(sid) = self.selected_rpc_ref() else {
            self.toasts.push_warning("No session");
            return Task::none();
        };
        Task::perform(
            rpc(move || control::session_follow_up(&sid, &prompt, false)),
            Message::FollowDone,
        )
    }

    fn mark_done(&mut self) -> Task<Message> {
        let Some(sid) = self.selected_rpc_ref() else {
            self.toasts.push_warning("No session");
            return Task::none();
        };
        Task::perform(
            rpc(move || control::session_done(&sid)),
            Message::FollowDone,
        )
    }

    fn copy_path(&mut self) -> Task<Message> {
        self.context = None;
        let path = self.session_path();
        if path.is_empty() {
            self.toasts.push_warning("No path");
            return Task::none();
        }
        write_os_clipboard(&path);
        Task::batch([
            icedtea::host::copy_text(path.clone()),
            iced::clipboard::write_primary(path),
        ])
    }

    fn on_event(&mut self, ev: Event) -> Task<Message> {
        // Do not steal focus to search on every click — expand cards, tabs,
        // and the detail pane must keep focus so shortcuts and expand work.
        if let Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, .. }) = ev {
            return self.on_key(key, modifiers);
        }
        Task::none()
    }

    fn on_key(&mut self, key: Key, modifiers: KeyMods) -> Task<Message> {
        if matches!(key, Key::Named(Named::Escape)) {
            if self.context.take().is_some() {
                return Task::none();
            }
            // Full-pane event detail → list at the current event before hide.
            if self.tab == Tab::Timeline && self.timeline_open.is_some() {
                return self.close_timeline_detail();
            }
            // Overlay: Escape hides. hide_palette no-ops in window mode.
            if icedtea::window::should_hide(
                icedtea::window::HidePolicy::Escape,
                icedtea::window::HideEvent::Escape,
                false,
            ) {
                return self.hide_palette();
            }
            return Task::none();
        }
        if modifiers.command() || modifiers.control() {
            if let Key::Character(c) = &key {
                if let Some(n) = c.chars().next().and_then(|ch| ch.to_digit(10)) {
                    if (1..=5).contains(&n) {
                        return self.update(Message::SetTab(Tab::ALL[(n as usize) - 1]));
                    }
                }
            }
        }
        if self.typing_notes {
            return Task::none();
        }
        if matches!(key, Key::Character(ref c) if c.as_str() == "/") && self.tab == Tab::Timeline {
            return operation::focus(self.tl_search_id.clone());
        }
        // Events turn scope without the pick-list mouse: `]` picks the first turn
        // when none is scoped, then advances; `[` clears to all turns.
        if self.tab == Tab::Timeline {
            if matches!(key, Key::Character(ref c) if c.as_str() == "]") {
                if self.events_turn_index.is_none() {
                    let first = self.events_turn_options.iter().find_map(|p| p.turn_index);
                    if first.is_some() {
                        return self.select_events_turn(first);
                    }
                } else {
                    return self.jump_next_turn();
                }
            }
            if matches!(key, Key::Character(ref c) if c.as_str() == "[") {
                return self.select_events_turn(None);
            }
        }
        // From Turns: `g` opens Timeline filtered to the focused turn’s events.
        if self.tab == Tab::Turns
            && matches!(key, Key::Character(ref c) if c.eq_ignore_ascii_case("g"))
        {
            if let Some(turn) = self.turns_focus {
                return self.select_events_turn(Some(turn));
            }
        }
        if matches!(key, Key::Character(ref c) if c.eq_ignore_ascii_case("y"))
            || ((modifiers.command() || modifiers.control())
                && modifiers.shift()
                && matches!(key, Key::Character(ref c) if c.eq_ignore_ascii_case("c")))
        {
            return self.yank_active();
        }
        // Tab / Shift+Tab: cycle browse panes (same as Ctrl+1…5). Not iced widget
        // focus soup — session search is only for Spotlight (type to switch).
        if matches!(key, Key::Named(Named::Tab))
            && !modifiers.alt()
            && !modifiers.logo()
            && self.browse_mode()
        {
            let i = Tab::ALL.iter().position(|t| *t == self.tab).unwrap_or(0);
            let next = if modifiers.shift() {
                (i + Tab::ALL.len() - 1) % Tab::ALL.len()
            } else {
                (i + 1) % Tab::ALL.len()
            };
            return self.update(Message::SetTab(Tab::ALL[next]));
        }
        if matches!(key, Key::Named(Named::Tab))
            && (modifiers.control() || modifiers.command())
            && self.browse_mode()
        {
            let i = Tab::ALL.iter().position(|t| *t == self.tab).unwrap_or(0);
            let next = if modifiers.shift() {
                (i + Tab::ALL.len() - 1) % Tab::ALL.len()
            } else {
                (i + 1) % Tab::ALL.len()
            };
            return self.update(Message::SetTab(Tab::ALL[next]));
        }
        match key {
            Key::Named(Named::ArrowDown) => self.nav_step(1),
            Key::Named(Named::ArrowUp) => self.nav_step(-1),
            Key::Named(Named::PageDown) => self.nav_step(5),
            Key::Named(Named::PageUp) => self.nav_step(-5),
            Key::Named(Named::Home) => self.nav_edge(true),
            Key::Named(Named::End) => self.nav_edge(false),
            // Enter drills deeper: pick session → next pane → open event.
            Key::Named(Named::Enter) => self.enter_next(),
            _ => Task::none(),
        }
    }

    /// Spotlight / switcher list is showing (not full-width browse).
    fn in_session_picker(&self) -> bool {
        !self.browse_mode()
    }

    /// Move highlight by *delta* rows (signed). First press from no selection
    /// lands on the first (down) or last (up) row.
    fn nav_step(&mut self, delta: i32) -> Task<Message> {
        if delta == 0 {
            return Task::none();
        }
        if self.in_session_picker() {
            return self.nav_sessions_step(delta);
        }
        match self.tab {
            Tab::Turns => self.nav_turns_step(delta),
            Tab::Timeline if self.timeline_open.is_none() => self.nav_timeline_step(delta),
            Tab::Timeline => self.nav_timeline_detail_step(delta),
            _ => Task::none(),
        }
    }

    fn nav_edge(&mut self, home: bool) -> Task<Message> {
        if self.in_session_picker() {
            let n = self.sessions().len();
            if n == 0 {
                return Task::none();
            }
            self.set_active(if home { 0 } else { n - 1 });
            return self.ensure_active_visible();
        }
        match self.tab {
            Tab::Turns => {
                let idxs = self.filtered_turn_indices();
                if idxs.is_empty() {
                    return Task::none();
                }
                let src = if home { idxs[0] } else { *idxs.last().unwrap() };
                if let Some(t) = self.overview.as_ref().and_then(|o| o.turns.turns.get(src)) {
                    self.turns_focus = Some(t.turn_index);
                    return self.scroll_turn_into_view();
                }
                Task::none()
            }
            Tab::Timeline if self.timeline_open.is_none() => {
                let n = self.tl_filter.len();
                if n == 0 {
                    return Task::none();
                }
                let pos = if home { 0 } else { n - 1 };
                let src = self.tl_filter[pos];
                if let Some(ev) = self.timeline.get(src) {
                    self.timeline_focus = Some(ev.index);
                    return self.scroll_focus_into_view();
                }
                Task::none()
            }
            _ => Task::none(),
        }
    }

    fn nav_sessions_step(&mut self, delta: i32) -> Task<Message> {
        let n = self.sessions().len();
        if n == 0 {
            return Task::none();
        }
        if matches!(self.list_selection, icedtea::collection::Selection::None) {
            self.set_active(if delta > 0 { 0 } else { n - 1 });
            return self.ensure_active_visible();
        }
        let i = self.active as i32;
        let next = (i + delta).rem_euclid(n as i32) as usize;
        self.set_active(next);
        self.ensure_active_visible()
    }

    fn nav_turns_step(&mut self, delta: i32) -> Task<Message> {
        let idxs = self.filtered_turn_indices();
        if idxs.is_empty() {
            return Task::none();
        }
        let cur = self.turns_focus.and_then(|ti| {
            idxs.iter().position(|&src| {
                self.overview
                    .as_ref()
                    .and_then(|o| o.turns.turns.get(src))
                    .is_some_and(|t| t.turn_index == ti)
            })
        });
        let pos = match cur {
            None => {
                if delta > 0 {
                    0
                } else {
                    idxs.len() - 1
                }
            }
            Some(p) => (p as i32 + delta).rem_euclid(idxs.len() as i32) as usize,
        };
        let src = idxs[pos];
        if let Some(t) = self.overview.as_ref().and_then(|o| o.turns.turns.get(src)) {
            self.turns_focus = Some(t.turn_index);
            return self.scroll_turn_into_view();
        }
        Task::none()
    }

    fn nav_timeline_step(&mut self, delta: i32) -> Task<Message> {
        let n = self.tl_filter.len();
        if n == 0 {
            return Task::none();
        }
        let cur = self.timeline_focus_pos();
        let pos = match cur {
            None => {
                if delta > 0 {
                    0
                } else {
                    n - 1
                }
            }
            Some(p) => (p as i32 + delta).rem_euclid(n as i32) as usize,
        };
        let src = self.tl_filter[pos];
        if let Some(ev) = self.timeline.get(src) {
            self.timeline_focus = Some(ev.index);
            return self.scroll_focus_into_view();
        }
        Task::none()
    }

    /// While reading full-pane detail, step previous/next event.
    ///
    /// Inside a **turn-scoped** filter, past the last event goes to the next
    /// turn’s first event (and prev → previous turn’s last). **All turns**
    /// wraps within the loaded filter. Session ends do not wrap.
    fn nav_timeline_detail_step(&mut self, delta: i32) -> Task<Message> {
        if delta == 0 {
            return Task::none();
        }
        let n = self.tl_filter.len();
        if n == 0 {
            return Task::none();
        }
        let cur = self
            .timeline_open
            .and_then(|ix| {
                self.tl_filter
                    .iter()
                    .position(|&src| self.timeline.get(src).is_some_and(|e| e.index == ix))
            })
            .or_else(|| self.timeline_focus_pos());
        let Some(p) = cur else {
            let src = if delta > 0 {
                self.tl_filter[0]
            } else {
                self.tl_filter[n - 1]
            };
            if let Some(i) = self.timeline.get(src).map(|ev| ev.index) {
                return self.open_timeline_detail(i);
            }
            return Task::none();
        };
        let next = p as i32 + delta;
        if next >= 0 && (next as usize) < n {
            let src = self.tl_filter[next as usize];
            if let Some(ev) = self.timeline.get(src) {
                return self.open_timeline_detail(ev.index);
            }
            return Task::none();
        }
        // Past this filter’s edge.
        if self.events_turn_index.is_some() {
            return self.detail_step_adjacent_turn(delta > 0);
        }
        // All turns: stop at ends (no wrap) — same hard edge as session turns.
        Task::none()
    }

    /// Next/prev turn while staying in full-pane detail (async reload).
    fn detail_step_adjacent_turn(&mut self, forward: bool) -> Task<Message> {
        let turns = self
            .overview
            .as_ref()
            .map(|o| o.turns.turns.clone())
            .unwrap_or_default();
        if turns.is_empty() {
            return Task::none();
        }
        let Some(cur) = self.events_turn_index else {
            return Task::none();
        };
        let Some(i) = turns.iter().position(|t| t.turn_index == cur) else {
            return Task::none();
        };
        let adj = if forward {
            turns.get(i + 1)
        } else {
            i.checked_sub(1).and_then(|j| turns.get(j))
        };
        let Some(t) = adj.cloned() else {
            // Session boundary — stay on the current edge event.
            return Task::none();
        };
        self.detail_turn_edge = Some(if forward {
            DetailTurnEdge::First
        } else {
            DetailTurnEdge::Last
        });
        self.events_turn_index = Some(t.turn_index);
        self.timeline_prompt = t.prompt_index;
        self.focus_turn(t.turn_index);
        self.timeline_query.clear();
        self.timeline_query_draft.clear();
        self.timeline_search_pending = false;
        self.timeline_kind = KindFilter::All;
        self.tl_window = icedtea::collection::VisibleWindow::new(self.tl_window.viewport.max(1.0));
        if let Some(sid) = self.detail_sid() {
            return self.ensure_timeline(sid, true);
        }
        Task::none()
    }

    fn scroll_turn_into_view(&mut self) -> Task<Message> {
        let Some(ti) = self.turns_focus else {
            return Task::none();
        };
        let idxs = self.filtered_turn_indices();
        let Some(pos) = idxs.iter().position(|&src| {
            self.overview
                .as_ref()
                .and_then(|o| o.turns.turns.get(src))
                .is_some_and(|t| t.turn_index == ti)
        }) else {
            return Task::none();
        };
        let view_h = self.turn_window.viewport.max(1.0);
        let y = list_scroll_to_cover(&self.turn_heights, pos, self.turn_window.scroll, view_h);
        self.turn_window.scroll = y;
        Task::none()
    }

    /// Enter: open the next level (pick → browse → event detail → next event).
    fn enter_next(&mut self) -> Task<Message> {
        if self.in_session_picker() {
            return self.update(Message::ActivateSelected);
        }
        match self.tab {
            Tab::Overview => self.update(Message::SetTab(Tab::Turns)),
            Tab::Turns => {
                let turn = self.turns_focus.or_else(|| {
                    self.filtered_turn_indices().first().and_then(|&src| {
                        self.overview
                            .as_ref()
                            .and_then(|o| o.turns.turns.get(src))
                            .map(|t| t.turn_index)
                    })
                });
                if let Some(ti) = turn {
                    self.turns_focus = Some(ti);
                    // Turn → Timeline list scoped to that turn (all its events).
                    return self.select_events_turn(Some(ti));
                }
                self.update(Message::SetTab(Tab::Timeline))
            }
            Tab::Timeline => {
                if self.timeline_open.is_some() {
                    // Already in detail: step to the next event.
                    return self.nav_timeline_detail_step(1);
                }
                let ix = self.timeline_focus.or_else(|| {
                    self.tl_filter
                        .first()
                        .and_then(|&src| self.timeline.get(src).map(|e| e.index))
                });
                if let Some(ix) = ix {
                    return self.open_timeline_detail(ix);
                }
                Task::none()
            }
            Tab::Findings | Tab::Notes => Task::none(),
        }
    }
}

impl icedtea::collection::ListModel for Hud {
    fn len(&self) -> usize {
        self.sessions().len()
    }

    fn id(&self, index: usize) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        self.sessions()
            .get(index)
            .map(|r| r.session_id.as_str())
            .unwrap_or("")
            .hash(&mut h);
        h.finish()
    }

    fn title(&self, index: usize) -> &str {
        self.sessions()
            .get(index)
            .map(SessionRow::display_title)
            .unwrap_or("")
    }

    fn meta(&self, index: usize) -> Option<&str> {
        self.session_metas
            .get(index)
            .map(String::as_str)
            .filter(|s| !s.is_empty())
    }
}

fn fetch_list(quiet: bool, since: i64) -> Task<Message> {
    Task::perform(
        rpc(move || {
            if quiet && since > 0 {
                control::session_list("", 10_000, 0, since)
            } else if quiet {
                control::session_list_all("")
            } else {
                let (limit, offset, since_rev) = first_list_fetch();
                control::session_list("", limit, offset, since_rev)
            }
        }),
        move |result| Message::ListLoaded { quiet, result },
    )
}

fn fetch_list_page(offset: u32) -> Task<Message> {
    let limit = first_list_fetch().0;
    Task::perform(
        rpc(move || control::session_list("", limit, offset, 0)),
        move |result| Message::ListPage { offset, result },
    )
}

#[derive(Debug, Clone, PartialEq)]
pub(crate) struct LastTimelineReq {
    pub prompt_index: Option<i64>,
    pub around_index: Option<i64>,
    pub query: String,
    pub kind: String,
}

#[derive(Debug, Clone)]
struct TimelineFetch {
    rpc_ref: String,
    sid: String,
    offset: u32,
    append: bool,
    advance: bool,
    gen: u64,
    limit: u32,
    kind: String,
    query: String,
    around: Option<i64>,
    at_index: Option<i64>,
    prompt_index: Option<i64>,
    content_chars: u32,
}

impl Hud {
    fn start_timeline(&mut self, req: TimelineFetch) -> Task<Message> {
        self.last_timeline = Some(LastTimelineReq {
            prompt_index: req.prompt_index,
            around_index: req.around.or(req.at_index),
            query: req.query.clone(),
            kind: req.kind.clone(),
        });
        fetch_timeline(req)
    }
}

fn fetch_timeline(req: TimelineFetch) -> Task<Message> {
    Task::perform(
        rpc(move || {
            control::session_timeline(control::TimelineRequest {
                session: &req.rpc_ref,
                offset: req.offset,
                limit: req.limit,
                content_chars: req.content_chars,
                kind: &req.kind,
                query: &req.query,
                around_index: req.around,
                at_index: req.at_index,
                prompt_index: req.prompt_index,
            })
        }),
        move |result| Message::TimelineLoaded {
            gen: req.gen,
            sid: req.sid.clone(),
            offset: req.offset,
            append: req.append,
            advance: req.advance,
            result,
        },
    )
}

fn finding_menu_key(f: &FindingRow) -> String {
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

/// Escape + pane digits while a field is focused (Captured).
///
/// Enter is **not** here: notes/follow-up use `on_submit`, and search uses
/// `on_submit(ActivateSelected)`. Bare Enter on Ignored still goes through
/// `RawEvent` → open selected session.
fn chrome_key_table() -> icedtea::action::ActionTable<Message> {
    use icedtea::action::Action;
    use icedtea::shortcut::Shortcut;
    let mut table = icedtea::action::ActionTable::new();
    table.insert(
        Action::new("overlay.hide", "Hide", Message::Hide)
            .with_shortcut(Shortcut::parse("escape").expect("escape")),
    );
    for (i, tab) in Tab::ALL.iter().enumerate() {
        let n = i + 1;
        table.insert(
            Action::new(format!("pane.{n}"), tab.label(), Message::SetTab(*tab))
                .with_shortcut(Shortcut::parse(&format!("ctrl+{n}")).expect("pane chord")),
        );
    }
    table
}

/// Arrow / Home / End / Page — list navigation even while a field is focused.
fn is_list_nav_key(kev: &keyboard::Event) -> bool {
    matches!(
        kev,
        keyboard::Event::KeyPressed {
            key: Key::Named(
                Named::ArrowDown
                    | Named::ArrowUp
                    | Named::Home
                    | Named::End
                    | Named::PageDown
                    | Named::PageUp
            ),
            ..
        }
    )
}

fn interesting_hud_event(event: Event, status: event::Status, id: window::Id) -> Option<Message> {
    match event {
        Event::Window(window::Event::CloseRequested) => Some(Message::CloseRequested(id)),
        Event::Window(window::Event::Resized(size)) => Some(Message::WindowSize(size)),
        Event::Keyboard(ref kev) => {
            // List arrows must work while Search sessions is focused (Spotlight).
            // Single-line fields capture them; we still want palette navigation.
            if is_list_nav_key(kev) {
                return Some(Message::RawEvent(event));
            }
            // Captured: Escape + pane chords (chrome_over_input). Enter stays
            // with the focused field's on_submit (or Ignored → RawEvent).
            let ctx = icedtea::key::KeyContext {
                text_input_focused: true,
                ..icedtea::key::KeyContext::default()
            }
            .chrome_over_input();
            if let Some(msg) = icedtea::key::handle(ctx, &chrome_key_table(), kev) {
                return Some(msg);
            }
            if status == event::Status::Ignored {
                return Some(Message::RawEvent(event));
            }
            None
        }
        _ => None,
    }
}

fn notify_subscription() -> Subscription<Message> {
    Subscription::run(notify_stream)
}

fn notify_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        let (tx, rx) = std::sync::mpsc::sync_channel::<()>(64);
        control::set_notify_wake(tx);
        let rx = std::sync::Arc::new(std::sync::Mutex::new(rx));
        loop {
            let rx = rx.clone();
            let got = tokio::task::spawn_blocking(move || {
                rx.lock().ok().and_then(|guard| guard.recv().ok())
            })
            .await
            .ok()
            .flatten();
            if got.is_none() {
                break;
            }
            if iced::futures::SinkExt::send(&mut output, Message::Tick)
                .await
                .is_err()
            {
                break;
            }
        }
    })
}

async fn rpc<F>(f: F) -> Result<Value, String>
where
    F: FnOnce() -> Result<Value, ControlError> + Send + 'static,
{
    tokio::task::spawn_blocking(f)
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())
}

fn delayed_focus(attempt: u8) -> Task<Message> {
    let wait_ms = if attempt == 0 {
        30
    } else {
        40 + u64::from(attempt) * 20
    };
    Task::perform(
        async move {
            tokio::time::sleep(Duration::from_millis(wait_ms)).await;
        },
        move |_| Message::FocusSearch(attempt),
    )
}

fn hotkey_subscription() -> Subscription<Message> {
    Subscription::run(hotkey_stream)
}

fn tray_subscription() -> Subscription<Message> {
    Subscription::run(tray_stream)
}

fn tray_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        loop {
            let action = tokio::task::spawn_blocking(crate::tray::recv_action)
                .await
                .ok()
                .and_then(Result::ok);
            let Some(action) = action else {
                break;
            };
            if iced::futures::SinkExt::send(&mut output, Message::Tray(action))
                .await
                .is_err()
            {
                break;
            }
        }
    })
}

fn register_global_hotkey(
    hk: global_hotkey::hotkey::HotKey,
    label: &str,
) -> Option<GlobalHotKeyManager> {
    match GlobalHotKeyManager::new() {
        Ok(mgr) => {
            if let Err(err) = mgr.register(hk) {
                let msg = format!("failed to register shortcut {label}: {err}");
                crate::log::error(&msg);
                eprintln!("groket-hud: {msg}");
            } else {
                eprintln!("groket-hud: summon shortcut {label}");
            }
            Some(mgr)
        }
        Err(err) => {
            let msg = format!("global hotkey unavailable: {err}");
            crate::log::error(&msg);
            eprintln!("groket-hud: {msg}");
            None
        }
    }
}

fn summon_subscription() -> Subscription<Message> {
    Subscription::run(summon_stream)
}

fn summon_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        loop {
            let action = tokio::task::spawn_blocking(crate::summon::recv_action)
                .await
                .ok()
                .and_then(Result::ok);
            let Some(action) = action else {
                break;
            };
            if iced::futures::SinkExt::send(&mut output, Message::Summon(action))
                .await
                .is_err()
            {
                break;
            }
        }
    })
}

fn hotkey_stream() -> impl iced::futures::Stream<Item = Message> {
    iced::stream::channel(8, |mut output| async move {
        loop {
            let pressed = tokio::task::spawn_blocking(|| {
                GlobalHotKeyEvent::receiver()
                    .recv()
                    .map(|ev| ev.state == HotKeyState::Pressed)
                    .unwrap_or(false)
            })
            .await
            .unwrap_or(false);
            if pressed {
                let _ = iced::futures::SinkExt::send(&mut output, Message::Hotkey).await;
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::TurnsBlock;

    #[test]
    fn browse_mode_is_full_width_only_with_session_and_empty_search() {
        let mut hud = Hud::default();
        assert!(!hud.browse_mode(), "cold start is the picker");
        hud.query = "disk".into();
        assert!(!hud.browse_mode(), "searching is the picker");
        hud.query.clear();
        hud.overview_pending = "s1".into();
        assert!(hud.browse_mode(), "loading a pick is browse");
        hud.overview_pending.clear();
        hud.overview_sid = "s1".into();
        hud.overview = Some(Overview::default());
        assert!(hud.browse_mode());
        hud.query = "switch".into();
        assert!(!hud.browse_mode(), "type again to switch sessions");
    }

    #[test]
    fn show_palette_returns_to_spotlight_not_last_session() {
        let mut hud = Hud {
            visible: false,
            palette_live: false,
            overview_sid: "keep".into(),
            overview: Some(Overview {
                session_id: "keep".into(),
                ..Overview::default()
            }),
            all_sessions: vec![SessionRow {
                session_id: "keep".into(),
                title: "Keep".into(),
                ..SessionRow::default()
            }],
            window_id: Some(window::Id::unique()),
            ..Hud::default()
        };
        let _ = hud.show_palette();
        assert!(hud.visible);
        assert!(hud.overview.is_none(), "summon must not restore browse");
        assert!(hud.overview_sid.is_empty());
        assert!(!hud.browse_mode());
        assert!(matches!(
            hud.list_selection,
            icedtea::collection::Selection::None
        ));
    }

    #[test]
    fn select_timeline_opens_full_pane_detail_not_toggle() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::SelectTimeline(7));
        assert!(hud.is_timeline_open(7));
        assert_eq!(hud.timeline_open(), Some(7));
        assert_eq!(hud.timeline_focus(), Some(7));
        // Second select keeps detail open (Esc closes).
        let _ = hud.update(Message::SelectTimeline(7));
        assert!(hud.is_timeline_open(7));
        let _ = hud.update(Message::SelectTimeline(9));
        assert!(!hud.is_timeline_open(7));
        assert!(hud.is_timeline_open(9));
        let _ = hud.update(Message::CloseTimelineDetail);
        assert!(hud.timeline_open().is_none());
        assert_eq!(hud.timeline_focus(), Some(9));
    }

    #[test]
    fn close_detail_after_next_lands_on_current_event() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(10, "a"), ev_json(11, "b"), ev_json(12, "c")],
            3,
            0,
        );
        let _ = hud.update(Message::SelectTimeline(10));
        let _ = hud.update(Message::TimelineDetailStep(1));
        let _ = hud.update(Message::TimelineDetailStep(1));
        assert!(hud.is_timeline_open(12));
        let _ = hud.update(Message::CloseTimelineDetail);
        assert!(hud.timeline_open().is_none());
        assert_eq!(
            hud.timeline_focus(),
            Some(12),
            "Esc after Next must highlight the last open event, not the first"
        );
    }

    #[test]
    fn timeline_detail_next_prev_steps_filtered_list() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(10, "a"), ev_json(11, "b"), ev_json(12, "c")],
            3,
            0,
        );
        let _ = hud.update(Message::SelectTimeline(10));
        assert_eq!(hud.timeline_detail_pos(), Some((1, 3)));
        let _ = hud.update(Message::TimelineDetailStep(1));
        assert!(hud.is_timeline_open(11));
        assert_eq!(hud.timeline_detail_pos(), Some((2, 3)));
        let _ = hud.update(Message::TimelineDetailStep(1));
        assert!(hud.is_timeline_open(12));
        let _ = hud.update(Message::TimelineDetailStep(1));
        // All-turns filter stops at the end (no wrap).
        assert!(hud.is_timeline_open(12));
        let _ = hud.update(Message::TimelineDetailStep(-1));
        assert!(hud.is_timeline_open(11));
    }

    #[test]
    fn timeline_detail_next_at_turn_end_advances_turn_scope() {
        let mut hud = hud_with_session();
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 2,
                "turns": [
                    {
                        "turnIndex": 0,
                        "promptIndex": 1,
                        "label": "first",
                        "userEventIndex": 10,
                        "firstIndex": 10,
                        "eventIndexes": [10, 11]
                    },
                    {
                        "turnIndex": 1,
                        "promptIndex": 2,
                        "label": "second",
                        "userEventIndex": 20,
                        "firstIndex": 20,
                        "eventIndexes": [20, 21]
                    }
                ]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        // Scope turn 0 with two loaded events.
        hud.events_turn_index = Some(0);
        hud.timeline_prompt = Some(1);
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(10, "a"), ev_json(11, "b")],
            2,
            0,
        );
        hud.rebuild_tl_filter();
        let _ = hud.update(Message::SelectTimeline(11));
        assert!(hud.is_timeline_open(11));
        // Next past last event of turn 0 → request turn 1, open first when page lands.
        let _ = hud.update(Message::TimelineDetailStep(1));
        assert_eq!(hud.events_turn_index, Some(1));
        assert_eq!(hud.timeline_prompt, Some(2));
        assert_eq!(hud.detail_turn_edge, Some(DetailTurnEdge::First));
        // Simulate turn-1 page arriving.
        let gen = hud.timeline_gen;
        let _ = hud.update(Message::TimelineLoaded {
            gen,
            sid: "s1".into(),
            offset: 0,
            append: false,
            advance: false,
            result: Ok(json!({
                "sessionId": "s1",
                "total": 2,
                "offset": 0,
                "limit": 2,
                "events": [
                    {
                        "index": 20,
                        "type": "user message chunk",
                        "kind": "user",
                        "content": "t1",
                        "preview": "t1"
                    },
                    {
                        "index": 21,
                        "type": "agent message chunk",
                        "kind": "agent",
                        "content": "ok",
                        "preview": "ok"
                    }
                ],
            })),
        });
        assert!(
            hud.is_timeline_open(20),
            "first event of next turn should open"
        );
        assert!(hud.detail_turn_edge.is_none());
    }

    #[test]
    fn escape_hide_uses_icedtea_window_policy() {
        use icedtea::window::{should_hide, HideEvent, HidePolicy};
        assert!(should_hide(HidePolicy::Escape, HideEvent::Escape, true));
        assert!(should_hide(HidePolicy::Escape, HideEvent::Escape, false));
        assert!(!should_hide(HidePolicy::Manual, HideEvent::Escape, false));
    }

    #[test]
    fn escape_closes_timeline_detail_before_hiding_hud() {
        let mut hud = Hud {
            visible: true,
            tab: Tab::Timeline,
            timeline_open: Some(3),
            timeline_focus: Some(3),
            ..Hud::default()
        };
        let _ = hud.update(Message::Hide);
        assert!(hud.timeline_open().is_none());
        assert!(hud.visible, "first Esc leaves the HUD up");
        let _ = hud.update(Message::Hide);
        assert!(!hud.visible);
    }

    #[test]
    fn timeline_filter_cache_avoids_per_frame_scan() {
        let mut hud = Hud {
            overview_sid: "s".into(),
            timeline_sid: "s".into(),
            timeline: vec![
                TimelineEvent {
                    index: 0,
                    kind: "user".into(),
                    content: "hello".into(),
                    ..TimelineEvent::default()
                },
                TimelineEvent {
                    index: 1,
                    kind: "tool".into(),
                    content: "run".into(),
                    ..TimelineEvent::default()
                },
                TimelineEvent {
                    index: 2,
                    kind: "agent".into(),
                    content: "ok".into(),
                    ..TimelineEvent::default()
                },
            ],
            ..Hud::default()
        };
        hud.rebuild_tl_filter();
        assert_eq!(hud.filtered_indices(), &[0, 1, 2]);
        hud.timeline_kind = KindFilter::Tools;
        hud.rebuild_tl_filter();
        assert_eq!(hud.filtered_indices(), &[1]);
        assert_eq!(hud.filtered_timeline().len(), 1);
    }

    #[test]
    fn empty_search_fills_spotlight_recent_not_full_catalog() {
        let mut hud = Hud {
            all_sessions: (0..20)
                .map(|i| SessionRow {
                    session_id: format!("s{i}"),
                    title: format!("Session {i}"),
                    sort_epoch: i as f64,
                    ..SessionRow::default()
                })
                .collect(),
            query: String::new(),
            ..Hud::default()
        };
        hud.rerank_visible();
        assert_eq!(hud.sessions().len(), SPOTLIGHT_RECENT);
        // Newest first.
        assert_eq!(hud.sessions()[0].session_id, "s19");
        use icedtea::collection::ListModel;
        assert_eq!(hud.len(), SPOTLIGHT_RECENT);
        assert_eq!(hud.title(0), "Session 19");
    }

    #[test]
    fn ranking_fills_card_heights_for_list_view() {
        let mut hud = Hud {
            all_sessions: vec![SessionRow {
                session_id: "a".into(),
                title: "Alpha".into(),
                context_usage_compact: "40%".into(),
                context_window_usage_pct: Some(40.0),
                ..SessionRow::default()
            }],
            ..Hud::default()
        };
        hud.rerank_visible();
        assert_eq!(hud.session_heights().len(), 1);
        assert!(hud.session_heights()[0] >= 50.0);
        // Spotlight idle: no forced selection until the operator picks.
        assert!(matches!(
            *hud.list_selection(),
            icedtea::collection::Selection::None
        ));
    }

    #[test]
    fn turn_scroll_does_not_move_timeline() {
        let mut hud = Hud {
            tl_window: icedtea::collection::VisibleWindow {
                scroll: 400.0,
                viewport: 400.0,
                start: 0,
                end: 0,
            },
            overview: Some(Overview {
                turns: TurnsBlock {
                    turns: vec![crate::wire::TurnRow {
                        turn_index: 0,
                        ..crate::wire::TurnRow::default()
                    }],
                    ..TurnsBlock::default()
                },
                ..Overview::default()
            }),
            ..Hud::default()
        };
        let mut win = icedtea::collection::VisibleWindow::new(400.0);
        win.scroll = 80.0;
        let _ = hud.update(Message::TurnScroll(win));
        assert!((hud.timeline_window().scroll - 400.0).abs() < f32::EPSILON);
        assert!((hud.turn_window().scroll - 80.0).abs() < f32::EPSILON);
    }

    #[test]
    fn palette_settings_are_fixed_overlay() {
        let w = palette_window_settings();
        assert_eq!(w.size, Size::new(HUD_W, HUD_H));
        assert!(!w.decorations);
        assert!(!w.resizable);
        assert_eq!(w.level, window::Level::AlwaysOnTop);
        assert!(w.icon.is_some());
        #[cfg(target_os = "linux")]
        assert!(w.platform_specific.override_redirect);
    }

    #[test]
    fn close_requested_event_carries_the_window_id() {
        let id = window::Id::unique();
        assert!(matches!(
            interesting_hud_event(
                Event::Window(window::Event::CloseRequested),
                event::Status::Ignored,
                id
            ),
            Some(Message::CloseRequested(got)) if got == id
        ));
    }

    #[test]
    fn interesting_hud_event_ignores_mouse_motion() {
        let ev = Event::Mouse(iced::mouse::Event::CursorMoved {
            position: Point::new(1.0, 1.0),
        });
        assert!(interesting_hud_event(ev, event::Status::Ignored, window::Id::unique()).is_none());
        let key = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Named(Named::ArrowDown),
            modified_key: Key::Named(Named::ArrowDown),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::ArrowDown),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: None,
            repeat: false,
        });
        assert!(
            interesting_hud_event(key.clone(), event::Status::Ignored, window::Id::unique())
                .is_some()
        );
        // Arrows navigate lists even while a text field has Captured them.
        assert!(
            interesting_hud_event(key, event::Status::Captured, window::Id::unique()).is_some()
        );
    }

    #[test]
    fn nav_step_selects_first_session_then_moves() {
        let mut hud = Hud {
            all_sessions: vec![
                SessionRow {
                    session_id: "a".into(),
                    sort_epoch: 1.0,
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "b".into(),
                    sort_epoch: 2.0,
                    ..SessionRow::default()
                },
            ],
            ..Hud::default()
        };
        hud.rerank_visible();
        assert!(matches!(
            hud.list_selection,
            icedtea::collection::Selection::None
        ));
        let _ = hud.nav_step(1);
        assert_eq!(hud.selected_sid().as_deref(), Some("b")); // newest first
        let _ = hud.nav_step(1);
        assert_eq!(hud.selected_sid().as_deref(), Some("a"));
    }

    #[test]
    fn enter_next_drills_picker_to_turns_to_timeline() {
        let mut hud = hud_with_session();
        // Picker path with one session.
        hud.overview = None;
        hud.overview_sid.clear();
        hud.query.clear();
        hud.rerank_visible();
        let _ = hud.nav_step(1);
        let _ = hud.enter_next();
        assert!(!hud.overview_pending.is_empty() || hud.overview.is_some());
    }

    fn escape_pressed() -> Event {
        Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Named(Named::Escape),
            modified_key: Key::Named(Named::Escape),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::Escape),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: None,
            repeat: false,
        })
    }

    #[test]
    fn captured_escape_still_hides_the_overlay() {
        let id = window::Id::unique();
        let esc = escape_pressed();
        assert!(matches!(
            interesting_hud_event(esc.clone(), event::Status::Captured, id),
            Some(Message::Hide)
        ));
        assert!(matches!(
            interesting_hud_event(esc, event::Status::Ignored, id),
            Some(Message::Hide)
        ));
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            typing_notes: true,
            window_id: Some(window::Id::unique()),
            ..Hud::default()
        };
        let _ = hud.update(Message::Hide);
        assert!(!hud.visible);
        assert!(!hud.palette_live);
    }

    #[test]
    fn captured_enter_does_not_activate_session() {
        // Notes/follow-up on_submit own Enter; chrome must not steal Captured Enter.
        let enter = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Named(Named::Enter),
            modified_key: Key::Named(Named::Enter),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::Enter),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: None,
            repeat: false,
        });
        assert!(
            interesting_hud_event(enter, event::Status::Captured, window::Id::unique()).is_none()
        );
    }

    #[test]
    fn select_session_reloads_when_active_index_matches_other_overview() {
        // Search can leave active on row 0 while overview_sid is still another session.
        let mut hud = Hud {
            all_sessions: vec![
                SessionRow {
                    session_id: "keep-out".into(),
                    title: "Hidden".into(),
                    path: "/tmp/keep-out".into(),
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "visible".into(),
                    title: "Visible".into(),
                    path: "/tmp/visible".into(),
                    ..SessionRow::default()
                },
            ],
            active: 0,
            overview_sid: "keep-out".into(),
            overview: Some(Overview {
                session_id: "keep-out".into(),
                meta: crate::wire::SessionMeta {
                    session_id: "keep-out".into(),
                    ..crate::wire::SessionMeta::default()
                },
                ..Overview::default()
            }),
            overview_gen: 1,
            ..Hud::default()
        };
        hud.rerank_visible_keeping("keep-out".into());
        // Filter that only keeps "visible".
        hud.query = "Visible".into();
        hud.rerank_visible_keeping("keep-out".into());
        assert_eq!(hud.sessions().len(), 1);
        assert_eq!(hud.sessions()[0].session_id, "visible");
        assert_eq!(hud.overview_sid, "keep-out");
        // Click the only visible row (index 0) must load it, not no-op as "same".
        let gen_before = hud.overview_gen;
        let _ = hud.update(Message::SelectSession(0));
        // Spotlight clears the query; active remaps to the full-catalog index.
        assert!(hud.query().is_empty());
        assert_eq!(
            hud.sessions()
                .get(hud.active())
                .map(|r| r.session_id.as_str()),
            Some("visible")
        );
        assert!(hud.overview_gen > gen_before || !hud.overview_pending.is_empty());
        assert_eq!(hud.overview_pending, "visible");
    }

    #[test]
    fn scroll_focus_into_view_clamps_to_content() {
        let mut hud = Hud {
            tl_heights: vec![40.0; 5],
            tl_filter: (0..5).collect(),
            timeline: (0..5)
                .map(|i| TimelineEvent {
                    index: i as i64,
                    ..TimelineEvent::default()
                })
                .collect(),
            timeline_focus: Some(4),
            ..Hud::default()
        };
        hud.tl_window.viewport = 100.0;
        let _ = hud.scroll_focus_into_view();
        let content: f32 = hud.tl_heights.iter().copied().sum();
        let max = (content - hud.tl_window.viewport).max(0.0);
        assert!(hud.tl_window.scroll <= max + f32::EPSILON);
        assert!(hud.tl_window.scroll >= 0.0);
    }

    #[test]
    fn app_window_settings_are_decorated() {
        let w = app_window_settings();
        assert!(w.decorations);
        assert!(w.resizable);
        assert_eq!(w.level, window::Level::Normal);
        assert!(w.icon.is_some());
        assert!(!w.exit_on_close_request);
        #[cfg(target_os = "linux")]
        assert!(!w.platform_specific.override_redirect);
    }

    #[test]
    fn window_settings_come_from_prepared_bootstrap() {
        let overlay = overlay_prepared();
        assert_eq!(overlay.window.size, Size::new(HUD_W, HUD_H));
        assert!(!overlay.window.decorations);
        assert_eq!(overlay.window.max_size, Some(Size::new(HUD_W, HUD_H)));
        assert!(!overlay.iced_settings.fonts.is_empty());
        let desk = desktop_prepared();
        assert!(desk.window.decorations);
        assert!(desk.window.resizable);
        assert!(!desk.window.exit_on_close_request);
        let src = include_str!("app.rs");
        assert!(src.contains("bootstrap_with_catalog"));
        assert!(src.contains(".open()"));
        assert!(src.contains("retarget"));
    }

    #[test]
    fn set_tab_without_overview_stays_on_overview_when_no_selection() {
        let mut hud = Hud::default();
        assert!(hud.overview().is_none());
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        assert_eq!(hud.tab(), Tab::Overview);
        let _ = hud.update(Message::SetTab(Tab::Turns));
        assert_eq!(hud.tab(), Tab::Overview);
    }

    #[test]
    fn set_tab_timeline_with_selection_loads_overview() {
        let mut hud = Hud {
            all_sessions: vec![SessionRow {
                session_id: "s1".into(),
                path: "/tmp/s1".into(),
                ..SessionRow::default()
            }],
            overview_gen: 0,
            ..Hud::default()
        };
        hud.rerank_visible();
        hud.set_active(0);
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert!(!hud.overview_pending.is_empty() || hud.overview_gen > 0);
    }

    #[test]
    fn wants_events_on_timeline_without_turn_or_query() {
        let hud = Hud {
            tab: Tab::Timeline,
            timeline_query: String::new(),
            timeline_prompt: None,
            ..Hud::default()
        };
        assert!(hud.wants_events());
    }

    #[test]
    fn selected_sid_is_none_when_list_selection_is_none() {
        let mut hud = Hud {
            all_sessions: vec![
                SessionRow {
                    session_id: "a".into(),
                    title: "Alpha".into(),
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "b".into(),
                    title: "Beta".into(),
                    ..SessionRow::default()
                },
            ],
            overview_sid: "a".into(),
            overview: Some(Overview {
                session_id: "a".into(),
                meta: crate::wire::SessionMeta {
                    session_id: "a".into(),
                    status: "running".into(),
                    ..crate::wire::SessionMeta::default()
                },
                ..Overview::default()
            }),
            ..Hud::default()
        };
        hud.rerank_visible_keeping("a".into());
        assert_eq!(hud.selected_sid().as_deref(), Some("a"));
        // Filter that hides overview session a — selection cleared.
        hud.query = "Beta".into();
        hud.rerank_visible_keeping("a".into());
        assert_eq!(hud.sessions().len(), 1);
        assert_eq!(hud.sessions()[0].session_id, "b");
        assert!(matches!(
            hud.list_selection,
            icedtea::collection::Selection::None
        ));
        assert!(
            hud.selected_sid().is_none(),
            "must not invent sessions()[0] while highlight is cleared"
        );
        // Enter/activate takes first visible match.
        hud.ensure_rail_selection_for_activate();
        assert_eq!(hud.selected_sid().as_deref(), Some("b"));
    }

    #[test]
    fn quiet_load_overview_keeps_open_session_when_rail_cleared() {
        // Overview A open; search hides A so selection is None; quiet tick must
        // refresh A, not clear overview.
        let mut hud = Hud {
            all_sessions: vec![
                SessionRow {
                    session_id: "keep-open".into(),
                    title: "Keep".into(),
                    path: "/tmp/keep-open".into(),
                    status: "running".into(),
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "other".into(),
                    title: "Other".into(),
                    path: "/tmp/other".into(),
                    ..SessionRow::default()
                },
            ],
            overview_sid: "keep-open".into(),
            overview: Some(Overview {
                session_id: "keep-open".into(),
                meta: crate::wire::SessionMeta {
                    session_id: "keep-open".into(),
                    path: "/tmp/keep-open".into(),
                    status: "running".into(),
                    ..crate::wire::SessionMeta::default()
                },
                ..Overview::default()
            }),
            list_selection: icedtea::collection::Selection::None,
            active: 0,
            overview_gen: 3,
            status: "keep-open · running".into(),
            ..Hud::default()
        };
        hud.query = "Other".into();
        hud.rerank_visible_keeping("keep-open".into());
        assert!(matches!(
            hud.list_selection,
            icedtea::collection::Selection::None
        ));
        assert!(hud.selected_sid().is_none());
        assert_eq!(hud.detail_sid().as_deref(), Some("keep-open"));
        // Quiet refresh must not wipe.
        let task = hud.load_overview(true);
        let _ = task; // schedule RPC; state before response:
        assert_eq!(hud.overview_sid, "keep-open");
        assert!(hud.overview.is_some());
        assert_eq!(hud.overview_pending, "keep-open");
        assert!(
            hud.status.starts_with("keep-open") || !hud.overview_pending.is_empty(),
            "open session preserved for quiet path"
        );
        // Explicit activate with no rail choice and we still have overview:
        // load_overview(false) without selection clears only when no detail_sid.
        // After ensure activate:
        hud.list_selection = icedtea::collection::Selection::None;
        hud.overview = Some(Overview {
            session_id: "keep-open".into(),
            meta: crate::wire::SessionMeta {
                session_id: "keep-open".into(),
                path: "/tmp/keep-open".into(),
                status: "running".into(),
                ..crate::wire::SessionMeta::default()
            },
            ..Overview::default()
        });
        hud.overview_sid = "keep-open".into();
        // Quiet again after clear selection.
        let _ = hud.load_overview(true);
        assert_eq!(hud.overview_sid, "keep-open");
        assert_eq!(hud.overview_pending, "keep-open");
    }

    #[test]
    fn overview_loaded_quiet_updates_status_and_rail() {
        let mut hud = Hud {
            all_sessions: vec![
                SessionRow {
                    session_id: "linus".into(),
                    title: "Linus".into(),
                    sort_epoch: 1.0,
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "disk".into(),
                    title: "Disk".into(),
                    path: "/tmp/disk".into(),
                    sort_epoch: 2.0,
                    ..SessionRow::default()
                },
            ],
            overview_gen: 1,
            status: "stale-other · running".into(),
            ..Hud::default()
        };
        hud.rerank_visible();
        let data = json!({
            "meta": {
                "sessionId": "disk",
                "path": "/tmp/disk",
                "status": "running",
                "title": "Disk"
            },
            "turns": { "total": 0, "turns": [] },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: 1,
            sid: "disk".into(),
            quiet: true,
            result: Ok(data),
        });
        assert_eq!(hud.overview_sid, "disk");
        assert!(
            hud.status.starts_with("disk"),
            "quiet load must still set footer status: {}",
            hud.status
        );
        assert_eq!(hud.selected_sid().as_deref(), Some("disk"));
        // Open session is pinned to the front of the recent strip.
        assert_eq!(hud.sessions()[0].session_id, "disk");
        assert!(matches!(
            hud.list_selection,
            icedtea::collection::Selection::Single(0)
        ));
    }

    #[test]
    fn selecting_a_session_does_not_start_a_timeline_rpc() {
        let mut hud = Hud {
            all_sessions: vec![SessionRow {
                session_id: "s1".into(),
                path: "/tmp/s1".into(),
                ..SessionRow::default()
            }],
            last_timeline: Some(LastTimelineReq {
                prompt_index: Some(1),
                around_index: None,
                query: "old".into(),
                kind: String::new(),
            }),
            ..Hud::default()
        };
        hud.rerank_visible();
        let _ = hud.update(Message::SelectSession(0));
        assert_eq!(hud.tab(), Tab::Overview);
        assert!(hud.last_timeline().is_none());
        assert!(!hud.timeline_loading());
    }

    #[test]
    fn turns_list_binds_overview_not_turn_bodies() {
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/overview.json");
        let data: Value =
            serde_json::from_str(&std::fs::read_to_string(path).expect("fixture")).expect("json");
        let mut hud = Hud {
            overview_gen: 1,
            ..Hud::default()
        };
        let _ = hud.update(Message::OverviewLoaded {
            gen: 1,
            sid: "sess-wire".into(),
            quiet: true,
            result: Ok(data),
        });
        // Turns tab is fixed cards; no per-turn extract buffers.
        assert!(hud.extract(ExtractKey::Overview("session")).is_some());
        assert!(!hud.filtered_turn_indices().is_empty());
    }

    #[test]
    fn pane_digit_keys_route_while_status_would_be_captured() {
        let digit = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Character("2".into()),
            modified_key: Key::Character("2".into()),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::Digit2),
            location: iced::keyboard::Location::Standard,
            modifiers: icedtea::shortcut::primary(),
            text: None,
            repeat: false,
        });
        assert!(matches!(
            interesting_hud_event(digit, event::Status::Captured, window::Id::unique()),
            Some(Message::SetTab(Tab::Turns))
        ));
    }

    #[test]
    fn turns_query_filters_prompt_and_label() {
        let mut hud = Hud {
            overview: Some(Overview {
                turns: crate::wire::TurnsBlock {
                    total: 2,
                    turns: vec![
                        crate::wire::TurnRow {
                            turn_index: 0,
                            label: "first".into(),
                            summary: "alpha prompt".into(),
                            ..Default::default()
                        },
                        crate::wire::TurnRow {
                            turn_index: 1,
                            label: "second".into(),
                            summary: "beta prompt".into(),
                            ..Default::default()
                        },
                    ],
                    ..Default::default()
                },
                ..Default::default()
            }),
            ..Hud::default()
        };
        hud.rebuild_turns_filter();
        assert_eq!(hud.filtered_turn_indices().len(), 2);
        let _ = hud.update(Message::TurnsQuery("beta".into()));
        assert_eq!(hud.filtered_turn_indices(), &[1]);
        let _ = hud.update(Message::TurnsQuery("first".into()));
        assert_eq!(hud.filtered_turn_indices(), &[0]);
        let _ = hud.update(Message::TurnsQuery(String::new()));
        assert_eq!(hud.filtered_turn_indices().len(), 2);
    }

    #[test]
    fn clearing_search_keeps_overview_session_on_rail() {
        let mut hud = Hud {
            all_sessions: vec![
                SessionRow {
                    session_id: "aaa-first".into(),
                    path: "/tmp/a".into(),
                    title: "alpha disk usage".into(),
                    sort_epoch: 2.0,
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "bbb-target".into(),
                    path: "/tmp/b".into(),
                    title: "multi harness expansion".into(),
                    sort_epoch: 1.0,
                    ..SessionRow::default()
                },
            ],
            query: "multi".into(),
            overview_sid: "bbb-target".into(),
            overview: Some(Overview {
                meta: crate::wire::SessionMeta {
                    session_id: "bbb-target".into(),
                    path: "/tmp/b".into(),
                    ..Default::default()
                },
                ..Default::default()
            }),
            ..Hud::default()
        };
        hud.rerank_visible();
        assert_eq!(hud.sessions().len(), 1);
        assert_eq!(hud.sessions()[0].session_id, "bbb-target");
        hud.active = 0;
        // Clear search: must not map filtered index 0 onto all_sessions[0] (aaa-first).
        let _ = hud.update(Message::SearchChanged(String::new()));
        assert!(hud.query.is_empty());
        assert_eq!(hud.selected_sid().as_deref(), Some("bbb-target"));
        assert_eq!(hud.overview_rpc_ref(), "bbb-target");
    }

    #[test]
    fn go_to_turn_events_sends_prompt_index() {
        let mut hud = hud_with_session();
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 1,
                "turns": [{
                    "turnIndex": 1,
                    "promptIndex": 4,
                    "summary": "please",
                    "assistantSummary": "done",
                    "open": false,
                    "userEventIndex": 10,
                    "eventIndexes": [10, 11, 12]
                }]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        let _ = hud.update(Message::JumpTimeline(10));
        let req = hud.last_timeline().expect("timeline rpc");
        assert_eq!(req.prompt_index, Some(4));
        assert_eq!(req.around_index, Some(10));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert_eq!(hud.events_turn_index, Some(1));
        assert_eq!(hud.turns_focus, Some(1));
    }

    #[test]
    fn events_bracket_picks_first_turn_then_next() {
        // Shipped keyboard path for Events turn-pick / next-turn (walk harness).
        let mut hud = hud_with_session();
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 2,
                "turns": [
                    {
                        "turnIndex": 0,
                        "promptIndex": 1,
                        "label": "first",
                        "summary": "a",
                        "userEventIndex": 1,
                        "eventIndexes": [1, 2]
                    },
                    {
                        "turnIndex": 1,
                        "promptIndex": 2,
                        "label": "second",
                        "summary": "b",
                        "userEventIndex": 5,
                        "eventIndexes": [5, 6]
                    }
                ]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        assert!(hud.events_turn_index.is_none());
        let press = |ch: &str| {
            Event::Keyboard(keyboard::Event::KeyPressed {
                key: Key::Character(ch.into()),
                modified_key: Key::Character(ch.into()),
                physical_key: iced::keyboard::key::Physical::Code(
                    iced::keyboard::key::Code::BracketRight,
                ),
                location: iced::keyboard::Location::Standard,
                modifiers: KeyMods::default(),
                text: None,
                repeat: false,
            })
        };
        let _ = hud.update(Message::RawEvent(press("]")));
        assert_eq!(hud.events_turn_index, Some(0));
        let _ = hud.update(Message::RawEvent(press("]")));
        assert_eq!(hud.events_turn_index, Some(1));
    }

    #[test]
    fn turn_card_opens_timeline_filtered_to_that_turn_not_event_detail() {
        let mut hud = hud_with_session();
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 2,
                "turns": [
                    {
                        "turnIndex": 0,
                        "promptIndex": 1,
                        "label": "first",
                        "summary": "a",
                        "userEventIndex": 10,
                        "firstIndex": 10,
                        "eventIndexes": [10, 11, 12]
                    },
                    {
                        "turnIndex": 1,
                        "promptIndex": 2,
                        "label": "second",
                        "summary": "b",
                        "userEventIndex": 20,
                        "firstIndex": 20,
                        "eventIndexes": [20, 21]
                    }
                ]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        hud.tab = Tab::Turns;
        // Same message the turn card emits on click.
        let _ = hud.update(Message::EventsTurnPicked(EventsTurnPick {
            turn_index: Some(1),
            label: "second".into(),
        }));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert_eq!(hud.events_turn_index, Some(1));
        assert_eq!(hud.timeline_prompt, Some(2));
        assert!(
            hud.timeline_open().is_none(),
            "turn click must show the turn event list, not open one event"
        );
        let req = hud.last_timeline().expect("turn-scoped fetch");
        assert_eq!(req.prompt_index, Some(2));
    }

    #[test]
    fn next_turn_events_opens_following_turn_and_focuses_turns() {
        let mut hud = hud_with_session();
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 2,
                "turns": [
                    {
                        "turnIndex": 0,
                        "promptIndex": 1,
                        "label": "first",
                        "summary": "a",
                        "userEventIndex": 1,
                        "eventIndexes": [1, 2]
                    },
                    {
                        "turnIndex": 1,
                        "promptIndex": 2,
                        "label": "second",
                        "summary": "b",
                        "userEventIndex": 5,
                        "eventIndexes": [5, 6]
                    }
                ]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        let _ = hud.update(Message::JumpTimeline(1));
        assert_eq!(hud.timeline_prompt, Some(1));
        assert_eq!(hud.events_turn_index, Some(0));
        assert_eq!(hud.turns_focus, Some(0));
        // `]` advances Events turn scope (no dedicated Next chip / message).
        let press_bracket = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Character("]".into()),
            modified_key: Key::Character("]".into()),
            physical_key: iced::keyboard::key::Physical::Code(
                iced::keyboard::key::Code::BracketRight,
            ),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: None,
            repeat: false,
        });
        let _ = hud.update(Message::RawEvent(press_bracket));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert_eq!(hud.timeline_prompt, Some(2));
        assert_eq!(hud.events_turn_index, Some(1));
        assert_eq!(hud.turns_focus, Some(1));
        let req = hud.last_timeline().expect("next turn timeline");
        assert_eq!(req.prompt_index, Some(2));
        // Focus is the next turn’s user event when present.
        assert_eq!(req.around_index, Some(5));
    }

    #[test]
    fn events_turn_pick_scopes_prompt_index() {
        let mut hud = hud_with_session();
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 1,
                "turns": [{
                    "turnIndex": 3,
                    "promptIndex": 9,
                    "label": "third",
                    "userEventIndex": 20,
                    "eventIndexes": [20, 21]
                }]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        let _ = hud.update(Message::EventsTurnPicked(EventsTurnPick {
            turn_index: Some(3),
            label: "third".into(),
        }));
        assert_eq!(hud.events_turn_index, Some(3));
        assert_eq!(hud.timeline_prompt, Some(9));
        assert_eq!(hud.turns_focus, Some(3));
        let req = hud.last_timeline().expect("pick timeline");
        assert_eq!(req.prompt_index, Some(9));
        let _ = hud.update(Message::EventsTurnPicked(EventsTurnPick {
            turn_index: None,
            label: "All turns".into(),
        }));
        assert!(hud.events_turn_index.is_none());
        assert!(hud.timeline_prompt.is_none());
        let all = hud.last_timeline().expect("all-turns fetch");
        assert!(all.prompt_index.is_none());
    }

    #[test]
    fn finding_jump_without_event_opens_overview() {
        let f = FindingRow {
            title: "x".into(),
            ..FindingRow::default()
        };
        assert!(matches!(
            crate::view::finding_jump(&f),
            Message::SetTab(Tab::Overview)
        ));
    }

    #[test]
    fn events_tab_all_turns_fetches_full_timeline() {
        // All turns (no prompt filter) still loads the first page — empty shell
        // was dishonest (see should_fetch_timeline).
        let mut hud = hud_with_session();
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 1,
                "turns": [{
                    "turnIndex": 0,
                    "promptIndex": 1,
                    "summary": "a",
                    "userEventIndex": 1,
                    "eventIndexes": [1, 2]
                }]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        hud.tab = Tab::Turns;
        hud.events_turn_index = None;
        hud.timeline_prompt = None;
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        let req = hud.last_timeline().expect("all-turns page");
        assert!(req.prompt_index.is_none());
        assert!(req.query.is_empty());
    }

    #[test]
    fn enter_on_turns_opens_turn_scoped_timeline_not_event_detail() {
        let mut hud = hud_with_session();
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 1,
                "turns": [{
                    "turnIndex": 0,
                    "promptIndex": 1,
                    "label": "only",
                    "summary": "hi",
                    "userEventIndex": 10,
                    "firstIndex": 10,
                    "eventIndexes": [10, 11]
                }]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        hud.tab = Tab::Turns;
        hud.turns_focus = Some(0);
        let _ = hud.enter_next();
        assert_eq!(hud.tab(), Tab::Timeline);
        assert_eq!(hud.events_turn_index, Some(0));
        assert!(hud.timeline_open().is_none());
        let req = hud.last_timeline().expect("turn-scoped");
        assert_eq!(req.prompt_index, Some(1));
    }

    #[test]
    fn jump_from_turn_opens_timeline_detail_on_that_event() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_json(0, "a"),
                ev_json(1, "b"),
                ev_json(2, "c"),
                ev_json(3, "user"),
            ],
            10,
            0,
        );
        hud.tab = Tab::Turns;
        let _ = hud.update(Message::JumpTimeline(3));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert!(hud.is_timeline_open(3));
        assert_eq!(hud.timeline_focus(), Some(3));
        assert!(hud.last_timeline().is_some());
    }

    #[test]
    fn jump_missing_event_reloads_around_it() {
        let mut hud = hud_with_session();
        let gen = hud.timeline_gen;
        hud.tab = Tab::Turns;
        let _ = hud.update(Message::JumpTimeline(99));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert!(hud.is_timeline_open(99));
        assert_eq!(hud.timeline_focus(), Some(99));
        assert!(hud.timeline_loading());
        assert!(hud.timeline_gen > gen);
    }

    #[test]
    fn finding_and_note_expanders_open_independently() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::FindingExpand {
            id: "a".into(),
            open: true,
        });
        let _ = hud.update(Message::FindingExpand {
            id: "b".into(),
            open: true,
        });
        assert!(hud.finding_expanded("a"));
        assert!(hud.finding_expanded("b"));
        let _ = hud.update(Message::NoteExpand {
            id: "n1".into(),
            open: true,
        });
        let _ = hud.update(Message::NoteExpand {
            id: "n2".into(),
            open: true,
        });
        assert!(hud.note_expanded("n1"));
        assert!(hud.note_expanded("n2"));
        let _ = hud.update(Message::FindingExpand {
            id: "a".into(),
            open: false,
        });
        assert!(!hud.finding_expanded("a"));
        assert!(hud.finding_expanded("b"));
    }

    #[test]
    fn overview_load_binds_copyable_fields() {
        let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/overview.json");
        let data: Value =
            serde_json::from_str(&std::fs::read_to_string(path).expect("fixture")).expect("json");
        let mut hud = Hud {
            overview_gen: 1,
            ..Hud::default()
        };
        let _ = hud.update(Message::OverviewLoaded {
            gen: 1,
            sid: "sess-wire".into(),
            quiet: true,
            result: Ok(data),
        });
        assert_eq!(
            hud.extract_src(ExtractKey::Overview("session")).as_deref(),
            Some("sess-wire")
        );
        assert_eq!(
            hud.extract_src(ExtractKey::Overview("path")).as_deref(),
            Some("/workspace/sess-wire")
        );
        // Raw event/findings counts are not copyable Overview fields.
        assert_eq!(hud.extract_src(ExtractKey::Overview("events")), None);
        assert!(hud.extract(ExtractKey::Overview("session")).is_some());
    }

    #[test]
    fn expanding_an_event_binds_extract_text() {
        use crate::format::event_body_text;
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(3, "# hello **md**")],
            10,
            0,
        );
        let _ = hud.update(Message::SelectTimeline(3));
        let ev = hud.timeline.iter().find(|e| e.index == 3).unwrap();
        let src = event_body_text(ev);
        assert!(src.contains("# hello **md**"));
        assert!(!src.contains("#3 "));
        assert!(hud.extract(ExtractKey::Event(3)).is_some());
        assert_eq!(
            hud.extract_src(ExtractKey::Event(3)).as_deref(),
            Some(src.as_str())
        );
    }

    #[test]
    fn expanding_a_tool_event_binds_input_and_output() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![json!({
                "index": 7,
                "type": "tool_call",
                "kind": "tool",
                "toolName": "search_replace",
                "toolCallId": "c1",
                "content": "replaced 1 file",
                "toolFields": [
                    {"id": "path", "label": "Path", "value": "src/a.rs"},
                    {"id": "old_string", "label": "Old", "value": "foo()"}
                ]
            })],
            10,
            0,
        );
        let _ = hud.update(Message::SelectTimeline(7));
        assert_eq!(
            hud.field("event.7.in.path").map(|c| c.text()).as_deref(),
            Some("src/a.rs")
        );
        assert_eq!(
            hud.field("event.7.in.old_string")
                .map(|c| c.text())
                .as_deref(),
            Some("foo()")
        );
        assert!(hud
            .field("event.7.out")
            .is_some_and(|c| c.text().contains("replaced 1 file")));
        let _ = hud.update(Message::Select {
            id: "event.7.in.path".into(),
            action: iced::widget::text_editor::Action::SelectAll,
        });
        assert_eq!(hud.copyable_text().trim(), "src/a.rs");
        let copy = hud
            .context_actions()
            .into_iter()
            .find(|a| a.id.as_str() == "edit.copy")
            .expect("copy");
        assert!(copy.enabled);
        let _ = hud.update(Message::Yank);
        // Copy is silent (no success toast).
        assert!(!hud.toasts().iter().any(|t| t.text.contains("Copied")));
    }

    #[test]
    fn expanding_read_file_tool_call_binds_paired_result_output() {
        // tool_call content is empty; file dump is on tool_call_update only.
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                json!({
                    "index": 10,
                    "type": "tool_call",
                    "kind": "tool",
                    "toolName": "read_file",
                    "toolCallId": "rf1",
                    "toolFamily": "read",
                    "content": "",
                    "rawInput": {"target_file": "a.py"},
                    "toolFields": [
                        {"id": "target_file", "label": "target_file", "value": "a.py"}
                    ]
                }),
                json!({
                    "index": 11,
                    "type": "tool_call_update",
                    "kind": "tool_result",
                    "toolName": "read_file",
                    "toolCallId": "rf1",
                    "toolFamily": "read",
                    "content": "1→def foo():\n2→    return 1\n",
                    "rawInput": {"target_file": "a.py"}
                }),
            ],
            10,
            0,
        );
        let _ = hud.update(Message::SelectTimeline(10));
        let out = hud
            .field("event.10.out")
            .map(|c| c.text())
            .expect("paired read_file output bound on tool_call");
        assert!(out.contains("def foo"), "got {out:?}");
        assert!(!out.contains('→'), "prefixes stripped: {out:?}");
        assert!(hud.field("event.10.in.target_file").is_some());
    }

    #[test]
    fn yank_uses_selectables_selection() {
        let mut hud = Hud::default();
        hud.bind_field("overview.session", "sess-wire");
        let _ = hud.update(Message::Select {
            id: "overview.session".into(),
            action: iced::widget::text_editor::Action::SelectAll,
        });
        assert_eq!(hud.copyable_text().trim(), "sess-wire");
        let _ = hud.update(Message::Select {
            id: "overview.session".into(),
            action: iced::widget::text_editor::Action::Edit(
                iced::widget::text_editor::Edit::Insert('x'),
            ),
        });
        assert_eq!(
            hud.extract_src(ExtractKey::Overview("session")).as_deref(),
            Some("sess-wire")
        );
    }

    #[test]
    fn timeline_search_does_not_apply_until_debounce() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::TimelineQuery("grep".into()));
        assert_eq!(hud.timeline_query_draft(), "grep");
        assert_eq!(hud.timeline_query(), "");
        assert!(hud.timeline_search_gen > 0);
        let _ = hud.update(Message::TimelineSearchApply(0));
        assert_eq!(hud.timeline_query_draft(), "grep");
        assert_eq!(hud.timeline_query(), "");
    }

    fn ev_json(index: i64, content: &str) -> Value {
        json!({
            "index": index,
            "type": "agent_message_chunk",
            "kind": "agent",
            "content": content,
            "contentLength": content.len(),
            "contentTruncated": content.len() < 80,
        })
    }

    fn hud_with_session() -> Hud {
        Hud {
            tab: Tab::Timeline,
            overview_sid: "s1".into(),
            timeline_sid: "s1".into(),
            timeline_gen: 1,
            all_sessions: vec![SessionRow {
                session_id: "s1".into(),
                path: "/tmp/s1".into(),
                ..SessionRow::default()
            }],
            ..Hud::default()
        }
    }

    fn load_page(
        hud: &mut Hud,
        offset: u32,
        append: bool,
        advance: bool,
        events: Vec<Value>,
        total: u32,
        page_offset: u32,
    ) {
        let gen = hud.timeline_gen;
        let _ = hud.update(Message::TimelineLoaded {
            gen,
            sid: "s1".into(),
            offset,
            append,
            advance,
            result: Ok(json!({
                "sessionId": "s1",
                "total": total,
                "offset": page_offset,
                "limit": events.len(),
                "events": events,
            })),
        });
    }

    #[test]
    fn timeline_query_holds_unfiltered_ids_until_apply() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_json(0, "alpha"),
                ev_json(1, "beta needle"),
                ev_json(2, "gamma"),
            ],
            80,
            0,
        );
        let held: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert_eq!(held, vec![0, 1, 2]);
        let gen_before = hud.timeline_gen;
        let _ = hud.update(Message::TimelineQuery("needle".into()));
        assert_eq!(hud.timeline_query_draft(), "needle");
        assert_eq!(hud.timeline_query(), "");
        let after_query: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert_eq!(after_query, held);
        let _ = hud.update(Message::LoadMoreTimeline);
        let mut far = icedtea::collection::VisibleWindow::new(400.0);
        far.scroll = 10_000.0;
        far.end = 20;
        let _ = hud.update(Message::TimelineScroll(far));
        // In-flight fill from the old gen, or a new-query slice on the new gen,
        // must not mix into the held page.
        let _ = hud.update(Message::TimelineLoaded {
            gen: gen_before,
            sid: "s1".into(),
            offset: 3,
            append: true,
            advance: true,
            result: Ok(json!({
                "sessionId": "s1",
                "total": 2,
                "offset": 0,
                "limit": 2,
                "events": [ev_json(1, "beta needle"), ev_json(50, "later needle")],
            })),
        });
        let _ = hud.update(Message::TimelineLoaded {
            gen: hud.timeline_gen,
            sid: "s1".into(),
            offset: 0,
            append: true,
            advance: true,
            result: Ok(json!({
                "sessionId": "s1",
                "total": 2,
                "offset": 0,
                "limit": 2,
                "events": [ev_json(1, "beta needle"), ev_json(50, "later needle")],
            })),
        });
        let shown: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert_eq!(shown, held);
        assert!(!shown.contains(&50));
    }

    #[test]
    fn around_page_advances_from_owner_offset() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_json(20, "a"),
                ev_json(21, "b"),
                ev_json(22, "c"),
                ev_json(23, "d"),
            ],
            100,
            12,
        );
        assert_eq!(hud.timeline_next, 16);
        assert_eq!(hud.timeline_offset, 12);
        assert_eq!(hud.timeline_meta(), "13-16 of 100");
        let first: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert_eq!(first, vec![20, 21, 22, 23]);
        // A later jump replaces the prefix window; the pager must follow
        // the new owner offset, not keep "1-60 of …".
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(2000, "late"), ev_json(2001, "later")],
            7663,
            1192,
        );
        assert_eq!(hud.timeline_offset, 1192);
        assert_eq!(hud.timeline_meta(), "1193-1194 of 7663");
    }

    #[test]
    fn scroll_up_after_jump_loads_earlier_events() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_json(20, "a"),
                ev_json(21, "b"),
                ev_json(22, "c"),
                ev_json(23, "d"),
            ],
            100,
            12,
        );
        assert_eq!(hud.timeline_offset, 12);
        assert!(
            hud.timeline_loading,
            "landing mid-session must fetch the page above"
        );
        let y_before = hud.timeline_window().scroll;
        let earlier: Vec<Value> = (8..20).map(|i| ev_json(i, "prev")).collect();
        load_page(&mut hud, 0, true, true, earlier, 100, 0);
        assert_eq!(hud.timeline_offset, 0);
        let ids: Vec<i64> = hud.timeline.iter().map(|e| e.index).collect();
        assert!(ids.contains(&8));
        assert!(ids.contains(&23));
        assert!(hud.timeline_window().scroll > y_before);
        assert_eq!(hud.timeline_meta(), "1-16 of 100");
    }

    #[test]
    fn expand_refetches_open_chars_and_paints_full_json() {
        use crate::format::{body_paint, BodyPaint};
        let mut hud = hud_with_session();
        let stub = "{".to_string() + &"\"k\":".repeat(200) + "1";
        assert!(!stub.ends_with('}'));
        // Incomplete JSON object is not valid JSON → chat agent path is Markdown.
        assert_eq!(body_paint("agent", &stub, true), BodyPaint::Markdown);
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![json!({
                "index": 3,
                "type": "tool_call_update",
                "kind": "tool_result",
                "content": stub,
                "contentLength": 9000,
                "contentTruncated": true,
            })],
            10,
            0,
        );
        let req = hud.open_event_fetch(3, hud.timeline_gen);
        assert_eq!(req.content_chars, TIMELINE_OPEN_CHARS);
        assert_eq!(req.at_index, Some(3));
        assert!(!req.advance);
        assert!(req.append);
        let next_before = hud.timeline_next;
        let _ = hud.update(Message::SelectTimeline(3));
        assert!(hud.is_timeline_open(3));
        let full = stub.clone() + "}";
        let gen = hud.timeline_gen;
        let _ = hud.update(Message::TimelineLoaded {
            gen,
            sid: "s1".into(),
            offset: 0,
            append: true,
            advance: false,
            result: Ok(json!({
                "sessionId": "s1",
                "total": 10,
                "offset": 3,
                "limit": 1,
                "events": [{
                    "index": 3,
                    "type": "tool_call_update",
                    "kind": "tool_result",
                    "content": full,
                    "contentLength": 9000,
                    "contentTruncated": false,
                }],
            })),
        });
        assert_eq!(hud.timeline_next, next_before);
        let ev = hud.timeline.iter().find(|e| e.index == 3).expect("row");
        assert!(ev.content.ends_with('}'));
        assert_eq!(body_paint(&ev.kind, &ev.content, true), BodyPaint::Json);
        assert!(hud.is_timeline_open(3));
    }

    #[test]
    fn copy_path_warns_without_a_session() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::CopyPath);
        assert!(hud.toasts().iter().any(|t| t.text.contains("No path")));
    }

    #[test]
    fn right_click_opens_context_menu() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::Cursor(icedtea::layout::CursorEvent::Move(
            Point::new(40.0, 80.0),
        )));
        let _ = hud.update(Message::Cursor(icedtea::layout::CursorEvent::Context));
        assert_eq!(hud.context_origin(), Some(Point::new(40.0, 80.0)));
        let _ = hud.update(Message::ContextDismiss);
        assert_eq!(hud.context_origin(), None);
    }

    #[test]
    fn escape_closes_context_menu_not_the_overlay() {
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            context: Some(Point::new(8.0, 8.0)),
            ..Hud::default()
        };
        let _ = hud.update(Message::Hide);
        assert!(hud.context_origin().is_none());
        assert!(hud.visible);
        assert!(hud.palette_live);
    }

    #[test]
    fn context_actions_are_copy_and_copy_path() {
        let hud = Hud::default();
        let acts = hud.context_actions();
        assert_eq!(acts.len(), 2);
        assert_eq!(acts[0].title, "Copy");
        assert!(!acts[0].enabled);
        assert_eq!(acts[1].title, "Copy path");
        assert!(!acts[1].enabled);
    }

    #[test]
    fn window_size_tracks_resize() {
        let mut hud = Hud::default();
        let _ = hud.update(Message::WindowSize(Size::new(900.0, 640.0)));
        assert_eq!(hud.window_size(), Size::new(900.0, 640.0));
    }

    #[test]
    fn boot_is_palette_not_window() {
        let hud = Hud::default();
        assert!(!hud.window_mode());
    }

    #[test]
    fn close_requested_keeps_process_in_window_mode() {
        // Decorated close must not iced::exit — dismiss_window closes the
        // surface and the summon hotkey opens a fresh palette.
        let mut hud = Hud {
            window_mode: true,
            visible: true,
            palette_live: true,
            ..Hud::default()
        };
        let _ = hud.dismiss_window();
        assert!(!hud.window_mode());
        assert!(!hud.visible);
        assert!(hud.window_id.is_none());
    }

    #[test]
    fn overlay_already_mapped_skips_remap() {
        assert!(overlay_already_mapped(true, false, true));
        assert!(!overlay_already_mapped(false, false, true));
        assert!(!overlay_already_mapped(true, true, true));
        assert!(!overlay_already_mapped(true, false, false));
    }

    #[test]
    fn hide_palette_destroys_overlay_window() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_mode: false,
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.hide_palette();
        assert!(!hud.visible);
        assert!(!hud.palette_live);
        assert!(hud.window_id.is_none());
    }

    #[test]
    fn summon_show_stashes_activation_token() {
        let mut hud = Hud::default();
        let _ = hud.on_summon(crate::summon::SummonRequest {
            action: crate::summon::SummonAction::Show,
            token: Some("tok-1".into()),
        });
        assert_eq!(hud.pending_activation_token.as_deref(), Some("tok-1"));
        let _ = hud.on_summon(crate::summon::SummonRequest::new(
            crate::summon::SummonAction::Hide,
        ));
        assert_eq!(hud.pending_activation_token, None);
    }

    #[test]
    fn summon_toggle_from_hidden_stashes_activation_token() {
        let mut hud = Hud {
            visible: false,
            window_mode: false,
            ..Hud::default()
        };
        let _ = hud.on_summon(crate::summon::SummonRequest {
            action: crate::summon::SummonAction::Toggle,
            token: Some("tok-2".into()),
        });
        assert_eq!(hud.pending_activation_token.as_deref(), Some("tok-2"));
    }

    #[test]
    fn summon_toggle_hide_clears_activation_token() {
        let mut hud = Hud {
            visible: true,
            window_mode: false,
            pending_activation_token: Some("stale".into()),
            ..Hud::default()
        };
        let _ = hud.on_summon(crate::summon::SummonRequest {
            action: crate::summon::SummonAction::Toggle,
            token: Some("fresh".into()),
        });
        assert_eq!(hud.pending_activation_token, None);
    }

    #[test]
    fn activation_applied_true_clears_token() {
        let mut hud = Hud {
            pending_activation_token: Some("tok".into()),
            ..Hud::default()
        };
        let _ = hud.update(Message::ActivationApplied(false));
        assert_eq!(hud.pending_activation_token.as_deref(), Some("tok"));
        let _ = hud.update(Message::ActivationApplied(true));
        assert_eq!(hud.pending_activation_token, None);
    }

    #[test]
    fn window_mode_boot_does_not_summon_overlay() {
        assert!(!boot_summons_overlay(true, true));
        assert!(boot_summons_overlay(false, true));
        assert!(!boot_summons_overlay(false, false));
        assert!(!boot_summons_overlay(true, false));
    }

    #[test]
    fn tray_show_on_visible_overlay_does_not_clear_window() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_mode: false,
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Show);
        assert!(hud.visible);
        assert!(!hud.window_mode);
        assert_eq!(hud.window_id, Some(id));
    }

    #[test]
    fn tray_toggle_hides_visible_overlay() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_mode: false,
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Toggle);
        assert!(!hud.visible);
        assert!(hud.window_id.is_none());
    }

    #[test]
    fn tray_quit_clears_the_window_id() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Quit);
        assert!(hud.window_id.is_none());
        assert!(!hud.visible);
        assert!(!hud.palette_live);
        assert!(!hud.window_mode);
    }

    #[test]
    fn tray_quit_stops_a_popped_out_window() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_mode: true,
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Quit);
        assert!(hud.window_id.is_none());
        assert!(!hud.visible);
        assert!(!hud.palette_live);
        assert!(!hud.window_mode);
    }

    #[test]
    fn close_requested_for_old_window_does_not_dismiss_pop_out() {
        let old = window::Id::unique();
        let new = window::Id::unique();
        let mut hud = Hud {
            window_mode: true,
            visible: true,
            palette_live: true,
            window_id: Some(new),
            ..Hud::default()
        };
        let _ = hud.update(Message::CloseRequested(old));
        assert_eq!(hud.window_id, Some(new));
        assert!(hud.visible);
        assert!(hud.window_mode);
    }

    #[test]
    fn close_requested_current_window_dismisses_pop_out() {
        let id = window::Id::unique();
        let mut hud = Hud {
            window_mode: true,
            visible: true,
            palette_live: true,
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.update(Message::CloseRequested(id));
        assert!(hud.window_id.is_none());
        assert!(!hud.visible);
        assert!(!hud.window_mode);
    }

    #[test]
    fn tray_show_reveals_hidden_palette() {
        let mut hud = Hud {
            visible: false,
            palette_live: false,
            window_mode: true,
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Show);
        assert!(hud.visible);
        assert!(hud.palette_live);
        assert!(!hud.window_mode);
    }

    #[test]
    fn window_id_none_does_not_replace_live_id() {
        let id = window::Id::unique();
        let mut hud = Hud {
            window_id: Some(id),
            ..Hud::default()
        };
        let _ = hud.update(Message::WindowId(None));
        assert_eq!(hud.window_id, Some(id));
    }
}
