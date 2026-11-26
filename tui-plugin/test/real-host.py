#!/usr/bin/env python3
"""
Drive the real OpenCode terminal app in a pseudo-terminal and check that the agent graph shows.

    python3 tui-plugin/test/real-host.py --size 160x45 [--no-graph] [--session ses_...] [--binary PATH]

It starts the app with `--standalone` (a private server: a TUI of another version attached to the shared background
service would restart that service, as `opencode-cli` 2.3.2 did to a 2.2.5 service once), opens the session, types
`/graph` and Enter (unless --no-graph), waits for the view and for toasts to go, captures the screen as text with
`pyte`, then presses `q` (the panel's close key) and quits with ctrl+c (SIGTERM, then SIGKILL, if it is still
running). It prints the screen, the view's lines, the colours of its first two lines, and where the view is after `q`.

Exit 0 only when the screen shows the hub line (`◉ orchestrator`) and at least one more non-empty line of the view
under it in the same column. With --no-graph the view must be in the sidebar.

`pyte` is not installed globally: put it in a folder and pass it with PYTEPATH (default: the scratch folder
<scratch>/pylib, filled with `pip install --target <scratch>/pylib pyte`). <scratch> is
$TMPDIR_OPENCODE/tui-real (default: <the system temp folder>/tui-real).

The session: --session, else $AGENTGRAPH_REAL_SESSION, else <scratch>/session.txt; when there is none it creates a
throwaway one with `opencode run --standalone --model anthropic/claude-haiku-5-5 "Do not call any tool. Reply with
exactly: OK"` in <scratch>/proj (about $0.004) and saves its id in session.txt.

AGENTGRAPH_TUI_DEBUG=<file> is passed to the app: the plugin appends every error it swallows to that file.

The list (what the panel opens on):
    --demo          start the app with AGENTGRAPH_TUI_DEMO=1 (the plugin draws the saved demo document, moving,
                    without Python). Without --fullscreen or --expect-scene, require the busy list (see list_report):
                    at most 16 rows, the hub row with its idle count, at least 2 rows of agents at work besides the
                    hub (a spinner row, or a needs-you row with an elapsed time), at least one of them a spinner row, the progress row, the
                    key line last, and two captures 1 s apart that differ (the spinners). Without --demo,
                    AGENTGRAPH_TUI_DEMO is removed from the app's environment.
    --dialog        only with --demo and without --session (refused otherwise, before anything starts): press `s` on the selected row (the stalled agent), require the host's
                    confirm dialog, press the down arrow and Enter in it, and require that the cursor did not move and
                    that the panel did not open a session (the plugin's debug log has no `action: open`). Enter answers
                    the confirm: if it confirms, the plugin asks the host to interrupt the demo's made-up session id,
                    which no real session has.
    --open          without --demo (a real session, at rest the hub row is the only row): press Enter and require that
                    the plugin asked the host to open the hub's session (the debug log: `action: open <session>`, the
                    session this script opened) with no error, and that the session is still on screen.
    --chat          only with --demo: the chat of the selected agent (the demo's `agentgraph chat --demo` feed). Require its
                    heading (`chat · impl-claude ───`) and some of its lines under the list; when the panel has no
                    room for it beside the list, press `c` to open it in place of the lower part and `Escape` to close
                    it. Beside the list, press the down arrow: the orchestrator's read-only `transcript · orchestrator`,
                    and once more: `chat · designer`; the up arrow twice returns. Not with --dialog, --open, --paths,
                    --live-paths, --gcheck, --fullscreen, --view3d or --orbit.
    --paths         start the app with AGENTGRAPH_TUI_DEMO=paths (the demo team at rest with three sample paths, see
                    src/demo.js) and require the list at rest: at most 4 rows (the hub, the paths row
                    `3 paths · last impl-loop 2h ago`, spend, the key line with `r run`). Then press `r` and require the
                    host's chooser (`Run a path`) with the three sample paths, and `3 runs · $1.20 avg · 2.0 rounds`
                    on the run one; press Enter (the first, impl-loop) and require the goal prompt
                    (`Goal for impl-loop`); press Escape and require it closed. Stops before any goal is typed: nothing
                    is sent, no back end runs (the demo never sends anyway), and the plugin's debug log must show
                    `action: choose` and no `action: send`, `run` or `open`. Not with --demo, --dialog, --open,
                    --gcheck, --fullscreen or --view3d.
    --live-paths    a live snapshot, not the demo: a clean copy of the committed toolkit (`git archive HEAD`) in
                    SCRATCH/live-orch with no paths, two named paths added there (`agentgraph flow add ... --structure
                    <scratch> --commands <scratch>`), and the app started with AGENTGRAPH_TUI_HOME=<scratch> (the
                    plugin's snapshot and `flow prompt` run there) and AGENTGRAPH_TUI_NOSEND=1 (`r` stops after the
                    run message). Requires the debug log to say both before any key, the `2 paths` row, the panel's
                    revs equal to the back end's (16 hex digits each), the chooser listing both; a goal, Enter: the
                    panel ran `flow prompt explorer-oracle-impl-claude --goal-file - --expect <rev>` and logged the
                    message's length as `flow prompt` gives it here. Then stale: `r`, the path's text changed by
                    `flow text` from this process while the chooser is open, Enter, a goal, Enter: requires the
                    refusal (`action: stale ID REV`), no message, the toast (`changed since you ...`, `Press r
                    again`), a refresh to the new rev; `r` again: the goal offered in the prompt, Enter: `--expect
                    <new rev>` and the message. Nothing is sent. Alone, and never with --session.
    --selftest      check list_report on built-in screens and exit (no app, no pyte).

The orbit (the full-panel scene):
    --fullscreen    press `f` once the view shows (the panel's full-screen key), before the captures. With --demo,
                    require the 2D scene: the hub ball (the hub's glyph inside the canvas with braille dots round it), at least 8 agent names over at least 6 rows on both sides of the hub,
                    at least 3 group colours, the context chart, and two captures 1 s apart that differ.
    --expect-scene  require the scene without --demo (the hub ring).
    --view3d        after the 2D captures press `d` (the orbit's 2D/3D key), wait 1 s and capture the 3D view, then
                    again 1 s later. Requires (with --demo) at least 8 agent names, `@`, `0` and `1` inside the canvas
                    rows and no braille there, at least 3 group colours, and two 3D captures that differ. With
                    --expect-scene instead of --demo: the depth characters and two captures that differ. Checked
                    only with --demo or --expect-scene.

    --orbit         with --demo --fullscreen --view3d, after the 3D captures: turning the 3D view by hand. The
                    self-turn's shift of the agent names in 1 s; then six left-arrow presses (90 degrees) and the
                    names' shift 1 s after the first press, which must be more than twice the self-turn's and at least
                    6 cells; then 1 s later the names have not moved (the self-turn waits 3 s after a turn); three
                    up-arrow presses (the elevation, logged); `0` (reset, logged). Then the mouse, as a terminal sends
                    it (SGR 1006: press, drags, release): a click on the canvas switches to 2D (braille, no `@`) and
                    a second click back to 3D; a drag of 8 columns turns it (the names shift, the log says `drag`) and
                    switches nothing. Requires the plugin's debug log to show each step (`action: key left`, `key up`,
                    `orbit reset`, `mouse: down`, `drag`).
    --plugin DIR    start the app with a scratch XDG_CONFIG_HOME (SCRATCH/xdg-plugin) whose `cli.json` loads only the
                    terminal plugin at DIR (for a worktree's `tui-plugin`): no other plugin, no user config.

The `g` key (orbit or list):
    --gcheck        after the view shows (and after the keys of --pre), capture, press `g`, capture, press `g`,
                    capture. Fails unless the second capture shows the other view than the first and the third the
                    first's again, and when g made the panel full screen (a second panel size in the log; not with `f` in --pre).
                    Prints the panel size the plugin measured (from the plugin's debug log). Use it with
                    --demo. Not with --view3d or --fullscreen: those are the d/f checks above.
    --pre KEYS      keys pressed one by one (1.5 s apart) before the first capture of --gcheck: `f` full screen, `d`
                    2D/3D, `g` the view, `R` close with `q` and open the panel again with /graph.
"""
import argparse
import json
import os
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time

try:  # a real terminal for the app: Unix only; --selftest needs none of it and runs everywhere
    import fcntl
    import pty
    import termios
except ImportError:
    fcntl = pty = termios = None

DEFAULT_BINARY = "/Applications/OpenCode.app/Contents/Resources/opencode-cli"
SCRATCH = os.path.join(os.environ.get("TMPDIR_OPENCODE") or tempfile.gettempdir(), "tui-real")
sys.path.insert(0, os.environ.get("PYTEPATH", os.path.join(SCRATCH, "pylib")))
try:
    import pyte  # noqa: E402
except ImportError:
    pyte = None  # --selftest needs no terminal; everything else stops in main()

HUB = "◉ orchestrator"
# Every wait is on an event (a line on screen, a line in the plugin's debug log) with this ceiling, never a fixed
# sleep: on a loaded machine (load average 70 to 110) a dialog took more than the 1.5 s the first version slept.
CEILING = 60.0
GRAPH_ITEM = re.compile(r"/graph\s+Agent graph")  # the command list's row for /graph, before Enter
TOAST = re.compile(r"Plugin failed|Open plugins|run opencode plugin update|Run /plugins")
DEBUG_LOG = os.environ.get("AGENTGRAPH_TUI_DEBUG", "")
EXTRA_ENV = {}  # more environment for the app (--live-paths: AGENTGRAPH_TUI_HOME, AGENTGRAPH_TUI_NOSEND)
ORCHESTRATION = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEMO = ""  # AGENTGRAPH_TUI_DEMO for the app, set from --demo ("1") or --paths ("paths") in main()
BRAILLE = re.compile(r"[\u2801-\u28ff]")
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# the group colours of src/scene.js (GROUP_COLORS), as pyte reports a 24-bit foreground
GROUP_HEX = {"council": ("b39ddb", "6a4fb3"), "decide": ("7fb2ff", "2f6fd0"), "build": ("e8a07c", "ac563d"),
             "verify": ("f28fb5", "b8336a"), "understand": ("e6c15a", "8a6d00")}
