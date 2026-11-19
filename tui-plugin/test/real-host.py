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
$TMPDIR_OPENCODE/tui-real (default /private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode/tui-real).

The session: --session, else $AGENTGRAPH_REAL_SESSION, else <scratch>/session.txt; when there is none it creates a
throwaway one with `opencode run --standalone --model anthropic/claude-haiku-5-5 "Do not call any tool. Reply with
exactly: OK"` in <scratch>/proj (about $0.004) and saves its id in session.txt.

AGENTGRAPH_TUI_DEBUG=<file> is passed to the app: the plugin appends every error it swallows to that file.

The orbit (the full-panel scene):
    --demo          start the app with AGENTGRAPH_TUI_DEMO=1 (the plugin draws the saved demo document, moving,
                    without Python), and require the 2D scene: the hub ring (the hub's glyph inside the canvas with
                    braille dots round it and open cells beside it), at least 8 agent names over at least 6 rows on
                    both sides of the hub, at least 3 group colours, the context chart, and two captures 1 s apart
                    that differ. Without --demo, AGENTGRAPH_TUI_DEMO is removed from the app's environment.
    --fullscreen    press `f` once the view shows (the panel's full-screen key), before the captures.
    --expect-scene  require the scene without --demo (the hub ring).
    --view3d        after the 2D captures press `d` (the orbit's 2D/3D key), wait 1 s and capture the 3D view, then
                    again 1 s later. Requires (with --demo) at least 8 agent names, `@`, `0` and `1` inside the canvas
                    rows and no braille there, at least 3 group colours, and two 3D captures that differ. With
                    --expect-scene instead of --demo: the depth characters and two captures that differ. Checked
                    only with --demo or --expect-scene.
"""
import argparse
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import time

DEFAULT_BINARY = "/Applications/OpenCode.app/Contents/Resources/opencode-cli"
SCRATCH = os.path.join(os.environ.get("TMPDIR_OPENCODE", "/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode"), "tui-real")
sys.path.insert(0, os.environ.get("PYTEPATH", os.path.join(SCRATCH, "pylib")))
try:
    import pyte  # noqa: E402
except ImportError:
    print("pyte is missing: pip install --target <folder> pyte, then set PYTEPATH=<folder>", file=sys.stderr)
    sys.exit(2)

HUB = "◉ orchestrator"
TOAST = re.compile(r"Plugin failed|Open plugins|run opencode plugin update|Run /plugins")
DEBUG_LOG = os.environ.get("AGENTGRAPH_TUI_DEBUG", "")
DEMO = False  # set from --demo in main()
BRAILLE = re.compile(r"[\u2801-\u28ff]")
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# the group colours of src/scene.js (GROUP_COLORS), as pyte reports a 24-bit foreground
GROUP_HEX = {"council": ("b39ddb", "6a4fb3"), "decide": ("7fb2ff", "2f6fd0"), "build": ("e8a07c", "ac563d"),
             "verify": ("f28fb5", "b8336a"), "understand": ("e6c15a", "8a6d00")}
CHART = re.compile(r"[▁▂▃▄▅▆▇█]{5}")
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
            if DEMO:
                os.environ["AGENTGRAPH_TUI_DEMO"] = "1"
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


def drive(term, args):
    started = term.wait_for(lambda ls: any("ctrl+p" in l or "commands" in l for l in ls), 40)
    term.pump(4)  # let plugins load and the first sidebar snapshot arrive
    if not args.no_graph:
        for ch in "/graph":
            term.write(ch.encode())
            term.pump(0.12)
        term.pump(0.8)
        term.write(b"\r")
        term.wait_for(lambda ls: find_view(ls) is not None and len([v for v in find_view(ls)[2] if v]) >= 2, args.wait)
    if args.fullscreen and find_view(term.lines()) is not None:
        term.write(b"f")
        term.pump(1.5)
    # toasts (plugin update notices, load warnings) draw over the right side for a few seconds: wait them out
    term.wait_for(lambda ls: not any(TOAST.search(l) for l in ls), args.settle)
    term.pump(args.keep_open)
    if args.demo or args.expect_scene:
        term.pump(2.0)  # the orbit never stands still: no waiting for a quiet screen
    else:
        # the app redraws only the cells that change: wait until two captures 1.5 s apart are the same
        previous, end = None, time.time() + 10
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
        term.pump(1.0)
        first3 = term.lines()
        seen3d = screen_colours(term.screen)
        term.pump(1.0)
        view3d = (first3, term.lines(), seen3d)
    after_q = None
    if not args.no_graph and find_view(lines) is not None:
        # `q` is this plugin's close key while its panel has the keyboard
        term.write(b"q")
        term.pump(2.0)
        after_q = term.lines()
    return lines, colours, after_q, started, second, seen2d, view3d


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
    """-> (row, col) of the hub's glyph inside the canvas with braille dots round it (the ring) and blank cells
    either side of it (open inside), or None."""
    _, col0, _ = found
    for r in range(rows[0], rows[1] + 1):
        c = lines[r].find("◉", col0 + 2)
        if c < 0:
            continue
        near = sum(1 for rr in range(max(0, r - 3), min(len(lines), r + 4))
                   for ch in lines[rr][max(0, c - 9):c + 10] if BRAILLE.match(ch) and ch not in SPIN)
        open_inside = lines[r][c - 2:c] == "  " and lines[r][c + 1:c + 3] == "  "
        if near >= 6 and open_inside:
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
    facts.append(f"canvas rows {rows[0]}-{rows[1]}; hub ring round row {hub[0]}, column {hub[1]}, open inside")
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


QUITTING = False


def _exit_on_signal(signum, _frame):
    if QUITTING:
        return  # already stopping the app: let that finish
    raise SystemExit(128 + signum)  # runs the `finally` that stops the app


def main():
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _exit_on_signal)
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", default="160x45")
    parser.add_argument("--binary", default=os.environ.get("OPENCODE_CLI", DEFAULT_BINARY))
    parser.add_argument("--session", default=os.environ.get("AGENTGRAPH_REAL_SESSION"))
    parser.add_argument("--no-graph", action="store_true", help="do not type /graph; report the sidebar only")
    parser.add_argument("--wait", type=float, default=12.0, help="seconds to wait for the view after /graph")
    parser.add_argument("--keep-open", type=float, default=0.0, help="extra seconds before the capture")
    parser.add_argument("--settle", type=float, default=12.0, help="seconds to wait for toasts to go away")
    parser.add_argument("--demo", action="store_true", help="AGENTGRAPH_TUI_DEMO=1: the moving demo; require the orbit")
    parser.add_argument("--fullscreen", action="store_true", help="press f (full screen) once the view shows")
    parser.add_argument("--expect-scene", action="store_true", help="require the orbit (the hub ring) without --demo")
    parser.add_argument("--view3d", action="store_true", help="press d after the 2D captures and check the 3D view")
    args = parser.parse_args()
    global DEMO
    DEMO = args.demo
    cols, rows = (int(n) for n in args.size.lower().split("x"))
    proj = os.path.join(SCRATCH, "proj")
    os.makedirs(SCRATCH, exist_ok=True)
    session = args.session or ensure_session(args.binary, proj)

    label = f"{cols}x{rows}{'-nograph' if args.no_graph else ''}{'-demo' if args.demo else ''}{'-full' if args.fullscreen else ''}{'-3d' if args.view3d else ''}"
    term = None
    try:
        term = Term([args.binary, "--standalone", "-s", session, proj], proj, cols, rows, os.path.join(SCRATCH, f"raw-{label}.bin"))
        term.start()
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
    if args.demo or args.expect_scene:
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
        if problems:
            print(f"===== {label}: FAIL, scene: " + "; ".join(problems))
            return 1
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
