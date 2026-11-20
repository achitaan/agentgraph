# Design for actual consumers

Inventory callers and producers before editing an interface. Specify accepted
types, missing/null behavior, ordering, pagination, errors, retries, idempotency,
authorization, and serialization only where relevant. Prefer extending a stable
contract over forcing coordinated rewrites without a demonstrated need.

Test a representative consumer against the produced value or protocol, including
failure behavior. Generated types and isolated mocks may agree with each other
while both disagreeing with the runtime producer.

For migrations, separate compatibility introduction, data transition, consumer
switch, and eventual removal. Record deployment ordering, supported old/new
combinations, backfill resumability, validation counts, and rollback limitations.
Check that old code can read data produced by new code during a rolling deploy.
Destructive data changes need a concrete recovery strategy and the appropriate
existing authorization; reversibility of code is not reversibility of data.

For deprecation, identify active consumers, the replacement, evidence of adoption,
and a removal condition. Do not invent a deadline or silently remove compatibility.
Use a compatibility matrix when several versions coexist. Database implementation
belongs with the installed Supabase/Postgres specialist when applicable.
