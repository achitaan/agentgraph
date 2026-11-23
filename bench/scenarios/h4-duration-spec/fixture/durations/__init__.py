"""durations: reading and writing short time spans like 1h30m."""
from .format import format_duration
from .parse import parse_duration

__all__ = ["format_duration", "parse_duration"]
