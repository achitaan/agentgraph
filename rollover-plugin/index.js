// Rollover plugin (OpenCode 2.3.x server plugin, V2 API: module.exports = {id, setup(ctx)}).
// A worker lane that has grown to ask_at (115k tokens of context) is asked to write handoff notes; at the first step end at or above
// that where the notes cover the context, the plugin starts a compaction and answers OpenCode's `compaction` hook with a summary
// built by `agentgraph handoff build` (the lane's notes plus what code knows), so no model call is made. The hook also answers a
// compaction OpenCode starts itself (automatic, at about 150k) when the same freshness rule holds (notes written after the last ask
// and covering the context), and a second compaction that arrives before any step has ended since the last rollover gets the same
// summary again. In every other case the plugin sets nothing and OpenCode's normal model summary is made. Root chats and the
// orchestrating agents are never touched.
// Files: the lane's notes in <session folder>/.opencode/handoff/<session>.md (with a .gitignore of `*`); everything else (state,
// decision log) in one folder outside any project: <data home>/agentgraph/rollover/. Nothing is written for a session this plugin
// does not act on. Several OpenCode processes (desktop server, CLI runs) can work on one session: before every decision the
// state file is compared with what this process last wrote or read, and reloaded when another process changed it. No message text is ever logged. Nothing here throws into OpenCode. Settings: rollover.json beside this file.
// See docs/ROLLOVER.md.
"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const cp = require("node:child_process");

const DEFAULTS = {
  enabled: true,
  ask_at: 115000,        // tokens of context: ask the lane to write its notes; the rollover starts at the first step end at or above this where the notes cover the context
  hard_at: 160000,       // tokens of context: notes still do not cover it, so give up and log why (OpenCode's own compaction is the backstop)
  workers_only: true,    // act only on sessions that have a parent session
  skip_agents: ["orchestrator", "build", "plan", "side-chat", "council"], // orchestrating agents: never touched (the team leads, hubs and council leads of the structure folder too)
  skip_leads: true,      // also leave alone the agents `agentgraph handoff leads` names (hubs, council leads, team leads of the structure folder; read once at start)
  only_agents: [],       // when not empty, act only on these agents (an unknown agent is then left alone)
  cache_seconds: 300,    // a session idle for longer than this has lost its prompt cache
  pause_min: 60000,      // after such a pause, roll over if the context is over this
  notes_gap: 10000,      // the notes cover the context when it grew at most this many tokens since they were written (well under one reading step; the roll and the pause trigger)
  hook_growth: 15000,    // when OpenCode asks for the summary, the context may have grown this much since the plugin requested the compaction
  reask_steps: 2,        // ask again at most once per this many steps ...
  reask_growth: 10000,   // ... and per this many tokens of growth
  max_asks: 4,           // most requests for notes between two rollovers
  notes_dir: ".opencode/handoff", // under the session's folder; a relative folder under .opencode/, no ".."
  compaction_patience_ms: 180000, // a requested compaction that has not ended after this long is counted as failed
  structure_dir: null,   // absolute path of the structure folder (team leads are read from it); found beside bin/agentgraph or in the personal folder when not set
  build_timeout_ms: 15000,
  agentgraph_bin: null,  // path of bin/agentgraph; found beside the plugin or in the config folder when not set
  state_dir: null,       // absolute folder for state and log; default <data home>/agentgraph/rollover (env AGENTGRAPH_ROLLOVER_HOME wins)
  families: {},          // per model family (opus, sonnet, other) overrides of the numbers above
};
// keys that are numbers, with their allowed range; thresholds must be at least 20,000
const RANGES = {
  ask_at: [20000, 5e6], hard_at: [20000, 5e6], cache_seconds: [0, 86400], pause_min: [0, 5e6], notes_gap: [1000, 1e6],
  reask_steps: [1, 50], reask_growth: [1000, 1e6], max_asks: [1, 20], build_timeout_ms: [1000, 120000], hook_growth: [1000, 1e6], compaction_patience_ms: [100, 3600000],
};
const BOOLS = new Set(["enabled", "workers_only", "skip_leads"]);
const LISTS = new Set(["skip_agents", "only_agents"]);
const FAMILIES = ["opus", "sonnet", "other"];
const SESSION_ID = /^[A-Za-z0-9_-]{1,100}$/;   // the same pattern agentgraph/handoff.py uses
const SOURCE = "agentgraph-rollover";
const WATCHED = new Set(["session.step.started", "session.step.ended", "session.execution.started", "session.execution.succeeded",
  "session.execution.interrupted", "session.execution.failed", "session.compaction.started", "session.compaction.ended", "session.compaction.failed",
  "session.deleted"]);
