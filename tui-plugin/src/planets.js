/*
 * The pixel planets: the Desktop graph in a terminal. The same figure as the 3D orbit (src/scene.js: its layout, camera,
 * depth and name placing are used as they are), but every body is a smooth shaded ball and every line is one pixel thin.
 * A pixel is half a cell: `▀` with the top pixel as its foreground and the bottom one as its background (two equal pixels
 * are a space with that background), so a 62 x 30 picture is 62 x 60 pixels. Names and the pill are ordinary
 * text over the picture. Nothing is drawn in letters or digits except those.
 *
 *   planetsLines(doc, w, h, now, options) -> Line[]   exactly `h` lines of exactly `w` cells; Line = Span[],
 *     Span = { text, tone, color?, bg?, bold? }; never throws
 *   planetsInfo(doc, w, h, now, options)  -> where everything stands (for the tests)
 *   planetsInterval(doc, options, now)    -> ms to the next frame, 0 for none
 *   planetsScene(snapshot, options)       -> the panel: a header, the picture, the keys; exactly `height` lines
 * options: { orbit, motion: "reduced" | undefined, colors: { dark, muted } }
 *
 * Under the hub: its name and a pill saying what the main chat does now (a zone mark, a verb, the target). They go
 * below the hub, or above it where that covers fewer balls. No row of symbols is drawn.
 */
import {
  ZONE_VERBS, TOOL_VERBS, SIDE_NAME,
  charWidth, displayWidth, truncate, clean, seatName, isObject, busyCount, hubWorking, sideBusy,
  validAgents, findHub, needsEntries, lineWidth, clipLine,
} from "./render.js";
import { zoneOfTool } from "./zones.js";
import {
  orbitLayout, figure3D, camera3D, orbitCamera, moonPhase, moonPoint, makePalette, Canvas, Styles, placeBodies,
  NAME_MAX, PERSPECTIVE, frameInterval, scene as orbitScene,
} from "./scene.js";

export const PLANETS_MIN_WIDTH = 36;
export const PLANETS_MIN_HEIGHT = 14;

// ---------------------------------------------------------------------------------------------------------------
// Colours: the Desktop's dark theme

const rgbOfHex = (s) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
const BG = rgbOfHex("#131111");
const INK = rgbOfHex("#e8e0da");
const MUTED = rgbOfHex("#b0a39c");
const IDLE = rgbOfHex("#8f8178");
const PEACH = rgbOfHex("#dfa183");
const CORAL = rgbOfHex("#ed9583");
const WARM = rgbOfHex("#e0874a");
const ORBIT_LINE = rgbOfHex("#65564e");
const ZONE_GLYPH = { web: "⊕", terminal: "❯", files: "▤", search: "⊙", thinking: "◇", spawn: "✦", messaging: "◆", tasks: "≡", tools: "◆" };
const GROUP_TINT = { build: "#b89987", decide: "#a996a7", council: "#b49f8c", understand: "#a5988e", verify: "#a79aac" };
const FALLBACK_TINT = rgbOfHex("#97877e");

const HUB_RAMP = [[58, 30, 20], [150, 88, 60], [196, 126, 91], [236, 180, 142], [255, 241, 220]];
const WORK_RAMP = [[44, 24, 17], [128, 76, 52], [196, 126, 91], [231, 178, 147], [255, 241, 229]];
const IDLE_RAMP = [[20, 17, 16], [46, 39, 36], [84, 73, 68], [128, 116, 109], [166, 155, 148]];
const CORE_HUB = rgbOfHex("#fff1dc");
const LIGHT = (() => {
  const v = [-0.42, -0.5, 0.75];
  const l = Math.hypot(...v);
  return v.map((x) => x / l);
})();

const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const hexes = new Map();
const hex = (c) => {
  const r = clamp(Math.round(c[0]), 0, 255);
  const g = clamp(Math.round(c[1]), 0, 255);
  const b = clamp(Math.round(c[2]), 0, 255);
  const key = (r << 16) | (g << 8) | b;
  let out = hexes.get(key);
  if (out === undefined) {
    out = "#" + (key | 0x1000000).toString(16).slice(1);
    hexes.set(key, out);
  }
  return out;
};

function ramp(stops, t) {
  const x = clamp(t, 0, 1) * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(x));
  return mix(stops[i], stops[i + 1], x - i);
}

