import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SKILL = Path(__file__).resolve().parents[3]


class InstalledSkillTests(unittest.TestCase):
    def test_copied_skill_runs_outside_repository_without_lane_or_network(self):
        with tempfile.TemporaryDirectory(prefix='copied owned skill ') as temporary:
            root = Path(temporary).resolve()
            installed = root / 'skills/personal-orchestrator'
            shutil.copytree(SKILL, installed)
            guard = root / 'deny-network'
            guard.mkdir()
            (guard / 'sitecustomize.py').write_text('import socket\ndef deny(*a, **kw):\n raise AssertionError("network disabled during copied-skill test")\nsocket.socket.connect=deny\nsocket.create_connection=deny\n')
            environment = dict(os.environ, CODEX_HOME=str(root / 'no-codex-install'), HOME=str(root / 'empty-home'),
                               PYTHONPATH=str(guard), PYTHONDONTWRITEBYTECODE='1')
            entry = installed / 'scripts/controller.py'
            def run(*arguments):
                result = subprocess.run([sys.executable, '-B', str(entry), *arguments], cwd=root,
                                        env=environment, capture_output=True, text=True, check=True)
                return json.loads(result.stdout)
            version = run('version')
            self.assertFalse(version['marketplace_dependency'])
            route = run('route', '--objective', 'Fix a typo in the README', '--known-area',
                        '--acceptance-criteria', '--files', '1', '--risk', 'low')
            self.assertEqual(route['workflow'], 'direct')
            self.assertIsNone(route['model'])
            example = installed / 'references/examples'
            assessed = run('assess', '--proposal', str(example / 'proposal.json'), '--ledger', str(example / 'ledger.json'))
            self.assertEqual(assessed['dispatch_state'], 'not_dispatched')
            self.assertFalse((root / 'no-codex-install').exists())

    def test_skill_entrypoint_and_licenses_ship_with_implementation(self):
        self.assertTrue((SKILL / 'SKILL.md').is_file())
        self.assertTrue((SKILL / 'references/LICENSE.lane').is_file())
        self.assertTrue((SKILL / 'LICENSE').is_file())
        metadata = json.loads((SKILL / 'scripts/orchestrator/metadata.json').read_text())
        self.assertFalse(metadata['live_execution_verified'])
