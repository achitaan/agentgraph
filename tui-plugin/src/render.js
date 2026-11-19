/*
 * Pure text rendering of an agentgraph snapshot document (WEB_API.md section 1) for a narrow terminal sidebar.
 *
 * render(snapshot, { width, frame, now }) -> Line[]
 *   Line = Span[] (an empty array is a blank line), Span = { text, tone }
 *   tone is one of TONES. No line is wider than `width` display columns.
 *
 * No OpenTUI or host imports: this module is shared by the plugin and the tests.
 * Every string from the document is untrusted: control characters are removed and nothing is interpreted.
 */

export const TONES = Object.freeze(["plain", "muted", "accent", "danger"]);

export const SPINNER = Object.freeze(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]);
export const MARK_WORKING = "●";
export const MARK_IDLE = "○";
export const LOOP_MARK = "↻";
export const REVIEW_MARK = "⇄";
export const STUCK_SUFFIX = "!";
export const BAR_FULL = "━";
export const BAR_EMPTY = "─";
export const ELLIPSIS = "…";

// Agents other than the hub, by group, in this order; unknown groups follow in document order.
export const GROUP_ORDER = Object.freeze(["council", "decide", "build", "verify", "understand"]);

const DEFAULT_WIDTH = 37;
const BAR_MAX = 10;
const BAR_MIN = 3;
const NAME_MIN = 6;
const EVALUATOR_MIN = 4;
const GOAL_MIN = 4; // columns for the goal text itself, after its leading space

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

export function seatName(name) {
  return name.startsWith("councillor-") && name.length > "councillor-".length ? name.slice("councillor-".length) : name;
}

function findHub(doc, agents) {
  const rootAgent = isObject(doc.root) && typeof doc.root.agent === "string" ? doc.root.agent : "";
  const hubs = agents.filter((a) => a.group === "hub");
  return hubs.find((a) => a.name === rootAgent) || hubs[0] || null;
}

// True when the hub is working (the only thing that animates).
export function isAnimated(doc) {
  if (!isObject(doc) || doc.error !== undefined && doc.error !== null) return false;
  const agents = list(doc.agents).filter((a) => isObject(a) && typeof a.name === "string");
  const hub = findHub(doc, agents);
  return Boolean(hub && busyCount(hub) > 0);
}

function currentGoal(graph, name) {
  if (!isObject(graph)) return "";
  const nodes = list(graph.nodes).filter((n) => isObject(n) && n.agent === name);
  const node = nodes.find((n) => n.busy === true) || nodes.find((n) => n.status === "running");
  return node ? clean(node.goal) : "";
}

// ---------------------------------------------------------------------------------------------------------------
// Line builders

function sp(text, tone) {
  return { text, tone };
}

function hubLine(doc, hub, width, frame) {
  const working = busyCount(hub) > 0;
  const mark = working ? SPINNER[((frame % SPINNER.length) + SPINNER.length) % SPINNER.length] : MARK_IDLE;
  let name = clean(hub.name) || "?";
  const graph = isObject(doc.graph) ? doc.graph : null;
  const progress = graph && isObject(graph.progress) ? graph.progress : null;
  const total = progress ? Math.max(0, Math.floor(Number(progress.total) || 0)) : 0;
  const passed = progress ? Math.min(total, Math.max(0, Math.floor(Number(progress.passed) || 0))) : 0;

  const head = [sp(mark, working ? "accent" : "muted"), sp(" ", "plain")];
  if (!total) return [...head, sp(truncate(name, width - 2), "plain")];

  const count = `${passed}/${total}`;
  const countW = displayWidth(count);
  let nameW = displayWidth(name);
  // Room after "<mark> <name>": " <bar> <count>" with a bar of at least BAR_MIN, else " <count>".
  if (2 + nameW + 1 + countW > width) {
    nameW = Math.max(Math.min(nameW, NAME_MIN), width - 2 - 1 - countW);
    name = truncate(name, nameW);
    nameW = displayWidth(name);
  }
  const room = width - 2 - nameW;
  const line = [...head, sp(name, "plain")];
  const barRoom = room - 1 - countW - 1; // spaces around the bar
  if (barRoom >= BAR_MIN) {
    const barW = Math.min(BAR_MAX, barRoom);
    const filled = Math.round((barW * passed) / total);
    const gap = room - barW - 1 - countW; // push bar and count to the right edge
    line.push(sp(" ".repeat(Math.max(1, gap)), "plain"));
    if (filled) line.push(sp(BAR_FULL.repeat(filled), "accent"));
    if (barW - filled) line.push(sp(BAR_EMPTY.repeat(barW - filled), "muted"));
    line.push(sp(" ", "plain"), sp(count, passed === total ? "plain" : "muted"));
  } else if (room >= 1 + countW) {
    line.push(sp(" ".repeat(Math.max(1, room - countW)), "plain"), sp(count, "muted"));
  }
  return line;
}

function loopsFor(name, graph, listed) {
  const work = [];
  const review = [];
  if (!isObject(graph)) return { work, review };
  for (const loop of list(graph.work_loops)) {
    if (!isObject(loop) || loop.agent !== name) continue;
    const rounds = Math.max(0, Math.floor(Number(loop.rounds) || 0));
    const stuck = loop.state === "stuck";
    if (!stuck && rounds < 2) continue;
    const text = LOOP_MARK + (rounds >= 2 ? String(rounds) : "") + (stuck ? STUCK_SUFFIX : "");
    work.push(sp(text, stuck ? "danger" : loop.active === true || loop.state === "working" ? "accent" : "muted"));
  }
  for (const loop of list(graph.loops)) {
    if (!isObject(loop) || loop.worker !== name) continue;
    if (typeof loop.evaluator !== "string" || !listed.has(loop.evaluator) || !listed.has(loop.worker)) continue;
    review.push({ evaluator: clean(seatName(loop.evaluator)), active: loop.active === true });
  }
  return { work, review };
}

