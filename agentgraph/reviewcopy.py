"""Run a node's own check and one end-to-end command in a throwaway copy of the project: `agentgraph review-copy`.

    review-copy --task T --node N [--dir PROJECT] [--timeout SECONDS] [--keep]

A reviewer that cannot run anything only reads code, and a bug that shows when the program is started gets through. This gives it
one safe thing to run. The commands come only from the graph node: its `check` and its `e2e` (one end-to-end command). Nothing on the
command line is ever run; a node with neither is refused. The project is the nearest `.opencode/graph`'s at or above the current
folder; `--dir` may only name that project or the root of the git work tree the current folder is in, and a linked `.opencode/graph`
is refused.

The project's working tree (uncommitted and untracked files too; git-ignored files, `.git`, `node_modules`, virtualenvs and the
`.opencode` artifacts, loops and handoff folders left out) is copied under the system temp folder, and `check` then `e2e` run in the
copy (`e2e` runs even when `check` fails), each with a timeout that kills the whole process group, in a reduced environment (PATH, HOME,
LANG, LC_*, TMPDIR; nothing named like a key, token, secret, password or credential, nothing starting with OPENCODE_). There is no sandbox:
a command that writes into the project by its absolute path cannot be stopped, so what is watched (the copied files, `.git/hooks`,
`.git/config`, `.git/HEAD`, `.git/refs` and `.opencode/artifacts`, each by size, times and inode; not `.opencode/graph`) is compared
before and after, and a change is reported and fails the run.

The result is saved as `.opencode/artifacts/T/N/review-copy-STAMP.json` (and `.txt`, the last 200 lines of each command's output) and
recorded on the node as `review_copy` under the graph's lock, by the rules `graph set` uses. The node's status and events are never
changed. One line is printed. Exit 0: every command that exists exited 0 and the project was unchanged; 1: a command failed or timed
out, or the project changed; 2: refused, or an error (including a result that could not be recorded). SIGTERM and SIGHUP stop the
commands, remove the copy and exit 2: give the shell call that runs this a timeout longer than `--timeout`.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import check, graphedit, osfs, osproc, outputs, transitions
from .noderun import SKIP as NODE_SKIP

DEFAULT_TIMEOUT = 600
HASH_LIMIT = 4 * 1024 * 1024  # Windows: files up to this size are compared by content too (see project_listing)
FILE_LIMIT = 20000
BYTE_LIMIT = 200 * 1024 * 1024
WATCH_LIMIT = 100000  # entries of .git/hooks, .git/refs and .opencode/artifacts compared before and after
TAIL_LINES = 200
TAIL_BYTES = 256 * 1024
SHOWN = 10  # changed files named in the line and the result
SKIP = NODE_SKIP | {".git"}
LEFT_OUT = (".opencode/artifacts", ".opencode/loops", ".opencode/handoff")
GRAPH_FOLDER = ".opencode/graph"  # copied, but not watched: `graph set` writes there while a run is going
SECRET_WORDS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
COMMANDS = ("check", "e2e")
# The project's own git config can start a program (core.fsmonitor): every git call here turns the two settings off.
GIT = ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false"]
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class Refused(Exception):
    """Nothing was run, or the run could not be recorded: the message is plain words for the person."""


class Stopped(KeyboardInterrupt):
    """SIGTERM or SIGHUP: raised in the signal handler so the process groups are killed and the copy removed."""


# ---- the node ----

def _git(args: list[str], cwd: Path, timeout: float = 120):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        return subprocess.run([*GIT, *args], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _linked(path: Path) -> bool:
    try:
        return osfs.is_link(osfs.lstat(path))
    except OSError:
        return False


def _nearest_project(start: Path) -> Path | None:
    """The folder at or above `start` that holds `.opencode/graph`; Refused when that folder or `.opencode` is a link."""
    for folder in (start, *start.parents):
        try:
            opencode = os.lstat(folder / ".opencode")
            graph = os.lstat(folder / GRAPH_FOLDER)
        except OSError:
            continue
        if osfs.is_link(opencode) or osfs.is_link(graph):
            raise Refused(f"{folder / GRAPH_FOLDER} is (or is below) a link; review-copy does not follow it")
        if stat.S_ISDIR(graph.st_mode):
            if graph.st_uid != osfs.getuid():
                raise Refused(f"{folder / GRAPH_FOLDER} is owned by another user; review-copy does not use it")
            return folder
    return None


def _git_root(start: Path) -> Path | None:
    done = _git(["rev-parse", "--show-toplevel"], start, 30)
    if done is None or done.returncode != 0:
        return None
    text = done.stdout.decode("utf-8", "surrogateescape").strip()
    return Path(text).resolve() if text else None


def _locate(task: str, directory) -> tuple[Path, Path]:
    """(the graph file, the project it belongs to). The project is the current folder's (the nearest `.opencode/graph`); `--dir`
    may only name it or the root of the git work tree the current folder is in. There is no other place to look."""
    if not outputs._component(task):
        raise Refused(f"{task[:80]!r} is not a task name")
    here = Path.cwd().resolve()
    nearest = _nearest_project(here)
    if directory is None:
        if nearest is None:
            raise Refused("there is no .opencode/graph in the current folder or above it; run review-copy from the project")
        project = nearest
    else:
        given = Path(directory).resolve()
        root = _git_root(here)
        if given not in [p for p in (nearest, root) if p is not None]:
            raise Refused(f"--dir {directory} is not this folder's project: it must be "
                          + (f"{nearest}" if nearest else "the folder that holds .opencode/graph")
                          + (f" or the git work tree's root {root}" if root and root != nearest else ""))
        project = given
    if _linked(project / ".opencode") or _linked(project / GRAPH_FOLDER):
        raise Refused(f"{project / GRAPH_FOLDER} is (or is below) a link; review-copy does not follow it")
    try:
        path = outputs.find_graph(task, [project / GRAPH_FOLDER])
    except outputs.OutputError as exc:
        raise Refused(str(exc)) from None
    return path, project


def _node(raw: dict, node_id: str) -> dict:
    try:
        return graphedit._find(raw, node_id)
    except outputs.OutputError as exc:
        raise Refused(str(exc)) from None


def _commands(node: dict) -> dict[str, str]:
    """The node's commands by name; Refused when one is not text or the node has none."""
    found = {}
    for name in COMMANDS:
        value = node.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise Refused(f"node {node.get('id')}: {name} must be a non-blank string")
        found[name] = value
    if not found:
        raise Refused(f"node {node.get('id')} has no check and no e2e in the graph, so there is nothing to run "
                      "(commands are never taken from the command line)")
    return found


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


