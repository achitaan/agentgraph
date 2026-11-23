"""The printed contact report."""
from .store import load


def initial(record):
    return (record["last"] or record["first"])[0].upper()


def render(path):
    records = load(path)
    if not records:
        return "(no contacts)"
    groups = {}
    for record in records:
        groups.setdefault(initial(record), []).append(record)
    lines = []
    for letter in sorted(groups):
        group = sorted(groups[letter], key=lambda r: (r["last"].lower(), r["first"].lower()))
        lines.append(f"{letter} ({len(group)})")
        for record in group:
            name = f"{record['last']}, {record['first']}" if record["last"] else record["first"]
            line = f"  {name}"
            if record["phones"]:
                line += " - " + ", ".join(record["phones"])
            lines.append(line)
    return "\n".join(lines)
