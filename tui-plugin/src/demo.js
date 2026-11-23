/*
 * The demo source (AGENTGRAPH_TUI_DEMO=1): the saved demo document (test/fixtures/demo.json, which has working,
 * stuck and sent-back agents), with a clock that advances, and no Python process.
 *
 * demoDocument(base, elapsedMs) is pure: the working agents' newest sessions cost a little more as time passes (so
 * the spend bars move), and the root gets a `wear` (the demo document has none) whose history grows turn by turn
 * and drops at each summary, so the context chart has something to draw. With the clock given, every timestamp is moved
 * so the document was generated now (the list shows how long each agent has been at its step). Nothing else changes.
 *
 * AGENTGRAPH_TUI_DEMO=side: the moving demo with a side chat, a spend limit, a rate and a preset name. sideDocument().
 * AGENTGRAPH_TUI_DEMO=paths: the same team at rest (nobody works, no task graph) with sample drawn paths
 * (`structure.flows`, one run 2 h ago), so the list shows its paths row and `r` opens the chooser. pathsDocument().
 */
import { errorDocument, errorMessage } from "./runner.js";

export const DEMO_INTERVAL_MS = 2000;
const TURN_MS = 3000; // one more model call every three seconds
const SUMMARY_EVERY = 24; // turns between summaries
const WINDOW = 1000000;
const COMPACT_AT = 250000;

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function contextAt(turn) {
  const since = turn % SUMMARY_EVERY;
  return Math.round(28000 + since * 9200 + 2500 * Math.sin(turn * 1.7));
}

const TIME_KEYS = new Set(["generated", "created", "updated", "started", "finished", "time"]);

// Move every timestamp (seconds) by `delta` seconds, so the demo's steps started minutes ago, not on the day it was saved.
function shiftTimes(value, delta) {
  if (Array.isArray(value)) {
    value.forEach((v) => shiftTimes(v, delta));
  } else if (isObject(value)) {
    for (const [key, v] of Object.entries(value)) {
      if (TIME_KEYS.has(key) && typeof v === "number" && Number.isFinite(v) && v > 1e9) value[key] = v + delta;
      else if (v && typeof v === "object") shiftTimes(v, delta);
    }
  }
}

// nowMs (optional): the clock; the document's times are moved so that `generated` is now.
// What the demo's working agents are doing now (the live demo snapshot, `snapshot_json --demo`, has the same lines).
const DEMO_ACTIVITY = {
  orchestrator: {
    kind: "tool", tool: "subagent", text: null, zone: "spawn", target: "explorer: add the 429 page", status: "running",
    recent: [
      { zone: "web", tool: "webfetch", target: "example.com/docs/intro", status: "completed", seconds: 1.2 },
      { zone: "terminal", tool: "shell", target: "npm test", status: "completed", seconds: 8.4 },
      { zone: "files", tool: "edit", target: "~/project/src/limits.js", status: "completed", seconds: 0.1 },
      { zone: "search", tool: "grep", target: "retry_after", status: "error", seconds: 0.3 },
      { zone: "spawn", tool: "subagent", target: "explorer: map the limiter", status: "completed", seconds: 41 },
    ],
  },
  "impl-claude": { kind: "tool", tool: "shell", text: null },
  designer: { kind: "reasoning", tool: null, text: "Check the three widths before writing the copy." },
};

export function demoDocument(base, elapsedMs, nowMs) {
  const doc = JSON.parse(JSON.stringify(isObject(base) ? base : {}));
  const t = Math.max(0, Number(elapsedMs) || 0);
  const now = Number(nowMs);
  if (Number.isFinite(now) && now > 0 && typeof doc.generated === "number") shiftTimes(doc, now / 1000 - doc.generated);
  doc.source = "demo";
  let total = 0;
  for (const agent of Array.isArray(doc.agents) ? doc.agents : []) {
    if (!isObject(agent)) continue;
    const sessions = Array.isArray(agent.sessions) ? agent.sessions : [];
    const busy = Number(agent.busy) > 0;
    if (busy && !isObject(agent.activity) && DEMO_ACTIVITY[agent.name]) agent.activity = Object.assign({ time: typeof doc.generated === "number" ? doc.generated : 0 }, DEMO_ACTIVITY[agent.name]);
    sessions.forEach((s, i) => {
      if (!isObject(s)) return;
      if (busy && i === 0 && typeof s.cost === "number") s.cost = Math.round((s.cost + (t / 1000) * 0.006) * 10000) / 10000;
      if (typeof s.cost === "number") total += s.cost;
    });
  }
  if (isObject(doc.totals)) doc.totals.cost = Math.round(total * 100) / 100;
  if (isObject(doc.root)) {
    const turns = 70 + Math.floor(t / TURN_MS);
    const first = Math.max(0, turns - 60);
    const history = [];
    for (let i = first; i < turns; i += 1) history.push(contextAt(i));
    const context = history[history.length - 1];
    const summaries = Math.floor(turns / SUMMARY_EVERY);
    doc.root.context = context;
    doc.root.wear = {
      context,
      window: WINDOW,
      window_known: true,
      compact_at: COMPACT_AT,
      summaries,
      turns,
      turns_since_summary: turns % SUMMARY_EVERY,
      history,
      per_turn: { start: 0.02, recent: 0.05 },
      level: summaries >= 3 ? "worn" : summaries === 0 && context < 0.6 * COMPACT_AT ? "fresh" : "long",
    };
  }
  return doc;
}

