"""The launcher a user runs: `bin/agentgraph` (a sh script) on macOS and Linux, `bin/agentgraph.cmd` on Windows."""
import os
from pathlib import Path

from tests import fakehome

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = [str(ROOT / "bin" / ("agentgraph.cmd" if os.name == "nt" else "agentgraph"))]


def bare_env(**extra):
    """A near-empty environment for a child that must not see the real one. On Windows Python needs a few
    variables (SYSTEMROOT above all) to start at all, and pathlib needs the profile folder, so those are kept;
    elsewhere it is just a PATH. The home is always the tests' made-up one (tests.fakehome), never the user's real
    one, which a child with no HOME would otherwise look up in the system's user database."""
    if os.name != "nt":
        base = {"PATH": "/usr/bin:/bin"}
    else:
        keep = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "COMSPEC", "USERPROFILE", "HOMEDRIVE", "HOMEPATH")
        base = {key: value for key, value in os.environ.items() if key.upper() in keep}
    return {**base, **home_env(fakehome.HOME), **extra}


def home_env(home):
    """HOME for a child on every system: POSIX reads HOME, Windows reads USERPROFILE (what Path.home() uses)."""
    return {"HOME": str(home), "USERPROFILE": str(home)}
