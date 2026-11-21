import unittest

from textstats import moving_sum


class MovingSumTests(unittest.TestCase):
    def test_pairs(self):
        self.assertEqual(moving_sum([1, 2, 3, 4], 2), [3, 5, 7])


if __name__ == "__main__":
    unittest.main()
