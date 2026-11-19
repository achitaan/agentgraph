/*
 * agentgraph-desktop: OpenCode Desktop GUI plugin that adds an "Agent graph" tab to a session's side panel.
 *
 * Hand-written CommonJS, no JSX, no build dependencies. build.mjs wraps this file, together with
 * ../agentgraph/web/app.js and ../agentgraph/web/style.css, into ../gui-experimental.cjs.
 *
 * The desktop app evaluates that one file in two processes (see the GUI plugin guide, "One file, two processes"):
 *   - every window runs `setup`: the panel, the "+" menu item, the command and the styles;
 *   - the Electron main process runs `main`: an Ipc that runs the Python snapshot command.
 * The host evaluates the bundle as `Function("require", "module", "exports", code)`, so `__dirname` and
 * `__filename` do not exist, and `require` only knows the modules the app shares. Keep each process's code inside
 * its own setup; module scope must stay safe in both.
 *
 * Everything that comes from the snapshot document is untrusted text. This file never builds markup from it:
 * it only sets `textContent` on its own message element and hands the parsed document to the view.
 */
"use strict";

const gui = require("@opencode/plugin/gui");
const effect = require("effect");

// Filled in by build.mjs. When this source runs on its own (no bundle), fall back to a global view, no CSS.
const ASSETS =
  typeof AGENTGRAPH_ASSETS !== "undefined" // eslint-disable-line no-undef
    ? AGENTGRAPH_ASSETS // eslint-disable-line no-undef
    : {
        css: "",
        view: "global",
        loadView: () => (typeof window !== "undefined" && window.AgentGraph) || null,
      };

const TAB_ID = "graph";
const PANEL_ID = "graph";
const COMMAND_ID = "open";
const MENU_ID = "open";
const IPC_SUFFIX = "snapshot";

const SNAPSHOT_TIMEOUT_MS = 20000;
const SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024;
const DEFAULT_INTERVAL_MS = 4000;
const ERROR_MAX_CHARS = 300;
const SESSION_ID = /^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$/;

const HOST_CSS = [
  ".agentgraph-desktop-panel,.agentgraph-desktop-view{box-sizing:border-box;width:100%;height:100%;min-width:0;min-height:0;overflow:hidden}",
  ".agentgraph-desktop-panel>.agentgraph-desktop-message{box-sizing:border-box;padding:12px;opacity:.8;white-space:pre-wrap;overflow-wrap:anywhere}",
].join("\n");

const GRAPH_ICON =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none">' +
  '<circle cx="8" cy="3.5" r="2" stroke="currentColor"/>' +
  '<circle cx="3.5" cy="12.5" r="2" stroke="currentColor"/>' +
  '<circle cx="12.5" cy="12.5" r="2" stroke="currentColor"/>' +
  '<path d="M7 5.3 4.5 10.7M9 5.3l2.5 5.4M5.5 12.5h5" stroke="currentColor" stroke-linecap="round"/>' +
  "</svg>";

const MESSAGES = {
  "tab.title": "Agent graph",
  "menu.title": "Agent graph",
  "command.open": "Open agent graph",
  "message.noView": "The agent graph view did not load.",
  "message.viewFailed": "The agent graph view failed to start: {{error}}",
  "message.sessionCopied": "Session ID copied",
  "message.sessionID": "Session ID",
};

// ---------------------------------------------------------------------------------------------------------------
// Shared helpers (pure; safe in both processes)

function oneLine(value) {
  const text = String(value === undefined || value === null ? "" : value)
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return "unknown error";
  return text.length > ERROR_MAX_CHARS ? text.slice(0, ERROR_MAX_CHARS - 1) + "…" : text;
}

function errorMessage(error) {
  if (error && typeof error === "object" && typeof error.message === "string") return error.message;
  return String(error);
}

