# Toolkit bench

Fixed tasks, run on an isolated scratch copy of the OpenCode config, to measure the toolkit before and after a change
(tokens, cache reads and writes, cost, time, agent calls, correctness, recovery). Code and the full description of
isolation, rows and recovery: `agentgraph/bench.py` (module docstring).

```
agentgraph bench scratch-config --to DIR                 # DIR under /private/var/folders/.../T/opencode/, new and empty
agentgraph bench probe orchestrator impl-openai explorer --label base --config DIR
agentgraph bench run s1-simple s2-parallel s3-workflow --label base --config DIR --reps 3 --budget 6
agentgraph bench run s3-workflow --label base-rec --config DIR --interrupt-after 50       # recovery variant (stop at 50 s, resume the same session)
agentgraph bench report base                              # medians over reps, per scenario
agentgraph bench report base after                        # two labels: the change in percent
agentgraph bench fingerprint                              # hashes of the live config + ledgers (before/after a bench: equal)
```

- A scratch copy is a scratch HOME (`DIR/home`): `~/.config/opencode` resolves to the copy, so prompt text is unchanged
  and `agentgraph record` hits an empty scratch ledger. Make one copy per configuration under test (the "before" copy
  and the "after" copy); `--pin-model` (default `anthropic/claude-sonnet-5-5`) makes prompt text the only difference.
- Sessions are stored in the real database as archived sessions titled `agentgraph-bench: <scenario> <label> rN`, so
  they count against the spend limit. Each row has the cost; `--budget` stops a run before it starts a rep past the amount.
- `bench/scenarios/<id>/`: `scenario.json` (agent, prompt, check, timeouts), `fixture/` (copied to a fresh git folder per
  rep), `hidden/` (copied in after the run, then `check` runs). `s1-simple` one bug; `s2-parallel` two independent
  features; `s3-workflow` a dependent three-step task asked to run as a task graph (the recovery variant stops it with
  `--interrupt-after` and resumes the same session).
- Results: `bench/results/<label>.jsonl`, one row per rep. Never compare rows made with different `config_hash` values
  as "the same config"; the hash is what changed.
