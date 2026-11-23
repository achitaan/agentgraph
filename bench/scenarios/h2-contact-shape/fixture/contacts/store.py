"""The address book file.

Today the file is a bare JSON list of records like {"name": "Ada Lovelace", "phone": "555-0100", "email": null}.
"""
import json
from pathlib import Path


def make_record(name, phone=None, email=None):
    return {"name": name.strip(), "phone": phone or None, "email": email or None}


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, records):
    Path(path).write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def add(path, name, phone=None, email=None):
    records = load(path)
    record = make_record(name, phone, email)
    records.append(record)
    save(path, records)
    return record
