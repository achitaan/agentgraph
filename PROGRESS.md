# Architecture improvement and executable loops: progress

Resume from here after an interruption. The checkpoints for the work itself are two task graphs, validated with
`bin/agentgraph check --task <name>`:

- `.opencode/graph/agentgraph-improve.json`: the architecture improvement plan (Phases 0-5).
- `.opencode/graph/agentgraph-loops.json`: executable visual workflows (releases vNext.1 to vNext.5).

Their artifacts are under `.opencode/artifacts/<graph name>/` (git-ignored, local only).

Spend stop: $1,600 for the whole chat, including its workers and bench runs (user, 2026-11-21); no new lanes
from $1,550. Prompt, settings, skill and model changes are built and measured on scratch copies of the config and
installed into the live config only after the user approves. Lanes run on Sonnet 5.5.

## Part 1: architecture improvement

| Phase | Graph nodes | Status |
|---|---|---|
| 0. Audit and baselines | n0-n7 | passed: four audits, the bench (`bin/agentgraph bench`), baseline runs, nine ranked problems |
| 1. Context and cost | n22 (cost visibility), n8 (measured proposals), n9 (after-measurements) | passed. The 20% target is met for worker context and not for whole runs (see below) |
| 2. Orchestration | n10 | passed: solo vs delegated measured, drawn flow followed in 3 of 3 runs |
| 3. Execution reliability | n11 (transitions), n19 (liveness, status), n20 (view labels), n21 (flows, shared problems) | passed |
| 4. Self-improvement in the views | n23 (proposal records and API), n24 (view controls), n25 (a real cycle from the command line), n12 (the same proposals driven through the page) | passed, on a scratch config only |
| 5. Install and customization | n13 (install, in a scratch home), n27 (install review fixes) | passed: 42 install tests, a reinstall kept the preset, an added agent, own prompt files and an edited skill; not run on the live home |
| Review | n16 (independent review), n26 and n27 (its minor findings) | passed: n16 confirmed (nothing run), twelve minor findings; n26 fixed 1-5, 10, 11 and n27 fixed 6-9; finding 12 (no server-side confirm) is a known limit |
| Final suites and report | n17 | passed: `docs/REPORT.md` and `docs/ARCHITECTURE.md` written; final suite counts and total spend are in `docs/REPORT.md` section 7 |
| Approval and install | n14, n15 | done 2026-11-21: A, B (less one deny), D, L installed; C, J deferred; K not drafted |

### Starting numbers (measured, 2026-11-21, before any change)

- Fixed text per turn (`bin/agentgraph bloat`, characters / 4): orchestrator 37,525 chars (~9.4k tokens) in the
  `normal` preset; workers 12,425-21,718 chars. `AGENTS.md` (12,042 chars) is loaded into every agent, though it
  says it applies only to the primary; for `explorer` it is 89% of the fixed text. Tool schemas and the plugin's
  built-in prompts are not counted by `bloat`.
- This chat (`bin/agentgraph cost`): $441.41 over 1,731 turns, largest context 848k tokens; 55 cold-cache turns
  cost $195.56 (44% of the chat); the 383 turns over 400k context cost $226.75. Its 123 worker sessions cost $792.98.
- Cost trend (`bin/agentgraph cost --trend`): $0.305 a turn before the cost settings changes, $0.109 a turn after
  the one-hour cache (Fable models only) and the compaction buffer change.

### Ranked problems (from the audits; details in `.opencode/artifacts/agentgraph-improve/n7/r1/ranked.md`)

1. Graph rules live only in prompts: `check` accepted 38 of 51 invalid edits (n2 experiment); the real
   `agentgraph-desktop` graph has 17 nodes passed before a dependency. Fixed in code: n11, n21.
2. Re-running `install.sh` resets the chosen preset, drops added agents, deletes the user's own prompt files and
   replaces edited skills (n3 scratch install). Fixed in code: n13.
3. Five long Opus `impl-claude` sessions (400+ turns) cost $309, 43% of worker spend; worker context is 13% fixed
   text and 87% growth (n1). Proposal C (needs approval; estimate only).
4. Compaction summary calls cost about $119 (9%) and are not shown by `cost` (n1). Fixed in code: n22.
5. The improvement cycle has never completed on real data (0 cases, 0 improvements) and no view or command can
   approve, reject or revert (n3). Built and demonstrated on a scratch config: n23, n24, n25, n12.
