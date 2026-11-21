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
a check command nor a judge. Parallel branches are not built.

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
agentgraph loop cancel RUN              # a live controller is asked; with none (interrupted, paused, waiting) the run is marked cancelled
agentgraph loop pause RUN               # after the step it is on (never cuts a turn short); the run then says paused
agentgraph loop resume RUN [--redo | --accept] [--wait]
agentgraph loop answer RUN approve|reject|stop [--note T]   # also for a waiting run that has no controller; resume picks the answer up
agentgraph loop start my-flow --goal-file goal.md --settings-file trial.json   # a trial: these settings for this run only
agentgraph loop versions my-flow        # the kept earlier versions of the saved loop, newest first
agentgraph loop restore my-flow VERSION # put one back (what it replaces is kept too)
```

`--project DIR` is where the record lives (default: the current folder); `--workdir` is where the agent and the check run
(default: the project). `--structure DIR` picks another structure folder. To try a loop without touching your own OpenCode
config, make a scratch copy with `agentgraph bench scratch-config --to DIR` and add `--scratch DIR`.

A run is its own process: closing the page or the terminal does not stop it. The goal is sent in full with every attempt
(it must survive the session's compaction); after a failed check the message also holds the exit code and the last 60 lines
of the output.

## Resume, pause, cancel, answer

A controller can die (SIGKILL, a reboot, a crash). The run then says `interrupted`, `agentgraph loop status RUN` says what to do, and
`agentgraph loop resume RUN` starts a new controller that goes on **from the cursor** in `run.json` using the pinned `definition.json`
(never the saved loop, which may have changed). The cursor is written before a message is sent (`phase: started`, with the session and
the sha256 of the message) and after the step is recorded (`phase: done`, with the route taken). Nothing that is recorded runs again.

- A step whose cursor is `done` continues at its route. A check step whose cursor is `started` runs the check again (see below).
- An agent step whose cursor is `started` may or may not have been sent. `resume` asks the executor to **reconcile** and acts on one of three answers:
  - `not_sent`: the session does not exist (and the cursor does not say it held user messages before the send), or it holds no user message since the send
    (the cursor records the count of user messages before the send; the clock decides nothing). A missing session that held messages before the send, a session
    with fewer messages than the count, and a cursor with no count when the session exists are `unknown`. The message is sent.
    A new user message that does not have this text, or two new messages with it, is `unknown`: someone else wrote into the session, or two tries cannot be told apart.
    A retry of a turn is told from its earlier try by that count, so a finished reply of try 1 is never adopted for try 2.
  - `finished`: exactly one new user message has this text and the session is idle after a reply (an idle row that says `succeeded` after an assistant reply, no live
    process for the session, every sub-session idle). A judge's verdict is read from the turn's last assistant message only (none with text: `inconclusive`). The reply is adopted as the step's result (`recovered: true`, cost read from the session) and the walk goes on.
  - `unknown`: anything else (still busy, a live process, the database unreadable, no idle row). The run becomes `paused` with the reason and **nothing is sent**.
- A paused step is the person's to decide: `resume --redo` sends the message again, `resume --accept` takes the step as returned and goes to the check.
  Both are refused (and change nothing) for a step that may not have run, a judge's turn cannot be accepted (no verdict to read), and any other
  word is refused. Each decision is in `run.json` `recoveries[]` (`answer`, `action`: `adopt`, `redo`, `accept`, `pause`, `send`).
- `resume` is refused (nothing written) for a final run, a queued run younger than 30 seconds (its controller may still be starting) and a run whose controller is alive.
  It is also refused when `settings_sha256` or `steps_sha256` no longer matches both `definition.json` and the copy in `run.json` (the same check runs when a controller starts), in words, and the run is left as it was.
  A run with no pin in either file (made before `run.json` kept `steps_sha256`) is refused in words that say it has no pin: refusing is the safe default.
  The pins catch an accidental change to the definition; they do not stop a person who edits both files on purpose.
  A `resume` of a run whose lock a controller holds is refused at once, without waiting for the lock.
- A run still `queued` 30 seconds after it was created with no controller holding its lock is shown `interrupted` (the controller never started, nothing was sent):
  `resume` starts it (`--redo` and `--accept` are refused: there is no step to redo) and `cancel` marks it `cancelled`. A detached `run` that cannot start the
  controller (no OpenCode, unreadable or changed definition) writes a final status with the reason in words instead of leaving `queued`, under the run's lock. A lock that is held
  means a controller exists and owns the record, so a `run` that finds it busy writes nothing.
- Looking does not touch a run: `list` and `status` only probe the lock read-only (a missing lock file means not held), create no file and do not follow a symlinked run folder.
  A probe never keeps a controller from taking its lock, and a controller waits up to a second for it.
- `cancel` while a check is running stops the check's process group within a quarter second and the run ends `cancelled`; a `pause` lets the check finish.
- `answer` is decided and written under `answer.lock` (a `flock`, so a stale file wedges nothing): a run that is no longer `waiting` refuses it, and an answer taken
  by a controller that is killed before it saved is not lost. A `cancel` with no controller and a controller ending the run take the same lock, so an answer is
  either decided before the run ends or is refused; one that lost is removed, and `answer.json` is never left in a run that is no longer `waiting`.
- A `cancel` answered "requested" always comes true: a controller that is pausing when it is asked ends the run `cancelled` instead of `paused`, and a `cancel` file that
  reached a run that had just paused ends its next `resume` `cancelled` before any step is sent.
- `pause` is for a run a controller is running: it writes a `pause` file and the controller stops after the step it is on (the run says `paused`,
  `resume` goes on at the next step). `cancel` and `answer` act on a paused, waiting or interrupted run with no controller, under the run's lock.
- `list` and `status` say it in words: `interrupted (the controller is gone; agentgraph loop resume RUN)`, `paused`, `waiting`, `trial`.

## Cost, history and trial runs

- Each turn has a `cost` (null when it cannot be read). `run.session_costs` is the cost per session (null if any turn of that session is unpriced),
  `totals.cost` the sum of the known costs (null when none is known) and `totals.cost_unknown_steps` how many agent turns have no known cost.
  With `limits.cost` set, an unpriced earlier turn means the spend is unknown, so no further agent turn starts: the run ends `limit` with
  "the spend of attempt N is unknown, so the spend limit cannot be checked". Without `limits.cost` an unknown cost stops nothing.
- Every save that replaces a saved loop, and every remove, first keeps the old file as `<structure>/.loops/.history/<flow>/<UTC time>-<sha8>.json`
  (at most 50, the oldest go first). `loop versions` lists them, `loop restore` puts one back byte for byte and keeps what it replaced. A bad or traversing version id is refused.
- `loop start --settings-file F` (or `{"op": "start", "settings": {...}}`) is a trial run: the settings (checked like a saved loop) are pinned in the run, the saved loop is neither read
  nor changed, and `run.json` says `trial: true`. Every run, trial or not, records `settings_sha256` (the sha256 of the pinned settings as JSON).

## What this does and does not guarantee

- The spend limit is an **admission** limit, not a billing cap: it is checked before a turn starts. A run can end above the limit by one turn.
- A **check may run twice** after a restart: a check whose cursor is `started` is run again (a check is meant to be repeatable), and a kill between the check
  finishing and its record being written loses that record.
- There is **no exactly-once**. The agent turn is protected by reconcile (a turn that cannot be told apart from one still running is never sent again unless you say `--redo`),
  but a message can still have run in a session that reconcile could not see; a person's `--redo` can run a turn twice, a `--accept` can take a turn that did not finish.
- Reconcile reads OpenCode 2.3.2's database and processes. A different OpenCode may store things differently; then the answer is `unknown` and the run pauses.
- A real **compaction was not provoked** in tests: whether a resumed session that was compacted after the kill still holds the goal is not proven. The goal is sent in full
  with every message, so the message itself is complete.

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
- `run.log` (the detached process), `lock`, `cancel` or `pause` when you asked for one, and `answer.json` for an answer not yet taken.

## What the statuses mean

- `passed`: in one attempt, the check (when one is saved) exited 0 and every judge answered `VERDICT: PASS` (or a person approved over a judge),
  and, with `approval`, a person approved. The run says how in `passed_by`: `check`, `judges` or `check+judges`, plus `+approval`. Nothing else
  gives this; a judge that passes while the saved check failed was never asked, and a definition edited by hand to skip a step still cannot pass.
- `failed`: every attempt (worker turn) was used and the last check or judge failed; also a definition that routed the run onward to its end after a check that exited non-zero or a judge that said FAIL.
- `limit`: stopped before an agent turn because the time or the spend limit was reached, or while waiting for a person (the reason says which).
- `stalled`: the last agent turn ran past `attempt_seconds` and was stopped, and no check passed.
- `agent_error`: the agent process failed (no model, bad agent, crash). The run stops unless `retries` allow another turn.
- `check_error`: the check command could not run (not found, not executable, killed by a signal).
- `inconclusive`: the check exited with one of your `inconclusive_codes` or timed out, or a judge gave no verdict line after its retries. The run stops; it does not guess.
  A judge's verdict is read from the turn's last assistant message (the last line of it); a `VERDICT: PASS` in an earlier message does not count, and a last message with no text is `inconclusive` (the judge is asked again).
- `waiting`: a person must answer (`agentgraph loop answer`). Not final.
- `paused`: the run stopped on request (`loop pause`) or because a step's fate could not be told after a restart (the reason says which). Not final; `loop resume`.
- `invalid`: refused before any attempt (no check command, no goal, unreadable settings, no OpenCode).
- `cancelled`: you asked it to stop (or answered `stop`). A cancel file stops it before the next turn, or stops a running turn at once.
- `interrupted` (shown, not stored): the record says `running` but the process is gone, or it says `queued` for 30 seconds with no controller. `agentgraph loop resume RUN` goes on from the cursor.

## The run clock

`totals.seconds` (and `limits.seconds`) counts the time a controller was alive and running the run: the walk, a turn, a check and the wait for an answer. It is saved at
the start and end of every step and every 5 seconds while a turn or a check runs, so a killed controller loses at most one heartbeat (5 s) plus a check's quarter-second
poll. Time with no controller (between a kill and a `resume`, a paused run, a queued run waiting for its controller) is **not** counted; `resume` continues from the saved value.

## The spend limit: what it does and does not promise

The limit is checked before each agent turn (a judge's too), against the cost the turns so far are known to have billed (read from the
OpenCode database, including any subagents the agent started). It stops the **next** turn. It does **not** cap one running
turn: an attempt that starts under the limit can bill more than what is left, up to what `attempt_seconds` allows. When a
cost cannot be read it is shown as unknown and is not counted as zero or as spent; with a cost limit set, an unknown spend ends the run (no
turn starts when the limit cannot be checked), and without one only the attempt and time limits apply.

## Known limits

- Loops are `A -> A` or `A -> J1 -> ... -> Jk -> A`; no parallel branches.
- A judge is a model. A `VERDICT: PASS` means the model said so after looking, not that the work is right; the saved check is the only
  part that is a program. A weak or lenient judge passes weak work. Use a check for what can be checked and judges for the rest.
- A fresh judge does not see the worker's session, but it works in the same folder and can read everything there, including notes the
  worker left; and with `handoff: report` it is told what the worker claimed. Nothing stops a judge from being influenced by the files.
- `no_artifact` only proves that a file exists inside the work folder after the turn, not that it is right; the copies are the files as
  they were then, and the hash says so.
- Approval is a person's answer; `approve` over a failed judge is that person's override and is recorded (`passed_by` has `+approval`).
- Resume goes on from the cursor, not from inside a turn: see "What this does and does not guarantee".
- Each attempt starts a private OpenCode server (about 3 seconds).
- A turn that is stopped leaves its killed command in the session's memory; the next message says so, but the agent may try
  the same command again.
- With `allow_asks` the agent can do anything its agent file does not deny: run it in a folder you can afford to lose.
- A stopped controller (killed, machine off) leaves its agent session in OpenCode; the session id is in `run.json`.
