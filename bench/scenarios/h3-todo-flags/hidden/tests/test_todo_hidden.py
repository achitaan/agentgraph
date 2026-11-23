import io
import json
import tempfile
import unittest
from pathlib import Path

from todo import cli, render, store
from todo.model import Task


def run(path, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["--file", str(path), *argv], out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "t.json"

    def write_old(self):
        self.path.write_text(json.dumps([
            {"id": 1, "title": "Buy milk", "done": False},
            {"id": 2, "title": "Walk dog", "done": True},
        ]))


class ExistingBehaviour(Base):
    def test_plain_and_done_lines_unchanged(self):
        self.assertEqual(render.render_task(Task(3, "Buy milk")), "[ ] 3 Buy milk")
        self.assertEqual(render.render_task(Task(3, "Buy milk", done=True)), "[x] 3 Buy milk")

    def test_long_title_still_cut_to_27_plus_dots(self):
        self.assertEqual(render.render_task(Task(1, "y" * 31)), "[ ] 1 " + "y" * 27 + "...")
        self.assertEqual(render.render_task(Task(1, "y" * 30)), "[ ] 1 " + "y" * 30)

    def test_ids_line_up_and_empty_list(self):
        self.assertEqual(render.render_list([Task(9, "a"), Task(10, "b")]), "[ ]  9 a\n[ ] 10 b")
        self.assertEqual(render.render_list([]), "(no tasks)")

    def test_old_file_without_new_fields_still_lists_the_same(self):
        self.write_old()
        self.assertEqual(run(self.path, "list"), (0, "[ ] 1 Buy milk\n[x] 2 Walk dog\n", ""))

    def test_done_on_an_old_file(self):
        self.write_old()
        self.assertEqual(run(self.path, "done", "1"), (0, "done 1\n", ""))
        self.assertEqual(run(self.path, "list")[1], "[x] 1 Buy milk\n[x] 2 Walk dog\n")

    def test_old_file_tasks_have_defaults(self):
        self.write_old()
        task = store.load(self.path)[0]
        self.assertEqual((task.due, task.priority), (None, "normal"))

    def test_unknown_id_and_empty_title(self):
        self.assertEqual(run(self.path, "done", "4")[::2], (1, "error: no task 4\n"))
        self.assertEqual(run(self.path, "add", "  ")[::2], (2, "error: a task needs a title\n"))


class DueDates(Base):
    def test_due_is_stored_and_shown(self):
        self.assertEqual(run(self.path, "add", "Pay rent", "--due", "2026-02-01")[:2], (0, "added 1\n"))
        self.assertEqual(json.loads(self.path.read_text())[0]["due"], "2026-02-01")
        self.assertEqual(run(self.path, "list")[1], "[ ] 1 Pay rent (due 2026-02-01)\n")

    def test_due_goes_after_the_shortened_title_and_is_never_cut(self):
        run(self.path, "add", "x" * 40, "--due", "2026-02-01")
        self.assertEqual(run(self.path, "list")[1], "[ ] 1 " + "x" * 27 + "... (due 2026-02-01)\n")

    def test_no_due_means_no_suffix(self):
        run(self.path, "add", "Pay rent")
        self.assertEqual(run(self.path, "list")[1], "[ ] 1 Pay rent\n")
        self.assertIsNone(json.loads(self.path.read_text())[0]["due"])

    def test_bad_dates_are_refused_and_nothing_is_stored(self):
        for bad in ("2026-02-30", "2026-2-1", "20260201", "tomorrow", "2026-13-01"):
            code, out, err = run(self.path, "add", "Pay rent", "--due", bad)
            self.assertEqual((code, out, err), (2, "", f"error: bad due date: {bad}\n"), bad)
        self.assertFalse(self.path.exists())


class Priority(Base):
    def test_high_is_marked_and_stored(self):
        self.assertEqual(run(self.path, "add", "Call bank", "--priority", "high")[:2], (0, "added 1\n"))
        self.assertEqual(json.loads(self.path.read_text())[0]["priority"], "high")
        self.assertEqual(run(self.path, "list")[1], "[ ] 1 ! Call bank\n")

    def test_normal_is_the_default_and_looks_plain(self):
        run(self.path, "add", "a")
        run(self.path, "add", "b", "--priority", "normal")
        self.assertEqual(run(self.path, "list")[1], "[ ] 1 a\n[ ] 2 b\n")
        self.assertEqual([d["priority"] for d in json.loads(self.path.read_text())], ["normal", "normal"])

    def test_bad_priority_is_refused(self):
        code, out, err = run(self.path, "add", "a", "--priority", "urgent")
        self.assertEqual((code, out, err), (2, "", "error: bad priority: urgent\n"))
        self.assertFalse(self.path.exists())

    def test_mark_sits_between_the_id_and_the_title_when_ids_line_up(self):
        text = render.render_list([Task(9, "a", priority="high"), Task(10, "b")])
        self.assertEqual(text, "[ ]  9 ! a\n[ ] 10 b")


class Together(Base):
    def test_both_on_one_task(self):
        run(self.path, "add", "one")
        run(self.path, "add", "two")
        run(self.path, "add", "Buy milk", "--priority", "high", "--due", "2026-01-05")
        self.assertEqual(run(self.path, "list")[1].splitlines()[-1], "[ ] 3 ! Buy milk (due 2026-01-05)")

    def test_done_keeps_due_and_priority(self):
        run(self.path, "add", "Buy milk", "--due", "2026-01-05", "--priority", "high")
        self.assertEqual(run(self.path, "done", "1")[0], 0)
        self.assertEqual(run(self.path, "list")[1], "[x] 1 ! Buy milk (due 2026-01-05)\n")

    def test_long_title_with_both(self):
        task = Task(1, "z" * 35, due="2026-03-04", priority="high")
        self.assertEqual(render.render_task(task), "[ ] 1 ! " + "z" * 27 + "... (due 2026-03-04)")


if __name__ == "__main__":
    unittest.main()
