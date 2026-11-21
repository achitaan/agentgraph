"""Read a small settings file of `key = value` lines."""


class ConfigError(Exception):
    """The settings file is not valid."""


def load(path):
    """Return the settings in the file at `path` as a dict of strings."""
    values = {}
    with open(path, encoding="utf-8") as stream:
        for number, raw in enumerate(stream, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep:
                raise ConfigError(f"line {number}: expected key = value, got {line!r}")
            value = value.strip()
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            values[key.strip()] = value
    return values


def get_int(values, key, default=None):
    """The setting `key` as an int, or `default` when it is missing."""
    if key not in values:
        return default
    try:
        return int(values[key])
    except ValueError:
        raise ConfigError(f"{key} is not a number: {values[key]!r}") from None
