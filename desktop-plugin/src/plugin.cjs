/*
 * agentgraph-desktop: OpenCode Desktop GUI plugin that adds an "Agent graph" tab to a session's side panel.
 *
 * Hand-written CommonJS, no JSX, no build dependencies. build.mjs wraps this file, together with
 * ../agentgraph/web/app.js and ../agentgraph/web/style.css, into ../gui-experimental.cjs.
 *
 * The desktop app evaluates that one file in two processes (see the GUI plugin guide, "One file, two processes"):
 *   - every window runs `setup`: the panel, the "+" menu item, the command and the styles;
 *   - the Electron main process runs `main`: an Ipc that runs the Python snapshot command, and the structure
 *     command that adds a flow through agents, changes its instruction, name or command, removes it or gives the
 *     message that runs it on a goal (`agentgraph structure flow`),
 *     the skills command that adds or removes one of an agent's skills or gives it all (`agentgraph skills`),
 *     the roster command that adds, renames, removes or restores an agent (`agentgraph agent`), the skill-file command
 *     that reads or edits a skill's text (`agentgraph skill`), the groups command (`agentgraph team`) and the
 *     outputs command that reads a step's output, saves a new text as its next revision or undoes that
 *     (`agentgraph output`).
 * The host evaluates the bundle as `Function("require", "module", "exports", code)`, so `__dirname` and
 * `__filename` do not exist, and `require` only knows the modules the app shares. Keep each process's code inside
 * its own setup; module scope must stay safe in both.
 *
 * Everything that comes from the snapshot document is untrusted text. This file never builds markup from it:
 * it only sets `textContent` on its own message element and hands the parsed document to the view.
 */
"use strict";

const gui = require("@opencode/plugin/gui");
const effect = require("effect");

// Filled in by build.mjs. When this source runs on its own (no bundle), fall back to a global view, no CSS.
const ASSETS =
  typeof AGENTGRAPH_ASSETS !== "undefined" // eslint-disable-line no-undef
    ? AGENTGRAPH_ASSETS // eslint-disable-line no-undef
    : {
        css: "",
        view: "global",
        loadView: () => (typeof window !== "undefined" && window.AgentGraph) || null,
      };

const TAB_ID = "graph";
const PANEL_ID = "graph";
const COMMAND_ID = "open";
const MENU_ID = "open";
const IPC_SUFFIX = "snapshot";

const SNAPSHOT_TIMEOUT_MS = 20000;
const SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024;
const DEFAULT_INTERVAL_MS = 4000;
const ERROR_MAX_CHARS = 300;
const SESSION_ID = /^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$/;
// The names: the same patterns as agentgraph/names.py, the one definition (tests/test_review4.py and the harness prove them equal).
const AGENT_NAME = /^[a-z][a-z0-9_-]{0,63}$/;
const FLOW_ID = /^[a-z0-9][a-z0-9-]{0,79}$/;
const SKILL_NAME = /^[a-z][a-z0-9-]{0,63}$/;
const MCP_NAME = /^[A-Za-z0-9_.-]{1,64}$/;
const VARIANT_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}$/; // a model's variant: not a name the toolkit defines
// A thing's `rev` (agentfiles.rev: 16 hex digits). A change from the view sends the rev it was showing as `expect`;
// the command refuses it, changing nothing, when the thing changed since (the answer is { error, rev }).
const REV = /^[0-9a-f]{16}$/;
const FLOW_STEPS_LIMIT = 20;
// The command's own limit, counted the same way: in characters (code points), not UTF-16 units.
const FLOW_TEXT_MAX_CHARS = 2000;
const FLOW_NAME = /^[a-z0-9][a-z0-9-]{1,31}$/; // a path's name, also its command's (/NAME)
const GOAL_MAX_CHARS = 4000; // the goal of a run message
// The chat of an agent's session (agentgraph.chatfeed): the latest messages to read (the command's own limits), and the
// longest message the panel sends to a session (a steer or a plain prompt), in characters.
const CHAT_LIMIT_DEFAULT = 40;
const CHAT_LIMIT_MAX = 200;
const CHAT_SEND_MAX_CHARS = 4000;
const FLOW_TIMEOUT_MS = 10000;
const SIDE_TIMEOUT_MS = 150000; // a question to the side chat can take two minutes; the view waits a little longer
const SIDE_MAX_CHARS = 4000;
const FLOW_MAX_BYTES = 1024 * 1024;
// The commands' own limits, in characters: an agent's description and prompt, a skill's text, a group's instruction.
const DESCRIPTION_MAX_CHARS = 300;
const PROMPT_MAX_CHARS = 8000;
const SKILL_TEXT_MAX_CHARS = 200000;
const SKILL_FILE_MAX_BYTES = 4 * 1024 * 1024;
const MODEL_NAME = /^[A-Za-z0-9_][A-Za-z0-9_.:@+-]{0,127}\/[A-Za-z0-9_][A-Za-z0-9_.:@+\/-]{0,199}$/;
const RECORD_MAX_CHARS = 4096;
// A step's output (agentgraph/outputs.py): its text is at most TEXT_LIMIT bytes of UTF-8; the task, step and output
// are named as the graph names them, each one argument, so none may start with "-" or hold a space or a control.
const OUTPUT_TEXT_MAX_BYTES = 256 * 1024;
// The back end's own words for a text holding NUL (agentgraph/outputs.py, text_problem), so both refuse it alike.
const OUTPUT_NUL = "the new text is not text: it holds the control character U+0000; nothing was written";
const OUTPUT_NAME = /^[^\s\-\u0000-\u001f\u007f][^\s\u0000-\u001f\u007f]{0,199}$/;

const HOST_CSS = [
  ".agentgraph-desktop-panel,.agentgraph-desktop-view{box-sizing:border-box;width:100%;height:100%;min-width:0;min-height:0;overflow:hidden}",
  ".agentgraph-desktop-panel>.agentgraph-desktop-message{box-sizing:border-box;padding:12px;opacity:.8;white-space:pre-wrap;overflow-wrap:anywhere}",
].join("\n");

const GRAPH_ICON =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="none">' +
  '<circle cx="8" cy="3.5" r="2" stroke="currentColor"/>' +
  '<circle cx="3.5" cy="12.5" r="2" stroke="currentColor"/>' +
  '<circle cx="12.5" cy="12.5" r="2" stroke="currentColor"/>' +
  '<path d="M7 5.3 4.5 10.7M9 5.3l2.5 5.4M5.5 12.5h5" stroke="currentColor" stroke-linecap="round"/>' +
  "</svg>";

const MESSAGES = {
  "tab.title": "Agent graph",
  "menu.title": "Agent graph",
  "command.open": "Open agent graph",
  "message.noView": "The agent graph view did not load.",
  "message.viewFailed": "The agent graph view failed to start: {{error}}",
  "message.sessionCopied": "Session ID copied",
  "message.sessionID": "Session ID",
};

// ---------------------------------------------------------------------------------------------------------------
// Shared helpers (pure; safe in both processes)

function oneLine(value) {
  const text = String(value === undefined || value === null ? "" : value)
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return "unknown error";
  return text.length > ERROR_MAX_CHARS ? text.slice(0, ERROR_MAX_CHARS - 1) + "…" : text;
}

function errorMessage(error) {
  if (error && typeof error === "object" && typeof error.message === "string") return error.message;
  return String(error);
}

function errorDocument(message) {
  return { version: 1, error: oneLine(message) };
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

// --- MCP servers: the live state the host knows (read only) -------------------------------------------------------------
// The snapshot (from Python) carries each server's name and its state from the settings (on or off); nothing else of an
// MCP entry ever leaves the settings. In the window, the Desktop host's own client may know more: its MCP status call
// (`client.mcp.status`, GET /mcp: an object of server name -> { status: "connected" | "disabled" | "failed" |
// "needs_auth" | "needs_client_registration", error? }). Only the state and the reason are taken from it, and only for a
// name the snapshot already lists; the reason is the host's untrusted text, cut to one line of at most 200 characters.
const MCP_STATE = { connected: "connected", failed: "failed", needs_auth: "needs sign-in", needs_client_registration: "needs sign-in", disabled: "off" };
const MCP_REASON_MAX_CHARS = 200;
const MCP_TIMEOUT_MS = 4000;

function mcpReason(value) {
  if (typeof value !== "string") return "";
  return Array.from(value.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, " ").replace(/\s+/g, " ").trim())
    .slice(0, MCP_REASON_MAX_CHARS)
    .join("");
}

// The call's answer as the status object: { data } (the SDK's wrapper), or the object itself; an error gives none.
function mcpStatusObject(result) {
  if (!isPlainObject(result)) return null;
  const wrapped = isPlainObject(result.data) && typeof result.data.status !== "string";
  const map = wrapped ? result.data : result.error ? null : result;
  return isPlainObject(map) ? map : null;
}

// Put the host's state on the snapshot's own servers, in place. Nothing is added to the document but `state`, `live` and
// `reason` of a server it already lists by a plain name.
function applyMcpStatus(doc, map) {
  if (!isPlainObject(doc) || !Array.isArray(doc.mcp) || !isPlainObject(map)) return doc;
  for (const row of doc.mcp) {
    if (!isPlainObject(row) || typeof row.name !== "string" || !MCP_NAME.test(row.name) || !Object.hasOwn(map, row.name)) continue;
    const entry = map[row.name];
    const status = isPlainObject(entry) && typeof entry.status === "string" ? entry.status : "";
    if (!Object.hasOwn(MCP_STATE, status)) continue;
    row.state = MCP_STATE[status];
    row.live = true;
    row.reason = row.state === "failed" || row.state === "needs sign-in" ? mcpReason(entry.error) : "";
  }
  return doc;
}

