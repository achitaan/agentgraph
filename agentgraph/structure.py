"""Drawn flows in the structure folder: `agentgraph structure flows|flow`.

A flow is a path through several agents, each agent's output being the next
one's input; a step to an agent already on the path sends the work back there
(`impl-openai -> verifier -> impl-openai` is a check loop). Each flow is one
file in the structure folder's `flows/` folder:

    path: researcher -> impl-claude -> reviewer

    The instruction, free text the user can edit.

    path: researcher -> impl-claude -> reviewer
    name: review-loop
    command: on

`name:` (optional) is the path's name, 2 to 32 lowercase letters, numbers and
`-`, unique among flows; `command: on` (optional, needs a name) says the path
is also the command `/NAME` (agentgraph.flowcommands writes its file).

`flow add A B [C ...]` writes one (with a plain instruction made from the path
unless a text is given), `flow text ID` replaces its instruction, `flow name ID
[NAME]` names it (no NAME: clears the name), `flow command ID --on|--off` turns
its command on or off, `flow prompt ID --goal-file F` prints the message that
runs it on a goal (agentgraph.flowrun), `flow scaffold P --task T` writes the
task graph that message describes to `.opencode/graph/T.json` (never over a
file) and `flow remove ID` deletes it (and the
`flows/` folder once it is empty; its command file goes with it). Every write
replaces a whole file atomically under a lock, inside `flows/` only, and never
through a symlink. A council seat stands for its council. Exits 0 on success,
1 when the request is refused, 2 on a usage error.
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
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

from . import agentfiles, flowcommands, flowrun, osfs
from .snapshot_json import (FLOW_FILES_LIMIT, FLOW_ID, FLOW_NAME, FLOW_TEXT_LIMIT, FLOWS_FOLDER, STRUCTURE_DIR,
                            flow_fields, flow_refusal, flow_steps, hub_names, is_self_loop, ledger_home, load_structure,
                            read_flow, seat_lead)

FLOW_STEPS_LIMIT = 20
_LOCK = threading.Lock()  # one writer at a time inside a process; flock on the folder across processes
_NOFOLLOW = osfs.O_NOFOLLOW
_DIRECTORY = osfs.O_DIRECTORY


class StructureError(Exception):
    """A refused request; the message says why in plain words."""


class StaleError(StructureError):
    """A write from a view that has not seen a later change (stale-contract.md): nothing was written; `rev` is the
    current one. The endpoints answer it with status 409."""

    def __init__(self, message: str, rev: str):
        super().__init__(message)
        self.rev = rev


def _expect_seen(expect, current: str, what: str) -> None:
    try:
        agentfiles.check_expect(expect, current, what)
    except agentfiles.StaleError as exc:
        raise StaleError(str(exc), exc.rev) from None
    except agentfiles.AgentfilesError as exc:
        raise StructureError(str(exc)) from None


def expect_rev(expect, data: bytes, what: str) -> None:
    """Refuse (StaleError) a write from a view showing `expect` when the thing's bytes `data` now have another rev
    (agentfiles.rev and agentfiles.check_expect, shared by every writer); no check when `expect` is None."""
    _expect_seen(expect, agentfiles.rev(data), what)


def arrow(path: list[str]) -> str:
    return " → ".join(path)


# --- the instruction made from a path -------------------------------------------------------------

def _forward(agents: list[str], then: bool) -> str:
    if len(agents) == 2:
        return f"{'Then hand' if then else 'Hand'} {agents[0]}'s finished work to {agents[1]} as its input."
    steps = ", then ".join(f"{a}'s work goes to {b}" for a, b in zip(agents, agents[1:]))
    return ("Then " + steps if then else steps) + "."


def _loop(agents: list[str], back_to: str, then: bool) -> list[str]:
    """`agents` carry the work forward from its first; the last sends it back to `back_to`."""
    last = agents[-1]
    repeat = f"send {last}'s findings back to {back_to} and repeat until it passes."
    if len(agents) == 1:  # straight after a return: the agent that got the work back sends it on back
        return [f"{'Then, if' if then else 'If'} {last} does not accept it, {repeat}"]
    if len(agents) == 2 and agents[0] == back_to:
        return [f"{'Then ' if then else ''}{last} checks {back_to}'s work.", f"If it does not pass, {repeat}"]
    start = f"{agents[0]}'s work goes to {agents[1]}" + "".join(f", then to {name}" for name in agents[2:]) + "."
    return [("Then " + start if then else start), f"If {last} does not accept it, {repeat}"]


def _sentences(working: list[str]) -> list[str]:
    sentences: list[str] = []
    start = 0
    while start < len(working) - 1:
        back = next((index for index in range(start + 1, len(working)) if working[index] in working[:index]), None)
        if back is None:
            sentences.append(_forward(working[start:], bool(sentences)))
            break
        carried, target = working[start:back], working[back]
        if target in carried:
            lead = carried[:carried.index(target) + 1]
            if len(lead) >= 2:
                sentences.append(_forward(lead, bool(sentences)))
            sentences.extend(_loop(carried[carried.index(target):], target, bool(sentences)))
        else:
            sentences.extend(_loop(carried, target, bool(sentences)))
        start = back
    return sentences


def flow_text(path: list[str], hubs=frozenset()) -> str:
    """A plain instruction for `path`, one to three sentences:

    A → B          Hand A's finished work to B as its input.
    A → B → C      A's work goes to B, then B's work goes to C.
    A → B → A      B checks A's work. If it does not pass, send B's findings back to A and repeat until it passes.
    A → B → C → A  A's work goes to B, then to C. If C does not accept it, send C's findings back to A and repeat
                   until it passes.
    A → A          A checks its own work against the task's evaluation and repeats until it passes.
    The hub first adds "Start with A." in front; the hub last ends the text with ", then report back."
    A longer path that would need more than three sentences gets one general instruction."""
    if is_self_loop(path):
        return f"{path[0]} checks its own work against the task's evaluation and repeats until it passes."
    working, first_return = flow_steps(path, hubs)
    hub_first, hub_last = path[0] in hubs, len(path) > 1 and path[-1] in hubs
    if len(working) < 2:
        sentences = [f"Start with {working[0]}." if working else "Start."]
        hub_last = hub_last or not hub_first
    else:
        sentences = _sentences(working)
        if len(sentences) + hub_first > 3:
            sentences = [f"Pass the work along {arrow(working)}, each agent's output being the next one's input."]
            if first_return >= 0:
                sentences.append("Where a step goes back to an earlier agent, send the findings back there "
                                 "and repeat until it passes.")
        if hub_first:
            sentences.insert(0, f"Start with {working[0]}.")
    if hub_last:
        sentences[-1] = sentences[-1][:-1] + ", then report back."
    return " ".join(sentences)


def clean_text(text) -> str:
    """The user's instruction, verbatim, or StructureError when it is not
    allowed: at most FLOW_TEXT_LIMIT characters, no control character other
    than newline and tab."""
    if not isinstance(text, str):
        raise StructureError("the instruction must be text")
    if len(text) > FLOW_TEXT_LIMIT:
        raise StructureError(f"the instruction is {len(text)} characters long; at most {FLOW_TEXT_LIMIT} are kept")
    bad = next((ch for ch in text if ch not in "\n\t" and unicodedata.category(ch) in ("Cc", "Cs")), None)
    if bad is not None:
        raise StructureError(f"the instruction holds the control character U+{ord(bad):04X}; "
                             "only newline and tab are allowed")
    return text


# --- files --------------------------------------------------------------------------------------

def flow_file(path: list[str], text: str, name: str | None = None, command: bool = False) -> bytes:
    """The file for a flow. The blank line after the settings keeps any text,
    including one that starts like a settings line, as text."""
    head = f"path: {' -> '.join(path)}\n" + (f"name: {name}\n" if name else "") + ("command: on\n" if command else "")
    return f"{head}\n{text}\n".encode("utf-8")


def clean_name(name) -> str:
    """A path's name as given ("" clears it), or StructureError."""
    if not isinstance(name, str):
        raise StructureError("the name must be text")
    if name and not FLOW_NAME.fullmatch(name):
        raise StructureError("a path's name is 2 to 32 lowercase letters, numbers and '-', starting with a letter or "
                             "a number")
    return name


