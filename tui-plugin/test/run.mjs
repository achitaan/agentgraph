// Tests for the agentgraph TUI sidebar plugin. Run with plain node: `node tui-plugin/test/run.mjs`.
// Exit status is non-zero when any test fails. No npm, no bun, no OpenCode needed.
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";
import module from "node:module";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const src = (name) => pathToFileURL(path.join(root, "src", name)).href;

const R = await import(src("render.js"));
const { createSnapshotRunner } = await import(src("runner.js"));
const P = await import(src("plugin.js"));

const tests = [];
const test = (name, fn) => tests.push({ name, fn });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const demo = JSON.parse(fs.readFileSync(path.join(here, "fixtures", "demo.json"), "utf8"));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "agentgraph-tui-test-"));
process.on("exit", () => fs.rmSync(tmp, { recursive: true, force: true }));

// ---------------------------------------------------------------------------------------------------------------
// Rendering

const text = (doc, width = 37, frame = 0) => R.toText(R.render(doc, { width, frame })).split("\n");
const A = (name, group, busy = 0, parent = "") => ({ name, group, busy, parent, sessions: [] });
const D = (agents, graph = null, rootAgent = "orchestrator") => ({
  version: 1, source: "live", warnings: [], root: { agent: rootAgent }, groups: [], agents, graph,
});
const G = (extra = {}) => Object.assign({ progress: { passed: 0, total: 0 }, nodes: [], loops: [], work_loops: [] }, extra);
const allDocs = [];
const keep = (doc) => (allDocs.push(doc), doc);

test("demo document at width 37", () => {
  assert.deepEqual(text(keep(demo), 37), [
    "⠋ orchestrator        ━━━━────── 4/11",
    "",
    "○ council",
    "  ○ fable",
    "  ○ astra",
    "  ○ carbon",
    "",
    "○ oracle",
    "○ oracle-lite",
    "",
    "○ impl-openai ↻3 ⇄ reviewer",
    "● impl-claude ↻5 ⇄ reviewer Apply th…",
    "○ impl-hard",
    "● designer ⇄ reviewer 429 error page…",
    "",
    "○ reviewer",
    "",
    "○ explorer ⇄ oracle",
    "○ researcher",
  ]);
});

test("demo document at width 20", () => {
  assert.deepEqual(text(demo, 20), [
    "⠋ orchestrator  4/11",
    "",
    "○ council",
    "  ○ fable",
    "  ○ astra",
    "  ○ carbon",
    "",
    "○ oracle",
    "○ oracle-lite",
    "",
    "○ impl-op… ↻3 ⇄ rev…",
    "● impl-cl… ↻5 ⇄ rev…",
    "○ impl-hard",
    "● designer ⇄ review…",
    "",
    "○ reviewer",
    "",
    "○ explorer ⇄ oracle",
    "○ researcher",
  ]);
});

test("demo tones: live things are accent, idle muted, waiting work loop muted, active review loop accent", () => {
  const lines = R.render(demo, { width: 37, frame: 0 });
  const span = (row, t) => lines[row].find((s) => s.text === t);
  assert.equal(span(0, "⠋").tone, "accent");
  assert.equal(span(0, "━━━━").tone, "accent");
  assert.equal(span(0, "──────").tone, "muted");
  assert.equal(span(2, "○").tone, "muted");
  assert.equal(span(10, "↻3").tone, "muted"); // impl-openai: state waiting
  assert.equal(span(10, "⇄ reviewer").tone, "accent"); // sent_back is active
  assert.equal(span(11, "●").tone, "accent");
  assert.equal(span(11, "↻5").tone, "accent");
  assert.equal(span(17, "⇄ oracle").tone, "muted"); // closed
  for (const line of lines) for (const s of line) assert.ok(R.TONES.includes(s.tone), s.tone);
});

test("spinner advances with frame while the hub works", () => {
  assert.equal(text(demo, 37, 1)[0][0], "⠙");
  assert.equal(text(demo, 37, 10)[0][0], "⠋");
  assert.equal(R.isAnimated(demo), true);
});

