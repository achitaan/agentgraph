"""Visible check: run `python3 check.py` in this folder. Exit code 0 means it passes."""
import sys

from settings import SettingsError, parse_settings


def error_of(text):
    try:
        parse_settings(text)
    except SettingsError as error:
        return str(error)
    except Exception as error:  # noqa: BLE001
        return f"wrong exception {error!r}"
    return None


def main():
    failures = []
    text = "# top\n\n  name  =  demo \n[server]\nhost = example.org\nurl = http://h/?a=b\n\n[db]\nuser=ada\n"
    want = {"name": "demo", "server.host": "example.org", "server.url": "http://h/?a=b", "db.user": "ada"}
    try:
        got = parse_settings(text)
    except Exception as error:  # noqa: BLE001
        got = repr(error)
    if got != want:
        failures.append(f"parse_settings gave {got!r}, expected {want!r}")
    message = error_of("# one\n\nport = 1\nthis is not a setting\n")
    if message is None or not message.startswith("line 4: "):
        failures.append(f"a line without '=' on line 4: message {message!r} should start with 'line 4: '")
    message = error_of("a = 1\nb = 2\na = 3\n")
    if message is None or not message.startswith("line 3: "):
        failures.append(f"a repeated key on line 3: message {message!r} should start with 'line 3: '")
    if error_of("[server\nhost = x\n") is None:
        failures.append("an unclosed section header should raise SettingsError")
    for line in failures:
        print("FAIL:", line)
    if not failures:
        print("ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
