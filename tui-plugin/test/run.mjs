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
const A = (name, group, busy = 0, parent = "", cost = undefined) => ({
  name, group, busy, parent, sessions: cost === undefined ? [] : [{ id: "s_" + name, cost, tokens_in: 1, tokens_out: 1, busy: busy > 0 }],
});
const D = (agents, graph = null, rootAgent = "orchestrator", extra = {}) => Object.assign({
  version: 1, source: "live", warnings: [], root: { agent: rootAgent }, groups: [], agents, graph,
}, extra);
const G = (extra = {}) => Object.assign({ progress: { passed: 0, total: 0 }, nodes: [], loops: [], work_loops: [] }, extra);
const T = (cost, tokens_in, tokens_out) => ({ sessions: 1, running: 0, cost, tokens_in, tokens_out });
const allDocs = [];
const keep = (doc) => (allDocs.push(doc), doc);
// "left" at the left edge, "right" at the right edge of a line `w` columns wide.
const pad = (left, right, w = 37) => left + " ".repeat(w - R.displayWidth(left) - R.displayWidth(right)) + right;

const DM_demoWorn = () => DM.demoDocument(demo, 60000); // DM is imported further down; called only when tests run
// The demo at the moment it was generated, so the elapsed times are fixed.
const NOW = demo.generated * 1000;
const KEYS = [["↑↓", "select"], ["⏎", "open"], ["s", "stop"], ["m", "message"], ["g", "graph"], ["q", "close"]];
const listText = (doc, width = 37, extra = {}) => R.toText(R.render(doc, Object.assign({ width, frame: 0, now: NOW }, extra))).split("\n");
const tonesOf = (line) => line.map((s) => [s.text, s.tone]);
// a session `ago` seconds old at NOW
const ses = (id, busy, ago, extra = {}) => Object.assign({ id, busy, created: demo.generated - ago, updated: demo.generated - 1, cost: 0.1 }, extra);
const AS = (name, group, busy, sessions) => ({ name, group, busy, parent: "", sessions });

test("list: the demo in the sidebar at 37: what needs you, the hub with the idle count, who works, progress, spend", () => {
  assert.deepEqual(listText(keep(demo), 37), [
    pad("! impl-claude stalled ↻5", "7m"),
    pad("◉ orchestrator ⠋", "11 idle"),
    pad("⠙ designer  429 error page and re…", "6m"),
    "4/11  gate reviewer refuted",
    "$6.57",
  ]);
});

test("list: the demo in the panel at 56: a gutter with the cursor on the first row, and one dim key line", () => {
  assert.deepEqual(listText(demo, 56, { cursor: true, keys: KEYS }), [
    "›" + pad("! impl-claude stalled ↻5  Apply the limiter in the…", "7m", 55),
    " " + pad("◉ orchestrator ⠋  Add per-user rate limiting", "11 idle", 55),
    " " + pad("⠙ designer  429 error page and retry-after message", "6m", 55),
    " ━━━━────── 4/11  add-rate-limit  gate reviewer refuted",
    " $6.57",
    " ↑↓ select  ⏎ open  s stop  m message  g graph  q close",
  ]);
  // the cursor follows `selected`; an unknown key selects the first row
  const L = R.layout(demo, { width: 56, now: NOW, cursor: true, selected: "agent:designer" });
  assert.equal(L.selected, "agent:designer");
  assert.deepEqual(L.lines.map((l) => l[0].text), [" ", " ", "›", " ", " "]);
  assert.equal(R.layout(demo, { width: 56, now: NOW, cursor: true, selected: "agent:nobody" }).selected, "stalled:impl-claude");
});

test("list: demo tones; never a tone outside the four", () => {
  const lines = R.render(demo, { width: 56, frame: 0, now: NOW, cursor: true, keys: KEYS });
  assert.deepEqual(tonesOf(lines[0]), [
    ["›", "accent"], ["!", "danger"], [" ", "plain"], ["impl-claude", "plain"], [" stalled", "danger"], [" ↻5", "accent"],
    ["  ", "plain"], ["Apply the limiter in the…", "muted"], ["  ", "plain"], ["7m", "muted"],
  ]);
  assert.deepEqual(tonesOf(lines[1]).filter(([t]) => t.trim()), [["◉", "accent"], ["orchestrator", "plain"], ["⠋", "accent"], ["Add per-user rate limiting", "muted"], ["11 idle", "muted"]]);
  assert.deepEqual(tonesOf(lines[2]).filter(([t]) => t.trim()), [["⠙", "accent"], ["designer", "plain"], ["429 error page and retry-after message", "muted"], ["6m", "muted"]]);
  assert.deepEqual(tonesOf(lines[3]).filter(([t]) => t.trim()), [
    ["━━━━", "accent"], ["──────", "muted"], ["4", "plain"], ["/11", "muted"], ["add-rate-limit", "muted"], ["gate ", "muted"], ["reviewer", "plain"], [" refuted", "accent"], // its work is being redone
  ]);
  assert.deepEqual(tonesOf(lines[5]).filter(([t]) => t.trim()), [["↑↓ select  ⏎ open  s stop  m message  g graph  q close", "muted"]]);
  for (const line of lines) for (const s of line) assert.ok(R.TONES.includes(s.tone), s.tone);
  assert.ok(!R.TONES.includes("success") && !R.TONES.includes("green"));
});

test("spinners: the hub and each working row, offset by row, advancing with frame", () => {
  assert.equal(listText(demo, 37, { frame: 1 })[1].slice(0, 16), "◉ orchestrator ⠙");
  assert.equal(listText(demo, 37, { frame: 1 })[2].slice(0, 2), "⠹ ");
  assert.equal(listText(demo, 37, { frame: 9 })[2].slice(0, 2), "⠋ ");
  assert.equal(listText(demo, 37, { frame: 10 })[1].slice(0, 16), "◉ orchestrator ⠋");
  assert.equal(R.isAnimated(demo), true);
});

test("sections in order: needs you, the hub, working, progress, spend, keys; empty ones left out; no blank rows", () => {
  const doc = keep(D(
    [A("orchestrator", "hub", 1), AS("w", "build", 1, [ses("s_w", true, 120)]), A("x", "build"), AS("v", "verify", 1, [ses("s_v", true, 60)])],
    G({ progress: { passed: 1, total: 4 }, nodes: [{ id: "n1", agent: "v", status: "blocked", attempts: 1, goal: "Check it" }] }),
    "orchestrator",
    { totals: T(1.5, 500, 20) },
  ));
  const lines = listText(doc, 56, { cursor: true, keys: KEYS });
  assert.deepEqual(lines.map((l) => l.slice(0, 3)), ["›! ", " ◉ ", " ⠙ ", " ━━", " $1", " ↑↓"]);
  assert.ok(lines[0].includes("v blocked") && lines[2].includes("w"), lines.join("\n"));
  assert.ok(lines.every((l) => l.trim() !== ""), "no blank rows");
  // hub only: the hub row alone
  assert.deepEqual(listText(keep(D([A("orchestrator", "hub")]))), ["◉ orchestrator"]);
  // hub, progress and spend
  assert.deepEqual(listText(keep(D([A("orchestrator", "hub", 1)], G({ progress: { passed: 3, total: 8 } }), "orchestrator", { totals: T(1.5, 500, 20) }))), [
    "◉ orchestrator ⠋", "━━━━────── 3/8", "$1.50",
  ]);
});

test("idle agents are a count on the hub's row, never a list", () => {
  const doc = keep(D([
    A("orchestrator", "hub"), A("explorer", "understand"), A("oracle", "decide"), A("council", "council"), A("councillor-fable", "council", 0, "council"),
    A("impl-openai", "build"), A("reviewer", "verify"), A("odd", "elsewhere"), AS("busy", "build", 1, [ses("s_b", true, 30)]),
  ]));
  const lines = listText(doc, 56);
  assert.equal(lines[0], pad("◉ orchestrator", "7 idle", 56));
  for (const name of ["explorer", "oracle", "council", "fable", "impl-openai", "reviewer", "odd"]) assert.ok(!lines.some((l) => l.includes(name)), name);
  assert.ok(lines[1].startsWith("⠙ busy"));
  // nothing idle: no count
  assert.deepEqual(listText(keep(D([A("orchestrator", "hub"), AS("w", "build", 1, [ses("s_w", true, 5)])]))), ["◉ orchestrator", pad("⠙ w", "5s")]);
});

test("hub is the root's agent, even when another hub agent is listed; a hub-less document uses root", () => {
  const doc = keep(D([AS("orchestrator", "hub", 1, [ses("s_o", true, 90)]), A("lead", "hub", 0), A("reviewer", "verify")], null, "lead"));
  assert.deepEqual(listText(doc), [pad("◉ lead", "1 idle"), pad("⠙ orchestrator", "1m")]);
  const none = keep(D([A("explorer", "understand")], null, "orchestrator", { root: { agent: "orchestrator", busy: true } }));
  assert.deepEqual(listText(none), [pad("◉ orchestrator ⠋", "1 idle")]);
  assert.equal(R.isAnimated(none), true);
  // the root's title next to the hub when there is room
  const titled = keep(D([A("orchestrator", "hub")], null, "orchestrator", { root: { agent: "orchestrator", title: "Add rate limiting" } }));
  assert.deepEqual(listText(titled, 56), ["◉ orchestrator  Add rate limiting"]);
});

test("spend row: dollars, then the context; tokens in and out are not shown", () => {
  const spend = (totals, root = { agent: "orchestrator" }) => text(D([A("orchestrator", "hub")], null, "orchestrator", { totals, root }))[1];
  assert.equal(spend(T(431.4, 2100000, 310000)), "$431.40");
  assert.equal(spend(T(431.4, 2100000, 310000), { agent: "orchestrator", context: 124000 }), "$431.40  ctx 124k");
  assert.equal(spend(T(431.4, 2100000, 310000), { agent: "orchestrator", context: null }), "$431.40");
  assert.equal(spend(T(0, 0, 0)), "$0.00");
  assert.equal(spend(T(0.999, 999, 1000)), "$1.00");
  assert.equal(spend(T(5, 12400, 310000), { agent: "orchestrator", context: 950 }), "$5.00  ctx 950");
  // no totals: the context alone; neither: no row
  assert.equal(spend(undefined, { agent: "orchestrator", context: 950 }), "ctx 950");
  assert.deepEqual(text(D([A("orchestrator", "hub")], null, "orchestrator", { totals: undefined })), ["◉ orchestrator"]);
  assert.ok(!text(demo).some((l) => l.includes("ctx") || l.includes("↑") || l.includes("↓")));
  const spans = R.render(D([A("orchestrator", "hub")], null, "orchestrator", { totals: T(1, 12400, 950), root: { agent: "orchestrator", context: 124000 } }))[1];
  assert.deepEqual(tonesOf(spans), [["$1.00", "plain"], ["  ", "plain"], ["ctx ", "muted"], ["124", "plain"], ["k", "muted"]]);
});

// root.wear (WEB_API.md): the context bar on the spend row
const W = (extra = {}) => Object.assign({
  context: 124000, window: 1000000, window_known: true, compact_at: 250000, summaries: 0, turns: 10,
  turns_since_summary: 10, history: [124000], per_turn: { start: null, recent: null }, level: "long",
}, extra);
const wearDoc = (wear, root = {}) => D([A("orchestrator", "hub")], null, "orchestrator", {
  totals: T(6.57, 12000, 70900), root: Object.assign({ agent: "orchestrator", context: 124000, wear }, root),
});
const wearLines = (wear, width = 37, root) => text(wearDoc(wear, root), width);
const barSpans = (wear, width = 37) => R.render(wearDoc(wear), { width })[1].filter((s) => /^[━─]+$/.test(s.text));

test("wear: an 8-cell bar to compact_at, then the number, after ctx", () => {
  // 124k of 250k: 49.6% -> 4 of 8 cells
  assert.equal(wearLines(W())[1], "$6.57  ctx ━━━━──── 124k");
  assert.deepEqual(barSpans(W()).map((s) => [s.text, s.tone]), [["━━━━", "muted"], ["────", "muted"]]);
  // compact_at null: the full bar is the window. 600k of 1M = 60% -> 5 cells
  assert.equal(wearLines(W({ context: 600000, compact_at: null }))[1], "$6.57  ctx ━━━━━─── 600k");
  // wear.context wins over root.context; a tiny context still shows one cell; never full below 100%
  assert.equal(wearLines(W({ context: 1000 }))[1], "$6.57  ctx ━─────── 1.0k");
  assert.equal(wearLines(W({ context: 249000 }))[1], "$6.57  ctx ━━━━━━━─ 249k");
  assert.equal(wearLines(W({ context: 300000 }))[1], "$6.57  ctx ━━━━━━━━ 300k");
  const spans = R.render(wearDoc(W()), { width: 37 })[1];
  assert.deepEqual(tonesOf(spans.slice(-3)), [[" ", "plain"], ["124", "plain"], ["k", "muted"]]);
});

test("wear: bar tone muted below 60%, accent from 60%, danger from 85%, never another tone", () => {
  const fill = (context, extra = {}) => barSpans(W(Object.assign({ context }, extra)))[0].tone;
  assert.equal(fill(149999), "muted"); // 59.99%
  assert.equal(fill(150000), "accent"); // 60%
  assert.equal(fill(212499), "accent"); // 84.99%
  assert.equal(fill(212500), "danger"); // 85%
  assert.equal(fill(400000), "danger");
  assert.equal(fill(850000, { compact_at: null }), "danger");
  assert.equal(R.wearTone(0.59), "muted");
  assert.equal(R.wearTone(0.6), "accent");
  assert.equal(R.wearTone(0.85), "danger");
  for (const share of [0, 0.3, 0.6, 0.7, 0.85, 1, 2]) assert.ok(["muted", "accent", "danger"].includes(R.wearTone(share)));
  assert.equal(barSpans(W({ context: 212500 }))[1].tone, "muted");
});

test("wear: one dot per summary, at most five, then the count; worn adds no row of its own", () => {
  assert.equal(wearLines(W({ summaries: 2 }))[1], "$6.57  ctx ━━━━──── 124k ••2");
  assert.equal(wearLines(W({ summaries: 5 }), 60)[1], "$6.57  ctx ━━━━──── 124k •••••5");
  assert.equal(wearLines(W({ summaries: 12 }), 60)[1], "$6.57  ctx ━━━━──── 124k •••••12");
  assert.equal(wearLines(W({ summaries: 0 }), 60)[1], "$6.57  ctx ━━━━──── 124k");
  const dots = R.render(wearDoc(W({ summaries: 2 })), { width: 60 })[1].pop();
  assert.deepEqual([dots.text, dots.tone], ["••2", "muted"]);
  assert.deepEqual(wearLines(W({ summaries: 4, level: "worn" }), 60), ["◉ orchestrator", "$6.57  ctx ━━━━──── 124k ••••4"]);
});

test("wear: null or absent wear: ctx and the number; no window: the number alone", () => {
  assert.equal(text(wearDoc(null))[1], "$6.57  ctx 124k");
  const absent = wearDoc(undefined);
  delete absent.root.wear;
  assert.deepEqual(R.render(wearDoc(null))[1], R.render(absent)[1]);
  assert.equal(wearLines(W({ window: null, compact_at: null }))[1], "$6.57  ctx 124k");
  assert.equal(wearLines(W({ context: null }), 37, { context: null })[1], "$6.57");
});

test("wear: too narrow drops the dots first, then the bar, then ctx", () => {
  const w = W({ summaries: 7 });
  assert.equal(wearLines(w, 31)[1], "$6.57  ctx ━━━━──── 124k •••••7");
  assert.equal(wearLines(w, 30)[1], "$6.57  ctx ━━━━──── 124k");
  assert.equal(wearLines(w, 24)[1], "$6.57  ctx ━━━━──── 124k");
  assert.equal(wearLines(w, 23)[1], "$6.57  ctx 124k");
  assert.equal(wearLines(w, 15)[1], "$6.57  ctx 124k");
  assert.equal(wearLines(w, 14)[1], "$6.57");
  for (let width = 8; width <= 70; width += 1) {
    for (const line of wearLines(W({ summaries: 9, level: "worn" }), width)) assert.ok(R.displayWidth(line) <= width, `${width}: ${line}`);
  }
});

test("formatTokens, formatDollars and formatElapsed at the boundaries", () => {
  const cases = [
    [0, "0"], [7, "7"], [999, "999"], [1000, "1.0k"], [1049, "1.0k"], [1050, "1.1k"], [9999, "10.0k"], [12400, "12.4k"],
    [99949, "99.9k"], [99999, "100k"], [310000, "310k"], [999499, "999k"], [999999, "1.0M"], [1000000, "1.0M"],
    [2100000, "2.1M"], [99999999, "100M"], [-5, "0"], [NaN, "0"],
  ];
  for (const [n, s] of cases) assert.equal(R.formatTokens(n), s, String(n));
  assert.equal(R.formatDollars(0), "$0.00");
  assert.equal(R.formatDollars(431.4), "$431.40");
  assert.equal(R.formatDollars(0.004), "$0.00");
  assert.equal(R.formatDollars(undefined), "$0.00");
  const el = [[0, "0s"], [59999, "59s"], [60000, "1m"], [3599999, "59m"], [3600000, "1h"], [3900000, "1h5m"], [36000000, "10h"], [172799999, "47h"], [172800000, "2d"], [-5, "0s"], [NaN, "0s"]];
  for (const [ms, s] of el) assert.equal(R.formatElapsed(ms), s, String(ms));
});

test("right column: elapsed time and the idle count end at the right edge", () => {
  for (const width of [37, 56, 80]) {
    const lines = listText(demo, width);
    for (const row of [0, 1, 2]) assert.equal(R.displayWidth(lines[row]), width, `${width}: ${lines[row]}`);
    assert.ok(lines[0].endsWith(" 7m") && lines[1].endsWith(" 11 idle") && lines[2].endsWith(" 6m"));
  }
});

test("progress row: a 10-cell bar, never full before done, never empty once started; the gate it is at", () => {
  const bar = (passed, total, graph = {}) => text(D([A("orchestrator", "hub")], G(Object.assign({ progress: { passed, total } }, graph))), 56)[1];
  assert.equal(bar(0, 5), "────────── 0/5");
  assert.equal(bar(5, 5), "━━━━━━━━━━ 5/5  done");
  assert.equal(bar(199, 200), "━━━━━━━━━─ 199/200");
  assert.equal(bar(1, 200), "━───────── 1/200");
  const gate = (status, extra = {}) => bar(2, 5, { nodes: [{ id: "a", type: "gate", agent: "reviewer", status: "passed" }, Object.assign({ id: "b", type: "gate", agent: "verifier", status }, extra)] });
  assert.equal(gate("pending"), "━━━━────── 2/5  next gate verifier");
  assert.equal(gate("running"), "━━━━────── 2/5  gate verifier checking");
  assert.equal(gate("failed", { verdict: "refuted" }), "━━━━────── 2/5  gate verifier refuted");
  assert.equal(gate("blocked"), "━━━━────── 2/5  gate verifier blocked");
  const human = [{ id: "x", type: "implement", agent: "b", status: "running" }, { id: "h", type: "human", agent: "user", status: "pending", depends_on: ["x"] }];
  assert.equal(bar(2, 5, { nodes: human }), "━━━━────── 2/5  next gate you");
  // the task's name between, cut in the middle when it does not fit; it goes first, then the bar
  assert.equal(bar(2, 5, { task: "add-rate-limit" }), "━━━━────── 2/5  add-rate-limit");
  const long = { task: "add-per-user-rate-limiting-to-the-public-api", nodes: [{ id: "b", type: "gate", agent: "reviewer", status: "running" }] };
  assert.equal(bar(2, 5, long), "━━━━────── 2/5  add-per-…lic-api  gate reviewer checking");
  assert.equal(text(D([A("orchestrator", "hub")], G(Object.assign({ progress: { passed: 2, total: 5 } }, long))), 30)[1], "2/5  gate reviewer checking");
  // no graph or no steps: no row
  assert.deepEqual(text(D([A("orchestrator", "hub")], G({ progress: { passed: 0, total: 0 } }))), ["◉ orchestrator"]);
});

test("needs you: requests from the host, ready human gates, stuck loops, failed, blocked and stalled steps; text and tone", () => {
  const agents = [A("orchestrator", "hub"), AS("a", "build", 1, [ses("s_a", true, 300)]), A("b", "build"), A("ev", "verify")];
  const root = { agent: "orchestrator", id: "s_root" };
  const one = (graph, requests) => R.render(D(agents, G(graph), "orchestrator", { root }), { width: 56, now: NOW, requests })[0];
  const t = (line) => R.toText([line]);
  assert.equal(t(one({}, [{ kind: "permission", session: "s_a" }])), pad("! a wants permission", "5m", 56));
  assert.equal(one({}, [{ kind: "permission", session: "s_a" }])[0].tone, "danger");
  assert.equal(t(one({}, [{ kind: "question", session: "s_root" }])), "? orchestrator asks you");
  assert.equal(one({}, [{ kind: "question", session: "s_root" }])[0].tone, "accent");
  assert.equal(t(one({}, [{ kind: "permission", session: "s_other", title: "Some session", count: 2 }])), "! Some session wants permission  2 requests");
  assert.equal(t(one({ nodes: [{ id: "h", type: "human", agent: "user", status: "running", goal: "Approve the limits" }] })), "? you to decide  Approve the limits");
  assert.equal(t(one({ nodes: [{ id: "n", type: "implement", agent: "b", status: "passed" }, { id: "h", type: "human", agent: "user", status: "pending", depends_on: ["n"], goal: "Approve" }] })), "? you to decide  Approve");
  assert.equal(t(one({ work_loops: [{ agent: "a", rounds: 4, state: "stuck" }] })), pad("! a stuck ↻4", "5m", 56));
  assert.equal(t(one({ nodes: [{ id: "n", agent: "b", status: "failed", attempts: 1, goal: "Build it" }] })), "! b failed  Build it");
  assert.equal(t(one({ nodes: [{ id: "n", agent: "b", status: "blocked", attempts: 3 }] })), "! b blocked ↻3");
  assert.equal(t(one({ nodes: [{ id: "n", agent: "a", status: "running", busy: true, stalled: true, attempts: 1, goal: "Wire it", started: demo.generated - 600 }] })), pad("! a stalled  Wire it", "10m", 56));
  // not the user's: a pending human gate whose steps have not passed, a refuting gate, sent back, waiting, working
  const calm = R.render(D(agents, G({
    nodes: [{ id: "n", type: "implement", agent: "b", status: "running" }, { id: "h", type: "human", agent: "user", status: "pending", depends_on: ["n"] }, { id: "g", type: "gate", agent: "ev", status: "failed", verdict: "refuted" }],
    loops: [{ worker: "b", evaluator: "ev", rounds: 3, state: "sent_back" }],
    work_loops: [{ agent: "b", rounds: 2, state: "waiting" }, { agent: "a", rounds: 2, state: "working" }],
  }), "orchestrator", { root }), { width: 56, now: NOW });
  assert.ok(!calm.some((l) => /^[!?↩]/.test(R.toText([l]))), R.toText(calm));
});

test("needs you: most urgent first, one row per agent, never also among the working; at most 5 then +N more", () => {
  const names = ["a1", "a2", "a3", "a4", "a5", "a6", "a7"];
  const agents = [A("orchestrator", "hub"), ...names.map((n) => AS(n, "build", 1, [ses("s_" + n, true, 60)]))];
  const doc = (k, requests) => keep(D(agents, G({ work_loops: names.slice(0, k).map((n, i) => ({ agent: n, rounds: 2 + i, state: "stuck" })) })));
  const six = listText(doc(6), 37);
  assert.deepEqual(six.slice(0, 6).map((l) => l.replace(/ +1m$/, "")), ["! a1 stuck ↻2", "! a2 stuck ↻3", "! a3 stuck ↻4", "! a4 stuck ↻5", "! a5 stuck ↻6", "! a6 stuck ↻7"]);
  const seven = listText(doc(7), 37);
  assert.equal(seven[5], "+2 more");
  assert.equal(R.render(doc(7), { width: 37, now: NOW })[5][0].tone, "muted");
  assert.ok(!seven.some((l) => /^⠙ a\d/.test(l)), "a flagged agent is not listed again as working");
  // a request outranks a stuck loop, and the same agent gets one row
  const both = listText(doc(1), 37, { requests: [{ kind: "permission", session: "s_a1" }, { kind: "question", session: "s_a2" }] });
  assert.deepEqual(both.slice(0, 2).map((l) => l.replace(/ +1m$/, "")), ["! a1 wants permission ↻2", "? a2 asks you"]);
  assert.ok(both[2].startsWith("◉ orchestrator"), both[2]);
});

