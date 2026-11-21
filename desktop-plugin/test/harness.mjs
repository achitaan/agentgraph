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
const LOOP_REV_NOW = "ab".repeat(32); // a loop settings rev: the file's sha256

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
        // n10: the default preset, the saved setups and the spend limit.
        setup: async (input, options) => {
          calls.push({ op: "setup", input: JSON.parse(JSON.stringify(input)), options });
          if (input.expect === STALE_REV) return JSON.stringify({ error: "it changed since you opened it; reload and try again", rev: CURRENT_REV });
          if (input.name === "damaged") return JSON.stringify({ error: "'damaged' is damaged: a file is missing from it" });
          return JSON.stringify({ name: input.name, changed: true, record: "/tmp/backups/undo.json", message: "done" });
        },
        spend: async (input, options) => {
          calls.push({ op: "spend", input: JSON.parse(JSON.stringify(input)), options });
          if (input.limit === "999.00") return JSON.stringify({ error: "the limit is too high for this test" });
          return JSON.stringify({ limit: input.op === "off" ? null : Number(input.limit), warn: null, previous: { limit: null, warn: null }, changed: true, message: "set" });
        },
        // n24: the improvement proposals (read the list or one, approve, reject, reopen, revert).
        improve: async (input, options) => {
          calls.push({ op: "improve", input: JSON.parse(JSON.stringify(input)), options });
          if (input.id === "p-refused") return JSON.stringify({ error: "file agents/impl.md changed since the proposal was drafted" });
          if (input.op === "read" && !input.id) return JSON.stringify({ proposals: [{ id: "p-1", title: "T", state: "drafted", created: 1, added: 1, removed: 0, files: ["agents/a.md"], summary: { verdict: "not enough runs" } }] });
          return JSON.stringify({ id: input.id, state: { approve: "applied", reject: "rejected", reopen: "drafted", revert: "reverted", read: "drafted" }[input.op], files: [], history: [] });
        },
        // v4: loops (the page's request as JSON text, with the session's directory): the loops, a run, a save, a start, a cancel.
        loops: async (input, options) => {
          calls.push({ op: "loops", input: JSON.parse(JSON.stringify(input)), options });
          const request = JSON.parse(input.request);
          if (request.flow === "stale-loop") return JSON.stringify({ error: "the settings changed since you read them", rev: LOOP_REV_NOW });
          if (request.flow === "refused-loop") return JSON.stringify({ error: "a loop needs limits.attempts" });
          if (request.flow === "old-main-loop") throw new Error('Ipc "agentgraph-desktop.snapshot" is unavailable: no method "loops"');
          if (request.op === "read" && request.run) return JSON.stringify({ run: { run: request.run, status: "passed" }, definition: {}, attempts: [] });
          if (request.op === "read") return JSON.stringify({ project: input.directory, scratch: false, loops: [{ flow: "fix-loop", runnable: true }], orphans: [], runs: [] });
          return JSON.stringify({ ok: true, op: request.op });
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
await check("main registers exactly one Ipc (<id>.snapshot) with snapshot, flow, skills, roster, skillfile, team, output and chat methods", async () => {
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
  // n110: the output method (a step's output: read, write, restore) joined them.
  // n14: the chat method (one session's recent chat: its id and a number of messages) joined them.
  // n8: the side method (one question to the side chat, sent on stdin) joined them (the key list above gains "side").
  // n10: the setup and spend methods (the preset, the saved setups, the spend limit) joined them.
  assert.deepEqual(Object.keys(token.spec.methods), ["snapshot", "flow", "skills", "roster", "skillfile", "team", "output", "chat", "side", "setup", "spend", "improve", "loops"]);
  // n24: the improve method (the improvement proposals) joined them.
  assert.deepEqual(Object.keys(token.spec.methods.improve.input.fields), ["op", "id", "note"]);
  assert.ok(Object.values(token.spec.methods.improve.input.fields).every((field) => field._tag === "String"), "improve: strings only");
  assert.ok(typeof impl.improve === "function");
  // v4: the loops method (the loop settings and runs of a self-loop flow) joined them.
  assert.deepEqual(Object.keys(token.spec.methods.loops.input.fields), ["directory", "request"]);
  assert.ok(Object.values(token.spec.methods.loops.input.fields).every((field) => field._tag === "String"), "loops: strings only");
  assert.ok(typeof impl.loops === "function");
  assert.deepEqual(Object.keys(token.spec.methods.setup.input.fields), ["op", "directory", "name", "record", "replace", "expect"]);
  assert.deepEqual(Object.keys(token.spec.methods.spend.input.fields), ["op", "directory", "limit", "warn"]);
  assert.ok(Object.entries(token.spec.methods.setup.input.fields).every(([key, field]) => key === "expect" ? field._tag === "optionalKey" && field.schema._tag === "String" : field._tag === "String"), "setup: strings and an optional expect");
  assert.ok(Object.values(token.spec.methods.spend.input.fields).every((field) => field._tag === "String"), "spend: strings only");
  assert.ok(typeof impl.setup === "function" && typeof impl.spend === "function");
  assert.deepEqual(Object.keys(token.spec.methods.chat.input.fields), ["session", "limit", "side"]);
  assert.equal(token.spec.methods.chat.input.fields.session._tag, "String");
  assert.equal(token.spec.methods.chat.input.fields.limit._tag, "Number");
  assert.equal(token.spec.methods.chat.output._tag, "String");
  assert.ok(typeof impl.chat === "function");
  // Round 3: roster, skillfile and team, each with string fields only.
  // n96: "to" (the new name of a rename) joined the roster's fields.
  // n99 r4: "expect" (the view's rev) is declared, optional, on every method whose ops take one: the host's decoding
  // drops undeclared keys. Every other field is a required string.
  const stringsAndExpect = (fields) => Object.entries(fields).every(([key, field]) => key === "expect"
    ? field._tag === "optionalKey" && field.schema._tag === "String" : field._tag === "String");
  assert.deepEqual(Object.keys(token.spec.methods.roster.input.fields), ["op", "name", "record", "model", "variant", "description", "prompt", "skills", "read_only", "copy_of", "template", "to", "expect"]);
  assert.deepEqual(Object.keys(token.spec.methods.skillfile.input.fields), ["op", "id", "record", "description", "text", "assign", "expect"]);
  assert.deepEqual(Object.keys(token.spec.methods.team.input.fields), ["op", "id", "lead", "members", "text", "expect"]);
  assert.deepEqual(Object.keys(token.spec.methods.output.input.fields), ["op", "directory", "task", "node", "artifact", "record", "text", "expect"]);
  for (const method of ["roster", "skillfile", "team", "output"]) {
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

// A scratch copy of structure/ leaves out the user's own drawn flows and groups (git-ignored): a flow with a command
// would block a removal, and the checks are about the shipped agents.
const withoutOwnDrawings = (source) => !["flows", "teams"].includes(path.basename(source));

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
  fs.cpSync(path.join(ORCHESTRATION, "structure"), path.join(home, "structure"), { recursive: true, filter: withoutOwnDrawings });
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

await check("panel render: the view gets actions.flow, which asks main and turns an error into a rejection; the demo passes only the chat", async () => {
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
  // n14: the demo is given the chat to read (the demo's example chats) and nothing that changes anything.
  assert.deepEqual(Object.keys(mountCalls[mountCalls.length - 1].options.actions), ["chat"], "the demo can draw flows or send");
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
    if (type._tag === "Number") {
      if (typeof input[key] !== "number") throw new Error(`input: ${key} is not a number`);
      out[key] = input[key];
      continue;
    }
    if (type._tag === "Boolean") {
      if (typeof input[key] !== "boolean") throw new Error(`input: ${key} is not a boolean`);
      out[key] = input[key];
      continue;
    }
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

// The host's PluginCall, as the installed app has it (read from its main bundle, never run): the input is decoded with
// the method's schema, main's answer is encoded with the output schema (a String here) and crosses as is, and an error
// main throws crosses as {code, message} only: any other property on it (a `rev`, a `status`) is lost.
async function hostCall(token, method, impl, input) {
  const value = hostDecode(token.spec.methods[method].input, input);
  let answer;
  try {
    answer = await impl[method](value, { window: 1, signal: new AbortController().signal });
  } catch (error) {
    throw new Error(error instanceof Error ? error.message : String(error));
  }
  assert.equal(typeof answer, "string", `${method} answers a String`);
  return structuredClone(answer);
}

// n99 r5 (from the verifier's note): a refusal over Ipc has the web page's shape. A stale refusal (the back end's reply
// carries the current rev) reaches the panel's action as an Error with status 409, stale true and that rev; any other
// refusal has no stale and no status (none is known: the commands have no HTTP status). The rev crosses the host's
// boundary inside main's answer (a String), and the Error is made on the window's side, so nothing depends on an
// error's own properties surviving the bridge, which they do not.
await check("a refusal over Ipc has the web page's shape: stale (409, stale, rev) or not (no stale, no status), through the host's call", async () => {
  const current = "fedcba9876543210";
  const python = path.join(TMP, "fake-refusing-python");
  fs.writeFileSync(python, `#!/bin/sh
cat > /dev/null
case "$*" in
  *" --expect "*) printf '{"error": "it was changed since you opened it", "rev": "${current}"}\\n';;
  *) printf '{"error": "refused for another reason"}\\n';;
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
  w.ctx.main = (token) => Object.fromEntries(Object.keys(token.spec.methods).map((method) =>
    [method, (input) => hostCall(token, method, impl, input)]));
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { flow, skills, roster, skillfile, team } = mountCalls[mountCalls.length - 1].options.actions;
  const ops = [
    ["flow text", flow, { op: "text", id: "f", text: "T" }],
    ["skills add", skills, { agent: "impl-openai", skill: "code-assurance", on: true }],
    ["agent text", roster, { op: "text", name: "checker", text: "New." }],
    ["skill write", skillfile, { op: "write", id: "s", text: "New." }],
    ["group text", team, { op: "text", id: "r", text: "New." }],
  ];
  const refusalOf = async (promise) => {
    try {
      await promise;
    } catch (error) {
      return error;
    }
    assert.fail("not refused");
  };
  for (const [what, action, request] of ops) {
    const stale = await refusalOf(action({ ...request, expect: "0123456789abcdef" }));
    assert.equal(Object.prototype.toString.call(stale), "[object Error]", what); // made in the window's realm
    assert.equal(stale.message, "it was changed since you opened it", `${what}: ${stale.message}`);
    assert.deepEqual({ status: stale.status, stale: stale.stale, rev: stale.rev }, { status: 409, stale: true, rev: current }, what);
    const other = await refusalOf(action(request));
    assert.equal(other.message, "refused for another reason", what);
    assert.equal("stale" in other, false, `${what}: not stale`);
    assert.equal(other.status, undefined, `${what}: no status is known`);
    assert.equal(other.rev, undefined, what);
  }
  rendered.dispose();
  // The host's boundary keeps an error's message only: properties put on an error in main would never arrive.
  const thrower = { flow: async () => { throw Object.assign(new Error("m"), { rev: current, status: 409 }); } };
  const lost = await refusalOf(hostCall({ spec: { methods: { flow: { input: { _tag: "Struct", fields: {} } } } } }, "flow", thrower, {}));
  assert.deepEqual([lost.message, lost.rev, lost.status], ["m", undefined, undefined]);
});

// Review n8 r6 (minor 3): a stale refusal needs both an error and a rev. Main forwards a rev only with an error
// (runCommand), but the window does not lean on that: an answer from main with no error, in a shape the action does
// not expect, that carries a rev (a success answer of another version, say) is refused as a broken answer: no stale,
// no status, no rev. An error with a rev is still stale.
await check("an answer that carries a rev but no error is not a stale refusal", async () => {
  const current = "fedcba9876543210";
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  let answer = "";
  w.ctx.main = (token) => Object.fromEntries(Object.keys(token.spec.methods).map((method) =>
    [method, async () => answer]));
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { flow, skills, roster, skillfile, team } = mountCalls[mountCalls.length - 1].options.actions;
  const ops = [
    ["flow text", flow, { op: "text", id: "f", text: "T" }],
    ["skills add", skills, { agent: "impl-openai", skill: "code-assurance", on: true }],
    ["agent text", roster, { op: "text", name: "checker", text: "New." }],
    ["skill write", skillfile, { op: "write", id: "s", text: "New." }],
    ["group text", team, { op: "text", id: "r", text: "New." }],
  ];
  const refusalOf = async (promise) => {
    try {
      await promise;
    } catch (error) {
      return error;
    }
    assert.fail("not refused");
  };
  for (const [what, action, request] of ops) {
    for (const doc of [{ rev: current, unexpected: true }, { error: "", rev: current }, { error: null, rev: current }]) {
      answer = JSON.stringify(doc);
      const refused = await refusalOf(action({ ...request, expect: "0123456789abcdef" }));
      assert.equal(Object.prototype.toString.call(refused), "[object Error]", what);
      assert.deepEqual([("stale" in refused), refused.status, refused.rev], [false, undefined, undefined],
        `${what} ${answer}: ${refused.message}`);
    }
    answer = JSON.stringify({ error: "it was changed since you opened it", rev: current });
    const stale = await refusalOf(action({ ...request, expect: "0123456789abcdef" }));
    assert.deepEqual([stale.message, stale.status, stale.stale, stale.rev],
      ["it was changed since you opened it", 409, true, current], what);
  }
  rendered.dispose();
});

// n110: a step's output, opened and edited from the graph view. The panel's own `output` action goes through the
// window's Ipc token, the host's decoding and the real main: a write with the rev the view showed arrives as
// `--expect REV --json` after the text on stdin and the session's folder as `--from`, a read and an undo take no rev,
// and a stale refusal reaches the panel in the web page's shape (409, stale, the current rev).
await check("output: read, write and undo cross the host's decoding; a write's rev arrives as --expect REV and a stale one is a 409 refusal", async () => {
  const current = "fedcba9876543210";
  const python = path.join(TMP, "fake-outputs-python");
  fs.writeFileSync(python, `#!/bin/sh
cat > /dev/null
case "$*" in
  *" --expect 0000000000000000 "*) printf '{"error": "step n1\\x27s output report changed since you opened it; reload and try again", "rev": "${current}"}\\n';;
  *" no-such-output "*) printf '{"error": "step n1 has no output"}\\n';;
  *" restore "*) printf '{"task": "t1", "node": "n1", "artifact": "report", "restored": true, "rev": "${current}"}\\n';;
  *) printf '{"task": "t1", "node": "n1", "artifact": "report", "rev": "${current}", "text": "x"}\\n';;
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
  w.ctx.main = (token) => Object.fromEntries(Object.keys(token.spec.methods).map((method) =>
    [method, (input) => hostCall(token, method, impl, input)]));
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { output } = mountCalls[mountCalls.length - 1].options.actions;
  assert.equal(typeof output, "function", "the panel has an output action");
  const where = { task: "t1", node: "n1", artifact: "report" };
  const base = ["-P", "-m", "agentgraph.outputs", "output"];

  const read = await output({ op: "read", ...where });
  assert.equal(read.text, "x");
  assert.deepEqual(log[0].args, base.concat(["read", "t1", "n1", "report", "--from", ORCHESTRATION, "--json"]));
  assert.equal(log[0].cwd, ORCHESTRATION);

  log.length = 0;
  const rev = "0123456789abcdef";
  const saved = await output({ op: "write", ...where, text: "new text\n", expect: rev });
  assert.equal(saved.rev, current);
  assert.deepEqual(log[0].args, base.concat(["write", "t1", "n1", "report", "--from", ORCHESTRATION, "--text-file", "-", "--expect", rev, "--json"]));
  assert.equal(log[0].stdin, "new text\n", "the text goes on stdin");
  log.length = 0;
  await output({ op: "write", ...where, text: "new text\n" });
  assert.equal(log[0].args.includes("--expect"), false, "a write without a rev runs as before");

  log.length = 0;
  const record = "/tmp/backups/2026-11-20-outputs/edit-0123456789ab/record.json";
  const undone = await output({ op: "restore", record });
  assert.equal(undone.restored, true);
  // Review round 7, minor 1: the session's folder goes with an undo too; the command takes back only a record of a
  // graph found from it.
  assert.deepEqual(log[0].args, base.concat(["restore", record, "--from", ORCHESTRATION, "--json"]));

  const refusalOf = async (promise) => {
    try {
      await promise;
    } catch (error) {
      return error;
    }
    assert.fail("not refused");
  };
  log.length = 0;
  const stale = await refusalOf(output({ op: "write", ...where, text: "mine\n", expect: STALE_REV }));
  assert.equal(Object.prototype.toString.call(stale), "[object Error]");
  assert.match(stale.message, /changed since you opened it; reload and try again/);
  assert.deepEqual({ status: stale.status, stale: stale.stale, rev: stale.rev }, { status: 409, stale: true, rev: current });
  const other = await refusalOf(output({ op: "read", task: "t1", node: "n1", artifact: "no-such-output" }));
  assert.equal(other.message, "step n1 has no output");
  assert.deepEqual(["stale" in other, other.status, other.rev], [false, undefined, undefined]);
  assert.equal(log.length, 2);

  // Refused before main: a rev on a read or an undo, a malformed rev, a name holding a space or starting with "-".
  for (const request of [
    { op: "read", ...where, expect: rev },
    { op: "restore", record, expect: rev },
    { op: "write", ...where, text: "x", expect: "ABCDEF0123456789" },
    { op: "write", ...where, text: "x", expect: "" },
    { op: "write", ...where, text: "x", expect: 7 },
    { op: "read", task: "t1", node: "-n1", artifact: "report" },
    { op: "read", task: "t 1", node: "n1", artifact: "report" },
    { op: "write", ...where },
    { op: "delete", ...where },
  ]) {
    log.length = 0;
    await refusalOf(output(request));
    assert.equal(log.length, 0, JSON.stringify(request));
  }
  rendered.dispose();

  // Main checks the same again, whatever reaches it.
  const blank = { op: "", directory: "", task: "", node: "", artifact: "", record: "", text: "" };
  const call = async (input) => JSON.parse(await impl.output(input, { window: 1, signal: new AbortController().signal }));
  log.length = 0;
  for (const input of [
    { ...blank, op: "read", directory: "relative", task: "t1", node: "n1", artifact: "report" },
    { ...blank, op: "read", directory: ORCHESTRATION, task: "--help", node: "n1", artifact: "report" },
    { ...blank, op: "read", directory: ORCHESTRATION, task: "t1", node: "n1", artifact: "report", expect: rev },
    { ...blank, op: "write", directory: ORCHESTRATION, task: "t1", node: "n1", artifact: "report", text: "a".repeat(256 * 1024 + 1) },
    { ...blank, op: "restore", record: "-x" },
    { ...blank, op: "restore", record, task: "t1" },
    { ...blank, op: "restore", record }, // no session folder: nothing to limit the undo to
    { ...blank, op: "restore", directory: "relative", record },
  ]) assert.match((await call(input)).error || "", /./, JSON.stringify(input).slice(0, 120));
  assert.equal(log.length, 0, "a bad request reached the command line");
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
  fs.cpSync(path.join(ORCHESTRATION, "structure"), path.join(orch, "structure"), { recursive: true, filter: withoutOwnDrawings });
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

// n110: the Desktop path for a step's output on real Python and scratch folders only. The snapshot runs in the
// session's folder (a project inside HOME, where graph discovery looks) and shows each output's rev; the outputs
// command runs in home with `--from` that folder, takes that rev as `--expect`, refuses an older one with the
// current rev and nothing changed, and after a write the step that read the old revision is stale. Undo puts the
// project back byte for byte.
await check("real Python on scratch folders: a step's output is read and written with the snapshot's rev, an older rev is refused, undo restores it", async () => {
  const scratch = path.join(fs.realpathSync(TMP), "desktop-outputs");
  const orch = path.join(scratch, "orchestration");
  const home = path.join(scratch, "home");
  const project = path.join(home, "project");
  fs.cpSync(path.join(ORCHESTRATION, "agentgraph"), path.join(orch, "agentgraph"), {
    recursive: true,
    filter: (source) => !/(^|\/)(__pycache__|web)$/.test(source),
  });
  fs.cpSync(path.join(ORCHESTRATION, "structure"), path.join(orch, "structure"), { recursive: true, filter: withoutOwnDrawings });
  fs.copyFileSync(path.join(ORCHESTRATION, "SKILLS.md"), path.join(orch, "SKILLS.md"));
  fs.mkdirSync(path.join(home, ".config", "opencode"), { recursive: true });
  fs.mkdirSync(path.join(project, ".opencode", "graph"), { recursive: true });
  fs.mkdirSync(path.join(project, ".opencode", "artifacts", "t1", "n1", "r1"), { recursive: true });
  fs.writeFileSync(path.join(project, ".opencode", "artifacts", "t1", "n1", "r1", "report.md"), "line one\n");
  fs.writeFileSync(path.join(project, ".opencode", "graph", "t1.json"), JSON.stringify({
    task: "t1", goal: "g", status: "running", revision: 1, created: 1, updated: 2,
    objectives: [{ id: "o1", text: "x", nodes: ["n1", "n2"] }],
    nodes: [
      { id: "n1", type: "research", agent: "explorer", goal: "find", depends_on: [], scope: [], done_when: "list", status: "passed",
        evidence: "e", artifacts: [{ id: "report", path: ".opencode/artifacts/t1/n1/r1/report.md", revision: 1 }] },
      { id: "n2", type: "implement", agent: "impl-openai", goal: "do", depends_on: ["n1"], scope: ["src/"], done_when: "tests",
        status: "passed", evidence: "e", inputs: [{ from: "n1", artifact: "report", revision: 1 }] },
    ],
    events: [],
  }, null, 1));
  const listing = (root) => fs.readdirSync(root, { recursive: true }).sort().map((name) => {
    const full = path.join(root, name);
    return [name, fs.statSync(full).isFile() ? fs.readFileSync(full, "utf8") : "dir"];
  });
  const pristine = listing(project);

  const definition = resolveDefault(mainLoad.module.exports);
  const m = mainContext({ home: orch });
  await definition.main(m.ctx);
  const impl = m.provided[0].impl;
  const caller = { window: 1, signal: new AbortController().signal };
  const call = async (method, input) => JSON.parse(await impl[method](input, caller));
  const outputOf = (document) => document.graph.nodes.find((node) => node.id === "n1").artifacts[0];
  const request = { op: "write", directory: project, task: "t1", node: "n1", artifact: "report", record: "", text: "line one\nline two\n" };
  const realHome = process.env.HOME;
  const spawned = spawns.length;
  process.env.HOME = home;
  try {
    const shown = outputOf(await call("snapshot", { directory: project, session: "" }));
    assert.deepEqual([shown.editable, shown.size, shown.why], [true, 9, ""], JSON.stringify(shown));
    assert.match(shown.rev || "", /^[0-9a-f]{16}$/);
    const opened = await call("output", { ...request, op: "read", text: "" });
    assert.deepEqual([opened.text, opened.rev], ["line one\n", shown.rev], JSON.stringify(opened));
    const stale = await call("output", { ...request, expect: "0123456789abcdef" });
    assert.match(stale.error || "", /reload and try again/, JSON.stringify(stale));
    assert.equal(stale.rev, shown.rev);
    assert.deepEqual(listing(project), pristine, "a stale write changed nothing");
    const saved = await call("output", { ...request, expect: shown.rev });
    assert.equal(saved.error, undefined, String(saved.error));
    assert.deepEqual(saved.stale.map((item) => item.id), ["n2"]);
    assert.ok(saved.record.startsWith(path.join(home, ".config", "opencode", "backups") + path.sep), saved.record);
    const after = await call("snapshot", { directory: project, session: "" });
    assert.equal(outputOf(after).rev, saved.rev);
    assert.deepEqual(after.graph.nodes.find((node) => node.id === "n2").inputs.map((input) => input.stale), [true]);
    const undone = await call("output", { op: "restore", directory: project, task: "", node: "", artifact: "", record: saved.record, text: "" });
    assert.equal(undone.restored, true, JSON.stringify(undone));
    assert.deepEqual(listing(project), pristine, "undo put the project back byte for byte");
  } finally {
    process.env.HOME = realHome;
  }
  for (const entry of spawns.slice(spawned)) {
    assert.equal(entry.cwd, entry.args.includes("agentgraph.snapshot_json") ? project : orch, entry.args.join(" "));
    assert.equal(entry.args.some((arg) => /^--(dir|home|backups)\b/.test(arg)), false, "the plugin passes no folder: " + entry.args.join(" "));
  }
});

// Review round 7, minor 1 (security): an undo record is taken back only in the session's own project. The panel of a
// session in project A is handed the record of an edit made in project B (the backups folder is shared): the real
// main and real Python refuse it, and B is left as it was. From B's own session the same record is taken back.
await check("real Python on scratch folders: the panel of one project cannot take back an edit of another project", async () => {
  const scratch = path.join(fs.realpathSync(TMP), "desktop-outputs-reach");
  const orch = path.join(scratch, "orchestration");
  const home = path.join(scratch, "home");
  fs.cpSync(path.join(ORCHESTRATION, "agentgraph"), path.join(orch, "agentgraph"), {
    recursive: true,
    filter: (source) => !/(^|\/)(__pycache__|web)$/.test(source),
  });
  fs.cpSync(path.join(ORCHESTRATION, "structure"), path.join(orch, "structure"), { recursive: true, filter: withoutOwnDrawings });
  fs.copyFileSync(path.join(ORCHESTRATION, "SKILLS.md"), path.join(orch, "SKILLS.md"));
  fs.mkdirSync(path.join(home, ".config", "opencode"), { recursive: true });
  const makeProject = (name) => {
    const project = path.join(home, name);
    fs.mkdirSync(path.join(project, ".opencode", "graph"), { recursive: true });
    fs.mkdirSync(path.join(project, ".opencode", "artifacts", "t1", "n1", "r1"), { recursive: true });
    fs.writeFileSync(path.join(project, ".opencode", "artifacts", "t1", "n1", "r1", "report.md"), "line one\n");
    fs.writeFileSync(path.join(project, ".opencode", "graph", "t1.json"), JSON.stringify({
      task: "t1", goal: "g", status: "running", revision: 1, created: 1, updated: 2,
      objectives: [{ id: "o1", text: "x", nodes: ["n1"] }],
      nodes: [{ id: "n1", type: "research", agent: "explorer", goal: "find", depends_on: [], scope: [], done_when: "list",
        status: "passed", evidence: "e", artifacts: [{ id: "report", path: ".opencode/artifacts/t1/n1/r1/report.md", revision: 1 }] }],
      events: [],
    }, null, 1));
    return project;
  };
  const projectA = makeProject("a");
  const projectB = makeProject("b");
  const listing = (root) => fs.readdirSync(root, { recursive: true }).sort().map((name) => {
    const full = path.join(root, name);
    return [name, fs.statSync(full).isFile() ? fs.readFileSync(full, "utf8") : "dir"];
  });

  const m = mainContext({ home: orch });
  await resolveDefault(mainLoad.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const definition = windowLoad.gui.defined[0];
  let session = { key: "srv\nses_a", id: "ses_a", directory: projectA };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  w.ctx.main = (token) => Object.fromEntries(Object.keys(token.spec.methods).map((method) =>
    [method, (input) => hostCall(token, method, impl, input)]));
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { output } = mountCalls[mountCalls.length - 1].options.actions;
  const realHome = process.env.HOME;
  process.env.HOME = home;
  try {
    session = { key: "srv\nses_b", id: "ses_b", directory: projectB };
    const saved = await output({ op: "write", task: "t1", node: "n1", artifact: "report", text: "theirs\n" });
    assert.equal(typeof saved.record, "string", JSON.stringify(saved));
    const edited = listing(projectB);
    const untouched = listing(projectA);
    session = { key: "srv\nses_a", id: "ses_a", directory: projectA };
    let refused;
    try {
      await output({ op: "restore", record: saved.record });
    } catch (error) {
      refused = error;
    }
    assert.ok(refused, "the panel of project A took back an edit of project B");
    assert.match(refused.message, /not in this project/);
    assert.deepEqual([listing(projectB), listing(projectA)], [edited, untouched], "nothing was written");
    session = { key: "srv\nses_b", id: "ses_b", directory: projectB };
    const undone = await output({ op: "restore", record: saved.record });
    assert.equal(undone.restored, true, JSON.stringify(undone));
  } finally {
    process.env.HOME = realHome;
    rendered.dispose();
  }
});

// Review round 7, minor 8: a text holding NUL is refused by main and by the back end, in the same words.
await check("output: a text holding NUL is refused by main in the back end's own words", async () => {
  const backEnd = childProcess.execFileSync("python3", ["-P", "-c",
    "from agentgraph import outputs\ntry:\n    outputs._clean_text('a\\x00b')\nexcept outputs.OutputError as exc:\n    print(exc)"],
  { cwd: ORCHESTRATION, env: { ...process.env, PYTHONPATH: ORCHESTRATION }, encoding: "utf8" }).trim();
  assert.match(backEnd, /U\+0000/);
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python: path.join(TMP, "never-run-python") });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const answer = JSON.parse(await m.provided[0].impl.output({ op: "write", directory: ORCHESTRATION, task: "t1", node: "n1",
    artifact: "report", record: "", text: "a\u0000b" }, { window: 1, signal: new AbortController().signal }));
  assert.equal(answer.error, backEnd);
  assert.equal(log.length, 0, "the text reached the command line");
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

// ---- MCP servers (n126): read only; names and states are all that leaves the settings ----
await check("panel render: the host's MCP status (client.mcp.status) puts its state on the servers the snapshot lists and nothing else; the reason is one line cut at 200; no call, a failing call, a slow call and an answer in another shape leave the settings' state; nothing is written", async () => {
  const definition = windowLoad.gui.defined[0];
  const row = (name, extra = {}) => ({ name, source: "opencode", kind: "remote", enabled: true, state: "on", reason: "", live: false, agents: [], ...extra });
  const snapshot = () => JSON.stringify({ version: 1, agents: [{ name: "impl-claude", mcps: ["a", "b"] }], mcp: [row("a"), row("b"), row("c"), row("d", { enabled: false, state: "off" }), row("e"), row("f"), row("bad name")] });
  const long = "<img src=x onerror=alert(1)>\n<b>two</b>\r\n" + "x".repeat(300);
  const answer = {
    a: { status: "connected", url: "https://SECRET.example/key", headers: { Authorization: "SECRET" } },
    b: { status: "failed", error: long },
    c: { status: "needs_auth", error: "ignored for sign-in" },
    d: { status: "disabled", error: "not shown" },
    e: { status: "needs_client_registration", error: "register <it>" },
    f: { status: "nonsense", error: "x" },
    unlisted: { status: "connected" },
    "bad name": { status: "connected" },
  };
  const calls = [], writes = [];
  const make = (client) => {
    const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION, server: { client } };
    const w = windowContext(definition, { session });
    w.ctx.main = () => ({ snapshot: async () => snapshot(), flow: async (input) => { writes.push(input); return "{}"; }, skills: async (input) => { writes.push(input); return "{}"; } });
    definition.setup(w.ctx);
    const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render({ get session() { return session; }, screen: {} }));
    const ask = mountCalls[mountCalls.length - 1].options.fetchSnapshot;
    return { fetch: async () => JSON.parse(JSON.stringify(await ask())), rendered };
  };
  const rowsOf = (doc) => Object.fromEntries(doc.mcp.map((item) => [item.name, item]));
  const asked = [];
  const wrapped = make({ session: {}, mcp: { status(input) { asked.push({ self: this, input }); return Promise.resolve({ data: answer }); } } });
  const doc = await wrapped.fetch();
  const rows = rowsOf(doc);
  assert.equal(asked.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(asked[0].input)), { directory: ORCHESTRATION });
  assert.deepEqual([rows.a.state, rows.b.state, rows.c.state, rows.d.state, rows.e.state], ["connected", "failed", "needs sign-in", "off", "needs sign-in"]);
  assert.ok([rows.a, rows.b, rows.c, rows.d, rows.e].every((item) => item.live === true));
  assert.equal(rows.b.reason.length, 200, "the reason is cut at 200 characters");
  assert.ok(rows.b.reason.startsWith("<img src=x onerror=alert(1)> <b>two</b> xxx") && !/[\r\n]/.test(rows.b.reason), "one line, as characters");
  assert.equal(rows.a.reason, "");
  assert.equal(rows.c.reason, "ignored for sign-in");
  assert.equal(rows.d.reason, "", "a reason for a state that has none is dropped");
  assert.equal(rows.e.reason, "register <it>");
  assert.deepEqual([rows.f.state, rows.f.live], ["on", false], "a state the view does not know leaves the settings' state");
  assert.equal(rows["bad name"].state, "on", "a name that is not a plain name takes nothing");
  assert.ok(!("unlisted" in rows), "a server the settings do not list is not added");
  for (const item of doc.mcp) assert.deepEqual(Object.keys(item).sort(), ["agents", "enabled", "kind", "live", "name", "reason", "source", "state"], item.name + " gained a field");
  assert.ok(!JSON.stringify(doc).includes("SECRET"), "a field of the host's answer reached the document");
  // The answer itself, without the wrapper.
  const bare = make({ mcp: { status: () => answer } });
  assert.equal(rowsOf(await bare.fetch()).a.state, "connected");
  // Settings only: no client, no call, a throwing call, a rejecting one, an error answer, a slow one, a wrong shape.
  const attempts = [undefined, {}, { mcp: {} }, { mcp: { status: "no" } }, { mcp: { status() { throw new Error("SECRET-THROW"); } } },
    { mcp: { status: () => Promise.reject(new Error("SECRET-REJECT")) } }, { mcp: { status: () => ({ error: { message: "SECRET" } }) } },
    { mcp: { status: () => ({ data: "text" }) } }, { mcp: { status: () => [answer] } }, { mcp: { status: () => new Promise(() => {}) } }];
  for (const client of attempts) {
    const view = make(client);
    const got = await view.fetch();
    assert.equal(got.error, undefined, "a host without the call broke the snapshot: " + JSON.stringify(client));
    assert.deepEqual(got.mcp.map((item) => [item.state, item.live]), [["on", false], ["on", false], ["on", false], ["off", false], ["on", false], ["on", false], ["on", false]]);
    assert.ok(!JSON.stringify(got).includes("SECRET"));
    view.rendered.dispose();
  }
  assert.deepEqual(writes, [], "the MCP view wrote something");
  wrapped.rendered.dispose();
  bare.rendered.dispose();
});

await check("the planted-secret test through the bridge: a scratch HOME whose settings hold a marker in every field of every MCP entry; the marker is in no snapshot reply, command line, standard input, log or error of main, and a server whose name is not a plain name is not shown", async () => {
  const MARKER = "PLANTED-SECRET-5d2c81";
  const home = fs.mkdtempSync(path.join(TMP, "planted-home-"));
  const configDir = path.join(home, ".config", "opencode");
  fs.mkdirSync(configDir, { recursive: true });
  const local = { type: "local", command: ["node", MARKER, "--token=" + MARKER], environment: { [MARKER]: MARKER }, enabled: true, cwd: "/" + MARKER };
  const remote = { type: "remote", url: "https://" + MARKER + ".example/mcp?key=" + MARKER, headers: { Authorization: "Bearer " + MARKER }, oauth: { clientId: MARKER, clientSecret: MARKER } };
  fs.writeFileSync(path.join(configDir, "opencode.json"), JSON.stringify({ mcp: { "plain-local": local, "plain.remote": remote, ["has space " + MARKER]: local, ["slash/" + MARKER]: remote, [MARKER + "\n"]: local } }));
  fs.writeFileSync(path.join(configDir, "oh-my-opencode-slim.json"), JSON.stringify({ preset: "p", disabled_mcps: [MARKER], mcp: { x: local },
    presets: { p: { orchestrator: { model: "a/b", prompt: MARKER, mcps: ["*", "!context7", "!" + MARKER] }, explorer: { model: "a/b", mcps: [MARKER, "plain-local", "has space " + MARKER], options: { headers: { X: MARKER } } } } } }));
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const previous = process.env.HOME;
  process.env.HOME = home;
  let text;
  try {
    text = await m.provided[0].impl.snapshot({ directory: ORCHESTRATION, session: "" }, { window: 1, signal: new AbortController().signal });
  } finally {
    process.env.HOME = previous;
  }
  const doc = JSON.parse(text);
  assert.equal(doc.error, undefined, "the snapshot failed: " + text.slice(0, 300));
  assert.deepEqual(doc.mcp.map((item) => item.name), ["context7", "gh_grep", "plain-local", "plain.remote"]);
  assert.deepEqual(doc.agents.find((item) => item.name === "explorer").mcps, ["plain-local"]);
  const everything = text + JSON.stringify(log) + JSON.stringify(m.logs);
  assert.ok(!everything.includes(MARKER) && !everything.includes("PLANTED"), "the marker got out");
  assert.ok(!everything.includes("has space"), "a name that is not plain was shown");
});

// ---------------------------------------------------------------------------------------------------------------
// n14: the chat of every agent: read the feed (`agentgraph chat`), steer a working agent, stop it.

const FEED = { session: "ses_a", agent: "impl-openai", busy: true, items: [{ kind: "text", time: 1, text: "<b>hi</b>" }, { kind: "tool", time: 2, name: "read", status: "running" }], truncated: false, more: false, warning: null };

function chatPython(name, script) {
  const python = path.join(TMP, name);
  fs.writeFileSync(python, `#!/bin/sh\n${script}\n`, { mode: 0o700 });
  return python;
}

// A window whose Ipc goes through the host's schema decoding to the real main (the python is `python`).
function chatWindow({ python, demo, client, sessionOverrides, snapshot }) {
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python, demo });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const definition = windowLoad.gui.defined[0];
  let session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION, server: client === undefined ? {} : { client }, ...sessionOverrides };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session, options: { demo } });
  const asked = [];
  w.ctx.main = (token) => Object.fromEntries(Object.keys(token.spec.methods).map((method) => [method, async (input) => {
    if (method === "snapshot" && snapshot) return JSON.stringify(typeof snapshot === "function" ? snapshot(input) : snapshot);
    asked.push({ method, input });
    return hostCall(token, method, impl, input);
  }]));
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const options = mountCalls[mountCalls.length - 1].options;
  return { log, logs: m.logs, options, actions: options.actions, asked, rendered, setSession: (next) => (session = next) };
}

