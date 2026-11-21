# Report: architecture improvement and executable loops

For a reader who saw none of the work. Two plans, both built on the branch `design-lab`. Every number carries a source path.
Paths starting `.opencode/` are local only: that folder is git-ignored, so the artifacts are on this machine, not in the repository.
`file#nNN` means the node `nNN` inside a task graph file.

Words used with a fixed meaning:

- **Demonstrated**: run on real OpenCode sessions (a real model), from the command line or through the real page.
- **Tested**: run against fixtures and scripted fakes. No model, or a fake one.
- **Scratch config**: a copy of the live configuration in a temporary folder. Every agent was pinned to one model, `anthropic/claude-sonnet-5-5`. Nothing was installed into the live configuration.

The live configuration was unchanged throughout: `bin/agentgraph bench fingerprint` gave config `86f9673fc3ff6a77` (347 files) and ledger `e3340c0e1d2cbb5b` (11 files) at the start, and gave the same values when this report was written (checked by running the command, not read from an artifact). Earlier nodes report the same values (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`).

## 1. One-screen summary

Task graphs: Part 1 is `.opencode/graph/agentgraph-improve.json`, Part 2 is `.opencode/graph/agentgraph-loops.json`.

### Part 1: architecture improvement (phases 0 to 5)

| Phase | Nodes | State | One line of evidence |
|---|---|---|---|
| 0 Audit and baselines | n0 to n7 | done, demonstrated | Bench on a scratch config with real runs: 12 agent probes, s1 x3, s2 x3, s3 x2 all pass (`.opencode/graph/agentgraph-improve.json#n6`). |
| 1 Context and cost | n22, n8, n9 | done, demonstrated on a scratch config; the two context changes (A, B) are not installed | Worker context -58.9%; whole run -6.5% context and -3.0% cost. The 20% target is met for worker context and not for whole runs (`.opencode/artifacts/agentgraph-improve/n9/r1/after.md`). |
| 2 Orchestration | n10 | done, demonstrated | 15 real runs: delegating cost 2.0x to 2.2x of solo on two small tasks (`.opencode/artifacts/agentgraph-improve/n10/r1/comparison.md`). |
| 3 Execution reliability | n11, n19, n20, n21 | done, tested; read on real graphs and the real database | 40 of 52 invalid graph edits rejected, before 11 of 51 (`.opencode/graph/agentgraph-improve.json#n11`). Not exercised by a live orchestrator: the three lines that tell it to use the commands wait as item L. |
| 4 Self-improvement in the views | n23, n24, n25, n12 | done, demonstrated on a scratch config | One real cycle, $6.11; verdict "better on tune only"; approve and revert driven by clicking in the real page, 8 of 8 checks (`.opencode/artifacts/agentgraph-improve/n25/r1/cycle.md`, `.opencode/graph/agentgraph-improve.json#n12`). |
| 5 Install and customization | n13, n27 | done, demonstrated in a scratch home; not run on the live home | 42 install tests; a reinstall kept the preset, an added agent, own prompt files and an edited skill (`.opencode/graph/agentgraph-improve.json#n13`). |
| Review | n16, n26, n27 | done | Verdict confirmed, 12 minor findings, the reviewer ran nothing (`.opencode/artifacts/agentgraph-improve/n16/r1/review.md`). |
| Approval and install | n14, n15 | not done | Wait for the owner (`.opencode/graph/agentgraph-improve.json#n14`). |
| Final report | n17 | this document | |

### Part 2: executable visual workflows (vNext.1 to vNext.5)

| Release | Nodes | State | One line of evidence |
|---|---|---|---|
| vNext.1 single-agent loop | v0 to v5 | done, demonstrated from the command line and through the real page | Tests A to D on real sessions: 38 checks, 0 failed, $0.0837 (`.opencode/artifacts/agentgraph-loops/v5a/r1/real-sessions.md`). |
| vNext.2 loops between agents | v6, v7 | done, demonstrated | Real judge FAIL then PASS through the page (run `20261121-074032-bdf5`); real FAIL then PASS in the benchmark (`.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md`). |
| vNext.3 recovery, pause, cost | v8, v8b | done; demonstrated in part | A real killed controller resumed through the page; a real `not_sent` answered by the real database. The adopt path and `redo` were not shown on a real session (`.opencode/graph/agentgraph-loops.json#v8`). |
| vNext.4 inspect and improve | v7, v10 | done; demonstrated through the page, except what is listed in section 5 | Failed run, trial, compare, keep, go back, undo: all PASS in `.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md`. That demonstration ran before the final review's fixes; `.opencode/graph/agentgraph-loops.json#v10` records no real-session run after them (section 3.4). |
| vNext.5 | none | not built | Six items, none built (section 3.7). |
| Benchmark | v9 | done | 36 real runs, every method passed every check: it does not rank them on correctness (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`). |
| Controller review | v11a | done, two rounds | Round 1 refuted, round 2 confirmed; the reviewer ran nothing (`.opencode/artifacts/agentgraph-loops/v11a/r2/review.md`). |
| Final review | v11 | done, three rounds; round 3 `confirmed` at `bb3bb31`; reading only | Round 1 found that the Desktop loop section spawned a command that did not exist. Fixed. Six minor findings left unfixed (section 3.6, `.opencode/artifacts/agentgraph-loops/v11/r3/review.md`). |
| Final suites, docs | v12 | final suites run, see section 7 | This document and the final suite runs in `.opencode/artifacts/agentgraph-loops/v12/r1/final-values.md`. |

### What waits for you

- Decisions on review items A, B, C, D, J, K, L and the earlier pending sections (section 6). Nothing in them is installed.
- The install into the live configuration after you approve (n14, n15).
- The six minor findings of the final review stay unfixed (section 3.6).
- Nothing in the temporary folders was deleted (section 7).
- Item K is not drafted (section 6).
- No placeholder is left in this report. Total spend is in section 7.

## 2. Part 1: architecture improvement

### 2.1 Starting numbers (measured before any change)

| Quantity | Value | Source |
|---|---|---|
| Orchestrator fixed text, `normal` preset | 37,525 characters (about 9.4k tokens) | `.opencode/graph/agentgraph-improve.json#n0` |
| Worker fixed text | 12.4k to 21.7k characters; `AGENTS.md` is 12,042 characters in every agent | `.opencode/graph/agentgraph-improve.json#n0` |
| One earlier chat | $441.41 over 1,731 turns, largest context 848k tokens | `.opencode/graph/agentgraph-improve.json#n0` |
| Cold-cache turns in it | 55 turns cost $195.56 | `.opencode/graph/agentgraph-improve.json#n0` |
| Turns over 400k context | 383 turns cost $226.75 | `.opencode/artifacts/agentgraph-improve/n0/r1/cost-this-chat.txt` |
| Cost per turn | $0.305 before the cost-settings changes, $0.109 after | `.opencode/graph/agentgraph-improve.json#n0` |
| First-turn tokens, bench probes | orchestrator 25,840; impl-openai 15,915; verifier 11,867 | `.opencode/graph/agentgraph-improve.json#n6`, `.opencode/artifacts/agentgraph-improve/n8/r1/proposals.md` |
| Cost of the baseline runs | $1.73 in `baseline.md`; "about $2.2" in the node evidence. The sources disagree; neither was reconciled. | `.opencode/artifacts/agentgraph-improve/n6/r1/baseline.md`, `.opencode/graph/agentgraph-improve.json#n6` |

The audits ranked nine problems (`.opencode/artifacts/agentgraph-improve/n7/r1/ranked.md`). The largest by dollars in the audit of a $1,279 chat: five long `impl-claude` Opus sessions of 400 or more turns cost $309, 43% of worker spend (`.opencode/graph/agentgraph-improve.json#n1`).

### 2.2 After numbers and the 20% target

Context tokens = input + cache read + cache write over all turns. First-turn numbers are bench probes of the agent's first prompt.

| Agent | First turn before | After A+B | Change | Source |
|---|---|---|---|---|
| impl-openai | 15,915 | 6,886 | -56.7% | `.opencode/artifacts/agentgraph-improve/n8/r1/proposals.md` |
| verifier | 11,867 | 4,362 | -63.2% | `.opencode/graph/agentgraph-improve.json#n8` |
| oracle | | | -66.5% | `.opencode/graph/agentgraph-improve.json#n8` |
| orchestrator | 25,840 | 24,071 | -6.8% | `.opencode/graph/agentgraph-improve.json#n8` |

Whole runs, scenario `p2-s3-deleg` (always delegates), n = 3 each, medians, code at `96a29bd`:

| Measure | Before | After A+B | Change | Source |
|---|---|---|---|---|
| Worker context, all workers | 234,177 | 96,278 | -58.9% | `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |
| Context per worker turn | 18,400 | 8,756 | -52.4% | `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |
| Whole run context | 1,171,595 | 1,095,932 | -6.5% | `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |
| Orchestrator's own context | 950,794 | 1,008,377 | share 81% to 92% of the run | `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |
| Run cost | $0.400 | $0.388 | -3.0% | `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |

Where the 20% target is met and where it is not (`.opencode/artifacts/agentgraph-improve/n8/r1/proposals.md`):

| Workload | Result | Source |
|---|---|---|
| Worker first-turn tokens | Met: -56.7% for impl-openai; every worker role -25% or more | `.opencode/graph/agentgraph-improve.json#n8` |
| Worker context in delegated runs | Met: -58.9% | `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |
| Whole delegated run | Not met: -6.5% context, -3.0% cost | `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |
| Orchestrator alone (`s1-simple`, `s2-parallel`) | Not met: first turn -6.8% to -6.9%; run totals rose +17.1% and +26.7% because the median run took one more turn (4 to 5, 3 to 4) | `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |
| Proposal C (round budget for long lanes) | Not met, and an estimate only: 7% to 19% of $380.79 | `.opencode/artifacts/agentgraph-improve/n8/r1/proposals.md` |

Other facts from the same measurement:

- `s3-workflow` fell -20.1% in context (n = 2). It delegated in 1 of 2, 2 of 2 and 1 of 2 runs in the three labels, so those totals are not comparable. It does not support the 20% claim (`.opencode/artifacts/agentgraph-improve/n9/r1/after.md`).
- The code changes alone moved context by +0.4% (`.opencode/artifacts/agentgraph-improve/n9/r1/after.md`).
- Every run passed its check (11 of 11, 11 of 11, recovery 1 of 1 and 1 of 1); after an interruption at 50 s the run resumed with 0 nodes dispatched again (`.opencode/artifacts/agentgraph-improve/n9/r1/after.md`).
- An earlier measurement, n8, gave worker context per turn 17.2k to 9.5k (n = 2, 9 runs, spend $3.05). n9 gives 18.4k to 8.8k (n = 3). n9 supersedes it (`.opencode/graph/agentgraph-improve.json#n8`, `.opencode/artifacts/agentgraph-improve/n9/r1/after.md`).
- What would move the whole-run number is the orchestrator's turn count (31 to 34 turns for a three-step task), not its fixed text (`.opencode/artifacts/agentgraph-improve/n9/r1/after.md`).

Cost visibility (n22, in code): on one chat `cost` shows billed $485.33 = messages $444.68 + $40.65 that no message row reports (19 compactions); workers $800.48; seven long sessions $415.09 (`.opencode/graph/agentgraph-improve.json#n22`).

### 2.3 Delegation evidence

15 real runs, n = 3 each, one model, all passed (`.opencode/artifacts/agentgraph-improve/n10/r1/comparison.md`). Spend $4.41.

| Scenario | Delegated | Solo | Ratio | Source |
|---|---|---|---|---|
| s3, median cost and time | $0.418, 93.6 s | $0.190, 52.7 s | 2.2x cost, 1.8x time | `.opencode/artifacts/agentgraph-improve/n10/r1/comparison.md` |
| s2, median cost and time | $0.167, 26.0 s | $0.082, 17.4 s | 2.0x cost, 1.5x time | `.opencode/artifacts/agentgraph-improve/n10/r1/comparison.md` |

A flow drawn in the scratch structure folder (`impl-openai -> verifier`) and never mentioned in the task was applied in 3 of 3 runs (9 of 9 nodes followed); obeying it cost 1.5x (`.opencode/graph/agentgraph-improve.json#n10`). These results cover two small, well-specified tasks and one model. They led to review item D, which is not applied.

### 2.4 What is enforced outside prompts

Code, not the orchestrator's reading of its prompt, now does these (all tested; commits in `PROGRESS.md`):

| Rule | Where | Evidence |
|---|---|---|
| A graph edit that undoes passed work, lowers attempts, or weakens a goal is rejected, with a history kept | `agentgraph/transitions.py`, `agentgraph/graphedit.py`, `agentgraph/check.py` | After n11: 40 rows REJECTED and 14 ACCEPTED, 2 of them unedited controls, so 40 of 52 invalid edits rejected (`.opencode/artifacts/agentgraph-improve/n11/r1/experiment-after.md`). Before: 11 of 51 rejected (`.opencode/graph/agentgraph-improve.json#n2`). After n21 the table has 49 REJECTED and 14 ACCEPTED rows (`.opencode/artifacts/agentgraph-improve/n21/r1/experiment-after.md`). The tables differ in size; they are not one series. |
| A drawn flow cannot be bypassed by retyping a node | `agentgraph/check.py` | n21 rows F3, F4, F5 rejected (`.opencode/graph/agentgraph-improve.json#n21`) |
| Whether a running node is `live`, `quiet`, `orphan` or `unreported` comes from the session database | `agentgraph/status.py`, `agentgraph/contract.py` | 209 tests; real `status` output (`.opencode/graph/agentgraph-improve.json#n19`) |
| Install and update keep user changes; backups and restore | `agentgraph/installer.py`, `install.sh` | 42 tests; 36 installer tests after the review fixes (`.opencode/graph/agentgraph-improve.json#n13`, `.opencode/graph/agentgraph-improve.json#n27`) |
| A prompt improvement is a record with computed verdict, approved with a backup and reverted by hash | `agentgraph/improve.py` | 43 tests (`.opencode/graph/agentgraph-improve.json#n23`) |
| A loop's attempts, check and limits | `agentgraph/looprun.py` | Part 2 |

Still prompts only: the orchestrator's choice to use `graph set`, `status` and the drawn flows (item L waits), and the whole delegation policy.

### 2.5 The visual self-improvement cycle

Built: a proposal is a file with its change, its tune and held-out results and a verdict computed by code. The terminal command `agentgraph improve` and the page can approve (with a backup), reject, revert and reopen (`.opencode/graph/agentgraph-improve.json#n23`, `.opencode/graph/agentgraph-improve.json#n24`).

One real cycle (n25, $6.11, scratch config; `.opencode/artifacts/agentgraph-improve/n25/r1/cycle.md`):

| Proposal | Result |
|---|---|
| `6f0e` do small steps solo | Tune 3/6 to 5/6; held-out 3/9 to 3/9; median cost of `p4-kv-steps` $0.401 to $0.247; p = 0.2. Verdict: better on tune only. |
| `5ac2` padding | No difference, n = 2 per scenario. |
| `d896` | Drafted; not enough runs. |

The pre-registered failure rule was not met by any behaviour, so the pre-registered cost criterion was used (`.opencode/artifacts/agentgraph-improve/n25/r1/cycle.md`). Driven through the real page in headless Chrome (n12): revert gave the original bytes, approve wrote nothing before its confirm step, a reject changed no file; 8 of 8 checks, 9 screenshots (`.opencode/artifacts/agentgraph-improve/n12/r1/visual-cycle.md`).

### 2.6 Install and customize

`sh install.sh` no longer resets the chosen preset, drops added agents, deletes the user's own prompt files or replaces an edited skill. In a scratch home an old `install.sh` lost all four; the new one kept them (`.opencode/graph/agentgraph-improve.json#n3`, `.opencode/graph/agentgraph-improve.json#n13`). `install.sh --list-backups` and `--restore` exist. Where the owner and the toolkit both changed a file, the toolkit's version arrives as `<name>.toolkit-new`. How to customize without editing toolkit files: `docs/CUSTOMIZE.md`. Not run on the live home.

## 3. Part 2: executable visual workflows

Design in `.opencode/artifacts/agentgraph-loops/v1/r1/design.md` and `.opencode/artifacts/agentgraph-loops/v1/r1/design2.md`; user reference in `docs/LOOPS.md`. Run of a loop = one detached controller process (code, not a model) per run. The audit of OpenCode 2.3.2 (15 probe sessions, $0.364) settled the design (`.opencode/artifacts/agentgraph-loops/v0/r1/audit.md`).

### 3.1 vNext.1: single-agent loop

Built: loop settings saved beside the drawn flow; a controller that runs one attempt, runs the check command itself, sends the failure into the same session and stops at a limit; start, status, list and cancel over the page API; the views (`.opencode/graph/agentgraph-loops.json#v2`, `.opencode/graph/agentgraph-loops.json#v3`, `.opencode/graph/agentgraph-loops.json#v4`).

Tests A to D were fixed in `.opencode/artifacts/agentgraph-loops/v1/r1/design.md` before the code. Each ran on a scripted fixture, then on real sessions from the command line (`.opencode/artifacts/agentgraph-loops/v5a/r1/real-sessions.md`), then through the real page (`.opencode/artifacts/agentgraph-loops/v5/r1/page-demo.md`):

| Test | Real session, command line | Real page | Source |
|---|---|---|---|
| A: check fails, then passes | `passed` in 2 attempts; check ran 2 times by its own counter; 1 session with 2 user messages, no child; $0.0160 | counter 2, one session, 2 user messages | `.opencode/artifacts/agentgraph-loops/v5a/r1/real-sessions.md`, `.opencode/graph/agentgraph-loops.json#v5` |
| B: check always fails, limit 3 | `failed` at 3 attempts; 3 check runs; 3 user messages; exit non-zero; $0.0310 | Failed at 3 of 3 | `.opencode/artifacts/agentgraph-loops/v5a/r1/real-sessions.md`, `.opencode/graph/agentgraph-loops.json#v5` |
| C: edit the check, run again | first run's files byte-identical after the edit; new run with its own session; $0.0090 | the first run's 11 files byte-identical, old check still shown | `.opencode/artifacts/agentgraph-loops/v5a/r1/real-sessions.md`, `.opencode/graph/agentgraph-loops.json#v5` |
| D: unusable setups | `invalid` (no check), `check_error` (exit 127), `stalled` (turn stopped at 25 s), `inconclusive` (exit 75); none `passed` | "Not started", "Check could not run", "Stalled", "Check inconclusive" | `.opencode/artifacts/agentgraph-loops/v5a/r1/real-sessions.md`, `.opencode/graph/agentgraph-loops.json#v5` |

Totals: 38 checks, 0 failed, $0.0837 (command line; `.opencode/artifacts/agentgraph-loops/v5a/r1/real-sessions.md`). Through the page, round 1 had 40 screenshots and 8 runs for $0.098. Steps 4 and 9 failed there (a new run stayed queued until a reload); the fix went into the views (v4) and round 2 showed the page following a run with no reload, 28 PASS and 0 FAIL in its log (`.opencode/artifacts/agentgraph-loops/v5/r2/drive.log`, `.opencode/graph/agentgraph-loops.json#v5`).

Not shown through the page: Undo pressed, a saved goal, a fresh session per attempt, allow-asks, the spend limit stopping a run, narrow widths (the view suite covers widths). One model, one repetition (`.opencode/graph/agentgraph-loops.json#v5`). The "no extra session" result rests on the scratch database for those runs; the controller does not stop an agent from launching subagents in general (`.opencode/artifacts/agentgraph-loops/v5a/r1/real-sessions.md`).

### 3.2 vNext.2: loops between agents

Built: `A -> J1 -> ... -> Jk -> A`: the first agent works, each judge in the drawn order judges, a failure goes back to the worker; a judge answers with a last line `VERDICT: PASS` or `VERDICT: FAIL`; a fresh judge session each turn by default; a person can approve; escalation, retries, artifacts copied per attempt with a hash. Unsupported paths are refused before a run exists (`.opencode/artifacts/agentgraph-loops/v1/r1/design2.md`).

Acceptance tests E1 to E13 (`.opencode/artifacts/agentgraph-loops/v1/r1/design2.md`, scripted fixture): before the code, `tests.test_looprun2` ran 61 tests with 30 failures and 64 errors (subtests count more than once); after, `tests.test_looprun`, `tests.test_looprun2` and `tests.test_loopapi` ran 125 tests, all OK (`.opencode/artifacts/agentgraph-loops/v6/r1/NOTES.md`).

| How demonstrated | What | Source |
|---|---|---|
| Real session, command line | Two runs `impl-openai -> verifier -> impl-openai` both passed on the first verdict; $0.0719 and $0.0247; each ran 2 sessions with 1 user message each | `.opencode/artifacts/agentgraph-loops/v6/r1/NOTES.md` |
| Real page | Judge FAIL, back to the worker, then PASS: one worker session over 2 attempts, a fresh judge session each; `passed_by` check+judges; $0.0538 (run `20261121-074032-bdf5`) | `.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md` |
| Real page | Approval: "Send back" with a note, then Approve; `passed_by` includes approval; $0.0381 (run `20261121-074225-b049`) | `.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md` |
| Real page | A path that does not return to its first agent is refused, no Run offered | `.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md` |

Three release 1 tests (two in `tests/test_server.py`, one in `tests/test_structure.py`) were moved from "a pair is refused" to "a path that does not loop back is refused", as the release 2 contract requires (`.opencode/graph/agentgraph-loops.json#v6`).

### 3.3 vNext.3: recovery, pause, cost

Built: a write-ahead cursor, `loop resume` with reconcile (ask OpenCode whether an in-flight turn was sent and finished: `not_sent` sends it, `finished` adopts the reply, `unknown` pauses and sends nothing), `--redo` and `--accept` as the person's choices, pause and cancel, cost per step, per session and per run with unknown kept apart from zero, the goal and criteria resent in every message, runs pinned to their own definition (`.opencode/artifacts/agentgraph-loops/v1/r1/design2.md`).

Acceptance tests R1 to R9 (scripted executor; `.opencode/artifacts/agentgraph-loops/v1/r1/design2.md`). Demonstrated on real sessions (`.opencode/graph/agentgraph-loops.json#v8`):

| What | Result | Source |
|---|---|---|
| Command line, real sessions | A detached self-loop passed in 2 attempts with 1 session and 2 user messages while a second process made 3,948 list and status calls | `.opencode/graph/agentgraph-loops.json#v8` |
| Command line, real database | A controller killed after the cursor and before the session existed was answered `not_sent`; resume sent once and passed. Real runs $0.123 in all | `.opencode/graph/agentgraph-loops.json#v8` |
| Real page | Pause then Resume: Running again within 1.5 s (round 2), passed, no step ran twice; $0.0827 (run `20261121-080718-8e3e`) | `.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md` |
| Real page | Controller killed with `kill -9` mid-turn: page read Interrupted 2 s later by itself; Resume answered "cannot tell"; "Take the turn as done" ended `limit` because the adopted turn's cost was unknown and a cost limit was set; $0.0463 (run `20261121-080634-b1dd`) | `.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md` |

Fixture only: a session that exists but holds no user message since the send; an HTTP test for the new 400 and 409 answers (`.opencode/graph/agentgraph-loops.json#v8`). The `finished` (adopt) path, `redo` on a real run, and a real compaction are not shown on a real session in the node evidence (`.opencode/graph/agentgraph-loops.json#v10`, `docs/LOOPS.md`).

### 3.4 vNext.4: inspect and improve

Built: click a step or edge for its history across attempts; compare two attempts or two runs; "Try without saving" runs changed settings for one run only; compare versions of the saved loop; "Keep this version", "Go back", "Undo" (`.opencode/artifacts/agentgraph-loops/v1/r1/design2.md`). Views tested at the end of the work: web 414 of 414 checks, terminal 194 (`.opencode/artifacts/agentgraph-loops/v12/r1/final-values.md`). Before the final review the web count was 402 (`.opencode/artifacts/agentgraph-loops/v7/r3/findings.md`).

Through the real page, real sessions, 10 runs, $0.6446 in all (`.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md`). Round 1 failed steps 6 and 7 on one view defect: after Resume or "Take the turn as done" the page read once, still saw paused, and never read again. It was fixed in `agentgraph/web/app.js` (a test went red on the old code first, 401 of 402) and the two steps were redone in round 2 with PASS. Steps 1 to 5 and 8 to 12 were not redone. The page changed again after this demonstration, in the fixes for the final review; those fixes were tested by web checks, Desktop checks 75 and 76 and screenshots, and no later real-session run is recorded (`.opencode/artifacts/agentgraph-loops/v7/r3/findings.md`, `.opencode/artifacts/agentgraph-loops/v7/r4/findings.md`).

| Step | Shown | Result |
|---|---|---|
| 8 | A loop with 1 attempt on a task needing 2: Failed; a trial with 4 attempts Passed; the saved file's hash unchanged by the trial | PASS |
| 9 | Compare the failed run and the trial: status and attempts marked "differs"; the trial settings run on another small task Passed | PASS |
| 10 | Keep this version, versions list 4 to 5, Go back (old bytes exactly), Undo (new bytes exactly); an unknown cost reads "cost unknown", never $0 | PASS |
| 11 | Reload: all 8 runs listed; the oldest run's 18 record files byte-identical to the hash list | PASS |
| 12 | Nothing live for 30 s: 0 requests | PASS |

### 3.5 Benchmark: direct vs loop-1 vs loop-2

Frozen suite of 6 tasks (two freezes), 36 real runs, one model, 2 repetitions per cell, n = 12 per method (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`). Methods: direct = one `impl-openai` run told to run the visible check; loop-1 = `impl-openai -> impl-openai` with the check; loop-2 = worker plus the `verifier` as judge.

| Method | n | Visible pass | Hidden pass | Cost over 12 runs |
|---|---|---|---|---|
| direct | 12 | 12/12 | 12/12 | $0.589 |
| loop-1 | 12 | 12/12 | 12/12 | $0.601 |
| loop-2 | 12 | 12/12 | 12/12 | $1.261 |

All three together cost $2.4506; the node evidence gives direct as $0.588 (rounding) (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`, `.opencode/graph/agentgraph-loops.json#v9`).

- Native `/goal` was not measured: it has no headless entry in OpenCode 2.3.2 (`.opencode/artifacts/agentgraph-loops/v9/r1/results.md`, `.opencode/artifacts/agentgraph-loops/v0/r1/audit.md`).
- Ceiling effect: every method passed every check. The suite does not rank the methods on correctness. That every direct run passed says these tasks are inside what the model does unaided (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`).
- One run changed an outcome: loop-2 on `t5`, run `20261121-054549-a1b1`. The check passed, the judge said FAIL for a real regression (an accented e-mail address no longer matched); the retry fixed it; $0.214 against $0.142 for the other repetition of that cell. The hidden check does not test that case, so it could not have caught it. It is a count of 1 event (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`).
- Loop-1's retry path was never used: no loop-1 run failed its check (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`).
- Cost: loop-2 cost about twice loop-1 and direct, because of the judge: 2.3 times direct in round 2, 1.9 times without the retried run. Direct and loop-1 are equal within repetition variance (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`).
- No saving by loops is claimed: nothing in the benchmark measured one.
- Task `t6`: its visible check touches 7 of 11 rules, not about a third as designed (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`).

### 3.6 The controller reviews

Reviewer: Sonnet 5.5, read-only. `shell` was denied in both rounds, so nothing was run: every row rests on reading. It is the same model family as the implementer, and in round 1 it did not read the contract files (`.opencode/artifacts/agentgraph-loops/v11a/r1/review.md`, `.opencode/graph/agentgraph-loops.json#v11a`).

| Round | Verdict | Findings | What was done |
|---|---|---|---|
| 1, at `6eb6ee6` | refuted | 2 major, 10 minor | v8 round 2: 12 findings, 11 reproduced with red tests and fixed (34 red tests on the old code), finding 9 (run clock) did not reproduce and no code changed; suite 1679 (`.opencode/artifacts/agentgraph-loops/v8/r2/findings.md`) |
| 2, at `cc6c9ce` | confirmed | rows 1 to 12 of its table (`.opencode/artifacts/agentgraph-loops/v11a/r2/review.md`): 6 fixed, 5 "fixed but", 1 agreed not a defect (finding 9); 8 new minor findings | v8b: all 8 reproduced, 17 tests red on the old code, then green; suite 1695 (`.opencode/artifacts/agentgraph-loops/v8b/r1/findings.md`) |

The first round's two major findings: (1) a status probe could take the lock a starting controller needed, leaving a started run `queued` forever; (2) a `queued` run with no controller could not be recovered from the page or the command line (`.opencode/artifacts/agentgraph-loops/v11a/r1/review.md`). Both were fixed in v8 round 2, and the reviewer's round 2 rows for them read "fixed" (finding 1) and "fixed but" (finding 2; `.opencode/artifacts/agentgraph-loops/v11a/r2/review.md`).

Consequences accepted in v8b: one additive key, `steps_sha256`, in `run.json` and the API; a run started before it has no pin and cannot be resumed (only scratch runs exist); eight older assertions changed where they had encoded a defect (`.opencode/graph/agentgraph-loops.json#v8b`). The final-message rule for a verdict was checked on 315 of 315 stored turns with a verdict line (`.opencode/artifacts/agentgraph-loops/v8b/r1/verdict-stream.md`). The final review (v11) read the v8b diff next.

**Final review (v11), three rounds.** A fresh reviewer session, Sonnet 5.5, read-only. Its `shell` tool was denied in every round, so it ran nothing; the orchestrator ran the suites and told it the results (`.opencode/graph/agentgraph-loops.json#v11`). Reports: `.opencode/artifacts/agentgraph-loops/v11/r1/review.md`, `.opencode/artifacts/agentgraph-loops/v11/r2/review.md`, `.opencode/artifacts/agentgraph-loops/v11/r3/review.md`. Each finding is by reading.

| Round | Verdict | Findings | Fixed, each with a test that failed first | Decided or documented instead of coded |
|---|---|---|---|---|
| 1, at `a14a2e5` | `unverifiable`, unfinished | 2 major, 10 minor | v8b round 2: six items, new tests that failed on the old code; suite 1695 to 1719 (`.opencode/artifacts/agentgraph-loops/v8b/r2/findings.md`). v7 round 3: eight rows, 8 web checks red before the edit, web 402 to 410, and Desktop check 75 (`.opencode/artifacts/agentgraph-loops/v7/r3/findings.md`). | Cancel of a paused, waiting or pausing run can wait up to 2 s; documented in `WEB_API.md` (`.opencode/artifacts/agentgraph-loops/v8b/r2/findings.md`). |
| 2, at `3421221` | `refuted` | 1 new major, 5 new minor | v8b round 3: 165 tests of `tests.test_looprun3` and `tests.test_loopapi` gave 6 failures and 3 errors on the old code; suite 1719 to 1732, 13 new tests (`.opencode/artifacts/agentgraph-loops/v8b/r3/findings.md`). v7 round 4: web 410 to 414 and Desktop check 76 (`.opencode/artifacts/agentgraph-loops/v7/r4/findings.md`). | A cursor from an earlier build is compared by count only; `docs/LOOPS.md` says so (`.opencode/artifacts/agentgraph-loops/v8b/r3/findings.md`). One finding did not reproduce: "A change is still being made" staying on screen; its check was kept (`.opencode/artifacts/agentgraph-loops/v7/r4/findings.md`). |
| 3, at `bb3bb31` | `confirmed` | no critical or major; 6 minor | none | The six minor findings were left unfixed on purpose: the reviewed commit is the shipped one (`.opencode/graph/agentgraph-loops.json#v11`). |

**The defect every earlier test had missed.** The Desktop loop section runs `python3 -P -m agentgraph.loopapi` (`desktop-plugin/src/plugin.cjs`). `agentgraph/loopapi.py` had no entry point, so the command printed nothing. The Desktop test harness used a fake interpreter, so every test passed. Round 1 found it by reading and the orchestrator confirmed it: at `a14a2e5` the command printed nothing and exited 0 (`.opencode/artifacts/agentgraph-loops/v7/r3/findings.md`). From the bridge code, a person would have seen "The loops command failed." for every loop request in Desktop. That is derived from the code and the empty output; no Desktop session was run to see it. The fix added the entry point; Desktop checks 75 and 76 now run the real `python3` and the real `agentgraph.loopapi` (`.opencode/artifacts/agentgraph-loops/v7/r4/findings.md`).

The other major findings: a `waiting` run with no controller could not be resumed or cancelled from the page (round 1); after a saved answer the page could lose its Resume button on a reload and show a 409 on every click (round 2). Both were fixed; round 3 marked both fixed (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).

**The six minor findings left** (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`):

- A live waiting run with a just-saved answer shows the three answer buttons for an instant; a second click gets a 409 (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).
- `cancel` can answer "requested" for a run that just ended `cancelled` (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).
- A controller that takes more than 2 s to exit after pausing leaves the cancel to the next resume (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).
- A note says "not started" when a controller started between the page's read and the answer (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).
- The answer lock goes on unlocked after 5 s (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).
- An error message in the entry point's reply has no length cap (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).

**Limits of the final review.** Nothing was run by the reviewer. It is the same model family as the implementers. No round read a test file, except the fake-interpreter part of `harness.mjs` in round 1. Round 3 did not read `style.css`, `WEB_API.md`, the terminal view's loop rows, `looprun.start` and `restore`, or Desktop checks 75 and 76 (it had only their map) (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`). The reviewed commit is `bb3bb31`. The only later code commit is the rebuilt Desktop bundle (`.opencode/artifacts/agentgraph-loops/v12/r1/final-values.md`).

### 3.7 vNext.5: not built

`.opencode/artifacts/agentgraph-loops/v1/r1/design2.md` lists six items as "not built unless a release above shows the need", each to stay "not built" with its reason in the final report. No measurement in this work showed a need. The design records no item-specific reason beyond that rule; where another source gives one it is named.

| Item | State | Reason on record |
|---|---|---|
| Parallel branches | not built | Rule above. `docs/LOOPS.md` says "Parallel branches are not built". |
| Nested loops | not built | Rule above. |
| A loop as a step of another loop | not built | Rule above. |
| Model routing per attempt | not built | Rule above. |
| A plugin that runs loops inside the interactive session | not built | Rule above. The v0 audit left plugin hooks untested and found the idle hook absent from the installed plugin types (`.opencode/artifacts/agentgraph-loops/v0/r1/audit.md`). |
| A native `/goal` bridge | not built | Rule above. `/goal` has no headless entry (`.opencode/artifacts/agentgraph-loops/v0/r1/audit.md`). |

## 4. Architecture decisions

The shape of the code is in `docs/ARCHITECTURE.md`. Decisions, each with its reason and what was rejected:

| Decision | Reason | Rejected alternative |
|---|---|---|
| The saved drawing and settings decide order and routes; code decides every transition; a model never decides whether a run passed | The audit found a self-loop was text for the orchestrator and nothing repeated it (`.opencode/artifacts/agentgraph-loops/v0/r1/audit.md`) | A prompt telling the orchestrator to loop |
| Only the check's exit code and a judge's verdict line end a run as passed; the agent's own "PASS" is never read | An agent's claim is not evidence (`agentgraph/looprun.py` docstring) | Trusting the agent's final word |
| A small Python controller calling `opencode run` once per attempt, one detached process per run | `run --session ID` continues a session at cached prices; a hung turn is stopped with a signal; cost is final at exit (`.opencode/artifacts/agentgraph-loops/v1/r1/design.md`) | A plugin using idle hooks (not in the installed types); native `/goal` (no headless entry) |
| Loop settings in their own file, `<structure>/.loops/<flow>.json`, beside the unchanged flow file | Flow files drop unknown head keys on every rewrite (`.opencode/artifacts/agentgraph-loops/v0/r1/audit.md`). The design said `structure/loops/`; the built folder is `.loops` (`.opencode/graph/agentgraph-loops.json#v2`) | Extending the flow file |
| Each run pins its own `definition.json`; editing the saved loop never changes a running or resumed run | Edits during a run must not alter it | Reading the saved loop at resume |
| Reconcile on resume: a turn that cannot be told from one still running is never sent again unless the person says `--redo` | Sending twice costs money and can repeat actions | Always re-sending |
| An unknown cost is never zero; with a cost limit set it ends the run | A limit that cannot be checked cannot be trusted (`docs/LOOPS.md`) | Counting unknown as zero |
| Graph rules live in code (`check`, `graph set`), not only in prompts | `check` accepted 38 of 51 invalid edits (`.opencode/graph/agentgraph-improve.json#n2`) | Stronger prompt wording |
| Proposals are files with a code-computed verdict; the person approves | The improvement cycle had never completed on real data (`.opencode/artifacts/agentgraph-improve/n7/r1/ranked.md`) | The old chat ritual |
| Install keeps user changes and writes the toolkit's version beside them | A reinstall lost four kinds of user change (`.opencode/graph/agentgraph-improve.json#n3`) | Overwriting |

### What this does and does not guarantee

From `docs/LOOPS.md`:

- The spend limit is an admission limit, not a billing cap. It is checked before a turn starts; a run can end above the limit by one turn.
- A check may run twice after a restart.
- There is no exactly-once. A message can still have run in a session that reconcile could not see; `--redo` can run a turn twice; `--accept` can take a turn that did not finish.
- An unknown cost is not zero. With a cost limit set, an unknown spend ends the run.
- A doubtful turn pauses and is not sent again unless you say so.
- Reconcile reads OpenCode 2.3.2's database and processes. A different version may answer `unknown`, which pauses the run.
- A real compaction was not provoked in tests.

## 5. Known limitations and gaps

Grouped. None softened.

### 5.1 Evidence limits

- One model (`anthropic/claude-sonnet-5-5`) in every measurement and demonstration. One repetition in the demonstrations (`.opencode/graph/agentgraph-loops.json#v5`, `.opencode/graph/agentgraph-loops.json#v10`).
- The delegation result covers two small, well-specified tasks (`.opencode/artifacts/agentgraph-improve/n10/r1/comparison.md`). Sample sizes are n = 3 for whole-run numbers, n = 2 per cell in the loop benchmark.
- The held-out set gave no confirmation for proposal `6f0e`, only no harm; p = 0.2; the case was hand-built because cases arise only from failed worker nodes (`.opencode/artifacts/agentgraph-improve/n25/r1/cycle.md`, `.opencode/graph/agentgraph-improve.json#n25`).
- The padding proposal `5ac2` had n = 2 (`.opencode/artifacts/agentgraph-improve/n25/r1/cycle.md`).
- Proposal C is an estimate, not a measurement (`.opencode/artifacts/agentgraph-improve/n8/r1/proposals.md`).
- The benchmark has a ceiling effect, the fixtures are 2 to 5 files written by the same author as the checks, and no loop-1 retry occurred (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`).
- Wall seconds in the benchmark are not clean: three runs shared the machine (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`).
- All five reviews of Part 2 (v11a rounds 1 and 2, v11 rounds 1 to 3) were by reading only, by the same model family as the implementer; Part 1's review also ran nothing (`.opencode/graph/agentgraph-loops.json#v11a`, `.opencode/graph/agentgraph-improve.json#n16`).
- Part 1 review limit: `check.py`, `contract.py`, `cost.py`, `ledger.py`, `snapshot_json.py`, `view.py` and the shell scripts were read only as a diff (`.opencode/graph/agentgraph-improve.json#n16`).
- Changed assertions: six older assertions in v8, eight in v8b round 1, three release 1 tests moved by the orchestrator in v6, three changes in v8b round 2 (one assertion replaced because it asserted the defect, one test that lost a last line with no assertion, one key set that gained `changed`), and one older view check in v7 round 3 whose expectation changed (the paused line shows the reason once) (`.opencode/graph/agentgraph-loops.json#v8`, `.opencode/graph/agentgraph-loops.json#v8b`, `.opencode/graph/agentgraph-loops.json#v6`, `.opencode/artifacts/agentgraph-loops/v8b/r2/findings.md`, `.opencode/artifacts/agentgraph-loops/v7/r3/findings.md`).
- Fixture only: a session that exists but holds no user message since the send; an HTTP test for the new 400 and 409 answers; the adopt path, `redo` and a real compaction on a real session (`.opencode/graph/agentgraph-loops.json#v8`, `docs/LOOPS.md`).
- Not shown through the page: Undo pressed in the release 1 demo, a saved goal, a fresh session per attempt, allow-asks, the spend limit stopping a run, narrow widths (`.opencode/graph/agentgraph-loops.json#v5`); the slow poll rate of a waiting run on a real run; the hash check after the two round-2 runs; "ask the agent again" on a real run (`.opencode/graph/agentgraph-loops.json#v10`).
- Not run: a paid bench session with the new check environment after n26 (`.opencode/graph/agentgraph-improve.json#n26`).
- The final review was by reading, found no critical or major defect in round 3, and left six minor findings unfixed (section 3.6). No round read a test file (`.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).
- No real Desktop app session drove the Loop section. Desktop checks 75 and 76 drive the bridge's loop command on the real `python3`, and a person has not looked at the panel (`.opencode/artifacts/agentgraph-loops/v12/r1/final-values.md`, `.opencode/graph/agentgraph-loops.json#v7`).
- Disagreeing sources, both kept: baseline spend ($1.73 and about $2.2); n8 and n9 worker context per turn; benchmark direct cost ($0.589 and $0.588) (`.opencode/artifacts/agentgraph-improve/n6/r1/baseline.md`, `.opencode/graph/agentgraph-improve.json#n6`, `.opencode/artifacts/agentgraph-loops/v9/r2/results.md`, `.opencode/graph/agentgraph-loops.json#v9`).
- The real-host check ran once, at the end, by the orchestrator, with `--size 160x45 --demo` (one tiny model call); it was not run at `--size 120x40` (`.opencode/artifacts/agentgraph-loops/v12/r1/final-values.md`).

### 5.2 Behaviour limits

- Whole-run context: the orchestrator's own turns are 81% to 92% of a delegated run, so worker savings barely move the total. Nothing measured lowers the orchestrator's share by 20% (`.opencode/artifacts/agentgraph-improve/n9/r1/after.md`, `PROGRESS.md`).
- Two behaviours fail in 3 of 3 runs and have no proposal: no task graph for a two-part task done directly (`p4-graph-two`), and a long explorer report not saved as a file (`p4-report-file`) (`PROGRESS.md`).
- `improve revert` appends no ledger row; `improve import-bench` has no cost-criterion option; a case arises only from a failed worker node (`PROGRESS.md`).
- `/api/improve` has no server-side confirm (the page asks first); same as the other POST endpoints (review finding 12, `.opencode/graph/agentgraph-improve.json#n16`).
- `busy` uses the root's session tree only and can disagree with `liveness`; `sources.CONFIG_DIR` ignores `OPENCODE_CONFIG_DIR`; the bench's config hash does not cover `structure/` (`PROGRESS.md`).
- The checker still accepts a graph marked passed while it is still running (case F6; the brief allowed it) (`.opencode/graph/agentgraph-improve.json#n21`).
- Loops are `A -> A` or `A -> J1 -> ... -> Jk -> A` only. A judge is a model. A fresh judge can read everything in the work folder. `no_artifact` proves only that a file exists. `allow_asks` lets the agent do anything its agent file does not deny. Each attempt starts a private OpenCode server (about 3 s). A stopped controller leaves its agent session in OpenCode (`docs/LOOPS.md`).
- A run started before v8b has no pin and cannot be resumed (`.opencode/graph/agentgraph-loops.json#v8b`).
- "Take the turn as done" after a kill leaves the spend unknown, so with a cost limit the run ends `limit` straight away and does not reach the verifier (`.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md`).
- View limits left: the flow's Cost block under the loop shows dashes; the no-check rule is derived in the page; a click on the loop's edge opens the flow panel, the history opens from the step chips (`.opencode/graph/agentgraph-loops.json#v4`, `.opencode/graph/agentgraph-loops.json#v7`, `.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md`). Fixed since: each version row says which settings differ from the saved loop; "Resumed. The run goes on." is replaced by a note when the run stops again; a paused reason is shown once, without command-line flags (`.opencode/artifacts/agentgraph-loops/v7/r3/findings.md`).
- `cancel` of a paused, waiting or pausing run can take up to 2 s; a plainly running run answers at once. A controller slower than 2 s leaves the cancel to the next resume (`WEB_API.md`, `.opencode/artifacts/agentgraph-loops/v8b/r2/findings.md`, `.opencode/artifacts/agentgraph-loops/v11/r3/review.md`).
- A cursor written by an earlier build has a count of user messages and no last message; reconcile compares the count only, so an equal-count case answers `not_sent` and one message can be sent again. Only runs left by an earlier build have such a cursor (`docs/LOOPS.md`, `.opencode/artifacts/agentgraph-loops/v8b/r3/findings.md`).
- The six minor findings of the final review (section 3.6).
- Part 1 review findings: 12 minor, 1 to 11 fixed, number 12 is a known limit above (`.opencode/graph/agentgraph-improve.json#n16`).

### 5.3 Not built

- vNext.5: the six items in section 3.7.
- Pinning one output as an example for later comparison (`.opencode/graph/agentgraph-loops.json#v7`).
- The two improvement skills still describe the older chat ritual: item K, not drafted (`PROGRESS.md`).
- Native `/goal` was not measured (section 3.5).

## 6. What changes for a user, and what waits

### Behaviour changes in the code already (committed on `design-lab`; undo with `git revert` of the commit)

- `install.sh` (`d5af126`): the toolkit's `AGENTS.md` arrives as `AGENTS.md.toolkit-new` when yours differs; unknown options are refused; files you edited are kept; `--list-backups` and `--restore` exist.
- `sync.sh` reads personal exclusions from the git-ignored `sync.conf`.
- `check` (`9f91822`) keeps `.opencode/graph/.history/` and rejects edits that undo passed work; `agentgraph graph set|event` changes one node under the lock.
- `agentgraph status --task T` prints confirmed against inferred state; the views label each running step live, quiet or orphan.
- `agentgraph cost` shows billed against messages against unreported spend.
- `agentgraph improve` and the page can approve, reject, revert and reopen proposals.
- `agentgraph loop ...` and the loop page (see `docs/LOOPS.md`). A flow `A -> A` without a saved loop behaves as before.
- Loop API, additive only: `answered` (boolean) on a run document and on each run row, true for a waiting run that holds a saved answer no controller has taken; `changed` on each version, the setting paths that differ from the saved loop (`WEB_API.md`, `docs/LOOPS.md`, `.opencode/artifacts/agentgraph-loops/v8b/r3/findings.md`, `.opencode/artifacts/agentgraph-loops/v8b/r2/findings.md`).
- A new entry point for the Desktop bridge: `python3 -P -m agentgraph.loopapi [--project DIR] [--structure DIR]` reads one JSON line on stdin and writes one on stdout (`WEB_API.md`, `.opencode/artifacts/agentgraph-loops/v8b/r2/findings.md`).
- `cancel` can wait up to 2 s on a run that is paused, waiting or pausing (section 5.2). A run's end is written by one code path, which also removes `cancel` and `pause` (`.opencode/artifacts/agentgraph-loops/v8b/r3/findings.md`).
- New files: `docs/CUSTOMIZE.md`, `docs/LOOPS.md`, this report, `docs/ARCHITECTURE.md`.

Nothing has been installed into the live configuration. No prompt, skill, setting or model was changed there.

### Waiting for your approval (copied from `PROGRESS.md`; paths under `.opencode/artifacts/agentgraph-improve/`)

| | Change | Evidence | File |
|---|---|---|---|
| A | Move the delegation policy out of the global `AGENTS.md` into the four `orchestrator_append.md` files, byte for byte. Workers stop reading it. | Workers' first turn -23% to -32%; orchestrator +0.3%. With B, in whole runs: worker context -58.9%, run cost -3.0% (n=3). | `.opencode/artifacts/agentgraph-improve/n8/r1/proposal-A.diff`; numbers in `.opencode/artifacts/agentgraph-improve/n8/r1/proposals.md`, `.opencode/artifacts/agentgraph-improve/n9/r1/after.md` |
| B | Per-agent tool denies in the plugin settings (for example, implementers lose web, image and `execute` tools). | impl-* -33%, verifier -31.5%, oracle -40.2%, orchestrator -7%. Risk: a denied tool a lane turns out to need. | `.opencode/artifacts/agentgraph-improve/n8/r1/proposal-B.diff`; numbers in `.opencode/artifacts/agentgraph-improve/n8/r1/proposals.md` |
| C | A round budget for long lanes (new session after N rounds). | Estimate only, not measured: 7-19% of the six long Opus sessions' $380.79. | `.opencode/artifacts/agentgraph-improve/n8/r1/proposal-C.diff`; numbers in `.opencode/artifacts/agentgraph-improve/n8/r1/proposals.md` |
| D | `AGENTS.md` line 9: the orchestrator may do work itself when the change fits in context it holds and has a check it can run (about 150 lines), instead of "under ~20 lines". | Delegating such tasks cost 2x, 1.5-1.8x time, same pass rate (n=3, small tasks, one model). | `.opencode/artifacts/agentgraph-improve/n10/r1/comparison.md` |
| J | The n25 proposal "do small steps solo" (one line in `normal/orchestrator_append.md`). Overlaps D. | Better on tune only: tune 3/6 to 5/6, held-out unchanged at 3/9, p = 0.2. By its own rule this is not enough to recommend. | `.opencode/artifacts/agentgraph-improve/n25/r1/cycle.md` |
| K | Point the two improvement skills at the `agentgraph improve` commands. | No measurement; a consistency change. **Not drafted.** | none |
| L | Three lines for `config/AGENTS.md` on `graph set`, `status` and flows. About 140 tokens more on every orchestrator turn. | No measurement. | `.opencode/artifacts/agentgraph-improve/review/proposal-L.md` |

Letters E to I name sections of an earlier review list (side chat round); `PROGRESS.md` holds no more detail on them.

Item K is not drafted. Nothing in this table is installed.

## 7. Tests

Final runs by the orchestrator, from the repository root, at the commit after the rebuilt Desktop bundle (`.opencode/artifacts/agentgraph-loops/v12/r1/final-values.md`). The reviewed code commit is `bb3bb31`.

| Suite | Command | Final result |
|---|---|---|
| Python | `python3 -m unittest discover -s tests -t .` | 1732 tests, OK |
| Web view | `AGENTGRAPH_VIEW_SHARDS=4 sh agentgraph/web/test/run.sh` (headless Chrome) | PASS, 414 of 414 |
| Terminal view | `node tui-plugin/test/run.mjs` | 194 passed, 0 failed |
| Desktop harness | `node desktop-plugin/test/harness.mjs` | 78 ok, 0 not ok |
| Real host | `python3 tui-plugin/test/real-host.py --size 160x45 --demo` (drives the real OpenCode terminal app; one small model call) | exit 0 |
| Fingerprint | `bin/agentgraph bench fingerprint` | config `86f9673fc3ff6a77` (347 files), ledger `e3340c0e1d2cbb5b` (11 files), unchanged |

Part 1 ended at 1444 Python tests (`.opencode/graph/agentgraph-improve.json#n26`). Counts by node, for the Python suite: 1490 (v2), 1522 (v3), 1584 (v6), 1679 (v8), 1695 (v8b round 1), 1719 (v8b round 2), 1732 (v8b round 3) (`.opencode/graph/agentgraph-loops.json#v2`; `.opencode/graph/agentgraph-loops.json#v3`; `.opencode/graph/agentgraph-loops.json#v6`; `.opencode/graph/agentgraph-loops.json#v8`; `.opencode/graph/agentgraph-loops.json#v8b`; `.opencode/artifacts/agentgraph-loops/v8b/r2/findings.md`; `.opencode/artifacts/agentgraph-loops/v8b/r3/findings.md`). Web: 402 (v7 round 1), 410 (round 3), 414 (round 4) (`.opencode/artifacts/agentgraph-loops/v7/r3/findings.md`, `.opencode/artifacts/agentgraph-loops/v7/r4/findings.md`).

Desktop bundle: rebuilt once with `node desktop-plugin/build.mjs` from the reviewed sources, 807,261 bytes, in the commit `038ef58` (`desktop-plugin/gui-experimental.cjs`). Check 63 failed before the rebuild because the bundle was stale; it passes after it. An open Desktop panel reloads the bundle. No person looked at the Loop section in the Desktop app (`.opencode/artifacts/agentgraph-loops/v12/r1/final-values.md`).

Temporary folders: the OpenCode temporary folder holds 33.5 GB in 1095 entries. Keep three until the review is decided: `p4-before` (a scratch config and ledger holding the proposals' state), `p4-after` and `p4-pad`. Everything else is leftover scratch from tests, trials and demonstrations; its evidence was copied into `.opencode/artifacts`. The largest are `trial-5` (8.6 GB), `trial-4` (6.1 GB) and `orbit-trial` (4.1 GB). Nothing was deleted (`.opencode/artifacts/agentgraph-loops/v12/r1/temp-folders.txt`).

Recorded spend per part, from the nodes (not a total): benchmark $2.4506 (`.opencode/artifacts/agentgraph-loops/v9/r2/results.md`), demonstrations $0.098 (`.opencode/graph/agentgraph-loops.json#v5`), $0.6446 (`.opencode/artifacts/agentgraph-loops/v10/r1/page-demo.md`), $0.0837 (`.opencode/graph/agentgraph-loops.json#v5a`), $0.123 (v8 real runs, `.opencode/graph/agentgraph-loops.json#v8`), and the Part 1 items in section 2. Total spend: $1440.38 billed at the closing sample, against a hard stop of $1600 (chat messages $476.04, 152 worker sessions $886.40, benchmark $27.48; average $15.18 an hour over the 1916 minutes since the first sample; per-lane amounts in the same file: `.opencode/artifacts/agentgraph-loops/v12/r1/spend.txt`). The sample was taken before the closing commit and the closing message, so the last few turns of the orchestrator are not in it.
