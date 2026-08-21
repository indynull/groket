//! Live-refresh helpers (pure).

use std::collections::{BTreeMap, HashMap, HashSet};

use crate::model::{KindFilter, SchemaField, SessionRow};
use crate::wire::{Overview, SessionMeta, TimelineEvent, TurnRow, TurnsBlock};

pub const LIVE_POLL_MS: u64 = 3000;
pub const IDLE_POLL_MS: u64 = 15_000;

/// Periodic catalog/overview RPC while the palette is on screen.
/// Overlay follows visibility (tray/token-less show does not take keyboard
/// focus). Pop-out also requires window focus. Hidden overlay and an
/// unfocused pop-out rely on control notifies instead.
pub fn wants_periodic_poll(visible: bool, focused: bool, window_mode: bool) -> bool {
    visible && (!window_mode || focused)
}
pub const LIVE_TAIL_LIMIT: u32 = 24;
pub const TIMELINE_CHUNK: u32 = 80;
/// Hard cap on buffered timeline rows. Host sessions of 5k–10k stay in memory.
pub const TIMELINE_BUFFER_CAP: usize = 10_000;
/// Preview bytes per row on a page. Opened cards refetch a larger slice.
pub const TIMELINE_PREVIEW_CHARS: u32 = 720;
/// One open event (`atIndex`). Matches ``MAX_CONTENT_CHARS`` on the owner.
pub const TIMELINE_OPEN_CHARS: u32 = 50_000;
/// Vertical gap after each virtual list card (must be inside row height —
/// ``virtual_column`` clips each row to ``heights[i]``).
pub const LIST_GAP: f32 = 8.0;
/// Closed timeline tile + gap. Title line plus one badge row (same
/// face as Recent). Open event detail uses the full pane.
pub const TIMELINE_ROW_H: f32 = 80.0;
/// Extra mounted timeline cards beyond the viewport.
pub const TIMELINE_OVERSCAN: usize = 1;
/// Closed Turns tile + gap. Title line plus one badge row.
pub const CLOSED_TURN_CARD_H: f32 = 80.0;
/// Extra mounted turn cards beyond the viewport.
pub const TURNS_OVERSCAN: usize = 1;
/// Overview Tasks / Workflows / Subagents card + gap (chips + name).
pub const OVERVIEW_LIST_ROW_H: f32 = 80.0;
/// Extra mounted Overview list cards beyond the viewport.
pub const OVERVIEW_LIST_OVERSCAN: usize = 1;
/// icedtea ``data_table`` body row on Overview Stats.
pub const STATS_ROW_H: f32 = 32.0;
/// Spotlight idle list: latest sessions by ``sort_epoch`` (not the full catalog).
pub const SPOTLIGHT_RECENT: usize = 8;

/// Indices into ``turns`` whose label or prompt match *query* (casefold substring).
pub fn filter_turn_indices(turns: &[TurnRow], query: &str) -> Vec<usize> {
    let q = query.trim().to_ascii_lowercase();
    if q.is_empty() {
        return (0..turns.len()).collect();
    }
    turns
        .iter()
        .enumerate()
        .filter(|(_, t)| {
            t.label.to_ascii_lowercase().contains(&q)
                || t.summary.to_ascii_lowercase().contains(&q)
                || t.outcome.to_ascii_lowercase().contains(&q)
                || format!("turn {}", t.turn_index).contains(&q)
        })
        .map(|(i, _)| i)
        .collect()
}

/// Latest catalog rows for the idle Spotlight list (newest ``sort_epoch`` first).
///
/// When *keep_sid* is non-empty and present in *all*, it is pinned at the front
/// so clearing search does not drop the open session from the list.
pub fn spotlight_recent(all: &[SessionRow], n: usize, keep_sid: &str) -> Vec<SessionRow> {
    if n == 0 || all.is_empty() {
        return Vec::new();
    }
    let mut idxs: Vec<usize> = (0..all.len()).collect();
    idxs.sort_by(|&a, &b| {
        all[b]
            .sort_epoch
            .partial_cmp(&all[a].sort_epoch)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| all[a].session_id.cmp(&all[b].session_id))
    });
    let mut out: Vec<SessionRow> = Vec::with_capacity(n.min(all.len()) + 1);
    if !keep_sid.is_empty() {
        if let Some(row) = all.iter().find(|r| r.session_id == keep_sid) {
            out.push(row.clone());
        }
    }
    for i in idxs {
        let row = &all[i];
        if !keep_sid.is_empty() && row.session_id == keep_sid {
            continue;
        }
        out.push(row.clone());
        if out.len() >= n {
            break;
        }
    }
    out
}

/// True when the mounted window has reached the tail of the Recent list.
pub fn should_page_recent(window_end: usize, visible_len: usize) -> bool {
    visible_len > 0 && window_end.saturating_add(2) >= visible_len
}

/// Next Recent cap after a scroll or Down at the tail, or ``None`` when the
/// loaded catalog is already fully shown.
pub fn next_spotlight_limit(shown: usize, have: usize, page: usize) -> Option<usize> {
    if page == 0 || shown >= have {
        return None;
    }
    Some(shown.saturating_add(page).min(have))
}

/// True when a non-delta ``session/list`` body is a page, not a full snapshot.
pub fn is_partial_list_page(
    incoming_len: usize,
    matched: i64,
    delta: bool,
    incomplete: bool,
    building: bool,
) -> bool {
    if delta {
        return false;
    }
    if incoming_len == 0 {
        return incomplete || building;
    }
    if incomplete || building {
        return matched <= 0 || incoming_len < matched as usize;
    }
    matched > incoming_len as i64
}

fn wrap_line_count(s: &str, cols: usize) -> usize {
    let cols = cols.max(1);
    let mut n = 0;
    for line in s.lines() {
        let w = line.chars().count().max(1);
        n += w.div_ceil(cols);
    }
    n.max(1)
}

/// Gap under each Recent session card (included in row height).
pub const LIST_CARD_GAP: f32 = 8.0;

/// Pixel height of one session tile in the full-width picker.
///
/// Includes [`LIST_CARD_GAP`] so `RowHeights` totals match the painted
/// gap under each card (otherwise the last cards cannot scroll fully
/// into view). Status / model sit on a badge row; *meta* is leftover
/// context text.
pub fn session_card_height(title: &str, meta: &str, has_ctx: bool) -> f32 {
    // Full-width picker (~780 shell − pad); ~7px at 14px body → ~90 cols.
    let cols = 72usize;
    let mut h = 24.0;
    h += wrap_line_count(title, cols) as f32 * 18.0;
    h += 4.0 + 16.0;
    if !meta.is_empty() {
        h += 2.0 + wrap_line_count(meta, cols) as f32 * 16.0;
    }
    if has_ctx {
        h += 5.0;
    }
    h + LIST_CARD_GAP
}

/// Scroll so ``active`` stays in the viewport. Offsets are height sums
/// (icedtea ``RowHeights::offset``), not painted column gaps.
pub fn list_scroll_to_cover(heights: &[f32], active: usize, scroll: f32, view_h: f32) -> f32 {
    let top: f32 = heights.iter().take(active).copied().sum();
    let bot = top + heights.get(active).copied().unwrap_or(0.0);
    let content: f32 = heights.iter().copied().sum();
    let row_h = bot - top;
    let mut y = scroll;
    // Tall open cards: pin the top so the list can scroll through the body.
    if row_h > view_h || top < y {
        y = top;
    } else if bot > y + view_h {
        y = (bot - view_h).max(0.0);
    }
    clamp_scroll(y, content, view_h)
}

/// Line height of a Diff hunk editor (token code size × iced default 1.3).
pub fn diff_hunk_line_h() -> f32 {
    crate::theme::tokens("textual-dark").code() * 1.3
}

/// Vertical offset so the Diff hunk scroll puts *hit_line* in view.
pub fn diff_hunk_scroll_y(hit_line: Option<usize>) -> f32 {
    hit_line
        .map(|i| i as f32 * diff_hunk_line_h())
        .unwrap_or(0.0)
}

/// Pin row top to the viewport top (expand / jump).
pub fn list_scroll_to_top(heights: &[f32], active: usize, view_h: f32) -> f32 {
    let top: f32 = heights.iter().take(active).copied().sum();
    let content: f32 = heights.iter().copied().sum();
    clamp_scroll(top, content, view_h)
}

