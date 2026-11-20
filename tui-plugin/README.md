# agentgraph-tui

The agent graph of the current OpenCode session, as text, in the terminal sidebar and, on demand, in a session panel. It is the terminal sibling of
`../desktop-plugin/` and reads the same snapshot document (`../WEB_API.md`, section 1).

## What it shows

The sidebar (37 columns), with the demo document:

```
! impl-claude stalled ↻5           7m
◉ orchestrator ⠋              11 idle
⠙ designer  429 error page and re… 6m
4/11  gate reviewer refuted
$6.57
```

The panel (here 56 columns) adds a one-column cursor gutter and one muted line of keys:

```
›! impl-claude stalled ↻5  Apply the limiter in the…  7m
 ◉ orchestrator ⠋  Add per-user rate limiting    11 idle
 ⠙ designer  429 error page and retry-after message   6m
 ━━━━────── 4/11  add-rate-limit  gate reviewer refuted
 $6.57
 ↑↓ select  ⏎ open  s stop  m message  g graph  q close
```

One row per thing, most actionable first, no blank rows, no title, no legend; an empty section is left out. At rest
that is 2 to 5 rows; a crowded run stays within 16.

1. **Needs you** (at most 5, then `+N more`), one row per agent, most urgent first: `! name wants permission` and
   `? name asks you` (pending permission requests and questions the host knows about, from OpenCode 2.3's
   `data.session.permission` and `data.session.form`), `? you to decide  <goal>` (a ready `human` step),
   `! name stuck ↻N` (a stuck work loop), `! name failed|blocked|stalled` (a step). The step's goal and how long the
   agent has been at it follow when they fit. A gate that refuted is the review loop doing its job: it is on the
   progress row, not here.
2. **The hub**: `◉`, a spinner while it works, the root session's title, and how many agents are idle (`11 idle`).
   Idle agents are a count, never a list.
3. **Working**: each busy agent (longest at it first, at most 5, then `+N more working`): spinner, name, `↻N` from
   round 2 of its loop, its step's goal (else its session's title), and the elapsed time on the right (`42s`, `16m`,
   `1h5m`, `12h`, `2d`).
4. **Progress**: `passed/total` with a 10-cell bar when there is room, the task's name, and the current gate
   (`next gate verifier`, `gate reviewer checking`, `gate reviewer refuted`, `done`). The current gate is, among the
   gates and human gates that have not passed, the one whose producer (a node it depends on or sends work back to) is
   running or returned; when none is, the first in the graph's order. A refuted gate whose work is being redone is
   the review loop at work and is drawn in the live colour; the danger colour is for a gate that is blocked, failed,
   or refuted with nobody redoing the work. When the row is narrow the bar goes before the gate, and the word `gate`
   before the gate's name.
   **Paths** (only when the snapshot has drawn paths, `structure.flows`, and nothing works or needs you): one row,
   `3 paths · last impl-loop 2h ago` (the count, and the path run last when its stats say; the time goes first when
   the row is narrow, then the name). So the rows at rest are 3 without paths (hub, spend, keys) and 4 with them:
   one row, only at rest, because busy the rows above matter more; `r` works busy or not.
5. **Spend and context**: dollars, then `ctx` and the root session's context: with `root.wear`
   (`../WEB_API.md`) an 8-cell bar to `compact_at` (or the window), the number and one `•` per summary, as in
   `ctx ━━━━──── 124k ••2`; the fill is muted below 60%, the live colour from 60%, the danger colour from 85%. Without
   wear, `ctx 124k`. Too narrow drops the dots, then the bar, then the context. Token counts are not shown.
6. **Keys** (panel only): one muted row with only the keys that do something on the selected row.

Names are cut in the middle (`impl-superc…alidocious`), goals at their end; no row is wider than the view. The
right-hand part of a row (elapsed time, idle count) goes before a name is cut.

