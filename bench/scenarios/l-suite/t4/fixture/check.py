"""Visible check: run `python3 check.py` in this folder. Exit code 0 means it passes."""
import os
import sys
import tempfile

from config import ConfigError, load


def read(text):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "app.conf")
        with open(path, "w", encoding="utf-8") as stream:
            stream.write(text)
        return load(path)


def main():
    failures = []
    try:
        got = read("# a comment\n\nport = 9000\nname = \"my service\"\nmode=fast\nport = 9100\n")
        want = {"port": "9100", "name": "my service", "mode": "fast"}
        if got != want:
            failures.append(f"load gave {got!r}, expected {want!r}")
    except Exception as error:  # noqa: BLE001
        failures.append(f"load raised {error!r}")
    try:
        read("port = 1\nthis line has no equals sign\n")
        failures.append("a line without '=' should raise ConfigError")
    except ConfigError:
        pass
    except Exception as error:  # noqa: BLE001
        failures.append(f"a line without '=' raised {error!r}, not ConfigError")
    for line in failures:
        print("FAIL:", line)
    if not failures:
        print("ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
