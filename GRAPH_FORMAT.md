# Task graph file format

The orchestrator writes one JSON file per task graph to
`.opencode/graph/<short-task-name>.json` in the project it is working in.
The file is the shared state and the checkpoint for the run, and it is what
`agentgraph` draws. Write it whole, as valid JSON, every time; to avoid a
half-written file, write to a temporary file in the same directory and rename it. Field names match `agentgraph/contract.py`.

Times are Unix seconds. Unknown fields are ignored by readers.

```json
{
  "task": "add-rate-limit",
  "goal": "Add per-user rate limiting to the public API",
  "status": "running",
  "session": "ses_...",
  "revision": 1,
  "objectives": [
    {"id": "o1", "text": "Every public route handler is known before the limiter is designed", "nodes": ["n1"]}
  ],
  "amendments": [],
  "lessons": [],
  "created": 1795037000,
  "updated": 1795037420,
  "nodes": [
    {
      "id": "n1",
      "type": "research",
      "agent": "explorer",
      "goal": "Find every public route handler and the existing middleware chain",
      "depends_on": [],
      "scope": [],
      "done_when": "List of handlers with file:line",
      "status": "passed",
      "attempts": 1,
      "tiers": ["explorer"],
      "evidence": "14 handlers in src/routes, middleware in src/app.ts:40",
      "session": "ses_...",
      "started": 1795037010,
      "finished": 1795037090,
      "baseline": "",
      "outputs": "Handler list in the evidence above; middleware order: auth, log, route",
      "changed_paths": [],
      "failure_kind": "",
      "stalled": false,
      "verdict": ""
    }
  ],
  "events": [
    {"time": 1795037000, "node": "", "event": "declared", "note": "1 node"},
    {"time": 1795037010, "node": "n1", "event": "dispatched", "note": "explorer"}
  ]
}
```

## Fields

Graph:

- `task`: short name, same as the file name.
- `goal`: the outcome the whole graph produces.
- `status`: `running`, `passed` or `blocked`.
- `session`: the orchestrator's own session id, if known.
- `created`, `updated`: set `updated` on every write.
- `objectives`: the requirements of the task, written when the graph is declared and frozen after that. Each has an `id`, the `text`, and `nodes`, the ids of the nodes that deliver it. Every objective must name at least one node.
- `revision`: starts at 1 and goes up by one on every amendment.
- `amendments`: one line per change to an objective or to a node's `done_when`, as `"what changed: why (approved by ...)"`. The old text stays in the line.
- `lessons`: one or two lines written at completion about what to do differently next time.

Node:

- `id`: short and stable (`n1`, `n2`, ...).
- `type`: `research`, `decision`, `implement`, `design`, `check`, `gate`, `join` or `human`.
- `agent`: the roster agent that runs it, `orchestrator` for work the orchestrator does itself, or `user` for a `human` node.
- `goal`: what the node produces.
- `depends_on`: ids of the nodes whose output this node needs.
- `scope`: files or directories the node may write. Empty means read-only.
- `done_when`: the evaluation that passes the node (a command or a concrete check).
- `status`: `pending`, `running`, `returned`, `passed`, `failed` or `blocked`. `returned` means the worker reports PASS and the orchestrator has not evaluated it yet; only the orchestrator sets `passed`.
- `attempts`: work/check passes used so far, across all tiers.
- `tiers`: the agents that have worked the node, in order. The node was escalated when two neighbouring entries name different agents; the same agent twice in a row is a resumed session, not an escalation.
- `evidence`: one or two lines that decided the last result.
- `session`: the sub-agent session id of the current run. It links the node to live status.
- `started`, `finished`: when the node first ran and when it passed.
- `baseline`: the evaluation result before any change was made. It separates failures that were already there from regressions.
- `outputs`: what downstream nodes need from this node (an interface, a decision, a path).
- `changed_paths`: the files the node actually changed, checked against `scope` at the join.
- `failure_kind`: for the last failure, one of `code`, `evaluation` (the check itself is wrong), `environment` or `input` (something required is missing).
- `stalled`: true when attempts are repeating without new evidence.
- `verdict`: for `gate` nodes, one of `confirmed`, `refuted` or `unverifiable`. `unverifiable` is neither a pass nor a fail.
- `pattern`: the shape this node is part of, one of `chain`, `route`, `parallel`, `vote`, `orchestrate`, `evaluate` or `synthesize` (the agent patterns Anthropic describes). Optional.
- `artifacts`: the files this node hands on to later nodes, so they can read the work itself instead of a retelling. Each is `{"id": "handoff", "path": ".opencode/artifacts/<task>/<node>/r<revision>/<name>", "revision": 1}`. Use a new path for each revision and never rewrite an older one. A writer node's artifact paths must be inside its `scope`; a read-only node (empty `scope`) has its report saved by the orchestrator under `.opencode/artifacts/`. Optional.
- `inputs`: the upstream artifacts this node reads directly: `[{"from": "n1", "artifact": "handoff", "revision": 1}]`. `from` must be one of this node's `depends_on`, or a node whose `feedback_to` names this node (a reviewer's findings sent back for rework); the artifact must be declared on that node. Optional.
- `base`: the project's commit (a hash of 7 to 64 lower-case hex digits) when the node was dispatched. Optional. When a worker node later turns out to have needed more than one attempt, `agentgraph record` keeps it as a case (`agentgraph cases`), and a case can be run again in a scratch worktree only if it knows this commit; without it the case is kept and marked not replayable. `check` refuses a `base` that is not a commit id (an empty string means unknown).
- `feedback_to`: for a node that evaluates other nodes' work, the ids of the nodes it sends that work back to when it does not pass. It is not a dependency: it draws as an edge going back, and it does not affect the order nodes run in. When a gate refutes, the nodes it names run again on the same sessions and their `attempts` go up; no new node is added for the rework.

