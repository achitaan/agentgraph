# Loops: an agent that repeats until a check passes

A drawn flow `A -> A` used to be only a sentence for the orchestrator model. With a saved loop it becomes something code
runs: a small controller program (not a model) starts the agent for one attempt in a real OpenCode session, runs **your
check command itself**, and if the check fails it sends the result back into the **same session**, until the check passes or
a limit stops it. The agent's own "done" or "PASS" never ends a run; only the check's exit code does.

A flow without a saved loop behaves exactly as before.

## Save a loop

```sh
agentgraph loop set my-flow --check "sh check.sh" --attempts 5 --attempt-seconds 900 --seconds 3600 --cost 2.0
agentgraph loop show my-flow        # one line; --json for the file
agentgraph loop unset my-flow       # removes the settings; runs already made are not touched
```

The settings are one file, `structure/.loops/<flow id>.json` (in your personal folder when you have one). Only keys the tool
knows are accepted, so a typo is an error, not a silent change.

| setting | meaning |
|---|---|
| `check.command` | run by the controller with `sh -c` in the work folder. Exit 0 is the only pass |
| `check.timeout` | seconds before the check is stopped (default 300) |
| `check.inconclusive_codes` | exit codes that mean "could not tell" (default `[75]`) |
| `limits.attempts` | required, 1 to 50 |
| `limits.attempt_seconds` | an agent turn running longer is stopped |
| `limits.seconds` | no new attempt after this much time since the run began |
| `limits.cost` | no new attempt once the known spend reaches this many dollars |
| `session` | `reuse` (default: every attempt in one session, so the agent remembers) or `fresh` (a new session each time) |
| `allow_asks` | `true` passes `--auto`: actions that would ask for permission are allowed. Default: they are refused, as in any headless run |
| `goal` | optional default goal text |

Only a loop of one agent on itself works now. Other paths are refused with "loops between several agents arrive in the next
release".

## Run one

```sh
agentgraph loop start my-flow --goal-file goal.md --workdir ~/project/app           # starts detached, prints the run id
agentgraph loop start my-flow --goal-file goal.md --wait                            # runs here and prints the result
agentgraph loop status                  # the newest run; or: status RUN [--json]
agentgraph loop list
agentgraph loop cancel RUN
```

`--project DIR` is where the record lives (default: the current folder); `--workdir` is where the agent and the check run
(default: the project). `--structure DIR` picks another structure folder. To try a loop without touching your own OpenCode
config, make a scratch copy with `agentgraph bench scratch-config --to DIR` and add `--scratch DIR`.

A run is its own process: closing the page or the terminal does not stop it. The goal is sent in full with every attempt
(it must survive the session's compaction); after a failed check the message also holds the exit code and the last 60 lines
of the output.

## The record

Each run keeps a folder `<project>/.opencode/loops/<run id>/`:

- `definition.json`: the settings, the steps, the goal and the work folder as they were at the start. Editing or deleting the
  saved loop later never changes a run, running or finished.
- `run.json`: the state after every step (status, reason, session ids, each attempt with its check exit code, output file and
  hash, cost, tokens).
- `events.jsonl`: one line per step.
- `attempt-N/message.md` (what the agent was sent) and `attempt-N/check.out` (the check's full output, up to 1 MB).
- `run.log` (the detached process), `lock`, and `cancel` when you asked for one.

## What the statuses mean

- `passed`: the check exited 0 after an attempt. Nothing else gives this.
- `failed`: every attempt was used and the last check failed.
- `limit`: stopped before an attempt because the time or the spend limit was reached (the reason says which).
- `stalled`: the last agent turn ran past `attempt_seconds` and was stopped, and no check passed.
- `agent_error`: the agent process failed (no model, bad agent, crash). The run stops; it is not retried.
- `check_error`: the check command could not run (not found, not executable, killed by a signal).
- `inconclusive`: the check exited with one of your `inconclusive_codes` or timed out. The run stops; it does not guess.
- `invalid`: refused before any attempt (no check command, no goal, unreadable settings, no OpenCode).
- `cancelled`: you asked it to stop. A cancel file stops it before the next attempt, or stops a running turn at once.
- `interrupted` (shown, not stored): the record says `running` but the process is gone. There is no resume yet.

## The spend limit: what it does and does not promise

The limit is checked before each new attempt, against the cost the attempts so far are known to have billed (read from the
OpenCode database, including any subagents the agent started). It stops the **next** attempt. It does **not** cap one running
attempt: an attempt that starts under the limit can bill more than what is left, up to what `attempt_seconds` allows. When a
cost cannot be read it is shown as unknown and is not counted as zero or as spent, so with no readable cost only the attempt
and time limits protect you.

## Known limits

- One agent on itself only; no verifier, human gate or escalation between agents yet.
- No resume after a crash or reboot, and no pause.
- Each attempt starts a private OpenCode server (about 3 seconds).
- A turn that is stopped leaves its killed command in the session's memory; the next message says so, but the agent may try
  the same command again.
- With `allow_asks` the agent can do anything its agent file does not deny: run it in a folder you can afford to lose.
- A stopped controller (killed, machine off) leaves its agent session in OpenCode; the session id is in `run.json`.
