---
name: delivery-operations
description: Diagnose or change CI/CD, prepare releases and rollbacks, and design useful observability. Use for delivery infrastructure or operational readiness; do not start deployments, monitors, or recurring jobs merely because code changed.
---

# Delivery operations

Inspect the repository's actual provider, scripts, triggers, permissions, and
environment. Reuse its release path. A skill is not authorization to publish,
deploy, message people, or activate monitoring; use the user's existing scope.

## CI and automation

Tie checks to the exact revision and configuration tested. Separate code failure,
fixture/environment failure, stale results, cancellation, and infrastructure
outage before retrying. Keep job permissions narrow; untrusted PR input must not
gain privileged tokens or writable deployment contexts through cached artifacts.

Check trigger filters, concurrency/cancellation, caching, artifact provenance,
secret exposure, and platform matrix when changing a pipeline. Reuse existing
scripts and pinned actions/dependencies. Report changed execution frequency,
resource use, and downstream scheduling effects. A local shell pass does not
validate a hosted runner or live service.

## Release and rollback

Define the artifact/revision, intended environment and audience, rollout steps,
health evidence, stop conditions, and rollback authority. Account for migration
ordering and data compatibility. A rollback must name what can be restored and
what requires forward repair. Do not create a recurring automation without a
request for one.

Track local verification, CI, review/merge, deployment, and production observation
as separate facts with timestamps and links. Continue authorized delivery work;
where authority or information is still missing at the consequential step, state your assumption and proceed if safe; otherwise report it as BLOCKED or under `Discovered` for the orchestrator, and do not wait.

## Observability

Instrument a question the operator needs answered. Define the event or metric,
unit, denominator, expected cadence, correlation identifier, and action on failure.
Use existing logging/tracing patterns. Bound labels/cardinality and volume, redact
secrets, and consider sampling and retention cost. Test that a failed operation
produces useful evidence without logging private payloads.

State important before/after effects on users, flags, eligibility, queues, traffic,
and cost. Passing CI proves neither deployment nor operational health.

Adapted in part from Addy Osmani's MIT-licensed Agent Skills; see LICENSE.addy.
