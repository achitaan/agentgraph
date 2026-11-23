import unittest

from durations import format_duration, parse_duration
from durations.timer import Countdown

YEAR = 365 * 86400


class Rules(unittest.TestCase):
    def bad(self, *texts, exc=ValueError):
        for text in texts:
            with self.subTest(text=text):
                with self.assertRaises(exc):
                    parse_duration(text)

    # rule 1
    def test_1_each_unit_and_the_result_is_an_int(self):
        for text, want in (("1d", 86400), ("1h", 3600), ("1m", 60), ("1s", 1), ("100s", 100), ("90m", 5400)):
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), want)
        self.assertIs(type(parse_duration("1m")), int)

    def test_1_zero_is_fine(self):
        self.assertEqual(parse_duration("0d0h0m0s"), 0)

    # rule 2
    def test_2_space_between_number_and_unit_is_an_error(self):
        self.bad("1 h", "1h 30 m", "5 s")

    def test_2_tabs_and_newlines_separate_parts(self):
        self.assertEqual(parse_duration("1h\t30m"), 5400)
        self.assertEqual(parse_duration("1h\n30m 5s"), 5405)

    # rule 3
    def test_3_case_insensitive(self):
        self.assertEqual(parse_duration("1H30M"), 5400)
        self.assertEqual(parse_duration("2D 1s"), 172801)

    # rule 4
    def test_4_bare_number_elsewhere_is_an_error(self):
        self.bad("1h 30", "30 1h", "1h30", "5 5")

    def test_4_bare_number_with_spaces_around_and_zeros(self):
        self.assertEqual(parse_duration("  45  "), 45)
        self.assertEqual(parse_duration("0"), 0)

    # rule 5
    def test_5_outer_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h 30m \n"), 5400)

    def test_5_empty_is_an_error(self):
        self.bad("", "   ", "\n\t")

    # rule 6
    def test_6_leading_zeros(self):
        self.assertEqual(parse_duration("01h"), 3600)
        self.assertEqual(parse_duration("007"), 7)

    def test_6_signs_decimals_and_separators(self):
        self.bad("-5s", "+5s", "1.5h", "1_000s", "1,000s", "-5", "1.5", "1_0")

    def test_6_digits_from_other_scripts(self):
        self.bad("\u0663s", "\u0661\u0662m", "\uff11h", "\u0663")

    # rule 7
    def test_7_unit_twice(self):
        self.bad("1h 2h", "1h30m5m", "1s1s", "1h 30m 1h")

    # rule 8
    def test_8_wrong_order(self):
        self.bad("30m1h", "5s1m", "1m 1d", "1s 1h")

    def test_8_skipping_units_is_fine(self):
        self.assertEqual(parse_duration("1d 5s"), 86405)
        self.assertEqual(parse_duration("2h5s"), 7205)

    # rule 9
    def test_9_limit_is_inclusive(self):
        self.assertEqual(parse_duration("365d"), YEAR)
        self.assertEqual(parse_duration(str(YEAR)), YEAR)
        self.assertEqual(parse_duration("364d 24h"), YEAR)

    def test_9_over_the_limit(self):
        self.bad("366d", "365d 1s", str(YEAR + 1), "8760h 1s", "999999999999d")

    # rule 10
    def test_10_not_text(self):
        self.bad(None, 90, b"1h", 1.5, ["1h"], exc=TypeError)

    # the rest of the spec
    def test_other_text_is_an_error(self):
        self.bad("abc", "1w", "h", "1h!", "1x", "h1", "1h,30m", "1hh")


class ExistingModules(unittest.TestCase):
    def test_format_and_countdown_unchanged(self):
        self.assertEqual(format_duration(90061), "1d1h1m1s")
        self.assertEqual(format_duration(7200), "2h")
        timer = Countdown.from_text("2m")
        self.assertEqual((timer.remaining, str(timer)), (120, "2m"))

    def test_round_trip(self):
        for seconds in (0, 1, 59, 60, 3661, 86399, 90061, YEAR):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