const CHAT_SNAPSHOT = { version: 1, root: { id: "ses_a" }, agents: [
  { name: "orchestrator", sessions: [{ id: "ses_a", busy: true }] },
  { name: "impl-openai", sessions: [{ id: "ses_child", busy: true }, { id: "ses_old", busy: false }] },
] };

await check("chat: main runs `python3 -P -m agentgraph.chatfeed --session=ID --json --limit=N` in home and the feed crosses as it is; the window's action adds nothing", async () => {
  const python = chatPython("fake-chat-python", `printf '%s\\n' '${JSON.stringify(FEED)}'`);
  const t = chatWindow({ python, snapshot: CHAT_SNAPSHOT });
  try {
    assert.equal(typeof t.actions.chat, "function");
    await t.options.fetchSnapshot(); // the chat is read for a session of the last snapshot
    const feed = await t.actions.chat("ses_child", { limit: 25 });
    assert.deepEqual(JSON.parse(JSON.stringify(feed)), FEED);
    assert.equal(t.log.length, 1);
    assert.equal(t.log[0].file, python);
    assert.deepEqual(t.log[0].args, ["-P", "-m", "agentgraph.chatfeed", "--session=ses_child", "--json", "--limit=25"]);
    assert.equal(t.log[0].cwd, ORCHESTRATION);
    assert.equal(t.log[0].timeout, 20000, "the snapshot's time limit");
    assert.equal(t.log[0].stdin, "");
    // No options: the default of 40; the limit's two ends are accepted.
    await t.actions.chat("ses_child");
    await t.actions.chat("ses_child", {});
    await t.actions.chat("ses_child", { limit: 1 });
    await t.actions.chat("ses_child", { limit: 200 });
    assert.deepEqual(t.log.slice(1).map((entry) => entry.args.slice(-2)), [["--json", "--limit=40"], ["--json", "--limit=40"], ["--json", "--limit=1"], ["--json", "--limit=200"]]);
    for (const entry of t.log) assert.equal(entry.args.includes("--demo"), false);
    assert.deepEqual(Object.keys(t.options.actions).filter((name) => ["chat", "send", "stop"].includes(name)), ["chat"], "no client: no send, no stop");
  } finally {
    t.rendered.dispose();
  }
});