@contextmanager
def _plain():
    """A refusal from the commands folder, said as the structure's own."""
    try:
        yield
    except flowcommands.CommandError as exc:
        raise StructureError(str(exc)) from None


def _taken_name(shape: dict, name: str, flow_id: str | None) -> None:
    other = next((flow for flow in shape["flows"] if flow.get("name") == name and flow["id"] != flow_id), None)
    if other is not None:
        raise StructureError(f"flow {other['id']} already has the name {name}; a name is unique among paths")


def _bytes(flows_fd: int, flow_id: str) -> bytes:
    with os.fdopen(osfs.open(flow_id + ".md", os.O_RDONLY | _NOFOLLOW, dir_fd=flows_fd), "rb") as stream:
        return stream.read()


def _shape(directory: Path | None) -> dict:
    shape = load_structure(directory)
    if shape is None:
        raise StructureError(f"no structure folder at {Path(directory) if directory is not None else STRUCTURE_DIR}")
    return shape


def flows_of(shape: dict | None) -> list[dict]:
    return [{**flow, "source": "drawn"} for flow in (shape["flows"] if shape else [])]


def drawn_flows(directory: Path | None = None) -> list[dict]:
    """The drawn flows as `{"id", "path", "text", "source": "drawn"}`; empty without a folder."""
    return flows_of(load_structure(directory))


@contextmanager
def _locked(root: Path):
    """The structure folder, open and locked for one writer at a time."""
    with _LOCK:
        try:
            folder = osfs.open(root, os.O_RDONLY | _DIRECTORY)
        except OSError as exc:
            raise StructureError(f"no structure folder at {root}") from exc
        try:
            osfs.lock(folder)
            yield folder
        finally:
            osfs.close(folder)  # closing releases the lock


