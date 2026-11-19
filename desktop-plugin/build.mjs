#!/usr/bin/env node
// Build desktop-plugin/gui-experimental.cjs: one CommonJS file made of src/plugin.cjs plus the framework-free view
// (../agentgraph/web/app.js) and its styles (../agentgraph/web/style.css). No dependencies, no network.
//
//   node desktop-plugin/build.mjs [--app PATH] [--css PATH] [--out PATH] [--quiet]
//
// If app.js or style.css is missing, the build still succeeds and embeds a small placeholder view (agent names and
// busy counts). If app.js exists but does not parse, the build fails rather than ship a bundle that breaks both
// processes.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const args = {
    app: path.join(HERE, "..", "agentgraph", "web", "app.js"),
    css: path.join(HERE, "..", "agentgraph", "web", "style.css"),
    out: path.join(HERE, "gui-experimental.cjs"),
    source: path.join(HERE, "src", "plugin.cjs"),
    quiet: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === "--quiet") args.quiet = true;
    else if (flag === "--app" || flag === "--css" || flag === "--out") {
      const value = argv[++i];
      if (!value) fail(`${flag} needs a path`);
      args[flag.slice(2)] = path.resolve(value);
    } else fail(`unknown argument: ${flag}`);
  }
  return args;
}

function fail(message) {
  console.error(`build.mjs: ${message}`);
  process.exit(1);
}

