# Test the contract where it can fail

Identify the behavior and its real trust or state boundaries. Select cases that
could reveal defects: missing/duplicate/foreign inputs, invalid encoding, boundary
sizes, unexpected ordering, timeouts, cancellation, partial writes, retries, and
concurrent updates. Choose relevant cases rather than generating a checklist suite.

When a regression is reproducible, verify the test fails for the old behavior
and passes after the fix. Preserve observable contracts: values and types,
serialization, ordering, error behavior, permissions, side effects, and timing
constraints. Exercise both producer and consumer when data crosses components.

Property-based tests help when invariants cover many inputs: round trips,
conservation, idempotency, monotonicity, or isolation. Use an installed framework
or existing project mechanism; record seeds and minimized counterexamples. Mutation
testing can assess a suspicious test's strength when requested or justified by
risk. Neither requires installing a broad security pack.

For security, define the authorized scope and attacker-controlled surface. Trace
input to privileged actions and output: authentication versus authorization,
tenant isolation, injection, path traversal, redirects, secret exposure, and unsafe
defaults. Validate in local fixtures or authorized test systems. Avoid real data
destruction, uncontrolled traffic, or exploitation outside the approved scope.

Use current authoritative advisories for dependency findings; identify affected
versions and reachability. A scanner result needs triage, and a clean scan does
not prove safety. Report secrets by location/type, never by copying their values.
Preserve redaction, access controls, and the existing execution policy.
