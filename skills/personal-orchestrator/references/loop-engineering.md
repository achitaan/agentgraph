# Loop engineering craft

The loop itself (loop item fields, the implementer's work/check passes, the
verifier check loop, no attempt limit, escalation, the `<loop>` report) is defined
in `AGENTS.md` and the orchestrator prompt
(`oh-my-opencode-slim/orchestrator_append.md`). This file adds only how to make a
loop item work well.

## Write an evaluation that decides

The loop is only as good as its evaluation. Before dispatching:

- Prefer a command the lane can run on its own scope: a targeted test, typecheck,
  build or lint. Use a file or content check only when no command exists.
- A bug fix needs an evaluation that fails first. If it already passes, it does
  not test the bug; add the failing test (in scope) or say so.
- An optimization names its metric, a representative input and the correctness
  checks that must keep passing. A faster result that breaks correctness is a FAIL.
- Research is checked against sources and proof steps: every material claim has
  a source, assumptions are stated, and a proof is not replaced by numbers.
- If the criteria can only be judged by reading, say so in the brief: the lane
  gets the narrowest check it can run itself, and a review follows.
- If the evaluation turns out to be wrong, the lane stops and reports it. It does
  not loosen it, and you do not loosen it to close the item.

## Shape each pass

One pass is one coherent change or investigation, then the evaluation, then the
actual output read, not assumed. Good passes:

1. Name the unmet criterion and the next hypothesis.
2. Change one thing.
3. Run the evaluation and keep the output.
4. Stop at PASS. Optional improvements are not a reason for another pass.

Identical failures call for diagnosis, not another try: reproduce in isolation,
add instrumentation, re-read the code being assumed about. If two passes bring no
new evidence, change the hypothesis. Further literature search needs a concrete
unresolved question, not another broad survey.

## When a pass fails

Record the kind of failure, because it decides the fix: `code` (the change is
wrong), `evaluation` (the check is wrong), `environment`, or `input` (something
needed is missing). Fix the check, the environment or the input before changing
code for the last three. Check whether the item is too big or the brief is wrong
before moving it up a tier.

## Feedback between verifier and implementer

Hand the verifier's findings back verbatim; a paraphrase loses the evidence.
Keep each round to the same loop item so the verifier checks against stable
criteria. A repeated finding is a stall: diagnose it rather than send it a third
time.

## Optimization loops

Keep a change only if the target metric improves and the correctness checks still
pass. Otherwise undo that one change, and only that change, then try the next
idea. Record every result, including discarded ones, so the same idea is not
retried. When the open question is an algorithm or a published method, ask
`researcher` first, using [paper research](paper-research.md), and hand the
sources, assumptions and a check that could disprove each hypothesis to the lane
as an artifact. Do not replay a research transcript.

## Stopping

Stop at PASS or when blocked. When blocked, keep the best working state, do not
revert or restart, and record the item as described in [blocked work](blocked-work.md).
