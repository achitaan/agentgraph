"""Visible check: run `python3 check.py` in this folder. Exit code 0 means it passes."""
import sys

from contacts import Contact, ContactBook
from search import search


def main():
    book = ContactBook()
    book.add(Contact("José", "García", "jg@example.com"))
    book.add(Contact("Zoe", "Brown", "zb@example.com"))
    book.add(Contact("Anna", "Smith", "as@example.com"))
    failures = []
    for query, want in (("jose", ["José García"]), ("garcia", ["José García"]), ("JOSÉ", ["José García"]), ("zoe", ["Zoe Brown"])):
        got = [c.full_name for c in search(book, query)]
        if got != want:
            failures.append(f"search({query!r}) = {got!r}, expected {want!r}")
    for line in failures:
        print("FAIL:", line)
    if not failures:
        print("ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
