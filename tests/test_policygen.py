"""The policy generator (`agentgraph/policygen.py`). Synthetic files in scratch folders only."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgraph import policygen
from tests.winskip import needs_symlinks

HEAD = "title\nintro\n"


def files(**changes):
    base = {"fast": "first\nA\nB\nC\nD\nlast\n", "normal": "first\nA\nB\nC\nD\nlast\n",
            "ultra-fast": "first\nA\nB\nC\nD\nlast\n"}
    base.update(changes)
    return {name: text.encode() for name, text in base.items()}


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = policygen.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class SplitBuildTests(unittest.TestCase):
    def round_trip(self, given):
        source = policygen.split(given)
        self.assertEqual(policygen.render(policygen.parse(source)), given)
        return source

    def test_identical_files_have_no_blocks(self):
        source = self.round_trip(files())
        self.assertNotIn("<!-- only", source)

    def test_difference_in_the_middle_start_and_end(self):
        given = files(fast="FIRST\nA\nB\nX\nC\nD\nlast\n", normal="first\nA\nB\nC\nD\nlast\nmore\n",
                      **{"ultra-fast": "first\nA\nC\nD\nLAST\n"})
        source = self.round_trip(given)
        self.assertIn("<!-- only: fast -->", source)

    def test_trailing_newlines_and_blank_lines(self):
        given = files(fast="first\nA\n\n", normal="first\nA", **{"ultra-fast": "first\nA\n\n\n"})
        self.round_trip(given)

    def test_empty_and_single_newline_files(self):
        self.round_trip(files(fast="", normal="\n", **{"ultra-fast": "first\n"}))

    def test_line_in_one_preset_only(self):
        given = files(normal="first\nA\nB\nonly here\nC\nD\nlast\n")
        source = self.round_trip(given)
        self.assertIn("<!-- only: normal -->\nonly here\n<!-- end -->", source)

    def test_shared_difference_of_two_presets_is_one_block(self):
        given = files(fast="first\nA\nX\nC\nD\nlast\n", normal="first\nA\nX\nC\nD\nlast\n")
        source = self.round_trip(given)
        self.assertIn("<!-- only: fast, normal -->", source)
        self.assertEqual(source.count("<!-- end -->"), 2)

    def test_crlf_and_non_ascii_survive(self):
        given = {"fast": "a\r\nb \u00e9\r\n".encode(), "normal": "a\r\nc\r\n".encode(), "ultra-fast": b"a\r\n"}
        self.round_trip(given)

    def test_text_that_looks_like_a_marker_cannot_be_split(self):
        with self.assertRaises(policygen.PolicyError) as caught:
            policygen.split(files(fast="first\n<!-- only: fast -->\n"))
        self.assertIn("fast line 2", str(caught.exception))

    def test_a_hand_edit_in_shared_text_reaches_every_preset(self):
        source = policygen.split(files()).replace("\nB\n", "\nBB\n")
        for data in policygen.render(policygen.parse(source)).values():
            self.assertIn(b"\nBB\n", data)


class ParseErrorTests(unittest.TestCase):
    def error(self, text):
        with self.assertRaises(policygen.PolicyError) as caught:
            policygen.parse(text)
        return str(caught.exception)

    def test_malformed_marker_names_its_line(self):
        self.assertIn("line 3: malformed only", self.error("<!-- presets: a, b -->\ntext\n<!-- only: a,b -->\nx\n<!-- end -->\n"))
        self.assertIn("line 2: malformed end", self.error("<!-- presets: a -->\n<!-- end  -->\n"))
        self.assertIn("line 2: malformed only", self.error("<!-- presets: a -->\n<!-- only: a --> trailing\n"))

    def test_structure_errors_name_their_line(self):
        self.assertIn("line 2: end without only", self.error("<!-- presets: a -->\n<!-- end -->\n"))
        self.assertIn("line 1: text before", self.error("text\n<!-- presets: a -->\n"))
        self.assertIn("line 3: only inside a block", self.error("<!-- presets: a -->\n<!-- only: a -->\n<!-- only: a -->\n"))
        self.assertIn("line 2: only block never closed", self.error("<!-- presets: a -->\n<!-- only: a -->\nx\n"))
        self.assertIn("line 2: unknown preset b", self.error("<!-- presets: a -->\n<!-- only: b -->\n<!-- end -->\n"))
        self.assertIn("line 3: presets given twice", self.error("<!-- presets: a -->\nx\n<!-- presets: a -->\n"))

    def test_bad_preset_names_are_refused(self):
        self.assertIn("line 1", self.error("<!-- presets: ../x -->\n"))
        self.assertIn("no presets marker", self.error("<!-- note: nothing else -->\n"))

    def test_notes_are_not_written(self):
        policy = policygen.parse("<!-- presets: a -->\n<!-- note: hi -->\nx\n")
        self.assertEqual(policygen.render(policy), {"a": b"x\n"})


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.live = self.tmp / "live"
        self.given = files(fast="first\nA\nB\nX\nD\nlast\n", normal="first\nA\nB\nC\nD\nlast\nextra\n")
        self.write(self.live, self.given)
        self.source = self.tmp / "src.md"
        self.source.write_text(policygen.split(self.given), encoding="utf-8", newline="\n")

    def write(self, root, given):
        for name, data in given.items():
            (root / name).mkdir(parents=True, exist_ok=True)
            (root / name / policygen.FILE).write_bytes(data)

    def test_split_command_then_build_command(self):
        target = self.tmp / "new" / "src.md"
        self.assertEqual(run("split", "--from", str(self.live), "--to", str(target))[0], 0)
        self.assertEqual(target.read_text(encoding="utf-8"), self.source.read_text(encoding="utf-8"))
        out = self.tmp / "out"
        self.assertEqual(run("--source", str(target), "build", "--out", str(out))[0], 0)
        for name, data in self.given.items():
            self.assertEqual((out / name / policygen.FILE).read_bytes(), data)
        code, _, err = run("split", "--from", str(self.live), "--to", str(target))
        self.assertEqual(code, 2)
        self.assertIn("--force", err)

    def test_check_same_exits_zero(self):
        code, out, _ = run("--source", str(self.source), "check", "--against", str(self.live))
        self.assertEqual((code, out), (0, "fast: same\nnormal: same\nultra-fast: same\n"))

    def test_check_reports_line_numbers_not_text(self):
        (self.live / "fast" / policygen.FILE).write_bytes(b"first\nSECRET\nB\nX\nD\nlast\n")
        (self.live / "normal" / policygen.FILE).write_bytes(b"first\nA\nB\nC\nD\nlast\n")
        (self.live / "ultra-fast" / policygen.FILE).write_bytes(self.given["ultra-fast"].rstrip(b"\n"))
        code, out, _ = run("--source", str(self.source), "check", "--against", str(self.live))
        self.assertEqual(code, 1)
        self.assertIn("fast: 1 lines differ (line 2)", out)
        self.assertIn("normal: 1 lines differ (missing after line 6)", out)
        self.assertIn("ultra-fast: 0 lines differ, final newline differs", out)
        self.assertNotIn("SECRET", out)

    def test_check_missing_file(self):
        (self.live / "normal" / policygen.FILE).unlink()
        code, out, _ = run("--source", str(self.source), "check", "--against", str(self.live))
        self.assertEqual(code, 1)
        self.assertIn("normal: missing", out)

    def test_build_never_writes_outside_out(self):
        evil = self.tmp / "evil.md"
        evil.write_text("<!-- presets: ../x -->\nhi\n", encoding="utf-8", newline="\n")
        code, _, err = run("--source", str(evil), "build", "--out", str(self.tmp / "out"))
        self.assertEqual(code, 2)
        self.assertIn("line 1", err)
        self.assertFalse((self.tmp / "x").exists())
        with self.assertRaises(policygen.PolicyError):
            policygen.write({"..": b"x"}, self.tmp / "out")
        with self.assertRaises(policygen.PolicyError):
            policygen.write({"a/b": b"x"}, self.tmp / "out")

    @needs_symlinks
    def test_build_refuses_a_symlinked_preset_folder_leading_outside(self):
        out, elsewhere = self.tmp / "out", self.tmp / "elsewhere"
        out.mkdir()
        elsewhere.mkdir()
        (out / "fast").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises(policygen.PolicyError):
            policygen.write(policygen.build_source(self.source), out)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_build_into_the_live_folder_needs_live_flag(self):
        with patch.object(policygen, "live_root", return_value=self.live):
            code, _, err = run("--source", str(self.source), "build", "--out", str(self.live))
            self.assertEqual(code, 2)
            self.assertIn("--live", err)
            code, _, err = run("--source", str(self.source), "build", "--out", str(self.live / "fast"))
            self.assertEqual(code, 2)
            before = (self.live / "fast" / policygen.FILE).read_bytes()
            self.assertEqual(run("--source", str(self.source), "build", "--out", str(self.live), "--live")[0], 0)
            self.assertEqual((self.live / "fast" / policygen.FILE).read_bytes(), before)

    def test_stats(self):
        code, out, _ = run("--source", str(self.source), "stats")
        self.assertEqual(code, 0)
        self.assertIn("shared lines: ", out)
        self.assertIn("fast: 6 lines", out)
        self.assertIn("normal: 7 lines", out)
        self.assertIn("fast vs normal: 2 lines differ", out)
        self.assertIn("fast vs ultra-fast: 1 lines differ", out)


if __name__ == "__main__":
    unittest.main()