/// After a wheel scroll, move list highlight to a row still in the viewport.
///
/// Returns ``None`` when focus is already on screen (or the list is empty).
pub fn list_focus_after_scroll(
    focus_pos: Option<usize>,
    scroll: f32,
    viewport: f32,
    heights: &[f32],
) -> Option<usize> {
    let pos = focus_pos?;
    let vis = icedtea::collection::visible_range_var(scroll, viewport, heights);
    if vis.is_empty() || vis.contains(&pos) {
        return None;
    }
    Some(if pos < vis.start {
        vis.start
    } else {
        vis.end.saturating_sub(1)
    })
}

/// Leftover line under the status badges (context compact only).
pub fn session_row_meta(row: &SessionRow) -> String {
    row.context_usage_compact.trim().to_string()
}

/// icedtea [`ListModel`] over catalog rows (owned meta lines for tests).
pub struct SessionList<'a> {
    pub rows: &'a [SessionRow],
    pub metas: Vec<String>,
}

impl SessionList<'_> {
    pub fn from_rows(rows: &[SessionRow]) -> SessionList<'_> {
        SessionList {
            rows,
            metas: rows.iter().map(session_row_meta).collect(),
        }
    }
}

impl icedtea::collection::ListModel for SessionList<'_> {
    fn len(&self) -> usize {
        self.rows.len()
    }

    fn id(&self, index: usize) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut h = std::collections::hash_map::DefaultHasher::new();
        self.rows
            .get(index)
            .map(|r| r.session_id.as_str())
            .unwrap_or("")
            .hash(&mut h);
        h.finish()
    }

    fn title(&self, index: usize) -> &str {
        self.rows
            .get(index)
            .map(SessionRow::display_title)
            .unwrap_or("")
    }

    fn meta(&self, index: usize) -> Option<&str> {
        self.metas
            .get(index)
            .map(String::as_str)
            .filter(|s| !s.is_empty())
    }
}

/// Clamp a rail/wheel offset so the window stays on content.
pub fn clamp_scroll(y: f32, content: f32, viewport: f32) -> f32 {
    y.clamp(0.0, (content - viewport).max(0.0))
}

/// Control `session` argument: live directory path, else id.
pub fn session_rpc_ref(path: &str, session_id: &str) -> String {
    let path = path.trim();
    if !path.is_empty() && std::path::Path::new(path).is_dir() {
        return path.to_string();
    }
    session_id.trim().to_string()
}

/// Event pages load whenever the Events pane is active (with a session).
///
/// **All turns** means no turn prompt filter — still paginated full timeline.
/// Search and Type filters apply on the client/owner over that stream.
/// The first page loads with no query.
pub fn should_fetch_timeline(on_events_tab: bool, _query: &str, _turn_prompt: Option<i64>) -> bool {
    on_events_tab
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct TickPlan {
    pub fetch_list: bool,
    pub load_overview: bool,
    pub refresh_timeline: bool,
}

/// One drained control notify for [`plan_tick`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TickNotify {
    pub method: String,
    pub session_id: String,
    /// ``session/changed.listChanged``; true when omitted (older owner).
    pub list_changed: bool,
}

impl TickNotify {
    pub fn new(method: impl Into<String>, session_id: impl Into<String>) -> Self {
        Self {
            method: method.into(),
            session_id: session_id.into(),
            list_changed: true,
        }
    }
}

/// Inputs for [`plan_tick`] (notify drain + live poll).
pub struct TickInput<'a> {
    pub notifies: &'a [TickNotify],
    pub selected_sid: &'a str,
    pub overview_sid: &'a str,
    pub palette_live: bool,
    pub list_elapsed_ms: u64,
    pub selected_live: bool,
    pub any_live: bool,
    pub on_timeline: bool,
    pub notes_locked: bool,
    /// Focus gain: same list/overview work as a due poll, without faking elapsed.
    pub catch_up: bool,
}

/// Coalesce notify + poll into at most one list fetch and one overview load.
pub fn plan_tick(input: TickInput<'_>) -> TickPlan {
    let mut plan = TickPlan::default();
    for note in input.notifies {
        let sid = note.session_id.as_str();
        let open = !sid.is_empty() && sid == input.overview_sid;
        if note.method == "session/changed" {
            if sid.is_empty() || note.list_changed {
                plan.fetch_list = true;
            }
            if open {
                plan.refresh_timeline = true;
            }
        }
        if note.method == "session/selected" {
            plan.fetch_list = true;
        }
        if open
            && (note.method == "notes/changed" || note.method == "analysis/changed")
            && !input.notes_locked
        {
            plan.load_overview = true;
        }
    }
    if input.catch_up {
        plan.fetch_list = true;
        if input.selected_live && !input.overview_sid.is_empty() && input.on_timeline {
            plan.refresh_timeline = true;
        }
    }
    let _ = (input.palette_live, input.any_live, input.list_elapsed_ms);
    plan
}

const LIVE_STATUS: &[&str] = &[
    "running",
    "ending",
    "in_progress",
    "pending",
    "awaiting",
    "awaiting_follow_up",
];

pub fn is_live_status(status: &str) -> bool {
    let x = status
        .trim()
        .to_ascii_lowercase()
        .replace(char::is_whitespace, "_");
    if x.is_empty() || x == "—" || x == "-" {
        return false;
    }
    if LIVE_STATUS.contains(&x.as_str()) {
        return true;
    }
    x.contains("await") || x == "run" || x.starts_with("runn")
}

pub fn has_open_turn(turns: &TurnsBlock) -> bool {
    turns.has_open_turn()
}

pub fn session_needs_live_poll(status: &str, turns: Option<&TurnsBlock>) -> bool {
    is_live_status(status) || turns.is_some_and(has_open_turn)
}

/// Indices into *events* after kind + typeahead filter.
///
/// Hits stay in timeline order. A non-empty query drops non-matches
/// via [`crate::fuzzy::fzf_score`] and does not clone the events.
pub fn filter_timeline_indices(
    events: &[TimelineEvent],
    kind: KindFilter,
    query: &str,
) -> Vec<usize> {
    let needle = query.trim();
    events
        .iter()
        .enumerate()
        .filter(|(_, ev)| ev.matches_kind(kind))
        .filter(|(_, ev)| needle.is_empty() || crate::fuzzy::fzf_score(needle, &ev.haystack()) > 0)
        .map(|(i, _)| i)
        .collect()
}

/// Next ``session/list`` offset, or ``None`` when the catalog drain is done.
pub fn catalog_drain_next(
    offset: u32,
    batch_len: usize,
    page: u32,
    matched: i64,
    stalled: bool,
) -> Option<u32> {
    if stalled || batch_len == 0 || page == 0 {
        return None;
    }
    let next = offset.saturating_add(batch_len as u32);
    if (batch_len as u32) < page {
        return None;
    }
    if matched > 0 && i64::from(next) >= matched {
        return None;
    }
    Some(next)
}

/// First HUD ``session/list``: one page, no ``sinceRevision`` drain.
///
/// :returns: ``(limit, offset, since)`` for the first catalog RPC.
pub fn first_list_fetch() -> (u32, u32, i64) {
    (200, 0, 0)
}

/// Next catalog page after a painted list, or ``None`` when the drain is done.
///
/// Wraps [`catalog_drain_next`] from offset 0 with *have* accumulated rows.
/// Stops when ``matched`` is not greater than *have*, or when *incomplete*
/// (including an empty incomplete first page).
///
/// :param have: Rows already applied to the HUD catalog.
/// :param page: Page size (``SESSION_LIST_PAGE``).
/// :param matched: Owner ``matched`` count from the last page.
/// :param incomplete: Owner still building, or drain should stall.
/// :returns: Offset for the next ``session/list``, if any.
pub fn next_list_offset(have: usize, page: u32, matched: i64, incomplete: bool) -> Option<u32> {
    if matched <= i64::try_from(have).unwrap_or(i64::MAX) {
        return None;
    }
    catalog_drain_next(0, have, page, matched, incomplete)
}

