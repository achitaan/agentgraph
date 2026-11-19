# Orchestration slash commands

Global command files live in `~/.config/opencode/commands/`; the toolkit's copies are in `orchestration/commands/`. `install.sh` and `sync.sh` copy only the seven commands on the shared toolkit allowlist. Neither deletes commands or copies personal commands. Both back up replaced command files and refuse symlink paths. `sh sync.sh --commands-and-learning` limits sync to the toolkit commands and the two improvement skills. `/learn` does not run full sync: it mirrors only the exact approved changes.

| Command | What it does | Call behind it |
|---|---|---|
| `/note <text>` | Save the exact note in the active preset's ledger, with an agent tag if clear. | `agentgraph feedback --home H --text-file F [--agent NAME]` |
| `/learn [agent]` | Review all pending notes and recent chat excerpts, or just notes tagged with one agent. Propose up to eight lasting edits and apply only approved ones. | `agentgraph signals --home H --json --feedback-ids --no-cost` (add `--messages` only without an agent); `agentgraph improved --home H --keep-watermark --json-file F` for each applied edit; `agentgraph improved --home H --handled-file F` for settled items and any chat checkpoint |
| `/flows` | List drawn flows with their stored instructions. | `agentgraph structure flows --json` |
| `/flow A B [C…] [: instruction]` | Add one flow with an optional instruction. | `agentgraph structure flow add A B [C…] [--text-file F] --json` |
| `/agent-skills AGENT [add SKILL \| remove SKILL \| all]` | Show or change an agent's skills; changes apply on restart. | `agentgraph skills show AGENT`, `agentgraph skills add AGENT SKILL`, `agentgraph skills remove AGENT SKILL`, or `agentgraph skills all AGENT` |
| `/spend` | Summarize the cost trend in at most three lines. | `agentgraph cost --trend` |
| `/graph-check [task]` | Validate this project's graphs and report unfinished work. | `agentgraph check [--task NAME]` |

`H` is the active preset's ledger home; `F` is a temporary file. For `/learn`, `--feedback-ids` exposes the IDs used in the `{ "id": "reason" }` marking file. Marks are append-only in `H/feedback-handled.jsonl`; they do not rewrite notes, advance the watermark, or erase history. Recording an approved batch edit keeps the existing watermark, so notes not settled by that run remain pending.

`--messages` reads the OpenCode V2 database in read-only mode. It returns up to 150 root-chat user messages, with at most 600 characters per excerpt and 30,000 characters of excerpt text total, plus a count of eligible messages omitted. It prefers original display text when available and excludes identifiable slash commands. A template saved as ordinary user text without presentation metadata cannot reliably be distinguished. A batch checkpoint advances separately from the single-proposal watermark; capped scans keep their backlog.

## OpenCode V2 command format

[OpenCode Commands](https://opencode.ai/docs/commands), read by the orchestrator on 2026-11-19 via `https://opencode.ai/v2/docs/commands`:

> Put global commands in `~/.config/opencode/commands/` and project commands in `.opencode/commands/`.
>
> Only `.md` files are discovered. Nested paths become command names with `/` separators.
>
> The trimmed Markdown body is always the prompt template.
>
> Do not put `template` in Markdown frontmatter.
>
> Use `$ARGUMENTS` for the complete argument string exactly as entered.
>
> Do not place untrusted arguments inside shell blocks.
>
> Later sources replace earlier commands with the same name.
>
> A custom definition can also replace an earlier built-in command.
>
> OpenCode reloads command files and configuration changes automatically.

The page calls `description` “Text shown in command lists and discovery” and says commands “run in the current session by default.” It documents `agent` for selecting another agent, `model` for selecting a model, `subagent: true` for a background child session (`false` forces the current session), and `subtask` as a deprecated alias. These files set only `description`, keep the current agent and session, and never use pre-submit shell blocks, which run “outside the agent's tool permission flow.”

## Not verified in the app

Static and ledger tests do not verify live discovery, built-in or project-name collisions, runtime argument expansion or hot reload, tool permissions, or the `/learn` approval dialog. The OpenCode app and CLI were not started.
