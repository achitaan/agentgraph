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
- Shape fields: a `pattern` that is not one of the names above, and a `feedback_to` entry that names an unknown node, the node itself, or a node that comes after it.
- Lifecycle: a graph marked `passed` with a node that has not passed or an objective not delivered, a `gate` marked `passed` whose `verdict` is not `confirmed`, and a node marked `passed` with no `evidence`.

After the problems, `check` prints lines that start with `advice:`. They never fail the check. They point out a graph that is a single chain, a reworked gate with no `feedback_to`, and fix rounds strung out as separate nodes.

## Recording a finished graph

`agentgraph record` saves graphs whose `status` is `passed`, and which pass `check`, to the run ledger in `~/.config/opencode/orchestration/memory/`. `agentgraph record --task NAME --abandoned` saves one unfinished graph, for a task that was given up. The ledger is what the `improve-orchestration` skill reads.
