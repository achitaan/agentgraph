import io
import tempfile
import unittest
from pathlib import Path

from todo import cli, render, store
from todo.model import Task


def run(path, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["--file", str(path), *argv], out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class TodoTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.json"

    def test_plain_task_line(self):
        self.assertEqual(render.render_task(Task(3, "Buy milk")), "[ ] 3 Buy milk")

    def test_done_task_line(self):
        self.assertEqual(render.render_task(Task(3, "Buy milk", done=True)), "[x] 3 Buy milk")

    def test_ids_line_up(self):
        text = render.render_list([Task(9, "a"), Task(10, "b")])
        self.assertEqual(text, "[ ]  9 a\n[ ] 10 b")

    def test_long_title_is_cut(self):
        line = render.render_task(Task(1, "x" * 40))
        self.assertEqual(line, "[ ] 1 " + "x" * 27 + "...")

    def test_empty_list(self):
        self.assertEqual(render.render_list([]), "(no tasks)")

    def test_add_done_list(self):
        self.assertEqual(run(self.path, "add", "Buy milk")[:2], (0, "added 1\n"))
        self.assertEqual(run(self.path, "done", "1")[0], 0)
        self.assertEqual(run(self.path, "list")[1], "[x] 1 Buy milk\n")

    def test_unknown_id(self):
        code, _, err = run(self.path, "done", "5")
        self.assertEqual((code, err), (1, "error: no task 5\n"))

    # --- new features ---
    def test_due_date_is_shown(self):
        self.assertEqual(run(self.path, "add", "Pay rent", "--due", "2026-02-01")[0], 0)
        self.assertEqual(run(self.path, "list")[1], "[ ] 1 Pay rent (due 2026-02-01)\n")

    def test_high_priority_is_marked(self):
        self.assertEqual(run(self.path, "add", "Call bank", "--priority", "high")[0], 0)
        self.assertEqual(run(self.path, "list")[1], "[ ] 1 ! Call bank\n")


if __name__ == "__main__":
    unittest.main()
