# Skills in this OpenCode setup

The skills come from the personal Codex toolkit (`achitaan/codex-personal-toolkit`), installed under `~/.config/opencode/skills`. Two things were done to fit them to OpenCode and this orchestration.

## Which agent may load which skill

Set in `~/.config/opencode/oh-my-opencode-slim.json`, the same in every preset. An agent sees only the skills listed for it; the orchestrator sees all. Each agent's details in the graph view list its skills.

| Agent | Skills |
|---|---|
| `orchestrator` | all |
| `oracle` | `math-research`, `sympy`, `performance-speedup`, `project-contracts` |
| `explorer` | `codebase-map`, `context7-mcp` |
| `designer` | `design-taste-frontend`, `ui-ux-pro-max`, `impeccable`, `playwright-cli`, `clone-any-website` |
| `impl-openai` | `engineering-delivery`, `code-assurance`, `shopify-ruby` |
| `impl-claude` | `engineering-delivery`, `code-assurance`, `project-contracts`, `codebase-map`, `shopify-ruby` |
| `impl-hard` | `code-assurance`, `research-software-engineering`, `performance-speedup`, `engineering-delivery`, `project-contracts` |
| `researcher` | `exa-research`, `research-summarizer`, `math-research`, `research-software-engineering`, `experimental-design` |
| `oracle-lite` | `performance-speedup`, `project-contracts` |
| `reviewer` | `code-assurance`, `project-contracts` |
| `council` | none |
| `verifier` | `code-assurance` |

Why these: implementers get delivery habits and code assurance; `impl-claude` adds interface contracts and the codebase map for cross-module work; `impl-hard` adds numerical and performance guidance; `verifier` and `reviewer` get code assurance; `designer` gets the interface skills; `explorer` gets the codebase map and library docs; `researcher`, `oracle` and `oracle-lite` get the research, math and performance guidance their questions need. `council` loads none.

## Skills reworded for OpenCode

`skills-opencode/` holds the `SKILL.md` of each skill whose wording was adapted (the installed copies are identical), and `skills-opencode/CHANGES.md` lists every replacement. Host wording (Codex) became OpenCode or neutral, model tier names became roles, and instructions to start other agents became: a worker reports the need, the orchestrator dispatches. `personal-orchestrator` was rewritten as a short guide that defers to `AGENTS.md` and the orchestrator prompt. The Codex toolkit itself is unchanged.
