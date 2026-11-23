/*
 * What kind of work a tool call is: its zone (files, terminal, web, search, thinking, spawn, messaging, tasks, tools) and
 * the plain words for it. Shared by the list (src/render.js) and the pictures (src/planets.js). The idea of zones comes
 * from AgentMove (github.com/FoothillSolutions/agent-move, MIT); the code is our own.
 */
import { clean, isObject } from "./render.js";

// The zone of a tool name, as the back end's table (agentgraph/chatfeed.py zone_of) for the agents whose activity carries none.
const ZONE_NAMES = {
  read: "files", write: "files", edit: "files", multiedit: "files", glob: "files", patch: "files", apply_patch: "files", applypatch: "files", list: "files", ls: "files",
  shell: "terminal", bash: "terminal", terminal: "terminal", grep: "search", websearch: "search", codesearch: "search", webfetch: "web",
  question: "thinking", skill: "thinking", subagent: "spawn", task: "spawn",
};
const ZONE_TOKENS = [
  ["messaging", ["message", "messages", "reply", "replies"]], ["tasks", ["todo", "todos", "todowrite", "todoread"]], ["thinking", ["plan", "think", "thinking"]],
  ["web", ["browser", "playwright", "puppeteer", "webfetch", "fetch", "navigate"]], ["search", ["search", "grep"]],
];
export const ROOM_OF_ZONE = { terminal: "terminal", files: "files", web: "web", search: "search", thinking: "thinking", spawn: "team", messaging: "team", tasks: "team", tools: "terminal" };

export function zoneOfTool(tool) {
  const name = clean(tool).toLowerCase();
  if (Object.hasOwn(ZONE_NAMES, name)) return ZONE_NAMES[name];
  const words = name.split(/[^a-z0-9]+/);
  if (words.some((w) => w.startsWith("browser") || w.startsWith("playwright"))) return "web";
  for (const [zone, wanted] of ZONE_TOKENS) if (words.some((w) => wanted.includes(w))) return zone;
  return "tools";
}

// The zone of an activity: its own, else the thinking of reasoning and replying, else its tool's.
export function zoneOfActivity(act) {
  if (!isObject(act)) return null;
  if (typeof act.zone === "string" && act.zone !== "") return Object.hasOwn(ROOM_OF_ZONE, act.zone) ? act.zone : "tools"; // an unknown zone is the terminal's
  if (act.kind === "reasoning" || act.kind === "text") return "thinking";
  if (act.kind === "tool") return zoneOfTool(act.tool);
  return null;
}

// The room of an activity: unknown goes to the terminal, a working agent with no activity to thinking.
export function roomOfActivity(act, unknown = "terminal") {
  const zone = zoneOfActivity(act);
  if (zone) return ROOM_OF_ZONE[zone];
  return isObject(act) && typeof act.zone === "string" ? "terminal" : unknown;
}

// Plain words for each zone, for a newcomer.
export const ZONE_WORDS = Object.freeze({
  files: "reading a file", terminal: "running a command", web: "reading a site", search: "searching", thinking: "thinking",
  spawn: "talking to a worker", messaging: "talking to a worker", tasks: "planning", tools: "working",
});
// A few tools say more than their zone does.
const TOOL_WORDS = Object.freeze({
  write: "writing a file", edit: "editing a file", multiedit: "editing a file", patch: "editing a file", apply_patch: "editing a file", applypatch: "editing a file",
  list: "looking at files", ls: "looking at files", glob: "looking for files", question: "asking a question", skill: "loading a skill", webfetch: "reading a site",
  websearch: "searching the web", todowrite: "planning", todoread: "planning",
});

// What a working agent is doing now, in words: "reading a file", "running a command", "thinking" ... "working" when it has no activity.
export function activityWords(act) {
  if (!isObject(act) || (act.kind !== "tool" && act.kind !== "reasoning" && act.kind !== "text")) return "working";
  if (act.kind === "reasoning") return "thinking";
  if (act.kind === "text") return "replying";
  const tool = clean(act.tool).toLowerCase();
  if (tool && Object.hasOwn(TOOL_WORDS, tool)) return TOOL_WORDS[tool];
  const zone = zoneOfActivity(act) || "tools";
  return Object.hasOwn(ZONE_WORDS, zone) ? ZONE_WORDS[zone] : "working";
}
