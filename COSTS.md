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
- The workers were cheap by comparison. Five verifier rounds cost $0.08.
- The orchestrator's prompt and rules are about 8k tokens, around 1 percent of a large context. Shortening them saves almost nothing.

## Changed

- `~/.config/opencode/opencode.json` now has `"compaction": { "buffer": 750000 }`. On a model with a 1M-token window the conversation is summarized at 250k tokens instead of 850k; models with smaller windows ignore the value and keep the default. Estimated effect on the measured chat: $319 becomes $195. Summaries lose detail, so the task graph file and its artifacts carry the state. To undo, delete that line.
- `AGENTS.md` has a short Cost section: one task per chat, long results go to artifacts, accept a verifier's pass on its evidence, scripts for mechanical work, settings changes in one batch.

## Tested, not applied: a one-hour cache

OpenCode can ask the provider to keep the cache for an hour (`settings.cache.ttl` on a provider or model). Tested through the proxy with Haiku 5.5 and a pause of 7 minutes 15 seconds:

| | Second turn read from cache | Second turn wrote again |
|---|---|---|
| With `"ttl": "1h"` | 30,770 tokens | 226 tokens |
| Default | 8,001 tokens | 22,989 tokens |

So it works. It was not applied, for two reasons:

- A one-hour write is billed at 2× the input price instead of 1.25×. It pays off for a session with pauses and costs more for a worker that runs straight through. The estimate for the measured chat is a further $31 saved on top of earlier compaction.
- OpenCode records the cost of every cache write at the five-minute price, so the dollars shown in the app and in the graph would read lower than the bill.

To apply it to the orchestrator's model only:

```jsonc
"providers": { "anthropic": { "models": { "claude-fable-5-2": { "settings": { "cache": { "ttl": "1h" } } } } } }
```

The what-if numbers are estimates: each recorded turn's cost is rescaled by how its priced tokens would change, using Anthropic's published ratios (cache read 0.1×, five-minute write 1.25×, one-hour write 2×, output 5×).