await check("chat: a bad session id or limit never reaches Python, on either side of the bridge", async () => {
  const python = chatPython("never-chat-python", `printf '%s\\n' '${JSON.stringify(FEED)}'`);
  const t = chatWindow({ python, snapshot: CHAT_SNAPSHOT });
  try {
    await t.options.fetchSnapshot();
    const asked0 = t.asked.length;
    const rejected = async (promise) => {
      try {
        await promise;
      } catch (error) {
        return error.message;
      }
      return "";
    };
    for (const id of ["", "--demo", "-x", "ses a", "ses_a\n", "../ses_a", "ses_a=1", 5, null, undefined, "s".repeat(129)])
      assert.equal(await rejected(t.actions.chat(id, { limit: 10 })), "Bad chat request.", JSON.stringify(id));
    for (const limit of [0, 201, -1, 1.5, "40", NaN, Infinity, [], {}, true])
      assert.equal(await rejected(t.actions.chat("ses_a", { limit })), "Bad chat request.", JSON.stringify(limit));
    assert.equal(t.asked.length, asked0, "the window sent a bad request over the bridge");
    assert.equal(t.log.length, 0);
  } finally {
    t.rendered.dispose();
  }
  const m = mainContext({ home: ORCHESTRATION, python });
  const log = [];
  resolveDefault(loadMain(CODE, log).module.exports).main(m.ctx);
  const chat = m.provided[0].impl.chat;
  for (const input of [null, "ses_a", {}, { session: "--demo", limit: 10 }, { session: "", limit: 10 }, { session: "ses a", limit: 10 }, { session: "ses_a\n", limit: 10 },
    { session: "ses_a", limit: 0 }, { session: "ses_a", limit: 201 }, { session: "ses_a", limit: 2.5 }, { session: "ses_a", limit: "10" }, { session: "ses_a", limit: NaN }, { session: "ses_a" }]) {
    const answer = JSON.parse(await chat(input));
    assert.deepEqual(Object.keys(answer), ["error"], JSON.stringify(input));
    assert.equal(typeof answer.error, "string");
    assert.doesNotMatch(answer.error, /[\r\n]/);
  }
  assert.equal(log.length, 0, "main ran Python for a bad request");
});