Event (`events` is append-only, newest last):

- `time`, `node` (empty for graph-level events), `event`, `note`.
- `event` is one of `declared`, `dispatched`, `attempt`, `returned`, `evaluated`, `retried`, `stalled`, `escalated`, `blocked`, `unblocked`, `joined`, `changed`, `invalidated`, `amended`, `discovered`, `completed`.
- Write one `attempt` event per work/check pass a worker reports, with the note `"what changed -> result"`, so the history of every attempt survives.

## Checking a graph file

`~/.config/opencode/orchestration/bin/agentgraph check` (run from the project, or with `--dir PATH`; `--task NAME` checks only the graph whose `task` is that name, and does not validate the others) reads the JSON strictly and prints every problem. It exits 0 when all graphs are sound, 1 when any has a problem, and 2 when a file cannot be read or the directory or task named does not exist.

It reports:

- Size: more than 1000 nodes, 2000 events or 200 objectives, or more than 200 entries in any list inside a node (`depends_on`, `scope`, `changed_paths`, `feedback_to`, `artifacts`, `inputs`, `tiers`) or in an objective's `nodes`. Readers keep the first entries up to these limits and drop repeated `depends_on` and `feedback_to` entries.
- Wrong types: a required field that is missing or blank, a list field that is not a list of strings, `attempts` that is not an integer, `stalled` that is not a boolean.
- Structure: duplicate node or objective ids, unknown or circular dependencies, a node with no `done_when`, an objective no node delivers, no objectives at all.
- Scope: two nodes that can run in parallel and share a write scope, and a `changed_paths` entry outside the node's `scope`. Paths are relative to the project root and compared as paths, so `src/a.py` and `./src/a.py` are the same file. A scope of `.` covers the project and nothing outside it; a path that starts with `..` needs a scope entry that names it. A node with an empty `scope` is read-only.
- Readiness: a node that is `running` or `returned` while one of its dependencies has not passed.
- Artifacts: an artifact without an id, path or integer revision; duplicate artifact ids on one node; a writer node's artifact path outside its `scope`, or a read-only node's outside `.opencode/artifacts/`; an input whose `from` is neither in `depends_on` nor a node that sends feedback to it, whose artifact the producer does not declare, or whose revision is higher than the producer's; and a node that is `running`, `returned` or `passed` while one of its inputs is older than the producer's current revision (it read stale work).
- Base: a `base` that is not 7 to 64 lower-case hex digits (or empty).
- Shape fields: a `pattern` that is not one of the names above, and a `feedback_to` entry that names an unknown node, the node itself, or a node that comes after it.
- Lifecycle: a graph marked `passed` with a node that has not passed or an objective not delivered, a `gate` marked `passed` whose `verdict` is not `confirmed`, and a node marked `passed` with no `evidence`.

After the problems, `check` prints lines that start with `advice:`. They never fail the check. They point out a graph that is a single chain, a reworked gate with no `feedback_to`, and fix rounds strung out as separate nodes.

### Flows from the structure folder

