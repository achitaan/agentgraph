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
- `AGENTS.md` has a short Cost section: one task per chat, long results go to artifacts, accept a verifier's pass on its evidence, scripts for mechanical work, settings changes in one batch.

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
