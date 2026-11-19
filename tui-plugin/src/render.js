/*
 * Pure text rendering of an agentgraph snapshot document (WEB_API.md section 1) for a narrow terminal sidebar.
 *
 * render(snapshot, { width, frame, now }) -> Line[]
 *   Blocks, separated by one blank line, empty ones omitted: Run, Needs you, Working, Idle.
 *   Line = Span[] (an empty array is a blank line), Span = { text, tone }
 *   tone is one of TONES. No line is wider than `width` display columns.
 *
 * No OpenTUI or host imports: this module is shared by the plugin and the tests.
 * Every string from the document is untrusted: control characters are removed and nothing is interpreted.
 */

export const TONES = Object.freeze(["plain", "muted", "accent", "danger"]);

export const SPINNER = Object.freeze(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]);
export const MARK_HUB = "◉";
export const MARK_IDLE = "○";
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
const NEEDS_MAX = 5;

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
export function isAnimated(doc) {
  if (!isObject(doc) || (doc.error !== undefined && doc.error !== null)) return false;
  const agents = validAgents(doc);
  if (!agents.length) return false;
  return hubWorking(doc, findHub(doc, agents)) || agents.some((a) => busyCount(a) > 0);
}

function currentGoal(graph, name) {
  if (!isObject(graph)) return "";
  const nodes = list(graph.nodes).filter((n) => isObject(n) && n.agent === name);
  const node = nodes.find((n) => n.busy === true) || nodes.find((n) => n.status === "running");
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

// Block 1: hub, progress, spend.
function runBlock(doc, hub, width, frame) {
  const working = hubWorking(doc, hub);
  const rootName = isObject(doc.root) && typeof doc.root.agent === "string" ? doc.root.agent : "";
  const name = clean(hub ? hub.name : rootName) || "?";
  const graph = isObject(doc.graph) ? doc.graph : null;
  const progress = graph && isObject(graph.progress) ? graph.progress : null;
  const total = progress ? Math.max(0, Math.floor(Number(progress.total) || 0)) : 0;
  const passed = progress ? Math.min(total, Math.max(0, Math.floor(Number(progress.passed) || 0))) : 0;
  const lines = [];

  // line 1
  const count = total ? [sp(String(passed), "plain"), sp("/" + total, "muted")] : [];
  const countW = total ? displayWidth(`${passed}/${total}`) : 0;
  const tail = working ? 2 : 0; // " <spinner>"
  let room = width - 2 - tail - (countW ? 1 + countW : 0);
  let showCount = countW > 0;
  if (showCount && room < 3) {
    showCount = false;
    room = width - 2 - tail;
  }
  const shown = truncate(name, Math.max(1, room));
  const line = [sp(MARK_HUB, working ? "accent" : "muted"), sp(" ", "plain"), sp(shown, "plain")];
  if (working) line.push(sp(" ", "plain"), sp(spinnerGlyph(frame), "accent"));
  if (showCount) {
    const gap = width - lineWidth(line) - countW;
    line.push(sp(" ".repeat(Math.max(1, gap)), "plain"), ...count);
  }
  lines.push(line);

  // line 2
  if (total) {
    let filled = Math.round((width * passed) / total);
    if (passed > 0) filled = Math.max(1, filled);
    if (passed < total) filled = Math.min(filled, width - 1);
    const bar = [];
    if (filled > 0) bar.push(sp(BAR_FULL.repeat(filled), "accent"));
    if (width - filled > 0) bar.push(sp(BAR_EMPTY.repeat(width - filled), "muted"));
    lines.push(bar);
  }

  // line 3
  const totals = isObject(doc.totals) ? doc.totals : null;
  if (totals) {
    const cost = finiteNumber(totals.cost);
    const tin = finiteNumber(totals.tokens_in);
    const tout = finiteNumber(totals.tokens_out);
    const root = isObject(doc.root) ? doc.root : null;
    const wear = root && isObject(root.wear) ? root.wear : null;
    const ctx = (wear ? finiteNumber(wear.context) : null) ?? (root ? finiteNumber(root.context) : null);
    const parts = [];
    if (cost !== null) parts.push([sp(formatDollars(cost), "plain")]);
    const tokens = [];
    if (tin !== null) tokens.push([...numberSpans(formatTokens(tin)), sp("↑", "muted")]);
    if (tout !== null) tokens.push([...numberSpans(formatTokens(tout)), sp("↓", "muted")]);
    const tokenSpans = [];
    tokens.forEach((t, i) => tokenSpans.push(...(i ? [sp(" ", "plain")] : []), ...t));
    if (tokenSpans.length) parts.push(tokenSpans);
    const join = (items, sep) => {
      const out = [];
      items.forEach((p, i) => out.push(...(i ? [sp(sep, "plain")] : []), ...p));
      return out;
    };
    // The context part, widest first: with `wear`, bar + number + summary dots, then without the dots, then the
    // number alone; without `wear`, "ctx 124k" as before.
    const ctxParts = ctx === null ? [] : wear ? wearParts(wear, ctx) : [[sp("ctx ", "muted"), ...numberSpans(formatTokens(ctx))]];
    let spend = null;
    for (const part of ctxParts) {
      const candidate = join([...parts, part], "  ");
      if (lineWidth(candidate) <= width) {
        spend = candidate;
        break;
      }
    }
    if (!spend) {
      const all = ctxParts.length ? [...parts, ctxParts[ctxParts.length - 1]] : parts;
      spend = join(all, "  ");
      if (lineWidth(spend) > width && ctx !== null) spend = join(all.slice(0, -1), "  ");
      if (lineWidth(spend) > width) spend = join(all.slice(0, -1).length ? all.slice(0, -1) : all, " ");
    }
    if (spend.length) lines.push(spend);
    if (wear && wear.level === "worn") {
      const n = Math.max(0, Math.floor(Number(wear.summaries) || 0));
      lines.push([sp(truncate(`summarized ${n} times`, width), "muted")]);
    }
  }
  return lines;
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
  const [name] = fitNames([entry.agent], 2 + 8, width, 1);
  return [sp(`${WAITING} ${name} waiting`, "plain")];
}

function needsBlock(doc, listed, width) {
  const entries = needsEntries(doc, listed);
  if (!entries.length) return [];
  const lines = entries.slice(0, NEEDS_MAX).map((e) => needsLine(e, width));
  if (entries.length > NEEDS_MAX) lines.push([sp(`+${entries.length - NEEDS_MAX} more`, "muted")]);
  return lines;
}

// Block 3: working agents, most expensive first, two lines each.
function workingBlock(doc, hub, agents, width, frame) {
  const graph = isObject(doc.graph) ? doc.graph : null;
  const busy = agents
    .filter((a) => a !== hub && busyCount(a) > 0)
    .map((a, i) => ({ a, i, cost: agentCost(a) }))
    .sort((x, y) => y.cost - x.cost || x.i - y.i);
  const lines = [];
  busy.forEach(({ a, cost }, index) => {
    const raw = clean(a.group === "council" ? seatName(a.name) : a.name) || "?";
    let n = 0;
    for (const loop of list(graph && graph.work_loops)) if (isObject(loop) && loop.agent === a.name) n = Math.max(n, rounds(loop));
    let loopText = n >= 2 ? ` ${LOOP_MARK}${n}` : "";
    let costText = formatDollars(cost);
    // "<glyph> <name><loop>" + gap + cost
    let room = width - 2 - displayWidth(loopText) - 1 - displayWidth(costText);
    if (room < 4 && loopText) {
      loopText = "";
      room = width - 2 - 1 - displayWidth(costText);
    }
    if (room < 2) {
      costText = "";
      room = width - 2 - displayWidth(loopText);
    }
    const name = truncate(raw, Math.max(1, room));
    const line = [sp(spinnerGlyph(frame, index), "accent"), sp(" ", "plain"), sp(name, "plain")];
    if (loopText) line.push(sp(loopText, "accent"));
    if (costText) {
      const gap = width - lineWidth(line) - displayWidth(costText);
      line.push(sp(" ".repeat(Math.max(1, gap)), "plain"), sp(costText, "muted"));
    }
    lines.push(line);
    const goal = currentGoal(graph, a.name);
    if (goal && width > 2) lines.push([sp("  ", "plain"), sp(truncate(goal, width - 2), "muted")]);
  });
  return lines;
}

// Block 4: everything idle, one line per group.
function idleBlock(agents, hub, width) {
  const byGroup = new Map();
  for (const agent of agents) {
    if (agent === hub || busyCount(agent) > 0) continue;
    const group = typeof agent.group === "string" && agent.group ? agent.group : "build";
    if (!byGroup.has(group)) byGroup.set(group, []);
    byGroup.get(group).push(agent);
  }
  const order = GROUP_ORDER.filter((g) => byGroup.has(g));
  for (const g of byGroup.keys()) if (!order.includes(g)) order.push(g);

  const lines = [];
  for (const group of order) {
    let members = byGroup.get(group);
    if (group === "council") {
      const lead = members.find((a) => a.name === "council");
      if (lead) members = [lead, ...members.filter((a) => a !== lead)];
    }
    const names = members.map((a) => clean(group === "council" ? seatName(a.name) : a.name) || "?");
    let prefix = MARK_IDLE + " ";
    let current = "";
    const flush = () => {
      lines.push([sp(prefix + current, "muted")]);
      prefix = "  ";
      current = "";
    };
    for (const raw of names) {
      const name = truncate(raw, Math.max(1, width - 2));
      if (current && displayWidth(prefix + current) + 1 + displayWidth(name) > width) flush();
      current = current ? current + " " + name : name;
    }
    if (current) flush();
  }
  return lines;
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

  if (snapshot === undefined || snapshot === null) return finish([[sp("Loading", "muted")]]);
  if (!isObject(snapshot)) return finish([[sp("Bad snapshot", "danger")]]);
  if (snapshot.error !== undefined && snapshot.error !== null) {
    return finish([[sp(truncate(clean(snapshot.error) || "unknown error", width), "danger")]]);
  }

  const agents = validAgents(snapshot);
  if (!agents.length) return finish([[sp("No agents", "muted")]]);
  const listed = new Set(agents.map((a) => a.name));
  const hub = findHub(snapshot, agents);

  const lines = [];
  const add = (block) => {
    if (!block.length) return;
    if (lines.length) lines.push([]);
    lines.push(...block);
  };
  add(runBlock(snapshot, hub, width, frame));
  add(needsBlock(snapshot, listed, width));
  add(workingBlock(snapshot, hub, agents, width, frame));
  add(idleBlock(agents, hub, width));
  return finish(lines);
}

// Plain text of the lines (tests, `node -e` previews).
export function toText(lines) {
  return lines.map((line) => line.map((s) => s.text).join("")).join("\n");
}
