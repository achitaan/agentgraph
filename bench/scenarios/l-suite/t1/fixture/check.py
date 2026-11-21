"""Visible check: run `python3 check.py` in this folder. Exit code 0 means it passes."""
import sys

import inventory

ITEMS = [
    {"name": "bolt", "price": 2.0, "qty": 10},
    {"name": "nut", "price": 1.0, "qty": 30},
]


def main():
    failures = []
    try:
        got = inventory.average_price(ITEMS)
        if abs(got - 1.25) > 1e-9:
            failures.append(f"average_price(ITEMS) = {got!r}, expected 1.25")
        if inventory.average_price([]) != 0.0:
            failures.append("average_price([]) should be 0.0")
    except Exception as error:  # noqa: BLE001
        failures.append(f"average_price raised {error!r}")
    for line in failures:
        print("FAIL:", line)
    if not failures:
        print("ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
