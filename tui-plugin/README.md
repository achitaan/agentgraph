# agentgraph-tui

The agent graph of the current OpenCode session, as text, in the terminal sidebar and, on demand, in a session panel. It is the terminal sibling of
`../desktop-plugin/` and reads the same snapshot document (`../WEB_API.md`, section 1).

## What it shows

```
◉ orchestrator ⠋                 4/11
━━━━━━━━━━━━━────────────────────────
$6.57  12.0k↑ 70.9k↓  ━━━━──── 124k

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
  the width, then spend: dollars, tokens in `↑` and out `↓`, and the context of the root session. Tokens read `950`,
  `12.4k`, `2.1M`.
  - With `root.wear` (`../WEB_API.md`): a bar 8 cells wide (`━` filled, `─` empty; the full bar is the distance to
    `compact_at`, or to `window` when `compact_at` is null), the number (`124k`), then one `•` per summary (at most
    five) and the count, as in `━━━━──── 124k ••2`. The fill is muted below 60%, the live colour from 60% and the
    danger colour from 85%; never green. Any context above zero fills at least one cell (so 1k of 250k shows one
    cell, not none), and the bar is full only from 100%. When `level` is `worn`, a muted line `summarized N times`
    follows. When the line is too wide it drops the dots first, then the bar, keeping the number; at the sidebar's 37
    columns the dots usually do not fit next to the tokens. The views are never drawn narrower than 37 columns, where
    the number always fits; only below that (a width the plugin never uses) can the number go too, by the older rule
    that drops the context part, then the double spaces.
  - Without `wear` (absent or null): `ctx 124k` from `root.context`, as before, and nothing when that is unknown.
- **Needs you** (only when something does; at most 5, then `+N more`), most urgent first, one line per agent:
  `! name stuck ↻N` (a stuck work loop, or a failed, blocked or stalled step), `↩ name sent back ↻N by evaluator`
  (a review loop that sent the work back), `? name waiting` (reported a pass, nobody has checked it). An agent that
  is both sent back and waiting shows the sent-back line only.
- **Working**: agents that are busy (not the hub), most expensive first. Name, `↻N` once its work loop is on round
  2 or more, the agent's total cost on the right, and its current step below.
- **Idle**: everything else, dimmed, one line per group (council, decide, build, verify, understand, others); the
  council lead first, seats by their short name; long groups wrap onto an indented line.
- An error document is one line of error text. `Loading` before the first snapshot, `No agents` when there are none,
  `No session` outside a session. If drawing itself fails, the reason is drawn as one line of plain text: the view
  is never empty for this plugin's panel or the sidebar.
- Nothing animates unless something works: the frame timer runs only while the hub or any agent is busy.
- Live things use the theme's warning or accent colour (whichever is nearer terracotta). A green one is never used:
  if both are green, live things are the plain text colour in bold.
- Theme: OpenCode 2.2.5 and 2.3.2 pass `text.base`, `text.muted`, `text.feedback.{warning,error,…}.base` and `hue.*`
  scales, as OpenTUI RGBA objects, and no top-level `warning`, `accent` or `error`. Plain is `text.base`, muted
  `text.muted`, live the nearer to terracotta of `text.feedback.warning.base` and `hue.accent` (its 200 step), danger
  `text.feedback.error.base`. The older flat shape (`text.default`, `text.subdued`, `warning`, `accent`, `error`) still
  works. Every colour reaches the host as a `#rrggbb` string, made from a `#rgb`, `#rrggbb` or `#rrggbbaa` string
  (alpha dropped) or any `{ r, g, b }` colour; anything else (a named colour, `rgb(…)`, a token object) is left out
  and the host's default text colour is used, because OpenTUI's `fg` throws for any object but its own RGBA.

## The panel

The host hides the sidebar on terminals narrower than about 120 columns. For those, and whenever you want a bigger
view, the same four blocks open as a session panel:

- Command `agentgraph.graph`, title `Agent graph`, in the command palette, or type `/graph` (alias `/agents`). It
  toggles: it closes the panel if this plugin's panel is the open one, otherwise it opens it. Outside a session
  there is nothing to show, so it only shows a short toast.
- While the panel has the keyboard: `f` toggles full screen, `q` closes it. The panel itself has no title, hints or
  key legend.
- Widths: the text follows the panel's width, never narrower than the sidebar's 37 columns and never wider than
  100, so lines do not stretch across a huge terminal. The host keeps narrow terminals in full screen by itself.
- It watches the panel's session with the same runner and `intervalMs` as the sidebar, starts when the panel opens
  and stops when it closes or the plugin is disposed.
