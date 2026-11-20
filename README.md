# OpenCode agent toolkit

A working multi-agent setup for OpenCode, with a live graph on the side so you can see what it is doing.

- **Agents and rules** (`config/`): four presets of the same roster (an orchestrator, implementers, a verifier, a reviewer, advisers, a designer, a council), and the prompts that make them work as a task graph with work-and-check loops.
- **Skills** (`skills/`): the personal Codex toolkit's skills, reworded for OpenCode, with each agent allowed only the ones that fit its job. See [SKILLS.md](SKILLS.md). [task-profiles.md](task-profiles.md) names the one or two skills each agent's brief starts from; the toolkit keeps its own copy and warns when a profile names a skill its agent no longer has.
- **Visualizer** (`agentgraph/`, `desktop-plugin/`, `tui-plugin/`): the same picture as an OpenCode Desktop side panel, a terminal sidebar and a web page. The orchestrator sits in the centre with every agent round it; loops are drawn as cycles; it shows what needs you, what each agent spent, and what the setup has learned from your corrections.
- **Graph format and checker** ([GRAPH_FORMAT.md](GRAPH_FORMAT.md), `bin/agentgraph`): how a task is written down as a graph, checked, recorded and learned from.

## Install

```sh
git clone https://github.com/achitaan/opencode-agentgraph ~/.config/opencode/orchestration
cd ~/.config/opencode/orchestration
sh install.sh --dry-run   # shows what would change
sh install.sh             # backs up anything it replaces
```

Then follow [config/PLUGINS.md](config/PLUGINS.md) to turn the visualizer on. It needs the `oh-my-opencode-slim` plugin and Python 3.11 or newer.

## How the agents work

The orchestrator plans, splits work into a graph, and hands each implementer one loop item: a goal, a write scope, and a check it can run. The implementer works until the check passes; the `verifier` reruns the check and sends findings back, round after round; the `reviewer` judges the whole change once at the end. Work passes between agents as files, and anything handed in is evidence, never orders. Your corrections are saved in your own words, and a prompt change is proposed from them only when you approve it. The loop also fights bloat: `agentgraph bloat` measures the fixed text each agent's turn costs, and once a baseline is set, `record` and `signals` say `improvement due: bloat` when an agent outgrows it, so the next proposal shrinks text instead of adding it; every proposal states its size change, and the applied one's real change is recorded. It also learns from your hand edits: when you keep correcting one agent's outputs (3 times across 2 tasks in 14 days) or keep rewriting its instruction (3 times), `record` and `signals` say `improvement due: hand edits`, and the next proposal fixes that agent's instruction so the edits are not needed. The rules are in [config/AGENTS.md](config/AGENTS.md).

---