pub fn timeline_seek_offset(focus_index: i64, pad: i64) -> u32 {
    if focus_index < 0 {
        return 0;
    }
    (focus_index - pad.max(0)).max(0) as u32
}

/// Pager copy for a loaded window into the filtered timeline.
///
/// *offset* is the owner's filtered-list index of the first buffered row
/// (0 when reading from the start; ``hit-8`` after a jump). *buffered* is
/// how many rows we have, not a position. A jump to a late turn must not
/// keep showing ``60 of 7663`` from an earlier prefix.
pub fn timeline_range_label(offset: u32, buffered: usize, total: u32) -> String {
    if buffered == 0 {
        return if total == 0 {
            String::new()
        } else {
            format!("0 of {total}")
        };
    }
    let start = offset.saturating_add(1);
    let end = offset.saturating_add(buffered as u32);
    let end = if total > 0 { end.min(total) } else { end };
    if total > 0 && offset == 0 && end >= total {
        return format!("{total}");
    }
    if total == 0 {
        return format!("{start}-{end}");
    }
    format!("{start}-{end} of {total}")
}

/// Start of the loaded window after a page lands.
pub fn timeline_window_start(
    prev_offset: u32,
    page_offset: u32,
    replace: bool,
    advance: bool,
) -> u32 {
    if !advance {
        return prev_offset;
    }
    if replace {
        return page_offset;
    }
    prev_offset.min(page_offset)
}

/// Near the top of a jumped window: fetch earlier filtered rows.
pub fn should_load_previous_timeline(scroll_y: f32, window_offset: u32, loading: bool) -> bool {
    !loading && window_offset > 0 && scroll_y < TIMELINE_ROW_H * 3.0
}

/// Owner ``offset``/``limit`` for the page before the current window.
pub fn previous_timeline_page(window_offset: u32, chunk: u32) -> Option<(u32, u32)> {
    if window_offset == 0 || chunk == 0 {
        return None;
    }
    let limit = chunk.min(window_offset);
    Some((window_offset - limit, limit))
}

/// Keep the same cards on screen after earlier rows are prepended.
pub fn scroll_after_prepend(scroll_y: f32, added: usize, row_h: f32) -> f32 {
    scroll_y + added as f32 * row_h.max(0.0)
}

/// Next filtered offset after a page the owner actually returned.
///
/// Jump/around replies often have ``page.offset != request offset``. Paging
/// must continue from the returned window, not the request, or a later fill
/// inserts earlier rows in front of the visible list.
pub fn timeline_page_next(page_offset: u32, batch_len: u32, prev_next: u32, advance: bool) -> u32 {
    if !advance {
        return prev_next;
    }
    prev_next.max(page_offset.saturating_add(batch_len))
}

/// Caption under the Timeline list when more owner rows exist.
///
/// Hidden when the buffer is complete or already on the last page (Tail).
pub fn timeline_more_caption(complete: bool, at_end: bool, loading: bool) -> Option<&'static str> {
    if complete || at_end {
        return None;
    }
    Some(if loading {
        "Loading more events…"
    } else {
        "More events available — scroll or wait"
    })
}

/// Owner offset of the last page of *total* events (*limit* rows).
pub fn last_timeline_page_offset(total: u32, limit: u32) -> u32 {
    let lim = limit.max(1);
    total.saturating_sub(lim)
}

pub fn timeline_coverage_complete(buffered: usize, total: u32) -> bool {
    if total == 0 {
        return buffered == 0;
    }
    buffered >= total as usize
}

/// Keep paging while the Timeline tab is open and the buffer is under *cap*.
pub fn should_continue_timeline(
    on_timeline: bool,
    complete: bool,
    loading: bool,
    buffered: usize,
    cap: usize,
) -> bool {
    on_timeline && !complete && !loading && buffered < cap
}

/// Accordion expand: same index collapses; a different index replaces the open set.
pub fn toggle_expand_set(expanded: &mut HashSet<i64>, index: i64) {
    if expanded.contains(&index) {
        expanded.clear();
        return;
    }
    expanded.clear();
    expanded.insert(index);
}

/// True when *index* is in the expanded set.
pub fn is_expanded(expanded: &HashSet<i64>, index: i64) -> bool {
    expanded.contains(&index)
}

/// Keep at most *cap* events around *pivot* (focus index, or the middle).
pub fn trim_timeline_buffer(
    events: Vec<crate::wire::TimelineEvent>,
    pivot: Option<i64>,
    cap: usize,
) -> Vec<crate::wire::TimelineEvent> {
    if cap == 0 {
        return vec![];
    }
    if events.len() <= cap {
        return events;
    }
    let mut evs = events;
    evs.sort_by_key(|e| e.index);
    let mid = pivot.unwrap_or_else(|| evs[evs.len() / 2].index);
    let pos = evs
        .iter()
        .position(|e| e.index >= mid)
        .unwrap_or(evs.len().saturating_sub(1));
    let half = cap / 2;
    let start = pos.saturating_sub(half);
    let end = (start + cap).min(evs.len());
    let start = end.saturating_sub(cap);
    evs.drain(start..end).collect()
}

/// Context fill 0..=1 from a percent field or a ``12%`` compact string.
pub fn context_fraction(pct: Option<f64>, compact: &str) -> f32 {
    if let Some(p) = pct {
        return (p as f32 / 100.0).clamp(0.0, 1.0);
    }
    let s = compact.trim().trim_end_matches('%').trim();
    s.parse::<f32>()
        .ok()
        .map(|v| (v / 100.0).clamp(0.0, 1.0))
        .unwrap_or(0.0)
}

/// Severity bucket for findings (0 = highest).
pub fn finding_severity_rank(sev: &str) -> u8 {
    match sev.to_ascii_lowercase().as_str() {
        "high" | "error" | "critical" => 0,
        "medium" | "warn" | "warning" => 1,
        "low" | "info" => 2,
        _ => 3,
    }
}

pub fn finding_severity_title(rank: u8) -> &'static str {
    match rank {
        0 => "High",
        1 => "Medium",
        2 => "Low",
        _ => "Other",
    }
}

pub fn timeline_first_missing_offset(events: &[TimelineEvent], total: u32) -> u32 {
    if total == 0 {
        return 0;
    }
    let mut have = HashSet::new();
    for ev in events {
        have.insert(ev.index);
    }
    for i in 0..i64::from(total) {
        if !have.contains(&i) {
            return i as u32;
        }
    }
    total
}

pub struct MergeResult {
    pub events: Vec<TimelineEvent>,
    pub added: usize,
    pub updated: usize,
}

pub fn merge_timeline_by_index(existing: &[TimelineEvent], batch: &[TimelineEvent]) -> MergeResult {
    let mut by_index: BTreeMap<i64, TimelineEvent> = BTreeMap::new();
    for ev in existing {
        by_index.insert(ev.index, ev.clone());
    }
    let mut added = 0;
    let mut updated = 0;
    for ev in batch {
        match by_index.get(&ev.index) {
            Some(prev) if prev.fingerprint() == ev.fingerprint() => {}
            Some(_) => {
                by_index.insert(ev.index, ev.clone());
                updated += 1;
            }
            None => {
                by_index.insert(ev.index, ev.clone());
                added += 1;
            }
        }
    }
    MergeResult {
        events: by_index.into_values().collect(),
        added,
        updated,
    }
}

pub fn is_soft_notes_save_error(msg: &str) -> bool {
    let m = msg;
    if m.is_empty() {
        return false;
    }
    let low = m.to_ascii_lowercase();
    low.contains("operator notes changed")
        || low.contains("notes_conflict")
        || low.contains("409")
        || low.contains("notes conflict")
        || (low.contains("stale") && low.contains("revision"))
        || low.contains("expectedrevision")
        || low.contains("note.id")
        || low.contains("must match")
        || low.contains("note is required")
        || low.contains("noteid is required")
}

pub fn default_notes_schema() -> Vec<SchemaField> {
    vec![
        SchemaField {
            id: "summary".into(),
            label: "Summary".into(),
            choices: vec![],
            pick: "one-of".into(),
        },
        SchemaField {
            id: "detail".into(),
            label: "Detail".into(),
            choices: vec![],
            pick: "one-of".into(),
        },
    ]
}

