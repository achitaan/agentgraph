"""Teams in the structure folder (the view calls them groups): several agents
report to one agent, the lead, and the lead sends one report on to the hub.

A team is one file in the structure folder's `teams/` folder, `<id>.md`; the id
is the lead's name (a name that is not a plain id is made into one):

    lead: reviewer
    members: impl-openai, impl-claude

    impl-openai and impl-claude report to reviewer. reviewer reconciles their
    results and sends one report to orchestrator.

The text after the blank line is the instruction, free text the user can edit;
it is generated from a template (never a model) until then. `add` writes a
team, `set_text` replaces its instruction (blank text means "write it again from
the lead and members"), `set_members` replaces the members (a generated
instruction is written again, an edited one is kept) and `remove` deletes the
file, and the `teams/` folder once it is empty. Every write replaces a whole
file atomically under a lock, inside `teams/` only, and never through a symlink.
A council seat stands for its council. `problems` is the rule `agentgraph check`
will enforce on a task graph. Command: `python3 -m agentgraph.teams list|add|text|members|remove`;
exits 0 on success, 1 when the request is refused, 2 on a usage error.

Agents are listed here from the structure folder itself (not through
`load_structure`), so that team files can never be taken for agents, whatever
`load_structure` does with the `teams/` folder.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX; elsewhere the in-process lock is the only one
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from .snapshot_json import FLOW_ID, FLOW_TEXT_LIMIT, FLOWS_FOLDER, HEAD_BYTES, STRUCTURE_DIR, read_settings
from .structure import StructureError, clean_text

TEAMS_FOLDER = "teams"  # inside the structure folder: one file per team, never a group of agents
TEAM_MEMBERS_LIMIT = 12
TEAM_FILES_LIMIT = 200
_LOCK = threading.Lock()  # one writer at a time inside a process; flock on the folder across processes
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class TeamError(StructureError):
    """A refused request; the message says why in plain words."""


# --- the instruction made from a team -----------------------------------------------------------

def team_text(lead: str, members: list[str], hub: str = "orchestrator") -> str:
    """B, C and D report to A. A reconciles their results and sends one report to the hub.
    (One member: B reports to A. A reconciles its result and sends one report to the hub.)"""
    if len(members) == 1:
        return f"{members[0]} reports to {lead}. {lead} reconciles its result and sends one report to {hub}."
    names = ", ".join(members[:-1]) + " and " + members[-1] if members else ""
    return f"{names} report to {lead}. {lead} reconciles their results and sends one report to {hub}."


# --- the agents of a structure folder -----------------------------------------------------------

def _agents(root: Path) -> dict:
    """{"group_of": {agent: group}, "seats": {lead: prefix}, "hubs": [names]} read as `load_structure` reads
    them, the `flows` and `teams` folders left out. TeamError when there is no structure folder."""
    try:
        folders = sorted(path for path in root.iterdir()
                         if path.is_dir() and not path.name.startswith(".")
                         and path.name not in (FLOWS_FOLDER, TEAMS_FOLDER))[:50]
    except OSError as exc:
        raise TeamError(f"no structure folder at {root}") from exc
    group_of: dict[str, str] = {}
    seats: dict[str, str] = {}
    groups: set[str] = set()
    for folder in folders:
        group = re.sub(r"^\d+[-_]", "", folder.name)
        if not group or group in groups:
            continue
        groups.add(group)
        try:
            files = sorted(path for path in folder.iterdir() if path.suffix == ".md" and path.is_file())[:100]
        except OSError:
            continue
        for path in files:
            if path.stem in group_of:
                continue
            group_of[path.stem] = group
            try:
                with path.open("rb") as stream:
                    settings, _, _ = read_settings(stream.read(HEAD_BYTES))
            except OSError:
                continue
            for key, value in settings:
                if key == "seats":
                    seats[path.stem] = value
    if not groups:
        raise TeamError(f"no structure folder at {root}")
    return {"group_of": group_of, "seats": seats,
            "hubs": sorted(name for name, group in group_of.items() if group == "hub")}


def _stand_for(shape: dict, name: str) -> str:
    """The council lead a seat stands for, or the name itself."""
    return next((lead for lead, prefix in shape["seats"].items() if name != lead and name.startswith(prefix)), name)


def _hub(shape: dict) -> str:
    return shape["hubs"][0] if shape["hubs"] else "orchestrator"


# --- rules --------------------------------------------------------------------------------------

def _reports_to(others: list[dict]) -> dict[str, set[str]]:
    reports: dict[str, set[str]] = {}
    for team in others:
        for member in team["members"]:
            reports.setdefault(member, set()).add(team["lead"])
    return reports


def _cycle(lead: str, members: list[str], others: list[dict]) -> str:
    """A refusal when members reporting to `lead` would close a cycle of leads (`lead` already reports,
    directly or through others, to one of its new members), else ""."""
    reports = _reports_to(others)
    came: dict[str, str] = {lead: ""}
    queue = [lead]
    while queue:
        current = queue.pop(0)
        for nxt in sorted(reports.get(current, ())):
            if nxt in came:
                continue
            came[nxt] = current
            if nxt in members:
                chain = [nxt]
                while chain[-1] != lead:
                    chain.append(came[chain[-1]])
                chain.reverse()  # lead … nxt
                return (f"{nxt} cannot report to {lead}: {lead} already reports to {nxt} "
                        f"({' → '.join(chain)}), so the reports would go round in a circle")
            queue.append(nxt)
    return ""


def _refusal(shape: dict, lead, members, others: list[dict]) -> tuple[str, list[str], list[str]]:
    """Check a lead and members (seats not yet replaced) against the structure and the other teams.
    Returns (lead, members, notes) with seats replaced; TeamError when refused."""
    if not isinstance(lead, str) or not lead:
        raise TeamError("the lead must be an agent name")
    if not isinstance(members, list) or not all(isinstance(name, str) and name for name in members):
        raise TeamError("the members must be a list of agent names")
    if len(members) < 1:
        raise TeamError("a team needs at least one member")
    if len(members) > TEAM_MEMBERS_LIMIT:
        raise TeamError(f"a team has at most {TEAM_MEMBERS_LIMIT} members")
    notes: list[str] = []

    def stand(name: str) -> str:
        found = _stand_for(shape, name)
        if found != name:
            notes.append(f"{name} is a seat of the council: stored as {found}")
        return found

    for name in [lead, *members]:
        if name not in shape["group_of"] and _stand_for(shape, name) == name:
            raise TeamError(f"unknown agent {name}: there is no {name}.md in the structure folder")
    lead = stand(lead)
    members = [stand(name) for name in members]
    hubs = set(shape["hubs"])
    if lead in hubs:
        raise TeamError(f"{lead} is the hub: everything already reports to it, so it cannot lead a team")
    for name in members:
        if name in hubs:
            raise TeamError(f"{name} is the hub: a team's report goes to it through the lead, "
                            "so it cannot be a member")
    if lead in members:
        raise TeamError(f"{lead} is the lead: it cannot also be a member of its own team")
    for index, name in enumerate(members):
        if name in members[:index]:
            raise TeamError(f"{name} is listed twice among the members")
    own = next((team for team in others if team["lead"] == lead), None)
    if own is not None:
        raise TeamError(f"{lead} already leads a team ({own['id']}); change that one instead")
    reason = _cycle(lead, members, others)
    if reason:
        raise TeamError(reason)
    return lead, members, notes


# --- files --------------------------------------------------------------------------------------

def team_file(lead: str, members: list[str], text: str) -> bytes:
    """The file for a team. The blank line after the settings keeps any text as text."""
    return f"lead: {lead}\nmembers: {', '.join(members)}\n\n{text}\n".encode("utf-8")


def read_team(data: bytes) -> tuple[str, list[str], str] | None:
    """A team file: `lead:` and `members:` lines, a blank line, then the instruction verbatim (one final
    newline dropped). None when it is not UTF-8, too large, or has no lead or no members."""
    if len(data) > 4 * FLOW_TEXT_LIMIT + 4096:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    head, _, body = text.partition("\n\n")
    lead, members = "", []
    for line in head.split("\n"):
        key, colon, value = line.partition(":")
        if not colon:
            continue
        key = key.strip().lower()
        if key == "lead":
            lead = value.strip()
        elif key == "members":
            members = [name.strip() for name in value.split(",")]
    if not lead or not members or not all(members):
        return None
    return lead, members, body[:-1] if body.endswith("\n") else body


def _team_dict(team_id: str, lead: str, members: list[str], text: str, hub: str) -> dict:
    return {"id": team_id, "lead": lead, "members": list(members), "text": text,
            "generated": text == team_text(lead, members, hub)}


def _load(root: Path, shape: dict) -> tuple[list[dict], list[str]]:
    """The teams in `teams/` in id order, and warnings. A file that cannot be read, or that breaks a rule
    (an unknown agent, a second team of one lead, a cycle), is skipped with a warning."""
    folder = root / TEAMS_FOLDER
    warnings: list[str] = []
    if folder.is_symlink() or not folder.is_dir():
        return [], warnings
    try:
        files = sorted((path for path in folder.iterdir() if path.suffix == ".md" and FLOW_ID.fullmatch(path.stem)),
                       key=lambda path: path.stem)
    except OSError:
        return [], warnings
    if len(files) > TEAM_FILES_LIMIT:
        warnings.append(f"structure: {len(files)} team files, more than the {TEAM_FILES_LIMIT} read; "
                        f"ignored: {', '.join(path.stem for path in files[TEAM_FILES_LIMIT:][:5])}")
    teams_read: list[dict] = []
    for path in files[:TEAM_FILES_LIMIT]:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            parsed = read_team(path.read_bytes())
        except OSError:
            continue
        if parsed is None:
            warnings.append(f"structure: team {path.stem} ignored: it has no readable lead and members")
            continue
        lead, members, text = parsed
        try:
            lead, members, _ = _refusal(shape, lead, members, teams_read)
        except TeamError as exc:
            warnings.append(f"structure: team {path.stem} ignored: {exc}")
            continue
        teams_read.append(_team_dict(path.stem, lead, members, text, _hub(shape)))
    return teams_read, warnings


def read_teams(structure_dir: Path | None = None) -> tuple[list[dict], list[str]]:
    """(teams, warnings) of a structure folder; no teams without a `teams/` folder."""
    root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
    return _load(root, _agents(root))


def teams(structure_dir: Path | None = None) -> list[dict]:
    """The teams as `{"id", "lead", "members", "text", "generated"}` in id order; empty when there is no
    `teams/` folder or no structure folder."""
    root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
    try:
        return _load(root, _agents(root))[0]
    except TeamError:
        return []


@contextmanager
def _locked(root: Path):
    """The structure folder, open and locked for one writer at a time."""
    with _LOCK:
        try:
            folder = os.open(root, os.O_RDONLY | _DIRECTORY)
        except OSError as exc:
            raise TeamError(f"no structure folder at {root}") from exc
        try:
            if fcntl is not None:
                fcntl.flock(folder, fcntl.LOCK_EX)
            yield folder
        finally:
            os.close(folder)  # closing releases the lock


def _open_teams(root_fd: int, create: bool) -> int | None:
    """The `teams` folder, opened without following a symlink; None when it is missing and not created."""
    try:
        return os.open(TEAMS_FOLDER, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=root_fd)
    except FileNotFoundError:
        if not create:
            return None
    except OSError as exc:
        raise TeamError(f"{TEAMS_FOLDER} in the structure folder is not a plain folder ({exc.strerror}); "
                        "nothing was changed") from exc
    os.mkdir(TEAMS_FOLDER, 0o755, dir_fd=root_fd)
    return _open_teams(root_fd, False)


def _files(teams_fd: int | None) -> set[str]:
    if teams_fd is None:
        return set()
    return {name[:-3] for name in os.listdir(teams_fd) if name.endswith(".md") and FLOW_ID.fullmatch(name[:-3])}


def _regular(teams_fd: int, team_id: str) -> None:
    if not stat.S_ISREG(os.lstat(team_id + ".md", dir_fd=teams_fd).st_mode):
        raise TeamError(f"team {team_id} is not a regular file; it was left unchanged")


def _write(teams_fd: int, name: str, data: bytes, *, new: bool) -> None:
    """Write `data` next to `name` in the teams folder, then link it in (new: never over an existing entry)
    or rename it over the old file (which replaces the entry itself, never a symlink's target)."""
    temporary = f".{name}.{secrets.token_hex(6)}.tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644, dir_fd=teams_fd)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            os.link(temporary, name, src_dir_fd=teams_fd, dst_dir_fd=teams_fd, follow_symlinks=False)
            os.unlink(temporary, dir_fd=teams_fd)
        else:
            os.replace(temporary, name, src_dir_fd=teams_fd, dst_dir_fd=teams_fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=teams_fd)
        except OSError:
            pass
        raise


def _new_id(lead: str, taken: set[str]) -> str:
    """The lead's name as an id. A lead has one team at most, so the id is the same whenever the team is
    added again; only a name that is not a plain id, and collides with another, gets a `-2`."""
    base = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", lead.lower())).strip("-")[:60].strip("-") or "team"
    team_id, number = base, 1
    while team_id in taken:
        number += 1
        team_id = f"{base}-{number}"
    return team_id


def _find(teams_fd: int | None, team_id) -> str:
    if not isinstance(team_id, str):
        raise TeamError("the team id must be text")
    if teams_fd is None or team_id not in _files(teams_fd):
        raise TeamError(f"there is no team {team_id[:80]!r}")
    return team_id


def _team(loaded: list[dict], team_id: str) -> dict:
    team = next((team for team in loaded if team["id"] == team_id), None)
    if team is None:
        raise TeamError(f"team {team_id} cannot be read as a team (see the warnings); it was left unchanged")
    return team


# --- the changes --------------------------------------------------------------------------------

def add(lead, members, text: str | None = None, structure_dir: Path | None = None) -> tuple[dict, list[str]]:
    """Write a new team of `lead` and `members` (a seat stands for its council) with `text`, or an instruction
    made from them when `text` is None or blank. Returns the team and notes. Raises TeamError when refused."""
    if text is not None:
        clean_text(text)
    root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
    with _locked(root) as root_fd:
        shape = _agents(root)
        loaded, _ = _load(root, shape)
        lead, members, notes = _refusal(shape, lead, members, loaded)
        if len(loaded) >= TEAM_FILES_LIMIT:
            raise TeamError(f"the structure folder already holds {len(loaded)} teams, the most it reads is "
                            f"{TEAM_FILES_LIMIT}; remove one first. Nothing was written")
        if text is None or not text.strip():
            text = team_text(lead, members, _hub(shape))
        teams_fd = _open_teams(root_fd, True)
        try:
            team_id = _new_id(lead, {name[:-3] for name in os.listdir(teams_fd) if name.endswith(".md")})
            _write(teams_fd, team_id + ".md", team_file(lead, members, text), new=True)
        finally:
            os.close(teams_fd)
        return _team(_load(root, shape)[0], team_id), notes


def set_text(team_id, text, structure_dir: Path | None = None) -> dict:
    """Replace a team's instruction. Blank text means "write it again from the lead and members"."""
    clean_text(text)
    root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
    with _locked(root) as root_fd:
        shape = _agents(root)
        teams_fd = _open_teams(root_fd, False)
        try:
            found = _find(teams_fd, team_id)
            _regular(teams_fd, found)
            team = _team(_load(root, shape)[0], found)
            if not text.strip():
                text = team_text(team["lead"], team["members"], _hub(shape))
            if text != team["text"]:
                _write(teams_fd, found + ".md", team_file(team["lead"], team["members"], text), new=False)
        finally:
            if teams_fd is not None:
                os.close(teams_fd)
        return _team(_load(root, shape)[0], found)


def set_members(team_id, members, structure_dir: Path | None = None) -> tuple[dict, list[str]]:
    """Replace a team's members. A generated instruction is written again for the new members; an edited one
    is kept. Returns the team and notes."""
    root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
    with _locked(root) as root_fd:
        shape = _agents(root)
        teams_fd = _open_teams(root_fd, False)
        try:
            found = _find(teams_fd, team_id)
            _regular(teams_fd, found)
            loaded, _ = _load(root, shape)
            team = _team(loaded, found)
            others = [other for other in loaded if other["id"] != found]
            lead, members, notes = _refusal(shape, team["lead"], members, others)
            text = team_text(lead, members, _hub(shape)) if team["generated"] else team["text"]
            if members != team["members"] or text != team["text"]:
                _write(teams_fd, found + ".md", team_file(lead, members, text), new=False)
        finally:
            if teams_fd is not None:
                os.close(teams_fd)
        return _team(_load(root, shape)[0], found), notes


def remove(team_id, structure_dir: Path | None = None) -> dict:
    """Delete a team's file, and the `teams` folder once nothing is left in it. Returns the team removed
    (`add(team["lead"], team["members"], team["text"])` puts it back, under the same id, byte for byte); a
    file that cannot be read as a team returns just `{"id": ...}`."""
    root = Path(structure_dir) if structure_dir is not None else STRUCTURE_DIR
    with _locked(root) as root_fd:
        shape = _agents(root)
        teams_fd = _open_teams(root_fd, False)
        try:
            found = _find(teams_fd, team_id)
            _regular(teams_fd, found)
            gone = next((team for team in _load(root, shape)[0] if team["id"] == found), {"id": found})
            os.unlink(found + ".md", dir_fd=teams_fd)
            empty = not os.listdir(teams_fd)
        finally:
            if teams_fd is not None:
                os.close(teams_fd)
        if empty:
            try:
                os.rmdir(TEAMS_FOLDER, dir_fd=root_fd)
            except OSError:
                pass
        return gone


# --- the validator rule -------------------------------------------------------------------------

def _get(item, key, default=None):
    try:
        return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)
    except Exception:
        return default


