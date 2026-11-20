"""n99 r5: the task profiles are the toolkit's own copy (`task-profiles.md` beside SKILLS.md), and nothing in the
toolkit reads from inside an installed skill's folder, which the user may remove. Scratch copies only: the real
skills folder is read, never written."""
import importlib
import json
import os
from pathlib import Path
import pkgutil
import shutil
import subprocess
import sys
import tempfile
import unittest

import agentgraph
from agentgraph import skillfiles, skillset, sources

ROOT = Path(__file__).resolve().parent.parent


def _inside(path, folder):
    path, folder = Path(os.path.abspath(path)), Path(os.path.abspath(folder))
    return path != folder and folder in path.parents


class ToolkitCopy(unittest.TestCase):
    def test_profiles_is_the_toolkits_own_copy_beside_skills_md(self):
        self.assertEqual(skillset.PROFILES, ROOT / "task-profiles.md")
        self.assertEqual(skillset.PROFILES.parent, skillset.MAP.parent)
        self.assertTrue(skillset.PROFILES.is_file())
        self.assertTrue(skillset.PROFILES.read_text(encoding="utf-8").startswith("# Task profiles\n"))

    def test_no_module_path_points_inside_a_skill_folder(self):
        """Every path a toolkit module holds at its top level (PROFILES among them) is outside every skill's
        folder, installed (the real skills folder) or the repository's copy (`skills/`). The skills folder itself
        is the catalogue the editors work on, and may be named."""
        folders = (skillfiles.SKILLS, sources.CONFIG_DIR / "skills", ROOT / "skills")
        found = []
        for module in pkgutil.iter_modules(agentgraph.__path__):
            if module.name == "__main__":
                continue
            loaded = importlib.import_module(f"agentgraph.{module.name}")
            for name, value in vars(loaded).items():
                if isinstance(value, Path) and any(_inside(value, folder) for folder in folders):
                    found.append(f"agentgraph.{module.name}.{name} = {value}")
        self.assertEqual(found, [], "a toolkit path inside a skill folder")


class WithoutTheSkill(unittest.TestCase):
    """The profile warnings, read and written with the defaults, under a home whose skills folder has no
    `personal-orchestrator` at all (the skill the profiles used to be read from)."""

    def test_profile_warnings_need_no_personal_orchestrator_skill(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            config_dir = home / ".config" / "opencode"
            skills = config_dir / "skills"
            real = sources.CONFIG_DIR / "skills"
            for entry in sorted(real.iterdir()):
                if entry.name != "personal-orchestrator" and (entry / "SKILL.md").is_file():
                    (skills / entry.name).mkdir(parents=True)
                    shutil.copyfile(entry / "SKILL.md", skills / entry.name / "SKILL.md")
            self.assertTrue((skills / "code-assurance" / "SKILL.md").is_file())
            self.assertFalse((skills / "personal-orchestrator").exists())
            shutil.copyfile(skillset.CONFIG, config_dir / skillset.CONFIG.name)
            work = Path(directory) / "work"
            work.mkdir()
            shutil.copyfile(skillset.MAP, work / "SKILLS.md")
            script = (
                "import json, sys\n"
                "from pathlib import Path\n"
                "from agentgraph import skillset\n"
                "work = Path(sys.argv[1])\n"
                "result = skillset.remove('impl-openai', 'code-assurance', map_path=work / 'SKILLS.md',\n"
                "                         backups=work / 'backups')\n"
                "print(json.dumps({'warnings': result['warnings'], 'after': skillset.profile_mismatches()}))\n")
            env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(ROOT)}
            done = subprocess.run([sys.executable, "-c", script, str(work)], cwd=work, env=env, capture_output=True,
                                  text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            out = json.loads(done.stdout)
            self.assertEqual(len(out["warnings"]), 1, out)
            self.assertIn("task-profiles.md: the impl-openai profile still names code-assurance", out["warnings"][0])
            self.assertTrue(out["after"])
            # The change went to the scratch home's preset file; the real one is the same as before.
            changed = json.loads((config_dir / skillset.CONFIG.name).read_text())
            self.assertNotIn("code-assurance", changed["presets"][changed["preset"]]["impl-openai"].get("skills", []))


if __name__ == "__main__":
    unittest.main()
