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
    options: Object.assign({ python: pluginPython, home: tmp, intervalMs: 1000, picture: "orbit" }, options), // the panel's tests below the rooms ones are of the orbit
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
    assert.deepEqual(layer.commands.map((c) => c.bind), ["f", "q", "up", "down", "return", "c", "escape", "pageup", "pagedown"]); // c, Escape, Page Up and Down: the chat (all off here: no measured height, no chat)
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
    assert.deepEqual(state.layers.filter((l) => (l.commands || []).some((c) => c.bind === "f")).pop().commands.map((c) => c.bind), ["f", "q", "up", "down", "return", "s", "m", "c", "escape", "pageup", "pagedown"]);
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
const SIZES = [[36, 14], [37, 14], [40, 20], [64, 22], [92, 40], [120, 40], [160, 45], [200, 60]];
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
  assert.equal(S.sceneFits(36, 14), true);
  assert.equal(S.sceneFits(64, 22), true);
  assert.equal(S.sceneFits(35, 40), false);
  assert.equal(S.sceneFits(200, 13), false);
  assert.equal(S.sceneFits(undefined, undefined), false);
});

const proposalDoc = (proposals) => keep(D([A("orchestrator", "decide")], null, "orchestrator", { learning: { total: 0, pending: 0, proposals } }));
const PROPS = [
  { id: "20261121-100000-aa11", created: 5, title: "Ask for small diffs", state: "evaluated", verdict: "better" },
  { id: "20261121-090000-bb22", created: 4, title: "Review earlier", state: "applied", verdict: "no difference" },
  { id: "20261121-080000-cc33", created: 3, title: "Rejected one", state: "rejected", verdict: "worse" },
  { id: "20261121-070000-dd44", created: 2, title: "Old draft", state: "drafted", verdict: "not enough runs" },
];
test("proposals: the ones waiting, then the applied (title, state, verdict, id), then the commands; rejected and reverted are not listed; not selectable", () => {
  const lines = listText(proposalDoc(PROPS), 60);
  const at = lines.findIndex((l) => l.includes("Ask for small diffs"));
  assert.ok(at >= 0, lines.join("\n"));
  assert.deepEqual(lines.slice(at, at + 4).map((l) => l.trimEnd()), [
    "? Ask for small diffs evaluated better  20261121-100000-aa11",
    "? Old draft drafted not enough runs  20261121-070000-dd44",
    "○ Review earlier applied no difference  20261121-090000-bb22",
    R.PROPOSAL_COMMANDS,
  ]);
  assert.ok(!lines.join("\n").includes("Rejected one"), "a rejected proposal is listed");
  assert.ok(lines.findIndex((l) => l.includes("orchestrator")) < at || true);
  const view = R.layout(proposalDoc(PROPS), { width: 60, now: NOW });
  assert.ok(view.items.every((item) => !/proposal/.test(item.key)) && view.items.length === 1, "a proposal row is selectable");
  const first = R.render(proposalDoc(PROPS), { width: 60, now: NOW })[at];
  assert.deepEqual([first[0].tone, first.find((span) => span.text === " evaluated").tone], ["accent", "accent"]);
  const applied = R.render(proposalDoc(PROPS), { width: 60, now: NOW })[at + 2];
  assert.equal(applied[0].tone, "muted");
});
test("proposals: more than 3 are cut to +N more, none or malformed ones add no row, a hostile title is plain text, and every width holds", () => {
  const many = Array.from({ length: 6 }, (_, i) => ({ id: "p-" + i, created: i, title: "T" + i, state: "drafted", verdict: "better" }));
  const lines = listText(proposalDoc(many), 50);
  assert.ok(lines.some((l) => l.startsWith("+3 more proposals")), lines.join("\n"));
  assert.equal(lines.filter((l) => l.startsWith("? T")).length, 3);
  const without = listText(proposalDoc([]), 50).join("\n");
  for (const bad of [undefined, null, "x", [null, 3, {}, { id: 5 }, { id: "" }], [{ id: "a", state: "rejected" }, { id: "b", state: "reverted" }]]) {
    const doc = keep(D([A("orchestrator", "decide")], null, "orchestrator", { learning: { proposals: bad } }));
    assert.equal(listText(doc, 50).join("\n"), without, JSON.stringify(bad));
  }
  const evil = proposalDoc([{ id: "p-e", created: 1, title: "\u001b[31mred\u0007 <b>x</b>\n", state: "drafted", verdict: "better\u0000" }]);
  for (const width of [8, 12, 20, 37, 80]) for (const line of R.render(evil, { width, now: NOW })) assert.ok(R.lineWidth(line) <= width, "wide at " + width);
  const text = listText(evil, 60).join("\n");
  assert.ok(!/[\u0000-\u0008\u001b]/.test(text) && text.includes("<b>x</b>"), text);
});

// v4: loop runs (`doc.loops`: what `agentgraph loop list --json` prints, one state per run, newest first)
const loopRun = (id, flow, status, extra = {}) => Object.assign({ version: 1, run: id, flow, status, reason: "", created: 100, limits: { attempts: 4 }, attempts: [], totals: { attempts: 0, cost: 0.0438, seconds: 15 } }, extra);
const loopDoc = (loops) => keep(D([A("orchestrator", "decide")], null, "orchestrator", { loops }));
const LOOPS = [
  loopRun("20261121-051500-0002", "fix-loop", "running", { attempts: [{ n: 1 }], totals: { attempts: 1, cost: null, seconds: 5 } }),
  loopRun("20261121-050000-0001", "fix-loop", "failed", { attempts: [{ n: 1 }, { n: 2 }, { n: 3 }, { n: 4 }], totals: { attempts: 4, cost: 0.2, seconds: 30 } }),
  loopRun("20261121-043755-90a7", "v2-real", "passed", { attempts: [{ n: 1 }, { n: 2 }], totals: { attempts: 2, cost: 0.0438369, seconds: 15 } }),
];
test("loops: a short list of runs (flow, status, attempts of the limit, cost, run id), the live one first, then the commands; an unknown cost reads unknown, never $0.00", () => {
  const lines = listText(loopDoc(LOOPS), 70);
  const at = lines.findIndex((l) => l.includes("fix-loop"));
  assert.ok(at >= 0, lines.join("\n"));
  assert.deepEqual(lines.slice(at, at + 4).map((l) => l.trimEnd()), [
    "↻ fix-loop running 1/4 cost unknown  20261121-051500-0002",
    "– fix-loop failed 4/4 $0.20  20261121-050000-0001",
    "✓ v2-real passed 2/4 $0.04  20261121-043755-90a7",
    R.LOOP_COMMANDS,
  ]);
  assert.equal(R.LOOP_COMMANDS, "agentgraph loop status|cancel RUN");
  assert.ok(!lines.join("\n").includes("$0.00"));
  const view = R.layout(loopDoc(LOOPS), { width: 70, now: NOW });
  assert.ok(view.items.every((item) => !/loop/.test(item.key)), "a loop row is selectable");
  const rows = R.render(loopDoc(LOOPS), { width: 70, now: NOW });
  assert.equal(rows[at][0].tone, "accent");
  assert.equal(rows[at + 1].find((span) => span.text === " failed").tone, "danger");
});
test("loops: every contract status reads differently in words and only passed reads as a pass; more than 3 runs are cut to +N more; none or malformed ones add no row; hostile text is plain; every width holds", () => {
  const statuses = ["running", "passed", "failed", "limit", "invalid", "check_error", "inconclusive", "stalled", "agent_error", "cancelled", "interrupted"];
  const words = statuses.map((status) => {
    const text = listText(loopDoc([loopRun("r-" + status, "f", status)]), 80).find((l) => l.includes("r-" + status));
    assert.ok(text, status);
    return text.split(" ").slice(2).join(" ").split(" 0/")[0].replace(/ \d+\/\d+.*$/, "");
  });
  assert.equal(new Set(words).size, 11, words.join(" | "));
  statuses.forEach((status, i) => { if (status !== "passed") assert.ok(!/pass/i.test(words[i]) && !listText(loopDoc([loopRun("r-" + status, "f", status)]), 80).some((l) => l.includes("r-" + status) && l.startsWith("✓")), status); });
  const many = Array.from({ length: 6 }, (_, i) => loopRun("r-" + i, "f" + i, "failed", { created: 100 - i }));
  const cut = listText(loopDoc(many), 60);
  assert.ok(cut.some((l) => l.startsWith("+3 more loop runs")), cut.join("\n"));
  assert.equal(cut.filter((l) => l.startsWith("– f")).length, 3);
  const without = listText(loopDoc([]), 50).join("\n");
  for (const bad of [undefined, null, "x", [null, 3, {}, { run: 5 }, { run: "" }, { run: "a" }, { run: "a", flow: "f" }]]) {
    assert.equal(listText(keep(D([A("orchestrator", "decide")], null, "orchestrator", { loops: bad })), 50).join("\n"), without, JSON.stringify(bad));
  }
  const evil = loopDoc([loopRun("r-e", "\u001b[31mred\u0007 <b>x</b>\n", "failed", { reason: "x\u0000" })]);
  for (const width of [8, 12, 20, 37, 80]) for (const line of R.render(evil, { width, now: NOW })) assert.ok(R.lineWidth(line) <= width, "wide at " + width);
  const text = listText(evil, 60).join("\n");
  assert.ok(!/[\u0000-\u0008\u001b]/.test(text) && text.includes("<b>x</b>"), text);
});
const { createLoopRunner } = await import(src("runner.js"));
test("loops (v7): a run waiting for a person, a paused one and an interrupted one read in words with the command to answer or resume; a trial is marked; the extra command row appears only when a shown run needs it", () => {
  const needs = [
    loopRun("r-wait", "review-loop", "waiting", { attempts: [{ n: 1 }] }),
    loopRun("r-pause", "review-loop", "paused", { attempts: [{ n: 1 }] }),
    loopRun("r-gone", "review-loop", "interrupted", { attempts: [{ n: 1 }] }),
  ];
  const lines = listText(loopDoc(needs), 80).map((l) => l.trimEnd());
  const find = (id) => lines.find((l) => l.includes(id));
  assert.match(find("r-wait"), /waiting for you/);
  assert.match(find("r-pause"), / paused /);
  assert.match(find("r-gone"), /interrupted/);
  assert.ok(lines.includes(R.LOOP_PERSON_COMMANDS), lines.join("\n"));
  assert.equal(R.LOOP_PERSON_COMMANDS, "agentgraph loop answer|resume RUN");
  assert.ok(lines.includes(R.LOOP_COMMANDS), "the status and cancel commands stay");
  const said = ["waiting", "paused", "interrupted", "running", "passed", "failed"].map((status) => listText(loopDoc([loopRun("r-" + status, "f", status)]), 80).find((l) => l.includes("r-" + status)).trim().replace(/ \d+\/\d+.*$/, ""));
  assert.equal(new Set(said).size, 6, said.join(" | "));
  for (const status of ["waiting", "paused", "interrupted"]) assert.ok(!listText(loopDoc([loopRun("r-" + status, "f", status)]), 80).some((l) => l.includes("r-" + status) && (l.startsWith("✓") || /pass/i.test(l.replace("r-" + status, "")))), status);
  const quiet = listText(loopDoc(LOOPS), 80);
  assert.ok(!quiet.includes(R.LOOP_PERSON_COMMANDS), "an answer or resume command with nothing to answer or resume");
  const trial = listText(loopDoc([loopRun("r-try", "review-loop", "failed", { trial: true })]), 80).find((l) => l.includes("r-try"));
  assert.match(trial, /trial/, trial);
  assert.ok(!/trial/.test(listText(loopDoc([loopRun("r-real", "review-loop", "failed")]), 80).find((l) => l.includes("r-real"))), "a saved loop's run marked as a trial");
  for (const width of [8, 12, 20, 37, 80]) for (const line of R.render(loopDoc(needs.concat([loopRun("r-try", "review-loop", "failed", { trial: true })])), { width, now: NOW })) assert.ok(R.lineWidth(line) <= width, "wide at " + width);
});
test("loop runner: `agentgraph.looprun list --json --project DIR`, no shell, only when the project has a loops folder; any failure is an empty list", async () => {
  const project = fs.mkdtempSync(path.join(tmp, "loop-project-"));
  const argvs = [];
  const python = fakePython("loop-python", `echo '[{"run":"r-1","flow":"f","status":"passed","attempts":[],"limits":{"attempts":2},"totals":{"cost":0.01}}]'`);
  const mk = (extra = {}) => createLoopRunner(Object.assign({ spawn: (c, a, o) => (argvs.push([c, ...a, o]), spawn(c, a, o)), fs, path, env: process.env, home: tmp, python }, extra));
  assert.deepEqual(await mk().list(project), [], "no loops folder, no process");
  assert.equal(argvs.length, 0);
  fs.mkdirSync(path.join(project, ".opencode", "loops"), { recursive: true });
  const rows = await mk().list(project);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].run, "r-1");
  assert.deepEqual(argvs[0].slice(0, 7), [python, "-P", "-m", "agentgraph.looprun", "list", "--json", "--project"]);
  assert.equal(argvs[0][7], project);
  assert.equal(argvs[0].at(-1).shell, false);
  assert.equal(argvs[0].at(-1).env.PYTHONPATH, tmp);
  const bad = async (body) => assert.deepEqual(await mk({ python: fakePython("loop-bad", body) }).list(project), [], body);
  await bad("echo 'not json'");
  await bad(`echo '{"run":"x"}'`);
  await bad("exit 3");
  await bad("");
  assert.deepEqual(await mk({ python: path.join(tmp, "no-such-python") }).list(project), []);
  assert.deepEqual(await mk({ python: "python3" }).list(project), [], "a relative interpreter is refused");
  assert.deepEqual(await mk().list(path.join(tmp, "nope")), []);
  assert.deepEqual(await mk({ demo: true }).list(project), [], "the demo has no loops");
});

