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
    // The host's Effect Schema has optionalKey (a key that may be absent; when present, of that schema).
    optionalKey: (schema) => ({ _tag: "optionalKey", schema }),
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
      const entry = { file, args, cwd: options.cwd, timeout: options.timeout, stdin: "" };
      spawnLog.push(entry);
      // Exercise Node's real timeout and kill without waiting the full production timeout.
      const child = childProcess.execFile(file, args, timeoutMs === undefined ? options : { ...options, timeout: timeoutMs }, callback);
      // Record what the plugin sends on stdin (the flow instruction), without changing it.
      if (child && child.stdin) {
        const end = child.stdin.end.bind(child.stdin);
        child.stdin.end = (chunk, ...rest) => {
          if (typeof chunk === "string") entry.stdin += chunk;
          return end(chunk, ...rest);
        };
      }
      return child;
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

const STALE_REV = "0000000000000000"; // a rev the view showed before something else changed the thing
const CURRENT_REV = "1111111111111111";

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
        flow: async (input, options) => {
          calls.push({ op: "flow", input: JSON.parse(JSON.stringify(input)), options });
          if (input.expect === STALE_REV) return JSON.stringify({ error: "it changed since you opened it; reload and try again", rev: CURRENT_REV });
          if (input.path.includes("orchestrator reviewer impl-openai")) return JSON.stringify({ error: "the hub orchestrator can only start or end a flow" });
          if (input.op !== "add") return JSON.stringify({ flows: [] });
          const flow = { id: input.path.split(" ").join("-"), path: input.path.split(" "), text: input.text || "Made.", source: "drawn" };
          return JSON.stringify({ flow, flows: [flow] });
        },
        skills: async (input, options) => {
          calls.push({ op: "skills", input: JSON.parse(JSON.stringify(input)), options });
          if (input.expect === STALE_REV) return JSON.stringify({ error: "it changed since you opened it; reload and try again", rev: CURRENT_REV });
          if (input.agent === "councillor-a") return JSON.stringify({ error: "Council seats are model calls defined in the council's own table." });
          const skills = input.op === "all" ? ["*"] : input.op === "add" ? ["alpha", input.skill] : ["alpha"];
          return JSON.stringify({ agent: input.agent, skills, warnings: input.op === "remove" ? ["task-profiles.md: still named."] : [], changed: true, message: "restart" });
        },
        roster: async (input, options) => {
          calls.push({ op: "roster", input: JSON.parse(JSON.stringify(input)), options });
          if (input.expect === STALE_REV) return JSON.stringify({ error: "it changed since you opened it; reload and try again", rev: CURRENT_REV });
          if (input.name === "orchestrator") return JSON.stringify({ error: "'orchestrator' is the orchestrator, the council or a council seat and cannot be removed here" });
          return JSON.stringify({ agent: input.name || "restored-agent", action: input.op, record: input.op === "remove" ? "/tmp/backups/undo.json" : undefined, message: "restart" });
        },
        skillfile: async (input, options) => {
          calls.push({ op: "skillfile", input: JSON.parse(JSON.stringify(input)), options });
          if (input.expect === STALE_REV) return JSON.stringify({ error: "it changed since you opened it; reload and try again", rev: CURRENT_REV });
          if (input.id === "shopify-ruby" && input.op !== "read") return JSON.stringify({ error: "shopify-ruby is kept in step with Shopify's own copy; edit it there, not here." });
          return JSON.stringify({ id: input.id || "restored-skill", description: input.description || "D", text: input.text || "T", changed: true });
        },
        team: async (input, options) => {
          calls.push({ op: "team", input: JSON.parse(JSON.stringify(input)), options });
          if (input.expect === STALE_REV) return JSON.stringify({ error: "it changed since you opened it; reload and try again", rev: CURRENT_REV });
          if (input.lead === "orchestrator") return JSON.stringify({ error: "the hub cannot lead a group" });
          if (input.op === "remove") return JSON.stringify({ teams: [] });
          const team = { id: input.id || input.lead, lead: input.lead || "reviewer", members: input.members ? input.members.split(" ") : ["a"], text: input.text || "Made." };
          return JSON.stringify({ team, teams: [team] });
        },
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
await check("main registers exactly one Ipc (<id>.snapshot) with snapshot, flow, skills, roster, skillfile and team methods", async () => {
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
  // Round 2: the skills method joined snapshot and flow.
  assert.deepEqual(Object.keys(token.spec.methods), ["snapshot", "flow", "skills", "roster", "skillfile", "team"]);
  // Round 3: roster, skillfile and team, each with string fields only.
  // n96: "to" (the new name of a rename) joined the roster's fields.
  // n99 r4: "expect" (the view's rev) is declared, optional, on every method whose ops take one: the host's decoding
  // drops undeclared keys. Every other field is a required string.
  const stringsAndExpect = (fields) => Object.entries(fields).every(([key, field]) => key === "expect"
    ? field._tag === "optionalKey" && field.schema._tag === "String" : field._tag === "String");
  assert.deepEqual(Object.keys(token.spec.methods.roster.input.fields), ["op", "name", "record", "model", "variant", "description", "prompt", "skills", "read_only", "copy_of", "template", "to", "expect"]);
  assert.deepEqual(Object.keys(token.spec.methods.skillfile.input.fields), ["op", "id", "record", "description", "text", "assign", "expect"]);
  assert.deepEqual(Object.keys(token.spec.methods.team.input.fields), ["op", "id", "lead", "members", "text", "expect"]);
  for (const method of ["roster", "skillfile", "team"]) {
    assert.ok(stringsAndExpect(token.spec.methods[method].input.fields), method);
    assert.equal(token.spec.methods[method].output._tag, "String");
    assert.ok(typeof impl[method] === "function", method);
  }
  assert.deepEqual(Object.keys(token.spec.methods.skills.input.fields), ["op", "agent", "skill", "expect"]);
  assert.ok(stringsAndExpect(token.spec.methods.skills.input.fields));
  assert.equal(token.spec.methods.skills.output._tag, "String");
  assert.ok(typeof impl.skills === "function");
  assert.deepEqual(Object.keys(token.spec.methods.flow.input.fields), ["op", "id", "path", "text", "expect"]);
  assert.ok(stringsAndExpect(token.spec.methods.flow.input.fields));
  assert.equal(token.spec.methods.flow.output._tag, "String");
  assert.ok(typeof impl.flow === "function");
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
  assert.deepEqual(tabCalls, [{ target: { server: "server_b", id: "ses_child" }, options: { focus: false } }]);
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
    assert.deepEqual(calls, [["open", { server: "server_current", id: "ses_child" }, { focus: false }]]);
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

function structureHashes() {
  const root = path.join(ORCHESTRATION, "structure");
  const out = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else out.push(full + " " + fs.readFileSync(full).toString("base64"));
    }
  };
  walk(root);
  return out.join("\n");
}

