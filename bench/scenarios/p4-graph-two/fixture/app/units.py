"""Durations."""


def parse_duration(text):
    """Parse a duration such as "1h30m", "45s", "2d" or "1d2h3m4s" into whole seconds.

    Units are d (86400), h (3600), m (60) and s (1); each may appear at most once and they
    must come in that order. Spaces around the text are ignored.
    parse_duration("1h30m") == 5400
    parse_duration(" 45s ") == 45
    Anything else (empty text, a missing number, an unknown unit, repeated or misordered
    units, a bare number such as "90") raises ValueError.
    """
    raise NotImplementedError
