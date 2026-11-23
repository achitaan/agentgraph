"""Writing a duration as text. This already works and has its own tests; leave it alone."""

UNITS = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))


def format_duration(seconds):
    """3725 -> "1h2m5s"; 0 -> "0s"; whole days, hours and minutes drop out when zero."""
    if isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 0:
        raise ValueError("seconds must be a non-negative whole number")
    if seconds == 0:
        return "0s"
    parts = []
    for unit, size in UNITS:
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count}{unit}")
    return "".join(parts)
