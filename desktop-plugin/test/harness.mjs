#!/usr/bin/env node
// Dependency-free checks of a fresh temporary bundle, without the desktop app.
//
//   node desktop-plugin/test/harness.mjs
//
// The bundle is evaluated the way OpenCode Desktop 2.3.x evaluates it: Function("require", "module", "exports", code)
// with a require that serves only the shared modules. Main-process mode runs in this realm (real Node built-ins,
// no window); window mode runs in a fresh vm context with a minimal fake window/document, where Node built-ins and
// electron are inert placeholders. @opencode/plugin/gui, effect and solid-js are small stubs that record calls.
//
// Prints one line per assertion group and exits 1 if any group fails.
import assert from "node:assert/strict";
import childProcess from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PLUGIN_DIR = path.resolve(HERE, "..");
const ORCHESTRATION = path.resolve(PLUGIN_DIR, "..");
const LIVE_BUNDLE = path.join(PLUGIN_DIR, "gui-experimental.cjs");
const PLUGIN_ID = JSON.parse(fs.readFileSync(path.join(PLUGIN_DIR, "package.json"), "utf8")).name;
const TMP = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), "agentgraph-desktop-"));
const BUNDLE = path.join(TMP, "gui-experimental.cjs");

let group = 0;
async function check(name, fn) {
  group += 1;
  try {
    await fn();
    console.log(`ok ${group} - ${name}`);
  } catch (error) {
    console.log(`not ok ${group} - ${name}`);
    console.error(error && error.stack ? error.stack : error);
    process.exitCode = 1;
  }
}

function cleanupTmp() {
  try {
    fs.rmSync(TMP, { recursive: true, force: true });
  } catch (_) {}
}
process.on("exit", cleanupTmp);

// ---------------------------------------------------------------------------------------------------------------
// Stubs

function inert(name) {
  const fn = function () {};
  let proxy;
  proxy = new Proxy(fn, {
    get: (_t, key) => (key === Symbol.toPrimitive ? () => `[inert module ${name}]` : proxy),
    set: () => true,
    has: () => true,
    deleteProperty: () => true,
    apply: () => proxy,
    construct: () => proxy,
  });
  return proxy;
}

function effectStub() {
  const Schema = {
    String: { _tag: "String" },
    Number: { _tag: "Number" },
    Boolean: { _tag: "Boolean" },
    Unknown: { _tag: "Unknown" },
    Struct: (fields) => ({ _tag: "Struct", fields }),
  };
  return { Schema };
}

// Mirrors the app: Plugin.define is the identity, Ipc.define returns { kind, id, spec }.
function guiStub(processKind) {
  const defined = [];
  const keyed = [];
  const stub = {
    defined,
    keyed,
    Plugin: {
      define(definition) {
        defined.push(definition);
        return definition;
      },
    },
    Ipc: { define: (spec) => ({ kind: "ipc", id: spec.id, spec }) },
    Contract: { define: (id) => ({ kind: "contract", id }) },
    Registry: { define: (id) => ({ kind: "registry", id }) },
    Store: { global: (schema, initial) => ({ scope: "global", schema, initial }) },
  };
  if (processKind === "window") {
    for (const name of ["Command", "Panel", "MenuItem", "Style", "Slot", "TitlebarItem", "SettingsPage"]) {
      stub[name] = { kind: "registry", id: name };
    }
    // createKeyed(source, fn, { otherwise }): the stub re-reads the source when the test calls rerun().
    stub.createKeyed = (source, fn, options) => {
      const entry = { source, fn, options, last: undefined, runs: 0 };
      entry.rerun = () => {
        const value = source();
        if (entry.runs > 0 && value === entry.last) return;
        entry.last = value;
        entry.runs += 1;
        if (value === undefined || value === null || value === false) options && options.otherwise && options.otherwise();
        else fn(value);
      };
      entry.rerun();
      keyed.push(entry);
    };
  }
  return stub;
}