await check("flow runs `python3 -P -m agentgraph.structure flow add|text|remove ... --json` in home with the text on stdin, one line back; bad input and the demo spawn nothing", async () => {
  const before = structureHashes();
  const python = path.join(TMP, "fake-structure-python");
  // Echo the arguments and stdin back as the flow, so the test sees exactly what the command got.
  fs.writeFileSync(python, `#!/bin/sh
shift 3
case "$*" in *"orchestrator reviewer impl-openai"*) printf '{"error": "the hub orchestrator can only start or end a flow"}\\n'; exit 1;; esac
input=$(cat)
if [ "$2" = remove ]; then printf '{"flows": []}\\n'; exit 0; fi
printf 'note\\n{"flow": {"id": "f", "path": ["a", "b"], "text": "%s"}, "flows": [], "notes": ["%s"]}\\n' "$input" "$*"
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const added = JSON.parse(await impl.flow({ op: "add", id: "", path: "impl-openai verifier impl-openai", text: "Check it; $(touch x) `id`" }));
  assert.deepEqual(added, { flow: { id: "f", path: ["a", "b"], text: "Check it; $(touch x) `id`" }, flows: [], notes: ["flow add impl-openai verifier impl-openai --text-file - --json"] });
  assert.equal(log.length, 1);
  assert.equal(log[0].file, python);
  assert.deepEqual(log[0].args, ["-P", "-m", "agentgraph.structure", "flow", "add", "impl-openai", "verifier", "impl-openai", "--text-file", "-", "--json"]);
  assert.equal(log[0].stdin, "Check it; $(touch x) `id`");
  assert.equal(log[0].cwd, ORCHESTRATION);
  const generated = JSON.parse(await impl.flow({ op: "add", id: "", path: "researcher impl-claude", text: "  " }));
  assert.deepEqual(log[1].args, ["-P", "-m", "agentgraph.structure", "flow", "add", "researcher", "impl-claude", "--json"], "a blank text asks for the generated one");
  assert.equal(generated.flow.text, "");
  JSON.parse(await impl.flow({ op: "text", id: "impl-openai-verifier", path: "", text: "New text\nsecond line" }));
  assert.deepEqual(log[2].args, ["-P", "-m", "agentgraph.structure", "flow", "text", "impl-openai-verifier", "--text-file", "-", "--json"]);
  assert.equal(log[2].stdin, "New text\nsecond line");
  assert.deepEqual(JSON.parse(await impl.flow({ op: "remove", id: "impl-openai-verifier", path: "", text: "" })), { flows: [] });
  assert.deepEqual(log[3].args, ["-P", "-m", "agentgraph.structure", "flow", "remove", "impl-openai-verifier", "--json"]);
  assert.deepEqual(JSON.parse(await impl.flow({ op: "add", id: "", path: "orchestrator reviewer impl-openai", text: "" })), { error: "the hub orchestrator can only start or end a flow" });
  log.length = 0;
  for (const input of [null, { op: "add", id: "", path: "impl-openai", text: "" }, { op: "add", id: "", path: "impl-openai --structure", text: "" }, { op: "add", id: "", path: "impl-openai  reviewer", text: "" },
    { op: "add", id: "x", path: "impl-openai reviewer", text: "" }, { op: "add", id: "", path: Array(21).fill("reviewer").join(" "), text: "" }, { op: "text", id: "-f", path: "", text: "x" },
    { op: "text", id: "Upper", path: "", text: "x" }, { op: "text", id: "f", path: "a b", text: "x" }, { op: "remove", id: "f", path: "", text: "x" }, { op: "link", id: "f", path: "", text: "" },
    { op: "add", path: "a b", text: "" }, { op: "text", id: "f", path: "", text: "x".repeat(8001) }, { op: "text", id: "f", path: "", text: "a\0b" }]) {
    const bad = JSON.parse(await impl.flow(input));
    assert.equal(typeof bad.error, "string", JSON.stringify(input));
  }
  const demo = mainContext({ home: ORCHESTRATION, python, demo: true });
  resolveDefault(loaded.module.exports).main(demo.ctx);
  assert.match(JSON.parse(await demo.provided[0].impl.flow({ op: "add", id: "", path: "impl-openai reviewer", text: "" })).error, /demo/);
  assert.equal(log.length, 0, "invalid requests and the demo spawn nothing");
  assert.equal(structureHashes(), before, "the real structure folder changed");
});

await check("flow refuses a name starting with '-' and gives the Python 3.11 hint when Python rejects -P", async () => {
  const before = structureHashes();
  const old = path.join(TMP, "old-structure-python");
  fs.writeFileSync(old, `#!/bin/sh\necho "Unknown option: -P" >&2\necho "usage: python3 [option] ..." >&2\nexit 2\n`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python: old });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  for (const path of ["-impl-openai reviewer", "impl-openai -reviewer", "impl-openai -P", "impl-openai --text-file"]) {
    const bad = JSON.parse(await impl.flow({ op: "add", id: "", path, text: "" }));
    assert.match(bad.error, /agent names/, path);
  }
  assert.equal(log.length, 0, "a name starting with '-' reached the command line");
  const hint = JSON.parse(await impl.flow({ op: "add", id: "", path: "impl-openai reviewer", text: "Check." }));
  assert.equal(log.length, 1);
  assert.match(hint.error, /Python 3\.11 or newer is required/);
  assert.ok(hint.error.includes(old) && hint.error.includes("options.python") && !hint.error.includes("\n"), hint.error);
  assert.equal(structureHashes(), before, "the real structure folder changed");
});

await check("flow with the real command in a copied home: add with stdin text, change it, remove it, and the copy is back as it was", async () => {
  const before = structureHashes();
  const home = path.join(TMP, "flow-home");
  fs.cpSync(path.join(ORCHESTRATION, "agentgraph"), path.join(home, "agentgraph"), { recursive: true, filter: (source) => !source.includes("__pycache__") && !source.includes(path.sep + "web" + path.sep) });
  fs.cpSync(path.join(ORCHESTRATION, "structure"), path.join(home, "structure"), { recursive: true });
  const copied = () => fs.readdirSync(path.join(home, "structure")).sort().join(",");
  const start = copied();
  const loaded = loadMain(CODE, []);
  const m = mainContext({ home, python: "python3" });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const added = JSON.parse(await impl.flow({ op: "add", id: "", path: "impl-openai reviewer impl-openai", text: "Review it.\n\tThen again; $(id)" }));
  assert.ok(added.flow && added.flow.id, JSON.stringify(added));
  assert.equal(added.flow.text, "Review it.\n\tThen again; $(id)");
  assert.deepEqual(added.flow.path, ["impl-openai", "reviewer", "impl-openai"]);
  const changed = JSON.parse(await impl.flow({ op: "text", id: added.flow.id, path: "", text: "" }));
  assert.ok(changed.flow && changed.flow.text && changed.flow.text !== added.flow.text, "a blank text did not give the generated one: " + JSON.stringify(changed));
  // Round 2: "A A" is an agent's loop on itself (one per agent); the same agent twice inside a longer path is refused.
  const refused = JSON.parse(await impl.flow({ op: "add", id: "", path: "impl-openai reviewer reviewer", text: "" }));
  assert.match(refused.error, /follows itself/);
  const own = JSON.parse(await impl.flow({ op: "add", id: "", path: "impl-openai impl-openai", text: "" }));
  assert.deepEqual(own.flow && own.flow.path, ["impl-openai", "impl-openai"], JSON.stringify(own));
  assert.equal(own.flow.text, "impl-openai checks its own work against the task's evaluation and repeats until it passes.");
  assert.match(JSON.parse(await impl.flow({ op: "add", id: "", path: "impl-openai impl-openai", text: "" })).error, /already has/);
  assert.equal(JSON.parse(await impl.flow({ op: "remove", id: own.flow.id, path: "", text: "" })).flows.length, 1);
  assert.deepEqual(JSON.parse(await impl.flow({ op: "remove", id: added.flow.id, path: "", text: "" })), { flows: [] });
  assert.equal(copied(), start, "the copied structure folder is not back as it was");
  assert.equal(structureHashes(), before, "the real structure folder changed");
});

await check("panel render: the view gets actions.flow, which asks main and turns an error into a rejection; the demo passes none", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const call = mountCalls[mountCalls.length - 1];
  assert.equal(typeof call.options.actions?.flow, "function");
  const answer = await call.options.actions.flow({ op: "add", path: ["impl-openai", "reviewer"] });
  assert.deepEqual(JSON.parse(JSON.stringify(answer)), { flow: { id: "impl-openai-reviewer", path: ["impl-openai", "reviewer"], text: "Made.", source: "drawn" }, flows: [{ id: "impl-openai-reviewer", path: ["impl-openai", "reviewer"], text: "Made.", source: "drawn" }] });
  await call.options.actions.flow({ op: "text", id: "impl-openai-reviewer", text: "New." });
  await call.options.actions.flow({ op: "remove", id: "impl-openai-reviewer" });
  const asked = w.calls.filter((c) => c.op === "flow");
  assert.deepEqual(asked.map((c) => c.input), [
    { op: "add", id: "", path: "impl-openai reviewer", text: "" },
    { op: "text", id: "impl-openai-reviewer", path: "", text: "New." },
    { op: "remove", id: "impl-openai-reviewer", path: "", text: "" },
  ]);
  assert.equal(w.calls.filter((c) => c.op === "main").at(-1).token.id, `${PLUGIN_ID}.snapshot`);
  const failure = async (request) => {
    try {
      await call.options.actions.flow(request);
    } catch (error) {
      return error.message;
    }
    return "";
  };
  assert.equal(await failure({ op: "add", path: ["orchestrator", "reviewer", "impl-openai"] }), "the hub orchestrator can only start or end a flow");
  const count = w.calls.filter((c) => c.op === "flow").length;
  assert.equal(await failure({ op: "add", path: ["impl-openai reviewer", "impl-hard"] }), "Bad flow request.");
  assert.equal(await failure(null), "Bad flow request.");
  assert.equal(w.calls.filter((c) => c.op === "flow").length, count, "a bad request reached main");
  rendered.dispose();

  const demo = windowContext(definition, { session, options: { demo: true } });
  definition.setup(demo.ctx);
  const r2 = windowLoad.solid.runWithOwner(() => value(demo.added[1].item).render(props));
  assert.equal(mountCalls[mountCalls.length - 1].options.actions, undefined, "the demo can draw flows");
  r2.dispose();
});