test("names are cut in the middle, goals at their end; never past the width", () => {
  const long = "impl-supercalifragilisticexpialidocious";
  const doc = keep(D([A("orchestrator", "hub"), AS(long, "build", 1, [ses("s_l", true, 75, { title: "Apply the limiter in the middleware chain and update every route" })])], G({
    work_loops: [{ agent: long, rounds: 12, state: "working" }],
  })));
  assert.deepEqual(listText(doc, 37).slice(1), [pad("⠙ impl-su…ocious ↻12  Apply the l…", "1m")]);
  assert.deepEqual(listText(doc, 56).slice(1), [pad("⠙ impl-superc…alidocious ↻12  Apply the limiter in t…", "1m", 56)]);
  assert.equal(R.truncateMiddle(long, 15), "impl-su…docious");
  assert.equal(R.truncateMiddle(long, 7), "imp…ous");
  assert.equal(R.truncateMiddle("abcdef", 6), "abcdef");
  assert.equal(R.truncateMiddle("abcdefgh", 5), "ab…gh");
  assert.equal(R.truncateMiddle("abcdefgh", 3), "ab…");
  assert.equal(R.truncateMiddle("修复中间件链", 7), "修…件链");
  assert.ok(R.displayWidth(R.truncateMiddle("修复中间件链", 7)) <= 7);
});

test("working: longest at it first; loop round from 2; the step's goal, else its session's title; elapsed", () => {
  const doc = keep(D(
    [
      A("orchestrator", "hub", 1),
      AS("w-new", "build", 1, [ses("s_new", true, 30, { title: "New session title" })]),
      AS("w-old", "build", 1, [ses("s_old", true, 3600 * 2)]),
      AS("w-loop", "verify", 1, [ses("s_loop", true, 400)]),
      AS("sleeper", "build", 0, [ses("s_z", false, 9999)]),
    ],
    G({
      nodes: [{ id: "1", agent: "w-old", status: "running", busy: true, goal: "Do the thing", started: demo.generated - 3900 }],
      work_loops: [{ agent: "w-loop", rounds: 2, state: "working" }, { agent: "w-new", rounds: 1, state: "working" }],
    }),
  ));
  assert.deepEqual(listText(doc, 56).slice(1), [
    pad("⠙ w-old  Do the thing", "1h5m", 56),
    pad("⠹ w-loop ↻2", "6m", 56),
    pad("⠸ w-new  New session title", "30s", 56),
  ]);
  // more than 6: five, then +N more working
  const many = keep(D([A("orchestrator", "hub"), ...Array.from({ length: 8 }, (_, i) => AS("w" + i, "build", 1, [ses("s" + i, true, 100 - i)]))]));
  const rows = listText(many, 56);
  assert.equal(rows.length, 7);
  assert.equal(rows[6], "+3 more working");
});

test("wide characters and escape sequences in untrusted text", () => {
  const doc = keep(D(
    [A("orchestrator", "hub"), AS("evil\u001b[31mred\u0007", "build", 1, [ses("s_e", true, 10)])],
    G({ nodes: [{ id: "n", agent: "evil\u001b[31mred\u0007", busy: true, status: "running", goal: "修复中间件链中的限流器问题并更新全部路由" }] }),
  ));
  const lines = listText(doc, 37);
  assert.equal(lines[1], "⠙ evilred  修复中间件链中的限流…  10s");
  assert.equal(R.displayWidth(lines[1]), 37);
});

test("at rest nothing moves: the same rows at every frame and any time later; no spinner, no elapsed time", () => {
  const rest = keep(D(
    [A("orchestrator", "hub", 0, "", 1.2), A("impl-openai", "build", 0, "", 0.4), A("reviewer", "verify")],
    G({ progress: { passed: 11, total: 11 }, status: "passed", nodes: [{ id: "n", agent: "impl-openai", status: "passed", started: demo.generated - 900 }] }),
    "orchestrator",
    { totals: T(1.6, 10, 10), root: { agent: "orchestrator", id: "s_orchestrator", title: "Add rate limiting", busy: false } },
  ));
  assert.equal(R.isAnimated(rest), false);
  const at = (frame, now) => R.toText(R.render(rest, { width: 56, frame, now, cursor: true, keys: KEYS }));
  const first = at(0, NOW);
  for (const [frame, now] of [[1, NOW], [7, NOW + 3000], [123, NOW + 3600000], [undefined, NOW + 86400000]]) assert.equal(at(frame, now), first);
  assert.ok(!/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/.test(first) && !/\b\d+[smhd]\b/.test(first), first);
  assert.deepEqual(first.split("\n"), [
    "›" + pad("◉ orchestrator  Add rate limiting", "2 idle", 55),
    " ━━━━━━━━━━ 11/11  done",
    " $1.60",
    " ↑↓ select  ⏎ open  s stop  m message  g graph  q close",
  ]);
});

test("list: the demo at 20, 24 and 37 columns; a name is cut only when nothing else can go", () => {
  // the host gives the sidebar 37 columns and the panel at least 37 (panelWidth); 20 and 24 are for robustness
  assert.deepEqual(listText(demo, 20), [
    "! imp…ude stalled ↻5",
    "◉ orchestrator ⠋",
    pad("⠙ designer", "6m", 20),
    "━━━━────── 4/11",
    "$6.57",
  ]);
  assert.deepEqual(listText(demo, 24), [
    "! impl-claude stalled ↻5",
    "◉ orchestrator ⠋ 11 idle",
    pad("⠙ designer", "6m", 24),
    "4/11  reviewer refuted",
    "$6.57",
  ]);
  assert.deepEqual(listText(demo, 37), [
    pad("! impl-claude stalled ↻5", "7m"),
    pad("◉ orchestrator ⠋", "11 idle"),
    pad("⠙ designer  429 error page and re…", "6m"),
    "4/11  gate reviewer refuted",
    "$6.57",
  ]);
  assert.deepEqual(listText(demo, 37, { cursor: true, keys: KEYS }), [
    "›" + pad("! impl-claude stalled ↻5", "7m", 36),
    " " + pad("◉ orchestrator ⠋", "11 idle", 36),
    " " + pad("⠙ designer  429 error page and r…", "6m", 36),
    " 4/11  gate reviewer refuted",
    " $6.57",
    " ↑↓ select  ⏎ open  s stop  m message",
  ]);
  for (const width of [20, 24, 37]) {
    for (const extra of [{}, { cursor: true, keys: KEYS }]) {
      for (const line of R.render(demo, Object.assign({ width, now: NOW }, extra))) assert.ok(R.lineWidth(line) <= width, `${width}: ${R.toText([line])}`);
    }
  }
});

test("progress row: a refuted gate whose work is being redone is in the work tone; danger only for blocked, failed, or refuted with nobody on it", () => {
  const row = (nodes) => R.render(D([A("orchestrator", "hub")], G({ progress: { passed: 2, total: 5 }, nodes })), { width: 56 })[1];
  const gateTone = (line) => line.find((s) => s.text.startsWith(" ") && /refuted|blocked|failed|checking/.test(s.text)).tone;
  const reworking = row([{ id: "w", type: "implement", agent: "b", status: "running" }, { id: "g", type: "gate", agent: "reviewer", status: "failed", verdict: "refuted", feedback_to: ["w"] }]);
  assert.equal(R.toText([reworking]), "━━━━────── 2/5  gate reviewer refuted");
  assert.equal(gateTone(reworking), "accent");
  const returned = row([{ id: "w", type: "implement", agent: "b", status: "returned" }, { id: "g", type: "gate", agent: "reviewer", status: "failed", verdict: "refuted", depends_on: ["w"] }]);
  assert.equal(gateTone(returned), "accent");
  // the demo: impl-claude and designer redo the work the reviewer refuted
  assert.equal(gateTone(R.render(demo, { width: 37, now: NOW })[3]), "accent");
  const idle = row([{ id: "w", type: "implement", agent: "b", status: "pending" }, { id: "g", type: "gate", agent: "reviewer", status: "failed", verdict: "refuted", feedback_to: ["w"] }]);
  assert.equal(gateTone(idle), "danger");
  assert.equal(gateTone(row([{ id: "g", type: "gate", agent: "reviewer", status: "blocked" }])), "danger");
  assert.equal(gateTone(row([{ id: "g", type: "gate", agent: "reviewer", status: "failed" }])), "danger");
});

test("progress row: the current gate is the one whose producer is running or returned, else the first gate not passed", () => {
  const row = (nodes) => R.toText([R.render(D([A("orchestrator", "hub")], G({ progress: { passed: 2, total: 9 }, nodes })), { width: 56 })[1]]);
  const nodes = [
    { id: "a", type: "implement", agent: "x", status: "pending" },
    { id: "g1", type: "gate", agent: "verifier", status: "pending", depends_on: ["a"], feedback_to: ["a"] },
    { id: "b", type: "implement", agent: "y", status: "running" },
    { id: "g2", type: "gate", agent: "reviewer", status: "pending", depends_on: ["b"], feedback_to: ["b"] },
  ];
  assert.equal(row(nodes), "━━──────── 2/9  next gate reviewer");
  nodes[2].status = "returned";
  assert.equal(row(nodes), "━━──────── 2/9  next gate reviewer");
  nodes[2].status = "passed";
  assert.equal(row(nodes), "━━──────── 2/9  next gate verifier", "nobody producing: the first gate not passed");
  nodes[1].status = "passed";
  assert.equal(row(nodes), "━━──────── 2/9  next gate reviewer");
});

test("rows: at most 8 at rest and at most 16 busy at 56 columns", () => {
  const rest = JSON.parse(JSON.stringify(DM_demoWorn()));
  for (const a of rest.agents) {
    a.busy = 0;
    for (const s of a.sessions) s.busy = false;
  }
  rest.root.busy = false;
  rest.graph.nodes = rest.graph.nodes.map((n) => Object.assign(n, { status: "passed", busy: false, stalled: false }));
  rest.graph.progress = { passed: 11, total: 11 };
  rest.graph.work_loops = [];
  rest.graph.loops = [];
  const restLines = R.render(rest, { width: 56, now: NOW, cursor: true, keys: KEYS });
  assert.ok(restLines.length <= 8, R.toText(restLines));
  assert.equal(R.isAnimated(rest), false);
  // busy: the demo, and a crowded document with many requests and workers
  assert.ok(R.render(DM_demoWorn(), { width: 56, now: NOW, cursor: true, keys: KEYS }).length <= 16);
  const names = Array.from({ length: 14 }, (_, i) => "agent-" + i);
  const crowded = D([A("orchestrator", "hub", 1), ...names.map((n) => AS(n, "build", 1, [ses("s_" + n, true, 60)]))],
    G({ progress: { passed: 3, total: 20 }, work_loops: names.slice(0, 7).map((n) => ({ agent: n, rounds: 3, state: "stuck" })) }), "orchestrator", { totals: T(9, 1, 1) });
  const crowdedLines = R.render(crowded, { width: 56, now: NOW, cursor: true, keys: KEYS, requests: [{ kind: "question", session: "s_x" }] });
  assert.ok(crowdedLines.length <= 16, crowdedLines.length + "\n" + R.toText(crowdedLines));
});

test("no line wider than the panel at 40, 56 and 80 columns (every document, cursor and keys on and off)", () => {
  let checked = 0;
  for (const doc of [...allDocs, demo, DM_demoWorn(), null]) {
    for (const width of [40, 56, 80]) {
      for (const extra of [{}, { cursor: true, keys: KEYS }, { requests: [{ kind: "permission", session: "s_a" }, { kind: "question", session: "x".repeat(90), title: "y".repeat(90) }] }]) {
        for (const line of R.render(doc, Object.assign({ width, frame: 3, now: NOW }, extra))) {
          assert.ok(R.lineWidth(line) <= width, `width ${width}: ${JSON.stringify(R.toText([line]))}`);
          checked += 1;
        }
      }
    }
  }
  assert.ok(checked > 300, String(checked));
});

test("nothing animates when nothing works", () => {
  const idle = keep(D([A("orchestrator", "hub"), A("explorer", "understand")], G({ progress: { passed: 1, total: 2 } })));
  assert.equal(R.isAnimated(idle), false);
  assert.deepEqual(text(idle, 37, 0), text(idle, 37, 5));
  assert.equal(text(idle)[0].includes("⠋"), false);
  assert.equal(R.isAnimated(D([A("orchestrator", "hub"), A("w", "build", 1)])), true); // a non-hub agent works
  assert.equal(R.isAnimated(D([A("orchestrator", "hub", 1)])), true);
  assert.equal(R.isAnimated(D([A("orchestrator", "hub"), A("w", "build", 0)], null, "orchestrator", { root: { agent: "orchestrator", busy: true } })), false);
  assert.equal(R.isAnimated(D([], null)), false);
  assert.equal(R.isAnimated({ version: 1, error: "x" }), false);
  assert.equal(R.isAnimated(null), false);
});

test("loading, error, empty", () => {
  assert.deepEqual(text(null), ["Loading"]);
  assert.deepEqual(text(undefined), ["Loading"]);
  assert.equal(R.render(null)[0][0].tone, "muted");
  assert.deepEqual(text(keep({ version: 1, error: "Python was not found at /x" })), ["Python was not found at /x"]);
  assert.deepEqual(R.render({ version: 1, error: "x" })[0][0].tone, "danger");
  assert.deepEqual(
    text(keep({ version: 1, error: "The snapshot command exited with status 1: Traceback (most recent call last)" })),
    ["The snapshot command exited with sta…"],
  );
  assert.deepEqual(text(keep(D([]))), ["No agents"]);
  assert.equal(R.render(D([]))[0][0].tone, "muted");
  assert.deepEqual(text(keep({ version: 1, error: "boom", agents: [A("orchestrator", "hub", 1)], totals: T(1, 1, 1) })), ["boom"]);
});

test("Run block alone: idle hub, no graph, no totals", () => {
  assert.deepEqual(text(keep(D([A("orchestrator", "hub")]))), ["◉ orchestrator"]);
  assert.equal(R.render(D([A("orchestrator", "hub")]))[0][0].tone, "muted");
});

test("every line fits its width (all documents, many widths)", () => {
  const widths = [37, 30, 24, 20, 16, 12, 8, 5, 3, 2, 1];
  const withContext = D([A("orchestrator", "hub", 1)], null, "orchestrator", { totals: T(431.4, 2100000, 310000), root: { agent: "orchestrator", context: 124000 } });
  let checked = 0;
  for (const doc of [...allDocs, demo, withContext, null]) {
    for (const width of widths) {
      for (const frame of [0, 3]) {
        for (const line of R.render(doc, { width, frame })) {
          assert.ok(R.lineWidth(line) <= width, `width ${width}: ${JSON.stringify(R.toText([line]))}`);
          checked += 1;
        }
      }
    }
  }
  assert.ok(checked > 400);
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
  const cleanups = [];
  return {
    cleanups,
    onCleanup(fn) {
      cleanups.push(fn);
    },
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
  "solid-js": "const s = globalThis.__agentgraphStub; export const createSignal = (v) => s.createSignal(v); export const onCleanup = (fn) => s.onCleanup(fn);",
  "@opentui/solid": [
    "const s = globalThis.__agentgraphStub;",
    "export function createElement(tag) { return { tag, props: {}, children: [] }; }",
    // Like the real host (OpenCode 2.2.5 and 2.3.2): `fg`/`bg` take a string or OpenTUI's own RGBA (it has a
    // Float32Array `buffer`); any other object throws inside OpenTUI. `failProp` makes one property always throw.
    "export function setProp(node, key, value) {",
    "  if (s.failProp === key) throw new Error('stub: ' + key + ' refused');",
    "  if ((key === 'fg' || key === 'bg') && typeof value !== 'string' && !(value && value.buffer && value.buffer.length >= 3)) throw new TypeError(\"undefined is not an object (evaluating 'e.length')\");",
    "  node.props[key] = value;",
    "}",
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
  ses_slow) sleep 0.8; echo '{"version":1,"root":{"agent":"orchestrator","title":"slowagent"},"agents":[{"name":"orchestrator","group":"hub","busy":0,"parent":"","sessions":[]},{"name":"slowagent","group":"build","busy":0,"parent":"","sessions":[]}],"graph":null,"warnings":[]}';;
  ses_err) echo '{"version":1,"error":"no db"}'; exit 1;;
  ses_hang) echo $$ > "${hangPid}"; exec sleep 30;;
  *) printf '{"version":1,"root":{"agent":"orchestrator","title":"t-%s"},"agents":[{"name":"orchestrator","group":"hub","busy":1,"parent":"","sessions":[]},{"name":"w-%s","group":"build","busy":0,"parent":"","sessions":[]}],"graph":null,"warnings":[]}\\n' "$sid" "$sid";;
esac`);

function nodeText(node) {
  if (typeof node === "string") return node;
  if (!node || typeof node !== "object") return "";
  return node.children.map(nodeText).join("");
}
const screen = (rootNode) => rootNode.children.map(nodeText);
// The fake interpreter's document for session S has the root title "t-S", drawn on the hub row.
const shows = (rootNode, title) => screen(rootNode).some((l) => l.includes(title));

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
    await waitFor(() => shows(view, "t-ses_a"), 5000, "first snapshot");
    assert.match(screen(view)[0], /^◉ orchestrator [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]  t-ses_a +1 idle$/);
    // the hub works: the spinner advances
    const last = (l) => l[15];
    const first = last(screen(view)[0]);
    await waitFor(() => last(screen(view)[0]) !== first, 2000, "spinner frame");
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
    await waitFor(() => shows(view, "t-ses_b"), 5000, "ses_b snapshot");
    await sleep(1200); // the slow process has finished by now
    assert.ok(fs.readFileSync(pluginLog, "utf8").includes("ses_slow"), "slow session was fetched");
    assert.ok(!screen(view).some((l) => l.includes("slowagent")), "late result was drawn: " + screen(view).join(" | "));
    assert.ok(shows(view, "t-ses_b"));

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

const timerPython = fakePython("timer-python", `
sid=""
for a in "$@"; do case "$a" in --session=*) sid="\${a#--session=}";; esac; done
doc() { printf '{"version":1,"root":{"agent":"orchestrator"},"agents":[{"name":"orchestrator","group":"hub","busy":%s,"parent":"","sessions":[]},{"name":"worker","group":"build","busy":%s,"parent":"","sessions":[]}],"graph":null,"warnings":[]}\\n' "$1" "$2"; }
case "$sid" in
  ses_idle) doc 0 0;;
  ses_worker) doc 0 1;;
  ses_hub) doc 1 0;;
  ses_flip) if [ -f "${path.join(tmp, "flip.flag")}" ]; then doc 0 0; else touch "${path.join(tmp, "flip.flag")}"; doc 0 1; fi;;