// A tiny Solid stand-in: an owner stack for onCleanup.
function solidStub() {
  const stack = [];
  return {
    owners: stack,
    runWithOwner(fn) {
      const owner = { cleanups: [] };
      stack.push(owner);
      try {
        return { value: fn(), dispose: () => owner.cleanups.splice(0).reverse().forEach((cleanup) => cleanup()), owner };
      } finally {
        stack.pop();
      }
    },
    onCleanup(fn) {
      const owner = stack[stack.length - 1];
      if (!owner) throw new Error("onCleanup outside an owner");
      owner.cleanups.push(fn);
    },
    createEffect(fn) {
      fn();
    },
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Fake DOM (only what the shell and the placeholder view use; innerHTML is a trap)

function fakeDocument() {
  class Element {
    constructor(tag, doc) {
      this.tagName = String(tag).toUpperCase();
      this.ownerDocument = doc;
      this.childNodes = [];
      this.parentNode = null;
      this.className = "";
      this.dataset = {};
      this.style = {};
      this.attributes = {};
      this._text = "";
      this.listeners = {};
      const self = this;
      this.classList = {
        add: (...names) => names.forEach((n) => !self.classList.contains(n) && (self.className = (self.className + " " + n).trim())),
        remove: (...names) => (self.className = self.className.split(/\s+/).filter((c) => c && !names.includes(c)).join(" ")),
        contains: (n) => self.className.split(/\s+/).includes(n),
        toggle: (n, force) => {
          const on = force === undefined ? !self.classList.contains(n) : force;
          on ? self.classList.add(n) : self.classList.remove(n);
          return on;
        },
      };
    }
    get children() {
      return this.childNodes;
    }
    get firstChild() {
      return this.childNodes[0] || null;
    }
    appendChild(node) {
      if (node.parentNode) node.parentNode.removeChild(node);
      node.parentNode = this;
      this.childNodes.push(node);
      return node;
    }
    append(...nodes) {
      nodes.forEach((node) => this.appendChild(typeof node === "string" ? this.ownerDocument.createTextNode(node) : node));
    }
    removeChild(node) {
      const index = this.childNodes.indexOf(node);
      if (index < 0) throw new Error("not a child");
      this.childNodes.splice(index, 1);
      node.parentNode = null;
      return node;
    }
    replaceChildren(...nodes) {
      while (this.firstChild) this.removeChild(this.firstChild);
      this.append(...nodes);
    }
    get textContent() {
      return this._text + this.childNodes.map((child) => child.textContent).join("");
    }
    set textContent(value) {
      while (this.firstChild) this.removeChild(this.firstChild);
      this._text = String(value);
    }
    set innerHTML(_value) {
      throw new Error("innerHTML is not allowed");
    }
    insertAdjacentHTML() {
      throw new Error("insertAdjacentHTML is not allowed");
    }
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    }
    getAttribute(name) {
      return name in this.attributes ? this.attributes[name] : null;
    }
    addEventListener(type, fn) {
      (this.listeners[type] = this.listeners[type] || []).push(fn);
    }
    removeEventListener(type, fn) {
      this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn);
    }
  }
  const doc = {
    Element,
    createElement: (tag) => new Element(tag, doc),
    createElementNS: (_ns, tag) => new Element(tag, doc),
    createTextNode: (text) => {
      const node = new Element("#text", doc);
      node._text = String(text);
      return node;
    },
    activeElement: null,
    set body(_v) {
      throw new Error("no writes to document.body");
    },
  };
  return doc;
}

// ---------------------------------------------------------------------------------------------------------------
// Loading the bundle like the host

function hostRequire(table, processKind, unexpected) {
  return (name) => {
    if (Object.prototype.hasOwnProperty.call(table, name)) return table[name];
    if (name === "electron" || name.startsWith("node:")) return processKind === "main" && name !== "electron" ? table.__node(name) : inert(name);
    if (["solid-js", "solid-js/web", "solid-js/store", "@opencode/client/solid"].includes(name)) return inert(name);
    unexpected.push(name);
    throw new Error(`Plugin GUI bundle requires a module the app does not share: ${name}`);
  };
}

function resolveDefault(exportsValue) {
  return Object.prototype.hasOwnProperty.call(exportsValue, "default") ? exportsValue.default : exportsValue;
}

function loadMain(code, spawnLog, { timeoutMs } = {}) {
  const gui = guiStub("main");
  const unexpected = [];
  const countingChildProcess = Object.assign({}, childProcess, {
    execFile(file, args, options, callback) {
      spawnLog.push({ file, args, cwd: options.cwd, timeout: options.timeout });
      // Exercise Node's real timeout and kill without waiting the full production timeout.
      return childProcess.execFile(file, args, timeoutMs === undefined ? options : { ...options, timeout: timeoutMs }, callback);
    },
  });
  const table = {
    "@opencode/plugin/gui": gui,
    "@opencode/plugin/gui/main": gui,
    effect: effectStub(),
    __node: (name) => (name === "node:child_process" ? countingChildProcess : process.getBuiltinModule(name)),
  };
  const module = { exports: {} };
  // Same realm as this script, like the Electron main process; no window or document here.
  assert.equal(typeof globalThis.window, "undefined");
  Function("require", "module", "exports", code)(hostRequire(table, "main", unexpected), module, module.exports);
  return { module, gui, unexpected };
}

