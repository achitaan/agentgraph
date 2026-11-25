"""Command line entry point: python3 -m agentgraph.

Live mode uses the alternate screen and a hidden cursor, redraws about eight
times a second, and re-reads the data every --interval seconds. Keys (see
viewstate.KEY_HELP) focus and scroll a panel; q, Q or Ctrl-C quits.

The terminal is restored on every exit path: q, Ctrl-C, SIGTERM, SIGHUP and
SIGQUIT all unwind and exit with code 0; a terminal that stops accepting
output ends the viewer with code 1. Cleanup restores the terminal attributes
first and the screen second, each attempted even if the other fails, with
stop signals held until it is done. Ctrl-Z (SIGTSTP) leaves the screen,
restores the terminal and stops; SIGCONT takes the screen back and repaints.
When stdin is not a terminal, or stops working, no keys are read and the view
keeps refreshing.

--once into a pipe whose reader leaves early (`| head`) stops quietly with
status 141 (128 + SIGPIPE, what a standard tool killed by SIGPIPE reports).
Error messages, on stderr and in the fallback frame, are reduced to printable
ASCII so that no text can put escape sequences on the terminal.
"""
from __future__ import annotations

import argparse
import dataclasses
import math
import os
import re
import select
import shutil
import signal
import sys
import time
from pathlib import Path

from . import winconsole
from .demo import demo_snapshot
from .view import render, render_html
from .viewstate import KEY_HELP, PANELS, ViewState, clamp, decode_keys, handle_key

try:  # terminal control is POSIX; the view still works without it
    import termios
    import tty
except ImportError:  # pragma: no cover
    termios = None
    tty = None

WINDOWS = os.name == "nt"  # the console is driven through msvcrt and ctypes there (winconsole), not termios and select

FRAME_SECONDS = 0.125      # animation frame period between data reads
FALLBACK_SIZE = (120, 40)  # used when the terminal size cannot be read
LATEST_TIME = 4102444800   # 2100-01-01: later --at values are refused
QUIT_KEYS = ("q", "Q", "ctrl-c")
COMPLAINT_LIMIT = 500      # longest stderr line when stderr is not a terminal
PIPE_CLOSED_STATUS = 128 + getattr(signal, "SIGPIPE", 13)  # 141, as for a tool killed by SIGPIPE

ENTER_SCREEN = "\x1b[?1049h\x1b[?25l\x1b[H\x1b[2J"  # alternate screen, hidden cursor, cleared
LEAVE_SCREEN = "\x1b[0m\x1b[?25h\x1b[?1049l"        # plain style, cursor shown, main screen
ERASE_TAIL = "\x1b[K"                               # erase from the cursor to the end of the line
_CSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _signals(*names: str) -> tuple:
    return tuple(getattr(signal, name) for name in names if hasattr(signal, name))


STOP_SIGNALS = _signals("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT", "SIGBREAK")  # unwind, restore, exit 0 (SIGBREAK: Ctrl-Break, Windows only)
# Held while the terminal is being restored, so a signal cannot cut that short.
# SIGTTOU too: then a viewer in the background still restores the terminal.
HELD_SIGNALS = STOP_SIGNALS + _signals("SIGTSTP", "SIGCONT", "SIGWINCH", "SIGTTOU")
# Held while taking the terminal (at start and on SIGCONT). Not SIGTTOU: started
# or resumed in the background, the viewer stops instead of drawing over the shell.
RESUME_SIGNALS = STOP_SIGNALS + _signals("SIGTSTP", "SIGWINCH")


# --- arguments ------------------------------------------------------------------


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be 0 or more, got {value}")
    return value


def _unix_time(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}") from None
    if not math.isfinite(value) or not 0 <= value <= LATEST_TIME:
        raise argparse.ArgumentTypeError(
            f"must be a unix time between 0 and {LATEST_TIME} (the year 2100), got {value}")
    return value


