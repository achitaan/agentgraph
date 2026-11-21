import contextlib
import io
import os
import tempfile
import unittest

from kv.cli import main


def run(argv, path):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv, path)
    return code, out.getvalue(), err.getvalue()


class CliHidden(unittest.TestCase):
    def test_flow(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kv.json")
            self.assertEqual(run(["set", "a", "1"], path)[:2], (0, "ok\n"))
            self.assertEqual(run(["set", "b", "2"], path)[0], 0)
            self.assertEqual(run(["get", "a"], path)[:2], (0, "1\n"))
            self.assertEqual(run(["list"], path)[:2], (0, "a\nb\n"))
            self.assertEqual(run(["del", "a"], path)[:2], (0, "deleted\n"))
            self.assertEqual(run(["get", "a"], path)[0], 1)

    def test_usage(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "kv.json")
            for argv in ([], ["nope"], ["set", "a"], ["get"], ["list", "x"]):
                code, out, err = run(argv, path)
                self.assertEqual(code, 2, argv)
                self.assertTrue(err.strip())


if __name__ == "__main__":
    unittest.main()
