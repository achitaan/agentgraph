"""The installer's front door for native Windows: `install.cmd [--dry-run] | --list-backups | --restore NAME [--dry-run]`.

The same options and the same work as `install.sh`, which wraps `installer.py` for macOS and Linux. The config folder
is `$OPENCODE_CONFIG_DIR`, else `.config/opencode` under the user's home folder (on Windows
`%USERPROFILE%\\.config\\opencode`, where OpenCode keeps it); the commands are `installer.TOOLKIT_COMMANDS`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from . import installer
except ImportError:  # run as a script
    import installer

USAGE = "usage: install.cmd [--dry-run] | --list-backups | --restore NAME [--dry-run]"


def target_folder(env=None, home=None) -> Path:
    env = os.environ if env is None else env
    given = env.get("OPENCODE_CONFIG_DIR")
    return Path(given) if given else (Path(home) if home is not None else Path.home()) / ".config" / "opencode"


def main(argv=None, env=None, home=None, source=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode, dry, backup = "install", False, None
    while args:
        item = args.pop(0)
        if item == "--dry-run":
            dry = True
        elif item == "--list-backups":
            mode = "list-backups"
        elif item == "--restore" and args:
            mode, backup = "restore", args.pop(0)
        else:
            print(USAGE, file=sys.stderr)
            return 2
    source = Path(source) if source is not None else Path(__file__).resolve().parent.parent  # tests pass a scratch kit
    call = [mode, "--target", str(target_folder(env, home)), "--source", str(source),
            "--commands", ",".join(installer.TOOLKIT_COMMANDS)]
    if dry:
        call.append("--dry-run")
    if backup:
        call += ["--backup", backup]
    return installer.main(call)


if __name__ == "__main__":
    sys.exit(main())
