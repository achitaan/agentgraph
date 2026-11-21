"""Run a drawn loop for real: `agentgraph loop ...`. A deterministic controller (code, not a model) runs the agent for one
attempt in a real OpenCode session, runs the configured check command itself, and on a failed check sends the evidence back
into the same session, until the check passes or a limit enforced here stops it. A flow `A -> A` without a saved loop stays
text for the orchestrator, as before.

**Saved loop.** `<structure>/.loops/<flow id>.json` (a dot folder, so the structure readers never take it for a group of agents; written atomically under a lock file; flow files are not extended, a
rewrite of a flow drops unknown head keys): `{"version": 1, "flow": ID, "goal": "...", "check": {"command", "timeout" (300),
"inconclusive_codes" ([75])}, "session": "reuse" | "fresh", "limits": {"attempts" (required, 1 to 50), "attempt_seconds",
"seconds", "cost"}, "allow_asks": false}`. Unknown keys are refused at every level. `check` may be missing in the file; a run
then ends `invalid`. `compile(path, settings)` turns the drawing into the steps the walker follows: `{"id", "kind": "agent" |
"check", ..., "on": {outcome: step id | "done" | "stop"}}`. Only a self-loop compiles in this release; any other path is
refused.

**Controller.** One run is one detached process (`python3 -m agentgraph.looprun run --run-dir D`) so it outlives the page and
the server. The walker is a small state machine: it enters the first step ("work") once per attempt, after an admission check
(attempts used, elapsed seconds, known cost, a cancel file), runs a step by its kind, maps the step's outcome to a route and
follows it. It decides from memory; the files are its report. The agent step sends the message built here (goal in every
message; after a failed check its exit code and the last 60 lines of its output) through an executor
(`OpenCodeExecutor`: `opencode run --standalone --format json --agent A --session ID [--title T] [--auto]`, the message on
stdin, own process group, stopped at `attempt_seconds`; `ScriptedExecutor` for tests). The session id is chosen in advance and
written to the record before the process starts. The check runs through `sh -c` in the work folder with its own timeout.
Outcome of the check from its exit code only: 0 `pass`; a code in `inconclusive_codes` or a timeout `inconclusive`; 126, 127, a
signal or a command that cannot start `check_error`; any other code `fail`. A turn that was stopped at the time limit
(`stalled`) still gets its check (its pass is real); a failed turn (`agent_error`) ends the run. Nothing but a check that
exited 0 ever gives `passed`; the agent's own "PASS" text is never read.

**Final status**: `passed`, `failed` (attempt limit, last check failed), `limit` (seconds or cost, with the reason),
`invalid` (refused before any attempt), `check_error`, `inconclusive`, `stalled` (the last turn was stopped, no pass),
`agent_error`, `cancelled`. The spend limit stops new attempts; it does not cap what a running attempt bills.

**Run record** `<project>/.opencode/loops/<run id>/`: `definition.json` (written once: settings, steps, their sha256, goal,
work folder, executor), `run.json` (atomic after every transition), `events.jsonl` (append-only), `attempt-N/message.md` and
`attempt-N/check.out` (capped at 1 MB), `lock` (held by the running controller, so a second process cannot run the folder),
`cancel` (a request), `run.log` (the detached process's output). Only `queued` runs are started; there is no resume yet.

    agentgraph loop set FLOW --check CMD --attempts N [--goal T | --goal-file F] [--check-timeout S] [--inconclusive C ...]
                             [--session reuse|fresh] [--attempt-seconds S] [--seconds S] [--cost D] [--allow-asks | --no-allow-asks]
    agentgraph loop show FLOW [--json] | unset FLOW
    agentgraph loop start FLOW [--goal T | --goal-file F] [--workdir D] [--wait]
    agentgraph loop status [RUN] [--json] | list [--json] | cancel RUN
    every command: --structure DIR, --project DIR (default: the current folder); start also --scratch DIR, --executable F, --db F

Exit codes: 0 done, 1 the run did not pass or the request found nothing to do, 2 arguments or settings not usable.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import bench, paths, sidechat, snapshot_json, sources

VERSION = 1
OUTPUT_CAP = 1_000_000
TAIL_LINES = 60
TAIL_CHARS = 6000
GOAL_LIMIT = 20000
HEARTBEAT = 5.0
RUN_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{4}")
FLOW_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
MULTI = "loops between several agents arrive in the next release; only a loop of one agent on itself (A -> A) runs now"
FINAL = ("passed", "failed", "limit", "invalid", "check_error", "inconclusive", "stalled", "agent_error", "cancelled")
STOP_STATUS = {"stalled": "stalled", "agent_error": "agent_error", "check_error": "check_error", "inconclusive": "inconclusive",
               "cancelled": "cancelled"}


class LoopError(Exception):
    """A refused request; the message says why. `code` is the exit code of the command."""
    code = 2


class Busy(LoopError):
    """The run is held by another process or is not startable (exit 1)."""
    code = 1


# ---- the saved loop ----

def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _only(block: dict, allowed, where: str, path) -> None:
    extra = sorted(str(k) for k in block if k not in allowed)
    if extra:
        raise LoopError(f"{path}: unknown key {extra[0]!r} in {where} (allowed: {', '.join(sorted(allowed))})")


def validate(settings, path) -> dict:
    """The settings with their defaults filled in, or LoopError naming `path` and the problem. Unknown keys are refused."""
    if not isinstance(settings, dict):
        raise LoopError(f"{path}: the settings must be a JSON object")
    _only(settings, {"version", "flow", "goal", "check", "session", "limits", "allow_asks"}, "the settings", path)
    if settings.get("version") != VERSION:
        raise LoopError(f"{path}: version must be {VERSION}")
    flow = settings.get("flow")
    if not isinstance(flow, str) or not FLOW_ID.fullmatch(flow):
        raise LoopError(f"{path}: flow must be a flow id (letters, digits, '.', '_', '-')")
    out: dict = {"version": VERSION, "flow": flow}
    goal = settings.get("goal")
    if goal is not None:
        if not isinstance(goal, str) or len(goal) > GOAL_LIMIT:
            raise LoopError(f"{path}: goal must be text of at most {GOAL_LIMIT} characters")
        if goal.strip():
            out["goal"] = goal
    check = settings.get("check")
    if check is not None:
        if not isinstance(check, dict):
            raise LoopError(f"{path}: check must be an object")
        _only(check, {"command", "timeout", "inconclusive_codes"}, "check", path)
        command = check.get("command")
        if not isinstance(command, str) or not command.strip() or len(command) > 4000:
            raise LoopError(f"{path}: check.command must be a command (text, at most 4000 characters)")
        timeout = check.get("timeout", 300)
        if not _number(timeout) or not 0 < timeout <= 86400:
            raise LoopError(f"{path}: check.timeout must be seconds, more than 0 and at most 86400")
        codes = check.get("inconclusive_codes", [75])
        if not isinstance(codes, list) or not all(isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= 255 for c in codes):
            raise LoopError(f"{path}: check.inconclusive_codes must be a list of exit codes from 1 to 255")
        out["check"] = {"command": command, "timeout": timeout, "inconclusive_codes": list(codes)}
    session = settings.get("session", "reuse")
    if session not in ("reuse", "fresh"):
        raise LoopError(f"{path}: session must be 'reuse' or 'fresh'")
    out["session"] = session
    limits = settings.get("limits")
    if not isinstance(limits, dict):
        raise LoopError(f"{path}: limits.attempts is required (1 to 50)")
    _only(limits, {"attempts", "attempt_seconds", "seconds", "cost"}, "limits", path)
    attempts = limits.get("attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 50:
        raise LoopError(f"{path}: limits.attempts is required and must be a whole number from 1 to 50")
    out["limits"] = {"attempts": attempts}
    for key, top in (("attempt_seconds", 86400), ("seconds", 604800), ("cost", 10000)):
        if key in limits and limits[key] is not None:
            if not _number(limits[key]) or not 0 < limits[key] <= top:
                raise LoopError(f"{path}: limits.{key} must be a number above 0 and at most {top}")
            out["limits"][key] = limits[key]
    allow = settings.get("allow_asks", False)
    if not isinstance(allow, bool):
        raise LoopError(f"{path}: allow_asks must be true or false")
    out["allow_asks"] = allow
    return out


def structure_dir(structure=None) -> Path:
    return Path(structure) if structure is not None else Path(snapshot_json.STRUCTURE_DIR)


def settings_path(structure, flow_id: str) -> Path:
    if not isinstance(flow_id, str) or not FLOW_ID.fullmatch(flow_id):
        raise LoopError(f"not a flow id: {str(flow_id)[:60]!r}")
    return structure_dir(structure) / ".loops" / f"{flow_id}.json"


_LOCK = threading.Lock()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class _Locked:
    """The loops folder, locked for one writer at a time (a lock file inside it, and a lock inside this process)."""

    def __init__(self, folder: Path):
        self.folder = folder

    def __enter__(self):
        _LOCK.acquire()
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.stream = open(self.folder / ".lock", "a+")
            fcntl.flock(self.stream, fcntl.LOCK_EX)
        except BaseException:
            _LOCK.release()
            raise
        return self

    def __exit__(self, *exc):
        try:
            self.stream.close()
        finally:
            _LOCK.release()


def load_settings(structure, flow_id: str) -> dict | None:
    """The saved loop of a flow (validated), None when there is none, LoopError when the file cannot be used."""
    path = settings_path(structure, flow_id)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LoopError(f"{path}: cannot be read ({exc.strerror})") from None
    try:
        raw = json.loads(data.decode("utf-8"))
    except ValueError:
        raise LoopError(f"{path}: not valid JSON") from None
    out = validate(raw, path)
    if out["flow"] != flow_id:
        raise LoopError(f"{path}: the file is for flow {out['flow']!r}")
    return out


def save_settings(structure, flow_id: str, settings: dict) -> Path:
    path = settings_path(structure, flow_id)
    clean = validate(settings, path)
    if clean["flow"] != flow_id:
        raise LoopError(f"{path}: the settings are for flow {clean['flow']!r}")
    with _Locked(path.parent):
        _write_atomic(path, (json.dumps(clean, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return path


def remove_settings(structure, flow_id: str) -> bool:
    path = settings_path(structure, flow_id)
    if not path.parent.is_dir():
        return False
    with _Locked(path.parent):
        try:
            path.unlink()
        except FileNotFoundError:
            return False
    return True


def read_flow_of(structure, flow_id: str) -> tuple[list[str], dict]:
    """(the flow's path, the structure) for a flow id, or LoopError."""
    root = structure_dir(structure)
    shape = snapshot_json.load_structure(root)
    if shape is None:
        raise LoopError(f"no structure folder at {root}")
    for flow in shape["flows"]:
        if flow["id"] == flow_id:
            return list(flow["path"]), shape
    for flow in shape["unreadable_flows"]:
        if flow["id"] == flow_id:
            raise LoopError(f"flow {flow_id} cannot be read: {flow['reason']}")
    raise LoopError(f"no flow {flow_id!r} in {root}")


def compile(path, settings) -> list[dict]:
    """The steps the walker follows for a drawn path. A self-loop `A -> A` is `work` + `check`; anything else is refused."""
    if not snapshot_json.is_self_loop(list(path)):
        raise LoopError(MULTI)
    check = settings.get("check")
    if not check:
        raise LoopError("the loop has no check command: a loop without a check never passes")
    return [{"id": "work", "kind": "agent", "agent": path[0], "session": settings.get("session", "reuse"),
             "on": {"returned": "check", "stalled": "check", "agent_error": "stop", "cancelled": "stop"}},
            {"id": "check", "kind": "check", "command": check["command"], "timeout": check.get("timeout", 300),
             "inconclusive_codes": list(check.get("inconclusive_codes", [75])),
             "on": {"pass": "done", "fail": "work", "inconclusive": "stop", "check_error": "stop"}}]


# ---- outcomes, messages, admission ----

def check_outcome(code, timed_out: bool, inconclusive_codes) -> str:
    """`pass` only for exit 0; see the module docstring for the rest."""
    if timed_out:
        return "inconclusive"
    if code is None:
        return "check_error"
    if code == 0:
        return "pass"
    if code in inconclusive_codes:
        return "inconclusive"
    if code in (126, 127) or code < 0:
        return "check_error"
    return "fail"


def status_of(outcome: str) -> str:
    """The final run status of a step outcome whose route is `stop`."""
    return STOP_STATUS.get(outcome, "failed")


def tail_of(output: str) -> str:
    text = "\n".join(output.splitlines()[-TAIL_LINES:])
    return text if len(text) <= TAIL_CHARS else "[...]" + text[-TAIL_CHARS:]


def build_message(goal: str, command: str, n: int, limit: int, previous: dict | None, last_agent: str | None, attempt_seconds=None) -> str:
    """What the agent is sent: the goal in full every time (it must survive compaction), and after a failed check its exit code
    and the end of its output. Nothing the agent said earlier is repeated or trusted."""
    if previous is None:
        return (f"Loop run, attempt {n} of {limit}. A controller program, not you, decides when the work is done.\n\n"
                f"# Goal\n\n{goal.strip()}\n\n# How it is checked\n\nAfter your turn the controller runs this command in the work folder:\n\n"
                f"    {command}\n\nThe work counts as done only when it exits 0. Saying that you are done changes nothing.\n\n"
                "Make one attempt and return. The controller runs the check and comes back with the result.\n")
    lines = [f"Loop run, attempt {n} of {limit}. A controller program, not you, decides when the work is done."]
    if last_agent == "stalled":
        seconds = f" after {attempt_seconds:g} seconds" if _number(attempt_seconds) else ""
        lines.append(f"Your last turn was stopped{seconds} (the time limit for one attempt); anything it was still running was killed.")
    code = previous.get("exit")
    result = f"exit code {code}" if code is not None else "no exit code"
    lines += ["", f"# Goal (unchanged)\n\n{goal.strip()}", "",
              f"# Result of the check after attempt {n - 1}\n\nThe controller ran `{command}`: {result} ({previous.get('outcome')}).\n",
              f"Last {TAIL_LINES} lines of its output:\n\n```\n{previous.get('tail', '').rstrip()}\n```\n",
              "Fix what the check reports. Make one attempt and return; the controller runs the check again."]
    return "\n".join(lines) + "\n"


def admit(used: int, limits: dict, elapsed: float, cost, cancelled: bool):
    """None to start another attempt, else (final status, reason). A cancel request comes first; an unknown cost is not counted."""
    if cancelled:
        return "cancelled", "cancel requested"
    if used >= limits["attempts"]:
        return "failed", f"attempt limit ({limits['attempts']}) reached without a passing check"
    if limits.get("seconds") is not None and elapsed >= limits["seconds"]:
        return "limit", f"seconds limit ({limits['seconds']:g}) reached after {elapsed:.0f} seconds"
    if limits.get("cost") is not None and cost is not None and cost >= limits["cost"]:
        return "limit", f"cost limit (${limits['cost']:g}) reached: ${cost:.4f} spent by the attempts so far"
    return None


# ---- the check runner ----

def run_check(command: str, timeout, cwd, env, out_path: Path, poll=None) -> dict:
    """Run `sh -c command`; the whole output goes to `out_path` (capped). {exit, timed_out, seconds, error, truncated}."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(["sh", "-c", command], cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
        out_path.write_text(f"the check could not be started: {exc}\n", encoding="utf-8")
        return {"exit": None, "timed_out": False, "seconds": 0.0, "error": str(exc), "truncated": 0}
    dropped = [0]

    def drain():
        with open(out_path, "wb") as sink:
            kept = 0
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                room = OUTPUT_CAP - kept
                if room > 0:
                    sink.write(chunk[:room])
                    kept += min(room, len(chunk))
                dropped[0] += max(0, len(chunk) - max(room, 0))
            if dropped[0]:
                sink.write(f"\n[output truncated: {dropped[0]} more bytes were dropped]\n".encode())

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    while True:
        try:
            code = proc.wait(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if poll:
                poll()
            if timeout and time.monotonic() - started > timeout:
                timed_out = True
                sidechat._stop(proc)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                code = None
                break
    reader.join(5)
    if not reader.is_alive():
        proc.stdout.close()
    return {"exit": None if timed_out else code, "timed_out": timed_out, "seconds": time.monotonic() - started, "error": None,
            "truncated": dropped[0]}


# ---- executors ----

def new_session_id() -> str:
    return f"ses_{int(time.time() * 1000):012x}{secrets.token_hex(7)}"


class ScriptedExecutor:
    """For tests: each call takes the next item of a list. Keys: `sleep` seconds, `hang` (wait for the time limit or a cancel),
    `error`, `text`, `cost`, `tokens`, `sessions`, `exit`. `calls` holds what the controller passed; `hook(n, call)` runs first."""

    def __init__(self, attempts, hook=None):
        self.script = list(attempts)
        self.hook = hook
        self.calls: list[dict] = []

    def spec(self) -> dict:
        return {"kind": "scripted", "attempts": self.script}

    def env(self, work) -> dict:
        return {**os.environ, "PWD": str(work)}

    def attempt(self, *, agent, session, first, title, message, timeout, env, cwd, auto, poll) -> dict:
        call = {"agent": agent, "session": session, "first": first, "title": title, "message": message, "timeout": timeout,
                "cwd": str(cwd), "auto": auto}
        self.calls.append(call)
        item = self.script[len(self.calls) - 1] if len(self.calls) <= len(self.script) else {}
        if self.hook:
            self.hook(len(self.calls), call)
        started = time.monotonic()
        wait = 600.0 if item.get("hang") else float(item.get("sleep", 0))
        outcome = None
        while time.monotonic() - started < wait:
            if poll():
                outcome = "cancelled"
                break
            if timeout and time.monotonic() - started >= timeout:
                outcome = "stalled"
                break
            time.sleep(0.02)
        error = item.get("error")
        outcome = outcome or ("agent_error" if error else "returned")
        return {"exit": None if outcome in ("stalled", "cancelled") else item.get("exit", 1 if error else 0), "outcome": outcome,
                "session": session, "cost": item.get("cost"), "tokens": item.get("tokens"), "sessions": item.get("sessions"),
                "seconds": time.monotonic() - started, "error": error, "text": item.get("text", "")}


class OpenCodeExecutor:
    """`opencode run` once per attempt, on the standalone route, in its own process group."""

    def __init__(self, executable=None, scratch: dict | None = None, db_path=None):
        self.exe = bench.executable_path(executable)
        self.scratch = scratch
        self.db_path = Path(db_path) if db_path else sources.DB_PATH
        self._seen: dict[str, dict] = {}

    def spec(self) -> dict:
        return {"kind": "opencode", "executable": self.exe, "scratch": str(self.scratch["dir"]) if self.scratch else None,
                "db": str(self.db_path)}

    def env(self, work) -> dict:
        return bench.run_env(self.scratch, Path(work)) if self.scratch else {**os.environ, "PWD": str(work)}

    def command(self, agent, session, first, title, auto) -> list[str]:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", agent or ""):
            raise LoopError(f"bad agent name: {agent!r}")
        if not bench.SESSION.fullmatch(session or ""):
            raise LoopError("bad session id")
        cmd = [self.exe, "run", "--standalone", "--format", "json", "--agent", agent, "--session", session]
        if first:
            cmd += ["--title", title]
        if auto:
            cmd.append("--auto")
        return cmd

    def attempt(self, *, agent, session, first, title, message, timeout, env, cwd, auto, poll) -> dict:
        cmd = self.command(agent, session, first, title, auto)
        started = time.monotonic()
        try:
            proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace", start_new_session=True, env=env)
        except OSError as exc:
            return self._result(session, "agent_error", None, started, "", error=f"the agent could not be started: {exc}")
        outcome, first_call, out, err = None, True, "", ""
        while True:
            try:
                out, err = proc.communicate(input=message if first_call else None, timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                first_call = False
                if poll():
                    outcome = "cancelled"
                elif timeout and time.monotonic() - started > timeout:
                    outcome = "stalled"
                if outcome:
                    sidechat._stop(proc)
                    out, err = proc.communicate()
                    break
        code = None if outcome else proc.returncode
        events = sidechat._events(out or "")
        error = events["error"] or (next((ln.strip() for ln in (err or "").splitlines() if ln.strip()), "")[:160] if code else None)
        if outcome is None:
            outcome = "agent_error" if (code or events["error"]) else "returned"
        return self._result(session, outcome, code, started, out or "", error=error or None, events=events)

    def _result(self, session, outcome, code, started, out, error=None, events=None) -> dict:
        events = events or sidechat._events(out)
        tree = self._tree(session, expect=outcome == "returned")
        stream_cost = events["cost"] if '"step_finish"' in out else None
        cost, tokens = stream_cost, (dict(events["tokens"]) if '"step_finish"' in out else None)
        if tree is not None:
            before = self._seen.get(session, {"cost": 0.0, "tokens": {}})
            cost = round(max(0.0, tree["cost"] - before["cost"]), 8)
            tokens = {k: max(0, v - before["tokens"].get(k, 0)) for k, v in tree["tokens"].items()}
            self._seen[session] = {"cost": tree["cost"], "tokens": dict(tree["tokens"])}
        text = "".join(events["text"])
        return {"exit": code, "outcome": outcome, "session": session, "cost": cost, "tokens": tokens,
                "sessions": tree["sessions"] if tree is not None else None, "seconds": time.monotonic() - started, "error": error,
                "text": text}

    def _tree(self, session, expect: bool):
        for attempt in range(6 if expect else 1):
            try:
                tree = bench.read_tree(self.db_path, session)
            except (sqlite3.Error, OSError):
                tree = None
            if tree and tree["sessions"]:
                return tree
            time.sleep(0.5)
        return None


def make_executor(spec: dict):
    kind = (spec or {}).get("kind")
    if kind == "scripted":
        return ScriptedExecutor(spec.get("attempts", []))
    if kind == "opencode":
        scratch = bench.open_scratch(spec["scratch"]) if spec.get("scratch") else None
        db = spec.get("db") or (scratch["dir"] / "home" / ".local" / "share" / "opencode" / "opencode.db" if scratch else None)
        return OpenCodeExecutor(spec.get("executable"), scratch, db)
    raise LoopError(f"unknown executor {kind!r}")


# ---- the run record ----

def loops_folder(project) -> Path:
    return Path(project) / ".opencode" / "loops"


def run_dir(project, run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise LoopError(f"not a run id: {str(run_id)[:40]!r}")
    return loops_folder(project) / run_id


def read_run(folder) -> dict:
    try:
        return json.loads((Path(folder) / "run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LoopError(f"no readable run record in {folder}") from None


def read_definition(folder) -> dict:
    try:
        return json.loads((Path(folder) / "definition.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise LoopError(f"no readable definition in {folder}") from None


def _json(data) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def steps_hash(steps) -> str:
    return hashlib.sha256(json.dumps(steps, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _event(folder: Path, **fields) -> None:
    with open(folder / "events.jsonl", "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time": round(time.time(), 3), **fields}, sort_keys=True) + "\n")


def list_runs(project) -> list[str]:
    try:
        return sorted((p.name for p in loops_folder(project).iterdir() if p.is_dir() and RUN_ID.fullmatch(p.name)), reverse=True)
    except OSError:
        return []


def request_cancel(folder) -> None:
    (Path(folder) / "cancel").write_text(f"{time.time()}\n", encoding="utf-8")


def _held(folder: Path) -> bool:
    """Whether a controller holds the run's lock right now."""
    try:
        stream = open(folder / "lock", "a+")
    except OSError:
        return False
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        stream.close()
    return False


def shown_status(folder, state: dict) -> str:
    """The status to show: a run that says `running` but whose controller is gone shows `interrupted` (the files are not changed)."""
    if state.get("status") == "running" and not _held(Path(folder)):
        return "interrupted"
    return state.get("status", "unknown")


def describe(folder, state: dict) -> str:
    totals = state.get("totals") or {}
    attempts = state.get("attempts") or []
    last = attempts[-1].get("check") if attempts and attempts[-1].get("check") else None
    limit = (state.get("limits") or {}).get("attempts")
    cost = totals.get("cost")
    seconds = totals.get("seconds") or 0
    parts = [state.get("run", Path(folder).name), shown_status(folder, state), f"attempts {len(attempts)}" + (f"/{limit}" if limit else ""),
             f"check exit {last['exit']}" if last and last.get("exit") is not None else ("check none" if not last else f"check {last['outcome']}"),
             f"cost ${cost:.4f}" if cost is not None else "cost unknown", f"{seconds:.0f}s", f"flow {state.get('flow')}"]
    if state.get("reason"):
        parts.append(str(state["reason"])[:120])
    return "  ".join(parts)


# ---- the controller ----

class Controller:
    """Walks the compiled steps of one run. Decides from memory; writes run.json after every transition."""

    def __init__(self, folder: Path, definition: dict, state: dict, executor, env: dict):
        self.folder, self.definition, self.state, self.executor, self.env = folder, definition, state, executor, env
        self.steps = {s["id"]: s for s in definition["steps"]}
        self.head = definition["steps"][0]["id"]
        self.check_step = next((s for s in definition["steps"] if s["kind"] == "check"), None)
        self.limits = definition["settings"]["limits"]
        self.workdir = Path(definition["workdir"])
        self.began = time.monotonic()
        self.last_save = 0.0
        self.terminated = False
        self.attempt: dict | None = None
        self.last_check: dict | None = None
        self.last_agent: str | None = None
        self.session_id: str | None = None
        self.confirmed: set[str] = set()
        self.roots: dict[str, int] = {}
        self.kinds = {"agent": self._agent, "check": self._check}

    # state
    def totals(self) -> dict:
        costs = [a["agent"]["cost"] for a in self.state["attempts"] if a.get("agent") and a["agent"].get("cost") is not None]
        return {"cost": round(sum(costs), 8) if costs else None, "seconds": round(time.monotonic() - self.began, 1),
                "sessions": sum(self.roots.values()) if self.roots else None, "attempts": len(self.state["attempts"])}

    def save(self) -> None:
        self.state["totals"] = self.totals()
        self.state["heartbeat"] = round(time.time(), 3)
        _write_atomic(self.folder / "run.json", _json(self.state))
        self.last_save = time.monotonic()

    def cancelled(self) -> bool:
        return self.terminated or (self.folder / "cancel").exists()

    def poll(self) -> bool:
        if time.monotonic() - self.last_save >= HEARTBEAT:
            self.save()
        return self.cancelled()

    def known_cost(self):
        return self.totals()["cost"]

    # the walk
    def run(self) -> str:
        self.state.update(status="running", pid=os.getpid(), started=round(time.time(), 3))
        self.save()
        _event(self.folder, attempt=0, step="-", outcome="started", route=self.head)
        step_id = self.head
        while True:
            step = self.steps[step_id]
            if step_id == self.head:
                refusal = admit(len(self.state["attempts"]), self.limits, time.monotonic() - self.began, self.known_cost(), self.cancelled())
                if refusal:
                    status, reason = refusal
                    if status == "failed" and self.last_agent == "stalled":
                        status, reason = "stalled", "attempt limit reached; the last turn was stopped at the time limit and no check passed"
                    return self.finish(status, reason, step_id)
                self._begin()
            outcome = self.kinds[step["kind"]](step)
            route = step.get("on", {}).get(outcome, "stop")
            self.attempt["steps"].append({"step": step["id"], "outcome": outcome})
            n = self.attempt["n"]
            _event(self.folder, attempt=n, step=step["id"], outcome=outcome, route=route)
            self.save()
            if route == "done":
                return self.finish_done(step)
            if route == "stop":
                return self.finish(status_of(outcome), f"{step['id']}: {outcome}", step["id"])
            if route not in self.steps:
                return self.finish("invalid", f"the definition routes to an unknown step {route!r}", step["id"])
            step_id = route

    def _begin(self) -> None:
        n = len(self.state["attempts"]) + 1
        if n > 1:
            self.state["attempts"][-1]["feedback"] = f"attempt-{n}/message.md"
        self.attempt = {"n": n, "steps": [], "started": round(time.time(), 3), "ended": None, "agent": None, "check": None,
                        "message": None, "feedback": None}
        self.state["attempts"].append(self.attempt)
        (self.folder / f"attempt-{n}").mkdir(exist_ok=True)

    def finish_done(self, step) -> str:
        check = (self.attempt or {}).get("check") or {}
        if step["kind"] != "check" or check.get("exit") != 0 or check.get("outcome") != "pass":
            return self.finish("check_error", "the definition ended the run without a check that exited 0", step["id"])
        return self.finish("passed", "the check exited 0", step["id"])

    def finish(self, status: str, reason: str, step_id: str) -> str:
        if status not in FINAL or (status == "passed" and not self._passed_by_check()):
            status, reason = "check_error", f"refused to report {status!r} without a check that exited 0"
        if self.attempt is not None and self.attempt.get("ended") is None:
            self.attempt["ended"] = round(time.time(), 3)
        self.state.update(status=status, reason=reason, ended=round(time.time(), 3))
        self.save()
        _event(self.folder, attempt=len(self.state["attempts"]), step=step_id, outcome=status, route="end", reason=reason)
        return status

    def _passed_by_check(self) -> bool:
        check = (self.attempt or {}).get("check") or {}
        return check.get("exit") == 0 and check.get("outcome") == "pass"

    # step kinds
    def _agent(self, step) -> str:
        n = self.attempt["n"]
        fresh = step.get("session") == "fresh"
        if fresh or self.session_id is None:
            self.session_id = new_session_id()
        session = self.session_id
        first = session not in self.confirmed
        title = f"agentgraph-loop: {self.state['flow']} {self.state['run']}" + (f" a{n}" if fresh else "")
        message = build_message(self.definition["goal"], self.check_step["command"], n, self.limits["attempts"], self.last_check,
                                self.last_agent, self.limits.get("attempt_seconds"))
        path = self.folder / f"attempt-{n}" / "message.md"
        path.write_text(message, encoding="utf-8")
        self.attempt["message"] = f"attempt-{n}/message.md"
        if session not in self.state["sessions"]:
            self.state["sessions"].append(session)  # in the record before the process starts, so a crash cannot orphan it
        self.save()
        try:
            result = self.executor.attempt(agent=step["agent"], session=session, first=first, title=title, message=message,
                                           timeout=self.limits.get("attempt_seconds"), env=self.env, cwd=self.workdir,
                                           auto=bool(self.definition["settings"].get("allow_asks")), poll=self.poll)
        except Exception as exc:  # an executor that fails is an agent failure, never a pass
            result = {"exit": None, "outcome": "agent_error", "session": session, "cost": None, "tokens": None, "sessions": None,
                      "seconds": 0.0, "error": f"{type(exc).__name__}: {exc}"[:200], "text": ""}
        outcome = result["outcome"]
        if outcome == "returned" or result.get("sessions"):
            self.confirmed.add(session)
        if result.get("sessions"):
            self.roots[session] = int(result["sessions"])
        reply = (result.get("text") or "").strip()
        self.attempt["agent"] = {"session": session, "exit": result.get("exit"), "outcome": outcome,
                                 "seconds": round(result.get("seconds") or 0, 2), "cost": result.get("cost"),
                                 "tokens": result.get("tokens"), "sessions": result.get("sessions"), "error": result.get("error"),
                                 "reply": reply[-400:]}
        self.last_agent = outcome
        return outcome

    def _check(self, step) -> str:
        n = self.attempt["n"]
        path = self.folder / f"attempt-{n}" / "check.out"
        result = run_check(step["command"], step.get("timeout"), self.workdir, self.env, path, self.poll)
        outcome = check_outcome(result["exit"], result["timed_out"], step.get("inconclusive_codes", []))
        data = path.read_bytes()
        self.attempt["check"] = {"exit": result["exit"], "outcome": outcome, "seconds": round(result["seconds"], 2),
                                 "output": f"attempt-{n}/check.out", "output_sha256": hashlib.sha256(data).hexdigest(),
                                 "timed_out": result["timed_out"], "error": result["error"]}
        self.attempt["ended"] = round(time.time(), 3)
        self.last_check = {"exit": result["exit"], "outcome": outcome, "tail": tail_of(data.decode("utf-8", "replace"))}
        return outcome


# ---- starting and running ----

def _new_run_folder(project) -> tuple[str, Path]:
    base = loops_folder(project)
    base.mkdir(parents=True, exist_ok=True)
    while True:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
        try:
            (base / run_id).mkdir()
            return run_id, base / run_id
        except FileExistsError:
            continue


def start(project, flow_id: str, *, structure=None, goal: str | None = None, workdir=None, wait: bool = False, executor=None,
          executor_spec: dict | None = None, scratch=None, executable=None, db=None) -> str:
    """Write the run folder and start the run: detached (default) or, with `wait`, in this process. Returns the run id.
    LoopError (nothing written) when the flow is not there or is not a self-loop or has no saved loop; a loop that cannot run
    (no check, no goal, unreadable settings, no opencode) is written as a run with the status `invalid` and no attempt."""
    project = Path(project).absolute()
    path, shape = read_flow_of(structure, flow_id)
    if not snapshot_json.is_self_loop(path):
        raise LoopError(MULTI)
    problem, settings = None, None
    try:
        settings = load_settings(structure, flow_id)
        if settings is None:
            raise LoopError(f"no loop is saved for flow {flow_id}; save one with `agentgraph loop set {flow_id} --check CMD --attempts N`")
    except LoopError as exc:
        if str(exc).startswith("no loop is saved"):
            raise
        problem = str(exc)
    work = Path(workdir).absolute() if workdir else project
    goal = goal if goal is not None else (settings or {}).get("goal")
    steps: list[dict] = []
    if problem is None:
        if path[0] not in shape["group_of"]:
            problem = f"the agent {path[0]!r} is not in the structure"
        elif not work.is_dir():
            problem = f"the work folder {work} does not exist"
        elif not goal or not goal.strip():
            problem = "no goal: give --goal-file / --goal, or save one with the loop"
        elif len(goal) > GOAL_LIMIT:
            problem = f"the goal is longer than {GOAL_LIMIT} characters"
        else:
            try:
                steps = compile(path, settings)
            except LoopError as exc:
                problem = str(exc)
    spec = None
    if problem is None:
        if executor is not None:
            spec = executor.spec()
        elif executor_spec is not None:
            spec, executor = executor_spec, None
        else:
            try:
                executor = OpenCodeExecutor(executable, bench.open_scratch(scratch) if scratch else None, db)
                spec = executor.spec()
            except (bench.BenchError, LoopError) as exc:
                problem = f"the opencode CLI cannot be used: {exc}"
        if problem is None and executor is None:
            try:
                executor = make_executor(spec)
            except (bench.BenchError, LoopError, KeyError) as exc:
                problem = f"the executor cannot be used: {exc}"
    run_id, folder = _new_run_folder(project)
    now = round(time.time(), 3)
    definition = {"version": VERSION, "run": run_id, "created": now, "flow": flow_id, "path": path, "structure": str(structure_dir(structure)),
                  "settings": settings, "steps": steps, "steps_sha256": steps_hash(steps), "goal": goal, "workdir": str(work),
                  "project": str(project), "executor": spec}
    with open(folder / "definition.json", "xb") as stream:
        stream.write(_json(definition))
    state = {"version": VERSION, "run": run_id, "flow": flow_id, "status": "queued", "reason": None, "goal": goal, "workdir": str(work),
             "pid": None, "heartbeat": now, "created": now, "sessions": [], "attempts": [],
             "limits": (settings or {}).get("limits"), "totals": {"cost": None, "seconds": 0, "sessions": None, "attempts": 0}}
    if problem is not None:
        state.update(status="invalid", reason=problem, ended=now)
        _write_atomic(folder / "run.json", _json(state))
        _event(folder, attempt=0, step="-", outcome="invalid", route="end", reason=problem)
        return run_id
    _write_atomic(folder / "run.json", _json(state))
    _event(folder, attempt=0, step="-", outcome="queued", route="-")
    if wait:
        execute(folder, executor)
        return run_id
    log = open(folder / "run.log", "ab")
    try:
        subprocess.Popen([sys.executable, "-P", "-m", "agentgraph.looprun", "run", "--run-dir", str(folder)], cwd=project,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env={**os.environ, "PYTHONPATH": str(paths.REPO)})
    finally:
        log.close()
    return run_id


def execute(folder, executor=None) -> str:
    """Run a queued run folder in this process and return its final status. Refused when another process holds the folder or
    the run is not `queued`."""
    folder = Path(folder)
    definition = read_definition(folder)
    lock = open(folder / "lock", "a+")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(f"run {folder.name} is already running in another process") from None
        state = read_run(folder)
        if state.get("status") != "queued":
            raise Busy(f"run {folder.name} is already {state.get('status')}; only a queued run can be started")
        if executor is None:
            executor = make_executor(definition["executor"])
        env = executor.env(definition["workdir"])
        controller = Controller(folder, definition, state, executor, env)
        previous = None
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGTERM, lambda *_: setattr(controller, "terminated", True))
        try:
            return controller.run()
        except BaseException as exc:  # leave a final record rather than a run that says `running`
            if state.get("status") == "running":
                state.update(status="agent_error", reason=f"the controller failed: {type(exc).__name__}: {exc}"[:200], ended=round(time.time(), 3))
                _write_atomic(folder / "run.json", _json(state))
            raise
        finally:
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
    finally:
        lock.close()


