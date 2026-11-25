"""The command line: arguments, one-frame output, and live mode in a real
pseudo-terminal (exit paths, terminal restoration, suspend, resize, keys).

Live runs go through a tiny job-control "shell" (MINI_SHELL) so the viewer
sits in its own foreground process group of a pty that is its controlling
terminal, as it would under a real shell: Ctrl-C written to the pty becomes
SIGINT, and SIGTSTP really stops the process.
"""
import contextlib
import dataclasses
import errno
import io
import os
import re
import select
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

import agentgraph.__main__ as cli
from agentgraph.demo import demo_snapshot
from agentgraph.viewstate import KEY_HELP, ViewState
from tests.winskip import posix_only

try:  # the pseudo-terminal tests are POSIX; on Windows the module still loads and those classes skip (osproc/winconsole tests cover the console)
    import fcntl
    import pty
    import termios
except ImportError:  # pragma: no cover - Windows
    fcntl = pty = termios = None

PROJECT = Path(__file__).resolve().parent.parent
AT = "1795000000"
TIMEOUT = 60.0  # a guard against a hang, never a verdict on speed: every wait below ends on an event

ENTER = b"\x1b[?1049h"
HIDE = b"\x1b[?25l"
LEAVE = b"\x1b[?1049l"
SHOW = b"\x1b[?25h"
CLEAR = b"\x1b[H\x1b[2J"
EL = b"\x1b[K"
ROW = re.compile(rb"\x1b\[(\d+);1H([^\x1b]*)")
LFLAGS = termios.ECHO | termios.ICANON | termios.ISIG if termios else 0

# An error message with ESC, DEL, a newline, a tab, an emoji and a combining mark,
# and what the sanitiser must make of it.
NASTY = "boom \x1b[31mred\x7f\nnext\tline 🙂 e\u0301 end"
CLEAN = "boom ?[31mred? next line ? e? end"


def _printable_ascii(text: str) -> bool:
    return all(" " <= ch <= "~" or ch == "\n" for ch in text)

# argv: STATUS_FD CTTY_FD COMMAND...  Reports "pid N", "stopped SIG",
# "continued" and "exit CODE" on STATUS_FD.
MINI_SHELL = r'''
import fcntl, os, signal, subprocess, sys, termios
status, ctty = int(sys.argv[1]), int(sys.argv[2])
fcntl.ioctl(ctty, termios.TIOCSCTTY, 0)  # a session leader adopting the pty, like a login shell

def report(text):
    os.write(status, (text + "\n").encode())

def foreground():  # in the child: its own process group, in front, default signals
    os.setpgid(0, 0)
    signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    os.tcsetpgrp(ctty, os.getpid())
    for sig in (signal.SIGTTOU, signal.SIGTTIN, signal.SIGTSTP, signal.SIGINT,
                signal.SIGQUIT, signal.SIGHUP, signal.SIGTERM):
        signal.signal(sig, signal.SIG_DFL)

child = subprocess.Popen(sys.argv[3:], preexec_fn=foreground)
report(f"pid {child.pid}")
while True:
    _, st = os.waitpid(child.pid, os.WUNTRACED | os.WCONTINUED)
    if os.WIFSTOPPED(st):
        report(f"stopped {os.WSTOPSIG(st)}")
    elif os.WIFCONTINUED(st):
        report("continued")
    else:
        report(f"exit {os.waitstatus_to_exitcode(st)}")
        break
'''


def _set_size(fd, cols, rows):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


CSI = re.compile(rb"\x1b\[[0-9;?]*[ -/]*[@-~]")
ROW_START = re.compile(rb"\x1b\[\d+;1H")


def _row_done(data: bytes, cols: int) -> bool:
    """Whether data ends where a row write ends: the last row written is followed by its erase, or fills the line
    (the viewer writes no erase after a row that fills it). A cut escape or character is not an end."""
    starts = list(ROW_START.finditer(data))
    if not starts:
        return False
    tail = data[starts[-1].end():]
    if tail.endswith(EL):
        return True
    text = CSI.sub(b"", tail)
    if b"\x1b" in text:
        return False
    try:
        return len(text.decode("utf-8")) >= cols
    except UnicodeDecodeError:
        return False


def _full_frame(data: bytes, rows: int, cols: int) -> bool:
    """A full repaint whose last row has been written to its end, at the end of data: the viewer has finished
    writing it (the event the tests wait on, never a length of time)."""
    start = data.rfind(CLEAR)
    return start >= 0 and (b"\x1b[%d;1H" % rows) in data[start:] and _row_done(data[start:], cols)


def _last_frame_rows(data: bytes):
    """{row number: text} of the last full repaint in plain output."""
    return {int(m.group(1)): m.group(2) for m in ROW.finditer(data[data.rfind(CLEAR):])}


def _screen(data: bytes, rows: int) -> list:
    """What a plain-mode viewer has on screen: the last full repaint with every
    later row write applied. Each written row ends at an erase or at the next
    write, so the text of a row is exactly what the terminal shows."""
    screen = [""] * rows
    for row, text in _last_frame_rows(data).items():
        if 1 <= row <= rows:
            screen[row - 1] = text.decode("ascii", "replace")
    return screen


