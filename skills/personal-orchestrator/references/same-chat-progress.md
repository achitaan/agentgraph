# Same-chat progress

> OpenCode note: this file describes the Codex toolkit's separate-session controller and its "spawn only when asked" stance. In this setup, follow `AGENTS.md` and the orchestrator prompt instead (task graph, loop items, verifier and reviewer); read this file as background only.

Use this when the user asks for durable loop tracking and visible progress in the
current chat. The existing controller's `progress` commands store a local ledger
and render `PROGRESS.md`. They do not launch models, agents, shell commands or
background jobs. Ordinary small tasks do not need a ledger.

## What the user sees

Keep short milestone updates in this chat: phase, pass number, useful finding,
check result and next action or blocker. Open the absolute `PROGRESS.md` path
with the host's `open_in_codex` file tool when available. Refresh `progress status`
and reopen the file after meaningful changes; do not assume the viewer updates
automatically. The file shows a snapshot timestamp, not a running countdown.
If the host cannot display it, provide a clickable local link and the same chat
updates. This is a file panel, not a new native progress widget.

An explicitly requested [native Codex Goal](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)
can own continuation in this chat. Do not create a Goal from an ordinary task
request. The tracker does not create, pause or resume Goals; those are separate
host actions. Do not nest a Goal or Ralph around separate-session dispatch.

## Start once, then retain the ledger

Use the actual current chat identifier provided by the host; never invent one.
Choose a private state directory under an existing, user-owned parent. Local
ignored `.work/` is suitable for direct work; keep state outside any delegated
worker workspace. Declare the deliverable and all affected source, tests and
consumer files to protect. Paths are relative to the workspace; declared files
may be created later. The helper checks only that declared scope, so inspect the
final diff for additional changed files. There is no automatic scope expansion.
If the task genuinely grows, stop and preserve the old ledger before explicitly
revising the task and remaining allowance; do not reset it to evade a limit.

Example invocation from the copied skill directory (replace the placeholders):

```sh
python -B scripts/controller.py progress init \
  --state /absolute/project/.work/task-progress \
  --workspace /absolute/project --thread HOST_CHAT_ID \
  --objective 'Implement and verify the agreed change' --profile Engineer \
  --artifact src/feature.py --artifact tests/test_feature.py \
  --max-passes 3 --minutes 20 --review-mode self
```

Initialize before the first tracked pass. If tracking starts later, state that;
do not fabricate prior steps. `self` means the current chat reviews its own work.
Use `independent` only when separate review is explicitly required and authorized.
An independent reviewer must have a distinct, actual identity and must not be a
recorded contributor. Never invent an actor to satisfy the gate. Selecting a
profile, loop mode or progress tracking does not authorize spawning anything.

## Each step

Every command takes the same `--state` and `--thread` arguments. Use stable,
unique step IDs. Begin before doing work; record real evidence afterward:

Add `--compact` to any progress action for routine use. It prints concise JSON
with the active step, limits, current checks/review, view path and any view-write
warning. Full ledger records, validation and `PROGRESS.md` are unchanged. The
compact time display rounds to tenths of a second; deadline enforcement uses the
original precision and the explicit `deadline_exceeded` field. Omit the flag
for full status, including the active source snapshot. Errors retain
their diagnostic message and nonzero exit status in either format. The option
reduces printed bytes; it does not measure or guarantee model-token savings.
Batch already-known sequential commands in one host tool call when useful;
inspect every outcome and stop on failure before starting dependent work. Never
pre-record a check or review that has not happened.

```sh
python -B scripts/controller.py progress begin \
  --state /absolute/project/.work/task-progress --thread HOST_CHAT_ID \
  --id work-1 --phase work --note 'Fix the reproduced cursor boundary'

# Perform the work here; save a concise, accurate result outside tracked files.
python -B scripts/controller.py progress record \
  --state /absolute/project/.work/task-progress --thread HOST_CHAT_ID \
  --id work-1 --outcome pass --evidence /absolute/project/.work/work-1.txt \
  --summary 'Implemented the cursor change; behavioral checks are next'
```

Use `work` for each implementation or research-synthesis pass on the deliverable;
it consumes a pass at begin, including an interrupted pass. `research` is bounded
supporting investigation, `check` runs the actual acceptance checks, and `review`
inspects the final artifact and affected consumers. Save actual check output and
exit status, and concrete review findings. The tracker hashes the saved evidence
but trusts the reported outcome; a `pass` label alone is not proof of correctness.

Only one step can be unresolved. After interruption, inspect its saved artifacts
and record `interrupted`, `blocked`, `fail` or the actually established result.
There is no automatic relaunch or refunded pass. Retrying a record is idempotent
only when its content is identical. Keep each evidence file immutable and use a
new file for a later step. Never overwrite a failed result with a successful retry.

Review can begin only after current checks pass. Recording a passing check or
review fails if declared artifacts changed during that step or are missing.
A subsequent check requires a new review, even if source is unchanged.

## Pause, resume, stop and complete

Use `pause`, `resume`, `stop` or `complete`, each with `--note` explaining why.
Pausing this tracker does not pause a model, process or native Goal. Stop any
running command through its host tool when needed and retain its actual outcome.
The original wall-clock deadline and consumed passes survive pause/resume;
resume does not permit new work after the deadline. Clock rollback before the
last saved transition is rejected. Late evidence can still be recorded.

`complete` requires an active run within its deadline, no unresolved step, and
current passing checks and the selected review mode. Changed or missing evidence
invalidates approval. `stop` retains an incomplete result. Neither terminal state
can restart. Later edits leave a historical completion record but show its
evidence as stale. Once current checks and review pass, complete the task rather
than spending leftover passes on optional work.

`status` regenerates the view without resetting state. The locked, atomic,
private `loop-progress.json` is authoritative; retain it after a view-write error
and retry status. Never reinitialize to recover a display failure. Do not store
secrets in summaries or evidence. POSIX file guarantees are required; native
Windows mutation is unsupported by this helper.

The tracker can refuse its own transitions. It cannot prevent edits, commands or
model calls made outside it, kill an overrun, prove reviewer independence, or
measure native-chat tokens and charges. Those remain unknown. Thread binding
protects against accidental cross-chat use, not another process with the same
OS user. Hash freshness is not semantic correctness. Use host permissions and
real checks, and describe these limits honestly.