def _arguments(argv=None):
    parser = argparse.ArgumentParser(
        prog="agentgraph",
        description="Live view of the agent roster and the active task graph.",
        epilog="Keys in live mode: " + KEY_HELP)
    parser.add_argument("--demo", action="store_true",
                        help="draw the built-in example snapshot")
    parser.add_argument("--dir", type=Path, dest="graph_dir", metavar="PATH",
                        help="directory containing task graph JSON files")
    parser.add_argument("--session", dest="root_id", metavar="ID",
                        help="root OpenCode session id to watch")
    parser.add_argument("--once", action="store_true",
                        help="draw one frame to stdout and exit")
    parser.add_argument("--html", metavar="PATH", type=Path,
                        help="write one frame as a standalone HTML file and exit")
    parser.add_argument("--width", type=_positive_int,
                        help="frame width for --once and --html (default: the terminal)")
    parser.add_argument("--height", type=_positive_int,
                        help="frame height for --once and --html (default: the terminal)")
    parser.add_argument("--focus", choices=PANELS, default="all",
                        help="panel to show full screen (live mode: the starting panel)")
    parser.add_argument("--scroll", type=_non_negative_int, default=0, metavar="N",
                        help="first line shown in the focused panel")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="seconds between data reads in live mode (default 1.0)")
    parser.add_argument("--no-color", action="store_true",
                        help="plain ASCII output without colour")
    parser.add_argument("--at", type=_unix_time, metavar="UNIX_TIME",
                        help="draw as if the clock read this time, for reproducible frames")
    return parser.parse_args(argv)


# --- one frame: --once and --html ----------------------------------------------------


def _load(args, previous):
    """Return (snapshot, source, note). The note explains a fallback, or is empty."""
    if args.demo:
        return demo_snapshot(args.at), "demo", ""
    try:
        from .sources import build_snapshot  # imported lazily: sources may be absent
        snap = build_snapshot(graph_dir=args.graph_dir, root_id=args.root_id, cwd=Path.cwd())
        return snap, "live", ""
    except Exception as exc:  # a failed import and a failed read both end up here
        note = f"live data unavailable ({exc})"
        if previous is not None:
            return previous, "live", note
        return demo_snapshot(args.at), "demo", note


def _pinned(snap, args):
    """Fix the frame clock when --at is given, so a frame can be reproduced."""
    return dataclasses.replace(snap, now=args.at) if args.at is not None else snap


def _size(args):
    detected = shutil.get_terminal_size(FALLBACK_SIZE)
    width = args.width if args.width is not None else detected.columns
    height = args.height if args.height is not None else detected.lines
    return max(1, width), max(1, height)


def _initial_state(args) -> ViewState:
    return ViewState(focus=args.focus, scroll=args.scroll)


def _plain_text(text, width: int) -> str:
    """Error text made safe for a terminal: every character outside printable
    ASCII (0x20 to 0x7E) becomes '?', each run of whitespace one space, and
    the result is clipped to `width` columns (one column per character, since
    only ASCII is left). Self-contained: it is also the fallback when the view
    itself fails."""
    out, pending_space = [], False
    for ch in str(text):
        if ch in " \t\n\r\v\f":
            pending_space = bool(out)  # leading whitespace is dropped
            continue
        if pending_space:
            out.append(" ")
            pending_space = False
        out.append(ch if " " < ch <= "~" else "?")
    return "".join(out)[:max(0, int(width))]


def _complain(message) -> None:
    """One sanitised line on stderr, written straight to the descriptor, never
    wider than stderr's terminal (or COMPLAINT_LIMIT characters); never raises."""
    width = COMPLAINT_LIMIT
    try:
        if os.isatty(2):
            width = os.get_terminal_size(2).columns
    except (OSError, ValueError):
        pass
    line = _plain_text("agentgraph: " + str(message), width) + "\n"
    try:
        os.write(2, line.encode("ascii"))
    except (OSError, ValueError):
        pass


def _silence_stdout() -> None:
    """Point stdout at /dev/null, so the interpreter's flush at exit cannot fail
    again on a pipe whose reader has gone."""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, sys.stdout.fileno())
        finally:
            os.close(devnull)
    except (OSError, ValueError, AttributeError):
        pass


def _once(args) -> int:
    # Colour only for a terminal: a redirected frame is plain text unless asked otherwise.
    color = not args.no_color and sys.stdout.isatty()
    state = None
    if color and WINDOWS:  # coloured output needs ANSI switched on in the console, and put back after
        console = winconsole.load_console()
        state = winconsole.enable_vt(console)
    try:
        return _once_frame(args, color)
    finally:
        if state is not None:
            winconsole.restore(console, state)


