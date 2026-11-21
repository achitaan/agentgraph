Customers say our invoices are a cent off. A 10% discount on 25 cents should come to 23 cents (22.5 rounds up) but we charge 22,
and 5 cents with 10% tax should be 6 (5.5 rounds up) but we get 5.

Fix `apply_discount` and `add_tax` in pricing.py so each returns whole cents (an int), rounded to the nearest cent with exact
halves going up. Invoices can be huge (whole warehouses, amounts far beyond 10**15 cents), so no precision may be lost on big
amounts. invoice.py calls both functions and must keep working unchanged.

The visible check is `python3 check.py` (run it in the project folder; exit code 0 means it passes).
