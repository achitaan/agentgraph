# What a chat costs, and what was done about it

Measured on one long orchestration chat (659 turns, context up to 848k tokens) with `bin/agentgraph cost`.

## Findings

| Where the money went | Turns | Cost |
|---|---|---|
| Warm turns: the context is read from the provider's cache | 620 | $138 |
| Cold turns: the whole context is written to the cache again | 39 | $181 |

- A cold turn cost $4.63 on average, a warm one $0.22.
- 21 of the 39 cold turns followed a pause of more than five minutes, which is the cache lifetime: $133. A pause costs about $1 for every 100k tokens of context.
- 5 followed a settings or model change in the middle of the chat ($13); 11 have no cause the records show ($33).
- Turns with more than 400k tokens of context cost $0.59 each; turns under 200k cost about $0.20.
- In that chat the workers were cheap by comparison: five verifier rounds cost $0.08. That does not hold in general. In a later chat with many long implementer lanes the workers cost more than the chat itself ($821 against $457), and 7 long worker sessions alone cost $415 (`bin/agentgraph cost`, 2026-11-21).
- The orchestrator's prompt and rules are about 8k tokens, around 1 percent of a large context. Shortening them saves almost nothing.

## Changed

- `~/.config/opencode/opencode.json` now has `"compaction": { "buffer": 750000 }`. On a model with a 1M-token window the conversation is summarized at 250k tokens instead of 850k; models with smaller windows ignore the value and keep the default. Estimated effect on the measured chat: $319 becomes $195. Summaries lose detail, so the task graph file and its artifacts carry the state. To undo, delete that line.
- 2026-11-20: the value is now `850000`, so on a 1M-token window the conversation is summarized at 150k tokens (token audit, about $13 a week less on long worker sessions; their summaries lose more detail). To undo, set it back to `750000`.
- The orchestrator's policy (in the `orchestrator_append.md` files, moved there from `AGENTS.md`) has a short Cost section: one task per chat, long results go to artifacts, accept a verifier's pass on its evidence, scripts for mechanical work, settings changes in one batch.

## A one-hour cache for the orchestrator

OpenCode can ask the provider to keep the cache for an hour (`settings.cache.ttl` on a provider or model). Tested through the proxy with Haiku 5.5 and a pause of 7 minutes 15 seconds:

| | Second turn read from cache | Second turn wrote again |
|---|---|---|
| With `"ttl": "1h"` | 30,770 tokens | 226 tokens |
| Default | 8,001 tokens | 22,989 tokens |

It is applied to the two Fable models the orchestrator runs on, and to nothing else:

```jsonc
"providers": { "anthropic": { "models": {
  "claude-fable-5-2": { "settings": { "cache": { "ttl": "1h" } } },
  "claude-fable-5-2-fast": { "settings": { "cache": { "ttl": "1h" } } }
} } }
```

- The cache timer already restarts on every read, so the cache only lapsed when nothing was sent for longer than its lifetime. The hour covers those pauses.
- A one-hour write is billed at 2× the input price instead of 1.25×. It pays off for a session with pauses and costs more for a worker that runs straight through, so workers keep five minutes.
- OpenCode records the cost of every cache write at the five-minute price, so the dollars shown in the app and in the graph read lower than the bill for these models. `agentgraph cost --trend` reports `billed`, an estimate of the real bill.

To undo, delete the `providers` block.

## Tracked over time

`cost-settings.jsonl` (git-ignored) records each change to the two settings above. `agentgraph cost --trend` splits the last 30 days of chats into periods at those changes and reports, for each, dollars per turn, the share spent on cold turns, and what pauses cost. `agentgraph signals` carries the same numbers.

The baseline before any change: 1,614 turns at $0.305 a turn, 56% of the spend on cold turns, $206 on pauses.

The `improve-orchestration` skills may propose a change to these two settings from those numbers, and only with the user's approval.

The what-if numbers are estimates: each recorded turn's cost is rescaled by how its priced tokens would change, using Anthropic's published ratios (cache read 0.1×, five-minute write 1.25×, one-hour write 2×, output 5×).

## Checking that it paid off

`agentgraph cost --check` sets the last 7 days (`--days N` changes that) against the baseline before the first change in `cost-settings.jsonl`. The recent window never starts before that first change. It prints seven lines, each with the word better, same, worse or not enough data and the two numbers behind it, then one overall line. `--json` prints the same as JSON.

1. Dollars a turn, in root chats, as `--trend` counts them.
2. Cold share: the share of that spend that went to cold turns.
3. Compactions: how many, what they cost, and their share of all spend.
4. Rollovers (from the rollover plugin's log): how many, and the most for one chat in one day. More than 6 in a day is worse.
5. Worker spend in long chats: the share of worker-session spend that went to chats over 300 turns or $25.
6. Whole chat re-cached: turns that read under 25,000 tokens from the cache and wrote over 30,000, two or more in a row in one chat. One such turn is an ordinary cold turn and is not counted. Any run is worse, with the line "caching stopped working in N chats".
7. Rollover gaps: the median minutes between rollovers of the same chat. Worse when a chat rolled more than 6 times in a day.

Lines 1, 2, 3 and 5 count as changed only when a number moves by more than 10% of the baseline (and, for shares, by more than 2 points). Lines 1 to 3 need 30 turns in both periods; with fewer, or with no recorded change to compare against, they say not enough data. The overall line is worse if any line is worse, else better if any is better.

Each run adds one dated JSON row (numbers and verdicts only, no chat text or names) to `~/.local/share/agentgraph/cost-check/cost-check.jsonl`. `AGENTGRAPH_COST_CHECK_HOME` names another folder, and `--check-log PATH` another file. The rollover log is read from `~/.local/share/agentgraph/rollover/rollover.log` (`AGENTGRAPH_ROLLOVER_HOME` or `--rollover-log PATH` change that); without it, lines 4 and 7 say not enough data.

Limits of the count: a worker session counts in the period of its last update; a rollover is a `rolled` event or an own compaction that got the plugin's summary; a compaction with no recorded cost counts as $0.

## Loops

A loop (`agentgraph loop`, [docs/LOOPS.md](docs/LOOPS.md)) spends money in a way the numbers above do not cover, so its rules are separate:

- The cost limit is checked before each agent turn, a judge's included. It stops the next turn; it does not cap a turn already running, so a run can end above the limit by one turn.
- An unknown cost is never counted as zero. With a cost limit set, an unknown cost ends the run `limit`; without one, only the attempt and time limits apply.
- A run's cost is kept per step, per session and in total in its `run.json` (`totals.cost`, `totals.cost_unknown_steps`).
- Measured cost on one model: a worker with a judge cost about twice a worker alone, 2.3 times a direct run in the second benchmark round, 1.9 times without the retried run. The benchmark showed no saving by loops. See `.opencode/artifacts/agentgraph-loops/v9/r2/results.md` and `docs/REPORT.md` section 3.5.