6. Reported `running` is drawn as live; resume is prompt-only (n2). Fixed in code: n19, n20.
7. Drawn flows can be bypassed by retyping or reassigning a node; `check`, `record` and the views disagree (n2).
   Fixed in code: n21.
8. 9-12k tokens of each worker's first prompt are not counted by `bloat` (tool schemas, built-in prompt) (n1).
   Measured with bench probes; proposal B.
9. `AGENTS.md` (the orchestrator policy) is in every worker's system prompt, about 3k tokens a turn, est $5.8
   (n1, confirmed). Proposal A.

Measured not to matter: orchestrator prompt text (~2% of root cost), skills (<$2), delegation briefs (~2% a lane).

### Measured changes

Code (committed on `design-lab`, tests rerun by the orchestrator):

- Graph rules in code (n11, `9f91822`): on the n2 experiment of invalid graph edits, `check` rejects 40 of 52
  with its new history (before: 11 of 51). The five real graphs give the same result before and after.
  `agentgraph graph set|event` changes one node under the lock and refuses a write `check` would reject.
- Flow enforcement and one problem list (n21, `0f44192`): the flow-bypass edits n11 still accepted are rejected;
  `check`, `record` and the views read the same problem list.
- Liveness (n19, `18e7a05`; views n20, `5e3c141`): every running node is `live`, `quiet`, `orphan` or
  `unreported` from the session database, not from the graph file's word; `agentgraph status --task T` prints
  confirmed vs inferred state; the views label each running step the same way.
- Cost visibility (n22, `4bc3ea0`): `cost` shows billed vs messages vs unreported. On this chat: $40.65 of
  compaction calls over 19 compactions that no message row reports; 7 long worker sessions cost $415.09.
- Install and update (n13, `d5af126`): in a scratch home, a reinstall kept the chosen preset, an added agent,
  the user's own prompt files and an edited skill (old `install.sh`: all four lost). `--list-backups`,
  `--restore`; personal folder `~/.config/opencode/agentgraph-personal/`; `docs/CUSTOMIZE.md`.
- Bench (n5, n6, `3fdba9b`, `4ecbf7b`): isolated scratch config, first-turn probes for 12 agents, three
  scenarios, recovery run (interrupted at 50 s, resumed with 0 nodes redone).
- Improvement proposals (n23 `3668aa3`, n24 `073554d`): a proposal is a record with its change, its tune and
  held-out results, and a verdict computed by code; `agentgraph improve` and the page can approve (with a
  backup), reject, revert and reopen. The verdict says "better on tune only" when held-out did not move.

Measured on scratch configs (A and B were installed later, on 2026-11-21; see "Waiting for your review"):

- Context, first turn (n8, `7fc7639`): with proposals A+B a worker's first turn falls from 15,915 to 6,886 tokens
  (impl-openai, -56.7%); verifier -63.2%, oracle -66.5%, every worker role -25% or more.
- Context, whole runs (n9, `45909ea`, scenario `p2-s3-deleg`, n=3 each): with A+B worker context falls from 234k
  to 96k (-58.9%), and per worker turn from 18.4k to 8.8k (-52.4%). The whole run falls 6.5% in context and 3.0%
  in cost, because the orchestrator's own turns are 81-92% of a delegated run and its first turn falls only
  6.8%. So the 20% target is met for worker context and is not met for whole runs. The code changes alone move
  context by +0.4%. All 24 runs pass; recovery 1 of 1 in both configs with 0 nodes dispatched again.
- Delegation (n10, `94949b6`): on two small tasks (n=3 each, one model) delegating cost 2.0-2.2x and took
  1.5-1.8x as long as solo, same pass rate. A flow drawn in the scratch structure folder and never mentioned
  in the task was applied and run in 3 of 3 runs; following it cost 1.5x.
- One real improvement cycle (n25, `7923c82`; $6.11 of runs; write-up `n25/r1/cycle.md`): five new scenarios,
  split into tune and held-out. No behaviour met the failure rule, so the pre-registered cost criterion was
  used. Proposal "do small steps solo": tune 3/6 to 5/6, held-out 3/9 to 3/9, median cost of `p4-kv-steps`
  $0.401 to $0.247, p = 0.2. Verdict: better on tune only. A padding proposal showed no difference (n=2).
  Approve and revert were verified by file hash from the command line (n25) and by clicking in the page in
  headless Chrome (n12, `n12/r1/visual-cycle.md`): approve writes nothing before its confirm step; revert
  restores the original bytes; a reject changes no file.

