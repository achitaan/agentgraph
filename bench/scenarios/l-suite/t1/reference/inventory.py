"""A tiny inventory helper. An item is a dict with 'name', 'price' (a float) and 'qty' (an int)."""


def total_value(items):
    """Sum of price * qty over all items."""
    return sum(item["price"] * item["qty"] for item in items)


def names(items):
    """The item names, sorted."""
    return sorted(item["name"] for item in items)


def average_price(items):
    """Average price per unit, weighted by quantity; 0.0 when there is no stock."""
    qty = sum(item["qty"] for item in items)
    if qty == 0:
        return 0.0
    return total_value(items) / qty