test("loading, error, empty", () => {
  assert.deepEqual(text(null), ["Loading"]);
  assert.deepEqual(text(undefined), ["Loading"]);
  assert.deepEqual(text(keep({ version: 1, error: "Python was not found at /x" })), ["Python was not found at /x"]);
  assert.deepEqual(R.render({ version: 1, error: "x" })[0][0].tone, "danger");
  assert.deepEqual(
    text(keep({ version: 1, error: "The snapshot command exited with status 1: Traceback (most recent call last)" })),
    ["The snapshot command exited with sta…"],
  );
  assert.deepEqual(text(keep(D([]))), ["No agents"]);
  assert.equal(R.render(D([]))[0][0].tone, "muted");
  assert.equal(R.isAnimated({ version: 1, error: "x" }), false);
});

test("no graph, idle hub", () => {
  const doc = keep(D([A("orchestrator", "hub"), A("explorer", "understand")]));
  assert.deepEqual(text(doc), ["○ orchestrator", "", "○ explorer"]);
  assert.equal(R.isAnimated(doc), false);
});

test("busy hub with progress", () => {
  const doc = keep(D([A("orchestrator", "hub", 1)], G({ progress: { passed: 3, total: 8 } })));
  assert.deepEqual(text(doc, 37, 0), ["⠋ orchestrator         ━━━━────── 3/8"]);
  assert.deepEqual(text(doc, 37, 1), ["⠙ orchestrator         ━━━━────── 3/8"]);
  assert.deepEqual(text(doc, 20, 0), ["⠋ orchestrator   3/8"]);
});

test("hub is the root's agent, even when another hub agent is listed", () => {
  const doc = keep(D([A("orchestrator", "hub", 1), A("lead", "hub", 0), A("reviewer", "verify")], null, "lead"));
  assert.deepEqual(text(doc), ["○ lead", "", "● orchestrator", "", "○ reviewer"]);
});

test("council with seats", () => {
  const doc = keep(D(
    [A("orchestrator", "hub", 1), A("councillor-fable", "council", 1, "council"), A("councillor-astra", "council", 0, "council"), A("council", "council", 1)],
    G({
      progress: { passed: 0, total: 2 },
      nodes: [
        { id: "a", agent: "councillor-fable", busy: true, status: "running", goal: "Weigh option A" },
        { id: "b", agent: "council", busy: true, status: "running", goal: "Synthesize" },
      ],
    }),
  ));
  assert.deepEqual(text(doc), [
    "⠋ orchestrator         ────────── 0/2",
    "",
    "● council Synthesize",
    "  ● fable Weigh option A",
    "  ○ astra",
  ]);
});

test("seats without a listed lead stay at top level with their short name", () => {
  const doc = keep(D([A("orchestrator", "hub"), A("councillor-fable", "council", 0, "")]));
  assert.deepEqual(text(doc), ["○ orchestrator", "", "○ fable"]);
});

test("group order: council, decide, build, verify, understand, then unknown groups", () => {
  const doc = keep(D([
    A("orchestrator", "hub"), A("explorer", "understand"), A("oracle", "decide"), A("council", "council"),
    A("impl-openai", "build"), A("reviewer", "verify"), A("odd", "elsewhere"),
  ]));
  assert.deepEqual(text(doc), [
    "○ orchestrator", "", "○ council", "", "○ oracle", "", "○ impl-openai", "", "○ reviewer", "", "○ explorer", "", "○ odd",
  ]);
});

