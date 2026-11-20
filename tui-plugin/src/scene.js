/*
 * The orbit: a full-panel animated picture of an agentgraph snapshot (WEB_API.md section 1) for the session panel.
 *
 * scene(snapshot, { width, height, frame, now, previous, view, change, colors }) -> Line[]
 *   Exactly `height` lines, each exactly `width` display cells, in the Line shape render() returns: Span[] with
 *   Span = { text, tone, color? }, tone one of plain, muted, accent, danger, and `color` an optional "#rrggbb" the
 *   host should use instead of the tone's colour (the group colours). Neighbouring cells of one style are one span.
 *   now       the clock in ms (default frame * 100)
 *   previous  { doc, at }: the document drawn before this one and when this one arrived (the spend bars tween)
 *   view      "2d" (default): the ring seen from above, in braille dots
 *             "3d": the same system as a turning solid figure, drawn with characters chosen by depth
 *   change    { from, at }: the camera moves from view `from` to `view`, starting at `at` (ms), for CHANGE_MS
 *   colors    { dark, muted }: the theme's background is dark (default) or light, and its muted colour "#rrggbb"
 *   orbit     { turn, elevation, resume }: the 3D camera turned by hand (orbitCamera, orbitTurned, orbitReset)
 *   keys      [[key, label], ...]: in 3D, one muted text of the pairs that fit on the canvas's bottom row
 *
 * From the top: one header row (the list's first line, with dollars), at most three "needs you" lines, the canvas,
 * then the instruments (context chart, steps strip, spend bars).
 *
 * 2D: braille cells, each 2 dots wide and 4 tall; as a terminal cell is about twice as tall as it is wide, a dot is
 * about square, so geometry is done in dots and comes out round. The hub is a ring outline with open space inside.
 * 3D: every structure group has its own orbit ring round the hub, each tilted differently; the figure turns round its
 * vertical axis once in TURN_MS and is drawn far to near with `·` `.` `:` `0` `1` `@`, far parts dimmed.
 *
 * Pure: no host imports, no timers, no mutation of the document. The layout of a document at a size is cached.
 */
import {
  SPINNER, MARK_HUB, MARK_IDLE, SENT_BACK, ATTENTION, LOOP_MARK, BAR_FULL, BAR_EMPTY, ELLIPSIS,
  charWidth, displayWidth, truncate, clean, clipLine, lineWidth, formatDollars, formatTokens, seatName, isAnimated,
  isObject, list, busyCount, finiteNumber, rounds, validAgents, findHub, hubWorking, currentGoal, agentCost,
  numberSpans, spinnerGlyph, wearParts, needsEntries, needsLine,
} from "./render.js";

export const SCENE_MIN_WIDTH = 64;
export const SCENE_MIN_HEIGHT = 22;
export const SIDE_BY_SIDE_WIDTH = 100;
export const FRAME_MS_WORKING = 100; // ten frames a second while work moves
export const FRAME_MS_IDLE = 250; // four when idle in 2D
export const FRAME_MS_IDLE_3D = 166; // six when idle in 3D (the turning)
export const BLOCKS = Object.freeze(["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]);
export const MARK_DONE = "●";
export const MARK_FAILED = "✗";
export const IDLE_LAP_MS = 20000;
export const TURN_MS = 40000; // one turn of the 3D figure
export const CHANGE_MS = 700; // the camera's move between the views
export const ELEVATION_3D = 0.5; // radians above the figure's equator
export const DEPTH_CHARS = Object.freeze(["·", ".", ":", "0", "1", "@"]); // far to near
// Turning the 3D figure by hand (see orbitCamera): the self-turn waits RESUME_MS after the last input, then goes on
// from where the user left it. The elevation stays between the figure's plane (0) and straight above (PI/2).
export const RESUME_MS = 3000;
export const TURN_STEP = Math.PI / 12; // one key press: 15 degrees round the upright axis
export const ELEVATION_STEP = Math.PI / 24; // one key press: 7.5 degrees up or down
export const ELEVATION_MIN = 0;
export const ELEVATION_MAX = Math.PI / 2;
export const DRAG_TURN_PER_CELL = Math.PI / 30; // a drag: 6 degrees per column across
export const DRAG_ELEVATION_PER_ROW = Math.PI / 15; // and 12 per row up or down (a row is about two columns tall)
export const CANVAS_MIN_ROWS = 12;
// The group colours, by the theme's background. No green or teal: every hue is outside 80-190 degrees.
export const GROUP_COLORS = Object.freeze({
  dark: Object.freeze({ council: "#b39ddb", decide: "#7fb2ff", build: "#e8a07c", verify: "#f28fb5", understand: "#e6c15a" }),
  light: Object.freeze({ council: "#6a4fb3", decide: "#2f6fd0", build: "#ac563d", verify: "#b8336a", understand: "#8a6d00" }),
});
const GROUP_ORDER = ["council", "decide", "build", "verify", "understand"];
const REST_MIX = 0.5; // an agent at rest: its group colour half way to the muted colour
const DEPTH_MIX = [0.72, 0.6, 0.45, 0.3, 0.15, 0]; // 3D: far to near, toward the muted colour
const NEEDS_MAX = 3;
const NAME_MAX = 12;
const DRAW_MAX_WIDTH = 400; // larger sizes are drawn at this size and padded with blank cells
const DRAW_MAX_HEIGHT = 200;
const TWEEN_MS = 700;
const PERSPECTIVE = 4; // the camera's distance in figure radii
const FIGURE_SCALE = 0.78; // the 3D figure's radius against the 2D ring's, so the near side and the labels fit

// braille dot bits by [row 0-3][column 0-1]
const DOT_BITS = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]];

export function sceneFits(width, height) {
  return Number(width) >= SCENE_MIN_WIDTH && Number(height) >= SCENE_MIN_HEIGHT;
}

// The camera's move between the views at `now`: { from, to, t } with t in [0, 1), or null when there is none.
export function changeAt(options, now) {
  const o = isObject(options) ? options : {};
  const to = o.view === "3d" ? "3d" : "2d";
  const c = isObject(o.change) ? o.change : null;
  if (!c) return null;
  const from = c.from === "3d" ? "3d" : "2d";
  const at = Number(c.at);
  if (from === to || !Number.isFinite(at)) return null;
  const t = (Number(now) - at) / CHANGE_MS;
  return t >= 0 && t < 1 ? { from, to, t } : null;
}

// The frame interval: 100 ms while work moves or the camera moves; when idle 250 ms in 2D and 166 ms in 3D.
export function frameInterval(doc, options = {}, now = 0) {
  if (changeAt(options, now)) return FRAME_MS_WORKING;
  if (isAnimated(doc)) return FRAME_MS_WORKING;
  return isObject(options) && options.view === "3d" ? FRAME_MS_IDLE_3D : FRAME_MS_IDLE;
}

