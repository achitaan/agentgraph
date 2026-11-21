# kv: a tiny key-value tool

Standard library only.

## kv/store.py

`class Store(path)`: a JSON file mapping string keys to string values.
- `Store(path)` reads the file if it exists; a missing file is an empty store; a file that is not a JSON object raises `ValueError`.
- `get(key, default=None)`, `set(key, value)`, `delete(key)` (returns True when the key existed), `keys()` (sorted list).
- `set` and `delete` save the file at once (write a temporary file next to it and rename it over).
- Keys and values must be `str`, else `TypeError`.

## kv/commands.py

Functions taking a `Store` and returning the text to print (no printing here):
- `cmd_set(store, key, value)` -> `"ok"`
- `cmd_get(store, key)` -> the value, or raises `KeyError(key)` when missing
- `cmd_del(store, key)` -> `"deleted"` or `"missing"`
- `cmd_list(store)` -> the keys, one per line, sorted (empty text for an empty store)

## kv/cli.py

`main(argv, path="kv.json")` -> exit code (0 ok, 1 missing key for `get`, 2 usage error). `argv` is e.g. `["set", "a", "1"]`, `["get", "a"]`, `["del", "a"]`, `["list"]`. It prints the commands' text on standard output (a usage message on standard error for code 2) and uses `Store(path)`.