def _open_flows(root_fd: int, create: bool) -> int | None:
    """The `flows` folder, opened without following a symlink; None when it is missing and not created."""
    try:
        return osfs.open(FLOWS_FOLDER, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=root_fd)
    except FileNotFoundError:
        if not create:
            return None
    except OSError as exc:
        raise StructureError(f"{FLOWS_FOLDER} in the structure folder is not a plain folder ({exc.strerror}); "
                             "nothing was changed") from exc
    osfs.mkdir(FLOWS_FOLDER, 0o755, dir_fd=root_fd)
    return _open_flows(root_fd, False)


def _files(flows_fd: int | None) -> set[str]:
    """The flow ids in the folder: names of the form `<id>.md`."""
    if flows_fd is None:
        return set()
    return {name[:-3] for name in osfs.listdir(flows_fd) if name.endswith(".md") and FLOW_ID.fullmatch(name[:-3])}


def _regular(flows_fd: int, flow_id: str) -> None:
    info = osfs.lstat(flow_id + ".md", dir_fd=flows_fd)
    if not stat.S_ISREG(info.st_mode):
        raise StructureError(f"flow {flow_id} is not a regular file; it was left unchanged")


def _write(flows_fd: int, name: str, data: bytes, *, new: bool) -> None:
    """Write `data` next to `name` in the flows folder, then link it in (new: never over an existing entry)
    or rename it over the old file (which replaces the entry itself, never a symlink's target)."""
    temporary = f".{name}.{secrets.token_hex(6)}.tmp"
    handle = osfs.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644, dir_fd=flows_fd)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            osfs.link(temporary, name, src_dir_fd=flows_fd, dst_dir_fd=flows_fd, follow_symlinks=False)
            osfs.unlink(temporary, dir_fd=flows_fd)
        else:
            osfs.replace(temporary, name, src_dir_fd=flows_fd, dst_dir_fd=flows_fd)
    except BaseException:
        try:
            osfs.unlink(temporary, dir_fd=flows_fd)
        except OSError:
            pass
        raise


def _new_id(path: list[str], taken: set[str]) -> str:
    base = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", "-".join(path).lower())).strip("-")[:60].strip("-") or "flow"
    flow_id, number = base, 1
    while flow_id in taken:
        number += 1
        flow_id = f"{base}-{number}"
    return flow_id


def _lookup(flows_fd: int | None, flow_id) -> str:
    """An id from a request, found among the files; it never builds a path itself."""
    if not isinstance(flow_id, str):
        raise StructureError("the flow id must be text")
    found = next((name for name in _files(flows_fd) if name == flow_id), None)
    if found is None:
        raise StructureError(f"there is no flow {flow_id[:80]!r}")
    return found


def _flow(root: Path, flow_id: str) -> dict:
    flow = next((flow for flow in drawn_flows(root) if flow["id"] == flow_id), None)
    if flow is None:  # written but not readable as a flow: say so instead of failing without a word
        raise StructureError(f"flow {flow_id} was written but cannot be read back (see the snapshot's warnings)")
    return flow


def add_flow(names, text: str | None = None, directory: Path | None = None, *, name: str | None = None,
             command: bool = False, commands_dir: Path | None = None) -> tuple[dict, list[str]]:
    """Write a new flow along `names` (a seat stands for its council) with
    `text`, or an instruction made from the path when `text` is None or blank;
    `name` names it and `command` (needs a name) writes its command file too
    (the way back from a removal). Returns the flow and notes. Raises
    StructureError when refused."""
    if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
        raise StructureError("the path must be a list of agent names")
    if len(names) > FLOW_STEPS_LIMIT:
        raise StructureError(f"a flow has at most {FLOW_STEPS_LIMIT} steps")
    if text is not None:
        clean_text(text)
    name = clean_name(name if name is not None else "") or None
    if not isinstance(command, bool):
        raise StructureError("command must be true or false")
    if command and name is None:
        raise StructureError("a path needs a name before it can be a command: the command is /NAME")
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    notes: list[str] = []
    with _locked(root) as root_fd, _plain():
        shape = _shape(root)
        path = []
        for agent in names:
            lead = seat_lead(shape, agent)
            if lead:
                notes.append(f"{agent} is a seat of the council: stored as {lead}")
            path.append(lead or agent)
        reason = flow_refusal(shape, path)
        if reason:
            raise StructureError(reason)
        if is_self_loop(path):
            own = next((flow for flow in shape["flows"] if flow["path"] == path), None)
            if own is not None:
                raise StructureError(f"{path[0]} already has a loop on itself (flow {own['id']}); "
                                     "an agent has one at most: select it to change its instruction")
        if name is not None:
            _taken_name(shape, name, None)
        existing = _open_flows(root_fd, False)
        try:
            count = len(_files(existing))
        finally:
            if existing is not None:
                osfs.close(existing)
        if count >= FLOW_FILES_LIMIT:
            raise StructureError(f"the structure folder already holds {count} flows, the most it reads is "
                                 f"{FLOW_FILES_LIMIT}; remove one first. Nothing was written")
        if text is None or not text.strip():
            text = flow_text(path, hub_names(shape))
        flows_fd = _open_flows(root_fd, True)
        try:
            flow_id = _new_id(path, {entry[:-3] for entry in osfs.listdir(flows_fd) if entry.endswith(".md")})
            data = flow_file(path, text, name, command)
            if not command:
                _write(flows_fd, flow_id + ".md", data, new=True)
            else:
                wanted = flowcommands.render({"id": flow_id, "path": path, "text": text, "name": name},
                                             hub_names(shape))
                with flowcommands.Folder(commands_dir, create=True) as folder:
                    there = flowcommands.check_free(folder, name, wanted)
                    _write(flows_fd, flow_id + ".md", data, new=True)
                    try:
                        if there is None:
                            folder.write(name, wanted, None)
                    except BaseException:
                        osfs.unlink(flow_id + ".md", dir_fd=flows_fd)
                        raise
        finally:
            osfs.close(flows_fd)
        return _flow(root, flow_id), notes


