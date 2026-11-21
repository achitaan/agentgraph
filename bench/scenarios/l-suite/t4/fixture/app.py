"""The service's start-up code reads its settings through config.py."""
from config import get_int, load


def settings(path):
    values = load(path)
    return {"port": get_int(values, "port", 8080), "name": values.get("name", "service")}
