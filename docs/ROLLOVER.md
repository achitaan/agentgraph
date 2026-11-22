# Rollover: reset a long worker lane to its own notes, with no model call

A worker lane that has read a lot carries all of it on every later step, and every step pays for that again. The rollover
plugin (`rollover-plugin/`) watches the size of a lane's context. When it is large, it asks the lane to write short handoff
notes, and then replaces the whole conversation with a summary that code builds from those notes. The summary costs nothing:
no model writes it. The lane keeps its session id and carries on with the next step.

## What it does

1. **Ask** (`ask_at`, default 115,000 tokens). At the first step end where the context is at or above `ask_at` and the lane's notes do not
   cover it, the plugin sends the lane one steering message. It says: finish the tool call you are in, write the notes NOW, read and
   start nothing else first, because the context is replaced right after the notes are saved and anything done after writing them is
   lost. It names the notes file (`<work folder>/.opencode/handoff/<session id>.md`), says what to put in it (the task, decisions, what
   failed, what is done including what was already read, the exact next step in the last lines, the files that matter) and says the
   folder holds a `.gitignore` of `*`. The message is sent only while the lane's turn is still going, so the turn is not cut short.
   If the notes still do not cover the context later, the plugin asks again, at most once per `reask_steps` (2) steps and per
   `reask_growth` (10,000) tokens of growth, and at most `max_asks` (4) times between two rollovers.
2. **Roll** (no threshold of its own). At the first step end at or above `ask_at` where the notes cover the context, the plugin starts a
   compaction itself (`session.compact`): in the normal case the step in which the lane wrote its notes. There is no `roll_at` any
   more; an old `rollover.json` that still has it loads, the key is ignored and one log line says so. OpenCode then asks the plugin for the
   summary; the plugin runs
   `agentgraph handoff build --newer-than <the later of the last rollover and the first request>`, which prints the lane's first
   user message, the work folder, the files changed, the task-graph node if there is one, and the lane's notes word for word.
   OpenCode uses that text and makes no model call. The lane continues in the same run.

   **Why ask late and roll at once.** A lane reads in steps of about 25,000 tokens, one module each. Measured per-step contexts (k tokens):
   - Round-4 plugin (ask 105k, roll 120k, gap 40k), 45 turns: `... 105 106 [ask] 106 108 131 132 [roll] 8 32 ...`. The lane wrote its notes at once (106 to 108k),
     then read the next module (131k) and worked on it, and the roll at 132k used notes that end at 108k. That module's work was lost
     and read again after every rollover (cache write 358k against 308k), and the last cycle read four modules instead of two, which
     pushed it past `ask_at` for a third ask. Three off/on pairs of this plugin saved only 6.3%.
   - First plugin, 31 turns: `... 105 106 [ask] 130 132 [roll] 8 9 33 ...`. The lane happened to read first and write its notes just before the
     roll, so nothing was lost. That was luck, not design.
   - Rolling as soon as the ask is answered, with the ask at 105k, rolled at about 110k: four modules per cycle and a third rollover.
   So now the ask comes later (115k, so with 25k steps it lands between 115k and 140k) and the answer is used the moment it arrives. Notes
   that are followed by a big read are *not* an answer: the gap to the context is limited to `notes_gap`, 10,000, well under one reading
   step, so the lane is asked again instead of being rolled on notes that miss a module.
3. **Pause** (`cache_seconds`, default 300; `pause_min`, default 60,000). A new turn that starts more than `cache_seconds` after the
   lane's last step has lost its prompt cache, so it would pay full price to re-read a big context. If the context is over
   `pause_min` and the notes cover it (the same rule as the roll), the plugin rolls over before the first model step of that turn.
4. **Give up** (`hard_at`, default 160,000). If the notes still do not cover the context, the plugin logs why once and goes on asking
   (up to `max_asks`); notes that arrive late are still used. OpenCode's own compaction is the backstop.

**One rule for "the notes cover the context".** The notes cover it when they were written after the previous step ended (never merely
because the file exists, and never from before the last rollover or the first request) and the context has grown past them by at most
`notes_gap` (default 10,000) tokens, well under one reading step. The plugin saves, per session,
when the notes were written and how big the context was then, so a restart of OpenCode loses nothing.

