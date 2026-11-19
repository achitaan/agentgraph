---
name: improve-orchestration-2
description: Improve the `ultra-fast`, `fast` or `normal` preset's prompts, skills and skill map from that preset's private evidence. Use only when one of those presets is active and its private signals report an improvement is due, or when the user asks to improve how that preset works. Proposes one small edit and applies it only after the user approves.
---

# Improve a secondary preset (ultra-fast, fast, normal)

Private improvement loop for one secondary preset of oh-my-opencode-slim (same agent names as `ultra-fast-frontier`, different models and prompts). `<P>` below is the active preset: `ultra-fast`, `fast` or `normal`. Never read or write another preset's ledger, proposals or prompt folder.

Constants (all under `~/.config/opencode/`):
- Ledger home `<H>`: `orchestration/memory-o2` for `ultra-fast` (historical name), `orchestration/memory-fast` for `fast`, `orchestration/memory-normal` for `normal`. Every `agentgraph` ledger command carries `--home ~/.config/opencode/<H>`. Record only a named graph produced by this profile (`record --task <current-task-name>`); never bulk-import other profiles' graph files from a shared project directory.
- Proposals: `orchestration/proposals-o2/` (ultra-fast), `orchestration/proposals-fast/` (fast) or `orchestration/proposals-normal/` (normal)
- Backups: `backups-o2/` (ultra-fast), `backups-fast/` (fast) or `backups-normal/` (normal)

## Files you may propose to change (with user approval)

- **Prompts:** files under `oh-my-opencode-slim/<P>/` (preset prompt overrides; create `<P>/<agent>_append.md` to diverge an agent from the shared base append), and the `prompt`/`orchestratorPrompt` text of agents under `presets.<P>` in `oh-my-opencode-slim.json`.
- **The skill map:** an agent's `skills` list under `presets.<P>` (add a skill an agent kept needing, remove one it never used or misused). Use only skill names that exist under `skills/`.
- **A skill:** one `SKILL.md` or reference file under `skills/<name>/`, this skill included. Skills are shared by every preset, so say so in the proposal, and keep the edit true for all of them. Never rename a skill or change what it is for.

Out of bounds: AGENTS.md, opencode.json (apart from the two cost settings in "Cost settings" below, which are shared by every preset), every other preset, the base `oh-my-opencode-slim/*_append.md` files (shared by all presets), skill scripts and data files, the original ledger, plus models, effort levels, permissions and the roster. If evidence says one of those is the problem, say so in the proposal and stop.

## Procedure

1. **Read private signals.** Run `~/.config/opencode/orchestration/bin/agentgraph signals --home ~/.config/opencode/<H> --json`. Note its `generated` time for step 9.
2. **Check the last improvement.** If the trend shows five or more runs since it and its named metric did not improve, prefer proposing to revert or replace it. Metrics: `first_pass_rate`, `mean_attempts`, `escalation_rate`, `stall_rate`, `unverifiable_rate`, `abandoned_rate`, `mean_worker_cost`.
3. **Choose one signal**, in order: user feedback (a note tagged with an agent points at that agent's prompt or skills first); the same lesson, failure kind or discovered problem in two or more runs; an agent whose numbers stand out; repeated `unverifiable` gates or amended objectives. One failure in one run is not a pattern.
4. **Examine the evidence.** Read the referenced graph files and prompts. Feedback can be stale or task-specific. If signals conflict, show both to the user.
5. **Find the cause** in the owning file: a rule in a prompt, guidance in a skill the agent loaded, or a skill the agent needed and was not allowed to load. If no prompt could have caused it (outage, broken tool), say so and stop.
6. **Draft ONE small exact before/after edit** (one or two sentences, principle with its reason, prefer replacing over adding). Preserve models, effort, safety rules and permissions. Never loosen an evaluation rule and never add a maximum number of attempts or dispatches.
7. **Write the proposal** to the preset's proposals folder `<YYYY-MM-DD>-<short-name>.md`: quoted signals with task names, every affected NEW file with exact before and after text, the intended behaviour change, and the metric that should move and in which direction.
8. **Ask the user** with the `question` tool: apply, reject or change. Never apply without approval. Keep rejected proposals in the folder with the reason.
9. **Apply an approved edit.**
   - Check the "before" text still matches each affected file exactly; if not, show the new text and ask again.
   - Back up every affected file into a new unique directory under the preset's backups folder.
   - Apply only the approved edits with the normal file edit tools.
   - Validate with `OH_MY_OPENCODE_SLIM_PRESET=<P> node <plugin>/dist/cli/index.js doctor` (plugin path under `~/.cache/opencode/npm/oh-my-opencode-slim@latest/`) and confirm the JSON still parses.
   - Record it: write a JSON file with the `write` tool with keys `file` (path or list), `summary`, `signals` (list of strings), `metric` and `seen_until` (the `generated` value from step 1), then run `~/.config/opencode/orchestration/bin/agentgraph improved --home ~/.config/opencode/<H> --json-file <that file>`. Keep all text data in files; never interpolate it into shell commands.
   - Bring the toolkit repository up to date: run `sh ~/.config/opencode/orchestration/sync.sh` and show the user its output (the changed files). Commit only if the user asks.
   - Tell the user the change applies on the next OpenCode start with preset `<P>` active.
10. **Stop after one proposal.** List other strong signals in one line each.

## Rules

- No automatic timers, scheduled jobs or base-prompt edits without user approval. Recording authorized run evidence and feedback in the private ledger does not itself change the prompts.
- Do not import or load the base `improve-orchestration` skill or reference original paths; they would reintroduce the shared ledger.
- Keep the files short: every sentence is read on every run, so prefer deletions and merges when a file has grown.

## Cost settings

Two settings in `~/.config/opencode/opencode.json` may be proposed in the same way as a prompt edit, and nothing else in that file:

- `compaction.buffer`: how early a long chat is summarized. A larger value compacts earlier, which costs less and forgets more.
- `providers.anthropic.models.<model>.settings.cache.ttl`: how long the provider keeps a chat's context cached, `"5m"` or `"1h"`. An hour costs more to write and saves a full rewrite after each pause.

The evidence is `agentgraph cost --trend` (the same numbers are under `cost` in `agentgraph signals`). It splits recent chats into periods at each settings change and reports, for each, dollars per turn, the share spent on cold turns, and what pauses cost.

- Propose a change only when the periods being compared each have a few hundred turns, or when the user asks.
- Dollars per turn higher after a change than before it: propose reverting that change.
- A cold share above about a third, mostly pauses: propose the one-hour cache for that model, or earlier compaction.
- The user says the agent forgot things after a summary: propose later compaction (a smaller buffer), and say what it is expected to cost.
- One setting per proposal, with the before and after values and the numbers from both periods. Back the file up before applying, then run `agentgraph cost` once so the settings log records the change.
