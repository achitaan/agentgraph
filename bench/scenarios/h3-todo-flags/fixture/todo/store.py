"""Tasks in a JSON file: a list of {"id": 1, "title": "Buy milk", "done": false}."""
import json
from pathlib import Path

from .model import Task


def to_dict(task):
    return {"id": task.id, "title": task.title, "done": task.done}


def from_dict(data):
    return Task(id=data["id"], title=data["title"], done=data.get("done", False))


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    return [from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def save(path, tasks):
    Path(path).write_text(json.dumps([to_dict(t) for t in tasks], indent=2) + "\n", encoding="utf-8")


def add_task(path, title):
    title = title.strip()
    if not title:
        raise ValueError("a task needs a title")
    tasks = load(path)
    task = Task(id=max((t.id for t in tasks), default=0) + 1, title=title)
    tasks.append(task)
    save(path, tasks)
    return task


def mark_done(path, task_id):
    tasks = load(path)
    for task in tasks:
        if task.id == task_id:
            task.done = True
            save(path, tasks)
            return task
    raise KeyError(task_id)
