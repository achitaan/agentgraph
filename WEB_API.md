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
     "configured": true, "busy": 1, "team": "main", "parent": "", "about": "What the agent does, when it is used, what it hands back.",
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
- `structure.source` is `folder` when the team's shape was read from the `structure/` folder (see `structure/README.md`), else `builtin`. With a folder, `groups` are its sub-folders in name order (`hub` first), an agent's `group`, `pool` and `parent`, and the `escalation` routes, come from the files in it, and an interface lays the groups out in that order. Change the folder and the next snapshot changes with it.
- `structure.flows` lists the flows drawn in the structure folder's `flows/` (see `structure/README.md`), in id order: `{"id": "impl-openai-verifier-impl-openai", "path": ["impl-openai", "verifier", "impl-openai"], "text": "...", "source": "drawn"}`. A flow is a path through two or more agents, each agent's output being the next one's input; a step to an agent already on the path sends the work back to it. `text` is the user's instruction (or the one made from the path), plain text. `agentgraph check` enforces them (see `GRAPH_FORMAT.md`). A path of one agent twice, `["impl-hard", "impl-hard"]`, is that agent's loop on itself: it checks its own work against the task's evaluation and repeats until it passes (that is its made text). Any agent may have one, the hub included, and at most one; `check` prints it and adds no rule for it. A file that cannot be read as a flow is left out and reported in `warnings`, never as an error, and so is a second loop on the same agent; the folder is read up to 200 flow files (in id order), and any beyond are named in `warnings`. Without a folder there are no flows.
- There are no default loops: settings (`pool`, `escalates_to`, `seats`) are data shown in the details and never draw a line by themselves. A fresh structure folder has agents and no connections; at rest, with no flows and no task graph, an interface draws only the hub's lines to its agents and the council's to its seats.
- `agents[].about` is the agent's description: the text of its file in the `structure/` folder after the settings lines, whitespace collapsed to single spaces, at most 600 characters. It is `""` when the file has no text, when the agent has no file (a councillor seat, an agent outside the folder), and whenever `structure.source` is `builtin` or the source is the demo. An interface shows it as plain text (never markup), first in the agent's details, and shows nothing when it is empty.
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
- `agents[].skills` lists the skills the agent may load, from the roster (`["*"]` means all; empty for an agent outside the roster). The hub is an entry like any other, so the orchestrator's `skills` is `["*"]` from the roster; a hub whose agent name is not in the roster (OpenCode's own `build` or `plan`, which the plugin does not filter) has `["*"]`, while a worker outside the roster keeps `[]` and a roster agent keeps exactly its list, an empty one included (`council`). In the details panel an agent with `*` shows `all · N` (N is `skills.length`) and, when `skills` is present, a closed fold "Installed skills" listing each skill's name and description (the description is the row's tooltip); an agent with a list shows that list in the same rows; an agent with neither shows no skills section.
- `agents[].skills_editable` (live sources only) is `true` when the agent's skills can be changed from here (`agentgraph skills`, `POST /api/skills`), and `agents[].skills_reason` is `""` then, or one sentence saying why not: a council seat (its skills are set in the council's own table), an agent outside the plugin's roster (OpenCode's own `build` or `plan` as the hub), or a preset file that could not be read. The top-level `skill_warnings` (live sources only) lists, one sentence each, where a task profile in `task-profiles.md` names a skill its agent's list in the active preset does not have; it is `[]` when they agree.
- `agents[].busy` is the number of that agent's sessions that are busy now. `agents[].sessions` is newest first and includes the root session under the orchestrator.
- `groups` always has the six groups in this order: `hub`, `understand`, `decide`, `council`, `build`, `verify`. `agents` in each is the names in display order (may be empty). Group of an agent: `orchestrator` is `hub`; `explorer` and `researcher` are `understand`; `oracle` and `oracle-lite` are `decide`; `council` and every `councillor-*` are `council`; `impl-openai`, `impl-claude`, `impl-hard` and `designer` are `build`; `verifier` and `reviewer` are `verify`; unknown agents are `build`.
- `agents[].team` and the top-level `team` are always `"main"`: there is one team. The fields stay so the shape of the document does not change.
- `agents[].parent` is the agent this one works under inside its group, or `""`. Today only council seats have one: every `councillor-*` has parent `council` when `council` is in `agents`. An interface draws a parent as the centre of its cluster with its children around it, and does not draw lines from the orchestrator to children: the orchestrator relates to the cluster through the parent.
- The root session's sessions are listed under the root's own agent name, not always under `orchestrator` (a root agent outside the roster is listed only while it is busy, like any other). Whoever runs the watched session is the hub: the root's agent is always listed, working or not, in group `hub`, whatever its name, and another configured `hub` agent is listed only while one of its sessions is busy. `user` and a disabled agent cannot be the hub; the configured one then stays.
- `escalation` lists the routes from `contract.ESCALATION` (`impl-openai` → `impl-claude` → `impl-hard` → `oracle`) whose two agents are both in `agents`. `used` is true when some node's `tiers` in the graph shows that handoff (neighbouring, different agents). An interface draws a route only when it is `used`; an unused one is data for the details.
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
- `agentgraph structure flows [--json]` lists the drawn flows (`--json`: `{"flows": [...]}`, the shape of `structure.flows`). `agentgraph structure flow add A B [C ...] [--text-file F|-] [--json]` adds the flow along that path, with the instruction read from file `F` or from standard input (`-`); without it, or when it is blank, the instruction is made from the path. `flow text ID --text-file F|- [--json]` replaces a flow's instruction (blank: the one made from the path) and `flow remove ID [--json]` removes it. They write only in the structure folder's `flows/` (one file per flow; a new file is linked into place so it never replaces another, a changed one is renamed over the old, and the folder is removed with its last flow), one change at a time under a lock, and never touch an agent's file. They refuse, with a message and exit 1 (with `--json`, `{"error": "..."}` on stdout): fewer than two agents, an unknown agent, the same agent twice in a row inside a longer path (`A A` alone is the agent's loop on itself; a second one for the same agent is refused with "already has one"), the hub anywhere but first or last, a return straight after the work came back to the first agent, more than 20 agents, an instruction over 2000 characters (Unicode characters, not UTF-16 units) or with control characters other than newline and tab, an unknown id, and a new flow when the folder already holds 200. A council seat is stored as its council, with a note. With `--json` they print `{"flow": {...}, "flows": [...], "notes": [...]}` (`flow` absent after a remove, `notes` only when there are some). An instruction that is empty or only whitespace always means "make it from the path", for `add` and for `text` alike. `--structure DIR` names another folder (for tests) and comes after the subcommand. Never runs git.
- `agentgraph skills show AGENT | add AGENT SKILL | remove AGENT SKILL | all AGENT [--json]` shows or changes which skills an agent may load, in the plugin's preset file (every preset that lists the agent; a backup is written first) and in `SKILLS.md`. `all` gives the agent `["*"]`; removing one skill from an agent with `*` leaves the list of every installed skill but that one. It refuses, with a message and exit 1: an unknown agent or skill, a council seat, and a name that starts with `-`. `--config F`, `--map F` and `--backups DIR` name other files (for tests). A change applies after OpenCode restarts. With `--json` it prints `{"agent", "preset", "skills": [...], "warnings": [...], "changed", "message", ...}`.

