# agentgraph-output-caps

An OpenCode 2.3.x (V2 API) server plugin that cuts very long tool output before it enters a chat. Measured on real chats: `read` makes
80% of all tool output characters (mean 74,399 a call, largest 351,752) and `shell` 17.7% (mean 3,137, largest 51,192). Output that is long
is paid for again at every later turn, so cutting it is the cheapest saving left.

**What it does.** After a tool finishes, if its text is longer than the cap, the plugin keeps the start (about 70%) and the end (about 30%),
cuts on line boundaries (a numbered line from `read` is never split) and puts one plain line in the gap, for example
`[31000 characters (620 lines) were left out here to keep the chat short. Read the file again with offset and limit to get them.]`.
Other tools get "Run the command again with a narrower command or filter the output to get them." With several text parts, the cap counts their total.
It also adds `metadata.capped = {original, kept}` to the result.

**The two numbers.** `read`: 40,000 characters. Every other tool: 20,000 characters.

**What it never touches:** file parts (images, PDFs), errors, results already under the cap, and the tools in `exempt_tools` (default: `edit`, `write`,
`question`, `skill`, `subagent`, `task_status`, `task_message`, `task_reply`, `task_cancel`, `interview_submit_state`). If anything unexpected
happens, the result is left exactly as it was and an `error` line (no text) goes to the log. It never throws into OpenCode.

**Files**
- `index.js`: the plugin (`module.exports = {id, setup(ctx)}`); plain Node, no packages.
- `output-caps.json`: settings. A missing file means the defaults; a bad value means the default for that key plus one `settings_invalid` log line.
  `enabled` (true), `read_chars` (40000), `other_chars` (20000), `head_share` (0.7: the share kept from the start), `exempt_tools` (the list above),
  `log_dir` (null: the default folder below). The file is read again when it changes.
- `test.js`: no-network test with made-up results: `node output-caps-plugin/test.js`.

**Log.** `~/.local/share/agentgraph/output-caps/output-caps.log` (`$XDG_DATA_HOME` is honoured; `AGENTGRAPH_OUTPUT_CAPS_HOME` or `log_dir` moves it).
One JSON line per cut: time, session, tool, original size, kept size, event (`capped`; also `error` and `settings_invalid`). It holds no output text.
A log over 5 MB is cut to its last 1 MB at start.

**Turn off:** `"enabled": false` in `output-caps.json`, or `AGENTGRAPH_OUTPUT_CAPS=off` in the environment, or remove the folder.

**Install:** copy this folder to `~/.config/opencode/plugins/agentgraph-output-caps/` and restart OpenCode.
**Remove:** delete that folder and restart OpenCode.

**Where the event shape comes from.** The shape (`event.tool`, `event.status`, `event.result = {output?, content, metadata?}`, where `content` is a string or a list of parts, and
OpenCode reads `event.result` back after the hook) was read from the code inside the installed OpenCode 2.3.2 program, not from a live run with this plugin.
When `content` is missing or empty, OpenCode uses `output` as the text; the plugin caps that case too (only when `output` is a string).