function loadWindow(code, { onAgentGraph } = {}) {
  const gui = guiStub("window");
  const solid = solidStub();
  const unexpected = [];
  const document = fakeDocument();
  const window = { document };
  if (onAgentGraph) {
    let stored;
    Object.defineProperty(window, "AgentGraph", {
      configurable: true,
      get: () => (stored ? onAgentGraph(stored) : undefined),
      set: (value) => {
        stored = value;
      },
    });
  }
  const sandbox = { window, document, setTimeout, clearTimeout, console, queueMicrotask };
  window.setTimeout = setTimeout;
  window.clearTimeout = clearTimeout;
  const context = vm.createContext(sandbox);
  const table = { "@opencode/plugin/gui": gui, effect: effectStub(), "solid-js": solid };
  const module = { exports: {} };
  const wrapper = vm.runInContext(`(function (require, module, exports) {\n${code}\n})`, context, { filename: BUNDLE });
  wrapper(hostRequire(table, "window", unexpected), module, module.exports);
  return { module, gui, solid, unexpected, document, window, context };
}

function windowContext(definition, overrides = {}) {
  const added = [];
  const opened = [];
  const calls = [];
  let session = overrides.session;
  const ctx = {
    id: PLUGIN_ID,
    options: overrides.options || {},
    tabs: overrides.tabs,
    system: overrides.system,
    toast: overrides.toast,
    signal: new AbortController().signal,
    icons: { graph: `${PLUGIN_ID}:graph` },
    t(key, params) {
      let text = (definition.i18n && definition.i18n.en && definition.i18n.en[key]) || key;
      for (const [name, value] of Object.entries(params || {})) text = text.split(`{{${name}}}`).join(String(value));
      return text;
    },
    add(token, item) {
      added.push({ token, item });
      return () => {};
    },
    sessions: { current: () => session },
    layout: {
      open: (key, s) => opened.push({ op: "open", key, session: s }),
      toggle: (key, s) => opened.push({ op: "toggle", key, session: s }),
      state: () => "closed",
    },
    main(token) {
      calls.push({ op: "main", token });
      return {
        snapshot: async (input, options) => {
          calls.push({ op: "snapshot", input: JSON.parse(JSON.stringify(input)), options });
          if (overrides.reject) throw new Error("main is not running");
          return JSON.stringify({ version: 1, agents: [{ name: "<img src=x onerror=alert(1)>", busy: 2 }, { name: "impl-openai", busy: 0 }], echo: input });
        },
      };
    },
  };
  return { ctx, added, opened, calls, setSession: (next) => (session = next) };
}

