# Skills adapted for OpenCode (2026-11-19)

Wording-only edits to 14 installed skills. Backups of every file before change: `~/.config/opencode/backups/2026-11-19-pre-skills-opencode/<same relative path>`. Copies of each changed `SKILL.md`: `skills-opencode/<name>/SKILL.md`. Codex originals were not touched. Frontmatter `name:` is unchanged everywhere.

Recurring replacement (the stance and loop wording): "Codex"-host and "spawn only when asked" wording became "delegation follows `AGENTS.md` and the orchestrator prompt; a worker never starts agents and reports the need under `Discovered` in its loop report". Verification wording became "run the item's evaluation and read its real output; a reported pass is a claim; never weaken a check to pass".

## engineering-delivery — changed
`SKILL.md`
- "Keep the task in one continuing Codex conversation where practical." → "...in one continuing session where practical."
- "Once acceptance and required checks pass, review the final diff..." now begins with "Run the agreed check and read its real output: a reported pass (a worker's or a verifier's) is a claim until the evidence is read, and a check is never weakened to get a pass." (added to the verification paragraph).
- "Use native delegation only when authorized under the active policy ... this skill does not start another controller, change models..." → "In this OpenCode setup the orchestrator delegates by default; the task graph, loop-item, verifier and reviewer rules are in `AGENTS.md` and the orchestrator prompt... A worker ... reports the need ... under `Discovered`..." (ownership/inputs/acceptance and "one continuation owner" kept).

`references/planning.md`
- "When delegation is authorized, assign ownership" → "When the orchestrator delegates, assign ownership".

Left on purpose: `references/experiments.md:16` "Codex AutoResearch skill" (proper name of the vendored `codex-autoresearch` skill).

## exa-research — changed
- "This is a search-and-synthesis workflow performed by Codex." → "...performed in the current session."

## grill-me — changed
- "Codex uses skill-file loading; do not require Claude's Skill tool." → "Read that file directly if no skill-loading tool is available; do not require Claude's Skill tool."

## grilling — changed
- "dispatch a sub-agent to find it ... wait for the sub-agent to report" → "look it up yourself, or, if you are the orchestrator and the lookup is non-trivial, dispatch `explorer` for it ... A worker does not start agents: it does the lookup itself or lists it under `Discovered`. ... wait for the explorer to report".

## impeccable — changed
- "adapts four Impeccable playbooks for Codex on macOS, Windows, and WSL" → "...for use in OpenCode on macOS, Windows, and WSL".

Left on purpose: `references/{adapt,audit,harden,polish}.md:1` HTML comment "Adapted for the portable Codex toolkit from Impeccable ... Apache-2.0" (provenance/attribution of the source toolkit).

## open-ralph-wiggum — changed (also "already differed", see below)
- "- Start a loop only when the user explicitly requests running Ralph..." gained: "Only the orchestrator starts a Ralph loop (it starts separate agent processes); a worker does not, and reports the need under `Discovered` in its loop report."
- "it does not continue the current chat" → "...the current session".