**The hook checks again.** Coverage is decided when the plugin requests the compaction, but the compaction runs at a later step
boundary. When OpenCode asks for the summary, the plugin checks again: the notes must still count, and the context may have grown at
most `hook_growth` (default 15,000) since the request. If not, it sets nothing, logs why (`fell_back`), and OpenCode makes a model
summary. If OpenCode's own compaction runs first while the plugin's request is queued, the plugin answers that one (the notes cover
it); the queued request then compacts a small context and gets a normal model summary, because the first compaction's end cleared the
request. There is no API to withdraw a queued compaction (`session.compact` and `session.wait` are all there is), so the cost is one
model summary of a small context, about $0.01 to $0.03.

**Several processes.** The desktop server and a CLI run (`opencode run --session X`) can work on one session. Before every decision the
plugin compares the state file with what this process last wrote or read (inode, size, mtime); when another process changed it, it
reloads it, drops what only this process remembered (a pending request, the hard_at note), and logs `state_reloaded`. State is written to
a temp file and renamed, so a reader never sees half a file, and a torn or empty file is ignored. A session idle for 24 hours leaves the plugin's memory (swept hourly); its state comes back from the file when it next works.

**A compaction OpenCode starts itself (automatic, or a manual one) gets the code summary too, when the notes are fresh.** Measured in round 5
(child lane): the ask went out at 129k, the step that answers it carried a 25k read already in flight, so its prompt was 154k, and OpenCode's own
automatic compaction fired at that step end, before the plugin had seen the step end. The old hook answered only compactions the plugin had
requested, so OpenCode made a model summary of 153k tokens ($0.40) and the plugin's request then queued a second one ($0.02). Now, for a compaction the plugin
did not request, the hook reads the notes file itself (the plugin's state is one step behind in that race) and supplies the code summary when and only when:
the plugin has seen a step end (no saved state and a first event that is not a step end: left), the notes exist, are newer than the last rollover and the last ask,
and cover the context (written after the last step end the plugin saw, i.e. in the step that is ending, or within `notes_gap` of the context at the last step end),
and no step has started and not ended since the notes were written (notes from before a step that is running do not cover its growth: left. This rule is for OpenCode's own compaction only: a first version applied it to the plugin's own
request too, and the round 7 run showed why that is wrong: the next step normally starts before the hook of a requested compaction runs, so the requested summary fell back to a model summary, $0.40), and `handoff build --newer-than` exits 0. Notes and events are compared in whole milliseconds, so notes written in the same millisecond as an event count as older (the safe side). In every other case `event.result` stays unset and
OpenCode's normal model summary is the fallback. One log line each: `own_compaction`, reason `own-compaction: supplied` or `own-compaction: left, <reason>`.
Root chats and `skip_agents` (and the leads) stay out as before: the hook checks the scope rules again each time (a settings edit that moves a session out of scope stops the summary at once). After the hook answers, the plugin holds that
compaction as its pending request (so the step end that follows requests no second one) and ignores the one step end that carries a context at least as large
as the one that was replaced (it can arrive after the compaction ended); the next step counts normally. This applies whenever the hook supplies a summary, also for a compaction the plugin requested.
A compaction that then fails clears both, so the next end is not logged as a rollover and the next step counts.
If the plugin's *own* request is already queued when OpenCode's compaction fires first, the queued request cannot be withdrawn (the API has `session.compact` and `session.wait`
only): the first compaction gets the code summary, and the queued one, which arrives before any step has ended since the rollover, gets **the same summary again with no build**
(round 5 measured a model summary of it, $0.02, which only paraphrased the notes). The summary is kept in memory only, and given again only when no step has ended since the rollover, within `compaction_patience_ms`
of it, while the notes file is still the one it was built from, and no model-summary compaction or failure came in between; a reload of the state from another process drops it. It would be wrong if the lane had
written something new that the summary lacks: then a step would have ended (the case is excluded) or the notes would have changed (excluded too); user messages typed in between stay in OpenCode's own tail of recent messages. A request that fails the freshness check is never queued twice.

**`handoff build` exit 4 (the task cannot be read from the database)** marks the session as left alone for the rest of the process (`task_unreadable`, one log line): no more asks and no more requests, which would each end in a model summary at about 117k.

**Not done:** asking one step earlier when the observed step growth would put the answering step above `hard_at`. It needs a growth estimate in the state machine; with the own-compaction path the late answer
is now rolled with a code summary instead, so it was left out.

**A compaction that fails.** A requested compaction that ends in `session.compaction.failed` is not a rollover. The plugin then waits
5 steps before trying again, 10 after the second failure in a row; after 3 failures in a row it leaves the session alone (one
`gave_up` line in the log) until a compaction ends well. A failed `compact` call is treated the same, never retried on every step.

**Where it acts.** Only on sessions that have a parent, that is, lanes started by another agent (`workers_only`), whose agent is not
in `skip_agents` (default: `orchestrator`, `build`, `plan`, `side-chat`, `council`: the root agents and the council of this setup; the
names come from the config layout, not from reading `opencode.json`), is not a lead that runs child sessions of its own (see below) and, when `only_agents` is not empty, is in `only_agents`. A session whose agent is
not known is left alone when either list is not empty, and so is a session that cannot be looked up (retried at most every 60 s).

**Leads.** Hubs, council leads and team leads (`structure/teams/*.md`, `agentgraph/teams.py`) run child sessions of their own and must
not be rolled over. At start the plugin runs `agentgraph handoff leads` once (a child process, never at an event), which prints their
names from the structure folder (`--structure-dir`, or the `structure_dir` setting, replaces the folder agentgraph uses), and adds them to
`skip_agents`. They are read once per process: a team added later is seen after a restart of OpenCode. If the command cannot be run the plugin logs
one line (`leads_unreadable`) and only `skip_agents` applies. `skip_leads: false` turns this off.

## Where files live, and what is never touched

- **The lane's notes**: `<session folder>/.opencode/handoff/<session id>.md`, the only file the plugin has the lane write inside a
  project. When the plugin creates that folder it also creates `.opencode/handoff/.gitignore` containing `*`, so neither the notes
  nor the `.gitignore` can be committed. (`handoff build` leaves that folder out of the changed-files list; other files under
  `.opencode/` are listed.)
- **Everything else** (per-session state `state/<session id>.json`, and the decision log `rollover.log`): one folder outside every
  project, `<data home>/agentgraph/rollover/`, where the data home is `$XDG_DATA_HOME` or `~/.local/share`
  (`AGENTGRAPH_ROLLOVER_HOME` or the `state_dir` setting replaces the whole folder). State files older than 30 days are deleted when
  the plugin starts, and a `rollover.log` over 5 MB is cut to its last 1 MB (from a whole line; one `log_cut` line says so); a session's file goes when OpenCode reports the session deleted.
- **Nothing at all is written for a session the plugin does not act on** (a root session, a skipped agent, an unknown session): no
  state, no log line, no folder, no `.gitignore`.
- Never touched: `opencode.json` (apart from the one line you add by hand, below), the lane's code and tests, git (no commit, no stash,
  no checkout), any session the plugin does not act on. Message text is never logged; a failed compaction is logged by error type only.

