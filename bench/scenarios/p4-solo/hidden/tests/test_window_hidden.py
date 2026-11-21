import unittest

from textstats import moving_sum


class MovingSumHiddenTests(unittest.TestCase):
    def test_pairs(self):
        self.assertEqual(moving_sum([1, 2, 3, 4], 2), [3, 5, 7])

    def test_whole_list_is_one_window(self):
        self.assertEqual(moving_sum([5, 6, 7], 3), [18])

    def test_size_one(self):
        self.assertEqual(moving_sum([4, 9], 1), [4, 9])

    def test_window_larger_than_list(self):
        self.assertEqual(moving_sum([1, 2], 3), [])

    def test_bad_size(self):
        with self.assertRaises(ValueError):
            moving_sum([1], 0)


if __name__ == "__main__":
    unittest.main()