await check("chat: an error document or a failing command rejects with one short line; a feed with a warning is a feed", async () => {
  const rejection = async (python) => {
    const t = chatWindow({ python, snapshot: CHAT_SNAPSHOT });
    try {
      await t.options.fetchSnapshot();
      await t.actions.chat("ses_a", { limit: 40 });
    } catch (error) {
      return error.message;
    } finally {
      t.rendered.dispose();
    }
    return "";
  };
  assert.equal(await rejection(chatPython("err-doc-python", `printf '%s\\n' '{"version":1,"error":"no such session\\nin the database"}'`)), "no such session in the database");
  assert.equal(await rejection(chatPython("err-exit-python", `printf '%s\\n' 'agentgraph chat: no such demo session' >&2\nexit 1`)), "The chat command failed: agentgraph chat: no such demo session");
  assert.match(await rejection(chatPython("err-json-python", `printf '%s\\n' 'not json'`)), /^The chat command failed\.$/);
  assert.match(await rejection(chatPython("err-shape-python", `printf '%s\\n' '{"hello": 1}'`)), /^The chat command failed\.$/);
  assert.match(await rejection(path.join(TMP, "no-such-chat-python")), /Python was not found/);
  const long = await rejection(chatPython("err-long-python", `printf '%s\\n' '{"error":"${"x".repeat(2000)}"}'`));
  assert.ok(long.length <= 300, "the message is short");
  const warned = { ...FEED, items: [], warning: "the OpenCode database is locked" };
  const t = chatWindow({ python: chatPython("warn-python", `printf '%s\\n' '${JSON.stringify(warned)}'`), snapshot: CHAT_SNAPSHOT });
  try {
    await t.options.fetchSnapshot();
    assert.deepEqual(JSON.parse(JSON.stringify(await t.actions.chat("ses_a", { limit: 40 }))), warned);
  } finally {
    t.rendered.dispose();
  }
});

await check("chat: the demo is given the chat alone, which reads `agentgraph.chatfeed --demo` through the real command", async () => {
  const t = chatWindow({ python: "python3", demo: true });
  try {
    assert.deepEqual(Object.keys(t.options.actions), ["chat"]);
    const feed = await t.actions.chat("ses_root", { limit: 5 });
    assert.equal(feed.session, "ses_root");
    assert.ok(Array.isArray(feed.items) && feed.items.length > 0, "the demo session has an example chat");
    assert.equal(t.log.length, 1);
    assert.deepEqual(t.log[0].args, ["-P", "-m", "agentgraph.chatfeed", "--session=ses_root", "--json", "--limit=5", "--demo"]);
    let failure = "";
    try {
      await t.actions.chat("ses_unknown", { limit: 5 });
    } catch (error) {
      failure = error.message;
    }
    assert.match(failure, /no such demo session/);
  } finally {
    t.rendered.dispose();
  }
});

await check("chat: the real command through the real main reads a real (empty) database without a traceback", async () => {
  const t = chatWindow({ python: "python3", snapshot: { version: 1, root: { id: "ses_harnessnothing" }, agents: [] } });
  try {
    await t.options.fetchSnapshot();
    const feed = await t.actions.chat("ses_harnessnothing", { limit: 3 });
    assert.equal(feed.session, "ses_harnessnothing");
    assert.ok(Array.isArray(feed.items));
  } finally {
    t.rendered.dispose();
  }
});

await check("chat: only a session of the last snapshot, or the snapshot's side session, is read; anything else is refused without reaching Python", async () => {
  const python = chatPython("scope-chat-python", `printf '%s\\n' '${JSON.stringify(FEED)}'`);
  let current = { ...CHAT_SNAPSHOT, sidechat: { installed: true, session: "ses_side" } };
  const t = chatWindow({ python, snapshot: () => current });
  const refusal = async (id) => {
    try {
      await t.actions.chat(id, { limit: 10 });
    } catch (error) {
      return error.message;
    }
    return "";
  };
  try {
    // Before any snapshot was read nothing is known.
    assert.equal(await refusal("ses_a"), "That session is not in the agent graph.");
    assert.equal(t.log.length, 0);
    await t.options.fetchSnapshot();
    for (const id of ["ses_other", "ses_sid", "ses_side2", "ses_Side"]) assert.equal(await refusal(id), "That session is not in the agent graph.", id);
    assert.equal(t.log.length, 0, "a session outside the tree reached Python");
    assert.equal(t.asked.length, 0, "a session outside the tree crossed the bridge");
    // The tree is read plain; the side session is read with --side (no briefs), and only that one.
    await t.actions.chat("ses_child", { limit: 10 });
    assert.deepEqual(t.log[0].args, ["-P", "-m", "agentgraph.chatfeed", "--session=ses_child", "--json", "--limit=10"]);
    await t.actions.chat("ses_side", { limit: 10 });
    assert.deepEqual(t.log[1].args, ["-P", "-m", "agentgraph.chatfeed", "--session=ses_side", "--json", "--limit=10", "--side"]);
    // A snapshot that no longer names it, an error document, another panel session: refused again.
    current = CHAT_SNAPSHOT;
    await t.options.fetchSnapshot();
    assert.equal(await refusal("ses_side"), "That session is not in the agent graph.");
    current = { ...CHAT_SNAPSHOT, sidechat: { installed: true, session: "ses_child" } }; // a side id that is in the tree is read plain
    await t.options.fetchSnapshot();
    await t.actions.chat("ses_child", { limit: 10 });
    assert.equal(t.log[t.log.length - 1].args.includes("--side"), false);
    current = { ...CHAT_SNAPSHOT, sidechat: { installed: true, session: "ses_side" } };
    await t.options.fetchSnapshot();
    t.setSession({ key: "srv\nses_z", id: "ses_z", directory: ORCHESTRATION, server: {} });
    assert.equal(await refusal("ses_side"), "That session is not in the agent graph.");
    assert.equal(await refusal("ses_a"), "That session is not in the agent graph.");
    // main refuses a malformed `side` too.
    const m = mainContext({ home: ORCHESTRATION, python });
    const log = [];
    resolveDefault(loadMain(CODE, log).module.exports).main(m.ctx);
    for (const side of ["true", 1, null, {}]) assert.deepEqual(Object.keys(JSON.parse(await m.provided[0].impl.chat({ session: "ses_a", limit: 5, side }))), ["error"]);
    assert.equal(log.length, 0);
  } finally {
    t.rendered.dispose();
  }
});

const plain = (answer) => JSON.parse(JSON.stringify(answer));

function chatClient(extra = {}) {
  const calls = [];
  const session = {};
  const behaviour = { prompt: undefined, interrupt: undefined, ...extra };
  if (behaviour.prompt !== null) session.prompt = function (input) { calls.push({ name: "prompt", self: this, input: JSON.parse(JSON.stringify(input)) }); return behaviour.prompt ? behaviour.prompt(input) : Promise.resolve({ data: { type: "user" } }); };
  if (behaviour.interrupt !== null) session.interrupt = function (input) { calls.push({ name: "interrupt", self: this, input: JSON.parse(JSON.stringify(input)) }); return behaviour.interrupt ? behaviour.interrupt(input) : Promise.resolve({ data: true }); };
  return { client: { session }, calls };
}

await check("send and stop: steer true and false make the stated calls on the host's client; the reply is { ok: true }", async () => {
  const { client, calls } = chatClient();
  const t = chatWindow({ python: "python3", client, snapshot: CHAT_SNAPSHOT });
  try {
    assert.equal(typeof t.actions.send, "function");
    assert.equal(typeof t.actions.stop, "function");
    assert.equal((await t.options.fetchSnapshot()).error, undefined);
    assert.deepEqual(plain(await t.actions.send("ses_child", "look at the tests too", { steer: true })), { ok: true });
    assert.deepEqual(plain(await t.actions.send("ses_a", "go on", { steer: false })), { ok: true });
    assert.deepEqual(plain(await t.actions.send("ses_old", "hello", {})), { ok: true });
    assert.deepEqual(plain(await t.actions.send("ses_old", "hello again")), { ok: true });
    assert.deepEqual(plain(await t.actions.stop("ses_child")), { ok: true });
    assert.deepEqual(calls.map((call) => [call.name, call.input]), [
      ["prompt", { sessionID: "ses_child", text: "look at the tests too", delivery: "steer" }],
      ["prompt", { sessionID: "ses_a", text: "go on" }],
      ["prompt", { sessionID: "ses_old", text: "hello" }],
      ["prompt", { sessionID: "ses_old", text: "hello again" }],
      ["interrupt", { sessionID: "ses_child" }],
    ]);
    for (const call of calls) assert.equal(call.self, client.session, "called on the host's session API");
    assert.equal(Object.prototype.hasOwnProperty.call(calls[1].input, "delivery"), false, "a plain prompt has no delivery");
    // The longest message is 4,000 characters (not UTF-16 units).
    calls.length = 0;
    assert.deepEqual(plain(await t.actions.send("ses_a", "x".repeat(4000), { steer: false })), { ok: true });
    assert.deepEqual(plain(await t.actions.send("ses_a", "\u{1F600}".repeat(4000), { steer: false })), { ok: true });
    assert.equal(calls.length, 2);
  } finally {
    t.rendered.dispose();
  }
});

await check("send and stop: empty or over-long text, bad ids and a session not in the last snapshot are refused without calling the host", async () => {
  const { client, calls } = chatClient();
  let current = CHAT_SNAPSHOT;
  const t = chatWindow({ python: "python3", client, snapshot: () => current });
  try {
    const refused = (answer) => {
      assert.deepEqual(Object.keys(answer), ["ok", "error"]); // keys of the answer, whatever realm made it
      assert.equal(answer.ok, false);
      assert.equal(typeof answer.error, "string");
      assert.ok(answer.error.length > 0 && answer.error.length <= 300);
      assert.doesNotMatch(answer.error, /[\r\n]/);
    };
    // Before any snapshot was read nothing is known.
    refused(await t.actions.send("ses_a", "hello", { steer: true }));
    refused(await t.actions.stop("ses_a"));
    await t.options.fetchSnapshot();
    for (const text of ["", "   \n", undefined, null, 5, {}, "x".repeat(4001), "\u{1F600}".repeat(4001)]) refused(await t.actions.send("ses_a", text, { steer: true }));
    for (const id of ["", "--x", "ses a", "../ses_a", undefined, 7, "ses_a\n"]) {
      refused(await t.actions.send(id, "hello", { steer: true }));
      refused(await t.actions.stop(id));
    }
    refused(await t.actions.send("ses_other", "hello", { steer: true }));
    refused(await t.actions.send("ses_other", "hello", { steer: false }));
    refused(await t.actions.stop("ses_other"));
    assert.equal(calls.length, 0, "the host was called for a refused request");
    // The session set is the last snapshot's: an agent that is gone, or an error document, changes it as it should.
    current = { version: 1, root: { id: "ses_a" }, agents: [{ name: "orchestrator", sessions: [{ id: "ses_a" }] }] };
    await t.options.fetchSnapshot();
    refused(await t.actions.stop("ses_child"));
    assert.deepEqual(plain(await t.actions.stop("ses_a")), { ok: true });
    current = { version: 1, error: "the snapshot failed" };
    await t.options.fetchSnapshot();
    assert.deepEqual(plain(await t.actions.stop("ses_a")), { ok: true }, "an error document leaves the known sessions as they were");
    // Another session in the panel: what the last snapshot showed for the first is not the second's.
    t.setSession({ key: "srv\nses_z", id: "ses_z", directory: ORCHESTRATION, server: { client } });
    refused(await t.actions.stop("ses_a"));
    assert.equal(calls.length, 2);
  } finally {
    t.rendered.dispose();
  }
});

