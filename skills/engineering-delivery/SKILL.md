---
name: engineering-delivery
description: "Plan a feature or substantial multi-step change into verified increments; define acceptance checks, API or component interfaces and compatible migrations; diagnose or change CI/CD, releases, rollbacks and observability. Use for specifications, task breakdown, requirements that cross consumers, and delivery infrastructure. Handle small, clear edits directly; do not start deployments or monitors just because code changed."
---

# Engineering delivery

Keep the task in one continuing session where practical. This skill
guides work; the active host retains control of execution.

This skill also carries the former `project-contracts` and `delivery-operations` skills, as the sections
"Project contracts" and "Delivery operations" below. Read those sections when requirements cross
consumers or a quality gate needs an explicit contract, or when the work touches CI/CD,
releases, rollbacks, observability or operational readiness.

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

## Project contracts

Read existing specs, schemas, project instructions, check scripts, and consumers
before proposing new rules. Capture only the decisions this task needs.

### Acceptance and quality

Describe observable behavior with representative inputs, outputs, errors, and
side effects. Tie each required quality claim to an existing command or concrete
inspection. Distinguish enforced gates, measured baselines, and proposed targets.
Do not invent coverage/performance thresholds or declare prose “enforced” without
a check. Reuse the project's canonical configuration instead of duplicating it.

When a check fails, diagnose the product, fixture, and environment. Do not remove
assertions, suppress warnings, or relax a threshold merely to achieve a pass.
A justified change to a contract should explain the affected consumers and why
the old rule is no longer appropriate. Preserve authorization already given.

### Interfaces and migrations

For contract changes, read [interfaces and migration](references/interfaces.md).
Use existing database, framework, and library specialists for their concrete
mechanics. This skill does not install new checkers or write a universal gate.

Record a contract in the existing spec or concise decision note when it must
survive the conversation. Include the decision owner/status and open questions;
unresolved proposals must not become implementation requirements by repetition.

## Delivery operations

Inspect the repository's actual provider, scripts, triggers, permissions, and
environment. Reuse its release path. A skill is not authorization to publish,
deploy, message people, or activate monitoring; use the user's existing scope.

### CI and automation

Tie checks to the exact revision and configuration tested. Separate code failure,
fixture/environment failure, stale results, cancellation, and infrastructure
outage before retrying. Keep job permissions narrow; untrusted PR input must not
gain privileged tokens or writable deployment contexts through cached artifacts.

Check trigger filters, concurrency/cancellation, caching, artifact provenance,
secret exposure, and platform matrix when changing a pipeline. Reuse existing
scripts and pinned actions/dependencies. Report changed execution frequency,
resource use, and downstream scheduling effects. A local shell pass does not
validate a hosted runner or live service.

### Release and rollback

Define the artifact/revision, intended environment and audience, rollout steps,
health evidence, stop conditions, and rollback authority. Account for migration
ordering and data compatibility. A rollback must name what can be restored and
what requires forward repair. Do not create a recurring automation without a
request for one.

Track local verification, CI, review/merge, deployment, and production observation
as separate facts with timestamps and links. Continue authorized delivery work;
where authority or information is still missing at the consequential step, state your assumption and proceed if safe; otherwise report it as BLOCKED or under `Discovered` for the orchestrator, and do not wait.

### Observability

Instrument a question the operator needs answered. Define the event or metric,
unit, denominator, expected cadence, correlation identifier, and action on failure.
Use existing logging/tracing patterns. Bound labels/cardinality and volume, redact
secrets, and consider sampling and retention cost. Test that a failed operation
produces useful evidence without logging private payloads.

State important before/after effects on users, flags, eligibility, queues, traffic,
and cost. Passing CI proves neither deployment nor operational health.

Adapted in part from Addy Osmani's MIT-licensed Agent Skills; see LICENSE.addy.