def _names(value) -> list[str]:
    return [name for name in value if isinstance(name, str)] if isinstance(value, (list, tuple)) else []


def problems(teams, graph, seats: dict | None = None) -> list[str]:
    """Messages for the teams a task graph does not follow; [] when it does. Where two or more nodes are run by
    members of one team, one node run by the team's lead must depend, directly or through other nodes, on each
    of them: otherwise one message names the team, its lead and the member nodes left unreported. A team of
    which none or one member has a node adds no rule. Node statuses do not matter. `graph` is a task graph or
    its JSON as a dict; `seats` ({council lead: seat prefix}) makes a seat's node count for its council.
    Odd input adds no message and never raises."""
    try:
        raw_nodes = _get(graph, "nodes")
        if not isinstance(raw_nodes, (list, tuple)) or not isinstance(teams, (list, tuple)):
            return []
        prefixes = {lead: prefix for lead, prefix in (seats or {}).items()
                    if isinstance(lead, str) and isinstance(prefix, str) and prefix}
        nodes: list[tuple[str, str, list[str]]] = []
        for raw in raw_nodes:
            node_id, agent = _get(raw, "id"), _get(raw, "agent")
            if not isinstance(node_id, str) or not isinstance(agent, str):
                continue
            agent = next((lead for lead, prefix in prefixes.items() if agent != lead and agent.startswith(prefix)), agent)
            nodes.append((node_id, agent, _names(_get(raw, "depends_on"))))
        depends: dict[str, set[str]] = {}
        for node_id, _, after in nodes:
            depends.setdefault(node_id, set()).update(after)

        def ancestors(start: str) -> set[str]:
            seen: set[str] = set()
            stack = list(depends.get(start, ()))
            while stack:
                current = stack.pop()
                if current not in seen:
                    seen.add(current)
                    stack.extend(depends.get(current, ()))
            return seen

        found: list[str] = []
        for team in teams:
            lead, members = _get(team, "lead"), _names(_get(team, "members"))
            if not isinstance(lead, str) or not members:
                continue
            team_id = _get(team, "id")
            label = team_id if isinstance(team_id, str) and team_id else lead
            member_nodes = [node_id for node_id, agent, _ in nodes if agent in members]
            member_nodes = list(dict.fromkeys(member_nodes))
            if len(member_nodes) < 2:
                continue
            best: list[str] | None = None
            for node_id in dict.fromkeys(node_id for node_id, agent, _ in nodes if agent == lead):
                above = ancestors(node_id)
                missing = [mine for mine in member_nodes if mine not in above]
                if best is None or len(missing) < len(best):
                    best = missing
            missing = member_nodes if best is None else best
            if not missing:
                continue
            who = {node_id: agent for node_id, agent, _ in nodes}
            shown = ", ".join(f"{node_id} ({who[node_id]})" for node_id in missing[:8]) \
                + (f" and {len(missing) - 8} more" if len(missing) > 8 else "")
            how = ("no node of " + lead + " follows them" if best is None
                   else f"no single node of {lead} depends on all of them")
            found.append(f"team {label}: the work of {shown} is not reported to {lead}, the team's lead: {how}; "
                         f"a node run by {lead} must depend on each of them")
        return found
    except Exception:  # odd input is the caller's problem, never a crash in the validator
        return []


