# agentgraph

A terminal viewer and a small set of commands for the multi-agent setup in `~/.config/opencode/`. The nodes it draws are the agents configured there. Python 3.13, standard library only.

Run everything through the launcher, from the project you are working in:

```
~/.config/opencode/orchestration/bin/agentgraph            # live view
~/.config/opencode/orchestration/bin/agentgraph --demo     # live view on sample data
~/.config/opencode/orchestration/bin/agentgraph serve      # the graph as a web page: http://127.0.0.1:4747/
```

The web page is the clean view: one network of your agents that you can pan, zoom and drag, with the lines following the current task's handoffs and review loops, and details on click. Open its address in a browser, or in the OpenCode Desktop Review pane. `desktop-plugin/` holds the same view as a native OpenCode Desktop side panel.

## Commands

| Command | What it does |
|---|---|
| `agentgraph` | Live full-screen view: the agent roster with running sessions, the current task graph by dependency wave, loop state per node, and the event log. |
| `agentgraph --once` / `--html PATH` | One frame as text, or as a standalone HTML page. `--width`, `--height`, `--focus PANEL`, `--scroll N` shape it. |
| `agentgraph check --task NAME` | Validate a task-graph file. Exit 0 sound, 1 problems, 2 unreadable or not found. |
| `agentgraph record` | Save graphs that are marked passed, and pass `check`, to the run ledger. `--task NAME --abandoned` saves one unfinished graph. |
| `agentgraph feedback --text-file PATH` | Save a correction from the user, in their words (`--agent NAME`, `--task NAME`). |
| `agentgraph signals [--json]` | What the ledger says: pending runs and feedback, per-agent numbers, the trend since the last improvement. |
| `agentgraph improved --json-file PATH` | Mark that a prompt improvement was applied. |
| `agentgraph serve [--port N] [--demo] [--open]` | Serve the graph view as a local web page. Listens on `127.0.0.1` only; `?task=NAME` picks a graph, `?select=n4` opens an item's details. |
| `agentgraph snapshot [--demo]` | Print the data behind the view as one line of JSON (the format is in `WEB_API.md`). |

Keys in the live view: `1` agents, `2` tasks, `3` loops, `4` events, `5` issues, `0` dashboard, `j`/`k` or arrows to scroll, `?` help, `q` quit.

## Where things live

- `agentgraph/contract.py`: the data model and the graph validator.
- `agentgraph/sources.py`: reads the agent config, the graph files and OpenCode's session database.
- `agentgraph/view.py`, `viewstate.py`, `__main__.py`: the view, its interactive state, and the CLI.
- `agentgraph/check.py`, `ledger.py`: the `check` command and the ledger commands.
- `agentgraph/snapshot_json.py`, `server.py`, `web/`: the JSON document, the local web server and the graph page.
- `desktop-plugin/`: the OpenCode Desktop side panel.
- `GRAPH_FORMAT.md`: the task-graph file format (`.opencode/graph/<task>.json` in each project). `WEB_API.md`: the contract between the data, the page and the plugin.
- `memory/`: the run ledger (`runs.jsonl`, `feedback.jsonl`, `improvements.jsonl`).
- `proposals/`: prompt edits proposed by the `improve-orchestration` skill, approved or rejected.
- `LESSONS.md`: lessons from finished graphs.
- `tests/`: `python3 -m unittest discover -s tests -t .`

## Limits

- **Nothing here enforces the protocol.** The orchestrator is a model following prompts. `check` catches a malformed or inconsistent graph file; it cannot stop the orchestrator from skipping a step.
- **Live status comes from OpenCode's SQLite database**, opened read-only with a one-second timeout. A read-only reader still takes a normal shared lock while it reads. OpenCode documents session read endpoints that could replace this; that has not been tried.
- **Session matching.** The view shows the session named in the newest graph file. Without one it looks for a session started in the project directory, and if none matches it shows the most recent session anywhere, with a warning on screen. `--session ID` picks one explicitly.
- **Task-graph status is only as fresh as the orchestrator's last write** to the graph file. Agent activity is live; node status is not.
- **Costs are partial.** A run's cost covers the worker sessions named on its nodes that are still in the database, not the orchestrator's own session.
- **The trend is approximate.** A run counts as "after" an improvement when its graph was created after the improvement was recorded, although a session that was already open kept the old prompt until restart.
- **Handoffs are recorded per node, not per edge.** A node's `outputs` field is the only saved handoff. It is enough when every consumer needs the same thing; a different payload or failure route for each consumer is not kept across a restart.
- **The scope check is lexical.** It compares paths as written, anchored to the project root. It does not follow symlinks, so two names that reach the same file through a link are not seen as a collision.
- **Graph discovery stops at your home folder.** For a project outside it, pass `--dir <project>/.opencode/graph`. `check --task NAME` fails loudly when it finds nothing; a bare `check` or `record` prints "no .opencode/graph directory found" and exits 0. Graph files are read without following symlinks, must be regular files under 2 MiB, and at most 200 are read per folder; a `.opencode/graph` folder that is itself a symlink is skipped file by file.
- **Terminals.** Developed against a pseudo-terminal and headless Chrome. Some glyphs depend on the terminal font. Key handling is POSIX only.
