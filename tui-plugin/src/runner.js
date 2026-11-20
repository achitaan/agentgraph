/*
 * Runs `<python> -P -m agentgraph.snapshot_json --session=<id>` without a shell, like the Desktop plugin's
 * createSnapshotRunner (desktop-plugin/src/plugin.cjs): absolute interpreter path, PYTHONPATH = the orchestration
 * folder, SIGKILL after the timeout, output capped, one process per session at a time, and every failure turned
 * into a one-line error document { version: 1, error }. snapshot() never rejects.
 */

export const SNAPSHOT_TIMEOUT_MS = 20000;
export const SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024;
const ERROR_MAX_CHARS = 300;
const SESSION_ID = /^[A-Za-z0-9_][A-Za-z0-9_-]{0,127}$/;

export function oneLine(value) {
  const text = String(value === undefined || value === null ? "" : value)
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return "unknown error";
  return text.length > ERROR_MAX_CHARS ? text.slice(0, ERROR_MAX_CHARS - 1) + "…" : text;
}

export function errorMessage(error) {
  if (error && typeof error === "object" && typeof error.message === "string") return error.message;
  return String(error);
}

export function errorDocument(message) {
  return { version: 1, error: oneLine(message) };
}

function isPlainObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function safeParse(text) {
  try {
    return JSON.parse(text);
  } catch (_) {
    return undefined;
  }
}

export function parseDocument(text) {
  if (typeof text !== "string" || !text.trim()) return errorDocument("The snapshot command printed nothing.");
  const doc = safeParse(text);
  if (doc === undefined) {
    return errorDocument("The snapshot command returned invalid JSON. Check that options.python is Python 3.11 or newer.");
  }
  if (!isPlainObject(doc)) return errorDocument("The snapshot is not a JSON object.");
  if (doc.version !== 1) return errorDocument("Unsupported snapshot version: " + JSON.stringify(doc.version));
  if (doc.error !== undefined && doc.error !== null) doc.error = oneLine(doc.error);
  return doc;
}

function lastLine(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .pop();
}

