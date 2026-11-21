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
| 0. Audit and baselines | n0-n7 | audits n1-n4 and synthesis n7 passed; bench n5 running, baseline n6 next |
| 1. Context and cost | n22 (cost visibility, code), n8 (prompt/settings proposals), n9 | n22 running |
| 2. Orchestration | n10 | waits for the baseline |
| 3. Execution reliability | n11 (transitions), n19 (liveness, status), n20 (view labels), n21 (flows, shared problems) | n11, n19 running |
| 4. Self-improvement in the views | n12 | after n20, n21 |
| 5. Install and customization | n13 | running |
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

## Ranked problems (from the audits; details in `.opencode/artifacts/agentgraph-improve/n7/r1/ranked.md`)

1. Graph rules live only in prompts: `check` accepted 38 of 51 invalid edits (n2 experiment); the real
   `agentgraph-desktop` graph has 17 nodes passed before a dependency. Fix: n11, n21.
2. Re-running `install.sh` resets the chosen preset, drops added agents, deletes the user's own prompt files and
   replaces edited skills (n3 scratch install). Fix: n13.
3. Five long Opus `impl-claude` sessions (400+ turns) cost $309, 43% of worker spend; worker context is 13% fixed
   text and 87% growth (n1). Fix: proposal n8 (needs approval).
4. Compaction summary calls cost about $119 (9%) and are not shown by `cost` (n1). Fix: n22.
5. The improvement cycle has never completed on real data (0 cases, 0 improvements) and no view or command can
   approve, reject or revert (n3). Fix: n12.
6. Reported `running` is drawn as live; resume is prompt-only (n2). Fix: n19, n20.
7. Drawn flows can be bypassed by retyping or reassigning a node; `check`, `record` and the views disagree (n2).
   Fix: n21.
8. 9-12k tokens of each worker's first prompt are not counted by `bloat` (tool schemas, built-in prompt) (n1).
   Measure with bench probes, then proposal n8.
9. `AGENTS.md` (the orchestrator policy) is in every worker's system prompt, about 3k tokens a turn, est $5.8
   (n1, confirmed). Proposal n8.

Measured not to matter: orchestrator prompt text (~2% of root cost), skills (<$2), delegation briefs (~2% a lane).

## Measured changes

None yet.

## Unresolved

- Unknown until the bench exists: per-agent first-turn prefix in tokens (with tool schemas), latency, agent calls
  and correctness on fixed tasks, recovery after interruption.

## Next

Phase 0 audits (n1-n4) and the bench (n5) run in parallel; then baseline runs (n6) and the ranked problem list (n7).
