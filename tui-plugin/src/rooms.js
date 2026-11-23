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
  MARK_HUB, ATTENTION, SENT_BACK, ZONE_VERBS, TOOL_VERBS,
  charWidth, displayWidth, truncate, clean, lineWidth, formatDollars, isObject, list, busyCount, finiteNumber, validAgents, findHub,
  hubWorking, currentGoal, agentCost, needsEntries, needsLine, isAnimated,
} from "./render.js";
import { scene as orbitScene, makePalette } from "./scene.js";

export const ROOMS_MIN_WIDTH = 36;
export const ROOMS_MIN_HEIGHT = 14;
export const WALK_MS = 1200; // the hub walks to the room of a new action for this long
export const BOB_MS = 500; // a working character moves up and down one pixel every BOB_MS
const NEEDS_MAX = 3;
const WORKING_MAX = 5;
const NAME_MAX = 12;
const BEFORE_ITEM_MAX = 22;

const BG = [13, 15, 20];
const ZONE_RGB = {
  terminal: [74, 222, 128], files: [251, 191, 36], web: [96, 165, 250], search: [192, 132, 252], thinking: [244, 114, 182], team: [148, 163, 184],
};
const HUB_GOLD = [250, 204, 21];
const ROOMS = [["terminal", 0, 0], ["files", 1, 0], ["web", 2, 0], ["search", 0, 1], ["thinking", 1, 1], ["team", 2, 1]];
const TITLES = { terminal: [">_", "TERMINAL"], files: ["▤", "FILES"], web: ["◍", "WEB"], search: ["⌕", "SEARCH"], thinking: ["…", "THINKING"], team: ["☾", "TEAM"] };
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

const HERO = ["..a..", ".hhh.", "hEhEh", ".hhh.", "bbbbb", "hbbbh", ".bbb.", ".l.l.", ".l.l."];
const HUB_TIERS = [HERO, [".a.", "EhE", "bbb", "bbb", "l.l"], ["hhh", "bbb", "l.l"], ["a", "b"]];
const BOT = ["hhh", "EhE", "bbb", "bbb", "l.l"];
const BOT_TIERS = [BOT, BOT, ["hhh", "bbb", "l.l"], ["h", "b"]];

function palette(hub, body, dim) {
  const k = dim ? (c) => mix(c, BG, 0.55) : (c) => c;
  return hub
    ? { a: ANTENNA, h: SKIN, E: EYE, b: body, l: [51, 65, 85] }
    : { h: k([203, 213, 225]), E: dim ? BG : EYE, b: k(body), l: k([71, 85, 105]) };
}

// The props, one group a room: [sprite rows, palette, dx, dy]
const PROPS = {
  terminal: [[["mmmmmmmmm", "mkkkkkkkm", "mkgkkkkkm", "mkkgkggkm", "mkgkkkkkm", "mkkkkkkkm", "mmmmmmmmm", "...sss...", "ddddddddddd"],
    { m: [71, 85, 105], k: [5, 18, 12], g: ZONE_RGB.terminal, s: [71, 85, 105], d: [120, 83, 50] }, 0, 0]],
  files: [[["ccccccc", "cyyyyyc", "cyyhyyc", "ccccccc", "cyyyyyc", "cyyhyyc", "ccccccc", "cyyyyyc", "cyyhyyc", "ccccccc"],
    { c: [120, 83, 50], y: mix(ZONE_RGB.files, BG, 0.35), h: [40, 30, 15] }, 0, 0],
  [["wwww.", "wllww", "wwwww", "wllww", "wwwww"], { w: [226, 232, 240], l: [148, 163, 184] }, 9, 6]],
  web: [[["..bbbbb..", ".bbeebbb.", "bbeeebbbb", "bbbeebbeb", "bbbbbbeeb", "bbebbbeeb", "bbeebbbbb", ".bbbbbbb.", "..bbbbb.."], { b: ZONE_RGB.web, e: [52, 211, 153] }, 0, 0]],
  search: [[[".rrrr...", "rwwwwr..", "rwwwwr..", "rwwwwr..", "rwwwwr..", ".rrrrr..", "....rrr.", ".....rrr", "......rr"], { r: ZONE_RGB.search, w: [30, 22, 45] }, 0, 0]],
  thinking: [[[".yyy.", "yyyyy", "yyyyy", ".yyy.", "..g..", "..g..", "..g..", ".ggg."], { y: [253, 224, 71], g: [100, 116, 139] }, 5, 0],
  [["s.......s", "sccccccs.", "sccccccs.", "sssssssss", "s.......s"], { s: mix(ZONE_RGB.thinking, BG, 0.45), c: mix(ZONE_RGB.thinking, BG, 0.2) }, 0, 10]],
  team: [0, 5, 10].map((dx) => [["ppp", "ppp", "ppp"], { p: mix(ZONE_RGB.team, BG, 0.6) }, dx, 0]),
};