`check` also enforces the flows drawn in the structure folder (`structure/flows/`, see `structure/README.md`; `--structure DIR` reads another folder, and exits 2 when that folder does not exist or holds no group folder; a missing default folder just means no flows). A flow applies to every `implement`, `design`, `research` or `decision` node of its first agent (the second when the hub is first) that has not passed, and wants the rest of the path as a chain from that node: a step to a new agent needs a node run by that agent that follows the current one (lists it in `depends_on`, or is a `gate` whose `feedback_to` names it); a step back to an agent already on the path needs the current node to be a `gate` whose `feedback_to` names that agent's node on the chain, and the chain goes on from there. A node run by a council seat counts as the council's (the lead's `seats` prefix). The hub as the last step adds nothing. An agent's loop on itself (a path `A → A`: it checks its own work against the task's evaluation and repeats until it passes) adds no rule either; `check` prints it with the others. A missing step is one problem per starting node, such as `n12 (impl-openai) is not followed by verifier: flow impl-openai → verifier → impl-openai`. After its result lines, `check` prints every flow (path, id and the user's instruction, under `flows:`) so the orchestrator follows them when it shapes a graph; this changes neither the exit code nor the first line.

A flow file whose path names an agent the structure folder no longer has cannot be read as a flow: `check` and the snapshot skip it with a warning that ends with how to remove it (`agentgraph flow remove ID`, which also deletes its command file while that file is exactly the one written for it and no readable flow holds the name), and `structure flows` lists it under `unreadable`.

A flow file may also carry `name: NAME` (2 to 32 lowercase letters, digits and `-`, unique among flows) and `command: on` (the path is also the slash command `/NAME`, see `COMMANDS.md`) after its `path:` line; `check` ignores both. `agentgraph flow prompt ID --goal-file F|-` (or `--name NAME` for the path of that name) prints the message that runs a path on a goal: it asks the orchestrator to declare the task graph so that this check passes, with the goal and the flow's instruction fenced as data. It lists the path once, one numbered line per step (a return marked `(return)`), and names it by its name, or by its id when it has none. Its rule is this check's: a step to a new agent is a node it runs, depending on the current node; a return is no node: the current node is a gate whose `feedback_to` names that agent's earlier node, which becomes the current node. So for `A → B → A → C`, C's node depends on A's node, not on B's gate. It says that every other drawn path applies too, to each node of its first agent, and names the ones that can make this check refuse a graph built that way (`flowrun.binding`). That list is the closure: a drawn path is listed when it applies to an implement, design, research or decision node (`check.FLOW_START_TYPES`) of the graph the message's words make and that graph does not already follow it there (a path inside this one, as `A → B` inside `A → B → C`, needs nothing more); it is followed from each such node of its first agent (the hub dropped) by the same rule, and the nodes that adds can bring in further paths, until nothing new comes. A node that following path F adds and that would start again a path it was made for (this path, F, or one F came from) would make the graph grow without end, since as a worker's node that path applies to it again: for P = `A → B` with Q = `B → A` drawn, Q's A node starts P again. The message names each such node under "Make these check nodes, or each starts a path again without end:" (its agent, F, and the path it would start again; "this path" for P): as a check node no path applies to it, and the graph is finite and passes (tests/test_flowrun.py, OtherPaths, builds the graph from the message's words alone and runs this check). The paths are listed in the order they were first followed, by name or id with their steps: at most three, then "and N more, by id:" with the remaining ids. A slash command's file (`/NAME`) carries the sentence but no list, which would go stale as other paths are drawn; it says to run `agentgraph flow prompt --name NAME --goal-file -` to see which apply now (at most 140 characters and the name, `flowrun.COMMAND_SHARE`). Without the goal and the instruction it is at most 775 characters plus each step's agent name and 20, plus that name or id (`flowrun.TEMPLATE_FIXED`, `STEP_SHARE`): under 1,500 for every path the toolkit accepts whose agent names are 12 characters or fewer (20 steps, returns and the hub included, a 32-character name or an 80-character id); longer names add their own length and nothing more. The listed paths add at most 230 (`OTHERS_FIXED`) plus, for each one shown, its name or id, 8 and each step's agent name and 3 (`OTHER_SHARE`, `ARROW_SHARE`): at the worst, three listed paths of 20 steps of 12-character names, each shown by an 80-character id, 2,889. Each path past the third adds its id and 2 (`MORE_SHARE`), each check node its agent's name, the two paths' names or ids and 42 (`CHECK_SHARE`). The graph the closure grows is cut at 200 nodes (`GROWTH_LIMIT`).

#### What a path costs (`structure flows --stats`, the snapshot's `structure.flows[].stats`)