# --- command ------------------------------------------------------------------------------------

def _read_text(source: str) -> str:
    if source == "-":
        data = sys.stdin.buffer.read(4 * FLOW_TEXT_LIMIT + 1)
    else:
        with open(source, "rb") as stream:
            data = stream.read(4 * FLOW_TEXT_LIMIT + 1)
    if len(data) > 4 * FLOW_TEXT_LIMIT:
        raise TeamError(f"the instruction is longer than {FLOW_TEXT_LIMIT} characters")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TeamError("the instruction is not UTF-8 text") from exc


def _show(team: dict) -> str:
    return (f"{team['id']}: {', '.join(team['members'])} report to {team['lead']}\n"
            + "".join(f"    {line}\n" for line in team["text"].split("\n")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m agentgraph.teams", description="Teams: agents that report to one lead.",
                                     allow_abbrev=False)
    parser.add_argument("--structure", type=Path, metavar="DIR", help="structure folder (default: the orchestration's own)")
    commands = parser.add_subparsers(dest="command", required=True)
    listed = commands.add_parser("list", help="list the teams", allow_abbrev=False)
    listed.add_argument("--json", action="store_true", help='print {"teams": [...]}')
    adding = commands.add_parser("add", help="add a team: LEAD MEMBER [MEMBER ...]", allow_abbrev=False)
    adding.add_argument("lead", metavar="LEAD")
    adding.add_argument("members", nargs="*", metavar="MEMBER")
    adding.add_argument("--text-file", metavar="F", help="the instruction from file F, or - for standard input "
                                                         "(default: one made from the team)")
    text = commands.add_parser("text", help="replace the instruction of team ID", allow_abbrev=False)
    text.add_argument("id", metavar="ID")
    text.add_argument("--text-file", metavar="F", required=True, help="the instruction from file F, or - for standard input")
    setting = commands.add_parser("members", help="replace the members of team ID", allow_abbrev=False)
    setting.add_argument("id", metavar="ID")
    setting.add_argument("members", nargs="*", metavar="MEMBER")
    removing = commands.add_parser("remove", help="remove team ID", allow_abbrev=False)
    removing.add_argument("id", metavar="ID")
    for command in (listed, adding, text, setting, removing):
        command.add_argument("--structure", type=Path, metavar="DIR", default=argparse.SUPPRESS,
                             help="structure folder (default: the orchestration's own)")
    for command in (adding, text, setting, removing):
        command.add_argument("--json", action="store_true", help="print the team and the list as JSON")
    args = parser.parse_args(argv)
    notes: list[str] = []
    result: dict = {}
    try:
        if args.command == "list":
            listing, _ = read_teams(args.structure)
        elif args.command == "add":
            given = _read_text(args.text_file) if args.text_file else None
            result, notes = add(args.lead, args.members, given, args.structure)
            listing = teams(args.structure)
        elif args.command == "text":
            result = set_text(args.id, _read_text(args.text_file), args.structure)
            listing = teams(args.structure)
        elif args.command == "members":
            result, notes = set_members(args.id, args.members, args.structure)
            listing = teams(args.structure)
        else:
            result = remove(args.id, args.structure)
            listing = teams(args.structure)
    except (StructureError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"agentgraph teams: {exc}", file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps({**({"team": result} if result else {}), "teams": listing, **({"notes": notes} if notes else {})}))
        return 0
    for note in notes:
        print(note)
    if args.command == "list":
        print("".join(_show(item) for item in listing) if listing else "no teams", end="" if listing else "\n")
    elif args.command == "add":
        print("added team " + _show(result), end="")
    elif args.command == "text":
        print("changed the instruction of team " + _show(result), end="")
    elif args.command == "members":
        print("changed the members of team " + _show(result), end="")
    else:
        print(f"removed team {args.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
