"""Read the service's settings file."""

_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t"}


class SettingsError(Exception):
    """The settings file is not valid."""


def _fail(number, reason):
    raise SettingsError(f"line {number}: {reason}")


def _quoted(raw, number):
    """The value of a quoted `raw` (it starts with '"'); only white space or a comment may follow the closing quote."""
    out = []
    i = 1
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= len(raw) or raw[i + 1] not in _ESCAPES:
                _fail(number, "bad escape sequence in quoted value")
            out.append(_ESCAPES[raw[i + 1]])
            i += 2
        elif ch == '"':
            rest = raw[i + 1:].strip()
            if rest and not rest.startswith("#"):
                _fail(number, "unexpected text after the closing quote")
            return "".join(out)
        else:
            out.append(ch)
            i += 1
    _fail(number, "quoted value is not closed")


def _plain(raw):
    for i, ch in enumerate(raw):
        if ch == "#" and (i == 0 or raw[i - 1].isspace()):
            return raw[:i].strip()
    return raw.strip()


def parse_settings(text):
    """Return the settings in `text` as a dict of strings."""
    values = {}
    section = ""
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("["):
            end = line.find("]")
            if end < 0:
                _fail(number, "section header is not closed")
            rest = line[end + 1:].strip()
            if rest and not rest.startswith("#"):
                _fail(number, "unexpected text after the section header")
            name = line[1:end].strip()
            if not name:
                _fail(number, "empty section name")
            section = name + "."
            continue
        key, eq, value = line.partition("=")
        if not eq:
            _fail(number, "expected 'key = value'")
        key = key.strip()
        if not key:
            _fail(number, "empty key")
        value = value.strip()
        value = _quoted(value, number) if value.startswith('"') else _plain(value)
        full = section + key
        if full in values:
            _fail(number, f"duplicate key {full}")
        values[full] = value
    return values


def get_int(values, key, default=None):
    """The setting `key` as an int, or `default` when it is missing."""
    if key not in values:
        return default
    try:
        return int(values[key])
    except ValueError:
        raise SettingsError(f"{key} is not a number: {values[key]!r}") from None


def get_bool(values, key, default=False):
    """The setting `key` as a bool: yes/true/on/1 and no/false/off/0 (any case), else SettingsError."""
    if key not in values:
        return default
    word = values[key].strip().lower()
    if word in ("yes", "true", "on", "1"):
        return True
    if word in ("no", "false", "off", "0"):
        return False
    raise SettingsError(f"{key} is not a yes/no value: {values[key]!r}")
