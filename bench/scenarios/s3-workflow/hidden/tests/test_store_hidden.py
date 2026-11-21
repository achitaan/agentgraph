import json
import os
import tempfile
import unittest

from kv.store import Store


class StoreHidden(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "s.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_roundtrip(self):
        s = Store(self.path)
        self.assertEqual(s.keys(), [])
        s.set("b", "2")
        s.set("a", "1")
        self.assertEqual(Store(self.path).keys(), ["a", "b"])
        self.assertEqual(Store(self.path).get("a"), "1")
        self.assertIsNone(Store(self.path).get("zz"))
        self.assertEqual(Store(self.path).get("zz", "d"), "d")

    def test_delete(self):
        s = Store(self.path)
        s.set("a", "1")
        self.assertTrue(s.delete("a"))
        self.assertFalse(s.delete("a"))
        self.assertEqual(Store(self.path).keys(), [])

    def test_bad_file(self):
        with open(self.path, "w") as f:
            json.dump([1], f)
        with self.assertRaises(ValueError):
            Store(self.path)

    def test_types(self):
        s = Store(self.path)
        with self.assertRaises(TypeError):
            s.set("a", 1)
        with self.assertRaises(TypeError):
            s.set(1, "a")


if __name__ == "__main__":
    unittest.main()