class Live:
    """The real CLI in live mode on a pseudo-terminal."""

    def __init__(self, args=(), size=(80, 24), stdin="tty", separate_stdout=False):
        """stdin: "tty" (the same pty as stdout), "pty" (a pty of its own, which the
        test can hang up with close_stdin) or "null" (/dev/null)."""
        self.cols, self.rows = size
        self.master, self.slave = pty.openpty()  # the test keeps the slave open throughout
        _set_size(self.slave, *size)
        self.out_master, out_slave = self.master, self.slave
        if separate_stdout:  # stdout on a second pty that the test can take away
            self.out_master, out_slave = pty.openpty()
            _set_size(out_slave, *size)
        self.in_master, self.in_slave = self.master, self.slave  # where keys go, whose attributes count
        if stdin == "pty":
            self.in_master, self.in_slave = pty.openpty()
        self.before = termios.tcgetattr(self.in_slave)
        stdin_fd = self.in_slave if stdin in ("tty", "pty") else os.open(os.devnull, os.O_RDONLY)
        self.status_r, status_w = os.pipe()
        self.err = tempfile.TemporaryFile()
        self.out = bytearray()
        self.status = ""
        self.pid = None
        self.code = None
        self.shell = None
        # Stale COLUMNS/LINES on purpose: the viewer must ask the terminal itself.
        env = dict(os.environ, COLUMNS="33", LINES="11", TERM="xterm-256color")
        command = [sys.executable, "-c", MINI_SHELL, str(status_w), "0" if stdin == "tty" else "1",
                   sys.executable, "-m", "agentgraph", "--demo", "--at", AT, *args]
        try:
            self.shell = subprocess.Popen(command, stdin=stdin_fd, stdout=out_slave, stderr=self.err,
                                          cwd=PROJECT, env=env, start_new_session=True,
                                          pass_fds=(status_w,))
        finally:
            os.close(status_w)
            if stdin == "null":
                os.close(stdin_fd)
            if separate_stdout:
                os.close(out_slave)  # only the viewer (and its shell) hold it now
        try:
            self.wait_for(lambda: self.pid is not None, "the viewer to start")
        except BaseException:
            self.close()
            raise

    # reading -------------------------------------------------------------------------
    def pump(self, timeout):
        fds = [fd for fd in (self.out_master, self.status_r) if fd is not None]
        ready, _, _ = select.select(fds, [], [], timeout)
        for fd in ready:
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b""
            if fd == self.status_r:
                if not data:
                    os.close(self.status_r)
                    self.status_r = None
                    continue
                self.status += data.decode()
                for line in self.status.splitlines():
                    word, _, value = line.partition(" ")
                    if word == "pid":
                        self.pid = int(value)
                    elif word == "exit":
                        self.code = int(value)
            elif data:
                self.out += data
            elif fd != self.master:  # the separate stdout pty has no holders left
                self.close_stdout()

    def wait_for(self, predicate, what, timeout=TIMEOUT):
        deadline = time.monotonic() + timeout
        while not predicate():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"timed out waiting for {what}\n  output tail: {bytes(self.out[-400:])!r}"
                    f"\n  status: {self.status!r}\n  stderr: {self.stderr()!r}")
            self.pump(min(remaining, 0.1))

    def wait_frame(self, since=0):
        self.wait_for(lambda: _full_frame(bytes(self.out[since:]), self.rows, self.cols), "a full frame")

    def wait_exit(self):
        self.wait_for(lambda: self.code is not None, "the viewer to exit")
        return self.code

    def since(self, mark):
        return bytes(self.out[mark:])

    def attrs(self):
        return termios.tcgetattr(self.in_slave)

    def stderr(self):
        self.err.seek(0)
        return self.err.read().decode(errors="replace")

    # control -------------------------------------------------------------------------
    def key(self, data: bytes):
        os.write(self.in_master, data)

    def signal(self, sig):
        os.kill(self.pid, sig)

    def close_stdout(self):
        if self.out_master is not None and self.out_master != self.master:
            os.close(self.out_master)
            self.out_master = None

    def close_stdin(self):
        """Hang up the viewer's own input pty: its reads now return end of file.
        The test keeps its copy of the slave, so the attributes can still be read."""
        if self.in_master is not None and self.in_master != self.master:
            os.close(self.in_master)
            self.in_master = None

    def close(self):
        try:
            if self.code is None and self.pid is not None:
                for sig in (signal.SIGCONT, signal.SIGKILL):
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(self.pid, sig)
            if self.shell is not None:
                try:
                    self.shell.wait(timeout=TIMEOUT)
                except subprocess.TimeoutExpired:
                    self.shell.kill()
                    self.shell.wait()
        finally:
            self.close_stdout()
            self.close_stdin()
            for fd in {self.status_r, self.master, self.slave, self.in_slave} - {None}:
                with contextlib.suppress(OSError):
                    os.close(fd)
            self.status_r = None
            self.err.close()