def _once_frame(args, color) -> int:
    try:
        snap, source, note = _load(args, None)
        if note:
            _complain(f"{note}; showing the demo")
        width, height = _size(args)
        frame = render(_pinned(snap, args), width, height, source=source,
                       state=_initial_state(args))
        rows = frame.rows_ansi(color=color)
    except Exception as exc:  # a broken frame is an error message, not a traceback
        _complain(f"could not draw the frame: {exc}")
        return 1
    try:
        sys.stdout.write("\n".join(rows) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:  # the reader left early (`| head`): stop quietly
        _silence_stdout()
        return PIPE_CLOSED_STATUS
    return 0


def _html(args) -> int:
    try:
        snap, source, note = _load(args, None)
        if note:
            _complain(f"{note}; showing the demo")
        width, height = _size(args)
        page = render_html(_pinned(snap, args), width, height, source=source,
                           state=_initial_state(args))
    except Exception as exc:
        _complain(f"could not draw the frame: {exc}")
        return 1
    try:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(page, encoding="utf-8")
    except OSError as exc:
        _complain(f"could not write {args.html}: {exc}")
        return 1
    return 0


# --- the terminal in live mode -----------------------------------------------------------


class _Stop(BaseException):
    """Raised by a stop signal to unwind the live loop. A BaseException, like
    KeyboardInterrupt, so `except Exception` in the data and frame code lets it pass."""


class _OutputLost(Exception):
    """The terminal stopped accepting output (closed, hung up, broken pipe)."""


def _hold(signals):
    """Block these signals; return the previous mask for _release."""
    try:
        return signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    except (AttributeError, OSError, ValueError):
        return None


def _release(mask) -> None:
    """Restore a mask from _hold. Signals that arrived meanwhile are handled
    before this returns (pthread_sigmask runs pending Python handlers)."""
    if mask is not None:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)


def _stdin_fd():
    """The stdin file descriptor when stdin is a terminal, otherwise None."""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            return sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        pass
    return None


def _terminal_size(fd) -> tuple[int, int]:
    """The size of the terminal on `fd`, asked of the terminal itself, so stale
    COLUMNS/LINES in the environment cannot override it."""
    try:
        size = os.get_terminal_size(fd)
        if size.columns > 0 and size.lines > 0:
            return size.columns, size.lines
    except (OSError, ValueError, TypeError):
        pass
    size = shutil.get_terminal_size(FALLBACK_SIZE)
    return max(1, size.columns), max(1, size.lines)


def _incomplete_escape(data: bytes) -> bool:
    """True when the input ends partway through an escape sequence."""
    start = data.rfind(b"\x1b")
    if start < 0:
        return False
    tail = data[start:]
    if len(tail) == 1:
        return True
    if tail[1:2] == b"O":
        return len(tail) == 2
    if tail[1:2] == b"[":
        return all(0x20 <= b <= 0x3F for b in tail[2:])
    return False