test("each loop state", () => {
  const doc = keep(D(
    [A("orchestrator", "hub"), A("impl-openai", "build"), A("impl-claude", "build", 1), A("impl-hard", "build"), A("designer", "build"), A("reviewer", "verify")],
    G({
      nodes: [{ id: "n1", agent: "impl-claude", busy: true, status: "running", goal: "Fix it" }],
      work_loops: [
        { agent: "impl-claude", nodes: ["n1"], rounds: 3, state: "working", active: true },
        { agent: "impl-openai", nodes: ["n2"], rounds: 2, state: "stuck", active: false },
        { agent: "impl-hard", nodes: ["n3"], rounds: 1, state: "working", active: true },
        { agent: "designer", nodes: ["n4"], rounds: 0, state: "stuck", active: false },
        { agent: "ghost", nodes: ["n5"], rounds: 4, state: "working", active: true },
      ],
      loops: [
        { id: "impl-claude~reviewer", worker: "impl-claude", evaluator: "reviewer", rounds: 3, state: "working", active: true },
        { id: "impl-openai~reviewer", worker: "impl-openai", evaluator: "reviewer", rounds: 2, state: "closed", active: false },
        { id: "impl-hard~ghost", worker: "impl-hard", evaluator: "ghost", rounds: 2, state: "checking", active: true },
        { id: "ghost~reviewer", worker: "ghost", evaluator: "reviewer", rounds: 2, state: "checking", active: true },
      ],
    }),
  ));
  assert.deepEqual(text(doc), [
    "○ orchestrator",
    "",
    "○ impl-openai ↻2! ⇄ reviewer",
    "● impl-claude ↻3 ⇄ reviewer Fix it",
    "○ impl-hard",
    "○ designer ↻!",
    "",
    "○ reviewer",
  ]);
  const lines = R.render(doc, { width: 37 });
  assert.equal(lines[2].find((s) => s.text === "↻2!").tone, "danger");
  assert.equal(lines[2].find((s) => s.text === "⇄ reviewer").tone, "muted");
  assert.equal(lines[3].find((s) => s.text === "↻3").tone, "accent");
  assert.equal(lines[3].find((s) => s.text === "⇄ reviewer").tone, "accent");
  assert.equal(lines[5].find((s) => s.text === "↻!").tone, "danger");
});

test("names and goals too long for 37 columns", () => {
  const longHub = "orchestrator-with-an-extremely-long-name-x";
  const doc = keep(D(
    [A(longHub, "hub", 1), A("impl-claude", "build", 1), A("impl-supercalifragilisticexpialidocious", "build", 1), A("reviewer-with-a-very-long-name", "verify")],
    G({
      progress: { passed: 1, total: 3 },
      nodes: [
        { id: "n1", agent: "impl-claude", busy: true, status: "running", goal: "Apply the limiter in the middleware chain and update every route" },
        { id: "n2", agent: "impl-supercalifragilisticexpialidocious", busy: false, status: "running", goal: "Something long" },
      ],
      work_loops: [{ agent: "impl-supercalifragilisticexpialidocious", nodes: ["n2"], rounds: 12, state: "working", active: true }],
      loops: [{ id: "x", worker: "impl-supercalifragilisticexpialidocious", evaluator: "reviewer-with-a-very-long-name", rounds: 12, state: "working", active: true }],
    }),
    longHub,
  ));
  assert.deepEqual(text(doc), [
    "⠋ orchestrator-with-an-extremely… 1/3",
    "",
    "● impl-claude Apply the limiter in t…",
    "● impl-supercalifragilist… ↻12 ⇄ rev…",
    "",
    "○ reviewer-with-a-very-long-name",
  ]);
});

test("wide characters and escape sequences in untrusted text", () => {
  const doc = keep(D(
    [A("orchestrator", "hub"), A("evil\u001b[31mred\u0007", "build", 1)],
    G({ nodes: [{ id: "n", agent: "evil\u001b[31mred\u0007", busy: true, status: "running", goal: "修复中间件链中的限流器问题并更新全部路由" }] }),
  ));
  const lines = text(doc);
  assert.equal(lines[2], "● evilred 修复中间件链中的限流器问题…");
  assert.equal(R.displayWidth(lines[2]), 37);
});

test("width 20 for hand-made documents", () => {
  const doc = keep(D(
    [A("orchestrator", "hub", 1), A("council", "council", 1), A("councillor-carbon", "council", 0, "council"), A("impl-claude", "build", 1)],
    G({
      progress: { passed: 2, total: 5 },
      nodes: [{ id: "n", agent: "impl-claude", busy: true, status: "running", goal: "Apply the limiter" }],
      work_loops: [{ agent: "impl-claude", nodes: ["n"], rounds: 2, state: "working", active: true }],
    }),
  ));
  assert.deepEqual(text(doc, 20), [
    "⠋ orchestrator   2/5",
    "",
    "● council",
    "  ○ carbon",
    "",
    "● impl-claude ↻2",
  ].map((l) => l));
});

test("every line fits its width (all documents, many widths)", () => {
  const widths = [37, 30, 24, 20, 16, 12, 8, 5, 3, 2, 1];
  let checked = 0;
  for (const doc of [...allDocs, null]) {
    for (const width of widths) {
      for (const frame of [0, 3]) {
        for (const line of R.render(doc, { width, frame })) {
          assert.ok(R.lineWidth(line) <= width, `width ${width}: ${JSON.stringify(R.toText([line]))}`);
          checked += 1;
        }
      }
    }
  }
  assert.ok(checked > 200);
});

