/*
 * Pure text rendering of an agentgraph snapshot document (WEB_API.md section 1) for a narrow terminal sidebar.
 *
 * render(snapshot, { width, frame, now, requests, cursor, selected, keys }) -> Line[]  (layout() also returns the
 * selectable rows)
 *   Rows, most actionable first, no blank lines: what needs the user (requests, human gates, stuck or stalled
 *   agents), the hub with the idle count, who works now, the task's progress in one row, spend and context in one
 *   row, and (panel only) one dim row of keys. Empty sections are left out. Improvement proposals waiting or applied
 *   (`learning.proposals`) are up to 3 rows after the needs list, then the commands that decide them; loop runs (`loops`,
 *   added by the plugin from `agentgraph loop list --json`) are up to 3 rows after those, then their commands.
 *   Line = Span[], Span = { text, tone }
 *   tone is one of TONES. No line is wider than `width` display columns.
 *
 * No OpenTUI or host imports: this module is shared by the plugin and the tests.
 * Every string from the document is untrusted: control characters are removed and nothing is interpreted.
 */

import { activityWords } from "./zones.js";

export const TONES = Object.freeze(["plain", "muted", "accent", "danger"]);

export const SPINNER = Object.freeze(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]);
export const MARK_HUB = "◉";
export const MARK_IDLE = "○";
export const MARK_SIDE = "◌";
export const SIDE_NAME = "side chat";
export const LOOP_MARK = "↻";
export const ATTENTION = "!";
export const SENT_BACK = "↩";
export const WAITING = "?";
export const BAR_FULL = "━";
export const BAR_EMPTY = "─";
export const ELLIPSIS = "…";

// Idle agents by group, in this order; unknown groups follow in document order.
export const GROUP_ORDER = Object.freeze(["council", "decide", "build", "verify", "understand"]);

const DEFAULT_WIDTH = 37;
export const NEEDS_MAX = 5;

// ---------------------------------------------------------------------------------------------------------------
// Display width

function isZeroWidth(cp) {
  return (
    (cp >= 0x0300 && cp <= 0x036f) ||
    (cp >= 0x0483 && cp <= 0x0489) ||
    (cp >= 0x0591 && cp <= 0x05bd) ||
    (cp >= 0x0610 && cp <= 0x061a) ||
    (cp >= 0x064b && cp <= 0x065f) ||
    (cp >= 0x1ab0 && cp <= 0x1aff) ||
    (cp >= 0x1dc0 && cp <= 0x1dff) ||
    (cp >= 0x200b && cp <= 0x200f) ||
    (cp >= 0x2028 && cp <= 0x202e) ||
    (cp >= 0x2060 && cp <= 0x2064) ||
    (cp >= 0x20d0 && cp <= 0x20ff) ||
    (cp >= 0xfe00 && cp <= 0xfe0f) ||
    (cp >= 0xfe20 && cp <= 0xfe2f) ||
    cp === 0xfeff ||
    (cp >= 0xe0100 && cp <= 0xe01ef)
  );
}

function isWide(cp) {
  return (
    (cp >= 0x1100 && cp <= 0x115f) ||
    (cp >= 0x231a && cp <= 0x231b) ||
    (cp >= 0x23e9 && cp <= 0x23ec) ||
    cp === 0x23f0 ||
    cp === 0x23f3 ||
    (cp >= 0x25fd && cp <= 0x25fe) ||
    (cp >= 0x2614 && cp <= 0x2615) ||
    (cp >= 0x2648 && cp <= 0x2653) ||
    cp === 0x267f ||
    cp === 0x2693 ||
    cp === 0x26a1 ||
    (cp >= 0x26aa && cp <= 0x26ab) ||
    (cp >= 0x26bd && cp <= 0x26be) ||
    (cp >= 0x26c4 && cp <= 0x26c5) ||
    cp === 0x26ce ||
    cp === 0x26d4 ||
    cp === 0x26ea ||
    (cp >= 0x26f2 && cp <= 0x26f3) ||
    cp === 0x26f5 ||
    cp === 0x26fa ||
    cp === 0x26fd ||
    cp === 0x2705 ||
    (cp >= 0x270a && cp <= 0x270b) ||
    cp === 0x2728 ||
    cp === 0x274c ||
    cp === 0x274e ||
    (cp >= 0x2753 && cp <= 0x2755) ||
    cp === 0x2757 ||
    (cp >= 0x2795 && cp <= 0x2797) ||
    cp === 0x27b0 ||
    cp === 0x27bf ||
    (cp >= 0x2b1b && cp <= 0x2b1c) ||
    cp === 0x2b50 ||
    cp === 0x2b55 ||
    (cp >= 0x2e80 && cp <= 0x303e) ||
    (cp >= 0x3041 && cp <= 0x33ff) ||
    (cp >= 0x3400 && cp <= 0x4dbf) ||
    (cp >= 0x4e00 && cp <= 0x9fff) ||
    (cp >= 0xa000 && cp <= 0xa4cf) ||
    (cp >= 0xa960 && cp <= 0xa97f) ||
    (cp >= 0xac00 && cp <= 0xd7a3) ||
    (cp >= 0xf900 && cp <= 0xfaff) ||
    (cp >= 0xfe10 && cp <= 0xfe19) ||
    (cp >= 0xfe30 && cp <= 0xfe6f) ||
    (cp >= 0xff00 && cp <= 0xff60) ||
    (cp >= 0xffe0 && cp <= 0xffe6) ||
    (cp >= 0x16fe0 && cp <= 0x18cff) ||
    (cp >= 0x1b000 && cp <= 0x1b2ff) ||
    cp === 0x1f004 ||
    cp === 0x1f0cf ||
    cp === 0x1f18e ||
    (cp >= 0x1f191 && cp <= 0x1f19a) ||
    (cp >= 0x1f200 && cp <= 0x1f2ff) ||
    (cp >= 0x1f300 && cp <= 0x1f64f) ||
    (cp >= 0x1f680 && cp <= 0x1f6ff) ||
    (cp >= 0x1f7e0 && cp <= 0x1f7eb) ||
    (cp >= 0x1f90c && cp <= 0x1f9ff) ||
    (cp >= 0x1fa70 && cp <= 0x1faff) ||
    (cp >= 0x20000 && cp <= 0x3fffd)
  );
}

export function charWidth(ch) {
  const cp = ch.codePointAt(0);
  if (cp === undefined) return 0;
  if (cp < 0x20 || (cp >= 0x7f && cp < 0xa0)) return 0;
  if (isZeroWidth(cp)) return 0;
  return isWide(cp) ? 2 : 1;
}

export function displayWidth(text) {
  let total = 0;
  for (const ch of String(text)) total += charWidth(ch);
  return total;
}

// Cut `text` to at most `max` columns, ending with "…" when something was cut.
export function truncate(text, max) {
  const value = String(text);
  if (max <= 0) return "";
  if (displayWidth(value) <= max) return value;
  if (max === 1) return ELLIPSIS;
  let out = "";
  let used = 0;
  for (const ch of value) {
    const w = charWidth(ch);
    if (used + w > max - 1) break;
    out += ch;
    used += w;
  }
  return out.replace(/\s+$/, "") + ELLIPSIS;
}

// Untrusted text -> one line of printable text (ANSI escapes and control characters removed).
export function clean(value) {
  if (value === undefined || value === null) return "";
  return String(value)
    // CSI / OSC / other escape sequences
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)?/g, "")
    .replace(/\u001b[@-_]?/g, "")
    .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function lineWidth(line) {
  let total = 0;
  for (const span of line) total += displayWidth(span.text);
  return total;
}

// Final safety: no line may be wider than `width`. Cuts at width-1 and ends with "…".
export function clipLine(line, width) {
  if (lineWidth(line) <= width) return line;
  if (width <= 0) return [];
  const out = [];
  let left = width - 1;
  for (const span of line) {
    const w = displayWidth(span.text);
    if (w <= left) {
      out.push(span);
      left -= w;
      continue;
    }
    let part = "";
    for (const ch of span.text) {
      const cw = charWidth(ch);
      if (cw > left) break;
      part += ch;
      left -= cw;
    }
    if (part) out.push({ text: part, tone: span.tone });
    break;
  }
  out.push({ text: ELLIPSIS, tone: out.length ? out[out.length - 1].tone : "plain" });
  return out;
}

// ---------------------------------------------------------------------------------------------------------------
// Document helpers

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function list(value) {
  return Array.isArray(value) ? value : [];
}

