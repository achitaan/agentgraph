"""Skill file operations use disposable fixtures, never the installed catalogue."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agentgraph import skillfiles as sf
from agentgraph import skillset

SCRATCH = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode/skillfiles")
ROOT = Path(__file__).resolve().parent.parent


class SkillfilesTest(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.skills = self.root / "skills"
        self.skills.mkdir()
        self.backups = self.root / "backups"
        self.config = self.root / "preset.json"
        self.config.write_text(json.dumps({"preset": "one", "presets": {
            "one": {"worker": {"model": "test/model", "skills": []}},
            "two": {"other": {"model": "test/model", "skills": []}}}}), encoding="utf-8")
        self.options = {"skills": self.skills}
        self.edit_options = self.options | {"backups": self.backups}
        self.delete_options = self.edit_options | {"config": self.config}
        self.folder = self.skills / "example"
        self.folder.mkdir()
        self.path = self.folder / "SKILL.md"
        self.original = b"---\n# a comment\nname: 'Example'\ndescription: Original description\nlicense: MIT\n---\n\n# Example\nBody.\n"
        self.path.write_bytes(self.original)
        (self.folder / "references").mkdir()
        (self.folder / "references" / "binary.dat").write_bytes(b"\x00\xff")
        (self.folder / "empty").mkdir()

    def tree(self):
        return {p.relative_to(self.skills).as_posix(): p.read_bytes() if p.is_file() else None
                for p in self.skills.rglob("*")}

    def cli(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), patch("sys.stdin", io.StringIO(stdin)):
            code = sf.main([*args, "--skills", str(self.skills), "--backups", str(self.backups),
                            "--config", str(self.config)])
        return code, out.getvalue(), err.getvalue()

    def test_read_fields_body_and_other_files(self):
        before = self.tree()
        result = sf.read("example", **self.options)
        self.assertEqual(result["frontmatter"], {"name": "Example", "description": "Original description",
                                                "license": "license: MIT\n"})
        self.assertEqual(result["body"], "\n# Example\nBody.\n")
        self.assertEqual(result["files"], ["references/binary.dat"])
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.backups.exists())

    def test_create_delete_round_trip(self):
        before = self.tree()
        sf.create("new-skill", "雪: # \"quote\"", "Unicode 雪\n", **self.options)
        self.assertEqual(sf.read("new-skill", **self.options)["frontmatter"]["name"], "new-skill")
        result = sf.delete("new-skill", **self.delete_options)
        self.assertTrue(Path(result["record"]).is_file())
        self.assertEqual(self.tree(), before)

    def test_delete_restore_round_trip_with_modes(self):
        before = self.tree()
        self.path.chmod(0o640)
        self.folder.chmod(0o750)
        old = self.path.stat()
        record = sf.delete("example", **self.delete_options)["record"]
        self.assertFalse(self.folder.exists())
        sf.restore(record, **self.edit_options)
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.path.stat().st_ino, old.st_ino)
        self.assertEqual(self.path.stat().st_mtime_ns, old.st_mtime_ns)
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o750)

    def test_body_write_back_is_byte_identical(self):
        before = self.tree()
        old = sf.read("example", **self.options)["body"]
        first = sf.write("example", body="new\nbody\t雪\n", **self.edit_options)
        self.assertEqual(Path(first["backup"]).read_bytes(), self.original)
        sf.write("example", body=old, **self.edit_options)
        self.assertEqual(self.tree(), before)

    def test_description_write_back_and_other_lines_preserved(self):
        sf.write("example", description="Another description", **self.edit_options)
        self.assertEqual(self.path.read_bytes(), self.original.replace(b"Original description", b"Another description"))
        sf.write("example", description="Original description", **self.edit_options)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_noop_makes_no_backup_or_mtime_change(self):
        old = self.path.stat()
        for kw in ({}, {"body": "\n# Example\nBody.\n"}, {"description": "Original description"}):
            self.assertFalse(sf.write("example", **kw, **self.edit_options)["changed"])
        self.assertEqual(self.path.stat().st_ino, old.st_ino)
        self.assertEqual(self.path.stat().st_mtime_ns, old.st_mtime_ns)
        self.assertFalse(self.backups.exists())

    def test_crlf_and_mode_preserved_and_each_previous_version_backed_up(self):
        self.path.write_bytes(self.original.replace(b"\n", b"\r\n"))
        self.path.chmod(0o600)
        original = self.path.read_bytes()
        one = sf.write("example", body="new\nline\n", description="changed", **self.edit_options)
        middle = self.path.read_bytes()
        self.assertNotIn(b"\n", middle.replace(b"\r\n", b""))
        self.assertEqual(Path(one["backup"]).read_bytes(), original)
        two = sf.write("example", body="third\n", **self.edit_options)
        self.assertNotEqual(one["backup"], two["backup"])
        self.assertEqual(Path(two["backup"]).read_bytes(), middle)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertRegex(one["backup"], r"\d{4}-\d{2}-\d{2}-skill-files/")

    def test_quoted_scalars(self):
        for raw, expected in (("'It''s text # yes'", "It's text # yes"),
                              ('"Snow \\u96ea: # yes"', "Snow 雪: # yes"),
                              ("Plain text # comment", "Plain text")):
            with self.subTest(raw=raw):
                self.path.write_text(f"---\nname: example\ndescription: {raw}\nextra: 'keep ''this'''\n---\nbody", encoding="utf-8")
                self.assertEqual(sf.read("example", **self.options)["frontmatter"]["description"], expected)
                sf.write("example", description="An editor's \"text\"", **self.edit_options)
                self.assertEqual(sf.read("example", **self.options)["frontmatter"]["description"], "An editor's \"text\"")
                self.assertIn(b"extra: 'keep ''this'''\n", self.path.read_bytes())

    def test_folded_and_literal_description(self):
        for indicator in (">", "|", ">-", "|-", ">+", "|+"):
            with self.subTest(indicator=indicator):
                raw = f"---\nname: example\ndescription: {indicator}\n  first line\n  second line\nlicense: MIT\n---\nbody\n"
                self.path.write_text(raw, encoding="utf-8")
                self.assertEqual(sf.read("example", **self.options)["frontmatter"]["description"], "first line second line")
                sf.write("example", body="new", **self.edit_options)
                self.assertEqual(self.path.read_text().split("---")[1], raw.split("---")[1])
                sf.write("example", description="simple now", **self.edit_options)
                self.assertEqual(sf.read("example", **self.options)["frontmatter"]["description"], "simple now")
                self.assertIn(b"license: MIT\n", self.path.read_bytes())
                sf.write("example", body="body\n", description="first line second line", **self.edit_options)
                self.assertEqual(self.path.read_bytes(), raw.encode())

    def test_opaque_nested_and_list_values_with_comments_crlf_and_no_final_newline(self):
        nested = (
            b"metadata: &complex\r\n"
            b"  # nested comment\r\n"
            b"  options: {any: [values, *reference]}\r\n"
            b"\r\n"
            b"  items:\r\n"
            b"    - {a: b}\r\n"
        )
        items = b"tags:\r\n- first\r\n- second\r\n# comment after list\r\n\r\n"
        heading = b"---\r\n# header comment\r\nname: example\r\n" + nested + items
        desc = b'description: >-  # keep this\r\n  first line\r\n  second line\r\n'
        tail = b"\r\n# standalone comment\r\n---\r\n"
        original = heading + desc + tail + b"original without final newline"
        self.path.write_bytes(original)
        result = sf.read("example", **self.options)
        self.assertEqual(result["frontmatter"]["metadata"], nested.decode())
        self.assertEqual(result["frontmatter"]["tags"], items.decode())
        self.assertEqual(result["frontmatter"]["description"], "first line second line")
        sf.write("example", description="renamed", **self.edit_options)
        self.assertEqual(self.path.read_bytes(),
                         heading + b'description: "renamed"  # keep this\r\n' + tail + result["body"].encode())
        sf.write("example", description=result["frontmatter"]["description"], **self.edit_options)
        self.assertEqual(self.path.read_bytes(), original)
        sf.write("example", body="new\nbody", **self.edit_options)
        self.assertEqual(self.path.read_bytes(), heading + desc + tail + b"new\r\nbody")

    def test_header_only_without_final_newline(self):
        original = b"---\nname: example\ndescription: old\n---"
        self.path.write_bytes(original)
        sf.write("example", description="new", **self.edit_options)
        self.assertEqual(self.path.read_bytes(), original.replace(b"old", b"new"))
        sf.write("example", body="body", **self.edit_options)
        self.assertTrue(self.path.read_bytes().endswith(b"---\nbody"))

    def test_description_whitespace_is_not_interpreted_as_yaml_padding(self):
        sf.write("example", description=" \ttext \t", **self.edit_options)
        self.assertEqual(sf.read("example", **self.options)["frontmatter"]["description"], " \ttext \t")

    def test_refuse_bad_ids_everywhere(self):
        for id in ("../escape", "/absolute", "x/y", "", "-a", "A", "a_b", "a" * 65, "a\n"):
            for function, args, kw in ((sf.read, (), self.options), (sf.create, ("d", "b"), self.options),
                                       (sf.write, (), self.edit_options), (sf.delete, (), self.delete_options)):
                with self.subTest(id=id, function=function.__name__):
                    with self.assertRaisesRegex(skillset.SkillsetError, "invalid skill id"):
                        function(id, *args, **kw)
        self.assertFalse(self.backups.exists())

    def test_limits_and_invalid_text(self):
        for description in ("x" * 1025, "two\nlines", "two\rlines", "bad\x00", "bad\x85", "\ud800"):
            for function, args in ((sf.create, ("new",)), (sf.write, ("example",))):
                with self.subTest(description=repr(description), function=function.__name__):
                    with self.assertRaises(skillset.SkillsetError):
                        function(*args, description=description, body="", **self.options)
        for body in ("x" * 200001, "bad\x00", "\udfff"):
            with self.assertRaises(skillset.SkillsetError):
                sf.write("example", body=body, **self.edit_options)
        sf.create("a" * 64, "x" * 1024, "x" * 200000, **self.options)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.backups.exists())

    def test_existing_id_including_dangling_symlink_refused(self):
        with self.assertRaisesRegex(skillset.SkillsetError, "already exists"):
            sf.create("example", "d", "b", **self.options)
        (self.skills / "dangling").symlink_to(self.root / "missing")
        with self.assertRaisesRegex(skillset.SkillsetError, "already exists"):
            sf.create("dangling", "d", "b", **self.options)

    def test_refuse_invalid_frontmatter_without_rewriting(self):
        heads = ("name: x\n", "name: x\ndescription: [x]\n", "name: x\ndescription: &a text\n",
                 "name: x\ndescription:\n  a: b\n", "name: *ref\ndescription: text\n",
                 "name: {x: y}\ndescription: text\n", "name:\n  value: x\ndescription: text\n",
                 "name: x\ndescription: one\ndescription: two\n",
                 "name: x\ndescription: \"broken\n", "name: x\ndescription: bad: value\n",
                 "name: x\ndescription: true\n", "name: x\ndescription: >\n  one\n second\n")
        bad = [b"\xff", b"no header", b"---\nname: x\n"]
        bad += [("---\n" + head + "---\nbody").encode() for head in heads]
        for raw in bad:
            self.path.write_bytes(raw)
            for function, kw in ((sf.read, self.options), (sf.write, self.edit_options),
                                 (sf.delete, self.delete_options)):
                with self.subTest(raw=raw, function=function.__name__):
                    with self.assertRaises(skillset.SkillsetError):
                        function("example", **kw)
                    self.assertEqual(self.path.read_bytes(), raw)
        self.assertFalse(self.backups.exists())

    def test_refuse_symlinked_skill_folder_file_and_ancillary_file(self):
        target = self.root / "outside"
        target.mkdir()
        (target / "SKILL.md").write_bytes(self.original)
        link = self.skills / "linked"
        link.symlink_to(target, target_is_directory=True)
        for function, kw in ((sf.read, self.options), (sf.write, self.edit_options),
                             (sf.delete, self.delete_options)):
            with self.assertRaisesRegex(skillset.SkillsetError, "symlink"):
                function("linked", **kw)
        self.path.unlink()
        self.path.symlink_to(target / "SKILL.md")
        with self.assertRaisesRegex(skillset.SkillsetError, "symlink"):
            sf.read("example", **self.options)
        self.path.unlink()
        self.path.write_bytes(self.original)
        (self.folder / "references" / "link").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(skillset.SkillsetError, "symlink"):
            sf.delete("example", **self.delete_options)
        self.assertEqual((target / "SKILL.md").read_bytes(), self.original)

    def test_refuse_symlinked_root_and_backup_component(self):
        link = self.root / "link"
        link.symlink_to(self.skills, target_is_directory=True)
        with self.assertRaisesRegex(skillset.SkillsetError, "symlink"):
            sf.read("example", skills=link)
        link.unlink()
        outside = self.root / "outside"
        outside.mkdir()
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(skillset.SkillsetError, "symlink"):
            sf.write("example", body="new", skills=self.skills, backups=link / "nested")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_fifo_refused_without_blocking(self):
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaisesRegex(skillset.SkillsetError, "regular"):
            sf.read("example", **self.options)

    def test_backups_inside_skills_refused(self):
        with self.assertRaisesRegex(skillset.SkillsetError, "outside"):
            sf.write("example", body="new", skills=self.skills, backups=self.skills / "backups")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_references_all_presets_wildcards_and_force_warning(self):
        obj = json.loads(self.config.read_bytes())
        obj["presets"]["one"]["worker"]["skills"] = ["*"]
        obj["presets"]["two"]["other"]["skills"] = ["example"]
        self.config.write_text(json.dumps(obj), encoding="utf-8")
        original_config = self.config.read_bytes()
        with self.assertRaisesRegex(skillset.SkillsetError, "other, worker"):
            sf.delete("example", **self.delete_options)
        self.assertFalse(self.backups.exists())
        result = sf.delete("example", force=True, **self.delete_options)
        self.assertIn("other, worker", result["warnings"][0])
        self.assertEqual(self.config.read_bytes(), original_config)
        sf.restore(result["record"], **self.edit_options)

    def test_unreadable_or_symlinked_config_refuses_even_force(self):
        for raw in (b"\xff", b"{}", b'{"preset":"x","presets":{},"presets":{}}'):
            self.config.write_bytes(raw)
            with self.assertRaises(skillset.SkillsetError):
                sf.delete("example", force=True, **self.delete_options)
        self.config.unlink()
        self.config.symlink_to(self.path)
        with self.assertRaises(skillset.SkillsetError):
            sf.delete("example", force=True, **self.delete_options)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(self.backups.exists())

    def test_restore_refuses_collision_and_second_restore(self):
        record = sf.delete("example", **self.delete_options)["record"]
        sf.create("example", "replacement", "new", **self.options)
        with self.assertRaisesRegex(skillset.SkillsetError, "already exists"):
            sf.restore(record, **self.edit_options)
        sf.delete("example", **self.delete_options)
        sf.restore(record, **self.edit_options)
        self.assertEqual(self.path.read_bytes(), self.original)
        with self.assertRaises(skillset.SkillsetError):
            sf.restore(record, **self.edit_options)

    def test_restore_refuses_forged_or_foreign_records_and_symlinks(self):
        record = Path(sf.delete("example", **self.delete_options)["record"])
        original = record.read_bytes()
        for value in ({"version": 1, "id": "../escape", "skills": str(self.skills)},
                      {"version": 1, "id": "example", "skills": str(self.root)},
                      [], {"id": "example"}):
            record.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(skillset.SkillsetError):
                sf.restore(record, **self.edit_options)
        record.write_bytes(original)
        target = self.root / "outside-record.json"
        target.write_bytes(original)
        record.unlink()
        record.symlink_to(target)
        with self.assertRaises(skillset.SkillsetError):
            sf.restore(str(record), **self.edit_options)
        with self.assertRaisesRegex(skillset.SkillsetError, "inside"):
            sf.restore(str(target), **self.edit_options)

    def test_atomic_write_failure_keeps_original_and_backup(self):
        replace = os.replace

        def fail_commit(src, dst):
            if dst == self.path:
                raise OSError("simulated write failure")
            return replace(src, dst)

        with patch.object(sf.os, "replace", side_effect=fail_commit):
            with self.assertRaisesRegex(skillset.SkillsetError, "simulated write failure"):
                sf.write("example", body="new", **self.edit_options)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(list(self.folder.glob(".*.tmp")), [])
        self.assertEqual(next(self.backups.rglob("SKILL.md")).read_bytes(), self.original)

    def test_a_file_changed_by_another_process_is_not_overwritten(self):
        other = self.original.replace(b"Original description", b"Someone else's description")
        real = sf._backup_folder

        def second_process(*args, **kwargs):
            self.path.write_bytes(other)  # between this write's read and its replace
            return real(*args, **kwargs)

        with patch.object(sf, "_backup_folder", side_effect=second_process):
            with self.assertRaisesRegex(skillset.SkillsetError, "changed while this edit was being made"):
                sf.write("example", body="new body\n", **self.edit_options)
        self.assertEqual(self.path.read_bytes(), other)
        self.assertEqual(list(self.folder.glob(".*.tmp")), [])

    def test_a_file_changed_just_before_the_replace_is_not_overwritten(self):
        other = self.original + b"Appended elsewhere.\n"
        real = skillset._staged
        calls = []

        def staged(path, *args):
            calls.append(path.name)
            if path == self.path:
                path.write_bytes(other)
            return real(path, *args)

        with patch.object(skillset, "_staged", side_effect=staged):
            with self.assertRaisesRegex(skillset.SkillsetError, "changed while this edit was being made"):
                sf.write("example", body="new body\n", **self.edit_options)
        self.assertEqual(self.path.read_bytes(), other)

    def test_write_backups_stay_bounded_on_a_busy_day(self):
        with patch.object(skillset, "MAX_BACKUPS", 3):
            for number in range(8):
                sf.write("example", body=f"body {number}\n", **self.edit_options)
        kept = list(self.backups.rglob("SKILL.md"))
        self.assertEqual(len(kept), 3)
        self.assertIn(b"body 6", b"".join(path.read_bytes() for path in kept))
        self.assertEqual(sf.read("example", **self.options)["body"], "body 7\n")

    def test_failed_create_and_delete_leave_tree_unchanged(self):
        original = self.tree()
        with patch.object(sf.os, "rename", side_effect=OSError("simulated rename failure")):
            with self.assertRaisesRegex(skillset.SkillsetError, "simulated"):
                sf.create("new", "description", "body", **self.options)
            with self.assertRaisesRegex(skillset.SkillsetError, "simulated"):
                sf.delete("example", **self.delete_options)
        self.assertEqual(self.tree(), original)

    def test_cli_json_file_stdin_delete_restore_and_errors(self):
        code, out, err = self.cli("show", "example", "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["id"], "example")
        code, out, err = self.cli("new", "new", "--description", "d", "--text-file", "-", "--json", stdin="stdin body")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["body"], "stdin body")
        text_file = self.root / "text.md"
        text_file.write_text("file body", encoding="utf-8")
        self.assertEqual(self.cli("write", "new", "--text-file", str(text_file))[0], 0)
        code, out, err = self.cli("delete", "new", "--json")
        self.assertEqual((code, err), (0, ""))
        record = json.loads(out)["record"]
        self.assertEqual(self.cli("restore", record)[0], 0)
        code, out, err = self.cli("show", "../no", "--json")
        self.assertEqual((code, err), (1, ""))
        self.assertIn("invalid skill id", json.loads(out)["error"])
        self.assertEqual(self.cli("show", "missing")[0], 1)
        code, _, _ = self.cli("write", "new", "--text-file", "-", stdin="x" * 200001)
        self.assertEqual(code, 1)

    def test_module_command_and_global_options(self):
        env = os.environ | {"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run([sys.executable, "-m", "agentgraph.skillfiles",
                                 "--skills", str(self.skills), "--json", "show", "example"],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["id"], "example")

    def test_concurrent_body_and_description_updates_both_land(self):
        env = os.environ | {"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
        command = [sys.executable, "-m", "agentgraph.skillfiles", "--skills", str(self.skills),
                   "--backups", str(self.backups), "--json", "write", "example"]
        text = self.root / "body.md"
        text.write_text("concurrent body", encoding="utf-8")
        one = subprocess.Popen([*command, "--description", "concurrent description"], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        two = subprocess.Popen([*command, "--text-file", str(text)], cwd=ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for process in (one, two):
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, (out, err))
        result = sf.read("example", **self.options)
        self.assertEqual(result["body"], "concurrent body")
        self.assertEqual(result["frontmatter"]["description"], "concurrent description")


if __name__ == "__main__":
    unittest.main()
