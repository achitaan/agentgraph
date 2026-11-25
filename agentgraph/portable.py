"""Keep the config folder's absolute path out of the files the toolkit ships.

Some live settings must name the exact absolute path of the launcher (a permission rule such as
`<config folder>/orchestration/bin/agentgraph review-copy *`; OpenCode does not expand `~` or `$HOME` there), and one
prompt line names the same path. `sync.sh` copies those files into the checkout with the folder's path replaced by
TOKEN; `install.sh` puts the target folder's absolute path back. Only the whole path counts: the path as a prefix of a
longer folder or file name (`.../opencode-old/x`) is left alone, and so is a path that merely ends the same way.

    python3 -m agentgraph.portable tokenize --dir DIR FILE_OR_FOLDER...   # in place, for sync.sh
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

TOKEN = "@OPENCODE_CONFIG_DIR@"
_BEFORE = r"(?<![A-Za-z0-9_.~/-])"
_WINDOWS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")  # C:\... or \\server\...
_AFTER = r"(?![A-Za-z0-9_-])(?!\.[A-Za-z0-9_])"


def _json_forms(path: str) -> list[str]:
    """How the path reads inside a JSON string (escaped), with and without \\u escapes for non-ASCII."""
    forms = [json.dumps(path, ensure_ascii=False)[1:-1], json.dumps(path)[1:-1]]
    return [form for i, form in enumerate(forms) if form not in forms[:i]]


def _patterns(dirs: list[str], as_json: bool) -> list[str]:
    seen: list[str] = []
    for d in dirs:
        for spelled in ((d, d.replace("\\", "/")) if _WINDOWS_PATH.match(d) else (d,)):  # also written with "/"
            spelled = spelled.rstrip("/\\" if _WINDOWS_PATH.match(d) else "/") or "/"
            for form in (_json_forms(spelled) if as_json else [spelled]):
                if form != "/" and form not in seen:
                    seen.append(form)
    return sorted(seen, key=len, reverse=True)  # the longer path first


def tokenize(text: str, dirs: list[str], as_json: bool) -> str:
    for path in _patterns(dirs, as_json):
        text = re.sub(_BEFORE + re.escape(path) + _AFTER, lambda _m: TOKEN, text)
    return text


def render(text: str, target: str, as_json: bool) -> str:
    """TOKEN becomes the absolute target folder (escaped when the file is JSON)."""
    path = os.path.abspath(target)
    return text.replace(TOKEN, _json_forms(path)[0] if as_json else path)


def folder_names(directory: str) -> list[str]:
    """The folder's path as given and, when it differs, with links resolved."""
    path = os.path.abspath(directory)
    real = os.path.realpath(directory)
    return [path] if path == real else [path, real]


def tokenize_file(path: Path, dirs: list[str]) -> bool:
    """Rewrite one mirrored file in place; True when it changed. Not UTF-8 text: left alone."""
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False
    new = tokenize(text, dirs, path.suffix == ".json")
    if new == text:
        return False
    path.write_bytes(new.encode("utf-8"))
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="portable", description=__doc__.split("\n")[0])
    parser.add_argument("mode", choices=("tokenize",))
    parser.add_argument("--dir", required=True, help="the live config folder")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    dirs = folder_names(args.dir)
    for top in args.paths:
        for path in sorted(top.rglob("*")) if top.is_dir() else [top]:
            if path.is_file() and not path.is_symlink():
                tokenize_file(path, dirs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