function busyCount(agent) {
  const n = Number(agent && agent.busy);
  return Number.isFinite(n) ? n : agent && agent.busy === true ? 1 : 0;
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function rounds(loop) {
  return Math.max(0, Math.floor(Number(loop && loop.rounds) || 0));
}

export function seatName(name) {
  return name.startsWith("councillor-") && name.length > "councillor-".length ? name.slice("councillor-".length) : name;
}

function validAgents(doc) {
  return list(doc.agents).filter((a) => isObject(a) && typeof a.name === "string" && a.name);
}

function findHub(doc, agents) {
  const rootAgent = isObject(doc.root) && typeof doc.root.agent === "string" ? doc.root.agent : "";
  const hubs = agents.filter((a) => a.group === "hub");
  return hubs.find((a) => a.name === rootAgent) || hubs[0] || null;
}

function hubWorking(doc, hub) {
  if (hub) return busyCount(hub) > 0;
  return isObject(doc.root) && doc.root.busy === true;
}

// True when anything is working: the hub or any other agent. Only then does anything animate.
// The side chat of a snapshot (WEB_API.md `sidechat`: { installed, session, model, busy, spent? }): null unless it is
// installed. It is not an agent: it is no row of the agents, not counted as working, and `isAnimated` never counts it.
const BIDI_MARKS = /[\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]/g; // direction marks and zero-width characters
export function sideChatOf(doc) {
  const side = isObject(doc) && isObject(doc.sidechat) ? doc.sidechat : null;
  if (!side || side.installed !== true) return null;
  const session = typeof side.session === "string" && /^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$/.test(side.session) ? side.session : null;
  return { session, model: typeof side.model === "string" ? clean(side.model.replace(BIDI_MARKS, "")) : "", busy: side.busy === true };
}

// The side chat is installed and answering: its moon moves, its row spins.
export const sideBusy = (doc) => {
  if (!isObject(doc) || (doc.error !== undefined && doc.error !== null)) return false;
  const side = sideChatOf(doc);
  return !!side && side.busy;
};

export function isAnimated(doc) {
  if (!isObject(doc) || (doc.error !== undefined && doc.error !== null)) return false;
  const agents = validAgents(doc);
  if (!agents.length) return false;
  return hubWorking(doc, findHub(doc, agents)) || agents.some((a) => busyCount(a) > 0);
}

// What the database confirms about a node (WEB_API.md `nodes[].liveness`): "live", "quiet", "orphan", "unreported",
// or nothing (an older snapshot, or no database). Only a live node, or one the snapshot says nothing about, is drawn
// as working; a quiet or orphan one is reported running only, and an unreported one is working though not marked.
export const unconfirmed = (node) => isObject(node) && node.status === "running" && (node.liveness === "quiet" || node.liveness === "orphan");
export const moves = (node) => isObject(node) && node.status === "running" && !unconfirmed(node);

// The plain words for a node that is not simply working, "" for any other. `generated` (epoch ms) is the snapshot's
// time, which the idle time is counted to.
export function liveWords(node, generated = null) {
  if (!isObject(node)) return "";
  if (unconfirmed(node)) {
    if (node.liveness === "orphan") return "reported running · no session";
    const since = epochMs(node.idle_since);
    return since !== null && generated !== null && generated >= since ? `reported running · idle ${formatElapsed(generated - since)}` : "reported running · idle";
  }
  return node.liveness === "unreported" ? "working · not marked running" : "";
}

function currentGoal(graph, name) {
  if (!isObject(graph)) return "";
  const nodes = list(graph.nodes).filter((n) => isObject(n) && n.agent === name);
  const node = nodes.find((n) => n.busy === true) || nodes.find(moves);
  return node ? clean(node.goal) : "";
}

function agentCost(agent) {
  let total = 0;
  for (const s of list(agent.sessions)) {
    const c = isObject(s) ? finiteNumber(s.cost) : null;
    if (c !== null) total += c;
  }
  return total;
}

// ---------------------------------------------------------------------------------------------------------------
// Number formatting

// 999 -> "999", 1000 -> "1.0k", 12400 -> "12.4k", 310000 -> "310k", 999999 -> "1.0M", 2100000 -> "2.1M"
export function formatTokens(value) {
  const n = Math.max(0, Number(value) || 0);
  if (n < 1000) return String(Math.round(n));
  const units = ["k", "M", "G"];
  let v = n / 1000;
  let i = 0;
  const shown = (x) => {
    const r = Math.round(x * 10) / 10;
    return r >= 100 ? Math.round(x) : r;
  };
  while (shown(v) >= 1000 && i < units.length - 1) {
    v /= 1000;
    i += 1;
  }
  const r = shown(v);
  return (r >= 100 ? String(r) : r.toFixed(1)) + units[i];
}

export function formatDollars(value) {
  const n = Math.max(0, Number(value) || 0);
  return "$" + n.toFixed(2);
}

// "12.4k" -> [{12.4, plain}, {k, muted}]
function numberSpans(text) {
  const m = /^(.*?)([a-zA-Z]*)$/.exec(text);
  return m[2] ? [sp(m[1], "plain"), sp(m[2], "muted")] : [sp(text, "plain")];
}

// ---------------------------------------------------------------------------------------------------------------
// Line builders

function sp(text, tone) {
  return { text, tone };
}

function spinnerGlyph(frame, offset = 0) {
  return SPINNER[(((frame + offset) % SPINNER.length) + SPINNER.length) % SPINNER.length];
}

export const WEAR_BAR_CELLS = 8;
export const SUMMARY_DOT = "•";
const SUMMARY_DOTS_MAX = 5;

// Share of the context window used: context / compact_at (or / window when compact_at is null); null if unknown.
export function wearShare(wear, ctx) {
  const compactAt = finiteNumber(wear.compact_at);
  const windowSize = finiteNumber(wear.window);
  const limit = compactAt !== null && compactAt > 0 ? compactAt : windowSize !== null && windowSize > 0 ? windowSize : null;
  return limit === null ? null : Math.max(0, ctx) / limit;
}

// Tone of the context bar's fill: muted below 60%, accent from 60%, danger from 85%. Never green.
export function wearTone(share) {
  return share >= 0.85 ? "danger" : share >= 0.6 ? "accent" : "muted";
}

function wearParts(wear, ctx) {
  const number = numberSpans(formatTokens(ctx));
  const share = wearShare(wear, ctx);
  const variants = [];
  if (share !== null) {
    let filled = Math.round(WEAR_BAR_CELLS * share);
    if (ctx > 0) filled = Math.max(1, filled);
    if (share < 1) filled = Math.min(filled, WEAR_BAR_CELLS - 1);
    filled = Math.max(0, Math.min(WEAR_BAR_CELLS, filled));
    const bar = [];
    if (filled > 0) bar.push(sp(BAR_FULL.repeat(filled), wearTone(share)));
    if (WEAR_BAR_CELLS - filled > 0) bar.push(sp(BAR_EMPTY.repeat(WEAR_BAR_CELLS - filled), "muted"));
    const withBar = [...bar, sp(" ", "plain"), ...number];
    const summaries = Math.max(0, Math.floor(Number(wear.summaries) || 0));
    if (summaries > 0) {
      variants.push([...withBar, sp(" ", "plain"), sp(SUMMARY_DOT.repeat(Math.min(SUMMARY_DOTS_MAX, summaries)) + summaries, "muted")]);
    }
    variants.push(withBar);
  }
  variants.push(number);
  return variants;
}

// Block 2: what needs the user, most urgent first, one line per agent.
function needsEntries(doc, listed) {
  const graph = isObject(doc.graph) ? doc.graph : null;
  if (!graph) return [];
  const entries = [];
  const seen = new Set();
  const take = (rank, agent, make) => {
    if (seen.has(agent)) return;
    seen.add(agent);
    entries.push({ rank, make });
  };
  const workRounds = (agent) => {
    let best = 0;
    for (const loop of list(graph.work_loops)) if (isObject(loop) && loop.agent === agent) best = Math.max(best, rounds(loop));
    return best;
  };

  // 0: stuck work loops
  for (const loop of list(graph.work_loops)) {
    if (!isObject(loop) || loop.state !== "stuck" || !listed.has(loop.agent)) continue;
    take(0, loop.agent, () => ({ kind: "stuck", agent: loop.agent, n: workRounds(loop.agent) }));
  }
  // 1: failed, blocked or stalled nodes
  for (const node of list(graph.nodes)) {
    if (!isObject(node) || !listed.has(node.agent)) continue;
    if (node.status !== "failed" && node.status !== "blocked" && node.stalled !== true) continue;
    const attempts = Math.max(0, Math.floor(Number(node.attempts) || 0));
    take(1, node.agent, () => ({ kind: "stuck", agent: node.agent, n: Math.max(workRounds(node.agent), attempts) }));
  }
  // 2: review loops that sent the work back
  for (const loop of list(graph.loops)) {
    if (!isObject(loop) || loop.state !== "sent_back") continue;
    if (!listed.has(loop.worker) || !listed.has(loop.evaluator)) continue;
    take(2, loop.worker, () => ({ kind: "sent_back", agent: loop.worker, n: rounds(loop), by: loop.evaluator }));
  }
  // 3: reported a pass that nobody has checked
  for (const loop of list(graph.work_loops)) {
    if (!isObject(loop) || loop.state !== "waiting" || !listed.has(loop.agent)) continue;
    take(3, loop.agent, () => ({ kind: "waiting", agent: loop.agent, n: rounds(loop) }));
  }
  // 4: reported running with no live session, or working without being marked running (a quiet line, not an alarm)
  for (const node of list(graph.nodes)) {
    if (!isObject(node) || !listed.has(node.agent) || node.stalled === true) continue;
    const words = liveWords(node, epochMs(doc.generated));
    if (words) take(4, node.agent + "\0" + node.id, () => ({ kind: "reported", agent: node.agent, words }));
  }
  return entries.sort((a, b) => a.rank - b.rank).map((e) => e.make());
}

// Shrink a list of names (longest first, never below `min`) until `total()` fits.
function fitNames(names, fixedWidth, width, min = 3) {
  const out = names.map((n) => clean(n) || "?");
  const w = () => fixedWidth + out.reduce((t, n) => t + displayWidth(n), 0);
  while (w() > width) {
    let longest = -1;
    for (let i = 0; i < out.length; i += 1) {
      if (displayWidth(out[i]) > min && (longest < 0 || displayWidth(out[i]) > displayWidth(out[longest]))) longest = i;
    }
    if (longest < 0) break;
    out[longest] = truncate(out[longest], displayWidth(out[longest]) - 1);
  }
  return out;
}

function needsLine(entry, width) {
  const loop = entry.n >= 2 ? ` ${LOOP_MARK}${entry.n}` : "";
  if (entry.kind === "stuck") {
    const [name] = fitNames([entry.agent], 2 + 6 + displayWidth(loop), width, 1);
    return [sp(`${ATTENTION} ${name} stuck${loop}`, "danger")];
  }
  if (entry.kind === "sent_back") {
    const [worker, by] = fitNames([entry.agent, seatName(entry.by)], 2 + 10 + displayWidth(loop) + 4, width);
    return [sp(`${SENT_BACK} ${worker} sent back${loop} by ${by}`, "accent")];
  }
  if (entry.kind === "reported") {
    const [name] = fitNames([entry.agent], 2 + 1 + displayWidth(entry.words), width, 1);
    return [sp(`${MARK_IDLE} ${name} ${entry.words}`, "muted")];
  }
  const [name] = fitNames([entry.agent], 2 + 8, width, 1);
  return [sp(`${WAITING} ${name} waiting`, "plain")];
}

// ---------------------------------------------------------------------------------------------------------------
// The list: what needs the user, who works now, the task's progress, spend and context, the keys.

export const WORKING_MAX = 5;
export const CURSOR = "›";
export const QUESTION = "?";
const MIN_WHAT = 12; // a goal narrower than this is left out
const SECTION_GAP = "  ";

// Cut `text` to at most `max` columns in the middle: "impl-supe…docious". Names are cut this way, so both ends stay.
export function truncateMiddle(text, max) {
  const value = String(text);
  if (max <= 0) return "";
  if (displayWidth(value) <= max) return value;
  if (max <= 3) return truncate(value, max);
  const chars = [...value];
  const keep = max - 1;
  const headMax = Math.ceil(keep / 2);
  let head = "";
  let used = 0;
  for (const ch of chars) {
    const w = charWidth(ch);
    if (used + w > headMax) break;
    head += ch;
    used += w;
  }
  const tailMax = keep - used;
  let tail = "";
  let tailUsed = 0;
  for (let i = chars.length - 1; i >= 0; i -= 1) {
    const w = charWidth(chars[i]);
    if (tailUsed + w > tailMax) break;
    tail = chars[i] + tail;
    tailUsed += w;
  }
  return head + ELLIPSIS + tail;
}

// 42000 -> "42s", 960000 -> "16m", 3900000 -> "1h5m", 43200000 -> "12h", 2 days -> "2d"
export function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(Number(ms) / 1000 || 0));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  if (h < 10) return h + "h" + (m % 60 ? (m % 60) + "m" : "");
  if (h < 48) return h + "h";
  return Math.floor(h / 24) + "d";
}