class _Terminal:
    """Owns the terminal during live mode: cbreak input, the alternate screen,
    and the signal handlers that keep both restorable."""

    def __init__(self, in_fd, out_fd, encoding: str = "utf-8", windows=None, console=None, msvcrt=None):
        self.in_fd = in_fd      # where keys come from; None once input fails or ends
        self.out_fd = out_fd    # the terminal the frames go to
        self.encoding = encoding
        # Windows: no termios, select or signals for the terminal. Keys are polled from msvcrt, ANSI output is switched on through
        # the console API; `console` and `msvcrt` are the real ones unless a test passes fakes.
        self.windows = WINDOWS if windows is None else windows
        self.console = console
        self.msvcrt = msvcrt
        self.console_state = None
        # The terminal whose attributes we changed, and the attributes to put back.
        # Kept apart from in_fd: losing input must not stop the restore.
        self.tty_fd = None
        self.saved = None
        self.previous = {}      # signal number -> handler to put back
        self.stop = None        # the signal that asked us to stop
        self.closing = False    # cleanup has started: signals are ignored from here on
        self.suspended = False  # SIGTSTP left the screen and SIGCONT has not taken it back
        self.repaint = False    # the next frame must redraw every row

    # set up and tear down ---------------------------------------------------------------
    def open(self) -> None:
        mask = _hold(RESUME_SIGNALS)
        try:
            handlers = {sig: self._on_stop for sig in STOP_SIGNALS}
            for name, handler in (("SIGTSTP", self._on_tstp), ("SIGCONT", self._on_cont),
                                  ("SIGWINCH", self._on_winch)):
                if hasattr(signal, name):
                    handlers[getattr(signal, name)] = handler
            for sig, handler in handlers.items():
                try:
                    self.previous[sig] = signal.signal(sig, handler)
                except (OSError, ValueError):  # not on the main thread, or not settable
                    pass
            if self.windows:
                self._open_console()
            elif self.in_fd is not None and termios is not None:
                try:
                    saved = termios.tcgetattr(self.in_fd)
                except Exception:  # not a usable terminal after all: leave it alone
                    saved = None
                if saved is not None:
                    self.tty_fd, self.saved = self.in_fd, saved
                    self._cbreak()
        finally:
            _release(mask)
        self.write(ENTER_SCREEN)  # after the release: a signal can interrupt a stuck write

    def _open_console(self) -> None:
        """Windows: ANSI output and UTF-8 on, keys from msvcrt. Without a console API the frames are still drawn (Windows Terminal
        has ANSI on already); without msvcrt there are no keys."""
        self.console = self.console if self.console is not None else winconsole.load_console()
        self.msvcrt = self.msvcrt if self.msvcrt is not None else winconsole.load_msvcrt()
        self.console_state = winconsole.set_utf8_output(self.console, winconsole.enable_vt(self.console))
        self.encoding = "utf-8"
        if self.msvcrt is None:
            self.in_fd = None

    def close(self) -> None:
        """Restore the terminal. Idempotent; safe to call again if interrupted."""
        self.closing = True
        mask = _hold(HELD_SIGNALS)
        try:
            self._leave()
        finally:
            _release(mask)  # anything that arrived meanwhile reaches our handlers, which ignore it
            self._restore_handlers()

    def _restore_handlers(self) -> None:
        for sig, handler in list(self.previous.items()):
            try:
                signal.signal(sig, signal.SIG_DFL if handler is None else handler)
            except (OSError, ValueError, TypeError):
                pass
            del self.previous[sig]

    def _leave(self) -> None:
        """Attributes first, then the screen; the screen is tried whatever the first step does."""
        try:
            self._restore_attributes()
        finally:
            try:
                self._emit_quietly(LEAVE_SCREEN)
            finally:
                if self.windows:  # after the screen is left: the escape sequences need the console mode we switched on
                    winconsole.restore(self.console, self.console_state)

    def _restore_attributes(self) -> None:
        if self.windows or self.saved is None or self.tty_fd is None:
            return
        try:
            termios.tcsetattr(self.tty_fd, termios.TCSANOW, self.saved)
        except Exception:  # descriptor gone or terminal hung up: nothing more to do for it
            pass

    def _cbreak(self) -> None:
        """Single keys, no echo; Ctrl-C and Ctrl-Z still signal."""
        if self.saved is None or self.tty_fd is None:
            return
        try:
            tty.setcbreak(self.tty_fd, termios.TCSANOW)
        except Exception:
            pass

    def _enter(self) -> None:
        self._cbreak()
        self._emit_quietly(ENTER_SCREEN)
        self.repaint = True

    def _resume(self) -> None:
        mask = _hold(RESUME_SIGNALS)
        try:
            self.suspended = False
            self._enter()
        finally:
            _release(mask)

    # signal handlers --------------------------------------------------------------------
    def _on_stop(self, signum, frame):
        if self.closing or self.stop is not None:
            return  # already on the way out: at most one _Stop is ever raised
        self.stop = signum
        raise _Stop(signum)

    def _on_tstp(self, signum, frame):
        if self.closing or self.stop is not None:
            return
        mask = _hold(HELD_SIGNALS)
        try:
            self._leave()
            self.suspended = True
            signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        finally:
            _release(mask)
        os.kill(os.getpid(), signal.SIGTSTP)  # the default action stops the process here
        signal.signal(signal.SIGTSTP, self._on_tstp)
        if self.suspended and not self.closing and self.stop is None:
            # No SIGCONT came: the stop was discarded (an orphaned process group
            # cannot be stopped by SIGTSTP), so take the screen back now.
            self._resume()

    def _on_cont(self, signum, frame):
        if self.closing or self.stop is not None:
            return
        self._resume()

    def _on_winch(self, signum, frame):
        self.repaint = True

    # output -------------------------------------------------------------------------------
    def write(self, text: str) -> None:
        """Write everything, blocking. Signals can interrupt it."""
        data = memoryview(text.encode(self.encoding, "replace"))
        try:
            while data:
                try:
                    written = os.write(self.out_fd, data)
                except BlockingIOError:  # someone made the terminal non-blocking
                    if self.windows:
                        time.sleep(FRAME_SECONDS)  # select cannot wait on a console
                    else:
                        select.select([], [self.out_fd], [], FRAME_SECONDS)
                    continue
                data = data[written:]
        except OSError as exc:
            raise _OutputLost(exc) from exc

    def _emit_quietly(self, text: str, timeout: float = 1.0) -> bool:
        """Write without ever blocking for long or raising: used while signals are held."""
        deadline = time.monotonic() + timeout
        try:
            data = memoryview(text.encode(self.encoding, "replace"))
            while data:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                if not self.windows:  # select cannot wait on a console: Windows writes at once
                    _, ready, _ = select.select([], [self.out_fd], [], remaining)
                    if not ready:
                        return False
                data = data[os.write(self.out_fd, data):]
        except Exception:  # never let a failed write cut cleanup short
            return False
        return True

    def size(self) -> tuple[int, int]:
        return _terminal_size(self.out_fd)

    def take_repaint(self) -> bool:
        repaint, self.repaint = self.repaint, False
        return repaint

    # input --------------------------------------------------------------------------------
    def read_keys(self, deadline: float):
        """Wait until `deadline` (time.monotonic) for input. Returns the decoded
        keys, possibly an empty list, or None when the time ran out."""
        while True:
            remaining = deadline - time.monotonic()
            if self.in_fd is None:
                if remaining > 0:
                    time.sleep(remaining)
                return None
            if self.windows:
                data = winconsole.wait_for_keys(self.msvcrt, deadline, clock=time.monotonic, sleep=time.sleep)
                return None if data is None else decode_keys(data)
            try:
                ready, _, _ = select.select([self.in_fd], [], [], max(0.0, remaining))
            except (OSError, ValueError, TypeError):  # input is gone: stop listening, keep drawing
                self.in_fd = None
                continue
            if not ready:
                return None
            data = self._read()
            if data is None:  # stdin closed or hung up: keep refreshing, stop listening
                self.in_fd = None
                continue
            return decode_keys(data)

    def _read(self):
        try:
            data = os.read(self.in_fd, 1024)
        except (OSError, ValueError, TypeError):
            return None
        if not data:
            return None
        # An escape sequence split across reads would decode as a lone ESC.
        while _incomplete_escape(data) and len(data) < 4096:
            try:
                ready, _, _ = select.select([self.in_fd], [], [], 0.05)
                more = os.read(self.in_fd, 1024) if ready else b""
            except (OSError, ValueError, TypeError):
                more = b""
            if not more:
                break
            data += more
        return data


