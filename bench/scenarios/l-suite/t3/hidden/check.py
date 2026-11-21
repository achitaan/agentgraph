"""Hidden consumer check for t3. Run with the work folder as the current directory."""
import sys

sys.path.insert(0, ".")
import invoice  # noqa: E402
import pricing  # noqa: E402

bad = []
BIG = 10**17 + 5
if pricing.apply_discount(BIG, 50) != (BIG * 50 + 50) // 100:
    bad.append(f"apply_discount big = {pricing.apply_discount(BIG, 50)!r}")
if pricing.add_tax(BIG, 50) != (BIG * 150 + 50) // 100:
    bad.append(f"add_tax big = {pricing.add_tax(BIG, 50)!r}")
HUGE = 3 * 10**20 + 1
if pricing.apply_discount(HUGE, 10) != (HUGE * 90 + 50) // 100:
    bad.append("apply_discount huge")
if pricing.add_tax(HUGE, 25) != (HUGE * 125 + 50) // 100:
    bad.append("add_tax huge")
for fn, args, want in ((pricing.apply_discount, (45, 10), 41), (pricing.apply_discount, (0, 10), 0),
                       (pricing.add_tax, (45, 10), 50), (pricing.add_tax, (1, 49), 1), (pricing.add_tax, (1, 50), 2)):
    got = fn(*args)
    if got != want or type(got) is not int:
        bad.append(f"{fn.__name__}{args} = {got!r}, want {want}")
if invoice.line_total(25, 4, discount=10, tax=10) != 101:
    bad.append(f"line_total = {invoice.line_total(25, 4, discount=10, tax=10)!r}")
lines = [{"unit_cents": 999, "qty": 3, "discount": 50, "tax": 10}, {"unit_cents": 5, "qty": 1}]
if invoice.invoice_total(lines) != 1655:
    bad.append(f"invoice_total = {invoice.invoice_total(lines)!r}")
print("hidden t3:", "FAIL " + "; ".join(bad) if bad else "ok")
sys.exit(1 if bad else 0)