test("display width and truncate", () => {
  assert.equal(R.displayWidth("abc"), 3);
  assert.equal(R.displayWidth("修复"), 4);
  assert.equal(R.displayWidth("e\u0301"), 1);
  assert.equal(R.truncate("abcdef", 4), "abc…");
  assert.equal(R.truncate("abcdef", 6), "abcdef");
  assert.equal(R.truncate("abcdef", 1), "…");
  assert.equal(R.truncate("修复中间", 5), "修复…");
  assert.equal(R.seatName("councillor-astra"), "astra");
  assert.equal(R.seatName("council"), "council");
});

// ---------------------------------------------------------------------------------------------------------------
// Snapshot runner with fake interpreters

function fakePython(name, body) {
  const file = path.join(tmp, name);
  fs.writeFileSync(file, "#!/bin/sh\n" + body + "\n", { mode: 0o755 });
  return file;
}

function runnerFor(python, extra = {}) {
  return createSnapshotRunner(Object.assign({ spawn, fs, path, env: process.env, home: tmp, python }, extra));
}

const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (_) {
    return false;
  }
};

async function waitFor(check, ms = 5000, what = "condition") {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (check()) return;
    await sleep(20);
  }
  throw new Error("timed out waiting for " + what);
}

test("runner: missing interpreter", async () => {
  const doc = await runnerFor(path.join(tmp, "no-such-python")).snapshot({ session: "ses_a" });
  assert.match(doc.error, /^Python was not found at .*no-such-python/);
});

test("runner: interpreter not executable", async () => {
  const file = path.join(tmp, "not-exec");
  fs.writeFileSync(file, "x", { mode: 0o644 });
  const doc = await runnerFor(file).snapshot({ session: "ses_a" });
  assert.match(doc.error, /^Python is not executable/);
});

test("runner: relative interpreter path is refused", async () => {
  const doc = await runnerFor("python3").snapshot({ session: "ses_a" });
  assert.match(doc.error, /^options\.python must be an absolute path/);
});

test("runner: interpreter too old for -P", async () => {
  const py = fakePython("old-python", 'echo "Unknown option: -P" >&2\necho "usage: python3 [option] ..." >&2\nexit 2');
  const doc = await runnerFor(py).snapshot({ session: "ses_a" });
  assert.match(doc.error, /^Python 3\.11 or newer is required/);
});

test("runner: timeout kills the process", async () => {
  const pidFile = path.join(tmp, "timeout.pid");
  const py = fakePython("slow-python", `echo $$ > "${pidFile}"\nexec sleep 30`);
  const started = Date.now();
  const doc = await runnerFor(py, { timeoutMs: 400 }).snapshot({ session: "ses_a" });
  assert.match(doc.error, /did not finish within/);
  assert.ok(Date.now() - started < 3000);
  const pid = Number(fs.readFileSync(pidFile, "utf8"));
  await waitFor(() => !alive(pid), 2000, "killed child");
});

test("runner: oversized output", async () => {
  const py = fakePython("big-python", "head -c 200000 /dev/zero | tr '\\0' a");
  const doc = await runnerFor(py, { maxBytes: 1024 }).snapshot({ session: "ses_a" });
  assert.match(doc.error, /^The snapshot was larger than/);
});

test("runner: bad JSON", async () => {
  const doc = await runnerFor(fakePython("bad-json", "echo 'not json'")).snapshot({ session: "ses_a" });
  assert.match(doc.error, /invalid JSON/);
});

test("runner: empty output", async () => {
  const doc = await runnerFor(fakePython("empty", "exit 0")).snapshot({ session: "ses_a" });
  assert.equal(doc.error, "The snapshot command printed nothing.");
});

test("runner: wrong version", async () => {
  const doc = await runnerFor(fakePython("v2", "echo '{\"version\":2}'")).snapshot({ session: "ses_a" });
  assert.match(doc.error, /^Unsupported snapshot version: 2/);
});

test("runner: non-zero exit with stderr", async () => {
  const doc = await runnerFor(fakePython("boom", "echo starting\necho boom >&2\nexit 3")).snapshot({ session: "ses_a" });
  assert.equal(doc.error, "The snapshot command exited with status 3: boom");
});

