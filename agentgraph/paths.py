"""Where the files you customize live: the toolkit's copy in this checkout, or your own personal copy.

Four things the editors and commands write are yours, not the toolkit's: `structure/` (agent descriptions, your
flows and teams), `SKILLS.md`, `task-profiles.md` and `setups/`. Keep them in a personal folder and a
`git pull` of the toolkit never touches them. The folder is, first match wins:

1. `$AGENTGRAPH_PERSONAL`, when it names an existing folder;
2. `~/.config/opencode/agentgraph-personal/`, when it exists;
3. nothing: the copies in this checkout are used, as before the personal folder existed.

Inside a personal folder an item is used when it is there (`structure/`, `SKILLS.md`, `task-profiles.md`);
otherwise that item falls back to the checkout's copy. `setups/` is the exception: it is only ever written by
you, so in a personal folder it is always the personal one (created on first save).

    python3 -m agentgraph.paths show   # which copy is in use for each item, and why
    python3 -m agentgraph.paths init   # copy the checkout's copies into the personal folder (never overwrites)

The constants (`skillset.MAP`, `skillset.PROFILES`, `setups.SETUPS`, `snapshot_json.STRUCTURE_DIR`) are resolved
once, when agentgraph is imported.
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = "AGENTGRAPH_PERSONAL"
NAMES = {"structure": "structure", "skills_map": "SKILLS.md", "profiles": "task-profiles.md", "setups": "setups"}
ALWAYS_PERSONAL = ("setups",)  # written fresh by the user, so never read from the checkout once a folder is active


@dataclass(frozen=True)
class Resolved:
    item: str
    path: Path
    source: str  # "env", "home" or "repo"
    reason: str


def default_folder(home: Path | str | None = None) -> Path:
    return (Path(home) if home is not None else Path.home()) / ".config" / "opencode" / "agentgraph-personal"


def personal_folder(env=None, home=None) -> tuple[Path | None, str, str]:
    """(folder or None, source, why) for the active personal folder."""
    env = os.environ if env is None else env
    raw = env.get(ENV)
    note = ""
    if raw:
        folder = Path(raw).expanduser().absolute()
        if folder.is_dir():
            return folder, "env", f"{ENV} names it"
        note = f"{ENV} is set to {folder}, which is not a folder, so it is ignored; "
    folder = default_folder(home)
    if folder.is_dir():
        return folder, "home", note + "the default personal folder exists"
    return None, "repo", note + f"no {ENV} folder and no {folder}"


def resolve_item(item: str, env=None, home=None, repo: Path | None = None) -> Resolved:
    if item not in NAMES:
        raise KeyError(f"unknown item {item!r}; one of {', '.join(NAMES)}")
    repo = Path(repo) if repo is not None else REPO
    name = NAMES[item]
    folder, source, why = personal_folder(env, home)
    if folder is not None:
        mine = folder / name
        if mine.exists() or item in ALWAYS_PERSONAL:
            return Resolved(item, mine, source, why if mine.exists() else f"{why}; not created yet")
        return Resolved(item, repo / name, "repo", f"{why}, but it has no {name}, so the toolkit's copy is used")
    return Resolved(item, repo / name, "repo", why)


def resolve(item: str, env=None, home=None, repo: Path | None = None) -> Path:
    return resolve_item(item, env, home, repo).path


def target_folder(env=None, home=None) -> Path:
    """The folder `init` fills: the one `AGENTGRAPH_PERSONAL` names (even before it exists), else the default."""
    env = os.environ if env is None else env
    raw = env.get(ENV)
    return Path(raw).expanduser().absolute() if raw else default_folder(home)


def init(env=None, home=None, repo: Path | None = None, out=print) -> int:
    """Copy the checkout's copies into the personal folder. Never overwrites: an item already there is refused."""
    repo = Path(repo) if repo is not None else REPO
    folder = target_folder(env, home)
    refused = copied = 0
    for item, name in NAMES.items():
        src, dst = repo / name, folder / name
        if dst.exists() or dst.is_symlink():
            out(f"refused  {dst} already exists; it was not touched")
            refused += 1
        elif src.is_dir():
            shutil.copytree(src, dst, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
            out(f"copied   {src} -> {dst}")
            copied += 1
        elif src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            out(f"copied   {src} -> {dst}")
            copied += 1
        elif item == "setups":
            dst.mkdir(parents=True, exist_ok=True)
            out(f"created  {dst} (the checkout has no saved setups yet)")
            copied += 1
        else:
            out(f"skipped  {src} does not exist")
    if copied:
        active = personal_folder(env, home)[0]
        out(f"Personal folder: {folder}. " + ("It is used from the next start of agentgraph." if active == folder
            else f"Not active yet: set {ENV}={folder} to use it."))
    return 1 if refused else 0


def show(env=None, home=None, repo: Path | None = None, out=print) -> int:
    for item in NAMES:
        r = resolve_item(item, env, home, repo)
        out(f"{item:<11} {r.source:<5} {r.path}")
        out(f"{'':<11} {r.reason}")
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["show"]:
        return show()
    if args == ["init"]:
        return init()
    print(__doc__.split("\n\n")[0] + "\n\nusage: python3 -m agentgraph.paths show | init", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