function readOptional(file) {
  try {
    return fs.readFileSync(file, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

// Placeholder view: same interface as app.js (WEB_API.md section 4), text only, scoped CSS.
const PLACEHOLDER_VIEW = String.raw`
function placeholderMount(root, options) {
  if (!root || !options || typeof options.fetchSnapshot !== "function") throw new Error("A root and fetchSnapshot are required");
  var doc = root.ownerDocument || document;
  var interval = Number(options.intervalMs) > 0 ? Number(options.intervalMs) : 1500;
  var alive = true, timer = null, pending = null;
  function el(tag, className, text) {
    var item = doc.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = String(text);
    return item;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  root.classList.add("agentgraph");
  root.classList.add("agentgraph-placeholder");
  var title = el("div", "agentgraph-placeholder-title", "Agent graph");
  var status = el("div", "agentgraph-placeholder-status", "Loading\u2026");
  var list = el("ul", "agentgraph-placeholder-list");
  root.appendChild(title);
  root.appendChild(status);
  root.appendChild(list);
  function draw(snapshot) {
    if (!alive) return;
    clear(list);
    if (!snapshot || typeof snapshot !== "object") { status.textContent = "No data."; return; }
    if (snapshot.error) { status.textContent = "Error: " + String(snapshot.error); return; }
    var agents = Array.isArray(snapshot.agents) ? snapshot.agents : [];
    var busy = 0;
    agents.forEach(function (agent) {
      var count = Number(agent && agent.busy) || 0;
      busy += count;
      var row = el("li", count > 0 ? "agentgraph-placeholder-agent is-busy" : "agentgraph-placeholder-agent");
      row.appendChild(el("span", "agentgraph-placeholder-name", agent && agent.name != null ? agent.name : "?"));
      row.appendChild(el("span", "agentgraph-placeholder-busy", count + " busy"));
      list.appendChild(row);
    });
    status.textContent = agents.length + (agents.length === 1 ? " agent, " : " agents, ") + busy + " busy";
  }
  function schedule() {
    if (!alive) return;
    clearTimeout(timer);
    timer = setTimeout(refresh, interval);
  }
  function refresh() {
    if (!alive) return Promise.resolve();
    if (pending) return pending;
    pending = Promise.resolve()
      .then(function () { return options.fetchSnapshot(); })
      .then(draw, function (error) { draw({ version: 1, error: error && error.message ? error.message : String(error) }); })
      .then(function () { pending = null; schedule(); });
    return pending;
  }
  refresh();
  return {
    refresh: refresh,
    destroy: function () {
      alive = false;
      clearTimeout(timer);
      clear(root);
      root.classList.remove("agentgraph-placeholder");
      root.classList.remove("agentgraph");
    },
  };
}
`;

const PLACEHOLDER_CSS = [
  ".agentgraph.agentgraph-placeholder{box-sizing:border-box;padding:12px;font:inherit;color:inherit}",
  ".agentgraph.agentgraph-placeholder .agentgraph-placeholder-title{font-weight:600;margin-bottom:4px}",
  ".agentgraph.agentgraph-placeholder .agentgraph-placeholder-status{opacity:.75;margin-bottom:8px}",
  ".agentgraph.agentgraph-placeholder .agentgraph-placeholder-list{list-style:none;margin:0;padding:0}",
  ".agentgraph.agentgraph-placeholder .agentgraph-placeholder-agent{display:flex;justify-content:space-between;gap:8px;padding:2px 0}",
  ".agentgraph.agentgraph-placeholder .agentgraph-placeholder-agent.is-busy .agentgraph-placeholder-busy{font-weight:600}",
  ".agentgraph.agentgraph-placeholder .agentgraph-placeholder-busy{opacity:.75;font-variant-numeric:tabular-nums}",
].join("\n");

function build(args) {
  const source = readOptional(args.source);
  if (source === null) fail(`missing ${args.source}`);
  const app = readOptional(args.app);
  const css = readOptional(args.css);

  if (app !== null) {
    try {
      new vm.Script(app, { filename: args.app });
    } catch (error) {
      fail(`${args.app} does not parse (is it being rewritten?): ${error.message}`);
    }
  }

  const warnings = [];
  if (app === null) warnings.push(`no ${args.app}: embedding the placeholder view`);
  if (css === null) warnings.push(`no ${args.css}: embedding placeholder styles only`);

  // The view only runs in a window, and only when the panel first renders. In the main process (no window or
  // document) loadView returns null without evaluating app.js. module/exports/require/define are shadowed so a
  // UMD-style script cannot touch the bundle's own module object.
  const viewLoader =
    app !== null
      ? [
          "  function loadView() {",
          '    if (typeof window === "undefined" || typeof document === "undefined") return null;',
          "    (function (module, exports, require, define) {",
          app.replace(/\s+$/, ""),
          "",
          "    }).call(window, undefined, undefined, undefined, undefined);",
          "    return window.AgentGraph || null;",
          "  }",
        ].join("\n")
      : [
          PLACEHOLDER_VIEW,
          "  function loadView() {",
          '    if (typeof window === "undefined" || typeof document === "undefined") return null;',
          "    return { mount: placeholderMount };",
          "  }",
        ].join("\n");

  const styles = (css !== null ? css : "") + (app === null || css === null ? "\n" + PLACEHOLDER_CSS : "");
  const rel = (file) => path.relative(path.dirname(args.out), file) || file;

  const bundle = [
    "// Generated by desktop-plugin/build.mjs. Do not edit; edit src/plugin.cjs and rebuild.",
    `// Sources: ${rel(args.source)}, ${app !== null ? rel(args.app) : "placeholder view"}, ${css !== null ? rel(args.css) : "placeholder styles"}`,
    "var AGENTGRAPH_ASSETS = (function () {",
    `  var css = ${JSON.stringify(styles)};`,
    viewLoader,
    `  return { css: css, view: ${JSON.stringify(app !== null ? "app.js" : "placeholder")}, loadView: loadView };`,
    "})();",
    "(function () {",
    source.replace(/\s+$/, ""),
    "",
    "})();",
    "",
  ].join("\n");

  // The host evaluates the file as Function("require", "module", "exports", code): check it parses that way.
  try {
    new vm.Script(`(function (require, module, exports) {\n${bundle}\n})`, { filename: args.out });
  } catch (error) {
    fail(`the bundle does not parse: ${error.message}`);
  }

  fs.mkdirSync(path.dirname(args.out), { recursive: true });
  const temp = `${args.out}.${process.pid}.tmp`;
  fs.writeFileSync(temp, bundle);
  fs.renameSync(temp, args.out);

  if (!args.quiet) {
    for (const warning of warnings) console.warn(`build.mjs: warning: ${warning}`);
    const size = Buffer.byteLength(bundle);
    console.log(`${args.out} (${size} bytes, ${(size / 1024).toFixed(1)} KiB, view: ${app !== null ? "app.js" : "placeholder"})`);
  }
}

build(parseArgs(process.argv.slice(2)));
