"""The order list screen."""
from .paging import page_count, page_slice

STATUSES = ("new", "paid", "shipped", "cancelled")


def make_order(order_id, customer, total_cents, status="new"):
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    return {"id": order_id, "customer": customer, "total_cents": total_cents, "status": status}


def filter_orders(orders, status=None, customer=None):
    rows = list(orders)
    if status is not None:
        rows = [o for o in rows if o["status"] == status]
    if customer is not None:
        rows = [o for o in rows if o["customer"] == customer]
    return rows


def list_orders(orders, page=1, per_page=20, status=None, customer=None):
    """One page of the (filtered) orders, with the page number and the page count for the footer."""
    rows = filter_orders(orders, status, customer)
    return {
        "page": page,
        "pages": page_count(len(rows), per_page),
        "items": page_slice(rows, page, per_page),
    }


def footer(listing):
    return f"Page {listing['page']} of {listing['pages']}"


def money(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def render_line(order):
    return f"#{order['id']:<5} {order['customer']:<12} {money(order['total_cents']):>9}  {order['status']}"


def render_page(listing):
    lines = [render_line(o) for o in listing["items"]]
    lines.append(footer(listing))
    return "\n".join(lines)


def total_value(orders, status=None):
    return sum(o["total_cents"] for o in filter_orders(orders, status))
