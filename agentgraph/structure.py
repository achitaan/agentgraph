"""Drawn flows in the structure folder: `agentgraph structure flows|flow`.

A flow is a path through several agents, each agent's output being the next
one's input; a step to an agent already on the path sends the work back there
(`impl-openai -> verifier -> impl-openai` is a check loop). Each flow is one
file in the structure folder's `flows/` folder:

    path: researcher -> impl-claude -> reviewer

    The instruction, free text the user can edit.

`flow add A B [C ...]` writes one (with a plain instruction made from the path
unless a text is given), `flow text ID` replaces its instruction and `flow
remove ID` deletes it (and the `flows/` folder once it is empty). Every write
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
import unicodedata
from contextlib import contextmanager
from pathlib import Path

try:  # POSIX; elsewhere the in-process lock is the only one
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

from .snapshot_json import (FLOW_FILES_LIMIT, FLOW_ID, FLOW_TEXT_LIMIT, FLOWS_FOLDER, STRUCTURE_DIR, flow_refusal,
                            flow_steps, hub_names, is_self_loop, load_structure, read_flow, seat_lead)

FLOW_STEPS_LIMIT = 20
_LOCK = threading.Lock()  # one writer at a time inside a process; flock on the folder across processes
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


class StructureError(Exception):
    """A refused request; the message says why in plain words."""


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

def flow_file(path: list[str], text: str) -> bytes:
    """The file for a flow. The blank line after `path:` keeps any text,
    including one that starts like a settings line, as text."""
    return f"path: {' -> '.join(path)}\n\n{text}\n".encode("utf-8")


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
            folder = os.open(root, os.O_RDONLY | _DIRECTORY)
        except OSError as exc:
            raise StructureError(f"no structure folder at {root}") from exc
        try:
            if fcntl is not None:
                fcntl.flock(folder, fcntl.LOCK_EX)
            yield folder
        finally:
            os.close(folder)  # closing releases the lock


def _open_flows(root_fd: int, create: bool) -> int | None:
    """The `flows` folder, opened without following a symlink; None when it is missing and not created."""
    try:
        return os.open(FLOWS_FOLDER, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=root_fd)
    except FileNotFoundError:
        if not create:
            return None
    except OSError as exc:
        raise StructureError(f"{FLOWS_FOLDER} in the structure folder is not a plain folder ({exc.strerror}); "
                             "nothing was changed") from exc
    os.mkdir(FLOWS_FOLDER, 0o755, dir_fd=root_fd)
    return _open_flows(root_fd, False)


def _files(flows_fd: int | None) -> set[str]:
    """The flow ids in the folder: names of the form `<id>.md`."""
    if flows_fd is None:
        return set()
    return {name[:-3] for name in os.listdir(flows_fd) if name.endswith(".md") and FLOW_ID.fullmatch(name[:-3])}


def _regular(flows_fd: int, flow_id: str) -> None:
    info = os.lstat(flow_id + ".md", dir_fd=flows_fd)
    if not stat.S_ISREG(info.st_mode):
        raise StructureError(f"flow {flow_id} is not a regular file; it was left unchanged")


def _write(flows_fd: int, name: str, data: bytes, *, new: bool) -> None:
    """Write `data` next to `name` in the flows folder, then link it in (new: never over an existing entry)
    or rename it over the old file (which replaces the entry itself, never a symlink's target)."""
    temporary = f".{name}.{secrets.token_hex(6)}.tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644, dir_fd=flows_fd)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if new:
            os.link(temporary, name, src_dir_fd=flows_fd, dst_dir_fd=flows_fd, follow_symlinks=False)
            os.unlink(temporary, dir_fd=flows_fd)
        else:
            os.replace(temporary, name, src_dir_fd=flows_fd, dst_dir_fd=flows_fd)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=flows_fd)
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


def add_flow(names, text: str | None = None, directory: Path | None = None) -> tuple[dict, list[str]]:
    """Write a new flow along `names` (a seat stands for its council) with
    `text`, or an instruction made from the path when `text` is None or blank.
    Returns the flow and notes. Raises StructureError when refused."""
    if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
        raise StructureError("the path must be a list of agent names")
    if len(names) > FLOW_STEPS_LIMIT:
        raise StructureError(f"a flow has at most {FLOW_STEPS_LIMIT} steps")
    if text is not None:
        clean_text(text)
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    notes: list[str] = []
    with _locked(root) as root_fd:
        shape = _shape(root)
        path = []
        for name in names:
            lead = seat_lead(shape, name)
            if lead:
                notes.append(f"{name} is a seat of the council: stored as {lead}")
            path.append(lead or name)
        reason = flow_refusal(shape, path)
        if reason:
            raise StructureError(reason)
        if is_self_loop(path):
            own = next((flow for flow in shape["flows"] if flow["path"] == path), None)
            if own is not None:
                raise StructureError(f"{path[0]} already has a loop on itself (flow {own['id']}); "
                                     "an agent has one at most: select it to change its instruction")
        existing = _open_flows(root_fd, False)
        try:
            count = len(_files(existing))
        finally:
            if existing is not None:
                os.close(existing)
        if count >= FLOW_FILES_LIMIT:
            raise StructureError(f"the structure folder already holds {count} flows, the most it reads is "
                                 f"{FLOW_FILES_LIMIT}; remove one first. Nothing was written")
        if text is None or not text.strip():
            text = flow_text(path, hub_names(shape))
        flows_fd = _open_flows(root_fd, True)
        try:
            flow_id = _new_id(path, {name[:-3] for name in os.listdir(flows_fd) if name.endswith(".md")})
            _write(flows_fd, flow_id + ".md", flow_file(path, text), new=True)
        finally:
            os.close(flows_fd)
        return _flow(root, flow_id), notes


