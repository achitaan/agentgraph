# agentgraph web contract

Two hosts show the same interface: a local web page (`agentgraph serve`) and an OpenCode Desktop side panel (the plugin in `desktop-plugin/`). Both get their data as one JSON document, and both draw it with the same framework-free script. This file is the contract between the data side, the interface, and the two hosts.

## 1. The snapshot document

Produced by `agentgraph.snapshot_json.snapshot_document(snapshot, source, task=None, *, session=None)` and printed by `agentgraph snapshot`. Served at `GET /api/snapshot`. Times are Unix seconds. Every key is always present; a missing value is `null`, an empty list is `[]`.

```json
{
  "version": 1,
  "generated": 1795000000.0,
  "source": "live",
  "warnings": ["..."],
  "team": "main",
  "totals": {"sessions": 9, "running": 3, "cost": 6.36, "tokens_in": 11290, "tokens_out": 67800},
  "root": {"id": "ses_...", "agent": "orchestrator", "title": "...", "model": "Claude Fable 5.2 Fast",
           "effort": "high", "busy": true, "created": 0.0, "updated": 0.0, "cost": 2.41,
           "tokens_in": 5200, "tokens_out": 14100, "directory": "/work/api", "context": 124000,
           "wear": {"context": 124000, "window": 1000000, "window_known": false, "compact_at": 250000,
                    "summaries": 2, "turns": 659, "turns_since_summary": 40, "history": [30000, 90000, 124000],
                    "per_turn": {"start": 0.07, "recent": 0.31}, "level": "long"}},
  "groups": [
    {"id": "hub", "title": "Orchestrator", "agents": ["orchestrator"]},
    {"id": "understand", "title": "Understand", "agents": ["explorer", "researcher"]}
  ],
  "agents": [
    {"name": "impl-openai", "group": "build", "model": "GPT-6.2 Sol Ultrafast", "effort": "high", "pool": 3,
     "configured": true, "busy": 1, "team": "main", "parent": "",
     "sessions": [{"id": "ses_...", "title": "...", "model": "...", "effort": "high", "busy": true,
                   "created": 0.0, "updated": 0.0, "cost": 0.38, "tokens_in": 900, "tokens_out": 8800}]}
  ],
  "escalation": [{"from": "impl-openai", "to": "impl-claude", "used": true}],
  "graph": {
    "task": "add-rate-limit", "goal": "...", "status": "running", "revision": 1,
    "created": 0.0, "updated": 0.0, "path": "/work/api/.opencode/graph/add-rate-limit.json",
    "progress": {"passed": 4, "total": 11},
    "coverage": {"delivered": 0, "total": 4},
    "objectives": [{"id": "o1", "text": "...", "nodes": ["n4", "n6"], "delivered": false}],
    "problems": ["..."],
    "waves": [["n1", "n2"], ["n3"]],
    "nodes": [
      {"id": "n6", "type": "implement", "agent": "impl-claude", "goal": "...", "status": "running",
       "depends_on": ["n3"], "scope": ["src/app.ts"], "done_when": "npm test -- middleware",
       "attempts": 5, "tiers": ["impl-openai", "impl-claude"], "escalated": true,
       "baseline": "5 of 9 passing", "evidence": "...", "outputs": "", "changed_paths": [],
       "failure_kind": "code", "stalled": true, "verdict": "", "session": "ses_...",
       "started": 0.0, "finished": null, "busy": true, "wave": 2,
       "pattern": "parallel", "feedback_to": [],
       "artifacts": [{"id": "handoff", "path": ".opencode/artifacts/add-rate-limit/n6/r1/handoff.md", "revision": 1}],
       "inputs": [{"from": "n3", "artifact": "decision", "revision": 1, "stale": false}]}
    ],
    "edges": [{"from": "n3", "to": "n6", "kind": "depends"}, {"from": "n9", "to": "n6", "kind": "feedback"}],
    "flows": [{"from": "oracle", "to": "impl-claude", "kind": "data", "pairs": [["n3", "n6"]], "artifacts": ["decision"], "active": true}],
    "loops": [{"id": "impl-claude~reviewer", "worker": "impl-claude", "evaluator": "reviewer", "pairs": [["n6", "n9"]], "rounds": 2, "state": "sent_back", "active": true, "artifacts": ["findings"]}],
    "work_loops": [{"agent": "impl-claude", "nodes": ["n6"], "rounds": 3, "state": "working", "active": true}],
    "advice": ["..."],
    "events": [{"time": 0.0, "node": "n6", "event": "escalated", "note": "..."}],
    "amendments": ["..."], "lessons": ["..."]
  },
  "graphs": [{"task": "add-rate-limit", "status": "running", "updated": 0.0}]
}
```

Rules:

