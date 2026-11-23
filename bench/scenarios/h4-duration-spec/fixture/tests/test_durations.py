"""Checks for parse_duration. These cover rules 1, 2 and 4 of SPEC.md only; the other rules still apply."""
import unittest

from durations import format_duration, parse_duration
from durations.timer import Countdown


class ParseVisible(unittest.TestCase):
    def test_units_add_up(self):  # rule 1
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("0s"), 0)

    def test_whitespace_between_parts(self):  # rule 2
        self.assertEqual(parse_duration("1h 30m"), 5400)
        self.assertEqual(parse_duration("2d   4h"), 187200)

    def test_bare_number_is_seconds(self):  # rule 4
        self.assertEqual(parse_duration("90"), 90)


class OtherModules(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_duration(3725), "1h2m5s")
        self.assertEqual(format_duration(0), "0s")

    def test_countdown(self):
        timer = Countdown.from_text("1m 30s")
        self.assertEqual(str(timer), "1m30s")
        self.assertEqual(timer.tick(100), 0)
        self.assertTrue(timer.finished)


if __name__ == "__main__":
    unittest.main()