await check("flow name|command|prompt and add with a name and the command: arguments without a shell, the goal on stdin, the demo gives only the run message", async () => {
  const before = structureHashes();
  const python = path.join(TMP, "fake-flow-python");
  fs.writeFileSync(python, `#!/bin/sh
shift 3
input=$(cat)
if [ "$2" = prompt ]; then printf '{"text": "%s"}\\n' "$input"; exit 0; fi
printf '{"flow": {"id": "f", "path": ["a", "b"], "text": "t", "name": null, "command": null}, "flows": [], "applies": "after a reload", "live": false, "notes": ["%s"]}\\n' "$*"
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const named = JSON.parse(await impl.flow({ op: "name", id: "impl-openai-verifier", path: "", text: "check-loop" }));
  assert.deepEqual(log[0].args, ["-P", "-m", "agentgraph.structure", "flow", "name", "impl-openai-verifier", "check-loop", "--json"]);
  assert.equal(named.flow.id, "f");
  await impl.flow({ op: "name", id: "impl-openai-verifier", path: "", text: "" });
  assert.deepEqual(log[1].args, ["-P", "-m", "agentgraph.structure", "flow", "name", "impl-openai-verifier", "--json"], "an empty name takes it away");
  const on = JSON.parse(await impl.flow({ op: "command", id: "impl-openai-verifier", path: "", text: "on" }));
  assert.deepEqual(log[2].args, ["-P", "-m", "agentgraph.structure", "flow", "command", "impl-openai-verifier", "--on", "--json"]);
  assert.deepEqual([on.applies, on.live], ["after a reload", false]);
  await impl.flow({ op: "command", id: "impl-openai-verifier", path: "", text: "off" });
  assert.deepEqual(log[3].args.slice(4), ["command", "impl-openai-verifier", "--off", "--json"]);
  const goal = "add a health endpoint; $(touch x) `id`";
  const prompt = JSON.parse(await impl.flow({ op: "prompt", id: "impl-openai-verifier", path: "", text: goal }));
  assert.deepEqual(log[4].args.slice(4), ["prompt", "impl-openai-verifier", "--goal-file", "-", "--json"]);
  assert.equal(log[4].stdin, goal);
  assert.deepEqual(prompt, { text: goal });
  await impl.flow({ op: "add-command", id: "check-loop", path: "impl-openai verifier impl-openai", text: "Loop." });
  assert.deepEqual(log[5].args.slice(4), ["add", "impl-openai", "verifier", "impl-openai", "--name", "check-loop", "--command", "--text-file", "-", "--json"]);
  await impl.flow({ op: "add", id: "check-loop", path: "impl-openai reviewer", text: "" });
  assert.deepEqual(log[6].args.slice(4), ["add", "impl-openai", "reviewer", "--name", "check-loop", "--json"]);
  log.length = 0;
  for (const input of [{ op: "name", id: "f", path: "", text: "-x" }, { op: "name", id: "f", path: "", text: "Upper" }, { op: "name", id: "-f", path: "", text: "ok-name" },
    { op: "name", id: "f", path: "", text: "a".repeat(33) }, { op: "command", id: "f", path: "", text: "yes" }, { op: "command", id: "f", path: "", text: "" },
    { op: "command", id: "f", path: "a b", text: "on" }, { op: "prompt", id: "f", path: "", text: "" }, { op: "prompt", id: "f", path: "", text: " \n " },
    { op: "prompt", id: "f", path: "", text: "x".repeat(4001) }, { op: "prompt", id: "f", path: "", text: "a\0b" }, { op: "prompt", id: "--goal-file", path: "", text: "g" },
    { op: "add-command", id: "", path: "impl-openai reviewer", text: "" }, { op: "add", id: "-n", path: "impl-openai reviewer", text: "" }]) {
    const bad = JSON.parse(await impl.flow(input));
    assert.equal(typeof bad.error, "string", JSON.stringify(input));
  }
  assert.equal(log.length, 0, "a bad request spawned the command");
  assert.ok(JSON.parse(await impl.flow({ op: "prompt", id: "f", path: "", text: "x".repeat(4000) })).text, "a goal of 4000 characters is refused");
  const demo = mainContext({ home: ORCHESTRATION, python, demo: true });
  resolveDefault(loaded.module.exports).main(demo.ctx);
  const demoImpl = demo.provided[0].impl;
  assert.deepEqual(JSON.parse(await demoImpl.flow({ op: "prompt", id: "f", path: "", text: "g" })), { text: "g" });
  for (const input of [{ op: "name", id: "f", path: "", text: "check-loop" }, { op: "command", id: "f", path: "", text: "on" }])
    assert.match(JSON.parse(await demoImpl.flow(input)).error, /demo/);
  assert.equal(structureHashes(), before, "the real structure folder changed");
});

await check("panel render: actions.flow takes the name, command and prompt requests; actions.run sends the run message with the host's session.prompt and rejects plainly", async () => {
  const definition = windowLoad.gui.defined[0];
  const sent = [];
  let sendFails = false;
  const client = { session: { prompt(input) { sent.push({ self: this, input }); return sendFails ? Promise.reject(new Error("offline\nnow")) : Promise.resolve({ data: { type: "user" } }); } } };
  let session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION, server: { client } };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  const asked = [];
  w.ctx.main = () => ({
    flow: async (input) => {
      asked.push(JSON.parse(JSON.stringify(input)));
      if (input.op === "prompt") return input.id === "ghost" ? JSON.stringify({ error: "there is no flow 'ghost'" }) : JSON.stringify({ text: "RUN " + input.id + ": " + input.text });
      return JSON.stringify({ flows: [], flow: { id: input.id } });
    },
  });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { flow, run } = mountCalls[mountCalls.length - 1].options.actions;
  assert.equal(typeof run, "function");
  await flow({ op: "name", id: "f", name: "check-loop" });
  await flow({ op: "command", id: "f", on: true });
  assert.deepEqual(JSON.parse(JSON.stringify(await flow({ op: "prompt", id: "f", goal: "g" }))), { text: "RUN f: g" });
  await flow({ op: "add", path: ["impl-openai", "reviewer"], text: "T", name: "check-loop", command: true });
  await flow({ op: "add", path: ["impl-openai", "reviewer"] });
  assert.deepEqual(asked, [
    { op: "name", id: "f", path: "", text: "check-loop" },
    { op: "command", id: "f", path: "", text: "on" },
    { op: "prompt", id: "f", path: "", text: "g" },
    { op: "add-command", id: "check-loop", path: "impl-openai reviewer", text: "T" },
    { op: "add", id: "", path: "impl-openai reviewer", text: "" },
  ]);
  const failure = async (action, request) => {
    try {
      await action(request);
    } catch (error) {
      return error.message;
    }
    return "";
  };
  asked.length = 0;
  for (const request of [{ op: "command", id: "f", on: "yes" }, { op: "name", id: "f" }, { op: "prompt", id: "f" }, { op: "add-command", path: ["a", "b"] },
    { op: "add", path: ["a", "b"], command: "yes" }])
    assert.equal(await failure(flow, request), "Bad flow request.", JSON.stringify(request));
  assert.equal(asked.length, 0, "a bad flow request reached main");

  assert.deepEqual(JSON.parse(JSON.stringify(await run({ flow: "f", goal: "add a health endpoint" }))), { sent: true, session: "ses_a" });
  assert.equal(sent.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(sent[0].input)), { sessionID: "ses_a", text: "RUN f: add a health endpoint" });
  assert.equal(sent[0].self, client.session, "session.prompt was called on the host's session API");
  assert.equal(await failure(run, { flow: "f", goal: "  " }), "A goal is required: say what the task must achieve.");
  assert.equal(await failure(run, { flow: "f" }), "Bad run request.");
  assert.equal(await failure(run, null), "Bad run request.");
  assert.equal(await failure(run, { flow: "ghost", goal: "g" }), "there is no flow 'ghost'");
  sendFails = true;
  assert.equal(await failure(run, { flow: "f", goal: "g" }), "The message could not be sent: offline now");
  sendFails = false;
  session = { ...session, parentID: "ses_root" };
  assert.match(await failure(run, { flow: "f", goal: "g" }), /orchestrator's session/);
  session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION, server: {} };
  assert.match(await failure(run, { flow: "f", goal: "g" }), /does not let a plugin send/);
  session = { key: "srv\nses_a", id: "", directory: ORCHESTRATION, server: { client } };
  assert.equal(await failure(run, { flow: "f", goal: "g" }), "No session is open.");
  assert.equal(sent.length, 2, "a refused run sent a message");
  rendered.dispose();
});

await check("panel render: the view gets actions.skills, which asks main's skills method and turns an error into a rejection", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const call = mountCalls[mountCalls.length - 1];
  assert.equal(typeof call.options.actions?.skills, "function");
  const added = await call.options.actions.skills({ agent: "impl-openai", skill: "beta", on: true });
  assert.deepEqual(JSON.parse(JSON.stringify(added)).skills, ["alpha", "beta"]);
  const removed = await call.options.actions.skills({ agent: "impl-openai", skill: "beta", on: false });
  assert.deepEqual(JSON.parse(JSON.stringify(removed)).warnings, ["task-profiles.md: still named."]);
  await call.options.actions.skills({ agent: "impl-openai", all: true });
  assert.deepEqual(w.calls.filter((c) => c.op === "skills").map((c) => c.input), [
    { op: "add", agent: "impl-openai", skill: "beta" },
    { op: "remove", agent: "impl-openai", skill: "beta" },
    { op: "all", agent: "impl-openai", skill: "" },
  ]);
  const failure = async (request) => {
    try {
      await call.options.actions.skills(request);
    } catch (error) {
      return error.message;
    }
    return "";
  };
  assert.match(await failure({ agent: "councillor-a", all: true }), /council's own table/);
  const count = w.calls.filter((c) => c.op === "skills").length;
  for (const bad of [null, { agent: "impl-openai" }, { agent: "-x", all: true }, { agent: "impl-openai", skill: "-f", on: true }, { agent: "impl openai", all: true }, { agent: "impl-openai", skill: 3, on: false }])
    assert.equal(await failure(bad), "Bad skills request.", JSON.stringify(bad));
  assert.equal(w.calls.filter((c) => c.op === "skills").length, count, "a bad skills request reached main");
  rendered.dispose();
});

await check("skills runs `python3 -P -m agentgraph.skillset add|remove|all ... --json` in home without a shell; a name starting with '-' and the demo spawn nothing", async () => {
  const python = path.join(TMP, "fake-skillset-python");
  fs.writeFileSync(python, `#!/bin/sh
shift 3
case "$2" in councillor-*) printf '{"error": "Council seats are model calls defined in the council\\u0027s own table; the plugin does not read skills for them."}\\n'; exit 1;; esac
printf 'noise\\n{"agent": "%s", "preset": "p", "skills": ["%s"], "warnings": [], "changed": true, "message": "%s"}\\n' "$2" "$3" "$*"
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const added = JSON.parse(await impl.skills({ op: "add", agent: "impl-openai", skill: "code-assurance" }));
  assert.deepEqual(added, { agent: "impl-openai", skills: ["code-assurance"], warnings: [], changed: true, message: "add impl-openai code-assurance --json" });
  assert.equal(log.length, 1);
  assert.equal(log[0].file, python);
  assert.deepEqual(log[0].args, ["-P", "-m", "agentgraph.skillset", "add", "impl-openai", "code-assurance", "--json"]);
  assert.equal(log[0].cwd, ORCHESTRATION);
  await impl.skills({ op: "remove", agent: "impl-openai", skill: "code-assurance" });
  assert.deepEqual(log[1].args, ["-P", "-m", "agentgraph.skillset", "remove", "impl-openai", "code-assurance", "--json"]);
  await impl.skills({ op: "all", agent: "orchestrator", skill: "" });
  assert.deepEqual(log[2].args, ["-P", "-m", "agentgraph.skillset", "all", "orchestrator", "--json"]);
  assert.match(JSON.parse(await impl.skills({ op: "all", agent: "councillor-fable", skill: "" })).error, /council's own table/);
  log.length = 0;
  for (const input of [null, { op: "add", agent: "-impl-openai", skill: "x" }, { op: "add", agent: "impl-openai", skill: "-x" }, { op: "add", agent: "impl-openai", skill: "--config" },
    { op: "add", agent: "impl openai", skill: "x" }, { op: "add", agent: "impl-openai", skill: "a b" }, { op: "all", agent: "impl-openai", skill: "x" }, { op: "show", agent: "impl-openai", skill: "" },
    { op: "add", agent: "impl-openai" }, { op: "remove", agent: "impl-openai", skill: "" }]) {
    const bad = JSON.parse(await impl.skills(input));
    assert.equal(typeof bad.error, "string", JSON.stringify(input));
  }
  const demo = mainContext({ home: ORCHESTRATION, python, demo: true });
  resolveDefault(loaded.module.exports).main(demo.ctx);
  assert.match(JSON.parse(await demo.provided[0].impl.skills({ op: "all", agent: "impl-openai", skill: "" })).error, /demo/);
  assert.equal(log.length, 0, "invalid requests and the demo spawn nothing");
});

await check("roster, skillfile and team run `python3 -P -m agentgraph.roster|skillfiles|teams ... --json` in home without a shell, free text on stdin; bad input and the demo spawn nothing", async () => {
  const python = path.join(TMP, "fake-editors-python");
  fs.writeFileSync(python, `#!/bin/sh
module="$3"
shift 3
input=$(cat)
case "$module" in
  agentgraph.roster) printf 'noise\\n{"agent": "%s", "action": "%s", "message": "%s", "stdin": "%s"}\\n' "$3" "$2" "$*" "$input";;
  agentgraph.skillfiles) printf '{"id": "%s", "action": "%s", "stdin": "%s"}\\n' "$3" "$2" "$input";;
  agentgraph.teams) printf '{"teams": [], "args": "%s", "stdin": "%s"}\\n' "$*" "$input";;
esac
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const blank = { op: "", name: "", record: "", model: "", variant: "", description: "", prompt: "", skills: "", read_only: "", copy_of: "", template: "", to: "" };
  const roster = async (fields) => JSON.parse(await impl.roster({ ...blank, ...fields }));
  const base = ["-P", "-m", "agentgraph.roster", "agent"];
  // add from the verifier template, as a copy, and from the fields given (the instruction on stdin)
  let doc = await roster({ op: "add", name: "checker", template: "verifier" });
  assert.equal(doc.agent, "checker");
  assert.deepEqual(log[0].args, base.concat(["add", "checker", "--template", "verifier", "--json"]));
  assert.equal(log[0].file, python);
  assert.equal(log[0].cwd, ORCHESTRATION);
  await roster({ op: "add", name: "claude-copy", copy_of: "impl-claude", skills: "-" });
  assert.deepEqual(log[1].args, base.concat(["add", "claude-copy", "--copy-of", "impl-claude", "--skills=", "--json"]));
  await roster({ op: "add", name: "plain", model: "anthropic/claude-sonnet-5-5", variant: "high", description: "--config x", prompt: "Work.\n", skills: "code-assurance,engineering-delivery", read_only: "true" });
  assert.deepEqual(log[2].args, base.concat(["add", "plain", "--model", "anthropic/claude-sonnet-5-5", "--variant", "high", "--description=--config x",
    "--skills", "code-assurance,engineering-delivery", "--read-only", "--prompt-file", "-", "--json"]));
  assert.equal(log[2].stdin, "Work.\n");
  doc = await roster({ op: "read", name: "checker" });
  assert.deepEqual(log[3].args, base.concat(["show", "checker", "--json"]));
  doc = await roster({ op: "text", name: "checker", prompt: "Check only the tests." });
  assert.deepEqual(log[4].args, base.concat(["text", "checker", "--text-file", "-", "--json"]));
  assert.equal(log[4].stdin, "Check only the tests.");
  await roster({ op: "remove", name: "checker" });
  assert.deepEqual(log[5].args, base.concat(["remove", "checker", "--json"]));
  await roster({ op: "restore", record: "/tmp/backups/2026-11-19-agent-files/remove-checker-0123456789ab/record.json" });
  assert.deepEqual(log[6].args, base.concat(["restore", "/tmp/backups/2026-11-19-agent-files/remove-checker-0123456789ab/record.json", "--json"]));
  await roster({ op: "rename", name: "checker", to: "auditor" });
  assert.deepEqual(log[7].args, base.concat(["rename", "checker", "auditor", "--json"]));
  assert.equal(log[7].stdin, "");
  // skill files
  const skill = async (fields) => JSON.parse(await impl.skillfile({ op: "", id: "", record: "", description: "", text: "", assign: "", ...fields }));
  const sbase = ["-P", "-m", "agentgraph.skillfiles", "skill"];
  log.length = 0;
  assert.equal((await skill({ op: "read", id: "code-assurance" })).id, "code-assurance");
  assert.deepEqual(log[0].args, sbase.concat(["show", "code-assurance", "--json"]));
  await skill({ op: "create", id: "new-skill", description: "A skill.", text: "Body", assign: "impl-openai" });
  assert.deepEqual(log[1].args, sbase.concat(["create", "new-skill", "--text-file", "-", "--assign", "impl-openai", "--description=A skill.", "--json"]));
  assert.equal(log[1].stdin, "Body");
  await skill({ op: "write", id: "new-skill", text: "Body 2" });
  assert.deepEqual(log[2].args, sbase.concat(["write", "new-skill", "--text-file", "-", "--json"]));
  await skill({ op: "delete", id: "new-skill" });
  assert.deepEqual(log[3].args, sbase.concat(["delete", "new-skill", "--json"]));
  await skill({ op: "restore", record: "/tmp/b/r.json" });
  assert.deepEqual(log[4].args, sbase.concat(["restore", "/tmp/b/r.json", "--json"]));
  // groups
  const team = async (fields) => JSON.parse(await impl.team({ op: "", id: "", lead: "", members: "", text: "", ...fields }));
  const tbase = ["-P", "-m", "agentgraph.teams"];
  log.length = 0;
  assert.deepEqual((await team({ op: "add", lead: "reviewer", members: "impl-openai impl-claude", text: "Reconcile." })).teams, []);
  assert.deepEqual(log[0].args, tbase.concat(["add", "reviewer", "impl-openai", "impl-claude", "--text-file", "-", "--json"]));
  assert.equal(log[0].stdin, "Reconcile.");
  await team({ op: "members", id: "reviewer", members: "impl-openai" });
  assert.deepEqual(log[1].args, tbase.concat(["members", "reviewer", "impl-openai", "--json"]));
  await team({ op: "text", id: "reviewer", text: "New." });
  assert.deepEqual(log[2].args, tbase.concat(["text", "reviewer", "--text-file", "-", "--json"]));
  await team({ op: "remove", id: "reviewer" });
  assert.deepEqual(log[3].args, tbase.concat(["remove", "reviewer", "--json"]));
  // refused before anything runs
  log.length = 0;
  for (const fields of [{ op: "add", name: "-x", template: "verifier" }, { op: "add", name: "a b", template: "verifier" }, { op: "add", name: "x", template: "nope" },
    { op: "add", name: "x", template: "verifier", copy_of: "impl-claude" }, { op: "add", name: "x", copy_of: "-impl" }, { op: "add", name: "x", model: "--config" , description: "d", prompt: "p" },
    { op: "add", name: "x", skills: "a,-b", template: "verifier" }, { op: "add", name: "x" }, { op: "read", name: "x", prompt: "p" }, { op: "text", name: "x", prompt: " " },
    { op: "text", name: "x", prompt: "p", template: "verifier" }, { op: "remove", name: "x", record: "/r" }, { op: "restore", record: "-r" }, { op: "restore", record: "relative/r.json" },
    { op: "show", name: "x" }, { op: "add", name: "x", template: "verifier", read_only: "yes" },
    { op: "rename", name: "x" }, { op: "rename", name: "x", to: "-y" }, { op: "rename", name: "x", to: "a b" }, { op: "rename", name: "-x", to: "y" },
    { op: "rename", name: "x", to: "y", prompt: "p" }, { op: "rename", name: "x", to: "y", record: "/r" }, { op: "remove", name: "x", to: "y" },
    { op: "restore", record: "/r.json", to: "y" }, { op: "add", name: "x", template: "verifier", to: "y" }])
    assert.equal(typeof (await roster(fields)).error, "string", JSON.stringify(fields));
  assert.equal(typeof JSON.parse(await impl.roster({ op: "add", name: "x" })).error, "string", "missing fields");
  for (const fields of [{ op: "read", id: "-x" }, { op: "write", id: "x", text: "t", assign: "a" }, { op: "create", id: "x", text: "t" }, { op: "restore", record: "r" }, { op: "rename", id: "x" }])
    assert.equal(typeof (await skill(fields)).error, "string", JSON.stringify(fields));
  for (const fields of [{ op: "add", lead: "-r", members: "a" }, { op: "add", lead: "r", members: "" }, { op: "add", lead: "r", members: "a -b" }, { op: "members", id: "r", members: "a", text: "t" },
    { op: "remove", id: "r", text: "t" }, { op: "remove", id: "-r" }, { op: "rename", id: "r" }])
    assert.equal(typeof (await team(fields)).error, "string", JSON.stringify(fields));
  const demo = mainContext({ home: ORCHESTRATION, python, demo: true });
  resolveDefault(loaded.module.exports).main(demo.ctx);
  assert.match(JSON.parse(await demo.provided[0].impl.roster({ ...blank, op: "remove", name: "checker" })).error, /demo/);
  assert.match(JSON.parse(await demo.provided[0].impl.skillfile({ op: "delete", id: "x", record: "", description: "", text: "", assign: "" })).error, /demo/);
  assert.match(JSON.parse(await demo.provided[0].impl.team({ op: "remove", id: "x", lead: "", members: "", text: "" })).error, /demo/);
  assert.equal(log.length, 0, "invalid requests and the demo spawn nothing");
});

await check("panel render: the view gets actions.roster, actions.skillfile and actions.team, which ask main with string fields and turn an error into a rejection", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const call = mountCalls[mountCalls.length - 1];
  const { roster, skillfile, team } = call.options.actions;
  for (const action of [roster, skillfile, team]) assert.equal(typeof action, "function");
  const empty = { name: "", record: "", model: "", variant: "", description: "", prompt: "", skills: "", read_only: "", copy_of: "", template: "", to: "" };
  await roster({ op: "add", name: "checker", template: "verifier" });
  await roster({ op: "add", name: "claude-copy", copy_of: "impl-claude", skills: [], read_only: false });
  await roster({ op: "read", name: "checker" });
  await roster({ op: "text", name: "checker", text: "New." });
  const removed = await roster({ op: "remove", name: "checker" });
  assert.equal(removed.record, "/tmp/backups/undo.json");
  await roster({ op: "restore", record: removed.record });
  await roster({ op: "rename", name: "checker", to: "auditor", text: "ignored" });
  assert.deepEqual(w.calls.filter((c) => c.op === "roster").map((c) => c.input), [
    { ...empty, op: "add", name: "checker", template: "verifier" },
    { ...empty, op: "add", name: "claude-copy", copy_of: "impl-claude", skills: "-", read_only: "false" },
    { ...empty, op: "read", name: "checker" },
    { ...empty, op: "text", name: "checker", prompt: "New." },
    { ...empty, op: "remove", name: "checker" },
    { ...empty, op: "restore", record: "/tmp/backups/undo.json" },
    { ...empty, op: "rename", name: "checker", to: "auditor" },
  ]);
  await skillfile({ op: "create", id: "new-skill", description: "D", text: "T", assign: "impl-openai" });
  await skillfile({ op: "restore", record: "/tmp/r.json" });
  assert.deepEqual(w.calls.filter((c) => c.op === "skillfile").map((c) => c.input), [
    { op: "create", id: "new-skill", record: "", description: "D", text: "T", assign: "impl-openai" },
    { op: "restore", id: "", record: "/tmp/r.json", description: "", text: "", assign: "" },
  ]);
  const made = await team({ op: "add", lead: "reviewer", members: ["impl-openai", "impl-claude"], text: "R." });
  assert.equal(made.team.id, "reviewer");
  await team({ op: "remove", id: "reviewer" });
  assert.deepEqual(w.calls.filter((c) => c.op === "team").map((c) => c.input), [
    { op: "add", id: "", lead: "reviewer", members: "impl-openai impl-claude", text: "R." },
    { op: "remove", id: "reviewer", lead: "", members: "", text: "" },
  ]);
  const failure = async (action, request) => {
    try {
      await action(request);
    } catch (error) {
      return error.message;
    }
    return "";
  };
  assert.match(await failure(roster, { op: "remove", name: "orchestrator" }), /cannot be removed/);
  assert.match(await failure(skillfile, { op: "delete", id: "shopify-ruby" }), /Shopify's own copy/);
  assert.match(await failure(team, { op: "add", lead: "orchestrator", members: ["a"] }), /hub/);
  const count = w.calls.length;
  for (const bad of [null, { op: "show", name: "x" }, { op: "add", name: "-x" }, { op: "add", name: "x", skills: "a" }, { op: "add", name: "x", read_only: "true" },
    { op: "text", name: "x" }, { op: "restore", record: "-r" }, { op: "add", name: "x", copy_of: "a b" },
    { op: "rename", name: "x" }, { op: "rename", name: "x", to: "-y" }, { op: "rename", name: "x", to: "a b" }, { op: "rename", name: "-x", to: "y" }])
    assert.equal(await failure(roster, bad), "Bad roster request.", JSON.stringify(bad));
  for (const bad of [null, { op: "read", id: "-x" }, { op: "write", id: "x", text: 3 }, { op: "restore" }])
    assert.equal(await failure(skillfile, bad), "Bad skill request.", JSON.stringify(bad));
  for (const bad of [null, { op: "add", lead: "r", members: [] }, { op: "members", id: "r", members: ["-a"] }, { op: "text", id: "r", text: 3 }])
    assert.equal(await failure(team, bad), "Bad group request.", JSON.stringify(bad));
  assert.equal(w.calls.length, count, "a bad request reached main");
  rendered.dispose();
});

await check("every change but an add, a read and a restore passes the view's rev as `--expect REV` before --json; a stale answer keeps its rev; a bad rev spawns nothing", async () => {
  const python = path.join(TMP, "fake-expect-python");
  fs.writeFileSync(python, `#!/bin/sh
cat > /dev/null
case "$*" in
  *"--expect ${STALE_REV}"*) printf '{"error": "flow x changed since you opened it; reload and try again", "rev": "${CURRENT_REV}"}\\n'; exit 1;;
esac
case "$3" in
  agentgraph.structure) printf '{"flows": [], "text": "message"}\\n';;
  agentgraph.skillset) printf '{"agent": "a", "skills": []}\\n';;
  agentgraph.roster) printf '{"agent": "a", "action": "x"}\\n';;
  agentgraph.skillfiles) printf '{"id": "a"}\\n';;
  agentgraph.teams) printf '{"teams": []}\\n';;