class FrameComplete(unittest.TestCase):
    """The tests below act on the viewer once its frame is on screen. A frame whose last row is still being
    written is not on screen yet: the viewer is still writing it, so taking its output away then loses output
    (under load the test's read can end anywhere inside that row)."""

    def frame(self, rows, cols, color):
        parts = [CLEAR]
        for row in range(1, rows + 1):
            text = (b"x" * (cols if row == rows else 10)).decode()
            if color:
                text = "\x1b[0;38;2;230;230;230m" + text + "\x1b[0m"
            parts.append(f"\x1b[{row};1H{text}".encode() + (b"" if row == rows else EL))
        return b"".join(parts)

    def test_a_frame_cut_inside_its_last_row_is_not_full(self):
        for color in (False, True):
            whole = self.frame(24, 80, color)
            self.assertTrue(_full_frame(whole, 24, 80))
            last = whole.rfind(b"\x1b[24;1H")
            for cut in (last + len(b"\x1b[24;1H"), last + 30, len(whole) - 1 if not color else len(whole) - 2):
                with self.subTest(color=color, cut=cut - last):
                    self.assertFalse(_full_frame(whole[:cut], 24, 80))
        short = CLEAR + b"\x1b[1;1Hab" + EL + b"\x1b[2;1Hcd" + EL
        self.assertTrue(_full_frame(short, 2, 80))
        self.assertFalse(_full_frame(short[:-1], 2, 80))


@posix_only
class LiveTerminal(unittest.TestCase):
    def live(self, *args, **options) -> Live:
        run = Live(args, **options)
        self.addCleanup(run.close)
        return run

    def assertCbreak(self, run):
        lflag = run.attrs()[3]
        self.assertEqual(lflag & (termios.ECHO | termios.ICANON), 0, "not in cbreak mode")
        self.assertTrue(lflag & termios.ISIG, "Ctrl-C must still signal")

    def assertRestored(self, run):
        after, before = run.attrs(), run.before
        self.assertEqual(after[3] & LFLAGS, before[3] & LFLAGS, "ECHO/ICANON/ISIG not restored")
        # Everything else too, except PENDIN: the kernel sets that state bit
        # itself when a terminal switches back to canonical input.
        pendin = getattr(termios, "PENDIN", 0)
        self.assertEqual(after[:3] + [after[3] & ~pendin] + after[4:],
                         before[:3] + [before[3] & ~pendin] + before[4:])

    def assertLeftScreen(self, data: bytes):
        self.assertIn(LEAVE, data)
        self.assertIn(SHOW, data)
        self.assertGreater(data.rfind(LEAVE), data.rfind(ENTER), "the alternate screen is still active")

    def test_every_way_out_restores_the_terminal(self):
        ways = {
            "q": lambda run: run.key(b"q"),
            "Q": lambda run: run.key(b"Q"),
            "ctrl-c": lambda run: run.key(b"\x03"),
            "SIGTERM": lambda run: run.signal(signal.SIGTERM),
            "SIGHUP": lambda run: run.signal(signal.SIGHUP),
            "SIGQUIT": lambda run: run.signal(signal.SIGQUIT),
        }
        for name, leave in ways.items():
            with self.subTest(name):
                run = Live()
                try:
                    run.wait_frame()
                    self.assertIn(ENTER, run.out)
                    self.assertIn(HIDE, run.out)
                    self.assertCbreak(run)
                    leave(run)
                    self.assertEqual(run.wait_exit(), 0)
                    self.assertRestored(run)
                    self.assertLeftScreen(bytes(run.out))
                    self.assertEqual(run.stderr(), "")
                finally:
                    run.close()

    def test_terminal_restored_when_stdout_is_gone_before_exit(self):
        run = self.live(separate_stdout=True)
        run.wait_frame()
        self.assertCbreak(run)
        run.close_stdout()  # the reader of the viewer's output disappears
        run.key(b"q")
        self.assertEqual(run.wait_exit(), 0)
        self.assertRestored(run)
        self.assertEqual(run.stderr(), "")

    def test_lost_output_ends_the_viewer_and_restores_the_terminal(self):
        run = self.live(separate_stdout=True)
        run.wait_frame()
        run.close_stdout()
        run.signal(signal.SIGWINCH)  # forces a full repaint into the dead terminal
        self.assertEqual(run.wait_exit(), 1)
        self.assertRestored(run)
        self.assertIn("output lost", run.stderr())
        self.assertNotIn("Traceback", run.stderr())

    def test_lost_input_still_restores_screen_and_attributes(self):
        # stdin on a pty of its own, stdout (the controlling terminal) stays healthy
        run = self.live("--no-color", stdin="pty")
        run.wait_frame()
        self.assertCbreak(run)
        run.close_stdin()  # input hangs up: reads return end of file
        mark = len(run.out)
        run.signal(signal.SIGWINCH)  # a repaint shows the viewer is still running
        run.wait_frame(since=mark)
        run.signal(signal.SIGTERM)
        self.assertEqual(run.wait_exit(), 0)
        self.assertLeftScreen(bytes(run.out))
        self.assertRestored(run)  # the descriptor is still valid, so the attributes are back
        self.assertEqual(run.stderr(), "")

    def test_suspend_and_continue(self):
        run = self.live("--no-color")
        run.wait_frame()
        self.assertCbreak(run)
        mark = len(run.out)
        run.signal(signal.SIGTSTP)
        run.wait_for(lambda: f"stopped {int(signal.SIGTSTP)}" in run.status, "the viewer to stop")
        run.wait_for(lambda: LEAVE in run.since(mark) and SHOW in run.since(mark), "the screen to be left")
        self.assertRestored(run)
        self.assertLeftScreen(run.since(mark))

        mark = len(run.out)
        run.signal(signal.SIGCONT)
        run.wait_frame(since=mark)
        resumed = run.since(mark)
        self.assertIn(ENTER, resumed)
        self.assertIn(HIDE, resumed)
        self.assertLess(resumed.find(ENTER), resumed.rfind(CLEAR))
        self.assertEqual(max(_last_frame_rows(resumed)), run.rows)
        self.assertCbreak(run)

        run.key(b"q")
        self.assertEqual(run.wait_exit(), 0)
        self.assertRestored(run)
        self.assertLeftScreen(bytes(run.out))
        self.assertEqual(run.stderr(), "")

    def test_keys_step_through_focused_frames_and_rows_erase_only_their_tail(self):
        run = self.live("--no-color")

        def draw(state):
            """The frame and plain rows the viewer must show for this state (the real view)."""
            view = dataclasses.replace(demo_snapshot(float(AT)), now=float(AT))
            return cli._safe_frame(view, run.cols, run.rows, "demo", KEY_HELP, state, False)

        def expect(state):
            return draw(state)[1]

        def showing(rows):
            return lambda: _screen(bytes(run.out), run.rows) == rows

        # Page Down moves by the rows the focused panel shows (canvas.page), else height - 6.
        page = getattr(draw(ViewState(focus="loops"))[0], "page", None)
        page = page if isinstance(page, int) and page > 0 else run.rows - 6
        steps = [(None, ViewState()), (b"3", ViewState(focus="loops")),
                 (b" ", ViewState(focus="loops", scroll=page)), (b"b", ViewState(focus="loops")),
                 (b"j", ViewState(focus="loops", scroll=1)), (b"j", ViewState(focus="loops", scroll=2)),
                 (b"k", ViewState(focus="loops", scroll=1)), (b"0", ViewState())]
        previous = None
        for key, state in steps:  # one key at a time: each frame must be on screen before the next key
            rows = expect(state)
            self.assertNotEqual(rows, previous, f"{state} would not change the screen")
            if key is not None:
                run.key(key)
            run.wait_for(showing(rows), f"the frame for {state}")
            previous = rows
        run.key(b"q")
        self.assertEqual(run.wait_exit(), 0)

        out = bytes(run.out)
        rows = list(ROW.finditer(out))
        full = [match for match in rows if len(match.group(2)) == run.cols]
        self.assertTrue(full, "a focused panel has full-width rows")
        self.assertTrue([match for match in rows if len(match.group(2)) < run.cols])
        for match in rows:
            text, erased = match.group(2), out.startswith(EL, match.end())
            self.assertLessEqual(len(text), run.cols)
            if len(text) < run.cols:  # anything shorter than the line must clear the old tail
                self.assertTrue(erased, f"row {match.group(1).decode()} is not followed by ESC [ K: {text!r}")
            else:  # a row that fills the line overwrote every cell; an erase would wipe the last one
                self.assertFalse(erased, f"full-width row {match.group(1).decode()} followed by ESC [ K")

    def test_resize_repaints_at_the_real_size(self):
        run = self.live("--no-color")  # COLUMNS=33 and LINES=11 are exported
        run.wait_frame()
        first = _last_frame_rows(bytes(run.out))
        self.assertEqual(max(first), 24)
        self.assertTrue(33 < max(len(text) for text in first.values()) <= 80)
        mark = len(run.out)
        _set_size(run.master, 100, 30)
        run.signal(signal.SIGWINCH)
        run.wait_for(lambda: _full_frame(run.since(mark), 30, 100), "a full repaint at 100 x 30")
        rows = _last_frame_rows(run.since(mark))
        self.assertEqual(max(rows), 30)
        self.assertTrue(80 < max(len(text) for text in rows.values()) <= 100)
        self.assertNotIn(b"needs at least", run.out)
        run.key(b"q")
        self.assertEqual(run.wait_exit(), 0)

    def test_stdin_not_a_terminal(self):
        run = self.live("--no-color", stdin="null")
        run.wait_frame()
        mark = len(run.out)
        run.signal(signal.SIGWINCH)  # still refreshing: a forced repaint arrives
        run.wait_frame(since=mark)
        run.signal(signal.SIGTERM)
        self.assertEqual(run.wait_exit(), 0)
        self.assertLeftScreen(bytes(run.out))
        self.assertEqual(run.attrs(), run.before)  # never touched
        self.assertEqual(run.stderr(), "")


