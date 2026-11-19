# agentgraph-tui

The agent graph of the current OpenCode session, as text, in the terminal sidebar. It is the terminal sibling of
`../desktop-plugin/` and reads the same snapshot document (`../WEB_API.md`, section 1).

## What it shows

```
◉ orchestrator ⠋                 4/11
━━━━━━━━━━━━━────────────────────────
$6.57  12.0k↑ 70.9k↓  ctx 124k

! impl-claude stuck ↻5
! reviewer stuck
↩ impl-open… sent back ↻3 by reviewer

⠋ impl-claude ↻5                $1.35
  Apply the limiter in the middlewar…
⠙ designer                      $0.09
  429 error page and retry-after mes…

○ council fable astra carbon
○ oracle oracle-lite
○ impl-openai impl-hard
○ reviewer
○ explorer researcher
```

Four blocks, one blank line between them; an empty block is left out.

- **Run**: the hub (`◉`, a spinner after the name while it works), `passed/total` on the right, a progress bar across
  the width, then spend: dollars, tokens in `↑` and out `↓`, and `ctx` (tokens in the hub's context window) when the
  snapshot has it. Tokens read `950`, `12.4k`, `2.1M`.
- **Needs you** (only when something does; at most 5, then `+N more`), most urgent first, one line per agent:
  `! name stuck ↻N` (a stuck work loop, or a failed, blocked or stalled step), `↩ name sent back ↻N by evaluator`
  (a review loop that sent the work back), `? name waiting` (reported a pass, nobody has checked it). An agent that
  is both sent back and waiting shows the sent-back line only.
- **Working**: agents that are busy (not the hub), most expensive first. Name, `↻N` once its work loop is on round
  2 or more, the agent's total cost on the right, and its current step below.
- **Idle**: everything else, dimmed, one line per group (council, decide, build, verify, understand, others); the
  council lead first, seats by their short name; long groups wrap onto an indented line.
- An error document is one line of error text. `Loading` before the first snapshot, `No agents` when there are none,
  `No session` outside a session.
- Nothing animates unless something works: the frame timer runs only while the hub or any agent is busy.
- Live things use the theme's warning or accent colour (whichever is nearer terracotta). A green one is never used:
  if both are green, live things are the plain text colour in bold.

## Enable

Add the folder to `plugins` in `~/.config/opencode/cli.json`:

```json
{
  "$schema": "https://opencode.ai/v2/cli.json",
  "plugins": [
    "oh-my-opencode-slim",
    "/Users/achita.anantachina/.config/opencode/orchestration/tui-plugin"
  ]
}
```

Restart the TUI. Options go in the `[path, options]` form:

```json
["/Users/achita.anantachina/.config/opencode/orchestration/tui-plugin", { "mode": "prepend", "intervalMs": 4000 }]
```

| Option       | Default                                                               | Meaning |
|--------------|-----------------------------------------------------------------------|---------|
| `mode`       | `"replace"`                                                           | `"replace"` takes over the sidebar (hides Context, Limit, MCP, Todo). `"prepend"` puts the graph above them. |
| `intervalMs` | `4000`                                                                | Refresh period; minimum 1000. |
| `python`     | `/Users/achita.anantachina/.local/state/tec/toolchain/base_profile/bin/python3` | Absolute path to Python 3.11+ (`/usr/bin/python3` is 3.9 and has no `-P`). `~/` is expanded. |
| `home`       | `~/.config/opencode/orchestration`                                   | The folder that contains `agentgraph/` (used as `PYTHONPATH`). |

To keep the built-in sections, use `"mode": "prepend"`.

## Disable

Remove the line from `plugins` in `~/.config/opencode/cli.json` and restart the TUI.

## How it gets the data

Every `intervalMs` it runs, without a shell,
`<python> -P -m agentgraph.snapshot_json --session=<sessionID>` with `PYTHONPATH=<home>`, in the session's project
directory (`location.directory`, else `home`). Like the Desktop plugin: SIGKILL after 20 s, output capped at 8 MiB,
one process per session at a time, and every failure becomes one line of error text. A result that arrives after
the session changed is dropped. The cleanup stops both timers and kills a running child.

## Limits

- The sidebar is host-owned: 42 columns, about 37 usable; every line is cut to 37 display columns.
- Text only; no mouse actions.
- The host shows the sidebar only when the terminal is wider than about 120 columns, and never in a subagent's own
  session.
- Glyph widths assume a terminal that draws `◉ ○ ━ ─ ↻ ↩ ↑ ↓ ⠋` one column wide (East Asian "ambiguous" width set to
  narrow, the default almost everywhere).

## Files

- `src/tui.js`: the `./tui` entry. Imports `@opentui/solid` (`createElement`, `insert`, `setProp`), `solid-js`
  (`createSignal`) and node built-ins, the same specifiers the installed `oh-my-opencode-slim` TUI plugin
  (`dist/tui2.js`) imports. Default-exports a plain `{ id, setup }` object, as that plugin does; it does not load
  `@opencode/plugin` at run time.
- `src/plugin.js`: the slot, timers, theme mapping and session handling, written against injected host functions.
- `src/render.js`: pure rendering, `render(snapshot, { width, frame, now })` → lines of `{ text, tone }` spans.
- `src/runner.js`: the snapshot process runner.
- `test/run.mjs`: `node tui-plugin/test/run.mjs` (plain node, exits non-zero on failure). `test/fixtures/demo.json`
  is a saved `agentgraph.snapshot_json --demo` document.

## Not verified

None of this has run inside a real OpenCode TUI. Specifically unverified:

- That OpenCode 2.3.2 loads a local directory listed in `cli.json` `plugins` through its `package.json` `./tui`
  export, and accepts a plain `{ id, setup }` default export (no `Plugin.define` wrapper).
- That the host resolves `@opentui/solid` and `solid-js` for a plugin outside its package cache (the tests use stubs).
- That `ui.slot({ replace: "sidebar.content", render })` and `{ prepend: … }` behave as documented, that `render`
  receives a reactive `props.sessionID`, and that the returned cleanup is called.
- How options reach `setup`: the plugin reads `context.options`, else `setup`'s second argument; the `[path, options]`
  form in `cli.json` is assumed.
- The theme shape: it reads `theme.text` (`default`, `muted`/`subdued`), `theme.warning`, `theme.accent`,
  `theme.error`, as strings or `{ r, g, b }` colours. Unknown shapes fall back to the host's default text colour.
- Bold through the `attributes: 1` text property (OpenTUI `TextAttributes.BOLD`), used only for a green-only theme.
- That a row of `<text>` elements inside a `<box flexDirection="row">` draws on one line without wrapping.
- Spawning a child process from the TUI process (the installed example does it; this plugin was only run under node).