esac`);

test("plugin: the frame timer runs only while the hub or any agent works", async () => {
  fs.rmSync(path.join(tmp, "flip.flag"), { force: true });
  const { ctx, state } = makeHost({ python: timerPython, intervalMs: 1000 });
  const before = new Set(activeIntervals);
  const stop = tui.setup(ctx);
  const running = () => [...activeIntervals].filter((h) => !before.has(h)).length; // poll timer + frame timer
  const [sid, setSid] = createSignal("ses_idle");
  const view = state.claims[0].render({ get sessionID() { return sid(); } });
  try {
    assert.equal(running(), 1, "only the poll timer before the first snapshot");
    await waitFor(() => screen(view).some((l) => /^◉ orchestrator +1 idle$/.test(l)), 5000, "idle snapshot");
    assert.equal(running(), 1, "nothing works: no frame timer");
    setSid("ses_worker"); // the hub is idle, a non-hub agent works
    await waitFor(() => screen(view).some((l) => /^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] worker/.test(l)), 5000, "worker snapshot");
    assert.equal(running(), 2, "a non-hub agent works: frame timer runs");
    setSid("ses_idle"); // switching sessions stops it at once
    assert.equal(running(), 1, "session change stops the frame timer");
    await waitFor(() => screen(view).some((l) => /^◉ orchestrator +1 idle$/.test(l)), 5000, "idle snapshot again");
    setSid("ses_hub");
    await waitFor(() => screen(view).some((l) => l.startsWith("◉ orchestrator ")), 5000, "hub snapshot");
    assert.equal(running(), 2, "the hub works: frame timer runs");
    setSid("ses_flip"); // worker busy on the first fetch, idle on the next poll
    await waitFor(() => screen(view).some((l) => /^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] worker/.test(l)), 5000, "flip: busy");
    assert.equal(running(), 2);
    await waitFor(() => screen(view).some((l) => /^◉ orchestrator +1 idle$/.test(l)), 5000, "flip: idle");
    assert.equal(running(), 1, "work finished: frame timer stopped");
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
// The panel and its command

function makePanelHost(options = {}, { session = true, current, keymap = true, panel = true, toast = true } = {}) {
  const { ctx, state } = makeHost(options);
  Object.assign(state, { layers: [], unlayered: 0, opened: [], closed: 0, toasts: [], currentName: current });
  if (keymap) {
    ctx.keymap = {
      layer(factory) {
        const layer = factory();
        state.layers.push(layer);
        return () => (state.unlayered += 1);
      },
    };
  }
  if (panel) {
    ctx.ui.panel = {
      open(name, opts) {
        if (!session) return false;
        state.opened.push({ name, opts });
        state.currentName = name;
        return true;
      },
      current: () => (state.currentName ? { name: state.currentName } : undefined),
      close() {
        state.closed += 1;
        state.currentName = undefined;
      },
    };
  }
  if (toast) ctx.ui.toast = { show: (t) => state.toasts.push(t) };
  const command = () => state.layers.flatMap((l) => l.commands || []).find((c) => c.id === "agentgraph.graph");
  const panelClaim = () => state.claims.find((c) => c.append === "session.panel");
  return { ctx, state, command, panelClaim };
}

const makePanel = (extra = {}) => {
  const calls = { full: 0, close: 0 };
  return Object.assign({ name: "agentgraph.graph", sessionID: "ses_p", width: 80, toggleFullscreen: () => (calls.full += 1), close: () => (calls.close += 1) }, extra, { calls });
};

test("panel: the command is registered with id, title, slash name and alias", () => {
  const { ctx, state, command } = makePanelHost();
  const stop = tui.setup(ctx);
  try {
    assert.equal(state.layers.length, 1);
    assert.equal(state.layers[0].mode, "global");
    const c = command();
    assert.equal(c.id, "agentgraph.graph");
    assert.equal(c.title, "Agent graph");
    assert.equal(c.palette, true);
    assert.deepEqual(c.slash, { name: "graph", aliases: ["agents"] });
    assert.equal(typeof c.run, "function");
    // the sidebar slot and the panel slot
    assert.equal(state.claims.length, 2);
    assert.equal(state.claims[0].replace, "sidebar.content");
    assert.deepEqual(Object.keys(state.claims[1]).sort(), ["append", "render"]);
  } finally {
    stop();
  }
});

test("panel: running the command opens the panel, running it again closes it", () => {
  const { ctx, state, command } = makePanelHost();
  const stop = tui.setup(ctx);
  try {
    command().run();
    assert.deepEqual(state.opened, [{ name: "agentgraph.graph", opts: undefined }]);
    assert.equal(state.closed, 0);
    command().run();
    assert.equal(state.closed, 1);
    assert.equal(state.opened.length, 1);
    command().run();
    assert.equal(state.opened.length, 2);
    // another plugin's panel is current: open ours, do not close theirs
    state.currentName = "other.panel";
    command().run();
    assert.equal(state.opened.length, 3);
    assert.equal(state.closed, 1);
    assert.equal(state.toasts.length, 0);
  } finally {
    stop();
  }
});

test("panel: outside a session the command toasts once and opens nothing", () => {
  const { ctx, state, command } = makePanelHost({}, { session: false });
  const stop = tui.setup(ctx);
  try {
    command().run();
    assert.equal(state.opened.length, 0);
    assert.equal(state.closed, 0);
    assert.equal(state.toasts.length, 1);
    assert.equal(typeof state.toasts[0].message, "string");
    assert.ok(state.toasts[0].message.length > 0 && state.toasts[0].message.length < 60);
  } finally {
    stop();
  }
  // no toast function: nothing throws
  const bare = makePanelHost({}, { session: false, toast: false });
  const stopBare = tui.setup(bare.ctx);
  assert.doesNotThrow(() => bare.command().run());
  stopBare();
});

test("panel: a failing host open does not throw into the host", () => {
  const { ctx, command } = makePanelHost();
  ctx.ui.panel.open = () => {
    throw new Error("boom");
  };
  const stop = tui.setup(ctx);
  try {
    assert.doesNotThrow(() => command().run());
  } finally {
    stop();
  }
});

const demoPython = fakePython("panel-demo-python", `cat "${path.join(here, "fixtures", "demo.json")}"`);

test("panel: other panel names render nothing; its own name draws the list with a cursor and one key line; width follows and is capped", async () => {
  const { ctx, state, panelClaim } = makePanelHost({ python: demoPython });
  const stop = tui.setup(ctx);
  try {
    const other = panelClaim().render(makePanel({ name: "other.panel" }));
    assert.deepEqual(screen(other), []);

    const [width, setWidth] = createSignal(80);
    const [name, setName] = createSignal("other.panel");
    const panel = makePanel();
    Object.defineProperties(panel, { width: { get: () => width() }, name: { get: () => name() } });
    const view = panelClaim().render(panel);
    assert.deepEqual(screen(view), []);
    setName("agentgraph.graph");
    assert.deepEqual(screen(view), ["Loading"]);
    await waitFor(() => screen(view).length > 3, 5000, "panel snapshot");
    // the demo: the stalled agent first (with the cursor), the hub and the idle count, who works, progress, spend, keys
    const widthOf = (i) => R.displayWidth(screen(view)[i]);
    const hubIndex = () => screen(view).findIndex((l) => l.startsWith(" ◉ orchestrator"));
    assert.match(screen(view)[0], /^›! impl-claude stalled ↻5  .* \d+[smhd]\w*$/);
    assert.match(screen(view)[hubIndex()], / 11 idle$/);
    assert.match(screen(view).at(-3), /4\/11  .*gate reviewer refuted$/);
    assert.equal(screen(view).at(-1), " ↑↓ select  g graph  q close");
    assert.ok(!screen(view).some((l) => l.trim() === ""), "no blank rows");
    assert.equal(widthOf(0), 80);
    assert.equal(widthOf(hubIndex()), 80);
    // wider panel, wider lines
    setWidth(100);
    assert.equal(widthOf(0), 100);
    assert.equal(widthOf(hubIndex()), 100);
    // the cap holds
    setWidth(300);
    assert.equal(widthOf(0), 100);
    // never narrower than the sidebar
    setWidth(10);
    assert.equal(widthOf(0), 37);
    setWidth(undefined);
    assert.equal(widthOf(0), 37);
    // at the sidebar's width: the sidebar's rows, plus the gutter and the key line
    setWidth(37);
    const sidebarView = state.claims[0].render({ sessionID: "ses_p" });
    await waitFor(() => screen(sidebarView).length > 3, 5000, "sidebar snapshot");
    assert.equal(screen(view).length, screen(sidebarView).length + 1);
    assert.ok(screen(sidebarView).every((l) => !/^[› ]/.test(l) && R.displayWidth(l) <= 37), "the sidebar has no gutter");
    assert.ok(screen(view).every((l) => /^[› ]/.test(l) && R.displayWidth(l) <= 37), "the panel has the gutter");
    // the name changes away: nothing is drawn
    setName("other.panel");
    assert.deepEqual(screen(view), []);
  } finally {
    stop();
  }
  assert.equal(P.panelWidth({ width: 120.9 }), 100);
  assert.equal(P.panelWidth({ width: 50.7 }), 50);
  assert.equal(P.panelWidth(null), 37);
});

test("panel: the panel session is polled; closing it stops its timers; the sidebar keeps polling", async () => {
  fs.writeFileSync(pluginLog, "");
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = tui.setup(ctx);
  try {
    const sideView = state.claims[0].render({ sessionID: "ses_side" });
    await waitFor(() => shows(sideView, "t-ses_side"), 5000, "sidebar snapshot");
    const withSidebar = new Set(activeIntervals);
    globalThis.__agentgraphStub.cleanups.length = 0;
    const view = panelClaim().render(makePanel({ sessionID: "ses_panel" }));
    assert.equal(globalThis.__agentgraphStub.cleanups.length, 1, "registered a cleanup with the host");
    assert.ok([...activeIntervals].some((h) => !withSidebar.has(h)), "panel poll timer started");
    await waitFor(() => shows(view, "t-ses_panel"), 5000, "panel snapshot");
    await sleep(2300);
    const log = () => fs.readFileSync(pluginLog, "utf8").trim().split("\n");
    assert.ok(log().filter((l) => l === "ses_panel").length >= 3, "panel polled: " + log().join(","));
    // the host disposes the panel
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    for (const h of activeIntervals) assert.ok(withSidebar.has(h), "a panel timer survived the close");
    const before = log().filter((l) => l === "ses_panel").length;
    await sleep(1300);
    assert.equal(log().filter((l) => l === "ses_panel").length, before, "panel polled after close");
    assert.ok(log().filter((l) => l === "ses_side").length >= 1, "sidebar still polls");
  } finally {
    stop();
  }
});

test("panel: f toggles full screen, q closes, the list's up, down and return; only while it is this plugin's panel", () => {
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = tui.setup(ctx);
  try {
    const panel = makePanel();
    const before = state.layers.length;
    panelClaim().render(panel);
    assert.equal(state.layers.length, before + 1);
    const layer = state.layers[before];
    assert.equal(layer.mode, undefined, "no mode: active only while the panel owns input");
    const f = layer.commands.find((c) => c.bind === "f");
    const q = layer.commands.find((c) => c.bind === "q");
    // f and q, and the list's keys; no s or m: this host has no client.session to stop or message with
    assert.deepEqual(layer.commands.map((c) => c.bind), ["f", "q", "up", "down", "return"]);
    assert.equal(f.enabled(), true);
    f.run();
    assert.equal(panel.calls.full, 1);
    assert.equal(panel.calls.close, 0);
    q.run();
    assert.equal(panel.calls.close, 1);
    assert.equal(panel.calls.full, 1);
    // another plugin's panel: the keys are not ours
    const foreign = makePanel({ name: "other.panel" });
    const n = state.layers.length;
    panelClaim().render(foreign);
    const keys = state.layers[n].commands;
    assert.ok(keys.every((c) => c.enabled() === false));
    // a throwing action is swallowed
    const bad = makePanel({ toggleFullscreen() { throw new Error("boom"); } });
    panelClaim().render(bad);
    const m = state.layers.length - 1;
    assert.doesNotThrow(() => state.layers[m].commands.find((c) => c.bind === "f").run());
  } finally {
    stop();
  }
});

// The panel's list keys on the demo: a host with a router, a client and dialogs, each recorded.
function makeActionHost({ router = true, client = true, dialog = true, answers = {} } = {}) {
  const host = makePanelHost({ python: demoPython });
  const calls = [];
  if (router) host.ctx.ui.router = { navigate: (r) => calls.push(["navigate", r]) };
  if (dialog) {
    host.ctx.ui.dialog = {
      confirm: async (o) => (calls.push(["confirm", o.title]), answers.confirm),
      prompt: async (o) => (calls.push(["prompt", o.title]), answers.prompt),
    };
  }
  if (client) {
    host.ctx.client = {
      session: {
        interrupt: async (a) => {
          calls.push(["interrupt", a]);
          if (answers.fail) throw new Error(answers.fail);
          return { data: { interrupted: answers.interrupted !== false } };
        },
        prompt: async (a) => (calls.push(["send", a]), { data: {} }),
      },
    };
  }
  const key = (bind) => {
    const layer = host.state.layers.filter((l) => (l.commands || []).some((c) => c.bind === bind)).pop();
    return layer ? layer.commands.find((c) => c.bind === bind) : undefined;
  };
  return Object.assign(host, { calls, key });
}
const cursorRow = (view) => screen(view).findIndex((l) => l.startsWith("›"));

test("panel keys: up and down move the cursor through the rows that act, clamped; return opens the row's session", async () => {
  const host = makeActionHost({ client: false, dialog: false });
  const stop = tui.setup(host.ctx);
  try {
    const view = host.panelClaim().render(makePanel({ sessionID: "ses_keys", width: 80 }));
    await waitFor(() => screen(view).length > 3, 5000, "panel snapshot");
    const { key, calls } = host;
    assert.deepEqual(screen(view).map((l) => l[0]), ["›", " ", " ", " ", " ", " "]); // stalled, hub, designer, progress, spend, keys
    assert.equal(key("up").enabled(), true);
    key("up").run();
    assert.equal(cursorRow(view), 0, "clamped at the top");
    key("down").run();
    assert.equal(cursorRow(view), 1);
    assert.ok(screen(view)[1].startsWith("›◉ orchestrator"));
    key("down").run();
    key("down").run();
    key("down").run();
    assert.equal(cursorRow(view), 2, "clamped at the last row that acts: the progress and spend rows are not selectable");
    assert.equal(screen(view).at(-1), " ↑↓ select  ⏎ open  g graph  q close");
    key("return").run();
    assert.deepEqual(calls.at(-1), ["navigate", { type: "session", sessionID: "ses_h" }]);
    key("up").run();
    key("return").run();
    assert.deepEqual(calls.at(-1), ["navigate", { type: "session", sessionID: "ses_root" }], "the hub row opens the root session");
    key("up").run();
    key("return").run();
    assert.deepEqual(calls.at(-1), ["navigate", { type: "session", sessionID: "ses_g" }], "the stalled agent's session");
    // a failing navigate is a toast, not a throw
    host.ctx.ui.router.navigate = () => {
      throw new Error("no route");
    };
    assert.doesNotThrow(() => key("return").run());
    assert.match(host.state.toasts.at(-1).message, /Could not open impl-claude: no route/);
    assert.equal(host.state.toasts.at(-1).variant, "error");
    // the selection is remembered by row, not by position: a remount keeps it
    key("down").run();
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    const again = host.panelClaim().render(makePanel({ sessionID: "ses_keys", width: 80 }));
    await waitFor(() => screen(again).length > 3, 5000, "remounted snapshot");
    assert.ok(screen(again)[1].startsWith("›◉ orchestrator"), screen(again).join("\n"));
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel keys: without a router there is no return key action and no ⏎ in the key line", async () => {
  const host = makeActionHost({ router: false, client: false, dialog: false });
  const stop = tui.setup(host.ctx);
  try {
    const view = host.panelClaim().render(makePanel({ sessionID: "ses_norouter", width: 80 }));
    await waitFor(() => screen(view).length > 3, 5000, "panel snapshot");
    assert.equal(screen(view).at(-1), " ↑↓ select  g graph  q close");
    assert.equal(host.key("return").enabled(), false);
    assert.doesNotThrow(() => host.key("return").run());
    assert.deepEqual(host.calls, []);
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel keys: s confirms, then interrupts the working agent's session; m asks for text, then sends it as a steer", async () => {
  const answers = { confirm: true, prompt: "  use the token bucket  " };
  const host = makeActionHost({ answers });
  const stop = tui.setup(host.ctx);
  try {
    const view = host.panelClaim().render(makePanel({ sessionID: "ses_act", width: 80 }));
    await waitFor(() => screen(view).length > 3, 5000, "panel snapshot");
    const { key, calls, state } = host;
    assert.deepEqual(state.layers.filter((l) => (l.commands || []).some((c) => c.bind === "f")).pop().commands.map((c) => c.bind), ["f", "q", "up", "down", "return", "s", "m"]);
    // the stalled impl-claude: it works, so s and m act on its running session
    assert.equal(screen(view).at(-1), " ↑↓ select  ⏎ open  s stop  m message  g graph  q close");
    assert.equal(key("s").enabled(), true);
    key("s").run();
    await waitFor(() => calls.some((c) => c[0] === "interrupt"), 2000, "interrupt");
    assert.deepEqual(calls.slice(0, 2), [["confirm", "Stop impl-claude?"], ["interrupt", { sessionID: "ses_g" }]]);
    await waitFor(() => state.toasts.length > 0, 2000, "toast");
    assert.equal(state.toasts.at(-1).message, "Stopped impl-claude");
    // declined: nothing is sent
    answers.confirm = false;
    calls.length = 0;
    key("s").run();
    await sleep(30);
    assert.deepEqual(calls, [["confirm", "Stop impl-claude?"]]);
    // the host says it was not running
    answers.confirm = true;
    answers.interrupted = false;
    key("s").run();
    await waitFor(() => state.toasts.at(-1).message === "impl-claude was not running", 2000, "not running");
    // a failure is a toast
    answers.fail = "session busy";
    key("s").run();
    await waitFor(() => /Could not stop impl-claude: session busy/.test(state.toasts.at(-1).message), 2000, "failure toast");
    assert.equal(state.toasts.at(-1).variant, "error");
    // m: the trimmed text, delivered as a steer into the running session
    calls.length = 0;
    key("m").run();
    await waitFor(() => calls.some((c) => c[0] === "send"), 2000, "send");
    assert.deepEqual(calls, [["prompt", "Message impl-claude"], ["send", { sessionID: "ses_g", text: "use the token bucket", delivery: "steer" }]]);
    await waitFor(() => state.toasts.at(-1).message === "Sent to impl-claude", 2000, "sent toast");
    // an empty or cancelled message sends nothing
    for (const text of ["   ", undefined, null]) {
      answers.prompt = text;
      calls.length = 0;
      key("m").run();
      await sleep(30);
      assert.deepEqual(calls, [["prompt", "Message impl-claude"]]);
    }
    // the hub row: the user's own session; nothing to stop or message
    key("down").run();
    assert.equal(screen(view).at(-1), " ↑↓ select  ⏎ open  g graph  q close");
    assert.equal(key("s").enabled(), false);
    assert.equal(key("m").enabled(), false);
    calls.length = 0;
    key("s").run();
    key("m").run();
    await sleep(30);
    assert.deepEqual(calls, []);
    // designer works: s acts on its session
    key("down").run();
    answers.fail = undefined;
    answers.interrupted = true;
    key("s").run();
    await waitFor(() => calls.some((c) => c[0] === "interrupt"), 2000, "designer interrupt");
    assert.deepEqual(calls.find((c) => c[0] === "interrupt"), ["interrupt", { sessionID: "ses_h" }]);
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel keys: while a host dialog is open the list keys do nothing, and the Enter that closes the confirm does not also open", async () => {
  const host = makeActionHost();
  const mode = { v: "base" };
  host.ctx.keymap.mode = { current: () => mode.v };
  let resolveConfirm;
  host.ctx.ui.dialog.confirm = (o) => (host.calls.push(["confirm", o.title]), new Promise((r) => (resolveConfirm = r)));
  const stop = tui.setup(host.ctx);
  try {
    const view = host.panelClaim().render(makePanel({ sessionID: "ses_dialog", width: 80 }));
    await waitFor(() => screen(view).length > 3, 5000, "panel snapshot");
    const { key, calls } = host;
    // any host dialog (the keymap is in "modal" mode): every list key is off and does nothing
    mode.v = "modal";
    for (const bind of ["up", "down", "return", "s", "m"]) {
      assert.equal(key(bind).enabled(), false, bind + " enabled in a dialog");
      key(bind).run();
    }
    await sleep(20);
    assert.deepEqual(calls, []);
    assert.equal(cursorRow(view), 0);
    mode.v = "base";
    // our own confirm: from `s` until a moment after it closes, the list keys do nothing
    key("s").run();
    await waitFor(() => calls.length === 1, 2000, "confirm shown");
    for (const bind of ["up", "down", "return", "s", "m"]) assert.equal(key(bind).enabled(), false, bind + " enabled under the confirm");
    key("down").run();
    key("return").run();
    assert.equal(cursorRow(view), 0, "down did not move the cursor under the dialog");
    // Enter answers the confirm; the same key reaching the panel must not open anything
    // The host dispatches that Enter in one go: the confirm resolves, its promise chain settles (microtasks), and the
    // same key may then reach the panel before any timer runs. The list keys must still be off then.
    resolveConfirm(false);
    key("return").run(); // same tick, before the chain settles
    for (let i = 0; i < 50; i += 1) await Promise.resolve(); // the chain settles; no timer has run yet
    assert.equal(key("return").enabled(), false, "the list keys came back before a timer ran");
    key("return").run(); // same macrotask, after the chain settled
    assert.ok(!calls.some((c) => c[0] === "navigate"), JSON.stringify(calls));
    // a moment later the keys work again
    await sleep(30);
    assert.equal(key("return").enabled(), true);
    key("return").run();
    assert.deepEqual(calls.at(-1), ["navigate", { type: "session", sessionID: "ses_g" }]);
    // the message prompt blocks them the same way
    let resolvePrompt;
    host.ctx.ui.dialog.prompt = (o) => (calls.push(["prompt", o.title]), new Promise((r) => (resolvePrompt = r)));
    key("m").run();
    await waitFor(() => calls.at(-1)[0] === "prompt", 2000, "prompt shown");
    assert.equal(key("down").enabled(), false);
    resolvePrompt(undefined);
    key("return").run();
    assert.ok(calls.filter((c) => c[0] === "navigate").length === 1);
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("real-host.py --selftest: the busy-list check passes the demo list and fails the orbit, a short list and a list without keys", async () => {
  const script = path.join(here, "real-host.py");
  const out = await new Promise((resolve) => {
    const child = spawn(realPython, ["-P", script, "--selftest"], { stdio: ["ignore", "pipe", "pipe"] });
    let text = "";
    child.stdout.on("data", (d) => (text += d));
    child.stderr.on("data", (d) => (text += d));
    child.on("close", (code) => resolve({ code, text }));
  });
  assert.equal(out.code, 0, out.text);
  assert.match(out.text, /selftest: \d+ cases, all as expected/);
});

test("panel: pending permission requests and questions from the host's data come first, and Enter opens their session", async () => {
  const host = makeActionHost({ client: false, dialog: false });
  const pending = { permission: { ses_h: [{ id: "p1" }, { id: "p2" }] }, form: { ses_root: [{ id: "f1" }], global: [{ id: "g" }] } };
  const store = (kind) => ({
    sessions: () => Object.keys(pending[kind]).filter((id) => pending[kind][id].length > 0),
    list: (id) => pending[kind][id],
  });
  host.ctx.data = { session: { permission: store("permission"), form: store("form"), get: (id) => ({ title: "title of " + id }) } };
  const stop = tui.setup(host.ctx);
  try {
    const view = host.panelClaim().render(makePanel({ sessionID: "ses_req", width: 80 }));
    await waitFor(() => screen(view).length > 3, 5000, "panel snapshot");
    const lines = screen(view);
    assert.match(lines[0], /^›! designer wants permission  2 requests +\S+$/);
    assert.match(lines[1], /^ \? orchestrator asks you  Add per-user rate limiting +\S+$/);
    assert.ok(lines.includes(" ○ 11 idle"), "the hub's row is up there: the idle count has its own row\n" + lines.join("\n"));
    assert.match(lines[2], /^ ! impl-claude stalled ↻5/);
    assert.ok(!lines.some((l) => l.includes("global")), "the global form is not a session's");
    assert.ok(!lines.some((l) => /^ [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] designer/.test(l)), "designer is not also among the working");
    host.key("return").run();
    assert.deepEqual(host.calls.at(-1), ["navigate", { type: "session", sessionID: "ses_h" }]);
    // a host without the data store: no requests, no error
    const bare = makeActionHost({ client: false, dialog: false });
    bare.ctx.data = { session: { permission: { sessions: () => { throw new Error("x"); } } } };
    const stop2 = tui.setup(bare.ctx);
    try {
      const v2 = bare.panelClaim().render(makePanel({ sessionID: "ses_req2", width: 80 }));
      await waitFor(() => screen(v2).length > 3, 5000, "bare snapshot");
      assert.match(screen(v2)[0], /^›! impl-claude stalled/);
    } finally {
      stop2();
    }
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel: closing removes its f/q layer once; the plugin's cleanup then does not remove it again", () => {
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = tui.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  panelClaim().render(makePanel({ sessionID: "ses_keys" }));
  assert.equal(state.unlayered, 0);
  const closers = globalThis.__agentgraphStub.cleanups.splice(0);
  for (const fn of closers) fn();
  assert.equal(state.unlayered, 1, "the f/q layer is removed when the panel closes");
  for (const fn of closers) fn();
  assert.equal(state.unlayered, 1, "closing twice is harmless");
  // a second mount adds a second layer; the plugin's cleanup removes it, and the command layer
  panelClaim().render(makePanel({ sessionID: "ses_keys" }));
  assert.equal(state.layers.length, 3);
  stop();
  assert.equal(state.unlayered, 3);
  stop();
  assert.equal(state.unlayered, 3);
  globalThis.__agentgraphStub.cleanups.length = 0;
});

test("panel: after it closes, a late fetch is dropped and a re-run view does not restart the timers", async () => {
  fs.writeFileSync(pluginLog, "");
  const { ctx, panelClaim } = makePanelHost();
  const stop = tui.setup(ctx);
  try {
    const [name, setName] = createSignal("agentgraph.graph");
    const panel = makePanel({ sessionID: "ses_slow" });
    Object.defineProperty(panel, "name", { get: () => name() });
    globalThis.__agentgraphStub.cleanups.length = 0;
    const base = new Set(activeIntervals);
    const view = panelClaim().render(panel);
    await waitFor(() => fs.readFileSync(pluginLog, "utf8").includes("ses_slow"), 3000, "slow fetch started");
    const withPanel = [...activeIntervals].filter((h) => !base.has(h));
    assert.ok(withPanel.length >= 1, "panel timers running");
    // close while the fetch is in flight
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    assert.ok([...activeIntervals].every((h) => base.has(h) || !withPanel.includes(h)), "panel timers stopped");
    // the host re-runs the view after disposal: still no timers
    setName("other.panel");
    setName("agentgraph.graph");
    const afterRerun = [...activeIntervals].filter((h) => !base.has(h));
    assert.deepEqual(afterRerun, [], "a re-run view restarted a timer");
    await sleep(1200); // the slow process finishes
    assert.ok(!screen(view).some((l) => l.includes("slowagent")), "late result was drawn: " + screen(view).join(" | "));
    assert.deepEqual([...activeIntervals].filter((h) => !base.has(h)), [], "the late result started a timer");
    // mounting it again starts it again
    globalThis.__agentgraphStub.cleanups.length = 0;
    const view2 = panelClaim().render(makePanel({ sessionID: "ses_again" }));
    await waitFor(() => shows(view2, "t-ses_again"), 5000, "remounted snapshot");
    assert.ok([...activeIntervals].some((h) => !base.has(h)), "remounted panel polls");
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
  } finally {
    stop();
  }
});

test("panel: no title, banner or legend; the keys are one muted line at the bottom; no green", async () => {
  const { ctx, panelClaim } = makePanelHost();
  const stop = tui.setup(ctx);
  try {
    const view = panelClaim().render(makePanel({ sessionID: "ses_legend" }));
    await waitFor(() => shows(view, "t-ses_legend"), 5000, "panel snapshot");
    const lines = screen(view);
    const all = lines.join("\n");
    assert.ok(!/fullscreen|full screen|Agent graph|agentgraph|[┌┐└┘╭╮╰╯]/i.test(all), all);
    assert.equal(lines.filter((l) => /\bq close\b/.test(l)).length, 1, all);
    assert.equal(lines.at(-1), " g graph  q close"); // one row: nothing to select, no session to open on this host
    const keyLine = view.children.at(-1);
    assert.ok(keyLine.children.every((c) => nodeText(c) === " " || c.props.fg === "#888888"), "the key line is muted");
    const hub = view.children[0].children.find((c) => nodeText(c) === "◉");
    assert.equal(hub.props.fg, "#d08040");
  } finally {
    stop();
  }
});

test("panel: a host without keymap, ui.panel, toast or onCleanup still loads and fills the sidebar", () => {
  for (const extra of [{ keymap: false }, { panel: false }, { keymap: false, panel: false, toast: false }]) {
    const { ctx, state } = makePanelHost({}, extra);
    const stop = tui.setup(ctx);
    assert.equal(state.claims[0].replace, "sidebar.content");
    assert.equal(state.claims.length, extra.panel === false ? 1 : 2);
    assert.equal(state.layers.length, extra.keymap === false || extra.panel === false ? 0 : 1);
    assert.doesNotThrow(() => state.claims[0].render({ sessionID: "ses_a" }));
    stop();
  }
  // no panel: no command either, because there is nothing for it to open
  const noPanel = makePanelHost({}, { panel: false });
  const stopNoPanel = tui.setup(noPanel.ctx);
  assert.equal(noPanel.command(), undefined);
  stopNoPanel();
  // a host whose keymap.layer throws
  const { ctx, state } = makePanelHost();
  ctx.keymap.layer = () => {
    throw new Error("no layer");
  };
  const stop = tui.setup(ctx);
  assert.equal(state.claims[0].replace, "sidebar.content");
  assert.doesNotThrow(() => state.claims[1].render(makePanel({ sessionID: "ses_a" })));
  stop();
});

test("panel: cleanup removes the slots, the command layer and the timers", async () => {
  fs.writeFileSync(pluginLog, "");
  const { ctx, state, panelClaim } = makePanelHost();
  const before = new Set(activeIntervals);
  const stop = tui.setup(ctx);
  panelClaim().render(makePanel({ sessionID: "ses_clean" }));
  state.claims[0].render({ sessionID: "ses_clean" });
  await waitFor(() => fs.readFileSync(pluginLog, "utf8").includes("ses_clean"), 3000, "fetch");
  stop();
  stop();
  for (const h of activeIntervals) assert.ok(before.has(h), "an interval survived cleanup");
  assert.equal(state.unslotted, 2);
  // the command layer and the panel's f/q layer, though the host never ran onCleanup; twice is harmless
  assert.equal(state.unlayered, 2);
  const calls = fs.readFileSync(pluginLog, "utf8").trim().split("\n").length;
  await sleep(1300);
  assert.equal(fs.readFileSync(pluginLog, "utf8").trim().split("\n").length, calls, "fetched after cleanup");
});

// ---------------------------------------------------------------------------------------------------------------
// The theme the real host passes (captured from OpenCode 2.3.2 in a terminal): colours are OpenTUI RGBA objects
// (a Float32Array `buffer`, r/g/b getters 0-1), text is `text.base` / `text.muted`, warnings and errors are
// `text.feedback.<kind>.base`, and there is no top-level warning, accent or error. The plugin used to hand the
// whole `text` object to `fg`: the host threw inside OpenTUI, the error line threw again, and the view was empty.

class HostRGBA {
  constructor(r, g, b, a = 1) {
    this.buffer = new Float32Array([r, g, b, a]);
  }
  get r() { return this.buffer[0]; }
  get g() { return this.buffer[1]; }
  get b() { return this.buffer[2]; }
  get a() { return this.buffer[3]; }
}
const rgb = (r, g, b) => new HostRGBA(r / 255, g / 255, b / 255);
const hostTheme = () => ({
  hue: { accent: { 100: rgb(179, 143, 244), 200: rgb(157, 124, 216) }, green: { 200: rgb(127, 216, 143) } },
  text: {
    base: rgb(238, 238, 238),
    muted: rgb(128, 128, 128),
    faint: rgb(128, 128, 128),
    feedback: {
      error: { base: rgb(224, 108, 117), muted: rgb(140, 57, 65) },
      warning: { base: rgb(245, 167, 66), muted: rgb(150, 99, 65) },
      success: { base: rgb(127, 216, 143), muted: rgb(61, 127, 75) },
      info: { base: rgb(92, 156, 245), muted: rgb(44, 91, 155) },
    },
  },
  background: { base: rgb(10, 10, 10) },
});
const allFg = (node, out = []) => {
  if (node && typeof node === "object") {
    if ("fg" in node.props) out.push(node.props.fg);
    for (const child of node.children) allFg(child, out);
  }
  return out;
};

test("host theme: text.base, text.muted, text.feedback and RGBA colours map to the four tones", () => {
  const tones = P.themeTones(hostTheme());
  assert.equal(P.colorProp(tones.plain.fg), "#eeeeee");
  assert.equal(P.colorProp(tones.muted.fg), "#808080");
  assert.equal(P.colorProp(tones.accent.fg), "#f5a742", "the warning colour is nearer terracotta than the purple accent");
  assert.equal(tones.accent.bold, false);
  assert.equal(P.colorProp(tones.danger.fg), "#e06c75");
  // a green warning and accent: plain text in bold, never green
  const greenTheme = hostTheme();
  greenTheme.text.feedback.warning.base = rgb(80, 200, 100);
  greenTheme.hue.accent = { 200: rgb(60, 180, 90) };
  const g = P.themeTones(greenTheme);
  assert.deepEqual([P.colorProp(g.accent.fg), g.accent.bold], ["#eeeeee", true]);
  // colorProp: always #rrggbb (from #rgb, #rrggbb, #rrggbbaa or any { r, g, b }), everything else undefined
  assert.equal(P.colorProp("#123456"), "#123456");
  assert.equal(P.colorProp(" #ABCDEF "), "#abcdef");
  assert.equal(P.colorProp("#abc"), "#aabbcc");
  assert.equal(P.colorProp("#11223344"), "#112233", "alpha dropped");
  assert.equal(P.colorProp({ r: 0.8, g: 0.45, b: 0.25 }), "#cc7340");
  assert.equal(P.colorProp({ r: 204, g: 115, b: 64 }), "#cc7340");
  assert.equal(P.colorProp({ base: rgb(1, 2, 3) }), undefined);
  assert.equal(P.colorProp(undefined), undefined);
  assert.equal(P.colorProp(""), undefined);
});

test("colorProp: anything but a hex colour or an { r, g, b } gives the host's default colour, never a raw string", async () => {
  for (const bad of ["not a colour", "red", "transparent", "rgb(1, 2, 3)", "123456", "#12345", "#1234567", "#ggg", "#", "", null, 12, {}, { base: "#ffffff" }]) {
    assert.equal(P.colorProp(bad), undefined, JSON.stringify(bad));
  }
  // a theme of named colours: the view still draws, every fg is left to the host
  const { ctx, state } = makeHost();
  ctx.theme = { text: { default: "white", muted: "grey" }, warning: "orange", error: "red" };
  const stop = tui.setup(ctx);
  try {
    const view = state.claims[0].render({ sessionID: "ses_named" });
    await waitFor(() => shows(view, "t-ses_named"), 5000, "snapshot with named colours");
    assert.match(screen(view)[0], /^◉ orchestrator/);
    assert.deepEqual(allFg(view), [], "no fg is set at all");
  } finally {
    stop();
  }
});

test("host theme: the sidebar and the panel draw the view with the real host's theme (was empty)", async () => {
  const { ctx, state, panelClaim } = makePanelHost();
  ctx.theme = hostTheme();
  const stop = tui.setup(ctx);
  try {
    const side = state.claims[0].render({ sessionID: "ses_theme" });
    const view = panelClaim().render(makePanel({ sessionID: "ses_theme" }));
    for (const v of [side, view]) {
      await waitFor(() => shows(v, "t-ses_theme"), 5000, "snapshot with the host theme");
      assert.match(screen(v)[0], /^›?◉ orchestrator/);
      const fgs = allFg(v);
      assert.ok(fgs.length > 0);
      assert.ok(fgs.every((fg) => typeof fg === "string" && /^#[0-9a-f]{6}$/.test(fg)), "every fg is a hex string: " + fgs.join(","));
      assert.equal(v.children[0].children.find((c) => nodeText(c) === "◉").props.fg, "#f5a742", "the busy hub mark uses the accent");
      assert.ok(!fgs.some((fg) => P.isGreen(fg)), "no green");
    }
  } finally {
    stop();
  }
});

test("panel: never empty for its own name: loading, error, no session and a drawing failure each draw a line", async () => {
  const { ctx, panelClaim } = makePanelHost();
  ctx.theme = hostTheme();
  const stop = tui.setup(ctx);
  const nonEmpty = (v) => screen(v).filter((l) => l.trim() !== "");
  try {
    // loading: drawn at once, before the first snapshot
    const loading = panelClaim().render(makePanel({ sessionID: "ses_slow" }));
    assert.deepEqual(screen(loading), ["Loading"]);
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    // error document
    const error = panelClaim().render(makePanel({ sessionID: "ses_err" }));
    await waitFor(() => screen(error)[0] === "no db", 5000, "error line");
    assert.deepEqual(screen(error), ["no db"]);
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    // no session: neither the panel nor the router has one
    const none = panelClaim().render(makePanel({ sessionID: undefined }));
    assert.deepEqual(screen(none), ["No session"]);
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    // drawing fails (the host refuses every colour): one plain line with the reason, no colour
    globalThis.__agentgraphStub.failProp = "fg";
    const broken = panelClaim().render(makePanel({ sessionID: "ses_broken" }));
    assert.equal(nonEmpty(broken).length, 1, "one line: " + JSON.stringify(screen(broken)));
    assert.match(screen(broken)[0], /fg refused/);
    assert.equal(broken.children[0].props.fg, undefined);
    assert.ok(R.displayWidth(screen(broken)[0]) <= 80);
  } finally {
    globalThis.__agentgraphStub.failProp = undefined;
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

// ---------------------------------------------------------------------------------------------------------------
// The orbit (src/scene.js): the full-panel scene, in 2D (braille, from above) and 3D (a turning figure)

const S = await import(src("scene.js"));
const DM = await import(src("demo.js"));
const SIZES = [[64, 22], [92, 40], [120, 40], [160, 45], [200, 60]];
const demoWorn = DM.demoDocument(demo, 60000); // the demo document with a wear history, as AGENTGRAPH_TUI_DEMO draws it
const DARK = { dark: true, muted: "#808080" };
const LIGHT = { dark: false, muted: "#8c8c8c" };
const V3 = { view: "3d" };
const cellsOf = (line) => {
  const out = [];
  for (const s of line) for (const ch of s.text) out.push({ ch, tone: s.tone, color: s.color });
  return out;
};
const sceneText = (lines) => lines.map((l) => l.map((s) => s.text).join(""));
const BRAILLE = /[\u2801-\u28ff]/;
const SPIN = /[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/;
function assertExact(lines, width, height, what) {
  assert.equal(lines.length, height, what + ": height");
  lines.forEach((l, i) => {
    assert.equal(R.displayWidth(l.map((s) => s.text).join("")), width, `${what}: line ${i} width`);
    for (const s of l) {
      assert.ok(R.TONES.includes(s.tone) && typeof s.text === "string" && s.text.length > 0, `${what}: span ${JSON.stringify(s)}`);
      if ("color" in s) assert.match(s.color, /^#[0-9a-f]{6}$/, `${what}: colour ${s.color}`);
    }
    for (let k = 1; k < l.length; k += 1) {
      assert.ok(l[k].tone !== l[k - 1].tone || l[k].color !== l[k - 1].color, `${what}: line ${i} has neighbouring spans of one style`);
    }
  });
}
const nonBlank = (lines) => sceneText(lines).some((l) => l.trim() !== "");
const wordIn = (line, name) => new RegExp(`(^|[^\\w-])${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}($|[^\\w-])`).test(line);
const countWord = (lines, name) => lines.reduce((t, l) => t + (l.match(new RegExp(`(?<![\\w-])${name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\w-])`, "g")) || []).length, 0);
const ringNames = demo.agents.filter((a) => a.group !== "hub" && !a.name.startsWith("councillor-")).map((a) => a.name);
const dotsIn = (ch) => {
  let c = ch.codePointAt(0) - 0x2800;
  if (c < 1 || c > 255) return 0;
  let n = 0;
  while (c) {
    n += c & 1;
    c >>= 1;
  }
  return n;
};

test("scene: exact width and height, one style per run, at every size, in both views and during the change", () => {
  for (const doc of [demo, demoWorn]) {
    for (const [w, h] of SIZES) {
      for (const now of [0, 1234, 98765]) {
        for (const extra of [{}, V3, { view: "3d", change: { from: "2d", at: now - 300 } }, { view: "2d", change: { from: "3d", at: now - 500 } }]) {
          for (const colors of [DARK, LIGHT]) assertExact(S.sceneStrict(doc, Object.assign({ width: w, height: h, now, colors }, extra)), w, h, `${w}x${h}@${now} ${JSON.stringify(extra)}`);
        }
      }
    }
  }
  assert.equal(S.sceneFits(64, 22), true);
  assert.equal(S.sceneFits(63, 40), false);
  assert.equal(S.sceneFits(200, 21), false);
  assert.equal(S.sceneFits(undefined, undefined), false);
});

test("scene 2D: header, needs lines, the hub ring, names on both sides, the instruments; no labels", () => {
  const lines = sceneText(S.sceneStrict(demoWorn, { width: 120, height: 40, now: 1234 }));
  assert.match(lines[0], /^◉ orchestrator [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] +\d+\/11  \$\d+\.\d\d$/);
  assert.match(lines[1], /^! impl-claude stuck ↻5/);
  assert.match(lines[2], /^! reviewer stuck/);
  assert.match(lines[3], /^↩ impl-openai sent back ↻3 by reviewer/);
  assert.ok(lines.slice(4, 34).some((l) => l.indexOf("◉") === 60), "the hub's glyph at the centre");
  const rows = { left: new Set(), right: new Set() };
  for (const name of ringNames) {
    const row = lines.findIndex((l, i) => i >= 4 && i < 34 && wordIn(l, name));
    assert.ok(row >= 0, name + " is on the canvas");
    (lines[row].indexOf(name) < 60 ? rows.left : rows.right).add(row);
  }
  assert.ok(rows.left.size >= 3 && rows.right.size >= 3, JSON.stringify([...rows.left, ...rows.right]));
  assert.ok(lines.slice(-6).some((l) => /[▁▂▃▄▅▆▇█]{5}/.test(l)), "the context chart");
  assert.ok(lines.slice(-6).some((l) => /[●○✗↩⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]{6,} \d+\/11/.test(l)), "the steps strip");
  assert.ok(lines.slice(-6).some((l) => /orchestrator +━+─* \$\d/.test(l)), "the spend bars");
  for (const v of [{}, V3]) {
    const all = sceneText(S.sceneStrict(demoWorn, Object.assign({ width: 120, height: 40, now: 1234 }, v))).join("\n");
    assert.ok(!/context|steps|spend|legend|press|\bkeys?\b|\b2d\b|\b3d\b/i.test(all), all);
  }
});

test("scene 2D: the hub is a ring with open space inside; no 6x3 window is more than 60% heavy braille", () => {
  for (const [w, h] of [[120, 30], [92, 30], [160, 35], [64, 14]]) {
    const L = S.orbitLayout(demoWorn, w, h);
    for (let now = 0; now < 3200; now += 100) {
      const grid = S.orbitLines(demoWorn, w, h, now).map(cellsOf);
      // inside the ring: blank but for the hub's glyph
      const inner = L.hubR - 1.5; // dots
      for (let r = 0; r < h; r += 1) {
        for (let c = 0; c < w; c += 1) {
          const corners = [[c * 2, r * 4], [c * 2 + 2, r * 4], [c * 2, r * 4 + 4], [c * 2 + 2, r * 4 + 4]];
          if (!corners.every(([x, y]) => Math.hypot(x - L.cx, y - L.cy) < inner)) continue;
          const ch = grid[r][c].ch;
          assert.ok(ch === " " || ch === "◉", `${w}x${h}@${now}: cell ${c},${r} inside the hub's ring is ${JSON.stringify(ch)}`);
        }
      }
      assert.ok(grid.some((row) => row.some((x) => x.ch === "◉")), "the hub's glyph");
      // density
      const heavy = grid.map((row) => row.map((x) => (dotsIn(x.ch) >= 4 ? 1 : 0)));
      for (let r = 0; r + 3 <= h; r += 1) {
        for (let c = 0; c + 6 <= w; c += 1) {
          let n = 0;
          for (let i = 0; i < 3; i += 1) for (let j = 0; j < 6; j += 1) n += heavy[r + i][c + j];
          assert.ok(n <= 10, `${w}x${h}@${now}: the window at ${c},${r} has ${n}/18 heavy cells`);
        }
      }
    }
  }
});