// deps: { spawn, fs, path, env, home, python, timeoutMs?, maxBytes?, demo? }
export function createSnapshotRunner(deps) {
  const timeoutMs = deps.timeoutMs || SNAPSHOT_TIMEOUT_MS;
  const maxBytes = deps.maxBytes || SNAPSHOT_MAX_BYTES;
  const inflight = new Map(); // session -> Promise<doc>
  const children = new Map(); // child process -> kill(reason)
  let stopped = false;

  function directoryFor(input) {
    const candidate = input && input.directory;
    if (typeof candidate === "string" && candidate && !candidate.includes("\0") && deps.path.isAbsolute(candidate)) {
      try {
        if (deps.fs.statSync(candidate).isDirectory()) return candidate;
      } catch (_) {
        /* fall through */
      }
    }
    return deps.home;
  }

  function processFailure(error, stderr) {
    if (error.code === "ENOENT") return "Python was not found at " + deps.python + ". Set options.python to a Python 3.11 or newer executable.";
    if (error.code === "EACCES") return "Python is not executable: " + deps.python + ".";
    if (/Unknown option:[ \t]+-P\b/i.test(stderr || "") || /unknown option -P\b/i.test(stderr || ""))
      return "Python 3.11 or newer is required; " + deps.python + " does not support -P. Set options.python.";
    return null;
  }

  function finish(outcome, stdout, stderr) {
    if (outcome.reason === "stopped") return errorDocument("The plugin stopped.");
    if (outcome.reason === "timeout") return errorDocument("The snapshot command did not finish within " + Math.round(timeoutMs / 1000) + " s.");
    if (outcome.reason === "overflow") return errorDocument("The snapshot was larger than " + Math.round(maxBytes / 1048576) + " MiB.");
    if (outcome.error) {
      const failure = processFailure(outcome.error, stderr);
      return errorDocument(failure || "Could not start Python: " + errorMessage(outcome.error));
    }
    const ok = outcome.code === 0;
    if (!ok) {
      const failure = processFailure({}, stderr);
      if (failure) return errorDocument(failure);
    }
    const last = lastLine(stdout);
    if (ok) return last ? parseDocument(last) : errorDocument(lastLine(stderr) || "The snapshot command printed nothing.");
    // The command prints its own error document when it fails ({"version": 1, "error": ...}, exit 1): keep it.
    const parsed = last ? safeParse(last) : undefined;
    if (isPlainObject(parsed) && parsed.version === 1 && parsed.error !== undefined && parsed.error !== null) return parseDocument(last);
    const status = typeof outcome.code === "number" ? "exited with status " + outcome.code
      : outcome.signal ? "was terminated by " + outcome.signal : "failed";
    const detail = lastLine(stderr) || "Check options.python and options.home.";
    return errorDocument("The snapshot command " + status + ": " + detail);
  }

  function run(session, cwd) {
    return new Promise((resolve) => {
      const args = ["-P", "-m", "agentgraph.snapshot_json"];
      if (deps.demo) args.push("--demo");
      if (session) args.push("--session=" + session);
      const env = Object.assign({}, deps.env, { PYTHONPATH: deps.home, PYTHONIOENCODING: "utf-8" });
      const out = [];
      const err = [];
      let outBytes = 0;
      let errBytes = 0;
      let settled = false;
      let reason = null;
      let child;
      let timer = null;

      const done = (outcome) => {
        if (settled) return;
        settled = true;
        if (timer) clearTimeout(timer);
        if (child) children.delete(child);
        let doc;
        try {
          doc = finish(reason ? { reason } : outcome, Buffer.concat(out).toString("utf8"), Buffer.concat(err).toString("utf8"));
        } catch (failure) {
          doc = errorDocument(errorMessage(failure));
        }
        resolve(doc);
      };
      const kill = (why) => {
        if (!reason) reason = why;
        try {
          child.kill("SIGKILL");
        } catch (_) {
          /* already gone */
        }
      };

      try {
        child = deps.spawn(deps.python, args, { cwd, env, stdio: ["ignore", "pipe", "pipe"], windowsHide: true, shell: false });
      } catch (error) {
        done({ error });
        return;
      }
      children.set(child, kill);
      timer = setTimeout(() => kill("timeout"), timeoutMs);
      if (child.stdout) {
        child.stdout.on("data", (chunk) => {
          outBytes += chunk.length;
          if (outBytes > maxBytes) kill("overflow");
          else out.push(chunk);
        });
      }
      if (child.stderr) {
        child.stderr.on("data", (chunk) => {
          errBytes += chunk.length;
          if (errBytes > maxBytes) kill("overflow");
          else err.push(chunk);
        });
      }
      child.on("error", (error) => done({ error }));
      child.on("close", (code, signal) => done({ code, signal }));
      if (stopped) kill("stopped");
    });
  }

  return {
    // input: { session, directory }. Never rejects.
    async snapshot(input) {
      try {
        if (stopped) return errorDocument("The plugin stopped.");
        if (typeof deps.python !== "string" || !deps.path.isAbsolute(deps.python))
          return errorDocument("options.python must be an absolute path to Python 3.11 or newer: " + String(deps.python));
        const session = input && typeof input.session === "string" ? input.session : "";
        if (session && !SESSION_ID.test(session)) return errorDocument("The session id is not valid.");
        let pending = inflight.get(session);
        if (!pending) {
          pending = run(session, directoryFor(input)).finally(() => {
            if (inflight.get(session) === pending) inflight.delete(session);
          });
          inflight.set(session, pending);
        }
        return await pending;
      } catch (error) {
        return errorDocument("Could not collect the snapshot: " + errorMessage(error));
      }
    },
    // Kill every running child; later calls return an error document at once.
    stop() {
      stopped = true;
      for (const kill of [...children.values()]) kill("stopped");
    },
    running: () => children.size,
    inflight: () => inflight.size,
  };
}

// ---------------------------------------------------------------------------------------------------------------
// The run message of a path: `<python> -P -m agentgraph.structure flow prompt ID --goal-file -` (what
// `agentgraph flow prompt ID --goal-file -` runs), the goal on standard input, no shell, the same interpreter,
// PYTHONPATH and timeout rules as the snapshot. Resolves { text } or { error } (one line); never rejects.

export const FLOW_PROMPT_TIMEOUT_MS = 20000;
export const FLOW_PROMPT_MAX_BYTES = 256 * 1024;
export const GOAL_LIMIT = 4000; // characters (the back end's flowrun.GOAL_LIMIT)
const FLOW_ID = /^[a-z0-9][a-z0-9-]{0,79}$/;
const FLOW_REV = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/; // render.js FLOW_REV
const STALE = /changed since you opened it/;

