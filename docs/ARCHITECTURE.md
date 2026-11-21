# Architecture

How the parts of the toolkit fit together, where each lives, and which rules are enforced by code and which only by prompts.
This page does not repeat reference material. For details see:

- [`docs/LOOPS.md`](LOOPS.md): loops, the run record, statuses, limits, guarantees.
- [`WEB_API.md`](../WEB_API.md): the page's HTTP API and the snapshot document.
- [`GRAPH_FORMAT.md`](../GRAPH_FORMAT.md): the task graph file.
- [`docs/CUSTOMIZE.md`](CUSTOMIZE.md): what is yours and what is the toolkit's, install, update, backups.
- [`docs/REPORT.md`](REPORT.md): what was built and measured, with the limits.

Everything is Python standard library (`agentgraph/`), launched by `bin/agentgraph`, a shell script that runs
`python3 -P -m agentgraph.<module>`. Three views draw the same data: a web page, an OpenCode Desktop side panel and a terminal sidebar.

## Parts and how they connect

```
  prompts + presets            task graph file              OpenCode database
  config/ skills/ commands/    .opencode/graph/<task>.json  (read only)
        |                            |   ^                        |
        v                            v   | graph set / event      v
   the agents (OpenCode) --------> agentgraph check <--- status (liveness) / cost / sources
                                     |  keeps .history/               |
                                     v                                v
                          record -> ledger (memory*/)          Snapshot -> view.py (terminal)
                                     |                                |
                       cases, signals, improve proposals              +-> snapshot_json -> server.py -> web page
                                     |                                |                       ^
                       bench (scratch config) <------ evidence        +-> desktop-plugin / tui-plugin
                                                                      |
                          saved loop (structure/.loops) -> looprun controller -> run record (.opencode/loops/<run>/)
                                                           (one detached process per run)  ^ loopapi <- server.py
```

| Part | Lives in | What it does |
|---|---|---|
| Task graph file and its checker | `.opencode/graph/<task>.json` (format: `GRAPH_FORMAT.md`); `agentgraph/check.py`, `agentgraph/transitions.py`, `agentgraph/graphedit.py` | `check` validates a graph and keeps a small state record in `.opencode/graph/.history/`; the next `check` rejects changes a graph may not make (passed work undone, attempts lowered, criteria weakened). `graph set` and `graph event` change one node under a lock and refuse an edit `check` would reject. |
| Liveness and resume view | `agentgraph/status.py`, `agentgraph/contract.py`, `agentgraph/sources.py` | `status --task T` reads the graph and the OpenCode database and says which nodes are ready, `live`, `quiet`, `orphan` or `unreported`. Read only; nothing is stored in the graph. |
| Data for the views | `agentgraph/sources.py` (config + graph files + database -> Snapshot), `agentgraph/contract.py` (the Snapshot type, shared by sources and views), `agentgraph/snapshot_json.py` (the JSON document) | The database is opened read only. Bad or missing input becomes warning strings, not exceptions. |
| Ledgers and records | `agentgraph/ledger.py`, `agentgraph/cases.py`, `agentgraph/handedits.py`, `agentgraph/overturned.py`; data in `memory*/` and `proposals*/` | `agentgraph record` saves finished graphs and feedback. Each worker node that did not pass first time becomes a case, half held out. Signals say what should improve. Ledger homes: `memory/`, `memory-fast/`, `memory-normal/`, `memory-o2/`. |
| Cost | `agentgraph/cost.py`, `agentgraph/spend.py`, `agentgraph/bloat.py` | `cost` shows billed, messages and unreported spend. `bloat` measures the fixed text each agent's turn carries. `spend` stores the project's spend limit. |
| Bench | `agentgraph/bench.py`, `bench/scenarios/`, `bench/results/` | Runs fixed tasks on a scratch copy of the config and measures tokens, cost, time, calls, correctness and recovery. `bench fingerprint` hashes the live config and ledgers to show they did not change. |
| Improve cycle | `agentgraph/improve.py` | A proposed prompt edit is one JSON file in `<ledger home>/proposals/` with evidence, results and a verdict computed by code. Approve (backup first), reject, revert by hash and reopen are commands and page buttons. |
| Loop controller | `agentgraph/looprun.py` | See below. |
| Page server and API | `agentgraph/server.py` (HTTP, localhost), `agentgraph/loopapi.py` (loop answers) | `server.py` parses a request and replies; `loopapi.py` holds pure functions over folders. Routes under `/api/`: snapshot, chat, improve, loop, loop/run, loops, output, roster, setup, skillfile, skills, spend, structure/flow, structure/team. Reference: `WEB_API.md`. |
| Structure, flows, teams, routing | `agentgraph/structure.py`, `agentgraph/flowrun.py`, `agentgraph/flowcommands.py`, `agentgraph/teams.py`, `agentgraph/routing.py`; data in `structure/` | The hub and groups the user draws, and the paths (flows) between agents. A flow is one file in `structure/flows/`; loop settings sit beside it in `structure/.loops/<flow>.json`, a folder made when a loop is saved. |
| Roster, setups, skills, outputs | `agentgraph/roster.py`, `agentgraph/agentfiles.py`, `agentgraph/setups.py`, `agentgraph/skillfiles.py`, `agentgraph/skillset.py`, `agentgraph/outputs.py`, `agentgraph/models.py` | What the views edit: agents, presets, saved setups, skills and step outputs. Their writes are undoable from the views (`WEB_API.md`). |
| Install and sync | `install.sh` (work in `agentgraph/installer.py`), `sync.sh` | `install.sh` copies commands, skills, presets and prompts into `~/.config/opencode`, keeping user changes (`--dry-run`, `--list-backups`, `--restore`, `<name>.toolkit-new`). `sync.sh` copies installed files back into the checkout; personal exclusions are in the git-ignored `sync.conf`. See `docs/CUSTOMIZE.md`. |