### Waiting for your review

Decided on 2026-11-21: A, B, D and L approved and installed (`.opencode/artifacts/agentgraph-improve/n15/r1/install.md`; B with the `ast_grep_replace` deny left out for the four agents that write). C and J deferred. K is not drafted. Undo: `sh .opencode/artifacts/agentgraph-improve/n15/r1/undo.sh`.

| | Change | Evidence | File |
|---|---|---|---|
| A | Move the delegation policy out of the global `AGENTS.md` into the four `orchestrator_append.md` files, byte for byte. Workers stop reading it. | Workers' first turn -23% to -33%; orchestrator +0.3%; orchestrator still quotes the policy, a worker answers NONE. With B, in whole runs: worker context -58.9%, run cost -3.0% (n=3). | `n8/r1/proposal-A.diff` |
| B | Per-agent tool denies in the plugin settings (e.g. implementers lose web, image and `execute` tools). | impl-* -33%, verifier -31.5%, oracle -40%, orchestrator -7%. Risk: a denied tool a lane turns out to need. | `n8/r1/proposal-B.diff` (A+B: `proposal-AB.diff`) |
| C | A round budget for long lanes (new session after N rounds). | Estimate only, not measured: 7-19% of the six long Opus sessions' $380.79. | `n8/r1/proposal-C.diff` |
| D | `AGENTS.md` line 9: the orchestrator may do work itself when the change fits in context it holds and has a check it can run (about 150 lines), instead of "under ~20 lines". | Delegating such tasks cost 2x, 1.5-1.8x time, same pass rate (n=3, small tasks, one model). | `n10/r1/comparison.md`, section "Proposal" |
| J | The n25 proposal "do small steps solo" (one line in `normal/orchestrator_append.md`). Overlaps D. | Better on tune only: tune 3/6 to 5/6, held-out unchanged at 3/9, p = 0.2. By its own rule this is not enough to recommend. | `n25/r1/cycle.md` |
| K | Point the two improvement skills at the `agentgraph improve` commands; they still describe the older chat ritual. | No measurement; a consistency change. | not drafted |
| L | Three lines for `config/AGENTS.md` on `graph set`, `status` and flows, so the orchestrator uses the commands instead of editing the graph file whole. About 140 tokens more on every orchestrator turn. | No measurement. | `review/proposal-L.md` |

Paths are under `.opencode/artifacts/agentgraph-improve/`.
Letters E to I are not used here: they name sections of the earlier review list (side chat round).

Behaviour changes already in the code, for your information (undo: `git revert` of the named commit):

- `install.sh` (`d5af126`): the toolkit's `AGENTS.md` arrives as `AGENTS.md.toolkit-new` when yours differs;
  unknown options are refused; files you edited are kept and the new version is put beside them.
