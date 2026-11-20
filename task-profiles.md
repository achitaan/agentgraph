# Task profiles

A profile is a way to think about a kind of work: which agent does it in this
setup, which one or two skills to name in that agent's brief, and what the
check should look like. A profile starts no agent and picks no model. Dispatch,
sizing, loop items, the verifier loop and the reviewer gate are defined in
`AGENTS.md` and the orchestrator prompt
(`oh-my-opencode-slim/orchestrator_append.md`); this file adds only the craft.

An agent can load only the skills on its own list (`orchestration/SKILLS.md`),
and a skill the brief does not name is usually not loaded. Name one skill, at
most two, in the `Context` field of the loop item. A skill the user names takes
precedence over these starting points.

## Agent to skills

Starting points, not bundles. Pick the line for the agent doing the work, then
one or two skills from it that fit the item.

- `impl-openai`: `engineering-delivery`, `code-assurance`
- `impl-claude`: `project-contracts`, `codebase-map`
- `impl-hard`: `performance-speedup`, `research-software-engineering`
- `verifier`: `code-assurance`, `playwright-cli`
- `reviewer`: `code-assurance`, `project-contracts`
- `designer`: `ui-ux-pro-max`, `playwright-cli`
- `researcher`: `exa-research`, `research-summarizer`
- `explorer`: `codebase-map`, `context7-mcp`

Other skills on the same lists fit narrower needs, for example `sympy` or
`math-research` for exact or proof work, `ml-experiment-standards` or
`experimental-design` for evaluation design, `supabase-postgres-best-practices`
for database changes, `design-taste-frontend` or `impeccable` for a new page or
an existing-interface audit.

## Kinds of work

| Work | Agent | Skills to name | Evaluation looks like |
|---|---|---|---|
| Engineer | `impl-openai`; `impl-claude` when big or cross-module; `impl-hard` when subtle | `engineering-delivery` for a substantial increment, `code-assurance` for a bug or risky change | A targeted test, typecheck, build or lint on the owned paths; for a bug, one that fails before the fix |
| Optimizer | `impl-hard` for subtle numerics or performance; `impl-openai` for a mechanical change | `performance-speedup` for a measured bottleneck, `research-software-engineering` for numerical code | A named metric on a representative input, plus the correctness checks that must keep passing |
| Interface Builder | `designer` | `ui-ux-pro-max` for app UI, `design-taste-frontend` for a new landing page, `impeccable` for an audit | Required states exercised in a browser (`playwright-cli`), plus a read of the result |
| Researcher | `researcher` for judgment about papers or techniques; `explorer` for codebase search and library docs | `exa-research` for public discovery, `research-summarizer` for supplied sources, `codebase-map` or `context7-mcp` for `explorer` | Claims tied to sources, assumptions and open questions separated |
| Investigator | `explorer` to map code; `impl-hard` when a persistent bug needs a fix | `code-assurance` for a bug, `codebase-map` for a relationship question | A cause shown by evidence, not a guess |
| Reviewer | `reviewer` for the final gate; `verifier` for one item inside a loop | `code-assurance` for code, `project-contracts` when an interface is disputed | Findings tied to the original requirements and the real changed files |

Non-coding work uses the matching research, writing or science skill; do not
force it through an engineering profile. A small known edit needs no skill.

## Craft per kind

**Engineer.** Read the project's own docs and the current code before the brief:
existing owners, affected consumers and how behavior is checked. Missing or stale
docs are uncertainties to resolve, not rules to invent. Prefer an existing
mechanism to a new one, and put the affected consumers in `Context` so the lane
checks them.

**Optimizer.** The brief names the baseline, the metric, a representative input,
the constraints and when to stop. Speed is not a pass unless correctness holds.
Do not turn a proof question into a benchmark, or a small edit into an open
search; if the algorithm is the open question, send it to `researcher` or
`oracle` first and hand the result to the lane as an artifact.

**Interface Builder.** Say which states matter (empty, error, loading, small
screen, keyboard) and what evidence shows them. A screenshot or browser run is
evidence; a claim that it looks right is not. The designer owns visual choices;
an implementer asked to change UI reports design questions under `Discovered`.

**Researcher.** Bound the question and the source scope. Separate supported
findings, assumptions and unresolved questions. When a published technique could
change a choice, follow [paper research](paper-research.md). Do not search when
the project's own evidence already settles it. Distinguish a proof from numerical
evidence. A read-only worker returns a report; the orchestrator saves it as the
node's artifact.

**Investigator.** Ask for the cause and the evidence, not a fix, unless the item
says otherwise. A cause that two attempts could not find goes to `oracle` with a
self-contained brief.

**Reviewer.** Give it the objectives, the changed files and the check evidence,
never the implementer's own account. Trace affected consumers for code, required
states for UI, baseline validity for optimization, and sources and proof steps
for research. A blocker needs a concrete requirement and reproducible evidence;
separate a demonstrated defect from an unsettled input boundary or a wished-for
extension. Passing checks are evidence, not proof that the checks cover every
requirement.

## Uncertainty to skill

| Actual uncertainty | Skill |
|---|---|
| Difficult proof or reformulation | `math-research` (`researcher`, `oracle`) |
| Exact algebra or calculus | `sympy` (`impl-hard`, `oracle`) |
| Numerical stability or simulation correctness | `research-software-engineering` (`impl-hard`, `researcher`) |
| Runtime, memory or query bottleneck | `performance-speedup` (`impl-hard`, `oracle-lite`) |
| Predictive ML training or evaluation | `ml-experiment-standards`, `experimental-design` (`researcher`) |

A coding-workflow check does not establish research accuracy, and a numerical
check alone is not a proof.