- `source` is `"live"` or `"demo"`.
- `agents` lists every agent in the active configured roster, plus an agent outside it only while one of its sessions is busy and its name is not in the config's `disabled_agents`, so live work is never invisible (`"configured": false`, model and effort taken from its newest session, or `""`; its group is looked up as below, so an unknown name is `build`). An agent that appears only in finished sessions or only in graph nodes is not listed. `totals` still count every session. Order: by group in the order of `groups`, then roster order.
- `nodes[].busy` is true only when the node's status is `running` and its `session` is busy now. A session is often reused for several nodes (rework runs on the same session), so a busy session alone does not make a finished node busy.
- `structure.source` is `folder` when the team's shape was read from the `structure/` folder (see `structure/README.md`), else `builtin`. With a folder, `groups` are its sub-folders in name order (`hub` first), an agent's `group`, `pool`, `checks` and `parent`, and the `escalation` routes, come from the files in it, and an interface lays the groups out in that order. Change the folder and the next snapshot changes with it.
- `skills` (live sources only) lists the installed skills: `name` and a one-line `description`. Which agent may load which is `agents[].skills`.
- `learning` (live sources only; may be `null`) is what the ledger has stored for the active preset: `total` and `pending` counts of the user's notes, `due` (a prompt change is due), `runs`, `improvements`, `last_improvement`, the newest `notes` (`time`, `agent`, `task`, `text` in the user's own words), `lessons`, the applied `changes` newest first (`time`, `summary`, `file`), `trend` (`before` and `after` the latest change: `runs`, `first_pass_rate`, `mean_attempts`; or `null`), and per agent `agents[name]` = `steps`, `first_pass_rate`, `mean_attempts` over recorded runs.
- A session's `tokens_in` is everything sent to the model (fresh input plus tokens read from or written to the cache), `tokens_out` what it produced, and `context` the prompt size of its last model call, or `null` when the database does not record it. `totals` sums `cost`, `tokens_in` and `tokens_out` over every session.
- `root.wear` (live sources only; `null` when the root session has made no model call, or for the demo) says how full and how worn the root session is. It is read from the database with `json_extract`, never by parsing whole messages. Older documents do not have the key; an interface treats an absent `wear` like `null` and draws what it drew before.
  - `context` is the prompt size of the last model call (the same number as `root.context`). `window` is the model's context window, looked up for the model of the session's last model call (else the session's own model) in this order: `providers.<provider>.models.<id>.limit.context` in `~/.config/opencode/opencode.json`; the same model in `~/.cache/opencode/models.json` (the catalogue is parsed again only when the file's modification time changes); otherwise 1,000,000 with `window_known: false`.
  - `compact_at` is where OpenCode next summarizes the conversation: `window` minus the buffer, or `null` when `compaction.auto` is `false`. The buffer is `compaction.buffer` in `opencode.json`: a number is a token count (ignored when it is not below the window), a string such as `"20%"` is that share of the window, and absent (or unusable) means 15% of the window and at least 16,000 (never more than half the window).
  - `summaries` is the number of rows of type `compaction` for the session. `turns` is the number of assistant messages (including one still being written); `turns_since_summary` counts those after the newest summary (all of them when there is none).
  - `history` has at most 60 numbers: the context size of each model call in order. A longer session is cut into 60 runs of neighbouring turns and each run keeps its largest value, so the series shows the growth and the drop at each summary.
  - `per_turn` is `{"start", "recent"}`: dollars per model call over the first 20 calls and over the last 20. A session under 40 calls uses what exists: the first half of its calls against the second half. Both are `null` with a single call.
  - `level` is `"worn"` with 3 or more summaries (the earliest work has been condensed again and again); else `"fresh"` with no summary and `context` under 60% of `compact_at` (of `window` when `compact_at` is `null`); else `"long"`.
  - The object is built by `sources.session_wear` and held in `contract.Session.wear`, set on the root session only.