def _open_flow(root_fd: int, shape: dict, flow_id, expect=None):
    """(the flows folder's fd, the id as found, the flow as read) for a change to an existing flow; refused
    (StaleError) when the view showed it as `expect` and its rev is now another."""
    flows_fd = _open_flows(root_fd, False)
    if flows_fd is None:
        raise StructureError(f"there is no flow {str(flow_id)[:80]!r}")
    try:
        found = _lookup(flows_fd, flow_id)
        _regular(flows_fd, found)
        expect_rev(expect, _bytes(flows_fd, found), f"flow {found}")
        flow = next((flow for flow in shape["flows"] if flow["id"] == found), None)
        if flow is None:
            raise StructureError(f"flow {found} cannot be read as a flow (see the snapshot's warnings); "
                                 "it was left unchanged")
    except BaseException:
        osfs.close(flows_fd)
        raise
    return flows_fd, found, flow


def set_flow_text(flow_id, text, directory: Path | None = None, *, commands_dir: Path | None = None,
                  expect: str | None = None) -> dict:
    """Replace a flow's instruction. A text that is empty or only whitespace means "write it again from the
    path": the instruction made from the path replaces it (the view's way back to the generated text). A flow whose
    command is on gets its command file written again with the new instruction."""
    clean_text(text)
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    with _locked(root) as root_fd, _plain():
        shape = _shape(root)
        flows_fd, found, flow = _open_flow(root_fd, shape, flow_id, expect)
        try:
            if not text.strip():
                text = flow_text(flow["path"], hub_names(shape))
            if text != flow["text"]:
                data = flow_file(flow["path"], text, flow["name"], bool(flow["command"]))
                if not flow["command"]:
                    _write(flows_fd, found + ".md", data, new=False)
                else:
                    hubs = hub_names(shape)
                    wanted = flowcommands.render({**flow, "text": text}, hubs)
                    with flowcommands.Folder(commands_dir, create=True) as folder:
                        there = flowcommands.check_unchanged(folder, flow["command"], flowcommands.written(flow, hubs))
                        before = _bytes(flows_fd, found)
                        _write(flows_fd, found + ".md", data, new=False)
                        try:
                            folder.write(flow["command"], wanted, there)
                        except BaseException:
                            _write(flows_fd, found + ".md", before, new=False)
                            raise
        finally:
            osfs.close(flows_fd)
        return _flow(root, found)


def set_flow_name(flow_id, name, directory: Path | None = None, *, commands_dir: Path | None = None,
                  expect: str | None = None) -> dict:
    """Name a flow (`name` "" clears it). A flow whose command is on gets its command renamed with it: the new
    file is written, then the flow, then the old file removed."""
    name = clean_name(name) or None
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    with _locked(root) as root_fd, _plain():
        shape = _shape(root)
        flows_fd, found, flow = _open_flow(root_fd, shape, flow_id, expect)
        try:
            if name == flow["name"]:
                return _flow(root, found)
            if name is not None:
                _taken_name(shape, name, found)
            data = flow_file(flow["path"], flow["text"], name, bool(flow["command"]))
            if not flow["command"]:
                _write(flows_fd, found + ".md", data, new=False)
                return _flow(root, found)
            if name is None:
                raise StructureError(f"flow {found} has the command /{flow['command']}; turn it off before taking "
                                     "its name away")
            hubs = hub_names(shape)
            wanted = flowcommands.render({**flow, "name": name}, hubs)
            with flowcommands.Folder(commands_dir, create=True) as folder:
                there = flowcommands.check_free(folder, name, wanted)
                old = flowcommands.check_unchanged(folder, flow["command"], flowcommands.written(flow, hubs),
                                                   leave_foreign=True)
                if there is None:
                    folder.write(name, wanted, None)
                try:
                    _write(flows_fd, found + ".md", data, new=False)
                except BaseException:
                    if there is None:
                        folder.write(name, None, wanted)
                    raise
                if old is not None:
                    folder.write(flow["command"], None, old)
        finally:
            osfs.close(flows_fd)
        return _flow(root, found)