test("runner: non-zero exit keeps the command's own error document", async () => {
  const doc = await runnerFor(fakePython("own-error", "printf '%s\\n' '{\"version\":1,\"error\":\"no   db\\there\"}'\nexit 1")).snapshot({ session: "ses_a" });
  assert.deepEqual(doc, { version: 1, error: "no db here" });
});

test("runner: invalid session id", async () => {
  const doc = await runnerFor(fakePython("unused", "exit 0")).snapshot({ session: "ses a;rm" });
  assert.equal(doc.error, "The session id is not valid.");
});

test("runner: arguments, environment, working directory, no shell", async () => {
  const out = path.join(tmp, "args.txt");
  const fixture = path.join(here, "fixtures", "demo.json");
  const py = fakePython("args-python", `pwd -P > "${out}"\necho "$PYTHONPATH" >> "${out}"\necho "$@" >> "${out}"\ncat "${fixture}"`);
  const work = fs.mkdtempSync(path.join(tmp, "work-"));
  const doc = await runnerFor(py, { home: "/opt/orch" }).snapshot({ session: "ses_abc", directory: work });
  assert.equal(doc.version, 1);
  assert.equal(doc.agents.length, demo.agents.length);
  const [cwd, pythonPath, args] = fs.readFileSync(out, "utf8").trim().split("\n");
  assert.equal(cwd, fs.realpathSync(work));
  assert.equal(pythonPath, "/opt/orch");
  assert.equal(args, "-P -m agentgraph.snapshot_json --session=ses_abc");
  // A missing directory falls back to home (here: tmp).
  const doc2 = await runnerFor(py).snapshot({ session: "ses_abc", directory: path.join(tmp, "missing") });
  assert.equal(doc2.version, 1);
  assert.equal(fs.readFileSync(out, "utf8").split("\n")[0], fs.realpathSync(tmp));
});

test("runner: one process per session at a time", async () => {
  const log = path.join(tmp, "count.log");
  const py = fakePython("count-python", `echo run >> "${log}"\nsleep 0.3\necho '{"version":1,"agents":[]}'`);
  const runner = runnerFor(py);
  const [a, b] = await Promise.all([runner.snapshot({ session: "ses_x" }), runner.snapshot({ session: "ses_x" })]);
  assert.equal(a, b);
  assert.equal(fs.readFileSync(log, "utf8").trim().split("\n").length, 1);
  await Promise.all([runner.snapshot({ session: "ses_x" }), runner.snapshot({ session: "ses_y" })]);
  assert.equal(fs.readFileSync(log, "utf8").trim().split("\n").length, 3);
  assert.equal(runner.inflight(), 0);
});

test("runner: stop() kills the child", async () => {
  const pidFile = path.join(tmp, "stop.pid");
  const py = fakePython("hang-python", `echo $$ > "${pidFile}"\nexec sleep 30`);
  const runner = runnerFor(py);
  const pending = runner.snapshot({ session: "ses_a" });
  await waitFor(() => fs.existsSync(pidFile) && fs.readFileSync(pidFile, "utf8").trim(), 3000, "pid file");
  const pid = Number(fs.readFileSync(pidFile, "utf8"));
  assert.equal(runner.running(), 1);
  runner.stop();
  const doc = await pending;
  assert.equal(doc.error, "The plugin stopped.");
  await waitFor(() => !alive(pid), 2000, "killed child");
  assert.equal((await runner.snapshot({ session: "ses_a" })).error, "The plugin stopped.");
});

const realPython = P.DEFAULT_PYTHON;
const orchestration = path.resolve(root, "..");
test("runner: real interpreter, real demo document", async () => {
  if (!fs.existsSync(realPython)) {
    console.log("    (skipped: " + realPython + " not found)");
    return;
  }
  const doc = await runnerFor(realPython, { home: orchestration, demo: true }).snapshot({ session: "" });
  assert.equal(doc.error, undefined, doc.error);
  assert.deepEqual(doc.agents.map((a) => a.name), demo.agents.map((a) => a.name));
  assert.equal(text(doc)[0].slice(1), text(demo)[0].slice(1));
});

// ---------------------------------------------------------------------------------------------------------------
// Theme mapping

