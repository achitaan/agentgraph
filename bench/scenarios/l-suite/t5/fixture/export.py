"""Write contacts out as vCard files, one file per contact."""
import re

from textnorm import normalize

_UNSAFE = re.compile(r"[^\w.-]")


def filename_for(contact):
    """The file name for a contact: its normalized full name with '_' for spaces, plus '.vcf'.

    The letters are kept exactly as they are in the name (accents included), so the files carry the names the people
    gave. Characters that are not letters, digits, '.', '-' or '_' are dropped.
    """
    base = normalize(contact.full_name).replace(" ", "_")
    base = _UNSAFE.sub("", base) or "contact"
    return base + ".vcf"


def _escape(value):
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def to_vcf(contact):
    """The vCard text of one contact."""
    lines = ["BEGIN:VCARD", "VERSION:3.0",
             f"N:{_escape(contact.last)};{_escape(contact.first)};;;",
             f"FN:{_escape(contact.full_name)}"]
    if contact.email:
        lines.append(f"EMAIL:{_escape(contact.email)}")
    if contact.phone:
        lines.append(f"TEL:{_escape(contact.phone)}")
    if contact.tags:
        lines.append("CATEGORIES:" + ",".join(_escape(t) for t in contact.tags))
    lines.append("END:VCARD")
    return "\n".join(lines) + "\n"


def export_all(book):
    """A dict of file name -> vCard text for every contact, in address-book order.

    When two contacts would get the same file name the later ones get '-2', '-3', ... before the '.vcf'.
    """
    files = {}
    for contact in book.sorted_by_name():
        name = filename_for(contact)
        stem, dot, ext = name.rpartition(".")
        n = 2
        while name in files:
            name = f"{stem}-{n}{dot}{ext}"
            n += 1
        files[name] = to_vcf(contact)
    return files


def to_csv(book):
    """The whole book as csv text in address-book order: full name, e-mail, phone; one line per contact."""
    lines = []
    for contact in book.sorted_by_name():
        name = contact.full_name
        if "," in name or '"' in name:
            name = '"' + name.replace('"', '""') + '"'
        lines.append(",".join([name, contact.email, contact.phone]))
    return "\n".join(lines) + ("\n" if lines else "")


def summary(book):
    """One line for the daily mail: how many contacts, how many pages, which file names would be written."""
    files = export_all(book)
    pages = len(book.group_by_initial())
    return f"{len(book)} contacts on {pages} pages, files: " + ", ".join(sorted(files))