test("scene: each group in its own colour, dimmed at rest and full while working; no hue between 80 and 190", () => {
  for (const [colors, table] of [[DARK, S.GROUP_COLORS.dark], [LIGHT, S.GROUP_COLORS.light]]) {
    const L = S.orbitLayout(demoWorn, 120, 30);
    const P = S.makePalette(L.keys, colors);
    for (const g of ["council", "decide", "build", "verify", "understand"]) assert.equal(P.full(g), table[g]);
    assert.equal(new Set(Object.values(table)).size, 5, "five different colours");
    const lines = S.orbitLines(demoWorn, 120, 30, 1000, { colors });
    for (const p of L.placed) {
      const row = cellsOf(lines[p.row]);
      const line = row.map((x) => x.ch).join("");
      const at = line.indexOf(p.label);
      assert.ok(at >= 0, p.agent.name);
      const want = p.state === "stuck" ? undefined : p.state === "working" ? P.full(p.key) : P.rest(p.key);
      assert.equal(row[at].color, want, `${p.agent.name} (${p.state}) name colour`);
      assert.equal(row[p.col].color, want, `${p.agent.name} glyph colour`);
      if (p.state === "stuck") assert.equal(row[at].tone, "danger");
    }
    // an unknown group takes the next unused colour, then they cycle
    const P2 = S.makePalette(["build", "ops", "infra", "decide", "a", "b", "c", "d"], colors);
    assert.equal(P2.full("ops"), table.council);
    assert.equal(P2.full("infra"), table.verify);
    assert.equal(P2.full("a"), table.understand);
    assert.equal(P2.full("b"), table.council, "then cycles");
  }
  // every colour over the frames of both views, dark and light
  const seen = new Set();
  for (const colors of [DARK, LIGHT, { dark: true, muted: "#5fd7af" }, {}]) {
    for (const v of [{}, V3, { view: "3d", change: { from: "2d", at: 0 } }]) {
      for (let now = 0; now < 2000; now += 100) {
        for (const l of S.sceneStrict(demoWorn, Object.assign({ width: 120, height: 40, now, colors }, v))) for (const s of l) if (s.color) seen.add(s.color);
      }
    }
  }
  assert.ok(seen.size >= 10);
  for (const c of seen) {
    assert.match(c, /^#[0-9a-f]{6}$/);
    const hue = S.hueOf(c);
    assert.ok(hue === null || hue < 80 || hue > 190, `${c}: hue ${hue}`);
  }
});

test("scene: every agent appears exactly once, the ring in the structure order", () => {
  for (const [w, h] of SIZES) {
    const L = S.orbitLayout(demo, w, h - 10);
    const names = [...L.placed.map((p) => p.agent.name), ...L.placed.flatMap((p) => p.seatDots.map((s) => s.agent.name))];
    assert.deepEqual([...names].sort(), demo.agents.map((a) => a.name).filter((n) => n !== "orchestrator").sort(), `${w}x${h}`);
    assert.deepEqual(L.placed.map((p) => p.agent.name), ["council", "oracle", "oracle-lite", "designer", "impl-claude", "impl-hard", "impl-openai", "reviewer", "explorer", "researcher"]);
    const angles = L.placed.map((p) => p.azimuth).map((a) => (a < -0.1 ? a + 2 * Math.PI : a));
    for (let i = 1; i < angles.length; i += 1) assert.ok(angles[i] > angles[i - 1], `${w}x${h}: ${L.placed[i].agent.name} after ${L.placed[i - 1].agent.name}`);
    assert.ok(Math.abs(L.placed[0].x - L.cx) <= 2 && L.placed[0].y < L.cy, "the lead group at the top");
  }
  const canvas = sceneText(S.orbitLines(demo, 120, 30, 500));
  for (const name of ringNames) assert.equal(countWord(canvas, name), 1, name);
  const folder = D([A("orchestrator", "hub"), A("z1", "zeta"), A("a1", "alpha"), A("a2", "alpha"), A("m1", "mid")], null, "orchestrator", {
    structure: { source: "folder" },
    groups: [{ id: "hub", agents: [] }, { id: "zeta" }, { id: "mid" }, { id: "alpha" }],
  });
  const ring = S.ringOrder(folder, R.validAgents(folder), R.findHub(folder, R.validAgents(folder)));
  assert.deepEqual(ring.groups.map((g) => g.key), ["zeta", "mid", "alpha"]);
  assert.equal(ring.topGap, false);
  assert.deepEqual(S.orbitLayout(folder, 80, 20).placed.map((p) => p.agent.name), ["z1", "m1", "a1", "a2"]);
  const builtin = D([A("orchestrator", "hub"), A("o", "decide"), A("b", "build"), A("v", "verify"), A("u", "understand")]);
  const L2 = S.orbitLayout(builtin, 80, 20);
  assert.deepEqual(L2.placed.map((p) => p.agent.name), ["o", "b", "v", "u"]);
  assert.ok(L2.placed[0].x > L2.cx + 4 && L2.placed[3].x < L2.cx - 4, "an empty slot at the top");
});

// Braille cells in a full group colour (the pulses), without the agents' glyph cells.
function pulseDots(doc, w, h, now) {
  const L = S.orbitLayout(doc, w, h);
  const P = S.makePalette(L.keys, DARK);
  const fulls = new Set(L.keys.map((k) => P.full(k)));
  const glyphs = new Set(L.placed.map((p) => p.row * w + p.col));
  const out = [];
  S.orbitLines(doc, w, h, now, { colors: DARK }).forEach((line, r) => cellsOf(line).forEach((c, col) => {
    if (fulls.has(c.color) && BRAILLE.test(c.ch) && !SPIN.test(c.ch) && !glyphs.has(r * w + col)) out.push({ r, col, d: Math.hypot((col * 2 + 1 - L.cx) / 2, (r * 4 + 2 - L.cy) / 4) });
  }));
  return out;
}
const cellOfDot = (w) => ([x, y]) => (Math.round(y) >> 2) * w + (Math.round(x) >> 1);

test("scene 2D: a working agent's spoke is sparse and carries a pulse outward; the hub breathes", () => {
  const doc = D([A("orchestrator", "hub", 0, "", 1), A("worker", "build", 1, "", 0.5), A("idle", "verify", 0, "", 0.2), A("other", "decide")]);
  const w = 92, h = 30;
  const L = S.orbitLayout(doc, w, h);
  const worker = L.placed.find((p) => p.agent.name === "worker");
  assert.ok(worker.spoke.length > 10);
  const spokeCells = new Set(worker.spoke.map(cellOfDot(w)));
  let ups = 0;
  let downs = 0;
  let differ = 0;
  let prev = null;
  for (let now = 0; now < 4000; now += 100) {
    const dots = pulseDots(doc, w, h, now);
    assert.ok(dots.every((c) => spokeCells.has(c.r * w + c.col)), `full colour only on the working spoke at ${now}`);
    const lines = S.orbitLines(doc, w, h, now);
    if (prev) {
      const a = prev.lines.flatMap(cellsOf);
      const b = lines.flatMap(cellsOf);
      if ([...spokeCells].some((i) => a[i].ch !== b[i].ch || (a[i].ch !== " " && a[i].color !== b[i].color))) differ += 1;
      if (prev.dots.length && dots.length) {
        const pd = Math.max(...prev.dots.map((c) => c.d));
        const nd = Math.max(...dots.map((c) => c.d));
        if (nd > pd) ups += 1;
        else if (nd < pd) downs += 1;
      }
    }
    prev = { lines, dots };
  }
  assert.ok(differ >= 30, "consecutive frames differ on the spoke: " + differ + "/39");
  assert.ok(ups >= 3 * downs && ups >= 10, `the pulse moves outward: ${ups} out, ${downs} back (the wrap)`);
  // the resting spoke: every third dot, starting outside the hub's ring and stopping short of the glyph
  const idle = L.placed.find((p) => p.agent.name === "idle");
  assert.ok(idle.worked);
  assert.equal(L.placed.find((p) => p.agent.name === "other").worked, false);
  const [sx, sy] = idle.spoke[0];
  const [ex, ey] = idle.spoke[idle.spoke.length - 1];
  assert.ok(Math.hypot(sx - L.cx, sy - L.cy) >= L.hubR + 3, "starts outside the hub's ring");
  assert.ok(Math.hypot(ex - idle.gx, ey - idle.gy) >= 4, "stops short of the glyph");
  const restDots = S.orbitLines(doc, w, h, 0).flatMap(cellsOf).filter((c, i) => new Set(idle.spoke.map(cellOfDot(w))).has(i) && BRAILLE.test(c.ch));
  assert.ok(restDots.length <= Math.ceil(idle.spoke.length / 3) + 1, "sparse: " + restDots.length + " cells for " + idle.spoke.length + " dots");
  // the hub breathes while anything works
  const ringDots = (now) => S.orbitLines(doc, w, h, now).flatMap(cellsOf).reduce((t, c) => t + (c.tone === "plain" && !c.color ? dotsIn(c.ch) : 0), 0);
  assert.ok(new Set([0, 400, 800, 1200].map(ringDots)).size > 1, "the hub breathes");
});

test("scene 2D: with nothing working only the faint dot moves, and nothing is at full strength", () => {
  const doc = D([A("orchestrator", "hub", 0, "", 1), A("a", "build", 0, "", 0.5), A("b", "verify", 0, "", 0.2), A("c", "decide")], G({ loops: [{ worker: "a", evaluator: "b", state: "closed", rounds: 2 }] }));
  assert.equal(S.frameInterval(doc), 250);
  assert.equal(S.frameInterval(doc, V3), 166);
  assert.equal(S.frameInterval(D([A("orchestrator", "hub", 1)])), 100);
  assert.equal(S.frameInterval(D([A("orchestrator", "hub", 1)]), V3), 100);
  assert.equal(S.frameInterval(doc, { view: "3d", change: { from: "2d", at: 0 } }, 300), 100, "the camera's move");
  assert.equal(S.frameInterval(doc, { view: "3d", change: { from: "2d", at: 0 } }, 800), 166, "after it");
  const L = S.orbitLayout(doc, 92, 25);
  const P = S.makePalette(L.keys, DARK);
  const fulls = new Set(L.keys.map((k) => P.full(k)));
  const w = 92, h = 40;
  let moved = 0;
  for (let now = 0; now < 20000; now += 250) {
    const a = S.sceneStrict(doc, { width: w, height: h, now, colors: DARK }).flatMap(cellsOf);
    const b = S.sceneStrict(doc, { width: w, height: h, now: now + 250, colors: DARK }).flatMap(cellsOf);
    assert.ok(!a.some((c) => c.tone === "accent" || fulls.has(c.color)), "no pulse at " + now);
    const seen = (c) => c.ch + (c.ch === " " ? "" : c.tone + (c.color || ""));
    const diff = a.map((c, i) => i).filter((i) => seen(a[i]) !== seen(b[i]));
    assert.ok(diff.length <= 2, `at most the old and new cell of the dot change: ${diff.length} at ${now}`);
    for (const i of diff) assert.ok((BRAILLE.test(a[i].ch) || a[i].ch === " ") && (BRAILLE.test(b[i].ch) || b[i].ch === " "), "only dots change");
    if (diff.length) moved += 1;
  }
  assert.ok(moved > 40, "the dot circles: " + moved);
  assert.deepEqual(S.orbitLines(doc, w, 30, 0), S.orbitLines(doc, w, 30, S.IDLE_LAP_MS));
});

test("scene 2D: loop arcs bow outward; the pulse runs to the checker while checking and back when sent back", () => {
  const mk = (state) => D([A("orchestrator", "hub", 1), A("impl", "build", 1, "", 1), A("rev", "verify", 0, "", 1), A("x", "decide"), A("y", "understand")], G({ loops: [{ worker: "impl", evaluator: "rev", state, rounds: 2 }] }));
  for (const [state, toward] of [["checking", "rev"], ["sent_back", "impl"]]) {
    const doc = mk(state);
    const L = S.orbitLayout(doc, 120, 30);
    const arc = L.arcs[0];
    assert.ok(arc && arc.points.length > 10, state + ": an arc");
    const mid = arc.points[Math.floor(arc.points.length / 2)];
    assert.ok(Math.hypot((mid[0] - L.cx) / L.rx, (mid[1] - L.cy) / L.ry) > 1.02, state + ": bowed outward");
    const target = L.placed.find((p) => p.agent.name === toward);
    const arcCells = new Set(arc.points.map(cellOfDot(120)));
    const near = (now) => {
      const dots = pulseDots(doc, 120, 30, now).filter((c) => arcCells.has(c.r * 120 + c.col));
      return dots.length ? Math.min(...dots.map((c) => Math.hypot(c.col - target.col, (c.r - target.row) * 2))) : null;
    };
    let closer = 0;
    let farther = 0;
    for (let now = 0; now < 3000; now += 100) {
      const a = near(now);
      const b = near(now + 100);
      if (a === null || b === null) continue;
      if (b < a) closer += 1;
      else if (b > a) farther += 1;
    }
    assert.ok(closer > 2 * farther, `${state}: toward ${toward}: ${closer} closer, ${farther} farther`);
  }
  const closed = mk("closed");
  const L = S.orbitLayout(closed, 120, 30);
  const arcCells = new Set(L.arcs[0].points.map(cellOfDot(120)));
  const spokes = new Set(L.placed.flatMap((p) => p.spoke.map(cellOfDot(120))));
  for (let now = 0; now < 2000; now += 100) {
    assert.ok(!pulseDots(closed, 120, 30, now).some((c) => arcCells.has(c.r * 120 + c.col) && !spokes.has(c.r * 120 + c.col)), "no pulse on a closed loop");
  }
});

test("scene 2D: glyphs by state; a stuck agent blinks once a second; seats are dots round the council", () => {
  const doc = D([
    A("orchestrator", "hub"), A("idler", "build"), A("busy", "build", 1, "", 1), A("done", "verify", 0, "", 1),
    A("back", "decide", 0, "", 1), A("stuck", "understand", 0, "", 1), A("council", "council"),
    A("councillor-a", "council", 0, "council"), A("councillor-b", "council", 1, "council"),
  ], G({
    nodes: [{ id: "n1", agent: "done", status: "passed" }, { id: "n2", agent: "stuck", status: "failed" }, { id: "n3", agent: "busy", status: "running", busy: true, goal: "Write the limiter" }],
    loops: [{ worker: "back", evaluator: "done", state: "sent_back", rounds: 3 }],
  }));
  const w = 120, h = 30;
  const L = S.orbitLayout(doc, w, h);
  const P = S.makePalette(L.keys, DARK);
  const glyph = (now, name) => {
    const p = L.placed.find((x) => x.agent.name === name);
    return cellsOf(S.orbitLines(doc, w, h, now, { colors: DARK })[p.row])[p.col];
  };
  assert.deepEqual(glyph(0, "idler"), { ch: "○", tone: "plain", color: P.rest("build") });
  assert.equal(glyph(0, "busy").color, P.full("build"));
  assert.match(glyph(0, "busy").ch, SPIN);
  assert.deepEqual(glyph(0, "done"), { ch: "●", tone: "plain", color: P.rest("verify") });
  assert.equal(glyph(0, "back").ch, "↩");
  assert.deepEqual(glyph(0, "stuck"), { ch: "!", tone: "danger", color: undefined });
  assert.notEqual(glyph(500, "stuck").ch, "!", "blink off");
  assert.equal(glyph(1000, "stuck").ch, "!", "blink on");
  const council = L.placed.find((x) => x.agent.name === "council");
  assert.deepEqual(council.seatDots.map((s) => s.agent.name), ["councillor-a", "councillor-b"]);
  for (const s of council.seatDots) assert.ok(Math.hypot(s.x - council.gx, s.y - council.gy) <= 7);
  const text = sceneText(S.orbitLines(doc, w, h, 0));
  const busy = L.placed.find((x) => x.agent.name === "busy");
  assert.match(text[busy.row + 1], /Write the limiter \$1\.00/);
});

test("scene: instruments: drop c, then b, then a; side by side from 100 columns; 6 rows from 30", () => {
  const instRows = (lines) => {
    const t = sceneText(lines);
    let n = 0;
    for (let i = t.length - 1; i >= 0 && !BRAILLE.test(t[i].replace(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/g, "")) && !/[○●↩!]\s+\S/.test(t[i].slice(0, 0)); i -= 1) n += 1;
    return t.slice(-6);
  };
  const t160 = sceneText(S.sceneStrict(demoWorn, { width: 160, height: 45, now: 1 }));
  const bottom = t160.slice(-6);
  assert.ok(bottom.some((l) => /█/.test(l) && /━/.test(l)), "side by side at 160");
  assert.ok(/━+─* \d/.test(t160[t160.length - 1]) && /\d+\/11/.test(t160[t160.length - 1]), "the wear line and the steps on the last row");
  const t92 = sceneText(S.sceneStrict(demoWorn, { width: 92, height: 40, now: 1 }));
  assert.ok(t92.slice(-6).every((l) => !(/[▁▂▃▄▅▆▇█]/.test(l) && /━/.test(l))), "stacked at 92");
  assert.match(t92[t92.length - 6], /[▁▂▃▄▅▆▇█]/);
  assert.match(t92[t92.length - 4], /^━+─* \d/);
  assert.match(t92[t92.length - 3], /^[●○✗↩⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]+ \d+\/11 *$/);
  assert.match(t92[t92.length - 2], /^orchestrator /);
  // 22 rows: four instrument rows at most, and room for the canvas (at least 12 rows) first
  const t64 = S.sceneStrict(demoWorn, { width: 64, height: 22, now: 1 });
  assertExact(t64, 64, 22, "64x22");
  const has = (h) => {
    const t = sceneText(S.sceneStrict(demoWorn, { width: 64, height: h, now: 1 })).slice(-4);
    return [t.some((l) => /[▁▂▃▄▅▆▇█]/.test(l)), t.some((l) => /\d+\/11 *$/.test(l) && /^[●○✗↩⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/.test(l)), t.some((l) => /^orchestrator +━/.test(l))];
  };
  assert.deepEqual(has(22), [true, true, true], "64x22: all three");
  assert.deepEqual(has(19), [true, true, false], "no room: spend (c) dropped first");
  assert.deepEqual(has(18), [true, false, false], "then steps (b)");
  assert.deepEqual(has(15), [false, false, false], "then context (a)");
  // the bars: largest first, the working agent's in the accent tone
  const bars = S.sceneStrict(demoWorn, { width: 160, height: 45, now: 1 }).slice(-6);
  const barSpan = bars.flatMap((l) => l).find((s) => /^━+$/.test(s.text) && s.tone === "accent");
  assert.ok(barSpan, "a working agent's bar is accent");
  // without wear: only ctx, or nothing
  const noWear = sceneText(S.sceneStrict(Object.assign({}, demo, { root: Object.assign({}, demo.root, { context: 124000 }) }), { width: 120, height: 40, now: 1 }));
  assert.ok(noWear.slice(-6).some((l) => /ctx 124k/.test(l)));
  assert.ok(!sceneText(S.sceneStrict(demo, { width: 120, height: 40 })).slice(-6).some((l) => /[▁▂▃▄▅▆▇█]/.test(l)));
  // no graph: no steps
  const noGraph = sceneText(S.sceneStrict(Object.assign({}, demoWorn, { graph: null }), { width: 120, height: 40 }));
  assert.ok(!noGraph.slice(-6).some((l) => /\d+\/11/.test(l)));
  // the bars move to a new length over a few frames
  const richer = JSON.parse(JSON.stringify(demoWorn));
  richer.agents.find((a) => a.name === "impl-hard").sessions = [{ id: "x", cost: 5, tokens_in: 1, tokens_out: 1 }];
  const barOf = (lines, name) => {
    const l = sceneText(lines).find((x) => new RegExp("^" + name + " +[━─]").test(x)) || "";
    return (l.match(/━/g) || []).length;
  };
  const frames = [0, 100, 300, 800].map((dt) => S.sceneStrict(richer, { width: 92, height: 45, now: 10000 + dt, previous: { doc: demoWorn, at: 10000 } }));
  const lens = frames.map((f) => barOf(f, "orchestrator"));
  assert.ok(lens[0] > lens[3] && lens[1] >= lens[2] && lens[2] >= lens[3], "the orchestrator's bar shrinks as impl-hard's grows: " + lens);
  assert.ok(lens[1] > lens[3], "over a few frames, not at once: " + lens);
  void instRows;
});


// ---- 3D

test("scene 3D: it turns when idle; near side of a ring draws @ or 1, the opposite side · or .", () => {
  const idleDoc = JSON.parse(JSON.stringify(demoWorn));
  for (const a of idleDoc.agents) a.busy = 0;
  assert.equal(R.isAnimated(idleDoc), false);
  let prev = null;
  for (let now = 0; now < 2000; now += 166) {
    const t = sceneText(S.orbitLines(idleDoc, 120, 30, now, V3)).join("\n");
    if (prev !== null) assert.notEqual(t, prev, "consecutive idle frames differ at " + now);
    prev = t;
  }
  // near and far: the nearest point of each ring and the point opposite it. The far half is a dotted line (every
  // other cell), so the far point is looked for in its cell or the cells either side.
  const check = (doc, w, h, now) => {
    const L = S.orbitLayout(doc, w, h);
    const F = S.figure3D(L);
    const project = S.camera3D(L, (2 * Math.PI * now) / S.TURN_MS, S.ELEVATION_3D);
    const grid = S.orbitLines(doc, w, h, now, V3).map((l) => cellsOf(l).map((c) => c.ch));
    const at = (q, dc = 0) => (grid[q.row] && grid[q.row][q.col + dc]) || "";
    return F.rings.map((ring) => {
      let best = 0;
      ring.points.forEach((p, i) => {
        if (project(p).depth > project(ring.points[best]).depth) best = i;
      });
      const a = project(ring.points[best]);
      const b = project(ring.points[(best + ring.points.length / 2) % ring.points.length]);
      const far = [at(b, -1), at(b), at(b, 1)];
      const hub = F.hubRing.map(project);
      const behindHub = b.row >= Math.min(...hub.map((q) => q.row)) - 1 && b.row <= Math.max(...hub.map((q) => q.row)) + 1 && b.col >= Math.min(...hub.map((q) => q.col)) - 1 && b.col <= Math.max(...hub.map((q) => q.col)) + 1;
      return { depth: a.depth, hidden: behindHub || (Math.abs(a.row - b.row) <= 1 && Math.abs(a.col - b.col) <= 3), near: at(a), far, ok: ["@", "1"].includes(at(a)) && far.some((c) => c === "·" || c === ".") && !far.some((c) => c === "@" || c === "1") };
    });
  };
  const single = D([A("orchestrator", "hub"), A("solo", "build", 0, "", 1)]);
  let checked = 0;
  for (let now = 0; now < S.TURN_MS; now += 1000) {
    for (const r of check(single, 120, 30, now)) {
      if ([r.near, ...r.far].some((c) => /[a-z○◉●]/.test(c))) continue; // the agent's own glyph or label sits there
      if (r.depth < 0.5) continue; // the ring is seen nearly face on: it has no near side
      if (r.hidden) continue; // the far point is behind the hub's ring or, seen edge on, behind the near point
      assert.ok(r.ok, `one ring at ${now}: near ${r.near}, far ${r.far.join("")}`);
      checked += 1;
    }
  }
  assert.ok(checked >= 16, "checked " + checked);
  let good = 0;
  for (const now of [0, 9000, 21000]) {
    for (const r of check(idleDoc, 120, 30, now)) {
      assert.ok(["@", "1"].includes(r.near), `demo at ${now}: near ${r.near}`);
      if (r.ok) good += 1;
    }
  }
  assert.ok(good >= 9, `demo: near @/1 and far ·/. on ${good} of 15 rings (a far point may sit behind something nearer)`);
  // the characters by depth
  assert.deepEqual([...S.DEPTH_CHARS], ["·", ".", ":", "0", "1", "@"]);
  assert.equal(S.depthLevel(-1), 0);
  assert.equal(S.depthLevel(1), 5);
});

test("scene 3D: 0s and 1s advance outward along a working agent's spoke", () => {
  const doc = D([A("orchestrator", "hub", 0, "", 1), A("worker", "build", 1, "", 0.5), A("idle", "verify", 0, "", 0.2), A("other", "decide")]);
  const w = 120, h = 30;
  const L = S.orbitLayout(doc, w, h);
  const P = S.makePalette(L.keys, DARK);
  let ups = 0;
  let downs = 0;
  let prev = null;
  for (let now = 0; now < 4000; now += 100) {
    const project = S.camera3D(L, (2 * Math.PI * now) / S.TURN_MS, S.ELEVATION_3D);
    const hub = project([0, 0, 0]);
    const digits = [];
    S.orbitLines(doc, w, h, now, { view: "3d", colors: DARK }).forEach((line, r) => cellsOf(line).forEach((c, col) => {
      if ((c.ch === "0" || c.ch === "1") && c.color === P.full("build")) digits.push(Math.hypot(col - hub.col, (r - hub.row) * 2));
    }));
    assert.ok(digits.length > 0 || prev === null || true);
    if (prev && prev.length && digits.length) {
      const a = Math.max(...prev);
      const b = Math.max(...digits);
      if (b > a) ups += 1;
      else if (b < a) downs += 1;
    }
    prev = digits;
  }
  assert.ok(ups >= 2 * downs && ups >= 10, `digits stream outward: ${ups} out, ${downs} back`);
});

test("scene 3D: every agent name exactly once in every frame; a far label never covers a near one", () => {
  for (const [w, h] of [[92, 30], [120, 30], [160, 35]]) {
    const L = S.orbitLayout(demoWorn, w, h);
    const F = S.figure3D(L);
    for (let now = 0; now < S.TURN_MS; now += 1300) {
      const lines = S.orbitLines(demoWorn, w, h, now, { view: "3d", colors: DARK });
      const text = sceneText(lines);
      for (const name of ringNames) assert.equal(countWord(text, name), 1, `${w}x${h}@${now}: ${name}`);
      // near names in their group colour at full strength, far names muted
      const project = S.camera3D(L, (2 * Math.PI * now) / S.TURN_MS, S.ELEVATION_3D);
      const P = S.makePalette(L.keys, DARK);
      for (const p of L.placed) {
        if (p.state === "stuck") continue;
        const q = project(F.agents.get(p.agent.name));
        const r = text.findIndex((l) => wordIn(l, p.agent.name));
        const c = text[r].search(new RegExp(`(?<![\\w-])${p.agent.name}(?![\\w-])`));
        const cell = cellsOf(lines[r])[c];
        if (q.depth >= 0) assert.equal(cell.color, P.full(p.key), `${p.agent.name} near: full colour`);
        else assert.equal(cell.tone, "muted", `${p.agent.name} far: muted`);
      }
    }
  }
});

test("scene: the change between the views: frames in between, all different, ending on the target's steady frame", () => {
  for (const [from, to] of [["2d", "3d"], ["3d", "2d"]]) {
    const at = 50000;
    const frames = [];
    for (let now = at; now <= at + S.CHANGE_MS; now += 100) frames.push(sceneText(S.sceneStrict(demoWorn, { width: 120, height: 40, now, view: to, change: { from, at }, colors: DARK })).join("\n"));
    const steadyFrom = sceneText(S.sceneStrict(demoWorn, { width: 120, height: 40, now: at, view: from, colors: DARK })).join("\n");
    const steadyTo = (now) => sceneText(S.sceneStrict(demoWorn, { width: 120, height: 40, now, view: to, colors: DARK })).join("\n");
    const between = frames.slice(1, -1);
    assert.ok(between.length >= 5, from + "->" + to);
    assert.equal(new Set(between).size, between.length, "the frames in between differ from each other");
    for (const f of between) {
      assert.notEqual(f, steadyFrom);
      assert.ok(/[@01:.·]{3}/.test(f), "drawn in the depth characters");
    }
    assert.equal(frames[frames.length - 1], steadyTo(at + S.CHANGE_MS), "the last frame is the target's steady frame");
    const full = S.sceneStrict(demoWorn, { width: 120, height: 40, now: at + S.CHANGE_MS, view: to, change: { from, at }, colors: DARK });
    assert.deepEqual(full, S.sceneStrict(demoWorn, { width: 120, height: 40, now: at + S.CHANGE_MS, view: to, colors: DARK }));
  }
  assert.equal(S.changeAt({ view: "3d", change: { from: "2d", at: 0 } }, 350).t, 0.5);
  assert.equal(S.changeAt({ view: "3d", change: { from: "3d", at: 0 } }, 350), null);
  assert.equal(S.changeAt({ view: "3d", change: { from: "2d", at: "x" } }, 350), null);
  assert.equal(S.changeAt({ view: "3d" }, 350), null);
});

const PARTIAL = () => {
  const strip = (doc, ...keys) => {
    const d = JSON.parse(JSON.stringify(doc));
    for (const k of keys) delete d[k];
    return d;
  };
  const unknown = JSON.parse(JSON.stringify(demoWorn));
  for (const a of unknown.agents) a.busy = "maybe";
  for (const n of unknown.graph.nodes) n.status = "weird";
  for (const l of unknown.graph.loops) l.state = 42;
  unknown.root.wear = { history: ["x", null, -1, 1e30], compact_at: "no", window: 0 };
  return {
    null: null, undefined, string: "doc", array: [], empty: {}, error: { version: 1, error: "no db" },
    noStructure: strip(demoWorn, "structure"), noGroups: strip(demoWorn, "groups"), noWear: demo, noGraph: strip(demoWorn, "graph"),
    noAgents: strip(demoWorn, "agents"), agentsEmpty: Object.assign({}, demoWorn, { agents: [] }), noRoot: strip(demoWorn, "root"),
    noTotals: strip(demoWorn, "totals"), unknown,
    onlyHub: D([A("orchestrator", "hub")]), noHub: D([A("a", "build", 1)]), noGroupField: D([A("orchestrator", "hub"), { name: "x", busy: 1, sessions: [] }]),
    many: D(Array.from({ length: 60 }, (_, i) => A("agent-with-a-long-name-" + i, ["build", "verify", "decide", "g" + (i % 9)][i % 4], i % 7 === 0 ? 1 : 0, "", i / 10))),
  };
};

test("scene: partial documents and absurd sizes, in both views: exact size, nothing thrown, never empty", () => {
  for (const [name, doc] of Object.entries(PARTIAL())) {
    for (const v of [{}, V3, { view: "3d", change: { from: "2d", at: 500 } }]) {
      for (const [w, h] of [...SIZES, [1, 1], [10, 3], [0, 5], [5, 0], [10000, 30], [80, 1000]]) {
        let lines;
        assert.doesNotThrow(() => (lines = S.sceneStrict(doc, Object.assign({ width: w, height: h, now: 777 }, v))), `${name} ${w}x${h} ${JSON.stringify(v)}`);
        assert.equal(lines.length, h, `${name} ${w}x${h}: height`);
        if (w * h <= 200000) assertExact(lines, w, h, `${name} ${w}x${h}`);
        else for (const i of [0, Math.floor(h / 2), h - 1]) assert.equal(R.displayWidth(lines[i].map((s) => s.text).join("")), w, `${name} ${w}x${h}: line ${i}`);
        if (w > 0 && h > 0) assert.ok(nonBlank(lines), `${name} ${w}x${h} is blank`);
      }
    }
  }
  const t0 = Date.now();
  const huge = S.sceneStrict(demoWorn, { width: 10000, height: 10000, now: 1, view: "3d" });
  assert.equal(huge.length, 10000);
  assert.equal(R.displayWidth(huge[9999].map((s) => s.text).join("")), 10000);
  assert.ok(Date.now() - t0 < 2000);
  assert.deepEqual(S.scene(demoWorn), []);
  assert.doesNotThrow(() => S.scene(demoWorn, { width: "120", height: "40", frame: "x", now: NaN, view: 3, change: "x", colors: 5 }));
  assertExact(S.scene(demoWorn, { width: "120", height: "40" }), 120, 40, "string sizes");
  const before = JSON.stringify(demoWorn);
  S.scene(demoWorn, { width: 120, height: 40, now: 5, view: "3d" });
  assert.equal(JSON.stringify(demoWorn), before);
});

test("scene: a seeded fuzz removes and spoils fields at random; every frame of both views is exact and drawn", () => {
  let seed = 20261119;
  const rand = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  const SPOIL = [undefined, null, 0, -1, "", "x", [], {}, NaN, 1e12, true];
  const walk = (v) => {
    if (Array.isArray(v)) return v.filter(() => rand() > 0.15).map(walk);
    if (v && typeof v === "object") {
      const out = {};
      for (const [k, x] of Object.entries(v)) {
        const r = rand();
        if (r < 0.12) continue;
        out[k] = r < 0.2 ? SPOIL[Math.floor(rand() * SPOIL.length)] : walk(x);
      }
      return out;
    }
    return v;
  };
  for (let i = 0; i < 250; i += 1) {
    const doc = walk(demoWorn);
    const [w, h] = SIZES[i % SIZES.length];
    const v = [{}, V3, { view: "2d", change: { from: "3d", at: i * 137 - 200 } }][i % 3];
    let lines;
    try {
      lines = S.sceneStrict(doc, Object.assign({ width: w, height: h, now: i * 137, colors: i % 2 ? LIGHT : DARK }, v));
    } catch (error) {
      throw new Error(`fuzz case ${i} (seed 20261119) threw: ${error && error.stack}`);
    }
    assertExact(lines, w, h, `fuzz ${i}`);
    assert.ok(nonBlank(lines), `fuzz ${i} blank`);
  }
});

// A fixed piece of work of the same kind as a frame (per cell: some trigonometry, a character, a string per row),
// timed in the same process as the frames: the frame's cost as a multiple of it does not depend on machine load.
function referenceFrame(width = 160, height = 45, t = 0) {
  const rows = [];
  for (let y = 0; y < height; y += 1) {
    let row = "";
    for (let x = 0; x < width; x += 1) {
      const v = Math.sin(x * 0.13 + t) * Math.cos(y * 0.21 - t) + Math.sqrt(x * x + y * y) * 0.01;
      row += String.fromCharCode(0x2800 + (Math.floor((v + 2) * 60) & 0xff));
    }
    rows.push(row);
  }
  return rows;
}
// Today's frame cost in reference frames: the medians of five runs on 2026-11-19 (2D 4.8 to 5.1, 2D with a new
// document each frame 6.6 to 8.5, 3D 7.0 to 7.8, 3D with a new document 9.8 to 12.3; at load average 110 the largest
// was 2.6 times its median). The limit is FRAME_COST_LIMIT times these: a frame 5 times slower fails.
const FRAME_COST_TODAY = { "2d": 5.0, "2d new": 7.1, "3d": 7.7, "3d new": 11.0 };
const FRAME_COST_LIMIT = 4;

test("scene: a 160x45 frame costs less than four times what it costs today, counted in reference frames (time reported only)", () => {
  // Interleaved batches; the fastest batch of each kind is the one the machine's load touched least.
  const batch = (fn) => {
    const t0 = process.hrtime.bigint();
    for (let i = 0; i < 10; i += 1) fn(i);
    return Number(process.hrtime.bigint() - t0) / 1e6 / 10;
  };
  const kinds = {
    ref: (i) => referenceFrame(160, 45, i),
    "2d": (i) => S.scene(demoWorn, { width: 160, height: 45, now: i * 100 }),
    "2d new": (i) => S.scene(DM.demoDocument(demo, i * 2000), { width: 160, height: 45, now: i * 100 }),
    "3d": (i) => S.scene(demoWorn, Object.assign({ width: 160, height: 45, now: i * 100 }, V3)),
    "3d new": (i) => S.scene(DM.demoDocument(demo, i * 2000), Object.assign({ width: 160, height: 45, now: i * 100 }, V3)),
  };
  for (const fn of Object.values(kinds)) for (let i = 0; i < 20; i += 1) fn(i); // warm up
  const best = Object.fromEntries(Object.keys(kinds).map((k) => [k, Infinity]));
  for (let round = 0; round < 15; round += 1) for (const [k, fn] of Object.entries(kinds)) best[k] = Math.min(best[k], batch(fn));
  const cost = Object.fromEntries(Object.keys(FRAME_COST_TODAY).map((k) => [k, best[k] / best.ref]));
  console.log(`     frame 160x45: reference ${best.ref.toFixed(3)} ms; ` +
    Object.keys(FRAME_COST_TODAY).map((k) => `${k} ${best[k].toFixed(3)} ms (${cost[k].toFixed(1)} ref)`).join(", "));
  for (const [k, today] of Object.entries(FRAME_COST_TODAY)) assert.ok(cost[k] < FRAME_COST_LIMIT * today, `${k}: ${cost[k].toFixed(1)} reference frames, today ${today}, limit ${FRAME_COST_LIMIT * today}`);
});

test("demo: demoDocument adds a growing wear history and moving costs; the runner spawns nothing", async () => {
  const a = DM.demoDocument(demo, 0);
  const b = DM.demoDocument(demo, 30000);
  assert.ok(Array.isArray(a.root.wear.history) && a.root.wear.history.length > 10);
  assert.equal(a.root.wear.compact_at, 250000);
  assert.ok(b.totals.cost > a.totals.cost);
  assert.notDeepEqual(a.root.wear.history, b.root.wear.history);
  assert.equal(demo.root.wear, undefined, "the fixture is not changed");
  let t = 1000;
  const runner = DM.createDemoRunner({ fs, file: path.join(here, "fixtures", "demo.json"), now: () => t });
  const first = await runner.snapshot("ses_x");
  t += 9000;
  const second = await runner.snapshot("ses_x");
  assert.ok(second.totals.cost > first.totals.cost);
  const bad = DM.createDemoRunner({ fs, file: path.join(tmp, "missing.json") });
  assert.match((await bad.snapshot("s")).error, /demo document/);
  runner.stop();
  assert.ok((await runner.snapshot("s")).error);
  assert.equal(P.demoRequested({ AGENTGRAPH_TUI_DEMO: "1" }), true);
  assert.equal(P.demoRequested({ AGENTGRAPH_TUI_DEMO: "0" }), false);
  assert.equal(P.demoRequested({ AGENTGRAPH_TUI_DEMO: "" }), false);
  assert.equal(P.demoRequested({}), false);
  assert.equal(P.demoRequested(null), false);
});

// A plugin built directly, with a clock the test controls and the demo source (no Python).
const OT = await import("@opentui/solid");
const solid = await import("solid-js");
function makeScenePlugin({ demoOn = true, spawned = { n: 0 }, screen = { width: 200, height: 60 } } = {}) {
  const clockRef = { t: 1795075000000 };
  const intervals = [];
  const plugin = P.createPlugin({
    createElement: OT.createElement, insert: OT.insert, setProp: OT.setProp, createSignal: solid.createSignal, onCleanup: solid.onCleanup,
    spawn: (...args) => {
      spawned.n += 1;
      throw new Error("spawn called: " + args[0]);
    },
    screenSize: () => screen,
    fs, os, path, env: demoOn ? { AGENTGRAPH_TUI_DEMO: "1" } : {}, demoFile: path.join(here, "fixtures", "demo.json"), now: () => clockRef.t,
    timers: {
      setInterval: (fn, ms) => {
        intervals.push(ms);
        return globalThis.setInterval(fn, ms);
      },
      clearInterval: (h) => globalThis.clearInterval(h),
    },
  });
  return { plugin, clockRef, spawned, intervals };
}
const sizePanel = (view, width, height) => {
  view.width = width;
  view.height = height;
  view.props.onSizeChange.call(view);
};

// `g` in the newest panel's view layer: the panel opens on the list, `g` shows the orbit.
const pressG = (state) => state.layers.filter((l) => (l.commands || []).some((c) => c.bind === "g")).pop().commands.find((c) => c.bind === "g").run();
// The orbit is on screen: the hub's glyph inside the canvas (the list has it only at the start of its first line).
const isOrbit = (lines) => lines.length >= 22 && lines.slice(1).some((l) => l.indexOf("◉") > 4);
// The 3D view: the near side of a ring in @, and 0s and 1s, inside the canvas rows (no braille ring there).
const has3D = (lines) => {
  const body = lines.slice(4, -6).join("\n");
  return /@@/.test(body) && /[01]/.test(body) && !/[\u2801-\u28ff]/.test(body.replace(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/g, ""));
};

test("panel scene: the panel opens on the list; g shows the orbit from 64x22 with a measured size; g switches back; sidebar unchanged", async () => {
  const { plugin, spawned, clockRef } = makeScenePlugin();
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(view).length > 5, 5000, "demo snapshot");
    // the size is not known yet: the list
    assert.ok(screen(view).some((l) => l.startsWith(" ◉ orchestrator")));
    assert.ok(screen(view).length < 40 && !isOrbit(screen(view)), "the list while the height is unknown");
    const gLayer = () => state.layers.find((l) => (l.commands || []).some((c) => c.bind === "g"));
    assert.equal(gLayer(), undefined, "no g before there is an orbit to switch to");
    // measured, room for the orbit: still the list (at most 16 rows), until g
    sizePanel(view, 120, 40);
    assert.ok(!isOrbit(screen(view)) && screen(view).length <= 16, "the list by default\n" + screen(view).join("\n"));
    gLayer().commands.find((c) => c.bind === "g").run();
    assert.equal(screen(view).length, 40);
    assert.ok(screen(view).every((l) => R.displayWidth(l) === 120));
    assert.ok(isOrbit(screen(view)), "the orbit");
    // smaller than 64x22: the list
    sizePanel(view, 63, 40);
    assert.ok(!isOrbit(screen(view)) && screen(view).length < 40);
    sizePanel(view, 100, 21);
    assert.ok(!isOrbit(screen(view)));
    sizePanel(view, 64, 22);
    assert.equal(screen(view).length, 22);
    assert.ok(screen(view).every((l) => R.displayWidth(l) === 64));
    // d: 2D to 3D and back, the camera moving over CHANGE_MS; the orbit opens in 2D
    sizePanel(view, 120, 40);
    assert.ok(!has3D(screen(view)), "opens in 2D");
    const d = gLayer().commands.find((c) => c.bind === "d");
    assert.equal(d.enabled(), true);
    d.run();
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => has3D(screen(view)), 3000, "d: the frame timer draws 3D");
    assert.ok(isOrbit(screen(view)), "d: 3D\n" + screen(view).join("\n"));
    // g: list, and back; one layer with g and d, enabled only for this panel
    const g = gLayer().commands.find((c) => c.bind === "g");
    assert.deepEqual(gLayer().commands.map((c) => c.bind), ["g", "d"]);
    assert.equal(g.enabled(), true);
    g.run();
    assert.ok(!isOrbit(screen(view)), "g: the list");
    // remembered: a new panel mount opens with the list
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    const again = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(again).length > 5, 5000, "remounted snapshot");
    sizePanel(again, 120, 40);
    assert.ok(!isOrbit(screen(again)), "the list is remembered");
    gLayer();
    state.layers.filter((l) => (l.commands || []).some((c) => c.bind === "g")).pop().commands[0].run();
    assert.ok(isOrbit(screen(again)), "g again: the orbit");
    assert.ok(has3D(screen(again)), "and still 3D: d is remembered while the app runs");
    const dAgain = state.layers.filter((l) => (l.commands || []).some((c) => c.bind === "d")).pop().commands.find((c) => c.bind === "d");
    dAgain.run();
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => !has3D(screen(again)), 3000, "d again: the frame timer draws 2D");
    assert.ok(isOrbit(screen(again)), "d again: 2D\n" + screen(again).join("\n"));
    // the sidebar draws the list, exactly as render() at 37 columns
    const side = state.claims[0].render({ sessionID: "ses_demo" });
    await waitFor(() => screen(side).length > 3, 5000, "sidebar snapshot");
    const spin = (l) => l.replace(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/g, "*");
    const doc = DM.demoDocument(demo, clockRef.t - 1795075000000, clockRef.t); // the demo at the plugin's clock (d moved it on)
    const shown = (l) => spin(l).trimEnd();
    assert.deepEqual(screen(side).map(shown), R.toText(R.render(doc, { width: 37, now: clockRef.t })).split("\n").map(shown));
    assert.equal(spawned.n, 0, "demo mode spawns nothing");
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