def set_flow_command(flow_id, on, directory: Path | None = None, *, commands_dir: Path | None = None,
                     expect: str | None = None) -> dict:
    """Turn a flow's command on (write `NAME.md` into the commands folder, then `command: on` into the flow) or
    off (the flow first, then the file). The flow needs a name; turning on a command that is on writes a missing
    file again."""
    if not isinstance(on, bool):
        raise StructureError("on must be true or false")
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    with _locked(root) as root_fd, _plain():
        shape = _shape(root)
        flows_fd, found, flow = _open_flow(root_fd, shape, flow_id, expect)
        try:
            hubs = hub_names(shape)
            if on:
                if not flow["name"]:
                    raise StructureError(f"flow {found} has no name; give it one first (agentgraph flow name {found} "
                                         "NAME): the command is /NAME")
                wanted = flowcommands.render(flow, hubs)
                with flowcommands.Folder(commands_dir, create=True) as folder:
                    # A file this flow's command was, written by an earlier version, is written in the current shape.
                    there = flowcommands.check_free(folder, flow["name"],
                                                    flowcommands.written(flow, hubs) if flow["command"] else wanted)
                    if there != wanted:
                        folder.write(flow["name"], wanted, there)
                    if not flow["command"]:
                        try:
                            _write(flows_fd, found + ".md", flow_file(flow["path"], flow["text"], flow["name"], True),
                                   new=False)
                        except BaseException:
                            if there is None:
                                folder.write(flow["name"], None, wanted)
                            raise
            elif flow["command"]:
                with flowcommands.Folder(commands_dir, create=False) as folder:
                    old = flowcommands.check_unchanged(folder, flow["command"], flowcommands.written(flow, hubs),
                                                       leave_foreign=True)
                    _write(flows_fd, found + ".md", flow_file(flow["path"], flow["text"], flow["name"], False),
                           new=False)
                    if old is not None:
                        folder.write(flow["command"], None, old)
        finally:
            osfs.close(flows_fd)
        return _flow(root, found)


def _unreadable_command(shape: dict, flow_id: str, data: bytes) -> tuple[str, tuple[bytes, ...]] | None:
    """For a flow file that cannot be read as a flow (an agent on its path is gone, say): (its command's name, the
    bytes its command file had when it was written, by this version or an earlier one: `flowcommands.written`), when its lines say its command is on, its name is valid and
    no readable flow holds that name; else None. Its command file goes with it only while it is exactly those bytes."""
    fields = flow_fields(data)
    if fields is None or not fields["command"] or not FLOW_NAME.fullmatch(fields["name"]):
        return None
    if any(flow.get("name") == fields["name"] for flow in shape["flows"]):
        return None  # the name is another path's now: its command file is that path's
    path = [seat_lead(shape, name) or name for name in fields["path"]]
    try:
        wanted = flowcommands.written({"id": flow_id, "path": path, "text": fields["text"], "name": fields["name"]},
                                      hub_names(shape))
    except flowcommands.CommandError:
        return None
    return fields["name"], wanted


def remove_flow(flow_id, directory: Path | None = None, *, commands_dir: Path | None = None,
                expect: str | None = None) -> dict | None:
    """Delete a flow's file, and the `flows` folder once nothing is left in it; a flow whose command is on loses
    its command file first. A file that cannot be read as a flow (an agent on its path is gone, say) is removed the
    same way: its command file goes too while it is exactly what was written for it. Returns the flow as it was
    ({"id", "path", "text", "name", "command"}; None for a file that could not be read as a flow), so `add_flow`
    can put it back. Given `expect`, refused (StaleError) when the file's rev is another. A saved loop of the flow
    (`<structure>/.loops/<id>.json`, agentgraph.looprun) stays: the undo record is this dict and the add that undoes
    it cannot carry another file, and `add_flow` gives a self-loop the same id again, so the loop is back with it. Until then
    the page lists it among the orphans."""
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    with _locked(root) as root_fd, _plain():
        shape = _shape(root)
        flows_fd = _open_flows(root_fd, False)
        if flows_fd is None:
            raise StructureError(f"there is no flow {str(flow_id)[:80]!r}")
        try:
            found = _lookup(flows_fd, flow_id)
            _regular(flows_fd, found)
            data = _bytes(flows_fd, found)
            expect_rev(expect, data, f"flow {found}")
            flow = next((flow for flow in shape["flows"] if flow["id"] == found), None)
            if flow is not None and flow["command"]:
                with flowcommands.Folder(commands_dir, create=False) as folder:
                    old = flowcommands.check_unchanged(folder, flow["command"],
                                                       flowcommands.written(flow, hub_names(shape)), leave_foreign=True)
                    if old is not None:
                        folder.write(flow["command"], None, old)
            elif flow is None and (command := _unreadable_command(shape, found, data)) is not None:
                with flowcommands.Folder(commands_dir, create=False) as folder:
                    there = folder.read(command[0])
                    if there is not None and there in command[1]:
                        folder.write(command[0], None, there)
            osfs.unlink(found + ".md", dir_fd=flows_fd)
            empty = not osfs.listdir(flows_fd)
        finally:
            osfs.close(flows_fd)
        if empty:
            try:
                osfs.rmdir(FLOWS_FOLDER, dir_fd=root_fd)
            except OSError:
                pass
        return flow


