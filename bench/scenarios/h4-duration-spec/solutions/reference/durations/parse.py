"""Reading a duration from text. The rules are in SPEC.md at the top of the project."""
import re

SIZES = {"d": 86400, "h": 3600, "m": 60, "s": 1}
ORDER = "dhms"
LIMIT = 365 * 86400
PART = re.compile(r"([0-9]+)([dhmsDHMS])")
BARE = re.compile(r"[0-9]+")


def parse_duration(text):
    """Return the number of seconds in `text` (see SPEC.md)."""
    if not isinstance(text, str):
        raise TypeError("duration must be text")
    text = text.strip()
    if not text:
        raise ValueError("empty duration")
    if BARE.fullmatch(text):
        total = int(text)
    else:
        total, last = 0, -1
        seen = set()
        for chunk in text.split():
            # a chunk may hold several touching parts, e.g. "1h30m"
            position = 0
            while position < len(chunk):
                match = PART.match(chunk, position)
                if not match:
                    raise ValueError(f"cannot read {chunk[position:]!r}")
                unit = match.group(2).lower()
                if unit in seen:
                    raise ValueError(f"unit {unit} twice")
                if ORDER.index(unit) < last:
                    raise ValueError("units out of order")
                seen.add(unit)
                last = ORDER.index(unit)
                total += int(match.group(1)) * SIZES[unit]
                position = match.end()
    if total > LIMIT:
        raise ValueError("more than 365 days")
    return total
