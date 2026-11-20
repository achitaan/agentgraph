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
column 90); the panel is a 75x39 side panel at 130x40, and full screen below about 107 columns (measured: 105x40 is full screen and `f` changes nothing, 108x40 is a side panel; 90x40: the whole
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
- It opens straight on the orbit (the picture) whenever the panel fits it (at least 36 columns by 14 rows: the orbit
  compresses to the panel's size), and on the
  list when it does not. `g` switches between them and the choice is remembered while the app runs (after `g` to the
  list, `/graph` opens the list again); `g` never goes full screen; `f` does,
  and only when you press it.
- Keys, while the panel has the keyboard (each one only when the host has the call it needs):

| key | does |
| --- | --- |
| `↑` `↓` | move the cursor through the rows that act (needs-you rows, the hub, working agents) |
| `Enter` | open the row's session (`ui.router.navigate`): the agent's running session, the root session for the hub, the session that asked for a request (that is where the host lets you answer it) |
| `s` | stop the selected working agent: asks `ui.dialog.confirm`, then `client.session.interrupt`; the result is a toast |
| `m` | message the selected working agent: `ui.dialog.prompt`, then `client.session.prompt` with `delivery: "steer"` into its running session; the result is a toast |
| `r` | run a drawn path (only when the snapshot has one): the host's chooser (`ui.dialog.select`, widened with `ui.dialog.set({ size: "large" })`; skipped with one path) lists `name  a → b → a` (the id when unnamed), most recently run first, with `3 runs · $1.20 avg · 2.0 rounds` from its stats (a dash for a null field, nothing without stats); then `ui.dialog.prompt` for the goal (required, at most 4,000 characters, refused with a toast); then the run message from `agentgraph flow prompt ID --goal-file -` (below); then `client.session.prompt({ sessionID, text })`, a normal user message, to the orchestrator's session (the snapshot's root session, never a child). A toast says sent, or the back end's or the host's error. `r` is off from the press until it is sent or given up, so nothing is sent twice, and `r run` leaves the key line for that time. The `r` layer is registered while the snapshot has a path and unregistered when the paths go. The selected row does not matter: a busy child selected still sends to the root. When the snapshot gives the path a `rev`, it goes along as `--expect REV`: path ids are reused (a removed and redrawn path can get the same id), and a path that changed since the chooser showed it is refused by the back end ("changed since you opened it"); then nothing is sent, both views refresh at once, a warning toast says `<name> changed since you opened it: nothing was sent. Press r again.`, and the next goal prompt for that id starts with the goal typed (not another path's prompt: the goal waits for its own path). Without `rev` (an older back end) no `--expect` is passed |
| `a` | ask the side chat (only while the side chat's row is selected; listed in the key line only then): `ui.dialog.prompt` ("Ask the side chat"), then `agentgraph sidechat ask` (below). An empty question is refused with a toast and nothing is run; so is one over 4,000 characters (the toast says how long it was). One question is in flight at a time: while it waits the row shows `answering` with a spinner, `a` is off and leaves the key line. The result is a toast (`Side chat answered`, or `Side chat: ` and one short error line) and the chat is read at once |
| `c` | the chat of the selected row, in place of the lower part of the list when it does not fit beside it (opens it where it shows already beside the list: nothing; `c` again or `Esc` closes it) |
| `Esc` | close the chat opened with `c` |
| `PgUp` `PgDn` | scroll the chat back and forward; while the chat is open in place, `↑` `↓` scroll it too (they move the cursor otherwise) |
| `f` | full screen on and off (yours: `g` never does it); full screen shows the orbit, and a side panel keeps it |
| `q` | close the panel |
| `g` | orbit or list (remembered while the app runs) |
| `d` | the orbit's 2D or 3D view |
| `←` `→` `↑` `↓`, `0` | on the 3D orbit: turn the view, change the elevation, reset (below) |

  `s` and `m` act only on agents that work (never on the hub, which is your own session). A stop or message sent from
  here goes round the orchestrator: it does not know you did it, and a stopped agent's step stays as it was until
  the orchestrator looks.
- The chat. Choose a row and the panel shows what that agent is doing, under the rows when there are 6 or more
  lines of room (and `c` opens it in place of the lower part of the list when there is not):
  `chat · NAME` for an agent that works (steer with `m`, stop with `s`), `transcript · NAME` for one that does not
  and for the orchestrator (read-only: `m` and `s` do nothing there and leave the key line). Oldest first, newest at
  the bottom, wrapped to the panel's width; it follows the newest until you scroll back (the heading says how far:
  `↓N`). `told …` is what the agent was told (`told (pending) …` for a message you just sent, until it lands),
  `· …` dimmer is its thinking, plain text is what it said, `▸ name state` is a tool (name and state only, never its
  input or output), `· note`, `(cut)` and `… older chat left out` are quiet marks. Every string is cleaned again
  before it is drawn: no control or escape sequences, tabs as spaces, wide characters measured by display width. A
  working agent also gets one line under its row: the tool it runs, or the first words it thinks or says.
- The side chat (the snapshot's `sidechat`, WEB_API.md). Only when `sidechat.installed` is true:
  - One more row after the orchestrator's: `◌ side chat  MODEL`, selectable like the others. Its chat is the side session
    (`sidechat.session`, read through the same chat reader as every other row), headed `transcript · side chat`; with
    no session yet it shows one quiet line (`No session yet`). `s` and `m` do nothing on it (there is nothing of yours to
    stop or steer) and `Enter` opens its session. The row spins while `sidechat.busy` or while a question of yours is
    out. It is not an agent: it is not counted as working or idle, has no needs and no arrows.
  - On the orbit, in 2D and 3D, the side chat is its own moon of the hub (the same moon drawing, on an orbit plane of
    its own in 3D), named `side chat` where names fit. It goes round only while `sidechat.busy` (and, with reduced
    motion, stays still); the ring of the team stays round and nothing else moves because of it.
  - `a` (above) runs `<python> -P -m agentgraph.sidechat ask --main <the snapshot's root session> --text-file - --json`
    (`--demo` added with `AGENTGRAPH_TUI_DEMO`) through the same runner rules (no shell, `PYTHONPATH=<home>`, the
    project folder as the working directory, capped output). **The question goes on the command's standard input, never
    in its arguments.** 150 s timeout (SIGKILL), one in flight. The toast only says it answered: the
    answer itself is in the side session and shows in the row's chat after the refresh.
  - Not installed (or `installed` anything but `true`): no row, no moon, no key, no word.
- The spend line. The snapshot's `spend` (`{ limit, warn, rate, chat, workers }`, dollars; `rate` in dollars an hour):
  every part may be absent or null and is then not shown (a snapshot without `spend` draws exactly what it drew before).
  With a `limit` the amount reads `$7.65 / $50`; from `warn` the amount takes the attention tone (the existing one, no
  new colour); at or over the limit it says so in the same place (`$50.00 / $50 at limit`, `$51.20 / $50 over limit`).
  Where there is width the rate (`$22/h`) and the split (`chat $3.10 · workers $4.55`) follow; they go in this order as
  the line narrows: the rate first, then the split, the limit last.
- The preset name. `setup.preset` shows as one quiet word at the right end of the spend line where there is width
  (`normal`); with `setup.override` set it reads `normal (overridden)` (`normal*` where that does not fit). It is
  display only: the panel does not switch presets (the host has its own command for that).
- Reads for the chat: `<python> -P -m agentgraph.chatfeed --session <its newest session> --json --limit 40`
  (what `agentgraph chat --session ID --json` runs; `--demo` added with `AGENTGRAPH_TUI_DEMO`), through the same
  runner rules as the snapshot (no shell, `PYTHONPATH=<home>`, 20 s SIGKILL, capped output, every failure becomes one
  line). Only for the selected row, one read in flight, about every 2 s while that session is busy and once when it
  is not (and at once after a successful `m`); none while the panel is hidden, none when no row is selected or the
  agent has no session yet.
- Widths: the text follows the panel's width, never narrower than the sidebar's 37 columns and never wider than
  100, so lines do not stretch across a huge terminal. The host keeps narrow terminals (under about 107 columns) in full screen by itself.
- It watches the panel's session with the same runner and `intervalMs` as the sidebar, starts when the panel opens
  and stops when it closes or the plugin is disposed.
- A host without `ui.panel`, `keymap.layer` or `ui.toast` gets the sidebar only, as before.

## The orbit (the panel's scene)

The picture lives in the side panel beside the chat. When the panel is at least 36 columns by 14 rows (the smallest
that still reads; the sidebar's 37 columns would fit) it opens on the orbit, drawn instead of the list, whatever its
size (`g` switches to the list and back; full screen is only ever the user's `f`, or the host's own choice on a narrow
terminal). Smaller than 36x14, and until the panel's height is known, it draws the list. The sidebar always draws the
list.

**Small panels.** The orbit compresses instead of giving up. As space shrinks it gives way in this order: the three
instruments (c, b, a), then the needs lines, then the header (its right side `4/11  $7.65` goes, then the hub's name
is cut), then the ring itself gets smaller (always a circle: one radius in dots across and up, never stretched to the
panel's shape; the largest circle that lets every name show whole, a name moves inside the ring beside its glyph when
there is no room outside it, and a name over 12 characters is still cut with an ellipsis; only when even a small ring
has no room for a name, as with twenty agents at 36 columns, is a name cut further or left out; never over another
name, a glyph or the hub). The hub, every agent's glyph, the working pulses, the moons and the sun's flares
stay. At 36x14 the canvas is 12 rows: a 2D ring of about 10 to 18 dots' radius (the demo's names fit at 10), and the 3D figure's moons show (a moon hidden
behind its parent for a moment shows nothing). So the sun's flares can leave the ball at that size, the 3D ball is never
smaller than 7 dots in radius on screen (across and up; about 3 cells, a little bigger than 0.2 of the figure there);
larger panels keep 0.2. The `d` morph works at that size too, and a frame costs no more than the
limit below.

From the top: the header line (the list's first line, with dollars), up to three "needs you" lines, the canvas (at
least 12 rows), then three instruments: a. the context chart of `root.wear` with the wear line, b. the steps strip,
c. the spend bars. They take 6 rows from 30 rows up (else 4), sit side by side from 100 columns, and when rows run
short they are dropped in the order c, b, a. No titles, hints or legends.

Keys: `g`, `d`, `f` and `q` as in the table above; the list keys do nothing while the orbit shows. On the 3D view
`←`/`→` turn it by 15°, `↑`/`↓` raise and lower the camera by 7.5° (from flat to overhead), and `0` resets; the
self-turn waits 3 s after the last turn and goes on from there. The bottom canvas row says `←→↑↓ turn  0 reset`.

Mouse: a click on the orbit switches 2D and 3D, as `d` does; a drag on the 3D view turns it (6° a column, 12° of
elevation a row) and switches nothing. The list ignores the mouse, and its text stays selectable; orbit text is
not selectable. Each orbit row is one node that stays while the orbit shows (a redraw swaps its spans), so a click
during the morph or an animation always lands: the host drops a press whose target was replaced since its last
paint.

`g` switches what the panel shows between the orbit and the list at the size the panel has, and never changes the
panel's presentation: it does not call the full-screen toggle, not in a narrow panel and not when the orbit would fit
only full screen (it used to). In a panel smaller than 36x14 there is only the list and `g` does nothing.

**What the host decides.** On a terminal narrower than about 107 columns the host forces the panel to full screen,
whatever the plugin does: measured in the real host, 100x40 and 105x40 open full screen and `f` (`toggleFullscreen`)
changes nothing (`presentation` stays `"fullscreen"`), while 108x40 and wider open a 62-column side panel. There the
plugin can only draw the orbit at the full-screen size it is given (90x40: the whole terminal); it cannot make the
panel narrower or force a side panel, and drawing a small picture in a corner of a full-screen panel is possible but
not built. The host's own sidebar (shown from about 120 columns, 37 columns wide, drawn by the plugin's
`sidebar.content` slot, which always draws the list) could show the small picture instead: 37 columns is above the 36
minimum, the slot's height would be measured like the panel's (`onSizeChange`), and the same orbit code would draw it.
That is not built.

**2D** is the ring seen from above, in braille dots (a dot is about square, so the geometry comes out round). The
hub is a ball: a rim of dots with shaded dots inside, dithered denser toward a light at the upper left (the other
way round on a light theme, so the lit side reads lighter either way), still at rest, its rim breathing by one dot
while work moves (not with reduced motion). While the hub itself works it is the sun (below). The agents sit on a larger
faint dotted circle (equal radius across and up, in dots; the radius is the canvas's height or width allows, less the
names that go outside it; the 3D camera uses the same one scale across and up, so the reference circle seen from
above is round too, and tilted it is the circle's width times the sine of the elevation) in the structure order of the desktop app (lead group at the top, then decide, build, verify,
understand; a folder structure keeps its groups order), with names outside it. A thin spoke (every third dot) runs
from outside the hub's ring to short of each agent that has worked in this session; while an agent works a pulse runs
out along it. Loop arcs bow outward between worker and checker and carry a pulse toward whoever has the work. With
nothing working one faint dot circles the ring, a lap in 20 s.

**3D** draws the same system as a turning solid figure: the hub at the centre, each group on its own tilted orbit
ring (like an armillary sphere), the whole figure turning once in 40 s round its vertical axis. Characters are chosen
by depth, far to near: `·` `.` `:` `0` `1` `@`; far parts are also dimmed toward the muted colour. Agents keep their
glyph and name; near names are at full strength, far ones muted, and the nearer label wins a place. The hub is a
ball in block shades `░` `▒` `▓` `█` (none of the rings' characters), lighter toward the upper left and denser toward
the lower right, drawn over the far parts of the rings and spokes and under the parts nearer than the hub. The agents under a parent (the council's seats, or
any group built the same way: `agents[].parent`) are its **moons**: a `•` and a short name each (`fable`, not
`councillor-fable`), each on **its own orbit** round the parent. The parent's moons are ordered by name, `i = 0 … n-1`.
Orbit `i` lies in the plane of a single small ring (leaning 63° out of the group's ring, turned the way that keeps the
planes furthest apart) turned about the vertical axis through the parent by 360° × i / n, so the planes are at least
20° apart; its radius is `r × (1 + 0.14 i)` with `r = min(0.4, 0.11 + 0.04 n)` of the figure's radius; the moon starts
360° × i / n round it and all go the same way. Each orbit is drawn as its own faint dotted ring where the canvas is at
least 48×18 (never over a name; the sky keeps clear of where a moon or its name can be). The moons go round once in
24 s, from the clock (`now`), not the frame count, whatever the camera does; with `motion: "reduced"` each stands at its
time-0 place. A moon's name takes a place like any other, nearest first, and never goes over another name or its
parent's; a moon hidden behind its parent shows no glyph and no name for that moment. In 2D the seats stay dots in a
small circle round the parent and nothing about them moves. While an agent
works, `0`/`1` digits stream along its spoke from the hub out to it in its group colour; on a loop's arc they stream
toward whoever has the work. A stuck agent blinks in the danger colour.

**The sun** (2D and 3D): while the hub itself works (not just any agent) it flares. 7 to 12 flame spikes (by the hub's
size: 7 at a radius of 5 dots, 12 at 9) shoot out of its rim and draw back, each with its own seeded life of 0.6 to 1.6 s
and its own start (so they are out of step; one life in five is skipped, and each life takes a new direction and
length), reaching at most 0.6 of the ball's radius beyond its rim; a faint glow ring swells and shrinks round the ball
every 1.4 s, and in 2D the ball's shaded dots turn warm. They start ramped in over 0.3 s after the hub begins to work
and die down over 1 s after it stops: the scene knows when from `previous` (`{ doc, at }`, the document drawn before and
when this one arrived), and `frameInterval` keeps the working rate for that second. In 2D they are braille dots. In 3D
a flare is a run of `|` `-` `/` `\` along its direction on the screen that ends in an arrow `^` `v` `<` `>` pointing
outward, and the glow is a ring of backticks `` ` `` (none is a depth character or a sky star: `·.:01@`, `'+*`); they
are drawn over the rings, spokes and arcs that pass behind or beside the hub, and under only the agents' glyphs, the
names and what is nearer than the hub; a cell shows only outside the ball's rim and within 0.6 of its radius of it. The
ball and its flares are drawn after the rings; at rest it is still, with no flares. Colours: the build group's
orange toward a pale yellow core, none green and none a group's colour (dark: core `#ffe9a8`, flame `#f2a65a`, glow
`#a9673f`; light: core `#b87200`, flame `#c0501a`, glow `#cf8a60`, all at least 2:1 against white). With
`motion: "reduced"` the spikes are short (0.3 of the radius), still and drawn while the hub works, the glow does not
swell and the rim does not breathe. A hub that is not working shows none of this.

**The change**: `d` (or a click) morphs between the views over 0.7 s: each agent travels on screen from its 2D place
to its 3D place (and back) while the rings tilt, drawn in the depth characters, ending exactly on the target view's
steady frame.

**The sky** (3D only): sparse dim stars (`'` and `+`, about 1.4 % of the cells, never on or next to a figure cell
or a name) from a fixed seed, so the sky is the same every time. It shifts at about a third of the figure's turn and
with the elevation, and fades in and out with the morph. Each passed run of a drawn path (`stats.passed`) adds a
warmer `*` star, at most 200 (past that, no new star and no streak). A run that finishes while the 3D view shows
flies a short diagonal streak (0.9 s) to its star; none on opening or switching to 3D, several spaced 0.7 s apart.
While the orchestrator works the hub and its ring are drawn in the accent tone. Option `reducedMotion: true`: no
streak (the star just appears) and no fade.

Frames: 10 a second while work moves or the camera moves; when idle 4 a second in 2D and 6 in 3D. A frame equal to
the last one drawn is not pushed to the host, and rows whose text and colours are unchanged keep their nodes. A
160×45 frame computes in about 0.6 ms in 2D and 0.9 ms in 3D (1.3 ms with a new document each frame). The test
reports these times but does not fail on them: it counts a frame in reference frames (a fixed piece of per-cell
work timed in the same process, best of interleaved batches), today 5.0 in 2D and 7.7 in 3D (7.1 and 11.0 with a new
document each frame), and fails at four times today's count: a frame five times slower fails, and machine load does
not get there (at load average 110 to 240 the counts stayed under 2.6 times, mostly under 1.3).

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
2 s at most. `AGENTGRAPH_TUI_DEMO=side` draws the moving demo with a side chat (`ses_demoside`, its row and moon), a spend limit of $50 with the rate and the chat/workers split, and the preset name `normal`; `a` there runs `agentgraph sidechat ask --demo`. `AGENTGRAPH_TUI_DEMO=paths` draws the same team at rest (nobody works, no task graph) with three sample
paths, so the paths row and `r` can be seen; in the demo `r` goes as far as the goal and then says
`Demo: impl-loop is not sent.`: no back end runs and nothing is sent.

**For checks.** The throwaway host loads the plugin from the user's config, so a check cannot give it options;
two environment switches stand in. `AGENTGRAPH_TUI_HOME=<folder>` points the snapshot and `flow prompt` at a scratch
orchestration folder with its own `structure/` (over the `home` option), honoured only for an existing folder inside
the system temp folder (`os.tmpdir()`, both resolved through links); anything else is ignored, so the environment
cannot make the panel run code from another folder. `AGENTGRAPH_TUI_NOSEND=1` makes `r` stop after the run message: a toast
`Not sent (AGENTGRAPH_TUI_NOSEND): <name>, a message of N characters for <session>` and nothing sent. With
`AGENTGRAPH_TUI_DEBUG` set, both are written to the debug log when the plugin starts.

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
| `reducedMotion` | `false`                                                            | The 3D sky without shooting stars or fades. |

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
20 s, output capped at 256 KiB; `--expect <rev>` is added when the chosen path has a `rev` that is a plain token (never one
starting with `-`). Exit 0 with text: the message. Otherwise the last line of its standard error is the
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
  colour). `view` is `"2d"` or `"3d"`, `change` `{ from, at }` the camera's move, `colors` `{ dark, muted }`; also
  `orbit` `{ turn, elevation, resume }`, `streaks` `[{ flow, run, at }]`, `motion: "reduced"` and `sky: false`.