// An idle body's ramp: grey, its middle stops a little toward its group's colour.
const idleRamps = new Map();
function idleRamp(key) {
  const name = typeof key === "string" ? key : "";
  let r = idleRamps.get(name);
  if (!r) {
    const tint = Object.hasOwn(GROUP_TINT, name) ? rgbOfHex(GROUP_TINT[name]) : FALLBACK_TINT;
    r = IDLE_RAMP.map((c, i) => (i === 0 || i === IDLE_RAMP.length - 1 ? c : mix(c, tint, 0.05)));
    idleRamps.set(name, r);
  }
  return r;
}

// ---------------------------------------------------------------------------------------------------------------
// The pixels

class Pixels {
  constructor(w, h) {
    this.w = w;
    this.h = h;
    this.rgb = new Float32Array(w * h * 3);
    for (let i = 0; i < w * h; i += 1) {
      this.rgb[i * 3] = BG[0];
      this.rgb[i * 3 + 1] = BG[1];
      this.rgb[i * 3 + 2] = BG[2];
    }
    this.cov = new Float32Array(w * h); // a thin line's coverage, kept apart so crossing samples do not add up
    this.bb = [w, -1, h, -1];
  }

  blend(x, y, c, a) {
    if (x < 0 || y < 0 || x >= this.w || y >= this.h || !(a > 0)) return;
    const i = (y * this.w + x) * 3;
    const k = a > 1 ? 1 : a;
    this.rgb[i] += (c[0] - this.rgb[i]) * k;
    this.rgb[i + 1] += (c[1] - this.rgb[i + 1]) * k;
    this.rgb[i + 2] += (c[2] - this.rgb[i + 2]) * k;
  }

  get(x, y) {
    const i = (y * this.w + x) * 3;
    return [this.rgb[i], this.rgb[i + 1], this.rgb[i + 2]];
  }

  // A thin line's sample at (x, y) in pixels: the pixel it falls in (crisp: a bilinear spread blurs a one-pixel line).
  splat(x, y, a) {
    this.cover(Math.floor(x), Math.floor(y), a);
  }

  cover(i, j, a) {
    if (i < 0 || j < 0 || i >= this.w || j >= this.h || !(a > 0)) return;
    const k = j * this.w + i;
    if (a > this.cov[k]) this.cov[k] = a;
    const bb = this.bb;
    if (i < bb[0]) bb[0] = i;
    if (i > bb[1]) bb[1] = i;
    if (j < bb[2]) bb[2] = j;
    if (j > bb[3]) bb[3] = j;
  }

