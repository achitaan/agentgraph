"""Tasks in a JSON file: a list of {"id": 1, "title": "Buy milk", "done": false}."""
import datetime
import json
import re
from pathlib import Path

from .model import Task


PRIORITIES = ("normal", "high")


def to_dict(task):
    return {"id": task.id, "title": task.title, "done": task.done, "due": task.due, "priority": task.priority}


def from_dict(data):
    return Task(id=data["id"], title=data["title"], done=data.get("done", False),
                due=data["due"], priority=data["priority"])


def check_due(due):
    if due is None:
        return None
    if isinstance(due, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
        try:
            datetime.date.fromisoformat(due)
            return due
        except ValueError:
            pass
    raise ValueError(f"bad due date: {due}")


def check_priority(priority):
    if priority not in PRIORITIES:
        raise ValueError(f"bad priority: {priority}")
    return priority


def load(path):
    path = Path(path)
    if not path.exists():
        return []
    return [from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]


def save(path, tasks):
    Path(path).write_text(json.dumps([to_dict(t) for t in tasks], indent=2) + "\n", encoding="utf-8")


def add_task(path, title, due=None, priority="normal"):
    title = title.strip()
    if not title:
        raise ValueError("a task needs a title")
    tasks = load(path)
    task = Task(id=max((t.id for t in tasks), default=0) + 1, title=title, due=due, priority=priority)
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
