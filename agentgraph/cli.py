"""The launcher's command table in Python: `python -P -m agentgraph.cli COMMAND ...`.

`bin/agentgraph` is a /bin/sh script, which native Windows does not have; `bin/agentgraph.cmd` starts this module
instead. The table below says what the script's `case` says: which module a command runs, and whether the command
word is dropped first. `tests/test_launcher.py` reads both and fails when they differ, so a command added to one
is added to the other. The script stays the launcher on macOS and Linux.

Each module runs as `python -m MODULE` does (runpy, as `__main__`), with the same arguments and exit status.
"""
from __future__ import annotations

import runpy
import sys

# command -> (module, drop the command word, arguments put before the rest)
COMMANDS: dict[str, tuple[str, bool, tuple[str, ...]]] = {
    "check": ("agentgraph.check", True, ()),
    "record": ("agentgraph.ledger", False, ()),
    "feedback": ("agentgraph.ledger", False, ()),
    "signals": ("agentgraph.ledger", False, ()),
    "improved": ("agentgraph.ledger", False, ()),
    "cases": ("agentgraph.cases", True, ()),
    "serve": ("agentgraph.server", True, ()),
    "bloat": ("agentgraph.bloat", True, ()),
    "cost": ("agentgraph.cost", True, ()),
    "chat": ("agentgraph.chatfeed", True, ()),
    "sidechat": ("agentgraph.sidechat", True, ()),
    "snapshot": ("agentgraph.snapshot_json", True, ("--no-activity-detail",)),
    "flow": ("agentgraph.structure", False, ()),
    "structure": ("agentgraph.structure", True, ()),
    "skills": ("agentgraph.skillset", True, ()),
    "agent": ("agentgraph.roster", False, ()),
    "skill": ("agentgraph.skillfiles", False, ()),
    "output": ("agentgraph.outputs", False, ()),
    "preset": ("agentgraph.setups", False, ()),
    "setup": ("agentgraph.setups", False, ()),
    "spend": ("agentgraph.spend", True, ()),
    "team": ("agentgraph.teams", True, ()),
    "improve": ("agentgraph.improve", True, ()),
    "loop": ("agentgraph.looprun", True, ()),
    "graph": ("agentgraph.graphedit", True, ()),
    "status": ("agentgraph.status", True, ()),
    "bench": ("agentgraph.bench", True, ()),
    "route": ("agentgraph.routing", True, ()),
}
# Anything else (no arguments, a flag, `handoff`, `node`, `review-copy` ...) goes to the viewer's own entry point.
DEFAULT_MODULE = "agentgraph.__main__"
HOME_MODULE = "agentgraph.ledger"  # "--home DIR COMMAND ..." with at least three words


def resolve(argv: list[str]) -> tuple[str, list[str]]:
    """The module to run and its arguments, as `bin/agentgraph` chooses them."""
    if argv[:1] == ["--home"] and len(argv) >= 3:
        return HOME_MODULE, list(argv)
    entry = COMMANDS.get(argv[0]) if argv else None
    if entry is None:
        return DEFAULT_MODULE, list(argv)
    module, drop, before = entry
    return module, [*before, *(argv[1:] if drop else argv)]


def main(argv: list[str] | None = None) -> int:
    given = list(sys.argv[1:] if argv is None else argv)
    module, args = resolve(given)
    sys.argv = [module, *args]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
