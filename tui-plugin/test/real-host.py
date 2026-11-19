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
    # toasts (plugin update notices, load warnings) draw over the right side for a few seconds: wait them out
    term.wait_for(lambda ls: not any(TOAST.search(l) for l in ls), args.settle)
    term.pump(args.keep_open)
    # the app redraws only the cells that change: wait until two captures 1.5 s apart are the same
    previous, end = None, time.time() + 10
    while time.time() < end:
        term.pump(1.5)
        if term.lines() == previous:
            break
        previous = term.lines()
    lines = term.lines()
    colours = colour_runs(term.screen, find_view(lines))
    after_q = None
    if not args.no_graph and find_view(lines) is not None:
        # `q` is this plugin's close key while its panel has the keyboard
        term.write(b"q")
        term.pump(2.0)
        after_q = term.lines()
    return lines, colours, after_q, started


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
    args = parser.parse_args()
    cols, rows = (int(n) for n in args.size.lower().split("x"))
    proj = os.path.join(SCRATCH, "proj")
    os.makedirs(SCRATCH, exist_ok=True)
    session = args.session or ensure_session(args.binary, proj)

    label = f"{cols}x{rows}{'-nograph' if args.no_graph else ''}"
    term = None
    try:
        term = Term([args.binary, "--standalone", "-s", session, proj], proj, cols, rows, os.path.join(SCRATCH, f"raw-{label}.bin"))
        term.start()
        lines, colours, after_q, started = drive(term, args)
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
