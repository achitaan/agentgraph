"""A made-up home folder for the whole test run, so no test depends on (or touches) the developer's real one.

`tests/__init__.py` calls `install()` before any test module imports agentgraph. After that every `Path.home()`,
`~`, `$HOME`/`%USERPROFILE%`, XDG folder and `%LOCALAPPDATA%` points into a throw-away folder that holds what a
fresh OpenCode setup would: `.config/opencode` with the toolkit's own settings template
(`config/oh-my-opencode-slim.json`, the file the installer puts there) and a few invented skills from
`tests/fixtures/home/`. The folder is deleted when the run ends.

Tests that need an empty or different home still build their own and pass it in or patch it; this is only the
default underneath them.
"""
import atexit
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "home"
TEMPLATE = ROOT / "config" / "oh-my-opencode-slim.json"
# Variables that would send a program to a real location (or to a developer's own override) instead of the home.
CLEARED = ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR",
           "OPENCODE_CONFIG_DIR", "OPENCODE_CONFIG", "AGENTGRAPH_PERSONAL", "AGENTGRAPH_HOME",
           "AGENTGRAPH_ROLLOVER_HOME", "HOMEDRIVE", "HOMEPATH")

HOME = None


def install():
    """Create the fake home once per process and point the environment at it. Child processes inherit it."""
    global HOME
    if HOME is not None:
        return HOME
    HOME = Path(tempfile.mkdtemp(prefix="agentgraph-testhome-")).resolve()
    atexit.register(shutil.rmtree, HOME, True)
    shutil.copytree(FIXTURE, HOME, dirs_exist_ok=True)
    config = HOME / ".config" / "opencode"
    config.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATE, config / TEMPLATE.name)
    for name in CLEARED:
        os.environ.pop(name, None)
    local, roaming = HOME / "AppData" / "Local", HOME / "AppData" / "Roaming"
    local.mkdir(parents=True, exist_ok=True)
    roaming.mkdir(parents=True, exist_ok=True)
    os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), LOCALAPPDATA=str(local), APPDATA=str(roaming))
    return HOME
