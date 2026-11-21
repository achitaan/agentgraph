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
| 0. Audit and baselines | n0-n7 | passed: four audits, the bench (`bin/agentgraph bench`), baseline runs, nine ranked problems |
| 1. Context and cost | n22 (cost visibility), n8 (measured proposals), n9 (after-measurements) | n22 and n8 passed; n9 runs once the code lanes land |
| 2. Orchestration | n10 | passed: solo vs delegated measured, drawn flow followed in 3 of 3 runs |
| 3. Execution reliability | n11 (transitions), n19 (liveness, status), n20 (view labels), n21 (flows, shared problems) | n11 and n19 passed; n20 and n21 running |
| 4. Self-improvement in the views | n23 (proposal records, approve/reject/revert, API), n24 (view controls), n12 (one demonstrated cycle) | n23 running; n24 after n20; n12 last |
| 5. Install and customization | n13 | passed |
| Review and final report | n16, n17 | not started |
| Approval and install | n14, n15 | waits for the user; nothing below "Waiting for your review" is installed |

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

Code (committed on `design-lab`, tests rerun by the orchestrator):

- Graph rules in code (n11, `9f91822`): on the n2 experiment of invalid graph edits, `check` rejects 40 of 52
  with its new history (before: 11 of 51). The five real graphs give the same result before and after.
  `agentgraph graph set|event` changes one node under the lock and refuses a write `check` would reject.
  Still accepted: three flow-bypass edits (n21).
- Liveness (n19, `18e7a05`): every running node is `live`, `quiet`, `orphan` or `unreported` from the session
  database, not from the graph file's word; `agentgraph status --task T` prints confirmed vs inferred state.
- Cost visibility (n22, `4bc3ea0`): `cost` shows billed vs messages vs unreported. On this chat: $40.65 of
  compaction calls over 19 compactions that no message row reports; 7 long worker sessions cost $415.09.
- Install and update (n13, `d5af126`): in a scratch home, a reinstall kept the chosen preset, an added agent,
  the user's own prompt files and an edited skill (old `install.sh`: all four lost). `--list-backups`,
  `--restore`; personal folder `~/.config/opencode/agentgraph-personal/`; `docs/CUSTOMIZE.md`.
- Bench (n5, n6, `3fdba9b`, `4ecbf7b`): isolated scratch config, first-turn probes for 12 agents, three
  scenarios, recovery run (interrupted at 50 s, resumed with 0 nodes redone).

Measured on scratch configs only (not installed):

- Context (n8, `7fc7639`): with proposals A+B a worker's first turn falls from 15,915 to 6,886 tokens
  (impl-openai, -56.7%); verifier -63.2%, oracle -66.5%, every worker role -25% or more. Worker context per
  turn inside a delegated run: 17.2k to 9.5k (n=2). The 20% target is met for workers. It is not met for the
  orchestrator (first turn -6.8%) or for whole delegated runs, where the orchestrator's own turns are 75-85%
  of the context and vary more than the saving.
- Delegation (n10, `94949b6`): on two small tasks (n=3 each, one model) delegating cost 2.0-2.2x and took
  1.5-1.8x as long as solo, same pass rate. A flow drawn in the scratch structure folder and never mentioned
  in the task was applied and run in 3 of 3 runs; following it cost 1.5x.

## Waiting for your review

Nothing here is in the live config. Each item names the file to read. Say "approve <letter>" or "reject".

| | Change | Evidence | File |
|---|---|---|---|
| A | Move the delegation policy out of the global `AGENTS.md` into the four `orchestrator_append.md` files, byte for byte. Workers stop reading it. | Workers' first turn -23% to -33%; orchestrator +0.3%; orchestrator still quotes the policy, a worker answers NONE. | `n8/r1/proposal-A.diff` |
| B | Per-agent tool denies in the plugin settings (e.g. implementers lose web, image and `execute` tools). | impl-* -33%, verifier -31.5%, oracle -40%, orchestrator -7%. Risk: a denied tool a lane turns out to need. | `n8/r1/proposal-B.diff` (A+B: `proposal-AB.diff`) |
| C | A round budget for long lanes (new session after N rounds). | Estimate only, not measured: 7-19% of the six long Opus sessions' $380.79. | `n8/r1/proposal-C.diff` |
| D | `AGENTS.md` line 9: the orchestrator may do work itself when the change fits in context it holds and has a check it can run (about 150 lines), instead of "under ~20 lines". | Delegating such tasks cost 2x, 1.5-1.8x time, same pass rate (n=3, small tasks, one model). | `n10/r1/comparison.md`, section "Proposal" |

Paths are under `.opencode/artifacts/agentgraph-improve/`.

Behaviour changes already in the code, for your information (undo: `git revert` of the named commit):

- `install.sh` (`d5af126`): the toolkit's `AGENTS.md` arrives as `AGENTS.md.toolkit-new` when yours differs;
  unknown options are refused; files you edited are kept and the new version is put beside them.
- `sync.sh` reads personal exclusions from the git-ignored `sync.conf` (yours was created with today's values).
- `check` (`9f91822`) keeps `.opencode/graph/.history/` and rejects edits that undo passed work.

## Unresolved

- Whole-run token totals cannot show a saving at n=2-3: the orchestrator's turn count varies more than the
  workers' share. A paired comparison needs a scenario that always delegates.
- The solo-vs-delegated result covers small, well-specified tasks on one model only.
- `busy` uses the root's session tree only and can disagree with `liveness`; `sources.CONFIG_DIR` ignores
  `OPENCODE_CONFIG_DIR`; the bench's config hash does not cover `structure/`.

## Next

n20, n21 and n23 are running. Then n24 (view controls), n12 (the demonstrated cycle), n9 (after-measurements),
the reviewer gate n16 and the final report n17.
