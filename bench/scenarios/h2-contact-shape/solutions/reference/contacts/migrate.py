"""Migrate an old (version 1) address book file to version 2."""
import json
import shutil
from pathlib import Path

from .store import make_record, save


def migrate_file(path):
    path = Path(path)
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return False
    shutil.copyfile(path, path.with_name(path.name + ".v1.bak"))
    save(path, [make_record(r["name"], r.get("phone"), r.get("email")) for r in data])
    return True