A **run** of a flow is a task graph that followed it, by the one definition `check` uses (`check.follows`): a node of the flow's first agent (of a start type, whatever its status) has the rest of the path after it as a chain, and no node of that agent that has not passed lacks one, so a graph `check` rejects for the flow is never a run of it. A run counts under the longest flow it follows: a chain that lies inside a longer flow's chain in the same graph (the shorter path's agents in a row in the longer one's, from the same node, as `A → B` inside `A → B → A`) counts for the longer flow only. Two drawn flows with the same path follow the same chains, and one run counts once: under the flow with the lowest id (in string order); the other does not count it. The graphs come from the run ledger (`runs.jsonl` written by `agentgraph record`, newest row per graph path; `--ledger DIR`, default the active preset's ledger) and from the graph files the caller already reads (the server's graphs; `--graphs DIR` on the command line); each graph counts once. Each is read from its graph file, because a ledger row keeps no edges: a ledger row whose graph file is gone is not counted. `stats` is `null` for a flow that never ran. Two shapes cannot be measured, because a task graph cannot show them and `check` does not enforce them: an agent's loop on itself (`A → A`) and one agent with the hub (`hub → A`, `A → hub`). Their `stats` has every field `null` (`runs: null`: unknown, not "never ran"); `structure flows --stats` prints `runs unknown` for them. Otherwise:

| Field | Where it comes from |
|---|---|
| `runs` | The number of graphs that followed the flow. |
| `passed` | Of those, the ones whose task passed: the ledger's `outcome` for a recorded graph, the graph's `status` for one not yet recorded. |
| `rounds.avg`, `rounds.max` | Per run, the most `attempts` of a node on the flow's chain (the ledger's node record, else the graph file's). `null` when any run has a chain node without `attempts`. |
| `cost.total`, `cost.avg` | Per run, what the chain's nodes cost: the ledger's `worker_cost` when the chain's nodes hold every session the graph names and the ledger knew each one's cost; else the sum of those sessions' costs in OpenCode's database (`--db`). `null` for the whole field when any run has a chain node without a session, a session that also served a node off the chain (its cost cannot be split), or a cost the database does not hold. |
| `last` | The newest run's `updated` time (seconds); `null` when no run has one. |

Nothing is written; an unknown number is `null`, never an estimate.

### Pool, escalation and groups from the structure folder

- **`escalates_to`** (a problem). A node's `tiers` list the agents tried in order; a move from one agent to another is an escalation. When the earlier agent has `escalates_to` in the structure folder, the move must go to that agent: `n4 was escalated from impl-openai to impl-hard; impl-openai escalates to impl-claude (structure folder)`. The same agent twice in a row is a resumed session; an agent without the setting may move anywhere. A council seat counts as its council. None of the graphs on disk when this was added breaks it.
- **`pool`** (a warning). When more nodes of one agent than its `pool` could run at once (no one depends on another, directly or through other nodes, whatever their status: the largest such set), `check` prints `warning: 4 nodes of impl-openai can run at once (n1, n2, n3, n4), more than its pool of 3 (structure folder)` after the `advice:` lines. It never changes the exit code: a graph that passed on disk has 9 parallel `impl-claude` nodes against a pool of 2, and the delegation policy starts more instances when there are more independent lanes than slots.
- **Groups** (`structure/teams/`, `agentgraph.teams`): where two or more nodes are run by a group's members, one node run by its lead depends on each of them. The run message (`flow prompt`) names each group this rule would fail the run's graph on, with its members, under "These groups apply: give each lead a join node depending on every node of its members:".

`agentgraph flow scaffold P --task T [--dir DIR]` (P a flow id or a path's name) writes the graph the run message describes to `.opencode/graph/T.json` (the nearest such folder, else `./.opencode/graph`; `--dir` another): the path's nodes with `depends_on`, gates with `feedback_to` and `pattern: evaluate`, every other drawn path that binds (check nodes where a path would start again without end), a `join` node of each lead whose group applies, one artifact per node at `.opencode/artifacts/T/<node>/r1/handoff.md` with an input per edge, every status `pending`. The task's `goal`, each node's `goal` and `done_when` and the objective's `text` are empty; filled, the graph passes `check` with every drawn path and group. It never writes over a file: an existing `T.json` is refused and left as it was.

## Recording a finished graph

`agentgraph record` saves graphs whose `status` is `passed`, and which pass `check`, to the run ledger in `~/.config/opencode/orchestration/memory/`. `agentgraph record --task NAME --abandoned` saves one unfinished graph, for a task that was given up. The ledger is what the `improve-orchestration` skill reads. Recording also keeps each worker node that did not pass first time as a case (`cases.jsonl`; see `agentgraph cases` in the README).

## Outputs edited by hand

The user can open a step's output (a node's artifact) from the graph view, the Desktop panel or `agentgraph output`, and save a new text for it (`agentgraph/outputs.py`). Nothing is ever run.

