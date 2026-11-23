"""The address book file (version 2: {"version": 2, "contacts": [...]})."""
import json
from pathlib import Path


class OldFormatError(Exception):
    pass


def make_record(name, phone=None, email=None):
    first, _, last = name.strip().partition(" ")
    return {"first": first, "last": last, "phones": [phone] if phone else [], "email": email or None}


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        raise OldFormatError("old file")
    return data["contacts"]


def save(path, records):
    Path(path).write_text(json.dumps({"version": 2, "contacts": records}, indent=2) + "\n", encoding="utf-8")


def add(path, name, phone=None, email=None):
    records = load(path)
    record = make_record(name, phone, email)
    records.append(record)
    save(path, records)
    return record