// ---------------------------------------------------------------------------------------------------------------
// The picture

// The six rooms' rectangles (pixels, inclusive) for a canvas of `w` cells and `rows` text rows.
function geometry(w, rows) {
  const RX = [0, Math.floor(w / 3), Math.floor((2 * w) / 3), w];
  const RY = [0, rows, rows * 2];
  const rooms = {};
  for (const [name, cx, cy] of ROOMS) rooms[name] = { name, col: cx, row: cy, x0: RX[cx], x1: RX[cx + 1] - 1, y0: RY[cy], y1: RY[cy + 1] - 1 };
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

// One room's characters in slots: rows from the bottom of the stage up, the hub first, each row centred. Sprites shrink
// (the hub 5x9, 3x5, 3x3, a pixel pair; the others 3x5, 3x3, a pair) only when no names fit; names go least important first
// (the hub's last); characters go, least important first, only when the smallest will not fit. -> placements.
function fitRoom(items, stage) {
  const W = stage.x1 - stage.x0 + 1;
  if (W < 1 || stage.y1 - stage.y0 < 1) return [];
  const flow = (all, tier, named) => {
    const cells = all.map((item) => {
      const rows = item.hub ? HUB_TIERS[tier] : BOT_TIERS[tier];
      const label = named.has(item) ? (item.mark ? item.mark + " " : "") + item.name : null;
      const nw = label ? displayWidth(label) : 0;
      return { item, rows, sw: rows[0].length, sh: rows.length, label, nw, iw: Math.max(rows[0].length, nw) };
    });
    if (cells.some((c) => c.iw > W)) return null;
    const lines = [];
    let cur = null;
    for (const c of cells) {
      if (!cur || cur.w + 1 + c.iw > W) {
        cur = { cells: [], w: -1 };
        lines.push(cur);
      }
      cur.cells.push(c);
      cur.w += 1 + c.iw;
    }
    const out = [];
    let B = stage.y1;
    for (const line of lines) {
      const has = line.cells.some((c) => c.label);
      if (has && B % 2 === 0) B -= 1;
      const band = has ? 3 : 0;
      const sh = Math.max(...line.cells.map((c) => c.sh));
      const top = B - band - sh + 1;
      if (top < stage.y0) return null;
      let x = stage.x0 + ((W - line.w) >> 1);
      for (const c of line.cells) {
        const sx = x + ((c.iw - c.sw) >> 1);
        const sy = B - band - c.sh + 1;
        out.push({ item: c.item, rows: c.rows, x: sx, y: sy, sw: c.sw, sh: c.sh, label: c.label, nx: x + ((c.iw - c.nw) >> 1), nr: c.label ? (B - 1) >> 1 : -1, ix: x, iw: c.iw, top: sy, bottom: c.label ? B : sy + c.sh - 1 });
        x += c.iw + 1;
      }
      B = top - 2;
    }
    return out;
  };
  let all = items.slice();
  for (;;) {
    for (let tier = 0; tier < 4; tier += 1) {
      const namable = all.filter((i) => i.name);
      const floor = namable.length && namable[0].hub ? 1 : 0;
      for (let n = namable.length; n >= floor; n -= 1) {
        const placed = flow(all, tier, new Set(namable.slice(0, n)));
        if (placed) return placed;
      }
    }
    if (all.length <= 1) return [];
    all = all.slice(0, -1);
  }
}

const colorFor = (c, dark) => (dark ? c : undefined);

// The picture alone: { lines, info }. `rows` text rows of `w` cells.
function buildRooms(doc, w, rows, now, options) {
  const pix = new Pix(w, rows);
  const rooms = geometry(w, rows);
  const info = { rooms, titles: [], chars: [], bubble: null, walking: false, lit: null };
  const reduced = options.motion === "reduced";
  const model = modelOf(isObject(doc) ? doc : {}, options);
  const lit = model.lit;
  info.lit = lit;

  // floors, walls
  for (const [name] of ROOMS) {
    const r = rooms[name];
    const z = ZONE_RGB[name];
    const on = name === lit;
    const plain = mix(BG, z, on ? 0.2 : 0.08);
    const checked = mix(plain, z, on ? 0.05 : 0.025);
    for (let y = r.y0; y <= r.y1; y += 1) {
      for (let x = r.x0; x <= r.x1; x += 1) pix.set(x, y, (((x >> 1) + (y >> 1)) & 1) === 1 ? checked : plain);
    }
    const wall = on ? z : mix(BG, z, 0.38);
    pix.rect(r.x0, r.y0, r.x1, r.y0, wall);
    pix.rect(r.x0, r.y1, r.x1, r.y1, wall);
    pix.rect(r.x0, r.y0, r.x0, r.y1, wall);
    pix.rect(r.x1, r.y0, r.x1, r.y1, wall);
  }
  // doors between neighbours
  const door = mix(BG, ZONE_RGB.team, 0.16);
  for (const [a, b] of [["terminal", "files"], ["files", "web"], ["search", "thinking"], ["thinking", "team"]]) {
    const l = rooms[a];
    const rt = rooms[b];
    const ym = centreOf(l)[1];
    const half = Math.min(2, (l.y1 - l.y0 - 3) >> 1);
    if (half >= 0) pix.rect(l.x1, ym - half, rt.x0, ym + half, door);
  }
  for (const [a, b] of [["terminal", "search"], ["files", "thinking"], ["web", "team"]]) {
    const t = rooms[a];
    const bt = rooms[b];
    const xm = centreOf(t)[0];
    const half = Math.min(2, (t.x1 - t.x0 - 3) >> 1);
    if (half >= 0) pix.rect(xm - half, t.y1, xm + half, bt.y0, door);
  }

  // titles, and each room's stage under them
  const stages = {};
  const titleDraws = [];
  for (const [name] of ROOMS) {
    const r = rooms[name];
    const [glyph, word] = TITLES[name];
    const tr = Math.ceil((r.y0 + 1) / 2);
    const avail = r.x1 - r.x0 - 2;
    const title = avail >= displayWidth(glyph + " " + word) ? glyph + " " + word : avail >= displayWidth(glyph) ? glyph : "";
    let top = r.y0 + 1;
    if (title && tr * 2 + 1 <= r.y1 - 1) {
      const tw = displayWidth(title);
      pix.take(r.x0 + 2, tr * 2, r.x0 + 2 + tw - 1, tr * 2 + 1);
      titleDraws.push({ name, tr, text: title, x: r.x0 + 2 });
      info.titles.push({ room: name, text: title, c0: r.x0 + 2, c1: r.x0 + 2 + tw - 1, r: tr });
      top = tr * 2 + 3;
    }
    stages[name] = { x0: r.x0 + 2, x1: r.x1 - 2, y0: top, y1: r.y1 - 2 };
  }

  // the characters in their rooms
  const placed = [];
  for (const [name] of ROOMS) {
    const mine = model.items.filter((i) => i.room === name);
    if (!mine.length) continue;
    for (const p of fitRoom(mine, stages[name])) {
      placed.push({ ...p, room: name });
      pix.take(p.ix, p.top, p.ix + p.iw - 1, p.bottom);
    }
  }
  const hubAt = placed.find((p) => p.item.hub) || null;

  // the walk: from the middle of the room of the last action to the hub's place, during the first WALK_MS of its action
  let walk = null;
  const before = model.act && Array.isArray(model.act.recent) ? model.act.recent.find(isObject) : null;
  const fromRoom = before ? roomOfActivity(before, null) : null;
  if (hubAt && model.hub.working && fromRoom && fromRoom !== hubAt.room) {
    const feet = [hubAt.x + (hubAt.sw >> 1), hubAt.y + hubAt.sh - 1];
    const pts = pathBetween(rooms, fromRoom, hubAt.room);
    pts[pts.length - 1] = feet;
    walk = { pts, lens: lengthsOf(pts) };
  }
  let walkAt = null;
  if (walk && !reduced && model.start !== null) {
    const t = (now - model.start) / WALK_MS;
    if (t >= 0 && t < 1) walkAt = pointAt(walk.pts, walk.lens, walk.lens[walk.lens.length - 1] * (t * t * (3 - 2 * t)));
  }
  info.walking = !!walkAt;

  // the bubble over the hub: what it does, cut to the room
  let bubble = null;
  const target = model.hub && model.hub.working && model.act ? clean(model.act.target) : "";
  if (hubAt && target && !walkAt) {
    const r = rooms[hubAt.room];
    const br = (hubAt.y - 2) >> 1;
    const limit = r.x1 - 1 - (r.x0 + 1) + 1 - 2;
    const text = truncate(target, Math.min(limit, 24));
    if (limit >= 3 && br >= 1 && displayWidth(text) >= 3) {
      const bw = displayWidth(text) + 2;
      for (const bx of [hubAt.x + (hubAt.sw >> 1) - (bw >> 1), r.x1 - 1 - bw + 1, r.x0 + 1]) {
        const x0 = Math.max(r.x0 + 1, Math.min(bx, r.x1 - 1 - bw + 1));
        if (pix.free(x0, br * 2, x0 + bw - 1, br * 2 + 1)) {
          bubble = { text, c0: x0, r: br, w: bw };
          pix.take(x0, br * 2, x0 + bw - 1, br * 2 + 1);
          break;
        }
      }
    }
  }
  info.bubble = bubble ? { text: bubble.text, c0: bubble.c0, c1: bubble.c0 + bubble.w - 1, r: bubble.r } : null;

  // props: where the characters, the names and the titles leave room
  const propsAt = [];
  for (const [name] of ROOMS) {
    const st = stages[name];
    const group = PROPS[name];
    const gw = Math.max(...group.map(([rowsOf, , dx]) => dx + rowsOf[0].length));
    const ax = st.x0 + Math.max(0, (st.x1 - st.x0 + 1 - gw) >> 1);
    for (const [rowsOf, pal, dx, dy] of group) {
      const sw = Math.max(...rowsOf.map((s) => s.length));
      const x = ax + dx;
      const y = st.y0 + 1 + dy;
      if (x + sw - 1 <= st.x1 && y + rowsOf.length - 1 <= st.y1 && pix.free(x, y, x + sw - 1, y + rowsOf.length - 1)) {
        pix.take(x, y, x + sw - 1, y + rowsOf.length - 1);
        propsAt.push({ rowsOf, pal, x, y });
      }
    }
  }

  // drawing: the trail, the props, the characters, then the text over them
  if (walk && hubAt) {
    const total = walk.lens[walk.lens.length - 1];
    const marks = Math.floor(total / 3);
    for (let i = 0; i < marks; i += 1) {
      const [x, y] = pointAt(walk.pts, walk.lens, i * 3 + 2);
      pix.set(Math.round(x), Math.round(y) + (i % 2 ? 1 : 0), mix(BG, ANTENNA, Math.min(0.7, 0.15 + 0.05 * i)));
    }
  }
  for (const p of propsAt) pix.sprite(p.x, p.y, p.rowsOf, p.pal);
  const bobs = !reduced && Math.floor(now / BOB_MS) % 2 === 1;
  for (const p of placed) {
    const it = p.item;
    const pal = palette(it.hub, it.color, it.dim);
    if (it.hub && walkAt) {
      pix.sprite(Math.round(walkAt[0]) - (p.sw >> 1), Math.round(walkAt[1]) - p.sh + 1, p.rows, pal);
      info.chars.push({ name: it.name, hub: true, kind: it.kind, room: p.room, x0: Math.round(walkAt[0]) - (p.sw >> 1), x1: Math.round(walkAt[0]) - (p.sw >> 1) + p.sw - 1, y0: Math.round(walkAt[1]) - p.sh + 1, y1: Math.round(walkAt[1]), name_cells: null });
      continue;
    }
    const dy = it.working && bobs ? -1 : 0;
    pix.sprite(p.x, p.y + dy, p.rows, pal);
    info.chars.push({ name: it.name, hub: it.hub, kind: it.kind, room: p.room, x0: p.x, x1: p.x + p.sw - 1, y0: p.y + dy, y1: p.y + dy + p.sh - 1, name_cells: p.label ? { c0: p.nx, c1: p.nx + displayWidth(p.label) - 1, r: p.nr } : null });
  }
  for (const t of titleDraws) {
    const on = t.name === lit;
    const z = ZONE_RGB[t.name];
    pix.text(t.x, t.tr, t.text, on ? z : mix(BG, z, 0.75), undefined, on);
  }
  for (const p of placed) {
    if (!p.label || (p.item.hub && walkAt)) continue;
    const it = p.item;
    const color = it.hub ? HUB_GOLD : it.mark ? (it.mark === ATTENTION ? [248, 113, 113] : [253, 186, 116]) : mix(it.color, WHITE, 0.45);
    pix.text(p.nx, p.nr, p.label, color, undefined, it.hub);
  }
  if (bubble) pix.text(bubble.c0, bubble.r, " " + bubble.text + " ", [5, 18, 12], [226, 232, 240], true);
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
// The text rows around the picture

const sp = (text, tone, color, bold) => {
  const s = { text, tone };
  if (color) s.color = color;
  if (bold) s.bold = true;
  return s;
};

// One line of exactly `width` cells: cut at the width, padded with spaces.
function fitLine(line, width) {
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
  if (used < width) out.push(sp(" ".repeat(width - used), "plain"));
  return out;
}

// left and right parts on one line, the right part flush to the edge when both fit
function sides(left, right, width) {
  const gap = width - lineWidth(left) - lineWidth(right);
  return gap >= 1 ? [...left, sp(" ".repeat(gap), "plain"), ...right] : left;
}

function headerRow(doc, model, width) {
  const working = !!model.hub && model.hub.working;
  const name = model.hub ? model.hub.name : clean(isObject(doc.root) ? doc.root.agent : "") || "?";
  const graph = isObject(doc.graph) ? doc.graph : null;
  const progress = graph && isObject(graph.progress) ? graph.progress : null;
  const total = progress ? Math.max(0, Math.floor(Number(progress.total) || 0)) : 0;
  const passed = progress ? Math.min(total, Math.max(0, Math.floor(Number(progress.passed) || 0))) : 0;
  const cost = isObject(doc.totals) ? finiteNumber(doc.totals.cost) : null;
  const right = [];
  if (total) right.push(sp(String(passed), "plain"), sp("/" + total, "muted"));
  if (cost !== null) right.push(sp((right.length ? "  " : "") + formatDollars(cost), "plain"));
  let rightW = lineWidth(right);
  const room = width - 3 - (rightW ? 1 + rightW : 0);
  const shown = room >= Math.min(3, displayWidth(name)) ? right : [];
  rightW = lineWidth(shown);
  const left = [sp(MARK_HUB, working ? "accent" : "muted"), sp(" ", "plain"), sp(truncate(name, Math.max(1, width - 2 - (rightW ? 1 + rightW : 0))), "plain", undefined, working)];
  return fitLine(sides(left, shown, width), width);
}

// What an activity is: its zone, glyph, colour, verb and target.
function describe(act, dark) {
  const zone = zoneOfActivity(act) || "tools";
  const room = ROOM_OF_ZONE[zone];
  const tool = clean(act.tool).toLowerCase();
  let verb = act.kind === "text" ? "replying" : act.kind === "reasoning" ? "thinking" : TOOL_VERBS[tool] || (tool ? `${ZONE_VERBS[zone]} ${tool}` : ZONE_VERBS[zone]);
  if (act.kind === "tool" && !tool) verb = ZONE_VERBS[zone];
  return { zone, glyph: ZONE_GLYPH[zone], color: colorFor(hex(ZONE_RGB[room]), dark), verb, target: act.kind === "tool" ? clean(act.target) : "" };
}

function nowRow(model, width, dark, keys) {
  const head = [sp("NOW ", "muted")];
  let left;
  if (model.hub && model.hub.working && isObject(model.act) && (model.act.kind === "tool" || model.act.kind === "reasoning" || model.act.kind === "text")) {
    const d = describe(model.act, dark);
    left = [...head, sp(`${d.glyph} ${d.verb}`, d.color ? "plain" : "accent", d.color, true)];
    const room = width - lineWidth(left) - 2;
    if (d.target && room >= 3) left.push(sp("  " + truncate(d.target, room), "plain"));
  } else if (model.hub && model.hub.working) left = [...head, sp("working", "accent", undefined, true)];
  else left = [...head, sp("☾ resting", "muted")];
  let hint = [];
  let text = "";
  for (const pair of list(keys)) if (Array.isArray(pair) && pair.length >= 2) text += (text ? "  " : "") + `${pair[0]} ${pair[1]}`;
  if (text && width - lineWidth(left) - 3 >= displayWidth(text)) hint = [sp(text, "muted")];
  return fitLine(sides(left, hint, width), width);
}

function beforeRow(model, width, dark) {
  const recent = model.act && Array.isArray(model.act.recent) ? model.act.recent.filter(isObject) : [];
  if (!model.hub || !model.hub.working || !recent.length) return null;
  const line = [sp("BEFORE ", "muted")];
  let used = 7;
  for (const item of recent) {
    const zone = typeof item.zone === "string" && Object.hasOwn(ZONE_GLYPH, item.zone) ? item.zone : zoneOfTool(item.tool);
    const label = `${ZONE_GLYPH[zone]} ${truncate(clean(item.target) || clean(item.tool) || zone, BEFORE_ITEM_MAX)}`;
    const bad = item.status === "error";
    const need = (line.length > 1 ? 2 : 0) + displayWidth(label) + (bad ? 2 : 0);
    if (used + need > width) break;
    if (line.length > 1) line.push(sp("  ", "plain"));
    line.push(sp(label, "muted", colorFor(hex(mix(ZONE_RGB[ROOM_OF_ZONE[zone]], BG, 0.25)), dark)));
    if (bad) line.push(sp(" !", "danger"));
    used += need;
  }
  return line.length > 1 ? fitLine(line, width) : null;
}

function workingRows(doc, model, width, dark) {
  const graph = isObject(doc.graph) ? doc.graph : null;
  const found = model.items.filter((i) => i.kind === "working");
  const nameW = Math.min(NAME_MAX, Math.max(0, ...found.map((i) => displayWidth(i.name))));
  return found.map((i) => {
    const act = isObject(i.agent.activity) ? i.agent.activity : null;
    const zone = zoneOfActivity(act) || "thinking";
    const verb = act && act.kind === "tool" ? TOOL_VERBS[clean(act.tool).toLowerCase()] || ZONE_VERBS[zone] : ZONE_VERBS[zone];
    const cost = formatDollars(i.cost);
    const task = clean(currentGoal(graph, i.agent.name));
    const dim = colorFor(hex(ZONE_RGB.team), dark);
    const left = [sp("▪ ", "plain", colorFor(hex(i.color), dark)), sp(i.name + " ".repeat(nameW - displayWidth(i.name)), "plain", colorFor(hex(mix(i.color, WHITE, 0.45)), dark))];
    const tail = [sp(`${ZONE_GLYPH[zone]} ${verb}`, "muted", dim)];
    const room = width - lineWidth(left) - 1 - lineWidth(tail) - 2 - (width > 30 ? displayWidth(cost) + 1 : 0);
    if (task && room >= 4) tail.push(sp("  " + truncate(task, room), "muted", dim));
    const right = width > 30 ? [sp(cost, "muted")] : [];
    return fitLine(sides([...left, sp(" ", "plain"), ...tail], right, width), width);
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

// The whole panel: header, what needs the user, the picture, NOW, BEFORE and the working agents. Rows give way, last
// first: the working agents, BEFORE, NOW, what needs the user; the picture keeps at least 13 rows (more on a tall panel).
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
    const out = [headerRow(doc, model, width)];
    if (height === 1) return out;
    const listed = new Set(agents.map((a) => a.name));
    const needs = needsEntries(doc, listed).map((e) => fitLine(needsLine(e, width), width)).slice(0, NEEDS_MAX);
    const now1 = nowRow(model, width, dark, options.keys);
    const before = beforeRow(model, width, dark);
    const working = workingRows(doc, model, width, dark).slice(0, WORKING_MAX);
    const floor = Math.min(height - 1, Math.max(13, Math.ceil((height - 1) * 0.55)));
    let room = height - 1 - floor;
    const top = [];
    const bottom = [];
    for (const [rows, into] of [[needs, top], [[now1], bottom], [before ? [before] : [], bottom], [working, bottom]]) {
      for (const row of rows) {
        if (room < 1) break;
        into.push(row);
        room -= 1;
      }
    }
    const picture = height - 1 - top.length - bottom.length;
    out.push(...top);
    out.push(...roomsLines(doc, width, picture, now, options));
    out.push(...bottom);
    return out.slice(0, height).map((line) => fitLine(line, width));
  } catch (_) {
    return blankLines(width, height);
  }
}
