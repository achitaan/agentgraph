import unittest

from app.slug import slugify


class SlugHidden(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(slugify("  Hello, World!  "), "hello-world")

    def test_slug_unchanged(self):
        self.assertEqual(slugify("Already-a-slug"), "already-a-slug")

    def test_only_symbols(self):
        self.assertEqual(slugify("!!!"), "")

    def test_runs_collapse(self):
        self.assertEqual(slugify("a --  b__c"), "a-b-c")

    def test_digits_and_non_ascii(self):
        self.assertEqual(slugify("Caf\u00e9 2024"), "caf-2024")

    def test_type(self):
        with self.assertRaises(TypeError):
            slugify(None)
        with self.assertRaises(TypeError):
            slugify(5)


if __name__ == "__main__":
    unittest.main()
