"""Find contacts by what the user typed in the search box."""
from textnorm import normalize

EXACT, PREFIX, CONTAINS, EMAIL = 0, 1, 2, 3


def rank(contact, query):
    """How well `contact` matches the already-normalized `query`: a rank (lower is better) or None for no match.

    0 the whole name equals the query; 1 the name or the last name starts with it; 2 the name contains it;
    3 the e-mail address contains it.
    """
    name = normalize(contact.full_name)
    if query == name:
        return EXACT
    if name.startswith(query) or normalize(contact.last).startswith(query):
        return PREFIX
    if query in name:
        return CONTAINS
    if contact.email and query in contact.email.lower():
        return EMAIL
    return None


def search(book, query, limit=None):
    """The contacts that match `query`, best matches first; ties are in address-book order.

    An empty (or all-blank) query matches nothing. `limit` caps the number of results when given.
    """
    q = normalize(query)
    if not q:
        return []
    order = {id(c): i for i, c in enumerate(book.sorted_by_name())}
    found = []
    for contact in book:
        score = rank(contact, q)
        if score is not None:
            found.append((score, order[id(contact)], contact))
    found.sort(key=lambda item: (item[0], item[1]))
    results = [contact for _score, _pos, contact in found]
    return results if limit is None else results[:limit]


def suggest(book, prefix, count=5):
    """Up to `count` full names that start with `prefix` (as the user types), in address-book order."""
    p = normalize(prefix)
    if not p:
        return []
    names = [c.full_name for c in book.sorted_by_name() if normalize(c.full_name).startswith(p)]
    return names[:count]


def by_domain(book, domain):
    """The contacts whose e-mail address ends with '@' + domain (ignoring case)."""
    suffix = "@" + domain.strip().lower()
    return [c for c in book if c.email.lower().endswith(suffix)]


def related(book, contact, limit=3):
    """Other contacts that share a tag or an e-mail domain with `contact`, most shared first, then address-book order."""
    domain = contact.email.lower().rpartition("@")[2]
    scores = []
    for position, other in enumerate(book.sorted_by_name()):
        if other is contact:
            continue
        shared = len(set(other.tags) & set(contact.tags))
        if domain and other.email.lower().endswith("@" + domain):
            shared += 1
        if shared:
            scores.append((-shared, position, other))
    scores.sort(key=lambda item: (item[0], item[1]))
    return [other for _s, _p, other in scores[:limit]]


def tagged(book, tag, query=""):
    """Contacts carrying `tag` that also match `query` (every contact with the tag when the query is blank)."""
    with_tag = book.by_tag(tag)
    if not normalize(query):
        return with_tag
    wanted = {id(c) for c in search(book, query)}
    return [c for c in with_tag if id(c) in wanted]