test("theme: accent is the warmer of warning/accent, never green", () => {
  const tones = P.themeTones({ text: { default: "#eeeeee", muted: "#888888" }, warning: "#d08040", accent: "#5080e0", error: "#e05050" });
  assert.deepEqual(tones.accent, { fg: "#d08040", bold: false });
  assert.deepEqual(tones.muted, { fg: "#888888", bold: false });
  assert.deepEqual(tones.danger, { fg: "#e05050", bold: false });
  const green = P.themeTones({ text: { default: "#eeeeee", subdued: "#777777" }, accent: "#40c060", warning: "#30b050" });
  assert.deepEqual(green.accent, { fg: "#eeeeee", bold: true });
  assert.deepEqual(green.muted, { fg: "#777777", bold: false });
  const rgba = P.themeTones({ text: { default: { r: 0.9, g: 0.9, b: 0.9 } }, accent: { r: 0.2, g: 0.8, b: 0.3 }, warning: { r: 0.8, g: 0.45, b: 0.25 } });
  assert.deepEqual(rgba.accent.fg, { r: 0.8, g: 0.45, b: 0.25 });
  assert.doesNotThrow(() => P.themeTones(undefined));
  assert.equal(P.isGreen("#00ff00"), true);
  assert.equal(P.isGreen("#c8643c"), false);
});

test("options: defaults, minimum interval, mode", () => {
  const o = P.resolveOptions(undefined, { os, path });
  assert.equal(o.python, P.DEFAULT_PYTHON);
  assert.equal(o.home, path.join(os.homedir(), ".config", "opencode", "orchestration"));
  assert.equal(o.intervalMs, 4000);
  assert.equal(o.mode, "replace");
  const p = P.resolveOptions({ intervalMs: 10, mode: "prepend", python: "~/py", home: "~/orch" }, { os, path });
  assert.equal(p.intervalMs, 1000);
  assert.equal(p.mode, "prepend");
  assert.equal(p.python, path.join(os.homedir(), "py"));
  assert.equal(p.home, path.join(os.homedir(), "orch"));
  assert.equal(P.resolveOptions({ mode: "after" }, { os, path }).mode, "replace");
});

// ---------------------------------------------------------------------------------------------------------------
// The plugin module (src/tui.js) with stub host modules

// A tiny reactive core shared by the two stub modules: signals re-run the computations that read them.
globalThis.__agentgraphStub = (() => {
  let listener = null;
  return {
    createSignal(value) {
      const subs = new Set();
      const read = () => {
        if (listener) subs.add(listener);
        return value;
      };
      const write = (next) => {
        value = typeof next === "function" ? next(value) : next;
        for (const sub of [...subs]) sub();
        return value;
      };
      return [read, write];
    },
    track(fn) {
      const run = () => {
        const previous = listener;
        listener = run;
        try {
          fn();
        } finally {
          listener = previous;
        }
      };
      run();
    },
  };
})();

const STUBS = {
  "solid-js": "const s = globalThis.__agentgraphStub; export const createSignal = (v) => s.createSignal(v);",
  "@opentui/solid": [
    "const s = globalThis.__agentgraphStub;",
    "export function createElement(tag) { return { tag, props: {}, children: [] }; }",
    "export function setProp(node, key, value) { node.props[key] = value; }",
    "export function insert(node, child) {",
    "  if (typeof child === 'function') { s.track(() => { const v = child(); node.children = Array.isArray(v) ? v : [v]; }); }",
    "  else node.children.push(child);",
    "}",
  ].join("\n"),
};
const seenSpecifiers = new Set();
module.registerHooks({
  resolve(specifier, context, nextResolve) {
    if (context.parentURL && context.parentURL.startsWith(pathToFileURL(path.join(root, "src")).href)) seenSpecifiers.add(specifier);
    if (Object.hasOwn(STUBS, specifier)) return { url: "stub:" + specifier, shortCircuit: true };
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url.startsWith("stub:")) return { format: "module", source: STUBS[url.slice(5)], shortCircuit: true };
    return nextLoad(url, context);
  },
});

// Track the intervals the plugin starts.
const activeIntervals = new Set();
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;
globalThis.setInterval = (fn, ms, ...rest) => {
  const handle = realSetInterval(fn, ms, ...rest);
  activeIntervals.add(handle);
  return handle;
};
globalThis.clearInterval = (handle) => {
  activeIntervals.delete(handle);
  return realClearInterval(handle);
};

