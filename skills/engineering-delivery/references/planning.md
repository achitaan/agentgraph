# Plan around decisions and dependencies

Start from the user's problem and inspect the closest existing feature. A useful
brief contains the current behavior, intended behavior, scope, constraints,
acceptance examples, and unresolved choices. Omit sections with no decision value.
Read an existing spec before creating another one. Record assumptions distinctly
from accepted requirements; do not turn a suggestion into a commitment.

Split work along behavior or independently verifiable boundaries. For each
increment, identify the relevant files or component, its prerequisite, its result,
and its check. A serial list is enough for most features. Use a task dependency
graph only when blocked prerequisites or safe parallelism are otherwise unclear.

Example task edges: `define response contract -> implement endpoint -> exercise
consumer`. Arrows point from prerequisite to dependent: endpoint implementation
needs the response contract. They say nothing about which model or process should
run the task. Detect cycles and resolve the underlying decision before pretending
the work is ready to schedule.

Avoid parallel edits to the same files or shared interfaces. When the orchestrator
delegates, assign ownership and integrate one coherent increment before judging
the combined result. Unexpected scope growth should update the plan and risk
assessment, not silently multiply workers.

Keep the plan proportional. A focused fix can have acceptance in the conversation.
A migration may need a durable compatibility and rollback contract. Do not make
the user answer a standard interview when repository evidence already answers it.

Choose a visual plan only when layout, interaction states or competing designs
are the unresolved decision. Use an existing visual tool or a small local HTML
artifact with representative states and acceptance notes; keep ordinary plans in
text. Prototype controls are illustrative until implemented and checked in the
real product. Reuse the project's design system and browser verification.

For repetitive changes, prove one representative increment before processing a
bounded batch. Reuse its procedure and check, but inspect exceptions and shared
resources. Stop expansion when the premise fails. This can capture the useful
part of a software factory without a cloud fleet or a new scheduling framework.