# the context chart: five of its blocks, one a partial block (the 3D ball is drawn in shades and full blocks: a row of `█` is no chart)
CHART = re.compile(r"(?=█*[▁▂▃▄▅▆▇])[▁▂▃▄▅▆▇█]{5}")
# the ring agents of test/fixtures/demo.json (council seats are dots, not names)
DEMO_NAMES = ["council", "oracle", "oracle-lite", "designer", "impl-claude", "impl-hard", "impl-openai", "reviewer",
              "explorer", "researcher"]


class Tracker:
    """The processes this script started: the root it launched and every descendant seen while it ran, keyed by pid
    and start time (so a reused pid is never mistaken for ours). The app (`opencode-cli --standalone`) starts its
    private server as a child in its own process group, so killing the app's process group would miss it (in
    2.3.2 the server exited by itself within a second of a SIGKILLed app; this does not rely on that). Nothing outside this set is ever signalled: not the shared background
    service, not the user's own terminal app."""

    def __init__(self, root):
        self.root = root
        self.known = {}  # pid -> start time
        self.last = 0.0
        self.track()  # the root itself, with its start time

    @staticmethod
    def table():
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,lstart="], capture_output=True, text=True).stdout
        rows = {}
        for line in out.splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                rows[int(parts[0])] = (int(parts[1]), parts[2].strip())
        return rows

    def ours(self, rows):
        """pids alive now that are ours: the known ones still with the same start time, plus all their descendants."""
        mine = {pid for pid, started in self.known.items() if pid in rows and rows[pid][1] == started}
        if not self.known and self.root in rows:
            mine.add(self.root)  # the first look, right after the launch
        grew = True
        while grew:
            grew = False
            for pid, (ppid, _) in rows.items():
                if ppid in mine and pid not in mine:
                    mine.add(pid)
                    grew = True
        return mine

    def track(self, every=0.0):
        now = time.time()
        if now - self.last < every:
            return
        self.last = now
        rows = self.table()
        for pid in self.ours(rows):
            self.known.setdefault(pid, rows[pid][1])

    def alive(self):
        return sorted(self.ours(self.table()))

    def stop_all(self, grace=5.0):
        """SIGTERM every process of ours still alive, wait up to `grace` seconds, then SIGKILL. -> how many there were."""
        self.track()
        left = [pid for pid in self.alive()]
        found = len(left)
        if not left:
            return 0
        for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
            for pid in left:
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
            end = time.time() + wait
            while time.time() < end:
                for pid in left:  # reap our own direct children so they do not linger as zombies
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        pass
                self.track()
                if not self.alive():
                    return found
                time.sleep(0.2)
            left = self.alive()
        return found


def ensure_session(binary, proj):
    path = os.path.join(SCRATCH, "session.txt")
    if os.path.exists(path):
        sid = open(path).read().strip()
        if sid:
            return sid
    os.makedirs(proj, exist_ok=True)
    out_path = os.path.join(SCRATCH, "run-session.json")
    with open(out_path, "w") as out:
        proc = subprocess.Popen(
            [binary, "run", "--standalone", "--model", "anthropic/claude-haiku-5-5", "--title", "agentgraph-tui real-host check (throwaway)",
             "--format", "json", "Do not call any tool. Reply with exactly: OK"],
            cwd=proj, stdout=out, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True,
        )
        tracker = Tracker(proc.pid)
        try:
            end = time.time() + 180
            while proc.poll() is None and time.time() < end:
                tracker.track()
                time.sleep(0.5)
        finally:
            tracker.stop_all()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
    match = re.search(r'"sessionID"\s*:\s*"(ses_[^"]+)"', open(out_path).read())
    if not match:
        print("could not create a throwaway session", file=sys.stderr)
        sys.exit(2)
    open(path, "w").write(match.group(1))
    return match.group(1)