const tui = (await import(src("tui.js"))).default;
const { createSignal } = await import("solid-js");

const pluginLog = path.join(tmp, "plugin-calls.log");
const hangPid = path.join(tmp, "plugin-hang.pid");
const pluginPython = fakePython("plugin-python", `
sid=""
for a in "$@"; do case "$a" in --session=*) sid="\${a#--session=}";; esac; done
echo "$sid" >> "${pluginLog}"
case "$sid" in
  ses_slow) sleep 0.8; echo '{"version":1,"root":{"agent":"orchestrator"},"agents":[{"name":"orchestrator","group":"hub","busy":0,"parent":"","sessions":[]},{"name":"slowagent","group":"build","busy":0,"parent":"","sessions":[]}],"graph":null,"warnings":[]}';;
  ses_err) echo '{"version":1,"error":"no db"}'; exit 1;;
  ses_hang) echo $$ > "${hangPid}"; exec sleep 30;;
  *) printf '{"version":1,"root":{"agent":"orchestrator"},"agents":[{"name":"orchestrator","group":"hub","busy":1,"parent":"","sessions":[]},{"name":"w-%s","group":"build","busy":0,"parent":"","sessions":[]}],"graph":null,"warnings":[]}\\n' "$sid";;
esac`);

function nodeText(node) {
  if (typeof node === "string") return node;
  if (!node || typeof node !== "object") return "";
  return node.children.map(nodeText).join("");
}
const screen = (rootNode) => rootNode.children.map(nodeText);

function makeHost(options = {}) {
  const claims = [];
  const state = { claims, unslotted: 0, renders: 0 };
  const ctx = {
    options: Object.assign({ python: pluginPython, home: tmp, intervalMs: 1000 }, options),
    ui: {
      slot(claim) {
        claims.push(claim);
        return () => {
          state.unslotted += 1;
        };
      },
    },
    theme: { text: { default: "#eeeeee", muted: "#888888" }, warning: "#d08040", accent: "#40c060", error: "#e05050" },
    renderer: { requestRender: () => (state.renders += 1) },
    location: { directory: tmp },
  };
  return { ctx, state };
}

test("plugin: exports { id, setup } and imports only host-resolved modules", () => {
  assert.equal(tui.id, "agentgraph-tui");
  assert.equal(typeof tui.setup, "function");
  const bare = [...seenSpecifiers].filter((s) => !s.startsWith(".") && !s.startsWith("node:"));
  assert.deepEqual(bare.sort(), ["@opentui/solid", "solid-js"]);
});

test("plugin: one slot, replace by default, prepend by option", () => {
  const a = makeHost();
  const stopA = tui.setup(a.ctx);
  assert.equal(a.state.claims.length, 1);
  assert.deepEqual(Object.keys(a.state.claims[0]).sort(), ["render", "replace"]);
  assert.equal(a.state.claims[0].replace, "sidebar.content");
  stopA();
  const b = makeHost({ mode: "prepend" });
  const stopB = tui.setup(b.ctx);
  assert.equal(b.state.claims.length, 1);
  assert.deepEqual(Object.keys(b.state.claims[0]).sort(), ["prepend", "render"]);
  assert.equal(b.state.claims[0].prepend, "sidebar.content");
  stopB();
  assert.equal(a.state.unslotted + b.state.unslotted, 2);
});

