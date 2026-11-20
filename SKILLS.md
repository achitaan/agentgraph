# Skills in this OpenCode setup

The skills come from the personal Codex toolkit (`achitaan/codex-personal-toolkit`), installed under `~/.config/opencode/skills`. Two things were done to fit them to OpenCode and this orchestration.

## Which agent may load which skill

Set in `~/.config/opencode/oh-my-opencode-slim.json`, the same in every preset. An agent sees only the skills listed for it; the orchestrator sees all. Each agent's details in the graph view list its skills; for the orchestrator they say `all · N` and open to the name and description of every installed skill.

| Agent | Skills |
|---|---|
| `orchestrator` | all |
| `oracle` | `math-research`, `performance-speedup`, `engineering-delivery` |
| `explorer` | `codebase-map` |
| `designer` | `design-taste-frontend`, `ui-ux-pro-max`, `impeccable`, `playwright-cli`, `clone-any-website` |
| `impl-openai` | `engineering-delivery`, `code-assurance`, `supabase-postgres-best-practices` |
| `impl-claude` | `engineering-delivery`, `code-assurance`, `codebase-map`, `supabase`, `supabase-postgres-best-practices` |
| `impl-hard` | `code-assurance`, `research-software-engineering`, `performance-speedup`, `ml-experiment-standards`, `sympy` |
| `researcher` | `exa-research`, `math-research`, `research-software-engineering`, `experimental-design` |
| `oracle-lite` | `math-research`, `performance-speedup`, `engineering-delivery` |
| `reviewer` | `code-assurance`, `research-software-engineering`, `ml-experiment-standards` |
| `council` | none |
| `verifier` | `code-assurance`, `playwright-cli` |

The reason for every choice is in [skills/AUDIT.md](skills/AUDIT.md). In short: implementers get delivery habits and code assurance; `impl-claude` adds interface contracts and the codebase map for cross-module work; `impl-hard` adds numerical and performance guidance; `verifier` and `reviewer` get code assurance; `designer` gets the interface skills; `explorer` gets the codebase map and library docs; `researcher`, `oracle` and `oracle-lite` get the research, math and performance guidance their questions need. `council` loads none.

## Skills reworded for OpenCode

`skills/` holds every skill as installed. Twelve were reworded for OpenCode, and [skills/OPENCODE-CHANGES.md](skills/OPENCODE-CHANGES.md) lists every replacement: host wording (Codex) became OpenCode or neutral, model tier names became roles, and instructions to start other agents became "a worker reports the need, the orchestrator dispatches". `personal-orchestrator` was rewritten as a short guide that defers to `AGENTS.md` and the orchestrator prompt. Third-party notices are in `skills/THIRD-PARTY-NOTICES.md` and `skills/licenses/`.