- `sync.sh` reads personal exclusions from the git-ignored `sync.conf` (yours was created with today's values).
- `check` (`9f91822`) keeps `.opencode/graph/.history/` and rejects edits that undo passed work.

### Unresolved

- Whole-run context: the orchestrator's own turns dominate a delegated run, so worker savings barely move the
  total. Nothing measured so far lowers the orchestrator's share by 20%.
- The solo-vs-delegated result covers small, well-specified tasks on one model only.
- Two behaviours fail in 3 of 3 runs and have no proposal yet: no task graph for a two-part task done directly
  (`p4-graph-two`), and a long explorer report not saved as a file (`p4-report-file`).
- `improve revert` appends no ledger row; `improve import-bench` has no option for a cost criterion; a case in
  `cases` arises only from a failed worker node.
- `/api/improve` has no confirm on the server side (the page asks first), the same as the other POST endpoints.
- `busy` uses the root's session tree only and can disagree with `liveness`; `sources.CONFIG_DIR` ignores
  `OPENCODE_CONFIG_DIR`; the bench's config hash does not cover `structure/`.

## Part 2: executable visual workflows

Aim: the saved drawing, not the orchestrator's reading of it, decides execution order and loop conditions; code
owns the control flow. Design: `.opencode/artifacts/agentgraph-loops/v1/r1/design.md`.

Audit (v0, real probes on OpenCode 2.3.2, $0.364): `run --session ID` continues one session and the next turn
recalls the earlier one at cached prices; a hung turn is stopped with SIGTERM and the session still takes the
next message; cost is final when the process exits. Native `/goal` has no headless entry (it is a slash command
of the terminal and Desktop apps). Plugin hooks were not tested; the idle hook is not in the installed plugin
types. A self-loop `A -> A` today is text for the orchestrator only, and flow files drop extra keys on rewrite.

Decision (v1): loop settings live in `structure/.loops/<flow id>.json` beside the unchanged flow file; one
detached controller process per run calls `opencode run` once per attempt, runs the check command itself and
routes on its exit code; each run keeps its own pinned definition and record under `.opencode/loops/<run id>/`.

| Release | Graph nodes | Status (graph) |
|---|---|---|
| vNext.1 single-agent loop | v0-v5 | passed. Tests A-D on real sessions: 38 checks, 0 failed, $0.0837; through the real page: 8 runs, $0.098, 28 PASS in round 2 (`v5a`, `v5`) |
| vNext.2 loops between agents | v6, v7 | passed. v6: two real runs, about $0.097. v7: four rounds; web 414/414, terminal 194. Real judge FAIL then PASS through the page (`v10`) |
| vNext.3 recovery, pause, cost | v8, v8b | passed. v8: a real `not_sent` answered and resumed, $0.123. v8b: three rounds, Python suite 1732 OK |
| vNext.4 inspect and improve | v7, v10 | passed. v10: failed run, trial, compare, keep, go back, undo through the real page, $0.6446 over 10 runs. It ran before the final review's fixes; no later real-session run is recorded |
| vNext.5 | none | not built (six items, `docs/REPORT.md` section 3.7) |
| Benchmark | v9 | passed: 36 real runs, every method 12/12 on visible and hidden checks, so no ranking on correctness; direct $0.589, loop-1 $0.601, loop-2 $1.261 |
| Controller review | v11a | passed: round 1 refuted, round 2 confirmed; the reviewer ran nothing |
| Final review | v11 | passed after three rounds: `unverifiable` (2 major, 10 minor), `refuted` (1 major, 5 minor), `confirmed` at `bb3bb31` (no critical or major). Six minor findings left unfixed. The reviewer ran nothing |
| Final suites, docs | v12 | final suites run: Python 1732 OK, web 414/414, terminal 194, Desktop 78 ok, real host exit 0 (`.opencode/artifacts/agentgraph-loops/v12/r1/final-values.md`); this document set written; the node is closed by the orchestrator |

No release is called complete until it has been run through the real page and real OpenCode sessions.

## Next

What remains:

1. Your decision on C, J, K and the earlier sections. Item K is not drafted.
2. A, B, D and L are installed (n14, n15): first-turn tokens of the implementers 15,911 to 7,273 (-54.3%), orchestrator -4.0%, one probe each; no whole run on the installed config. Restart OpenCode for open sessions to read the new text.
3. Total spend at the closing sample is in `docs/REPORT.md` section 7.
4. After the report was closed, the loops graph was recorded in the `memory-fast` ledger (`bin/agentgraph record`), so the ledger fingerprint is now
   `d7201d28c3276af0` (13 files); the config fingerprint was unchanged then (`86f9673fc3ff6a77`) and is `a39b89269a623236` after the install. The record says an improvement is due
   (1 pending run, 78 pending feedback items); none was proposed or applied in this session.
   After the install the improvement graph was closed (27 of 27 nodes, 9 of 9 objectives, three lessons) and recorded too: ledger
   `2046809d1c872356` (13 files), "improvement due: 2 pending runs, 78 pending feedback items".
5. The six minor findings of the final review are not fixed (`docs/REPORT.md` section 3.6). The temporary folders were deleted on 2026-11-21 on the user's word,
   except `p4-before`, `p4-after` and `p4-pad` (299 MB left; `docs/REPORT.md` section 7).

A next session should first run `bin/agentgraph check --task agentgraph-loops` and `bin/agentgraph check --task agentgraph-improve`,
read `docs/REPORT.md` sections 1, 3.6 and 5, and read `.opencode/artifacts/agentgraph-improve/n15/r1/install.md`
before changing anything.

Documents: `docs/REPORT.md` (what was done, numbers, limits), `docs/ARCHITECTURE.md` (how the parts fit), `docs/LOOPS.md` (loop reference).
