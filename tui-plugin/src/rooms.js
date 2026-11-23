/*
 * The rooms: a pixel-art floor plan of an agentgraph snapshot (WEB_API.md section 1) for the session panel, the picture the
 * panel opens on (the orbit, src/scene.js, is one key away). One room per kind of work: TERMINAL, FILES, WEB / SEARCH,
 * THINKING, TEAM. The main chat (the hub) is a character standing in the room of what it does now; every other agent is a
 * smaller character in the room of what it does; idle agents rest in TEAM. The idea of zones, a short trail and a small
 * floor plan comes from AgentMove (github.com/FoothillSolutions/agent-move, MIT); the code is our own.
 *
 * roomsScene(snapshot, { width, height, now, motion, colors, keys }) -> Line[]
 *   Exactly `height` lines, each exactly `width` display cells. A line is Span[] with
 *   Span = { text, tone, color?, bg?, bold? }: tone one of plain, muted, accent, danger; `color` the foreground and `bg` the
 *   background, each an optional "#rrggbb" the host uses instead of the tone's colour; `bold` true for bold text.
 *   now       the clock in ms (default 0); every motion is a function of the document and `now`, nothing is stored
 *   motion    "reduced": nothing moves
 *   colors    { dark, muted }: as the orbit's (a light theme gets no coloured text outside the picture)
 *   keys      [[key, label], ...]: named on the NOW row's right side when there is room
 * The rows: the header, up to three lines of what needs the user, the picture, `NOW`, `BEFORE`, one line per working agent.
 * roomsLines(doc, w, h, now, options) is the picture alone; roomsInfo(...) says where everything stands (for the tests).
 *
 * A pixel is half a cell: `▀` with the top pixel as its foreground and the bottom one as its background; two equal
 * pixels are a space with that background. Names and titles are ordinary text over the floor.
 */
import {
  MARK_HUB, ATTENTION, SENT_BACK, LOOP_MARK, MARK_IDLE, WAITING, ZONE_VERBS, TOOL_VERBS, seatName,
  charWidth, displayWidth, truncate, clean, lineWidth, formatDollars, isObject, list, busyCount, finiteNumber, validAgents, findHub,
  hubWorking, currentGoal, agentCost, needsEntries, isAnimated,
} from "./render.js";
import { scene as orbitScene, makePalette } from "./scene.js";

export const ROOMS_MIN_WIDTH = 36;
export const ROOMS_MIN_HEIGHT = 14;
export const WALK_MS = 1200; // the hub walks to the room of a new action for this long
export const BOB_MS = 500; // a working character moves up and down one pixel every BOB_MS
const NEEDS_MAX = 3;
const WORKING_MAX = 5;
const NAME_MAX = 12;
const BEFORE_ITEM_MAX = 16;

const BG = [13, 15, 20];
const ZONE_RGB = {
  terminal: [74, 222, 128], files: [251, 191, 36], web: [96, 165, 250], search: [192, 132, 252], thinking: [244, 114, 182], team: [148, 163, 184],
};
const HUB_GOLD = [250, 204, 21];
const ROOMS = [["terminal", 0, 0], ["files", 1, 0], ["web", 2, 0], ["search", 0, 1], ["thinking", 1, 1], ["team", 2, 1]];
const TITLES = { terminal: [">_", "TERMINAL", "TERM"], files: ["▤", "FILES", "FILES"], web: ["◍", "WEB", "WEB"], search: ["⌕", "SEARCH", "SEARCH"], thinking: ["…", "THINKING", "THINK"], team: ["☾", "TEAM", "TEAM"] };
const ZONE_GLYPH = { terminal: ">_", files: "▤", web: "◍", search: "⌕", thinking: "…", spawn: "✦", messaging: "»", tasks: "☰", tools: "◇" };
const ROOM_OF_ZONE = { terminal: "terminal", files: "files", web: "web", search: "search", thinking: "thinking", spawn: "team", messaging: "team", tasks: "team", tools: "terminal" };
const FALLBACK_COLORS = [[248, 113, 113], [45, 212, 191], [129, 140, 248], [251, 146, 60], [232, 121, 249], [163, 230, 53]];
const SKIN = [245, 208, 170];
const EYE = [15, 23, 42];
const ANTENNA = [253, 224, 71];
const WHITE = [255, 255, 255];

// The zone of a tool name, as the back end's table (agentgraph/chatfeed.py zone_of) for the agents whose activity carries none.
const ZONE_NAMES = {
  read: "files", write: "files", edit: "files", multiedit: "files", glob: "files", patch: "files", apply_patch: "files", applypatch: "files", list: "files", ls: "files",
  shell: "terminal", bash: "terminal", terminal: "terminal", grep: "search", websearch: "search", codesearch: "search", webfetch: "web",
  question: "thinking", skill: "thinking", subagent: "spawn", task: "spawn",
};
const ZONE_TOKENS = [
  ["messaging", ["message", "messages", "reply", "replies"]], ["tasks", ["todo", "todos", "todowrite", "todoread"]], ["thinking", ["plan", "think", "thinking"]],
  ["web", ["browser", "playwright", "puppeteer", "webfetch", "fetch", "navigate"]], ["search", ["search", "grep"]],
];

export function zoneOfTool(tool) {
  const name = clean(tool).toLowerCase();
  if (Object.hasOwn(ZONE_NAMES, name)) return ZONE_NAMES[name];
  const words = name.split(/[^a-z0-9]+/);
  if (words.some((w) => w.startsWith("browser") || w.startsWith("playwright"))) return "web";
  for (const [zone, wanted] of ZONE_TOKENS) if (words.some((w) => wanted.includes(w))) return zone;
  return "tools";
}

// The zone of an activity: its own, else the thinking of reasoning and replying, else its tool's.
function zoneOfActivity(act) {
  if (!isObject(act)) return null;
  if (typeof act.zone === "string" && act.zone !== "") return Object.hasOwn(ROOM_OF_ZONE, act.zone) ? act.zone : "tools"; // an unknown zone is the terminal's
  if (act.kind === "reasoning" || act.kind === "text") return "thinking";
  if (act.kind === "tool") return zoneOfTool(act.tool);
  return null;
}

// The room of an activity: unknown goes to the terminal, a working agent with no activity to thinking.
export function roomOfActivity(act, unknown = "terminal") {
  const zone = zoneOfActivity(act);
  if (zone) return ROOM_OF_ZONE[zone];
  return isObject(act) && typeof act.zone === "string" ? "terminal" : unknown;
}

// ---------------------------------------------------------------------------------------------------------------
// Colours and the pixel canvas

const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
const hexes = new Map(); // rounded colour -> "#rrggbb": a frame names a few dozen colours over thousands of cells
const hex = (c) => {
  const r = Math.max(0, Math.min(255, Math.round(c[0])));
  const g = Math.max(0, Math.min(255, Math.round(c[1])));
  const b = Math.max(0, Math.min(255, Math.round(c[2])));
  const key = (r << 16) | (g << 8) | b;
  let out = hexes.get(key);
  if (out === undefined) {
    out = "#" + (key | 0x1000000).toString(16).slice(1);
    hexes.set(key, out);
  }
  return out;
};
const rgbOf = (value) => {
  const m = typeof value === "string" ? /^#([0-9a-f]{6})$/i.exec(value.trim()) : null;
  return m ? [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)) : null;
};
const same = (a, b) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2];

class Pix {
  constructor(w, rows) {
    this.w = w;
    this.rows = rows;
    this.h = rows * 2;
    this.px = new Array(w * this.h).fill(null);
    this.txt = new Array(w * rows).fill(null);
    this.taken = new Uint8Array(w * this.h);
  }

  set(x, y, c) {
    if (x >= 0 && x < this.w && y >= 0 && y < this.h) this.px[y * this.w + x] = c;
  }

  get(x, y) {
    return x >= 0 && x < this.w && y >= 0 && y < this.h ? this.px[y * this.w + x] || BG : BG;
  }

  rect(x0, y0, x1, y1, c) {
    for (let y = y0; y <= y1; y += 1) for (let x = x0; x <= x1; x += 1) this.set(x, y, c);
  }

  sprite(x, y, rows, pal) {
    rows.forEach((row, j) => {
      for (let i = 0; i < row.length; i += 1) if (row[i] !== "." && pal[row[i]]) this.set(x + i, y + j, pal[row[i]]);
    });
  }

  free(x0, y0, x1, y1) {
    if (x0 < 0 || y0 < 0 || x1 >= this.w || y1 >= this.h) return false;
    for (let y = y0; y <= y1; y += 1) for (let x = x0; x <= x1; x += 1) if (this.taken[y * this.w + x]) return false;
    return true;
  }

  take(x0, y0, x1, y1) {
    for (let y = Math.max(0, y0); y <= Math.min(this.h - 1, y1); y += 1) for (let x = Math.max(0, x0); x <= Math.min(this.w - 1, x1); x += 1) this.taken[y * this.w + x] = 1;
  }

  // text in whole cells from cell (c, r): over the floor colour under its first cell unless `bg` is given; -> the cells used
  text(c, r, str, color, bg, bold) {
    if (r < 0 || r >= this.rows) return 0;
    const back = bg || this.get(c, r * 2);
    let col = c;
    for (const ch of str) {
      const cw = charWidth(ch);
      if (!cw) continue;
      if (col + cw > this.w) break;
      if (col >= 0) {
        this.txt[r * this.w + col] = { ch, color, bg: back, bold: !!bold };
        if (cw === 2) this.txt[r * this.w + col + 1] = { cont: true };
      }
      col += cw;
    }
    return col - c;
  }