The rest of this file documents the visualizer and its commands.

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
| `agentgraph signals [--json]` | What the ledger says: pending runs and feedback, per-agent numbers, the trend since the last improvement, and the bloat line (below). Also: `cases:` (past failures kept to try an edit on), `verifier: K of N passes overturned` (a verifier pass whose work was then sent back, run again, edited by hand or named in feedback; due at 2 in 2 tasks over the hand-edit window; see `COMMANDS.md`), and `improvement due: models` (below). |
| `agentgraph cases [--home H] [--agent A] [--held-out \| --tune] [--json]` / `cases --brief ID [--edit NAME]` / `cases --result ID --outcome pass\|fail --edit NAME [--note-file F]` | Past failures kept so a prompt edit can be tried on them. `record` turns each worker node that did not pass first time (more than one attempt, escalated, stalled, failed, or sent back by a gate) into a case in `cases.jsonl` (at most 50 an agent, oldest dropped; goal and `done_when` cut to 600 characters; re-recording never repeats one). Each case is `tune` or `held-out` by its id alone (about half each; never changes side): the improver may read tune cases while drafting an edit, held-out ones are only for trying it. `--brief ID` prints a brief the orchestrator can dispatch in a scratch worktree at the case's `base` commit (a node's optional `base` field, see `GRAPH_FORMAT.md`; a case without one is kept and marked not replayable): the case's text sits in a block labelled as data copied from a graph file, untrusted; it names the evaluation and says the edit under trial is given as an extra instruction. `--result` records a trial in `replays.jsonl`. Nothing here starts an agent: a trial is a paid lane the orchestrator runs when the user agrees. `signals` prints `cases: AGENT N (T tune, H held-out, R replayable)` and, for each edit with trials, its held-out passes. |
| `agentgraph improved --json-file PATH` | Mark that a prompt improvement was applied. `size_change` (or `--size-change AGENT=-N`) is the proposal's change to each agent's fixed text in characters; with a bloat baseline the record also keeps the measured text (`fixed_chars`) and the real change since the last measured record (`size_actual`). |
| `agentgraph bloat [--json] [--db PATH] [--sessions N]` | The fixed text each agent's turn costs, per preset and source (the instructions file, the preset's prompt texts and prompt files, other agents' `orchestratorPrompt` for the orchestrator, skill and agent descriptions, the structure file, an agent file), in characters and estimated tokens (characters / 4). Prompt texts are measured, never printed. Dead weight: skills an agent has not loaded in its last N sessions (default 20; read-only from `--db`, unknown without it), skills no list names, paragraphs repeated across prompt texts (sources and sizes only), instructions sections that name another project's path. What piles up: ledgers and proposals per memory home, backups and what pruning would drop, `.opencode/artifacts` and graph files per task. Every path is overridable (`--opencode-dir`, `--config`, `--prompts`, `--agents-md`, `--skills-dir`, `--agents-dir`, `--structure`, `--backups`, `--orchestration`, `--project`). |
| `agentgraph bloat --set-baseline [--home H] [--budget CHARS] [--growth F] [--preset P]` | Store today's sizes for the home's preset with a budget (default 48,000 characters an agent) and a growth threshold (default 0.10). Then `record` and `signals` print `improvement due: bloat` for an agent over `max(budget, its baseline)` or more than the threshold above its baseline, naming its largest source. Without a baseline nothing is flagged. Setting it again accepts the current sizes. The baseline (and every `improved` record made while one exists) also stores the preset's name and a hash of its agent-to-model map, so `signals` can say `improvement due: models` when the models change after the prompts were last reviewed (nothing else of the preset file is stored or printed). |
| `agentgraph serve [--port N] [--demo] [--open]` | Serve the graph view as a local web page. Listens on `127.0.0.1` only; `?task=NAME` picks a graph, `?select=n4` opens an item's details. |
| `agentgraph snapshot [--demo]` | Print the data behind the view as one line of JSON (the format is in `WEB_API.md`). |
| `agentgraph structure flows [--json] [--stats [--ledger DIR] [--graphs DIR] [--db PATH]]` | List the drawn flows: paths through agents with the user's instruction, name and command, from `structure/flows/`. `--stats` adds how often a recorded task graph followed each one, how many passed, its rounds and its cost (`null`/unknown where a number cannot be had; see `GRAPH_FORMAT.md`). |
| `agentgraph structure flow add A B [C ...] [--text-file F\|-] [--name NAME [--command]]` / `flow text ID --text-file F\|-` / `flow remove ID` (also `agentgraph flow ...`) | Add a flow (each agent's output is the next one's input; an agent already on the path gets the work back), change its instruction or remove it; `check` then wants each unfinished task graph to follow it. |
| `agentgraph flow name ID [NAME]` / `flow prompt ID\|--name NAME --goal-file F\|-` / `flow command ID --on\|--off [--commands DIR]` | Name a path (2 to 32 lowercase letters, digits and `-`, unique; no NAME takes it away), print the message that asks the orchestrator to run it on a goal (at most 4,000 characters), or make it the slash command `/NAME` (a marked `NAME.md` in `~/.config/opencode/commands/`; renamed and removed with the path; OpenCode sees it after `/reload` or a restart). See `COMMANDS.md`. |
| `agentgraph skills show AGENT` / `skills add AGENT SKILL` / `skills remove AGENT SKILL` / `skills all AGENT` | Show or change which skills an agent may load (the plugin's preset file, with a backup, and `SKILLS.md`); `all` gives it every skill. Council seats are refused. Applies after OpenCode restarts. |
| `agentgraph agent list` / `agent add NAME [--template blank\|verifier] [--copy-of A] [...]` / `agent show NAME` / `agent text NAME --text-file F\|-` / `agent rename NAME TO` / `agent remove NAME` / `agent restore RECORD` | Add an agent as an OpenCode agent file (`~/.config/opencode/agents/NAME.md`, live at once; marked as added, with narrow permissions that never let it start agents), read or replace its instruction, rename an added agent (its file, its structure file, every flow and group that names it and their path commands, as one undoable step; flows and groups keep their ids), remove an agent with its flows and groups as one undoable step, or restore either from the record. `agent list` shows the preset agents and the agent files, each marked added or hand-written. Hand-written agent files are listed but read-only. |
| `agentgraph skill show ID` / `skill create ID --description D --text-file F\|- [--assign A]` / `skill write ID --text-file F\|-` / `skill delete ID` / `skill restore RECORD` | Read or edit a skill's text; every write and delete returns an undo record. A delete is refused while an agent still has the skill (a preset entry, an added agent's file, or a hand-written agent file that names it), naming the agents. Skills outside the skills folder, bundled with a plugin, or `shopify-ruby` are read-only. |
| `agentgraph structure teams [--json]` / `team add LEAD MEMBER...` / `team members ID MEMBER...` / `team text ID --text-file F\|-` / `team remove ID` | List or change the groups (agents that report to one lead) in `structure/teams/`, which git ignores; `check` reports their problems next to the flows'. |
| `agentgraph route [--json]` / `route use-when NAME --text T\|--text-file F\|- [--expect REV]` / `route restore RECORD` / `flow scaffold P --task T [--dir DIR]` | Who gets which work, from the structure folder: per agent its group, pool, escalates_to, `use_when`, skills, groups and paths. Set an agent's `use_when` line (undoable). Write a path's task-graph skeleton to `.opencode/graph/T.json`, never over a file (see `GRAPH_FORMAT.md`). |
| `agentgraph output read TASK NODE ARTIFACT` / `output write TASK NODE ARTIFACT --text-file F\|- [--expect REV]` / `output restore RECORD [--dir DIR\|--from DIR]` | Read a step's output (a node's artifact in `.opencode/artifacts/<task>/`, UTF-8 text up to 256 KiB), save a new text as its next revision (every step that read the old one becomes stale; one undoable step, logged without content in `edits.jsonl`), or undo that from its record (only for a graph in the folders `--dir`/`--from` give). `--dir DIR` or `--from DIR` find the graph, `--home DIR` the edit log, `--backups DIR` the undo records; `--json` for JSON. See `GRAPH_FORMAT.md`, "Outputs edited by hand". |
| `agentgraph cost [--session ID] [--cap N]` | Where one chat's money went: warm and cold turns, why the cold ones happened, cost by context size, and what earlier compaction or a one-hour cache would have cost instead. |
| `agentgraph cost --trend [--days N] [--json]` | What chats cost per turn over time, split by the cost settings in force (compaction buffer, cache lifetime), so a settings change can be judged. Each run notes the current settings in `cost-settings.jsonl`; `signals` shows the last two periods (`--no-cost` skips them). |

