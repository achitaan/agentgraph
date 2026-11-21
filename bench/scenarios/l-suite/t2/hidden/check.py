"""Hidden consumer check for t2. Run with the work folder as the current directory."""
import sys

sys.path.insert(0, ".")
import durations  # noqa: E402
import scheduler  # noqa: E402

bad = []


def expect_error(text):
    try:
        value = durations.parse_duration(text)
    except ValueError:
        return
    except Exception as error:  # noqa: BLE001
        bad.append(f"{text!r} raised {type(error).__name__}, not ValueError")
        return
    bad.append(f"{text!r} returned {value!r}")


for text in ("", "5", "h", "3x", "1h2", "1h 30m", "m5", "1h30"):
    expect_error(text)
for text, want in (("1H30M", 5400), ("2W", 1209600), ("1w1d1h1m1s", 694861), ("0s", 0), ("90m", 5400)):
    got = durations.parse_duration(text)
    if got != want:
        bad.append(f"parse_duration({text!r}) = {got!r}, want {want}")
for seconds, want in ((5400, "1h30m"), (0, "0s"), (86400, "24h"), (61, "1m1s")):
    if durations.format_duration(seconds) != want:
        bad.append(f"format_duration({seconds}) changed")
if scheduler.deadline(100, "1m") != 160 or scheduler.deadline(0, "1d") != 86400:
    bad.append("scheduler.deadline")
try:
    scheduler.deadline(0, "bogus")
    bad.append("scheduler.deadline accepted junk")
except ValueError:
    pass
print("hidden t2:", "FAIL " + "; ".join(bad) if bad else "ok")
sys.exit(1 if bad else 0)