## The loop controller

`agentgraph loop start` writes a run record and starts one detached process (`agentgraph/looprun.py`, class `Controller`). A model never
decides whether the run passed, how many attempts remain or what happens next; the controller does, from the saved settings.

```
 queued -> running -> passed | failed | limit | invalid | check_error | inconclusive | stalled | agent_error | cancelled   (final)
              |  ^
              v  |  loop resume
   paused / waiting (stored, not final)      interrupted (shown only: record says running, no process)
```

One attempt, for `A -> A`: send the goal to the agent (`opencode run`, same session on later attempts), run the check command, read its exit code.

| Check outcome | Exit code | What follows |
|---|---|---|
| pass | 0 | The run passes. |
| inconclusive | one of `inconclusive_codes` (default 75), or a timeout | The run stops `inconclusive`; it does not guess. |
| check_error | 126, 127, killed by a signal, or cannot start | The run ends `check_error`. |
| fail | any other | The failure text goes back to the agent; a new attempt if limits allow. |

For `A -> J1 -> ... -> Jk -> A`, each judge replies and its last line must be exactly `VERDICT: PASS` or `VERDICT: FAIL`. A failure goes back to the
worker as findings; routes, retries, escalation, artifact copies and a person's approval are settings. The statuses, routes and limits are defined in `docs/LOOPS.md`.

Recovery: before each step the controller writes a cursor into `run.json`. After a crash, `agentgraph loop resume RUN` reads it. A finished step is
not run again; a check step runs again; an agent step is reconciled with OpenCode's database as `not_sent` (send it), `finished` (adopt the reply) or
`unknown` (pause, send nothing). `--redo` and `--accept` are the person's choices. Each run pins its own `definition.json`, so editing the saved loop does
not change a run.

Run record, in `<project>/.opencode/loops/<run id>/`: `definition.json`, `run.json`, `events.jsonl`, `attempt-N/...`, `lock`, `cancel` or `pause`,
`answer.json`, `run.log`. Fields: `docs/LOOPS.md` section "The record".

## What code enforces and what prompts enforce

| Rule | Enforced by | Where |
|---|---|---|
| A loop's attempts, check, limits, final status and next step | Code | `agentgraph/looprun.py` |
| A run is `passed` only by a check exit 0 and judge `VERDICT: PASS` lines (or a person's approval) | Code | `agentgraph/looprun.py` |
| A cost limit is checked before each turn; an unknown cost with a cost limit ends the run | Code | `agentgraph/looprun.py` |
| A graph edit that undoes passed work, lowers attempts or weakens criteria | Code | `agentgraph/check.py`, `agentgraph/transitions.py`, `agentgraph/graphedit.py` |
| Whether a running node is live, quiet or orphan | Code | `agentgraph/status.py` |
| Install and update keep user changes | Code | `agentgraph/installer.py` |
| A proposal's verdict | Code | `agentgraph/improve.py` |
| Which agent works, who delegates, when to write a graph, which commands the orchestrator calls | Prompts only | `config/`, `skills/` |
| Delegation policy | Prompts only | `config/AGENTS.md` and the `orchestrator_append.md` files |

`docs/REPORT.md` section 2.4 lists the evidence for each code rule and what is still prompts only.

## Files and their formats

| File | Format reference |
|---|---|
| `.opencode/graph/<task>.json` | `GRAPH_FORMAT.md` |
| `.opencode/graph/.history/<stem>.state` | `agentgraph/transitions.py` docstring |
| `structure/flows/<id>.md` and `structure/.loops/<flow id>.json` (version 1) | `docs/LOOPS.md` "Save a loop" |
| `.opencode/loops/<run id>/` | `docs/LOOPS.md` "The record" |
| `<ledger home>/runs.jsonl`, `feedback.jsonl`, `cases.jsonl`, `proposals/<id>.json` | `agentgraph/ledger.py`, `agentgraph/cases.py`, `agentgraph/improve.py` docstrings |
| `bench/results/*.jsonl`, `bench/scenarios/` | `bench/README.md` |
| The snapshot and every `/api/` answer | `WEB_API.md` |

## The three views

| View | Lives in | Notes |
|---|---|---|
| Web page | `agentgraph/web/app.js`, `style.css`, `index.html`; served by `agentgraph serve` | The reference view. Checks run in headless Chrome: `sh agentgraph/web/test/run.sh`. |
| OpenCode Desktop side panel | `desktop-plugin/` (plugin `agentgraph-desktop`, built by `desktop-plugin/build.mjs`) | The same page inside the panel. The built bundle must be rebuilt after the sources change; the harness fails check 63 until then. |
| Terminal sidebar and terminal view | `tui-plugin/` (sidebar), `agentgraph/view.py`, `agentgraph/viewstate.py` (terminal view) | `view.py` draws a Snapshot; `viewstate.py` holds keys and state. Neither imports the other. |

All three read the same Snapshot and, for loops, the same run record through `loopapi.py` or the command line.

## How to extend without editing core files

Add agents, presets, prompts, skills, flows and loop settings as your own files, and keep updates working: `docs/CUSTOMIZE.md`
("What is personal and what is the toolkit's"). Loop settings and flows are data (`structure/flows/`, `structure/.loops/`); a new check is a command
you name in `agentgraph loop set --check`.