await check("send and stop: a host call that throws, rejects or answers { error } comes back as { ok: false, error } and never throws", async () => {
  for (const [what, prompt, interrupt] of [
    ["throws", () => { throw new Error("boom\nnow"); }, () => { throw new Error("boom\nnow"); }],
    ["rejects", () => Promise.reject(new Error("boom\nnow")), () => Promise.reject(new Error("boom\nnow"))],
    ["rejects a string", () => Promise.reject("boom\nnow"), () => Promise.reject("boom\nnow")],
    ["answers an error", () => Promise.resolve({ error: "boom\nnow" }), () => Promise.resolve({ error: "boom\nnow" })],
    ["answers an error object", () => Promise.resolve({ error: { message: "boom\nnow" } }), () => Promise.resolve({ error: { message: "boom\nnow" } })],
  ]) {
    const { client } = chatClient({ prompt, interrupt });
    const t = chatWindow({ python: "python3", client, snapshot: CHAT_SNAPSHOT });
    try {
      await t.options.fetchSnapshot();
      const sent = await t.actions.send("ses_child", "hello", { steer: true });
      const stopped = await t.actions.stop("ses_child");
      assert.deepEqual(plain(sent), { ok: false, error: "The message could not be sent: boom now" }, what);
      assert.deepEqual(plain(stopped), { ok: false, error: "The session could not be stopped: boom now" }, what);
    } finally {
      t.rendered.dispose();
    }
  }
  // A long host error is one short line.
  const { client } = chatClient({ prompt: () => Promise.reject(new Error("y".repeat(2000))) });
  const t = chatWindow({ python: "python3", client, snapshot: CHAT_SNAPSHOT });
  try {
    await t.options.fetchSnapshot();
    const answer = await t.actions.send("ses_child", "hello", { steer: true });
    assert.equal(answer.ok, false);
    assert.ok(answer.error.length <= 300 + 40);
  } finally {
    t.rendered.dispose();
  }
});

await check("send and stop: a client without session.prompt gives no send, one without session.interrupt gives no stop, none gives neither", async () => {
  const keys = (client) => {
    const t = chatWindow({ python: "python3", client });
    try {
      return Object.keys(t.options.actions).filter((name) => ["chat", "send", "stop"].includes(name));
    } finally {
      t.rendered.dispose();
    }
  };
  assert.deepEqual(keys(chatClient().client), ["chat", "send", "stop"]);
  assert.deepEqual(keys(chatClient({ prompt: null }).client), ["chat", "stop"]);
  assert.deepEqual(keys(chatClient({ interrupt: null }).client), ["chat", "send"]);
  assert.deepEqual(keys(chatClient({ prompt: null, interrupt: null }).client), ["chat"]);
  assert.deepEqual(keys({ session: { prompt: "not a function", interrupt: 3 } }), ["chat"]);
  assert.deepEqual(keys({}), ["chat"]);
  assert.deepEqual(keys(undefined), ["chat"]);
  // The demo never gets them, whatever the host has.
  const t = chatWindow({ python: "python3", demo: true, client: chatClient().client });
  try {
    assert.deepEqual(Object.keys(t.options.actions), ["chat"]);
  } finally {
    t.rendered.dispose();
  }
});

await check("send, stop and chat: no message text reaches a log, the console or an error line", async () => {
  const MARK = "PLANTED-MESSAGE-TEXT-7f3a";
  const seen = [];
  const original = {};
  for (const name of ["log", "info", "warn", "error", "debug"]) {
    original[name] = console[name];
    console[name] = (...args) => seen.push(args.map(String).join(" "));
  }
  const { client, calls } = chatClient({ prompt: () => Promise.reject(new Error("the host said no")) });
  const t = chatWindow({ python: chatPython("log-python", `printf '%s\\n' '${JSON.stringify(FEED)}'`), client, snapshot: CHAT_SNAPSHOT });
  try {
    await t.options.fetchSnapshot();
    const failed = await t.actions.send("ses_child", MARK, { steer: true });
    await t.actions.chat("ses_child", { limit: 5 });
    const tooLong = await t.actions.send("ses_child", MARK + "x".repeat(4001), { steer: true });
    const unknown = await t.actions.send("ses_other", MARK, { steer: false });
    for (const answer of [failed, tooLong, unknown]) assert.equal(JSON.stringify(answer).includes(MARK), false, "the text came back in an error line");
    assert.equal(calls.length, 1);
  } finally {
    for (const [name, fn] of Object.entries(original)) console[name] = fn;
    t.rendered.dispose();
  }
  const everything = JSON.stringify([seen, t.logs, t.log]);
  assert.equal(everything.includes(MARK), false, "message text was logged or put on a command line");
  assert.equal(JSON.stringify(t.asked).includes(MARK), false, "message text crossed the bridge");
});

// ---------------------------------------------------------------------------------------------------------------
// n8: the side chat: ask it one question through the bridge (`agentgraph sidechat ask`), the question on stdin.

const SIDE_ANSWER = { answer: "It is fine <b>really</b>", session: "ses_side", model: "openai/gpt-6-luna", fallback: false, spent: 0.004 };
const sideSnapshot = (root = "ses_a") => ({ version: 1, root: { id: root }, agents: [{ name: "orchestrator", sessions: [{ id: root, busy: false }] }] });
const plainReply = (answer) => JSON.parse(JSON.stringify(answer));

await check("side chat: main runs `python3 -P -m agentgraph.sidechat ask --main ROOT --text-file - --json` in home with a 150 s limit and the question on stdin; the reply is { ok: true, answer }", async () => {
  const python = chatPython("fake-side-python", `cat > /dev/null; printf '%s\\n' '${JSON.stringify(SIDE_ANSWER)}'`);
  const t = chatWindow({ python, client: chatClient().client, snapshot: sideSnapshot("ses_root1") });
  try {
    assert.equal(typeof t.actions.side, "function", "the window gives the page a side action");
    await t.options.fetchSnapshot();
    const question = "What is it doing? $(touch x) `id` \"quoted\"\nsecond line <b>x</b>";
    const reply = await t.actions.side(question);
    assert.deepEqual(plainReply(reply), { ok: true, answer: SIDE_ANSWER.answer });
    assert.deepEqual(Object.keys(reply), ["ok", "answer"]);
    assert.equal(t.log.length, 1);
    assert.equal(t.log[0].file, python);
    assert.deepEqual(t.log[0].args, ["-P", "-m", "agentgraph.sidechat", "ask", "--main", "ses_root1", "--text-file", "-", "--json"]);
    assert.equal(t.log[0].cwd, ORCHESTRATION);
    assert.equal(t.log[0].timeout, 150000, "the side chat's time limit");
    assert.equal(t.log[0].stdin, question, "the question went on stdin as it was");
    assert.equal(t.log[0].args.some((arg) => arg.includes("What is it") || arg.includes("second line")), false, "the question is in the arguments");
    assert.deepEqual(t.asked.map((item) => item.method), ["side"], "one bridge call per question");
    assert.deepEqual(Object.keys(t.asked[0].input), ["main", "text"]);
    // The 4,000 characters (counted as characters) are the most it takes.
    assert.equal((await t.actions.side("x".repeat(4000))).ok, true);
    assert.equal((await t.actions.side("\u{1F600}".repeat(4000))).ok, true);
    assert.equal(t.log.length, 3);
  } finally {
    t.rendered.dispose();
  }
});

await check("side chat: an error document, a failing command, a missing answer, no JSON and a missing Python are each one short line { ok: false, error }", async () => {
  const cases = [
    ["error line", `cat > /dev/null; printf '%s\\n' '{"error": "the side chat is not installed.\\nRun agentgraph sidechat install"}'; exit 1`, /^the side chat is not installed\. Run agentgraph sidechat install$/],
    ["error line, exit 0", `cat > /dev/null; printf '%s\\n' '{"error": "no model"}'`, /^no model$/],
    ["no answer field", `cat > /dev/null; printf '%s\\n' '{"session": "ses_side"}'`, /^The side chat failed/],
    ["no JSON, stderr", `cat > /dev/null; echo "boom now" >&2; exit 3`, /^The side chat failed: boom now$/],
    ["not JSON", `cat > /dev/null; echo "plain words"`, /^The side chat failed\.$/],
    ["answer not a string", `cat > /dev/null; printf '%s\\n' '{"answer": 5}'`, /^The side chat failed/],
    ["old python", `cat > /dev/null; echo "Unknown option: -P" >&2; exit 2`, /Python 3\.11 or newer/],
  ];
  for (const [name, script, expected] of cases) {
    const t = chatWindow({ python: chatPython("side-case-python", script), client: chatClient().client, snapshot: sideSnapshot() });
    try {
      await t.options.fetchSnapshot();
      const reply = await t.actions.side("Why?");
      assert.deepEqual(Object.keys(reply), ["ok", "error"], name);
      assert.equal(reply.ok, false, name);
      assert.match(reply.error, expected, name);
      assert.ok(reply.error.length > 0 && reply.error.length <= 300 && !/[\r\n]/.test(reply.error), name + ": one short line");
    } finally {
      t.rendered.dispose();
    }
  }
  const t = chatWindow({ python: path.join(TMP, "no-such-python"), client: chatClient().client, snapshot: sideSnapshot() });
  try {
    await t.options.fetchSnapshot();
    const reply = await t.actions.side("Why?");
    assert.equal(reply.ok, false);
    assert.match(reply.error, /Python was not found/);
  } finally {
    t.rendered.dispose();
  }
});

await check("side chat: an empty, over-long or NUL question, no snapshot yet and another session's root never reach execFile; the main method refuses the same", async () => {
  const python = chatPython("never-side-python", `cat > /dev/null; printf '%s\\n' '${JSON.stringify(SIDE_ANSWER)}'`);
  const t = chatWindow({ python, client: chatClient().client, snapshot: sideSnapshot() });
  try {
    const refused = (answer) => {
      assert.deepEqual(Object.keys(answer), ["ok", "error"]);
      assert.equal(answer.ok, false);
      assert.ok(answer.error.length > 0 && answer.error.length <= 300 && !/[\r\n]/.test(answer.error));
    };
    refused(await t.actions.side("Before any snapshot"));
    await t.options.fetchSnapshot();
    for (const text of ["", "  \n ", undefined, null, 7, {}, ["x"], "x".repeat(4001), "\u{1F600}".repeat(4001), "has\0nul"]) refused(await t.actions.side(text));
    t.setSession({ key: "srv\nses_z", id: "ses_z", directory: ORCHESTRATION, server: {} });
    refused(await t.actions.side("Another session"));
    assert.equal(t.log.length, 0, "execFile ran for a refused question");
    // Main checks again whatever the window sent it.
    const loaded = loadMain(CODE, []);
    const m = mainContext({ home: ORCHESTRATION, python });
    resolveDefault(loaded.module.exports).main(m.ctx);
    const impl = m.provided[0].impl;
    const direct = [{ main: "", text: "q" }, { main: "--x", text: "q" }, { main: "ses a", text: "q" }, { main: "ses_a\n", text: "q" }, { main: "ses_a", text: "" }, { main: "ses_a", text: " \n" }, { main: "ses_a", text: "x".repeat(4001) }, { main: "ses_a", text: "a\0b" }, { main: 5, text: "q" }, { main: "ses_a", text: 5 }, null, "ses_a", []];
    for (const input of direct) {
      const doc = JSON.parse(await impl.side(input));
      assert.equal(typeof doc.error, "string", JSON.stringify(input));
      assert.ok(doc.error.length > 0 && !/[\r\n]/.test(doc.error));
    }
    assert.equal(JSON.parse(await impl.side({ main: "ses_a", text: "fine" })).answer, SIDE_ANSWER.answer);
  } finally {
    t.rendered.dispose();
  }
});

await check("side chat: the demo gives the page no side action and spawns nothing", async () => {
  const demo = chatWindow({ python: chatPython("demo-side-python", `echo '{"answer":"x"}'`), demo: true, client: chatClient().client, snapshot: sideSnapshot() });
  try {
    assert.equal("side" in demo.actions, false, "the demo has a side action");
    assert.deepEqual(Object.keys(demo.actions), ["chat"]);
    assert.equal(demo.log.length, 0);
  } finally {
    demo.rendered.dispose();
  }
});

await check("side chat: the question and the answer are in no log, command line or bridge record of main or the window", async () => {
  const MARK = "SIDE-MARK-9f3a41";
  const seen = [];
  const original = {};
  for (const name of ["log", "info", "warn", "error", "debug"]) {
    original[name] = console[name];
    console[name] = (...args) => seen.push(args.map(String).join(" "));
  }
  const python = chatPython("mark-side-python", `cat > /dev/null; printf '%s\\n' '{"answer":"${MARK} answer"}'`);
  const t = chatWindow({ python, client: chatClient().client, snapshot: sideSnapshot() });
  try {
    await t.options.fetchSnapshot();
    assert.equal((await t.actions.side(MARK + " question")).ok, true);
    await t.actions.side(MARK + "x".repeat(4001));
  } finally {
    for (const [name, fn] of Object.entries(original)) console[name] = fn;
    t.rendered.dispose();
  }
  assert.equal(JSON.stringify([seen, t.logs, t.log.map(({ stdin, ...rest }) => rest)]).includes(MARK), false, "the question or answer was logged or put on a command line");
  assert.equal(t.log[0].stdin, MARK + " question");
});

await check("the bundle carries the side chat: the main command, the window's action and the page's side chat code", () => {
  assert.match(CODE, /agentgraph\.sidechat/);
  assert.match(CODE, /const sideAction = action\('side'\)/);
  assert.match(CODE, /ag-sidemoon/);
});

await check("the bundle carries the chat: the main command, the window's actions and the page's chat code; the live bundle is the build of the current sources", () => {
  assert.match(CODE, /agentgraph\.chatfeed/);
  assert.match(CODE, /const chatAction = action\('chat'\), sendAction = action\('send'\), stopAction = action\('stop'\)/);
  assert.match(CODE, /actions\.chat\(sessionID, \{ limit \}\)/);
  // The header's "Sources:" line holds paths relative to the output file, so it differs for a temporary output.
  const body = (text) => text.replace(/^\/\/ Sources: .*\n/m, "");
  assert.equal(body(fs.readFileSync(LIVE_BUNDLE, "utf8")), body(CODE), "gui-experimental.cjs is not the build of src/plugin.cjs, app.js and style.css: run node desktop-plugin/build.mjs");
});