# ---- command line ----

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentgraph loop", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, project=False):
        p.add_argument("--structure", metavar="DIR", help="the structure folder (default: the active one)")
        if project:
            p.add_argument("--project", metavar="DIR", default=".", help="the project folder that holds .opencode/loops (default: here)")

    p = sub.add_parser("set", help="save or change the loop of a flow")
    p.add_argument("flow")
    p.add_argument("--check", metavar="CMD")
    p.add_argument("--attempts", type=int)
    goal = p.add_mutually_exclusive_group()
    goal.add_argument("--goal")
    goal.add_argument("--goal-file", metavar="F")
    p.add_argument("--check-timeout", type=float)
    p.add_argument("--inconclusive", type=int, nargs="*", metavar="CODE")
    p.add_argument("--session", choices=("reuse", "fresh"))
    p.add_argument("--attempt-seconds", type=float)
    p.add_argument("--seconds", type=float)
    p.add_argument("--cost", type=float)
    asks = p.add_mutually_exclusive_group()
    asks.add_argument("--allow-asks", dest="allow_asks", action="store_true", default=None)
    asks.add_argument("--no-allow-asks", dest="allow_asks", action="store_false")
    common(p)
    p = sub.add_parser("show", help="the saved loop of a flow")
    p.add_argument("flow")
    p.add_argument("--json", action="store_true")
    common(p)
    p = sub.add_parser("unset", help="remove the saved loop of a flow")
    p.add_argument("flow")
    common(p)
    p = sub.add_parser("start", help="start a run of a flow's loop")
    p.add_argument("flow")
    goal = p.add_mutually_exclusive_group()
    goal.add_argument("--goal")
    goal.add_argument("--goal-file", metavar="F")
    p.add_argument("--workdir", metavar="D")
    p.add_argument("--wait", action="store_true", help="run in the foreground and print the result")
    p.add_argument("--scratch", metavar="DIR", help="run the agent on this scratch config (agentgraph bench scratch-config)")
    p.add_argument("--executable", metavar="F", help="the opencode CLI (default: OPENCODE_CLI or the app's)")
    p.add_argument("--db", metavar="F", help="the opencode database to read cost from")
    common(p, True)
    p = sub.add_parser("run", help="(internal) run a queued run folder")
    p.add_argument("--run-dir", required=True, metavar="D")
    p = sub.add_parser("status", help="one run (default: the newest)")
    p.add_argument("run", nargs="?")
    p.add_argument("--json", action="store_true")
    common(p, True)
    p = sub.add_parser("list", help="the runs, newest first")
    p.add_argument("--json", action="store_true")
    common(p, True)
    p = sub.add_parser("cancel", help="ask a run to stop")
    p.add_argument("run")
    common(p, True)
    return parser