function sp(text, tone, color) {
  return color ? { text, tone, color } : { text, tone };
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

// ---------------------------------------------------------------------------------------------------------------
// Colours

function parseHex(hex) {
  const m = typeof hex === "string" ? /^#([0-9a-f]{6})$/i.exec(hex.trim()) : null;
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function toHex(rgb) {
  return "#" + rgb.map((v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, "0")).join("");
}

// -> the hue of "#rrggbb" in degrees (0-360), or null for a grey or a bad value.
export function hueOf(hex) {
  const c = parseHex(hex);
  if (!c) return null;
  const [r, g, b] = c.map((v) => v / 255);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const d = max - min;
  if (d < 1e-9) return null;
  let h;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return (h * 60 + 360) % 360;
}

// A neutral grey as light as `hex`: mixing a colour with it keeps the colour's hue.
function greyOf(hex) {
  const c = parseHex(hex) || [128, 128, 128];
  const l = 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2];
  return toHex([l, l, l]);
}

function mix(a, b, t) {
  const A = parseHex(a);
  const B = parseHex(b);
  if (!A || !B || t <= 0) return a;
  return toHex(A.map((v, i) => v + (B[i] - v) * t));
}

// The group colours of a frame: full(key), rest(key), depth(key, level 0-5).
export function makePalette(keys, colors) {
  const dark = !(isObject(colors) && colors.dark === false);
  const table = dark ? GROUP_COLORS.dark : GROUP_COLORS.light;
  const grey = greyOf(isObject(colors) && parseHex(colors.muted) ? colors.muted : dark ? "#808080" : "#8c8c8c");
  const assigned = new Map();
  const used = new Set();
  for (const k of keys) {
    if (Object.hasOwn(table, k)) {
      assigned.set(k, table[k]);
      used.add(table[k]);
    }
  }
  let cycle = 0;
  for (const k of keys) {
    if (assigned.has(k)) continue;
    const free = GROUP_ORDER.map((g) => table[g]).find((c) => !used.has(c));
    const color = free || table[GROUP_ORDER[cycle++ % GROUP_ORDER.length]];
    used.add(color);
    assigned.set(k, color);
  }
  const cache = new Map();
  const shade = (k, t) => {
    const key = k + "\u0001" + t;
    let c = cache.get(key);
    if (c === undefined) {
      c = mix(assigned.get(k) || table.build, grey, t);
      cache.set(key, c);
    }
    return c;
  };
  return {
    dark,
    groups: assigned,
    full: (k) => shade(k, 0),
    rest: (k) => shade(k, REST_MIX),
    depth: (k, level) => shade(k, DEPTH_MIX[clamp(level, 0, 5)]),
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Lines: exact width, neighbouring spans of one style joined

function sameStyle(a, b) {
  return a.tone === b.tone && (a.color || "") === (b.color || "");
}

function normalize(line) {
  const out = [];
  for (const s of line) {
    if (!s || !s.text) continue;
    const last = out[out.length - 1];
    if (last && (sameStyle(last, s) || /^ +$/.test(s.text))) last.text += s.text;
    else if (last && /^ +$/.test(last.text)) {
      last.text += s.text;
      last.tone = s.tone;
      if (s.color) last.color = s.color;
      else delete last.color;
    } else out.push(sp(s.text, s.tone, s.color));
  }
  return out;
}

export function padLine(line, width) {
  if (width <= 0) return [];
  const cut = clipLine(line, width);
  const w = lineWidth(cut);
  const out = w < width ? [...cut, sp(" ".repeat(width - w), "plain")] : cut;
  return normalize(out);
}

// ---------------------------------------------------------------------------------------------------------------
// The canvas: a text layer over a layer of depth characters (3D) over braille dots (2D)

// Styles of one frame: id -> { tone, color }, and a rank (a dot of a higher rank wins its cell). Id 0: blank.
class Styles {
  constructor() {
    this.items = [{ tone: "plain" }];
    this.rank = [0];
    this.ids = new Map();
  }

  get(tone, color, rank) {
    const key = tone + "\u0001" + (color || "");
    let id = this.ids.get(key);
    if (id === undefined) {
      id = this.items.length;
      this.items.push(color ? { tone, color } : { tone });
      this.rank.push(rank === undefined ? { muted: 1, plain: 3, accent: 4, danger: 5 }[tone] || 2 : rank);
      this.ids.set(key, id);
    }
    return id;
  }
}

class Canvas {
  constructor(w, h, styles) {
    this.w = w;
    this.h = h;
    this.styles = styles;
    const n = w * h;
    this.bits = new Uint8Array(n);
    this.dotStyle = new Uint16Array(n);
    this.fig = new Array(n).fill(null);
    this.figStyle = new Uint16Array(n);
    this.figDepth = new Float32Array(n).fill(-Infinity);
    this.text = new Array(n).fill(null); // a character, "" for the right half of a wide one, null for none
    this.textStyle = new Uint16Array(n);
  }

  dot(x, y, style) {
    x = Math.round(x);
    y = Math.round(y);
    if (x < 0 || y < 0 || x >= this.w * 2 || y >= this.h * 4) return;
    const i = (y >> 2) * this.w + (x >> 1);
    this.bits[i] |= DOT_BITS[y & 3][x & 1];
    if (this.styles.rank[style] >= this.styles.rank[this.dotStyle[i]]) this.dotStyle[i] = style;
  }

  // A depth character at cell (c, r): the nearest one wins its cell.
  plot(c, r, ch, style, depth) {
    if (!(c >= 0 && r >= 0 && c < this.w && r < this.h)) return false;
    const i = r * this.w + c;
    if (depth <= this.figDepth[i]) return false;
    this.figDepth[i] = depth;
    this.fig[i] = ch;
    this.figStyle[i] = style;
    return true;
  }

  free(c, r) {
    return c >= 0 && r >= 0 && c < this.w && r < this.h && this.text[r * this.w + c] === null;
  }

  // Free cells from (c, r) going `dir` (+1 right, -1 left), keeping one free cell before the next text.
  room(c, r, dir, cap) {
    let n = 0;
    while (n < cap && this.free(c + dir * n, r)) n += 1;
    if (n < cap && n > 0 && c + dir * n >= 0 && c + dir * n < this.w) n -= 1; // a gap before other text
    return n;
  }

  put(c, r, value, style) {
    if (r < 0 || r >= this.h) return 0;
    let col = c;
    for (const ch of value) {
      const cw = charWidth(ch);
      if (cw === 0) continue;
      if (col < 0) {
        col += cw;
        continue;
      }
      if (col + cw > this.w) break;
      const i = r * this.w + col;
      this.text[i] = ch;
      this.textStyle[i] = style;
      if (cw === 2) {
        this.text[i + 1] = "";
        this.textStyle[i + 1] = style;
      }
      col += cw;
    }
    return col - c;
  }

  lines() {
    const items = this.styles.items;
    const out = [];
    for (let r = 0; r < this.h; r += 1) {
      const runs = [];
      let text = "";
      let style = 0;
      const flush = () => {
        if (text) {
          const s = items[style];
          runs.push(sp(text, s.tone, s.color));
        }
        text = "";
      };
      for (let c = 0; c < this.w; c += 1) {
        const i = r * this.w + c;
        let ch;
        let st;
        if (this.text[i] !== null) {
          ch = this.text[i];
          st = this.textStyle[i];
          if (ch === "") continue;
          if (ch === " ") st = 0;
        } else if (this.fig[i] !== null) {
          ch = this.fig[i];
          st = this.figStyle[i];
        } else if (this.bits[i]) {
          ch = String.fromCharCode(0x2800 + this.bits[i]);
          st = this.dotStyle[i];
        } else {
          ch = " ";
          st = 0;
        }
        if (st === 0 || st === style) text += ch;
        else if (style === 0) {
          text += ch;
          style = st;
        } else {
          flush();
          text = ch;
          style = st;
        }
      }
      flush();
      out.push(runs);
    }
    return out;
  }
}

// ---------------------------------------------------------------------------------------------------------------
// The ring: the same order as the desktop view (agentgraph/web/app.js addPositions)

const BUILTIN_ORDER = ["decide", "build", "verify", "understand"];

function groupOf(agent) {
  return typeof agent.group === "string" && agent.group ? agent.group : "build";
}

// -> { groups: [{ key, units: [{ agent, seats: [agent] }] }], topGap, seats: Set<agent> }
export function ringOrder(doc, agents, hub) {
  const byName = new Map();
  for (const a of agents) if (!byName.has(a.name)) byName.set(a.name, a);
  const unique = [...byName.values()];
  const hubName = hub ? hub.name : "";
  const isSeat = (a) => {
    const lead = typeof a.parent === "string" && a.parent && a.parent !== a.name && a.parent !== hubName && byName.get(a.parent);
    return !!lead && !(typeof lead.parent === "string" && lead.parent && byName.has(lead.parent));
  };
  const seatsOf = new Map();
  for (const a of unique.filter(isSeat).sort((x, y) => x.name.localeCompare(y.name))) {
    if (!seatsOf.has(a.parent)) seatsOf.set(a.parent, []);
    seatsOf.get(a.parent).push(a);
  }
  const ring = unique.filter((a) => a !== hub && !isSeat(a));
  const keys = [...new Set(ring.map(groupOf))];
  const structure = isObject(doc.structure) ? doc.structure : null;
  const listed = structure && structure.source === "folder"
    ? list(doc.groups).filter(isObject).map((g) => g.id).filter((id) => keys.includes(id))
    : null;
  const order = listed ? listed.slice(1) : BUILTIN_ORDER;
  const lead = listed
    ? listed.slice(0, 1)
    : keys.filter((k) => !order.includes(k)).sort((a, b) => Number(b === "council") - Number(a === "council") || String(a).localeCompare(String(b)));
  const groupKeys = [...lead, ...order.filter((k) => keys.includes(k)), ...(listed ? keys.filter((k) => !listed.includes(k)).sort() : [])];
  const groups = groupKeys.map((key) => ({
    key,
    units: ring.filter((a) => groupOf(a) === key).sort((a, b) => a.name.localeCompare(b.name)).map((agent) => ({ agent, seats: seatsOf.get(agent.name) || [] })),
  }));
  // seats whose lead is not on the ring (it cannot happen with the rule above, but keep every agent)
  const placedSeats = new Set(groups.flatMap((g) => g.units.flatMap((u) => u.seats)));
  for (const a of unique) {
    if (isSeat(a) && !placedSeats.has(a)) {
      const g = groups.find((x) => x.key === groupOf(a)) || (groups.push({ key: groupOf(a), units: [] }), groups[groups.length - 1]);
      g.units.push({ agent: a, seats: [] });
    }
  }
  return { groups, topGap: !lead.length && groups.length > 2 };
}

// ---------------------------------------------------------------------------------------------------------------
// Agent state

function agentStates(doc, agents) {
  const graph = isObject(doc.graph) ? doc.graph : null;
  const states = new Map();
  const nodes = graph ? list(graph.nodes).filter(isObject) : [];
  const stuck = new Set();
  const back = new Set();
  const done = new Set();
  for (const loop of list(graph && graph.work_loops)) {
    if (!isObject(loop)) continue;
    if (loop.state === "stuck") stuck.add(loop.agent);
    if (loop.state === "waiting" || loop.state === "closed") done.add(loop.agent);
  }
  for (const n of nodes) {
    if (n.status === "failed" || n.status === "blocked" || n.stalled === true) stuck.add(n.agent);
    if (n.status === "returned" || n.status === "passed") done.add(n.agent);
  }
  for (const loop of list(graph && graph.loops)) if (isObject(loop) && loop.state === "sent_back") back.add(loop.worker);
  for (const a of agents) {
    let s = "idle";
    if (busyCount(a) > 0) s = "working";
    else if (stuck.has(a.name)) s = "stuck";
    else if (back.has(a.name)) s = "back";
    else if (done.has(a.name)) s = "done";
    states.set(a.name, s);
  }
  return states;
}

function workRounds(doc, name) {
  const graph = isObject(doc.graph) ? doc.graph : null;
  let best = 0;
  for (const loop of list(graph && graph.work_loops)) if (isObject(loop) && loop.agent === name) best = Math.max(best, rounds(loop));
  return best;
}

function hasWorked(agent) {
  return busyCount(agent) > 0 || list(agent.sessions).some(isObject);
}

// ---------------------------------------------------------------------------------------------------------------
// Geometry (in dots)

function ellipsePath(cx, cy, rx, ry) {
  const N = 360;
  const pts = [];
  const cum = [0];
  for (let i = 0; i <= N; i += 1) {
    const t = (i / N) * 2 * Math.PI;
    pts.push([cx + rx * Math.sin(t), cy - ry * Math.cos(t)]); // from the top, clockwise on screen
    if (i) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  }
  const length = cum[N];
  const at = (s) => {
    const d = ((s % length) + length) % length;
    let lo = 0;
    let hi = N;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] <= d) lo = mid;
      else hi = mid;
    }
    const f = (d - cum[lo]) / ((cum[hi] - cum[lo]) || 1);
    return [pts[lo][0] + (pts[hi][0] - pts[lo][0]) * f, pts[lo][1] + (pts[hi][1] - pts[lo][1]) * f];
  };
  return { length, at };
}

