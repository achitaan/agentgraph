import os
from pathlib import Path
import tempfile
import unittest

from orchestrator.controlplane.config import apply_config, preview_config, preview_restore
from orchestrator.controlplane.security import platform_mutation_supported


@unittest.skipUnless(platform_mutation_supported()[0], 'config mutation requires POSIX guarantees')
class ConfigRestoreTests(unittest.TestCase):
    def test_exact_backup_and_reviewed_restoration(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve() / 'state'
            token, preview = preview_config({'reviewer.model': 'selected-reviewer'}, state)
            apply_config(token, state, 'approve:' + preview.data['approval_digest'])
            original = (state / 'config.json').read_bytes()
            token, preview = preview_config({'reviewer.model': 'other-reviewer'}, state)
            applied = apply_config(token, state, 'approve:' + preview.data['approval_digest'])
            backup = applied.data['previous_configuration_backup']
            self.assertEqual((state / backup).read_bytes(), original)
            self.assertEqual((state / backup).stat().st_mode & 0o777, 0o600)
            token, restore = preview_restore(backup, state)
            self.assertNotEqual((state / 'config.json').read_bytes(), original)
            apply_config(token, state, 'approve:' + restore.data['approval_digest'])
            self.assertEqual((state / 'config.json').read_bytes(), original)

    def test_missing_or_tampered_backup_does_not_change_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve() / 'state'
            token, preview = preview_config({'reviewer.model': 'selected-reviewer'}, state)
            apply_config(token, state, 'approve:' + preview.data['approval_digest'])
            before = (state / 'config.json').read_bytes()
            with self.assertRaises(ValueError):
                preview_restore('../config.json', state)
            name = 'config-backup-' + 'a' * 64 + '.json'
            with self.assertRaises(ValueError):
                preview_restore(name, state)
            (state / name).write_bytes(before)
            (state / name).chmod(0o600)
            with self.assertRaises(ValueError):
                preview_restore(name, state)
            self.assertEqual((state / 'config.json').read_bytes(), before)
