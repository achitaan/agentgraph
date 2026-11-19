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
 */
import { render, isAnimated } from "./render.js";
import { createSnapshotRunner, errorDocument, errorMessage, oneLine } from "./runner.js";

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

// host: { createElement, insert, setProp, createSignal, onCleanup?, spawn, fs, os, path, env, timers? }
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
      let pollTimer = null;
      let stopped = false; // set when the panel closes; a late fetch or a re-run view must not revive the timers

      function spinner(doc) {
        const live = !disposed && !stopped && isAnimated(doc);
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
        if (disposed || stopped || !current) return Promise.resolve();
        const session = current;
        const asked = generation;
        return runner
          .snapshot({ session, directory: directory() })
          .catch((error) => errorDocument(errorMessage(error)))
          .then((doc) => {
            if (disposed || stopped || asked !== generation || session !== current) return; // late result: drop it
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
      }

      // The panel is mounted again: start from a clean slate.
      function reopen() {
        if (!stopped) return;
        stopped = false;
        current = undefined;
      }

      const watcher = { state, frame, watch, poll, stop, reopen };
      watchers.push(watcher);
      return watcher;
    }

    // `resolve(props)` -> { session, width }, or { hidden: true } to draw nothing.
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
        let lines;
        if (!session) lines = [[{ text: "No session", tone: "muted" }]];
        else if (snap.session !== session || !snap.doc) lines = render(null, { width });
        else lines = render(snap.doc, { width, frame: watcher.frame() });
        return lines.map((line) => lineNode(line, tones));
      } catch (error) {
        try {
          return [lineNode(render(errorDocument(errorMessage(error)), { width })[0], tones)];
        } catch (_) {
          return [];
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
          try {
            return element("text", { wrapMode: "none" }, [oneLine(errorMessage(error)).slice(0, WIDTH)]);
          } catch (_) {
            return null;
          }
        }
      };
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
        const panelRender = makeRender(panelWatcher, (panel) => {
          if (!isOurs(panel)) return { hidden: true };
          panelWatcher.poll(true); // stopped again when the panel closes (below) or the plugin is disposed
          return { session: sessionOf(panel), width: panelWidth(panel) };
        });
        dispose(
          ctx.ui.slot({
            append: PANEL_SLOT,
            render(panel) {
              try {
                panelWatcher.reopen();
                const dropKeys = panelKeys(panel);
                if (typeof host.onCleanup === "function") {
                  host.onCleanup(() => {
                    panelWatcher.stop();
                    dropKeys();
                  });
                }
              } catch (_) {
                /* ignore */
              }
              return panelRender(panel);
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
    // Returns a function that removes the layer; it is also run by the plugin's own cleanup, and twice is harmless.
    function panelKeys(panel) {
      if (!ctx.keymap || typeof ctx.keymap.layer !== "function") return () => {};
      const ours = () => {
        try {
          return panel.name === PANEL_NAME;
        } catch (_) {
          return false;
        }
      };
      const act = (name) => () => {
        try {
          panel[name]();
        } catch (_) {
          /* ignore */
        }
      };
      const unregister = ctx.keymap.layer(() => ({
        commands: [
          { id: PANEL_NAME + ".fullscreen", bind: "f", enabled: ours, run: act("toggleFullscreen") },
          { id: PANEL_NAME + ".close", bind: "q", enabled: ours, run: act("close") },
        ],
      }));
      let removed = false;
      const remove = () => {
        if (removed) return;
        removed = true;
        panelLayers.delete(remove);
        try {
          if (typeof unregister === "function") unregister();
        } catch (_) {
          /* ignore */
        }
      };
      panelLayers.add(remove);
      return remove;
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
