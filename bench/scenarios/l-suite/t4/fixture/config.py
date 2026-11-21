"""Read a small settings file of `key = value` lines."""


class ConfigError(Exception):
    """The settings file is not valid."""


def load(path):
    """Return the settings in the file at `path` as a dict of strings."""
    values = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def get_int(values, key, default=None):
    """The setting `key` as an int, or `default` when it is missing."""
    if key not in values:
        return default
    try:
        return int(values[key])
    except ValueError:
        raise ConfigError(f"{key} is not a number: {values[key]!r}") from None
