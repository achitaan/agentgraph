"""Used by the job runner: when is a job due?"""
from durations import parse_duration


def deadline(start, text):
    """The start time (seconds) plus a duration string such as '90m'."""
    return start + parse_duration(text)