function agentLine(agent, doc, listed, width, { indent = "", short = false } = {}) {
  const graph = isObject(doc.graph) ? doc.graph : null;
  const working = busyCount(agent) > 0;
  const raw = clean(short ? seatName(agent.name) : agent.name) || "?";
  const { work, review } = listed.has(agent.name) ? loopsFor(agent.name, graph, listed) : { work: [], review: [] };
  const goal = working ? currentGoal(graph, agent.name) : "";

  const fixed = displayWidth(indent) + 2; // "<mark> "
  let nameW = displayWidth(raw);
  const evalW = review.map((r) => displayWidth(r.evaluator));
  let workKeep = work.length;
  let reviewKeep = review.length;

  const total = () => {
    let t = fixed + nameW;
    for (let i = 0; i < workKeep; i += 1) t += 1 + displayWidth(work[i].text);
    for (let i = 0; i < reviewKeep; i += 1) t += 1 + displayWidth(REVIEW_MARK) + 1 + evalW[i];
    return t;
  };
  // Shrink in order: evaluator names, agent name (to NAME_MIN), drop review loops, drop work loops, name to 1.
  while (total() > width) {
    let longest = -1;
    for (let i = 0; i < reviewKeep; i += 1) if (evalW[i] > EVALUATOR_MIN && (longest < 0 || evalW[i] > evalW[longest])) longest = i;
    if (longest >= 0) { evalW[longest] -= 1; continue; }
    if (nameW > NAME_MIN) { nameW -= 1; continue; }
    if (reviewKeep) { reviewKeep -= 1; continue; }
    if (workKeep) { workKeep -= 1; continue; }
    if (nameW > 1) { nameW -= 1; continue; }
    break;
  }

  const line = [];
  if (indent) line.push(sp(indent, "plain"));
  line.push(sp(working ? MARK_WORKING : MARK_IDLE, working ? "accent" : "muted"), sp(" ", "plain"));
  line.push(sp(truncate(raw, nameW), working ? "plain" : "muted"));
  for (let i = 0; i < workKeep; i += 1) line.push(sp(" ", "plain"), work[i]);
  for (let i = 0; i < reviewKeep; i += 1) {
    const tone = review[i].active ? "accent" : "muted";
    line.push(sp(" ", "plain"), sp(REVIEW_MARK + " " + truncate(review[i].evaluator, evalW[i]), tone));
  }
  const left = width - lineWidth(line) - 1;
  if (goal && left >= GOAL_MIN) line.push(sp(" ", "plain"), sp(truncate(goal, left), "muted"));
  return line;
}

// ---------------------------------------------------------------------------------------------------------------
// Entry point

export function render(snapshot, options = {}) {
  const width = Math.max(1, Math.floor(Number(options.width) || DEFAULT_WIDTH));
  const now = Number(options.now);
  const frame = Number.isFinite(Number(options.frame))
    ? Math.floor(Number(options.frame))
    : Number.isFinite(now) ? Math.floor(now / 120) : 0;
  const finish = (lines) => lines.map((line) => clipLine(line, width));

  if (snapshot === undefined || snapshot === null) return finish([[sp("Loading", "plain")]]);
  if (!isObject(snapshot)) return finish([[sp("Bad snapshot", "danger")]]);
  if (snapshot.error !== undefined && snapshot.error !== null) {
    return finish([[sp(truncate(clean(snapshot.error) || "unknown error", width), "danger")]]);
  }

  const agents = list(snapshot.agents).filter((a) => isObject(a) && typeof a.name === "string" && a.name);
  if (!agents.length) return finish([[sp("No agents", "muted")]]);
  const listed = new Set(agents.map((a) => a.name));

  const lines = [];
  const hub = findHub(snapshot, agents);
  if (hub) lines.push(hubLine(snapshot, hub, width, frame));

  // Groups other than the hub's own agent.
  const byGroup = new Map();
  for (const agent of agents) {
    if (agent === hub) continue;
    const group = typeof agent.group === "string" && agent.group ? agent.group : "build";
    if (!byGroup.has(group)) byGroup.set(group, []);
    byGroup.get(group).push(agent);
  }
  const order = [];
  if (byGroup.has("hub")) order.push("hub"); // another hub agent that is busy: right under the hub
  for (const g of GROUP_ORDER) if (byGroup.has(g)) order.push(g);
  for (const g of byGroup.keys()) if (!order.includes(g)) order.push(g);

  for (const group of order) {
    const members = byGroup.get(group);
    const block = [];
    if (group === "council") {
      const lead = members.find((a) => a.name === "council");
      const seats = members.filter((a) => lead && a !== lead && a.parent === lead.name);
      const others = members.filter((a) => a !== lead && !seats.includes(a));
      if (lead) {
        block.push(agentLine(lead, snapshot, listed, width));
        for (const seat of seats) block.push(agentLine(seat, snapshot, listed, width, { indent: "  ", short: true }));
      }
      for (const a of others) block.push(agentLine(a, snapshot, listed, width, { short: true }));
    } else {
      for (const a of members) block.push(agentLine(a, snapshot, listed, width));
    }
    if (block.length) {
      if (lines.length) lines.push([]);
      lines.push(...block);
    }
  }
  return finish(lines);
}

// Plain text of the lines (tests, `node -e` previews).
export function toText(lines) {
  return lines.map((line) => line.map((s) => s.text).join("")).join("\n");
}
