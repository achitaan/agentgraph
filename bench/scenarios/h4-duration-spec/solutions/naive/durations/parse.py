"""Reading a duration from text."""
import re

SIZES = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    text = text.strip()
    if text.isdigit():
        return int(text)
    parts = re.findall(r"(\d+)\s*([dhms])", text)
    if not parts:
        raise ValueError("cannot read duration")
    return sum(int(n) * SIZES[u] for n, u in parts)