test("plugin: renders normal, error and missing-session documents; follows session changes; drops late results", async () => {
  fs.writeFileSync(pluginLog, "");
  const { ctx, state } = makeHost();
  const before = new Set(activeIntervals);
  const stop = tui.setup(ctx);
  const [sid, setSid] = createSignal("ses_a");
  const props = { get sessionID() { return sid(); } };
  const view = state.claims[0].render(props);
  try {
    assert.deepEqual(screen(view), ["Loading"]);
    await waitFor(() => screen(view).includes("○ w-ses_a"), 5000, "first snapshot");
    assert.match(screen(view)[0], /^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] orchestrator$/);
    // the hub works: the spinner advances
    const first = screen(view)[0][0];
    await waitFor(() => screen(view)[0][0] !== first, 2000, "spinner frame");
    // colours come from the theme; the accent is never the green one
    const hubMark = view.children[0].children[0];
    assert.equal(hubMark.props.fg, "#d08040");
    assert.ok(state.renders > 0);

    // error document: one line
    setSid("ses_err");
    assert.deepEqual(screen(view), ["Loading"]);
    await waitFor(() => screen(view)[0] === "no db", 5000, "error line");
    assert.deepEqual(screen(view), ["no db"]);
    assert.equal(view.children[0].children[0].props.fg, "#e05050");

    // a slow session, then a fast one: the slow result must be ignored
    setSid("ses_slow");
    assert.deepEqual(screen(view), ["Loading"]);
    await waitFor(() => fs.readFileSync(pluginLog, "utf8").includes("ses_slow"), 3000, "slow fetch started");
    setSid("ses_b");
    await waitFor(() => screen(view).includes("○ w-ses_b"), 5000, "ses_b snapshot");
    await sleep(1200); // the slow process has finished by now
    assert.ok(fs.readFileSync(pluginLog, "utf8").includes("ses_slow"), "slow session was fetched");
    assert.ok(!screen(view).some((l) => l.includes("slowagent")), "late result was drawn: " + screen(view).join(" | "));
    assert.ok(screen(view).includes("○ w-ses_b"));

    // missing session
    setSid(undefined);
    assert.deepEqual(screen(view), ["No session"]);

    // a throwing props getter is drawn as text, not thrown
    const bad = state.claims[0].render({ get sessionID() { throw new Error("boom"); } });
    assert.deepEqual(screen(bad), ["No session"]);
    assert.doesNotThrow(() => state.claims[0].render(undefined));
  } finally {
    stop();
  }
  for (const h of activeIntervals) assert.ok(before.has(h), "an interval survived cleanup");
});

test("plugin: polls on its interval", async () => {
  fs.writeFileSync(pluginLog, "");
  const { ctx, state } = makeHost({ intervalMs: 1000 });
  const stop = tui.setup(ctx);
  state.claims[0].render({ sessionID: "ses_poll" });
  try {
    await sleep(2300);
    const calls = fs.readFileSync(pluginLog, "utf8").trim().split("\n").filter((l) => l === "ses_poll").length;
    assert.ok(calls >= 3, "calls: " + calls);
  } finally {
    stop();
  }
});

test("plugin: cleanup stops timers, kills the child and removes the slot", async () => {
  fs.rmSync(hangPid, { force: true });
  fs.writeFileSync(pluginLog, "");
  const { ctx, state } = makeHost();
  const before = new Set(activeIntervals);
  const stop = tui.setup(ctx);
  assert.ok([...activeIntervals].some((h) => !before.has(h)), "poll timer started");
  state.claims[0].render({ sessionID: "ses_hang" });
  await waitFor(() => fs.existsSync(hangPid) && fs.readFileSync(hangPid, "utf8").trim(), 3000, "child started");
  const pid = Number(fs.readFileSync(hangPid, "utf8"));
  assert.ok(alive(pid));
  stop();
  stop(); // twice is harmless
  for (const h of activeIntervals) assert.ok(before.has(h), "an interval survived cleanup");
  assert.equal(state.unslotted, 1);
  await waitFor(() => !alive(pid), 2000, "child killed");
  const calls = fs.readFileSync(pluginLog, "utf8").trim().split("\n").length;
  await sleep(1300);
  assert.equal(fs.readFileSync(pluginLog, "utf8").trim().split("\n").length, calls, "fetched after cleanup");
});

test("plugin: a host without ui.slot, theme or renderer does not throw", () => {
  const stop = tui.setup({ options: { python: pluginPython, home: tmp } });
  assert.equal(typeof stop, "function");
  stop();
  assert.doesNotThrow(() => tui.setup(undefined)());
});

// ---------------------------------------------------------------------------------------------------------------

let failed = 0;
for (const t of tests) {
  try {
    await t.fn();
    console.log("ok   " + t.name);
  } catch (error) {
    failed += 1;
    console.log("FAIL " + t.name);
    console.log("     " + String(error && error.stack ? error.stack : error).split("\n").join("\n     "));
  }
}
globalThis.setInterval = realSetInterval;
globalThis.clearInterval = realClearInterval;
console.log(`\n${tests.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
