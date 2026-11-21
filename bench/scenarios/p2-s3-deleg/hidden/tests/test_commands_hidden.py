import os
import tempfile
import unittest

from kv.commands import cmd_del, cmd_get, cmd_list, cmd_set
from kv.store import Store


class CommandsHidden(unittest.TestCase):
    def test_commands(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(os.path.join(d, "s.json"))
            self.assertEqual(cmd_list(s), "")
            self.assertEqual(cmd_set(s, "b", "2"), "ok")
            self.assertEqual(cmd_set(s, "a", "1"), "ok")
            self.assertEqual(cmd_get(s, "a"), "1")
            self.assertEqual(cmd_list(s), "a\nb")
            self.assertEqual(cmd_del(s, "a"), "deleted")
            self.assertEqual(cmd_del(s, "a"), "missing")
            with self.assertRaises(KeyError):
                cmd_get(s, "a")


if __name__ == "__main__":
    unittest.main()