function epochMs(value) {
  const n = finiteNumber(value);
  if (n === null || n <= 0) return null;
  return n < 1e11 ? n * 1000 : n; // seconds (the snapshot's unit) or milliseconds
}

function sessionsOf(agent) {
  return list(agent && agent.sessions).filter((s) => isObject(s) && typeof s.id === "string" && s.id);
}

function newest(sessions) {
  let best = null;
  let bestAt = -Infinity;
  for (const s of sessions) {
    const at = epochMs(s.updated) ?? epochMs(s.created) ?? 0;
    if (!best || at > bestAt) {
      best = s;
      bestAt = at;
    }
  }
  return best;
}

// The session Enter opens for an agent (its busy one, else its newest) and the one stop and message act on (busy only).
function agentSessions(agent) {
  const all = sessionsOf(agent);
  const busy = newest(all.filter((s) => s.busy === true));
  const open = busy || newest(all);
  return { open: open ? open.id : null, busy: busy ? busy.id : null, busySession: busy };
}

function graphOf(doc) {
  return isObject(doc.graph) ? doc.graph : null;
}

function busyNodeOf(graph, name) {
  if (!graph) return null;
  const nodes = list(graph.nodes).filter((n) => isObject(n) && n.agent === name);
  return nodes.find((n) => n.busy === true) || nodes.find(moves) || null;
}

function roundsOf(graph, name) {
  let best = 0;
  if (!graph) return best;
  for (const loop of list(graph.work_loops)) if (isObject(loop) && loop.agent === name) best = Math.max(best, rounds(loop));
  for (const loop of list(graph.loops)) if (isObject(loop) && loop.worker === name) best = Math.max(best, rounds(loop));
  return best;
}

function displayName(agent) {
  const name = clean(agent.name) || "?";
  return agent.group === "council" ? seatName(name) : name;
}

// What an agent is on: its busy step's goal, else the title of its busy session.
function whatOf(graph, agent, node) {
  const goal = node ? clean(node.goal) : currentGoal(graph, agent.name);
  if (goal) return goal;
  const { busySession } = agentSessions(agent);
  return busySession ? clean(busySession.title) : "";
}

function sinceOf(node, agent) {
  const started = node ? epochMs(node.started) : null;
  if (started !== null) return started;
  const { busySession } = agentSessions(agent);
  return busySession ? epochMs(busySession.created) : null;
}

// Requests the host knows about (`requests`: [{ kind: "permission" | "question", session, count?, title? }]), then
// ready human gates, stuck loops, and failed, blocked or stalled steps. A gate that refuted is the review loop doing its
// job: it is on the progress line, not here. One entry per agent, most urgent first.
function needsItems(doc, agents, hub, requests) {
  const graph = graphOf(doc);
  const byName = new Map(agents.map((a) => [a.name, a]));
  const bySession = new Map();
  for (const a of agents) for (const s of sessionsOf(a)) bySession.set(s.id, a);
  const rootId = isObject(doc.root) && typeof doc.root.id === "string" ? doc.root.id : null;
  if (rootId && hub && !bySession.has(rootId)) bySession.set(rootId, hub);
  const items = [];
  const seen = new Set();
  const push = (who, item) => {
    if (seen.has(who)) return;
    seen.add(who);
    items.push(item);
  };
  const forAgent = (agent, rest) => {
    const node = busyNodeOf(graph, agent.name);
    const sessions = agentSessions(agent);
    return Object.assign({
      agent: agent.name, name: displayName(agent), hub: agent === hub, open: sessions.open, busy: agent !== hub ? sessions.busy : null,
      what: whatOf(graph, agent, node), since: busyCount(agent) > 0 ? sinceOf(node, agent) : null, rounds: roundsOf(graph, agent.name),
    }, rest);
  };

  for (const kind of ["permission", "question"]) {
    for (const r of list(requests)) {
      if (!isObject(r) || r.kind !== kind || typeof r.session !== "string" || !r.session) continue;
      const agent = bySession.get(r.session);
      const label = kind === "permission" ? "wants permission" : "asks you";
      const mark = kind === "permission" ? ATTENTION : QUESTION;
      const n = Math.max(1, Math.floor(Number(r.count) || 1));
      const what = clean(r.what) || (n > 1 ? `${n} requests` : "");
      if (agent) {
        const rest = { key: `${kind}:${r.session}`, kind, mark, tone: kind === "permission" ? "danger" : "accent", label, open: r.session };
        if (what) rest.what = what;
        push(agent.name, forAgent(agent, rest));
      } else {
        push("session:" + r.session, {
          key: `${kind}:${r.session}`, kind, mark, tone: kind === "permission" ? "danger" : "accent", agent: null, name: clean(r.title) || "a session",
          label, what, open: r.session, busy: null, since: null, hub: false,
        });
      }
    }
  }
  if (!graph) return items;
  const statusOf = new Map(list(graph.nodes).filter(isObject).map((n) => [n.id, n.status]));
  for (const node of list(graph.nodes)) {
    if (!isObject(node) || node.type !== "human") continue;
    const ready = node.status === "running" || (node.status === "pending" && list(node.depends_on).every((d) => statusOf.get(d) === "passed"));
    if (!ready) continue;
    push("human:" + node.id, {
      key: `human:${node.id}`, kind: "human", mark: QUESTION, tone: "accent", agent: null, name: "you", label: "to decide",
      what: clean(node.goal), open: rootId, busy: null, since: epochMs(node.started), hub: false,
    });
  }
  for (const loop of list(graph.work_loops)) {
    if (!isObject(loop) || loop.state !== "stuck" || !byName.has(loop.agent)) continue;
    push(loop.agent, forAgent(byName.get(loop.agent), { key: `stuck:${loop.agent}`, kind: "stuck", mark: ATTENTION, tone: "danger", label: "stuck" }));
  }
  for (const node of list(graph.nodes)) {
    if (!isObject(node) || !byName.has(node.agent)) continue;
    const stalled = node.stalled === true;
    if (!stalled && node.status !== "failed" && node.status !== "blocked") continue;
    if (!stalled && node.type === "gate" && node.status === "failed") continue; // a refuting gate: the progress line
    const label = stalled ? "stalled" : node.status;
    const agent = byName.get(node.agent);
    const attempts = Math.max(0, Math.floor(Number(node.attempts) || 0));
    push(node.agent, forAgent(agent, {
      key: `${label}:${node.agent}`, kind: label, mark: ATTENTION, tone: "danger", label, what: clean(node.goal) || whatOf(graph, agent, null),
      rounds: Math.max(roundsOf(graph, node.agent), attempts),
    }));
  }
  for (const node of list(graph.nodes)) {
    if (!isObject(node) || node.stalled === true) continue;
    const words = liveWords(node, epochMs(doc.generated));
    if (!words) continue;
    push("unconfirmed:" + node.id, {
      key: `unconfirmed:${node.id}`, kind: "unconfirmed", mark: MARK_IDLE, tone: "muted", agent: null, name: clean(node.agent) || "?", label: words,
      what: clean(node.goal), open: (typeof node.session === "string" && node.session) || rootId, busy: null, since: null, hub: false,
    });
  }
  return items;
}

// One row: "<mark> <name><tail>  <what>          <right>". The name is cut in the middle, the goal at its end; the
// goal goes when it would be narrower than MIN_WHAT, the right part when even the name does not fit.
function composeRow(width, { head, name, nameTone = "plain", tail = [], what = "", right = "", rightTone = "muted" }) {
  const headW = lineWidth(head);
  const tailW = lineWidth(tail);
  let rightText = right || "";
  const room = () => width - headW - tailW - (rightText ? 1 + displayWidth(rightText) : 0);
  const nameCap = Math.max(12, Math.floor(width * 0.4));
  if (room() < Math.min(displayWidth(name), nameCap)) rightText = ""; // the name before the right part
  const avail = Math.max(1, room());
  const nameMax = Math.min(avail, nameCap);
  const shown = truncateMiddle(name, nameMax);
  const line = [...head, sp(shown, nameTone), ...tail];
  const left = avail - displayWidth(shown);
  if (what && left >= SECTION_GAP.length + MIN_WHAT) line.push(sp(SECTION_GAP, "plain"), sp(truncate(what, left - SECTION_GAP.length), "muted"));
  if (rightText) {
    const gap = width - lineWidth(line) - displayWidth(rightText);
    line.push(sp(" ".repeat(Math.max(1, gap)), "plain"), sp(rightText, rightTone));
  }
  return line;
}

const elapsedText = (since, now) => (since !== null && since !== undefined && now !== null ? formatElapsed(now - since) : "");

function needsRow(item, width, now) {
  const loop = item.rounds >= 2 ? [sp(` ${LOOP_MARK}${item.rounds}`, "accent")] : [];
  return composeRow(width, {
    head: [sp(item.mark, item.tone), sp(" ", "plain")],
    name: item.name,
    tail: [sp(" " + item.label, item.tone), ...loop],
    what: item.what,
    right: elapsedText(item.since, now),
  });
}

