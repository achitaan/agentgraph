# Diagnose before retrying

Capture the smallest failing input, expected behavior, observed behavior, and
environment. Determine whether the failure is in the product, test fixture,
dependency, infrastructure, or current configuration. A passing substitute input
does not explain a failure in the real one.

Follow the data across the producer/consumer boundary. Find a known-good path or
revision and compare the relevant differences. Form a falsifiable hypothesis
and choose a narrow observation or test that distinguishes it from alternatives.
After a failed attempt, record what it ruled out before changing another thing.

For concurrency or flaky behavior, preserve timing and correlation evidence;
look at ownership, cancellation, retries, idempotency, and shared state. Retry a
transient operation only with a reason and a finite bound. Repeated identical
failure calls for inspecting the failure mechanism or environment.

Fix the cause within scope. Add a regression test when it captures meaningful
behavior and fits the project's test approach. Use the real entrypoint and data
shape; a mock that bypasses the failure is not evidence of a fix. Re-run the
relevant checks and expand only when changed dependencies or new failures justify
it. Report missing services or unavailable checks instead of calling them passed.