## 3. The server

- `GET /` and `GET /index.html` serve `agentgraph/web/index.html`. `GET /app.js`, `GET /style.css` and other files directly inside `agentgraph/web/` are served by name. Nothing outside that directory is ever served.
- `GET /api/snapshot` returns the document. Query parameters: `demo=1` (sample data), `task=NAME`. `Cache-Control: no-store`.
- `POST /api/structure/flow` changes the flows exactly like `agentgraph structure flow`, with the JSON body `{"op": "add", "path": [A, B, ...]}` (and an optional `"text"`), `{"op": "text", "id": ID, "text": T}` or `{"op": "remove", "id": ID}`, and answers `{"flow"?, "flows": [...], "notes"?}` like the command's `--json`. It is refused with `{"error": "..."}`: 403 for a client that is not loopback, an `Origin` header (when present) other than `http://` plus the request's own `Host`, or a demo server; 415 for a body that is not `application/json`; 411 or 413 for a missing, empty or oversized (over 32768 bytes) body; 400 for a body with any other keys or for a change the command refuses. Any other method on this path is 405 with `Allow: POST`. An `"op": "text"` whose `"text"` is empty or only whitespace gives the instruction made from the path.
- `POST /api/skills` changes one agent's skills exactly like `agentgraph skills`, with the JSON body `{"agent": A, "skill": S, "on": true|false}` (add or remove one) or `{"agent": A, "all": true}` (give it every skill), and answers `{"agent", "skills": [...], "warnings": [...], "changed", "message"}`: `skills` is the agent's new list, `warnings` the task-profile mismatches a remove creates. It has every protection of the flow endpoint (loopback client, same-origin `Origin`, `application/json`, a body of at most 4096 bytes, exactly these keys, 405 with `Allow: POST` for any other method); a demo server refuses it with 403, and an unknown agent or skill, a council seat or a name starting with `-` is 400 with `{"error": "..."}`. The files it writes are parameters of the server (the real preset file and `SKILLS.md` by default), so tests pass copies.
- Every other path takes only `GET` and `HEAD` (405 otherwise). A request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` is refused with 403, so another site cannot reach it through DNS rebinding. No CORS headers are sent.

## 4. The interface script

`agentgraph/web/app.js` is one framework-free script with no imports and no build step. It defines one global function:

```js
// root: an empty element to draw into.
// options.fetchSnapshot: async () => snapshot document (already parsed). Required.
// options.intervalMs: how often to refresh. Default 1500 (the desktop panel passes 4000).
// options.onOpenSession: optional ({ agent, sessionID }) => true when the host opened that session.
//   Called only from the "Open session" button in an agent's details, with its newest busy session
//   (for the hub, its newest working subagent). Details open on a right-click (see "Details" below);
//   the graph never gives way to another view on its own.
// options.actions.flow: optional (request) => Promise. Asks the host to change the drawn flows; request
//   is { op: "add", path: [names], text? }, { op: "text", id, text } or { op: "remove", id }, as for
//   POST /api/structure/flow. Resolves with { flow?, flows, notes? } (the next snapshot shows the change)
//   or rejects with an Error whose message is shown as the first agent's tooltip. A host that cannot
//   write passes no flow. With it: a click on an agent starts a path (a dashed line follows the pointer)
//   and each click on another agent adds a step without saving anything (the path so far is dimmed; a
//   council seat stands for its council; an agent already on the path gets the work back). Once the hub is
//   the newest step nothing else can be added: Enter or a click on it again finishes. Enter, a click on
//   the newest agent again or a double-click on the last one finishes it and asks the host to add it; a
//   path of one agent finishes to nothing, and a second click on a lone armed agent slower than the
//   double-click time (500 ms) cancels it. Two clicks on one agent within 500 ms with no path pending
//   before the first are a double-click: the second click itself adds its loop on itself ({ op: "add",
//   path: [A, A] }; the arm of the first click turns straight into it, never into a cancel, and the
//   browser's dblclick that follows is swallowed; a dblclick slower than 500 ms, from a long system
//   setting, still adds it after the cancel),
//   drawn as a small loop on the agent's outer side, away from its name; when it has one already, the
//   double-click selects it and opens its details (it never removes or doubles it). A press that does
//   not move is a click however long it is held; one that moves moves the agent (see "Moving agents"
//   below) and never touches the path. Backspace removes the newest step; Escape or a click on empty
//   canvas drops the path. A left click never opens details (see "Details" below). A new flow (finished
//   path or double-click) draws in once and opens its details: the path, its instruction in an editable
//   box (saved when focus leaves it or on Ctrl/Cmd+Enter, with its state beside it) and Remove. The box
//   has focus with the made-up instruction selected, so typing replaces it and leaving it (a click
//   away, or Escape, which closes the details) keeps it; no browser dialog opens. A
//   right-click on any step (or Enter on a focused flow) opens the whole flow's details. A left click on
//   any step of a drawn flow or a loop on itself picks it (marked as selected, nothing else dims, no
//   details); Escape or a click anywhere else lets it go.
//   Deleting lines: Delete or Backspace removes the focused flow, else the picked one, else the one whose
//   details are open, with the same { op: "remove", id } request as Remove (which stays in the details).
//   They do nothing while a path is pending (Backspace then removes its newest step), while focus is in a
//   text field or the instruction box (they edit the text there), or on any control in the details panel.
//   Only the user's own lines can be deleted: the hub's lines to its agents, the council's lines to its
//   seats and the loops and lines drawn from a live task graph cannot, and Delete on them or on a node
//   changes nothing and asks nothing (deleting a node is a later addition). Without actions.flow it does
//   nothing. A remove that fails keeps the flow, opens its details and shows the message as the error
//   line beside Remove. Ctrl/Cmd+Z puts a removed flow back with its path and its newest instruction (an
//   edit still being saved when Delete was pressed included) as an { op: "add" } request; the back end
//   gives it the first free id made from the path, which is the old id unless another flow has taken it
//   (add has no way to ask for an id). There is no redo: Ctrl/Cmd+Shift+Z does nothing. With actions.flow
//   each drawn flow carries aria-keyshortcuts "Enter I Shift+F10 ContextMenu Delete Backspace". A new or changed instruction is held to 2000 characters (counted as characters); a
//   save asked for while one is out is queued and sent after it. Ctrl/Cmd+Z undoes the last ten adds,
//   removes, text changes, skill changes and moves made in the view, newest first, whichever kind it was.
//   Keyboard: Enter (with no path pending) opens an agent's details, `c` starts a path at the focused agent, `l` gives the
//   focused agent its loop on itself (or selects the one it has; only with at most one step pending), Tab
//   and the arrow keys move between agents, Enter on another agent adds a step, Enter on the newest
//   finishes, Shift+Enter opens details, Escape cancels (the agents carry aria-keyshortcuts "Enter
//   Shift+Enter I Shift+F10 ContextMenu C L R Alt+ArrowUp Alt+ArrowDown Alt+ArrowLeft Alt+ArrowRight
//   Backspace Escape"); `aria-pressed` marks the newest step and a status line reads the path out.
//   Without flow, a left click on an agent does nothing (it only takes focus), flows are still drawn,
//   and a right-click on one shows its details read-only.
// Details (with or without actions): left click is for building, right-click is for information.
//   A right-click (the contextmenu event, so a two-finger click and Ctrl+click on a Mac count) on any
//   node (an agent, the hub, the council, a seat), on a drawn flow or on a review or work loop opens its
//   details in the side panel; on the hub's outer learning ring it opens the learning panel. The
//   browser's menu is replaced on those targets only; on empty canvas, on the controls and inside the
//   details panel it stays. It does so in any state (during a drag it is ignored, see below), and it is never a click: it does not arm, pick, connect, finish or
//   cancel a path (a pending path stays pending), start a drag, or count towards a double-click. The
//   keyboard opens the focused node's, flow's or loop's details with `i`, the context-menu key or
//   Shift+F10 (the browser's own contextmenu event that follows those keys is swallowed once), and with
//   Enter as before (on an agent only when no path is pending; Shift+Enter always). On touch, a press
//   held still for 500 ms opens the details of what it is on and its release is not a click; a press
//   that moves first is the drag (or pan) described below. A left click on the hub's learning ring still
//   opens the learning panel (it is a control, not a node).
// Moving agents (with or without actions): every node can be moved, the hub, the council (it carries its
//   seats) and a single seat included. A press on an agent that moves more than 5 px drags it: the agent
//   follows the pointer, its lines, loops and flows follow live, and on release it stays where it was
//   dropped (the camera does not move). A drag never arms, picks, connects, opens details or makes a loop
//   on itself: the click and dblclick the browser sends after its release are swallowed, and the
//   double-click timer starts again (a double-click after a drag needs two fresh clicks). Movement of 5 px
//   or less (trackpad jitter) is still a click and moves nothing. The pointer is captured, so leaving the
//   agent or the canvas does not drop the drag; pointercancel ends it where it is (one undoable move, no
//   click); a second pointer during a press or drag is ignored (a pinch needs both on the canvas).
//   Escape during a drag puts the agent back where it was (nothing to undo) and leaves a pending path;
//   the next Escape drops the path. A right-click during a drag is ignored (no details, no menu) and the
//   drag goes on. The cursor is grab on every node and grabbing while dragging. Alt+arrow (Shift+arrow too) moves the
//   focused agent 12 px; a run of presses on one agent is one move for undo. Ctrl/Cmd+Z undoes a move.
//   `r` (on the canvas or a focused agent) or a double-click on empty canvas puts every agent back in its
//   automatic place, itself one undoable move. Positions are kept per team and arrangement (wide or
//   compact), survive refreshes, snapshot rebuilds and reset(), and are remembered across sessions in
//   localStorage when the host has it (a body as { x, y }, a seat as { dx, dy } from its lead); without
//   storage, or when it throws, they hold for the session only. An agent never moved keeps the automatic
//   layout. Nothing is written to the structure folder. Nothing is added to the graph and nothing moves
//   at rest.
// options.positionsKey: optional localStorage key for the moved positions. Default
//   "agentgraph.positions.v1"; a host showing two unrelated graphs can pass its own.
// options.actions.skills: optional (request) => Promise. Asks the host to change one agent's skills;
//   request is { agent, skill, on: true|false } or { agent, all: true }, as for POST /api/skills.
//   Resolves with { agent, skills, warnings, changed, message } or rejects with an Error. With it, the
//   details of an agent whose skills_editable is true get a remove control on every skill row, a closed
//   fold "Add skills" of the installed skills it lacks (description as tooltip), each with an add control,
//   and "Give it every skill"; an agent with `*` keeps `all · N` and every row of its "Installed skills"
//   fold gets remove (which leaves the list of all the others). One change per agent at a time: it shows
//   as pending on its row and the controls wait. On success the list updates and one line says it
//   applies after OpenCode restarts, plus one line per warning; on failure the row reverts and the
//   message shows as a line. Folds keep their open state and the panel its scroll across refreshes. An
//   agent that is not editable (a council seat, a hub outside the roster) shows skills_reason as one
//   line. Without skills, every agent's skills are read-only and no reason is shown.
// options.motion: "full" (default) or "none" for a still picture (tests, screenshots). The system's
//   reduced-motion setting is always honoured.
// options.scheme: "auto" (default; follows a data-color-scheme on an ancestor or the document element,
//   else the system setting, live), "light" or "dark".
// Returns { refresh(): Promise<void>, reset(): Promise<void>, destroy(): void }.
// reset() is for a host that switches to another session: it drops the cached document, the automatic
// layout, selection, open details and team choice (the positions the user moved are kept: they belong to
// the host), shows the loading state, fetches again and ignores any
// response that was requested before the reset.
window.AgentGraph.mount(root, options)
```

- What it draws: the hub in the centre and every other agent on one ring round it (a tall oval in a narrow container), each group side by side; council seats evenly on a circle round their lead, linked only to it. Ordinary handoffs are straight lines, and work that passes through the hub travels along its spoke. `graph.loops` are closed two-lane leaves between the two agents, and `graph.work_loops` are rings on the agent. The hub's inner arc is `graph.progress`. There is no header and no instructions: an agent's name, a review loop's round count, and on an agent with two or more sessions working now a count badge "×N" at its top right (the same number as "N active" in its details; none at 0 or 1; it used to be the work loop's round count, which is now in the loop's label and details only), and two icon buttons (fit, run details). A working agent's task is not drawn on the canvas: it is in the agent's accessible name and its details. The camera fits every agent, its name and its badge inside the canvas with a margin, in a wide and in a narrow (compact) container, with the details open or closed: full size when they fit, smaller just as far as needed when they do not (a narrow container no longer pans to agents below). An agent the user moves stays inside the canvas. The top-right readout is a button that opens the run details: dollars, tokens, and the context of the root session. With `root.wear` the context is a bar 56 px wide and 4 px tall (the track is the distance to `compact_at`, or to `window` when it is `null`; the fill is `context`: the muted ink below 60%, the work colour from 60%, ink on a work-coloured track from 85%), the number beside it, then one dot per summary (at most five) and the count; its tooltip says it in words. Without `wear` it shows `ctx 124k` from `root.context`. A narrow panel drops the tokens first, then the bar, dots and count. The run details open with a "Session" block: a line chart of `history`, then plain lines for context and the next summary, summaries (and, when `level` is `worn`, a sentence that early work has been condensed), turns, and cost per turn, each left out when its value is null.
- It keeps everything inside `root`: no writes to `document.body`, no global listeners that outlive `destroy()`, no globals other than `window.AgentGraph`.
- All of its CSS is in `agentgraph/web/style.css`, and every selector is scoped under the class `agentgraph` (which `mount` puts on `root`), so it cannot restyle a host application. No external fonts, images or scripts.
- It never uses `innerHTML` (or anything else that parses markup) with text from the document.
- Drawn flows (`structure.flows`): a forward step is a straight line with an arrowhead, a step back to an agent already on the path a curved arc; steps of different flows on one pair are offset, and a pair that already has something drawn on it (a hub spoke, a task-graph work line, escalation or review loop) gets its flow steps beside that, never on top of it or hidden by it. Every step has a wide invisible hit area above the graph's own lines and loops. A flow is still at rest. A new flow draws in once (about 300 ms); a refused one disappears and the first agent shakes once. No text is added to the graph.
- `index.html` loads `style.css` and `app.js` and calls `AgentGraph.mount(document.getElementById("root"), { fetchSnapshot: () => fetch("/api/snapshot" + location.search).then(r => r.json()), actions: { flow, skills } })`, where `flow` posts its request to `/api/structure/flow` and `skills` to `/api/skills`; with `?demo` it passes no `actions`.
- The desktop plugin bundles the same `app.js` and `style.css` and passes a `fetchSnapshot` that asks its main process for the document, and an `actions.flow` that asks its main process (Ipc method `flow`, input `{op, id, path, text}` as strings, the path's names joined by single spaces) to run `python3 -P -m agentgraph.structure flow add|text|remove ... --json` without a shell, with the instruction on stdin (`--text-file -`), and an `actions.skills` that asks its main process (Ipc method `skills`, input `{op, agent, skill}` as strings, `op` one of `add`, `remove`, `all` and `skill` `""` for `all`) to run `python3 -P -m agentgraph.skillset add|remove AGENT SKILL --json` or `all AGENT --json` without a shell; a name that is not a plain agent or skill name (one starting with `-` included) is refused before anything runs. No `actions` when its `demo` option is set.