function value(item) {
  return typeof item === "function" ? item() : item;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// ---------------------------------------------------------------------------------------------------------------

function liveBundleState() {
  const stat = fs.statSync(LIVE_BUNDLE, { bigint: true, throwIfNoEntry: false });
  return stat && { mtimeNs: stat.mtimeNs, ino: stat.ino };
}

const liveBefore = liveBundleState();
// Seed stale output only in the temporary directory so the freshness check covers an existing file.
fs.writeFileSync(BUNDLE, "// stale bundle fixture\n");
const previousBundle = fs.statSync(BUNDLE);
childProcess.execFileSync(process.execPath, [path.join(PLUGIN_DIR, "build.mjs"), "--out", BUNDLE], { stdio: "inherit" });
const CODE = fs.readFileSync(BUNDLE, "utf8");

await check("bundle parses as a CommonJS file and as the host's Function wrapper", () => {
  new vm.Script(CODE, { filename: BUNDLE });
  new vm.Script(`(function (require, module, exports) {\n${CODE}\n})`, { filename: BUNDLE });
  assert.match(CODE, /var AGENTGRAPH_ASSETS = /);
});

let mainLoad;
const spawns = [];
await check("main-process load: export is the Plugin.define result, host shape, no window code runs", () => {
  mainLoad = loadMain(CODE, spawns);
  const { module, gui, unexpected } = mainLoad;
  assert.deepEqual(unexpected, []);
  assert.equal(gui.defined.length, 1, "Plugin.define called once");
  const definition = gui.defined[0];
  assert.equal(module.exports, definition, "module.exports is the definition");
  assert.equal(resolveDefault(module.exports), definition, "the host's default-export reading gives the definition");
  assert.equal(Object.keys(module.exports).includes("default"), false, "default is not enumerable");
  assert.equal(typeof definition.setup, "function");
  assert.equal(typeof definition.main, "function");
  assert.equal(definition.os, undefined);
  assert.equal(typeof definition.i18n.en, "object");
  for (const [k, v] of Object.entries(definition.i18n.en)) assert.equal(typeof v, "string", k);
  assert.match(definition.icons.graph, /^<svg xmlns="http:\/\/www\.w3\.org\/2000\/svg"[^>]*>[\s\S]*<\/svg>$/);
  assert.equal(spawns.length, 0, "nothing spawned at load");
});

function mainContext(options) {
  const provided = [];
  const added = [];
  const logs = [];
  const controller = new AbortController();
  const ctx = {
    id: PLUGIN_ID,
    options,
    signal: controller.signal,
    onCleanup: () => {},
    log: { write: (level, message, data) => logs.push({ level, message, data }) },
    provide(token, impl) {
      provided.push({ token, impl });
      return { changed() {}, emit() {}, dispose() {} };
    },
    add(token, item) {
      added.push({ token, item });
      return () => {};
    },
  };
  return { ctx, provided, added, logs, controller };
}

let handler;
await check("main registers exactly one Ipc (<id>.snapshot) with a snapshot method", async () => {
  const definition = resolveDefault(mainLoad.module.exports);
  const m = mainContext({ home: ORCHESTRATION });
  const cleanup = await definition.main(m.ctx);
  assert.ok(cleanup === undefined || typeof cleanup === "function");
  assert.equal(m.provided.length, 1);
  assert.equal(m.added.length, 0);
  const { token, impl } = m.provided[0];
  assert.equal(token.kind, "ipc");
  assert.equal(token.id, `${PLUGIN_ID}.snapshot`);
  assert.ok(token.id === m.ctx.id || token.id.startsWith(`${m.ctx.id}.`), "Ipc id is under the plugin id");
  assert.deepEqual(Object.keys(token.spec.methods), ["snapshot"]);
  assert.equal(token.spec.methods.snapshot.input._tag, "Struct");
  assert.deepEqual(Object.keys(token.spec.methods.snapshot.input.fields).sort(), ["directory", "session"]);
  assert.equal(token.spec.methods.snapshot.output._tag, "String");
  assert.ok(Object.prototype.hasOwnProperty.call(impl, "snapshot") && typeof impl.snapshot === "function");
  handler = impl.snapshot;
});

await check("real handler + real Python: version 1 document with an agents array", async () => {
  spawns.length = 0;
  const caller = { window: 1, signal: new AbortController().signal };
  const text = await handler({ directory: ORCHESTRATION, session: "" }, caller);
  assert.equal(typeof text, "string");
  const doc = JSON.parse(text);
  assert.equal(doc.version, 1, `document: ${text.slice(0, 300)}`);
  assert.equal(doc.error, undefined, `unexpected error: ${doc.error}`);
  assert.ok(Array.isArray(doc.agents), "agents is an array");
  assert.equal(spawns.length, 1);
  assert.equal(spawns[0].file, "python3");
  assert.deepEqual(spawns[0].args, ["-P", "-m", "agentgraph.snapshot_json"]);
  assert.equal(spawns[0].cwd, ORCHESTRATION);
  // A session id goes in as one --session=ID argument.
  const withSession = JSON.parse(await handler({ directory: ORCHESTRATION, session: "ses_harness" }, caller));
  assert.equal(withSession.version, 1);
  assert.deepEqual(spawns[1].args.slice(-1), ["--session=ses_harness"]);
});

await check("two overlapping requests share one Python process; a later one runs again", async () => {
  spawns.length = 0;
  const caller = { window: 1, signal: new AbortController().signal };
  const [a, b] = await Promise.all([
    handler({ directory: ORCHESTRATION, session: "" }, caller),
    handler({ directory: ORCHESTRATION + "/", session: "" }, caller),
  ]);
  assert.equal(spawns.length, 1, `spawned ${spawns.length}`);
  assert.equal(a, b);
  assert.equal(JSON.parse(a).version, 1);
  await handler({ directory: ORCHESTRATION, session: "" }, caller);
  assert.equal(spawns.length, 2, "not cached after the run finished");
  // A different pair runs on its own.
  await Promise.all([
    handler({ directory: ORCHESTRATION, session: "" }, caller),
    handler({ directory: ORCHESTRATION, session: "ses_other" }, caller),
  ]);
  assert.equal(spawns.length, 4);
});

await check("failures return { version: 1, error } without throwing (missing Python, bad input, bad home)", async () => {
  const definition = resolveDefault(mainLoad.module.exports);
  const caller = { window: 1, signal: new AbortController().signal };

  const missing = mainContext({ home: ORCHESTRATION, python: path.join(TMP, "no-such-python3") });
  definition.main(missing.ctx);
  const doc = JSON.parse(await missing.provided[0].impl.snapshot({ directory: ORCHESTRATION, session: "" }, caller));
  assert.equal(doc.version, 1);
  assert.equal(typeof doc.error, "string");
  assert.match(doc.error, /Python was not found/);
  assert.ok(doc.error.includes(missing.ctx.options.python));
  assert.match(doc.error, /options\.python/);
  assert.equal(doc.error.includes("\n"), false);

  spawns.length = 0;
  for (const input of [
    { directory: ORCHESTRATION, session: "--demo" },
    { directory: "relative/dir", session: "" },
    { directory: path.join(TMP, "does-not-exist"), session: "" },
    { directory: "", session: "" },
    null,
  ]) {
    const bad = JSON.parse(await handler(input, caller));
    assert.equal(bad.version, 1);
    assert.equal(typeof bad.error, "string", JSON.stringify(input));
  }
  assert.equal(spawns.length, 0, "invalid requests spawn nothing");

  const badHome = mainContext({ home: path.join(TMP, "no-orchestration") });
  definition.main(badHome.ctx);
  const noModule = JSON.parse(await badHome.provided[0].impl.snapshot({ directory: ORCHESTRATION, session: "" }, caller));
  assert.equal(noModule.version, 1);
  assert.match(noModule.error, /agentgraph/);
  assert.equal(noModule.error.includes("\n"), false);
});

async function fakePythonSnapshot(name, script, timeoutMs) {
  const python = path.join(TMP, name);
  fs.writeFileSync(python, `#!/bin/sh\n${script}\n`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log, { timeoutMs });
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const doc = JSON.parse(await m.provided[0].impl.snapshot({ directory: ORCHESTRATION, session: "ses_harness" }));
  assert.equal(log.length, 1);
  assert.equal(log[0].timeout, 20000, "the production timeout stays at 20 s");
  assert.deepEqual(Object.keys(doc).sort(), ["error", "version"]);
  assert.equal(doc.version, 1);
  assert.equal(typeof doc.error, "string");
  assert.doesNotMatch(doc.error, /[\r\n]/);
  return doc.error;
}

const SCRIPT_ERROR = `printf '%s\\n' '{"version":1,"error":"partial document before the process failed"}'`;

await check("old Python rejects -P with version and configuration guidance", async () => {
  const error = await fakePythonSnapshot("old-python", `printf '%s\\n' 'Unknown option: -P' 'usage: python [option]' >&2\nexit 2`);
  assert.match(error, /Python 3\.11 or newer is required.*options\.python/);
});

await check("timeout beats partial output and returns a one-line error document", async () => {
  const error = await fakePythonSnapshot("slow-python", `${SCRIPT_ERROR}\nexec /bin/sleep 5`, 300);
  assert.match(error, /did not finish within 20 s/);
});

await check("output over the cap beats partial output and returns a one-line error document", async () => {
  const error = await fakePythonSnapshot("noisy-python", `${SCRIPT_ERROR}\n/bin/dd if=/dev/zero bs=1048576 count=9 >&2 2>/dev/null`);
  assert.match(error, /larger than 8 MiB/);
});

await check("malformed JSON returns one line with configuration guidance", async () => {
  const error = await fakePythonSnapshot("bad-json-python", `printf '%s\\n' 'this is not a snapshot'`);
  assert.match(error, /returned invalid JSON.*options\.python/);
});

await check("a non-zero exit reports its status and diagnostic unless Python provides an error document", async () => {
  const error = await fakePythonSnapshot("failing-python", `printf '%s\\n' 'details' 'Could not read session data' >&2\nexit 23`);
  assert.match(error, /exited with status 23: Could not read session data/);
  assert.equal(await fakePythonSnapshot("reported-python-error", `${SCRIPT_ERROR}\nexit 1`), "partial document before the process failed");
});

let windowLoad;
let mountCalls;
await check("window load: same export, setup registers exactly Style, Panel, MenuItem and Command", () => {
  mountCalls = [];
  windowLoad = loadWindow(CODE, {
    onAgentGraph: () => ({
      mount(root, options) {
        const handle = { refreshes: 0, resets: 0, destroys: 0 };
        handle.refresh = () => {
          handle.refreshes += 1;
          return Promise.resolve();
        };
        handle.reset = () => {
          handle.resets += 1;
          return options.fetchSnapshot();
        };
        handle.destroy = () => {
          handle.destroys += 1;
        };
        mountCalls.push({ root, options, handle });
        return handle;
      },
    }),
  });
  const { module, gui, unexpected } = windowLoad;
  assert.deepEqual(unexpected, []);
  assert.equal(gui.defined.length, 1);
  const definition = gui.defined[0];
  assert.equal(module.exports, definition);
  assert.equal(resolveDefault(module.exports), definition);

  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const w = windowContext(definition, { session });
  const result = definition.setup(w.ctx);
  assert.ok(result === undefined || typeof result === "function");
  windowLoad.w = w;

  const kinds = w.added.map((entry) => entry.token.id);
  assert.deepEqual(kinds, ["Style", "Panel", "MenuItem", "Command"]);
  assert.equal(mountCalls.length, 0, "the view is not mounted by setup");

  const style = value(w.added[0].item);
  assert.equal(typeof style, "string");
  assert.match(style, /\.agentgraph-desktop-panel/);

  const panel = value(w.added[1].item);
  assert.equal(panel.id, "graph");
  assert.equal(panel.region, "side");
  assert.equal(typeof panel.render, "function");
  const tabs = panel.list({ session, open: ["graph"] });
  assert.equal(tabs.length, 1);
  assert.equal(tabs[0].id, "graph");
  assert.equal(tabs[0].title, "Agent graph");
  assert.equal(panel.list({ session, open: ["graph"] })[0], tabs[0], "the same tab object each time");
  assert.equal(panel.list({ session, open: [] }).length, 0);

  const menu = value(w.added[2].item);
  assert.equal(menu.menu, "session.panel");
  assert.equal(menu.id, "open");
  assert.equal(menu.title, "Agent graph");
  assert.equal(menu.icon, `${PLUGIN_ID}:graph`);
  assert.equal(menu.keybind, `${PLUGIN_ID}.open`);
  assert.equal(menu.opened, false);

  const command = value(w.added[3].item);
  assert.equal(command.id, "open");
  assert.equal(command.title, "Open agent graph");
  assert.equal(command.enabled, true);

  command.run();
  menu.run();
  assert.deepEqual(
    w.opened.map((o) => [o.op, o.key, o.session && o.session.id]),
    [
      ["open", `${PLUGIN_ID}:graph`, "ses_a"],
      ["open", `${PLUGIN_ID}:graph`, "ses_a"],
    ],
  );
  w.setSession(undefined);
  assert.equal(value(w.added[3].item).enabled, false, "command disabled without a session");
  value(w.added[3].item).run();
  assert.equal(w.opened.length, 2, "no open without a session");
  w.setSession(session);
});

await check("panel render: returns a DOM node, mounts the view, resets and fetches on session switch, cleanup destroys", async () => {
  const { solid, gui } = windowLoad;
  const w = windowLoad.w;
  const panel = value(w.added[1].item);
  let current = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = {
    get session() {
      return current;
    },
    get tab() {
      return panel.list({ open: ["graph"] })[0];
    },
    screen: {},
  };
  const rendered = solid.runWithOwner(() => panel.render(props));
  const host = rendered.value;
  assert.ok(host instanceof windowLoad.document.Element, "render returns a DOM element");
  assert.equal(host.className, "agentgraph-desktop-panel");
  assert.equal(mountCalls.length, 1, "AgentGraph.mount called once");
  const call = mountCalls[0];
  assert.ok(call.root instanceof windowLoad.document.Element, "mount gets an element");
  assert.equal(call.root.parentNode, host, "the mount element is inside the returned node");
  assert.equal(typeof call.options.fetchSnapshot, "function");
  assert.equal(rendered.owner.cleanups.length, 1, "one cleanup registered");

  const doc = await call.options.fetchSnapshot();
  assert.equal(doc.version, 1);
  assert.equal(doc.agents[0].name, "<img src=x onerror=alert(1)>", "text passed through as data");
  const snapshotCalls = w.calls.filter((c) => c.op === "snapshot");
  assert.deepEqual(snapshotCalls[0].input, { directory: ORCHESTRATION, session: "ses_a" });
  const tokens = w.calls.filter((c) => c.op === "main").map((c) => c.token);
  assert.equal(tokens[0].id, `${PLUGIN_ID}.snapshot`);

  // Switch sessions in the same mounted panel.
  current = { key: "srv\nses_b", id: "ses_b", directory: path.join(ORCHESTRATION, "agentgraph") };
  gui.keyed.forEach((entry) => entry.rerun());
  await flush();
  assert.equal(call.handle.resets, 1, "a session switch resets the view");
  assert.equal(call.handle.refreshes, 0, "refresh alone would leave the old session's picture visible");
  assert.equal(mountCalls.length, 1, "a reset-capable view stays mounted");
  const next = w.calls.filter((c) => c.op === "snapshot")[snapshotCalls.length];
  assert.deepEqual(next?.input, { directory: path.join(ORCHESTRATION, "agentgraph"), session: "ses_b" });

  // No session: an error document, no call to main.
  current = undefined;
  const before = w.calls.length;
  const none = await call.options.fetchSnapshot();
  assert.equal(none.version, 1);
  assert.equal(typeof none.error, "string");
  assert.equal(w.calls.length, before);

  rendered.dispose();
  assert.equal(call.handle.destroys, 1, "cleanup calls destroy");
  current = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const closed = await call.options.fetchSnapshot();
  assert.equal(typeof closed.error, "string", "no fetches after cleanup");
});

await check("a view without reset is destroyed and its replacement fetches the new session", async () => {
  const lifecycle = [];
  const callbacks = [];
  const tabCalls = [];
  const win = loadWindow(CODE, {
    onAgentGraph: () => ({
      mount(root, { fetchSnapshot, onOpenSession }) {
        lifecycle.push("mount");
        callbacks.push(onOpenSession);
        root.textContent = "drawing";
        void fetchSnapshot();
        return { refresh: () => lifecycle.push("refresh"), destroy: () => lifecycle.push("destroy") };
      },
    }),
  });
  const definition = resolveDefault(win.module.exports);
  let session = { id: "ses_a", directory: ORCHESTRATION, server: { id: "server_a" } };
  const tabs = { open: (target, options) => tabCalls.push(structuredClone({ target, options })) };
  const w = windowContext(definition, { session, tabs });
  definition.setup(w.ctx);
  const panel = value(w.added[1].item);
  const rendered = win.solid.runWithOwner(() => panel.render({ get session() { return session; } }));
  session = { ...session, id: "ses_b", server: { id: "server_b" } };
  win.gui.keyed.forEach((entry) => entry.rerun());
  await flush();
  assert.deepEqual(lifecycle, ["mount", "destroy", "mount"]);
  assert.equal(rendered.value.textContent, "drawing", "the previous drawing is gone");
  assert.deepEqual(w.calls.filter((call) => call.op === "snapshot").map((call) => call.input.session), ["ses_a", "ses_b"]);
  assert.equal(typeof callbacks[0], "function", "the initial mount gets the callback");
  assert.equal(typeof callbacks[1], "function", "the remount also gets the callback");
  assert.equal(callbacks[1]({ agent: "impl-openai", sessionID: "ses_b" }), false, "the new panel's own session stays in the view");
  assert.equal(callbacks[1]({ agent: "impl-openai", sessionID: "ses_child" }), true);
  assert.deepEqual(tabCalls, [{ target: { server: "server_b", id: "ses_child" }, options: { focus: true } }]);
  rendered.dispose();
  assert.deepEqual(lifecycle, ["mount", "destroy", "mount", "destroy"]);
});

async function withOpenSession(apis, run) {
  const definition = resolveDefault(windowLoad.module.exports);
  const session = { id: "ses_root", directory: ORCHESTRATION, server: { id: "server_root" } };
  const w = windowContext(definition, { ...apis, session });
  definition.setup(w.ctx);
  const panel = value(w.added[1].item);
  let current = session;
  const rendered = windowLoad.solid.runWithOwner(() => panel.render({ get session() { return current; } }));
  try {
    const callback = mountCalls[mountCalls.length - 1].options.onOpenSession;
    assert.equal(typeof callback, "function");
    await run(callback, (session) => { current = session; });
  } finally {
    rendered.dispose();
  }
}

function sessionActions(calls) {
  return {
    tabs: { open: (target, options) => calls.push(["open", structuredClone(target), structuredClone(options)]) },
    system: { copy: (id) => calls.push(["copy", id]) },
    toast: { show: (toast) => calls.push(["toast", structuredClone(toast)]) },
  };
}

await check("a working agent opens its session in a focused tab using the panel's server at click time", async () => {
  const calls = [];
  await withOpenSession(sessionActions(calls), (open, switchProps) => {
    switchProps({ id: "ses_current", directory: ORCHESTRATION, server: { id: "server_current" } });
    assert.equal(open({ agent: "impl-openai", sessionID: "ses_child" }), true);
    assert.deepEqual(calls, [["open", { server: "server_current", id: "ses_child" }, { focus: true }]]);
  });
});

await check("the panel's own session and malformed IDs leave all tab, clipboard and toast actions untouched", async () => {
  const calls = [];
  await withOpenSession(sessionActions(calls), (open) => {
    assert.equal(open({ agent: "orchestrator", sessionID: "ses_root" }), false);
    for (const sessionID of [undefined, 123, "", "../ses_child", "ses_bad\nid"]) {
      assert.equal(open({ agent: "impl-openai", sessionID }), false);
    }
    assert.equal(open(), false);
    assert.deepEqual(calls, []);
  });
});

await check("missing or throwing tab APIs fall back to copying the ID and showing one toast", async () => {
  for (const tabs of [undefined, { open() { throw new Error("tabs unavailable"); } }]) {
    const calls = [];
    await withOpenSession({ ...sessionActions(calls), tabs }, (open) => {
      assert.equal(open({ sessionID: "ses_child" }), true);
      assert.deepEqual(calls, [["copy", "ses_child"], ["toast", { title: "Session ID copied" }]]);
    });
  }
  const calls = [];
  const { toast } = sessionActions(calls);
  const tabs = { async open() { throw new Error("tabs unavailable"); } };
  const system = { async copy(id) { calls.push(["copy", id]); } };
  await withOpenSession({ tabs, system, toast }, async (open) => {
    assert.equal(open({ sessionID: "ses_child" }), true, "the view gets a synchronous result");
    await flush();
    assert.deepEqual(calls, [["copy", "ses_child"], ["toast", { title: "Session ID copied" }]]);
  });
});

await check("toast-only and clipboard-only hosts handle the click; unavailable or throwing fallbacks leave details to the view", async () => {
  const calls = [];
  const { system, toast } = sessionActions(calls);
  const click = { sessionID: "ses_child" };
  const throwing = () => { throw new Error("host capability unavailable"); };
  await withOpenSession({ system }, (open) => assert.equal(open(click), true));
  await withOpenSession({ system: { copy: throwing }, toast }, (open) => assert.equal(open(click), true));
  assert.deepEqual(calls, [["copy", "ses_child"], ["toast", { title: "Session ID", description: "ses_child" }]]);
  await withOpenSession({}, (open) => assert.equal(open(click), false));
  await withOpenSession({ tabs: { open: throwing }, system: { copy: throwing }, toast: { show: throwing } }, (open) => {
    assert.equal(open(click), false);
  });
});

await check("panel poll interval defaults to 4000 ms and accepts overrides of at least 250 ms", () => {
  const definition = resolveDefault(windowLoad.module.exports);
  for (const [options, expected] of [[{}, 4000], [{ intervalMs: 249 }, 4000], [{ intervalMs: 250 }, 250], [{ intervalMs: 5200 }, 5200]]) {
    const w = windowContext(definition, { options });
    definition.setup(w.ctx);
    const panel = value(w.added[1].item);
    const rendered = windowLoad.solid.runWithOwner(() => panel.render({}));
    try {
      assert.equal(mountCalls[mountCalls.length - 1].options.intervalMs, expected);
    } finally {
      rendered.dispose();
    }
  }
});

await check("sidebar host and mount element fill their container without enforcing a minimum size or scrolling", () => {
  const { w, solid } = windowLoad;
  const style = value(w.added[0].item);
  const panel = value(w.added[1].item);
  const rendered = solid.runWithOwner(() => panel.render({}));
  try {
    const root = mountCalls[mountCalls.length - 1].root;
    for (const [className, element] of [["agentgraph-desktop-panel", rendered.value], ["agentgraph-desktop-view", root]]) {
      assert.ok(element.classList.contains(className), `missing ${className} class`);
      const rule = style.match(new RegExp(`\\.${className}\\s*(?:,\\s*[^{}]+)?\\{([^}]+)\\}`));
      assert.ok(rule, `missing sizing rule for ${className}`);
      const declarations = Object.fromEntries(rule[1].split(";").filter(Boolean).map((part) => part.split(":").map((text) => text.trim())));
      for (const [property, expected] of Object.entries({ width: "100%", height: "100%", "min-width": "0", "min-height": "0", overflow: "hidden" })) {
        assert.equal(declarations[property], expected, `${className}: ${property}`);
      }
    }
  } finally {
    rendered.dispose();
  }
});

await check("panel render: main-process rejection becomes an error document; a throwing view shows text only", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const w = windowContext(definition, { session, reject: true });
  definition.setup(w.ctx);
  const panel = value(w.added[1].item);
  const props = { get session() { return session; }, screen: {} };
  const rendered = windowLoad.solid.runWithOwner(() => panel.render(props));
  const call = mountCalls[mountCalls.length - 1];
  const doc = await call.options.fetchSnapshot();
  assert.equal(doc.version, 1);
  assert.match(doc.error, /main process did not answer/);
  rendered.dispose();

  const throwing = loadWindow(CODE, {
    onAgentGraph: () => ({
      mount() {
        throw new Error("<b>boom</b>");
      },
    }),
  });
  const def2 = throwing.gui.defined[0];
  const w2 = windowContext(def2, { session });
  def2.setup(w2.ctx);
  const panel2 = value(w2.added[1].item);
  const r2 = throwing.solid.runWithOwner(() => panel2.render(props));
  assert.match(r2.value.textContent, /failed to start: <b>boom<\/b>/);
  r2.dispose();
});

