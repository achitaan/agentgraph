---
name: project-contracts
description: Define project acceptance checks, design API or component interfaces, and plan compatible migrations. Use when requirements cross consumers or quality gates need an explicit contract; reuse established contracts for ordinary edits.
---

# Project contracts

Read existing specs, schemas, project instructions, check scripts, and consumers
before proposing new rules. Capture only the decisions this task needs.

## Acceptance and quality

Describe observable behavior with representative inputs, outputs, errors, and
side effects. Tie each required quality claim to an existing command or concrete
inspection. Distinguish enforced gates, measured baselines, and proposed targets.
Do not invent coverage/performance thresholds or declare prose “enforced” without
a check. Reuse the project's canonical configuration instead of duplicating it.

When a check fails, diagnose the product, fixture, and environment. Do not remove
assertions, suppress warnings, or relax a threshold merely to achieve a pass.
A justified change to a contract should explain the affected consumers and why
the old rule is no longer appropriate. Preserve authorization already given.

## Interfaces and migrations

For contract changes, read [interfaces and migration](references/interfaces.md).
Use existing database, framework, and library specialists for their concrete
mechanics. This skill does not install new checkers or write a universal gate.

Record a contract in the existing spec or concise decision note when it must
survive the conversation. Include the decision owner/status and open questions;
unresolved proposals must not become implementation requirements by repetition.

Adapted in part from Addy Osmani's MIT-licensed Agent Skills; see LICENSE.addy.
