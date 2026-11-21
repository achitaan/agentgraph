"""The address book: contacts and the book that holds them."""
from textnorm import normalize, split_name


class Contact:
    """One person. `tags` is a tuple of lower-case labels such as ('family', 'work')."""

    def __init__(self, first, last, email="", phone="", tags=()):
        self.first = first
        self.last = last
        self.email = email
        self.phone = phone
        self.tags = tuple(tags)

    @property
    def full_name(self):
        return f"{self.first} {self.last}".strip()

    def __repr__(self):
        return f"Contact({self.full_name!r})"


class ContactBook:
    """An ordered collection of contacts, at most one per e-mail address (when an address is given)."""

    def __init__(self):
        self._contacts = []

    def __len__(self):
        return len(self._contacts)

    def __iter__(self):
        return iter(list(self._contacts))

    def add(self, contact):
        """Add a contact. A second contact with the same non-empty e-mail address (ignoring case) is refused."""
        if contact.email and self.get(contact.email) is not None:
            raise ValueError(f"duplicate e-mail address: {contact.email}")
        self._contacts.append(contact)
        return contact

    def remove(self, contact):
        self._contacts.remove(contact)

    def get(self, email):
        """The contact with this e-mail address (ignoring case), or None."""
        wanted = email.strip().lower()
        for contact in self._contacts:
            if contact.email and contact.email.lower() == wanted:
                return contact
        return None

    def by_tag(self, tag):
        """The contacts that carry `tag`, in the order they were added."""
        tag = tag.strip().lower()
        return [c for c in self._contacts if tag in c.tags]

    def emails(self):
        """Every e-mail address in the book, in the order of the contacts."""
        return [c.email for c in self._contacts if c.email]

    def sorted_by_name(self):
        """The address-book order: by last name, then first name, compared in normalized form.

        Names are compared character by character after `normalize`, so a name that starts with an accented letter
        comes after the plain Z names, exactly as the printed address book has always been ordered.
        """
        return sorted(self._contacts, key=lambda c: (normalize(c.last), normalize(c.first)))

    def names(self):
        """The full names in address-book order."""
        return [c.full_name for c in self.sorted_by_name()]

    def group_by_initial(self):
        """A dict from the first letter of the normalized last name to the contacts under it, in address-book order.

        The printed address book has one page per letter; a contact without a last name is filed under its first name.
        """
        pages = {}
        for contact in self.sorted_by_name():
            key = (normalize(contact.last) or normalize(contact.first))[:1]
            pages.setdefault(key, []).append(contact)
        return pages

    def merge(self, other):
        """Add the contacts of another book that are not here yet (same e-mail address counts as the same person).

        Returns how many contacts were added. Contacts without an e-mail address are always added.
        """
        added = 0
        for contact in other:
            if contact.email and self.get(contact.email) is not None:
                continue
            self.add(contact)
            added += 1
        return added


def parse_csv_line(line):
    """'Ada Lovelace,ada@x.org,555-1' -> a Contact. Fields: full name, e-mail, phone; the last two may be missing."""
    fields = [f.strip() for f in line.split(",")]
    first, last = split_name(fields[0])
    email = fields[1] if len(fields) > 1 else ""
    phone = fields[2] if len(fields) > 2 else ""
    return Contact(first, last, email, phone)


def load_csv(text):
    """A ContactBook from lines of `parse_csv_line` text; blank lines and lines starting with '#' are skipped."""
    book = ContactBook()
    for line in text.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            book.add(parse_csv_line(line))
    return book