function errorDocument(message) {
  return { version: 1, error: oneLine(message) };
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// The snapshot document as text -> an object with version 1, else an error document.
function parseDocument(text) {
  if (typeof text !== "string" || !text.trim()) return errorDocument("The snapshot command printed nothing.");
  let doc;
  try {
    doc = JSON.parse(text);
  } catch (_) {
    return errorDocument("The snapshot command returned invalid JSON. Check that options.python runs agentgraph.snapshot_json with Python 3.11 or newer.");
  }
  if (!isPlainObject(doc)) return errorDocument("The snapshot is not a JSON object.");
  if (doc.version !== 1) return errorDocument("Unsupported snapshot version: " + JSON.stringify(doc.version));
  if (doc.error !== undefined && doc.error !== null) doc.error = oneLine(doc.error);
  return doc;
}

// One Ipc token per plugin id. The id is the host-given ctx.id (the package name), so the token always matches the
// id the host enforces, whatever the plugin directory is called. Both processes build the same token from it.
const ipcTokens = new Map();
function snapshotIpc(pluginId) {
  let token = ipcTokens.get(pluginId);
  if (!token) {
    const Schema = effect.Schema;
    token = gui.Ipc.define({
      id: pluginId + "." + IPC_SUFFIX,
      methods: {
        // Input: the session's directory and id. Output: the snapshot document as JSON text, so any document
        // (including an error document) crosses the bridge without a schema for its whole shape.
        snapshot: {
          input: Schema.Struct({ directory: Schema.String, session: Schema.String }),
          output: Schema.String,
        },
      },
    });
    ipcTokens.set(pluginId, token);
  }
  return token;
}

// ---------------------------------------------------------------------------------------------------------------
// Main process: run `python3 -P -m agentgraph.snapshot_json` without a shell

function createSnapshotRunner(deps) {
  // deps: { execFile, fs, path, env, home, python, demo, signal, timeoutMs, maxBytes }
  const inflight = new Map();

  function validate(input) {
    if (!isPlainObject(input)) return { error: "Bad snapshot request." };
    const directory = input.directory;
    const session = input.session === undefined || input.session === null ? "" : input.session;
    if (typeof directory !== "string" || !directory || directory.length > 4096 || directory.includes("\0"))
      return { error: "The session has no usable directory." };
    if (!deps.path.isAbsolute(directory)) return { error: "The session directory is not an absolute path." };
    if (typeof session !== "string" || (session && !SESSION_ID.test(session)))
      return { error: "The session id is not valid." };
    return { directory: deps.path.resolve(directory), session };
  }

  function processFailure(error, stderr) {
    if (error.name === "AbortError" || error.code === "ABORT_ERR") return "The plugin stopped.";
    if (error.code === "ENOENT")
      return "Python was not found at " + deps.python + ". Set options.python in opencode.json to a Python 3.11 or newer executable.";
    if (error.code === "EACCES") return "Python is not executable: " + deps.python + ".";
    if (error.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER")
      return "The snapshot was larger than " + Math.round(deps.maxBytes / 1048576) + " MiB.";
    if (error.killed && error.signal)
      return "The snapshot command did not finish within " + Math.round(deps.timeoutMs / 1000) + " s.";
    if (/Unknown option:[ \t]+-P\b/i.test(stderr || ""))
      return "Python 3.11 or newer is required. Set options.python to a newer executable; " + deps.python + " does not support -P.";
    return null;
  }

  function lastLine(text) {
    return String(text || "").split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .pop();
  }

  function finish(error, stdout, stderr) {
    // Partial output must not hide a timeout, output overflow, or interpreter/startup failure.
    if (error) {
      const failure = processFailure(error, stderr);
      if (failure) return errorDocument(failure);
    }
    const last = lastLine(stdout);
    if (!error) return last ? parseDocument(last) : errorDocument(lastLine(stderr) || "The snapshot command printed nothing.");
    // The command prints its own error document when it fails ({"version": 1, "error": ...}, exit 1): keep it.
    const parsed = last ? safeParse(last) : undefined;
    if (isPlainObject(parsed) && parsed.version === 1 && parsed.error !== undefined && parsed.error !== null) {
      return parseDocument(last);
    }
    const status = typeof error.code === "number" ? "exited with status " + error.code
      : error.signal ? "was terminated by " + error.signal : "failed";
    const detail = lastLine(stderr) || "Check options.python and that agentgraph is installed.";
    return errorDocument("The snapshot command " + status + ": " + detail);
  }

  function safeParse(text) {
    try {
      return JSON.parse(text);
    } catch (_) {
      return undefined;
    }
  }

  function run(request) {
    return new Promise((resolve) => {
      deps.fs.stat(request.directory, (statError, stat) => {
        if (statError || !stat.isDirectory()) {
          resolve(errorDocument("The session directory does not exist: " + request.directory));
          return;
        }
        const args = ["-P", "-m", "agentgraph.snapshot_json"];
        if (deps.demo) args.push("--demo");
        if (request.session) args.push("--session=" + request.session);
        const env = Object.assign({}, deps.env, { PYTHONPATH: deps.home, PYTHONIOENCODING: "utf-8" });
        const options = {
          cwd: request.directory,
          env,
          encoding: "utf8",
          timeout: deps.timeoutMs,
          killSignal: "SIGKILL",
          maxBuffer: deps.maxBytes,
          windowsHide: true,
        };
        if (deps.signal) options.signal = deps.signal;
        try {
          deps.execFile(deps.python, args, options, (error, stdout, stderr) => {
            try {
              resolve(finish(error, stdout, stderr));
            } catch (failure) {
              resolve(errorDocument(errorMessage(failure)));
            }
          });
        } catch (error) {
          resolve(errorDocument(processFailure(error, "") || "Could not start Python: " + errorMessage(error)));
        }
      });
    });
  }

  return {
    // Never rejects: failures become { version: 1, error }.
    async snapshot(input) {
      try {
        const request = validate(input);
        if (request.error) return errorDocument(request.error);
        const key = request.directory + "\n" + request.session;
        let pending = inflight.get(key);
        if (!pending) {
          pending = run(request).finally(() => {
            if (inflight.get(key) === pending) inflight.delete(key);
          });
          inflight.set(key, pending);
        }
        return await pending;
      } catch (error) {
        return errorDocument("Could not collect the snapshot: " + errorMessage(error));
      }
    },
    inflight: () => inflight.size,
  };
}

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() !== "";
}

function main(ctx) {
  const childProcess = require("node:child_process");
  const fs = require("node:fs");
  const os = require("node:os");
  const path = require("node:path");
  const options = isPlainObject(ctx.options) ? ctx.options : {};
  const expandHome = (value) => (value === "~" || value.startsWith("~/") ? path.join(os.homedir(), value.slice(1)) : value);

  const home = nonEmptyString(options.home)
    ? path.resolve(expandHome(options.home.trim()))
    : path.join(os.homedir(), ".config", "opencode", "orchestration");
  const python = nonEmptyString(options.python) ? expandHome(options.python.trim()) : "python3";

  const runner = createSnapshotRunner({
    execFile: childProcess.execFile,
    fs,
    path,
    env: process.env,
    home,
    python,
    demo: options.demo === true,
    signal: ctx.signal,
    timeoutMs: SNAPSHOT_TIMEOUT_MS,
    maxBytes: SNAPSHOT_MAX_BYTES,
  });

  ctx.provide(snapshotIpc(ctx.id), {
    snapshot: (input) => runner.snapshot(input).then((doc) => JSON.stringify(doc)),
  });

  if (ctx.log && typeof ctx.log.write === "function") {
    ctx.log.write("info", "agentgraph snapshot bridge ready", { home, python });
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Windows: panel, menu item, command, styles

let viewCache;
let viewFailure;
function loadView() {
  if (viewCache === undefined) {
    try {
      viewCache = ASSETS.loadView() || null;
    } catch (error) {
      viewCache = null;
      viewFailure = error;
    }
  }
  return viewCache;
}

function showMessage(host, text) {
  const doc = host.ownerDocument || document;
  const message = doc.createElement("div");
  message.className = "agentgraph-desktop-message";
  message.textContent = text; // text only, never markup
  host.appendChild(message);
}

function renderPanel(ctx, solid, props) {
  // A panel's render returns a Solid JSX.Element; a DOM node is one, and Solid inserts it as it is.
  const host = document.createElement("div");
  host.className = "agentgraph-desktop-panel";

  let alive = true;
  let handle = null;

  // Read props.session where it is used (never destructured), so a session switch in the same panel is followed.
  const sessionRequest = () => {
    const session = props.session;
    if (!session) return null;
    return {
      directory: typeof session.directory === "string" ? session.directory : "",
      session: typeof session.id === "string" ? session.id : "",
    };
  };
  const sessionKey = () => {
    const request = sessionRequest();
    return request ? request.directory + "\n" + request.session : undefined;
  };

  const ask = async (request) => {
    const text = await ctx.main(snapshotIpc(ctx.id)).snapshot(request, { signal: ctx.signal });
    return parseDocument(text);
  };

  const fetchSnapshot = async () => {
    if (!alive) return errorDocument("The panel is closed.");
    try {
      const request = sessionRequest();
      if (!request || !request.directory) return errorDocument("No session is open.");
      const doc = await ask(request);
      if (!alive) return errorDocument("The panel is closed.");
      // Reset/remount already fetches the new session; don't repeat it from an obsolete request.
      const now = sessionRequest();
      if (!now || now.directory !== request.directory || now.session !== request.session)
        return errorDocument("The session changed while collecting the snapshot.");
      return doc;
    } catch (error) {
      return errorDocument("The desktop main process did not answer: " + errorMessage(error));
    }
  };

  const showSessionToast = (sessionID, copied) => {
    try {
      const toast = ctx.toast;
      if (typeof toast?.show !== "function") return false;
      const key = copied ? "message.sessionCopied" : "message.sessionID";
      const title = typeof ctx.t === "function" ? ctx.t(key) : MESSAGES[key];
      const result = toast.show(copied ? { title } : { title, description: sessionID });
      Promise.resolve(result).catch(() => {});
      return true;
    } catch (_) {
      return false;
    }
  };

  const copyOrShowSessionID = (sessionID) => {
    try {
      const system = ctx.system;
      if (typeof system?.copy === "function") {
        const result = system.copy(sessionID);
        if (result && typeof result.then === "function") {
          Promise.resolve(result).then(() => showSessionToast(sessionID, true), () => showSessionToast(sessionID, false));
        } else {
          showSessionToast(sessionID, true);
        }
        return true;
      }
    } catch (_) {
      // If copying fails, still offer the ID in a toast.
    }
    return showSessionToast(sessionID, false);
  };

  const onOpenSession = (selection) => {
    try {
      const sessionID = selection?.sessionID;
      if (!alive || typeof sessionID !== "string" || !SESSION_ID.test(sessionID)) return false;
      const session = props.session;
      if (session?.id === sessionID) return false;
      try {
        const server = session?.server?.id;
        const tabs = ctx.tabs;
        if (typeof server === "string" && server && typeof tabs?.open === "function") {
          const result = tabs.open({ server, id: sessionID }, { focus: false });
          Promise.resolve(result).catch(() => { if (alive) copyOrShowSessionID(sessionID); });
          return true;
        }
      } catch (_) {
        // This host could not open the tab; try the clipboard and toast instead.
      }
      return copyOrShowSessionID(sessionID);
    } catch (_) {
      return false;
    }
  };

  const view = loadView();
  const interval = Number(ctx.options && ctx.options.intervalMs);
  const intervalMs = Number.isFinite(interval) && interval >= 250 ? interval : DEFAULT_INTERVAL_MS;
  const mountView = () => {
    const target = document.createElement("div");
    target.className = "agentgraph-desktop-view";
    host.appendChild(target);
    try {
      handle = view.mount(target, { fetchSnapshot, intervalMs, onOpenSession });
    } catch (error) {
      host.removeChild(target);
      showMessage(host, ctx.t("message.viewFailed", { error: oneLine(errorMessage(error)) }));
    }
  };
  const destroyView = () => {
    const current = handle;
    handle = null;
    if (current && typeof current.destroy === "function") {
      try {
        current.destroy();
      } catch (_) {
        // The view is going away either way.
      }
    }
    host.replaceChildren();
  };
  if (!view || typeof view.mount !== "function") {
    showMessage(host, viewFailure ? ctx.t("message.viewFailed", { error: oneLine(errorMessage(viewFailure)) }) : ctx.t("message.noView"));
  } else {
    mountView();
  }

  // Drop the old session's view state before fetching for the new one.
  let shown = sessionKey();
  const follow = (key) => {
    if (key === shown) return;
    shown = key;
    Promise.resolve()
      .then(() => {
        if (!alive || key !== shown || !handle) return;
        if (typeof handle.reset === "function") return handle.reset();
        destroyView();
        mountView();
      })
      .catch(() => {});
  };
  if (typeof gui.createKeyed === "function") {
    gui.createKeyed(sessionKey, follow, { otherwise: () => follow(undefined) });
  } else if (solid && typeof solid.createEffect === "function") {
    solid.createEffect(() => follow(sessionKey()));
  }

  const cleanup = () => {
    if (!alive) return;
    alive = false;
    destroyView();
  };
  // render runs under the panel's Solid owner: onCleanup runs when the tab's content unmounts.
  if (solid && typeof solid.onCleanup === "function") solid.onCleanup(cleanup);

  return host;
}

function setup(ctx) {
  const solid = require("solid-js");
  const Panel = gui.Panel;
  const MenuItem = gui.MenuItem;
  const Command = gui.Command;
  const Style = gui.Style;
  const key = ctx.id + ":" + TAB_ID;
  const icon = ctx.icons && ctx.icons.graph;

  ctx.add(Style, HOST_CSS + "\n" + (ASSETS.css || ""));

  // The host keeps tabs from list stable: return the same object, labels from getters.
  const tab = {
    id: TAB_ID,
    get title() {
      return ctx.t("tab.title");
    },
    closable: "compact",
    fallback: true,
  };

  const panel = {
    id: PANEL_ID,
    region: "side",
    list: (input) => (input && Array.isArray(input.open) && input.open.includes(TAB_ID) ? [tab] : []),
    render: (props) => renderPanel(ctx, solid, props),
  };
  ctx.add(Panel, panel);

  const open = () => {
    const session = ctx.sessions.current();
    if (session) ctx.layout.open(key, session);
  };

  ctx.add(MenuItem, () => {
    const session = ctx.sessions.current();
    const item = {
      menu: "session.panel",
      id: MENU_ID,
      title: ctx.t("menu.title"),
      keybind: ctx.id + "." + COMMAND_ID,
      opened: !!session && ctx.layout.state(key, session) !== "closed",
      order: 40,
      run: open,
    };
    if (icon) item.icon = icon;
    return item;
  });

  ctx.add(Command, () => ({
    id: COMMAND_ID,
    title: ctx.t("command.open"),
    keywords: "agents agentgraph orchestration subagents graph",
    enabled: !!ctx.sessions.current(),
    run: open,
  }));
}

// ---------------------------------------------------------------------------------------------------------------

const definition = gui.Plugin.define({
  icons: { graph: GRAPH_ICON },
  i18n: { en: MESSAGES },
  setup,
  main,
});

// The host reads `Object.hasOwn(module.exports, "default") ? module.exports.default : module.exports`.
// Exporting the definition itself, with a non-enumerable own `default` pointing back at it, satisfies both readings
// (and the docs' "default-exports Plugin.define") without adding an enumerable key to the definition.
module.exports = definition;
try {
  Object.defineProperty(module.exports, "default", { value: definition, enumerable: false, configurable: true });
} catch (_) {
  // A frozen definition: the host falls back to module.exports itself.
}