  // Lay the coverage layer over the picture in colour `c`, then clear it.
  flush(c) {
    const bb = this.bb;
    for (let j = bb[2]; j <= bb[3]; j += 1) {
      for (let i = bb[0]; i <= bb[1]; i += 1) {
        const k = j * this.w + i;
        const a = this.cov[k];
        if (a > 0) {
          this.blend(i, j, c, a);
          this.cov[k] = 0;
        }
      }
    }
    this.bb = [this.w, -1, this.h, -1];
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Small things

function hash(a, b) {
  let x = Math.imul((a | 0) ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul((b | 0) + 0x632be5ab, 0xc2b2ae35);
  x = Math.imul(x ^ (x >>> 16), 0x7feb352d);
  x = Math.imul(x ^ (x >>> 15), 0x846ca68b);
  return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
}

function pick(map, key) {
  return typeof key === "string" && Object.hasOwn(map, key) ? map[key] : "";
}

const ZONE_NAMES = new Set(Object.keys(ZONE_VERBS));
function zoneOfActivity(act) {
  if (!isObject(act)) return null;
  if (typeof act.zone === "string" && act.zone !== "") return ZONE_NAMES.has(act.zone) ? act.zone : "tools";
  if (act.kind === "reasoning" || act.kind === "text") return "thinking";
  if (act.kind === "tool") return zoneOfTool(act.tool);
  return null;
}

// What the main chat does, for the pill: { zone, glyph, verb, target } (target cleaned as the list cleans it).
function describe(act) {
  const zone = zoneOfActivity(act) || "tools";
  const tool = clean(act.tool).toLowerCase();
  let verb = act.kind === "text" ? "replying" : act.kind === "reasoning" ? "thinking" : pick(TOOL_VERBS, tool) || (tool ? `${ZONE_VERBS[zone]} ${tool}` : ZONE_VERBS[zone]);
  if (act.kind === "tool" && !tool) verb = ZONE_VERBS[zone];
  return { zone, glyph: ZONE_GLYPH[zone] || "◆", verb, target: act.kind === "tool" ? clean(act.target) : "" };
}

// ---------------------------------------------------------------------------------------------------------------
// The picture

function buildPlanets(doc, w, h, now, options) {
  const reduced = options.motion === "reduced";
  const W = w;
  const H = h * 2;
  const px = new Pixels(W, H);
  // row 0 is the pill's: the figure is laid out in the rows below it
  const L = orbitLayout(doc, w, Math.max(1, h - 1));
  const F = figure3D(L);
  const cam = orbitCamera(options.orbit, reduced ? 0 : now);
  const project = camera3D(L, cam.turn, cam.elevation);
  const P = makePalette(L.keys, options.colors);
  const small = w < 50 || h < 20;
  const owner = new Int32Array(W * H).fill(-1);
  const ids = [];
  const t = reduced ? 0 : now;

  // the figure a little smaller than the orbit's (the same places, scaled about the middle) so balls and names have room
  const K = small ? 0.8 : h < 34 ? 0.8 : 0.9;
  const at = (q) => ({ x: (L.cx + (q.x - L.cx) * K) / 2, y: (L.cy + (q.y - L.cy) * K) / 2 + 2, depth: q.depth });
  const hubQ = project([0, 0, 0]);
  const hubP = at({ x: project.hub.x, y: project.hub.y, depth: 0 });
  const base = Math.min(W, H);
  const hubR = clamp(base * (small ? 0.12 : 0.135), 3, 13);
  const scaleOf = (depth) => PERSPECTIVE / (PERSPECTIVE - depth);
  const light = (depth) => 0.55 + 0.45 * clamp((depth + 1) / 2, 0, 1); // far bodies are dimmer
  const far = (depth) => 0.4 * (1 - clamp((depth + 1) / 2, 0, 1)); // ... and mixed toward the sky

  // ---- orbits: a few thin, very faint ellipses behind everything
  const sweep = (points, alphaOf, step = 1) => {
    for (let i = 0; i < points.length; i += step) {
      const q = project(points[i]);
      const p = at(q);
      px.splat(p.x, p.y, alphaOf(q.depth));
    }
  };
  for (const ring of F.rings) sweep(ring.points, (d) => 0.07 + 0.12 * clamp((d + 1) / 2, 0, 1));
  px.flush(ORBIT_LINE);

  // ---- the bodies
  const bodies = [];
  const hubWork = hubWorking(doc, L.hub);
  const needs = (st) => st === "stuck" || st === "back";
  const addBody = (b) => bodies.push(b);
  addBody({ id: "hub", kind: "hub", depth: 0, x: hubP.x, y: hubP.y, r: hubR, working: hubWork, needs: false, key: "hub" });
  const planetR = (depth) => Math.max(small ? 1.7 : 2, hubR * 0.5 * scaleOf(depth));
  for (const p of L.placed) {
    const pos = F.agents.get(p.agent.name);
    if (!pos) continue;
    const q = project(pos);
    const a = at(q);
    addBody({ id: "p:" + p.agent.name, kind: "planet", depth: q.depth, x: a.x, y: a.y, r: planetR(q.depth), working: p.state === "working", needs: needs(p.state), key: p.key, p, q });
  }
  const phase = moonPhase(now, reduced);
  const seatBodies = [];
  for (const seat of F.seats) {
    const q = project(moonPoint(seat, phase));
    const a = at(q);
    const st = L.states.get(seat.agent.name);
    const busy = busyCount(seat.agent) > 0;
    const b = { id: "m:" + seat.lead + "/" + seat.agent.name, kind: "moon", depth: q.depth, x: a.x, y: a.y, r: Math.max(small ? 1.3 : 1.5, hubR * 0.3 * scaleOf(q.depth)), working: busy, needs: needs(st), key: seat.key, seat, q, busy };
    seatBodies.push(b);
    addBody(b);
  }
  let sideBody = null;
  if (F.side) {
    const busy = sideBusy(doc);
    const q = project(moonPoint(F.side, busy ? phase : 0));
    const a = at(q);
    sideBody = { id: "side", kind: "side", depth: q.depth, x: a.x, y: a.y, r: Math.max(1.6, hubR * 0.36 * scaleOf(q.depth)), working: false, needs: false, key: "side", q, busy };
    addBody(sideBody);
  }
  bodies.forEach((b, i) => {
    b.index = i;
  });
  const byAgent = new Map();
  for (const b of bodies) if (b.kind === "planet") byAgent.set(b.p.agent.name, b);

  // ---- the hub's glow, under the balls
  {
    const R = hubR * 2.5;
    const strength = hubWork ? 0.2 : 0.06;
    for (let y = Math.floor(hubP.y - R); y <= Math.ceil(hubP.y + R); y += 1) {
      for (let x = Math.floor(hubP.x - R); x <= Math.ceil(hubP.x + R); x += 1) {
        const d = Math.hypot(x + 0.5 - hubP.x, y + 0.5 - hubP.y);
        if (d > hubR * 0.8 && d < R) px.blend(x, y, WARM, strength * (1 - (d - hubR * 0.8) / (R - hubR * 0.8)) ** 2);
      }
    }
  }

  // ---- hand-offs: thin curves between a worker and its evaluator, an arrowhead where the work goes
  for (const arc of F.arcs) {
    const s = arc.loop && arc.loop.state;
    const live = (s === "working" || s === "checking") && arc.loop.active !== false;
    const back = s === "sent_back";
    if (!live && !back) continue;
    const from = byAgent.get(arc.worker.agent.name);
    const to = byAgent.get(arc.evaluator.agent.name);
    if (!from || !to) continue;
    const pts = arc.points.map((v) => at(project(v)));
    const aim = back ? from : to; // the arrow lands on this ball
    const src = back ? to : from;
    const run = back ? pts.slice().reverse() : pts;
    // trim to the balls' rims
    let i0 = 0;
    while (i0 < run.length - 1 && Math.hypot(run[i0].x - src.x, run[i0].y - src.y) < src.r + 1.4) i0 += 1;
    let i1 = run.length - 1;
    while (i1 > i0 && Math.hypot(run[i1].x - aim.x, run[i1].y - aim.y) < aim.r + 1.6) i1 -= 1;
    if (i1 - i0 < 3) continue;
    const color = back ? CORAL : PEACH;
    for (let i = i0; i < i1; i += 1) {
      const a = run[i];
      const b = run[i + 1];
      const len = Math.hypot(b.x - a.x, b.y - a.y);
      const n = Math.max(1, Math.ceil(len * 2));
      for (let k = 0; k < n; k += 1) {
        const f = k / n;
        px.splat(a.x + (b.x - a.x) * f, a.y + (b.y - a.y) * f, 0.45);
      }
    }
    px.flush(color);
    // the arrowhead: a small triangle, tip at the end of the trimmed curve
    const tip = run[i1];
    const tail = run[Math.max(i0, i1 - 3)];
    let dx = tip.x - tail.x;
    let dy = tip.y - tail.y;
    const dl = Math.hypot(dx, dy) || 1;
    dx /= dl;
    dy /= dl;
    const size = small ? 2.4 : 3.2;
    const bx = tip.x - dx * size;
    const by = tip.y - dy * size;
    const nx = -dy * size * 0.5;
    const ny = dx * size * 0.5;
    const tri = [[tip.x, tip.y], [bx + nx, by + ny], [bx - nx, by - ny]];
    const minX = Math.floor(Math.min(...tri.map((v) => v[0])));
    const maxX = Math.ceil(Math.max(...tri.map((v) => v[0])));
    const minY = Math.floor(Math.min(...tri.map((v) => v[1])));
    const maxY = Math.ceil(Math.max(...tri.map((v) => v[1])));
    const edge = (a, b, x, y) => (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]);
    for (let y = minY; y <= maxY; y += 1) {
      for (let x = minX; x <= maxX; x += 1) {
        // 2 x 2 supersamples for a soft edge
        let hit = 0;
        for (const [ox, oy] of [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]]) {
          const e0 = edge(tri[0], tri[1], x + ox, y + oy);
          const e1 = edge(tri[1], tri[2], x + ox, y + oy);
          const e2 = edge(tri[2], tri[0], x + ox, y + oy);
          if ((e0 >= 0 && e1 >= 0 && e2 >= 0) || (e0 <= 0 && e1 <= 0 && e2 <= 0)) hit += 1;
        }
        px.cover(x, y, (hit / 4) * 0.85);
      }
    }
    px.flush(color);
  }

  // ---- balls, the farthest first
  const order = bodies.slice().sort((a, b) => a.depth - b.depth || a.index - b.index);
  for (const b of order) {
    const lit = light(b.depth);
    const fade = far(b.depth);
    const ramps = b.kind === "hub" ? HUB_RAMP : b.working ? WORK_RAMP : idleRamp(b.key);
    const R = b.r;
    const x0 = Math.floor(b.x - R - 1);
    const x1 = Math.ceil(b.x + R + 1);
    const y0 = Math.floor(b.y - R - 1);
    const y1 = Math.ceil(b.y + R + 1);
    for (let y = y0; y <= y1; y += 1) {
      for (let x = x0; x <= x1; x += 1) {
        if (x < 0 || y < 0 || x >= W || y >= H) continue;
        const dx = x + 0.5 - b.x;
        const dy = y + 0.5 - b.y;
        const d = Math.hypot(dx, dy);
        const cov = clamp(R + 0.5 - d, 0, 1);
        if (cov <= 0) continue;
        const nx = dx / R;
        const ny = dy / R;
        const nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
        const lam = Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]);
        let c = ramp(ramps, 0.06 + 0.94 * lam ** 0.85);
        if (b.kind === "side") c = ramp(IDLE_RAMP, 0.06 + 0.94 * lam ** 0.85);
        if (!b.working && b.kind !== "hub") {
          const rim = clamp((Math.hypot(nx, ny) - 0.8) / 0.2, 0, 1); // a faint lighter rim on a resting ball
          c = mix(c, MUTED, rim * 0.1);
        }
        c = mix(c, BG, 1 - lit);
        c = mix(c, BG, fade);
        px.blend(x, y, c, cov);
        if (cov >= 0.5) owner[y * W + x] = b.index;
      }
    }
    // the core: a bright dot for the hub and the working, a soft lilac one for a resting ball
    const coreR = b.kind === "hub" ? Math.max(1, R * 0.17) : Math.max(0.6, R * 0.14);
    const coreC = b.kind === "hub" ? CORE_HUB : b.working ? [255, 244, 232] : mix(idleRamp(b.key)[3], MUTED, 0.4);
    if (R >= 3.2 || b.kind === "hub") {
      for (let y = Math.floor(b.y - coreR - 1); y <= Math.ceil(b.y + coreR + 1); y += 1) {
        for (let x = Math.floor(b.x - coreR - 1); x <= Math.ceil(b.x + coreR + 1); x += 1) {
          if (x < 0 || y < 0 || x >= W || y >= H) continue;
          const d = Math.hypot(x + 0.5 - b.x, y + 0.5 - b.y);
          const a = clamp(1 - d / (coreR + 0.9), 0, 1) ** 0.8 * (b.working || b.kind === "hub" ? 0.95 : 0.55) * lit;
          if (a > 0) px.blend(x, y, mix(coreC, BG, fade), a);
        }
      }
    }
    // needs the user: a small coral dot at the top left
    if (b.needs) {
      const ox = b.x - R * 0.78;
      const oy = b.y - R * 0.78;
      const dr = Math.max(1.2, R * 0.28);
      for (let y = Math.floor(oy - dr - 1.5); y <= Math.ceil(oy + dr + 1.5); y += 1) {
        for (let x = Math.floor(ox - dr - 1.5); x <= Math.ceil(ox + dr + 1.5); x += 1) {
          const d = Math.hypot(x + 0.5 - ox, y + 0.5 - oy);
          px.blend(x, y, CORAL, clamp(dr + 0.5 - d, 0, 1));
        }
      }
    }
  }

  // ---- text over the picture: names and the pill
  const text = new Array(w * h).fill(null); // { ch, fg, bold, bg? }
  const putText = (c, r, str, fg, bold, bg) => {
    let col = c;
    for (const ch of str) {
      const cw = charWidth(ch);
      if (cw === 0) continue;
      if (col >= 0 && col + cw <= w && r >= 0 && r < h) {
        text[r * w + col] = { ch, fg, bold: !!bold, bg };
        if (cw === 2) text[r * w + col + 1] = { ch: "", fg, bold: !!bold, bg };
      }
      col += cw;
    }
  };

  // the cells a ball covers (its ring included): names and the pill keep off them
  const canvas = new Canvas(w, h, new Styles());
  for (const b of order) {
    const reach = b.r + 0.6 + 0.5;
    for (let r = Math.max(0, Math.floor((b.y - reach) / 2)); r <= Math.min(h - 1, Math.floor((b.y + reach) / 2)); r += 1) {
      for (let c = Math.max(0, Math.floor(b.x - reach)); c <= Math.min(w - 1, Math.floor(b.x + reach)); c += 1) {
        if (Math.hypot(c + 0.5 - b.x, 2 * r + 1 - b.y) <= reach + 0.5) {
          canvas.plotBody(c, r, " ", 0, b.depth);
        }
      }
    }
  }
  // the hub's name (the header line says it; it is not drawn in the picture) and the pill
  const hubName = truncate(clean(L.hub && L.hub.name) || "main", NAME_MAX + 4);
  const act = L.hub && isObject(L.hub.activity) ? L.hub.activity : null;
  let pillParts = [];
  let zone = null;
  if (hubWork && act && (act.kind === "tool" || act.kind === "reasoning" || act.kind === "text" || typeof act.zone === "string")) {
    const d = describe(act);
    zone = d.zone;
    pillParts = [d.glyph, d.verb, d.target, PEACH, PEACH];
  } else if (hubWork) {
    pillParts = ["✦", "working", "", PEACH, PEACH];
  } else {
    pillParts = ["☾", "resting", "", MUTED, MUTED];
  }
  const maxPill = Math.max(0, Math.min(w - 3, 46));
  let pill = null;
  {
    let [glyph, verb, target, glyphColor, verbColor] = pillParts;
    const compose = (g, v, tg) => " " + (g ? g + " " : "") + v + (tg ? " " + tg : "") + " ";
    let tg = target;
    let g = glyph;
    let inner = compose(g, verb, tg);
    if (displayWidth(inner) + 2 > maxPill) {
      const roomT = maxPill - 2 - displayWidth(compose(g, verb, ""));
      tg = roomT >= 5 ? truncate(target, roomT - 1) : "";
      inner = compose(g, verb, tg);
    }
    if (displayWidth(inner) + 2 > maxPill) {
      g = "";
      inner = compose(g, verb, tg);
    }
    if (displayWidth(inner) + 2 > maxPill) {
      verb = truncate(verb, Math.max(1, maxPill - 4));
      inner = compose(g, verb, "");
      tg = "";
    }
    if (maxPill >= 5) pill = { g, glyphColor, verb, verbColor, tg, width: displayWidth(inner) + 2 };
  }
  const info = { hub: { x: hubP.x, y: hubP.y, r: hubR, name: hubName }, balls: [], labels: [], pill: null, zone, owner: null };
  const reserve = (c0, c1, r) => {
    for (let c = c0 - 1; c <= c1 + 1; c += 1) canvas.put(c, r, " ", 0);
  };
  // the pill: always on row 0, directly under the header line, from column 1 (the header's own left edge)
  const pillW = pill ? pill.width : 0;
  if (pill) {
    const pr = 0;
    const c0 = 1;
    const tint = mix(BG, PEACH, 0.14);
    let col = c0 + 1;
    const put = (str, fg, bold) => {
      for (const ch of str) {
        putText(col, pr, ch, fg, bold, tint);
        col += charWidth(ch);
      }
    };
    put(" ", INK);
    if (pill.g) {
      put(pill.g, pill.glyphColor, true);
      put(" ", INK);
    }
    put(pill.verb, pill.verbColor, true);
    if (pill.tg) {
      put(" ", INK);
      put(pill.tg, INK);
    }
    put(" ", INK);
    text[pr * w + c0] = { ch: "▐", fg: tint, bold: false, bg: null, cap: true };
    text[pr * w + c0 + pillW - 1] = { ch: "▌", fg: tint, bold: false, bg: null, cap: true };
    reserve(c0, c0 + pillW - 1, pr);
    info.pill = { c0, c1: c0 + pillW - 1, r: pr, text: (pill.g ? pill.g + " " : "") + pill.verb + (pill.tg ? " " + pill.tg : "") };
  }

  // ---- the other names, placed as the orbit places them
  const labelBodies = [];
  const cellOf = (b) => ({ col: Math.floor(b.x), row: Math.floor(b.y / 2), depth: b.depth });
  const tiny = w < 44 || h < 16;
  for (const b of bodies) {
    if (b.kind === "hub") continue;
    if (tiny && (b.kind !== "planet" || (!b.working && !b.needs))) continue; // a small picture names the working and the waiting only
    const q = cellOf(b);
    if (b.kind === "planet") {
      const rank = b.needs ? 0 : b.working ? 1 : 2;
      labelBodies.push({ id: b.id, kind: "planet", q, rank, name: b.p.label, key: b.key, p: b.p, k: b.index, body: b });
    } else if (b.kind === "moon") {
      labelBodies.push({ id: b.id, kind: "moon", q, rank: b.needs ? 0 : b.busy ? 1 : 2, name: truncate(clean(seatName(b.seat.agent.name)) || "?", NAME_MAX), key: b.key, seat: b.seat, busy: b.busy, body: b });
    } else {
      labelBodies.push({ id: b.id, kind: "side", q, rank: b.busy ? 1 : 2, name: truncate(SIDE_NAME, NAME_MAX), key: "side", shown: 1, ball: { x: b.x, y: b.y }, body: b });
    }
  }
  const trace = [];
  const Lc = Object.assign({}, L, { w, h });
  const hubBall = { x: hubP.x * 2, y: hubP.y * 2, rx: hubR * 2, ry: hubR * 2 };
  placeBodies(Lc, canvas, new Styles(), P, labelBodies, { cam, frame: 0, blinkOn: false, muted: 0, hubBall, trace, noGlyphs: true, memoryKey: "planets:" });
  const byId = new Map(bodies.map((b) => [b.id, b]));
  for (const e of trace) {
    if (e.kind !== "label") continue;
    const b = byId.get(e.id);
    if (!b) continue;
    const strong = b.working || b.needs;
    const hidden = owner[Math.min(H - 1, Math.max(0, Math.floor(b.y))) * W + Math.min(W - 1, Math.max(0, Math.floor(b.x)))] !== b.index;
    let fg = strong ? INK : IDLE;
    if (hidden || b.depth < -0.4) fg = mix(fg, BG, 0.35);
    let onBall = false;
    for (let c = e.col; c < e.col + e.width && !onBall; c += 1) {
      for (const y of [2 * e.row, 2 * e.row + 1]) if (c >= 0 && c < W && y >= 0 && y < H && owner[y * W + c] >= 0) onBall = true;
    }
    if (onBall) continue; // a name is never drawn over a ball
    putText(e.col, e.row, e.text, fg, false, BG);
    info.labels.push({ id: e.id, text: e.text, c0: e.col, c1: e.col + e.width - 1, r: e.row });
  }
  info.balls = order.map((b) => ({ id: b.id, kind: b.kind, x: b.x, y: b.y, r: b.r, depth: b.depth, working: b.working, needs: b.needs, index: b.index }));
  info.owner = (x, y) => {
    if (x < 0 || y < 0 || x >= W || y >= H) return null;
    const k = owner[Math.floor(y) * W + Math.floor(x)];
    return k < 0 ? null : bodies[k].id;
  };

  // ---- the lines: two pixels a cell
  const lines = [];
  for (let r = 0; r < h; r += 1) {
    const spans = [];
    let cur = null;
    const push = (ch, fg, bg, bold) => {
      const f = fg ? hex(fg) : undefined;
      const g = hex(bg);
      if (cur && cur.bg === g && cur.color === f && !!cur.bold === !!bold) cur.text += ch;
      else {
        cur = { text: ch, tone: "plain", bg: g };
        if (f) cur.color = f;
        if (bold) cur.bold = true;
        spans.push(cur);
      }
    };
    for (let c = 0; c < w; c += 1) {
      const top = px.get(c, 2 * r);
      const bot = px.get(c, 2 * r + 1);
      const tx = text[r * w + c];
      if (tx) {
        if (tx.ch === "") continue;
        if (tx.cap) {
          // the pill's ends: half a cell of its tint against the picture
          push(tx.ch, tx.fg, mix(top, bot, 0.5), false);
        } else push(tx.ch, tx.fg, tx.bg || mix(top, bot, 0.5), tx.bold);
        continue;
      }
      if (Math.round(top[0]) === Math.round(bot[0]) && Math.round(top[1]) === Math.round(bot[1]) && Math.round(top[2]) === Math.round(bot[2])) push(" ", null, top, false);
      else push("▀", top, bot, false);
    }
    lines.push(spans);
  }
  return { lines, info };
}

