"""The side chat: a small read-only agent the user can ask about the running task (the view's "moon").

    agentgraph sidechat ask --main SESSION --text-file F|- [--full] [--json] [--demo]
    agentgraph sidechat status [--main SESSION] [--json] [--demo]
    agentgraph sidechat brief --main SESSION [--json] [--demo]
    agentgraph sidechat install [--with-skill] [--agent-file F] [--skill-file F] [--json]
    agentgraph sidechat uninstall [--json]
    agentgraph sidechat undo RECORD [--json]

**What it is.** One native agent, `side-chat` (a file in OpenCode's agents folder; the proposal is
`config/side-chat/agent.md`), with every tool denied: no file tool, no
shell, no web, no sub-agent. It talks only to the user. This module is the only thing that asks it anything.

**How it gets its information: not through tools.** Each question is sent with a brief this module builds, capped
at `BRIEF_LIMIT` (24,000) characters of plain text: the task graph in short (objectives; each node's id, type, agent,
status, goal and evidence, capped), the list of the task's artifact files, the capped head of an artifact the
question names (a node id, a file name or path; only files under the task's `.opencode/artifacts/<task>/`, reached
without following a link, text only), and the recent main chat (`chatfeed.digest`: reasoning, messages and tool NAMES,
never a tool's input or output). Everything passes `chatfeed.clean` (control characters out, obvious secrets
masked). A later question in the same side session sends a part again only when it changed: an unchanged section is
replaced by one line, the main chat by the lines after the ones already sent (`--full` sends it all again).

**How it runs.** `opencode-cli run [--standalone] --agent side-chat --model M --format json [--session ID]` in the
project folder, by one of two routes chosen for each question. **service** (no `--standalone`): the shared background
service answers; it holds the providers and credentials, so this is the route that works from an app whose own
process has none (the Desktop app). It is taken only when a service is running (`ps`, a fixed argument list, a short
timeout, nothing from the question or the project) AND its executable is the very file this module is about to run:
a CLI of another version attached to the shared service would restart that service and kill the user's running
sessions. **standalone** (`--standalone`: a private server that takes its providers from the caller's environment) is
every other case: no service, another executable, any doubt. `AGENTGRAPH_SIDECHAT_ROUTE` = `service` | `standalone` |
`auto` (the default) overrides the choice; `service` while the executables differ is refused, never attached. The
first question has no `--session` and creates the side session, archived so
it stays out of the user's session list (its cost stays readable); its id is remembered per main session in
`<project>/.opencode/sidechat/<main>.json` (git-ignored with the rest of `.opencode/`). One question at a time (an
flock; a second `ask` is refused, not queued). A question runs with a timeout; our `run` process and its children are
stopped when it passes (on the service route the service ends the turn itself when its client goes away: probed
with a 2 s timeout, the side session went idle at once, no more cost, and `opencode-cli` has no command to stop a
turn, so none is called). The model is `openai/gpt-6-luna`; when that run fails (exit code, an error event or no
text, not a timeout) the same question is asked once more with `anthropic/claude-haiku-5-5`. A standalone run that
fails with a provider or credential kind of error says so in its one-line error and what to do.

**Never the orchestrator.** The only command this module builds is `command()`; it names one session, the side
session it created, and only after checking that the id is not in the main session's tree (the main session, its
sub-agents, theirs): an id from the tree is refused, a stored id that is in the tree is refused, and a new id the host
reports that is in the tree is refused and not remembered. There is no code path that prompts, steers, forks,
continues or interrupts any other session, and no post to anything. The side session's own transcript is served to
the view (`side_feed`), with each question cut before its brief.

**Spend.** The side session's cost is read from `session_v2.cost` (a read-only query) and every answer reports what
that question cost (`step_finish` events). `status` and the snapshot's `sidechat.spent` show the total.

**Installing.** `install` writes the agent file (and with `--with-skill` the skill, and the one rule that lets the
agent load it) from the proposal files into the real config, with an undo record under
`<backups>/<date>-sidechat/`; it refuses over an existing file. `uninstall` removes only a file this module wrote
(its marker line), keeping a backup. `undo RECORD` reverses either. Every folder is a parameter, so tests run on
scratch copies. Nothing here runs `install` by itself.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time

from . import agentfiles, chatfeed, osfs, osproc, outputs, skillfiles, skillset, sources

AGENT = "side-chat"
SKILL = "side-chat"
MARK = "# agentgraph: sidechat"
MODELS = ("openai/gpt-6-luna", "anthropic/claude-haiku-5-5")
DEFAULT_BINARY = "/Applications/OpenCode.app/Contents/Resources/opencode-cli"
ENV_BINARY = "OPENCODE_CLI"
ROUTE_ENV = "AGENTGRAPH_SIDECHAT_ROUTE"
ROUTES = ("service", "standalone")
PS_TIMEOUT = 3.0
CREDENTIAL = re.compile(r"Model unavailable|api-key|\b40[13]\b", re.IGNORECASE)
CREDENTIAL_HINT = ("the side chat ran on its own server, which has no providers here; open it from an app whose "
                   "background service is running")
QUESTION_LIMIT = 4000
BRIEF_LIMIT = 24000
ANSWER_LIMIT = 6000
TIMEOUT = 120.0  # for the whole question, fallback included: under the hosts' own 150 s kill, so this module stops its children first
GRAPH_CAP = 6500
LIST_CAP = 2500
HEAD_EACH = 3000
HEADS = 2
CHAT_CAP = 10000
FILES_LIMIT = 200
WALK_DEPTH = 5
PROPOSALS = Path(__file__).resolve().parent.parent / "config" / "side-chat"  # shipped with the toolkit
AGENT_PROPOSAL = PROPOSALS / "agent.md"
SKILL_PROPOSAL = PROPOSALS / "skill.md"
PROPOSAL_LIMIT = 16 * 1024
SEPARATOR = "\n\n--- brief"  # the question ends here; the view's copy of the side chat is cut at it
SECTIONS = ("graph", "artifacts", "heads", "chat")
ALLOWED_FLAGS = ("run", "--standalone", "--agent", "--model", "--format", "--session", "--archived", "--title")


class SidechatError(skillset.SkillsetError):
    """A refused request; the message says why and nothing was changed."""


def windows_binary(*, which=None, environ=None, isfile=None) -> str | None:
    """Where the OpenCode CLI is on Windows: the one on PATH (`opencode.exe`, or the `opencode.cmd` shim npm makes, found through
    PATHEXT), else the usual install places under the user's profile; None when none is there. Used only when OPENCODE_CLI is not set."""
    environ = os.environ if environ is None else environ
    isfile = isfile or os.path.isfile
    found = (which or shutil.which)("opencode")
    if found:
        return found
    places = []
    for variable, parts in (("LOCALAPPDATA", ("Programs", "OpenCode", "opencode-cli.exe")),
                            ("LOCALAPPDATA", ("Programs", "OpenCode", "opencode.exe")),
                            ("LOCALAPPDATA", ("OpenCode", "opencode-cli.exe")),
                            ("USERPROFILE", (".opencode", "bin", "opencode.exe")),
                            ("APPDATA", ("npm", "opencode.cmd")),
                            ("USERPROFILE", ("scoop", "shims", "opencode.exe"))):
        base = environ.get(variable)
        if base:
            places.append(os.path.join(base, *parts))
    return next((place for place in places if isfile(place)), None)


