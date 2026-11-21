"""The service's start-up code reads its settings through settings.py."""
from settings import SettingsError, get_bool, get_int, parse_settings


def load_config(text):
    """The service configuration from the text of a settings file."""
    values = parse_settings(text)
    return {
        "host": values.get("server.host", "localhost"),
        "port": get_int(values, "server.port", 8080),
        "debug": get_bool(values, "debug", False),
        "motd": values.get("motd", ""),
    }


def describe(text):
    """One line for the log; a bad file gives the error message instead of raising."""
    try:
        config = load_config(text)
    except SettingsError as error:
        return f"settings rejected: {error}"
    return f"{config['host']}:{config['port']} debug={config['debug']}"
