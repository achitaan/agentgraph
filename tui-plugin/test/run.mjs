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

test("demo document at width 37", () => {
  assert.deepEqual(text(keep(demo), 37), [
    pad("◉ orchestrator ⠋", "4/11"),
    "━━━━━━━━━━━━━────────────────────────",
    "$6.57  12.0k↑ 70.9k↓",
    "",
    "! impl-claude stuck ↻5",
    "! reviewer stuck",
    "↩ impl-open… sent back ↻3 by reviewer",
    "",
    pad("⠋ impl-claude ↻5", "$1.35"),
    "  Apply the limiter in the middlewar…",
    pad("⠙ designer", "$0.09"),
    "  429 error page and retry-after mes…",
    "",
    "○ council fable astra carbon",
    "○ oracle oracle-lite",
    "○ impl-openai impl-hard",
    "○ reviewer",
    "○ explorer researcher",
  ]);
});

test("demo document at width 24", () => {
  assert.deepEqual(text(demo, 24), [
    "◉ orchestrator ⠋    4/11",
    "━━━━━━━━━───────────────",
    "$6.57  12.0k↑ 70.9k↓",
    "",
    "! impl-claude stuck ↻5",
    "! reviewer stuck",
    "↩ im… sent back ↻3 by r…",
    "",
    "⠋ impl-claude ↻5   $1.35",
    "  Apply the limiter in…",
    "⠙ designer         $0.09",
    "  429 error page and re…",
    "",
    "○ council fable astra",
    "  carbon",
    "○ oracle oracle-lite",
    "○ impl-openai impl-hard",
    "○ reviewer",
    "○ explorer researcher",
  ]);
});

test("demo tones", () => {
  const lines = R.render(demo, { width: 37, frame: 0 });
  const tone = (row, t) => (lines[row].find((s) => s.text === t) || {}).tone;
  assert.equal(tone(0, "◉"), "accent");
  assert.equal(tone(0, "⠋"), "accent");
  assert.equal(tone(0, "orchestrator"), "plain");
  assert.equal(tone(0, "4"), "plain");
  assert.equal(tone(0, "/11"), "muted");
  assert.equal(tone(1, "━".repeat(13)), "accent");
  assert.equal(tone(1, "─".repeat(24)), "muted");
  assert.equal(tone(2, "$6.57"), "plain");
  assert.equal(tone(2, "12.0"), "plain");
  assert.equal(tone(2, "k"), "muted");
  assert.equal(tone(2, "↑"), "muted");
  assert.equal(tone(4, "! impl-claude stuck ↻5"), "danger");
  assert.equal(tone(5, "! reviewer stuck"), "danger");
  assert.equal(tone(6, "↩ impl-open… sent back ↻3 by reviewer"), "accent");
  assert.equal(tone(8, "⠋"), "accent");
  assert.equal(tone(8, "impl-claude"), "plain");
  assert.equal(tone(8, " ↻5"), "accent");
  assert.equal(tone(8, "$1.35"), "muted");
  assert.equal(tone(9, "Apply the limiter in the middlewar…"), "muted");
  assert.equal(tone(13, "○ council fable astra carbon"), "muted");
  for (const line of lines) for (const s of line) assert.ok(R.TONES.includes(s.tone), s.tone);
  assert.ok(!R.TONES.includes("success") && !R.TONES.includes("green"));
});

