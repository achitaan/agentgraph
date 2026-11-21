# Loops: an agent that repeats until a check or a judge passes

A drawn flow used to be only a sentence for the orchestrator model. With a saved loop it becomes something code runs: a small
controller program (not a model) starts the agent for one attempt in a real OpenCode session, runs **your check command
itself** and, in a loop between agents, asks the **judges** you drew, and sends what failed back to the worker, until
everything passes or a limit stops it. The agent's own "done" or "PASS" never ends a run: only the check's exit code and a
judge's verdict line do.

## Which paths run

| drawn path | runs as |
|---|---|
| `A -> A` | the agent works, your check decides (release 1, unchanged) |
| `A -> J1 -> ... -> Jk -> A` | `A` works; the check runs (when a command is saved); `J1` ... `Jk` judge in the drawn order; a failure goes back to `A` |

Each agent appears once in the loop. Anything else is refused before a run exists: a path that does not return to its first agent
("this path does not loop back to its first agent"), an agent drawn twice, an agent that is not in the structure, a loop with neither
a check command nor a judge. Parallel branches, resume, pause and trials are not built yet.

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
| `limits.attempts` | required, 1 to 50. Counts worker turns |
| `limits.attempt_seconds` | an agent turn running longer is stopped |
| `limits.seconds` | no agent turn (worker or judge) starts after this much time since the run began; waiting for a person counts |
| `limits.cost` | no agent turn starts once the known spend reaches this many dollars |
| `session` | `reuse` (default: every attempt in one session, so the agent remembers) or `fresh` (a new session each time) |
| `allow_asks` | `true` passes `--auto`: actions that would ask for permission are allowed. Default: they are refused, as in any headless run |
| `goal` | optional default goal text |
| `steps` | per agent on the path, see below. Not for `A -> A` |
| `artifacts` | up to 20 file paths relative to the work folder, copied after each worker turn. Not for `A -> A` |
| `approval` | `before_pass`: a person approves between the last pass and `passed`. Not for `A -> A` |

`steps`, `artifacts` and `approval` are set with `--steps-file F` (a JSON file), repeatable `--artifact NAME` (replaces the list),
`--no-artifacts` and `--approval before_pass|none`. A change keeps everything else. `set` refuses what a run would refuse and
saves nothing.

### `steps`

```json
{"reviewer": {"session": "fresh", "retries": 1, "handoff": "none", "on": {"fail": "worker"}},
 "worker":   {"session": "reuse", "retries": 0, "escalate": {"after": 2, "to": "hard"}},
 "check":    {"on": {"fail": "worker"}}}
```

Keys are agent names on the path, or `check`. Anything else, and any unknown key, is refused with the reason.

