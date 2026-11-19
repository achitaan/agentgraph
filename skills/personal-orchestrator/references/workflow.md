# Workflow decisions

Skilled work in the continuing conversation is the default. Use the
[task profiles](task-profiles.md) to change the assignment and selected guidance
without changing the model, effort or execution policy. The main profiles are
Engineer, Optimizer, Interface Builder and Researcher. Loop mode can apply to any
of them; Loop Engineer is the Engineer + loop preset.
Engineer, Loop Engineer, Interface Builder and optimization changes use
the existing implementer role; Researcher and analysis-only optimization use the
read-only investigator role. Investigator and Reviewer remain supporting
assignments with their existing read-only roles.

Profiles do not start agents; delegation follows `AGENTS.md` and the orchestrator
prompt, and a worker never starts one. Run the agreed checks and read their real
output, with bounded repairs as needed; a reported pass is a claim, and a check
is never weakened to get a pass. An independent review is the single `reviewer`
gate, dispatched by the orchestrator; if it cannot run, that stage remains pending. The task-profile guide defines shared limits
and domain checks.

| Role | Responsibility | Default model | Execution target |
|---|---|---|---|
| Coordinator | Scope work, choose actions, own state and acceptance | User-selected model in the continuing conversation | Sole dispatch authority |
| Implementer | Change owned paths and check acceptance | Inherit the user-selected model | Workspace-write |
| Investigator | Explain a bounded unknown with source evidence | Inherit the user-selected model | Read-only |
| Reviewer | Independently inspect behavior and its consumers | Inherit | Read-only |

Fast requires bounded scope, explicit acceptance, low consequence and low
uncertainty. A short auth, integrity or concurrency change is not automatically
low risk. The controller rejects an incompatible inherited effort; a fast preset may not
accept `ultra` in the recorded host catalog. An explicit effort override must
appear in the preview. Never silently substitute or downgrade a model.

Optional prompt profiles are separate from execution permissions. Profile/config
preferences describe exports; an existing execution session retains its captured
model, effort and policy. Updating a profile config does not rewrite that session.

## Loops proportional to the task

For an explicitly selected loop mode on any profile, follow the
[loop-engineering guide](loop-engineering.md): bounded work/check passes,
evidence-based diagnosis and the requested review mode before completion. Researcher
keeps its read-only role and uses source/proof checks; Optimizer uses measurements
and correctness checks. Shared allowances survive profile or mode changes.
The Loop Engineer preset selects Engineer with loop mode. No external process
is started by either selection.

When the user wants visible durable tracking, use [same-chat progress](same-chat-progress.md).
It saves checkpoints and gates completion without launching work. Native Goals
remain separately requested host features; never create one implicitly.

For a small unprofiled edit: inspect, change, run the relevant check, review the diff, finish.
That is a normal feedback loop, not a reason for an outer agent loop. Retry after
a new hypothesis or useful evidence. For substantial work, use one bounded packet
at a time, choosing only the roles that help.

Defaults are eight calls, two retries per logical packet, and one active worker.
Failed and interrupted launches count. Keep packet IDs stable across retries.
Evidence fingerprints must come from real observations, not invented digests.
A token threshold uses observed input plus output; cached input is a subset.
Unknown usage blocks further admission when that threshold is configured. This
cannot reserve provider tokens or cap a call already running.

## Skills, graphs and knowledge

Start with one entrypoint, normally use one or two, and add a third only for a
specific acceptance requirement. Select guidance separately for each role; the
coordinator receives no worker
guides by default. The runtime accepts at most three entrypoints per role, within
16,000 combined characters. Existing engineering, assurance, contracts, delivery and knowledge
skills consolidate Addy-inspired workflows; no 25-agent roster is needed.
Frontend and research guidance is loaded only for the actual task.

Use a source-backed codebase map for cross-module callers, dependencies or change
impact. Refresh the relevant subgraph rather than rebuilding an entire graph for
one known edit. A graph is evidence, not a scheduler. Use project-knowledge for
ADRs, linked notes and compact handoffs when they help future work. Obsidian can
view that Markdown; no vault plugin is required here.

A handoff identifies changed paths/findings, checks and results, unresolved risks,
and the next useful action. Distinguish self-review from explicitly requested
independent review. Compare total model usage
including failed attempts and review before claiming a cheaper preset saves cost.
