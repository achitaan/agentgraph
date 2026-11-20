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
        # Nothing here reads the real skills folder: the scratch home's holds one made-up skill, the one a profile
        # names for an agent of the active preset (picked from the toolkit's task-profiles.md, so a later change to
        # the agents' skill lists does not break it). The preset file and SKILLS.md are scratch copies, as everywhere.
        obj = json.loads(skillset.CONFIG.read_text())
        active = obj["presets"][obj["preset"]]

        class Every:
            def __contains__(self, name):
                return True

        refs = [(agent, skill) for agent, skill in skillset._profile_refs(skillset.PROFILES, Every())
                if agent in active and skill != "ui-ux-pro-max"]
        self.assertTrue(refs, "task-profiles.md names no skill for an agent of the active preset")
        agent, skill = refs[0]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "home"
            config_dir = home / ".config" / "opencode"
            skills = config_dir / "skills"
            (skills / skill).mkdir(parents=True)
            (skills / skill / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: {skill} skill\n---\nBody\n")
            self.assertFalse((skills / "personal-orchestrator").exists())
            shutil.copyfile(skillset.CONFIG, config_dir / skillset.CONFIG.name)
            work = Path(directory) / "work"
            work.mkdir()
            shutil.copyfile(skillset.MAP, work / "SKILLS.md")
            script = (
                "import json, sys\n"
                "from pathlib import Path\n"
                "from agentgraph import skillset\n"
                "work, agent, skill = Path(sys.argv[1]), sys.argv[2], sys.argv[3]\n"
                "paths = dict(map_path=work / 'SKILLS.md', backups=work / 'backups')\n"
                "skillset.add(agent, skill, **paths)\n"
                "before = skillset.profile_mismatches()\n"
                "result = skillset.remove(agent, skill, **paths)\n"
                "print(json.dumps({'before': before, 'warnings': result['warnings'],\n"
                "                  'after': skillset.profile_mismatches()}))\n")
            env = {**os.environ, "HOME": str(home), "PYTHONPATH": str(ROOT)}
            done = subprocess.run([sys.executable, "-c", script, str(work), agent, skill], cwd=work, env=env,
                                  capture_output=True, text=True, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            out = json.loads(done.stdout)
            self.assertEqual(out["before"], [], out)
            self.assertEqual(len(out["warnings"]), 1, out)
            self.assertIn(f"task-profiles.md: the {agent} profile still names {skill}", out["warnings"][0])
            self.assertTrue(out["after"])
            # The change went to the scratch home's preset file.
            changed = json.loads((config_dir / skillset.CONFIG.name).read_text())
            self.assertNotIn(skill, changed["presets"][changed["preset"]][agent].get("skills", []))

if __name__ == "__main__":
    unittest.main()
