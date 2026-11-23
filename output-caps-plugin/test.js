// No-network test of index.js with a fake OpenCode context and made-up tool results: `node output-caps-plugin/test.js`.
"use strict";
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "output-caps-test-"));
let n = 0;

// A plugin copy with its own settings file and log folder, and a fake context that keeps the registered hook.
async function harness(settings = {}, { rawSettings = null, noSettings = false, hookFails = false } = {}) {
  const dir = path.join(root, `case${n++}`);
  const home = path.join(dir, "home");
  process.env.AGENTGRAPH_OUTPUT_CAPS_HOME = home;
  delete process.env.AGENTGRAPH_OUTPUT_CAPS;
  fs.mkdirSync(path.join(dir, "plugin"), { recursive: true });
  fs.copyFileSync(path.join(__dirname, "index.js"), path.join(dir, "plugin", "index.js"));
  if (!noSettings) fs.writeFileSync(path.join(dir, "plugin", "output-caps.json"), rawSettings !== null ? rawSettings : JSON.stringify(settings));
  const h = { dir, home, registered: [], disposed: 0 };
  const ctx = { tool: { hook: async (name, fn) => {
    if (hookFails) throw new Error("no hook");
    h.registered.push(name); h.fn = fn;
    return { dispose: () => { h.disposed += 1; } };
  } } };
  const plugin = require(path.join(dir, "plugin", "index.js"));
  h.plugin = plugin;
  h.dispose = await plugin.setup(ctx);
  // fire the hook with a completed result; returns the event after the hook ran
  h.run = (tool, result, extra = {}) => { const event = { tool, sessionID: "ses_test1", input: {}, status: "completed", result, ...extra }; h.fn(event); return event; };
  h.rows = () => { try { return fs.readFileSync(path.join(home, "output-caps.log"), "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)); } catch { return []; } };
  return h;
}

// made-up numbered lines like a file read: "00001| line text ..." , each of `width` characters including the newline
const lines = (count, width = 50) => Array.from({ length: count }, (_, i) => (String(i + 1).padStart(5, "0") + "| " + "x".repeat(width)).slice(0, width - 1) + "\n").join("");
const chars = (s) => (typeof s === "string" ? s : s.filter((p) => p.type === "text").map((p) => p.text).join("")).length;
const GAP = /\[(\d+) characters \((\d+) lines\) were left out here/;

const cases = {
  async "registers one execute.after hook and returns a dispose"() {
    const h = await harness();
    assert.deepEqual(h.registered, ["execute.after"]); assert.equal(typeof h.dispose, "function"); h.dispose();
  },
  async "string content over the cap is cut: head and tail kept, one gap line"() {
    const h = await harness();
    const text = lines(1000);                                   // 50,000 characters, over the 20,000 of an ordinary tool
    const e = h.run("shell", { content: text });
    assert.equal(typeof e.result.content, "string");
    assert.ok(e.result.content.length < 20500 && e.result.content.length > 19000, String(e.result.content.length));
    assert.ok(e.result.content.startsWith("00001| ")); assert.ok(e.result.content.endsWith("01000| " + "x".repeat(42) + "\n"));
    assert.equal(e.result.content.split("\n").filter((l) => GAP.test(l)).length, 1, "exactly one gap line");
    assert.match(e.result.content, /narrower command or filter the output/); assert.doesNotMatch(e.result.content, /offset and limit/);
  },
  async "list content over the cap is cut; the first text part holds the result"() {
    const h = await harness();
    const e = h.run("grep", { content: [{ type: "text", text: lines(1000) }] });
    assert.ok(Array.isArray(e.result.content) && e.result.content.length === 1 && e.result.content[0].type === "text");
    assert.ok(chars(e.result.content) < 20500); assert.match(e.result.content[0].text, GAP);
  },
  async "under the cap and exactly at the cap: untouched"() {
    const h = await harness();
    for (const size of [0, 100, 19999, 20000]) {
      const result = { content: "a".repeat(size) }; const e = h.run("shell", result);
      assert.strictEqual(e.result, result, "same object for size " + size);
    }
    const result = { content: [{ type: "text", text: "a".repeat(20000) }] }; assert.strictEqual(h.run("shell", result).result, result);
    assert.deepEqual(h.rows(), []);
  },
  async "one character over the cap: cut only when that makes the result shorter"() {
    const h = await harness();
    for (const text of ["a".repeat(20001), lines(401, 50) , "a\n".repeat(10001)]) {      // one long line; just over the cap on whole lines; many short lines
      const e = h.run("shell", { content: text }); assert.ok(chars(e.result.content) <= text.length, "never longer than the original");
    }
    const one = { content: "a".repeat(20001) }; assert.strictEqual(h.run("shell", one).result, one, "one line with no newline: nothing worth cutting is left alone");
  },
  async "read has 40,000 and the others 20,000; the gap line for read names offset and limit"() {
    const h = await harness();
    const text = lines(700);                                            // 35,000 characters
    const r = h.run("read", { content: text }); assert.strictEqual(r.result.content, text, "read at 35,000 is under its cap");
    const o = h.run("shell", { content: text }); assert.notStrictEqual(o.result.content, text); assert.ok(o.result.content.length < 20500);
    const big = h.run("read", { content: lines(1500) });                // 75,000 characters
    assert.ok(big.result.content.length < 40500 && big.result.content.length > 38000, String(big.result.content.length));
    assert.match(big.result.content, /Read the file again with offset and limit/); assert.doesNotMatch(big.result.content, /narrower command/);
    const lower = h.run("READ", { content: lines(1500) }); assert.match(lower.result.content, /offset and limit/, "tool name matched without case");
  },
  async "the cut is on line boundaries: every kept line is a whole numbered line"() {
    const h = await harness();
    const e = h.run("read", { content: lines(2000, 73) });
    const kept = e.result.content.split("\n").filter(Boolean);
    const body = kept.filter((l) => !GAP.test(l));
    assert.ok(body.every((l) => /^\d{5}\| x+$/.test(l) && l.length === 72), "no line cut in the middle");
    assert.equal(kept.length - body.length, 1);
    // the numbers are the first lines and the last lines, and the gap line's counts add up
    const m = e.result.content.match(GAP); const left = Number(m[2]);
    assert.equal(body.length + left, 2000); assert.equal(Number(m[1]), left * 73);
    assert.equal(body[0].slice(0, 5), "00001"); assert.equal(body[body.length - 1].slice(0, 5), "02000");
  },
  async "head is about 70% and tail about 30% (setting head_share)"() {
    const h = await harness();
    const e = h.run("shell", { content: lines(2000) }); const all = e.result.content.split("\n");
    const gap = all.findIndex((l) => GAP.test(l));
    const headChars = all.slice(0, gap).join("\n").length, tailChars = all.slice(gap + 1).join("\n").length;
    assert.ok(Math.abs(headChars / (headChars + tailChars) - 0.7) < 0.03, `${headChars} ${tailChars}`);
    const h2 = await harness({ head_share: 0.5 }); const e2 = h2.run("shell", { content: lines(2000) }); const a2 = e2.result.content.split("\n");
    const g2 = a2.findIndex((l) => GAP.test(l)); const hc = a2.slice(0, g2).join("\n").length, tc = a2.slice(g2 + 1).join("\n").length;
    assert.ok(Math.abs(hc / (hc + tc) - 0.5) < 0.03);
  },
  async "several text parts: every part stays in place and in order; the cap applies to their total, each part cut by its share"() {
    const h = await harness();
    const parts = [{ type: "text", text: lines(200), id: "a" }, { type: "text", text: lines(200), id: "b" }, { type: "text", text: lines(200), id: "c" }];   // three of 10,000
    const e = h.run("shell", { content: parts });
    assert.equal(e.result.content.length, 3); assert.deepEqual(e.result.content.map((p) => p.id), ["a", "b", "c"], "order and other fields kept");
    for (const p of e.result.content) { assert.equal(p.type, "text"); assert.equal(p.text.split("\n").filter((l) => GAP.test(l)).length, 1, "one gap line a cut part"); assert.ok(p.text.startsWith("00001| ")); }
    assert.ok(chars(e.result.content) < 20000 + 3 * 200 && chars(e.result.content) > 18000, String(chars(e.result.content)));
    assert.equal(e.result.metadata.capped.original, 30000); assert.equal(e.result.metadata.capped.kept, chars(e.result.content));
    const under = [{ type: "text", text: lines(100) }, { type: "text", text: lines(100) }];
    const r = { content: under }; assert.strictEqual(h.run("shell", r).result, r, "two parts of 5,000 each are under the cap");
  },
  async "parts are cut in proportion to their length, and none is fused with another"() {
    const h = await harness();
    const big = "b".repeat(3000) + "\n" + "c".repeat(60000), small = "no newline at the end of this part " + "d".repeat(1200);   // no part ends with a newline
    const e = h.run("shell", { content: [{ type: "text", text: big }, { type: "text", text: small }] });
    assert.equal(e.result.content.length, 2);
    const [x, y] = e.result.content.map((p) => p.text);
    assert.ok(x.length < big.length / 2 && y.length < small.length, "both over their share are cut");
    assert.ok(x.startsWith("bbb") && !x.includes("no newline at the end"), "the first part holds only its own text");
    assert.ok(y.startsWith("no newline at the end") && y.endsWith("d") && !y.includes("c".repeat(100)), "the second part holds only its own text");
    assert.ok(x.length > y.length * 5, "the longer part keeps more");
  },
  async "a file part beside capped text is left alone, in its place"() {
    const h = await harness();
    const file = { type: "file", uri: "data:image/png;base64,AAAA", mime: "image/png", name: "a.png" };
    const tail = { type: "text", text: "tail text" };
    const e = h.run("shell", { content: [{ type: "text", text: lines(1000) }, file, tail] });
    assert.equal(e.result.content.length, 3); assert.strictEqual(e.result.content[1], file); assert.strictEqual(e.result.content[2], tail, "a small part is not touched");
    assert.equal(e.result.content[0].type, "text"); assert.ok(e.result.content[0].text.length < 20500);
    const two = h.run("shell", { content: [{ type: "text", text: lines(600) }, file, { type: "text", text: lines(600) }] });
    assert.deepEqual(two.result.content.map((p) => p.type), ["text", "file", "text"]); assert.strictEqual(two.result.content[1], file);
    assert.ok(two.result.content[0].text.length < 12000 && two.result.content[2].text.length < 12000);
    const onlyFile = { content: [file] }; assert.strictEqual(h.run("read", onlyFile).result, onlyFile);
    const big = { type: "file", uri: "data:application/pdf;base64," + "A".repeat(200000), mime: "application/pdf" };
    const pdf = { content: [big] }; assert.strictEqual(h.run("read", pdf).result, pdf, "a large file part is never cut");
  },
  async "one long line with no newline: cut by characters, within the cap plus the gap line, head budget used"() {
    const h = await harness();
    const e = h.run("shell", { content: "x".repeat(100000) });
    const c = e.result.content; const gapAt = c.search(/\[\d+ characters/);
    assert.ok(c.length <= 20000 + 200, String(c.length)); assert.equal(c.slice(0, gapAt), "x".repeat(14000) + "\n", "70% of the cap kept at the start");
    assert.equal(c.split("\n").filter((l) => GAP.test(l)).length, 1); assert.equal(c.slice(c.indexOf("\n", gapAt) + 1), "x".repeat(6000), "30% kept at the end");
    const r = h.run("read", { content: "y".repeat(300000) }); assert.ok(r.result.content.length <= 40000 + 200);
  },
  async "a short line then one very long line: the head budget is not wasted"() {
    const h = await harness();
    const e = h.run("shell", { content: "short first line\n" + "y".repeat(100000) });
    const c = e.result.content; const gapAt = c.search(/\[\d+ characters/);
    assert.ok(c.length <= 20000 + 200, String(c.length)); assert.ok(gapAt > 13000, "head holds about 14,000 characters, not just the short line: " + gapAt);
    assert.ok(c.startsWith("short first line\nyyy")); assert.ok(c.endsWith("y".repeat(5000)));
    const tailOnly = h.run("shell", { content: "z".repeat(100000) + "\nlast short line\n" });   // a very long line then a short one
    assert.ok(tailOnly.result.content.length <= 20000 + 200); assert.ok(tailOnly.result.content.endsWith("last short line\n"));
    assert.ok(tailOnly.result.content.endsWith("z".repeat(5000) + "\nlast short line\n") || tailOnly.result.content.length > 19000);
  },
  async "a cut inside a long line never splits a surrogate pair"() {
    const h = await harness({ other_chars: 20001 });
    const lone = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
    const E = "\u{1F600}";
    const texts = [E.repeat(200000), "a" + E.repeat(200000), E.repeat(200000) + "a", "ab" + E.repeat(200000) + "c", "x\n" + E.repeat(200000)];
    let cutOnes = 0;
    for (const text of texts) {
      for (const tool of ["shell", "read"]) {
        const e = h.run(tool, { content: text }); assert.notStrictEqual(typeof e.result.content, "undefined");
        assert.ok(e.result.content.length < text.length, "cut"); cutOnes += 1;
        assert.ok(!lone.test(e.result.content), "no lone surrogate in the capped text");
        const asParts = h.run(tool, { content: [{ type: "text", text }] }); assert.ok(!lone.test(asParts.result.content[0].text));
      }
    }
    assert.equal(cutOnes, 10);
    // every cut point of a head budget lands on a pair edge or is stepped off it
    for (let cap = 1000; cap < 1010; cap++) {
      const g = await harness({ other_chars: cap });
      for (const text of [E.repeat(5000), "a" + E.repeat(5000), E.repeat(5000) + "a"]) assert.ok(!lone.test(g.run("shell", { content: text }).result.content), `cap ${cap}`);
    }
  },
  async "an error result is untouched"() {
    const h = await harness();
    const event = { tool: "shell", sessionID: "ses_test1", status: "error", error: { message: "x".repeat(50000) }, result: { content: "y".repeat(50000) } };
    const before = JSON.stringify(event); h.fn(event); assert.equal(JSON.stringify(event), before);
    const e2 = { tool: "shell", status: "pending", result: { content: "y".repeat(50000) } }; h.fn(e2); assert.equal(e2.result.content.length, 50000);
  },
  async "each exempt tool is untouched, however long"() {
    const h = await harness();
    for (const tool of ["edit", "write", "question", "skill", "subagent", "task_status", "task_message", "task_reply", "task_cancel", "interview_submit_state"]) {
      const result = { content: lines(3000) }; assert.strictEqual(h.run(tool, result).result, result, tool);
    }
    const h2 = await harness({ exempt_tools: ["shell"] });
    const r = { content: lines(3000) }; assert.strictEqual(h2.run("shell", r).result, r, "the list is a setting");
    assert.ok(h2.run("edit", { content: lines(3000) }).result.content.length < 20500, "an edit result is no longer exempt when the list drops it");
  },
  async "other result fields are kept; metadata.capped holds the two sizes"() {
    const h = await harness();
    const e = h.run("shell", { output: { a: 1 }, content: lines(1000), metadata: { exit: 0 } });
    assert.deepEqual(e.result.output, { a: 1 }); assert.equal(e.result.metadata.exit, 0);
    assert.deepEqual(e.result.metadata.capped, { original: 50000, kept: e.result.content.length });
    const e2 = h.run("shell", { content: lines(1000) }); assert.equal(e2.result.metadata.capped.original, 50000);
    const e3 = h.run("shell", { content: lines(1000), metadata: "odd" }); assert.equal(e3.result.metadata, "odd", "a metadata that is not an object is left alone");
  },
  async "empty content with a long string output: the output is capped (OpenCode would use it as the text)"() {
    const h = await harness();
    for (const content of [undefined, [], ""]) {
      const e = h.run("shell", { output: lines(1000), content }); assert.ok(e.result.output.length < 20500, String(content)); assert.match(e.result.output, GAP);
      assert.strictEqual(e.result.content, content, "the empty content is left as it was"); assert.equal(e.result.metadata.capped.original, 50000);
    }
    const shortOut = { output: "short", content: "" }; assert.strictEqual(h.run("shell", shortOut).result, shortOut, "a short output is left alone");
    const w = { output: { big: "x".repeat(90000) } }; assert.strictEqual(h.run("shell", w).result, w, "an output that is not a string is left alone");
    const keep = { output: lines(1000), content: "short" }; assert.strictEqual(h.run("shell", keep).result, keep, "output is not the text when content has some");
  },
  async "off switch: the environment variable and enabled:false"() {
    const h = await harness();
    process.env.AGENTGRAPH_OUTPUT_CAPS = "off";
    try { const r = { content: lines(3000) }; assert.strictEqual(h.run("shell", r).result, r); process.env.AGENTGRAPH_OUTPUT_CAPS = "OFF"; assert.strictEqual(h.run("shell", r).result, r); }
    finally { delete process.env.AGENTGRAPH_OUTPUT_CAPS; }
    assert.ok(h.run("shell", { content: lines(3000) }).result.content.length < 20500, "back on without the variable");
    const off = await harness({ enabled: false }); const r = { content: lines(3000) }; assert.strictEqual(off.run("shell", r).result, r);
    assert.deepEqual(off.rows(), []);
  },
  async "missing settings file: the defaults are used"() {
    const h = await harness({}, { noSettings: true });
    assert.ok(h.run("read", { content: lines(1500) }).result.content.length < 40500);
    assert.ok(h.run("shell", { content: lines(500) }).result.content.length < 20500);
  },
  async "invalid settings: bad keys fall back to the defaults, good keys stay, one log line says which"() {
    const h = await harness({ read_chars: "lots", other_chars: 5000, head_share: 7, exempt_tools: "edit", enabled: "yes", log_dir: "relative" });
    const e = h.run("shell", { content: lines(500) }); assert.ok(e.result.content.length < 5500 && e.result.content.length > 4000, "other_chars 5000 is used");
    assert.ok(h.run("read", { content: lines(1500) }).result.content.length < 40500, "read_chars default");
    const bad = h.rows().filter((r) => r.event === "settings_invalid"); assert.equal(bad.length, 1);
    for (const key of ["read_chars", "head_share", "exempt_tools", "enabled", "log_dir"]) assert.ok(bad[0].reason.includes(key), key);
    const garbage = await harness({}, { rawSettings: "{ not json" }); assert.ok(garbage.run("shell", { content: lines(500) }).result.content.length < 20500);
    assert.equal(garbage.rows().filter((r) => r.event === "settings_invalid").length, 1);
    for (const raw of ["[]", "null", "42", '"x"']) { const g = await harness({}, { rawSettings: raw }); assert.ok(g.run("shell", { content: lines(500) }).result.content.length < 20500, raw); }
  },
  async "settings are read again after an edit"() {
    const h = await harness({ other_chars: 30000 });
    const r = { content: lines(500) }; assert.strictEqual(h.run("shell", r).result, r);             // 25,000 is under 30,000
    const file = path.join(h.dir, "plugin", "output-caps.json");
    fs.writeFileSync(file, JSON.stringify({ other_chars: 10000 })); const t = Date.now() / 1000 + 5; fs.utimesSync(file, t, t);
    assert.ok(h.run("shell", { content: lines(500) }).result.content.length < 10500);
  },
  async "the hook never throws on odd input and leaves the result as it was"() {
    const h = await harness();
    const cyc = { content: "z".repeat(50000) }; cyc.self = cyc;
    const odd = [null, undefined, 42, "text", [], {}, { tool: "shell" }, { tool: "shell", status: "completed" }, { tool: "shell", status: "completed", result: null },
      { tool: "shell", status: "completed", result: "text" }, { tool: "shell", status: "completed", result: {} }, { tool: "shell", status: "completed", result: { content: null } },
      { tool: "shell", status: "completed", result: { content: 12 } }, { tool: "shell", status: "completed", result: { content: [null, 1, "s", {}, { type: "text" }, { type: "text", text: 5 }, { type: "text", text: null }] } },
      { tool: 7, status: "completed", result: { content: "a".repeat(50000) } }, { tool: "shell", status: "completed", result: { content: [{ type: "text", text: { a: 1 } }] } },
      { tool: "shell", status: "completed", result: cyc }];
    for (const event of odd) assert.doesNotThrow(() => h.fn(event), JSON.stringify(event && typeof event === "object" ? Object.keys(event) : event));
    const nonString = { type: "text", text: { a: 1 } }; const ev = h.run("shell", { content: [nonString, { type: "text", text: lines(1000) }] });
    assert.strictEqual(ev.result.content[0], nonString, "text that is not a string is kept as it is and not counted");
    assert.ok(chars(ev.result.content) < 20500);
    // a frozen result: the replacement still works because the event is replaced, not the result mutated
    const frozen = Object.freeze({ content: Object.freeze([Object.freeze({ type: "text", text: lines(1000) })]) });
    assert.doesNotThrow(() => h.run("shell", frozen));
    // an event that cannot be written: the hook logs an error line and does not throw
    const locked = Object.freeze({ tool: "shell", sessionID: "ses_test1", status: "completed", result: { content: lines(1000) } });
    assert.doesNotThrow(() => h.fn(locked)); assert.ok(h.rows().some((r) => r.event === "error"));
    assert.ok(h.rows().filter((r) => r.event === "error").every((r) => !("text" in r) && JSON.stringify(r).length < 400));
  },
  async "the error row holds the error's class name or a fixed code, never any part of its message"() {
    const h = await harness();
    const secret = "MADE-UP-MESSAGE-4711";
    class OddFailure extends RangeError {}
    for (const make of [() => new Error(secret), () => new TypeError(secret), () => new OddFailure(secret), () => secret, () => ({ message: secret }), () => null]) {
      const event = { tool: "shell", sessionID: "ses_test1", status: "completed" };
      Object.defineProperty(event, "result", { get() { throw make(); } });
      assert.doesNotThrow(() => h.fn(event));
    }
    const raw = fs.readFileSync(path.join(h.home, "output-caps.log"), "utf8");
    assert.ok(!raw.includes(secret) && !raw.includes("4711"), "no part of the message in the log");
    const reasons = h.rows().filter((r) => r.event === "error").map((r) => r.reason);
    assert.deepEqual(reasons, ["Error", "TypeError", "OddFailure", "String", "Object", "error"]);
    const f = await harness({}, { hookFails: true });
    const rawF = fs.readFileSync(path.join(f.home, "output-caps.log"), "utf8"); assert.ok(!rawF.includes("no hook"), "the registration error's message is not logged");
  },
  async "the log holds sizes only: no output text, no tool input"() {
    const h = await harness();
    const secret = "TOP-SECRET-MADE-UP-VALUE";
    h.run("shell", { content: (secret + " ".repeat(40) + "\n").repeat(1000), metadata: { note: secret } }, { input: { command: secret } });
    h.fn({ tool: "shell", sessionID: "ses_test1", status: "completed", result: { content: [{ type: "text", text: secret }] }, input: secret, get x() { throw new Error(secret); } });
    const raw = fs.readFileSync(path.join(h.home, "output-caps.log"), "utf8");
    assert.ok(!raw.includes(secret) && !raw.includes("left out"), "no output text in the log");
    const rows = h.rows(); assert.equal(rows.length, 1);
    assert.deepEqual(Object.keys(rows[0]).sort(), ["event", "kept", "original", "session", "t", "tool"]);
    assert.equal(rows[0].event, "capped"); assert.equal(rows[0].tool, "shell"); assert.equal(rows[0].session, "ses_test1"); assert.equal(rows[0].original, 1000 * (secret.length + 41));
    assert.ok(rows[0].kept < rows[0].original && typeof rows[0].t === "number");
    h.run("shell", { content: lines(1000) }, { sessionID: "bad id with spaces\n" }); assert.equal(h.rows()[1].session, null, "an odd session id is not logged");
  },
  async "the log folder follows the environment variable, then log_dir; a log over 5 MB is cut at start"() {
    const h = await harness(); h.run("shell", { content: lines(1000) }); assert.ok(fs.existsSync(path.join(h.home, "output-caps.log")));
    const dir = path.join(root, "cfg-log"); const c = await harness({ log_dir: dir }); delete process.env.AGENTGRAPH_OUTPUT_CAPS_HOME;
    c.run("shell", { content: lines(1000) }); assert.ok(fs.existsSync(path.join(dir, "output-caps.log")));
    const big = await harness(); fs.mkdirSync(big.home, { recursive: true });
    const line = JSON.stringify({ t: 1, session: null, tool: "shell", original: 1, kept: 1, event: "capped" }) + "\n";
    fs.writeFileSync(path.join(big.home, "output-caps.log"), line.repeat(Math.ceil(5.5e6 / line.length)));
    await big.plugin.setup({ tool: { hook: async () => ({ dispose() {} }) } });
    const size = fs.statSync(path.join(big.home, "output-caps.log")).size; assert.ok(size <= 1024 * 1024 && size > 900000, String(size));
    assert.ok(big.rows().every((r) => r.event === "capped"), "whole lines only");
  },
  async "dispose removes the hook, can be called twice, and survives a registration without dispose"() {
    const h = await harness(); h.dispose(); h.dispose(); assert.equal(h.disposed, 1);
    const plain = await harness(); const p2 = await plain.plugin.setup({ tool: { hook: async () => ({}) } }); assert.doesNotThrow(() => p2());
    const p3 = await plain.plugin.setup({ tool: { hook: async () => null } }); assert.doesNotThrow(() => p3());
  },
  async "a hook that cannot be registered does not throw out of setup"() {
    const h = await harness({}, { hookFails: true }); assert.equal(typeof h.dispose, "function"); h.dispose();
    assert.ok(h.rows().some((r) => r.event === "error"));
  },
  async "the plugin id and the shape of the module"() {
    const h = await harness(); assert.equal(h.plugin.id, "agentgraph-output-caps"); assert.equal(typeof h.plugin.setup, "function");
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
