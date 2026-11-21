# Architecture improvement: progress

Resume from here after an interruption. The checkpoint for the work itself is the task graph
`.opencode/graph/agentgraph-improve.json` (validate with `bin/agentgraph check --task agentgraph-improve`); its
artifacts are under `.opencode/artifacts/agentgraph-improve/` (git-ignored, local only).

Spend stop: $1,600 for the whole chat, including its workers and bench runs (user, 2026-11-21); no new lanes
from $1,550. Prompt, settings and model changes are built and measured on scratch copies of the config and
installed into the live config only after the user approves.

## Phases

| Phase | Graph nodes | Status |
|---|---|---|
| 0. Audit and baselines | n0-n7 | running |
| 1. Context and cost | n8, n9 | not started |
| 2. Orchestration | n10 | not started |
| 3. Execution reliability | n11 | not started |
| 4. Self-improvement in the views | n12 | not started |
| 5. Install and customization | n13 | not started |
| Approval and install | n14, n15 | waits for the user |
| Review and final report | n16, n17 | not started |

## Starting numbers (measured, 2026-11-21, before any change)

- Fixed text per turn (`bin/agentgraph bloat`, characters / 4): orchestrator 37,525 chars (~9.4k tokens) in the
  `normal` preset; workers 12,425-21,718 chars. `AGENTS.md` (12,042 chars) is loaded into every agent, though it
  says it applies only to the primary; for `explorer` it is 89% of the fixed text. Tool schemas and the plugin's
  built-in prompts are not counted by `bloat`.
- This chat (`bin/agentgraph cost`): $441.41 over 1,731 turns, largest context 848k tokens; 55 cold-cache turns
  cost $195.56 (44% of the chat); the 383 turns over 400k context cost $226.75. Its 123 worker sessions cost $792.98.
- Cost trend (`bin/agentgraph cost --trend`): $0.305 a turn before the cost settings changes, $0.109 a turn after
  the one-hour cache (Fable models only) and the compaction buffer change.

## Measured changes

None yet.

## Unresolved

- Unknown until the bench exists: per-agent first-turn prefix in tokens (with tool schemas), latency, agent calls
  and correctness on fixed tasks, recovery after interruption.

## Next

Phase 0 audits (n1-n4) and the bench (n5) run in parallel; then baseline runs (n6) and the ranked problem list (n7).