// Points about one dot apart from a to b (both ends included).
function segment(ax, ay, bx, by) {
  const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay)));
  const out = [];
  for (let i = 0; i <= n; i += 1) out.push([ax + ((bx - ax) * i) / n, ay + ((by - ay) * i) / n]);
  return out;
}

const layoutCache = new WeakMap();

// -> the positions of everything on the canvas of `w` x `h` cells, for one document. Cached.
export function orbitLayout(doc, w, h) {
  let byDoc = layoutCache.get(doc);
  const key = w + "x" + h;
  if (byDoc && byDoc.has(key)) return byDoc.get(key);
  const layout = buildLayout(doc, w, h);
  if (!byDoc) {
    byDoc = new Map();
    layoutCache.set(doc, byDoc);
  }
  if (byDoc.size > 8) byDoc.clear();
  byDoc.set(key, layout);
  return layout;
}

function buildLayout(doc, w, h) {
  const agents = validAgents(doc);
  const hub = findHub(doc, agents);
  const { groups, topGap } = ringOrder(doc, agents, hub);
  const units = groups.flatMap((g, gi) => g.units.map((u) => Object.assign({ group: gi }, u)));
  const states = agentStates(doc, agents);
  const cx = w; // dots
  const cy = h * 2;
  const labelOf = (a) => truncate(clean(a.name) || "?", NAME_MAX);
  const longest = Math.max(1, ...units.map((u) => displayWidth(labelOf(u.agent))));
  // as large as the canvas allows: the glyph, a space and the name fit outside it on both sides, at most 2:1
  const ry = Math.max(4, h * 2 - 5); // dots: about a row and a quarter from the top and the bottom
  const rxCells = Math.max(3, Math.min(w / 2 - 3 - longest, ry));
  const rx = rxCells * 2;
  const ring = ellipsePath(cx, cy, rx, ry);
  // slots: one per unit, a council-sized unit 1.5, and a larger gap between groups
  const GAP = 0.6;
  const widthOf = (u) => (u.seats.length ? 1.5 : 1);
  const slots = units.reduce((t, u) => t + widthOf(u), 0) + (groups.length + (topGap ? 1 : 0)) * GAP + (topGap ? 1 : 0);
  const step = ring.length / Math.max(1, slots);
  let cursor = topGap ? (step * (1 + GAP)) / 2 + (step * GAP) / 2 : -((groups[0] ? groups[0].units.reduce((t, u) => t + widthOf(u), 0) : 0) * step) / 2;
  const placed = [];
  groups.forEach((g, gi) => {
    for (const u of g.units) {
      const wu = widthOf(u) * step;
      const along = cursor + wu / 2;
      const [x, y] = ring.at(along);
      cursor += wu;
      placed.push({ agent: u.agent, seats: u.seats, x, y, along, group: gi, key: g.key });
    }
    cursor += GAP * step;
  });
  // the hub: a ring outline, open inside, small against the orbit so there is empty space round it
  const hubR = clamp(Math.round(Math.min(rx, ry) * 0.17), 5, 9);
  for (const p of placed) {
    p.col = clamp(Math.floor(p.x / 2), 0, w - 1);
    p.row = clamp(Math.floor(p.y / 4), 0, h - 1);
    p.gx = p.col * 2 + 1; // the glyph cell's centre, in dots
    p.gy = p.row * 4 + 2;
    p.side = p.x >= cx - 0.5 ? 1 : -1;
    p.label = labelOf(p.agent);
    p.state = states.get(p.agent.name) || "idle";
    p.worked = hasWorked(p.agent);
    p.azimuth = Math.atan2((p.x - cx) / rx, -(p.y - cy) / ry); // clockwise from the top
    // the spoke: from outside the hub's ring to short of the glyph
    const dx = p.gx - cx;
    const dy = p.gy - cy;
    const len = Math.hypot(dx, dy) || 1;
    const from = hubR + 4;
    const to = len - 5;
    p.spoke = to > from ? segment(cx + (dx * from) / len, cy + (dy * from) / len, cx + (dx * to) / len, cy + (dy * to) / len) : [];
    // seats: a small circle round the lead, on the side away from its name
    const rs = 5;
    const n = p.seats.length;
    p.seatDots = p.seats.map((seat, i) => {
      const spread = Math.PI * 1.1;
      const centre = p.side > 0 ? Math.PI : 0;
      const a = n === 1 ? centre : centre - spread / 2 + (spread * i) / (n - 1);
      return { agent: seat, x: p.gx + rs * Math.cos(a), y: p.gy + rs * Math.sin(a) * 0.9 };
    });
  }
  // loops: an arc between the worker and its checker that leaves the ring outward and follows it round the
  // shorter way, so it never cuts across the middle
  const at = new Map(placed.map((p) => [p.agent.name, p]));
  const graph = isObject(doc.graph) ? doc.graph : null;
  const arcs = [];
  const bulge = clamp(Math.round(Math.min(rx, ry) * 0.18), 3, 8);
  for (const loop of list(graph && graph.loops)) {
    if (!isObject(loop)) continue;
    const a = at.get(loop.worker);
    const b = at.get(loop.evaluator);
    if (!a || !b || a === b) continue;
    let d = b.along - a.along;
    d = ((d % ring.length) + ring.length) % ring.length;
    if (d > ring.length / 2) d -= ring.length;
    const n = Math.max(2, Math.ceil(Math.abs(d) * 1.2));
    // arcs that share an agent sit in separate lanes, three dots apart
    const lane = arcs.filter((o) => [o.loop.worker, o.loop.evaluator].some((x) => x === loop.worker || x === loop.evaluator)).length;
    const points = [];
    for (let i = 0; i <= n; i += 1) {
      const t = i / n;
      const [x, y] = ring.at(a.along + d * t);
      const ux = x - cx;
      const uy = y - cy;
      const ul = Math.hypot(ux, uy) || 1;
      const out = (Math.min(bulge, 2 + Math.abs(d) * 0.12) + 3 * Math.min(lane, 3)) * Math.sqrt(Math.sin(Math.PI * t));
      points.push([x + (ux / ul) * out, y + (uy / ul) * out]);
    }
    arcs.push({ loop, points, worker: a, evaluator: b });
  }
  const keys = groups.map((g) => g.key);
  return { w, h, cx, cy, rx, ry, ring, placed, arcs, hubR, hub, states, agents, keys, groups };
}

