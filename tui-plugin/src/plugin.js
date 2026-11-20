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
 * The panel opens on the list. It draws the orbit (src/scene.js) when asked: `g`, or `f` (full screen), and only when
 * it has at least 64 columns and 22 rows; its size is measured from the panel's own box (OpenTUI `onSizeChange`,
 * `width`, `height`), because the documented panel input has no height. `d` switches the orbit between its 2D view
 * (the ring from above, braille dots) and its 3D view (a turning figure in depth characters). In the list, ↑/↓ select
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
import { render, layout, isAnimated, truncate as truncateText, flowLabel, flowName, flowStatsText } from "./render.js";
import {
  scene, sceneFits, frameInterval, changeAt, orbitTurned, orbitReset, TURN_STEP, ELEVATION_STEP, DRAG_TURN_PER_CELL, DRAG_ELEVATION_PER_ROW,
} from "./scene.js";
import { createSnapshotRunner, errorDocument, errorMessage, oneLine, runFlowPrompt, GOAL_LIMIT } from "./runner.js";
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
  return { home, python, intervalMs, mode, demo: options.demo === true };
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
  for (const span of line) key += span.tone + "\u0001" + (span.color || "") + "\u0001" + span.text + "\u0002";
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

  // `fixed`: an orbit line, never selected with the mouse (a press and a drag there turn the 3D view instead)
  function lineNode(line, tones, fixed = false) {
    const selectable = fixed ? false : undefined;
    if (!line.length) return element("text", { wrapMode: "none", fg: colorProp(tones.muted.fg), selectable }, [" "]);
    return element(
      "box",
      { flexDirection: "row", width: "100%", flexShrink: 0 },
      line.map((span) => {
        const tone = tones[span.tone] || tones.plain;
        const own = span.color ? colorProp(span.color) : undefined; // the orbit's group colours
        return element("text", { fg: own || colorProp(tone.fg), attributes: !own && tone.bold ? BOLD : undefined, wrapMode: "none", flexShrink: 0, selectable }, [span.text]);
      }),
    );
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
      ? createDemoRunner({ fs: host.fs, file: host.demoFile, now: clock, paths: String(host.env.AGENTGRAPH_TUI_DEMO).trim() === "paths" })
      : createSnapshotRunner({
          spawn: host.spawn,
          fs: host.fs,
          path: host.path,
          env: host.env,
          home: options.home,
          python: options.python,
          demo: options.demo,
        });

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

    // One watcher follows one session: its latest document, the spinner frame, its timers. The sidebar and the
    // panel each own one; both go through the same runner, which runs one process per session at a time.
    function createWatcher() {
      const [state, setState] = host.createSignal({ session: undefined, doc: null });
      const [frame, setFrame] = host.createSignal(0);
      let current; // the session being watched
      let generation = 0;
      let spinTimer = null;
      let spinMs = 0;
      let pollTimer = null;
      let stopped = false; // set when the panel closes; a late fetch or a re-run view must not revive the timers
      let sceneOn = false; // the panel draws the orbit: frames at 100 ms while work moves, 250 ms (2D) or 166 ms (3D) idle
      const sceneInterval = (doc) => {
        try {
          return typeof watcher.sceneInterval === "function" ? watcher.sceneInterval(doc) : frameInterval(doc);
        } catch (_) {
          return frameInterval(doc);
        }
      };

      // The frame timer. The list animates only while something works (a spinner every 200 ms); the orbit always
      // moves while it is shown. Each tick asks `beforeTick` (the orbit's painter) whether the frame changed.
      function spinner(doc) {
        let ms = 0;
        if (!disposed && !stopped) {
          if (sceneOn) ms = isPlainObject(doc) && (doc.error === undefined || doc.error === null) ? sceneInterval(doc) : 0;
          else ms = isAnimated(doc) ? SPINNER_MS : 0;
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
        return runner
          .snapshot({ session, directory: directory() })
          .catch((error) => errorDocument(errorMessage(error)))
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
          const first = layout(snap.doc, { width, frame: watcher.frame(), now: clock(), requests, cursor: !!listOptions.cursor, selected });
          let result = first;
          if (typeof listOptions.keys === "function") {
            const item = first.items.find((i) => i.key === first.selected) || null;
            result = layout(snap.doc, { width, frame: watcher.frame(), now: clock(), requests, cursor: !!listOptions.cursor, selected, keys: listOptions.keys(item, first.items, first) });
          }
          result.session = session;
          if (typeof listOptions.onLayout === "function") listOptions.onLayout(result);
          lines = result.lines;
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

    // The view chosen with `g` in the panel, remembered while the app runs: "auto" (the list, or the orbit while `f`
    // made the panel full screen), "scene" or "list".
    const [viewMode, setViewMode] = host.createSignal("auto");
    const [fullByF, setFullByF] = host.createSignal(false); // the user made the panel full screen with `f`
    // The orbit is wanted after `g`, or while the panel is full screen by `f` (not once the host has made it a side
    // panel again by its own key: then `presentation` says "panel").
    const presentationOf = (p) => {
      try {
        return p ? p.presentation : undefined;
      } catch (_) {
        return undefined;
      }
    };
    const wantScene = (p) => viewMode() === "scene" || (viewMode() === "auto" && fullByF() && presentationOf(p) !== "panel");

    // The list's rows in the panel: the selected row's key (remembered while the app runs) and the rows last drawn.
    const [selection, setSelection] = host.createSignal(null);
    const rows = { items: [], selected: null, scene: false, flows: [], root: null, session: null };
    const has = (fn) => typeof fn === "function";
    const canOpen = () => !!(ctx.ui && ctx.ui.router && has(ctx.ui.router.navigate));
    const canStop = () => !!(ctx.client && ctx.client.session && has(ctx.client.session.interrupt) && ctx.ui && ctx.ui.dialog && has(ctx.ui.dialog.confirm));
    const canMessage = () => !!(ctx.client && ctx.client.session && has(ctx.client.session.prompt) && ctx.ui && ctx.ui.dialog && has(ctx.ui.dialog.prompt));
    const canRun = canMessage; // the goal's prompt and the send are the calls `m` uses; the chooser is optional
    let runBusy = false; // a path is being run: from `r` until it was sent or given up
    const [runShown, setRunShown] = host.createSignal(true); // the key line's `r run`: hidden while runBusy
    const setRunBusy = (busy) => {
      runBusy = busy;
      setRunShown(!busy);
      requestRender();
    };
    let runKeyWanted = null; // the mounted panel's setRunKey(want): `r` is there while the snapshot has a path
    // The key line for the selected row: only keys that do something there.
    const keyPieces = (item, items, result) => {
      const pieces = [];
      if (Array.isArray(items) && items.length > 1) pieces.push(["↑↓", "select"]);
      if (item && item.open && canOpen()) pieces.push(["⏎", "open"]);
      if (item && item.busy && canStop()) pieces.push(["s", "stop"]);
      if (item && item.busy && canMessage()) pieces.push(["m", "message"]);
      if (result && Array.isArray(result.flows) && result.flows.length && canRun() && runShown()) pieces.push(["r", "run"]);
      pieces.push(["g", "graph"], ["q", "close"]);
      return pieces;
    };
    const panelList = {
      cursor: true,
      selected: () => selection(),
      keys: (item, items, result) => (ctx.keymap && has(ctx.keymap.layer) ? keyPieces(item, items, result) : []),
      onLayout(result) {
        rows.items = Array.isArray(result.items) ? result.items : [];
        rows.selected = result.selected || null;
        rows.scene = !!result.scene;
        rows.flows = Array.isArray(result.flows) ? result.flows : [];
        rows.root = typeof result.root === "string" ? result.root : null;
        rows.session = typeof result.session === "string" ? result.session : null;
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
          return Promise.resolve(ctx.client.session.prompt({ sessionID, text: text.trim(), delivery: "steer" })).then(() => toast(`Sent to ${item.name}`));
        })
        .catch((error) => {
          report("message", error);
          toast(`Could not message ${item.name}: ${errorMessage(error)}`, "error");
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
    // The terminal's size, which is the panel's size in full screen; null when the host does not say.
    const screenSize = () => {
      try {
        const out = typeof host.screenSize === "function" ? host.screenSize() : globalThis.process && globalThis.process.stdout;
        const width = Math.floor(Number(out && (out.width !== undefined ? out.width : out.columns)));
        const height = Math.floor(Number(out && (out.height !== undefined ? out.height : out.rows)));
        return width > 0 && height > 0 ? { width, height } : null;
      } catch (_) {
        return null;
      }
    };
    // The orbit's view chosen with `d`, remembered while the app runs; it opens in 2D. A switch moves the camera
    // over CHANGE_MS from the old view to the new one.
    const [dimension, setDimension] = host.createSignal({ view: "2d", change: null });
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
    // ignores the mouse, as before. OpenTUI sends a drag's motion to the line that was pressed, and a turn redraws
    // (replaces) that line, after which its drags go nowhere; the line under the pointer still gets "over" with the
    // button held (onMouseOver), which carries the drag on.
    let press = null; // { x, y, lastX, lastY, dragged } from the left button's press on the orbit
    const orbitShown = () => rows.scene && !dialogOpen();
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
    let orbitKeysOn = false;
    // The orbit's frames: rows are reused while their text and tones stay the same, and a tick whose frame equals
    // the last one drawn pushes nothing to the host.
    function createPainter(watcher) {
      let rows = []; // [{ key, node }]
      let drawn = null; // the key of the last frame drawn
      let prepared = null; // a frame the tick computed, for the view to use as it is
      let colors = sceneColors(ctx.theme, null);
      const viewOptions = () => {
        const d = dimension();
        const o = orbit();
        return Object.assign({ view: d.view, change: d.change, colors }, o ? { orbit: o } : {}, orbitKeysOn ? { keys: ORBIT_KEYS } : {});
      };
      const compute = (snap, size) =>
        scene(snap.doc, Object.assign(viewOptions(), { width: size.width, height: size.height, now: clock(), previous: snap.previous ? { doc: snap.previous, at: snap.at } : undefined }));
      watcher.sceneInterval = (doc) => frameInterval(doc, viewOptions(), clock());
      let lastSize = null;
      watcher.beforeTick = () => {
        if (!lastSize) return true;
        const snap = watcher.state();
        if (!snap.doc) return true;
        const lines = compute(snap, lastSize);
        const key = lines.map(lineKey).join("\n");
        if (key === drawn) return false;
        prepared = { doc: snap.doc, width: lastSize.width, height: lastSize.height, lines, dimension: dimension(), orbit: orbit() };
        return true;
      };
      return {
        draw(snap, size, tones) {
          lastSize = size;
          colors = sceneColors(ctx.theme, tones);
          let lines;
          const same = prepared && prepared.doc === snap.doc && prepared.width === size.width && prepared.height === size.height;
          if (same && prepared.dimension === dimension() && prepared.orbit === orbit()) lines = prepared.lines;
          else lines = compute(snap, size);
          prepared = null;
          const keys = lines.map(lineKey);
          const next = lines.map((line, i) => (rows[i] && rows[i].key === keys[i] ? rows[i] : { key: keys[i], node: lineNode(line, tones, true) }));
          rows = next;
          drawn = keys.join("\n");
          return rows.map((r) => r.node);
        },
        forget() {
          rows = [];
          drawn = null;
          prepared = null;
          lastSize = null;
        },
        setTones() {
          rows = [];
        },
      };
    }

    // The panel's root box fills the panel; its measured size decides between the orbit and the list.
    function panelRender(panel, watcher, painter, isOurs, keys) {
      try {
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
              if (!isOurs(p)) return { hidden: true };
              watcher.poll(true); // stopped again when the panel closes (below) or the plugin is disposed
              if (ctx.theme !== themeRef) {
                themeRef = ctx.theme;
                painter.setTones();
              }
              const known = size() || measuredSize(root);
              const fits = !!known && wantScene(p) && sceneFits(known.width, known.height);
              return { session: sessionOf(p), width: panelWidth(p), scene: fits ? known : null, painter, list: panelList };
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
        const panelWatcher = createWatcher();
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
                keys = panelKeys(panel);
                if (typeof host.onCleanup === "function") {
                  host.onCleanup(() => {
                    panelWatcher.stop();
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
      } catch (_) {
        /* no panel */
      }
    }

    // `f` and `q` while this plugin's panel owns input. Called from the panel's render, so the host drops the layer
    // when the panel closes. Without a mode the layer is active only while the panel owns input.
    // Returns { remove, addViewKey, sized }: remove() drops the layers (also run by the plugin's own cleanup; twice is
    // harmless); addViewKey() adds the `g` layer once the panel's size is known, since only then is there an orbit
    // to switch to.
    function panelKeys(panel) {
      if (!ctx.keymap || typeof ctx.keymap.layer !== "function") return { remove() {}, addViewKey() {}, sized() {} };
      const ours = () => {
        try {
          return panel.name === PANEL_NAME;
        } catch (_) {
          return false;
        }
      };
      // `g` shows the orbit and the panel is too small for it as it is, but the screen is not: `g` goes full screen.
      // enteredFullscreen: it was `g` that did, so `g` back to the list leaves full screen again.
      let enteredFullscreen = false;
      let unverified = false; // the screen's size was not known: check the orbit fits once the panel is measured
      const fullscreenNow = () => {
        try {
          return panel.presentation === "fullscreen";
        } catch (_) {
          return false;
        }
      };
      const fullscreenToggle = () => {
        try {
          if (typeof panel.toggleFullscreen === "function") {
            panel.toggleFullscreen();
            return true;
          }
        } catch (_) {
          /* ignore */
        }
        return false;
      };
      const leaveFullscreen = () => {
        if (enteredFullscreen && fullscreenNow()) fullscreenToggle();
        enteredFullscreen = false;
        unverified = false;
      };
      // `g` always changes what the panel shows, unless the orbit cannot fit even full screen.
      const switchView = (currentSize) => {
        const known = currentSize();
        const orbitShown = wantScene(panel) && !!known && sceneFits(known.width, known.height);
        if (orbitShown) {
          setViewMode("list");
          leaveFullscreen();
        } else if (known && sceneFits(known.width, known.height)) {
          setViewMode("scene");
        } else if (fullscreenNow()) {
          // already full screen and still too small: nothing to show
        } else {
          const screen = screenSize();
          if (!screen || sceneFits(screen.width, screen.height)) {
            if (fullscreenToggle()) {
              enteredFullscreen = true;
              unverified = !screen;
              setViewMode("scene");
            }
          }
        }
        requestRender();
      };
      // The panel was measured: when `g` guessed that full screen would fit and it does not, go back.
      const sized = (next) => {
        if (!unverified || !enteredFullscreen || !fullscreenNow()) return;
        unverified = false;
        if (!sceneFits(next.width, next.height)) leaveFullscreen();
      };
      const onList = () => ours() && !rows.scene && rows.items.length > 0 && !dialogOpen();
      // the 3D orbit shows: the arrow keys turn it (left and right in the turn layer, up and down in the layer above)
      const turning = () => ours() && rows.scene && dimension().view === "3d" && !dialogOpen();
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
              enteredFullscreen = false; // full screen is the user's now: `g` no longer leaves it
              const told = typeof presentationOf(panel) === "string"; // the host says whether it is full screen
              const before = told ? fullscreenNow() : fullByF();
              act("toggleFullscreen")();
              setFullByF(!before); // full screen by `f` shows the orbit (when it fits); back to a side panel, the list
              setViewMode("auto");
              requestRender();
            },
          },
          { id: PANEL_NAME + ".close", bind: "q", enabled: ours, run: act("close") },
          // the list's rows; on the 3D orbit the elevation (turning(), below); nothing on the 2D orbit
          { id: PANEL_NAME + ".up", bind: "up", enabled: () => onList() || turning(), run: () => (onList() ? moveSelection(-1) : turning() && turnBy(0, ELEVATION_STEP, "key up")) },
          { id: PANEL_NAME + ".down", bind: "down", enabled: () => onList() || turning(), run: () => (onList() ? moveSelection(1) : turning() && turnBy(0, -ELEVATION_STEP, "key down")) },
          { id: PANEL_NAME + ".open", bind: "return", enabled: () => onList() && canOpen(), run: () => onList() && openSelected() },
          ...(canStop() ? [{ id: PANEL_NAME + ".stop", bind: "s", enabled: () => onList() && !!(selectedItem() || {}).busy, run: () => onList() && stopSelected() }] : []),
          ...(canMessage() ? [{ id: PANEL_NAME + ".message", bind: "m", enabled: () => onList() && !!(selectedItem() || {}).busy, run: () => onList() && messageSelected() }] : []),
        ],
      }));
      const unregisters = [unregister];
      let removed = false;
      let viewKey = false;
      const remove = () => {
        if (removed) return;
        removed = true;
        if (runKeyWanted === setRunKey) runKeyWanted = null;
        dropRunKey();
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
                { id: PANEL_NAME + ".dimension", bind: "d", enabled: ours, run: toggleDimension },
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
      } catch (_) {
        /* no command */
      }
    }

    return function cleanup() {
      if (disposed) return;
      disposed = true;
      for (const watcher of watchers) watcher.stop();
      runner.stop();
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