def flow_prompt(flow_id, goal, directory: Path | None = None, *, expect: str | None = None,
                name: str | None = None) -> str:
    """The run message for a flow and a goal (read-only). Given `expect`, refused (StaleError) when the flow's rev
    is another: the view would run a path it is not showing. `name`: the path of that name instead of `flow_id`
    (None): what a path command's body says to run, since a flow's name is its command's."""
    try:
        flowrun.clean_goal(goal)
    except flowrun.RunError as exc:
        raise StructureError(str(exc)) from None
    shape = _shape(Path(directory) if directory is not None else STRUCTURE_DIR)
    if name is not None:
        if flow_id is not None or not isinstance(name, str):
            raise StructureError("give a flow id or a path's name, not both")
        flow = next((flow for flow in shape["flows"] if flow["name"] == name), None)
        if flow is None:
            raise StructureError(f"there is no path named {name[:80]!r}")
        flow_id = flow["id"]
    elif not isinstance(flow_id, str):
        raise StructureError("the flow id must be text")
    else:
        flow = next((flow for flow in shape["flows"] if flow["id"] == flow_id), None)
    if flow is None:
        raise StructureError(f"there is no flow {flow_id[:80]!r}")
    if expect is not None:
        _expect_seen(expect, flow["rev"], f"flow {flow_id}")
    hubs = hub_names(shape)
    # The other drawn paths `check` will hold a run of this one to, and the check nodes that keep its graph finite,
    # named in the message (flowrun.binding); then the groups whose rule that graph would break (binding_groups).
    others, checks, nodes = flowrun._closure(flow, shape["flows"], hubs, shape["seats"])
    groups, _ = flowrun.binding_groups(nodes, shape.get("teams", []), shape["seats"])
    return flowrun.run_message(flow, goal, hubs, others=others, checks=checks, groups=groups)