- `src/chat.js`: pure chat rendering: cleaning, wrapping, kinds, window and heading.
- `src/demo.js`: the demo runner for `AGENTGRAPH_TUI_DEMO`.
- `src/runner.js`: the snapshot process runner, the chat reader (`createChatRunner`), and `runFlowPrompt` (the run message of a path).
- `test/run.mjs`: `node tui-plugin/test/run.mjs` (plain node, exits non-zero on failure). `test/fixtures/demo.json`
  is a saved `agentgraph.snapshot_json --demo` document. Its stub `setProp` refuses an `fg` that is neither a string
  nor an RGBA, as the real host does.
- `test/real-host.py`: drives the real terminal app in a pseudo-terminal (`--size 160x45`, `--size 90x40`,
  `--no-graph`, `--binary`, `--demo`, `--fullscreen`, `--expect-scene`, `--view3d`, `--chat` and `--side` with `--demo`), types `/graph`, captures the screen with `pyte` and exits 0 only when the view is on
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
- The orbit, with `--demo` at 120×40 and 160×45 (in the panel and full screen): the 2D view with the hub ball,
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
python3 tui-plugin/test/real-host.py --size 160x45 --demo --view3d   # the orbit in the side panel (it opens on the orbit); --fullscreen presses f first
python3 tui-plugin/test/real-host.py --size 160x45 --demo --gcheck [--pre f|g|gd|d|R|gR]
```

- `--demo`: runs the app with `AGENTGRAPH_TUI_DEMO=1` and requires the 2D orbit: the hub ball (the hub's glyph inside
  the canvas, braille dots round it), at least 8 agent names on at least 6 rows on both sides
  of the hub, at least 3 group colours (matched by hue, so resting mixes count) and none green or teal, the context
  chart, and two captures 1 s apart that differ. (`--demo` alone at 130x40, and `--dialog`, `--open`, `--paths` and
  `--live-paths`, test the list: the panel opens on the orbit, so the run presses `g` first and requires the list.)
- `--fullscreen`: presses `f` once the view shows.
- `--expect-scene`: requires the hub ring without the demo (a live session's own agents).
- `--view3d`: after the 2D captures presses `d`, waits 1 s and captures the 3D view, then again 1 s later. With
  `--demo` it requires at least 8 names, `@`, `0` and `1` inside the canvas rows and no braille there, at least 3
  group colours and none green or teal, and the two 3D captures to differ.
- `--orbit` (with `--demo --fullscreen --view3d`): six `←`, three `↑` and `0` on the 3D view, then a click (2D), a
  second click (3D) and a drag of 8 columns; fails unless each is logged by the plugin and changes the screen.
- `--plugin DIR`: loads only the terminal plugin in `DIR` (a scratch config folder), for testing a checkout.
- `--gcheck`: presses `g`, captures, presses `g` again, captures; fails unless the first press changed between orbit
  and list and the second changed it back. It prints the panel size the plugin measured (from its debug log; sizes
  come in order, so `62x39, 130x40` is a side panel and then full screen by `f`); it also fails when `g` changes the panel's size (`g` never goes full screen; not checked when `--pre` has `f`). `--pre KEYS` presses keys first: `f`, `d`,
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
before any goal, so nothing is sent and no back end runs. `--live-paths` (alone, never with `--session`) uses a live
snapshot instead of the demo: it puts a clean copy of the committed toolkit (`git archive HEAD`, not the working
folder) in `SCRATCH/live-orch`, adds two named paths there with `agentgraph flow add ... --structure <scratch>
--commands <scratch>` (the real structure and commands folders are not touched), and starts the app with
`AGENTGRAPH_TUI_HOME=<scratch>` and `AGENTGRAPH_TUI_NOSEND=1`. It requires both in the debug log before any key, the
`2 paths` row, the panel's revs equal to the back end's, the chooser listing both; a goal and Enter must run
`flow prompt <id> --goal-file - --expect <rev>` and log the message's length as the script's own `flow prompt` gives
it. Then the stale step: `r`, and while the chooser is open the script changes that path's text with `flow text`;
Enter, a goal, Enter must give the refusal, no message, the toast, a refresh to the new rev; `r` again must offer the
goal typed, and Enter must run it with `--expect <new rev>`. Nothing is sent. With `AGENTGRAPH_TUI_DEBUG` the plugin
logs each `flow prompt` command line (`action: flow prompt: ...`, the goal goes on standard input), a refusal
(`action: stale <id> <rev>`) and the paths' revs when they change (`paths: <id>@<rev> ...`, `-` for none).

Every wait in the script is on an event (a line on screen, a line in the debug log) with a 60 s ceiling (`--wait`,
`--settle`), not a fixed sleep: under load (load average 70 to 230) a dialog or a view switch took longer than the
fixed 1.5 s sleeps of the first version. `/graph` is submitted only after its row shows in the command list; without
it the input is cleared, so `/graph` never goes to the session as a message.
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
