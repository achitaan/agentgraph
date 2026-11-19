# Compact task profiles

A profile chooses the current responsibility and relevant guidance. It preserves
the user's model and reasoning setting. Model choice follows the routing in the orchestrator prompt. The profile arrangement
has not been benchmarked; it does not promise the same quality or lower cost.

Do the main work in the continuing session. Delegation follows `AGENTS.md` and
the orchestrator prompt; a profile selection does not by itself start any agent,
and a worker never starts one. Direct work includes a clearly labeled self-review. Start with one
skill; normally use one or two. A third needs a specific
acceptance requirement, within the controller's three-entrypoint / 16,000-character
guidance limit when using that controller. Mandatory host and project instructions
are never removed to meet a skill budget. Read only needed references.

## Choose a task profile

Keep Engineer, Optimizer, Interface Builder and Researcher. Loop engineering is
an optional mode for any profile; Loop Engineer remains shorthand for Engineer
with that mode enabled. Investigator and Reviewer are supporting assignments.
The default is work, checks, self-review, then bounded corrections if needed.
An explicitly requested independent review requires an authorized separate
read-only reviewer; unavailable review remains pending. Use host-supported
delegation only when explicitly requested; this guidance does not automatically
invoke the experimental mode pipeline, a paid coordinator or its model defaults.

| Profile | Existing controller role, if used | Start with | Add only when the task requires it |
|---|---|---|---|
| Engineer | `implementer` | The relevant project/language specialist, or Engineering Delivery for substantial increments | Project Contracts for a consumer boundary; Context7 for an uncertain library API |
| Loop Engineer (preset) | `implementer` | Engineer guidance with loop mode enabled | Same task-relevant specialists as Engineer |
| Optimizer | `implementer` for changes; `investigator` for analysis only | Performance Speedup for a measured bottleneck, or Research Software Engineering for numerical work | Math Research for proof/reformulation; SymPy for exact calculations; relevant experiment guidance when evaluation needs it |
| Interface Builder | `implementer` | UI-UX-Pro-Max for app UI, Taste for a new landing page, or Impeccable for an existing-interface audit; choose the owner of this task | Playwright CLI for interaction evidence; a project specialist for its implementation rules |
| Researcher | `investigator` | Exa Research for public discovery, Research Summarizer for supplied sources, Codebase Map for repository evidence, or Math Research for a mathematical question | SymPy for exact calculations; Project Knowledge for a requested durable synthesis |

### Supporting assignments

| Assignment | Existing controller role, if used | Start with | Add only when the task requires it |
|---|---|---|---|
| Investigator | `investigator` | Code Assurance for a bug, or Codebase Map for a relationship question | Context7 for an uncertain dependency; Project Contracts when an interface is the disputed behavior |
| Reviewer | `reviewer` | Code Assurance for code; the relevant source/math/science specialist for other artifacts | Project guidance when its conventions affect correctness; the relevant UI skill for visual review |

These skill choices are alternatives, not bundles to preload. A small known edit can use
no extra skill beyond applicable project guidance. Non-coding tasks use their
matching research, writing, data or science specialist; do not force them through
an engineering profile.

Profile, mode and execution role are separate choices. “Use Researcher in loop
mode” keeps a read-only investigator when delegated; “Use Optimizer in loop mode”
uses an implementer only when changes are authorized. Mode selection never grants
write access or starts another coordinator. It changes how feedback is used,
not which subject skills are loaded.

The coordinator handles scope and acceptance with a compact brief. It need not
read every implementation skill the worker receives. These profiles do not
introduce new controller role values or register native agent types.

### Engineer: understand, reuse and verify

Start with relevant project documentation and instructions, then inspect current
code, existing owners and affected consumers. Understand the architectural
boundaries and how the project checks its behavior. Missing or stale docs are
uncertainties to resolve, not rules to invent.

Prefer a suitable existing mechanism before adding one. Keep the change cohesive,
use local conventions, and verify the real behavior and its affected consumers.
Engineering Delivery supplies general planning and verification habits; choose
project/language specialists when local implementation or review rules matter.
Adapt depth to the task rather than requiring a fixed document list or applying
one company's conventions everywhere.

### Review and repair

In direct work, review the final diff, affected consumers and evidence here.
Do not call that independent review. The following separate-review procedure
applies only when the orchestrator dispatches a reviewer; a profile selection alone starts no agent.

1. Complete the bounded work and relevant checks. Reserve time and launch
   capacity for a fresh read-only reviewer using the selected model and effort.
   Keep one active worker; the continuing conversation dispatches the review.
2. Give the reviewer the original requirements, current artifact/diff, affected
   source or supporting evidence, actual check results and selected domain
   guidance. Do not substitute the worker's summary for accessible evidence.
3. Require concrete findings tied to the contract. For Engineer, trace affected
   consumers and compatibility; for Interface Builder, inspect required UI
   states and accessibility; for Optimizer, check baseline/measurement validity
   and correctness constraints; for Researcher, check sources, material claims,
   assumptions and proof steps. Loop Engineer uses the domain checks for its
   task and verifies that feedback covers the original requirements. Numerical
   checks alone are not a proof.
4. Return concrete defects to the worker for correction, rerun affected checks,
   and independently re-review the changed result. Use an Investigator only when
   the cause is unclear or repeated failure needs diagnosis. Optional extensions
   do not justify repair loops; a disputed finding needs evidence and scope review.
