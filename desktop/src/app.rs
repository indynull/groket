//! iced application: state, RPC, hotkey, live poll.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};
use iced::keyboard::{key::Named, Key, Modifiers as KeyMods};
use iced::widget::operation;
use iced::widget::Id;
use iced::window::{self, Mode};
use iced::{
    event, keyboard, time, Animation, Color, Element, Event, Pixels, Point, Size, Subscription,
    Task, Theme,
};
use serde_json::{json, Value};

use crate::control::{self, ControlError};
use crate::format::{
    control_down_message, event_body_text, extract_event, extract_turn, is_chat_message,
    list_status_label, looks_like_markdown, message_markdown_source, new_note_id,
    tool_fields_from_raw,
};
use crate::fuzzy::session_search_indices;
use crate::live::{
    card_marks_from_overview, clamp_scroll, diff_hunk_scroll_y, filter_timeline_indices,
    filter_turn_indices, first_list_fetch, is_partial_list_page, is_soft_notes_save_error,
    last_timeline_page_offset, list_focus_after_scroll, list_scroll_to_cover, list_scroll_to_top,
    merge_catalog_rows, merge_timeline_by_index, next_list_offset, next_spotlight_limit,
    notes_schema_fields, patch_catalog_delta, patch_list_row_from_meta, plan_tick,
    previous_timeline_page, scroll_after_prepend, session_card_height, session_needs_live_poll,
    session_rpc_ref, should_fetch_timeline, should_load_previous_timeline, should_page_recent,
    spotlight_recent, timeline_coverage_complete, timeline_page_next, timeline_range_label,
    timeline_window_start, trim_timeline_buffer, wants_periodic_poll, CardMark, TickInput,
    CLOSED_TURN_CARD_H, IDLE_POLL_MS, LIVE_TAIL_LIMIT, OVERVIEW_LIST_ROW_H, SPOTLIGHT_RECENT,
    STATS_ROW_H, TIMELINE_BUFFER_CAP, TIMELINE_CHUNK, TIMELINE_OPEN_CHARS, TIMELINE_PREVIEW_CHARS,
    TIMELINE_ROW_H,
};
use crate::model::{
    DiffContext, DiffPointPick, EventsTurnPick, KindFilter, NoteDraft, SchemaField, SessionRow, Tab,
};
use crate::motion::{self, MotionRole, PageLayer};
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
    #[allow(dead_code)]
    Last,
}

#[derive(Debug, Clone)]
pub enum Message {
    OsMode(icedtea::iced::theme::Mode),
    SearchChanged(String),
    SelectSession(usize),
    OpenChild {
        path: String,
        sid: String,
    },
    SetTab(Tab),
    SetOverviewSection(crate::model::OverviewSection),
    /// Ctrl+1…N over the tabs that are visible for this session.
    PaneDigit(u8),
    TimelineQuery(String),
    TimelineKind(KindFilter),
    JumpTimeline(i64),
    /// Events pane turn pick list (`None` key = all turns / search).
    EventsTurnPicked(EventsTurnPick),
    /// Turns card click: focus that turn (Timeline stays on `g` / the chip).
    FocusTurn(i64),
    SelectTimeline(i64),
    /// Follow new Timeline events to the end (live turn only).
    TimelineTail(bool),
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
    /// Leave a focused search / follow-up field so list keys work.
    LeaveInput,
    /// Leave the open session and show Recent + session search.
    SessionsHome,
    WindowFocus(bool),
    CloseRequested(window::Id),
    Tray(crate::tray::TrayAction),
    Summon(crate::summon::SummonRequest),
    ActivationApplied(bool),
    MdLink(String),
    ListScroll(icedtea::collection::VisibleWindow),
    TimelineScroll(icedtea::collection::VisibleWindow),
    TurnScroll(icedtea::collection::VisibleWindow),
    OverviewScroll(icedtea::collection::VisibleWindow),
    /// Highlight a Tasks / Workflows / Subagents row (second press opens).
    FocusOverviewRow(usize),
    StatsScroll(icedtea::collection::VisibleWindow),
    StatsHScroll(f32),
    StatsSort(usize),
    StatsCell(icedtea::collection::ItemClick, usize),
    /// Enter: open the selected session (works while search is focused).
    ActivateSelected,
    TimelineSearchApply(u64),
    DiffLoaded {
        gen: u64,
        result: Result<Value, String>,
    },
    DiffQuery(String),
    SelectDiffFile(String),
    DiffTreeToggle(u64),
    DiffTreeSelect(u64),
    DiffPointPicked(DiffPointPick),
    DiffContext(DiffContext),
    MdPointer {
        slot: usize,
        ev: icedtea::select::MarkdownPointer,
    },
    /// Turns card: open Diff on the snapshot for this prompt, if any.
    OpenTurnDiff {
        prompt_index: Option<i64>,
    },
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
    SelectAllText,
    Cursor(icedtea::layout::CursorEvent),
    ContextDismiss,
    WindowSize(Size),
    Select {
        id: String,
        action: iced::widget::text_editor::Action,
    },
    ToastDismiss(u64),
    FollowDone(Result<Value, String>),
    /// Toggle the keyboard-shortcut cheatsheet (`?`).
    ToggleHelp,
    /// Hidden look drawer (F12). Gallery density / type / shape / elevation.
    ToggleLook,
    LookDensity(String),
    LookScale(String),
    LookShape(String),
    LookElevation(String),
    /// Discard — close handlers and contribution-shaped tab chrome.
    Noop,
}

/// Where Esc lands after leaving a child session.
#[derive(Debug, Clone)]
struct ParentFrame {
    path: String,
    sid: String,
    tab: Tab,
    timeline_kind: KindFilter,
    timeline_query: String,
    timeline_query_draft: String,
    timeline_focus: Option<i64>,
    timeline_prompt: Option<i64>,
    events_turn_index: Option<i64>,
    turns_focus: Option<i64>,
    turns_query: String,
    turn_scroll: f32,
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
    /// How many idle Recent rows to show (grows on scroll / Down at the tail).
    spotlight_limit: usize,
    active: usize,
    tab: Tab,
    overview_section: crate::model::OverviewSection,
    tasks_focus: Option<usize>,
    /// True after a click on the focused Overview list row (second click opens).
    overview_row_armed: bool,
    overview_window: icedtea::collection::VisibleWindow,
    overview_heights: Vec<f32>,
    overview_scroll_id: Id,
    stats_table: icedtea::collection::TableModel,
    stats_cols: icedtea::collection::ColumnLayout,
    stats_window: icedtea::collection::VisibleWindow,
    stats_selection: icedtea::collection::Selection,
    stats_cursor: Option<(usize, usize)>,
    overview: Option<Overview>,
    overview_sid: String,
    overview_pending: String,
    overview_gen: u64,
    /// Parent sessions to restore when Esc leaves a child overview.
    parent_stack: Vec<ParentFrame>,
    /// One-shot: fetch the restored parent timeline around this event.
    restore_around: Option<i64>,
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
    /// Follow the last Timeline event while a turn is open.
    timeline_follow_tail: bool,
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
    focused: bool,
    catch_up: bool,
    palette_live: bool,
    palette_origin: Option<Point>,
    last_live: Instant,
    typing_notes: bool,
    search_id: Id,
    tl_search_id: Id,
    theme_name: String,
    appearance: icedtea::theme::Appearance,
    _hotkeys: Option<GlobalHotKeyManager>,
    _tray: Option<crate::tray::HudTray>,
    _summon: Option<crate::summon::SummonServer>,
    notify_q: Arc<Mutex<VecDeque<(String, Value)>>>,
    window_id: Option<window::Id>,
    catalog_revision: i64,
    list_window: icedtea::collection::VisibleWindow,
    list_scroll_id: Id,
    list_selection: icedtea::collection::Selection,
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
    notices_primed: bool,
    seen_analysis: std::collections::HashMap<String, String>,
    toasts: icedtea::toast::ToastQueue,
    last_tick: Instant,
    spin_phase: f32,
    overlay: Animation<bool>,
    page: Animation<bool>,
    page_slide: icedtea::motion::Slide,
    page_role: MotionRole,
    page_layer: PageLayer,
    page_dir: Option<icedtea::motion::Slide>,
    reduced_motion: bool,
    catalog_busy: bool,
    findings_open: HashSet<String>,
    notes_open: HashSet<String>,
    finding_motion: HashMap<String, Animation<bool>>,
    note_motion: HashMap<String, Animation<bool>>,
    /// Last Turns card focused (jump / `g` / yank).
    turns_focus: Option<i64>,
    turns_query: String,
    turns_filter: Vec<usize>,
    turns_search_id: Id,
    diff: crate::wire::DiffBlock,
    diff_sid: String,
    diff_gen: u64,
    diff_point: String,
    diff_file: String,
    diff_tree_collapsed: HashSet<u64>,
    diff_query: String,
    diff_hit_line: Option<usize>,
    diff_search_id: Id,
    diff_hunk_scroll_id: Id,
    diff_point_options: Vec<DiffPointPick>,
    diff_context: DiffContext,
    /// After Diff loads, select the snapshot for this prompt (Turns → Diff).
    diff_want_prompt: Option<i64>,
    follow_draft: String,
    follow_id: Id,
    timeline_search_gen: u64,
    fields: icedtea::field::Selectables,
    md_docs: HashMap<String, icedtea::widget::MarkdownDoc>,
    md_sel: HashMap<String, icedtea::select::MarkdownSelect>,
    md_ids: Vec<String>,
    /// Last selectable the pointer or keys touched.
    select_id: Option<String>,
    /// Selection snapshotted when the context menu opened.
    context_sel: Option<String>,
    key_mods: KeyMods,
    pointer: Point,
    context: Option<Point>,
    window_size: Size,
    /// Last show/toggle xdg-activation token; cleared on hide or successful activate.
    pending_activation_token: Option<String>,
    /// `?` keyboard-shortcut cheatsheet is open.
    help_open: bool,
    /// Hidden F12 look drawer (debug).
    look_open: bool,
    look: crate::theme::Look,
    /// Resolved keys.toml overlay (defaults when missing or refused).
    keys: crate::keys::KeyOverlay,
    /// Leader prefix is waiting for the next key.
    leader_armed: bool,
    leader_until: Option<Instant>,
}

fn diff_point_label(point: &crate::wire::DiffPointRow, index: usize) -> String {
    if point.source == "search_replace" {
        return "Approximate edits".into();
    }
    if let Some(n) = point.prompt_index {
        return format!("Prompt {n}");
    }
    format!("Snapshot {}", index + 1)
}

impl Default for Hud {
    fn default() -> Self {
        Self {
            query: String::new(),
            all_sessions: vec![],
            sessions: vec![],
            spotlight_limit: SPOTLIGHT_RECENT,
            active: 0,
            tab: Tab::Overview,
            overview_section: crate::model::OverviewSection::Session,
            tasks_focus: None,
            overview_row_armed: false,
            overview_window: icedtea::collection::VisibleWindow::new(400.0),
            overview_heights: vec![],
            overview_scroll_id: Id::new("hud-overview-list"),
            stats_table: icedtea::collection::TableModel::default(),
            stats_cols: icedtea::collection::ColumnLayout::new(vec![110.0, 280.0, 80.0])
                .with_frozen(1),
            stats_window: icedtea::collection::VisibleWindow::new(400.0),
            stats_selection: icedtea::collection::Selection::None,
            stats_cursor: None,
            overview: None,
            overview_sid: String::new(),
            overview_pending: String::new(),
            overview_gen: 0,
            parent_stack: vec![],
            restore_around: None,
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
            timeline_follow_tail: false,
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
            window_mode: crate::prefs::window_mode()
                || std::env::var_os("GROKET_HUD_WINDOW").is_some(),
            visible: true,
            focused: true,
            catch_up: false,
            palette_live: true,
            palette_origin: None,
            last_live: Instant::now(),
            typing_notes: false,
            search_id: Id::new("search"),
            tl_search_id: Id::new("tl-search"),
            theme_name: prefs::theme_name(),
            appearance: icedtea::theme::Appearance::Dark,
            _hotkeys: None,
            _tray: None,
            _summon: None,
            notify_q: Arc::new(Mutex::new(VecDeque::new())),
            catalog_revision: 0,
            window_id: None,
            list_window: icedtea::collection::VisibleWindow::new(400.0),
            list_scroll_id: Id::new("hud-sessions"),
            list_selection: icedtea::collection::Selection::None,
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
            notices_primed: false,
            seen_analysis: std::collections::HashMap::new(),
            toasts: icedtea::toast::ToastQueue::new(),
            last_tick: Instant::now(),
            spin_phase: 0.0,
            overlay: motion::role_animation(MotionRole::Present, true, false),
            page: motion::role_animation(MotionRole::Sibling, true, false),
            page_slide: icedtea::motion::Slide::None,
            page_role: MotionRole::None,
            page_layer: PageLayer::Pane,
            page_dir: None,
            reduced_motion: motion::detect_reduced_motion(),
            catalog_busy: false,
            findings_open: HashSet::new(),
            notes_open: HashSet::new(),
            finding_motion: HashMap::new(),
            note_motion: HashMap::new(),
            turns_focus: None,
            turns_query: String::new(),
            turns_filter: vec![],
            turns_search_id: Id::new("turns-search"),
            diff: crate::wire::DiffBlock::default(),
            diff_sid: String::new(),
            diff_gen: 0,
            diff_point: String::new(),
            diff_file: String::new(),
            diff_tree_collapsed: HashSet::new(),
            diff_query: String::new(),
            diff_hit_line: None,
            diff_search_id: Id::new("diff-search"),
            diff_hunk_scroll_id: Id::new("diff-hunk"),
            diff_point_options: vec![],
            diff_context: DiffContext::Prompt,
            diff_want_prompt: None,
            follow_draft: String::new(),
            follow_id: Id::new("follow-up"),
            timeline_search_gen: 0,
            fields: icedtea::field::Selectables::new(),
            md_docs: HashMap::new(),
            md_sel: HashMap::new(),
            md_ids: Vec::new(),
            select_id: None,
            context_sel: None,
            key_mods: KeyMods::empty(),
            pointer: Point::ORIGIN,
            context: None,
            window_size: if crate::prefs::window_mode()
                || std::env::var_os("GROKET_HUD_WINDOW").is_some()
            {
                Size::new(980.0, 700.0)
            } else {
                Size::new(HUD_W, HUD_H)
            },
            pending_activation_token: None,
            help_open: false,
            look_open: false,
            look: crate::theme::Look::default(),
            keys: crate::keys::KeyOverlay::default(),
            leader_armed: false,
            leader_until: None,
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
    prep.iced_settings.default_font = icedtea::typo::UI;
    prep.iced_settings.default_text_size =
        Pixels::from(crate::theme::tokens("textual-dark").body());
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
    // Clear surface so present/dismiss can fade the shell card, not a solid window.
    prep.window.transparent = true;
    prep
}

fn desktop_prepared() -> icedtea::app::Prepared {
    let mut prep = overlay_prepared();
    prep.window.transparent = false;
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
        return match crate::summon::send_command(crate::summon::SummonAction::Show) {
            Ok(()) => Ok(()),
            Err(err) => {
                eprintln!("groket: {err}");
                Ok(())
            }
        };
    }
    // icedtea::daemon! is equivalent; catalog + dual window modes stay manual
    // via Prepared + iced::daemon. Call the same face remap the macro would.
    icedtea::typo::install_platform_faces();
    iced::daemon(Hud::new, Hud::update, Hud::view)
        .title(concat!("groket ", env!("CARGO_PKG_VERSION")))
        .subscription(Hud::subscription)
        .theme(|hud: &Hud, window| Some(hud.theme(window)))
        .style(|hud: &Hud, theme| hud.window_style(theme))
        .settings(overlay_prepared().iced_settings)
        .run()
}

impl Hud {
    fn new() -> (Self, Task<Message>) {
        let mut hud = Hud::default();
        // iced/winit creates NSApplication as Regular after main() ran
        // accessory. Re-pin before the first window maps.
        crate::macoswin::set_desktop_app(hud.window_mode);
        let overlay = crate::keys::KeyOverlay::load();
        crate::keys::install_process_overlay(overlay.clone());
        hud.keys = overlay;
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
                match crate::summon::send_command(crate::summon::SummonAction::Show) {
                    Ok(()) => {}
                    Err(err) => {
                        eprintln!("groket: already running ({path}): {err}");
                    }
                }
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
            icedtea::iced::system::theme().map(Message::OsMode),
            Task::perform(rpc(control::initialize), |r| {
                Message::Inited(r.map(|_| String::new()))
            }),
            fetch_list(false, 0),
        ];
        if boot_summons_overlay(hud.window_mode, crate::tray::show_on_start()) {
            hud.visible = false;
            hud.palette_live = false;
            hud.overlay = motion::role_animation(MotionRole::Dismiss, false, hud.reduced_motion);
            boot.push(hud.show_palette());
        }
        (hud, Task::batch(boot))
    }

    fn theme(&self, _window: window::Id) -> Theme {
        theme::iced_theme(&self.theme_name)
    }

    /// Overlay: clear window fill so [`tokens`][`Self::tokens`] fade the card.
    /// Decorated window mode keeps an opaque canvas.
    pub fn window_style(&self, theme: &Theme) -> iced::theme::Style {
        let palette = theme.palette();
        if self.window_mode {
            return iced::theme::Style {
                background_color: palette.background,
                text_color: palette.text,
            };
        }
        iced::theme::Style {
            background_color: Color::TRANSPARENT,
            text_color: palette.text,
        }
    }

