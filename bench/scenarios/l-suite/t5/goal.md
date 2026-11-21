Searching our address book for `jose` finds nobody, although José García is in it; `garcia` finds nobody either. Please make the
search ignore accents: typing `jose` must find `José`, typing `José` must find `Jose` (and `Zoë` finds `Zoe`, `Zoe` finds `Zoë`),
and it must work the same when an accent is written as a separate combining mark (an `e` followed by U+0301), in the stored name
or in the typed query. The search keeps its present ranking and order, now comparing the names without accents.

Everything else in the code that uses the same text helpers must keep behaving exactly as it does today.

The visible check is `python3 check.py` (run it in the project folder; exit code 0 means it passes).