- `agents[].skills` lists the skills the agent may load, from the roster (`["*"]` means all; empty for an agent outside the roster).
- `agents[].checks` is true for an agent that checks every other agent's work (`verifier`); an interface links it to all of them.
- `agents[].busy` is the number of that agent's sessions that are busy now. `agents[].sessions` is newest first and includes the root session under the orchestrator.
- `groups` always has the six groups in this order: `hub`, `understand`, `decide`, `council`, `build`, `verify`. `agents` in each is the names in display order (may be empty). Group of an agent: `orchestrator` is `hub`; `explorer` and `researcher` are `understand`; `oracle` and `oracle-lite` are `decide`; `council` and every `councillor-*` are `council`; `impl-openai`, `impl-claude`, `impl-hard` and `designer` are `build`; `verifier` and `reviewer` are `verify`; unknown agents are `build`.
- `agents[].team` and the top-level `team` are always `"main"`: there is one team. The fields stay so the shape of the document does not change.
- `agents[].parent` is the agent this one works under inside its group, or `""`. Today only council seats have one: every `councillor-*` has parent `council` when `council` is in `agents`. An interface draws a parent as the centre of its cluster with its children around it, and does not draw lines from the orchestrator to children: the orchestrator relates to the cluster through the parent.
- The root session's sessions are listed under the root's own agent name, not always under `orchestrator` (a root agent outside the roster is listed only while it is busy, like any other). Whoever runs the watched session is the hub: the root's agent is always listed, working or not, in group `hub`, whatever its name, and another configured `hub` agent is listed only while one of its sessions is busy. `user` and a disabled agent cannot be the hub; the configured one then stays.
- `escalation` lists the routes from `contract.ESCALATION` (`impl-openai` → `impl-claude` → `impl-hard` → `oracle`) whose two agents are both in `agents`. `used` is true when some node's `tiers` in the graph shows that handoff (neighbouring, different agents).
- `graph` is the one named by `task` when a task is given (`null` if there is no such graph). Otherwise, when a session was requested (`--session`, or `serve --session`), the candidate owners are that session and every ancestor up its parent chain, nearest first, and `graph` is the newest graph whose `session` is the nearest candidate that owns one. When no graph names any of them, it is the newest graph and `warnings` gets `graph: no graph belongs to session <id>; showing the newest`. With no session requested it is the newest graph, or `null` when there is none. `problems` is `contract.validate(graph)` on the original graph object. `nodes[].busy` follows the rule above. `nodes[].wave` is the index into `waves`. `nodes[].escalated` is true when `tiers` has two neighbouring entries that differ. `events` is the last 200, oldest first.
- `nodes[].pattern` is the node's declared pattern (`chain`, `route`, `parallel`, `vote`, `orchestrate`, `evaluate`, `synthesize`) or `""`. `nodes[].feedback_to` lists the nodes this node sends work back to when it does not pass.
- `edges` has one `depends` edge per distinct dependency between known nodes (`from` is the dependency, `to` is the node that needs it) and one `feedback` edge per distinct `feedback_to` entry; no edge appears twice (`from` is the evaluating node, `to` is the node that gets the work back). Interfaces draw from `edges`.
- `nodes[].artifacts` lists the files a node hands on: each has an `id`, a `path` and an integer `revision`. `nodes[].inputs` lists the upstream artifacts a node reads directly: `from` (the producing node), `artifact` (its id) and the `revision` it read; `stale` is true when the producer now has a higher revision of that artifact.
- `flows` is the same work seen agent to agent, for an interface that draws agents rather than steps. There is one entry per ordered pair of different agents: a `data` flow when a node of `from` produces something a node of `to` depends on, and a `feedback` flow when a node of `from` sends work back to a node of `to` (`feedback_to`). `pairs` lists the node id pairs behind it, `artifacts` the artifact ids passed along it (from `inputs`, for both kinds), and `active` says work is moving along it now: a `data` flow is active when one of its consuming nodes is busy (status `running` and its session busy), and a `feedback` flow when its source refuted or failed and the target node is running. Nodes whose agent is `user` and pairs where both nodes have the same agent are left out. Agent names are the node `agent` values as written; a flow can name an agent that is not in `agents` (see above), and an interface that draws agents may skip such a flow or draw its unlisted end faintly.
- `loops` lists the review loops in the graph, agent to agent, so an interface can draw each one as a closed cycle. There is one entry per ordered pair (`worker`, `evaluator`) of different agents, neither of them `user`, for which some node of `evaluator` names a node of `worker` in its `feedback_to`. The list is sorted by `id`, which is `<worker>~<evaluator>`.
  - `pairs` lists the `[worker node, evaluator node]` id pairs behind it, sorted.
  - `rounds` is how many times work has gone round: the largest `attempts` among the loop's worker nodes, and at least 1.
  - `artifacts` lists the artifact ids the evaluator sent back (inputs of a worker node whose `from` is its evaluator node), sorted.
  - `state` says where the work is now. The first rule that matches wins: `working` when a worker node of the loop is busy or has status `running`; `checking` when an evaluator node of the loop is busy or has status `running`; `sent_back` when an evaluator node has verdict `refuted` or status `failed`; `closed` when every evaluator node of the loop has status `passed`; otherwise `idle`.
  - `active` is true when `state` is `working`, `checking` or `sent_back`.
  - A loop is a view of the same edges as `flows`: its forward half is the worker's output going to the evaluator and its return half is the `feedback` flow. An interface that draws a loop should not also draw the `data` and `feedback` flows between the same two agents.
