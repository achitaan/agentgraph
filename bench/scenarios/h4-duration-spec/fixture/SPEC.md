# parse_duration: the spec

`durations.parse.parse_duration(text)` turns a short written duration into a whole number of seconds (an `int`).
All ten rules below apply. Wherever a rule says "error", raise the named exception.

1. **Units.** A duration is one or more parts, each a whole number followed by a unit letter: `d` = 86400 seconds,
   `h` = 3600, `m` = 60, `s` = 1. `"1h30m"` is 5400. The parts add up. Zero is a fine value: `"0s"` is 0.
2. **Spacing.** Parts may be written together (`"1h30m"`) or separated by whitespace (`"1h 30m"`, `"2d   4h"`).
   The number and its own unit must touch: `"1 h"` is a `ValueError`.
3. **Case.** Unit letters are case-insensitive: `"1H30M"` equals `"1h30m"`.
4. **Bare number.** If the whole text is a single number with no unit, it is seconds: `"90"` is 90. A bare number
   anywhere else (`"1h 30"`, `"30 1h"`) is a `ValueError`.
5. **Outer whitespace.** Whitespace at the start and end of the text is ignored. After that, empty text is a
   `ValueError`.
6. **Digits.** Numbers are written with the ASCII digits 0-9 only. Leading zeros are fine (`"01h"` is 3600). No sign,
   no decimal point, no separators (`"-5s"`, `"+5s"`, `"1.5h"`, `"1_000s"` are each a `ValueError`), and no digits from
   other scripts.
7. **Each unit once.** The same unit may not appear twice: `"1h 2h"` is a `ValueError`.
8. **Order.** Units must go from large to small: d, then h, then m, then s. `"30m1h"` is a `ValueError`.
9. **Limit.** The total may not be more than 365 days (31,536,000 seconds). Exactly 365 days is allowed; one second
   more is a `ValueError`.
10. **Type.** If `text` is not a `str` (for example `None`, an `int` or `bytes`), raise `TypeError`.

Any text that is not made of parts as described above (letters other than d, h, m, s; stray symbols) is a `ValueError`.