// Improvement proposals (`learning.proposals`: id, title, state, verdict, newest first): the ones waiting for a decision
// (drafted, evaluated), then the applied, at most PROPOSALS_MAX rows, each "<mark> <title> <state> <verdict>  <id>", then
// one row with the commands. The rows are not selectable (a select-and-act pattern exists only for sessions and paths):
// the commands decide, in a shell, with the id the row shows.
export const PROPOSALS_MAX = 3;
export const PROPOSAL_COMMANDS = "agentgraph improve show|approve|reject|revert ID";
function proposalRows(doc, width) {
  const learning = isObject(doc.learning) ? doc.learning : null;
  const shown = list(learning && learning.proposals).filter((p) => isObject(p) && typeof p.id === "string" && p.id);
  const waiting = shown.filter((p) => p.state === "drafted" || p.state === "evaluated");
  const applied = shown.filter((p) => p.state === "applied");
  const rows = waiting.concat(applied);
  if (!rows.length) return [];
  const out = [];
  const take = rows.length > PROPOSALS_MAX + 1 ? rows.slice(0, PROPOSALS_MAX) : rows;
  for (const p of take) {
    const wait = p.state === "drafted" || p.state === "evaluated";
    const verdict = clean(p.verdict);
    out.push(composeRow(width, {
      head: [sp(wait ? QUESTION : MARK_IDLE, wait ? "accent" : "muted"), sp(" ", "plain")],
      name: clean(p.title) || clean(p.id),
      tail: [sp(" " + clean(p.state), wait ? "accent" : "muted"), ...(verdict ? [sp(" " + verdict, "muted")] : [])],
      what: clean(p.id),
    }));
  }
  if (take.length < rows.length) out.push([sp(`+${rows.length - take.length} more proposals`, "muted")]);
  out.push([sp(truncate(PROPOSAL_COMMANDS, width), "muted")]);
  return out;
}

// Loop runs (`doc.loops`: what `agentgraph loop list --json` prints, one state per run, newest first; the plugin adds it
// to the snapshot): the live ones first, then the rest, at most LOOPS_MAX rows, each "<mark> <flow> <status> <n>/<limit>
// <cost>  <run id>", then one row with the commands. Only a passed run is marked as a pass; an unknown cost says so.
// Not selectable, like the proposals: the commands decide, in a shell, with the id the row shows.
export const LOOPS_MAX = 3;
export const LOOP_COMMANDS = "agentgraph loop status|cancel RUN";
// A run that waits for a person, or is paused or interrupted, is answered or resumed in a shell with its id: one more row says how.
export const LOOP_PERSON_COMMANDS = "agentgraph loop answer|resume RUN";
const LOOP_STATUS = {
  running: ["running", "accent"], passed: ["passed", "plain"], failed: ["failed", "danger"], limit: ["at its limit", "muted"],
  invalid: ["not started", "danger"], check_error: ["check error", "danger"], inconclusive: ["inconclusive", "muted"],
  stalled: ["stalled", "danger"], agent_error: ["agent error", "danger"], cancelled: ["cancelled", "muted"], interrupted: ["interrupted", "muted"],
  waiting: ["waiting for you", "accent"], paused: ["paused", "muted"],
};
const LOOP_PERSON = new Set(["waiting", "paused", "interrupted"]);
function loopRows(doc, width) {
  const shown = list(doc.loops).filter((r) => isObject(r) && typeof r.run === "string" && r.run && typeof r.flow === "string" && r.flow && typeof r.status === "string");
  if (!shown.length) return [];
  const rows = shown.filter((r) => r.status === "running").concat(shown.filter((r) => r.status !== "running"));
  const out = [];
  const take = rows.length > LOOPS_MAX + 1 ? rows.slice(0, LOOPS_MAX) : rows;
  for (const r of take) {
    const [words, tone] = LOOP_STATUS[r.status] || [clean(r.status) || "unknown", "muted"];
    const used = Array.isArray(r.attempts) ? r.attempts.length : Math.max(0, Math.floor(Number(r.attempts) || 0));
    const limit = Math.floor(Number(isObject(r.limits) ? r.limits.attempts : r.limit) || 0);
    const spent = isObject(r.totals) ? r.totals.cost : r.cost;
    const known = spent !== null && spent !== undefined && spent !== "" && Number.isFinite(Number(spent));
    out.push(composeRow(width, {
      head: [sp(r.status === "running" ? LOOP_MARK : r.status === "passed" ? "✓" : r.status === "waiting" ? "?" : "–", tone), sp(" ", "plain")],
      name: clean(r.flow),
      tail: [sp(" " + words, tone), ...(r.trial === true ? [sp(" (trial)", "muted")] : []), sp(` ${used}/${limit} ${known ? formatDollars(spent) : "cost unknown"}`, "muted")],
      what: clean(r.run),
    }));
  }
  if (take.length < rows.length) out.push([sp(`+${rows.length - take.length} more loop runs`, "muted")]);
  out.push([sp(truncate(LOOP_COMMANDS, width), "muted")]);
  if (take.some((r) => LOOP_PERSON.has(r.status))) out.push([sp(truncate(LOOP_PERSON_COMMANDS, width), "muted")]);
  return out;
}

function hubRow(doc, hub, idle, width, frame) {
  const working = hubWorking(doc, hub);
  const rootName = isObject(doc.root) && typeof doc.root.agent === "string" ? doc.root.agent : "";
  const name = clean(hub ? hub.name : rootName) || "?";
  const title = isObject(doc.root) ? clean(doc.root.title) : "";
  return composeRow(width, {
    head: [sp(MARK_HUB, working ? "accent" : "muted"), sp(" ", "plain")],
    name,
    tail: working ? [sp(" ", "plain"), sp(spinnerGlyph(frame), "accent")] : [],
    what: title,
    right: idle > 0 ? `${idle} idle` : "",
  });
}

// The side chat's row, after the orchestrator's: the mark, the name, a spinner while it answers (the snapshot says busy, or
// a question of ours is out), and its model. It is not an agent: nobody counts it as working.
function sideRow(side, asking, width, frame) {
  const live = side.busy || asking;
  return composeRow(width, {
    head: [sp(MARK_SIDE, live ? "accent" : "muted"), sp(" ", "plain")],
    name: SIDE_NAME,
    tail: live ? [sp(" ", "plain"), sp(spinnerGlyph(frame), "accent")] : [],
    what: asking ? "answering" : side.model,
  });
}

function workingRow(item, width, frame, index, now) {
  const loop = item.rounds >= 2 ? [sp(` ${LOOP_MARK}${item.rounds}`, "accent")] : [];
  return composeRow(width, {
    head: [sp(spinnerGlyph(frame, index + 1), "accent"), sp(" ", "plain")],
    name: item.name,
    tail: loop,
    what: item.what,
    right: elapsedText(item.since, now),
  });
}

// The gate the task is at: among the gates (and human gates) that have not passed, the first whose producer (a node
// it depends on or sends work back to) is running or returned; else the first, in the graph's order. A refuted gate
// whose work is being redone is the review loop at work (the live tone); a gate that is blocked, failed, or refuted
// with nobody on it is the danger tone. `label: false` leaves out the word "gate".
function currentGate(graph) {
  const nodes = list(graph.nodes).filter(isObject);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const producing = (gate) => [...list(gate.depends_on), ...list(gate.feedback_to)].some((id) => {
    const n = byId.get(id);
    return !!n && (moves(n) || n.status === "returned" || n.busy === true);
  });
  const open = nodes.filter((n) => (n.type === "gate" || n.type === "human") && n.status !== "passed");
  const gate = open.find(producing) || open[0] || null;
  return gate ? { gate, reworked: producing(gate) } : null;
}

function gateSpans(graph, passed, total, label = true, generated = null) {
  if (graph.status === "passed" || (total > 0 && passed >= total)) return [sp("done", "muted")];
  const found = currentGate(graph);
  if (!found) return [];
  const { gate, reworked } = found;
  const who = gate.type === "human" ? "you" : clean(gate.agent) || "?";
  const word = (text) => (label ? [sp(text, "muted")] : []);
  if (gate.status === "failed" || gate.status === "blocked") {
    const verdict = clean(gate.verdict) || gate.status;
    const tone = gate.status === "failed" && verdict === "refuted" && reworked ? "accent" : "danger";
    return [...word("gate "), sp(who, "plain"), sp(" " + verdict, tone)];
  }
  if (unconfirmed(gate)) return [...word("gate "), sp(who, "plain"), sp(" " + liveWords(gate, generated), "muted")];
  if (gate.status === "running" || gate.status === "returned" || gate.busy === true) return [...word("gate "), sp(who, "plain"), sp(" checking", "accent")];
  if (gate.liveness === "unreported") return [...word("next gate "), sp(who, "plain"), sp(" " + liveWords(gate), "muted")];
  return [...word("next gate "), sp(who, "plain")];
}

export const PROGRESS_BAR_CELLS = 10;

function progressRow(doc, width) {
  const graph = graphOf(doc);
  const progress = graph && isObject(graph.progress) ? graph.progress : null;
  const total = progress ? Math.max(0, Math.floor(Number(progress.total) || 0)) : 0;
  if (!total) return null;
  const passed = Math.min(total, Math.max(0, Math.floor(Number(progress.passed) || 0)));
  let filled = Math.round((PROGRESS_BAR_CELLS * passed) / total);
  if (passed > 0) filled = Math.max(1, filled);
  if (passed < total) filled = Math.min(filled, PROGRESS_BAR_CELLS - 1);
  const bar = [];
  if (filled > 0) bar.push(sp(BAR_FULL.repeat(filled), "accent"));
  if (PROGRESS_BAR_CELLS - filled > 0) bar.push(sp(BAR_EMPTY.repeat(PROGRESS_BAR_CELLS - filled), "muted"));
  const count = [sp(String(passed), "plain"), sp("/" + total, "muted")];
  const generated = epochMs(doc.generated);
  const gate = gateSpans(graph, passed, total, true, generated);
  const task = clean(graph.task);
  const join = (...parts) => {
    const out = [];
    for (const p of parts.filter((x) => x && x.length)) out.push(...(out.length ? [sp(SECTION_GAP, "plain")] : []), ...p);
    return out;
  };
  const withBar = [...bar, sp(" ", "plain"), ...count];
  const candidates = [];
  if (task) {
    const taskRoom = width - lineWidth(join(withBar, gate)) - SECTION_GAP.length;
    if (taskRoom >= 8) candidates.push(join(withBar, [sp(truncateMiddle(task, taskRoom), "muted")], gate));
  }
  candidates.push(join(withBar, gate), join(count, gate), join(count, gateSpans(graph, passed, total, false, generated)), withBar, count);
  return candidates.find((c) => lineWidth(c) <= width) || count;
}

