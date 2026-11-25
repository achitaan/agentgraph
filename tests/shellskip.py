"""More helpers for tests that run shell commands or hold locks, next to `winskip` (which this module does not replace).

A loop's check, a review-copy command and a bench check go through `sh -c`; on Windows with no `sh` on PATH they go through `cmd`,
which cannot read most of what the tests write (`$(...)`, `[ ... ]`, `sleep`, `/dev/null`). `HAS_SH` says whether the shell the code
under test will pick understands those; `needs_sh` skips a test that cannot do without them; `python_command` and `sleep_command` give
a command that means the same in sh and in cmd, where a test can use one instead of a skip.
"""
import shutil
import sys
import unittest

from tests.winskip import WINDOWS

HAS_SH = (not WINDOWS) or shutil.which("sh") is not None

needs_sh = unittest.skipUnless(HAS_SH, "needs a POSIX sh on PATH (without one the command runs through cmd)")


def python_command(code: str) -> str:
    """A command string that runs the Python source `code` with this interpreter, the same under sh and under cmd: the interpreter's path
    with forward slashes in double quotes, the code in double quotes. `code` must hold no backslash, no `$` and no double quote (use
    single quotes in it): those are the characters sh and cmd read differently inside double quotes."""
    assert not any(ch in code for ch in '\\$"'), code
    return f'"{sys.executable.replace(chr(92), "/")}" -c "{code}"'


def sleep_command(seconds) -> str:
    """`sleep N` where there is an sh (the test's command is then what it always was); a Python sleep where there is not."""
    if HAS_SH:
        return f"sleep {seconds}"
    return python_command(f"import time; time.sleep({seconds})")


def sh_or(sh_text: str, code: str) -> str:
    """`sh_text` where there is an sh (so the Mac runs exactly the command the test always ran); otherwise a Python command that does the same."""
    return sh_text if HAS_SH else python_command(code)


# The check that fails the first time and passes the second (a counter in `.count`); the Python form is the same check for cmd.
CHECK_SECOND = sh_or(
    'n=$(cat .count 2>/dev/null || echo 0); n=$((n+1)); echo $n > .count; echo "check call $n"; [ "$n" -ge 2 ]',
    "import os; n = int(open('.count').read()) + 1 if os.path.exists('.count') else 1; open('.count', 'w').write(str(n)); "
    "print('check call', n); raise SystemExit(0 if n >= 2 else 1)")


# `true` and `false` as a check command: the sh builtins where there is an sh, `exit 0` / `exit 1` (cmd has neither word) where there is not.
TRUE = "true" if HAS_SH else "exit 0"
FALSE = "false" if HAS_SH else "exit 1"