function blankLines(w, h) {
  const row = w > 0 ? [{ text: " ".repeat(w), tone: "plain", bg: hex(BG) }] : [];
  return Array.from({ length: h }, () => row.map((s) => ({ ...s })));
}

// The picture alone, `w` x `h` cells: exactly `h` lines of exactly `w` cells. Never throws.
export function planetsLines(doc, w, h, now = 0, options = {}) {
  w = Math.max(0, Math.floor(Number(w) || 0));
  h = Math.max(0, Math.floor(Number(h) || 0));
  try {
    if (w < 6 || h < 4) return blankLines(w, h);
    return buildPlanets(isObject(doc) ? doc : {}, w, h, Number.isFinite(Number(now)) ? Number(now) : 0, isObject(options) ? options : {}).lines;
  } catch (_) {
    return blankLines(w, h);
  }
}

// Where the balls, names and pill stand (cells and pixels), for the tests. Null for a size too small to draw.
export function planetsInfo(doc, w, h, now = 0, options = {}) {
  w = Math.max(0, Math.floor(Number(w) || 0));
  h = Math.max(0, Math.floor(Number(h) || 0));
  if (w < 6 || h < 4) return null;
  return buildPlanets(isObject(doc) ? doc : {}, w, h, Number.isFinite(Number(now)) ? Number(now) : 0, isObject(options) ? options : {}).info;
}

