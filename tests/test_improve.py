import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from agentgraph import cases, improve, ledger


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def case_id(split: str, start: int = 0) -> str:
    """A case id (16 hex digits) that falls on the given side of the tune / held-out split."""
    n = start
    while True:
        candidate = f"{n:016x}"
        if cases.split_of(candidate) == split:
            return candidate
        n += 1


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = self.tmp / "config"
        self.home = self.tmp / "ledger"
        (self.root / "oh-my-opencode-slim" / "fast").mkdir(parents=True)
        self.rel = "oh-my-opencode-slim/fast/designer.md"
        self.file = self.root / self.rel
        self.original = "You are the designer.\nBe brief.\r\nKeep \u00e9 bytes.\n".encode("utf-8")
        self.file.write_bytes(self.original)
        self.newfile = self.tmp / "new.md"
        self.newtext = "You are the designer.\nBe briefer.\n"
        self.newfile.write_text(self.newtext)

    def propose(self, **kw):
        return improve.propose(self.home, self.root, "Shorter designer", "Designer talks too much",
                               [(self.rel, self.newtext)], **kw)

    def approve(self, record, **kw):
        return improve.approve(self.home, self.root, record["id"], who="user", note="yes, apply it", **kw)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = improve.main([argv[0], *argv[1:], "--config-root", str(self.root), "--home", str(self.home)])
        return code, out.getvalue(), err.getvalue()


class DigestTests(Base):
    def test_digest_is_short_newest_first_and_needs_no_file(self):
        self.assertEqual(improve.digest(self.home), [])
        first = self.propose(now=1000.0)
        second = improve.propose(self.home, self.root, "Second", "why", [(self.rel, "x\n")], now=2000.0)
        improve.reject(self.home, first["id"], "no")
        self.file.unlink()  # the digest reads the records alone
        digest = improve.digest(self.home)
        self.assertEqual([(item["id"], item["state"], item["verdict"]) for item in digest],
                         [(second["id"], "drafted", "not enough runs"), (first["id"], "rejected", "not enough runs")])
        self.assertEqual(set(digest[0]), {"id", "created", "title", "state", "verdict"})
        self.assertEqual(len(improve.digest(self.home, limit=1)), 1)

    def test_the_snapshots_learning_document_carries_it(self):
        from agentgraph import snapshot_json
        self.home = self.tmp / "memory"  # where the snapshot looks for the active ledger
        record = self.propose()
        (self.home / "feedback.jsonl").write_text(json.dumps({"time": 1.0, "text": "n", "agent": "a", "task": "t"}) + "\n")
        learning = snapshot_json.learning_document(self.tmp, config_path=self.tmp / "none.json")
        self.assertEqual([(item["id"], item["state"]) for item in learning["proposals"]], [(record["id"], "drafted")])


