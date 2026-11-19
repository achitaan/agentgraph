# Workflow decisions

Craft for choosing how to run a task. The rules (roster, sizing, loop items, the
verifier loop, the reviewer gate, the task graph, artifacts) are in `AGENTS.md`
and the orchestrator prompt (`oh-my-opencode-slim/orchestrator_append.md`) and are
not restated here. The
[task profiles](task-profiles.md) say which agent and skills fit a kind of work.

## Choose the smallest shape

- A small edit: inspect, change, run the relevant check, read the diff, finish.
  That is a feedback loop already; it needs no graph, no packet and no extra call.
- One bounded change: one loop item to one implementer.
- Anything with two or more nodes: a graph. Before writing it, ask what can run
  at the same time; a dependency you invented turns a graph into a line.
- After a failed pass, change the hypothesis or add evidence before the next pass; repeating an identical pass adds nothing.

## Fast versus careful

Treat a task as low risk only when its scope is bounded, its acceptance is
explicit, the consequence of a mistake is small and little is uncertain. A short
change to auth, data integrity or concurrency is not low risk because it is
short: size it up a tier or add the reviewer gate. When unsure, start small and
move up on evidence.

## Tracking

The task graph file (`.opencode/graph/<task>.json`) is the record: statuses,
attempts, artifacts, changed paths. Update it as each node starts and finishes
rather than reconstructing it afterward. For a single lane with no graph, a
todo list with the attempt count and last result is enough.

## Handoffs and cost

A handoff names the changed paths or findings, the checks run and their results,
unresolved risks and the next useful action. It separates what a check showed
from what an agent claimed.

When comparing a cheaper arrangement with a dearer one, count the whole cost:
failed attempts, retries and review calls, not only the first successful lane.
Judge quality per accepted result, not token count or agent count alone.

## Skills, maps and notes

Name skills per agent, from that agent's own list. For cross-module callers,
dependencies or change impact, have `explorer` or `impl-claude` use `codebase-map`
and refresh only the subgraph the task touches. A map is evidence, not a plan.
For decisions worth keeping, write a short note with source, date and
uncertainty; do not keep transcripts.
