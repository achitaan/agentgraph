"""The Windows console for the live view. Standard library only; imports nothing Windows-specific at import time.

Keys come from `msvcrt` (`kbhit`, `getwch`): an arrow, Home, End or Page key arrives as two reads (a prefix of "\\x00" or "\\xe0",
then a code), which `key_bytes` turns into the ANSI sequence a POSIX terminal sends, so `viewstate.decode_keys` serves both.
Output is ANSI: `enable_vt` switches the console to ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004) and `set_utf8_output` selects code
page 65001 so UTF-8 bytes written straight to the console descriptor are read as UTF-8; `restore` puts both back.

`console` and `msvcrt` are passed in, so tests drive every function here with fakes; only `load_console` and `load_msvcrt`
touch the real modules.
"""
from __future__ import annotations

import sys
import time

ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
STD_OUTPUT_HANDLE = -11
CP_UTF8 = 65001
INVALID_HANDLE = -1
KEY_POLL_SECONDS = 0.01

# What the second read of a two-step key means, as the ANSI bytes a POSIX terminal sends for it.
_EXTENDED = {
    "H": b"\x1b[A", "P": b"\x1b[B", "K": b"\x1b[D", "M": b"\x1b[C",   # up, down, left, right
    "I": b"\x1b[5~", "Q": b"\x1b[6~",                                  # page up, page down
    "G": b"\x1b[H", "O": b"\x1b[F",                                    # home, end
    "S": b"\x1b[3~",                                                   # delete
    "\x0f": b"\x1b[Z",                                                 # shift-tab (prefix 0x00, code 0x0f)
}


class Console:
    """Windows only: the kernel32 console calls this module needs, behind plain methods. A test passes a fake with the same methods."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes, self._wintypes = ctypes, wintypes
        self._lib = ctypes.WinDLL("kernel32", use_last_error=True)
        self._lib.GetStdHandle.argtypes = [wintypes.DWORD]
        self._lib.GetStdHandle.restype = wintypes.HANDLE
        self._lib.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self._lib.GetConsoleMode.restype = wintypes.BOOL
        self._lib.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._lib.SetConsoleMode.restype = wintypes.BOOL
        self._lib.GetConsoleOutputCP.restype = wintypes.UINT
        self._lib.SetConsoleOutputCP.argtypes = [wintypes.UINT]
        self._lib.SetConsoleOutputCP.restype = wintypes.BOOL

    def std_handle(self, which):
        return self._lib.GetStdHandle(which & 0xFFFFFFFF)

    def get_mode(self, handle):
        mode = self._wintypes.DWORD(0)
        return mode.value if self._lib.GetConsoleMode(handle, self._ctypes.byref(mode)) else None

    def set_mode(self, handle, mode) -> bool:
        return bool(self._lib.SetConsoleMode(handle, mode))

    def get_code_page(self) -> int:
        return int(self._lib.GetConsoleOutputCP())

    def set_code_page(self, page) -> bool:
        return bool(self._lib.SetConsoleOutputCP(page))


def load_console():
    """The console API, or None where ctypes has no Windows library."""
    try:
        return Console()
    except (ImportError, AttributeError, OSError):
        return None


def load_msvcrt():
    """Windows only: the msvcrt module, or None."""
    try:
        import msvcrt
        return msvcrt
    except ImportError:
        return None


def utf8_streams(streams=None) -> None:
    """Make stdout and stderr write UTF-8 whatever the console or locale code page is (cp1252 cannot write the view's box characters).
    Streams that cannot be reconfigured (replaced by a test, closed) are left alone."""
    for stream in (streams if streams is not None else (sys.stdout, sys.stderr)):
        try:
            if (getattr(stream, "encoding", "") or "").lower().replace("_", "-") not in ("utf-8", "utf8"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


class ConsoleState:
    """What `prepare` changed and `restore` puts back."""

    def __init__(self, handle=None, mode=None, code_page=None, changed_mode=False, changed_page=False):
        self.handle, self.mode, self.code_page = handle, mode, code_page
        self.changed_mode, self.changed_page = changed_mode, changed_page


def enable_vt(console) -> ConsoleState:
    """ANSI output on: add 0x0004 to the stdout console mode. The state says whether it worked (`changed_mode`); when it did not (stdout
    is no console, or an old console that lacks the flag) nothing was changed and the caller goes on anyway: Windows Terminal has it on."""
    state = ConsoleState()
    if console is None:
        return state
    try:
        handle = console.std_handle(STD_OUTPUT_HANDLE)
        if handle in (None, 0, INVALID_HANDLE):
            return state
        mode = console.get_mode(handle)
        if mode is None:
            return state
        state.handle, state.mode = handle, mode
        if not mode & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            state.changed_mode = bool(console.set_mode(handle, mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except (OSError, AttributeError, ValueError, TypeError):
        pass
    return state


def set_utf8_output(console, state: ConsoleState) -> ConsoleState:
    """Console output code page 65001, remembering the old one."""
    if console is None:
        return state
    try:
        old = console.get_code_page()
        if old and old != CP_UTF8 and console.set_code_page(CP_UTF8):
            state.code_page, state.changed_page = old, True
    except (OSError, AttributeError, ValueError, TypeError):
        pass
    return state


def restore(console, state: ConsoleState) -> None:
    """Put the console mode and code page back; every step is tried, none raises."""
    if console is None or state is None:
        return
    if state.changed_mode:
        state.changed_mode = False
        try:
            console.set_mode(state.handle, state.mode)
        except Exception:
            pass
    if state.changed_page:
        state.changed_page = False
        try:
            console.set_code_page(state.code_page)
        except Exception:
            pass


def key_bytes(msvcrt) -> bytes:
    """Everything waiting in the console input, as the bytes a POSIX terminal would have sent. Two-step keys become ANSI sequences;
    an unknown two-step key is dropped; text goes out as UTF-8. Returns b"" when nothing is waiting."""
    out = bytearray()
    while msvcrt.kbhit():
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            code = msvcrt.getwch() if msvcrt.kbhit() else ""
            if char == "\x00" and code == "\x0f":
                out += _EXTENDED["\x0f"]
            else:
                out += _EXTENDED.get(code, b"")
            continue
        out += char.encode("utf-8", "replace")
    return bytes(out)


def wait_for_keys(msvcrt, deadline: float, clock=time.monotonic, sleep=time.sleep):
    """Poll until `deadline` (clock time) for input. Returns the bytes read (never empty), or None when the time ran out."""
    while True:
        data = key_bytes(msvcrt)
        if data:
            return data
        remaining = deadline - clock()
        if remaining <= 0:
            return None
        sleep(min(KEY_POLL_SECONDS, remaining))
