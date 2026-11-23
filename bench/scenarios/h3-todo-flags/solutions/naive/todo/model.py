"""The Task record."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Task:
    id: int
    title: str
    done: bool = False
    due: Optional[str] = None
    priority: str = "normal"
