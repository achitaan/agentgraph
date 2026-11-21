import unittest

from app.units import parse_duration


class DurationHidden(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration(" 45s "), 45)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 86400 + 7200 + 180 + 4)
        self.assertEqual(parse_duration("0s"), 0)

    def test_invalid(self):
        for bad in ("", "  ", "90", "h", "1x", "1m1h", "1h1h", "1h 30m", "-5s", "1.5h", "s1"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_duration(bad)


if __name__ == "__main__":
    unittest.main()