await check("placeholder build (no app.js/style.css): builds, loads in both modes, draws names and busy counts as text", async () => {
  const out = path.join(TMP, "placeholder.cjs");
  const stdout = childProcess.execFileSync(
    process.execPath,
    [path.join(PLUGIN_DIR, "build.mjs"), "--app", path.join(TMP, "missing-app.js"), "--css", path.join(TMP, "missing.css"), "--out", out],
    { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] },
  );
  assert.match(stdout, /placeholder/);
  const code = fs.readFileSync(out, "utf8");
  new vm.Script(code);
  const m = loadMain(code, []);
  assert.equal(resolveDefault(m.module.exports), m.gui.defined[0]);

  const win = loadWindow(code);
  const definition = win.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  assert.match(value(w.added[0].item), /agentgraph-placeholder/);
  const panel = value(w.added[1].item);
  const props = { get session() { return session; }, screen: {} };
  const rendered = win.solid.runWithOwner(() => panel.render(props));
  await flush();
  await flush();
  const text = rendered.value.textContent;
  rendered.dispose();
  assert.match(text, /<img src=x onerror=alert\(1\)>/, "names drawn as text");
  assert.match(text, /impl-openai/, "current agent names are drawn");
  assert.match(text, /2 busy/);
  assert.match(text, /2 agents, 2 busy/);
  assert.equal(rendered.value.textContent, "", "destroy clears the view");
});

await check("the harness replaces an existing temporary bundle before reading it", () => {
  // build.mjs writes atomically, so even an identical rebuild replaces the existing file.
  assert.notEqual(fs.statSync(BUNDLE).ino, previousBundle.ino, "the existing bundle was reused");
});

await check("the live bundle's modification time and file identity are unchanged", () => {
  assert.deepEqual(liveBundleState(), liveBefore);
});
