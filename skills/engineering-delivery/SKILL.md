---
name: engineering-delivery
description: Turn a feature idea or substantial multi-step software change into a concise implementation plan and verified increments. Use for specifications, task breakdown, and coordinated implementation; handle small, clear edits directly.
---

# Engineering delivery

Keep the task in one continuing session where practical. This skill
guides work; the active host retains control of execution.

## Decide how much process helps

Before choosing a change, read the relevant project documentation and instructions,
then inspect the working-tree state, existing mechanisms and affected consumers.
Understand where the change belongs and which boundaries it must preserve.
Read only what the task needs; reconcile documentation with current code instead
of assuming either is complete. Define the requested result and the evidence
that will show it works. Reuse decisions already made; for a consequential unknown, state your assumption and proceed, or report it as BLOCKED or under `Discovered` for the orchestrator; do not wait.
An explicit design interview belongs to the existing Grill-Me/Grilling workflow.

- **Small, known change:** edit, run the relevant check, inspect the diff, finish.
  Fix a failed check in the same conversation. A loop runner, separate plan file,
  graph build, or new test suite is unnecessary for a trivial change.
- **Substantial change:** record a short acceptance contract and sequence of
  independently verifiable increments. Use [planning](references/planning.md)
  when requirements or dependencies need resolving.
- **Measured search:** use [bounded experiments](references/experiments.md)
  when repeated candidates can be compared against a stable metric. Repetition
  alone does not make a task an experiment.

## Implement and verify

Choose the smallest useful increment that exercises the real producer and
consumer. Check existing owners, implementations and framework APIs before
adding another mechanism. Prefer the project's patterns and checks; keep
responsibilities clear and abstractions proportional to the problem. Let names
and structure explain ordinary behavior, with comments for useful non-obvious
constraints. Use current library documentation when an API is uncertain.
Project and language specialists supply local conventions; this skill does not
impose one company's architecture, style or toolchain on other projects.

Keep the edit–check–fix cycle local. Each retry should test a new hypothesis or
respond to new evidence. Repeated identical failures call for diagnosis, not
another blind retry. Run the agreed check and read its real output: a reported
pass (a worker's or a verifier's) is a claim until the evidence is read, and a
check is never weakened to get a pass. Once acceptance and required checks pass,
review the final diff as your own self-check; independent checking is done by the verifier and the reviewer, which the orchestrator dispatches, so name that need in your report. Then stop unless new evidence reveals remaining work.

Trace cross-module changes with the available code map or source search. A graph
is a navigation aid; inspect current definitions and callers before editing.
Read a review, database, frontend, or science specialist only when the change
needs that expertise. Do not preload the whole toolkit.

In this OpenCode setup the orchestrator delegates by default; the task graph,
loop-item, verifier and reviewer rules are in `AGENTS.md` and the orchestrator
prompt, not here. A worker that reads this skill does not start agents: it
reports the need to the orchestrator under `Discovered` in its loop report.
Either way, specify ownership, inputs, acceptance, and dependencies, and keep one
continuation owner; this skill does not change models or bypass host trust and
permission checks.

## Finish the requested delivery

Preserve unrelated changes. Check the full diff, affected callers, tests, and
operational settings. Continue through commits, publication, and CI when those
actions are already authorized; do not invent a fresh approval gate. For a PR,
describe the problem, resulting behavior, validation, and outstanding risks.
Record the actual code/CI/merge/deploy state separately. A local pass is not proof
that users have the change.

For unfinished work, leave the current scope, file pointers, failed hypothesis,
exact checks, and next action. Prefer a compact handoff over restarting discovery.

Adapted in part from Addy Osmani's MIT-licensed Agent Skills; see LICENSE.addy.