# ---- the files ----

def _git_files(project: Path) -> list[str] | None:
    done = _git(["ls-files", "-co", "--exclude-standard", "-z"], project)
    if done is None or done.returncode != 0:
        return None
    found = list(dict.fromkeys(p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p))
    return found or None  # a project that git ignores as a whole is walked instead


def _walk_files(project: Path) -> list[str]:
    """Every file below the project but the skipped folders, refused as soon as the walk goes over a cap."""
    found: list[str] = []
    total = 0
    for folder, dirs, names in os.walk(project):
        rel = os.path.relpath(folder, project)
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not _left_out(_posix(os.path.normpath(os.path.join(rel, d))))
                         and not os.path.exists(os.path.join(folder, d, "pyvenv.cfg")))
        for name in names + [d for d in dirs if os.path.islink(os.path.join(folder, d))]:
            path = _posix(os.path.normpath(os.path.join(rel, name)))
            found.append(path)
            try:
                total += os.lstat(project / path).st_size
            except OSError:
                pass
            _caps(len(found), total)
    return found


def _posix(path: str) -> str:
    """A relative path written with `/`, as git lists it and as the results and the tests name it (Windows walks with `\\`)."""
    return path.replace(os.sep, "/") if os.sep != "/" else path


def _left_out(rel: str) -> bool:
    return any(rel == prefix or rel.startswith(prefix + "/") for prefix in LEFT_OUT)