// The spend line (WEB_API.md `spend`: { limit, warn, rate, chat, workers }, dollars; rate in dollars an hour). Every part
// can be absent or null: it is then not shown. Limit: "$7.65 / $50"; from `warn` the amount takes the attention tone;
// at or over the limit the line says so ("at limit", "over limit"). Rate "$22/h" and the split "chat $3.10 · workers
// $4.55" show where there is width; they drop in this order: the rate, the split, the limit last.
const moneyOf = (value) => {
  const n = finiteNumber(value);
  return n !== null && n >= 0 ? n : null;
};
const limitText = (n) => "$" + (Number.isInteger(n) ? String(n) : n.toFixed(2));
const rateText = (n) => "$" + (n >= 10 ? String(Math.round(n)) : n.toFixed(1).replace(/\.0$/, "")) + "/h";

function spendParts(doc, cost) {
  const spend = isObject(doc.spend) ? doc.spend : null;
  const limit = spend ? moneyOf(spend.limit) : null;
  const warn = spend ? moneyOf(spend.warn) : null;
  const rate = spend ? moneyOf(spend.rate) : null;
  const chat = spend ? moneyOf(spend.chat) : null;
  const workers = spend ? moneyOf(spend.workers) : null;
  const hot = cost !== null && ((warn !== null && cost >= warn) || (limit !== null && limit > 0 && cost >= limit));
  const amount = cost !== null ? [sp(formatDollars(cost), hot ? "accent" : "plain")] : [];
  const plain = amount;
  let withLimit = amount;
  let limitOnly = amount; // without the words: for a line too narrow for them
  if (cost !== null && limit !== null && limit > 0) {
    limitOnly = [...amount, sp(" / " + limitText(limit), "muted")];
    withLimit = cost >= limit ? [...limitOnly, sp(cost > limit ? " over limit" : " at limit", "accent")] : limitOnly;
  }
  const split = [];
  if (chat !== null) split.push(`chat ${formatDollars(chat)}`);
  if (workers !== null) split.push(`workers ${formatDollars(workers)}`);
  return { plain, withLimit, limitOnly, rate: rate !== null ? [sp(rateText(rate), "muted")] : null, split: split.length ? [sp(split.join(" · "), "muted")] : null };
}

// The spend forms of a graph view's top line, richest first (limit + rate + split, limit + split, limit, the limit
// without its "over limit" words, the bare total), built on spendParts so the amounts, tones and number checks are the list line's. Null when the spend has
// nothing usable (no limit, rate, chat or workers) or there is no total: the caller then shows the bare total as before.
export function spendForms(doc, cost) {
  const spend = spendParts(doc, cost);
  if (!spend.plain.length || (spend.withLimit === spend.plain && !spend.rate && !spend.split)) return null;
  const join = (...parts) => {
    const out = [];
    for (const part of parts) {
      if (!part || !part.length) continue;
      if (out.length) out.push(sp(SECTION_GAP, "plain"));
      out.push(...part);
    }
    return out;
  };
  const forms = [];
  for (const set of [[spend.rate, spend.split], [spend.split], []]) forms.push(join(spend.withLimit, ...set.filter(Boolean)));
  forms.push(spend.limitOnly, spend.plain);
  return forms.filter((f, i) => i === 0 || lineWidth(f) < lineWidth(forms[i - 1]));
}

function presetSpans(doc, room) {
  const setup = isObject(doc.setup) ? doc.setup : null;
  const name = setup && typeof setup.preset === "string" ? clean(setup.preset.replace(BIDI_MARKS, "")) : "";
  if (!name) return null;
  const overridden = !!setup.override;
  const forms = overridden ? [`${name} (overridden)`, `${name}*`] : [name];
  const text = forms.find((f) => displayWidth(f) <= room);
  return text ? [sp(text, "muted")] : null;
}

function costRow(doc, width) {
  const totals = isObject(doc.totals) ? doc.totals : null;
  const cost = totals ? finiteNumber(totals.cost) : null;
  const root = isObject(doc.root) ? doc.root : null;
  const wear = root && isObject(root.wear) ? root.wear : null;
  const ctx = (wear ? finiteNumber(wear.context) : null) ?? (root ? finiteNumber(root.context) : null);
  const spend = spendParts(doc, cost);
  const label = sp("ctx ", "muted");
  const ctxParts = ctx === null ? [] : wear ? wearParts(wear, ctx).map((p) => [label, ...p]) : [[label, ...numberSpans(formatTokens(ctx))]];
  const joined = (...parts) => {
    const out = [];
    for (const part of parts) {
      if (!part || !part.length) continue;
      if (out.length) out.push(sp(SECTION_GAP, "plain"));
      out.push(...part);
    }
    return out;
  };
  // the richest line that fits: the ctx part as before, then the rate and the split (rate first to go), the limit last
  const extras = [[spend.rate, spend.split], [spend.split], []].map((set) => set.filter(Boolean));
  const tries = [];
  if (spend.withLimit.length) {
    for (const part of ctxParts) for (const set of extras) tries.push(joined(spend.withLimit, part, ...set));
    for (const set of extras) tries.push(joined(spend.withLimit, ...set));
  }
  for (const part of ctxParts) {
    for (const set of spend.plain.length ? extras : [[]]) tries.push(joined(spend.plain, part, ...set));
  }
  let line = tries.find((t) => t.length && lineWidth(t) <= width);
  if (!line) {
    if (spend.plain.length) line = spend.plain;
    else if (ctxParts.length) line = ctxParts[ctxParts.length - 1];
    else line = [];
  }
  const preset = line.length ? presetSpans(doc, width - lineWidth(line) - 2) : presetSpans(doc, width);
  if (!preset) return line.length ? line : null;
  if (!line.length) return preset;
  return [...line, sp(" ".repeat(Math.max(2, width - lineWidth(line) - lineWidth(preset))), "plain"), ...preset];
}

// ---------------------------------------------------------------------------------------------------------------
// Paths: the agent paths the user drew (snapshot `structure.flows`, WEB_API.md); `r` in the panel runs one.

const FLOW_ID = /^[a-z0-9][a-z0-9-]{0,79}$/;
// A path's rev as the back end makes it and checks it: exactly 16 lowercase hex digits (never starting with "-", so
// it cannot read as an option on the command line). Anything else is no rev.
export const FLOW_REV = /^[0-9a-f]{16}$/;
const PATH_ARROW = " → ";
const DASH = "–";

// The drawn paths of a snapshot: { id, path, name, stats, command } with a valid id and at least two agents.
export function flowsOf(doc) {
  const structure = isObject(doc) && isObject(doc.structure) ? doc.structure : null;
  return list(structure && structure.flows)
    .filter((f) => isObject(f) && typeof f.id === "string" && FLOW_ID.test(f.id) && list(f.path).length >= 2 && list(f.path).every((a) => typeof a === "string" && a))
    .map((f) => ({
      id: f.id,
      path: f.path.map((a) => clean(a)),
      name: typeof f.name === "string" && clean(f.name) ? clean(f.name) : null,
      stats: isObject(f.stats) ? f.stats : null,
      command: typeof f.command === "string" ? f.command : null,
      // the version the snapshot saw (a short hash of the path's file): `flow prompt --expect` refuses when it changed
      rev: typeof f.rev === "string" && FLOW_REV.test(f.rev) ? f.rev : null,
    }));
}

const lastRun = (flow) => (flow.stats ? epochMs(flow.stats.last) : null);

// Most recently run first (stats.last), then the paths that never ran, in the snapshot's order.
export function sortFlows(flows) {
  return list(flows)
    .map((f, i) => ({ f, i, last: lastRun(f) }))
    .sort((x, y) => (y.last ?? -Infinity) - (x.last ?? -Infinity) || x.i - y.i)
    .map((x) => x.f);
}

export const flowName = (flow) => flow.name || flow.id;

// "impl-loop  impl-openai → verifier → impl-openai" (the id when the path has no name).
export function flowLabel(flow) {
  return `${flowName(flow)}  ${flow.path.join(PATH_ARROW)}`;
}

// "3 runs · $1.20 avg · 2.0 rounds"; a dash for a field the stats do not have, "" without stats.
export function flowStatsText(stats) {
  if (!isObject(stats)) return "";
  const runs = finiteNumber(stats.runs);
  const avg = isObject(stats.cost) ? finiteNumber(stats.cost.avg) : null;
  const roundsAvg = isObject(stats.rounds) ? finiteNumber(stats.rounds.avg) : null;
  return [
    runs === null ? `${DASH} runs` : `${runs} ${runs === 1 ? "run" : "runs"}`,
    `${avg === null ? DASH : formatDollars(avg)} avg`,
    `${roundsAvg === null ? DASH : roundsAvg.toFixed(1)} rounds`,
  ].join(" · ");
}

// "3 paths · last impl-loop 2h ago": the count, and the path run last when the stats say. The name is cut in the
// middle when the row is narrow; then the time goes, then the name.
function pathsRow(flows, width, now) {
  const count = [sp(String(flows.length), "plain"), sp(flows.length === 1 ? " path" : " paths", "muted")];
  const last = sortFlows(flows)[0];
  const at = last ? lastRun(last) : null;
  const candidates = [];
  if (at !== null) {
    const ago = now !== null ? ` ${formatElapsed(now - at)} ago` : "";
    const lead = [...count, sp(" · last ", "muted")];
    const room = width - lineWidth(lead) - displayWidth(ago);
    if (room >= Math.min(4, displayWidth(flowName(last)))) candidates.push([...lead, sp(truncateMiddle(flowName(last), room), "plain"), ...(ago ? [sp(ago, "muted")] : [])]);
    const roomNoTime = width - lineWidth(lead);
    if (roomNoTime >= Math.min(4, displayWidth(flowName(last)))) candidates.push([...lead, sp(truncateMiddle(flowName(last), roomNoTime), "plain")]);
  }
  candidates.push(count);
  return candidates.find((c) => lineWidth(c) <= width) || count;
}

