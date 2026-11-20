/*
 * The demo source (AGENTGRAPH_TUI_DEMO=1): the saved demo document (test/fixtures/demo.json, which has working,
 * stuck and sent-back agents), with a clock that advances, and no Python process.
 *
 * demoDocument(base, elapsedMs) is pure: the working agents' newest sessions cost a little more as time passes (so
 * the spend bars move), and the root gets a `wear` (the demo document has none) whose history grows turn by turn
 * and drops at each summary, so the context chart has something to draw. With the clock given, every timestamp is moved
 * so the document was generated now (the list shows how long each agent has been at its step). Nothing else changes.
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

// The runner the plugin uses in demo mode: the same interface as createSnapshotRunner, and it never rejects.
export function createDemoRunner({ fs, file, now }) {
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
        return demoDocument(base, at - start, at);
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