class FakeCanvas:
    def __init__(self, rows, last_line=None, page=None):
        self.rows = rows
        if last_line is not None:
            self.last_line = last_line
        if page is not None:
            self.page = page

    def rows_ansi(self, color=True):
        return list(self.rows)


class VirtualClock:
    """The loop's clock (`time.monotonic`, `time.sleep`) and its waits (`select.select`) on a clock that moves only
    while the loop waits for nothing: a wait whose descriptors are not ready returns at once and moves the clock
    by its timeout. What the loop does then depends on what it was given, never on how busy the machine is."""

    def __init__(self):
        self.now = 1000.0
        self.waited = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.advance(seconds)

    def advance(self, seconds):
        seconds = max(0.0, seconds or 0.0)
        self.now += seconds
        self.waited += seconds

    def select(self, read, write, other, timeout=None):
        ready = select.select(read, write, other, 0)
        if not any(ready):
            self.advance(timeout)
        return ready

    def installed(self):
        clock = types.SimpleNamespace(monotonic=self.monotonic, sleep=self.sleep, time=time.time)
        return mock.patch.multiple(cli, time=clock, select=types.SimpleNamespace(select=self.select))


@posix_only  # its clock waits with select.select on pipes, which Windows allows only on sockets
class LiveLoop(unittest.TestCase):
    """The live loop in-process, with the view replaced by a recording stub."""

    def run_loop(self, on_frame, argv=(), first_keys=b"", size=(100, 30)):
        """on_frame(n, state, send) -> rows, (rows, last_line) or a FakeCanvas;
        send(bytes) types keys."""
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        term = cli._Terminal(read_fd, None)
        written = []
        term.write = written.append
        term.size = lambda: size
        calls = []

        def send(data):
            os.write(write_fd, data)

        def stub(snapshot, width, height, source="", hint="", state=None):
            calls.append({"state": state, "hint": hint, "size": (width, height)})
            if len(calls) > 200:  # safety net: never hang the test run
                send(b"q")
            result = on_frame(len(calls), state, send)
            if isinstance(result, FakeCanvas):
                return result
            rows, last_line = result if isinstance(result, tuple) else (result, None)
            return FakeCanvas(rows, last_line)

        args = cli._arguments(["--demo", "--at", AT, *argv])
        send(first_keys)
        outcome = []
        with mock.patch.object(cli, "render", stub):
            thread = threading.Thread(target=lambda: outcome.append(cli._loop(args, term)), daemon=True)
            thread.start()
            thread.join(10)
        self.assertFalse(thread.is_alive(), "the loop did not stop")
        self.assertEqual(outcome, [0])
        return calls, "".join(written)

    def test_3_then_j_reaches_render(self):
        def on_frame(n, state, send):
            if state == ViewState(focus="loops"):
                send(b"j")
            elif state == ViewState(focus="loops", scroll=1):
                send(b"q")
            return ["x"] * 30

        calls, _ = self.run_loop(on_frame, first_keys=b"3")
        states = [call["state"] for call in calls]
        self.assertEqual(states[0], ViewState())
        self.assertIn(ViewState(focus="loops", scroll=1), states)
        self.assertLess(states.index(ViewState(focus="loops")),
                        states.index(ViewState(focus="loops", scroll=1)))
        self.assertTrue(all(call["hint"] == KEY_HELP for call in calls))

    def page_down(self, page_of):
        """Focus tasks, press Page Down once; page_of(state) is the canvas page.
        Returns the state the view was asked to draw after the page key."""
        def on_frame(n, state, send):
            if state == ViewState(focus="tasks"):
                send(b" ")
            elif state.scroll:
                send(b"q")
            return FakeCanvas(["x"] * 30, page=page_of(state))

        calls, _ = self.run_loop(on_frame, first_keys=b"2", size=(100, 30))
        return calls[-1]["state"]

    def test_page_keys_move_by_the_rows_the_focused_panel_shows(self):
        # The page of the frame just drawn counts: 9 on the dashboard, 17 on tasks.
        state = self.page_down(lambda state: 17 if state.focus == "tasks" else 9)
        self.assertEqual(state, ViewState(focus="tasks", scroll=17))

    def test_page_keys_fall_back_to_height_minus_6(self):
        for page in (None, 0, -3, True, 2.5, "17"):  # absent, not positive, or not an int
            with self.subTest(page=page):
                self.assertEqual(self.page_down(lambda state: page), ViewState(focus="tasks", scroll=24))

    def test_focus_and_scroll_set_the_starting_state(self):
        calls, _ = self.run_loop(lambda n, state, send: send(b"q") or ["x"] * 30,
                                 argv=["--focus", "events", "--scroll", "4"])
        self.assertEqual(calls[0]["state"], ViewState(focus="events", scroll=4))

    def test_last_line_hook_clamps_the_scroll(self):
        def on_frame(n, state, send):
            if state == ViewState(focus="loops"):
                send(b"G")
            elif state == ViewState(focus="loops", scroll=3):
                send(b"q")
            return ["x"] * 30, 3

        calls, _ = self.run_loop(on_frame, first_keys=b"3")
        states = [call["state"] for call in calls]
        self.assertIn(ViewState(focus="loops", scroll=10**9), states)  # drawn once, then clamped
        self.assertEqual(states[-1], ViewState(focus="loops", scroll=3))

    def test_held_key_repaints_at_once_but_reads_data_once_per_interval(self):
        loads = []
        clock = VirtualClock()

        def counting_load(args, previous):
            loads.append(clock.monotonic())
            return demo_snapshot(float(AT)), "demo", ""

        def on_frame(n, state, send):
            send(b"j" if n < 40 else b"q")
            return ["x"] * 30

        with clock.installed(), mock.patch.object(cli, "_load", counting_load):
            calls, _ = self.run_loop(on_frame, argv=["--interval", "60"])
        self.assertEqual(len(loads), 1)
        self.assertEqual(calls[-1]["state"].scroll, 39)
        # 40 frames at the animation rate would wait 5 seconds: keys repaint at once, so no frame waited at all.
        self.assertEqual(clock.waited, 0.0)

    def test_data_is_reread_every_interval(self):
        loads = []
        clock = VirtualClock()

        def counting_load(args, previous):
            loads.append(clock.monotonic())
            return demo_snapshot(float(AT)), "demo", ""

        def on_frame(n, state, send):
            if len(loads) >= 3 or clock.monotonic() - loads[0] > 3:
                send(b"q")
            return ["x"] * 30

        with clock.installed(), mock.patch.object(cli, "_load", counting_load):
            self.run_loop(on_frame, argv=["--interval", "0.3"])
        self.assertGreaterEqual(len(loads), 3)
        gaps = [b - a for a, b in zip(loads, loads[1:])]
        self.assertTrue(all(gap >= 0.29 for gap in gaps), gaps)  # never more often than asked
        self.assertLess(loads[2] - loads[0], 1.5, gaps)  # but again and again

    def test_a_shorter_row_erases_the_old_tail(self):
        def on_frame(n, state, send):
            send(b"j" if n == 1 else b"q")
            return ["a much longer first row"] * 30 if n == 1 else ["short"] * 30

        _, out = self.run_loop(on_frame, argv=["--no-color"])
        self.assertIn("\x1b[1;1Ha much longer first row\x1b[K", out)
        self.assertIn("\x1b[1;1Hshort\x1b[K", out)

    def test_a_frame_that_raises_shows_one_line_and_the_loop_goes_on(self):
        def on_frame(n, state, send):
            send(b"j" if n == 1 else b"q")
            if n == 1:
                raise RuntimeError("boom")
            return ["x"] * 30

        calls, out = self.run_loop(on_frame)
        self.assertEqual(len(calls), 2)
        self.assertIn("agentgraph could not draw this frame: boom\x1b[K", out)

    def test_the_error_frame_is_sanitised_and_clipped(self):
        for cols, expected in ((200, "  agentgraph could not draw this frame: " + CLEAN),
                               (60, ("  agentgraph could not draw this frame: " + CLEAN)[:60])):
            with self.subTest(cols=cols):
                def on_frame(n, state, send):
                    send(b"j" if n == 1 else b"q")
                    if n == 1:
                        raise RuntimeError(NASTY)
                    return ["x"] * 30

                _, out = self.run_loop(on_frame, size=(cols, 30))
                second_row = [m.group(2) for m in re.finditer(r"\x1b\[(\d+);1H([^\x1b]*)", out)
                              if m.group(1) == "2"]
                self.assertEqual(second_row[0], expected)  # the error frame, before the next frame
                self.assertTrue(_printable_ascii(out.replace("\x1b", "")), repr(out[:300]))
                for bad in ("\x1b[31m", "\x7f", "\n", "🙂", "\u0301"):
                    self.assertNotIn(bad, out)

    def test_quit_keys(self):
        for key in ("q", "Q", "ctrl-c"):
            state, quit_ = cli._apply_keys(ViewState(), ["j", key, "j"], page=10)
            self.assertEqual((state, quit_), (ViewState(scroll=1), True))
        self.assertEqual(cli._apply_keys(ViewState(), ["3", "j"], page=10),
                         (ViewState(focus="loops", scroll=1), False))