## Settings

`rollover.json` beside the plugin; a missing file means these defaults. Edits are picked up without a restart.

| key | default | meaning |
|---|---|---|
| `enabled` | `true` | `false` turns it all off |
| `ask_at`, `hard_at` | 115000, 160000 | tokens of context (plain input + cache read + cache write of the last step); each at least 20,000 and `ask_at < hard_at`. `ask_at` is where the ask goes out and the first step end where a rollover can start. `roll_at` of earlier versions is ignored with one log line |
| `families` | `{}` | overrides per model family, e.g. `{"opus": {"ask_at": 140000, "hard_at": 170000}}` (`opus`, `sonnet`, `other`) |
| `skip_agents` | orchestrator, build, plan, side-chat, council | agent names never touched |
| `skip_leads` | `true` | also skip the hubs, council leads and team leads that `agentgraph handoff leads` prints (read once at start) |
| `only_agents` | `[]` | when not empty, act only on these agents |
| `workers_only` | `true` | only sessions that have a parent; `false` also acts on a top-level session |
| `notes_gap` | 10000 | the notes cover the context (roll and pause) when it grew at most this much since they were written |
| `hook_growth` | 15000 | how much the context may grow between the request for a compaction and OpenCode's call for the summary |
| `compaction_patience_ms` | 180000 | a requested compaction that has not ended after this long counts as failed |
| `reask_steps`, `reask_growth`, `max_asks` | 2, 10000, 4 | how often the lane is asked again |
| `cache_seconds`, `pause_min` | 300, 60000 | the pause trigger |
| `notes_dir` | `.opencode/handoff` | a relative folder **under `.opencode/`** of the lane's work folder, no `..`; anything else (`.`, `src`, `.opencode` itself) gives the default and one log line, because the plugin puts a `.gitignore` of `*` there. `handoff` applies the same rule to `--notes-dir` |
| `structure_dir` | found | absolute path of the structure folder the lead names are read from |
| `build_timeout_ms` | 15000 | how long `handoff build` may take |
| `agentgraph_bin` | found | path of `bin/agentgraph`; found beside the plugin or in `<config>/orchestration/bin` |
| `state_dir` | `<data home>/agentgraph/rollover` | where the state and the log live |

