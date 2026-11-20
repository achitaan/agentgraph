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

## Paths as commands

`agentgraph flow command ID --on` (or the graph view) makes a named path the command `/NAME`: it writes `NAME.md` into `~/.config/opencode/commands/` (`--commands DIR` for another folder). The file starts with the marker line `# agentgraph: flow command ...` in its frontmatter, sets only `description`, and its body is the path's run message (`agentgraph flow prompt`) with `$ARGUMENTS` as the goal, so `/NAME add a health endpoint` asks the orchestrator to run the path on that goal in the current session. Renaming the path renames the file, changing its instruction rewrites it, `--off` or removing the path deletes it, and undoing any of these writes the same bytes again. A file without the marker, or one whose bytes were changed by hand, is never replaced or removed. A file an earlier version of the toolkit wrote for the path (its template then: `agentgraph/flowearlier.py`) counts as the toolkit's: it is rewritten in the current shape, renamed with the path, and removed with `--off` or the path, as one this version wrote; turning the command `--on` again rewrites it. **What a command file cannot hold:** the run message from the graph view, the terminal panel or `flow prompt` names the other drawn paths that bind this run and the nodes to make check nodes (`GRAPH_FORMAT.md`); a command file is written once, and drawn paths change after it, so it holds no such list. Its body states the general rule (every other drawn path applies too, to each node of its first agent) and says to run `agentgraph flow prompt --name NAME --goal-file -` to see which apply now. Removing a path that can no longer be read (an agent on it is gone) deletes its command file too, under the same rule; `agentgraph structure flows` lists such paths under `unreadable`. The command's name is the path's name, so it follows `agentgraph/names.py` (`COMMAND_PATTERN`); a file name outside it is never read or written as a path's command.

Refused names: OpenCode's own commands (2.3.2: `init`, `review`, `goal`, the Desktop and terminal slash commands such as `reload`, `undo`, `new`, `compact`), the oh-my-opencode-slim plugin's (`deepwork`, `reflect`, `loop`, `interview`, `implement`), the seven toolkit commands above, and any existing file the toolkit did not write. An instruction holding `$ARGUMENTS`, `$1`…`$9` or a `` !` `` shell block is refused for a command, because OpenCode would replace or run it.

Measured on a scratch OpenCode 2.3.2 server with the plugin loaded (`.opencode/artifacts/agentgraph-desktop/n96/r1/command-proof.md`): a new command file is **not** live at once, despite the documentation quoted below; it appears after the configuration is reloaded (Desktop: `/reload`) or after a restart. The answer to `--on` says so (`applies`, `live: false`). On the same server, with a stand-in model that only recorded what it was sent (`n96/r2/at-reference-proof.md`): an `@path` file reference in a command's text is sent as plain text, never attached, inside a fenced block or not; so an instruction may hold one. **What a user types after `/NAME` is their own typing, and OpenCode treats it as it treats the text of any command: it runs a `` !`...` `` in it as a shell command, outside the agents' permissions, even inside the goal's fenced block. The toolkit never sees that text before OpenCode does and cannot check it; the command file it writes holds no such form.**

**Stale changes.** A path's `rev` (16 hex digits) is computed from its flow file's bytes; `flow command ID --on|--off`, `flow name`, `flow text`, `flow prompt` and `flow remove` take `--expect REV` and change nothing when the path changed since (`... changed since you opened it; reload and try again (rev now X)`, exit 1). An empty `--expect` is bad input, never taken as no `--expect`. A path removed and drawn again with identical bytes has the same `rev`: it is the same path, so a change expecting that `rev` is accepted.

## Agents

`agentgraph agent add NAME` with no other option adds a blank agent (description "An agent you added. Open it to say what it does.", an empty instruction, the default verifier's model, writer permissions). `--template verifier` or `--copy-of AGENT` start from the verifier or a copy instead; `--model`, `--variant`, `--description`, `--prompt-file`, `--skills` and `--read-only/--no-read-only` replace a default. `agentgraph agent rename NAME TO` renames an added agent with its flows, groups and path commands in one undoable step; `agentgraph agent remove NAME` is refused while a path through the agent is a command (turn it off first). `agent text|rename|remove`, `skills add|remove|all`, `skill write|delete` and `team members|text|remove` take `--expect REV` as the path commands do. Every option of these commands is listed in `WEB_API.md`, "2. The commands".

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
