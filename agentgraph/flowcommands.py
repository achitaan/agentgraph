"""A path as a command: `NAME.md` in OpenCode's commands folder, so `/NAME goal` runs the path on the goal.

The file holds a `description` and, as its body, the path's run message (agentgraph.flowrun) with `$ARGUMENTS` as the
goal; its first frontmatter line is a marker saying this toolkit wrote it. agentgraph.structure writes it when a
flow's command is turned on, renames it with the flow, rewrites it with the flow's instruction and removes it when the
command is turned off or the flow is removed. The bytes follow from the flow alone, so undoing any of these writes the
same file again, byte for byte; a file whose bytes differ from what the flow would give (changed by hand) is never
replaced or removed. What an earlier version of this toolkit wrote for the flow (agentgraph.flowearlier) counts as
what it would give (`written`): such a file is rewritten in the current shape, renamed and removed like one this
version wrote.

The body holds no list of the other drawn paths that bind a run: they change after the file is written. It states
the general rule and how to see which apply now (`agentgraph flow prompt --name NAME`).

Every write happens inside the commands folder, opened without following a symlink and locked for one writer at a
time: a new file by exclusive rename (never over a file that appeared), a change by rename over the file it read, and
each refused when the file is no longer what was read.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import flowearlier, flowrun, names, sources

COMMANDS_DIR = sources.CONFIG_DIR / "commands"
MARK = "# agentgraph: flow command"
_MARK_LINE = MARK + " (written by `agentgraph flow command`; change the path with `agentgraph flow`)"
TOOLKIT_COMMANDS_DIR = Path(__file__).resolve().parent.parent / "commands"
# Measured on OpenCode 2.3.2 (2026-11-19): the server's own commands (GET /api/command on a scratch host with no
# command files), the Desktop app's slash commands and the terminal app's slash commands and their aliases (read from
# the installed app and its CLI). A command file of one of these names would be shadowed or would shadow it.
HOST_COMMANDS = frozenset({
    "init", "review", "goal",
    "agent", "btw", "compact", "connect", "export", "fork", "mcp", "model", "new", "open", "redo", "reload",
    "terminal", "undo",
    "agents", "cd", "clear", "copy", "debug", "diff", "editor", "exit", "help", "highlights", "mcps", "models", "pair",
    "plugins", "rename", "restart", "sessions", "settings", "skills", "stats", "status", "themes", "timeline", "update",
    "usage", "variants", "worktrees",
    "continue", "effort", "project", "projects", "q", "quit", "resume", "thinking", "web",
})
# Commands the oh-my-opencode-slim plugin (v3.0.6) adds, measured the same way with the plugin loaded.
PLUGIN_COMMANDS = frozenset({"deepwork", "reflect", "loop", "interview", "implement"})
# What the commands proof (n96/r1/command-proof.md) measured: OpenCode reads its commands folder when it starts and
# when the configuration is reloaded, not when a file appears.
APPLIES = ("OpenCode reads its commands folder when it starts and when its configuration is reloaded (Desktop: the "
           "/reload command), not at once: /{name} is {state} after a reload or a restart.")
LIVE = False


APPLIES_ANY = APPLIES.format(name="NAME", state="there").replace("/NAME is there", "a new command is there")


def applies(name: str, on: bool) -> str:
    """What a command write does in OpenCode, as measured (APPLIES)."""
    return APPLIES.format(name=name, state="there" if on else "gone")
# What OpenCode does to a command's text before sending it (2.3.2): `$ARGUMENTS` and `$1`, `$2`, ... become the
# arguments, and !`...` runs as a shell command (the goal typed after /NAME included, which nothing here can check).
# The instruction is fixed text, so it may hold none of them. An @path file reference is sent as plain text, never
# attached, fenced or not (measured with a recording stand-in model: n96/r2/at-reference-proof.md), so it is allowed.
_EXPANDED = re.compile(r"\$(?:ARGUMENTS|[1-9])|!`")
FILE_LIMIT = 64 * 1024  # as agentfiles.FILE_LIMIT: the folder helpers read no more


class CommandError(Exception):
    """A refused request; the message says why in plain words."""


def toolkit_commands() -> frozenset[str]:
    """The names of the toolkit's own command files (`commands/` in the orchestration folder)."""
    try:
        return frozenset(path.stem for path in TOOLKIT_COMMANDS_DIR.glob("*.md"))
    except OSError:
        return frozenset()


def is_ours(data: bytes | None) -> bool:
    return data is not None and data.startswith(b"---\n" + MARK.encode("ascii"))


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _file(flow: dict, body: str) -> bytes:
    description = f"Run the path {flow['name']} ({' -> '.join(flow['path'])}) on the goal given after /{flow['name']}"
    return ("---\n" + _MARK_LINE + "\ndescription: " + _quote(description) + "\n---\n" + body).encode("utf-8")


def render(flow: dict, hubs=frozenset()) -> bytes:
    """The bytes of the command file for `flow` ({"id", "path", "text", "name"}, the name set)."""
    reason = text_refusal(flow.get("text") or "")
    if reason:
        raise CommandError(reason)
    data = _file(flow, flowrun.run_message(flow, "$ARGUMENTS", hubs, checked=False, command=True))
    if len(data) > FILE_LIMIT:
        raise CommandError("the command file would be too large")
    return data