// deps: { spawn, path, env, home, python, timeoutMs?, structure? } (structure: a structure folder, for tests)
// -> {text} | {error} | {error, stale: true} (`--expect rev` refused: the path changed since the snapshot showed it).
export function runFlowPrompt(deps, id, goal, rev) {
  const timeoutMs = deps.timeoutMs || FLOW_PROMPT_TIMEOUT_MS;
  const fail = (message) => ({ error: oneLine(message) });
  if (typeof deps.python !== "string" || !deps.path.isAbsolute(deps.python)) {
    return Promise.resolve(fail("options.python must be an absolute path to Python 3.11 or newer: " + String(deps.python)));
  }
  if (typeof id !== "string" || !FLOW_ID.test(id)) return Promise.resolve(fail("The path's id is not valid."));
  if (typeof goal !== "string" || !goal.trim()) return Promise.resolve(fail("The goal is empty."));
  return new Promise((resolve) => {
    const args = ["-P", "-m", "agentgraph.structure", "flow", "prompt", id, "--goal-file", "-"];
    if (deps.structure) args.push("--structure", deps.structure);
    if (typeof rev === "string" && FLOW_REV.test(rev)) args.push("--expect", rev);
    const env = Object.assign({}, deps.env, { PYTHONPATH: deps.home, PYTHONIOENCODING: "utf-8" });
    const out = [];
    const err = [];
    let outBytes = 0;
    let errBytes = 0;
    let settled = false;
    let reason = null;
    let child;
    let timer = null;
    const finish = (outcome) => {
      const stdout = Buffer.concat(out).toString("utf8");
      const stderr = Buffer.concat(err).toString("utf8");
      if (reason === "timeout") return fail("agentgraph flow prompt did not finish within " + Math.round(timeoutMs / 1000) + " s.");
      if (reason === "overflow") return fail("agentgraph flow prompt printed more than " + Math.round(FLOW_PROMPT_MAX_BYTES / 1024) + " KiB.");
      if (outcome.error) {
        if (outcome.error.code === "ENOENT") return fail("Python was not found at " + deps.python + ". Set options.python.");
        return fail("Could not start Python: " + errorMessage(outcome.error));
      }
      if (outcome.code === 0 && stdout.trim()) return { text: stdout.replace(/\s+$/, "") };
      const detail = lastLine(stderr) || "";
      if (/No module named ['"]?agentgraph\.structure/.test(stderr) || /invalid choice: '(flow|prompt)'/.test(stderr) || /unrecognized arguments: --goal-file/.test(stderr)) {
        return fail("This agentgraph has no `flow prompt` command yet; update the orchestration folder.");
      }
      if (outcome.code === 0) return fail("agentgraph flow prompt printed nothing.");
      if (STALE.test(stderr)) return Object.assign(fail(detail.replace(/^agentgraph structure:\s*/, "")), { stale: true });
      const status = typeof outcome.code === "number" ? "exited with status " + outcome.code : outcome.signal ? "was terminated by " + outcome.signal : "failed";
      return fail(detail ? detail.replace(/^agentgraph structure:\s*/, "") : "agentgraph flow prompt " + status + ".");
    };
    const done = (outcome) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      let result;
      try {
        result = finish(outcome);
      } catch (failure) {
        result = fail(errorMessage(failure));
      }
      resolve(result);
    };
    const kill = (why) => {
      if (!reason) reason = why;
      try {
        child.kill("SIGKILL");
      } catch (_) {
        /* already gone */
      }
    };
    try {
      child = deps.spawn(deps.python, args, { cwd: deps.home, env, stdio: ["pipe", "pipe", "pipe"], windowsHide: true, shell: false });
    } catch (error) {
      done({ error });
      return;
    }
    timer = setTimeout(() => kill("timeout"), timeoutMs);
    const collect = (chunks, add) => (chunk) => {
      if (add(chunk.length) > FLOW_PROMPT_MAX_BYTES) kill("overflow");
      else chunks.push(chunk);
    };
    if (child.stdout) child.stdout.on("data", collect(out, (n) => (outBytes += n)));
    if (child.stderr) child.stderr.on("data", collect(err, (n) => (errBytes += n)));
    child.on("error", (error) => done({ error }));
    child.on("close", (code, signal) => done({ code, signal }));
    if (child.stdin) {
      child.stdin.on("error", () => {
        /* the command stopped reading: its exit status says why */
      });
      child.stdin.end(goal, "utf8");
    }
  });
}