class CycleTests(Base):
    def test_propose_records_base_hash_and_state(self):
        record = self.propose(cases_=["abc123"], notes=["1795000000"])
        saved = json.loads((self.home / "proposals" / f"{record['id']}.json").read_text())
        self.assertEqual(saved["state"], "drafted")
        self.assertEqual(saved["changes"], [{"path": self.rel, "base_sha256": sha(self.original), "new_text": self.newtext}])
        self.assertEqual(saved["evidence"]["cases"], ["abc123"])
        self.assertEqual(saved["history"][0]["state"], "drafted")
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_show_prints_diff_against_the_file_now(self):
        record = self.propose()
        code, out, _ = self.cli("show", record["id"])
        self.assertEqual(code, 0)
        self.assertIn(f"--- a/{self.rel}", out)
        self.assertIn("-Be brief.", out)
        self.assertIn("+Be briefer.", out)
        code, out, _ = self.cli("show", record["id"], "--json")
        shown = json.loads(out)
        self.assertTrue(shown["files"][0]["base_matches"])
        self.assertIn("+Be briefer.", shown["files"][0]["diff"])

    def test_approve_applies_exact_bytes_after_backup_and_records_ledger_row(self):
        record = self.propose()
        done = self.approve(record)
        self.assertEqual(self.file.read_bytes(), self.newtext.encode())
        application = done["applications"][-1]
        self.assertEqual(done["state"], "applied")
        self.assertEqual(done["history"][-1]["who"], "user")
        self.assertEqual(done["history"][-1]["note"], "yes, apply it")
        backup = Path(application["backup"])
        self.assertEqual(backup.parent, self.root / "backups")
        self.assertIn(f"-improve-{record['id']}", backup.name)
        self.assertEqual((backup / self.rel).read_bytes(), self.original)
        self.assertEqual(application["files"], [{"path": self.rel, "before_sha256": sha(self.original),
                                                 "after_sha256": sha(self.newtext.encode())}])
        rows = [json.loads(line) for line in (self.home / "improvements.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], [self.rel])
        self.assertIn(record["id"], rows[0]["summary"])
        self.assertEqual(json.loads((self.home / "proposals" / f"{record['id']}.json").read_text())
                         ["applications"][-1]["ledger_time"], rows[0]["time"])
        self.assertEqual(ledger._read(self.home / "improvements.jsonl", ledger._valid_improvement)[0][1]["file"], [self.rel])

    def test_approve_refused_when_file_changed_since_drafting(self):
        record = self.propose()
        changed = self.original + b"extra\n"
        self.file.write_bytes(changed)
        with self.assertRaisesRegex(improve.ImproveError, "changed since"):
            self.approve(record)
        self.assertEqual(self.file.read_bytes(), changed)
        self.assertFalse((self.root / "backups").exists())
        self.assertFalse((self.home / "improvements.jsonl").exists())
        done = self.approve(record, force=True)
        self.assertEqual(done["state"], "applied")
        self.assertEqual(self.file.read_bytes(), self.newtext.encode())
        self.assertEqual((Path(done["applications"][-1]["backup"]) / self.rel).read_bytes(), changed)

    def test_approve_needs_the_users_words(self):
        record = self.propose()
        with self.assertRaises(improve.ImproveError):
            improve.approve(self.home, self.root, record["id"], who="user", note="  ")
        code, _, err = self.cli("approve", record["id"], "--by-user", "")
        self.assertEqual(code, 1)
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_reject_leaves_every_file_byte_identical(self):
        other = self.root / "AGENTS.md"
        other.write_bytes(b"mine\n")
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        record = self.propose()
        code, out, _ = self.cli("reject", record["id"], "--reason", "too blunt")
        self.assertEqual(code, 0)
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, before)
        saved = improve._load(self.home, record["id"])
        self.assertEqual(saved["state"], "rejected")
        self.assertEqual(saved["history"][-1]["note"], "too blunt")
        with self.assertRaises(improve.ImproveError):
            self.approve(record)
        self.assertFalse((self.home / "improvements.jsonl").exists())

    def test_reopen_undoes_a_rejection(self):
        record = self.propose()
        with self.assertRaisesRegex(improve.ImproveError, "only a rejected"):
            improve.reopen(self.home, record["id"])
        improve.reject(self.home, record["id"], "not now")
        with self.assertRaises(improve.ImproveError):
            self.approve(record)
        self.assertEqual(self.cli("reopen", record["id"])[0], 0)
        saved = improve._load(self.home, record["id"])
        self.assertEqual((saved["state"], saved["history"][-1]["who"], saved["history"][-1]["note"]),
                         ("drafted", "command", "reopened"))
        self.assertEqual(self.file.read_bytes(), self.original)
        # with a result recorded it comes back as evaluated
        improve.add_result(self.home, record["id"], "before", "case-a", "pass")
        improve.reject(self.home, record["id"], "again")
        done = improve.reopen(self.home, record["id"], note="changed my mind")
        self.assertEqual((done["state"], done["history"][-1]["who"]), ("evaluated", "user"))
        self.assertEqual(self.approve(record)["state"], "applied")

    def test_the_size_and_diff_of_the_change_are_its_own_in_every_state(self):
        record = self.propose()
        def sizes():
            shown = improve.show(self.home, self.root, record["id"])
            listed = improve.list_proposals(self.home, self.root)[0]
            self.assertEqual((listed["added"], listed["removed"]), (shown["added"], shown["removed"]))
            entry = shown["files"][0]
            self.assertEqual((entry["added"], entry["removed"]), (shown["added"], shown["removed"]))
            return (shown["added"], shown["removed"]), entry
        drafted, entry = sizes()
        self.assertEqual((drafted, entry["diff_against"]), ((1, 2), "file"))
        self.approve(record)
        applied, entry = sizes()  # the file now holds the new text, the change is still +1 -2
        self.assertEqual((applied, entry["diff_against"]), ((1, 2), "backup"))
        self.assertIn("-Be brief.", entry["diff"])
        self.assertIn("+Be briefer.", entry["diff"])
        self.assertTrue(entry["after_matches"])
        text = improve._show_text(improve.show(self.home, self.root, record["id"]))
        self.assertIn("+1 -2", text)
        self.assertNotIn("no difference from the file now", text)
        # edited by hand afterwards: still the proposal's own change, and the edit is still flagged
        self.file.write_bytes(self.newtext.encode() + b"hand edit\n")
        edited, entry = sizes()
        self.assertEqual((edited, entry["diff_against"], entry["after_matches"]), ((1, 2), "backup", False))
        self.file.write_bytes(self.newtext.encode())
        # the backup gone or changed: the diff falls back to the file now and says so
        backup = Path(improve._load(self.home, record["id"])["applications"][-1]["backup"]) / self.rel
        backup.write_bytes(b"not what was saved\n")
        fallback, entry = sizes()
        self.assertEqual((fallback, entry["diff_against"]), ((0, 0), "file"))
        backup.write_bytes(self.original)
        self.assertEqual(sizes()[0], (1, 2))
        improve.revert(self.home, self.root, record["id"])
        reverted, entry = sizes()
        self.assertEqual((reverted, entry["diff_against"]), ((1, 2), "file"))
        improve.reject(self.home, record["id"], "no")
        self.assertEqual(sizes()[0], (1, 2))

    def test_a_file_the_proposal_created_diffs_against_nothing_once_applied(self):
        new = improve.propose(self.home, self.root, "New file", "w", [("oh-my-opencode-slim/fast/extra.md", "one\ntwo\n")])
        improve.approve(self.home, self.root, new["id"], who="user", note="yes")
        shown = improve.show(self.home, self.root, new["id"])
        self.assertEqual((shown["added"], shown["removed"], shown["files"][0]["diff_against"]), (2, 0, "backup"))

    def test_revert_restores_exact_bytes(self):
        record = self.propose()
        self.approve(record)
        done = improve.revert(self.home, self.root, record["id"])
        self.assertEqual(done["state"], "reverted")
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(sha(self.file.read_bytes()), sha(self.original))
        with self.assertRaises(improve.ImproveError):
            improve.revert(self.home, self.root, record["id"])  # not applied now
        again = self.approve(record)  # a reverted proposal can be approved again
        self.assertEqual(again["state"], "applied")
        self.assertEqual(len(again["applications"]), 2)
        self.assertEqual(self.file.read_bytes(), self.newtext.encode())

    def test_revert_refused_when_edited_after_applying(self):
        record = self.propose()
        self.approve(record)
        edited = self.newtext.encode() + b"hand edit\n"
        self.file.write_bytes(edited)
        with self.assertRaisesRegex(improve.ImproveError, "changed after"):
            improve.revert(self.home, self.root, record["id"])
        self.assertEqual(self.file.read_bytes(), edited)
        self.assertEqual(improve._load(self.home, record["id"])["state"], "applied")
        improve.revert(self.home, self.root, record["id"], force=True)
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_new_file_is_created_and_deleted_on_revert(self):
        rel = "skills/new-skill/SKILL.md"
        record = improve.propose(self.home, self.root, "New skill", "needed", [(rel, "# new\n")])
        self.assertIsNone(record["changes"][0]["base_sha256"])
        self.approve(record)
        self.assertEqual((self.root / rel).read_text(), "# new\n")
        improve.revert(self.home, self.root, record["id"])
        self.assertFalse((self.root / rel).exists())
        self.assertFalse((self.root / "skills").exists())  # the folders it made are gone too

    def test_two_files_are_written_together_or_not_at_all(self):
        second = self.root / "AGENTS.md"
        second.write_bytes(b"old\n")
        record = improve.propose(self.home, self.root, "Two", "both",
                                 [(self.rel, self.newtext), ("AGENTS.md", "new\n")])
        second.write_bytes(b"drifted\n")
        with self.assertRaises(improve.ImproveError):
            self.approve(record)
        self.assertEqual(self.file.read_bytes(), self.original)  # the first change was not applied alone

    def test_propose_refuses_a_no_op(self):
        with self.assertRaisesRegex(improve.ImproveError, "same as the file"):
            improve.propose(self.home, self.root, "t", "w", [(self.rel, self.original.decode())])


class PathTests(Base):
    def test_unsafe_and_non_prompt_paths_are_refused(self):
        outside = self.tmp / "outside.md"
        outside.write_text("x")
        (self.root / "skills").mkdir()
        os.symlink(self.tmp, self.root / "skills" / "escape")
        os.symlink(outside, self.root / "agents-link.md")
        os.symlink(outside, self.root / "skills" / "link.md")
        bad = ["../outside.md", "skills/../../outside.md", "/etc/hosts.md", str(outside), "skills//x.md", "skills/./x.md",
               "oh-my-opencode-slim.json", "oh-my-opencode-slim/fast/settings.json", "opencode.json", "skills/x/run.sh",
               "notes.md", "commands/x.md", "agents-link.md", "skills/link.md", "skills/escape/outside.md",
               "skills\\x.md", "", "skills/.md", "skills/x.md\0"]
        for path in bad:
            with self.subTest(path=path):
                with self.assertRaises(improve.ImproveError):
                    improve.propose(self.home, self.root, "t", "w", [(path, "text\n")])
        self.assertEqual(outside.read_text(), "x")
        self.assertFalse((self.home / "proposals").exists())

    def test_a_json_target_through_the_command_is_refused(self):
        source = self.tmp / "x.json"
        source.write_text("{}")
        code, _, err = self.cli("propose", "--title", "t", "--why", "w", "--change", f"oh-my-opencode-slim.json={source}")
        self.assertEqual(code, 1)
        self.assertIn("not a prompt file", err)

    def test_allowed_paths(self):
        for path in ("AGENTS.md", "agents/mine.md", "skills/a/SKILL.md", "oh-my-opencode-slim/fast/x_append.md"):
            self.assertEqual(improve.safe_path(self.root, path), self.root / path)

    def test_a_symlink_swapped_in_after_drafting_is_refused_at_approval(self):
        record = self.propose()
        target = self.tmp / "elsewhere.md"
        target.write_bytes(self.original)
        self.file.unlink()
        os.symlink(target, self.file)
        with self.assertRaisesRegex(improve.ImproveError, "symlink"):
            self.approve(record)
        self.assertEqual(target.read_bytes(), self.original)


class FaultTests(Base):
    """Fault injection into approve and revert (a failing write or record save, a damaged record, a symlinked backups folder)."""

    def setUp(self):
        super().setUp()
        self.second = self.root / "AGENTS.md"
        self.second.write_bytes(b"old agents\n")
        self.fresh = "skills/new-skill/SKILL.md"

    def two(self):
        return improve.propose(self.home, self.root, "Two", "both", [(self.rel, self.newtext), ("AGENTS.md", "new agents\n")])

    def failing_write(self, fail, real=improve._write_atomic):
        """A `_write_atomic` that raises OSError for the Nth write to each of the named files (1-based, counting only those files)."""
        seen = {}

        def write(path, data):
            name = Path(path).name
            if name in fail and "backups" not in Path(path).parts:
                seen[name] = seen.get(name, 0) + 1
                if seen[name] in fail[name]:
                    raise OSError(f"disk full writing {name}")
            return real(path, data)
        return write

    def untouched(self):
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(self.second.read_bytes(), b"old agents\n")

    def test_a_text_that_cannot_be_encoded_is_refused_before_anything_is_written(self):
        record = self.two()
        path = improve._path(self.home, record["id"])
        path.write_text(path.read_text().replace("new agents\\n", "\\ud800").replace('"new agents\n"', '"\\ud800"'))
        self.assertIn("ud800", path.read_text())
        with self.assertRaises(improve.ImproveError):
            self.approve(record)
        self.untouched()
        self.assertFalse((self.root / "backups").exists())
        self.assertEqual(improve._load(self.home, record["id"])["state"], "drafted")

    def test_a_failure_that_is_not_an_oserror_rolls_back_too(self):
        record = self.two()
        calls = []

        def boom(path, data, real=improve._write_atomic):
            calls.append(path)
            if Path(path).name == "AGENTS.md" and "backups" not in Path(path).parts:
                raise ValueError("odd failure")
            return real(path, data)
        with unittest.mock.patch.object(improve, "_write_atomic", boom), self.assertRaises(ValueError):
            self.approve(record)
        self.untouched()

    def test_a_rollback_step_that_fails_is_named_and_the_others_still_run(self):
        record = self.two()
        # the 2nd file fails to write; putting the 1st (designer.md) back fails as well (its 2nd write)
        with unittest.mock.patch.object(improve, "_write_atomic", self.failing_write({"AGENTS.md": {1}, "designer.md": {2}})):
            with self.assertRaises(improve.ImproveError) as caught:
                self.approve(record)
        self.assertIn(self.rel, str(caught.exception))
        self.assertIn("could not be put back", str(caught.exception))
        self.assertEqual(self.second.read_bytes(), b"old agents\n")

    def test_folders_the_apply_made_are_removed_when_it_is_rolled_back(self):
        record = improve.propose(self.home, self.root, "Fresh", "w", [(self.fresh, "# new\n"), ("AGENTS.md", "new agents\n")])
        with unittest.mock.patch.object(improve, "_write_atomic", self.failing_write({"AGENTS.md": {1}})):
            with self.assertRaisesRegex(improve.ImproveError, "put back"):
                self.approve(record)
        self.assertFalse((self.root / "skills").exists())
        self.assertEqual(self.second.read_bytes(), b"old agents\n")

    def test_a_failed_record_save_puts_the_files_back_and_approve_can_be_retried(self):
        record = self.two()
        real = improve._save

        def save(home, rec):
            if rec["state"] == "applied":
                raise OSError("disk full saving the record")
            return real(home, rec)
        with unittest.mock.patch.object(improve, "_save", save), self.assertRaises(improve.ImproveError):
            self.approve(record)
        self.untouched()
        self.assertEqual(improve._load(self.home, record["id"])["state"], "drafted")
        self.assertEqual(self.approve(record)["state"], "applied")
        self.assertEqual(self.file.read_bytes(), self.newtext.encode())

    def test_a_revert_that_failed_halfway_can_be_retried(self):
        record = self.two()
        self.approve(record)
        # revert writes designer.md back first, then AGENTS.md fails
        with unittest.mock.patch.object(improve, "_write_atomic", self.failing_write({"AGENTS.md": {1}})):
            with self.assertRaises(OSError):
                improve.revert(self.home, self.root, record["id"])
        self.assertEqual(self.file.read_bytes(), self.original)
        self.assertEqual(improve._load(self.home, record["id"])["state"], "applied")
        done = improve.revert(self.home, self.root, record["id"])
        self.assertEqual(done["state"], "reverted")
        self.untouched()

    def applied_record_edit(self, edit):
        record = self.propose()
        self.approve(record)
        path = improve._path(self.home, record["id"])
        data = json.loads(path.read_text())
        edit(data["applications"][-1])
        path.write_text(json.dumps(data))
        return record

    def test_a_created_folder_outside_the_root_is_not_removed_on_revert(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        record = self.applied_record_edit(lambda app: app.update(created_dirs=["../outside"]))
        with self.assertRaises(improve.ImproveError):
            improve.revert(self.home, self.root, record["id"])
        self.assertTrue(outside.is_dir())
        self.assertEqual(self.file.read_bytes(), self.newtext.encode())  # nothing was restored either

    def test_a_backup_outside_the_backups_folder_is_not_read_on_revert(self):
        other = self.tmp / "other"
        (other / "oh-my-opencode-slim" / "fast").mkdir(parents=True)
        (other / self.rel).write_bytes(self.original)
        record = self.applied_record_edit(lambda app: app.update(backup=str(other)))
        with self.assertRaises(improve.ImproveError):
            improve.revert(self.home, self.root, record["id"])
        self.assertEqual(self.file.read_bytes(), self.newtext.encode())

    def test_a_symlinked_backups_folder_is_refused(self):
        record = self.propose()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        os.symlink(elsewhere, self.root / "backups")
        with self.assertRaisesRegex(improve.ImproveError, "symlink"):
            self.approve(record)
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertEqual(self.file.read_bytes(), self.original)


class EvaluationTests(Base):
    def rows(self, phase, split, outcomes):
        record = None
        for i, outcome in enumerate(outcomes):
            record = improve.add_result(self.home, self.record["id"], phase, case_id(split, 1000 * (i + 1)), outcome)
        return record

    def setUp(self):
        super().setUp()
        self.record = self.propose()

    def verdict(self):
        return improve.summary(improve._load(self.home, self.record["id"])["evaluation"])

    def test_not_enough_runs(self):
        self.assertEqual(self.verdict()["verdict"], "not enough runs")
        self.rows("before", "held-out", ["fail"])
        self.rows("after", "held-out", ["pass"])
        self.assertEqual(self.verdict()["verdict"], "not enough runs")  # one held-out result each side

    def test_better_same_worse(self):
        self.rows("before", "held-out", ["pass", "fail"])
        self.rows("before", "tune", ["fail", "fail"])
        self.rows("after", "held-out", ["pass", "pass"])
        self.rows("after", "tune", ["pass", "fail"])
        self.assertEqual(self.verdict()["verdict"], "better")
        self.assertEqual(self.verdict()["held_out"], {"before": [1, 2], "after": [2, 2]})

        same = improve.propose(self.home, self.root, "same", "w", [(self.rel, "other\n")])
        for phase in ("before", "after"):
            for i, outcome in enumerate(["pass", "fail"]):
                improve.add_result(self.home, same["id"], phase, case_id("held-out", 7000 + 100 * i), outcome)
        self.assertEqual(improve.summary(improve._load(self.home, same["id"])["evaluation"])["verdict"], "no difference")

        worse = improve.propose(self.home, self.root, "worse", "w", [(self.rel, "third\n")])
        for i, outcome in enumerate(["pass", "pass"]):
            improve.add_result(self.home, worse["id"], "before", case_id("held-out", 500 * (i + 1)), outcome)
        for i, outcome in enumerate(["pass", "fail"]):
            improve.add_result(self.home, worse["id"], "after", case_id("held-out", 500 * (i + 1)), outcome)
        self.assertEqual(improve.summary(improve._load(self.home, worse["id"])["evaluation"])["verdict"], "worse")

    def test_better_on_tune_only_when_held_out_did_not_move(self):
        # The real cycle that showed the old rule misleading: held-out 3/9 -> 3/9, tune 3/6 -> 5/6.
        self.rows("before", "held-out", ["pass"] * 3 + ["fail"] * 6)
        self.rows("after", "held-out", ["pass"] * 3 + ["fail"] * 6)
        self.rows("before", "tune", ["pass"] * 3 + ["fail"] * 3)
        self.rows("after", "tune", ["pass"] * 5 + ["fail"])
        verdict = self.verdict()
        self.assertEqual(verdict["verdict"], "better on tune only")
        self.assertEqual((verdict["held_out"], verdict["tune"]),
                         ({"before": [3, 9], "after": [3, 9]}, {"before": [3, 6], "after": [5, 6]}))
        self.assertIn("better on tune only", verdict["text"])

    def test_held_out_up_with_tune_down_is_worse_and_held_out_up_with_tune_level_is_better(self):
        self.rows("before", "held-out", ["fail", "fail"])
        self.rows("after", "held-out", ["pass", "fail"])
        self.rows("before", "tune", ["pass", "fail"])
        self.rows("after", "tune", ["pass", "fail"])
        self.assertEqual(self.verdict()["verdict"], "better")
        self.rows("after", "tune", ["fail"])  # tune now 1/3 after against 1/2 before
        self.assertEqual(self.verdict()["verdict"], "worse")

    def test_tune_drop_alone_is_worse_and_rates_not_counts_are_compared(self):
        self.rows("before", "held-out", ["pass", "fail"])
        self.rows("after", "held-out", ["pass", "fail", "pass", "fail"])  # same rate, more runs
        self.rows("before", "tune", ["pass"])
        self.rows("after", "tune", ["fail"])
        self.assertEqual(self.verdict()["verdict"], "worse")

    def test_state_moves_to_evaluated_and_the_split_is_the_cases_split(self):
        self.assertEqual(improve._load(self.home, self.record["id"])["state"], "drafted")
        record = improve.add_result(self.home, self.record["id"], "before", "p2-s3-solo", "pass", cost=0.2, turns=12,
                                    source="bench:p2-base:p2-s3-solo:r1")
        self.assertEqual(record["state"], "evaluated")
        self.assertEqual(record["evaluation"]["before"][0]["split"], cases.split_of("p2-s3-solo"))

    def test_same_source_for_different_cases_counts_each(self):
        for case in ("a1", "a2"):
            improve.add_result(self.home, self.record["id"], "before", case, "fail", source="by hand")
        improve.add_result(self.home, self.record["id"], "before", "a2", "pass", source="by hand")  # replaces a2
        rows = improve._load(self.home, self.record["id"])["evaluation"]["before"]
        self.assertEqual([(r["case"], r["outcome"]) for r in rows], [("a1", "fail"), ("a2", "pass")])

    def test_bad_results_are_refused(self):
        for kwargs in ({"case": "bad id", "outcome": "pass"}, {"case": "ok", "outcome": "maybe"},
                       {"case": "ok", "outcome": "pass", "cost": -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(improve.ImproveError):
                improve.add_result(self.home, self.record["id"], "before", **kwargs)

    def test_import_bench_reads_rows_and_is_idempotent(self):
        results = self.tmp / "results"
        results.mkdir()
        rows = [{"scenario": "s1", "label": "lab", "rep": 1, "check": {"passed": True}, "cost": 0.1, "turns": 4},
                {"scenario": "s1", "label": "lab", "rep": 2, "check": {"passed": False}, "cost": 0.3, "turns": 9},
                {"scenario": "probe", "label": "lab", "rep": 1}]
        (results / "lab.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        code, out, _ = self.cli("import-bench", self.record["id"], "--phase", "after", "--label", "lab", "--results", str(results))
        self.assertEqual(code, 0, out)
        self.cli("import-bench", self.record["id"], "--phase", "after", "--label", "lab", "--results", str(results))
        after = improve._load(self.home, self.record["id"])["evaluation"]["after"]
        self.assertEqual([(r["case"], r["outcome"], r["source"]) for r in after],
                         [("s1", "pass", "bench:lab:s1:r1"), ("s1", "fail", "bench:lab:s1:r2")])
        code, _, err = self.cli("import-bench", self.record["id"], "--phase", "after", "--label", "nope", "--results", str(results))
        self.assertEqual(code, 1)

    def test_approve_prints_and_records_the_summary_even_if_worse(self):
        self.rows("before", "held-out", ["pass", "pass"])
        self.rows("after", "held-out", ["fail", "fail"])
        code, out, _ = self.cli("approve", self.record["id"], "--by-user", "do it anyway")
        self.assertEqual(code, 0)
        self.assertIn("worse", out)
        saved = improve._load(self.home, self.record["id"])
        self.assertEqual(saved["applications"][-1]["evaluation"]["verdict"], "worse")
        self.assertEqual(saved["state"], "applied")


class CommandTests(Base):
    def test_full_cycle_through_the_command_line(self):
        code, out, _ = self.cli("propose", "--title", "Shorter", "--why", "too long", "--change", f"{self.rel}={self.newfile}",
                                "--case", "c1")
        self.assertEqual(code, 0, out)
        proposal_id = out.split()[1].rstrip(":")
        self.assertEqual(self.cli("result", proposal_id, "--phase", "before", "--case", "c1", "--outcome", "fail")[0], 0)
        code, out, _ = self.cli("list")
        self.assertIn(proposal_id, out)
        self.assertIn("evaluated", out)
        code, listed, _ = self.cli("list", "--json")
        item = json.loads(listed)["proposals"][0]
        self.assertEqual((item["state"], item["files"], item["added"]), ("evaluated", [self.rel], 1))
        self.assertEqual(self.cli("approve", proposal_id, "--by-user", "go")[0], 0)
        self.assertEqual(self.file.read_bytes(), self.newtext.encode())
        self.assertEqual(self.cli("revert", proposal_id)[0], 0)
        self.assertEqual(self.file.read_bytes(), self.original)
        code, _, err = self.cli("show", "nope")
        self.assertEqual(code, 1)
        self.assertIn("no proposal", err)

    def test_line_mode_answers_one_line_of_json_for_the_desktop_panel(self):
        code, out, _ = self.cli("propose", "--title", "Shorter", "--why", "too long", "--change", f"{self.rel}={self.newfile}")
        proposal_id = out.split()[1].rstrip(":")

        def line(*argv):
            code, out, err = self.cli(*argv, "--line")
            self.assertEqual(len(out.strip().splitlines()), 1, out)
            return code, json.loads(out), err

        code, listed, _ = line("list")
        self.assertEqual((code, [item["id"] for item in listed["proposals"]]), (0, [proposal_id]))
        code, shown, _ = line("show", proposal_id)
        self.assertEqual((code, shown["state"], shown["files"][0]["path"]), (0, "drafted", self.rel))
        code, rejected, _ = line("reject", proposal_id, "--reason", "not now")
        self.assertEqual((code, rejected["state"], rejected["history"][-1]["who"]), (0, "rejected", "user"))
        code, reopened, _ = line("reopen", proposal_id)
        self.assertEqual((code, reopened["state"]), (0, "drafted"))
        code, applied, _ = line("approve", proposal_id, "--by-user", "approved in the panel")
        self.assertEqual((code, applied["state"], applied["history"][-1]["who"]), (0, "applied", "user"))
        self.assertEqual(self.file.read_bytes(), self.newtext.encode())
        code, refused, err = line("approve", proposal_id, "--by-user", "again")
        self.assertEqual(code, 1)
        self.assertIn("applied", refused["error"])
        self.assertEqual(err, "")
        code, reverted, _ = line("revert", proposal_id)
        self.assertEqual((code, reverted["state"]), (0, "reverted"))
        self.assertEqual(self.file.read_bytes(), self.original)

    def test_ids_cannot_escape_the_proposals_folder(self):
        for bad in ("../x", "a/b", ".hidden"):
            with self.subTest(bad=bad), self.assertRaises(improve.ImproveError):
                improve.show(self.home, self.root, bad)


class LedgerRowTests(Base):
    """Every change has a ledger row: approve (with the recorded results), revert, and `note` for the rest."""

    def rows(self):
        return [json.loads(line) for line in (self.home / "improvements.jsonl").read_text().splitlines()]

    def test_approve_fills_the_metric_from_the_recorded_results(self):
        record = self.propose()
        improve.add_results(self.home, record["id"], "before", [
            improve._result_row("c1", "fail", 0.4, 10, None, weighted=1000.0),
            improve._result_row("c2", "pass", 0.2, 6, None, weighted=500.0)])
        improve.add_results(self.home, record["id"], "after", [improve._result_row("c1", "pass", 0.1, 4, None, weighted=250.0)])
        self.approve(record)
        row = self.rows()[0]
        self.assertEqual(row["kind"], "proposal")
        self.assertEqual(row["proposal"], record["id"])
        self.assertEqual(row["metric_before"], {"runs": 2, "passes": 1, "mean_cost": 0.3, "mean_turns": 8.0, "mean_weighted_tokens": 750.0})
        self.assertEqual(row["metric_after"]["passes"], 1)
        self.assertIn("before: 2 runs, 1 passed, mean cost $0.300, mean turns 8.0", row["metric"])
        self.assertIn("after: 1 runs, 1 passed", row["metric"])
        self.assertEqual(row["files_sha256"], {self.rel: sha(self.newtext.encode())})
        self.assertEqual((row["by"], row["by_words"]), ("user", "yes, apply it"))

    def test_approve_without_results_says_so(self):
        self.approve(self.propose())
        row = self.rows()[0]
        self.assertIsNone(row["metric"])
        self.assertIsNone(row["metric_before"])
        self.assertIn("no results recorded", row["summary"])

    def test_revert_writes_a_row_naming_the_proposal_who_and_why(self):
        record = self.propose()
        self.approve(record, now=100.0)
        improve.revert(self.home, self.root, record["id"], who="user", note="it made the designer worse", now=200.0)
        row = self.rows()[-1]
        self.assertEqual((row["kind"], row["proposal"], row["by"], row["why"]), ("revert", record["id"], "user", "it made the designer worse"))
        self.assertEqual(row["files_sha256"], {self.rel: sha(self.original)})
        self.assertTrue(row["keep_watermark"])
        self.assertEqual([item["kind"] for item in improve.changes(self.home)], ["proposal", "revert"])

    def test_note_records_hashes_never_contents_and_changes_only_the_ledger(self):
        before = self.file.read_bytes()
        code, out, err = self.cli("note", "--kind", "settings", "--title", "Raised the buffer", "--why", "long chats cost too much",
                                  "--files", self.rel, "missing.json", "--metric-before", "319", "--metric-after", "195",
                                  "--unit", "dollars", "--when", "2026-11-19 12:39", "--by-user", "yes do it")
        self.assertEqual(code, 0, err)
        row = self.rows()[0]
        self.assertEqual(row["files_sha256"], {self.rel: sha(before), "missing.json": None})
        self.assertEqual((row["metric_before"], row["metric_after"], row["unit"], row["kind"]), (319, 195, "dollars", "settings"))
        self.assertEqual((row["by"], row["by_words"]), ("user", "yes do it"))
        self.assertNotIn("Be brief", json.dumps(row))
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(sorted(path.name for path in self.home.iterdir() if path.is_file() and not path.name.startswith(".")), ["improvements.jsonl"])
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["oh-my-opencode-slim"])

    def test_note_needs_the_users_words_and_a_plain_path(self):
        base = ("note", "--kind", "other", "--title", "t", "--why", "w")
        self.assertEqual(self.cli(*base)[0], 1)
        self.assertEqual(self.cli(*base, "--by-user", "")[0], 1)
        self.assertEqual(self.cli(*base, "--by-user", "ok", "--files", "../escape")[0], 1)
        self.assertEqual(self.cli(*base, "--by-user", "ok", "--files", "/etc/hosts")[0], 1)
        with self.assertRaises(SystemExit):
            self.cli("note", "--kind", "nope", "--title", "t", "--why", "w", "--by-user", "ok")
        self.assertFalse((self.home / "improvements.jsonl").exists())

    def test_note_refuses_a_file_that_leaves_the_root_through_a_link(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("secret")
        (self.root / "link.txt").symlink_to(outside)
        code, _, err = self.cli("note", "--kind", "other", "--title", "t", "--why", "w", "--by-user", "ok", "--files", "link.txt")
        self.assertEqual(code, 1)
        self.assertIn("outside", err)
        self.assertFalse((self.home / "improvements.jsonl").exists())

    def test_changes_lists_in_time_order_with_metrics(self):
        record = self.propose()
        self.approve(record, now=2000.0)
        improve.note(self.home, self.root, "install", "Installed by hand", "owner asked", by_user="yes", files=[self.rel],
                     metric_before=100, metric_after=60, unit="tokens", when=1000.0)
        improve.note(self.home, self.root, "other", "Later thing", "why", by_user="yes", when=3000.0)
        found = improve.changes(self.home)
        self.assertEqual([item["time"] for item in found], [1000.0, 2000.0, 3000.0])
        self.assertEqual([item["kind"] for item in found], ["install", "proposal", "other"])
        code, out, _ = self.cli("changes")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("install", lines[0])
        self.assertIn("Installed by hand", lines[0])
        self.assertIn("before 100; after 60 (tokens)", lines[1])
        self.assertIn("no metric recorded", out)
        code, out, _ = self.cli("changes", "--json")
        self.assertEqual([item["title"] for item in json.loads(out)["changes"]][0], "Installed by hand")

    def test_a_note_does_not_consume_the_signals_waiting(self):
        ledger.feedback(self.home, 50.0, "the designer talks too much", agent="designer", task="t")
        pending = lambda now: ledger.signals(self.home, now, cost_db=self.tmp / "none.db", settings_log=self.tmp / "none.jsonl")["feedback"]
        before = pending(60.0)
        self.assertTrue(before)
        improve.note(self.home, self.root, "settings", "A setting", "why", by_user="yes", now=70.0)
        self.assertEqual(pending(80.0), before)

    def test_backfill_file_applies_once_and_refuses_a_duplicate(self):
        path = self.tmp / "backfill.jsonl"
        rows = [{"kind": "install", "title": "Proposal A", "why": "moved the policy", "files": [self.rel],
                 "sha256": {self.rel: "ab" * 32}, "metric_before": 234, "metric_after": 96, "unit": "k tokens", "when": "2026-11-21 12:02",
                 "source": "commit 1e6d700"},
                {"kind": "settings", "title": "Cache", "why": "long pauses", "metric_before": None, "metric_after": None, "when": 1795110955.16,
                 "source": "COSTS.md"}]
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        self.assertEqual(self.cli("note", "--from-file", str(path))[0], 1)  # no approval anywhere
        code, out, err = self.cli("note", "--from-file", str(path), "--by-user", "yes in chat")
        self.assertEqual(code, 0, err)
        found = self.rows()
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]["files_sha256"], {self.rel: "ab" * 32})  # the hash in the file, not the file as it is now
        self.assertEqual(found[0]["source"], "commit 1e6d700")
        self.assertIsNone(found[1]["metric_before"])
        code, _, err = self.cli("note", "--from-file", str(path), "--by-user", "yes in chat")
        self.assertEqual(code, 1)
        self.assertIn("already recorded", err)
        self.assertEqual(len(self.rows()), 2)

    def test_a_backfill_row_without_a_time_is_refused(self):
        path = self.tmp / "notime.jsonl"
        path.write_text(json.dumps({"kind": "other", "title": "ok", "why": "w"}) + "\n")
        code, _, err = self.cli("note", "--from-file", str(path), "--by-user", "yes")
        self.assertEqual(code, 1)
        self.assertIn("needs `when`", err)

    def test_a_bad_row_in_a_backfill_file_records_nothing(self):
        path = self.tmp / "bad.jsonl"
        path.write_text(json.dumps({"kind": "other", "title": "ok", "why": "w", "when": 5}) + "\n" + json.dumps({"kind": "bogus", "title": "t", "why": "w"}) + "\n")
        code, _, err = self.cli("note", "--from-file", str(path), "--by-user", "yes")
        self.assertEqual(code, 1)
        self.assertIn("line 2", err)
        self.assertFalse((self.home / "improvements.jsonl").exists())


class CostCriterionTests(Base):
    def side(self, costs, outcome="pass", key="weighted"):
        return [improve._result_row(f"c{n % 2}", outcome, c if key == "cost" else None, 5, None, weighted=c if key == "weighted" else None)
                for n, c in enumerate(costs)]

    def evaluation(self, before, after):
        return {"before": before, "after": after}

    def test_weighted_tokens_count_cache_reads_cheap_and_writes_dear(self):
        from agentgraph import bench
        row = {"tokens": {"input": 100, "cache_read": 1000, "cache_write": 80, "output": 10, "reasoning": 2}}
        self.assertEqual(bench.weighted_tokens(row), 100 + 0.1 * 1000 + 1.25 * 80 + 5 * 12)
        self.assertIsNone(bench.weighted_tokens({"cost": 1.0}))
        self.assertIsNone(bench.weighted_tokens({"tokens": {"input": 1}}))

    def test_import_bench_records_weighted_tokens(self):
        record = self.propose()
        results = self.tmp / "results"
        results.mkdir()
        row = {"scenario": "s1", "rep": 1, "check": {"passed": True}, "cost": 0.1, "turns": 4,
               "tokens": {"input": 10, "cache_read": 100, "cache_write": 8, "output": 2, "reasoning": 0}}
        (results / "lab.jsonl").write_text(json.dumps(row) + "\n" + json.dumps({**row, "rep": 2, "tokens": None}) + "\n")
        improve.import_bench(self.home, record["id"], "after", "lab", results)
        after = improve._load(self.home, record["id"])["evaluation"]["after"]
        self.assertEqual(after[0]["weighted"], 10 + 10 + 10 + 10)
        self.assertNotIn("weighted", after[1])

    def test_equal_passes_are_ranked_by_cost_with_the_spread(self):
        cheaper = improve.cost_comparison(self.evaluation(self.side([100, 102, 98, 101]), self.side([60, 62, 58, 61])))
        self.assertEqual((cheaper["verdict"], cheaper["unit"]), ("cheaper", "weighted tokens"))
        dearer = improve.cost_comparison(self.evaluation(self.side([60, 62, 58, 61]), self.side([100, 102, 98, 101])))
        self.assertEqual(dearer["verdict"], "dearer")
        # the gap is inside the spread across runs
        noisy = improve.cost_comparison(self.evaluation(self.side([10, 200, 30, 180]), self.side([20, 190, 40, 150])))
        self.assertEqual(noisy["verdict"], "no clear difference")
        # a clear but tiny gap (under 5%) is not a difference either
        tiny = improve.cost_comparison(self.evaluation(self.side([100.0, 100.1, 99.9, 100.0]), self.side([98.0, 98.1, 97.9, 98.0])))
        self.assertEqual(tiny["verdict"], "no clear difference")

    def test_fewer_than_three_runs_a_side_is_too_few_to_tell(self):
        for before, after in (([100, 100], [1, 1, 1, 1]), ([100, 100, 100], [1, 1]), ([], [1, 1, 1])):
            with self.subTest(before=before, after=after):
                found = improve.cost_comparison(self.evaluation(self.side(before), self.side(after)))
                self.assertEqual(found["verdict"], "too few runs to tell")
                self.assertIn("too few runs to tell", found["text"])

    def test_dollars_are_used_when_no_run_has_tokens(self):
        found = improve.cost_comparison(self.evaluation(self.side([1.0, 1.1, 0.9], key="cost"), self.side([0.5, 0.55, 0.45], key="cost")))
        self.assertEqual((found["verdict"], found["unit"]), ("cheaper", "dollars"))

    def test_passes_decide_first_and_cost_only_breaks_a_tie(self):
        better = self.evaluation(self.side([100, 101, 99], "fail"), self.side([500, 501, 499], "pass"))
        ranked = improve.summary(better)["ranking"]
        self.assertEqual((ranked["by"], ranked["result"]), ("passes", "more passes"))
        tie = improve.summary(self.evaluation(self.side([100, 101, 99]), self.side([50, 51, 49])))["ranking"]
        self.assertEqual((tie["by"], tie["result"]), ("cost", "cheaper"))
        self.assertIn("same passes", tie["text"])

    def test_show_states_the_rule_and_min_held_out_still_applies(self):
        record = self.propose()
        for n, value in enumerate((100, 101, 99)):
            improve.add_results(self.home, record["id"], "before", [improve._result_row(f"c{n}", "pass", None, 3, f"s{n}", weighted=value)])
            improve.add_results(self.home, record["id"], "after", [improve._result_row(f"c{n}", "pass", None, 3, f"s{n}", weighted=value / 2)])
        code, out, _ = self.cli("show", record["id"])
        self.assertIn("ranking: same passes", out)
        self.assertIn("cheaper", out)
        self.assertIn("cost rule:", out)
        self.assertEqual(improve.summary(improve._load(self.home, record["id"])["evaluation"])["verdict"], "not enough runs")


if __name__ == "__main__":
    unittest.main()