await check("n20 bundle: the page code in the panel tells a quiet or orphan step from a live one (plain words, no animation), as the web page does", () => {
  assert.match(CODE, /Reported running · no session/);
  assert.match(CODE, /Reported running · ' \+ idleFor\(/);
  assert.match(CODE, /Working · not marked running/);
  assert.match(CODE, /data-status=\\"running\\"\]:is\(\[data-liveness=\\"quiet\\"\], \[data-liveness=\\"orphan\\"\]\) \.ag-status/);
  assert.doesNotMatch(CODE, /data-liveness=\\"(?:quiet|orphan)\\"[^}]*animation/, "a quiet or orphan step animates");
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

// ---- n10: the default preset, the saved setups and the spend limit ----
await check("n10 main: setup and spend run `python3 -P -m agentgraph.setups|spend ... --dir SESSION_DIR --json` in home without a shell; the view's rev goes before --json; a stale answer keeps its rev; bad input and the demo spawn nothing", async () => {
  const python = path.join(TMP, "fake-setup-python");
  fs.writeFileSync(python, `#!/bin/sh
cat > /dev/null
module="$3"
shift 3
case "$*" in
  *"--expect ${STALE_REV}"*) printf '{"error": "setup x changed since you saved it; reload and try again", "rev": "${CURRENT_REV}"}\\n'; exit 1;;
esac
case "$module" in
  agentgraph.setups) printf 'noise\\n{"message": "done", "record": "/tmp/backups/undo.json", "args": "%s"}\\n' "$*";;
  agentgraph.spend) printf '{"limit": 50.0, "warn": 45.0, "previous": {"limit": null, "warn": null}, "args": "%s"}\\n' "$*";;
esac
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const dir = ORCHESTRATION, rev = "0123456789abcdef", record = "/tmp/backups/undo.json";
  const blank = { op: "", directory: dir, name: "", record: "", replace: "" };
  const setup = async (fields) => JSON.parse(await impl.setup({ ...blank, ...fields }));
  const tail = `--dir ${dir} --json`;
  const argsOf = (doc) => doc.args;
  assert.equal(argsOf(await setup({ op: "preset-switch", name: "fast" })), `preset switch fast ${tail}`);
  assert.equal(argsOf(await setup({ op: "preset-restore", record })), `preset restore ${record} ${tail}`);
  assert.equal(argsOf(await setup({ op: "undo", record })), `setup undo ${record} ${tail}`);
  assert.equal(argsOf(await setup({ op: "save", name: "cheap-week" })), `setup save cheap-week ${tail}`);
  assert.equal(argsOf(await setup({ op: "save", name: "cheap-week", replace: "true", expect: rev })), `setup save cheap-week --replace --dir ${dir} --expect ${rev} --json`);
  assert.equal(argsOf(await setup({ op: "restore", name: "cheap-week", expect: rev })), `setup restore cheap-week --dir ${dir} --expect ${rev} --json`);
  assert.equal(argsOf(await setup({ op: "remove", name: "cheap-week", expect: rev })), `setup remove cheap-week --dir ${dir} --expect ${rev} --json`);
  const stale = await setup({ op: "remove", name: "cheap-week", expect: STALE_REV });
  assert.deepEqual([stale.error.includes("changed since"), stale.rev], [true, CURRENT_REV]);
  const spawned = log.length;
  assert.equal(spawned, 8, "one command each");
  const bad = [
    { op: "show", name: "x" }, { op: "save", name: "-x" }, { op: "save", name: "A" }, { op: "save", name: "a b" }, { op: "save", name: "a".repeat(81) }, { op: "save", name: "" },
    { op: "restore", name: "x/y" }, { op: "preset-switch", name: "-x" }, { op: "preset-switch", name: "a b" }, { op: "preset-switch", name: "" }, { op: "preset-switch", name: "x", expect: rev },
    { op: "undo", record: "relative.json" }, { op: "undo", record: "" }, { op: "undo", record: "/tmp/r.json", name: "x" }, { op: "preset-restore", record: "-r" },
    { op: "restore", name: "x", expect: "nothex" }, { op: "undo", record, expect: rev }, { op: "restore", name: "x", replace: "true" }, { op: "save", name: "x", replace: "yes" },
    { op: "save", name: "x", record }, { op: "save", name: "x", directory: "relative" }, { op: "save", name: "x", directory: "" },
  ];
  for (const fields of bad) assert.equal(typeof (await setup(fields)).error, "string", JSON.stringify(fields));
  assert.equal(typeof JSON.parse(await impl.setup({ op: "save", name: "x" })).error, "string", "missing fields");
  assert.equal(log.length, spawned, "invalid setup requests spawned something");
  const spend = async (fields) => JSON.parse(await impl.spend({ op: "limit", directory: dir, limit: "", warn: "", ...fields }));
  assert.equal(argsOf(await spend({ limit: "50.00" })), `limit 50.00 ${tail}`);
  assert.equal(argsOf(await spend({ limit: "60", warn: "45.50" })), `limit 60 --warn 45.50 ${tail}`);
  assert.equal(argsOf(await spend({ op: "off" })), `limit --off ${tail}`);
  const spawnedSpend = log.length;
  for (const fields of [{ limit: "" }, { limit: "-5" }, { limit: "1e3" }, { limit: "$5" }, { limit: "abc" }, { limit: "5", warn: "x" }, { limit: "5", warn: "-1" }, { limit: "12345678" }, { limit: "5.123" },
    { op: "off", limit: "5" }, { op: "off", warn: "5" }, { op: "show", limit: "5" }, { limit: "5", directory: "relative" }, { limit: "5", directory: "" }])
    assert.equal(typeof (await spend(fields)).error, "string", JSON.stringify(fields));
  assert.equal(log.length, spawnedSpend, "invalid spend requests spawned something");
  for (const entry of log) assert.ok(!JSON.stringify(entry).includes("shell\":true"), "a shell");
  const demo = mainContext({ home: ORCHESTRATION, python, demo: true });
  resolveDefault(loaded.module.exports).main(demo.ctx);
  assert.match(JSON.parse(await demo.provided[0].impl.setup({ ...blank, op: "save", name: "x" })).error, /demo/);
  assert.match(JSON.parse(await demo.provided[0].impl.spend({ op: "off", directory: dir, limit: "", warn: "" })).error, /demo/);
  assert.equal(log.length, spawnedSpend, "the demo spawned something");
});

await check("n10 panel: the view gets actions.setup and actions.spend, which ask main with string fields and the session's folder, carry the rev, turn a refusal into a rejection (a stale one with status 409) and refuse a bad request before main", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { setup, spend } = mountCalls[mountCalls.length - 1].options.actions;
  assert.equal(typeof setup, "function");
  assert.equal(typeof spend, "function");
  const empty = { directory: ORCHESTRATION, name: "", record: "", replace: "" };
  const rev = "0123456789abcdef";
  const switched = await setup({ op: "preset-switch", name: "fast" });
  assert.equal(switched.record, "/tmp/backups/undo.json");
  await setup({ op: "preset-restore", record: switched.record });
  await setup({ op: "save", name: "cheap-week" });
  await setup({ op: "save", name: "cheap-week", replace: true, expect: rev });
  await setup({ op: "restore", name: "cheap-week", expect: rev });
  await setup({ op: "remove", name: "cheap-week", expect: rev, text: "ignored" });
  await setup({ op: "undo", record: switched.record });
  assert.deepEqual(w.calls.filter((c) => c.op === "setup").map((c) => c.input), [
    { ...empty, op: "preset-switch", name: "fast" },
    { ...empty, op: "preset-restore", record: "/tmp/backups/undo.json" },
    { ...empty, op: "save", name: "cheap-week" },
    { ...empty, op: "save", name: "cheap-week", replace: "true", expect: rev },
    { ...empty, op: "restore", name: "cheap-week", expect: rev },
    { ...empty, op: "remove", name: "cheap-week", expect: rev },
    { ...empty, op: "undo", record: "/tmp/backups/undo.json" },
  ]);
  await spend({ op: "limit", limit: 50 });
  await spend({ op: "limit", limit: 60.5, warn: 45 });
  await spend({ op: "off" });
  assert.deepEqual(w.calls.filter((c) => c.op === "spend").map((c) => c.input), [
    { op: "limit", directory: ORCHESTRATION, limit: "50.00", warn: "" },
    { op: "limit", directory: ORCHESTRATION, limit: "60.50", warn: "45.00" },
    { op: "off", directory: ORCHESTRATION, limit: "", warn: "" },
  ]);
  const failure = async (action, request) => {
    try {
      await action(request);
    } catch (error) {
      return error;
    }
    return null;
  };
  assert.match((await failure(setup, { op: "restore", name: "damaged" })).message, /is damaged/);
  const stale = await failure(setup, { op: "remove", name: "cheap-week", expect: STALE_REV });
  assert.deepEqual([stale.status, stale.stale, stale.rev], [409, true, CURRENT_REV]);
  assert.match((await failure(spend, { op: "limit", limit: 999 })).message, /too high/);
  const count = w.calls.length;
  for (const bad of [null, { op: "show" }, { op: "save" }, { op: "save", name: "-x" }, { op: "save", name: "A" }, { op: "save", name: "a b" }, { op: "preset-switch", name: "-x" }, { op: "preset-switch", name: "a b" },
    { op: "undo" }, { op: "undo", record: "-r" }, { op: "save", name: "x", replace: "true" }, { op: "restore", name: "x", replace: true }, { op: "restore", name: "x", expect: "bad" },
    { op: "preset-switch", name: "x", expect: rev }, { op: "undo", record: "/tmp/r.json", expect: rev }])
    assert.equal((await failure(setup, bad))?.message, "Bad setup request.", JSON.stringify(bad));
  for (const bad of [null, { op: "show" }, { op: "limit" }, { op: "limit", limit: "50" }, { op: "limit", limit: 0 }, { op: "limit", limit: -1 }, { op: "limit", limit: NaN },
    { op: "limit", limit: Infinity }, { op: "limit", limit: 1000001 }, { op: "limit", limit: 5, warn: "x" }, { op: "limit", limit: 5, warn: 0 }, { op: "off", limit: 5 }, { op: "off", warn: 1 }])
    assert.equal((await failure(spend, bad))?.message, "Bad spend request.", JSON.stringify(bad));
  assert.equal(w.calls.length, count, "a bad request reached main");
  rendered.dispose();
});

await check("n10 panel: a session with no folder cannot write setups or the spend limit, and the demo has neither action", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: "" };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { setup, spend } = mountCalls[mountCalls.length - 1].options.actions;
  const failure = async (action, request) => {
    try {
      await action(request);
    } catch (error) {
      return error.message;
    }
    return "";
  };
  assert.equal(await failure(setup, { op: "save", name: "x" }), "No session is open.");
  assert.equal(await failure(spend, { op: "off" }), "No session is open.");
  assert.equal(w.calls.filter((c) => c.op === "setup" || c.op === "spend").length, 0);
  rendered.dispose();
});
// ---- end n10 ----

// ---- n24: the improvement proposals (Learning area) ----
await check("n24 main: improve runs `python3 -P -m agentgraph.improve list|show|approve|reject|reopen|revert ... --line` in home without a shell, never with --force; ids are checked; bad input and the demo spawn nothing", async () => {
  const python = path.join(TMP, "fake-improve-python");
  fs.writeFileSync(python, `#!/bin/sh
cat > /dev/null
shift 3
case "$1" in
  list) printf 'noise\\n{"proposals": [], "args": "%s"}\\n' "$*";;
  *) case "$2" in refused) printf '{"error": "proposal refused is applied; only a drafted one can be approved"}\\n'; exit 1;; esac; printf '{"id": "%s", "state": "x", "args": "%s"}\\n' "$2" "$*";;
esac
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const improve = async (fields) => JSON.parse(await impl.improve({ op: "read", id: "", note: "", ...fields }));
  assert.equal((await improve({})).args, "list --line");
  assert.equal((await improve({ id: "p-1.a_b" })).args, "show p-1.a_b --line");
  assert.equal((await improve({ op: "approve", id: "p-1" })).args, "approve p-1 --by-user approved in the panel --line");
  assert.equal((await improve({ op: "approve", id: "p-1", note: " ok then " })).args, "approve p-1 --by-user ok then --line");
  assert.equal((await improve({ op: "reject", id: "p-1", note: "not now; rm -rf" })).args, "reject p-1 --reason not now; rm -rf --line");
  assert.equal((await improve({ op: "reopen", id: "p-1" })).args, "reopen p-1 --line");
  assert.equal((await improve({ op: "revert", id: "p-1", note: "x" })).args, "revert p-1 --note x --line");
  assert.match((await improve({ op: "approve", id: "refused" })).error, /is applied/);
  const spawned = log.length;
  assert.equal(spawned, 8, "one command each");
  for (const fields of [{ op: "reject", id: "p-1" }, { op: "reject", id: "p-1", note: "  " }, { op: "approve", id: "" }, { op: "approve", id: "-x" }, { op: "approve", id: "../x" }, { op: "show", id: "p" },
    { op: "read", id: "a b" }, { op: "read", id: "-x" }, { op: "read", id: "p-1", note: "x" }, { op: "revert", id: "a".repeat(65) }, { op: "approve", id: "p-1", note: "x".repeat(301) }, { op: "approve", id: "p-1", note: "a\0b" }])
    assert.equal(typeof (await improve(fields)).error, "string", JSON.stringify(fields));
  assert.equal(typeof JSON.parse(await impl.improve({ op: "approve" })).error, "string", "missing fields");
  assert.equal(log.length, spawned, "invalid proposal requests spawned something");
  for (const entry of log) assert.ok(!JSON.stringify(entry).includes("--force") && !JSON.stringify(entry).includes("shell\":true"), "a force flag or a shell");
  const demo = mainContext({ home: ORCHESTRATION, python, demo: true });
  resolveDefault(loaded.module.exports).main(demo.ctx);
  assert.match(JSON.parse(await demo.provided[0].impl.improve({ op: "read", id: "", note: "" })).error, /demo/);
  assert.equal(log.length, spawned, "the demo spawned something");
});

