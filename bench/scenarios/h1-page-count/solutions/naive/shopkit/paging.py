"""Page arithmetic shared by the order screens and the outbox."""


def check_size(per_page):
    """Reject a page size that is not a positive whole number."""
    if isinstance(per_page, bool) or not isinstance(per_page, int) or per_page < 1:
        raise ValueError("per_page must be a positive whole number")


def page_count(total, per_page):
    """How many pages `total` items make at `per_page` items per page."""
    check_size(per_page)
    return -(-total // per_page)


def page_slice(items, page, per_page):
    """The items on one page. Pages are numbered from 1; a page past the end is an empty list."""
    check_size(per_page)
    if page < 1:
        raise ValueError("pages start at 1")
    start = (page - 1) * per_page
    return list(items[start:start + per_page])
