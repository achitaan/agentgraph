"""Every module of the package must import on Windows, where `fcntl`, `termios`, `tty`, `pwd`, `grp`, `resource`
and `curses` do not exist. A subprocess makes those unimportable (the file layer in the portable mode, as on
Windows), then imports each module of `agentgraph/` and lists the ones that cannot be imported."""
import os
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PROBE = textwrap.dedent('''
    import builtins, importlib, json, pkgutil, sys, traceback

    MISSING = ("fcntl", "termios", "tty", "pwd", "grp", "resource", "curses")
    # osfs may fall back to fcntl for locks on a machine that has it (a Mac running the portable mode); on Windows
    # it uses msvcrt. So fcntl stays importable for the one file agentgraph/osfs.py and for nothing else.
    import fcntl as _real_fcntl  # the real module, imported before every one of them is hidden
    for name in MISSING:
        sys.modules[name] = None
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "fcntl" and (globals or {}).get("__name__") == "agentgraph.osfs":
            return _real_fcntl
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = guarded

    import agentgraph
    failed = {}
    for info in pkgutil.walk_packages(agentgraph.__path__, "agentgraph."):
        if info.name.endswith(".__main__"):
            continue  # runs the program on import
        try:
            importlib.import_module(info.name)
        except BaseException as error:  # noqa: BLE001
            failed[info.name] = f"{type(error).__name__}: {error}"
    # __main__ is imported by its path with a different name so its top-level code is not run as a program
    main = agentgraph.__path__[0] + "/__main__.py"
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("agentgraph._main_probe", main)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except BaseException as error:  # noqa: BLE001
        failed["agentgraph.__main__"] = f"{type(error).__name__}: {error}"
    print(json.dumps(failed))
''')


class WindowsImportTests(unittest.TestCase):
    def test_every_module_imports_without_posix_only_modules(self):
        env = dict(os.environ, AGENTGRAPH_OSFS="portable")
        result = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True,
                                timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        import json
        failed = json.loads(result.stdout.strip().splitlines()[-1])
        listing = "\n".join(f"  {name}: {why}" for name, why in sorted(failed.items()))
        self.assertEqual(failed, {}, f"{len(failed)} module(s) cannot be imported on Windows:\n{listing}")


if __name__ == "__main__":
    unittest.main()