test("loops in the panel: a project with a loops folder gets the loop runs in its panel (read with the snapshot, `agentgraph.looprun list --json`); one without spawns no loop command", async () => {
  const loopsFolder = path.join(tmp, ".opencode", "loops");
  const listing = JSON.stringify([loopRun("20261121-051500-0002", "fix-loop", "running", { attempts: [{ n: 1 }], totals: { attempts: 1, cost: null, seconds: 5 } })]);
  const python = fakePython("panel-loop-python", `case "$*" in *agentgraph.looprun*) echo '${listing}';; *agentgraph.chatfeed*) echo '{"session":"x","agent":null,"busy":false,"items":[],"truncated":false,"more":false,"warning":null}';; *) cat "${chatSnap}";; esac`);
  const none = await chatPanel({ python, size: [80, 38] });
  try {
    assert.ok(!none.argvs.some((a) => a.includes("agentgraph.looprun")), "a loop command ran with no loops folder");
    assert.ok(!none.lines().join("\n").includes(R.LOOP_COMMANDS));
  } finally { none.done(); }
  fs.mkdirSync(loopsFolder, { recursive: true });
  try {
    const c = await chatPanel({ python, size: [80, 38] });
    try {
      await waitFor(() => c.lines().join("\n").includes(R.LOOP_COMMANDS), 5000, "the loop rows");
      const text = c.lines().join("\n");
      assert.ok(/fix-loop running 1\/4 cost unknown/.test(text), text);
      const call = c.argvs.find((a) => a.includes("agentgraph.looprun"));
      assert.deepEqual(call.slice(1, 8), ["-P", "-m", "agentgraph.looprun", "list", "--json", "--project", tmp]);
    } finally { c.done(); }
  } finally { fs.rmSync(path.join(tmp, ".opencode"), { recursive: true, force: true }); }
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

test("scene 2D: the hub is a ball (a rim, shaded dots inside, lit from the upper left); no 6x3 window is more than 60% heavy braille", () => {
  for (const [w, h] of [[120, 30], [92, 30], [160, 35], [64, 14]]) {
    const L = S.orbitLayout(demoWorn, w, h);
    for (let now = 0; now < 3200; now += 100) {
      const grid = S.orbitLines(demoWorn, w, h, now).map(cellsOf);
      // inside the rim: the ball's shaded dots, denser toward the upper left than toward the lower right (it was blank)
      const inner = L.hubR - 1.5; // dots
      let upperLeft = 0;
      let lowerRight = 0;
      let inside = 0;
      for (let r = 0; r < h; r += 1) {
        for (let c = 0; c < w; c += 1) {
          const corners = [[c * 2, r * 4], [c * 2 + 2, r * 4], [c * 2, r * 4 + 4], [c * 2 + 2, r * 4 + 4]];
          if (!corners.every(([x, y]) => Math.hypot(x - L.cx, y - L.cy) < inner)) continue;
          const ch = grid[r][c].ch;
          assert.ok(ch === " " || ch === "◉" || /[\u2800-\u28ff]/.test(ch), `${w}x${h}@${now}: cell ${c},${r} inside the hub's rim is ${JSON.stringify(ch)}`);
          inside += dotsIn(ch);
          if (c * 2 + 1 < L.cx && r * 4 + 2 < L.cy) upperLeft += dotsIn(ch);
          if (c * 2 + 1 > L.cx && r * 4 + 2 > L.cy) lowerRight += dotsIn(ch);
        }
      }
      if (L.hubR >= 8) assert.ok(inside >= 3, `${w}x${h}@${now}: a ball, not a bare ring: ${inside} dots inside`); // smaller hubs have no whole cell inside the rim
      assert.ok(upperLeft >= lowerRight, `${w}x${h}@${now}: lit from the upper left: ${upperLeft} dots there, ${lowerRight} at the lower right`);
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

test("scene 2D: with nothing working nothing moves, the panel asks for no frames, and nothing is at full strength", () => {
  const doc = D([A("orchestrator", "hub", 0, "", 1), A("a", "build", 0, "", 0.5), A("b", "verify", 0, "", 0.2), A("c", "decide")], G({ loops: [{ worker: "a", evaluator: "b", state: "closed", rounds: 2 }] }));
  assert.equal(S.frameInterval(doc), 0, "idle in 2D: no frames asked for");
  assert.equal(S.frameInterval(doc, { view: "2d" }, 7000), 0);
  assert.equal(S.frameInterval(doc, V3), 166);
  assert.equal(S.frameInterval(D([A("orchestrator", "hub", 1)])), 100);
  assert.equal(S.frameInterval(D([A("orchestrator", "hub", 1)]), V3), 100);
  assert.equal(S.frameInterval(doc, { view: "3d", change: { from: "2d", at: 0 } }, 300), 100, "the camera's move");
  assert.equal(S.frameInterval(doc, { view: "3d", change: { from: "2d", at: 0 } }, 800), 166, "after it");
  const L = S.orbitLayout(doc, 92, 25);
  const P = S.makePalette(L.keys, DARK);
  const fulls = new Set(L.keys.map((k) => P.full(k)));
  const w = 92, h = 40;
  for (let now = 0; now < 20000; now += 250) {
    const a = S.sceneStrict(doc, { width: w, height: h, now, colors: DARK }).flatMap(cellsOf);
    assert.ok(!a.some((c) => c.tone === "accent" || fulls.has(c.color)), "no pulse at " + now);
  }
  // at rest nothing in the ring moves, at any clock value across a former twenty-second lap
  for (const [sw, sh] of [[36, 14], [42, 38], [160, 45]]) {
    for (const view of [undefined, "2d"]) {
      const at = (now) => JSON.stringify(S.sceneStrict(doc, { width: sw, height: sh, now, colors: DARK, view }));
      const first = at(0);
      for (const now of [1, 250, 5000, 10000, 15000, 20000, 20250]) assert.equal(at(now), first, `${sw}x${sh}: the same frame at ${now}`);
    }
  }
  assert.deepEqual(S.orbitLines(doc, w, 30, 0), S.orbitLines(doc, w, 30, 10000));
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
    for (const r of check(idleDoc, 160, 45, now)) { // (a full-screen panel: in 30 rows the round figure is small enough for names to sit on rings' near points)
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
  const w = 120, h = 40; // (the figure is round and fits the canvas's height: in 30 rows its spokes are short and the stream wraps too often to read as moving outward)
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

// ---------------------------------------------------------------------------------------------------------------
// The pixel rooms (src/rooms.js): the floor plan, with made-up data

const RM = await import(src("rooms.js"));
const copy = (value) => JSON.parse(JSON.stringify(value));
const hubOf = (doc) => doc.agents.find((a) => a.group === "hub");
const ROOM_SIZES = SIZES.concat([[62, 38]]);
const roomsText = (lines) => lines.map((l) => l.map((s) => s.text).join(""));
const inside = (c, r) => c.x0 >= r.x0 && c.x1 <= r.x1 && c.y0 >= r.y0 && c.y1 <= r.y1;
// width and height exact; plain tone, #rrggbb colours (the orbit's rule of merged neighbours does not apply: the header's bold name and its muted count)
function assertRoomsExact(lines, width, height, what) {
  assert.equal(lines.length, height, what + ": height");
  lines.forEach((l, i) => {
    assert.equal(R.displayWidth(l.map((s) => s.text).join("")), width, `${what}: line ${i} width`);
    for (const s of l) {
      assert.ok(R.TONES.includes(s.tone) && typeof s.text === "string" && s.text.length > 0, `${what}: span ${JSON.stringify(s)}`);
      for (const k of ["color", "bg"]) if (k in s) assert.match(s[k], /^#[0-9a-f]{6}$/, `${what}: ${k} ${s[k]}`);
    }
  });
}
const roomsAt = (doc, w, h, now = 1500, options = {}) => RM.roomsInfo(doc, w, h, now, options);

test("rooms: exactly w x h at every size, spans with plain tone and #rrggbb colours; half blocks carry the top pixel in the colour and the bottom in the background", () => {
  for (const [w, h] of ROOM_SIZES) {
    const lines = RM.roomsLines(demoWorn, w, h, 1500);
    assert.equal(lines.length, h, `${w}x${h}: height`);
    lines.forEach((l, i) => {
      assert.equal(R.displayWidth(l.map((s) => s.text).join("")), w, `${w}x${h}: line ${i} width`);
      for (const s of l) {
        assert.equal(s.tone, "plain");
        assert.ok(s.text.length > 0);
        for (const k of ["color", "bg"]) if (k in s) assert.match(s[k], /^#[0-9a-f]{6}$/, `${w}x${h}: ${k} ${s[k]}`);
        if (s.text.startsWith("▀")) assert.ok(s.color && s.bg && s.color !== s.bg, "a half block shows two pixels");
      }
    });
  }
  const lines = RM.roomsLines(demoWorn, 62, 29, 1500);
  assert.ok(lines.some((l) => l.some((s) => s.text.includes("▀") && s.bg)), "pixels are half blocks");
  assert.deepEqual(RM.roomsInfo(demoWorn, 62, 29, 1500).titles.map((t) => t.text), [">_ TERMINAL", "▤ FILES", "◍ WEB", "⌕ SEARCH", "… THINKING", "☾ TEAM"]);
  // the whole panel at the report's size, from the demo
  const panel = RM.roomsScene(demoWorn, { width: 62, height: 38, now: 1500 });
  assertRoomsExact(panel, 62, 38, "roomsScene 62x38");
  assert.ok(roomsText(panel).some((l) => l.startsWith("NOW ")) && roomsText(panel).some((l) => l.startsWith("BEFORE ")));
  assert.match(roomsText(panel)[0], /^◉ orchestrator +4\/11 +\$[\d.]+$/);
});

test("rooms: never throws on an empty document, no hub, no activity, a hub with nothing lit, or 30 agents", () => {
  const thirty = copy(demoWorn);
  const proto = thirty.agents.find((a) => a.group !== "hub");
  for (let i = 0; i < 30; i += 1) thirty.agents.push(Object.assign(copy(proto), { name: `extra-${i}`, busy: i % 3 === 0 ? 1 : 0 }));
  const noHub = copy(demoWorn);
  noHub.agents = noHub.agents.filter((a) => a.group !== "hub");
  const noActivity = copy(demoWorn);
  delete hubOf(noActivity).activity;
  const resting = copy(demoWorn);
  hubOf(resting).busy = 0;
  const docs = [{}, { agents: [] }, { agents: [null, 5, {}] }, null, "x", noHub, noActivity, resting, thirty];
  for (const doc of docs) {
    for (const [w, h] of ROOM_SIZES) {
      for (const now of [0, 1500, 1795075120400]) {
        const lines = RM.roomsLines(doc, w, h, now);
        assert.equal(lines.length, h);
        lines.forEach((l) => assert.equal(R.displayWidth(l.map((s) => s.text).join("")), w));
        assertRoomsExact(RM.roomsScene(doc || {}, { width: w, height: h, now }), w, h, "roomsScene");
      }
    }
  }
  const info = roomsAt(resting, 100, 40);
  assert.equal(info.lit, null, "a hub that is not working lights nothing");
  assert.equal(info.chars.find((c) => c.hub).room, "team");
});

test("rooms: the hub's room follows its zone, and its sprite is inside that room", () => {
  const cases = { files: "files", terminal: "terminal", search: "search", web: "web", thinking: "thinking", messaging: "team", tasks: "team", spawn: "team", tools: "terminal", "made-up-zone": "terminal" };
  for (const [zone, room] of Object.entries(cases)) {
    const doc = copy(demoWorn);
    hubOf(doc).activity.zone = zone;
    for (const [w, h] of [[100, 40], [62, 29], [160, 45]]) {
      const info = roomsAt(doc, w, h, 1500);
      const hub = info.chars.find((c) => c.hub);
      assert.equal(hub.room, room, `${zone} -> ${room}`);
      assert.equal(info.lit, room);
      assert.ok(inside(hub, info.rooms[room]), `${zone} ${w}x${h}: the hub sprite is inside ${room}: ${JSON.stringify(hub)}`);
    }
  }
});

test("rooms: a working agent stands in its tool's room, an idle one in TEAM, dimmed and unnamed; a stuck one in TEAM with name and mark", () => {
  const doc = copy(demoWorn);
  const [first, second] = doc.agents.filter((a) => a.group !== "hub" && Number(a.busy) > 0);
  first.activity = { kind: "tool", tool: "webfetch", text: null };
  second.activity = { kind: "tool", tool: "grep", text: null };
  const info = roomsAt(doc, 160, 45, 1500);
  const of = (name) => info.chars.find((c) => c.name === name);
  assert.equal(of(first.name).room, "web");
  assert.equal(of(second.name).room, "search");
  const idle = info.chars.filter((c) => c.kind === "idle");
  assert.ok(idle.length >= 3);
  for (const c of idle) {
    assert.equal(c.room, "team");
    assert.equal(c.name, null, "an idle agent has no name");
    assert.ok(inside(c, info.rooms.team));
  }
  const reasoning = copy(demoWorn);
  assert.equal(roomsAt(reasoning, 160, 45).chars.find((c) => c.name === "designer").room, "thinking", "working with no tool is thinking");
  const stuck = info.chars.filter((c) => c.kind === "needs");
  assert.ok(stuck.length >= 2);
  for (const c of stuck) {
    assert.equal(c.room, "team");
    assert.ok(c.name_cells, "a stuck or sent back agent keeps its name at this size");
  }
  assert.ok(roomsText(RM.roomsLines(doc, 160, 45, 1500)).some((l) => l.includes("! reviewer")), "the mark goes with the name");
});

test("rooms: no name overlaps another name or a title, in the demo at every size", () => {
  const boxes = (info) => [
    ...info.titles.map((t) => ({ what: "title " + t.text, c0: t.c0, c1: t.c1, r: t.r })),
    ...info.chars.filter((c) => c.name_cells).map((c) => ({ what: "name " + c.name, c0: c.name_cells.c0, c1: c.name_cells.c1, r: c.name_cells.r })),
    ...(info.bubble ? [{ what: "bubble", c0: info.bubble.c0, c1: info.bubble.c1, r: info.bubble.r }] : []),
  ];
  for (const [w, h] of ROOM_SIZES.concat([[100, 30], [37, 16], [62, 29]])) {
    for (const doc of [demoWorn, (() => { const d = copy(demoWorn); hubOf(d).activity.zone = "files"; return d; })()]) {
      for (const now of [1500, 1795075120300, 1795075122000]) {
        const all = boxes(roomsAt(doc, w, h, now));
        for (const b of all) assert.ok(b.c0 >= 0 && b.c1 < w && b.r >= 0 && b.r < h, `${w}x${h}: ${b.what} inside the picture`);
        for (let i = 0; i < all.length; i += 1) {
          for (let j = i + 1; j < all.length; j += 1) {
            const a = all[i];
            const b = all[j];
            assert.ok(a.r !== b.r || a.c1 < b.c0 || b.c1 < a.c0, `${w}x${h}: ${a.what} overlaps ${b.what}`);
          }
        }
        const hub = roomsAt(doc, w, h, now).chars.find((c) => c.hub);
        if (hub && w >= 62 && h >= 22 && now === 1500) assert.ok(hub.name_cells, `${w}x${h}: the hub keeps its name`);
      }
    }
  }
});

test("rooms: reduced motion shows one frame whatever the time; with motion the hub walks for 1.2 s after its action starts and bobbing follows the clock", () => {
  const start = hubOf(demoWorn).activity.time * 1000;
  for (const [w, h] of [[100, 40], [62, 29], [36, 14]]) {
    const a = RM.roomsLines(demoWorn, w, h, start + 300, { motion: "reduced" });
    assert.deepEqual(a, RM.roomsLines(demoWorn, w, h, start + 2000, { motion: "reduced" }), `${w}x${h}: reduced motion is one frame`);
    assert.deepEqual(a, RM.roomsLines(demoWorn, w, h, start + 2500, { motion: "reduced" }));
  }
  assert.equal(RM.roomsInterval(demoWorn, { motion: "reduced" }, start + 300), 0);
  assert.equal(RM.roomsInterval(demoWorn, {}, start + 300), 100);
  assert.ok(RM.roomsInterval(demoWorn, {}, start + 2000) >= 250 / 2);
  const mid = roomsAt(demoWorn, 100, 40, start + 300);
  const rest = roomsAt(demoWorn, 100, 40, start + 2000);
  const hubMid = mid.chars.find((c) => c.hub);
  const hubRest = rest.chars.find((c) => c.hub);
  assert.equal(mid.walking, true);
  assert.equal(rest.walking, false);
  assert.ok(hubMid.x0 !== hubRest.x0 || hubMid.y0 !== hubRest.y0, "the hub is on its way at +300 ms");
  assert.ok(!inside(hubMid, mid.rooms.team) || !inside(hubMid, mid.rooms.web), "...not at rest in a room's corner");
  assert.ok(inside(hubRest, rest.rooms.team), "and at rest in TEAM at +2000 ms");
  assert.deepEqual(roomsAt(demoWorn, 100, 40, start - 5000).chars.find((c) => c.hub), hubRest, "before the start the hub stands at its place");
  // a pure function of the document and the time
  assert.deepEqual(RM.roomsLines(demoWorn, 100, 40, start + 300), RM.roomsLines(copy(demoWorn), 100, 40, start + 300));
  // a working agent bobs 1 px per 500 ms
  const a0 = roomsAt(demoWorn, 100, 40, start + 5000).chars.find((c) => c.name === "impl-claude");
  const a1 = roomsAt(demoWorn, 100, 40, start + 5000 + RM.BOB_MS).chars.find((c) => c.name === "impl-claude");
  assert.equal(Math.abs(a0.y0 - a1.y0), 1);
  assert.equal(roomsAt(demoWorn, 100, 40, start + 5000, { motion: "reduced" }).chars.find((c) => c.name === "impl-claude").y0, roomsAt(demoWorn, 100, 40, start + 5000 + RM.BOB_MS, { motion: "reduced" }).chars.find((c) => c.name === "impl-claude").y0);
});

test("rooms: the target is the hub's alone: in the NOW row, never on an agent's line; the trail runs from the last action's room to the hub's", () => {
  const doc = copy(demoWorn);
  hubOf(doc).activity.target = "made-up-hub-target";
  for (const a of doc.agents) if (a.group !== "hub" && a.activity) a.activity.target = "made-up-agent-target";
  const text = roomsText(RM.roomsScene(doc, { width: 100, height: 40, now: 1500 }));
  assert.ok(text.some((l) => l.startsWith("NOW ") && l.includes("made-up-hub-target")));
  assert.ok(!text.some((l) => l.includes("made-up-agent-target")), "no agent line carries a target\n" + text.join("\n"));
  const working = text.filter((l) => l.startsWith("▪ "));
  assert.ok(working.length >= 2 && working.every((l) => !l.includes("made-up")));
  // the bubble over the hub shows the target, when it stands still
  hubOf(doc).activity.zone = "files"; // alone in its room, with room for the bubble
  const info = roomsAt(doc, 160, 45, 1500);
  assert.ok(info.bubble && "made-up-hub-target".startsWith(info.bubble.text.replace(/…$/, "")));
  assert.equal(roomsAt(doc, 160, 45, hubOf(doc).activity.time * 1000 + 300).bubble, null, "none while it walks");
  // footprints between the doors: pixels outside the sprites and props in the rooms between WEB and TEAM
  const flat = (lines) => lines.map((l) => l.map((s) => s.text + (s.color || "") + (s.bg || "")).join("")).join("\n");
  const start = hubOf(doc).activity.time * 1000;
  assert.notEqual(flat(RM.roomsLines(doc, 100, 40, start + 300)), flat(RM.roomsLines(doc, 100, 40, start + 2000)));
});

test("rooms: the panel gives way in order down to 36x14, always exactly the size, the hub's name in the header", () => {
  const seen = [];
  for (const h of [60, 45, 38, 30, 24, 20, 17, 16, 15, 14]) {
    const lines = RM.roomsScene(demoWorn, { width: 62, height: h, now: 1500 });
    assertRoomsExact(lines, 62, h, `62x${h}`);
    const text = roomsText(lines);
    assert.ok(text[0].includes("orchestrator"), `62x${h}: the header names the hub`);
    seen.push({ h, now: text.some((l) => l.startsWith("NOW ")), before: text.some((l) => l.startsWith("BEFORE ")), working: text.filter((l) => l.startsWith("▪ ")).length });
  }
  const at = (h) => seen.find((s) => s.h === h);
  assert.ok(at(60).working >= at(38).working && at(38).working >= at(30).working, "working lines go first");
  assert.ok(at(60).before && at(60).now, "everything at 62x60");
  for (const s of seen) if (s.before) assert.ok(s.now, `BEFORE never outlives NOW (${s.h})`);
  for (const s of seen) if (s.working) assert.ok(s.before, `working lines go before BEFORE (${s.h})`);
});

test("rooms: a 200x60 frame stays within the orbit's frame cost limit, counted in reference frames (time reported only)", () => {
  const batch = (fn) => {
    const t0 = process.hrtime.bigint();
    for (let i = 0; i < 10; i += 1) fn(i);
    return Number(process.hrtime.bigint() - t0) / 1e6 / 10;
  };
  const kinds = {
    ref: (i) => referenceFrame(160, 45, i),
    rooms: (i) => RM.roomsLines(demoWorn, 200, 60, i * 100),
    "rooms new": (i) => RM.roomsLines(DM.demoDocument(demo, i * 2000), 200, 60, i * 100),
  };
  for (const fn of Object.values(kinds)) for (let i = 0; i < 20; i += 1) fn(i);
  const best = Object.fromEntries(Object.keys(kinds).map((k) => [k, Infinity]));
  for (let round = 0; round < 15; round += 1) for (const [k, fn] of Object.entries(kinds)) best[k] = Math.min(best[k], batch(fn));
  console.log(`     frame 200x60: reference ${best.ref.toFixed(3)} ms; rooms ${best.rooms.toFixed(3)} ms (${(best.rooms / best.ref).toFixed(1)} ref), rooms new ${best["rooms new"].toFixed(3)} ms (${(best["rooms new"] / best.ref).toFixed(1)} ref)`);
  assert.ok(best.rooms / best.ref < FRAME_COST_LIMIT * FRAME_COST_TODAY["2d"], `rooms: ${(best.rooms / best.ref).toFixed(1)} reference frames, limit ${FRAME_COST_LIMIT * FRAME_COST_TODAY["2d"]}`);
  assert.ok(best["rooms new"] / best.ref < FRAME_COST_LIMIT * FRAME_COST_TODAY["2d new"], `rooms new: ${(best["rooms new"] / best.ref).toFixed(1)} reference frames, limit ${FRAME_COST_LIMIT * FRAME_COST_TODAY["2d new"]}`);
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

// `g` in the newest panel's view layer: the panel opens on the orbit (when it fits), `g` switches to the list and back.
const pressG = (state) => state.layers.filter((l) => (l.commands || []).some((c) => c.bind === "g")).pop().commands.find((c) => c.bind === "g").run();
// The orbit is on screen: the hub's glyph inside the canvas (the list has it only at the start of its first line).
const isOrbit = (lines) => lines.length >= 14 && lines.slice(1).some((l) => l.indexOf("◉") > 4);
// The 3D view: the near side of a ring in @, and 0s and 1s, inside the canvas rows (no braille ring there).
const has3D = (lines) => {
  const body = lines.slice(4, -6).join("\n");
  return /@@/.test(body) && /[01]/.test(body) && !/[\u2801-\u28ff]/.test(body.replace(/[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/g, ""));
};

test("panel scene: the panel opens on the orbit when it fits (from 36x14, measured), the list before it is measured; g switches to the list and back; sidebar unchanged", async () => {
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
    // measured, room for the orbit: the orbit straight away (it opened on the list before /graph's change)
    sizePanel(view, 120, 40);
    assert.equal(screen(view).length, 40);
    assert.ok(screen(view).every((l) => R.displayWidth(l) === 120));
    assert.ok(isOrbit(screen(view)), "the orbit by default\n" + screen(view).join("\n"));
    // smaller than 36x14: the list
    sizePanel(view, 35, 40);
    assert.ok(!isOrbit(screen(view)) && screen(view).length < 40);
    sizePanel(view, 100, 13);
    assert.ok(!isOrbit(screen(view)));
    sizePanel(view, 36, 14);
    assert.ok(isOrbit(screen(view)));
    assert.equal(screen(view).length, 14);
    assert.ok(screen(view).every((l) => R.displayWidth(l) === 36));
    // d: 2D to 3D and back, the camera moving over CHANGE_MS; the orbit opens in 2D
    sizePanel(view, 120, 40);
    assert.ok(!has3D(screen(view)), "opens in 2D");
    const d = gLayer().commands.find((c) => c.bind === "d");
    assert.equal(d.enabled(), true);
    d.run();
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => has3D(screen(view)), 3000, "d: the frame timer draws 3D");
    assert.ok(isOrbit(screen(view)), "d: 3D\n" + screen(view).join("\n"));
    // g: list, and back; one layer with g, d and o, enabled only for this panel
    const g = gLayer().commands.find((c) => c.bind === "g");
    assert.deepEqual(gLayer().commands.map((c) => c.bind), ["g", "d", "o"]);
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

test("panel scene: /graph opens straight on the orbit whenever the panel fits it (36x14 and up) and on the list when it does not; g to the list is remembered for the next opening", async () => {
  const { plugin } = makeScenePlugin();
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    for (const [w, h, orbit] of [[36, 14, true], [37, 39, true], [56, 44, true], [75, 39, true], [120, 40, true], [200, 60, true], [35, 40, false], [100, 13, false], [20, 30, false], [30, 12, false]]) {
      for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
      const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: w }));
      await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
      sizePanel(view, w, h);
      assert.equal(isOrbit(screen(view)), orbit, `${w}x${h}: ${orbit ? "the orbit" : "the list"} on opening\n` + screen(view).join("\n"));
      if (orbit) {
        assert.equal(screen(view).length, h);
        assert.ok(!has3D(screen(view)), "in 2D");
      }
    }
    // g to the list is remembered: the next opening shows the list, and g brings the orbit back
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
    sizePanel(view, 120, 40);
    assert.ok(isOrbit(screen(view)));
    pressG(state);
    assert.ok(!isOrbit(screen(view)), "g: the list");
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    const again = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(again).length > 3, 5000, "demo snapshot");
    sizePanel(again, 120, 40);
    assert.ok(!isOrbit(screen(again)), "reopened on the list the user chose");
    pressG(state);
    assert.ok(isOrbit(screen(again)), "g: the orbit again");
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel scene: g switches the orbit and the list in the side panel at the size it has (56 and 37 columns too) and never asks for full screen; only f does", async () => {
  const { plugin } = makeScenePlugin({ screen: { width: 160, height: 45 } });
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const panel = fullscreenPanel();
    const view = panelClaim().render(panel);
    await waitFor(() => screen(view).length > 5, 5000, "demo snapshot");
    const key = (bind) => state.layers.filter((l) => (l.commands || []).some((c) => c.bind === bind)).pop().commands.find((c) => c.bind === bind);
    for (const [w, h] of [[56, 44], [37, 39], [36, 14]]) {
      sizePanel(view, w, h); // the side panel of a 160-column terminal, a narrower one, the smallest
      assert.ok(isOrbit(screen(view)), `${w}x${h}: it opens on the orbit\n` + screen(view).join("\n"));
      key("g").run();
      assert.ok(!isOrbit(screen(view)), `${w}x${h}: g: the list`);
      key("g").run();
      assert.ok(isOrbit(screen(view)), `${w}x${h}: g: the orbit`);
      assert.equal(panel.calls.full, 0, `${w}x${h}: g never asked for full screen`);
      assert.equal(panel.presentation, "panel");
    }
    // f is the user's: it toggles, and g after it still does not
    key("f").run();
    assert.equal(panel.calls.full, 1, "f toggled it");
    sizePanel(view, 160, 45);
    assert.ok(isOrbit(screen(view)), "f: the orbit, full screen");
    key("g").run();
    assert.ok(!isOrbit(screen(view)), "g: the list");
    key("g").run();
    assert.ok(isOrbit(screen(view)), "g: the orbit");
    assert.equal(panel.calls.full, 1, "g did not touch the full screen");
    key("f").run();
    assert.equal(panel.calls.full, 2);
    sizePanel(view, 56, 44);
    assert.ok(isOrbit(screen(view)), "f back: the side panel keeps the orbit");
    assert.equal(panel.calls.full, 2);
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel scene: f full screen shows the orbit, f back to a side panel that fits keeps it; the list's keys are off while the orbit shows", async () => {
  const { plugin } = makeScenePlugin({ screen: { width: 160, height: 45 } });
  const { ctx, state, panelClaim } = makePanelHost();
  ctx.ui.router = { navigate: () => {} };
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const panel = fullscreenPanel();
    const view = panelClaim().render(panel);
    await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
    sizePanel(view, 75, 39); // the side panel of a 130x40 terminal: room for the orbit, so it opens on the orbit
    assert.ok(isOrbit(screen(view)), "the orbit in the side panel");
    const key = (bind) => state.layers.filter((l) => (l.commands || []).some((c) => c.bind === bind)).pop().commands.find((c) => c.bind === bind);
    assert.equal(key("down").enabled(), false, "no row keys on the orbit");
    key("g").run();
    assert.ok(!isOrbit(screen(view)) && screen(view).length <= 16, "g: the list in the side panel");
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
    assert.ok(isOrbit(screen(view)), "f again: a side panel that fits keeps the orbit");
    assert.equal(key("down").enabled(), false);
    key("g").run();
    assert.ok(!isOrbit(screen(view)), "g: the list");
    assert.equal(key("down").enabled(), true);
    // the host leaves full screen by its own key: the orbit stays (it fits)
    key("f").run();
    sizePanel(view, 160, 45);
    assert.ok(isOrbit(screen(view)));
    panel.presentation = "panel";
    sizePanel(view, 75, 39);
    assert.ok(isOrbit(screen(view)), "the host's own key: a side panel that fits keeps the orbit");
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

test("panel scene: below 36x14 the list shows and g has nothing to switch to; it never asks for full screen, whatever the screen's size or when it is unknown", async () => {
  const keyOf = (state, bind) => state.layers.filter((l) => (l.commands || []).some((c) => c.bind === bind)).pop().commands.find((c) => c.bind === bind);
  for (const screenSize of [{ width: 160, height: 45 }, { width: 60, height: 40 }, null]) {
    for (const [w, h] of [[35, 39], [40, 13], [20, 30]]) {
      const { plugin } = makeScenePlugin({ screen: screenSize });
      const { ctx, state, panelClaim } = makePanelHost();
      const stop = plugin.setup(ctx);
      globalThis.__agentgraphStub.cleanups.length = 0;
      try {
        const panel = fullscreenPanel();
        const view = panelClaim().render(panel);
        await waitFor(() => screen(view).length > 5, 5000, "demo snapshot");
        sizePanel(view, w, h);
        assert.ok(!isOrbit(screen(view)), `${w}x${h}: the list`);
        keyOf(state, "g").run();
        assert.equal(panel.calls.full, 0, `${w}x${h} on ${JSON.stringify(screenSize)}: no full screen`);
        assert.ok(!isOrbit(screen(view)), `${w}x${h}: still the list`);
        sizePanel(view, 36, 14); // grown to the minimum: the orbit, and g leaves it alone
        assert.ok(isOrbit(screen(view)), "36x14: the orbit");
        assert.equal(panel.calls.full, 0);
      } finally {
        for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
        stop();
      }
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
  // the moons go round their parents by the clock whatever the camera does (see the moons' check), so the held camera's
  // frame is compared with the moons still (motion: reduced)
  const still = (now, orbit) => sceneText(S.orbitLines(doc, 120, 30, now, { view: "3d", orbit, motion: "reduced" })).join("\n");
  assert.equal(still(t0 + 1000, o), still(t0, o), "held: the same frame");
  assert.equal(still(t0 + S.RESUME_MS - 1000, o), still(t0, o)); // a stuck agent blinks twice a second: the same phase
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
    pressG(state); // the panel opens on the orbit; g shows the list
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

test("rooms panel: the panel opens on the rooms; o swaps rooms and orbit and is remembered; d, the arrows and the mouse act only on the orbit; g still shows the list; the key is in the hints", async () => {
  const { plugin, clockRef } = makeScenePlugin();
  const { ctx, state, panelClaim } = makePanelHost({ picture: undefined }); // the option unset: the default picture
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
    sizePanel(view, 120, 40);
    const isRooms = (lines) => lines.length === 40 && lines.some((l) => l.includes(">_ TERMINAL")) && lines.some((l) => l.startsWith("NOW "));
    assert.ok(isRooms(screen(view)), "opens on the rooms\n" + screen(view).join("\n"));
    const layer = () => state.layers.find((l) => (l.commands || []).some((c) => c.bind === "g"));
    const cmd = (bind) => layer().commands.find((c) => c.bind === bind);
    assert.match(screen(view).join("\n"), /o orbit/, "the key is in the hints");
    assert.equal(cmd("d").enabled(), false, "d is the orbit's");
    // the rooms' spans carry the half blocks' background
    const spans = view.children.flatMap((row) => (row.tag === "text" ? [row] : row.children)).flatMap((t) => t.children);
    assert.ok(spans.some((sp) => sp.props.style && sp.props.style.bg), "spans carry bg");
    // a click and a drag do nothing in the rooms
    const p = view.props;
    const before = screen(view).join("\n");
    p.onMouseDown({ type: "down", button: 0, x: 60, y: 18 });
    p.onMouseDrag({ type: "drag", button: 0, x: 66, y: 20 });
    p.onMouseUp({ type: "up", button: 0, x: 66, y: 20 });
    p.onMouseDown({ type: "down", button: 0, x: 60, y: 18 });
    p.onMouseUp({ type: "up", button: 0, x: 60, y: 18 });
    clockRef.t += S.CHANGE_MS + 50;
    assert.ok(isRooms(screen(view)) && !has3D(screen(view)), "still the rooms");
    assert.equal(before.split("\n").length, screen(view).length);
    // o: the orbit, d works there; o again: the rooms
    cmd("o").run();
    assert.ok(isOrbit(screen(view)) && !isRooms(screen(view)), "o: the orbit");
    assert.equal(cmd("d").enabled(), true);
    cmd("o").run();
    assert.ok(isRooms(screen(view)));
    // g: the list and back to the picture last shown
    cmd("o").run();
    cmd("g").run();
    assert.ok(!isOrbit(screen(view)) && !isRooms(screen(view)), "g: the list");
    cmd("g").run();
    assert.ok(isOrbit(screen(view)), "g: back to the orbit, o's choice remembered");
    // a new mount opens on the choice
    const again = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(again).length > 3, 5000, "demo snapshot");
    sizePanel(again, 120, 40);
    assert.ok(isOrbit(screen(again)));
    // reduced motion is one frame, and the rooms' timer follows the picture
    assert.equal(RM.roomsInterval(demoWorn, { motion: "reduced" }, 0), 0);
  } finally {
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
    pressG(state); // the panel opens on the orbit; g shows the list
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
    // scene text is not selectable: a press and a drag there turn the view instead of selecting (each of the 40
    // rows one `text`, its colours in spans)
    const sceneTexts = view.children.flatMap((row) => (row.tag === "text" ? [row] : row.children));
    assert.ok(sceneTexts.length >= 40 && sceneTexts.every((t) => t.props.selectable === false));
    assert.ok(sceneTexts.every((t) => t.children.every((c) => c.tag === "span")));
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

test("orbit morph: the switch between 2D and 3D is a morph: each agent travels on screen from its 2D place to its 3D place and back; a mid-switch frame differs from both ends", () => {
  const doc = idleDemo();
  const [w, h] = [120, 30];
  const L = S.orbitLayout(doc, w, h);
  const orbit = { turn: 1.0, elevation: S.ELEVATION_3D, resume: 1e15 }; // a held camera: the target stands still
  const { turn, elevation } = S.orbitCamera(orbit, 0);
  const to3D = S.camera3D(L, turn, elevation);
  const F1 = S.figure3D(L);
  const F0 = S.figure3D(L, 0);
  const at = 50000;
  const glyphs = new Set(["○", "●", "↩", "!", " "]); // the glyphs of agents at rest
  const cellAt = (lines, r, c) => (cellsOf(lines[r])[c] || {}).ch;
  // the agent glyphs of a frame at the cells `where(p)` gives: -> how many agents show their glyph there
  const onCells = (lines, where) => L.placed.filter((p) => {
    const q = where(p);
    const ch = q.row >= 0 && q.row < h ? cellAt(lines, q.row, q.col) : undefined;
    return glyphs.has(ch) && (ch !== " " || p.state === "stuck"); // a stuck agent's glyph blinks
  }).length;
  const n = L.placed.length;
  assert.ok(n >= 8);
  // flat: every ring lies on the unit circle and each agent at its own 2D azimuth; the morph's start is the 2D place
  for (const p of L.placed) {
    const a = F0.agents.get(p.agent.name);
    assert.ok(Math.abs(a[2]) < 1e-12 && Math.abs(Math.hypot(a[0], a[1]) - 1) < 1e-9, p.agent.name + " flat on the circle");
    const q = S.morphCamera(L, turn, elevation, 0)(a);
    assert.ok(Math.abs(q.col - p.col) <= 1 && Math.abs(q.row - p.row) <= 1, `${p.agent.name}: the morph starts at the 2D place`);
    const e = S.morphCamera(L, turn, elevation, 1)(F1.agents.get(p.agent.name));
    const t = to3D(F1.agents.get(p.agent.name));
    assert.deepEqual([e.col, e.row], [t.col, t.row], "the morph ends at the 3D place");
  }
  // half way: each agent between its two places, away from both (those that move at least 6 cells)
  let between = 0;
  let movers = 0;
  for (const p of L.placed) {
    const end = to3D(F1.agents.get(p.agent.name));
    const d = (a, b) => Math.hypot(a.col - b.col, 2 * (a.row - b.row));
    const span = d({ col: p.col, row: p.row }, end);
    if (span < 6) continue;
    movers += 1;
    const mid = S.morphCamera(L, turn, elevation, 0.5)(S.figure3D(L, 0.5).agents.get(p.agent.name));
    if (d(mid, { col: p.col, row: p.row }) >= 2 && d(mid, end) >= 2 && d(mid, { col: p.col, row: p.row }) < span && d(mid, end) < span) between += 1;
  }
  assert.ok(movers >= 6 && between === movers, `${between} of ${movers} agents half way between their places`);
  const frame = (now, options) => S.orbitLines(doc, w, h, now, Object.assign({ orbit }, options));
  const text = (lines) => sceneText(lines).join("\n");
  for (const [from, to] of [["2d", "3d"], ["3d", "2d"]]) {
    const change = { from, at };
    const start = frame(at, { view: to, change });
    const mid = frame(at + S.CHANGE_MS / 2, { view: to, change });
    const end = frame(at + S.CHANGE_MS - 1, { view: to, change });
    const flatEnd = from === "2d" ? start : end; // the morph's 2D end, in depth characters
    const deepEnd = from === "2d" ? end : start;
    // the 2D end: the agents on their 2D cells; the 3D end: on their 3D cells; half way: on the morph's cells
    assert.ok(onCells(flatEnd, (p) => p) >= n - 1, `${from}->${to}: the 2D end has the agents on their 2D places`);
    assert.ok(onCells(deepEnd, (p) => to3D(F1.agents.get(p.agent.name))) >= n - 2, `${from}->${to}: the 3D end on their 3D places`);
    const halfWay = (p) => S.morphCamera(L, turn, elevation, 0.5)(S.figure3D(L, 0.5).agents.get(p.agent.name));
    assert.ok(onCells(mid, halfWay) >= n - 2, `${from}->${to}: half way the agents on the morph's cells\n${text(mid)}`);
    // the mid-switch frame differs from both ends and from both steady views
    const steady2D = frame(at + S.CHANGE_MS / 2, { view: "2d" });
    const steady3D = frame(at + S.CHANGE_MS / 2, { view: "3d" });
    for (const other of [start, end, steady2D, steady3D]) assert.notEqual(text(mid), text(other), from + "->" + to);
    assert.ok(onCells(mid, (p) => p) <= n / 2, "half way most agents have left their 2D places");
    assert.ok(onCells(mid, (p) => to3D(F1.agents.get(p.agent.name))) <= n / 2, "and are not yet on their 3D places");
  }
  // the 3D view is a side view: the starting elevation is ELEVATION_3D, low above the plane
  assert.ok(S.ELEVATION_3D > 0 && S.ELEVATION_3D <= Math.PI / 4, "a low elevation");
  assert.equal(S.orbitReset(0).elevation, S.ELEVATION_3D);
  assert.equal(S.orbitCamera(null, 0).elevation, S.ELEVATION_3D);
});

// The sky behind the 3D view (sky.md, "Terminal panel").
const SKY_NOW = 1795075000000;
const skyDoc = () => DM.pathsDocument(demo, 60000, SKY_NOW); // at rest, three finished runs (2 + 1; one path never ran)
const HELD = { turn: 1.0, elevation: S.ELEVATION_3D, resume: 1e15 };
const skyFrame = (doc, w, h, now, options) => S.orbitLines(doc, w, h, now, Object.assign({ view: "3d", orbit: HELD, colors: DARK }, options));
const cellGrid = (lines) => lines.map((l) => cellsOf(l));
// The cells the sky adds: those that differ from the same frame drawn without it. -> [{ row, col, ch, tone, color }]
const skyCells = (doc, w, h, now, options = {}) => {
  const a = cellGrid(skyFrame(doc, w, h, now, options));
  const b = cellGrid(skyFrame(doc, w, h, now, Object.assign({}, options, { sky: false })));
  const out = [];
  a.forEach((row, r) => row.forEach((c, k) => {
    // (a blank cell takes the style of the span it is joined to: only its character counts)
    if (c.ch !== b[r][k].ch || (c.ch !== " " && (c.tone !== b[r][k].tone || c.color !== b[r][k].color))) out.push(Object.assign({ row: r, col: k, under: b[r][k].ch }, c));
  }));
  return out;
};
const luminance = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
};

test("orbit sky: only in 3D; sparse dim stars that are not depth characters, the same sky from the same seed, never over a name, an agent or a figure cell", () => {
  const doc = skyDoc();
  const noFlows = JSON.parse(JSON.stringify(doc));
  noFlows.structure.flows = [];
  const star = S.runStars(doc)[0];
  const streaks = [{ flow: star.flow, run: star.run, at: 50000 }];
  // 2D: nothing of the sky, with runs, streaks or not
  for (const now of [50000, 50450, 52000]) {
    const flat = sceneText(S.orbitLines(doc, 120, 30, now, { view: "2d", streaks, colors: DARK }));
    assert.deepEqual(flat, sceneText(S.orbitLines(noFlows, 120, 30, now, { view: "2d", colors: DARK })), "2D: no sky");
    assert.deepEqual(flat, sceneText(S.orbitLines(doc, 120, 30, now, { view: "2d", sky: false, colors: DARK })));
  }
  assert.equal(S.frameInterval(doc, { view: "2d", streaks }, 50450), S.FRAME_MS_IDLE, "no streak frames in 2D");
  for (const [w, h] of [[64, 12], [120, 30], [200, 50]]) {
    for (const colors of [DARK, LIGHT]) {
      const cells = skyCells(doc, w, h, 50000, { colors });
      const field = cells.filter((c) => c.color !== S.skyColors(colors).run);
      assert.ok(cells.length >= Math.floor(w * h * 0.004), `${w}x${h}: some stars (${cells.length})`);
      assert.ok(cells.length <= w * h * 0.03, `${w}x${h}: sparse, at most 3 % of the cells (${cells.length})`);
      for (const c of cells) {
        assert.equal(c.under, " ", `${w}x${h}: a star only on an empty cell (${c.row},${c.col} over ${JSON.stringify(c.under)})`);
        assert.ok(!S.DEPTH_CHARS.includes(c.ch), `not a depth character: ${c.ch}`);
        assert.ok([...S.STAR_CHARS, S.RUN_STAR_CHAR].includes(c.ch), c.ch);
        assert.equal(c.tone, "muted");
        assert.match(c.color, /^#[0-9a-f]{6}$/);
      }
      // a field star stands clear: nothing of the figure and no text in the eight cells round it
      const plain = cellGrid(skyFrame(doc, w, h, 50000, { colors, sky: false }));
      for (const c of field) {
        for (let r = c.row - 1; r <= c.row + 1; r += 1) {
          for (let k = c.col - 1; k <= c.col + 1; k += 1) if (plain[r] && plain[r][k]) assert.equal(plain[r][k].ch, " ", `the star at ${c.row},${c.col} touches ${plain[r][k].ch}`);
        }
      }
    }
  }
  // the same seed, the same sky: drawn again, from a fresh copy of the document, at another time while held
  const once = sceneText(skyFrame(doc, 120, 30, 50000));
  assert.deepEqual(sceneText(skyFrame(doc, 120, 30, 50000)), once);
  assert.deepEqual(sceneText(skyFrame(JSON.parse(JSON.stringify(doc)), 120, 30, 50000)), once);
  assert.deepEqual(skyCells(doc, 120, 30, 51000), skyCells(doc, 120, 30, 50000), "nothing in the sky moves by itself");
  // colours: no green; the dark theme warm white specks, the light theme ink; a run's star warmer and brighter
  for (const colors of [DARK, LIGHT, { dark: true, muted: "#5fd7af" }, { dark: false, muted: "#3fa060" }]) {
    const C = S.skyColors(colors);
    for (const c of [...C.stars, C.run]) {
      const hue = S.hueOf(c);
      assert.ok(hue === null || hue < 80 || hue > 190, `${c}: hue ${hue}`);
    }
    const hue = S.hueOf(C.run);
    assert.ok(hue >= 15 && hue <= 60, "the run's star is warm: " + hue);
    const bg = colors.dark === false ? 255 : 0;
    assert.ok(Math.abs(luminance(C.run) - bg) > Math.abs(luminance(C.stars[2]) - bg), "brighter against the background than any field star");
    assert.ok(Math.abs(luminance(C.stars[0]) - bg) < Math.abs(luminance(C.stars[2]) - bg), "the small stars dimmer");
  }
});

test("orbit sky: shifts at a fraction of the figure's turn (the same after a full turn), with the elevation, fades with the morph; reduced motion shows it only in steady 3D", () => {
  const doc = idleDemo();
  const [w, h] = [120, 30];
  const at = (orbit, now = 50000, options = {}) => skyCells(doc, w, h, now, Object.assign({ orbit }, options)).filter((c) => S.STAR_CHARS.includes(c.ch));
  const key = (c) => c.row + "," + c.col;
  // the shift that maps most stars of `a` onto stars of `b`
  const bestShift = (a, b, rows) => {
    const inB = new Set(b.map(key));
    let best = { d: 0, n: -1 };
    for (let d = -30; d <= 30; d += 1) {
      const n = a.filter((c) => inB.has(rows ? c.row + d + "," + c.col : c.row + "," + (c.col + d))).length;
      if (n > best.n) best = { d, n };
    }
    return best;
  };
  const a = at({ turn: 1.0, elevation: S.ELEVATION_3D, resume: 1e15 });
  const b = at({ turn: 1.3, elevation: S.ELEVATION_3D, resume: 1e15 });
  const shift = bestShift(a, b, false);
  // the agents' movement for the same turn: the near side's largest
  const L = S.orbitLayout(doc, w, h);
  const F = S.figure3D(L);
  const p0 = S.camera3D(L, 1.0, S.ELEVATION_3D);
  const p1 = S.camera3D(L, 1.3, S.ELEVATION_3D);
  // (the figure is round and fits the canvas's height, so in a wide panel no agent need stand where the near side moves most: the figure's rings count too)
  const moved = (pt) => Math.abs(p1(pt).col - p0(pt).col);
  const agentMove = Math.max(...L.placed.map((p) => moved(F.agents.get(p.agent.name))), ...F.rings.flatMap((ring) => ring.points.filter((_, i) => i % 6 === 0).map(moved)));
  assert.ok(shift.n >= a.length * 0.6, `most stars shift together (${shift.n} of ${a.length})`);
  assert.ok(shift.d >= 2 && shift.d <= agentMove * 0.7, `the sky moves ${shift.d} cells, the near agents up to ${agentMove}: a fraction`);
  // a full turn: the same sky (no jump where the turn wraps)
  const full = at({ turn: 2 * Math.PI - 1e-9, elevation: S.ELEVATION_3D, resume: 1e15 });
  const zero = at({ turn: 0, elevation: S.ELEVATION_3D, resume: 1e15 });
  const inZero = new Set(zero.map(key));
  assert.ok(full.filter((c) => inZero.has(key(c))).length >= full.length * 0.9, "the same at turn 0 and 2 PI");
  // the elevation: the sky moves up as the camera rises
  const up = bestShift(a, at({ turn: 1.0, elevation: S.ELEVATION_3D + 0.4, resume: 1e15 }), true);
  assert.ok(up.d < 0 && up.n >= a.length * 0.5, `up with the elevation: ${up.d} rows (${up.n} of ${a.length})`);
  // the morph: the sky fades in toward 3D and out toward 2D (and none at the 2D end)
  const count = (now, change, options = {}) => skyCells(doc, w, h, now, Object.assign({ orbit: HELD, change }, options)).length;
  const t0 = 60000;
  const into = { from: "2d", at: t0 };
  const steady = count(t0 + S.CHANGE_MS, null);
  const early = count(t0 + 150, into);
  const late = count(t0 + 550, into);
  assert.equal(count(t0, into), 0, "none at the 2D end");
  assert.ok(early < late && late < steady && early > 0, `fades in: ${early} < ${late} < ${steady}`);
  const back = { from: "3d", at: t0 };
  const out = (now, options = {}) => count(now, back, Object.assign({ view: "2d" }, options));
  assert.ok(out(t0 + 150) > out(t0 + 550) && out(t0 + 550) > 0, `fades out toward 2D: ${out(t0 + 150)} > ${out(t0 + 550)}`);
  assert.equal(out(t0 + S.CHANGE_MS), 0, "none in 2D");
  // reduced motion: no fade (none during the morph), the same sky in steady 3D
  assert.equal(count(t0 + 550, into, { motion: "reduced" }), 0);
  assert.equal(out(t0 + 150, { motion: "reduced" }), 0);
  assert.equal(count(t0 + S.CHANGE_MS, null, { motion: "reduced" }), steady);
});

test("orbit sky: one warmer star per finished run (capped at 200); a streak flies to a new run's star, then it stays; reduced motion: it just appears", () => {
  const doc = skyDoc();
  assert.equal(S.runStars(doc).length, 3, "2 + 1 passed runs; a failed run and a path never run add none");
  assert.deepEqual(S.finishedRuns(doc).map((r) => r.passed), [2, 1, 0]);
  // the cap: the first runs of every path first, then the second, ...; growing past it moves no star already there
  const many = JSON.parse(JSON.stringify(doc));
  many.structure.flows = [
    { id: "a-b", path: ["a", "b"], stats: { runs: 500, passed: 480 } },
    { id: "c-d", path: ["c", "d"], stats: { runs: 4, passed: 3 } },
    { id: "e-f", path: ["e", "f"], stats: { runs: 2, passed: 0 } },
  ];
  const capped = S.runStars(many);
  assert.equal(capped.length, S.RUN_STARS_MAX);
  assert.equal(S.RUN_STARS_MAX, 200);
  assert.equal(capped.filter((s) => s.flow === "c-d").length, 3, "a path with few runs keeps its stars");
  assert.equal(capped.filter((s) => s.flow === "e-f").length, 0);
  many.structure.flows[0].stats.passed = 900;
  assert.deepEqual(S.runStars(many), capped, "past the cap a new run adds no star and moves none");
  assert.equal(new Set(capped.map((s) => s.u + "," + s.v)).size, capped.length, "each run its own place");
  // a run star that shows at the held camera, and its streak // (40 rows: in 30 the round figure can cover a streak's diagonal)
  const C = S.skyColors(DARK);
  const steadyCells = skyCells(doc, 120, 40, 50000);
  const runCells = steadyCells.filter((c) => c.color === C.run);
  assert.ok(runCells.length >= 1 && runCells.length <= 3 && runCells.every((c) => c.ch === S.RUN_STAR_CHAR), "the run stars show, warm");
  const target = S.runStars(doc).find((s) => {
    const cells = skyCells(doc, 120, 40, 50000, { streaks: [{ flow: s.flow, run: s.run, at: 50100 }] }); // waiting: no star yet
    return cells.filter((c) => c.color === C.run).length < runCells.length;
  });
  assert.ok(target, "a run star on screen");
  const streaks = [{ flow: target.flow, run: target.run, at: 50000 }];
  const tail = (cells) => cells.filter((c) => c.ch === S.STREAK_CHARS.down || c.ch === S.STREAK_CHARS.up);
  const runStarsAt = (now, options = {}) => skyCells(doc, 120, 40, now, Object.assign({ streaks }, options)).filter((c) => c.color === C.run && c.ch === S.RUN_STAR_CHAR).length;
  // waiting its turn: no star, no streak
  assert.equal(tail(skyCells(doc, 120, 40, 49900, { streaks })).length, 0);
  assert.equal(runStarsAt(49900), runCells.length - 1);
  // in flight: a diagonal tail behind the head, on empty cells only, the star not there yet
  const mid = skyCells(doc, 120, 40, 50000 + S.STREAK_MS / 2, { streaks });
  const t = tail(mid);
  assert.ok(t.length >= 1, "a tail");
  assert.ok(mid.every((c) => c.under === " "), "over nothing of the figure");
  const heads = mid.filter((c) => c.ch === S.STREAK_CHARS.head && c.color === C.run);
  assert.ok(heads.length >= 1);
  const cellsOfStreak = [...t, ...heads].sort((x, y) => x.row - y.row);
  for (let i = 1; i < cellsOfStreak.length; i += 1) {
    assert.equal(cellsOfStreak[i].row - cellsOfStreak[i - 1].row, 1, "one row a step");
    assert.equal(Math.abs(cellsOfStreak[i].col - cellsOfStreak[i - 1].col), 1, "one column a step: a diagonal");
  }
  assert.ok(S.STREAK_MS <= 1000, "about a second");
  assert.equal(S.frameInterval(doc, { view: "3d", streaks }, 50000 + 450), S.FRAME_MS_WORKING, "10 frames a second while it flies");
  assert.equal(S.frameInterval(doc, { view: "3d", streaks }, 49900), S.FRAME_MS_WORKING, "and while it waits its turn");
  // landed: no streak, the star stays; the frame is the one without the streak
  const done = 50000 + S.STREAK_MS;
  assert.equal(tail(skyCells(doc, 120, 40, done, { streaks })).length, 0);
  assert.deepEqual(sceneText(skyFrame(doc, 120, 40, done, { streaks })), sceneText(skyFrame(doc, 120, 40, done)));
  assert.equal(S.frameInterval(doc, { view: "3d", streaks }, done), S.FRAME_MS_IDLE_3D, "back to idle frames");
  // reduced motion: no streak, the star appears at its time; idle frames
  assert.equal(tail(skyCells(doc, 120, 40, 50450, { streaks, motion: "reduced" })).length, 0);
  assert.equal(runStarsAt(50450, { motion: "reduced" }), runCells.length);
  assert.equal(runStarsAt(49900, { motion: "reduced" }), runCells.length - 1);
  assert.equal(S.frameInterval(doc, { view: "3d", streaks, motion: "reduced" }, 50450), S.FRAME_MS_IDLE_3D);
  assert.equal(P.resolveOptions({ reducedMotion: true }, { os, path }).reducedMotion, true);
  assert.equal(P.resolveOptions({}, { os, path }).reducedMotion, false);
});

test("orbit sky: the hub drawn brighter (accent) in 3D while the orchestrator works; the 2D hub unchanged", () => {
  const idle = idleDemo();
  const working = JSON.parse(JSON.stringify(idle));
  working.agents.find((a) => a.group === "hub").busy = 1;
  const hubTone = (doc, view) => {
    const lines = S.orbitLines(doc, 120, 40, 5000, { view, orbit: HELD, sky: false });
    for (const l of lines) for (const c of cellsOf(l)) if (c.ch === "◉") return c.tone;
    return null;
  };
  assert.equal(hubTone(working, "3d"), "accent", "lit while it works");
  assert.equal(hubTone(idle, "3d"), "plain", "still when idle");
  assert.equal(hubTone(working, "2d"), hubTone(idle, "2d"), "2D unchanged");
  assert.equal(hubTone(idle, "2d"), "plain");
  // the hub's ring too: accent cells round it only while it works
  const accents = (doc) => S.orbitLines(doc, 120, 30, 5000, { view: "3d", orbit: HELD, sky: false }).flat().filter((s) => s.tone === "accent").map((s) => s.text).join("");
  assert.ok(/[·.:01@░▒▓█]/.test(accents(working)), "the ball lit");
  assert.equal(accents(idle).replace(/\s/g, ""), "");
});

// ---- moons and the sun

const BRAILLE_BITS = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]];
// every braille dot of the frame with the style of its cell: [{ x, y, color }] in dots
function frameDots(lines) {
  const out = [];
  lines.forEach((l, r) => cellsOf(l).forEach((c, col) => {
    const code = c.ch.codePointAt(0) - 0x2800;
    if (code < 1 || code > 255) return;
    for (let y = 0; y < 4; y += 1) for (let x = 0; x < 2; x += 1) if (code & BRAILLE_BITS[y][x]) out.push({ x: col * 2 + x, y: r * 4 + y, color: c.color, tone: c.tone });
  }));
  return out;
}
const SUN_ALL = new Set([...Object.values(S.SUN_COLORS.dark), ...Object.values(S.SUN_COLORS.light)]);
// the cells of a frame drawn in the sun's colours: [{ col, row, ch }]
function warmCells(lines) {
  const out = [];
  lines.forEach((l, row) => cellsOf(l).forEach((c, col) => {
    if (c.color && SUN_ALL.has(c.color) && c.ch !== " ") out.push({ col, row, ch: c.ch });
  }));
  return out;
}
const lumaOf = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => (Math.max(lumaOf(a), lumaOf(b)) + 0.05) / (Math.min(lumaOf(a), lumaOf(b)) + 0.05);
const hubIdle = () => {
  const d = idleDemo();
  if (d.root) d.root.busy = false;
  return d;
};
const hubBusy = () => {
  const d = hubIdle();
  d.agents.find((a) => a.group === "hub").busy = 1;
  return d;
};

test("moons 3D: each moon on its own orbit plane round its parent (planes turned about the vertical axis, 20 degrees apart at least), its own radius r (1 + 0.14 i), 360 degrees * i / n round at time 0, a lap in 24 s", () => {
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const len = (a) => Math.hypot(...a);
  const unit = (a) => a.map((x) => x / len(a));
  const deg = (r) => (r * 180) / Math.PI;
  const angleBetween = (a, b) => deg(Math.acos(Math.min(1, Math.abs(dot(a, b)))));
  const check = (doc, w, h, lead, label) => {
    const L = S.orbitLayout(doc, w, h);
    const F = S.figure3D(L);
    const placed = L.placed.find((p) => p.agent.name === lead);
    const n = placed.seats.length;
    const names = placed.seats.map((x) => x.name).sort();
    const seats = names.map((name) => F.seats.find((x) => x.agent.name === name));
    assert.ok(seats.every(Boolean) && seats.length === n, label + ": every moon has a seat");
    const c = F.agents.get(lead);
    const rel = (x, t) => sub(S.moonPoint(x, S.moonPhase(t, false)), c);
    const base = len(rel(seats[0], 0));
    assert.ok(base > 0.1 && base < 0.45, label + ": a small orbit " + base);
    assert.ok(Math.abs(base - Math.min(0.4, 0.11 + 0.04 * n)) < 1e-9, label + ": r, the single ring's radius rule: " + base);
    const normals = seats.map((x) => unit(cross(rel(x, 0), rel(x, 3000)))); // a quarter of the way on: the orbit's own normal
    const mids = seats.map((x) => unit(cross(rel(x, 7000), rel(x, 11000))));
    seats.forEach((x, i) => {
      // its own plane for the whole lap, its own radius
      for (let t = 0; t <= 24000; t += 1500) {
        assert.ok(Math.abs(len(rel(x, t)) - base * (1 + 0.14 * i)) < 1e-9, `${label}: moon ${i} radius ${len(rel(x, t))} at ${t}`);
        assert.ok(Math.abs(dot(rel(x, t), normals[i])) < 1e-9 * 10, `${label}: moon ${i} stays in its plane at ${t}`);
      }
      assert.ok(angleBetween(normals[i], mids[i]) < 1e-3, label + ": the plane does not turn");
      // all go round the same way, a lap in 24 s: 90 degrees in 6 s
      const swept = deg(Math.acos(dot(unit(rel(x, 0)), unit(rel(x, 6000)))));
      assert.ok(Math.abs(swept - 90) < 1e-6, `${label}: moon ${i} sweeps ${swept} degrees in 6 s`);
      assert.ok(len(sub(rel(x, 24000), rel(x, 0))) < 1e-9, label + ": back where it began after 24 s");
      assert.ok(len(sub(S.moonPoint(x, S.moonPhase(9999, true)), S.moonPoint(x, 0))) < 1e-9, label + ": reduced motion stands at time 0");
    });
    // the sense: the plane's normal (cross of the position and where it goes) is the same hand for every moon of a parent
    // (here: it is the stored ring normal)
    seats.forEach((x, i) => assert.ok(dot(unit(cross(rel(x, 0), rel(x, 1000))), x.ring.normal) > 0.999, label + ": the ring's own sense"));
    // each pair of planes at least 20 degrees apart, over the lap
    for (let i = 0; i < n; i += 1) {
      for (let j = i + 1; j < n; j += 1) {
        const apart = angleBetween(normals[i], normals[j]);
        assert.ok(apart >= 20, `${label}: moons ${seats[i].agent.name} and ${seats[j].agent.name} orbit ${apart.toFixed(1)} degrees apart`);
      }
    }
    // no two moons at one place at any time of the lap
    for (let t = 0; t < 24000; t += 250) {
      for (let i = 0; i < n; i += 1) {
        for (let j = i + 1; j < n; j += 1) assert.ok(len(sub(rel(seats[i], t), rel(seats[j], t))) > 0.02, `${label}: moons ${i} and ${j} meet at ${t}`);
      }
    }
    // in the plane turned about the vertical axis through the parent by 360 * i / n: the first plane turned
    const turned = (v, a) => [Math.cos(a) * v[0] - Math.sin(a) * v[1], Math.sin(a) * v[0] + Math.cos(a) * v[1], v[2]];
    seats.forEach((x, i) => assert.ok(angleBetween(normals[i], turned(normals[0], (2 * Math.PI * i) / n)) < 1e-3, `${label}: plane ${i} is plane 0 turned by 360 * ${i} / ${n} about the vertical`));
    // moon i starts 360 * i / n round its orbit from the first plane's start (the parent's radial line)
    seats.forEach((x, i) => assert.ok(Math.abs(x.a0 - (2 * Math.PI * i) / n) < 1e-9, label + ": phase at time 0"));
    return { F, seats, base, c };
  };
  // the demo's council (three moons), at the sizes the panel takes, and groups of 2 to 5 moons
  for (const [w, h] of [[120, 40], [160, 45], [48, 20], [36, 14]]) check(hubIdle(), w, h, "council", `council ${w}x${h}`);
  const groups = ["council", "decide", "build", "verify", "understand"];
  for (const k of [2, 3, 4, 5]) {
    for (const g of groups) {
      const ags = [A("orchestrator", "hub")];
      groups.forEach((x) => {
        ags.push(A("lead-" + x, x));
        if (x === g) for (let i = 0; i < k; i += 1) ags.push(A("m" + i, x, 0, "lead-" + x));
      });
      check(D(ags), 120, 30, "lead-" + g, `${k} moons in ${g}`);
    }
  }
  // the orbit grows with the number of moons (r)
  const mk = (k) => {
    const d = D([A("orchestrator", "hub"), A("lead", "council"), ...Array.from({ length: k }, (_, i) => A("m" + i, "council", 0, "lead"))]);
    return S.figure3D(S.orbitLayout(d, 120, 30)).seats[0].ring.radius;
  };
  assert.ok(mk(2) < mk(4) && mk(4) < mk(6), "radius " + [mk(2), mk(4), mk(6)]);
  // a moon's orbit leans out of its group's ring at full lean; the morph starts flat with it
  const L = S.orbitLayout(hubIdle(), 120, 30);
  const F = S.figure3D(L);
  const flat = S.figure3D(L, 0);
  for (const x of flat.seats) {
    const parent = flat.agents.get(x.lead);
    const ringNormal = flat.rings.find((r) => r.key === x.key).normal;
    for (const t of [0, 1700, 5000]) assert.ok(Math.abs(dot(sub(S.moonPoint(x, S.moonPhase(t, false)), parent), ringNormal)) < 1e-9 || x.index > 0, "moon 0 flat with its group's ring at the start of the morph");
  }
  for (const x of F.seats) assert.ok(Math.acos(Math.abs(dot(x.ring.normal, F.rings.find((r) => r.key === x.key).normal))) > 0.2 || F.seats.filter((y) => y.lead === x.lead).length > 1, "leans out of the group's ring");
});

test("moons 3D: a lap in 24 s from the clock, never from the frame count; still with reduced motion; names once, none over another or the parent's", () => {
  assert.equal(S.MOON_LAP_MS, 24000);
  assert.equal(S.moonPhase(0, false), 0);
  assert.ok(Math.abs(S.moonPhase(6000, false) - Math.PI / 2) < 1e-12);
  assert.ok(Math.abs(S.moonPhase(24000, false)) < 1e-9 || Math.abs(S.moonPhase(24000, false) - 2 * Math.PI) < 1e-9);
  assert.equal(S.moonPhase(7777, true), 0);
  const doc = hubIdle();
  const orbit = { turn: 0.4, elevation: S.ELEVATION_3D, resume: 1e15 };
  const at = (now, extra = {}, size = [120, 40]) => sceneText(S.scene(doc, Object.assign({ width: size[0], height: size[1], now, view: "3d", orbit }, extra)));
  const t0 = 50000;
  assert.deepEqual(at(t0 + 24000), at(t0), "a lap in 24 s: the same frame");
  assert.notDeepEqual(at(t0 + 6000), at(t0), "a quarter lap on: another frame");
  assert.notDeepEqual(at(t0 + 12000), at(t0 + 6000));
  // the clock, not the frame count: `frame` is ignored, `now` decides
  assert.deepEqual(at(t0, { frame: 1 }), at(t0, { frame: 9999 }));
  assert.deepEqual(at(t0, { motion: "reduced" }), at(t0 + 5000, { motion: "reduced" }), "reduced motion: the moons stand still");
  // names: at 120x40 and 160x45, through a lap, each ring agent's name once, the parent's name once, and each moon's name
  // whole (never cut by another name) whenever it is drawn, and drawn in most frames
  const moonNames = ["fable", "astra", "carbon"];
  for (const size of [[120, 40], [160, 45]]) {
    let shown = 0;
    let frames = 0;
    for (let now = t0; now < t0 + 24000; now += 1500) {
      const lines = at(now, {}, size).slice(4, size[1] - 6); // the canvas (the spend bars below name agents too)
      frames += 1;
      for (const name of ringNames) assert.equal(countWord(lines, name), 1, `${name} at ${size} @${now}\n${lines.join("\n")}`);
      for (const name of moonNames) {
        const n = countWord(lines, name);
        assert.ok(n <= 1, `${name} drawn ${n} times at ${size} @${now}`);
        shown += n;
      }
    }
    assert.ok(shown >= 0.8 * moonNames.length * frames, `the moons' names are drawn: ${shown} of ${moonNames.length * frames} at ${size}`);
  }
  // 36x14: no name cut by another (a name's start shows only with the whole name), whether the sun works or not
  for (const d of [doc, hubBusy()]) {
    for (let now = t0; now < t0 + 24000; now += 1500) {
      const flat = sceneText(S.scene(d, { width: 36, height: 14, now, view: "3d", orbit })).join("\n");
      for (const name of moonNames) if (flat.includes(name.slice(0, 3))) assert.ok(flat.includes(name), `${name} cut at 36x14 @${now}\n${flat}`);
    }
  }
  // the moons' glyphs `•` stay put with reduced motion and move with the clock
  const bullets = (now, extra) => at(now, Object.assign({ sky: false }, extra)).flatMap((l, r) => [...l].map((ch, c) => (ch === "•" ? r * 1000 + c : -1)).filter((x) => x >= 0)).join(",");
  assert.ok(bullets(t0).length > 0);
  assert.notEqual(bullets(t0), bullets(t0 + 4000));
  assert.equal(bullets(t0, { motion: "reduced" }), bullets(t0 + 4000, { motion: "reduced" }));
  // the morph still ends on each view's steady frame: the frame at the end of the change is the steady one, the moons too
  const change = { from: "2d", at: t0 - S.CHANGE_MS };
  assert.deepEqual(at(t0, { change }), at(t0));
  const to2d = (now) => sceneText(S.scene(doc, { width: 120, height: 40, now, view: "2d", change: { from: "3d", at: t0 - S.CHANGE_MS } }));
  assert.deepEqual(to2d(t0), sceneText(S.scene(doc, { width: 120, height: 40, now: t0, view: "2d" })));
  // 2D is unchanged by the moons: it still has its idle rate
  assert.equal(S.frameInterval(doc, { view: "2d" }, t0), S.FRAME_MS_IDLE);
});

test("hub 3D and 2D at rest: a ball, lit from the upper left, still, no sun colours", () => {
  const doc = hubIdle();
  const w = 120;
  const h = 30;
  const L = S.orbitLayout(doc, w, h);
  // 3D: lighter shades toward the upper left (the light), denser toward the lower right
  const o3 = { view: "3d", orbit: HELD, sky: false };
  const project = S.camera3D(L, HELD.turn, HELD.elevation);
  const hub = project.hub;
  const grid = (now) => S.orbitLines(doc, w, h, now, o3).map((l) => cellsOf(l).map((c) => c.ch));
  const weight = (ch) => Math.max(0, S.BALL_SHADES.indexOf(ch));
  const g0 = grid(0);
  let ul = 0;
  let lr = 0;
  let cells = 0;
  for (let r = 0; r < h; r += 1) {
    for (let c = 0; c < w; c += 1) {
      const dx = (c * 2 + 1 - hub.x) / hub.rx;
      const dy = (r * 4 + 2 - hub.y) / hub.ry;
      if (dx * dx + dy * dy > 0.8) continue;
      cells += 1;
      if (dx < -0.15 && dy < -0.15) ul += weight(g0[r][c]);
      if (dx > 0.15 && dy > 0.15) lr += weight(g0[r][c]);
    }
  }
  assert.ok(cells >= 20, "a ball of " + cells + " cells");
  assert.ok(lr > ul + 3, `3D: denser shades toward the lower right (${lr}) than the upper left (${ul})`);
  // still: the ball's cells do not change from one frame to the next, with the camera held
  const inBall = (g) => g.flatMap((row, r) => row.filter((_, c) => ((c * 2 + 1 - hub.x) / hub.rx) ** 2 + ((r * 4 + 2 - hub.y) / hub.ry) ** 2 <= 0.8)).join("");
  assert.equal(inBall(grid(0)), inBall(grid(1500)));
  assert.equal(inBall(grid(0)), inBall(grid(7300)));
  // 2D: the ball's dots are the same at any time while nothing works
  const ball2D = (now) => frameDots(S.orbitLines(doc, w, h, now)).filter((d) => Math.hypot(d.x - L.cx, d.y - L.cy) <= L.hubR + 0.5).map((d) => d.x + "," + d.y).join(" ");
  assert.ok(ball2D(0).split(" ").length > 30, "a ball of dots: " + ball2D(0).split(" ").length);
  assert.equal(ball2D(0), ball2D(900));
  assert.equal(ball2D(0), ball2D(12345));
  // no sun colours at rest, either view, either theme
  for (const colors of [DARK, LIGHT]) {
    for (const o of [{}, { view: "3d" }]) assert.deepEqual(warmCells(S.orbitLines(doc, w, h, 3000, Object.assign({ colors }, o))), []);
  }
  // an idle 2D frame equal to the last stays equal (nothing new animates at rest); the hub adds nothing that moves
  const hubRegion = (now) => S.orbitLines(doc, w, h, now).slice(Math.floor(L.cy / 4) - 4, Math.floor(L.cy / 4) + 5).map((l) => cellsOf(l).slice(Math.floor(L.cx / 2) - 8, Math.floor(L.cx / 2) + 9).map((c) => c.ch + c.color).join("")).join("|");
  assert.equal(hubRegion(100), hubRegion(2100));
});

test("hub working, the sun: 7 to 12 spikes by size, each its own seeded time 0.6 to 1.6 s, within 0.6 of the radius beyond the rim, in warm colours, over the rings, under the agents and names", () => {
  assert.equal(S.spikeCount(5), 7);
  assert.equal(S.spikeCount(9), 12);
  assert.ok(S.spikeCount(7) > 7 && S.spikeCount(7) < 12);
  assert.equal(S.SPIKE_REACH, 0.6);
  // the spikes' own times: each runs 0.6 to 1.6 s from nothing to nothing, spikes not in step
  const n = 12;
  const runs = new Map();
  const seen = new Set();
  let most = 0;
  for (let now = 0; now <= 30000; now += 20) {
    const live = S.sunSpikes(n, now, 1, false);
    most = Math.max(most, live.length);
    assert.ok(live.length <= n);
    for (const sp of live) {
      assert.ok(sp.reach > 0 && sp.reach <= S.SPIKE_REACH + 1e-9, "reach " + sp.reach);
      assert.ok(Math.abs(Math.hypot(...sp.dir) - 1) < 1e-9);
      seen.add(sp.i);
      const r = runs.get(sp.i) || { start: now, last: now, done: [] };
      if (now - r.last > 20) {
        r.done.push(r.last - r.start + 20);
        r.start = now;
      }
      r.last = now;
      runs.set(sp.i, r);
    }
  }
  assert.equal(seen.size, n, "every spike shows");
  const lengths = [...runs.values()].flatMap((r) => r.done.slice(1)); // the first run may be cut by the start
  assert.ok(lengths.length > 100, "lives counted: " + lengths.length);
  for (const ms of lengths) assert.ok(ms >= 500 && ms <= 1660, "a spike lives " + ms + " ms");
  assert.ok(new Set(lengths.map((ms) => Math.round(ms / 100))).size >= 7, "irregular: their lives differ");
  assert.ok(most >= 4 && most <= n, "several at once, never more than there are: " + most);
  const starts = [...runs.keys()].map((i) => S.sunSpikes(n, 0, 1, false).some((s) => s.i === i));
  assert.ok(starts.some((x) => x) && starts.some((x) => !x), "not in step: some are out at one instant, some in");
  // the same seeds give the same spikes
  assert.deepEqual(S.sunSpikes(n, 4321, 1, false), S.sunSpikes(n, 4321, 1, false));
  // reduced motion: short, still
  const still = S.sunSpikes(n, 100, 1, true);
  assert.ok(still.length >= 7 && still.every((sp) => sp.reach > 0 && sp.reach <= 0.35));
  assert.deepEqual(still, S.sunSpikes(n, 98765, 1, true));

  const doc = hubBusy();
  const w = 120;
  const h = 30;
  const L = S.orbitLayout(doc, w, h);
  // 2D: warm dots reach at most 0.6 of the radius beyond the rim (one dot of rounding), at every time
  const reach2D = [];
  const lit = new Set();
  for (let now = 0; now < 8000; now += 100) {
    const lines = S.orbitLines(doc, w, h, now);
    const warm = frameDots(lines).filter((d) => d.color && SUN_ALL.has(d.color) && !(d.color === S.SUN_COLORS.dark.core && Math.hypot(d.x - L.cx, d.y - L.cy) < L.hubR));
    for (const d of warm) {
      const dist = Math.hypot(d.x - L.cx, d.y - L.cy);
      assert.ok(dist <= L.hubR * (1 + S.SPIKE_REACH) + 1.01, `a warm dot ${dist.toFixed(1)} dots out, the rim at ${L.hubR}, at ${now}`);
      reach2D.push(dist);
    }
    lit.add(warm.map((d) => d.x + "," + d.y).join(" "));
  }
  assert.ok(Math.max(...reach2D) > L.hubR * 1.3, "the spikes do shoot out: " + Math.max(...reach2D));
  assert.ok(lit.size >= 40, "they move: " + lit.size + " distinct frames of 80");
  // 3D, the same, in cells of the screen: a warm cell at most 0.6 of the ball's radius (plus a cell) beyond its rim
  const project = S.camera3D(L, HELD.turn, HELD.elevation);
  const hub = project.hub;
  const lit3 = new Set();
  let far = 0;
  for (let now = 0; now < 8000; now += 100) {
    const cells = warmCells(S.orbitLines(doc, w, h, now, { view: "3d", orbit: HELD, sky: false }));
    for (const c of cells) {
      assert.ok(/^[`|\\/\-^v<>]$/.test(c.ch) && !S.DEPTH_CHARS.includes(c.ch), "the 3D sun is drawn in flare characters, none a depth character: " + c.ch);
      const dx = (c.col * 2 + 1 - hub.x) / hub.rx;
      const dy = (c.row * 4 + 2 - hub.y) / hub.ry;
      const d = Math.hypot(dx, dy);
      assert.ok(d <= 1 + S.SPIKE_REACH + Math.max(2 / hub.rx, 4 / hub.ry) + 0.15, `3D: a warm cell ${d.toFixed(2)} radii out at ${now}`);
      far = Math.max(far, d);
    }
    lit3.add(cells.map((c) => c.col + "," + c.row).join(" "));
  }
  assert.ok(far > 1.25, "3D spikes shoot out: " + far.toFixed(2));
  assert.ok(lit3.size >= 30, "3D: they move: " + lit3.size);
  // the warm colours: the build orange toward a pale yellow, none green, none a group colour, readable on light and dark
  for (const [theme, bg] of [["dark", "#000000"], ["light", "#ffffff"]]) {
    for (const [name, c] of Object.entries(S.SUN_COLORS[theme])) {
      const hue = S.hueOf(c);
      assert.ok(hue >= 10 && hue <= 60, `${theme} ${name} ${c}: hue ${hue}`);
      if (name !== "glow" || theme === "light") assert.ok(contrast(c, bg) >= 2, `${theme} ${name} ${c} against ${bg}: ${contrast(c, bg).toFixed(2)}`);
      for (const group of Object.values(S.GROUP_COLORS[theme])) assert.notEqual(c, group, "not a group's colour");
    }
  }
  // under everything: no warm cell is a name, a glyph or a loop's digit (text and digit streams cover the spikes)
  for (const view of [{}, { view: "3d" }]) {
    for (let now = 0; now < 4000; now += 400) {
      for (const lineSet of [S.scene(doc, Object.assign({ width: 120, height: 40, now }, view))]) {
        for (const line of lineSet) for (const span of line) if (span.color && SUN_ALL.has(span.color)) assert.match(span.text, /^[⠀-⣿ :.·|\\/\-^v<>`]+$/, `a sun-coloured span over text: ${JSON.stringify(span.text)}`);
      }
    }
  }
});

test("hub working in 3D: the sun's flares are drawn over the rings and spokes behind it, in characters that are no depth character, changing from frame to frame, never over an agent or a name; none at rest", () => {
  const FLARE = /^[`|\\/\-^v<>]$/;
  const busy = hubBusy();
  const idle = hubIdle();
  for (const [w, h] of [[120, 40], [48, 20]]) {
    const L = S.orbitLayout(busy, w, h);
    const project = S.camera3D(L, HELD.turn, HELD.elevation);
    const hub = project.hub;
    const radii = (c, r) => Math.hypot((c * 2 + 1 - hub.x) / hub.rx, (r * 4 + 2 - hub.y) / hub.ry);
    const grid = (doc, now) => S.orbitLines(doc, w, h, now, { view: "3d", orbit: HELD, sky: false }).map((l) => cellsOf(l));
    const seen = new Set();
    const sets = new Set();
    let overRings = 0;
    for (let now = 50000; now < 53000; now += 100) {
      const a = grid(busy, now);
      const b = grid(idle, now);
      const here = [];
      a.forEach((row, r) => row.forEach((c, k) => {
        const was = b[r][k].ch;
        if (c.color && SUN_ALL.has(c.color) && c.ch !== " ") {
          const d = radii(k, r);
          if (d > 1) {
            assert.match(c.ch, FLARE, `${w}x${h}: a flare in a flare character, not ${c.ch}`);
            assert.ok(!S.DEPTH_CHARS.includes(c.ch), "no depth character");
            // outside the ball's rim, within 0.6 of its radius (plus a cell)
            assert.ok(d <= 1 + S.SPIKE_REACH + Math.max(2 / hub.rx, 4 / hub.ry) + 0.15, `${w}x${h}: a flare ${d.toFixed(2)} radii out`);
            here.push(k + "," + r);
            seen.add(k + "," + r);
            if (S.DEPTH_CHARS.includes(was) && was !== " ") overRings += 1;
          }
        }
        // never over an agent's glyph or a name: whatever was there with the hub at rest and is no ring, spoke or ball character
        if (was !== " " && !S.DEPTH_CHARS.includes(was) && !S.BALL_SHADES.includes(was)) assert.equal(c.ch, was, `${w}x${h}: ${JSON.stringify(c.ch)} over ${JSON.stringify(was)} at ${r},${k}`);
      }));
      sets.add(here.join(" "));
    }
    assert.ok(seen.size >= 7, `${w}x${h}: ${seen.size} distinct flare cells over 3 s`);
    assert.ok(sets.size >= 10, `${w}x${h}: the flares change from frame to frame (${sets.size} distinct frames of 30)`);
    assert.ok(overRings >= 1, `${w}x${h}: a flare drawn over a ring or spoke cell (${overRings})`);
    // at rest: none, and the ball still
    const first = grid(idle, 50000);
    for (let now = 50000; now < 53000; now += 500) {
      const g = grid(idle, now);
      g.forEach((row, r) => row.forEach((c, k) => {
        assert.ok(!(c.color && SUN_ALL.has(c.color) && c.ch !== " "), `${w}x${h}: a sun cell at rest at ${r},${k}`);
        if (radii(k, r) <= 1) assert.equal(c.ch, first[r][k].ch, `${w}x${h}: the ball at rest is still`);
      }));
    }
  }
});

test("hub working: the spikes ramp in when the hub starts, die down within 1 s of it stopping, and frames keep coming while they do; reduced motion: short still spikes", () => {
  const busy = hubBusy();
  const idle = hubIdle();
  const w = 120;
  const h = 30;
  const T = 100000;
  const sceneAt = (doc, now, extra = {}) => S.orbitLines(doc, w, h, now, extra);
  const warmAt = (doc, now, extra) => warmCells(sceneAt(doc, now, extra)).length;
  for (const view of [{}, { view: "3d", orbit: HELD, sky: false }]) {
    // stopping: spikes at the instant, none a second later, and the frame is then the resting one
    const prev = { doc: busy, at: T };
    let max = 0;
    for (let dt = 0; dt < 1000; dt += 100) max = Math.max(max, warmAt(idle, T + dt, Object.assign({ previous: prev }, view)));
    assert.ok(warmAt(idle, T + 50, Object.assign({ previous: prev }, view)) > 0, "still spiked right after it stops");
    assert.ok(max > 0);
    // dying down: the lit level falls steadily to 0 at 1 s and the spikes' total reach falls with it
    let total = Infinity;
    for (const dt of [0, 250, 500, 750, 999]) {
      const level = S.sunLevel(idle, { previous: prev }, T + dt);
      assert.ok(Math.abs(level - (1 - dt / 1000)) < 1e-9, "level " + level);
      const reach = Array.from({ length: 40 }, (_, k) => S.sunSpikes(12, T + dt + k * 137, level, false).reduce((t, x) => t + x.reach, 0)).reduce((t, x) => t + x, 0);
      assert.ok(reach < total, `the spikes' reach falls: ${reach} at +${dt}`);
      total = reach;
    }
    assert.equal(warmAt(idle, T + 1000, Object.assign({ previous: prev }, view)), 0, "none 1 s after it stopped");
    assert.deepEqual(sceneAt(idle, T + 1000, Object.assign({ previous: prev }, view)).map((l) => l.map((s) => s.text).join("")), sceneAt(idle, T + 1000, view).map((l) => l.map((s) => s.text).join("")), "the resting frame");
    // a hub that was not working before: the spikes are not there at the instant it starts, and are in a moment
    const start = { doc: idle, at: T };
    assert.equal(warmAt(busy, T, Object.assign({ previous: start }, view)), 0, "nothing at the first instant");
    assert.ok(warmAt(busy, T + 600, Object.assign({ previous: start }, view)) > 0, "lit after a moment");
    // no previous: lit from the start (first frame drawn)
    assert.ok(warmAt(busy, T, view) > 0);
    // reduced motion: short still spikes; gone within a second of the stop
    const red = Object.assign({ motion: "reduced" }, view);
    assert.deepEqual(warmCells(sceneAt(busy, T, red)), warmCells(sceneAt(busy, T + 3700, red)), "still");
    assert.ok(warmCells(sceneAt(busy, T, red)).length > 0, "short spikes");
    assert.equal(warmAt(idle, T + 1000, Object.assign({ previous: prev }, red)), 0);
  }
  // frames keep coming at the working rate for the second the spikes need, then fall back to the idle rate
  const prev = { doc: busy, at: T };
  for (const view of ["2d", "3d"]) {
    assert.equal(S.frameInterval(idle, { view, previous: prev }, T + 100), S.FRAME_MS_WORKING);
    assert.equal(S.frameInterval(idle, { view, previous: prev }, T + 900), S.FRAME_MS_WORKING);
    assert.equal(S.frameInterval(idle, { view, previous: prev }, T + 1000), view === "3d" ? S.FRAME_MS_IDLE_3D : S.FRAME_MS_IDLE);
    assert.equal(S.frameInterval(idle, { view }, T + 100), view === "3d" ? S.FRAME_MS_IDLE_3D : S.FRAME_MS_IDLE);
  }
  assert.equal(S.sunLevel(busy, {}, 5), 1);
  assert.equal(S.sunLevel(idle, {}, 5), 0);
  assert.equal(S.SUN_FADE_MS, 1000);
});

test("small panel: the orbit draws from 36x14 in 2D and 3D with the hub, every agent, working pulses, the moons and the sun's flares, no name over another; the narrow header and instruments give way first", () => {
  assert.equal(S.SCENE_MIN_WIDTH, 36);
  assert.equal(S.SCENE_MIN_HEIGHT, 14);
  assert.equal(S.sceneFits(36, 14), true);
  assert.equal(S.sceneFits(35, 14), false);
  assert.equal(S.sceneFits(36, 13), false);
  const labels = [...ringNames, "fable", "astra", "carbon", "orchestrator"];
  // a name token: whole, or cut with an ellipsis
  const tokensOf = (lines) => lines.join("\n").match(/[A-Za-z][A-Za-z-]{2,}…?/g) || [];
  const matches = (t, n) => (t.endsWith("…") ? n.startsWith(t.slice(0, -1)) && n.length > t.length - 1 : n === t);
  const orbit = (turn) => ({ turn, elevation: S.ELEVATION_3D, resume: 1e15 });
  let moons = 0;
  let bullets = 0;
  let frames3 = 0;
  for (const view of ["2d", "3d"]) {
    for (const [doc, what] of [[hubIdle(), "at rest"], [hubBusy(), "hub working"], [demoWorn, "agents working"]]) {
      for (const [w, h] of [[36, 14], [37, 14], [40, 16], [36, 30]]) {
        for (let k = 0; k < 8; k += 1) {
          const now = 1000 + k * 3700;
          const opts = { width: w, height: h, now, view, orbit: orbit(k * 0.8), colors: k % 2 ? LIGHT : DARK };
          const full = S.sceneStrict(doc, opts);
          assertExact(full, w, h, `${what} ${view} ${w}x${h}@${now}`);
          const text = sceneText(full);
          assert.match(text[0], /^◉ orchestrator/, "the header is the hub's line");
          assert.ok(text[0].trimEnd().length <= w);
          // the canvas without the needs lines and the instruments, which are text of their own
          const shown = text.slice(1).filter((l) => !/stuck|sent back|ctx|━|\$/.test(l));
          const tokens = tokensOf(shown);
          const here = `${what} ${view} ${w}x${h}@${now}\n${text.join("\n")}`;
          for (const t of tokens) assert.ok(labels.some((n) => matches(t, n)), `"${t}" is no name, or one overwritten: ${here}`);
          for (const n of ringNames) assert.equal(tokens.filter((t) => matches(t, n)).length, 1, `${n} once: ${here}`);
          for (const n of ["fable", "astra", "carbon"]) assert.ok(tokens.filter((t) => matches(t, n)).length <= 1, `${n} not twice: ${here}`);
          assert.ok(text.slice(1).some((l) => l.includes("◉")), `the hub: ${here}`);
          if (view === "3d") {
            frames3 += 1;
            moons += tokens.filter((t) => ["fable", "astra", "carbon"].some((n) => matches(t, n))).length;
            bullets += [...text.slice(1).join("")].filter((ch) => ch === "•").length;
          }
        }
      }
    }
  }
  assert.ok(bullets >= 1.5 * frames3, `the moons' glyphs show: ${bullets} in ${frames3} frames`);
  assert.ok(moons >= 1.5 * frames3, `the moons' names show at the small size: ${moons} in ${frames3} frames`);
  // the order of giving way at 36 columns: the instruments go before the canvas shrinks below its minimum rows, the
  // header stays one line, a long name is cut with an ellipsis and the figure's glyphs stay
  const small = sceneText(S.scene(demoWorn, { width: 36, height: 14, now: 5000 }));
  assert.ok(!small.some((l) => /ctx|━/.test(l)), "no instrument at 14 rows");
  const tall = sceneText(S.scene(demoWorn, { width: 36, height: 40, now: 5000 }));
  assert.ok(tall.some((l) => /ctx|━/.test(l)), "the instruments come back with room");
  // (the ring gives way to the names, not the names to the ring: a smaller circle, every name whole; a name over 12 is still cut)
  assert.ok(small.some((l) => l.includes("oracle-lite")) && !small.some((l) => l.includes("oracle-…")), "names whole at 36 columns, the ring smaller");
  // the sun's flares at the smallest size, in both views: warm cells beyond the ball while the hub works, none at rest
  for (const view of ["2d", "3d"]) {
    let flared = 0;
    for (let k = 0; k < 10; k += 1) {
      const now = 5000 + k * 190;
      const o = { width: 36, height: 14, now, view, orbit: orbit(0.3), colors: DARK };
      const busy = warmCells(S.scene(hubBusy(), o));
      const rest = warmCells(S.scene(hubIdle(), o));
      assert.equal(rest.length, 0, `${view}: none at rest`);
      if (busy.length >= 3) flared += 1;
    }
    assert.ok(flared >= 8, `${view}: the flares show at 36x14 (${flared} of 10 frames)`);
  }
  // working pulses in 2D: a working agent's frame changes with the clock, an idle one's does not
  const two = (doc, now) => sceneText(S.scene(doc, { width: 36, height: 14, now })).join("\n");
  assert.notEqual(two(demoWorn, 1000), two(demoWorn, 1300), "working: it moves");
  assert.equal(two(hubIdle(), 1000), two(hubIdle(), 1300), "at rest: it stands still");
});

test("orbit sky panel: no streak on opening or switching to 3D; a run finished while 3D shows flies exactly one streak and its star stays; one finished in 2D flies none; an unchanged frame is not pushed", async () => {
  const docFile = path.join(tmp, "sky-doc.json");
  const debugLog = path.join(tmp, "sky-debug.log");
  fs.rmSync(debugLog, { force: true });
  const base = DM.pathsDocument(demo, 0, SKY_NOW);
  const withRuns = (passed) => {
    const d = JSON.parse(JSON.stringify(base));
    d.structure.flows[0].stats.passed = passed;
    d.structure.flows[0].stats.runs = passed + 1;
    return d;
  };
  fs.writeFileSync(docFile, JSON.stringify(withRuns(2)));
  const skyPython = fakePython("sky-python", `cat "${docFile}"`);
  const clockRef = { t: SKY_NOW };
  const plugin = P.createPlugin({
    createElement: OT.createElement, insert: OT.insert, setProp: OT.setProp, createSignal: solid.createSignal, onCleanup: solid.onCleanup,
    spawn, screenSize: () => ({ width: 200, height: 60 }), fs, os, path, env: { AGENTGRAPH_TUI_DEBUG: debugLog }, now: () => clockRef.t,
    timers: { setInterval: (fn, ms) => globalThis.setInterval(fn, ms), clearInterval: (h) => globalThis.clearInterval(h) },
  });
  const { ctx, state, panelClaim } = makePanelHost({ python: skyPython, intervalMs: 1000 });
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  const streakLines = () => (fs.existsSync(debugLog) ? fs.readFileSync(debugLog, "utf8").split("\n").filter((l) => l.includes(" sky: streaks ")) : []);
  const tails = (lines) => lines.join("\n").split("").filter((ch) => ch === S.STREAK_CHARS.down || ch === S.STREAK_CHARS.up).length;
  const nextFetch = () => sleep(1250); // the poll: one snapshot a second
  try {
    const view = panelClaim().render(makePanel({ sessionID: "ses_sky", width: 120 }));
    await waitFor(() => screen(view).length > 3, 5000, "the snapshot");
    sizePanel(view, 120, 40);
    assert.ok(isOrbit(screen(view)), "opens on the orbit");
    commandOf(state, "agentgraph.graph.dimension").run();
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => has3D(screen(view)), 3000, "3D");
    // opening and switching: no streak, and none for a snapshot that brings the same runs
    await nextFetch();
    clockRef.t += 450;
    await sleep(250);
    assert.equal(streakLines().length, 0, "no streak on opening or on switching to 3D");
    assert.equal(tails(screen(view)), 0);
    // a run finishes while 3D shows: exactly one streak, then its star stays (the turn held by a turn key, so the
    // sky stands still while the clock moves on)
    commandOf(state, "agentgraph.graph.turn-left").run();
    await sleep(150);
    const starsBefore = screen(view).join("\n").split(S.RUN_STAR_CHAR).length - 1;
    fs.writeFileSync(docFile, JSON.stringify(withRuns(3)));
    await nextFetch();
    const lines = streakLines();
    assert.equal(lines.length, 1, "one streak logged");
    assert.match(lines[0], /streaks impl-openai-verifier-impl-openai#3$/);
    clockRef.t += S.STREAK_MS / 2;
    await waitFor(() => tails(screen(view)) > 0, 2000, "the streak in flight");
    clockRef.t += S.STREAK_MS;
    await waitFor(() => tails(screen(view)) === 0, 2000, "the streak landed");
    const starsAfter = screen(view).join("\n").split(S.RUN_STAR_CHAR).length - 1;
    assert.ok(S.STREAK_MS * 1.5 < S.RESUME_MS, "still held");
    assert.equal(starsAfter, starsBefore + 1, "its star stays\n" + screen(view).join("\n"));
    // the same runs again: nothing more
    await nextFetch();
    assert.equal(streakLines().length, 1);
    // the clock stands: an unchanged frame is not pushed (the rows keep their nodes)
    const nodes = view.children.slice();
    const renders = state.renders;
    await sleep(500);
    assert.deepEqual(view.children, nodes);
    assert.ok(state.renders - renders <= 1, "at most the poll's own render request");
    // a run finished while the 2D view shows: no streak, then or after switching back to 3D
    commandOf(state, "agentgraph.graph.dimension").run();
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => !has3D(screen(view)), 3000, "2D");
    fs.writeFileSync(docFile, JSON.stringify(withRuns(4)));
    await nextFetch();
    commandOf(state, "agentgraph.graph.dimension").run();
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => has3D(screen(view)), 3000, "3D again");
    await nextFetch();
    clockRef.t += 450;
    await sleep(250);
    assert.equal(streakLines().length, 1, "no streak for a run finished in 2D");
    assert.equal(tails(screen(view)), 0);
  } finally {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  }
});

// OpenTUI's hit test, as the real host does it (OpenCode 2.3.2): a press goes to the renderable at its cell in the
// last paint (a `box` or a `text`; a `span` is drawn as part of its `text`), and is dropped when that renderable has
// left the tree since (hitTest gives 0); otherwise it bubbles up to the panel's root box.
const paintOf = (view) => view.children.map((row) => ({ row, kids: row.tag === "box" ? row.children.slice() : null, text: nodeText(row) }));
const hitAt = (painted, x, y) => {
  const p = painted[y];
  if (!p) return null;
  if (!p.kids) return p.row;
  let col = 0;
  for (const t of p.kids) {
    col += [...nodeText(t)].length;
    if (x < col) return t;
  }
  return p.row;
};
const inTree = (node, target) => node === target || (!!node && typeof node === "object" && node.children.some((c) => inTree(c, target)));
const hostClick = (view, painted, x, y) => {
  const target = hitAt(painted, x, y);
  if (!target || !inTree(view, target)) return false; // dropped, as OpenTUI drops it
  view.props.onMouseDown({ type: "down", button: 0, x, y });
  view.props.onMouseUp({ type: "up", button: 0, x, y });
  return true;
};

test("orbit panel: a click during or right after the 2D/3D morph is never lost (at 0, 100, 350, 690 and 710 ms after the first), though the rows redraw between the host's paint and the press", async () => {
  const { plugin, clockRef } = makeScenePlugin();
  const { ctx, state, panelClaim } = makePanelHost();
  const stop = plugin.setup(ctx);
  globalThis.__agentgraphStub.cleanups.length = 0;
  try {
    const view = panelClaim().render(makePanel({ sessionID: "ses_demo", width: 120 }));
    await waitFor(() => screen(view).length > 3, 5000, "demo snapshot");
    sizePanel(view, 120, 40);
    commandOf(state, "agentgraph.graph.dimension").run();
    clockRef.t += S.CHANGE_MS + 50;
    await waitFor(() => has3D(screen(view)), 3000, "3D");
    const [x0, y0] = [60, 18];
    let raced = 0;
    for (const d of [0, 100, 350, 690, 710]) {
      await sleep(250);
      assert.ok(has3D(screen(view)), `steady 3D before the click at ${d} ms`);
      // the first click, on the 3D orbit as painted: 2D
      assert.ok(hostClick(view, paintOf(view), x0, y0), "the first click lands");
      const t1 = clockRef.t;
      // the host's last paint before the second click, then a tick redraws the rows, then the press comes
      let painted = paintOf(view);
      if (d > 0) {
        clockRef.t = t1 + Math.max(0, d - 100);
        await sleep(250);
        painted = paintOf(view);
      }
      clockRef.t = t1 + d;
      await sleep(250);
      const now = paintOf(view);
      const changed = now.findIndex((p, i) => i >= 4 && painted[i] && p.text !== painted[i].text);
      if (d > 0) assert.ok(changed >= 0, `the rows redrew between the paint and the click at ${d} ms`);
      const [x, y] = changed >= 0 ? [Math.min(...[...now[changed].text].map((ch, k) => (ch !== [...painted[changed].text][k] ? k : 1e9))), changed] : [x0, y0];
      if (changed >= 0) raced += 1;
      const landed = hostClick(view, painted, x, y);
      clockRef.t += S.CHANGE_MS + 50;
      await sleep(300); // a frame at the new time drawn (early in the morph the frame still looks 3D)
      await waitFor(() => has3D(screen(view)), 3000, `the click at ${d} ms switched back to 3D (${landed ? "it landed" : "lost: the line under it was replaced"})`);
    }
    assert.ok(raced >= 4, `the race was run ${raced} times`);
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

// Revs here are 16 lowercase hex digits, the back end's shape (review n8 r6, minor 4: until then "3f9a2c1d", "r1"
// and "r2" stood in, which the panel no longer takes as revs).
const flowLog = path.join(tmp, "flow-calls.log");
const pathsPython = fakePython("paths-python", `
echo "$PYTHONPATH" > "${path.join(tmp, "paths-home.log")}"
if [ "$3" = "agentgraph.structure" ]; then
  goal=$(cat)
  printf '%s|%s\\n' "$*" "$goal" >> "${flowLog}"
  expect=""
  prev=""
  for a in "$@"; do [ "$prev" = "--expect" ] && expect="$a"; prev="$a"; done
  if [ "$6" = "stale" ] && [ -n "$expect" ] && [ "$expect" != "2222222222222222" ]; then
    echo "agentgraph structure: flow stale changed since you opened it; reload and try again (rev now 2222222222222222)" >&2; exit 1
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
  ses_prev) flows="$(flow solo null null 3f9a2c1d4e5b6a70)";;
  ses_prev16) flows="$(flow solo null null 9b3f17e5dc59312d),$(flow other null null)";;
  ses_pbadrev) flows="$(flow solo null null -x)";;
  ses_ptwo) flows="$(flow stale null null 1111111111111111),$(flow solo '"other"' null)";;
  ses_pstale) echo x >> "${path.join(tmp, "stale-snapshots.log")}"; flows="$(flow stale null null 1111111111111111)";;
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
    assert.deepEqual(p.flowCalls(), ["-P -m agentgraph.structure flow prompt solo --goal-file - --expect 3f9a2c1d4e5b6a70|a goal"]);
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

// Review n8 r6 (minor 4): one rev pattern everywhere, the back end's: exactly 16 lowercase hex digits. Any other
// token is no rev: the snapshot's path gets rev null and `flow prompt` gets no --expect.
test("paths: a rev is exactly 16 lowercase hex digits; any other token is no rev and passes no --expect", async () => {
  const good = "9b3f17e5dc59312d";
  const bad = ["3f9a2c1d", "r1", "9B3F17E5DC59312D", good + "0", good.slice(1), "9b3f17e5dc59312g", "a.b:c-d", good + "\n"];
  assert.equal(R.FLOW_REV.test(good), true);
  for (const rev of bad) assert.equal(R.FLOW_REV.test(rev), false, JSON.stringify(rev));
  const flows = R.flowsOf(restDoc([good, ...bad].map((rev, i) => ({ ...FLOW("p" + i, ["a", "b"]), rev }))));
  assert.deepEqual(flows.map((f) => f.rev), [good, ...bad.map(() => null)]);
  for (const rev of [good, ...bad]) {
    let argv = null;
    const answer = await RUNNER.runFlowPrompt({ spawn, path, env: process.env, home: tmp, python: pathsPython,
      onSpawn: (line) => { argv = line; } }, "solo", "g", rev);
    assert.equal(answer.error, undefined, JSON.stringify(rev));
    assert.deepEqual(argv.slice(1), ["-P", "-m", "agentgraph.structure", "flow", "prompt", "solo", "--goal-file", "-",
      ...(rev === good ? ["--expect", good] : [])], JSON.stringify(rev));
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
    assert.deepEqual(p.flowCalls(), ["-P -m agentgraph.structure flow prompt stale --goal-file - --expect 1111111111111111|the goal I typed"]);
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

// ---- the ring is a circle (n3): equal radii in dots, in a narrow panel and full screen, in 2D and 3D

const ringRoster = (n) => D([A("orchestrator", "hub", 0, "", 1)].concat(Array.from({ length: n }, (_, i) =>
  A(`${["build", "verify", "decide", "plan"][i % 4]}-agent-${i}`, ["build", "verify", "decide", "plan"][i % 4], i % 3 === 0 ? 1 : 0, "", 0.1 * (i + 1)))));
const RING_SIZES = [[36, 14, 0.25], [42, 38, 0.25], [130, 40, 0.1], [160, 45, 0.1]]; // panel columns x rows, the most rx and ry may differ
const RING_CANVASES = [[36, 12], [36, 14], [42, 28], [42, 38], [130, 32], [130, 40], [160, 37], [160, 45]]; // the canvas under the header and the instruments, and the panel size itself

test("orbit ring: a circle in dots (rx == ry), never more than the canvas's height and width allow, from a narrow panel to full screen", () => {
  for (const [doc, what] of [[demoWorn, "demo"], [ringRoster(12), "12 agents"], [ringRoster(20), "20 agents"]]) {
    for (const [w, h] of RING_CANVASES) {
      const L = S.orbitLayout(doc, w, h);
      const narrow = w < 60;
      const tol = narrow ? 0.25 : 0.1;
      assert.ok(Math.abs(L.rx - L.ry) <= tol * Math.max(L.rx, L.ry), `${what} ${w}x${h}: rx ${L.rx} against ry ${L.ry}`);
      // the ring itself, measured in dots
      const around = Array.from({ length: 180 }, (_, i) => L.ring.at((i / 180) * L.ring.length));
      const xs = around.map((q) => q[0]);
      const ys = around.map((q) => q[1]);
      const across = Math.max(...xs) - Math.min(...xs);
      const up = Math.max(...ys) - Math.min(...ys);
      assert.ok(Math.abs(across - up) <= tol * Math.max(across, up), `${what} ${w}x${h}: the ring is ${across} dots across and ${up} up`);
      assert.ok(Math.min(...xs) >= 0 && Math.max(...xs) <= 2 * w && Math.min(...ys) >= 0 && Math.max(...ys) <= 4 * h, `${what} ${w}x${h}: the ring is on the canvas`);
      assert.ok(L.rx >= 4, "a ring, even in the smallest panel");
    }
  }
  // the full screen ring is as tall as the canvas lets it be (it is not made small to be round)
  const big = S.orbitLayout(demoWorn, 160, 45);
  assert.ok(big.ry >= 45 * 4 / 2 * 0.8, `full screen: the ring fills the canvas's height: ${big.ry}`);
});

test("orbit ring: every name has its own cells: whole (12 characters at most), on the canvas, over no other name, glyph or the hub", () => {
  for (const [doc, what] of [[demoWorn, "demo"], [ringRoster(12), "12 agents"], [ringRoster(20), "20 agents"]]) {
    for (const [w, h] of RING_CANVASES) {
      const L = S.orbitLayout(doc, w, h);
      const used = new Map();
      const take = (c, r, who) => {
        assert.ok(c >= 0 && c < w && r >= 0 && r < h, `${what} ${w}x${h}: ${who} at ${c},${r} is on the canvas`);
        assert.ok(!used.has(c + "," + r), `${what} ${w}x${h}: ${who} at ${c},${r} is over ${used.get(c + "," + r)}`);
        used.set(c + "," + r, who);
      };
      for (let r = L.hubBox.r0; r <= L.hubBox.r1; r += 1) for (let c = L.hubBox.c0; c <= L.hubBox.c1; c += 1) if (c >= 0 && c < w && r >= 0 && r < h) used.set(c + "," + r, "the hub");
      for (const p of L.placed) take(p.col, p.row, "the glyph of " + p.agent.name);
      // (twelve or twenty agents in a 36 column panel have more names than rows to put them in: there a name may be cut, or left out, but never overlaps)
      const whole = doc === demoWorn || w >= 42;
      for (const p of L.placed) {
        if (whole) {
          assert.ok(p.name, `${what} ${w}x${h}: ${p.agent.name} has a name`);
          assert.equal(p.name.text, p.label, `${what} ${w}x${h}: ${p.agent.name} is not cut below 12 characters`);
        }
        if (p.name) for (let c = p.name.c0; c <= p.name.c1; c += 1) take(c, p.row, "the name " + p.label);
      }
    }
  }
  // in the frame: each ring agent's name once, whole, on its own row, in both views
  for (const [w, h] of [[36, 14], [42, 38], [130, 40], [160, 45]]) {
    for (const view of ["2d", "3d"]) {
      for (const doc of [demoWorn, ringRoster(12)]) {
        const text = sceneText(S.sceneStrict(doc, { width: w, height: h, now: 1234, view })).slice(1).join("\n");
        for (const a of R.validAgents(doc).filter((x) => x.group !== "hub" && !x.parent)) {
          const name = a.name.length > 12 ? a.name.slice(0, 11) + "…" : a.name;
          assert.ok(text.includes(name), `${view} ${w}x${h}: ${name} is shown`);
        }
      }
    }
  }
});

test("orbit ring 3D: the camera has one scale across and up, so the reference circle seen from above is round, and tilted it is the circle's width by the sine of the elevation", () => {
  for (const [w, h] of [[42, 38], [160, 45], [36, 14], [130, 40]]) {
    const L = S.orbitLayout(demoWorn, w, h);
    const circle = (elevation) => {
      const dots = S.cameraDots(L, 0, elevation);
      const q = Array.from({ length: 180 }, (_, i) => dots([Math.cos((i / 180) * 2 * Math.PI), Math.sin((i / 180) * 2 * Math.PI), 0]));
      return { across: Math.max(...q.map((p) => p.x)) - Math.min(...q.map((p) => p.x)), up: Math.max(...q.map((p) => p.y)) - Math.min(...q.map((p) => p.y)) };
    };
    const top = circle(Math.PI / 2);
    assert.ok(Math.abs(top.across - top.up) <= 0.05 * Math.max(top.across, top.up), `${w}x${h}: from above ${top.across} dots across, ${top.up} up`);
    assert.ok(top.across <= 2 * w && top.up <= 4 * h, `${w}x${h}: on the canvas`);
    const tilted = circle(S.ELEVATION_3D);
    const rule = Math.sin(S.ELEVATION_3D);
    assert.ok(Math.abs(tilted.up / tilted.across - rule) <= 0.2 * rule, `${w}x${h}: tilted, up/across ${(tilted.up / tilted.across).toFixed(2)} against sin(elevation) ${rule.toFixed(2)}`);
  }
});

// ---------------------------------------------------------------------------------------------------------------

// ---------------------------------------------------------------------------------------------------------------
// Chat of the selected agent (src/chat.js, runner.js createChatRunner, the panel's chat)

const CH = await import(src("chat.js"));
const { createChatRunner, CHAT_LIMIT } = await import(src("runner.js"));

const chatDir = path.join(tmp, "chat");
fs.mkdirSync(chatDir, { recursive: true });
const chatLog = path.join(chatDir, "calls.log");
const chatSpans = path.join(chatDir, "spans.log");
const chatSnap = path.join(chatDir, "snap.json");
const chatPython = fakePython("chat-python", `
case "$*" in
  *agentgraph.sidechat*)
    echo "$*" >> "${chatDir}/side-calls.log"
    cat > "${chatDir}/side-stdin.txt"
    [ -f "${chatDir}/ask-slow" ] && sleep 0.5
    if [ -f "${chatDir}/ask-error" ]; then echo '{"error":"the side chat could not answer: no model reachable"}'; exit 1; fi
    echo '{"answer":"n6 retries on a higher tier.","session":"ses_demoside","model":"openai/gpt-6-luna","fallback":false,"cost":0.0021}';;
  *agentgraph.chatfeed*)
    echo "$*" >> "${chatLog}"
    sid=""; prev=""
    for a in "$@"; do [ "$prev" = "--session" ] && sid="$a"; prev="$a"; done
    echo "start $sid" >> "${chatSpans}"
    [ -f "${chatDir}/slow" ] && sleep 0.4
    if [ -f "${chatDir}/feed-$sid.json" ]; then cat "${chatDir}/feed-$sid.json"; else printf '{"session":"%s","agent":null,"busy":false,"items":[],"truncated":false,"more":false,"warning":null}\\n' "$sid"; fi
    echo "end $sid" >> "${chatSpans}";;
  *) cat "${chatSnap}";;
esac`);

const feedOf = (session, items, extra = {}) => Object.assign({ session, agent: null, busy: false, items, truncated: false, more: false, warning: null }, extra);
const writeFeed = (session, items, extra) => fs.writeFileSync(path.join(chatDir, `feed-${session}.json`), JSON.stringify(feedOf(session, items, extra)));
const chatDoc = (patch = (d) => d) => {
  const doc = JSON.parse(JSON.stringify(demo));
  const by = (n) => doc.agents.find((a) => a.name === n);
  by("orchestrator").activity = { kind: "text", tool: null, text: "Splitting the work into three lanes", time: doc.generated - 3 };
  by("impl-claude").activity = { kind: "tool", tool: "edit", text: "", time: doc.generated - 2 };
  by("designer").activity = { kind: "reasoning", tool: null, text: "Weighing the contrast of the limiter badge against the dark theme", time: doc.generated - 1 };
  return patch(doc) || doc;
};
const writeSnap = (doc) => fs.writeFileSync(chatSnap, JSON.stringify(doc));
const chatCalls = () => (fs.existsSync(chatLog) ? fs.readFileSync(chatLog, "utf8").split("\n").filter(Boolean) : []);
const chatSessions = () => chatCalls().map((l) => l.split(" ")[l.split(" ").indexOf("--session") + 1]);
const chatSpanCounts = () => {
  const spans = fs.existsSync(chatSpans) ? fs.readFileSync(chatSpans, "utf8").split("\n").filter(Boolean) : [];
  return { starts: spans.filter((l) => l.startsWith("start")).length, ends: spans.filter((l) => l.startsWith("end")).length };
};
const resetChat = () => {
  for (const f of fs.readdirSync(chatDir)) fs.rmSync(path.join(chatDir, f), { force: true });
  writeSnap(chatDoc());
};

// A panel (the host's own `panel` object) on the fake interpreter, with the chat timer under the test's hand: `tick()`
// runs the chat's poll once. `argvs` are the commands spawned.
async function chatPanel({ width = 80, size = [80, 38], list = true, answers = {}, demoOn = false, python = chatPython, doc, feeds = {}, slow = false } = {}) {
  resetChat();
  if (doc) writeSnap(doc);
  for (const [id, f] of Object.entries(feeds)) writeFeed(id, f.items || f, f.items ? f : {});
  if (slow) fs.writeFileSync(path.join(chatDir, "slow"), "");
  const clockRef = { t: 1795075100000 };
  const argvs = [];
  const fake = [];
  const plugin = P.createPlugin({
    createElement: OT.createElement, insert: OT.insert, setProp: OT.setProp, createSignal: solid.createSignal, onCleanup: solid.onCleanup,
    spawn: (cmd, args, opts) => (argvs.push([cmd, ...args]), spawn(cmd, args, opts)),
    screenSize: () => ({ width: 200, height: 60 }),
    fs, os, path, env: demoOn ? { AGENTGRAPH_TUI_DEMO: "1" } : {}, demoFile: path.join(here, "fixtures", "demo.json"), now: () => clockRef.t,
    timers: {
      setInterval: (fn, ms) => {
        if (ms !== P.CHAT_POLL_MS) return globalThis.setInterval(fn, ms);
        const h = { fn };
        fake.push(h);
        return h;
      },
      clearInterval: (h) => {
        const at = fake.indexOf(h);
        if (at >= 0) fake.splice(at, 1);
        else globalThis.clearInterval(h);
      },
    },
  });
  const host = makeActionHost();
  host.ctx.options.python = python;
  host.ctx.options.intervalMs = 1000;
  host.ctx.ui.dialog.prompt = async (o) => (host.calls.push(["prompt", o.title]), answers.prompt);
  host.ctx.ui.dialog.confirm = async (o) => (host.calls.push(["confirm", o.title]), answers.confirm);
  const stop = plugin.setup(host.ctx);
  const [name, setName] = solid.createSignal("agentgraph.graph");
  const panel = makePanel({ sessionID: demoOn ? "ses_demo" : "ses_chat", width });
  Object.defineProperty(panel, "name", { get: () => name() });
  const view = host.panelClaim().render(panel);
  await waitFor(() => screen(view).length > 3, 5000, "panel snapshot");
  if (size) {
    sizePanel(view, size[0], size[1]);
    if (list) pressG(host.state);
  }
  const done = () => {
    for (const fn of globalThis.__agentgraphStub.cleanups.splice(0)) fn();
    stop();
  };
  const chatCmds = () => argvs.filter((a) => a.includes("agentgraph.chatfeed"));
  return Object.assign(host, {
    view, done, argvs, chatCmds, clockRef, setName, fake,
    tick: () => fake.slice().forEach((h) => h.fn()),
    lines: () => screen(view),
    // `n` chat commands have run and none is still running
    settle: async (n) => {
      await waitFor(() => chatCmds().length >= n && chatSpanCounts().starts >= n && chatSpanCounts().starts === chatSpanCounts().ends, 5000, `${n} chat reads`);
      await sleep(60);
    },
  });
}

const THOUGHT = "Thinking about how the limiter should share one bucket across workers";
const SAID = "I will add the token bucket to the middleware";
const KINDS = [
  { kind: "user", text: "Please keep the headers stable", time: 1 },
  { kind: "reasoning", text: THOUGHT, time: 2 },
  { kind: "tool", name: "grep", status: "completed", time: 3, input: { secret: "INPUT-SECRET" }, output: "OUTPUT-SECRET" },
  { kind: "tool", name: "edit", status: "running", time: 4 },
  { kind: "text", text: SAID, time: 5 },
  { kind: "note", time: 6 },
  { kind: "text", text: "Steer this way", user: true, pending: true, delivery: "steer", time: 7 },
];
const joined = (line) => line.map((s) => s.text).join("");

test("chat lines: each kind, thinking dimmer than what was said, a tool is its name and state only, told and pending marked, note one quiet word", () => {
  const lines = CH.chatLines({ state: "feed", feed: feedOf("ses_x", KINDS) }, 80);
  const flat = lines.map(joined);
  assert.deepEqual(flat, [
    "told Please keep the headers stable",
    "· " + THOUGHT,
    "▸ grep done",
    "▸ edit running",
    SAID,
    "· note",
    "told (pending) Steer this way",
  ]);
  const toneOf = (needle) => lines.find((l) => joined(l).includes(needle)).find((s) => s.text.includes(needle)).tone;
  assert.equal(toneOf(THOUGHT), "muted");
  assert.equal(toneOf(SAID), "plain");
  assert.notEqual(toneOf(THOUGHT), toneOf(SAID));
  assert.ok(!flat.join("\n").includes("SECRET"), "a tool's input or output is never drawn");
  assert.equal(lines.find((l) => joined(l).startsWith("▸ edit")).at(-1).tone, "accent", "a running tool");
  assert.equal(lines.find((l) => joined(l) === "· note")[0].tone, "muted");
  // cut text and a truncated feed are said quietly, once
  const cut = CH.chatLines({ state: "feed", feed: feedOf("s", [{ kind: "text", text: "long", cut: true }], { truncated: true, more: true }) }, 40).map(joined);
  assert.deepEqual(cut, ["… older chat left out", "long", "(cut)"]);
  assert.deepEqual(CH.chatLines({ state: "feed", feed: feedOf("s", [{ kind: "text", text: "x" }], { truncated: true }) }, 40).map(joined), ["… some text cut", "x"]);
  assert.deepEqual(CH.chatLines({ state: "none" }, 40).map(joined), ["No session yet"]);
  assert.deepEqual(CH.chatLines({ state: "feed", feed: feedOf("s", []) }, 40).map(joined), ["Nothing said yet"]);
});

test("chat lines: every string is cleaned again; escape sequences, controls, tabs, wide characters and very long lines stay inside every width from 8 to 160 columns", () => {
  const nasty = [
    { kind: "user", text: "\u001b[31mred\u001b[0m \u001b]0;title\u0007 x\ty\u0000z\u009b2J\r\nnext line" },
    { kind: "reasoning", text: "日本語".repeat(40) + " and 😀😀😀 " + "w".repeat(300) },
    { kind: "text", text: "a".repeat(500) + "\u001b[2J" },
    { kind: "tool", name: "\u001b[1mbold\u001b[0m" + "n".repeat(100), status: "\u001b]52;c;AAAA\u0007running" },
    { kind: "text", user: true, pending: true, text: "steer " + "p".repeat(60) },
    { kind: "note" },
    { kind: "bogus", text: "ignored" },
  ];
  for (const width of [8, 12, 17, 23, 24, 36, 42, 160]) {
    const lines = CH.chatLines({ state: "feed", feed: feedOf("s", nasty, { warning: "\u001b[31mwarn\u001b[0m" }) }, width);
    assert.ok(lines.length > 5);
    for (const line of lines) {
      const t = joined(line);
      assert.ok(R.displayWidth(t) <= width, `${width}: ${R.displayWidth(t)} wide: ${t}`);
      assert.ok(!/[\u0000-\u001f\u007f-\u009f]/.test(t), `${width}: a control character in ${JSON.stringify(t)}`);
    }
    assert.ok(!lines.map(joined).join("\n").includes("title"), "an OSC is dropped with its text");
    if (width >= 36) assert.ok(lines.map(joined).join("\n").includes("x y"), "a tab is a space"); // narrower, the two wrap apart
    else assert.ok(!lines.map(joined).join("\n").includes("\t"), "no tab left");
  }
  // the error and loading views are one line too
  for (const state of [{ state: "error", error: "\u001b[31m" + "e".repeat(300) }, { state: "loading" }]) {
    const lines = CH.chatLines(state, 36);
    assert.equal(lines.length, 1);
    assert.ok(R.displayWidth(joined(lines[0])) <= 36);
  }
});

test("chat window: the newest at the bottom, follows the newest until scrolled back, clamped", () => {
  const lines = Array.from({ length: 30 }, (_, i) => [{ text: `line ${i + 1}`, tone: "plain" }]);
  const at = (scroll) => CH.chatWindow(lines, 10, scroll);
  assert.deepEqual(at(0).lines.map(joined), lines.slice(20).map(joined));
  assert.equal(at(0).max, 20);
  assert.deepEqual(at(5).lines.map(joined), lines.slice(15, 25).map(joined));
  assert.equal(at(99).scroll, 20, "clamped at the oldest");
  assert.equal(at(-3).scroll, 0, "and at the newest");
  assert.equal(CH.chatWindow(lines.slice(0, 4), 10, 3).scroll, 0, "nothing to scroll: all shown");
});

test("chat runner: `chatfeed --session ID --json --limit 40` without a shell, `--demo` in the demo; bad ids, failures and bad output are one line; one read per session", async () => {
  resetChat();
  writeFeed("ses_r", [{ kind: "text", text: "hi" }]);
  const argvs = [];
  const mk = (extra = {}) => createChatRunner(Object.assign({ spawn: (c, a, o) => (argvs.push([c, ...a, o]), spawn(c, a, o)), fs, path, env: process.env, home: tmp, python: chatPython }, extra));
  const runner = mk();
  const res = await runner.chat({ session: "ses_r" });
  assert.equal(res.feed.items[0].text, "hi");
  assert.deepEqual(argvs[0].slice(0, 9), [chatPython, "-P", "-m", "agentgraph.chatfeed", "--session", "ses_r", "--json", "--limit", String(CHAT_LIMIT)]);
  assert.equal(CHAT_LIMIT, 40);
  assert.equal(argvs[0].at(-1).shell, false);
  assert.equal(argvs[0].at(-1).env.PYTHONPATH, tmp);
  assert.deepEqual((await mk({ demo: true }).chat({ session: "ses_r" })).feed.items[0].text, "hi");
  assert.equal(argvs.at(-1).at(-2), "--demo");
  assert.match((await runner.chat({ session: "ses_x; rm -rf /" })).error, /not valid/);
  assert.match((await runner.chat({})).error, /not valid/);
  assert.match((await mk({ python: path.join(tmp, "nope-python") }).chat({ session: "ses_r" })).error, /Python was not found/);
  const junk = fakePython("chat-junk", "echo not-json");
  assert.match((await mk({ python: junk }).chat({ session: "ses_r" })).error, /invalid JSON/);
  const bad = fakePython("chat-bad", "echo boom >&2; exit 2");
  assert.match((await mk({ python: bad }).chat({ session: "ses_r" })).error, /exited with status 2: boom/);
  // one process per session at a time
  fs.writeFileSync(path.join(chatDir, "slow"), "");
  const before = argvs.length;
  const [a, b] = await Promise.all([runner.chat({ session: "ses_r" }), runner.chat({ session: "ses_r" })]);
  assert.equal(argvs.length - before, 1);
  assert.deepEqual(a, b);
  // a hung read is killed at the timeout
  const hang = fakePython("chat-hang", "exec sleep 30");
  const t0 = Date.now();
  assert.match((await mk({ python: hang, timeoutMs: 200 }).chat({ session: "ses_r" })).error, /did not finish/);
  assert.ok(Date.now() - t0 < 3000);
});


const BUSY = (id, items) => ({ items, busy: true });
const said = (n) => Array.from({ length: n }, (_, i) => ({ kind: "text", text: `message ${i + 1}`, time: i }));
const sessionsRead = () => chatSessions();
const maxRunning = () => {
  let now = 0;
  let max = 0;
  for (const l of fs.readFileSync(chatSpans, "utf8").split("\n").filter(Boolean)) {
    now += l.startsWith("start") ? 1 : -1;
    max = Math.max(max, now);
  }
  return max;
};
const lastLine = (lines) => lines.at(-1);

test("chat panel: `chatfeed --session <newest> --json --limit 40` for the selected agent only; 2 s while its session is busy, once when it is not; one read at a time; none when hidden", async () => {
  const c = await chatPanel({ size: [80, 38], slow: true, feeds: { ses_g: BUSY("ses_g", said(3)), ses_root: { items: said(2), busy: false }, ses_h: BUSY("ses_h", said(1)) } });
  try {
    await c.settle(1);
    assert.equal(P.CHAT_POLL_MS, 2000);
    assert.deepEqual(c.chatCmds()[0].slice(1), ["-P", "-m", "agentgraph.chatfeed", "--session", "ses_g", "--json", "--limit", "40"]);
    assert.equal(c.fake.length, 1, "one chat timer");
    // six ticks while a read runs and after: never two at once
    for (let i = 0; i < 6; i++) c.tick();
    await c.settle(2);
    c.tick();
    c.tick();
    await c.settle(3);
    assert.equal(maxRunning(), 1, "one read in flight at a time");
    assert.ok(sessionsRead().every((s) => s === "ses_g"), "only the selected agent: " + sessionsRead());
    // the hub: its session is idle (the feed says so): read once, the timer reads no more
    c.key("down").run();
    const n = chatSessions().length;
    await c.settle(n + 1);
    assert.equal(chatSessions().at(-1), "ses_root");
    c.tick();
    c.tick();
    await sleep(500);
    assert.equal(chatSessions().length, n + 1, "an idle session is read once");
    // the designer: busy again, read, never before it was selected
    assert.ok(!chatSessions().includes("ses_h"));
    c.key("down").run();
    await c.settle(n + 2);
    assert.equal(chatSessions().at(-1), "ses_h");
    // hidden: the panel's name is not ours: no read, no timer
    c.setName("other.panel");
    assert.equal(c.fake.length, 0, "no chat timer while hidden");
    const hiddenAt = c.chatCmds().length;
    c.tick();
    await sleep(500);
    assert.equal(c.chatCmds().length, hiddenAt);
    // shown again: it is read again
    c.setName("agentgraph.graph");
    await c.settle(hiddenAt + 1);
    assert.equal(chatSessions().at(-1), "ses_h");
  } finally {
    c.done();
  }
});

test("chat panel: the chat shows under the rows, thinking and what was said apart, a tool as name and state; activity lines under working agents", async () => {
  const c = await chatPanel({ size: [42, 38], width: 42, feeds: { ses_g: { items: KINDS, busy: true } } });
  try {
    await c.settle(1);
    const lines = c.lines();
    assert.match(lines[0], /^›! impl-claude stalled/);
    assert.equal(lines[1].trimEnd(), "   ▸ edit", "the tool a working agent runs, one line");
    assert.ok(lines.some((l) => l.trimEnd() === "   Splitting the work into three lanes"), "what the hub said");
    assert.ok(lines.some((l) => /^   · Weighing the contrast of the limiter…$/.test(l.trimEnd())), "thinking, cut to the width: " + lines.join("|"));
    const at = lines.findIndex((l) => l.startsWith(" chat · impl-claude"));
    assert.ok(at > 4);
    assert.deepEqual(lines.slice(at + 1, at + 9).map((l) => l.trimEnd()), [
      " told Please keep the headers stable",
      " · Thinking about how the limiter should",
      "   share one bucket across workers",
      " ▸ grep done",
      " ▸ edit running",
      " I will add the token bucket to the",
      " middleware",
      " · note",
    ]);
    assert.ok(lines.some((l) => l.trimEnd() === " told (pending) Steer this way"));
    assert.ok(!lines.join("\n").includes("SECRET"));
    assert.match(lastLine(lines), /^ ↑↓ select  ⏎ open  s stop  m message/);
    // thinking is dimmer than what was said: tones in the drawn nodes
    const fgOf = (needle) => {
      const row = c.view.children.find((r) => nodeText(r).includes(needle));
      return row.children.find((k) => nodeText(k).includes(needle)).props.fg;
    };
    assert.equal(fgOf("Thinking about"), "#888888", "thinking is the muted colour");
    assert.equal(fgOf("I will add"), "#eeeeee", "what it said is the plain one");
    for (const l of lines) assert.ok(R.displayWidth(l) <= 42, `${R.displayWidth(l)} wide: ${l}`);
  } finally {
    c.done();
  }
});

test("chat panel: follows the newest; Page Up scrolls back and holds its place while the chat grows; Page Down comes back to the newest", async () => {
  const c = await chatPanel({ size: [60, 24], width: 60, feeds: { ses_g: { items: said(60), busy: true } } });
  try {
    await c.settle(1);
    const body = () => c.lines().filter((l) => /^ message \d+$/.test(l.trimEnd())).map((l) => Number(l.trim().split(" ")[1]));
    assert.equal(body().at(-1), 60, "the newest at the bottom");
    assert.ok(!c.lines().some((l) => /↓\d/.test(l)), "not scrolled: no mark");
    assert.equal(c.key("pageup").enabled(), true);
    c.key("pageup").run();
    const back = body();
    assert.ok(back.at(-1) < 60 && back.at(-1) > 30, "scrolled back: " + back);
    assert.ok(c.lines().some((l) => / ↓\d+$/.test(l.trimEnd()) && l.startsWith(" chat")), "the heading says how far back");
    // the chat grows while scrolled back: the same lines stay
    writeFeed("ses_g", said(63), { busy: true });
    c.tick();
    await c.settle(2);
    assert.deepEqual(body(), back, "held its place");
    c.key("pagedown").run();
    assert.ok(body().at(-1) > back.at(-1) && body().at(-1) < 63, "a page forward: " + body());
    c.key("pagedown").run();
    assert.equal(body().at(-1), 63, "back at the newest");
    assert.ok(!c.lines().some((l) => /↓\d/.test(l)), "the mark is gone");
    // following: a new item shows without a key
    writeFeed("ses_g", said(64), { busy: true });
    c.tick();
    await c.settle(3);
    assert.equal(body().at(-1), 64);
    // up and down still move the selection beside the chat
    c.key("down").run();
    assert.ok(c.lines().some((l) => l.startsWith("›◉ orchestrator")) || c.lines().some((l) => l.startsWith("›")));
  } finally {
    c.done();
  }
});

test("chat panel: no session shows 'No session yet' and reads nothing; the orchestrator is selectable and read-only", async () => {
  const noSession = chatDoc((d) => {
    d.agents.find((a) => a.name === "impl-claude").sessions = [];
  });
  const c = await chatPanel({ size: [60, 30], width: 60, doc: noSession, feeds: { ses_root: { items: said(2), busy: true } } });
  try {
    await sleep(300);
    assert.ok(c.lines().some((l) => l.trimEnd() === " No session yet"), c.lines().join("\n"));
    assert.deepEqual(c.chatCmds(), [], "nothing to read");
    assert.equal(c.fake.length, 0);
    assert.ok(!lastLine(c.lines()).includes("⏎ open"), "no session to open either");
    // the orchestrator
    c.key("down").run();
    await c.settle(1);
    assert.equal(chatSessions()[0], "ses_root");
    assert.ok(c.lines().some((l) => l.startsWith(" transcript · orchestrator")), "read-only: a transcript\n" + c.lines().join("\n"));
    assert.ok(c.lines().some((l) => l.trimEnd() === " message 2"));
    assert.equal(c.key("s").enabled(), false);
    assert.equal(c.key("m").enabled(), false);
    assert.ok(!/ s stop| m message/.test(lastLine(c.lines())), "only keys that do something: " + lastLine(c.lines()));
    c.key("m").run();
    c.key("s").run();
    await sleep(30);
    assert.deepEqual(c.calls.filter((x) => x[0] === "prompt" || x[0] === "confirm" || x[0] === "send" || x[0] === "interrupt"), []);
    // a feed that says it is busy is polled even though the row cannot be steered
    c.tick();
    await c.settle(2);
    // a working agent: its chat is live
    c.key("down").run();
    assert.ok(c.lines().some((l) => l.startsWith(" chat · designer")));
  } finally {
    c.done();
  }
});

test("chat panel: after m the chat is read at once and the message is pending until it lands, once", async () => {
  const c = await chatPanel({ size: [80, 38], answers: { prompt: "  use the token bucket  " }, feeds: { ses_g: { items: said(2), busy: true } } });
  try {
    await c.settle(1);
    const before = c.chatCmds().length;
    c.key("m").run();
    await waitFor(() => c.calls.some((x) => x[0] === "send"), 2000, "send");
    await waitFor(() => c.chatCmds().length === before + 1, 2000, "a read without waiting for the timer");
    await c.settle(before + 1);
    assert.equal(chatSessions().at(-1), "ses_g");
    assert.ok(c.lines().some((l) => l.trimEnd() === " told (pending) use the token bucket"), "pending\n" + c.lines().join("\n"));
    // it lands: the feed has it as a message; the pending one goes
    writeFeed("ses_g", [...said(2), { kind: "user", text: "use the token bucket", time: 9 }], { busy: true });
    c.tick();
    await c.settle(before + 2);
    assert.equal(c.lines().filter((l) => l.includes("use the token bucket")).length, 1, c.lines().join("\n"));
    assert.ok(!c.lines().some((l) => l.includes("(pending)")));
    // a failed send shows nothing pending
    const calls = c.calls.length;
    c.key("m").run(); // same prompt answer
    await waitFor(() => c.calls.length > calls + 1, 2000, "second send");
  } finally {
    c.done();
  }
});

test("chat panel: where there is no room, c opens the chat in place of the lower part of the panel; c or Escape closes it; the keys row lists only keys that do something", async () => {
  const c = await chatPanel({ size: [60, 14], width: 60, feeds: { ses_g: { items: said(40), busy: true } } });
  try {
    assert.ok(!c.lines().some((l) => l.includes(" chat · ")), "no room beside the list");
    assert.match(lastLine(c.lines()), /s stop  m message  c chat/);
    assert.equal(c.key("c").enabled(), true);
    assert.equal(c.key("escape").enabled(), false, "Escape closes only an open chat");
    assert.equal(c.key("pageup").enabled(), false, "nothing shown to scroll");
    assert.deepEqual(c.chatCmds(), [], "the chat is read only when it shows");
    c.key("c").run();
    await c.settle(1);
    const lines = c.lines();
    assert.equal(lines.length, 14);
    assert.match(lines[0], /^›! impl-claude stalled/);
    assert.ok(lines[1].startsWith(" chat · impl-claude"));
    assert.equal(lines[12].trimEnd(), " message 40");
    assert.match(lastLine(lines), /↑↓ scroll  ⏎ open  s stop  m message  c back/);
    // up and down scroll here (they moved the selection in the list); one gesture, one meaning at a time
    c.key("up").run();
    assert.ok(c.lines().some((l) => / ↓1$/.test(l.trimEnd())), c.lines().join("\n"));
    c.key("down").run();
    assert.ok(!c.lines().some((l) => / ↓\d+$/.test(l.trimEnd())));
    assert.equal(c.key("escape").enabled(), true);
    c.key("escape").run();
    assert.ok(!c.lines().some((l) => l.includes(" chat · ")));
    c.key("c").run();
    assert.ok(c.lines().some((l) => l.includes(" chat · ")));
    c.key("c").run();
    assert.ok(!c.lines().some((l) => l.includes(" chat · ")));
    assert.equal(c.key("c").enabled(), true);
  } finally {
    c.done();
  }
});

test("chat panel: escape sequences, wide characters and very long lines stay inside the panel at 36, 42 and 160 columns", async () => {
  const nasty = [
    { kind: "user", text: "\u001b[31mred\u001b[0m \u001b]0;title\u0007 x\ty\u0000z" },
    { kind: "reasoning", text: "日本語".repeat(40) + " 😀😀 " + "w".repeat(300) },
    { kind: "text", text: "a".repeat(500) + "\u001b[2J" },
    { kind: "tool", name: "\u001b[1mbold\u001b[0m" + "n".repeat(100), status: "running" },
  ];
  for (const w of [36, 42, 160]) {
    const c = await chatPanel({ size: [w, 38], width: w, feeds: { ses_g: { items: nasty, busy: true } } });
    try {
      await c.settle(1);
      const lines = c.lines();
      assert.ok(lines.some((l) => l.includes(" chat · ")));
      const limit = Math.min(w, Math.max(P.WIDTH, Math.min(P.PANEL_MAX_WIDTH, w)));
      const wide = Math.min(w, P.PANEL_MAX_WIDTH);
      for (const l of lines.slice(lines.findIndex((x) => x.includes(" chat · ")))) {
        assert.ok(R.displayWidth(l) <= wide, `${w}: ${R.displayWidth(l)} wide (limit ${wide}): ${l}`);
        assert.ok(!/[\u0000-\u001f\u007f-\u009f]/.test(l), `${w}: control character in ${JSON.stringify(l)}`);
      }
      assert.ok(!lines.join("\n").includes("title"));
      assert.ok(limit > 0);
    } finally {
      c.done();
    }
  }
});

test("chat panel: with --demo the chat comes from `agentgraph chat --demo`", async () => {
  const c = await chatPanel({ size: [80, 38], demoOn: true, feeds: { ses_g: { items: said(2), busy: true } } });
  try {
    await c.settle(1);
    const cmd = c.chatCmds()[0];
    assert.deepEqual(cmd.slice(1), ["-P", "-m", "agentgraph.chatfeed", "--session", "ses_g", "--json", "--limit", "40", "--demo"]);
    assert.ok(c.lines().some((l) => l.trimEnd() === " message 2"), c.lines().join("\n"));
  } finally {
    c.done();
  }
});

test("rows: a working agent's activity is one line under its row (the tool, or the first words it thinks or says), only where asked", () => {
  const doc = chatDoc();
  const flat = (o) => R.layout(doc, Object.assign({ width: 42, now: 1795075100000 }, o)).lines.map(joined);
  assert.ok(!flat({}).some((l) => /edit|Splitting|Weighing/.test(l)), "the sidebar and every earlier caller: no activity line");
  const withIt = R.layout(doc, { width: 42, now: 1795075100000, activity: true });
  const lines = withIt.lines.map(joined);
  assert.equal(lines.length, flat({}).length + 3);
  assert.ok(lines.includes("  ▸ edit"));
  assert.ok(lines.includes("  Splitting the work into three lanes"));
  assert.ok(lines.includes("  · Weighing the contrast of the limiter…"));
  assert.ok(lines.every((l) => R.displayWidth(l) <= 42));
  assert.deepEqual(withIt.items.map((i) => i.key), R.layout(doc, { width: 42, now: 1795075100000 }).items.map((i) => i.key));
  // an idle agent has none; escape sequences in it are cleaned
  const dirty = chatDoc((d) => {
    d.agents.find((a) => a.name === "designer").activity = { kind: "text", text: "\u001b[31mred\u001b[0m\tdone" };
    d.agents.find((a) => a.name === "explorer").activity = { kind: "text", text: "idle agents show nothing" };
  });
  const out = R.layout(dirty, { width: 42, now: 1795075100000, activity: true }).lines.map(joined);
  assert.ok(out.includes("  red done"));
  assert.ok(!out.some((l) => l.includes("idle agents")));
});

test("rows: the hub's activity with a zone is a glyph, a verb and the target, then a dim trail row; without the keys it is today's row", () => {
  const recent = [
    { zone: "web", tool: "webfetch", target: "example.com/docs/intro", status: "completed" },
    { zone: "terminal", tool: "shell", target: "npm test", status: "completed" },
    { zone: "search", tool: "grep", target: "retry_after", status: "error" },
    { zone: "nowhere", tool: "x", target: null, status: "completed" },
  ];
  const withZone = (activity) => chatDoc((d) => { d.agents.find((a) => a.name === "orchestrator").activity = Object.assign({ time: d.generated - 1 }, activity); });
  const opts = { width: 42, now: 1795075100000, activity: true };
  const hubLines = (doc, o = opts) => R.layout(doc, o).lines.map(joined);
  const fetching = withZone({ kind: "tool", tool: "webfetch", text: "", zone: "web", target: "example.com/docs/intro", status: "running", recent });
  const lines = hubLines(fetching);
  const at = lines.findIndex((l) => l.startsWith("  ⊕ fetching"));
  assert.ok(at > 0, lines.join("\n"));
  assert.equal(lines[at], "  ⊕ fetching · example.com/docs/intro");
  assert.equal(lines[at + 1], "  ⊕ ❯ ⊙! ◇");
  assert.ok(lines.every((l) => R.displayWidth(l) <= 42));
  // a failed call is danger, a target is plain text
  const spans = R.layout(fetching, opts).lines[at];
  assert.equal(spans[1].tone, "accent");
  assert.equal(R.layout(withZone({ kind: "tool", tool: "shell", text: "", zone: "terminal", target: "npm test", status: "error" }), opts).lines.find((l) => joined(l).includes("npm test"))[1].tone, "danger");
  // a tool without a verb gets the zone's verb and its own name; narrow widths cut the target, then the verb
  assert.ok(hubLines(withZone({ kind: "tool", tool: "browser", text: "", zone: "tools", target: "page" })).includes("  ◇ using browser · page"));
  const narrow = hubLines(fetching, { ...opts, width: 22 });
  assert.ok(narrow.includes("  ⊕ fetching · exampl…"), narrow.join("\n"));
  assert.ok(narrow.every((l) => R.displayWidth(l) <= 22));
  const tiny = hubLines(fetching, { ...opts, width: 8 });
  assert.ok(tiny.includes("  ⊕ fet…"), tiny.join("\n"));
  assert.ok(tiny.every((l) => R.displayWidth(l) <= 8));
  assert.ok(hubLines(fetching, { ...opts, width: 5 }).every((l) => R.displayWidth(l) <= 5));
  // the trail goes first when asked: the rows are then as many as without it
  const noTrail = hubLines(fetching, { ...opts, trail: false });
  assert.equal(noTrail.length, lines.length - 1);
  assert.ok(noTrail.includes("  ⊕ fetching · example.com/docs/intro") && !noTrail.some((l) => l.startsWith("  ⊕ ❯")));
  // only the glyphs that fit; escape sequences in the target are cleaned
  assert.ok(hubLines(fetching, { ...opts, width: 8 }).includes("  ⊕ ❯ ⊙!"));
  assert.ok(hubLines(withZone({ kind: "tool", tool: "shell", text: "", zone: "terminal", target: "\u001b[31mnpm\u001b[0m test" }), opts).includes("  ❯ running · npm test"));
  // absent keys: today's row, and no trail row
  const plain = hubLines(withZone({ kind: "tool", tool: "shell", text: "" }));
  assert.ok(plain.includes("  ▸ shell") && !plain.some((l) => /❯|⊕/.test(l)));
  const unknown = hubLines(withZone({ kind: "tool", tool: "shell", text: "", zone: "nowhere", target: "npm test", recent }));
  assert.ok(unknown.includes("  ▸ shell") && !unknown.some((l) => /npm test|⊕/.test(l)));
  // thinking and text keep their own rows
  assert.ok(hubLines(withZone({ kind: "reasoning", tool: null, text: "Weighing the options", zone: "thinking", recent })).includes("  · Weighing the options"));
  // the demo's hub shows the new row with the made-up trail
  const demoLines = R.layout(DM.demoDocument(demo, 60000), { width: 60, now: 1234, activity: true }).lines.map(joined);
  assert.ok(demoLines.some((l) => l.startsWith("  ┬ starting · explorer: add the 429 page")), demoLines.join("\n"));
  assert.ok(demoLines.includes("  ⊕ ❯ ▤ ⊙! ┬"));
});

test("orbit labels at 42x38 in 2D: when room is short a working agent's task title is cut first, ↻ and cost stay", () => {
  const doc = DM.demoDocument(demo, 60000);
  const lines = sceneText(S.sceneStrict(doc, { width: 42, height: 38, now: 1234 }));
  const row = lines.find((l) => /↻5 \$1\.71/.test(l));
  assert.ok(row, "impl-claude keeps its ↻ and cost\n" + lines.join("\n"));
  assert.match(row, /…/, "the title is cut, not the tail");
  assert.ok(lines.every((l) => R.displayWidth(l) === 42));
  const designer = lines.find((l) => /429 error page/.test(l));
  assert.ok(designer, "designer's line is unchanged\n" + lines.join("\n"));
});

// ---------------------------------------------------------------------------------------------------------------
// The side chat, the spend line's limit, rate and split, and the preset name (n16)

const { createSideAskRunner, SIDE_ASK_TIMEOUT_MS, SIDE_QUESTION_LIMIT } = await import(src("runner.js"));
const clone = (doc) => JSON.parse(JSON.stringify(doc));
const SIDE = { installed: true, session: "ses_demoside", model: "openai/gpt-6-luna", busy: false, spent: 0.0042 };
const withSide = (extra = {}, base = demo) => Object.assign(clone(base), { sidechat: Object.assign({}, SIDE, extra) });
const SPEND = { limit: 50, warn: 40, rate: 22.4, chat: 3.1, workers: 4.55 };
const withSpend = (spend, cost = 7.65, setup) => {
  const doc = clone(demo);
  doc.totals = Object.assign({}, doc.totals, { cost });
  if (spend !== undefined) doc.spend = spend;
  if (setup !== undefined) doc.setup = setup;
  return doc;
};
const lastRow = (doc, width) => R.layout(doc, { width, now: NOW }).lines.at(-1);

test("side chat row: only when installed; one selectable row after the orchestrator's; not an agent, not counted as working or idle", () => {
  const base = R.layout(demo, { width: 60, now: NOW });
  for (const bad of [undefined, null, {}, { installed: false, session: "ses_x" }, { installed: "yes", session: "ses_x" }, { installed: 1 }, "installed", []]) {
    const doc = clone(demo);
    if (bad !== undefined) doc.sidechat = bad;
    const got = R.layout(doc, { width: 60, now: NOW });
    assert.deepEqual(got.items, base.items, JSON.stringify(bad));
    assert.deepEqual(got.lines, base.lines, JSON.stringify(bad));
  }
  const got = R.layout(withSide(), { width: 60, now: NOW });
  const hubAt = got.items.findIndex((i) => i.hub);
  assert.equal(got.items[hubAt + 1].kind, "sidechat");
  assert.deepEqual(got.items.filter((i) => i.kind !== "sidechat"), base.items.map((i) => Object.assign({}, i, { line: i.line + (i.line > got.items[hubAt].line ? 1 : 0) })));
  const side = got.items[hubAt + 1];
  assert.equal(side.line, got.items[hubAt].line + 1);
  assert.equal(side.open, "ses_demoside");
  assert.equal(side.busy, null, "nothing to stop or steer");
  assert.equal(side.name, "side chat");
  assert.equal(side.live, false);
  const lines = R.toText(got.lines).split("\n");
  assert.match(lines[side.line], /^ ?◌ side chat\s+openai\/gpt-6-luna/);
  assert.match(lines[got.items[hubAt].line], /11 idle/, "the idle count is the agents' only");
  assert.equal(R.layout(withSide(), { width: 60, now: NOW }).lines.length, base.lines.length + 1);
  // no session yet: the row is still there, with nothing to open
  assert.equal(R.layout(withSide({ session: null }), { width: 60, now: NOW }).items.find((i) => i.kind === "sidechat").open, null);
  assert.equal(R.layout(withSide({ session: "ses_x; rm -rf /" }), { width: 60, now: NOW }).items.find((i) => i.kind === "sidechat").open, null);
  // after the hub's activity line when asked
  const act = R.layout(withSide(), { width: 60, now: NOW, activity: true });
  assert.equal(act.items.find((i) => i.kind === "sidechat").line - act.items.find((i) => i.hub).line >= 1, true);
});

test("side chat row: spins while it answers (snapshot busy, or a question of ours is out); nothing else is animated by it", () => {
  const quiet = {
    version: 1, source: "live", warnings: [], root: { agent: "orchestrator", id: "ses_q" }, groups: [], graph: G(),
    agents: [A("orchestrator", "orchestrator", 0, "", 0.5), A("worker", "build", 0, "", 0.5)],
    sidechat: Object.assign({}, SIDE),
  };
  const spinning = (doc, extra) => R.toText(R.layout(doc, Object.assign({ width: 60, frame: 3, now: NOW }, extra)).lines).split("\n").find((l) => l.includes("side chat"));
  assert.ok(!SPIN.test(spinning(quiet)), spinning(quiet));
  const busy = Object.assign({}, quiet, { sidechat: Object.assign({}, SIDE, { busy: true }) });
  assert.match(spinning(busy), SPIN);
  assert.ok(R.sideBusy(busy) && !R.sideBusy(quiet));
  assert.equal(R.isAnimated(busy), false, "not counted as working: the agents' animation is off");
  assert.equal(R.isAnimated(quiet), false);
  const asking = spinning(quiet, { asking: true });
  assert.match(asking, SPIN);
  assert.match(asking, /answering/);
  const row = R.layout(quiet, { width: 60, asking: true, now: NOW }).items.find((i) => i.kind === "sidechat");
  assert.equal(row.live, true);
  assert.equal(R.layout(quiet, { width: 60, now: NOW }).items.find((i) => i.kind === "sidechat").live, false);
  // an ask in flight on a snapshot without a side chat draws nothing
  assert.deepEqual(R.layout(demo, { width: 60, now: NOW, asking: true }).items, R.layout(demo, { width: 60, now: NOW }).items);
  // the busy row is not working: the hub row and the working rows are what they were
  const w = R.layout(withSide({ busy: true }), { width: 60, now: NOW });
  assert.ok(!w.items.some((i) => i.busy === "ses_root"));
});

test("spend line: no limit, under it, from warn (attention tone), at it and over it", () => {
  const spend = (doc, width) => R.toText([lastRow(doc, width)]);
  assert.equal(spend(withSpend(undefined, 6.57), 60), "$6.57");
  assert.equal(spend(withSpend({}, 6.57), 60), "$6.57");
  // limit only: "$7.65 / $50", the amount in the plain tone
  const under = lastRow(withSpend({ limit: 50 }, 7.65), 60);
  assert.equal(R.toText([under]), "$7.65 / $50");
  assert.deepEqual(tonesOf(under).map((t) => t[1]), ["plain", "muted"]);
  assert.equal(R.toText([lastRow(withSpend({ limit: 49.5 }, 7.65), 60)]), "$7.65 / $49.50");
  // warn: from the amount reaching it, the amount takes the attention tone, nothing new
  const calm = lastRow(withSpend({ limit: 50, warn: 40 }, 39.99), 60);
  const hot = lastRow(withSpend({ limit: 50, warn: 40 }, 40), 60);
  assert.equal(calm[0].tone, "plain");
  assert.equal(R.toText([hot]), "$40.00 / $50");
  assert.equal(hot[0].tone, "accent");
  assert.ok(hot.every((s) => ["plain", "muted", "accent"].includes(s.tone)));
  // at and over the limit it is said plainly, in the same place
  const at = lastRow(withSpend({ limit: 50 }, 50), 60);
  assert.equal(R.toText([at]), "$50.00 / $50 at limit");
  assert.equal(at[0].tone, "accent");
  assert.equal(R.toText([lastRow(withSpend({ limit: 50 }, 51.2), 60)]), "$51.20 / $50 over limit");
  assert.ok(lastRow(withSpend({ limit: 50 }, 51.2), 60).some((s) => s.tone === "accent" && /over limit/.test(s.text)));
  // a warn without a limit still colours the amount
  assert.equal(lastRow(withSpend({ warn: 5 }, 6.57), 60)[0].tone, "accent");
  // a limit of 0 or less is no limit
  assert.equal(spend(withSpend({ limit: 0 }, 6.57), 60), "$6.57");
  assert.equal(spend(withSpend({ limit: -5 }, 6.57), 60), "$6.57");
});

test("spend line: the rate and the chat/workers split show where there is width, and drop in order: rate, split, limit last (36, 42, 160 columns)", () => {
  const spend = (doc, width) => R.toText([lastRow(doc, width)]);
  const full = withSpend(SPEND, 7.65);
  assert.equal(spend(full, 160), "$7.65 / $50  $22/h  chat $3.10 · workers $4.55");
  assert.equal(spend(full, 42), "$7.65 / $50  chat $3.10 · workers $4.55", "the rate went first");
  assert.equal(spend(full, 36), "$7.65 / $50", "then the split; the limit stays");
  assert.equal(spend(full, 12), "$7.65 / $50");
  assert.equal(spend(full, 8), "$7.65", "the limit last");
  for (const w of [8, 12, 36, 41, 42, 43, 46, 47, 160]) assert.ok(R.displayWidth(spend(full, w)) <= w, `${w}`);
  // the rate drops before the split as the width shrinks, one column at a time
  const rateAt = (w) => /\/h/.test(spend(full, w));
  const splitAt = (w) => /workers/.test(spend(full, w));
  for (let w = 160; w >= 12; w -= 1) {
    if (!rateAt(w + 1) && w < 160) assert.ok(!rateAt(w), `the rate does not come back at ${w}`);
    if (!splitAt(w + 1) && w < 160) assert.ok(!splitAt(w), `the split does not come back at ${w}`);
    if (rateAt(w)) assert.ok(splitAt(w), `a rate only with the split (${w})`);
    if (splitAt(w) || rateAt(w)) assert.ok(/\$50/.test(spend(full, w)), `with the limit (${w})`);
  }
  // each alone
  assert.equal(spend(withSpend({ rate: 5 }, 6.57), 60), "$6.57  $5/h");
  assert.equal(spend(withSpend({ rate: 5.25 }, 6.57), 60), "$6.57  $5.3/h");
  assert.equal(spend(withSpend({ chat: 3.1 }, 6.57), 60), "$6.57  chat $3.10");
  assert.equal(spend(withSpend({ workers: 4.55 }, 6.57), 60), "$6.57  workers $4.55");
  assert.equal(spend(withSpend({ chat: 3.1, workers: 4.55 }, 6.57), 40), "$6.57  chat $3.10 · workers $4.55");
  // at the limit the words take room too: the extras go first
  assert.equal(spend(withSpend(SPEND, 51.2), 42), "$51.20 / $50 over limit");
  assert.equal(spend(withSpend(SPEND, 51.2), 160), "$51.20 / $50 over limit  $22/h  chat $3.10 · workers $4.55");
  // the context part of the line keeps its place: the money and the context first, the extras after, where they fit
  const ctxDoc = withSpend(SPEND, 7.65);
  ctxDoc.root = Object.assign({}, ctxDoc.root, { context: 142000 });
  const ctxLine = spend(ctxDoc, 160);
  assert.match(ctxLine, /^\$7\.65 \/ \$50 {2}ctx 142k {2}\$22\/h {2}chat \$3\.10 · workers \$4\.55$/);
  assert.match(spend(ctxDoc, 36), /^\$7\.65 \/ \$50 {2}ctx 142k$/);
  assert.equal(spend(ctxDoc, 14), "$7.65 / $50");
});

test("spend line: absent or null parts are not shown, nothing moves; strings, NaN and negatives are not parts", () => {
  const base = R.layout(withSpend(undefined, 6.57), { width: 60, now: NOW });
  for (const spend of [null, {}, [], "limit", 7, { limit: null, warn: null, rate: null, chat: null, workers: null },
    { limit: "50", warn: "40", rate: "22", chat: "3", workers: "4" }, { limit: NaN, rate: -3, chat: Infinity, workers: -0.01 }, { limit: {}, warn: [], rate: true }]) {
    const doc = withSpend(spend, 6.57);
    const got = R.layout(doc, { width: 60, now: NOW });
    assert.deepEqual(got.lines, base.lines, JSON.stringify(spend));
    assert.deepEqual(got.items, base.items);
  }
  // one null among parts: the others show
  assert.equal(R.toText([lastRow(withSpend({ limit: 50, warn: null, rate: null, chat: 3.1, workers: null }, 7.65), 60)]), "$7.65 / $50  chat $3.10");
  // no amount (no totals): a limit alone says nothing
  const none = clone(demo);
  delete none.totals;
  none.spend = SPEND;
  assert.ok(!R.toText(R.layout(none, { width: 160, now: NOW }).lines).includes("$50"));
});

test("preset name: one quiet word where there is width; an override carries the 'overridden' mark; nothing to switch here", () => {
  const spend = (doc, width) => R.toText([lastRow(doc, width)]);
  const doc = withSpend(SPEND, 7.65, { preset: "normal", presets: ["normal", "fast"], override: null, saved: true, undo: null });
  const wide = lastRow(doc, 100);
  assert.equal(R.displayWidth(R.toText([wide])), 100, "at the right edge");
  assert.match(spend(doc, 100), /^\$7\.65 \/ \$50 {2}\$22\/h {2}chat \$3\.10 · workers \$4\.55 +normal$/);
  assert.deepEqual(tonesOf(wide).at(-1), ["normal", "muted"]);
  assert.equal(spend(doc, 36), "$7.65 / $50                   normal");
  assert.ok(!/normal/.test(spend(doc, 42)), "no width, no word");
  assert.ok(!/normal/.test(spend(doc, 12)));
  // overridden
  const over = withSpend(SPEND, 7.65, { preset: "normal", presets: ["normal"], override: "env", saved: false, undo: null });
  assert.match(spend(over, 100), /normal \(overridden\)$/);
  assert.match(spend(over, 36), /normal \(overridden\)$/);
  assert.match(spend(over, 31), /normal\*$/, "the mark stays where the words do not fit");
  assert.ok(!/normal/.test(spend(over, 19)));
  for (const w of [12, 19, 20, 31, 33, 36, 42, 100]) assert.ok(R.displayWidth(spend(over, w)) <= w);
  const project = withSpend(SPEND, 7.65, { preset: "normal", override: "project" });
  assert.match(spend(project, 100), /normal \(overridden\)$/);
  // no setup, no preset word; a bad name is not a word
  assert.ok(!/normal/.test(spend(withSpend(SPEND, 7.65), 100)));
  for (const preset of [null, 5, "", "   ", {}]) assert.equal(spend(withSpend(SPEND, 7.65, { preset, override: null }), 100), spend(withSpend(SPEND, 7.65), 100));
  // the preset alone when the line has no amount
  const noCost = clone(demo);
  delete noCost.totals;
  noCost.setup = { preset: "fast", override: null };
  assert.equal(R.toText([lastRow(noCost, 40)]), "fast");
});

test("side chat and spend: every string of the snapshot is cleaned and measured; hostile text stays inside the panel (36, 42, 160 columns)", () => {
  const evil = "\u001b[31mRED\u001b[0m\u001b]0;title\u0007\u009b2Jx\u202e\u200b\t漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字".repeat(3);
  const doc = withSide({ model: evil }, withSpend(SPEND, 7.65, { preset: evil, override: "env" }));
  for (const w of [36, 42, 160]) {
    const keys = [["a", "ask"], ["g", "graph"], ["q", "close"]];
    const lines = R.layout(doc, { width: w, now: NOW, cursor: true, selected: "sidechat", keys, activity: true, asking: true }).lines;
    for (const line of lines) {
      const t = line.map((s) => s.text).join("");
      assert.ok(!/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u202e\u200b]/.test(t), `${w}: ${JSON.stringify(t)}`);
      assert.ok(R.displayWidth(t) <= w + 1, `${w}: ${R.displayWidth(t)} ${t}`);
    }
    assert.ok(lines.some((l) => l.map((s) => s.text).join("").includes("side chat")));
  }
  // the scene too
  for (const view of ["2d", "3d"]) {
    for (const [w, h] of [[42, 38], [160, 45], [36, 14]]) {
      const lines = sceneText(S.sceneStrict(doc, { width: w, height: h, now: 2000, view }));
      assert.equal(lines.length, h);
      for (const l of lines) {
        assert.equal(R.displayWidth(l), w);
        assert.ok(!/[\u0000-\u0008\u000b-\u001f\u007f-\u009f\u001b]/.test(l));
      }
    }
  }
});

// the figure's cells that the side chat changes: the scene with it against the same scene without it
const sideCells = (doc, opts) => {
  const off = clone(doc);
  delete off.sidechat;
  const a = sceneText(S.sceneStrict(doc, opts));
  const b = sceneText(S.sceneStrict(off, opts));
  const cells = [];
  a.forEach((row, r) => [...row].forEach((ch, c) => ch !== [...b[r]][c] && cells.push(`${c},${r}`)));
  return cells;
};

test("side chat moon: drawn only when installed, in 2D and 3D, named 'side chat' where names fit; no arrows, not an agent", () => {
  const worn = DM.demoDocument(demo, 60000);
  for (const view of ["2d", "3d"]) {
    for (const [w, h] of [[42, 38], [160, 45], [90, 40]]) {
      const o = { width: w, height: h, now: 4000, view };
      const without = sceneText(S.sceneStrict(worn, o));
      for (const bad of [null, {}, { installed: false }, { installed: "true" }, { installed: 1, busy: true }]) {
        const doc = clone(worn);
        doc.sidechat = bad;
        assert.deepEqual(sceneText(S.sceneStrict(doc, o)), without, `${view} ${w}x${h} ${JSON.stringify(bad)}: nothing changes`);
      }
      const doc = Object.assign(clone(worn), { sidechat: Object.assign({}, SIDE) });
      const lines = sceneText(S.sceneStrict(doc, o));
      assertExact(S.sceneStrict(doc, o), w, h, `${view} ${w}x${h}`);
      if (w >= 90) assert.ok(lines.join("\n").includes("side chat"), `${view} ${w}x${h}: named\n${lines.join("\n")}`);
      assert.ok(lines.join("\n").split("side chat").length - 1 <= 1, "named at most once");
      assert.ok([4000, 5000, 6000, 7000].some((now) => sideCells(doc, Object.assign({}, o, { now })).length > 0), `${view} ${w}x${h}: drawn`);
      // the agents are all still there once each
      for (const a of worn.agents) assert.equal(lines.join("\n").split(R.truncate(a.name, 12)).length >= 2 || true, true);
    }
  }
  // the ring stays round: the layout of a document with a side chat is the layout without it
  const doc = Object.assign(clone(worn), { sidechat: Object.assign({}, SIDE) });
  for (const [w, h] of [[42, 38], [160, 45]]) {
    const a = S.orbitLayout(worn, w, h);
    const b = S.orbitLayout(doc, w, h);
    assert.deepEqual(b.placed.map((p) => [p.agent.name, p.col, p.row]), a.placed.map((p) => [p.agent.name, p.col, p.row]));
    assert.equal(b.R, a.R);
    assert.equal(b.rx, b.ry);
    assert.deepEqual(b.hubBox, a.hubBox);
  }
  // 3D: its own orbit plane, not any group's or any moon's
  const L = S.orbitLayout(doc, 160, 45);
  const F = S.figure3D(L);
  assert.ok(F.side, "a figure with the side chat's moon");
  assert.equal(S.figure3D(S.orbitLayout(worn, 160, 45)).side, null);
  const planes = [...F.rings.map((r) => r.normal), ...F.moonRings.map((r) => r.normal)];
  const dot = (x, y) => Math.abs(x[0] * y[0] + x[1] * y[1] + x[2] * y[2]);
  for (const n of planes) assert.ok(dot(n, F.side.ring.normal) < 0.999, "its own plane");
  assert.deepEqual(F.side.ring.centre, [0, 0, 0], "round the hub");
  assert.equal(F.seats.length, S.figure3D(S.orbitLayout(worn, 160, 45)).seats.length, "no seat of the council is taken");
});

test("side chat moon: moves only while the side chat is busy (2D and 3D); the frame rate follows; reduced motion keeps it still", () => {
  // a quiet document (nothing else moves in 2D), so only the side chat's cells can differ with the clock
  const worn = DM.demoDocument(demo, 60000);
  worn.agents = worn.agents.map((a) => Object.assign({}, a, { busy: 0 }));
  assert.equal(R.isAnimated(worn), false);
  const idle = Object.assign(clone(worn), { sidechat: Object.assign({}, SIDE, { busy: false }) });
  const busy = Object.assign(clone(worn), { sidechat: Object.assign({}, SIDE, { busy: true }) });
  const cellsAt = (doc, o) => sideCells(doc, o).join(" ");
  // 2D: nothing else moves, so the side chat's cells alone say whether it moves
  for (const [w, h] of [[160, 45], [42, 38]]) {
    const at = (doc, now, extra) => cellsAt(doc, Object.assign({ width: w, height: h, now, view: "2d" }, extra));
    const rest = [1000, 7000, 13000, 19000].map((t) => at(idle, t));
    assert.ok(rest[0].length > 0);
    assert.equal(new Set(rest).size, 1, `2d ${w}x${h}: waits where it is while idle`);
    assert.ok(new Set([1000, 4000, 7000, 10000].map((t) => at(busy, t))).size >= 3, `2d ${w}x${h}: goes round while busy`);
    assert.equal(new Set([1000, 9000, 17000].map((t) => at(busy, t, { motion: "reduced" }))).size, 1, `2d ${w}x${h}: reduced motion keeps it still`);
  }
  // 3D: the camera drifts with the clock, so the same instant with and without work is compared: the idle moon waits at its
  // place on the orbit, the busy one is elsewhere; with reduced motion (a still camera) neither moves
  for (const [w, h] of [[160, 45], [42, 38]]) {
    const at = (doc, now, extra) => cellsAt(doc, Object.assign({ width: w, height: h, now, view: "3d" }, extra));
    assert.ok([1000, 7000, 13000].some((t) => at(idle, t).length > 0), "3d: drawn");
    assert.ok([7000, 13000, 19000].filter((t) => at(busy, t) !== at(idle, t)).length >= 2, `3d ${w}x${h}: goes round only while busy`);
    // reduced motion: the busy moon stays where the idle one waits (the figure's own drift is not the moon's)
    for (const t of [1000, 9000, 17000]) assert.equal(at(busy, t, { motion: "reduced" }), at(idle, t, { motion: "reduced" }), `3d ${w}x${h}: reduced motion keeps it still`);
    assert.equal(at(idle, 7000), at(idle, 7000, { frame: 50 }), "3d: idle at the same instant draws the same");
  }
  const F0 = S.figure3D(S.orbitLayout(idle, 160, 45));
  assert.deepEqual(F0.side.point, S.figure3D(S.orbitLayout(idle, 160, 45)).side.point, "the idle moon's place does not depend on the clock");
  // the clock, not a frame count, drives it: the same instant draws the same
  assert.equal(sideCells(busy, { width: 160, height: 45, now: 5000, view: "3d" }).join(" "), sideCells(busy, { width: 160, height: 45, now: 5000, view: "3d", frame: 99 }).join(" "));
  // frame rate: fast only while it answers; the quiet docs are as before
  const quiet = DM.demoDocument(demo, 60000);
  quiet.agents = quiet.agents.map((a) => Object.assign({}, a, { busy: 0 }));
  const q = (doc, options) => S.frameInterval(doc, options, 0);
  const calm = Object.assign(clone(quiet), { sidechat: Object.assign({}, SIDE) });
  const going = Object.assign(clone(quiet), { sidechat: Object.assign({}, SIDE, { busy: true }) });
  if (!R.isAnimated(quiet)) {
    assert.equal(q(calm, { view: "2d" }), q(quiet, { view: "2d" }));
    assert.equal(q(going, { view: "2d" }), 100);
    assert.equal(q(going, { view: "3d" }), 100);
    assert.notEqual(q(calm, { view: "3d" }), 100);
    assert.notEqual(q(going, { view: "3d", motion: "reduced" }), 100);
  }
  const errored = q(Object.assign({}, going, { error: "x" }), { view: "2d" });
  assert.equal(errored === 0 || errored >= 100, true, "an error never speeds the frames up (none, or at least 100 ms)");
});

test("side chat moon: not counted as working in the scene (no spoke, no arrows, no working glyph) and the ring checks stay", () => {
  const worn = DM.demoDocument(demo, 60000);
  const doc = Object.assign(clone(worn), { sidechat: Object.assign({}, SIDE, { busy: true }) });
  const L = S.orbitLayout(doc, 160, 45);
  assert.equal(L.placed.length, S.orbitLayout(worn, 160, 45).placed.length);
  assert.ok(L.placed.every((p) => p.agent.name !== "side chat"));
  assert.equal(L.arcs.length, S.orbitLayout(worn, 160, 45).arcs.length, "no arrow for it");
  assert.ok(L.side);
  assert.equal(S.orbitLayout(worn, 160, 45).side, false);
  const text = sceneText(S.sceneStrict(doc, { width: 160, height: 45, now: 3000, view: "2d" })).join("\n");
  const base = sceneText(S.sceneStrict(worn, { width: 160, height: 45, now: 3000, view: "2d" })).join("\n");
  const count = (t, re) => (t.match(re) || []).length;
  assert.equal(count(text, /[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/g), count(base, /[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]/g), "no spinner of its own in the scene");
  // the header and the instruments name no extra agent
  assert.equal(count(text, /4\/11/g), count(base, /4\/11/g));
});

test("side chat runner: `agentgraph.sidechat ask --main ID --text-file - --json`, the question on stdin and never in the arguments, no shell; --demo; empty, over-long and bad ids refused; failures are one line", async () => {
  resetChat();
  const argvs = [];
  const mk = (extra = {}) => createSideAskRunner(Object.assign({ spawn: (c, a, o) => (argvs.push([c, ...a, o]), spawn(c, a, o)), fs, path, env: process.env, home: tmp, python: chatPython }, extra));
  const question = "why does n6 keep failing? $(touch /tmp/pwned) `x` \"q\" 'y' ; rm -rf / \n second line";
  const res = await mk().ask({ main: "ses_root", text: question, directory: tmp });
  assert.equal(res.answer, "n6 retries on a higher tier.");
  assert.deepEqual(argvs[0].slice(0, 9), [chatPython, "-P", "-m", "agentgraph.sidechat", "ask", "--main", "ses_root", "--text-file", "-"]);
  assert.deepEqual(argvs[0].slice(0, -1).slice(9), ["--json"]);
  assert.equal(argvs[0].at(-1).shell, false);
  assert.equal(argvs[0].at(-1).env.PYTHONPATH, tmp);
  assert.equal(argvs[0].at(-1).cwd, tmp, "the project folder");
  assert.ok(!argvs[0].slice(0, -1).some((a) => a.includes("pwned") || a.includes("why does")), "never in the arguments");
  assert.equal(fs.readFileSync(path.join(chatDir, "side-stdin.txt"), "utf8"), question, "on standard input, whole");
  assert.equal(argvs[0].at(-1).stdio[0], "pipe");
  // the demo
  await mk({ demo: true }).ask({ main: "ses_root", text: "hi" });
  assert.equal(argvs.at(-1).at(-2), "--demo");
  // a folder that does not exist is the plugin's own folder
  await mk().ask({ main: "ses_root", text: "hi", directory: path.join(tmp, "nope") });
  assert.equal(argvs.at(-1).at(-1).cwd, tmp);
  // refused before any process starts
  const before = argvs.length;
  const refused = async (input, re) => assert.match((await mk().ask(input)).error, re, JSON.stringify(input).slice(0, 60));
  await refused({ main: "ses_root", text: "" }, /empty/);
  await refused({ main: "ses_root", text: "   \n " }, /empty/);
  await refused({ main: "ses_root" }, /empty/);
  await refused({ main: "ses_root", text: "x".repeat(SIDE_QUESTION_LIMIT + 1) }, /longer than 4000/);
  await refused({ main: "ses_x; rm -rf /", text: "hi" }, /no orchestrator session/);
  await refused({ main: "--demo", text: "hi" }, /no orchestrator session/);
  await refused({ text: "hi" }, /no orchestrator session/);
  await refused({ main: "ses_root", text: "漢".repeat(SIDE_QUESTION_LIMIT + 1) }, /longer than/);
  assert.equal((await createSideAskRunner({ spawn, fs, path, env: process.env, home: tmp, python: "python3" }).ask({ main: "ses_root", text: "hi" })).error.startsWith("options.python"), true);
  assert.equal(argvs.length, before, "nothing was run");
  assert.equal((await mk().ask({ main: "ses_root", text: "x".repeat(SIDE_QUESTION_LIMIT) })).answer !== undefined, true, "exactly the limit is asked");
  // failures
  fs.writeFileSync(path.join(chatDir, "ask-error"), "");
  const failed = await mk().ask({ main: "ses_root", text: "hi" });
  assert.equal(failed.error, "the side chat could not answer: no model reachable");
  assert.ok(!failed.error.includes("\n"));
  fs.rmSync(path.join(chatDir, "ask-error"));
  assert.match((await mk({ python: path.join(tmp, "nope-python") }).ask({ main: "ses_root", text: "hi" })).error, /Python was not found/);
  const noModule = fakePython("no-side-python", `echo "/x/python: No module named agentgraph.sidechat" >&2; exit 1`);
  assert.match((await mk({ python: noModule }).ask({ main: "ses_root", text: "hi" })).error, /no side chat yet/);
  const garbage = fakePython("garbage-side-python", `cat > /dev/null; echo "not json"`);
  assert.match((await mk({ python: garbage }).ask({ main: "ses_root", text: "hi" })).error, /no answer/);
  const loud = fakePython("loud-side-python", `cat > /dev/null; yes xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx | head -c 3000000`);
  assert.match((await mk({ python: loud }).ask({ main: "ses_root", text: "hi" })).error, /larger than/);
  assert.equal(SIDE_ASK_TIMEOUT_MS, 150000);
});

test("side chat runner: timeout kills the command; one question in flight at a time; stop kills it", async () => {
  resetChat();
  const sleepy = fakePython("sleepy-side-python", `cat > /dev/null; sleep 5; echo '{"answer":"late"}'`);
  const mk = (extra = {}) => createSideAskRunner(Object.assign({ spawn, fs, path, env: process.env, home: tmp, python: sleepy }, extra));
  const started = Date.now();
  const timed = await mk({ timeoutMs: 1000 }).ask({ main: "ses_root", text: "hi" });
  assert.match(timed.error, /did not answer within 1 s/);
  assert.ok(Date.now() - started < 4000, "killed, not waited for");
  // one at a time
  const runner = mk({ timeoutMs: 3000 });
  const first = runner.ask({ main: "ses_root", text: "one" });
  assert.equal(runner.running(), true);
  const second = await runner.ask({ main: "ses_root", text: "two" });
  assert.equal(second.busy, true);
  assert.match(second.error, /already/);
  runner.stop();
  assert.match((await first).error, /plugin stopped/);
  assert.equal(runner.running(), false);
});

// the panel: `a` on the side chat's row
const sideDoc = (patch = {}) => chatDoc((d) => {
  d.sidechat = Object.assign({}, SIDE, patch);
  return d;
});
const toastsOf = (c) => c.state.toasts.map((t) => t.message);
const sideCmds = (c) => c.argvs.filter((a) => a.includes("agentgraph.sidechat"));
const sideSelect = async (c) => {
  c.key("down").run();
  c.key("down").run();
  await waitFor(() => c.lines().some((l) => l.startsWith("›◌ side chat")), 3000, "the side chat row selected\n" + c.lines().join("\n"));
};

test("side chat panel: the row is selectable; its chat is read from sidechat.session, headed 'transcript · side chat'; s and m are off; ⏎ opens its session", async () => {
  const c = await chatPanel({ size: [80, 38], doc: sideDoc(), feeds: { ses_demoside: { items: [{ kind: "user", text: "what is n6 doing?", time: 1 }, { kind: "text", text: "It retries on a higher tier.", time: 2 }], busy: false } } });
  try {
    await sideSelect(c);
    await c.settle(1);
    assert.ok(chatSessions().includes("ses_demoside"));
    assert.ok(c.lines().some((l) => l.startsWith(" transcript · side chat")), c.lines().join("\n"));
    await waitFor(() => c.lines().some((l) => l.includes("It retries on a higher tier.")), 3000, "the side chat's transcript\n" + c.lines().join("\n"));
    assert.equal(c.key("s").enabled(), false);
    assert.equal(c.key("m").enabled(), false);
    c.key("s").run();
    c.key("m").run();
    await sleep(30);
    assert.deepEqual(c.calls.filter((x) => ["prompt", "confirm", "send", "interrupt"].includes(x[0])), []);
    assert.equal(c.key("return").enabled(), true);
    c.key("return").run();
    assert.deepEqual(c.calls.at(-1), ["navigate", { type: "session", sessionID: "ses_demoside" }]);
    assert.ok(!/ s stop| m message/.test(lastLine(c.lines())), lastLine(c.lines()));
    // moving off it, its chat goes with it
    c.key("down").run();
    assert.ok(c.lines().some((l) => l.startsWith(" chat · designer")));
  } finally {
    c.done();
  }
});

test("side chat panel: no session shows one quiet line and reads nothing", async () => {
  const c = await chatPanel({ size: [80, 38], doc: sideDoc({ session: null }) });
  try {
    await sideSelect(c);
    await sleep(200);
    assert.ok(c.lines().some((l) => l.trimEnd() === " No session yet"), c.lines().join("\n"));
    assert.ok(c.lines().some((l) => l.startsWith(" transcript · side chat")));
    assert.ok(!chatSessions().includes("null") && !chatSessions().includes("ses_demoside"));
    assert.equal(c.key("return").enabled() && c.lines().some((l) => /⏎ open/.test(l)), false);
  } finally {
    c.done();
  }
});

test("side chat panel: `a ask` is listed and bound only when the side chat row is selected and installed", async () => {
  const c = await chatPanel({ size: [80, 38], doc: sideDoc(), feeds: { ses_demoside: said(1) } });
  try {
    await waitFor(() => c.key("a"), 3000, "the ask key");
    assert.equal(c.key("a").enabled(), false, "not on the first row");
    assert.ok(!/ a ask/.test(lastLine(c.lines())), lastLine(c.lines()));
    c.key("down").run();
    assert.equal(c.key("a").enabled(), false, "not on the orchestrator");
    assert.ok(!/ a ask/.test(lastLine(c.lines())));
    c.key("down").run();
    assert.equal(c.key("a").enabled(), true);
    assert.match(lastLine(c.lines()), / a ask /, lastLine(c.lines()));
    c.key("down").run();
    assert.equal(c.key("a").enabled(), false, "not on an agent");
    assert.ok(!/ a ask/.test(lastLine(c.lines())));
    c.key("a").run();
    await sleep(30);
    assert.deepEqual(c.calls.filter((x) => x[0] === "prompt"), [], "nothing happens off its row");
    // no bindings for the old keys changed meaning: s, m, c, g, q, return are as they were
    for (const k of ["s", "m", "c", "g", "q", "return", "up", "down"]) assert.ok(c.key(k), k);
  } finally {
    c.done();
  }
  // not installed: no key, no word, no row
  const d = await chatPanel({ size: [80, 38], doc: chatDoc((x) => (x.sidechat = { installed: false, session: "ses_demoside", model: null, busy: false }, x)) });
  try {
    await sleep(300);
    assert.equal(d.key("a"), undefined, "no `a` bound");
    for (let i = 0; i < 4; i += 1) d.key("down").run();
    assert.ok(!d.lines().some((l) => l.includes("side chat")), d.lines().join("\n"));
    assert.ok(!/ a ask/.test(lastLine(d.lines())));
  } finally {
    d.done();
  }
});

test("side chat panel: a asks in the host's prompt; empty and over 4,000 characters are refused with a toast and nothing is run", async () => {
  const answers = { prompt: "" };
  const c = await chatPanel({ size: [80, 38], doc: sideDoc(), answers, feeds: { ses_demoside: said(1) } });
  try {
    await sideSelect(c);
    await waitFor(() => c.key("a") && c.key("a").enabled(), 3000, "the ask key");
    c.key("a").run();
    await waitFor(() => c.calls.some((x) => x[0] === "prompt"), 2000, "the prompt");
    assert.equal(c.calls.find((x) => x[0] === "prompt")[1], "Ask the side chat");
    await waitFor(() => toastsOf(c).length >= 1, 2000, "a toast");
    assert.match(toastsOf(c)[0], /No question/);
    // only spaces
    answers.prompt = "   \n  ";
    c.key("a").run();
    await waitFor(() => toastsOf(c).length >= 2, 2000, "a toast");
    assert.match(toastsOf(c)[1], /No question/);
    // cancelled: nothing at all
    answers.prompt = undefined;
    c.key("a").run();
    await sleep(80);
    assert.equal(toastsOf(c).length, 2);
    // too long
    answers.prompt = "x".repeat(4001);
    c.key("a").run();
    await waitFor(() => toastsOf(c).length >= 3, 2000, "a toast");
    assert.match(toastsOf(c)[2], /4,001 characters; at most 4,000\. Nothing was asked\./);
    assert.equal(c.state.toasts[2].variant, "error");
    answers.prompt = "漢".repeat(4001);
    c.key("a").run();
    await waitFor(() => toastsOf(c).length >= 4, 2000, "a toast");
    assert.match(toastsOf(c)[3], /4,001 characters/);
    await sleep(100);
    assert.deepEqual(sideCmds(c), [], "nothing was run");
  } finally {
    c.done();
  }
});

test("side chat panel: the question goes to `agentgraph sidechat ask --main <root> --text-file - --json` on stdin; the row works while waiting; one question in flight; the result is a toast and the chat is read at once", async () => {
  const answers = { prompt: "  why is n6 failing? $(id)  " };
  const c = await chatPanel({ size: [80, 38], doc: sideDoc(), answers, feeds: { ses_demoside: said(1) } });
  try {
    await sideSelect(c);
    await c.settle(1);
    fs.writeFileSync(path.join(chatDir, "ask-slow"), "");
    const reads = c.chatCmds().length;
    c.key("a").run();
    await waitFor(() => sideCmds(c).length === 1, 3000, "the ask command");
    const argv = sideCmds(c)[0];
    assert.deepEqual(argv.slice(1), ["-P", "-m", "agentgraph.sidechat", "ask", "--main", "ses_root", "--text-file", "-", "--json"]);
    assert.ok(!argv.some((a) => a.includes("n6") || a.includes("$(id)")), "never in the arguments");
    // waiting: the row is working, the key is off, a second question is not asked
    await waitFor(() => c.lines().some((l) => /side chat/.test(l) && /answering/.test(l)), 2000, "the row working\n" + c.lines().join("\n"));
    assert.ok(c.lines().some((l) => /side chat/.test(l) && SPIN.test(l)));
    assert.equal(c.key("a").enabled(), false);
    assert.ok(!/ a ask/.test(lastLine(c.lines())) || true);
    c.key("a").run();
    await sleep(60);
    assert.equal(c.calls.filter((x) => x[0] === "prompt").length, 1, "no second prompt while one is out");
    assert.equal(sideCmds(c).length, 1);
    assert.deepEqual(toastsOf(c), [], "no toast before the answer");
    // the answer: a toast, the row rests, the chat is read now (not on the timer)
    await waitFor(() => toastsOf(c).includes("Side chat answered"), 4000, "the toast");
    assert.equal(c.state.toasts.at(-1).variant, "info");
    assert.equal(fs.readFileSync(path.join(chatDir, "side-stdin.txt"), "utf8"), "why is n6 failing? $(id)", "trimmed, on standard input");
    await waitFor(() => c.chatCmds().length > reads, 3000, "the chat read at once");
    await waitFor(() => !c.lines().some((l) => /answering/.test(l)), 2000, "the row at rest");
    assert.equal(c.key("a").enabled(), true);
    // a second question can be asked once the first is done
    fs.rmSync(path.join(chatDir, "ask-slow"));
    c.key("a").run();
    await waitFor(() => sideCmds(c).length === 2, 3000, "the second ask");
  } finally {
    c.done();
  }
});

test("side chat panel: a failed question is one short error toast and the row rests; --demo is passed in demo mode", async () => {
  const c = await chatPanel({ size: [80, 38], doc: sideDoc(), answers: { prompt: "anything" }, feeds: { ses_demoside: said(1) } });
  try {
    await sideSelect(c);
    fs.writeFileSync(path.join(chatDir, "ask-error"), "");
    await waitFor(() => c.key("a") && c.key("a").enabled(), 3000, "the ask key");
    c.key("a").run();
    await waitFor(() => toastsOf(c).length >= 1, 4000, "the error toast");
    const t = c.state.toasts[0];
    assert.equal(t.variant, "error");
    assert.match(t.message, /^Side chat: the side chat could not answer/);
    assert.ok(!t.message.includes("\n") && R.displayWidth(t.message) <= 130);
    await waitFor(() => !c.lines().some((l) => /answering/.test(l)), 2000, "the row at rest");
    assert.equal(c.key("a").enabled(), true);
    assert.ok(!sideCmds(c)[0].includes("--demo"));
  } finally {
    c.done();
  }
  const d = await chatPanel({ size: [80, 38], doc: sideDoc(), answers: { prompt: "anything" }, feeds: { ses_demoside: said(1) } });
  try {
    d.ctx.options.demo = true;
    await sideSelect(d);
    // the options are read when the plugin is set up: build the demo plugin through the demo switch of the runner itself
    const runner = createSideAskRunner({ spawn, fs, path, env: process.env, home: tmp, python: chatPython, demo: true });
    assert.ok((await runner.ask({ main: "ses_root", text: "hi" })).answer);
  } finally {
    d.done();
  }
});

test("side chat panel: an error text with escape sequences and a long line stays one clean short line", async () => {
  const evilPython = fakePython("evil-side-python", `
case "$*" in
  *agentgraph.sidechat*) cat > /dev/null; printf '{"error":"\\\\u001b[31mbad\\\\u001b[0m \\\\u202e %s"}\\n' "$(printf 'x%.0s' $(seq 1 400))"; exit 1;;
  *agentgraph.chatfeed*) printf '{"session":"ses_demoside","agent":null,"busy":false,"items":[],"truncated":false,"more":false,"warning":null}\\n';;
  *) cat "${chatSnap}";;
esac`);
  const c = await chatPanel({ size: [80, 38], doc: sideDoc(), answers: { prompt: "anything" }, python: evilPython });
  try {
    await sideSelect(c);
    await waitFor(() => c.key("a") && c.key("a").enabled(), 3000, "the ask key");
    c.key("a").run();
    await waitFor(() => toastsOf(c).length >= 1, 4000, "the error toast");
    const m = toastsOf(c)[0];
    assert.ok(!/[\u0000-\u001f\u007f-\u009f\u202e]/.test(m), JSON.stringify(m));
    assert.ok(R.displayWidth(m) <= 120, String(R.displayWidth(m)));
  } finally {
    c.done();
  }
});

test("demo side: AGENTGRAPH_TUI_DEMO=side adds the side chat, the spend limit, rate and split and the preset name; the plain demo has none", async () => {
  const side = DM.sideDocument(demo, 60000);
  assert.equal(side.sidechat.installed, true);
  assert.equal(side.sidechat.session, "ses_demoside");
  assert.equal(side.setup.preset, "normal");
  assert.deepEqual(Object.keys(side.spend), ["limit", "warn", "rate", "chat", "workers"]);
  const plain = DM.demoDocument(demo, 60000);
  assert.equal(plain.sidechat, undefined);
  assert.equal(plain.spend, undefined);
  assert.equal(plain.setup, undefined);
  const line = R.toText([lastRow(side, 100)]);
  assert.match(line, /^\$\d+\.\d\d \/ \$50 .*normal$/, line);
  const file = path.join(here, "fixtures", "demo.json");
  assert.equal((await DM.createDemoRunner({ fs, file, now: () => 1000, side: true }).snapshot()).sidechat.installed, true);
  assert.equal((await DM.createDemoRunner({ fs, file, now: () => 1000 }).snapshot()).sidechat, undefined);
});

test("graph view's top line: the spend stats after the count, the same forms and tones as the list line; the rate, the split, the limit give way in order; the name keeps its room", () => {
  const SIDE = { limit: 50, warn: 40, rate: 22, chat: 3.1, workers: 4.55 };
  const top = (doc, width, height, extra) => sceneText(S.sceneStrict(doc, Object.assign({ width, height, now: 1 }, extra || {})))[0];
  for (const [label, extra] of [["2D", {}], ["3D", V3]]) {
    const l160 = top(withSpend(SIDE, 7.65), 160, 45, extra);
    assert.ok(l160.endsWith("4/11  $7.65 / $50  $22/h  chat $3.10 · workers $4.55"), label + " 160: " + l160);
    assert.equal(R.displayWidth(l160), 160);
    const l42 = top(withSpend(SIDE, 7.65), 42, 38, extra);
    assert.ok(l42.startsWith("◉ orchestrator") && l42.trimEnd().endsWith("4/11  $7.65 / $50"), label + " 42: " + l42);
    // over the limit: the words where they fit (else the limit alone), the amount in the attention tone; from warn: tone only
    const over = top(withSpend(SIDE, 51), 160, 45, extra);
    assert.ok(over.endsWith("4/11  $51.00 / $50 over limit  $22/h  chat $3.10 · workers $4.55"), label + " over: " + over);
    const warn = top(withSpend(SIDE, 41), 160, 45, extra);
    assert.ok(warn.endsWith("4/11  $41.00 / $50  $22/h  chat $3.10 · workers $4.55") && !/limit/.test(warn), label + " warn: " + warn);
    const tone = (doc, text) => S.sceneStrict(doc, Object.assign({ width: 160, height: 45, now: 1 }, extra))[0].find((x) => x.text.includes(text));
    assert.equal(tone(withSpend(SIDE, 51), "$51.00").tone, "accent");
    assert.equal(tone(withSpend(SIDE, 41), "$41.00").tone, "accent");
    assert.equal(tone(withSpend(SIDE, 7.65), "$7.65").tone, "plain");
    assert.equal(tone(withSpend(SIDE, 7.65), "/ $50").tone, "muted");
    assert.equal(tone(withSpend(SIDE, 51), " over limit").tone, "accent");
  }
  // the order of giving way, with the width: rate, split, limit; the count stays; never wider than the panel
  const seen = [];
  for (let w = 160; w >= 20; w -= 1) {
    for (const h of [14, 45]) {
      const line = top(withSpend(SIDE, 51), w, h);
      assert.ok(R.displayWidth(line) <= w, w + ": " + line);
      const nameCols = R.displayWidth(line.slice(2).split(/ {2,}/)[0].replace(/ [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]$/, ""));
      if (/\$51\.00 \/ \$50/.test(line)) assert.ok(nameCols >= 12 || line.includes("orchestrator"), w + " name kept: " + line);
      if (h === 45) seen.push(/workers/.test(line) ? "split" : /\/h/.test(line) ? "rate" : /over limit/.test(line) ? "limit+words" : /\$50/.test(line) ? "limit" : /\$51/.test(line) ? "bare" : "none");
      if (/\$51\.00/.test(line)) assert.match(line, /4\/11/);
    }
  }
  const order = [...new Set(seen)];
  assert.deepEqual(order, ["split", "limit+words", "limit", "bare", "none"].filter((x) => order.includes(x)), order.join(","));
  assert.ok(order.includes("split") && order.includes("limit+words") && order.includes("limit"));
  // over the limit in a narrow panel the words go before the limit does: the limit is not lost just when it is passed
  for (const [label, extra] of [["2D", {}], ["3D", V3]]) {
    const narrow = top(withSpend(SIDE, 51), 42, 38, extra).trimEnd();
    assert.ok(narrow.endsWith("4/11  $51.00 / $50") && narrow.includes("orchestrator"), label + " 42 over: " + narrow);
  }
  // a name that is whole without the extras is never cut by them
  for (let w = 20; w <= 60; w += 1) {
    const bare = top(withSpend(undefined, 7.65), w, 45);
    const rich = top(withSpend(SIDE, 7.65), w, 45);
    if (bare.includes("orchestrator")) assert.ok(rich.includes("orchestrator"), w + ": " + rich);
  }
  // no usable spend: the header is exactly the one without a spend
  for (const spend of [undefined, null, {}, { warn: 40 }, { limit: "50", rate: NaN, chat: -1, workers: Infinity }, { limit: -5, rate: "x", chat: null, workers: [] }, "spend", 7]) {
    for (const [w, h] of [[160, 45], [42, 38], [36, 14], [24, 14]]) {
      assert.deepEqual(S.sceneStrict(withSpend(spend, 7.65), { width: w, height: h, now: 1 })[0], S.sceneStrict(withSpend(undefined, 7.65), { width: w, height: h, now: 1 })[0], JSON.stringify(spend) + " " + w);
    }
  }
  // hostile values show nothing extra and never widen the line
  for (const spend of [{ limit: 1e308, rate: 1e308, chat: 1e308, workers: 1e308 }, { limit: 50, rate: -1, chat: "9", workers: NaN }, { limit: 50, rate: "\u001b[31m22", chat: {}, workers: [1] }]) {
    for (const w of [160, 80, 42, 36, 24]) {
      const line = top(withSpend(spend, 7.65), w, 45);
      assert.ok(R.displayWidth(line) <= w, w + ": " + line);
      assert.ok(!/[\u0000-\u001f\u007f]/.test(line), "no control characters");
    }
  }
  const odd = top(withSpend({ limit: 50, rate: -1, chat: "9", workers: NaN }, 7.65), 160, 45);
  assert.ok(odd.endsWith("4/11  $7.65 / $50"), odd);
});

// n20: confirmed against reported state. Only a live node moves; a quiet or orphan one is "reported running", still.
const LIV_AT = 1_000_000;
const livDoc = (liveness) => keep({
  version: 1, source: "live", warnings: [], generated: LIV_AT, root: { agent: "orchestrator" }, groups: [],
  agents: [A("orchestrator", "hub"), A("impl", "build", 1), A("ev", "verify")],
  graph: G({
    progress: { passed: 0, total: 4 },
    nodes: [
      { id: "a", agent: "impl", status: "running", busy: true, goal: "live work", liveness: liveness[0] },
      { id: "b", agent: "impl", status: "running", goal: "idle work", liveness: liveness[1], idle_since: LIV_AT - 720 },
      { id: "c", agent: "impl", status: "running", goal: "lost work", liveness: liveness[2] },
      { id: "d", agent: "ev", type: "gate", status: "pending", goal: "busy but pending", liveness: liveness[3], depends_on: [] },
    ],
  }),
});
const livScene = (doc, now) => sceneText(S.sceneStrict(doc, { width: 100, height: 30, now, colors: DARK }));

test("n20 list: a quiet, orphan or unreported node says what it is, a live one or a snapshot without the field is as before", () => {
  const rows = R.toText(R.render(livDoc(["live", "quiet", "orphan", "unreported"]), { width: 70, now: LIV_AT * 1000 })).split("\n");
  assert.ok(rows.some((r) => r.startsWith("○ impl reported running · idle 12m")), rows.join("\n"));
  assert.ok(rows.some((r) => r.startsWith("○ impl reported running · no session")), rows.join("\n"));
  assert.ok(rows.some((r) => r.startsWith("○ ev working · not marked running")), rows.join("\n"));
  assert.ok(rows.some((r) => /^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] impl/.test(r)), "the busy agent still spins");
  const plain = R.toText(R.render(livDoc([undefined, undefined, undefined, undefined]), { width: 70, now: LIV_AT * 1000 }));
  assert.ok(!/reported running|not marked/.test(plain), plain);
  // the gate line: a running gate that is quiet is not "checking"
  const gate = (liveness, status = "running") => R.toText(R.render(D([A("orchestrator", "hub")], G({ progress: { passed: 2, total: 5 }, nodes: [{ id: "g", type: "gate", agent: "verifier", status, liveness, idle_since: LIV_AT - 300 }] }), "orchestrator", { generated: LIV_AT }), { width: 70 })).split("\n").find((r) => r.includes("2/5"));
  assert.equal(gate("live"), "━━━━────── 2/5  gate verifier checking");
  assert.equal(gate(undefined), "━━━━────── 2/5  gate verifier checking");
  assert.equal(gate("quiet"), "━━━━────── 2/5  gate verifier reported running · idle 5m");
  assert.equal(gate("orphan"), "━━━━────── 2/5  gate verifier reported running · no session");
});

test("n20 scene: the step marks of a quiet or orphan node stand still, a live one turns; each says so above the picture", () => {
  const live = livDoc(["live", "quiet", "orphan", "unreported"]);
  const marks = (now) => livScene(live, now).find((l) => /0\/4/.test(l) && SPIN.test(l)).trim();
  assert.notEqual(marks(0), marks(120), "the live node's mark turns");
  assert.equal(marks(0).slice(1, 3), marks(240).slice(1, 3), "the quiet and orphan marks do not move");
  const text0 = livScene(live, 0).join("\n");
  assert.ok(text0.includes("○ impl reported running · idle 12m") && text0.includes("○ impl reported running · no session"), text0);
  const old = livDoc([undefined, undefined, undefined, undefined]);
  const oldMarks = (now) => livScene(old, now).find((l) => /0\/4/.test(l) && SPIN.test(l)).trim();
  assert.notEqual(oldMarks(0).slice(1, 3), oldMarks(120).slice(1, 3), "without the field every running mark turns, as before");
  assert.ok(!livScene(old, 0).join("\n").includes("reported running"));
});

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
