"""Turn short duration strings such as '1h30m' into seconds, and back."""
import re

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_PART = re.compile(r"(\d+)([a-z])", re.IGNORECASE)
_WHOLE = re.compile(r"(?:\d+[a-z])+", re.IGNORECASE)


def parse_duration(text):
    """'1h30m' -> 5400. Returns whole seconds."""
    if not _WHOLE.fullmatch(text):
        raise ValueError(f"bad duration: {text!r}")
    total = 0
    for number, unit in _PART.findall(text):
        if unit.lower() not in UNITS:
            raise ValueError(f"unknown unit {unit!r} in {text!r}")
        total += int(number) * UNITS[unit.lower()]
    return total


def format_duration(seconds):
    """5400 -> '1h30m'; 0 -> '0s'. Only hours, minutes and seconds are used."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    out = ""
    if hours:
        out += f"{hours}h"
    if minutes:
        out += f"{minutes}m"
    if secs or not out:
        out += f"{secs}s"
    return out
