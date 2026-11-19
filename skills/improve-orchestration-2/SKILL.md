---
name: improve-orchestration-2
description: Improve the `ultra-fast` or `fast` preset's prompts from that preset's private evidence. Use only when one of those presets is active and its private signals report an improvement is due, or when the user asks to improve how that preset works. Proposes one small edit and applies it only after the user approves.
---

# Improve a secondary preset (ultra-fast, fast)

Private improvement loop for one secondary preset of oh-my-opencode-slim (same agent names as `ultra-fast-frontier`, different models and prompts). `<P>` below is the active preset: `ultra-fast` or `fast`. Never read or write another preset's ledger, proposals or prompt folder.

Constants (all under `~/.config/opencode/`):
- Ledger home `<H>`: `orchestration/memory-o2` for `ultra-fast` (historical name), `orchestration/memory-fast` for `fast`. Every `agentgraph` ledger command carries `--home ~/.config/opencode/<H>`. Record only a named graph produced by this profile (`record --task <current-task-name>`); never bulk-import other profiles' graph files from a shared project directory.
- Proposals: `orchestration/proposals-o2/` (ultra-fast) or `orchestration/proposals-fast/` (fast)
- Backups: `backups-o2/` (ultra-fast) or `backups-fast/` (fast)

## Files you may propose to change (with user approval)

Files under `oh-my-opencode-slim/<P>/` (preset prompt overrides; create `<P>/<agent>_append.md` to diverge an agent from the shared base append), `skills/improve-orchestration-2/SKILL.md`, `orchestration/ORCHESTRATOR_2.md`, and the `prompt`/`orchestratorPrompt` text of agents under `presets.<P>` in `oh-my-opencode-slim.json`.

Out of bounds: AGENTS.md, opencode.json, every other preset, the base `oh-my-opencode-slim/*_append.md` files (shared by all presets), base skills, the original ledger, plus models, effort levels, permissions and the roster. If evidence says one of those is the problem, say so in the proposal and stop.

## Procedure

1. **Read private signals.** Run `~/.config/opencode/orchestration/bin/agentgraph signals --home ~/.config/opencode/<H> --json`. Note its `generated` time for step 9.
2. **Check the last improvement.** If the trend shows five or more runs since it and its named metric did not improve, prefer proposing to revert or replace it. Metrics: `first_pass_rate`, `mean_attempts`, `escalation_rate`, `stall_rate`, `unverifiable_rate`, `abandoned_rate`, `mean_worker_cost`.
3. **Choose one signal**, in order: user feedback; the same lesson, failure kind or discovered problem in two or more runs; an agent whose numbers stand out; repeated `unverifiable` gates or amended objectives. One failure in one run is not a pattern.
4. **Examine the evidence.** Read the referenced graph files and prompts. Feedback can be stale or task-specific. If signals conflict, show both to the user.
5. **Find the cause** in the owning file. If no prompt could have caused it (outage, broken tool), say so and stop.
6. **Draft ONE small exact before/after edit** (one or two sentences, principle with its reason, prefer replacing over adding). Preserve models, effort, safety rules and permissions. Never loosen an evaluation rule and never add a maximum number of attempts or dispatches.
7. **Write the proposal** to the preset's proposals folder `<YYYY-MM-DD>-<short-name>.md`: quoted signals with task names, every affected NEW file with exact before and after text, the intended behaviour change, and the metric that should move and in which direction.
8. **Ask the user** with the `question` tool: apply, reject or change. Never apply without approval. Keep rejected proposals in the folder with the reason.
9. **Apply an approved edit.**
   - Check the "before" text still matches each affected file exactly; if not, show the new text and ask again.
   - Back up every affected file into a new unique directory under the preset's backups folder.
   - Apply only the approved edits with the normal file edit tools.
   - Validate with `OH_MY_OPENCODE_SLIM_PRESET=<P> node <plugin>/dist/cli/index.js doctor` (plugin path under `~/.cache/opencode/npm/oh-my-opencode-slim@latest/`) and confirm the JSON still parses.
   - Record it: write a JSON file with the `write` tool with keys `file` (path or list), `summary`, `signals` (list of strings), `metric` and `seen_until` (the `generated` value from step 1), then run `~/.config/opencode/orchestration/bin/agentgraph improved --home ~/.config/opencode/<H> --json-file <that file>`. Keep all text data in files; never interpolate it into shell commands.
   - Tell the user the change applies on the next OpenCode start with preset `<P>` active.
10. **Stop after one proposal.** List other strong signals in one line each.

## Rules

- No automatic timers, scheduled jobs or base-prompt edits without user approval. Recording authorized run evidence and feedback in the private ledger does not itself change the prompts.
- Do not import or load the base `improve-orchestration` skill or reference original paths; they would reintroduce the shared ledger.
- Keep the files short: every sentence is read on every run, so prefer deletions and merges when a file has grown.