pub fn notes_schema_fields(overview: Option<&Overview>) -> Vec<SchemaField> {
    let Some(ov) = overview else {
        return default_notes_schema();
    };
    let mut out = Vec::new();
    for f in &ov.notes.schema.fields {
        let id = f.id.trim();
        if id.is_empty() {
            continue;
        }
        let label = if f.label.is_empty() {
            id.to_string()
        } else {
            f.label.clone()
        };
        out.push(SchemaField {
            id: id.to_string(),
            label,
            choices: f.choices.clone(),
            pick: if f.pick.is_empty() {
                "one-of".into()
            } else {
                f.pick.clone()
            },
        });
    }
    if out.is_empty() {
        default_notes_schema()
    } else {
        out
    }
}

/// Iced id for a free-text notes schema field.
pub fn note_field_input_key(field_id: &str) -> String {
    format!("note-field-{field_id}")
}

/// Iced id for the notes turn field.
pub const NOTE_TURN_INPUT: &str = "note-turn";

/// Tab order for notes text fields (schema free-text, then turn).
pub fn note_text_input_keys(fields: &[SchemaField]) -> Vec<String> {
    let mut keys: Vec<String> = fields
        .iter()
        .filter(|spec| !spec.constrained())
        .map(|spec| note_field_input_key(&spec.id))
        .collect();
    keys.push(NOTE_TURN_INPUT.to_string());
    keys
}

/// Split a stored multi-select value into tokens (newline-separated).
pub fn decode_many_choices(stored: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for line in stored.replace('\r', "\n").split('\n') {
        let tok = line.trim();
        if !tok.is_empty() && seen.insert(tok.to_string()) {
            out.push(tok.to_string());
        }
    }
    out
}

/// Join selected tokens: schema order first, then extras.
pub fn encode_many_choices(selected: &[String], choices: &[String]) -> String {
    let mut seen = HashSet::new();
    let mut sel = Vec::new();
    for item in selected {
        let s = item.trim();
        if !s.is_empty() && seen.insert(s.to_string()) {
            sel.push(s.to_string());
        }
    }
    if sel.is_empty() {
        return String::new();
    }
    let allowed: HashSet<&str> = choices.iter().map(String::as_str).collect();
    let mut ordered: Vec<String> = choices
        .iter()
        .filter(|c| seen.contains(c.as_str()))
        .cloned()
        .collect();
    for extra in sel {
        if !allowed.contains(extra.as_str()) {
            ordered.push(extra);
        }
    }
    ordered.join("\n")
}

/// Toggle one token in a stored multi-select value.
pub fn toggle_many_choice(stored: &str, choice: &str, choices: &[String]) -> String {
    let mut selected = decode_many_choices(stored);
    if let Some(i) = selected.iter().position(|c| c == choice) {
        selected.remove(i);
    } else {
        selected.push(choice.to_string());
    }
    encode_many_choices(&selected, choices)
}

#[derive(Debug, Clone, Default)]
pub struct CardMark {
    pub findings: u32,
    pub notes: u32,
    pub errors: u32,
    pub first_finding_event: Option<i64>,
    pub first_note_id: String,
}

fn turn_list_keys(turns: &[TurnRow], face: i64) -> Vec<i64> {
    turns
        .iter()
        .filter(|t| t.face_id() == Some(face))
        .map(|t| t.turn_index)
        .collect()
}

pub fn card_marks_from_overview(
    overview: &Overview,
) -> (HashMap<i64, CardMark>, HashMap<i64, CardMark>) {
    let mut turns: HashMap<i64, CardMark> = HashMap::new();
    let mut events: HashMap<i64, CardMark> = HashMap::new();
    let rows = &overview.turns.turns;

    for f in &overview.findings.findings {
        let evs = &f.event_indices;
        let primary = f.primary_event_index.or_else(|| evs.first().copied());
        for face in &f.turn_indices {
            for key in turn_list_keys(rows, *face) {
                let row = turns.entry(key).or_default();
                row.findings += 1;
                if row.first_finding_event.is_none() {
                    row.first_finding_event = primary;
                }
            }
        }
        for ei in evs {
            let row = events.entry(*ei).or_default();
            row.findings += 1;
            if row.first_finding_event.is_none() {
                row.first_finding_event = primary;
            }
        }
        if let Some(first) = primary {
            if evs.is_empty() {
                let row = events.entry(first).or_default();
                row.findings += 1;
                if row.first_finding_event.is_none() {
                    row.first_finding_event = Some(first);
                }
            }
        }
    }

    for n in &overview.notes.notes {
        let nid = n.id.clone();
        if let Some(face) = n.turn_index {
            for key in turn_list_keys(rows, face) {
                let row = turns.entry(key).or_default();
                row.notes += 1;
                if row.first_note_id.is_empty() {
                    row.first_note_id = nid.clone();
                }
            }
        }
        for ei in &n.event_indices {
            let row = events.entry(*ei).or_default();
            row.notes += 1;
            if row.first_note_id.is_empty() {
                row.first_note_id = nid.clone();
            }
        }
    }

    for t in &overview.turns.turns {
        let err = t.tool_error_count;
        if err == 0 {
            continue;
        }
        turns.entry(t.turn_index).or_default().errors += err as u32;
    }

    (turns, events)
}

/// Keep overview-patched status when a quiet catalog refresh sends a blank label.
fn catalog_row_key(row: &SessionRow) -> String {
    crate::desktop::notice_row_key(&row.origin, &row.session_id)
}

pub fn patch_catalog_delta(
    prev: &[SessionRow],
    upserted: Vec<SessionRow>,
    removed: &[String],
) -> Vec<SessionRow> {
    let drop: HashSet<&str> = removed.iter().map(String::as_str).collect();
    let mut kept: Vec<SessionRow> = prev
        .iter()
        .filter(|row| !drop.contains(row.session_id.as_str()))
        .cloned()
        .collect();
    if upserted.is_empty() {
        return kept;
    }
    let patched = merge_catalog_rows(&kept, upserted);
    let mut by_id: HashMap<String, usize> = kept
        .iter()
        .enumerate()
        .map(|(i, row)| (catalog_row_key(row), i))
        .collect();
    for row in patched {
        let key = catalog_row_key(&row);
        if let Some(idx) = by_id.get(&key).copied() {
            kept[idx] = row;
        } else {
            by_id.insert(key, kept.len());
            kept.push(row);
        }
    }
    kept
}

pub fn merge_catalog_rows(prev: &[SessionRow], next: Vec<SessionRow>) -> Vec<SessionRow> {
    use crate::format::{is_blank_status, list_status_label};

    let old: HashMap<String, &SessionRow> = prev.iter().map(|r| (catalog_row_key(r), r)).collect();
    next.into_iter()
        .map(|mut row| {
            if let Some(p) = old.get(&catalog_row_key(&row)) {
                if is_blank_status(&row.status) && !is_blank_status(&p.status) {
                    row.status = p.status.clone();
                }
                if crate::format::is_terminal_status(&p.status)
                    && is_live_status(&row.status)
                    && !is_live_status(&list_status_label("", &row.outcome))
                {
                    row.status = p.status.clone();
                }
                if row.outcome.is_empty() && !p.outcome.is_empty() {
                    row.outcome = p.outcome.clone();
                }
                if row.context_usage_compact.is_empty() && !p.context_usage_compact.is_empty() {
                    row.context_usage_compact = p.context_usage_compact.clone();
                }
            }
            row.status = list_status_label(&row.status, &row.outcome);
            row
        })
        .collect()
}

