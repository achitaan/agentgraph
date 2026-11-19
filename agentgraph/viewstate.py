"""Interactive state of the live view, shared by the CLI and the renderer.

`__main__.py` reads keys, turns them into a new ViewState with `handle_key`,
and passes the state to `view.render(..., state=state)`. `view.py` draws it.
Neither needs to know how the other works. Pure functions, standard library
only.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

# "all" is the dashboard. Any other value gives that one panel the whole
# screen, so everything it holds can be read by scrolling.
PANELS = ("all", "agents", "tasks", "loops", "events", "issues")
PAGE = 10  # lines moved by a page key when the caller gives no page size

KEY_HELP = "1 agents  2 tasks  3 loops  4 events  5 issues  0 dashboard  j/k scroll  ? help  q quit"


@dataclass(frozen=True)
class ViewState:
    focus: str = "all"  # one of PANELS
    scroll: int = 0  # first visible line of the focused panel; the view clamps the top end
    help: bool = False  # show the key help


def handle_key(state: ViewState, key: str, page: int = PAGE) -> ViewState:
    """The state after one key. `key` is a single character or one of the
    names produced by `decode_keys`. Unknown keys leave the state unchanged."""
    page = max(1, int(page))
    if key in ("j", "down"):
        return replace(state, scroll=state.scroll + 1)
    if key in ("k", "up"):
        return replace(state, scroll=max(0, state.scroll - 1))
    if key in (" ", "pgdn"):
        return replace(state, scroll=state.scroll + page)
    if key in ("b", "pgup"):
        return replace(state, scroll=max(0, state.scroll - page))
    if key in ("g", "home"):
        return replace(state, scroll=0)
    if key in ("G", "end"):
        return replace(state, scroll=10**9)  # the view clamps to the last line
    if key in ("0", "a", "esc"):
        return replace(state, focus="all", scroll=0, help=False)
    if key in ("1", "2", "3", "4", "5"):
        return replace(state, focus=PANELS[int(key)], scroll=0)
    if key == "tab":
        return replace(state, focus=PANELS[(PANELS.index(state.focus) + 1) % len(PANELS)], scroll=0)
    if key == "shift-tab":
        return replace(state, focus=PANELS[(PANELS.index(state.focus) - 1) % len(PANELS)], scroll=0)
    if key == "?":
        return replace(state, help=not state.help)
    return state


def clamp(state: ViewState, last_line: int) -> ViewState:
    """Limit the scroll position to what the focused panel actually holds."""
    focus = state.focus if state.focus in PANELS else "all"
    scroll = min(max(0, state.scroll), max(0, int(last_line)))
    if focus == state.focus and scroll == state.scroll:
        return state
    return replace(state, focus=focus, scroll=scroll)


_SEQUENCES = {
    b"\x1b[A": "up", b"\x1b[B": "down", b"\x1bOA": "up", b"\x1bOB": "down",
    b"\x1b[5~": "pgup", b"\x1b[6~": "pgdn",
    b"\x1b[H": "home", b"\x1b[F": "end", b"\x1b[1~": "home", b"\x1b[4~": "end",
    b"\x1bOH": "home", b"\x1bOF": "end",
    b"\x1b[Z": "shift-tab",
}


def decode_keys(data: bytes) -> list[str]:
    """Split raw terminal input into key names. Known escape sequences become
    names ("up", "pgdn", ...), a lone ESC becomes "esc", tab becomes "tab",
    Ctrl-C becomes "ctrl-c", and other printable bytes become one-character
    strings. Unknown escape sequences and other control bytes are dropped."""
    keys: list[str] = []
    i = 0
    while i < len(data):
        if data[i] == 0x1B:
            for sequence, name in _SEQUENCES.items():
                if data.startswith(sequence, i):
                    keys.append(name)
                    i += len(sequence)
                    break
            else:
                if i + 1 < len(data) and data[i + 1] in (0x5B, 0x4F):  # unknown CSI / SS3: skip it
                    i += 2
                    while i < len(data) and not 0x40 <= data[i] <= 0x7E:
                        i += 1
                    i += 1
                else:
                    keys.append("esc")
                    i += 1
            continue
        byte = data[i]
        i += 1
        if byte == 0x09:
            keys.append("tab")
        elif byte == 0x03:
            keys.append("ctrl-c")
        elif 0x20 <= byte < 0x7F:
            keys.append(chr(byte))
    return keys
