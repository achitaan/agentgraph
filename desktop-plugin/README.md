# agentgraph-desktop

An OpenCode Desktop GUI plugin that adds an **Agent graph** tab to a session's side panel. The tab shows the same live
view as `agentgraph serve`: the agent roster, busy counts and the current task graph, drawn by the framework-free
`../agentgraph/web/app.js` with `../agentgraph/web/style.css` (see `../WEB_API.md`, section 4).

While the hub works, its planet carries a chip with a glyph for the kind of action (web, shell, files, search, subagent), the
site or command, and a short trail of the last few actions; the details panel lists them. The chip and trail are drawn in the
flat view only; the 3D view leaves them out. The snapshot's
`activity.zone`, `target` and `recent` feed it (`../WEB_API.md`); the zone and trail idea is AgentMove's (MIT).

It is a GUI-only plugin (no server or TUI part), loaded as a local directory. Its id is the `name` in `package.json`:
**`agentgraph-desktop`** (not reserved, not in the `opencode` namespace).

## Files

| File | What it is |
| --- | --- |
| `src/plugin.cjs` | Hand-written plugin source (CommonJS, no JSX). |
| `build.mjs` | Dependency-free build: writes `gui-experimental.cjs` from `src/plugin.cjs` + `app.js` + `style.css`. |
| `gui-experimental.cjs` | The built bundle the desktop app loads. Generated; do not edit. |
| `package.json` | Only `name` (the plugin id). Deliberately no `exports`; see below. |
| `test/harness.mjs` | Dependency-free checks that run without the desktop app. |

## How it works

- **Window (`setup`)**: adds the styles, a side panel with one tab (`agentgraph-desktop:graph`, title "Agent graph"),
  an "Agent graph" item in the side panel's **+** menu, and an "Open agent graph" command in the command palette. The
  panel's `render` returns a plain `<div>` that fills the sidebar without adding scrollbars, calls
  `AgentGraph.mount(element, { fetchSnapshot, intervalMs })`, and registers `destroy()` with Solid's `onCleanup`.
  On a session switch, the plugin calls `reset()` (or destroys and remounts older views) so the previous graph is
  cleared before fetching the new session. `fetchSnapshot` reads `props.session` each time it runs.
  Clicking a working subagent opens its session in a focused chat tab. If opening fails, the plugin offers the ID via
  the clipboard and a toast when available. Clicking the panel's own session still shows its graph details.
- **Main process (`main`)**: provides the Ipc `agentgraph-desktop.snapshot` with one method,
  `snapshot({ directory, session }) -> JSON text`. It runs
  `python3 -P -m agentgraph.snapshot_json [--session=ID]` with `execFile` (no shell), `cwd` = the session's directory,
  `PYTHONPATH` = the orchestration directory, a 20 s timeout and an 8 MiB output cap. Overlapping requests for the same
  (directory, session) share one process. Any failure returns `{"version": 1, "error": "<one line>"}`.
- **The preset, the saved setups and the spend limit** (the run details the top total opens; `options.actions.setup` and
  `spend`, neither given in demo mode): two more Ipc methods, `setup({ op, directory, name, record, replace, expect? })` and
  `spend({ op, directory, limit, warn })`, every field a string, run `python3 -P -m agentgraph.setups preset switch|restore ...`
  or `setup save|restore|remove|undo ...` and `python3 -P -m agentgraph.spend limit AMOUNT [--warn W] | --off`, each with
  `--dir <the session's directory> --json`, with `execFile` (no shell) and the other writes' environment and limits. There is no
  free text to send: a setup name is checked against the flow-id rule, a preset name against a plain-name rule, an amount
  against a number of dollars and an undo record against an absolute path, in the window and again in main; a bad one never
  reaches Python. `expect` goes before `--json`; a stale refusal rejects with `status: 409, stale: true, rev`.
