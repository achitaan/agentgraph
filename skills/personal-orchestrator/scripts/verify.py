#!/usr/bin/env python3
"""Offline tests against this skill's own source; no installed Lane required."""
import os
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['PYTHONPATH'] = str(ROOT)
suite = unittest.defaultTestLoader.discover(str(ROOT / 'orchestrator/tests'), pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
