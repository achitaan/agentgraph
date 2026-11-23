import json
import tempfile
import unittest
from pathlib import Path

from contacts import cli, migrate, report, store

ROOT = Path(__file__).resolve().parents[1]

ORIGINAL_NAMES = ["Ada Lovelace", "Mary Ann Smith", "Cher", "Jean-Luc de la Mer", "Grace Hopper", "Alan Turing"]


def rec(first, last, phones=(), email=None):
    return {"first": first, "last": last, "phones": list(phones), "email": email}


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "c.json"

    def write_old(self, rows):
        self.path.write_text(json.dumps(rows))


class Step1Shape(Base):
    def test_names_split_on_the_last_word(self):
        self.assertEqual(store.make_record("Mary Ann Smith"), rec("Mary Ann", "Smith"))

    def test_one_word_name(self):
        self.assertEqual(store.make_record("Cher"), rec("Cher", ""))

    def test_extra_spaces_are_collapsed(self):
        self.assertEqual(store.make_record("  Jean-Luc   de la  Mer "), rec("Jean-Luc de la", "Mer"))

    def test_empty_phone_is_no_phone(self):
        self.assertEqual(store.make_record("Ada Lovelace", "", None)["phones"], [])

    def test_missing_file_loads_empty(self):
        self.assertEqual(store.load(self.dir / "nope.json"), [])

    def test_cli_add_writes_version_2(self):
        import io
        cli.main(["--file", str(self.path), "add", "Grace Hopper", "--phone", "555-1"], out=io.StringIO())
        data = json.loads(self.path.read_text())
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["contacts"], [rec("Grace", "Hopper", ["555-1"])])


class Step2Migrate(Base):
    def old_rows(self):
        return [
            {"name": "Mary Ann Smith", "phone": "555-0111", "email": None},
            {"name": "Cher", "phone": None, "email": "c@x.org"},
            {"name": "Ada Lovelace", "phone": "", "email": None},
        ]

    def test_migrates_by_the_name_rule_in_order(self):
        self.write_old(self.old_rows())
        self.assertTrue(migrate.migrate_file(self.path))
        self.assertEqual(
            store.load(self.path),
            [rec("Mary Ann", "Smith", ["555-0111"]), rec("Cher", "", [], "c@x.org"), rec("Ada", "Lovelace")],
        )

    def test_old_copy_is_kept(self):
        self.write_old(self.old_rows())
        migrate.migrate_file(self.path)
        backup = self.path.with_name("c.json.v1.bak")
        self.assertEqual(json.loads(backup.read_text()), self.old_rows())

    def test_second_run_changes_nothing(self):
        self.write_old(self.old_rows())
        migrate.migrate_file(self.path)
        before = self.path.read_text()
        backup = self.path.with_name("c.json.v1.bak")
        backup_before = backup.read_text()
        self.assertFalse(migrate.migrate_file(self.path))
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual(backup.read_text(), backup_before)

    def test_missing_file_returns_false(self):
        self.assertFalse(migrate.migrate_file(self.dir / "nope.json"))

    def test_repository_data_file_is_migrated(self):
        data = json.loads((ROOT / "data" / "contacts.json").read_text())
        self.assertEqual(data["version"], 2)
        self.assertEqual(
            data["contacts"],
            [
                rec("Ada", "Lovelace", ["555-0100"], "ada@example.com"),
                rec("Mary Ann", "Smith", ["555-0111"]),
                rec("Cher", ""),
                rec("Jean-Luc de la", "Mer", ["555-0122"], "jl@example.com"),
                rec("Grace", "Hopper", [], "grace@example.com"),
                rec("Alan", "Turing", ["555-0133"]),
            ],
        )

    def test_repository_backup_holds_the_original(self):
        old = json.loads((ROOT / "data" / "contacts.json.v1.bak").read_text())
        self.assertEqual([r["name"] for r in old], ORIGINAL_NAMES)
        self.assertEqual(old[0], {"name": "Ada Lovelace", "phone": "555-0100", "email": "ada@example.com"})


class Step3Report(Base):
    def test_repository_report(self):
        self.assertEqual(
            report.render(ROOT / "data" / "contacts.json"),
            "\n".join([
                "C (1)",
                "  Cher",
                "H (1)",
                "  Hopper, Grace",
                "L (1)",
                "  Lovelace, Ada - 555-0100",
                "M (1)",
                "  Mer, Jean-Luc de la - 555-0122",
                "S (1)",
                "  Smith, Mary Ann - 555-0111",
                "T (1)",
                "  Turing, Alan - 555-0133",
            ]),
        )

    def test_empty_book(self):
        store.save(self.path, [])
        self.assertEqual(report.render(self.path), "(no contacts)")

    def test_order_inside_a_group_is_by_last_then_first_lowercase(self):
        store.save(self.path, [rec("zed", "Smith"), rec("Amy", "smith"), rec("Bob", "Sanders")])
        self.assertEqual(
            report.render(self.path),
            "S (3)\n  Sanders, Bob\n  smith, Amy\n  Smith, zed",
        )

    def test_group_letter_is_upper_case_and_groups_are_alphabetical(self):
        store.save(self.path, [rec("a", "zulu"), rec("b", "alpha")])
        self.assertEqual(report.render(self.path), "A (1)\n  alpha, b\nZ (1)\n  zulu, a")

    def test_one_word_name_is_grouped_by_first(self):
        store.save(self.path, [rec("cher", ""), rec("Bo", "Cole")])
        self.assertEqual(report.render(self.path), "C (2)\n  cher\n  Cole, Bo")

    def test_phones_joined(self):
        store.save(self.path, [rec("Ada", "Lovelace", ["1", "2"])])
        self.assertEqual(report.render(self.path), "L (1)\n  Lovelace, Ada - 1, 2")

    def test_old_file_is_refused_by_report(self):
        self.write_old([{"name": "Ada Lovelace", "phone": None, "email": None}])
        with self.assertRaises(store.OldFormatError):
            report.render(self.path)
        self.assertEqual(json.loads(self.path.read_text())[0]["name"], "Ada Lovelace")

    def test_migrated_old_file_reports(self):
        self.write_old([{"name": "Mary Ann Smith", "phone": "555-0111", "email": None}, {"name": "Cher", "phone": None, "email": None}])
        migrate.migrate_file(self.path)
        self.assertEqual(report.render(self.path), "C (1)\n  Cher\nS (1)\n  Smith, Mary Ann - 555-0111")


if __name__ == "__main__":
    unittest.main()
