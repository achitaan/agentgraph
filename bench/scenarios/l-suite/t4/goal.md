Our service reads its settings with `load(path)` in config.py (app.py is the caller). The reader is too naive: it keeps blank
lines and comments as settings and accepts garbage lines. Make `load` follow these rules:

- Blank lines, and lines whose first non-space character is `#`, are ignored.
- The value is everything after the first `=`, stripped of surrounding spaces. A value wrapped in double quotes loses the quotes
  and keeps everything inside as it is: `#`, `=` and leading or trailing spaces included.
- When a key appears twice, the last one wins.
- A line with no `=` raises `ConfigError`, and the message names the line number (1-based), for example `line 3`.

The visible check is `python3 check.py` (run it in the project folder; exit code 0 means it passes).