- **The chat on every agent** (the view's details; `options.actions.chat`, `send`, `stop`):
  - `chat(sessionID, { limit })` reads one session's recent chat through a second Ipc method,
    `chat({ session, limit }) -> JSON text`, which runs `python3 -P -m agentgraph.chatfeed --session=ID --json --limit=N`
    (the `agentgraph chat` command; `--demo` added in demo mode). It is read only for a session of the last snapshot's tree, or the snapshot's `sidechat.session`, which is read with `--side` (each question cut before its brief, no reasoning, as `GET /api/chat` shows it); any other id is refused without running Python (the demo reads its example chats) with the same `execFile` rules, environment, 20 s timeout
    and 8 MiB cap as the snapshot. The session id (the snapshot's id pattern) and the limit (a whole number, 1 to 200;
    40 when not given) are checked in the window and again in main; a bad one never reaches Python. The feed crosses as
    the command printed it (it holds no tool input or output); an error document or a failed command rejects the
    promise with one short line. The page keeps one read in flight per session; the plugin queues nothing.
  - `send(sessionID, text, { steer })` calls the host's own `props.session.server.client.session.prompt`: `{ sessionID,
    text, delivery: "steer" }` when `steer` is true, else the plain `{ sessionID, text }` the run message already uses.
    The text is 1 to 4,000 characters. `stop(sessionID)` calls `client.session.interrupt({ sessionID })`. Both answer
    `{ ok: true }` or `{ ok: false, error: "<one line>" }` and never throw; a host call that throws, rejects or answers
    `{ error }` is `{ ok: false }`. Both accept only a session of the last snapshot this panel read (the root, or one
    of the agents' sessions) and refuse any other without calling the host. The panel gives `send` only when
    `client.session.prompt` is a function and `stop` only when `client.session.interrupt` is, so the page shows no
    dead control. The demo is given `chat` alone (it reads the demo's example chats); the page code is the same.
- **The side chat's moon and its question** (`options.actions.side`): when the snapshot says the side chat is installed, the
  view draws a small moon named `side chat` and opens a panel with its chat and a box. `side(text)` asks it through a
  third Ipc method, `side({ main, text }) -> JSON text`, which runs
  `python3 -P -m agentgraph.sidechat ask --main <the snapshot's root session> --text-file - --json` with the same
  `execFile` rules and environment as the other commands but a 150 s timeout. The question goes on stdin only: it is in no
  argument and in no log. The question (1 to 4,000 characters, no NUL) and the root session id are checked in the window
  and again in main; a bad one never reaches Python, and nothing is asked before a snapshot has been read. The reply is
  `{ ok: true, answer }` or `{ ok: false, error: "<one line>" }` and never throws. It is given in every mode but the demo
  (the page shows the box only when the side chat is installed); the demo has no `side`.
  The Desktop app's own process carries no model providers, so `agentgraph sidechat` asks through the shared background
  service when that service runs from the very CLI file it is about to run (it never attaches a CLI of another version),
  and on a private `--standalone` server otherwise; a standalone failure about providers or credentials says so in its one
  line (`AGENTGRAPH_SIDECHAT_ROUTE=service|standalone|auto` overrides the choice).
  - Nothing here logs: no message text, nor the feed, is written to a log or put on a command line.

## Load it

OpenCode Desktop loads GUI parts only from plugins listed in the **global** config, under the V2 key **`plugins`**
(plural). Add this to `~/.config/opencode/opencode.json` (not done for you):

```json
"plugins": [
  {
    "package": "/ABSOLUTE/PATH/TO/.config/opencode/orchestration/desktop-plugin",
    "options": {
      "python": "/ABSOLUTE/PATH/TO/python3"
    }
  }
]
```

The short form `"plugins": ["/ABSOLUTE/PATH/TO/.config/opencode/orchestration/desktop-plugin"]` (or the
relative `"./orchestration/desktop-plugin"`, resolved from the config file) also loads it, but then `main` uses
`python3` from the desktop app's `PATH`. An app started from the Dock or Finder usually gets the system `PATH`, where
`/usr/bin/python3` is Python 3.9 on this Mac; it has no `-P` option and cannot run `agentgraph`, so the panel would show
"Python 3.11 or newer is required". Set `options.python` to a Python 3.11+ executable to avoid that.

**Native Windows.** There is no `python3`: without `options.python` the plugin asks `py -3`, then `python`, once at
start, for a Python 3.11 or newer and uses the `python.exe` path it reports (a Python that old or missing leaves plain
`python` and the panel says to set `options.python`). Give the package as a full Windows path or file URL, for example
`C:\\Users\\NAME\\.config\\opencode\\orchestration\\desktop-plugin`, and `options.python` as
`C:\\Users\\NAME\\AppData\\Local\\Programs\\Python\\Python312\\python.exe`; `~\\` is expanded like `~/`. All the editors
are offered on Windows as on other systems. Nothing runs through a shell, so there is no `.cmd` or PowerShell step.

Options (all optional):

| Option | Default | Meaning |
| --- | --- | --- |
| `python` | `python3`; on Windows `py -3`, then `python`, resolved to its `python.exe` | Python executable (absolute path recommended; `~/` is expanded, on Windows also `~\\`). |
| `home` | `~/.config/opencode/orchestration` | Directory that contains the `agentgraph` package. |
| `intervalMs` | `4000` | Refresh interval in milliseconds; values below 250 are ignored. |
| `demo` | `false` | `true` passes `--demo`, to see sample data. |

Changing options reloads the plugin.

**The existing `"plugin": ["oh-my-opencode-slim"]` array** is the V1 key (singular). Adding `plugins` does not change
or remove it, and this plugin does not depend on it. According to the V1 migration guide, V2 reads only `plugins` and
V1 plugin implementations do not run in V2; whether your current setup still needs the `plugin` entry is a separate
question this plugin does not answer.

### Why `package.json` has a `name` and no `exports`