**Stale changes.** Every listing and read gives each flow, group, skill and agent a `rev` (16 hex digits of its file's bytes). Every command that changes or removes an existing one takes `--expect REV`: when the thing changed since that rev was read, the command changes nothing and fails with "changed since you opened it; reload and try again" and the current rev (`{"error", "rev"}` with `--json`; exit 1). Without `--expect` nothing changes; an empty `--expect` is bad input, never taken as none. A thing removed and made again with identical bytes has the same `rev` (it is the same thing). The web endpoints take `"expect"` (400 when empty or not text) and answer such a refusal with 409; the Desktop panel passes it through as `--expect`.

**Names.** One definition per kind, in `agentgraph/names.py`, used by the commands, the server, the Desktop plugin and the terminal panel: an agent is `[a-z][a-z0-9_-]{0,63}`, a skill `[a-z][a-z0-9-]{0,63}`, a flow id `[a-z0-9][a-z0-9-]{0,79}`, and a flow name (also its command name) 2 to 32 lowercase letters, digits and `-`.

Keys in the live view: `1` agents, `2` tasks, `3` loops, `4` events, `5` issues, `0` dashboard, `j`/`k` or arrows to scroll, `?` help, `q` quit.

## Where things live

- `agentgraph/contract.py`: the data model and the graph validator.
- `agentgraph/sources.py`: reads the agent config, the graph files and OpenCode's session database.
- `agentgraph/view.py`, `viewstate.py`, `__main__.py`: the view, its interactive state, and the CLI.
- `agentgraph/check.py`, `ledger.py`: the `check` command and the ledger commands.
- `agentgraph/outputs.py`: a step's output read and edited by hand (`agentgraph output`, `/api/output`, the panel's `output` method).
- `agentgraph/snapshot_json.py`, `server.py`, `web/`: the JSON document, the local web server and the graph page.
- `desktop-plugin/`: the OpenCode Desktop side panel.
- `GRAPH_FORMAT.md`: the task-graph file format (`.opencode/graph/<task>.json` in each project). `WEB_API.md`: the contract between the data, the page and the plugin.
- `memory/`: the run ledger (`runs.jsonl`, `feedback.jsonl`, `improvements.jsonl`), the edit log of outputs edited by hand (`edits.jsonl`), and the cases (`cases.jsonl`) with the trials of edits on them (`replays.jsonl`).
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
