# When a packet cannot proceed

Use the actual denial and current state to choose the next action. This is
coordinator guidance, not an automatic recovery engine. Never change acceptance,
permissions or budgets merely to make a rejected packet pass.

| Cause | Useful next action | Boundary |
|---|---|---|
| Another writer owns overlapping paths or a shared resource | Review that work and accept or reject based on checks; then refresh and serialize the next packet | A dependent packet still needs its accepted prerequisite; worktrees alone do not isolate databases, ports or accounts |
| Missing accepted prerequisite | Inspect its result and checks, or finish the prerequisite | A worker's completion message is not acceptance; resolve cycles before dispatch |
| Oversized context | Keep constraints and current evidence; replace transcripts with source pointers and useful failed hypotheses | Recheck the rendered size; no silent truncation or removal of acceptance checks |
| Worker discovers a larger task | Return findings and a proposed split to the coordinator | No child-created workers; a genuine new subtask needs owned scope and acceptance |
| Failed attempt | Diagnose with new evidence, then retry within the same logical packet and remaining budget | Renaming or splitting failed work must not reset accounting |
| Budget exhausted or usage unknown under a configured threshold | Preserve the result and give an incomplete handoff | Do not open another session, switch models or continue directly to evade the limit |
| Unsafe helper, unsupported execution host, or approval denial | Follow the host's approval/error path; report the blocked action | Do not weaken trust checks or route the same denied action through another tool |
| Stale preview or changed source | Inspect the new state and regenerate the proposal/preview | Do not replay an old approval against changed work |

Prefer serialization when ownership is uncertain. Split only when it produces
independently useful results, rather than merely satisfying a gate. Direct work
is suitable when delegation itself is unnecessary and the action remains allowed;
it is not a fallback around a safety or spending limit.

After an adaptation, reassess once against the actual gate. If the same cause
persists without new evidence, stop that path and name the blocker. Continue
independent authorized work only when it fits the remaining task budget.
