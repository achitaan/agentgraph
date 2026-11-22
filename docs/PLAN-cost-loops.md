# Plan: context cost, time-based loops, orchestration and self-improvement

Approved by the user on 2026-11-21 ("start and finish"). Task graph: `.opencode/graph/agentgraph-cost-loops.json`.
Spend stop: $250 for this chat (session `ses_e00c524f5ffe1ZnetjODXR8yVQ`, its workers, and bench, loop and probe runs
started after it began; it had spent $24 when the work started); no new lanes from $225; lanes on Sonnet 5.5;
`python3 .opencode/artifacts/agentgraph-desktop/usage/usage.py` before any spend.

Standing rules: no prompt, setting, skill or model change reaches the live config without the user's approval; build and
measure on a scratch copy first. Never print `opencode.json`, settings or session text. Code in `agentgraph` itself needs
no per-change approval. The graphs `agentgraph-improve` and `agentgraph-loops` are closed.

## What the stored data says (replay of 151 worker sessions, matches the bill within 0.4%)

- Worker message cost: cache read 52%, cache write 24%, output 23%.
- A summary (compaction) call is billed at 1.06 (Opus) to 1.16 (Sonnet) weighted tokens per context token, about ten
  times a normal cached read. Summary calls: about $69 of $886 worker spend.
- Opus lanes compact at 249k of context (57k after), Sonnet at 161k (46k after); growth 2.3k to 2.9k tokens a turn.
- Best rollover point with a summary that needs no model call: 100k to 120k. What-if saving (a replay, before
  anything was built): 22% of worker spend ($192), 29% on the nine long lanes. With today's summary call the best is 9%.
- **Measured after the build** (real runs on a scratch config, Sonnet 5.5, weighted tokens; `docs/ROLLOVER.md`,
  `.opencode/artifacts/agentgraph-cost-loops/r2/results.md`): a worker started by a chat, plugin on, mean 537,074
  (three runs) against 739,594 with it off (one run), -27.4%; a lane as its own session 568,856 (four runs) against
  688,226 (six runs), -17.3%; every run passed its check and its recall questions. These are long lanes that cross the
  rollover point; a short lane saves nothing. A main chat with the cover on (`chat_agents`, ask at 90,000): level
  with today on a six-message chat (358,381 and 360,569 against 363,572), no model summary, recall 4 of 4; with the
  needed global setting and no cover a session pays more for its summary (one run: +31.8%).
- Cache-read share of input is 93 to 98% per agent (chat 94.7%). Worker cold turns: 149, $83.90 (pause over 5 minutes
  97/$57.18, over an hour 20/$10.53, after compaction 16/$5.34, other 16/$10.85).
- Context growth per turn in long lanes: p50 1.4k, p90 4.8k, p99 15k; the largest 5% of turns add 26% of growth.
- Root chat: moving its compaction point with a model summary gains nothing (-12% at 100k, +2% at 150k, -2% at 200k).
- Installed: OpenCode app 2.3.2; plugin and SDK type files 1.19.3 show `experimental.session.compacting`
  (`context`, `prompt`), `experimental.compaction.autocontinue` (`enabled`) and the v2 client call `session.compact`.
  Supplying a ready summary with no model call (`ctx.session.hook("compaction")`, `event.result = { summary }`) is in the
  docs only.

## Phases

| Phase | Built | Expected | Estimate |
|---|---|---|---|
| 0 | `agentgraph cost` reports (rollover what-if, cache-read share per agent, worker cold causes, growth by tool); live hook tests on a scratch config | Phase 1 route chosen from facts | $8 |
| 1 | Automatic rollover for lanes near 110k from a handoff file | 15 to 20% off worker spend; no summary calls in lanes | $36 |
| 2 | Pause rule measured; the 28 cold turns with no known cause traced; prefix rules | about half of the $68 pause cost | $10 |
| 3 | Interval and time-boxed loops in the loop controller | unattended runs that cost nothing while waiting | $27 |
| 4 | `agentgraph node run`; graph fields; policy proposals; policy from one source | two orchestrator turns per node | $42 |
| 5 | `agentgraph review-copy`; end-to-end result required on a release node | a planted "cannot start" bug caught by running, 3 of 3 | $11 |
| 6 | Ledger row for every change; wider case evidence; cost criterion; harder bench scenarios | changes can be ranked | $16 |

Extras, in order, if budget remains: chat rollover ($8), `improve cycle` command ($10), output caps and batched tool
calls ($11), a desktop panel for timed loops ($6). Parked: effort levels, model choice, advisor tool, Batch API, more
prompt trimming, scheduled start (needs a launch agent and the user's approval).

### Phase 1 design

- Handoff file per lane (`.opencode/handoff/<session id>.md` in the lane's work folder unless Phase 0 shows a better
  place). Code writes the brief, check command, changed files, last check output and verifier findings. The lane writes
  decisions, dead ends and next step in a normal (cached) turn.
- Trigger: at a round end once context passes the threshold (start at 110k, from `agentgraph cost`), and before the
  first turn after a pause longer than the cache lifetime. OpenCode's own limit stays as the backstop. A stale or missing
  file falls back to the normal summary.
- Routes, chosen after Phase 0: (1) the hook supplies the summary with no model call; (2) a cheap summary model copies
  the notes (about 10%); (3) the loop controller starts a fresh session from the handoff file at round ends.
- Check: one long bench task, three runs each way: same pass rate, at least 15% lower cost, a usable session after a
  kill during rollover, a correct answer about an early decision.

### Phase 3 design

- Settings block `timing` in `structure/.loops/<flow id>.json` (unknown keys still refused; no block = today).
- Interval: `every` seconds; a watch command run by the controller with no model call; exit codes for nothing to do,
  wake the agent, finished; ends at `until`, `for` or `ticks`.
- Time-boxed: a wall-clock deadline (`until` or `for`); a passing check ends it early; rollover between attempts.
- Required to save: a spend limit and an end. Overlapping ticks are skipped. One command turns all timed loops off.
  A supervisor process restarts a controller that died; a step whose fate is unknown still pauses. A late wake (past
  the cache lifetime) starts a fresh session from the handoff file. `loop status` shows next tick, last result, spend.
- Not covered: surviving a reboot.

### Phase 4 design

- `agentgraph node run --task T --node N`: one background command; the controller runs implement, check and verify
  rounds; ends with one short line (pass, stall, limit, blocked) and the long report in a file; its spend is counted by
  `usage.py`.
- Graph: node fields for budget, loop run and times; `check` rejects a passed graph with a running node; `busy` agrees
  with `liveness`.
- Policy proposals: use `node run` for multi-round nodes; one dependent chain that fits in context stays in one session
  and lanes are for parallel or oversized work; no graph for solo two-part work; three policy files generated from one
  source (byte-identical first).
- Gate: installed only if a three-round bench task costs at least 15% less at the same pass rate.

### Approval points

1. Rollover plugin, its settings and one lane instruction line (after Phase 1; needs an OpenCode restart).
2. Any prompt fix from Phase 2.
3. Phase 4 policy changes.
4. The reviewer's permission to run `agentgraph review-copy`.
5. Proposal K (`.opencode/artifacts/agentgraph-k-shots/k1/r1/`).

After each phase: spend against its estimate; an overrun above 30% stops the work for a re-plan with the user.