# --- the live loop -------------------------------------------------------------------------


class _MessageFrame:
    """Stands in for a canvas when the view raised: one line of plain text.
    Self-contained, so it cannot fail the way the view just did."""

    def __init__(self, width: int, height: int, message: str):
        self.lines = [""] * max(1, height)
        width = max(0, int(width))
        self.lines[min(1, len(self.lines) - 1)] = ("  " + _plain_text(message, width - 2))[:width]

    def rows_ansi(self, color: bool = True) -> list[str]:
        return list(self.lines)


def _safe_frame(view, width: int, height: int, source: str, hint: str, state, color: bool):
    """Returns (frame, rows). A live frame must never take the screen down:
    on an error, show it on one line."""
    try:
        frame = render(view, width, height, source=source, hint=hint, state=state)
        return frame, frame.rows_ansi(color=color)
    except Exception as exc:  # defensive; the frame still refreshes
        frame = _MessageFrame(width, height, f"agentgraph could not draw this frame: {exc}")
        return frame, frame.rows_ansi(color=color)


def _erase_tail(row: str, width: int) -> str:
    """ESC [ K after a row that does not fill the line, so a shorter row never
    leaves the end of the previous one on screen. A row that fills the line
    has overwritten every cell; an erase there would wipe its last cell on
    xterm-compatible terminals, where the cursor stays on the last column."""
    return ERASE_TAIL if len(_CSI.sub("", row)) < width else ""


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _page_size(frame, height: int) -> int:
    """Lines a page key moves: the rows the focused panel shows at once, as the
    frame just drawn reports in `page`; height - 6 when it reports none."""
    page = getattr(frame, "page", None)
    return page if _is_int(page) and page > 0 else max(1, height - 6)


