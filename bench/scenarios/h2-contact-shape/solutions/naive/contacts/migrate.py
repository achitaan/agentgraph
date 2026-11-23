import json
from pathlib import Path

from .store import make_record, save


def migrate_file(path):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    save(path, [make_record(r["name"], r.get("phone"), r.get("email")) for r in data])
    return True
