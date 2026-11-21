`parse_settings(text)` in `settings.py` reads the service's settings file. It is too simple; please make it follow these rules.
The result is a dict of strings; keys keep their case. Leave `get_int`, `get_bool` and `app.py` as they are.

1. Blank lines are ignored, and so are lines whose first non-blank character is `#` or `;`.
2. A setting is `key = value`. The line is split at the FIRST `=`; key and value are stripped of surrounding white space. Any later `=` is part of the value.
3. A line `[name]` starts a section (white space is allowed around the line and inside the brackets; the name is used as written, case included). Every setting after it is stored under `name.key` until the next header. Settings before any header use the plain key. The same section may be opened again later.
4. A header that is not closed by `]`, or that has anything after the `]` except white space or a comment (rule 9), is an error. So is an empty name (`[]`, `[ ]`).
5. A line that is not blank, not a comment, not a header and has no `=` is an error.
6. A setting whose key is empty (`= value`) is an error.
7. An empty value is allowed; it is stored as an empty string.
8. A value that starts with `"` is quoted. It ends at the next `"` that is not escaped; the quotes are removed, white space inside is kept exactly, and `#` and `;` inside have no special meaning. Inside quotes `\"` is a quote, `\\` is a backslash, `\n` is a newline and `\t` is a tab; any other backslash sequence is an error. A quoted value that is never closed is an error. After the closing quote only white space or a comment may follow; anything else is an error.
9. An unquoted value ends at a `#` that is at the start of the value or follows white space; that `#` and everything after it are a comment and the value is stripped again (`a #b` is `a`, `# none` is empty, but `a#b` keeps its `#`). Only `#` starts a comment at the end of a line; `;` does so only at the start of a line (rule 1). A comment is also allowed after a header and after the closing quote of a quoted value.
10. A setting whose full key (with its section prefix) was already set earlier is an error. Keys are case-sensitive, so `a` and `A` are different. The same key name in different sections is fine.
11. Every error is a `SettingsError` whose message starts with `line N: `, where N is the 1-based number of the line in the text (blank and comment lines count; `\r\n` ends a line like `\n`), followed by a short reason. For rule 10 the reason names the full key. When there are several errors, the first one in the text is reported.

The visible check is `python3 check.py` (run it in the project folder; exit code 0 means it passes); it does not test every rule.
