"""A step's outputs, opened and edited by hand: `agentgraph output read|write|restore`.

A node of a task graph lists `artifacts` ({id, path, revision}, files under `.opencode/artifacts/<task>/`); another
node reads one by naming it in `inputs`. In the user's words a node is a step and an artifact is its output.

- **Shown.** `annotate` gives every output of the snapshot's graph its `rev` (agentfiles.rev of its path, revision
  and bytes: 16 lowercase hex digits), `size` (bytes) and `editable`, with a plain reason (`why`) when it is not:
  a missing file, not text, larger than TEXT_LIMIT, outside the task's artifacts folder or reached through a link.
  No content.
- **Read.** `read_output` gives an output's text and `rev`.
- **Write.** `write_output` takes the new text and an optional `expect`; a stale one changes nothing
  (agentfiles.StaleError: 409 with the current `rev`). Otherwise, as one undoable step: the text is written as the
  output's next revision, `r<N+1>` beside `r<N>` (the earlier file is never rewritten, as GRAPH_FORMAT.md asks), the
  graph records the new path and revision (so every node that read revision N is stale by the existing rule,
  contract.stale_inputs) with a `changed` event ("edited by hand"), an undo record is saved, and a line is
  appended to the edit log (EDIT_LOG in the memory home; no content).
- **Undo.** `restore_output` takes the record back: the new file goes and the graph records the earlier revision
  again, byte for byte when nothing else changed the graph since. It is refused, naming what stands in the way,
  when a later run replaced the output, or the file was changed after the edit.

The graph file is shared with a running orchestrator, which may rewrite it whole at any moment. A write merges only
its own fields (the artifact's `path` and `revision`, one event, `updated`) into the graph as it is on disk, and
puts the result in place with an atomic exchange (`renameatx_np` RENAME_SWAP on macOS, `renameat2`
RENAME_EXCHANGE on Linux): the file it displaced must be the one it merged into, or that file is put back and the
merge is done again from it. An orchestrator's change is therefore never lost; one that changes the edited
output's own record refuses the edit. When an orchestrator later rewrites the graph from its own older copy,
dropping the edit's record, `check` reports it from the edit log (`lost_edits`).

Only files inside the task's own artifacts folder (`<project>/.opencode/artifacts/<task>/`), reached without
following any link, are read or written; every check is made under the graph folder's lock. Nothing is ever run.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, OrderedDict
import copy
import ctypes
import difflib
import errno
import fcntl
import hashlib
import json
import os
from datetime import date
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
import threading
import time
import unicodedata

from . import agentfiles, skillset
from .handedits import AGENT_NAME, OUTPUT_NAME
from .contract import _contains, _path_parts
from .sources import MAX_GRAPH_BYTES, ORCHESTRATION_GRAPH_DIR, find_graph_dir, graph_files, read_graph_text

TEXT_LIMIT = 256 * 1024  # bytes of UTF-8: the largest output read or written here
TEXT_LIMIT_WORDS = "256 KiB"
EDIT_LOG = "edits.jsonl"  # in the memory home, beside runs.jsonl and feedback.jsonl
EDIT_LOG_READ = 8 * 1024 * 1024  # the most of the edit log `check` reads (its newest lines)
ANNOTATE_LIMIT = 400  # outputs read for one snapshot; the rest are shown without a rev
SEEN_LIMIT = 2 * ANNOTATE_LIMIT  # outputs whose rev the snapshot remembers (by file identity), newest kept
DIFF_LINES = 200  # the most lines (each side) counted by a diff; above it the lines are counted as a multiset
MAX_EVENTS = 2000  # contract.GRAPH_LIMITS["events"]
RECORD_LIMIT = 8 * 1024 * 1024
RECORD_PATH = re.compile(r"\d{4}-\d{2}-\d{2}-outputs/edit-[0-9a-f]{12}/record\.json")
EVENT_NOTE = "edited by hand"
LOG_PATH_LIMIT = 1024  # characters of an edit-log line's `path`
LOG_GRAPH_LIMIT = 4096  # ... and of its `graph`
LOG_FIELD_LIMIT = LOG_GRAPH_LIMIT  # no field of an edit-log line is longer than this (task, step, output: 200)
_LOCK = threading.Lock()  # one writer at a time inside a process; flock on the graph folder across processes
_HOOK = None  # tests only: called as _HOOK(stage) at the points where another writer could step in


class OutputError(Exception):
    """A refused request; the message says why in plain words."""


def _hook(stage: str) -> None:
    if _HOOK is not None:
        _HOOK(stage)


# --- what counts as text -----------------------------------------------------------------------------


def text_problem(data: bytes) -> str | None:
    """Why these bytes are not an output this edits (None: they are): at most TEXT_LIMIT bytes of UTF-8 with no
    control character other than tab, newline and carriage return."""
    if len(data) > TEXT_LIMIT:
        return f"larger than {TEXT_LIMIT_WORDS}, the most an output may be to be edited here"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return "not text: it is not UTF-8"
    bad = next((ch for ch in text if ch not in "\t\n\r" and unicodedata.category(ch) == "Cc"), None)
    if bad is not None:
        return f"not text: it holds the control character U+{ord(bad):04X}"
    return None


def _clean_text(text) -> bytes:
    if not isinstance(text, str):
        raise OutputError("the text must be text")
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        raise OutputError("the text is not valid Unicode") from None
    problem = text_problem(data)
    if problem is not None:
        raise OutputError(f"the new text is {problem}; nothing was written")
    return data


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def output_rev(path: str, revision: int, data: bytes | None) -> str:
    """The `rev` of an output: its path, its revision and its file's bytes (None when there is no file)."""
    return agentfiles.rev(path, str(revision), data)


# --- where an output's file is -----------------------------------------------------------------------

OUTSIDE = "outside this task's artifacts folder (.opencode/artifacts/{task}/)"
LINKED = "reached through a link or something that is not a plain folder"


def _component(value) -> bool:
    return (isinstance(value, str) and value not in ("", ".", "..") and "/" not in value and "\0" not in value
            and len(value.encode("utf-8", "surrogatepass")) <= 255)


def project_of(graph_path) -> Path | None:
    """The project a graph file belongs to: the folder holding `.opencode/graph/<file>`; None otherwise."""
    path = Path(os.path.abspath(graph_path))
    if path.parent.name == "graph" and path.parent.parent.name == ".opencode":
        return path.parent.parent.parent
    return None


def _inside(task: str, path) -> list[str] | None:
    """The parts of an artifact path below `.opencode/artifacts/<task>/`, or None when it is not there (an absolute
    path, a `..`, another task's folder)."""
    if not _component(task) or not isinstance(path, str) or path.startswith("/") or "\0" in path or "\\" in path:
        return None
    parts = [part for part in path.split("/") if part not in ("", ".")]
    if ".." in parts or len(parts) < 4 or parts[:3] != [".opencode", "artifacts", task]:
        return None
    if not all(_component(part) for part in parts[3:]):
        return None
    return parts[3:]