// The zone of a tool call (snapshot `agents[].activity.zone`, as the web view): one single-width glyph for each. The
// idea of zones and of a short trail of recent actions comes from AgentMove (MIT).
const ZONE_GLYPHS = Object.freeze({ web: "⊕", terminal: "❯", files: "▤", search: "⊙", thinking: "·", messaging: "»", tasks: "☰", spawn: "┬", tools: "◇" });
export const ZONE_VERBS = Object.freeze({ web: "browsing", terminal: "running", files: "reading", search: "searching", thinking: "thinking", messaging: "messaging", tasks: "planning", spawn: "starting", tools: "using" });
export const TOOL_VERBS = Object.freeze({
  webfetch: "fetching", shell: "running", bash: "running", execute: "running", read: "reading", write: "writing", edit: "editing", multiedit: "editing",
  patch: "editing", apply_patch: "editing", grep: "searching", glob: "searching", websearch: "searching", codesearch: "searching", question: "asking",
  skill: "loading", subagent: "starting", task: "starting",
});
const own = (o, k) => (typeof k === "string" && Object.hasOwn(o, k) ? o[k] : undefined); // never a name from the prototype
const TRAIL_MAX = 5;

// What a working agent is doing now, one line under its row: the tool it runs, or the first words of what it thinks
// or says (snapshot `agents[].activity`: { kind: "tool" | "reasoning" | "text", tool, text }). Null when it has none.
// With a zone (the hub's, from the back end) a tool shows its zone glyph, a verb and what it acts on (`target`): a site, a
// command, a path; without one the row is what it always was.
function activityRow(agent, width) {
  const a = agent && isObject(agent.activity) ? agent.activity : null;
  if (!a) return null;
  const kind = a.kind === "tool" || a.kind === "reasoning" || a.kind === "text" ? a.kind : null;
  if (!kind) return null;
  const text = clean(kind === "tool" ? a.tool : a.text);
  if (!text) return null;
  const glyph = kind === "tool" && own(ZONE_GLYPHS, a.zone);
  if (glyph) {
    const tool = text.toLowerCase();
    const known = own(TOOL_VERBS, tool);
    const verb = known || own(ZONE_VERBS, a.zone);
    const what = known ? verb : `${verb} ${tool}`;
    const target = clean(a.target);
    const room = width - 2 - displayWidth(glyph) - 1;
    if (room < 1) return null;
    const head = truncate(what, room);
    const left = room - displayWidth(head) - 3;
    const mark = a.status === "error" ? "danger" : "accent";
    return [sp("  ", "plain"), sp(glyph, mark), sp(` ${head}`, "muted"), ...(target && left >= 2 ? [sp(" · ", "muted"), sp(truncate(target, left), "plain")] : [])];
  }
  const mark = kind === "tool" ? "▸ " : kind === "reasoning" ? "· " : "";
  const room = width - 2 - displayWidth(mark);
  if (room < 1) return null;
  return [sp("  ", "plain"), ...(mark ? [sp(mark, "muted")] : []), sp(truncate(text, room), kind === "text" ? "plain" : "muted")];
}

// The hub's last few actions, newest first, one dim row of zone glyphs; a failed call has a "!" after its glyph. Null
// when it has none. Only as many as fit are drawn.
function trailRow(agent, width) {
  const a = agent && isObject(agent.activity) ? agent.activity : null;
  const known = a && own(ZONE_GLYPHS, a.zone);
  const recent = known && Array.isArray(a.recent) ? a.recent.filter(isObject).slice(0, TRAIL_MAX) : [];
  const spans = [];
  let used = 2;
  for (const item of recent) {
    const glyph = own(ZONE_GLYPHS, item.zone) || ZONE_GLYPHS.tools;
    const bad = item.status === "error";
    const need = displayWidth(glyph) + (bad ? 1 : 0) + (spans.length ? 1 : 0);
    if (used + need > width) break;
    if (spans.length) spans.push(sp(" ", "muted"));
    spans.push(sp(glyph, "muted"));
    if (bad) spans.push(sp("!", "danger"));
    used += need;
  }
  return spans.length ? [sp("  ", "plain"), ...spans] : null;
}

// The keys: [[key, label], ...] -> one muted line of the pairs that fit, in order.
function keysRow(keys, width) {
  let text = "";
  for (const pair of list(keys)) {
    if (!Array.isArray(pair) || pair.length < 2) continue;
    const piece = `${pair[0]} ${pair[1]}`;
    const next = text ? text + SECTION_GAP + piece : piece;
    if (displayWidth(next) > width) break;
    text = next;
  }
  return text ? [sp(text, "muted")] : null;
}

// ---------------------------------------------------------------------------------------------------------------
// The calm list (`styled: true`, the panel's text list): plain words, one line per agent, a lot of room. The main chat
// on top, then NEEDS YOU, AT WORK, then the idle agents on one dim line, one dim line of totals and the keys. Peach
// marks work, grey is idle, coral is only for what needs the user. Nothing moves. A dark theme gets these colours and a
// faint tint on the selected row; a light one gets the tones only. The rows, items and keys are the plain list's.

const CALM = Object.freeze({
  ink: [232, 224, 218], muted: [176, 163, 156], idle: [143, 129, 120], peach: [223, 161, 131], coral: [237, 149, 131], rule: [78, 68, 62], selected: [38, 33, 31],
});
const MARK_DOT = "●";
const ID_SELECTED = CALM.selected;

const hexOf = (c) => "#" + c.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
// A span with, on a dark theme only, a colour, a background and bold.
function ssp(text, tone, color, bold, bg) {
  const span = { text, tone };
  if (color) span.color = hexOf(color);
  if (bold) span.bold = true;
  if (bg) span.bg = hexOf(bg);
  return span;
}
const padRight = (text, n) => text + " ".repeat(Math.max(0, n - displayWidth(text)));
const fillTo = (line, width, bg) => (lineWidth(line) >= width ? line : [...line, ssp(" ".repeat(width - lineWidth(line)), "plain", null, false, bg)]);
const tint = (line, bg) => line.map((span) => (span.bg ? span : { ...span, bg: hexOf(bg) }));

// What a working agent is doing, in words, and when that began (ms, or null): the activity's own time, else the step's start.
function doingOf(agent, since) {
  const act = agent && isObject(agent.activity) ? agent.activity : null;
  const at = act ? epochMs(act.time) : null;
  return { words: activityWords(act), since: at !== null ? at : since };
}

// One row: "<mark> <name>  <words> · <age>          <spend>". The name is cut at its end, the words before the age,
// the spend goes first when there is no room for all of the words.
function calmRow(width, nameW, p) {
  const c = (v) => (p.dark ? v : null);
  const room0 = width - 1 - (2 + nameW + 2);
  const age = p.age || "";
  const full = p.words + (age ? " · " + age : "");
  const spend = p.spend || "";
  let showSpend = spend !== "" && room0 >= displayWidth(full) + 2 + displayWidth(spend);
  const room = room0 - (showSpend ? displayWidth(spend) + 2 : 0);
  let status = "";
  let ageText = "";
  if (room >= displayWidth(full)) {
    status = p.words;
    ageText = age ? " · " + age : "";
  } else {
    const suffix = age ? " · " + age : "";
    if (age && room - displayWidth(suffix) >= 6) {
      status = truncate(p.words, room - displayWidth(suffix));
      ageText = suffix;
    } else if (room >= 3) status = truncate(p.words, room);
  }
  const line = [
    ssp(p.mark, p.markTone, c(p.markColor), false),
    ssp(" ", "plain"),
    ssp(padRight(truncate(p.name, nameW), nameW), p.nameTone || "plain", c(p.nameColor || CALM.ink), p.bold),
    ssp("  ", "plain"),
  ];
  if (status) line.push(ssp(status, p.wordsTone, c(p.wordsColor)));
  if (ageText) line.push(ssp(ageText, "muted", c(CALM.idle)));
  if (showSpend) {
    const gap = width - 1 - lineWidth(line) - displayWidth(spend);
    line.push(ssp(" ".repeat(Math.max(1, gap)), "plain"), ssp(spend, "muted", c(CALM.idle)));
  }
  return line;
}

// A section label with a thin line after it: "NEEDS YOU ─────────".
function calmRule(label, width, dark, color) {
  const c = (v) => (dark ? v : null);
  const room = width - 1;
  const text = truncate(label, Math.max(1, room));
  const rest = room - displayWidth(text) - 1;
  return [ssp(text, color ? "danger" : "muted", c(color || CALM.muted), true), ...(rest >= 2 ? [ssp(" ", "plain"), ssp("─".repeat(rest), "muted", c(CALM.rule))] : [])];
}

// "Idle: a, b, c" or, when they do not fit, "Idle: a, b, +3 more".
function idleLine(names, width, dark) {
  const c = (v) => (dark ? v : null);
  const head = "Idle: ";
  const all = names.map((n) => clean(n) || "?");
  let text = head + all.join(", ");
  if (displayWidth(text) > width - 1) {
    let shown = [];
    for (let i = 0; i < all.length; i++) {
      const rest = all.length - i - 1;
      const next = head + [...shown, all[i]].join(", ") + (rest > 0 ? `, +${rest} more` : "");
      if (displayWidth(next) > width - 1) break;
      shown = [...shown, all[i]];
    }
    const more = all.length - shown.length;
    text = shown.length ? head + shown.join(", ") + (more > 0 ? `, +${more} more` : "") : truncate(head + all.join(", "), Math.max(1, width - 1));
  }
  return [ssp(text, "muted", c(CALM.idle))];
}

// "3 of 5 steps done · $4.20 in all": the task's steps and the whole spend, when there are any.
function totalsLine(doc, width, dark) {
  const graph = graphOf(doc);
  const progress = graph && isObject(graph.progress) ? graph.progress : null;
  const total = progress ? Math.max(0, Math.floor(Number(progress.total) || 0)) : 0;
  const passed = progress ? Math.min(total, Math.max(0, Math.floor(Number(progress.passed) || 0))) : 0;
  const cost = isObject(doc.totals) ? finiteNumber(doc.totals.cost) : null;
  const parts = [];
  if (total) parts.push(`${passed} of ${total} steps done`);
  if (cost !== null && cost > 0) parts.push(`${formatDollars(cost)} in all`);
  if (!parts.length) return null;
  return [ssp(truncate(parts.join(" · "), Math.max(1, width - 1)), "muted", dark ? CALM.idle : null)];
}

