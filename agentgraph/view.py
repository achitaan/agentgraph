"""Terminal and HTML views of an agentgraph Snapshot.

Standard library only. A frame is drawn into a canvas of cells (character,
foreground, background, bold). Every write is clipped to its own region, so text
cannot cross a border or the terminal width. Motion is a pure function of
snapshot.now. The interactive state (focus, scroll, help) comes from
viewstate.ViewState: the CLI owns the keys, this module owns what they show.
"""
from __future__ import annotations

import html
import math
import textwrap
import time
import unicodedata
from dataclasses import dataclass

from .contract import ESCALATION, GROUPS, GROUP_TITLES, POOL, Snapshot, group_of, validate
from .viewstate import KEY_HELP, PANELS, ViewState

Color = tuple[int, int, int]

# --- palette: a dark slate page, one live accent, one hue per concern ----------
BG: Color = (6, 11, 17)
PANEL: Color = (10, 19, 28)
CARD: Color = (16, 31, 42)
CARD_HOT: Color = (12, 66, 68)
SPINE: Color = (58, 92, 110)
RULE: Color = (36, 60, 75)
INK: Color = (230, 238, 242)
MUTED: Color = (128, 152, 164)
FAINT: Color = (66, 92, 106)
WHITE: Color = (255, 255, 255)
TEAL: Color = (72, 230, 200)     # running and build work
BLUE: Color = (118, 192, 255)    # understand, research, wave labels
PURPLE: Color = (190, 160, 255)  # decide, decisions, events
GREEN: Color = (130, 232, 160)   # verify, passed
ORANGE: Color = (255, 184, 98)   # the orchestrator hub
PINK: Color = (255, 122, 186)    # escalation edges, and nothing else
RED: Color = (255, 110, 110)     # failed and stalled: alarms only
AMBER: Color = (255, 214, 110)   # blocked, loop attention, data warnings
LIME: Color = (200, 242, 120)    # returned: claimed, not yet checked

GROUP_COLOR = {"hub": ORANGE, "understand": BLUE, "decide": PURPLE, "build": TEAL, "verify": GREEN}
LANE_GROUPS = ("understand", "decide", "build", "verify")
CANON = ("orchestrator", "explorer", "researcher", "oracle", "oracle-lite", "council",
         "councillor-fable", "councillor-astra", "councillor-carbon", "impl-openai", "impl-claude",
         "impl-hard", "designer", "reviewer")
STATUS_COLOR = {"pending": MUTED, "running": TEAL, "returned": LIME, "passed": GREEN,
                "failed": RED, "blocked": AMBER}
STATUS_GLYPH = {"pending": "○", "returned": "◎", "passed": "✓", "failed": "✗", "blocked": "×"}
VERDICT = {"confirmed": ("CONFIRMED", GREEN), "refuted": ("REFUTED", RED),
           "unverifiable": ("UNVERIFIABLE", AMBER)}
EVENT_COLOR = {"declared": MUTED, "dispatched": TEAL, "returned": LIME, "retried": AMBER,
               "escalated": PINK, "stalled": RED, "blocked": AMBER, "unblocked": GREEN,
               "joined": BLUE, "changed": PURPLE, "completed": GREEN, "attempt": AMBER,
               "invalidated": RED, "amended": PURPLE, "discovered": BLUE}
SPINNER = "◐◓◑◒"  # busy agents; these glyphs measure the same width as the rest of the frame
PACKET_SPEED = 7.0  # cells per second along a route
LEGEND_TASKS = (("✓ passed  ", GREEN), ("◎ returned  ", LIME), ("▶ running  ", TEAL),
                ("○ pending  ", MUTED), ("× blocked  ", AMBER), ("✗ failed  ", RED),
                ("! stalled", RED))
LEGEND_ROSTER = (("━━ ", ORANGE), ("handoff   ", MUTED), ("┈┈ ", PINK), ("escalation   ", MUTED),
                 ("● ", WHITE), ("work moving   ", MUTED), ("◐ ", TEAL), ("busy   ", MUTED),
                 ("* ", MUTED), ("not in config", MUTED))
FOCUS_TITLE = {"agents": "AGENTS", "tasks": "TASKS", "loops": "LOOPS", "events": "EVENTS",
               "issues": "ISSUES"}

# --- box-drawing edges: a cell's glyph is chosen from the arms that meet in it ------
N, E, S, W = 1, 2, 4, 8
LIGHT = {0: " ", N | S: "│", E | W: "─", E | S: "┌", W | S: "┐", N | E: "└", N | W: "┘",
         N | S | E: "├", N | S | W: "┤", E | W | S: "┬", E | W | N: "┴", N | S | E | W: "┼",
         N: "│", S: "│", E: "─", W: "─"}
HEAVY = {N | S: "┃", E | W: "━", E | S: "┏", W | S: "┓", N | E: "┗", N | W: "┛",
         N | S | E: "┣", N | S | W: "┫", E | W | S: "┳", E | W | N: "┻", N | S | E | W: "╋",
         N: "┃", S: "┃", E: "━", W: "━"}
DOTTED = {E | W: "┈", N | S: "┊"}
_DIR = {(1, 0): E, (-1, 0): W, (0, 1): S, (0, -1): N}
_KIND_RANK = {"stem": 0, "bus": 1, "esc": 2}

# Plain output is ASCII only, one character per cell, so widths can be checked
# with any tool regardless of locale.
_ASCII = {
    "━": "=", "┃": "|", "┏": "+", "┓": "+", "┗": "+", "┛": "+", "┣": "+", "┫": "+",
    "┳": "+", "┻": "+", "╋": "+", "─": "-", "│": "|", "┌": "+", "┐": "+", "└": "+",
    "┘": "+", "├": "+", "┤": "+", "┬": "+", "┴": "+", "┼": "+", "┈": "-", "┊": ":",
    "╭": "+", "╮": "+", "╰": "+", "╯": "+", "▌": "|", "▍": "|", "█": "#", "░": ".",
    "◈": "*", "●": "*", "◉": "*", "○": "o", "◐": "o", "▶": ">", "▷": ">", "◂": "<",
    "▲": "^", "▼": "v", "✓": "v", "✗": "x", "×": "x", "→": ">", "←": "<", "⇢": ">",
    "⇠": "<", "◇": "o", "◆": "*", "·": ".", "…": "~", "•": "*", "↳": ">",
    "◎": "O", "◓": "o", "◑": "o", "◒": "o", "◜": "(", "◝": ")", "◞": ")", "◟": "(",
    "—": "-", "–": "-", "½": "1",
}


# --- text: the single place where values become cells ---------------------------------


def _cell(ch: str) -> str:
    """One terminal cell for one character. Every character takes exactly one column:
    controls, DEL and format characters become spaces; unpaired surrogates, unassigned
    code points, and wide characters (emoji, CJK) become "?". Text from live data
    therefore cannot push a border out of line or break the HTML encoding."""
    code = ord(ch)
    if code < 128:
        return ch if 0x20 <= code < 0x7F else " "
    category = unicodedata.category(ch)
    if category in ("Cs", "Cn"):
        return "?"
    if category in ("Cc", "Cf", "Mn", "Me"):
        return " "
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return "?"
    return ch


def _ascii(ch: str) -> str:
    if ord(ch) < 128:
        return ch
    return _ASCII.get(ch, "?")


def _sgr(fg: Color, bg: Color, bold: bool) -> str:
    return (f"\x1b[0;{'1;' if bold else ''}38;2;{fg[0]};{fg[1]};{fg[2]};"
            f"48;2;{bg[0]};{bg[1]};{bg[2]}m")


def mix(a: Color, b: Color, t: float) -> Color:
    t = min(1.0, max(0.0, t))
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t),
            round(a[2] + (b[2] - a[2]) * t))


def _pulse(now: float, period: float = 1.8) -> float:
    """A 0..1 breathing curve. It depends only on the frame time."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * now / period)


def _s(value) -> str:
    """Text from any value: None is empty, anything else is its str()."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:  # a value whose __str__ fails must not stop the frame
        return ""


def _flat(value) -> str:
    return " ".join(_s(value).split())


def _short(value, width: int) -> str:
    """Text cut to at most `width` characters, with an ellipsis when it is cut."""
    text = _s(value)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    return text[: width - 1] + "…"


def _fit(value, width: int) -> str:
    """Exactly `width` characters: padded with spaces, or cut with an ellipsis."""
    if width <= 0:
        return ""
    return _short(value, width).ljust(width)


def _seq(value) -> list:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return []


def _int(value, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, number)


