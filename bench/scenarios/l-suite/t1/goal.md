Our inventory helper (inventory.py) can give the total value of the stock but not the average price of a unit.

Add `average_price(items)` to inventory.py. It is the average price per unit, weighted by quantity: total value divided by
total quantity. It returns 0.0 when there are no items, or when the total quantity is zero. Leave the other functions as they are.

The visible check is `python3 check.py` (run it in the project folder; exit code 0 means it passes).
