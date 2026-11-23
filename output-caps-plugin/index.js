// Output caps plugin (OpenCode 2.3.x server plugin, V2 API: module.exports = {id, setup(ctx)}).
// A tool result with more text than the cap (read: 40,000 characters, every other tool: 20,000) is cut before it enters the chat: the head
// and the tail are kept (about 70% / 30%), the cut falls on line boundaries, and ONE plain line in the gap says how much was left out and
// how to get it. File parts (images, PDFs), errors, results under the cap and the exempt tools (edit, write, question, ...) are never touched.
// Nothing here throws into OpenCode: on any surprise the result is left as it was and an `error` line (no text) goes to the log.
// The log (JSON lines: time, session, tool, original size, kept size, event) holds no text. Settings: output-caps.json beside this file.
// Off switch: AGENTGRAPH_OUTPUT_CAPS=off. See README.md.
"use strict";
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const DEFAULTS = {
  enabled: true,
  read_chars: 40000,     // most characters of text one `read` result may keep
  other_chars: 20000,    // the same for every other tool
  head_share: 0.7,       // share of the kept characters taken from the start (the rest from the end)
  exempt_tools: ["edit", "write", "question", "skill", "subagent", "task_status", "task_message", "task_reply", "task_cancel", "interview_submit_state"],
  log_dir: null,         // absolute folder for the log; default <data home>/agentgraph/output-caps (env AGENTGRAPH_OUTPUT_CAPS_HOME wins)
};
const RANGES = { read_chars: [1000, 5e6], other_chars: [1000, 5e6], head_share: [0.1, 0.9] };
const LOG_MAX = 5 * 1024 * 1024, LOG_KEEP = 1024 * 1024;   // a log over 5 MB is cut to its last 1 MB at start
const SESSION_ID = /^[A-Za-z0-9_-]{1,100}$/;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const shortError = (e) => String((e && e.message) || e).replace(/\s+/g, " ").slice(0, 120);

// the settings in force: defaults plus every valid key; {cfg, problems}
function resolveSettings(raw) {
  const problems = [];
  const cfg = { ...DEFAULTS, exempt_tools: [...DEFAULTS.exempt_tools] };
  if (!isObj(raw)) { problems.push("output-caps.json: not an object"); return { cfg, problems }; }
  for (const [k, v] of Object.entries(raw)) {
    if (k in RANGES) {
      const [lo, hi] = RANGES[k];
      if (isNum(v) && v >= lo && v <= hi) cfg[k] = v; else problems.push(`${k}: must be a number from ${lo} to ${hi}`);
    } else if (k === "enabled") {
      if (typeof v === "boolean") cfg[k] = v; else problems.push("enabled: must be true or false");
    } else if (k === "exempt_tools") {
      if (Array.isArray(v) && v.every((x) => typeof x === "string")) cfg[k] = v; else problems.push("exempt_tools: must be a list of names");
    } else if (k === "log_dir") {
      if (v === null || (typeof v === "string" && path.isAbsolute(v))) cfg[k] = v; else problems.push("log_dir: must be an absolute path");
    }
  }
  return { cfg, problems };
}

// the text parts of a result's content: a string is one part; a list holds parts, of which only {type:"text", text:<string>} count
function textOf(content) {
  if (typeof content === "string") return { kind: "string", total: content.length, text: content };
  if (!Array.isArray(content)) return null;
  const idx = [];
  let text = "";
  content.forEach((p, i) => { if (isObj(p) && p.type === "text" && typeof p.text === "string") { idx.push(i); text += p.text; } });
  return idx.length ? { kind: "list", idx, total: text.length, text } : null;
}

const countLines = (s) => (s ? (s.match(/\n/g) || []).length + (s.endsWith("\n") ? 0 : 1) : 0);

// cut `text` to about `cap` characters: head and tail on line boundaries and one line in the gap; null when the cut would not make it shorter
function cut(text, cap, headShare, isRead) {
  const headBudget = Math.floor(cap * headShare), tailBudget = cap - headBudget;
  let head = text.slice(0, headBudget);
  if (text[headBudget] !== "\n") { const nl = head.lastIndexOf("\n"); if (nl >= 0) head = head.slice(0, nl + 1); }   // whole lines only (a line longer than the budget is cut in the line)
  else head += "\n";
  let tailStart = text.length - tailBudget;
  if (tailStart < head.length) tailStart = head.length;
  if (tailStart > 0 && text[tailStart - 1] !== "\n") { const nl = text.indexOf("\n", tailStart); if (nl >= 0 && nl + 1 < text.length) tailStart = nl + 1; }
  const middle = text.slice(head.length, tailStart);
  if (!middle) return null;
  const how = isRead ? "Read the file again with offset and limit to get them." : "Run the command again with a narrower command or filter the output to get them.";
  const line = `[${middle.length} characters (${countLines(middle)} lines) were left out here to keep the chat short. ${how}]`;
  const out = head + (head.endsWith("\n") || !head ? "" : "\n") + line + "\n" + text.slice(tailStart);
  return out.length < text.length ? out : null;
}