- A host without `ui.panel`, `keymap.layer` or `ui.toast` gets the sidebar only, as before.

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
- `src/plugin.js`: the slots (sidebar, panel), the command, timers, theme mapping and session handling, written against injected host functions.
- `src/render.js`: pure rendering, `render(snapshot, { width, frame, now })` → lines of `{ text, tone }` spans.
- `src/runner.js`: the snapshot process runner.
- `test/run.mjs`: `node tui-plugin/test/run.mjs` (plain node, exits non-zero on failure). `test/fixtures/demo.json`
  is a saved `agentgraph.snapshot_json --demo` document. Its stub `setProp` refuses an `fg` that is neither a string
  nor an RGBA, as the real host does.
- `test/real-host.py`: drives the real terminal app in a pseudo-terminal (`--size 160x45`, `--size 90x40`,
  `--no-graph`, `--binary`), types `/graph`, captures the screen with `pyte` and exits 0 only when the view is on
  it; see its docstring for the scratch folder, the throwaway session and `pyte`. It always runs the app with
  `--standalone`.
- Debugging: `AGENTGRAPH_TUI_DEBUG=<file>` in the TUI's environment appends every error the plugin swallows to that
  file.

## Verified in the real host

`test/real-host.py` ran the terminal app (`opencode-cli` 2.3.2 from OpenCode.app and the `opencode2` 2.2.5 build, both
`--standalone`) at 160×45 and 90×40 on a throwaway session. Proven there:

- OpenCode loads this local directory from `cli.json` `plugins` through the `./tui` export and accepts the plain
  `{ id, setup }` default export; it resolves `@opentui/solid` and `solid-js` for it; `setup` receives `options`,
  `location`, `app`, `renderer`, `client`, `data`, `attention`, `theme`, `themeMode`, `markdown`, `keymap`, `storage`
  and `ui`. In the standalone app `location` was undefined, so the snapshot runs in `home`.
- `ui.slot({ replace: "sidebar.content", render })` draws in the sidebar (at 160 columns: column 120, 37 wide, under
  the host's session title; the built-in sections and the other plugin's sidebar section are hidden), and `render`
  receives `props.sessionID`.
- The command: `/graph` from the prompt runs it (a `keymap.layer` with `mode: "global"` and `slash`).
- The panel: `ui.slot({ append: "session.panel" })` and `ui.panel.open("agentgraph.graph")`; the panel props are
  `name` (`"agentgraph.graph"`), `sessionID`, `width`, `presentation`, `focused`, `focus`, `close`,
  `toggleFullscreen`. At 160 columns the panel takes the last 92 columns and `width` is 92, so `width` is the usable
  text width; at 90 columns it is full screen. `q` from the panel's own `keymap.layer` (no `mode`, made inside the
  panel's render) closes it.
- A row of `<text>` elements inside a `<box flexDirection="row">` draws on one line.
- Spawning the snapshot process from the TUI process works.
- Theme colours: plain is drawn `#eeeeee` and muted `#808080` (the theme's `text.base` and `text.muted`).
- The context bar from `root.wear`: `$0.00  31.3k↑ 4↓  ━─────── 31.3k` (31.3k of 250k, one cell) at 90×40 and
  160×45, in the panel and the sidebar.

The cause of the empty panel (and the empty sidebar) in these hosts: the theme has no `text.default`, so the plugin
passed the whole `text` token object as `fg`; OpenTUI threw (`undefined is not an object (evaluating 'e.length')`),
the error line threw the same way, and the view caught it and returned nothing. `Loading` showed for a moment
because the muted colour happened to be a real colour.

## Running the real-host check

```
python3 tui-plugin/test/real-host.py --size 160x45
python3 tui-plugin/test/real-host.py --size 90x40
python3 tui-plugin/test/real-host.py --size 160x45 --no-graph
```

The script always starts the app with `--standalone`, and so must anyone who drives the app by hand for a test. A
launch without it attaches to the user's shared background service, and when the versions differ it restarts that
service (`opencode-cli` 2.3.2 replaced a running 2.2.5 service once): never do that. On every exit path (success,
an exception, ctrl+c, SIGTERM or SIGHUP to the script) it quits the app and then stops every process the app
started, including the private server, which runs in its own process group (in 2.3.2 it exited by itself when the
app was killed; the script does not rely on that). It signals
only processes it saw descend from its own launch (matched by pid and start time); a SIGKILL to the script itself
cannot be caught.

## Not verified

- `{ prepend: "sidebar.content" }`; that the returned cleanup of `ui.slot` and `keymap.layer` is called.
- That `onCleanup` from `solid-js` runs when the host disposes the panel (it is what stops the panel's timers).
- `f` (full screen) in the panel, and that the panel's key commands honour `enabled`.
- The `[path, options]` form in `cli.json` (`setup` gets an `options` object; only the default options were run).
- The toast variant `"info"` (only outside a session).
- Bold through the `attributes: 1` text property (OpenTUI `TextAttributes.BOLD`), used only for a green-only theme.
- The live colour and the danger colour on screen (the throwaway session never works and never errs).