esac
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const rev = "0123456789abcdef";
  const tail = ["--expect", rev, "--json"];
  const blank = { op: "", name: "", record: "", model: "", variant: "", description: "", prompt: "", skills: "", read_only: "", copy_of: "", template: "", to: "" };
  const flowBlank = { op: "", id: "", path: "", text: "" };
  const calls = [
    [impl.flow, { ...flowBlank, op: "text", id: "f", text: "T", expect: rev }, ["text", "f", "--text-file", "-"]],
    [impl.flow, { ...flowBlank, op: "name", id: "f", text: "nm", expect: rev }, ["name", "f", "nm"]],
    [impl.flow, { ...flowBlank, op: "command", id: "f", text: "on", expect: rev }, ["command", "f", "--on"]],
    [impl.flow, { ...flowBlank, op: "prompt", id: "f", text: "goal", expect: rev }, ["prompt", "f", "--goal-file", "-"]],
    [impl.flow, { ...flowBlank, op: "remove", id: "f", expect: rev }, ["remove", "f"]],
    [impl.skills, { op: "add", agent: "impl-openai", skill: "code-assurance", expect: rev }, ["add", "impl-openai", "code-assurance"]],
    [impl.skills, { op: "all", agent: "impl-openai", skill: "", expect: rev }, ["all", "impl-openai"]],
    [impl.roster, { ...blank, op: "text", name: "checker", prompt: "P", expect: rev }, ["text", "checker", "--text-file", "-"]],
    [impl.roster, { ...blank, op: "rename", name: "checker", to: "auditor", expect: rev }, ["rename", "checker", "auditor"]],
    [impl.roster, { ...blank, op: "remove", name: "checker", expect: rev }, ["remove", "checker"]],
    [impl.skillfile, { op: "write", id: "s", record: "", description: "", text: "T", assign: "", expect: rev }, ["write", "s", "--text-file", "-"]],
    [impl.skillfile, { op: "delete", id: "s", record: "", description: "", text: "", assign: "", expect: rev }, ["delete", "s"]],
    [impl.team, { op: "members", id: "r", lead: "", members: "a b", text: "", expect: rev }, ["members", "r", "a", "b"]],
    [impl.team, { op: "text", id: "r", lead: "", members: "", text: "T", expect: rev }, ["text", "r", "--text-file", "-"]],
    [impl.team, { op: "remove", id: "r", lead: "", members: "", text: "", expect: rev }, ["remove", "r"]],
  ];
  for (const [method, input, args] of calls) {
    log.length = 0;
    const doc = JSON.parse(await method(input));
    assert.equal(doc.error, undefined, JSON.stringify(input));
    assert.deepEqual(log[0].args.slice(-args.length - tail.length), args.concat(tail), JSON.stringify(input));
  }
  log.length = 0;
  await impl.team({ op: "remove", id: "r", lead: "", members: "", text: "" });
  assert.deepEqual(log[0].args, ["-P", "-m", "agentgraph.teams", "remove", "r", "--json"], "no expect: the command as before");
  const stale = JSON.parse(await impl.flow({ ...flowBlank, op: "remove", id: "f", expect: STALE_REV }));
  assert.deepEqual(stale, { error: "flow x changed since you opened it; reload and try again", rev: CURRENT_REV });
  log.length = 0;
  for (const [method, input] of [[impl.flow, { ...flowBlank, op: "add", path: "a b", expect: rev }], [impl.flow, { ...flowBlank, op: "remove", id: "f", expect: "--json" }],
    [impl.flow, { ...flowBlank, op: "remove", id: "f", expect: "ABCDEF0123456789" }], [impl.flow, { ...flowBlank, op: "remove", id: "f", expect: 7 }],
    [impl.flow, { ...flowBlank, op: "remove", id: "f", expect: "" }], [impl.team, { op: "remove", id: "r", lead: "", members: "", text: "", expect: "" }],
    [impl.skills, { op: "add", agent: "impl-openai", skill: "code-assurance", expect: "" }], [impl.roster, { ...blank, op: "remove", name: "checker", expect: "" }],
    [impl.skillfile, { op: "delete", id: "s", record: "", description: "", text: "", assign: "", expect: "" }], [impl.flow, { ...flowBlank, op: "remove", id: "f", expect: null }],
    [impl.roster, { ...blank, op: "read", name: "checker", expect: rev }], [impl.roster, { ...blank, op: "restore", record: "/r.json", expect: rev }],
    [impl.roster, { ...blank, op: "add", name: "x", template: "verifier", expect: rev }], [impl.skillfile, { op: "read", id: "s", record: "", description: "", text: "", assign: "", expect: rev }],
    [impl.team, { op: "add", id: "", lead: "r", members: "a", text: "", expect: rev }],
    // Review n8 r5, note: a blank or short rev is bad input here as on the endpoints and the commands.
    [impl.roster, { ...blank, op: "remove", name: "checker", expect: " " }], [impl.skills, { op: "add", agent: "impl-openai", skill: "code-assurance", expect: " " }],
    [impl.flow, { ...flowBlank, op: "remove", id: "f", expect: "0".repeat(15) }], [impl.skillfile, { op: "delete", id: "s", record: "", description: "", text: "", assign: "", expect: " " }]])
    assert.equal(typeof JSON.parse(await method(input)).error, "string", JSON.stringify(input));
  assert.equal(log.length, 0, "a bad or misplaced rev spawned a command");
});