  lines() {
    const out = [];
    for (let r = 0; r < this.rows; r += 1) {
      const line = [];
      const push = (text, color, bg, bold) => {
        const last = line[line.length - 1];
        if (last && last.color === color && last.bg === bg && !!last.bold === !!bold) last.text += text;
        else {
          const span = { text, tone: "plain" };
          if (color) span.color = color;
          if (bg) span.bg = bg;
          if (bold) span.bold = true;
          line.push(span);
        }
      };
      for (let c = 0; c < this.w; c += 1) {
        const t = this.txt[r * this.w + c];
        if (t) {
          if (!t.cont) push(t.ch, hex(t.color), hex(t.bg), t.bold);
          continue;
        }
        const top = this.get(c, r * 2);
        const bottom = this.get(c, r * 2 + 1);
        if (same(top, bottom)) push(" ", undefined, hex(top), false);
        else push("▀", hex(top), hex(bottom), false);
      }
      out.push(line);
    }
    return out;
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Sprites

// One template for everybody: a head with eyes, a body, legs. Body colour by agent; the hub is gold, wears an antenna and
// stands one row taller. Four sizes each: 5 wide, 3 wide, 3 x 3 and a pair of pixels.
const HUB_TIERS = [
  ["..a..", ".hhh.", "hEhEh", ".hhh.", "bbbbb", "hbbbh", ".bbb.", ".l.l.", ".l.l."],
  [".a.", "hhh", "EhE", "bbb", "bbb", "l.l"],
  [".a.", "hhh", "bbb", "l.l"],
  ["a", "b"],
];
const BOT_TIERS = [
  [".hhh.", "hEhEh", ".hhh.", "bbbbb", "hbbbh", ".bbb.", ".l.l.", ".l.l."],
  ["hhh", "EhE", "bbb", "bbb", "l.l"],
  ["hhh", "bbb", "l.l"],
  ["h", "b"],
];
const LEG = [51, 65, 85];
// The idle ones in TEAM, from a 3 x 3 person to a one-pixel stick of head, body and legs, to a pair of pixels
const IDLE_TIERS = [[".h.", "bbb", "l.l"], ["h", "b", "l"], BOT_TIERS[3]];

function palette(hub, body, dim) {
  const k = dim ? (c) => mix(c, BG, 0.5) : (c) => c;
  const skin = dim ? mix(SKIN, BG, 0.3) : SKIN;
  return { a: ANTENNA, h: skin, E: dim ? mix(skin, BG, 0.35) : EYE, b: k(body), l: dim ? mix(LEG, BG, 0.15) : LEG };
}

// The furniture, two sizes a room, the larger first: parts of [sprite rows, palette, dx, dy]. It stands against the back
// wall; the characters stand in front of it.
const WOOD = [150, 104, 64];
const WOOD_DARK = [102, 70, 43];
const METAL = [71, 85, 105];
const FURNITURE = {
  terminal: [
    [[["..mmmmmmmmm..", "..mkkkkkkkm..", "..mkgkkkkkm..", "..mkkgkggkm..", "..mkgkkkkkm..", "..mkkkkkkkm..", "..mmmmmmmmm..", ".....sss.....", "wwwwwwwwwwwww", "vvvvvvvvvvvvv", "v...........v"],
      { m: METAL, k: [5, 18, 12], g: ZONE_RGB.terminal, s: [100, 116, 139], w: WOOD, v: WOOD_DARK }, 0, 0]],
    [[[".mmmmmmm.", ".mgkkkkm.", ".mkgkggm.", ".mmmmmmm.", "....s....", "wwwwwwwww", "vvvvvvvvv"],
      { m: METAL, k: [5, 18, 12], g: ZONE_RGB.terminal, s: [100, 116, 139], w: WOOD, v: WOOD_DARK }, 0, 0]],
    [[[".mmmmm.", ".mgkgm.", "wwwwwww", "v.....v"], { m: METAL, k: [5, 18, 12], g: ZONE_RGB.terminal, w: WOOD, v: WOOD_DARK }, 0, 0]],
  ],
  files: [
    [[["ttttttt", "cyyyyyc", "cyyhyyc", "ccccccc", "cyyyyyc", "cyyhyyc", "ccccccc", "cyyyyyc", "cyyhyyc", "ccccccc"],
      { t: WOOD, c: WOOD_DARK, y: mix(ZONE_RGB.files, BG, 0.4), h: [40, 30, 15] }, 0, 0],
    [["ppppp", "pllpp", "ppppp", "pllpp", "ppppp"], { p: [226, 232, 240], l: [148, 163, 184] }, 9, 5]],
    [[["ttttt", "cyhyc", "ccccc", "cyhyc", "ccccc"], { t: WOOD, c: WOOD_DARK, y: mix(ZONE_RGB.files, BG, 0.4), h: [40, 30, 15] }, 0, 0]],
    [[["ttttt", "cyhyc", "ccccc", "cyhyc"], { t: WOOD, c: WOOD_DARK, y: mix(ZONE_RGB.files, BG, 0.4), h: [40, 30, 15] }, 0, 0]],
  ],
  web: [
    [[["..bbbbb..", ".bbeebbb.", "bbeeebbbb", "bbbeebbeb", "bbbbbbeeb", "bbebbbeed", "bbeebbbdd", ".bbbbbddd", "..bbddd..", "....s....", "..sssss.."],
      { b: ZONE_RGB.web, e: [52, 211, 153], d: mix(ZONE_RGB.web, BG, 0.35), s: [100, 116, 139] }, 0, 0]],
    [[[".bbb.", "bebbb", "bbebd", "bbbdd", ".bdd.", "..s..", ".sss."], { b: ZONE_RGB.web, e: [52, 211, 153], d: mix(ZONE_RGB.web, BG, 0.35), s: [100, 116, 139] }, 0, 0]],
    [[[".bbb.", "bebbb", "bbebd", ".sss."], { b: ZONE_RGB.web, e: [52, 211, 153], d: mix(ZONE_RGB.web, BG, 0.35), s: [100, 116, 139] }, 0, 0]],
  ],
  search: [
    [[[".rrrr...", "rwgwwr..", "rwwwwr..", "rwwwwr..", "rwwwwr..", ".rrrrr..", "....rrr.", ".....rrr", "......rr"], { r: ZONE_RGB.search, w: [30, 22, 45], g: [120, 96, 160] }, 0, 0]],
    [[[".rrr.", "rwwwr", "rwwwr", ".rrrr", "...rr"], { r: ZONE_RGB.search, w: [30, 22, 45] }, 0, 0]],
    [[[".rrr.", "rwwwr", ".rrrr", "...rr"], { r: ZONE_RGB.search, w: [30, 22, 45] }, 0, 0]],
  ],
  thinking: [
    [[["..yyy..", ".yyyyy.", "yyYYyyy", "yyYyyyy", "yyyyyyy", ".yyyyy.", "..ggg..", "..kgk..", "...g..."], { y: [253, 224, 71], Y: [255, 245, 170], g: [100, 116, 139], k: [71, 85, 105] }, 0, 0]],
    [[[".yyy.", "yYyyy", "yyyyy", ".yyy.", "..g..", "..g.."], { y: [253, 224, 71], Y: [255, 245, 170], g: [100, 116, 139] }, 0, 0]],
    [[[".yyy.", "yYyyy", ".yyy.", "..g.."], { y: [253, 224, 71], Y: [255, 245, 170], g: [100, 116, 139] }, 0, 0]],
  ],
  team: [
    [[[".BBBBBBBBBBB.", "ABBBBBBBBBBBA", "ACCCCCCCCCCCA", "ACCCCCCCCCCCA", "AAAAAAAAAAAAA", ".L.........L."], { B: mix(ZONE_RGB.team, BG, 0.5), C: mix(ZONE_RGB.team, BG, 0.62), A: mix(ZONE_RGB.team, BG, 0.4), L: [51, 65, 85] }, 0, 0]],
    [[["BBBBBBBBB", "ACCCCCCCA", "AAAAAAAAA", ".L.....L."], { B: mix(ZONE_RGB.team, BG, 0.5), C: mix(ZONE_RGB.team, BG, 0.62), A: mix(ZONE_RGB.team, BG, 0.4), L: [51, 65, 85] }, 0, 0]],
    [[["BBBBBBB", "AAAAAAA", ".L...L."], { B: mix(ZONE_RGB.team, BG, 0.5), A: mix(ZONE_RGB.team, BG, 0.4), L: [51, 65, 85] }, 0, 0]],
  ],
};
for (const tiers of Object.values(FURNITURE)) {
  tiers.forEach((parts, i) => {
    tiers[i] = { parts, w: Math.max(...parts.map(([rows, , dx]) => dx + rows[0].length)), h: Math.max(...parts.map(([rows, , , dy]) => dy + rows.length)) };
  });
}

// ---------------------------------------------------------------------------------------------------------------
// The picture

// The six rooms' rectangles (pixels, inclusive) for a canvas of `w` cells and `rows` text rows. Neighbours share their wall
// (one pixel thick everywhere); the shared row is an even one, so every title row has the same floor pixel above it.
function geometry(w, rows) {
  const RX = [0, Math.floor(w / 3), Math.floor((2 * w) / 3), w - 1];
  const RY = [0, rows - (rows & 1), rows * 2 - 1];
  const rooms = {};
  for (const [name, cx, cy] of ROOMS) rooms[name] = { name, col: cx, row: cy, x0: RX[cx], x1: RX[cx + 1], y0: RY[cy], y1: RY[cy + 1] };
  return rooms;
}

const centreOf = (r) => [(r.x0 + r.x1) >> 1, (r.y0 + r.y1) >> 1];

// The door path between two rooms: along the row first, then along the column; each step through the door in the wall
// between them and the middle of the room beyond. -> [[x, y], ...] from the middle of `a` to the middle of `b`.
function pathBetween(rooms, a, b) {
  const byCell = new Map(Object.values(rooms).map((r) => [r.col + "," + r.row, r]));
  let cur = rooms[a];
  const goal = rooms[b];
  const pts = [centreOf(cur)];
  const step = (next, door) => {
    pts.push(door, centreOf(next));
    cur = next;
  };
  while (cur.col !== goal.col) {
    const next = byCell.get(cur.col + Math.sign(goal.col - cur.col) + "," + cur.row);
    const left = next.col > cur.col ? cur : next;
    const right = next.col > cur.col ? next : cur;
    step(next, [(left.x1 + right.x0 + 1) >> 1, centreOf(cur)[1]]);
  }
  if (cur.row !== goal.row) {
    const next = byCell.get(cur.col + "," + goal.row);
    const top = next.row > cur.row ? cur : next;
    const bottom = next.row > cur.row ? next : cur;
    step(next, [centreOf(cur)[0], (top.y1 + bottom.y0 + 1) >> 1]);
  }
  return pts;
}

function lengthsOf(pts) {
  const out = [0];
  for (let i = 1; i < pts.length; i += 1) out.push(out[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  return out;
}

function pointAt(pts, lens, d) {
  const total = lens[lens.length - 1];
  if (!(total > 0)) return pts[pts.length - 1];
  const at = Math.max(0, Math.min(total, d));
  let i = 1;
  while (i < lens.length - 1 && lens[i] < at) i += 1;
  const f = lens[i] === lens[i - 1] ? 1 : (at - lens[i - 1]) / (lens[i] - lens[i - 1]);
  return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * f, pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * f];
}

// Where everything stands, from the document: { hub, items, lit, act, start }.
function modelOf(doc, options) {
  const agents = validAgents(doc);
  const hub = findHub(doc, agents);
  const root = isObject(doc.root) ? doc.root : null;
  const hubName = clean(hub ? hub.name : root && root.agent);
  const working = hubWorking(doc, hub);
  const act = hub && isObject(hub.activity) ? hub.activity : null;
  const listed = new Set(agents.map((a) => a.name));
  const needs = new Map();
  for (const e of needsEntries(doc, listed)) if ((e.kind === "stuck" || e.kind === "sent_back") && !needs.has(e.agent)) needs.set(e.agent, e.kind);
  const keys = [];
  for (const a of agents) if (a !== hub && !keys.includes(a.group || "build")) keys.push(a.group || "build");
  const pal = makePalette(keys, options.colors);
  const items = [];
  let hubItem = null;
  if (hubName) {
    const room = working ? roomOfActivity(act) : "team";
    hubItem = { hub: true, name: truncate(hubName, NAME_MAX), room, color: HUB_GOLD, working, dim: false, kind: "hub", cost: Infinity };
    items.push(hubItem);
  }
  let n = 0;
  for (const a of agents) {
    if (a === hub) continue;
    const own = rgbOf(pal.full(a.group || "build")) || FALLBACK_COLORS[n % FALLBACK_COLORS.length];
    n += 1;
    const busy = busyCount(a) > 0;
    const need = needs.get(a.name);
    const base = { hub: false, color: own, cost: agentCost(a), agent: a };
    if (busy) items.push({ ...base, room: roomOfActivity(isObject(a.activity) ? a.activity : null, "thinking"), name: truncate(clean(a.name), NAME_MAX), working: true, dim: false, kind: "working" });
    else if (need) items.push({ ...base, room: "team", name: truncate(clean(a.name), NAME_MAX - 2), mark: need === "stuck" ? ATTENTION : SENT_BACK, working: false, dim: false, kind: "needs" });
    else items.push({ ...base, room: "team", name: null, working: false, dim: true, kind: "idle" });
  }
  const rank = { hub: 0, needs: 1, working: 2, idle: 3 };
  items.forEach((it, i) => (it.order = i));
  items.sort((x, y) => rank[x.kind] - rank[y.kind] || (x.kind === "working" ? y.cost - x.cost : 0) || x.order - y.order);
  const start = act && finiteNumber(act.time) !== null ? act.time * 1000 : null;
  return { hub: hubItem, items, lit: hubItem && working ? hubItem.room : null, act, start, agents };
}

// One room's characters. The hub comes first and keeps the biggest sprite its stage holds (5x9, 3x5, 3x3, a pixel pair);
// then the agents that need the user, then the working ones (in `lead`, a tier of their own that shrinks first); the idle
// ones are a neat grid above them at the smallest sprites, and go (as a `+N` in the title row) before anything else does.
// A bubble row is kept above the hub's line when `want.bubble`, a pixel row off the hub when it can be. Every candidate setup is tried and the best one kept:
// fewest working agents hidden, then the bubble, big sprites, the hub's name, the fewest idle ones hidden, then names.
// -> { placed, hidden, bubbleRow } with `placed` as slots.
function fitRoom(items, stage, want = {}) {
  const W = stage.x1 - stage.x0 + 1;
  const H = stage.y1 - stage.y0 + 1;
  const total = items.length;
  if (W < 1 || H < 2) return { placed: [], hidden: total, bubbleRow: -1, idleCount: null, idleLost: 0 };
  const cap = Math.max(1, ((W + 1) >> 1) * Math.floor((H + 1) / 3)); // no more than the smallest pairs could ever hold
  const hubItem = items.find((i) => i.hub) || null;
  const lead = items.filter((i) => !i.hub && i.kind !== "idle");
  const idle = items.filter((i) => !i.hub && i.kind === "idle");
  const leadAt = lead.slice(0, cap);
  const idleAt = idle.slice(0, cap);
  const ht = hubItem ? HUB_TIERS.findIndex((r) => r[0].length <= W && r.length <= H) : -1;
  const hub = ht >= 0 ? hubItem : null;
  const labels = new Map();
  for (const it of items) if (it.name) labels.set(it, { text: (it.mark ? it.mark + " " : "") + it.name, w: displayWidth((it.mark ? it.mark + " " : "") + it.name) });
  // `named` is true to name it; for the hub 2 names it in full (cut to the stage only when nothing else would fit), 1 cut
  // to its sprite's width, 0 not at all
  const cells = new Map();
  const cell = (item, rows, named) => {
    const key = rows === undefined ? "" : rows.length + ":" + named;
    let mine = cells.get(item);
    if (!mine) cells.set(item, (mine = new Map()));
    let made = mine.get(key);
    if (!made) mine.set(key, (made = cellOf(item, rows, named)));
    return made;
  };
  const cellOf = (item, rows, named) => {
    const l = named ? labels.get(item) : null;
    let text = l ? l.text : null;
    if (text && named === 1) text = truncate(text, rows[0].length);
    else if (text && l.w > W) text = truncate(text, W);
    return { item, rows, sw: rows[0].length, sh: rows.length, text, tw: text ? (text === (l && l.text) ? l.w : displayWidth(text)) : 0 };
  };
  // One line of cells: sprites a pixel apart, each name centred under its sprite. A name that would touch the one before it
  // is cut down to what is left (the hub comes first, so it keeps its own), or left out under three cells.
  // -> { pos: [{ c, sx, lx, text, nw }], min, max } in cells relative to the first sprite.
  const flow = (cells, gap = 1) => {
    const pos = [];
    let le = -Infinity;
    let min = 0;
    let max = 0;
    cells.forEach((c, i) => {
      const sx = i ? pos[i - 1].sx + pos[i - 1].c.sw + gap : 0;
      let text = c.text;
      let nw = c.tw;
      let lx = sx + ((c.sw - nw) >> 1);
      if (text && lx < le + 2) {
        let a = nw - 1;
        for (; a >= 3; a -= 1) if (sx + ((c.sw - a) >> 1) >= le + 2) break;
        if (a >= 3) {
          text = truncate(text, a);
          nw = displayWidth(text);
          lx = sx + ((c.sw - nw) >> 1);
        } else {
          text = null;
          nw = 0;
        }
      }
      if (text) le = lx + nw - 1;
      pos.push({ c, sx, lx, text, nw });
      min = Math.min(min, sx, text ? lx : sx);
      max = Math.max(max, sx + c.sw - 1, text ? lx + nw - 1 : sx);
    });
    return { pos, min, max };
  };
  const wrap = (cells) => {
    const lines = [];
    let cur = [];
    for (const c of cells) {
      if (cur.length && (() => { const f = flow([...cur, c]); return f.max - f.min + 1 > W; })()) {
        lines.push(cur);
        cur = [];
      }
      cur.push(c);
    }
    if (cur.length) lines.push(cur);
    return lines;
  };
  // one line of cells with its bottom pixel at B: -> its top pixel, or null when it leaves the stage
  const lay = (cells, B, out) => {
    let f = flow(cells);
    // names that were cut or left out: stand the people further apart while the stage holds them
    if (cells.length > 1 && f.pos.some((p) => p.c.text !== p.text)) {
      const kept = (x) => x.pos.reduce((t, p) => t + (p.text ? displayWidth(p.text) : 0), 0);
      for (let g = 2; g <= 14; g += 1) {
        const t = flow(cells, g);
        if (t.max - t.min + 1 > W) break;
        if (kept(t) > kept(f)) f = t;
        if (!f.pos.some((p) => p.c.text !== p.text)) break;
      }
    }
    const has = f.pos.some((p) => p.text);
    if (has && B % 2 === 0) B -= 1;
    const band = has ? 3 : 0;
    const top = B - band - Math.max(...cells.map((c) => c.sh)) + 1;
    if (top < stage.y0) return null;
    const ox = stage.x0 + ((W - (f.max - f.min + 1)) >> 1) - f.min;
    for (const p of f.pos) {
      const c = p.c;
      const sy = B - band - c.sh + 1;
      const x = ox + p.sx;
      const nx = ox + p.lx;
      const ix = p.text ? Math.min(x, nx) : x;
      const iw = (p.text ? Math.max(x + c.sw, nx + p.nw) : x + c.sw) - ix;
      out.push({ item: c.item, rows: c.rows, x, y: sy, sw: c.sw, sh: c.sh, label: p.text, nx, nr: p.text ? (B - 1) >> 1 : -1, ix, iw, top: sy, bottom: p.text ? B : sy + c.sh - 1 });
    }
    return top;
  };
  // the hub and the first `kn` of the others, `n` of them named
  const lower = (c, kn, n) => {
    const cells = hub ? [cell(hub, HUB_TIERS[ht], c.hubName)] : [];
    for (let i = 0; i < kn; i += 1) cells.push(cell(leadAt[i], BOT_TIERS[c.T], i < n));
    if (cells.some((x) => x.sw > W)) return null;
    const out = [];
    const lines = wrap(cells);
    let B = stage.y1;
    let bubbleRow = -1;
    for (let i = 0; i < lines.length; i += 1) {
      const top = lay(lines[i], B, out);
      if (top === null) return null;
      B = top - 2;
      if (i === 0 && hub && c.gap >= 0) {
        const br = (top - 2 - c.gap) >> 1;
        if (br * 2 < stage.y0) return null;
        bubbleRow = br;
        B = br * 2 - 2;
      }
    }
    return { out, B, bubbleRow };
  };
  const run = (c, n) => {
    let best = lower(c, 0, n);
    if (!best) return null;
    let lo = 0;
    let hi = leadAt.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      const r = lower(c, mid, n);
      if (r) {
        lo = mid;
        best = r;
      } else hi = mid - 1;
    }
    const rows = IDLE_TIERS[c.IT];
    const sw = rows[0].length;
    const sh = rows.length;
    const gap = sw === 1 ? 2 : 1; // the one-pixel people stand two apart
    // when not all of them fit, a `+N` closes the last row; it takes the cells of the last sprites of that row
    let cols = Math.floor((W + gap) / (sw + gap));
    let reserve = 0;
    if (cols > 0 && idleAt.length > cols * (best.B - sh + 1 >= stage.y0 ? Math.floor((best.B - sh + 1 - stage.y0) / (sh + 1)) + 1 : 0)) {
      reserve = Math.ceil((("+" + idle.length).length + 1) / (sw + gap));
      if (cols - reserve < 1) reserve = 0;
      cols -= reserve;
    }
    const gx = stage.x0 + ((W - (cols * (sw + gap) - gap + (reserve ? reserve * (sw + gap) : 0))) >> 1);
    let shown = 0;
    let count = null;
    for (let b = best.B; cols > 0 && b - sh + 1 >= stage.y0 && shown < idleAt.length; b -= sh + 1) {
      for (let k = 0; k < cols && shown < idleAt.length; k += 1) {
        const x = gx + k * (sw + gap);
        best.out.push({ item: idleAt[shown], rows, x, y: b - sh + 1, sw, sh, label: null, nx: 0, nr: -1, ix: x, iw: sw, top: b - sh + 1, bottom: b });
        shown += 1;
      }
      if (!count && reserve) count = { c0: gx + cols * (sw + gap), r: (b - 1) >> 1, bottom: b };
    }
    const idleLost = idle.length - shown;
    if (count && idleLost > 0) count.text = "+" + idleLost;
    else count = null;
    return { out: best.out, bubbleRow: best.bubbleRow, lost: lead.length - lo, idleLost, count };
  };
  let found = null;
  let foundKey = null;
  for (const gap of want.bubble && hub ? [1, 0, -1] : [-1]) { // the bubble's gap to the hub; -1: no bubble
    for (const hubName of hub && labels.has(hub) ? [2, 1, 0] : [0]) {
      for (const T of leadAt.length ? [0, 1, 2, 3] : [0]) {
        let tLost = Infinity; // a bigger T only matters while working agents are still lost
        for (const IT of idleAt.length ? (T < 3 ? [0, 1, 2] : [1, 2]) : [2]) {
          const c = { gap, hubName, T, IT };
          const base = run(c, 0);
          if (!base) continue;
          const score = (r) => r.lost * 1e6 + r.idleLost;
          let r = base;
          let lo = 0;
          let hi = leadAt.length;
          while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            const t = run(c, mid);
            if (t && score(t) <= score(base)) {
              lo = mid;
              r = t;
            } else hi = mid - 1;
          }
          const named = r.out.filter((p) => p.label && !p.item.hub).length;
          const hubLabel = r.out.find((p) => p.label && p.item.hub);
          const key = [r.lost, gap < 0 ? 2 : 1 - gap, hubLabel ? (hubLabel.label === labels.get(hub).text ? 0 : 1) : 2, T, r.idleLost, IT, -named];
          let better = !foundKey;
          for (let i = 0; !better && i < key.length && key[i] <= foundKey[i]; i += 1) if (key[i] < foundKey[i]) better = true;
          if (better) {
            found = r;
            foundKey = key;
          }
          tLost = Math.min(tLost, r.lost);
          if (r.idleLost === 0) break; // a smaller idle sprite can only be worse
        }
        if (tLost === 0) break;
      }
      // nothing later can beat: no one lost, the bubble as close as asked, the hub named in full
      if (foundKey && foundKey[0] === 0 && foundKey[1] === (gap < 0 ? 2 : 1 - gap) && foundKey[2] === 0) break;
    }
    if (foundKey && foundKey[0] === 0 && foundKey[1] === 0) break;
  }
  return { placed: found ? found.out : [], hidden: total - (found ? found.out.length : 0), bubbleRow: found ? found.bubbleRow : -1, idleCount: found ? found.count : null, idleLost: found ? found.idleLost : 0 };
}

const pick = (o, k) => (typeof k === "string" && Object.hasOwn(o, k) ? o[k] : undefined); // never a name from the prototype
const colorFor = (c, dark) => (dark ? c : undefined);

// The fit of one room, with its furniture: the furniture stands against the back wall (the top of the stage) and the
// characters in front of it. The larger piece is used when everything that matters still fits beside it (every working
// agent, the hub's size and name, its bubble, a few idle ones); else the smaller; else none (it is drawn later in whatever
// free space is left). -> fitRoom's result and `furniture`: { tier, x, y } or null.
function planRoom(items, stage, want, name) {
  const base = fitRoom(items, stage, want);
  base.furniture = null;
  base.bubbleSpot = null;
  if (!name) return base;
  const W = stage.x1 - stage.x0 + 1;
  const H = stage.y1 - stage.y0 + 1;
  const tiers = FURNITURE[name];
  const lead = (fit) => fit.placed.filter((p) => !p.item.hub && p.item.kind !== "idle");
  const sum = (ps) => ps.reduce((t, p) => t + p.sh, 0);
  const idleOf = (fit) => fit.placed.filter((p) => p.item.kind === "idle").length;
  const hubOf = (fit) => fit.placed.find((p) => p.item.hub) || null;
  const baseHub = hubOf(base);
  const baseLead = lead(base);
  // first looking for a piece that leaves the idle ones as people (three pixels tall); when none does, the idle give way
  const idleH = (fit) => fit.placed.reduce((m, p) => (p.item.kind === "idle" ? Math.max(m, p.sh) : m), 0);
  const wantIdle = Math.min(idleOf(base), 3);
  for (let pass = 0; pass < 2; pass += 1) for (let t = 0; t < tiers.length; t += 1) {
    const tier = tiers[t];
    if (tier.w > W || tier.h + 2 > H) continue;
    const middle = { tier, x: stage.x0 + ((W - tier.w) >> 1), y: stage.y0 };
    if (!items.length) return { ...base, furniture: middle };
    // the furniture in the middle with the bubble under it; else in a corner with the bubble beside it, in the same rows
    const options = [{ furniture: middle, spot: null, want }];
    if (want.bubble && baseHub) {
      for (const left of [true, false]) {
        const x = left ? stage.x0 : stage.x1 - tier.w + 1;
        const x0 = left ? x + tier.w + 1 : stage.x0 - 1;
        const x1 = left ? stage.x1 + 1 : x - 2;
        if (x1 - x0 + 1 >= 8) options.push({ furniture: { tier, x, y: stage.y0 }, spot: { x0, x1, top: stage.y0 + 1 }, want: { ...want, bubble: false } });
      }
    }
    for (const o of options) {
      const fit = fitRoom(items, { ...stage, y0: stage.y0 + tier.h + 1 }, o.want);
      const hub = hubOf(fit);
      const ok = lead(fit).length === baseLead.length && sum(lead(fit)) >= sum(baseLead) && (!baseHub || (hub && hub.sh === baseHub.sh && !!hub.label === !!baseHub.label)) &&
        (base.bubbleRow < 0 || o.spot || fit.bubbleRow >= 0) &&
        (pass === 1 ? t < tiers.length - 1 || idleOf(fit) >= wantIdle : idleOf(fit) >= wantIdle && (!wantIdle || idleH(fit) >= Math.min(idleH(base), 3)));
      if (ok) return { ...fit, furniture: o.furniture, bubbleSpot: o.spot };
    }
  }
  return base;
}

// Plans are kept per document (a new snapshot is a new object), size and room, so a tick that moves nothing but a bob does
// not fit the rooms again.
const planCache = new WeakMap();
function cachedPlan(doc, key, make) {
  if (!isObject(doc)) return make();
  let kept = planCache.get(doc);
  if (!kept) {
    kept = new Map();
    planCache.set(doc, kept);
  }
  let plan = kept.get(key);
  if (!plan) {
    if (kept.size > 96) kept.clear();
    plan = make();
    kept.set(key, plan);
  }
  return plan;
}

// The way for the footprints: over free floor and through the doors only, from the free spot nearest the middle of the room
// the hub came from to the free cell nearest its feet, drawn straight wherever the way is clear. -> points, or null.
function routeAround(pix, pass, room, feet) {
  const { w, h } = pix;
  const open = new Uint8Array(w * h); // 1: free floor or door
  for (let i = 0; i < open.length; i += 1) open[i] = pass[i] === 1 && !pix.taken[i] ? 1 : 0;
  const ok = (x, y) => x >= 0 && y >= 0 && x < w && y < h && open[y * w + x] === 1;
  const [cx, cy] = centreOf(room);
  let start = null;
  for (let d = 0; d <= 14 && !start; d += 1) {
    let bestD = Infinity;
    for (let y = Math.max(room.y0 + 1, cy - d); y <= Math.min(room.y1 - 1, cy + d); y += 1) for (let x = Math.max(room.x0 + 1, cx - d); x <= Math.min(room.x1 - 1, cx + d); x += 1) {
      const dd = Math.hypot(x - cx, y - cy);
      if (open[y * w + x] === 1 && dd < bestD) {
        bestD = dd;
        start = [x, y];
      }
    }
  }
  if (!start) return null;
  const prev = new Int32Array(w * h).fill(-1);
  const queue = new Int32Array(w * h);
  let head = 0;
  let tail = 0;
  queue[tail++] = start[1] * w + start[0];
  prev[queue[0]] = queue[0];
  let goal = -1;
  const DX = [1, -1, 0, 0, 1, 1, -1, -1];
  const DY = [0, 0, 1, -1, 1, -1, 1, -1];
  while (head < tail) {
    const cur = queue[head++];
    const x = cur % w;
    const y = (cur - x) / w;
    if (Math.abs(x - feet[0]) <= 2 && Math.abs(y - feet[1]) <= 2) {
      goal = cur;
      break;
    }
    for (let k = 0; k < 8; k += 1) {
      const nx = x + DX[k];
      const ny = y + DY[k];
      if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
      const at = ny * w + nx;
      if (open[at] !== 1 || prev[at] >= 0) continue;
      if (k >= 4 && (open[y * w + nx] !== 1 || open[ny * w + x] !== 1)) continue;
      prev[at] = cur;
      queue[tail++] = at;
    }
  }
  if (goal < 0) return null;
  const cells = [];
  for (let at = goal; ; at = prev[at]) {
    cells.push([at % w, Math.floor(at / w)]);
    if (prev[at] === at) break;
  }
  cells.reverse();
  const clear = (a, b) => {
    const n = Math.max(Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
    for (let i = 1; i < n; i += 1) if (!ok(Math.round(a[0] + ((b[0] - a[0]) * i) / n), Math.round(a[1] + ((b[1] - a[1]) * i) / n))) return false;
    return true;
  };
  const pts = [cells[0]];
  let at = 0;
  while (at < cells.length - 1) {
    let next = at + 1;
    for (let k = Math.min(cells.length - 1, at + 40); k > at + 1; k -= 1) {
      if (clear(cells[at], cells[k])) {
        next = k;
        break;
      }
    }
    pts.push(cells[next]);
    at = next;
  }
  pts.push(feet);
  return pts;
}

const BUBBLE = [226, 232, 240];
const BUBBLE_TEXT = [15, 23, 42];
const PLAQUE_DARK = [8, 12, 18];

// The picture alone: { lines, info }. `rows` text rows of `w` cells.
function buildRooms(doc, w, rows, now, options) {
  const pix = new Pix(w, rows);
  const rooms = geometry(w, rows);
  const info = { rooms, titles: [], plaques: [], furniture: [], chars: [], bubble: null, more: [], walking: false, lit: null, compact: false };
  const reduced = options.motion === "reduced";
  const model = modelOf(isObject(doc) ? doc : {}, options);
  const lit = model.lit;
  info.lit = lit;
  const compact = Math.min(...ROOMS.map(([n]) => rooms[n].x1 - rooms[n].x0 - 1)) < 15 || Math.min(...ROOMS.map(([n]) => rooms[n].y1 - rooms[n].y0 + 1)) < 14;
  info.compact = compact;

  // floors (flat), walls (one pixel; the lit room's wins where it is shared), doors
  for (const [name] of ROOMS) {
    const r = rooms[name];
    pix.rect(r.x0, r.y0, r.x1, r.y1, mix(BG, ZONE_RGB[name], name === lit ? 0.17 : 0.075));
  }
  for (const [name] of [...ROOMS.filter(([n]) => n !== lit), ...ROOMS.filter(([n]) => n === lit)]) {
    const r = rooms[name];
    const wall = name === lit ? ZONE_RGB[name] : mix(BG, ZONE_RGB[name], 0.4);
    pix.rect(r.x0, r.y0, r.x1, r.y0, wall);
    pix.rect(r.x0, r.y1, r.x1, r.y1, wall);
    pix.rect(r.x0, r.y0, r.x0, r.y1, wall);
    pix.rect(r.x1, r.y0, r.x1, r.y1, wall);
  }
  const door = mix(BG, ZONE_RGB.team, 0.2);
  const pass = new Uint8Array(pix.w * pix.h); // where feet may go: the floors, and the doors in the walls
  for (const [name] of ROOMS) {
    const r = rooms[name];
    for (let y = r.y0 + 1; y < r.y1; y += 1) for (let x = r.x0 + 1; x < r.x1; x += 1) pass[y * pix.w + x] = 1;
  }
  for (const [a, b] of [["terminal", "files"], ["files", "web"], ["search", "thinking"], ["thinking", "team"]]) {
    const l = rooms[a];
    const ym = centreOf(l)[1];
    const half = Math.min(1, (l.y1 - l.y0 - 3) >> 1);
    if (half >= 0) {
      pix.rect(l.x1, ym - half, rooms[b].x0, ym + half, door);
      for (let y = ym - half; y <= ym + half; y += 1) for (let x = l.x1; x <= rooms[b].x0; x += 1) pass[y * pix.w + x] = 1;
    }
  }
  for (const [a, b] of [["terminal", "search"], ["files", "thinking"], ["web", "team"]]) {
    const t = rooms[a];
    const xm = centreOf(t)[0];
    const half = Math.min(1, (t.x1 - t.x0 - 3) >> 1);
    if (half >= 0) {
      pix.rect(xm - half, t.y1, xm + half, rooms[b].y0, door);
      for (let y = t.y1; y <= rooms[b].y0; y += 1) for (let x = xm - half; x <= xm + half; x += 1) pass[y * pix.w + x] = 1;
    }
  }

  // plaques, and each room's stage under them
  const stages = {};
  const plaqueDraws = [];
  for (const [name] of ROOMS) {
    const r = rooms[name];
    const [glyph, word, short] = TITLES[name];
    const tr = Math.ceil((r.y0 + 1) / 2);
    const roomW = r.x1 - r.x0 + 1;
    const variants = compact ? [short, glyph] : [glyph + " " + word, word, short, glyph];
    const title = variants.find((v) => displayWidth(v) + 2 <= roomW - 4) || "";
    let top = r.y0 + 1;
    if (title && tr * 2 + 1 <= r.y1 - 1) {
      const pw = displayWidth(title) + 2;
      const c0 = r.x0 + ((roomW - pw) >> 1);
      pix.take(c0, tr * 2, c0 + pw - 1, tr * 2 + 1);
      plaqueDraws.push({ name, tr, text: " " + title + " ", c0 });
      info.titles.push({ room: name, text: title, c0, c1: c0 + pw - 1, r: tr });
      info.plaques.push({ room: name, c0, c1: c0 + pw - 1, r: tr });
      top = tr * 2 + 3;
    }
    stages[name] = { x0: r.x0 + 2, x1: r.x1 - 2, y0: top, y1: r.y1 - 2, bob: top - 1 };
  }

  // the characters in their rooms
  const placed = [];
  const bubbleRows = {};
  const bubbleSpots = {};
  const moreDraws = [];
  const countDraws = [];
  const furnitureAt = [];
  const target = model.hub && model.hub.working && model.act ? clean(model.act.target) : "";
  let bubbleText = "";
  if (model.hub && target) {
    const r = rooms[model.hub.room];
    const limit = r.x1 - r.x0 - 7; // two floor pixels left and right of the bubble
    const text = truncate(target, Math.min(limit, 24));
    if (limit >= 3 && displayWidth(text) >= 3) bubbleText = text;
  }
  for (const [name] of ROOMS) {
    const mine = model.items.filter((i) => i.room === name);
    const st = stages[name];
    const bubble = !!bubbleText && model.hub.room === name;
    const bw0 = bubble ? Math.min(displayWidth(bubbleText) + 4, 11) : 0;
    const key = [name, st.x0, st.x1, st.y0, st.y1, bw0, compact ? 1 : 0, ...mine.map((i) => [i.kind, i.name, i.mark, i.hub ? 1 : 0, i.working ? 1 : 0, i.dim ? 1 : 0, i.color.join(",")].join(":"))].join("|");
    const fit = cachedPlan(doc, key, () => planRoom(mine, st, { bubble, bubbleW: bw0 }, compact ? null : name));
    bubbleRows[name] = fit.bubbleRow;
    bubbleSpots[name] = fit.bubbleSpot || null;
    if (fit.furniture) {
      const f = fit.furniture;
      pix.take(f.x, f.y, f.x + f.tier.w - 1, f.y + f.tier.h - 1);
      furnitureAt.push({ room: name, tier: f.tier, x: f.x, y: f.y });
    }
    for (const p of fit.placed) {
      placed.push({ ...p, room: name });
      pix.take(p.ix, p.top, p.ix + p.iw - 1, p.bottom);
    }
    // the ones that did not fit: `+N` at the right end of the plaque row, when the plaque leaves room
    const t = plaqueDraws.find((d) => d.name === name);
    if (fit.idleCount) {
      const ic = fit.idleCount;
      pix.take(ic.c0, ic.r * 2, ic.c0 + ic.text.length - 1, ic.r * 2 + 1);
      countDraws.push({ name, text: ic.text, c0: ic.c0, r: ic.r });
      info.more.push({ room: name, text: ic.text, count: fit.idleLost, c0: ic.c0, c1: ic.c0 + ic.text.length - 1, r: ic.r });
    }
    const lostHere = fit.hidden - (fit.idleCount ? fit.idleLost : 0);
    const text = "+" + lostHere;
    const c1 = st.x1;
    if (lostHere > 0 && t && c1 - text.length + 1 > t.c0 + displayWidth(t.text) + 1) {
      const c0 = c1 - text.length + 1;
      pix.take(c0, t.tr * 2, c1, t.tr * 2 + 1);
      moreDraws.push({ name, text, c0, r: t.tr });
      info.more.push({ room: name, text, count: lostHere, c0, c1, r: t.tr });
    }
  }
  const hubAt = placed.find((p) => p.item.hub) || null;

  // the bubble over the hub: what it does, cut to the room, with a tail of one pixel pointing at the hub
  let bubble = null;
  let tail = null;
  if (hubAt && bubbleText) {
    const r = rooms[hubAt.room];
    const br = bubbleRows[hubAt.room];
    const spot = bubbleSpots[hubAt.room];
    const hubMid = hubAt.x + (hubAt.sw >> 1);
    if (spot) {
      // beside the furniture: as wide as the gap allows, the text cut to fit
      const bw = Math.min(displayWidth(bubbleText) + 4, spot.x1 - spot.x0 + 1);
      const text = truncate(bubbleText, bw - 4);
      const x0 = spot.x0 + ((spot.x1 - spot.x0 + 1 - bw) >> 1);
      if (displayWidth(text) >= 3 && pix.free(x0, spot.top, x0 + bw - 1, spot.top + 1)) {
        bubble = { text, c0: x0, r: spot.top >> 1, w: bw };
        pix.take(x0, spot.top, x0 + bw - 1, spot.top + 1);
      }
    } else if (br >= 1) {
      const bw = displayWidth(bubbleText) + 4;
      for (const bx of [hubMid - (bw >> 1), r.x1 - 2 - bw + 1, r.x0 + 2]) {
        const x0 = Math.max(r.x0 + 2, Math.min(bx, r.x1 - 2 - bw + 1));
        if (pix.free(x0, br * 2, x0 + bw - 1, br * 2 + 1)) {
          bubble = { text: bubbleText, c0: x0, r: br, w: bw };
          pix.take(x0, br * 2, x0 + bw - 1, br * 2 + 1);
          const tx = Math.max(x0 + 2, Math.min(hubMid, x0 + bw - 3));
          if (br * 2 + 2 < hubAt.y && pix.free(tx, br * 2 + 2, tx, br * 2 + 2)) tail = [tx, br * 2 + 2];
          break;
        }
      }
    }
  }

  // furniture that did not fit beside the characters: where it fits in the free space of the stage
  if (!compact) {
    for (const [name] of ROOMS) {
      if (furnitureAt.some((f) => f.room === name)) continue;
      const st = stages[name];
      const W = st.x1 - st.x0 + 1;
      for (const tier of FURNITURE[name]) {
        if (tier.w > W) continue;
        let done = false;
        for (let y = st.y0; y + tier.h - 1 <= st.y1 && !done; y += 1) {
          const x = st.x0 + ((W - tier.w) >> 1);
          if (pix.free(x, y, x + tier.w - 1, y + tier.h - 1)) {
            pix.take(x, y, x + tier.w - 1, y + tier.h - 1);
            furnitureAt.push({ room: name, tier, x, y });
            done = true;
          }
        }
        if (done) break;
      }
    }
  }
  for (const f of furnitureAt) info.furniture.push({ room: f.room, x0: f.x, y0: f.y, x1: f.x + f.tier.w - 1, y1: f.y + f.tier.h - 1 });

  // the walk: from the middle of the room of the last action round whatever stands in the way to the hub's place, during
  // the first WALK_MS of its action; the footprints are laid along it on bare floor only
  let walk = null;
  const before = model.act && Array.isArray(model.act.recent) ? model.act.recent.find(isObject) : null;
  const fromRoom = before ? roomOfActivity(before, null) : null;
  if (hubAt && model.hub.working && fromRoom && fromRoom !== hubAt.room) {
    const feet = [hubAt.x + (hubAt.sw >> 1), hubAt.y + hubAt.sh - 1];
    const routed = cachedPlan(doc, ["route", w, rows, fromRoom, feet.join(","), compact ? 1 : 0, placed.length, furnitureAt.length].join("|"), () => routeAround(pix, pass, rooms[fromRoom], feet) || pathBetween(rooms, fromRoom, hubAt.room));
    const pts = routed.map((q) => q.slice());
    pts[pts.length - 1] = feet;
    walk = { pts, lens: lengthsOf(pts) };
  }
  let walkAt = null;
  if (walk && !reduced && model.start !== null) {
    const t = (now - model.start) / WALK_MS;
    if (t >= 0 && t < 1) walkAt = pointAt(walk.pts, walk.lens, walk.lens[walk.lens.length - 1] * (t * t * (3 - 2 * t)));
  }
  info.walking = !!walkAt;
  info.bubble = bubble && !walkAt ? { text: bubble.text, c0: bubble.c0, c1: bubble.c0 + bubble.w - 1, r: bubble.r } : null;

  // drawing: the trail, the furniture, the bubble's edge, the characters, then the text over them
  if (walk && hubAt) {
    const total = walk.lens[walk.lens.length - 1];
    const marks = Math.floor(total / 3);
    for (let i = 0; i < marks; i += 1) {
      const [x, y] = pointAt(walk.pts, walk.lens, i * 3 + 2);
      const tx = Math.round(x);
      const ty = Math.round(y) + (i % 2 ? 1 : 0);
      if (pix.free(tx, ty, tx, ty) && pass[ty * pix.w + tx]) pix.set(tx, ty, mix(pix.get(tx, ty), ANTENNA, Math.min(0.4, 0.1 + 0.025 * i)));
    }
  }
  for (const f of furnitureAt) for (const [rowsOf, pal, dx, dy] of f.tier.parts) pix.sprite(f.x + dx, f.y + dy, rowsOf, pal);
  if (bubble && !walkAt) {
    pix.set(bubble.c0, bubble.r * 2 + 1, BUBBLE);
    pix.set(bubble.c0 + bubble.w - 1, bubble.r * 2 + 1, BUBBLE);
    if (tail) pix.set(tail[0], tail[1], BUBBLE);
  }
  const bobs = !reduced && Math.floor(now / BOB_MS) % 2 === 1;
  for (const p of placed) {
    const it = p.item;
    const pal = palette(it.hub, it.color, it.dim);
    if (it.hub && walkAt) {
      pix.sprite(Math.round(walkAt[0]) - (p.sw >> 1), Math.round(walkAt[1]) - p.sh + 1, p.rows, pal);
      info.chars.push({ name: it.name, hub: true, kind: it.kind, room: p.room, x0: Math.round(walkAt[0]) - (p.sw >> 1), x1: Math.round(walkAt[0]) - (p.sw >> 1) + p.sw - 1, y0: Math.round(walkAt[1]) - p.sh + 1, y1: Math.round(walkAt[1]), name_cells: null });
      continue;
    }
    const dy = it.working && bobs && p.y - 1 >= stages[p.room].bob && pix.free(p.x, p.y - 1, p.x + p.sw - 1, p.y - 1) ? -1 : 0;
    pix.sprite(p.x, p.y + dy, p.rows, pal);
    info.chars.push({ name: it.name, hub: it.hub, kind: it.kind, room: p.room, x0: p.x, x1: p.x + p.sw - 1, y0: p.y + dy, y1: p.y + dy + p.sh - 1, name_cells: p.label ? { c0: p.nx, c1: p.nx + displayWidth(p.label) - 1, r: p.nr } : null });
  }
  for (const t of plaqueDraws) {
    const on = t.name === lit;
    const z = ZONE_RGB[t.name];
    pix.text(t.c0, t.tr, t.text, on ? PLAQUE_DARK : mix(z, WHITE, 0.2), on ? z : mix(BG, z, 0.24), on);
  }
  for (const m of moreDraws) pix.text(m.c0, m.r, m.text, mix(BG, ZONE_RGB[m.name], 0.75));
  for (const m of countDraws) pix.text(m.c0, m.r, m.text, mix(BG, WHITE, 0.4));
  for (const p of placed) {
    if (!p.label || (p.item.hub && walkAt)) continue;
    const it = p.item;
    const color = it.hub ? HUB_GOLD : it.mark ? (it.mark === ATTENTION ? [248, 113, 113] : [253, 186, 116]) : mix(it.color, WHITE, 0.45);
    pix.text(p.nx, p.nr, p.label, color, undefined, it.hub);
  }
  if (bubble && !walkAt) pix.text(bubble.c0 + 1, bubble.r, " " + bubble.text + " ", BUBBLE_TEXT, BUBBLE, true);
  return { lines: pix.lines(), info };
}

function blankLines(w, h) {
  const row = w > 0 ? [{ text: " ".repeat(w), tone: "plain", bg: hex(BG) }] : [];
  return Array.from({ length: h }, () => row.map((s) => ({ ...s })));
}

// The picture alone, `w` x `h` cells (for the tests): { lines } of exactly `h` rows. Never throws.
export function roomsLines(doc, w, h, now = 0, options = {}) {
  w = Math.max(0, Math.floor(Number(w) || 0));
  h = Math.max(0, Math.floor(Number(h) || 0));
  try {
    if (w < 6 || h < 4) return blankLines(w, h);
    return buildRooms(doc, w, h, Number.isFinite(Number(now)) ? Number(now) : 0, isObject(options) ? options : {}).lines;
  } catch (_) {
    return blankLines(w, h);
  }
}

// Where the rooms, titles, characters, names and the bubble stand (cells and pixels), for the tests.
export function roomsInfo(doc, w, h, now = 0, options = {}) {
  return buildRooms(doc, Math.floor(w), Math.floor(h), Number(now) || 0, isObject(options) ? options : {}).info;
}

// ---------------------------------------------------------------------------------------------------------------
// The text rows around the picture. Bars and chips carry a background only on a dark theme; a light one gets the tones.

const BAR = [30, 35, 49];
const INK = [226, 232, 240];
const MUTED = [148, 163, 184];
const NEED_TONE = { stuck: [248, 113, 113], sent_back: [253, 186, 116], reported: [148, 163, 184], waiting: [129, 140, 248] };
const NEED_MARK = { stuck: ATTENTION, sent_back: SENT_BACK, reported: MARK_IDLE, waiting: WAITING };
const NEED_TEXT_TONE = { stuck: "danger", sent_back: "accent", reported: "muted", waiting: "plain" };

// A span: text, tone and, on a dark theme only, a foreground `color`, a background `bg` (both [r, g, b]) and `bold`.
const sp = (text, tone, color, bold, bg) => {
  const s = { text, tone };
  if (color) s.color = typeof color === "string" ? color : hex(color);
  if (bold) s.bold = true;
  if (bg) s.bg = typeof bg === "string" ? bg : hex(bg);
  return s;
};

// One line of exactly `width` cells: cut at the width, padded with spaces (on `bg` when given).
function fitLine(line, width, bg) {
  const out = [];
  let used = 0;
  for (const span of line) {
    if (!span.text) continue;
    let text = "";
    for (const ch of span.text) {
      const cw = charWidth(ch);
      if (used + cw > width) break;
      text += ch;
      used += cw;
    }
    if (text) out.push({ ...span, text });
    if (used >= width) break;
  }
  if (used < width) out.push(sp(" ".repeat(width - used), "plain", null, false, bg));
  return out;
}

const padTo = (text, n) => text + " ".repeat(Math.max(0, n - displayWidth(text)));
const padLeft = (text, n) => " ".repeat(Math.max(0, n - displayWidth(text))) + text;

// left and right parts on one line, the right part flush to the edge when both fit (the gap on `bg`)
function sides(left, right, width, bg) {
  const gap = width - lineWidth(left) - lineWidth(right);
  return gap >= 1 ? [...left, sp(" ".repeat(gap), "plain", null, false, bg), ...right] : left;
}

// The header: one status bar, the hub's name on the left, steps and spend on the right.
function headerRow(doc, model, width, dark) {
  const working = !!model.hub && model.hub.working;
  const name = model.hub ? model.hub.name : clean(isObject(doc.root) ? doc.root.agent : "") || "?";
  const graph = isObject(doc.graph) ? doc.graph : null;
  const progress = graph && isObject(graph.progress) ? graph.progress : null;
  const total = progress ? Math.max(0, Math.floor(Number(progress.total) || 0)) : 0;
  const passed = progress ? Math.min(total, Math.max(0, Math.floor(Number(progress.passed) || 0))) : 0;
  const cost = isObject(doc.totals) ? finiteNumber(doc.totals.cost) : null;
  const bg = dark ? BAR : null;
  const c = (v) => (dark ? v : null);
  const right = [];
  if (total) right.push(sp(String(passed), "plain", c(INK), true, bg), sp("/" + total, "muted", c(MUTED), false, bg));
  if (cost !== null) right.push(sp((right.length ? "  " : "") + formatDollars(cost), "plain", c(INK), false, bg));
  let rightW = lineWidth(right);
  const room = width - 4 - (rightW ? 1 + rightW : 0);
  const shown = room >= Math.min(3, displayWidth(name)) ? right : [];
  rightW = lineWidth(shown);
  if (shown.length) shown.push(sp(" ", "plain", null, false, bg));
  const left = [sp(" ", "plain", null, false, bg), sp(MARK_HUB, working ? "accent" : "muted", c(working ? HUB_GOLD : MUTED), false, bg), sp(" ", "plain", null, false, bg), sp(truncate(name, Math.max(1, width - 3 - (rightW ? 2 + rightW : 0))), "plain", c(INK), true, bg)];
  return fitLine(sides(left, shown, width, bg), width, bg);
}

// What needs the user: a chip with the mark, then name, reason and count in columns; the row tinted by kind.
function needsRows(entries, width, dark) {
  if (!entries.length) return [];
  const reasonOf = (e) => (e.kind === "stuck" ? "stuck" : e.kind === "sent_back" ? "sent back by " + clean(seatName(e.by)) : e.kind === "reported" ? clean(e.words) : "waiting");
  const rows = entries.map((e) => ({ e, name: clean(e.agent) || "?", reason: reasonOf(e), count: e.n >= 2 ? LOOP_MARK + e.n : "" }));
  let nameW = Math.min(NAME_MAX, Math.max(...rows.map((r) => displayWidth(r.name))));
  let reasonW = Math.max(...rows.map((r) => displayWidth(r.reason)));
  const countW = Math.max(...rows.map((r) => displayWidth(r.count)));
  const fixed = 1 + 3 + 1 + 2 + (countW ? 1 + countW : 0) + 1;
  while (fixed + nameW + reasonW > width && (reasonW > 5 || nameW > 3)) {
    if (reasonW > 5 && reasonW >= nameW) reasonW -= 1;
    else if (nameW > 3) nameW -= 1;
    else reasonW -= 1;
  }
  return rows.map(({ e, name, reason, count }) => {
    const tone = NEED_TONE[e.kind];
    const bg = dark ? mix(BG, tone, 0.13) : null;
    const strong = e.kind === "stuck" || e.kind === "sent_back";
    const c = (v) => (dark ? v : null);
    const line = [
      sp(" ", "plain", null, false, bg),
      strong ? sp(" " + NEED_MARK[e.kind] + " ", NEED_TEXT_TONE[e.kind], c(PLAQUE_DARK), true, dark ? tone : null) : sp(" " + NEED_MARK[e.kind] + " ", NEED_TEXT_TONE[e.kind], c(tone), false, bg),
      sp(" ", "plain", null, false, bg),
      sp(padTo(truncate(name, nameW), nameW), "plain", c(mix(tone, WHITE, 0.6)), strong, bg),
      sp("  ", "plain", null, false, bg),
      sp(padTo(truncate(reason, reasonW), reasonW), NEED_TEXT_TONE[e.kind], c(mix(tone, WHITE, 0.2)), false, bg),
    ];
    const right = count ? [sp(padLeft(count, countW), "muted", c(MUTED), false, bg), sp(" ", "plain", null, false, bg)] : [sp(" ", "plain", null, false, bg)];
    return fitLine(sides(line, right, width, bg), width, bg);
  });
}

// What an activity is: its zone, glyph, colour, verb and target.
function describe(act) {
  const zone = zoneOfActivity(act) || "tools";
  const room = ROOM_OF_ZONE[zone];
  const tool = clean(act.tool).toLowerCase();
  let verb = act.kind === "text" ? "replying" : act.kind === "reasoning" ? "thinking" : pick(TOOL_VERBS, tool) || (tool ? `${ZONE_VERBS[zone]} ${tool}` : ZONE_VERBS[zone]);
  if (act.kind === "tool" && !tool) verb = ZONE_VERBS[zone];
  return { zone, glyph: ZONE_GLYPH[zone], rgb: ZONE_RGB[room], verb, target: act.kind === "tool" ? clean(act.target) : "" };
}

// NOW: a bar tinted with the zone's colour: label, a chip with the zone's glyph, the verb, the hub's target, the keys.
function nowRow(model, width, dark, keys) {
  const working = !!model.hub && model.hub.working;
  const act = model.act;
  const known = working && isObject(act) && (act.kind === "tool" || act.kind === "reasoning" || act.kind === "text");
  const d = known ? describe(act) : null;
  const bg = dark ? (d ? mix(BG, d.rgb, 0.2) : BAR) : null;
  const c = (v) => (dark ? v : null);
  const left = [sp(" NOW ", "muted", c(MUTED), false, bg)];
  if (d) {
    left.push(sp(` ${d.glyph} `, "accent", c(PLAQUE_DARK), true, dark ? d.rgb : null), sp(" ", "plain", null, false, bg), sp(d.verb, d.rgb ? "accent" : "plain", c(mix(d.rgb, WHITE, 0.3)), true, bg));
    const room = width - lineWidth(left) - 3;
    if (d.target && room >= 3) left.push(sp("  ", "plain", null, false, bg), sp(truncate(d.target, room), "plain", c(INK), false, bg));
  } else if (working) left.push(sp("working", "accent", c(HUB_GOLD), true, bg));
  else left.push(sp("☾ resting", "muted", c(MUTED), false, bg));
  let hint = [];
  let text = "";
  for (const pair of list(keys)) if (Array.isArray(pair) && pair.length >= 2) text += (text ? "  " : "") + `${pair[0]} ${pair[1]}`;
  if (text && width - lineWidth(left) - 4 >= displayWidth(text)) hint = [sp(text, "muted", c(MUTED), false, bg), sp(" ", "plain", null, false, bg)];
  return fitLine(sides(left, hint, width, bg), width, bg);
}

// BEFORE: the last actions as small chips, a glyph and a short target each; an error adds a red chip.
function beforeRow(model, width, dark) {
  const recent = model.act && Array.isArray(model.act.recent) ? model.act.recent.filter(isObject) : [];
  if (!model.hub || !model.hub.working || !recent.length) return null;
  const c = (v) => (dark ? v : null);
  const line = [sp(" BEFORE ", "muted", c(MUTED))];
  let used = lineWidth(line);
  let chips = 0;
  for (const item of recent) {
    const zone = typeof item.zone === "string" && Object.hasOwn(ZONE_GLYPH, item.zone) ? item.zone : zoneOfTool(item.tool);
    const rgb = ZONE_RGB[ROOM_OF_ZONE[zone]];
    const label = ` ${ZONE_GLYPH[zone]} ${truncate(clean(item.target) || clean(item.tool) || zone, BEFORE_ITEM_MAX)} `;
    const bad = item.status === "error";
    const need = (chips ? 1 : 0) + displayWidth(label) + (bad ? 3 : 0);
    if (used + need > width) break;
    if (chips) line.push(sp(" ", "plain"));
    line.push(sp(label, "muted", c(mix(rgb, WHITE, 0.25)), false, dark ? mix(BG, rgb, 0.2) : null));
    if (bad) line.push(sp(" ! ", "danger", c(PLAQUE_DARK), true, dark ? NEED_TONE.stuck : null));
    used += need;
    chips += 1;
  }
  return chips ? fitLine(line, width) : null;
}

// A thin rule with its heading: ` AT WORK ─────`.
function ruleRow(width, dark) {
  const c = (v) => (dark ? v : null);
  return fitLine([sp(" AT WORK ", "muted", c(MUTED)), sp("─".repeat(Math.max(0, width - 9)), "muted", c(mix(BG, WHITE, 0.2)))], width);
}

// One row per working agent, the busiest first, in columns: name, glyph and verb, task, cost on the right.
function workingRows(doc, model, width, dark) {
  const graph = isObject(doc.graph) ? doc.graph : null;
  const found = model.items.filter((i) => i.kind === "working").slice(0, WORKING_MAX);
  const c = (v) => (dark ? v : null);
  const rows = found.map((i) => {
    const act = isObject(i.agent.activity) ? i.agent.activity : null;
    const zone = zoneOfActivity(act) || "thinking";
    const verb = act && act.kind === "tool" ? pick(TOOL_VERBS, clean(act.tool).toLowerCase()) || ZONE_VERBS[zone] : ZONE_VERBS[zone];
    return { i, zone, what: `${ZONE_GLYPH[zone]} ${verb}`, glyph: ZONE_GLYPH[zone], cost: formatDollars(i.cost), task: clean(currentGoal(graph, i.agent.name)) };
  });
  const nameW = Math.min(NAME_MAX, Math.max(0, ...rows.map((r) => displayWidth(r.i.name))));
  const costW = Math.max(0, ...rows.map((r) => displayWidth(r.cost)));
  const whatFull = Math.max(0, ...rows.map((r) => displayWidth(r.what)));
  const base = 1 + 2 + nameW + 2;
  const costRoom = width > 30 ? costW + 2 : 1;
  const whatW = base + whatFull + costRoom <= width ? whatFull : Math.min(whatFull, 1); // too narrow: the glyph alone
  return rows.map((r) => {
    const i = r.i;
    const what = whatW === whatFull ? r.what : r.glyph;
    const line = [
      sp(" ", "plain"), sp("▪ ", "plain", c(i.color)), sp(padTo(i.name, nameW), "plain", c(mix(i.color, WHITE, 0.45))), sp("  ", "plain"),
      sp(padTo(what, whatW), "muted", c(mix(ZONE_RGB[ROOM_OF_ZONE[r.zone]], WHITE, 0.15))),
    ];
    const room = width - lineWidth(line) - 2 - (width > 30 ? costW + 1 : 0);
    if (r.task && room >= 4) line.push(sp("  " + truncate(r.task, room), "muted", c(MUTED)));
    const right = width > 30 ? [sp(padLeft(r.cost, costW), "muted", c(MUTED)), sp(" ", "plain")] : [];
    return fitLine(sides(line, right, width), width);
  });
}

// How often the picture needs a new frame at `now`: 0 when nothing moves, 100 ms in the hub's walk, else BOB_MS / 2.
export function roomsInterval(doc, options = {}, now = 0) {
  if (!isObject(doc) || (isObject(options) && options.motion === "reduced") || !isAnimated(doc)) return 0;
  const hub = findHub(doc, validAgents(doc));
  const act = hub && isObject(hub.activity) ? hub.activity : null;
  const start = act && finiteNumber(act.time) !== null ? act.time * 1000 : null;
  if (start !== null && now - start >= 0 && now - start < WALK_MS) return 100;
  return BOB_MS / 2;
}

// The whole panel: header bar, what needs the user, the picture, NOW, BEFORE and the working agents. Rows give way, last
// first: the working agents (and their rule), BEFORE, NOW, what needs the user; the picture keeps at least 13 rows (more on a tall panel).
export function roomsScene(snapshot, options = {}) {
  options = isObject(options) ? options : {};
  const width = Math.max(0, Math.floor(Number(options.width) || 0));
  const height = Math.max(0, Math.floor(Number(options.height) || 0));
  if (!height) return [];
  const doc = snapshot;
  const nowOpt = Number(options.now);
  const now = Number.isFinite(nowOpt) ? nowOpt : 0;
  const agents = isObject(doc) && (doc.error === undefined || doc.error === null) ? validAgents(doc) : [];
  if (!agents.length) return orbitScene(snapshot, options); // the orbit's messages: Loading, No agents, an error
  try {
    const dark = !(isObject(options.colors) && options.colors.dark === false);
    const model = modelOf(doc, options);
    const out = [headerRow(doc, model, width, dark)];
    if (height === 1) return out;
    const listed = new Set(agents.map((a) => a.name));
    const entries = needsEntries(doc, listed);
    const needs = needsRows(entries.slice(0, NEEDS_MAX), width, dark);
    const more = entries.length > NEEDS_MAX ? fitLine([sp(` +${entries.length - NEEDS_MAX} more`, "muted", dark ? MUTED : null)], width) : null;
    const now1 = nowRow(model, width, dark, options.keys);
    const before = beforeRow(model, width, dark);
    const working = workingRows(doc, model, width, dark);
    const floor = Math.min(height - 1, Math.max(13, Math.ceil((height - 1) * 0.55)));
    let room = height - 1 - floor;
    const top = [];
    const bottom = [];
    const take = (rows, into) => {
      for (const row of rows) {
        if (room < 1) break;
        into.push(row);
        room -= 1;
      }
    };
    take(needs, top);
    take([now1], bottom);
    take(before ? [before] : [], bottom);
    if (working.length && room >= 2) take([ruleRow(width, dark)], bottom); // the rule only with a row under it
    take(working, bottom);
    if (!working.length || bottom[bottom.length - 1] === now1 || bottom.length === 0) take([], bottom);
    if (more) take([more], top);
    const picture = height - 1 - top.length - bottom.length;
    out.push(...top);
    out.push(...roomsLines(doc, width, picture, now, options));
    out.push(...bottom);
    return out.slice(0, height).map((line) => fitLine(line, width));
  } catch (_) {
    return blankLines(width, height);
  }
}
