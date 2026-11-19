/*
 * OpenCode TUI plugin entry (the package's "./tui" export): the agent graph of the current session, as text,
 * in the session sidebar and, on demand, in a session panel. Plain JavaScript, no JSX and no build step.
 *
 * Imports only what the installed oh-my-opencode-slim TUI plugin (dist/tui2.js) imports and the host resolves:
 * `@opentui/solid` (createElement, insert, setProp), `solid-js` (createSignal) and node built-ins.
 * Like that plugin, it default-exports a plain `{ id, setup }` object and does not load `@opencode/plugin` at run
 * time.
 */
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { createElement, insert, setProp } from "@opentui/solid";
import { createSignal, onCleanup } from "solid-js";
import { createPlugin } from "./plugin.js";

// The saved demo document, drawn when AGENTGRAPH_TUI_DEMO is set.
let demoFile;
try {
  demoFile = fileURLToPath(new URL("../test/fixtures/demo.json", import.meta.url));
} catch (_) {
  demoFile = undefined;
}

const plugin = createPlugin({ createElement, insert, setProp, createSignal, onCleanup, spawn, fs, os, path, env: process.env, demoFile });

export default plugin;
