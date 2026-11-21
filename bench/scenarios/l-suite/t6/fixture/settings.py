"""Read the service's settings file."""


class SettingsError(Exception):
    """The settings file is not valid."""


def parse_settings(text):
    """Return the settings in `text` as a dict of strings."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
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
