# agentgraph web contract

Two hosts show the same interface: a local web page (`agentgraph serve`) and an OpenCode Desktop side panel (the plugin in `desktop-plugin/`). Both get their data as one JSON document, and both draw it with the same framework-free script. This file is the contract between the data side, the interface, and the two hosts.

## 1. The snapshot document

Produced by `agentgraph.snapshot_json.snapshot_document(snapshot, source, task=None, *, session=None)` and printed by `agentgraph snapshot`. Served at `GET /api/snapshot`. Times are Unix seconds. The keys of the example below are always present (a missing value there is `null`, an empty list `[]`). These are not, and a reader must allow for their absence: `agents[].activity` (only on an agent that is busy now; its `zone`, `status` and `target` on every busy agent and `recent` on the hub only, and `target` and `recent` not in `agentgraph snapshot`); `sidechat.spent` (only when known); and the keys a live source adds after `snapshot_document`: `learning`, `skills`, `skill_warnings`, `mcp`, `roster`, `setup`, `spend`, `sidechat`, on each agent `added`, `mcps`, `removable`, `removable_reason`, `rev`, `skills_editable`, `skills_reason` and `use_when`, and on each `graph.nodes[].artifacts[]` entry `rev`, `size`, `editable` and `why`. The demo has `setup`, `spend` and `sidechat` (sample ones) but none of the rest. The plain `agentgraph snapshot` command with a live source adds all of them, as `GET /api/snapshot` does, and `agentgraph snapshot --demo` adds only the demo's three; the bare `snapshot_document` function adds none, and `spend` can be missing from an older back end.

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
    "liveness": {"live": 1, "quiet": 0, "orphan": 0, "unreported": 0},
    "problems": ["..."],
    "waves": [["n1", "n2"], ["n3"]],
    "nodes": [
      {"id": "n6", "type": "implement", "agent": "impl-claude", "goal": "...", "status": "running",
       "depends_on": ["n3"], "scope": ["src/app.ts"], "done_when": "npm test -- middleware",
       "attempts": 5, "tiers": ["impl-openai", "impl-claude"], "escalated": true,
       "baseline": "5 of 9 passing", "evidence": "...", "outputs": "", "changed_paths": [],
       "failure_kind": "code", "stalled": true, "verdict": "", "session": "ses_...",
       "started": 0.0, "finished": null, "busy": true, "liveness": "live", "idle_since": null, "wave": 2,
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
- `nodes[].liveness` says what the database confirms about a node, as against the `status` the graph reports; it is derived on every read and never written to the graph file. `"live"`: status `running` and its session is busy now. `"quiet"`: status `running`, the session exists and is idle (`nodes[].idle_since` is when it went idle, in Unix seconds, else its last update; `null` for every other value). `"orphan"`: status `running` with no `session`, or a session id the database does not have. `"unreported"`: the session is busy but the node is `pending`, `returned` or `blocked`, and no running node of the graph, nor the graph's own `session`, uses that session (a `passed` or `failed` node is never `unreported`: its session is often reused for later work). `null` otherwise, and `null` for every node when the database cannot be read (a running node with no session id is still `orphan`: that is read from the graph). Liveness is decided by the whole database, so a session outside the watched session's tree still counts; `busy` keeps its rule above. `graph.liveness` counts the nodes of each of the four values (`{"live", "quiet", "orphan", "unreported"}`, all four always present). A view should draw a running node as working only when `liveness` is `live`, and name `quiet`, `orphan` and `unreported` as what they are.
- `structure.source` is `folder` when the team's shape was read from the `structure/` folder (see `structure/README.md`), else `builtin`. With a folder, `groups` are its sub-folders in name order (`hub` first), an agent's `group`, `pool` and `parent`, and the `escalation` routes, come from the files in it, and an interface lays the groups out in that order. Change the folder and the next snapshot changes with it.
- `structure.flows` lists the flows drawn in the structure folder's `flows/` (see `structure/README.md`), in id order: `{"id": "impl-openai-verifier-impl-openai", "path": ["impl-openai", "verifier", "impl-openai"], "text": "...", "name": null, "command": null, "source": "drawn", "rev": "0123456789abcdef", "stats": null}`. `name` is the path's name or null; `command` is the name when the path is a slash command, else null; `rev` is described below. `stats` (live snapshots only) is null for a flow that never ran, else `{"runs", "passed", "rounds": {"avg", "max"}, "cost": {"total", "avg"}, "last"}`, where `last` is the newest run's `updated` time in Unix seconds (null when no run has one); see `GRAPH_FORMAT.md`, "What a path costs", for where each number comes from; a number that cannot be had is null, never guessed. A flow no task graph can show (`A → A`, or one agent and the hub) has `stats` with every field null: unknown, not "never ran" (that is `stats: null`). Live snapshots also carry `structure.commands_live` (`false`) and `structure.commands_applies`, the sentence saying when OpenCode sees a new command (measured on OpenCode 2.3.2: after `/reload` or a restart, not at once). A flow is a path through two or more agents, each agent's output being the next one's input; a step to an agent already on the path sends the work back to it. `text` is the user's instruction (or the one made from the path), plain text. `agentgraph check` enforces them (see `GRAPH_FORMAT.md`). A path of one agent twice, `["impl-hard", "impl-hard"]`, is that agent's loop on itself: it checks its own work against the task's evaluation and repeats until it passes (that is its made text). Any agent may have one, the hub included, and at most one; `check` prints it and adds no rule for it. A file that cannot be read as a flow is left out and reported in `warnings`, never as an error, and so is a second loop on the same agent (a flow whose path names an agent the folder no longer has gets a warning that ends with "`agentgraph flow remove ID` removes it (and its command file)"); the folder is read up to 200 flow files (in id order), and any beyond are named in `warnings`. Without a folder there are no flows.
- `structure.teams` is `[{"id", "lead", "members": [...], "text", "rev"}]`. It is `[]` on a fresh download, because `structure/teams/` is git-ignored. `agentgraph check` reports group problems next to the flow problems.
- `agents[].added` (live sources only) is `true` for an agent file this toolkit wrote; its frontmatter's first line is the comment `# agentgraph: added (...)`. An added agent has `skills` (read from its file) and `skills_editable: true`. Added agents appear in the next `GET /api/snapshot` without restarting the server.
- `agents[].removable` says whether Delete can remove the agent, and `agents[].removable_reason` says why not (`""` when it can), in the same shape as `skills_editable`/`skills_reason`. A hand-written agent file is listed but read-only: its `removable_reason` (and `skills_reason`) is "NAME is an agent file written by hand, not by this toolkit; it is read-only here. ...".
- `agents[].use_when` (live sources only) is the agent's `use_when:` setting in its structure file: one line saying when the orchestrator should pick it (`agentgraph.routing`; written by `agentgraph route use-when NAME --text TEXT`, which takes `--expect` the agent's `rev`). It is `""` when the file has none, the agent has no file, or there is no structure folder. Plain text, shown as text.
- `roster` (live sources only) is `{"editable", "why"?, "models": ["provider/model#variant", ...]}`. `models` lists the distinct models the presets and the added agents use.
- `skills[].editable` (and `why` when false) marks each installed skill.
- **`rev`.** Every drawn flow (`structure.flows[]`), group (`structure.teams[]`), skill (`skills[]`) and agent (`agents[]`, live sources) carries `rev`: 16 lowercase hex digits computed from its file's bytes (`agentfiles.rev`: a flow's or group's file, a skill's `SKILL.md`; an agent's from its file, its preset entry and its flows and groups, `roster.agent_rev`). It changes whenever the thing does, and only then: a thing removed and drawn (or written) again with identical bytes has the same `rev`, because it is the same thing, so a change expecting that `rev` is accepted. A view sends it back as `expect` on a change (section 3).
- **Names.** One definition per kind, in `agentgraph/names.py`: an agent `[a-z][a-z0-9_-]{0,63}`, a skill `[a-z][a-z0-9-]{0,63}`, a flow id `[a-z0-9][a-z0-9-]{0,79}`, a flow name and its command name `[a-z0-9][a-z0-9-]{1,31}`. The server, the command lines, the Desktop plugin's Ipc and the terminal panel all use these; a test compares the plugin's constants with them.
- There are no default loops: settings (`pool`, `escalates_to`, `seats`) are data shown in the details and never draw a line by themselves. A fresh structure folder has agents and no connections; at rest, with no flows and no task graph, an interface draws only the hub's lines to its agents and the council's to its seats.
- `agents[].about` is the agent's description: the text of its file in the `structure/` folder after the settings lines, whitespace collapsed to single spaces, at most 600 characters. It is `""` when the file has no text, when the agent has no file (a councillor seat, an agent outside the folder), and whenever `structure.source` is `builtin` or the source is the demo. An interface shows it as plain text (never markup), first in the agent's details, and shows nothing when it is empty.
- `skills` (live sources only) lists the installed skills: `name`, a one-line `description`, `editable` (and `why`) and `rev`. Which agent may load which is `agents[].skills`.
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
- `mcp` (live sources only; absent in the demo and from an older back end) lists the MCP servers there are, read only: `[{"name", "source": "plugin"|"opencode", "kind": "local"|"remote"|"", "enabled", "state": "on"|"off", "reason": "", "live": false, "agents": [agent names]}]`, sorted by name (`agentgraph/mcpview.py`). `agents[].mcps` (live sources only) is the names of the servers that agent may use, `[]` for none, or `null` for an agent the plugin does not manage (OpenCode's own `build` or `plan`, an agent file). The rule is the plugin's own (oh-my-opencode-slim 3.0.6, `dist/index.js`: `getAgentMcpList`, `DEFAULT_AGENT_MCPS`, `parseList`, and the `availableMcpNames` of `buildAgentRegistry`): the servers there are the plugin's built-in `context7` and `gh_grep` (except `disabled_mcps`) and the keys of the `mcp` map of OpenCode's `opencode.json` (a host server of a built-in's name replaces it, source `opencode`); an agent's list is its `mcps` in the active preset (with its `extends` chain, under the top-level `agents`; no `mcps` takes the built-in default, a custom agent none); `*` allows every server except the `!`-denied ones, `!*` none, otherwise the listed names that exist. `state` from the settings alone is `on`, or `off` for an entry with `enabled: false` or `disabled: true`; `live` is `true` and `state` one of `connected`, `failed`, `needs sign-in`, `off` only where the host said so (the Desktop app, below), with its short `reason` for the first two failures (`""` otherwise). **Only names and states leave the settings.** A server's name must match `agentgraph/names.py` `MCP` (1 to 64 letters, digits, `_`, `-`, `.`; any other is not shown, nor named in any `mcps`); `kind` is `local` or `remote` only when the entry's `type` says exactly that. No command, argument, environment name or value, URL, header or other field is copied, logged or put in an error; `tests/test_mcpview.py` plants a marker in every one and looks for it in the snapshot, the endpoint's reply and the Desktop command's output. Not read: a project's own `opencode.json`, the `OH_MY_OPENCODE_SLIM_PRESET` variable and a preset switched in a running session.
- `agents[].activity` (present only on an agent that is busy now; an idle agent has no such key) says what its newest busy session is doing: `{"kind": "tool"|"reasoning"|"text", "tool": name or null, "text": first non-empty line, at most 160 characters, or null, "time": unix seconds}`. It is the last item of that session's last assistant message. `tool` is set for `kind` `tool` (the name only, never its input or output); `text` for `reasoning` and `text`. The text is plain (escape and control characters removed) and obvious secrets are masked. Read with one short indexed query per busy session in `agentgraph.chatfeed.activities` (about 0.5 ms for three sessions); `{}` when the database cannot be read, so the key is simply absent. The demo carries one for each busy demo agent (the hub and three workers, with different zones).
  - **A busy agent's activity has more.** Every busy agent's `activity` (the hub's and each worker's) has `zone`, `status` and, in the two views below, `target`; only the hub's also has `recent`. A worker's is read in `chatfeed.busy_activities` with the hub's own cleaning and masking, over one connection, and only when its item is a tool call (one short query over its newest assistant message; `reasoning` and `text` need none, and a worker costs nothing extra when it is thinking). `zone` is one of `files`, `terminal`, `search`, `web`, `thinking`, `messaging`, `tasks`, `spawn`, `tools`: the tool name (lower-cased) goes through a small table in `chatfeed.zone_of` (read/write/edit/glob/patch `files`; shell/bash `terminal`; grep/websearch/codesearch `search`; webfetch and browser tools `web`; question/skill and plan tools `thinking`; subagent/task `spawn`; todo tools `tasks`; task_message/task_reply `messaging`); `execute` goes by the first `tools.<namespace>` its code calls (`browser` `web`; `context7`, `gh_grep` `search`; any other `tools`); an unknown or MCP tool is `tools`. `kind` `reasoning` has zone `thinking`; `kind` `text` has no `zone` key (a view shows "replying"). `status` is the tool part's state: `running` (`streaming` too), `completed` or `error`; `null` unless `kind` is `tool`. A view that has no `zone` draws today's line.
  - `target` (every busy agent, and only in the two views, below; the same function, `chatfeed.target_of`, and the same masking for a worker as for the hub) is one short line saying what the tool acts on, or `null`: `webfetch` host and path only (no scheme, query string, fragment or user:password); `shell` the first non-empty line of the command; `read`/`write`/`edit` the path, the home directory shown as `~`; `grep`/`glob` the pattern; `websearch`/`codesearch` the query; `subagent`/`task` `<agent>: <description>`; `skill` its id; `execute` the first `<namespace>.<tool>` it calls; `question` and any other tool `null`. Never the content of a write or edit, a prompt, or a tool's output. It is plain text, masked (`mask_secrets`, and in addition the value of an assignment whose name has key/token/secret/password/credential/auth in it, anything after `Authorization:`, a `Bearer` token of any length, the value after `--password`/`--token`/`--api-key`, and `user:password@` in a URL) and cut to 120 characters with an ellipsis (`…`).
  - `recent` (the hub only, never a worker's; and only in the two views) is the hub session's last 5 tool calls before the current one, newest first (the last 5 when the current item is not a tool): `{"zone", "tool", "target", "status", "time": unix seconds, "seconds": completed minus ran, or null}`; `[]` for none. It looks at the last 6 assistant messages only, in `chatfeed.hub_activity` (one more short query for the hub). Any failure leaves `zone`, `status`, `target` and `recent` out and the four old keys stay.
  - **Who gets `target` and `recent`.** `GET /api/snapshot` and `python3 -P -m agentgraph.snapshot_json` (what the Desktop and terminal plugins run) carry them. `bin/agentgraph snapshot`, the command agents run inside a chat, does not: `bin/agentgraph` adds `--no-activity-detail`, so every agent's activity there (a worker's too) has `zone` and `status` but no `target` and no `recent`, unless `--activity-detail` is given after it. With `--no-activity-detail` the database is not asked for any tool input except an `execute` call's code (for its zone). The demo's hub has a made-up zone, target and trail of five (web, terminal, files, search, spawn; one `error`); its busy workers are `impl-claude` (`terminal`, `npm test -- middleware`), `designer` (`thinking`, no target) and `explorer` (`search`, `Retry-After`).
  - Example worker `activity`: `{"kind": "tool", "tool": "shell", "text": null, "time": 1795414730.1, "zone": "terminal", "status": "running", "target": "npm test -- middleware"}`. A view shows it as one calm mark on the planet and, in the agent's details, one line `Now: <the zone in words> · <N s ago>` and the target under it, dim and cut. The target is session text of a kind: a view shows it only in that line, never in a tooltip, a name or a label of the graph.
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
- `setup` (a live source; the demo has sample names) is where the default preset stands and what is saved: `{"preset", "presets": [names], "override": null|"env"|"project", "saved": [{"name", "saved", "preset", "rev"}], "undo"}`. `preset` is the top-level `preset` of the plugin's preset file (`null`, with `presets` empty and an `error` text, when the file cannot be read), `presets` the names of its presets in file order, `override` what takes precedence over it when OpenCode starts (`"env"` when `OH_MY_OPENCODE_SLIM_PRESET` is set, `"project"` when a `.opencode/oh-my-opencode-slim.json[c]` in the server's folder or one above it sets a preset; by name only, never its contents), `saved` the setups under `setups/` (`saved` is the UTC time, `rev` is the `expect` value for a replace, restore or remove; a setup whose manifest cannot be read has `"damaged": text`), `undo` the path of the newest undo record in the backups folder (`<date>-setups/...`), or `null`. It is cheap: one file parsed and the manifests listed; no setup file is hashed. A switch of the preset applies at the next start of OpenCode.
- `spend` (live and demo; absent from an older back end) is the project's spend limit and where the tree's cost stands against it: `{"limit": dollars|null, "warn": dollars|null, "rate": dollars an hour|null, "chat": dollars, "workers": dollars}` (`agentgraph/spend.py`). `limit` is kept in `<project>/.opencode/spend.json` (`agentgraph spend limit AMOUNT [--warn W]`, `--off` removes it); `warn` defaults to 90% of the limit and is always below it; with no limit both are null. A spend file that cannot be read counts as no limit and is never echoed. Nothing is stopped at the limit: the view only draws the total against it, in its attention colour from `warn`, and says so plainly at and over the limit. `chat` is the root (orchestrator) session's cost and `workers` the cost of every other session of the tree, so `chat + workers` is `totals.cost`. `rate` is computed: the cost of the assistant messages the tree's sessions made in the last 30 minutes, times two (dollars an hour); null when the database or its columns are missing (the view then shows no rate). The demo has a sample (limit 10, warn 9, rate 4.2).
- `sidechat` (a live source; the demo has a sample) is the side chat's state: `{"installed": bool, "session": the side session id or null, "model": "provider/model" or null, "busy": bool (a question is being answered), "spent": US dollars the side session has cost, present only when known}` (`agentgraph sidechat status`). It is read only; there is no route that asks the side chat a question (a Desktop host asks it through `options.actions.side`, section 4). The view draws it as a small moon named "side chat" only when `installed` is true.
- All text is passed through as data. The interface must treat it as untrusted text and never as markup.

## 2. The commands

- `agentgraph [--demo] [--dir PATH] [--session ID] [--once] [--html PATH] [--width W] [--height H] [--focus all|agents|tasks|loops|events|issues] [--scroll N] [--interval S] [--no-color] [--at UNIX_TIME]` is the terminal view of the same data (`--help` lists what each does; see `README.md`). It reads the same snapshot and writes nothing; `--once` draws one frame to stdout, `--html PATH` one frame as a standalone page, `--at` draws as if the clock read that time.
- `agentgraph status --task NAME [--dir PATH] [--db PATH] [--json]` (`python3 -m agentgraph.status`; read only) says where one task stands, from the graph file and the database, so a restart resumes without guessing. Plain text is a few lines: the graph's progress, `check:` (the existing `agentgraph check` in one line), `ready:`, `running:` (each node with its liveness and, when quiet, how long it has been idle), then only when present `returned, to evaluate:`, `busy but not running in the graph:`, `stale inputs:`, `passed, inputs changed:` and `stall candidates:`. With `--json` it prints `{"task", "status", "revision", "path", "progress": {"passed", "total"}, "database": bool (false: liveness unknown), "check": {"ok", "exit", "headline", "problems": [...], "warnings": n}, "ready": [node id], "running": [{"id", "agent", "session", "liveness", "idle_since", "idle_seconds", "started"}], "returned": [node id], "unreported": [{"id", "status", "session"}], "stale_inputs": [{"node", "from", "artifact", "read", "current"}], "passed_inputs_changed": [node id], "stall_candidates": [{"id", "reasons": [...]}], "liveness": {"live", "quiet", "orphan", "unreported"}}`. `ready` is every `pending` node whose dependencies are all `passed` and whose inputs are current; a stall candidate is a node not `passed` with 3 or more attempts, `stalled` set, two `attempt`/`retried` events with the same note, or (from the second attempt) two `evaluated` events with the same note. Exit 0; 2 when the task or graph folder is not found (message on stderr, nothing on stdout).
- `agentgraph snapshot [--demo] [--dir PATH] [--session ID] [--task NAME]` prints the document as one line of JSON and exits 0. On an internal error it prints `{"version": 1, "error": "<message>"}` and exits 1.
- `agentgraph serve [--port N] [--demo] [--dir PATH] [--session ID] [--open]` starts the web server (default port 4747) and prints its address. It listens on `127.0.0.1` only.
- `agentgraph structure flows [--json]` lists the drawn flows (`--json`: `{"flows": [...]}`, the shape of `structure.flows`). `agentgraph structure flow add A B [C ...] [--text-file F|-] [--json]` adds the flow along that path, with the instruction read from file `F` or from standard input (`-`); without it, or when it is blank, the instruction is made from the path. `flow text ID --text-file F|- [--json]` replaces a flow's instruction (blank: the one made from the path) and `flow remove ID [--json]` removes it. They write only in the structure folder's `flows/` (one file per flow; a new file is linked into place so it never replaces another, a changed one is renamed over the old, and the folder is removed with its last flow), one change at a time under a lock, and never touch an agent's file. They refuse, with a message and exit 1 (with `--json`, `{"error": "..."}` on stdout): fewer than two agents, an unknown agent, the same agent twice in a row inside a longer path (`A A` alone is the agent's loop on itself; a second one for the same agent is refused with "already has one"), the hub anywhere but first or last, a return straight after the work came back to the first agent, more than 20 agents, an instruction over 2000 characters (Unicode characters, not UTF-16 units) or with control characters other than newline and tab, an unknown id, and a new flow when the folder already holds 200. A council seat is stored as its council, with a note. With `--json` they print `{"flow": {...}, "flows": [...], "notes": [...]}` (`flow` absent after a remove, `notes` only when there are some). An instruction that is empty or only whitespace always means "make it from the path", for `add` and for `text` alike. `--structure DIR` names another folder (for tests) and comes after the subcommand. Never runs git.
- `agentgraph structure flows --stats [--json] [--ledger DIR] [--graphs DIR] [--db PATH]` adds to each flow its `stats` (section 1): the ledger is read from `--ledger` (default: the active preset's ledger folder), more graph files from each `--graphs` folder, costs from `--db` (default: OpenCode's database). Read-only. `structure flows` also lists the flow files that cannot be read as flows (`--json`: `"unreadable": [{"id", "reason", "rev"}]`) with how to remove them; `flow remove ID` removes such a file too, and its command file while that file is exactly the one written for it and no readable flow holds the name.
- `agentgraph flow ...` is the same as `agentgraph structure flow ...`.
  - `flow add ... --name NAME [--command]` adds a flow with a name (and its command on: the way back from a removal).
  - `flow name ID [NAME] [--json]` names a flow: 2 to 32 lowercase letters, digits and `-`, starting with a letter or a digit, unique among flows. No NAME takes the name away. A flow whose command is on gets its command file renamed with it, and cannot lose its name until the command is off.
  - `flow command ID --on|--off [--commands DIR] [--json]` turns the flow's command on or off (it needs a name). On writes `NAME.md` into the commands folder (default `~/.config/opencode/commands/`); off deletes it. `--json` adds `"applies"` (what was measured: OpenCode reads its commands folder when it starts and when its configuration is reloaded, not at once) and `"live": false`. See `COMMANDS.md`, "Paths as commands".
  - `flow prompt ID --goal-file F|- [--json]` prints the run message (with `--json`, `{"text": ...}`). `flow prompt --name NAME --goal-file F|-` does the same for the path named NAME (what a path's command tells the orchestrator to run); give a flow id or `--name`, one of them, else it is refused ("give a flow id or --name NAME, one of them"). The goal is required, at most 4,000 characters, with newline and tab the only control characters allowed.
  - `flow text` rewrites the command file of a flow whose command is on; `flow remove` deletes it with the flow. `--commands DIR` comes after the subcommand, like `--structure`.
- `agentgraph agent list | show NAME | add NAME [...] | text NAME --text-file F|- | rename NAME TO | remove NAME | restore RECORD [--json]` is the same as `POST /api/roster` (section 3).
  - `agent add NAME` with no other option adds a blank agent: the description "An agent you added. Open it to say what it does.", an empty instruction, the default verifier's model (with its variant) and the writer permissions. `--template blank|verifier` or `--copy-of AGENT` start from the blank agent, the verifier or a copy instead; `--model M`, `--variant V`, `--description D`, `--prompt-file F|-`, `--skills a,b` (repeatable; `--skills=` for none) and `--read-only`/`--no-read-only` each replace a default.
  - Path options: `--config`, `--map`, `--backups`, `--structure`, `--agents`, `--skills-dir` and `--commands` (a rename rewrites the commands of the paths through the agent).
  - `list` shows the preset agents (`built-in` or `custom`) and the agent files in `--agents` that are not preset agents, each `added` (by this toolkit) or `hand-written`, with the reason when read-only. With `--json`, the files are in `agent_files: [{"name", "kind": "added"|"hand-written", "editable", "reason", "model", "description", "skills", "remove": "delete"|null}]`. `show` answers with the agent's `rev`.
  - A `remove` that fails leaves no undo record and no empty record folder behind. `remove` is refused while a flow through the agent is a command (its command file is outside the removal's undo record); turn the command off first.
  - Exit codes: 0 on success; 1 when refused (with `--json`, `{"error"}` on stdout); 2 on a usage error.
- `agentgraph skill show ID | create ID --description D --text-file F|- [--assign A] | write ID --text-file F|- [--description D] | delete ID | restore RECORD [--json]` is the same as `POST /api/skillfile`. Path options: `--skills`, `--backups`, `--config`, `--map`, `--profiles` and `--agents`.
- `agentgraph team add LEAD MEMBER... [--text-file F|-] | members ID MEMBER... | text ID --text-file F|- | remove ID | list [--json]` is the same as `POST /api/structure/team`. `agentgraph structure teams [--json]` lists the groups as `{"teams": [...]}`. Both take `--structure DIR`.
- **`--expect REV`.** Every command that changes or removes an existing thing takes `--expect REV`: `flow text|name|command|prompt|remove ID`, `team members|text|remove ID`, `skill write|delete ID`, `skills add|remove|all AGENT` (the agent's rev) and `agent text|rename|remove NAME`. When the thing's rev is no longer REV, the command changes nothing and exits 1 with "… changed since you opened it; reload and try again": with `--json` the last line is `{"error": "...", "rev": "<current rev>"}` (`rev` is `null` when the thing is gone); without it, stderr ends with `(rev now X)`. An empty `--expect ""` is bad input (exit 1, "expect must be text, not empty"), never taken as no `--expect` and never compared. Without `--expect` every command behaves as before. A read (`show`, `flows --json`, `teams --json`) answers with each thing's `rev`.
- `agentgraph preset show | switch NAME | restore RECORD [--json]` and `agentgraph setup save NAME [--replace] | list | show NAME | restore NAME | remove NAME | undo RECORD [--json]` (`agentgraph.setups`; `COMMANDS.md`, "Presets and saved setups") show and switch the default preset and keep named setups of the files the toolkit manages. `--config-dir D` (the preset file, `agents/`, `commands/` and `backups/` in it), `--orchestration D` (`SKILLS.md`, `structure/`, `setups/`), the single-file options `--config`, `--map`, `--agents`, `--structure`, `--commands`, `--setups`, `--backups`, and `--dir PROJECT` (where a project override is looked for; default: here) name other places, for tests. `save`, `restore` and `remove` take `--expect REV`. A refusal prints a line to stderr (with `--json`: `{"error", "rev"?}` on stdout) and exits 1.
- `agentgraph skills show AGENT | add AGENT SKILL | remove AGENT SKILL | all AGENT [--json]` shows or changes which skills an agent may load, in the plugin's preset file (every preset that lists the agent; a backup is written first) and in `SKILLS.md`. `all` gives the agent `["*"]`; removing one skill from an agent with `*` leaves the list of every installed skill but that one. It refuses, with a message and exit 1: an unknown agent or skill, a council seat, and a name outside `agentgraph/names.py` (one that starts with `-` included). `--config F`, `--map F`, `--backups DIR`, `--agents DIR`, `--skills-dir DIR`, `--structure DIR` and `--preset NAME` (repeatable) name other files and presets (for tests). A preset change applies after OpenCode restarts; an added agent's skills are read from and written to its agent file, the change is live at once, and `message` says so. With `--json` it prints `{"agent", "preset", "skills": [...], "warnings": [...], "changed", "message", ...}`.

## 3. The server

- `GET /` and `GET /index.html` serve `agentgraph/web/index.html`. `GET /app.js`, `GET /style.css` and other files directly inside `agentgraph/web/` are served by name. Nothing outside that directory is ever served.
- `GET /api/chat?session=ID[&limit=N][&demo=1]` returns one session's recent chat (`agentgraph chat`, `agentgraph.chatfeed.read_chat`), `Cache-Control: no-store`, `application/json`. GET and HEAD only (405 `Allow: GET, HEAD` for anything else), under the same `Host` guard as every route. `limit` is the number of latest messages, 1 to 200 (default 40). Only a session of the tree the snapshot shows is served: the root session and the sessions listed under the agents. The one other session served is the side chat's own (`sidechat.session`; its transcript, each question cut before its brief, no reasoning). Any other session id gets a bare 404 without detail; a missing, repeated or malformed `session` or `limit` is 400. With `demo=1` (or a demo server) the demo sessions' example chats are served and every other id is 404.
  - Body: `{"session", "agent", "busy", "items", "truncated", "more", "warning"}`. `items` is oldest first. Each item is `{"kind", "time"}` plus: `text` and `cut` for `reasoning` (the model's visible thinking), `text` (what the agent said) and `user` (a prompt, or a steered message once it has landed; one still waiting in the inbox has `"pending": true` and `"delivery"`); `name` and `status` (`running`, `streaming`, `completed`, `error`) for `tool`; nothing more for `note`, a message the host wrote itself (a shell result, a sub-agent's reply), whose text is never read. `text` is at most 2000 characters (`cut` is true when it was longer); the whole reply holds about 24000 characters, the oldest items dropped first. `truncated` is true when some text was cut or items were dropped for size; `more` is true when older chat exists. `warning` is `null`, or one line when the database is missing, locked or unreadable (the items are then empty, never a traceback).
  - Never read: a tool's input (`state.input`) or output (`state.content`), the model's `native` state, any `metadata`, a tool's error text. Text is capped in SQL (`substr` over `json_each`/`json_extract`), so a large message never reaches Python. Text is plain (ANSI/OSC escapes and control characters removed) and obvious secrets (Bearer tokens, `sk-`, `ghp_`, `xox` and `AKIA` keys, `password=`/`token=`/`api_key=` values, PEM private keys) are masked: a second line of defence only. Untrusted text, as everywhere in this document.
  - `agentgraph chat --session ID [--limit N] [--json | --text] [--chars N] [--demo]` prints the same feed: `--json` is the object above on one line; the default is a plain block, `[user] ...`, `[thinking] ...`, `[says] ...`, `[tool] name (status)`, newest content kept when over `--chars` (default 24000). An id that is not `ses_` plus letters and digits exits 2 with one line.
- `GET /api/snapshot` returns the document. Query parameters: `demo=1` (sample data), `task=NAME`. `Cache-Control: no-store`.
- **Stale writes.** Every `POST` op that names an existing thing accepts an optional `"expect"`: the `rev` the view was showing (section 1). `/api/structure/flow`: every op but `add` (`add` with `"expect"` is 400); `/api/structure/team`: `members`, `text`, `remove`; `/api/skillfile`: `write`, `delete`; `/api/skills`: every op (the agent's rev); `/api/roster`: `text`, `rename`, `remove`. A stale `expect` changes nothing and answers **409** with `{"error": "... changed since you opened it; reload and try again", "rev": "<current rev or null>"}`; an `expect` that is empty or not a string is 400 (never taken as no `"expect"`, never compared). The answers carry the new `rev` of what they changed (`flow.rev`, `team.rev`, a skill write's `rev`, the agent's `rev` on `/api/skills` and `/api/roster`). Without `"expect"` every op works as before.
- `POST /api/structure/flow` changes the flows exactly like `agentgraph structure flow`, with the JSON body `{"op": "add", "path": [A, B, ...]}` (optional `"text"`, `"name"`, and `"command": true|false`, which needs the name), `{"op": "text", "id": ID, "text": T}`, `{"op": "name", "id": ID, "name": N}` (`""` takes the name away), `{"op": "command", "id": ID, "on": true|false}`, `{"op": "prompt", "id": ID, "goal": G}` or `{"op": "remove", "id": ID}`, each but `add` with an optional `"expect"`. It answers `{"flow"?, "flows": [...], "notes"?}` like the command's `--json` (`flow` absent after a remove); `command` adds `"applies"` (a sentence: what was measured about when OpenCode sees the command) and `"live": false`; `prompt` answers `{"text": ...}` only and writes nothing. It is refused with `{"error": "..."}`: 403 for a client that is not loopback, an `Origin` header (when present) other than `http://` plus the request's own `Host`, or a demo server (except `prompt`, which is read-only and works on the demo server, reading the structure folder the server was given); 415 for a body that is not `application/json`; 411 or 413 for a missing, empty or oversized (over 32768 bytes) body; 400 for a body with any other keys, a value of the wrong type (`"on"` and `"command"` must be booleans, `"path"` a list, the rest strings) or a change the command refuses; 409 for a stale `expect`. Any other method on this path is 405 with `Allow: POST`. An `"op": "text"` whose `"text"` is empty or only whitespace gives the instruction made from the path. The server writes command files into the folder given as `make_server(commands_dir=...)` (default: OpenCode's commands folder).
- Refusals specific to names and commands, each changing nothing:
  - a name that is not 2 to 32 lowercase letters, digits and `-`, or that another flow has;
  - a command for a flow without a name;
  - a command name OpenCode, the oh-my-opencode-slim plugin or this toolkit already has (`init`, `review`, `goal`, the Desktop and terminal slash commands, `deepwork`, `reflect`, `loop`, `interview`, `implement`, `agent-skills`, `flow`, `flows`, `graph-check`, `learn`, `note`, `spend`);
  - an existing `NAME.md` the toolkit did not write, or wrote for another path;
  - a command file changed by hand (it is never replaced or removed);
  - an instruction holding `$ARGUMENTS`, `$1`…`$9` or `` !` `` while the command is on (OpenCode would replace or run them). An `@path` file reference is allowed: OpenCode 2.3.2 sends it as plain text in a command, never attached, fenced or not (`n96/r2/at-reference-proof.md`).
- **What the user types after `/NAME`.** It is their own typing, and OpenCode treats it as it treats the text of any command, including running a `` !`...` `` in it as a shell command (outside the agents' permissions, even inside the goal's fenced block). The toolkit never sees that text before OpenCode does and cannot check it; the command file it writes holds no such form. The run message fences the goal as data for the orchestrator, which is no protection against the host's own expansion.
- `POST /api/output` reads a step's output, saves a new text as its next revision, or undoes that, exactly like `agentgraph output read|write|restore ... --json` (`agentgraph.outputs`; `GRAPH_FORMAT.md`, "Outputs edited by hand"). Bodies: `{"op": "read", "task", "node", "artifact"}` → `{"task", "node", "artifact", "path", "revision", "rev", "size", "text", "editable": true}`; `{"op": "write", "task", "node", "artifact", "text", "expect"?}` → `{"task", "node", "artifact", "changed": true, "output", "rev", "stale": [{"id", "agent", "goal", "status", "read"}], "record", "message", "warnings"?}` (the text unchanged: `"changed": false` and no `record`); `{"op": "restore", "record"}` → `{"task", "node", "artifact", "changed": true, "restored": true, "output", "rev", "text"?, "message", "warnings"?}` (`warnings` when the edit log could not be marked undone). `output` is the entry as the snapshot shows it: each `graph.nodes[].artifacts[]` there also carries `rev` (16 hex digits from the file and the graph's record of it), `size` (bytes, `null` without a file), `editable` and `why` (`""` when editable, else the plain reason). A write is a new revision `r<N+1>/<name>` beside the old one, so every step that read the old one is stale for `agentgraph check`, and `stale` names them; nothing is run. Text: UTF-8, at most 256 KiB, no control character but tab, newline and carriage return (a NUL is refused as `the new text is not text: it holds the control character U+0000; nothing was written`, by the Desktop's main process too). Refusals: 409 `{"error", "rev"}` for a stale `expect` (nothing written), 400 `{"error"}` for an output that cannot be opened (`... cannot be opened here: <why>`), an unknown task, step or output, a malformed `expect`, an unknown key, or an undo that cannot be done (a later run replaced the edit, its file changed since, or its graph is not in the graph folders this server shows: `... not in this project ...`, nothing written). An output file with another name (a hard link) is neither read nor written. It has every protection of the other endpoints (below; a demo server refuses it). The undo records go into `make_server(skills_backups=...)` (`<date>-outputs/edit-<id>/record.json`) and the edit log into the ledger root's memory folder.
- `POST /api/setup` switches the default preset and keeps named setups, exactly like `agentgraph preset ...` and `agentgraph setup ...` `--json` (`agentgraph.setups`). Bodies: `{"op": "preset-switch", "name"}` → `{"preset", "was", "changed": true, "backup", "previous", "record", "message"}` (the plugin reads the preset at the next start of OpenCode; a session already open keeps its models); `{"op": "preset-restore", "record"}` undoes it byte for byte; `{"op": "save", "name", "replace"?: bool, "expect"?}` → `{"name", "changed": true, "replaced", "preset", "files", "counts", "record", "message"}` (a name in use is refused unless `replace`; `expect` is the replaced setup's `rev`); `{"op": "restore", "name", "expect"?}` → `{"name", "changed", "preset", "written", "removed", "record", "message"}` (every hash is checked first; a managed file the setup does not have is removed); `{"op": "remove", "name", "expect"?}` → `{"name", "changed": true, "record", "message"}` (the setup is moved to `<backups>/<date>-setups/`); `{"op": "undo", "record"}` puts back everything a restore, a remove or a save over a name changed (`{"name", "changed", "record", "message"}`; its `record` undoes the undo). A setup holds the preset file, the native agent files that carry the toolkit's marker, the structure folder's agent files, `teams/` and `flows/`, the path command files that carry the marker and `SKILLS.md`, each with SHA-256 and size in `manifest.json`; never `opencode.json`, credentials, prompts or skill files. A name is a plain id (`names.py`, a flow id). Refusals: 409 `{"error", "rev"}` for a stale `expect` or an undo of a change that was changed since (nothing written), 400 `{"error"}` for an unknown preset or setup, the preset already in use, an env variable or project config that sets another preset, a name in use, a name that is not a plain id, a damaged setup, a record that is not one of the backups folder's, a malformed `expect` or an unknown key. Every protection below applies; a demo server refuses it with 403; the body is at most 4096 bytes. The folders are parameters of the server (`skills_config`, `skills_map`, `agents_dir`, `structure_dir`, `commands_dir`, `setups_dir`, `skills_backups`; the real ones by default), so tests pass copies.
- `POST /api/spend` keeps or removes the project's spend limit, exactly like `agentgraph spend limit ... --json` (`agentgraph.spend`). Bodies: `{"op": "limit", "limit": dollars, "warn"?: dollars}` and `{"op": "off"}`; both answer `{"limit", "warn", "previous": {"limit", "warn"}, "changed", "message"}`. An amount is above 0 and at most 1,000,000 dollars, rounded to the cent; `warn` is below the limit (default 90% of it). The limit is a setting of the project, not a file with a `rev`, so there is no `expect`; the answer's `previous` is what undoes the change (`limit` with it, or `off` when `previous.limit` is null). The body is at most 4 KiB. Refusals: 400 `{"error"}` for an amount that is not dollars (`$` and a plain number are taken; anything else, a negative or an exponent is not), a `warn` at or above the limit, an unknown `op`, missing or extra keys; a lock held too long is a 400, not a hang. It has every protection of the other endpoints (below; a demo server refuses it). The limit is written to `<project>/.opencode/spend.json` under `spend.lock`; the file holds the two amounts and nothing else.
- `GET /api/improve` and `POST /api/improve` show and decide the improvement proposals of the active preset's ledger (`<ledger home>/proposals/<id>.json`; `agentgraph.improve`, `python3 -m agentgraph.improve`). `GET /api/improve` answers `{"proposals": [{"id", "created", "title", "state", "summary", "added", "removed", "files"}]}`, newest first: `state` is `drafted`, `evaluated`, `applied`, `rejected` or `reverted`; `summary` is `{"tune": {"before": [passes, runs], "after": [...]}, "held_out": {...}, "verdict": "better"|"better on tune only"|"no difference"|"worse"|"not enough runs", "text"}`; `added`/`removed` count the lines of the proposal's own change: for an applied proposal the backup against the new text, otherwise the files as they are now against it. `GET /api/improve?id=ID` answers the whole record (`why`, `evidence`, `evaluation`, `history`, `applications` with the backup folder and each file's hash before and after, `changes` with the new text) and, per file, `files: [{"path", "diff" (unified; against the backup for an applied proposal, `diff_against` is `"backup"`, else against the file now, `"file"`, also when the backup is gone), "added", "removed", "base_matches", "after_matches", "current_sha256", ...}]`; an unknown id is 404 `{"error"}`. `POST /api/improve` takes `{"action": "approve"|"reject"|"reopen"|"revert", "id", "note"?}` and answers the proposal as `GET ?id=` does. `approve` is the user's own approval (`who: "user"`; `note` is their words, default "approved in the view"): every file must still have the hash the proposal was drafted against, each is copied to `<config root>/backups/<time>-improve-<id>/`, the new text is written atomically (all files or none), and an `improved` row goes to the ledger. `reject` needs a `note` (the reason) and changes no file; `reopen` undoes it for a rejected proposal (back to `evaluated` if it has results, else `drafted`; `note` optional). `revert` is for an applied proposal only and puts back the exact bytes from the backup (or deletes a file the proposal created); it is refused when a file was edited after applying. Neither can be forced from here (`--force` is a command-line flag). Only `AGENTS.md` and `*.md` files under `oh-my-opencode-slim/`, `skills/` and `agents/` of the config root can be changed, never through a symlink; no JSON setting. Refusals are 400 `{"error"}` (hash mismatch, wrong state, bad key or type, a path outside those). The ledger home comes from `ledger_root` as for `/api/output`, the config root from `improve_config_root` (default `opencode_dir`, else OpenCode's config folder), so tests pass temporary folders; the body is at most 4096 bytes; a demo server answers GET with no proposals and refuses POST with 403; it has the protections below (loopback client, own Origin, JSON, bounded body), and `GET`, `HEAD` and `POST` are the only methods.
- `GET /api/loops`, `GET /api/loop/run?id=RUN` and `POST /api/loop` save a flow's loop and run it (`agentgraph.loopapi` over `agentgraph.looprun`; the contract is `.opencode/artifacts/agentgraph-loops/v1/r1/api.md`, the controller `docs/LOOPS.md`). The server holds no run state: a run is its own detached process and `<project>/.opencode/loops/<run id>/` is the truth; the saved loop is `<structure>/.loops/<flow id>.json`. `GET /api/loops` answers `{"project", "scratch", "loops", "orphans", "runs"}`: `loops` has one entry per drawn flow in the structure's order, `{"flow", "name", "path", "agent", "runnable" (only a self-loop `A -> A`), "reason" (the controller's words when not), "settings" (validated, defaults filled; null when nothing is saved or the file is unusable), "problem" (why a file is unusable, else ""), "rev" (sha256 of the file's bytes, "" without one)}`; `orphans` are flow ids with a settings file and no flow (removing a flow leaves its loop: adding the same self-loop again picks it up); `runs` are the newest 50, `{"run", "flow", "status" (shown: a `running` run with no live controller is `interrupted`), "reason", "live", "attempts", "limit", "cost" (number or null: unknown is never 0), "seconds", "created", "ended", "goal" (200 characters), "check", "last_check": {"outcome", "exit"} or null}`; `scratch` is true when the server was given a scratch config for runs. `GET /api/loop/run?id=RUN` answers `{"run" (run.json with the shown `status` and `live`), "definition" ({settings, path, steps, steps_sha256, goal, workdir, created}), "attempts": [{"n", "message" (what the agent was sent), "check_output" (the last 20,000 characters), "check_output_truncated"}]}`; an id that is not `YYYYMMDD-HHMMSS-xxxx` is 400, an unknown run 404. `POST /api/loop` takes `{"op": "save", "flow", "settings", "rev"}` (the whole settings object, `version` and `flow` optional; `rev` is what the page read, "" for a first save; answers `{"ok", "settings", "rev", "before"}`, `before` being the settings it replaced or null: Undo posts `save` with it, or `remove` when null), `{"op": "remove", "flow", "rev"}` (answers `{"ok", "before"}`; works on an orphan; runs already made stay), `{"op": "start", "flow", "goal"?, "workdir"?}` (an absolute existing folder, default the project; answers `{"ok", "run", "status": "running"|"invalid", "reason"}` as soon as the run folder is written: a loop with no check or no goal is a run with status `invalid` and no attempt) and `{"op": "cancel", "run"}` (answers `{"ok", "run"}`; the run stops before the next attempt or now). Refusals: 400 `{"error"}` with the controller's message (unknown key, attempts missing, not a self-loop, unknown flow, bad run id, missing work folder, bad op or shape), 409 `{"error", "rev"}` when `rev` is stale (cancel of a run that is not running is 409 `{"error"}`), 404 for an unknown run on `cancel`. `save` and `remove` compare `rev` under the controller's lock. The project is `loops_project` (default `cwd`), the structure folder `structure_dir`, `loop_scratch` the scratch config a started run uses (default none: the live config) and `loop_executor_spec` replaces OpenCode with a scripted executor (tests); the body is at most 256,384 bytes; a demo server answers both GETs with nothing and refuses POST with 403; the protections below apply, `GET`/`HEAD` for the two reads and `POST` for the write.
- Release 3 of the loop API (the same three endpoints; `docs/LOOPS.md` has the controller). `GET /api/loops` adds `versions` to every loop entry (the kept earlier saved loops, newest first, `{"version", "time", "sha256", "changed"}`; at most 50, written when a save replaces a file and when it is removed; `changed` is the sorted list of dotted setting paths, such as `goal`, `check.command`, `limits.attempts`, `steps.verifier.session`, `artifacts` or `approval`, whose values differ between that stored version and the loop as saved now: `[]` when identical, no values, a section missing on one side listed by its leaf paths from the other, and every path of the version when no loop is saved now; both sides are compared with defaults filled in when both are valid loops, else as plain JSON) and `trial` (bool), `settings_sha256`, `passed_by`, `waiting` ({"step", ...} or null) and `answered` (bool: `true` only while the run is `waiting` and a person's answer is saved and no controller has taken it yet, so the run is waiting to be resumed; `false` for any run that is not waiting, and again once a controller takes the answer) to every run, and `GET /api/loop/run` carries the same `answered` in `run` next to `live`; the shown `status` of a run also says `paused` (stopped on request, or a step's fate could not be told after a restart: `reason` says which) and `waiting` with or without a controller. `GET /api/loop/run` adds to every attempt `steps: [{"step", "try", "message", "reply", "findings"}]`: the texts of what was sent, the reply and a judge's findings for each turn, the last 20,000 characters each, "" when there is none. `POST /api/loop` has four more ops and one more field: `{"op": "pause", "run"}` (the controller stops after the step it is on; answers `{"ok", "run"}`; 409 unless a controller is running it), `{"op": "resume", "run", "choice"?: "redo"|"accept"}` (a new controller from the cursor and the pinned definition; answers `{"ok", "run", "status": "running"}`; `choice` is for a paused step whose fate is unknown; 409 for a run that is over, queued or has a live controller; 400 for a choice that does not fit), `{"op": "answer", "run", "answer": "approve"|"reject"|"stop", "note"?}` (answers `{"ok", "run"}`; 409 when the run is not waiting or already has an answer; a waiting run with no controller takes it at the next `resume`), `{"op": "restore", "flow", "version", "rev"}` (puts a kept version back; answers like `save`: `{"ok", "settings", "rev", "before"}`; 409 with the current `rev` when stale; 400 for a version id that is not one or not there), and `start` takes an optional `"settings"` object: a trial run that pins those settings and neither reads nor changes the saved loop. `cancel` now also works on a run with no controller (interrupted, paused, waiting, or queued whose controller never started): it is marked `cancelled` and the reply is `{"ok", "run", "result": "cancelled"}`; with a live controller the reply is as before. The shown `status` is `interrupted` for a run still `queued` 30 s after it was created with no controller (nothing was sent; `resume` starts it, `cancel` ends it; `agentgraph loop status` says so in words); `resume` of a queued run younger than that is 409, an older one starts. A run whose detached command could not start its controller is stored with a final status (`agent_error` when the lock stayed held, `invalid` otherwise) and the reason. A `resume` whose pinned `settings_sha256` or `steps_sha256` no longer matches `definition.json` and `run.json` (or has no pin there: a run made before `run.json` kept `steps_sha256`) is 400 in words; `GET /api/loop/run` therefore also carries `steps_sha256` in `run` (a key `run.json` now has; no key was removed or changed). A `resume` of a run a controller holds answers at once (409), and so does a `cancel` of a plainly running run (`{"run"}`); a `cancel` of a run that is paused, waiting or being paused waits up to 2 s for the controller's lock and, if the run then reads `paused`, marks it `cancelled` (`{"ok", "run", "result": "cancelled"}`) instead of leaving a request nobody will read. `answer` is 409 when the run stopped waiting, and 409 "run RUN already has an answer that was not taken yet" for a second one. A `cancel` of a run that is over is 409 "run RUN is already STATUS" and writes no request; a run's end removes any request it holds. Reading (`GET /api/loops`, `GET /api/loop/run`) writes nothing and does not follow a symlinked run folder. A run the definition routed onward to its end after a failed check or judge is `failed`, never `check_error`; a `human` route on `agent_error` or `no_artifact` is refused when the loop is saved. Errors: 400 with the controller's words, 404 unknown run, 409 wrong state, as for the other ops.
- The Desktop bridge's entry point: `python3 -P -m agentgraph.loopapi [--project DIR] [--structure DIR]` reads ONE JSON request line on stdin (at most 64 KB) and writes ONE JSON line on stdout. A request is `{"op": "read"}` (the body of `GET /api/loops`), `{"op": "read", "run": id}` (`GET /api/loop/run`), or any `POST /api/loop` body (`save`, `remove`, `restore`, `start`, `cancel`, `pause`, `resume`, `answer`, with the same keys and the same replies). `--project` is the session folder (omit it for none: `read` then lists no runs, `read` of one run is 404, and `start`, `cancel`, `pause`, `resume` and `answer` are 400 in words); `--structure` defaults to the page server's (`$AGENTGRAPH_PERSONAL` honoured). A failure is `{"error": text, "status": 400|404|409|500, "rev"?: current rev}` with exit code 1 (a stale `save` carries the current `rev`); a success exits 0. Nothing is printed but that one line, never a traceback, and a bad request line or argument is the same kind of error document. Failures are mapped as `POST /api/loop` and the reads map them: `ApiError` as it is, a `ValueError` of a write 400 in its own words, an `OSError` of a write 500 "could not make the change: ...", and either one of a read 500 "could not read the loops: ...".
- What the loop API does and does not guarantee: the spend limit is an admission limit, not a billing cap (a run can end above it by one turn, and with a limit set an unknown spend ends the run); after a restart a check may run twice; there is no exactly-once (a turn reconcile cannot place is never sent again unless the person chooses `redo`, and that can run it twice); and a real compaction of a resumed session was not provoked in tests.
- `POST /api/skills` changes one agent's skills exactly like `agentgraph skills`, with the JSON body `{"agent": A, "skill": S, "on": true|false}` (add or remove one) or `{"agent": A, "all": true}` (give it every skill), each with an optional `"expect"` (the agent's rev), and answers `{"agent", "skills": [...], "warnings": [...], "changed", "message", "rev"}`: `skills` is the agent's new list, `warnings` the task-profile mismatches a remove creates, `rev` the agent's rev after the change. It has every protection of the flow endpoint (loopback client, same-origin `Origin`, `application/json`, a body of at most 4096 bytes, exactly these keys, 405 with `Allow: POST` for any other method); a demo server refuses it with 403, an unknown agent or skill, a council seat or a name outside `agentgraph/names.py` is 400 with `{"error": "..."}`, and a stale `expect` is 409. The files it writes are parameters of the server (the real preset file, `SKILLS.md`, the agents and skills folders by default), so tests pass copies.

**Protections, for `POST /api/roster`, `/api/skillfile`, `/api/structure/team`, `/api/output`, `/api/setup` and `/api/spend`.** Each has every protection of `POST /api/structure/flow`:
- 403 for a client that is not loopback, for a `Host` other than `127.0.0.1:<port>` or `localhost:<port>`, for an `Origin` (when present) other than `http://` plus the request's own `Host`, and on a demo server;
- 415 for a body that is not `application/json`;
- 411 or 413 for a missing, empty or oversized body;
- 400 for a body that is not an object, has an unknown `op`, has missing or extra keys, or has a value of the wrong type (an empty `"expect"` included);
- 409 for a stale `"expect"`;
- 405 with `Allow: POST` for any other method.

No CORS headers are sent. A change the back end refuses is a 400 with `{"error": "<one sentence>"}`, never a traceback; a file system failure is a 500 with `{"error": ...}`. Every file and folder each endpoint reads or writes is a server parameter: `make_server(skills_config, skills_map, skills_backups, skills_profiles, skills_dir, skills_bundled, agents_dir, structure_dir, opencode_dir, ledger_root, commands_dir, sessions_db)`. The snapshot reads through the same parameters, so a server built on copies never reads the real files. `opencode_dir` is the folder whose `opencode.json` gives the root session's wear; the models catalogue is read only for the real folder. `ledger_root` is the folder that holds the ledger, `memory-<preset>` or `memory`. `commands_dir` is where path commands are written and rewritten; `sessions_db` is OpenCode's database, read for path costs. Each defaults to the real one.

- `POST /api/roster` (body at most 131072 bytes):
  - `{"op": "add", "name", ...}` adds an agent as one OpenCode agent file, `NAME.md` in the agents folder (default `~/.config/opencode/agents/`), and adds the agent's file in the structure folder. Both are one step: if either fails, neither is left.
    - A name alone is enough. `{"op": "add", "name"}` and `{"op": "add", "name", "template": "blank"}` write a blank agent: the description "An agent you added. Open it to say what it does." (`agentfiles.BLANK_DESCRIPTION`); an empty instruction, for the user to write with `op: "text"` (OpenCode 2.3.2 lists such a file as a subagent with an empty system prompt); the model of the presets' default verifier (with its variant), the one `template: "verifier"` uses, and with no default verifier in the presets `model` must be given ("model is required"); and the writer permissions (`read_only: true` gives the read-only set). It never lets the agent start agents.
    - `"template": "verifier"` takes the model, variant and permissions of the default `verifier` (the active preset's entry, else the first preset that has one). Its instruction is "You are given a goal and the work done for it. Check the work against the goal and report pass or fail, with the evidence for each."
    - `"copy_of": A` copies the model, variant, description, instruction, skills and permissions of a preset agent or of an added agent. The copy's permissions are never wider: there is no wildcard allow, and it may never start agents.
    - Optional `"model"` (`provider/model`, or `provider/model#variant`), `"variant"`, `"description"`, `"prompt"`, `"skills": [...]` and `"read_only": true|false` replace what the blank agent, the template or the copy brings. Each may be `null` (keep the default). `template` and `copy_of` also work with a name alone.
    - The answer is `{"agent", "kind": "added", "action": "added", "changed", "copy_of", "template", "model", "skills", "permission": "read-only"|"writer", "structure_file", "warnings", "message"}`.
  - `{"op": "read", "name"}` answers `{"agent", "action": "read", "name", "description", "model", "text", "skills", "permissions", "rev"}` for an added agent. `text` is its instruction. `permissions` is what the agent may do: `{"default": "deny", "rules": [{"action", "resource", "effect"}, ...], "skills": [...], "start_agents": "deny", "read_only": true|false}`. `rules` are the tool rules in file order, where the last matching rule wins, without the skill rules. See "What an added agent may do" below.
  - `{"op": "text", "name", "text"}` (optional `"expect"`) replaces an added agent's instruction. The answer has the new `text`, `changed` and `record`. Restoring that record brings back the exact earlier file.
  - `{"op": "rename", "name", "to"}` (these keys, all strings, and an optional `"expect"`) runs `agentgraph agent rename NAME TO`: one step with one undo record over the added agent's file (`agents/NAME.md` becomes `agents/TO.md`, same bytes, live at once), its file in the structure folder (moved within its group folder), every flow through it (its path; a generated instruction follows the new name, one the user wrote is kept), every group it leads or is in (lead and members; a generated instruction follows) and the command file of each of those paths whose command is on (rewritten; one changed by hand refuses the rename). Flows and groups keep their ids (file names). It answers `{"agent": TO, "action": "renamed", "renamed": {"from", "to", "flows", "teams", "memberships", "files", "commands"}, "record", "file", "message"}`. Restoring that record gives the old name back, byte for byte; it is refused, with nothing changed, when anything the rename wrote was changed since or the old name now belongs to another agent. The server rewrites path commands in `make_server(commands_dir=...)`.
  - `{"op": "remove", "name"}` (optional `"expect"`) removes an agent as one undoable step, together with every flow whose path includes it, the group it leads and its places in other groups: an added agent's file is deleted; a custom preset agent's entries and structure file go; a built-in agent is turned off. The answer is `{"agent", "kind", "action", "changed", "record", "removed": {"flows", "teams", "memberships", "files"}, "warnings", "message"}`. The answers to `text` and `rename` carry the agent's new `rev`. `record` is an opaque string. Removal is refused while a flow through the agent is a command; turn the command off first.
  - `{"op": "restore", "record"}` puts back everything that a removal, a rename or an instruction change took, byte for byte. It is refused, with nothing changed, when any of it was changed or taken again since.
  - `message` says when a change applies, in the same field the skills response uses: for an added agent's file, "OpenCode watches its agents folder; the change applies at once, without a restart, from the next session or turn."; for a preset change, "OpenCode reads this file when it starts; the change applies after a restart."
  - Refused with 400:
    - the orchestrator, `council` or a council seat, and the plugin's agents (for rename);
    - a name reserved for the user or OpenCode;
    - a name already in use, by a preset agent or by any file `NAME.md` or `NAME.md.*` (such as `NAME.md.disabled`) in the agents folder;
    - a bad name (a lowercase letter, then up to 63 lowercase letters, digits, `_` or `-`);
    - for rename, a missing agent, or a new name that fails any of a new agent's name checks (format, built-in or alias, seat prefix, reserved key, host agent, display name, a preset entry, an existing agent file such as `TO.md.disabled`, OpenCode's own config) or that a flow, group or structure file already uses;
    - an unknown template, both `copy_of` and `template`, or a skill that is not installed;
    - a hand-written agent file (for text, rename, remove and copy), with the reason;
    - an added file changed by hand in a way the writer cannot keep exactly. Text, skills, rename and remove all refuse it, with the same reason: "NAME.md was changed by hand in a way this toolkit cannot keep exactly; it is read-only here. ..." A file that still has the marker remains the toolkit's after a hand edit the writer can keep, such as an added `temperature: 0.2` line; each of these operations works on it and keeps every line the toolkit did not write, byte for byte. The writer cannot keep a file whose own lines are repeated or no longer in its shape, or whose `permissions` block goes on in lines it does not write;
    - an agent file changed while the edit was being made;
    - a forged or foreign record.
- `POST /api/skillfile` (body at most 2416384 bytes):
  - `{"op": "read", "id"}` answers `{"id", "description", "text", "editable", "why"?, "rev"}`. `text` is the body without the frontmatter.
  - `{"op": "write", "id", "text", "description"?, "expect"?}` replaces the body (and the description). The answer is `{"id", "description", "text", "changed", "record"?, "message"?, "rev"}`; restoring `record` brings back the exact earlier text. The write is refused if the file changed underneath.
  - `{"op": "create", "id", "description", "text", "assign"?}` creates the skill. With `assign`, it also gives the skill to that agent, as `POST /api/skills` would (for an added agent, in its file), in the same step; if the agent cannot have it, the skill is taken away again. The answer is `{"id", "description", "text", "changed", "assigned"?, "warnings"?, "message"?}`.
  - `{"op": "delete", "id", "expect"?}` deletes the skill's folder and answers with `record`. It is refused while an agent still has the skill, naming those agents: a preset entry lists the skill by name, an added agent's file allows it, or a hand-written (or hand-changed) agent file names it in its frontmatter. Agents that hold it only through `*` are named in a warning.
  - `{"op": "restore", "record"}` puts back a deleted skill, or the text before an edit, exactly.
  - Read-only skills can be read but not written or deleted; the refusal says why. A skill is read-only when it lies outside the skills folder, is a symbolic link, is bundled with a plugin, or is `shopify-ruby`.
- `POST /api/structure/team` (body at most 32768 bytes):
  - `{"op": "add", "lead", "members": [...], "text"?}` creates the group. When the text is absent or blank, it is made from the lead and members.
  - `{"op": "members", "id", "members": [...]}`, `text` and `remove` each take an optional `"expect"` (the group's rev). `members` replaces the members.
  - `{"op": "text", "id", "text"}` replaces the instruction. A blank text makes it again.
  - `{"op": "remove", "id"}` removes the group.
  - The answer is `{"team"?, "teams": [...], "notes"?}`, like `team ... --json`; `team.rev` is the group's new rev. A change is refused, with nothing written, when the group's file changed while it was being made.
- Every other path takes only `GET` and `HEAD` (405 otherwise). A request whose `Host` header is not `127.0.0.1:<port>` or `localhost:<port>` is refused with 403, so another site cannot reach it through DNS rebinding (no path sends CORS headers).

### What an added agent may do

Every file written for an added agent starts with `* * deny` and ends with `subagent * deny`, and the last matching rule wins. The action `*` appears only in that first deny, so no rule allows every action. The tools an agent may use are allowed per action, with `resource: "*"` (any file, any command), as in the table. A copy is never wider than its source: its rules come from the source's permission, the source's wildcard allow is dropped, and `task` becomes `subagent` deny. `{"op": "read"}` returns the same facts as `permissions`, so the view can show them.

| Agent | read, grep, glob, list | shell | edit (every file-editing tool) | skills | start other agents | `read_only` |
|---|---|---|---|---|---|---|
| blank (a name alone, or `template: blank`) | allow `*` | allow `*` | allow `*` | the ones given | deny | false |
| blank with `read_only: true` | allow `*` | deny | deny | the ones given | deny | true |
| `template: verifier` | allow `*` | as the default verifier's `bash`; today allow `*` | as its `edit`; today deny | none | deny | true |
| `copy_of: A` | allow `*` | as A's `bash`, with A's patterns in order (none: allow `*`) | as A's `edit`/`write`/`apply_patch`/... (none: allow `*`) | A's | deny | from A |

`read_only` says only that editing is denied. An agent allowed `shell` can still change files through commands. Today's verifier is one such agent: it inherits `bash` allow from the preset.

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
//   is { op: "add", path: [names], text?, name?, command? }, { op: "text", id, text }, { op: "name", id, name },
//   { op: "command", id, on }, { op: "prompt", id, goal } (resolves { text }) or { op: "remove", id }, each but
//   add with an optional expect (the flow's rev), as for POST /api/structure/flow (section 3). Resolves with
//   { flow?, flows, notes?, applies?, live? } (the next snapshot shows the change)
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
//   changes nothing and asks nothing (deleting an agent is options.actions.roster's, below). Without actions.flow it does
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
//   Resolves with { agent, skills, warnings, changed, message, rev } or rejects with an Error. With it, the
//   details of an agent whose skills_editable is true get a remove control on every skill row, a closed
//   fold "Add skills" of the installed skills it lacks (description as tooltip), each with an add control,
//   and "Give it every skill"; an agent with `*` keeps `all · N` and every row of its "Installed skills"
//   fold gets remove (which leaves the list of all the others). One change per agent at a time: it shows
//   as pending on its row and the controls wait. On success the list updates and one line says it
//   applies after OpenCode restarts, plus one line per warning; on failure the row reverts and the
//   message shows as a line. Folds keep their open state and the panel its scroll across refreshes. An
//   agent that is not editable (a council seat, a hub outside the roster) shows skills_reason as one
//   line. Without skills, every agent's skills are read-only and no reason is shown. A skill change's line
//   says where it went ("Changed in every preset that lists A and in SKILLS.md.", or "in A's agent file."
//   for an added agent) followed by the back end's message; a removed row stays in place, struck and inert,
//   for 1 s or until the pointer leaves the list; undo puts the skill back at its old place in the list;
//   an error line wraps and is scrolled into view. The back end appends a skill added back, so the place is the
//   view's: kept with the positions (same storage, key POSITIONS_KEY + ":skill-order"), it holds after a reload
//   and after later changes to that agent's skills (a skill added after it goes last, where the back end puts
//   it; "every skill" hands the order back to the back end). Another browser, or the Desktop panel's own
//   storage, shows the file's order: for every view to agree the skills endpoint would need a place on an add.
// options.actions.roster: optional (request) => Promise. The requests of POST /api/roster (section 3):
//   { op: "add", name, template?: "blank" | "verifier", copy_of?, model?, variant?, description?, prompt?,
//   skills?, read_only? } (a name alone adds a blank agent with the back end's defaults: description "An agent
//   you added. Open it to say what it does.", an empty instruction, the default verifier's model and the writer
//   permissions; any field given replaces its default), { op: "read", name }, { op: "text", name, text },
//   { op: "remove", name } (resolves with { record, message }), { op: "rename", name, to } (resolves with
//   { agent, renamed, record, message, rev }) and { op: "restore", record }; text, rename and remove take an
//   optional expect (the agent's rev). Shown only when the snapshot's roster.editable is true (else the hub's
//   details give roster.why). With it: a "+" glyph on the canvas (aria-label "Add an agent", key N)
//   opens a menu: Blank agent (the view sends template "blank" and no model, so the back end's default, the
//   default verifier's model, applies as on the command line), Verifier, or a copy of any agent. The new circle
//   follows the pointer and is dropped by a click or a release (Escape cancels); it settles at the
//   nearest free spot beside what it would cover, clear of every circle and name; adding it moves no other
//   agent (it keeps its place and takes none on the ring). From the keyboard it is placed at once at a free spot
//   with room round it for Alt+arrows.
//   On the drop the view asks { op: "add" } under the first free name (verifier-2, impl-claude-2,
//   agent-1), keeps the dropped place, opens the details with the name selected (Alt+arrows move it,
//   Enter renames it) and shows the reply's message as the success line (the view never says when it
//   applies by itself). Rename (any added agent, on flows and in groups too) is one request,
//   { op: "rename", name: OLD, to: NEW }; its place in every layout moves to the new name and the details
//   say "Renamed OLD to NEW.". A refusal changes nothing in the view (name, place, lines, saved places,
//   undo) and is the agent's error line, announced. Undo sends { op: "restore", record } with the reply's
//   record and moves the place back; a refused undo changes nothing and says what and why (see Undo below).
//   Renaming an agent just added is part of adding it (undo removes it under the new name). An agent with
//   added: true draws no line at all (not even the hub's spoke) until it is on a flow or a group, has a
//   busy session or a task-graph step. Delete rule: Delete removes, in this order, the focused flow,
//   group line or agent, else the picked flow, else the picked group line, else the lone armed agent,
//   else the open flow's details, else the open agent's details; nothing while a path of two or more
//   steps is pending or focus is in a text field. Backspace removes the newest pending step, else a flow
//   or a group line, never an agent. The hub, the council and its seats are never removed (removable
//   false shows the reason). Removing an agent says the back end's message; Ctrl/Cmd+Z sends
//   { op: "restore", record } and brings back its flows and its place. Focus stays in the view after any
//   delete, so Ctrl/Cmd+Z works straight after a mouse delete. While a new circle's add is out it stays drawn
//   whatever the user opens; a refusal is always shown (the new agent's details with Try again), and a success
//   opens the new agent only if the user has opened nothing else since the drop.
//   An added agent's details have its instruction (the system prompt in its file) in a monospace box under the
//   name: read with { op: "read", name } when the details open (text, rev), saved with
//   { op: "text", name, text, expect } by the rules of a path's instruction (after a pause, on leaving the box,
//   Ctrl/Cmd+Enter, on page hide; one undo step per editing session, undo sends { op: "text" } with the earlier
//   text). A blank agent's is empty, with the placeholder "Say what this agent does.". Read-only (no box to
//   type in) when roster.editable is false; a preset agent has none.
// options.actions.skillfile: optional (request) => Promise. { op: "read", id } → { text, description, rev, ... },
//   { op: "write", id, text } → { record, rev, ... }, { op: "create", id, description, text, assign? },
//   { op: "delete", id } → { record } and { op: "restore", record }; write and delete take an optional expect
//   (the skill's rev). Full shapes: POST /api/skillfile (section 3). A skill's name in an agent's
//   details opens its text in an editor (Save, Delete, undo for each); a skill with editable false shows
//   its text read-only with why. "New skill" makes one and gives it to the agent in one request. Text typed in
//   a skill and not saved outlives the details (Escape, a click away): opened again the skill shows it as
//   Changed, and its Save expects the rev it was typed against.
// options.actions.output: optional (request) => Promise. The requests of POST /api/output (section 3):
//   { op: "read", task, node, artifact } → { text, rev, ... }, { op: "write", task, node, artifact, text, expect }
//   → { output, rev, stale, record, message } and { op: "restore", record } → { output, rev, text?, message, warnings? }.
//   With it, an agent's details list, under the task shown ("Outputs in TASK"), that agent's steps that have
//   outputs: each step by name (its id and agent), each output's name, size and revision. An output with
//   editable false says why and offers nothing to open; an agent with none has no list; nothing is added to the
//   graph area. Opening an output reads its text into a monospace box that scrolls inside the sheet (one open at
//   a time). Save (or Ctrl/Cmd+Enter) is the only way it is saved, by the rules of a path's instruction:
//   { op: "write", ..., expect } with the rev of the read; refused as stale (409 or stale: true) the typing stays,
//   the box takes the latest as its saved text and nothing is sent until the user saves again (expecting the
//   latest); no answer within 15 s is given up and a late answer is dropped. After a save the sheet says the
//   revision saved and lists by name the steps that read the older revision and now need a rerun (the view never
//   starts one). Ctrl/Cmd+Z sends { op: "restore", record }: one step per save; refused, it is dropped with
//   "Could not undo the edit of the step's output: WHY". Text typed and not saved outlives the details.
//   Every text from the back end (the output, why, step names, messages) is shown as text.
// options.actions.setup and options.actions.spend (n10: the spend limit, the active preset and the saved setups; both
//   optional, none in the demo; the web page and the Desktop panel pass them). Everything is in the run details, which the
//   top total opens; nothing is added to the graph area but the limit beside the total.
//   spend: (request) => Promise. The requests of POST /api/spend (section 3): { op: "limit", limit, warn? } and
//   { op: "off" } → { limit, warn, previous, changed, message }. The top total reads "$7.65 / $50" with a thin line
//   under it filling toward the limit (data-limit on the button: none, under, warn, at, over); from `spend.warn` the
//   line and the limit use the view's attention colour (no new colour, never green), at the limit it says "at limit"
//   and over it "over". The details have Spend limit (the limit line, rate unless null, chat against workers, a small
//   field to set, change or remove it, each with one Undo: the answer's `previous`). A bad amount asks nothing.
//   setup: (request) => Promise. The requests of POST /api/setup: { op: "preset-switch", name }, { op: "preset-restore",
//   record }, { op: "save", name, replace?, expect? }, { op: "restore", name, expect? }, { op: "remove", name,
//   expect? } and { op: "undo", record }. The details have Preset (the active name, the presets with the active one
//   marked; choosing another asks once, writes preset-switch, says it applies at the next start of OpenCode and offers
//   Undo = preset-restore; with `setup.override` set it says what overrides the preset and offers no switch) and Saved
//   setups (name, date saved and preset of each; a damaged one is marked and has no Restore; "Save current as…" with a
//   name of the flow-id rule, asking once before it replaces a name in use; Restore asks once and says what it
//   replaces; Remove asks once; each change offers one Undo = undo with its record). Every write names the setup's
//   `rev` as `expect`; a refusal shows the back end's one line (a 409 the plain stale sentence) and changes nothing.
//   All of it is look-only in 3D (the usual sentence, nothing asked of the host). Names, dates and messages are text.
// options.actions.chat / send / stop (the chat in an agent's details, loop n5). None of the three changes the
//   setup, so all work in 3D too (everything that does change it stays look-only there).
//   chat: optional (sessionID, { limit }) => Promise of the feed of GET /api/chat (section 3: { session, agent, busy,
//   items[{ kind, time, text | name + status, cut, pending }], truncated, more, warning }). Without it the view
//   fetches `/api/chat?session=ID&limit=N` itself (options.chatUrl names another path; options.chatDemo adds
//   `&demo=1`; the web page sets it with ?demo). Only the agent whose details are open is read: every 1.5 s while
//   its session is busy, once when it is not (and once more after it stops being busy), never while
//   document.hidden or with the details closed, one request at a time. A failed read shows one quiet line and tries
//   again on the next tick. The session shown is the newest of agents[].sessions (the orchestrator: the root
//   session, snapshot root.id), with a small way to step to the others; with none, one quiet line and nothing else.
//   The session id is sent exactly as the snapshot gives it.
//   send: optional (sessionID, text, { steer }) => Promise of { ok: true } or { ok: false, error }. With it, a text
//   box shows in the chat when the session is busy (sent with steer: true) or when the agent is the orchestrator
//   and its session is idle (steer: false, a plain prompt). Enter sends, Shift+Enter is a newline, empty is
//   refused, at most 4,000 characters (over it is refused and said), no second send while one is out, the box
//   clears only on ok and an error shows as one line and keeps the text (a typed text is kept per session). No
//   answer within 15 s: "Not confirmed as sent..." and the text stays.
//   stop: optional (sessionID) => Promise of { ok: true } or { ok: false, error }. With it, Stop shows while the
//   session is busy; it asks once ("Stop this agent?", Stop / Keep going) before it calls, then says the result.
//   Without send and stop (the web page: it has neither) the chat is a read-only transcript: no box, no Stop.
// options.actions.side (the side chat's moon and panel, loop n8). Optional: (text) => Promise of { ok: true,
//   answer } or { ok: false, error }. It asks the side chat one question (Desktop runs `agentgraph sidechat ask
//   --main <root session> --text-file - --json`, the question on stdin only, 150 s at most) and changes nothing in
//   the setup, so it works in 3D too. The moon: when snapshot.sidechat.installed is true (section 3) one small moon
//   named "side chat" sits beside the orchestrator; when it is not, nothing is drawn or said. It is not an agent: no
//   lines, never a path's end, not in the roster, group or skill editors, never counted in "N working" or the
//   "! N" badge. It goes round the orchestrator (the moons' lap) only while sidechat.busy and not with reduced
//   motion, and stands still otherwise; in 3D it goes round on a tilted plane of its own. Choosing it as an agent is
//   chosen (Enter or Space on it, a right-click, a long press) opens its panel: one quiet line (it reads the main
//   chat and the task graph, answers only you, changes nothing), its model and spend when known, and its chat: the
//   transcript of sidechat.session, read through actions.chat (or /api/chat on the web page), with no Stop, no
//   steer and no session stepper; with no session yet, one quiet line. The ask box shows only when the host gives
//   `side`: Enter sends, Shift+Enter is a newline, empty is refused, at most 4,000 characters (over it is said, not
//   sent), one question at a time and none while sidechat.busy. The question shows at once as pending ("waiting")
//   with the chat's caret moving only while it waits (up to about 2 minutes; the panel stays usable). The box clears
//   only on ok; the answer shows from the reply and then from the next read of the session. An error is one line
//   and keeps the text. The web page gives no `side`: it shows the transcript only. Questions and answers reach the
//   page as text only: no markup, link or image is made from them.
//   The chat, oldest first: `reasoning` quieter than `text` (italic, muted, a rule at the left), `tool` as its name
//   and state only (never its input or output), `user` marked (and "waiting" while `pending`), `note` as the one
//   word "note", a `cut` item and a `truncated` or `more` feed saying so quietly, following the newest line unless
//   the user has scrolled up, a caret that blinks only while the session is busy and not with reduced motion. Every
//   word reaches the page as text: no markup, link, image or markdown is ever made from it. agents[].activity (the
//   tool it runs, "thinking", "replying") shows as one line under the agent's name in the details, nowhere else.
// options.actions.team: optional (request) => Promise. { op: "add", lead, members, text? } → { team },
//   { op: "members", id, members }, { op: "text", id, text } and { op: "remove", id }, each but add with an
//   optional expect (the group's rev). structure.teams is [{ id, lead, members, text, rev }] (the user's word is group). Members sit evenly on one circle round
//   their lead, move with it, and have one broad soft line each to the lead (no hub spoke, not dotted,
//   no text). Delete on a picked group line takes that member out; a new group opens with its
//   instruction focused and selected. Flow and group instructions save after a 700 ms pause in typing
//   and on pagehide; the state reads "Saving" or "Saved", never asks the user to save.
// A path's details (contract n96; the user's word for a flow is path). Nothing of this is drawn in the graph.
//   Name: a field saved with Enter or on leaving it as { op: "name", id, name } through actions.flow (empty
//   clears it); Escape puts the saved name back; a refusal shows as the field's error line and in the live
//   region with the typed name kept; Ctrl/Cmd+Z puts the old name back. Read-only hosts show a name, no field.
//   Run: a goal box (required, at most 4,000 characters counted as characters; a counter "N of 4,000" from
//   3,600, "Too long: …" past the limit). Enter is a new line; Ctrl/Cmd+Enter or the button runs. With
//   options.actions.run the button is Run and sends { flow: id, goal }; while it is out the button is busy
//   ("Sending…", disabled) and nothing fires twice. Only a reply with sent === true reads as sent: its message,
//   else "Sent to the orchestrator (session S).", and the goal is cleared. A rejection (Desktop, the only host
//   with run, resolves { sent: true, session } and rejects on any failure) is the reason in plain words, else
//   "Not sent"; { sent: false, message } is its message; any other reply reads "Not confirmed as sent. Look in
//   the orchestrator's chat before you send it again."; in each of these the goal stays and the line is an
//   announced error. Without it the button is "Copy run message":
//   it asks actions.flow { op: "prompt", id, goal }, puts reply.text on the clipboard and says so; when the
//   clipboard is refused or missing it shows the text read-only, focused and selected. A goal stays (closing
//   and reopening the details, refreshes) until it is sent. Sending is not a change to the structure and has
//   no undo. No Run without actions.run or actions.flow.
//   Cost (when the snapshot carries structure.flows[].stats): runs, passed, rounds (avg · most), cost
//   (total · avg), last run (stats.last, Unix seconds: the newest run's updated time, read as "just now" under
//   90 s, then "Nm ago", "Nh ago", "Nd ago"); stats null reads "Not run yet"; a null field reads "—", never 0.
//   No chart.
//   Command: a switch "Use as a command" (role switch). Without a name it is aria-disabled and says "Give
//   this path a name to use it as a command." On sends { op: "command", id, on }; on, it shows /NAME (from
//   structure.flows[].command) and when it applies, as the back end reports it: the command reply's live
//   (boolean) and applies (a sentence), else the snapshot's structure.commands_live and
//   structure.commands_applies, in plain words: live true reads "Live now."; live false (or a sentence that
//   speaks of a reload or restart) reads, after turning it on, "Not in OpenCode yet: /NAME works once
//   OpenCode reloads (type /reload) or restarts." ("(type /reload)" only when the sentence names /reload),
//   at load "A new command works once OpenCode reloads … or restarts.", and after turning it off
//   "/NAME stays in OpenCode until OpenCode reloads … or restarts."; each is announced with the change. A
//   sentence that names neither is shown as sent. A refusal is the switch's error line and is announced.
//   Ctrl/Cmd+Z turns it back.
//   Undo of a removed path is one request, { op: "add", path, text, name?, command?: true } (name when it had
//   one, command when it was a command), so one undo cannot stop half way; refused, nothing comes back and the
//   view says what could not be undone and why (see Undo below). Later steps that named the removed path (path
//   steps only, never a group's with the same id) follow it to its new id. Remove and the command switch are not
//   asked for again while their request is out (a double click, a held Enter or Delete asks once).
//   Keys: G on a focused path, or on the canvas with a path picked, opens its details at the goal box (the
//   path's aria-keyshortcuts end with G when it can run).
//   Fit (the button or F) never takes names under 11 px either; past that the graph pans.
// The figures row (cost, "! N", "N working", tokens, the context bar and number, the summary dots) keeps one
//   row in the pane the details leave, never overlapping or clipped. What does not fit is dropped, least important
//   first: the dots, the tokens, the context bar, the working count; the cost and "! N" always stay. It is measured
//   (the pane, not the whole view), on every snapshot and every resize, on top of two fixed steps for a narrow view
//   (330 px: dots and tokens; 300 px: the bar). With the compact sheet open the row stays, in its own strip above
//   the canvas.
// Refit: opening or closing the details refits the graph into the space left (every circle and name clear of the
//   sheet, the figures and the controls; names at 11 px or more, past that it pans), unless the user has panned or
//   zoomed since the last fit: then their view stays (in compact, as it was on screen; see Narrow canvas). A change of the pane's own size or of arrangement refits.
//   A change of layout (a loop drawn or removed, an agent added or removed, a keyboard nudge) never discards the
//   user's pan or zoom; an automatic view stays put too when nothing but a loop's agents or a new agent moved (it
//   pans only to bring them into view), else it is fitted again. A wide canvas the details leave taller than wide
//   gets a taller oval, and a compact canvas is laid out for what its bottom sheet leaves, open or closed (opening
//   or closing it never moves an agent), so everything fits above the sheet at 11 px or more where it can; where it
//   cannot, the opened agent comes into view with the agents its paths join it to (a loop entered from A, as in
//   A, B, C, B, forms near A so they fit in one view).
//   The compact sheet has a grab handle on its top edge (a focusable separator named "Details height"): dragging
//   it, or its Arrow, Page, Home and End keys, resizes the sheet from 120 px up to leaving the figures row and
//   100 px of graph, and does nothing else (no pan, pick, move or close). The height is kept as a share of the
//   pane for the page and, where localStorage works, under `<positionsKey>.sheet`; it is clamped to the pane
//   whenever the pane changes. The graph refits above the sheet as when the details open (the user's own pan and
//   zoom stay). The wide side panel is full height and keeps its fixed width.
//   The root paints its own warm canvas (`--ag-canvas-bg`, light or dark by the host's or system scheme), never
//   a host's background token, so a host's panel shows what the plain page shows.
// Zoom floor: wheel, Ctrl+wheel, pinch, the - key and Fit all stop where names are 11 px on screen (12 px names
//   at scale 11/12). The background pans, like an agent drags, only once the pointer has moved more than 5 px.
//   A plain wheel zooms about the pointer at every width, the compact arrangement included (one gesture, one
//   meaning); a drag on empty canvas pans whenever anything lies outside the canvas, at any zoom.
// Narrow canvas (n114): the fit and the pan limits count loop rings as well as agents (no ring is left cut by the
//   edge when a pan-free placement exists; an automatic view whose picture a new loop no longer lets fit is fitted
//   again when it fits at 11 px). With the trial's loop sets and the details open at 620 px every circle,
//   name and ring is in the canvas; at 420 px what is outside comes into view with one drag. Every ring keeps at
//   least 8 drawing px clear of every circle and name not on it, the orchestrator's name included (the aim is 12);
//   where the canvas has no room for that the ring keeps clear and lies outside, a pan away. Two loops through one
//   agent leave it side by side, nearly touching at its centre, so a click a few px off one picks that one.
//   After the user zoomed or panned in compact, opening the sheet keeps the drawing where it is on screen (the
//   canvas moves down under the figures; the opened agent alone is panned into view, as far as it takes), closing
//   it puts the view back where it was (unless the user panned or zoomed meanwhile), and a loop drawn or an agent
//   placed or added from the + does not pull the view to the picture's new edges. An agent the user placed is left
//   out of where loops are fitted, so dropping one moves no loop. Text in the details is 11 px or more.
// A refused path keeps the reason as the tooltip of its first agent and of the newest agent (which shakes and
//   shows it in its details).
// A blank agent is asked for as { op: "add", name, template: "blank" } with no model, so the back end's default
//   (the default verifier's model) applies, as with `agentgraph agent add NAME`. Any refused add removes the new
//   circle at once; the details keep the reason (role alert, announced) with Try again (puts the circle back where it
//   was and asks again) and Cancel. Back-end reasons in developer words are said plainly (an agent with no file in the
//   structure folder, a missing description or model, a taken name); anything else is shown as sent.
// Delete on an agent that stays (the orchestrator, the council, a seat, one the roster keeps) shakes it, sets the
//   reason as its tooltip and announces it, with the details open or closed, and shows it for 6 s in the status
//   line at the top left (11 px, never in the graph; aria-hidden, the live region says it); nothing is asked of
//   the host.
// After a skill save, create or delete, focus stays on the control used (Save, or the text box after Ctrl/Cmd+Enter),
//   goes to the new skill's row, or to the agent's skill list after a delete; never to the close button.
// Undo of a skill change puts the skill back in its place in the list and keeps that order across reloads (same
//   storage as the positions, key "<positions key>:skill-order"), although the back end appends it to the file.
// Loop rings keep clear of every agent not on them: agents that can move are pushed off a ring's line (32 units
//   beyond their reach, room for the name), a later ring whose agents all move is moved off an earlier ring and off
//   the hub or an agent that stays put. A click off an agent picks the line nearest the pointer by distance to the
//   stroke (unchanged); a click on an agent (circle, name or hover halo) is the agent's.
// Narrow panes: when every agent cannot fit at the 11 px floor (18 agents beside the 340 px sheet at a 760 px pane,
//   or above the sheet at 520 px), the graph stays at 11 px and pans: the opened agent is wholly visible and panning
//   reaches every agent.
// No write from a stale view (agentgraph contract n8 r4): when snapshot items (structure.flows, structure.teams,
//   agents, skills) and skillfile { op: "read" } replies carry `rev`, every write that names one of them sends
//   `expect` with the rev the view shows: flow text, name, command, remove and prompt; run (the flow's rev); team
//   members, text and remove; roster rename and remove; skills; skillfile write and delete. Adds, reads and restores
//   (undo of a rename, delete or skill delete) send none. An instruction box or path name sends the rev it was drawn
//   with, so a change drawn while it is being typed is not overwritten. A write's reply rev (reply.flow.rev,
//   reply.team.rev, reply.rev of skills, roster text and rename (under the new name) and skillfile) is the one
//   shown until a snapshot fetched after it is drawn, and writes to one thing go one after another, so saves in a
//   row each expect what the last one wrote. The view's own accepted writes are remembered as moves from the rev
//   they expected to the rev they were answered with, and every expect is carried along them: a field drawn (or a
//   request queued, or an undo step made) at rev A expects B once this view's own write moved A to B, also when
//   an older snapshot asked for before that write is drawn after it; a change made anywhere else breaks the chain
//   and is refused. A group's members after a members change are the answer's (reply.team.members) until a
//   snapshot fetched after it is drawn, so a second change before then builds on them. A refusal with
//   status 409 (or error.stale, or a message saying it "changed since you opened it") refreshes the view, keeps
//   what was typed (instruction, path name, new agent name, skill text, run goal) and says "This was changed
//   somewhere else since you opened it. The view now shows the latest; try again." (announced); trying again
//   expects the latest rev. An instruction box (a path's, a group's, an added agent's) refused as stale keeps the
//   typing, drops the save queued behind it, takes the latest text as its saved text and stops saving by itself
//   (pause, page hide) until the user saves it by leaving the box or Ctrl/Cmd+Enter; that save expects the latest
//   and its undo goes back to the latest. A snapshot without rev sends no expect. index.html passes the response
//   status and rev on the thrown error.
// Undo (Ctrl/Cmd+Z): each step's request expects the rev its own write produced (carried along the view's later
//   writes), so it never lands on a change made elsewhere since or on another thing that took the same id; adds
//   whose reply has no rev (roster add, skillfile create) fall back to the rev shown. Refused (409, or a refusal
//   with a reason), the step is dropped and the view says "Could not undo WHAT: WHY" (WHAT: the path's
//   instruction, name or command, adding or removing the path, the skill change, deleting, adding or renaming the
//   agent, the agent's instruction, the change to the skill, making the skill, making or removing the group, the
//   group's members or instruction; WHY: the back end's reason, or "it was changed somewhere else since. The view
//   now shows the latest."), so older steps stay reachable. With no answer within 15 s, a failed connection (a
//   TypeError from fetch, or error.transport) or a change to the same thing still out, the step stays: "Not
//   undone: … Ctrl+Z tries again.".
// reset() drops the history and every answer to a write sent before it (neither said nor remembered).
// A write, or the refresh after a stale refusal, that gets no answer within 15 s is given up: what was typed is
//   kept, later writes to that thing go ahead, a late answer is dropped (never drawn over newer state), and the
//   view says "agentgraph did not answer within 15 seconds, so the view stopped waiting. Try again." (after a
//   stale refusal whose refresh hangs: "…and the latest did not load within 15 seconds. Try again."). A snapshot
//   that does not come within 15 s leaves the drawing as it is. options.clock ({ setTimeout, clearTimeout })
//   replaces the timer, for tests.
// 3D view (orbit lane n105; the terminal's model in tui-plugin/src/scene.js): a button beside the controls (named
//   "3D view", aria-pressed) and the key D (wherever the view has the keys, not while typing) switch 2D and 3D; the
//   switch is one morph over 700 ms (eased t*t*(3-2t); at once with reduced motion), the terminal's morphCamera: the
//   camera stays at the side view (0.5 rad above the system's plane) while the rings rise out of the plane (each
//   ring's tilt times the morph's progress, figure3D(L, k)) and every point moves in a straight line on screen from
//   its 2D place to where that camera shows it (no cut, no fade), and back the same way. A press of D or the button
//   during the morph is never lost: it turns the move round from where it is; a click during it picks as in 3D. In 3D the orchestrator is the sun (at the
//   middle of the canvas the details leave, below the top band; the rings narrowed until everything drawn round
//   every agent, its seats or members, every name and count and the sun's glow, stays inside that canvas at every
//   turn and at every elevation, sampled every 7.5 degrees; fitted again when that canvas changes size in 3D, e.g. the
//   details opening, the user's zoom kept), each group its own ring tilted differently, its agents on it near their 2D places, council seats and a group's
//   members round their lead; nearer agents are larger and drawn over farther ones, whose colour is mixed toward the
//   muted one (the terminal's DEPTH_MIX .72 farthest to 0 nearest, continuous here; not while an agent works); names
//   stay upright and at 11 px or more. Every line, loop (a closed dotted ring), group line, badge and pick follows
//   the agents. Going back puts the 2D view back exactly (pan, zoom, every position). The choice is kept in the
//   page and across a reload (key "<positions key>:view"; no storage: the page only). The system turns by itself
//   once in 40 s, only in 3D, never with reduced motion, while the page is hidden or during a press. A drag (over
//   5 px, on empty space or starting on an agent) turns it: across turns, up and down tilt it between along its
//   plane (0) and straight down (PI/2, never past); arrow keys on the canvas turn it 15 degrees and tilt it 7.5
//   degrees a press; R or a double-click on
//   empty canvas resets the angle; Escape during a drag puts the angle back. Turning by hand pauses the self-turn
//   for 3 s. Wheel and pinch zoom; there is no pan in 3D. The flat view is where the work is done; 3D is for
//   looking: a click picks an agent or a line (a second agent is picked instead, never joined by a path; lines do
//   not bend round agents; the screen-reader line says what is picked and never offers drawing a path), a
//   right-click, i, or Enter on an agent opens the details to read, and Escape lets go.
//   While anything but the flat view shows (3D or either morph) no write is asked of the host, whatever starts it;
//   reads are. Refused in the one place every request goes through, with the status line "3D is for looking: press D
//   to change things in 2D.": drawing a path (Enter, l, a double-click's loop; a path or a new circle begun in 2D is
//   not finished), N and the +, Delete and Backspace, G, Ctrl/Cmd+Z (wherever the focus is; no undo step is used),
//   moving an agent (a drag on one turns the system; Alt/Shift+arrow; a drag moving one in 2D holds the switch),
//   typing in the details and pressing their controls, a save queued behind one out, the pause after typing, leaving
//   a box and the page hidden. The typing stays, marked not saved. At the switch to 3D what is typed is saved at
//   once, in 2D; a save already out finishes and its answer is shown. Back in 2D the usual rules go on (the typing
//   is saved, undo takes the steps made before). Whatever happened in 3D (the details opened or closed, Fit, a
//   resize, a snapshot), back in 2D an automatic view is the automatic view for the canvas it returns to, a view the
//   user placed is where they left it, and neither counts as a move of the user's. A step's output (Outputs in TASK)
//   opens to read. The sky (3D only; nothing of it is drawn in 2D) is the drawing's
//   first child (svg.ag-sky: aria-hidden, no pointer events, no focus, no text) and fades in and out with the morph:
//   a sparse field of specks from a fixed seed (the same sky every time; three sizes, at most .3 opaque; warm white
//   on dark, an ink star chart on light; the terminal's seed 0x5ca1ab1e and hash32) that shifts, wrapping round,
//   as the terminal's skyPlace: it is V = max(view width, 2 PI x a third of the near side's movement per radian)
//   wide and a full turn moves it exactly V; tilting lifts it by a third of the figure's movement; a warmer star (.4 opaque) for each finished run, i.e. run 1..stats.passed of each path (failed
//   runs add none), placed by the FNV-1a seed of "<path id>#<run number>"; at most 200: every path's first run, then every
//   path's second, and so on, in the snapshot's order; past 200 a new run adds no star and no streak. When a snapshot shows a run finished while 3D is
//   shown, one shooting star (0.9 s) crosses to where that run's star then appears; several are spaced 0.7 s apart
//   (at most 8 a snapshot, none for a path new in it); none on load, on entering 3D or for a failed run. The orchestrator glows as the sun, swelling while it works
//   (still when idle); every other agent is shaded as a sphere lit from it. Nothing else moves by itself but the moons (below). Reduced
//   motion: no streak (the star simply appears), no swell, no fade. A name over a speck keeps 4.5:1 or better even
//   without its halo. The sky draws no frame of its own: it moves only with the frames below. A frame is drawn only when the angle or the data changed. root carries
//   data-ag-view ("2d"/"3d"), data-ag-blend (0 to 1), data-ag-turn, data-ag-elevation (the 3D angle), data-ag-camera-elevation (the
//   camera's, through the morph; 3D only), data-ag-frames,
//   data-ag-moon (the moons' phase, radians) and data-ag-frame-ms (mean of the last frames) for hosts and tests. Where the web differs from the terminal (pixels,
//   not cells): the drag's rate per pixel; one screen scale for both axes, the rings fitted to the free canvas; the
//   field's density (a star per 9,000 px^2, a cell taken as 8 x 16 px) over a sky a quarter taller than the view
//   above and below instead of wrapped rows; the depth mix continuous rather than six steps; the streak a line on
//   the terminal's diagonal (one column, one row a step), not characters; the sun's glow and sphere shading.
// Moons (n119, n118 ruling 4; the same model as the terminal): an agent with agents[].parent (other than the hub) is a moon of its
//   parent (today the council's seats). In 3D the parent sits on its group's ring like any planet and each of its moons
//   goes round it on an orbit of its own (two path.ag-moon-ring in the rings' layer for each moon, data-moons = the
//   parent, data-moon = the moon, data-side near/far, data-radius = that orbit's radius). Moons of a parent are ordered
//   by name, i = 0 .. n-1: the first one's plane is the one ring's (tilted 1.1 rad up from the system's plane about the
//   line across the parent's way round the sun, so it lies unlike the group's ring and is narrow toward and away from
//   the sun), moon i's plane that one turned about the vertical axis through the parent by 360 i / n degrees (so no two
//   moons share a plane); its radius r (1 + .14 i), r the parent's 3D radius + the largest moon's 3D radius + 8, so seen
//   face on the moon's disc clears the parent's by 8 (data-rest is that radius); its place at time 0 360 i / n degrees
//   along its orbit, all going round the same way. A moon's disc in the steady 3D frame is .55 of its 2D radius (its
//   halo, ring, inner dot and marks with it; the size comes with the morph and ends exactly on each steady frame; 2D is
//   unchanged); its name keeps its size (never under 11 px) and sits close under the small disc; its pick target is never
//   under 12 px in radius. A canvas where the orbits at rest size would leave the figure under .97 of its size without
//   moons (MOON_ROOM) gets smaller orbits, but never under .86 of rest (MOON_FLOOR; the figure takes what is left):
//   at 1100 px and wider each orbit is at least .85 of its rest radius and the figure is within 3% of its size before
//   the small moons. They go round once in 24 s on the clock the system turns by, and stand still at their time-0
//   places whenever it does (reduced motion, a hidden page, a press). A moon's name is drawn again (use.ag-moon-name, no
//   pointer events, aria-hidden) above its parent's disc and its siblings' at every point of the lap, planets nearer the
//   viewer may still cover it; a moon's disc behind its parent is still drawn behind it. The fit counts each moon's
//   orbit as seen from each sampled angle. In 2D nothing changes and nothing new moves; the morph takes each moon in a
//   straight line from its 2D seat to its 3D place and back. A moon picks, opens its details, and keeps its spoke,
//   pulses, loops, badges and paths, as before.
// The sun (n119): the hub (hubName) is a ball lit from the upper left (g.ag-sun-ball: radial gradients #<uid>-ball-rest
//   and -fire, colours --ag-ball-light/--ag-ball-dark/--ag-flare-core), in 2D and 3D, still at rest. While the hub
//   works it flares: g.ag-flare, the stage's first child (under every line, ring, agent and name; aria-hidden, no
//   pointer events, no text; its transform is the hub's), holds a warm glow (three rings to 1.5 r, swelling) and
//   clamp(round(r / 4), 7, 12) flame spikes, each on its own time from 0.6 to 1.6 s seeded from "flare:<hub name>"
//   (FNV-1a and hash32, as the sky), at most 0.6 r beyond the rim. data-state "on" while it works, "dying" for 0.7 s
//   after it stops (fading over 0.6 s), "" at rest. Colours --ag-flare-spike/--ag-flare-warm/--ag-flare-core, warm
//   only (no green), a spike's base 2:1 or more against the page in light and dark. Picking, the fit, the camera and
//   the 8 px ring clearance (from the hub's circle) are unchanged. Reduced motion: short still spikes, no swell. In 3D
//   the flare's glow gives way to the sky's sun glow as the morph goes on.
// options.motion: "full" (default) or "none" for a still picture (tests, screenshots). The system's
//   reduced-motion setting is always honoured.
// options.autofocus: true to give the canvas keyboard focus once the graph is first drawn (N, F and the arrows
//   work without a click), unless something other than the page already has focus. index.html passes it; a host
//   panel (the desktop plugin) does not, so the view never takes focus there.
// options.scheme: "auto" (default; follows a data-color-scheme on an ancestor or the document element,
//   else the system setting, live), "light" or "dark".
// Returns { refresh(): Promise<void>, reset(): Promise<void>, destroy(): void }.
// reset() is for a host that switches to another session: it drops the cached document, the automatic
// layout, selection, open details and team choice (the positions the user moved are kept: they belong to
// the host), shows the loading state, fetches again and ignores any
// response that was requested before the reset.
window.AgentGraph.mount(root, options)
```

- What it draws: the hub in the centre and every other agent on one ring round it (a tall oval in a narrow container), each group side by side; council seats evenly on a circle round their lead, linked only to it. Ordinary handoffs are straight lines, and work that passes through the hub travels along its spoke. `graph.loops` are closed two-lane leaves between the two agents, and `graph.work_loops` are rings on the agent. The hub's inner arc is `graph.progress`. There is no header and no instructions: an agent's name, a review loop's round count, and on an agent with two or more sessions working now a count badge "×N" at its top right (the same number as "N active" in its details; none at 0 or 1; it used to be the work loop's round count, which is now in the loop's label and details only), and two icon buttons (fit, run details). A working agent's task is not drawn on the canvas: it is in the agent's accessible name and its details. The camera fits every agent, its name and its badge inside the canvas with a margin, in a wide and in a narrow (compact) container, with the details open or closed: full size when they fit, smaller just as far as needed when they do not, but never so small that an agent's name is under 11 px on screen; past that the graph keeps that size and pans, and the agent whose details are open is brought fully into view with its name. A wide graph's oval is shaped for the canvas the 340 px details panel leaves, so opening the panel never moves the agents. In a narrow container the details are a sheet of at most 40% of the height that scrolls inside itself. An agent the user moves stays inside the canvas. The top-right readout is a button that opens the run details: dollars, tokens, and the context of the root session. With `root.wear` the context is a bar 56 px wide and 4 px tall (the track is the distance to `compact_at`, or to `window` when it is `null`; the fill is `context`: the muted ink below 60%, the work colour from 60%, ink on a work-coloured track from 85%), the number beside it, then one dot per summary (at most five) and the count; its tooltip says it in words. Without `wear` it shows `ctx 124k` from `root.context`. A narrow panel drops the tokens first, then the bar, dots and count. The run details open with a "Session" block: a line chart of `history`, then plain lines for context and the next summary, summaries (and, when `level` is `worn`, a sentence that early work has been condensed), turns, and cost per turn, each left out when its value is null.
- It keeps everything inside `root`: no writes to `document.body`, no global listeners that outlive `destroy()`, no globals other than `window.AgentGraph`.
- All of its CSS is in `agentgraph/web/style.css`, and every selector is scoped under the class `agentgraph` (which `mount` puts on `root`), so it cannot restyle a host application. No external fonts, images or scripts.
- It never uses `innerHTML` (or anything else that parses markup) with text from the document.
- Loops, defined once (`loopStart(path)` in `app.js`): a path whose last agent appears earlier in it, at index `i` with at least two steps after it, closes a loop from `i` to the end; a path that ends at its first agent (`i = 0`) is a full loop. `A, A` (a loop on one agent) is not this kind of loop and is drawn as before. The loop part is drawn as one closed, fully curved ring in the dotted dash `.5 5.5` (`data-step="loop"`; the flow carries `data-loop` `full`, `part`, `self` or `none`); the lead-in (`A → B` of `A, B, C, B`) stays solid and straight. Dotted means loop: no other drawn flow step is dashed. A full loop's agents (other than the hub, a seat, a group member or an agent already on another loop's circle, which stay where they are) move onto an even circle in path order, in one transition under 300 ms (none with reduced motion); dragging one moves the circle; removing the loop or undoing it puts every agent back exactly. The circle goes in free space: its line and its moved agents keep clear of every circle and name not on it (the hub's name always), and of other loops (two loops meet only at an agent on both); an agent the user placed stays and the circle goes through it, unless that would run into something. No agent on no loop moves.
- Picking lines: every flow, loop and group line has an invisible band 16 screen px wide at any zoom (`vector-effect: non-scaling-stroke`); where bands overlap the line nearest the pointer takes the press, the right-click and the hover. A click picks the whole flow or loop; hover marks all of it and fades the other lines. The cursor is a hand on anything that can be picked and a grab hand on anything that can be moved. Lines bend round a third agent's circle or name only when the straight line would cross it. Nothing in the graph can be selected as text; the camera keeps the graph clear of the usage figures, the status line and the compact sheet; wheel zoom stops before names go under 8 px.
- Drawn flows (`structure.flows`): a forward step is a straight line with an arrowhead, a step back to an agent already on the path a curved arc; steps of different flows on one pair are offset, and a pair that already has something drawn on it (a hub spoke, a task-graph work line, escalation or review loop) gets its flow steps beside that, never on top of it or hidden by it. Every step has a wide invisible hit area above the graph's own lines and loops. A flow is still at rest. A new flow draws in once (about 300 ms); a refused one disappears and the first agent shakes once. No text is added to the graph.
- `index.html` loads `style.css` and `app.js` and calls `AgentGraph.mount(document.getElementById("root"), { fetchSnapshot: () => fetch("/api/snapshot" + location.search).then(r => r.json()), actions: { flow, skills, roster, skillfile, team, output, setup, spend } })`, where each posts its request to its endpoint (`/api/structure/flow`, `/api/skills`, `/api/roster`, `/api/skillfile`, `/api/structure/team`, `/api/output`, `/api/setup`, `/api/spend`) and rejects with the reply's `error` (and its `rev` on a 409); with `?demo` it passes no `actions`. The page passes no `run`: a path's details show "Copy run message" (it asks `flow` `{op: "prompt"}` and puts the text on the clipboard) instead of Run.
- **MCP in the view.** An agent's details have a row `MCP` (its servers by name, each with its state; `none` for an agent with none; no row when `agents[].mcps` is `null`), and a plug button (`MCP servers`, no words) in the bottom-right button cluster, directly beside the menu button (`≡`), toggles a list over the graph (closed by Escape, a click outside, or choosing an agent; shown only when the snapshot has `mcp`): each server's name, state, where it is declared, why it failed (the host's words, as text) and the agents that may use it; an agent's name there picks that agent as a click on it would. Reading only: it works in 2D and 3D, sends nothing, and shows nothing of a server but what `mcp` holds. The web page shows the settings' state. The desktop plugin also asks the host's own client, in the window, `props.session.server.client.mcp.status({directory})` (`GET /mcp`: an object of server name to `{status: connected|disabled|failed|needs_auth|needs_client_registration, error?}`), at each refresh with the snapshot, and only for a name the snapshot lists: it sets `state` (`needs_auth` and `needs_client_registration` are `needs sign-in`, `disabled` is `off`), `live: true` and `reason` (the host's `error` for `failed` and `needs sign-in`, control characters made spaces, one line, cut at 200 characters, drawn as text). A host without that call, an answer in another shape, a failure or no answer within 4 s leave the settings' state, silently.
- The desktop plugin bundles the same `app.js` and `style.css` and passes a `fetchSnapshot` that asks its main process for the document, and `actions` `{flow, skills, roster, skillfile, team, output, run, setup, spend}` (none when its `demo` option is set). Each of the first five takes the endpoint's requests (section 3) and asks its main process over Ipc (methods `flow`, `skills`, `roster`, `skillfile`, `team`; every input field a string) to run `python3 -P -m agentgraph.structure flow ...`, `agentgraph.skillset ...`, `agentgraph.roster agent ...`, `agentgraph.skillfiles skill ...` or `agentgraph.teams add|members|text|remove ...` with `--json`, without a shell, with any instruction, prompt, skill text or goal on stdin. A name, id or flow id outside `agentgraph/names.py` (one starting with `-` included) is refused before anything runs. An `expect` is passed as `--expect REV`: the plugin refuses an `expect` that is not 16 lowercase hex digits (the empty text and `null` included; only an absent `expect` means none) and one on an add, a read or a restore; a stale refusal rejects with an Error whose `rev` is the current one. `actions.output` takes the requests of `/api/output` and asks main (method `output`) to run `python3 -P -m agentgraph.outputs output read|write|restore ... --json`, the text on stdin; a stale refusal rejects with an Error carrying `status: 409, stale: true, rev`. `actions.setup` and `actions.spend` (n10) take the requests of `/api/setup` and `/api/spend` and ask main (methods `setup` and `spend`; every input field a string, the session's folder with each) to run `python3 -P -m agentgraph.setups preset switch|restore ... | setup save|restore|remove|undo ... --dir DIR --json` or `python3 -P -m agentgraph.spend limit AMOUNT [--warn W] | --off --dir DIR --json`, without a shell; there is no free text (a setup's name is checked against the flow-id rule, a preset's name against a plain-name rule, an amount to a number in dollars, a record to an absolute path), an `expect` goes before `--json` as in the others, and a stale refusal rejects with `status: 409, stale: true, rev`. `actions.run({flow: id, goal})` asks main for the run message (`flow` `{op: "prompt"}`) and sends it to the panel's session (the orchestrator's) with `props.session.server.client.session.prompt`; it resolves `{sent: true, session}` or rejects with a message (no goal, no session, a subagent's session, the session changed, no client, the send failed).
