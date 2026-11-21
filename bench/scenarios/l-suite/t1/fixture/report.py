"""A one-line summary used by the shop's daily email."""
from inventory import total_value


def summary(items):
    return f"{len(items)} items worth {total_value(items):.2f}"