The desktop app's server (2.3.2) resolves a local plugin directory like this: when `package.json` has both `name` and
`exports`, it resolves `<name>/gui-experimental` through `exports` (a package self-reference). Otherwise it loads
`gui-experimental.cjs` (or `.js`) at the directory root, and the plugin id is still taken from `package.json`'s
`name` (the directory name only when there is no `name`). With only `name`, the id is the stable `agentgraph-desktop`
whatever the directory is called, and the bundle is found by its file name without relying on self-reference
resolution. There is no `server`/`index` file at the root, so no server part is loaded.

## Rebuild

```sh
cd ~/.config/opencode/orchestration
node desktop-plugin/build.mjs        # updates the live bundle; prints its path and size
node desktop-plugin/test/harness.mjs # builds and tests a fresh temporary bundle
```

Rebuild after every change to `src/plugin.cjs`, `agentgraph/web/app.js` or `agentgraph/web/style.css`. Each rebuild
of the live bundle reloads the GUI in every window. The main process keeps the copy it loaded when the app started:
a method added since then (seen for `side` and `loops`) answers `no method` until you quit the app and open it again,
and the panel says so. The harness never replaces the live
bundle. If `app.js` or `style.css` is
missing, the build still succeeds and embeds a placeholder view (agent names and busy counts); if `app.js` exists but
does not parse, the build fails instead of shipping a broken bundle. `--app`, `--css` and `--out` override the paths.

## Confirm it loaded

- **Settings > Extensions > Plugins** (with the app's own server selected) should list `agentgraph-desktop` with its
  switch on. "Not loaded" or an error there means the bundle failed to evaluate or the id was rejected.
- The command palette has **Open agent graph**, and the side panel's **+** menu has **Agent graph**.
- `~/.local/share/opencode/log/opencode.log` is the server's log: search it for `agentgraph-desktop` or
  `loading plugin` to see the directory being loaded (and any load error).
- The desktop app's own logs are in `~/Library/Application Support/ai.opencode.desktop/logs/<start time>/`.
  `main.log` should contain `agentgraph snapshot bridge ready` with the `home` and `python` that `main` uses (written
  with `ctx.log.write`); `renderer.log` has window-side errors.
- In the panel itself, a failed snapshot shows the one-line error from the main process (for example a Python that is
  too old or not found).

## Turn it off

- Turn off just its desktop UI with its switch in **Settings > Extensions > Plugins**.
- Or add `"-agentgraph-desktop"` to `plugins`, or remove its entry from `plugins`.

## What could not be verified here

Checked without the desktop app (`test/harness.mjs`): the bundle parses; evaluated the way the 2.3.2 host evaluates it
(`Function("require", "module", "exports", code)` with only the shared modules), in main-process and window modes,
the export is the `Plugin.define` result; `main` provides exactly one Ipc; `setup` registers exactly the style, panel,
menu item and command with the right ids and titles; the real handler runs the real Python command and returns a
version 1 document with `agents`; a missing or outdated Python, timeout, oversized output, invalid JSON, and non-zero
exit return one-line error documents; overlapping requests spawn one process. The panel and view root fill the sidebar
without scrolling; the harness checks the poll interval and both session-switch paths (`reset` and destruction/remount).
Cleanup calls `destroy`, and the placeholder view draws names as text.

Not verified, because it needs the running desktop app:

- that the panel, menu item and command appear and the tab opens; that Solid inserts the returned `<div>` and runs the
  `onCleanup` registered in `render` when the tab unmounts (both follow the guide and Solid's semantics);
- that the host accepts the declared SVG icon and the `{{error}}` message parameter;
- the real Ipc round trip through Effect `Schema` (`Struct` of two strings in, `String` out);
- which `PATH` the Electron main process gets, and so which `python3` the default resolves to;
- whether the real `app.js` draws correctly inside the side panel (the harness replaces `mount` with a spy and never
  draws the real view in its fake DOM);
- whether the V1 `plugin` key alongside `plugins` causes any warning in your OpenCode version.

Not yet tried on the real app (the harness uses a fake client; the chat read uses the real Python command):

- `client.session.prompt({ sessionID, text, delivery: "steer" })` in Desktop: the flat shape with `delivery` is proven
  in the terminal host on OpenCode 2.3.2 only, and whether Desktop's client accepts it, and delivers the message into
  the working turn, is untried. A plain prompt in Desktop is the call the run message already makes.
- `client.session.interrupt({ sessionID })` in Desktop: proven in the terminal host only; untried here. It does nothing
  to an idle session.
- that `props.session.server.client` exposes `session.prompt` and `session.interrupt` at all in the app (a missing one
  leaves out Send or Stop, by design); and that the `chat` Ipc method (a `Number` field in its input schema) crosses the
  host's bridge like the string methods do.
- the chat view drawing and scrolling inside the real side panel.
- asking the side chat through the bridge: the `side` Ipc method with a real `agentgraph sidechat ask` (a real model answer
  taking up to two minutes), and the moon and its panel drawing inside the real side panel.
