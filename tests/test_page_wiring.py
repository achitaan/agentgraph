"""The plain web page must hand the view every write action the server offers."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "agentgraph" / "web" / "index.html").read_text()
SERVER = (ROOT / "agentgraph" / "server.py").read_text()
WIRED = {"flow": "/api/structure/flow", "skills": "/api/skills", "roster": "/api/roster",
         "skillfile": "/api/skillfile", "team": "/api/structure/team",
         "output": "/api/output"}


class PageWiring(unittest.TestCase):
    def test_every_write_endpoint_of_the_server_is_an_action_on_the_page(self):
        actions = re.search(r"actions:[^{]*\{([^}]*)\}", PAGE)
        self.assertIsNotNone(actions, "index.html passes no actions to the view")
        names = {name.strip() for name in actions.group(1).split(",")}
        for name, path in WIRED.items():
            with self.subTest(action=name):
                self.assertIn(f'"{path}"', SERVER, f"the server has no {path}")
                self.assertRegex(PAGE, rf"const {name} = post\('{re.escape(path)}'")
                self.assertIn(name, names, f"index.html does not pass actions.{name}")


if __name__ == "__main__":
    unittest.main()