def binary(*, windows=None, which=None, environ=None, isfile=None) -> str:
    """OPENCODE_CLI when set; on Windows the CLI found by `windows_binary`; else the macOS app's CLI (also the last fallback on Windows,
    where starting it then fails with a message that names OPENCODE_CLI)."""
    environ_in_use = os.environ if environ is None else environ
    if environ_in_use.get(ENV_BINARY):
        return environ_in_use[ENV_BINARY]
    if osproc.is_windows() if windows is None else windows:
        found = windows_binary(which=which, environ=environ, isfile=isfile)
        if found:
            return found
    return DEFAULT_BINARY


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# --- state ----------------------------------------------------------------------------------------------------

def state_dir(project) -> Path:
    return Path(project) / ".opencode" / "sidechat"


def _state_path(project, main: str) -> Path:
    return state_dir(project) / f"{main}.json"


def _plain_dir(path: Path, *, create: bool = False) -> bool:
    try:
        return stat.S_ISDIR(osfs.lstat(path).st_mode)
    except FileNotFoundError:
        if not create:
            return False
        try:
            os.makedirs(path, 0o700, exist_ok=True)
        except OSError:
            return False
        return stat.S_ISDIR(osfs.lstat(path).st_mode)
    except OSError:
        return False


def load_state(project, main) -> dict:
    """The remembered side session of `main` in `project`: {side, model, asks, hashes, chat, ...}, or {}."""
    if not chatfeed.valid_session(main):
        return {}
    path = _state_path(project, main)
    try:
        fd = osfs.open(path, os.O_RDONLY | osfs.O_NOFOLLOW | osfs.O_NONBLOCK)
    except OSError:
        return {}
    try:
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return {}
            data = json.loads(stream.read(1 << 20).decode("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("main") != main or not chatfeed.valid_session(data.get("side")):
        return {}
    return data


def _save_state(project, state: dict) -> None:
    folder = state_dir(project)
    if not _plain_dir(folder.parent, create=True) or not _plain_dir(folder, create=True):
        raise SidechatError(f"{folder} is not a plain folder; the side session was not remembered")
    path = _state_path(project, state["main"])
    temporary = folder / f".{path.name}.{secrets.token_hex(6)}.tmp"
    fd = osfs.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | osfs.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(state, indent=1) + "\n")
    osfs.replace(temporary, path)


@contextmanager
def _lock(project, *, wait: bool = False):
    """One question at a time: an flock on `<project>/.opencode/sidechat/lock`, refused (not queued) when held."""
    folder = state_dir(project)
    if not _plain_dir(folder.parent, create=True) or not _plain_dir(folder, create=True):
        raise SidechatError(f"{folder} is not a plain folder")
    fd = osfs.open(folder / "lock", os.O_RDWR | os.O_CREAT | osfs.O_NOFOLLOW, 0o600)
    try:
        try:
            osfs.lock(fd, block=wait)
        except OSError:
            raise SidechatError("another question is being answered; ask again when it has finished") from None
        yield
    finally:
        osfs.close(fd)


def is_busy(project) -> bool:
    """Whether a question is being answered now (the lock is held). Creates nothing."""
    path = state_dir(project) / "lock"
    try:
        fd = osfs.open(path, os.O_RDWR | osfs.O_NOFOLLOW)
    except OSError:
        return False
    try:
        try:
            osfs.lock(fd, block=False)
        except OSError:
            return True
        return False
    finally:
        osfs.close(fd)


# --- what is read ---------------------------------------------------------------------------------------------

def session_cost(db_path, session) -> float | None:
    """The side session's cost so far (`session_v2.cost`, a read-only query), or None when unreadable."""
    if not chatfeed.valid_session(session) or not Path(db_path).exists():
        return None
    try:
        conn = sources._connect(db_path)
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute("SELECT cost FROM session_v2 WHERE id = ?", (session,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    finally:
        conn.close()
    value = row[0] if row else None
    return round(float(value), 6) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def tree_ids(snapshot) -> set[str]:
    """The main session's tree: the root and every sub-agent session below it."""
    ids = {snapshot.root.id} if snapshot.root is not None and snapshot.root.id else set()
    ids.update(item.id for item in snapshot.sessions if item.id)
    return ids


def _active_graph(snapshot, main):
    from .snapshot_json import _active_graph as pick, _time_key
    graphs = sorted(snapshot.graphs, key=lambda graph: _time_key(graph.updated), reverse=True)
    sessions = ([snapshot.root] if snapshot.root is not None else []) + snapshot.sessions
    return pick(graphs, sessions, None, main)[0]


def _cut(text, cap: int) -> str:
    text = " ".join(chatfeed.clean(text).split())
    return text if len(text) <= cap else text[:max(cap - 3, 0)].rstrip() + "..."


def _node_lines(graph, goal_cap: int, evidence_cap: int) -> list[str]:
    lines = []
    for node in graph.nodes:
        line = f"{_cut(node.id, 40)} [{_cut(node.type, 20)}] {_cut(node.agent, 30)} {_cut(node.status, 20)}"
        if node.attempts:
            line += f" attempts={node.attempts}"
        if node.stalled:
            line += " stalled"
        if node.verdict:
            line += f" verdict={_cut(node.verdict, 20)}"
        if node.failure_kind:
            line += f" failure={_cut(node.failure_kind, 30)}"
        if goal_cap and node.goal:
            line += f" | goal: {_cut(node.goal, goal_cap)}"
        if evidence_cap and node.evidence:
            line += f" | evidence: {_cut(node.evidence, evidence_cap)}"
        lines.append(line)
    return lines


def graph_text(graph) -> str:
    """The task graph in short, at most `GRAPH_CAP` characters: caps on each text shrink until it fits."""
    if graph is None:
        return "No task graph was found for this session."
    head = [f"task {_cut(graph.task, 80)} | status {_cut(graph.status, 20)} | revision {graph.revision} | "
            f"nodes {len(graph.nodes)}", f"goal: {_cut(graph.goal, 300)}"]
    objectives = [f"{_cut(o.id, 30)}: {_cut(o.text, 200)} [{' '.join(_cut(n, 30) for n in o.nodes[:12])}]"
                  for o in graph.objectives[:30]]
    for goal_cap, evidence_cap in ((160, 200), (100, 100), (60, 60), (0, 0)):
        nodes = _node_lines(graph, goal_cap, evidence_cap)
        text = "\n".join([*head, "objectives:", *objectives, "nodes (id [type] agent status):", *nodes])
        if len(text) <= GRAPH_CAP:
            return text
    kept, used = [], len("\n".join([*head, "objectives:", *objectives, "nodes (id [type] agent status):"])) + 80
    for line in nodes:  # still too long: the first nodes, then a count
        if used + len(line) + 1 > GRAPH_CAP:
            break
        kept.append(line)
        used += len(line) + 1
    more = f"(+{len(nodes) - len(kept)} more nodes not shown)"
    return "\n".join([*head, "objectives:", *objectives, "nodes (id [type] agent status):", *kept, more])[:GRAPH_CAP]


def _walk(project: Path, task: str) -> list[tuple[str, int]]:
    """[(project-relative path, size)] of the regular files under `.opencode/artifacts/<task>/`, never through a
    link, at most FILES_LIMIT, sorted by path."""
    try:
        task_fd = outputs._task_folder(project, task)
    except (OSError, outputs.OutputError):
        return []
    found: list[tuple[str, int]] = []
    flags = os.O_RDONLY | osfs.O_DIRECTORY | osfs.O_NOFOLLOW

    def go(fd: int, prefix: str, depth: int) -> None:
        try:
            listed = sorted(osfs.listdir(fd))
        except OSError:
            return
        for name in listed:
            if len(found) >= FILES_LIMIT or name.startswith("."):
                continue
            try:
                info = osfs.lstat(name, dir_fd=fd)
                if osfs.is_link(info):
                    continue
                if stat.S_ISREG(info.st_mode):
                    if info.st_nlink == 1:
                        found.append((f"{prefix}/{name}", info.st_size))
                elif stat.S_ISDIR(info.st_mode) and depth < WALK_DEPTH:
                    child = osfs.open(name, flags, dir_fd=fd)
                    try:
                        go(child, f"{prefix}/{name}", depth + 1)
                    finally:
                        osfs.close(child)
            except OSError:
                continue

    try:
        go(task_fd, f".opencode/artifacts/{task}", 0)
    finally:
        osfs.close(task_fd)
    return sorted(found)


def artifact_list(files) -> str:
    lines, used = [], 0
    for index, (path, size) in enumerate(files):
        line = f"{_cut(path, 200)} ({size} bytes)"
        if used + len(line) + 1 > LIST_CAP - 40:
            lines.append(f"(+{len(files) - index} more files)")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if lines else "No artifact files."


def _words(question: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z0-9_.\-/]+", question)}


def named_files(question: str, graph, files) -> list[str]:
    """The artifact files (at most HEADS) the question names: a path, or a file name together with the node it is
    under, or a node id (that node's declared artifacts, newest revision). A name shared by several nodes, with no
    node named, picks nothing."""
    if graph is None:
        return []
    words = _words(question)
    paths = [path for path, _ in files]
    nodes = [node for node in graph.nodes if node.id and node.id.lower() in words]
    picked: list[str] = []

    def add(path):
        if path in paths and path not in picked:
            picked.append(path)

    for path in paths:
        if path.lower() in words or path.lower().removeprefix(".opencode/artifacts/") in words:
            add(path)
    for node in nodes:
        mine = [path for path in paths if f"/{graph.task}/{node.id}/" in path]
        by_name = [path for path in mine if path.rsplit("/", 1)[-1].lower() in words]
        for path in by_name:
            add(path)
        if not by_name:
            for artifact in node.artifacts:
                add(artifact.path)
    if not nodes:
        for path in paths:
            name = path.rsplit("/", 1)[-1].lower()
            if name in words and sum(other.rsplit("/", 1)[-1].lower() == name for other in paths) == 1:
                add(path)
    return picked[:HEADS]


def read_head(project: Path, task: str, path: str, cap: int = HEAD_EACH) -> str | None:
    """The first `cap` characters of an artifact under the task's folder as clean text, or None (not there, a
    link, a hard link, not text)."""
    parts = outputs._inside(task, path)
    if parts is None:
        return None
    try:
        task_fd = outputs._task_folder(project, task)
    except (OSError, outputs.OutputError):
        return None
    try:
        folder = outputs._folder_of(task_fd, parts[:-1])
        try:
            stream, _ = outputs._open_file(folder, parts[-1])
        finally:
            osfs.close(folder)
    except (OSError, outputs.OutputError):
        return None
    finally:
        osfs.close(task_fd)
    if stream is None:
        return None
    with stream:
        data = stream.read(cap * 4 + 1)
    if b"\0" in data[:8192]:
        return None
    text = data.decode("utf-8", "replace")
    if "\ufffd" in text[:200]:
        return None
    text = chatfeed.clean(text).strip()
    return text[:cap] + (" ..." if len(text) > cap else "")


def _chat_part(digest: str, before: list[str]) -> tuple[str, list[str]]:
    """The main chat's section and the line hashes to remember. With the hashes of the lines already sent, only the
    lines after them are sent again (when the last three of them are found in order)."""
    lines = digest.split("\n")
    head, body = lines[0], lines[1:]
    hashes = [_sha(line) for line in body]
    text = digest
    tail = before[-3:]
    if len(tail) >= 1 and body:
        for end in range(len(body) - 1, len(tail) - 2, -1):
            if hashes[end - len(tail) + 1:end + 1] == tail:
                fresh = body[end + 1:]
                text = head + "\n" + ("(new since your last brief)\n" + "\n".join(fresh) if fresh
                                      else "(no new chat since your last brief)")
                break
    return text, hashes[-6:]


class Brief:
    def __init__(self):
        self.parts: dict[str, str] = {}
        self.sent: dict[str, str] = {}
        self.full = True
        self.chat_hashes: list[str] = []
        self.task = None
        self.named: list[str] = []

    def text(self, question: str) -> str:
        titles = {"graph": "task graph", "artifacts": "artifact files", "heads": "artifact start",
                  "chat": "main chat (recent, newest last)"}
        out = [f"Question from the user:\n{question}", "--- brief (all you know; the user did not write it) ---"]
        for name in SECTIONS:
            if self.parts.get(name):
                out.append(f"## {titles[name]}\n{self.parts[name]}")
        return "\n\n".join(out)[:BRIEF_LIMIT + QUESTION_LIMIT + 200]

    def sizes(self) -> dict:
        return {name: len(self.parts[name]) for name in SECTIONS if self.parts.get(name)}


def build_brief(question: str, snapshot, project, main: str, *, db_path, previous: dict | None = None,
                full: bool = False) -> Brief:
    """The brief for one question (see the module's description): at most BRIEF_LIMIT characters in all its
    sections. `previous` is the side session's remembered state; with it, a section that did not change is one
    line and the chat is only what is new."""
    previous = {} if full or not previous else previous
    sent = previous.get("sent") if isinstance(previous.get("sent"), dict) else {}
    brief = Brief()
    brief.full = not sent
    graph = _active_graph(snapshot, main)
    project_root = outputs.project_of(graph.path) if graph is not None else None
    project_root = Path(project_root) if project_root is not None else Path(project)
    brief.task = graph.task if graph is not None else None
    files = _walk(project_root, graph.task) if graph is not None else []
    brief.named = named_files(question, graph, files)
    sections = {"graph": graph_text(graph), "artifacts": artifact_list(files) if graph is not None else ""}
    heads, budget = [], HEADS * HEAD_EACH
    for path in brief.named:
        text = read_head(project_root, graph.task, path, min(HEAD_EACH, budget))
        if text is not None:
            heads.append(f"{path}:\n{text}")
            budget -= min(len(text), HEAD_EACH)
    sections["heads"] = "\n\n".join(heads)
    for name in ("graph", "artifacts", "heads"):
        text = sections[name]
        brief.sent[name] = _sha(text) if text else ""
        if text and sent.get(name) == brief.sent[name]:
            text = "(unchanged since your last brief)"
        brief.parts[name] = text
    used = sum(len(text) for text in brief.parts.values()) + 400
    chars = max(200, min(CHAT_CAP, BRIEF_LIMIT - used))
    digest = chatfeed.digest(main, chars, db_path=db_path)
    brief.parts["chat"], brief.chat_hashes = _chat_part(digest, previous.get("chat") or [])
    return brief


# --- the route ------------------------------------------------------------------------------------------------

def _windows_service_executables(run, windows: bool) -> list[str] | None:
    """The Windows form of `service_executables`: the process list comes from PowerShell (`osproc.list_processes`), and a service is a
    process whose command line is `<absolute executable> serve --service` (a path in double quotes, or one without spaces); a
    relative executable counts as "cannot be told"."""
    try:
        table = osproc.list_processes(windows=windows, runner=run, timeout=PS_TIMEOUT)
        found = []
        for row in table:
            words = (row.command or "").split()
            if words[-2:] != ["serve", "--service"]:
                continue
            split = osproc.split_executable(row.command)
            if split is None or not os.path.isabs(split[0]):
                return None
            if split[1].split() == ["serve", "--service"]:  # started as the executable itself, not through a shell
                found.append(split[0])
        return found
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def service_executables(*, run=subprocess.run, windows=None) -> list[str] | None:
    """The executables of the user's own running background services (`<exe> serve --service`), as `ps` reports
    them (on Windows: as PowerShell's process list reports them); [] when none runs; None when it cannot be told (a `ps`
    failure or timeout, an unreadable line). Fixed argument lists, a short timeout, no shell, and nothing from a question,
    snapshot or project."""
    if osproc.is_windows() if windows is None else windows:
        return _windows_service_executables(run, True)
    def ps(cmd):
        out = run(cmd, capture_output=True, text=True, timeout=PS_TIMEOUT, shell=False)
        if out.returncode != 0:
            raise OSError("ps failed")
        return out.stdout or ""
    try:
        lines = []
        for line in ps(["ps", "-x", "-o", "pid=,command="]).splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and parts[1].split()[-2:] == ["serve", "--service"]:
                lines.append((int(parts[0]), " ".join(parts[1].split())))
        found = []
        for pid, command in lines:
            path = ps(["ps", "-p", str(pid), "-o", "comm="]).strip()
            if not path.startswith("/"):
                return None
            if command == f"{path} serve --service":  # started as the executable itself, not `sh -c "... serve --service"`
                found.append(path)
        return found
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def choose_route(executable: str | None = None) -> tuple[str | None, str | None]:
    """(route, why-not): the route the next question takes, "service" or "standalone", or (None, a one-line reason)
    when the override asks for the service while it is not running from the file this module would run."""
    exe = executable or binary()
    wanted = (os.environ.get(ROUTE_ENV) or "auto").strip().lower()
    if wanted not in (*ROUTES, "auto"):
        return None, f"{ROUTE_ENV} must be service, standalone or auto"
    if wanted == "standalone":
        return "standalone", None
    found = service_executables()
    same = bool(found) and all(_same_file(path, exe) for path in found)
    if same:
        return "service", None
    if wanted == "service":
        return None, (f"{ROUTE_ENV}=service refused: no background service is running from {exe}; the side chat never "
                      "attaches a CLI of another version to the shared service")
    return "standalone", None


# --- running --------------------------------------------------------------------------------------------------

def command(*, model: str, side: str | None, title: str, tree: set[str], executable: str | None = None,
            route: str = "standalone") -> list[str]:
    """The command line (without the message) for one question. It names at most one session: the side session
    (`side`), refused when it is in the main session's tree. Nothing else in this module starts the host. Only the
    `standalone` route carries `--standalone`; the `service` route never does."""
    if route not in ROUTES:
        raise SidechatError("the route must be service or standalone")
    if side is not None:
        if not chatfeed.valid_session(side):
            raise SidechatError("the side session id is not a session id")
        if side in tree:
            raise SidechatError(f"{side} is a session of the task being watched; the side chat never uses one of those")
    if not isinstance(model, str) or not agentfiles.MODEL.fullmatch(model):
        raise SidechatError("the model must be provider/model")
    cmd = [executable or binary(), "run", *(["--standalone"] if route == "standalone" else []), "--agent", AGENT,
           "--model", model, "--format", "json"]
    cmd += ["--session", side] if side is not None else ["--archived", "--title", title]
    stray = [arg for arg in cmd[1:] if (arg.startswith("-") or arg == "run") and arg not in ALLOWED_FLAGS]
    if ("--standalone" in cmd) != (route == "standalone"):
        raise SidechatError("the service route never carries --standalone, and the standalone route always does")
    if stray:  # a model or title that looks like a flag would be read by the host as one
        raise SidechatError(f"the command line would carry a flag the side chat does not use: {_cut(stray[0], 40)}")
    return cmd


def _children(pid: int) -> list[int]:
    return osproc.children(pid)


def _stop(proc: subprocess.Popen) -> None:
    """Stop the process we started and its descendants (the standalone server is one), nothing else."""
    osproc.stop_family(proc, listing=_children)


def _events(output: str) -> dict:
    result = {"session": None, "text": [], "cost": 0.0, "tokens": {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0},
              "error": None, "tools": 0, "events": 0}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        result["events"] += 1
        sid = event.get("sessionID")
        if result["session"] is None and chatfeed.valid_session(sid):
            result["session"] = sid
        kind, part = event.get("type"), event.get("part") if isinstance(event.get("part"), dict) else {}
        if kind == "text" and isinstance(part.get("text"), str):
            result["text"].append(part["text"])
        elif kind == "step_finish":
            cost = part.get("cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                result["cost"] += float(cost)
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            for key, value in (("input", tokens.get("input")), ("output", tokens.get("output")),
                               ("cache_write", cache.get("write")), ("cache_read", cache.get("read"))):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    result["tokens"][key] += int(value)
        elif kind == "error":
            err = event.get("error") if isinstance(event.get("error"), dict) else event
            result["error"] = _cut(json.dumps(err.get("data", err.get("message", "error")))[:300], 160)
        elif isinstance(kind, str) and kind.startswith("tool"):
            result["tools"] += 1
    return result


def _run(cmd: list[str], message: str, cwd, timeout: float) -> tuple[int | None, dict, str]:
    """(exit code or None when stopped on the timeout, the parsed events, the first line of stderr). The message (the
    question and the brief, up to some 28,000 characters) goes to the host on stdin: `opencode run` reads it there when
    it is given no message argument (probed, `.opencode/artifacts/agentgraph-chat/n11/r2/stdin-probe.txt`), so it is
    not in the process list and does not count against the argument limit."""
    cwd = os.path.abspath(cwd)
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, errors="replace", **osproc.group_kwargs(),
                                **osproc.text_kwargs(), env={**os.environ, "PWD": str(cwd)})  # the host reads $PWD, not the process's cwd
    except OSError as exc:
        hint = f" (on Windows set {ENV_BINARY} to the full path of opencode.exe or opencode.cmd)" if osproc.is_windows() else ""
        raise SidechatError(f"cannot start {cmd[0]}: {exc.strerror}{hint}") from exc
    try:
        out, err = proc.communicate(input=message, timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        _stop(proc)
        out, err = proc.communicate()
        code = None
    first = next((line.strip() for line in chatfeed.clean(err).split("\n") if line.strip()), "")
    return code, _events(out or ""), first[:160]


def _answer(parts: list[str]) -> str:
    text = chatfeed.clean("\n".join(parts)).strip()
    return text if len(text) <= ANSWER_LIMIT else text[:ANSWER_LIMIT].rstrip() + " ..."


def clean_question(text) -> str:
    if not isinstance(text, str):
        raise SidechatError("the question must be text")
    text = chatfeed.plain(text).strip()
    if not text:
        raise SidechatError("the question is empty")
    if len(text) > QUESTION_LIMIT:
        raise SidechatError(f"the question is {len(text)} characters; at most {QUESTION_LIMIT}")
    return text


def _snapshot(project, main, db_path, config_dir, session_graph_dir):
    if not chatfeed.valid_session(main):
        raise SidechatError("--main must look like ses_ followed by letters and digits")
    graph_dir = Path(project) / ".opencode" / "graph"
    graph_dir = graph_dir if _plain_dir(graph_dir) else None  # else found from the project folder, as the view does
    snapshot = sources.build_snapshot(config_dir=config_dir, db_path=db_path, root_id=main, cwd=Path(project),
                                      graph_dir=graph_dir,
                                      session_graph_dir=None if graph_dir else session_graph_dir)
    if snapshot.root is None or main not in tree_ids(snapshot):
        raise SidechatError(f"{main} is not a session in the database; nothing was asked")
    return snapshot


def ask(question, *, main, project=None, db_path=None, config_dir=None, session_graph_dir=sources.ORCHESTRATION_GRAPH_DIR,
        model: str | None = None, timeout: float = TIMEOUT, full: bool = False, executable: str | None = None) -> dict:
    """Ask the side chat one question about the main session `main`. Returns {answer, session, model, fallback, route,
    brief: {chars, full, task, sections: {name: chars}, files}, cost, tokens, tools, seconds}. Refused
    (SidechatError) for a bad question or session, a second question at the same time, a timeout, or a failed run."""
    question = clean_question(question)
    project = Path(project) if project is not None else Path.cwd()
    db_path = Path(db_path) if db_path is not None else sources.DB_PATH
    config_dir = Path(config_dir) if config_dir is not None else sources.CONFIG_DIR
    started = time.time()
    route, why_not = choose_route(executable)
    if route is None:
        raise SidechatError(why_not)
    with _lock(project):
        snapshot = _snapshot(project, main, db_path, config_dir, session_graph_dir)
        tree = tree_ids(snapshot)
        state = load_state(project, main)
        side = state.get("side")
        brief = build_brief(question, snapshot, project, main, db_path=db_path, previous=state, full=full)
        message = brief.text(question)
        models = [model or MODELS[0]]
        if models[0] != MODELS[1]:
            models.append(MODELS[1])
        title = f"side chat for {main}"
        total_cost, tokens, attempt, last = 0.0, {}, 0, None
        deadline = time.monotonic() + timeout
        for index, chosen in enumerate(models):
            cmd = command(model=chosen, side=side, title=title, tree=tree, executable=executable, route=route)
            left = deadline - time.monotonic()
            if left <= 0:
                raise SidechatError(f"the side chat did not answer within {int(timeout)} seconds and was stopped")
            code, found, stderr = _run(cmd, message, project, left)
            total_cost += found["cost"]
            for key, value in found["tokens"].items():
                tokens[key] = tokens.get(key, 0) + value
            made = found["session"]
            if side is None and made is not None:
                if made in tree:
                    raise SidechatError(f"the host answered in {made}, a session of the task being watched; the "
                                        "side chat does not use it and did not remember it")
                side = made
                _remember(project, state, main, side, chosen, brief, found, total_cost)
            if code is None:
                raise SidechatError(f"the side chat did not answer within {int(timeout)} seconds and was stopped")
            answer = _answer(found["text"])
            if code == 0 and not found["error"] and answer:
                last = (chosen, found, answer, index > 0)
                break
            problem = found["error"] or stderr or (f"it exited with code {code}" if code else "it gave no answer")
            last = None
            if index == len(models) - 1:
                if route == "standalone" and CREDENTIAL.search(problem):
                    problem = f"{problem.rstrip('. ')}. {CREDENTIAL_HINT[0].upper()}{CREDENTIAL_HINT[1:]}"
                raise SidechatError(f"the side chat failed ({chosen}): {problem}")
        chosen, found, answer, fallback = last
        _remember(project, state, main, side, chosen, brief, found, total_cost, done=True)
    spent = session_cost(db_path, side)
    return {"answer": answer, "session": side, "model": chosen, "fallback": fallback, "route": route,
            "brief": {"chars": len(message), "full": brief.full, "task": brief.task, "sections": brief.sizes(),
                      "files": len(brief.named)},
            "cost": round(total_cost, 6), "tokens": tokens, "tools": found["tools"],
            "spent": spent, "seconds": round(time.time() - started, 1)}


def _remember(project, state, main, side, model, brief, found, cost, done: bool = False) -> None:
    now = {**state, "version": 1, "main": main, "side": side, "model": model,
           "created": state.get("created") or round(time.time(), 3)}
    if done:
        now.update(asks=int(state.get("asks") or 0) + 1, sent=brief.sent, chat=brief.chat_hashes,
                   updated=round(time.time(), 3), last_cost=round(cost, 6),
                   ask_cost=round(float(state.get("ask_cost") or 0) + cost, 6))
    _save_state(project, now)
    state.clear()
    state.update(now)


# --- what is shown ---------------------------------------------------------------------------------------------

def agent_installed(agents_dir=None, project=None) -> str | None:
    """"user" when the agent file is in `agents_dir`, "project" when it is in the project's own, else None."""
    for where, folder in (("user", agents_dir if agents_dir is not None else agentfiles.AGENTS_DIR),
                          ("project", Path(project) / ".opencode" / "agents" if project is not None else None)):
        if folder is not None:
            try:
                if stat.S_ISREG(osfs.lstat(Path(folder) / f"{AGENT}.md").st_mode):
                    return where
            except OSError:
                pass
    return None


def side_session(project, main) -> str | None:
    """The remembered side session of `main`, or None; never one that is in a tree (it cannot be: see `command`)."""
    return load_state(project, main).get("side")


def status(*, main=None, project=None, db_path=None, agents_dir=None, executable: str | None = None,
           with_route: bool = True) -> dict:
    """{installed, where, session, model, busy, spent?, asks, route, route_note?}: the snapshot's `sidechat` object
    plus `where`, `asks` and the `route` the next question would take (None, with the reason in `route_note`, when
    it would be refused). `spent` (US dollars) is the side session's cost when it can be read."""
    project = Path(project) if project is not None else Path.cwd()
    db_path = Path(db_path) if db_path is not None else sources.DB_PATH
    where = agent_installed(agents_dir, project)
    state = load_state(project, main) if main else {}
    side = state.get("side")
    out = {"installed": where is not None, "where": where, "session": side, "model": state.get("model") if side else None,
           "busy": is_busy(project), "asks": int(state.get("asks") or 0) if side else 0}
    if with_route:
        out["route"], note = choose_route(executable)
        if note:
            out["route_note"] = note
    spent = session_cost(db_path, side) if side else None
    if spent is None and side and isinstance(state.get("ask_cost"), (int, float)):
        spent = state["ask_cost"]
    if spent is not None:
        out["spent"] = spent
    return out


def annotate(document: dict, *, project=None, agents_dir=None, db_path=None) -> None:
    """Add the snapshot's `sidechat` object: {installed, session, model, busy, spent?}."""
    root = document.get("root")
    main = root.get("id") if isinstance(root, dict) else None
    shown = status(main=main if chatfeed.valid_session(main) else None, project=project, db_path=db_path,
                   agents_dir=agents_dir,
                   with_route=False)  # the snapshot is built often; a `ps` per snapshot is not worth a line nobody there shows
    document["sidechat"] = {key: shown[key] for key in ("installed", "session", "model", "busy", "spent") if key in shown}


def side_feed(feed: dict) -> dict:
    """The side session's transcript for the view: each of the user's questions cut before its brief, no
    reasoning."""
    items = []
    for item in feed.get("items", []):
        if item.get("kind") == "reasoning":
            continue
        if item.get("kind") == "user":
            text = item.get("text", "")
            cut = text.find(SEPARATOR)
            if cut >= 0:
                item = {**item, "text": text[:cut], "cut": False}
        items.append(item)
    return {**feed, "items": items}


def brief_info(question: str, *, main, project=None, db_path=None, config_dir=None,
               session_graph_dir=sources.ORCHESTRATION_GRAPH_DIR, full: bool = False) -> dict:
    """The sizes and section names of the brief the next question would send. Never its text."""
    project = Path(project) if project is not None else Path.cwd()
    db_path = Path(db_path) if db_path is not None else sources.DB_PATH
    config_dir = Path(config_dir) if config_dir is not None else sources.CONFIG_DIR
    question = clean_question(question)
    snapshot = _snapshot(project, main, db_path, config_dir, session_graph_dir)
    state = load_state(project, main)
    brief = build_brief(question, snapshot, project, main, db_path=db_path, previous=state, full=full)
    message = brief.text(question)
    return {"chars": len(message), "limit": BRIEF_LIMIT, "full": brief.full, "task": brief.task,
            "sections": brief.sizes(), "files": len(brief.named), "question_chars": len(question),
            "session": state.get("side")}


# --- install ------------------------------------------------------------------------------------------------

def _read_proposal(path, label: str) -> str:
    try:
        fd = osfs.open(path, os.O_RDONLY | osfs.O_NOFOLLOW | osfs.O_NONBLOCK)
    except OSError as exc:
        raise SidechatError(f"cannot read {label} ({Path(path).name}): {exc.strerror}") from exc
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SidechatError(f"{label} must be a regular file")
        data = stream.read(PROPOSAL_LIMIT + 1)
    if len(data) > PROPOSAL_LIMIT:
        raise SidechatError(f"{label} is larger than {PROPOSAL_LIMIT} bytes")
    try:
        return data.decode("utf-8").replace("\r\n", "\n")  # a file checked out or saved on Windows may end its lines with CRLF
    except UnicodeDecodeError:
        raise SidechatError(f"{label} is not UTF-8 text") from None


_RULES = re.compile(r'  - action: "([^"\n]*)"\n    resource: "([^"\n]*)"\n    effect: (allow|ask|deny)\n')
SKILL_RULE = '  - action: "skill"\n    resource: "side-chat"\n    effect: allow\n'


def check_agent_text(text: str) -> None:
    """Refuse an agent file that is not the side chat: marker first, `mode: primary` (the orchestrator cannot
    dispatch it), model given, and every rule a deny."""
    if not text.startswith("---\n" + MARK):
        raise SidechatError(f"the agent file must start with the marker line {MARK!r}")
    head, sep, _ = text[4:].partition("\n---\n")
    if not sep:
        raise SidechatError("the agent file has no closing --- after its frontmatter")
    if not re.search(r"(?m)^mode: primary$", head):
        raise SidechatError("the agent file must say `mode: primary` so the orchestrator cannot dispatch it")
    if not re.search(r'(?m)^model: "[^"\n]+"$', head) or not agentfiles.MODEL.fullmatch(re.search(r'(?m)^model: "([^"\n]+)"$', head)[1]):
        raise SidechatError("the agent file must give a model as provider/model")
    block = head.split("permissions:\n", 1)
    for line in block[0].splitlines():
        if not (line.startswith("#") or re.match(r"(description|mode|model): ", line)):
            raise SidechatError(f"the agent file may only carry description, mode, model and permissions, not {line[:30]!r}")
    if len(block) != 2:
        raise SidechatError("the agent file has no permissions list")
    rules = block[1] + "\n"
    found = _RULES.findall(rules)
    if not found or _RULES.sub("", rules).strip():
        raise SidechatError("the permissions must be a plain list of action, resource and effect")
    if any(effect != "deny" for _, _, effect in found):
        raise SidechatError("every permission of the side chat must be a deny")
    if found[0][:2] != ("*", "*"):
        raise SidechatError("the permissions must start by denying everything")


def _with_skill_rule(text: str) -> str:
    return text.replace('  - action: "subagent"', SKILL_RULE + '  - action: "subagent"', 1)


def _b64(data):
    return None if data is None else base64.b64encode(data).decode("ascii")


def _un64(value):
    return None if value is None else base64.b64decode(value.encode("ascii"), validate=True)


def _record(backups, op: str, data: dict) -> str:
    root = Path(backups)
    folder = root / f"{date.today().isoformat()}-sidechat"
    for path in (root, folder):
        try:
            osfs.mkdir(path, 0o700)
        except FileExistsError:
            pass
        if not stat.S_ISDIR(osfs.lstat(path).st_mode):
            raise SidechatError(f"{path} is not a plain folder")
    target = folder / f"{op}-{secrets.token_hex(6)}"
    osfs.mkdir(target, 0o700)
    path = target / "record.json"
    fd = osfs.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | osfs.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"version": 1, "op": op, **data}, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return str(path)


def _skill_parts(text: str) -> tuple[str, str]:
    try:
        fields, body = skillfiles._parse(text)[:2]
    except skillfiles.SkillfilesError as exc:
        raise SidechatError(f"the skill file is refused: {exc}") from exc
    if fields.get("name") != SKILL:
        raise SidechatError(f"the skill file must be named {SKILL}")
    return fields["description"], body


def install(*, with_skill: bool = False, agent_file=AGENT_PROPOSAL, skill_file=SKILL_PROPOSAL, agents_dir=None,
            skills_dir=None, backups=None) -> dict:
    """Write the agent file (and with `with_skill` the skill and the rule that lets the agent load it) from the
    proposal files. Refused, leaving everything as it was, when the proposal is not the side chat, or a file is
    already there. Returns {installed, agent, skill, record, message}."""
    agents_dir = Path(agents_dir) if agents_dir is not None else agentfiles.AGENTS_DIR
    skills_dir = Path(skills_dir) if skills_dir is not None else skillfiles.SKILLS
    backups = Path(backups) if backups is not None else skillset.BACKUPS
    text = _read_proposal(agent_file, "the agent file")
    check_agent_text(text)
    skill = _skill_parts(_read_proposal(skill_file, "the skill file")) if with_skill else None
    data = (_with_skill_rule(text) if with_skill else text).encode("utf-8")
    try:
        agentfiles.replace(AGENT, None, data, agents_dir=agents_dir)
    except agentfiles.AgentfilesError as exc:
        raise SidechatError(str(exc)) from exc
    created = False
    try:
        if skill is not None:
            try:
                skillfiles.create(SKILL, skill[0], skill[1], skills=skills_dir)
            except skillfiles.SkillfilesError as exc:
                raise SidechatError(str(exc)) from exc
            created = True
        record = _record(backups, "install", {"agents_dir": str(agents_dir), "skills_dir": str(skills_dir),
                                              "agent": {"after": _b64(data)}, "skill": {"id": SKILL} if created else None})
    except BaseException:
        if created:
            _remove_skill(skills_dir, backups, None)
        try:
            agentfiles.replace(AGENT, data, None, agents_dir=agents_dir)
        except agentfiles.AgentfilesError:
            pass
        raise
    return {"installed": True, "agent": f"{AGENT}.md", "skill": SKILL if created else None, "record": record,
            "message": agentfiles.LIVE if not created else agentfiles.LIVE + " The skill is listed from the next session."}


def _remove_skill(skills_dir, backups, config) -> str | None:
    try:
        done = skillfiles.delete(SKILL, skills=skills_dir, backups=backups, config=config or skillset.CONFIG,
                                 force=True, wildcards_hold=False, agents_dir=None)
    except skillfiles.SkillfilesError as exc:
        raise SidechatError(str(exc)) from exc
    return done["record"]


def uninstall(*, agents_dir=None, skills_dir=None, backups=None, config=None) -> dict:
    """Remove the agent file this module wrote (its marker is on the first line; a hand-written file is refused)
    and the skill if there is one, keeping what was removed in an undo record. Refused when there is nothing to
    remove."""
    agents_dir = Path(agents_dir) if agents_dir is not None else agentfiles.AGENTS_DIR
    skills_dir = Path(skills_dir) if skills_dir is not None else skillfiles.SKILLS
    backups = Path(backups) if backups is not None else skillset.BACKUPS
    path = agents_dir / f"{AGENT}.md"
    try:
        found = setups_read(path)
    except OSError as exc:
        raise SidechatError(f"cannot read {path.name}: {exc.strerror}") from exc
    skill_here = os.path.lexists(skills_dir / SKILL / "SKILL.md")
    if found is None and not skill_here:
        raise SidechatError("the side chat is not installed; there is nothing to remove")
    if found is not None and not found.startswith(b"---\n" + MARK.encode("ascii")):
        raise SidechatError(f"{path.name} was not written by `agentgraph sidechat install`; it is not touched here")
    if found is None and skill_here:
        found_skill_only = True
    else:
        found_skill_only = False
    skill_record = None
    try:
        if found is not None:
            try:
                agentfiles.replace(AGENT, found, None, agents_dir=agents_dir)
            except agentfiles.AgentfilesError as exc:
                raise SidechatError(str(exc)) from exc
        if skill_here:
            skill_record = _remove_skill(skills_dir, backups, config)
        record = _record(backups, "uninstall", {"agents_dir": str(agents_dir), "skills_dir": str(skills_dir),
                                                "agent": {"before": _b64(found)},
                                                "skill": {"record": skill_record} if skill_record else None})
    except BaseException:
        if found is not None:
            try:
                agentfiles.replace(AGENT, None, found, agents_dir=agents_dir)
            except agentfiles.AgentfilesError:
                pass
        raise
    return {"uninstalled": True, "agent": None if found_skill_only else f"{AGENT}.md", "skill": SKILL if skill_here else None,
            "record": record, "message": "Undo it with `agentgraph sidechat undo RECORD`."}


def setups_read(path: Path):
    """The bytes of a regular file read without following a link; None when there is none."""
    try:
        fd = osfs.open(path, os.O_RDONLY | osfs.O_NOFOLLOW | osfs.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise SidechatError(f"{path.name} must be a regular file")
        return stream.read(PROPOSAL_LIMIT + 1)


_RECORD = re.compile(r"\d{4}-\d{2}-\d{2}-sidechat/(install|uninstall)-[0-9a-f]{12}/record\.json")


def undo(record, *, agents_dir=None, skills_dir=None, backups=None, config=None) -> dict:
    """Reverse an `install` (remove what it wrote, if it is still as written) or an `uninstall` (put back the
    agent file and the skill). Refused, changing nothing, when the record is not one of the backups folder's or
    the files are no longer as the record left them."""
    backups = Path(backups) if backups is not None else skillset.BACKUPS
    if not isinstance(record, str) or not record or "\0" in record:
        raise SidechatError("invalid undo record: expected the record a change returned")
    root, path = Path(os.path.abspath(backups)), Path(os.path.abspath(record))
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise SidechatError("invalid undo record: it is not one of the backups folder's undo records") from None
    if not _RECORD.fullmatch(relative):
        raise SidechatError("invalid undo record: it is not one of the backups folder's undo records")
    try:
        data = json.loads(setups_read(path).decode("utf-8")) if setups_read(path) is not None else None
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise SidechatError(f"invalid undo record: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("op") != relative.rsplit("/", 2)[1].split("-")[0]:
        raise SidechatError("invalid undo record: it does not match its file name")
    agents_dir = Path(agents_dir if agents_dir is not None else data.get("agents_dir") or agentfiles.AGENTS_DIR)
    skills_dir = Path(skills_dir if skills_dir is not None else data.get("skills_dir") or skillfiles.SKILLS)
    try:
        if data["op"] == "install":
            after = _un64(data["agent"]["after"])
            if setups_read(agents_dir / f"{AGENT}.md") != after:
                raise SidechatError("the agent file was changed after the install; nothing was undone")
            if data.get("skill"):
                _remove_skill(skills_dir, backups, config)
            agentfiles.replace(AGENT, after, None, agents_dir=agents_dir)
            return {"undone": "install", "message": "The side chat is removed."}
        before, skill = _un64(data["agent"]["before"]), data.get("skill")
        if before is not None:
            agentfiles.replace(AGENT, None, before, agents_dir=agents_dir)
        if skill and skill.get("record"):
            try:
                skillfiles.restore(skill["record"], skills=skills_dir, backups=backups)
            except BaseException:
                if before is not None:
                    agentfiles.replace(AGENT, before, None, agents_dir=agents_dir)
                raise
        return {"undone": "uninstall", "message": "The side chat is back."}
    except (agentfiles.AgentfilesError, skillfiles.SkillfilesError, KeyError, TypeError, ValueError) as exc:
        raise SidechatError(str(exc)) from exc


# --- the command ---------------------------------------------------------------------------------------------

def _read_question(source: str) -> str:
    if source == "-":
        return sys.stdin.read(QUESTION_LIMIT * 4 + 1)
    return _read_proposal(source, "the question file")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentgraph sidechat", description="The side chat: a small read-only agent "
                                     "the user can ask about the running task. It talks only to the user.")
    sub = parser.add_subparsers(dest="op", required=True)

    def common(p, main: str = "none"):
        p.add_argument("--dir", type=Path, metavar="PROJECT", help="the project folder (default: here)")
        if main != "none":
            p.add_argument("--main", metavar="SESSION", required=main == "required", help="the main session (ses_...)")
        p.add_argument("--json", action="store_true", help="the result as one line of JSON")
        p.add_argument("--demo", action="store_true", help="answer from a fixed sample; calls nothing")
        for flag in ("--db", "--config-dir", "--agents-dir", "--skills-dir", "--backups", "--config"):
            p.add_argument(flag, type=Path, help=argparse.SUPPRESS)

    ask_p = sub.add_parser("ask", help="ask one question")
    common(ask_p, "required")
    ask_p.add_argument("--text-file", metavar="F", required=True, help="the question (a file, or - for stdin)")
    ask_p.add_argument("--full", action="store_true", help="send the whole brief again, not only what changed")
    ask_p.add_argument("--model", metavar="M", help=argparse.SUPPRESS)
    ask_p.add_argument("--timeout", type=float, default=TIMEOUT, help=argparse.SUPPRESS)
    status_p = sub.add_parser("status", help="installed or not, the session, the model, the spend")
    common(status_p, "optional")
    brief_p = sub.add_parser("brief", help="the sizes and section names of the brief the next question would send")
    common(brief_p, "required")
    brief_p.add_argument("--text-file", metavar="F", help="a question, so a named artifact counts (default: none)")
    brief_p.add_argument("--full", action="store_true")
    install_p = sub.add_parser("install", help="write the agent file from the approved proposal")
    common(install_p)
    install_p.add_argument("--with-skill", action="store_true", help="also the skill, and the rule that lets the agent load it")
    install_p.add_argument("--agent-file", type=Path, default=AGENT_PROPOSAL)
    install_p.add_argument("--skill-file", type=Path, default=SKILL_PROPOSAL)
    common(sub.add_parser("uninstall", help="remove what install wrote"))
    undo_p = sub.add_parser("undo", help="reverse an install or an uninstall")
    common(undo_p)
    undo_p.add_argument("record")
    return parser


def _text(op: str, result: dict) -> str:
    if op == "ask":
        brief = result["brief"]
        cost = f"${result['cost']:.4f}" if result.get("cost") is not None else "cost unknown"
        return (f"{result['answer']}\n-- side chat {result['session']} | {result['model']}"
                f"{' (fallback)' if result.get('fallback') else ''} | brief {brief['chars']} chars"
                f"{'' if brief['full'] else ' (only what changed)'} | {cost} for this question")
    if op == "status":
        lines = [f"installed: {'yes (' + result['where'] + ')' if result['installed'] else 'no'}",
                 f"session: {result['session'] or '-'}", f"model: {result['model'] or '-'}",
                 f"busy: {'yes' if result['busy'] else 'no'}", f"questions: {result['asks']}"]
        if "route" in result:
            lines.append(f"route: {result['route'] or '-'}" + (f" ({result['route_note']})" if result.get("route_note") else ""))
        if "spent" in result:
            lines.append(f"spent: ${result['spent']:.4f}")
        return "\n".join(lines)
    if op == "brief":
        sections = ", ".join(f"{name} {chars}" for name, chars in result["sections"].items())
        return (f"brief {result['chars']} of {result['limit']} chars ({'full' if result['full'] else 'only what changed'}); "
                f"sections: {sections or '-'}; named files: {result['files']}")
    return str(result.get("message") or json.dumps(result))


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.demo and args.op in ("ask", "status", "brief"):
            from . import demo
            result = {"ask": demo.demo_side_answer, "status": demo.demo_sidechat, "brief": demo.demo_side_brief}[args.op]()
        elif args.op == "ask":
            result = ask(_read_question(args.text_file), main=args.main, project=args.dir, db_path=args.db,
                         config_dir=args.config_dir, model=args.model, timeout=args.timeout, full=args.full)
        elif args.op == "status":
            if args.main is not None and not chatfeed.valid_session(args.main):
                raise SidechatError("--main must look like ses_ followed by letters and digits")
            result = status(main=args.main, project=args.dir, db_path=args.db, agents_dir=args.agents_dir)
        elif args.op == "brief":
            question = _read_question(args.text_file) if args.text_file else "(no question)"
            result = brief_info(question, main=args.main, project=args.dir, db_path=args.db, config_dir=args.config_dir,
                                full=args.full)
        elif args.op == "install":
            result = install(with_skill=args.with_skill, agent_file=args.agent_file, skill_file=args.skill_file,
                             agents_dir=args.agents_dir, skills_dir=args.skills_dir, backups=args.backups)
        elif args.op == "uninstall":
            result = uninstall(agents_dir=args.agents_dir, skills_dir=args.skills_dir, backups=args.backups,
                               config=args.config)
        else:
            result = undo(args.record, agents_dir=args.agents_dir, skills_dir=args.skills_dir, backups=args.backups,
                          config=args.config)
    except (skillset.SkillsetError, OSError) as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"sidechat: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, allow_nan=False) if args.json else _text(args.op, result))
    return 0


if __name__ == "__main__":
    from agentgraph.sidechat import main as package_main
    raise SystemExit(package_main())
