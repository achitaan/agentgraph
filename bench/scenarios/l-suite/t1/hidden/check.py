"""Hidden consumer check for t1. Run with the work folder as the current directory."""
import sys

sys.path.insert(0, ".")
import inventory  # noqa: E402
import report  # noqa: E402

bad = []
items = [{"name": "a", "price": 10.0, "qty": 1}, {"name": "b", "price": 1.0, "qty": 9}]
if abs(inventory.average_price(items) - 1.9) > 1e-9:
    bad.append("weighted average")
if inventory.average_price([{"name": "z", "price": 5.0, "qty": 0}]) != 0.0:
    bad.append("zero quantity")
if inventory.average_price([]) != 0.0:
    bad.append("empty")
if inventory.total_value(items) != 19.0:
    bad.append("total_value changed")
if inventory.names(items) != ["a", "b"]:
    bad.append("names changed")
if report.summary(items) != "2 items worth 19.00":
    bad.append("report.summary changed")
print("hidden t1:", "FAIL " + ", ".join(bad) if bad else "ok")
sys.exit(1 if bad else 0)
