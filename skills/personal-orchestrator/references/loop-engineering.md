# Loop engineering mode

Apply this mode when the user asks to turn on loop engineering or requests a
profile “in loop mode.” Keep the selected profile, model, reasoning setting,
skills and permissions. **Loop Engineer** remains a convenient preset for
**Engineer + loop mode**. This is instruction-level behavior over existing tools;
it does not install an engine or start a background job. Keep work in this session;
delegation follows `AGENTS.md` and the orchestrator prompt, and a worker never
starts an agent. A profile or mode name alone starts no agent.

## Match the loop to the profile

| Profile | Work and feedback | Delegated execution role |
|---|---|---|
| Engineer | Implement a bounded change, check behavior, diagnose failures | Implementer |
| Interface Builder | Change the interface, exercise required states, inspect the result | Implementer |
| Optimizer | Establish a baseline, try a hypothesis, measure it and recheck correctness | Implementer for changes; investigator for analysis only |
| Researcher | Investigate, synthesize, check sources or proof steps, resolve a specific gap | Read-only investigator |

Profile determines expertise; mode determines iteration; execution role determines
the requested access boundary. Researcher in loop mode remains read-only when
delegated and returns findings to the coordinator. It cannot edit source or spawn
implementers. The coordinator saves an authorized report or dispatches the next
assignment. Independent Reviewer remains a separate read-only assignment.

## Set the scope and shared limits

Record the outcome, authorized scope, constraints and completion evidence before
starting. Use behavioral checks for engineering, fixed measurements plus
correctness checks for optimization, and source/proof checks for research.
Preserve unrelated work. If feedback is insufficient, add a focused check or
resolve the evidence gap; never weaken requirements to make a result pass.

Default to at most **three work/check passes** on the owning deliverable: initial
work plus two revisions. For code these are implementation/check passes; for a
research deliverable they are synthesis/verification passes. A new candidate or
corrective change after feedback uses a remaining pass, including review fixes.

Use one finite task deadline, at most **eight delegated role launches** total,
and any smaller usage/resource allowance. Research, implementation, investigation,
review and failed launches share those limits. A supporting research assignment
uses shared time and launches; it does not create another code-pass allowance.
Give it a bounded question and return condition. Role/profile changes, enabling
or disabling loop mode, and fresh sessions never reset spent allowances.
Reserve time for review and, when explicitly requested, launches for independent review. Larger limits require an
explicit user override. These instructions are not a guaranteed billing or
process-timeout cap.

## Run one useful pass at a time

1. State the unmet requirement or uncertainty and the next hypothesis. Make one
   coherent change or investigation using the current profile's relevant skills.
2. Check the result against actual behavior, measurements, sources or proof
   obligations. Keep compact findings, evidence and the next decision.
3. Continue only when new evidence supports a useful next step within the shared
   allowance. Identical failures need diagnosis. Further literature lookup needs
   a concrete unresolved question, not another broad survey.
4. Once acceptance checks pass, review original requirements, current artifacts,
   affected consumers and actual evidence. In direct work, do this here and label
   it self-review. An independent review is the orchestrator's single `reviewer` gate.
   Required independent review remains pending if it cannot run. Review covers
   the final deliverable and its research assumptions, not every exploratory edit.
5. Resolve concrete review defects within remaining passes, rerun affected checks
   and re-review the changed result using the requested review mode. Stop as soon as requirements,
   checks and review pass. Optional improvements do not justify more iterations.

If limits, cancellation, unavailable review, a blocker or no defensible next
hypothesis prevents completion, retain the best current artifact and report what
passed, what remains unverified and the next useful action. Do not restart or
change the acceptance target. A best-so-far artifact may still be incomplete.

## Research inside an implementation loop

For a kernel or algorithm task, the main conversation can own Optimizer + loop
mode. First inspect the baseline and local evidence. Use Researcher guidance in
this chat when outside knowledge or a mathematical uncertainty could change the
next candidate. The orchestrator may delegate that question (to `researcher`); a worker reports it under `Discovered`.
Use [paper research](paper-research.md) for relevant
recent and foundational papers. Pass back sources, assumptions, promising
hypotheses and checks that could disprove them; do not replay a research transcript.

The main conversation implements, measures and reviews. Separately requested
independent review follows the same limits. Do further research only for a specific new uncertainty. Use one
active worker by default; workers never delegate. Keep the selected model and
normally one or two relevant skills per role. A fresh worker gets a compact
packet; changing profile names in one conversation does not erase prior context.

Do not add an outer loop to an active controller or AutoResearch experiment;
use its existing limits and owner. Ralph remains a separately requested CLI
route with its own applicable skill and host settings.

## Examples

> Use Optimizer in loop mode for this kernel. Keep my model and reasoning
> setting. Use Researcher guidance here if published methods could help.
> Allow three work/check passes and thirty minutes total including self-review.
> Show progress in this chat; do not spawn anything. Preserve correctness and
> report measured improvements or remaining gaps.

> Use Researcher in loop mode to compare approaches to this question. Inspect
> relevant recent and foundational papers, keep assumptions and conflicting
> evidence explicit, and stop within three synthesis/check passes and twenty
> minutes including source review in this chat. Return findings without editing code.

## Visible checkpoints in this chat

For requested tracking, follow [same-chat progress](same-chat-progress.md). Begin
each bounded step before doing it, save real outcomes, and refresh the progress
file at phase changes with a brief chat update. Completion requires passing
checks and review tied to current declared files. The helper stores counters,
deadlines and evidence; it never launches work. An explicitly requested native
Goal may own continued work in this chat. Do not use it to relaunch the separate
controller or reset an exhausted allowance.

This mode and the Loop Engineer preset have not been benchmarked as separate
treatments. A small workflow smoke test cannot establish quality or cost gains.
