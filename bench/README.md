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

## Plain OpenCode against this toolkit

Three commands answer "on the same fixed tasks, is setup A better than setup B?":

```
agentgraph bench plain-config --to DIR                    # plain OpenCode: provider entries and pinned model only, agent build
agentgraph bench suite compare --setup toolkit=DIR1 --setup plain=DIR2 --reps 3 --budget 10
agentgraph bench card toolkit plain --suite compare --html card.html
```

- `plain-config` makes a scratch HOME like `scratch-config`, but keeps only an allow-list of settings keys (`PLAIN_KEYS`
  in `bench.py`). No agent files, prompts, plugins, skills, `AGENTS.md`, MCP servers or commands. It takes `--pin-model` too.
- Each copy holds a `bench-setup.json` marker (`plain` or `toolkit`). For a plain copy the runner uses its agent (`build`)
  instead of the scenario's agent. Copies never include `bench/scenarios` (the hidden checks).
- `suite NAME` runs every scenario in `bench/suites/<name>.json` on every `--setup LABEL=DIR`, interleaved (task 1 rep 1
  on A, then B, task 1 rep 2 ...) with the first setup alternating. `--budget` is one total over all setups; when it is
  reached the suite stops before the next run, says so and exits with code 3. Rows go to `bench/results/<LABEL>.jsonl`
  with a `suite` field.
- `card A B` reads existing rows and prints a scorecard; `--html FILE` also writes a self-contained page. `l-suite:METHOD`
  names the rows of `l-suite.jsonl` for one method. `--suite NAME` keeps only rows made by that suite.
- Every verdict (passing, cost, time, turns, agent calls) uses one rule over the tasks both setups have: a task goes to the
  setup that is better on it (passing: the larger share of runs passed; the others: the lower median), ties go to neither,
  and an exact two-sided sign test must give p <= 0.10 (5 of 5 tasks is enough, 4 of 5 or 5 of 6 is not). `same` means
  every task tied (passing) or within 5 percent (the others). At least 5 tasks are needed, so `hard` (4 tasks) and `basic`
  (3 tasks) can never give a verdict on their own; use `compare` (six tasks, plain unit-test checks only) for plain against
  toolkit. A run with an error or no session is broken: it counts as failed but is left out of cost, time, turns and calls.
  Anything else says "too few runs to say".

## Hard tasks (`bench/suites/hard.json`)

Four small projects where the quick fix passes the visible check and fails the hidden one:

- `h1-page-count`: a page-count bug whose obvious fix in the shared helper breaks the outbox, a second caller.
- `h2-contact-shape`: a three-step change (new record shape, migrate old data, update the report) that has to be done in order.
- `h3-todo-flags`: two features that both change one shared render file, without changing how existing tasks look or load.
- `h4-duration-spec`: one function with a ten-rule spec in the fixture; the visible tests cover three rules.

Each task folder also has `solutions/reference/` (a correct solution) and `solutions/naive/` (the hurried fix); both hold only
the files that differ from `fixture/`. They are never copied into a run's work folder (`prepare_work` copies `fixture/`
only); `tests/test_bench_hard.py` uses them to prove that the fixture fails the visible check, the naive fix passes it
and fails the hidden check, and the reference passes both. Hidden checks only test what the prompt or the fixture's own
spec states. The suite has 4 tasks, too few for a verdict alone.

spec states.

## What a run can touch

Shared with the real install: the scratch HOME links three real folders, so sessions are stored and costed in the real
database (`~/.local/share/opencode`), and OpenCode's state (`~/.local/state/opencode`), plugin package cache
(`~/.cache/opencode`) and logs (kept under those folders) are the real ones. Everything else a run sees is a scratch copy.

Each run works in a new temporary folder of its own, outside the scratch folder. The hidden check runs in a throwaway copy
of it (same path, symlinks left out, `hidden/` added), so the model's folder never holds `hidden/`. Both are deleted once the
row is written. `--keep-work` keeps the model's folder only; remove kept folders before another run. `run`, `suite` and `probe`
refuse to start if the scratch folder still has a `work/` folder from the old layout (delete it).

On macOS the host runs under `sandbox-exec` unless `--no-sandbox` is given (rows record `"sandbox": true` or `false`; without
`/usr/bin/sandbox-exec` the run is not sandboxed). The profile denies:

- reading any `bench/scenarios` folder, and the git folder, of this checkout, of every `git worktree list` path and of the live toolkit checkout;
- writing anywhere under the real config folder or under those checkouts.

It does not block the real database (OpenCode needs it, so a run can read other sessions' text: a known gap), the network,
running programs, or reading the rest of the home folder. Environment variables that point into a checkout or the config
folder are dropped, and the check runs with a minimal environment.

Run the suite from a work copy (a git worktree), not from the live install: the sandbox protects the checkout it runs from, and the live install is the one place a mistake would cost.

New flags: `--keep-work` and `--no-sandbox` (`run`, `probe`, `suite`); `--append` (`suite`: add rows to labels that already have
rows of that suite; rep numbers continue). A suite stops with exit code 3 when the budget is spent between rounds and 4 when two
runs in a row leave no session in the database.