// ---------------------------------------------------------------------------------------------------------------
// The 3D figure (in figure radii: the orbit rings have radius 1 round the hub at the origin; z is up)

const INCLINATIONS = [0.9, -1.05, 0.7, -0.8, 1.15, -0.6, 1.0, -0.9];

function horizontal(a) {
  return [Math.sin(a), Math.cos(a), 0]; // azimuth a, clockwise from the top of the 2D view
}

// -> { rings: [{ key, points }], agents: Map(name -> point), seats: [{ agent, key, lead, point }], arcs, hub }
export function figure3D(L) {
  if (L.figure) return L.figure;
  const byKey = new Map();
  for (const p of L.placed) {
    if (!byKey.has(p.key)) byKey.set(p.key, []);
    byKey.get(p.key).push(p);
  }
  const rings = [];
  const agents = new Map();
  let gi = 0;
  for (const [key, members] of byKey) {
    // the ring's node line runs through the middle of its agents, so from above they sit near where the 2D view has them
    const sx = members.reduce((t, p) => t + Math.sin(p.azimuth), 0);
    const sy = members.reduce((t, p) => t + Math.cos(p.azimuth), 0);
    const node = Math.atan2(sx, sy);
    const inc = INCLINATIONS[gi % INCLINATIONS.length];
    gi += 1;
    const n = horizontal(node);
    const m = [Math.cos(node) * Math.cos(inc), -Math.sin(node) * Math.cos(inc), Math.sin(inc)];
    const at = (u) => [Math.cos(u) * n[0] + Math.sin(u) * m[0], Math.cos(u) * n[1] + Math.sin(u) * m[1], Math.sin(u) * m[2]];
    const points = [];
    const N = 720;
    for (let i = 0; i < N; i += 1) points.push(at((i / N) * 2 * Math.PI));
    rings.push({ key, points, normal: [n[1] * m[2] - n[2] * m[1], n[2] * m[0] - n[0] * m[2], n[0] * m[1] - n[1] * m[0]] });
    for (const p of members) {
      const beta = p.azimuth - node;
      agents.set(p.agent.name, at(Math.atan2(Math.sin(beta), Math.cos(beta) * Math.cos(inc))));
    }
  }
  const seats = [];
  for (const p of L.placed) {
    if (!p.seats.length) continue;
    const c = agents.get(p.agent.name);
    // two directions across the lead's radius
    const e1 = norm(cross(c, [0, 0, 1])) || [1, 0, 0];
    const e2 = norm(cross(c, e1)) || [0, 0, 1];
    p.seats.forEach((agent, i) => {
      const a = (2 * Math.PI * i) / p.seats.length + 0.5;
      const r = 0.13;
      seats.push({ agent, key: p.key, lead: p.agent.name, point: [c[0] + r * (Math.cos(a) * e1[0] + Math.sin(a) * e2[0]), c[1] + r * (Math.cos(a) * e1[1] + Math.sin(a) * e2[1]), c[2] + r * (Math.cos(a) * e1[2] + Math.sin(a) * e2[2])] });
    });
  }
  // loops: an arc over the sphere between the two agents, bowed outward
  const arcs = L.arcs.map((arc) => {
    const a = agents.get(arc.worker.agent.name);
    const b = agents.get(arc.evaluator.agent.name);
    const pts = [];
    const N = 90;
    for (let i = 0; i <= N; i += 1) {
      const t = i / N;
      let v = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
      if (Math.hypot(...v) < 0.25) v = [v[0], v[1], v[2] + 0.6 * Math.sin(Math.PI * t)];
      const u = norm(v) || [0, 0, 1];
      const r = 1 + 0.22 * Math.sin(Math.PI * t);
      pts.push([u[0] * r, u[1] * r, u[2] * r]);
    }
    return { loop: arc.loop, worker: arc.worker, evaluator: arc.evaluator, points: pts };
  });
  const hubRing = [];
  for (let i = 0; i < 96; i += 1) {
    const a = (i / 96) * 2 * Math.PI;
    hubRing.push([0.2 * Math.sin(a), 0.2 * Math.cos(a), 0]);
  }
  L.figure = { rings, agents, seats, arcs, hubRing };
  return L.figure;
}