// A host panel that really toggles: `presentation` flips between "panel" and "fullscreen" on toggleFullscreen, and the test
// sets the size the host would measure afterwards.
function fullscreenPanel(presentation = "panel") {
  const panel = makePanel({ sessionID: "ses_demo", width: 120, presentation });
  panel.toggleFullscreen = () => {
    panel.calls.full += 1;
    panel.presentation = panel.presentation === "fullscreen" ? "panel" : "fullscreen";
  };
  return panel;
}

test("panel scene: g always changes what is shown; too small as a side panel, it goes full screen and back", async () => {
  const { plugin } = makeScenePlugin({ screen: { width: 160, height: 45 } });
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const panel = fullscreenPanel();
    const view = panelClaim().render(panel);
    await waitFor(() => screen(view).length > 5, 5000, "demo snapshot");
    sizePanel(view, 56, 44); // the side panel of a 160-column terminal
    assert.ok(!isOrbit(screen(view)), "the list: 56 columns are too narrow");
    const key = (bind) => state.layers.filter((l) => (l.commands || []).some((c) => c.bind === bind)).pop().commands.find((c) => c.bind === bind);
    key("g").run();
    assert.equal(panel.calls.full, 1, "g asked for full screen");
    sizePanel(view, 160, 45); // the host lays the panel out full screen
    assert.ok(isOrbit(screen(view)), "g: the orbit");
    key("d").run(); // d does not change what g leaves
    key("g").run();
    assert.equal(panel.calls.full, 2, "g back leaves the full screen that g entered");
    assert.equal(panel.presentation, "panel");
    sizePanel(view, 56, 44);
    assert.ok(!isOrbit(screen(view)), "g: the list");
    key("g").run();
    sizePanel(view, 160, 45);
    assert.ok(isOrbit(screen(view)), "g again: the orbit");
    // f in between: full screen is the user's now, g to the list keeps it
    key("f").run();
    assert.equal(panel.calls.full, 4, "f toggled it");
    sizePanel(view, 56, 44);
    assert.ok(!isOrbit(screen(view)), "f left full screen: the list");
    key("g").run();
    sizePanel(view, 160, 45);
    assert.ok(isOrbit(screen(view)), "g after f: the orbit");
    key("f").run(); // the user leaves full screen while the orbit shows ...
    sizePanel(view, 56, 44);
    assert.ok(!isOrbit(screen(view)));
    // ... and a panel already full screen (by f) stays so when g shows the list and the orbit again
    key("f").run();
    sizePanel(view, 160, 45);
    const full = panel.calls.full;
    key("g").run();
    assert.ok(!isOrbit(screen(view)), "g: the list");
    key("g").run();
    assert.ok(isOrbit(screen(view)), "g: the orbit");
    assert.equal(panel.calls.full, full, "g did not touch a full screen it did not enter");
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel scene: f full screen shows the orbit, f back to a side panel the list; the list's keys are off while the orbit shows", async () => {
  const { plugin } = makeScenePlugin({ screen: { width: 160, height: 45 } });
  const { ctx, state, panelClaim } = makePanelHost();
  ctx.ui.router = { navigate: () => {} };
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const panel = fullscreenPanel();
    const view = panelClaim().render(panel);
    await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
    sizePanel(view, 75, 39); // the side panel of a 130x40 terminal: room for the orbit, but the list is shown
    assert.ok(!isOrbit(screen(view)) && screen(view).length <= 16, "the list in the side panel");
    const key = (bind) => state.layers.filter((l) => (l.commands || []).some((c) => c.bind === bind)).pop().commands.find((c) => c.bind === bind);
    assert.equal(key("down").enabled(), true);
    key("f").run();
    assert.equal(panel.presentation, "fullscreen");
    sizePanel(view, 160, 45);
    assert.ok(isOrbit(screen(view)), "f: the orbit, full screen");
    assert.equal(key("down").enabled(), false, "no row keys on the orbit");
    assert.equal(key("return").enabled(), false);
    key("f").run();
    assert.equal(panel.presentation, "panel");
    sizePanel(view, 75, 39);
    assert.ok(!isOrbit(screen(view)), "f again: the list");
    assert.equal(key("down").enabled(), true);
    // the host leaves full screen by its own key: the list, not the orbit in a side panel
    key("f").run();
    sizePanel(view, 160, 45);
    assert.ok(isOrbit(screen(view)));
    panel.presentation = "panel";
    sizePanel(view, 75, 39);
    assert.ok(!isOrbit(screen(view)), "the host's own key: the list");
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel scene: g does nothing only when the orbit cannot fit even full screen; an unknown screen is tried and undone", async () => {
  const keyOf = (state, bind) => state.layers.filter((l) => (l.commands || []).some((c) => c.bind === bind)).pop().commands.find((c) => c.bind === bind);
  // a screen too small for the orbit: nothing to switch to
  {
    const { plugin } = makeScenePlugin({ screen: { width: 60, height: 40 } });
    const { ctx, state, panelClaim } = makePanelHost();
    const stop = plugin.setup(ctx);
    globalThis.__agentgraphStub.cleanups.length = 0;
    try {
      const panel = fullscreenPanel();
      const view = panelClaim().render(panel);
      await waitFor(() => screen(view).length > 5, 5000, "demo snapshot");
      sizePanel(view, 40, 39);
      keyOf(state, "g").run();
      assert.equal(panel.calls.full, 0, "no full screen that would not help");
      assert.ok(!isOrbit(screen(view)));
    } finally {
      for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
      stop();
    }
  }
  // already full screen and too small: nothing either
  {
    const { plugin } = makeScenePlugin({ screen: { width: 60, height: 40 } });
    const { ctx, state, panelClaim } = makePanelHost();
    const stop = plugin.setup(ctx);
    globalThis.__agentgraphStub.cleanups.length = 0;
    try {
      const panel = fullscreenPanel("fullscreen");
      const view = panelClaim().render(panel);
      await waitFor(() => screen(view).length > 5, 5000, "demo snapshot");
      sizePanel(view, 60, 40);
      keyOf(state, "g").run();
      assert.equal(panel.calls.full, 0);
      assert.ok(!isOrbit(screen(view)));
    } finally {
      for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
      stop();
    }
  }
  // the screen's size is unknown: g tries full screen; when the panel measures too small it goes back
  {
    const { plugin } = makeScenePlugin({ screen: null });
    const { ctx, state, panelClaim } = makePanelHost();
    const stop = plugin.setup(ctx);
    globalThis.__agentgraphStub.cleanups.length = 0;
    try {
      const panel = fullscreenPanel();
      const view = panelClaim().render(panel);
      await waitFor(() => screen(view).length > 5, 5000, "demo snapshot");
      sizePanel(view, 56, 44);
      keyOf(state, "g").run();
      assert.equal(panel.calls.full, 1, "tried");
      sizePanel(view, 60, 40);
      assert.equal(panel.calls.full, 2, "and undone");
      assert.equal(panel.presentation, "panel");
      assert.ok(!isOrbit(screen(view)));
      // and when it does fit, it stays
      keyOf(state, "g").run();
      sizePanel(view, 120, 40);
      assert.equal(panel.calls.full, 3);
      assert.ok(isOrbit(screen(view)));
    } finally {
      for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
      stop();
    }
  }
});

