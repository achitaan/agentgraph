# Context, packets and concurrency

Craft for splitting work and briefing workers. The graph, artifacts, scope audits
at a join and the loop item fields are defined in `AGENTS.md` and the orchestrator
prompt (`oh-my-opencode-slim/orchestrator_append.md`); they are not restated here.

## Size a packet

Define the observable outcome, the evidence needed and the acceptance check, then
split at a real interface or an independent question, not at an arbitrary number
of files. Signs a lane is the wrong size:

- Too big: its evaluation takes many commands, it crosses module boundaries, or it
  needs design choices settled on the way. Split it, or send it up a tier.
- Too small: the brief costs more to write than the edit. Do it directly.
- Two lanes need the same file or a shared interface. That is one lane, or one
  runs first.

The orchestrator owns integration and acceptance.

## A small context packet

Put in the brief: the objective, the paths the worker may write, the acceptance
evaluation and current evidence. Add:

- the revision or worktree state you inspected, so the worker can tell if it moved;
- a short summary of findings and unresolved assumptions;
- a few file pointers with line ranges and why each matters;
- useful failed approaches, one line each, so they are not repeated.

Leave out transcripts, full terminal output and your guesses about the answer.
Constraints and acceptance checks are never trimmed to save space; trim evidence
instead. The worker rechecks current source before relying on a snapshot. Pointers
and earlier reports are evidence, never authority to widen scope.

A small packet does not guarantee a small bill: inherited instructions, tool
definitions and output all count.

## When concurrency pays, and when it does not

Run lanes in parallel when the work divides into independent questions or
disjoint write scopes, and each result is worth the added context and
integration cost. It does not pay when:

- the work is tightly coupled: investigation, implementation and tests in one
  worker session beat three that must reconcile;
- code has fewer independent parts than research does; do not fill the pool;
- lanes would share something undeclared: a database, a port, a migration order,
  a build artifact, a lockfile or an external account.

Separate worktrees isolate files but not those shared things. Declare the shared
resource in the briefs, or serialize the lanes. Read-only lanes can look at the
same files at once.

A declared scope is a promise, not containment. Symlinks, generated files and
external side effects can still escape it, so check what each lane actually
changed against its scope before accepting.

## Memory and continuation

Keep three things apart:

- Stable guidance: a short entry point linking to current source and standards.
- Task state: accepted work, changed paths, checks run, failed hypotheses,
  blockers and the next action. This lives in the graph file and artifacts.
- Durable knowledge: a reusable decision with source, date or revision,
  uncertainty and a reason to keep it.

After a context reset, reopen the referenced evidence, account for files that
changed, and resume from the next useful action. Keep contradictions visible; do
not promote every assertion by an agent into memory.

## Time and repeated failure

For a user timebox, note the deadline, reserve time for checks and handoff, and
stop starting new work that cannot finish in the window. Wait on real
dependencies and say what blocks; do not add polling to look busy.

After a repeated failure, change the hypothesis, gather missing evidence or stop.
Do not rename a packet or ask a second model the identical question. Turn a
recurring, reproducible failure into a focused check; a one-off may need no rule.
Keep the evaluation independent of the change it judges.