- `work_loops` says where loop engineering is in use: an agent given a loop item runs work and check passes until its evaluation passes. There is one entry per agent, other than `user`, that has at least one node of type `implement` or `design`, sorted by `agent`. An interface draws it as a circular edge on that agent.
  - `nodes` lists those node ids, sorted.
  - `rounds` is the largest `attempts` among them (0 when none has started).
  - `state`: the first rule that matches wins. `working` when one of the nodes is busy or has status `running`; `waiting` when one has status `returned` (the worker reports a pass and the orchestrator has not evaluated it yet); `stuck` when one has status `failed` or `blocked`; `closed` when all of them have status `passed`; otherwise `idle`.
  - `active` is true when `state` is `working`.
- `advice` is `contract.shape_advice(graph)`: non-failing remarks about the graph's shape, such as a graph that is a single chain.
- The reserved agent name `user` (a `human` node) is not an agent and never appears in `agents`.
- `graphs` lists every graph found, newest first.
- All text is passed through as data. The interface must treat it as untrusted text and never as markup.

## 2. The commands

- `agentgraph snapshot [--demo] [--dir PATH] [--session ID] [--task NAME]` prints the document as one line of JSON and exits 0. On an internal error it prints `{"version": 1, "error": "<message>"}` and exits 1.
- `agentgraph serve [--port N] [--demo] [--dir PATH] [--session ID] [--open]` starts the web server (default port 4747) and prints its address. It listens on `127.0.0.1` only.

## 3. The server

- `GET /` and `GET /index.html` serve `agentgraph/web/index.html`. `GET /app.js`, `GET /style.css` and other files directly inside `agentgraph/web/` are served by name. Nothing outside that directory is ever served.
- `GET /api/snapshot` returns the document. Query parameters: `demo=1` (sample data), `task=NAME`. `Cache-Control: no-store`.
- Only `GET` and `HEAD`. A request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` is refused with 403, so another site cannot reach it through DNS rebinding. No CORS headers are sent.

## 4. The interface script

`agentgraph/web/app.js` is one framework-free script with no imports and no build step. It defines one global function:

```js
// root: an empty element to draw into.
// options.fetchSnapshot: async () => snapshot document (already parsed). Required.
// options.intervalMs: how often to refresh. Default 1500 (the desktop panel passes 4000).
// options.onOpenSession: optional ({ agent, sessionID }) => true when the host opened that session.
//   Called only from the "Open session" button in an agent's details, with its newest busy session
//   (for the hub, its newest working subagent). Clicking an agent always opens its details; the
//   graph never gives way to another view on its own.
// options.motion: "full" (default) or "none" for a still picture (tests, screenshots). The system's
//   reduced-motion setting is always honoured.
// options.scheme: "auto" (default; follows a data-color-scheme on an ancestor or the document element,
//   else the system setting, live), "light" or "dark".
// Returns { refresh(): Promise<void>, reset(): Promise<void>, destroy(): void }.
// reset() is for a host that switches to another session: it drops the cached document, positions,
// selection, open details and team choice, shows the loading state, fetches again and ignores any
// response that was requested before the reset.
window.AgentGraph.mount(root, options)
```

- What it draws: the hub in the centre and every other agent on one ring round it (a tall oval in a narrow container), each group side by side; council seats evenly on a circle round their lead, linked only to it. Ordinary handoffs are straight lines, and work that passes through the hub travels along its spoke. `graph.loops` are closed two-lane leaves between the two agents, and `graph.work_loops` are rings on the agent. The hub's inner arc is `graph.progress`. There is no header and no instructions: an agent's name, a working agent's step, round counts, a working count, and two icon buttons (fit, run details). The top-right readout is a button that opens the run details: dollars, tokens, and the context of the root session. With `root.wear` the context is a bar 56 px wide and 4 px tall (the track is the distance to `compact_at`, or to `window` when it is `null`; the fill is `context`: the muted ink below 60%, the work colour from 60%, ink on a work-coloured track from 85%), the number beside it, then one dot per summary (at most five) and the count; its tooltip says it in words. Without `wear` it shows `ctx 124k` from `root.context`. A narrow panel drops the tokens first, then the bar, dots and count. The run details open with a "Session" block: a line chart of `history`, then plain lines for context and the next summary, summaries (and, when `level` is `worn`, a sentence that early work has been condensed), turns, and cost per turn, each left out when its value is null.
- It keeps everything inside `root`: no writes to `document.body`, no global listeners that outlive `destroy()`, no globals other than `window.AgentGraph`.
- All of its CSS is in `agentgraph/web/style.css`, and every selector is scoped under the class `agentgraph` (which `mount` puts on `root`), so it cannot restyle a host application. No external fonts, images or scripts.
- It never uses `innerHTML` (or anything else that parses markup) with text from the document.
- `index.html` loads `style.css` and `app.js` and calls `AgentGraph.mount(document.getElementById("root"), { fetchSnapshot: () => fetch("/api/snapshot" + location.search).then(r => r.json()) })`.
- The desktop plugin bundles the same `app.js` and `style.css` and passes a `fetchSnapshot` that asks its main process for the document.