def flow_scaffold(path_ref, task, directory: Path | None = None, *, graph_dir: Path | None = None,
                  now: float | None = None) -> tuple[Path, dict]:
    """Write the task graph the run message's words make for the drawn path `path_ref` (a flow id, else a path's
    name) to `<graph_dir>/<task>.json` (flowrun.scaffold: every binding path, check node and group included; goals,
    done_when and the objective's text left empty). `graph_dir` None: the nearest `.opencode/graph` from the working
    directory, else `./.opencode/graph` (made). Never over an existing file. Returns (the path, the graph)."""
    from . import names, sources  # sources is heavy; read lazily
    if not isinstance(task, str) or not names.FLOW_ID.fullmatch(task):
        raise StructureError("the task name is 1 to 80 lowercase letters, numbers and '-', starting with a letter or a "
                             "number: the graph file is <task>.json")
    if not isinstance(path_ref, str):
        raise StructureError("give a flow id or a path's name")
    shape = _shape(Path(directory) if directory is not None else STRUCTURE_DIR)
    flow = next((flow for flow in shape["flows"] if flow["id"] == path_ref), None) \
        or next((flow for flow in shape["flows"] if flow["name"] == path_ref), None)
    if flow is None:
        raise StructureError(f"there is no flow or path named {path_ref[:80]!r}")
    hubs = hub_names(shape)
    graph = flowrun.scaffold(flow, shape["flows"], task, hubs, shape["seats"], shape.get("teams", []), now)
    if graph_dir is None:
        graph_dir = sources.find_graph_dir(Path.cwd()) or Path.cwd() / ".opencode" / "graph"
    graph_dir = Path(graph_dir)
    graph_dir.mkdir(parents=True, exist_ok=True)
    target = graph_dir / f"{task}.json"
    temporary = graph_dir / f".{task}.{secrets.token_hex(6)}.tmp"
    fd = osfs.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(graph, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            osfs.link(temporary, target, follow_symlinks=False)  # never over a file that is there
        except FileExistsError:
            raise StructureError(f"{target} already exists; it was left unchanged (pick another --task)") from None
    finally:
        try:
            osfs.unlink(temporary)
        except OSError:
            pass
    return target, graph


def flows_with_stats(shape: dict | None, *, ledger: Path | None, graph_paths=(), db_path: Path | None = None) -> list[dict]:
    """The drawn flows, each with its `stats` (agentgraph.flowrun)."""
    flows = flows_of(shape)
    stats = flowrun.flow_stats(flows, ledger_home=ledger, graph_paths=graph_paths, db_path=db_path,
                               hubs=hub_names(shape), seats=shape["seats"] if shape else None)
    return [{**flow, "stats": stats.get(flow["id"])} for flow in flows]


# --- command ------------------------------------------------------------------------------------

def _read_text(source: str, limit: int = FLOW_TEXT_LIMIT, what: str = "the instruction") -> str:
    if source == "-":
        data = sys.stdin.buffer.read(4 * limit + 1)
    else:
        with open(source, "rb") as stream:
            data = stream.read(4 * limit + 1)
    if len(data) > 4 * limit:
        raise StructureError(f"{what} is longer than {limit} characters")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StructureError(f"{what} is not UTF-8 text") from exc


def _show(flow: dict) -> str:
    named = (f"  name: {flow['name']}" if flow.get("name") else "") + (f"  command: /{flow['command']}"
                                                                      if flow.get("command") else "")
    return (f"{flow['id']}: {arrow(flow['path'])}{named}\n"
            + "".join(f"    {line}\n" for line in flow["text"].split("\n")))


def _money(value) -> str:
    return "unknown" if value is None else f"${value:.2f}"


def _show_stats(stats: dict | None) -> str:
    if stats is None:
        return "    never ran\n"
    if stats["runs"] is None:
        return ("    runs unknown: a task graph cannot show this path (a loop of one agent on itself, or one agent and "
                "the hub)\n")
    rounds, cost = stats["rounds"], stats["cost"]
    last = time.strftime("%Y-%m-%d %H:%M", time.localtime(stats["last"])) if stats["last"] is not None else "unknown"
    return (f"    runs {stats['runs']}, passed {stats['passed']}, rounds "
            + ("unknown" if rounds["avg"] is None else f"avg {rounds['avg']:g}, max {rounds['max']}")
            + f", cost {_money(cost['total'])} in all, {_money(cost['avg'])} a run, last {last}\n")


def _list_teams(directory: Path | None, as_json: bool) -> int:
    """`structure teams`: the groups as `agentgraph team list` prints them."""
    from . import teams  # teams imports this module
    try:
        found, warnings = teams.read_teams(directory)
    except (StructureError, OSError) as exc:
        if as_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"agentgraph structure: {exc}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps({"teams": found, **({"warnings": warnings} if warnings else {})}))
        return 0
    for warning in warnings:
        print(warning, file=sys.stderr)
    print("".join(teams._show(item) for item in found) if found else "no groups", end="" if found else "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph structure", description=__doc__.splitlines()[0])
    parser.add_argument("--structure", type=Path, metavar="DIR", help="structure folder (default: the orchestration's own)")
    commands = parser.add_subparsers(dest="command", required=True)
    listed = commands.add_parser("flows", help="list the drawn flows: what a new task graph must follow")
    listed.add_argument("--json", action="store_true", help='print {"flows": [...]}')
    listed.add_argument("--stats", action="store_true", help="with each flow, how its recorded runs went "
                                                             "(agentgraph.flowrun says where each number comes from)")
    listed.add_argument("--ledger", type=Path, metavar="DIR", help="the run ledger's folder (default: the active "
                                                                   "preset's memory folder)")
    listed.add_argument("--graphs", type=Path, metavar="DIR", action="append", default=[],
                        help="a folder of graph files to count besides the ledger's (default: none)")
    listed.add_argument("--db", type=Path, metavar="PATH", help="OpenCode's sessions database, for costs "
                                                                "(default: the user's)")
    grouped = commands.add_parser("teams", help="list the groups: agents that report to one lead (agentgraph team ...)")
    grouped.add_argument("--json", action="store_true", help='print {"teams": [...]}')
    flow = commands.add_parser("flow", help="add a flow, change its instruction, name or command, or remove it")
    actions = flow.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="add a flow along AGENT AGENT [AGENT ...]")
    add.add_argument("agents", nargs="+", metavar="AGENT")
    add.add_argument("--text-file", metavar="F", help="the instruction from file F, or - for standard input "
                                                      "(default: one made from the path)")
    add.add_argument("--name", metavar="NAME", default="", help="the path's name")
    add.add_argument("--command", action="store_true", help="make the path the command /NAME too (needs --name)")
    text = actions.add_parser("text", help="replace the instruction of flow ID")
    text.add_argument("id", metavar="ID")
    text.add_argument("--text-file", metavar="F", required=True, help="the instruction from file F, or - for standard input")
    name = actions.add_parser("name", help="name flow ID (no NAME: take its name away)")
    name.add_argument("id", metavar="ID")
    name.add_argument("name", metavar="NAME", nargs="?", default="")
    command = actions.add_parser("command", help="make flow ID the command /NAME, or stop it being one")
    command.add_argument("id", metavar="ID")
    switch = command.add_mutually_exclusive_group(required=True)
    switch.add_argument("--on", dest="on", action="store_true", help="write NAME.md into the commands folder")
    switch.add_argument("--off", dest="on", action="store_false", help="remove it")
    prompt = actions.add_parser("prompt", help="print the message that runs flow ID (or the path named NAME) on a goal")
    prompt.add_argument("id", metavar="ID", nargs="?")
    prompt.add_argument("--name", metavar="NAME", help="the path named NAME instead of flow ID (what a path command "
                        "says to run)")
    prompt.add_argument("--goal-file", metavar="F", required=True, help="the goal from file F, or - for standard input")
    remove = actions.add_parser("remove", help="remove flow ID (and its command file)")
    remove.add_argument("id", metavar="ID")
    scaffold = actions.add_parser("scaffold", help="write the task graph for the path P (a flow id or a path's name) "
                                                   "to .opencode/graph/T.json, goals and checks left for you to fill")
    scaffold.add_argument("id", metavar="P")
    scaffold.add_argument("--task", metavar="T", required=True, help="the task's name: the file is T.json")
    scaffold.add_argument("--dir", type=Path, metavar="DIR", help="the graph folder (default: the nearest "
                                                                  ".opencode/graph, else ./.opencode/graph)")
    subparsers = [listed, grouped, flow, add, text, name, command, prompt, remove, scaffold]
    for parser_ in subparsers:
        parser_.add_argument("--structure", type=Path, metavar="DIR", default=argparse.SUPPRESS,
                             help="structure folder (default: the orchestration's own)")
    for parser_ in (add, text, name, command, prompt, remove, scaffold):
        parser_.add_argument("--json", action="store_true", help="print the answer as JSON")
    for parser_ in (add, text, name, command, remove):
        parser_.add_argument("--commands", type=Path, metavar="DIR",
                             help="OpenCode's commands folder (default: ~/.config/opencode/commands)")
    for parser_ in (text, name, command, prompt, remove):
        parser_.add_argument("--expect", metavar="REV", help="refuse, changing nothing, unless the flow's rev is REV "
                                                             "(the rev the caller last read)")
    args = parser.parse_args(argv)
    action = getattr(args, "action", None)
    notes: list[str] = []
    result: dict = {}
    extra: dict = {}
    if args.command == "teams":
        return _list_teams(args.structure, args.json)
    commands_dir = getattr(args, "commands", None)
    expect = getattr(args, "expect", None)
    unreadable: list[dict] = []
    try:
        if args.command == "flows":
            shape = _shape(args.structure)
            if args.stats:
                from .sources import DB_PATH
                ledger = args.ledger if args.ledger is not None else ledger_home()
                graph_paths = [path for folder in args.graphs for path in _graph_files(folder)]
                flows = flows_with_stats(shape, ledger=ledger, graph_paths=graph_paths,
                                         db_path=args.db if args.db is not None else DB_PATH)
            else:
                flows = flows_of(shape)
            unreadable = [dict(item) for item in shape.get("unreadable_flows", [])]
            if shape.get("warnings"):
                extra["warnings"] = shape["warnings"]
        elif action == "add":
            given = _read_text(args.text_file) if args.text_file else None
            result, notes = add_flow(args.agents, given, args.structure, name=args.name or None, command=args.command,
                                     commands_dir=commands_dir)
            flows = drawn_flows(args.structure)
        elif action == "text":
            result = set_flow_text(args.id, _read_text(args.text_file), args.structure, commands_dir=commands_dir,
                                   expect=expect)
            flows = drawn_flows(args.structure)
        elif action == "name":
            result = set_flow_name(args.id, args.name, args.structure, commands_dir=commands_dir, expect=expect)
            flows = drawn_flows(args.structure)
        elif action == "command":
            result = set_flow_command(args.id, args.on, args.structure, commands_dir=commands_dir, expect=expect)
            flows = drawn_flows(args.structure)
            if result["name"]:
                extra["applies"] = flowcommands.applies(result["name"], args.on)
            extra["live"] = flowcommands.LIVE
        elif action == "scaffold":
            target, graph = flow_scaffold(args.id, args.task, args.structure, graph_dir=args.dir)
            if args.json:
                print(json.dumps({"path": str(target), "graph": graph}))
            else:
                print(f"wrote {target}: {len(graph['nodes'])} nodes; fill the empty goal, done_when and text, then "
                      f"run agentgraph check --task {args.task}")
            return 0
        elif action == "prompt":
            if (args.id is None) == (args.name is None):
                raise StructureError("give a flow id or --name NAME, one of them")
            text_ = flow_prompt(args.id, _read_text(args.goal_file, flowrun.GOAL_LIMIT, "the goal"), args.structure,
                                expect=expect, name=args.name)
            if args.json:
                print(json.dumps({"text": text_}))
            else:
                print(text_, end="")
            return 0
        else:
            remove_flow(args.id, args.structure, commands_dir=commands_dir, expect=expect)
            flows = drawn_flows(args.structure)
    except (StructureError, OSError) as exc:
        current = getattr(exc, "rev", None)
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc), **({"rev": current} if current is not None else {})}))
        else:
            print(f"agentgraph structure: {exc}" + (f" (rev now {current})" if current is not None else ""),
                  file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({**({"flow": result} if result else {}), "flows": flows, **extra,
                          **({"unreadable": unreadable} if unreadable else {}), **({"notes": notes} if notes else {})}))
        return 0
    for note in notes:
        print(note)
    for warning in extra.get("warnings", []):
        print(warning, file=sys.stderr)
    if args.command == "flows":
        if not flows and not unreadable:
            print("no drawn flows")
        for item in flows:
            print(_show(item), end="")
            if args.stats:
                print(_show_stats(item["stats"]), end="")
        for item in unreadable:
            print(f"{item['id']}: cannot be read as a flow ({item['reason']}); `agentgraph flow remove {item['id']}` "
                  "removes it")
    elif action == "add":
        print("added flow " + _show(result), end="")
    elif action == "text":
        print("changed the instruction of flow " + _show(result), end="")
    elif action == "name":
        print((f"named flow {result['id']} {result['name']}" if result["name"] else f"took the name of flow {result['id']} away"))
    elif action == "command":
        print(f"flow {result['id']} is the command /{result['command']}" if result["command"]
              else f"flow {result['id']} is not a command", end=f": {extra['applies']}\n" if "applies" in extra else "\n")
    else:
        print(f"removed flow {args.id}")
    return 0


def _graph_files(folder: Path) -> list[Path]:
    from .sources import graph_files
    try:
        return list(graph_files(folder)[0])
    except OSError:
        return []


if __name__ == "__main__":
    raise SystemExit(main())
