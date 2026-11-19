---
name: code-assurance
description: Investigate bugs, review code, design meaningful failure tests, or simplify behavior safely. Use for requested code quality, debugging, adversarial testing, and focused security review; use the project's specialist guidance where applicable.
---

# Code assurance

Establish the requested mode first. A review produces findings; it does not imply
permission to edit. An implementation or fix request includes fixing relevant
defects and verifying the result. Reuse the project's checks and conventions.

Read only the applicable guide:

- [Debugging](references/debugging.md): failures, flaky behavior, or error recovery.
- [Failure testing and security](references/failure-testing.md): how the behavior
  can break, meaningful test design, or a targeted security audit.
- [Review and simplification](references/review.md): full-diff assessment,
  consistency, refactoring, or interpreting review feedback.

Use source locations and executable evidence to support findings. Separate a
reproducible defect from a plausible risk, style preference, or unknown. Check
affected callers, consumers, tests, and operational configuration, including code
outside the edited lines. Do not reduce a failing quality bar to get a pass.

Choose review depth by consequence, not line count. A small change to auth,
money movement, concurrency, data deletion, or a public contract can deserve
independent review. Treat your own review as a self-check; independent checking is done by the verifier and the reviewer, which the orchestrator dispatches. Name the need in your report
when it is useful; do not ask for cross-model review after every cycle.

Stop when the requested assessment or fix and its required verification are
complete. Report important before/after effects, evidence, and unresolved limits.
Use installed Shopify or other project-specific review guidance where available.

Adapted in part from Addy Osmani's MIT-licensed Agent Skills; see LICENSE.addy.
