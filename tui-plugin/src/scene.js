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
 *   change    { from, at }: the morph from view `from` to `view`, starting at `at` (ms), for CHANGE_MS
 *   colors    { dark, muted }: the theme's background is dark (default) or light, and its muted colour "#rrggbb"
 *   orbit     { turn, elevation, resume }: the 3D camera turned by hand (orbitCamera, orbitTurned, orbitReset)
 *   keys      [[key, label], ...]: in 3D, one muted text of the pairs that fit on the canvas's bottom row
 *   streaks   [{ flow, run, at }]: in 3D, a shooting star for that finished run starting at `at` (ms), STREAK_MS long,
 *             ending where the run's star then stays (the panel adds one per run that finishes while 3D shows)
 *   motion    "reduced": no streak (the run's star appears at `at`), no fade of the sky (shown only in steady 3D)
 *   sky       false: no sky (for the tests)
 *
 * From the top: one header row (the list's first line, with dollars), at most three "needs you" lines, the canvas,
 * then the instruments (context chart, steps strip, spend bars).
 *
 * 2D: braille cells, each 2 dots wide and 4 tall; as a terminal cell is about twice as tall as it is wide, a dot is
 * about square, so geometry is done in dots and comes out round. The hub is a shaded ball (a rim and dithered dots), and
 * the sun while it works: flame spikes and a glow (see "The hub as a ball").
 * 3D: every structure group has its own orbit ring round the hub, each tilted differently; the figure turns round its
 * vertical axis once in TURN_MS (the agents under a parent are moons, each on its own orbit plane round it, a lap in MOON_LAP_MS) and is drawn far to near
 * with `·` `.` `:` `0` `1` `@`, far parts dimmed; the hub is a ball in block shades with the sun's flares drawn over the rings behind it. Behind it, in 3D
 * only, a sky (drawSky): sparse fixed stars, one warmer star per finished run, a streak when a run finishes.
 *
 * Pure: no host imports, no timers, no mutation of the document. The layout of a document at a size is cached.
 */
import {
  SPINNER, MARK_HUB, MARK_IDLE, SENT_BACK, ATTENTION, LOOP_MARK, BAR_FULL, BAR_EMPTY, ELLIPSIS,
  charWidth, displayWidth, truncate, clean, clipLine, lineWidth, formatDollars, formatTokens, seatName, isAnimated,
  isObject, list, busyCount, finiteNumber, rounds, validAgents, findHub, hubWorking, currentGoal, agentCost,
  numberSpans, spinnerGlyph, wearParts, needsEntries, needsLine, flowsOf,
} from "./render.js";

export const SCENE_MIN_WIDTH = 36;
export const SCENE_MIN_HEIGHT = 14;
export const SIDE_BY_SIDE_WIDTH = 100;
export const FRAME_MS_WORKING = 100; // ten frames a second while work moves
export const FRAME_MS_IDLE = 250; // four when idle in 2D
export const FRAME_MS_IDLE_3D = 166; // six when idle in 3D (the turning)
export const BLOCKS = Object.freeze(["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]);
export const MARK_DONE = "●";
export const MARK_FAILED = "✗";
export const IDLE_LAP_MS = 20000;
export const TURN_MS = 40000; // one turn of the 3D figure
export const CHANGE_MS = 700; // the morph between the views (see morphCamera)
// The 3D view is a side view: the camera ELEVATION_3D radians above the figure's plane, looking across it. The one
// place the starting elevation is set (orbitCamera's default, orbitReset, the morph's target).
export const ELEVATION_3D = 0.5;
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
// The sky behind the 3D view (drawSky). Field stars by size (dim to bright; none of DEPTH_CHARS); a run's star is
// RUN_STAR_CHAR in a warmer, brighter colour.
export const STAR_CHARS = Object.freeze(["'", "'", "+"]);
export const RUN_STAR_CHAR = "*";
export const STREAK_CHARS = Object.freeze({ head: "*", down: "\\", up: "/" }); // the head; the tail: "\\" or "/" by its slope
export const STAR_DENSITY = 1 / 70; // field stars per cell of the sky (about 1.4 % of the canvas)
export const RUN_STARS_MAX = 200;
export const STREAK_MS = 900; // a shooting star's flight
export const STREAK_GAP_MS = 700; // streaks that start together are spaced this far apart (the panel)
export const PARALLAX = 1 / 3; // the sky moves about a third as far as the figure's near side when it turns
const SKY_SEED = 0x5ca1ab1e;
const STAR_FADE = [0.55, 0.3, 0]; // small to large: the stars' colour toward the background
const SKY_DEPTH = -3; // below every part of the figure (the farthest is about -1.01): any figure cell covers a star
const STREAK_DEPTH = -2;
const STREAK_LEN = 7; // cells, one row and one column a step
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
export const MOON_LAP_MS = 24000; // one lap of the moons round their parent (the web view's clock too)
const HUB_RADIUS3 = 0.2; // the hub ball in figure radii (hubRing's radius)
// ... but never smaller on screen than HUB_MIN_DOTS3 dots in radius, across and up: in a small panel 0.2 of the figure is a ball
// of about three cells, too small for the sun's flares (0.6 of its radius) to leave its outline.
const HUB_MIN_DOTS3 = 7;
const hubRadius3 = (L) => Math.max(HUB_RADIUS3, HUB_MIN_DOTS3 / Math.min(L.fitY * 0.7, (L.w - 2) / (PERSPECTIVE / (PERSPECTIVE - 1))));
const MOON_TILT = 1.1; // radians: the moon ring against the group's ring
const MOON_RING_BASE = 0.11; // figure radii; the ring grows with the number of moons
const MOON_RING_PER = 0.04;
const MOON_RING_MAX = 0.4;
// the moons' orbits are drawn (faint, one each) only where the canvas has room for them beside the names
const MOON_RINGS_MIN_W = 48;
const MOON_RINGS_MIN_H = 18;
const MOON_RADIUS_STEP = 0.14; // moon i's orbit is r * (1 + 0.14 i): no two moons of a parent at the same distance
const PERSPECTIVE = 4; // the camera's distance in figure radii

// braille dot bits by [row 0-3][column 0-1]
const DOT_BITS = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]];

export function sceneFits(width, height) {
  return Number(width) >= SCENE_MIN_WIDTH && Number(height) >= SCENE_MIN_HEIGHT;
}

// The morph between the views at `now`: { from, to, t } with t in [0, 1), or null when there is none.
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

// The frame interval: 100 ms while work moves or the views morph; when idle 250 ms in 2D and 166 ms in 3D.
export function frameInterval(doc, options = {}, now = 0) {
  if (changeAt(options, now)) return FRAME_MS_WORKING;
  if (streaking(options, now)) return FRAME_MS_WORKING;
  if (isAnimated(doc)) return FRAME_MS_WORKING;
  if (isObject(options) && options.motion !== "reduced" && sunFading(doc, options, now)) return FRAME_MS_WORKING; // the spikes draw back
  return isObject(options) && options.view === "3d" ? FRAME_MS_IDLE_3D : FRAME_MS_IDLE;
}

// A shooting star flies or waits its turn (3D, not reduced motion).
function streaking(options, now) {
  if (!isObject(options) || options.view !== "3d" || options.motion === "reduced" || options.sky === false) return false;
  return (Array.isArray(options.streaks) ? options.streaks : []).some((st) => isObject(st) && Number(now) < Number(st.at) + STREAK_MS);
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
    this.kept = new Uint8Array(n);
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

  // The cell and its eight neighbours hold no figure character and no text (a star there reads as sky).
  // Cells where something will pass later (the moons' glyphs and names): the sky leaves them clear.
  reserve(c, r) {
    if (c >= 0 && r >= 0 && c < this.w && r < this.h) this.kept[r * this.w + c] = 1;
  }

  open(c, r) {
    for (let rr = r - 1; rr <= r + 1; rr += 1) {
      for (let cc = c - 1; cc <= c + 1; cc += 1) {
        if (cc < 0 || rr < 0 || cc >= this.w || rr >= this.h) continue;
        const i = rr * this.w + cc;
        if (this.fig[i] !== null || this.text[i] !== null || this.kept[i]) return false;
      }
    }
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

// The cells (columns and rows of the canvas) the hub's ball covers.
function hubBox(cx, cy, r) {
  return { c0: Math.floor((cx - r) / 2), c1: Math.floor((cx + r) / 2), r0: Math.floor((cy - r) / 4), r1: Math.floor((cy + r) / 4) };
}

// Give every agent's name its place on the glyph's row: outside the ring (the glyph, one clear cell, the name) when it
// fits, else inside it, else cut to what fits on the roomier side (at least two cells, else no name). A name never
// covers a glyph, the hub's ball or another name, and keeps one clear cell from each; `p.dir` is the way it runs from
// the glyph (+1 right, -1 left) and `p.name` is { text, c0, c1 } in cells, or null. The most urgent agent chooses first.
// -> how many names are whole, less 1000 for each glyph that sits on the hub or on another glyph.
function placeNames(placed, w, h, cx, cy, hubR) {
  const used = new Uint8Array(w * h);
  const mark = (c0, c1, r) => {
    for (let c = Math.max(0, c0); c <= Math.min(w - 1, c1); c += 1) if (r >= 0 && r < h) used[r * w + c] = 1;
  };
  const box = hubBox(cx, cy, hubR);
  for (let r = box.r0; r <= box.r1; r += 1) mark(box.c0, box.c1, r);
  let clashes = 0; // glyphs on the hub's ball or on each other
  for (const p of placed) {
    if (used[p.row * w + p.col]) clashes += 1;
    mark(p.col, p.col, p.row);
  }
  const order = placed.map((p, i) => ({ p, i })).sort((a, b) => URGENCY[a.p.state] - URGENCY[b.p.state] || a.i - b.i);
  let whole = 0;
  for (const { p } of order) {
    const tw = displayWidth(p.label);
    let pick = null;
    for (const dir of [p.side, -p.side]) {
      // the free cells from the first name cell on, keeping one clear cell before whatever is next
      const first = p.col + 2 * dir;
      const inside = (m) => first + dir * m >= 0 && first + dir * m < w;
      let m = 0;
      while (m <= tw && inside(m) && !used[p.row * w + first + dir * m]) m += 1;
      const n = m > tw ? tw : inside(m) ? m - 1 : m;
      const text = n >= tw ? p.label : n >= 2 ? truncate(p.label, n) : "";
      if (!text) continue;
      const width = displayWidth(text);
      const c0 = dir > 0 ? first : first - width + 1;
      if (!pick || width > pick.width) pick = { dir, text, width, c0, c1: c0 + width - 1 };
      if (text === p.label) break;
    }
    if (pick) {
      p.dir = pick.dir;
      p.name = { text: pick.text, c0: pick.c0, c1: pick.c1 };
      mark(pick.c0, pick.c1, p.row);
      if (pick.text === p.label) whole += 1;
    } else {
      p.dir = p.side;
      p.name = null;
    }
  }
  return whole - 1000 * clashes;
}

const URGENCY = { stuck: 0, working: 1, back: 2, done: 3, idle: 4 };
const MIN_RING = 4; // dots: the smallest ring radius

// The ring is a circle in dots (a dot is about square: a cell is 2 dots across and 4 down), so rx == ry. It is the
// largest circle on the canvas for which every name still has its own place (see placeNames), never more than the
// canvas's height allows (about a row and a quarter from the top and the bottom) and never so wide that a glyph leaves
// the canvas. In a wide panel the names fit outside it and it is as tall as the canvas; in a narrow one the names go
// inside the ring when outside would shrink it, and the spare rows stay empty.
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
  const fitY = Math.max(MIN_RING, h * 2 - 5); // dots: what the canvas's height allows
  const maxR = Math.max(MIN_RING, Math.min(fitY, w - 3));
  // slots: one per unit, a council-sized unit 1.5, and a larger gap between groups
  const GAP = 0.6;
  const widthOf = (u) => (u.seats.length ? 1.5 : 1);
  const slots = units.reduce((t, u) => t + widthOf(u), 0) + (groups.length + (topGap ? 1 : 0)) * GAP + (topGap ? 1 : 0);
  const arrange = (R, phase) => {
    const ring = ellipsePath(cx, cy, R, R);
    const step = ring.length / Math.max(1, slots);
    let cursor = (topGap ? (step * (1 + GAP)) / 2 + (step * GAP) / 2 : -((groups[0] ? groups[0].units.reduce((t, u) => t + widthOf(u), 0) : 0) * step) / 2) + phase * step;
    const placed = [];
    groups.forEach((g, gi) => {
      for (const u of g.units) {
        const wu = widthOf(u) * step;
        const along = cursor + wu / 2;
        const [x, y] = ring.at(along);
        cursor += wu;
        const p = { agent: u.agent, seats: u.seats, x, y, along, group: gi, key: g.key };
        p.col = clamp(Math.floor(x / 2), 0, w - 1);
        p.row = clamp(Math.floor(y / 4), 0, h - 1);
        p.gx = p.col * 2 + 1; // the glyph cell's centre, in dots
        p.gy = p.row * 4 + 2;
        p.side = x >= cx - 0.5 ? 1 : -1;
        p.label = labelOf(u.agent);
        p.state = states.get(u.agent.name) || "idle";
        placed.push(p);
      }
      cursor += GAP * step;
    });
    // the hub: a ring outline, open inside, small against the orbit so there is empty space round it
    const hubR = clamp(Math.round(R * 0.17), 5, 9);
    const full = placeNames(placed, w, h, cx, cy, hubR);
    return { R, ring, placed, hubR, full };
  };
  // the largest ring that gives every name its place; failing that, the one that shows the most whole names
  let best = null;
  // (the ring may be turned a little, up to a fifth of a slot either way, to bring the names clear of one another)
  search: for (let R = maxR; R >= MIN_RING; R -= 1) {
    for (const phase of [0, -0.2, 0.2, -0.1, 0.1]) {
      const a = arrange(R, phase);
      if (!best || a.full > best.full) best = a;
      if (a.full === units.length) break search;
    }
  }
  const { R, ring, placed, hubR } = best;
  const rx = R;
  const ry = R;
  for (const p of placed) {
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
      const centre = p.dir > 0 ? Math.PI : 0;
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
  return { w, h, cx, cy, rx, ry, R, fitY, hubBox: hubBox(cx, cy, hubR), ring, placed, arcs, hubR, hub, states, agents, keys, groups };
}

// ---------------------------------------------------------------------------------------------------------------
// The 3D figure (in figure radii: the orbit rings have radius 1 round the hub at the origin; z is up)

const INCLINATIONS = [0.9, -1.05, 0.7, -0.8, 1.15, -0.6, 1.0, -0.9];

function horizontal(a) {
  return [Math.sin(a), Math.cos(a), 0]; // azimuth a, clockwise from the top of the 2D view
}

// -> { rings: [{ key, points }], agents: Map(name -> point), seats: [{ agent, key, lead, point }], arcs, hub }
// `rise` (0 to 1) scales every ring's tilt, for the morph: at 0 every ring lies flat on the unit circle and each agent
// sits at its own 2D azimuth; at 1 (the 3D view, cached) the rings have their full tilt.
export function figure3D(L, rise = 1) {
  const k = Number.isFinite(Number(rise)) ? clamp(Number(rise), 0, 1) : 1;
  if (k === 1 && L.figure) return L.figure;
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
    const inc = INCLINATIONS[gi % INCLINATIONS.length] * k;
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
  // moons: each agent under a parent travels on its own orbit round it, in its own plane. The moons of a parent are
  // ordered by name, i = 0 .. n-1. The first plane is the one the single small ring had: it leans out of the group's
  // ring by MOON_TILT about the radial line through the parent (flat with the group at rise 0); moon i's plane is that
  // one turned about the vertical axis through the parent by 360 degrees * i / n, its radius is r * (1 + 0.14 i) (r:
  // MOON_RING_BASE + MOON_RING_PER per moon, at most MOON_RING_MAX) and it starts 360 degrees * i / n round its orbit.
  // The orbits themselves stand still and the moons go round them (moonPoint).
  const seats = [];
  const moonRings = [];
  for (const p of L.placed) {
    if (!p.seats.length) continue;
    const c = agents.get(p.agent.name);
    const group = rings.find((r) => r.key === p.key);
    const ng = group ? group.normal : [0, 0, 1];
    const u = norm(cross(ng, c)) || [1, 0, 0];
    // the lean goes the way (out of the two) that keeps the moons' planes furthest apart: a plane that is flat, or
    // that stands upright and is turned by 180 degrees, is turned about the vertical axis into itself. The sign is
    // decided at full lean, so the morph does not flip.
    const leaned = (lean) => [Math.cos(lean) * ng[0] + Math.sin(lean) * u[0], Math.cos(lean) * ng[1] + Math.sin(lean) * u[1], Math.cos(lean) * ng[2] + Math.sin(lean) * u[2]];
    const n = p.seats.length;
    const apart = (nz) => {
      let least = Infinity;
      for (let i = 0; i < n; i += 1) {
        for (let j = i + 1; j < n; j += 1) {
          const a = turnZ(nz, (2 * Math.PI * i) / n);
          const b = turnZ(nz, (2 * Math.PI * j) / n);
          least = Math.min(least, Math.acos(Math.min(1, Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2]))));
        }
      }
      return least;
    };
    const sign = apart(leaned(-MOON_TILT)) > apart(leaned(MOON_TILT)) + 1e-9 ? -1 : 1;
    const nm = leaned(sign * MOON_TILT * k);
    const e1 = norm(c) || [1, 0, 0];
    const e2 = norm(cross(nm, e1)) || [0, 0, 1];
    const base = Math.min(MOON_RING_MAX, MOON_RING_BASE + MOON_RING_PER * n);
    [...p.seats]
      .sort((x, y) => (x.name < y.name ? -1 : x.name > y.name ? 1 : 0))
      .forEach((agent, i) => {
        const turn = (2 * Math.PI * i) / n;
        const ring = { key: p.key, lead: p.agent.name, centre: c, e1: turnZ(e1, turn), e2: turnZ(e2, turn), normal: turnZ(nm, turn), radius: base * (1 + MOON_RADIUS_STEP * i), points: [] };
        for (let j = 0; j < 48; j += 1) ring.points.push(moonOn(ring, (j / 48) * 2 * Math.PI));
        moonRings.push(ring);
        seats.push({ agent, key: p.key, lead: p.agent.name, ring, a0: turn, index: i, point: moonOn(ring, turn) });
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
  const hubRho = hubRadius3(L);
  for (let i = 0; i < 96; i += 1) {
    const a = (i / 96) * 2 * Math.PI;
    hubRing.push([hubRho * Math.sin(a), hubRho * Math.cos(a), 0]);
  }
  const figure = { rings, agents, seats, moonRings, arcs, hubRing };
  if (k === 1) L.figure = figure;
  return figure;
}

// A point on a moon ring at angle `a`.
function moonOn(ring, a) {
  const ca = Math.cos(a) * ring.radius;
  const sa = Math.sin(a) * ring.radius;
  return [ring.centre[0] + ca * ring.e1[0] + sa * ring.e2[0], ring.centre[1] + ca * ring.e1[1] + sa * ring.e2[1], ring.centre[2] + ca * ring.e1[2] + sa * ring.e2[2]];
}

// How far round its ring the moons are at `now`: a lap in MOON_LAP_MS from the clock, never from a frame count; still
// (0) with reduced motion.
export function moonPhase(now, reduced) {
  if (reduced) return 0;
  const t = Number(now) || 0;
  return 2 * Math.PI * ((((t % MOON_LAP_MS) + MOON_LAP_MS) % MOON_LAP_MS) / MOON_LAP_MS);
}

// The place of a moon (an entry of figure3D(L).seats) at moonPhase `phase`.
export function moonPoint(seat, phase) {
  return moonOn(seat.ring, seat.a0 + phase);
}

// `v` turned about the vertical axis (z) by `a`.
function turnZ(v, a) {
  const ca = Math.cos(a);
  const sa = Math.sin(a);
  return [ca * v[0] - sa * v[1], sa * v[0] + ca * v[1], v[2]];
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
  const dots = cameraDots(L, turn, elevation);
  const project = (p) => {
    const q = dots(p);
    return { col: Math.floor(q.x / 2), row: Math.floor(q.y / 4), depth: q.depth };
  };
  project.hub = dots.hub;
  project.hubRho = dots.hubRho;
  return project;
}

// The morph between the views, `k` of the way from 2D (0) to 3D (1): a point of the figure (figure3D(L, k)) goes in a
// straight line on screen from where the 2D view has it, (cx + x rx, cy - y ry) in dots (straight above, unturned,
// on the 2D ellipse), to where the 3D camera (`turn`, `elevation`) shows it. So each agent travels from its 2D place
// to its 3D place while its ring tilts up out of the plane. -> project(point) = { col, row, depth }
export function morphCamera(L, turn, elevation, k) {
  const dots = cameraDots(L, turn, elevation);
  const f = clamp(Number(k) || 0, 0, 1);
  const project = (p) => {
    const q = dots(p);
    const fx = L.cx + p[0] * L.rx;
    const fy = L.cy - p[1] * L.ry;
    return { col: Math.floor((fx + (q.x - fx) * f) / 2), row: Math.floor((fy + (q.y - fy) * f) / 4), depth: q.depth };
  };
  // the hub ball: the 2D ball at the 2D centre, grown and moved to the 3D ball as the morph goes
  const h = dots.hub;
  project.hubRho = dots.hubRho;
  project.hub = { x: L.cx + (h.x - L.cx) * f, y: L.cy + (h.y - L.cy) * f, rx: L.hubR + (h.rx - L.hubR) * f, ry: L.hubR + (h.ry - L.hubR) * f };
  return project;
}

// The 3D figure's scale at `elevation`: `vs` dots per figure radius (the same across and up) and `mid`, the figure's
// middle on screen (in radii, up). The figure fills the canvas's rows (centred, and it does not grow and shrink as it
// turns: its highest and lowest point at any turn), and never reaches past the canvas's sides, near side included.
function figureScale(L, elevation) {
  const ce = Math.cos(elevation);
  const se = Math.sin(elevation);
  const scale = (depth) => PERSPECTIVE / (PERSPECTIVE - depth);
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
  const near = PERSPECTIVE / (PERSPECTIVE - 1); // the near side's magnification
  const vs = Math.min((L.fitY * 0.94) / Math.max(0.35, (hi - lo) / 2), (L.w - 2) / near);
  return { vs, mid: (hi + lo) / 2 };
}

// camera3D in dots: project(point) = { x, y, depth }
export function cameraDots(L, turn, elevation) {
  const ce = Math.cos(elevation);
  const se = Math.sin(elevation);
  const scale = (depth) => PERSPECTIVE / (PERSPECTIVE - depth);
  const { vs, mid } = figureScale(L, elevation);
  const hs = vs; // one scale for across and up: a dot is square, so the figure is never stretched to the panel's shape
  const ct = Math.cos(turn);
  const st = Math.sin(turn);
  const dots = (p) => {
    const x = p[0] * ct + p[1] * st;
    const y = -p[0] * st + p[1] * ct;
    const up = y * se + p[2] * ce;
    const depth = -y * ce + p[2] * se;
    const s = scale(depth);
    return { x: L.cx + x * s * hs, y: L.cy - (up * s - mid) * vs, depth };
  };
  // the hub ball (a sphere of HUB_RADIUS3 figure radii at the origin): its centre and radii on screen, in dots
  const hubRho = hubRadius3(L);
  dots.hub = { x: L.cx, y: L.cy + mid * vs, rx: hubRho * hs, ry: hubRho * vs };
  dots.hubRho = hubRho;
  return dots;
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
// The hub as a ball, and as the sun while it works
//
// At rest the hub is a ball shaded from a light at the upper left (2D: braille dots dithered denser toward the light on a
// dark theme, the other way on a light one, so the lit side reads lighter either way; 3D: the depth characters, `@`
// toward the light on a dark theme), and it is still. While the hub itself works it is the sun: SPIKES shoot out from
// its rim and draw back, each at its own seeded time (SPIKE_MIN_MS to SPIKE_MAX_MS a spike, 7 to 12 spikes by the hub's
// size), reaching at most SPIKE_REACH of the ball's radius beyond the rim, and a faint glow swells round it. They
// start when the hub starts working (ramped in over SUN_RAMP_MS) and die down within SUN_FADE_MS of its stopping (the
// scene knows when from `previous`). Reduced motion: short still spikes, no swell. The spikes and the glow are
// drawn first, under every spoke, arc, agent and name, in warm colours: the build group's orange toward a pale
// yellow core (none of them a group colour, none between 80 and 190 degrees).

export const SUN_FADE_MS = 1000;
export const SUN_RAMP_MS = 300;
export const SPIKE_MIN_MS = 600;
export const SPIKE_MAX_MS = 1600;
export const SPIKE_REACH = 0.6; // of the ball's radius, beyond its rim
export const SUN_COLORS = Object.freeze({
  dark: Object.freeze({ core: "#ffe9a8", flame: "#f2a65a", glow: "#a9673f" }),
  light: Object.freeze({ core: "#b87200", flame: "#c0501a", glow: "#cf8a60" }),
});
const SUN_SEED = 0x5a17c0de;
const SUN_REDUCED_REACH = 0.3;
const GLOW_MS = 1400; // the glow's swell
const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
const LIGHT = (() => {
  const v = [-0.5, 0.55, 0.7]; // toward the viewer, upper left (x right, y up, z out of the screen)
  const l = Math.hypot(...v);
  return v.map((x) => x / l);
})();

function hubOn(doc) {
  return isObject(doc) && hubWorking(doc, findHub(doc, validAgents(doc)));
}

// How lit the sun is at `now`, 0 to 1: 1 while the hub works (ramped in over SUN_RAMP_MS after it began, as the
// `previous` document shows), and falling to 0 over SUN_FADE_MS after it stopped.
export function sunLevel(doc, options, now) {
  const prev = isObject(options) && isObject(options.previous) && isObject(options.previous.doc) ? options.previous : null;
  const at = prev ? Number(prev.at) : NaN;
  const t = Number(now) || 0;
  if (hubOn(doc)) return prev && Number.isFinite(at) && !hubOn(prev.doc) ? clamp((t - at) / SUN_RAMP_MS, 0, 1) : 1;
  return prev && Number.isFinite(at) && hubOn(prev.doc) ? clamp(1 - (t - at) / SUN_FADE_MS, 0, 1) : 0;
}

// The hub stopped less than SUN_FADE_MS ago: the spikes are still drawing back, so frames keep coming.
function sunFading(doc, options, now) {
  const level = sunLevel(doc, options, now);
  return level > 0 && level < 1 && !hubOn(doc);
}

export function spikeCount(radius) {
  return clamp(Math.round(7 + ((radius - 5) / 4) * 5), 7, 12);
}

function smooth(x) {
  const v = clamp(x, 0, 1);
  return v * v * (3 - 2 * v);
}

// The spikes at `now`: [{ i (the spike's number), angle (radians, on screen), dir (a unit vector in the figure, for 3D), reach (0 to
// SPIKE_REACH of the radius) }]. Spike i has its own length of life (SPIKE_MIN_MS to SPIKE_MAX_MS) and its own start; each life
// shoots out (30 %) and draws back, takes a new direction and length from its seed, and one life in five is skipped,
// so they pop in and out out of step. `level` scales every reach (1: fully lit). Reduced motion: every spike still and short, shown while
// `level` is at least a half.
export function sunSpikes(n, now, level, reduced) {
  const out = [];
  if (!(level > 0)) return out;
  if (reduced && level < 0.5) return out;
  for (let i = 0; i < n; i += 1) {
    const dur = SPIKE_MIN_MS + (SPIKE_MAX_MS - SPIKE_MIN_MS) * unit(SUN_SEED + i, 11);
    const t = (Number(now) || 0) + unit(SUN_SEED + i, 12) * 7000;
    const life = reduced ? 0 : Math.floor(t / dur);
    const u = reduced ? 0 : (t - life * dur) / dur;
    const seed = hash32(SUN_SEED + i * 7919, life + 3);
    if (!reduced && unit(seed, 1) < 0.2) continue;
    const angle = ((i + 0.5) / n) * 2 * Math.PI + (unit(seed, 2) - 0.5) * (2 * Math.PI / n) * 0.9;
    const z = (unit(seed, 3) - 0.5) * 1.2;
    const flat = Math.sqrt(1 - z * z);
    const size = 0.5 + 0.5 * unit(seed, 4);
    const reach = reduced ? SUN_REDUCED_REACH * (0.7 + 0.3 * unit(seed, 4)) : SPIKE_REACH * size * level * (u < 0.3 ? smooth(u / 0.3) : smooth((1 - u) / 0.7));
    if (reach > 0.02) out.push({ i, angle, dir: [Math.sin(angle) * flat, Math.cos(angle) * flat, z], reach });
  }
  return out;
}

// How lit a point of the ball is: its normal (x right, y up, z toward the viewer) against the light, 0 to 1.
function ballLum(nx, nyUp, nz) {
  return clamp(nx * LIGHT[0] + nyUp * LIGHT[1] + nz * LIGHT[2], 0, 1);
}

function sunColors(P) {
  return P.dark ? SUN_COLORS.dark : SUN_COLORS.light;
}

// 2D: the glow and the spikes (under everything else); `R` the ball's radius in dots.
function drawSun2D(canvas, S, L, P, now, level, reduced) {
  if (!(level > 0)) return;
  const sun = sunColors(P);
  const R = L.hubR;
  const glow = S.get("plain", sun.glow, 1);
  const swell = reduced ? 0.5 : 0.5 + 0.5 * Math.sin((2 * Math.PI * now) / GLOW_MS);
  const gr = R + 1 + Math.round(swell * level * Math.max(1, Math.floor(R * 0.3)));
  const gn = Math.ceil((2 * Math.PI * gr) / 3);
  for (let i = 0; i < gn; i += 1) {
    const a = (i / gn) * 2 * Math.PI;
    canvas.dot(L.cx + gr * Math.cos(a), L.cy + gr * Math.sin(a), glow);
  }
  const core = S.get("plain", sun.core, 1);
  const flame = S.get("plain", sun.flame, 1);
  for (const sp of sunSpikes(spikeCount(R), now, level, reduced)) {
    const len = sp.reach * R;
    const dx = Math.sin(sp.angle);
    const dy = -Math.cos(sp.angle);
    for (let d = Math.floor(len); d >= 1; d -= 1) {
      const x = L.cx + dx * (R + d);
      const y = L.cy + dy * (R + d);
      const st = d <= len * 0.45 ? core : flame;
      canvas.dot(x, y, st);
      if (d <= len * 0.6) canvas.dot(x - dy, y + dx, st); // thicker toward the base
    }
  }
}

// 2D: the ball: a rim, and inside it the shaded dots (dithered, static).
function drawBall2D(canvas, S, L, P, r, tint) {
  const R = L.hubR;
  const plain = S.get("plain", undefined, 3);
  const body = tint ? S.get("plain", tint, 3) : plain;
  const n = Math.ceil(2 * Math.PI * r * 1.2);
  for (let i = 0; i < n; i += 1) {
    const a = (i / n) * 2 * Math.PI;
    canvas.dot(L.cx + r * Math.cos(a), L.cy + r * Math.sin(a), plain);
  }
  for (let dy = -R + 1; dy <= R - 1; dy += 1) {
    for (let dx = -R + 1; dx <= R - 1; dx += 1) {
      const d2 = (dx * dx + dy * dy) / (R * R);
      if (d2 >= 0.72) continue; // the rim holds the edge
      const lum = ballLum(dx / R, -dy / R, Math.sqrt(1 - d2));
      const dens = 0.06 + 0.42 * (P.dark ? lum : 1 - lum);
      const x = L.cx + dx;
      const y = L.cy + dy;
      if (dens > (BAYER[(y & 3) * 4 + (x & 3)] + 0.5) / 16) canvas.dot(x, y, body);
    }
  }
}

// 3D: the flares and the glow, drawn over the rings and spokes (SUN_TOP is above every figure depth; only the text layer,
// the agents' glyphs and names, stays on top) and in characters that are none of the depth characters: a flame is a run
// of `|` `-` `/` `\` along its direction on the screen and ends in an arrow, `^` `v` `<` `>`, pointing outward; the glow
// is a ring of backticks (the sky's stars are `'` `+` `*`). A cell is drawn only if its centre is outside the ball's rim and no farther than SPIKE_REACH of the
// ball's radius beyond it (so a flame that points at the camera, or away, shows only where it clears the ball).
const SUN_TOP = 4;
export const FLARE_BODY = Object.freeze(["|", "-", "/", "\\"]);
export const FLARE_TIPS = Object.freeze(["^", "v", "<", ">"]);
export const GLOW_CHAR = "`";
function ballRadiusOf(hub, c, r) {
  const dx = (c * 2 + 1 - hub.x) / hub.rx;
  const dy = (r * 4 + 2 - hub.y) / hub.ry;
  return Math.hypot(dx, dy);
}
function flareChar(hub, c, r, tip) {
  const vx = c * 2 + 1 - hub.x;
  const vy = r * 4 + 2 - hub.y;
  if (tip) return Math.abs(vx) > Math.abs(vy) ? (vx > 0 ? ">" : "<") : vy > 0 ? "v" : "^";
  const m = Math.hypot(vx, vy) || 1;
  if (Math.abs(vy) / m > 0.92) return "|";
  if (Math.abs(vx) / m > 0.92) return "-";
  return vx * vy < 0 ? "/" : "\\";
}
function drawSun3D(canvas, S, project, P, now, level, reduced, count) {
  if (!(level > 0)) return;
  const sun = sunColors(P);
  const hub = project.hub;
  const reach = 1 + SPIKE_REACH;
  const glow = S.get("plain", sun.glow);
  const swell = reduced ? 0.5 : 0.5 + 0.5 * Math.sin((2 * Math.PI * now) / GLOW_MS);
  const gr = 1.12 + 0.2 * swell * level;
  const gn = Math.max(24, Math.ceil(2 * Math.PI * Math.max(hub.rx / 2, hub.ry / 2)));
  for (let i = 0; i < gn; i += 2) {
    const a = (i / gn) * 2 * Math.PI;
    const c = Math.floor((hub.x + hub.rx * gr * Math.cos(a)) / 2);
    const r = Math.floor((hub.y + hub.ry * gr * Math.sin(a)) / 4);
    const d = ballRadiusOf(hub, c, r);
    if (d > 1 && d <= reach) canvas.plot(c, r, GLOW_CHAR, glow, SUN_TOP - 0.1);
  }
  const core = S.get("plain", sun.core);
  const flame = S.get("plain", sun.flame);
  for (const sp of sunSpikes(count, now, level, reduced)) {
    const pts = [];
    for (let j = 0; j <= 12; j += 1) {
      const rho = (project.hubRho || HUB_RADIUS3) * (1 + (sp.reach * j) / 12);
      pts.push([sp.dir[0] * rho, sp.dir[1] * rho, sp.dir[2] * rho]);
    }
    const cells = cellPath(project, pts).filter((c) => {
      const d = ballRadiusOf(hub, c.col, c.row);
      return d > 1 && d <= reach;
    });
    cells.forEach((c, i) => {
      const f = (i + 1) / cells.length;
      canvas.plot(c.col, c.row, flareChar(hub, c.col, c.row, i === cells.length - 1), f <= 0.5 ? core : flame, SUN_TOP);
    });
  }
}

// 3D: the ball: a cell inside its outline takes the shade for how lit it is, in block shades none of the rings' or the
// flares' characters (BALL_SHADES, light to dense: lighter toward the upper left, denser toward the lower right).
export const BALL_SHADES = Object.freeze(["░", "▒", "▓", "█"]);
function drawBall3D(canvas, project, style, P) {
  const hub = project.hub;
  const c0 = Math.floor((hub.x - hub.rx) / 2) - 1;
  const c1 = Math.ceil((hub.x + hub.rx) / 2) + 1;
  const r0 = Math.floor((hub.y - hub.ry) / 4) - 1;
  const r1 = Math.ceil((hub.y + hub.ry) / 4) + 1;
  for (let r = r0; r <= r1; r += 1) {
    for (let c = c0; c <= c1; c += 1) {
      const dx = (c * 2 + 1 - hub.x) / hub.rx;
      const dy = (r * 4 + 2 - hub.y) / hub.ry;
      const d2 = dx * dx + dy * dy;
      if (d2 > 1) continue;
      const nz = Math.sqrt(1 - d2);
      const lum = ballLum(dx, -dy, nz);
      const k = clamp(Math.round((1 - lum) * (BALL_SHADES.length - 1)), 0, BALL_SHADES.length - 1);
      canvas.plot(c, r, BALL_SHADES[k], style, (project.hubRho || HUB_RADIUS3) * nz);
    }
  }
}

// ---------------------------------------------------------------------------------------------------------------
// 2D: the ring from above, in braille dots

function draw2D(doc, L, canvas, S, P, now, frame, options) {
  const animated = isAnimated(doc);
  const reduced = options.motion === "reduced";
  const level = sunLevel(doc, options, now);
  const muted = S.get("muted");
  const restOf = (key) => S.get("plain", P.rest(key), 2);
  const fullOf = (key) => S.get("plain", P.full(key), 4);

  // the orbit: a faint dotted ellipse the agents sit on
  for (let a = 0; a < L.ring.length; a += 5) {
    const [x, y] = L.ring.at(a);
    canvas.dot(x, y, muted);
  }
  // the sun's glow and spikes, under the spokes, the arcs, the agents and the names
  drawSun2D(canvas, S, L, P, now, level, reduced);
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
  // the hub: a shaded ball with a rim that is one dot wider while anything works (the sun's glow and spikes: drawSun2D)
  const r = L.hubR + (animated && !reduced && Math.sin((2 * Math.PI * now) / 1600) > 0 ? 1 : 0); // reduced motion: no breathing
  const plain = S.get("plain", undefined, 3);
  drawBall2D(canvas, S, L, P, r, level > 0 ? sunColors(P).core : null);
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
  // the names have their places from the layout (placeNames): none overlaps another name, a glyph or the hub
  const shown = new Set();
  for (const p of L.placed) {
    if (!p.name) continue;
    canvas.put(p.col + p.dir, p.row, " ", 0); // a clear cell between the glyph and its name
    canvas.put(p.name.c0, p.row, p.name.text, agentStyle(S, P, p, true));
    shown.add(p);
  }
  // a working agent's goal and spend: on the row below its name, where there is room (not over the hub's ball, and not
  // on a row of another agent's glyph and name, where it would read as that agent's)
  const muted = S.get("muted");
  const box = L.hubBox;
  const agentRows = new Set(L.placed.map((p) => p.row));
  for (const p of L.placed) {
    if (p.state !== "working" || p.row + 1 >= L.h || !shown.has(p) || agentRows.has(p.row + 1)) continue;
    const start = p.col + 2 * p.dir;
    let room = canvas.room(start, p.row + 1, p.dir, 30);
    if (p.row + 1 >= box.r0 && p.row + 1 <= box.r1) {
      const edge = p.dir > 0 ? box.c0 - 1 - start : start - box.c1 - 1; // cells before the ball and a clear cell
      if (edge >= 0) room = Math.min(room, edge);
    }
    const n = workRounds(doc, p.agent.name);
    const tail = (n >= 2 ? `${LOOP_MARK}${n} ` : "") + formatDollars(agentCost(p.agent));
    const goal = currentGoal(isObject(doc.graph) ? doc.graph : null, p.agent.name);
    let text = "";
    if (goal && room - displayWidth(tail) - 1 >= 4) text = truncate(goal, room - displayWidth(tail) - 1) + " " + tail;
    else if (displayWidth(tail) <= room) text = tail;
    if (!text) continue;
    const tw = displayWidth(text);
    canvas.put(p.dir > 0 ? start : start - tw + 1, p.row + 1, text, muted);
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

// `cam`: { turn, elevation } and, during the morph between the views, `morph` (0: the 2D places, 1: the 3D view).
function draw3D(doc, L, canvas, S, P, now, frame, cam, options) {
  const animated = isAnimated(doc);
  const reduced = options.motion === "reduced";
  const level = sunLevel(doc, options, now);
  const morphing = Number.isFinite(cam.morph) && cam.morph < 1;
  const F = morphing ? figure3D(L, cam.morph) : figure3D(L);
  const project = morphing ? morphCamera(L, cam.turn, cam.elevation, cam.morph) : camera3D(L, cam.turn, cam.elevation);
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
  // the hub: a shaded ball, in the accent tone while the orchestrator works (the sun lit; its glow and spikes under all)
  const hubStyle = hubWorking(doc, L.hub) ? S.get("accent", undefined, 4) : S.get("plain", undefined, 3);
  drawSun3D(canvas, S, project, P, now, level, reduced, spikeCount(L.hubR));
  drawBall3D(canvas, project, hubStyle, P);
  // the moons' orbits: a faint dotted ring each, round its parent
  for (const ring of L.w >= MOON_RINGS_MIN_W && L.h >= MOON_RINGS_MIN_H ? F.moonRings : []) {
    cellPath(project, ring.points).forEach((q, i) => {
      if (i % 2) return;
      const lvl = depthLevel(q.depth);
      canvas.plot(q.col, q.row, DEPTH_CHARS[Math.max(0, Math.min(2, lvl - 1))], S.get("plain", P.depth(ring.key, Math.max(1, lvl)), 2), q.depth - 0.02);
    });
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
  // the sky keeps clear of every place a moon, or its name, can reach: a star next to a moon would blink in and out as
  // the moon goes round
  const reach = F.seats.reduce((m, x) => Math.max(m, displayWidth(truncate(clean(seatName(x.agent.name)) || "?", NAME_MAX))), 1) + 2;
  for (const ring of F.moonRings) {
    for (const q of cellPath(project, ring.points)) {
      for (let r = q.row - 1; r <= q.row + 1; r += 1) for (let c = q.col - reach; c <= q.col + reach; c += 1) canvas.reserve(c, r);
    }
  }
  // moons: where they are on their rings now (a lap in MOON_LAP_MS from the clock; still with reduced motion)
  const phase = moonPhase(now, reduced);
  const moons = F.seats.map((seat) => ({ seat, q: project(moonPoint(seat, phase)) }));
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
  canvas.put(hubAt.col, hubAt.row, MARK_HUB, hubStyle);
  const blinkOn = Math.floor(now / 500) % 2 === 0;
  const items = L.placed.map((p, k) => ({ p, k, q: project(F.agents.get(p.agent.name)) })).sort((a, b) => b.q.depth - a.q.depth);
  for (const { p, k, q } of items) {
    if (!canvas.free(q.col, q.row)) continue;
    let st = agentStyle(S, P, p, blinkOn);
    if (p.state !== "working" && p.state !== "stuck" && q.depth < 0) st = S.get("plain", P.depth(p.key, 0), 2);
    canvas.put(q.col, q.row, glyphFor(p, k, frame, blinkOn), st);
  }
  // names: the planets' first, nearest first, so a moon never moves a planet's name; then the moons' glyphs where their
  // cells are still free (a moon behind its parent, or under a name, shows none) and their names, nearest first
  const placeLabels = (labels) => {
    labels.sort((a, b) => b.q.depth - a.q.depth);
    for (const { text, q, key, stuck, lit } of labels) {
      const tw = displayWidth(text);
      const st = stuck ? S.get("danger") : q.depth >= 0 || lit ? S.get("plain", P.full(key), 4) : muted;
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
      canvas.put(spot[0], spot[1], text, st);
    }
  };
  placeLabels(items.map(({ p, q }) => ({ text: p.label, q, key: p.key, stuck: p.state === "stuck", lit: false })));
  const moonLabels = [];
  for (const { seat, q } of [...moons].sort((a, b) => b.q.depth - a.q.depth)) {
    if (!canvas.free(q.col, q.row)) continue;
    const busy = busyCount(seat.agent) > 0;
    canvas.put(q.col, q.row, "•", busy ? S.get("plain", P.full(seat.key), 4) : S.get("plain", P.depth(seat.key, Math.min(4, depthLevel(q.depth))), 2));
    moonLabels.push({ text: truncate(clean(seatName(seat.agent.name)) || "?", NAME_MAX), q, key: seat.key, stuck: false, lit: busy });
  }
  placeLabels(moonLabels);
}

// ---------------------------------------------------------------------------------------------------------------
// The sky (3D only): drawn into the figure's layer below every figure cell, so a ring, spoke, arc or seat covers it,
// and under the text layer, so it never covers a name, an agent's glyph, the clear cell beside a name or the keys
// line. Nothing in it moves by itself: it shifts only as the camera turns, and a streak flies only for a run.

function hash32(a, b) {
  let x = Math.imul((a | 0) ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul((b | 0) + 0x632be5ab, 0xc2b2ae35);
  x = Math.imul(x ^ (x >>> 16), 0x7feb352d);
  x = Math.imul(x ^ (x >>> 15), 0x846ca68b);
  return (x ^ (x >>> 16)) >>> 0;
}

const unit = (a, b) => hash32(a, b) / 4294967296;

// The finished runs that passed, per drawn path (structure.flows[].stats.passed; a failed run adds none):
// [{ flow, passed }] in the snapshot's order.
export function finishedRuns(doc) {
  return flowsOf(doc).map((f) => ({ flow: f.id, passed: Math.max(0, Math.floor(finiteNumber(f.stats && f.stats.passed) || 0)) }));
}

// The run stars: one per finished run, [{ flow, run, u, v }] (u, v in [0, 1): its place on the sky, from the path's
// id and the run's number), every path's first run, then every path's second, and so on, at most RUN_STARS_MAX.
// Beyond that a new run adds no star (and no streak).
export function runStars(doc) {
  const runs = finishedRuns(doc).filter((r) => r.passed > 0);
  const out = [];
  for (let n = 1; out.length < RUN_STARS_MAX && runs.some((r) => r.passed >= n); n += 1) {
    for (const r of runs) {
      if (r.passed < n || out.length >= RUN_STARS_MAX) continue;
      const seed = nameSeed(r.flow + "#" + n);
      out.push({ flow: r.flow, run: n, u: unit(seed, 1), v: unit(seed, 2), down: (seed & 1) === 1 });
    }
  }
  return out;
}

// The sky's colours from the theme: field stars by size and the run stars (warmer and brighter than the field;
// dark theme warm white, light theme brown ink). Hues stay outside green.
export function skyColors(colors) {
  const dark = !(isObject(colors) && colors.dark === false);
  const muted = greyOf(isObject(colors) && parseHex(colors.muted) ? colors.muted : dark ? "#808080" : "#8c8c8c"); // its lightness, no hue
  const warm = mix(muted, dark ? "#f0dcc0" : "#6e5440", 0.3);
  const background = dark ? "#000000" : "#ffffff";
  return { stars: STAR_FADE.map((t) => mix(warm, background, t)), run: mix(muted, dark ? "#ffd9a0" : "#8a4f1c", 0.65) };
}

// Where the sky stands for the camera (turn, elevation): a sky `V` cells wide (at least the canvas) wrapping round,
// shifted by V a full turn (so it is the same at turn 0 and 2 PI) and by rows as the elevation changes. -> place(u, v)
function skyPlace(L, cam) {
  const w = L.w;
  const h = L.h;
  const frontCells = figureScale(L, ELEVATION_3D).vs / 2; // the near side's movement, cells per radian
  const V = Math.max(w, Math.round(2 * Math.PI * frontCells * PARALLAX));
  const turn = wrapTurn(Number(cam.turn) || 0);
  const dc = Math.round((turn / (2 * Math.PI)) * V); // the far side's way: rightward as the turn grows; whole cells, so the sky moves as one
  const elevation = Number.isFinite(Number(cam.elevation)) ? Number(cam.elevation) : ELEVATION_3D;
  const dr = -Math.round((elevation - ELEVATION_3D) * (L.ry / 4) * PARALLAX); // up as the camera rises
  return {
    V,
    place(u, v) {
      const col = Math.floor((((u * V + dc) % V) + V) % V);
      const row = Math.floor((((v * h + dr) % h) + h) % h);
      return col < w ? { col, row } : null;
    },
  };
}

// The sky of a 3D frame, drawn after the figure: a field star only where it and its neighbours are empty, so it
// never touches a ring, a name or a glyph; a run's star and a streak wherever their own cell is empty. `shown` (0 to 1): how much of the sky is in (the morph fades it in toward 3D, out toward
// 2D: a star shows once the morph passes its own threshold).
function drawSky(doc, L, canvas, S, cam, now, shown, options) {
  if (!(shown > 0)) return;
  const reduced = options.motion === "reduced";
  if (reduced && shown < 1) return; // reduced motion: no fade, shown in steady 3D only
  const C = skyColors(options.colors);
  const { V, place } = skyPlace(L, cam);
  const n = Math.max(1, Math.round(V * L.h * STAR_DENSITY));
  const styles = C.stars.map((c) => S.get("muted", c, 1));
  for (let i = 0; i < n; i += 1) {
    if (unit(SKY_SEED + i, 4) >= shown) continue;
    const at = place(unit(SKY_SEED + i, 1), unit(SKY_SEED + i, 2));
    if (!at) continue;
    const k = hash32(SKY_SEED + i, 3) % 10;
    const size = k < 6 ? 0 : k < 9 ? 1 : 2; // mostly small
    if (canvas.open(at.col, at.row)) canvas.plot(at.col, at.row, STAR_CHARS[size], styles[size], SKY_DEPTH); // clear of the figure
  }
  // the run stars; a run whose streak has not landed yet has no star
  const flying = new Map();
  for (const st of Array.isArray(options.streaks) ? options.streaks : []) {
    if (isObject(st) && typeof st.flow === "string" && Number.isFinite(Number(st.at))) flying.set(st.flow + "#" + Math.floor(Number(st.run)), Number(st.at));
  }
  const runStyle = S.get("muted", C.run, 2);
  for (const star of runStars(doc)) {
    if (unit(nameSeed(star.flow + "#" + star.run), 4) >= shown) continue;
    const at = place(star.u, star.v);
    const start = flying.get(star.flow + "#" + star.run);
    if (start !== undefined && !reduced && shown >= 1) {
      const t = (now - start) / STREAK_MS;
      if (t < 0) continue; // its streak is waiting its turn
      if (t < 1) {
        streak(canvas, star, at, t, S, C);
        continue;
      }
    } else if (start !== undefined && now < start) continue;
    if (at) canvas.plot(at.col, at.row, RUN_STAR_CHAR, runStyle, SKY_DEPTH + 0.5);
  }
}

// A shooting star, `t` (0 to 1) of its flight: a head and a short tail along a diagonal ending at the run's star. It
// comes down from the left or the right (by the run's seed), or up from below when the star is too near the top for
// the streak to fit, and from the other side when it would start off the canvas.
function streak(canvas, star, end, t, S, C) {
  if (!end) return;
  const dy = end.row - (STREAK_LEN - 1) >= 0 ? 1 : -1; // 1: coming down
  let dx = star.down ? 1 : -1; // 1: coming from the left
  const startCol = end.col - dx * (STREAK_LEN - 1);
  if (startCol < 0 || startCol >= canvas.w) dx = -dx;
  const tailChar = dx * dy > 0 ? STREAK_CHARS.down : STREAK_CHARS.up;
  const cells = [];
  for (let j = STREAK_LEN - 1; j >= 0; j -= 1) cells.push({ col: end.col - dx * j, row: end.row - dy * j });
  const head = Math.floor(t * (cells.length + 2));
  const headStyle = S.get("muted", C.run, 2);
  const tailStyle = S.get("muted", C.stars[1], 1);
  for (let k = 0; k < 3; k += 1) {
    const c = cells[head - k];
    if (!c) continue;
    canvas.plot(c.col, c.row, k === 0 ? STREAK_CHARS.head : tailChar, k === 0 ? headStyle : tailStyle, STREAK_DEPTH);
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

// The camera for a frame on its way between the views (null when there is no change): the 3D camera at `now` (the
// orbit's, as the steady 3D view would have it) and how far the morph is toward it, eased (smoothstep).
function cameraAt(options, now) {
  const change = changeAt(options, now);
  if (!change) return null;
  const target = orbitCamera(options.orbit, now);
  const e = change.t * change.t * (3 - 2 * change.t); // smoothstep
  return { turn: target.turn, elevation: target.elevation, morph: change.to === "3d" ? e : 1 - e };
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
  const cam = moving || (options.view === "3d" ? orbitCamera(options.orbit, now) : null);
  if (cam) {
    draw3D(doc, L, canvas, styles, P, now, frame, cam, options);
    if (options.sky !== false) drawSky(doc, L, canvas, styles, cam, now, moving ? moving.morph : 1, options);
  } else draw2D(doc, L, canvas, styles, P, now, frame, options);
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