5. Default to at most two repair cycles and eight delegated role launches in
   total, within the task's finite deadline and any usage allowance. Review,
   investigation and retries consume that same allowance. If required review,
   evidence or a supported launch is unavailable, or limits are reached, report
   the result as incomplete with review pending. Do not substitute self-review
   or another model without the user's direction.

Profile switching preserves completed work, current evidence
and remaining allowances; it does not reset the limits or validate a stale review.
Within one composed task, a supporting Researcher memo is intermediate evidence:
the final review covers the deliverable and the research claims it uses. A
standalone research report needs its own verification, with independent review
only when requested. Do not add a separate review after every internal handoff.

### Loop mode across profiles

Use [loop engineering](loop-engineering.md) when selected for any profile.
Engineer and Interface Builder iterate changes and behavioral checks; Optimizer
iterates measured candidates while preserving correctness; Researcher iterates
findings and source/proof checks. Default to three work/check passes including
initial work and two revisions, with the requested review mode before completion.
Keep one shared allowance across supporting research, implementation and review.
The Loop Engineer preset selects Engineer plus this mode. Existing profile,
model and permissions are preserved when the user simply turns loop mode on.

### Research, mathematics and optimization

Researcher starts with a bounded question and source scope. Separate supported
findings, assumptions and unresolved questions. When a research-dependent choice
could benefit from published work, follow [paper research](paper-research.md):
look for relevant recent work and foundational papers, inspect the methods and
limits, and return source-backed implications for this task. A supplied-source
summary stays within its requested scope; do not search by default when current
project evidence already settles the question. For mathematical work, preserve
assumptions and distinguish a proof from numerical evidence. A delegated
researcher returns a report without editing project source; saving that report
uses an authorized write outside the read-only worker.

Optimizer starts with a baseline, metric, representative evaluation, constraints
and a finite stopping allowance. Recheck correctness alongside speed, memory,
cost or numerical accuracy. Do not turn a proof question into a benchmark or a
small edit into an open-ended search.

| Actual uncertainty | Relevant guidance to select |
|---|---|
| Difficult proof, algorithmic reduction or reformulation | Math Research |
| Exact algebra, calculus or symbolic calculation | SymPy |
| Numerical stability, scientific code or simulation correctness | Research Software Engineering |
| Runtime, memory, GPU or query bottleneck | Performance Speedup; the applicable project/database specialist |
| Predictive ML training or evaluation | ML Experiment Standards; Experimental Design when planning the comparison |

This is a selection map, not a science bundle. Keep one or two relevant skills
and add further guidance only for an actual requirement. A coding-workflow
benchmark does not establish research accuracy or mathematical capability.

## Swap in one sentence

- “Use Engineer; keep the model and reasoning setting; follow this project's implementation conventions.”
- “Use Loop Engineer here; keep the model; allow three work/check passes and twenty minutes total, including self-review. Show progress without spawning anything.”
- “Use Optimizer in loop mode; explicitly spawn one bounded Researcher for the algorithm question and one independent reviewer after measurement.”
- “Use Researcher in loop mode; compare relevant recent and foundational papers, verify the central claims, and stop within three synthesis/check passes.”
- “Use Optimizer; keep the model; measure this bottleneck with Performance Speedup, preserve correctness, and try at most two candidates.”
- “Use Interface Builder; keep the model; use UI-UX-Pro-Max and Playwright.”
- “Use Researcher; keep the model; compare these supplied papers with Research Summarizer.”
- “Use Researcher; keep the model; use Math Research for this proof and SymPy only if exact calculations help.”
- “Use Investigator; keep the model; use Code Assurance; explain the cause without editing.”
- “Use Reviewer; keep the model; use Code Assurance; check the original contract and affected consumers.”
- “Use direct skilled work without a separate reviewer for this small change; keep the model.”

A skill or model explicitly named by the user takes precedence over these
starting points. A role swap alone never selects a cheaper worker or escalates
to a larger model. If the user explicitly chooses a different model or effort,
apply it at a supported dispatch boundary and retain the previous evidence.

For a delegated assignment, keep a compact packet:

```text
Task profile: Interface Builder
Mode: direct or loop (only when selected)
Execution role: implementer
Model / reasoning: inherit the user's selection
Selected skills: ui-ux-pro-max, playwright-cli
Objective and accepted input/failure boundaries: ...
Owned paths and affected consumers: ...
Current source and check evidence: ...
Acceptance, deadline and remaining allowance: ...
```

The host still needs its supported role name and explicit execution settings.
A profile line describes the work and its review; the host still controls
execution and permissions.

## Context and review boundaries

Switching profiles in the same conversation does not erase previously read
skills or history. When the user requests a fresh worker, finish or
checkpoint the active assignment, then provide only the current task packet,
source pointers, relevant check results and bounded findings. Keep one active
worker when authorized; workers do not delegate. Requested independent review
uses a separate session; relabeling the implementer's conversation is not an
independent review.

The role's skill list guides what is read. It does not hide the host's installed
skill catalog or enforce a hard context/token limit in the main conversation.
The execution host remains responsible for read-only and workspace-write
permissions; changing a profile cannot widen or retroactively change them.

Review receives the original requirements, accepted input forms, affected source,
diff and current check results. A blocker needs a concrete requirement and
reproducible evidence. Distinguish a demonstrated defect from an unresolved input
boundary or a proposed extension. Resolve a disputed boundary before asking a
worker to implement new behavior. Passing checks is evidence, not proof that the
checks cover every requirement.
