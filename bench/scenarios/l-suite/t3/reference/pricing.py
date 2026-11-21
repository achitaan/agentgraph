"""Money helpers. All amounts are whole cents (ints); percents are ints from 0 to 100 (tax may be higher)."""


def _scaled(cents, factor):
    """cents * factor / 100 rounded to the nearest int, halves up, in exact integer arithmetic."""
    return (cents * factor + 50) // 100


def apply_discount(cents, percent):
    """The price in cents after taking `percent` percent off."""
    return _scaled(cents, 100 - percent)


def add_tax(cents, percent):
    """The price in cents after adding `percent` percent tax."""
    return _scaled(cents, 100 + percent)