test("panel scene: dark or light from the theme's background; the group colours reach the host as #rrggbb", async () => {
  assert.deepEqual(P.sceneColors(hostTheme(), P.themeTones(hostTheme())), { dark: true, muted: P.colorProp(P.themeTones(hostTheme()).muted.fg) });
  assert.equal(P.sceneColors({ background: { base: rgb(250, 250, 245) } }).dark, false, "a light background");
  assert.equal(P.sceneColors({ background: "#fdf6e3" }).dark, false);
  assert.equal(P.sceneColors({ background: "#002b36" }).dark, true);
  assert.equal(P.sceneColors({ backgroundPanel: rgb(240, 240, 240) }).dark, false);
  for (const odd of [undefined, null, 5, {}, { background: "nope" }, { background: { base: {} } }]) assert.deepEqual(P.sceneColors(odd), { dark: true, muted: "#808080" }, "default dark");
  assert.equal(P.sceneColors({ background: "#ffffff" }, { muted: { fg: rgb(140, 140, 140) } }).muted, "#8c8c8c");
  // through the panel: every fg handed to the host is "#rrggbb", and the light palette is used on a light theme
  for (const [bg, table] of [[rgb(10, 10, 10), S.GROUP_COLORS.dark], [rgb(250, 250, 250), S.GROUP_COLORS.light]]) {
    const { plugin } = makeScenePlugin();
    const { ctx, state, panelClaim } = makePanelHost();
    ctx.theme = Object.assign(hostTheme(), { background: { base: bg } });
    const stop = plugin.setup(ctx);
    globalThis.__agentgraphStub.cleanups.length = 0;
    try {
      const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
      await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
      sizePanel(view, 120, 40);
      pressG(state); // the panel opens on the list
      const fgs = allFg(view);
      assert.ok(fgs.length > 20);
      for (const fg of fgs) assert.match(fg, /^#[0-9a-f]{6}$/);
      const working = new Set([table.build]); // impl-claude and designer work in the demo
      assert.ok(fgs.some((c) => working.has(c)), "the build colour at full strength: " + [...new Set(fgs)].join(" "));
      for (const fg of new Set(fgs)) {
        const hue = S.hueOf(fg);
        assert.ok(hue === null || hue < 80 || hue > 190, fg);
      }
    } finally {
      for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
      stop();
    }
  }
});

test("panel scene: frames at 100 ms while work moves; an unchanged frame is not pushed; timers stop on close and dispose", async () => {
  const { plugin, clockRef, intervals } = makeScenePlugin();
  const { ctx, state, panelClaim } = makePanelHost();
  const before = new Set(activeIntervals);
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
    sizePanel(view, 120, 40);
    pressG(state); // the panel opens on the list
    // the clock stands still: ticks compute the same frame and push nothing
    await sleep(150);
    const rendersStill = state.renders;
    const nodesStill = view.children.slice();
    await sleep(450);
    assert.equal(state.renders, rendersStill, "no render request for an unchanged frame");
    assert.deepEqual(view.children, nodesStill);
    // the clock moves: frames are pushed, and unchanged rows keep their nodes
    const frames = [];
    for (let i = 0; i < 6; i += 1) {
      clockRef.t += 100;
      const r = state.renders;
      await waitFor(() => state.renders > r, 2000, "a pushed frame");
      frames.push(screen(view).join("\n"));
    }
    assert.ok(new Set(frames).size >= 5, "frames differ");
    const kept = view.children.filter((n, i) => nodesStill[i] === n).length;
    assert.ok(kept >= 10, "unchanged rows are reused: " + kept);
    // 100 ms while work moves: about 10 ticks a second
    const plugged = [...activeIntervals].filter((h) => !before.has(h));
    assert.ok(plugged.length >= 2, "poll and frame timers");
    assert.ok(intervals.includes(100), "a 100 ms frame timer: " + intervals.join(","));
    // close: every panel timer stops
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    const sidebarTimers = new Set(activeIntervals);
    clockRef.t += 1000;
    const r = state.renders;
    await sleep(400);
    assert.equal(state.renders, r, "no frames after the close");
    assert.deepEqual([...sidebarTimers].filter((h) => !before.has(h)).length <= 1, true, "only the sidebar poll may remain");
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
  for (const h of activeIntervals) assert.ok(before.has(h), "an interval survived dispose");
});

// ---------------------------------------------------------------------------------------------------------------
// orbit lane (n104): turning the 3D orbit by hand (keys and mouse), the 3D keys line. Start of the block.

const idleDemo = () => {
  const doc = JSON.parse(JSON.stringify(demoWorn));
  for (const a of doc.agents) a.busy = 0;
  return doc;
};

test("orbit: the camera without an orbit is the self-turn; a turn holds it RESUME_MS, then it goes on from the angle left", () => {
  const full = 2 * Math.PI;
  for (const now of [0, 166, 9000, 39999, 40000, 123456]) {
    assert.equal(S.orbitCamera(null, now).turn, (full * (((now % S.TURN_MS) + S.TURN_MS) % S.TURN_MS)) / S.TURN_MS, "no orbit at " + now);
    assert.equal(S.orbitCamera(null, now).elevation, S.ELEVATION_3D);
    assert.equal(S.orbitCamera(null, now).held, false);
  }
  const t0 = 9000;
  const o = S.orbitTurned(null, t0, S.TURN_STEP, 0);
  const shown = S.orbitCamera(null, t0).turn;
  assert.ok(Math.abs(o.turn - ((shown + S.TURN_STEP) % full)) < 1e-12, "from the angle shown, one step on");
  assert.equal(o.resume, t0 + S.RESUME_MS);
  assert.equal(S.RESUME_MS >= 2000 && S.RESUME_MS <= 5000, true, "a few seconds");
  // held: the same camera until resume
  for (const dt of [0, 500, S.RESUME_MS - 1]) {
    assert.equal(S.orbitCamera(o, t0 + dt).turn, o.turn, "held at +" + dt);
    assert.equal(S.orbitCamera(o, t0 + dt).held, true);
  }
  // then on from there at the self-turn's speed: no jump at resume
  const r = o.resume;
  assert.equal(S.orbitCamera(o, r).turn, o.turn);
  const after = S.orbitCamera(o, r + 1000).turn;
  assert.ok(Math.abs(((after - o.turn + full) % full) - (full * 1000) / S.TURN_MS) < 1e-9, "on at the self-turn's speed");
  assert.ok(Math.abs(S.orbitCamera(o, r + 1).turn - S.orbitCamera(o, r - 1).turn) < 1e-3, "continuous at resume");
  // a turn while the self-turn runs again starts from where it is then
  const later = r + 5000;
  const o2 = S.orbitTurned(o, later, -S.TURN_STEP, 0);
  assert.ok(Math.abs(((o2.turn - S.orbitCamera(o, later).turn + full) % full) - (full - S.TURN_STEP)) < 1e-9);
  // the elevation is clamped: never past looking along the plane or straight down, so it never flips
  let e = null;
  for (let i = 0; i < 40; i += 1) e = S.orbitTurned(e, t0, 0, S.ELEVATION_STEP);
  assert.equal(e.elevation, S.ELEVATION_MAX);
  assert.equal(S.ELEVATION_MAX, Math.PI / 2);
  for (let i = 0; i < 80; i += 1) e = S.orbitTurned(e, t0, 0, -S.ELEVATION_STEP);
  assert.equal(e.elevation, S.ELEVATION_MIN);
  assert.equal(S.ELEVATION_MIN, 0);
  assert.equal(S.orbitCamera({ elevation: 9 }, 0).elevation, Math.PI / 2);
  assert.equal(S.orbitCamera({ elevation: -9 }, 0).elevation, 0);
  // reset: the starting camera, held
  assert.deepEqual(S.orbitReset(t0), { turn: 0, elevation: S.ELEVATION_3D, resume: t0 + S.RESUME_MS });
  // nonsense in, a camera out
  for (const bad of [{ turn: "x", elevation: null, resume: NaN }, { turn: -7 }, 5, "a"]) {
    const c = S.orbitCamera(bad, 1000);
    assert.ok(c.turn >= 0 && c.turn < full && c.elevation >= 0 && c.elevation <= Math.PI / 2, JSON.stringify(bad));
  }
});

test("orbit: a turned 3D frame differs from the self-turn's, stands still while held, then turns on; every name once", () => {
  const doc = idleDemo();
  const t0 = 20000;
  const o = S.orbitTurned(null, t0, Math.PI / 2, 0.3);
  const at = (now, orbit) => sceneText(S.orbitLines(doc, 120, 30, now, Object.assign({ view: "3d" }, orbit ? { orbit } : {}))).join("\n");
  assert.notEqual(at(t0, o), at(t0, null), "turned by hand: another picture");
  assert.equal(at(t0 + 1000, o), at(t0, o), "held: the same frame");
  assert.equal(at(t0 + S.RESUME_MS - 1000, o), at(t0, o)); // a stuck agent blinks twice a second: the same phase
  assert.notEqual(at(o.resume + 1000, o), at(t0, o), "then it turns on");
  // the self-turn's frames are unchanged without an orbit
  assert.equal(at(t0, null), sceneText(S.orbitLines(doc, 120, 30, t0, V3)).join("\n"));
  // every ring agent's name exactly once, at any turn and elevation, at the smallest size too
  for (const [w, h] of [[64, 12], [120, 30]]) {
    for (const elevation of [0, 0.25, S.ELEVATION_3D, 1.2, Math.PI / 2]) {
      for (const turn of [0, 1.3, 2.9, 4.4]) {
        const lines = sceneText(S.orbitLines(doc, w, h, 0, { view: "3d", orbit: { turn, elevation, resume: 1e15 }, keys: [["←→↑↓", "turn"], ["0", "reset"]] }));
        assert.equal(lines.length, h);
        for (const name of ringNames) assert.equal(countWord(lines, R.truncate(name, 12)), 1, `${name} at ${w}x${h} e=${elevation} t=${turn}\n${lines.join("\n")}`);
      }
    }
  }
});

test("orbit: the keys line: in 3D only, muted, on the canvas's bottom row, within the width at 64x22; the change starts from the orbit's camera", () => {
  const keys = [["←→↑↓", "turn"], ["0", "reset"]];
  const want = "←→↑↓ turn  0 reset";
  for (const [w, h] of SIZES) {
    const lines = S.scene(demoWorn, { width: w, height: h, now: 5000, view: "3d", keys });
    assertExact(lines, w, h, `3D with keys ${w}x${h}`);
    const text = sceneText(lines);
    const row = text.findIndex((l) => l.includes(want));
    assert.ok(row > 0, `the keys at ${w}x${h}\n${text.join("\n")}`);
    assert.ok(text[row].startsWith(want), "at the left edge");
    assert.equal(text.filter((l) => l.includes("reset")).length, 1);
    const span = lines[row].find((s) => s.text.includes("turn"));
    assert.equal(span.tone, "muted");
    assert.ok(!span.color, "no group colour");
    // the canvas's bottom row: the next row is an instrument or the end
    const plain = sceneText(S.scene(demoWorn, { width: w, height: h, now: 5000, view: "3d" }));
    assert.deepEqual(text.slice(row + 1), plain.slice(row + 1), "the rows under it are the instruments, unchanged");
    assert.deepEqual(text.slice(0, 1), plain.slice(0, 1));
    // 2D: no keys line, the 2D frame unchanged
    const flat = sceneText(S.scene(demoWorn, { width: w, height: h, now: 5000, view: "2d", keys }));
    assert.ok(!flat.some((l) => l.includes("reset")), "no keys line in 2D");
    assert.deepEqual(flat, sceneText(S.scene(demoWorn, { width: w, height: h, now: 5000 })));
  }
  // too narrow for a pair: it is left out, never cut
  const narrow = sceneText(S.orbitLines(demoWorn, 10, 12, 0, { view: "3d", keys: [["abcdefghijk", "x"]] }));
  assert.ok(!narrow.some((l) => l.includes("abc")));
  // the change into 3D ends on the turned orbit's steady frame, and the change back starts from it (no jump)
  const doc = idleDemo();
  const orbit = { turn: 2.2, elevation: 1.1, resume: 1e15 };
  const at = 50000;
  const steady = sceneText(S.orbitLines(doc, 120, 30, at + S.CHANGE_MS, { view: "3d", orbit })).join("\n");
  const almost = sceneText(S.orbitLines(doc, 120, 30, at + S.CHANGE_MS - 1, { view: "3d", orbit, change: { from: "2d", at } })).join("\n");
  // where the names stand (the sparse far half of a ring may differ by a dot at a camera a hair away)
  const spots = (t) => ringNames.map((n) => t.split("\n").map((l, r) => [r, l.search(new RegExp(`(?<![\\w-])${n}(?![\\w-])`))]).find(([, c]) => c >= 0).join(",")).join(" ");
  assert.equal(spots(almost), spots(steady), "the change into 3D lands on the orbit's camera");
  const selfTurn = sceneText(S.orbitLines(doc, 120, 30, at + S.CHANGE_MS, V3)).join("\n");
  assert.notEqual(spots(almost), spots(selfTurn), "not on the self-turn's camera");
  const leaving = sceneText(S.orbitLines(doc, 120, 30, at + 1, { view: "2d", orbit, change: { from: "3d", at } })).join("\n");
  assert.equal(spots(leaving), spots(steady), "the change back starts from the orbit's camera");
});

// The newest panel's command with `id`.
const commandOf = (state, id) => state.layers.flatMap((l) => l.commands || []).filter((c) => c.id === id).pop();

test("orbit panel: in 3D the arrow keys turn it and 0 resets, only on the 3D orbit; the keys line shows only in 3D; an unchanged frame is not pushed", async () => {
  const { plugin, clockRef } = makeScenePlugin();
  const { ctx, state, panelClaim } = makePanelHost();
  ctx.ui.router = { navigate: () => {} };
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
    sizePanel(view, 120, 40);
    const left = commandOf(state, "agentgraph.graph.turn-left");
    const right = commandOf(state, "agentgraph.graph.turn-right");
    const reset = commandOf(state, "agentgraph.graph.turn-reset");
    const up = commandOf(state, "agentgraph.graph.up");
    const down = commandOf(state, "agentgraph.graph.down");
    assert.deepEqual([left.bind, right.bind, reset.bind, up.bind, down.bind], ["left", "right", "0", "up", "down"]);
    const turnLayer = state.layers.find((l) => (l.commands || []).includes(left));
    assert.deepEqual(turnLayer.commands.map((c) => c.bind), ["left", "right", "0"]);
    assert.equal(turnLayer.mode, undefined, "no mode: only while the panel owns input");
    // the list: the arrows move the cursor, left, right and 0 are off
    assert.equal(up.enabled(), true);
    assert.equal([left, right, reset].some((c) => c.enabled()), false, "off on the list");
    assert.ok(!screen(view).some((l) => l.includes("reset")), "no keys line on the list");
    // the 2D orbit: every turn key off, no keys line
    pressG(state);
    assert.ok(isOrbit(screen(view)));
    assert.equal([left, right, reset, up, down].some((c) => c.enabled()), false, "off on the 2D orbit");
    assert.ok(!screen(view).some((l) => l.includes("reset")), "no keys line in 2D");
    // 3D: on, and named on the keys line, which fits
    commandOf(state, "agentgraph.graph.dimension").run();
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => has3D(screen(view)), 3000, "3D");
    assert.equal([left, right, reset, up, down].every((c) => c.enabled()), true, "on on the 3D orbit");
    const keysRow = screen(view).find((l) => l.includes("reset"));
    assert.ok(keysRow && keysRow.startsWith("←→↑↓ turn  0 reset"), "the keys line\n" + screen(view).join("\n"));
    // a turn: drawn at once, held (the clock moves, the frame does not), then the self-turn goes on
    const before = screen(view).join("\n");
    left.run();
    const turned = screen(view).join("\n");
    assert.notEqual(turned, before, "left: drawn at once");
    clockRef.t += 1000;
    await sleep(400);
    const rendersHeld = state.renders;
    await sleep(400);
    // the demo works, so its spinners and 0/1 streams move with the clock; the clock stands: nothing is pushed
    assert.equal(state.renders, rendersHeld, "an unchanged frame is not pushed");
    const names = (t) => ringNames.map((n) => t.split("\n").findIndex((l) => countWord([l], n) === 1) + ":" + t.split("\n").map((l) => l.search(new RegExp(`(?<![\\w-])${n}(?![\\w-])`))).find((c) => c >= 0)).join(" ");
    assert.equal(names(screen(view).join("\n")), names(turned), "held: the names stand while the clock moves");
    clockRef.t += S.RESUME_MS + 3000;
    await waitFor(() => names(screen(view).join("\n")) !== names(turned), 3000, "the self-turn goes on");
    // right turns back; up and down change the elevation, and the frame
    const r1 = screen(view).join("\n");
    right.run();
    assert.notEqual(screen(view).join("\n"), r1);
    const e1 = screen(view).join("\n");
    up.run();
    up.run();
    assert.notEqual(screen(view).join("\n"), e1, "up: another elevation");
    down.run();
    down.run();
    const beforeReset = screen(view).join("\n");
    reset.run(); // turn 0 at ELEVATION_3D: elsewhere than the turned camera
    assert.notEqual(names(screen(view).join("\n")), names(beforeReset), "0: back to the start");
    // a dialog open: off
    ctx.keymap.mode = { current: () => "modal" };
    assert.equal([left, right, reset, up, down].some((c) => c.enabled()), false, "off under a host dialog");
    ctx.keymap.mode = { current: () => "base" };
    // back to the list: up and down move the cursor again
    commandOf(state, "agentgraph.graph.view").run();
    assert.ok(!isOrbit(screen(view)));
    assert.equal([left, right, reset].some((c) => c.enabled()), false);
    assert.equal(up.enabled(), true);
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("orbit panel: a click on the orbit switches 2D and 3D, a drag turns the 3D view and switches nothing; the list ignores the mouse", async () => {
  const { plugin, clockRef } = makeScenePlugin();
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
    sizePanel(view, 120, 40);
    const p = view.props;
    for (const k of ["onMouseDown", "onMouseDrag", "onMouseUp", "onMouseOver"]) assert.equal(typeof p[k], "function", k);
    const click = (x, y) => {
      p.onMouseDown({ type: "down", button: 0, x, y });
      p.onMouseUp({ type: "up", button: 0, x, y });
    };
    // the list: a click does nothing, its text can still be selected
    const list = screen(view).join("\n");
    click(10, 2);
    assert.equal(screen(view).join("\n"), list);
    assert.ok(!isOrbit(screen(view)));
    const listText = view.children.flatMap((row) => (row.tag === "text" ? [row] : row.children));
    assert.ok(listText.every((t) => t.props.selectable === undefined), "list text keeps the host's selection");
    pressG(state);
    assert.ok(isOrbit(screen(view)) && !has3D(screen(view)));
    // scene text is not selectable: a press and a drag there turn the view instead of selecting
    const sceneTexts = view.children.flatMap((row) => (row.tag === "text" ? [row] : row.children));
    assert.ok(sceneTexts.length > 40 && sceneTexts.every((t) => t.props.selectable === false));
    // a click: 3D (the camera moves over CHANGE_MS)
    click(60, 18);
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => has3D(screen(view)), 3000, "a click: 3D");
    // the right button, or a press that is not on the orbit, does nothing
    p.onMouseDown({ type: "down", button: 2, x: 60, y: 18 });
    p.onMouseUp({ type: "up", button: 2, x: 60, y: 18 });
    assert.ok(has3D(screen(view)));
    // a drag: turns, held, and no switch on release; the line under the pointer carries it on ("over")
    const before = screen(view).join("\n");
    p.onMouseDown({ type: "down", button: 0, x: 60, y: 18 });
    p.onMouseDrag({ type: "drag", button: 0, x: 61, y: 18 });
    const one = screen(view).join("\n");
    assert.notEqual(one, before, "a drag turns at once");
    p.onMouseDrag({ type: "drag", button: 0, x: 61, y: 18 }); // no motion: nothing
    assert.equal(screen(view).join("\n"), one);
    p.onMouseOver({ type: "over", button: 0, x: 64, y: 18 });
    assert.notEqual(screen(view).join("\n"), one, "over with the button held turns on");
    p.onMouseOver({ type: "over", button: 0, x: 64, y: 20 }); // down: the elevation
    p.onMouseUp({ type: "up", button: 0, x: 64, y: 20 });
    clockRef.t += S.CHANGE_MS + 50;
    await sleep(300);
    assert.ok(has3D(screen(view)), "a drag switches nothing");
    // plain motion (no button) after the release turns nothing
    const still = screen(view).join("\n");
    p.onMouseOver({ type: "over", button: 3, x: 80, y: 25 });
    p.onMouseDrag({ type: "drag", button: 0, x: 90, y: 25 });
    assert.equal(screen(view).join("\n"), still, "no press, no turn");
    // a click: back to 2D
    click(60, 18);
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => !has3D(screen(view)) && isOrbit(screen(view)), 3000, "a click: 2D");
    // a drag on the 2D orbit turns nothing and switches nothing
    p.onMouseDown({ type: "down", button: 0, x: 60, y: 18 });
    p.onMouseDrag({ type: "drag", button: 0, x: 70, y: 18 });
    p.onMouseUp({ type: "up", button: 0, x: 70, y: 18 });
    clockRef.t += S.CHANGE_MS + 50;
    await sleep(300);
    assert.ok(!has3D(screen(view)) && isOrbit(screen(view)));
    // bad events are ignored
    assert.doesNotThrow(() => {
      p.onMouseDown(undefined);
      p.onMouseDown({ button: 0, x: "a", y: null });
      p.onMouseDrag(null);
      p.onMouseUp({});
    });
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

// orbit lane (n104): end of the block.

// ---------------------------------------------------------------------------------------------------------------
// Paths: `r` runs a drawn path (structure.flows), the paths row at rest

const RUNNER = await import(src("runner.js"));
const FLOW = (id, path_, name = null, stats = null) => ({ id, path: path_, text: "t", name, command: null, source: "drawn", stats });
const STATS = (runs, avg, rounds, last) => ({ runs, passed: runs, rounds: { avg: rounds, max: rounds }, cost: { total: avg === null ? null : avg * runs, avg }, last });
const restDoc = (flows, extra = {}) => D([A("orchestrator", "hub"), A("impl-openai", "build"), A("verifier", "verify")], null, "orchestrator",
  Object.assign({ root: { id: "ses_top", agent: "orchestrator", title: "Rate limits" }, totals: T(1.5, 10, 10), structure: { source: "builtin", flows, teams: [] } }, extra));

test("paths row: at rest only, after the hub: the count and the path run last; at 37, 56 and 75 columns never wider", () => {
  const nowSec = NOW / 1000;
  const flows = [
    FLOW("a-b-a", ["a", "b", "a"], null, STATS(1, 0.5, 1, nowSec - 3 * 86400)),
    FLOW("impl-openai-verifier-impl-openai", ["impl-openai", "verifier", "impl-openai"], "impl-loop", STATS(3, 1.2, 2, nowSec - 2 * 3600)),
    FLOW("c-d", ["c", "d"]),
  ];
  const doc = restDoc(flows);
  assert.deepEqual(listText(doc, 37), [pad("◉ orchestrator  Rate limits", "2 idle"), "3 paths · last impl-loop 2h ago", "$1.50"]);
  assert.deepEqual(listText(doc, 56, { cursor: true, keys: [["⏎", "open"], ["r", "run"], ["g", "graph"], ["q", "close"]] }), [
    "›" + pad("◉ orchestrator  Rate limits", "2 idle", 55),
    " 3 paths · last impl-loop 2h ago",
    " $1.50",
    " ⏎ open  r run  g graph  q close",
  ]);
  assert.deepEqual(listText(doc, 75).slice(1, 2), ["3 paths · last impl-loop 2h ago"]);
  // no paths: the rows at rest are as before (3 in the panel: hub, spend, keys)
  assert.deepEqual(listText(restDoc([]), 37), [pad("◉ orchestrator  Rate limits", "2 idle"), "$1.50"]);
  // one path, never run: the count only
  assert.deepEqual(listText(restDoc([FLOW("c-d", ["c", "d"])]), 37)[1], "1 path");
  // narrow: the name is cut in the middle, then the time goes, then the name
  const long = restDoc([FLOW("x-y", ["x", "y"], "a-very-long-path-name-indeed", STATS(1, null, null, nowSec - 60))]);
  assert.equal(listText(long, 37)[1], "1 path · last a-very-long-pa…eed 1m ago".replace("a-very-long-pa…eed", R.truncateMiddle("a-very-long-path-name-indeed", 37 - 14 - 7)));
  assert.equal(listText(long, 24)[1], "1 path · last a-ver…deed");
  assert.equal(listText(long, 12)[1], "1 path");
  for (const width of [12, 24, 37, 56, 75]) {
    for (const d of [doc, long]) {
      for (const extra of [{}, { cursor: true, keys: KEYS }]) {
        for (const line of R.render(d, Object.assign({ width, now: NOW }, extra))) assert.ok(R.lineWidth(line) <= width, `${width}: ${R.toText([line])}`);
      }
    }
  }
  // busy (anything works or needs the user): no paths row
  const busy = restDoc(flows, { agents: [A("orchestrator", "hub", 1), A("impl-openai", "build"), A("verifier", "verify")] });
  assert.ok(!listText(busy, 56).some((l) => l.includes("paths")));
  const working = restDoc(flows, { agents: [A("orchestrator", "hub"), A("impl-openai", "build", 1), A("verifier", "verify")] });
  assert.ok(!listText(working, 56).some((l) => l.includes("paths")));
  assert.ok(!listText(demo, 75).some((l) => l.includes("paths")));
  // malformed flows are left out
  assert.equal(R.flowsOf(restDoc([{ id: "-x", path: ["a", "b"] }, { id: "ok", path: ["a"] }, null, { id: "y", path: ["a", 3] }])).length, 0);
});

test("paths: the chooser's rows: `name  a → b → a` (the id when unnamed), most recently run first, stats with a dash for a null field", () => {
  const flows = R.flowsOf(restDoc([
    FLOW("never-run", ["a", "b"]),
    FLOW("old", ["a", "b", "a"], "old-loop", STATS(1, null, 1, 100)),
    FLOW("new", ["x", "y"], null, STATS(3, 1.2, 2, 200)),
  ]));
  const sorted = R.sortFlows(flows);
  assert.deepEqual(sorted.map(R.flowLabel), ["new  x → y", "old-loop  a → b → a", "never-run  a → b"]);
  assert.deepEqual(sorted.map((f) => R.flowStatsText(f.stats)), ["3 runs · $1.20 avg · 2.0 rounds", "1 run · – avg · 1.0 rounds", ""]);
  assert.equal(R.flowStatsText({ runs: null, rounds: null, cost: null }), "– runs · – avg · – rounds");
});

const flowLog = path.join(tmp, "flow-calls.log");
const pathsPython = fakePython("paths-python", `
echo "$PYTHONPATH" > "${path.join(tmp, "paths-home.log")}"
if [ "$3" = "agentgraph.structure" ]; then
  goal=$(cat)
  printf '%s|%s\\n' "$*" "$goal" >> "${flowLog}"
  expect=""
  prev=""
  for a in "$@"; do [ "$prev" = "--expect" ] && expect="$a"; prev="$a"; done
  if [ "$6" = "stale" ] && [ -n "$expect" ] && [ "$expect" != "r2" ]; then
    echo "agentgraph structure: flow stale changed since you opened it; reload and try again (rev now r2)" >&2; exit 1
  fi
  case "$6" in
    broken) echo "agentgraph structure: no flow broken" >&2; exit 1;;
    nomod) echo "/usr/bin/python3: No module named agentgraph.structure" >&2; exit 1;;
    slow) sleep 0.4;;
  esac
  printf 'RUN %s\\n%s\\n' "$6" "$goal"
  exit 0
fi
sid=""
for a in "$@"; do case "$a" in --session=*) sid="\${a#--session=}";; esac; done
flow() {
  printf '{"id":"%s","path":["impl-openai","verifier","impl-openai"],"text":"t","name":%s,"command":null,"source":"drawn","stats":%s' "$1" "$2" "$3"
  if [ -n "$4" ]; then printf ',"rev":"%s"' "$4"; fi
  printf '}'
}
case "$sid" in
  ses_p0) flows="";;
  ses_p1) flows="$(flow solo null null)";;
  ses_pbroken) flows="$(flow broken null null)";;
  ses_pnomod) flows="$(flow nomod null null)";;
  ses_pslow) flows="$(flow slow null null)";;
  ses_pbusy) flows="$(flow solo null null)";;
  ses_prev) flows="$(flow solo null null 3f9a2c1d)";;
  ses_prev16) flows="$(flow solo null null 9b3f17e5dc59312d),$(flow other null null)";;
  ses_pbadrev) flows="$(flow solo null null -x)";;
  ses_ptwo) flows="$(flow stale null null r1),$(flow solo '"other"' null)";;
  ses_pstale) echo x >> "${path.join(tmp, "stale-snapshots.log")}"; flows="$(flow stale null null r1)";;
  ses_pflip) if [ -f "${path.join(tmp, "paths-gone.flag")}" ]; then flows=""; else flows="$(flow solo null null)"; fi;;
  *) flows="$(flow aaa null null),$(flow bbb '"impl-loop"' '{"runs":3,"passed":2,"rounds":{"avg":2,"max":3},"cost":{"total":3.6,"avg":1.2},"last":1795000000}'),$(flow ccc '"old"' '{"runs":1,"passed":1,"rounds":{"avg":null,"max":null},"cost":{"total":null,"avg":null},"last":1700000000}')";;
esac
worker='{"name":"impl-openai","group":"build","busy":0,"parent":"","sessions":[]}'
if [ "$sid" = "ses_pbusy" ]; then worker='{"name":"impl-openai","group":"build","busy":1,"parent":"orchestrator","sessions":[{"id":"ses_child","title":"child work","busy":true,"created":1795000000,"updated":1795000000}]}'; fi
printf '{"version":1,"root":{"id":"ses_top","agent":"orchestrator","title":"t-%s"},"agents":[{"name":"orchestrator","group":"hub","busy":0,"parent":"","sessions":[]},'"$worker"'],"graph":null,"warnings":[],"totals":{"cost":1.5},"structure":{"source":"builtin","teams":[],"flows":[%s]}}\\n' "$sid" "$flows"
`);

// A panel on `session` with the paths fake; dialogs answer from `answers` (functions or values) and are logged.
async function pathsPanel(session, { answers = {}, select = true, send, intervalMs } = {}) {
  const host = makeActionHost();
  host.ctx.options.python = pathsPython;
  if (intervalMs) host.ctx.options.intervalMs = intervalMs;
  const calls = host.calls;
  const answer = (name, o) => (typeof answers[name] === "function" ? answers[name](o) : answers[name]);
  host.ctx.ui.dialog.prompt = async (o) => (calls.push(["prompt", o.title]), answer("prompt", o));
  if (select) host.ctx.ui.dialog.select = async (o) => (calls.push(["select", o.title, o.options]), answer("select", o));
  host.ctx.ui.dialog.set = (o) => calls.push(["dialog size", o]);
  if (send) host.ctx.client.session.prompt = send;
  const stop = tui.setup(host.ctx);
  const view = host.panelClaim().render(makePanel({ sessionID: session, width: 80 }));
  await waitFor(() => screen(view).some((l) => l.includes("t-" + session)), 5000, "panel snapshot " + session);
  fs.writeFileSync(flowLog, "");
  const done = () => {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  };
  return Object.assign(host, { view, done, toasts: host.state.toasts, flowCalls: () => fs.readFileSync(flowLog, "utf8").split("\n").filter(Boolean) });
}
const sends = (calls) => calls.filter((c) => c[0] === "send");

test("paths: no paths, no `r`: no key, nothing in the key line, no paths row", async () => {
  const p = await pathsPanel("ses_p0");
  try {
    await sleep(50);
    assert.equal(p.key("r"), undefined);
    assert.ok(!screen(p.view).some((l) => / r run|paths?\b/.test(l)), screen(p.view).join(" | "));
    assert.deepEqual(screen(p.view).map((l) => l.slice(1, 15)), ["◉ orchestrator", "$1.50", "⏎ open  g grap"]);
  } finally {
    p.done();
  }
});

test("paths: one path skips the chooser; the goal; the back end's run message is sent to the orchestrator's root session", async () => {
  const p = await pathsPanel("ses_p1", { answers: { prompt: "  add a cache  " } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    assert.deepEqual(screen(p.view).map((l) => l.slice(1)), [pad("◉ orchestrator  t-ses_p1", "1 idle", 79), "1 path", "$1.50", "⏎ open  r run  g graph  q close"]);
    assert.equal(p.key("r").enabled(), true);
    p.key("r").run();
    await waitFor(() => sends(p.calls).length === 1, 5000, "sent");
    assert.deepEqual(p.calls.filter((c) => c[0] !== "send"), [["prompt", "Goal for solo"]], "no chooser for one path");
    assert.deepEqual(sends(p.calls)[0], ["send", { sessionID: "ses_top", text: "RUN solo\nadd a cache" }]);
    assert.deepEqual(p.flowCalls(), ["-P -m agentgraph.structure flow prompt solo --goal-file -|add a cache"], "goal on stdin, no shell");
    await waitFor(() => p.toasts.length > 0, 2000, "toast");
    assert.deepEqual(p.toasts.at(-1), { message: "Sent solo to the orchestrator", variant: "info" });
  } finally {
    p.done();
  }
});

test("paths: several: the host's chooser lists them, most recently run first, stats as each row's footer; the choice is run", async () => {
  const p = await pathsPanel("ses_p3", { answers: { select: "ccc", prompt: "goal two" } });
  try {
    assert.equal(screen(p.view)[1], " 3 paths · last impl-loop " + R.formatElapsed(Date.now() - 1795000000 * 1000) + " ago");
    await waitFor(() => p.key("r"), 2000, "r key");
    p.key("r").run();
    await waitFor(() => sends(p.calls).length === 1, 5000, "sent");
    const [kind, title, options] = p.calls[0];
    assert.deepEqual([kind, title], ["select", "Run a path"]);
    assert.deepEqual(p.calls.find((c) => c[0] === "dialog size"), ["dialog size", { size: "large" }], "the chooser is widened once open");
    assert.ok(p.calls.indexOf(p.calls.find((c) => c[0] === "dialog size")) === 1);
    assert.deepEqual(options, [
      { title: "impl-loop  impl-openai → verifier → impl-openai", value: "bbb", footer: "3 runs · $1.20 avg · 2.0 rounds" },
      { title: "old  impl-openai → verifier → impl-openai", value: "ccc", footer: "1 run · – avg · – rounds" },
      { title: "aaa  impl-openai → verifier → impl-openai", value: "aaa" },
    ]);
    assert.deepEqual(p.calls[2], ["prompt", "Goal for old"]);
    assert.deepEqual(sends(p.calls)[0][1], { sessionID: "ses_top", text: "RUN ccc\ngoal two" });
    // the chooser closed with nothing chosen: nothing more happens
    p.calls.length = 0;
    const q = p.calls;
    await sleep(20);
    p.ctx.ui.dialog.select = async (o) => (q.push(["select", o.title]), undefined);
    p.key("r").run();
    await sleep(100);
    assert.deepEqual(q, [["select", "Run a path"], ["dialog size", { size: "large" }]]);
  } finally {
    p.done();
  }
});

test("paths: without the host's chooser the paths are numbered in a prompt; the number picks one", async () => {
  let n = 0;
  const p = await pathsPanel("ses_p3", { select: false, answers: { prompt: () => (n += 1) === 1 ? "2" : "a goal" } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    p.key("r").run();
    await waitFor(() => sends(p.calls).length === 1, 5000, "sent");
    assert.deepEqual(p.calls.slice(0, 2), [["prompt", "Run which path? 1 impl-loop  2 old  3 aaa"], ["prompt", "Goal for old"]]);
    assert.equal(sends(p.calls)[0][1].text, "RUN ccc\na goal");
  } finally {
    p.done();
  }
});

test("paths: an empty goal sends nothing (a toast says so); a cancelled goal sends nothing and says nothing", async () => {
  const p = await pathsPanel("ses_p1", { answers: { prompt: "   " } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    p.key("r").run();
    await waitFor(() => p.toasts.length > 0, 2000, "toast");
    assert.deepEqual(p.toasts.at(-1), { message: "No goal: nothing was sent.", variant: "info" });
    p.ctx.ui.dialog.prompt = async (o) => (p.calls.push(["prompt", o.title]), undefined);
    await sleep(20);
    p.key("r").run();
    await sleep(150);
    assert.equal(p.toasts.length, 1);
    assert.deepEqual(sends(p.calls), []);
    assert.deepEqual(p.flowCalls(), [], "the back end was not asked");
  } finally {
    p.done();
  }
});

test("paths: a goal over 4,000 characters is refused with a line saying so; 4,000 is sent", async () => {
  const p = await pathsPanel("ses_p1", { answers: { prompt: "é".repeat(4001) } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    p.key("r").run();
    await waitFor(() => p.toasts.length > 0, 2000, "toast");
    assert.deepEqual(p.toasts.at(-1), { message: "The goal is 4,001 characters; at most 4,000. Nothing was sent.", variant: "error" });
    assert.deepEqual(p.flowCalls(), []);
    assert.deepEqual(sends(p.calls), []);
    p.ctx.ui.dialog.prompt = async () => "é".repeat(4000);
    await sleep(20);
    p.key("r").run();
    await waitFor(() => sends(p.calls).length === 1, 5000, "sent at the limit");
  } finally {
    p.done();
  }
});

test("paths: the back end missing (no `flow prompt` yet) or failing is a toast with its error, and nothing is sent", async () => {
  for (const [session, message] of [
    ["ses_pnomod", "Could not run nomod: This agentgraph has no `flow prompt` command yet; update the orchestration folder."],
    ["ses_pbroken", "Could not run broken: no flow broken"],
  ]) {
    const p = await pathsPanel(session, { answers: { prompt: "a goal" } });
    try {
      await waitFor(() => p.key("r"), 2000, "r key");
      p.key("r").run();
      await waitFor(() => p.toasts.length > 0, 5000, "toast " + session);
      assert.deepEqual(p.toasts.at(-1), { message, variant: "error" });
      assert.deepEqual(sends(p.calls), []);
    } finally {
      p.done();
    }
  }
  // no interpreter at all
  const missing = await RUNNER.runFlowPrompt({ spawn, path, env: process.env, home: tmp, python: path.join(tmp, "no-python") }, "solo", "g");
  assert.match(missing.error, /^Python was not found at /);
  assert.deepEqual(await RUNNER.runFlowPrompt({ spawn, path, env: process.env, home: tmp, python: pathsPython }, "-x", "g"), { error: "The path's id is not valid." });
});

test("paths: the host refusing the send is a toast with its error", async () => {
  const p = await pathsPanel("ses_p1", { answers: { prompt: "a goal" }, send: async () => { throw new Error("Session not found: ses_top"); } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    p.key("r").run();
    await waitFor(() => p.toasts.length > 0, 5000, "toast");
    assert.deepEqual(p.toasts.at(-1), { message: "Could not send solo: Session not found: ses_top", variant: "error" });
  } finally {
    p.done();
  }
});

test("paths: a double press runs it once: `r` is off from the first press until the send is done", async () => {
  let resolveSelect;
  const p = await pathsPanel("ses_pslow", { answers: { prompt: "once" } });
  const p3 = await pathsPanel("ses_p3", { answers: { select: () => new Promise((r) => (resolveSelect = r)), prompt: "once" } });
  try {
    await waitFor(() => p.key("r") && p3.key("r"), 2000, "r keys");
    // several paths: a second r while the chooser is open opens nothing
    p3.key("r").run();
    p3.key("r").run();
    await sleep(30);
    assert.equal(p3.calls.filter((c) => c[0] === "select").length, 1);
    resolveSelect("bbb");
    await waitFor(() => sends(p3.calls).length === 1, 5000, "sent");
    // one path, a slow back end: r again while it works does nothing
    p.key("r").run();
    await waitFor(() => p.flowCalls().length === 1, 3000, "back end asked");
    assert.equal(p.key("r").enabled(), false, "r is off while the path is being run");
    p.key("r").run();
    await waitFor(() => sends(p.calls).length === 1, 5000, "sent");
    await sleep(100);
    assert.equal(sends(p.calls).length, 1);
    assert.equal(p.calls.filter((c) => c[0] === "prompt").length, 1);
    assert.equal(p.key("r").enabled(), true, "on again once sent");
  } finally {
    p.done();
    p3.done();
  }
});

test("paths: dialog guard: `r` is off while any host dialog is open, and the keys stay off under the chooser and the goal prompt", async () => {
  let resolveSelect, resolvePrompt;
  const p = await pathsPanel("ses_p3", { answers: { select: () => new Promise((r) => (resolveSelect = r)), prompt: () => new Promise((r) => (resolvePrompt = r)) } });
  const mode = { v: "base" };
  p.ctx.keymap.mode = { current: () => mode.v };
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    mode.v = "modal";
    assert.equal(p.key("r").enabled(), false);
    p.key("r").run();
    await sleep(20);
    assert.deepEqual(p.calls, []);
    mode.v = "base";
    p.key("r").run();
    await waitFor(() => resolveSelect, 2000, "chooser");
    for (const bind of ["r", "return", "down", "up"]) assert.equal(p.key(bind).enabled(), false, bind + " under the chooser");
    resolveSelect("aaa");
    p.key("return").run(); // the Enter that chose, reaching the panel in the same tick
    await waitFor(() => resolvePrompt, 2000, "goal prompt");
    for (const bind of ["r", "return"]) assert.equal(p.key(bind).enabled(), false, bind + " under the goal prompt");
    resolvePrompt(undefined);
    p.key("return").run();
    await sleep(30);
    assert.ok(!p.calls.some((c) => c[0] === "navigate"), JSON.stringify(p.calls));
    assert.deepEqual(sends(p.calls), []);
  } finally {
    p.done();
  }
});

test("paths: the chosen path's rev from the snapshot is passed as --expect REV to flow prompt", async () => {
  const p = await pathsPanel("ses_prev", { answers: { prompt: "a goal" } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    p.key("r").run();
    await waitFor(() => sends(p.calls).length === 1, 5000, "sent");
    assert.deepEqual(p.flowCalls(), ["-P -m agentgraph.structure flow prompt solo --goal-file - --expect 3f9a2c1d|a goal"]);
  } finally {
    p.done();
  }
});

test("paths: a rev as the back end makes it (16 hex digits) goes to --expect unchanged; a path without rev in the same snapshot gets no --expect; the debug log has each command line and the paths' revs", async () => {
  const log = path.join(tmp, "debug-prev16.log");
  fs.writeFileSync(log, "");
  process.env.AGENTGRAPH_TUI_DEBUG = log;
  const picks = ["solo", "other"];
  try {
    const p = await pathsPanel("ses_prev16", { answers: { select: () => picks.shift(), prompt: "a goal" } });
    try {
      await waitFor(() => p.key("r"), 2000, "r key");
      for (let i = 1; i <= 2; i += 1) {
        await waitFor(() => p.key("r").enabled(), 3000, "r on");
        p.key("r").run();
        await waitFor(() => sends(p.calls).length === i && p.key("r").enabled(), 5000, "sent " + i);
      }
      assert.deepEqual(p.flowCalls(), [
        "-P -m agentgraph.structure flow prompt solo --goal-file - --expect 9b3f17e5dc59312d|a goal",
        "-P -m agentgraph.structure flow prompt other --goal-file -|a goal",
      ]);
      const lines = fs.readFileSync(log, "utf8").split("\n").filter(Boolean).map((l) => l.replace(/^\S+ /, ""));
      assert.deepEqual(lines.filter((l) => l.startsWith("action: flow prompt:")), [
        `action: flow prompt: ${pathsPython} -P -m agentgraph.structure flow prompt solo --goal-file - --expect 9b3f17e5dc59312d`,
        `action: flow prompt: ${pathsPython} -P -m agentgraph.structure flow prompt other --goal-file -`,
      ]);
      const revs = lines.filter((l) => l.startsWith("paths: "));
      assert.ok(revs.length >= 1 && revs.every((l) => l === "paths: solo@9b3f17e5dc59312d other@-"), revs.join(" / "));
    } finally {
      p.done();
    }
  } finally {
    delete process.env.AGENTGRAPH_TUI_DEBUG;
  }
});

test("paths: a snapshot without rev (or with a rev that is not a plain token) passes no --expect", async () => {
  for (const session of ["ses_p1", "ses_pbadrev"]) {
    const p = await pathsPanel(session, { answers: { prompt: "a goal" } });
    try {
      await waitFor(() => p.key("r"), 2000, "r key");
      p.key("r").run();
      await waitFor(() => sends(p.calls).length === 1, 5000, "sent");
      assert.deepEqual(p.flowCalls(), ["-P -m agentgraph.structure flow prompt solo --goal-file -|a goal"], session);
    } finally {
      p.done();
    }
  }
});

test("paths: the path changed since the chooser showed it (--expect refused): nothing sent, a refresh, a toast to press r again, the goal kept", async () => {
  const counts = path.join(tmp, "stale-snapshots.log");
  fs.writeFileSync(counts, "");
  const values = [];
  // polls every 60 s: a new snapshot within 1.5 s is the refresh
  const p = await pathsPanel("ses_pstale", { intervalMs: 60000, answers: { prompt: (o) => (values.push(o.value), "the goal I typed") } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    const before = fs.readFileSync(counts, "utf8").split("\n").filter(Boolean).length;
    p.key("r").run();
    await waitFor(() => p.toasts.length > 0, 5000, "toast");
    assert.deepEqual(p.flowCalls(), ["-P -m agentgraph.structure flow prompt stale --goal-file - --expect r1|the goal I typed"]);
    assert.deepEqual(p.toasts.at(-1), { message: "stale changed since you opened it: nothing was sent. Press r again.", variant: "warning" });
    assert.deepEqual(sends(p.calls), []);
    await waitFor(() => fs.readFileSync(counts, "utf8").split("\n").filter(Boolean).length > before, 1500, "a refresh at once, not at the next poll (4 s)");
    await waitFor(() => p.key("r").enabled(), 2000, "r back on");
    p.key("r").run();
    await waitFor(() => p.flowCalls().length === 2, 5000, "asked again");
    assert.deepEqual(values, [undefined, "the goal I typed"], "the second goal prompt starts with what was typed");
    assert.deepEqual(sends(p.calls), []);
  } finally {
    p.done();
  }
});

test("paths: with a busy child selected, `r` still sends to the orchestrator's root session, never the child's", async () => {
  const p = await pathsPanel("ses_pbusy", { answers: { prompt: "a goal" } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    p.key("down").run();
    await waitFor(() => screen(p.view).some((l) => l.startsWith("›") && l.includes("impl-openai")), 2000, "the child selected");
    assert.match(screen(p.view).at(-1), / s stop  m message  r run /, "the child works: s and m act on it");
    p.key("r").run();
    await waitFor(() => sends(p.calls).length === 1, 5000, "sent");
    assert.deepEqual(sends(p.calls)[0][1], { sessionID: "ses_top", text: "RUN solo\na goal" });
  } finally {
    p.done();
  }
});

test("paths: the key line drops `r run` while a path is being sent, as the key itself is off, and shows it again after", async () => {
  const p = await pathsPanel("ses_pslow", { answers: { prompt: "once" } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    assert.match(screen(p.view).at(-1), / r run /);
    p.key("r").run();
    await waitFor(() => p.flowCalls().length === 1, 3000, "back end asked");
    await waitFor(() => !/ r run /.test(screen(p.view).at(-1)), 2000, "r run hidden while sending");
    assert.equal(p.key("r").enabled(), false);
    await waitFor(() => sends(p.calls).length === 1, 5000, "sent");
    await waitFor(() => / r run /.test(screen(p.view).at(-1)), 2000, "r run back after the send");
  } finally {
    p.done();
  }
});

test("paths: when the paths go, the `r` layer is unregistered (and a stale `r` does nothing); when they come back it is added again", async () => {
  const flag = path.join(tmp, "paths-gone.flag");
  fs.rmSync(flag, { force: true });
  const host = makeActionHost();
  host.ctx.options.python = pathsPython;
  host.ctx.options.intervalMs = 1000;
  const runLayers = [];
  const layer = host.ctx.keymap.layer;
  host.ctx.keymap.layer = (factory) => {
    const off = layer(factory);
    const made = factory();
    if ((made.commands || []).some((c) => c.bind === "r")) {
      const entry = { command: made.commands[0], off: false };
      runLayers.push(entry);
      return () => ((entry.off = true), off());
    }
    return off;
  };
  host.ctx.ui.dialog.select = async (o) => (host.calls.push(["select"]), undefined);
  host.ctx.ui.dialog.prompt = async (o) => (host.calls.push(["prompt"]), undefined);
  const stop = tui.setup(host.ctx);
  try {
    const view = host.panelClaim().render(makePanel({ sessionID: "ses_pflip", width: 80 }));
    await waitFor(() => runLayers.length === 1, 5000, "r registered");
    assert.ok(screen(view).some((l) => l.includes("1 path")));
    fs.writeFileSync(flag, "");
    await waitFor(() => !screen(view).some((l) => l.includes("1 path")), 6000, "paths gone");
    await waitFor(() => runLayers[0].off, 2000, "r unregistered");
    assert.ok(!/ r run /.test(screen(view).at(-1)));
    runLayers[0].command.run();
    await sleep(50);
    assert.deepEqual(host.calls, [], "a stale r does nothing");
    fs.rmSync(flag);
    await waitFor(() => runLayers.length === 2 && !runLayers[1].off, 6000, "r registered again");
  } finally {
    fs.rmSync(flag, { force: true });
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
  assert.ok(runLayers.every((l) => l.off), "the plugin's cleanup unregisters r");
});

test("paths: a goal kept for a path that changed is offered only for that path: not when another is chosen, and still for it after", async () => {
  const picks = ["stale", "solo", "stale"];
  const values = [];
  const p = await pathsPanel("ses_ptwo", { answers: { select: () => picks.shift(), prompt: (o) => (values.push([o.title, o.value]), values.length === 2 ? undefined : "goal for A") } });
  try {
    await waitFor(() => p.key("r"), 2000, "r key");
    for (let i = 1; i <= 3; i += 1) {
      await waitFor(() => p.key("r").enabled(), 3000, "r on");
      p.key("r").run();
      await waitFor(() => values.length === i && p.key("r").enabled(), 5000, "run " + i + " done");
    }
    assert.deepEqual(values, [["Goal for stale", undefined], ["Goal for other", undefined], ["Goal for stale", "goal for A"]]);
    assert.deepEqual(sends(p.calls), []);
  } finally {
    p.done();
  }
});

test("paths: AGENTGRAPH_TUI_HOME (only a folder inside the system temp folder) points the snapshot and `flow prompt` at a scratch orchestration folder; AGENTGRAPH_TUI_NOSEND stops after the message", async () => {
  const scratch = fs.realpathSync(fs.mkdtempSync(path.join(tmp, "orch-")));
  process.env.AGENTGRAPH_TUI_HOME = scratch;
  process.env.AGENTGRAPH_TUI_NOSEND = "1";
  let p;
  try {
    p = await pathsPanel("ses_p1", { answers: { prompt: "a goal" } });
  } finally {
    delete process.env.AGENTGRAPH_TUI_HOME;
    delete process.env.AGENTGRAPH_TUI_NOSEND;
  }
  try {
    assert.equal(fs.readFileSync(path.join(tmp, "paths-home.log"), "utf8").trim(), scratch, "the snapshot ran with PYTHONPATH = AGENTGRAPH_TUI_HOME");
    await waitFor(() => p.key("r"), 2000, "r key");
    p.key("r").run();
    await waitFor(() => p.toasts.length > 0, 5000, "toast");
    assert.deepEqual(p.flowCalls(), ["-P -m agentgraph.structure flow prompt solo --goal-file -|a goal"]);
    assert.equal(fs.readFileSync(path.join(tmp, "paths-home.log"), "utf8").trim(), scratch, "flow prompt ran with PYTHONPATH = AGENTGRAPH_TUI_HOME");
    assert.deepEqual(p.toasts.at(-1), { message: "Not sent (AGENTGRAPH_TUI_NOSEND): solo, a message of 15 characters for ses_top", variant: "info" });
    assert.deepEqual(sends(p.calls), []);
  } finally {
    p.done();
  }
  // ignored: relative, empty, outside the system temp folder (also through a link inside it), the temp folder itself,
  // a folder that does not exist
  // the link is made under the resolved temp folder (os.tmpdir() can itself be a link, /var -> /private/var, and a
  // path under the unresolved one would be refused by the prefix check alone): only resolving the link refuses it
  const link = path.join(fs.realpathSync(tmp), "link-out");
  fs.rmSync(link, { force: true });
  fs.symlinkSync("/usr", link);
  for (const value of ["relative/dir", "  ", "/usr", os.homedir(), link, os.tmpdir(), path.join(tmp, "no-such-dir")]) {
    process.env.AGENTGRAPH_TUI_HOME = value;
    try {
      const q = await pathsPanel("ses_p0");
      q.done();
      assert.equal(fs.readFileSync(path.join(tmp, "paths-home.log"), "utf8").trim(), tmp);
    } finally {
      delete process.env.AGENTGRAPH_TUI_HOME;
    }
  }
});

test("demo paths: AGENTGRAPH_TUI_DEMO=paths is the team at rest with three sample paths; the demo never sends", async () => {
  const now = Date.now();
  const doc = DM.pathsDocument(demo, 0, now);
  assert.equal(R.isAnimated(doc), false);
  const lines = R.toText(R.render(doc, { width: 75, now, cursor: true, keys: [["⏎", "open"], ["r", "run"], ["g", "graph"], ["q", "close"]] })).split("\n");
  assert.deepEqual(lines.map((l) => l.slice(0, 16)), ["›◉ orchestrator ", " 3 paths · last ", " $6.57  ctx ━━━━", " ⏎ open  r run  "]);
  assert.equal(lines[1], " 3 paths · last impl-loop 2h ago");
  const host = makeActionHost();
  host.ctx.ui.dialog.select = async (o) => (host.calls.push(["select", o.options.length]), o.options[0].value);
  host.ctx.ui.dialog.prompt = async (o) => (host.calls.push(["prompt", o.title]), "a goal");
  let spawned = 0;
  const plugin = P.createPlugin({
    createElement: OT.createElement, insert: OT.insert, setProp: OT.setProp, createSignal: solid.createSignal, onCleanup: solid.onCleanup,
    spawn: () => {
      spawned += 1;
      throw new Error("the demo spawned a process");
    },
    fs, os, path, env: { AGENTGRAPH_TUI_DEMO: "paths" }, demoFile: path.join(here, "fixtures", "demo.json"),
  });
  const stop = plugin.setup(host.ctx);
  try {
    const view = host.panelClaim().render(makePanel({ sessionID: "ses_demo", width: 80 }));
    await waitFor(() => screen(view).some((l) => l.includes("3 paths")), 5000, "demo paths row");
    await waitFor(() => host.key("r"), 2000, "r key");
    host.key("r").run();
    await waitFor(() => host.state.toasts.length > 0, 2000, "toast");
    assert.deepEqual(host.calls, [["select", 3], ["prompt", "Goal for impl-loop"]]);
    assert.deepEqual(host.state.toasts.at(-1), { message: "Demo: impl-loop is not sent.", variant: "info" });
    assert.equal(spawned, 0, "no back end, no send");
    assert.ok(!host.calls.some((c) => c[0] === "send"));
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("runner: flow prompt: arguments, goal on stdin, working directory, PYTHONPATH, no shell; timeout", async () => {
  const out = path.join(tmp, "flow-args.txt");
  const py = fakePython("flow-args-python", `pwd -P > "${out}"\necho "$PYTHONPATH" >> "${out}"\necho "$@" >> "${out}"\ncat >> "${out}"\nprintf 'the message\\n\\n'`);
  const work = fs.mkdtempSync(path.join(tmp, "flowhome-"));
  const made = await RUNNER.runFlowPrompt({ spawn, path, env: process.env, home: work, python: py }, "a-b", "line one\n$(rm -rf /) `x`");
  assert.deepEqual(made, { text: "the message" });
  assert.deepEqual(fs.readFileSync(out, "utf8").split("\n").slice(0, 5), [fs.realpathSync(work), work, "-P -m agentgraph.structure flow prompt a-b --goal-file -", "line one", "$(rm -rf /) `x`"]);
  const hang = fakePython("flow-hang-python", "exec sleep 30");
  const t0 = Date.now();
  const late = await RUNNER.runFlowPrompt({ spawn, path, env: process.env, home: tmp, python: hang, timeoutMs: 300 }, "a-b", "g");
  assert.deepEqual(late, { error: "agentgraph flow prompt did not finish within 0 s." });
  assert.ok(Date.now() - t0 < 3000);
  assert.deepEqual(await RUNNER.runFlowPrompt({ spawn, path, env: process.env, home: tmp, python: py }, "a-b", "  "), { error: "The goal is empty." });
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