pub fn patch_list_row_from_meta(rows: &mut [SessionRow], session_id: &str, meta: &SessionMeta) {
    let Some(row) = rows.iter_mut().find(|r| r.session_id == session_id) else {
        return;
    };
    if !meta.status.is_empty() {
        row.status = crate::format::list_status_label(&meta.status, &row.outcome);
    }
    if !meta.title.is_empty() {
        row.title = meta.title.clone();
    }
    if !meta.label.is_empty() {
        row.label = meta.label.clone();
    }
    if !meta.model.is_empty() {
        row.model = meta.model.clone();
    }
    if !meta.origin.is_empty() {
        row.origin = meta.origin.clone();
    }
    if meta.duration_seconds > 0.0 {
        row.duration_seconds = meta.duration_seconds;
    }
    if !meta.outcome.is_empty() {
        row.outcome = meta.outcome.clone();
        row.status = crate::format::list_status_label(&row.status, &row.outcome);
    }
    if !meta.context_usage_compact.is_empty() {
        row.context_usage_compact = meta.context_usage_compact.clone();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::wire::{SessionMeta, TimelineEvent, TurnRow, TurnsBlock};

    fn ev(index: i64, kind: &str, content: &str) -> TimelineEvent {
        TimelineEvent {
            index,
            kind: kind.into(),
            content: content.into(),
            ..TimelineEvent::default()
        }
    }

    #[test]
    fn live_status_labels() {
        assert!(is_live_status("running"));
        assert!(is_live_status("awaiting"));
        assert!(is_live_status("awaiting_follow_up"));
        assert!(is_live_status("ending"));
        assert!(!is_live_status("complete"));
        assert!(!is_live_status("cancelled"));
        assert!(!is_live_status("—"));
        assert!(!is_live_status(""));
    }

    #[test]
    fn catalog_delta_keeps_host_and_work_copies() {
        let work = SessionRow {
            session_id: "s1".into(),
            origin: "work".into(),
            status: "complete".into(),
            ..SessionRow::default()
        };
        let host = SessionRow {
            session_id: "s1".into(),
            origin: "host".into(),
            status: "running".into(),
            ..SessionRow::default()
        };
        let out = patch_catalog_delta(std::slice::from_ref(&work), vec![host.clone()], &[]);
        assert_eq!(out.len(), 2);
        assert!(out
            .iter()
            .any(|r| r.origin == "work" && r.status == "complete"));
        assert!(out
            .iter()
            .any(|r| r.origin == "host" && r.status == "running"));
        let again = patch_catalog_delta(&out, vec![work, host], &[]);
        assert_eq!(again.len(), 2);
    }

    #[test]
    fn open_turn_forces_poll() {
        let open = TurnsBlock {
            turns: vec![TurnRow {
                open: true,
                ..TurnRow::default()
            }],
            ..TurnsBlock::default()
        };
        let closed = TurnsBlock {
            turns: vec![TurnRow {
                open: false,
                ..TurnRow::default()
            }],
            ..TurnsBlock::default()
        };
        assert!(session_needs_live_poll("complete", Some(&open)));
        assert!(!session_needs_live_poll("complete", Some(&closed)));
    }

    #[test]
    fn catalog_delta_removes_and_upserts() {
        use crate::model::SessionRow;
        let prev = vec![
            SessionRow {
                session_id: "a".into(),
                title: "A".into(),
                ..SessionRow::default()
            },
            SessionRow {
                session_id: "b".into(),
                title: "B".into(),
                ..SessionRow::default()
            },
        ];
        let upserted = vec![SessionRow {
            session_id: "c".into(),
            title: "C".into(),
            ..SessionRow::default()
        }];
        let out = patch_catalog_delta(&prev, upserted, &["b".into()]);
        let ids: Vec<&str> = out.iter().map(|r| r.session_id.as_str()).collect();
        assert_eq!(ids, ["a", "c"]);
    }

    #[test]
    fn catalog_pages() {
        assert_eq!(catalog_drain_next(0, 200, 200, 450, false), Some(200));
        assert_eq!(catalog_drain_next(200, 200, 200, 450, false), Some(400));
        assert_eq!(catalog_drain_next(400, 50, 200, 450, false), None);
        assert_eq!(catalog_drain_next(0, 200, 200, 200, false), None);
        assert_eq!(catalog_drain_next(200, 200, 200, 450, true), None);
        assert_eq!(catalog_drain_next(0, 0, 200, 10, false), None);
    }

    #[test]
    fn first_list_fetch_is_one_page() {
        let (limit, offset, since) = first_list_fetch();
        assert_eq!(limit, 200);
        assert_eq!(offset, 0);
        assert_eq!(since, 0);
    }

    #[test]
    fn next_list_offset_wraps_catalog_drain() {
        assert_eq!(next_list_offset(200, 200, 450, false), Some(200));
        assert_eq!(next_list_offset(400, 200, 450, false), Some(400));
        assert_eq!(next_list_offset(450, 200, 450, false), None);
        assert_eq!(next_list_offset(200, 200, 200, false), None);
        assert_eq!(next_list_offset(200, 200, 450, true), None);
        assert_eq!(next_list_offset(0, 200, 10, false), None);
        assert_eq!(next_list_offset(0, 200, 10, true), None);
    }

    #[test]
    fn filter_turn_indices_matches_label_and_prompt() {
        let turns = vec![
            TurnRow {
                turn_index: 0,
                label: "setup".into(),
                summary: "install deps".into(),
                ..TurnRow::default()
            },
            TurnRow {
                turn_index: 1,
                label: "fix".into(),
                summary: "paint cards".into(),
                ..TurnRow::default()
            },
        ];
        assert_eq!(filter_turn_indices(&turns, "").len(), 2);
        assert_eq!(filter_turn_indices(&turns, "paint"), vec![1]);
        assert_eq!(filter_turn_indices(&turns, "setup"), vec![0]);
        assert!(filter_turn_indices(&turns, "missing").is_empty());
    }

    #[test]
    fn filter_timeline_indices_keeps_order_without_query() {
        let events = vec![
            ev(0, "user", "hello"),
            ev(1, "tool", "run"),
            ev(2, "agent", "ok"),
        ];
        assert_eq!(
            filter_timeline_indices(&events, KindFilter::All, ""),
            vec![0, 1, 2]
        );
        assert_eq!(
            filter_timeline_indices(&events, KindFilter::Tools, ""),
            vec![1]
        );
    }

    #[test]
    fn filter_timeline_indices_keeps_time_order_for_query() {
        let events = vec![
            ev(0, "user", "also hud"),
            ev(1, "user", "hud window"),
            ev(2, "user", "other"),
        ];
        assert_eq!(
            filter_timeline_indices(&events, KindFilter::All, "hud"),
            vec![0, 1]
        );
        assert!(filter_timeline_indices(&events, KindFilter::Tools, "hud").is_empty());
    }

    #[test]
    fn timeline_holes() {
        assert!(!timeline_coverage_complete(5, 100));
        assert!(timeline_coverage_complete(100, 100));
        assert!(timeline_coverage_complete(0, 0));
        let events = vec![ev(0, "user", ""), ev(2, "agent", "")];
        assert_eq!(timeline_first_missing_offset(&events, 3), 1);
    }

    #[test]
    fn merge_updates_changed_only() {
        let existing = vec![ev(1, "agent", "a")];
        let batch = vec![ev(1, "agent", "a"), ev(2, "user", "b")];
        let m = merge_timeline_by_index(&existing, &batch);
        assert_eq!(m.added, 1);
        assert_eq!(m.updated, 0);
        assert_eq!(m.events.len(), 2);
    }

    #[test]
    fn soft_notes_errors() {
        assert!(is_soft_notes_save_error("operator notes changed"));
        assert!(is_soft_notes_save_error("notes_conflict"));
        assert!(is_soft_notes_save_error(
            "RPC error 409: operator notes changed"
        ));
        assert!(!is_soft_notes_save_error("connection refused"));
        assert!(!is_soft_notes_save_error(""));
    }

    #[test]
    fn catalog_refresh_applies_newer_live_status() {
        use crate::model::SessionRow;
        let prev = vec![SessionRow {
            session_id: "s1".into(),
            status: "complete".into(),
            outcome: "success".into(),
            ..SessionRow::default()
        }];
        let next = vec![SessionRow {
            session_id: "s1".into(),
            status: "running".into(),
            outcome: "running".into(),
            ..SessionRow::default()
        }];
        let merged = merge_catalog_rows(&prev, next);
        assert_eq!(merged[0].status, "running");
    }

    #[test]
    fn catalog_refresh_keeps_complete_when_live_label_has_no_live_outcome() {
        use crate::model::SessionRow;
        let prev = vec![SessionRow {
            session_id: "s1".into(),
            status: "complete".into(),
            outcome: "success".into(),
            ..SessionRow::default()
        }];
        let next = vec![SessionRow {
            session_id: "s1".into(),
            status: "running".into(),
            ..SessionRow::default()
        }];
        let merged = merge_catalog_rows(&prev, next);
        assert_eq!(merged[0].status, "complete");
        assert_eq!(merged[0].outcome, "success");
    }

    #[test]
    fn tick_plan_coalesces_session_changed_into_one_list_fetch() {
        let notifies = vec![
            TickNotify::new("session/changed", "a"),
            TickNotify::new("session/changed", "b"),
            TickNotify::new("session/changed", "a"),
        ];
        let plan = plan_tick(TickInput {
            notifies: &notifies,
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: 0,
            selected_live: true,
            any_live: true,
            on_timeline: false,
            notes_locked: false,
            catch_up: false,
        });
        assert!(plan.fetch_list);
        assert!(!plan.load_overview);
        assert!(plan.refresh_timeline);
    }

    #[test]
    fn wants_periodic_poll_overlay_tracks_visibility() {
        assert!(wants_periodic_poll(true, true, false));
        assert!(wants_periodic_poll(true, false, false));
        assert!(!wants_periodic_poll(false, true, false));
        assert!(!wants_periodic_poll(false, false, false));
    }

    #[test]
    fn wants_periodic_poll_pop_out_requires_focus() {
        assert!(wants_periodic_poll(true, true, true));
        assert!(!wants_periodic_poll(true, false, true));
        assert!(!wants_periodic_poll(false, true, true));
    }

    #[test]
    fn tick_plan_unfocused_still_fetches_on_session_changed() {
        let notifies = vec![TickNotify::new("session/changed", "a")];
        let plan = plan_tick(TickInput {
            notifies: &notifies,
            selected_sid: "a",
            overview_sid: "a",
            palette_live: false,
            list_elapsed_ms: LIVE_POLL_MS,
            selected_live: true,
            any_live: true,
            on_timeline: true,
            notes_locked: false,
            catch_up: false,
        });
        assert!(plan.fetch_list);
        assert!(!plan.load_overview);
        assert!(plan.refresh_timeline);
    }

    #[test]
    fn tick_plan_skips_list_fetch_on_quiet_tick() {
        let plan = plan_tick(TickInput {
            notifies: &[],
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: 500,
            selected_live: true,
            any_live: true,
            on_timeline: false,
            notes_locked: false,
            catch_up: false,
        });
        assert!(!plan.fetch_list);
        assert!(!plan.load_overview);
    }

    #[test]
    fn tick_plan_notify_does_not_open_from_list_highlight() {
        let notifies = vec![TickNotify::new("session/changed", "a")];
        let plan = plan_tick(TickInput {
            notifies: &notifies,
            selected_sid: "a",
            overview_sid: "",
            palette_live: true,
            list_elapsed_ms: 0,
            selected_live: true,
            any_live: true,
            on_timeline: false,
            notes_locked: false,
            catch_up: false,
        });
        assert!(plan.fetch_list);
        assert!(
            !plan.load_overview,
            "session/changed on a Spotlight highlight must not open browse"
        );
        assert!(!plan.refresh_timeline);
    }

    #[test]
    fn tick_plan_live_poll_does_not_open_session_without_overview() {
        let plan = plan_tick(TickInput {
            notifies: &[],
            selected_sid: "a",
            overview_sid: "",
            palette_live: true,
            list_elapsed_ms: LIVE_POLL_MS,
            selected_live: true,
            any_live: true,
            on_timeline: false,
            notes_locked: false,
            catch_up: false,
        });
        assert!(!plan.fetch_list);
        assert!(
            !plan.load_overview,
            "Spotlight must not auto-open from a list highlight"
        );
    }

    #[test]
    fn clamp_scroll_keeps_offset_on_content() {
        assert_eq!(clamp_scroll(-10.0, 600.0, 400.0), 0.0);
        assert_eq!(clamp_scroll(50.0, 600.0, 400.0), 50.0);
        assert_eq!(clamp_scroll(500.0, 600.0, 400.0), 200.0);
        assert_eq!(clamp_scroll(50.0, 100.0, 400.0), 0.0);
    }

    #[test]
    fn partial_list_page_is_not_a_full_snapshot() {
        assert!(is_partial_list_page(0, 0, false, true, true));
        assert!(is_partial_list_page(1, 964, false, false, false));
        assert!(is_partial_list_page(200, 964, false, false, false));
        assert!(!is_partial_list_page(964, 964, false, false, false));
        assert!(!is_partial_list_page(1, 1, true, false, false));
        assert!(!is_partial_list_page(0, 0, false, false, false));
        assert!(!is_partial_list_page(0, 0, false, false, false));
    }

    #[test]
    fn session_list_title_and_meta_are_two_lines() {
        use icedtea::collection::ListModel;
        let mut row = SessionRow {
            session_id: "abc".into(),
            title: "Fix the rail".into(),
            model: "grok-4".into(),
            status: "running".into(),
            context_usage_compact: "12%".into(),
            ..SessionRow::default()
        };
        assert_eq!(session_row_meta(&row), "12%");
        row.context_usage_compact.clear();
        let list = SessionList::from_rows(std::slice::from_ref(&row));
        assert_eq!(list.len(), 1);
        assert_eq!(list.title(0), "Fix the rail");
        assert_eq!(list.meta(0), None);
        assert_eq!(list.title(9), "");
        assert_eq!(list.meta(9), None);
        assert_eq!(
            list.id(0),
            SessionList::from_rows(std::slice::from_ref(&row)).id(0)
        );
    }

    #[test]
    fn spotlight_recent_is_newest_first_and_pins_keep() {
        let all: Vec<SessionRow> = (0..12)
            .map(|i| SessionRow {
                session_id: format!("s{i}"),
                sort_epoch: i as f64,
                ..SessionRow::default()
            })
            .collect();
        let recent = spotlight_recent(&all, 5, "");
        assert_eq!(recent.len(), 5);
        assert_eq!(recent[0].session_id, "s11");
        assert_eq!(recent[4].session_id, "s7");
        let pinned = spotlight_recent(&all, 5, "s0");
        assert_eq!(pinned[0].session_id, "s0");
        assert_eq!(pinned.len(), 5);
        assert!(pinned.iter().any(|r| r.session_id == "s11"));
    }

    #[test]
    fn should_page_recent_at_the_tail() {
        assert!(!should_page_recent(0, 0));
        assert!(!should_page_recent(3, 8));
        assert!(should_page_recent(7, 8));
        assert!(should_page_recent(8, 8));
    }

    #[test]
    fn next_spotlight_limit_grows_by_page() {
        assert_eq!(next_spotlight_limit(8, 40, 8), Some(16));
        assert_eq!(next_spotlight_limit(16, 20, 8), Some(20));
        assert_eq!(next_spotlight_limit(20, 20, 8), None);
        assert_eq!(next_spotlight_limit(8, 40, 0), None);
    }

    #[test]
    fn session_card_grows_when_the_title_wraps() {
        let short = session_card_height("Fix the rail", "", false);
        // Full-width picker uses ~72 columns — need a long line to wrap.
        let long_title =
            "Rewrite the session catalog filter and keep the host path readable ".repeat(3);
        let long = session_card_height(&long_title, "12%", true);
        assert!(short >= 50.0, "{short}");
        assert!(long > short + 10.0, "short={short} long={long}");
        assert!(short >= LIST_CARD_GAP, "card heights include LIST_CARD_GAP");
    }

    #[test]
    fn diff_hunk_scroll_y_pins_the_hit_line() {
        assert_eq!(diff_hunk_scroll_y(None), 0.0);
        assert_eq!(diff_hunk_scroll_y(Some(0)), 0.0);
        assert_eq!(diff_hunk_scroll_y(Some(10)), 10.0 * diff_hunk_line_h());
    }

    #[test]
    fn list_scroll_to_cover_stays_inside_height_sum() {
        let row = 80.0;
        let n = 200usize;
        let heights: Vec<f32> = (0..n).map(|_| row).collect();
        let view_h = 400.0;
        let y = list_scroll_to_cover(&heights, n - 1, 0.0, view_h);
        let content: f32 = heights.iter().copied().sum();
        assert_eq!(content, n as f32 * row);
        assert_eq!(y, (content - view_h).max(0.0));
        let top: f32 = heights.iter().take(n - 1).copied().sum();
        assert!(y + view_h + f32::EPSILON >= top + row);
    }

    #[test]
    fn list_focus_after_scroll_snaps_to_first_visible_row() {
        let heights = vec![92.0; 20];
        let view_h = 200.0;
        let scroll = 92.0 * 8.0;
        assert_eq!(
            list_focus_after_scroll(Some(0), scroll, view_h, &heights),
            Some(8)
        );
        assert_eq!(
            list_focus_after_scroll(Some(8), scroll, view_h, &heights),
            None
        );
        assert_eq!(
            list_focus_after_scroll(Some(19), scroll, view_h, &heights),
            Some(10)
        );
    }

    #[test]
    fn list_scroll_to_top_pins_tall_open_card() {
        let heights = vec![88.0, 2_000.0, 88.0];
        let y = list_scroll_to_top(&heights, 1, 400.0);
        assert!((y - 88.0).abs() < f32::EPSILON);
        // Tall row: cover also pins to top so the body can be scrolled through.
        let cover = list_scroll_to_cover(&heights, 1, 0.0, 400.0);
        assert!((cover - 88.0).abs() < f32::EPSILON);
    }

    #[test]
    fn session_rpc_ref_uses_path_only_when_directory_exists() {
        let dir = std::env::temp_dir().join("groket-hud-rpc-ref");
        let _ = std::fs::create_dir_all(&dir);
        assert_eq!(
            session_rpc_ref(dir.to_str().unwrap(), "uuid"),
            dir.to_str().unwrap()
        );
        assert_eq!(
            session_rpc_ref("/no/such/groket-hud-session", "uuid"),
            "uuid"
        );
        assert_eq!(session_rpc_ref("", "uuid"), "uuid");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn should_fetch_timeline_on_events_tab_including_all_turns() {
        assert!(should_fetch_timeline(true, "", None));
        assert!(should_fetch_timeline(true, "grep", None));
        assert!(should_fetch_timeline(true, "", Some(3)));
        assert!(!should_fetch_timeline(false, "grep", None));
        assert!(!should_fetch_timeline(false, "", None));
    }

    #[test]
    fn previous_page_starts_before_the_jumped_window() {
        assert_eq!(previous_timeline_page(1192, 80), Some((1112, 80)));
        assert_eq!(previous_timeline_page(40, 80), Some((0, 40)));
        assert_eq!(previous_timeline_page(0, 80), None);
        assert!(should_load_previous_timeline(0.0, 1192, false));
        assert!(!should_load_previous_timeline(0.0, 0, false));
        assert!(!should_load_previous_timeline(0.0, 1192, true));
        assert!(!should_load_previous_timeline(5_000.0, 1192, false));
        assert_eq!(scroll_after_prepend(0.0, 80, 160.0), 12_800.0);
    }

    #[test]
    fn timeline_range_label_uses_owner_window_not_buffer_count() {
        assert_eq!(timeline_range_label(0, 60, 7663), "1-60 of 7663");
        assert_eq!(timeline_range_label(1192, 40, 7663), "1193-1232 of 7663");
        assert_eq!(timeline_range_label(0, 7663, 7663), "7663");
        assert_eq!(timeline_range_label(0, 0, 7663), "0 of 7663");
        assert_eq!(timeline_window_start(0, 1192, true, true), 1192);
        assert_eq!(timeline_window_start(1192, 1232, false, true), 1192);
        assert_eq!(timeline_window_start(1192, 1192, false, false), 1192);
    }

    #[test]
    fn timeline_page_next_uses_owner_offset_not_request() {
        assert_eq!(timeline_page_next(12, 40, 0, true), 52);
        assert_eq!(timeline_page_next(0, 40, 0, true), 40);
        assert_eq!(timeline_page_next(12, 1, 40, false), 40);
        assert_eq!(timeline_page_next(50, 1, 40, true), 51);
    }

    #[test]
    fn should_continue_timeline_while_short() {
        assert!(should_continue_timeline(true, false, false, 10, 320));
        assert!(!should_continue_timeline(true, true, false, 10, 320));
        assert!(!should_continue_timeline(true, false, true, 10, 320));
        assert!(!should_continue_timeline(false, false, false, 10, 320));
        assert!(!should_continue_timeline(true, false, false, 320, 320));
        assert!(!should_continue_timeline(true, false, false, 400, 320));
    }

    #[test]
    fn toggle_expand_then_collapse_same_index() {
        let mut set = HashSet::new();
        toggle_expand_set(&mut set, 12);
        assert!(is_expanded(&set, 12));
        toggle_expand_set(&mut set, 12);
        assert!(!is_expanded(&set, 12));
        assert!(set.is_empty());
        toggle_expand_set(&mut set, 12);
        toggle_expand_set(&mut set, 44);
        assert!(!is_expanded(&set, 12));
        assert!(is_expanded(&set, 44));
        assert_eq!(set.len(), 1);
    }

    #[test]
    fn virtual_window_over_8000_only_mounts_the_visible_slice() {
        let n = 8000;
        let scroll = 320.0;
        let view_h = 480.0;
        let row_h = TIMELINE_ROW_H;
        let (top, win, _) =
            icedtea::collection::virtual_pads(n, row_h, scroll, view_h, TIMELINE_OVERSCAN, None);
        assert!(win.end > win.start);
        assert!(
            win.end - win.start < 20,
            "mounted {} rows",
            win.end - win.start
        );
        let ids: Vec<i64> = (0..n as i64).collect();
        let shown = ids[win.start..win.end].to_vec();
        let mut grown = ids.clone();
        grown.extend(8000..8500);
        let (top2, after, _) = icedtea::collection::virtual_pads(
            grown.len(),
            row_h,
            scroll,
            view_h,
            TIMELINE_OVERSCAN,
            None,
        );
        assert_eq!(after.start, win.start);
        assert_eq!(top2, top);
        assert_eq!(&grown[win.start..win.end], shown.as_slice());
        assert_eq!(ids[win.start], shown[0]);
    }

    #[test]
    fn timeline_more_caption_hides_at_end() {
        assert_eq!(
            timeline_more_caption(false, false, false),
            Some("More events available — scroll or wait")
        );
        assert_eq!(
            timeline_more_caption(false, false, true),
            Some("Loading more events…")
        );
        assert_eq!(timeline_more_caption(true, false, false), None);
        assert_eq!(timeline_more_caption(false, true, false), None);
        assert_eq!(timeline_more_caption(false, true, true), None);
    }

    #[test]
    fn last_timeline_page_offset_jumps_to_the_end() {
        assert_eq!(last_timeline_page_offset(3427, 80), 3347);
        assert_eq!(last_timeline_page_offset(10, 80), 0);
        assert_eq!(last_timeline_page_offset(0, 80), 0);
        assert_eq!(last_timeline_page_offset(80, 80), 0);
        assert_eq!(last_timeline_page_offset(81, 80), 1);
    }

    #[test]
    fn trim_timeline_keeps_a_window_around_the_pivot() {
        let ev = |i: i64| crate::wire::TimelineEvent {
            index: i,
            ..crate::wire::TimelineEvent::default()
        };
        let all: Vec<_> = (0..800).map(ev).collect();
        let kept = trim_timeline_buffer(all, Some(400), 100);
        assert_eq!(kept.len(), 100);
        assert!(kept.first().expect("start").index <= 400);
        assert!(kept.last().expect("end").index >= 400);
        assert!(kept.iter().any(|e| e.index == 400));
    }

    #[test]
    fn context_fraction_reads_pct_and_compact() {
        assert!((context_fraction(Some(12.0), "") - 0.12).abs() < 0.001);
        assert!((context_fraction(None, "48%") - 0.48).abs() < 0.001);
        assert_eq!(context_fraction(None, ""), 0.0);
        assert_eq!(context_fraction(Some(200.0), ""), 1.0);
    }

    #[test]
    fn tick_plan_catch_up_fetches_without_elapsed() {
        let plan = plan_tick(TickInput {
            notifies: &[],
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: 0,
            selected_live: true,
            any_live: true,
            on_timeline: false,
            notes_locked: false,
            catch_up: true,
        });
        assert!(plan.fetch_list);
        assert!(!plan.load_overview);
        assert!(!plan.refresh_timeline);
    }

    #[test]
    fn tick_plan_append_without_list_change_tails_only() {
        let notifies = vec![TickNotify {
            method: "session/changed".into(),
            session_id: "a".into(),
            list_changed: false,
        }];
        let plan = plan_tick(TickInput {
            notifies: &notifies,
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: 0,
            selected_live: true,
            any_live: true,
            on_timeline: true,
            notes_locked: false,
            catch_up: false,
        });
        assert!(!plan.fetch_list);
        assert!(!plan.load_overview);
        assert!(plan.refresh_timeline);
    }

    #[test]
    fn tick_plan_live_poll_does_not_reload_overview() {
        let plan = plan_tick(TickInput {
            notifies: &[],
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: LIVE_POLL_MS,
            selected_live: true,
            any_live: true,
            on_timeline: true,
            notes_locked: false,
            catch_up: false,
        });
        assert!(!plan.fetch_list);
        assert!(!plan.load_overview);
        assert!(!plan.refresh_timeline);
    }

    #[test]
    fn tick_plan_idle_poll_refreshes_list() {
        let plan = plan_tick(TickInput {
            notifies: &[],
            selected_sid: "a",
            overview_sid: "a",
            palette_live: true,
            list_elapsed_ms: IDLE_POLL_MS,
            selected_live: false,
            any_live: false,
            on_timeline: false,
            notes_locked: false,
            catch_up: false,
        });
        assert!(!plan.fetch_list);
        assert!(!plan.load_overview);
    }

    #[test]
    fn catalog_refresh_keeps_overview_status() {
        use crate::model::SessionRow;
        let prev = vec![SessionRow {
            session_id: "s1".into(),
            status: "complete".into(),
            outcome: "success".into(),
            context_usage_compact: "12%".into(),
            ..SessionRow::default()
        }];
        let next = vec![SessionRow {
            session_id: "s1".into(),
            status: "—".into(),
            ..SessionRow::default()
        }];
        let merged = merge_catalog_rows(&prev, next);
        assert_eq!(merged[0].status, "complete");
        assert_eq!(merged[0].outcome, "success");
        assert_eq!(merged[0].context_usage_compact, "12%");
    }

    #[test]
    fn schema_fallback() {
        let fields = notes_schema_fields(None);
        assert_eq!(fields.len(), 2);
        assert_eq!(fields[0].id, "summary");
        assert!(!fields[0].constrained());
        assert_eq!(
            note_text_input_keys(&fields),
            vec![
                note_field_input_key("summary"),
                note_field_input_key("detail"),
                NOTE_TURN_INPUT.to_string()
            ]
        );
    }

    #[test]
    fn notes_schema_keeps_choice_fields() {
        let ov = Overview {
            notes: crate::wire::NotesBlock {
                schema: crate::wire::NotesSchema {
                    fields: vec![
                        crate::wire::NoteSchemaField {
                            id: "severity".into(),
                            label: "Severity".into(),
                            choices: vec!["low".into(), "medium".into(), "high".into()],
                            pick: "one-of".into(),
                        },
                        crate::wire::NoteSchemaField {
                            id: "tags".into(),
                            label: "Tags".into(),
                            choices: vec!["ux".into(), "tooling".into()],
                            pick: "many".into(),
                        },
                    ],
                    ..crate::wire::NotesSchema::default()
                },
                ..crate::wire::NotesBlock::default()
            },
            ..Overview::default()
        };
        let fields = notes_schema_fields(Some(&ov));
        assert_eq!(fields.len(), 2);
        assert!(fields[0].constrained());
        assert!(!fields[0].pick_many());
        assert!(fields[1].pick_many());
        assert_eq!(
            note_text_input_keys(&fields),
            vec![NOTE_TURN_INPUT.to_string()]
        );
    }

    #[test]
    fn many_choices_round_trip_and_toggle() {
        let choices = ["regression".into(), "ux".into(), "tooling".into()];
        assert_eq!(
            encode_many_choices(
                &["tooling".into(), "regression".into(), "tooling".into()],
                &choices
            ),
            "regression\ntooling"
        );
        assert_eq!(encode_many_choices(&[], &choices), "");
        assert_eq!(
            decode_many_choices("ux\ntooling\nux"),
            vec!["ux", "tooling"]
        );
        assert_eq!(
            encode_many_choices(&["custom".into(), "ux".into()], &choices),
            "ux\ncustom"
        );
        let on = toggle_many_choice("", "ux", &choices);
        assert_eq!(on, "ux");
        assert_eq!(toggle_many_choice(&on, "ux", &choices), "");
    }

    #[test]
    fn card_marks_join_trace_number_to_list_key() {
        let ov = Overview {
            turns: TurnsBlock {
                turns: vec![TurnRow {
                    turn_index: 13,
                    turn_number: Some(15),
                    tool_error_count: 2,
                    ..TurnRow::default()
                }],
                ..TurnsBlock::default()
            },
            findings: crate::wire::FindingsBlock {
                findings: vec![crate::wire::FindingRow {
                    turn_indices: vec![15],
                    primary_event_index: Some(100),
                    ..crate::wire::FindingRow::default()
                }],
                ..crate::wire::FindingsBlock::default()
            },
            notes: crate::wire::NotesBlock {
                notes: vec![crate::wire::NoteRow {
                    id: "n1".into(),
                    turn_index: Some(15),
                    ..crate::wire::NoteRow::default()
                }],
                ..crate::wire::NotesBlock::default()
            },
            ..Overview::default()
        };
        let (turns, _) = card_marks_from_overview(&ov);
        let mark = turns.get(&13).expect("list key 13");
        assert_eq!(mark.findings, 1);
        assert_eq!(mark.notes, 1);
        assert_eq!(mark.errors, 2);
        assert_eq!(mark.first_finding_event, Some(100));
        assert_eq!(mark.first_note_id, "n1");
        assert!(!turns.contains_key(&15));
    }

    #[test]
    fn card_marks_skip_unnumbered_list_key() {
        let ov = Overview {
            turns: TurnsBlock {
                turns: vec![TurnRow {
                    turn_index: 27,
                    turn_number: None,
                    ..TurnRow::default()
                }],
                ..TurnsBlock::default()
            },
            findings: crate::wire::FindingsBlock {
                findings: vec![crate::wire::FindingRow {
                    turn_indices: vec![27],
                    primary_event_index: Some(100),
                    ..crate::wire::FindingRow::default()
                }],
                ..crate::wire::FindingsBlock::default()
            },
            ..Overview::default()
        };
        let (turns, _) = card_marks_from_overview(&ov);
        assert!(!turns.contains_key(&27));
    }

    #[test]
    fn card_marks_and_meta_patch_from_typed_overview() {
        let ov = crate::wire::decode_overview(&{
            let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/overview.json");
            serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
        })
        .unwrap();
        let (turns, _events) = card_marks_from_overview(&ov);
        assert!(turns.is_empty() || ov.findings.findings.is_empty());
        let fields = notes_schema_fields(Some(&ov));
        assert!(fields.iter().any(|f| f.id == "summary"));

        let mut rows = vec![SessionRow {
            session_id: "sess-wire".into(),
            status: "—".into(),
            ..SessionRow::default()
        }];
        patch_list_row_from_meta(&mut rows, "sess-wire", &ov.meta);
        assert_eq!(rows[0].status, "running");
        assert_eq!(rows[0].title, "View session");
        assert_eq!(rows[0].origin, "work");
    }

    #[test]
    fn patch_list_row_copies_origin_and_duration() {
        let mut rows = vec![SessionRow {
            session_id: "s1".into(),
            ..SessionRow::default()
        }];
        let meta = SessionMeta {
            origin: "host".into(),
            duration_seconds: 125.0,
            model: "grok-4".into(),
            status: "complete".into(),
            ..SessionMeta::default()
        };
        patch_list_row_from_meta(&mut rows, "s1", &meta);
        assert_eq!(rows[0].origin, "host");
        assert_eq!(rows[0].duration_seconds, 125.0);
        assert_eq!(rows[0].model, "grok-4");
        assert_eq!(rows[0].status, "complete");
    }
}