def _caps(count: int, total: int) -> None:
    if count > FILE_LIMIT:
        raise Refused(f"the project has more than {FILE_LIMIT} files to copy; review-copy refuses a project that big "
                      "(node_modules, virtualenvs, .git and git-ignored files are already left out)")
    if total > BYTE_LIMIT:
        raise Refused(f"the files to copy are over {BYTE_LIMIT // (1024 * 1024)} MiB; review-copy refuses a project that big")


def project_files(project: Path) -> list[str]:
    """The relative paths review-copy copies, sorted: in a git work tree `git ls-files -co --exclude-standard`, else a walk; either
    way without the skipped folders, virtualenvs and what is left out. Refused over the file-count and size caps."""
    listed = _git_files(project)
    if listed is None:
        files = _walk_files(project)
    else:
        venvs = tuple(os.path.dirname(p) + "/" for p in listed if os.path.basename(p) == "pyvenv.cfg" and os.path.dirname(p))
        files = [rel for rel in listed
                 if not any(part in SKIP for part in rel.split("/")) and not _left_out(rel) and not rel.startswith(venvs)]
    total = 0
    for i, rel in enumerate(files, 1):
        try:
            total += os.lstat(project / rel).st_size
        except OSError:
            continue
        _caps(i, total)
    return sorted(files)


def _git_common_dir(project: Path) -> Path | None:
    done = _git(["rev-parse", "--git-common-dir"], project, 30)
    if done is None or done.returncode != 0:
        return None
    text = done.stdout.decode("utf-8", "surrogateescape").strip()
    return Path(os.path.normpath(project / text)) if text else None


def _extras(project: Path) -> list[str]:
    """What is watched besides the copied files: `.git/config`, `.git/HEAD`, `.git/hooks`, `.git/refs` and every entry of
    `.opencode/artifacts` (links included, never followed)."""
    found: list[str] = []

    def key(path) -> str:
        return _posix(os.path.relpath(path, project)) if _inside(os.path.realpath(path), str(project)) else str(path)

    def walk(rel) -> None:
        for folder, dirs, names in os.walk(project / rel):
            found.extend(key(os.path.join(folder, name)) for name in names)
            found.extend(key(os.path.join(folder, d)) for d in dirs if os.path.islink(os.path.join(folder, d)))
            if len(found) > WATCH_LIMIT:
                raise Refused(f"{rel} has more than {WATCH_LIMIT} entries; review-copy cannot watch it")

    try:
        git = os.lstat(project / ".git")
    except OSError:
        git = None
    if git is not None and stat.S_ISDIR(git.st_mode):
        found += [rel for rel in (".git/config", ".git/HEAD") if os.path.lexists(project / rel)]
        walk(".git/hooks")
        walk(".git/refs")
    elif git is not None:
        found.append(".git")  # a linked work tree's pointer to its git folder: the common git folder is watched too
        common = _git_common_dir(project)
        if common is not None:
            found += [str(common / name) for name in ("config",) if os.path.lexists(common / name)]
            walk(common / "hooks")
            walk(common / "refs")
    walk(".opencode/artifacts")
    return found


def project_listing(project: Path, files: list[str]) -> dict[str, tuple]:
    """What is compared before and after: each watched path's size, modification and change times and inode. The copied files
    but not `.opencode/graph`, plus `_extras`. A path that is not there is left out (listed by git as deleted)."""
    found = {}
    for rel in [f for f in files if not (f == GRAPH_FOLDER or f.startswith(GRAPH_FOLDER + "/"))] + _extras(project):
        try:
            st = os.lstat(project / rel)
        except OSError:
            continue
        found[rel] = (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino)
        if osproc.is_windows() and stat.S_ISREG(st.st_mode) and st.st_size <= HASH_LIMIT:
            # there `st_ctime` is the creation time, not the change time: a same-size rewrite with the old modification time put back would
            # not show in the numbers above, so the content is part of what is compared
            try:
                found[rel] += (hashlib.sha256((project / rel).read_bytes()).hexdigest(),)
            except OSError:
                pass
    return found


