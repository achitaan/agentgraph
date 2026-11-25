/*
 * The sidebar and panel plugin, written against injected host functions so it can be tested without OpenCode.
 * src/tui.js imports the real ones (@opentui/solid, solid-js, node:*) and default-exports createPlugin(...).
 *
 * One slot on "sidebar.content" (replace by default, or prepend). Every 4 s it runs the snapshot command for
 * the slot's session; a result that arrives after the session changed is dropped. Nothing here throws into
 * the host: a failure is drawn as one line of text.
 *
 * On hosts with session panels it also contributes the panel "agentgraph.graph" (same view, drawn at the panel's
 * width) and the command "agentgraph.graph" (slash /graph, alias /agents) that opens and closes it. Every host
 * function is feature-detected; a host without them gets the sidebar only.
 *
 * The panel opens on a calm plain list (who needs you, who is at work and what they are doing, who is idle). `g`
 * switches to a picture (the planets, src/planets.js) and back, `o` between the planets and the orbit (src/scene.js), each from then on
 * remembered while the app runs, and neither ever makes the panel full screen (only the user's `f` does, and the host on a narrow terminal);
 * the picture is drawn at whatever size the panel has down to 36 columns by 14 rows, and a smaller panel has only the list;
 * its size is measured from the panel's own box (OpenTUI `onSizeChange`,
 * `width`, `height`), because the documented panel input has no height. On the orbit, `d` switches it between its 2D view
 * (the ring from above, braille dots) and its 3D view (a turning figure in depth characters). On the 3D view ←/→ turn
 * it round its upright axis, ↑/↓ change the elevation and `0` resets, named on its bottom row; a click on the orbit
 * switches the views as `d` does and a drag turns the 3D view (OpenTUI mouse events). In the list, ↑/↓ select
 * a row, Enter opens that row's session (`ui.router.navigate`), `s` stops a working agent (`client.session.interrupt`,
 * after `ui.dialog.confirm`) and `m` sends it a message (`ui.dialog.prompt`, `client.session.prompt`); each key exists
 * only when the host has the call it needs. Pending permission requests and questions come from `ctx.data`.
 * `r` runs a drawn path (snapshot `structure.flows`): the host's chooser (`ui.dialog.select`), the goal
 * (`ui.dialog.prompt`), the run message from `agentgraph flow prompt ID --goal-file -` (src/runner.js), sent to the
 * orchestrator's session (the snapshot's root session) as a user message (`client.session.prompt`).
 *
 * AGENTGRAPH_TUI_DEMO=1 in the TUI's environment draws a moving demo document instead of running Python;
 * AGENTGRAPH_TUI_DEMO=paths draws it at rest with sample paths (src/demo.js). The demo never sends a path.
 */
import { render, layout, isAnimated, sideBusy, truncate as truncateText, flowLabel, flowName, flowStatsText, clipLine } from "./render.js";
import {
  scene, sceneFits, frameInterval, changeAt, orbitTurned, orbitReset, TURN_STEP, ELEVATION_STEP, DRAG_TURN_PER_CELL, DRAG_ELEVATION_PER_ROW,
  finishedRuns, STREAK_MS, STREAK_GAP_MS,
} from "./scene.js";
import { planetsScene, planetsInterval } from "./planets.js";
import { createSnapshotRunner, createChatRunner, createSideAskRunner, createLoopRunner, errorDocument, errorMessage, oneLine, runFlowPrompt, GOAL_LIMIT, SIDE_QUESTION_LIMIT } from "./runner.js";
import { chatLines, chatWindow, chatHeading, cleanChat, CHAT_MIN_INLINE } from "./chat.js";
import { createDemoRunner, DEMO_INTERVAL_MS } from "./demo.js";

export const PLUGIN_ID = "agentgraph-tui";
export const SLOT = "sidebar.content";
export const PANEL_SLOT = "session.panel";
export const PANEL_NAME = "agentgraph.graph";
export const WIDTH = 37; // the host's sidebar is 42 columns wide, about 37 usable
export const PANEL_MAX_WIDTH = 100; // the panel's text never stretches wider than this
export const DEFAULT_INTERVAL_MS = 4000;
export const MIN_INTERVAL_MS = 1000;
export const SPINNER_MS = 200;
export const CHAT_POLL_MS = 2000; // a busy session's chat is read this often; an idle one once
export const DEFAULT_PYTHON = "/Users/achita.anantachina/.local/state/tec/toolchain/base_profile/bin/python3";
const TERRACOTTA_HUE = 18;
const BOLD = 1; // OpenTUI TextAttributes.BOLD

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() !== "";
}

// ---------------------------------------------------------------------------------------------------------------
// Options

export function resolveOptions(raw, { os, path }) {
  const options = isPlainObject(raw) ? raw : {};
  const expandHome = (value) => (value === "~" || value.startsWith("~/") ? path.join(os.homedir(), value.slice(1)) : value);
  const home = nonEmptyString(options.home)
    ? path.resolve(expandHome(options.home.trim()))
    : path.join(os.homedir(), ".config", "opencode", "orchestration");
  const python = nonEmptyString(options.python) ? expandHome(options.python.trim()) : DEFAULT_PYTHON;
  const requested = Number(options.intervalMs);
  const intervalMs = Number.isFinite(requested) && requested > 0 ? Math.max(MIN_INTERVAL_MS, Math.floor(requested)) : DEFAULT_INTERVAL_MS;
  const mode = options.mode === "prepend" ? "prepend" : "replace";
  // reducedMotion: the 3D sky without shooting stars or fades (scene.js `motion: "reduced"`)
  // picture: "off" (the default: the panel opens on the plain list; `g` shows a picture), "planets" or "orbit" (the panel
  // opens on that picture, still switchable to the list with `g`)
  const picture = options.picture === "orbit" ? "orbit" : options.picture === "planets" ? "planets" : "off";
  return { home, python, intervalMs, mode, demo: options.demo === true, reducedMotion: options.reducedMotion === true, picture };
}

// ---------------------------------------------------------------------------------------------------------------
// Theme -> colours for the four tones. Never green for "accent".

// A colour token: a colour, or a token object holding one (`default`, or `base` as OpenCode 2.3 themes have, or the
// 200 step of a hue scale).
function pick(value) {
  if (value === undefined || value === null) return undefined;
  if (isPlainObject(value) && !("r" in value)) {
    if ("default" in value) return pick(value.default);
    if ("base" in value) return pick(value.base);
    if ("200" in value) return pick(value[200]);
  }
  return value;
}

function dig(value, ...keys) {
  let v = value;
  for (const key of keys) {
    if (!v || typeof v !== "object") return undefined;
    v = v[key];
  }
  return v;
}

export function toRgb(color) {
  if (typeof color === "string") {
    let hex = color.trim().replace(/^#/, "");
    if (/^[0-9a-f]{3}$/i.test(hex)) hex = hex.split("").map((c) => c + c).join("");
    if (/^[0-9a-f]{6}([0-9a-f]{2})?$/i.test(hex)) {
      return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
    }
    return null;
  }
  if (color && typeof color === "object") {
    const r = Number(color.r), g = Number(color.g), b = Number(color.b);
    if ([r, g, b].every(Number.isFinite)) {
      const scale = r <= 1 && g <= 1 && b <= 1 ? 255 : 1;
      return [r * scale, g * scale, b * scale];
    }
  }
  return null;
}

// The value handed to the host's `fg`: always "#rrggbb", from a "#rgb", "#rrggbb" or "#rrggbbaa" string (alpha
// dropped) or any { r, g, b } colour (OpenTUI RGBA, 0-1 or 0-255); anything else undefined (the host's default text
// colour). The host's `fg` setter accepts only strings and its own RGBA class; any other object throws inside
// OpenTUI, and a named or functional colour string would be up to the host to understand.
export function colorProp(value) {
  let rgb = null;
  if (typeof value === "string") rgb = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(value.trim()) ? toRgb(value) : null;
  else if (value && typeof value === "object") rgb = toRgb(value);
  if (!rgb) return undefined;
  return "#" + rgb.map((c) => Math.max(0, Math.min(255, Math.round(c))).toString(16).padStart(2, "0")).join("");
}

function hsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const l = (max + min) / 2;
  const d = max - min;
  if (d === 0) return { h: 0, s: 0, l };
  const s = d / (1 - Math.abs(2 * l - 1));
  let h;
  if (max === r) h = ((g - b) / d) % 6;
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  h *= 60;
  if (h < 0) h += 360;
  return { h, s, l };
}

export function isGreen(color) {
  const rgb = toRgb(color);
  if (!rgb) return false;
  const { h, s } = hsl(rgb);
  return s >= 0.2 && h >= 70 && h <= 170;
}

function hueDistance(color) {
  const rgb = toRgb(color);
  if (!rgb) return 400; // unknown: after every known colour
  const { h, s } = hsl(rgb);
  const d = Math.min(Math.abs(h - TERRACOTTA_HUE), 360 - Math.abs(h - TERRACOTTA_HUE));
  return d + (s < 0.15 ? 180 : 0); // greys are far from terracotta
}

// -> { plain, muted, accent, danger }: each { fg, bold }
// Two theme shapes: OpenCode 2.3 (`text.base`, `text.muted`, `text.feedback.{warning,error}.base`, `hue.accent`)
// and the older flat one (`text.default`, `text.muted`/`subdued`, `warning`, `accent`, `error`).
export function themeTones(theme) {
  const t = theme && typeof theme === "object" ? theme : {};
  const text = t.text;
  const plain = pick(text) ?? pick(t.foreground);
  const muted = (text && (pick(text.muted) ?? pick(text.subdued))) ?? pick(t.textMuted) ?? plain;
  const warning = pick(t.warning) ?? pick(dig(text, "feedback", "warning"));
  const accentColor = pick(t.accent) ?? pick(dig(t, "hue", "accent"));
  const candidates = [warning, accentColor].filter((c) => c !== undefined && !isGreen(c));
  candidates.sort((a, b) => hueDistance(a) - hueDistance(b));
  const accent = candidates.length ? { fg: candidates[0], bold: false } : { fg: plain, bold: true };
  const errorColor = pick(t.error) ?? pick(t.danger) ?? pick(t.critical) ?? pick(dig(text, "feedback", "error"));
  const danger = errorColor !== undefined && !isGreen(errorColor) ? { fg: errorColor, bold: false } : { fg: accent.fg, bold: true };
  return { plain: { fg: plain, bold: false }, muted: { fg: muted, bold: false }, accent, danger };
}

