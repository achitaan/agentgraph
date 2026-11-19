#!/usr/bin/env python3
"""Self-contained personal orchestration controller; no downloads or plugin imports."""
import sys
sys.dont_write_bytecode = True
if sys.version_info < (3, 11):
    raise SystemExit('Python 3.11+ is required.')

from orchestrator.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
