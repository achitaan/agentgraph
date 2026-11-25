"""Every module of the package must import on Windows, where `fcntl`, `termios`, `tty`, `pwd`, `grp`, `resource`
and `curses` do not exist. A subprocess makes those unimportable (the file layer in the portable mode, as on
Windows), then imports each module of `agentgraph/` and lists the ones that cannot be imported."""
import ast
import json
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
    try:
        import fcntl as _real_fcntl  # the real module, imported before every one of them is hidden
    except ImportError:  # on Windows there is none, and osfs does not ask for it there
        _real_fcntl = None
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

# Names that exist on macOS and Linux and not on native Windows. Reading one when a module is imported stops the
# whole program there; inside a function, or behind a `try` or a test of the platform, it does not.
MISSING_NAMES = {
    "signal": {"SIGHUP", "SIGKILL", "SIGQUIT", "SIGCONT", "SIGUSR1", "SIGUSR2", "SIGWINCH"},
    "os": {"killpg", "getpgid", "fork", "getuid", "geteuid", "O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "mkfifo"},
}
PLATFORM_TESTS = ("hasattr", "os.name", "sys.platform", "platform.system", "WINDOWS")


def unguarded_uses(source):
    """[(line, "os.getuid")] for module-level uses of MISSING_NAMES outside a `try` body and a platform test."""
    found = []

    def visit(node, guarded):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.attr in MISSING_NAMES.get(node.value.id, ()) and not guarded:
                found.append((node.lineno, f"{node.value.id}.{node.attr}"))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # decorators and default values run at import; the body does not
            for item in [*node.decorator_list, *node.args.defaults, *[d for d in node.args.kw_defaults if d]]:
                visit(item, guarded)
            return
        if isinstance(node, ast.Lambda):
            for item in node.args.defaults:
                visit(item, guarded)
            return
        if isinstance(node, ast.Try):
            for item in node.body:
                visit(item, True)
            for item in [*node.handlers, *node.orelse, *node.finalbody]:
                visit(item, guarded)
            return
        if isinstance(node, ast.If) and any(word in ast.unparse(node.test) for word in PLATFORM_TESTS):
            visit(node.test, guarded)
            for item in [*node.body, *node.orelse]:
                visit(item, True)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(ast.parse(source), False)
    return found


class StaticScanTests(unittest.TestCase):
    def test_the_scan_sees_what_it_is_for(self):
        source = textwrap.dedent("""
            import os, signal
            A = signal.SIGHUP
            B = os.O_NOFOLLOW | os.O_DIRECTORY
            def inside():
                return os.getuid(), signal.SIGKILL
            try:
                C = os.mkfifo
            except AttributeError:
                C = None
            if hasattr(os, "fork"):
                D = os.fork
            if os.name == "nt":
                pass
            else:
                E = os.getpgid
            class Late:
                F = os.killpg
            def with_default(x=os.O_NONBLOCK):
                pass
            G = getattr(os, "geteuid", None)
            H = signal.SIGTERM
        """)
        self.assertEqual([name for _line, name in unguarded_uses(source)],
                         ["signal.SIGHUP", "os.O_NOFOLLOW", "os.O_DIRECTORY", "os.killpg", "os.O_NONBLOCK"])

    def test_no_module_reads_a_posix_only_name_when_it_is_imported(self):
        hits = []
        for path in sorted((ROOT / "agentgraph").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            hits += [f"{path.relative_to(ROOT).as_posix()}:{line}: {name}" for line, name in unguarded_uses(source)]
        self.assertEqual(hits, [], "read at import time, so the program stops on Windows:\n  " + "\n  ".join(hits))


class WindowsImportTests(unittest.TestCase):
    def test_every_module_imports_without_posix_only_modules(self):
        env = dict(os.environ, AGENTGRAPH_OSFS="portable")
        result = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env, capture_output=True, text=True,
                                timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        failed = json.loads(result.stdout.strip().splitlines()[-1])
        listing = "\n".join(f"  {name}: {why}" for name, why in sorted(failed.items()))
        self.assertEqual(failed, {}, f"{len(failed)} module(s) cannot be imported on Windows:\n{listing}")


if __name__ == "__main__":
    unittest.main()