await check("panel render: every action passes `expect` to main as it is, and a stale refusal rejects with the current rev", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { flow, skills, roster, skillfile, team } = mountCalls[mountCalls.length - 1].options.actions;
  const rev = "0123456789abcdef";
  await flow({ op: "text", id: "f", text: "T", expect: rev });
  await skills({ agent: "impl-openai", skill: "alpha", on: true, expect: rev });
  await roster({ op: "remove", name: "checker", expect: rev });
  await skillfile({ op: "delete", id: "s", expect: rev });
  await team({ op: "remove", id: "r", expect: rev });
  await team({ op: "remove", id: "r" });
  const sent = w.calls.filter((c) => c.input).map((c) => c.input.expect);
  assert.deepEqual(sent, [rev, rev, rev, rev, rev, undefined]);
  for (const [action, request] of [[flow, { op: "remove", id: "f" }], [skills, { agent: "impl-openai", skill: "alpha", on: false }],
    [roster, { op: "text", name: "checker", text: "x" }], [skillfile, { op: "write", id: "s", text: "x" }], [team, { op: "text", id: "r", text: "x" }]]) {
    let caught = null;
    try {
      await action({ ...request, expect: STALE_REV });
    } catch (error) {
      caught = error;
    }
    assert.ok(caught, JSON.stringify(request));
    assert.match(caught.message, /changed since you opened it/);
    assert.equal(caught.rev, CURRENT_REV);
  }
  const count = w.calls.length;
  for (const [action, request, message] of [[flow, { op: "remove", id: "f", expect: 5 }, "Bad flow request."], [team, { op: "remove", id: "r", expect: [] }, "Bad group request."],
    [flow, { op: "remove", id: "f", expect: "" }, "Bad flow request."], [skills, { agent: "impl-openai", skill: "alpha", on: false, expect: "" }, "Bad skills request."],
    [roster, { op: "remove", name: "checker", expect: "" }, "Bad roster request."], [skillfile, { op: "delete", id: "s", expect: "" }, "Bad skill request."],
    [team, { op: "remove", id: "r", expect: "" }, "Bad group request."], [flow, { op: "remove", id: "f", expect: null }, "Bad flow request."],
    // Review n8 r5, note: the view refuses what main refuses: a blank, short or capital rev never reaches it.
    [roster, { op: "remove", name: "checker", expect: " " }, "Bad roster request."], [team, { op: "remove", id: "r", expect: "0".repeat(15) }, "Bad group request."],
    [skills, { agent: "impl-openai", skill: "alpha", on: false, expect: "ABCDEF0123456789" }, "Bad skills request."]]) {
    let text = "";
    try {
      await action(request);
    } catch (error) {
      text = error.message;
    }
    assert.equal(text, message);
  }
  assert.equal(w.calls.length, count, "a bad expect reached main");
  rendered.dispose();
});

