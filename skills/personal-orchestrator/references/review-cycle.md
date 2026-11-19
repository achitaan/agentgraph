# Bounded delegated review cycle

> OpenCode note: this file describes the Codex toolkit's separate-session controller and its "spawn only when asked" stance. In this setup, follow `AGENTS.md` and the orchestrator prompt instead (task graph, loop items, verifier and reviewer); read this file as background only.

Use `orchestrator.review_cycle.ReviewCycle` only when delegation is authorized and
useful. This optional Python API is copied with the skill; installation and the
controller CLI do not activate it. Keep small clear changes direct.

The trusted host supplies a metered read-only coordinator callback and a
runtime-compatible worker runner factory. Use the existing `CodexRunner` for
real launches, preserving executable, authentication, permissions and cancellation
checks. `run()` returns a completed or incomplete record. Callbacks must retain
failed calls and verify routing; fabricated receipts are not host evidence.

## Callback and guidance

`coordinator(prompt, remaining_seconds)` returns `(decision, receipt)`. Existing
dictionary decisions remain supported. To enable bounded JSON recovery, return
the raw final JSON string with its receipt. The adapter saves both before parsing;
one malformed-JSON retry is allowed by default (`max_format_retries=0` disables
it). It consumes the same decision allowance and deadline, with no acceptance or
dispatch before successful decoding and schema validation. Duplicate keys and
non-finite constants are invalid JSON. Schema, admission, security, transport and
unknown-usage failures do not retry. A callback that parses and throws before
returning cannot use this recovery path; the frozen 0.3.0 benchmark host does that
and remains unchanged. No automatic format-fixing or inferred action is provided.

`guidance` may be a mapping from `coordinator`, `implementer` and `reviewer` to the
existing `{name, text}` lists. Omitted roles get no guidance. Select only relevant
entrypoints, within the existing three-entrypoint/16,000-character limit per role.
For compatibility, a list still supplies both worker roles and never the
coordinator. The adapter does not discover skills or load references itself.

The coordinator packet retains the original contract, current state, latest
handoff and unresolved rejection, without replaying the event log. Workers
receive that contract through the existing proposal packet; avoid duplicating it
in objectives or guidance. Reuse baseline facts and relevant source pointers in
`context`, checking freshness. Full-contract propagation can increase worker
context. The 26,000-character coordinator and 32,000-character worker caps remain;
oversized required material is rejected rather than truncated.

## Acceptance and correction

A normal cycle uses three coordinator decisions: dispatch implementation; accept
it and dispatch review; accept review and complete. Every proposal requires an
`evidence` list, which can be empty. Rejection needs a source/check reference,
original contract requirement and observed failure. Optional improvements or
inherited behavior outside that contract must not trigger code repair.

Code rejection remains the default. New evidence is required for repairs, which
invalidate old review and count as extra implementation. A rejected reviewer can
instead specify `rejection_kind: "invalid_review"` with evidence of the review's
fault. This allows one corrected independent review by default
(`max_review_corrections=0` disables it), using a new launch ID and the same
logical packet and implementation generation. Exact rejection evidence is
attached automatically, preserving the runtime retry gate. Correction never
accepts work; completion still needs acceptance of the subsequent review. A code
finding from that review follows the normal repair path. The trusted coordinator
must judge scope honestly; runtime checks cannot prove that a finding is valid.

The unchanged limits are eight worker launches, two code repairs and ten
coordinator decisions. Corrections consume those same allowances and deadline.
Repeated identical rejection, exhaustion or interrupted/unknown work returns
incomplete. There is no resume, concurrent cycle owner or automatic relaunch.
Synchronous Python cannot forcibly stop an uncooperative callback; the host must
honor its remaining deadline and meter every call.

## Verification capability and evidence

Before choosing this cycle, confirm that required independent checks can run
read-only. Reviewers and coordinator cannot create scratch files or temporary
directories. An implementer can run authorized checks needing writes and retain
commands/results for source review. If the contract requires an independent
execution that the host cannot provide, return incomplete; do not widen the
sandbox or count the check as passed. This is a capability boundary, not a new
verification service.

Supplied receipt details are revalidated before acceptance: cache reads plus
cache writes fit input tokens, and reasoning fits output. Details do not add to
ledger totals. A host wrapper must still propagate its own validation failures;
the runtime cannot detect a discarded error or validate data it never receives.

Version 0.3.0 completed no workflows in its sixteen original live trials; all
saved outputs were separately graded. Version 0.4.0 addresses specific failure
mechanisms with offline regression tests only. Historical source and results
remain frozen. No coding-quality, token, cost or latency improvement is established,
and no new live trial or global installation is implied. Defaults retain the
coordinator/reviewer and implementer presets at high effort only for this explicitly
chosen API; model arguments do not attest routing or entitlement.