function cross(a, b) {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function norm(v) {
  const l = Math.hypot(v[0], v[1], v[2]);
  return l > 1e-9 ? [v[0] / l, v[1] / l, v[2] / l] : null;
}

// The camera: the figure turned by `turn` round its vertical axis, seen from `elevation` above its equator
// (PI/2: straight above, as the 2D view). -> project(point) = { col, row, depth } (depth: +1 nearest, -1 farthest)
export function camera3D(L, turn, elevation) {
  const ce = Math.cos(elevation);
  const se = Math.sin(elevation);
  const scale = (depth) => PERSPECTIVE / (PERSPECTIVE - depth);
  // the figure's highest and lowest point on screen at any turn, so it fills the canvas's rows, centred, and does not
  // grow and shrink as it turns
  let hi = -Infinity;
  let lo = Infinity;
  for (const ring of figure3D(L).rings) {
    for (let i = 0; i < ring.points.length; i += 6) {
      const p = ring.points[i];
      const rho = Math.hypot(p[0], p[1]);
      for (const y of [-rho, rho]) {
        const v = (y * se + p[2] * ce) * scale(-y * ce + p[2] * se);
        hi = Math.max(hi, v);
        lo = Math.min(lo, v);
      }
    }
  }
  if (!(hi > lo)) {
    hi = 1;
    lo = -1;
  }
  const mid = (hi + lo) / 2;
  const vs = (L.ry * 0.94) / Math.max(0.35, (hi - lo) / 2); // dots per unit, up and down
  const hs = L.rx * FIGURE_SCALE; // dots per unit, across
  const ct = Math.cos(turn);
  const st = Math.sin(turn);
  return (p) => {
    const x = p[0] * ct + p[1] * st;
    const y = -p[0] * st + p[1] * ct;
    const up = y * se + p[2] * ce;
    const depth = -y * ce + p[2] * se;
    const s = scale(depth);
    return { col: Math.floor((L.cx + x * s * hs) / 2), row: Math.floor((L.cy - (up * s - mid) * vs) / 4), depth };
  };
}

export function depthLevel(depth) {
  return clamp(Math.floor(((depth + 1) / 2) * 6), 0, 5);
}

// ---------------------------------------------------------------------------------------------------------------
// Header and instruments

function headerLine(doc, hub, width, frame) {
  const working = hubWorking(doc, hub);
  const rootName = isObject(doc.root) && typeof doc.root.agent === "string" ? doc.root.agent : "";
  const name = clean(hub ? hub.name : rootName) || "?";
  const graph = isObject(doc.graph) ? doc.graph : null;
  const progress = graph && isObject(graph.progress) ? graph.progress : null;
  const total = progress ? Math.max(0, Math.floor(Number(progress.total) || 0)) : 0;
  const passed = progress ? Math.min(total, Math.max(0, Math.floor(Number(progress.passed) || 0))) : 0;
  const totals = isObject(doc.totals) ? doc.totals : null;
  const cost = totals ? finiteNumber(totals.cost) : null;
  const right = [];
  if (total) right.push(sp(String(passed), "plain"), sp("/" + total, "muted"));
  if (cost !== null) right.push(...(right.length ? [sp("  ", "plain")] : []), sp(formatDollars(cost), "plain"));
  let rightW = lineWidth(right);
  const tail = working ? 2 : 0;
  if (rightW && width - 2 - tail - 1 - rightW < 3) {
    right.length = 0;
    rightW = 0;
  }
  const room = width - 2 - tail - (rightW ? 1 + rightW : 0);
  const line = [sp(MARK_HUB, working ? "accent" : "muted"), sp(" ", "plain"), sp(truncate(name, Math.max(1, room)), "plain")];
  if (working) line.push(sp(" ", "plain"), sp(spinnerGlyph(frame), "accent"));
  if (rightW) line.push(sp(" ".repeat(Math.max(1, width - lineWidth(line) - rightW)), "plain"), ...right);
  return line;
}

// a. Context: an area chart of root.wear.history and the wear line under it.
function contextInstrument(doc) {
  const root = isObject(doc.root) ? doc.root : null;
  const wear = root && isObject(root.wear) ? root.wear : null;
  const ctx = (wear ? finiteNumber(wear.context) : null) ?? (root ? finiteNumber(root.context) : null);
  if (!wear) {
    if (ctx === null) return null;
    return { min: 1, want: () => 1, draw: () => [[sp("ctx ", "muted"), ...numberSpans(formatTokens(ctx))]] };
  }
  const history = list(wear.history).map(finiteNumber).filter((v) => v !== null && v >= 0);
  const compactAt = finiteNumber(wear.compact_at);
  const windowSize = finiteNumber(wear.window);
  const top = compactAt !== null && compactAt > 0 ? compactAt : windowSize !== null && windowSize > 0 ? windowSize : Math.max(1, ...history);
  return {
    min: 2,
    want: (rows) => rows,
    draw(width, rows) {
      const lines = [];
      const chartRows = rows - 1;
      if (chartRows > 0) {
        const cols = history.length ? width : 0;
        const heights = [];
        for (let c = 0; c < cols; c += 1) {
          let v;
          if (history.length >= cols) {
            const a = Math.floor((c * history.length) / cols);
            const b = Math.max(a + 1, Math.floor(((c + 1) * history.length) / cols));
            v = Math.max(...history.slice(a, b));
          } else v = history[Math.min(history.length - 1, Math.floor((c * history.length) / cols))];
          let e = Math.round((v / top) * chartRows * 8);
          if (v > 0) e = Math.max(1, e);
          heights.push(clamp(e, 0, chartRows * 8));
        }
        for (let r = chartRows - 1; r >= 0; r -= 1) {
          let body = "";
          for (let c = 0; c < cols - 1; c += 1) {
            const e = clamp(heights[c] - r * 8, 0, 8);
            body += e ? BLOCKS[e - 1] : " ";
          }
          const line = [sp(body, "muted")];
          if (cols) {
            const e = clamp(heights[cols - 1] - r * 8, 0, 8);
            line.push(sp(e ? BLOCKS[e - 1] : " ", "accent"));
          }
          lines.push(line);
        }
      }
      if (ctx !== null) {
        const parts = wearParts(wear, ctx);
        lines.push(parts.find((p) => lineWidth(p) <= width) || parts[parts.length - 1]);
      } else lines.push([]);
      return lines;
    },
  };
}

// b. Steps: one cell per task-graph node, then passed/total.
function stepsInstrument(doc, frame, animated) {
  const graph = isObject(doc.graph) ? doc.graph : null;
  const nodes = graph ? list(graph.nodes).filter(isObject) : [];
  if (!nodes.length) return null;
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const sentBack = new Set();
  for (const loop of list(graph.loops)) {
    if (!isObject(loop)) continue;
    for (const pair of list(loop.pairs)) {
      if (!Array.isArray(pair)) continue;
      const worker = byId.get(pair[0]);
      const evaluator = byId.get(pair[1]);
      if (worker && evaluator && worker.status !== "passed" && worker.status !== "running" && (evaluator.verdict === "refuted" || evaluator.status === "failed")) sentBack.add(worker);
    }
  }
  const marks = nodes.map((n, i) => {
    if (n.status === "passed") return sp(MARK_DONE, "plain");
    if (n.status === "running") return sp(animated ? spinnerGlyph(frame, i) : SPINNER[0], "accent");
    if (n.status === "failed" || n.status === "blocked") return sp(MARK_FAILED, "danger");
    if (sentBack.has(n)) return sp(SENT_BACK, "accent");
    if (n.status === "returned") return sp(MARK_DONE, "muted");
    return sp(MARK_IDLE, "muted");
  });
  const progress = isObject(graph.progress) ? graph.progress : null;
  const total = progress ? Math.max(0, Math.floor(Number(progress.total) || 0)) : nodes.length;
  const passed = progress ? Math.min(total, Math.max(0, Math.floor(Number(progress.passed) || 0))) : nodes.filter((n) => n.status === "passed").length;
  const count = [sp(" ", "plain"), sp(String(passed), "plain"), sp("/" + total, "muted")];
  const countW = lineWidth(count);
  const rowsFor = (width) => (marks.length + countW <= width ? 1 : 2);
  return {
    min: 1,
    want: (rows, width) => Math.min(rows, rowsFor(width)),
    draw(width, rows) {
      const lines = [];
      let i = 0;
      for (let r = 0; r < rows && i < marks.length; r += 1) {
        const last = r === rows - 1;
        const cap = last ? Math.max(0, width - countW) : width;
        const take = marks.length - i <= cap ? marks.length - i : Math.max(0, last ? cap - 1 : cap);
        const line = marks.slice(i, i + take);
        i += take;
        if (last && i < marks.length) line.push(sp(ELLIPSIS, "muted"));
        lines.push(line);
      }
      const end = lines[lines.length - 1];
      if (lineWidth(end) + countW <= width) end.push(...count);
      else if (lines.length < rows) lines.push(count.slice(1));
      return lines;
    },
  };
}

// c. Spend: the top agents by dollars as bars scaled to the largest.
function spendInstrument(doc, now, previous) {
  const agents = validAgents(doc);
  const before = new Map();
  let ease = 1;
  if (previous && isObject(previous.doc) && Number.isFinite(Number(previous.at))) {
    const t = (now - Number(previous.at)) / TWEEN_MS;
    if (t < 1) {
      ease = 1 - (1 - clamp(t, 0, 1)) ** 3;
      for (const a of validAgents(previous.doc)) before.set(a.name, agentCost(a));
    }
  }
  const rows = agents
    .map((a, i) => {
      const cost = agentCost(a);
      const from = before.has(a.name) ? before.get(a.name) : cost;
      return { a, i, cost, shown: from + (cost - from) * ease };
    })
    .filter((r) => r.cost > 0)
    .sort((x, y) => y.cost - x.cost || x.i - y.i);
  if (!rows.length) return null;
  return {
    min: 1,
    want: (n) => Math.min(n, rows.length),
    draw(width, n) {
      const shown = rows.slice(0, n);
      const names = shown.map((r) => truncate(clean(r.a.group === "council" ? seatName(r.a.name) : r.a.name) || "?", NAME_MAX));
      const nameW = Math.max(...names.map(displayWidth));
      const dollars = shown.map((r) => formatDollars(r.cost));
      const dollarW = Math.max(...dollars.map(displayWidth));
      const max = Math.max(...shown.map((r) => r.shown), 1e-9);
      const barW = width - nameW - dollarW - 2;
      return shown.map((r, k) => {
        const busy = busyCount(r.a) > 0;
        const line = [sp(names[k] + " ".repeat(nameW - displayWidth(names[k])), busy ? "plain" : "muted"), sp(" ", "plain")];
        if (barW >= 3) {
          let filled = Math.round((barW * r.shown) / max);
          if (r.shown > 0) filled = Math.max(1, filled);
          filled = clamp(filled, 0, barW);
          if (filled) line.push(sp(BAR_FULL.repeat(filled), busy ? "accent" : "plain"));
          if (barW - filled) line.push(sp(BAR_EMPTY.repeat(barW - filled), "muted"));
          line.push(sp(" ", "plain"));
        }
        line.push(sp(dollars[k], "muted"));
        return line;
      });
    },
  };
}

// Lay out the instruments in `rows` rows: side by side when wide, stacked otherwise. -> Line[] (each `width` wide)
function instrumentLines(items, width, rows) {
  const present = items.filter((x) => x.item);
  if (!present.length || rows <= 0) return [];
  if (width >= SIDE_BY_SIDE_WIDTH) {
    const GAP = 3;
    const MIN = { a: 16, b: 10, c: 22 };
    const WEIGHT = { a: 0.4, b: 0.27, c: 0.33 };
    let shown = present.slice();
    while (shown.length && shown.reduce((t, x) => t + MIN[x.id], 0) + GAP * (shown.length - 1) > width) shown = shown.slice(0, -1);
    if (!shown.length) return [];
    const avail = width - GAP * (shown.length - 1);
    const weight = shown.reduce((t, x) => t + WEIGHT[x.id], 0);
    const widths = shown.map((x) => Math.max(MIN[x.id], Math.floor((avail * WEIGHT[x.id]) / weight)));
    widths[widths.length - 1] = Math.max(1, avail - widths.slice(0, -1).reduce((t, v) => t + v, 0));
    const blocks = shown.map((x, k) => {
      const n = Math.max(x.item.min, Math.min(rows, x.item.want(rows, widths[k])));
      const drawn = x.item.draw(widths[k], n);
      // bottom-aligned: the wear line and the last bar sit on the last row
      const pad = Array.from({ length: Math.max(0, rows - drawn.length) }, () => []);
      return [...pad, ...drawn].slice(-rows).map((l) => padLine(l, widths[k]));
    });
    const out = [];
    for (let r = 0; r < rows; r += 1) {
      const line = [];
      blocks.forEach((b, k) => {
        if (k) line.push(sp(" ".repeat(GAP), "plain"));
        line.push(...b[r]);
      });
      out.push(padLine(line, width));
    }
    return out;
  }
  // stacked: a first, then b, then c; drop c, then b, when they do not fit
  let shown = present.slice();
  const plan = () => {
    const out = [];
    let left = rows;
    shown.forEach((x, k) => {
      const later = shown.slice(k + 1).reduce((t, y) => t + y.item.min, 0);
      const cap = x.id === "a" ? (rows >= 6 ? 3 : 2) : x.id === "b" ? (rows >= 6 ? 2 : 1) : left;
      const n = clamp(x.item.want(Math.min(cap, left - later), width), x.item.min, Math.max(x.item.min, left - later));
      out.push(n);
      left -= n;
    });
    return { counts: out, left };
  };
  let p = plan();
  while (shown.length > 1 && p.left < 0) {
    shown = shown.slice(0, -1);
    p = plan();
  }
  if (p.left < 0) return [];
  const out = [];
  shown.forEach((x, k) => {
    const drawn = x.item.draw(width, p.counts[k]);
    for (let r = 0; r < p.counts[k]; r += 1) out.push(padLine(drawn[r] || [], width));
  });
  return out;
}

// ---------------------------------------------------------------------------------------------------------------
// Motion

// A pulse along `points`: three bright dots and a sparse tail, the head at `phase` (0-1) of the way.
function pulse(canvas, points, phase, style) {
  const n = points.length;
  if (n < 2) return;
  const tail = [4, 6, 9, 13];
  const head = Math.floor(phase * (n + 14));
  for (const k of [0, 1, 2, ...tail]) {
    const i = head - k;
    if (i >= 0 && i < n) canvas.dot(points[i][0], points[i][1], style);
  }
}

function pulsePhase(now, length, offset = 0, speed = 40) {
  const period = Math.max(900, ((length + 14) / speed) * 1000);
  return ((((now + offset) % period) + period) % period) / period;
}

function glyphFor(p, k, frame, blinkOn) {
  if (p.state === "working") return spinnerGlyph(frame, k);
  if (p.state === "stuck") return blinkOn ? ATTENTION : " ";
  if (p.state === "back") return SENT_BACK;
  if (p.state === "done") return MARK_DONE;
  return MARK_IDLE;
}

// ---------------------------------------------------------------------------------------------------------------
// 2D: the ring from above, in braille dots

function draw2D(doc, L, canvas, S, P, now, frame) {
  const animated = isAnimated(doc);
  const muted = S.get("muted");
  const restOf = (key) => S.get("plain", P.rest(key), 2);
  const fullOf = (key) => S.get("plain", P.full(key), 4);

  // the orbit: a faint dotted ellipse the agents sit on
  for (let a = 0; a < L.ring.length; a += 5) {
    const [x, y] = L.ring.at(a);
    canvas.dot(x, y, muted);
  }
  // spokes: thin and sparse, to every agent that has done work in this session
  for (const p of L.placed) {
    if (!p.worked) continue;
    const st = restOf(p.key);
    for (let i = 0; i < p.spoke.length; i += 3) canvas.dot(p.spoke[i][0], p.spoke[i][1], st);
  }
  // loops: a sparse arc bowed outward
  for (const arc of L.arcs) {
    const live = arc.loop.state === "checking" || arc.loop.state === "sent_back";
    for (let i = 0; i < arc.points.length; i += live ? 3 : 4) canvas.dot(arc.points[i][0], arc.points[i][1], muted);
  }
  // the hub: a ring outline, one dot wider while anything works
  const r = L.hubR + (animated && Math.sin((2 * Math.PI * now) / 1600) > 0 ? 1 : 0);
  const plain = S.get("plain", undefined, 3);
  const n = Math.ceil(2 * Math.PI * r * 1.2);
  for (let i = 0; i < n; i += 1) {
    const a = (i / n) * 2 * Math.PI;
    canvas.dot(L.cx + r * Math.cos(a), L.cy + r * Math.sin(a), plain);
  }
  canvas.put(Math.floor(L.cx / 2), Math.floor(L.cy / 4), MARK_HUB, plain);
  if (animated) {
    L.placed.forEach((p, k) => {
      if (p.state === "working" && p.spoke.length) pulse(canvas, p.spoke, pulsePhase(now, p.spoke.length, k * 337), fullOf(p.key));
    });
    for (const arc of L.arcs) {
      const s = arc.loop.state;
      if (s === "checking") pulse(canvas, arc.points, pulsePhase(now, arc.points.length), fullOf(arc.evaluator.key));
      else if (s === "sent_back") pulse(canvas, [...arc.points].reverse(), pulsePhase(now, arc.points.length), fullOf(arc.worker.key));
    }
  } else {
    // nothing works: one faint dot circles the ring, a lap in about twenty seconds
    const [x, y] = L.ring.at(((now % IDLE_LAP_MS) / IDLE_LAP_MS) * L.ring.length);
    canvas.dot(x, y, muted);
  }
  // council seats
  for (const p of L.placed) {
    for (const s of p.seatDots) {
      const st = busyCount(s.agent) > 0 ? fullOf(p.key) : restOf(p.key);
      canvas.dot(s.x, s.y, st);
      canvas.dot(s.x + 1, s.y, st);
    }
  }
  drawLabels2D(doc, L, canvas, S, P, now, frame);
}

function agentStyle(S, P, p, blinkOn) {
  if (p.state === "stuck") return S.get("danger");
  if (p.state === "working") return S.get("plain", P.full(p.key), 4);
  return S.get("plain", P.rest(p.key), 2);
}

function drawLabels2D(doc, L, canvas, S, P, now, frame) {
  const blinkOn = Math.floor(now / 500) % 2 === 0;
  L.placed.forEach((p, k) => canvas.put(p.col, p.row, glyphFor(p, k, frame, blinkOn), agentStyle(S, P, p, blinkOn)));
  const urgency = { stuck: 0, working: 1, back: 2, done: 3, idle: 4 };
  const order = [...L.placed].sort((a, b) => urgency[a.state] - urgency[b.state]);
  const shown = new Map();
  for (const p of order) {
    const start = p.col + 2 * p.side;
    const room = canvas.room(start, p.row, p.side, NAME_MAX);
    if (room < 1) continue;
    const text = truncate(p.label, room);
    const tw = displayWidth(text);
    canvas.put(p.col + p.side, p.row, " ", 0); // a clear cell between the glyph and its name
    canvas.put(p.side > 0 ? start : start - tw + 1, p.row, text, agentStyle(S, P, p, true));
    shown.set(p, text);
  }
  const muted = S.get("muted");
  for (const p of order) {
    if (p.state !== "working" || p.row + 1 >= L.h || !shown.has(p)) continue;
    const start = p.col + 2 * p.side;
    const room = canvas.room(start, p.row + 1, p.side, 30);
    const n = workRounds(doc, p.agent.name);
    const tail = (n >= 2 ? `${LOOP_MARK}${n} ` : "") + formatDollars(agentCost(p.agent));
    const goal = currentGoal(isObject(doc.graph) ? doc.graph : null, p.agent.name);
    let text = "";
    if (goal && room - displayWidth(tail) - 1 >= 4) text = truncate(goal, room - displayWidth(tail) - 1) + " " + tail;
    else if (displayWidth(tail) <= room) text = tail;
    if (!text) continue;
    const tw = displayWidth(text);
    canvas.put(p.side > 0 ? start : start - tw + 1, p.row + 1, text, muted);
  }
}

// ---------------------------------------------------------------------------------------------------------------
// 3D: the turning figure in depth characters

function bitAt(seed, k) {
  let x = (seed * 374761393 + k * 668265263) | 0;
  x = Math.imul(x ^ (x >>> 13), 1274126177);
  return (x ^ (x >>> 16)) & 1 ? "1" : "0";
}

function nameSeed(name) {
  let h = 2166136261;
  for (let i = 0; i < name.length; i += 1) h = Math.imul(h ^ name.charCodeAt(i), 16777619);
  return h >>> 0;
}

// Cells along projected points, one per cell, in order: [{ col, row, depth }]
function cellPath(project, points) {
  const out = [];
  let last = null;
  for (const p of points) {
    const q = project(p);
    if (last && last.col === q.col && last.row === q.row) {
      if (q.depth > last.depth) last.depth = q.depth;
      continue;
    }
    out.push(q);
    last = q;
  }
  return out;
}

function line3D(a, b, from, to, n) {
  const pts = [];
  for (let i = 0; i <= n; i += 1) {
    const t = from + ((to - from) * i) / n;
    pts.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
  }
  return pts;
}

// A stream of 0s and 1s along `cells` from the first to the last: two trains of five digits.
function digitStream(canvas, cells, now, seed, style, offset = 0) {
  const n = cells.length;
  if (n < 2) return;
  const period = Math.max(900, ((n + 6) / 14) * 1000); // about fourteen cells a second
  for (const shift of [0, 0.5]) {
    const phase = ((((now + offset) / period + shift) % 1) + 1) % 1;
    const head = Math.floor(phase * (n + 6));
    for (let k = 0; k < 5; k += 1) {
      const i = head - k;
      if (i < 0 || i >= n) continue;
      const c = cells[i];
      canvas.plot(c.col, c.row, bitAt(seed + Math.floor(now / 200), i), style, c.depth + 0.02);
    }
  }
}

function draw3D(doc, L, canvas, S, P, now, frame, cam) {
  const animated = isAnimated(doc);
  const F = figure3D(L);
  const project = camera3D(L, cam.turn, cam.elevation);
  const N = Math.max(120, Math.ceil(2 * Math.PI * Math.max(L.rx / 2, L.ry / 2) * 2.5));
  const step = Math.max(1, Math.floor(720 / N));
  // the orbit rings: one per group, near side dense, far side faint
  for (const ring of F.rings) {
    cellPath(project, ring.points.filter((_, i) => i % step === 0)).forEach((q, i) => {
      const lvl = depthLevel(q.depth);
      if (lvl < 3 && i % 2) return; // the far half is sparse
      canvas.plot(q.col, q.row, DEPTH_CHARS[lvl], S.get("plain", P.depth(ring.key, lvl), 2), q.depth);
    });
  }
  // the hub: a small ring round the centre
  for (const p of F.hubRing) {
    const q = project(p);
    const lvl = depthLevel(q.depth * 4);
    canvas.plot(q.col, q.row, DEPTH_CHARS[lvl], S.get("plain", undefined, 3), q.depth);
  }
  // spokes: dotted, from outside the hub's ring to short of the agent
  const spokes = new Map();
  for (const p of L.placed) {
    if (!p.worked) continue;
    const a = F.agents.get(p.agent.name);
    const cells = cellPath(project, line3D([0, 0, 0], a, 0.24, 0.9, 80));
    spokes.set(p, cells);
    cells.forEach((c, i) => {
      if (i % 2) return;
      const lvl = depthLevel(c.depth);
      canvas.plot(c.col, c.row, DEPTH_CHARS[Math.max(0, lvl - 1)], S.get("plain", P.depth(p.key, Math.max(0, lvl - 2)), 2), c.depth);
    });
  }
  // loops: a muted arc over the sphere
  const muted = S.get("muted");
  const arcCells = F.arcs.map((arc) => {
    const cells = cellPath(project, arc.points);
    cells.forEach((c, i) => {
      if (i % 3) return;
      canvas.plot(c.col, c.row, DEPTH_CHARS[Math.floor(depthLevel(c.depth) / 2)], muted, c.depth - 0.01);
    });
    return { arc, cells };
  });
  // council seats
  for (const s of F.seats) {
    const q = project(s.point);
    const st = busyCount(s.agent) > 0 ? S.get("plain", P.full(s.key), 4) : S.get("plain", P.depth(s.key, Math.min(4, depthLevel(q.depth))), 2);
    canvas.plot(q.col, q.row, "•", st, q.depth + 0.01);
  }
  // motion: 0s and 1s stream from the hub to each working agent, and round a loop toward whoever has the work
  if (animated) {
    L.placed.forEach((p, k) => {
      if (p.state === "working" && spokes.has(p)) digitStream(canvas, spokes.get(p), now, nameSeed(p.agent.name), S.get("plain", P.full(p.key), 4), k * 337);
    });
    for (const { arc, cells } of arcCells) {
      const s = arc.loop.state;
      if (s === "checking") digitStream(canvas, cells, now, 7, S.get("plain", P.full(arc.evaluator.key), 4));
      else if (s === "sent_back") digitStream(canvas, [...cells].reverse(), now, 11, S.get("plain", P.full(arc.worker.key), 4));
    }
  }
  // text: the hub's glyph, then the agents' glyphs and names, nearest first; a label never covers a nearer one
  const hubAt = project([0, 0, 0]);
  canvas.put(hubAt.col, hubAt.row, MARK_HUB, S.get("plain", undefined, 3));
  const blinkOn = Math.floor(now / 500) % 2 === 0;
  const items = L.placed.map((p, k) => ({ p, k, q: project(F.agents.get(p.agent.name)) })).sort((a, b) => b.q.depth - a.q.depth);
  for (const { p, k, q } of items) {
    if (!canvas.free(q.col, q.row)) continue;
    let st = agentStyle(S, P, p, blinkOn);
    if (p.state !== "working" && p.state !== "stuck" && q.depth < 0) st = S.get("plain", P.depth(p.key, 0), 2);
    canvas.put(q.col, q.row, glyphFor(p, k, frame, blinkOn), st);
  }
  for (const { p, q } of items) {
    const tw = displayWidth(p.label);
    const near = q.depth >= 0;
    const st = p.state === "stuck" ? S.get("danger") : near ? S.get("plain", P.full(p.key), 4) : muted;
    const out = q.col >= Math.floor(L.cx / 2) ? 1 : -1;
    const fits = (c, r) => {
      if (c < 0 || c + tw > L.w || r < 0 || r >= L.h) return false;
      for (let i = -1; i <= tw; i += 1) if (c + i >= 0 && c + i < L.w && !canvas.free(c + i, r)) return false;
      return true;
    };
    // beside the glyph, outward first; then a row or two up or down; then anywhere on the nearest free row
    let spot = null;
    for (const dr of [0, -1, 1, -2, 2]) {
      for (const side of [out, -out]) {
        const c = side > 0 ? q.col + 2 : q.col - 1 - tw;
        if (!spot && fits(c, q.row + dr)) spot = [c, q.row + dr];
      }
    }
    for (let d = 0; !spot && d < L.h * 2; d += 1) {
      const r = q.row + (d % 2 ? -(d + 1) / 2 : d / 2);
      if (r < 0 || r >= L.h) continue;
      let best = null;
      for (let c = 0; c + tw <= L.w; c += 1) if (fits(c, r) && (best === null || Math.abs(c - q.col) < Math.abs(best - q.col))) best = c;
      if (best !== null) spot = [best, r];
    }
    if (!spot) continue;
    // a clear cell each side, so the name reads apart from the figure
    if (spot[0] > 0) canvas.put(spot[0] - 1, spot[1], " ", 0);
    if (spot[0] + tw < L.w) canvas.put(spot[0] + tw, spot[1], " ", 0);
    canvas.put(spot[0], spot[1], p.label, st);
  }
}

function wrapTurn(a) {
  const full = 2 * Math.PI;
  return a >= 0 && a < full ? a : ((a % full) + full) % full;
}

// The 3D camera at `now` from the orbit `{ turn, elevation, resume }` (each optional): the figure stands at `turn`
// until `resume` (ms), then turns on from there, once in TURN_MS. No orbit: turn 0 at time 0 and ELEVATION_3D, so
// the angle is 2 PI (now mod TURN_MS) / TURN_MS. -> { turn (0 to 2 PI), elevation, held (true before `resume`) }
export function orbitCamera(orbit, now) {
  const o = isObject(orbit) ? orbit : {};
  const base = Number.isFinite(Number(o.turn)) ? Number(o.turn) : 0;
  const resume = o.resume !== undefined && o.resume !== null && Number.isFinite(Number(o.resume)) ? Number(o.resume) : null;
  const t = Number(now) || 0;
  const run = resume === null ? t : Math.max(0, t - resume);
  const turn = wrapTurn(base + 2 * Math.PI * ((((run % TURN_MS) + TURN_MS) % TURN_MS) / TURN_MS));
  const elevation = Number.isFinite(Number(o.elevation)) ? clamp(Number(o.elevation), ELEVATION_MIN, ELEVATION_MAX) : ELEVATION_3D;
  return { turn, elevation, held: resume !== null && t < resume };
}

// The orbit after the user turns it by `dTurn` and `dElevation` (radians) at `now`: from the angle shown at `now`,
// the elevation clamped, the self-turn held for RESUME_MS.
export function orbitTurned(orbit, now, dTurn = 0, dElevation = 0) {
  const cam = orbitCamera(orbit, now);
  return {
    turn: wrapTurn(cam.turn + (Number(dTurn) || 0)),
    elevation: clamp(cam.elevation + (Number(dElevation) || 0), ELEVATION_MIN, ELEVATION_MAX),
    resume: (Number(now) || 0) + RESUME_MS,
  };
}

// The orbit put back to where the 3D view starts (turn 0, ELEVATION_3D), held for RESUME_MS from `now`.
export function orbitReset(now) {
  return { turn: 0, elevation: ELEVATION_3D, resume: (Number(now) || 0) + RESUME_MS };
}

// The camera for a frame: the target view's steady camera, or one on its way between the views.
function cameraAt(options, now) {
  const change = changeAt(options, now);
  if (!change) return null;
  const target = orbitCamera(options.orbit, now);
  const e = change.t * change.t * (3 - 2 * change.t); // smoothstep
  const k = change.to === "3d" ? e : 1 - e; // 0: straight above, unturned; 1: the 3D camera
  const wrapped = target.turn > Math.PI ? target.turn - 2 * Math.PI : target.turn;
  return { turn: wrapped * k, elevation: Math.PI / 2 + (target.elevation - Math.PI / 2) * k };
}

// The keys of the 3D view ([[key, label], ...]) as one muted text on the canvas's bottom row: the pairs that fit.
function keysText(keys, width) {
  let text = "";
  for (const pair of Array.isArray(keys) ? keys : []) {
    if (!Array.isArray(pair) || pair.length < 2) continue;
    const piece = `${clean(String(pair[0]))} ${clean(String(pair[1]))}`;
    const next = text ? text + "  " + piece : piece;
    if (displayWidth(next) > width) break;
    text = next;
  }
  return text;
}

function drawCanvas(doc, w, h, now, frame, options) {
  const styles = new Styles();
  const canvas = new Canvas(w, h, styles);
  if (w < 8 || h < 4) return canvas.lines();
  const L = orbitLayout(doc, w, h);
  const P = makePalette(L.keys, options.colors);
  const moving = cameraAt(options, now);
  if (options.view === "3d" && options.keys) {
    // the keys of the 3D view, first on the bottom row's text layer, so no name is placed over them
    const hint = keysText(options.keys, w);
    if (hint) canvas.put(0, h - 1, hint, styles.get("muted"));
  }
  if (moving) draw3D(doc, L, canvas, styles, P, now, frame, moving);
  else if (options.view === "3d") draw3D(doc, L, canvas, styles, P, now, frame, orbitCamera(options.orbit, now));
  else draw2D(doc, L, canvas, styles, P, now, frame);
  return canvas.lines();
}

// ---------------------------------------------------------------------------------------------------------------
// Entry point

function messageScene(text, tone, width, height) {
  const lines = [];
  for (let r = 0; r < height; r += 1) lines.push(padLine(r === 0 ? [sp(truncate(text, width), tone)] : [], width));
  return lines;
}


export function scene(snapshot, options = {}) {
  return drawSized(snapshot, options, false);
}

// The same, but a drawing failure is thrown instead of drawn as a message line (for the tests).
export function sceneStrict(snapshot, options = {}) {
  return drawSized(snapshot, options, true);
}

// The canvas alone, `w` x `h` cells (for the tests). `options`: view, change, colors.
export function orbitLines(doc, w, h, now = 0, options = {}) {
  return drawCanvas(doc, w, h, now, Math.floor(now / FRAME_MS_WORKING), isObject(options) ? options : {});
}

function drawSized(snapshot, options, strict) {
  options = isObject(options) ? options : {};
  const width = Math.max(0, Math.floor(Number(options.width) || 0));
  const height = Math.max(0, Math.floor(Number(options.height) || 0));
  if (!height) return [];
  const frameOpt = Number(options.frame);
  const nowOpt = Number(options.now);
  const now = Number.isFinite(nowOpt) ? nowOpt : Number.isFinite(frameOpt) ? frameOpt * FRAME_MS_WORKING : 0;
  const frame = Math.floor(now / FRAME_MS_WORKING);
  const w = Math.min(width, DRAW_MAX_WIDTH);
  const h = Math.min(height, DRAW_MAX_HEIGHT);
  let lines;
  try {
    lines = drawScene(snapshot, w, h, now, frame, options);
  } catch (error) {
    if (strict) throw error;
    lines = messageScene(clean(error && error.message) || "scene failed", "danger", w, h);
  }
  // larger than drawn: blank cells to the exact size
  if (width > w) {
    const extra = " ".repeat(width - w);
    lines = lines.map((l) => normalize([...l, sp(extra, "plain")]));
  }
  if (height > h) {
    const blank = [sp(" ".repeat(width), "plain")];
    for (let r = h; r < height; r += 1) lines.push(width ? blank.map((s) => ({ ...s })) : []);
  }
  return lines;
}

function drawScene(doc, width, height, now, frame, options) {
  if (doc === undefined || doc === null) return messageScene("Loading", "muted", width, height);
  if (!isObject(doc)) return messageScene("Bad snapshot", "danger", width, height);
  if (doc.error !== undefined && doc.error !== null) return messageScene(clean(doc.error) || "unknown error", "danger", width, height);
  const agents = validAgents(doc);
  if (!agents.length) return messageScene("No agents", "muted", width, height);
  const hub = findHub(doc, agents);
  const animated = isAnimated(doc);

  const out = [padLine(headerLine(doc, hub, width, frame), width)];
  if (height === 1) return out;

  const listed = new Set(agents.map((a) => a.name));
  const needs = needsEntries(doc, listed).map((e) => needsLine(e, width));
  let needsN = Math.min(NEEDS_MAX, needs.length);
  const items = [
    { id: "a", item: contextInstrument(doc) },
    { id: "b", item: stepsInstrument(doc, frame, animated) },
    { id: "c", item: spendInstrument(doc, now, options.previous) },
  ];
  let rows = height >= 30 ? 6 : 4;
  let inst = instrumentLines(items, width, rows);
  const canvasRows = () => height - 1 - needsN - inst.length;
  while (canvasRows() < CANVAS_MIN_ROWS) {
    const kept = items.filter((x) => x.item);
    if (inst.length && rows > 4) rows = 4;
    else if (kept.length) kept[kept.length - 1].item = null;
    else if (needsN > 0) needsN -= 1;
    else break;
    inst = instrumentLines(items, width, rows);
  }
  for (let i = 0; i < needsN; i += 1) out.push(padLine(needs[i], width));
  const cr = Math.max(0, canvasRows());
  if (cr > 0) for (const line of drawCanvas(doc, width, cr, now, frame, options)) out.push(padLine(line, width));
  for (const line of inst) out.push(line);
  return out.slice(0, height);
}