def _num(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _finite(value, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return number if math.isfinite(number) else fallback


# --- time: every conversion goes through these helpers ----------------------------------

_MIN_TIME, _MAX_TIME = 0.0, 4102444800.0  # Same frame-clock range as --at: 1970 through 2100.


def _time_or_none(value, *, zero_is_missing: bool = True):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not _MIN_TIME <= number <= _MAX_TIME:
        return None
    if zero_is_missing and number == 0:
        return None  # Graph/session timestamps use zero for an unrecorded time.
    return number


def _clock(value) -> float:
    """A valid frame clock, including epoch zero; invalid input freezes motion."""
    return _time_or_none(value, zero_is_missing=False) or 0.0


def _when(value, *, frame: bool = False) -> str:
    """Clock time of a unix timestamp, or an en dash when the time is missing or impossible."""
    moment = _time_or_none(value, zero_is_missing=not frame)
    if moment is None:
        return "–"
    try:
        return time.strftime("%H:%M:%S", time.localtime(moment))
    except (OverflowError, OSError, ValueError):
        return "–"


def _dur(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60:02}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60:02}s"
    return f"{seconds}s"


def _span(start, end) -> str:
    """Time between two timestamps, or an en dash when either one is missing."""
    a, b = _time_or_none(start), _time_or_none(end)
    if a is None or b is None or b < a:
        return "–"
    return _dur(b - a)


def _tok(n) -> str:
    n = _int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _bar(done, total, width: int):
    """A two-part progress bar as segments: filled, then empty."""
    done, total = _int(done), _int(total)
    if total <= 0:
        return [("—", FAINT)]
    filled = round(width * min(done, total) / total)
    return [("█" * filled, TEAL), ("░" * (width - filled), FAINT)]


# --- segments: (text, fg[, bold[, inverse]]) drawn left to right -----------------------


def _seg(seg):
    """Normalise a segment to (text, fg, bold, inverse)."""
    text, fg = _s(seg[0]), seg[1]
    bold = seg[2] if len(seg) > 2 else False
    inverse = seg[3] if len(seg) > 3 else False
    return text, fg, bold, inverse


def _clip_segments(segs, width: int):
    out, used = [], 0
    for seg in segs:
        if used >= width:
            break
        text = _s(seg[0])[: width - used]
        out.append((text,) + tuple(seg[1:]))
        used += len(text)
    return out


def _draw_segments(c, x: int, y: int, segs, bg=None) -> int:
    """Write segments left to right. Inverse segments are badges. Returns the end column."""
    cursor = x
    for seg in segs:
        text, fg, bold, inverse = _seg(seg)
        if inverse:
            c.put(cursor, y, text, BG, fg, True)
        else:
            c.put(cursor, y, text, fg, bg, bold)
        cursor += len(text)
    return cursor


def _write_segments(c, x: int, y: int, segs, limit: int, bg=None) -> None:
    kept, used = [], 0
    for seg in segs:
        if used >= limit:
            break
        text = _seg(seg)[0][: limit - used]
        kept.append((text,) + tuple(seg[1:]))
        used += len(text)
    _draw_segments(c, x, y, kept, bg)


def _write_right(c, y: int, segs) -> None:
    total = sum(len(_seg(s)[0]) for s in segs)
    _draw_segments(c, max(0, c.width - 2 - total), y, segs, BG)


# --- the canvas -------------------------------------------------------------------------


@dataclass
class Cell:
    ch: str = " "
    fg: Color = INK
    bg: Color = BG
    bold: bool = False


def polyline(points):
    """Expand axis-aligned corner points into the cells they pass through."""
    cells = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 != x1 and y0 != y1:
            raise ValueError("route segments must be axis-aligned")
        dx = (x1 > x0) - (x1 < x0)
        dy = (y1 > y0) - (y1 < y0)
        x, y = x0, y0
        while (x, y) != (x1, y1):
            x, y = x + dx, y + dy
            cells.append((x, y))
    return cells


class Canvas:
    """A grid of cells. Writes outside the active region are dropped.
    `last_line` is set by a focused panel: the greatest useful scroll value."""

    def __init__(self, width: int, height: int):
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.rows = [[Cell() for _ in range(self.width)] for _ in range(self.height)]
        self.content = [[False] * self.width for _ in range(self.height)]
        self.edges: dict[tuple[int, int], list] = {}
        self.arrows: dict[tuple[int, int], tuple[str, Color]] = {}
        self.boxes: list[tuple[int, int, int, int]] = []
        self.clip = (0, 0, self.width, self.height)
        self.last_line = 0
        self.page = 0

    # regions and raw cells -------------------------------------------------------
    def region(self, x: int, y: int, w: int, h: int) -> None:
        self.clip = (max(0, x), max(0, y), min(self.width, x + w), min(self.height, y + h))

    def _inside(self, x: int, y: int) -> bool:
        x0, y0, x1, y1 = self.clip
        return x0 <= x < x1 and y0 <= y < y1

    def _set(self, x: int, y: int, ch: str, fg: Color, bg, bold: bool = False) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            cell = self.rows[y][x]
            cell.ch, cell.fg, cell.bold = ch, fg, bold
            if bg is not None:
                cell.bg = bg
            self.content[y][x] = True

    # drawing -----------------------------------------------------------------------
    def put(self, x: int, y: int, text, fg: Color = INK, bg=None, bold: bool = False) -> None:
        if not 0 <= y < self.height:
            return
        for i, ch in enumerate(_s(text)):
            if self._inside(x + i, y):
                self._set(x + i, y, _cell(ch), fg, bg, bold)

    def border_text(self, x: int, y: int, segments, bg=None) -> None:
        """Write styled text on a border row, ignoring the content region."""
        saved = self.clip
        self.clip = (0, 0, self.width, self.height)
        for seg in segments:
            text, fg, bold, inverse = _seg(seg)
            for i, ch in enumerate(text):
                if inverse:
                    self._set(x + i, y, _cell(ch), BG, fg, True)
                else:
                    self._set(x + i, y, _cell(ch), fg, bg, bold)
            x += len(text)
        self.clip = saved

    def fill(self, x: int, y: int, w: int, h: int, bg: Color, content: bool = False) -> None:
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                if self._inside(xx, yy):
                    cell = self.rows[yy][xx]
                    cell.ch, cell.fg, cell.bg, cell.bold = " ", INK, bg, False
                    if content:
                        self.content[yy][xx] = True

    def box(self, x: int, y: int, w: int, h: int, title: str = "", accent: Color = RULE,
            bg: Color = PANEL, right: str = "") -> None:
        saved = self.clip
        self.clip = (0, 0, self.width, self.height)
        if w >= 4 and h >= 3:
            self.fill(x + 1, y + 1, w - 2, h - 2, bg)
            r, b = x + w - 1, y + h - 1
            for xx in range(x + 1, r):
                self._set(xx, y, "─", accent, bg)
                self._set(xx, b, "─", accent, bg)
            for yy in range(y + 1, b):
                self._set(x, yy, "│", accent, bg)
                self._set(r, yy, "│", accent, bg)
            self._set(x, y, "╭", accent, bg)
            self._set(r, y, "╮", accent, bg)
            self._set(x, b, "╰", accent, bg)
            self._set(r, b, "╯", accent, bg)
            self.boxes.append((x, y, w, h))
            label = f" {_flat(title)} " if title else ""
            tail = f" {_flat(right)} " if right else ""
            if label and len(label) + len(tail) + 6 <= w:
                self.border_text(x + 2, y, [(label, accent, True)], bg)
                if tail:
                    self.border_text(r - len(tail) - 1, y, [(tail, MUTED)], bg)
            elif label and len(label) + 6 <= w:
                self.border_text(x + 2, y, [(label, accent, True)], bg)
        self.clip = saved

    # edges, arrows and packets ---------------------------------------------------------
    def edge(self, x: int, y: int, bits: int, colour: Color, kind: str = "stem") -> None:
        """Add arms to a cell. Arms from several edges merge into one junction."""
        if not self._inside(x, y):
            return
        slot = self.edges.get((x, y))
        if slot is None:
            self.edges[(x, y)] = [bits, colour, kind]
            return
        slot[0] |= bits
        if _KIND_RANK[kind] >= _KIND_RANK[slot[2]]:
            slot[1], slot[2] = colour, kind

    def route(self, points, colour: Color, kind: str = "esc"):
        cells = polyline(points)
        for i, (x, y) in enumerate(cells):
            bits = 0
            for j in (i - 1, i + 1):
                if 0 <= j < len(cells):
                    nx, ny = cells[j]
                    bits |= _DIR[(nx - x, ny - y)]
            self.edge(x, y, bits, colour, kind)
        return cells

    def flush_edges(self) -> None:
        for (x, y), (bits, colour, kind) in self.edges.items():
            if self.content[y][x] or (x, y) in self.arrows:
                continue
            if kind == "esc" and bits in DOTTED:
                ch = DOTTED[bits]
            else:
                ch = (HEAVY if kind == "bus" else LIGHT).get(bits, "┼")
            cell = self.rows[y][x]
            # Never bold line glyphs: bold box-drawing falls back to a wider font.
            cell.ch, cell.fg, cell.bold = ch, colour, False
        for (x, y), (ch, colour) in self.arrows.items():
            if not self.content[y][x]:
                cell = self.rows[y][x]
                cell.ch, cell.fg, cell.bold = ch, colour, True

    def packet(self, x: int, y: int, colour: Color, glyph: str = "●", bold: bool = True) -> None:
        if not self._inside(x, y) or self.content[y][x] or (x, y) not in self.edges:
            return
        if (x, y) in self.arrows:
            return
        cell = self.rows[y][x]
        cell.ch, cell.fg, cell.bold = glyph, colour, bold

    def packets_along(self, path, t: float, colour: Color, count: int = 1) -> None:
        """Moving packets on a path. t is the travel position, in cells."""
        n = len(path)
        if n == 0:
            return
        for k in range(count):
            head = int(t + k * n / max(1, count)) % n
            for trail, glyph, fade in ((2, "·", 0.75), (1, "•", 0.4)):
                tx, ty = path[(head - trail) % n]
                self.packet(tx, ty, mix(colour, BG, fade), glyph, False)
            hx, hy = path[head]
            self.packet(hx, hy, mix(colour, WHITE, 0.45), "●", True)

    # output ------------------------------------------------------------------------------
    def rows_ansi(self, color: bool = True) -> list[str]:
        out = []
        for row in self.rows:
            parts, last = [], None
            for cell in row:
                if color:
                    key = (cell.fg, cell.bg, cell.bold)
                    if key != last:
                        parts.append(_sgr(cell.fg, cell.bg, cell.bold))
                        last = key
                    parts.append(cell.ch)
                else:
                    parts.append(_ascii(cell.ch))
            text = "".join(parts)
            out.append(text + "\x1b[0m" if color else text.rstrip())
        return out

    def plain(self) -> str:
        return "\n".join(self.rows_ansi(color=False))

    def to_html(self) -> str:
        chunks = []
        for row in self.rows:
            spans, run, text = [], None, []
            for cell in row:
                key = (cell.fg, cell.bg, cell.bold)
                if run is not None and key != run:
                    spans.append(self._span("".join(text), run))
                    text = []
                run = key
                text.append(cell.ch)
            if text:
                spans.append(self._span("".join(text), run))
            chunks.append("".join(spans))
        return "\n".join(chunks)

    @staticmethod
    def _span(text: str, style) -> str:
        fg, bg, bold = style
        weight = "700" if bold else "400"
        return (f'<span style="color:rgb({fg[0]},{fg[1]},{fg[2]});'
                f'background:rgb({bg[0]},{bg[1]},{bg[2]});font-weight:{weight}">'
                f"{html.escape(text)}</span>")


# --- what a frame needs, normalised once ------------------------------------------------
# Snapshot data comes from files and a database and may be incomplete. Everything the
# view reads is copied into plain records below. Identifiers, paths and commands retain
# their original text; only None/numeric/time handling is normalised here. Character
# safety belongs to _cell(), and validation always sees the original graph.


@dataclass
class _N:
    """A task node, normalised. `index` is its position in the graph."""
    index: int
    id: str
    type: str
    agent: str
    goal: str
    status: str
    deps: list
    scope: list
    done_when: str
    attempts: int
    tiers: list
    evidence: str
    baseline: str
    outputs: str
    changed: list
    failure: str
    stalled: bool
    verdict: str
    wave: int = 1
    pos: int = 0
    wave_size: int = 1


@dataclass
class _E:
    time: float | None
    node: str
    event: str
    note: str


@dataclass
class _O:
    id: str
    text: str
    nodes: list


@dataclass
class _S:
    """One OpenCode session, normalised."""
    id: str
    agent: str
    title: str
    model: str
    effort: str
    busy: bool
    updated: float | None
    cost: float
    tin: int
    tout: int
    root: bool


@dataclass
class _A:
    """One agent of the roster. It is drawn only if the configuration, a session or the
    graph names it (`sources`); `configured` says whether the configuration does."""
    name: str
    group: str
    pool: int | None = None
    model: str = ""
    effort: str = ""
    configured: bool = False
    model_from_sessions: bool = False
    sources: set = None
    sessions: list = None
    busy: int = 0

    def __post_init__(self):
        self.sources = set() if self.sources is None else self.sources
        self.sessions = [] if self.sessions is None else self.sessions


@dataclass
class _G:
    """A task graph as the view reads it. Derived once per graph version."""
    task: str
    goal: str
    status: str
    created: float | None
    updated: float | None
    revision: int
    nodes: list
    by_id: dict
    flat: list           # nodes in wave order, the order of the spine
    events: list
    objectives: list     # (_O, state), state is delivered, partial or open
    problems: list       # validate(), or the reason it could not run
    passed: int
    coverage: tuple      # (delivered, total) objectives
    loops: list          # nodes with loop data, most urgent first
    not_started: list    # nodes with no loop data yet
    handoffs: set        # (from, to) pairs seen in the tiers of this graph


def _node(raw, index: int) -> _N:
    return _N(index=index,
              id=_s(getattr(raw, "id", "")),
              type=_s(getattr(raw, "type", "")),
              agent=_s(getattr(raw, "agent", "")),
              goal=_s(getattr(raw, "goal", "")),
              status=_s(getattr(raw, "status", "")),
              deps=[_s(d) for d in _seq(getattr(raw, "depends_on", None))],
              scope=[_s(p) for p in _seq(getattr(raw, "scope", None))],
              done_when=_s(getattr(raw, "done_when", "")),
              attempts=_int(getattr(raw, "attempts", 0)),
              tiers=[_s(t) for t in _seq(getattr(raw, "tiers", None))],
              evidence=_s(getattr(raw, "evidence", "")),
              baseline=_s(getattr(raw, "baseline", "")),
              outputs=_s(getattr(raw, "outputs", "")),
              changed=[_s(p) for p in _seq(getattr(raw, "changed_paths", None))],
              failure=_s(getattr(raw, "failure_kind", "")),
              stalled=bool(getattr(raw, "stalled", False)),
              verdict=_s(getattr(raw, "verdict", "")))


def _event(raw) -> _E:
    return _E(time=_time_or_none(getattr(raw, "time", None)),
              node=_s(getattr(raw, "node", "")),
              event=_s(getattr(raw, "event", "")),
              note=_s(getattr(raw, "note", "")))


def _objective(raw) -> _O:
    return _O(id=_s(getattr(raw, "id", "")), text=_s(getattr(raw, "text", "")),
              nodes=[_s(n) for n in _seq(getattr(raw, "nodes", None))])


def _session(raw, root: bool = False) -> _S:
    return _S(id=_s(getattr(raw, "id", "")),
              agent=_s(getattr(raw, "agent", "")),
              title=_s(getattr(raw, "title", "")),
              model=_s(getattr(raw, "model", "")),
              effort=_s(getattr(raw, "effort", "")),
              busy=bool(getattr(raw, "busy", False)),
              updated=_time_or_none(getattr(raw, "updated", None)),
              cost=_num(getattr(raw, "cost", 0.0)),
              tin=_int(getattr(raw, "tokens_in", 0)),
              tout=_int(getattr(raw, "tokens_out", 0)),
              root=root)


def _has_loop_data(n: _N) -> bool:
    """A node has loop data once it has started, or has any recorded loop field.
    A planned node with only a goal and a done_when has none yet."""
    return (n.status != "pending" or n.attempts > 0 or bool(n.tiers) or bool(n.evidence)
            or bool(n.baseline) or n.stalled or bool(n.failure) or bool(n.verdict)
            or bool(n.outputs) or bool(n.changed))


_LOOP_RANK = {"blocked": 1, "returned": 2, "running": 3}


def _loop_rank(n: _N) -> int:
    """Most urgent first: stalled or failed; blocked; returned (it waits for the orchestrator's
    check, so it needs action now); running (work in progress); everything else."""
    if n.stalled or n.status == "failed":
        return 0
    return _LOOP_RANK.get(n.status, 4)


def _layers(nodes: list) -> list:
    """Dependency waves: wave 0 has no dependencies, wave n depends on earlier waves.
    Unknown dependencies are ignored; nodes in a cycle form one last wave."""
    known = {n.id for n in nodes}
    placed: set = set()  # ids that are already in an earlier wave
    layers: list = []
    remaining = list(nodes)
    while remaining:
        ready = [n for n in remaining if all(d in placed or d not in known for d in n.deps)]
        if not ready:
            layers.append(remaining)
            break
        layers.append(ready)
        placed.update(n.id for n in ready)
        taken = {id(n) for n in ready}  # by identity: two nodes may share an id, and both stay
        remaining = [n for n in remaining if id(n) not in taken]
    return layers


def _build_graph(graph) -> _G:
    nodes = [_node(raw, i) for i, raw in enumerate(_seq(getattr(graph, "nodes", None)))]
    by_id: dict = {}
    for n in nodes:
        by_id.setdefault(n.id, n)
    flat: list = []
    for wave_no, layer in enumerate(_layers(nodes), 1):
        for pos, n in enumerate(layer):
            n.wave, n.pos, n.wave_size = wave_no, pos, len(layer)
            flat.append(n)
    events = [_event(raw) for raw in _seq(getattr(graph, "events", None))]
    objectives_raw = [_objective(raw) for raw in _seq(getattr(graph, "objectives", None))]
    status_of = {n.id: n.status for n in nodes}
    objectives = []
    for o in objectives_raw:
        if o.nodes and all(status_of.get(i) == "passed" for i in o.nodes):
            state = "delivered"
        elif any(status_of.get(i) == "passed" for i in o.nodes):
            state = "partial"
        else:
            state = "open"
        objectives.append((o, state))
    delivered = sum(1 for _, state in objectives if state == "delivered")
    task = _s(getattr(graph, "task", ""))
    status = _s(getattr(graph, "status", ""))
    revision = _int(getattr(graph, "revision", 1), 1)
    problems: list = []
    try:
        problems = [_s(message) for message in validate(graph)]
    except Exception as exc:  # a malformed graph is reported, never fatal
        problems = [f"could not check the graph ({_short(exc, 80)})"]
    handoffs = {(a, b) for n in nodes for a, b in zip(n.tiers, n.tiers[1:])}
    return _G(task=task, goal=_s(getattr(graph, "goal", "")), status=status,
              created=_time_or_none(getattr(graph, "created", None)),
              updated=_time_or_none(getattr(graph, "updated", None)),
              revision=revision, nodes=nodes, by_id=by_id, flat=flat, events=events,
              objectives=objectives, problems=problems,
              passed=sum(1 for n in nodes if n.status == "passed"),
              coverage=(delivered, len(objectives)),
              loops=sorted((n for n in nodes if _has_loop_data(n)), key=_loop_rank),
              not_started=[n for n in nodes if not _has_loop_data(n)],
              handoffs=handoffs)


_DERIVED: dict = {}


def _fingerprint_value(value):
    """An immutable copy of containers, retaining scalar text without normalising it."""
    if isinstance(value, (list, tuple)):
        return tuple(_fingerprint_value(item) for item in value)
    if isinstance(value, dict):
        return tuple((key, _fingerprint_value(item)) for key, item in value.items())
    return value


def _record_stamp(record, fields):
    return tuple(_fingerprint_value(getattr(record, name, None)) for name in fields)


_NODE_FIELDS = ("id", "type", "agent", "goal", "status", "depends_on", "scope", "done_when",
                "attempts", "tiers", "evidence", "session", "started", "finished", "baseline",
                "outputs", "changed_paths", "failure_kind", "stalled", "verdict")


def _graph_stamp(graph) -> tuple:
    # Linear, cheap field comparisons catch in-place edits even without an updated bump.
    # Include validation inputs too: a changed path or command must never reuse a verdict.
    return (_record_stamp(graph, ("path", "task", "goal", "status", "created", "updated", "revision")),
            tuple(_record_stamp(n, _NODE_FIELDS) for n in _seq(getattr(graph, "nodes", None))),
            tuple(_record_stamp(o, ("id", "text", "nodes")) for o in _seq(getattr(graph, "objectives", None))),
            tuple(_record_stamp(e, ("time", "node", "event", "note")) for e in _seq(getattr(graph, "events", None))))


def _derive_graph(graph) -> _G:
    """The derived view of a graph, computed once per graph version. The cache holds the
    graph itself, so its identity cannot be reused while the entry is alive."""
    stamp = _graph_stamp(graph)
    cached = _DERIVED.get(id(graph))
    if cached is not None and cached[0] is graph and cached[1] == stamp:
        return cached[2]
    value = _build_graph(graph)
    if len(_DERIVED) >= 8:
        _DERIVED.clear()
    _DERIVED[id(graph)] = (graph, stamp, value)
    return value


# --- the roster: only what the configuration, the sessions or the graph names -----------

_GROUP_RANK = {"understand": 0, "decide": 1, "build": 2, "verify": 3}


def _canon_key(agent: _A):
    index = CANON.index(agent.name) if agent.name in CANON else len(CANON)
    return (_GROUP_RANK.get(agent.group, 9), index, agent.name)


def _roster(snap: Snapshot, graph, sessions: list, root):
    """Who is on the roster. The hub is the root session's agent. Any other agent is drawn
    only if the configuration lists it, one of its sessions is running, or the graph names
    it. A session of a drawn agent fills in its model and effort when the configuration has
    none."""
    agents: dict[str, _A] = {}
    config: dict[str, dict] = {}

    def get(name: str) -> _A:
        if name not in agents:
            agents[name] = _A(name=name, group=group_of(name), pool=POOL.get(name))
        return agents[name]

    for raw in _seq(snap.roster):
        name = _s(getattr(raw, "name", ""))
        if not name:
            continue
        a = get(name)
        a.configured = True
        a.sources.add("config")
        group = _flat(getattr(raw, "group", ""))
        a.group = group if group in GROUPS else group_of(name)
        pool = _int(getattr(raw, "pool", None), 0)
        a.pool = pool or POOL.get(name)
        config[name] = {"model": _flat(getattr(raw, "model", "")),
                        "effort": _flat(getattr(raw, "effort", ""))}
        a.model = config[name]["model"]
        a.effort = config[name]["effort"]
    if root is not None and root.agent:
        hub_name = root.agent
    else:
        configured_hub = [a.name for a in agents.values() if a.configured and a.group == "hub"]
        hub_name = configured_hub[0] if configured_hub else "orchestrator"
    hub = get(hub_name)  # created before any session is attached, so a busy root always counts
    hub.group = "hub"
    hub.pool = None
    for s in sessions:  # a running session puts its agent on the roster, configured or not
        if s.agent and s.busy:
            get(s.agent).sources.add("session")
    if graph is not None:
        for n in graph.nodes:
            for name in [n.agent, *n.tiers]:
                if name and name != "user":
                    get(name).sources.add("graph")
    for s in sorted(sessions, key=lambda x: x.updated or 0.0):  # newest last: it wins
        owner = hub if s.root else (agents.get(s.agent) if s.agent else None)
        if owner is None:  # an idle session of an agent that nothing else names is not drawn
            continue
        owner.sessions.append(s)
        if s.busy:
            owner.busy += 1
        if not config.get(owner.name, {}).get("model") and s.model:
            owner.model, owner.model_from_sessions = s.model, True
        if not config.get(owner.name, {}).get("effort") and s.effort:
            owner.effort = s.effort
    others = [a for a in agents.values() if a is not hub and a.sources]
    for a in others:
        if a.group not in LANE_GROUPS:
            a.group = "decide" if a.group == "hub" else "build"
    others.sort(key=_canon_key)
    groups = {g: [a for a in others if a.group == g] for g in LANE_GROUPS}
    return hub, groups, others


@dataclass
class _Ctx:
    snap: Snapshot
    now: float               # finite: the animation and the clock use this
    source: str
    hint: str
    state: ViewState
    graph: object            # _G or None
    hub: _A
    groups: dict             # group name -> [_A], display order
    agents: list             # every agent but the hub, display order
    sessions: list           # root first, then the child sessions
    root: object             # _S or None: decided by snapshot.root alone
    running: int             # busy sessions, root included
    warnings: list


def _context(snap: Snapshot, source: str, hint: str, state: ViewState) -> _Ctx:
    root = _session(snap.root, root=True) if snap.root is not None else None
    children = []
    for raw in _seq(snap.sessions):
        if raw is None:
            continue
        raw_id = _s(getattr(raw, "id", ""))
        if root is not None and raw_id and raw_id == root.id:  # the root may also be listed
            continue
        children.append(_session(raw))
    sessions = ([root] if root is not None else []) + children
    first = next((g for g in _seq(snap.graphs) if g is not None), None)
    graph = _derive_graph(first) if first is not None else None
    hub, groups, others = _roster(snap, graph, sessions, root)
    return _Ctx(snap=snap, now=_clock(snap.now), source=_s(source), hint=_s(hint), state=state,
                graph=graph, hub=hub, groups=groups, agents=others, sessions=sessions,
                root=root, running=sum(1 for s in sessions if s.busy),
                warnings=[_flat(w) for w in _seq(snap.warnings) if _flat(w)])


def _has_hub(ctx: _Ctx) -> bool:
    return ctx.root is not None or bool(ctx.hub.sources)


def _agent_count(ctx: _Ctx) -> int:
    return len(ctx.agents) + int(_has_hub(ctx))


def _known_escalations(ctx: _Ctx):
    names = {a.name for a in ctx.agents}
    if _has_hub(ctx):
        names.add(ctx.hub.name)
    return [(a, b) for a, b in ESCALATION if a in names and b in names]


def _no_roster(c: Canvas, ctx: _Ctx, x: int, y: int, w: int, h: int) -> bool:
    if _agent_count(ctx):
        return False
    c.box(x, y, w, h, "AGENT GRAPH", TEAL, PANEL, right="0 agents")
    c.region(x + 1, y + 1, w - 2, h - 2)
    c.put(x + 2, y + 1, "No roster available", MUTED, PANEL, True)
    if h > 4:
        c.put(x + 2, y + 2, _short("No configured agents, running sessions or graph-named agents.", w - 4), MUTED, PANEL)
        c.put(x + 2, y + 3, "No root session selected.", FAINT, PANEL)
    return True


# --- node vocabulary: status is always the real status; stalled is its own marker ---------


def _glyph(n: _N, now: float) -> str:
    if n.status == "running":
        return "▶" if int(now * 2) % 2 == 0 else "▷"
    return STATUS_GLYPH.get(n.status, "?")


def _label(n: _N) -> str:
    return n.status.upper()


def _status_colour(n: _N) -> Color:
    return STATUS_COLOR.get(n.status, MUTED)


def _alarm(n: _N) -> bool:
    """Failing or stalled work. Blocked is amber: waiting, not failing."""
    return n.stalled or n.status == "failed"


def _try_colour(n: _N) -> Color:
    if _alarm(n):
        return RED
    if n.status == "blocked":
        return AMBER
    if n.attempts > 1 and n.status != "passed":
        return AMBER
    return MUTED


def _value_colour(n: _N) -> Color:
    if _alarm(n):
        return RED
    return {"passed": GREEN, "returned": LIME, "blocked": AMBER, "running": INK}.get(n.status, MUTED)


def _goal_colour(n: _N) -> Color:
    if n.status in ("running", "returned") or _alarm(n):
        return INK
    return {"pending": FAINT}.get(n.status, MUTED)


def _row_bg(n: _N, now: float):
    if _alarm(n):
        return mix(PANEL, RED, 0.16)
    if n.status == "running":
        return mix(PANEL, TEAL, 0.05 + 0.05 * _pulse(now))
    return None


def _verdict(n: _N):
    return VERDICT.get(n.verdict, (n.verdict.upper(), MUTED))


def _tier_segs(n: _N):
    if not n.tiers:
        return [(f"  {'tiers':<{_LABEL_W}}", FAINT), ("—", MUTED)]
    segs = [(f"  {'tiers':<{_LABEL_W}}", FAINT)]
    for i, tier in enumerate(n.tiers):
        if i:
            segs.append((" ⇢ ", PINK, True))
        segs.append((tier, INK))
    if len(n.tiers) > 1:
        segs.append(("  escalated", PINK, True))
    return segs


def _flags_segs(n: _N):
    segs = [(f"  {'flags':<{_LABEL_W}}", FAINT)]
    if n.stalled:
        segs += [(" STALLED ", RED, True, True), ("  ", MUTED)]
    if n.failure:
        segs += [(f"{n.failure} failure  ", RED)]
    if n.verdict:
        name, colour = _verdict(n)
        segs += [(f"verdict {name.lower()}", colour)]
    return segs


_LABEL_W = 9  # every labelled row: two spaces, a 9-column label, then the value


def _label_rows(label: str, text, fg: Color, width: int, lines=None):
    """A labelled value, wrapped under its label. `lines` caps the height; a cut
    value ends with an ellipsis rather than silently losing its end."""
    lead = f"{label:<{_LABEL_W}}"
    body = max(8, width - len(lead) - 2)
    wrapped = textwrap.wrap(_s(text), body, expand_tabs=False,
                            replace_whitespace=False, drop_whitespace=False) or [""]
    if lines is not None and len(wrapped) > lines:
        wrapped = wrapped[:lines]
        wrapped[-1] = _short(wrapped[-1] + " …", body)
    return [[("  " + (lead if i == 0 else " " * _LABEL_W), FAINT), (line, fg)]
            for i, line in enumerate(wrapped)]


def _loop_header(n: _N, now: float):
    colour = _status_colour(n)
    segs = [("▍ ", colour, True), (f"{n.id}  ", INK, True),
            (f"{_glyph(n, now)} {_label(n)}", colour, True)]
    if n.stalled:
        segs.append(("  STALLED ", RED, True, True))
    segs.append((f"  {n.type} · {n.agent or '—'}", MUTED))
    if n.attempts:
        segs.append((f"  try {n.attempts}", _try_colour(n)))
    return segs


def _loop_fields(n: _N, width: int, show_empty: bool):
    """Every recorded loop field of a node. Empty fields are left out on the dashboard
    and shown as a dash in the loops focus, where nothing may be missing."""
    rows = []
    if show_empty:
        rows.append([(f"  {'tries':<{_LABEL_W}}", FAINT), (str(n.attempts), INK)])
    if n.tiers or show_empty:
        rows.append(_tier_segs(n) if show_empty else _clip_segments(_tier_segs(n), width))
    if n.done_when or show_empty:
        rows += _label_rows("done", n.done_when or "—", MUTED, width)
    if n.baseline or show_empty:
        rows += _label_rows("base", n.baseline or "—", MUTED, width)
    rows += _label_rows("latest", n.evidence or "waiting for evidence", _value_colour(n), width)
    if n.stalled or show_empty:
        rows += _label_rows("stalled", "yes: attempts repeat without new evidence" if n.stalled else "no",
                            RED if n.stalled else MUTED, width)
    if n.failure or show_empty:
        rows += _label_rows("failure", n.failure or "—", RED if n.failure else MUTED, width)
    if n.verdict or show_empty:
        name, colour = _verdict(n) if n.verdict else ("—", MUTED)
        rows += _label_rows("verdict", name, colour, width)
    if n.outputs or show_empty:
        rows += _label_rows("gives", n.outputs or "—", MUTED, width)
    if n.changed or show_empty:
        rows += _label_rows("files", ", ".join(n.changed) or "—", MUTED, width)
    return rows


def _loop_card(n: _N, width: int, level: int, focus: bool = False, now: float = 0.0):
    """Rows of one loop card. The dashboard uses level 0 (header), 1 (latest evidence),
    2 (tiers, latest, flags) or 3 (every field that has a value). The loops focus always
    shows every field."""
    head = [_loop_header(n, now)]
    if focus:
        return head + _loop_fields(n, width, True)
    if level <= 0:
        return head
    if level == 1:
        return head + _label_rows("latest", n.evidence or "waiting for evidence",
                                  _value_colour(n), width, lines=2)
    if level == 2:
        rows = []
        if n.tiers:
            rows.append(_clip_segments(_tier_segs(n), width))
        if n.done_when:
            rows += _label_rows("done", n.done_when, MUTED, width, lines=1)
        if n.baseline:
            rows += _label_rows("base", n.baseline, MUTED, width, lines=1)
        rows += _label_rows("latest", n.evidence or "waiting for evidence", _value_colour(n),
                            width, lines=3)
        if n.stalled or n.failure or n.verdict:
            rows.append(_clip_segments(_flags_segs(n), width))
        return head + rows
    return head + _loop_fields(n, width, False)


# Detail is granted in passes. Each pass raises the nodes it covers to the level it names,
# in priority order, wherever the rows still fit: the work that needs action first
# (stalled, failed, blocked, returned), then running work, then the latest evidence for
# everyone, then the middle card as space allows. A node that loses detail keeps its
# header, so none vanishes.
_PASSES = ((3, lambda rank: rank <= 2), (2, lambda rank: rank <= 3),
           (1, lambda rank: True), (2, lambda rank: True))


def _pack_loops(loops: list, width: int, limit: int, now: float):
    """Loop cards that fit `limit` rows, as [(node, level)], plus how many are hidden.
    Only when even the headers do not fit are the least urgent nodes left out, and the
    caller names them."""
    if not loops:
        return [], 0

    limit = max(0, limit)
    count = len(loops) if len(loops) <= limit else max(0, limit - 1)
    levels = [0] * count  # Every level-zero card is exactly one header row.
    lengths = [1] * count
    used = count
    hidden = len(loops) - count
    if count == 0:
        return [], hidden
    reserve = 1 if hidden else 0
    for target, wanted in _PASSES:
        for i in range(count):
            if levels[i] >= target or not wanted(_loop_rank(loops[i])):
                continue
            size = len(_loop_card(loops[i], width, target, now=now))
            extra = size - lengths[i]
            if used + extra <= limit - reserve:
                levels[i], lengths[i] = target, size
                used += extra
    return [(loops[i], levels[i]) for i in range(count)], hidden


# --- the agent graph -------------------------------------------------------------------

LANE_LABEL = 16                                             # spine, connector and group label
LANE_CHIP = {"cards": (26, 2), "wide": (38, 1), "text": (24, 1)}  # (width, rows per chip)
_ESC_OUT = {src for src, _ in ESCALATION}  # ⇢ : work escalates from here
_ESC_IN = {dst for _, dst in ESCALATION}   # ⇠ : work escalates to here


def _esc_mark(name: str) -> str:
    marks = ("⇠" if name in _ESC_IN else "") + ("⇢" if name in _ESC_OUT else "")
    return f" {marks}" if marks else ""


def _chip_state(a: _A, ctx: _Ctx, w: int, rows: int) -> str:
    spin = SPINNER[int(ctx.now * 10) % len(SPINNER)]
    if rows > 1:
        text = f"{spin} {a.busy} busy" if a.busy else "○ idle"
    elif w >= 34:
        text = f"{spin} {a.busy} busy" if a.busy else "○"
    else:
        text = f"{spin} {a.busy}" if a.busy else "○"
    if len(text) > max(1, w - 6):
        text = f"{spin}{a.busy}" if a.busy else "○"
    return text


def _chip(c: Canvas, x: int, y: int, w: int, a: _A, ctx: _Ctx, colour: Color, rows: int) -> None:
    """One agent as a node card, drawn inside its own rectangle. Busy cards glow."""
    p = _pulse(ctx.now)
    bg = mix(CARD, CARD_HOT, 0.4 + 0.6 * p) if a.busy else CARD
    c.fill(x, y, w, rows, bg, content=True)
    bar = mix(TEAL, WHITE, 0.45 * p) if a.busy else mix(colour, CARD, 0.45)
    for k in range(rows):
        c.put(x, y + k, "▌", bar, bg)
    state = _chip_state(a, ctx, w, rows)
    pool = f" ×{a.pool}" if a.pool else ""
    unlisted = "" if a.configured else "*"
    room = w - len(state) - 4
    mark = _esc_mark(a.name) if rows == 1 and w < 34 else ""
    base = _short(f"{a.name}{pool}{unlisted}", room - len(mark))
    c.put(x + 2, y, base, INK, bg, True)
    if mark:
        c.put(x + 2 + len(base), y, mark, PINK, bg, True)
    name_end = x + 2 + len(base) + len(mark)
    c.put(x + w - len(state) - 1, y, state, TEAL if a.busy else FAINT, bg, bool(a.busy))
    model = _s(a.model) or "model not reported"
    effort = _short(a.effort, max(0, w // 3)) if a.effort else ""
    if rows > 1:
        room = w - 3 - (len(effort) + 1 if effort else 0)
        c.put(x + 2, y + 1, _short(model, room), MUTED, bg)
        if effort:
            c.put(x + w - len(effort) - 1, y + 1, effort, FAINT, bg)
    elif w >= 34 and a.model:
        start = name_end + 2
        room = x + w - len(state) - 2 - start + 1
        text = f"{a.model} · {a.effort}" if a.effort else a.model
        if a.effort and len(text) > room:
            text = f"{a.model}/{a.effort}"
        if room > 6:
            c.put(start, y, _short(text, room), MUTED, bg)


def _hub_box(c: Canvas, x: int, y: int, w: int, ctx: _Ctx) -> None:
    """The orchestrator: a heavy box whose right edge carries the bus."""
    hub = ctx.hub
    busy = hub.busy
    p = _pulse(ctx.now)
    frame = mix(ORANGE, WHITE, 0.35 * p) if busy else mix(ORANGE, BG, 0.2)
    bg = mix(PANEL, ORANGE, 0.08 + 0.07 * p) if busy else PANEL
    c.fill(x + 1, y + 1, w - 2, 3, bg, content=True)
    for xx in range(x + 1, x + w - 1):
        c._set(xx, y, "━", frame, bg)
        c._set(xx, y + 4, "━", frame, bg)
    for yy in range(y + 1, y + 4):
        c._set(x, yy, "┃", frame, bg)
        c._set(x + w - 1, yy, "┃", frame, bg)
    c._set(x, y, "┏", frame, bg)
    c._set(x + w - 1, y, "┓", frame, bg)
    c._set(x, y + 4, "┗", frame, bg)
    c._set(x + w - 1, y + 4, "┛", frame, bg)
    c._set(x + w - 1, y + 2, "┫", frame, bg)  # the bus leaves from this row
    title = ("ORCHESTRATOR" if hub.name == "orchestrator" else hub.name.upper()) if _has_hub(ctx) else "ROOT NOT SELECTED"
    c.put(x + 2, y + 1, _short(f"◈ {title}", w - 4), ORANGE, bg, True)
    if ctx.root is None:  # decided by snapshot.root, not by the model field
        c.put(x + 2, y + 2, "no root session", MUTED, bg)
    else:
        c.put(x + 2, y + 2, _short(hub.model or "model not reported", w - 4),
              INK if hub.model else MUTED, bg)
    effort = f"{hub.effort} · " if hub.effort else ""
    spin = SPINNER[int(ctx.now * 10) % len(SPINNER)]
    state = f"{spin} {busy} working" if busy else "○ idle"
    c.put(x + 2, y + 3, _short(effort + state, w - 4), TEAL if busy else FAINT, bg, bool(busy))


def _member_path(bus_from: int, stem: int, col: int, by: int, top: int):
    """The cells a packet travels: along the bus, down the stem, into the card."""
    cells = [(x, by) for x in range(bus_from, stem + 1)]
    cells += [(stem, y) for y in range(by + 1, top + 1)]
    cells += [(x, top) for x in range(stem + 1, col)]
    return cells


def _roster_graph(c: Canvas, ctx: _Ctx, x0: int, y0: int, w: int, h: int) -> None:
    """Hub on the left, a heavy bus to four group stems, one card per agent. Escalation
    runs as dotted pink routes between the build cards; only a route that a node's tiers
    show is animated."""
    if _no_roster(c, ctx, x0, y0, w, h):
        return
    c.box(x0, y0, w, h, "AGENT GRAPH  ·  live roster", TEAL, PANEL,
          right=f"{_agent_count(ctx)} agents · {ctx.running} sessions running")
    c.region(x0 + 1, y0 + 1, w - 2, h - 2)
    ix, iy = x0 + 2, y0 + 1
    right = x0 + w - 3
    hub_w = 26
    by = iy + 2          # the bus runs along the hub's middle row
    first = iy + 3       # first card row under the group labels
    gap = 6
    col0 = ix + hub_w + 3
    cw = min(28, (right - col0 + 1 - 3 * gap) // 4)
    cols = [col0 + j * (cw + gap) for j in range(4)]
    stems = [col - 2 for col in cols]
    fit = max(0, (y0 + h - 1 - first) // 2)  # complete two-row cards, excluding the border
    _hub_box(c, ix, iy, hub_w, ctx)
    bus_from = ix + hub_w
    for xx in range(bus_from, stems[-1]):
        c.edge(xx, by, E | W, ORANGE, "bus")
    c.edge(stems[-1], by, W, ORANGE, "bus")
    cards: dict[str, tuple[int, int]] = {}
    shown_by_group: dict[str, list] = {}
    for j, grp in enumerate(LANE_GROUPS):
        col, stem = cols[j], stems[j]
        colour = GROUP_COLOR[grp]
        members = ctx.groups[grp]
        busy_n = sum(a.busy for a in members)
        c.put(col, iy + 1, _short(f"{GROUP_TITLES[grp].upper()} · {busy_n} busy", cw), colour, PANEL, True)
        if not members:
            c.put(col, first, "none seen", FAINT, PANEL)
            continue
        show = members if len(members) <= fit else members[: max(0, fit - 1)]
        hidden = len(members) - len(show)
        shown_by_group[grp] = show
        if show:
            c.edge(stem, by, S, colour, "stem")
            last_top = first + 2 * (len(show) - 1)
            for yy in range(by + 1, last_top + 1):
                c.edge(stem, yy, N | (S if yy < last_top else 0), colour, "stem")
            for k, a in enumerate(show):
                top = first + 2 * k
                c.edge(stem, top, E, colour, "stem")
                for xx in range(stem + 1, col):
                    c.edge(xx, top, E | W, colour, "stem")
                _chip(c, col, top, cw, a, ctx, colour, 2)
                cards[a.name] = (col, top)
        if hidden:
            c.put(col, first + 2 * len(show), _short(f"+{hidden} more · agents focus (1)", cw),
                  MUTED, PANEL)

    def top_of(name: str):
        spot = cards.get(name)
        return spot[1] if spot else None

    # Escalation: dotted pink routes. impl-openai hands to impl-claude, which hands to
    # impl-hard, which escalates back west to oracle.
    ex = cols[2] + cw          # first free cell east of the build column
    va, vc = ex + 2, ex + 1
    wa = cols[1] + cw          # first free cell west of the build column
    vd = wa + 2
    first_tier, second = top_of("impl-openai"), top_of("impl-claude")
    hard, oracle = top_of("impl-hard"), top_of("oracle")
    routes = []
    if first_tier is not None and second is not None:
        routes.append((("impl-openai", "impl-claude"),
                       [(ex, first_tier), (va, first_tier), (va, second), (ex, second)]))
    if second is not None and hard is not None:
        routes.append((("impl-claude", "impl-hard"), [(ex, second + 1), (vc, second + 1), (vc, hard), (ex, hard)]))
    if hard is not None and oracle is not None:
        routes.append((("impl-hard", "oracle"),
                       [(cols[2] - 1, hard + 1), (vd, hard + 1), (vd, oracle + 1), (wa, oracle + 1)]))
    for _pair, points in routes:
        c.route(points, PINK, "esc")
    for _pair, points in routes:
        c.arrows[points[-1]] = ("◂", PINK)
    c.flush_edges()

    key_y = iy + 6
    escalation = _known_escalations(ctx)
    if escalation:
        c.put(ix + 1, key_y, "ESCALATION", PINK, PANEL, True)
    for k, (src, dst) in enumerate(escalation):
        _draw_segments(c, ix + 1, key_y + 1 + k, [(src, INK), (" → ", PINK, True), (dst, INK)], PANEL)
    c.border_text(x0 + 2, y0 + h - 1, _clip_segments(LEGEND_ROSTER, w - 6), PANEL)

    # Motion: packets travel from the hub to every busy card. A route is animated only
    # when some node's tiers show that handoff in this graph.
    t = ctx.now * PACKET_SPEED
    for a in ctx.agents:
        if not a.busy or a.name not in cards:
            continue
        col, top = cards[a.name]
        group = a.group if a.group in LANE_GROUPS else "build"
        path = _member_path(bus_from, col - 2, col, by, top)
        c.packets_along(path, t + len(a.name), GROUP_COLOR[group], min(a.busy, 3))
    handoffs = ctx.graph.handoffs if ctx.graph is not None else set()
    busy_agents = {a.name for a in [ctx.hub, *ctx.agents] if a.busy}
    for pair, points in routes:
        if pair in handoffs and busy_agents.intersection(pair):
            c.packets_along(polyline(points)[:-1], t, PINK, 1)


def _lane_plan(ctx: _Ctx, area: int, style: str):
    chip_w, rows = LANE_CHIP[style]
    chip_w = max(12, min(chip_w, area))
    per_row = max(1, (area + 2) // (chip_w + 2))
    plan = []
    for grp in LANE_GROUPS:
        members = ctx.groups[grp]
        lines = math.ceil(len(members) / per_row) * rows if members else 1
        plan.append((grp, members, lines, per_row, chip_w, rows))
    return plan


def _escalation_lines(ctx: _Ctx, area: int):
    text = "   ".join(f"{a} → {b}" for a, b in _known_escalations(ctx))
    return textwrap.wrap(text, max(16, area)) if text else []


def _lanes_inner(ctx: _Ctx, width: int, style: str) -> int:
    """Inner rows of the lane roster for this width. The narrowest style marks the
    escalations on the chips instead of a text line."""
    area = width - 6 - LANE_LABEL
    lines = sum(p[2] for p in _lane_plan(ctx, area, style))
    if style == "text":
        return 1 + lines
    escalation = _escalation_lines(ctx, area)
    return 1 + lines + (1 + len(escalation) if escalation else 0)


def _summary_rows(ctx: _Ctx) -> int:
    return 1 + sum(1 for g in LANE_GROUPS if ctx.groups[g])


def _pick_roster(ctx: _Ctx, width: int, budget: int):
    """The richest roster style whose rows fit the budget, as (style, panel height).
    The summary style always fits: it is the floor, and it says what it leaves out."""
    for style in ("cards", "wide", "text"):
        inner = _lanes_inner(ctx, width, style)
        if inner + 2 <= budget:
            return style, inner + 2
    return "summary", max(3, min(budget, _summary_rows(ctx) + 2))


def _arrow_text(c: Canvas, x: int, y: int, text: str) -> None:
    cursor = x
    for i, part in enumerate(text.split("→")):
        if i:
            c.put(cursor, y, "→", PINK, PANEL, True)
            cursor += 1
        c.put(cursor, y, part, INK, PANEL)
        cursor += len(part)


def _roster_lanes(c: Canvas, ctx: _Ctx, x0: int, y0: int, w: int, h: int, style: str) -> None:
    """Hub line, then one lane per group with its agents as cards, joined by a spine."""
    if _no_roster(c, ctx, x0, y0, w, h):
        return
    c.box(x0, y0, w, h, "AGENT GRAPH  ·  live roster", TEAL, PANEL,
          right=f"{_agent_count(ctx)} agents · {ctx.running} sessions running")
    c.region(x0 + 1, y0 + 1, w - 2, h - 2)
    ix, iy = x0 + 2, y0 + 1
    chip_x = ix + LANE_LABEL
    right = x0 + w - 3
    area = right - chip_x + 1
    hub = ctx.hub
    spin = SPINNER[int(ctx.now * 10) % len(SPINNER)]
    state = f"{spin} {hub.busy} working" if hub.busy else "○ idle"
    title = ("ORCHESTRATOR" if hub.name == "orchestrator" else hub.name.upper()) if _has_hub(ctx) else "ROOT NOT SELECTED"
    title = _short(title, max(0, right - ix - len(state) - 7))
    c.put(ix + 3, iy, f"◈ {title}", ORANGE, PANEL, True)
    if ctx.root is None:
        model, model_colour = "no root session", MUTED
    else:
        model = f"{hub.model} · {hub.effort}" if hub.model else "model not reported"
        model_colour = INK if hub.model else MUTED
    model_x = ix + 3 + len(f"◈ {title}") + 2
    c.put(model_x, iy, _short(model, right - len(state) - model_x - 1), model_colour, PANEL)
    c.put(right - len(state) + 1, iy, state, TEAL if hub.busy else FAINT, PANEL, bool(hub.busy))

    spine = ix + 1
    c.edge(spine, iy, S, ORANGE, "bus")
    row = iy + 1
    heads = []
    for grp, members, lines, per_row, chip_w, rows in _lane_plan(ctx, area, style):
        colour = GROUP_COLOR[grp]
        heads.append((row, colour))
        c.put(ix + 4, row, GROUP_TITLES[grp].upper(), colour, PANEL, True)
        if members:
            for i, a in enumerate(members):
                cx = chip_x + (i % per_row) * (chip_w + 2)
                cy = row + (i // per_row) * rows
                _chip(c, cx, cy, chip_w, a, ctx, colour, rows)
        else:
            c.put(chip_x, row, "none seen", FAINT, PANEL)
        row += lines
    last = heads[-1][0]
    for yy in range(iy + 1, last):
        c.edge(spine, yy, N | S, ORANGE, "bus")
    for r, colour in heads:
        c.edge(spine, r, N | E | (S if r != last else 0), colour, "bus")
        c.edge(spine + 1, r, E | W, colour, "stem")
        c.edge(spine + 2, r, E | W, colour, "stem")
    c.flush_edges()

    if style != "text" and _known_escalations(ctx):
        esc_y = row + 1
        c.put(ix + 3, esc_y, "ESCALATION", PINK, PANEL, True)
        for n, line in enumerate(_escalation_lines(ctx, area)):
            _arrow_text(c, chip_x, esc_y + n, line)
    c.border_text(x0 + 2, y0 + h - 1, _clip_segments(LEGEND_ROSTER, w - 6), PANEL)


def _roster_summary(c: Canvas, ctx: _Ctx, x0: int, y0: int, w: int, h: int) -> None:
    """The floor of the roster: one line per group, names with their state. What does not
    fit is counted, never silently dropped."""
    if _no_roster(c, ctx, x0, y0, w, h):
        return
    c.box(x0, y0, w, h, "AGENT GRAPH  ·  live roster", TEAL, PANEL,
          right=f"{_agent_count(ctx)} agents · {ctx.running} sessions running")
    c.region(x0 + 1, y0 + 1, w - 2, h - 2)
    inner = w - 4
    hub = ctx.hub
    state = f"{hub.busy} working" if hub.busy else "idle"
    model = "no root session" if ctx.root is None else (hub.model or "model not reported")
    title = ctx.hub.name.upper() if _has_hub(ctx) else "ROOT NOT SELECTED"
    lines = [[(_short(f"◈ {title}  ", inner // 2), ORANGE, True), (_short(model, inner - 24), MUTED),
              (f"  ·  {state}", TEAL if hub.busy else FAINT)]]
    for grp in LANE_GROUPS:
        members = ctx.groups[grp]
        if not members:
            continue
        segs = [(f"{GROUP_TITLES[grp].upper()}  ", GROUP_COLOR[grp], True)]
        used = len(segs[0][0])
        for i, a in enumerate(members):
            pool = f" ×{a.pool}" if a.pool else ""
            label = f"{a.name}{pool}{'' if a.configured else '*'} {'◐' + str(a.busy) if a.busy else '○'}"
            piece = (label if i == 0 else " · " + label)
            if used + len(piece) > inner - 8:
                segs.append((f" +{len(members) - i} more", MUTED))
                break
            segs.append((piece, TEAL if a.busy else INK, bool(a.busy)))
            used += len(piece)
        lines.append(segs)
    budget = h - 2
    if len(lines) > budget:
        hidden = len(lines) - (budget - 1)
        more = f"+{hidden} more group{'s' if hidden != 1 else ''} · agents focus (1)"
        lines = lines[: max(0, budget - 1)] + [[(more, MUTED)]]
    for i, segs in enumerate(lines[:budget]):
        _write_segments(c, x0 + 2, y0 + 1 + i, segs, inner)
    c.border_text(x0 + 2, y0 + h - 1, _clip_segments(LEGEND_ROSTER, w - 6), PANEL)


# --- the task graph -------------------------------------------------------------------


def _task_head(ctx: _Ctx, n: _N, width: int, goal: bool):
    """The first row of a task card. Every field has its own width, so a long id or an
    unknown status cannot push the rest of the row out of place."""
    narrow = width < 64
    id_w, status_w, type_w, agent_w = (4, 8, 6, 10) if narrow else (5, 9, 9, 13)
    colour = _status_colour(n)
    segs = [(_fit(n.id, id_w), RED if _alarm(n) else INK, True),
            (f"{_glyph(n, ctx.now)} ", colour, True),
            (_fit(_label(n), status_w), colour, True)]
    if n.stalled:
        segs += [(" STALLED ", RED, True, True), (" ", MUTED)]
    segs += [(" " + _fit(n.type, type_w) + " ", MUTED),
             (_fit(n.agent or "—", agent_w) + " ", INK)]
    if n.attempts:
        segs.append((f"try {n.attempts} ", _try_colour(n)))
    if n.verdict:
        name, vcolour = _verdict(n)
        segs.append((f"◆ {name} ", vcolour))
    elif n.type == "human" and n.status != "passed":
        segs.append(("◇ needs you ", PURPLE))
    room = width - sum(len(seg[0]) for seg in segs)
    if goal:  # the one-line card: dependencies, then as much of the goal as fits
        if n.deps and room > 22:
            dep_text = _short("← " + ",".join(n.deps), min(16, room - 12))
            segs.append((dep_text + "  ", MUTED))
            room -= len(dep_text) + 2
        if room >= 8:
            segs.append((_short(n.goal, room), _goal_colour(n)))
    return segs  # the two-row card shows its dependencies on the goal row instead


def _loop_task_rows(n: _N, width: int):
    """Loop state under a task card, where the dashboard has room for it: the tries and
    tiers, the baseline and latest evidence, then the flags. Long lines are clipped; the
    loops focus has every field in full."""
    colour = _value_colour(n)
    first = [("↳ ", MUTED, True)]
    if n.attempts:
        first += [(f"try {n.attempts}  ", _try_colour(n), True)]
    if n.tiers:
        for i, tier in enumerate(n.tiers):
            if i:
                first.append((" ⇢ ", PINK, True))
            first.append((tier, INK))
        if len(n.tiers) > 1:
            first.append(("  escalated", PINK, True))
        first.append(("  ", MUTED))
    if n.done_when:
        first += [("done ", FAINT), (n.done_when, MUTED)]
    second = [("↳ ", MUTED, True)]
    if n.baseline:
        second += [("base ", FAINT), (n.baseline, MUTED), ("  →  ", MUTED)]
    second += [("latest ", FAINT), (n.evidence or "waiting for evidence", colour)]
    rows = [_clip_segments(first, width), _clip_segments(second, width)]
    if n.stalled or n.failure or n.verdict or n.outputs or n.changed:
        third = [("↳ ", MUTED, True)]
        if n.stalled:
            third += [(" STALLED ", RED, True, True), ("  ", MUTED)]
        if n.failure:
            third.append((f"{n.failure} failure  ", RED))
        if n.verdict:
            name, vcolour = _verdict(n)
            third.append((f"verdict {name.lower()}  ", vcolour))
        if n.outputs:
            third += [("gives ", FAINT), (n.outputs, MUTED), ("  ", MUTED)]
        if n.changed:
            third += [("files ", FAINT), (", ".join(n.changed), MUTED)]
        rows.append(_clip_segments(third, width))
    return rows


def _task_blocks(ctx: _Ctx, g: _G, width: int, mode: str, loops: bool):
    """One block per node: a list of rows (spine, wave label, segments, background).
    "full" puts the goal on its own row; "line" keeps it on the card's first row.
    The spine runs down the whole list, so the waves read as one flow."""
    blocks = []
    last = len(g.flat) - 1
    for idx, n in enumerate(g.flat):
        bits = E | (N if idx > 0 else 0) | (S if idx < last else 0)
        spine = LIGHT[bits]
        cont = "│" if idx < last else " "
        bg = _row_bg(n, ctx.now)
        wave = f"W{n.wave:02}" if n.pos == 0 else ""
        if mode == "line":
            rows = [(spine, wave, _task_head(ctx, n, width, goal=True), bg)]
        else:
            rows = [(spine, wave, _task_head(ctx, n, width, goal=False), bg)]
            prefix = [("← " + ",".join(n.deps) + "  ", MUTED)] if n.deps else []
            goal_text = _short(n.goal, width - sum(len(s[0]) for s in prefix))
            rows.append((cont, "", prefix + [(goal_text, _goal_colour(n))], bg))
        if loops and _has_loop_data(n):
            for segs in _loop_task_rows(n, width):
                rows.append((cont, "", segs, None))
        blocks.append((n, rows))
    return blocks


def _loops_summary(g: _G, width: int):
    """One line that names every node in the loop, so none is reachable only by luck.
    The key that opens the full panel comes first, so it survives a narrow line."""
    segs = [("loops ", FAINT), (f"{len(g.loops)} · 3 opens them: ", MUTED)]
    for i, n in enumerate(g.loops):
        if i:
            segs.append(("  ·  ", FAINT))
        segs.append((f"{n.id} ", INK, True))
        segs.append((_label(n), _status_colour(n)))
        if n.stalled:
            segs.append((" STALLED", RED, True))
        if n.attempts:
            segs.append((f" try {n.attempts}", _try_colour(n)))
    return _clip_segments(segs, width)


def _window(sizes, focus: int, avail: int):
    """The first and last block to show, so the focus block stays visible."""
    n = len(sizes)
    if sum(sizes) <= avail:
        return 0, n
    start = max(0, focus - 1)
    while start <= focus:
        top = 1 if start > 0 else 0
        budget = avail - top
        end, used = start, 0
        while end < n and used + sizes[end] <= budget:
            used += sizes[end]
            end += 1
        if end < n:  # leave a row for the marker below
            budget -= 1
            end, used = start, 0
            while end < n and used + sizes[end] <= budget:
                used += sizes[end]
                end += 1
        if end > focus:
            return start, end
        start += 1
    return focus, min(n, focus + 1)


def _empty_state(c: Canvas, x: int, y: int, w: int, title: str, body: str = "") -> None:
    c.put(x, y, _short(title, w), MUTED, PANEL, True)
    if body:
        for n, line in enumerate(textwrap.wrap(body, max(10, w))[:4]):
            c.put(x, y + 1 + n, line, FAINT, PANEL)


def _focus_block(chosen) -> int:
    """The block the window must keep on screen: the most urgent one."""
    index = next((i for i, (n, _) in enumerate(chosen)
                  if n.stalled or n.status in ("running", "returned", "failed", "blocked")), None)
    if index is None:
        index = next((i for i, (n, _) in enumerate(chosen) if n.status != "passed"), len(chosen) - 1)
    return index


def _panel_tasks(c: Canvas, ctx: _Ctx, x: int, y: int, w: int, h: int, stacked: bool) -> None:
    g = ctx.graph
    c.box(x, y, w, h, "TASK GRAPH  ·  dependency waves", BLUE, PANEL, right=(g.task if g else ""))
    c.border_text(x + 2, y + h - 1, _clip_segments(LEGEND_TASKS, w - 6), PANEL)
    c.region(x + 1, y + 1, w - 2, h - 2)
    cx, cw = x + 2, w - 4
    avail = h - 2
    if g is None:
        _empty_state(c, cx, y + 2, cw, "No active task graph",
                     "The roster and live sessions stay visible here. A dependency graph appears "
                     "once an orchestrator writes one.")
        return
    if not g.flat:
        _empty_state(c, cx, y + 2, cw, "This graph has no nodes yet")
        return
    text_w = cw - 6
    loops_exist = bool(g.loops)
    # The loop rows come before the roomier two-row cards: the evaluation, baseline and
    # evidence are what a reader of the stacked layout cannot get elsewhere.
    options = ([("full", True), ("line", True), ("full", False), ("line", False)] if stacked
               else [("full", False), ("line", False)])
    chosen, summary = None, 0
    for mode, loops in options:
        blocks = _task_blocks(ctx, g, text_w, mode, loops)
        reserve = 1 if (loops_exist and not loops) else 0
        if sum(len(rows) for _, rows in blocks) + reserve <= avail:
            chosen, summary = blocks, reserve
            break
    if chosen is None:  # nothing fits whole: the window moves to the focus block
        chosen = _task_blocks(ctx, g, text_w, "line", False)
        summary = 1 if loops_exist else 0
    focus = _focus_block(chosen)
    start, end = _window([len(rows) for _, rows in chosen], focus, avail - summary)
    yy = y + 1
    if start > 0:
        c.put(cx, yy, f"▲ {start} more above", MUTED, PANEL)
        yy += 1
    for _node, rows in chosen[start:end]:
        for spine, wave, segs, bg in rows:
            if bg is not None:
                c.fill(cx - 1, yy, cw + 2, 1, bg, content=True)
            if spine.strip():
                c.put(cx + 4, yy, spine, SPINE, bg)
            if wave:
                c.put(cx, yy, wave, BLUE, bg, True)
            _draw_segments(c, cx + 6, yy, segs, bg)
            yy += 1
    if end < len(chosen):
        c.put(cx, yy, f"▼ {len(chosen) - end} more below", MUTED, PANEL)
    if summary:
        _draw_segments(c, cx, y + h - 2, _loops_summary(g, cw), PANEL)


# --- loops and events -------------------------------------------------------------------


def _panel_loops(c: Canvas, ctx: _Ctx, x: int, y: int, w: int, h: int) -> None:
    g = ctx.graph
    loops = g.loops if g is not None else []
    c.box(x, y, w, h, "LOOPS  ·  tries, tiers and evidence", AMBER, PANEL,
          right=(f"{len(loops)} in the loop" if loops else ""))
    c.region(x + 1, y + 1, w - 2, h - 2)
    cx, inner = x + 2, w - 4
    if g is None:
        _empty_state(c, cx, y + 2, inner, "No task graph, so no loops",
                     "Loops appear once a graph's nodes start to run.")
        return
    if not loops:
        _empty_state(c, cx, y + 2, inner, "No loops running",
                     "Nodes that run, return, fail, block or take more than one try appear here.")
        return
    packed, hidden = _pack_loops(loops, inner, h - 2, ctx.now)
    cards = [_loop_card(n, inner, level, now=ctx.now) for n, level in packed]
    room = h - 2 - (1 if hidden else 0)
    gap = 1 if sum(len(card) for card in cards) + max(0, len(cards) - 1) <= room else 0
    yy = y + 1
    for index, card in enumerate(cards):
        if index and gap:
            yy += 1
        for segs in card:
            _draw_segments(c, cx, yy, segs, PANEL)
            yy += 1
    if hidden:
        shown = {n.id for n, _ in packed}
        names = ", ".join(n.id for n in loops if n.id not in shown)
        c.put(cx, y + h - 2, _short(f"+ {hidden} more: {names} · 3 reads them all", inner),
              MUTED, PANEL)


def _event_colour(e: _E) -> Color:
    if e.event == "evaluated":
        note = e.note.lower()
        if note.startswith("passed"):
            return GREEN
        if "fail" in note:
            return RED
        return INK
    return EVENT_COLOR.get(e.event, MUTED)


def _panel_events(c: Canvas, ctx: _Ctx, x: int, y: int, w: int, h: int) -> None:
    events = ctx.graph.events if ctx.graph is not None else []
    hidden = max(0, len(events) - max(0, h - 2))
    detail = f"+{hidden} more · 4 all" if hidden else (f"{len(events)} events" if events else "")
    c.box(x, y, w, h, "EVENT TAIL", PURPLE, PANEL, right=detail)
    c.region(x + 1, y + 1, w - 2, h - 2)
    if not events:
        _empty_state(c, x + 2, y + 1, w - 4, "No graph events yet")
        return
    capacity = h - 2
    tail = events[-capacity:] if capacity > 0 else []
    top = y + 1 + (capacity - len(tail))  # the newest event sits on the bottom row
    note_w = max(0, w - 31)
    for i, e in enumerate(tail):
        yy = top + i
        c.put(x + 2, yy, _when(e.time), MUTED, PANEL)
        c.put(x + 11, yy, _fit(e.event, 10), _event_colour(e), PANEL, e.event in ("escalated", "stalled"))
        c.put(x + 22, yy, _fit(e.node or "graph", 5), INK, PANEL)
        c.put(x + 28, yy, _short(e.note, note_w), MUTED, PANEL)


# --- header, status lines and the dashboard ---------------------------------------------


def _run_values(ctx: _Ctx):
    g = ctx.graph
    passed, total = (g.passed, len(g.flat)) if g else (0, 0)
    done_obj, total_obj = g.coverage if g else (0, 0)
    elapsed = _span(g.created, ctx.now if g.status == "running" else g.updated) if g else "—"
    cost = sum(s.cost for s in ctx.sessions)
    tokens = sum(s.tin + s.tout for s in ctx.sessions)
    return passed, total, done_obj, total_obj, elapsed, cost, tokens


def _small_metrics(ctx: _Ctx):
    passed, total, done, objectives, elapsed, cost, tokens = _run_values(ctx)
    return [[(f"{passed}/{total} nodes · {done}/{objectives} obj · {ctx.running} running", INK)],
            [(f"{elapsed} elapsed · ${cost:.2f} · {_tok(tokens)} tok", MUTED)]]


def _run_summary_rows(ctx: _Ctx, width: int):
    """A complete, scrollable run summary; never dependent on header space."""
    g = ctx.graph
    passed, total, done, objectives, elapsed, cost, tokens = _run_values(ctx)
    rows = [[("RUN SUMMARY", BLUE, True)]]
    rows += _label_rows("task", g.task if g else "No task graph", INK, width)
    rows += _label_rows("goal", g.goal if g else "No active graph goal", INK, width)
    rows += _label_rows("status", _label_status(g.status) if g else "IDLE",
                        STATUS_COLOR.get(g.status, MUTED) if g else MUTED, width)
    rows += _label_rows("nodes", f"{passed}/{total} passed · objectives {done}/{objectives}", INK, width)
    rows += _label_rows("elapsed", f"{elapsed} · {ctx.running} running sessions", MUTED, width)
    rows += _label_rows("cost", f"${cost:.2f} · tokens {_tok(tokens)}", INK, width)
    return rows


def _header(c: Canvas, ctx: _Ctx, h: int) -> None:
    w, g, now = c.width, ctx.graph, ctx.now
    c.region(0, 0, w, h)
    c.fill(0, 0, w, h, BG)
    c.put(2, 0, "◈", mix(TEAL, WHITE, 0.4 * _pulse(now)), BG, True)
    c.put(4, 0, "AGENTGRAPH", INK, BG, True)
    where = "live system map" if ctx.state.focus == "all" else FOCUS_TITLE.get(ctx.state.focus, "ALL")
    if ctx.state.focus != "all" and w >= 80:
        where += "  ·  full screen"
    right = []
    if ctx.source:
        badge = AMBER if ctx.source == "demo" else GREEN
        right += [(f" {ctx.source.upper()} ", badge, True, True), ("   ", MUTED)]
    right.append((_when(ctx.snap.now, frame=True), MUTED))
    right_width = sum(len(_seg(seg)[0]) for seg in right)
    c.put(15, 0, _short(where, max(0, w - right_width - 19)), MUTED, BG)
    _write_right(c, 0, right)

    status = g.status.upper() if g else "IDLE"
    title_room = w - min(14, len(status)) - 10
    if g is not None and g.revision > 1:
        title_room -= len(f"rev {g.revision}") + 2
    title = _short((g.task if g else "No task graph").upper(), max(0, title_room))
    c.put(2, 1, title, ORANGE if g else MUTED, BG, True)
    if g is not None and g.revision > 1:
        c.put(2 + len(title) + 2, 1, f"rev {g.revision}", PURPLE, BG, True)
    status_colour = {"RUNNING": TEAL, "PASSED": GREEN, "BLOCKED": AMBER}.get(status, MUTED)
    _write_right(c, 1, [(f" {_short(status, 14)} ", status_colour, True, True)])

    if h >= 4:
        goal = g.goal if g else "Watching the agent roster and live sessions. No task graph is active."
        c.put(2, 2, _short(goal, w - 4), INK if g else MUTED, BG)
    stat_y = 3 if h >= 4 else 2

    passed, total, done_obj, total_obj, elapsed, cost, tokens = _run_values(ctx)
    if w >= 110:
        segs = [("NODES ", MUTED)] + _bar(passed, total, 12)
        segs += [(f" {passed}/{total}", INK, True), ("     OBJECTIVES ", MUTED)]
        segs += (_bar(done_obj, total_obj, 6) + [(f" {done_obj}/{total_obj}", INK, True)]
                 if total_obj else [("none declared", FAINT)])
        segs += [("     ELAPSED ", MUTED), (elapsed, INK, True), ("     RUNNING ", MUTED),
                 (str(ctx.running), TEAL if ctx.running else INK, True),
                 ("     COST ", MUTED), (f"${cost:.2f}", INK, True),
                 ("     TOKENS ", MUTED), (_tok(tokens), INK, True)]
    elif w < 80:
        if h >= 5:
            for i, row in enumerate(_small_metrics(ctx)):
                _write_segments(c, 2, stat_y + i, row, w - 4, BG)
            return
        segs = [(f"N {passed}/{total} O {done_obj}/{total_obj} {elapsed.replace(' ', '')} "
                 f"{ctx.running}run ${cost:.2f} {_tok(tokens)}tok", INK)]
    else:
        segs = [(f"{passed}/{total} nodes", INK, True), ("  ·  ", FAINT),
                (f"{done_obj}/{total_obj} obj", INK, True), ("  ·  ", FAINT),
                (elapsed, INK, True), ("  ·  ", FAINT),
                (f"{ctx.running} running", TEAL if ctx.running else INK, True), ("  ·  ", FAINT),
                (f"${cost:.2f}", INK, True), ("  ·  ", FAINT), (f"{_tok(tokens)} tok", INK, True)]
    _write_segments(c, 2, stat_y, segs, w - 4, BG)
    if h >= 5:
        c.put(2, 4, "─" * (w - 4), RULE, BG)


def _check_rows(ctx: _Ctx, width: int):
    """Two lines, always: the data warnings and the graph problems, each with its count.
    Nothing is folded into a single "more" line; the issues focus lists them all."""
    g = ctx.graph
    rows = []
    count = len(ctx.warnings)
    if count:
        suffix = "  · press 5 for all" if count > 1 else ""
        rows.append([("▲ data   ", AMBER, True), (f"{count} warning{'s' if count != 1 else ''}", AMBER, True),
                     (f" · {ctx.warnings[0]}{suffix}", AMBER)])
    else:
        rows.append([("✓ data   ", MUTED), ("no warnings", MUTED)])
    problems = g.problems if g is not None else []
    if problems:
        suffix = "  · press 5 for all" if len(problems) > 1 else ""
        rows.append([("▲ graph  ", RED, True), (f"{len(problems)} problem{'s' if len(problems) != 1 else ''}", RED, True),
                     (f" · {problems[0]}{suffix}", RED)])
    elif g is None:
        rows.append([("· graph  ", MUTED), ("no task graph to check", MUTED)])
    else:
        segs = [("✓ graph  ", GREEN, True), ("checks pass", GREEN)]
        open_items = [o for o, state in g.objectives if state != "delivered"]
        if open_items:
            segs.append(("  ·  open: " + ", ".join(o.id for o in open_items), MUTED))
        rows.append(segs)
    return rows


def _draw_checks(c: Canvas, ctx: _Ctx, y: int) -> None:
    c.region(0, 0, c.width, c.height)
    for i, segs in enumerate(_check_rows(ctx, c.width)):
        _write_segments(c, 2, y + i, segs, c.width - 4, BG)


def _cut_at_gap(text, width: int) -> str:
    """The text, or as much of it as fits. A cut lands on a double space where it can and is
    marked with an ellipsis, so a list of keys is cut between its items."""
    text = _s(text)
    if len(text) <= width:
        return text
    if width <= 1:
        return text[: max(0, width)]
    gap = text.rfind("  ", 0, width - 1)
    if gap > 0:
        return text[:gap].rstrip() + "…"
    return text[: width - 1] + "…"


def _footer_row(c: Canvas, ctx: _Ctx, y: int) -> None:
    """The footer: the root session and the session counts, with the key hint on the right.
    The keys have priority. When the row is too narrow for everything, the counts go first,
    then the root session, and the hint is cut at a gap as a last resort."""
    c.region(0, 0, c.width, c.height)
    room = c.width - 4

    def length(parts) -> int:
        return sum(len(_seg(s)[0]) for s in parts)

    root = ctx.root
    tin = sum(s.tin for s in ctx.sessions)
    tout = sum(s.tout for s in ctx.sessions)
    head = [("ROOT ", MUTED)]
    if root is not None:
        title_limit = 12 if c.width < 80 else (20 if ctx.hint else 34)
        head += [("● " if root.busy else "○ ", TEAL if root.busy else FAINT),
                 (_short(root.title or root.id, title_limit), INK)]
    else:
        head += [("not selected", AMBER), ("  — pass --session ID", MUTED)]
    if c.width >= 110:
        counts = [("   ·   ", FAINT), (f"{len(ctx.sessions)} sessions incl. root, {ctx.running} running", MUTED),
                  ("   ·   ", FAINT), (f"IN {tin:,} / OUT {tout:,} tokens", MUTED)]
    elif c.width >= 80:
        counts = [("  ·  ", FAINT), (f"{len(ctx.sessions)} sessions, {ctx.running} running", MUTED),
                  ("  ·  ", FAINT), (f"{_tok(tin + tout)} tok", MUTED)]
    else:
        counts = [("  ·  ", FAINT), (f"{len(ctx.sessions)} sessions, {ctx.running} running", MUTED)]
    full = head + counts
    if not ctx.hint:
        _write_segments(c, 2, y, full, room, BG)
        return
    hint = _cut_at_gap(ctx.hint, room)
    if length(full) + 3 + len(hint) <= room:
        _write_segments(c, 2, y, full, room - len(hint) - 3, BG)
    elif length(head) + 3 + len(hint) <= room:
        _write_segments(c, 2, y, head, room - len(hint) - 3, BG)
    else:
        _write_segments(c, 2, y, [(hint, MUTED)], room, BG)
        return
    _write_right(c, y, [(hint, MUTED)])


def _compact_lines(ctx: _Ctx, width: int) -> list:
    """The lines of a short terminal, in order: the task graph near the top and the latest
    event last. `_compact` keeps the last line whatever the height."""
    g = ctx.graph
    lines = []
    head = [("◈ AGENTGRAPH  ", TEAL, True)]
    if ctx.source:
        head.append((f"{ctx.source.upper()}  ", AMBER if ctx.source == "demo" else GREEN, True))
    head.append((_when(ctx.snap.now, frame=True), MUTED))
    lines.append(head)
    if g is None:
        lines.append([("TASKS  ", BLUE, True), ("no active task graph", MUTED)])
    else:
        lines.append([("TASKS  ", BLUE, True), (f"{g.task or 'unnamed'} ", INK, True),
                      (_label_status(g.status), STATUS_COLOR.get(g.status, MUTED), True),
                      (f"  {g.passed}/{len(g.flat)} passed · obj {g.coverage[0]}/{g.coverage[1]}", INK)])
        lines.append([("GOAL   ", BLUE), (g.goal, INK)])
    lines += _small_metrics(ctx)
    hub_model = "no root session" if ctx.root is None else (ctx.hub.model or "model not reported")
    if _agent_count(ctx):
        lines.append([("AGENTS  ", TEAL, True), (f"{_agent_count(ctx)} known · {ctx.running} running · hub ", INK),
                      (hub_model, MUTED)])
    else:
        lines.append([("AGENTS  ", TEAL, True), ("No roster available · no root session", MUTED)])
    if g is not None and g.loops:
        lines.append([("  ", MUTED)] + _loops_summary(g, width - 2))
        for n in g.loops[:2]:
            lines.append([("   ", MUTED)] + _task_head(ctx, n, width - 3, goal=True))
        for n in g.loops[:2]:
            lines.append([("   ↳ ", MUTED, True), (n.id + " ", INK, True),
                          (f"latest {n.evidence or 'waiting for evidence'}", _value_colour(n))])
    if g is not None and g.events:
        e = g.events[-1]
        lines.append([("LATEST  ", AMBER, True), (f"{_when(e.time)} {e.event} {e.node or 'graph'} · {e.note}", MUTED)])
    else:
        lines.append([("LATEST  ", AMBER, True), ("no graph events yet", MUTED)])
    return lines


def _compact(c: Canvas, ctx: _Ctx, rows: int) -> None:
    """A short terminal, drawn as plain lines. The task graph and the latest event are always
    among them. Whatever does not fit is counted in a "+N more" line, and the focus keys reach
    the rest."""
    width = c.width
    lines = _compact_lines(ctx, width)
    if len(lines) <= rows:
        for i, segs in enumerate(lines):
            _write_segments(c, 2, i, segs, width - 4, BG)
        return
    keep = max(0, rows - 2)  # the lines above the "+N more" line; the latest event stays last
    hidden = len(lines) - keep - 1
    for i, segs in enumerate(lines[:keep]):
        _write_segments(c, 2, i, segs, width - 4, BG)
    c.put(2, rows - 2, _short(f"+{hidden} more lines · 1 to 5 read each panel in full", width - 4), MUTED, BG)
    _write_segments(c, 2, rows - 1, lines[-1], width - 4, BG)


def _dashboard(c: Canvas, ctx: _Ctx) -> None:
    """The dashboard: header, roster, tasks with loops and events, two check lines, footer.
    The heights are allocated once, and they add up to the terminal height."""
    width, height = c.width, c.height
    head_h = 5 if height >= 36 or (width < 80 and height >= 24) else (4 if height >= 24 else 3)
    foot_h = 3  # two check lines and the footer
    _header(c, ctx, head_h)
    body = height - head_h - foot_h
    side = width >= 120 and height >= 36  # tasks on the left, loops and events on the right
    reserve = min(body // 2, 15 if side else 14)  # the rows the task list keeps
    if width >= 150 and height >= 40:
        roster_h, style = 17, "graph"
    else:
        style, roster_h = _pick_roster(ctx, width, max(3, body - reserve))
    if style == "graph":
        _roster_graph(c, ctx, 1, head_h, width - 2, roster_h)
    elif style == "summary":
        _roster_summary(c, ctx, 1, head_h, width - 2, roster_h)
    else:
        _roster_lanes(c, ctx, 1, head_h, width - 2, roster_h, style)
    y = head_h + roster_h
    rest = height - foot_h - y
    if side and rest >= 15:
        left = (width - 4) * 3 // 5
        _panel_tasks(c, ctx, 1, y, left, rest, stacked=False)
        rx, rw = left + 2, width - 3 - left
        ev_h = max(5, min(8, rest // 3))
        _panel_loops(c, ctx, rx, y, rw, rest - ev_h)
        _panel_events(c, ctx, rx, y + rest - ev_h, rw, ev_h)
    elif rest >= 4:
        ev_h = min(6, max(4, rest // 3))
        tasks_h = rest - ev_h
        _panel_tasks(c, ctx, 1, y, width - 2, tasks_h, stacked=True)
        _panel_events(c, ctx, 1, y + tasks_h, width - 2, ev_h)
    _draw_checks(c, ctx, height - foot_h)
    _footer_row(c, ctx, height - 1)


def _label_status(status: str) -> str:
    return status.upper() if status else "UNKNOWN"


# --- the focused panels: every field, every line, read by scrolling ----------------------

_FOCUS_ACCENT = {"agents": TEAL, "tasks": BLUE, "loops": AMBER, "events": PURPLE, "issues": RED}
_FOCUS_BLURB = {
    "agents": "every agent: configuration, model, effort and its sessions",
    "tasks": "every node: its wave, goal, dependencies and done-when evaluation",
    "loops": "every node: tries, tiers, evaluation, baseline, evidence and all loop fields",
    "events": "the whole event log of the graph, oldest first",
    "issues": "every data warning and every graph problem",
}


def _agent_rows(a: _A, width: int, hub: bool):
    rows = []
    title = "orchestrator (hub)" if hub else GROUP_TITLES.get(a.group, a.group)
    pool = f"  ·  pool ×{a.pool}" if a.pool else ""
    rows.append([(a.name, GROUP_COLOR.get(a.group, INK), True),
                 (f"   {title}{pool} · {a.busy} running", TEAL if a.busy else MUTED)])
    model = a.model or "model not reported"
    if a.model and a.model_from_sessions:
        model += "  (from sessions)"
    rows += _label_rows("model", model, INK if a.model else MUTED, width)
    rows += _label_rows("effort", a.effort or "effort not reported", INK if a.effort else MUTED, width)
    rows += _label_rows("config", "in the configured roster" if a.configured else "not in the configured roster",
                        TEAL if a.configured else MUTED, width)
    rows += _label_rows("seen in", ", ".join(sorted(a.sources)) or "nowhere", MUTED, width)
    live = [s for s in a.sessions if s.busy]
    if live:
        for s in live:
            rows += _label_rows("running", f"{s.id} · {s.title or 'untitled'} · {s.model or '—'} · {s.effort or '—'}",
                                TEAL, width)
    else:
        rows += _label_rows("running", "none", MUTED, width)
    idle = len(a.sessions) - len(live)
    rows += _label_rows("idle", f"{idle} session{'s' if idle != 1 else ''}", MUTED, width)
    return rows


def _wrap_rows(rows: list, width: int) -> list:
    """Wrap long focus headings without losing their text or segment colours."""
    out = []
    for row in rows:
        text = "".join(_s(seg[0]) for seg in row)
        if len(text) <= width:
            out.append(row)
            continue
        spans, offset = [], 0
        for seg in row:
            end = offset + len(_s(seg[0]))
            spans.append((offset, end, seg))
            offset = end
        start = 0
        while start < len(text):
            end = min(len(text), start + width)
            if end < len(text):
                gap = text.rfind(" ", start + 1, end + 1)
                if gap > start:
                    end = gap
            out.append([(text[max(start, a):min(end, b)],) + tuple(seg[1:])
                        for a, b, seg in spans if a < end and b > start])
            start = end
            while start < len(text) and text[start] == " ":
                start += 1
    return out


def _focus_rows(ctx: _Ctx, focus: str, width: int):
    """Every line of one panel, as rows of segments. Nothing is shortened here: long
    values are wrapped, and the panel is read by scrolling."""
    rows = []
    g = ctx.graph
    if focus == "agents":
        roster = ([ctx.hub] if _has_hub(ctx) else []) + ctx.agents
        if not roster:
            return _wrap_rows([[("No roster available.", MUTED, True)],
                    [("No configured agents, running sessions or graph-named agents.", MUTED)],
                    [("No root session selected.", FAINT)]], width)
        configured = sum(a.configured for a in roster)
        rows.append([(f"{len(roster)} agents", INK, True),
                     (f" · {configured} configured · "
                      f"{len(roster) - configured} not in the configured roster",
                       MUTED)])
        rows.append([])
        routes = _known_escalations(ctx)
        rows.append([("ESCALATION ROUTES", PINK, True), (" · possible handoffs", MUTED)])
        if routes:
            for source, target in routes:
                rows.append([("  ", MUTED), (source, INK), (" → ", PINK), (target, INK)])
        else:
            rows.append([("  No routes between the agents present.", MUTED)])
        rows.append([])
        if _has_hub(ctx):
            rows += _agent_rows(ctx.hub, width, hub=True)
            rows.append([])
        for grp in LANE_GROUPS:
            members = ctx.groups[grp]
            rows.append([(GROUP_TITLES[grp].upper(), GROUP_COLOR[grp], True),
                         (f"  {len(members)} agent{'s' if len(members) != 1 else ''}", MUTED)])
            for a in members:
                rows += _agent_rows(a, width, hub=False)
            rows.append([])
        if not any(ctx.groups[grp] for grp in LANE_GROUPS):
            rows.append([("No other agent is configured, running, or named in the graph yet.", MUTED)])
    elif focus == "tasks":
        rows += _run_summary_rows(ctx, width)
        rows.append([])
        if g is None:
            rows.append([("No active task graph.", MUTED)])
        else:
            for n in g.flat:
                colour = _status_colour(n)
                head = [(f"W{n.wave:02}  ", BLUE, True), (f"{n.id}  ", INK, True),
                        (f"{_glyph(n, ctx.now)} {_label(n)}", colour, True)]
                if n.stalled:
                    head.append(("  STALLED ", RED, True, True))
                head.append((f"  {n.type} · {n.agent or '—'}", MUTED))
                if n.attempts:
                    head.append((f"  try {n.attempts}", _try_colour(n)))
                rows.append(head)
                rows += _label_rows("goal", n.goal or "—", INK, width)
                rows += _label_rows("needs", ", ".join(n.deps) or "nothing", MUTED, width)
                rows += _label_rows("done", n.done_when or "—", MUTED, width)
                rows.append([])
    elif focus == "loops":
        if g is None:
            rows.append([("No active task graph, so no loops.", MUTED)])
        else:
            if not g.loops:
                rows.append([("No node has loop data yet.", MUTED)])
            for n in g.loops + g.not_started:
                rows += _loop_card(n, width, 3, focus=True, now=ctx.now)
                rows.append([])
    elif focus == "events":
        events = g.events if g is not None else []
        if not events:
            rows.append([("No graph events yet.", MUTED)])
        for i, e in enumerate(events, 1):
            head = [(f"{i:>4}  ", FAINT), (f"{_when(e.time)}  ", MUTED),
                    (f"{e.event}  ", _event_colour(e), True), (f"{e.node or 'graph'}  ", INK)]
            rows.append(head + [(e.note, MUTED)])
    elif focus == "issues":
        rows.append([("DATA WARNINGS", AMBER, True), (f"  {len(ctx.warnings)}", INK, True)])
        if not ctx.warnings:
            rows.append([("  none", MUTED)])
        for i, text in enumerate(ctx.warnings, 1):
            rows += _label_rows(f"{i}.", text, AMBER, width)
        rows.append([])
        problems = g.problems if g is not None else []
        rows.append([("GRAPH PROBLEMS", RED, True), (f"  {len(problems)}", INK, True)])
        if not problems:
            rows.append([("  none" if g is not None else "  no task graph to check", MUTED)])
        for i, text in enumerate(problems, 1):
            rows += _label_rows(f"{i}.", text, RED, width)
    return _wrap_rows(rows, width)


def _help_rows(width: int):
    rows = [[("KEYS", AMBER, True)], [(KEY_HELP, INK, True)], []]
    rows.append([("PANELS", TEAL, True)])
    for name in PANELS:
        if name == "all":
            rows.append([("0 dashboard  ", INK, True), ("the whole picture on one screen", MUTED)])
        else:
            rows.append([(f"{PANELS.index(name)} {name:<8}", INK, True), (_FOCUS_BLURB[name], MUTED)])
    rows += [[], [("SCROLLING", TEAL, True)],
             [("j k or the arrows move one line; space and b move a page; g and G go to the top and the end.", INK)],
             [], [("? closes this help. q quits.", MUTED)]]
    out = []
    for segs in rows:
        if not segs:
            out.append([])
            continue
        text = "".join(_s(s[0]) for s in segs)
        if len(text) <= width:
            out.append(segs)
            continue
        seg = segs[0]  # a long row wraps, and its lines keep the style of its first segment
        bold = seg[2] if len(seg) > 2 else False
        for line in textwrap.wrap(text, max(8, width)) or [""]:
            out.append([(line, seg[1], bold)])
    return out


def _draw_panel_screen(c: Canvas, ctx: _Ctx, rows: list, title: str, accent: Color, empty: str) -> None:
    """A focused panel below the compact header: the box, the lines from the scroll
    position, where the reader is, and the checks and footer."""
    box_top, box_h = 3, c.height - 6
    capacity = max(1, box_h - 2)
    last = max(0, len(rows) - capacity)
    scroll = min(max(0, _int(ctx.state.scroll)), last)
    c.last_line = last
    c.page = capacity
    shown = rows[scroll: scroll + capacity]
    if rows:
        where = f"lines {scroll + 1}–{min(len(rows), scroll + capacity)} of {len(rows)}"
    else:
        where = empty
    c.box(0, box_top, c.width, box_h, title, accent, PANEL, right=where)
    c.region(2, box_top + 1, c.width - 4, box_h - 2)
    for i, segs in enumerate(shown):
        _draw_segments(c, 2, box_top + 1 + i, segs, PANEL)
    _draw_checks(c, ctx, c.height - 3)
    _footer_row(c, ctx, c.height - 1)


def _draw_focus(c: Canvas, ctx: _Ctx) -> None:
    focus = ctx.state.focus
    _header(c, ctx, 3)
    rows = _focus_rows(ctx, focus, c.width - 6)
    _draw_panel_screen(c, ctx, rows, FOCUS_TITLE[focus], _FOCUS_ACCENT[focus],
                       "nothing to show in this panel")


def _draw_help(c: Canvas, ctx: _Ctx) -> None:
    """The key help. It scrolls like any panel when the terminal is too short for all of it,
    and its last_line says how far."""
    _header(c, ctx, 3)
    _draw_panel_screen(c, ctx, _help_rows(c.width - 6), "HELP", AMBER, "")


# --- the entry points -------------------------------------------------------------------


def render(snapshot: Snapshot, width: int = 160, height: int = 50, source: str = "",
           hint: str = "", state=None) -> Canvas:
    """One frame. The same snapshot, size, state and time always give the same frame.
    `state` selects the dashboard (the default) or one panel, a scroll position, or help."""
    width, height = max(1, int(width)), max(1, int(height))
    state = state if isinstance(state, ViewState) else ViewState()
    if state.focus not in PANELS:
        state = ViewState(focus="all", scroll=state.scroll, help=state.help)
    c = Canvas(width, height)
    c.fill(0, 0, width, height, BG)
    if width < 56 or height < 14:
        c.region(0, 0, width, height)
        note = ["agentgraph needs at least 56 × 14 characters", f"this terminal is {width} × {height}"]
        for i, line in enumerate(note):
            c.put(max(0, (width - len(line)) // 2), height // 2 - 1 + i, _short(line, width), MUTED, BG)
        return c
    ctx = _context(snapshot, source, hint, state)
    if state.help:
        _draw_help(c, ctx)
    elif state.focus != "all":
        _draw_focus(c, ctx)
    elif height < 20:
        _compact(c, ctx, height - 3)
        _draw_checks(c, ctx, height - 3)
        _footer_row(c, ctx, height - 1)
    else:
        _dashboard(c, ctx)
    return c


_HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>agentgraph</title>
<style>
html,body{margin:0;background:#04080c;}
body{min-height:100vh;display:flex;align-items:flex-start;justify-content:center;box-sizing:border-box;padding:32px;}
.window{border-radius:12px;overflow:hidden;background:#060b11;box-shadow:0 0 0 1px #1d3140,0 30px 80px rgba(0,0,0,.55);}
.bar{height:30px;display:flex;align-items:center;gap:8px;padding:0 14px;background:#0b141c;color:#5f7888;font:12px/1 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;}
.dot{width:11px;height:11px;border-radius:50%;display:inline-block;}
pre{margin:0;padding:14px 18px 18px;font:13px/1.32 ui-monospace,"SF Mono",Menlo,Consolas,monospace;white-space:pre;font-variant-ligatures:none;font-feature-settings:"liga" 0,"calt" 0;}
</style>
</head>
<body>
<div class="window">
<div class="bar"><span class="dot" style="background:#ff5f57"></span><span class="dot" style="background:#febc2e"></span><span class="dot" style="background:#28c840"></span><span style="margin-left:10px">{TITLE}</span></div>
<pre>{BODY}</pre>
</div>
</body>
</html>
"""


def render_html(snapshot: Snapshot, width: int = 160, height: int = 50, source: str = "",
                state=None) -> str:
    """A standalone HTML page with the same frame, for screenshots and review."""
    canvas = render(snapshot, width, height, source=source, state=state)
    title = f"agentgraph · {width} × {height}"
    if isinstance(state, ViewState) and state.focus in FOCUS_TITLE:
        title += f" · {FOCUS_TITLE[state.focus]}"
    return _HTML_PAGE.replace("{TITLE}", html.escape(title)).replace("{BODY}", canvas.to_html())