Left on purpose (product names of the Codex CLI as one of Ralph's supported agents, CLI flags/binaries/env names, a real path): `SKILL.md` lines 7 (trigger phrase list), 20 ("never Codex" and the real path `/Users/achita.anantachina/.codex/bin/ralph`), 31, 42, 98, 163, 206, 272, 333, 375, 541.

## performance-speedup — changed
- "Use a bounded subagent for heavy log, trace, or profile reading..." → "If you are the orchestrator, send heavy log, trace, or profile reading to `explorer` ... A worker does the reading itself."
- Section "## Subagent use" / "Prefer the custom subagent `perf-auditor`..." → "## Delegation" / "There is no `perf-auditor` agent in this setup. ... the orchestrator may dispatch `explorer` (read-only) ...; `oracle-lite` can give a second opinion ... A worker does not start agents..."
- "The subagent is not limited..." → "The delegated worker is not limited..."; "The main thread should then: ... apply the changes" → "The orchestrator should then: ... dispatch the changes as loop items (the before/after measurement can be the item's evaluation)".

## personal-orchestrator — changed (rewrite)
`SKILL.md` rewritten from 79 to 77 lines as a short guide for the OpenCode orchestrator: when to delegate (direct / one implementer lane / graph / research and advice), how to write a bounded task packet, independent check and review (worker PASS is a claim; one `reviewer` gate), durable records; it defers to `AGENTS.md` and the orchestrator prompt for the rules and no longer describes the Codex controller workflow. Description rewritten to match. Kept host-neutral craft: bounded packets, disjoint write scopes, independent review, durable records.

References (Codex-only procedures, minimal edits):
- `controller.md`, `readiness.md`, `review-cycle.md`, `same-chat-progress.md`: a one-paragraph "OpenCode note" added after the title (they describe the Codex toolkit's separate-session controller; read as background only).
- `workflow.md`: "All profiles stay in this conversation unless the user explicitly asks to spawn another agent or session. Perform checks and a labeled self-review here..." → "Profiles do not start agents; delegation follows `AGENTS.md`... Run the agreed checks and read their real output... a reported pass is a claim... An independent review is the single `reviewer` gate". Table: "Inherit; explicit fast preset requests Luna" and "Inherit; explicit Astra preset available" → "Inherit the user-selected model" (both); "Luna does not accept `ultra`" → "a fast preset may not accept `ultra`".
- `task-profiles.md`: "If the user is choosing a coding model, Sol with selective skills is the starting recommendation." → "Model choice follows the routing in the orchestrator prompt."; "Spawn another agent, chat or model session only when the user explicitly asks; a profile selection is not permission." → "Delegation follows `AGENTS.md` and the orchestrator prompt; a profile selection does not by itself start any agent, and a worker never starts one."; "never spawn just because a profile was selected" → "a profile selection alone starts no agent"; two examples "keep Sol/high" → "keep the model".
- `loop-engineering.md`: "Keep work in this chat; spawn another agent or model session only when the user explicitly asks" → "Keep work in this session; delegation follows `AGENTS.md`... a worker never starts an agent"; "Spawn a fresh read-only reviewer only when explicitly requested." → "An independent review is the orchestrator's single `reviewer` gate."; "Delegate that question only if the user explicitly asks." → "The orchestrator may delegate that question (to `researcher`); a worker reports it under `Discovered`."
- `review-cycle.md`: "Defaults retain Sol coordination/review and Luna implementation" → "Defaults retain the coordinator/reviewer and implementer presets".
- `context-and-concurrency.md`: "No automatic Codex memory writes" → "No automatic agent memory writes".

Left on purpose (the Codex controller and its CLI, covered by the OpenCode note or the "Codex-controller material" section): `SKILL.md:72,75` (names the Codex toolkit controller); `references/controller.md:3,34,37,56,75`; `references/readiness.md:3,15,20,21,34`; `references/review-cycle.md:3`; `references/same-chat-progress.md:3,20` ("native Codex Goal" is a Codex feature and a link title). Scripts, profiles/TOML, JSON examples were not touched.

## project-knowledge — changed
- description: "no ... automatic Codex memory writes are required" → "... automatic agent memory writes ..."
- "User notes are separate from Codex memory; do not write to Codex memory without an explicit request." → "...separate from any agent memory; do not write to agent memory...".

## research-summarizer — changed
- description: "Plugin for Claude Code, Codex, Gemini CLI, and OpenClaw." → "Plugin for Claude Code, OpenCode, Gemini CLI, and OpenClaw."
- Verification Loop: "Before delivering any brief, check:" → "...check (run each check and read its real output; a reported pass is a claim, and a check is never relaxed to get a pass):"
- Left alone: the install command `--tool codex|gemini|...` (a flag of the upstream converter script; lowercase, not matched by the grep).

## research-software-engineering — unchanged
No host, model, agent-spawning or checking wording to adapt (its "verify behaviourally, not by inspection" already fits the loop rules).

## writing-clearly-and-concisely — changed
- "2. Dispatch a subagent with your draft and `elements-of-style.md` / 3. Have the subagent copyedit and return the revision" → "2. If you are the orchestrator, hand the draft and `elements-of-style.md` to a worker as a loop item and read the revision it returns / 3. A worker does not start agents: it copyedits against the guide itself, or lists the need under `Discovered` in its loop report".
- "Low on tokens? Dispatch a subagent to copyedit with the guide." → "Low on tokens? See Limited Context Strategy: have a worker copyedit with the guide."

Left on purpose: `references/third-party/obra--the-elements-of-style--README.md:32` ("| Codex | see docs/install/codex.md |", third-party text and a real path in that project).

## math-research — changed (also "already differed", see below)
- `SKILL.md` Verify and deliver: "Use a fresh-context referee for a hard or suspect argument when available; otherwise identify the check as self-review." → "...the orchestrator dispatches `reviewer` (or `oracle` for hard mathematics) and treats its verdict as a claim to check; a worker does not start agents, so it reports the need under `Discovered`... and identifies its own check as self-review."
- `SKILL.md` Execution: the optional runner now says "(it launches the separate Codex CLI, so use it only where that CLI is installed; otherwise the orchestrator dispatches generation and review as loop items)".
- `references/prover-verifier-loop.md`: "If delegation is unavailable, perform a distinct adversarial self-review and label it accordingly." → "The orchestrator can dispatch the verifier role to `reviewer` (or `oracle`). A worker does not start agents: it performs a distinct adversarial self-review, labels it..., and lists the need ... under `Discovered`..."
- `references/expert-consultation.md`: "Use a fresh native subagent when available." → "Use a fresh agent session: the orchestrator dispatches `oracle` (or `oracle-lite`) with the packet. A worker does not start agents; it reports the packet as the next action under `Discovered`."

Left on purpose: `SKILL.md:70` (new text names the Codex CLI that `scripts/proof_loop.py` actually launches); `README.md:32` ("the optional agent runner also needs an available Codex CLI"; describes the real script requirement). Not Markdown prose and untouched: `codex-cite`, `codex-math-python`, `codex-wmath` command names and `codex-theory-proof-workbench` (real tool and repository names).

## sympy — unchanged
No host, model or agent wording; "Scientific Agent Skills" is a citation title.

## Already differed from the Codex originals (before this work, not mine)
- `math-research`: `SKILL.md`, `references/lean-formalization-bridge.md`, `scripts/start_proof.py` differ; `scripts/__pycache__` exists only in the installed copy.
- `open-ralph-wiggum`: `SKILL.md` differs (the installed one is the full upstream guide plus a "Local OpenCode setup" block); `LICENSE`, `agents/`, `references/` exist only in the Codex original.

## Summary table

| Skill | Status |
|---|---|
| engineering-delivery | changed |
| exa-research | changed |
| grill-me | changed |
| grilling | changed |
| impeccable | changed |
| open-ralph-wiggum | changed |
| performance-speedup | changed |
| personal-orchestrator | changed (rewrite + 8 references) |
| project-knowledge | changed |
| research-summarizer | changed |
| research-software-engineering | unchanged |
| writing-clearly-and-concisely | changed |
| math-research | changed |
| sympy | unchanged |

## Notes
- `grill-me/SKILL.md` uses CRLF line endings; they were kept, so only the one line differs from the original.
- `research-software-engineering/SKILL.md` (unchanged, byte-identical to the Codex original) also uses CRLF; its frontmatter `name:` equals the folder name, but a naive line-by-line check sees `name: ...\r`.
