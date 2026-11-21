"""Visible check: run `python3 check.py` in this folder. Exit code 0 means it passes."""
import sys

from pricing import add_tax, apply_discount

DISCOUNTS = [(25, 10, 23), (5, 10, 5), (1000, 10, 900), (999, 50, 500), (101, 50, 51), (12345, 0, 12345), (100, 100, 0)]
TAXES = [(5, 10, 6), (25, 10, 28), (1000, 20, 1200), (333, 50, 500), (7, 50, 11), (500, 0, 500)]


def main():
    failures = []
    for cents, percent, want in DISCOUNTS:
        got = apply_discount(cents, percent)
        if got != want or type(got) is not int:
            failures.append(f"apply_discount({cents}, {percent}) = {got!r}, expected {want}")
    for cents, percent, want in TAXES:
        got = add_tax(cents, percent)
        if got != want or type(got) is not int:
            failures.append(f"add_tax({cents}, {percent}) = {got!r}, expected {want}")
    for line in failures:
        print("FAIL:", line)
    if not failures:
        print("ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