| key | meaning |
|---|---|
| `session` | `reuse` or `fresh`. Default: the worker follows the top-level `session`; a judge is `fresh`: a new session id every turn |
| `retries` | 0 to 3 extra turns when the step fails to give a result (a turn that errors, a judge reply with no verdict line). Default 0 for the worker, 1 for a judge. A retry is another turn, so cost and time limits apply |
| `handoff` | judges only: `none` (default) or `report` (the worker's last reply is added, labelled as a claim, not as evidence) |
| `on` | routes per outcome, see the table. Values: `next`, `done` (only on `pass` of the last step), `stop`, `human`, or the worker's name |
| `escalate` | worker only: `{"after": N, "to": AGENT}`. From attempt N+1 the work step is run by `AGENT` (in the structure, not on the path) in a new session; the record says `escalated_to` |

### Outcomes and routes

| step | outcome | default route |
|---|---|---|
| work | `returned`, `stalled` (stopped at `attempt_seconds`) | next step |
| work | `agent_error` | stop |
| work | `no_artifact` (a listed file is missing) | back to work with the names; no check, no judge |
| check | `pass` | next step, or done when there is no judge |
| check | `fail` | work, with the exit code and the end of the output |
| check | `inconclusive`, `check_error` | stop |
| judge | `pass` | next judge, or done after the last |
| judge | `fail` | work, with the findings |
| judge | `inconclusive` (no verdict line), `agent_error`, `stalled` | stop, after its retries |

Refused: an unknown outcome name, an unknown target, `done` from a step that is not the last or on anything but `pass`, a route
to a later step ("would skip ..."), a route to a judge (work goes back to the worker only), a route to an agent not on the path,
and `human` on a failing check (approving would carry on past a check that did not pass).

### The verdict line

A judge ends its reply with a last non-empty line that is exactly `VERDICT: PASS` or `VERDICT: FAIL`. The text above it is the
findings. A missing or different line is `inconclusive`: the judge is asked again (its retries), then the run stops. It is never
read as a pass, and no word elsewhere in the reply counts.

### What a judge is given

A fresh session and one message: the goal in full, the check result (exit code), the paths of the artifact copies, the work folder,
"you did not do this work; judge it", the list of judges, and the verdict-line rule. It is **not** given the worker's reply or session,
unless `handoff` is `report`. Text that came from another agent is marked as data to act on, not as instructions that change the goal.
The worker's message after a failure says who failed it, the evidence, and "make one attempt and return"; it also holds the goal in full.

### Approval and waiting

With `approval: before_pass` or a route to `human`, the run goes to status `waiting` (no model call, no cost) when the step is reached;
`run.json` has `waiting: {step, outcome, since, question}`. Answer from the same project folder:

```sh
agentgraph loop answer RUN approve [--note T]     # follow the route pass would have taken (with approval: finish as passed)
agentgraph loop answer RUN reject --note "why"    # back to the worker, the note is its feedback; a new attempt, limits apply
agentgraph loop answer RUN stop                   # the run ends cancelled
```

The answer is written to `answer.json` (atomically), the controller reads it, files it as `attempt-N/answer-K.json` and lists it in
`run.answers`. An answer to a run that is not waiting, or a second one before the first is taken, is refused. Waiting counts toward
`limits.seconds` (the run ends `limit` with a reason that says it was waiting); `loop cancel` works while waiting. A person who approves a
failed judge's step is overriding that judge; the other steps still run, and the check, if saved, must still have exited 0.

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
- `attempt-N/message.md` (what the worker was sent) and `attempt-N/check.out` (the check's full output, up to 1 MB).
- In a loop between agents also, per step: `attempt-N/<step>.message.md` (what a judge was sent; the step id with `:` as `-`, e.g.
  `judge-reviewer`), `<step>.reply.md`, `<step>.findings.md` (a judge's findings after FAIL), a second try adds `.t2`; `attempt-N/artifacts/<name>`
  (copies); `attempt-N/answer-K.json`.
- `run.json` there also holds, per attempt, `steps[]` (kind, agent, session, fresh, try, seconds, cost, tokens, exit, outcome, route, message,
  reply, verdict, findings, error), `artifacts[]` (`name`, `revision`, `sha256`, `bytes`), `escalated_to` and `feedback_from` (`check`, a judge's name,
  `artifacts` or `human`); and per run `handoffs[]` (`attempt`, `from`, `to`, `kind` findings/artifact/report/check, `file`, `sha256`), `passed_by`,
  `waiting` and `answers[]`. `attempt.agent` is the worker's last try and `attempt.check` the check, as in `A -> A`.
- `run.log` (the detached process), `lock`, and `cancel` when you asked for one.

## What the statuses mean

- `passed`: in one attempt, the check (when one is saved) exited 0 and every judge answered `VERDICT: PASS` (or a person approved over a judge),
  and, with `approval`, a person approved. The run says how in `passed_by`: `check`, `judges` or `check+judges`, plus `+approval`. Nothing else
  gives this; a judge that passes while the saved check failed was never asked, and a definition edited by hand to skip a step still cannot pass.
- `failed`: every attempt (worker turn) was used and the last check or judge failed.
- `limit`: stopped before an agent turn because the time or the spend limit was reached, or while waiting for a person (the reason says which).
- `stalled`: the last agent turn ran past `attempt_seconds` and was stopped, and no check passed.
- `agent_error`: the agent process failed (no model, bad agent, crash). The run stops unless `retries` allow another turn.
- `check_error`: the check command could not run (not found, not executable, killed by a signal).
- `inconclusive`: the check exited with one of your `inconclusive_codes` or timed out, or a judge gave no verdict line after its retries. The run stops; it does not guess.
- `waiting`: a person must answer (`agentgraph loop answer`). Not final.
- `invalid`: refused before any attempt (no check command, no goal, unreadable settings, no OpenCode).
- `cancelled`: you asked it to stop (or answered `stop`). A cancel file stops it before the next turn, or stops a running turn at once.
- `interrupted` (shown, not stored): the record says `running` but the process is gone. There is no resume yet.

## The spend limit: what it does and does not promise

The limit is checked before each agent turn (a judge's too), against the cost the turns so far are known to have billed (read from the
OpenCode database, including any subagents the agent started). It stops the **next** turn. It does **not** cap one running
turn: an attempt that starts under the limit can bill more than what is left, up to what `attempt_seconds` allows. When a
cost cannot be read it is shown as unknown and is not counted as zero or as spent, so with no readable cost only the attempt
and time limits protect you.

## Known limits

- Loops are `A -> A` or `A -> J1 -> ... -> Jk -> A`; no parallel branches, no cursor or step-by-step control, no trial runs.
- A judge is a model. A `VERDICT: PASS` means the model said so after looking, not that the work is right; the saved check is the only
  part that is a program. A weak or lenient judge passes weak work. Use a check for what can be checked and judges for the rest.
- A fresh judge does not see the worker's session, but it works in the same folder and can read everything there, including notes the
  worker left; and with `handoff: report` it is told what the worker claimed. Nothing stops a judge from being influenced by the files.
- `no_artifact` only proves that a file exists inside the work folder after the turn, not that it is right; the copies are the files as
  they were then, and the hash says so.
- Approval is a person's answer; `approve` over a failed judge is that person's override and is recorded (`passed_by` has `+approval`).
- No resume after a crash or reboot, and no pause.
- Each attempt starts a private OpenCode server (about 3 seconds).
- A turn that is stopped leaves its killed command in the session's memory; the next message says so, but the agent may try
  the same command again.
- With `allow_asks` the agent can do anything its agent file does not deny: run it in a folder you can afford to lose.
- A stopped controller (killed, machine off) leaves its agent session in OpenCode; the session id is in `run.json`.