test("spinners: hub and each working agent, offset by index, advancing with frame", () => {
  assert.equal(text(demo, 37, 1)[0], "◉ orchestrator ⠙                 4/11");
  assert.equal(text(demo, 37, 1)[8].slice(0, 2), "⠙ ");
  assert.equal(text(demo, 37, 1)[10].slice(0, 2), "⠹ ");
  assert.equal(text(demo, 37, 9)[10].slice(0, 2), "⠋ ");
  assert.equal(text(demo, 37, 10)[0].slice(0, 16), "◉ orchestrator ⠋");
  assert.equal(R.isAnimated(demo), true);
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

test("blocks are omitted when empty; the rest are separated by one blank line", () => {
  const hubOnly = keep(D([A("orchestrator", "hub", 1)], G({ progress: { passed: 3, total: 8 } }), "orchestrator", { totals: T(1.5, 500, 20) }));
  assert.deepEqual(text(hubOnly, 37, 0), [
    pad("◉ orchestrator ⠋", "3/8"),
    "━━━━━━━━━━━━━━───────────────────────",
    "$1.50  500↑ 20↓",
  ]);
  // Run + Idle
  assert.deepEqual(text(keep(D([A("orchestrator", "hub"), A("explorer", "understand")]))), ["◉ orchestrator", "", "○ explorer"]);
  // Run + Working (nothing idle)
  assert.deepEqual(text(keep(D([A("orchestrator", "hub"), A("w", "build", 1, "", 2)]))), [
    "◉ orchestrator", "", pad("⠋ w", "$2.00"),
  ]);
  // Run + Needs you (nothing else)
  assert.deepEqual(
    text(keep(D([A("orchestrator", "hub"), A("w", "build")], G({ work_loops: [{ agent: "w", rounds: 1, state: "waiting", active: false }] })))),
    ["◉ orchestrator", "", "? w waiting", "", "○ w"],
  );
  // all four
  assert.deepEqual(
    text(keep(D(
      [A("orchestrator", "hub"), A("w", "build", 1, "", 1), A("v", "verify"), A("x", "build")],
      G({ work_loops: [{ agent: "x", rounds: 1, state: "waiting", active: false }] }),
    ))),
    ["◉ orchestrator", "", "? x waiting", "", pad("⠋ w", "$1.00"), "", "○ x", "○ v"],
  );
});

test("hub is the root's agent, even when another hub agent is listed; a hub-less document uses root", () => {
  const doc = keep(D([A("orchestrator", "hub", 1, "", 3), A("lead", "hub", 0), A("reviewer", "verify")], null, "lead"));
  assert.deepEqual(text(doc), ["◉ lead", "", pad("⠋ orchestrator", "$3.00"), "", "○ reviewer"]);
  const none = keep(D([A("explorer", "understand")], null, "orchestrator", { root: { agent: "orchestrator", busy: true } }));
  assert.deepEqual(text(none), ["◉ orchestrator ⠋", "", "○ explorer"]);
  assert.equal(R.isAnimated(none), true);
});

test("spend line: dollars, tokens, units muted, context only when known", () => {
  const spend = (totals, root = { agent: "orchestrator" }) =>
    text(D([A("orchestrator", "hub")], null, "orchestrator", { totals, root }))[1];
  assert.equal(spend(T(431.4, 2100000, 310000)), "$431.40  2.1M↑ 310k↓");
  assert.equal(spend(T(431.4, 2100000, 310000), { agent: "orchestrator", context: 124000 }), "$431.40  2.1M↑ 310k↓  ctx 124k");
  assert.equal(spend(T(431.4, 2100000, 310000), { agent: "orchestrator", context: null }), "$431.40  2.1M↑ 310k↓");
  assert.equal(spend(T(0, 0, 0)), "$0.00  0↑ 0↓");
  assert.equal(spend(T(0.999, 999, 1000)), "$1.00  999↑ 1.0k↓");
  assert.equal(spend(T(1234.5, 999999, 1000000)), "$1234.50  1.0M↑ 1.0M↓");
  assert.equal(spend(T(5, 12400, 99999)), "$5.00  12.4k↑ 100k↓");
  assert.equal(spend(T(5, 12345678, 999499)), "$5.00  12.3M↑ 999k↓");
  assert.equal(spend(T(5, 12400, 310000), { agent: "orchestrator", context: 950 }), "$5.00  12.4k↑ 310k↓  ctx 950");
  // no totals at all: no spend line
  assert.deepEqual(text(D([A("orchestrator", "hub")], null, "orchestrator", { totals: undefined })), ["◉ orchestrator"]);
  // context never shown without the field
  assert.ok(!text(demo).some((l) => l.includes("ctx")));
  // spans: number plain, unit and arrow muted
  const spans = R.render(D([A("orchestrator", "hub")], null, "orchestrator", { totals: T(1, 12400, 950), root: { agent: "orchestrator", context: 124000 } }))[1];
  assert.deepEqual(spans.map((s) => [s.text, s.tone]), [
    ["$1.00", "plain"], ["  ", "plain"], ["12.4", "plain"], ["k", "muted"], ["↑", "muted"], [" ", "plain"],
    ["950", "plain"], ["↓", "muted"], ["  ", "plain"], ["ctx ", "muted"], ["124", "plain"], ["k", "muted"],
  ]);
});

test("formatTokens and formatDollars at the boundaries", () => {
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
});

test("right alignment at width 37: run count and agent cost end at the right edge", () => {
  const lines = text(demo, 37);
  for (const row of [0, 8, 10]) assert.equal(R.displayWidth(lines[row]), 37, lines[row]);
  assert.ok(lines[0].endsWith(" 4/11"));
  assert.ok(lines[8].endsWith("$1.35"));
  assert.equal(R.displayWidth(lines[1]), 37); // the bar spans the full width
  const wide = text(keep(D([A("orchestrator", "hub", 1), A("w", "build", 1, "", 1234.5)], G({ progress: { passed: 10, total: 12 } }))), 37);
  assert.equal(wide[0], "◉ orchestrator ⠋                10/12");
  assert.equal(wide[1], "━".repeat(31) + "─".repeat(6));
  assert.equal(wide[3], pad("⠋ w", "$1234.50"));
});

test("progress bar rounding: never full before done, never empty once started", () => {
  const bar = (passed, total, width = 20) =>
    text(D([A("orchestrator", "hub")], G({ progress: { passed, total } })), width)[1];
  assert.equal(bar(0, 5), "─".repeat(20));
  assert.equal(bar(5, 5), "━".repeat(20));
  assert.equal(bar(199, 200), "━".repeat(19) + "─");
  assert.equal(bar(1, 200), "━" + "─".repeat(19));
});

test("Needs you: each kind, text and tone", () => {
  const agents = [A("orchestrator", "hub"), A("a", "build"), A("b", "build"), A("c", "build"), A("d", "build"), A("e", "build"), A("ev", "verify")];
  const one = (graph) => text(D(agents, G(graph)));
  assert.deepEqual(one({ work_loops: [{ agent: "a", rounds: 4, state: "stuck", active: false }] }).slice(2, 3), ["! a stuck ↻4"]);
  assert.deepEqual(one({ work_loops: [{ agent: "a", rounds: 1, state: "stuck", active: false }] }).slice(2, 3), ["! a stuck"]);
  assert.deepEqual(one({ nodes: [{ id: "n", agent: "b", status: "failed", attempts: 1 }] }).slice(2, 3), ["! b stuck"]);
  assert.deepEqual(one({ nodes: [{ id: "n", agent: "b", status: "blocked", attempts: 3 }] }).slice(2, 3), ["! b stuck ↻3"]);
  assert.deepEqual(one({ nodes: [{ id: "n", agent: "b", status: "running", busy: true, stalled: true, attempts: 1 }] }).slice(2, 3), ["! b stuck"]);
  assert.deepEqual(
    one({ loops: [{ worker: "c", evaluator: "ev", rounds: 3, state: "sent_back", active: true }] }).slice(2, 3),
    ["↩ c sent back ↻3 by ev"],
  );
  assert.deepEqual(
    one({ loops: [{ worker: "c", evaluator: "ev", rounds: 1, state: "sent_back", active: true }] }).slice(2, 3),
    ["↩ c sent back by ev"],
  );
  assert.deepEqual(one({ work_loops: [{ agent: "d", rounds: 2, state: "waiting", active: false }] }).slice(2, 3), ["? d waiting"]);
  // not urgent: working, checking, closed, passed
  const calm = one({
    nodes: [{ id: "n", agent: "e", status: "passed" }, { id: "m", agent: "d", status: "running", busy: true, stalled: false }],
    loops: [{ worker: "c", evaluator: "ev", rounds: 2, state: "working", active: true }, { worker: "a", evaluator: "ev", rounds: 2, state: "closed", active: false }],
    work_loops: [{ agent: "b", rounds: 2, state: "working", active: true }],
  });
  assert.ok(!calm.some((l) => l.startsWith("!") || l.startsWith("↩") || l.startsWith("?")), calm.join("|"));
  // tones
  const toned = R.render(D(agents, G({
    work_loops: [{ agent: "a", rounds: 2, state: "stuck" }, { agent: "d", rounds: 2, state: "waiting" }],
    loops: [{ worker: "c", evaluator: "ev", rounds: 2, state: "sent_back" }],
  })), { width: 37 });
  assert.deepEqual(toned.slice(2, 5).map((l) => [l[0].text, l[0].tone]), [
    ["! a stuck ↻2", "danger"], ["↩ c sent back ↻2 by ev", "accent"], ["? d waiting", "plain"],
  ]);
});

test("Needs you: most urgent first, one line per agent, unlisted agents skipped", () => {
  const agents = [A("orchestrator", "hub"), A("w1", "build"), A("w2", "build"), A("w3", "build"), A("w4", "build"), A("w5", "build"), A("ev", "verify")];
  const doc = keep(D(agents, G({
    nodes: [
      { id: "1", agent: "w2", status: "failed", attempts: 1 },
      { id: "2", agent: "w2", status: "blocked", attempts: 1 }, // same agent again: no second line
      { id: "3", agent: "ghost", status: "failed", attempts: 1 }, // not listed
      { id: "4", agent: "user", status: "blocked", attempts: 1 }, // not listed
    ],
    work_loops: [
      { agent: "w4", rounds: 2, state: "waiting", active: false },
      { agent: "w1", rounds: 6, state: "stuck", active: false },
      { agent: "w2", rounds: 3, state: "stuck", active: false }, // stuck loop outranks its failed nodes
      { agent: "w5", rounds: 2, state: "waiting", active: false },
      { agent: "ghost", rounds: 2, state: "stuck", active: false },
    ],
    loops: [
      { worker: "w3", evaluator: "ev", rounds: 2, state: "sent_back", active: true },
      { worker: "w4", evaluator: "ev", rounds: 2, state: "sent_back", active: true }, // w4 also waits: the sent-back line wins
      { worker: "w5", evaluator: "ghost", rounds: 2, state: "sent_back", active: true }, // evaluator not listed
    ],
  })));
  assert.deepEqual(text(doc).slice(1, 6), [
    "",
    "! w1 stuck ↻6",
    "! w2 stuck ↻3",
    "↩ w3 sent back ↻2 by ev",
    "↩ w4 sent back ↻2 by ev",
  ]);
  assert.deepEqual(text(doc).slice(6, 9), ["? w5 waiting", "", "○ w1 w2 w3 w4 w5"]);
});

test("Needs you: at most 5 lines, then +N more", () => {
  const names = ["a1", "a2", "a3", "a4", "a5", "a6", "a7"];
  const agents = [A("orchestrator", "hub"), ...names.map((n) => A(n, "build"))];
  const six = keep(D(agents, G({ work_loops: names.slice(0, 6).map((n, i) => ({ agent: n, rounds: 2 + i, state: "stuck" })) })));
  assert.deepEqual(text(six).slice(0, 8), [
    "◉ orchestrator", "", "! a1 stuck ↻2", "! a2 stuck ↻3", "! a3 stuck ↻4", "! a4 stuck ↻5", "! a5 stuck ↻6", "+1 more",
  ]);
  assert.equal(R.render(six)[7][0].tone, "muted");
  const exactly = keep(D(agents, G({ work_loops: names.slice(0, 5).map((n) => ({ agent: n, rounds: 2, state: "stuck" })) })));
  assert.ok(!text(exactly).some((l) => l.includes("more")));
  assert.equal(text(exactly).filter((l) => l.startsWith("!")).length, 5);
  const seven = keep(D(agents, G({ work_loops: names.map((n) => ({ agent: n, rounds: 2, state: "stuck" })) })));
  assert.equal(text(seven)[7], "+2 more");
});

test("Needs you: long names shrink, never past the width", () => {
  const long = "impl-supercalifragilisticexpialidocious";
  const doc = keep(D([A("orchestrator", "hub"), A(long, "build"), A("reviewer-with-a-very-long-name", "verify")], G({
    loops: [{ worker: long, evaluator: "reviewer-with-a-very-long-name", rounds: 12, state: "sent_back" }],
    work_loops: [{ agent: "reviewer-with-a-very-long-name", rounds: 12, state: "stuck" }],
  })));
  assert.deepEqual(text(doc).slice(2, 4), ["! reviewer-with-a-very-lon… stuck ↻12", "↩ impl-su… sent back ↻12 by reviewer…"]);
});

test("Working: most expensive first, loop count from 2 rounds, goal only with a busy step", () => {
  const doc = keep(D(
    [A("orchestrator", "hub", 1, "", 9), A("w-cheap", "build", 1, "", 0.5), A("w-rich", "build", 2, "", 12.34), A("w-free", "verify", 1), A("sleeper", "build", 0, "", 99)],
    G({
      nodes: [
        { id: "1", agent: "w-rich", status: "running", busy: true, goal: "Do the thing" },
        { id: "2", agent: "w-cheap", status: "pending", busy: false, goal: "Not started" },
        { id: "3", agent: "w-free", status: "running", busy: false, goal: "Running but quiet" },
      ],
      work_loops: [
        { agent: "w-cheap", rounds: 2, state: "working", active: true },
        { agent: "w-rich", rounds: 1, state: "working", active: true },
      ],
    }),
  ));
  assert.deepEqual(text(doc, 37, 0).slice(2, 9), [
    pad("⠋ w-rich", "$12.34"),
    "  Do the thing",
    pad("⠙ w-cheap ↻2", "$0.50"),
    pad("⠹ w-free", "$0.00"),
    "  Running but quiet",
    "",
    "○ sleeper",
  ]);
});

test("Working: long name and goal at width 20 and 37", () => {
  const long = "impl-supercalifragilisticexpialidocious";
  const doc = keep(D([A("orchestrator", "hub"), A(long, "build", 1, "", 1.25)], G({
    nodes: [{ id: "n", agent: long, busy: true, status: "running", goal: "Apply the limiter in the middleware chain and update every route" }],
    work_loops: [{ agent: long, rounds: 12, state: "working", active: true }],
  })));
  assert.deepEqual(text(doc, 37).slice(2, 4), [pad("⠋ impl-supercalifragilisti… ↻12", "$1.25"), "  Apply the limiter in the middlewar…"]);
  assert.deepEqual(text(doc, 20).slice(2, 4), ["⠋ impl-su… ↻12 $1.25", "  Apply the limiter…"]);
});

test("Idle: group order, one line per group, hub and working agents left out", () => {
  const doc = keep(D([
    A("orchestrator", "hub"), A("explorer", "understand"), A("oracle", "decide"), A("council", "council"),
    A("impl-openai", "build"), A("reviewer", "verify"), A("odd", "elsewhere"), A("busy", "build", 1, "", 0.1),
  ]));
  assert.deepEqual(text(doc).slice(4), [
    "○ council", "○ oracle", "○ impl-openai", "○ reviewer", "○ explorer", "○ odd",
  ]);
});

test("Idle: a council lead comes first, seats use their short names", () => {
  const doc = keep(D([
    A("orchestrator", "hub"), A("councillor-fable", "council", 0, "council"), A("councillor-astra", "council", 0, "council"),
    A("council", "council"), A("councillor-carbon", "council", 0, "council"),
  ]));
  assert.deepEqual(text(doc).slice(2), ["○ council fable astra carbon"]);
  // seats without a listed lead
  assert.deepEqual(text(keep(D([A("orchestrator", "hub"), A("councillor-fable", "council", 0, "")]))).slice(2), ["○ fable"]);
  // a working seat moves to Working with its short name; the rest stay idle
  assert.deepEqual(
    text(keep(D([A("orchestrator", "hub"), A("council", "council"), A("councillor-fable", "council", 1, "council", 0.2), A("councillor-astra", "council", 0, "council")]))).slice(2),
    [pad("⠋ fable", "$0.20"), "", "○ council astra"],
  );
});

test("Idle: wraps onto an indented line; truncates only as a last resort", () => {
  const doc = keep(D([
    A("orchestrator", "hub"),
    ...["alpha-one", "beta-two", "gamma-three", "delta-four", "epsilon-five"].map((n) => A(n, "build")),
    A("zz", "verify"),
  ]));
  assert.deepEqual(text(doc).slice(2), [
    "○ alpha-one beta-two gamma-three",
    "  delta-four epsilon-five",
    "○ zz",
  ]);
  const fit = keep(D([A("orchestrator", "hub"), A("a".repeat(17), "build"), A("b".repeat(17), "build")]));
  assert.deepEqual(text(fit).slice(2), ["○ " + "a".repeat(17) + " " + "b".repeat(17)]); // exactly 37
  const over = keep(D([A("orchestrator", "hub"), A("a".repeat(17), "build"), A("b".repeat(18), "build")]));
  assert.deepEqual(text(over).slice(2), ["○ " + "a".repeat(17), "  " + "b".repeat(18)]);
  const huge = keep(D([A("orchestrator", "hub"), A("x".repeat(50), "build")]));
  assert.deepEqual(text(huge).slice(2), ["○ " + "x".repeat(34) + "…"]);
});

test("wide characters and escape sequences in untrusted text", () => {
  const doc = keep(D(
    [A("orchestrator", "hub"), A("evil\u001b[31mred\u0007", "build", 1, "", 0.5)],
    G({ nodes: [{ id: "n", agent: "evil\u001b[31mred\u0007", busy: true, status: "running", goal: "修复中间件链中的限流器问题并更新全部路由" }] }),
  ));
  const lines = text(doc);
  assert.equal(lines[2], pad("⠋ evilred", "$0.50"));
  assert.equal(lines[3], "  修复中间件链中的限流器问题并更新全…");
  assert.equal(R.displayWidth(lines[3]), 37);
});

test("width 20 for a hand-made document", () => {
  const doc = keep(D(
    [A("orchestrator", "hub", 1), A("council", "council", 1, "", 3), A("councillor-carbon", "council", 0, "council"), A("impl-claude", "build", 1, "", 1.25)],
    G({
      progress: { passed: 2, total: 5 },
      nodes: [{ id: "n", agent: "impl-claude", busy: true, status: "running", goal: "Apply the limiter" }],
      work_loops: [{ agent: "impl-claude", nodes: ["n"], rounds: 2, state: "working", active: true }],
    }),
    "orchestrator",
    { totals: T(1.5, 5200, 14100) },
  ));
  assert.deepEqual(text(doc, 20), [
    "◉ orchestrator ⠋ 2/5",
    "━━━━━━━━────────────",
    "$1.50  5.2k↑ 14.1k↓",
    "",
    "⠋ council      $3.00",
    "⠙ impl-cla… ↻2 $1.25",
    "  Apply the limiter",
    "",
    "○ carbon",
  ]);
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
    assert.match(screen(view)[0], /^◉ orchestrator [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]$/);
    // the hub works: the spinner advances
    const last = (l) => l[l.length - 1];
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
    await waitFor(() => screen(view).includes("○ worker"), 5000, "idle snapshot");
    assert.equal(running(), 1, "nothing works: no frame timer");
    setSid("ses_worker"); // the hub is idle, a non-hub agent works
    await waitFor(() => screen(view).some((l) => l.startsWith("⠋ worker")), 5000, "worker snapshot");
    assert.equal(running(), 2, "a non-hub agent works: frame timer runs");
    setSid("ses_idle"); // switching sessions stops it at once
    assert.equal(running(), 1, "session change stops the frame timer");
    await waitFor(() => screen(view).includes("○ worker"), 5000, "idle snapshot again");
    setSid("ses_hub");
    await waitFor(() => screen(view).some((l) => l.startsWith("◉ orchestrator ")), 5000, "hub snapshot");
    assert.equal(running(), 2, "the hub works: frame timer runs");
    setSid("ses_flip"); // worker busy on the first fetch, idle on the next poll
    await waitFor(() => screen(view).some((l) => l.startsWith("⠋ worker")), 5000, "flip: busy");
    assert.equal(running(), 2);
    await waitFor(() => screen(view).includes("○ worker"), 5000, "flip: idle");
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