await check("n24 panel: the view gets actions.improve, which asks main with string fields, answers the list or the proposal as main said, turns a refusal into a rejection with its words, and refuses a bad request before main; the demo has none", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { improve } = mountCalls[mountCalls.length - 1].options.actions;
  assert.equal(typeof improve, "function");
  assert.equal((await improve({ op: "read" })).proposals[0].id, "p-1");
  assert.equal((await improve({ op: "read", id: "p-1" })).state, "drafted");
  assert.equal((await improve({ op: "approve", id: "p-1" })).state, "applied");
  assert.equal((await improve({ op: "reject", id: "p-1", note: "no" })).state, "rejected");
  assert.equal((await improve({ op: "reopen", id: "p-1" })).state, "drafted");
  assert.equal((await improve({ op: "revert", id: "p-1" })).state, "reverted");
  assert.deepEqual(w.calls.filter((c) => c.op === "improve").map((c) => c.input), [
    { op: "read", id: "", note: "" }, { op: "read", id: "p-1", note: "" }, { op: "approve", id: "p-1", note: "" },
    { op: "reject", id: "p-1", note: "no" }, { op: "reopen", id: "p-1", note: "" }, { op: "revert", id: "p-1", note: "" },
  ]);
  const failure = async (request) => {
    try {
      await improve(request);
    } catch (error) {
      return error.message;
    }
    return "";
  };
  assert.match(await failure({ op: "approve", id: "p-refused" }), /changed since the proposal was drafted/);
  const asked = w.calls.filter((c) => c.op === "improve").length;
  for (const bad of [undefined, null, {}, { op: "show" }, { op: "approve" }, { op: "approve", id: "-x" }, { op: "read", id: "a b" }, { op: "approve", id: "p-1", note: 5 }, { op: "reject", id: "p-1", note: "x".repeat(301) }])
    assert.equal(await failure(bad), "Bad proposal request.", JSON.stringify(bad));
  assert.equal(w.calls.filter((c) => c.op === "improve").length, asked, "a bad request reached main");
  rendered.dispose();
  const demoWindow = windowContext(definition, { session, options: { demo: true } });
  definition.setup(demoWindow.ctx);
  const demoRendered = windowLoad.solid.runWithOwner(() => value(demoWindow.added[1].item).render(props));
  assert.equal(mountCalls[mountCalls.length - 1].options.actions?.improve, undefined, "the demo has an improve action");
  demoRendered.dispose();
});
// ---- end n24 ----