module.exports = {
  id: "agentgraph-output-caps",
  async setup(ctx) {
    const settingsFile = path.join(__dirname, "output-caps.json");
    let cache = { mtime: -2, cfg: resolveSettings({}).cfg };
    const settings = () => {
      try {
        let m = -1;
        try { m = fs.statSync(settingsFile).mtimeMs; } catch (e) { if (!e || e.code !== "ENOENT") throw e; }
        if (m !== cache.mtime) {
          if (m < 0) cache = { mtime: m, cfg: resolveSettings({}).cfg };
          else {
            let raw, problems = [];
            try { raw = JSON.parse(fs.readFileSync(settingsFile, "utf8")); } catch (e) { raw = {}; problems.push("output-caps.json does not parse"); }
            const r = resolveSettings(raw);
            cache = { mtime: m, cfg: r.cfg };
            problems = problems.concat(r.problems);
            if (problems.length) log(null, null, "settings_invalid", null, null, problems.join("; ").slice(0, 400));
          }
        }
      } catch { /* an unreadable settings file leaves the last settings in force */ }
      return cache.cfg;
    };
    const active = () => settings().enabled !== false && String(process.env.AGENTGRAPH_OUTPUT_CAPS || "").toLowerCase() !== "off";

    const homeDir = () => {
      const own = process.env.AGENTGRAPH_OUTPUT_CAPS_HOME;
      if (own && path.isAbsolute(own)) return own;
      const cfgDir = settings().log_dir;
      if (cfgDir) return cfgDir;
      const xdg = process.env.XDG_DATA_HOME;
      return path.join(xdg && path.isAbsolute(xdg) ? xdg : path.join(os.homedir(), ".local", "share"), "agentgraph", "output-caps");
    };
    // one JSON line: time, session, tool, original size, kept size, event (and for settings_invalid or error a short reason made of setting names or an error message, never tool output)
    function log(session, tool, event, original, kept, reason) {
      try {
        const dir = homeDir();
        fs.mkdirSync(dir, { recursive: true });
        const row = { t: Date.now(), session: typeof session === "string" && SESSION_ID.test(session) ? session : null,
          tool: typeof tool === "string" ? tool.slice(0, 80) : null, original, kept, event };
        if (reason) row.reason = reason;
        fs.appendFileSync(path.join(dir, "output-caps.log"), JSON.stringify(row) + "\n");
      } catch { /* the log is best effort */ }
    }
    try {   // a log over 5 MB is cut to its last 1 MB (from a line start)
      const file = path.join(homeDir(), "output-caps.log");
      if (fs.statSync(file).size > LOG_MAX) {
        const buf = fs.readFileSync(file).subarray(-LOG_KEEP);
        const nl = buf.indexOf(10);
        fs.writeFileSync(file, nl >= 0 ? buf.subarray(nl + 1) : buf);
      }
    } catch { /* no log yet */ }

    // the hook: replaces event.result only when a completed, non-exempt result has more text than its cap
    const capResult = (event) => {
      if (!isObj(event) || event.status !== "completed" || !active()) return;
      const cfg = settings();
      const tool = typeof event.tool === "string" ? event.tool : "";
      if (!tool || cfg.exempt_tools.some((x) => x.toLowerCase() === tool.toLowerCase())) return;
      const result = event.result;
      if (!isObj(result)) return;
      const isRead = tool.toLowerCase() === "read";
      const cap = isRead ? cfg.read_chars : cfg.other_chars;
      // OpenCode turns an empty or missing content into the text of `output` (when that is a string); cap that case the same way
      const viaOutput = (result.content === undefined || (Array.isArray(result.content) && result.content.length === 0)) && typeof result.output === "string";
      const found = viaOutput ? { kind: "output", total: result.output.length, text: result.output } : textOf(result.content);
      if (!found || found.total <= cap) return;
      const cutText = cut(found.text, cap, cfg.head_share, isRead);
      if (cutText === null) return;
      let next;
      if (found.kind === "string") next = { ...result, content: cutText };
      else if (found.kind === "output") next = { ...result, output: cutText };
      else {
        const first = found.idx[0], drop = new Set(found.idx.slice(1));
        const content = [];
        result.content.forEach((p, i) => { if (drop.has(i)) return; content.push(i === first ? { ...p, text: cutText } : p); });
        next = { ...result, content };
      }
      if (result.metadata === undefined || isObj(result.metadata)) next.metadata = { ...(result.metadata || {}), capped: { original: found.total, kept: cutText.length } };
      event.result = next;
      log(event.sessionID, tool, "capped", found.total, cutText.length);
    };
    const handler = (event) => {
      try { capResult(event); } catch (e) {
        try { log(event && event.sessionID, event && event.tool, "error", null, null, shortError(e)); } catch { /* nothing */ }
      }
    };

    let registration = null;
    try { registration = await ctx.tool.hook("execute.after", handler); } catch (e) { log(null, null, "error", null, null, "hook not registered: " + shortError(e)); }
    const dispose = () => { try { if (registration && typeof registration.dispose === "function") registration.dispose(); } catch { /* nothing */ } registration = null; };
    dispose.handler = handler;   // so a test can call it directly
    return dispose;
  },
};