def written(flow: dict, hubs=frozenset()) -> tuple[bytes, ...]:
    """Every command file this toolkit, this version or an earlier one, wrote for `flow`: `render` first."""
    return (render(flow, hubs), *(_file(flow, body) for body in flowearlier.bodies(flow, hubs).values()))


def text_refusal(text: str) -> str:
    """Why an instruction cannot go into a command file, or ""."""
    found = _EXPANDED.search(text)
    if found is None:
        return ""
    return (f"the instruction holds {found.group(0)!r}, which OpenCode would replace or run in a command's text; "
            "take it out of the instruction first, or keep the path without a command")


def name_refusal(name: str) -> str:
    """Why `name` cannot be a command this toolkit writes, whatever the folder holds, or ""."""
    if name in HOST_COMMANDS:
        return f"/{name} is a command OpenCode already has; give the path another name"
    if name in PLUGIN_COMMANDS:
        return f"/{name} is a command the oh-my-opencode-slim plugin already has; give the path another name"
    if name in toolkit_commands():
        return f"/{name} is one of this toolkit's own commands; give the path another name"
    return ""


class Folder:
    """The commands folder, open and locked (`with Folder(path, create=...) as folder`); None inside when it is
    missing and not created."""

    def __init__(self, directory: Path | None, *, create: bool):
        self.path = Path(directory) if directory is not None else COMMANDS_DIR
        self.create = create
        self._context = None
        self.fd = None

    def __enter__(self):
        from . import agentfiles  # agentfiles imports modules that import this one
        self._context = agentfiles._folder(self.path, create=self.create)
        try:
            self.fd = self._context.__enter__()
        except agentfiles.AgentfilesError as exc:
            self._context = None
            if not self.create and not self.path.exists():
                return self
            raise CommandError(str(exc).replace("agents folder", "commands folder")) from None
        return self

    def __exit__(self, *exc):
        if self._context is not None:
            return self._context.__exit__(*exc)
        return False

    @staticmethod
    def _command(name) -> None:
        """Only a valid command name (agentgraph.names) is ever a file name here."""
        if not isinstance(name, str) or not names.COMMAND.fullmatch(name):
            raise CommandError(f"{str(name)[:40]!r} is not a valid command name")

    def read(self, name: str) -> bytes | None:
        self._command(name)
        if self.fd is None:
            return None
        from . import agentfiles
        try:
            return agentfiles._read_at(self.fd, name + ".md")
        except agentfiles.AgentfilesError as exc:
            raise CommandError(f"{name}.md in the commands folder: {exc}") from None

    def write(self, name: str, data: bytes | None, before: bytes | None) -> None:
        """`data` as NAME.md (None: delete it), refused when the file is no longer `before` (None: no file)."""
        self._command(name)
        if self.fd is None:
            if data is None and before is None:
                return
            raise CommandError(f"there is no commands folder at {self.path}")
        if data is None and before is None:
            if self.read(name) is not None:
                raise CommandError(f"{name}.md appeared in the commands folder while this change was being made; "
                                   "nothing was written")
            return
        from . import agentfiles
        try:
            agentfiles._write_at(self.fd, name + ".md", data, before)
        except agentfiles.AgentfilesError as exc:
            raise CommandError(str(exc).replace("agent file", "command file")) from None


def _accepted(expected) -> tuple[bytes, ...]:
    return () if expected is None else (expected,) if isinstance(expected, bytes) else tuple(expected)


def check_free(folder: Folder, name: str, expected: bytes | tuple[bytes, ...] | None = None) -> bytes | None:
    """Refuse a name that is taken: a command OpenCode, the plugin or this toolkit has, or a file in the folder that
    is not `expected` (the file this flow's command would be, or any of them: `written`). Returns the file's bytes
    when it is `expected`."""
    reason = name_refusal(name)
    if reason:
        raise CommandError(reason)
    data = folder.read(name)
    if data is None:
        return None
    if data in _accepted(expected):
        return data
    if not is_ours(data):
        raise CommandError(f"the commands folder already has {name}.md, which this toolkit did not write; give the "
                           "path another name")
    raise CommandError(f"the commands folder already has {name}.md, written by this toolkit for another path; give "
                       "the path another name")


def check_unchanged(folder: Folder, name: str, expected: bytes | tuple[bytes, ...], *,
                    leave_foreign: bool = False) -> bytes | None:
    """The bytes of this flow's command file NAME.md when they are exactly `expected`, or one of them (`written`)
    (None when there is no such file); CommandError when it was changed by hand, so it is never replaced or removed. `leave_foreign`: a file
    without this toolkit's marker is not this flow's command; None, and it is left alone (turning a command off or
    removing the flow then only changes the flow)."""
    data = folder.read(name)
    if data is None or data in _accepted(expected):
        return data
    if not is_ours(data):
        if leave_foreign:
            return None
        raise CommandError(f"the commands folder has {name}.md, which this toolkit did not write; it was left as it "
                           "is and nothing was changed. Turn the path's command off, or give the path another name")
    raise CommandError(f"the command file {name}.md was changed by hand (or by another version of this toolkit); "
                       "it was left as it is and nothing was changed. Edit or delete it in the commands folder, or "
                       "write it again with `agentgraph flow command ID --on` once it is gone")