def _open_dir(parent: int, name: str, *, create: bool = False, created: list | None = None) -> int:
    """A folder inside `parent`, never through a link; with `create`, made when it is missing (and noted)."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
    except OSError as exc:
        raise OutputError(f"{name} is {LINKED} ({exc.strerror})") from None
    os.mkdir(name, 0o755, dir_fd=parent)
    if created is not None:
        created.append(name)
    return os.open(name, flags, dir_fd=parent)


def _task_folder(project: Path, task: str) -> int:
    """The task's artifacts folder, opened from the project without following a link below the project."""
    current = os.open(project, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in (".opencode", "artifacts", task):
            following = _open_dir(current, name)
            os.close(current)
            current = following
    except BaseException:
        os.close(current)
        raise
    return current


def _folder_of(task_fd: int, dirs: list[str], *, create: bool = False, created: list | None = None) -> int:
    """The folder `dirs` (parts) inside the task folder; made (each new part noted in `created`) with `create`."""
    current = os.dup(task_fd)
    try:
        made = []
        for index, name in enumerate(dirs):
            before = len(made)
            following = _open_dir(current, name, create=create, created=made)
            if created is not None and len(made) > before:
                created.append("/".join(dirs[:index + 1]))
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


class HardLinkError(OutputError):
    """A file with more than one name: another name may be outside the task's artifacts folder."""


HARD_LINK = ("the file has another name (a hard link), so it may be a file outside this task's artifacts folder; "
             "it is not opened here")


def _open_file(folder: int, name: str):
    """(an open binary stream, its stat) of a regular file with one name in `folder`, never through a link; (None,
    None) when there is none."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        raise OutputError(f"{name} is {LINKED} ({exc.strerror})") from None
    stream = os.fdopen(fd, "rb")
    try:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise OutputError(f"{name} is not a regular file")
        if info.st_nlink > 1:
            raise HardLinkError(f"{name}: {HARD_LINK}")
    except BaseException:
        stream.close()
        raise
    return stream, info


def _read_file(folder: int, name: str) -> tuple[bytes | None, int | None]:
    """(the first TEXT_LIMIT + 1 bytes, the size) of a regular file with one name in `folder`, never through a link;
    (None, None) when there is none."""
    stream, info = _open_file(folder, name)
    if stream is None:
        return None, None
    with stream:
        return stream.read(TEXT_LIMIT + 1), info.st_size


def _write_new(folder: int, name: str, data: bytes) -> None:
    """Write `data` as a new file `name` in `folder`: written beside it, then linked in, never over an entry that
    is there."""
    temporary = f".{name}.{secrets.token_hex(6)}.tmp"
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=folder)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, name, src_dir_fd=folder, dst_dir_fd=folder, follow_symlinks=False)
        except FileExistsError:
            raise OutputError(f"{name} already exists where the next revision goes; nothing was written") from None
    finally:
        try:
            os.unlink(temporary, dir_fd=folder)
        except FileNotFoundError:
            pass
    os.fsync(folder)


# The snapshot's memory of the outputs it read: (path, revision, device, inode, size, mtime ns, ctime ns) -> (rev,
# why it is not text or None). A file whose bytes change gets a new ctime (and usually a new mtime, size or inode),
# so it is read again; nothing but a rev and a reason is kept. Only the snapshot uses it: a read, a write and an
# undo always read the file.
_SEEN: OrderedDict = OrderedDict()
_SEEN_LOCK = threading.Lock()


def _seen(key, value=None):
    with _SEEN_LOCK:
        if value is None:
            found = _SEEN.get(key)
            if found is not None:
                _SEEN.move_to_end(key)
            return found
        _SEEN[key] = value
        _SEEN.move_to_end(key)
        while len(_SEEN) > SEEN_LIMIT:
            _SEEN.popitem(last=False)
        return value


def _inspect(project: Path | None, task: str, node: dict, artifact: dict, *, cache: bool = False) -> dict:
    """{"rev", "size", "editable", "why", "data", "parts"} of one output, read under the caller's lock. With
    `cache` (the snapshot), a file read before and not changed since is not read again, and `data` is None."""
    path, revision = artifact.get("path"), artifact.get("revision")
    shown = {"rev": None, "size": None, "editable": False, "why": "", "data": None, "parts": None}
    if not isinstance(path, str) or type(revision) is not int or revision < 1:
        return {**shown, "why": "the graph does not give it a path and a revision"}
    parts = _inside(task, path) if project is not None else None
    if parts is None:
        return {**shown, "rev": output_rev(path, revision, None), "why": OUTSIDE.format(task=task)}
    shown["parts"] = parts
    try:
        task_fd = _task_folder(project, task)
    except FileNotFoundError:
        return {**shown, "rev": output_rev(path, revision, None), "why": "the file is missing"}
    except OutputError as exc:
        return {**shown, "rev": output_rev(path, revision, None), "why": f"the file is {LINKED}: {exc}"}
    try:
        try:
            folder = _folder_of(task_fd, parts[:-1])
        except FileNotFoundError:
            return {**shown, "rev": output_rev(path, revision, None), "why": "the file is missing"}
        try:
            stream, info = _open_file(folder, parts[-1])
        finally:
            os.close(folder)
    except HardLinkError:
        return {**shown, "rev": output_rev(path, revision, None), "why": HARD_LINK}
    except OutputError as exc:
        return {**shown, "rev": output_rev(path, revision, None), "why": f"the file is {LINKED}: {exc}"}
    finally:
        os.close(task_fd)
    if stream is None:
        return {**shown, "rev": output_rev(path, revision, None), "why": "the file is missing"}
    with stream:
        size = info.st_size
        key = (path, revision, info.st_dev, info.st_ino, size, info.st_mtime_ns, info.st_ctime_ns)
        known = _seen(key) if cache else None
        if known is not None:
            rev, problem = known
        else:
            data = stream.read(TEXT_LIMIT + 1)
            rev = output_rev(path, revision, data if size <= TEXT_LIMIT else data + b"+%d" % size)
            problem = text_problem(data)
            shown["data"] = data
            if cache:
                _seen(key, (rev, problem))
    shown.update(rev=rev, size=size)
    if problem is not None:
        return {**shown, "why": f"the file is {problem}"}
    if not _component(node.get("id")):
        return {**shown, "why": "the step's id cannot name a folder"}
    nxt = _next_path(task, node, artifact, parts)
    scope = node.get("scope")
    if isinstance(scope, list) and scope and not _in_scope(nxt, scope, project):
        return {**shown, "why": f"its next revision ({nxt}) would be outside the step's scope"}
    return {**shown, "editable": True}


def _next_parts(node: dict, artifact: dict, parts: list[str]) -> list[str]:
    """Where the next revision goes, below the task folder: `<folder>/r<N+1>/<rest>` for an output kept as
    `<folder>/r<k>/<rest>`, else `<node>/r<N+1>/<file name>`."""
    revision = artifact["revision"] + 1
    if len(parts) >= 3 and re.fullmatch(r"r\d+", parts[1]):
        return [parts[0], f"r{revision}", *parts[2:]]
    return [node["id"], f"r{revision}", parts[-1]]


def _next_path(task: str, node: dict, artifact: dict, parts: list[str]) -> str:
    return "/".join([".opencode", "artifacts", task, *_next_parts(node, artifact, parts)])


def _in_scope(path: str, scope: list, project: Path) -> bool:
    """Whether `path` is inside one of the step's `scope` entries, compared as `check` compares them."""
    anchored = PurePosixPath(str(project))
    target = _path_parts(path, anchored)
    return any(isinstance(entry, str) and entry.strip() and _contains(_path_parts(entry, anchored), target)
               for entry in scope)


def shown_output(project: Path | None, task: str, node: dict, artifact: dict, *, cache: bool = False) -> dict:
    """What the snapshot and the replies show of an output: {id, path, revision, rev, size, editable, why}."""
    found = _inspect(project, task, node, artifact, cache=cache)
    return {"id": artifact.get("id"), "path": artifact.get("path"), "revision": artifact.get("revision"),
            "rev": found["rev"], "size": found["size"], "editable": found["editable"], "why": found["why"]}


def annotate(document: dict) -> None:
    """Give each output of the snapshot's graph (`graph.nodes[].artifacts[]`) its `rev`, `size`, `editable` and
    `why`. Never raises; never adds content."""
    graph = document.get("graph") if isinstance(document, dict) else None
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return
    try:
        project = project_of(graph.get("path")) if isinstance(graph.get("path"), str) else None
    except (OSError, ValueError):
        project = None
    task, count = graph.get("task"), 0
    for node in graph["nodes"]:
        if not isinstance(node, dict) or not isinstance(node.get("artifacts"), list):
            continue
        for artifact in node["artifacts"]:
            if not isinstance(artifact, dict):
                continue
            count += 1
            if count > ANNOTATE_LIMIT:
                artifact.update(rev=None, size=None, editable=False,
                                why=f"not read: the graph lists more than {ANNOTATE_LIMIT} outputs")
                continue
            try:
                shown = shown_output(project, task, node, artifact, cache=True)
            except (OSError, ValueError, TypeError) as exc:
                artifact.update(rev=None, size=None, editable=False, why=f"cannot be read: {exc}")
                continue
            artifact.update({key: shown[key] for key in ("rev", "size", "editable", "why")})


# --- the graph file ------------------------------------------------------------------------------------


def graph_dirs(directory=None, start=None) -> list[Path]:
    """The graph folders searched for a task, in order: `directory` alone when given, else the one found from
    `start` (default: the working folder) and the orchestration's own, as the snapshot reads them."""
    if directory is not None:
        return [Path(directory)]
    found = find_graph_dir(Path(start) if start is not None else Path.cwd())
    return [folder for folder in (found, ORCHESTRATION_GRAPH_DIR) if folder is not None]


def _task_of(raw: dict, path: Path) -> str:
    value = raw.get("task")
    return path.stem if value is None else str(value)


def find_graph(task, dirs) -> Path:
    """The graph file whose task is `task`, in the first folder of `dirs` that has one; refused when there is none
    or when two files in that folder declare it."""
    if not isinstance(task, str) or not task:
        raise OutputError("the task must be a name")
    seen = set()
    for folder in dirs:
        try:
            folder = Path(folder)
            key = folder.resolve()
            if key in seen or not folder.is_dir():
                continue
            seen.add(key)
            files, _ = graph_files(folder)
        except OSError:
            continue
        found = []
        for path in files:
            try:
                raw = json.loads(read_graph_text(path))
            except (OSError, ValueError):
                continue
            if isinstance(raw, dict) and _task_of(raw, path) == task:
                found.append(path)
        if len(found) > 1:
            raise OutputError(f"{len(found)} graph files in {folder} declare the task {task[:80]!r} "
                              f"({', '.join(sorted(path.name for path in found))}); nothing was changed")
        if found:
            return found[0]
    raise OutputError(f"there is no task graph {task[:80]!r}")


class _GraphFolder:
    """The graph folder, open and locked for one writer at a time (a flock of the folder, and a lock inside this
    process)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def __enter__(self) -> int:
        _LOCK.acquire()
        try:
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY)
        except OSError as exc:
            _LOCK.release()
            raise OutputError(f"the graph folder {self.path} cannot be opened ({exc.strerror})") from None
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except BaseException as exc:
            os.close(self.fd)
            _LOCK.release()
            if isinstance(exc, OSError):
                raise OutputError(f"the graph folder {self.path} cannot be locked ({exc.strerror}); nothing was "
                                  "changed") from None
            raise
        return self.fd

    def __exit__(self, *exc) -> None:
        os.close(self.fd)
        _LOCK.release()


def _read_graph(folder: int, name: str) -> bytes:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    except FileNotFoundError:
        raise OutputError(f"the graph file {name} is gone; nothing was changed") from None
    except OSError as exc:
        raise OutputError(f"the graph file {name} is not a regular file ({exc.strerror})") from None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OutputError(f"the graph file {name} is not a regular file")
        data = stream.read(MAX_GRAPH_BYTES + 1)
    if len(data) > MAX_GRAPH_BYTES:
        raise OutputError(f"the graph file {name} is larger than {MAX_GRAPH_BYTES} bytes")
    return data


def _parse(data: bytes, name: str) -> dict:
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise OutputError(f"the graph file {name} is not valid JSON; nothing was changed") from None
    if not isinstance(raw, dict):
        raise OutputError(f"the graph file {name} is not a JSON object; nothing was changed")
    return raw


def _serialize(raw: dict) -> bytes:
    return (json.dumps(raw, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _locate(raw: dict, node_id, artifact_id) -> tuple[dict, dict]:
    """The node and its artifact entry, refused when either is missing, repeated or not well formed."""
    if not isinstance(node_id, str) or not node_id or not isinstance(artifact_id, str) or not artifact_id:
        raise OutputError("the step and the output must be named")
    nodes = [node for node in raw.get("nodes", []) if isinstance(node, dict) and node.get("id") == node_id] \
        if isinstance(raw.get("nodes"), list) else []
    if not nodes:
        raise OutputError(f"the graph has no step {node_id[:80]!r}")
    if len(nodes) > 1:
        raise OutputError(f"the graph has {len(nodes)} steps with the id {node_id[:80]!r}")
    node = nodes[0]
    found = [entry for entry in node.get("artifacts", []) if isinstance(entry, dict) and entry.get("id") == artifact_id] \
        if isinstance(node.get("artifacts"), list) else []
    if not found:
        raise OutputError(f"step {node_id} has no output {artifact_id[:80]!r}")
    if len(found) > 1:
        raise OutputError(f"step {node_id} has {len(found)} outputs with the id {artifact_id[:80]!r}")
    artifact = found[0]
    if not isinstance(artifact.get("path"), str) or type(artifact.get("revision")) is not int or artifact["revision"] < 1:
        raise OutputError(f"step {node_id}'s output {artifact_id} has no path and revision the graph can keep")
    return node, artifact


def _record_of(artifact: dict) -> dict:
    return {"path": artifact["path"], "revision": artifact["revision"]}


# An atomic exchange of two names in one folder: macOS renameatx_np(RENAME_SWAP), Linux renameat2(RENAME_EXCHANGE).
_SWAP = getattr(agentfiles._LIBC, "renameatx_np", None) or getattr(agentfiles._LIBC, "renameat2", None)
_SWAP_FLAG = 0x2


def _swap(folder: int, first: str, second: str) -> bool:
    """Exchange two entries of `folder` atomically; False when the system cannot."""
    if _SWAP is None:
        return False
    if _SWAP(folder, os.fsencode(first), folder, os.fsencode(second), _SWAP_FLAG) != 0:
        error = ctypes.get_errno()
        if error in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
            return False
        raise OSError(error, os.strerror(error), second)
    return True


def _peek(folder: int, name: str) -> bytes | None:
    try:
        return _read_graph(folder, name)
    except OutputError:
        return None


def _replace_graph(folder: int, name: str, make) -> tuple[bytes, bytes]:
    """Put `make(current bytes)` in place of the graph file, never losing a change another writer made meanwhile:
    the merged file goes in by an atomic exchange, and when the file it displaced is not the one it was merged
    from, that file goes back and the merge is made again from it. `make` may refuse (OutputError). Returns
    (the bytes merged from, the bytes written); both the same when `make` changed nothing."""
    for _ in range(8):
        current = _read_graph(folder, name)
        merged = make(current)
        if merged == current:
            return current, current
        mode = stat.S_IMODE(os.stat(name, dir_fd=folder, follow_symlinks=False).st_mode)
        temporary = f".{name}.{secrets.token_hex(6)}.tmp"
        handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(merged)
                stream.flush()
                os.fchmod(stream.fileno(), mode)
                os.fsync(stream.fileno())
            _hook("replace")
            if _swap(folder, temporary, name):
                displaced = _peek(folder, temporary)
                if displaced == current:
                    os.fsync(folder)
                    return current, merged
                # Another writer replaced the graph after it was read: its file goes back, and the merge is made
                # again from it.
                _swap(folder, temporary, name)
                if _peek(folder, temporary) != merged:
                    _swap(folder, temporary, name)  # yet another write came in between: keep it, and stop
                    raise OutputError("the graph file kept changing while this was being saved; nothing was "
                                      "changed. Try again.")
                continue
            if _peek(folder, name) != current:
                continue
            os.replace(temporary, name, src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
            return current, merged
        finally:
            try:
                os.unlink(temporary, dir_fd=folder)
            except FileNotFoundError:
                pass
    raise OutputError("the graph file kept changing while this was being saved; nothing was changed. Try again.")


def _readers(raw: dict, node_id: str, artifact_id: str, revision: int) -> list[dict]:
    """The steps that read an older revision of the output than `revision`: they need a rerun."""
    found = []
    for node in raw.get("nodes", []) if isinstance(raw.get("nodes"), list) else []:
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), list):
            continue
        reads = [entry.get("revision") for entry in node["inputs"]
                 if isinstance(entry, dict) and entry.get("from") == node_id and entry.get("artifact") == artifact_id
                 and type(entry.get("revision")) is int]
        old = [value for value in reads if value < revision]
        if old:
            found.append({"id": str(node.get("id", "")), "agent": str(node.get("agent", "")),
                          "goal": str(node.get("goal", ""))[:200], "status": str(node.get("status", "")),
                          "read": max(old)})
    return found


# --- the edit log ------------------------------------------------------------------------------------


def edit_home(home=None, *, root=None, config_path=None) -> Path:
    """The memory home the edit log goes in, beside the other ledgers: `home` when given; with `root` (the
    server's ledger root) the active preset's memory folder there (snapshot_json.ledger_home) or `root/memory`;
    else $AGENTGRAPH_HOME, the active preset's memory folder, or the orchestration's `memory`."""
    from .snapshot_json import ledger_home
    if home is not None:
        return Path(home).expanduser()
    if root is not None:
        return ledger_home(root, config_path=config_path) or Path(root) / "memory"
    if os.environ.get("AGENTGRAPH_HOME"):
        return Path(os.environ["AGENTGRAPH_HOME"]).expanduser()
    return ledger_home(config_path=config_path) or ORCHESTRATION_GRAPH_DIR.parent.parent / "memory"


class _Ledger:
    """The memory home's lock (the `.lock` file `agentgraph record` takes too)."""

    def __init__(self, home: Path):
        self.home = Path(home)

    def __enter__(self):
        self.home.mkdir(parents=True, exist_ok=True)
        self.stream = (self.home / ".lock").open("a+b")
        fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        self.stream.close()


def _line_counts(before: str, after: str) -> tuple[int, int]:
    """(lines added, lines removed) from the earlier text to the new one. Up to DIFF_LINES lines on each side, by a
    diff; above it, as multisets (a line moved is not counted), which takes linear time: a diff of text made of one
    repeated line takes time cubic in its lines. Called with no lock held."""
    old, new = before.splitlines(), after.splitlines()
    if len(old) > DIFF_LINES or len(new) > DIFF_LINES:
        change = Counter(new)
        change.subtract(Counter(old))
        return sum(n for n in change.values() if n > 0), -sum(n for n in change.values() if n < 0)
    added = removed = 0
    for kind, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if kind in ("replace", "delete"):
            removed += i2 - i1
        if kind in ("replace", "insert"):
            added += j2 - j1
    return added, removed


def _append_log(home: Path, entry: dict) -> None:
    with _Ledger(home):
        with (Path(home) / EDIT_LOG).open("a+b") as stream:
            separator = b""
            if stream.seek(0, os.SEEK_END):
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    separator = b"\n"
            stream.write(separator + (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())


def _rewrite_log(home: Path, edit_id: str, change) -> bool:
    """Replace the edit's line by `change(entry)` (None drops it); every other line is kept byte for byte. A log left
    empty is removed. False when the log has no such line."""
    path = Path(home) / EDIT_LOG
    with _Ledger(home):
        try:
            lines = path.read_bytes().splitlines(keepends=True)
        except FileNotFoundError:
            return False
        changed = False
        for index, line in enumerate(lines):
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("id") == edit_id:
                item = change(item)
                lines[index] = b"" if item is None else (json.dumps(item, ensure_ascii=False) + "\n").encode("utf-8")
                changed = True
        if not changed:
            return False
        if not any(lines):
            path.unlink()
            return True
        temporary = path.with_name(f".{EDIT_LOG}.{secrets.token_hex(6)}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.writelines(lines)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return True


def _mark_undone(home: Path, edit_id: str, now: float) -> None:
    """Set `undone` on the edit's line; every other line is kept byte for byte."""
    _rewrite_log(home, edit_id, lambda item: {**item, "undone": True, "undone_time": now}
                 if item.get("undone") is False else item)


def _drop_line(home, edit_id: str) -> None:
    """Take back the line of an edit that was refused after it was written (never raises)."""
    try:
        _rewrite_log(Path(home), edit_id, lambda item: None)
    except OSError:
        pass


def read_log(home: Path) -> list[dict]:
    """The edit log's entries (its newest EDIT_LOG_READ bytes), oldest first; lines that are not entries are
    skipped. Never raises."""
    try:
        with (Path(home) / EDIT_LOG).open("rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(size - EDIT_LOG_READ, 0))
            data = stream.read(EDIT_LOG_READ)
    except OSError:
        return []
    lines = data.splitlines()
    if len(data) == EDIT_LOG_READ and size > EDIT_LOG_READ:
        lines = lines[1:]  # the first line may be cut
    entries = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if (isinstance(item, dict) and isinstance(item.get("graph"), str) and isinstance(item.get("node"), str)
                and isinstance(item.get("artifact"), str) and type(item.get("revision")) is int):
            entries.append(item)
    return entries


def _graph_key(path) -> str:
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return str(path)


def _event_note(entry: dict) -> str:
    """The note of the `changed` event an edit put in the graph (from its log line, or before it is written)."""
    return (f"output {entry.get('artifact')} {EVENT_NOTE} by the user: revision {entry.get('from_revision')} -> "
            f"{entry.get('revision')} ({entry.get('path')})")


def _has_record(backups, edit_id) -> bool:
    """Whether the undo record of the edit `edit_id` (the log line's `id`) is in `backups`."""
    if not isinstance(edit_id, str) or not re.fullmatch(r"[0-9a-f]{12}", edit_id):
        return False
    try:
        return any((day / f"edit-{edit_id}" / "record.json").is_file() for day in Path(backups).glob("*-outputs"))
    except OSError:
        return False


def _backups_of(home) -> Path:
    """The backups folder beside the memory home's orchestration folder, as in `~/.config/opencode`."""
    return Path(home).expanduser().parent.parent / "backups"


def _never_saved(entry: dict, graph, backups) -> bool:
    """Whether the log line is of a save stopped before the graph was written: its line counts were never made, it
    has no undo record, and the graph neither records its revision and path nor holds its `changed` event."""
    if entry.get("lines_added") is not None or entry.get("lines_removed") is not None:
        return False
    node = next((item for item in graph.nodes if item.id == entry["node"]), None)
    artifact = next((item for item in node.artifacts if item.id == entry["artifact"]), None) if node else None
    if artifact is not None and (artifact.revision, artifact.path) == (entry["revision"], entry.get("path")):
        return False
    if any(event.node == entry["node"] and event.event == "changed" and event.note == _event_note(entry)
           for event in graph.events):
        return False
    return not _has_record(backups, entry.get("id"))


def unsaved_edits(graph, home, backups=None) -> list[str]:
    """`check`'s warnings for edits whose save was stopped before the graph was written (killed, or the power went):
    the line is in the edit log, the graph never had the edit, and its file is left with no record of it."""
    if home is None:
        return []
    backups = _backups_of(home) if backups is None else backups
    key = _graph_key(graph.path)
    return [f"{entry['node']} output {entry['artifact']!r}: a hand edit's save was stopped before the graph was "
            f"written, so the graph never had it; its file {entry.get('path', '?')} is no recorded revision and can "
            "be deleted" for entry in read_log(Path(home))
            if entry.get("undone") is False and _graph_key(entry["graph"]) == key
            and _never_saved(entry, graph, backups)]


def lost_edits(graph, home, backups=None) -> list[str]:
    """`check`'s problems for outputs edited by hand after the graph last recorded them: an edit in the log, not
    undone, whose revision and path the graph does not record, unless the graph records a later revision and still
    holds the edit's `changed` event (a later run built on it). Such an edit was lost when an orchestrator rewrote the
    graph from an older copy. An edit whose save was stopped before the graph was written is left to
    `unsaved_edits`. `backups` (the undo records) defaults to the folder beside the memory home's orchestration
    folder."""
    if home is None:
        return []
    backups = _backups_of(home) if backups is None else backups
    key = _graph_key(graph.path)
    newest: dict[tuple[str, str], dict] = {}
    for entry in read_log(Path(home)):
        if entry.get("undone") is not False or _graph_key(entry["graph"]) != key:
            continue
        if _never_saved(entry, graph, backups):
            continue  # a warning (unsaved_edits), never a problem
        pair = (entry["node"], entry["artifact"])
        if pair not in newest or entry["revision"] >= newest[pair]["revision"]:
            newest[pair] = entry
    problems = []
    for (node_id, artifact_id), entry in newest.items():
        node = next((item for item in graph.nodes if item.id == node_id), None)
        artifact = next((item for item in node.artifacts if item.id == artifact_id), None) if node else None
        if artifact is not None and (artifact.revision, artifact.path) == (entry["revision"], entry.get("path")):
            continue  # the graph records the edit
        if artifact is not None and artifact.revision > entry["revision"] and any(
                event.node == node_id and event.event == "changed" and event.note == _event_note(entry)
                for event in graph.events):
            continue  # a later revision, written to a graph that had recorded the edit
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["time"])) \
            if isinstance(entry.get("time"), (int, float)) else "at an unknown time"
        now = (f"the graph says revision {artifact.revision}" if artifact is not None
               else "the graph no longer lists that output")
        problems.append(f"{node_id} output {artifact_id!r} was edited by hand after the graph last recorded it: the "
                        f"edit of {when} made revision {entry['revision']} ({entry.get('path', '?')}), {now}; "
                        f"record revision {entry['revision']} with that path, or undo the edit "
                        "(agentgraph output restore RECORD)")
    return problems


# --- undo records --------------------------------------------------------------------------------------


def _save_record(backups, record: dict, edit_id: str) -> str:
    """Save the undo record as `<backups>/<date>-outputs/edit-<edit_id>/record.json` (the edit log's `id`, so the
    log line leads to its record)."""
    root = Path(backups)
    folder = root / f"{date.today().isoformat()}-outputs"
    root.parent.mkdir(parents=True, exist_ok=True)
    for path in (root, folder):
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            pass
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            raise OutputError(f"{path} is not a plain folder")
    target = folder / f"edit-{edit_id}"
    os.mkdir(target, 0o700)
    path = target / "record.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return str(path)


def _prune_records(record: str) -> None:
    """Keep the newest skillset.MAX_BACKUPS edit records across every day's folder; `record` is always kept."""
    folder = Path(record).parent
    try:
        mine = []
        for day in folder.parent.parent.iterdir():
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}-outputs", day.name) or not stat.S_ISDIR(day.lstat().st_mode):
                continue
            mine += [(day.name, path.lstat().st_mtime_ns, path) for path in day.iterdir()
                     if path != folder and re.fullmatch(r"edit-[0-9a-f]{12}", path.name)
                     and stat.S_ISDIR(path.lstat().st_mode)]
        mine.sort(key=lambda item: item[:2])
        for _, _, old in mine[:max(len(mine) + 1 - skillset.MAX_BACKUPS, 0)]:
            if [child.name for child in old.iterdir()] == ["record.json"]:
                (old / "record.json").unlink()
                old.rmdir()
                if old.parent != folder.parent and not any(old.parent.iterdir()):
                    old.parent.rmdir()
    except OSError:
        pass


def _drop_record(record: str) -> None:
    path = Path(record)
    path.unlink(missing_ok=True)
    for folder in (path.parent, path.parent.parent):
        try:
            folder.rmdir()
        except OSError:
            break


_RECORD_KEYS = {"version", "op", "task", "graph", "node", "artifact", "before", "after", "before_digest",
                "after_digest", "graph_before", "graph_after", "event", "created", "log"}


def _load_record(record, backups) -> dict:
    if not isinstance(record, str) or not record or "\0" in record or len(record) > 4096:
        raise OutputError("invalid undo record: expected the record an edit returned")
    root = Path(os.path.abspath(backups))
    path = Path(os.path.abspath(record))
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise OutputError("invalid undo record: it is not one of the backups folder's undo records") from None
    if not RECORD_PATH.fullmatch(relative.as_posix()):
        raise OutputError("invalid undo record: it is not one of the backups folder's undo records")
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise OutputError("invalid undo record: its folder is not a plain folder")
        except FileNotFoundError:
            raise OutputError("invalid undo record: there is no such record") from None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise OutputError("invalid undo record: there is no such record") from None
    except OSError as exc:
        raise OutputError(f"invalid undo record: {exc.strerror}") from None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise OutputError("invalid undo record: not a regular file")
        raw = stream.read(RECORD_LIMIT + 1)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise OutputError("invalid undo record: it is not JSON") from None
    pairs = ("before", "after")
    if (not isinstance(data, dict) or set(data) != _RECORD_KEYS or data["version"] != 1 or data["op"] != "edit"
            or not all(isinstance(data[key], str) for key in ("task", "graph", "node", "artifact", "before_digest",
                                                               "after_digest", "graph_before", "graph_after", "log"))
            or not all(isinstance(data[key], dict) and set(data[key]) == {"path", "revision"}
                       and isinstance(data[key]["path"], str) and type(data[key]["revision"]) is int for key in pairs)
            or not isinstance(data["event"], dict)
            or not isinstance(data["created"], list) or not all(isinstance(item, str) for item in data["created"])):
        raise OutputError("invalid undo record: not an output edit's record")
    try:
        data["graph_before"] = base64.b64decode(data["graph_before"], validate=True)
    except ValueError:
        raise OutputError("invalid undo record: the earlier graph is not base64") from None
    earlier = data["graph_before"]
    try:
        nodes = json.loads(earlier.decode("utf-8")).get("nodes") if len(earlier) <= MAX_GRAPH_BYTES else None
    except (UnicodeDecodeError, ValueError, RecursionError, AttributeError):
        nodes = None
    if not isinstance(nodes, list):
        raise OutputError("invalid undo record: the earlier graph is not a graph file (JSON with a `nodes` list, at "
                          f"most {MAX_GRAPH_BYTES} bytes); nothing was restored")
    return data


# --- the three operations --------------------------------------------------------------------------------


def _what(node_id: str, artifact_id: str) -> str:
    return f"step {node_id}'s output {artifact_id}"


def _expect(expect, current: str, what: str) -> None:
    try:
        agentfiles.check_expect(expect, current, what)
    except agentfiles.StaleError:
        raise
    except agentfiles.AgentfilesError as exc:
        raise OutputError(str(exc)) from None


def read_output(task, node_id, artifact_id, *, dirs) -> dict:
    """{"task", "node", "artifact", "path", "revision", "rev", "size", "text", "editable": true} of an output;
    refused, with the reason, when it cannot be edited here."""
    graph_path = find_graph(task, dirs)
    project = project_of(graph_path)
    with _GraphFolder(graph_path.parent) as folder:
        raw = _parse(_read_graph(folder, graph_path.name), graph_path.name)
        node, artifact = _locate(raw, node_id, artifact_id)
        found = _inspect(project, task, node, artifact)
    if not found["editable"]:
        raise OutputError(f"{_what(node_id, artifact_id)} cannot be opened here: {found['why'] or 'unknown reason'}")
    return {"task": task, "node": node_id, "artifact": artifact_id, "path": artifact["path"],
            "revision": artifact["revision"], "rev": found["rev"], "size": found["size"],
            "text": found["data"].decode("utf-8"), "editable": True}


def write_output(task, node_id, artifact_id, text, *, dirs, home, backups, expect=None, now=None) -> dict:
    """Save `text` as the output's next revision (see the module's description). Returns {"task", "node",
    "artifact", "changed", "output", "rev", "stale", "message", "record"?}: `stale` lists the steps that read an
    older revision and now need a rerun. Refused (agentfiles.StaleError) when `expect` is given and is not the
    output's `rev`; refused (OutputError) for every other reason. Nothing is ever run."""
    data = _clean_text(text)
    graph_path = find_graph(task, dirs)
    project = project_of(graph_path)
    if project is None:
        raise OutputError(f"the graph file {graph_path} is not in a project's .opencode/graph folder")
    now = time.time() if now is None else now
    name = graph_path.name
    edit_id = secrets.token_hex(6)
    warnings: list[str] = []
    # The log line goes in before the graph lock is taken (so an edit never waits on the memory home's lock while it
    # holds the graph's) and before the graph is saved, its line counts null until they are made after the lock is
    # released: a write stopped at any point after the graph is saved still leaves its line. It is planned from the
    # graph as it is now; the save under the lock refuses when the graph says otherwise, and a refused save takes the
    # line back after the lock is released.
    planned = _plan_entry(graph_path, project, task, node_id, artifact_id, data, text, expect, now, edit_id)
    logged = False
    if planned is not None:
        try:
            _append_log(Path(home), planned)
            logged = True
        except OSError as exc:
            warnings.append(f"the edit log could not be written: {exc}")
    kept = False
    try:
        with _GraphFolder(graph_path.parent) as folder:
            answer = _write_locked(folder, name, graph_path, project, task, node_id, artifact_id, data, text, expect,
                                   now, edit_id, planned, backups)
        kept = answer["changed"]
    finally:
        if logged and not kept:
            _drop_line(home, edit_id)
    if not answer["changed"]:
        return answer
    entry, record, old_text, new_text, written, shown = (answer.pop(key) for key in
                                                           ("entry", "record", "old", "new", "written", "shown"))
    _prune_records(record)
    added, removed = _line_counts(old_text, new_text)  # no lock held: never delays another writer
    if logged:
        try:
            _rewrite_log(Path(home), edit_id, lambda item: {**item, "lines_added": added, "lines_removed": removed})
        except OSError as exc:
            warnings.append(f"the edit log's line counts could not be written: {exc}")
    elif planned is None:  # the graph could not be read before the lock: the line goes in now, whole
        try:
            _append_log(Path(home), {**entry, "lines_added": added, "lines_removed": removed})
        except OSError as exc:
            warnings.append(f"the edit log could not be written: {exc}")
    fresh = _parse(written, name)
    stale = _readers(fresh, node_id, artifact_id, entry["revision"])
    named = ", ".join(f"{item['id']} ({item['agent']})" for item in stale)
    message = (f"Saved as revision {entry['revision']}." + (f" These steps read revision {entry['from_revision']} "
               f"and now need a rerun: {named}." if stale else " No step reads it yet.") + " Nothing was run.")
    return {"task": task, "node": node_id, "artifact": artifact_id, "changed": True, "output": shown,
            "rev": shown["rev"], "stale": stale, "record": record, "message": message,
            **({"warnings": warnings} if warnings else {})}


def _new_entry(graph_path, task, node, node_id, artifact_id, before, after, old_text, new_text, now, edit_id):
    """The edit's log line. What a graph says reaches the log only as a plain name: an `agent` that is not one is
    logged as "" (under no agent); a task, step or output name or a path that cannot be held to the log's bounds
    refuses the edit (before anything is written)."""
    graph = _graph_key(graph_path)
    if not all(isinstance(name, str) and OUTPUT_NAME.fullmatch(name) for name in (task, node_id, artifact_id)) \
            or len(after["path"]) > LOG_PATH_LIMIT or len(graph) > LOG_GRAPH_LIMIT:
        raise OutputError("the edit cannot be logged (a name or path is too long or not a plain name); nothing was "
                          "written")
    agent = node.get("agent")
    return {"time": now, "id": edit_id, "task": task, "graph": graph, "node": node_id,
            "agent": agent if isinstance(agent, str) and AGENT_NAME.fullmatch(agent) else "", "artifact": artifact_id, "from_revision": before["revision"],
            "revision": after["revision"], "path": after["path"], "chars_before": len(old_text),
            "chars_after": len(new_text), "lines_added": None, "lines_removed": None, "undone": False}


def _plan_entry(graph_path, project, task, node_id, artifact_id, data, text, expect, now, edit_id):
    """The edit's log line as the graph and the output are now, read without the graph lock; None when the save
    would change nothing or be refused, or the graph cannot be read now (the read under the lock decides). Never
    raises."""
    try:
        folder = os.open(graph_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            raw = _parse(_read_graph(folder, graph_path.name), graph_path.name)
        finally:
            os.close(folder)
        node, artifact = _locate(raw, node_id, artifact_id)
        found = _inspect(project, task, node, artifact)
        if not found["editable"] or found["data"] == data or (expect is not None and expect != found["rev"]):
            return None
        before = _record_of(artifact)
        parts = _next_parts(node, artifact, found["parts"])
        after = {"path": "/".join([".opencode", "artifacts", task, *parts]), "revision": before["revision"] + 1}
        here = Path(project) / ".opencode" / "artifacts" / task
        for index, part in enumerate(parts):
            here = here / part
            if here.is_symlink() or (index == len(parts) - 1 and os.path.lexists(here)):
                return None  # the save is refused: something is already where the next revision goes
        return _new_entry(graph_path, task, node, node_id, artifact_id, before, after, found["data"].decode("utf-8"),
                          text, now, edit_id)
    except (OutputError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def _write_locked(folder, name, graph_path, project, task, node_id, artifact_id, data, text, expect, now, edit_id,
                  planned, backups) -> dict:
    """The part of write_output done under the graph lock: {"changed": False, ...} (the reply) when the text is the
    same, else {"changed": True, "entry", "record", "old", "new", "written", "shown"}."""
    base = _read_graph(folder, name)
    raw = _parse(base, name)
    node, artifact = _locate(raw, node_id, artifact_id)
    found = _inspect(project, task, node, artifact)
    _expect(expect, found["rev"], _what(node_id, artifact_id))
    if not found["editable"]:
        raise OutputError(f"{_what(node_id, artifact_id)} cannot be edited here: {found['why']}")
    before = _record_of(artifact)
    if found["data"] == data:
        return {"task": task, "node": node_id, "artifact": artifact_id, "changed": False,
                "output": shown_output(project, task, node, artifact), "rev": found["rev"],
                "stale": _readers(raw, node_id, artifact_id, before["revision"]),
                "message": "The text is the same; nothing was changed."}
    parts = _next_parts(node, artifact, found["parts"])
    after = {"path": "/".join([".opencode", "artifacts", task, *parts]), "revision": before["revision"] + 1}
    if raw.get("events") is not None and isinstance(raw.get("events"), list) and len(raw["events"]) >= MAX_EVENTS:
        raise OutputError(f"the graph already holds {MAX_EVENTS} events, the most `check` allows; nothing was "
                          "written")
    old_text, new_text = found["data"].decode("utf-8"), text
    entry = _new_entry(graph_path, task, node, node_id, artifact_id, before, after, old_text, new_text, now,
                       edit_id)
    if planned is not None and planned != entry:
        raise OutputError(f"{_what(node_id, artifact_id)} was changed in the graph while this edit was being "
                          f"saved (it is now revision {before['revision']} at {before['path']}); nothing was "
                          "changed")
    event = {"time": now, "node": node_id, "event": "changed", "note": _event_note(entry)}

    def merged(current: bytes) -> bytes:
        fresh = _parse(current, name)
        fresh_node, fresh_artifact = _locate(fresh, node_id, artifact_id)
        if _record_of(fresh_artifact) != before:
            raise OutputError(f"{_what(node_id, artifact_id)} was changed in the graph while this edit was being "
                              f"saved (it is now revision {fresh_artifact['revision']} at "
                              f"{fresh_artifact['path']}); nothing was changed")
        fresh_artifact.update(after)
        events = fresh.get("events")
        if not isinstance(events, list):
            events = fresh["events"] = []
        if len(events) >= MAX_EVENTS:
            raise OutputError(f"the graph already holds {MAX_EVENTS} events, the most `check` allows; nothing "
                              "was written")
        events.append(dict(event))
        fresh["updated"] = now
        return _serialize(fresh)

    created: list[str] = []
    task_fd = _task_folder(project, task)
    try:
        target = _folder_of(task_fd, parts[:-1], create=True, created=created)
        try:
            written_file = False
            try:
                _write_new(target, parts[-1], data)
                written_file = True
                _hook("file")
                used, written = _replace_graph(folder, name, merged)
            except BaseException:
                if written_file:
                    os.unlink(parts[-1], dir_fd=target)
                _remove_created(task_fd, created)
                raise
        finally:
            os.close(target)
        record_data = {"version": 1, "op": "edit", "task": task, "graph": _graph_key(graph_path),
                       "node": node_id, "artifact": artifact_id, "before": before, "after": after,
                       "before_digest": _digest(found["data"]), "after_digest": _digest(data),
                       "graph_before": base64.b64encode(used).decode("ascii"), "graph_after": _digest(written),
                       "event": event, "created": created, "log": edit_id}
        try:
            record = _save_record(backups, record_data, edit_id)
        except BaseException:
            _undo_graph(folder, name, used, written)
            _remove_file(task_fd, parts, _digest(data), created)
            raise
    finally:
        os.close(task_fd)
    fresh_node, fresh_artifact = _locate(_parse(written, name), node_id, artifact_id)
    return {"changed": True, "entry": entry, "record": record, "old": old_text, "new": new_text, "written": written,
            "shown": shown_output(project, task, fresh_node, fresh_artifact)}


def _remove_created(task_fd: int, created: list[str]) -> None:
    for relative in reversed(created):
        parts = relative.split("/")
        try:
            parent = _folder_of(task_fd, parts[:-1])
        except (OSError, OutputError):
            continue
        try:
            os.rmdir(parts[-1], dir_fd=parent)
        except OSError:
            pass
        finally:
            os.close(parent)


def _remove_file(task_fd: int, parts: list[str], digest: str, created: list[str]) -> None:
    """Remove the edit's file while it is still what the edit wrote, then the folders the edit made, when empty."""
    try:
        folder = _folder_of(task_fd, parts[:-1])
    except (OSError, OutputError):
        return
    try:
        current, _ = _read_file(folder, parts[-1])
        if current is not None and _digest(current) == digest:
            os.unlink(parts[-1], dir_fd=folder)
    finally:
        os.close(folder)
    _remove_created(task_fd, created)


def _undo_graph(folder: int, name: str, before: bytes, after: bytes) -> None:
    """Put the graph back to `before` while it is still `after` (a failed save after the graph was written)."""
    try:
        _replace_graph(folder, name, lambda current: before if current == after else current)
    except (OSError, OutputError):
        pass


def restore_output(record, *, backups, home, dirs, now=None) -> dict:
    """Take back an edit: the graph records the earlier revision again (byte for byte, when nothing else changed
    it since) and the edit's file goes. Refused, naming what stands in the way, when a later run replaced the
    output in the graph, or a file was changed after the edit. Returns {"task", "node", "artifact", "changed",
    "restored", "output", "rev", "text"?, "message", "warnings"?}. Only a record of a graph in one of `dirs` (the
    request's graph folders, as a read or a write finds them) is taken back."""
    data = _load_record(record, backups)
    now = time.time() if now is None else now
    task, node_id, artifact_id = data["task"], data["node"], data["artifact"]
    before, after = data["before"], data["after"]
    graph_path = Path(data["graph"])
    if _graph_key(graph_path) != str(graph_path):
        # Every lock and write below uses this path: it must be the one checked here (a write's record holds the
        # real path; a path through a link would let the project, its artifacts or the lock be another folder's).
        raise OutputError(f"the undo record's graph path is not its real path ({graph_path}); nothing was restored")
    if os.path.dirname(_graph_key(graph_path)) not in {_graph_key(folder) for folder in dirs}:
        raise OutputError(f"the undo record is for a graph that is not in this project ({graph_path}); nothing was "
                          "restored")
    project = project_of(graph_path)
    before_parts, after_parts = _inside(task, before["path"]), _inside(task, after["path"])
    if project is None or before_parts is None or after_parts is None or not all(
            _inside(task, f".opencode/artifacts/{task}/{relative}") for relative in data["created"]):
        raise OutputError("invalid undo record: its paths are not a task's artifacts")
    what = _what(node_id, artifact_id)
    name = graph_path.name
    try:
        locked = _GraphFolder(graph_path.parent)
        folder = locked.__enter__()
    except OutputError:
        raise OutputError(f"the graph of task {task!r} is gone; nothing was restored") from None
    try:
        current = _read_graph(folder, name)
        raw = _parse(current, name)
        if _task_of(raw, graph_path) != task:
            raise OutputError(f"{name} is no longer the graph of task {task!r}; nothing was restored")
        try:
            node, artifact = _locate(raw, node_id, artifact_id)
        except OutputError as exc:
            raise OutputError(f"the edit cannot be undone: {exc}; nothing was restored") from None
        state = _record_of(artifact)
        if state not in (before, after):
            raise OutputError(f"the edit cannot be undone: {what} is now revision {state['revision']} at "
                              f"{state['path']} (a later run replaced it), not the hand edit's revision "
                              f"{after['revision']}; nothing was restored")
        try:
            task_fd = _task_folder(project, task)
        except FileNotFoundError:
            raise OutputError(f"the edit cannot be undone: the artifacts folder of task {task!r} is gone; nothing "
                              "was restored") from None
        try:
            for parts, digest, label in ((after_parts, data["after_digest"], f"revision {after['revision']}"),
                                         (before_parts, data["before_digest"], f"revision {before['revision']}")):
                try:
                    holder = _folder_of(task_fd, parts[:-1])
                except FileNotFoundError:
                    raise OutputError(f"the edit cannot be undone: the file of {what} {label} ({'/'.join(parts)}) is "
                                      "gone; nothing was restored") from None
                try:
                    found, _ = _read_file(holder, parts[-1])
                finally:
                    os.close(holder)
                if found is None or _digest(found) != digest:
                    raise OutputError(f"the edit cannot be undone: the file of {what} {label} was "
                                      f"{'removed' if found is None else 'changed'} after the edit; nothing was "
                                      "restored")

            def reverted(current_bytes: bytes) -> bytes:
                if _digest(current_bytes) == data["graph_after"]:
                    return data["graph_before"]
                fresh = _parse(current_bytes, name)
                _, fresh_artifact = _locate(fresh, node_id, artifact_id)
                record_now = _record_of(fresh_artifact)
                if record_now == before:
                    return current_bytes  # the graph already records the earlier revision
                if record_now != after:
                    raise OutputError(f"the edit cannot be undone: {what} is now revision {record_now['revision']} "
                                      f"at {record_now['path']} (a later run replaced it), not the hand edit's "
                                      f"revision {after['revision']}; nothing was restored")
                fresh_artifact.update(before)
                events = fresh.get("events")
                if isinstance(events, list):
                    for index in range(len(events) - 1, -1, -1):
                        if events[index] == data["event"]:
                            del events[index]
                            break
                fresh["updated"] = now
                return _serialize(fresh)

            _, written = _replace_graph(folder, name, reverted)
            _remove_file(task_fd, after_parts, data["after_digest"], data["created"])
            fresh = _parse(written, name)
            fresh_node, fresh_artifact = _locate(fresh, node_id, artifact_id)
        finally:
            os.close(task_fd)
        found = _inspect(project, task, fresh_node, fresh_artifact)
        shown = shown_output(project, task, fresh_node, fresh_artifact)
    finally:
        locked.__exit__(None, None, None)
    warnings = []
    try:
        _mark_undone(Path(home), data["log"], now)
    except OSError as exc:
        warnings.append(f"the edit log could not be updated ({exc}): its line for this edit does not say undone, so "
                        "`agentgraph check` may still report the edit as lost")
    _drop_record(record)
    text = found["data"].decode("utf-8") if found["editable"] else None
    return {"task": task, "node": node_id, "artifact": artifact_id, "changed": True, "restored": True,
            "output": shown, "rev": shown["rev"], **({"text": text} if text is not None else {}),
            "message": f"Put back revision {before['revision']}; the hand edit's revision {after['revision']} is gone.",
            **({"warnings": warnings} if warnings else {})}


# --- command -------------------------------------------------------------------------------------------


def _read_text(source: str) -> str:
    if source == "-":
        data = sys.stdin.buffer.read(TEXT_LIMIT + 1)
    else:
        with open(source, "rb") as stream:
            data = stream.read(TEXT_LIMIT + 1)
    if len(data) > TEXT_LIMIT:
        raise OutputError(f"the new text is larger than {TEXT_LIMIT_WORDS}; nothing was written")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise OutputError("the new text is not UTF-8 text; nothing was written") from None


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["output"]:
        argv = argv[1:]
    parser = argparse.ArgumentParser(prog="agentgraph output", description=__doc__.splitlines()[0])
    actions = parser.add_subparsers(dest="action", required=True)
    read = actions.add_parser("read", help="print the text of TASK's step NODE's output ARTIFACT")
    write = actions.add_parser("write", help="save a new text as the output's next revision (undoable)")
    restore = actions.add_parser("restore", help="undo an edit from the record it returned")
    restore.add_argument("record", metavar="RECORD")
    for sub in (read, write, restore):
        if sub is not restore:
            sub.add_argument("task", metavar="TASK")
            sub.add_argument("node", metavar="NODE")
            sub.add_argument("artifact", metavar="ARTIFACT")
        where = sub.add_mutually_exclusive_group()
        where.add_argument("--dir", type=Path, metavar="DIR", help="the graph folder (default: found from --from)")
        where.add_argument("--from", dest="start", type=Path, metavar="DIR",
                           help="find .opencode/graph from DIR, as the snapshot does (default: the working folder)")
    write.add_argument("--text-file", metavar="F", required=True, help="the new text from file F, or - for stdin")
    write.add_argument("--expect", metavar="REV", help="refuse, changing nothing, unless the output's rev is REV")
    for sub in (write, restore):
        sub.add_argument("--home", type=Path, metavar="DIR", help="the memory home the edit log is in (default: "
                         "$AGENTGRAPH_HOME, else the active preset's memory folder)")
        sub.add_argument("--backups", type=Path, metavar="DIR", help="where undo records go (default: "
                         "~/.config/opencode/backups)")
    for sub in (read, write, restore):
        sub.add_argument("--json", action="store_true", help="print the answer as JSON")
    args = parser.parse_args(argv)
    try:
        if args.action == "read":
            result = read_output(args.task, args.node, args.artifact, dirs=graph_dirs(args.dir, args.start))
        elif args.action == "write":
            text = _read_text(args.text_file)
            result = write_output(args.task, args.node, args.artifact, text, dirs=graph_dirs(args.dir, args.start),
                                  home=edit_home(args.home), backups=args.backups or skillset.BACKUPS,
                                  expect=args.expect)
        else:
            result = restore_output(args.record, home=edit_home(args.home), backups=args.backups or skillset.BACKUPS,
                                    dirs=graph_dirs(args.dir, args.start))
    except (OutputError, agentfiles.AgentfilesError, OSError) as exc:
        current = getattr(exc, "rev", None)
        if args.json:
            print(json.dumps({"error": str(exc), **({"rev": current} if current is not None else {})}))
        else:
            print(f"agentgraph output: {exc}" + (f" (rev now {current})" if current is not None else ""),
                  file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.action == "read":
        sys.stdout.write(result["text"])
        print(f"rev {result['rev']}, revision {result['revision']}, {result['path']}", file=sys.stderr)
        return 0
    print(result["message"])
    if result.get("record"):
        print(f"undo: agentgraph output restore {result['record']}")
    for warning in result.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
