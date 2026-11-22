# agentgraph-rollover

An OpenCode 2.3.x (V2 API) server plugin that resets a long worker lane to its own handoff notes, with a summary built by code
and no model call. Full description, settings and measurements: `docs/ROLLOVER.md`.

- `index.js`: the plugin (`module.exports = {id, setup(ctx)}`); plain Node, no packages.
- `rollover.json`: settings (every key with its default; a missing file means the same; a bad value means the default plus one log line).
- `test.js`: no-network test with fake events: `node rollover-plugin/test.js`.

**Install:** copy this folder to `<opencode config>/plugins/agentgraph-rollover/`, add the one needed setting
`{"compaction": {"background": false}}` to `opencode.json`, restart OpenCode.

**Where files live.** The lane's notes: `<session folder>/.opencode/handoff/<session id>.md` (`notes_dir` can move them to another folder
**under `.opencode/`**, never elsewhere), beside a `.gitignore` of `*` that the plugin creates. Everything else (`state/<session id>.json`, `rollover.log`): `<data home>/agentgraph/rollover/` (`$XDG_DATA_HOME` or
`~/.local/share`), outside every project. Nothing is written for a session the plugin does not act on.

**What it never touches:** `opencode.json`, the lane's code, git, root sessions, the agents in `skip_agents` (default orchestrator,
build, plan, side-chat, council) and the hubs, council leads and team leads that `agentgraph handoff leads` prints (read once at start;
`skip_leads: false` turns that off). It sets a compaction summary only for a compaction it started; any other compaction gets a normal model summary.

**When it rolls:** the ask goes out at the first step end at or above `ask_at` (default 115,000). It tells the lane to finish its tool call and write the
notes NOW, because the context is replaced right after. The roll starts at the first step end at or above `ask_at` where the notes cover the context
(written after the previous step ended, grown past by at most `notes_gap`, default 10,000): normally the step in which the lane wrote them. Notes followed by a
big read are not coverage; the lane is asked again. There is no `roll_at` any more (an old `rollover.json` that has it still loads; the key is ignored and logged).
When OpenCode itself starts a compaction (automatic, at about 150k) the plugin supplies the code summary too if the notes written after the last ask are fresh
(the file is read at that moment, and no step that is running began after them); otherwise OpenCode makes its normal model summary. A second compaction that arrives before any step
has ended since a rollover gets the same summary again (no build). A `handoff build` exit 4 (task unreadable) leaves that session alone for the rest of the process. When OpenCode asks for the summary the plugin checks coverage again (`hook_growth`, default 15,000). Several OpenCode processes on one session are safe: the state
file is reloaded when another process changed it.

**Turn off:** `"enabled": false`, or `AGENTGRAPH_ROLLOVER=off`, or remove the folder.

**Audit:** `agentgraph handoff log [--session ID]` shows the decision log (asked, rolled, skipped, fell back, compaction_failed, ...);
`agentgraph handoff check --session ID` the notes; `AGENTGRAPH_ROLLOVER_DEBUG=1` also logs every compaction-hook call.

It uses `agentgraph handoff build` (see `agentgraph/handoff.py`) as a child process; that needs Python 3.11 or newer.