class Term:
    def __init__(self, argv, cwd, cols, rows, raw_path):
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self.alive = True
        self.raw = open(raw_path, "wb") if raw_path else None
        self.argv, self.cwd, self.size = argv, cwd, (cols, rows)
        self.pid, self.fd, self.tracker = None, None, None
        self.quit_result = None

    def start(self):
        """Launch the app. Separate from __init__ so the caller holds the object (and its `finally: quit()`) first."""
        argv, cwd, (cols, rows) = self.argv, self.cwd, self.size
        pid, self.fd = pty.fork()
        if pid == 0:
            os.environ["TERM"] = "xterm-256color"
            os.environ["COLORTERM"] = "truecolor"
            os.environ.pop("OPENCODE_SESSION_ID", None)
            if DEBUG_LOG:
                os.environ["AGENTGRAPH_TUI_DEBUG"] = DEBUG_LOG
            for key in ("AGENTGRAPH_TUI_HOME", "AGENTGRAPH_TUI_NOSEND"):
                os.environ.pop(key, None)
            os.environ.update(EXTRA_ENV)
            if DEMO:
                os.environ["AGENTGRAPH_TUI_DEMO"] = DEMO
            else:
                os.environ.pop("AGENTGRAPH_TUI_DEMO", None)
            os.chdir(cwd)
            try:
                os.execv(argv[0], argv)
            finally:
                os._exit(127)
        self.pid = pid
        self.tracker = Tracker(pid)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        # Answer the terminal's queries (cursor position, device attributes) as a real terminal would.
        self.screen.write_process_input = lambda data: self.write(data.encode())

    def write(self, data):
        try:
            os.write(self.fd, data)
        except OSError:
            self.alive = False

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end and self.alive:
            self.tracker.track(every=1.0)
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
            if not data:
                self.alive = False
                break
            if self.raw:
                self.raw.write(data)
            self.stream.feed(data)

    def wait_for(self, predicate, seconds):
        end = time.time() + seconds
        while time.time() < end and self.alive:
            self.pump(0.25)
            if predicate(self.lines()):
                return True
        return predicate(self.lines())

    def wait_log(self, predicate, seconds):
        """Pump until predicate(the plugin's debug log lines) holds, at most `seconds`."""
        end = time.time() + seconds
        while time.time() < end and self.alive:
            if predicate(debug_lines()):
                return True
            self.pump(0.25)
        return predicate(debug_lines())

    def lines(self):
        return [line.rstrip() for line in self.screen.display]

    def quit(self):
        """Quit the app and stop every process it started. Safe to call more than once: later calls return the
        first result. ctrl+c (twice if needed), Escape, ctrl+d, waiting up to 8 s after each key (a standalone app
        shuts its private server down first); then SIGTERM, then SIGKILL; then whatever is left of the processes it
        started (its private server above all) gets SIGTERM, then SIGKILL. Returns how it ended."""
        global QUITTING
        if self.quit_result is not None:
            return self.quit_result
        QUITTING = True
        self.quit_result = "quitting"  # a second call while this one runs (signal handler) does not start over
        if self.pid is None:  # never launched
            if self.raw:
                self.raw.close()
                self.raw = None
            self.quit_result = "not started"
            return self.quit_result
        if self.tracker is None:  # interrupted between the fork and the tracker
            self.tracker = Tracker(self.pid)
        how = "exited"
        try:
            self.tracker.track()
            how = self._quit_app()
        finally:
            leftover = self.tracker.stop_all()
            if leftover:
                how += f", then stopped {leftover} leftover process(es)"
            self.exited()
            try:
                if self.fd is not None:
                    os.close(self.fd)
            except OSError:
                pass
            if self.raw:
                self.raw.close()
                self.raw = None
            self.quit_result = how
        return how

    def _quit_app(self):
        for key in (b"\x03", b"\x03", b"\x1b", b"\x03", b"\x03", b"\x04"):
            if self.exited():
                return "exited"
            self.write(key)
            if self.wait_exit(8 if key != b"\x1b" else 0.6):
                return {b"\x03": "ctrl+c", b"\x04": "ctrl+d"}.get(key, "exited")
            if os.environ.get("REAL_HOST_TRACE_QUIT"):
                print("---- after", key, [l for l in self.lines() if l.strip()][-4:], file=sys.stderr)
        if self.exited():
            return "exited"
        for sig, name in ((signal.SIGTERM, "SIGTERM"), (signal.SIGKILL, "SIGKILL")):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                pass
            if self.wait_exit(8):
                return name
        return "SIGKILL (not reaped)"

    def wait_exit(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.exited():
                return True
            if self.alive:
                self.pump(0.2)
            else:
                time.sleep(0.2)
        return self.exited()

    def exited(self, block=False):
        if self.pid is None or getattr(self, "_reaped", False):
            return True
        try:
            pid, _ = os.waitpid(self.pid, 0 if block else os.WNOHANG)
            self._reaped = pid != 0
        except ChildProcessError:
            self._reaped = True
        return self._reaped


def find_view(lines):
    """-> (row, col, view_lines) of the agent graph, or None. view_lines: the hub line and the lines below it that
    start at the hub's column, up to the first fully blank run of three rows."""
    for row, line in enumerate(lines):
        col = line.find(HUB)
        if col < 0:
            continue
        view, blank = [], 0
        for below in lines[row:]:
            cell = below[col:col + 100].rstrip() if len(below) > col else ""
            # stop at a border glyph of the host
            cell = re.split(r"[┃│]", cell)[0].rstrip()
            if not cell.strip():
                blank += 1
                if blank >= 3:
                    break
                view.append("")
                continue
            blank = 0
            view.append(cell)
        while view and not view[-1]:
            view.pop()
        return row, col, view
    return None


def colour_runs(screen, found):
    """The foreground colour of each run of text in the first two view lines (to check the theme mapping)."""
    if found is None:
        return []
    row, col, view = found
    runs = []
    for r in range(row, min(row + 2, screen.lines)):
        line = screen.buffer[r]
        current, fg = "", None
        for x in range(col, min(col + 100, screen.columns)):
            ch = line[x]
            if ch.data in "┃│":
                break
            if ch.fg != fg and current.strip():
                runs.append((current.strip(), fg))
                current = ""
            fg = ch.fg
            current += ch.data
        if current.strip():
            runs.append((current.strip(), fg))
    return runs


LIST_WORK = re.compile(r"^(?:[" + SPIN + r"] \S|◉ \S+ [" + SPIN + r"]|[!?] .* \d+(?:s|m|h|d|h\d+m)$)")
LIST_KEYS = re.compile(r"\bg graph\b.*\bq close$")


def list_rows(lines, found):
    """-> the list's rows from the screen: the needs-you rows above the hub row (same column), each with its activity
    line (two spaces, then what the agent does), and the view. The chat under the rows (its heading down to the line
    before the key line) is not part of the list."""
    row, col, view = found
    cell = lambda r: re.split(r"[┃│]", lines[r][col:col + 100])[0].rstrip()
    above = []
    r = row - 1
    while r >= 0 and (re.match(r"[!?↩] \S", cell(r)) or re.match(r"  \S", cell(r))):
        above.insert(0, cell(r))
        r -= 1
    while above and not re.match(r"[!?↩] \S", above[0]):
        above.pop(0)  # an indented line with no needs row over it is not ours
    body = [v for v in view if v.strip()]
    heading = next((i for i, v in enumerate(body) if CHAT_HEADING.search(v)), None)
    if heading is not None:
        keys = len(body) - 1 if LIST_KEYS.search(body[-1]) else len(body)
        body = body[:heading] + body[keys:]
    return above + body


def list_report(rows):
    """-> (problems, facts) for the busy list of the demo: rows as list_rows gives them (no cursor gutter)."""
    problems, facts = [], []
    work = [r for r in rows if LIST_WORK.search(r) and not r.startswith(HUB)]  # the hub's own row does not count
    hub = [r for r in rows if r.startswith(HUB)]
    progress = [r for r in rows if re.search(r"\b\d+/\d+\b", r) and not r.startswith(HUB)]
    facts.append(f"{len(rows)} rows, {len(work)} at work, hub {'yes' if hub else 'no'}, progress {'yes' if progress else 'no'}, "
                 f"keys {'yes' if rows and LIST_KEYS.search(rows[-1]) else 'no'}")
    if len(rows) > 16:
        problems.append(f"{len(rows)} rows, more than 16")
    if not hub or not re.search(r" \d+ idle$", hub[0]):
        problems.append("no hub row with the idle count")
    if len(work) < 2:
        problems.append(f"{len(work)} rows of agents at work besides the hub, fewer than 2")
    if not any(re.match(r"[" + SPIN + r"] \S", r) for r in work):
        problems.append("no spinner row: no agent besides the hub is shown working")
    if not progress:
        problems.append("no progress row")
    if not rows or not LIST_KEYS.search(rows[-1]):
        problems.append("the key line is not last")
    return problems, facts


SELFTEST_LIST = [
    "! impl-claude stalled ↻5  Apply the limiter in the middleware chain     7m",
    "◉ orchestrator ⠴  Add per-user rate limiting                       11 idle",
    "⠦ designer  429 error page and retry-after message                      6m",
    "━━━━────── 4/11  add-rate-limit  gate reviewer refuted",
    "$6.80  ctx ━─────── 34.7k •••3",
    "↑↓ select  ⏎ open  s stop  m message  g graph  q close",
]


def refuse(demo, dialog, session_given):
    """-> why these options must not run, or None. `--dialog` presses `s` (stop) on the selected row: only the demo's
    made-up sessions may be there, so it needs --demo and refuses an explicit --session."""
    if dialog and not demo:
        return "--dialog presses s (stop) on the selected row: it runs only with --demo"
    if dialog and session_given:
        return "--dialog presses s (stop) on the selected row: not with --session"
    return None


def selftest():
    """list_report and list_rows on built-in screens: the demo list passes; the orbit, a short list, a list without
    keys and a list without the progress row fail."""
    gutter = lambda rows: ["›" + rows[0]] + [" " + r for r in rows[1:]]
    screen = [" " * 10 + l for l in gutter(SELFTEST_LIST)]
    found = find_view(screen)
    cases = [
        ("the demo list from the screen", list_rows(screen, found) if found else [], True),
        ("the demo list", SELFTEST_LIST, True),
        ("the orbit", ["◉ orchestrator                                $6.57"] + ["      ⠤⠤⠤⠤  ⠒⠒ ⠉⠉"] * 30, False),
        ("only the hub at work", [SELFTEST_LIST[1]] + SELFTEST_LIST[3:], False),
        ("the hub and one other at work", SELFTEST_LIST[1:], False),
        ("the hub and two needs-you rows, no spinner row",
         [SELFTEST_LIST[0], "? designer asks you                                                    6m"] + [SELFTEST_LIST[1]] + SELFTEST_LIST[3:], False),
        ("the hub, one spinner row and one needs-you row", SELFTEST_LIST, True),
        ("no key line", SELFTEST_LIST[:-1], False),
        ("no progress row", SELFTEST_LIST[:3] + SELFTEST_LIST[4:], False),
        ("no idle count", [SELFTEST_LIST[0], "◉ orchestrator ⠴  Add per-user rate limiting"] + SELFTEST_LIST[2:], False),
    ]
    wrong = []
    for name, rows, good in cases:
        problems, _ = list_report(rows)
        if (not problems) != good:
            wrong.append(f"{name}: {'passed' if not problems else 'failed: ' + '; '.join(problems)}")
    refusals = [((True, True, False), False), ((False, True, False), True), ((True, True, True), True),
                ((False, True, True), True), ((False, False, True), False), ((True, False, False), False)]
    for (demo, dialog, given), refused in refusals:
        if (refuse(demo, dialog, given) is not None) != refused:
            wrong.append(f"refuse(demo={demo}, dialog={dialog}, session={given}): {'refused' if not refused else 'allowed'}")
    cases += refusals
    for line in wrong:
        print("selftest: WRONG " + line)
    if wrong:
        return 1
    print(f"selftest: {len(cases)} cases, all as expected")
    return 0


def debug_lines():
    try:
        return open(DEBUG_LOG, encoding="utf8").read().splitlines() if DEBUG_LOG else []
    except OSError:
        return []


def cursor_row(lines, found):
    """-> the screen row of the list's cursor (`›` just left of the view's column), or None."""
    _, col, _ = found
    if col < 1:
        return None
    for r, line in enumerate(lines):
        if len(line) > col and line[col - 1] == "›":
            return r
    return None


def drive_dialog(term, found_lines):
    """--dialog: `s` on the selected row, then down and Enter inside the host's confirm. -> (problems, facts, screens)"""
    problems, facts, screens = [], [], []
    found = find_view(found_lines)
    before = cursor_row(found_lines, found) if found else None
    opened_before = sum("action: open" in l for l in debug_lines())
    term.write(b"s")
    term.wait_for(lambda ls: any("Stop impl-claude?" in l for l in ls), CEILING)
    term.pump(0.5)
    shown = term.lines()
    screens.append(("after s", shown))
    if not any("Stop impl-claude?" in l for l in shown):
        problems.append("dialog: no `Stop impl-claude?` confirm on screen after s")
    term.write(b"\x1b[B")  # down: the dialog has the keys, the panel's cursor must not move
    term.pump(1.0)
    under = term.lines()
    screens.append(("down in the dialog", under))
    term.write(b"\r")  # Enter answers the confirm, and must not also reach the panel
    term.wait_for(lambda ls: not any("Stop impl-claude?" in l for l in ls), CEILING)
    term.wait_log(lambda log: any("action: stop " in l for l in log), CEILING)  # the plugin saw the answer
    term.pump(1.0)  # time for a leaked Enter to act, if it did
    # the demo has no real session to stop: the host's error toast covers the sidebar for a few seconds; wait it out
    if found:
        term.wait_for(lambda ls: cursor_row(ls, found) is not None, CEILING)
    after = term.lines()
    screens.append(("Enter in the dialog", after))
    if any("Stop impl-claude?" in l for l in after):
        problems.append("dialog: still open after Enter")
    now = cursor_row(after, found) if found else None  # the column found before: a toast may cover the hub row now
    facts.append(f"cursor row before {before}, after down and Enter {now}")
    if before is None or now != before:
        problems.append(f"dialog: the cursor moved ({before} -> {now})")
    log = debug_lines()
    opened = sum("action: open" in l for l in log)
    facts += [l.split(" ", 1)[1] for l in log if "action:" in l][-4:]
    if opened != opened_before:
        problems.append("dialog: Enter in the confirm also opened a session")
    return problems, facts, screens


CHAT_HEADING = re.compile(r"(chat|transcript) · (\S+) ─")


def chat_heading(lines):
    """-> (row, kind, name) of the chat's heading on the screen, or None."""
    for i, line in enumerate(lines):
        m = CHAT_HEADING.search(line)
        if m:
            return i, m.group(1), m.group(2)
    return None


def chat_body(lines):
    """-> the non-empty screen text under the heading, up to the key line."""
    at = chat_heading(lines)
    if at is None:
        return []
    found = find_view(lines)
    col = found[1] if found else 0
    out = []
    for line in lines[at[0] + 1:]:
        text = line[col:].rstrip()
        if not text.strip():
            break
        out.append(text)
    return out


def drive_chat(term, lines):
    """--chat: the chat under the list (or opened with `c`), the orchestrator's transcript, a working agent's chat.
    -> (problems, facts, screens)"""
    problems, facts, screens = [], [], []
    term.pump(1.0)
    opened_by_c = False
    if chat_heading(term.lines()) is None:
        term.write(b"c")
        term.wait_for(lambda ls: chat_heading(ls) is not None, CEILING)
        term.pump(0.5)
        opened_by_c = True
    shown = term.lines()
    screens.append(("the chat" + (" after c" if opened_by_c else ""), shown))
    at = chat_heading(shown)
    if at is None:
        problems.append("chat: no heading `chat · NAME ───` on screen" + (" after c" if opened_by_c else ""))
        return problems, facts, screens
    facts.append(f"{'opened with c' if opened_by_c else 'beside the list'}: {at[1]} · {at[2]}, {len(chat_body(shown))} lines")
    if (at[1], at[2]) != ("chat", "impl-claude"):
        problems.append(f"chat: expected `chat · impl-claude`, saw `{at[1]} · {at[2]}`")
    if len(chat_body(shown)) < 3:
        problems.append("chat: fewer than 3 lines of the chat")
    if not opened_by_c:
        for key, want, label in ((b"\x1b[B", ("transcript", "orchestrator"), "down once"), (b"\x1b[B", ("chat", "designer"), "down twice")):
            term.write(key)
            term.wait_for(lambda ls: (chat_heading(ls) or (0, 0, 0))[1:] == want, CEILING)
            term.pump(1.0)
            shot = term.lines()
            screens.append((label, shot))
            now = chat_heading(shot)
            facts.append(f"{label}: {now[1] + ' · ' + now[2] if now else 'no heading'}, {len(chat_body(shot))} lines")
            if now is None or (now[1], now[2]) != want:
                problems.append(f"chat: after {label} expected `{want[0]} · {want[1]}`, saw {now}")
            elif len(chat_body(shot)) < 2:
                problems.append(f"chat: after {label} fewer than 2 lines of the chat")
        for _ in range(2):
            term.write(b"\x1b[A")
            term.pump(0.5)
        term.pump(1.0)
        back = chat_heading(term.lines())
        if back is None or back[2] != "impl-claude":
            problems.append(f"chat: after up twice expected impl-claude, saw {back}")
    else:
        term.write(b"\x1b")  # Escape closes the chat opened with c
        term.wait_for(lambda ls: chat_heading(ls) is None, CEILING)
        term.pump(0.5)
        shot = term.lines()
        screens.append(("after Escape", shot))
        if chat_heading(shot) is not None:
            problems.append("chat: Escape did not close the chat")
        term.write(b"c")
        term.wait_for(lambda ls: chat_heading(ls) is not None, CEILING)
        term.pump(0.5)
        if chat_heading(term.lines()) is None:
            problems.append("chat: c did not open it again")
        term.write(b"c")
        term.wait_for(lambda ls: chat_heading(ls) is None, CEILING)
        term.pump(0.5)
        if chat_heading(term.lines()) is not None:
            problems.append("chat: c did not close it")
    return problems, facts, screens


def drive_open(term, lines, session):
    """--open: Enter on the hub row. -> (problems, facts, screen)"""
    problems, facts = [], []
    found = find_view(lines)
    row = cursor_row(lines, found) if found else None
    if found is None or row != found[0]:
        problems.append(f"open: the cursor is not on the hub row (cursor {row}, hub {found[0] if found else None})")
    term.write(b"\r")
    term.wait_log(lambda log: any("action: open" in l or " open:" in l for l in log), CEILING)
    term.wait_for(lambda ls: any("ctrl+p" in l or "commands" in l for l in ls), CEILING)
    after = term.lines()
    log = debug_lines()
    asked = [l for l in log if "action: open" in l]
    facts += [l.split(" ", 1)[1] for l in asked]
    if not any(l.endswith(f"action: open {session}") for l in asked):
        problems.append(f"open: the plugin did not ask the host to open {session}")
    errors = [l for l in log if " open:" in l]
    if errors:
        problems.append("open: " + errors[-1])
    if not any("ctrl+p" in l or "commands" in l for l in after):
        problems.append("open: the session is not on screen after Enter")
    return problems, facts, after


PATHS_ROW = re.compile(r"^3 paths · last impl-loop \d+h(?:\d+m)? ago$")
PATHS_SAMPLE = ["impl-loop", "deep-change", "designer-reviewer-designer"]


def drive_paths(term, lines):
    """--paths: the rest list with the paths row, then `r`, the chooser, Enter, the goal prompt, Escape.
    -> (problems, facts, screens)"""
    problems, facts, screens = [], [], []
    found = find_view(lines)
    rows = list_rows(lines, found) if found else []
    facts.append(f"{len(rows)} rows at rest: " + " | ".join(rows))
    if len(rows) > 4:
        problems.append(f"paths: {len(rows)} rows at rest, more than 4")
    if not any(PATHS_ROW.match(r) for r in rows):
        problems.append("paths: no `3 paths · last impl-loop 2h ago` row")
    if not rows or not re.search(r"\br run\b", rows[-1]):
        problems.append("paths: no `r run` in the key line")
    before = sum(1 for l in debug_lines() if "action:" in l)
    term.write(b"r")
    term.wait_for(lambda ls: any("Run a path" in l for l in ls) and all(any(n in l for l in ls) for n in PATHS_SAMPLE)
                  and any("3 runs · $1.20 avg · 2.0 rounds" in l for l in ls), CEILING)
    chooser = term.lines()
    screens.append(("after r", chooser))
    if not any("Run a path" in l for l in chooser):
        problems.append("paths: no `Run a path` chooser after r")
    for name in PATHS_SAMPLE:
        if not any(name in l for l in chooser):
            problems.append(f"paths: `{name}` is not in the chooser")
    if not any("3 runs · $1.20 avg · 2.0 rounds" in l for l in chooser):
        problems.append("paths: no `3 runs · $1.20 avg · 2.0 rounds` in the chooser")
    if not any("impl-openai → verifier → impl-openai" in l for l in chooser):
        problems.append("paths: no `impl-openai → verifier → impl-openai` in the chooser")
    term.write(b"\r")  # the first row (impl-loop, run last) is the current one
    term.wait_for(lambda ls: any("Goal for impl-loop" in l for l in ls), CEILING)
    goal = term.lines()
    screens.append(("Enter in the chooser", goal))
    if not any("Goal for impl-loop" in l for l in goal):
        problems.append("paths: no `Goal for impl-loop` prompt after Enter")
    term.write(b"\x1b")  # Escape: no goal, nothing sent
    term.wait_for(lambda ls: not any("Goal for impl-loop" in l or "Run a path" in l for l in ls), CEILING)
    term.pump(0.5)
    after = term.lines()
    screens.append(("Escape in the goal prompt", after))
    if any("Goal for impl-loop" in l or "Run a path" in l for l in after):
        problems.append("paths: a dialog is still open after Escape")
    actions = [l.split(" ", 1)[1] for l in debug_lines() if "action:" in l][before:]
    facts += actions
    if not any(a.startswith("action: choose impl-openai-verifier-impl-openai") for a in actions):
        problems.append("paths: the plugin did not log `action: choose impl-openai-verifier-impl-openai`")
    leaked = [a for a in actions if re.match(r"action: (send|run|open|stop)\b", a)]
    if leaked:
        problems.append("paths: " + "; ".join(leaked))
    return problems, facts, screens


def view_shown(ls):
    found = find_view(ls)
    return found is not None and len([v for v in found[2] if v]) >= 2


def app_started(term):
    started = term.wait_for(lambda ls: any("ctrl+p" in l or "commands" in l for l in ls), 2 * CEILING)
    term.pump(4)  # let plugins load and the first sidebar snapshot arrive
    return started


LIVE_PATHS = [  # (name, agents): the two named paths of --live-paths' scratch structure folder
    ("review-loop", ["impl-openai", "verifier", "impl-openai"]),
    ("deep-change", ["explorer", "oracle", "impl-claude"]),
]
LIVE_GOAL = "Tune a new blue cache"  # no panel key in it (s m r g q f d), should a keystroke ever reach the panel
LIVE_STALE_GOAL = "Tune the blue cache"  # the same: typed once, then offered again after the refusal


def scratch_orchestration():
    """--live-paths: a scratch orchestration folder (SCRATCH/live-orch) holding a clean copy of the committed toolkit
    (`git archive HEAD`, so edits in progress in the working folder are not run) with its own structure/ and no
    paths, then two named paths added there by `agentgraph flow add` (--structure and --commands point into the
    scratch folder, so neither the real structure folder nor the real commands folder is touched). -> folder"""
    import shutil
    root = os.path.join(SCRATCH, "live-orch")
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root)
    tar = subprocess.run(["git", "-C", ORCHESTRATION, "archive", "--format=tar", "HEAD"], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", root], input=tar, check=True)
    flows = os.path.join(root, "structure", "flows")
    if os.path.isdir(flows):
        shutil.rmtree(flows)
    for name, agents in LIVE_PATHS:
        backend(root, "flow", "add", *agents, "--name", name, *scratch_dirs(root))
    return root


def scratch_dirs(root):
    return ["--structure", os.path.join(root, "structure"), "--commands", os.path.join(root, "commands")]


def backend(root, *argv, stdin=b""):
    """Run `agentgraph structure ARGV` from the scratch copy. -> (exit code, stdout, stderr)"""
    done = subprocess.run([sys.executable, "-P", "-m", "agentgraph.structure", *argv], input=stdin,
                          env=dict(os.environ, PYTHONPATH=root), cwd=root, capture_output=True, timeout=60)
    return done.returncode, done.stdout.decode().rstrip(), done.stderr.decode().strip()


def scratch_revs(root):
    """{flow id: rev} as the scratch back end reports them now."""
    code, out, err = backend(root, "flows", "--json", "--structure", os.path.join(root, "structure"))
    return {f["id"]: f.get("rev") for f in json.loads(out)["flows"]} if code == 0 else {}


def flow_prompt(root, flow_id, goal):
    """The run message as the plugin gets it: `python -P -m agentgraph.structure flow prompt ID --goal-file -`."""
    return backend(root, "flow", "prompt", flow_id, "--goal-file", "-", stdin=goal.encode())


def logged(prefix):
    """The debug log's lines that start (after the time) with `prefix`."""
    return [l.split(" ", 1)[1] for l in debug_lines() if " " in l and l.split(" ", 1)[1].startswith(prefix)]


def drive_live_paths(term, lines, root, session):
    """--live-paths: the list at rest from a live snapshot of the scratch folder; `r`, the chooser with its two named
    paths and their revs, Enter, a goal, Enter: the plugin runs `flow prompt ... --expect REV` and, with
    AGENTGRAPH_TUI_NOSEND, stops before the send. Then stale for real: `r`, and while the chooser shows the path its
    text is changed by the back end from this process; Enter, a goal, Enter: refused, nothing sent, a refresh to the
    new rev, the toast; `r` again offers the typed goal, Enter runs it with the new rev. -> (problems, facts, screens)"""
    problems, facts, screens = [], [], []
    labels = [f"{name}  {' → '.join(agents)}" for name, agents in LIVE_PATHS]
    flow_id, first = "explorer-oracle-impl-claude", "deep-change"  # neither has run: the snapshot's order, by id
    modes = logged("mode: ")
    facts += modes
    if not any("nosend" in m for m in modes) or not any(f"home {root}" in m for m in modes):
        return ["live paths: the plugin did not log nosend and the scratch home: not pressing r"], facts, screens
    term.wait_for(lambda ls: (lambda f: f is not None and any(r.startswith("2 paths") for r in list_rows(ls, f)))(find_view(ls)), CEILING)
    lines = term.lines()
    found = find_view(lines)
    rows = list_rows(lines, found) if found else []
    facts.append(f"{len(rows)} rows at rest: " + " | ".join(rows))
    if not any(r == "2 paths" for r in rows):
        problems.append("live paths: no `2 paths` row (the live snapshot of the scratch folder)")
    if not rows or not re.search(r"\br run\b", rows[-1]):
        problems.append("live paths: no `r run` in the key line")
    revs = scratch_revs(root)
    seen = logged("paths: ")
    facts.append(f"back end revs: {revs}; the panel's: {seen[-1] if seen else 'none'}")
    want = " ".join(f"{i}@{r}" for i, r in sorted(revs.items()))
    if len(revs) != 2 or not all(re.fullmatch(r"[0-9a-f]{16}", r or "") for r in revs.values()):
        problems.append(f"live paths: the scratch back end does not give two paths each with a 16-hex rev: {revs}")
    elif not seen or seen[-1] != f"paths: {want}":
        problems.append(f"live paths: the panel's snapshot does not carry the back end's revs ({want})")
    if problems:
        return problems, facts, screens

    def choose(label):
        """r, and the chooser with both paths. -> True when it showed"""
        term.write(b"r")
        if not term.wait_for(lambda ls: any("Run a path" in l for l in ls) and all(any(x in l for l in ls) for x in labels), CEILING):
            problems.append(f"live paths ({label}): the chooser with both paths did not show")
            return False
        screens.append((f"{label}: after r", term.lines()))
        return True

    def goal_step(goal, label, typed=True):
        term.write(b"\r")
        if not term.wait_for(lambda ls: any(f"Goal for {first}" in l for l in ls), CEILING):
            problems.append(f"live paths ({label}): no `Goal for {first}` prompt after Enter")
            term.write(b"\x1b")
            return False
        if typed:
            for ch in goal:
                term.write(ch.encode())
                term.pump(0.05)
        shown = term.wait_for(lambda ls: any(goal in l for l in ls) and any(f"Goal for {first}" in l for l in ls), CEILING)
        screens.append((f"{label}: goal {'typed' if typed else 'offered'}", term.lines()))
        if not shown:
            problems.append(f"live paths ({label}): the goal is not in the prompt{'' if typed else ' (not offered again)'}; not pressing Enter")
            term.write(b"\x1b")
            return False
        term.write(b"\r")
        return True

    def prompts():
        return logged(f"action: prompt {flow_id} ")

    # 1. the panel passes the rev it shows and gets the message
    old = revs[flow_id]
    if choose("first") and goal_step(LIVE_GOAL, "first"):
        term.wait_log(lambda log: len(prompts()) >= 1 or any(" run:" in l for l in log), CEILING)
        screens.append(("first: Enter in the goal prompt", term.lines()))
    made = [a for a in prompts() if a.endswith(f"for {session}, not sent")]
    argv = logged("action: flow prompt: ")
    facts += argv
    if not argv or not argv[-1].endswith(f" -P -m agentgraph.structure flow prompt {flow_id} --goal-file - --expect {old}"):
        problems.append(f"live paths: the panel did not run `flow prompt {flow_id} --goal-file - --expect {old}`")
    code, text, err = flow_prompt(root, flow_id, LIVE_GOAL)
    facts.append(f"flow prompt {flow_id} (the same command, run here): exit {code}, {len(text)} chars" + (f", stderr {err}" if err else ""))
    facts += ["  > " + line for line in text.splitlines()[:2]]
    if code != 0 or LIVE_GOAL not in text:
        problems.append("live paths: `flow prompt` did not return a message with the goal")
    elif not made or f" {len(text)} chars " not in made[-1]:
        problems.append(f"live paths: the plugin did not log `action: prompt {flow_id} {len(text)} chars for {session}, not sent`")
    if problems:
        return problems, facts, screens

    # 2. stale for real: the path changes under the open chooser
    term.wait_for(lambda ls: not any("Not sent" in l for l in ls), CEILING)  # the first toast gone
    if not choose("stale"):
        return problems, facts, screens
    code, out, err = backend(root, "flow", "text", flow_id, "--text-file", "-", *scratch_dirs(root),
                             stdin=b"explorer maps the code; oracle decides; impl-claude builds it (changed under the chooser).\n")
    new = scratch_revs(root).get(flow_id)
    facts.append(f"changed {flow_id} under the chooser: exit {code}{', ' + err if err else ''}; rev {old} -> {new}")
    if code != 0 or not new or new == old:
        problems.append("live paths: the back end did not change the path's rev")
        term.write(b"\x1b")
        return problems, facts, screens
    if not goal_step(LIVE_STALE_GOAL, "stale"):
        return problems, facts, screens
    term.wait_log(lambda log: any(f"action: stale {flow_id} {old}" in l for l in log), CEILING)
    toast = term.wait_for(lambda ls: any("changed since you" in l for l in ls) and any("Press r again" in l for l in ls), CEILING)
    screens.append(("stale: Enter in the goal prompt", term.lines()))
    refreshed = term.wait_log(lambda log: any(l.split(" ", 1)[-1].startswith("paths: ") and f"{flow_id}@{new}" in l for l in log), CEILING)
    stale = logged(f"action: stale {flow_id} ")
    argv = logged("action: flow prompt: ")
    facts += stale + argv[-1:] + [l for l in logged("paths: ")][-1:]
    if not stale or stale[-1] != f"action: stale {flow_id} {old}":
        problems.append(f"live paths: the plugin did not log `action: stale {flow_id} {old}` (the refusal not recognised)")
    if len(prompts()) != 1:
        problems.append("live paths: a run message was made for the stale path")
    if not toast:
        problems.append("live paths: no toast saying the path changed and to press r again")
    if not refreshed:
        problems.append(f"live paths: the panel did not refresh to {flow_id}@{new}")
    if problems:
        return problems, facts, screens
    term.wait_for(lambda ls: not any("changed since you" in l for l in ls), CEILING)

    # 3. r again: the typed goal is offered; Enter runs it with the new rev
    if choose("again") and goal_step(LIVE_STALE_GOAL, "again", typed=False):
        term.wait_log(lambda log: len(prompts()) >= 2 or len(logged(f"action: stale {flow_id} ")) >= 2, CEILING)
        screens.append(("again: Enter in the goal prompt", term.lines()))
    argv = logged("action: flow prompt: ")
    facts += argv[-1:] + prompts()[-1:]
    if not argv or not argv[-1].endswith(f" flow prompt {flow_id} --goal-file - --expect {new}"):
        problems.append(f"live paths: the second run did not pass the new rev (--expect {new})")
    code, text, err = flow_prompt(root, flow_id, LIVE_STALE_GOAL)
    if len(prompts()) != 2 or code != 0 or not prompts()[-1].startswith(f"action: prompt {flow_id} {len(text)} chars for {session}"):
        problems.append(f"live paths: the second run did not make the run message ({len(text)} chars)")
    actions = logged("action: ")
    if any(re.match(r"action: (send|open|stop)\b", a) for a in actions):
        problems.append("live paths: " + "; ".join(a for a in actions if re.match(r"action: (send|open|stop)\b", a)))
    return problems, facts, screens


LEFT, UP = b"\x1b[D", b"\x1b[A"


def mouse(term, kind, col, row):
    """A mouse event as a terminal sends it with SGR reporting (1006), 0-based cell -> 1-based: press, drag
    (button 0 held, motion bit 32) or release."""
    code, end = {"press": (0, "M"), "drag": (32, "M"), "release": (0, "m")}[kind]
    term.write(f"\x1b[<{code};{col + 1};{row + 1}{end}".encode())


def name_spots(lines, names):
    """-> {name: (row, col)} of each agent name standing alone (not part of a longer name) in the canvas rows."""
    found = find_view(lines)
    rows = canvas_rows(lines, found) if found else None
    spots = {}
    if rows is None:
        return spots
    for r in range(rows[0], rows[1] + 1):
        for name in names:
            m = re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", lines[r][found[1]:])
            if m and name not in spots:
                spots[name] = (r, found[1] + m.start())
    return spots


def spot_shift(a, b):
    """The names' movement between two captures: the sum over names in both of |columns| + 2 |rows| (a row is
    about two columns tall), and the count of names in both."""
    both = [n for n in a if n in b]
    return sum(abs(a[n][1] - b[n][1]) + 2 * abs(a[n][0] - b[n][0]) for n in both), len(both)


def drive_orbit(term):
    """--orbit: the keys and the mouse on the 3D orbit. -> (problems, facts, screens)."""
    problems, facts, screens = [], [], []
    spots = lambda: name_spots(term.lines(), DEMO_NAMES)
    count = lambda prefix: len(logged(prefix))
    a = spots()
    term.pump(1.0)
    b = spots()
    self_shift, n_self = spot_shift(a, b)
    facts.append(f"orbit: the self-turn moved {n_self} names by {self_shift} cells in 1 s")
    screens.append(("before the turn keys", term.lines()))
    t0, keys0 = time.time(), count("action: key left")
    for _ in range(6):
        term.write(LEFT)
        term.pump(0.05)
    term.pump(max(0.0, 1.0 - (time.time() - t0)))
    c = spots()
    screens.append(("1 s after six left arrows", term.lines()))
    key_shift, n_key = spot_shift(b, c)
    presses = count("action: key left") - keys0
    facts.append(f"orbit: six left arrows ({presses} logged) moved {n_key} names by {key_shift} cells in 1 s")
    if presses != 6:
        problems.append(f"orbit: {presses} of 6 left arrows reached the panel's turn key")
    if n_key < 4 or key_shift < 6 or key_shift <= 2 * self_shift:
        problems.append(f"orbit: the turn keys moved the names {key_shift} cells, the self-turn {self_shift}: not more than twice")
    term.pump(1.0)
    d = spots()
    held, n_held = spot_shift(c, d)
    facts.append(f"orbit: 1 s later (the self-turn held) {n_held} names moved {held} cells")
    if held > 2:
        problems.append(f"orbit: the names moved {held} cells while the self-turn should be held")
    ups0 = count("action: key up")
    for _ in range(3):
        term.write(UP)
        term.pump(0.15)
    term.pump(0.5)
    ups = logged("action: key up")[ups0:]
    facts.append(f"orbit: three up arrows: {len(ups)} logged" + (f", last {ups[-1]}" if ups else ""))
    if len(ups) != 3:
        problems.append(f"orbit: {len(ups)} of 3 up arrows reached the panel")
    screens.append(("after three up arrows", term.lines()))
    resets0 = count("action: orbit reset")
    term.write(b"0")
    term.wait_log(lambda ls: len([l for l in ls if " action: orbit reset" in l]) > resets0, 10)
    if count("action: orbit reset") <= resets0:
        problems.append("orbit: `0` did not reset")
    else:
        facts.append("orbit: `0` reset the camera (logged)")
    # the mouse: a click in the canvas (the hub's glyph), 2D, a click again, 3D
    found = find_view(term.lines())
    rows = canvas_rows(term.lines(), found) if found else None
    if rows is None:
        problems.append("orbit: no canvas to click on")
        return problems, facts, screens
    hub = next(((r, term.lines()[r].find("◉", found[1] + 2)) for r in range(rows[0], rows[1] + 1)
                if term.lines()[r].find("◉", found[1] + 2) >= 0), None)
    at_row, at_col = hub if hub else ((rows[0] + rows[1]) // 2, found[1] + 30)
    downs0 = count("mouse: down")
    mouse(term, "press", at_col, at_row)
    term.pump(0.1)
    mouse(term, "release", at_col, at_row)
    two_d = term.wait_for(lambda ls: orbit_on(ls) and not three_d_on(ls) and any(BRAILLE.match(ch) and ch not in SPIN for ch in "".join(ls)), 10)
    screens.append(("after a click", term.lines()))
    clicks = logged("mouse: up")
    facts.append(f"orbit: a click ({count('mouse: down') - downs0} press logged{', ' + clicks[-1] if clicks else ''}): {'2D' if two_d else 'still 3D'}")
    if not two_d:
        problems.append("orbit: a click on the 3D orbit did not switch to 2D")
    mouse(term, "press", at_col, at_row)
    term.pump(0.1)
    mouse(term, "release", at_col, at_row)
    back = term.wait_for(three_d_on, 10)
    term.pump(1.0)
    facts.append(f"orbit: a second click: {'3D' if back else 'not 3D'}")
    if not back:
        problems.append("orbit: a second click did not switch back to 3D")
    # a drag: 8 columns to the right, one column at a time
    e = spots()
    drags0 = count("action: drag")
    mouse(term, "press", at_col, at_row)
    term.pump(0.05)
    for k in range(1, 9):
        mouse(term, "drag", at_col + k, at_row)
        term.pump(0.05)
    mouse(term, "release", at_col + 8, at_row)
    term.pump(0.6)
    f = spots()
    drag_shift, n_drag = spot_shift(e, f)
    drags = count("action: drag") - drags0
    still3d = three_d_on(term.lines())
    screens.append(("after a drag of 8 columns", term.lines()))
    facts.append(f"orbit: a drag of 8 columns: {drags} turns logged, {n_drag} names moved {drag_shift} cells, {'still 3D' if still3d else 'not 3D'}")
    if drags < 4 or drag_shift < 6 or not still3d:
        problems.append(f"orbit: the drag did not turn the 3D view ({drags} turns logged, {drag_shift} cells, 3D {still3d})")
    return problems, facts, screens


def drive(term, args):
    started = app_started(term)
    if not args.no_graph:
        open_panel(term, args)
    if (args.list_mode or args.dialog or args.open or args.paths or args.live_paths or args.chat) and not args.fullscreen:
        # /graph opens on the orbit when the panel fits it (36x14 up, so the side panel of every size run here): these
        # runs test the list, so show it with `g` first (the panel's own key, remembered while the app runs)
        if term.wait_for(orbit_on, args.wait):
            term.pump(1.0)
            term.write(b"g")
            term.wait_for(lambda ls: find_view(ls) is not None and not orbit_on(ls), args.wait)
            term.pump(0.5)
    if args.fullscreen and find_view(term.lines()) is not None:
        col = find_view(term.lines())[1]
        term.write(b"f")
        # full screen moves the view to the left edge; with --demo the orbit then shows
        term.wait_for(lambda ls: find_view(ls) is not None and find_view(ls)[1] != col and (not args.demo or orbit_on(ls)), args.wait)
        term.pump(0.5)
    # toasts (plugin update notices, load warnings) draw over the right side for a few seconds: wait them out
    term.wait_for(lambda ls: not any(TOAST.search(l) for l in ls), args.settle)
    term.pump(args.keep_open)
    if args.demo or args.expect_scene:
        term.pump(2.0)  # the orbit never stands still: no waiting for a quiet screen
    else:
        # the app redraws only the cells that change: wait until two captures 1.5 s apart are the same
        previous, end = None, time.time() + CEILING / 2
        while time.time() < end:
            term.pump(1.5)
            if term.lines() == previous:
                break
            previous = term.lines()
    lines = term.lines()
    seen2d = screen_colours(term.screen)
    term.pump(1.0)
    second = term.lines()  # one second later
    colours = colour_runs(term.screen, find_view(lines))
    view3d = None
    if args.view3d and find_view(lines) is not None:
        term.write(b"d")  # the orbit's 2D/3D key: the camera tilts over 0.7 s
        term.wait_for(three_d_on, args.wait)
        term.pump(0.5)
        first3 = term.lines()
        seen3d = screen_colours(term.screen)
        term.pump(1.0)
        view3d = (first3, term.lines(), seen3d)
        if args.orbit:
            args.orbit_result = drive_orbit(term)
    extra = None
    if args.dialog and find_view(lines) is not None:
        extra = ("dialog",) + drive_dialog(term, lines)
    elif args.paths and find_view(lines) is not None:
        extra = ("paths",) + drive_paths(term, lines)
    elif args.live_paths and find_view(lines) is not None:
        extra = ("live paths",) + drive_live_paths(term, lines, args.live_root, args.session_id)
    elif args.chat and find_view(lines) is not None:
        extra = ("chat",) + drive_chat(term, lines)
    elif args.open and find_view(lines) is not None:
        p, f, after = drive_open(term, lines, args.session_id)
        extra = ("open", p, f, [("after Enter", after)])
    args.extra = extra
    after_q = None
    if not args.no_graph and find_view(lines) is not None:
        # `q` is this plugin's close key while its panel has the keyboard
        col = find_view(lines)[1]
        term.write(b"q")
        term.wait_for(lambda ls: find_view(ls) is None or find_view(ls)[1] != col, CEILING / 2)
        after_q = term.lines()
    return lines, colours, after_q, started, second, seen2d, view3d


def orbit_on(lines):
    """True when the orbit (2D or 3D) is on screen: the hub's glyph inside the canvas rows under the hub line."""
    found = find_view(lines)
    if found is None:
        return False
    rows = canvas_rows(lines, found)
    if rows is None or rows[1] - rows[0] < 8:
        return False
    return any(lines[r].find("◉", found[1] + 2) >= 0 for r in range(rows[0], rows[1] + 1))


def three_d_on(lines):
    """True when the canvas shows the 3D view: `@` on it and no braille (spinner glyphs aside)."""
    found = find_view(lines)
    rows = canvas_rows(lines, found) if found else None
    if rows is None:
        return False
    body = "".join(lines[r][found[1]:] for r in range(rows[0], rows[1] + 1))
    return "@" in body and not any(BRAILLE.match(ch) and ch not in SPIN for ch in body)


def open_panel(term, args):
    """Type /graph, wait for its row in the command list, then Enter, and wait for the view. Without the row the
    input is cleared (ctrl+u) and nothing is submitted, so /graph never goes to the session as a message."""
    for ch in "/graph":
        term.write(ch.encode())
        term.pump(0.12)
    if not term.wait_for(lambda ls: any(GRAPH_ITEM.search(l) for l in ls), args.wait):
        term.write(b"\x15")
        term.pump(0.5)
        return False
    term.write(b"\r")
    return term.wait_for(view_shown, args.wait)


def drive_g(term, args):
    """-> (started, steps): steps = [(label, lines, orbit_on)] for the capture before `g`, after `g`, after `g` again."""
    started = app_started(term)
    open_panel(term, args)
    for key in args.pre:
        if key == "R":
            col = find_view(term.lines())[1] if find_view(term.lines()) else None
            term.write(b"q")
            term.wait_for(lambda ls: find_view(ls) is None or find_view(ls)[1] != col, CEILING / 2)
            open_panel(term, args)
        else:
            term.write(key.encode())
        term.pump(1.5)
    term.wait_for(lambda ls: not any(TOAST.search(l) for l in ls), args.settle)
    term.pump(2.0)
    steps = [("before g", term.lines())]
    for label in ("after g", "after g again"):
        was = orbit_on(term.lines())
        term.write(b"g")
        # g switches between the list and the orbit: wait for the switch (a FAIL below if it never comes)
        term.wait_for(lambda ls: find_view(ls) is not None and orbit_on(ls) != was, args.wait)
        term.pump(0.5)
        steps.append((label, term.lines()))
    return started, [(label, lines, orbit_on(lines)) for label, lines in steps]


def screen_colours(screen):
    """-> {row: set of foreground colours (pyte's hex without '#') of the non-blank cells of that row}."""
    out = {}
    for r in range(screen.lines):
        line = screen.buffer[r]
        out[r] = {line[x].fg for x in range(screen.columns) if line[x].data.strip()}
    return out


def canvas_rows(lines, found):
    """-> (first, last) rows of the orbit's canvas: below the hub line and its "needs you" lines, above the
    instruments (the context chart or the spend bars), or None."""
    row0, col0, _ = found
    first = row0 + 1
    while first < len(lines) and re.match(r"[!↩] ", lines[first][col0:]):
        first += 1
    last = first
    for r in range(first, len(lines)):
        tail = lines[r][col0:]
        if CHART.search(tail) or re.search(r"━{3,}", tail):
            break
        last = r
    return (first, last) if last > first else None


def hub_ring(lines, found, rows):
    """-> (row, col) of the hub's glyph inside the canvas with braille dots round it (the ball: its rim and shaded dots;
    it was a ring with blank cells either side), or None."""
    _, col0, _ = found
    for r in range(rows[0], rows[1] + 1):
        c = lines[r].find("◉", col0 + 2)
        if c < 0:
            continue
        near = sum(1 for rr in range(max(0, r - 3), min(len(lines), r + 4))
                   for ch in lines[rr][max(0, c - 9):c + 10] if BRAILLE.match(ch) and ch not in SPIN)
        if near >= 6:
            return r, c
    return None


def _hue_sat(hexstr):
    try:
        n = int(hexstr, 16)
    except (TypeError, ValueError):
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{6}", str(hexstr)):
        return None
    r, g, b = ((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255
    mx, mn = max(r, g, b), min(r, g, b)
    d = mx - mn
    if d < 0.06:
        return None  # a grey
    if mx == r:
        h = ((g - b) / d) % 6
    elif mx == g:
        h = (b - r) / d + 2
    else:
        h = (r - g) / d + 4
    return (h * 60) % 360, d


def group_colours(colours, rows):
    """-> the group names whose colour is on the canvas rows: the colour of the dark or light table, full or mixed
    toward the muted grey (a resting agent, a far part), matched by hue within 10 degrees."""
    on = set()
    for r in range(rows[0], rows[1] + 1):
        on |= {str(fg).lower() for fg in colours.get(r, ())}
    hues = {g: [_hue_sat(h)[0] for h in hexes] for g, hexes in GROUP_HEX.items()}
    found = set()
    for fg in on:
        hs = _hue_sat(fg)
        if hs is None:
            continue
        best = min(((min(abs(hs[0] - h), 360 - abs(hs[0] - h)), g) for g, hl in hues.items() for h in hl))
        if best[0] <= 10:
            found.add(best[1])
    return sorted(found)


def green_colours(colours, rows):
    """-> the colours on the canvas rows with a hue between 80 and 190 degrees (green or teal: never wanted)."""
    out = set()
    for r in range(rows[0], rows[1] + 1):
        for fg in colours.get(r, ()):
            hs = _hue_sat(str(fg).lower())
            if hs is not None and 80 <= hs[0] <= 190:
                out.add(str(fg))
    return sorted(out)


def names_report(lines, found, rows, names, centre):
    """-> (seen, left_rows, right_rows): the agent names (word-bounded, right of the view's column) on the canvas
    rows, and the rows they are on left and right of `centre`."""
    _, col0, _ = found
    left, right, seen = set(), set(), []
    for name in names:
        pattern = re.compile(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])")
        for r in range(rows[0], rows[1] + 1):
            m = next((m for m in pattern.finditer(lines[r]) if m.start() >= col0), None)
            if m is None:
                continue
            seen.append(name)
            if m.end() <= centre:
                left.add(r)
            elif m.start() >= centre:
                right.add(r)
            break
    return seen, left, right


def scene_report(lines, names, colours=None):
    """-> (problems, facts) for the 2D orbit on the screen: the hub ring, the agent names on both sides of it, the
    group colours, and the context chart."""
    found = find_view(lines)
    problems, facts = [], []
    if found is None:
        return ["no hub line"], facts
    rows = canvas_rows(lines, found)
    if rows is None:
        return ["no canvas rows"], facts
    hub = hub_ring(lines, found, rows)
    if hub is None:
        return ["no hub ring"], facts
    facts.append(f"canvas rows {rows[0]}-{rows[1]}; hub ring round row {hub[0]}, column {hub[1]}")
    seen, left, right = names_report(lines, found, rows, names, hub[1])
    facts.append(f"{len(seen)} names on {len(left | right)} rows: {len(left)} rows left of the hub, {len(right)} right")
    if len(seen) < 8:
        problems.append(f"only {len(seen)} agent names: {seen}")
    if len(left | right) < 6:
        problems.append(f"names on only {len(left | right)} rows")
    if not left or not right:
        problems.append("names not on both sides of the hub")
    if colours is not None:
        groups = group_colours(colours, rows)
        facts.append(f"group colours on the canvas: {', '.join(groups) or 'none'}")
        if len(groups) < 3:
            problems.append(f"only {len(groups)} group colours")
        green = green_colours(colours, rows)
        if green:
            problems.append(f"green or teal on the canvas: {green}")
    if not any(CHART.search(l[found[1]:]) for l in lines[rows[1]:]):
        problems.append("no context chart under the canvas")
    else:
        facts.append("context chart present")
    return problems, facts


def scene3d_report(lines, later, names, colours, demo):
    """-> (problems, facts) for the 3D orbit: names, `@`, `0` and `1` inside the canvas rows and no braille there
    (spinner glyphs aside), the group colours, and a later capture that differs."""
    found = find_view(lines)
    problems, facts = [], []
    if found is None:
        return ["3D: no hub line"], facts
    rows = canvas_rows(lines, found)
    if rows is None:
        return ["3D: no canvas rows"], facts
    col0 = found[1]
    body = "".join(lines[r][col0:] for r in range(rows[0], rows[1] + 1))
    chars = {ch: body.count(ch) for ch in "@01:.·"}
    facts.append(f"3D canvas rows {rows[0]}-{rows[1]}: " + ", ".join(f"{ch!r} x{n}" for ch, n in chars.items()))
    for ch in "@01":
        if not chars[ch]:
            problems.append(f"3D: no {ch!r} on the canvas")
    braille = [ch for ch in body if BRAILLE.match(ch) and ch not in SPIN]
    if braille:
        problems.append(f"3D: {len(braille)} braille cells on the canvas (still 2D?)")
    if demo:
        seen, left, right = names_report(lines, found, rows, names, col0 + (len(lines[rows[0]]) - col0) // 2)
        facts.append(f"3D: {len(seen)} names on {len(left | right)} rows")
        if len(seen) < 8:
            problems.append(f"3D: only {len(seen)} agent names: {seen}")
        groups = group_colours(colours, rows)
        facts.append(f"3D: group colours on the canvas: {', '.join(groups) or 'none'}")
        if len(groups) < 3:
            problems.append(f"3D: only {len(groups)} group colours")
        green = green_colours(colours, rows)
        if green:
            problems.append(f"3D: green or teal on the canvas: {green}")
    differs = lines != later
    facts.append(f"3D: two captures 1 s apart {'differ' if differs else 'are the same'}"
                 + (f" ({sum(a != b for a, b in zip(lines, later))} rows changed)" if differs else ""))
    if not differs:
        problems.append("3D: the screen did not change in 1 s")
    return problems, facts


def report_g(label, args, started, how, steps):
    print(f"===== {label} ({args.binary}, started={started}, quit={how}) panel sizes the plugin measured:")
    sizes = []
    try:
        sizes = [l.split("panel size:")[1].strip() for l in open(DEBUG_LOG).read().splitlines() if "panel size:" in l]
    except OSError:
        pass
    print("   " + (", ".join(sizes) or "none logged"))
    for name, lines, orbit in steps:
        found = find_view(lines)
        print(f"----- {name}: {'the orbit' if orbit else ('the list' if found else 'NO VIEW')} (view column {found[1] if found else '-'})")
        for i, line in enumerate(lines):
            print(f"{i:2d}|{line}")
    kinds = [orbit for _, _, orbit in steps]
    if any(find_view(lines) is None for _, lines, _ in steps):
        print(f"===== {label}: FAIL, no view on screen in some capture")
        return 1
    shown = ["orbit" if k else "list" for k in kinds]
    if kinds[1] == kinds[0] or kinds[2] != kinds[0]:
        print(f"===== {label}: FAIL, g did not switch the view and back: {' -> '.join(shown)}; panel {', '.join(sizes) or '?'}")
        return 1
    if "f" not in args.pre and len(set(sizes)) > 1:
        # g never makes the panel full screen (only the user's `f` does): the panel was laid out at a second size
        print(f"===== {label}: FAIL, g changed the panel's size, so it went full screen: {', '.join(sizes)}")
        return 1
    print(f"===== {label}: PASS, g switched the view and back: {' -> '.join(shown)}; panel {', '.join(sizes) or '?'}")
    return 0


QUITTING = False


def _exit_on_signal(signum, _frame):
    if QUITTING:
        return  # already stopping the app: let that finish
    raise SystemExit(128 + signum)  # runs the `finally` that stops the app


def main():
    for name in ("SIGTERM", "SIGHUP"):  # Windows has no SIGHUP
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _exit_on_signal)
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", default="160x45")
    parser.add_argument("--binary", default=os.environ.get("OPENCODE_CLI", DEFAULT_BINARY))
    parser.add_argument("--session", default=os.environ.get("AGENTGRAPH_REAL_SESSION"))
    parser.add_argument("--no-graph", action="store_true", help="do not type /graph; report the sidebar only")
    parser.add_argument("--wait", type=float, default=CEILING, help="most seconds to wait for each view change (the view after /graph, f, d, g)")
    parser.add_argument("--keep-open", type=float, default=0.0, help="extra seconds before the capture")
    parser.add_argument("--settle", type=float, default=CEILING, help="most seconds to wait for toasts to go away")
    parser.add_argument("--demo", action="store_true", help="AGENTGRAPH_TUI_DEMO=1: the moving demo; require the orbit")
    parser.add_argument("--fullscreen", action="store_true", help="press f (full screen) once the view shows")
    parser.add_argument("--expect-scene", action="store_true", help="require the orbit (the hub ring) without --demo")
    parser.add_argument("--gcheck", action="store_true", help="press g twice and require the view to switch and come back")
    parser.add_argument("--pre", default="", help="keys before --gcheck's first capture: f, d, g, R (close and reopen)")
    parser.add_argument("--view3d", action="store_true", help="press d after the 2D captures and check the 3D view")
    parser.add_argument("--dialog", action="store_true", help="with --demo: s, then down and Enter in the confirm; nothing leaks")
    parser.add_argument("--open", action="store_true", help="without --demo: Enter on the hub row opens its session")
    parser.add_argument("--paths", action="store_true", help="the demo at rest with sample paths: r, the chooser, the goal prompt, Escape; nothing sent")
    parser.add_argument("--side", action="store_true", help="with --demo: AGENTGRAPH_TUI_DEMO=side, the demo with the side chat's row and moon, the spend limit, rate and split, the preset name")
    parser.add_argument("--chat", action="store_true", help="with --demo: the chat of the selected agent (open it with c where it does not fit), the orchestrator's transcript")
    parser.add_argument("--live-paths", action="store_true", help="a scratch orchestration folder with two named paths: the live chooser and flow prompt; nothing sent")
    parser.add_argument("--orbit", action="store_true", help="with --demo --fullscreen --view3d: the arrow keys, 0, a click and a drag on the 3D orbit")
    parser.add_argument("--plugin", default="", help="load only the terminal plugin at this folder (a scratch XDG_CONFIG_HOME)")
    parser.add_argument("--selftest", action="store_true", help="check list_report on built-in screens and exit")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if pty is None:
        print("real-host.py: driving the real app needs a Unix terminal (pty); only --selftest runs on this system", file=sys.stderr)
        return 2
    why = refuse(args.demo, args.dialog, any(a == "--session" or a.startswith("--session=") for a in sys.argv[1:]))
    if not why and (args.paths or args.live_paths) and (args.paths and args.live_paths or args.demo or args.dialog or args.open
                                                         or args.gcheck or args.fullscreen or args.view3d):
        why = "--paths and --live-paths run alone (with --size, --binary, --wait, --settle, --keep-open)"
    if not why and args.live_paths and any(a == "--session" or a.startswith("--session=") for a in sys.argv[1:]):
        why = "--live-paths types a goal: only in this script's throwaway session, not with --session"
    if not why and args.chat and (not args.demo or args.dialog or args.open or args.paths or args.live_paths or args.gcheck or args.fullscreen or args.view3d or args.orbit):
        why = "--chat needs --demo and runs without --dialog, --open, --paths, --live-paths, --gcheck, --fullscreen, --view3d or --orbit"
    if not why and args.side and (not args.demo or args.chat):
        why = "--side needs --demo and runs without --chat (the chat run presses down twice and expects the designer; the side chat's row is in the way)"
    if not why and args.orbit and not (args.demo and args.fullscreen and args.view3d):
        why = "--orbit needs --demo --fullscreen --view3d"
    if not why and args.plugin and not os.path.isfile(os.path.join(args.plugin, "src", "tui.js")):
        why = "--plugin: no src/tui.js in " + args.plugin
    if why:
        print("real-host.py: " + why, file=sys.stderr)
        return 2
    if pyte is None:
        print("pyte is missing: pip install --target <folder> pyte, then set PYTEPATH=<folder>", file=sys.stderr)
        return 2
    global DEMO, DEBUG_LOG
    DEMO = "paths" if args.paths else "side" if args.demo and args.side else "1" if args.demo else ""
    if args.gcheck and not DEBUG_LOG:
        DEBUG_LOG = os.path.join(SCRATCH, "gcheck-debug.log")
    if (args.dialog or args.open or args.paths or args.live_paths) and not DEBUG_LOG:
        DEBUG_LOG = os.path.join(SCRATCH, "actions-debug.log")
    if args.orbit and not DEBUG_LOG:
        DEBUG_LOG = os.path.join(SCRATCH, "orbit-debug.log")
    if args.plugin:
        config = os.path.join(SCRATCH, "xdg-plugin")
        os.makedirs(os.path.join(config, "opencode"), exist_ok=True)
        with open(os.path.join(config, "opencode", "cli.json"), "w") as out:
            json.dump({"plugins": [os.path.abspath(args.plugin)]}, out)
        EXTRA_ENV["XDG_CONFIG_HOME"] = config
    args.list_mode = args.demo and not args.fullscreen and not args.expect_scene and not args.view3d
    cols, rows = (int(n) for n in args.size.lower().split("x"))
    proj = os.path.join(SCRATCH, "proj")
    os.makedirs(SCRATCH, exist_ok=True)
    session = args.session or ensure_session(args.binary, proj)
    args.session_id = session
    if args.live_paths:
        args.live_root = scratch_orchestration()
        EXTRA_ENV.update({"AGENTGRAPH_TUI_HOME": args.live_root, "AGENTGRAPH_TUI_NOSEND": "1"})

    args.orbit_result = None
    label = f"{cols}x{rows}{'-nograph' if args.no_graph else ''}{'-orbit' if args.orbit else ''}{'-demo' if args.demo else ''}{'-side' if args.side else ''}{'-dialog' if args.dialog else ''}{'-open' if args.open else ''}{'-paths' if args.paths else ''}{'-chat' if args.chat else ''}{'-livepaths' if args.live_paths else ''}{'-full' if args.fullscreen else ''}{'-3d' if args.view3d else ''}{'-g' + args.pre if args.gcheck else ''}"
    term = None
    try:
        term = Term([args.binary, "--standalone", "-s", session, proj], proj, cols, rows, os.path.join(SCRATCH, f"raw-{label}.bin"))
        if DEBUG_LOG and (args.gcheck or args.dialog or args.open or args.paths or args.live_paths or args.orbit):
            open(DEBUG_LOG, "w").close()
        term.start()
        if args.gcheck:
            started, steps = drive_g(term, args)
            how = term.quit()
            return report_g(label, args, started, how, steps)
        lines, colours, after_q, started, second, seen2d, view3d = drive(term, args)
        how = term.quit()
    finally:
        if term is not None:
            term.quit()  # every exit path: an exception, ctrl+c, SIGTERM or SIGHUP to this script

    print(f"===== {label} screen ({args.binary}, session {session}, started={started}, quit={how})")
    for i, line in enumerate(lines):
        print(f"{i:2d}|{line}")
    found = find_view(lines)
    if found is None:
        print(f"===== {label}: no `{HUB}` line on screen")
        return 1
    row, col, view = found
    print(f"===== {label}: view at row {row}, column {col}")
    for line in view:
        print(f"   |{line}")
    for text, fg in colours:
        print(f"   colour {fg:>8}  {text}")
    rest = [v for v in view[1:] if v.strip()]
    if not rest:
        print(f"===== {label}: FAIL, the hub line has no view lines under it")
        return 1
    rows_c = canvas_rows(lines, found)
    scene_on = rows_c is not None and hub_ring(lines, found, rows_c) is not None
    print(f"===== {label}: {'the orbit' if scene_on else 'the list'} is on screen")
    differs = lines != second
    print(f"===== {label}: two captures 1 s apart {'differ' if differs else 'are the same'}"
          + (f" ({sum(a != b for a, b in zip(lines, second))} rows changed)" if differs else ""))
    if args.list_mode:
        rows_l = list_rows(lines, found)
        print(f"===== {label}: the list's rows")
        for line in rows_l:
            print(f"   |{line}")
        problems, facts = list_report(rows_l)
        for fact in facts:
            print(f"   list: {fact}")
        if scene_on:
            problems.append("the orbit is on screen after `g`: the list was expected")
        if not differs:
            problems.append("the screen did not change in 1 s (the spinners)")
        if problems:
            print(f"===== {label}: FAIL, list: " + "; ".join(problems))
            return 1
    elif args.demo or args.expect_scene:
        problems, facts = scene_report(lines, DEMO_NAMES if args.demo else DEMO_NAMES + ["orchestrator"], seen2d if args.demo else None)
        for fact in facts:
            print(f"   scene: {fact}")
        if not args.demo and scene_on:
            problems = []  # a live session: its own agents, so only the hub ring is required
        if args.demo and not differs:
            problems.append("the screen did not change in 1 s")
        if args.view3d:
            if view3d is None:
                problems.append("3D: no capture (no view on screen to press d in)")
            else:
                first3, later3, seen3d = view3d
                print(f"===== {label}: 3D screen, 1 s after `d`")
                for i, line in enumerate(first3):
                    print(f"{i:2d}|{line}")
                p3, f3 = scene3d_report(first3, later3, DEMO_NAMES, seen3d, args.demo)
                for fact in f3:
                    print(f"   scene: {fact}")
                problems += p3
        if args.orbit:
            if args.orbit_result is None:
                problems.append("orbit: not driven (no 3D view)")
            else:
                p_o, f_o, shots = args.orbit_result
                for name, shot in shots:
                    print(f"===== {label}: orbit: screen {name}")
                    for i, line in enumerate(shot):
                        print(f"{i:2d}|{line}")
                for fact in f_o:
                    print(f"   {fact}")
                problems += p_o
                if not p_o:
                    print(f"===== {label}: orbit: PASS")
        if problems:
            print(f"===== {label}: FAIL, scene: " + "; ".join(problems))
            return 1
    extra = getattr(args, "extra", None)
    if args.dialog or args.open or args.paths or args.live_paths or args.chat:
        if extra is None:
            print(f"===== {label}: FAIL, {'dialog' if args.dialog else 'paths' if args.paths or args.live_paths else 'chat' if args.chat else 'open'}: no view to press keys in")
            return 1
        kind, p_extra, f_extra, screens = extra
        for name, shot in screens:
            print(f"===== {label}: {kind}: screen {name}")
            for i, line in enumerate(shot):
                print(f"{i:2d}|{line}")
        for fact in f_extra:
            print(f"   {kind}: {fact}")
        if p_extra:
            print(f"===== {label}: FAIL, " + "; ".join(p_extra))
            return 1
        print(f"===== {label}: {kind}: PASS")
    print(f"===== {label}: PASS, hub line and {len(rest)} more view lines")
    if after_q is not None:
        moved = find_view(after_q)
        if moved is None:
            print(f"===== {label}: after `q`: the panel closed, no view on screen")
        elif moved[1] != col:
            print(f"===== {label}: after `q`: the panel closed; the view is now in the sidebar at row {moved[0]}, column {moved[1]}")
            for line in moved[2]:
                print(f"   |{line}")
        else:
            print(f"===== {label}: after `q`: the view is still at column {col} (the panel did not close)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