// The key hints: the key plain, its label dim, two spaces between the pairs, as many as fit.
function keysStyled(keys, width, dark) {
  const c = (v) => (dark ? v : null);
  const out = [];
  let used = 0;
  for (const pair of list(keys)) {
    if (!Array.isArray(pair) || pair.length < 2) continue;
    const key = String(pair[0]);
    const label = String(pair[1]);
    const need = (out.length ? 2 : 0) + displayWidth(key) + 1 + displayWidth(label);
    if (used + need > width) break;
    if (out.length) out.push(ssp("  ", "plain"));
    out.push(ssp(key, "plain", c(CALM.ink)), ssp(" " + label, "muted", c(CALM.idle)));
    used += need;
  }
  return out.length ? out : null;
}

// layout() for `styled: true`: same result shape.
function calmLayout(snapshot, agents, options, width, now) {
  const dark = options.dark !== false;
  const k = (v) => (dark ? v : null);
  const cursor = options.cursor === true && width > 2;
  const inner = cursor ? width - 1 : width;
  const generated = epochMs(snapshot.generated);
  const at = options.reduced === true ? generated ?? now : now ?? generated;
  const hub = findHub(snapshot, agents);
  const graph = graphOf(snapshot);
  const needs = needsItems(snapshot, agents, hub, options.requests);
  const shownNeeds = needs.length > NEEDS_MAX + 1 ? needs.slice(0, NEEDS_MAX) : needs;
  const flagged = new Set(needs.map((n) => n.agent).filter(Boolean));
  const hubBusy = !!hub && hubWorking(snapshot, hub);
  const hubFlagged = !!hub && flagged.has(hub.name);
  const working = agents
    .filter((a) => a !== hub && busyCount(a) > 0 && !flagged.has(a.name))
    .map((a, i) => {
      const node = busyNodeOf(graph, a.name);
      const sessions = agentSessions(a);
      return { agent: a, key: `agent:${a.name}`, name: displayName(a), open: sessions.open, busy: sessions.busy, what: whatOf(graph, a, node), since: sinceOf(node, a), order: i };
    })
    .sort((x, y) => (x.since ?? Infinity) - (y.since ?? Infinity) || x.order - y.order);
  const idle = agents.filter((a) => a !== hub && busyCount(a) <= 0 && !flagged.has(a.name));
  const side = sideChatOf(snapshot);
  const asking = options.asking === true;
  const hubName = displayName(hub || { name: isObject(snapshot.root) && typeof snapshot.root.agent === "string" ? snapshot.root.agent : "" });
  const rootId = isObject(snapshot.root) && typeof snapshot.root.id === "string" ? snapshot.root.id : null;
  const hubSessions = hub ? agentSessions(hub) : { open: null };
  const shownWorking = working.length > WORKING_MAX + 1 ? working.slice(0, WORKING_MAX) : working;

  const candidates = [];
  if (hub && !hubFlagged) candidates.push(`agent:${hub.name}`);
  for (const item of shownNeeds) candidates.push(item.key);
  for (const item of shownWorking) candidates.push(item.key);
  if (side) candidates.push("sidechat");
  const selected = candidates.includes(options.selected) ? options.selected : candidates.length ? candidates[0] : null;

  const nameCap = Math.max(8, Math.floor(inner * 0.34));
  const names = [hubName, ...shownNeeds.map((n) => n.name), ...shownWorking.map((w) => w.name), ...(side ? [SIDE_NAME] : [])];
  const nameW = Math.max(4, Math.min(nameCap, ...[Math.max(...names.map((n) => displayWidth(n)))]));
  const ageOf = (since) => (since !== null && since !== undefined && at !== null ? formatElapsed(at - since) : "");
  const spendOf = (agent) => {
    const cost = agent ? agentCost(agent) : 0;
    return cost > 0 ? formatDollars(cost) : "";
  };
  const detail = (text) => {
    const room = inner - 3;
    return room >= 4 && text ? [ssp("  ", "plain"), ssp(truncate(text, room), "muted", k(CALM.idle))] : null;
  };

  // rows: { line, item?, extra?, kind } ; kind "blank" and "totals" go first when the panel is short
  const rows = [];
  const blank = () => rows.push({ line: [ssp(" ", "plain")], kind: "blank" });
  const label = (text, color) => {
    if (rows.length) blank();
    rows.push({ line: calmRule(text, inner, dark, color), kind: "label" });
  };

  // the main chat
  {
    const c = hubFlagged ? CALM.coral : hubBusy ? CALM.peach : CALM.idle;
    const act = hub ? doingOf(hub, null) : { words: "", since: null };
    const hubNeed = hubFlagged ? needs.find((n) => n.agent === hub.name) : null;
    const words = hubNeed ? hubNeed.label : hubBusy ? act.words : "idle";
    const row = {
      mark: MARK_HUB, markTone: hubFlagged ? "danger" : hubBusy ? "accent" : "muted", markColor: c, name: hubName, bold: true,
      words, wordsTone: hubFlagged ? "danger" : hubBusy ? "accent" : "muted", wordsColor: hubFlagged ? CALM.coral : hubBusy ? CALM.peach : CALM.idle,
      age: hubBusy ? ageOf(act.since) : "", spend: spendOf(hub), dark,
    };
    const item = hub && !hubFlagged
      ? { key: `agent:${hub.name}`, kind: "hub", agent: hub.name, name: displayName(hub), hub: true, open: rootId || hubSessions.open, busy: null }
      : null;
    const a = hub && isObject(hub.activity) ? hub.activity : null;
    rows.push({ line: calmRow(inner, nameW, row), item, kind: "row", extra: hubBusy && a ? clean(a.target) : "" });
  }

  if (shownNeeds.length) label("NEEDS YOU", CALM.coral);
  for (const item of shownNeeds) {
    const who = item.agent ? agents.find((a) => a.name === item.agent) : null;
    const row = {
      mark: MARK_DOT, markTone: "danger", markColor: CALM.coral, name: item.name, words: item.label, wordsTone: "danger", wordsColor: CALM.coral,
      age: ageOf(item.since), spend: spendOf(who), dark,
    };
    rows.push({ line: calmRow(inner, nameW, row), item: { key: item.key, kind: item.kind, agent: item.agent || null, name: item.name, hub: !!item.hub, open: item.open, busy: item.busy }, kind: "row", extra: item.what });
  }
  if (shownNeeds.length < needs.length) rows.push({ line: [ssp(`+${needs.length - shownNeeds.length} more`, "muted", k(CALM.idle))], kind: "more" });

  const proposals = proposalRows(snapshot, inner);
  if (proposals.length) label("PROPOSALS");
  for (const line of proposals) rows.push({ line, kind: "misc" });
  const loops = loopRows(snapshot, inner);
  if (loops.length) label("LOOP RUNS");
  for (const line of loops) rows.push({ line, kind: "misc" });

  if (shownWorking.length) label("AT WORK");
  for (const item of shownWorking) {
    const act = doingOf(item.agent, item.since);
    const a = isObject(item.agent.activity) ? item.agent.activity : null;
    const row = {
      mark: MARK_DOT, markTone: "accent", markColor: CALM.peach, name: item.name, words: act.words, wordsTone: "accent", wordsColor: CALM.peach,
      age: ageOf(act.since), spend: spendOf(item.agent), dark,
    };
    rows.push({ line: calmRow(inner, nameW, row), item: { key: item.key, kind: "working", agent: item.agent.name, name: item.name, hub: false, open: item.open, busy: item.busy }, kind: "work", extra: (a && a.kind === "tool" && clean(a.target)) || item.what });
  }
  if (shownWorking.length < working.length) rows.push({ line: [ssp(`+${working.length - shownWorking.length} more at work`, "muted", k(CALM.idle))], kind: "more" });

  if (idle.length) {
    blank();
    rows.push({ line: idleLine(idle.map((a) => displayName(a)), inner, dark), kind: "idle" });
  }
  if (side) {
    const live = side.busy || asking;
    const row = {
      mark: MARK_SIDE, markTone: live ? "accent" : "muted", markColor: live ? CALM.peach : CALM.idle, name: SIDE_NAME, nameColor: CALM.muted,
      words: live ? "answering" : clean(side.model), wordsTone: live ? "accent" : "muted", wordsColor: live ? CALM.peach : CALM.idle, age: "", spend: "", dark,
    };
    if (!idle.length) blank();
    rows.push({ line: calmRow(inner, nameW, row), item: { key: "sidechat", kind: "sidechat", agent: null, name: SIDE_NAME, hub: false, open: side.session, busy: null, live }, kind: "row", extra: "" });
  }

  const flows = sortFlows(flowsOf(snapshot));
  if (flows.length && !needs.length && !working.length && !hubBusy) {
    label("PATHS");
    rows.push({ line: pathsRow(flows, inner, at), kind: "misc" });
  }

  const totals = totalsLine(snapshot, inner, dark);
  if (totals) {
    blank();
    rows.push({ line: totals, kind: "totals" });
  }

  // the selected row says more on a line of its own
  for (const r of rows) {
    if (r.item && r.item.key === selected && r.extra) {
      const line = detail(r.extra);
      if (line) r.under = { line, kind: "under", follow: r.item.key };
    }
  }
  // a short panel: the room between the sections goes first, then the totals, then the working agents from the last
  const keys = options.keys ? keysStyled(options.keys, inner, dark) : null;
  const limit = Number.isFinite(Number(options.height)) && options.height > 0 ? Math.floor(options.height) - 1 : Infinity;
  const count = () => rows.reduce((n, r) => n + 1 + (r.under ? 1 : 0), 0);
  const drop = (test) => {
    for (let i = rows.length - 1; i >= 0 && count() > limit; i--) if (test(rows[i], i)) rows.splice(i, 1);
  };
  if (count() > limit) {
    drop((r) => r.kind === "blank" && r !== rows[0]);
  }
  if (count() > limit) for (const r of rows) if (r.under && r.item.key !== selected) delete r.under;
  if (count() > limit) for (const r of rows) if (r.under) delete r.under;
  if (count() > limit) drop((r) => r.kind === "totals");
  if (count() > limit) drop((r) => r.kind === "misc");
  if (count() > limit) drop((r) => r.kind === "label");
  if (count() > limit) drop((r) => r.kind === "idle");
  if (count() > limit) drop((r) => r.kind === "more");
  if (count() > limit) drop((r) => r.kind === "work" && r.item.key !== selected);
  if (count() > limit) drop((r) => r.kind === "row" && r.item && r.item.key !== selected && r.item.kind !== "hub" && r.item.kind !== "sidechat");
  if (count() > limit) drop((r) => r.kind === "row" && r.item && r.item.key !== selected);
  if (count() > limit) drop((r) => r.kind === "row");

  const out = [];
  for (const r of rows) {
    out.push(r);
    if (r.under) out.push(r.under);
  }
  if (keys) {
    if (out.length < limit) out.push({ line: [ssp(" ", "plain")], kind: "blank" });
    out.push({ line: keys, kind: "keys" });
  }
  const keysAt = keys ? out.length - 1 : null;
  const lines = out.map((r, i) => {
    const mine = (r.item && r.item.key === selected) || (r.follow && r.follow === selected);
    const glow = dark && mine ? ID_SELECTED : null;
    const body = glow ? tint(fillTo(r.line, inner, glow), glow) : r.line;
    return cursor ? [glow ? ssp(r.item ? CURSOR : " ", "accent", k(CALM.peach), false, glow) : sp(mine && r.item ? CURSOR : " ", "accent"), ...body] : body;
  });
  const items = [];
  out.forEach((r, i) => {
    if (!r.item) return;
    const { key, kind, agent, name, hub: isHub, open, busy, live } = r.item;
    items.push(Object.assign({ key, line: i, kind, agent, name, hub: !!isHub, open: open || null, busy: busy || null }, live === undefined ? {} : { live }));
  });
  const finalSelected = items.some((i) => i.key === selected) ? selected : items.length ? items[0].key : null;
  const root = rootId || null;
  return { lines: lines.map((line) => clipLine(line, width)), items, selected: finalSelected, flows, root, keysLine: keysAt };
}

