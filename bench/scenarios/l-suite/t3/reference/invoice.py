"""Invoice totals for the billing page."""
from pricing import add_tax, apply_discount


def line_total(unit_cents, qty, discount=0, tax=0):
    """One invoice line: discount the unit price, multiply by qty, then add tax."""
    price = apply_discount(unit_cents, discount)
    return add_tax(price * qty, tax)


def invoice_total(lines):
    """`lines` is a list of dicts with the keyword arguments of line_total."""
    return sum(line_total(**line) for line in lines)
