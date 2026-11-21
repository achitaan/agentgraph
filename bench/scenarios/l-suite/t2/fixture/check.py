"""Visible check: run `python3 check.py` in this folder. Exit code 0 means it passes."""
import sys

from durations import format_duration, parse_duration


def raises_value_error(text):
    try:
        parse_duration(text)
    except ValueError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def main():
    failures = []
    cases = [("1h30m", 5400), ("45s", 45), ("2d", 172800), ("1w", 604800), ("1d2h", 93600)]
    for text, want in cases:
        try:
            got = parse_duration(text)
        except Exception as error:  # noqa: BLE001
            got = repr(error)
        if got != want:
            failures.append(f"parse_duration({text!r}) = {got!r}, expected {want}")
    for text in ("", "3x"):
        if not raises_value_error(text):
            failures.append(f"parse_duration({text!r}) should raise ValueError")
    if format_duration(5400) != "1h30m":
        failures.append("format_duration(5400) changed")
    for line in failures:
        print("FAIL:", line)
    if not failures:
        print("ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