def project_digest(listing: dict[str, tuple]) -> str:
    h = hashlib.sha256()
    for rel in sorted(listing):
        h.update(f"{rel}\0{listing[rel]}\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def _inside(real: str, root: str) -> bool:
    return real == root or real.startswith(root + os.sep)


def _place_links(links: list[tuple[str, str]], target: Path) -> tuple[list[tuple[str, str]], int]:
    """Make the symlinks, after every regular file is there; (the ones kept, the count left out). A link is left out when
    something is already at its place (a case-insensitive disk, so nothing is written through a link), when it is absolute, or
    when its real path ends up outside the copy; the last is checked after all links are made, until no link is removed."""
    root = os.path.realpath(target)
    placed: list[tuple[str, str]] = []
    skipped = 0
    for rel, text in sorted(links):
        dest = target / rel
        if os.path.isabs(text) or os.path.lexists(dest) or not _inside(os.path.realpath(dest.parent), root):
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(text, dest)
        except (OSError, NotImplementedError):
            if not osproc.is_windows():
                raise
            skipped += 1  # Windows makes a symlink only for a user with the privilege: the link is left out, and counted as such
            continue
        placed.append((rel, text))
    moved = True
    while moved:
        moved = False
        for entry in list(placed):
            if not _inside(os.path.realpath(target / entry[0]), root):
                os.unlink(target / entry[0])
                placed.remove(entry)
                skipped += 1
                moved = True
    return placed, skipped


def _copy(project: Path, files: list[str], target: Path) -> tuple[str, int, int, int]:
    """Copy the files; (tree digest, files copied, bytes, symlinks left out). Symlinks are made last (`_place_links`)."""
    tree = hashlib.sha256()
    count = size = 0
    links = []
    for rel in files:
        source, dest = project / rel, target / rel
        try:
            if os.path.islink(source):
                links.append((rel, os.readlink(source)))
                continue
            if not os.path.isfile(source):
                continue  # a folder (a submodule), a socket or a file that went away
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        except FileNotFoundError:
            continue
        digest = hashlib.sha256()
        with open(dest, "rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
        tree.update(f"F {rel}\0{digest.hexdigest()}\n".encode("utf-8", "surrogateescape"))
        count += 1
        size += dest.stat().st_size
    placed, skipped = _place_links(links, target)
    for rel, text in placed:
        tree.update(f"L {rel}\0{text}\n".encode("utf-8", "surrogateescape"))
    return tree.hexdigest(), count + len(placed), size, skipped


# ---- running ----

def reduced_environment(tmp: str, source=None, windows=None) -> dict[str, str]:
    """PATH, HOME, LANG, LC_* and TMPDIR; nothing named like a secret and nothing starting with OPENCODE_. On Windows also the names
    programs need to start there (`osproc.base_env_names`: SYSTEMROOT, PATHEXT, USERPROFILE, ...), and TEMP and TMP point at `tmp`."""
    on_windows = osproc.is_windows() if windows is None else windows
    source = os.environ if source is None else source
    kept = {}
    for name, value in source.items():
        upper = name.upper()
        if upper.startswith("OPENCODE_") or any(word in upper for word in SECRET_WORDS):
            continue
        if name in ("PATH", "HOME", "LANG") or name.startswith("LC_") or (on_windows and upper in osproc.base_env_names(True)):
            kept[name] = value
    if on_windows:
        root = source.get("SYSTEMROOT") or source.get("SystemRoot") or "C:\\Windows"
        kept.setdefault("PATH", os.pathsep.join([os.path.join(root, "System32"), root]))
        kept["TEMP"] = kept["TMP"] = tmp
    else:
        kept.setdefault("PATH", "/usr/bin:/bin")
    kept["TMPDIR"] = tmp
    return kept


def _kill_group(pid: int, job=None) -> None:
    if job is not None and job.terminate():  # Windows: everything the command started, MSYS programs too
        return
    try:
        osproc.kill_group(pid, force=True)
    except (ProcessLookupError, PermissionError):
        pass


def _wait(proc: subprocess.Popen, timeout: float) -> bool:
    """Wait until the command ends (True) or the timeout passes (False), without reaping it: the process group id is the
    command's pid, and it can be handed out again once the command is reaped, so the group is killed before that."""
    if not hasattr(os, "waitid"):
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False
    deadline = time.monotonic() + timeout
    while True:
        try:
            done = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        if done is not None and done.si_pid:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def _tail(path: Path) -> str:
    with open(path, "rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - TAIL_BYTES))
        data = stream.read()
    text = _CONTROL.sub("?", data.decode("utf-8", "replace").replace("\r\n", "\n"))
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def run_command(command: str, cwd: Path, env: dict, timeout: float, log: Path) -> dict:
    """Run one command under /bin/sh in `cwd` (on Windows: `sh` when one is on PATH, else `cmd /d /s /c`; see `osproc.shell_command`), its output (both streams) in `log`. The whole process group is killed on a timeout
    and, when the command ends, whatever it left running. exit is None for a timeout (and, with "error" set, for a command that was not run: see below).
    On Windows `taskkill /T` walks the tree through parent ids, and once the shell has exited its children no longer point at it, so a
    grandchild the command left behind (a server started with `&`, a detached job) is NOT stopped there, where POSIX `killpg` stops the
    whole group; a copy that must be clean afterwards cannot rely on this clean-up. A multi-line command with no `sh` on PATH is refused
    (cmd would run only its first line): no process starts, the log says why, exit is None and "error" holds the reason."""
    began = time.monotonic()
    try:
        argument = osproc.shell_command(command, posix_shell="/bin/sh")
    except osproc.ShellRefused as exc:  # Windows without an sh: a command cmd cannot run whole is a failed command, with the reason in its log
        Path(log).write_text(f"the command was not run: {exc}\n", encoding="utf-8")
        return {"command": command, "sha256": _sha(command), "exit": None, "timed_out": False, "seconds": 0.0, "error": str(exc)}
    with open(log, "wb") as out:
        proc = subprocess.Popen(argument, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT, **osproc.group_kwargs())
        job = osproc.Job()
        job.adopt(proc)
        try:
            try:
                ended = _wait(proc, timeout)
            except BaseException:
                _kill_group(proc.pid, job)
                proc.wait()
                raise
            _kill_group(proc.pid, job)
            code = proc.wait()
        finally:
            job.close()
    return {"command": command, "sha256": _sha(command), "exit": code if ended else None, "timed_out": not ended,
            "seconds": round(time.monotonic() - began, 2)}


# ---- the result ----

def _open_result_folder(project: Path, task: str, node_id: str) -> int:
    """`.opencode/artifacts/T/N` opened from the project, made when missing, never through a link."""
    if not outputs._component(node_id):
        raise Refused(f"{node_id[:80]!r} is not a node id")
    current = osfs.open(project, os.O_RDONLY | osfs.O_DIRECTORY)
    try:
        for name in (".opencode", "artifacts", task, node_id):
            following = outputs._open_dir(current, name, create=True)
            osfs.close(current)
            current = following
    except outputs.OutputError as exc:
        osfs.close(current)
        raise Refused(f"the result was not written: {exc}") from None
    except BaseException:
        osfs.close(current)
        raise
    return current


def _create(folder: int, name: str) -> int:
    return osfs.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | osfs.O_NOFOLLOW, 0o644, dir_fd=folder)


def _stamped(folder: int) -> tuple[str, int, int]:
    """(the stamp, the .json's and the .txt's open files): the first stamp for which neither file exists."""
    base = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1000):
        stamp = base if n == 0 else f"{base}-{n}"
        try:
            text = _create(folder, f"review-copy-{stamp}.txt")
        except FileExistsError:
            continue
        try:
            return stamp, _create(folder, f"review-copy-{stamp}.json"), text
        except FileExistsError:
            osfs.close(text)
            osfs.unlink(f"review-copy-{stamp}.txt", dir_fd=folder)
    raise Refused("too many review-copy results at the same second")


def _record(path: Path, node_id: str, record: dict, now: float) -> None:
    """Set the node's `review_copy` under the graph's lock, with the validation `graph set` uses; its status and events are
    not touched (`updated` goes up, as for any write). A problem the end-to-end rule finds (a passed node whose new result says
    it failed) does not stop the write: it is what `check` has to report."""
    with outputs._GraphFolder(path.parent) as folder:
        def make(current: bytes) -> bytes:
            raw = outputs._parse(current, path.name)
            try:
                previous = transitions.load_state(folder, path.stem)
            except transitions.HistoryError:
                previous = None
            before_state = previous if previous is not None else transitions.state_of(raw)
            before = graphedit._problems(raw, path, previous)
            new = json.loads(json.dumps(raw))
            _node(new, node_id)["review_copy"] = record
            new["updated"] = graphedit._updated(new, now)
            after = graphedit._problems(new, path, before_state)
            added = [problem for problem in after if problem not in before and check.E2E_RULE not in problem]
            if added:
                raise graphedit.Refused("this write would add " + ("a problem" if len(added) == 1 else f"{len(added)} problems")
                                        + f" to {path.name}: " + "; ".join(added))
            make.state, make.clean = transitions.state_of(new), not after
            return outputs._serialize(new)
        outputs._replace_graph(folder, path.name, make)
        if make.clean:
            try:
                transitions.save_state(folder, path.stem, make.state)
            except OSError:
                pass


def _rmtree(path: Path) -> None:
    def fix(function, target, _error):
        try:
            if osproc.is_windows():  # deleting fails on a read-only file there; no other mode bit means anything
                for item in (os.path.dirname(target), target):
                    Path(item).chmod(stat.S_IWRITE | stat.S_IREAD)
            else:
                osfs.chmod(os.path.dirname(target), 0o700)
                osfs.chmod(target, 0o700)
            function(target)
        except OSError:
            pass
    shutil.rmtree(path, onerror=fix)


@contextlib.contextmanager
def _signals_blocked():
    """SIGTERM and SIGHUP are held back (not lost) inside the block; one that came is delivered, to the handler, when it ends."""
    try:
        old = signal.pthread_sigmask(signal.SIG_BLOCK, set(osproc.term_signals()))
    except (AttributeError, ValueError):
        yield
        return
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


@contextlib.contextmanager
def _signals_ignored():
    """Cleanup is not interrupted by a second SIGTERM or SIGHUP."""
    old = {}
    for sig in osproc.term_signals():
        try:
            old[sig] = signal.signal(sig, signal.SIG_IGN)
        except ValueError:  # not the main thread
            pass
    try:
        yield
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def _printable(text: str) -> str:
    """Safe to print: control characters and lone surrogates (a file name that is not UTF-8) shown as escapes."""
    return _CONTROL.sub("?", text).encode("utf-8", "backslashreplace").decode("utf-8")


def review_copy(task: str, node_id: str, directory=None, timeout: float = DEFAULT_TIMEOUT, keep: bool = False) -> tuple[int, str]:
    """Do the whole thing; (exit code, the one line). Refused for what cannot be run."""
    path, project = _locate(task, directory)
    raw = outputs._parse(path.read_bytes(), path.name)
    node = _node(raw, node_id)
    commands = _commands(node)
    revision = raw["revision"] if type(raw.get("revision")) is int else 1
    scratch = Path(tempfile.mkdtemp(prefix="agentgraph-review-copy-")).resolve()
    try:
        if scratch == project or scratch.is_relative_to(project) or project.is_relative_to(scratch):
            raise Refused(f"the temp folder {scratch.parent} is inside the project (or the other way round); set TMPDIR elsewhere")
        started = round(time.time(), 3)  # the copy is of the project as it is now: a later change to it makes the result older
        files = project_files(project)
        if not files:
            raise Refused("the project has no files to copy")
        before = project_listing(project, files)
        before_digest = project_digest(before)
        copy, tmp, logs = scratch / "copy", scratch / "tmp", scratch / "logs"
        for folder in (copy, tmp, logs):
            folder.mkdir()
        tree, count, size, links = _copy(project, files, copy)
        env = reduced_environment(str(tmp))
        results = {}
        for name, command in commands.items():
            results[name] = run_command(command, copy, env, timeout, logs / f"{name}.log")
            results[name]["output"] = _tail(logs / f"{name}.log")
        not_checked = None
        try:
            after = project_listing(project, project_files(project))
            unchanged = project_digest(after) == before_digest
            changed = sorted(f for f in set(before) | set(after) if before.get(f) != after.get(f))
        except (Refused, OSError) as exc:  # the project grew past a cap while the commands ran: it cannot be said to be unchanged
            not_checked = str(exc)
            unchanged, changed = False, []
        now = round(time.time(), 3)
        recorded = False
        late = None
        try:
            with _signals_blocked():  # a SIGTERM or SIGHUP waits until the result files and the node are written
                folder = _open_result_folder(project, task, node_id)
                json_fd = text_fd = -1
                names: list[str] = []
                try:
                    stamp, json_fd, text_fd = _stamped(folder)
                    json_name, text_name = f"review-copy-{stamp}.json", f"review-copy-{stamp}.txt"
                    names = [json_name, text_name]
                    rel_json = f".opencode/artifacts/{task}/{node_id}/{json_name}"
                    current = _node(outputs._parse(path.read_bytes(), path.name), node_id)
                    graph_same = all(current.get(name) == command for name, command in commands.items())
                    result = {
                        "task": task, "node": node_id, "started": started, "at": now, "project": str(project), "graph_revision": revision,
                        "graph_commands_unchanged": graph_same,
                        "commands": [{"name": name, **{k: v for k, v in r.items() if k != "output"}} for name, r in results.items()],
                        "tree": tree, "files": count, "bytes": size, "symlinks_left_out": links,
                        "project_unchanged": unchanged, "project_not_checked": not_checked, "project_digest": before_digest,
                        "project_changed_files": changed[:SHOWN * 2],
                        "timeout": timeout, "environment": sorted(env), "copy": str(copy) if keep else None, "text": text_name,
                    }
                    with os.fdopen(json_fd, "w", encoding="utf-8") as out:
                        json_fd = -1
                        out.write(json.dumps(result, indent=2, ensure_ascii=True) + "\n")
                    with os.fdopen(text_fd, "w", encoding="utf-8", errors="backslashreplace") as out:
                        text_fd = -1
                        for name, r in results.items():
                            out.write(f"=== {name}: {_CONTROL.sub('?', r['command'])}\n=== sha256 {r['sha256']}  exit {r['exit']}  "
                                      f"{'TIMED OUT  ' if r['timed_out'] else ''}{r['seconds']}s  (last {TAIL_LINES} lines)\n{r['output']}\n\n")
                except BaseException:
                    for fd in (json_fd, text_fd):
                        if fd >= 0:
                            osfs.close(fd)
                    json_fd = text_fd = -1
                    for name in names:  # no half-written result is left behind
                        with contextlib.suppress(OSError):
                            osfs.unlink(name, dir_fd=folder)
                    raise
                finally:
                    osfs.close(folder)
                record = {"result": rel_json, "check_exit": results.get("check", {}).get("exit"), "e2e_exit": results.get("e2e", {}).get("exit"),
                          "tree": tree, "started": started, "at": now, "project_unchanged": unchanged}
                for name, r in results.items():
                    record[f"{name}_sha256"] = r["sha256"]
                try:
                    _record(path, node_id, record, now)
                except (graphedit.Refused, outputs.OutputError) as exc:
                    raise Refused(f"the result is saved in {rel_json} but the node was not updated: {exc}") from None
                recorded = True
        except Stopped as exc:
            if not recorded:
                raise
            late = exc  # it arrived while the result was being saved, and was held until the node was written
        parts = []
        for name, r in results.items():
            seconds = f"{r['seconds']:.0f}s"
            parts.append(f"{name} timed out ({seconds})" if r["timed_out"] else
                         f"{name} not run: {r['error']}" if r.get("error") else f"{name} exit {r['exit']} ({seconds})")
        line = f"review-copy {task}/{node_id}: " + ", ".join(parts) + f", result {rel_json}"
        if not_checked:
            line += f", PROJECT NOT CHECKED AFTER THE RUN ({not_checked})"
        elif not unchanged:
            line += f", PROJECT CHANGED ({len(changed)} file{'s' if len(changed) != 1 else ''}: {', '.join(changed[:3])})"
        if not graph_same:
            line += ", the node's commands changed in the graph meanwhile"
        if keep:
            line += f", copy {copy}"
        if late is not None:
            line += f", {late} arrived while the result was being saved: it is saved and recorded"
        ok = unchanged and all(r["exit"] == 0 and not r["timed_out"] for r in results.values())
        return (0 if ok else 1), _printable(line)
    except KeyboardInterrupt as exc:
        exc.kept = str(scratch) if keep else None
        raise
    finally:
        if not keep:
            with _signals_ignored():
                _rmtree(scratch)


# ---- the command line ----

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentgraph review-copy", allow_abbrev=False, description=__doc__.splitlines()[0],
        epilog="The commands are the node's own `check` and `e2e` in the graph; none can be given here. Each runs for up to --timeout "
               "seconds: give the shell call that runs this a timeout longer than --timeout (twice that when the node has both "
               "commands), or it is stopped (SIGTERM) first and exits 2 without a result.")
    parser.add_argument("--task", required=True, metavar="T")
    parser.add_argument("--node", required=True, metavar="N")
    parser.add_argument("--dir", type=Path, metavar="PROJECT",
                        help="only this folder's project (the nearest .opencode/graph's) or the root of its git work tree (default: the first)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS",
                        help=f"for each command (default {DEFAULT_TIMEOUT}); the caller's own timeout must be longer")
    parser.add_argument("--keep", action="store_true", help="keep the copy and print where it is")
    return parser


def _stop(signum, _frame):
    raise Stopped(signal.Signals(signum).name)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    if not args.timeout > 0 or args.timeout == float("inf"):
        print("review-copy: --timeout must be a number of seconds above 0")
        return 2
    old = {}
    for sig in osproc.term_signals():
        try:
            old[sig] = signal.signal(sig, _stop)
        except ValueError:  # not the main thread: the caller owns the signals
            pass
    try:
        code, line = review_copy(args.task, args.node, args.dir, args.timeout, args.keep)
    except Refused as exc:
        print(_printable(f"review-copy: {exc}"))
        return 2
    except KeyboardInterrupt as exc:
        why = f"stopped by {exc}" if isinstance(exc, Stopped) else "interrupted"
        kept = getattr(exc, "kept", None)
        print(f"review-copy: {why}; the commands were killed and " + (f"the copy was kept in {kept}" if kept else "the copy was removed")
              + "; no result was recorded")
        return 2
    except (OSError, subprocess.SubprocessError, outputs.OutputError, ValueError) as exc:
        print(_printable(f"review-copy: error: {exc}"))
        return 2
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