const DAY = 86400000;
const LOG_MAX = 5 * 1024 * 1024, LOG_KEEP = 1024 * 1024;   // a decision log over 5 MB is cut to its last 1 MB at start
const LOOKUP_RETRY = 60000;           // a failed session lookup is not repeated sooner than this
const WAIT_STEPS = 5;                 // steps to wait after the first failed compaction; doubled after each one
const GIVE_UP_AFTER = 3;              // failures in a row after which the session is left alone until a compaction ends well

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const num = (v) => (isNum(v) ? v : 0);
const family = (modelID) => { const m = String(modelID || "").toLowerCase(); return m.includes("opus") ? "opus" : m.includes("sonnet") ? "sonnet" : "other"; };
const contextOf = (t) => (isObj(t) ? num(t.input) + (isObj(t.cache) ? num(t.cache.read) + num(t.cache.write) : 0) : 0);
const shortError = (e) => String((e && e.message) || e).replace(/\s+/g, " ").slice(0, 200);
const validId = (v) => typeof v === "string" && SESSION_ID.test(v);
const orderOk = (c) => c.ask_at < c.hard_at;

// a notes folder must be a folder UNDER .opencode/ of the session's folder (it gets a .gitignore of `*`, so ".", "src" or ".opencode" itself would hide real work from git);
// returns it normalized, or null
function notesDirOk(v) {
  if (typeof v !== "string" || !v || path.isAbsolute(v) || path.win32.isAbsolute(v)) return null;
  const parts = v.split(/[\\/]+/).filter((x) => x && x !== ".");
  if (parts.length < 2 || parts[0] !== ".opencode" || parts.includes("..")) return null;
  return parts.join("/");
}

// the valid keys of a settings object; every rejected key adds a line to `problems`
function validated(raw, label, problems) {
  const out = {};
  if (!isObj(raw)) { problems.push(`${label}: not an object`); return out; }
  for (const [k, v] of Object.entries(raw)) {
    if (k in RANGES) {
      const [lo, hi] = RANGES[k];
      if (isNum(v) && v >= lo && v <= hi) out[k] = v; else problems.push(`${label}.${k}: must be a number from ${lo} to ${hi}`);
    } else if (BOOLS.has(k)) {
      if (typeof v === "boolean") out[k] = v; else problems.push(`${label}.${k}: must be true or false`);
    } else if (LISTS.has(k)) {
      if (Array.isArray(v) && v.every((x) => typeof x === "string")) out[k] = v; else problems.push(`${label}.${k}: must be a list of names`);
    } else if (k === "roll_at") {      // the key of earlier versions: the rollover no longer has a threshold of its own
      problems.push(`${label}.roll_at: no longer used (the rollover starts where the notes cover the context at or above ask_at); ignored`);
    } else if (k === "notes_dir") {
      const dir = notesDirOk(v);
      if (dir) out[k] = dir; else problems.push(`${label}.${k}: must be a relative folder under .opencode/ without ..`);
    } else if (k === "agentgraph_bin" || k === "state_dir" || k === "structure_dir") {
      if (v === null || (typeof v === "string" && path.isAbsolute(v))) out[k] = v; else problems.push(`${label}.${k}: must be an absolute path`);
    }
  }
  return out;
}

// the settings in force: defaults plus valid keys; {cfg, problems}
function resolveSettings(raw) {
  const problems = [];
  const top = validated(raw, "rollover.json", problems);
  const cfg = { ...DEFAULTS, ...top, families: {} };
  if (!orderOk(cfg)) {
    problems.push("ask_at < hard_at does not hold: the two defaults are used");
    cfg.ask_at = DEFAULTS.ask_at; cfg.hard_at = DEFAULTS.hard_at;
  }
  const fam = isObj(raw) && raw.families !== undefined ? raw.families : {};
  if (!isObj(fam)) problems.push("families: not an object");
  else {
    for (const [name, ov] of Object.entries(fam)) {
      if (!FAMILIES.includes(name)) { problems.push(`families.${name}: unknown family`); continue; }
      const own = validated(ov, `families.${name}`, problems);
      const eff = { ...cfg, ...own };
      if (!orderOk(eff)) { problems.push(`families.${name}: ask_at < hard_at does not hold; override ignored`); continue; }
      cfg.families[name] = eff;
    }
  }
  return { cfg, problems };
}

function askText(notesFile) {
  return [
    "Checkpoint from the rollover plugin (automatic, not from the user). Your context is large and will be replaced by a short summary right after your notes are saved: anything you do after writing them is lost.",
    `Finish the tool call you are in, then write your handoff notes NOW to ${notesFile} with the write tool (create the folder if it is missing, overwrite the file). Read nothing and start nothing else first. This file is allowed even if your brief limits where you may write. The folder holds a .gitignore of \`*\` so the notes are never committed; leave it alone.`,
    "Put in it: (1) the task as you were given it, with every requirement, number and name that matters; (2) the decisions you made and why, including any name, code or choice that exists only in this conversation; (3) what you tried that failed or must not be repeated; (4) what is done, including what you have already read and what you learned from it, so that nothing has to be read again; (5) the exact next step, in the last lines of the file; (6) the files that matter and what each holds.",
    "Be concrete and short: after the reset these notes, the task text and the last few messages are all you will have. Do not reply with a summary or wait for an answer; once the notes are saved, carry on with the task from your next step.",
  ].join("\n\n");
}