def set_flow_text(flow_id, text, directory: Path | None = None) -> dict:
    """Replace a flow's instruction. A text that is empty or only whitespace means "write it again from the
    path": the instruction made from the path replaces it (the view's way back to the generated text)."""
    clean_text(text)
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    with _locked(root) as root_fd:
        shape = _shape(root)
        flows_fd = _open_flows(root_fd, False)
        if flows_fd is None:
            raise StructureError(f"there is no flow {str(flow_id)[:80]!r}")
        try:
            found = _lookup(flows_fd, flow_id)
            _regular(flows_fd, found)
            flow = next((flow for flow in shape["flows"] if flow["id"] == found), None)
            if flow is None:
                raise StructureError(f"flow {found} cannot be read as a flow (see the snapshot's warnings); "
                                     "it was left unchanged")
            if not text.strip():
                text = flow_text(flow["path"], hub_names(shape))
            if text != flow["text"]:
                _write(flows_fd, found + ".md", flow_file(flow["path"], text), new=False)
        finally:
            os.close(flows_fd)
        return _flow(root, found)


def remove_flow(flow_id, directory: Path | None = None) -> None:
    """Delete a flow's file, and the `flows` folder once nothing is left in it."""
    root = Path(directory) if directory is not None else STRUCTURE_DIR
    with _locked(root) as root_fd:
        _shape(root)
        flows_fd = _open_flows(root_fd, False)
        if flows_fd is None:
            raise StructureError(f"there is no flow {str(flow_id)[:80]!r}")
        try:
            found = _lookup(flows_fd, flow_id)
            _regular(flows_fd, found)
            os.unlink(found + ".md", dir_fd=flows_fd)
            empty = not os.listdir(flows_fd)
        finally:
            os.close(flows_fd)
        if empty:
            try:
                os.rmdir(FLOWS_FOLDER, dir_fd=root_fd)
            except OSError:
                pass


# --- command ------------------------------------------------------------------------------------

def _read_text(source: str) -> str:
    if source == "-":
        data = sys.stdin.buffer.read(4 * FLOW_TEXT_LIMIT + 1)
    else:
        with open(source, "rb") as stream:
            data = stream.read(4 * FLOW_TEXT_LIMIT + 1)
    if len(data) > 4 * FLOW_TEXT_LIMIT:
        raise StructureError(f"the instruction is longer than {FLOW_TEXT_LIMIT} characters")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StructureError("the instruction is not UTF-8 text") from exc


def _show(flow: dict) -> str:
    return f"{flow['id']}: {arrow(flow['path'])}\n" + "".join(f"    {line}\n" for line in flow["text"].split("\n"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph structure", description=__doc__.splitlines()[0])
    parser.add_argument("--structure", type=Path, metavar="DIR", help="structure folder (default: the orchestration's own)")
    commands = parser.add_subparsers(dest="command", required=True)
    listed = commands.add_parser("flows", help="list the drawn flows: what a new task graph must follow")
    listed.add_argument("--json", action="store_true", help='print {"flows": [...]}')
    flow = commands.add_parser("flow", help="add a flow, change its instruction or remove it")
    actions = flow.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="add a flow along AGENT AGENT [AGENT ...]")
    add.add_argument("agents", nargs="+", metavar="AGENT")
    add.add_argument("--text-file", metavar="F", help="the instruction from file F, or - for standard input "
                                                      "(default: one made from the path)")
    text = actions.add_parser("text", help="replace the instruction of flow ID")
    text.add_argument("id", metavar="ID")
    text.add_argument("--text-file", metavar="F", required=True, help="the instruction from file F, or - for standard input")
    remove = actions.add_parser("remove", help="remove flow ID")
    remove.add_argument("id", metavar="ID")
    subparsers = [listed, flow, add, text, remove]
    for command in subparsers:
        command.add_argument("--structure", type=Path, metavar="DIR", default=argparse.SUPPRESS,
                             help="structure folder (default: the orchestration's own)")
    for command in (add, text, remove):
        command.add_argument("--json", action="store_true", help="print the flow and the list as JSON")
    args = parser.parse_args(argv)
    action = getattr(args, "action", None)
    notes: list[str] = []
    result: dict = {}
    try:
        if args.command == "flows":
            flows = flows_of(_shape(args.structure))
        elif action == "add":
            given = _read_text(args.text_file) if args.text_file else None
            result, notes = add_flow(args.agents, given, args.structure)
            flows = drawn_flows(args.structure)
        elif action == "text":
            result = set_flow_text(args.id, _read_text(args.text_file), args.structure)
            flows = drawn_flows(args.structure)
        else:
            remove_flow(args.id, args.structure)
            flows = drawn_flows(args.structure)
    except (StructureError, OSError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"agentgraph structure: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({**({"flow": result} if result else {}), "flows": flows, **({"notes": notes} if notes else {})}))
        return 0
    for note in notes:
        print(note)
    if args.command == "flows":
        print("".join(_show(item) for item in flows) if flows else "no drawn flows", end="" if flows else "\n")
    elif action == "add":
        print("added flow " + _show(result), end="")
    elif action == "text":
        print("changed the instruction of flow " + _show(result), end="")
    else:
        print(f"removed flow {args.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