- **What can be edited.** A file inside the task's own artifacts folder, `.opencode/artifacts/<task>/`, reached without following a link; UTF-8 text with no control character other than tab, newline and carriage return; at most 256 KiB. A passed node's outputs may be edited. Anything else is shown with `editable: false` and a plain reason (`why`): the file is missing, it is not text, it is larger than 256 KiB, it is outside the task's artifacts folder or reached through a link, it has another name (a hard link, which may be a file outside the project; never read or written), or its next revision would fall outside the writer node's `scope`.
- **What the snapshot shows.** Each entry of `graph.nodes[].artifacts[]` gets `rev` (16 lowercase hex digits from the output's path, revision and bytes), `size` (bytes, `null` without a file), `editable` and `why` (empty when editable). Never the content. The snapshot remembers the rev of a file it read by the file's identity (device, inode, size, mtime and ctime, with the graph's path and revision; at most 800 entries), so an unchanged output is not read again on the next snapshot.
- **What a save does.** The text is written as the output's next revision, `<node>/r<N+1>/<name>` beside `r<N>` (the earlier file is never rewritten), and the graph records the new `path` and `revision`. So every node whose `inputs` read revision N is stale by the usual rule, and `check` reports it. The graph gets one `changed` event on the node with the note `output <id> edited by hand by the user: revision N -> N+1 (<path>)`, and `updated` is set. A save that gives `expect` (the `rev` the view showed) changes nothing when the output changed since: "changed since you opened it; reload and try again" with the current `rev` (409 on the web endpoint). A save is one undoable step: its undo record (`~/.config/opencode/backups/<date>-outputs/edit-<id>/record.json`, `<id>` being the edit log's `id`) puts back the graph as it was (byte for byte when nothing else changed it; otherwise only the artifact's `path` and `revision` and the edit's event) and removes the new file. An undo is refused, naming what stands in the way, when a later run replaced the output in the graph, or a file was changed after the edit; it is refused, with nothing written, when the record's graph is not in one of the request's own graph folders (found as a read or a write finds them), or when the record's graph path is not its real path (every lock and write uses the path that was checked). An undo whose edit-log line could not be marked undone still succeeds, and its answer says so in `warnings`.
- **The shared graph file.** The orchestrator rewrites the graph whole at any moment. A save merges only its own fields (the artifact's `path` and `revision`, its event, `updated`) into the graph as it is on disk, under a lock on the graph folder, and puts the result in place with an atomic exchange; when the file it displaced is not the one it merged into, that file goes back and the merge is made again from it, so an orchestrator's change is never lost. When the orchestrator changed the same output's record meanwhile, the save is refused and its file taken back.
- **The edit log.** Every save appends one JSON line to `edits.jsonl` in the memory home, beside `runs.jsonl` and `feedback.jsonl` (`memory*/` is git-ignored): `time` (Unix seconds), `id`, `task`, `graph` (the graph file's real path), `node`, `agent` (the node's agent), `artifact` (the output's id), `from_revision`, `revision`, `path` (the new revision's), `chars_before`, `chars_after`, `lines_added`, `lines_removed` and `undone` (`false`; an undo sets it to `true` and adds `undone_time`). Never the content. The line is written before the graph's lock is taken (an edit never waits on the memory home's lock while it holds the graph's) and so before the graph is saved, with `lines_added` and `lines_removed` `null`; it is planned from the graph as it is then, and the save refuses, changing nothing, when the graph says otherwise under the lock. The counts are filled in after the graph's lock is released, so a save stopped at any point after the graph is saved still leaves its line (counted, and undoable from its record); a refused save takes its line back. The counts come from a line diff when both texts have at most 200 lines; above that they compare the lines as multisets (linear time; a moved line is not counted). A count can stay `null` when the save was stopped before it.
- **An edit the graph lost.** When an orchestrator later rewrites the graph from its own older copy, dropping the edit's record, `check` reports `<node> output '<id>' was edited by hand after the graph last recorded it` from the edit log (the newest edit not undone whose revision and path the graph does not record, unless the graph records a later revision and still holds the edit's `changed` event). Record that revision with its path, or undo the edit. A line whose save was stopped before the graph was written (its counts `null`, no undo record, and the graph never had its revision or its event) is not a problem and not a hand edit for the improvement loop: `check` gives a warning that names its file, which can be deleted (until it is, the next save of that output is refused, since the file is where the next revision goes). `check --home DIR` reads the edit log in DIR; the default is `$AGENTGRAPH_HOME`, else the active preset's memory folder.