@posix_only
class TerminalCleanup(unittest.TestCase):
    """The terminal owner in-process on a pty: cleanup order and signals."""

    def setUp(self):
        self.master, self.slave = pty.openpty()
        self.out_r, self.out_w = os.pipe()
        self.before = termios.tcgetattr(self.slave)
        self.handlers = {sig: signal.getsignal(sig) for sig in cli.HELD_SIGNALS}
        self.addCleanup(self.close_fds)

    def close_fds(self):
        for sig, handler in self.handlers.items():  # never leave a test handler behind
            signal.signal(sig, signal.SIG_DFL if handler is None else handler)
        for fd in (self.master, self.slave, self.out_r, self.out_w):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)

    def opened(self):
        term = cli._Terminal(self.slave, self.out_w)
        term.open()
        self.assertEqual(termios.tcgetattr(self.slave)[3] & (termios.ECHO | termios.ICANON), 0)
        self.assertNotEqual(signal.getsignal(signal.SIGTERM), self.handlers[signal.SIGTERM])
        return term

    def assertRestored(self):
        self.assertEqual(termios.tcgetattr(self.slave)[3] & LFLAGS, self.before[3] & LFLAGS)
        self.assertEqual({sig: signal.getsignal(sig) for sig in cli.HELD_SIGNALS}, self.handlers)

    def screen_output(self):
        os.close(self.out_w)
        self.out_w = None
        with os.fdopen(os.dup(self.out_r), "rb") as pipe:
            return pipe.read()

    def lose_input(self, term, failure):
        """Make the next input check fail the way `failure` says, then wait for keys."""
        with mock.patch.object(cli.select, "select", side_effect=failure):
            self.assertIsNone(term.read_keys(time.monotonic()))

    def test_input_failure_still_restores_screen_and_attributes(self):
        for failure in (OSError(errno.EIO, "input hung up"), ValueError("file descriptor closed")):
            with self.subTest(failure=repr(failure)):
                term = self.opened()
                self.lose_input(term, failure)
                term.close()
                self.assertRestored()  # the descriptor is still valid: attributes are back
                self.assertTrue(self.screen_output().endswith(cli.LEAVE_SCREEN.encode()))
                os.close(self.out_r)
                self.out_r, self.out_w = os.pipe()

    def test_input_at_end_of_file_still_restores_screen_and_attributes(self):
        term = self.opened()
        with mock.patch.object(cli.os, "read", return_value=b""):
            with mock.patch.object(cli.select, "select", return_value=([self.slave], [], [])):
                self.assertIsNone(term.read_keys(time.monotonic()))
        term.close()
        self.assertRestored()
        output = self.screen_output()
        self.assertIn(LEAVE, output)
        self.assertIn(SHOW, output)

    def test_screen_is_restored_whatever_restoring_the_attributes_raises(self):
        term = self.opened()
        with mock.patch.object(cli.termios, "tcsetattr", side_effect=RuntimeError("unexpected")):
            term.close()
        termios.tcsetattr(self.slave, termios.TCSANOW, self.before)
        self.assertTrue(self.screen_output().endswith(cli.LEAVE_SCREEN.encode()))
        self.assertRestored()

    def test_attributes_first_then_screen_even_when_output_is_broken(self):
        term = self.opened()
        seen = []
        emit = term._emit_quietly

        def recording_emit(text, timeout=1.0):
            seen.append((text, bool(termios.tcgetattr(self.slave)[3] & termios.ICANON)))
            return emit(text, timeout)

        term._emit_quietly = recording_emit
        os.close(self.out_r)  # nobody reads the output any more: the screen write fails
        term.close()
        self.assertEqual(seen, [(cli.LEAVE_SCREEN, True)])  # attributes were already back
        self.assertRestored()

    def test_screen_is_restored_even_when_the_attributes_cannot_be(self):
        term = self.opened()
        with mock.patch.object(cli.termios, "tcsetattr", side_effect=termios.error("no")):
            term.close()
        termios.tcsetattr(self.slave, termios.TCSANOW, self.before)
        self.assertTrue(os.read(self.out_r, 4096).endswith(cli.LEAVE_SCREEN.encode()))
        self.assertRestored()

    def test_a_signal_during_cleanup_waits_until_it_is_done(self):
        term = self.opened()
        pending = []

        def interrupted_emit(text, timeout=1.0):
            os.kill(os.getpid(), signal.SIGINT)  # a second Ctrl-C while restoring
            pending.append(signal.SIGINT in signal.sigpending())
            return True

        term._emit_quietly = interrupted_emit
        term.close()  # neither KeyboardInterrupt nor _Stop escapes
        self.assertEqual(pending, [True])  # held, not delivered halfway
        self.assertRestored()

    def test_only_one_stop_is_ever_raised(self):
        term = cli._Terminal(None, None)
        with self.assertRaises(cli._Stop):
            term._on_stop(signal.SIGTERM, None)
        self.assertIsNone(term._on_stop(signal.SIGHUP, None))
        self.assertEqual(term.stop, signal.SIGTERM)
        self.assertFalse(issubclass(cli._Stop, Exception))  # `except Exception` cannot swallow it


