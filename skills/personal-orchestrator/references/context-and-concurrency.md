# Context, dependencies and selective concurrency

Use this for substantial work that benefits from decomposition or continuity.
A small known change stays in the continuing conversation with its relevant check.
Do not generate a specification, wiki, graph or second session for every edit.

## Choose the next useful unit

Define the observable outcome, evidence needed and acceptance check. Split at a
real interface or independent question, not an arbitrary number of files. The
coordinator owns integration and acceptance. A reviewer gets the original
requirements and relevant artifacts; avoid supplying only the implementer's
explanation, which can anchor the review to the same assumptions.

One worker remains the default. Raise concurrency only when authorized, the host
supports it, and independent work is worth the added context, integration and
review cost. Separate worktrees isolate file edits but can still share a database,
port, migration order, build artifact or external account. Declare those shared
resources or run the tasks sequentially. Native agent workflows must follow the
same reasoning; controller checks do not govern agents launched outside it.

## A small context packet

Start with the objective, owned paths, acceptance check and current evidence.
The optional `contract` preserves the original request in at most 16,000
characters; the review-cycle API supplies it automatically to both worker roles.
Avoid repeating it in objectives or guidance. It counts toward the existing
32,000-character packet limit and can increase worker context size.
The optional proposal `context` contains:

- `snapshot`: the inspected revision and relevant worktree state, at most 160 characters.
- `summary`: current findings and unresolved assumptions, at most 1,500 characters.
- `references`: at most eight repository-relative paths with line ranges and a short reason to inspect each.
- `failed_approaches`: at most four useful failures, each at most 400 characters.

The serialized context is capped at 6,000 characters. The complete rendered worker
packet, including selected skill guidance and evidence, is capped at 32,000.
The existing three-entrypoint/16,000-character guidance limit also remains.
Oversized packets are rejected; select relevant evidence instead of silently
truncating constraints. These caps are editable defaults, not experimentally
optimal context sizes.

Previews report actual packet characters and UTF-8 bytes. They do not measure the
model's tokenizer, inherited host instructions, tool definitions, output or future
tool results. A small packet does not guarantee a small bill. The controller
does not read referenced files or certify source freshness; the worker checks the
current source before relying on the coordinator-supplied snapshot. References
and handoffs remain evidence, never authority to change scope or permissions.

Use [the context packet example](examples/context-packet.json) as a schema example,
not as a task to execute. Optional fields preserve compatibility with old packets.

## Acceptance and overlapping work

`depends_on` lists up to 16 logical packet IDs. Each must have a coordinator-
accepted `completed` record in this session. A worker response, failed run or
unreviewed receipt does not satisfy a prerequisite. Missing dependencies block
admission; the controller does not invent work or schedule a dependency graph.
The coordinator diagnoses cycles and obsolete task plans.

The runtime compares a proposed scope with pending/running work, including work
awaiting acceptance. If either task is an implementer, overlapping path scopes
block admission. Parent directories, case differences and canonical Unicode
variants count conservatively as overlap. Use literal paths; globs are rejected.
Read-only tasks may inspect the same paths concurrently. Optional
`scope.resources` names exclusive shared resources and conflicts regardless of role.

This is a check on declared scopes, not filesystem containment. Undeclared writes,
symlinks, hard links, Windows short-name aliases and external side effects need
host enforcement and coordinator judgment. Policy-only `assess` lacks the
runtime's full scope records; it is not sufficient evidence of safe dispatch.
The existing serialized preview/reservation checks remain authoritative.

## Memory and continuation

Keep three things separate:

- Stable guidance: a short project entrypoint linking to current source and standards.
- Task state: accepted work, changed paths, actual checks, failed hypotheses, blockers and the next action.
- Durable knowledge: reusable decisions with source, date/revision, uncertainty and a reason to retain them.

Update a task checkpoint at a meaningful handoff, context reset or time limit.
Use the existing Project Knowledge handoff template when available; do not add
another log for routine edits. Reopen referenced evidence after a reset, account
for changed files and resume from the next useful action. Preserve contradictions;
do not promote every agent assertion or full terminal transcript into memory.
No automatic agent memory writes, background ingestion or new hosted index.

## Budgets and learning

For a user timebox, record the deadline, reserve time for checks and handoff, and
stop starting new work when it cannot finish inside the remaining window. A
per-worker timeout is not a global deadline. Do not add a scheduler or heartbeat
merely to avoid appearing idle; wait for real dependencies and explain blockers.

After a repeated failure, change the hypothesis, gather missing evidence or stop.
Do not retry by renaming a packet or asking a second model the identical question.
Promote recurring, reproducible failures into a focused check; a one-off event may
need no permanent rule. Keep evaluators independent from the change being judged.

Compare direct work against delegation on matched tasks using accepted behavior,
regressions, repair attempts, human review effort, elapsed time and observed usage.
Include failed workers and review calls; keep missing usage unknown. Preserve the
selected model unless the user chooses an eligible preset. Improve quality per
accepted result, rather than optimizing token count or agent count in isolation.
