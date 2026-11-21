Job durations in our runner are strings like '1h30m'. durations.py (used by scheduler.py) does not know days or weeks yet, and
it quietly returns a wrong number for bad input.

Change `parse_duration` in durations.py: add `d` (days) and `w` (weeks); units are case-insensitive ('1H30M' works). Anything
that is not a sequence of number-plus-unit pairs with nothing else in it must raise ValueError instead of returning a partial
or zero result: an empty string, a bare number such as '5', a unit with no number such as 'h', an unknown unit such as '3x',
and leftover characters such as '1h2' or '1h 30m' (spaces included). `format_duration` and scheduler.py stay as they are.

The visible check is `python3 check.py` (run it in the project folder; exit code 0 means it passes).