Sizes measured in the real host (OpenCode 2.3.2): the sidebar is drawn at 37 columns (at 130x40 it starts at
column 90); the panel is a 75x39 side panel at 130x40, and full screen below about 120 columns (90x40: the whole
terminal). The panel never draws narrower than 37 columns (`panelWidth`). Tests also cover 20 and 24 columns.

While any host dialog is open (the host's keymap leaves its `base` mode, as it does for every dialog), and until a
moment after the plugin's own confirm or prompt closes, the list keys do nothing, so the Enter that answers a
confirm never also opens a row.
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
view, the list opens as a session panel:

- Command `agentgraph.graph`, title `Agent graph`, in the command palette, or type `/graph` (alias `/agents`). It
  toggles: it closes the panel if this plugin's panel is the open one, otherwise it opens it. Outside a session
  there is nothing to show, so it only shows a short toast.
- It opens on the list, even when the orbit would fit. `g` shows the orbit, and so does full screen by `f`.
- Keys, while the panel has the keyboard (each one only when the host has the call it needs):

| key | does |
| --- | --- |
| `↑` `↓` | move the cursor through the rows that act (needs-you rows, the hub, working agents) |
| `Enter` | open the row's session (`ui.router.navigate`): the agent's running session, the root session for the hub, the session that asked for a request (that is where the host lets you answer it) |
| `s` | stop the selected working agent: asks `ui.dialog.confirm`, then `client.session.interrupt`; the result is a toast |
| `m` | message the selected working agent: `ui.dialog.prompt`, then `client.session.prompt` with `delivery: "steer"` into its running session; the result is a toast |
| `r` | run a drawn path (only when the snapshot has one): the host's chooser (`ui.dialog.select`, widened with `ui.dialog.set({ size: "large" })`; skipped with one path) lists `name  a → b → a` (the id when unnamed), most recently run first, with `3 runs · $1.20 avg · 2.0 rounds` from its stats (a dash for a null field, nothing without stats); then `ui.dialog.prompt` for the goal (required, at most 4,000 characters, refused with a toast); then the run message from `agentgraph flow prompt ID --goal-file -` (below); then `client.session.prompt({ sessionID, text })`, a normal user message, to the orchestrator's session (the snapshot's root session, never a child). A toast says sent, or the back end's or the host's error. `r` is off from the press until it is sent or given up, so nothing is sent twice |
| `f` | full screen on and off; full screen shows the orbit, back to a side panel the list |
| `q` | close the panel |
| `g` | orbit or list (remembered while the app runs) |
| `d` | the orbit's 2D or 3D view |

  `s` and `m` act only on agents that work (never on the hub, which is your own session). A stop or message sent from
  here goes round the orchestrator: it does not know you did it, and a stopped agent's step stays as it was until
  the orchestrator looks.
- Widths: the text follows the panel's width, never narrower than the sidebar's 37 columns and never wider than
  100, so lines do not stretch across a huge terminal. The host keeps narrow terminals in full screen by itself.
- It watches the panel's session with the same runner and `intervalMs` as the sidebar, starts when the panel opens
  and stops when it closes or the plugin is disposed.
- A host without `ui.panel`, `keymap.layer` or `ui.toast` gets the sidebar only, as before.

## The orbit (the panel's scene)

After `g` (or full screen by `f`), when the panel is at least 64 columns by 22 rows, it draws the orbit instead of
the list: a full-panel animated picture of the same snapshot. Smaller than that, and until the panel's height is
known, it draws the list. The sidebar always draws the list.

From the top: the header line (the list's first line, with dollars), up to three "needs you" lines, the canvas (at
least 12 rows), then three instruments: a. the context chart of `root.wear` with the wear line, b. the steps strip,
c. the spend bars. They take 6 rows from 30 rows up (else 4), sit side by side from 100 columns, and when rows run
short they are dropped in the order c, b, a. No titles, hints or legends.

Keys: `g`, `d`, `f` and `q` as in the table above; the list keys do nothing while the orbit shows.

`g` always changes what the panel shows. The side panel of an ordinary terminal is about 56 columns wide, too
narrow for the orbit, so it opens on the list; there `g` goes full screen and shows the orbit, and `g` again returns
to the list and leaves full screen (only if `g` entered it; `f` is yours and `g` never takes you out of it). Only
when the orbit would not fit even full screen (the terminal is smaller than 64 columns by 22 rows) does `g` do
nothing.

**2D** is the ring seen from above, in braille dots (a dot is about square, so the geometry comes out round). The
hub is a ring outline with open space inside; it breathes by one dot while work moves. The agents sit on a larger
faint dotted ellipse in the structure order of the desktop app (lead group at the top, then decide, build, verify,
understand; a folder structure keeps its groups order), with names outside it. A thin spoke (every third dot) runs
from outside the hub's ring to short of each agent that has worked in this session; while an agent works a pulse runs
out along it. Loop arcs bow outward between worker and checker and carry a pulse toward whoever has the work. With
nothing working one faint dot circles the ring, a lap in 20 s.

**3D** draws the same system as a turning solid figure: the hub at the centre, each group on its own tilted orbit
ring (like an armillary sphere), the whole figure turning once in 40 s round its vertical axis. Characters are chosen
by depth, far to near: `·` `.` `:` `0` `1` `@`; far parts are also dimmed toward the muted colour. Agents keep their
glyph and name; near names are at full strength, far ones muted, and the nearer label wins a place. While an agent
works, `0`/`1` digits stream along its spoke from the hub out to it in its group colour; on a loop's arc they stream
toward whoever has the work. A stuck agent blinks in the danger colour.

**The change**: `d` tilts the camera from overhead to the 3D angle (and back) over 0.7 s, drawn in the depth
characters, and ends exactly on the target view's steady frame.

Frames: 10 a second while work moves or the camera moves; when idle 4 a second in 2D and 6 in 3D. A frame equal to
the last one drawn is not pushed to the host, and rows whose text and colours are unchanged keep their nodes. A
160×45 frame computes in about 0.6 ms in 2D and 0.9 ms in 3D (1.3 ms with a new document each frame).

**Colours.** Each group colours its agents' glyphs, names, spokes and pulses. The hub is plain, stuck or failed is
the theme's danger colour, resting detail is muted. An agent at rest is its group colour mixed half way toward the
muted colour (toward a neutral grey as light as it, so the hue stays); a working one is at full strength. The table
is chosen from the theme's background (`background.base`, a flat `background`, or `backgroundPanel`): light when
its luma is 128 or more, dark otherwise or when unknown. A group not in the table takes the next unused colour, then
they cycle. No hue between 80° and 190° (no green or teal). Every colour reaches the host as `#rrggbb`. The list
and the sidebar keep the four tones.

| group | dark background | light background |
| --- | --- | --- |
| council | `#b39ddb` | `#6a4fb3` |
| decide | `#7fb2ff` | `#2f6fd0` |
| build | `#e8a07c` | `#ac563d` |
| verify | `#f28fb5` | `#b8336a` |
| understand | `#e6c15a` | `#8a6d00` |

**How the panel's height is learned.** The panel props carry a width but no height. The plugin wraps the panel in a
root `box` (100% wide and high, `flexGrow: 1`, `minHeight: 0`) and sets an `onSizeChange` prop on it with `setProp`;
in that callback it reads the node's `width` and `height` into a signal. Terminal resizes and `f` fire it again. In
the real host the debug log showed `panel size: 71x39`, then `120x40` after `f` (120×40 terminal), `71x44` then
`160x45` (160×45), and `90x40` (90×40, already full screen).

**Demo.** `AGENTGRAPH_TUI_DEMO=1` in the TUI's environment makes the plugin draw `test/fixtures/demo.json` instead
of running the snapshot process (nothing is spawned), with a growing context history and moving costs, polled every
2 s at most. `AGENTGRAPH_TUI_DEMO=paths` draws the same team at rest (nobody works, no task graph) with three sample
paths, so the paths row and `r` can be seen; in the demo `r` goes as far as the goal and then says
`Demo: impl-loop is not sent.`: no back end runs and nothing is sent.

Real host, 120×40, demo, full screen, 2D (`real-host.py --size 120x40 --demo --fullscreen --view3d`):

```
◉ orchestrator ⠧                                                                                             4/11  $6.79
! impl-claude stuck ↻5
! reviewer stuck
↩ impl-openai sent back ↻3 by reviewer
                                        ⡀⠠ ⠐ ⠈               ⠤         ⠈ ⠂ ⠄⢀
                                   ⡀⠐ ⠁         ⡀ ⠠  ⠄ ⠐  ⠦ ○ council ⠄ ⢀     ⠈ ⠂⠠
                              ⡀⠠ ⠁      ⠠  ⠂ ⠈               ⠤             ⠁ ⠐  ⠄  ⠁⠐ ⡀
                           ⡀⠐    ⢀  ⠂ ⠈                                           ⠁ ⠐  ⡈ ⠄
                 researcher ●⡀ ⠐                                                         ⠂⠁● oracle
                      ⡀⠂   ⠄   ⠂⡀                                                       ⡀⠂ ⠈ ⠠
                    ⠠    ⠂       ⠐⠠                                                  ⠠⠐        ⠐
                  ⢀⠈ ⡀ ⠁            ⠁⠄⢀                                          ⢀ ⠄⠁            ⠈
                 ⠐ ⡀                   ⠐ ⡀                                     ⡀⠐                  ⠈ ⡀
                ⡀⠡                        ⠁⠠                                ⠠ ⠁                       ⢀
       explorer ●   ⡀                       ⠈ ⠂⡀                         ⡀⠂⠈                           ○ oracle-lite
               ⢀     ⠈ ⠂⠐ ⠄⠠⢀                   ⠐⠠                    ⠠⠐
                              ⠁⠈ ⠂⠐ ⠄⢀ ⡀           ⠁⠂⢀   ⢀⣀⠤⠤⢄⣀   ⢀ ⠂⠁                                   ⠁
              ⠈                         ⠈ ⠂⠐ ⠄⠠ ⡀     ⠐⢀⡔⠉    ⠈⠑⣄⠐                                       ⠐
              ⠂                                  ⠁⠈ ⠂⠐ ⡜        ⠘⡄                                        ⠄
              ⠄                                        ⢇    ◉   ⢀⠇                                        ⡀
              ⢀                                    ⡀⠠ ⠂⠘⢆      ⢀⠞ ⠂⠠ ⡀
                                             ⢀ ⠄⠐ ⠁      ⠙⠒⠤⠤⠔⠚⠁⠠     ⠁⠐ ⠄⢀                              ⠈
               ⠁                         ⢀ ⠄⠐           ⠠⠈       ⠈⠠        ⠐ ⠄⢀                          ⠁
                ⠁                   ⡀⠠⠐ ⠁               ⠂          ⠈⠠           ⠁⠐⠠ ⡀                   ⠂
                 ⠈              ⡀⠂⠈                    ⡈             ⠐⢀              ⠈⠐⠂⡀             ⠐
                   ⠁      ⡀⠠ ⠂⠈                       ⠂                ⠐⢀                ⠈⠈⠒⠤ ⡀      ⠂
             reviewer    ⠁                           ⡈                   ⠂                     ⠁ ⠋ designer
                     ⠠⡈⡀⢀                           ⠐                     ⠁⠄                   ⢀ ⡈ 429 error page… $0.16
                     ⢂ ⠂⠂ ⠠                        ⡈                        ⠁⠄               ⠠  ⠂
                     ⠐⢄ ⠁⠅⠠ ⠈ ⠠                   ⠐                           ⠁⡄         ⡀ ⠁ ⠠ ⠁
                       ⢆⡀ ⠁⢈ ⠄⢀  ⠂ ⠠             ⡀⠁                             ⠂   ⢀ ⠐  ⢀ ⠄⠈
                        ⠁⠦⡀  ⠂⠠ ⠂⢀   ⠈  ⠄ ⢀     ⠐                            ⡀ ⠠  ⠙ impl-claude
                           ⠂⠄⢀  ⠁⠠ impl-openai ↩⠂ ⠄ ⠠  ⡀ ⢀  ⡀ ⢀  ○ impl-hard   ⡀⠠⠈⡀⠠Apply the limiter in… ↻5 $1.42
                               ⠁⠐ ⠄⢁⠐⡀⢀ ⠁⠐⡀⢠ ⡐                             ⡀⠤⠐⠐ ⠁
    ▁▃▅▇               ▄▅▆              ▁▃▅▇                                     orchestrator ━━━━━━━━━━━━━━━━━━━━ $2.48
▁▃▅▇████          ▁▂▆▇████          ▁▃▅▇████                                     impl-claude  ━━━━━━━━━━━───────── $1.42
████████      ▂▄▅█████████      ▂▃▅█████████                                     impl-openai  ━━━━━━━━──────────── $0.94
████████  ▁▄▆▇████████████  ▁▄▅▇████████████                                     oracle       ━━━━━━━───────────── $0.92
████████▆█████████████████▆▇████████████████▆                                    researcher   ━━━━━─────────────── $0.61
━─────── 34.7k •••3                             ●●●●↩⠹⠸○✗○○ 4/11                 reviewer     ━━────────────────── $0.21
```

The same run, 1 s after `d` (3D):

```
◉ orchestrator ⠧                                                                                             4/11  $6.83
! impl-claude stuck ↻5
! reviewer stuck
↩ impl-openai sent back ↻3 by reviewer


                                            0000
                                          011 :0::::: : :.:::::::::: : : :
                                    0000001       . : . .          : . .  : : : :
                                 0000  1@@@@@. . .   . .                . .      : : 0
                              0111111111  @ @@@@@@@@@@@@@@@                 . .      0000
                            001        . .@              ·@@@@@@@@             . .      000
                          000       . .   @@                ·    @@@@@@@          .       1↩ impl-openai
                    oracle ●:   :  .       @                 ·         @@@@@@      .@@@@111 000 11000
                   .  . 00   :   : : :     @@                  ·            @@@@@@@@@○ impl-hard     .
                 .      1     : .     : : : @@                  ·         @@@@@@@@@@   .       00      .
                       11    . .           : @ : :               ·  @@@@@@·⠙ impl-claude  ·     0        .
                .      1        . .          @@   : :  ...····.@@@@@@:0                @@@  ·  ·0
                       1     .     researcher ●@ 1 100.0 :@@◉@⠋ designer                 @@@    0·  .   :
               :       1   oracle-lite ○ ·     @@   0@@@@@· ·  1111:·: : :·  ·  ·         . @1  1     :
               :       1•                 · ·  @@@@@@ · 1@@@@@@1     ·    : : : : :·: :  ·  ·11 1  1001
                        1   :             @@@@@@·@@          :        ·                : ::0  1  reviewer
                council ○1           @@@@@@ ·    ·@@ ·        :        ·                 :    11
                         1• :   @@@@@@.            @@@· · · ·                                110
                  :       11111@@.                   @@      ·:· · · · ·                :: :11
                           111:                       @@       :      · . . . . . . . ::: 11
                      :       11                        @@      0        .            : 111
                                1110                     @@@                       : 111
                          :       1111                     @@    0       :      : 1111
                              :      11111                   @@@        :    011111
                                   :     111111                @111    01111111
                                       :      01110000000000001:11● explorer
                                            :   :  :  :  :  :

    ▁▃▅▇               ▄▅▆              ▁▃▅▇                                     orchestrator ━━━━━━━━━━━━━━━━━━━━ $2.50
▁▃▅▇████          ▁▂▆▇████          ▁▃▅▇████                                     impl-claude  ━━━━━━━━━━━━──────── $1.44
████████      ▂▄▅█████████      ▂▃▅█████████                                     impl-openai  ━━━━━━━━──────────── $0.94
████████  ▁▄▆▇████████████  ▁▄▅▇████████████                                     oracle       ━━━━━━━───────────── $0.92
████████▆█████████████████▆▇████████████████▆                                    researcher   ━━━━━─────────────── $0.61
━─────── 34.7k •••3                             ●●●●↩⠹⠸○✗○○ 4/11                 reviewer     ━━────────────────── $0.21
```

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

`r` gets the run message the same way: `<python> -P -m agentgraph.structure flow prompt <ID> --goal-file -` (what
`agentgraph flow prompt` runs), no shell, `PYTHONPATH=<home>`, in `home`, the goal on standard input, SIGKILL after
20 s, output capped at 256 KiB. Exit 0 with text: the message. Otherwise the last line of its standard error is the
toast (`there is no flow 'x'`); an agentgraph without the command (`No module named agentgraph.structure`,
`invalid choice: 'prompt'`) says ``This agentgraph has no `flow prompt` command yet; update the orchestration
folder.``

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
- `src/scene.js`: the orbit, pure: `scene(snapshot, { width, height, frame, now, previous, view, change, colors })`
  → exactly `height` lines of exactly `width` cells, spans `{ text, tone, color? }` (`color` a `#rrggbb` group
  colour). `view` is `"2d"` or `"3d"`, `change` `{ from, at }` the camera's move, `colors` `{ dark, muted }`.
- `src/demo.js`: the demo runner for `AGENTGRAPH_TUI_DEMO`.
- `src/runner.js`: the snapshot process runner, and `runFlowPrompt` (the run message of a path).
- `test/run.mjs`: `node tui-plugin/test/run.mjs` (plain node, exits non-zero on failure). `test/fixtures/demo.json`
  is a saved `agentgraph.snapshot_json --demo` document. Its stub `setProp` refuses an `fg` that is neither a string
  nor an RGBA, as the real host does.
- `test/real-host.py`: drives the real terminal app in a pseudo-terminal (`--size 160x45`, `--size 90x40`,
  `--no-graph`, `--binary`, `--demo`, `--fullscreen`, `--expect-scene`, `--view3d`), types `/graph`, captures the screen with `pyte` and exits 0 only when the view is on
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
- The orbit, with `--demo` at 120×40 and 160×45 (in the panel and full screen): the 2D view with the open hub ring,
  10 agent names and all five group colours; `d` switches to the 3D view (`@`, `0`, `1` and the other depth
  characters on the canvas, no braille, 10 names, all five group colours) and two 3D captures 1 s apart differ. At
  90×40 on the throwaway session (no demo) the orbit draws the session's own agents. `q` closes the panel after
  each run; the process count was the same before and after.

The cause of the empty panel (and the empty sidebar) in these hosts: the theme has no `text.default`, so the plugin
passed the whole `text` token object as `fg`; OpenTUI threw (`undefined is not an object (evaluating 'e.length')`),
the error line threw the same way, and the view caught it and returned nothing. `Loading` showed for a moment
because the muted colour happened to be a real colour.

## Running the real-host check

```
python3 tui-plugin/test/real-host.py --size 160x45
python3 tui-plugin/test/real-host.py --size 90x40
python3 tui-plugin/test/real-host.py --size 160x45 --no-graph
python3 tui-plugin/test/real-host.py --size 120x40 --demo --fullscreen --view3d
python3 tui-plugin/test/real-host.py --size 160x45 --demo --view3d   # fails: the side panel is 56 columns wide, so the list shows; add --fullscreen
python3 tui-plugin/test/real-host.py --size 160x45 --demo --gcheck [--pre f|g|gd|d|R|gR]
```

- `--demo`: runs the app with `AGENTGRAPH_TUI_DEMO=1` and requires the 2D orbit: the hub ring (the hub's glyph inside
  the canvas, braille dots round it, open cells beside it), at least 8 agent names on at least 6 rows on both sides
  of the hub, at least 3 group colours (matched by hue, so resting mixes count) and none green or teal, the context
  chart, and two captures 1 s apart that differ.
- `--fullscreen`: presses `f` once the view shows.
- `--expect-scene`: requires the hub ring without the demo (a live session's own agents).
- `--view3d`: after the 2D captures presses `d`, waits 1 s and captures the 3D view, then again 1 s later. With
  `--demo` it requires at least 8 names, `@`, `0` and `1` inside the canvas rows and no braille there, at least 3
  group colours and none green or teal, and the two 3D captures to differ.
- `--gcheck`: presses `g`, captures, presses `g` again, captures; fails unless the first press changed between orbit
  and list and the second changed it back. It prints the panel size the plugin measured (from its debug log; sizes
  come in order, so `56x44, 160x45` is a side panel and then full screen). `--pre KEYS` presses keys first: `f`, `d`,
  `g`, and `R` (close with `q`, reopen with `/graph`). Use it with `--demo`, not with `--fullscreen` or `--view3d`.

The script always starts the app with `--standalone`, and so must anyone who drives the app by hand for a test. A
launch without it attaches to the user's shared background service, and when the versions differ it restarts that
service (`opencode-cli` 2.3.2 replaced a running 2.2.5 service once): never do that. On every exit path (success,
an exception, ctrl+c, SIGTERM or SIGHUP to the script) it quits the app and then stops every process the app
started, including the private server, which runs in its own process group (in 2.3.2 it exited by itself when the
app was killed; the script does not rely on that). It signals
only processes it saw descend from its own launch (matched by pid and start time); a SIGKILL to the script itself
cannot be caught.


List-mode checks (see the script's docstring): `--demo` alone requires the busy list (hub row with the idle count,
at least 2 rows at work besides the hub, one a spinner row, the progress row, the key line last); `--demo --dialog` (refused without `--demo` or with `--session`) presses `s`, then down and Enter in
the host's confirm, and requires the cursor not to move and no session to open; `--open` (a real session) presses
Enter on the hub row and requires the plugin to ask the host to open that session with no error. `--selftest`
checks the list check itself on built-in screens (run by `test/run.mjs`). `--paths` (alone) runs the
`AGENTGRAPH_TUI_DEMO=paths` demo, requires the 4 rows at rest with the paths row and `r run`, presses `r` and requires
the host's chooser with the sample paths and their stats, Enter and requires the goal prompt, then Escape: it stops
before any goal, so nothing is sent and no back end runs.
## Not verified

- `{ prepend: "sidebar.content" }`; that the returned cleanup of `ui.slot` and `keymap.layer` is called.
- That `onCleanup` from `solid-js` runs when the host disposes the panel (it is what stops the panel's timers).
- That the panel's key commands honour `enabled` (`f`, `d`, `g` and `q` were pressed in the panel and worked; `g` by `--gcheck`).
- The light palette on screen (the real host's theme was dark). The demo's stuck agents were drawn, but their
  danger colour on screen was not read.
- The `[path, options]` form in `cli.json` (`setup` gets an `options` object; only the default options were run).
- The toast variant `"info"` (only outside a session).
- Bold through the `attributes: 1` text property (OpenTUI `TextAttributes.BOLD`), used only for a green-only theme.
- The live colour and the danger colour on screen (the throwaway session never works and never errs).
