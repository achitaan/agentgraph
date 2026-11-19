# Bounded measured experiments

Use an experiment when candidate changes have a comparable score, evaluation is
affordable, and improvement requires trying alternatives. A one-line bug fix
usually needs a regression check; an isolated small performance problem may still
justify several measured candidates. Task size alone does not decide.

Before starting, record:

- Baseline revision, dirty-tree state, environment, inputs, seeds, and score.
- Editable paths, immutable evaluation code/data, metric direction, target, and
  separate correctness or quality regressions that must remain acceptable.
- A finite attempt or time budget, resource limits, and stopping conditions:
  verified success, exhausted budget, repeated lack of progress, or external block.
- One owner for continuation and one owner for commits/reverts. Reuse the existing
  Codex AutoResearch skill if its controlled Git experiments fit the request.

Measure the baseline before editing. For each candidate, state one hypothesis,
make a reversible change, run the unchanged evaluator and regression checks, and
record the result. Account for noise with matched conditions and repeats when
needed. Avoid tuning against held-out evaluation data.

Keep only improvements that satisfy the contract. Revert only the candidate's
changes; preserve the user's pre-existing work. An active AutoResearch controller
owns its experiment history and Git transitions; do not bypass it with manual
commits or resets. Do not nest Ralph or another continuation loop around it.

Log candidate, hypothesis, measured score, correctness result, keep/discard,
revision, and reason. Revalidate the selected candidate. Report budget exhaustion
as incomplete, even if it is the best result so far. Do not alter the evaluator,
lower thresholds, or disable permission checks to obtain success.

This is a general experiment policy inspired by measured optimization. It does
not bundle Karpathy's training code, require a GPU, or implement a loop engine.