// ---------------------------------------------------------------------------------------------------------------
// Entry point

// layout(snapshot, { width, frame, now, requests, cursor, selected, keys, activity, trail }) -> { lines, items, selected, keysLine }
//   lines: the rows, each at most `width` columns. items: the rows that can be selected, in order, each
//   { key, line, kind, agent, name, hub, open, busy }: `open` the session Enter opens, `busy` the running session stop
//   and message act on (null for the hub and for agents that do not work). selected: the selected item's key (the
//   first item's when `selected` names none). With `cursor` every row has a one-column gutter, `›` on the selected
//   row; `keys` adds the dim key line ([[key, label], ...]; `keysLine` is its index in `lines`, else null); `activity`
//   adds one line under each working agent's row with what it is doing now; the hub's, when the back end gives it a zone, shows the
//   zone's glyph, a verb and the target, and a dim row of its last few actions follows unless `trail` is false. A side chat (snapshot `sidechat.installed`) is one
//   more row after the orchestrator's (kind "sidechat", `live` while it answers or `asking` says a question of ours is out). Also returned: `flows` (the drawn paths, most recently run
//   first) and `root` (the root session's id, the orchestrator's session, or null). With paths and nothing at work
//   (nothing needs the user, nobody works, the hub is idle) one row after the hub says how many and which ran last.
export function layout(snapshot, options = {}) {
  const width = Math.max(1, Math.floor(Number(options.width) || DEFAULT_WIDTH));
  const now = Number.isFinite(Number(options.now)) && options.now !== null && options.now !== undefined ? Number(options.now) : null;
  const frame = Number.isFinite(Number(options.frame))
    ? Math.floor(Number(options.frame))
    : now !== null ? Math.floor(now / 120) : 0;
  const finish = (lines) => lines.map((line) => clipLine(line, width));
  const single = (line) => ({ lines: finish([line]), items: [], selected: null, flows: [], root: null, keysLine: null });

  if (snapshot === undefined || snapshot === null) return single([sp("Loading", "muted")]);
  if (!isObject(snapshot)) return single([sp("Bad snapshot", "danger")]);
  if (snapshot.error !== undefined && snapshot.error !== null) {
    return single([sp(truncate(clean(snapshot.error) || "unknown error", width), "danger")]);
  }
  const agents = validAgents(snapshot);
  if (!agents.length) return single([sp("No agents", "muted")]);

  if (options.styled === true && width >= 30) return calmLayout(snapshot, agents, options, width, now);

  const cursor = options.cursor === true && width > 2;
  const inner = cursor ? width - 1 : width;
  const at = now ?? epochMs(snapshot.generated);
  const graph = graphOf(snapshot);
  const hub = findHub(snapshot, agents);
  const rows = []; // { line, item?, full? }

  // what needs the user, who works now: the hub, then the others (longest at it first); idle agents are a count on the hub's row
  const needs = needsItems(snapshot, agents, hub, options.requests);
  const shownNeeds = needs.length > NEEDS_MAX + 1 ? needs.slice(0, NEEDS_MAX) : needs;
  const flagged = new Set(needs.map((n) => n.agent).filter(Boolean));
  const working = agents
    .filter((a) => a !== hub && busyCount(a) > 0 && !flagged.has(a.name))
    .map((a, i) => {
      const node = busyNodeOf(graph, a.name);
      const sessions = agentSessions(a);
      return {
        key: `agent:${a.name}`, kind: "working", agent: a.name, name: displayName(a), hub: false, open: sessions.open, busy: sessions.busy,
        what: whatOf(graph, a, node), since: sinceOf(node, a), rounds: roundsOf(graph, a.name), order: i, doing: a,
      };
    })
    .sort((x, y) => (x.since ?? Infinity) - (y.since ?? Infinity) || x.order - y.order);
  const idle = agents.filter((a) => a !== hub && busyCount(a) <= 0 && !flagged.has(a.name)).length;
  const shownWorking = working.length > WORKING_MAX + 1 ? working.slice(0, WORKING_MAX) : working;
  const side = sideChatOf(snapshot);
  const asking = options.asking === true;
  const hubShown = !flagged.has(hub ? hub.name : "");
  const hubBusy = !!hub && hubWorking(snapshot, hub);

  // 1. what needs the user
  shownNeeds.forEach((item, i) => {
    rows.push({ line: needsRow(item, inner, at), item });
    const who = options.activity === true && item.agent && !item.hub ? agents.find((a) => a.name === item.agent) : null;
    const doing = who && busyCount(who) > 0 ? activityRow(who, inner) : null;
    if (doing) rows.push({ line: doing });
  });
  if (shownNeeds.length < needs.length) rows.push({ line: [sp(`+${needs.length - shownNeeds.length} more`, "muted")] });

  const proposals = proposalRows(snapshot, inner);
  for (const line of proposals) rows.push({ line });
  const loops = loopRows(snapshot, inner);
  for (const line of loops) rows.push({ line });

  // 2. who works now
  if (hubShown) {
    const rootId = isObject(snapshot.root) && typeof snapshot.root.id === "string" ? snapshot.root.id : null;
    const hubSessions = hub ? agentSessions(hub) : { open: null };
    rows.push({
      line: hubRow(snapshot, hub, idle, inner, frame),
      item: { key: `agent:${hub ? hub.name : "hub"}`, kind: "hub", agent: hub ? hub.name : null, name: hub ? displayName(hub) : "hub", hub: true, open: rootId || hubSessions.open, busy: null },
    });
  }
  if (options.activity === true && hub && hubShown && hubBusy) {
    const doing = activityRow(hub, inner);
    if (doing) rows.push({ line: doing });
    const earlier = doing && options.trail !== false ? trailRow(hub, inner) : null;
    if (earlier) rows.push({ line: earlier });
  }
  if (side) {
    rows.push({
      line: sideRow(side, asking, inner, frame),
      item: { key: "sidechat", kind: "sidechat", agent: null, name: SIDE_NAME, hub: false, open: side.session, busy: null, live: side.busy || asking },
    });
  }
  shownWorking.forEach((item, i) => {
    rows.push({ line: workingRow(item, inner, frame, i, at), item });
    const doing = options.activity === true ? activityRow(item.doing, inner) : null;
    if (doing) rows.push({ line: doing });
  });
  if (shownWorking.length < working.length) rows.push({ line: [sp(`+${working.length - shownWorking.length} more working`, "muted")] });
  // the hub needs the user, so its row is up there without the idle count: the count gets a row of its own
  if (hub && flagged.has(hub.name) && idle > 0) rows.push({ line: [sp(MARK_IDLE, "muted"), sp(` ${idle} idle`, "muted")] });

  // the paths, at rest only (busy, the rows above matter more)
  const flows = sortFlows(flowsOf(snapshot));
  if (flows.length && !needs.length && !working.length && !hubWorking(snapshot, hub)) {
    rows.push({ line: pathsRow(flows, inner, at) });
  }

  // 3. the task's progress, 4. spend and context, 5. the keys: one block under a rule
  const progress = progressRow(snapshot, inner);
  const cost = costRow(snapshot, inner);
  const keys = options.keys ? keysRow(options.keys, inner) : null;
  if (progress) rows.push({ line: progress });
  if (cost && cost.length) rows.push({ line: cost });
  const keysAt = keys ? rows.length : null;
  if (keys) rows.push({ line: keys });

  const selectable = rows.filter((r) => r.item);
  const selected = selectable.some((r) => r.item.key === options.selected) ? options.selected : selectable.length ? selectable[0].item.key : null;
  const lines = rows.map((r) => {
    const mine = r.item && r.item.key === selected;
    return cursor ? [sp(mine ? CURSOR : " ", "accent"), ...r.line] : r.line;
  });
  const items = [];
  rows.forEach((r, i) => {
    if (!r.item) return;
    const { key, kind, agent, name, hub: isHub, open, busy, live } = r.item;
    items.push(Object.assign({ key, line: i, kind, agent, name, hub: !!isHub, open: open || null, busy: busy || null }, live === undefined ? {} : { live }));
  });
  const root = isObject(snapshot.root) && typeof snapshot.root.id === "string" && snapshot.root.id ? snapshot.root.id : null;
  return { lines: finish(lines), items, selected, flows, root, keysLine: keysAt };
}

// render(snapshot, options) -> the lines of layout().
export function render(snapshot, options = {}) {
  return layout(snapshot, options).lines;
}

// Plain text of the lines (tests, `node -e` previews).
export function toText(lines) {
  return lines.map((line) => line.map((s) => s.text).join("")).join("\n");
}

// Shared with the full-panel scene (src/scene.js); not part of the list's behaviour.
export {
  isObject, list, busyCount, finiteNumber, rounds, validAgents, findHub, hubWorking, currentGoal, agentCost,
  numberSpans, spinnerGlyph, wearParts, needsEntries, needsLine,
};