// ---- v4: loops (the Loop section of a self-loop flow) ----
await check("v4 main: loops runs `python3 -P -m agentgraph.loopapi --project DIR` in home without a shell, the page's request as JSON on stdin; only the known fields are passed on; bad input and the demo spawn nothing", async () => {
  const python = path.join(TMP, "fake-loops-python");
  const stdin = path.join(TMP, "fake-loops-stdin.json");
  fs.writeFileSync(python, `#!/bin/sh
cat > "${stdin}"
if grep -q refuse-me "${stdin}"; then printf '{"error": "refused", "rev": "${"cd".repeat(32)}"}\\n'; exit 1; fi
printf 'noise\\n{"ok": true, "args": "%s"}\\n' "$*"
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const loops = async (request, directory = "/work/project") => JSON.parse(await impl.loops({ directory, request: JSON.stringify(request) }));
  const first = await loops({ op: "read" });
  assert.equal(first.args, "-P -m agentgraph.loopapi --project /work/project");
  assert.deepEqual(JSON.parse(fs.readFileSync(stdin, "utf8")), { op: "read" });
  await loops({ op: "read", run: "20261121-043755-90a7" });
  assert.deepEqual(JSON.parse(fs.readFileSync(stdin, "utf8")), { op: "read", run: "20261121-043755-90a7" });
  const settings = { version: 1, flow: "fix-loop", goal: "g; rm -rf /", check: { command: "sh check.sh", timeout: 300, inconclusive_codes: [75] }, session: "reuse", limits: { attempts: 4 }, allow_asks: false };
  await loops({ op: "save", flow: "fix-loop", settings, rev: "", extra: "dropped" });
  assert.deepEqual(JSON.parse(fs.readFileSync(stdin, "utf8")), { op: "save", flow: "fix-loop", settings, rev: "" });
  await loops({ op: "remove", flow: "fix-loop", rev: "ab".repeat(32) });
  await loops({ op: "start", flow: "fix-loop", goal: "do it", workdir: "/tmp/work" });
  assert.deepEqual(JSON.parse(fs.readFileSync(stdin, "utf8")), { op: "start", flow: "fix-loop", goal: "do it", workdir: "/tmp/work" });
  await loops({ op: "cancel", run: "20261121-043755-90a7" });
  assert.equal((await loops({ op: "read" }, "")).args, "-P -m agentgraph.loopapi", "no folder, no --project");
  const refused = await loops({ op: "start", flow: "refuse-me" });
  assert.deepEqual([refused.error, refused.rev], ["refused", "cd".repeat(32)], "a refusal keeps its words and its rev");
  const spawned = log.length;
  assert.equal(spawned, 8, "one command each");
  const bad = [{}, { op: "list" }, { op: "read", run: "../x" }, { op: "read", run: "-x" }, { op: "save", flow: "f" }, { op: "save", flow: "f", settings: [], rev: "" }, { op: "save", flow: "f", settings: {}, rev: "zz" },
    { op: "save", flow: "", settings: {}, rev: "" }, { op: "remove", flow: "f" }, { op: "start", flow: "f", goal: 5 }, { op: "start", flow: "f", goal: "x".repeat(4001) }, { op: "start", flow: "f", workdir: "a\0b" },
    { op: "cancel" }, { op: "cancel", run: "a b" }, { op: "start", flow: "f".repeat(200) }];
  for (const request of bad) assert.equal(typeof (await loops(request)).error, "string", JSON.stringify(request));
  for (const odd of [{ directory: 5, request: "{}" }, { directory: "rel/dir", request: JSON.stringify({ op: "read" }) }, { directory: "/a\0b", request: JSON.stringify({ op: "read" }) }, { directory: "", request: "not json" }, { directory: "", request: "[]" }, { directory: "", request: JSON.stringify({ op: "save", flow: "f", settings: { big: "x".repeat(70000) }, rev: "" }) }])
    assert.equal(typeof JSON.parse(await impl.loops(odd)).error, "string", JSON.stringify(odd).slice(0, 80));
  assert.equal(typeof JSON.parse(await impl.loops({ directory: "" })).error, "string", "missing fields");
  assert.equal(log.length, spawned, "invalid loop requests spawned something");
  for (const entry of log) assert.ok(!JSON.stringify(entry).includes("--force") && !JSON.stringify(entry).includes("shell\":true"), "a force flag or a shell");
  const demo = mainContext({ home: ORCHESTRATION, python, demo: true });
  resolveDefault(loaded.module.exports).main(demo.ctx);
  assert.match(JSON.parse(await demo.provided[0].impl.loops({ directory: "", request: JSON.stringify({ op: "read" }) })).error, /demo/);
  assert.equal(log.length, spawned, "the demo spawned something");
});

await check("v4 panel: the view gets actions.loops, which asks main with the session's folder and the request as JSON text, answers as main said, turns a refusal into a rejection with its words (a stale one with its rev), and refuses a bad request before main; the demo has none", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { loops } = mountCalls[mountCalls.length - 1].options.actions;
  assert.equal(typeof loops, "function");
  assert.equal((await loops({ op: "read" })).loops[0].flow, "fix-loop");
  assert.equal((await loops({ op: "read", run: "20261121-043755-90a7" })).run.status, "passed");
  assert.equal((await loops({ op: "save", flow: "fix-loop", settings: { version: 1 }, rev: "" })).ok, true);
  assert.equal((await loops({ op: "start", flow: "fix-loop", goal: "g" })).ok, true);
  assert.deepEqual(w.calls.filter((c) => c.op === "loops").map((c) => c.input), [
    { directory: ORCHESTRATION, request: JSON.stringify({ op: "read" }) },
    { directory: ORCHESTRATION, request: JSON.stringify({ op: "read", run: "20261121-043755-90a7" }) },
    { directory: ORCHESTRATION, request: JSON.stringify({ op: "save", flow: "fix-loop", settings: { version: 1 }, rev: "" }) },
    { directory: ORCHESTRATION, request: JSON.stringify({ op: "start", flow: "fix-loop", goal: "g" }) },
  ]);
  const failure = async (request) => {
    try {
      await loops(request);
    } catch (error) {
      return error;
    }
    return null;
  };
  const refused = await failure({ op: "start", flow: "refused-loop" });
  assert.match(refused.message, /a loop needs limits\.attempts/);
  assert.notEqual(refused.status, 409);
  const stale = await failure({ op: "save", flow: "stale-loop", settings: {}, rev: "" });
  assert.deepEqual([stale.message, stale.status, stale.stale, stale.rev], ["the settings changed since you read them", 409, true, LOOP_REV_NOW]);
  // A main process that loaded the plugin before `loops` existed: the host's words become what to do about it.
  const old = await failure({ op: "start", flow: "old-main-loop", goal: "g" });
  assert.equal(old.message, "OpenCode Desktop is still running an older copy of this plugin. Quit the app and open it again to use loops.");
  const asked = w.calls.filter((c) => c.op === "loops").length;
  for (const bad of [undefined, null, {}, { op: "list" }, { op: "read", run: "../x" }, { op: "save", flow: "f" }, { op: "start", flow: "f", goal: 5 }, { op: "cancel" }])
    assert.equal((await failure(bad)).message, "Bad loop request.", JSON.stringify(bad));
  assert.equal(w.calls.filter((c) => c.op === "loops").length, asked, "a bad request reached main");
  rendered.dispose();
  const demoWindow = windowContext(definition, { session, options: { demo: true } });
  definition.setup(demoWindow.ctx);
  const demoRendered = windowLoad.solid.runWithOwner(() => value(demoWindow.added[1].item).render(props));
  assert.equal(mountCalls[mountCalls.length - 1].options.actions?.loops, undefined, "the demo has a loops action");
  demoRendered.dispose();
});
// ---- v7: loops between agents, a person and recovery, versions and trials ----
await check("v7 main: loops passes pause, resume (with a choice), answer (with a note), restore and a start with settings through, nothing else; bad values spawn nothing", async () => {
  const python = path.join(TMP, "fake-loops7-python");
  const stdin = path.join(TMP, "fake-loops7-stdin.json");
  fs.writeFileSync(python, `#!/bin/sh
cat > "${stdin}"
printf '{"ok": true}\\n'
`, { mode: 0o700 });
  const log = [];
  const loaded = loadMain(CODE, log);
  const m = mainContext({ home: ORCHESTRATION, python });
  resolveDefault(loaded.module.exports).main(m.ctx);
  const impl = m.provided[0].impl;
  const loops = async (request) => JSON.parse(await impl.loops({ directory: "/work/project", request: JSON.stringify(request) }));
  const sent = () => JSON.parse(fs.readFileSync(stdin, "utf8"));
  const run = "20261121-043755-90a7", version = "20261121T060000-000001", rev = "ab".repeat(32);
  const good = [
    { op: "pause", run },
    { op: "resume", run },
    { op: "resume", run, choice: "redo" },
    { op: "resume", run, choice: "accept" },
    { op: "answer", run, answer: "approve" },
    { op: "answer", run, answer: "reject", note: "the counter is never reset" },
    { op: "answer", run, answer: "stop" },
    { op: "restore", flow: "review-loop", version, rev },
    { op: "start", flow: "review-loop", goal: "g", settings: { version: 1, flow: "review-loop", limits: { attempts: 2 } } },
    { op: "save", flow: "review-loop", settings: { version: 1, approval: "before_pass", artifacts: ["a.md"], steps: { verifier: { retries: 2 } } }, rev: "" }
  ];
  for (const request of good) {
    assert.equal((await loops({ ...request, extra: "dropped" })).ok, true, JSON.stringify(request));
    assert.deepEqual(sent(), request, "what reached the command");
  }
  const spawned = log.length;
  assert.equal(spawned, good.length, "one command each");
  const bad = [{ op: "pause" }, { op: "pause", run: "../x" }, { op: "resume" }, { op: "resume", run, choice: "maybe" }, { op: "resume", run, choice: 5 },
    { op: "answer", run }, { op: "answer", run, answer: "maybe" }, { op: "answer", run, answer: "reject", note: 5 }, { op: "answer", run, answer: "reject", note: "x".repeat(4001) }, { op: "answer", run, answer: "reject", note: "a\0b" },
    { op: "restore", flow: "f", rev }, { op: "restore", flow: "f", version: "../x", rev }, { op: "restore", flow: "f", version, rev: "zz" }, { op: "restore", version, rev },
    { op: "start", flow: "f", settings: [] }, { op: "start", flow: "f", settings: "x" }];
  for (const request of bad) assert.equal(typeof (await loops(request)).error, "string", JSON.stringify(request));
  assert.equal(log.length, spawned, "a bad request spawned something");
});

await check("v7 panel: the view's loops action carries pause, resume, answer, restore and a trial start to main unchanged, and refuses a bad one before main", async () => {
  const definition = windowLoad.gui.defined[0];
  const session = { key: "srv\nses_a", id: "ses_a", directory: ORCHESTRATION };
  const props = { get session() { return session; }, screen: {} };
  const w = windowContext(definition, { session });
  definition.setup(w.ctx);
  const rendered = windowLoad.solid.runWithOwner(() => value(w.added[1].item).render(props));
  const { loops } = mountCalls[mountCalls.length - 1].options.actions;
  const run = "20261121-043755-90a7", version = "20261121T060000-000001", rev = "ab".repeat(32);
  const requests = [{ op: "pause", run }, { op: "resume", run, choice: "redo" }, { op: "answer", run, answer: "reject", note: "again" }, { op: "restore", flow: "fix-loop", version, rev }, { op: "start", flow: "fix-loop", goal: "g", settings: { version: 1 } }];
  for (const request of requests) assert.equal((await loops(request)).ok, true, JSON.stringify(request));
  assert.deepEqual(w.calls.filter((c) => c.op === "loops").map((c) => c.input.request), requests.map((request) => JSON.stringify(request)));
  const asked = w.calls.filter((c) => c.op === "loops").length;
  for (const bad of [{ op: "pause" }, { op: "resume", run, choice: "maybe" }, { op: "answer", run, answer: "maybe" }, { op: "restore", flow: "f", version: "../x", rev }, { op: "start", flow: "f", settings: [] }]) {
    let failure = null;
    try { await loops(bad); } catch (error) { failure = error; }
    assert.equal(failure && failure.message, "Bad loop request.", JSON.stringify(bad));
  }
  assert.equal(w.calls.filter((c) => c.op === "loops").length, asked, "a bad request reached main");
  rendered.dispose();
});
// ---- end v4 ----

// ---- v7 round 3: the Desktop bridge against the REAL interpreter and the repo's real agentgraph.loopapi (no model call) ----
// The fake interpreters above cannot tell whether `python3 -P -m agentgraph.loopapi` exists. These run the command itself, on a temp structure
// folder and a temp project, and set each answer beside what the page's HTTP server (agentgraph.server, the same folders copied) answers
// for the same request.
const LOOPS_WORLD_PY = `
import json, sys
from pathlib import Path
from agentgraph import looprun
base = Path(sys.argv[1])
structure = base / "personal" / "structure"
(structure / "30-build").mkdir(parents=True)
(structure / "30-build" / "worker.md").write_text("Does the work.\\n")
(structure / "30-build" / "reviewer.md").write_text("Reviews.\\n")
(structure / "flows").mkdir()
(structure / "flows" / "self.md").write_text("path: worker -> worker\\n\\nCheck its own work.\\n")
(structure / "flows" / "pair.md").write_text("path: worker -> reviewer -> worker\\n\\nA longer path.\\n")
project = base / "project"
project.mkdir()
looprun.save_settings(structure, "self", {"version": 1, "flow": "self", "limits": {"attempts": 3}, "check": {"command": "true", "timeout": 20}})
ids = []
for _ in range(2):
    ids.append(looprun.start(project, "self", structure=structure, goal="Make it pass.", wait=True, executor=looprun.ScriptedExecutor([{}])))
folder = looprun.run_dir(project, ids[1])  # the second run says running and has no controller (a controller that was killed)
state = looprun.read_run(folder)
(folder / "run.json").write_text(json.dumps({**state, "status": "running", "ended": None}))
print(json.dumps(ids))
`;
const LOOPS_SERVER_PY = `
import sys
from pathlib import Path
from agentgraph import server
base = Path(sys.argv[1])
httpd = server.make_server(0, structure_dir=base / "personal" / "structure", loops_project=base / "project")
print(httpd.server_port, flush=True)
httpd.serve_forever()
`;
await check("v7 r3 main (real python3): the bridge's loop command answers read, read of one run, save, a stale save, cancel of a run with no controller and an unknown run as the page's HTTP server does for the same request (same keys, same words)", async () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(TMP, "loops-real-")));
  const env = Object.assign({}, process.env, { PYTHONPATH: ORCHESTRATION, PYTHONIOENCODING: "utf-8" });
  delete env.AGENTGRAPH_PERSONAL;
  const a = path.join(root, "a"), b = path.join(root, "b");
  fs.mkdirSync(a);
  const made = childProcess.spawnSync("python3", ["-P", "-c", LOOPS_WORLD_PY, a], { cwd: ORCHESTRATION, env, encoding: "utf8" });
  assert.equal(made.status, 0, "building the temp world failed: " + made.stderr.slice(-400));
  const [run1, run2] = JSON.parse(made.stdout.trim().split("\n").pop());
  fs.cpSync(a, b, { recursive: true }); // the same bytes twice: a for the bridge, b for the page's server
  const httpd = childProcess.spawn("python3", ["-P", "-c", LOOPS_SERVER_PY, b], { cwd: ORCHESTRATION, env, stdio: ["ignore", "pipe", "pipe"] });
  const saved = process.env.AGENTGRAPH_PERSONAL;
  try {
    const port = await new Promise((resolve, reject) => {
      let out = "";
      httpd.stdout.on("data", (chunk) => { out += chunk; if (/\n/.test(out)) resolve(Number(out.trim())); });
      httpd.on("exit", () => reject(new Error("the page server did not start")));
      setTimeout(() => reject(new Error("the page server did not start in time")), 15000);
    });
    process.env.AGENTGRAPH_PERSONAL = path.join(a, "personal"); // the structure folder the real command reads (the bridge passes the process environment on)
    const m = mainContext({ home: ORCHESTRATION });
    resolveDefault(loadMain(CODE, []).module.exports).main(m.ctx);
    const impl = m.provided[0].impl;
    const bridge = async (request, directory = path.join(a, "project")) => JSON.parse(await impl.loops({ directory, request: JSON.stringify(request) }));
    const page = async (request) => {
      const read = request.op === "read";
      const url = "http://127.0.0.1:" + port + (read ? (request.run ? "/api/loop/run?id=" + encodeURIComponent(request.run) : "/api/loops") : "/api/loop");
      const response = await fetch(url, read ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
      return { status: response.status, doc: await response.json() };
    };
    const plain = (doc) => JSON.parse(JSON.stringify(doc).split(a).join("ROOT").split(b).join("ROOT"));
    const keys = (doc) => Object.keys(doc).sort();
    // read: the listing
    const list = await bridge({ op: "read" }), listed = await page({ op: "read" });
    assert.equal(typeof list.error, "undefined", "the listing: " + JSON.stringify(list).slice(0, 300));
    assert.deepEqual(keys(list), ["loops", "orphans", "project", "runs", "scratch"]);
    assert.deepEqual(plain(list), plain(listed.doc), "the listing differs from the page's");
    assert.deepEqual(list.loops.map((one) => one.flow).sort(), ["pair", "self"]);
    assert.equal(list.runs.length, 2);
    assert.equal(list.runs.find((one) => one.run === run2).status, "interrupted", "a run with no controller is shown interrupted");
    // read: one run
    const one = await bridge({ op: "read", run: run1 }), oneHttp = await page({ op: "read", run: run1 });
    assert.deepEqual(keys(one), keys(oneHttp.doc));
    assert.deepEqual(plain(one), plain(oneHttp.doc), "the run differs from the page's");
    assert.equal(one.run.status, "passed");
    // save with the rev the listing carried, then a second save with that same (now stale) rev
    const entry = list.loops.find((item) => item.flow === "self");
    const settings = { version: 1, flow: "self", limits: { attempts: 4 }, check: { command: "true", timeout: 20, inconclusive_codes: [75] }, session: "reuse", allow_asks: false };
    const save = { op: "save", flow: "self", settings, rev: entry.rev };
    const saved = await bridge(save), savedHttp = await page(save);
    assert.deepEqual(keys(saved), keys(savedHttp.doc));
    assert.deepEqual(plain(saved), plain(savedHttp.doc), "the save differs from the page's");
    assert.equal(saved.ok, true);
    assert.equal(saved.settings.limits.attempts, 4);
    const stale = await bridge(save), staleHttp = await page(save);
    assert.equal(staleHttp.status, 409, "the page's server refuses a stale rev with 409");
    assert.deepEqual({ error: stale.error, rev: stale.rev }, { error: staleHttp.doc.error, rev: staleHttp.doc.rev }, "the stale refusal differs from the page's");
    assert.match(stale.error, /changed since/i);
    assert.equal(stale.rev, saved.rev, "the refusal carries the current rev");
    // cancel of a run with no controller
    const cancel = { op: "cancel", run: run2 };
    const cancelled = await bridge(cancel), cancelledHttp = await page(cancel);
    assert.deepEqual(plain(cancelled), plain(cancelledHttp.doc), "the cancel differs from the page's");
    assert.equal(cancelled.run, run2);
    assert.equal(cancelled.result, "cancelled", "a run with no controller is marked cancelled here");
    const after = await bridge({ op: "read", run: run2 }), afterHttp = await page({ op: "read", run: run2 });
    assert.equal(after.run.status, "cancelled");
    assert.equal(afterHttp.doc.run.status, "cancelled");
    // an unknown run, and no project folder
    const missing = { op: "read", run: "20261121-000000-ffff" };
    const none = await bridge(missing), noneHttp = await page(missing);
    assert.equal(noneHttp.status, 404);
    assert.deepEqual(plain({ error: none.error }), plain({ error: noneHttp.doc.error }), "the unknown run is not refused in the page's words");
    const bare = await bridge({ op: "read" }, "");
    assert.deepEqual([keys(bare), bare.project, bare.runs], [["loops", "orphans", "project", "runs", "scratch"], "", []], "no folder: no runs");
  } finally {
    if (saved === undefined) delete process.env.AGENTGRAPH_PERSONAL; else process.env.AGENTGRAPH_PERSONAL = saved;
    httpd.kill("SIGKILL");
  }
});
// v7 r4: the waiting-run path through the bridge on the real interpreter: a run folder written by hand as `waiting` (no controller), an answer, the record
// saying `answered`, and a second answer refused in the server's words.
const LOOPS_WAIT_PY = LOOPS_WORLD_PY + `
folder = looprun.run_dir(project, ids[0])
state = looprun.read_run(folder)
(folder / "run.json").write_text(json.dumps({**state, "status": "waiting", "ended": None, "waiting": {"step": "check", "outcome": "pass", "since": 1.0, "question": "check ended with pass. Answer approve to carry on."}}))
print(json.dumps(ids[0]))
`;
await check("v7 r4 main (real python3): a run written as waiting with no controller: read says answered false, `answer` through the bridge, read says answered true (the controller lane's field), a second answer is the 409 in the server's words; each answer equals the page server's for the same request", async () => {
  const root = fs.realpathSync(fs.mkdtempSync(path.join(TMP, "loops-wait-")));
  const env = Object.assign({}, process.env, { PYTHONPATH: ORCHESTRATION, PYTHONIOENCODING: "utf-8" });
  delete env.AGENTGRAPH_PERSONAL;
  const a = path.join(root, "a"), b = path.join(root, "b");
  fs.mkdirSync(a);
  const made = childProcess.spawnSync("python3", ["-P", "-c", LOOPS_WAIT_PY, a], { cwd: ORCHESTRATION, env, encoding: "utf8" });
  assert.equal(made.status, 0, "building the temp world failed: " + made.stderr.slice(-400));
  const id = JSON.parse(made.stdout.trim().split("\n").pop());
  fs.cpSync(a, b, { recursive: true });
  const httpd = childProcess.spawn("python3", ["-P", "-c", LOOPS_SERVER_PY, b], { cwd: ORCHESTRATION, env, stdio: ["ignore", "pipe", "pipe"] });
  const saved = process.env.AGENTGRAPH_PERSONAL;
  try {
    const port = await new Promise((resolve, reject) => {
      let out = "";
      httpd.stdout.on("data", (chunk) => { out += chunk; if (/\n/.test(out)) resolve(Number(out.trim())); });
      httpd.on("exit", () => reject(new Error("the page server did not start")));
      setTimeout(() => reject(new Error("the page server did not start in time")), 15000);
    });
    process.env.AGENTGRAPH_PERSONAL = path.join(a, "personal");
    const m = mainContext({ home: ORCHESTRATION });
    resolveDefault(loadMain(CODE, []).module.exports).main(m.ctx);
    const impl = m.provided[0].impl;
    const bridge = async (request) => JSON.parse(await impl.loops({ directory: path.join(a, "project"), request: JSON.stringify(request) }));
    const page = async (request) => {
      const read = request.op === "read";
      const url = "http://127.0.0.1:" + port + (read ? (request.run ? "/api/loop/run?id=" + encodeURIComponent(request.run) : "/api/loops") : "/api/loop");
      const response = await fetch(url, read ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
      return { status: response.status, doc: await response.json() };
    };
    const plain = (doc) => JSON.parse(JSON.stringify(doc).split(a).join("ROOT").split(b).join("ROOT"));
    const same = async (request, what) => { const x = await bridge(request), y = await page(request); assert.deepEqual(Object.keys(x).sort(), Object.keys(y.doc).sort(), what + ": keys"); assert.deepEqual(plain(x), plain(y.doc), what + ": differs from the page's"); return { bridge: x, status: y.status }; };
    // 1. read: waiting, no controller, nothing answered
    const before = (await same({ op: "read", run: id }, "read before")).bridge;
    assert.equal(before.run.status, "waiting");
    assert.equal(before.run.live, false, "no controller");
    // 2. answer through the bridge (the same on the page's server)
    const request = { op: "answer", run: id, answer: "approve" };
    const answered = await same(request, "answer");
    assert.equal(answered.bridge.ok, true, JSON.stringify(answered.bridge));
    // 3. the record: answered true now (the controller lane's field: red until it lands)
    const after = (await same({ op: "read", run: id }, "read after")).bridge;
    assert.equal(before.run.answered, false, "the run record says answered false before an answer");
    assert.equal(after.run.answered, true, "the run record says answered true once an answer is saved");
    const row = (await bridge({ op: "read" })).runs.find((one) => one.run === id);
    assert.equal(row.answered, true, "the listing row says it too");
    // 4. a second answer: refused with 409 in the server's words
    const twice = await same(request, "second answer");
    assert.equal(twice.status, 409);
    assert.match(twice.bridge.error, /already has an answer that was not taken yet/);
  } finally {
    if (saved === undefined) delete process.env.AGENTGRAPH_PERSONAL; else process.env.AGENTGRAPH_PERSONAL = saved;
    httpd.kill("SIGKILL");
  }
});

await check("the harness replaces an existing temporary bundle before reading it", () => {
  // build.mjs writes atomically, so even an identical rebuild replaces the existing file.
  assert.notEqual(fs.statSync(BUNDLE).ino, previousBundle.ino, "the existing bundle was reused");
});

await check("the live bundle's modification time and file identity are unchanged", () => {
  assert.deepEqual(liveBundleState(), liveBefore);
});