// The next frame in ms: 0 when nothing moves (no document, or reduced motion), else the orbit's 3D pace.
export function planetsInterval(doc, options = {}, now = 0) {
  if (!isObject(doc)) return 0;
  const o = isObject(options) ? options : {};
  if (o.motion === "reduced") return 0;
  return frameInterval(doc, Object.assign({}, o, { view: "3d" }), now);
}


// The whole picture panel, exactly `height` lines of `width` cells: a header row (the main chat's name, and in coral how many
// agents need the user), the picture, and the keys on the last row. No agents, a loading document or an error is the orbit's
// own message. options: { width, height, now, motion, colors, keys, orbit }. Never throws.
export function planetsScene(snapshot, options = {}) {
  const o = isObject(options) ? options : {};
  const width = Math.max(0, Math.floor(Number(o.width) || 0));
  const height = Math.max(0, Math.floor(Number(o.height) || 0));
  if (!height) return [];
  try {
    const agents = isObject(snapshot) && (snapshot.error === undefined || snapshot.error === null) ? validAgents(snapshot) : [];
    if (!agents.length) return orbitScene(snapshot, o);
    const now = Number.isFinite(Number(o.now)) ? Number(o.now) : 0;
    const bg = hex(BG);
    const sp = (text, color, bold) => {
      const span = { text, tone: "plain", bg };
      if (color) span.color = hex(color);
      if (bold) span.bold = true;
      return span;
    };
    const fit = (line) => {
      const clipped = clipLine(line, width);
      const used = lineWidth(clipped);
      return used < width ? [...clipped, sp(" ".repeat(width - used))] : clipped;
    };
    const hub = findHub(snapshot, agents);
    const working = !!hub && hubWorking(snapshot, hub);
    const name = clean(hub ? hub.name : isObject(snapshot.root) ? snapshot.root.agent : "") || "main";
    const needing = needsEntries(snapshot, new Set(agents.map((a) => a.name))).length;
    const right = needing ? `${needing} ${needing === 1 ? "needs" : "need"} you` : "";
    const gap = width - 3 - displayWidth(name) - (right ? displayWidth(right) + 2 : 0);
    const head = [sp(" "), sp("◉", working ? PEACH : IDLE), sp(" "), sp(truncate(name, Math.max(1, width - 4 - (right ? displayWidth(right) + 2 : 0))), INK, true)];
    if (right && gap >= 1) head.push(sp(" ".repeat(gap)), sp(right, CORAL), sp(" "));
    const out = [fit(head)];
    const keys = Array.isArray(o.keys) && o.keys.length ? o.keys : null;
    const picture = height - 1 - (keys ? 1 : 0);
    if (picture > 0) out.push(...planetsLines(snapshot, width, picture, now, o));
    if (keys) {
      const row = [sp(" ")];
      let used = 1;
      for (const pair of keys) {
        if (!Array.isArray(pair) || pair.length < 2) continue;
        const k = String(pair[0]);
        const l = String(pair[1]);
        const need = (row.length > 1 ? 2 : 0) + displayWidth(k) + 1 + displayWidth(l);
        if (used + need > width) break;
        if (row.length > 1) row.push(sp("  "));
        row.push(sp(k, INK), sp(" " + l, IDLE));
        used += need;
      }
      out.push(fit(row));
    }
    return out.slice(0, height).map(fit);
  } catch (_) {
    return blankLines(width, height);
  }
}