// The host decodes each Ipc input with its schema, as Effect's Schema.Struct does by default: a declared field must
// have its type (an optionalKey may be absent), and a key the schema does not declare is dropped.
function hostDecode(schema, input) {
  assert.equal(schema._tag, "Struct");
  const out = {};
  for (const [key, field] of Object.entries(schema.fields)) {
    const optional = field._tag === "optionalKey";
    const type = optional ? field.schema : field;
    if (optional && (!Object.prototype.hasOwnProperty.call(input, key) || input[key] === undefined)) continue;
    assert.equal(type._tag, "String", key);
    if (typeof input[key] !== "string") throw new Error(`input: ${key} is not a string`);
    out[key] = input[key];
  }
  return out;
}

// n99 r4 (from the documentation lane): `expect` must cross the host's bridge. Each op that takes a rev, sent by the
// panel's own action, goes through the window's Ipc token, hostDecode and the real main, and the command line main
// runs ends with `--expect REV --json`.
await check("renderer to command line through the host's schema decoding: every op that takes a rev arrives as --expect REV", async () => {
  const python = path.join(TMP, "fake-bridge-python");
  fs.writeFileSync(python, `#!/bin/sh
cat > /dev/null
case "$3" in
  agentgraph.structure) case "$*" in *" prompt "*) printf '{"text": "run it"}\\n';; *) printf '{"flows": []}\\n';; esac;;
  agentgraph.skillset) printf '{"agent": "impl-openai", "skills": []}\\n';;
  agentgraph.roster) printf '{"agent": "checker", "action": "x"}\\n';;
  agentgraph.skillfiles) printf '{"id": "s"}\\n';;
  agentgraph.teams) printf '{"teams": []}\\n';;
esac
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;

  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  const decoded = [];
  w.ctx.main = (token) => Object.fromEntries(Object.keys(token.spec.methods).map((method) => [method, async (input) => {
    const value = hostDecode(token.spec.methods[method].input, input);
    decoded.push({ method, value });
    return impl[method](value, { window: 1, signal: new AbortController().signal });
  }]));
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { flow, skills, roster, skillfile, team } = mountCalls[mountCalls.length - 1].options.actions;
  const rev = "0123456789abcdef";
  const ops = [
    ["flow text", flow, { op: "text", id: "f", text: "T" }],
    ["flow name", flow, { op: "name", id: "f", name: "nm" }],
    ["flow command", flow, { op: "command", id: "f", on: true }],
    ["flow prompt", flow, { op: "prompt", id: "f", goal: "a goal" }],
    ["flow remove", flow, { op: "remove", id: "f" }],
    ["skills add", skills, { agent: "impl-openai", skill: "code-assurance", on: true }],
    ["skills remove", skills, { agent: "impl-openai", skill: "code-assurance", on: false }],
    ["skills all", skills, { agent: "impl-openai", all: true }],
    ["agent text", roster, { op: "text", name: "checker", text: "New." }],
    ["agent rename", roster, { op: "rename", name: "checker", to: "auditor" }],
    ["agent remove", roster, { op: "remove", name: "checker" }],
    ["skill write", skillfile, { op: "write", id: "s", text: "New." }],
    ["skill delete", skillfile, { op: "delete", id: "s" }],
    ["group members", team, { op: "members", id: "r", members: ["a", "b"] }],
    ["group text", team, { op: "text", id: "r", text: "New." }],
    ["group remove", team, { op: "remove", id: "r" }],
  ];
  for (const [what, action, request] of ops) {
    log.length = 0;
    await action({ ...request, expect: rev });
    assert.equal(log.length, 1, what);
    assert.deepEqual(log[0].args.slice(-3), ["--expect", rev, "--json"], `${what}: ${log[0].args.join(" ")}`);
    log.length = 0;
    await action(request);
    assert.equal(log[0].args.includes("--expect"), false, `${what} without a rev runs as before`);
  }
  assert.equal(decoded.length, ops.length * 2);
  rendered.dispose();
  // The decoding is the host's: an undeclared key is dropped, so without the declaration the rev would be lost.
  const undeclared = { _tag: "Struct", fields: { op: { _tag: "String" } } };
  assert.deepEqual(hostDecode(undeclared, { op: "remove", expect: rev }), { op: "remove" });
});

// Review n8 r5 (major): the server path was tested, the Desktop path was not. Here the plugin's own main runs its
// exact command lines against a scratch copy of the orchestration (options.home) with HOME in scratch, so every
// default folder (agents, commands, backups, skills, the preset file, structure/, SKILLS.md) is a scratch one: the
// snapshot runs in the session's folder, each writer in home, and a rev the snapshot shows must be the one each
// writer checks, for every kind (agent text, skills, rename, remove; flow text; group text; skill text).
await check("real Python on scratch folders: one write per kind with the snapshot's rev is accepted, an older rev is refused with the current one", async () => {
  const scratch = path.join(fs.realpathSync(TMP), "desktop-paths"); // the writers refuse a folder through a symlink (/var)
  const orch = path.join(scratch, "orchestration");
  const home = path.join(scratch, "home");
  const config = path.join(home, ".config", "opencode");
  const project = path.join(scratch, "project");
  fs.cpSync(path.join(ORCHESTRATION, "agentgraph"), path.join(orch, "agentgraph"), {
    recursive: true,
    filter: (source) => !/(^|\/)(__pycache__|web)$/.test(source),
  });
  fs.cpSync(path.join(ORCHESTRATION, "structure"), path.join(orch, "structure"), { recursive: true });
  fs.copyFileSync(path.join(ORCHESTRATION, "SKILLS.md"), path.join(orch, "SKILLS.md"));
  for (const folder of ["agents", "commands", "backups", "skills/code-assurance"]) fs.mkdirSync(path.join(config, folder), { recursive: true });
  fs.writeFileSync(path.join(config, "skills/code-assurance/SKILL.md"), "---\nname: code-assurance\ndescription: a skill\n---\nBody\n");
  // The preset file is copied (never read into this script, never written, never printed).
  fs.copyFileSync(path.join(os.homedir(), ".config/opencode/oh-my-opencode-slim.json"), path.join(config, "oh-my-opencode-slim.json"));
  fs.mkdirSync(project);

  const definition = resolveDefault(mainLoad.module.exports);
  const m = mainContext({ home: orch });
  await definition.main(m.ctx);
  const impl = m.provided[0].impl;
  const realHome = process.env.HOME;
  const OLD = "0123456789abcdef";
  const caller = { window: 1, signal: new AbortController().signal };
  const blank = { op: "", name: "", record: "", model: "", variant: "", description: "", prompt: "", skills: "", read_only: "", copy_of: "", template: "", to: "" };
  const call = async (method, input) => JSON.parse(await impl[method](input, caller));
  const snapshot = () => call("snapshot", { directory: project, session: "" });
  const rev = async (pick) => {
    const document = await snapshot();
    assert.equal(document.error, undefined, String(document.error));
    const found = pick(document);
    assert.ok(found && /^[0-9a-f]{16}$/.test(found.rev || ""), "the snapshot shows a rev");
    return found.rev;
  };
  const accepted = (answer, what) => assert.equal(answer.error, undefined, `${what}: ${answer.error}`);
  const refused = (answer, what) => {
    assert.match(answer.error || "", /reload and try again/, `${what}: ${JSON.stringify(answer)}`);
    assert.match(answer.rev || "", /^[0-9a-f]{16}$/, `${what}: ${JSON.stringify(answer)}`);
  };
  // Each kind: an older rev is refused, the snapshot's rev goes ahead.
  const both = async (what, method, input, revOf) => {
    const current = await rev(revOf);
    refused(await call(method, { ...input, expect: OLD }), what);
    accepted(await call(method, { ...input, expect: current }), what);
  };
  const agent = (name) => (document) => document.agents.find((item) => item.name === name);
  const spawned = spawns.length;
  process.env.HOME = home;
  try {
    accepted(await call("roster", { ...blank, op: "add", name: "xavier", template: "verifier" }), "add");
    await both("agent text", "roster", { ...blank, op: "text", name: "xavier", prompt: "Mine." }, agent("xavier"));
    await both("added agent's skills", "skills", { op: "add", agent: "xavier", skill: "code-assurance" }, agent("xavier"));
    await both("preset agent's skills", "skills", { op: "add", agent: "impl-claude", skill: "code-assurance" }, agent("impl-claude"));
    await both("rename", "roster", { ...blank, op: "rename", name: "xavier", to: "yusuf" }, agent("xavier"));
    await both("remove", "roster", { ...blank, op: "remove", name: "yusuf" }, agent("yusuf"));
    await both("preset agent's remove", "roster", { ...blank, op: "remove", name: "impl-hard" }, agent("impl-hard"));
    const flow = await call("flow", { op: "add", id: "", path: "impl-openai reviewer", text: "" });
    accepted(flow, "flow add");
    const flowId = (await snapshot()).structure.flows.find((item) => item.path.join(" ") === "impl-openai reviewer").id;
    await both("flow text", "flow", { op: "text", id: flowId, path: "", text: "Mine." },
      (document) => document.structure.flows.find((item) => item.id === flowId));
    accepted(await call("team", { op: "add", id: "", lead: "reviewer", members: "impl-openai", text: "" }), "group add");
    const teamId = (await snapshot()).structure.teams.find((item) => item.lead === "reviewer").id;
    await both("group text", "team", { op: "text", id: teamId, lead: "", members: "", text: "Mine." },
      (document) => document.structure.teams.find((item) => item.id === teamId));
    await both("skill text", "skillfile", { op: "write", id: "code-assurance", record: "", description: "", text: "Mine.\n", assign: "" },
      (document) => document.skills.find((item) => item.name === "code-assurance"));
  } finally {
    process.env.HOME = realHome;
  }
  const ran = spawns.slice(spawned);
  assert.ok(ran.length > 20, "the plugin's own command lines ran");
  for (const entry of ran) {
    assert.equal(entry.cwd, entry.args.includes("agentgraph.snapshot_json") ? project : orch, entry.args.join(" "));
    assert.equal(entry.args.some((arg) => /^--(structure|agents|config|map|backups|commands|skills)\b/.test(arg)), false,
      "the plugin passes no folder: " + entry.args.join(" "));
  }
});

await check("the name patterns are agentgraph/names.py's own: AGENT_NAME, FLOW_ID, FLOW_NAME and SKILL_NAME equal them", () => {
  const python = childProcess.execFileSync("python3", ["-P", "-c", "import json; from agentgraph import names; print(json.dumps(names.PLUGIN_CONSTANTS))"],
    { cwd: ORCHESTRATION, env: { ...process.env, PYTHONPATH: ORCHESTRATION }, encoding: "utf8" });
  const wanted = JSON.parse(python);
  const source = fs.readFileSync(path.join(PLUGIN_DIR, "src", "plugin.cjs"), "utf8");
  for (const [constant, pattern] of Object.entries(wanted)) {
    const found = source.match(new RegExp("^const " + constant + " = /(.*)/;", "m"));
    assert.ok(found, constant);
    assert.equal(found[1], "^" + pattern + "$", constant);
  }
});

await check("flow counts the instruction limit in characters: 2000 emoji (4000 UTF-16 units) go to the command, 2001 do not", async () => {
  const python = path.join(TMP, "fake-structure-python");
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  await impl.flow({ op: "text", id: "f", path: "", text: "\u{1F600}".repeat(2000) });
  assert.equal(log.length, 1, "2000 characters were refused");
  assert.equal(Array.from(log[0].stdin).length, 2000);
  const refused = JSON.parse(await impl.flow({ op: "text", id: "f", path: "", text: "\u{1F600}".repeat(2001) }));
  assert.match(refused.error, /2000 characters/);
  assert.equal(log.length, 1, "2001 characters reached the command");
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
