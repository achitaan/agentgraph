# Skill audit

Every skill in the toolkit, what was done with it for OpenCode, and which agents may load it. The orchestrator may load all of them; "orchestrator only" means no other agent can.

| Skill | Verdict | Agents | Why |
|---|---|---|---|
| `code-assurance` | reworded | `impl-openai`, `impl-claude`, `impl-hard`, `verifier`, `reviewer` | Host-neutral craft for finding and testing failures; the core skill of everything that writes or checks code. |
| `engineering-delivery` | reworded | `impl-openai`, `impl-claude`, `impl-hard` | Delivery habits for implementers; its checking advice now matches the loop rules (a reported pass is a claim). |
| `project-contracts` | keep | `oracle`, `oracle-lite`, `impl-claude`, `impl-hard`, `reviewer` | Acceptance checks and interfaces: for whoever designs across module boundaries and whoever judges it. |
| `codebase-map` | keep | `explorer`, `impl-claude` | Source-backed maps: for discovery and for cross-module changes. |
| `delivery-operations` | reworded | `impl-claude`, `impl-hard` | CI, releases and rollbacks belong to the implementers that take big or hard changes. |
| `performance-speedup` | reworded | `oracle`, `oracle-lite`, `impl-hard` | Its delegation section named an agent that does not exist here; it now points at `explorer` and `oracle-lite`. |
| `research-software-engineering` | reworded | `researcher`, `impl-hard`, `reviewer` | Numerical correctness: for hard implementation, research, and review of numerical code. |
| `ml-experiment-standards` | reworded | `researcher`, `impl-hard`, `reviewer` | Leakage, splits and honest reporting: for whoever builds, studies or reviews a model. |
| `math-research` | reworded | `researcher`, `oracle`, `oracle-lite` | Proof work for the advisers; model names became roles. Its runner scripts start the Codex CLI and are used only where that exists. |
| `sympy` | reworded | `oracle`, `oracle-lite`, `impl-hard` | Exact mathematics for the adviser and the hard implementer, who can run it. |
| `experimental-design` | reworded | `researcher`, `oracle`, `oracle-lite` | Designing a study before data: research and advice. |
| `exa-research` | reworded | `researcher` | Public source discovery for the researcher. |
| `research-summarizer` | reworded | `researcher` | Summaries of supplied sources for the researcher. |
| `context7-mcp` | keep | `explorer` | Library docs; only `explorer` has that tool. |
| `design-taste-frontend` | reworded | `designer` | Original visual direction: designer. |
| `ui-ux-pro-max` | reworded | `designer` | App interfaces and design-system reference data: designer. |
| `impeccable` | reworded | `designer` | Audit and polish of an existing interface: designer. |
| `clone-any-website` | reworded | `designer` | Measured reconstruction of a site: designer. |
| `playwright-cli` | reworded | `designer`, `verifier` | Browser evidence: the designer builds with it and the verifier checks interface items with it. |
| `supabase` | reworded | `impl-claude` | Stack-specific: the implementer that takes multi-file product work. |
| `supabase-postgres-best-practices` | keep | `impl-openai`, `impl-claude`, `reviewer` | Schema, RLS and query rules: implementers that touch a database, and the reviewer for security. |
| `writing-clearly-and-concisely` | reworded | `designer` | Prose people read: the orchestrator (reports) and the designer (interface text). |
| `humanizer` | keep | orchestrator only | Editing prose for the user: orchestrator only. |
| `internal-comms` | keep | orchestrator only | Company formats for updates: orchestrator only. |
| `project-knowledge` | reworded | orchestrator only | Notes, ADRs and handoffs: orchestrator only, because it owns the records. |
| `grilling` | reworded | orchestrator only | Interviewing the user; only the orchestrator talks to the user. Lookups go to `explorer`. |
| `grill-me` | keep | orchestrator only | A two-line entry point to `grilling` for an explicit request. |
| `find-skills` | keep | orchestrator only | Installing skills changes the setup: orchestrator only. |
| `personal-orchestrator` | rewritten | orchestrator only | Now a short guide that defers to the orchestration rules. The Codex separate-session controller (38 scripts, four references) was removed from this version. |
| `improve-orchestration` | rewritten | orchestrator only | The self-adapting part: it may now propose a skill edit or a skill-map change as well as a prompt edit, and ends by syncing the toolkit repository. |
| `improve-orchestration-2` | rewritten | orchestrator only | The same for the `fast`, `normal` and `ultra-fast` presets and their private ledgers. |
| `open-ralph-wiggum` | reworded | orchestrator only | An external loop runner. Implementer loops are native here, so it is for the orchestrator when the user asks for ralph. |
| `codex-autoresearch` | reworded | orchestrator only | Measured git experiments toward a number: the orchestrator runs it under a session goal. Its detached mode needs the Codex CLI. |

