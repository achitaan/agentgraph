import json
import tempfile
import unittest
from pathlib import Path

from contacts import migrate, report, store


class ContactsTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "c.json"

    def test_new_record_shape(self):
        self.assertEqual(
            store.make_record("Ada Lovelace", "555-0100", "a@x.org"),
            {"first": "Ada", "last": "Lovelace", "phones": ["555-0100"], "email": "a@x.org"},
        )

    def test_add_and_load_version_2(self):
        store.add(self.path, "Grace Hopper")
        self.assertEqual(json.loads(self.path.read_text())["version"], 2)
        self.assertEqual(store.load(self.path)[0]["last"], "Hopper")

    def test_old_file_is_refused(self):
        self.path.write_text(json.dumps([{"name": "Ada Lovelace", "phone": None, "email": None}]))
        with self.assertRaises(store.OldFormatError):
            store.load(self.path)

    def test_migrate_simple_name(self):
        self.path.write_text(json.dumps([{"name": "Ada Lovelace", "phone": "555-0100", "email": None}]))
        self.assertTrue(migrate.migrate_file(self.path))
        self.assertEqual(store.load(self.path), [{"first": "Ada", "last": "Lovelace", "phones": ["555-0100"], "email": None}])

    def test_report_simple(self):
        store.add(self.path, "Ada Lovelace", "555-0100")
        store.add(self.path, "Alan Turing")
        self.assertEqual(report.render(self.path), "L (1)\n  Lovelace, Ada - 555-0100\nT (1)\n  Turing, Alan")


if __name__ == "__main__":
    unittest.main()