def _read_goal(args) -> str | None:
    if getattr(args, "goal_file", None):
        try:
            return Path(args.goal_file).read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise LoopError(f"cannot read the goal file: {exc}") from None
    return getattr(args, "goal", None)


def _set(args) -> int:
    path, _shape = read_flow_of(args.structure, args.flow)
    if not snapshot_json.is_self_loop(path):
        raise LoopError(MULTI)
    current = load_settings(args.structure, args.flow) or {"version": VERSION, "flow": args.flow, "limits": {}}
    if args.check is not None:
        current.setdefault("check", {})["command"] = args.check
    for key, value in (("timeout", args.check_timeout), ("inconclusive_codes", args.inconclusive)):
        if value is not None:
            if "check" not in current:
                raise LoopError("--check is needed before the check's timeout or codes")
            current["check"][key] = value
    goal = _read_goal(args)
    if goal is not None:
        current["goal"] = goal
    for key, value in (("attempts", args.attempts), ("attempt_seconds", args.attempt_seconds), ("seconds", args.seconds), ("cost", args.cost)):
        if value is not None:
            current["limits"][key] = int(value) if key == "attempts" else value
    if args.session:
        current["session"] = args.session
    if args.allow_asks is not None:
        current["allow_asks"] = args.allow_asks
    saved = save_settings(args.structure, args.flow, current)
    print(f"saved the loop of flow {args.flow}: {saved}")
    return 0


