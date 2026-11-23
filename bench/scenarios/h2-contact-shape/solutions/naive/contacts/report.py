from .store import load


def render(path):
    records = load(path)
    if not records:
        return "(no contacts)"
    groups = {}
    for record in records:
        groups.setdefault(record["last"][:1].upper(), []).append(record)
    lines = []
    for letter in sorted(groups):
        group = sorted(groups[letter], key=lambda r: r["last"].lower())
        lines.append(f"{letter} ({len(group)})")
        for record in group:
            line = f"  {record['last']}, {record['first']}"
            if record["phones"]:
                line += " - " + ", ".join(record["phones"])
            lines.append(line)
    return "\n".join(lines)