// Ask the host. Never rejects and never says why it could not: a host without the call, a failure or a slow answer
// leaves the settings' own state. No text from the host is logged.
async function hostMcpStatus(session) {
  let timer;
  try {
    const client = session && session.server && session.server.client;
    const status = client && client.mcp && client.mcp.status;
    if (typeof status !== "function") return null;
    const directory = typeof session.directory === "string" ? session.directory : undefined;
    const answer = Promise.resolve(status.call(client.mcp, directory ? { directory } : {}));
    const result = await Promise.race([answer, new Promise((resolve) => { timer = setTimeout(() => resolve(null), MCP_TIMEOUT_MS); })]);
    return mcpStatusObject(result);
  } catch (_) {
    return null;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

// The snapshot document as text -> an object with version 1, else an error document.
function parseDocument(text) {
  if (typeof text !== "string" || !text.trim()) return errorDocument("The snapshot command printed nothing.");
  let doc;
  try {
    doc = JSON.parse(text);
  } catch (_) {
    return errorDocument("The snapshot command returned invalid JSON. Check that options.python runs agentgraph.snapshot_json with Python 3.11 or newer.");
  }
  if (!isPlainObject(doc)) return errorDocument("The snapshot is not a JSON object.");
  if (doc.version !== 1) return errorDocument("Unsupported snapshot version: " + JSON.stringify(doc.version));
  if (doc.error !== undefined && doc.error !== null) doc.error = oneLine(doc.error);
  return doc;
}

// One Ipc token per plugin id. The id is the host-given ctx.id (the package name), so the token always matches the
// id the host enforces, whatever the plugin directory is called. Both processes build the same token from it.
const ipcTokens = new Map();
function snapshotIpc(pluginId) {
  let token = ipcTokens.get(pluginId);
  if (!token) {
    const Schema = effect.Schema;
    // `expect`: the rev the view was showing, on every method whose ops take one (all but the snapshot). It must be
    // declared: the host decodes each input with its schema (Effect Schema.Struct), which drops keys it does not
    // declare, so an undeclared `expect` would never reach main. Optional: absent means no check (an add, a read, a
    // restore, an old view); when present it is a string, and main and the view accept only a rev (REV).
    const expect = (Schema.optionalKey || Schema.optional)(Schema.String);
    token = gui.Ipc.define({
      id: pluginId + "." + IPC_SUFFIX,
      methods: {
        // Input: the session's directory and id. Output: the snapshot document as JSON text, so any document
        // (including an error document) crosses the bridge without a schema for its whole shape.
        snapshot: {
          input: Schema.Struct({ directory: Schema.String, session: Schema.String }),
          output: Schema.String,
        },
        // Add a flow, change its instruction, name or command, remove it, or get the message that runs it on a
        // goal. Input: op ("add", "add-command", "text", "name", "command", "prompt" or "remove"), id (the flow; for
        // add the path's name or ""; add-command is add with the command on and needs the name), path (the agents
        // separated by single spaces; "" unless add) and text: the instruction for add and text ("" for the
        // generated one; on stdin), the name for name ("" takes it away), "on" or "off" for command, the goal for
        // prompt (on stdin), "" for remove. Output: JSON text, {"flow"?, "flows": [...], "notes"?, "applies"?,
        // "live"?} afterwards, {"text"} for prompt, or {"error": "..."}.
        flow: {
          input: Schema.Struct({ op: Schema.String, id: Schema.String, path: Schema.String, text: Schema.String, expect }),
          output: Schema.String,
        },
        // Change one agent's skills in the plugin's preset file. Input: op ("add", "remove" or "all"), agent, and
        // skill ("" for all). Output: JSON text, {"agent", "skills": [...], "warnings": [...], "changed", "message"},
        // or {"error": "..."}.
        skills: {
          input: Schema.Struct({ op: Schema.String, agent: Schema.String, skill: Schema.String, expect }),
          output: Schema.String,
        },
        // Add an agent (an agent file, live at once: from a template, a copy or the fields given), read or replace
        // an added agent's instruction, rename an added agent with its flows, groups and path commands (one undo
        // record), remove an agent with its flows and groups, or restore what a removal, a rename or an instruction
        // change took. Input: op ("add", "read", "text", "rename", "remove" or "restore"), name, to (rename only: the
        // new name), record (restore only), model, variant, description, prompt (the instruction, on stdin; for "text" the new one), skills
        // (comma-separated; "" for none given, "-" for an empty list), read_only ("", "true" or "false"), copy_of
        // and template ("blank" or "verifier"); "" when not given. Output: JSON text, the command's answer
        // ({"agent", "action", "record"?, "message", ...}) or {"error": "..."}.
        roster: {
          input: Schema.Struct({
            op: Schema.String, name: Schema.String, record: Schema.String, model: Schema.String, variant: Schema.String,
            description: Schema.String, prompt: Schema.String, skills: Schema.String, read_only: Schema.String,
            copy_of: Schema.String, template: Schema.String, to: Schema.String, expect,
          }),
          output: Schema.String,
        },
        // Read or edit a skill's text. Input: op ("read", "write", "create", "delete" or "restore"), id, record
        // (restore only), description, text (on stdin) and assign (create only); "" when not given. Output: JSON
        // text, {"id", "description"?, "text"?, "record"?, ...} or {"error": "..."}.
        skillfile: {
          input: Schema.Struct({
            op: Schema.String, id: Schema.String, record: Schema.String, description: Schema.String, text: Schema.String,
            assign: Schema.String, expect,
          }),
          output: Schema.String,
        },
        // Add a group (agents that report to one lead), replace its members or instruction, or remove it. Input:
        // op ("add", "members", "text" or "remove"), id ("" for add), lead (add only), members (separated by single
        // spaces) and text (on stdin). Output: JSON text, {"team"?, "teams": [...], "notes"?} or {"error": "..."}.
        team: {
          input: Schema.Struct({ op: Schema.String, id: Schema.String, lead: Schema.String, members: Schema.String, text: Schema.String, expect }),
          output: Schema.String,
        },
        // Read a step's output, save a new text as its next revision (the steps that read the old one become stale;
        // one undoable step), or undo that. Input: op ("read", "write" or "restore"), directory (the session's: the
        // task graph is found from it as the snapshot finds it, and an undo is taken back only for a graph found so),
        // task, node and artifact (the output; "" for restore), record (restore only) and text (write only, on
        // stdin); "" when not given. Output: JSON
        // text, the command's answer ({"task", "node", "artifact", "text"?, "rev", "output"?, "stale"?, "record"?,
        // ...}) or {"error": "...", "rev"?}.
        output: {
          input: Schema.Struct({
            op: Schema.String, directory: Schema.String, task: Schema.String, node: Schema.String, artifact: Schema.String,
            record: Schema.String, text: Schema.String, expect,
          }),
          output: Schema.String,
        },
        // Read one session's recent chat (`agentgraph chat --session ID --json --limit N`). Input: the session id and the
        // number of latest messages (a whole number, 1 to 200). Output: the feed as JSON text (WEB_API.md, GET /api/chat)
        // as the command printed it, or {"error": "..."}.
        chat: {
          input: Schema.Struct({ session: Schema.String, limit: Schema.Number }),
          output: Schema.String,
        },
        // Ask the side chat one question (`agentgraph sidechat ask --main ID --text-file - --json`). Input: the main
        // session's id and the question (sent on stdin, never in the arguments). Output: the command's JSON text,
        // {"answer": "...", ...} or {"error": "..."}.
        side: {
          input: Schema.Struct({ main: Schema.String, text: Schema.String }),
          output: Schema.String,
        },
      },
    });
    ipcTokens.set(pluginId, token);
  }
  return token;
}

// ---------------------------------------------------------------------------------------------------------------
// Main process: run `python3 -P -m agentgraph.snapshot_json` without a shell

function createSnapshotRunner(deps) {
  // deps: { execFile, fs, path, env, home, python, demo, signal, timeoutMs, maxBytes }
  const inflight = new Map();

  function validate(input) {
    if (!isPlainObject(input)) return { error: "Bad snapshot request." };
    const directory = input.directory;
    const session = input.session === undefined || input.session === null ? "" : input.session;
    if (typeof directory !== "string" || !directory || directory.length > 4096 || directory.includes("\0"))
      return { error: "The session has no usable directory." };
    if (!deps.path.isAbsolute(directory)) return { error: "The session directory is not an absolute path." };
    if (typeof session !== "string" || (session && !SESSION_ID.test(session)))
      return { error: "The session id is not valid." };
    return { directory: deps.path.resolve(directory), session };
  }

  function processFailure(error, stderr) {
    if (error.name === "AbortError" || error.code === "ABORT_ERR") return "The plugin stopped.";
    if (error.code === "ENOENT")
      return "Python was not found at " + deps.python + ". Set options.python in opencode.json to a Python 3.11 or newer executable.";
    if (error.code === "EACCES") return "Python is not executable: " + deps.python + ".";
    if (error.code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER")
      return "The snapshot was larger than " + Math.round(deps.maxBytes / 1048576) + " MiB.";
    if (error.killed && error.signal)
      return "The snapshot command did not finish within " + Math.round(deps.timeoutMs / 1000) + " s.";
    if (/Unknown option:[ \t]+-P\b/i.test(stderr || ""))
      return "Python 3.11 or newer is required. Set options.python to a newer executable; " + deps.python + " does not support -P.";
    return null;
  }

  function lastLine(text) {
    return String(text || "").split(/\r?\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .pop();
  }

  function finish(error, stdout, stderr) {
    // Partial output must not hide a timeout, output overflow, or interpreter/startup failure.
    if (error) {
      const failure = processFailure(error, stderr);
      if (failure) return errorDocument(failure);
    }
    const last = lastLine(stdout);
    if (!error) return last ? parseDocument(last) : errorDocument(lastLine(stderr) || "The snapshot command printed nothing.");
    // The command prints its own error document when it fails ({"version": 1, "error": ...}, exit 1): keep it.
    const parsed = last ? safeParse(last) : undefined;
    if (isPlainObject(parsed) && parsed.version === 1 && parsed.error !== undefined && parsed.error !== null) {
      return parseDocument(last);
    }
    const status = typeof error.code === "number" ? "exited with status " + error.code
      : error.signal ? "was terminated by " + error.signal : "failed";
    const detail = lastLine(stderr) || "Check options.python and that agentgraph is installed.";
    return errorDocument("The snapshot command " + status + ": " + detail);
  }

  function safeParse(text) {
    try {
      return JSON.parse(text);
    } catch (_) {
      return undefined;
    }
  }

  function run(request) {
    return new Promise((resolve) => {
      deps.fs.stat(request.directory, (statError, stat) => {
        if (statError || !stat.isDirectory()) {
          resolve(errorDocument("The session directory does not exist: " + request.directory));
          return;
        }
        const args = ["-P", "-m", "agentgraph.snapshot_json"];
        if (deps.demo) args.push("--demo");
        if (request.session) args.push("--session=" + request.session);
        const env = Object.assign({}, deps.env, { PYTHONPATH: deps.home, PYTHONIOENCODING: "utf-8" });
        const options = {
          cwd: request.directory,
          env,
          encoding: "utf8",
          timeout: deps.timeoutMs,
          killSignal: "SIGKILL",
          maxBuffer: deps.maxBytes,
          windowsHide: true,
        };
        if (deps.signal) options.signal = deps.signal;
        try {
          deps.execFile(deps.python, args, options, (error, stdout, stderr) => {
            try {
              resolve(finish(error, stdout, stderr));
            } catch (failure) {
              resolve(errorDocument(errorMessage(failure)));
            }
          });
        } catch (error) {
          resolve(errorDocument(processFailure(error, "") || "Could not start Python: " + errorMessage(error)));
        }
      });
    });
  }

  return {
    // Never rejects: failures become { version: 1, error }.
    async snapshot(input) {
      try {
        const request = validate(input);
        if (request.error) return errorDocument(request.error);
        const key = request.directory + "\n" + request.session;
        let pending = inflight.get(key);
        if (!pending) {
          pending = run(request).finally(() => {
            if (inflight.get(key) === pending) inflight.delete(key);
          });
          inflight.set(key, pending);
        }
        return await pending;
      } catch (error) {
        return errorDocument("Could not collect the snapshot: " + errorMessage(error));
      }
    },
    inflight: () => inflight.size,
  };
}

// Run one python command without a shell, its stdin from request.stdin; the last line of its output is JSON, and
// `accept` turns a successful document into the answer (null: not one). Never rejects: failures become { error }.
function runCommand(deps, request, label, accept) {
  const fail = (message) => ({ error: oneLine(message) });
  return new Promise((resolve) => {
    const options = {
      cwd: deps.home,
      env: Object.assign({}, deps.env, { PYTHONPATH: deps.home, PYTHONIOENCODING: "utf-8" }),
      encoding: "utf8",
      timeout: deps.timeoutMs,
      killSignal: "SIGKILL",
      maxBuffer: deps.maxBytes,
      windowsHide: true,
    };
    if (deps.signal) options.signal = deps.signal;
    const finish = (error, stdout, stderr) => {
      const last = String(stdout || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean).pop();
      let doc;
      try {
        doc = last ? JSON.parse(last) : undefined;
      } catch (_) {
        doc = undefined;
      }
      if (isPlainObject(doc) && typeof doc.error === "string" && doc.error)
        return typeof doc.rev === "string" && REV.test(doc.rev) ? { error: oneLine(doc.error), rev: doc.rev } : fail(doc.error);
      const answer = !error && isPlainObject(doc) ? accept(doc) : null;
      if (answer) return answer;
      if (error && error.code === "ENOENT")
        return fail("Python was not found at " + deps.python + ". Set options.python in opencode.json to a Python 3.11 or newer executable.");
      if (error && error.killed && error.signal) return fail(label + " did not finish in time.");
      // The same hint as the snapshot: Apple's /usr/bin/python3 (3.9) is often first on the app's PATH.
      if (error && /Unknown option:[ \t]+-P\b/i.test(stderr || ""))
        return fail("Python 3.11 or newer is required. Set options.python to a newer executable; " + deps.python + " does not support -P.");
      const detail = String(stderr || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean).pop();
      return fail(label + " failed" + (detail ? ": " + detail : "."));
    };
    try {
      const child = deps.execFile(deps.python, request.args, options, (error, stdout, stderr) => {
        try {
          resolve(finish(error, stdout, stderr));
        } catch (failure) {
          resolve(fail(errorMessage(failure)));
        }
      });
      // A command that exits before reading stdin must not crash the main process with EPIPE.
      if (child && child.stdin) {
        child.stdin.on("error", () => {});
        child.stdin.end(request.stdin, "utf8");
      }
    } catch (error) {
      resolve(fail("Could not start Python: " + errorMessage(error)));
    }
  });
}

// ---------------------------------------------------------------------------------------------------------------
// Main process: run `python3 -P -m agentgraph.structure flow add|text|name|command|prompt|remove ... --json` without a
// shell; the instruction and the goal go to the command on stdin (`--text-file -`, `--goal-file -`), never on the
// command line.

// The `--expect REV` arguments for a request's optional `expect` (absent: none), or null when it is not a rev. An
// `expect` that is given must be a rev: an empty one is bad input, never taken as none (the endpoints answer 400).
function expectArgs(input) {
  if (input.expect === undefined) return [];
  return typeof input.expect === "string" && REV.test(input.expect) ? ["--expect", input.expect] : null;
}

// A command's request with `--expect REV` put before its final `--json`; { error } when `expect` is not a rev or the
// op takes none (an add, a read or a restore names nothing the view was showing).
function withExpect(request, input, allowed, fail) {
  if (request.error) return request;
  const extra = expectArgs(input);
  if (extra === null) return fail("The change needs the rev the view was showing, or none.");
  if (!extra.length) return request;
  if (!allowed) return fail("This change takes no rev.");
  const args = request.args.slice(0, -1).concat(extra, request.args.slice(-1));
  return Object.assign({}, request, { args });
}

function createFlowRunner(deps) {
  // deps: { execFile, path, env, home, python, demo, signal, timeoutMs, maxBytes }
  const fail = (message) => ({ error: oneLine(message) });

  // The request -> the command's arguments and its stdin, or { error }. Every name and id is checked here, so
  // nothing that starts with "-" (or holds a space) reaches the command line.
  function command(input) {
    if (!isPlainObject(input)) return fail("Bad flow request.");
    const { op, id, path, text } = input;
    if (![op, id, path, text].every((value) => typeof value === "string")) return fail("Bad flow request.");
    if (text.includes("\0")) return fail("Bad flow request.");
    const base = ["-P", "-m", "agentgraph.structure", "flow"];
    if (op === "prompt") {
      if (!FLOW_ID.test(id) || path) return fail("The flow needs a valid id.");
      if (!text.trim()) return fail("A goal is required: say what the task must achieve.");
      if (Array.from(text).length > GOAL_MAX_CHARS) return fail("The goal is longer than " + GOAL_MAX_CHARS + " characters.");
      return { args: base.concat(["prompt", id, "--goal-file", "-", "--json"]), stdin: text, prompt: true };
    }
    if (Array.from(text).length > FLOW_TEXT_MAX_CHARS) return fail("The instruction is longer than " + FLOW_TEXT_MAX_CHARS + " characters.");
    if (op === "add" || op === "add-command") {
      const names = path.split(" ");
      if (names.length < 2 || names.length > FLOW_STEPS_LIMIT || !names.every((name) => AGENT_NAME.test(name)))
        return fail("A flow needs two to " + FLOW_STEPS_LIMIT + " agent names.");
      if (id ? !FLOW_NAME.test(id) : op === "add-command") return fail("A path's name is 2 to 32 lowercase letters, digits and '-'.");
      const named = (id ? ["--name", id] : []).concat(op === "add-command" ? ["--command"] : []);
      return text.trim()
        ? { args: base.concat(["add"], names, named, ["--text-file", "-", "--json"]), stdin: text }
        : { args: base.concat(["add"], names, named, ["--json"]), stdin: "" };
    }
    if (op === "name" || op === "command") {
      if (!FLOW_ID.test(id) || path) return fail("The flow needs a valid id.");
      if (op === "name") {
        if (text && !FLOW_NAME.test(text)) return fail("A path's name is 2 to 32 lowercase letters, digits and '-'.");
        return { args: base.concat(["name", id], text ? [text] : [], ["--json"]), stdin: "" };
      }
      if (text !== "on" && text !== "off") return fail("Bad flow request.");
      return { args: base.concat(["command", id, "--" + text, "--json"]), stdin: "" };
    }
    if (op === "text" || op === "remove") {
      if (!FLOW_ID.test(id) || path) return fail("The flow needs a valid id.");
      if (op === "remove" && text) return fail("Bad flow request.");
      return op === "text"
        ? { args: base.concat(["text", id, "--text-file", "-", "--json"]), stdin: text }
        : { args: base.concat(["remove", id, "--json"]), stdin: "" };
    }
    return fail("Bad flow request.");
  }

  const checked = (input) => withExpect(command(input), isPlainObject(input) ? input : {},
    isPlainObject(input) && input.op !== "add" && input.op !== "add-command", fail);

  const run = (request) => runCommand(deps, request, "The structure command", (doc) => {
    if (request.prompt) return typeof doc.text === "string" ? { text: doc.text } : null;
    if (!Array.isArray(doc.flows)) return null;
    const answer = { flows: doc.flows };
    if (isPlainObject(doc.flow)) answer.flow = doc.flow;
    if (Array.isArray(doc.notes)) answer.notes = doc.notes;
    if (typeof doc.applies === "string") answer.applies = doc.applies;
    if (typeof doc.live === "boolean") answer.live = doc.live;
    return answer;
  });

  let queue = Promise.resolve();
  return {
    // Never rejects: failures become { error }. One change at a time, in the order asked. The run message is
    // read-only, so the demo gives it too.
    flow(input) {
      const request = checked(input);
      if (request.error) return Promise.resolve(request);
      if (deps.demo && !request.prompt) return Promise.resolve(fail("The demo cannot change the structure."));
      const next = queue.then(() => run(request));
      queue = next.catch(() => {});
      return next;
    },
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Main process: run `python3 -P -m agentgraph.skillset add|remove AGENT SKILL --json` or `all AGENT --json` without a
// shell. The command keeps its own default files (the plugin's preset file and SKILLS.md, with a backup).

function createSkillRunner(deps) {
  const fail = (message) => ({ error: oneLine(message) });
  // Every name is checked here, so nothing that starts with "-" (or holds a space) reaches the command line.
  function command(input) {
    if (!isPlainObject(input)) return fail("Bad skills request.");
    const { op, agent, skill } = input;
    if (![op, agent, skill].every((value) => typeof value === "string")) return fail("Bad skills request.");
    if (!AGENT_NAME.test(agent)) return fail("The skills change needs a valid agent name.");
    const base = ["-P", "-m", "agentgraph.skillset"];
    if (op === "all") return skill ? fail("Bad skills request.") : { args: base.concat(["all", agent, "--json"]), stdin: "" };
    if (op === "add" || op === "remove") {
      if (!SKILL_NAME.test(skill)) return fail("The skills change needs a valid skill name.");
      return { args: base.concat([op, agent, skill, "--json"]), stdin: "" };
    }
    return fail("Bad skills request.");
  }
  const checked = (input) => withExpect(command(input), isPlainObject(input) ? input : {}, true, fail);
  const run = (request) => runCommand(deps, request, "The skills command", (doc) => {
    if (typeof doc.agent !== "string" || !Array.isArray(doc.skills)) return null;
    return { agent: doc.agent, skills: doc.skills, warnings: Array.isArray(doc.warnings) ? doc.warnings : [], changed: doc.changed !== false, message: typeof doc.message === "string" ? doc.message : "" };
  });
  let queue = Promise.resolve();
  return {
    // Never rejects: failures become { error }. One change at a time, in the order asked.
    skills(input) {
      const request = checked(input);
      if (request.error) return Promise.resolve(request);
      if (deps.demo) return Promise.resolve(fail("The demo cannot change skills."));
      const next = queue.then(() => run(request));
      queue = next.catch(() => {});
      return next;
    },
  };
}

// ---------------------------------------------------------------------------------------------------------------
// Main process: the roster, skill-file and groups commands, without a shell. Every name, id and record is checked
// here, so nothing that starts with "-" (or holds a space) reaches the command line; free text goes on stdin, or
// as one `--option=value` argument when it is short (a description).

const chars = (text) => Array.from(text).length;
const freeText = (value, limit) => typeof value === "string" && !value.includes("\0") && chars(value) <= limit;
const recordPath = (value) => typeof value === "string" && value.startsWith("/") && value.length <= RECORD_MAX_CHARS
  && !value.includes("\0") && !/[\r\n]/.test(value);

// One change at a time, in the order asked; the demo changes nothing. Never rejects: failures become { error }.
function serialRunner(deps, command, label, accept, demoMessage) {
  let queue = Promise.resolve();
  return (input) => {
    const request = command(input);
    if (request.error) return Promise.resolve(request);
    if (deps.demo) return Promise.resolve({ error: demoMessage });
    const next = queue.then(() => runCommand(deps, request, label, accept));
    queue = next.catch(() => {});
    return next;
  };
}

function createRosterRunner(deps) {
  const fail = (message) => ({ error: oneLine(message) });
  const KEYS = ["op", "name", "record", "model", "variant", "description", "prompt", "skills", "read_only", "copy_of", "template", "to"];
  function command(input) {
    if (!isPlainObject(input) || !KEYS.every((key) => typeof input[key] === "string")) return fail("Bad roster request.");
    const { op, name, record, model, variant, description, prompt, skills, read_only: readOnly, copy_of: copyOf, template, to } = input;
    const base = ["-P", "-m", "agentgraph.roster", "agent"];
    const others = [model, variant, description, prompt, skills, readOnly, copyOf, template];
    if (op === "restore") {
      if (name || to || others.some(Boolean) || !recordPath(record)) return fail("The restore needs the undo record a removal or a rename returned.");
      return { args: base.concat(["restore", record, "--json"]), stdin: "" };
    }
    if (!AGENT_NAME.test(name)) return fail("The agent needs a valid name.");
    if (record) return fail("Bad roster request.");
    if (op === "rename") {
      if (others.some(Boolean)) return fail("Bad roster request.");
      if (!AGENT_NAME.test(to)) return fail("The new name is not a valid agent name.");
      return { args: base.concat(["rename", name, to, "--json"]), stdin: "" };
    }
    if (to) return fail("Bad roster request.");
    if (op === "remove" || op === "read") {
      if (others.some(Boolean)) return fail("Bad roster request.");
      return { args: base.concat([op === "read" ? "show" : "remove", name, "--json"]), stdin: "" };
    }
    if (op === "text") {
      if ([model, variant, description, skills, readOnly, copyOf, template].some(Boolean)) return fail("Bad roster request.");
      if (!freeText(prompt, PROMPT_MAX_CHARS) || !prompt.trim()) return fail("The instruction must be 1 to " + PROMPT_MAX_CHARS + " characters.");
      return { args: base.concat(["text", name, "--text-file", "-", "--json"]), stdin: prompt };
    }
    if (op !== "add") return fail("Bad roster request.");
    const args = base.concat(["add", name]);
    if (template) {
      if (template !== "blank" && template !== "verifier") return fail("The template must be blank or verifier.");
      if (copyOf) return fail("Give either an agent to copy or a template, not both.");
      args.push("--template", template);
    }
    if (model) {
      if (!MODEL_NAME.test(model)) return fail("The model must be provider/model.");
      args.push("--model", model);
    }
    if (variant) {
      if (!VARIANT_NAME.test(variant)) return fail("The variant is not a valid name.");
      args.push("--variant", variant);
    }
    if (copyOf) {
      if (!AGENT_NAME.test(copyOf)) return fail("The agent to copy needs a valid name.");
      args.push("--copy-of", copyOf);
    }
    if (description) {
      if (!freeText(description, DESCRIPTION_MAX_CHARS) || /[\r\n]/.test(description))
        return fail("The description must be one line of at most " + DESCRIPTION_MAX_CHARS + " characters.");
      args.push("--description=" + description);
    }
    if (skills === "-") args.push("--skills="); // an empty list given
    else if (skills) {
      const names = skills.split(",");
      if (!names.every((skill) => SKILL_NAME.test(skill))) return fail("The skills must be skill names separated by commas.");
      args.push("--skills", names.join(","));
    }
    if (readOnly === "true") args.push("--read-only");
    else if (readOnly === "false") args.push("--no-read-only");
    else if (readOnly) return fail("Bad roster request.");
    if (!freeText(prompt, PROMPT_MAX_CHARS)) return fail("The prompt is longer than " + PROMPT_MAX_CHARS + " characters.");
    if (prompt.trim()) args.push("--prompt-file", "-");
    if (!copyOf && template !== "verifier" && !(model && description && prompt.trim()))
      return fail("A new agent needs a model, a description and an instruction, or a template or an agent to copy.");
    args.push("--json");
    return { args, stdin: prompt.trim() ? prompt : "" };
  }
  const accept = (doc) => (typeof doc.agent === "string" && typeof doc.action === "string" ? doc : null);
  const checked = (input) => withExpect(command(input), isPlainObject(input) ? input : {},
    isPlainObject(input) && ["remove", "text", "rename"].includes(input.op), fail);
  return { roster: serialRunner(deps, checked, "The roster command", accept, "The demo cannot change the agents.") };
}

function createSkillFileRunner(deps) {
  const fail = (message) => ({ error: oneLine(message) });
  const KEYS = ["op", "id", "record", "description", "text", "assign"];
  function command(input) {
    if (!isPlainObject(input) || !KEYS.every((key) => typeof input[key] === "string")) return fail("Bad skill request.");
    const { op, id, record, description, text, assign } = input;
    const base = ["-P", "-m", "agentgraph.skillfiles", "skill"];
    if (op === "restore") {
      if (id || description || text || assign || !recordPath(record)) return fail("The restore needs the undo record an edit returned.");
      return { args: base.concat(["restore", record, "--json"]), stdin: "" };
    }
    if (!SKILL_NAME.test(id)) return fail("The skill needs a valid id.");
    if (record) return fail("Bad skill request.");
    if (op === "read" || op === "delete") {
      if (description || text || assign) return fail("Bad skill request.");
      return { args: base.concat([op === "read" ? "show" : "delete", id, "--json"]), stdin: "" };
    }
    if (op !== "write" && op !== "create") return fail("Bad skill request.");
    if (!freeText(text, SKILL_TEXT_MAX_CHARS)) return fail("The skill's text is longer than " + SKILL_TEXT_MAX_CHARS + " characters.");
    if (description && (!freeText(description, 1024) || /[\r\n]/.test(description))) return fail("The description must be one line.");
    const args = base.concat([op, id, "--text-file", "-"]);
    if (op === "create") {
      if (!description) return fail("A new skill needs a description.");
      if (assign) {
        if (!AGENT_NAME.test(assign)) return fail("The agent to give the skill to needs a valid name.");
        args.push("--assign", assign);
      }
    } else if (assign) return fail("Bad skill request.");
    if (description) args.push("--description=" + description);
    args.push("--json");
    return { args, stdin: text };
  }
  const accept = (doc) => (typeof doc.id === "string" ? doc : null);
  const checked = (input) => withExpect(command(input), isPlainObject(input) ? input : {},
    isPlainObject(input) && (input.op === "write" || input.op === "delete"), fail);
  return { skillfile: serialRunner(deps, checked, "The skill command", accept, "The demo cannot change skills.") };
}

function createTeamRunner(deps) {
  const fail = (message) => ({ error: oneLine(message) });
  function command(input) {
    if (!isPlainObject(input)) return fail("Bad group request.");
    const { op, id, lead, members, text } = input;
    if (![op, id, lead, members, text].every((value) => typeof value === "string")) return fail("Bad group request.");
    if (!freeText(text, FLOW_TEXT_MAX_CHARS)) return fail("The instruction is longer than " + FLOW_TEXT_MAX_CHARS + " characters.");
    const base = ["-P", "-m", "agentgraph.teams"];
    const names = members ? members.split(" ") : [];
    const named = names.length <= FLOW_STEPS_LIMIT && names.every((name) => AGENT_NAME.test(name));
    if (op === "add") {
      if (id || !AGENT_NAME.test(lead) || !names.length || !named) return fail("A group needs a lead and members.");
      return text.trim()
        ? { args: base.concat(["add", lead], names, ["--text-file", "-", "--json"]), stdin: text }
        : { args: base.concat(["add", lead], names, ["--json"]), stdin: "" };
    }
    if (!FLOW_ID.test(id) || lead) return fail("The group needs a valid id.");
    if (op === "members") {
      if (!names.length || !named || text) return fail("The group needs its members.");
      return { args: base.concat(["members", id], names, ["--json"]), stdin: "" };
    }
    if (members) return fail("Bad group request.");
    if (op === "text") return { args: base.concat(["text", id, "--text-file", "-", "--json"]), stdin: text };
    if (op === "remove" && !text) return { args: base.concat(["remove", id, "--json"]), stdin: "" };
    return fail("Bad group request.");
  }
  const accept = (doc) => {
    if (!Array.isArray(doc.teams)) return null;
    const answer = { teams: doc.teams };
    if (isPlainObject(doc.team)) answer.team = doc.team;
    if (Array.isArray(doc.notes)) answer.notes = doc.notes;
    return answer;
  };
  const checked = (input) => withExpect(command(input), isPlainObject(input) ? input : {},
    isPlainObject(input) && input.op !== "add", fail);
  return { team: serialRunner(deps, checked, "The groups command", accept, "The demo cannot change the structure.") };
}

// Main process: `python3 -P -m agentgraph.outputs output read|write|restore ... --json` without a shell; the new text
// goes on stdin (`--text-file -`), the session's folder as `--from DIR` (the graph is found from it, as the snapshot
// finds it from its working folder).
function createOutputRunner(deps) {
  const fail = (message) => ({ error: oneLine(message) });
  const KEYS = ["op", "directory", "task", "node", "artifact", "record", "text"];
  function command(input) {
    if (!isPlainObject(input) || !KEYS.every((key) => typeof input[key] === "string")) return fail("Bad output request.");
    const { op, directory, task, node, artifact, record, text } = input;
    const base = ["-P", "-m", "agentgraph.outputs", "output"];
    if (op !== "read" && op !== "write" && op !== "restore") return fail("Bad output request.");
    if (!directory || directory.length > 4096 || directory.includes("\0") || !deps.path.isAbsolute(directory))
      return fail("The session has no usable directory.");
    const from = ["--from", deps.path.resolve(directory)];
    if (op === "restore") {
      // Only a record of a graph found from the session's folder is taken back (the command refuses any other).
      if (task || node || artifact || text || !recordPath(record)) return fail("The undo needs the record a save returned.");
      return { args: base.concat(["restore", record], from, ["--json"]), stdin: "" };
    }
    if (record) return fail("Bad output request.");
    if (![task, node, artifact].every((name) => OUTPUT_NAME.test(name))) return fail("The output needs its task, step and name.");
    const where = [task, node, artifact].concat(from);
    if (op === "read") {
      if (text) return fail("Bad output request.");
      return { args: base.concat(["read"], where, ["--json"]), stdin: "" };
    }
    if (text.includes("\0")) return fail(OUTPUT_NUL);
    if (new TextEncoder().encode(text).length > OUTPUT_TEXT_MAX_BYTES)
      return fail("The text is larger than 256 KiB, the most an output may be to be edited here.");
    return { args: base.concat(["write"], where, ["--text-file", "-", "--json"]), stdin: text };
  }
  const accept = (doc) => (typeof doc.task === "string" ? doc : null);
  const checked = (input) => withExpect(command(input), isPlainObject(input) ? input : {},
    isPlainObject(input) && input.op === "write", fail);
  return { output: serialRunner(deps, checked, "The outputs command", accept, "The demo cannot change outputs.") };
}

// Main process: `python3 -P -m agentgraph.chatfeed --session=ID --json --limit=N [--demo]` without a shell, read-only. The
// session id and the limit are checked here as the window checks them; the feed is passed on as the command printed it
// (it holds no tool input or output), and an error document or a failed command is { error: one line }.
function createChatRunner(deps) {
  const fail = (message) => ({ error: oneLine(message) });
  function command(input) {
    if (!isPlainObject(input)) return fail("Bad chat request.");
    const { session, limit } = input;
    if (typeof session !== "string" || !SESSION_ID.test(session)) return fail("The session id is not valid.");
    if (!Number.isInteger(limit) || limit < 1 || limit > CHAT_LIMIT_MAX) return fail("The number of messages is not valid.");
    const args = ["-P", "-m", "agentgraph.chatfeed", "--session=" + session, "--json", "--limit=" + limit];
    if (deps.demo) args.push("--demo");
    return { args, stdin: "" };
  }
  const accept = (doc) => (typeof doc.session === "string" && Array.isArray(doc.items) ? doc : null);
  return {
    chat(input) {
      const request = command(input);
      return request.error ? Promise.resolve(request) : runCommand(deps, request, "The chat command", accept);
    },
  };
}

// Main process: `python3 -P -m agentgraph.sidechat ask --main ID --text-file - --json` without a shell. The question goes
// to the command on stdin, never on the command line (and so never into a log); the main session's id and the question
// are checked here as the window checks them. The answer is the command's document, or { error: one line }.
function createSideRunner(deps) {
  const fail = (message) => ({ error: oneLine(message) });
  function command(input) {
    if (!isPlainObject(input)) return fail("Bad side chat request.");
    const { main, text } = input;
    if (deps.demo) return fail("The demo has no side chat.");
    if (typeof main !== "string" || !SESSION_ID.test(main)) return fail("The session id is not valid.");
    if (typeof text !== "string" || !text.trim()) return fail("Write a question first.");
    if (text.includes("\0") || Array.from(text).length > SIDE_MAX_CHARS) return fail("Bad side chat request.");
    return { args: ["-P", "-m", "agentgraph.sidechat", "ask", "--main", main, "--text-file", "-", "--json"], stdin: text };
  }
  const accept = (doc) => (typeof doc.answer === "string" ? doc : null);
  return {
    side(input) {
      const request = command(input);
      return request.error ? Promise.resolve(request) : runCommand(deps, request, "The side chat", accept);
    },
  };
}

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() !== "";
}

function main(ctx) {
  const childProcess = require("node:child_process");
  const fs = require("node:fs");
  const os = require("node:os");
  const path = require("node:path");
  const options = isPlainObject(ctx.options) ? ctx.options : {};
  const expandHome = (value) => (value === "~" || value.startsWith("~/") ? path.join(os.homedir(), value.slice(1)) : value);

  const home = nonEmptyString(options.home)
    ? path.resolve(expandHome(options.home.trim()))
    : path.join(os.homedir(), ".config", "opencode", "orchestration");
  const python = nonEmptyString(options.python) ? expandHome(options.python.trim()) : "python3";

  const runner = createSnapshotRunner({
    execFile: childProcess.execFile,
    fs,
    path,
    env: process.env,
    home,
    python,
    demo: options.demo === true,
    signal: ctx.signal,
    timeoutMs: SNAPSHOT_TIMEOUT_MS,
    maxBytes: SNAPSHOT_MAX_BYTES,
  });

  const flows = createFlowRunner({
    execFile: childProcess.execFile,
    path,
    env: process.env,
    home,
    python,
    demo: options.demo === true,
    signal: ctx.signal,
    timeoutMs: FLOW_TIMEOUT_MS,
    maxBytes: FLOW_MAX_BYTES,
  });

  const skills = createSkillRunner({
    execFile: childProcess.execFile,
    path,
    env: process.env,
    home,
    python,
    demo: options.demo === true,
    signal: ctx.signal,
    timeoutMs: FLOW_TIMEOUT_MS,
    maxBytes: FLOW_MAX_BYTES,
  });

  const commandDeps = (maxBytes) => ({
    execFile: childProcess.execFile,
    path,
    env: process.env,
    home,
    python,
    demo: options.demo === true,
    signal: ctx.signal,
    timeoutMs: FLOW_TIMEOUT_MS,
    maxBytes,
  });
  const roster = createRosterRunner(commandDeps(FLOW_MAX_BYTES));
  const skillFiles = createSkillFileRunner(commandDeps(SKILL_FILE_MAX_BYTES));
  const groups = createTeamRunner(commandDeps(FLOW_MAX_BYTES));
  const outputs = createOutputRunner(commandDeps(SKILL_FILE_MAX_BYTES));
  // The chat is read as the snapshot is: the same time limit and size cap.
  const chats = createChatRunner(Object.assign(commandDeps(SNAPSHOT_MAX_BYTES), { timeoutMs: SNAPSHOT_TIMEOUT_MS }));

  const sides = createSideRunner(Object.assign(commandDeps(SNAPSHOT_MAX_BYTES), { timeoutMs: SIDE_TIMEOUT_MS }));

  ctx.provide(snapshotIpc(ctx.id), {
    snapshot: (input) => runner.snapshot(input).then((doc) => JSON.stringify(doc)),
    flow: (input) => flows.flow(input).then((doc) => JSON.stringify(doc)),
    skills: (input) => skills.skills(input).then((doc) => JSON.stringify(doc)),
    roster: (input) => roster.roster(input).then((doc) => JSON.stringify(doc)),
    skillfile: (input) => skillFiles.skillfile(input).then((doc) => JSON.stringify(doc)),
    team: (input) => groups.team(input).then((doc) => JSON.stringify(doc)),
    output: (input) => outputs.output(input).then((doc) => JSON.stringify(doc)),
    chat: (input) => chats.chat(input).then((doc) => JSON.stringify(doc)),
    side: (input) => sides.side(input).then((doc) => JSON.stringify(doc)),
  });

  if (ctx.log && typeof ctx.log.write === "function") {
    ctx.log.write("info", "agentgraph snapshot bridge ready", { home, python });
  }
}

// ---------------------------------------------------------------------------------------------------------------
// Windows: panel, menu item, command, styles

let viewCache;
let viewFailure;
function loadView() {
  if (viewCache === undefined) {
    try {
      viewCache = ASSETS.loadView() || null;
    } catch (error) {
      viewCache = null;
      viewFailure = error;
    }
  }
  return viewCache;
}

function showMessage(host, text) {
  const doc = host.ownerDocument || document;
  const message = doc.createElement("div");
  message.className = "agentgraph-desktop-message";
  message.textContent = text; // text only, never markup
  host.appendChild(message);
}

function renderPanel(ctx, solid, props) {
  // A panel's render returns a Solid JSX.Element; a DOM node is one, and Solid inserts it as it is.
  const host = document.createElement("div");
  host.className = "agentgraph-desktop-panel";

  let alive = true;
  let handle = null;

  // Read props.session where it is used (never destructured), so a session switch in the same panel is followed.
  const sessionRequest = () => {
    const session = props.session;
    if (!session) return null;
    return {
      directory: typeof session.directory === "string" ? session.directory : "",
      session: typeof session.id === "string" ? session.id : "",
    };
  };
  const sessionKey = () => {
    const request = sessionRequest();
    return request ? request.directory + "\n" + request.session : undefined;
  };

  const ask = async (request) => {
    const text = await ctx.main(snapshotIpc(ctx.id)).snapshot(request, { signal: ctx.signal });
    return parseDocument(text);
  };

  // The sessions of the last snapshot this panel read (the root and each agent's), for the session it was read for:
  // a message or a stop is accepted for one of these only.
  let known = { key: undefined, ids: new Set(), root: "" };
  const rememberSessions = (doc) => {
    const ids = new Set();
    const add = (id) => {
      if (typeof id === "string" && SESSION_ID.test(id)) ids.add(id);
    };
    if (isPlainObject(doc.root)) add(doc.root.id);
    for (const agent of Array.isArray(doc.agents) ? doc.agents : []) {
      if (isPlainObject(agent) && Array.isArray(agent.sessions)) for (const item of agent.sessions) add(isPlainObject(item) ? item.id : undefined);
    }
    known = { key: sessionKey(), ids, root: isPlainObject(doc.root) && typeof doc.root.id === "string" && SESSION_ID.test(doc.root.id) ? doc.root.id : "" };
  };
  const isKnownSession = (id) => known.key !== undefined && known.key === sessionKey() && known.ids.has(id);

  const fetchSnapshot = async () => {
    if (!alive) return errorDocument("The panel is closed.");
    try {
      const request = sessionRequest();
      if (!request || !request.directory) return errorDocument("No session is open.");
      const doc = await ask(request);
      if (!alive) return errorDocument("The panel is closed.");
      if (Array.isArray(doc.mcp) && doc.mcp.length) applyMcpStatus(doc, await hostMcpStatus(props.session));
      if (!alive) return errorDocument("The panel is closed.");
      // Reset/remount already fetches the new session; don't repeat it from an obsolete request.
      const now = sessionRequest();
      if (!now || now.directory !== request.directory || now.session !== request.session)
        return errorDocument("The session changed while collecting the snapshot.");
      if (!doc.error) rememberSessions(doc);
      return doc;
    } catch (error) {
      return errorDocument("The desktop main process did not answer: " + errorMessage(error));
    }
  };

  // Drawing a flow in the view writes the structure folder through the main process. The demo writes nothing,
  // so it gets no flow action and the view only shows the flows. The requests are the loopback endpoint's
  // (/api/structure/flow): {op: "add", path, text?, name?, command?}, {op: "text", id, text}, {op: "name", id, name},
  // {op: "command", id, on}, {op: "prompt", id, goal} (answers {text}) and {op: "remove", id}.
  // A change may carry the `rev` the view was showing as `expect` (a rev, REV: anything else is refused here, as main
  // and the commands refuse it). A refusal has the web page's shape (index.html `post`): one because the thing changed
  // since (the reply carries the current rev) is an Error with `status` 409, `stale` true and that `rev`; any other has
  // no `stale` and no `status` (the commands have none). It is made here, on the window's side: the rev crosses the
  // host's bridge inside main's answer (a String), while an Error thrown in main would cross as its message only.
  const carryExpect = (request, input, bad) => {
    if (request.expect === undefined) return input;
    if (typeof request.expect !== "string" || !REV.test(request.expect)) throw new Error(bad);
    return Object.assign(input, { expect: request.expect });
  };
  // Stale needs both: an error, and a rev. An answer with no error is never a stale refusal (review n8 r6).
  const refusal = (doc, fallback) => {
    const error = new Error(oneLine(isPlainObject(doc) && doc.error ? doc.error : fallback));
    if (isPlainObject(doc) && typeof doc.error === "string" && doc.error && typeof doc.rev === "string" && REV.test(doc.rev))
      Object.assign(error, { status: 409, stale: true, rev: doc.rev });
    return error;
  };
  const flow = async (request) => {
    if (!alive) throw new Error("The panel is closed.");
    if (!isPlainObject(request)) throw new Error("Bad flow request.");
    const path = Array.isArray(request.path) ? request.path : [];
    // The path crosses the bridge as names joined by single spaces, so a name may not hold any space itself.
    if (!path.every((name) => typeof name === "string" && /^\S+$/.test(name))) throw new Error("Bad flow request.");
    const op = String(request.op || "");
    const input = {
      op,
      id: typeof request.id === "string" ? request.id : "",
      path: path.join(" "),
      text: typeof request.text === "string" ? request.text : "",
    };
    if (op === "add") {
      const name = optionalText(request.name);
      if (typeof name !== "string" || (request.command !== undefined && typeof request.command !== "boolean"))
        throw new Error("Bad flow request.");
      if (request.command === true) input.op = "add-command";
      input.id = name;
    } else if (op === "name") {
      if (typeof request.name !== "string") throw new Error("Bad flow request.");
      input.text = request.name;
    } else if (op === "command") {
      if (typeof request.on !== "boolean") throw new Error("Bad flow request.");
      input.text = request.on ? "on" : "off";
    } else if (op === "prompt") {
      if (typeof request.goal !== "string") throw new Error("Bad flow request.");
      input.text = request.goal;
    } else if (op === "add-command") {
      throw new Error("Bad flow request.");
    }
    carryExpect(request, input, "Bad flow request.");
    const text = await ctx.main(snapshotIpc(ctx.id)).flow(input, { signal: ctx.signal });
    let doc;
    try {
      doc = JSON.parse(text);
    } catch (_) {
      throw new Error("The structure command did not answer with JSON.");
    }
    const valid = op === "prompt" ? (answer) => typeof answer.text === "string" : (answer) => Array.isArray(answer.flows);
    if (!isPlainObject(doc) || doc.error || !valid(doc)) throw refusal(doc, "The flow could not be changed.");
    return doc;
  };
  // Run a path: the run message for the flow and the goal (from main, as `agentgraph flow prompt` gives it), sent
  // as a user message to this panel's session (the orchestrator's) with the host's own call,
  // props.session.server.client.session.prompt({sessionID, text}): the call the Desktop app makes for a message
  // typed into a session. The orchestrator then takes it as a new task.
  const run = async (request) => {
    if (!alive) throw new Error("The panel is closed.");
    if (!isPlainObject(request) || typeof request.flow !== "string" || typeof request.goal !== "string")
      throw new Error("Bad run request.");
    if (!request.goal.trim()) throw new Error("A goal is required: say what the task must achieve.");
    const session = props.session;
    const sessionID = session && typeof session.id === "string" ? session.id : "";
    if (!sessionID) throw new Error("No session is open.");
    if (typeof session.parentID === "string" && session.parentID)
      throw new Error("This is a subagent's session; open the orchestrator's session to run a path.");
    const client = session.server && session.server.client;
    const send = client && client.session && client.session.prompt;
    if (typeof send !== "function") throw new Error("This OpenCode does not let a plugin send a message to a session.");
    const { text } = await flow({ op: "prompt", id: request.flow, goal: request.goal });
    if (!alive) throw new Error("The panel is closed.");
    const now = props.session;
    if (!now || now.id !== sessionID) throw new Error("The session changed before the message was sent; nothing was sent.");
    try {
      await send.call(client.session, { sessionID, text });
    } catch (error) {
      throw new Error("The message could not be sent: " + oneLine(errorMessage(error)));
    }
    return { sent: true, session: sessionID };
  };
  // A skill change in an agent's details runs the skills command through the main process; names cross the
  // bridge as they are, so one holding a space or starting with "-" is refused here and again in main.
  const skills = async (request) => {
    if (!alive) throw new Error("The panel is closed.");
    if (!isPlainObject(request)) throw new Error("Bad skills request.");
    const all = request.all === true;
    const input = { op: all ? "all" : request.on === true ? "add" : request.on === false ? "remove" : "", agent: request.agent, skill: all ? "" : request.skill };
    if (!input.op || ![input.agent, input.skill].every((name) => typeof name === "string" && !/\s/.test(name) && !name.startsWith("-")))
      throw new Error("Bad skills request.");
    carryExpect(request, input, "Bad skills request.");
    const text = await ctx.main(snapshotIpc(ctx.id)).skills(input, { signal: ctx.signal });
    let doc;
    try {
      doc = JSON.parse(text);
    } catch (_) {
      throw new Error("The skills command did not answer with JSON.");
    }
    if (!isPlainObject(doc) || doc.error || !Array.isArray(doc.skills)) throw refusal(doc, "The skills could not be changed.");
    return doc;
  };
  // The roster, skill-file and groups actions take the requests of the loopback endpoints (/api/roster,
  // /api/skillfile, /api/structure/team) and send them to main as string fields; names holding a space or starting
  // with "-" are refused here and again in main.
  const plainName = (value) => typeof value === "string" && value !== "" && !/\s/.test(value) && !value.startsWith("-");
  const optionalText = (value) => (value === undefined || value === null ? "" : value);
  const askMain = async (method, input, label, valid) => {
    if (!alive) throw new Error("The panel is closed.");
    const text = await ctx.main(snapshotIpc(ctx.id))[method](input, { signal: ctx.signal });
    let doc;
    try {
      doc = JSON.parse(text);
    } catch (_) {
      throw new Error("The " + label + " command did not answer with JSON.");
    }
    if (!isPlainObject(doc) || doc.error || !valid(doc)) throw refusal(doc, "The " + label + " change did not work.");
    return doc;
  };
  const roster = async (request) => {
    if (!alive) throw new Error("The panel is closed.");
    if (!isPlainObject(request) || !["add", "read", "text", "rename", "remove", "restore"].includes(request.op)) throw new Error("Bad roster request.");
    const input = { op: request.op, name: "", record: "", model: "", variant: "", description: "", prompt: "", skills: "", read_only: "", copy_of: "", template: "", to: "" };
    if (request.op === "restore") {
      if (typeof request.record !== "string" || !request.record || request.record.startsWith("-")) throw new Error("Bad roster request.");
      input.record = request.record;
    } else {
      if (!plainName(request.name)) throw new Error("Bad roster request.");
      input.name = request.name;
    }
    if (request.op === "text") {
      if (typeof request.text !== "string") throw new Error("Bad roster request.");
      input.prompt = request.text;
    }
    if (request.op === "rename") {
      if (!plainName(request.to)) throw new Error("Bad roster request.");
      input.to = request.to;
    }
    if (request.op === "add") {
      for (const key of ["model", "variant", "description", "prompt", "copy_of", "template"]) {
        const value = optionalText(request[key]);
        if (typeof value !== "string") throw new Error("Bad roster request.");
        input[key] = value;
      }
      if (input.copy_of && !plainName(input.copy_of)) throw new Error("Bad roster request.");
      const skills = optionalText(request.skills);
      if (skills !== "") {
        if (!Array.isArray(skills) || !skills.every(plainName) || skills.some((name) => name.includes(","))) throw new Error("Bad roster request.");
        input.skills = skills.length ? skills.join(",") : "-";
      }
      const readOnly = optionalText(request.read_only);
      if (readOnly !== "" && typeof readOnly !== "boolean") throw new Error("Bad roster request.");
      input.read_only = readOnly === "" ? "" : String(readOnly);
    }
    carryExpect(request, input, "Bad roster request.");
    return askMain("roster", input, "roster", (doc) => typeof doc.agent === "string");
  };
  const skillfile = async (request) => {
    if (!alive) throw new Error("The panel is closed.");
    if (!isPlainObject(request) || !["read", "write", "create", "delete", "restore"].includes(request.op)) throw new Error("Bad skill request.");
    const input = { op: request.op, id: "", record: "", description: "", text: "", assign: "" };
    if (request.op === "restore") {
      if (typeof request.record !== "string" || !request.record || request.record.startsWith("-")) throw new Error("Bad skill request.");
      input.record = request.record;
    } else {
      if (!plainName(request.id)) throw new Error("Bad skill request.");
      input.id = request.id;
    }
    if (request.op === "write" || request.op === "create") {
      for (const key of ["description", "text", "assign"]) {
        const value = optionalText(request[key]);
        if (typeof value !== "string") throw new Error("Bad skill request.");
        input[key] = value;
      }
      if (input.assign && !plainName(input.assign)) throw new Error("Bad skill request.");
    }
    carryExpect(request, input, "Bad skill request.");
    return askMain("skillfile", input, "skill", (doc) => typeof doc.id === "string");
  };
  const team = async (request) => {
    if (!alive) throw new Error("The panel is closed.");
    if (!isPlainObject(request) || !["add", "members", "text", "remove"].includes(request.op)) throw new Error("Bad group request.");
    const input = { op: request.op, id: "", lead: "", members: "", text: "" };
    if (request.op === "add") {
      if (!plainName(request.lead)) throw new Error("Bad group request.");
      input.lead = request.lead;
    } else {
      if (!plainName(request.id)) throw new Error("Bad group request.");
      input.id = request.id;
    }
    if (request.op === "add" || request.op === "members") {
      if (!Array.isArray(request.members) || !request.members.length || !request.members.every(plainName)) throw new Error("Bad group request.");
      input.members = request.members.join(" ");
    }
    if (request.op === "add" || request.op === "text") {
      const text = optionalText(request.text);
      if (typeof text !== "string") throw new Error("Bad group request.");
      input.text = text;
    }
    carryExpect(request, input, "Bad group request.");
    return askMain("team", input, "groups", (doc) => Array.isArray(doc.teams));
  };
  // A step's output takes the requests of the loopback endpoint (/api/output): {op: "read", task, node, artifact},
  // {op: "write", task, node, artifact, text, expect?} and {op: "restore", record}; the session's folder goes with
  // them, so main finds the task graph the snapshot showed.
  const output = async (request) => {
    if (!alive) throw new Error("The panel is closed.");
    if (!isPlainObject(request) || !["read", "write", "restore"].includes(request.op)) throw new Error("Bad output request.");
    const input = { op: request.op, directory: "", task: "", node: "", artifact: "", record: "", text: "" };
    // The session's folder goes with every op: main finds the task graph from it, and takes an undo back only for a
    // graph found so.
    const where = sessionRequest();
    if (!where || !where.directory) throw new Error("No session is open.");
    input.directory = where.directory;
    if (request.op === "restore") {
      if (typeof request.record !== "string" || !request.record || request.record.startsWith("-")) throw new Error("Bad output request.");
      input.record = request.record;
    } else {
      if (![request.task, request.node, request.artifact].every(plainName)) throw new Error("Bad output request.");
      Object.assign(input, { task: request.task, node: request.node, artifact: request.artifact });
    }
    if (request.op === "write") {
      if (typeof request.text !== "string") throw new Error("Bad output request.");
      input.text = request.text;
    }
    carryExpect(request, input, "Bad output request.");
    return askMain("output", input, "output", (doc) => typeof doc.task === "string");
  };
  // The chat of an agent's session: read through main (`agentgraph chat`), the page asks for one at a time.
  const chat = async (sessionID, request) => {
    if (!alive) throw new Error("The panel is closed.");
    if (typeof sessionID !== "string" || !SESSION_ID.test(sessionID)) throw new Error("Bad chat request.");
    const asked = isPlainObject(request) ? request.limit : undefined;
    const limit = asked === undefined || asked === null ? CHAT_LIMIT_DEFAULT : asked;
    if (!Number.isInteger(limit) || limit < 1 || limit > CHAT_LIMIT_MAX) throw new Error("Bad chat request.");
    const text = await ctx.main(snapshotIpc(ctx.id)).chat({ session: sessionID, limit }, { signal: ctx.signal });
    let doc;
    try {
      doc = JSON.parse(text);
    } catch (_) {
      throw new Error("The chat command did not answer with JSON.");
    }
    if (!isPlainObject(doc)) throw new Error("The chat command did not answer with a chat.");
    if (typeof doc.error === "string" && doc.error) throw new Error(oneLine(doc.error));
    if (typeof doc.session !== "string" || !Array.isArray(doc.items)) throw new Error("The chat command did not answer with a chat.");
    return doc;
  };
  // Asking the side chat: one question about the main chat (the root session of the last snapshot this panel read), asked
  // through main (`agentgraph sidechat ask`), answered { ok: true, answer } or { ok: false, error } (one short line), never
  // thrown. Neither the question nor the answer is written down anywhere here.
  const side = async (text) => {
    try {
      if (!alive) return { ok: false, error: "The panel is closed." };
      if (typeof text !== "string" || !text.trim()) return { ok: false, error: "Write a question first." };
      if (text.includes("\0") || Array.from(text).length > SIDE_MAX_CHARS) return { ok: false, error: "The question is longer than " + SIDE_MAX_CHARS + " characters." };
      if (known.key === undefined || known.key !== sessionKey() || !known.root) return { ok: false, error: "The main chat is not in the agent graph." };
      const reply = await ctx.main(snapshotIpc(ctx.id)).side({ main: known.root, text }, { signal: ctx.signal });
      let doc;
      try {
        doc = JSON.parse(reply);
      } catch (_) {
        return { ok: false, error: "The side chat did not answer with JSON." };
      }
      if (!isPlainObject(doc)) return { ok: false, error: "The side chat did not answer." };
      if (typeof doc.error === "string" && doc.error) return { ok: false, error: oneLine(doc.error) };
      if (typeof doc.answer !== "string") return { ok: false, error: "The side chat did not answer." };
      return { ok: true, answer: doc.answer };
    } catch (error) {
      return { ok: false, error: oneLine(errorMessage(error)) };
    }
  };
  // Steering and stopping use the host's own client (props.session.server.client.session), read where used, and answer
  // { ok: true } or { ok: false, error } (one short line) and never throw. They are refused, without calling the host,
  // for a session that is not in the last snapshot this panel read. Only the id, a count or a short error line is ever
  // written down about them; the message text is not.
  const sessionClient = () => {
    const session = props.session;
    return session && session.server && session.server.client && session.server.client.session;
  };
  const hostFailure = (error) => oneLine(isPlainObject(error) && typeof error.error === "string" ? error.error : errorMessage(error));
  const hostCall = async (name, sessionID, input, failed) => {
    try {
      if (!alive) return { ok: false, error: "The panel is closed." };
      const api = sessionClient();
      if (!api || typeof api[name] !== "function") return { ok: false, error: "This OpenCode does not let a plugin do that." };
      if (!isKnownSession(sessionID)) return { ok: false, error: "That session is not in the agent graph." };
      const result = await api[name](input);
      // A client that answers { error } instead of throwing refuses too.
      if (isPlainObject(result) && result.error) return { ok: false, error: failed + oneLine(isPlainObject(result.error) ? hostFailure(result.error) : result.error) };
      return { ok: true };
    } catch (error) {
      return { ok: false, error: failed + hostFailure(error) };
    }
  };
  const send = async (sessionID, text, request) => {
    if (typeof sessionID !== "string" || !SESSION_ID.test(sessionID)) return { ok: false, error: "The session id is not valid." };
    if (typeof text !== "string" || !text.trim()) return { ok: false, error: "Write a message first." };
    if (Array.from(text).length > CHAT_SEND_MAX_CHARS) return { ok: false, error: "The message is longer than " + CHAT_SEND_MAX_CHARS + " characters." };
    const input = isPlainObject(request) && request.steer === true ? { sessionID, text, delivery: "steer" } : { sessionID, text };
    return hostCall("prompt", sessionID, input, "The message could not be sent: ");
  };
  const stop = async (sessionID) => {
    if (typeof sessionID !== "string" || !SESSION_ID.test(sessionID)) return { ok: false, error: "The session id is not valid." };
    return hostCall("interrupt", sessionID, { sessionID }, "The session could not be stopped: ");
  };
  // The page shows a control only for an action it is given: send and stop only where the host has the call.
  const demoMode = ctx.options && ctx.options.demo === true;
  const hostHas = (name) => typeof sessionClient()?.[name] === "function";
  const actions = demoMode ? { chat } : { flow, skills, roster, skillfile, team, output, run, chat, side };
  if (!demoMode && hostHas("prompt")) actions.send = send;
  if (!demoMode && hostHas("interrupt")) actions.stop = stop;

  const showSessionToast = (sessionID, copied) => {
    try {
      const toast = ctx.toast;
      if (typeof toast?.show !== "function") return false;
      const key = copied ? "message.sessionCopied" : "message.sessionID";
      const title = typeof ctx.t === "function" ? ctx.t(key) : MESSAGES[key];
      const result = toast.show(copied ? { title } : { title, description: sessionID });
      Promise.resolve(result).catch(() => {});
      return true;
    } catch (_) {
      return false;
    }
  };

  const copyOrShowSessionID = (sessionID) => {
    try {
      const system = ctx.system;
      if (typeof system?.copy === "function") {
        const result = system.copy(sessionID);
        if (result && typeof result.then === "function") {
          Promise.resolve(result).then(() => showSessionToast(sessionID, true), () => showSessionToast(sessionID, false));
        } else {
          showSessionToast(sessionID, true);
        }
        return true;
      }
    } catch (_) {
      // If copying fails, still offer the ID in a toast.
    }
    return showSessionToast(sessionID, false);
  };

  const onOpenSession = (selection) => {
    try {
      const sessionID = selection?.sessionID;
      if (!alive || typeof sessionID !== "string" || !SESSION_ID.test(sessionID)) return false;
      const session = props.session;
      if (session?.id === sessionID) return false;
      try {
        const server = session?.server?.id;
        const tabs = ctx.tabs;
        if (typeof server === "string" && server && typeof tabs?.open === "function") {
          const result = tabs.open({ server, id: sessionID }, { focus: false });
          Promise.resolve(result).catch(() => { if (alive) copyOrShowSessionID(sessionID); });
          return true;
        }
      } catch (_) {
        // This host could not open the tab; try the clipboard and toast instead.
      }
      return copyOrShowSessionID(sessionID);
    } catch (_) {
      return false;
    }
  };

  const view = loadView();
  const interval = Number(ctx.options && ctx.options.intervalMs);
  const intervalMs = Number.isFinite(interval) && interval >= 250 ? interval : DEFAULT_INTERVAL_MS;
  const mountView = () => {
    const target = document.createElement("div");
    target.className = "agentgraph-desktop-view";
    host.appendChild(target);
    try {
      handle = view.mount(target, { fetchSnapshot, intervalMs, onOpenSession, actions });
    } catch (error) {
      host.removeChild(target);
      showMessage(host, ctx.t("message.viewFailed", { error: oneLine(errorMessage(error)) }));
    }
  };
  const destroyView = () => {
    const current = handle;
    handle = null;
    if (current && typeof current.destroy === "function") {
      try {
        current.destroy();
      } catch (_) {
        // The view is going away either way.
      }
    }
    host.replaceChildren();
  };
  if (!view || typeof view.mount !== "function") {
    showMessage(host, viewFailure ? ctx.t("message.viewFailed", { error: oneLine(errorMessage(viewFailure)) }) : ctx.t("message.noView"));
  } else {
    mountView();
  }

  // Drop the old session's view state before fetching for the new one.
  let shown = sessionKey();
  const follow = (key) => {
    if (key === shown) return;
    shown = key;
    Promise.resolve()
      .then(() => {
        if (!alive || key !== shown || !handle) return;
        if (typeof handle.reset === "function") return handle.reset();
        destroyView();
        mountView();
      })
      .catch(() => {});
  };
  if (typeof gui.createKeyed === "function") {
    gui.createKeyed(sessionKey, follow, { otherwise: () => follow(undefined) });
  } else if (solid && typeof solid.createEffect === "function") {
    solid.createEffect(() => follow(sessionKey()));
  }

  const cleanup = () => {
    if (!alive) return;
    alive = false;
    destroyView();
  };
  // render runs under the panel's Solid owner: onCleanup runs when the tab's content unmounts.
  if (solid && typeof solid.onCleanup === "function") solid.onCleanup(cleanup);

  return host;
}

function setup(ctx) {
  const solid = require("solid-js");
  const Panel = gui.Panel;
  const MenuItem = gui.MenuItem;
  const Command = gui.Command;
  const Style = gui.Style;
  const key = ctx.id + ":" + TAB_ID;
  const icon = ctx.icons && ctx.icons.graph;

  ctx.add(Style, HOST_CSS + "\n" + (ASSETS.css || ""));

  // The host keeps tabs from list stable: return the same object, labels from getters.
  const tab = {
    id: TAB_ID,
    get title() {
      return ctx.t("tab.title");
    },
    closable: "compact",
    fallback: true,
  };

  const panel = {
    id: PANEL_ID,
    region: "side",
    list: (input) => (input && Array.isArray(input.open) && input.open.includes(TAB_ID) ? [tab] : []),
    render: (props) => renderPanel(ctx, solid, props),
  };
  ctx.add(Panel, panel);

  const open = () => {
    const session = ctx.sessions.current();
    if (session) ctx.layout.open(key, session);
  };

  ctx.add(MenuItem, () => {
    const session = ctx.sessions.current();
    const item = {
      menu: "session.panel",
      id: MENU_ID,
      title: ctx.t("menu.title"),
      keybind: ctx.id + "." + COMMAND_ID,
      opened: !!session && ctx.layout.state(key, session) !== "closed",
      order: 40,
      run: open,
    };
    if (icon) item.icon = icon;
    return item;
  });

  ctx.add(Command, () => ({
    id: COMMAND_ID,
    title: ctx.t("command.open"),
    keywords: "agents agentgraph orchestration subagents graph",
    enabled: !!ctx.sessions.current(),
    run: open,
  }));
}

// ---------------------------------------------------------------------------------------------------------------

const definition = gui.Plugin.define({
  icons: { graph: GRAPH_ICON },
  i18n: { en: MESSAGES },
  setup,
  main,
});

// The host reads `Object.hasOwn(module.exports, "default") ? module.exports.default : module.exports`.
// Exporting the definition itself, with a non-enumerable own `default` pointing back at it, satisfies both readings
// (and the docs' "default-exports Plugin.define") without adding an enumerable key to the definition.
module.exports = definition;
try {
  Object.defineProperty(module.exports, "default", { value: definition, enumerable: false, configurable: true });
} catch (_) {
  // A frozen definition: the host falls back to module.exports itself.
}