def _line(settings: dict) -> str:
    limits = settings["limits"]
    bits = [f"attempts {limits['attempts']}"] + [f"{k.replace('_', ' ')} {limits[k]:g}" for k in ("attempt_seconds", "seconds", "cost") if k in limits]
    check = settings.get("check")
    return (f"flow {settings['flow']}: check {check['command']!r} (timeout {check['timeout']:g}s)" if check else f"flow {settings['flow']}: no check") + \
        f", {', '.join(bits)}, session {settings['session']}" + (", allow asks" if settings["allow_asks"] else "")


def _start(args) -> int:
    run_id = start(args.project, args.flow, structure=args.structure, goal=_read_goal(args), workdir=args.workdir, wait=args.wait,
                   scratch=args.scratch, executable=args.executable, db=args.db)
    folder = run_dir(Path(args.project).absolute(), run_id)
    state = read_run(folder)
    if state["status"] == "invalid" or args.wait:
        print(describe(folder, state))
        return 0 if state["status"] == "passed" else 1
    print(f"run {run_id} started: agentgraph loop status {run_id}")
    return 0


def _find_run(args) -> tuple[Path, dict]:
    project = Path(args.project).absolute()
    run_id = args.run or next(iter(list_runs(project)), None)
    if run_id is None:
        raise Busy(f"no runs in {loops_folder(project)}")
    folder = run_dir(project, run_id)
    if not folder.is_dir():
        raise Busy(f"no run {run_id} in {loops_folder(project)}")
    return folder, read_run(folder)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "set":
            return _set(args)
        if args.command == "show":
            settings = load_settings(args.structure, args.flow)
            if settings is None:
                raise Busy(f"no loop is saved for flow {args.flow}")
            print(json.dumps(settings, indent=2, sort_keys=True) if args.json else _line(settings))
            return 0
        if args.command == "unset":
            if not remove_settings(args.structure, args.flow):
                raise Busy(f"no loop is saved for flow {args.flow}")
            print(f"removed the loop of flow {args.flow}; runs already made are unchanged")
            return 0
        if args.command == "start":
            return _start(args)
        if args.command == "run":
            folder = Path(args.run_dir)
            status = execute(folder)
            print(describe(folder, read_run(folder)))
            return 0 if status == "passed" else 1
        if args.command == "status":
            folder, state = _find_run(args)
            state = {**state, "status": shown_status(folder, state)} if args.json else state
            print(json.dumps(state, indent=2, sort_keys=True) if args.json else describe(folder, state))
            return 0
        if args.command == "list":
            project = Path(args.project).absolute()
            rows = [(rid, read_run(loops_folder(project) / rid)) for rid in list_runs(project)]
            if args.json:
                print(json.dumps([{**s, "status": shown_status(loops_folder(project) / rid, s)} for rid, s in rows], sort_keys=True))
            else:
                for rid, state in rows:
                    print(describe(loops_folder(project) / rid, state))
            return 0
        if args.command == "cancel":
            project = Path(args.project).absolute()
            folder = run_dir(project, args.run)
            if not folder.is_dir():
                raise LoopError(f"no run {args.run} in {loops_folder(project)}")
            state = read_run(folder)
            if state.get("status") not in ("queued", "running"):
                raise Busy(f"run {args.run} is already {state.get('status')}")
            request_cancel(folder)
            print(f"cancel requested for run {args.run}: it stops before the next attempt, or now if an agent turn is running")
            return 0
    except LoopError as exc:
        print(f"agentgraph loop: {exc}", file=sys.stderr)
        return exc.code
    except bench.BenchError as exc:
        print(f"agentgraph loop: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