// Sample paths: one named and run (with a cost), one named and run once without a cost, one never run and unnamed.
export function samplePaths(nowSec) {
  return [
    { id: "impl-openai-verifier-impl-openai", path: ["impl-openai", "verifier", "impl-openai"], name: "impl-loop", command: null,
      stats: { runs: 3, passed: 2, rounds: { avg: 2, max: 3 }, cost: { total: 3.6, avg: 1.2 }, last: nowSec - 2 * 3600 } },
    { id: "explorer-oracle-impl-claude", path: ["explorer", "oracle", "impl-claude"], name: "deep-change", command: null,
      stats: { runs: 1, passed: 1, rounds: { avg: 1, max: 1 }, cost: { total: null, avg: null }, last: nowSec - 3 * 86400 } },
    { id: "designer-reviewer-designer", path: ["designer", "reviewer", "designer"], name: null, command: null, stats: null },
  ];
}

// The demo at rest with sample paths: nobody works, no task graph, no needs.
export function pathsDocument(base, elapsedMs, nowMs) {
  const doc = demoDocument(base, elapsedMs, nowMs);
  for (const agent of Array.isArray(doc.agents) ? doc.agents : []) {
    if (!isObject(agent)) continue;
    agent.busy = 0;
    for (const s of Array.isArray(agent.sessions) ? agent.sessions : []) if (isObject(s)) s.busy = false;
  }
  if (isObject(doc.root)) doc.root.busy = false;
  doc.graph = null;
  doc.graphs = [];
  const nowSec = typeof doc.generated === "number" ? doc.generated : Number(nowMs) / 1000;
  doc.structure = Object.assign({ source: "builtin", teams: [] }, isObject(doc.structure) ? doc.structure : {}, { flows: samplePaths(nowSec) });
  return doc;
}

// The runner the plugin uses in demo mode: the same interface as createSnapshotRunner, and it never rejects.
// The moving demo with the web snapshot's `sidechat`, `setup` and `spend` (AGENTGRAPH_TUI_DEMO=side): the side chat's row and
// moon, the limit, the rate and the split, the preset name. Fixed samples (agentgraph/demo.py); nothing is read or called.
export function sideDocument(base, elapsedMs, nowMs) {
  const doc = demoDocument(base, elapsedMs, nowMs);
  const spent = isObject(doc.totals) && typeof doc.totals.cost === "number" ? doc.totals.cost : 0;
  doc.sidechat = { installed: true, session: "ses_demoside", model: "openai/gpt-6-luna", busy: false, spent: 0.0042 };
  doc.setup = { preset: "normal", presets: ["ultra-fast-frontier", "ultra-fast", "fast", "normal"], override: null, saved: true, undo: null };
  doc.spend = { limit: 50, warn: 40, rate: 22, chat: 0.0042, workers: spent };
  return doc;
}

export function createDemoRunner({ fs, file, now, paths = false, side = false }) {
  const clock = typeof now === "function" ? now : Date.now;
  const start = clock();
  let base = null;
  let stopped = false;
  return {
    async snapshot() {
      if (stopped) return errorDocument("The plugin stopped.");
      try {
        if (!base) base = JSON.parse(fs.readFileSync(file, "utf8"));
        const at = clock();
        return (paths ? pathsDocument : side ? sideDocument : demoDocument)(base, at - start, at);
      } catch (error) {
        return errorDocument("The demo document could not be read: " + errorMessage(error));
      }
    },
    stop() {
      stopped = true;
    },
    running: () => 0,
    inflight: () => 0,
  };
}