// The orbit's colours from the theme: { dark, muted }. The background decides dark or light (dark unless it is
// clearly light, or unknown); the muted colour dims resting agents and far parts.
export function sceneColors(theme, tones) {
  let dark = true;
  try {
    const t = theme && typeof theme === "object" ? theme : {};
    const b = t.background;
    const bg = b && typeof b === "object" && !("r" in b) && !b.buffer ? b.base ?? b.default ?? b.panel : b;
    const rgb = toRgb(colorProp(bg) || colorProp(t.bg) || colorProp(t.backgroundPanel));
    if (rgb) dark = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] < 128;
  } catch (_) {
    dark = true;
  }
  return { dark, muted: colorProp(tones && tones.muted && tones.muted.fg) || (dark ? "#808080" : "#8c8c8c") };
}

// ---------------------------------------------------------------------------------------------------------------
// Session id from the slot props, else from the router (as the installed example does).

function routeSession(ctx) {
  try {
    const route = ctx.ui && ctx.ui.router && typeof ctx.ui.router.current === "function" ? ctx.ui.router.current() : undefined;
    if (!route || typeof route !== "object") return undefined;
    if (route.name === "session" && route.params && typeof route.params.sessionID === "string") return route.params.sessionID;
    if (route.type === "session" && typeof route.sessionID === "string") return route.sessionID;
  } catch (_) {
    /* no router */
  }
  return undefined;
}

// The panel's text width: follows panel.width, never narrower than the sidebar, never wider than the cap.
export function panelWidth(panel) {
  let width;
  try {
    width = Math.floor(Number(panel && panel.width));
  } catch (_) {
    width = NaN;
  }
  if (!Number.isFinite(width)) return WIDTH;
  return Math.min(PANEL_MAX_WIDTH, Math.max(WIDTH, width));
}

// ---------------------------------------------------------------------------------------------------------------

// The demo switch: AGENTGRAPH_TUI_DEMO set to anything but "" or "0".
export function demoRequested(env) {
  const value = env && typeof env === "object" ? env.AGENTGRAPH_TUI_DEMO : undefined;
  return typeof value === "string" && value.trim() !== "" && value.trim() !== "0";
}

// The size of a measured OpenTUI node, or null while it has not been laid out.
export function measuredSize(node) {
  try {
    const width = Math.floor(Number(node && node.width));
    const height = Math.floor(Number(node && node.height));
    return width > 0 && height > 0 ? { width, height } : null;
  } catch (_) {
    return null;
  }
}

function lineKey(line) {
  let key = "";
  for (const span of line) key += span.tone + "\u0001" + (span.color || "") + "\u0001" + (span.bg || "") + (span.bold ? "b" : "") + "\u0001" + span.text + "\u0002";
  return key;
}

