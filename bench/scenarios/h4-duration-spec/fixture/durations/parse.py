"""Reading a duration from text. The rules are in SPEC.md at the top of the project."""


def parse_duration(text):
    """Return the number of seconds in `text` (see SPEC.md)."""
    # TODO: placeholder that only understands plain seconds like "45s". Replace it with the full spec.
    return int(text.rstrip("s"))