Verdicts: **keep** is unchanged from the Codex toolkit; **reworded** had host, model or delegation wording replaced (every replacement is in [OPENCODE-CHANGES.md](OPENCODE-CHANGES.md)); **rewritten** was substantially changed for this setup.

Counts: 8 kept, 22 reworded, 3 rewritten, 33 in all. `shopify-ruby` is kept out of the toolkit as a separate, employer-specific skill.

A second pass removed from 13 worker-loaded skills the instructions a worker here cannot follow (asking the user, committing or resetting history, global installs, attempt caps, a self-review as the final check, pointers to skills that are not installed). The same limits are stated once in every worker's prompt ("Skills serve the brief"), so a skill added later is covered too.

2026-11-19: the user asked for `oracle-lite` and `oracle` to share one skill list and to differ by angle (the oracle works a question in depth; `oracle-lite` challenges the framing and the plan). `oracle-lite` therefore gained `math-research`, `sympy` and `experimental-design`.

## Fixed 2026-11-19

Wording only; what each skill teaches is unchanged. Backups are in `~/.config/opencode/backups/2026-11-19-pre-leftovers/`.

- `math-research`: the description's last sentence no longer points at `math-proof-writing` (not installed). Before: "Use math-proof-writing for exposition along an unchanged mathematical route." After: "Not needed for writing up a proof whose mathematical route is already settled." Three more mentions, in `references/structural-proof-compression.md` (line 3) and `README.md` (lines 43 and 111), were reworded the same way.
- `ui-ux-pro-max`: three lines. A missing Python 3 now means "report that it is missing and that it must be installed" instead of asking the user; "or ask" (which stack) became "or report that it is unknown"; the checklist is "a self-check before reporting" instead of "a final review". This skill lives in `~/.agents/skills/`, which OpenCode also reads (skills page, Discovery table), and `sync.sh` does not carry it, so the repository copy (`skills/ui-ux-pro-max/`) is kept by hand and was updated with these three lines.
- `playwright-cli`: "record user actions in the browser" now says "record the actions performed in the automated browser session".
- The orchestrator's details in the graph view now show its skills: `all · N` and a closed fold with every installed skill (see `WEB_API.md`). A root agent the roster does not list (OpenCode's own `build` or `plan`) is given `*` in the snapshot, because the plugin does not filter it.

`allowed-tools` (`sympy`, `experimental-design`, `playwright-cli`): not removed. The OpenCode V2 skills page (`https://opencode.ai/v2/docs/skills`, read 2026-11-19) lists the frontmatter fields it reads: `name`, `description`, `metadata.opencode/autoinvoke` and `disable-model-invocation`; it says V2 "accepts portability fields such as `license` and `compatibility` but does not interpret them", and it does not mention `allowed-tools` at all. So the documentation does not list `allowed-tools` as a field OpenCode V2 reads; whether the installed build ignores it was not tested, and the page is for V2 only.

## Left for a later pass

Not changed, because the rule in every worker prompt already covers them or the decision is the user's:

- `math-research`: `scripts/proof_loop.py` defaults to three iterations and launches the Codex CLI. The skill now says only the orchestrator decides to run it; the scripts were out of scope for a wording pass.
- `supabase`, `exa-research`, `context7-mcp`, `experimental-design`, `sympy`, `design-taste-frontend`: lines that fetch web pages or documentation. A worker without that tool reports the need.
