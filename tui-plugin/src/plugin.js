/*
 * The sidebar plugin, written against injected host functions so it can be tested without OpenCode.
 * src/tui.js imports the real ones (@opentui/solid, solid-js, node:*) and default-exports createPlugin(...).
 *
 * One slot on "sidebar.content" (replace by default, or prepend). Every 4 s it runs the snapshot command for
 * the slot's session; a result that arrives after the session changed is dropped. Nothing here throws into
 * the host: a failure is drawn as one line of text.
 */
import { render, isAnimated } from "./render.js";
import { createSnapshotRunner, errorDocument, errorMessage, oneLine } from "./runner.js";

export const PLUGIN_ID = "agentgraph-tui";
export const SLOT = "sidebar.content";
export const WIDTH = 37; // the host's sidebar is 42 columns wide, about 37 usable
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

function pick(value) {
  if (value === undefined || value === null) return undefined;
  if (isPlainObject(value) && !("r" in value) && "default" in value) return pick(value.default);
  return value;
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
export function themeTones(theme) {
  const t = theme && typeof theme === "object" ? theme : {};
  const text = t.text;
  const plain = pick(text) ?? pick(t.foreground);
  const muted = (text && (pick(text.muted) ?? pick(text.subdued))) ?? pick(t.textMuted) ?? plain;
  const candidates = [pick(t.warning), pick(t.accent)].filter((c) => c !== undefined && !isGreen(c));
  candidates.sort((a, b) => hueDistance(a) - hueDistance(b));
  const accent = candidates.length ? { fg: candidates[0], bold: false } : { fg: plain, bold: true };
  const errorColor = pick(t.error) ?? pick(t.danger) ?? pick(t.critical);
  const danger = errorColor !== undefined && !isGreen(errorColor) ? { fg: errorColor, bold: false } : { fg: accent.fg, bold: true };
  return { plain: { fg: plain, bold: false }, muted: { fg: muted, bold: false }, accent, danger };
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

// ---------------------------------------------------------------------------------------------------------------

// host: { createElement, insert, setProp, createSignal, spawn, fs, os, path, env, timers? }
export function createPlugin(host) {
  const timers = host.timers || { setInterval, clearInterval };

  function element(tag, props, children = []) {
    const node = host.createElement(tag);
    for (const [key, value] of Object.entries(props)) if (value !== undefined) host.setProp(node, key, value);
    for (const child of children) if (child !== null && child !== undefined && child !== false) host.insert(node, child);
    return node;
  }

  function lineNode(line, tones) {
    if (!line.length) return element("text", { wrapMode: "none", fg: tones.muted.fg }, [" "]);
    return element(
      "box",
      { flexDirection: "row", width: "100%", flexShrink: 0 },
      line.map((span) => {
        const tone = tones[span.tone] || tones.plain;
        return element("text", { fg: tone.fg, attributes: tone.bold ? BOLD : undefined, wrapMode: "none", flexShrink: 0 }, [span.text]);
      }),
    );
  }

  function setup(ctx, secondOptions) {
    ctx = ctx || {};
    const options = resolveOptions(isPlainObject(ctx.options) ? ctx.options : secondOptions, host);
    const runner = createSnapshotRunner({
      spawn: host.spawn,
      fs: host.fs,
      path: host.path,
      env: host.env,
      home: options.home,
      python: options.python,
      demo: options.demo,
    });

    const [state, setState] = host.createSignal({ session: undefined, doc: null });
    const [frame, setFrame] = host.createSignal(0);
    let current; // the session being watched
    let generation = 0;
    let disposed = false;
    let spinTimer = null;

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

    function spinner(doc) {
      const live = !disposed && isAnimated(doc);
      if (live && !spinTimer) {
        spinTimer = timers.setInterval(() => {
          setFrame((f) => f + 1);
          requestRender();
        }, SPINNER_MS);
      } else if (!live && spinTimer) {
        timers.clearInterval(spinTimer);
        spinTimer = null;
      }
    }

    function refresh() {
      if (disposed || !current) return Promise.resolve();
      const session = current;
      const asked = generation;
      return runner
        .snapshot({ session, directory: directory() })
        .catch((error) => errorDocument(errorMessage(error)))
        .then((doc) => {
          if (disposed || asked !== generation || session !== current) return; // late result: drop it
          setState({ session, doc });
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

    const pollTimer = timers.setInterval(() => {
      refresh();
    }, options.intervalMs);

    function view(props) {
      let tones = { plain: {}, muted: {}, accent: {}, danger: {} };
      try {
        tones = themeTones(ctx.theme);
        let session;
        try {
          session = props && typeof props.sessionID === "string" && props.sessionID ? props.sessionID : routeSession(ctx);
        } catch (_) {
          session = routeSession(ctx);
        }
        watch(session);
        const snap = state();
        let lines;
        if (!session) lines = [[{ text: "No session", tone: "muted" }]];
        else if (snap.session !== session || !snap.doc) lines = render(null, { width: WIDTH });
        else lines = render(snap.doc, { width: WIDTH, frame: frame() });
        return lines.map((line) => lineNode(line, tones));
      } catch (error) {
        try {
          return [lineNode(render(errorDocument(errorMessage(error)), { width: WIDTH })[0], tones)];
        } catch (_) {
          return [];
        }
      }
    }

    function slotRender(props) {
      try {
        const root = element("box", { width: "100%", flexDirection: "column", flexShrink: 0 });
        host.insert(root, () => view(props));
        return root;
      } catch (error) {
        try {
          return element("text", { wrapMode: "none" }, [oneLine(errorMessage(error)).slice(0, WIDTH)]);
        } catch (_) {
          return null;
        }
      }
    }

    let unslot;
    try {
      if (ctx.ui && typeof ctx.ui.slot === "function") {
        unslot = ctx.ui.slot({ [options.mode]: SLOT, render: slotRender });
      }
    } catch (_) {
      unslot = undefined;
    }

    return function cleanup() {
      if (disposed) return;
      disposed = true;
      timers.clearInterval(pollTimer);
      if (spinTimer) timers.clearInterval(spinTimer);
      spinTimer = null;
      runner.stop();
      try {
        if (typeof unslot === "function") unslot();
      } catch (_) {
        /* ignore */
      }
    };
  }

  return { id: PLUGIN_ID, setup };
}
