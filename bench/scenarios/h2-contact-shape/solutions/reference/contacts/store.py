"""The address book file.

Version 2 file: {"version": 2, "contacts": [{"first": "Ada", "last": "Lovelace", "phones": ["555-0100"], "email": null}]}.
"""
import json
from pathlib import Path

VERSION = 2


class OldFormatError(Exception):
    """The file is a version 1 address book (a bare list); migrate it first."""


def split_name(name):
    words = name.split()
    if not words:
        return "", ""
    if len(words) == 1:
        return words[0], ""
    return " ".join(words[:-1]), words[-1]


def make_record(name, phone=None, email=None):
    first, last = split_name(name)
    return {"first": first, "last": last, "phones": [phone] if phone else [], "email": email or None}


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise OldFormatError(f"{path} is not a version {VERSION} address book; run the migration")
    return data["contacts"]


def save(path, records):
    payload = {"version": VERSION, "contacts": records}
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def add(path, name, phone=None, email=None):
    records = load(path)
    record = make_record(name, phone, email)
    records.append(record)
    save(path, records)
    return record