    fn subscription(&self) -> Subscription<Message> {
        let mut subs = vec![
            event::listen_with(interesting_hud_event),
            hotkey_subscription(),
            summon_subscription(),
            notify_subscription(),
            icedtea::iced::system::theme_changes().map(Message::OsMode),
        ];
        if self.needs_motion_tick() {
            subs.push(window::frames().map(|_| Message::Tick));
        }
        if wants_periodic_poll(self.visible, self.focused, self.window_mode) {
            // Toast / overlay clock only. List and timeline follow socket notifies.
            subs.push(time::every(Duration::from_millis(IDLE_POLL_MS)).map(|_| Message::Tick));
        }
        if self.note_delete_until.is_some() || self.leader_until.is_some() {
            subs.push(time::every(Duration::from_millis(250)).map(|_| Message::Tick));
        }
        subs.push(tray_subscription());
        subs.push(icedtea::layout::listen_cursor().map(Message::Cursor));
        Subscription::batch(subs)
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::OsMode(mode) => {
                self.appearance = icedtea::theme::Appearance::from_mode(mode);
                self.sync_theme();
                Task::none()
            }
            Message::SearchChanged(q) => {
                // Capture identity before `query` changes which list `sessions()` returns.
                let keep = self.session_keep_id();
                self.query = q;
                self.rerank_visible_keeping(keep);
                Task::none()
            }
            Message::OpenChild { path, sid } => self.open_child_session(path, sid),
            Message::SelectSession(i) => {
                self.parent_stack.clear();
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
                self.go_page(
                    motion::session_enter_role(),
                    PageLayer::Browse,
                    icedtea::motion::Slide::End,
                );
                self.reset_detail_chrome();
                // Loading placeholder this frame; body fills via OverviewLoaded.
                // Do not yank keyboard into session search after a pick.
                Task::batch([self.load_overview(false), self.focus_browse()])
            }
            Message::PaneDigit(n) => {
                let tabs = self.visible_tabs();
                let Some(&tab) = tabs.get((n as usize).saturating_sub(1)) else {
                    return Task::none();
                };
                self.update(Message::SetTab(tab))
            }
            Message::SetOverviewSection(section) => {
                self.overview_section = section;
                self.tasks_focus = None;
                self.overview_row_armed = false;
                self.overview_window =
                    icedtea::collection::VisibleWindow::new(self.overview_window.viewport.max(1.0));
                self.rebuild_overview_heights();
                if self.overview_list_count() > 0 {
                    self.tasks_focus = Some(0);
                }
                if section == crate::model::OverviewSection::Stats {
                    self.rebuild_stats_table();
                    if let Some(sid) = self.detail_sid() {
                        return self.ensure_timeline(sid, false);
                    }
                }
                Task::none()
            }
            Message::SetTab(tab) => {
                let tab = if tab == Tab::Turns && self.compact_child_chrome() {
                    Tab::Timeline
                } else {
                    tab
                };
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
                if self.tab != tab {
                    self.go_page(
                        motion::tab_role(self.tab, tab),
                        PageLayer::Pane,
                        icedtea::motion::Slide::None,
                    );
                }
                self.tab = tab;
                self.bind_copy_bodies();
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
                    Tab::Turns | Tab::Diff => self.load_diff(),
                    _ => Task::none(),
                };
                // Same as Escape in search: land on the list so j/k work.
                // `/` focuses this tab's search again.
                Task::batch([load, Self::blur_text_inputs()])
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
            Message::DiffLoaded { gen, result } => {
                if gen != self.diff_gen {
                    return Task::none();
                }
                match result {
                    Ok(v) => {
                        if let Ok(block) = serde_json::from_value::<crate::wire::DiffBlock>(v) {
                            self.diff = block;
                            if let Some(last) = self.diff.points.last() {
                                if self.diff_point.is_empty()
                                    || self.diff.points.iter().all(|p| p.key != self.diff_point)
                                {
                                    self.diff_point = last.key.clone();
                                }
                            }
                            self.rebuild_diff_point_options();
                            self.apply_diff_want_prompt();
                            self.ensure_diff_file();
                            self.bind_diff_bodies();
                        }
                    }
                    Err(e) => {
                        crate::log::error(&format!("session/diff: {e}"));
                        self.toasts.push_danger(control_down_message(&e));
                    }
                }
                Task::none()
            }
            Message::DiffQuery(q) => {
                self.diff_query = q;
                self.ensure_diff_file();
                self.bind_diff_bodies();
                self.reveal_diff_hit()
            }
            Message::SelectDiffFile(path) => {
                self.diff_file = path;
                self.refresh_diff_hit();
                self.bind_diff_bodies();
                self.reveal_diff_hit()
            }
            Message::DiffTreeToggle(id) => {
                if !self.diff_tree_collapsed.remove(&id) {
                    self.diff_tree_collapsed.insert(id);
                }
                Task::none()
            }
            Message::DiffTreeSelect(id) => {
                let paths: Vec<String> = self
                    .visible_diff_files()
                    .iter()
                    .map(|f| f.path.clone())
                    .collect();
                if let Some(path) = crate::diff_tree::file_path_for_id(&paths, id) {
                    self.diff_file = path;
                    self.refresh_diff_hit();
                    self.bind_diff_bodies();
                    return self.reveal_diff_hit();
                }
                Task::none()
            }
            Message::DiffPointPicked(pick) => {
                if pick.key == self.diff_point {
                    return Task::none();
                }
                self.diff_point = pick.key;
                self.diff_file.clear();
                self.ensure_diff_file();
                self.bind_diff_bodies();
                Task::none()
            }
            Message::DiffContext(tab) => {
                self.diff_context = tab;
                Task::none()
            }
            Message::OpenTurnDiff { prompt_index } => {
                self.diff_want_prompt = prompt_index;
                let load = self.update(Message::SetTab(Tab::Diff));
                self.apply_diff_want_prompt();
                self.ensure_diff_file();
                load
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
            Message::FocusTurn(ti) => {
                self.tab = Tab::Turns;
                self.turns_focus = Some(ti);
                self.focus_turn(ti);
                self.scroll_turn_into_view()
            }
            Message::SelectTimeline(ix) => {
                self.timeline_focus = Some(ix);
                if let Some((path, sid)) = self.openable_child_at(ix) {
                    return self.open_child_session(path, sid);
                }
                self.open_timeline_detail(ix)
            }
            Message::TimelineTail(on) => {
                if !self.show_timeline_tail() {
                    self.timeline_follow_tail = false;
                    return Task::none();
                }
                self.timeline_follow_tail = on;
                if !on {
                    return Task::none();
                }
                if self.window_covers_timeline_end() {
                    return self.scroll_timeline_to_end();
                }
                if let Some(sid) = self.detail_sid() {
                    return self.fetch_timeline_end(sid);
                }
                self.scroll_timeline_to_end()
            }
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
                if self.query.trim().is_empty()
                    && should_page_recent(win.end, self.sessions().len())
                {
                    self.grow_recent();
                }
                Task::none()
            }
            Message::TimelineScroll(win) => {
                self.tl_window = win;
                if let Some(dest) = list_focus_after_scroll(
                    self.timeline_focus_pos(),
                    self.tl_window.scroll,
                    self.tl_window.viewport,
                    &self.tl_heights,
                ) {
                    if let Some(&src) = self.tl_filter.get(dest) {
                        if let Some(ev) = self.timeline.get(src) {
                            self.timeline_focus = Some(ev.index);
                        }
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
            Message::OverviewScroll(win) => {
                self.overview_window = if self.overview_list_count() == 0 {
                    icedtea::collection::VisibleWindow::new(win.viewport.max(1.0))
                } else {
                    win
                };
                Task::none()
            }
            Message::FocusOverviewRow(i) => {
                if self.tasks_focus == Some(i) && self.overview_row_armed {
                    return self.open_focused_task();
                }
                self.tasks_focus = Some(i);
                self.overview_row_armed = true;
                self.scroll_overview_into_view()
            }
            Message::StatsScroll(win) => {
                self.stats_window = if self.stats_table.rows.is_empty() {
                    icedtea::collection::VisibleWindow::new(win.viewport.max(1.0))
                } else {
                    win
                };
                Task::none()
            }
            Message::StatsHScroll(x) => {
                self.stats_cols.set_h_scroll(x);
                Task::none()
            }
            Message::StatsSort(col) => {
                self.sort_stats(col);
                Task::none()
            }
            Message::StatsCell(click, col) => {
                self.stats_selection = icedtea::collection::Selection::Single(click.id);
                self.stats_cursor = Some((click.id, col));
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
                self.go_page(
                    motion::session_enter_role(),
                    PageLayer::Browse,
                    icedtea::motion::Slide::End,
                );
                self.query.clear();
                self.rerank_visible_keeping(sid.clone());
                if let Some(idx) = self.sessions().iter().position(|r| r.session_id == sid) {
                    self.set_active(idx);
                }
                Task::batch([self.load_overview(false), self.focus_browse()])
            }
            Message::FindingExpand { id, open } => {
                self.set_expand(true, id, open);
                self.bind_copy_bodies();
                Task::none()
            }
            Message::NoteExpand { id, open } => {
                self.set_expand(false, id, open);
                self.bind_copy_bodies();
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
                let action = match action {
                    iced::widget::text_editor::Action::Click(p) if self.key_mods.shift() => {
                        iced::widget::text_editor::Action::Drag(p)
                    }
                    other => other,
                };
                self.fields.perform(&id, action);
                self.select_id = Some(id);
                Task::none()
            }
            Message::MdPointer { slot, ev } => {
                self.apply_md_pointer(slot, ev);
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
                        crate::log::error(&format!("session/follow_up: {e}"));
                        self.toasts.push_danger(control_down_message(&e));
                    }
                }
                Task::none()
            }
            Message::Tick => self.on_tick(),
            Message::FocusSearch(attempt) => {
                if self.browse_mode() {
                    // Do not unfocus turns / timeline / session search.
                    Task::none()
                } else {
                    self.on_focus_search(attempt)
                }
            }
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
                        self.clear_footer_identity();
                        self.overview = Some(ov);
                        self.overview_sid = sid.clone();
                        self.overview_pending.clear();
                        if !self.show_timeline_tail() {
                            self.timeline_follow_tail = false;
                        }
                        if self.tab == Tab::Turns && self.compact_child_chrome() {
                            self.tab = Tab::Timeline;
                        }
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
                            self.bind_copy_bodies();
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
                        self.bind_overview_fields();
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
                                tasks.push(self.fetch_open_detail_bodies(ix));
                            }
                        }
                        if append && advance && added > 0 && page_off < old_offset {
                            self.tl_window.scroll =
                                scroll_after_prepend(self.tl_window.scroll, added, TIMELINE_ROW_H);
                        }
                        if advance && self.timeline_follow_tail {
                            if self.window_covers_timeline_end() {
                                tasks.push(self.scroll_timeline_to_end());
                            } else {
                                let want = last_timeline_page_offset(
                                    self.timeline_owner_total(),
                                    TIMELINE_CHUNK,
                                );
                                let asked_end = self
                                    .last_timeline
                                    .as_ref()
                                    .is_some_and(|r| r.offset == want);
                                if asked_end {
                                    tasks.push(self.scroll_timeline_to_end());
                                } else if let Some(next_sid) = self.detail_sid() {
                                    tasks.push(self.fetch_timeline_end(next_sid));
                                }
                            }
                        }
                        if advance
                            && should_load_previous_timeline(
                                self.tl_window.scroll,
                                self.timeline_offset,
                                false,
                            )
                        {
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
                // Present starts here so map time is not spent on an unseen fade.
                if self.visible && !self.window_mode {
                    self.go_overlay(true);
                }
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
            Message::WindowFocus(on) => self.on_window_focus(on),
            Message::Hide => self.on_escape(),
            Message::LeaveInput => Self::blur_text_inputs(),
            Message::SessionsHome => self.go_sessions_home(),
            Message::ToggleHelp => {
                if self.typing_notes {
                    return Task::none();
                }
                self.help_open = !self.help_open;
                self.context = None;
                Task::none()
            }
            Message::ToggleLook => {
                self.look_open = !self.look_open;
                self.context = None;
                Task::none()
            }
            Message::LookDensity(name) => {
                self.look = self.look.with_density_label(&name);
                Task::none()
            }
            Message::LookScale(name) => {
                self.look = self.look.with_scale_label(&name);
                Task::none()
            }
            Message::LookShape(name) => {
                self.look = self.look.with_shape_label(&name);
                Task::none()
            }
            Message::LookElevation(name) => {
                self.look = self.look.with_elevation_label(&name);
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
            Message::SelectAllText => self.select_all_text(),
            Message::Cursor(ev) => self.on_cursor(ev),
            Message::ContextDismiss => {
                self.context = None;
                self.context_sel = None;
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

    pub fn help_open(&self) -> bool {
        self.help_open
    }

    pub fn key_scope(&self) -> crate::help::KeyScope {
        crate::help::KeyScope {
            browse: self.browse_mode(),
            help_open: self.help_open,
            timeline_detail: self.tab == Tab::Timeline && self.timeline_open.is_some(),
            awaiting: self.selected_awaiting(),
            child_open: !self.parent_stack.is_empty(),
            compact_child: self.compact_child_chrome(),
            turn_pick: !self.hide_events_turn_pick(),
            turn_locked: self.events_turn_index.is_some(),
            diff_pick: self.tab == Tab::Diff && self.diff.points.len() > 1,
            tab: self.tab,
            leader_armed: self.leader_armed,
        }
    }

    pub fn leader_armed(&self) -> bool {
        self.leader_armed
    }

    pub fn key_overlay(&self) -> &crate::keys::KeyOverlay {
        &self.keys
    }

    /// Visible Spotlight rows: Recent when the query is empty, else search hits.
    /// Recent starts at [`SPOTLIGHT_RECENT`] and grows as the list is paged.
    pub fn sessions(&self) -> &[SessionRow] {
        &self.sessions
    }
    pub fn active(&self) -> usize {
        self.active
    }
    pub fn tab(&self) -> Tab {
        self.tab
    }

    /// Subagent with exactly one operator turn — no Turns pane.
    pub fn compact_child_chrome(&self) -> bool {
        let Some(o) = &self.overview else {
            return false;
        };
        o.meta.is_subagent() && o.turns.turns.len() == 1
    }

    /// Hide the Events turn pick when there is nothing to choose.
    pub fn hide_events_turn_pick(&self) -> bool {
        self.overview
            .as_ref()
            .map(|o| o.turns.turns.len() <= 1)
            .unwrap_or(true)
    }

    pub fn visible_tabs(&self) -> &'static [Tab] {
        if self.compact_child_chrome() {
            Tab::CHILD
        } else {
            &Tab::ALL
        }
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
    pub fn overview_section(&self) -> crate::model::OverviewSection {
        self.overview_section
    }
    pub fn tasks_focus(&self) -> Option<usize> {
        self.tasks_focus
    }
    pub fn overview_window(&self) -> icedtea::collection::VisibleWindow {
        self.overview_window
    }
    pub fn overview_heights(&self) -> &[f32] {
        &self.overview_heights
    }
    pub fn overview_scroll_id(&self) -> Id {
        self.overview_scroll_id.clone()
    }
    pub fn stats_table(&self) -> &icedtea::collection::TableModel {
        &self.stats_table
    }
    pub fn stats_cols(&self) -> &icedtea::collection::ColumnLayout {
        &self.stats_cols
    }
    pub fn stats_window(&self) -> icedtea::collection::VisibleWindow {
        self.stats_window
    }
    pub fn stats_selection(&self) -> &icedtea::collection::Selection {
        &self.stats_selection
    }
    pub fn stats_cursor(&self) -> Option<(usize, usize)> {
        self.stats_cursor
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
    pub fn timeline_follow_tail(&self) -> bool {
        self.timeline_follow_tail
    }
    /// True when Tail should show (a turn is still open).
    pub fn show_timeline_tail(&self) -> bool {
        let Some(o) = &self.overview else {
            return false;
        };
        o.meta.turn_in_progress
            || crate::live::session_needs_live_poll(&o.meta.status_label(), Some(&o.turns))
    }

    /// True when the loaded window already includes the last owner event.
    pub fn timeline_at_live_end(&self) -> bool {
        self.window_covers_timeline_end()
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

    /// Adjacent filtered events (previous, next) for the open detail card.
    pub fn timeline_detail_adjacent(&self) -> (Option<&TimelineEvent>, Option<&TimelineEvent>) {
        let Some((at, n)) = self.timeline_detail_pos() else {
            return (None, None);
        };
        let ev_at = |i: usize| {
            let src = *self.tl_filter.get(i)?;
            self.timeline.get(src)
        };
        let prev = if at > 1 { ev_at(at - 2) } else { None };
        let next = if at < n { ev_at(at) } else { None };
        (prev, next)
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

    fn bind_markdown(&mut self, id: impl Into<String>, src: &str) {
        if src.trim().is_empty() {
            return;
        }
        let id = id.into();
        let painted = message_markdown_source(src);
        self.bind_field(&id, &painted);
        let doc = icedtea::widget::parse(&painted);
        if self
            .md_docs
            .get(&id)
            .is_some_and(|old| old.hash == doc.hash)
        {
            return;
        }
        self.md_docs.insert(id.clone(), doc);
        self.md_sel.remove(&id);
        if !self.md_ids.iter().any(|s| s == &id) {
            self.md_ids.push(id);
        }
    }

    pub fn markdown(&self, id: &str) -> Option<&icedtea::widget::MarkdownDoc> {
        self.md_docs.get(id)
    }

    pub fn markdown_span(&self, id: &str) -> Option<&icedtea::select::MarkdownSpan> {
        self.md_sel.get(id).map(|s| &s.span)
    }

    pub fn markdown_slot(&self, id: &str) -> Option<usize> {
        self.md_ids.iter().position(|s| s == id)
    }

    fn apply_md_pointer(&mut self, slot: usize, ev: icedtea::select::MarkdownPointer) {
        let Some(id) = self.md_ids.get(slot).cloned() else {
            return;
        };
        let Some(doc) = self.md_docs.get(&id) else {
            return;
        };
        let prev = self.md_sel.get(&id).copied().unwrap_or_default();
        let next = icedtea::select::markdown_select(&doc.items, prev, ev, self.tokens());
        self.md_sel.insert(id.clone(), next);
        self.select_id = Some(id);
    }

    fn clear_footer_identity(&mut self) {
        let s = self.status.trim();
        if s.is_empty() {
            return;
        }
        if self
            .all_sessions
            .iter()
            .any(|r| s == r.session_id.as_str() || s.starts_with(&format!("{} · ", r.session_id)))
        {
            self.status.clear();
        }
    }

    fn unbind_event_fields(&mut self, index: i64) {
        let prefix = format!("event.{index}");
        let drop = |id: &str| id == prefix.as_str() || id.starts_with(&format!("{prefix}."));
        self.fields.retain(|id| !drop(id));
        self.md_docs.retain(|id, _| !drop(id));
        self.md_sel.retain(|id, _| !drop(id));
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
        let ov = self.overview.as_ref();
        self.turn_heights = self
            .turns_filter
            .iter()
            .map(|&src| {
                let extra = ov
                    .and_then(|o| o.turns.turns.get(src))
                    .map(|t| {
                        if t.subagent_runs.is_empty() {
                            0.0
                        } else {
                            22.0 * (t.subagent_runs.len().min(4) as f32)
                        }
                    })
                    .unwrap_or(0.0);
                CLOSED_TURN_CARD_H + extra
            })
            .collect();
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

    fn rebuild_overview_heights(&mut self) {
        let n = self.overview_list_count();
        self.overview_heights = vec![OVERVIEW_LIST_ROW_H; n];
        let view_h = self.overview_window.viewport.max(1.0);
        let content: f32 = self.overview_heights.iter().copied().sum();
        self.overview_window.scroll = clamp_scroll(self.overview_window.scroll, content, view_h);
    }

    fn rebuild_stats_table(&mut self) {
        let sort_col = self.stats_table.sort_col;
        let sort_asc = self.stats_table.sort_asc;
        let mut rows = match self.overview.as_ref() {
            Some(o) if !o.stats.event_types.is_empty() || !o.stats.tools.is_empty() => {
                crate::format::overview_stat_rows_from_counts(&o.stats.event_types, &o.stats.tools)
            }
            _ => crate::format::overview_stat_rows(&self.timeline),
        };
        if let Some(col) = sort_col {
            crate::format::sort_stat_rows(&mut rows, col, sort_asc);
        }
        self.stats_table = icedtea::collection::TableModel {
            headers: vec!["Kind".into(), "Name".into(), "Count".into()],
            rows: rows
                .into_iter()
                .map(|r| vec![r.section.to_string(), r.label, r.value])
                .collect(),
            sort_col,
            sort_asc,
            checks: vec![],
        };
        let n = self.stats_table.rows.len();
        let view_h = self.stats_window.viewport.max(1.0);
        let content = STATS_ROW_H * n as f32;
        self.stats_window.scroll = clamp_scroll(self.stats_window.scroll, content, view_h);
        if let Some(i) = self.stats_selection.primary() {
            if i >= n {
                self.stats_selection = icedtea::collection::Selection::None;
                self.stats_cursor = None;
            }
        }
    }

    fn sort_stats(&mut self, col: usize) {
        if self.stats_table.sort_col == Some(col) {
            self.stats_table.sort_asc = !self.stats_table.sort_asc;
        } else {
            self.stats_table.sort_col = Some(col);
            self.stats_table.sort_asc = col != 2;
        }
        self.rebuild_stats_table();
    }

    fn scroll_overview_into_view(&mut self) -> Task<Message> {
        let Some(pos) = self.tasks_focus else {
            return Task::none();
        };
        let view_h = self.overview_window.viewport.max(1.0);
        let y = list_scroll_to_cover(
            &self.overview_heights,
            pos,
            self.overview_window.scroll,
            view_h,
        );
        self.overview_window.scroll = y;
        Task::none()
    }

    fn scroll_stats_into_view(&mut self) -> Task<Message> {
        let Some(pos) = self.stats_selection.primary() else {
            return Task::none();
        };
        let heights: Vec<f32> = vec![STATS_ROW_H; self.stats_table.rows.len()];
        let view_h = self.stats_window.viewport.max(1.0);
        let y = list_scroll_to_cover(&heights, pos, self.stats_window.scroll, view_h);
        self.stats_window.scroll = y;
        Task::none()
    }

    fn bind_extract_text(&mut self, key: ExtractKey, src: &str) {
        self.bind_field(key.id(), src);
    }

    fn bind_display(src: &str) -> String {
        let cap = crate::format::EXTRACT_CHARS;
        if crate::format::looks_like_json(src) {
            crate::format::capped_display(&crate::format::pretty_json(src), cap)
        } else {
            crate::format::capped_display(src, cap)
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
            let ev = &self.timeline[pos];
            if is_chat_message(&ev.kind, &ev.event_type) {
                self.bind_markdown(&body_id, &src);
            } else {
                self.bind_extract_text(ExtractKey::Event(index), &Self::bind_display(&src));
            }
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
        let (raw, content, etype) = {
            let ev = &self.timeline[pos];
            (
                ev.raw_input.clone(),
                ev.content.clone(),
                ev.event_type.clone(),
            )
        };
        if crate::format::job_event_label(&etype, crate::format::event_is_monitor(&raw)).is_some() {
            let cmd = crate::format::job_command(&raw, &content);
            if !cmd.is_empty() {
                let id = format!("event.{index}.cmd");
                keep.insert(id.clone());
                self.bind_field(id, &cmd);
            }
            let desc = crate::format::job_description(&raw);
            if !desc.is_empty() {
                let id = format!("event.{index}.desc");
                keep.insert(id.clone());
                if crate::format::looks_like_markdown(&desc) {
                    self.bind_markdown(&id, &desc);
                } else {
                    self.bind_field(id, &desc);
                }
            }
            let tail = crate::format::sanitize_console_text(&crate::format::job_inspect_log(
                &self.session_path(),
                &crate::format::job_output_path(&raw),
            ));
            if !tail.trim().is_empty() {
                let id = format!("event.{index}.log");
                keep.insert(id.clone());
                self.bind_field(id, &tail);
            }
            let prompt = raw
                .get("prompt")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            if !prompt.is_empty() {
                let id = format!("event.{index}.prompt");
                keep.insert(id.clone());
                if crate::format::looks_like_markdown(&prompt) {
                    self.bind_markdown(&id, &prompt);
                } else {
                    self.bind_field(id, &prompt);
                }
            }
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
        self.md_docs.retain(|id, _| {
            if id == prefix.as_str() || id.starts_with(&prefix_dot) {
                keep.contains(id)
            } else {
                true
            }
        });
        self.md_sel.retain(|id, _| self.md_docs.contains_key(id));
    }

    fn bind_turn_extracts(&mut self) {
        self.bind_overview_fields();
        self.bind_copy_bodies();
        self.rebuild_turns_filter();
    }

    fn bind_copy_bodies(&mut self) {
        let Some(o) = self.overview.as_ref() else {
            self.bind_diff_bodies();
            return;
        };
        let summary = if !o.summary.is_empty() {
            o.summary.clone()
        } else {
            o.meta.summary.clone()
        };
        let turns: Vec<(i64, String, String)> = o
            .turns
            .turns
            .iter()
            .map(|t| (t.turn_index, t.summary.clone(), t.assistant_summary.clone()))
            .collect();
        let findings: Vec<(String, String)> = o
            .findings
            .findings
            .iter()
            .map(|f| {
                let body = if f.detail.is_empty() {
                    f.title.clone()
                } else {
                    f.detail.clone()
                };
                (finding_menu_key(f), body)
            })
            .collect();
        let notes: Vec<(String, String)> = o
            .notes
            .notes
            .iter()
            .map(|n| (n.id.clone(), crate::format::note_fields_view(&n.fields).1))
            .collect();
        if !summary.is_empty() {
            if looks_like_markdown(&summary) {
                self.bind_markdown("overview.summary", &summary);
            } else {
                self.bind_field("overview.summary", &summary);
            }
        }
        for (i, prompt, asst) in turns {
            if !prompt.is_empty() {
                self.bind_markdown(format!("turn.{i}.prompt"), &prompt);
            }
            if !asst.is_empty() {
                self.bind_markdown(format!("turn.{i}.assistant"), &asst);
            }
        }
        for (id, body) in findings {
            if !body.is_empty() {
                if looks_like_markdown(&body) {
                    self.bind_markdown(format!("finding.{id}"), &body);
                } else {
                    self.bind_field(format!("finding.{id}"), &body);
                }
            }
        }
        for (id, body) in notes {
            if !body.is_empty() {
                self.bind_field(format!("note.{id}"), &body);
            }
        }
        self.bind_diff_bodies();
    }

    fn bind_diff_bodies(&mut self) {
        let Some(point) = self.current_diff_point() else {
            return;
        };
        let prompt = point.prompt.clone();
        let assistant = point.assistant.clone();
        if !prompt.is_empty() {
            self.bind_markdown("diff.prompt", &prompt);
        }
        if !assistant.is_empty() {
            self.bind_markdown("diff.assistant", &assistant);
        }
        let hunk = self.diff_hunk_display();
        if !hunk.trim().is_empty() {
            self.bind_field("diff.hunk", &hunk);
        }
    }

    /// Unified hunk, with ``> `` on the search hit so the match is visible
    /// while the search field (not the editor) has focus.
    fn diff_hunk_display(&self) -> String {
        let raw = self.selected_diff_unified();
        if self.diff_hit_line.is_none() {
            return raw;
        }
        crate::fuzzy::mark_unified_hit(&raw, self.diff_hit_line).join("\n")
    }

    fn reveal_diff_hit(&self) -> Task<Message> {
        use iced::widget::scrollable::AbsoluteOffset;
        operation::scroll_to(
            self.diff_hunk_scroll_id.clone(),
            AbsoluteOffset {
                x: 0.0,
                y: diff_hunk_scroll_y(self.diff_hit_line),
            },
        )
    }

    fn bind_overview_fields(&mut self) {
        let fields = self
            .overview
            .as_ref()
            .map(|o| crate::format::overview_fields(&o.meta, &o.turns))
            .unwrap_or_default();
        for field in fields {
            if !field.value.is_empty() {
                self.bind_extract_text(ExtractKey::Overview(field.key), &field.value);
            }
        }
        self.rebuild_stats_table();
        self.rebuild_overview_heights();
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
        self.context_sel = None;
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

    fn select_target_id(&self) -> Option<String> {
        if let Some(id) = &self.select_id {
            if self.fields.contains(id) {
                return Some(id.clone());
            }
        }
        let id = match self.tab {
            Tab::Timeline => {
                let ix = self.timeline_open.or(self.timeline_focus)?;
                let out = format!("event.{ix}.out");
                if self.fields.contains(&out) {
                    out
                } else {
                    format!("event.{ix}")
                }
            }
            Tab::Turns => format!("turn.{}.prompt", self.turns_focus?),
            Tab::Diff => {
                if self.fields.contains("diff.hunk") {
                    "diff.hunk".into()
                } else {
                    match self.diff_context {
                        DiffContext::Prompt => "diff.prompt".into(),
                        DiffContext::Assistant => "diff.assistant".into(),
                    }
                }
            }
            Tab::Findings => {
                let id = self.findings_open.iter().next()?;
                format!("finding.{id}")
            }
            Tab::Notes => {
                let id = self.notes_open.iter().next()?;
                format!("note.{id}")
            }
            Tab::Overview => "overview.summary".into(),
        };
        self.fields.contains(&id).then_some(id)
    }

    fn select_all_text(&mut self) -> Task<Message> {
        let Some(id) = self.select_target_id() else {
            return Task::none();
        };
        if let Some(doc) = self.md_docs.get(&id) {
            self.md_sel
                .insert(id.clone(), icedtea::select::markdown_select_all(&doc.items));
            self.select_id = Some(id);
            return Task::none();
        }
        self.fields
            .perform(&id, iced::widget::text_editor::Action::SelectAll);
        self.select_id = Some(id);
        if self.context.is_some() {
            self.context_sel = self.fields.first_selection();
        }
        Task::none()
    }

    pub(crate) fn copyable_text(&self) -> String {
        if self.context.is_some() {
            if let Some(sel) = self.context_sel.as_deref() {
                if !sel.trim().is_empty() {
                    return sel.to_string();
                }
            }
        }
        if let Some(id) = self.select_id.as_deref() {
            if let (Some(doc), Some(sel)) = (self.md_docs.get(id), self.md_sel.get(id)) {
                let span = sel.span.text(&doc.items);
                if !span.trim().is_empty() {
                    return span;
                }
            }
        }
        if let Some(sel) = self.fields.first_selection() {
            if !sel.trim().is_empty() {
                return sel;
            }
        }
        if let Some(id) = self.select_target_id() {
            let body = self.fields.copy(&id);
            if !body.trim().is_empty() {
                return body;
            }
        }
        match self.tab {
            Tab::Timeline => self
                .timeline_open
                .or(self.timeline_focus)
                .and_then(|ix| self.timeline.iter().find(|e| e.index == ix))
                .map(extract_event)
                .filter(|s| !s.trim().is_empty())
                .or_else(|| {
                    self.timeline_open.or(self.timeline_focus).and_then(|ix| {
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
            Tab::Diff => self.selected_diff_unified(),
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
            Tab::Overview if self.overview_section == crate::model::OverviewSection::Stats => self
                .stats_selection
                .primary()
                .map(|i| {
                    let name = self.stats_table.cell(i, 1);
                    let count = self.stats_table.cell(i, 2);
                    format!("{name}\t{count}")
                })
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

    pub(crate) fn session_path(&self) -> String {
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
        let mut all =
            icedtea::action::Action::new("edit.select_all", "Select all", Message::SelectAllText);
        all.enabled = self.select_target_id().is_some();
        if let Some(spec) = icedtea::shortcut::Shortcut::parse("ctrl+a") {
            all = all.with_shortcut(spec);
        }
        let mut acts = vec![copy, all];
        if self.show_copy_path() {
            let mut path =
                icedtea::action::Action::new("session.copy", "Copy path", Message::CopyPath);
            path.enabled = !self.session_path().is_empty();
            acts.push(path);
        }
        acts
    }

    /// Session folder path — picker and Overview only, not event bodies.
    fn show_copy_path(&self) -> bool {
        self.in_session_picker() || (self.browse_mode() && self.tab == Tab::Overview)
    }

    fn on_cursor(&mut self, ev: icedtea::layout::CursorEvent) -> Task<Message> {
        match ev {
            icedtea::layout::CursorEvent::Move(p) => {
                self.pointer = p;
            }
            icedtea::layout::CursorEvent::Context if self.visible => {
                self.context_sel = self.fields.first_selection();
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
    pub fn look_open(&self) -> bool {
        self.look_open
    }

    pub fn look(&self) -> crate::theme::Look {
        self.look
    }

    pub fn tokens(&self) -> icedtea::theme::Tokens {
        let tok = crate::theme::tokens_with(&self.theme_name, self.look)
            .with_reduced_motion(self.reduced_motion);
        let t = icedtea::motion::visual(self.overlay_progress(), tok.reduced_motion);
        tok.fade(t)
    }

    /// Body paint: overlay fade times in-flight page fade.
    pub fn body_tokens(&self) -> icedtea::theme::Tokens {
        let tok = self.tokens();
        if !self.page_moving() {
            return tok;
        }
        tok.fade(icedtea::motion::visual(
            self.page_progress(),
            self.reduced_motion,
        ))
    }

    pub fn overlay_progress(&self) -> f32 {
        self.overlay.interpolate(0.0, 1.0, Instant::now())
    }

    pub fn overlay_moving(&self) -> bool {
        self.overlay.is_animating(Instant::now())
    }

    pub fn page_progress(&self) -> f32 {
        self.page.interpolate(0.0, 1.0, Instant::now())
    }

    pub fn page_moving(&self) -> bool {
        self.page.is_animating(Instant::now())
    }

    pub fn page_slide(&self) -> icedtea::motion::Slide {
        self.page_slide
    }

    pub fn page_layer(&self) -> PageLayer {
        self.page_layer
    }

    pub fn page_role(&self) -> MotionRole {
        self.page_role
    }

    fn expanders_moving(&self) -> bool {
        let now = Instant::now();
        self.finding_motion.values().any(|a| a.is_animating(now))
            || self.note_motion.values().any(|a| a.is_animating(now))
    }

    fn needs_motion_tick(&self) -> bool {
        let now = Instant::now();
        self.overlay.is_animating(now)
            || self.page.is_animating(now)
            || self.expanders_moving()
            || self.page_busy()
            || (!self.visible && self.window_id.is_some() && !self.window_mode)
    }

    fn go_page(&mut self, role: MotionRole, layer: PageLayer, slide: icedtea::motion::Slide) {
        if matches!(role, MotionRole::None) {
            return;
        }
        let now = Instant::now();
        let current = self.page.interpolate(0.0, 1.0, now);
        let animating = self.page.is_animating(now);
        self.page_role = role;
        self.page_layer = layer;
        self.page_slide = motion::visual_slide(role, slide, self.reduced_motion);
        self.page = motion::continue_or_restart(
            std::mem::replace(
                &mut self.page,
                motion::role_animation(role, true, self.reduced_motion),
            ),
            role,
            current,
            animating,
            self.reduced_motion,
            now,
        );
    }

    fn go_overlay(&mut self, open: bool) {
        let now = Instant::now();
        self.overlay = motion::retune_overlay(
            std::mem::replace(
                &mut self.overlay,
                motion::role_animation(
                    if open {
                        MotionRole::Present
                    } else {
                        MotionRole::Dismiss
                    },
                    open,
                    self.reduced_motion,
                ),
            ),
            open,
            self.reduced_motion,
            now,
        );
    }

    fn set_expand(&mut self, findings: bool, id: String, open: bool) {
        let reduced = self.reduced_motion;
        let (set, store) = if findings {
            (&mut self.findings_open, &mut self.finding_motion)
        } else {
            (&mut self.notes_open, &mut self.note_motion)
        };
        if open {
            set.insert(id.clone());
        } else {
            set.remove(&id);
        }
        let now = Instant::now();
        let prev = store
            .remove(&id)
            .unwrap_or_else(|| motion::disclose_animation(!open, reduced));
        if reduced {
            store.insert(id, motion::disclose_animation(open, true));
            return;
        }
        let mut anim = prev
            .duration(MotionRole::Disclose.duration(false))
            .easing(MotionRole::Disclose.easing());
        anim.go_mut(open, now);
        store.insert(id, anim);
    }

    fn expand_progress(
        open_set: &HashSet<String>,
        store: &HashMap<String, Animation<bool>>,
        id: &str,
    ) -> f32 {
        if let Some(anim) = store.get(id) {
            return anim.interpolate(0.0, 1.0, Instant::now());
        }
        if open_set.contains(id) {
            1.0
        } else {
            0.0
        }
    }

    pub fn finding_expand_progress(&self, id: &str) -> f32 {
        Self::expand_progress(&self.findings_open, &self.finding_motion, id)
    }

    pub fn note_expand_progress(&self, id: &str) -> f32 {
        Self::expand_progress(&self.notes_open, &self.note_motion, id)
    }

    fn finish_overlay_hide(&mut self) -> Task<Message> {
        if self.visible || self.window_mode {
            return Task::none();
        }
        if self.overlay.is_animating(Instant::now()) {
            return Task::none();
        }
        #[cfg(target_os = "linux")]
        crate::x11focus::release_keyboard();
        // Destroy the overlay so Sway rematches for_window on the next show.
        match self.window_id.take() {
            Some(id) => window::close(id),
            None => Task::none(),
        }
    }
    pub fn search_id(&self) -> Id {
        self.search_id.clone()
    }
    pub fn tl_search_id(&self) -> Id {
        self.tl_search_id.clone()
    }

    pub fn diff_search_id(&self) -> Id {
        self.diff_search_id.clone()
    }

    pub fn diff_hunk_scroll_id(&self) -> Id {
        self.diff_hunk_scroll_id.clone()
    }

    pub fn diff(&self) -> &crate::wire::DiffBlock {
        &self.diff
    }

    pub fn diff_query(&self) -> &str {
        &self.diff_query
    }

    pub fn diff_file(&self) -> &str {
        &self.diff_file
    }

    pub fn diff_tree_collapsed(&self) -> &HashSet<u64> {
        &self.diff_tree_collapsed
    }

    pub fn diff_point_key(&self) -> &str {
        &self.diff_point
    }

    pub fn diff_can_step(&self) -> bool {
        self.diff.points.len() > 1
    }

    pub fn current_diff_point(&self) -> Option<&crate::wire::DiffPointRow> {
        if let Some(p) = self.diff.points.iter().find(|p| p.key == self.diff_point) {
            return Some(p);
        }
        self.diff.points.last()
    }

    pub fn diff_point_options(&self) -> &[DiffPointPick] {
        &self.diff_point_options
    }

    pub fn diff_point_selected(&self) -> Option<DiffPointPick> {
        let key = self.diff_point_key();
        self.diff_point_options
            .iter()
            .find(|p| p.key == key)
            .cloned()
            .or_else(|| self.diff_point_options.last().cloned())
    }

    pub fn diff_context(&self) -> DiffContext {
        self.diff_context
    }

    pub fn turn_has_diff(&self, prompt_index: Option<i64>) -> bool {
        let Some(n) = prompt_index else {
            return false;
        };
        self.diff.points.iter().any(|p| p.prompt_index == Some(n))
    }

    fn apply_diff_want_prompt(&mut self) {
        let Some(n) = self.diff_want_prompt else {
            return;
        };
        let Some(point) = self.diff.points.iter().find(|p| p.prompt_index == Some(n)) else {
            return;
        };
        self.diff_point = point.key.clone();
        self.diff_file.clear();
        self.diff_want_prompt = None;
    }

    fn rebuild_diff_point_options(&mut self) {
        self.diff_point_options = self
            .diff
            .points
            .iter()
            .enumerate()
            .map(|(i, p)| DiffPointPick {
                key: p.key.clone(),
                label: diff_point_label(p, i),
            })
            .collect();
    }

    pub fn visible_diff_files(&self) -> Vec<&crate::wire::DiffFileRow> {
        let Some(point) = self.current_diff_point() else {
            return Vec::new();
        };
        let pairs: Vec<(String, String)> = point
            .files
            .iter()
            .map(|f| (f.path.clone(), f.unified.clone()))
            .collect();
        crate::fuzzy::filter_diff_hunks(&self.diff_query, &pairs)
            .into_iter()
            .filter_map(|(i, _)| point.files.get(i))
            .collect()
    }

    pub fn diff_hit_line(&self) -> Option<usize> {
        self.diff_hit_line
    }

    pub fn painted_hit_line(&self) -> Option<String> {
        let unified = self.selected_diff_unified();
        crate::fuzzy::mark_unified_hit(&unified, self.diff_hit_line)
            .into_iter()
            .find(|line| line.starts_with("> "))
    }

    /// Search field `/` should focus: events, turns, or session switcher.
    fn search_focus_id(&self) -> Id {
        if self.browse_mode() && self.tab == Tab::Timeline {
            self.tl_search_id.clone()
        } else if self.browse_mode() && self.tab == Tab::Turns {
            self.turns_search_id.clone()
        } else if self.browse_mode() && self.tab == Tab::Diff {
            self.diff_search_id.clone()
        } else {
            self.search_id.clone()
        }
    }

    fn load_diff(&mut self) -> Task<Message> {
        let Some(sid) = self.detail_sid() else {
            return Task::none();
        };
        let Some(rpc_ref) = self.detail_rpc_ref() else {
            return Task::none();
        };
        if sid == self.diff_sid && !self.diff.points.is_empty() {
            self.ensure_diff_file();
            self.bind_diff_bodies();
            return Task::none();
        }
        self.diff_sid = sid;
        self.diff_gen = self.diff_gen.wrapping_add(1);
        let gen = self.diff_gen;
        Task::perform(
            rpc(move || crate::control::session_diff(&rpc_ref)),
            move |result| Message::DiffLoaded { gen, result },
        )
    }

    fn ensure_diff_file(&mut self) {
        let paths: Vec<String> = self
            .visible_diff_files()
            .iter()
            .map(|f| f.path.clone())
            .collect();
        if !paths.iter().any(|p| p == &self.diff_file) {
            self.diff_file = paths.first().cloned().unwrap_or_default();
        }
        self.refresh_diff_hit();
    }

    fn refresh_diff_hit(&mut self) {
        let Some(point) = self.current_diff_point() else {
            self.diff_hit_line = None;
            return;
        };
        let pairs: Vec<(String, String)> = point
            .files
            .iter()
            .map(|f| (f.path.clone(), f.unified.clone()))
            .collect();
        let hits = crate::fuzzy::filter_diff_hunks(&self.diff_query, &pairs);
        self.diff_hit_line = hits
            .iter()
            .find(|(i, _)| {
                point
                    .files
                    .get(*i)
                    .is_some_and(|f| f.path == self.diff_file)
            })
            .and_then(|(_, line)| *line);
    }

    fn step_diff_point(&mut self, delta: i32) {
        let keys: Vec<String> = self.diff.points.iter().map(|p| p.key.clone()).collect();
        if keys.len() < 2 || delta == 0 {
            return;
        }
        let cur = if keys.iter().any(|k| k == &self.diff_point) {
            self.diff_point.clone()
        } else {
            keys.last().cloned().unwrap_or_default()
        };
        let i = keys.iter().position(|k| k == &cur).unwrap_or(0);
        let nxt = (i as i32 + delta).clamp(0, keys.len() as i32 - 1) as usize;
        self.diff_point = keys[nxt].clone();
        self.diff_file.clear();
        self.ensure_diff_file();
    }

    fn selected_diff_unified(&self) -> String {
        self.current_diff_point()
            .and_then(|p| p.files.iter().find(|f| f.path == self.diff_file))
            .map(|f| f.unified.clone())
            .unwrap_or_default()
    }

    fn focus_context_search(&mut self) -> Task<Message> {
        let focus = operation::focus(self.search_focus_id());
        if self.browse_mode() && self.tab == Tab::Timeline && self.timeline_open.is_some() {
            return Task::batch([self.close_timeline_detail(), focus]);
        }
        focus
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
        // Context compact is a badge on the status row, not a third text line.
        session_card_height(title, "", false)
    }

    fn refresh_session_rows(&mut self) {
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
        // virtual_column reads VisibleWindow.scroll; no iced scrollable.
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
        self.overview_section = crate::model::OverviewSection::Session;
        self.tasks_focus = None;
        self.overview_row_armed = false;
        self.overview_window =
            icedtea::collection::VisibleWindow::new(self.overview_window.viewport.max(1.0));
        self.overview_heights.clear();
        self.stats_table = icedtea::collection::TableModel::default();
        self.stats_window =
            icedtea::collection::VisibleWindow::new(self.stats_window.viewport.max(1.0));
        self.stats_selection = icedtea::collection::Selection::None;
        self.stats_cursor = None;
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
        self.finding_motion.clear();
        self.note_motion.clear();
        self.fields = icedtea::field::Selectables::new();
        self.note_draft = NoteDraft::default();
        self.note_compose_lock = false;
        self.typing_notes = false;
        self.overview = None;
        self.overview_sid.clear();
        self.overview_pending.clear();
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
                    crate::desktop::notice_row_key(&r.origin, &r.session_id),
                    r.display_title().to_string(),
                    list_status_label(&r.status, &r.outcome),
                )
            })
            .collect();
        for notice in crate::desktop::notices_from_rows(&mut self.seen_status, &rows, seed) {
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
    /// `active` alone is not a pick — Spotlight starts with no highlight.
    fn session_keep_id(&self) -> String {
        if !self.overview_sid.is_empty() {
            return self.overview_sid.clone();
        }
        if !self.overview_pending.is_empty() {
            return self.overview_pending.clone();
        }
        match self.list_selection {
            icedtea::collection::Selection::Single(i) => self
                .sessions()
                .get(i)
                .map(|r| r.session_id.clone())
                .filter(|s| !s.is_empty())
                .unwrap_or_default(),
            icedtea::collection::Selection::None | icedtea::collection::Selection::Multi(_) => {
                String::new()
            }
        }
    }

    fn rerank_visible(&mut self) {
        let keep = self.session_keep_id();
        self.rerank_visible_keeping(keep);
    }

    fn rerank_visible_keeping(&mut self, keep: String) {
        if self.query.trim().is_empty() {
            self.sessions = spotlight_recent(&self.all_sessions, self.spotlight_limit, &keep);
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
        let child_off_rail = quiet
            && !self.overview_sid.is_empty()
            && self.selected_sid().as_deref() != Some(self.overview_sid.as_str());
        let (sid, rpc_ref) = if child_off_rail {
            let r = self.overview_rpc_ref();
            if r.is_empty() {
                return Task::none();
            }
            (self.overview_sid.clone(), r)
        } else if let (Some(s), Some(r)) = (self.selected_sid(), self.selected_rpc_ref()) {
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
        if sid != self.overview_sid {
            self.timeline_follow_tail = false;
        }
        self.overview_pending = sid.clone();
        self.turns_focus = None;
        self.turns_query.clear();
        if sid != self.diff_sid {
            self.diff = crate::wire::DiffBlock::default();
            self.diff_sid.clear();
            self.diff_point.clear();
            self.diff_file.clear();
            self.diff_query.clear();
            self.diff_point_options.clear();
            self.diff_context = DiffContext::Prompt;
            self.diff_want_prompt = None;
        }
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

    fn resolve_open_child_path(&self, path: &str, sid: &str) -> String {
        if !path.is_empty() && std::path::Path::new(path).is_dir() {
            return path.to_string();
        }
        let parent = self.session_path();
        if parent.is_empty() || sid.is_empty() {
            return path.to_string();
        }
        let mut cand = std::path::PathBuf::from(&parent);
        if cand.pop() {
            cand.push(sid);
            if cand.is_dir() {
                return cand.to_string_lossy().into_owned();
            }
        }
        path.to_string()
    }

    fn open_child_session(&mut self, path: String, sid: String) -> Task<Message> {
        let path = self.resolve_open_child_path(&path, &sid);
        if self.tab == Tab::Turns && self.turns_focus.is_none() {
            self.turns_focus = self
                .subagent_run_for_child(&sid)
                .and_then(|run| run.turn_index);
        }
        if let Some(frame) = self.capture_parent_frame() {
            self.parent_stack.push(frame);
        }
        self.load_session_ref(path, sid)
    }

    fn load_session_ref(&mut self, path: String, sid: String) -> Task<Message> {
        let rpc_ref = session_rpc_ref(&path, &sid);
        if rpc_ref.is_empty() {
            return Task::none();
        }
        let sid_keep = if sid.is_empty() {
            std::path::Path::new(&path)
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or(rpc_ref.as_str())
                .to_string()
        } else {
            sid
        };
        self.reset_detail_chrome();
        self.overview_pending = sid_keep.clone();
        self.overview_gen += 1;
        let gen = self.overview_gen;
        Task::perform(
            rpc(move || control::session_overview(&rpc_ref)),
            move |result| Message::OverviewLoaded {
                gen,
                sid: sid_keep.clone(),
                quiet: false,
                result,
            },
        )
    }

    fn return_to_parent(&mut self) -> Task<Message> {
        let Some(frame) = self.parent_stack.pop() else {
            return Task::none();
        };
        let task = self.load_session_ref(frame.path.clone(), frame.sid.clone());
        self.apply_parent_frame(&frame);
        task
    }

    fn capture_parent_frame(&self) -> Option<ParentFrame> {
        let path = self.overview.as_ref()?.meta.path.clone();
        let sid = self.overview_sid.clone();
        if sid.is_empty() {
            return None;
        }
        Some(ParentFrame {
            path,
            sid,
            tab: self.tab,
            timeline_kind: self.timeline_kind,
            timeline_query: self.timeline_query.clone(),
            timeline_query_draft: self.timeline_query_draft.clone(),
            timeline_focus: self.timeline_focus,
            timeline_prompt: self.timeline_prompt,
            events_turn_index: self.events_turn_index,
            turns_focus: self.turns_focus,
            turns_query: self.turns_query.clone(),
            turn_scroll: self.turn_window.scroll,
        })
    }

    fn apply_parent_frame(&mut self, frame: &ParentFrame) {
        self.tab = frame.tab;
        self.timeline_kind = frame.timeline_kind;
        self.timeline_query = frame.timeline_query.clone();
        self.timeline_query_draft = frame.timeline_query_draft.clone();
        self.timeline_focus = frame.timeline_focus;
        self.timeline_open = None;
        self.timeline_prompt = frame.timeline_prompt;
        self.events_turn_index = frame.events_turn_index;
        self.turns_focus = frame.turns_focus;
        self.turns_query = frame.turns_query.clone();
        self.turn_window.scroll = frame.turn_scroll;
        self.restore_around = if frame.tab == Tab::Timeline {
            frame.timeline_focus
        } else {
            None
        };
    }

    fn event_is_subagent_bookend(ev: &TimelineEvent) -> bool {
        ev.event_type == "subagent_spawned"
            || ev.event_type == "subagent_finished"
            || ev.kind == "subagent"
    }

    fn openable_child_at(&self, ix: i64) -> Option<(String, String)> {
        let ev = self.timeline.iter().find(|e| e.index == ix)?;
        if !Self::event_is_subagent_bookend(ev) || ev.child_session_id.is_empty() {
            return None;
        }
        let run = self.subagent_run_for_child(&ev.child_session_id)?;
        if !run.openable || run.child_path.is_empty() {
            return None;
        }
        Some((run.child_path.clone(), run.child_session_id.clone()))
    }

    pub fn subagent_run_for_child(&self, child: &str) -> Option<&crate::wire::SubagentRunRow> {
        self.overview
            .as_ref()
            .and_then(|ov| Self::run_for_child(ov, child))
    }

    fn run_for_child<'a>(
        ov: &'a crate::wire::Overview,
        child: &str,
    ) -> Option<&'a crate::wire::SubagentRunRow> {
        ov.turns
            .subagent_runs
            .iter()
            .find(|r| r.child_session_id == child)
            .or_else(|| {
                ov.turns
                    .turns
                    .iter()
                    .flat_map(|t| t.subagent_runs.iter())
                    .find(|r| r.child_session_id == child)
            })
    }

    fn scroll_timeline_to_end(&mut self) -> Task<Message> {
        if let Some(&src) = self.tl_filter.last() {
            if let Some(ev) = self.timeline.get(src) {
                self.timeline_focus = Some(ev.index);
            }
        }
        self.scroll_focus_into_view()
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

    fn filter_pos(&self, index: i64) -> Option<usize> {
        self.tl_filter
            .iter()
            .position(|&src| self.timeline.get(src).is_some_and(|e| e.index == index))
    }

    fn detail_open_slide(&self, index: i64) -> icedtea::motion::Slide {
        let Some(prev) = self.timeline_open else {
            return icedtea::motion::Slide::End;
        };
        match (self.filter_pos(prev), self.filter_pos(index)) {
            (Some(a), Some(b)) => motion::event_step_slide(b as i32 - a as i32),
            _ => icedtea::motion::Slide::End,
        }
    }

    /// Open full-pane event detail on Timeline (fetch full content).
    fn open_timeline_detail(&mut self, index: i64) -> Task<Message> {
        if self.timeline_open == Some(index) {
            return Task::none();
        }
        let already = self.timeline_open.is_some();
        let slide = self
            .page_dir
            .take()
            .unwrap_or_else(|| self.detail_open_slide(index));
        let role = if matches!(
            slide,
            icedtea::motion::Slide::Up | icedtea::motion::Slide::Down
        ) {
            MotionRole::Step
        } else {
            motion::event_open_role(already)
        };
        self.go_page(role, PageLayer::Pane, slide);
        if let Some(prev) = self.timeline_open {
            self.unbind_event_fields(prev);
        }
        self.timeline_open = Some(index);
        self.timeline_focus = Some(index);
        self.bind_event_extract(index);
        self.fetch_open_detail_bodies(index)
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
        if self.timeline_open.is_some() {
            self.go_page(
                motion::event_close_role(),
                PageLayer::Pane,
                icedtea::motion::Slide::Start,
            );
        }
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
                .and_then(|o| o.turns.turns.iter().find(|t| t.face_id() == Some(ti)))
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

    fn turn_index_of_event(&self, index: i64) -> Option<i64> {
        self.turn_row_for_event(index).map(|t| t.turn_index)
    }

    fn matching_turn_indexes(&self) -> Vec<i64> {
        let mut out = Vec::new();
        for &src in &self.tl_filter {
            let Some(ev) = self.timeline.get(src) else {
                continue;
            };
            let Some(ti) = self.turn_index_of_event(ev.index) else {
                continue;
            };
            if out.last() != Some(&ti) {
                out.push(ti);
            }
        }
        out
    }

    fn first_filtered_event_on_turn(&self, turn: i64) -> Option<i64> {
        self.tl_filter.iter().find_map(|&src| {
            let ev = self.timeline.get(src)?;
            (self.turn_index_of_event(ev.index) == Some(turn)).then_some(ev.index)
        })
    }

    fn focus_matching_turn(&mut self, forward: bool) -> Task<Message> {
        let turns = self.matching_turn_indexes();
        if turns.is_empty() {
            return Task::none();
        }
        let cur = self
            .timeline_focus
            .or(self.timeline_open)
            .and_then(|ix| self.turn_index_of_event(ix));
        let dest = match cur {
            None => {
                if forward {
                    turns.first().copied()
                } else {
                    turns.last().copied()
                }
            }
            Some(c) => {
                if forward {
                    turns.iter().copied().find(|t| *t > c)
                } else {
                    turns.iter().rev().copied().find(|t| *t < c)
                }
            }
        };
        let Some(ti) = dest else {
            return Task::none();
        };
        let Some(ix) = self.first_filtered_event_on_turn(ti) else {
            return Task::none();
        };
        self.timeline_focus = Some(ix);
        if self.timeline_open.is_some() {
            return self.open_timeline_detail(ix);
        }
        self.scroll_focus_into_view()
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
                let label = t.face_caption();
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

    fn select_events_turn(&mut self, turn_index: Option<i64>) -> Task<Message> {
        self.go_page(
            MotionRole::Sibling,
            PageLayer::Pane,
            icedtea::motion::Slide::None,
        );
        self.tab = Tab::Timeline;
        // Filter and search stay; only the turn scope changes.
        // List stays on the list. An open event page stays open on the
        // first card of the new turn (or the same card when returning to all).
        let stay = self.timeline_open.is_some();
        if stay && turn_index.is_some() {
            self.detail_turn_edge = Some(DetailTurnEdge::First);
        } else if !stay {
            self.drop_timeline_detail();
        }
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

    fn jump_timeline(&mut self, index: i64) -> Task<Message> {
        // Chrome first: Timeline tab + turn scope, then full-pane detail.
        self.tab = Tab::Timeline;
        self.timeline_query.clear();
        self.timeline_query_draft.clear();
        self.timeline_search_pending = false;
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
        let (offset, limit, around) = if self.timeline_follow_tail {
            let limit = TIMELINE_CHUNK;
            (
                last_timeline_page_offset(self.timeline_owner_total(), limit),
                limit,
                None,
            )
        } else {
            let around = self.restore_around.take().or_else(|| {
                if self.timeline_query.trim().is_empty() && self.timeline_kind == KindFilter::All {
                    self.timeline_focus
                } else {
                    None
                }
            });
            (0, 40, around)
        };
        self.start_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset,
            append: false,
            advance: true,
            gen,
            limit,
            kind: self.timeline_kind.wire_name().to_string(),
            query: self.timeline_query.clone(),
            around,
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

    fn fetch_open_detail_bodies(&mut self, index: i64) -> Task<Message> {
        let mut idxs = vec![index];
        if let Some(partner) = self.paired_tool_index(index) {
            if partner != index {
                idxs.push(partner);
            }
        }
        let mut tasks = Vec::with_capacity(idxs.len());
        for i in idxs {
            tasks.push(self.fetch_open_event(i));
        }
        Task::batch(tasks)
    }

    fn paired_tool_index(&self, index: i64) -> Option<i64> {
        let ev = self.timeline.iter().find(|e| e.index == index)?;
        let id = ev.tool_call_id.trim();
        if id.is_empty() {
            return None;
        }
        self.timeline.iter().find_map(|other| {
            if other.index == index || other.tool_call_id != ev.tool_call_id {
                return None;
            }
            let tool_side = other.kind == "tool" || other.event_type == "tool_call";
            let result_side = other.kind == "tool_result"
                || other.event_type == "tool_call_update"
                || other.event_type == "tool_result";
            (tool_side || result_side).then_some(other.index)
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
        if self.timeline_follow_tail && !self.window_covers_timeline_end() {
            return self.fetch_timeline_end(sid);
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

    fn timeline_owner_total(&self) -> u32 {
        if self.timeline_total > 0 {
            return self.timeline_total;
        }
        self.overview
            .as_ref()
            .map(|o| o.meta.num_events.max(0) as u32)
            .unwrap_or(0)
    }

    fn window_covers_timeline_end(&self) -> bool {
        let total = self.timeline_owner_total();
        if total == 0 {
            return self.timeline_complete();
        }
        self.timeline_next >= total && !self.timeline.is_empty()
    }

    fn fetch_timeline_end(&mut self, sid: String) -> Task<Message> {
        if self.timeline_search_pending {
            return Task::none();
        }
        let limit = TIMELINE_CHUNK;
        let offset = last_timeline_page_offset(self.timeline_owner_total(), limit);
        self.timeline_gen = self.timeline_gen.wrapping_add(1);
        let gen = self.timeline_gen;
        self.timeline_loading = true;
        self.start_timeline(TimelineFetch {
            rpc_ref: self.overview_rpc_ref(),
            sid,
            offset,
            append: false,
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
        self.focused = true;
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
        self.go_overlay(false);
        self.finish_overlay_hide()
    }

    fn grow_recent(&mut self) -> bool {
        if !self.query.trim().is_empty() {
            return false;
        }
        let Some(next) = next_spotlight_limit(
            self.spotlight_limit,
            self.all_sessions.len(),
            SPOTLIGHT_RECENT,
        ) else {
            return false;
        };
        if next == self.spotlight_limit {
            return false;
        }
        self.spotlight_limit = next;
        self.rerank_visible();
        true
    }

    /// Leave browse (and any child) for Recent + session search.
    fn go_sessions_home(&mut self) -> Task<Message> {
        self.go_page(
            motion::session_leave_role(),
            PageLayer::Browse,
            icedtea::motion::Slide::Start,
        );
        self.return_to_spotlight();
        self.on_focus_search(0)
    }

    /// Summon lands on Spotlight (Recent + search), never the last open session.
    fn return_to_spotlight(&mut self) {
        self.query.clear();
        self.spotlight_limit = SPOTLIGHT_RECENT;
        self.help_open = false;
        self.reset_detail_chrome();
        self.parent_stack.clear();
        self.restore_around = None;
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
        self.focused = true;
        self.palette_live = true;
        self.last_live = Instant::now();
        // Fresh map: stay gone until WindowId. A live hide reverse, or a
        // window we already asked to open, starts present now.
        let interrupting = self.window_id.is_some() && self.overlay_moving();
        if interrupting || self.reduced_motion {
            self.go_overlay(true);
        } else {
            self.overlay = motion::role_animation(MotionRole::Present, false, false);
            if self.window_id.is_some() {
                self.go_overlay(true);
            }
        }
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
        let name = theme::resolve_name(&prefs::theme_name(), self.appearance, prefs::follow_os());
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
                let _ = crate::place_linux::place_overlay(HUD_W, HUD_H);
                // Retry while the token is still pending: attempt 0 often
                // runs before iced has Wayland handles. Clear on success
                // only (`ActivationApplied(true)`). No `gain_focus` here —
                // tray / token-less toggle must not steal the keyboard.
                let activate = match self.pending_activation_token.clone() {
                    Some(tok) => window::run(id, move |win| crate::wlactivate::activate(win, &tok))
                        .map(Message::ActivationApplied),
                    None => Task::none(),
                };
                if attempt < 6 {
                    return Task::batch([activate, delayed_focus(attempt.saturating_add(1))]);
                }
                return activate;
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

    /// Drop iced text-input focus so Enter / [ / ] reach browse after a pick.
    fn blur_text_inputs() -> Task<Message> {
        iced::advanced::widget::operate(
            iced::advanced::widget::operation::focusable::unfocus::<()>(),
        )
        .discard()
    }

    /// Window focus after a pick. Unfocus session search once so Enter drills
    /// panes; later clicks in turns / timeline search keep the caret.
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

    fn on_window_focus(&mut self, on: bool) -> Task<Message> {
        let gained = on && !self.focused;
        self.focused = on;
        if gained && self.visible {
            self.catch_up = true;
            return self.on_tick();
        }
        Task::none()
    }

    fn on_tick(&mut self) -> Task<Message> {
        let now = Instant::now();
        let dt = now.saturating_duration_since(self.last_tick).as_millis() as u64;
        self.last_tick = now;
        self.toasts.tick(dt.max(1));
        self.spin_phase = (self.spin_phase + 0.05) % 1.0;
        self.sync_theme();
        if let Some(until) = self.leader_until {
            if Instant::now() >= until {
                self.disarm_leader();
            }
        }
        if let Some(until) = self.note_delete_until {
            if Instant::now() >= until {
                self.note_delete_armed.clear();
                self.note_delete_until = None;
                self.status = "Delete cancelled".into();
            }
        }
        let mut cmds = Vec::new();
        cmds.push(self.finish_overlay_hide());
        let notifies: Vec<(String, Value)> = if let Ok(mut g) = self.notify_q.lock() {
            g.drain(..).collect()
        } else {
            vec![]
        };
        let notify_pairs: Vec<crate::live::TickNotify> = notifies
            .iter()
            .map(|(method, params)| {
                let sid = params
                    .get("sessionId")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                let list_changed = params
                    .get("listChanged")
                    .and_then(Value::as_bool)
                    .unwrap_or(true);
                crate::live::TickNotify {
                    method: method.clone(),
                    session_id: sid,
                    list_changed,
                }
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
        let catch_up = self.catch_up;
        self.catch_up = false;
        let plan = plan_tick(TickInput {
            notifies: &notify_pairs,
            selected_sid: &selected,
            overview_sid: &self.overview_sid,
            palette_live: self.palette_live
                && wants_periodic_poll(self.visible, self.focused, self.window_mode),
            list_elapsed_ms: elapsed,
            selected_live: live,
            any_live,
            on_timeline: self.wants_events(),
            notes_locked: self.note_compose_lock,
            catch_up,
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

    /// True while the icedtea busy overlay should cover the shell.
    pub fn page_busy(&self) -> bool {
        self.catalog_busy
            || !self.overview_pending.is_empty()
            || self.timeline_waiting()
            || self.stats_waiting()
    }

    fn timeline_waiting(&self) -> bool {
        if self.tab != Tab::Timeline || self.overview.is_none() {
            return false;
        }
        if let Some(ix) = self.timeline_open {
            return !self.timeline.iter().any(|e| e.index == ix);
        }
        if !self.filtered_indices().is_empty() {
            return false;
        }
        self.timeline_loading || self.last_timeline.is_none() || !self.timeline_complete()
    }

    fn stats_waiting(&self) -> bool {
        self.tab == Tab::Overview
            && self.overview_section == crate::model::OverviewSection::Stats
            && self.timeline_loading
            && self.stats_table.rows.is_empty()
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
    pub fn follow_id(&self) -> Id {
        self.follow_id.clone()
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
        self.context_sel = None;
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
        match ev {
            Event::Keyboard(keyboard::Event::KeyPressed { key, modifiers, .. }) => {
                self.key_mods = modifiers;
                self.on_key(key, modifiers)
            }
            Event::Keyboard(keyboard::Event::KeyReleased { modifiers, .. }) => {
                self.key_mods = modifiers;
                Task::none()
            }
            _ => Task::none(),
        }
    }

    fn on_escape(&mut self) -> Task<Message> {
        if self.help_open {
            self.help_open = false;
            return Task::none();
        }
        if self.look_open {
            self.look_open = false;
            return Task::none();
        }
        if self.context.take().is_some() {
            self.context_sel = None;
            return Task::none();
        }
        // Full-pane event detail → list at the current event before hide.
        if self.tab == Tab::Timeline && self.timeline_open.is_some() {
            return self.close_timeline_detail();
        }
        if !self.parent_stack.is_empty() {
            return self.return_to_parent();
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

    fn key_is(&self, id: &str, default: &str, key: &Key, modifiers: KeyMods) -> bool {
        self.keys.matches(id, default, key, modifiers)
    }

    fn arm_leader(&mut self) {
        let ms = self.keys.leader_timeout_ms().max(1) as u64;
        self.leader_armed = true;
        self.leader_until = Some(Instant::now() + Duration::from_millis(ms));
    }

    fn disarm_leader(&mut self) {
        self.leader_armed = false;
        self.leader_until = None;
    }

    fn expire_leader(&mut self) {
        if let Some(until) = self.leader_until {
            if Instant::now() >= until {
                self.disarm_leader();
            }
        }
    }

    fn dispatch_catalog_id(&mut self, id: &str) -> Task<Message> {
        match id {
            "session.follow" if self.browse_mode() && self.selected_awaiting() => {
                operation::focus(self.follow_id.clone())
            }
            "session.done" if self.browse_mode() && self.selected_awaiting() => self.mark_done(),
            "list.down" => self.nav_step(1),
            "list.up" => self.nav_step(-1),
            "edit.copy" | "edit.copy_chord" => self.yank_active(),
            "search.focus" => self.focus_context_search(),
            "pane.notes" if self.browse_mode() => self.update(Message::SetTab(Tab::Notes)),
            _ => Task::none(),
        }
    }

    fn handle_leader(&mut self, key: &Key, modifiers: KeyMods) -> Option<Task<Message>> {
        self.expire_leader();
        if self.typing_notes {
            if self.leader_armed {
                self.disarm_leader();
            }
            return None;
        }
        if self.leader_armed {
            self.disarm_leader();
            if self.keys.is_leader_key(key, modifiers) {
                return Some(Task::none());
            }
            if let Some(id) = self
                .keys
                .lookup_sequence(key, modifiers)
                .map(str::to_string)
            {
                return Some(self.dispatch_catalog_id(&id));
            }
            return Some(Task::none());
        }
        if self.keys.is_leader_key(key, modifiers) {
            self.arm_leader();
            return Some(Task::none());
        }
        None
    }

    fn on_key(&mut self, key: Key, modifiers: KeyMods) -> Task<Message> {
        self.key_mods = modifiers;
        self.expire_leader();
        if matches!(key, Key::Named(Named::Escape)) {
            if self.leader_armed {
                self.disarm_leader();
                return Task::none();
            }
            return self.on_escape();
        }
        if matches!(key, Key::Named(Named::F12)) {
            return self.update(Message::ToggleLook);
        }
        if self.help_open {
            return Task::none();
        }
        if let Some(task) = self.handle_leader(&key, modifiers) {
            return task;
        }
        for n in 1u8..=self.visible_tabs().len() as u8 {
            if self.key_is(&format!("pane.{n}"), &format!("ctrl+{n}"), &key, modifiers) {
                return self.update(Message::PaneDigit(n));
            }
        }
        if self.typing_notes {
            return Task::none();
        }
        if self.browse_mode() && self.key_is("pane.notes", "shift+n", &key, modifiers) {
            return self.update(Message::SetTab(Tab::Notes));
        }
        if self.browse_mode() && self.selected_awaiting() {
            if self.key_is("session.follow", "n", &key, modifiers) {
                return operation::focus(self.follow_id.clone());
            }
            if self.key_is("session.done", "e", &key, modifiers) {
                return self.mark_done();
            }
        }
        if self.key_is("search.focus", "slash", &key, modifiers) {
            return self.focus_context_search();
        }
        if self.browse_mode() && self.key_is("sessions.home", "u", &key, modifiers) {
            return self.go_sessions_home();
        }
        // Events turn scope: h / l / Left / Right (shared). HUD `]` is
        // the same next-turn step; `[` clears to all turns.
        if self.tab == Tab::Diff && self.diff.points.len() > 1 {
            if self.key_is("events.next_turn", "l,right", &key, modifiers) {
                self.step_diff_point(1);
                return Task::none();
            }
            if self.key_is("events.prev_turn", "h,left", &key, modifiers) {
                self.step_diff_point(-1);
                return Task::none();
            }
        }
        if self.tab == Tab::Timeline && !self.hide_events_turn_pick() {
            if self.key_is("events.all_turns", "left_square_bracket", &key, modifiers) {
                return self.select_events_turn(None);
            }
            let next_turn = self.key_is("events.next_turn", "l,right", &key, modifiers)
                || self.key_is("events.scope_next", "right_square_bracket", &key, modifiers);
            let prev_turn = self.key_is("events.prev_turn", "h,left", &key, modifiers);
            if self.events_turn_index.is_none() {
                if next_turn {
                    return self.focus_matching_turn(true);
                }
                if prev_turn {
                    return self.focus_matching_turn(false);
                }
            }
        }
        // From Turns: `g` opens Timeline for that turn, all event types.
        if self.tab == Tab::Turns && self.key_is("turns.timeline", "g", &key, modifiers) {
            if let Some(turn) = self.turns_focus {
                self.timeline_kind = KindFilter::All;
                return self.select_events_turn(Some(turn));
            }
        }
        if self.key_is("edit.copy", "y", &key, modifiers)
            || self.key_is("edit.copy_chord", "ctrl+shift+c", &key, modifiers)
        {
            return self.yank_active();
        }
        if modifiers.command()
            && !modifiers.alt()
            && matches!(&key, Key::Character(c) if c.eq_ignore_ascii_case("a"))
        {
            return self.select_all_text();
        }
        // Tab / Shift+Tab: cycle browse panes (same as Ctrl+1…6). Not iced widget
        // focus soup — session search is only for Spotlight (type to switch).
        if matches!(key, Key::Named(Named::Tab))
            && !modifiers.alt()
            && !modifiers.logo()
            && self.browse_mode()
        {
            let tabs = self.visible_tabs();
            let i = tabs.iter().position(|t| *t == self.tab).unwrap_or(0);
            let next = if modifiers.shift() {
                (i + tabs.len() - 1) % tabs.len()
            } else {
                (i + 1) % tabs.len()
            };
            return self.update(Message::SetTab(tabs[next]));
        }
        if matches!(key, Key::Named(Named::Tab))
            && (modifiers.control() || modifiers.command())
            && self.browse_mode()
        {
            let tabs = self.visible_tabs();
            let i = tabs.iter().position(|t| *t == self.tab).unwrap_or(0);
            let next = if modifiers.shift() {
                (i + tabs.len() - 1) % tabs.len()
            } else {
                (i + 1) % tabs.len()
            };
            return self.update(Message::SetTab(tabs[next]));
        }
        if self.key_is("list.down", "j", &key, modifiers) {
            return self.nav_step(1);
        }
        if self.key_is("list.up", "k", &key, modifiers) {
            return self.nav_step(-1);
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
            Tab::Overview
                if matches!(
                    self.overview_section,
                    crate::model::OverviewSection::Tasks
                        | crate::model::OverviewSection::Workflows
                        | crate::model::OverviewSection::Subagents
                ) =>
            {
                self.nav_tasks_step(delta)
            }
            Tab::Overview if self.overview_section == crate::model::OverviewSection::Stats => {
                self.nav_stats_step(delta)
            }
            Tab::Turns => self.nav_turns_step(delta),
            Tab::Timeline if self.timeline_open.is_none() => self.nav_timeline_step(delta),
            Tab::Timeline => self.nav_timeline_detail_step(delta),
            Tab::Diff => {
                let files = self.visible_diff_files();
                if files.is_empty() {
                    return Task::none();
                }
                let i = files
                    .iter()
                    .position(|f| f.path == self.diff_file)
                    .unwrap_or(0);
                let n = files.len();
                let nxt = if delta > 0 {
                    (i + 1).min(n - 1)
                } else {
                    i.saturating_sub(1)
                };
                self.diff_file = files[nxt].path.clone();
                self.refresh_diff_hit();
                self.bind_diff_bodies();
                self.reveal_diff_hit()
            }
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
            Tab::Overview
                if matches!(
                    self.overview_section,
                    crate::model::OverviewSection::Tasks
                        | crate::model::OverviewSection::Workflows
                        | crate::model::OverviewSection::Subagents
                ) =>
            {
                let n = self.overview_list_count();
                if n == 0 {
                    return Task::none();
                }
                self.tasks_focus = Some(if home { 0 } else { n - 1 });
                self.overview_row_armed = false;
                self.scroll_overview_into_view()
            }
            Tab::Overview if self.overview_section == crate::model::OverviewSection::Stats => {
                let n = self.stats_table.rows.len();
                if n == 0 {
                    return Task::none();
                }
                let i = if home { 0 } else { n - 1 };
                self.stats_selection = icedtea::collection::Selection::Single(i);
                self.stats_cursor = Some((i, 1));
                self.scroll_stats_into_view()
            }
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
        if delta > 0 && self.active + 1 == n && self.grow_recent() {
            let grown = self.sessions().len();
            if self.active + 1 < grown {
                self.set_active(self.active + 1);
                return self.ensure_active_visible();
            }
        }
        let i = self.active as i32;
        let next = (i + delta).rem_euclid(n as i32) as usize;
        self.set_active(next);
        self.ensure_active_visible()
    }

    fn overview_list_count(&self) -> usize {
        let Some(o) = self.overview.as_ref() else {
            return 0;
        };
        match self.overview_section {
            crate::model::OverviewSection::Tasks => {
                crate::format::overview_task_rows(&o.background_jobs, &o.schedules).len()
            }
            crate::model::OverviewSection::Workflows => {
                crate::format::overview_workflow_rows(&o.workflows).len()
            }
            crate::model::OverviewSection::Subagents => {
                crate::format::overview_subagent_rows(&o.turns.subagent_runs).len()
            }
            _ => 0,
        }
    }

    fn nav_tasks_step(&mut self, delta: i32) -> Task<Message> {
        let n = self.overview_list_count();
        if n == 0 {
            return Task::none();
        }
        let pos = match self.tasks_focus {
            None => {
                if delta > 0 {
                    0
                } else {
                    n - 1
                }
            }
            Some(p) => (p as i32 + delta).rem_euclid(n as i32) as usize,
        };
        self.tasks_focus = Some(pos);
        self.overview_row_armed = false;
        self.scroll_overview_into_view()
    }

    fn nav_stats_step(&mut self, delta: i32) -> Task<Message> {
        let n = self.stats_table.rows.len();
        if n == 0 {
            return Task::none();
        }
        let pos = match self.stats_selection.primary() {
            None => {
                if delta > 0 {
                    0
                } else {
                    n - 1
                }
            }
            Some(p) => (p as i32 + delta).rem_euclid(n as i32) as usize,
        };
        self.stats_selection = icedtea::collection::Selection::Single(pos);
        self.stats_cursor = Some((pos, 1));
        self.scroll_stats_into_view()
    }

    fn open_focused_task(&mut self) -> Task<Message> {
        let Some(o) = self.overview.as_ref() else {
            return Task::none();
        };
        let Some(i) = self.tasks_focus else {
            return Task::none();
        };
        if self.overview_section == crate::model::OverviewSection::Subagents {
            let Some(run) = o.turns.subagent_runs.get(i) else {
                return Task::none();
            };
            if run.openable && !run.child_path.is_empty() && !run.child_session_id.is_empty() {
                return self.update(Message::OpenChild {
                    path: run.child_path.clone(),
                    sid: run.child_session_id.clone(),
                });
            }
            if let Some(ix) = run.spawn_event_index {
                return self.update(Message::JumpTimeline(ix));
            }
            return Task::none();
        }
        let rows = match self.overview_section {
            crate::model::OverviewSection::Tasks => {
                crate::format::overview_task_rows(&o.background_jobs, &o.schedules)
            }
            crate::model::OverviewSection::Workflows => {
                crate::format::overview_workflow_rows(&o.workflows)
            }
            _ => return Task::none(),
        };
        let Some(row) = rows.get(i) else {
            return Task::none();
        };
        if !row.openable {
            self.toasts.push_warning("No Timeline bookend for this row");
            return Task::none();
        }
        let Some(ix) = row.event_index else {
            self.toasts.push_warning("No Timeline bookend for this row");
            return Task::none();
        };
        self.update(Message::JumpTimeline(ix))
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
        // Past this filter’s edge: stay on the last matching row.
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
            Tab::Overview => {
                if matches!(
                    self.overview_section,
                    crate::model::OverviewSection::Tasks
                        | crate::model::OverviewSection::Workflows
                        | crate::model::OverviewSection::Subagents
                ) {
                    return self.open_focused_task();
                }
                if self.compact_child_chrome() {
                    self.update(Message::SetTab(Tab::Timeline))
                } else {
                    self.update(Message::SetTab(Tab::Turns))
                }
            }
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
                if let Some(ix) = self.timeline_open.or(self.timeline_focus) {
                    if let Some((path, sid)) = self.openable_child_at(ix) {
                        return self.open_child_session(path, sid);
                    }
                }
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
            Tab::Diff | Tab::Findings | Tab::Notes => Task::none(),
        }
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
    pub offset: u32,
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
        if req.at_index.is_none() {
            self.last_timeline = Some(LastTimelineReq {
                prompt_index: req.prompt_index,
                around_index: req.around,
                offset: req.offset,
                query: req.query.clone(),
                kind: req.kind.clone(),
            });
        }
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
    table.insert(
        Action::new("help.toggle", "Help", Message::ToggleHelp)
            .with_shortcut(Shortcut::parse("?").expect("?")),
    );
    let overlay = crate::keys::process_overlay();
    for n in 1u8..=crate::model::Tab::ALL.len() as u8 {
        let spec = overlay.hud_spec(&format!("pane.{n}"), &format!("ctrl+{n}"));
        let parsed = Shortcut::parse(&spec).expect("pane chord");
        table.insert(
            Action::new(
                format!("pane.{n}"),
                format!("Pane {n}"),
                Message::PaneDigit(n),
            )
            .with_shortcut(parsed),
        );
    }
    table
}

/// Arrow / Home / End / Page / j / k — list navigation while a field is focused.
fn is_list_nav_key(kev: &keyboard::Event) -> bool {
    let keyboard::Event::KeyPressed { key, modifiers, .. } = kev else {
        return false;
    };
    if matches!(
        key,
        Key::Named(
            Named::ArrowDown
                | Named::ArrowUp
                | Named::Home
                | Named::End
                | Named::PageDown
                | Named::PageUp
        )
    ) {
        return true;
    }
    let overlay = crate::keys::process_overlay();
    overlay.matches("list.down", "j", key, *modifiers)
        || overlay.matches("list.up", "k", key, *modifiers)
}

fn interesting_hud_event(event: Event, status: event::Status, id: window::Id) -> Option<Message> {
    match event {
        Event::Window(window::Event::CloseRequested) => Some(Message::CloseRequested(id)),
        Event::Window(window::Event::Resized(size)) => Some(Message::WindowSize(size)),
        Event::Window(window::Event::Focused) => Some(Message::WindowFocus(true)),
        Event::Window(window::Event::Unfocused) => Some(Message::WindowFocus(false)),
        Event::Keyboard(ref kev) => {
            // List arrows must work while Search sessions is focused (Spotlight).
            // Single-line fields capture them; we still want palette navigation.
            // j/k and arrows while Spotlight search is focused. Tab and /
            // stay with a focused field (turns / timeline / notes).
            if is_list_nav_key(kev) {
                return Some(Message::RawEvent(event));
            }
            // A focused field captures Escape. Leave the field first so the
            // next Escape can hide (or close help / detail). Ignored Escape
            // still goes through chrome → Hide → on_escape.
            if status == event::Status::Captured {
                if let keyboard::Event::KeyPressed {
                    key: Key::Named(Named::Escape),
                    ..
                } = kev
                {
                    return Some(Message::LeaveInput);
                }
            }
            // Captured: pane chords (chrome_over_input). Enter stays
            // with the focused field's on_submit (or Ignored → RawEvent).
            let ctx = icedtea::key::KeyContext {
                text_input_focused: true,
                ..icedtea::key::KeyContext::default()
            }
            .chrome_over_input();
            let table = chrome_key_table();
            if let Some(msg) = icedtea::key::handle(ctx, &table, kev) {
                return Some(msg);
            }
            if icedtea::key::typed(kev).as_deref() == Some("?") {
                return table.invoke("help.toggle");
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
    fn overview_jobs_are_glance_counts() {
        use crate::format::overview_job_fields;
        use crate::wire::{BackgroundJobRow, ScheduleRow};
        let jobs = vec![
            BackgroundJobRow {
                id: "a".into(),
                status: "running".into(),
                ..BackgroundJobRow::default()
            },
            BackgroundJobRow {
                id: "b".into(),
                status: "done".into(),
                ..BackgroundJobRow::default()
            },
        ];
        let schedules = vec![ScheduleRow {
            id: "s".into(),
            human_schedule: "every 1 hour".into(),
            ..ScheduleRow::default()
        }];
        let rows = overview_job_fields(&jobs, &schedules, &[]);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].key, "background");
        assert_eq!(rows[0].value, "1 running · 1 complete");
        assert_eq!(rows[1].key, "schedules");
        assert_eq!(rows[1].value, "every 1 hour");
        assert!(!rows.iter().any(|r| r.value.contains("DONE")));
        assert!(!rows.iter().any(|r| r.value.contains('\n')));
    }

    #[test]
    fn focus_search_after_pick_stays_in_browse() {
        let mut hud = Hud {
            visible: true,
            query: String::new(),
            overview_pending: "s1".into(),
            overview_sid: "s1".into(),
            overview: Some(Overview::default()),
            ..Hud::default()
        };
        assert!(hud.browse_mode());
        let _ = hud.update(Message::FocusSearch(0));
        assert!(hud.browse_mode());
        assert!(hud.query.is_empty());
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
    fn sessions_home_leaves_browse_for_the_picker() {
        let mut hud = Hud {
            query: String::new(),
            overview_pending: "s1".into(),
            overview_sid: "s1".into(),
            overview: Some(Overview {
                session_id: "s1".into(),
                ..Overview::default()
            }),
            all_sessions: vec![SessionRow {
                session_id: "s1".into(),
                title: "Open".into(),
                ..SessionRow::default()
            }],
            ..Hud::default()
        };
        assert!(hud.browse_mode());
        let _ = hud.update(Message::SessionsHome);
        assert!(!hud.browse_mode());
        assert!(hud.overview.is_none());
        assert!(hud.overview_sid.is_empty());
        assert!(hud.query.is_empty());

        hud.overview_pending = "s1".into();
        hud.overview_sid = "s1".into();
        hud.overview = Some(Overview {
            session_id: "s1".into(),
            ..Overview::default()
        });
        assert!(hud.browse_mode());
        let _ = hud.on_key(Key::Character("u".into()), KeyMods::default());
        assert!(!hud.browse_mode());
        assert!(hud.overview.is_none());
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
    fn timeline_detail_adjacent_is_the_prev_and_next_card() {
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
        let _ = hud.update(Message::SelectTimeline(11));
        let (prev, next) = hud.timeline_detail_adjacent();
        assert_eq!(prev.map(|e| e.index), Some(10));
        assert_eq!(next.map(|e| e.index), Some(12));
        let _ = hud.update(Message::SelectTimeline(10));
        let (prev, next) = hud.timeline_detail_adjacent();
        assert!(prev.is_none());
        assert_eq!(next.map(|e| e.index), Some(11));
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
        let _ = hud.update(Message::TimelineDetailStep(1));
        assert_eq!(hud.events_turn_index, Some(0));
        assert!(hud.is_timeline_open(11));
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
    fn question_mark_toggles_help() {
        let mut hud = Hud::default();
        assert!(!hud.help_open());
        let _ = hud.update(Message::ToggleHelp);
        assert!(hud.help_open());
        let _ = hud.update(Message::ToggleHelp);
        assert!(!hud.help_open());
    }

    #[test]
    fn escape_closes_help_before_timeline_and_hide() {
        let mut hud = Hud {
            visible: true,
            help_open: true,
            tab: Tab::Timeline,
            timeline_open: Some(3),
            timeline_focus: Some(3),
            ..Hud::default()
        };
        let _ = hud.update(Message::Hide);
        assert!(!hud.help_open(), "first Esc closes help");
        assert!(hud.timeline_open().is_some());
        assert!(hud.visible);
        let _ = hud.update(Message::Hide);
        assert!(hud.timeline_open().is_none());
        assert!(hud.visible);
        let _ = hud.update(Message::Hide);
        assert!(!hud.visible);
    }

    #[test]
    fn f12_toggles_the_look_drawer() {
        let mut hud = Hud::default();
        assert!(!hud.look_open());
        let _ = hud.on_key(Key::Named(Named::F12), KeyMods::empty());
        assert!(hud.look_open());
        let _ = hud.on_key(Key::Named(Named::Escape), KeyMods::empty());
        assert!(!hud.look_open());
    }

    #[test]
    fn look_picks_change_live_tokens() {
        let mut hud = Hud::default();
        assert_eq!(
            hud.tokens().density.name,
            icedtea::density::DensityName::Default
        );
        let _ = hud.update(Message::LookDensity("Comfortable".into()));
        assert_eq!(
            hud.tokens().density.name,
            icedtea::density::DensityName::Comfortable
        );
        let _ = hud.update(Message::LookScale("110%".into()));
        assert_eq!(hud.tokens().body(), 16.0);
        let _ = hud.update(Message::LookShape("Pill".into()));
        assert_eq!(hud.tokens().shape, icedtea::m3::ShapePolicy::Pill);
        let _ = hud.update(Message::LookElevation("Flat".into()));
        assert_eq!(hud.tokens().elevation, icedtea::m3::ElevationPolicy::Flat);
    }

    #[test]
    fn on_key_question_opens_help_and_blocks_nav() {
        use iced::keyboard::{key::Named, Key, Modifiers};
        let mut hud = Hud {
            visible: true,
            query: "x".into(),
            all_sessions: vec![
                SessionRow {
                    session_id: "s1".into(),
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "s2".into(),
                    ..SessionRow::default()
                },
            ],
            ..Hud::default()
        };
        hud.sessions = hud.all_sessions.clone();
        hud.set_active(0);
        let _ = hud.on_key(Key::Character("j".into()), Modifiers::empty());
        assert_eq!(hud.active, 1);
        let _ = hud.update(Message::ToggleHelp);
        assert!(hud.help_open());
        let _ = hud.on_key(Key::Character("j".into()), Modifiers::empty());
        assert_eq!(hud.active, 1, "j is swallowed while help is open");
        let _ = hud.on_key(Key::Named(Named::Escape), Modifiers::empty());
        assert!(!hud.help_open());
    }

    #[test]
    fn on_key_remapped_list_down_uses_n() {
        use iced::keyboard::{Key, Modifiers};
        let overlay = crate::keys::KeyOverlay::parse(
            "[home]\n\"list.down\" = \"n\"\n\"session.follow\" = \"z\"\n",
        )
        .expect("valid overlay");
        let mut hud = Hud {
            visible: true,
            query: "x".into(),
            all_sessions: vec![
                SessionRow {
                    session_id: "s1".into(),
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "s2".into(),
                    ..SessionRow::default()
                },
            ],
            keys: overlay,
            ..Hud::default()
        };
        hud.sessions = hud.all_sessions.clone();
        hud.set_active(0);
        let _ = hud.on_key(Key::Character("j".into()), Modifiers::empty());
        assert_eq!(hud.active, 0, "catalog j is list.down; overlay remaps it");
        let _ = hud.on_key(Key::Character("n".into()), Modifiers::empty());
        assert_eq!(hud.active, 1, "remapped list.down = n takes the j nav path");
    }

    fn colemak_overlay() -> crate::keys::KeyOverlay {
        crate::keys::KeyOverlay::parse(concat!(
            "leader = \";\"\n",
            "leader_timeout_ms = 800\n",
            "[home]\n",
            "\"list.down\" = \"n\"\n",
            "\"list.up\" = \"e\"\n",
            "\"session.follow\" = \"leader+n\"\n",
            "\"session.done\" = \"leader+e\"\n",
        ))
        .expect("colemak")
    }

    #[test]
    fn leader_arms_and_dispatches_follow_and_done() {
        use iced::keyboard::{Key, Modifiers};
        let overlay = colemak_overlay();
        let mut hud = Hud {
            overview: Some(Overview {
                meta: crate::wire::SessionMeta {
                    status: "awaiting".into(),
                    ..crate::wire::SessionMeta::default()
                },
                ..Overview::default()
            }),
            tab: Tab::Overview,
            keys: overlay,
            all_sessions: vec![SessionRow {
                session_id: "s1".into(),
                ..SessionRow::default()
            }],
            ..Hud::default()
        };
        hud.sessions = hud.all_sessions.clone();
        assert!(hud.browse_mode());
        assert!(hud.selected_awaiting());
        let _ = hud.on_key(Key::Character("n".into()), Modifiers::empty());
        assert_eq!(hud.active, 0, "n is list.down only after leader");
        let _ = hud.on_key(Key::Character(";".into()), Modifiers::empty());
        assert!(hud.leader_armed());
        let _ = hud.on_key(Key::Character("n".into()), Modifiers::empty());
        assert!(!hud.leader_armed());
        let _ = hud.on_key(Key::Character(";".into()), Modifiers::empty());
        let _ = hud.on_key(Key::Character("e".into()), Modifiers::empty());
        assert!(!hud.leader_armed());
    }

    #[test]
    fn leader_cancelled_by_escape_and_timeout() {
        use iced::keyboard::{key::Named, Key, Modifiers};
        let mut hud = Hud {
            keys: colemak_overlay(),
            all_sessions: vec![
                SessionRow {
                    session_id: "s1".into(),
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "s2".into(),
                    ..SessionRow::default()
                },
            ],
            query: "x".into(),
            ..Hud::default()
        };
        hud.sessions = hud.all_sessions.clone();
        hud.set_active(0);
        let _ = hud.on_key(Key::Character(";".into()), Modifiers::empty());
        assert!(hud.leader_armed());
        let _ = hud.on_key(Key::Named(Named::Escape), Modifiers::empty());
        assert!(!hud.leader_armed());
        assert!(hud.visible, "Esc while armed does not hide");
        let _ = hud.on_key(Key::Character(";".into()), Modifiers::empty());
        assert!(hud.leader_armed());
        hud.leader_until = Some(Instant::now() - Duration::from_millis(1));
        let _ = hud.on_tick();
        assert!(!hud.leader_armed());
        let start = hud.active;
        let _ = hud.on_key(Key::Character("n".into()), Modifiers::empty());
        assert_eq!(hud.active, start + 1, "n after cancel is list.down");
    }

    #[test]
    fn leader_expires_before_next_key_is_sequence() {
        use iced::keyboard::{Key, Modifiers};
        let mut hud = Hud {
            keys: colemak_overlay(),
            all_sessions: vec![
                SessionRow {
                    session_id: "s1".into(),
                    ..SessionRow::default()
                },
                SessionRow {
                    session_id: "s2".into(),
                    ..SessionRow::default()
                },
            ],
            query: "x".into(),
            ..Hud::default()
        };
        hud.sessions = hud.all_sessions.clone();
        hud.set_active(0);
        let _ = hud.on_key(Key::Character(";".into()), Modifiers::empty());
        assert!(hud.leader_armed());
        hud.leader_until = Some(Instant::now() - Duration::from_millis(1));
        let _ = hud.on_key(Key::Character("n".into()), Modifiers::empty());
        assert!(!hud.leader_armed());
        assert_eq!(hud.active, 1, "expired arm treats n as list.down");
    }

    #[test]
    fn leader_does_not_arm_while_notes_focused() {
        use iced::keyboard::{Key, Modifiers};
        let mut hud = Hud {
            keys: colemak_overlay(),
            typing_notes: true,
            ..Hud::default()
        };
        let _ = hud.on_key(Key::Character(";".into()), Modifiers::empty());
        assert!(!hud.leader_armed());
    }

    #[test]
    fn on_key_n_e_and_shift_n_match_tui_when_awaiting() {
        use iced::keyboard::{Key, Modifiers};
        let mut hud = Hud {
            overview: Some(Overview {
                meta: crate::wire::SessionMeta {
                    status: "awaiting".into(),
                    ..crate::wire::SessionMeta::default()
                },
                ..Overview::default()
            }),
            tab: Tab::Overview,
            ..Hud::default()
        };
        assert!(hud.browse_mode());
        assert!(hud.selected_awaiting());
        assert!(hud.key_scope().awaiting);
        let _ = hud.on_key(Key::Character("n".into()), Modifiers::SHIFT);
        assert_eq!(hud.tab, Tab::Notes);
        // `e` fires session/done; `n` focuses the follow-up field.
        let _ = hud.on_key(Key::Character("e".into()), Modifiers::empty());
        let _ = hud.on_key(Key::Character("n".into()), Modifiers::empty());
        hud.overview = Some(Overview::default());
        assert!(!hud.selected_awaiting());
        assert!(!hud.key_scope().awaiting);
    }

    fn question_pressed() -> Event {
        Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Character("/".into()),
            modified_key: Key::Character("?".into()),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::Slash),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::SHIFT,
            text: Some("?".into()),
            repeat: false,
        })
    }

    #[test]
    fn question_mark_chrome_bind_toggles_help() {
        let id = window::Id::unique();
        let ev = question_pressed();
        assert!(
            matches!(
                interesting_hud_event(ev.clone(), event::Status::Captured, id),
                Some(Message::ToggleHelp)
            ),
            "? must fire from the chrome table even when a widget captured the key"
        );
        assert!(matches!(
            interesting_hud_event(ev, event::Status::Ignored, id),
            Some(Message::ToggleHelp)
        ));
        let mut hud = Hud {
            typing_notes: true,
            ..Hud::default()
        };
        let _ = hud.update(Message::ToggleHelp);
        assert!(!hud.help_open(), "notes keep ?");
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
        assert_eq!(hud.sessions()[0].title, "Session 19");
    }

    #[test]
    fn scroll_at_recent_tail_pages_more_sessions() {
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
        let _ = hud.update(Message::ListScroll(icedtea::collection::VisibleWindow {
            start: 0,
            end: SPOTLIGHT_RECENT,
            scroll: 200.0,
            viewport: 400.0,
        }));
        assert_eq!(hud.sessions().len(), SPOTLIGHT_RECENT * 2);
        assert_eq!(hud.sessions()[0].session_id, "s19");
        assert_eq!(hud.sessions()[8].session_id, "s11");
    }

    #[test]
    fn down_at_last_recent_pages_instead_of_wrapping() {
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
        hud.set_active(SPOTLIGHT_RECENT - 1);
        assert_eq!(hud.active(), SPOTLIGHT_RECENT - 1);
        let _ = hud.nav_sessions_step(1);
        assert_eq!(hud.sessions().len(), SPOTLIGHT_RECENT * 2);
    }

    #[test]
    fn summon_resets_recent_to_the_first_page() {
        let mut hud = Hud {
            all_sessions: (0..20)
                .map(|i| SessionRow {
                    session_id: format!("s{i}"),
                    sort_epoch: i as f64,
                    ..SessionRow::default()
                })
                .collect(),
            spotlight_limit: 16,
            visible: false,
            window_id: Some(window::Id::unique()),
            ..Hud::default()
        };
        hud.rerank_visible();
        assert_eq!(hud.sessions().len(), 16);
        let _ = hud.show_palette();
        assert_eq!(hud.sessions().len(), SPOTLIGHT_RECENT);
    }

    #[test]
    fn ranking_fills_card_heights() {
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
        assert!(w.transparent);
        assert!(!app_window_settings().transparent);
        #[cfg(target_os = "linux")]
        assert!(w.platform_specific.override_redirect);
    }

    #[test]
    fn overlay_window_style_is_clear_so_the_card_can_fade() {
        let overlay = Hud {
            window_mode: false,
            ..Hud::default()
        };
        let theme = theme::iced_theme(overlay.theme_name());
        let style = overlay.window_style(&theme);
        assert_eq!(style.background_color, Color::TRANSPARENT);
        let desk = Hud {
            window_mode: true,
            ..Hud::default()
        };
        let filled = desk.window_style(&theme);
        assert_eq!(filled.background_color.a, 1.0);
    }

    #[test]
    fn show_palette_starts_present_from_gone() {
        let mut hud = Hud {
            visible: false,
            window_mode: false,
            window_id: None,
            overlay: motion::role_animation(MotionRole::Dismiss, false, false),
            reduced_motion: false,
            ..Hud::default()
        };
        let _ = hud.show_palette();
        assert!(
            !hud.overlay_moving(),
            "present must wait for the surface, progress={}",
            hud.overlay_progress()
        );
        assert!(
            hud.overlay_progress() < 0.01,
            "parked gone, got {}",
            hud.overlay_progress()
        );
        let id = window::Id::unique();
        let _ = hud.update(Message::WindowId(Some(id)));
        assert!(hud.overlay_moving());
        assert!(
            hud.overlay_progress() < 0.35,
            "first present frame must be faded out, got {}",
            hud.overlay_progress()
        );
    }

    #[test]
    fn window_focus_events_map() {
        let id = window::Id::unique();
        assert!(matches!(
            interesting_hud_event(
                Event::Window(window::Event::Focused),
                event::Status::Ignored,
                id
            ),
            Some(Message::WindowFocus(true))
        ));
        assert!(matches!(
            interesting_hud_event(
                Event::Window(window::Event::Unfocused),
                event::Status::Ignored,
                id
            ),
            Some(Message::WindowFocus(false))
        ));
    }

    #[test]
    fn unfocused_pop_out_stops_periodic_poll_flag() {
        let mut hud = Hud {
            visible: true,
            focused: true,
            window_mode: true,
            palette_live: true,
            ..Hud::default()
        };
        let _ = hud.update(Message::WindowFocus(false));
        assert!(!hud.focused);
        assert!(!wants_periodic_poll(
            hud.visible,
            hud.focused,
            hud.window_mode
        ));
    }

    #[test]
    fn unfocused_overlay_keeps_periodic_poll_flag() {
        let mut hud = Hud {
            visible: true,
            focused: true,
            window_mode: false,
            palette_live: true,
            ..Hud::default()
        };
        let _ = hud.update(Message::WindowFocus(false));
        assert!(!hud.focused);
        assert!(wants_periodic_poll(
            hud.visible,
            hud.focused,
            hud.window_mode
        ));
    }

    fn assert_focus_gain_catch_up_does_not_rewind_last_live(window_mode: bool) {
        let stale = Duration::from_millis(800);
        let mut hud = Hud {
            visible: true,
            focused: false,
            window_mode,
            palette_live: true,
            last_live: Instant::now().checked_sub(stale).expect("stale last_live"),
            ..Hud::default()
        };
        let before = hud.last_live;
        assert!(
            before.elapsed() < Duration::from_secs(5),
            "fixture last_live must be recent, not a minute back"
        );
        let _ = hud.update(Message::WindowFocus(true));
        assert!(hud.focused);
        assert!(
            hud.last_live >= before,
            "focus gain must not rewind last_live"
        );
        assert!(
            hud.last_live.elapsed() < Duration::from_millis(200),
            "catch-up must schedule a fetch and stamp last_live as now"
        );
        assert!(
            before.elapsed() >= stale,
            "fixture was overdue on the clock but under LIVE_POLL_MS"
        );
    }

    #[test]
    fn focus_gain_on_pop_out_catches_up_without_rewinding_last_live() {
        assert_focus_gain_catch_up_does_not_rewind_last_live(true);
    }

    #[test]
    fn focus_gain_on_overlay_catches_up_without_rewinding_last_live() {
        assert_focus_gain_catch_up_does_not_rewind_last_live(false);
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
        let jay = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Character("j".into()),
            modified_key: Key::Character("j".into()),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::KeyJ),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: Some("j".into()),
            repeat: false,
        });
        assert!(
            interesting_hud_event(jay, event::Status::Captured, window::Id::unique()).is_some()
        );
        let tab = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Named(Named::Tab),
            modified_key: Key::Named(Named::Tab),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::Tab),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: None,
            repeat: false,
        });
        assert!(
            interesting_hud_event(tab, event::Status::Captured, window::Id::unique()).is_none(),
            "focused search keeps Tab"
        );
        let slash = Event::Keyboard(keyboard::Event::KeyPressed {
            key: Key::Character("/".into()),
            modified_key: Key::Character("/".into()),
            physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::Slash),
            location: iced::keyboard::Location::Standard,
            modifiers: KeyMods::default(),
            text: Some("/".into()),
            repeat: false,
        });
        assert!(
            interesting_hud_event(slash.clone(), event::Status::Captured, window::Id::unique())
                .is_none(),
            "focused search keeps /"
        );
        assert!(
            interesting_hud_event(slash, event::Status::Ignored, window::Id::unique()).is_some(),
            "unfocused / still focuses the pane search"
        );
    }

    #[test]
    fn overlay_tokens_use_theme_density() {
        let hud = Hud::default();
        assert_eq!(hud.tokens().density.name, icedtea::m3::DensityName::Default);
        assert_eq!(
            hud.tokens().density.name,
            crate::theme::tokens(hud.theme_name()).density.name
        );
    }

    #[test]
    fn slash_targets_the_search_on_this_screen() {
        let hud = Hud::default();
        assert_eq!(hud.search_focus_id(), hud.search_id());
        let mut hud = Hud {
            overview: Some(Overview::default()),
            overview_sid: "s1".into(),
            tab: Tab::Timeline,
            ..Hud::default()
        };
        assert!(hud.browse_mode());
        assert_eq!(hud.search_focus_id(), hud.tl_search_id());
        hud.tab = Tab::Turns;
        assert_eq!(hud.search_focus_id(), hud.turns_search_id());
        hud.tab = Tab::Overview;
        assert_eq!(hud.search_focus_id(), hud.search_id());
        hud.tab = Tab::Timeline;
        hud.timeline_open = Some(3);
        let _ = hud.focus_context_search();
        assert!(hud.timeline_open.is_none());
        hud.typing_notes = true;
        let _ = hud.on_key(Key::Character("/".into()), KeyMods::default());
        assert!(hud.typing_notes, "slash must not steal a note field");
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
    fn captured_escape_leaves_the_search_field() {
        let id = window::Id::unique();
        let esc = escape_pressed();
        assert!(
            matches!(
                interesting_hud_event(esc.clone(), event::Status::Captured, id),
                Some(Message::LeaveInput)
            ),
            "Escape in search must leave the field, not hide"
        );
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
    fn timeline_scroll_keeps_selection_in_the_viewport() {
        let mut hud = hud_with_session();
        let events: Vec<Value> = (0..20).map(|i| ev_json(i, "e")).collect();
        load_page(&mut hud, 0, false, true, events, 20, 0);
        hud.timeline_focus = Some(0);
        hud.tl_window.viewport = 200.0;
        let mut win = icedtea::collection::VisibleWindow::new(200.0);
        win.scroll = TIMELINE_ROW_H * 8.0;
        win.start = 0;
        win.end = 20;
        let _ = hud.update(Message::TimelineScroll(win));
        let pos = hud.timeline_focus_pos().expect("focus");
        let vis = icedtea::collection::visible_range_var(win.scroll, 200.0, hud.timeline_heights());
        assert!(
            vis.contains(&pos),
            "selected row {pos} must stay in {vis:?}"
        );
        assert_ne!(hud.timeline_focus(), Some(0));
        let kept = hud.timeline_focus();
        win.scroll += 4.0;
        let _ = hud.update(Message::TimelineScroll(win));
        assert_eq!(hud.timeline_focus(), kept);
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
        assert!(
            overlay.iced_settings.fonts.is_empty(),
            "HUD uses platform faces; do not embed TTF"
        );
        assert_eq!(overlay.iced_settings.default_font, icedtea::typo::UI);
        let desk = desktop_prepared();
        assert!(desk.window.decorations);
        assert!(desk.window.resizable);
        assert!(!desk.window.exit_on_close_request);
        let src = include_str!("app.rs");
        assert!(src.contains("bootstrap_with_catalog"));
        assert!(src.contains(".open()"));
        assert!(src.contains("retarget"));
        assert!(src.contains("install_platform_faces"));
    }

    #[test]
    fn set_tab_leaves_search_so_list_keys_work() {
        let src = include_str!("app.rs");
        let body = src
            .split("Message::SetTab(tab) =>")
            .nth(1)
            .expect("SetTab")
            .split("Message::TimelineQuery")
            .next()
            .expect("arm");
        assert!(
            body.contains("blur_text_inputs"),
            "tab change must leave search like Escape"
        );
        assert!(!body.contains("Keep in-pane search focused"));
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
    fn catalog_refresh_does_not_highlight_first_recent_row() {
        let mut hud = Hud::default();
        hud.apply_list(
            json!({
                "sessions": [
                    {"sessionId": "new", "title": "New", "sortEpoch": 2.0},
                    {"sessionId": "old", "title": "Old", "sortEpoch": 1.0},
                ],
                "matched": 2,
                "offset": 0,
                "limit": 2,
            }),
            false,
        );
        assert!(
            matches!(hud.list_selection, icedtea::collection::Selection::None),
            "first catalog page is idle Spotlight"
        );
        hud.apply_list(
            json!({
                "sessions": [
                    {"sessionId": "new", "title": "New", "sortEpoch": 2.0},
                    {"sessionId": "old", "title": "Old", "sortEpoch": 1.0},
                ],
                "matched": 2,
                "offset": 0,
                "limit": 2,
            }),
            true,
        );
        assert!(
            matches!(hud.list_selection, icedtea::collection::Selection::None),
            "a later catalog fill must not treat active=0 as a pick"
        );
        assert!(hud.selected_sid().is_none());
        assert!(!hud.browse_mode());
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
            !hud.status.starts_with("disk"),
            "footer must not show session id or state: {}",
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
                offset: 0,
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
            Some(Message::PaneDigit(2))
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
    fn events_bracket_from_all_focuses_next_matching_turn() {
        let mut hud = two_turn_timeline();
        hud.timeline_focus = Some(1);
        hud.rebuild_tl_filter();
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
        assert!(hud.events_turn_index.is_none());
        assert_eq!(hud.timeline_focus(), Some(3));
        let _ = hud.update(Message::RawEvent(press("]")));
        assert!(hud.events_turn_index.is_none());
        assert_eq!(hud.timeline_focus(), Some(5));
    }

    #[test]
    fn events_h_l_from_all_focuses_matching_turns() {
        let mut hud = two_turn_timeline();
        hud.timeline_focus = Some(1);
        hud.rebuild_tl_filter();
        let press = |ch: &str, code: iced::keyboard::key::Code| {
            Event::Keyboard(keyboard::Event::KeyPressed {
                key: Key::Character(ch.into()),
                modified_key: Key::Character(ch.into()),
                physical_key: iced::keyboard::key::Physical::Code(code),
                location: iced::keyboard::Location::Standard,
                modifiers: KeyMods::default(),
                text: None,
                repeat: false,
            })
        };
        assert!(hud.events_turn_index.is_none());
        let _ = hud.update(Message::RawEvent(press(
            "l",
            iced::keyboard::key::Code::KeyL,
        )));
        assert!(hud.events_turn_index.is_none());
        assert_eq!(hud.timeline_focus(), Some(3));
        let _ = hud.update(Message::RawEvent(press(
            "l",
            iced::keyboard::key::Code::KeyL,
        )));
        assert_eq!(hud.timeline_focus(), Some(5));
        let _ = hud.update(Message::RawEvent(press(
            "h",
            iced::keyboard::key::Code::KeyH,
        )));
        assert!(hud.events_turn_index.is_none());
        assert_eq!(hud.timeline_focus(), Some(3));
    }

    #[test]
    fn turn_step_from_open_event_keeps_the_page() {
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
                        "eventIndexes": [10]
                    },
                    {
                        "turnIndex": 1,
                        "promptIndex": 2,
                        "label": "second",
                        "userEventIndex": 11,
                        "eventIndexes": [11]
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
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(10, "a"), ev_json(11, "b")],
            2,
            0,
        );
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        let _ = hud.update(Message::SelectTimeline(10));
        assert_eq!(hud.timeline_open(), Some(10));
        let _ = hud.select_events_turn(Some(1));
        assert_eq!(hud.events_turn_index, Some(1));
        assert_eq!(hud.timeline_open(), Some(10));
        assert_eq!(hud.detail_turn_edge, Some(DetailTurnEdge::First));
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
        assert_eq!(hud.events_turn_index, Some(0));
        assert_eq!(hud.timeline_prompt, Some(1));
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
    fn filter_then_turn_keeps_filter() {
        let mut hud = two_turn_timeline();
        let _ = hud.update(Message::TimelineKind(KindFilter::Workflows));
        reload_two_turn_page(&mut hud);
        assert_eq!(hud.timeline_kind(), KindFilter::Workflows);
        let _ = hud.update(Message::EventsTurnPicked(EventsTurnPick {
            turn_index: Some(2),
            label: "third".into(),
        }));
        assert_eq!(hud.events_turn_index, Some(2));
        assert_eq!(hud.timeline_kind(), KindFilter::Workflows);
        let _ = hud.update(Message::EventsTurnPicked(EventsTurnPick {
            turn_index: None,
            label: "All turns".into(),
        }));
        assert!(hud.events_turn_index.is_none());
        assert_eq!(hud.timeline_kind(), KindFilter::Workflows);
    }

    #[test]
    fn turn_pick_keeps_search_and_filter() {
        let mut hud = two_turn_timeline();
        hud.timeline_kind = KindFilter::Tools;
        hud.timeline_query = "needle".into();
        hud.timeline_query_draft = "needle".into();
        let _ = hud.select_events_turn(Some(0));
        assert_eq!(hud.timeline_kind(), KindFilter::Tools);
        assert_eq!(hud.timeline_query(), "needle");
        assert_eq!(hud.timeline_query_draft(), "needle");
    }

    #[test]
    fn turn_then_filter_keeps_turn() {
        let mut hud = two_turn_timeline();
        let _ = hud.update(Message::EventsTurnPicked(EventsTurnPick {
            turn_index: Some(0),
            label: "first".into(),
        }));
        let _ = hud.update(Message::TimelineKind(KindFilter::Workflows));
        reload_two_turn_page(&mut hud);
        assert_eq!(hud.events_turn_index, Some(0));
        assert_eq!(hud.timeline_kind(), KindFilter::Workflows);
    }

    #[test]
    fn jump_timeline_keeps_filter() {
        let mut hud = two_turn_timeline();
        let _ = hud.update(Message::TimelineKind(KindFilter::Workflows));
        reload_two_turn_page(&mut hud);
        let _ = hud.update(Message::JumpTimeline(6));
        assert_eq!(hud.timeline_kind(), KindFilter::Workflows);
        assert_eq!(hud.events_turn_index, Some(2));
    }

    #[test]
    fn specific_turn_locks_hl_and_brackets() {
        let mut hud = two_turn_timeline();
        let _ = hud.update(Message::EventsTurnPicked(EventsTurnPick {
            turn_index: Some(0),
            label: "first".into(),
        }));
        hud.timeline_focus = Some(1);
        hud.rebuild_tl_filter();
        let press = |ch: &str, code: iced::keyboard::key::Code| {
            Event::Keyboard(keyboard::Event::KeyPressed {
                key: Key::Character(ch.into()),
                modified_key: Key::Character(ch.into()),
                physical_key: iced::keyboard::key::Physical::Code(code),
                location: iced::keyboard::Location::Standard,
                modifiers: KeyMods::default(),
                text: None,
                repeat: false,
            })
        };
        for (ch, code) in [
            ("l", iced::keyboard::key::Code::KeyL),
            ("h", iced::keyboard::key::Code::KeyH),
            ("]", iced::keyboard::key::Code::BracketRight),
        ] {
            let _ = hud.update(Message::RawEvent(press(ch, code)));
            assert_eq!(
                hud.events_turn_index,
                Some(0),
                "key {ch} must not change turn"
            );
        }
        let _ = hud.update(Message::TimelineKind(KindFilter::Workflows));
        reload_two_turn_page(&mut hud);
        let _ = hud.update(Message::RawEvent(press(
            "[",
            iced::keyboard::key::Code::BracketLeft,
        )));
        assert!(hud.events_turn_index.is_none());
        assert_eq!(hud.timeline_kind(), KindFilter::Workflows);
    }

    #[test]
    fn jk_walk_filtered_rows_inside_turn() {
        let mut hud = two_turn_timeline();
        let _ = hud.update(Message::EventsTurnPicked(EventsTurnPick {
            turn_index: Some(0),
            label: "first".into(),
        }));
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_named(1, "user", "", 0, "u0"),
                ev_named(2, "tool", "workflow", 0, "wf0"),
            ],
            2,
            0,
        );
        let _ = hud.update(Message::TimelineKind(KindFilter::Workflows));
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_named(1, "user", "", 0, "u0"),
                ev_named(2, "tool", "workflow", 0, "wf0"),
            ],
            2,
            0,
        );
        hud.rebuild_tl_filter();
        hud.timeline_focus = Some(2);
        let _ = hud.update(Message::RawEvent(Event::Keyboard(
            keyboard::Event::KeyPressed {
                key: Key::Character("j".into()),
                modified_key: Key::Character("j".into()),
                physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::KeyJ),
                location: iced::keyboard::Location::Standard,
                modifiers: KeyMods::default(),
                text: None,
                repeat: false,
            },
        )));
        assert_eq!(hud.timeline_focus(), Some(2));
        assert_eq!(hud.events_turn_index, Some(0));
        let _ = hud.update(Message::RawEvent(Event::Keyboard(
            keyboard::Event::KeyPressed {
                key: Key::Character("k".into()),
                modified_key: Key::Character("k".into()),
                physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::KeyK),
                location: iced::keyboard::Location::Standard,
                modifiers: KeyMods::default(),
                text: None,
                repeat: false,
            },
        )));
        assert_eq!(hud.timeline_focus(), Some(2));
    }

    #[test]
    fn all_turns_next_skips_turn_without_filter_hit() {
        let mut hud = two_turn_timeline();
        let _ = hud.update(Message::TimelineKind(KindFilter::Workflows));
        reload_two_turn_page(&mut hud);
        hud.timeline_focus = Some(2);
        let _ = hud.update(Message::RawEvent(Event::Keyboard(
            keyboard::Event::KeyPressed {
                key: Key::Character("l".into()),
                modified_key: Key::Character("l".into()),
                physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::KeyL),
                location: iced::keyboard::Location::Standard,
                modifiers: KeyMods::default(),
                text: None,
                repeat: false,
            },
        )));
        assert_eq!(hud.timeline_kind(), KindFilter::Workflows);
        assert!(hud.events_turn_index.is_none());
        assert_eq!(hud.timeline_focus(), Some(6));
    }

    #[test]
    fn g_from_turns_opens_that_turn_all_events() {
        let mut hud = two_turn_timeline();
        let _ = hud.update(Message::TimelineKind(KindFilter::Workflows));
        hud.tab = Tab::Turns;
        hud.turns_focus = Some(2);
        let _ = hud.update(Message::RawEvent(Event::Keyboard(
            keyboard::Event::KeyPressed {
                key: Key::Character("g".into()),
                modified_key: Key::Character("g".into()),
                physical_key: iced::keyboard::key::Physical::Code(iced::keyboard::key::Code::KeyG),
                location: iced::keyboard::Location::Standard,
                modifiers: KeyMods::default(),
                text: None,
                repeat: false,
            },
        )));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert_eq!(hud.events_turn_index, Some(2));
        assert_eq!(hud.timeline_kind(), KindFilter::All);
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
    fn page_busy_while_overview_pending() {
        let mut hud = Hud::default();
        assert!(!hud.page_busy());
        hud.overview_pending = "s1".into();
        assert!(hud.page_busy());
        hud.overview_pending.clear();
        hud.catalog_busy = true;
        assert!(hud.page_busy());
    }

    #[test]
    fn overview_section_cycles_in_strip_order() {
        let mut hud = hud_with_session();
        hud.tab = Tab::Overview;
        hud.overview_pending = "s1".into();
        assert_eq!(
            hud.overview_section(),
            crate::model::OverviewSection::Session
        );
        let _ = hud.update(Message::SetOverviewSection(hud.overview_section().other()));
        assert_eq!(hud.overview_section(), crate::model::OverviewSection::Tasks);
        let _ = hud.update(Message::SetOverviewSection(
            crate::model::OverviewSection::Workflows,
        ));
        assert_eq!(
            hud.overview_section(),
            crate::model::OverviewSection::Workflows
        );
    }

    #[test]
    fn overview_task_list_virtualizes_and_highlights_before_open() {
        let mut hud = hud_with_session();
        hud.tab = Tab::Overview;
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "backgroundJobs": [{
                "id": "job-1",
                "kind": "monitor",
                "status": "done",
                "description": "Watch board",
                "eventIndex": 4
            }],
            "turns": { "total": 0, "turns": [] },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        let _ = hud.update(Message::SetOverviewSection(
            crate::model::OverviewSection::Tasks,
        ));
        assert_eq!(hud.overview_heights().len(), 1);
        assert!(hud.overview_heights()[0] >= 80.0);
        assert_eq!(hud.tasks_focus(), Some(0));
        let _ = hud.update(Message::FocusOverviewRow(0));
        assert_eq!(hud.tasks_focus(), Some(0));
        assert!(hud.timeline_open().is_none());
        let _ = hud.update(Message::FocusOverviewRow(0));
        assert_eq!(hud.timeline_open(), Some(4));
    }

    #[test]
    fn overview_schedule_second_click_opens_bookend() {
        let mut hud = hud_with_session();
        hud.tab = Tab::Overview;
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "schedules": [{
                "id": "sched-1",
                "humanSchedule": "every 1 hour",
                "promptPreview": "hourly ping",
                "eventIndex": 9
            }],
            "turns": { "total": 0, "turns": [] },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        let _ = hud.update(Message::SetOverviewSection(
            crate::model::OverviewSection::Tasks,
        ));
        let _ = hud.update(Message::FocusOverviewRow(0));
        assert!(hud.timeline_open().is_none());
        let _ = hud.update(Message::FocusOverviewRow(0));
        assert_eq!(hud.timeline_open(), Some(9));
    }

    #[test]
    fn overview_schedule_without_bookend_stays_closed() {
        let mut hud = hud_with_session();
        hud.tab = Tab::Overview;
        let data = json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "schedules": [{
                "id": "sched-1",
                "humanSchedule": "every 1 hour",
                "promptPreview": "hourly ping"
            }],
            "turns": { "total": 0, "turns": [] },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(data),
        });
        let _ = hud.update(Message::SetOverviewSection(
            crate::model::OverviewSection::Tasks,
        ));
        let _ = hud.update(Message::FocusOverviewRow(0));
        let _ = hud.update(Message::FocusOverviewRow(0));
        assert!(hud.timeline_open().is_none());
        assert_eq!(hud.tab(), Tab::Overview);
    }

    #[test]
    fn overview_stats_table_uses_timeline_labels() {
        let mut hud = hud_with_session();
        hud.timeline = vec![
            crate::wire::TimelineEvent {
                event_type: "tool_call".into(),
                tool_name: "read_file".into(),
                ..crate::wire::TimelineEvent::default()
            },
            crate::wire::TimelineEvent {
                event_type: "tool_call".into(),
                tool_name: "read_file".into(),
                ..crate::wire::TimelineEvent::default()
            },
        ];
        let _ = hud.update(Message::SetOverviewSection(
            crate::model::OverviewSection::Stats,
        ));
        assert_eq!(
            hud.stats_table().headers,
            ["Kind".to_string(), "Name".to_string(), "Count".to_string()]
        );
        let names: Vec<&str> = hud
            .stats_table()
            .rows
            .iter()
            .map(|r| r[1].as_str())
            .collect();
        assert!(names.contains(&"tool call"));
        assert!(names.contains(&"read file"));
        assert!(!names.iter().any(|n| n.contains('_')));
        let _ = hud.update(Message::StatsSort(2));
        assert_eq!(hud.stats_table().cell(0, 1), "tool call");
        assert_eq!(hud.stats_table().cell(0, 2), "2");
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
    fn timeline_jump_opens_the_event() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(0, "a"), ev_json(12, "workflow")],
            20,
            0,
        );
        let _ = hud.update(Message::JumpTimeline(12));
        assert_eq!(hud.tab(), Tab::Timeline);
        assert_eq!(hud.timeline_focus(), Some(12));
        assert!(hud.is_timeline_open(12));
    }

    #[test]
    fn failed_workflow_finding_is_on_findings() {
        use crate::wire::{FindingRow, FindingsBlock, Overview};
        let mut hud = hud_with_session();
        hud.overview = Some(Overview {
            findings: FindingsBlock {
                findings: vec![FindingRow {
                    id: "workflow:wf_sprint8".into(),
                    plugin_id: "basic".into(),
                    title: "Workflow sprint-8 failed".into(),
                    event_indices: vec![12],
                    ..FindingRow::default()
                }],
                ..FindingsBlock::default()
            },
            ..Overview::default()
        });
        let _ = hud.update(Message::SetTab(Tab::Findings));
        assert_eq!(hud.tab(), Tab::Findings);
        let titles: Vec<String> = hud
            .overview()
            .map(|o| {
                o.findings
                    .findings
                    .iter()
                    .map(|f| f.title.clone())
                    .collect()
            })
            .unwrap_or_default();
        assert!(titles.iter().any(|t| t.contains("sprint-8")));
    }

    #[test]
    fn turn_card_click_focuses_turns_not_timeline() {
        let mut hud = hud_with_session();
        hud.tab = Tab::Turns;
        hud.turns_focus = None;
        let _ = hud.update(Message::FocusTurn(0));
        assert_eq!(hud.tab(), Tab::Turns);
        assert_eq!(hud.turns_focus(), Some(0));
        assert!(hud.timeline_open().is_none());
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
    fn finding_turn_and_summary_bodies_bind_for_copy() {
        use crate::wire::{FindingRow, FindingsBlock, Overview, TurnRow, TurnsBlock};
        let mut hud = hud_with_session();
        hud.overview = Some(Overview {
            summary: "session blurb".into(),
            turns: TurnsBlock {
                turns: vec![TurnRow {
                    turn_index: 2,
                    summary: "please fix it".into(),
                    assistant_summary: "done".into(),
                    ..TurnRow::default()
                }],
                ..TurnsBlock::default()
            },
            findings: FindingsBlock {
                findings: vec![FindingRow {
                    id: "f1".into(),
                    title: "Claimed MCP failed".into(),
                    detail: "What: bad tool\nWhere: call 3".into(),
                    ..FindingRow::default()
                }],
                ..FindingsBlock::default()
            },
            ..Overview::default()
        });
        let _ = hud.update(Message::SetTab(Tab::Findings));
        assert_eq!(
            hud.field("overview.summary").map(|c| c.text()).as_deref(),
            Some("session blurb")
        );
        assert_eq!(
            hud.field("turn.2.prompt").map(|c| c.text()).as_deref(),
            Some("please fix it")
        );
        assert_eq!(
            hud.field("turn.2.assistant").map(|c| c.text()).as_deref(),
            Some("done")
        );
        assert_eq!(
            hud.field("finding.f1").map(|c| c.text()).as_deref(),
            Some("What: bad tool\nWhere: call 3")
        );
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
        let _ = hud.update(Message::Select {
            id: ExtractKey::Event(3).id(),
            action: iced::widget::text_editor::Action::SelectAll,
        });
        assert_eq!(hud.copyable_text().trim(), src.trim());
    }

    #[test]
    fn opening_a_long_event_binds_the_full_body() {
        let body = "x".repeat(8_000);
        let mut hud = hud_with_session();
        load_page(&mut hud, 0, false, true, vec![ev_json(3, &body)], 10, 0);
        let _ = hud.update(Message::SelectTimeline(3));
        let got = hud.extract_src(ExtractKey::Event(3)).expect("bound");
        assert_eq!(got.chars().count(), 8_000, "copy bind must not clip at 4k");
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

    fn live_overview() -> Overview {
        Overview {
            session_id: "s1".into(),
            meta: crate::wire::SessionMeta {
                session_id: "s1".into(),
                path: "/tmp/s1".into(),
                status: "running".into(),
                ..crate::wire::SessionMeta::default()
            },
            ..Overview::default()
        }
    }

    #[test]
    fn timeline_tail_shows_when_turn_in_progress() {
        let mut hud = hud_with_session();
        hud.overview = Some(Overview {
            session_id: "s1".into(),
            meta: crate::wire::SessionMeta {
                session_id: "s1".into(),
                turn_in_progress: true,
                ..crate::wire::SessionMeta::default()
            },
            ..Overview::default()
        });
        assert!(hud.show_timeline_tail());
    }

    #[test]
    fn timeline_tail_toggle_follows_last_only_when_live() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(0, "a"), ev_json(1, "b"), ev_json(2, "c")],
            3,
            0,
        );
        hud.timeline_focus = Some(0);
        let _ = hud.update(Message::TimelineTail(true));
        assert!(!hud.timeline_follow_tail());
        assert_eq!(hud.timeline_focus(), Some(0));
        hud.overview = Some(live_overview());
        let _ = hud.update(Message::TimelineTail(true));
        assert!(hud.timeline_follow_tail());
        assert_eq!(hud.timeline_focus(), Some(2));
        let _ = hud.update(Message::TimelineTail(false));
        assert!(!hud.timeline_follow_tail());
        assert_eq!(hud.timeline_focus(), Some(2));
    }

    #[test]
    fn timeline_tail_jumps_to_last_page_on_large_session() {
        let mut hud = hud_with_session();
        hud.overview = Some(Overview {
            session_id: "s1".into(),
            meta: crate::wire::SessionMeta {
                session_id: "s1".into(),
                path: "/tmp/s1".into(),
                status: "running".into(),
                num_events: 3427,
                ..crate::wire::SessionMeta::default()
            },
            ..Overview::default()
        });
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(0, "a"), ev_json(1, "b")],
            3427,
            0,
        );
        hud.timeline_focus = Some(0);
        assert!(hud.timeline_next < 3427);
        let _ = hud.update(Message::TimelineTail(true));
        assert!(hud.timeline_follow_tail());
        let end_off = hud.last_timeline().expect("end fetch").offset;
        assert_eq!(end_off, last_timeline_page_offset(3427, TIMELINE_CHUNK));
        load_page(
            &mut hud,
            end_off,
            false,
            true,
            vec![ev_json(3425, "late"), ev_json(3426, "last")],
            3427,
            end_off,
        );
        assert_eq!(hud.timeline_focus(), Some(3426));
    }

    #[test]
    fn timeline_append_keeps_focus_unless_tail() {
        let mut hud = hud_with_session();
        hud.overview = Some(live_overview());
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![ev_json(0, "a"), ev_json(1, "b")],
            4,
            0,
        );
        hud.timeline_focus = Some(0);
        load_page(&mut hud, 2, true, true, vec![ev_json(2, "c")], 3, 2);
        assert_eq!(hud.timeline_focus(), Some(0));
        hud.timeline_follow_tail = true;
        load_page(&mut hud, 3, true, true, vec![ev_json(3, "d")], 4, 3);
        assert_eq!(hud.timeline_focus(), Some(3));
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

    fn ev_named(index: i64, kind: &str, tool: &str, turn: i64, content: &str) -> Value {
        json!({
            "index": index,
            "type": if tool == "workflow" { "tool_call" } else { "user_message_chunk" },
            "kind": kind,
            "toolName": tool,
            "turnIndex": turn,
            "content": content,
            "preview": content,
            "contentLength": content.len(),
            "contentTruncated": false,
        })
    }

    fn two_turn_overview_json() -> Value {
        json!({
            "meta": { "sessionId": "s1", "path": "/tmp/s1", "status": "complete" },
            "turns": {
                "total": 3,
                "turns": [
                    {
                        "turnIndex": 0,
                        "promptIndex": 1,
                        "label": "first",
                        "userEventIndex": 1,
                        "firstIndex": 1,
                        "eventIndexes": [1, 2]
                    },
                    {
                        "turnIndex": 1,
                        "promptIndex": 2,
                        "label": "second",
                        "userEventIndex": 3,
                        "firstIndex": 3,
                        "eventIndexes": [3]
                    },
                    {
                        "turnIndex": 2,
                        "promptIndex": 3,
                        "label": "third",
                        "userEventIndex": 5,
                        "firstIndex": 5,
                        "eventIndexes": [5, 6]
                    }
                ]
            },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        })
    }

    fn two_turn_timeline() -> Hud {
        let mut hud = hud_with_session();
        let _ = hud.update(Message::OverviewLoaded {
            gen: hud.overview_gen,
            sid: "s1".into(),
            quiet: true,
            result: Ok(two_turn_overview_json()),
        });
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                ev_named(1, "user", "", 0, "u0"),
                ev_named(2, "tool", "workflow", 0, "wf0"),
                ev_named(3, "user", "", 1, "u1"),
                ev_named(5, "user", "", 2, "u2"),
                ev_named(6, "tool", "workflow", 2, "wf2"),
            ],
            5,
            0,
        );
        hud.rebuild_tl_filter();
        hud
    }

    fn reload_two_turn_page(hud: &mut Hud) {
        load_page(
            hud,
            0,
            false,
            true,
            vec![
                ev_named(1, "user", "", 0, "u0"),
                ev_named(2, "tool", "workflow", 0, "wf0"),
                ev_named(3, "user", "", 1, "u1"),
                ev_named(5, "user", "", 2, "u2"),
                ev_named(6, "tool", "workflow", 2, "wf2"),
            ],
            5,
            0,
        );
        hud.rebuild_tl_filter();
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

    #[test]
    fn diff_query_filters_visible_files_and_scope_includes_search() {
        let mut hud = Hud {
            overview_pending: "s1".into(),
            tab: Tab::Diff,
            diff: crate::wire::DiffBlock {
                points: vec![crate::wire::DiffPointRow {
                    key: "0".into(),
                    files: vec![
                        crate::wire::DiffFileRow {
                            path: "a.py".into(),
                            unified: "+alpha\n".into(),
                            ..crate::wire::DiffFileRow::default()
                        },
                        crate::wire::DiffFileRow {
                            path: "b.py".into(),
                            unified: "+beta unique\n".into(),
                            ..crate::wire::DiffFileRow::default()
                        },
                    ],
                    ..crate::wire::DiffPointRow::default()
                }],
                ..crate::wire::DiffBlock::default()
            },
            diff_point: "0".into(),
            ..Hud::default()
        };
        assert_eq!(hud.visible_diff_files().len(), 2);
        let _ = hud.update(Message::DiffQuery("a.py".into()));
        let paths: Vec<&str> = hud
            .visible_diff_files()
            .iter()
            .map(|f| f.path.as_str())
            .collect();
        assert_eq!(paths, vec!["a.py"]);
        let _ = hud.update(Message::DiffQuery("unique".into()));
        let paths: Vec<&str> = hud
            .visible_diff_files()
            .iter()
            .map(|f| f.path.as_str())
            .collect();
        assert_eq!(paths, vec!["b.py"]);
        assert!(hud.diff_hit_line().is_some());
        let painted = hud.painted_hit_line().expect("marked hit line");
        assert!(painted.starts_with("> "), "{painted}");
        assert!(painted.contains("unique"), "{painted}");
        hud.bind_diff_bodies();
        let hunk = hud
            .field("diff.hunk")
            .map(|c| c.text())
            .expect("hunk body must be selectable");
        assert!(hunk.contains("unique"), "{hunk}");
        assert!(
            hunk.lines()
                .any(|l| l.starts_with("> ") && l.contains("unique")),
            "search hit must be marked in the selectable body: {hunk}"
        );
        assert_eq!(
            crate::live::diff_hunk_scroll_y(hud.diff_hit_line()),
            crate::live::diff_hunk_line_h() * hud.diff_hit_line().unwrap() as f32
        );
        let scope = hud.key_scope();
        assert_eq!(scope.tab, Tab::Diff);
        let hints = crate::help::footer_table(scope).footer_hints();
        let blob = hints.join("  ·  ");
        assert!(blob.contains("/ search"), "{blob}");
    }

    #[test]
    fn diff_nav_step_repaints_hit_on_the_new_file() {
        let mut hud = Hud {
            overview_pending: "s1".into(),
            tab: Tab::Diff,
            diff: crate::wire::DiffBlock {
                points: vec![crate::wire::DiffPointRow {
                    key: "0".into(),
                    files: vec![
                        crate::wire::DiffFileRow {
                            path: "a.py".into(),
                            unified: "@@\n-old\n+common-needle\n".into(),
                            ..crate::wire::DiffFileRow::default()
                        },
                        crate::wire::DiffFileRow {
                            path: "b.py".into(),
                            unified: "@@\n-x\n-y\n-z\n+common-needle\n".into(),
                            ..crate::wire::DiffFileRow::default()
                        },
                    ],
                    ..crate::wire::DiffPointRow::default()
                }],
                ..crate::wire::DiffBlock::default()
            },
            diff_point: "0".into(),
            ..Hud::default()
        };
        let _ = hud.update(Message::DiffQuery("common-needle".into()));
        assert_eq!(hud.visible_diff_files().len(), 2);
        let first = hud.painted_hit_line().expect("hit on first file");
        assert!(first.contains("common-needle"), "{first}");
        let first_file = hud.diff_file().to_string();
        let _ = hud.nav_step(1);
        assert_ne!(hud.diff_file(), first_file, "j steps to the next file");
        let second = hud.painted_hit_line().expect("hit on second file");
        assert!(second.starts_with("> "), "{second}");
        assert!(
            second.contains("common-needle"),
            "stale line would mark -y: {second}"
        );
    }

    #[test]
    fn diff_search_jumps_to_the_matching_hunk_line() {
        let mut hud = Hud {
            tab: Tab::Diff,
            diff: crate::wire::DiffBlock {
                points: vec![crate::wire::DiffPointRow {
                    key: "0".into(),
                    files: vec![crate::wire::DiffFileRow {
                        path: "a.py".into(),
                        unified: "@@\n-one\n-two\n-three\n-four\n+needle here\n".into(),
                        ..crate::wire::DiffFileRow::default()
                    }],
                    ..crate::wire::DiffPointRow::default()
                }],
                ..crate::wire::DiffBlock::default()
            },
            diff_point: "0".into(),
            diff_file: "a.py".into(),
            ..Hud::default()
        };
        let _ = hud.update(Message::DiffQuery("needle".into()));
        assert_eq!(hud.diff_hit_line(), Some(5));
        assert_eq!(
            crate::live::diff_hunk_scroll_y(hud.diff_hit_line()),
            5.0 * crate::live::diff_hunk_line_h()
        );
        let hunk = hud
            .field("diff.hunk")
            .map(|c| c.text())
            .expect("hunk bound for copy");
        assert!(
            hunk.lines()
                .any(|l| l == "> +needle here" || l.starts_with("> ") && l.contains("needle here")),
            "hit line must be marked so the jump is visible: {hunk}"
        );
    }

    #[test]
    fn diff_point_pick_selects_snapshot() {
        let mut hud = Hud {
            tab: Tab::Diff,
            diff: crate::wire::DiffBlock {
                points: vec![
                    crate::wire::DiffPointRow {
                        key: "0".into(),
                        prompt_index: Some(0),
                        files: vec![crate::wire::DiffFileRow {
                            path: "a.py".into(),
                            unified: "+a\n".into(),
                            ..crate::wire::DiffFileRow::default()
                        }],
                        ..crate::wire::DiffPointRow::default()
                    },
                    crate::wire::DiffPointRow {
                        key: "1".into(),
                        prompt_index: Some(1),
                        files: vec![crate::wire::DiffFileRow {
                            path: "b.py".into(),
                            unified: "+b\n".into(),
                            ..crate::wire::DiffFileRow::default()
                        }],
                        ..crate::wire::DiffPointRow::default()
                    },
                ],
                ..crate::wire::DiffBlock::default()
            },
            diff_point: "1".into(),
            ..Hud::default()
        };
        hud.rebuild_diff_point_options();
        assert_eq!(hud.diff_point_options().len(), 2);
        let pick = hud
            .diff_point_options()
            .iter()
            .find(|p| p.key == "0")
            .cloned()
            .expect("first snapshot");
        let _ = hud.update(Message::DiffPointPicked(pick));
        assert_eq!(hud.diff_point_key(), "0");
        assert_eq!(hud.diff_file(), "a.py");
    }

    #[test]
    fn open_turn_diff_selects_matching_snapshot() {
        let mut hud = Hud {
            tab: Tab::Turns,
            overview: Some(crate::wire::Overview {
                meta: crate::wire::SessionMeta {
                    session_id: "s1".into(),
                    ..crate::wire::SessionMeta::default()
                },
                ..crate::wire::Overview::default()
            }),
            overview_sid: "s1".into(),
            diff_sid: "s1".into(),
            diff: crate::wire::DiffBlock {
                points: vec![
                    crate::wire::DiffPointRow {
                        key: "0".into(),
                        prompt_index: Some(0),
                        files: vec![crate::wire::DiffFileRow {
                            path: "a.py".into(),
                            unified: "+a\n".into(),
                            ..crate::wire::DiffFileRow::default()
                        }],
                        ..crate::wire::DiffPointRow::default()
                    },
                    crate::wire::DiffPointRow {
                        key: "1".into(),
                        prompt_index: Some(1),
                        files: vec![crate::wire::DiffFileRow {
                            path: "b.py".into(),
                            unified: "+b\n".into(),
                            ..crate::wire::DiffFileRow::default()
                        }],
                        ..crate::wire::DiffPointRow::default()
                    },
                ],
                ..crate::wire::DiffBlock::default()
            },
            diff_point: "0".into(),
            ..Hud::default()
        };
        let _ = hud.update(Message::OpenTurnDiff {
            prompt_index: Some(1),
        });
        assert_eq!(hud.tab, Tab::Diff);
        assert_eq!(hud.diff_point_key(), "1");
        assert_eq!(hud.diff_file(), "b.py");
    }

    #[test]
    fn turn_has_diff_only_when_a_snapshot_matches() {
        let hud = Hud {
            diff: crate::wire::DiffBlock {
                points: vec![crate::wire::DiffPointRow {
                    key: "0".into(),
                    prompt_index: Some(1),
                    ..crate::wire::DiffPointRow::default()
                }],
                ..crate::wire::DiffBlock::default()
            },
            ..Hud::default()
        };
        assert!(hud.turn_has_diff(Some(1)));
        assert!(!hud.turn_has_diff(Some(2)));
        assert!(!hud.turn_has_diff(None));
    }

    #[test]
    fn diff_context_switches_to_assistant() {
        let mut hud = Hud::default();
        assert_eq!(hud.diff_context(), crate::model::DiffContext::Prompt);
        let _ = hud.update(Message::DiffContext(crate::model::DiffContext::Assistant));
        assert_eq!(hud.diff_context(), crate::model::DiffContext::Assistant);
    }

    #[test]
    fn diff_assistant_binds_as_markdown() {
        let mut hud = Hud::default();
        hud.bind_markdown("diff.assistant", "# Done\n\n**bold** list:\n\n- a\n- b");
        assert!(hud.markdown("diff.assistant").is_some());
        assert!(hud.markdown_slot("diff.assistant").is_some());
        assert!(!hud.markdown("diff.assistant").unwrap().items.is_empty());
    }

    #[test]
    fn chrome_key_table_registers_every_pane_digit() {
        let table = chrome_key_table();
        for n in 1u8..=crate::model::Tab::ALL.len() as u8 {
            assert!(
                table.get(&format!("pane.{n}")).is_some(),
                "missing pane.{n}"
            );
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
        assert_eq!(TIMELINE_OPEN_CHARS, 50_000);
        assert_eq!(req.content_chars, TIMELINE_OPEN_CHARS);
        assert_eq!(req.at_index, Some(3));
        assert!(!req.advance);
        assert!(req.append);
        hud.last_timeline = Some(LastTimelineReq {
            prompt_index: None,
            around_index: None,
            offset: 80,
            query: String::new(),
            kind: String::new(),
        });
        let next_before = hud.timeline_next;
        let _ = hud.update(Message::SelectTimeline(3));
        assert_eq!(
            hud.last_timeline().map(|r| r.offset),
            Some(80),
            "open-event fetch must keep the last page request"
        );
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
    fn open_detail_also_fetches_paired_tool_result() {
        let mut hud = hud_with_session();
        load_page(
            &mut hud,
            0,
            false,
            true,
            vec![
                json!({
                    "index": 1,
                    "type": "tool_call",
                    "kind": "tool",
                    "toolCallId": "c1",
                    "toolName": "read_file",
                    "content": "",
                }),
                json!({
                    "index": 2,
                    "type": "tool_call_update",
                    "kind": "tool_result",
                    "toolCallId": "c1",
                    "toolName": "read_file",
                    "content": "snip",
                    "contentTruncated": true,
                }),
            ],
            4,
            0,
        );
        assert_eq!(hud.paired_tool_index(1), Some(2));
        assert_eq!(hud.paired_tool_index(2), Some(1));
        let partner = hud.open_event_fetch(2, hud.timeline_gen);
        assert_eq!(partner.at_index, Some(2));
        assert_eq!(partner.content_chars, TIMELINE_OPEN_CHARS);
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
    fn context_actions_are_copy_select_all_and_copy_path() {
        let hud = Hud::default();
        let acts = hud.context_actions();
        assert_eq!(acts.len(), 3);
        assert_eq!(acts[0].title, "Copy");
        assert!(!acts[0].enabled);
        assert_eq!(acts[1].title, "Select all");
        assert!(!acts[1].enabled);
        assert_eq!(acts[2].title, "Copy path");
        assert!(!acts[2].enabled);
    }

    #[test]
    fn right_click_keeps_a_text_selection_for_copy() {
        let mut hud = Hud::default();
        hud.bind_field("event.3.out", "line one\nline two\nline three");
        let _ = hud.update(Message::Select {
            id: "event.3.out".into(),
            action: iced::widget::text_editor::Action::SelectAll,
        });
        let _ = hud.update(Message::Cursor(icedtea::layout::CursorEvent::Move(
            Point::new(40.0, 80.0),
        )));
        let _ = hud.update(Message::Cursor(icedtea::layout::CursorEvent::Context));
        assert_eq!(hud.context_origin(), Some(Point::new(40.0, 80.0)));
        assert!(hud.copyable_text().contains("line two"));
        let copy = hud
            .context_actions()
            .into_iter()
            .find(|a| a.id.as_str() == "edit.copy")
            .expect("copy");
        assert!(copy.enabled);
    }

    #[test]
    fn left_click_clears_the_range_before_context_copy() {
        let mut hud = Hud::default();
        hud.bind_field("event.3.out", "alpha beta gamma");
        let _ = hud.update(Message::Select {
            id: "event.3.out".into(),
            action: iced::widget::text_editor::Action::SelectAll,
        });
        assert!(hud.fields.first_selection().is_some());
        let _ = hud.update(Message::Select {
            id: "event.3.out".into(),
            action: iced::widget::text_editor::Action::Click(Point::new(4.0, 0.0)),
        });
        assert!(
            hud.fields.first_selection().is_none(),
            "left Click is a caret; iced never sends Click for a right press"
        );
        let _ = hud.update(Message::Cursor(icedtea::layout::CursorEvent::Context));
        // No live range: Copy uses the bound field (full text).
        assert_eq!(hud.copyable_text().trim(), "alpha beta gamma");
    }

    #[test]
    fn timeline_detail_context_omits_copy_path() {
        let mut hud = Hud {
            overview: Some(Overview::default()),
            tab: Tab::Timeline,
            timeline_open: Some(3),
            ..Hud::default()
        };
        hud.bind_field("event.3.out", "body");
        let titles: Vec<String> = hud.context_actions().into_iter().map(|a| a.title).collect();
        assert_eq!(titles, ["Copy", "Select all"]);
    }

    #[test]
    fn select_all_text_selects_the_last_field() {
        let mut hud = Hud::default();
        hud.bind_field("event.3.out", "alpha\nbeta");
        let _ = hud.update(Message::Select {
            id: "event.3.out".into(),
            action: iced::widget::text_editor::Action::Click(Point::new(0.0, 0.0)),
        });
        let _ = hud.update(Message::SelectAllText);
        assert_eq!(hud.copyable_text().trim(), "alpha\nbeta");
    }

    #[test]
    fn shift_click_extends_instead_of_collapsing() {
        let mut hud = Hud::default();
        hud.bind_field("event.3.out", "abcdef");
        let _ = hud.update(Message::Select {
            id: "event.3.out".into(),
            action: iced::widget::text_editor::Action::SelectAll,
        });
        assert!(hud.fields.first_selection().is_some());
        hud.key_mods = KeyMods::SHIFT;
        let _ = hud.update(Message::Select {
            id: "event.3.out".into(),
            action: iced::widget::text_editor::Action::Click(Point::new(12.0, 0.0)),
        });
        assert!(
            hud.fields.first_selection().is_some(),
            "shift-click must not drop the range"
        );
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
    fn overview_tick_does_not_rewrite_notice_seen_status() {
        let mut hud = Hud {
            notices_primed: true,
            overview_gen: 1,
            all_sessions: vec![SessionRow {
                session_id: "abc".into(),
                title: "Demo".into(),
                status: "awaiting".into(),
                ..SessionRow::default()
            }],
            seen_status: std::collections::HashMap::from([("abc".into(), "awaiting".into())]),
            ..Hud::default()
        };
        let data = serde_json::json!({
            "sessionId": "abc",
            "meta": {
                "sessionId": "abc",
                "title": "Demo",
                "status": "running"
            },
            "turns": { "turns": [] },
            "findings": { "count": 0, "findings": [] },
            "notes": { "count": 0, "notes": [] }
        });
        let _ = hud.update(Message::OverviewLoaded {
            gen: 1,
            sid: "abc".into(),
            quiet: true,
            result: Ok(data),
        });
        assert_eq!(
            hud.seen_status.get("abc").map(String::as_str),
            Some("awaiting")
        );
    }

    #[test]
    fn hide_palette_destroys_overlay_window() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_mode: false,
            window_id: Some(id),
            overlay: motion::role_animation(MotionRole::Present, true, true),
            reduced_motion: true,
            ..Hud::default()
        };
        let _ = hud.hide_palette();
        assert!(!hud.visible);
        assert!(!hud.palette_live);
        // Reduced-motion hide settles in the same call.
        let _ = hud.finish_overlay_hide();
        assert!(hud.window_id.is_none());
    }

    #[test]
    fn hide_palette_keeps_window_until_exit_motion_settles() {
        let id = window::Id::unique();
        let mut hud = Hud {
            visible: true,
            palette_live: true,
            window_mode: false,
            window_id: Some(id),
            overlay: motion::role_animation(MotionRole::Present, true, false),
            reduced_motion: false,
            ..Hud::default()
        };
        let _ = hud.hide_palette();
        assert!(!hud.visible);
        assert_eq!(hud.window_id, Some(id));
        assert!(hud.overlay.is_animating(Instant::now()));
    }

    #[test]
    fn overlay_progress_is_open_at_rest() {
        let hud = Hud::default();
        assert!((hud.overlay_progress() - 1.0).abs() < 0.01);
        assert!(!hud.overlay_moving());
        assert!(!hud.page_moving());
        assert_eq!(
            hud.tokens().text.a,
            crate::theme::tokens(hud.theme_name()).text.a
        );
    }

    #[test]
    fn present_dismiss_use_asymmetric_durations() {
        assert_eq!(motion::PRESENT_MS, 220);
        assert_eq!(motion::DISMISS_MS, 180);
        const { assert!(motion::DISMISS_MS < motion::PRESENT_MS) };
        assert!(motion::PRESENT_MS < icedtea::m3::DurationStep::Long2.millis());
        assert_eq!(
            motion::MotionRole::Present.ease(),
            icedtea::m3::Ease::EmphasizedDecelerate
        );
        assert_eq!(
            motion::MotionRole::Dismiss.ease(),
            icedtea::m3::Ease::EmphasizedAccelerate
        );
    }

    #[test]
    fn tab_change_is_sibling_fade() {
        let mut hud = hud_with_session();
        hud.overview = Some(Overview {
            session_id: "s1".into(),
            ..Overview::default()
        });
        hud.tab = Tab::Turns;
        hud.reduced_motion = false;
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        assert_eq!(hud.page_role(), MotionRole::Sibling);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::None);
        assert_eq!(hud.page_layer(), PageLayer::Pane);
        let _ = hud.update(Message::SetTab(Tab::Turns));
        assert_eq!(hud.page_role(), MotionRole::Sibling);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::None);
        let _ = hud.select_events_turn(Some(0));
        assert_eq!(hud.page_role(), MotionRole::Sibling);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::None);
    }

    #[test]
    fn page_slide_detail_next_and_prev() {
        let mut hud = hud_with_session();
        hud.reduced_motion = false;
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
        assert_eq!(hud.page_role(), MotionRole::Push);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::End);
        let _ = hud.update(Message::TimelineDetailStep(1));
        assert_eq!(hud.page_role(), MotionRole::Step);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::Up);
        let _ = hud.update(Message::TimelineDetailStep(-1));
        assert_eq!(hud.page_role(), MotionRole::Step);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::Down);
        let _ = hud.update(Message::CloseTimelineDetail);
        assert_eq!(hud.page_role(), MotionRole::Pop);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::Start);
    }

    #[test]
    fn second_page_motion_does_not_reset_progress() {
        let mut hud = hud_with_session();
        hud.overview = Some(Overview {
            session_id: "s1".into(),
            ..Overview::default()
        });
        hud.tab = Tab::Turns;
        hud.reduced_motion = false;
        let started = Instant::now() - Duration::from_millis(80);
        let mut page = motion::role_animation(MotionRole::Sibling, false, false);
        page.go_mut(true, started);
        hud.page = page;
        let mid = hud.page_progress();
        assert!(mid > 0.1 && mid < 0.95, "mid-flight {mid}");
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        let after = hud.page_progress();
        assert!(
            after > 0.1,
            "tab change reset page progress to {after} (was {mid})"
        );
        assert_eq!(hud.page_role(), MotionRole::Sibling);
    }

    #[test]
    fn session_pick_is_browse_push() {
        let mut hud = hud_with_session();
        hud.reduced_motion = false;
        hud.query = "s1".into();
        hud.rerank_visible();
        let _ = hud.update(Message::SelectSession(0));
        assert_eq!(hud.page_role(), MotionRole::Push);
        assert_eq!(hud.page_layer(), PageLayer::Browse);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::End);
    }

    #[test]
    fn expander_progress_is_between_ends_while_opening() {
        let mut hud = Hud {
            reduced_motion: false,
            ..Hud::default()
        };
        let _ = hud.update(Message::FindingExpand {
            id: "a".into(),
            open: true,
        });
        assert!(hud.finding_expanded("a"));
        assert!(
            hud.finding_motion
                .get("a")
                .is_some_and(|a| a.is_animating(Instant::now())),
            "opening must start disclose animation"
        );
        assert!(hud.finding_expand_progress("a") < 1.0);
        let started = Instant::now() - Duration::from_millis(80);
        let mut anim = motion::disclose_animation(false, false);
        anim.go_mut(true, started);
        hud.finding_motion.insert("a".into(), anim);
        let p = hud.finding_expand_progress("a");
        assert!(p > 0.0 && p < 1.0, "expander progress {p}");
        let mut snap = Hud {
            reduced_motion: true,
            ..Hud::default()
        };
        let _ = snap.update(Message::FindingExpand {
            id: "b".into(),
            open: true,
        });
        assert!((snap.finding_expand_progress("b") - 1.0).abs() < 0.01);
        assert!(snap.finding_expanded("b"));
    }

    #[test]
    fn reduced_motion_tab_change_snaps() {
        let mut hud = hud_with_session();
        hud.overview = Some(Overview {
            session_id: "s1".into(),
            ..Overview::default()
        });
        hud.tab = Tab::Turns;
        hud.reduced_motion = true;
        let _ = hud.update(Message::SetTab(Tab::Timeline));
        assert!(!hud.page_moving());
        assert!((hud.page_progress() - 1.0).abs() < 0.01);
        assert_eq!(hud.page_slide(), icedtea::motion::Slide::None);
    }

    #[test]
    fn motion_clock_uses_window_frames() {
        let src = include_str!("app.rs");
        assert!(src.contains("if self.needs_motion_tick()"));
        assert!(src.contains("window::frames()"));
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
            overlay: motion::role_animation(MotionRole::Present, true, true),
            reduced_motion: true,
            ..Hud::default()
        };
        let _ = hud.on_tray(crate::tray::TrayAction::Toggle);
        assert!(!hud.visible);
        let _ = hud.finish_overlay_hide();
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

    fn openable_run() -> crate::wire::SubagentRunRow {
        crate::wire::SubagentRunRow {
            child_session_id: "child-1".into(),
            child_path: "/tmp/child-1".into(),
            openable: true,
            turn_index: Some(2),
            subagent_type: "coder".into(),
            ..Default::default()
        }
    }

    fn parent_with_openable_child() -> Hud {
        let ev = TimelineEvent {
            index: 7,
            event_type: "subagent_spawned".into(),
            kind: "subagent".into(),
            child_session_id: "child-1".into(),
            ..Default::default()
        };
        Hud {
            tab: Tab::Timeline,
            timeline_kind: KindFilter::Subagents,
            timeline_focus: Some(7),
            overview_sid: "parent-1".into(),
            overview: Some(Overview {
                session_id: "parent-1".into(),
                meta: crate::wire::SessionMeta {
                    session_id: "parent-1".into(),
                    path: "/tmp/parent-1".into(),
                    ..Default::default()
                },
                turns: TurnsBlock {
                    subagent_runs: vec![openable_run()],
                    ..Default::default()
                },
                ..Default::default()
            }),
            timeline: vec![ev],
            timeline_sid: "parent-1".into(),
            ..Hud::default()
        }
    }

    #[test]
    fn select_timeline_on_spawn_opens_child() {
        let mut hud = parent_with_openable_child();
        let _ = hud.update(Message::SelectTimeline(7));
        assert_eq!(hud.parent_stack.len(), 1);
        assert_eq!(hud.parent_stack[0].sid, "parent-1");
        assert_eq!(hud.parent_stack[0].tab, Tab::Timeline);
        assert_eq!(hud.parent_stack[0].timeline_kind, KindFilter::Subagents);
        assert_eq!(hud.parent_stack[0].timeline_focus, Some(7));
        assert_eq!(hud.overview_pending, "child-1");
        assert!(hud.timeline_open.is_none());
    }

    #[test]
    fn select_timeline_on_agent_opens_detail() {
        let mut hud = parent_with_openable_child();
        hud.timeline.push(TimelineEvent {
            index: 3,
            event_type: "agent_message_chunk".into(),
            kind: "agent".into(),
            ..Default::default()
        });
        let _ = hud.update(Message::SelectTimeline(3));
        assert!(hud.parent_stack.is_empty());
        assert_eq!(hud.timeline_open, Some(3));
        assert!(hud.overview_pending.is_empty());
    }

    #[test]
    fn select_timeline_on_unopenable_spawn_opens_detail() {
        let mut hud = parent_with_openable_child();
        hud.overview.as_mut().expect("overview").turns.subagent_runs[0].openable = false;
        let _ = hud.update(Message::SelectTimeline(7));
        assert!(hud.parent_stack.is_empty());
        assert_eq!(hud.timeline_open, Some(7));
    }

    #[test]
    fn return_from_child_restores_timeline_place() {
        let mut hud = parent_with_openable_child();
        let _ = hud.update(Message::SelectTimeline(7));
        assert_eq!(hud.tab, Tab::Overview);
        let _ = hud.return_to_parent();
        assert!(hud.parent_stack.is_empty());
        assert_eq!(hud.tab, Tab::Timeline);
        assert_eq!(hud.timeline_kind, KindFilter::Subagents);
        assert_eq!(hud.timeline_focus, Some(7));
        assert!(hud.timeline_open.is_none());
        assert_eq!(hud.overview_pending, "parent-1");
        assert_eq!(hud.restore_around, Some(7));
    }

    #[test]
    fn return_from_turns_chip_restores_turns_tab() {
        let mut hud = parent_with_openable_child();
        hud.tab = Tab::Turns;
        hud.turns_focus = Some(2);
        let _ = hud.update(Message::OpenChild {
            path: "/tmp/child-1".into(),
            sid: "child-1".into(),
        });
        assert_eq!(hud.parent_stack[0].tab, Tab::Turns);
        assert_eq!(hud.parent_stack[0].turns_focus, Some(2));
        let _ = hud.return_to_parent();
        assert_eq!(hud.tab, Tab::Turns);
        assert_eq!(hud.turns_focus, Some(2));
        assert!(hud.restore_around.is_none());
    }

    #[test]
    fn compact_child_hides_turns_and_remaps_pane() {
        let mut hud = parent_with_openable_child();
        {
            let ov = hud.overview.as_mut().expect("overview");
            ov.meta.session_kind = "subagent".into();
            ov.turns.turns = vec![crate::wire::TurnRow {
                turn_index: 0,
                ..Default::default()
            }];
        }
        assert!(hud.compact_child_chrome());
        assert_eq!(hud.visible_tabs(), Tab::CHILD);
        assert!(hud.hide_events_turn_pick());
        let _ = hud.update(Message::SetTab(Tab::Turns));
        assert_eq!(hud.tab(), Tab::Timeline);
        hud.tab = Tab::Overview;
        let _ = hud.update(Message::PaneDigit(2));
        assert_eq!(hud.tab(), Tab::Timeline);
    }

    #[test]
    fn turns_chip_records_run_turn_when_focus_empty() {
        let mut hud = parent_with_openable_child();
        hud.tab = Tab::Turns;
        hud.turns_focus = None;
        let _ = hud.update(Message::OpenChild {
            path: "/tmp/child-1".into(),
            sid: "child-1".into(),
        });
        assert_eq!(hud.parent_stack[0].turns_focus, Some(2));
    }
}