@contextlib.contextmanager
def _stderr_fd():
    """Capture what is written to file descriptor 2."""
    captured = io.BytesIO()
    with tempfile.TemporaryFile() as tmp:
        sys.stderr.flush()
        saved = os.dup(2)
        os.dup2(tmp.fileno(), 2)
        try:
            yield captured
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            tmp.seek(0)
            captured.write(tmp.read())


def _cli(*args, **options):
    return subprocess.run([sys.executable, "-m", "agentgraph", *args], cwd=PROJECT,
                          capture_output=True, text=True, timeout=30, **options)


class Arguments(unittest.TestCase):
    def test_bad_values_are_usage_errors(self):
        for bad in (["--width", "0"], ["--height", "-3"], ["--at", "nan"], ["--at", "1e30"],
                    ["--at", "inf"], ["--at", "-1"], ["--focus", "nope"], ["--scroll", "-1"]):
            with self.subTest(bad):
                result = _cli("--demo", "--once", *bad)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("error", result.stderr)
                self.assertEqual(result.stdout, "")
        for edge in ("0", "4102444800", "1795000000.5"):
            self.assertEqual(cli._arguments(["--at", edge]).at, float(edge))
        self.assertEqual(cli._arguments(["--width", "1", "--height", "1"]).width, 1)

    def assertFocusedAndScrolled(self, export):
        """export(focus, scroll) -> list of frame rows. A focused panel is not the
        dashboard, and scrolling by 5 moves its rows up by 5."""
        dashboard, top, scrolled = export("all", 0), export("loops", 0), export("loops", 5)
        self.assertEqual(len(dashboard), len(top))
        self.assertNotEqual(top, dashboard)
        self.assertNotEqual(scrolled, top)
        moved = [i for i in range(len(scrolled) - 5)
                 if scrolled[i] == top[i + 5] and sum(ch.isalnum() for ch in scrolled[i]) >= 3
                 and scrolled[i] not in dashboard]
        self.assertGreaterEqual(len(moved), 3, "the panel's lines did not move up by the scroll")

    def test_once_exports_a_focused_scrolled_panel(self):
        def export(focus, scroll):
            result = _cli("--demo", "--once", "--focus", focus, "--scroll", str(scroll), "--no-color",
                          "--width", "120", "--height", "40", "--at", AT)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("\x1b", result.stdout)
            rows = result.stdout.splitlines()
            self.assertEqual(len(rows), 40)
            return rows

        self.assertFocusedAndScrolled(export)

    def test_html_exports_a_focused_scrolled_panel(self):
        def export(focus, scroll):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "frame.html"
                result = _cli("--demo", "--html", str(path), "--focus", focus, "--scroll", str(scroll),
                              "--width", "120", "--height", "40", "--at", AT)
                self.assertEqual(result.returncode, 0, result.stderr)
                page = path.read_text(encoding="utf-8")
            body = re.search(r"<pre>(.*)</pre>", page, re.S)  # the frame only, not the title
            self.assertIsNotNone(body)
            return body.group(1).split("\n")

        self.assertFocusedAndScrolled(export)

    def test_focus_and_scroll_reach_render_in_once_and_html(self):
        seen = []

        def stub(snapshot, width, height, source="", hint="", state=None):
            seen.append(state)
            return FakeCanvas(["frame"])

        with mock.patch.object(cli, "render", stub), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--demo", "--once", "--focus", "loops", "--scroll", "2",
                                       "--width", "80", "--height", "24"]), 0)
        self.assertEqual(seen, [ViewState(focus="loops", scroll=2)])

        pages = []

        def html_stub(snapshot, width, height, source="", state=None):
            pages.append(state)
            return "<pre>frame</pre>"

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(cli, "render_html", html_stub):
            path = Path(tmp) / "out" / "frame.html"
            self.assertEqual(cli.main(["--demo", "--html", str(path), "--focus", "events"]), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), "<pre>frame</pre>")
        self.assertEqual(pages, [ViewState(focus="events")])

    def test_a_frame_that_raises_is_one_sanitised_line_and_exit_1(self):
        def broken(*args, **kwargs):
            raise RuntimeError(NASTY)

        for argv, target in ((["--demo", "--once"], "render"),
                             (["--demo", "--html", os.devnull + "/x.html"], "render_html")):
            with self.subTest(argv[1]):
                with mock.patch.object(cli, target, broken), _stderr_fd() as err, \
                        contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(cli.main(argv), 1)
                self.assertEqual(err.getvalue(), f"agentgraph: could not draw the frame: {CLEAN}\n".encode())
                self.assertEqual(out.getvalue(), "")

    @posix_only  # a path under the null device
    def test_an_unwritable_html_path_is_reported_sanitised(self):
        path = os.devnull + "/\x1b[31m🙂\n.html"
        with _stderr_fd() as err:
            self.assertEqual(cli.main(["--demo", "--html", path, "--at", AT]), 1)
        line = err.getvalue().decode("ascii")
        self.assertTrue(line.startswith("agentgraph: could not write /dev/null/?[31m? .html: "), line)
        self.assertEqual(line.count("\n"), 1)
        self.assertTrue(_printable_ascii(line), repr(line))

    def test_the_sanitiser(self):
        self.assertEqual(cli._plain_text(NASTY, 200), CLEAN)
        self.assertEqual(cli._plain_text(NASTY, 12), CLEAN[:12])
        self.assertEqual(cli._plain_text("  \t\n ", 10), "")
        self.assertEqual(cli._plain_text("\x00\x1f\x80\x9b\xa0\u2028x", 20), "??????x")
        self.assertEqual(cli._plain_text("abc", 0), "")

    def test_the_stderr_line_fits_a_terminal(self):
        with _stderr_fd() as err, mock.patch.object(cli.os, "isatty", return_value=True), \
                mock.patch.object(cli.os, "get_terminal_size", return_value=os.terminal_size((30, 10))):
            cli._complain(NASTY)
        self.assertEqual(err.getvalue(), ("agentgraph: " + CLEAN)[:30].encode() + b"\n")

    @posix_only  # exit status 128 + SIGPIPE
    def test_once_into_a_closed_pipe_exits_quietly(self):
        # A frame far larger than a pipe holds, so the writer is still busy when the reader goes.
        with tempfile.TemporaryFile() as err:
            child = subprocess.Popen(
                [sys.executable, "-m", "agentgraph", "--demo", "--once", "--no-color",
                 "--width", "600", "--height", "300", "--at", AT],
                cwd=PROJECT, stdout=subprocess.PIPE, stderr=err)
            try:
                first = child.stdout.readline()
                child.stdout.close()  # like `| head -1`
                code = child.wait(timeout=30)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
            err.seek(0)
            stderr = err.read().decode(errors="replace")
        self.assertIn(b"AGENTGRAPH", first)
        self.assertEqual(stderr, "")  # no traceback, no "Exception ignored"
        self.assertEqual(code, 141)  # 128 + SIGPIPE, like a standard tool cut off by a pipe

    def test_piped_live_mode_gives_one_plain_frame(self):
        result = _cli("--demo", "--width", "100", "--height", "30", "--at", AT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 30)
        self.assertNotIn("\x1b", result.stdout)
        self.assertTrue(all(len(line) <= 100 for line in result.stdout.splitlines()))

    def test_dir_and_session_reach_the_data_source(self):
        calls = []

        def fake_build(**kwargs):
            calls.append(kwargs)
            return demo_snapshot(float(AT))

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("agentgraph.sources.build_snapshot", fake_build), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.main(["--once", "--dir", tmp, "--session", "ses_x", "--interval", "0.5",
                             "--no-color", "--width", "100", "--height", "30", "--at", AT])
        self.assertEqual(code, 0)
        self.assertEqual(calls[0]["graph_dir"], Path(tmp))
        self.assertEqual(calls[0]["root_id"], "ses_x")
        self.assertEqual(len(out.getvalue().splitlines()), 30)


if __name__ == "__main__":
    unittest.main()