module.exports = {
  id: "agentgraph-rollover",
  async setup(ctx) {
    const here = __dirname;
    const settingsFile = path.join(here, "rollover.json");
    let cache = { mtime: -2, cfg: resolveSettings({}).cfg };
    const settings = () => {
      try {
        let m = -1;
        try { m = fs.statSync(settingsFile).mtimeMs; } catch (e) { if (!e || e.code !== "ENOENT") throw e; }
        if (m !== cache.mtime) {
          if (m < 0) cache = { mtime: m, cfg: resolveSettings({}).cfg };
          else {
            let raw, problems = [];
            try { raw = JSON.parse(fs.readFileSync(settingsFile, "utf8")); } catch (e) { raw = {}; problems.push("rollover.json does not parse: " + shortError(e)); }
            const r = resolveSettings(raw);
            cache = { mtime: m, cfg: r.cfg };
            problems = problems.concat(r.problems);
            if (problems.length) log(null, "settings_invalid", { reason: problems.join("; ").slice(0, 600) + "; defaults are used for these" });
          }
        }
      } catch { /* an unreadable settings file leaves the last settings in force */ }
      return cache.cfg;
    };
    const active = () => settings().enabled !== false && String(process.env.AGENTGRAPH_ROLLOVER || "").toLowerCase() !== "off";
    const limits = (s) => settings().families[s.family] || settings();

    // ---- the plugin's own folder: state/<session>.json and rollover.log, outside every project ----
    const homeDir = () => {
      const own = process.env.AGENTGRAPH_ROLLOVER_HOME;
      if (own && path.isAbsolute(own)) return own;
      const cfgDir = settings().state_dir;
      if (cfgDir) return cfgDir;
      const xdg = process.env.XDG_DATA_HOME;
      return path.join(xdg && path.isAbsolute(xdg) ? xdg : path.join(os.homedir(), ".local", "share"), "agentgraph", "rollover");
    };
    const stateFile = (sid) => path.join(homeDir(), "state", `${sid}.json`);
    const log = (s, event, extra) => {
      try {
        const dir = homeDir();
        fs.mkdirSync(dir, { recursive: true });
        fs.appendFileSync(path.join(dir, "rollover.log"),
          JSON.stringify({ t: Date.now(), session: s ? s.sid : null, event, context: s && s.lastCtx ? s.lastCtx : null, agent: s ? s.agent : null, ...extra }) + "\n");
      } catch { /* the log is best effort */ }
    };
    // what the state file looked like when this process last wrote or read it; another process that changes it changes this
    const stamp = (sid) => { try { const st = fs.statSync(stateFile(sid)); return `${st.ino}:${st.size}:${st.mtimeMs}`; } catch { return ""; } };
    const loadSaved = (s) => {
      s.stamp = stamp(s.sid);     // taken before the read: a write that lands in between is seen at the next event
      try {
        const v = JSON.parse(fs.readFileSync(stateFile(s.sid), "utf8"));
        s.lastEnd = num(v.lastEnd); s.lastCtx = num(v.lastCtx); s.lastRollover = num(v.lastRollover); s.steps = num(v.steps);
        s.notesMtime = num(v.notesMtime); s.notesCtx = v.notesCtx == null ? null : num(v.notesCtx);
        s.asked = isObj(v.asked) ? { at: num(v.asked.at), ctx: num(v.asked.ctx), step: num(v.asked.step) } : null;
        s.firstAskAt = num(v.firstAskAt); s.askCount = num(v.askCount);
        s.failures = num(v.failures); s.waitSteps = num(v.waitSteps); s.givenUp = v.givenUp === true;
      } catch { /* none yet */ }
    };
    const saveState = (s) => {
      if (!s.scoped) return;
      const file = stateFile(s.sid), tmp = `${file}.${process.pid}.tmp`;
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(tmp, JSON.stringify({ lastEnd: s.lastEnd, lastCtx: s.lastCtx, lastRollover: s.lastRollover, steps: s.steps, notesMtime: s.notesMtime,
          notesCtx: s.notesCtx, asked: s.asked, firstAskAt: s.firstAskAt, askCount: s.askCount, failures: s.failures, waitSteps: s.waitSteps, givenUp: s.givenUp }));
        fs.renameSync(tmp, file);     // a reader never sees half a file
        s.stamp = stamp(s.sid);
      } catch { try { fs.rmSync(tmp, { force: true }); } catch { /* best effort */ } }
    };
    // before every decision: the first time a session is in scope load its state; later, reload it (and drop what only this process
    // remembered) when another process has written it since, e.g. a CLI run that rolled the session over
    const syncState = (s) => {
      if (!s.scoped) { s.scoped = true; loadSaved(s); return; }
      const now = stamp(s.sid);
      if (!now || now === s.stamp) return;
      loadSaved(s);
      s.rolling = null; s.supplied = null; s.ownRoll = null; s.lastSummary = null; s.fellBack = false; s.skipped.clear();
      log(s, "state_reloaded", { reason: "another process changed this session's state" });
    };

    const sessions = new Map();
    const state = (sid) => {
      let s = sessions.get(sid);
      if (!s) {
        s = { sid, info: null, infoFailAt: 0, scoped: false, agent: null, model: null, family: "other", lastCtx: 0, lastEnd: 0, touched: Date.now(), steps: 0,
          asked: null, firstAskAt: 0, askCount: 0, rolling: null, supplied: null, ownRoll: null, lastStart: 0, lastSummary: null, noTask: false, stamp: "", fellBack: false, lastRollover: 0, notesMtime: 0, notesCtx: null,
          failures: 0, waitSteps: 0, givenUp: false, skipped: new Set() };
        sessions.set(sid, s);
      }
      s.touched = Date.now();
      return s;
    };
    const skipOnce = (s, reason) => { if (!s.skipped.has(reason)) { s.skipped.add(reason); log(s, "skipped", { reason }); } };

    // ---- scope: a session this plugin acts on. Nothing is written about any other. ----
    const dirOf = (s) => { const d = s.info && s.info.location && s.info.location.directory; return typeof d === "string" && path.isAbsolute(d) ? d : null; };
    const notesFolder = (s) => path.join(dirOf(s), settings().notes_dir);
    const notesFile = (s) => path.join(notesFolder(s), `${s.sid}.md`);
    // the agents that run child sessions of their own, read once per process by `agentgraph handoff leads` (never at an event)
    let leadNames = new Set(), leadsRead = Promise.resolve();   // filled in below, once the command can be found
    // the scope rules on what is known now (no lookup): also called by the hook, so a settings edit that moves a session out of scope stops the summary too
    const scopeOk = (s) => {
      if (!s.info) return false;
      const cfg = settings();
      if (cfg.workers_only !== false && !s.info.parentID) return false;
      const skip = cfg.skip_agents.map((a) => a.toLowerCase()), only = cfg.only_agents.map((a) => a.toLowerCase());
      if (skip.length || only.length || leadNames.size) {
        if (!s.agent) return false;     // an unknown agent is left alone when agents are named; looked at again at the next event
        const a = s.agent.toLowerCase();
        if (skip.includes(a) || leadNames.has(a) || (only.length && !only.includes(a))) return false;
      }
      return !!dirOf(s);
    };
    const inScope = async (s) => {
      await leadsRead;
      if (!s.info) {
        if (s.infoFailAt && Date.now() - s.infoFailAt < LOOKUP_RETRY) return false;
        let info = null;
        try { info = await ctx.session.get({ sessionID: s.sid }); } catch (e) { s.infoFailAt = Date.now(); log(s, "error", { reason: "session lookup: " + shortError(e) }); return false; }
        if (!isObj(info)) { s.infoFailAt = Date.now(); return false; }
        s.info = info;
        if (!s.agent && typeof info.agent === "string") s.agent = info.agent;
      }
      if (!scopeOk(s)) return false;
      syncState(s);
      return true;
    };

    // ---- notes: where they are, whether they cover the context ----
    // the file's mtime in whole milliseconds, like Date.now(): its fraction would make notes written in the same millisecond as an event look newer than it
    const readNotes = (s) => { try { const st = fs.statSync(notesFile(s)); return st.size > 0 ? Math.floor(st.mtimeMs) : 0; } catch { return 0; } };
    // one rule for the roll and the pause: the notes were written after the previous step ended and the context has grown past them by at most `notes_gap`
    const covered = (s, lim) => s.notesCtx != null && s.notesMtime > s.lastRollover && s.lastCtx - s.notesCtx <= num(lim.notes_gap);
    const notWhy = (s, lim) => (s.notesCtx == null || s.notesMtime <= s.lastRollover ? "no notes written since the last rollover"
      : `notes are ${s.lastCtx - s.notesCtx} tokens behind the context (limit ${lim.notes_gap})`);
    const ensureFolder = (s) => {
      const folder = notesFolder(s);
      fs.mkdirSync(folder, { recursive: true });
      try { fs.writeFileSync(path.join(folder, ".gitignore"), "*\n", { flag: "wx" }); } catch (e) { if (!e || e.code !== "EEXIST") throw e; }
    };

    // ---- the summary: `agentgraph handoff build` as a child process ----
    let buildBroken = null;   // set when the command cannot start at all (e.g. python3 older than 3.11): no more asks or rolls in this process
    const agentgraphBin = () => {
      const cfg = settings();
      const found = [cfg.agentgraph_bin, process.env.AGENTGRAPH_BIN, path.resolve(here, "..", "bin", "agentgraph"),
        path.resolve(here, "..", "..", "orchestration", "bin", "agentgraph")].filter(Boolean).find((p) => { try { return fs.statSync(p).isFile(); } catch { return false; } });
      return found || null;
    };
    const readLeads = () => new Promise((resolve) => {
      if (settings().skip_leads === false) return resolve();
      const fail = (why) => { log(null, "leads_unreadable", { reason: why + "; only skip_agents is used to leave orchestrating agents alone" }); resolve(); };
      const bin = agentgraphBin();
      if (!bin) return fail("agentgraph command not found");
      const args = ["handoff", "leads"];
      if (settings().structure_dir) args.push("--structure-dir", settings().structure_dir);
      try {
        cp.execFile(bin, args, { timeout: 15000, maxBuffer: 1024 * 1024, encoding: "utf8", windowsHide: true }, (err, stdout) => {
          if (err) return fail(err.killed ? "handoff leads timed out" : "handoff leads failed (exit " + err.code + ")");
          leadNames = new Set(String(stdout).split("\n").map((x) => x.trim().toLowerCase()).filter((x) => /^[a-z0-9_.-]{1,100}$/.test(x)));
          resolve();
        });
      } catch (e) { fail("handoff leads did not start: " + shortError(e)); }
    });
    leadsRead = readLeads();
    const newerThan = (s) => Math.max(s.lastRollover || 0, s.firstAskAt || 0);   // notes older than this are the last cycle's, or older than the ask
    // the summary text, or {fail: reason}; never rejects
    const build = (s) => new Promise((resolve) => {
      const bin = agentgraphBin();
      if (!bin) return resolve({ fail: "agentgraph command not found", broken: true });
      const args = ["handoff", "build", "--session", s.sid, "--dir", dirOf(s), "--notes-dir", settings().notes_dir, "--newer-than", String(newerThan(s))];
      try {
        cp.execFile(bin, args, { timeout: num(settings().build_timeout_ms) || 15000, maxBuffer: 8 * 1024 * 1024, encoding: "utf8", windowsHide: true }, (err, stdout) => {
          if (!err) return stdout && stdout.trim() ? resolve({ text: stdout }) : resolve({ fail: "empty summary" });
          if (err.killed) return resolve({ fail: "build timed out" });
          if (err.code === 3) return resolve({ fail: "notes missing or older than the last rollover or request" });
          if (err.code === 4) return resolve({ fail: "the task could not be read from the database", noTask: true });
          if (err.code === 2 || typeof err.code === "string") return resolve({ fail: "build could not start (exit " + err.code + ")", broken: true });
          resolve({ fail: "build failed, exit " + err.code });
        });
      } catch (e) { resolve({ fail: "build did not start: " + shortError(e), broken: true }); }
    });

    // ---- failures of a compaction: wait (5, 10, 20 ... steps), and after 3 in a row stop until one ends well ----
    const noteFailure = (s, why) => {
      s.failures += 1;
      s.waitSteps = WAIT_STEPS * 2 ** (s.failures - 1);
      log(s, "compaction_failed", { reason: why, failures: s.failures, wait_steps: s.waitSteps });
      if (s.failures >= GIVE_UP_AFTER && !s.givenUp) { s.givenUp = true; log(s, "gave_up", { reason: `${s.failures} failed compactions in a row; left alone until a compaction ends well` }); }
      saveState(s);
    };

    const requestCompaction = (s, reason) => {
      s.rolling = { at: Date.now(), reason, ctx: s.lastCtx, hookUsed: false };
      let p;
      try { p = ctx.session.compact({ sessionID: s.sid }); } catch (e) { p = Promise.reject(e); }
      // `compact` returns when the compaction is queued, not when it ends (a probe measured 2 ms); never hold the session's queue on it
      Promise.resolve(p).then(() => log(s, "roll_requested", { reason }), (e) => { s.rolling = null; noteFailure(s, "compact: " + shortError(e)); });
    };

    // Whether the notes are fresh enough for a compaction the plugin did not request (OpenCode's own automatic one): null when they are,
    // else the reason they are not. OpenCode's compaction can fire before the plugin has seen the end of the step that wrote the notes
    // (a measured race), so the file is read here, not taken from the state of the last step end.
    const ownStale = (s, lim) => {
      if (buildBroken) return "the summary builder is unavailable in this process";
      if (s.noTask) return "the task could not be read from the database (this session is left alone in this process)";
      if (!s.lastEnd || !s.steps) return "no step seen by the plugin yet";
      const m = readNotes(s);
      if (!m) return "no notes file";
      if (m <= s.lastRollover) return "notes older than the last rollover";
      if (s.asked && m <= s.asked.at) return "notes older than the last ask";
      if (s.lastStart > s.lastEnd && m < s.lastStart) return "notes written before the step that is running began (its growth is not covered)";
      if (m > s.lastEnd) return null;                  // written after the last step end the plugin saw: in the step that is ending now
      if (s.notesMtime === m && s.notesCtx != null) {
        const behind = s.lastCtx - s.notesCtx;
        return behind <= num(lim.notes_gap) ? null : `notes are ${behind} tokens behind the context (limit ${lim.notes_gap})`;
      }
      return "notes written before the last step ended and not counted as covering it";
    };

    // the last supplied summary can be given again to a compaction that arrives before any step has ended since the rollover, shortly after
    // it (the queued second request) and only while the notes file is the one it was built from
    const reusable = (s) => !!s.lastSummary && s.steps > 0 && s.lastCtx === 0 && Date.now() - s.lastRollover <= num(settings().compaction_patience_ms) && readNotes(s) === s.notesMtime;

    // ---- the compaction hook: our summary for a compaction we requested, and for one OpenCode starts when the same freshness rule holds ----
    await ctx.session.hook("compaction", async (event) => {
      let s;
      try {
        if (!active() || !isObj(event) || !validId(event.sessionID)) return;
        s = sessions.get(event.sessionID);
        if (!s || !s.scoped || !scopeOk(s)) return;   // scope is checked again: a settings edit may have moved the session out of it
        if (process.env.AGENTGRAPH_ROLLOVER_DEBUG) log(s, "hook_seen", { keys: Object.keys(event).join(","), rolling: !!s.rolling });
        syncState(s);                                     // another process may have rolled this session over since
        let roll = s.rolling;                           // held here: the 180 s limit or an event can clear s.rolling while the build runs
        const lim = limits(s);
        let own = false;
        if (!roll && reusable(s)) {
          // a second compaction before any step has ended since the last rollover (e.g. the plugin's request queued behind OpenCode's own): the
          // same summary again, with no build. The model summary of [summary + a few messages] would only paraphrase it.
          roll = s.rolling = { at: Date.now(), reason: "opencode compaction, summary again", ctx: 0, hookUsed: true, own: true };
          event.result = { summary: s.lastSummary.text }; s.supplied = roll;
          log(s, "own_compaction", { reason: "own-compaction: supplied, the last summary again (no step has ended since the last rollover)", chars: s.lastSummary.text.length });
          return;
        }
        if (!roll) {
          // not ours: OpenCode's own automatic compaction (or a manual one). Supplied only when the notes are fresh; else its normal model summary
          const why = ownStale(s, lim);
          if (why) { log(s, "own_compaction", { reason: "own-compaction: left, " + why + "; a normal model summary is made" }); return; }
          own = true;
          // held as the pending request, so that the step end that follows does not request a second compaction (a second, small model summary)
          roll = s.rolling = { at: Date.now(), reason: "opencode compaction", ctx: s.lastCtx, hookUsed: false, own: true };
        } else {
          // the request was covered; check again, allowing the context one step of growth since (the compaction runs at a step boundary)
          const grew = s.lastCtx - roll.ctx;
          if (s.notesCtx == null || s.notesMtime <= s.lastRollover) { log(s, "fell_back", { reason: "no notes written since the last rollover; a normal model summary is made" }); return; }
          if (grew > num(lim.hook_growth)) { log(s, "fell_back", { reason: `the context grew ${grew} tokens since the request (limit ${lim.hook_growth}); a normal model summary is made` }); return; }
        }
        const got = await build(s);
        if (got.text) {
          event.result = { summary: got.text }; roll.hookUsed = true; s.supplied = roll;
          s.lastSummary = { text: got.text, at: Date.now() };
          s.ownRoll = { ctx: s.lastCtx };      // the step end that arrives late (after this compaction) belongs to the replaced context, whoever asked for the compaction
          if (own) { log(s, "own_compaction", { reason: "own-compaction: supplied", chars: got.text.length }); }
          else log(s, "summary_supplied", { chars: got.text.length, reason: roll.reason });
        } else {
          if (own) { if (s.rolling === roll) s.rolling = null; s.ownRoll = { ctx: s.lastCtx }; log(s, "own_compaction", { reason: "own-compaction: left, " + got.fail + "; a normal model summary is made" }); }
          else log(s, "fell_back", { reason: got.fail });
          if (got.noTask && !s.noTask) { s.noTask = true; log(s, "task_unreadable", { reason: "the task could not be read from the database; the plugin asks for no more notes and requests no more compactions for this session in this process" }); }
          if (got.broken && !buildBroken) { buildBroken = got.fail; log(s, "build_unavailable", { reason: got.fail + "; the plugin asks for no more notes in this process (is python3 older than 3.11, or the path wrong?)" }); }
        }
      } catch (e) { try { if (s && s.scoped) log(s, "error", { reason: "hook: " + shortError(e) }); } catch { /* nothing more to do */ } }
    });

    // ---- events, one session at a time ----
    const onStepStarted = (s, d) => {
      s.lastStart = Date.now();
      if (typeof d.agent === "string" && d.agent) s.agent = d.agent;
      if (isObj(d.model) && d.model.id) { s.model = d.model.id; s.family = family(d.model.id); }
    };

    const maybeAsk = async (s, lim, turnContinues) => {
      const ctxNow = s.lastCtx;
      const due = !s.asked || (s.steps - s.asked.step >= num(lim.reask_steps) && ctxNow - s.asked.ctx >= num(lim.reask_growth));
      if (!due) return;
      if (s.askCount >= num(lim.max_asks)) { skipOnce(s, `asked ${s.askCount} times since the last rollover; not asking again`); return; }
      if (!turnContinues) { skipOnce(s, "turn ended; will ask on a later step"); return; }
      s.askCount += 1;
      s.asked = { at: Date.now(), ctx: ctxNow, step: s.steps };
      try {
        ensureFolder(s);
        await ctx.session.synthetic({ sessionID: s.sid, text: askText(notesFile(s)), description: "rollover: write handoff notes", metadata: { source: SOURCE }, delivery: "steer" });
        if (!s.firstAskAt) s.firstAskAt = s.asked.at;
        log(s, "asked", { notes: path.basename(notesFile(s)), reason: notWhy(s, lim), count: s.askCount });
      } catch (e) { log(s, "error", { reason: "ask: " + shortError(e) }); }
    };

    const onStepEnded = async (s, d) => {
      if (!(await inScope(s))) return;                       // loads the saved state the first time
      if (s.ownRoll) {
        // OpenCode's compaction was answered by the hook before this step's end reached the plugin: that step belongs to the context that was
        // replaced (its size is at least the last one seen). Counting it would ask for notes on a context that is already small.
        const r = s.ownRoll; s.ownRoll = null;
        if (contextOf(d.tokens) >= r.ctx) { log(s, "step_ignored", { reason: "the step ended after OpenCode's compaction was answered with the notes" }); return; }
      }
      const prevEnd = s.lastEnd;
      s.lastCtx = contextOf(d.tokens);
      s.lastEnd = Date.now();
      s.steps += 1;
      const lim = limits(s);
      // the notes cover everything up to this step only if they were written after the previous step ended (never merely because they exist)
      const m = readNotes(s);
      if (m && m !== s.notesMtime) { s.notesMtime = m; if (prevEnd > 0 && m > prevEnd) s.notesCtx = s.lastCtx; }
      if (s.rolling && Date.now() - s.rolling.at > num(settings().compaction_patience_ms)) { s.rolling = null; noteFailure(s, "the requested compaction did not end in time"); }
      saveState(s);
      if (s.rolling || s.givenUp || buildBroken || s.noTask) return;
      if (s.waitSteps > 0) { s.waitSteps -= 1; return; }
      const ctxNow = s.lastCtx, isCovered = covered(s, lim);
      if (isCovered && ctxNow >= num(lim.ask_at)) { requestCompaction(s, "context " + ctxNow); return; }
      if (ctxNow >= num(lim.hard_at) && !isCovered && !s.fellBack) { s.fellBack = true; log(s, "fell_back", { reason: notWhy(s, lim) + " at hard_at; OpenCode's own compaction will run, with a model summary" }); }
      if (ctxNow >= num(lim.ask_at) && !isCovered) await maybeAsk(s, lim, d.finish === "tool-calls");
    };

    // The pause trigger: a new turn that starts after the prompt cache has gone cold (`cache_seconds` since the last step) on a big
    // context whose notes cover it is rolled over before its first model step. `session.execution.started` fires before the step.
    const onTurnStarted = async (s) => {
      if (!(await inScope(s))) return;
      if (!s.lastEnd || s.rolling || s.givenUp || buildBroken || s.noTask || s.waitSteps > 0) return;
      const lim = limits(s);
      if (!num(lim.cache_seconds) || Date.now() - s.lastEnd <= num(lim.cache_seconds) * 1000 || s.lastCtx < num(lim.pause_min)) return;
      if (!covered(s, lim)) { skipOnce(s, "pause: " + notWhy(s, lim)); return; }
      requestCompaction(s, "pause");
    };

    const resetCycle = (s) => {
      s.rolling = null; s.supplied = null; s.asked = null; s.firstAskAt = 0; s.askCount = 0; s.fellBack = false; s.skipped.clear();
      s.failures = 0; s.waitSteps = 0; s.givenUp = false;
      s.lastRollover = Date.now(); s.lastEnd = s.lastRollover; s.lastCtx = 0; s.notesCtx = null; s.notesMtime = readNotes(s);
    };
    const onCompactionEnded = async (s, d) => {
      if (!(await inScope(s))) return;
      const roll = s.rolling || s.supplied, ours = !!(roll && roll.hookUsed);
      if (!ours) s.lastSummary = null;
      log(s, ours ? "rolled" : "compacted", { cost: num(d.cost), tokens: isObj(d.tokens) ? contextOf(d.tokens) : 0, reason: roll ? roll.reason : (d.reason || "opencode") });
      resetCycle(s);
      saveState(s);
    };
    const onCompactionFailed = async (s, d) => {
      if (!(await inScope(s))) return;
      const kind = isObj(d.error) && typeof d.error.type === "string" ? d.error.type.slice(0, 60) : "unknown";   // never the message: it can carry prompt text
      s.rolling = null; s.supplied = null; s.ownRoll = null; s.lastSummary = null;   // nothing was replaced: the next compaction is a new one
      noteFailure(s, "compaction ended in failure (" + kind + ")");
    };

    const handle = async (ev) => {
      const s = state(ev.data.sessionID);
      switch (ev.type) {
        case "session.step.started": return onStepStarted(s, ev.data);
        case "session.step.ended": return onStepEnded(s, ev.data);
        case "session.execution.started": return onTurnStarted(s);
        case "session.compaction.ended": return onCompactionEnded(s, ev.data);
        case "session.compaction.failed": return onCompactionFailed(s, ev.data);
        case "session.compaction.started": if (s.scoped && !s.rolling) log(s, "compaction_started", { reason: ev.data.reason || "opencode" }); return;
        case "session.deleted": sessions.delete(s.sid); try { fs.rmSync(stateFile(s.sid), { force: true }); } catch { /* best effort */ } return;
        default:
      }
    };
    const chains = new Map();   // one queue per session: a slow lookup, ask or build of one session never holds up another
    const dispatch = (ev) => {
      if (!isObj(ev) || !WATCHED.has(ev.type) || !isObj(ev.data) || !validId(ev.data.sessionID) || !active()) return;
      const sid = ev.data.sessionID;
      const next = (chains.get(sid) || Promise.resolve()).then(() => handle(ev)).catch((e) => {
        try { const s = sessions.get(sid); if (s && s.scoped) log(s, "error", { reason: "event " + ev.type + ": " + shortError(e) }); } catch { /* ignore */ }
      });
      chains.set(sid, next);
      next.then(() => { if (chains.get(sid) === next) chains.delete(sid); });
    };

    // ---- housekeeping: sessions idle for a day leave memory; state files older than 30 days are deleted at start ----
    const sweep = () => { const cutoff = Date.now() - DAY; for (const [sid, s] of sessions) if (s.touched < cutoff && !chains.has(sid)) sessions.delete(sid); };
    try {
      const folder = path.join(homeDir(), "state");
      for (const name of fs.readdirSync(folder)) {
        if (!name.endsWith(".json") && !name.endsWith(".tmp")) continue;
        try { const f = path.join(folder, name); if (Date.now() - fs.statSync(f).mtimeMs > 30 * DAY) fs.rmSync(f, { force: true }); } catch { /* skip it */ }
      }
    } catch { /* no state folder yet */ }
    try {      // a decision log over 5 MB is cut to its last 1 MB (from a whole line) at start
      const file = path.join(homeDir(), "rollover.log");
      const size = fs.statSync(file).size;
      if (size > LOG_MAX) {
        const fd = fs.openSync(file, "r"), buf = Buffer.alloc(LOG_KEEP);
        try { fs.readSync(fd, buf, 0, LOG_KEEP, size - LOG_KEEP); } finally { fs.closeSync(fd); }
        const nl = buf.indexOf(10), kept = nl < 0 ? Buffer.alloc(0) : buf.subarray(nl + 1);
        const tmp = `${file}.${process.pid}.tmp`;
        fs.writeFileSync(tmp, kept); fs.renameSync(tmp, file);
        log(null, "log_cut", { reason: `rollover.log was ${size} bytes; the last ${kept.length} bytes were kept` });
      }
    } catch { /* no log yet, or it cannot be cut */ }
    const timer = setInterval(sweep, 3600000);
    if (timer.unref) timer.unref();

    const stop = new AbortController();
    void (async () => {
      try { for await (const ev of ctx.event.subscribe({ signal: stop.signal })) dispatch(ev); } catch { /* the subscription ended */ }
    })();
    const dispose = () => { stop.abort(); clearInterval(timer); };
    dispose.sweep = sweep;     // the hourly memory sweep, so a test can run it
    // resolves when the leads are read and every queued event has been handled (a test waits for this instead of a fixed time)
    dispose.idle = async () => { await leadsRead; while (chains.size) await Promise.all([...chains.values()]); await new Promise((r) => setImmediate(r)); };
    return dispose;
  },
};
