# Review the behavior and its consumers

Inspect the complete changed scope and the baseline it changes. Trace affected
callers, config, schemas, tests, error paths, and downstream consumers. A codebase
map can prioritize inspection but cannot prove the absence of dynamic callers.
For each actionable finding, name the trigger, consequence, source location, and
supporting evidence. Keep optional conventions separate from correctness defects.

Review feedback may express a general convention. Inspect comparable changed
code and apply that convention consistently when fixes are authorized. Preserve
the user's scope and unrelated edits. Do not manufacture findings to fill a quota.

For simplification, establish what must stay the same before editing. Prefer
existing helpers, direct control flow, and fewer redundant representations. Avoid
new abstractions with only one unexplained use. Replacing an object with a Map,
changing iteration order, swallowing an exception, or making a lazy operation
eager can change behavior even if the result looks cleaner.

Compare public values/types, errors, serialization, side effects, concurrency,
and performance-sensitive behavior. Use configured format/lint/type tools; do not
replace repository conventions with personal stylistic rules. Inspect the final
diff for weakened assertions, skipped checks, suppressed errors, or altered
thresholds. Legitimate changes to checks need a reason tied to changed behavior.

When independent reviewers disagree, resolve the disputed trigger against source
or a focused check. More agreeing agents are not stronger evidence by themselves.
Give a fresh reviewer the requirements and artifacts before the implementation's
defense. Another review is useful only for an unresolved consequential question.

For a recurring reproducible defect, prefer one focused regression or existing CI
check over more review prompts. Automatic checks should report evidence; adding
hooks that mutate code, retry agents or publish changes needs its own scoped task.
