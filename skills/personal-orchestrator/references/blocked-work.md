# When an item cannot proceed

Routing for blocked, failed and stalled nodes is in `AGENTS.md` and the
orchestrator prompt (`oh-my-opencode-slim/orchestrator_append.md`). This file
covers how to read the cause and how to record an item so it can resume. Never change acceptance, scope or permissions just to get
an item through.

| Cause | Useful next action |
|---|---|
| Another lane owns overlapping paths or a shared resource | Finish or accept that lane first, then refresh and serialize this one; separate worktrees do not isolate databases, ports or accounts |
| A prerequisite has not passed | Evaluate its result yourself or finish it; a worker's completion message is not acceptance |
| The packet is too large | Keep constraints and current evidence; replace transcripts with file pointers and failed hypotheses; never drop the acceptance check |
| A worker finds a larger task | Take its `Discovered` report, add a node or split the item, with owned scope and a `done_when` |
| A failed attempt | Diagnose with new evidence and continue the same session; renaming or splitting does not make it a new item |
| Same failure twice or a stall | Ask whether the item is too big or its brief or evaluation is wrong, then move it up a tier |
| Missing fact, design question, or a choice only the user can make | Route to `explorer` or `researcher`, to `oracle` or `oracle-lite`, or ask the user, then resume the blocked item with the answer |
| Approval denied, or a tool or environment error | Report the blocked action and the denial; do not route the same action through another tool |
| Source moved since the packet was written | Inspect the new state and rewrite the brief; do not replay old instructions against changed files |

Prefer serializing when ownership is unclear. Split only when each piece is useful
on its own. Keep dispatching other ready nodes while one waits.

## Record a blocked item so it can resume

Write these into the graph node (or its artifact) before stopping:

- the goal and the evaluation, unchanged;
- the best working state and where it is (paths, branch or revision);
- what passed and what remains unverified;
- the failed hypotheses and the evidence against each;
- the exact command that fails and its output, trimmed to the deciding lines;
- the kind of failure (`code`, `evaluation`, `environment`, `input`) and the next
  hypothesis or the question that must be answered.

Keep the best state; do not revert or restart. After one adaptation, reassess
once against the real cause. If it persists without new evidence, record the item
as blocked and route it as `AGENTS.md` says.