// host: { createElement, insert, setProp, createSignal, onCleanup?, spawn, fs, os, path, env, timers?, now?, demoFile? }
export function createPlugin(host) {
  const timers = host.timers || { setInterval, clearInterval };
  const clock = () => {
    try {
      const t = typeof host.now === "function" ? Number(host.now()) : Date.now();
      return Number.isFinite(t) ? t : Date.now();
    } catch (_) {
      return Date.now();
    }
  };

  function element(tag, props, children = []) {
    const node = host.createElement(tag);
    for (const [key, value] of Object.entries(props)) if (value !== undefined) host.setProp(node, key, value);
    for (const child of children) if (child !== null && child !== undefined && child !== false) host.insert(node, child);
    return node;
  }

  function lineNode(line, tones) {
    if (!line.length) return element("text", { wrapMode: "none", fg: colorProp(tones.muted.fg) }, [" "]);
    return element(
      "box",
      { flexDirection: "row", width: "100%", flexShrink: 0 },
      line.map((span) => {
        const tone = tones[span.tone] || tones.plain;
        const own = span.color ? colorProp(span.color) : undefined; // the orbit's group colours
        const bg = span.bg ? colorProp(span.bg) : undefined; // the styled list's bar and selected row (dark themes)
        return element("text", { fg: own || colorProp(tone.fg), bg, attributes: span.bold || (!own && tone.bold) ? BOLD : undefined, wrapMode: "none", flexShrink: 0 }, [span.text]);
      }),
    );
  }

  // An orbit line: one `text`, never selected with the mouse (a press and a drag there turn the 3D view instead),
  // whose spans carry the colours. A span is not a renderable of its own, so the mouse lands on the line's `text`;
  // the painter keeps that node while the orbit shows and swaps only its spans (sceneSpans, then host.insert with
  // the old spans as the current children). OpenTUI finds the target of a press in the hit grid of its last paint
  // and drops the press when that renderable has been destroyed since (hitTest gives 0); a line replaced between
  // two paints therefore swallowed a click that came in between, as during the 2D/3D morph, which redraws every row.
  function sceneSpans(line, tones) {
    const spans = line.length ? line : [{ text: " ", tone: "muted" }];
    return spans.map((span) => {
      const tone = tones[span.tone] || tones.plain;
      const own = span.color ? colorProp(span.color) : undefined; // the orbit's group colours
      const fg = own || colorProp(tone.fg);
      const bg = span.bg ? colorProp(span.bg) : undefined; // the pictures' half-block pixels: top in fg, bottom in bg
      const bold = span.bold || (!own && tone.bold);
      // a span takes its colour from `style`; OpenTUI ignores a span's `fg` property, kept for the tests' colour checks
      return element("span", Object.assign({ fg }, bg ? { bg } : {}, { style: Object.assign({ fg }, bg ? { bg } : {}, bold ? { bold: true } : {}) }), [span.text]);
    });
  }
  function sceneRow(spans) {
    return element("text", { wrapMode: "none", flexShrink: 0, selectable: false }, spans);
  }

  // Last resort when drawing failed: one line of text with no colour or other property that could fail again.
  function plainLine(message, width) {
    return element("text", { wrapMode: "none" }, [truncateText(oneLine(message) || "agent graph: unknown error", width)]);
  }

  // Optional debug log of swallowed errors: AGENTGRAPH_TUI_DEBUG=<file> in the TUI's environment.
  function report(where, error) {
    try {
      const file = host.env && host.env.AGENTGRAPH_TUI_DEBUG;
      if (file && host.fs && typeof host.fs.appendFileSync === "function") {
        host.fs.appendFileSync(file, `${new Date().toISOString()} ${where}: ${(error && error.stack) || errorMessage(error)}\n`);
      }
    } catch (_) {
      /* ignore */
    }
  }

  // The real path of `dir` when it is an existing folder inside (not equal to) the system temp folder, else "".
  function scratchHome(dir) {
    try {
      if (typeof dir !== "string" || !dir.trim() || !host.path.isAbsolute(dir.trim())) return "";
      const temp = host.fs.realpathSync(host.os.tmpdir());
      const real = host.fs.realpathSync(dir.trim());
      return real.startsWith(temp + host.path.sep) && host.fs.statSync(real).isDirectory() ? real : "";
    } catch (_) {
      return "";
    }
  }

  function setup(ctx, secondOptions) {
    ctx = ctx || {};
    const options = resolveOptions(isPlainObject(ctx.options) ? ctx.options : secondOptions, host);
    const demo = demoRequested(host.env);
    // For checks on a throwaway host, which loads the plugin from the user's config and so cannot give it options:
    // AGENTGRAPH_TUI_HOME is the orchestration folder for the snapshot and `flow prompt` (a scratch copy with its own
    // structure/), honoured only for an existing folder inside the system temp folder (resolved through links), so
    // the environment cannot point the panel at code anywhere else. AGENTGRAPH_TUI_NOSEND: `r` stops after the run
    // message, before the send (it can only prevent a send).
    const envHome = scratchHome(host.env && host.env.AGENTGRAPH_TUI_HOME);
    if (envHome) options.home = envHome;
    const noSend = !!host.env && typeof host.env.AGENTGRAPH_TUI_NOSEND === "string" && !["", "0"].includes(host.env.AGENTGRAPH_TUI_NOSEND.trim());
    if (envHome && options.home === envHome) report("mode", `home ${envHome} (AGENTGRAPH_TUI_HOME)`); // debug log only
    if (noSend) report("mode", "nosend: r stops before the send (AGENTGRAPH_TUI_NOSEND)");
    if (demo) options.intervalMs = Math.min(options.intervalMs, DEMO_INTERVAL_MS);
    const runner = demo
      ? createDemoRunner({ fs: host.fs, file: host.demoFile, now: clock, paths: String(host.env.AGENTGRAPH_TUI_DEMO).trim() === "paths", side: String(host.env.AGENTGRAPH_TUI_DEMO).trim() === "side" })
      : createSnapshotRunner({
          spawn: host.spawn,
          fs: host.fs,
          path: host.path,
          env: host.env,
          home: options.home,
          python: options.python,
          demo: options.demo,
        });

    // The loop runs of the project (`agentgraph loop list --json`), added to each snapshot as `doc.loops`; read only when the
    // project has run a loop (a `.opencode/loops` folder), so most projects spawn nothing.
    const loopRunner = createLoopRunner({ spawn: host.spawn, fs: host.fs, path: host.path, env: host.env, home: options.home, python: options.python, demo: demo || options.demo });

    const requestRender = () => {
      try {
        if (ctx.renderer && typeof ctx.renderer.requestRender === "function") ctx.renderer.requestRender();
      } catch (_) {
        /* ignore */
      }
    };

    const directory = () => {
      try {
        return (ctx.location && ctx.location.directory) || undefined;
      } catch (_) {
        return undefined;
      }
    };

    let disposed = false;
    const watchers = [];

    // The chat of the selected agent: `agentgraph chat` (src/runner.js), read only while the panel's list shows it for
    // the selected row, one read at a time, once when its session is not busy and every CHAT_POLL_MS while it is.
    // `want(session, busy)` is called from the view (it changes no signal itself); `sent` is the `m` key's success.
    const chatRunner = createChatRunner({
      spawn: host.spawn,
      fs: host.fs,
      path: host.path,
      env: host.env,
      home: options.home,
      python: options.python,
      demo: demo || options.demo,
      onSpawn: (argv) => report("chat", argv.join(" ")), // debug log only
    });
    // The side chat's question (`a` on its row): one in flight; `asking` draws the row as working until the answer is in.
    const sideRunner = createSideAskRunner({
      spawn: host.spawn,
      fs: host.fs,
      path: host.path,
      env: host.env,
      home: options.home,
      python: options.python,
      demo: demo || options.demo,
      onSpawn: (argv) => report("sidechat", argv.join(" ")), // debug log only
    });
    const [asking, setAsking] = host.createSignal(false);
    const SENT_KEEP_MS = 60000; // a sent message the feed has not shown yet is drawn as pending for this long
    const [chatState, setChatState] = host.createSignal({ session: null, feed: null, error: null, extras: [] });
    const chatFeed = (() => {
      let session = null; // the session wanted now
      let busyHint = false; // the snapshot says that session is busy
      let generation = 0;
      let inflight = false;
      let again = false; // a read was asked for while one ran
      let timer = null;
      const busyNow = () => {
        const s = chatState();
        return busyHint || (s.session === session && !!s.feed && s.feed.busy === true);
      };
      function read() {
        if (disposed || !session) return Promise.resolve();
        if (inflight) {
          again = true;
          return Promise.resolve();
        }
        const asked = session;
        const gen = generation;
        inflight = true;
        return chatRunner
          .chat({ session: asked, directory: directory() })
          .catch((error) => ({ error: errorMessage(error) }))
          .then((res) => {
            if (disposed || gen !== generation || asked !== session) return; // late result: drop it
            const before = chatState();
            const extras = before.extras.filter((e) => e.session === asked);
            if (res && res.feed) setChatState({ session: asked, feed: res.feed, error: null, extras });
            else setChatState({ session: asked, feed: before.session === asked ? before.feed : null, error: (res && res.error) || "The chat could not be read.", extras });
            requestRender();
          })
          .finally(() => {
            inflight = false;
            if (again && session && !disposed) {
              again = false;
              read();
            }
          });
      }
      function stopTimer() {
        if (timer) timers.clearInterval(timer);
        timer = null;
      }
      return {
        want(id, busy) {
          busyHint = !!busy;
          if ((id || null) === session) return;
          session = id || null;
          generation += 1;
          again = false;
          if (!session) {
            stopTimer();
            return;
          }
          if (!timer && !disposed) {
            timer = timers.setInterval(() => {
              if (session && !inflight && busyNow()) read();
            }, CHAT_POLL_MS);
          }
          Promise.resolve().then(() => (session === id ? read() : undefined));
        },
        // `m` delivered `text` to `id`: it shows as pending until the feed has it, and the chat is read at once
        sent(id, text) {
          if (disposed || typeof id !== "string" || typeof text !== "string") return;
          const before = chatState();
          setChatState(Object.assign({}, before, { extras: [...before.extras, { session: id, text, at: clock() }].slice(-8) }));
          if (id === session) read();
          requestRender();
        },
        read,
        stop() {
          session = null;
          generation += 1;
          stopTimer();
          chatRunner.stop();
        },
        wanted: () => session,
      };
    })();


    // One watcher follows one session: its latest document, the spinner frame, its timers. The sidebar and the
    // panel each own one; both go through the same runner, which runs one process per session at a time.
    function createWatcher(calm = false) {
      const [state, setState] = host.createSignal({ session: undefined, doc: null });
      const [frame, setFrame] = host.createSignal(0);
      let current; // the session being watched
      let generation = 0;
      let spinTimer = null;
      let spinMs = 0;
      let pollTimer = null;
      let stopped = false; // set when the panel closes; a late fetch or a re-run view must not revive the timers
      let sceneOn = false; // the panel draws the orbit: frames at 100 ms while work moves, no frames (2D) or 166 ms (3D) idle
      const sceneInterval = (doc) => {
        try {
          return typeof watcher.sceneInterval === "function" ? watcher.sceneInterval(doc) : frameInterval(doc);
        } catch (_) {
          return frameInterval(doc);
        }
      };

      // The frame timer. The sidebar's list animates only while something works (a spinner every 200 ms); the panel's list does not move; the orbit always
      // moves while it is shown. Each tick asks `beforeTick` (the orbit's painter) whether the frame changed.
      function spinner(doc) {
        let ms = 0;
        if (!disposed && !stopped) {
          if (sceneOn) ms = isPlainObject(doc) && (doc.error === undefined || doc.error === null) ? sceneInterval(doc) : 0;
          else if (!calm) ms = isAnimated(doc) || sideBusy(doc) || asking() ? SPINNER_MS : 0; // the panel's list never ticks
        }
        if (ms === spinMs && !!spinTimer === ms > 0) return;
        if (spinTimer) timers.clearInterval(spinTimer);
        spinTimer = null;
        spinMs = ms;
        if (ms) {
          spinTimer = timers.setInterval(() => {
            try {
              if (sceneOn && sceneInterval(state().doc) !== spinMs) spinner(state().doc); // the camera's move began or ended
              if (typeof watcher.beforeTick === "function" && watcher.beforeTick() === false) return; // nothing changed
            } catch (error) {
              report("tick", error);
            }
            setFrame((f) => f + 1);
            requestRender();
          }, ms);
        }
      }

      function setScene(on) {
        if (sceneOn === !!on) return;
        sceneOn = !!on;
        spinner(state().doc);
      }

      function refresh() {
        if (disposed || stopped || !current) return Promise.resolve();
        const session = current;
        const asked = generation;
        return Promise.all([
          runner.snapshot({ session, directory: directory() }).catch((error) => errorDocument(errorMessage(error))),
          host.os?.platform?.() === "win32" ? Promise.resolve([]) : loopRunner.list(directory()).catch(() => []),
        ])
          .then(([doc, loops]) => {
            if (doc && typeof doc === "object" && !doc.error && loops.length) doc.loops = loops;
            return doc;
          })
          .then((doc) => {
            if (disposed || stopped || asked !== generation || session !== current) return; // late result: drop it
            const before = state();
            const previous = before.session === session && before.doc && before.doc !== doc ? before.doc : null;
            setState({ session, doc, previous, at: clock() });
            spinner(doc);
            requestRender();
          });
      }

      // Called from inside the reactive view: it changes no signal, it only schedules the fetch.
      function watch(session) {
        if (session === current) return;
        current = session;
        generation += 1;
        spinner(null);
        if (session) Promise.resolve().then(refresh);
      }

      function poll(on) {
        if (on && !pollTimer && !disposed && !stopped) {
          pollTimer = timers.setInterval(() => {
            refresh();
          }, options.intervalMs);
        } else if (!on && pollTimer) {
          timers.clearInterval(pollTimer);
          pollTimer = null;
        }
      }

      function stop() {
        stopped = true;
        generation += 1;
        poll(false);
        if (spinTimer) timers.clearInterval(spinTimer);
        spinTimer = null;
        spinMs = 0;
      }

      // The panel is mounted again: start from a clean slate.
      function reopen() {
        if (!stopped) return;
        stopped = false;
        current = undefined;
      }

      const retime = () => spinner(state().doc);
      const watcher = { state, frame, watch, poll, stop, reopen, setScene, retime, refresh, beforeTick: null, sceneInterval: null };
      watchers.push(watcher);
      return watcher;
    }

    // Pending permission requests and questions the host knows about: OpenCode 2.3's `data.session.permission` and
    // `data.session.form` (reactive store reads, so the view draws again when they change). None on other hosts.
    function hostRequests() {
      const out = [];
      try {
        const store = ctx.data && ctx.data.session;
        if (!store || typeof store !== "object") return out;
        const titleOf = (id) => {
          try {
            const info = typeof store.get === "function" ? store.get(id) : undefined;
            return info && typeof info.title === "string" ? info.title : undefined;
          } catch (_) {
            return undefined;
          }
        };
        for (const [kind, source] of [["permission", store.permission], ["question", store.form]]) {
          if (!source || typeof source.sessions !== "function") continue;
          let ids = [];
          try {
            ids = source.sessions();
          } catch (error) {
            report("requests", error);
            continue;
          }
          for (const id of Array.isArray(ids) ? ids : []) {
            if (typeof id !== "string" || !id || id === "global") continue;
            let count = 1;
            try {
              const pending = typeof source.list === "function" ? source.list(id) : undefined;
              if (Array.isArray(pending)) count = Math.max(1, pending.length);
            } catch (_) {
              /* count unknown */
            }
            out.push({ kind, session: id, count, title: titleOf(id) });
          }
        }
      } catch (error) {
        report("requests", error);
      }
      return out;
    }

    // `resolve(props)` -> { session, width, list? }, or { hidden: true } to draw nothing.
    function makeView(watcher, resolve, props) {
      let tones = { plain: {}, muted: {}, accent: {}, danger: {} };
      let width = WIDTH;
      try {
        tones = themeTones(ctx.theme);
        const resolved = resolve(props);
        if (resolved.hidden) {
          watcher.poll(false);
          watcher.watch(undefined);
          return [];
        }
        const { session } = resolved;
        width = resolved.width || WIDTH;
        watcher.watch(session);
        const snap = watcher.state();
        const ready = !!session && snap.session === session && !!snap.doc;
        if (resolved.painter) {
          const sceneSize = ready && resolved.scene ? resolved.scene : null;
          watcher.setScene(!!sceneSize);
          if (sceneSize) {
            if (resolved.list && typeof resolved.list.onLayout === "function") resolved.list.onLayout({ items: [], selected: null, scene: true });
            watcher.frame(); // a dependency: each tick that changed the frame draws again
            return resolved.painter.draw(snap, sceneSize, tones);
          }
          resolved.painter.forget();
        }
        let lines;
        const listOptions = resolved.list || {};
        if (!session) lines = [[{ text: "No session", tone: "muted" }]];
        else if (!ready) lines = render(null, { width });
        else {
          const requests = hostRequests();
          const selected = typeof listOptions.selected === "function" ? listOptions.selected() : undefined;
          const height = resolved.height || null;
          const columns = resolved.columns || null;
          const base = { width, frame: watcher.frame(), now: clock(), requests, cursor: !!listOptions.cursor, selected };
          if (listOptions.styled) Object.assign(base, { styled: true, dark: sceneColors(ctx.theme, tones).dark, reduced: options.reducedMotion === true });
          if (listOptions.styled && height) base.height = height;
          if (typeof listOptions.asking === "function" && listOptions.asking()) base.asking = true;
          // the panel's rows carry what each working agent is doing; they go when the panel is too short for the rows
          let more = listOptions.activity ? { activity: true } : {};
          let first = layout(snap.doc, Object.assign({}, base, more));
          // the hub's trail of recent actions goes first, then the activity lines altogether
          if (more.activity && height && first.lines.length + 1 > height) {
            more = { activity: true, trail: false };
            first = layout(snap.doc, Object.assign({}, base, more));
          }
          if (more.activity && height && first.lines.length + 1 > height) {
            more = {};
            first = layout(snap.doc, base);
          }
          const plan = typeof listOptions.plan === "function" ? listOptions.plan(first, height, columns ? Math.min(width, columns) : width) : null;
          let result = first;
          if (typeof listOptions.keys === "function") {
            const item = first.items.find((i) => i.key === first.selected) || null;
            result = layout(snap.doc, Object.assign({}, base, more, { keys: listOptions.keys(item, first.items, first, plan) }));
          }
          result.session = session;
          if (typeof listOptions.onLayout === "function") listOptions.onLayout(result, plan);
          lines = plan && typeof listOptions.compose === "function" ? listOptions.compose(result, plan) : result.lines;
        }
        if (!ready && typeof listOptions.onLayout === "function") listOptions.onLayout({ items: [], selected: null });
        return lines.map((line) => lineNode(line, tones));
      } catch (error) {
        // Never an empty view: draw the error as one line, and if even that fails, as plain uncoloured text.
        report("view", error);
        try {
          return [lineNode(render(errorDocument(errorMessage(error)), { width })[0], tones)];
        } catch (again) {
          report("error line", again);
          try {
            return [plainLine(errorMessage(error), width)];
          } catch (last) {
            report("plain line", last);
            return [];
          }
        }
      }
    }

    function makeRender(watcher, resolve) {
      return function slotRender(props) {
        try {
          const root = element("box", { width: "100%", flexDirection: "column", flexShrink: 0 });
          host.insert(root, () => makeView(watcher, resolve, props));
          return root;
        } catch (error) {
          report("render", error);
          try {
            return plainLine(errorMessage(error), WIDTH);
          } catch (_) {
            return null;
          }
        }
      };
    }

    // The view chosen with `g` in the panel, remembered while the app runs: "list" (what the panel opens on, unless
    // `options.picture` names a picture) or "scene" (the picture, chosen with `g`).
    const [viewMode, setViewMode] = host.createSignal(options.picture === "off" ? "list" : "scene");
    const [fullByF, setFullByF] = host.createSignal(false); // the user made the panel full screen with `f`
    // The picture is wanted only when the user chose it with `g`; it is drawn only where it fits (sceneFits).
    const presentationOf = (p) => {
      try {
        return p ? p.presentation : undefined;
      } catch (_) {
        return undefined;
      }
    };
    const wantScene = () => viewMode() === "scene";
    // whether the panel, as last measured, is big enough for a picture (the list names `g picture` only then)
    let panelFits = false;
    const pictureFits = () => panelFits;

    // The list's rows in the panel: the selected row's key (remembered while the app runs) and the rows last drawn.
    const [selection, setSelection] = host.createSignal(null);
    const rows = { items: [], selected: null, scene: false, flows: [], root: null, session: null, chat: "off", chatMax: 0, chatRows: 0 };
    const has = (fn) => typeof fn === "function";
    const canOpen = () => !!(ctx.ui && ctx.ui.router && has(ctx.ui.router.navigate));
    const canStop = () => !!(ctx.client && ctx.client.session && has(ctx.client.session.interrupt) && ctx.ui && ctx.ui.dialog && has(ctx.ui.dialog.confirm));
    const canMessage = () => !!(ctx.client && ctx.client.session && has(ctx.client.session.prompt) && ctx.ui && ctx.ui.dialog && has(ctx.ui.dialog.prompt));
    const canAsk = () => !!(ctx.ui && ctx.ui.dialog && has(ctx.ui.dialog.prompt));
    const canRun = () => host.os?.platform?.() !== "win32" && canMessage(); // toolkit flow commands require POSIX
    let runBusy = false; // a path is being run: from `r` until it was sent or given up
    const [runShown, setRunShown] = host.createSignal(true); // the key line's `r run`: hidden while runBusy
    const setRunBusy = (busy) => {
      runBusy = busy;
      setRunShown(!busy);
      requestRender();
    };
    let askKeyWanted = null; // the mounted panel's setAskKey(want): `a` is there while the snapshot has a side chat row
    let runKeyWanted = null; // the mounted panel's setRunKey(want): `r` is there while the snapshot has a path
    // The chat of the selected row, in the panel's list: `chatOpen` is the user's `c` (the chat in place of the lower part of
    // the panel, for when it does not fit beside the list); `chatView` is where it is scrolled (lines up from the newest,
    // 0 follows it) and what it last drew; `chatTick` redraws after a scroll.
    const [chatOpen, setChatOpen] = host.createSignal(false);
    const [chatTick, setChatTick] = host.createSignal(0);
    const chatView = { scroll: 0, session: null, total: null, max: 0, rows: 0 };
    // Native Windows hosts can retain panel key layers after input returns to
    // the prompt. Until panel-scoped bindings are verified there, never
    // register those layers: even disabled bindings can consume typing.
    const hasKeys = () => host.os?.platform?.() !== "win32" && !!(ctx.keymap && has(ctx.keymap.layer));
    const squash = (text) => cleanChat(text).replace(/\s+/g, " ").trim();
    // messages sent from here that the feed does not show yet, as the pending items they will be
    const pendingExtras = (state, session, feed) => {
      const now = clock();
      const told = new Set((feed && Array.isArray(feed.items) ? feed.items : []).filter((i) => i && (i.kind === "user" || i.user === true)).map((i) => squash(i.text)));
      return state.extras
        .filter((e) => e.session === session && now - e.at < SENT_KEEP_MS && !told.has(squash(e.text)))
        .map((e) => ({ kind: "user", text: e.text, pending: true }));
    };
    // The chat's place: beside the list when the panel has the room, in place of its lower part after `c`, else nowhere.
    // -> { mode: "inline" | "open" | "none" | "off", item, win, live, name, width }
    // the row's session is working: an agent's running session, or the side chat answering
    const itemLive = (item) => (item.kind === "sidechat" ? item.live === true : !!item.busy);
    const chatPlan = (first, height, width) => {
      chatTick();
      const item = first.items.find((i) => i.key === first.selected) || null;
      if (!item || !height) return { mode: "off", item };
      const keyRows = hasKeys() ? 1 : 0;
      let mode = "none";
      let rowsAvail = 0;
      if (chatOpen()) {
        mode = "open";
        rowsAvail = Math.max(1, height - 2 - keyRows);
      } else {
        const room = height - first.lines.length - keyRows;
        if (room >= CHAT_MIN_INLINE) {
          mode = "inline";
          rowsAvail = room - 1;
        }
      }
      if (mode === "none") return { mode, item };
      const cw = Math.max(1, width - 1);
      const st = chatState();
      let view;
      if (!item.open) view = { state: "none" };
      else if (st.session !== item.open || (!st.feed && !st.error)) view = { state: "loading" };
      else if (!st.feed) view = { state: "error", error: st.error };
      else view = { state: "feed", feed: st.error ? Object.assign({}, st.feed, { warning: st.error }) : st.feed, extra: pendingExtras(st, item.open, st.feed) };
      const lines = chatLines(view, cw);
      if (chatView.session !== item.open) Object.assign(chatView, { session: item.open, scroll: 0, total: null });
      if (chatView.scroll > 0 && chatView.total !== null && lines.length !== chatView.total) chatView.scroll += lines.length - chatView.total;
      chatView.total = lines.length;
      const win = chatWindow(lines, rowsAvail, chatView.scroll);
      Object.assign(chatView, { scroll: win.scroll, max: win.max, rows: win.rows });
      return { mode, item, win, live: itemLive(item), name: item.kind === "human" ? "orchestrator" : item.name, width: cw };
    };
    // The lines to draw: the list's, with the chat under its rows (before the key line) or in place of them (the
    // selected row, the chat, the key line).
    const gutter = (line) => [{ text: " ", tone: "plain" }, ...line];
    const chatCompose = (result, plan) => {
      if ((plan.mode !== "inline" && plan.mode !== "open") || !plan.win) return result.lines;
      const width = plan.width + 1;
      const keysAt = typeof result.keysLine === "number" ? result.keysLine : null;
      const picked = result.items.find((i) => i.key === result.selected);
      const top = plan.mode === "open" && picked ? [result.lines[picked.line]] : result.lines.filter((_, i) => i !== keysAt);
      const heading = gutter(chatHeading({ name: plan.name, live: plan.live, scroll: plan.win.scroll }, plan.width));
      const body = plan.win.lines.map(gutter);
      const keyLine = keysAt !== null ? [result.lines[keysAt]] : [];
      return [...top, heading, ...body, ...keyLine].map((line) => clipLine(line, width));
    };
    // The key line for the selected row: only keys that do something there.
    const keyPieces = (item, items, result, plan) => {
      const pieces = [];
      const open = !!plan && plan.mode === "open";
      if (open) pieces.push(["↑↓", "scroll"]);
      else if (Array.isArray(items) && items.length > 1) pieces.push(["↑↓", "select"]);
      if (item && item.open && canOpen()) pieces.push(["⏎", "open"]);
      if (item && item.busy && canStop()) pieces.push(["s", "stop"]);
      if (item && item.busy && canMessage()) pieces.push(["m", "message"]);
      if (item && item.kind === "sidechat" && canAsk()) pieces.push(["a", "ask"]);
      if (plan && item && item.open) {
        if (plan.mode === "open") pieces.push(["c", "back"]);
        else if (plan.mode === "none") pieces.push(["c", "chat"]);
      }
      if (plan && plan.win && plan.win.max > 0 && !open) pieces.push(["⇞⇟", "scroll"]);
      if (result && Array.isArray(result.flows) && result.flows.length && canRun() && runShown()) pieces.push(["r", "run"]);
      if (pictureFits()) pieces.push(["g", "picture"]);
      pieces.push(["q", "close"]);
      return pieces;
    };
    const panelList = {
      cursor: true,
      styled: true,
      activity: true,
      asking: () => asking(),
      selected: () => selection(),
      keys: (item, items, result, plan) => (hasKeys() ? keyPieces(item, items, result, plan) : []),
      plan: chatPlan,
      compose: chatCompose,
      onLayout(result, plan) {
        rows.items = Array.isArray(result.items) ? result.items : [];
        rows.selected = result.selected || null;
        rows.scene = !!result.scene;
        rows.flows = Array.isArray(result.flows) ? result.flows : [];
        rows.root = typeof result.root === "string" ? result.root : null;
        rows.session = typeof result.session === "string" ? result.session : null;
        rows.chat = plan ? plan.mode : "off";
        rows.chatMax = plan && plan.win ? plan.win.max : 0;
        rows.chatRows = plan && plan.win ? plan.win.rows : 0;
        // the chat is read for the selected row while it shows; never on the orbit, hidden or with no rows
        if (!result.scene && rows.items.length && plan && plan.item && plan.item.open && (plan.mode === "inline" || plan.mode === "open")) {
          chatFeed.want(plan.item.open, itemLive(plan.item));
        } else {
          chatFeed.want(null);
        }
        if (Array.isArray(result.flows) && askKeyWanted) askKeyWanted(result.items.some((i) => i.kind === "sidechat")); // a list's layout, not the orbit's
        if (Array.isArray(result.flows) && runKeyWanted) runKeyWanted(result.flows.length > 0); // a list's layout, not the orbit's
        if (Array.isArray(result.flows)) {
          // debug log only, when they change (real-host.py --live-paths waits on a path's new rev after a refresh)
          const revs = result.flows.map((f) => `${f.id}@${f.rev || "-"}`).join(" ");
          if (revs && revs !== rows.revs) report("paths", revs);
          rows.revs = revs;
        }
      },
    };
    const toast = (message, variant = "info") => {
      try {
        if (ctx.ui && ctx.ui.toast && has(ctx.ui.toast.show)) ctx.ui.toast.show({ message, variant });
      } catch (error) {
        report("toast", error);
      }
    };
    const selectedItem = () => rows.items.find((i) => i.key === rows.selected) || null;
    // A dialog is open: the host's keymap is out of its "base" mode (OpenCode 2.3 pushes "modal" for every dialog,
    // and layers without a mode, like the panel's, are then off), or one of ours (the stop confirm, the message prompt)
    // has not closed yet. Ours stays "open" until a moment after it closes, so the key that closed it (Enter in the
    // confirm) cannot also act on the list.
    let ourDialogs = 0;
    const hostMode = () => {
      try {
        const mode = ctx.keymap && ctx.keymap.mode;
        return mode && typeof mode.current === "function" ? mode.current() : undefined;
      } catch (_) {
        return undefined;
      }
    };
    const dialogOpen = () => ourDialogs > 0 || (hostMode() !== undefined && hostMode() !== "base");
    const withDialog = (open) => {
      ourDialogs += 1;
      let done = false;
      const close = () => {
        if (done) return;
        done = true;
        setTimeout(() => {
          ourDialogs = Math.max(0, ourDialogs - 1);
        }, 0);
      };
      return Promise.resolve()
        .then(open)
        .then(
          (value) => (close(), value),
          (error) => {
            close();
            throw error;
          },
        );
    };
    const moveSelection = (step) => {
      if (!rows.items.length) return;
      const at = Math.max(0, rows.items.findIndex((i) => i.key === rows.selected));
      const next = rows.items[Math.max(0, Math.min(rows.items.length - 1, at + step))];
      if (next && next.key !== rows.selected) {
        rows.selected = next.key;
        setSelection(next.key);
        requestRender();
      }
    };
    // Enter: open the row's session (a request's session is where the host shows it to answer).
    const openSelected = () => {
      const item = selectedItem();
      if (!item || !item.open || !canOpen()) return;
      try {
        report("action", `open ${item.open}`); // debug log only (real-host.py --open, --dialog)
        ctx.ui.router.navigate({ type: "session", sessionID: item.open });
      } catch (error) {
        report("open", error);
        toast(`Could not open ${item.name}: ${errorMessage(error)}`, "error");
      }
    };
    // `s`: interrupt the working agent's running session, after a confirmation.
    const stopSelected = () => {
      const item = selectedItem();
      if (!item || !item.busy || !canStop()) return;
      const sessionID = item.busy;
      withDialog(() => ctx.ui.dialog.confirm({ title: `Stop ${item.name}?`, message: `Interrupts its running session ${sessionID}.` }))
        .then((ok) => {
          report("action", `stop ${sessionID} confirm=${ok}`); // debug log only
          if (ok !== true) return undefined;
          return Promise.resolve(ctx.client.session.interrupt({ sessionID })).then((res) => {
            const data = res && typeof res === "object" && res.data && typeof res.data === "object" ? res.data : res;
            toast(data && data.interrupted === false ? `${item.name} was not running` : `Stopped ${item.name}`);
          });
        })
        .catch((error) => {
          report("stop", error);
          toast(`Could not stop ${item.name}: ${errorMessage(error)}`, "error");
        });
    };
    // `m`: send the working agent a message into its running session (delivered as a steer, during its turn).
    const messageSelected = () => {
      const item = selectedItem();
      if (!item || !item.busy || !canMessage()) return;
      const sessionID = item.busy;
      withDialog(() => ctx.ui.dialog.prompt({ title: `Message ${item.name}`, placeholder: "Sent into its running session" }))
        .then((text) => {
          if (typeof text !== "string" || !text.trim()) return undefined;
          const sentText = text.trim();
          return Promise.resolve(ctx.client.session.prompt({ sessionID, text: sentText, delivery: "steer" })).then(() => {
            toast(`Sent to ${item.name}`);
            chatFeed.sent(sessionID, sentText); // pending in the chat until it lands, and the chat is read at once
          });
        })
        .catch((error) => {
          report("message", error);
          toast(`Could not message ${item.name}: ${errorMessage(error)}`, "error");
        });
    };
    // `a` on the side chat's row: one question to `agentgraph sidechat ask` (the text goes on its standard input), asked in the
    // host's prompt. Empty and over-long questions are refused; one is in flight at a time; the result is a toast and the chat
    // (and the snapshot, which has the side session once it exists) is read at once.
    const askSide = () => {
      const item = selectedItem();
      if (!item || item.kind !== "sidechat" || !canAsk() || asking()) return;
      withDialog(() => ctx.ui.dialog.prompt({ title: "Ask the side chat", placeholder: "A question about the running task" }))
        .then((answer) => {
          if (typeof answer !== "string") return undefined; // cancelled
          const question = answer.trim();
          if (!question) {
            toast("No question: nothing was asked.");
            return undefined;
          }
          const length = [...question].length;
          if (length > SIDE_QUESTION_LIMIT) {
            toast(`The question is ${length.toLocaleString("en-US")} characters; at most ${SIDE_QUESTION_LIMIT.toLocaleString("en-US")}. Nothing was asked.`, "error");
            return undefined;
          }
          if (asking()) return undefined;
          if (!rows.root) {
            toast("The side chat could not be asked: no orchestrator session.", "error");
            return undefined;
          }
          setAsking(true);
          for (const watcher of watchers) watcher.retime();
          requestRender();
          report("action", "ask side chat");
          return sideRunner
            .ask({ main: rows.root, text: question, directory: directory() })
            .then((res) => {
              if (disposed) return;
              if (res && typeof res.answer === "string") toast("Side chat answered");
              else toast("Side chat: " + truncateText(cleanChat((res && res.error) || "no answer").replace(/\s+/g, " ").trim() || "no answer", 100), "error");
            })
            .finally(() => {
              if (disposed) return;
              setAsking(false);
              for (const watcher of watchers) {
                watcher.retime();
                watcher.refresh();
              }
              chatFeed.read();
              requestRender();
            });
        })
        .catch((error) => {
          report("ask", error);
          if (!disposed) toast("Side chat: " + truncateText(cleanChat(errorMessage(error)).replace(/\s+/g, " ").trim() || "failed", 100), "error");
        });
    };
    // `r`: run a drawn path. The chooser (skipped with one path), the goal, the run message from the back end, then the
    // send to the orchestrator's session: the snapshot's root session (the top of the panel's session tree, never a
    // child), else the panel's own. Each failure is a toast; nothing is sent twice (runBusy); the demo sends nothing.
    const choosePath = (flows) => {
      if (flows.length === 1) return Promise.resolve(flows[0]);
      if (has(ctx.ui.dialog.select)) {
        const choices = flows.map((f) => {
          const stats = flowStatsText(f.stats);
          return Object.assign({ title: flowLabel(f), value: f.id }, stats ? { footer: stats } : {});
        });
        return withDialog(() => {
          const chosen = ctx.ui.dialog.select({ title: "Run a path", placeholder: "Search", options: choices, current: flows[0].id });
          // the host opens every plugin dialog at its medium width (60 columns), too narrow for a path and its stats
          // side by side; `ui.dialog.set` widens the open dialog (large: 88 columns, within the terminal)
          try {
            if (has(ctx.ui.dialog.set)) ctx.ui.dialog.set({ size: "large" });
          } catch (error) {
            report("dialog size", error);
          }
          return chosen;
        }).then(
          (id) => flows.find((f) => f.id === id) || null,
        );
      }
      // a host without a chooser: the paths are numbered in a prompt's title, the answer is a number
      return withDialog(() =>
        ctx.ui.dialog.prompt({ title: "Run which path? " + flows.map((f, i) => `${i + 1} ${flowName(f)}`).join("  "), placeholder: "1" }),
      ).then((answer) => flows[Math.floor(Number(String(answer || "").trim())) - 1] || null);
    };
    let keptGoal = null; // {id, goal}: the goal typed for a path that had changed, offered again on the next `r`
    const runPath = () => {
      const flows = rows.flows.slice();
      if (runBusy || !flows.length || !canRun()) return;
      const sessionID = rows.root || rows.session;
      setRunBusy(true);
      let chosen = null;
      Promise.resolve()
        .then(() => choosePath(flows))
        .then((flow) => {
          if (!flow) return undefined;
          chosen = flow;
          report("action", `choose ${flow.id}`); // debug log only (real-host.py --paths)
          // offered only for the path it was typed for; kept while another path is chosen
          const kept = keptGoal && keptGoal.id === flow.id ? keptGoal.goal : null;
          if (kept) keptGoal = null;
          return withDialog(() =>
            ctx.ui.dialog.prompt(
              Object.assign(
                { title: `Goal for ${flowName(flow)}`, description: flow.path.join(" → "), placeholder: "The task for the orchestrator to run on this path" },
                kept ? { value: kept } : {},
              ),
            ),
          ).then((answer) => {
            const goal = typeof answer === "string" ? answer.trim() : "";
            if (!goal) {
              if (typeof answer === "string") toast("No goal: nothing was sent.");
              return undefined;
            }
            const length = [...goal].length;
            if (length > GOAL_LIMIT) {
              toast(`The goal is ${length.toLocaleString("en-US")} characters; at most ${GOAL_LIMIT.toLocaleString("en-US")}. Nothing was sent.`, "error");
              return undefined;
            }
            if (demo) {
              report("action", `run ${flow.id} demo`);
              toast(`Demo: ${flowName(flow)} is not sent.`);
              return undefined;
            }
            if (!sessionID) {
              toast(`Could not run ${flowName(flow)}: no orchestrator session.`, "error");
              return undefined;
            }
            // flow.rev (when the snapshot has one) is the version the chooser showed: `--expect` refuses a path
            // that changed since (ids are reused: a removed and redrawn path can get the same id)
            const onSpawn = (argv) => report("action", "flow prompt: " + argv.join(" ")); // debug log only
            return runFlowPrompt({ spawn: host.spawn, path: host.path, env: host.env, home: options.home, python: options.python, onSpawn }, flow.id, goal, flow.rev).then((made) => {
              if (made && made.stale) {
                report("action", `stale ${flow.id} ${flow.rev}`);
                keptGoal = { id: flow.id, goal };
                for (const watcher of watchers) watcher.refresh();
                toast(`${flowName(flow)} changed since you opened it: nothing was sent. Press r again.`, "warning");
                return undefined;
              }
              if (!made || typeof made.text !== "string") {
                toast(`Could not run ${flowName(flow)}: ${(made && made.error) || "no run message"}`, "error");
                return undefined;
              }
              if (noSend) {
                report("action", `prompt ${flow.id} ${[...made.text].length} chars for ${sessionID}, not sent`);
                toast(`Not sent (AGENTGRAPH_TUI_NOSEND): ${flowName(flow)}, a message of ${[...made.text].length} characters for ${sessionID}`);
                return undefined;
              }
              report("action", `send ${flow.id} ${sessionID}`);
              return Promise.resolve(ctx.client.session.prompt({ sessionID, text: made.text })).then(() => toast(`Sent ${flowName(flow)} to the orchestrator`));
            });
          });
        })
        .catch((error) => {
          report("run", error);
          toast(`Could not send ${chosen ? flowName(chosen) : "the path"}: ${errorMessage(error)}`, "error");
        })
        .finally(() => setRunBusy(false));
    };
    // The orbit's view chosen with `d`, remembered while the app runs; it opens in 2D. A switch morphs the old view
    // into the new one over CHANGE_MS (each agent travels from its place in one to its place in the other).
    const [dimension, setDimension] = host.createSignal({ view: "2d", change: null });
    // The picture chosen with `o`, remembered while the app runs: the planets (src/planets.js, the default picture) or the orbit, which `options.picture` can open on.
    const [picture, setPicture] = host.createSignal(options.picture === "orbit" ? "orbit" : "planets");
    const togglePicture = () => {
      setPicture(picture() === "planets" ? "orbit" : "planets");
      for (const w of watchers) if (w.retime) w.retime();
      requestRender();
    };
    const toggleDimension = () => {
      const cur = dimension();
      const view = cur.view === "3d" ? "2d" : "3d";
      setDimension({ view, change: { from: cur.view, at: clock() } });
      for (const w of watchers) if (w.retime) w.retime(); // the timer follows the camera's move
      requestRender();
    };
    // The 3D camera turned by hand (scene.js orbitCamera): null until the first turn; remembered while the app runs.
    // A turn holds the self-turn for RESUME_MS, which then goes on from the angle left.
    const [orbit, setOrbit] = host.createSignal(null);
    const turnBy = (dTurn, dElevation, why) => {
      const next = orbitTurned(orbit(), clock(), dTurn, dElevation);
      report("action", `${why} turn=${next.turn.toFixed(3)} elevation=${next.elevation.toFixed(3)}`); // debug log only (real-host.py --orbit)
      setOrbit(next);
      requestRender();
    };
    const resetOrbit = () => {
      report("action", "orbit reset"); // debug log only
      setOrbit(orbitReset(clock()));
      requestRender();
    };
    // The mouse on the orbit (OpenTUI's onMouseDown, onMouseDrag and onMouseUp on the panel's root box; a press on
    // any line of the scene bubbles up to it): a click switches 2D and 3D, as `d` does; a drag turns the 3D view (a
    // column across 6 degrees round the upright axis, a row 12 degrees of elevation) and switches nothing. The list
    // ignores the mouse, as before. OpenTUI sends a press to the line under it and a drag's motion to the line that
    // was pressed; a redraw keeps the lines' nodes (sceneSpans), so neither is lost. Should a line go (a resize), the
    // line under the pointer still gets "over" with the button held (onMouseOver), which carries the drag on.
    let press = null; // { x, y, lastX, lastY, dragged } from the left button's press on the orbit
    const orbitShown = () => rows.scene && picture() === "orbit" && !dialogOpen();
    const cell = (e, key) => Math.floor(Number(e && e[key]));
    const sceneMouse = {
      down(e) {
        try {
          press = null;
          if (!e || Number(e.button) !== 0 || !orbitShown()) return;
          const x = cell(e, "x");
          const y = cell(e, "y");
          if (!Number.isFinite(x) || !Number.isFinite(y)) return;
          press = { x, y, lastX: x, lastY: y, dragged: false };
          report("mouse", `down ${x},${y}`); // debug log only (real-host.py --orbit)
        } catch (error) {
          report("mouse", error);
        }
      },
      drag(e) {
        try {
          if (!press || !orbitShown() || Number(e && e.button) !== 0) return;
          const x = cell(e, "x");
          const y = cell(e, "y");
          if (!Number.isFinite(x) || !Number.isFinite(y)) return;
          const dx = x - press.lastX;
          const dy = y - press.lastY;
          press.lastX = x;
          press.lastY = y;
          if (!dx && !dy) return;
          press.dragged = true;
          if (dimension().view === "3d") turnBy(-dx * DRAG_TURN_PER_CELL, dy * DRAG_ELEVATION_PER_ROW, `drag ${dx},${dy}`);
        } catch (error) {
          report("mouse", error);
        }
      },
      up(e) {
        try {
          const p = press;
          press = null;
          if (!p || !orbitShown()) return;
          const moved = cell(e, "x") !== p.x || cell(e, "y") !== p.y;
          report("mouse", `up ${cell(e, "x")},${cell(e, "y")}${p.dragged || moved ? " (drag)" : " (click)"}`); // debug log only
          if (!p.dragged && !moved) toggleDimension();
        } catch (error) {
          report("mouse", error);
        }
      },
    };
    // The keys named on the 3D view's bottom row while the panel has the turn layer (panelKeys binds them).
    const ORBIT_KEYS = Object.freeze([["←→↑↓", "turn"], ["0", "reset"]]);
    const PLANET_KEYS = Object.freeze([["g", "list"], ["o", "orbit"]]);
    let orbitKeysOn = false;
    // The orbit's frames: rows are reused while their text and tones stay the same, and a tick whose frame equals
    // the last one drawn pushes nothing to the host.
    function createPainter(watcher) {
      let rows = []; // [{ key, node }]
      let drawn = null; // the key of the last frame drawn
      let prepared = null; // a frame the tick computed, for the view to use as it is
      let colors = sceneColors(ctx.theme, null);
      // The 3D sky's shooting stars: one per run that finishes (a path's stats.passed grows) between two snapshots
      // both drawn while the 3D view showed, spaced STREAK_GAP_MS apart. None for the first snapshot drawn (opening
      // the orbit), for runs that finished while the list or the 2D view showed, or with reduced motion.
      let seen = null; // { doc, runs: Map(flow -> passed), in3d }: the runs of the last snapshot drawn
      let streaks = [];
      const noteRuns = (snap) => {
        const now = clock();
        const live = streaks.filter((st) => now < st.at + STREAK_MS);
        if (live.length !== streaks.length) streaks = live;
        const in3d = picture() === "orbit" && dimension().view === "3d";
        if (seen && seen.doc === snap.doc) {
          if (in3d) seen.in3d = true;
          return;
        }
        const runs = new Map(finishedRuns(snap.doc).map((r) => [r.flow, r.passed]));
        if (seen && seen.in3d && in3d && snap.previous === seen.doc && !options.reducedMotion) {
          const added = [];
          let at = Math.max(now, ...streaks.map((st) => st.at + STREAK_GAP_MS));
          for (const [flow, passed] of runs) {
            const before = seen.runs.has(flow) ? seen.runs.get(flow) : passed;
            for (let run = before + 1; run <= passed && added.length < 8; run += 1) {
              added.push({ flow, run, at });
              at += STREAK_GAP_MS;
            }
          }
          if (added.length) {
            streaks = streaks.concat(added);
            report("sky", `streaks ${added.map((st) => st.flow + "#" + st.run).join(" ")}`); // debug log only
          }
        }
        seen = { doc: snap.doc, runs, in3d };
      };
      const viewOptions = () => {
        const d = dimension();
        const o = orbit();
        if (picture() === "planets") return Object.assign({ colors }, orbitKeysOn ? { keys: PLANET_KEYS } : {}, options.reducedMotion ? { motion: "reduced" } : {});
        return Object.assign({ view: d.view, change: d.change, colors }, o ? { orbit: o } : {}, orbitKeysOn ? { keys: ORBIT_KEYS } : {},
          streaks.length ? { streaks } : {}, options.reducedMotion ? { motion: "reduced" } : {});
      };
      const compute = (snap, size) => {
        noteRuns(snap);
        if (picture() === "planets") return planetsScene(snap.doc, Object.assign(viewOptions(), { width: size.width, height: size.height, now: clock() }));
        return scene(snap.doc, Object.assign(viewOptions(), { width: size.width, height: size.height, now: clock(), previous: snap.previous ? { doc: snap.previous, at: snap.at } : undefined }));
      };
      // the frame interval at now; the previous document is part of it (the sun's spikes draw back for a second after the hub stops)
      watcher.sceneInterval = (doc) => {
        const snap = watcher.state();
        const previous = snap && snap.doc === doc && snap.previous ? { doc: snap.previous, at: snap.at } : undefined;
        if (picture() === "planets") return planetsInterval(doc, viewOptions(), clock());
        return frameInterval(doc, Object.assign(viewOptions(), previous ? { previous } : {}), clock());
      };
      let lastSize = null;
      watcher.beforeTick = () => {
        if (!lastSize) return true;
        const snap = watcher.state();
        if (!snap.doc) return true;
        const lines = compute(snap, lastSize);
        const key = lines.map(lineKey).join("\n");
        if (key === drawn) return false;
        prepared = { doc: snap.doc, width: lastSize.width, height: lastSize.height, lines, picture: picture(), dimension: dimension(), orbit: orbit(), streaks };
        return true;
      };
      return {
        draw(snap, size, tones) {
          lastSize = size;
          colors = sceneColors(ctx.theme, tones);
          let lines;
          const same = prepared && prepared.doc === snap.doc && prepared.width === size.width && prepared.height === size.height;
          if (same && prepared.picture === picture() && prepared.dimension === dimension() && prepared.orbit === orbit() && prepared.streaks === streaks) lines = prepared.lines;
          else lines = compute(snap, size);
          prepared = null;
          const keys = lines.map(lineKey);
          // a changed line keeps its node and gets new spans: the node under the mouse stays (sceneSpans)
          const next = lines.map((line, i) => {
            const old = rows[i];
            if (old && old.key === keys[i]) return old;
            const spans = sceneSpans(line, tones);
            if (!old) return { key: keys[i], node: sceneRow(spans), spans };
            host.insert(old.node, () => spans, undefined, old.spans);
            return { key: keys[i], node: old.node, spans };
          });
          rows = next;
          drawn = keys.join("\n");
          return rows.map((r) => r.node);
        },
        forget() {
          rows = [];
          drawn = null;
          prepared = null;
          lastSize = null;
          seen = null;
          streaks = [];
        },
        setTones() {
          rows = [];
        },
      };
    }

    // The panel's root box fills the panel; its measured size decides between the orbit and the list.
    function panelRender(panel, watcher, painter, isOurs, keys) {
      try {
        painter.forget(); // a new root: none of the last panel's rows (the host destroyed them with it)
        const [size, setSize] = host.createSignal(null);
        let root = null;
        const measure = function () {
          try {
            const next = measuredSize(this && typeof this === "object" && "height" in this ? this : root);
            if (!next) return;
            const cur = size();
            if (cur && cur.width === next.width && cur.height === next.height) return;
            report("panel size", `${next.width}x${next.height}`); // debug log only
            keys.addViewKey(() => size() || measuredSize(root));
            keys.sized(next);
            setSize(next);
            requestRender();
          } catch (error) {
            report("measure", error);
          }
        };
        root = element("box", {
          width: "100%",
          height: "100%",
          flexGrow: 1,
          flexShrink: 1,
          minHeight: 0,
          flexDirection: "column",
          overflow: "hidden",
          onSizeChange: measure,
          onMouseDown: sceneMouse.down,
          onMouseDrag: sceneMouse.drag,
          onMouseUp: sceneMouse.up,
          onMouseOver: sceneMouse.drag,
        });
        let themeRef = ctx.theme;
        host.insert(root, () =>
          makeView(
            watcher,
            (p) => {
              if (!isOurs(p)) {
                chatFeed.want(null); // hidden: no chat read
                return { hidden: true };
              }
              watcher.poll(true); // stopped again when the panel closes (below) or the plugin is disposed
              if (ctx.theme !== themeRef) {
                themeRef = ctx.theme;
                painter.setTones();
              }
              const known = size() || measuredSize(root);
              panelFits = !!known && sceneFits(known.width, known.height);
              const fits = panelFits && wantScene(p);
              return { session: sessionOf(p), width: panelWidth(p), height: known ? known.height : null, columns: known ? known.width : null, scene: fits ? known : null, painter, list: panelList };
            },
            panel,
          ),
        );
        return root;
      } catch (error) {
        report("render", error);
        try {
          return plainLine(errorMessage(error), WIDTH);
        } catch (_) {
          return null;
        }
      }
    }

    function sessionOf(props) {
      try {
        return props && typeof props.sessionID === "string" && props.sessionID ? props.sessionID : routeSession(ctx);
      } catch (_) {
        return routeSession(ctx);
      }
    }

    // The sidebar.
    const sidebar = createWatcher();
    sidebar.poll(true);
    const sidebarRender = makeRender(sidebar, (props) => ({ session: sessionOf(props), width: WIDTH }));

    const disposers = [];
    const panelLayers = new Set(); // the f/q layers of mounted panels
    const dispose = (unregister) => {
      if (typeof unregister === "function") disposers.push(unregister);
    };

    try {
      if (ctx.ui && typeof ctx.ui.slot === "function") {
        dispose(ctx.ui.slot({ [options.mode]: SLOT, render: sidebarRender }));
      }
    } catch (_) {
      /* no sidebar slot */
    }

    // The panel, on hosts that have session panels.
    const panelApi = ctx.ui && ctx.ui.panel && typeof ctx.ui.panel.open === "function" ? ctx.ui.panel : undefined;
    if (panelApi && typeof ctx.ui.slot === "function") {
      try {
        const panelWatcher = createWatcher(true);
        const isOurs = (panel) => {
          try {
            return !!panel && panel.name === PANEL_NAME;
          } catch (_) {
            return false;
          }
        };
        const painter = createPainter(panelWatcher);
        dispose(
          ctx.ui.slot({
            append: PANEL_SLOT,
            render(panel) {
              let keys = { remove() {}, addViewKey() {}, sized() {} };
              try {
                panelWatcher.reopen();
                setFullByF(false); // the host opens a panel as a side panel
                setChatOpen(false);
                keys = panelKeys(panel);
                if (typeof host.onCleanup === "function") {
                  host.onCleanup(() => {
                    panelWatcher.stop();
                    chatFeed.want(null);
                    keys.remove();
                  });
                }
              } catch (_) {
                /* ignore */
              }
              return panelRender(panel, panelWatcher, painter, isOurs, keys);
            },
          }),
        );
        registerCommand();
        } catch (error) {
          report("panel registration", error);
      }
    }

    // `f` and `q` while this plugin's panel owns input. Called from the panel's render, so the host drops the layer
    // when the panel closes. Without a mode the layer is active only while the panel owns input.
    // Returns { remove, addViewKey, sized }: remove() drops the layers (also run by the plugin's own cleanup; twice is
    // harmless); addViewKey() adds the `g` layer once the panel's size is known, since only then is there an orbit
    // to switch to.
    function panelKeys(panel) {
      if (!hasKeys()) return { remove() {}, addViewKey() {}, sized() {} };
      const ours = () => {
        try {
          return panel.name === PANEL_NAME;
        } catch (_) {
          return false;
        }
      };
      const fullscreenNow = () => {
        try {
          return panel.presentation === "fullscreen";
        } catch (_) {
          return false;
        }
      };
      // `g` switches between the orbit and the list in the panel as it is. It never makes the panel full screen (the
      // user's `f` does, and the host does by itself on a narrow terminal): the orbit compresses to what the panel
      // has (sceneFits), and a panel smaller than that has only the list, so `g` has nothing to switch to.
      const switchView = (currentSize) => {
        const known = currentSize();
        const fits = !!known && sceneFits(known.width, known.height);
        if (fits) setViewMode(wantScene(panel) ? "list" : "scene");
        requestRender();
      };
      // kept for the panel's measure callback: nothing to undo now that `g` never changes the presentation
      const sized = () => {};
      const onList = () => ours() && !rows.scene && rows.items.length > 0 && !dialogOpen();
      const chatIsOpen = () => rows.chat === "open";
      const chatKeyOn = () => onList() && (rows.chat === "open" || (rows.chat === "none" && !!(selectedItem() || {}).open));
      const chatScrollable = () => onList() && (rows.chat === "inline" || rows.chat === "open") && rows.chatMax > 0;
      const toggleChat = () => {
        chatView.scroll = 0;
        setChatOpen(!chatOpen());
        setChatTick((t) => t + 1);
        requestRender();
      };
      const scrollChat = (delta) => {
        const next = Math.max(0, Math.min(rows.chatMax, chatView.scroll + delta));
        if (next === chatView.scroll) return;
        chatView.scroll = next;
        setChatTick((t) => t + 1);
        requestRender();
      };
      // the 3D orbit shows: the arrow keys turn it (left and right in the turn layer, up and down in the layer above)
      const turning = () => ours() && rows.scene && picture() === "orbit" && dimension().view === "3d" && !dialogOpen();
      const act = (name) => () => {
        try {
          panel[name]();
        } catch (_) {
          /* ignore */
        }
      };
      const unregister = ctx.keymap.layer(() => ({
        commands: [
          {
            id: PANEL_NAME + ".fullscreen",
            bind: "f",
            enabled: ours,
            run: () => {
              const told = typeof presentationOf(panel) === "string"; // the host says whether it is full screen
              const before = told ? fullscreenNow() : fullByF();
              act("toggleFullscreen")();
              setFullByF(!before); // `f` only changes the size; the list or the picture stays as it was chosen
              requestRender();
            },
          },
          { id: PANEL_NAME + ".close", bind: "q", enabled: ours, run: act("close") },
          // the list's rows; on the 3D orbit the elevation (turning(), below); nothing on the 2D orbit
          // in the list the rows, or the chat's lines while it is open in place of them; on the 3D orbit the elevation
          { id: PANEL_NAME + ".up", bind: "up", enabled: () => onList() || turning(), run: () => (onList() ? (chatIsOpen() ? scrollChat(1) : moveSelection(-1)) : turning() && turnBy(0, ELEVATION_STEP, "key up")) },
          { id: PANEL_NAME + ".down", bind: "down", enabled: () => onList() || turning(), run: () => (onList() ? (chatIsOpen() ? scrollChat(-1) : moveSelection(1)) : turning() && turnBy(0, -ELEVATION_STEP, "key down")) },
          { id: PANEL_NAME + ".open", bind: "return", enabled: () => onList() && canOpen(), run: () => onList() && openSelected() },
          ...(canStop() ? [{ id: PANEL_NAME + ".stop", bind: "s", enabled: () => onList() && !!(selectedItem() || {}).busy, run: () => onList() && stopSelected() }] : []),
          ...(canMessage() ? [{ id: PANEL_NAME + ".message", bind: "m", enabled: () => onList() && !!(selectedItem() || {}).busy, run: () => onList() && messageSelected() }] : []),
          // the chat: `c` opens it in place of the list's lower part when it does not fit beside it (and closes it, as Escape
          // does); Page Up and Page Down scroll it back and forward whenever it shows
          { id: PANEL_NAME + ".chat", bind: "c", enabled: chatKeyOn, run: () => chatKeyOn() && toggleChat() },
          { id: PANEL_NAME + ".chat-close", bind: "escape", enabled: () => onList() && chatIsOpen(), run: () => onList() && chatIsOpen() && toggleChat() },
          { id: PANEL_NAME + ".chat-back", bind: "pageup", enabled: chatScrollable, run: () => chatScrollable() && scrollChat(Math.max(1, chatView.rows - 1)) },
          { id: PANEL_NAME + ".chat-forward", bind: "pagedown", enabled: chatScrollable, run: () => chatScrollable() && scrollChat(-Math.max(1, chatView.rows - 1)) },
        ],
      }));
      const unregisters = [unregister];
      let removed = false;
      let viewKey = false;
      const remove = () => {
        if (removed) return;
        removed = true;
        if (runKeyWanted === setRunKey) runKeyWanted = null;
        if (askKeyWanted === setAskKey) askKeyWanted = null;
        dropRunKey();
        dropAskKey();
        panelLayers.delete(remove);
        for (const fn of unregisters) {
          try {
            if (typeof fn === "function") fn();
          } catch (_) {
            /* ignore */
          }
        }
      };
      const addViewKey = (currentSize) => {
        if (viewKey || removed) return;
        viewKey = true;
        try {
          unregisters.push(
            ctx.keymap.layer(() => ({
              commands: [
                { id: PANEL_NAME + ".view", bind: "g", enabled: ours, run: () => switchView(currentSize) },
                { id: PANEL_NAME + ".dimension", bind: "d", enabled: () => ours() && picture() === "orbit", run: () => picture() === "orbit" && toggleDimension() },
                { id: PANEL_NAME + ".picture", bind: "o", enabled: ours, run: togglePicture },
              ],
            })),
          );
        } catch (error) {
          report("view key", error);
        }
        // the 3D view turned by hand: left and right round the upright axis in a layer of their own, on only while
        // the 3D orbit shows, with `0` back to the start (up and down, the elevation, share the list's commands)
        try {
          const turnKey = (name, bind, dTurn, dElevation) => ({
            id: PANEL_NAME + ".turn-" + name, bind, enabled: turning, run: () => turning() && turnBy(dTurn, dElevation, "key " + bind),
          });
          unregisters.push(
            ctx.keymap.layer(() => ({
              commands: [
                turnKey("left", "left", TURN_STEP, 0),
                turnKey("right", "right", -TURN_STEP, 0),
                { id: PANEL_NAME + ".turn-reset", bind: "0", enabled: turning, run: () => turning() && resetOrbit() },
              ],
            })),
          );
          orbitKeysOn = true;
        } catch (error) {
          report("turn keys", error);
        }
      };
      // `r` (run a path): registered while the list's snapshot has a path, unregistered when the paths go: no
      // paths, no key. Changed after the render that saw it (a timer), outside the host's render.
      let runKey = false; // wanted
      let runOff = null; // the registered layer's unregister
      const setRunKey = (want) => {
        want = !!want && canRun();
        if (want === runKey || removed) return;
        runKey = want;
        setTimeout(() => {
          if (removed) return;
          if (runKey && !runOff) {
            try {
              runOff = ctx.keymap.layer(() => ({
                commands: [{ id: PANEL_NAME + ".run", bind: "r", enabled: () => ours() && onList() && !runBusy && rows.flows.length > 0, run: () => ours() && onList() && runPath() }],
              }));
            } catch (error) {
              report("run key", error);
            }
          } else if (!runKey && runOff) {
            dropRunKey();
          }
        }, 0);
      };
      const dropRunKey = () => {
        const off = runOff;
        runOff = null;
        try {
          if (typeof off === "function") off();
        } catch (_) {
          /* ignore */
        }
      };
      // `a` (ask the side chat): registered while the list has the side chat's row (it is installed), like `r`; it acts only
      // while that row is selected.
      let askKey = false;
      let askOff = null;
      const setAskKey = (want) => {
        want = !!want && canAsk();
        if (want === askKey || removed) return;
        askKey = want;
        setTimeout(() => {
          if (removed) return;
          if (askKey && !askOff) {
            try {
              askOff = ctx.keymap.layer(() => ({
                commands: [{ id: PANEL_NAME + ".ask", bind: "a", enabled: () => ours() && onList() && !asking() && (selectedItem() || {}).kind === "sidechat", run: () => ours() && onList() && askSide() }],
              }));
            } catch (error) {
              report("ask key", error);
            }
          } else if (!askKey && askOff) {
            dropAskKey();
          }
        }, 0);
      };
      const dropAskKey = () => {
        const off = askOff;
        askOff = null;
        try {
          if (typeof off === "function") off();
        } catch (_) {
          /* ignore */
        }
      };
      askKeyWanted = setAskKey;
      runKeyWanted = setRunKey;
      panelLayers.add(remove);
      return { remove, addViewKey, sized };
    }

    function toggle() {
      try {
        const active = typeof panelApi.current === "function" ? panelApi.current() : undefined;
        if (active && active.name === PANEL_NAME) {
          panelApi.close();
          return;
        }
        if (panelApi.open(PANEL_NAME) === false) {
          if (ctx.ui.toast && typeof ctx.ui.toast.show === "function") {
            ctx.ui.toast.show({ message: "Open a session to see the agent graph.", variant: "info" });
          }
        }
      } catch (_) {
        /* ignore */
      }
    }

    function registerCommand() {
      if (!ctx.keymap || typeof ctx.keymap.layer !== "function") return;
      try {
        dispose(
          ctx.keymap.layer(() => ({
            mode: "global",
            commands: [
              { id: PANEL_NAME, title: "Agent graph", group: "Agents", palette: true, slash: { name: "graph", aliases: ["agents"] }, run: toggle },
            ],
          })),
        );
      } catch (error) {
        report("command registration", error);
      }
    }

    return function cleanup() {
      if (disposed) return;
      disposed = true;
      for (const watcher of watchers) watcher.stop();
      chatFeed.stop();
      sideRunner.stop();
      runner.stop();
      loopRunner.stop();
      for (const remove of [...panelLayers]) remove();
      for (const unregister of disposers) {
        try {
          unregister();
        } catch (_) {
          /* ignore */
        }
      }
    };
  }

  return { id: PLUGIN_ID, setup };
}