**A bad value never breaks it.** A key that is not a number in its range (a string, null, too small), a malformed `rollover.json`, a
bad family override, or thresholds that are not in order give the defaults (both thresholds, `ask_at` and `hard_at`, when the order is wrong) and one
`settings_invalid` line in the log that names the keys.

## Install and turn off

Copy `rollover-plugin/` to `<opencode config>/plugins/agentgraph-rollover/`; OpenCode loads it at the next start. **One setting is
needed**, one line in `opencode.json`:

```json
{ "compaction": { "background": false } }
```

Without it OpenCode writes a speculative model summary early (at about 80,000 tokens with the usual `buffer`), which it then
reuses when the plugin asks for a compaction, so the hook is never asked and the rollover costs what a normal compaction costs
(measured: $0.21 per compaction, 0 rollovers). The plugin works without any other key; the default `keep.tokens` is
left alone (the context right after a rollover was about 9,000 tokens, most of it the system prompt).

**Turn it off** with `"enabled": false`, with the environment variable `AGENTGRAPH_ROLLOVER=off`, or by removing the folder. Notes
files are not deleted; delete `.opencode/handoff/` when you want them gone.

**Audit a rollover.** `agentgraph handoff log` prints the decision log as a table (`--session ID` for one session, `--last N`):
asked, rolled, compacted (someone else's), fell back, skipped, compaction_failed, gave_up, settings_invalid, state_reloaded, leads_unreadable, log_cut, error; each with the
session, the context size, the time and a reason, never message text. `agentgraph handoff check --session ID` says whether a lane's
notes are fresh; `handoff path` and `handoff build` are what the plugin calls. To see exactly what a rollover put into the lane,
read `<project>/.opencode/handoff/<session id>.md` (the lane's own words) and run `handoff build` by hand for the same session
(`--newer-than 0`; `handoff leads` lists the agents the plugin leaves alone): the summary is the code-written parts plus those notes. Set `AGENTGRAPH_ROLLOVER_DEBUG=1` to log every
compaction-hook call too.

## `handoff build`: exit codes and size

0 summary printed; 3 notes missing, empty, or written before `--newer-than` (without it: older than `--max-age`, default 1800 s);
**4 the task (the lane's first user message) could not be read** from the database (locked, unreadable or absent): no summary is
printed, the plugin falls back to a normal model summary, because a summary without the task is worse than none; 1 other failure;
2 bad arguments. The database is found like the rest of `agentgraph` finds it (`sources.DB_PATH`), or under `$XDG_DATA_HOME/opencode/`
when that is set. The summary stays under `--limit` (24,000 characters): the task keeps its room first (up to 6,000 characters), the
other code-written parts and the notes share the rest, and notes that are too long keep their head and their last 2,000 characters
(where the next step is), with the cut marked. A git command that times out is said (`git timed out`) and the build carries on
without the list of files.

## Fallback

If the notes are missing or old, the task cannot be read, the build fails or takes too long, or the plugin errors, nothing is set and
OpenCode makes its own model summary as before. If `handoff build` cannot start at all (exit 2, or the command is missing; `bin/agentgraph`
needs Python 3.11 or newer), the plugin logs one line, `build_unavailable`, and asks no lane for notes in that process. Every handler
catches and logs its own errors; the plugin never throws into OpenCode. Events are queued per session, so a slow call for one session
does not hold up another, and nothing waits on the compaction itself (`compact` returns when the request is queued: 2 ms measured).

## What was measured

OpenCode 2.3.2, Sonnet 5.5, one task of 12 generated modules (about 280,000 tokens of reading, a sign-off word given only in the
first message). Single runs, on a scratch config:

| | dollars | weighted tokens | turns | largest context | compactions |
|---|---|---|---|---|---|
| off, as the live config is | 1.36 | 679,817 | 27 | 154,672 | 2 (model summaries, $0.43) |
| off, with `background:false` | 1.79 | 895,851 | 31 | 156,364 | 2 (model summaries, $0.78) |
| on | 1.02 | 511,344 | 31 | 134,383 | 2 rollovers, $0 |

After the second round of fixes (hook only for the plugin's own compaction, one coverage rule, state outside the project, back-off, validated
settings), the same task with the plugin on and `compaction.background: false`, one full run: 511,666 weighted tokens ($1.02, 31 turns,
largest context 134,395, 2 asks, 2 rollovers at $0, check passed, sign-off word right) against the 3-run mean of 689,268 with the plugin
off: -25.8%. A child lane through the task tool: 603,921 weighted tokens ($1.21, 3 asks, 2 rollovers, check passed, word right). Writing
the notes took 2,777 output tokens in the full run (3,991 in the child run); the step that answers an ask, all counted, was 85,990
weighted tokens (69,411), most of it the cache read every step pays anyway. A manual compaction with the context past `ask_at` and no
notes got a normal model summary ($0.21) and the log said why. A compaction that failed shows as `session.compaction.failed` with an
`error.type`; a compaction that ended well as `session.compaction.ended`, which has no status field.

Round 7, one run (`--plugin on --child`, label `r1fix7`, $1.46): 729,925 weighted tokens; cycle 1 fell back to a model summary ($0.40) because of a guard that was then removed (see the item-1 note above), cycle 2 got the code summary from the own-compaction path at 157k. Not repeated after the fix.

Round 6 (the hook also answers OpenCode's own compaction when the notes are fresh), label `r1fix6`, `compaction.background: false`, one run each:

| run | weighted tokens | dollars | turns | asks | plugin rollovers | compactions (model summaries) | largest context | cache write |
|---|---|---|---|---|---|---|---|---|
| off, child | 739,594 | 1.48 | 34 | 0 | 0 | 2 (2) | 157,425 | 481,927 |
| on, child 1 | 530,060 | 1.06 | 32 | 2 | 2 (own-compaction supplied) | 2 (0) | 157,860 | 318,101 |
| on, child 2 | 541,877 | 1.08 | 45 | 2 | 2 | 2 (0) | 134,500 | 281,536 |
| on, full | 567,917 | 1.14 | 42 | 2 | 2 | 2 (0) | 134,507 | 308,839 |

All passed the check and the sign-off word. Child on against child off (mean of the two on runs, 535,969 weighted tokens, $1.07): -27.5% in weighted tokens and in dollars. The full run
(567,917) is -17.5% against the off mean of 688,209. Child-on run 1 had the round-5 failure shape: `19 20 20 8 32 32 56 57 81 82 106 106 130 131 [ask] 155 [roll] 9 10 34 34 59 59 83 84 108 109 133 133 [ask] 158 [roll] 9 10 10 10`:
the answering step at 155k and 158k, OpenCode's own compaction first, and now the hook gave it the code summary (log: `own_compaction: supplied`, 2 times; model summaries 0). In the off child run both compactions are OpenCode's own model summaries at 153k and 157k ($0.42).
Run 2 rolled at 132k and 133k by the plugin's own request. The context of 155k to 158k in run 1 is the step before the roll, not a step after it; it is the size the lane had when the ask was answered.

Round 5 (ask late at 115k, roll at the step end that holds the answer, `notes_gap` 10,000, `hook_growth` 15,000, no `roll_at`), label `r1fix5`, plugin on,
`compaction.background: false`. Three full runs: 569,763 / 569,025 / 568,718 weighted tokens ($1.14 each, 42 turns, 2 asks, 2 rollovers at $0, largest context
134,260 / 134,471 / 134,625, cache write 311,448 / 308,782 / 309,043). Mean 569,169 against the off mean of all six off runs, 688,209: **-17.3%**, and the three
runs are within 0.2% of each other. No module was read again after a rollover. Per-step context of the first run (k tokens):
`8 31 32 56 57 57 81 82 82 106 106 106 130 131 [ask] 131 133 [roll] 9 33 33 34 58 58 58 82 83 83 107 108 108 132 132 [ask] 133 134 [roll] 9 33 33 34 58 58 58 59 59`
(five modules, then five, then two: twelve reads, the notes are written in the step right after the ask and the roll follows at once).
**The child-lane run did not work:** 945,992 weighted tokens ($1.89, 33 turns, 2 asks, 0 plugin rollovers, 4 compactions, largest context 156,774).
Its lane's steps fall on other sizes: `8 31 32 56 56 81 81 105 105 129 129 [ask] 154 [roll] 9 [roll] 9 33 33 58 58 82 83 107 107 132 132 [ask] 157 [roll] 9 [roll] 8 8 9 9`.
The ask went out at 129k; the step that answers it carries the 25k read that was already in flight, so its prompt is 154k, and at the end of that step OpenCode's
own automatic compaction started (reason `auto`, 4 ms before the plugin's request) and made a model summary of 153k tokens ($0.40); the plugin's request then
queued a second compaction on the small context ($0.02, also a model summary). Same in the second cycle (157k). The plugin's hook correctly gave
neither a code summary (the first was not its own, the second had its request cleared), as designed in round 3; the cost of that design is these $0.83. In the earlier child runs the plugin's own
request won this race at 158k (round 2) or the lane's steps landed lower. So the limit of this design is a lane whose step sizes put the answering step at about 150k or more.

Round 4 (the early roll removed: a rollover needs the context at or above `roll_at` and the notes within `notes_gap`), label `r1fix3`, plugin on,
`compaction.background: false`: full run 1 648,452 weighted tokens ($1.30, 45 turns, 3 asks, 2 rollovers, largest context 133,790); full run 2 572,437
($1.14, 37 turns, 2 asks, 2 rollovers, 134,187); child lane 543,425 ($1.09, 34 turns, 2 asks, 2 rollovers, 134,612). All passed check and sign-off word.
Mean of the two full runs 610,445 against the off mean of 689,268: -11.4%. Two rollovers again, no context over 135k, but the saving
did not come back to round 2's -25.8%: the two full runs differ by 76,000 tokens from each other (the task's own length varies, 37 vs 45 turns), so with
three or four runs a mean is needed before comparing rounds. Round 3 (with the early roll) is kept below for the record.

Round 3 (state reload, roll at the answered ask, hook re-check, leads, `notes_dir` rule), label `r1fix2`, one run each, plugin on, `compaction.background: false`:
full 607,051 weighted tokens ($1.21, 37 turns, 3 asks, 3 rollovers at $0, largest context 133,005); child lane 620,699 ($1.24, 44 turns, 3 asks, 3
rollovers, largest context 132,223); both passed the check and the sign-off word. Against round 2 (511,666 / 134,395 full; 603,921 / 158,029 child)
rolling earlier made the full run **dearer** (+18.6%: three rollovers, not two, each paying the roughly 8,900-token restart and a cache write)
and the child run about the same (+2.8%), but the largest context fell by 25,000 tokens in the child run. Against the off mean of 689,268 the
full run is -11.9% and the child run -9.9%. These are single runs; round 2's 511,666 was likely the low end. The notes-writing step cost 3,801 / 3,921
output tokens and 37,458 / 38,094 weighted tokens in all. If the extra rollover is not worth it for your tasks, raise `ask_at`.

Both tasks passed their check and the sign-off word was right in all three. A killed process during a rollover left a session that
finished. A lane started through the task tool is seen, rolled over and finishes, and its parent gets the result.

## Not covered

- Single runs; the spread between runs is not known. The 3-against-3 comparison is separate.
- The lane's own decisions survive only if it writes them into the notes (the first user message is always kept). A lane told
  to keep something out of files lost it in a probe.
- The summary replaces only the old part; the last `keep.tokens` of messages stay word for word.
- A rollover starts at a step boundary at or above `ask_at`, so the context can pass it by one step (about 10,000 to 25,000 tokens).
- After a kill during a rollover, OpenCode may re-run the queued compaction in the next process with a model summary of the small
  context ($0.03 measured).
- Only the model families and settings above; nothing tested with another provider or with `compaction.auto: false`.