def _apply_keys(state: ViewState, keys, page: int):
    """Apply decoded keys. Returns (state, quit): quit is True for q, Q or Ctrl-C,
    and keys after it are ignored."""
    for key in keys:
        if key in QUIT_KEYS:
            return state, True
        state = handle_key(state, key, page=page)
    return state, False


def _loop(args, term) -> int:
    interval = max(0.1, args.interval)
    color = not args.no_color
    state = _initial_state(args)
    snap, source, note = None, "demo", ""
    last_good = None
    next_read = 0.0
    shown_rows, shown_size = None, None
    while True:
        if term.stop is not None:  # a stop signal whose exception was swallowed somewhere
            return 0
        started = time.monotonic()
        if snap is None or started >= next_read:  # data at most once per interval
            snap, source, note = _load(args, last_good)
            if source == "live" and not note:
                last_good = snap
            next_read = started + interval
        dims = term.size()
        now = args.at if args.at is not None else time.time()
        view = dataclasses.replace(snap, now=now)  # the animation clock, not the data clock
        if note:
            view = dataclasses.replace(view, warnings=list(view.warnings) + [note])
        frame, rows = _safe_frame(view, dims[0], dims[1], source, KEY_HELP, state, color)
        full = (term.take_repaint() or shown_rows is None or dims != shown_size
                or len(rows) != len(shown_rows))
        parts = ["\x1b[H\x1b[2J"] if full else []
        for y, row in enumerate(rows):
            if full or row != shown_rows[y]:
                parts.append(f"\x1b[{y + 1};1H{row}{_erase_tail(row, dims[0])}")
        if parts:
            term.write("".join(parts))
        shown_rows, shown_size = rows, dims
        last_line = getattr(frame, "last_line", None)
        if _is_int(last_line):
            state = clamp(state, last_line)
        page = _page_size(frame, dims[1])
        # Wait for the next animation frame, but repaint at once after a key that
        # changed the state. The data is still read only once per interval.
        deadline = started + FRAME_SECONDS
        while True:
            keys = term.read_keys(deadline)
            if keys is None:
                break
            new, quit_ = _apply_keys(state, keys, page)
            if quit_:
                return 0
            if new != state:
                state = new
                break


def _live(args) -> int:
    try:
        interactive = sys.stdout.isatty()
        out_fd = sys.stdout.fileno() if interactive else None
    except (AttributeError, ValueError, OSError):
        interactive = False
    if not interactive:
        return _once(args)  # piped output gets one frame, never escape sequences
    try:
        sys.stdout.flush()  # frames go straight to the descriptor from here on
    except (OSError, ValueError):
        pass
    in_fd = _stdin_fd() if termios is not None or WINDOWS else None  # keys need POSIX terminal control, or msvcrt
    term = _Terminal(in_fd, out_fd, getattr(sys.stdout, "encoding", None) or "utf-8")
    code, lost = 0, None
    try:
        term.open()
        code = _loop(args, term)
    except (_Stop, KeyboardInterrupt):  # KeyboardInterrupt: only if our handlers could not be set
        code = 0
    except _OutputLost as exc:
        code, lost = 1, exc.__cause__ or exc
    finally:
        try:  # first statement: no signal can land between here and the call
            term.close()
        except _Stop:  # raised just as cleanup began (only one ever is): finish it
            term.close()
    if lost is not None:
        _complain(f"output lost ({lost})")
    return code


def main(argv=None) -> int:
    given = sys.argv[1:] if argv is None else list(argv)
    if WINDOWS:
        winconsole.utf8_streams()  # the default code page cannot write the view's characters
    if given[:1] == ["handoff"]:  # bin/agentgraph sends the commands it does not name here
        from .handoff import main as handoff_main
        return handoff_main(given[1:])
    if given[:1] == ["node"]:
        from .noderun import main as node_main
        return node_main(given[1:])
    if given[:1] == ["review-copy"]:
        from .reviewcopy import main as review_copy_main
        return review_copy_main(given[1:])
    args = _arguments(argv)
    if args.html is not None:
        return _html(args)
    if args.once:
        return _once(args)
    try:
        return _live(args)
    except _Stop:  # a stop signal after the loop ended; the terminal is already restored
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
