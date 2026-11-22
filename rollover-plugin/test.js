// No-network test of index.js with a fake OpenCode context and fake events: `node rollover-plugin/test.js`.
"use strict";
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "rollover-test-"));
let n = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A plugin copy with its own settings file, a fake `agentgraph` command and a fake OpenCode context.
// `from` reuses the folders of an earlier harness (a restart of OpenCode).
async function harness(settings, { build, leads = "true", from = null, rawSettings = null, getDelay = {}, getFail = false } = {}) {
  const dir = from ? from.dir : path.join(root, `case${n++}`);
  const home = path.join(dir, "home");
  process.env.AGENTGRAPH_ROLLOVER_HOME = home;
  fs.mkdirSync(path.join(dir, "plugin"), { recursive: true });
  fs.copyFileSync(path.join(__dirname, "index.js"), path.join(dir, "plugin", "index.js"));
  const argsFile = path.join(dir, "build-args.txt");
  const fake = path.join(dir, "fake-agentgraph");
  fs.writeFileSync(fake, `#!/bin/sh\nif [ "$1 $2" = "handoff leads" ]; then\n${leads}\nexit 0\nfi\necho "$@" >> "${argsFile}"\n${build || "echo SUMMARY"}\n`, { mode: 0o755 });
  const settingsFile = path.join(dir, "plugin", "rollover.json");
  fs.writeFileSync(settingsFile, rawSettings !== null ? rawSettings : JSON.stringify({ agentgraph_bin: fake, ...settings }));
  const queue = []; let wake = null;
  const calls = { synthetic: [], compact: [], get: [] }; const hooks = {};
  const infos = new Map(); const failures = new Set();
  const sessions = [];
  const add = (sid, { parent = "ses_parent", agent = "impl-openai", folder = null } = {}) => {
    const work = folder || path.join(dir, "work-" + sid);
    infos.set(sid, { id: sid, agent, parentID: parent || undefined, location: { directory: work } });
    sessions.push(sid);
    return { sid, work };
  };
  const main = from ? add(from.main.sid, { folder: from.main.work }) : add("ses_test" + n);
  const fakeCtx = {
    location: { directory: path.join(dir, "work-main") },
    event: { subscribe: ({ signal }) => ({ [Symbol.asyncIterator]() { return { next: () => new Promise((resolve) => {
      const step = () => { if (queue.length) return resolve({ value: queue.shift(), done: false }); if (signal.aborted) return resolve({ done: true }); wake = step; };
      step(); }) }; } }) },
    session: {
      get: async ({ sessionID }) => { calls.get.push(sessionID); if (getDelay[sessionID]) await sleep(getDelay[sessionID]); if (getFail) throw new Error("lookup failed"); return infos.get(sessionID) || null; },
      synthetic: async (a) => { calls.synthetic.push(a); if (failures.has("synthetic")) throw new Error("boom"); },
      compact: async (a) => { calls.compact.push(a); if (failures.has("compact")) throw new Error("boom"); },
      hook: async (name, fn) => { hooks[name] = fn; },
    },
  };
  const fresh = require(path.join(dir, "plugin", "index.js"));
  const dispose = await fresh.setup(fakeCtx);
  await dispose.idle();   // `agentgraph handoff leads` is read once at start; events wait for it
  const h = { dir, home, main, sid: main.sid, work: main.work, calls, hooks, dispose, fail: (k) => failures.add(k), add, argsFile, settingsFile };
  // `wait` is a fixed time in ms (only where a test needs one session to be still busy); by default the plugin is waited for until it is idle
  h.emit = async (type, data, { sid = main.sid, wait = null } = {}) => {
    queue.push({ type, data: { sessionID: sid, ...data } }); if (wake) { const w = wake; wake = null; w(); }
    if (wait === null) { await new Promise((r) => setImmediate(r)); await dispose.idle(); } else if (wait) await sleep(wait);
  };
  h.step = (context, finish = "tool-calls", sid = main.sid) => h.emit("session.step.ended", { finish, tokens: { input: 5, output: 10, cache: { read: context - 15, write: 10 } } }, { sid });
  h.notesPath = (sid = main.sid) => path.join(infos.get(sid).location.directory, ".opencode", "handoff", `${sid}.md`);
  // size 5: "notes" (a lane's); bigger: text of that many characters that names the topics of the chat floor; `body` overrides the text
  const topical = (size) => "Objective: x. Requirements and limits: y. Decisions: z. Work state: done. Next steps: a. Files: b. ".repeat(Math.ceil(size / 100)).slice(0, size);
  h.notes = (sid = main.sid, ageSeconds = 0, size = 5, body = null) => {
    const t0 = Date.now(); while (Date.now() === t0) { /* the notes get a later millisecond than every event before them (the plugin compares whole milliseconds) */ }
    const f = h.notesPath(sid); fs.mkdirSync(path.dirname(f), { recursive: true }); fs.writeFileSync(f, body !== null ? body : size <= 5 ? "notes".padEnd(size, " ") : topical(size));
    if (ageSeconds) { const t = (Date.now() - ageSeconds * 1000) / 1000; fs.utimesSync(f, t, t); }
  };
  h.logRows = () => { try { return fs.readFileSync(path.join(home, "rollover.log"), "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)); } catch { return []; } };
  h.state = (sid = main.sid) => { try { return JSON.parse(fs.readFileSync(path.join(home, "state", `${sid}.json`), "utf8")); } catch { return null; } };
  h.buildArgs = () => { try { return fs.readFileSync(argsFile, "utf8").trim().split("\n"); } catch { return []; } };
  h.hook = async (sid = main.sid) => { const event = { sessionID: sid, agent: "impl-openai" }; await hooks.compaction(event); return event; };
  h.has = (event, re) => h.logRows().some((r) => r.event === event && (!re || re.test(r.reason || "")));
  h.stop = () => dispose && dispose();
  // a normal rollover: ask, notes, roll, hook, ended
  h.roll = async (start = 116000) => {
    await h.step(start); h.notes(); await h.step(start + 1000);
    const event = await h.hook(); await h.emit("session.compaction.ended", { reason: "manual", text: "x" });
    return event;
  };
  return h;
}

const cases = {
  async "below ask_at nothing"() {
    const h = await harness({}); await h.step(50000); await h.step(114000);
    assert.equal(h.calls.synthetic.length, 0); assert.equal(h.calls.compact.length, 0); h.stop();
  },
  async "at ask_at one request, a .gitignore in the notes folder, said in the request"() {
    const h = await harness({}); await h.step(115000); await h.step(116000);
    assert.equal(h.calls.synthetic.length, 1); assert.equal(h.calls.synthetic[0].delivery, "steer");
    assert.ok(h.calls.synthetic[0].text.includes(`${h.sid}.md`)); assert.ok(h.calls.synthetic[0].text.includes(".gitignore"));
    assert.equal(fs.readFileSync(path.join(h.work, ".opencode", "handoff", ".gitignore"), "utf8"), "*\n");
    assert.equal(h.calls.compact.length, 0);
    assert.equal(h.logRows().filter((r) => r.event === "asked").length, 1); h.stop();
  },
  async "at ask_at with notes one compact, the hook supplies the summary, --newer-than is passed"() {
    const h = await harness({}); await h.step(116000); h.notes(); await h.step(117000); await h.step(122000);
    assert.equal(h.calls.compact.length, 1);
    const event = await h.hook(); assert.equal(event.result && event.result.summary.trim(), "SUMMARY");
    const args = h.buildArgs()[0]; assert.match(args, /--newer-than \d{13}\b/);
    assert.ok(h.has("summary_supplied")); h.stop();
  },
  async "the hook leaves a compaction the plugin did not request to OpenCode, even past ask_at"() {
    const h = await harness({}); await h.step(130000);       // past ask_at, no notes, no request from the plugin
    assert.equal(h.calls.compact.length, 0);
    const event = await h.hook(); assert.equal(event.result, undefined);
    assert.deepEqual(h.buildArgs(), []);                     // the build command was not even run
    assert.ok(h.has("own_compaction", /own-compaction: left, no notes file/)); h.stop();
  },
  async "round 7: a compaction right after a rollover, before any step has ended, gets the last summary again with no build; bounded"() {
    const h = await harness({}); await h.roll();            // a full cycle ends: nothing is pending any more
    const event = await h.hook(); assert.equal(event.result && event.result.summary.trim(), "SUMMARY");
    assert.equal(h.buildArgs().length, 1, "no second build"); assert.ok(h.has("own_compaction", /^own-compaction: supplied, the last summary again/)); h.stop();
    // a step has ended since: the context is no longer the one the summary made, so the normal rules apply (no fresh notes: left)
    const a = await harness({}); await a.roll(); await a.step(9000);
    const later = await a.hook(); assert.equal(later.result, undefined); assert.equal(a.buildArgs().length, 1); a.stop();
    // the notes were rewritten since the summary was built: the old summary would be stale (the normal rules then judge the new notes)
    const b = await harness({}); await b.roll(); await sleep(15); b.notes();
    const edited = await b.hook(); assert.equal(b.buildArgs().length, 2, "the old summary is not given again: the newer notes are built into a new one"); assert.ok(!b.has("own_compaction", /again/)); b.stop();
    // too long after the rollover (it is meant for the queued second request, seconds later)
    const c = await harness({ compaction_patience_ms: 200 }); await c.roll(); await sleep(300);
    const old = await c.hook(); assert.equal(old.result, undefined); c.stop();
    // a model-summary compaction in between ends the reuse
    const d = await harness({}); await d.roll(); await d.emit("session.compaction.failed", { error: { type: "X" } });
    const failed = await d.hook(); assert.equal(failed.result, undefined); d.stop();
  },
  async "notes older than the last rollover do not count"() {
    const h = await harness({}); await h.roll();
    assert.equal(h.calls.compact.length, 1);
    await h.step(116000); await h.step(117000); await h.step(125000);   // the old notes file is still there
    assert.equal(h.calls.compact.length, 1);
    assert.ok(h.calls.synthetic.length >= 2, "asked again after the rollover");
    h.notes(); await h.step(126000); assert.equal(h.calls.compact.length, 2); h.stop();
  },
  async "notes are not counted as covering unless written after the previous step ended"() {
    const h = await harness({}); h.notes(); await h.step(116000); await h.step(117000);   // notes exist from before: they cover nothing
    assert.equal(h.calls.compact.length, 0); h.stop();
  },
  async "a restart loses nothing: notes, last rollover and coverage are reloaded"() {
    const h = await harness({}); await h.step(100000); h.notes(); await h.step(106000);   // notes without an ask
    const saved = h.state(); assert.ok(saved.notesMtime > 0); assert.equal(saved.notesCtx, 106000); h.stop();
    const again = await harness({}, { from: h });
    await again.step(116000);                                 // 10,000 past the notes: covered without new notes
    assert.equal(again.calls.compact.length, 1);
    await again.hook(); await again.emit("session.compaction.ended", { reason: "manual" });
    const rolled = again.state(); assert.ok(rolled.lastRollover > 0); again.stop();
    const third = await harness({}, { from: h });
    await third.step(116000); await third.step(117000);       // the notes file is older than the saved rollover
    assert.equal(third.calls.compact.length, 0); third.stop();
  },
  async "notes that cover only part of the context: ask again, roll only when covered"() {
    const h = await harness({}); await h.step(100000); h.notes(); await h.step(106000);   // notes without an ask: covered up to 106,000
    await h.step(116000);                                      // 10,000 behind, inside notes_gap: covered, rolls
    assert.equal(h.calls.compact.length, 1); h.stop();
    const g = await harness({}); await g.step(100000); g.notes(); await g.step(106000);
    await g.step(117000);                                      // 11,000 behind: not covered, asked
    assert.equal(g.calls.compact.length, 0); assert.equal(g.calls.synthetic.length, 1);
    assert.ok(g.logRows().some((r) => r.event === "asked" && /tokens behind/.test(r.reason)));
    g.notes(); await g.step(118000); assert.equal(g.calls.compact.length, 1); g.stop();
  },
  async "asks at most max_asks times"() {
    const h = await harness({ max_asks: 2, reask_steps: 1, reask_growth: 1000 });
    for (let c = 116000; c < 122000; c += 2000) await h.step(c);
    assert.equal(h.calls.synthetic.length, 2); h.stop();
  },
  async "several sessions in different folders keep separate files, and a slow one does not block another"() {
    const h = await harness({}, { getDelay: { ses_slowA: 600 } });
    const a = h.add("ses_slowA"), b = h.add("ses_quickB");
    await h.emit("session.step.ended", { finish: "tool-calls", tokens: { input: 5, output: 1, cache: { read: 120000, write: 0 } } }, { sid: a.sid, wait: 0 });
    await h.emit("session.step.ended", { finish: "tool-calls", tokens: { input: 5, output: 1, cache: { read: 120000, write: 0 } } }, { sid: b.sid, wait: 150 });
    assert.equal(h.calls.synthetic.filter((c) => c.sessionID === b.sid).length, 1, "B was asked while A's lookup was still running");
    assert.equal(h.calls.synthetic.filter((c) => c.sessionID === a.sid).length, 0);
    await sleep(700);
    assert.equal(h.calls.synthetic.filter((c) => c.sessionID === a.sid).length, 1);
    assert.ok(fs.existsSync(path.join(a.work, ".opencode", "handoff", ".gitignore"))); assert.ok(fs.existsSync(path.join(b.work, ".opencode", "handoff", ".gitignore")));
    assert.ok(h.calls.synthetic.find((c) => c.sessionID === a.sid).text.includes(a.work));
    assert.ok(h.calls.synthetic.find((c) => c.sessionID === b.sid).text.includes(b.work));
    assert.ok(h.state(a.sid) && h.state(b.sid)); assert.ok(!fs.existsSync(path.join(a.work, ".opencode", "handoff", "rollover.log")));
    assert.deepEqual(fs.readdirSync(path.join(a.work, ".opencode", "handoff")).sort(), [".gitignore"]);   // only the lane's notes will live here
    h.stop();
  },
  async "an out-of-scope session writes no file anywhere, including on compaction.ended"() {
    const h = await harness({}); const root1 = h.add("ses_root", { parent: null });
    await h.emit("session.step.ended", { finish: "tool-calls", tokens: { input: 5, cache: { read: 130000, write: 0 } } }, { sid: root1.sid });
    await h.emit("session.execution.started", {}, { sid: root1.sid });
    await h.emit("session.compaction.started", { reason: "auto" }, { sid: root1.sid });
    await h.emit("session.compaction.ended", { reason: "auto" }, { sid: root1.sid });
    await h.emit("session.compaction.failed", { error: { type: "x" } }, { sid: root1.sid });
    await h.emit("session.deleted", {}, { sid: root1.sid });
    assert.ok(!fs.existsSync(h.home), "no plugin folder, so no state or log");
    assert.ok(!fs.existsSync(root1.work), "no project folder");
    assert.equal(h.calls.synthetic.length + h.calls.compact.length, 0); h.stop();
  },
  async "a session id that is not valid is ignored before any path is built"() {
    const h = await harness({}); h.add("../evil");
    await h.step(130000, "tool-calls", "../evil"); await h.step(130000, "tool-calls", "a/b");
    assert.equal(h.calls.get.length, 0); assert.ok(!fs.existsSync(h.home)); h.stop();
  },
  async "a failed session lookup is cached for a minute and leaves the session alone"() {
    const h = await harness({}, { getFail: true });
    await h.step(130000); await h.step(131000); await h.step(132000);
    assert.equal(h.calls.get.length, 1); assert.equal(h.calls.synthetic.length, 0); h.stop();
  },
  async "an unknown session is out of scope"() {
    const h = await harness({}); await h.step(130000, "tool-calls", "ses_nobody");
    assert.equal(h.calls.synthetic.length, 0); assert.ok(!fs.existsSync(h.home)); h.stop();
  },
  async "a session without a parent is skipped"() {
    const h = await harness({}); const r = h.add("ses_r2", { parent: null }); await h.step(106000, "tool-calls", r.sid); h.notes(r.sid); await h.step(130000, "tool-calls", r.sid);
    assert.equal(h.calls.synthetic.length, 0); assert.equal(h.calls.compact.length, 0); h.stop();
  },
  async "skip_agents, only_agents and an unknown agent"() {
    const h = await harness({ skip_agents: ["impl-openai"] }); await h.step(116000); await h.step(130000);
    assert.equal(h.calls.synthetic.length, 0); h.stop();
    const d = await harness({}); const orch = d.add("ses_orch", { agent: "orchestrator" }); await d.step(130000, "tool-calls", orch.sid);
    assert.equal(d.calls.synthetic.length, 0, "default skip_agents keeps the orchestrating agents alone"); d.stop();
    const o = await harness({ only_agents: ["impl-hard"] }); await o.step(130000);
    assert.equal(o.calls.synthetic.length, 0); const hard = o.add("ses_hard", { agent: "impl-hard" }); await o.step(130000, "tool-calls", hard.sid);
    assert.equal(o.calls.synthetic.length, 1); o.stop();
    const u = await harness({}); const unk = u.add("ses_unk", { agent: null }); await u.step(130000, "tool-calls", unk.sid);
    assert.equal(u.calls.synthetic.length, 0, "an unknown agent is left alone when agents are named"); u.stop();
  },
  async "enabled false does nothing"() {
    const h = await harness({ enabled: false }); await h.step(116000); h.notes(); await h.step(130000);
    assert.equal(h.calls.synthetic.length, 0); assert.equal(h.calls.compact.length, 0);
    const event = await h.hook(); assert.equal(event.result, undefined); assert.equal(h.logRows().length, 0); h.stop();
  },
  async "thresholds that are strings, null, too small or missing fall back to the defaults, with one log line"() {
    const h = await harness({ ask_at: "5000", roll_at: null, hard_at: 10 }); await h.step(114000);
    assert.equal(h.calls.synthetic.length, 0, "ask_at is the default 115,000, not the string");
    await h.step(116000); assert.equal(h.calls.synthetic.length, 1);
    await h.step(107000); await h.step(108000);
    const rows = h.logRows().filter((r) => r.event === "settings_invalid"); assert.equal(rows.length, 1);
    assert.match(rows[0].reason, /ask_at/); assert.match(rows[0].reason, /roll_at/); assert.match(rows[0].reason, /hard_at/); h.stop();
  },
  async "thresholds out of order all go back to the defaults"() {
    const h = await harness({ ask_at: 150000, hard_at: 140000 }); await h.step(120000);
    assert.equal(h.calls.synthetic.length, 1, "ask_at is 115,000 again");
    assert.ok(h.has("settings_invalid", /ask_at < hard_at/)); h.stop();
  },
  async "a malformed rollover.json gives the defaults and one log line"() {
    const h = await harness({}, { rawSettings: "{ not json" }); await h.step(116000); await h.step(107000);
    assert.equal(h.calls.synthetic.length, 1); assert.equal(h.logRows().filter((r) => r.event === "settings_invalid").length, 1); h.stop();
  },
  async "a malformed family override is ignored, a good one applies"() {
    const bad = await harness({ families: { opus: { roll_at: "soon" }, sonnet: 5 } });
    await bad.emit("session.step.started", { agent: "impl-openai", model: { id: "claude-opus-5" } });
    await bad.step(120000); assert.equal(bad.calls.synthetic.length, 1);
    assert.ok(bad.has("settings_invalid", /families/)); bad.stop();
    const good = await harness({ families: { opus: { ask_at: 50000, hard_at: 70000 } } });
    await good.emit("session.step.started", { agent: "impl-openai", model: { id: "claude-opus-5" } });
    await good.step(55000); assert.equal(good.calls.synthetic.length, 1); good.stop();
  },
  async "a failing build leaves event.result unset"() {
    const h = await harness({}, { build: "exit 3" }); await h.step(116000); h.notes(); await h.step(117000);
    const event = await h.hook(); assert.equal(event.result, undefined); assert.ok(h.has("fell_back")); h.stop();
  },
  async "the task could not be read (exit 4): no summary, said in the log"() {
    const h = await harness({}, { build: "exit 4" }); await h.step(116000); h.notes(); await h.step(117000);
    const event = await h.hook(); assert.equal(event.result, undefined); assert.ok(h.has("fell_back", /task/)); h.stop();
  },
  async "a hanging build times out and leaves event.result unset"() {
    const h = await harness({ build_timeout_ms: 1000 }, { build: "exec sleep 5" }); await h.step(116000); h.notes(); await h.step(117000);
    const event = await h.hook(); assert.equal(event.result, undefined); assert.ok(h.has("fell_back", /timed out/)); h.stop();
  },
  async "a build that cannot start is logged once and stops further asks in this process"() {
    const h = await harness({}, { build: "exit 2" }); await h.step(116000); h.notes(); await h.step(117000);
    await h.hook(); await h.hook();
    assert.equal(h.logRows().filter((r) => r.event === "build_unavailable").length, 1);
    const b = h.add("ses_other"); await h.step(120000, "tool-calls", b.sid);
    assert.equal(h.calls.synthetic.filter((c) => c.sessionID === b.sid).length, 0); h.stop();
  },
  async "a failing ask is caught and logged, and the next events still work"() {
    const h = await harness({}); h.fail("synthetic"); await h.step(116000);
    assert.ok(h.has("error", /ask: boom/));
    h.notes(); await h.step(117000); assert.equal(h.calls.compact.length, 1); h.stop();
  },
  async "a failed compact call is logged and not retried on every step"() {
    const h = await harness({}); h.fail("compact"); await h.step(116000); h.notes(); await h.step(117000);
    assert.ok(h.has("compaction_failed", /compact: boom/));
    for (let i = 0; i < 5; i++) await h.step(118000 + i * 100);
    assert.equal(h.calls.compact.length, 1, "waits 5 steps");
    await h.step(118600); assert.equal(h.calls.compact.length, 2); h.stop();
  },
  async "failed compactions back off 5, 10 steps, and after 3 in a row the session is left alone until one ends well"() {
    const h = await harness({}); await h.step(116000); h.notes(); await h.step(117000);
    assert.equal(h.calls.compact.length, 1);
    await h.emit("session.compaction.failed", { reason: "manual", error: { type: "UnknownError", message: "SECRET prompt text" } });
    assert.ok(h.has("compaction_failed", /UnknownError/)); assert.ok(!JSON.stringify(h.logRows()).includes("SECRET"), "never the error message");
    for (let i = 0; i < 5; i++) await h.step(117500);
    assert.equal(h.calls.compact.length, 1, "no re-ask storm: 5 steps of waiting");
    await h.step(117500); assert.equal(h.calls.compact.length, 2);
    await h.emit("session.compaction.failed", { error: { type: "UnknownError" } });
    for (let i = 0; i < 10; i++) await h.step(117500);
    assert.equal(h.calls.compact.length, 2, "10 steps of waiting");
    await h.step(117500); assert.equal(h.calls.compact.length, 3);
    await h.emit("session.compaction.failed", { error: { type: "UnknownError" } });
    for (let i = 0; i < 30; i++) await h.step(121500, "tool-calls", h.sid);
    assert.equal(h.calls.compact.length, 3, "given up");
    assert.equal(h.logRows().filter((r) => r.event === "gave_up").length, 1);
    assert.equal(h.calls.synthetic.length, 1, "no new asks either");
    await h.emit("session.compaction.ended", { reason: "auto" });                    // one ends well: back to normal
    await h.step(116000); assert.equal(h.calls.synthetic.length, 2); h.stop();
  },
  async "only a compaction that ended is a rollover; a failed one is not"() {
    const h = await harness({}); await h.step(116000); h.notes(); await h.step(117000); await h.hook();
    await h.emit("session.compaction.failed", { error: { type: "X" } });
    assert.ok(!h.logRows().some((r) => r.event === "rolled")); assert.equal(h.state().lastRollover, 0);
    await h.emit("session.compaction.ended", { reason: "manual" }); assert.ok(h.state().lastRollover > 0); h.stop();
  },
  async "pause: cold cache and notes that cover the context roll over at the next turn"() {
    const h = await harness({ cache_seconds: 1, pause_min: 60000 }); await h.step(100000); h.notes(); await h.step(108000, "stop");
    await h.emit("session.execution.succeeded", {}); await sleep(1200); await h.emit("session.execution.started", {});
    assert.equal(h.calls.compact.length, 1); assert.ok(h.has("roll_requested", /pause/)); h.stop();
  },
  async "pause: warm cache, or notes that do not cover the context, do nothing"() {
    const h = await harness({ cache_seconds: 100, pause_min: 60000 }); await h.step(100000); h.notes(); await h.step(108000, "stop");
    await h.emit("session.execution.started", {}); assert.equal(h.calls.compact.length, 0); h.stop();
    const g = await harness({ cache_seconds: 1, pause_min: 60000 }); await g.step(108000, "stop"); await sleep(1200); await g.emit("session.execution.started", {});
    assert.equal(g.calls.compact.length, 0); g.stop();
  },
  async "the pause trigger uses the same coverage rule as the roll trigger"() {
    const h = await harness({ cache_seconds: 1, pause_min: 60000 }); await h.step(100000); h.notes(); await h.step(101000, "stop");
    await h.step(112000, "stop");                                // 11,000 past the notes: over notes_gap (10,000)
    await sleep(1200); await h.emit("session.execution.started", {});
    assert.equal(h.calls.compact.length, 0); h.stop();
  },
  async "state files older than 30 days are deleted at start, a deleted session loses its file"() {
    const home = path.join(root, "oldstate", "home"); fs.mkdirSync(path.join(home, "state"), { recursive: true });
    const old = path.join(home, "state", "ses_old.json"), young = path.join(home, "state", "ses_young.json");
    fs.writeFileSync(old, "{}"); fs.writeFileSync(young, "{}"); const t = (Date.now() - 31 * 86400000) / 1000; fs.utimesSync(old, t, t);
    const h = await harness({}, { from: { dir: path.join(root, "oldstate"), main: { sid: "ses_young", work: path.join(root, "oldstate", "w") } } });
    assert.ok(!fs.existsSync(old)); assert.ok(fs.existsSync(young)); h.stop();
    const d = await harness({}); await d.step(120000); assert.ok(d.state()); await d.emit("session.deleted", {}); assert.equal(d.state(), null); d.stop();
  },
  async "a malformed event is ignored and a throwing hook is caught"() {
    const h = await harness({}); await h.emit("session.step.ended", { finish: "tool-calls", tokens: null });
    await h.emit("session.step.ended", { tokens: { input: "x", cache: 5 } });
    await h.hooks.compaction(null); await h.hooks.compaction({}); await h.hooks.compaction({ sessionID: "../x" }); await h.step(50000); h.stop();
  },
  async "harness: a slow `handoff leads` read (0.4 s) and a slow session lookup do not change a decision (the test waits for the plugin, not for a fixed time)"() {
    const h = await harness({}, { leads: "sleep 0.4", getDelay: { ses_slowC: 300 } });
    await h.step(130000); assert.equal(h.calls.synthetic.length, 1, "asked before the next line runs");
    const c = h.add("ses_slowC"); await h.step(130000, "tool-calls", c.sid); assert.equal(h.calls.synthetic.filter((x) => x.sessionID === c.sid).length, 1); h.stop();
  },
  // ---- round 3 ----
  async "N1: another process rolled the session over; this one reloads before it decides and does not overwrite that state"() {
    const d = await harness({}); await d.step(100000); d.notes(); await d.step(101000);   // D: notes without an ask, covering 101,000
    await sleep(20); d.notes();                                                            // rewritten just before the other process rolls
    const c = await harness({}, { from: d }); await c.step(117000);                        // C continues the session: loads D's state, rolls over
    assert.equal(c.calls.compact.length, 1); await c.hook(); await c.emit("session.compaction.ended", { reason: "manual" });
    const rolledAt = c.state().lastRollover; assert.ok(rolledAt > 0); await c.step(120000);
    await d.step(117000);                                                                   // the session comes back to D, which still holds lastRollover 0
    assert.equal(d.calls.compact.length, 0, "D must not roll over on notes that C's rollover already used");
    assert.ok(d.has("state_reloaded"));
    assert.equal(d.state().lastRollover, rolledAt, "D did not write its old state over C's");
    assert.deepEqual(fs.readdirSync(path.join(d.home, "state")), [`${d.sid}.json`], "state is written through a temp file that is renamed: none is left");
    d.stop(); c.stop();
  },
  async "N1: a state file that another process changed is read again at every event, one that this process wrote is not"() {
    const d = await harness({}); await d.step(100000); await d.step(101000); await d.step(102000);
    assert.ok(!d.has("state_reloaded"), "its own writes are not mistaken for another process's");
    const f = path.join(d.home, "state", `${d.sid}.json`); const v = JSON.parse(fs.readFileSync(f, "utf8"));
    fs.writeFileSync(f, JSON.stringify({ ...v, steps: 40, askCount: 3 })); await d.step(103000);
    assert.equal(d.state().steps, 41); assert.equal(d.logRows().filter((r) => r.event === "state_reloaded").length, 1); d.stop();
  },
  async "N2: answered notes roll at the same step boundary, with no waiting for a higher threshold"() {
    const h = await harness({}); await h.step(120000); assert.equal(h.calls.synthetic.length, 1);
    h.notes(); await h.step(121000);                            // the lane writes them in its next step
    assert.equal(h.calls.compact.length, 1); assert.ok(h.has("roll_requested", /context 121000/)); assert.equal(h.calls.synthetic.length, 1); h.stop();
  },
  async "N2: the first plugin's trace: a reading step lands over ask_at, the ask is answered, the roll comes at once with no module in between"() {
    const h = await harness({});
    for (const c of [7, 31, 32, 56, 56, 81, 81, 105, 106]) await h.step(c * 1000);
    assert.equal(h.calls.synthetic.length, 0, "ask_at is 115k: no ask yet"); assert.equal(h.calls.compact.length, 0);
    await h.step(130000); assert.equal(h.calls.synthetic.length, 1, "the ask lands at the step that crosses ask_at");
    h.notes(); await h.step(132000);
    assert.equal(h.calls.compact.length, 1, "rolled at the step that holds the notes"); assert.ok(h.has("roll_requested", /context 132000/));
    assert.equal(h.calls.synthetic.length, 1); h.stop();
  },
  async "N2: the round 4 trace: notes at 108k, then a module read to 131k: those notes are not coverage, the lane is asked again, the roll follows the new notes"() {
    const h = await harness({});
    for (const c of [7, 31, 31, 56, 56, 57, 81, 81, 81, 105, 106]) await h.step(c * 1000);
    h.notes(); await h.step(108000);                            // the lane wrote its notes early (no ask: below ask_at)
    assert.equal(h.calls.synthetic.length, 0); assert.equal(h.calls.compact.length, 0);
    await h.step(131000);                                       // then it read the next module: 23k past the notes
    assert.equal(h.calls.compact.length, 0, "no roll on notes that miss a module"); assert.equal(h.calls.synthetic.length, 1, "asked again");
    assert.ok(h.has("asked", /tokens behind/));
    h.notes(); await h.step(132000);
    assert.equal(h.calls.compact.length, 1, "rolled on the new notes"); h.stop();
  },
  async "N2: notes followed by growth over notes_gap are not an answer; growth inside it is"() {
    const g = await harness({}); await g.step(120000); g.notes(); await g.step(121000);   // answer: rolled
    assert.equal(g.calls.compact.length, 1); g.stop();
    const h = await harness({}); await h.step(100000); h.notes(); await h.step(106000);
    await h.step(116000); assert.equal(h.calls.compact.length, 1, "10,000 past the notes is inside the gap"); h.stop();
    const f = await harness({}); await f.step(100000); f.notes(); await f.step(106000);
    await f.step(116001); assert.equal(f.calls.compact.length, 0, "10,001 is over it"); assert.equal(f.calls.synthetic.length, 1); f.stop();
  },
  async "N2: notes written without an ask roll at the first step end at or above ask_at, with no ask"() {
    const h = await harness({}); await h.step(100000); h.notes(); await h.step(110000);
    assert.equal(h.calls.compact.length, 0); assert.equal(h.calls.synthetic.length, 0);
    await h.step(116000); assert.equal(h.calls.compact.length, 1); assert.equal(h.calls.synthetic.length, 0); h.stop();
  },
  async "an old rollover.json with roll_at still loads: roll_at is ignored with one log line, the rest applies"() {
    const h = await harness({ ask_at: 110000, roll_at: 120000, hard_at: 150000 });
    await h.step(112000); assert.equal(h.calls.synthetic.length, 1, "ask_at 110,000 applies");
    h.notes(); await h.step(113000); assert.equal(h.calls.compact.length, 1, "rolled before the old roll_at");
    const rows = h.logRows().filter((r) => r.event === "settings_invalid"); assert.equal(rows.length, 1); assert.match(rows[0].reason, /roll_at: no longer used/); h.stop();
    const f = await harness({ families: { opus: { ask_at: 50000, roll_at: 60000, hard_at: 70000 } } });
    await f.emit("session.step.started", { agent: "impl-openai", model: { id: "claude-opus-5" } });
    await f.step(55000); assert.equal(f.calls.synthetic.length, 1); assert.ok(f.has("settings_invalid", /families\.opus\.roll_at: no longer used/)); f.stop();
  },
  async "the ask says: finish the tool call, write the notes now, nothing is kept after them, nothing needs reading again"() {
    const h = await harness({}); await h.step(120000); const text = h.calls.synthetic[0].text;
    assert.match(text, /Finish the tool call you are in, then write your handoff notes NOW/); assert.match(text, /anything you do after writing them is lost/);
    assert.match(text, /nothing has to be read again/); assert.ok(text.length < 2400, String(text.length)); h.stop();
  },
  async "the defaults, by behaviour: ask_at 115,000, notes_gap 10,000, hard_at 160,000, hook_growth 15,000; there is no roll_at"() {
    const h = await harness({}); await h.step(114999); assert.equal(h.calls.synthetic.length, 0); await h.step(115000); assert.equal(h.calls.synthetic.length, 1); h.stop();
    const f = await harness({}); await f.step(116000); await f.step(159999); assert.ok(!f.has("fell_back")); await f.step(160000); assert.ok(f.has("fell_back", /hard_at/)); f.stop();
    const s = JSON.parse(fs.readFileSync(path.join(__dirname, "rollover.json"), "utf8"));
    assert.ok(!("roll_at" in s)); assert.equal(s.ask_at, 115000); assert.equal(s.hard_at, 160000); assert.equal(s.notes_gap, 10000); assert.equal(s.hook_growth, 15000);
  },
  async "N2: crossing hard_at without coverage and then catching up"() {
    const h = await harness({}); await h.step(116000);
    await h.step(135000); await h.step(165000);
    assert.equal(h.calls.compact.length, 0); assert.equal(h.logRows().filter((r) => r.event === "fell_back").length, 1); assert.ok(h.has("fell_back", /hard_at/));
    h.notes(); await h.step(166000);
    assert.equal(h.calls.compact.length, 1, "the notes arrive late and the rollover still happens");
    const event = await h.hook(); assert.equal(event.result.summary.trim(), "SUMMARY"); assert.equal(h.logRows().filter((r) => r.event === "fell_back").length, 1); h.stop();
  },
  async "N3: the hook checks coverage again: a context that grew too far since the request gets no code summary"() {
    const h = await harness({}); await h.step(116000); h.notes(); await h.step(117000); assert.equal(h.calls.compact.length, 1);
    await h.step(132000);                                       // 15,000 of growth: still fine
    const ok = await h.hook(); assert.equal(ok.result && ok.result.summary.trim(), "SUMMARY"); h.stop();
    const g = await harness({}); await g.step(116000); g.notes(); await g.step(117000); assert.equal(g.calls.compact.length, 1);
    await g.step(133000);                                       // 16,000 past the request
    const late = await g.hook(); assert.equal(late.result, undefined); assert.deepEqual(g.buildArgs(), [], "no build was even run");
    assert.ok(g.has("fell_back", /grew 16000/)); g.stop();
  },
  async "N3: OpenCode's own compaction runs before the plugin's queued request: the second gets the same code summary again"() {
    const h = await harness({}); await h.step(116000); h.notes(); await h.step(117000); assert.equal(h.calls.compact.length, 1);
    const first = await h.hook(); assert.equal(first.result && first.result.summary.trim(), "SUMMARY");   // OpenCode's own, answered while a request is pending
    await h.emit("session.compaction.ended", { reason: "auto" });
    const second = await h.hook();                                                                          // the queued request now runs on a small context
    assert.equal(second.result && second.result.summary.trim(), "SUMMARY", "the same summary again, not a model paraphrase of it"); assert.equal(h.buildArgs().length, 1, "with no build");
    assert.ok(h.has("own_compaction", /supplied, the last summary again/)); h.stop();
  },
  // ---- round 6: the hook answers OpenCode's own compaction when the notes are fresh ----
  async "round 6: the child trace: ask at 129k, the lane writes its notes in the 154k step, OpenCode's own compaction fires first: one code summary, no model summary, no second request"() {
    const h = await harness({});
    for (const c of [19, 20, 20, 8, 31, 32, 56, 56, 81, 81, 105, 105]) await h.step(c * 1000);
    assert.equal(h.calls.synthetic.length, 0);
    await h.step(129000); assert.equal(h.calls.synthetic.length, 1, "ask at 129k");
    h.notes();                                                        // written inside the next step (the 154k one)
    const own = await h.hook();                                       // OpenCode's own compaction fires before the plugin sees that step end
    assert.equal(own.result && own.result.summary.trim(), "SUMMARY", "one code summary"); assert.equal(h.buildArgs().length, 1);
    assert.ok(h.has("own_compaction", /^own-compaction: supplied$/));
    await h.step(154000);                                             // the step end arrives after the hook
    assert.equal(h.calls.compact.length, 0, "the plugin requests no compaction of its own for that step");
    assert.equal(h.calls.synthetic.length, 1, "and asks for nothing");
    assert.ok(h.has("step_ignored")); assert.equal(h.state().steps, 13, "the late step end was not counted");
    await h.emit("session.compaction.ended", { reason: "auto" });
    assert.ok(h.has("rolled", /opencode compaction/)); assert.ok(h.state().lastRollover > 0);
    for (const c of [9, 33, 33, 58]) await h.step(c * 1000);
    assert.equal(h.calls.compact.length, 0); assert.equal(h.calls.synthetic.length, 1); h.stop();
  },
  async "round 6: the step end that arrives after the compaction ended is ignored: no ask on the replaced context"() {
    const h = await harness({}); await h.step(129000); h.notes();
    const own = await h.hook(); assert.equal(own.result && own.result.summary.trim(), "SUMMARY");
    await h.emit("session.compaction.ended", { reason: "auto" });     // a quick code summary: the end comes first
    await h.step(154000);
    assert.equal(h.calls.synthetic.length, 1, "no new ask"); assert.equal(h.calls.compact.length, 0); assert.ok(h.has("step_ignored"));
    await h.step(9000); assert.equal(h.state().steps, 2, "the next step counts");
    h.stop();
  },
  async "round 6: own compaction with stale, older or missing notes leaves event.result unset"() {
    const a = await harness({}); await a.step(129000);                  // asked, notes never written
    const none = await a.hook(); assert.equal(none.result, undefined); assert.deepEqual(a.buildArgs(), []);
    assert.ok(a.has("own_compaction", /own-compaction: left, no notes file/)); a.stop();
    const b = await harness({}); b.notes(); await sleep(30); await b.step(129000);   // notes from before the ask
    const old = await b.hook(); assert.equal(old.result, undefined); assert.ok(b.has("own_compaction", /left, notes older than the last ask/)); assert.deepEqual(b.buildArgs(), []); b.stop();
    const c = await harness({}); c.notes(); await c.step(100000);       // notes written before the first step end: they cover nothing
    const early = await c.hook(); assert.equal(early.result, undefined); assert.ok(c.has("own_compaction", /notes written before the last step ended and not counted as covering it/)); assert.deepEqual(c.buildArgs(), []); c.stop();
    const behind = await harness({}); await behind.step(100000); behind.notes(); await behind.step(101000);
    await behind.step(108000, "stop"); await behind.step(111500, "stop");   // 10,500 past the notes, under ask_at
    const lateEv = await behind.hook(); assert.equal(lateEv.result, undefined); assert.ok(behind.has("own_compaction", /tokens behind the context \(limit 10000\)/)); behind.stop();
    const d = await harness({}); await d.roll(); await d.step(9000);     // a whole rollover, a step, then OpenCode compacts again: the old notes are not fresh
    const again = await d.hook(); assert.equal(again.result, undefined); assert.ok(d.has("own_compaction", /notes older than the last rollover/)); d.stop();
  },
  async "round 6: own compaction with notes that fail the build gets no summary, and no second compaction follows"() {
    const h = await harness({}, { build: "exit 3" }); await h.step(129000); h.notes();
    const ev = await h.hook(); assert.equal(ev.result, undefined); assert.ok(h.has("own_compaction", /left, notes missing or older/));
    await h.step(154000); assert.equal(h.calls.compact.length, 0, "OpenCode made its model summary for that step: the plugin does not queue a second compaction"); h.stop();
  },
  async "round 6: a step end that arrives while the hook is still building the own-compaction summary does not request a second compaction"() {
    const h = await harness({}, { build: "sleep 1; echo SUMMARY" }); await h.step(129000); h.notes();
    const pending = h.hook(); await sleep(300); await h.step(154000);
    assert.equal(h.calls.compact.length, 0, "held as the pending request"); assert.equal(h.calls.synthetic.length, 1);
    const ev = await pending; assert.equal(ev.result && ev.result.summary.trim(), "SUMMARY"); h.stop();
  },
  async "round 6: a root chat and an agent in skip_agents are left alone by the hook even with fresh notes"() {
    const h = await harness({}); const root1 = h.add("ses_rootchat", { parent: null }), orch = h.add("ses_orch2", { agent: "orchestrator" });
    for (const s of [root1, orch]) { await h.step(129000, "tool-calls", s.sid); h.notes(s.sid); const ev = await h.hook(s.sid); assert.equal(ev.result, undefined); }
    assert.deepEqual(h.buildArgs(), []); assert.ok(!h.has("own_compaction")); h.stop();
  },
  async "round 6: own compaction answered while the plugin's own request is queued: the first gets the summary, the queued second the same summary again (it cannot be withdrawn)"() {
    const h = await harness({}); await h.step(116000); h.notes(); await h.step(117000); assert.equal(h.calls.compact.length, 1);
    const first = await h.hook(); assert.equal(first.result && first.result.summary.trim(), "SUMMARY");
    await h.emit("session.compaction.ended", { reason: "auto" });
    const second = await h.hook(); assert.equal(second.result && second.result.summary.trim(), "SUMMARY"); assert.equal(h.buildArgs().length, 1); assert.equal(h.calls.compact.length, 1); h.stop();
  },
  // ---- round 7 ----
  async "round 7 item 1: notes written before a step that started and has not ended: the big read is not covered (own compaction; a requested one is not refused for it)"() {
    const h = await harness({}); await h.step(100000); h.notes(); await h.step(101000); await sleep(8);   // notes cover 101k; below ask_at
    await h.emit("session.step.started", { agent: "impl-openai" }); await sleep(8);                       // the next step began (a big read); its end has not been seen
    const ev = await h.hook(); assert.equal(ev.result, undefined); assert.deepEqual(h.buildArgs(), []);
    assert.ok(h.has("own_compaction", /written before the step that is running began/)); h.stop();
    // a requested compaction is different: the next step normally starts before the hook runs (measured in round 7), its tool results are not in the context yet
    // and growth already seen is limited by hook_growth, so a started step does not refuse a requested summary
    const r = await harness({}); await r.step(116000); r.notes(); await r.step(117000); assert.equal(r.calls.compact.length, 1); await sleep(8);
    await r.emit("session.step.started", { agent: "impl-openai" }); await sleep(8);
    const ev2 = await r.hook(); assert.equal(ev2.result && ev2.result.summary.trim(), "SUMMARY"); assert.ok(r.has("summary_supplied")); r.stop();
    const ok = await harness({}); await ok.step(100000); await ok.emit("session.step.started", { agent: "impl-openai" }); await sleep(8); ok.notes();   // written inside the running step: fine
    const inside = await ok.hook(); assert.equal(inside.result && inside.result.summary.trim(), "SUMMARY"); ok.stop();
  },
  async "round 7 item 2: no saved state (a first event that is not a step end): any notes file is not fresh"() {
    const h = await harness({}); h.notes(); await h.emit("session.execution.started", {});
    const ev = await h.hook(); assert.equal(ev.result, undefined); assert.deepEqual(h.buildArgs(), []); assert.ok(h.has("own_compaction", /no step seen/)); h.stop();
  },
  async "round 7 item 3: a compaction that failed after the hook answered: the next end is not a rollover and the next step is counted"() {
    const h = await harness({}); await h.step(129000); h.notes();
    const ev = await h.hook(); assert.equal(ev.result && ev.result.summary.trim(), "SUMMARY");
    await h.emit("session.compaction.failed", { error: { type: "X" } });
    await h.step(154000); assert.equal(h.state().steps, 2, "the step after the failure is a real step");
    assert.ok(!h.has("step_ignored")); await h.emit("session.compaction.ended", { reason: "auto" });
    assert.ok(h.has("compacted")); assert.ok(!h.has("rolled"), "a compaction that got a model summary is not logged as ours"); h.stop();
  },
  async "round 7 item 4: a plugin-requested compaction that ends before its late step end: that step end is ignored, no ask on the reset context"() {
    const h = await harness({}); await h.step(116000); h.notes(); await h.step(117000); assert.equal(h.calls.compact.length, 1);
    const ev = await h.hook(); assert.equal(ev.result && ev.result.summary.trim(), "SUMMARY"); assert.ok(h.has("summary_supplied"));
    await h.emit("session.compaction.ended", { reason: "manual" });
    await h.step(118000);                                           // the step that was in flight when the compaction was requested ends now
    assert.equal(h.calls.synthetic.length, 1, "no new ask"); assert.equal(h.calls.compact.length, 1); assert.ok(h.has("step_ignored"));
    await h.step(9000); await h.step(33000); assert.equal(h.state().steps, 4); assert.equal(h.calls.synthetic.length, 1); h.stop();
  },
  async "round 7 item 5: a settings edit that moves a session out of scope stops the summary, own and requested path"() {
    const out = (h) => { fs.writeFileSync(h.settingsFile, JSON.stringify({ agentgraph_bin: JSON.parse(fs.readFileSync(h.settingsFile, "utf8")).agentgraph_bin, skip_agents: ["impl-openai"] }));
      const f = Date.now() / 1000 + 5; fs.utimesSync(h.settingsFile, f, f); };
    const h = await harness({}); await h.step(129000); h.notes(); out(h);
    const ev = await h.hook(); assert.equal(ev.result, undefined); assert.deepEqual(h.buildArgs(), []); assert.ok(!h.has("own_compaction")); h.stop();
    const r = await harness({}); await r.step(116000); r.notes(); await r.step(117000); assert.equal(r.calls.compact.length, 1); out(r);
    const ev2 = await r.hook(); assert.equal(ev2.result, undefined); assert.deepEqual(r.buildArgs(), []); r.stop();
  },
  async "round 7 item 6: the task cannot be read (exit 4): the session is left alone in this process, own and requested path"() {
    const h = await harness({}, { build: "exit 4" }); await h.step(129000); assert.equal(h.calls.synthetic.length, 1); h.notes();
    const ev = await h.hook(); assert.equal(ev.result, undefined); assert.ok(h.has("task_unreadable")); assert.ok(h.has("own_compaction", /left, the task could not be read/));
    await h.emit("session.compaction.ended", { reason: "auto" });
    await h.step(120000); await h.step(125000); await h.step(130000);
    assert.equal(h.calls.synthetic.length, 1, "no new ask"); assert.equal(h.calls.compact.length, 0, "no request"); h.stop();
    const r = await harness({}, { build: "exit 4" }); await r.step(116000); r.notes(); await r.step(117000); assert.equal(r.calls.compact.length, 1);
    await r.hook(); assert.ok(r.has("task_unreadable")); await r.emit("session.compaction.ended", { reason: "manual" });
    for (const c of [116000, 120000, 125000]) await r.step(c);
    assert.equal(r.calls.synthetic.length, 1, "no second cycle of asking"); assert.equal(r.calls.compact.length, 1, "and no second request that would end in a model summary");
    const own = await r.hook(); assert.equal(own.result, undefined); r.stop();
  },
  async "round 7 item 10: rollover.json lists every key of the defaults and loads with no settings_invalid line"() {
    const file = fs.readFileSync(path.join(__dirname, "rollover.json"), "utf8"), keys = Object.keys(JSON.parse(file));
    const src = fs.readFileSync(path.join(__dirname, "index.js"), "utf8"), block = src.slice(src.indexOf("const DEFAULTS = {"), src.indexOf("};", src.indexOf("const DEFAULTS = {")));
    const declared = [...block.matchAll(/^  ([a-z_]+):/gm)].map((x) => x[1]);
    assert.deepEqual([...keys].sort(), [...declared].sort(), "every default is in rollover.json and nothing else is");
    const h = await harness({}, { rawSettings: file }); await h.step(116000); assert.ok(!h.has("settings_invalid"), JSON.stringify(h.logRows())); assert.equal(h.calls.synthetic.length, 1); h.stop();
  },
  // ---- chat rollover (x1): a root chat of an agent in chat_agents ----
  async "chat: by default a root chat of the orchestrator is left alone: no ask, no compaction, no summary, no file"() {
    const h = await harness({}); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(130000, "tool-calls", c.sid); await h.step(150000, "stop", c.sid); h.notes(c.sid, 0, 400); await h.step(152000, "stop", c.sid);
    assert.equal(h.calls.synthetic.length, 0); assert.equal(h.calls.compact.length, 0);
    const ev = { sessionID: c.sid, agent: "orchestrator" }; await h.hooks.compaction(ev); assert.equal(ev.result, undefined);
    assert.deepEqual(h.buildArgs(), []); assert.equal(h.state(c.sid), null, "no state file"); assert.ok(!fs.existsSync(path.join(c.work, ".opencode", "handoff", ".gitignore")));
    h.stop();
  },
  async "chat: a listed agent's root chat is asked with the chat ask; lanes, other root agents and a listed agent's child sessions keep the old rules"() {
    const h = await harness({ chat_agents: ["Orchestrator"] }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(116000, "tool-calls", c.sid);
    assert.equal(h.calls.synthetic.length, 1); const text = h.calls.synthetic[0].text;
    assert.ok(text.includes("ses_chat.md")); assert.ok(/user's own messages/.test(text) && /session id of each running subagent/.test(text) && /one short line/.test(text) && /quoted/.test(text) && /replaced/.test(text) && /lost/.test(text), "the chat ask carries what the notes must hold");
    assert.ok(!/task as you were given it/.test(text), "not the lane's ask");
    assert.ok(h.has("asked")); assert.equal(h.logRows().find((r) => r.event === "asked").agent, "orchestrator");
    const lane = h.add("ses_lane", { agent: "impl-openai" }); await h.step(116000, "tool-calls", lane.sid);
    assert.equal(h.calls.synthetic.length, 2); assert.ok(/task as you were given it/.test(h.calls.synthetic[1].text), "a lane still gets the lane's ask");
    const other = h.add("ses_build", { parent: null, agent: "build" }); await h.step(130000, "tool-calls", other.sid);
    const child = h.add("ses_orchchild", { parent: "ses_chat", agent: "orchestrator" }); await h.step(130000, "tool-calls", child.sid);
    const council = h.add("ses_council", { agent: "council" }); await h.step(130000, "tool-calls", council.sid);
    assert.equal(h.calls.synthetic.length, 2, "root build chat, orchestrator child session and council stay out"); h.stop();
  },
  async "chat: the build is told to make the chat summary (with the caps), a lane's build is told --lane"() {
    const h = await harness({ chat_agents: ["orchestrator"], chat_message_chars: 3000, chat_user_chars: 9000 }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(116000, "tool-calls", c.sid); h.notes(c.sid, 0, 400); await h.step(117000, "tool-calls", c.sid);
    assert.equal(h.calls.compact.length, 1); const ev = { sessionID: c.sid, agent: "orchestrator" }; await h.hooks.compaction(ev);
    assert.equal(ev.result && ev.result.summary.trim(), "SUMMARY");
    const line = h.buildArgs()[0]; assert.ok(/--chat /.test(line + " ") && /--user-message-chars 3000/.test(line) && /--user-chars 9000/.test(line) && !/--lane/.test(line), line);
    await h.emit("session.compaction.ended", { reason: "manual" }, { sid: c.sid }); assert.ok(h.has("rolled"));
    const lane = h.add("ses_lane", { agent: "impl-openai" }); await h.step(116000, "tool-calls", lane.sid); h.notes(lane.sid, 0, 400); await h.step(117000, "tool-calls", lane.sid);
    await h.hooks.compaction({ sessionID: lane.sid, agent: "impl-openai" });
    const laneLine = h.buildArgs()[1]; assert.ok(/--lane/.test(laneLine) && !/--chat/.test(laneLine), laneLine); h.stop();
  },
  async "chat: chat_ask_at moves the ask for a chat only; a value at or over hard_at, or not a number, is ignored with one log line"() {
    const h = await harness({ chat_agents: ["orchestrator"], chat_ask_at: 90000 }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(89000, "tool-calls", c.sid); assert.equal(h.calls.synthetic.length, 0); await h.step(91000, "tool-calls", c.sid); assert.equal(h.calls.synthetic.length, 1);
    const lane = h.add("ses_lane", { agent: "impl-openai" }); await h.step(100000, "tool-calls", lane.sid); assert.equal(h.calls.synthetic.length, 1, "a lane still asks at 115,000"); h.stop();
    const over = await harness({ chat_agents: ["orchestrator"], chat_ask_at: 170000 }); const o = over.add("ses_chat", { parent: null, agent: "orchestrator" });
    await over.step(116000, "tool-calls", o.sid); assert.equal(over.calls.synthetic.length, 1, "ask_at is used"); over.stop();
    const bad = await harness({ chat_agents: ["orchestrator"], chat_ask_at: "90000" }); const b = bad.add("ses_chat", { parent: null, agent: "orchestrator" });
    await bad.step(100000, "tool-calls", b.sid); assert.equal(bad.calls.synthetic.length, 0); assert.ok(bad.has("settings_invalid", /chat_ask_at/)); bad.stop();
    const list = await harness({ chat_agents: "orchestrator" }); await list.step(116000); assert.ok(list.has("settings_invalid", /chat_agents/)); list.stop();
  },
  async "chat: the ask goes out at the end of the answer too (finish stop), a lane's does not; the chat then rolls over like a lane"() {
    const h = await harness({ chat_agents: ["orchestrator"] }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(120000, "stop", c.sid); assert.equal(h.calls.synthetic.length, 1, "asked at the end of the answer");
    const lane = h.add("ses_lane", { agent: "impl-openai" }); await h.step(120000, "stop", lane.sid); assert.equal(h.calls.synthetic.length, 1, "a lane's turn end asks nothing");
    h.notes(c.sid, 0, 400); await h.step(121000, "stop", c.sid);          // the answer to the ask: notes written, one short line
    assert.equal(h.calls.compact.length, 1, "the notes cover the context: the compaction is requested at once");
    const ev = { sessionID: c.sid, agent: "orchestrator" }; await h.hooks.compaction(ev); assert.equal(ev.result && ev.result.summary.trim(), "SUMMARY");
    await h.emit("session.compaction.ended", { reason: "manual" }, { sid: c.sid }); assert.ok(h.has("rolled")); h.stop();
  },
  async "chat: OpenCode's own compaction of a chat gets the code summary when the notes are fresh, and a model summary when they are not"() {
    const h = await harness({ chat_agents: ["orchestrator"] }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(129000, "tool-calls", c.sid);
    const stale = { sessionID: c.sid, agent: "orchestrator" }; await h.hooks.compaction(stale); assert.equal(stale.result, undefined); assert.ok(h.has("own_compaction", /left, no notes file/));
    h.notes(c.sid, 0, 400);
    const own = { sessionID: c.sid, agent: "orchestrator" }; await h.hooks.compaction(own); assert.equal(own.result && own.result.summary.trim(), "SUMMARY"); assert.ok(h.has("own_compaction", /supplied/)); h.stop();
  },
  async "chat: a subagent's report between the ask and the roll: before the notes it changes nothing, after the request a small one is covered, a big one falls back to a model summary (never a stale code summary)"() {
    const mk = async () => { const h = await harness({ chat_agents: ["orchestrator"] }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" }); return { h, c, ev: () => ({ sessionID: c.sid, agent: "orchestrator" }) }; };
    const a = await mk();                                                        // the report arrives before the notes are written
    await a.h.step(116000, "tool-calls", a.c.sid); await a.h.step(140000, "stop", a.c.sid);
    assert.equal(a.h.calls.compact.length, 0); assert.equal(a.h.calls.synthetic.length, 1, "no second ask yet");
    a.h.notes(a.c.sid, 0, 400); await a.h.step(141000, "stop", a.c.sid); assert.equal(a.h.calls.compact.length, 1, "the notes step covers it"); a.h.stop();
    const b = await mk();                                                        // a small report between the request and the hook
    await b.h.step(116000, "tool-calls", b.c.sid); b.h.notes(b.c.sid, 0, 400); await b.h.step(117000, "tool-calls", b.c.sid); await b.h.step(120000, "stop", b.c.sid);
    const e1 = b.ev(); await b.h.hooks.compaction(e1); assert.equal(e1.result && e1.result.summary.trim(), "SUMMARY"); b.h.stop();
    const c = await mk();                                                        // a big one: the notes do not cover it, so no code summary
    await c.h.step(116000, "tool-calls", c.c.sid); c.h.notes(c.c.sid, 0, 400); await c.h.step(117000, "tool-calls", c.c.sid); await c.h.step(142000, "stop", c.c.sid);
    const e2 = c.ev(); await c.h.hooks.compaction(e2); assert.equal(e2.result, undefined); assert.ok(c.h.has("fell_back", /grew/));
    await c.h.emit("session.compaction.ended", { reason: "manual" }, { sid: c.c.sid }); assert.ok(c.h.has("compacted")); assert.equal(c.h.calls.compact.length, 1, "no second request"); c.h.stop();
  },
  async "chat: a settings edit that takes the agent out of chat_agents stops the summary at once, and the chat is out of scope again"() {
    const k = await harness({ chat_agents: ["orchestrator"] }); const kc = k.add("ses_chat", { parent: null, agent: "orchestrator" });
    await k.step(129000, "tool-calls", kc.sid); k.notes(kc.sid, 0, 400);
    const control = { sessionID: kc.sid, agent: "orchestrator" }; await k.hooks.compaction(control); assert.equal(control.result && control.result.summary.trim(), "SUMMARY", "in scope before the edit"); k.stop();
    const h = await harness({ chat_agents: ["orchestrator"] }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(129000, "tool-calls", c.sid); h.notes(c.sid, 0, 400);
    fs.writeFileSync(h.settingsFile, JSON.stringify({ agentgraph_bin: JSON.parse(fs.readFileSync(h.settingsFile, "utf8")).agentgraph_bin })); const f = Date.now() / 1000 + 5; fs.utimesSync(h.settingsFile, f, f);
    const ev = { sessionID: c.sid, agent: "orchestrator" }; await h.hooks.compaction(ev); assert.equal(ev.result, undefined); assert.deepEqual(h.buildArgs(), []); h.stop();
  },
  async "chat: rollover.json lists the chat settings with the defaults (workers_only true, orchestrator still in skip_agents)"() {
    const s = JSON.parse(fs.readFileSync(path.join(__dirname, "rollover.json"), "utf8"));
    assert.deepEqual(s.chat_agents, []); assert.equal(s.chat_ask_at, null); assert.ok(s.skip_agents.includes("orchestrator")); assert.equal(s.workers_only, true);
    assert.ok(s.chat_message_chars > 0 && s.chat_user_chars > s.chat_message_chars);
  },
  // ---- fix round (x1 review): floor for chat notes, one-off database failure, unseen_step ----
  async "chat: notes under the floor do not cover the context; the chat is asked again at the next step end with a line that says so, then rolls; a lane's short notes still roll"() {
    const h = await harness({ chat_agents: ["orchestrator"] }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(116000, "tool-calls", c.sid); assert.equal(h.calls.synthetic.length, 1);
    assert.ok(!/too short/.test(h.calls.synthetic[0].text));
    h.notes(c.sid, 0, 40); await h.step(117000, "stop", c.sid);                       // "Notes saved." and little else
    assert.equal(h.calls.compact.length, 0, "no roll on thin notes"); assert.equal(h.calls.synthetic.length, 2, "asked again at once, not after reask_steps");
    assert.ok(/too short/.test(h.calls.synthetic[1].text) && /Write them again in full/.test(h.calls.synthetic[1].text));
    h.notes(c.sid, 0, 400); await h.step(118000, "stop", c.sid); assert.equal(h.calls.compact.length, 1, "full notes roll");
    const own = await harness({ chat_agents: ["orchestrator"] }); const o = own.add("ses_chat", { parent: null, agent: "orchestrator" });
    await own.step(129000, "tool-calls", o.sid); own.notes(o.sid, 0, 40); const ev = { sessionID: o.sid, agent: "orchestrator" }; await own.hooks.compaction(ev);
    assert.equal(ev.result, undefined, "thin notes: OpenCode's own compaction gets no code summary"); assert.ok(own.has("own_compaction", /left, no notes file/));
    const l = await harness({}); await l.step(116000); l.notes(); await l.step(117000); assert.equal(l.calls.compact.length, 1, "a lane's 5-byte notes still roll");
    l.stop(); own.stop(); h.stop();
  },
  async "chat: a build that cannot read the database (exit 5) is a one-off: that compaction gets a model summary and the chat is asked and rolled again later; exit 4 still leaves a session alone"() {
    const h = await harness({ chat_agents: ["orchestrator"] }, { build: "exit 5" }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(116000, "tool-calls", c.sid); h.notes(c.sid, 0, 400); await h.step(117000, "tool-calls", c.sid);
    const ev = { sessionID: c.sid, agent: "orchestrator" }; await h.hooks.compaction(ev);
    assert.equal(ev.result, undefined); assert.ok(h.has("fell_back", /one-off/)); assert.ok(!h.has("task_unreadable"));
    await h.emit("session.compaction.ended", { reason: "manual" }, { sid: c.sid });
    await h.step(30000, "stop", c.sid); await h.step(120000, "tool-calls", c.sid);
    assert.equal(h.calls.synthetic.length, 2, "asked again in the next cycle: not switched off for the life of the process"); h.stop();
    const g = await harness({ chat_agents: ["orchestrator"] }, { build: "exit 4" }); const d = g.add("ses_chat", { parent: null, agent: "orchestrator" });
    await g.step(116000, "tool-calls", d.sid); g.notes(d.sid, 0, 400); await g.step(117000, "tool-calls", d.sid); await g.hooks.compaction({ sessionID: d.sid, agent: "orchestrator" });
    assert.ok(g.has("task_unreadable")); g.stop();
  },
  async "chat: unseen_step is logged when a step that began after the request has not ended (its growth is not checked), and not otherwise; the same for OpenCode's own compaction"() {
    const mk = async () => { const h = await harness({ chat_agents: ["orchestrator"] }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" }); return { h, c, ev: () => ({ sessionID: c.sid, agent: "orchestrator" }) }; };
    const row = (h, event) => h.logRows().find((r) => r.event === event);
    const a = await mk();                                             // requested, a step is running when the hook is called
    await a.h.step(116000, "tool-calls", a.c.sid); a.h.notes(a.c.sid, 0, 400); await a.h.step(117000, "tool-calls", a.c.sid);
    await sleep(5); await a.h.emit("session.step.started", { agent: "orchestrator", model: { id: "claude-sonnet-5-5" } }, { sid: a.c.sid });
    await a.h.hooks.compaction(a.ev()); assert.equal(row(a.h, "summary_supplied").unseen_step, true); a.h.stop();
    const b = await mk();                                             // requested, every step end seen
    await b.h.step(116000, "tool-calls", b.c.sid); b.h.notes(b.c.sid, 0, 400); await b.h.step(117000, "tool-calls", b.c.sid);
    await b.h.hooks.compaction(b.ev()); assert.equal(row(b.h, "summary_supplied").unseen_step, false); b.h.stop();
    const c = await mk();                                             // OpenCode's own compaction while a step runs, notes written inside it
    await c.h.step(129000, "tool-calls", c.c.sid); await sleep(5); await c.h.emit("session.step.started", { agent: "orchestrator", model: { id: "claude-sonnet-5-5" } }, { sid: c.c.sid });
    c.h.notes(c.c.sid, 0, 400); await c.h.hooks.compaction(c.ev()); assert.equal(row(c.h, "own_compaction").unseen_step, true); c.h.stop();
  },
  async "chat: one floor in the plugin and the build: notes over the length but naming fewer than 3 topics bring a re-ask, not a roll and not a model summary; the numbers and topic words equal the build's"() {
    const src = fs.readFileSync(path.join(__dirname, "..", "agentgraph", "handoff.py"), "utf8");
    const pyMin = Number(/CHAT_NOTES_MIN, REPORT_CHARS = (\d+)/.exec(src)[1]);
    const pyTopics = [...(/NOTE_TOPICS = \(([^\n]+)\)\n/.exec(src)[1]).matchAll(/\(([^()]*)\)/g)].map((m) => [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]));
    const js = fs.readFileSync(path.join(__dirname, "index.js"), "utf8");
    const jsMin = Number(/const CHAT_NOTES_FLOOR = (\d+);/.exec(js)[1]);
    const jsTopics = JSON.parse(/const NOTE_TOPICS = (\[\[.*\]\]);/.exec(js)[1]);
    assert.equal(pyMin, 300); assert.equal(jsMin, pyMin); assert.deepEqual(jsTopics, pyTopics);
    const h = await harness({ chat_agents: ["orchestrator"] }); const c = h.add("ses_chat", { parent: null, agent: "orchestrator" });
    await h.step(116000, "tool-calls", c.sid); assert.equal(h.calls.synthetic.length, 1);
    h.notes(c.sid, 0, 400, "Notes saved. ".repeat(40) + " Objective: finish. Next steps: go."); await h.step(117000, "stop", c.sid);   // 400+ characters, only 2 topics
    assert.equal(h.calls.compact.length, 0); assert.equal(h.calls.synthetic.length, 2, "asked again at once"); assert.ok(/too short/.test(h.calls.synthetic[1].text));
    const ev = { sessionID: c.sid, agent: "orchestrator" }; await h.hooks.compaction(ev); assert.equal(ev.result, undefined);
    h.notes(c.sid, 0, 400); await h.step(118000, "stop", c.sid); assert.equal(h.calls.compact.length, 1, "notes with the topics roll");
    h.stop();
  },
  async "N3: a request is dropped by a reload and then gets no summary either"() {
    const d = await harness({}); await d.step(116000); d.notes(); await d.step(117000); assert.equal(d.calls.compact.length, 1);
    await sleep(10);
    const f = path.join(d.home, "state", `${d.sid}.json`); fs.writeFileSync(f, JSON.stringify({ lastRollover: Date.now(), steps: 9 }));   // another process rolled it over
    const event = await d.hook(); assert.equal(event.result, undefined); d.stop();
  },
  async "N4: council is skipped by default; the lead names that `handoff leads` prints are skipped too"() {
    const h = await harness({}, { leads: "echo Reviewer; echo team-lead; echo 'bad name!'" });
    const council = h.add("ses_c", { agent: "council" }), rev = h.add("ses_r", { agent: "reviewer" }), lead = h.add("ses_l", { agent: "team-lead" });
    for (const s of [council, rev, lead]) await h.step(120000, "tool-calls", s.sid);
    assert.equal(h.calls.synthetic.length, 0); await h.step(120000); assert.equal(h.calls.synthetic.length, 1, "an ordinary worker is still asked"); h.stop();
    const off = await harness({ skip_leads: false }, { leads: "echo reviewer" }); const r2 = off.add("ses_r", { agent: "reviewer" });
    await off.step(120000, "tool-calls", r2.sid); assert.equal(off.calls.synthetic.length, 1, "skip_leads false"); off.stop();
  },
  async "N4: when the leads cannot be read one line says so and the plugin still works"() {
    const h = await harness({}, { leads: "exit 1" }); await h.step(120000); await h.step(111000);
    assert.equal(h.logRows().filter((r) => r.event === "leads_unreadable").length, 1); assert.equal(h.calls.synthetic.length, 1);
    const o = h.add("ses_o", { agent: "orchestrator" }); await h.step(120000, "tool-calls", o.sid); assert.equal(h.calls.synthetic.length, 1, "skip_agents still applies"); h.stop();
  },
  async "N4: the leads are read once per process, not at events"() {
    const h = await harness({}, { leads: `echo x >> "${path.join(root, "leads-count")}"` });
    for (let i = 0; i < 4; i++) await h.step(110000 + i);
    assert.equal(fs.readFileSync(path.join(root, "leads-count"), "utf8").trim().split("\n").length, 1); h.stop();
  },
  async "N5: notes_dir outside .opencode/ falls back to the default with one log line, and no .gitignore lands elsewhere"() {
    for (const bad of [".", "src", "notes", ".opencode", "./", "../x", ".opencode/../src", "/tmp/.opencode/x", 5, null]) {
      const h = await harness({ notes_dir: bad }); await h.step(120000);
      assert.equal(h.logRows().filter((r) => r.event === "settings_invalid" && /notes_dir/.test(r.reason)).length, 1, String(bad));
      assert.ok(!fs.existsSync(path.join(h.work, ".gitignore")), "no .gitignore at the work folder: " + bad);
      assert.ok(!fs.existsSync(path.join(h.work, "src")));
      assert.equal(fs.readFileSync(path.join(h.work, ".opencode", "handoff", ".gitignore"), "utf8"), "*\n");
      assert.ok(h.calls.synthetic[0].text.includes(path.join(h.work, ".opencode", "handoff", `${h.sid}.md`))); h.stop();
    }
  },
  async "N5: a folder under .opencode/ works, and the build is given the same relative folder"() {
    const h = await harness({ notes_dir: ".opencode\\mine/deep" }); await h.step(120000);
    const folder = path.join(h.work, ".opencode", "mine", "deep");
    assert.ok(h.calls.synthetic[0].text.includes(path.join(folder, `${h.sid}.md`)));
    assert.equal(fs.readFileSync(path.join(folder, ".gitignore"), "utf8"), "*\n"); assert.ok(!fs.existsSync(path.join(h.work, ".opencode", "handoff")));
    fs.mkdirSync(folder, { recursive: true }); fs.writeFileSync(path.join(folder, `${h.sid}.md`), "notes"); await h.step(117000);
    assert.equal(h.calls.compact.length, 1); await h.hook(); assert.match(h.buildArgs()[0], /--notes-dir \.opencode\/mine\/deep /); assert.ok(!h.has("settings_invalid")); h.stop();
  },
  async "N6a: the patience limit clears the request while the hook builds: the rollover is still logged truthfully"() {
    const h = await harness({ compaction_patience_ms: 200, build_timeout_ms: 5000 }, { build: "sleep 1; echo SUMMARY" });
    await h.step(116000); h.notes(); await h.step(117000); assert.equal(h.calls.compact.length, 1);
    const pending = h.hook(); await sleep(400); await h.step(117000);         // patience (200 ms) is over: s.rolling is cleared mid-build
    assert.ok(h.has("compaction_failed", /did not end in time/));
    const event = await pending;
    assert.equal(event.result && event.result.summary.trim(), "SUMMARY"); assert.ok(h.has("summary_supplied"));
    assert.ok(!h.logRows().some((r) => r.event === "error"), JSON.stringify(h.logRows().filter((r) => r.event === "error")));
    await h.emit("session.compaction.ended", { reason: "manual" }); assert.ok(h.has("rolled"), "a rollover that happened is logged as one"); h.stop();
  },
  async "N6b: a decision log over 5 MB is cut to its last 1 MB at start, on a whole line"() {
    const dir = path.join(root, "bigLog"), home = path.join(dir, "home"); fs.mkdirSync(home, { recursive: true });
    const line = JSON.stringify({ t: 1, session: "ses_x", event: "asked", reason: "x".repeat(80) }) + "\n";
    fs.writeFileSync(path.join(home, "rollover.log"), line.repeat(Math.ceil(5.5 * 1024 * 1024 / line.length)) + JSON.stringify({ t: 2, event: "last" }) + "\n");
    const h = await harness({}, { from: { dir, main: { sid: "ses_big", work: path.join(dir, "w") } } });
    const size = fs.statSync(path.join(home, "rollover.log")).size; assert.ok(size < 1100000 && size > 900000, String(size));
    const rows = h.logRows(); assert.equal(rows[0].event, "asked"); assert.ok(rows.some((r) => r.event === "last")); assert.equal(rows[rows.length - 1].event, "log_cut");
    h.stop();
    const small = await harness({}); await small.step(120000); const before = fs.statSync(path.join(small.home, "rollover.log")).size;
    small.stop(); assert.ok(before < 5000, "a small log is left alone"); assert.ok(!small.has("log_cut"));
  },
  async "the 24 hour memory sweep drops an idle session, keeps a busy one, and the state comes back from its file"() {
    const h = await harness({}); const b = h.add("ses_busy");
    await h.step(100000); await h.step(101000);
    const real = Date.now; Date.now = () => real() + 25 * 3600 * 1000;       // the clock, a day on
    try { await h.step(100000, "tool-calls", b.sid); h.dispose.sweep(); } finally { Date.now = real; }
    const gets = (sid) => h.calls.get.filter((x) => x === sid).length;
    assert.equal(gets(h.sid), 1); assert.equal(gets(b.sid), 1);
    await h.step(102000); await h.step(100500, "tool-calls", b.sid);
    assert.equal(gets(h.sid), 2, "the idle session was dropped from memory and looked up again"); assert.equal(gets(b.sid), 1, "the busy one was kept");
    assert.equal(h.state().steps, 3, "its saved state was read back, not lost"); h.stop();
  },
  async "a torn or empty state file is ignored and replaced by a whole one"() {
    for (const torn of ['{"lastEnd": 12, "notesC', "", "[1,2", "null"]) {
      const dir = path.join(root, "torn" + n++), home = path.join(dir, "home"); fs.mkdirSync(path.join(home, "state"), { recursive: true });
      fs.writeFileSync(path.join(home, "state", "ses_torn.json"), torn);
      const h = await harness({}, { from: { dir, main: { sid: "ses_torn", work: path.join(dir, "w") } } });
      await h.step(120000); await h.step(111000);
      assert.equal(h.calls.synthetic.length, 1, JSON.stringify(torn)); assert.ok(!h.logRows().some((r) => r.event === "error"));
      assert.equal(h.state().steps, 2); assert.deepEqual(fs.readdirSync(path.join(home, "state")), ["ses_torn.json"]); h.stop();
    }
  },
  async "a notes file older than the last rollover is not coverage, even when the saved state says it covers"() {
    const h = await harness({}); await h.roll(); h.stop();                           // a rollover: lastRollover is now
    const f = path.join(h.home, "state", `${h.sid}.json`); const v = JSON.parse(fs.readFileSync(f, "utf8"));
    fs.writeFileSync(f, JSON.stringify({ ...v, notesCtx: 100000, notesMtime: v.lastRollover - 5000, steps: 3 }));   // as if reset forgot the notes
    const again = await harness({}, { from: h }); await again.step(125000);
    assert.equal(again.calls.compact.length, 0); assert.ok(again.calls.synthetic.length >= 1, "asked for new notes instead"); again.stop();
  },
};

(async () => {
  let failed = 0;
  for (const [name, fn] of Object.entries(cases)) {
    try { await fn(); console.log("ok   " + name); } catch (e) { failed += 1; console.log("FAIL " + name + ": " + (e && e.stack ? e.stack.split("\n").slice(0, 4).join(" | ") : e)); }
  }
  fs.rmSync(root, { recursive: true, force: true });
  console.log(failed ? `${failed} failed` : `all ${Object.keys(cases).length} passed`);
  process.exit(failed ? 1 : 0);
})();
