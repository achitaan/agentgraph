"""The printed contact report."""
from .store import load


def initial(record):
    return record["name"].split()[-1][0].upper()


def render(path):
    records = load(path)
    if not records:
        return "(no contacts)"
    groups = {}
    for record in records:
        groups.setdefault(initial(record), []).append(record)
    lines = []
    for letter in sorted(groups):
        group = sorted(groups[letter], key=lambda r: r["name"].split()[-1].lower())
        lines.append(f"{letter} ({len(group)})")
        for record in group:
            line = f"  {record['name']}"
            if record["phone"]:
                line += f" - {record['phone']}"
            lines.append(line)
    return "\n".join(lines)
