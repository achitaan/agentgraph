---
name: improve-orchestration
description: Improve the orchestration prompts from accumulated evidence. Use when `agentgraph record` or `agentgraph signals` reports an improvement is due, when the user asks to improve, tune or review how the agents work, or when the user says the orchestration keeps making the same mistake. Proposes one small prompt edit and applies it only after the user approves.
---

# Improve the orchestration

This is the outer loop of a two-loop system. The inner loop is the normal work: the orchestrator and its agents follow their base prompts, and every finished task graph and every correction from the user is saved to a ledger. This skill is the improver: it reads what accumulated, finds one thing that keeps going wrong, and proposes the smallest edit to a base prompt that would fix it. The user approves or rejects it. The next run inherits the change.

The models never change. What improves is the set of files they read. So an improvement is only real when it is written into one of those files, reviewed, and later shown to have helped.

## The base files (what you may propose to edit)

All paths are under `~/.config/opencode/`.

| Behaviour | File |
|---|---|
| How the orchestrator routes, briefs, evaluates and runs graphs | `oh-my-opencode-slim/orchestrator_append.md` |
| The same rules in short form for the primary agent | `AGENTS.md` (keep it consistent with the file above) |
| The loop every implementer follows | `oh-my-opencode-slim/impl-openai_append.md`, and its two identical copies `impl-claude_append.md`, `impl-hard_append.md` (edit all three the same way) |
| The reviewer's or verifier's behaviour | its `prompt` under each preset in `oh-my-opencode-slim.json` (edit every preset the same way) |
| What an agent knows how to do | one `SKILL.md` or reference file under `skills/<name>/` (shared by every preset; never rename a skill or change what it is for) |
| Which skills an agent may load | that agent's `skills` list under each preset in `oh-my-opencode-slim.json` |
| One other agent's behaviour | `oh-my-opencode-slim/<agent>_append.md` (create it if it does not exist) |
| A fact about one project or one kind of task | `orchestration/LESSONS.md`, not a prompt |

Out of bounds: models, effort levels, permissions and the agent roster. Those are the user's choices. If the evidence says one of them is the problem, say so in the proposal and stop there.

## Procedure

1. **Read the signals.** Run `~/.config/opencode/orchestration/bin/agentgraph signals --json`. Note its `generated` time; you need it in step 9. It gives you the user's feedback, the lessons and discovered work from recent graphs, per-agent numbers (first-pass rate, mean attempts, escalations, stalls, failure kinds), gate verdicts, and the trend since the last improvement.

2. **Check the last improvement first.** If `trend` shows at least five runs after the last improvement and the metric it named did not move, or got worse, the best proposal is to revert or replace that edit. An improvement that did not help is clutter in every future prompt. Two limits on the trend: it counts a run as "after" when the graph was created after the improvement was recorded, although an OpenCode session that was already open kept the old prompt until it restarted; and it only holds these metrics: `first_pass_rate`, `mean_attempts`, `escalation_rate`, `stall_rate`, `unverifiable_rate`, `abandoned_rate` and `mean_worker_cost`. Name one of those as the metric for a proposal, so its effect can be checked later. (Before the first improvement `trend` is empty, which is why the names are listed here.)

3. **Choose one signal.** Rank them in this order:
   - Feedback from the user. It is the knowledge the agents cannot get any other way. One clear statement of how the user wants things done is enough to act on.
   - The same lesson, failure kind or discovered problem in two or more runs.
   - An agent whose numbers stand out: a low first-pass rate, repeated stalls, or work that is usually escalated away from it.
   - Gates that keep returning `unverifiable`, or objectives that keep being amended.

   A single failure in a single run is not a pattern. Leave it in the ledger; it will count if it happens again.

4. **Sanity-check it.** Feedback can be wrong, stale or about one task only. Read the graph file or the prompt it refers to before you believe it. If two signals contradict each other, show both to the user instead of picking one.

5. **Find the cause in the base file.** Read the file that owns the behaviour. Ask what in it produced the outcome: a missing principle, a rule that is too rigid, an instruction that is ambiguous, or one that conflicts with another. If nothing in the prompts could have caused it (a model outage, a broken tool), it is not a prompt problem: say so and stop.

6. **Draft the smallest edit that would have prevented it.**
   - Write a principle, not a rule. "Check that an evaluation can fail before trusting it" generalises; a list of cases does not.
   - Give the reason in the same sentence, so the agent can apply it to cases you did not think of.
   - Prefer changing or replacing an existing sentence over adding a new one. If a rule is not working, remove it.
   - One or two sentences. One change per cycle, so its effect can be measured.
   - Never loosen an evaluation rule, and never add a maximum number of attempts or dispatches: loops on this computer have none.

7. **Write the proposal** to `~/.config/opencode/orchestration/proposals/<YYYY-MM-DD>-<short-name>.md` with:
   - The signals it answers, quoted, with the task names.
   - Every file the edit touches (all four copies for the implementer loop; both `orchestrator_append.md` and `AGENTS.md` when a rule appears in both), and the exact text before and after in each.
   - What should change in the agents' behaviour.
   - The number in `agentgraph signals` that should move if it works, and which way.

8. **Ask the user.** Show the before and after text and the reason in the chat, and ask for approval with the `question` tool: apply, reject, or change. Never apply an edit that has not been approved. A rejected proposal stays in the folder with the reason, so it is not proposed again.

9. **Apply an approved edit.**
   - Check that the "before" text in the proposal still matches each file exactly. If a file changed since the user approved, show the new text and ask again.
   - Copy every file you will edit to a new folder under `~/.config/opencode/backups/` first. Use a folder name that does not exist yet.
   - Make the edit in every file listed in the proposal.
   - Confirm the plugin config still loads (the `doctor` command of oh-my-opencode-slim).
   - Record it. Write a JSON file with the `write` tool, with the keys `file` (a path, or a list of paths), `summary` (one line), `signals` (the evidence that prompted it, as a list of strings), `metric` (what should move) and `seen_until` (the `generated` time from step 1). Then run `agentgraph improved --json-file <that file>`. A file keeps quoted evidence out of the shell. Evidence that arrived after `seen_until` stays pending, so a correction the user made while you were drafting is not lost.
   - Tell the user it takes effect the next time OpenCode starts.

10. **Stop after one proposal.** If other strong signals remain, list them in one line each so the user can ask for another cycle.

## Keeping the files healthy

- The base files are read on every run, so every sentence costs tokens forever. If a file has grown past what you can explain line by line, propose a deletion or a merge as the improvement.
- Procedures that only matter sometimes belong in a skill, loaded when needed, not in a prompt that is always read.
- `LESSONS.md` holds lessons that are specific. When the same lesson has been written there for two different projects, it is general: propose moving it into a base prompt and delete it from the lessons file.

## Skills and the toolkit repository

- A note from the user tagged with an agent points at that agent's prompt or skills first. The cause may be a rule in a prompt, guidance in a skill the agent loaded, or a skill it needed and was not allowed to load; propose the edit where the cause is.
- A skill edit follows the same procedure as a prompt edit: one small exact before/after change, a proposal, the user's approval, a backup, then `agentgraph improved`. Skill scripts and data files are out of bounds.
- After an approved edit is applied, run `sh ~/.config/opencode/orchestration/sync.sh` and show the user its output, so the toolkit repository carries the change. Commit only if the user asks.
