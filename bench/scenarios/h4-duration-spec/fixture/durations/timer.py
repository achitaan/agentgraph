"""A countdown that is built from text such as "1h 30m"."""
from .format import format_duration
from .parse import parse_duration


class Countdown:
    def __init__(self, seconds):
        self.remaining = seconds

    @classmethod
    def from_text(cls, text):
        return cls(parse_duration(text))

    def tick(self, seconds=1):
        self.remaining = max(0, self.remaining - seconds)
        return self.remaining

    @property
    def finished(self):
        return self.remaining == 0

    def __str__(self):
        return format_duration(self.remaining)
