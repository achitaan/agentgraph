"""Run one node of a task graph as a loop: `agentgraph node run|status|wait|cancel`.

    node run    --task T --node N [--check CMD] [--verify AGENT] [--attempts K] [--cost DOLLARS] [--attempt-seconds S]
                [--raise-budget] [--amend-check] [--accept-unpriced]
                [--brief FILE] [--wait] [--executor scripted:FILE] [--dir D] [--check-timeout S] [--scratch DIR] [--executable E] [--db F]
    node status --task T --node N [--dir D]
    node wait   --task T --node N [--timeout S] [--dir D]
    node cancel --task T --node N [--dir D]

`run` makes a loop of the node in memory (the worker is the node's agent, the rounds are the loop's attempts) and hands it to the loop
controller (agentgraph.looprun), which runs the check itself in the project folder, sends the tail of a failing check back to the same
session, counts attempts, seconds and dollars, and stops on a limit, a stall or a turn whose fate is not known. With a verifier the check
passes first and the verifier judges once per pass (`VERDICT: PASS` or `VERDICT: FAIL`); a rejection goes back to the worker. Nothing is
drawn or saved in the structure folder; the run is an ordinary run in `.opencode/loops` (flow `node-TASK-NODE`), so `agentgraph loop status`
shows it and its sessions are titled `agentgraph-loop: ...` as every loop run's are.

While the run goes the graph node is kept current under the graph's lock: status running, session, attempts, loop_run, started, spent
(a running total over every run of the node), unpriced (turns with no known cost). At the end the node is `returned` when the check passed
(never `passed`: the orchestrator evaluates), `failed` when the attempts or the spend or a time limit ran out, `blocked` when it could not go
on (check error, agent error, a person's or `node cancel`'s stop). A report goes to `.opencode/artifacts/TASK/NODE/node-run-RUN.md`.
`status` prints one line and never any message text:
    WORD attempts=N cost=$D seconds=S check="CMD" sha256=H report=PATH
    (WORD: passed-check, passed-check-flagged, failed, limit, blocked, running; exit codes in `exit_code`)

The budget belongs to the node: `--cost` and `--attempts` set it only when the node has none; a later run gets what is left of it (the
controller is given cost minus what the node has spent, attempts minus what it has used) and is refused at zero. `--raise-budget` changes
a budget on purpose and writes an `amended` event. The check and the verifier are frozen once the node was dispatched, like `done_when`:
another check needs `--amend-check` (an `amended` event and a line in the graph's amendments). The command is pinned at the start of the run.
A passing check is flagged (`passed-check-flagged`, exit code 2) only when (a) a file the command names (or a file below a folder it names)
changed during the run, (b) this node's own definition, `check` or `verify` changed in the graph, or (c) a changed file lies outside the node's
scope, in the scope of no other node that was running meanwhile, and is not ignored by git (a node with no scope is not compared). Anything else
that changed is listed in the report without a flag.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import graphedit, looprun, outputs, paths, transitions

DEFAULT_ATTEMPTS = 3
DEFAULT_ATTEMPT_SECONDS = 1800
POLL = 2.0  # seconds between looks at the run record while the node is kept current
# any other final status says `blocked`. The loop's `stalled` is a worker turn stopped at its time limit: a limit, not a stall.
WORD = {"passed": "passed-check", "failed": "failed", "limit": "limit", "stalled": "limit"}
EXIT = {"passed-check": 0, "failed": 1, "stalled": 1, "limit": 1, "blocked": 2, "passed-check-flagged": 2, "running": 3}
NOT_A_WORKER = ("orchestrator", "user")
NOT_A_WORK_TYPE = ("check", "join", "human", "gate")
AGENT = re.compile(r"[a-z0-9][a-z0-9-]*")  # what `opencode run --agent` takes
SKIP = {".git", "__pycache__", "node_modules", ".venv", ".mypy_cache", ".pytest_cache"}
FILE_LIMIT = 20000
BRIEF_LIMIT = 12000
SHOWN = 20  # files named in a flag
CUT = 60  # characters of the check command in the status line and the evidence
DEFINITION = ("id", "type", "agent", "goal", "scope", "depends_on", "done_when")  # what a node is, as against how its run went
_DROP = object()  # in `extra`: take the field out of the node


class NodeRefused(Exception):
    """A node that cannot be run, or a run that cannot be asked: the message is plain words for the person."""


class WaitTimeout(NodeRefused):
    """`wait` gave up: the run is still going."""


class _Lost(NodeRefused):
    """The node no longer belongs to the run that is being followed."""


class _Same(Exception):
    """Nothing to write: the node already says what the run says."""


# ---- the graph ----

def _locate(task: str, directory) -> tuple[Path, Path]:
    """(the graph file, the project it belongs to)."""
    try:
        path = outputs.find_graph(task, outputs.graph_dirs(directory))
    except outputs.OutputError as exc:
        raise NodeRefused(str(exc)) from None
    project = outputs.project_of(path)
    if project is None:
        raise NodeRefused(f"{path} is not in a project's .opencode/graph folder, so there is no project to run the node in")
    return path, project


def _read(path: Path) -> dict:
    try:
        return outputs._parse(path.read_bytes(), path.name)
    except (OSError, outputs.OutputError) as exc:
        raise NodeRefused(f"cannot read {path.name}: {exc}") from None


def _node_of(raw: dict, node_id: str) -> dict:
    try:
        return graphedit._find(raw, node_id)
    except outputs.OutputError as exc:
        raise NodeRefused(str(exc)) from None


def _edit(path: Path, node_id: str, change: dict, extra: dict | None = None, guard=None, now: float | None = None,
          amendment: dict | None = None) -> str:
    """`graphedit.edit` with the fields graphedit does not know (`extra`: field -> value, or _DROP), a `guard(raw, node)` that runs under
    the graph's lock before anything changes, and an `amendment` ({"event": text for the amended event, "line": a line for the graph's
    amendments, which also takes the revision up, or None}). The same lock, the same rules: a write that would add a problem to the graph is
    refused (Refused), and nothing is written."""
    now = round(time.time() if now is None else now, 3)
    result: dict = {}
    with outputs._GraphFolder(path.parent) as folder:
        def make(current: bytes) -> bytes:
            raw = outputs._parse(current, path.name)
            if guard is not None:
                guard(raw, _node_of(raw, node_id))
            try:
                previous = transitions.load_state(folder, path.stem)
            except transitions.HistoryError:
                previous = None
            before_state = previous if previous is not None else transitions.state_of(raw)
            before = graphedit._problems(raw, path, previous)
            new = json.loads(json.dumps(raw))
            result["line"] = graphedit.apply(new, node_id, change, now)
            node = _node_of(new, node_id)
            for key, value in (extra or {}).items():
                if value is _DROP:
                    node.pop(key, None)
                else:
                    node[key] = value
            if amendment:
                graphedit._append(new, "amended", node_id, amendment["event"], now)
                if amendment.get("line"):
                    new["revision"] = (new["revision"] if type(new.get("revision")) is int else 1) + 1
                    if not isinstance(new.get("amendments"), list):
                        new["amendments"] = []
                    new["amendments"].append(amendment["line"])
            after = graphedit._problems(new, path, before_state)
            added = [problem for problem in after if problem not in before]
            if added:
                raise graphedit.Refused("this write would add " + ("a problem" if len(added) == 1 else f"{len(added)} problems")
                                        + f" to {path.name}: " + "; ".join(added))
            result["clean"] = not after
            result["state"] = transitions.state_of(new)
            return outputs._serialize(new)
        outputs._replace_graph(folder, path.name, make)
        if result.get("clean"):
            try:
                transitions.save_state(folder, path.stem, result["state"])
            except OSError:
                pass
    return result["line"]


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")


def _typed(raw: dict, node: dict) -> None:
    """NodeRefused, in plain words, for any field this module reads that has the wrong type (the graph's own `check` says more)."""
    name = node.get("id")
    wrong = []
    for field in ("status", "agent", "type", "goal", "done_when", "check", "verify", "loop_run"):
        if node.get(field) is not None and not isinstance(node[field], str):
            wrong.append(f"{field} must be text")
    for field in ("depends_on", "scope"):
        value = node.get(field)
        if value is not None and not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
            wrong.append(f"{field} must be a list of text")
    if node.get("attempts") is not None and not (type(node["attempts"]) is int and node["attempts"] >= 0):
        wrong.append("attempts must be a whole number, 0 or more")
    if node.get("unpriced") is not None and not (type(node["unpriced"]) is int and node["unpriced"] >= 0):
        wrong.append("unpriced must be a whole number, 0 or more")
    if node.get("spent") is not None and not (_number(node["spent"]) and node["spent"] >= 0):
        wrong.append("spent must be dollars (a number, 0 or more)")
    budget = node.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            wrong.append("budget must be an object with cost and attempts")
        else:
            if budget.get("cost") is not None and not (_number(budget["cost"]) and budget["cost"] > 0):
                wrong.append("budget cost must be a number above 0")
            if budget.get("attempts") is not None and not (type(budget["attempts"]) is int and budget["attempts"] >= 1):
                wrong.append("budget attempts must be a whole number, 1 or more")
    if raw.get("goal") is not None and not isinstance(raw["goal"], str):
        wrong.append("the graph's goal must be text")
    if wrong:
        raise NodeRefused(f"node {name} cannot be run: " + "; ".join(wrong))


def _going(project: Path, node: dict) -> str | None:
    """`running` or `queued` when the loop run the node names still has a controller (or was queued a moment ago); else None."""
    run = node.get("loop_run")
    if not isinstance(run, str) or not run:
        return None
    try:
        folder = looprun.run_dir(project, run)
        state = looprun.read_run(folder)
    except (looprun.LoopError, OSError, ValueError):
        return None
    if state.get("status") in looprun.FINAL:
        return None
    shown = looprun.shown_status(folder, state)
    return shown if shown in ("running", "queued") else None


def _settle(path: Path, project: Path, task: str, node_id: str, raw: dict, node: dict) -> tuple[dict, dict]:
    """A node whose last loop run is not over and has no controller (paused, waiting, interrupted, queued and never taken) must not be left
    as it is when another run starts: an orphaned `opencode run` goes on spending, and `loop resume` would bring the old run back with no
    follower. The old run is ended the way `node cancel` ends it (cancelled, its leftover agent processes stopped), what it did is added to
    the node's `attempts`, `spent` and `unpriced`, and one line says so. Returns the graph and the node as they are then."""
    run = node.get("loop_run")
    if node.get("status") in ("running", "passed") or not isinstance(run, str) or not run:
        return raw, node
    try:
        folder = looprun.run_dir(project, run)
        state = looprun.read_run(folder)
    except (looprun.LoopError, OSError, ValueError):
        return raw, node
    if state.get("status") in looprun.FINAL or _going(project, node):
        return raw, node
    was = looprun.shown_status(folder, state)
    try:
        answer = looprun.cancel(folder)
    except looprun.Busy:
        return _read(path), _node_of(_read(path), node_id)  # it ended by itself in between
    if answer != "cancelled":
        raise NodeRefused(f"node {node_id}'s loop run {run} is still going, whatever the node says: agentgraph node cancel --task {task} "
                          f"--node {node_id} stops it, agentgraph node wait --task {task} --node {node_id} waits for it")
    stopped, spared = _kill_leftovers(state.get("sessions") or [])
    state = looprun.read_run(folder)
    try:
        now = _progress(state, looprun.read_definition(folder))
    except looprun.LoopError:
        now = None
    held_attempts = node.get("attempts") if type(node.get("attempts")) is int else 0
    held_spent = node.get("spent") if _number(node.get("spent")) else None
    held_unpriced = node.get("unpriced") if type(node.get("unpriced")) is int else 0
    attempts = max(held_attempts, now["attempts"]) if now else held_attempts
    spent = max(held_spent or 0.0, now["spent"] or 0.0) if now and (now["spent"] is not None or held_spent is not None) else held_spent
    unpriced = max(held_unpriced, now["unpriced"]) if now else held_unpriced
    extra = {**({"spent": spent} if spent is not None else {}), "unpriced": unpriced if unpriced else _DROP}
    _edit(path, node_id, {"attempts": str(attempts), "note": f"loop run {run} ({was}) was cancelled before a new run"}, extra)
    said = f"node {node_id}: the earlier loop run {run} was {was} and is now cancelled (attempts {attempts}, " + (
        f"spent ${spent:.4f}" if spent is not None else "spent unknown") + (f", {unpriced} turn(s) with no known cost" if unpriced else "") + ")"
    if stopped:
        said += f"; {stopped} leftover agent process{'es' if stopped != 1 else ''} stopped"
    if spared:
        said += f"; NOT stopped (not an orphaned group leader): pid {', '.join(map(str, spared))}"
    print(said)
    raw = _read(path)
    return raw, _node_of(raw, node_id)


def _refusal(raw: dict, node: dict, command, project: Path | None = None) -> str:
    """Why this node cannot be run now, or ''."""
    task = raw.get("task") if isinstance(raw.get("task"), str) else "TASK"
    name = node.get("id")
    status = node.get("status", "pending")
    if status in ("running", "passed"):
        if status == "running":
            run = f" (loop run {node['loop_run']})" if node.get("loop_run") else ""
            return (f"node {name} is already running{run}: agentgraph node cancel --task {task} --node {name} stops it, "
                    f"agentgraph node wait --task {task} --node {name} waits for it")
        return f"node {name} is already passed"
    by_id = {n.get("id"): n for n in raw.get("nodes", []) if isinstance(n, dict)}
    waiting = [dep for dep in node.get("depends_on") or [] if (by_id.get(dep) or {}).get("status") != "passed"]
    if waiting:
        return f"node {name} is not ready: {', '.join(waiting)} {'has' if len(waiting) == 1 else 'have'} not passed"
    agent = node.get("agent")
    if not isinstance(agent, str) or agent in NOT_A_WORKER or node.get("type") in NOT_A_WORK_TYPE or not AGENT.fullmatch(agent):
        return f"node {name} has no agent that can do the work (agent {agent!r}, type {node.get('type')!r})"
    if not command:
        return f"node {name} has no check command: give --check CMD, or put a check in the node"
    going = _going(project, node) if project is not None else None
    if going:
        return (f"node {name}'s loop run {node['loop_run']} is still going ({going}), whatever the node says: "
                f"agentgraph node cancel --task {task} --node {name} stops it, agentgraph node wait --task {task} --node {name} waits for it")
    return ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cut(text: str, length: int = CUT) -> str:
    one = " ".join(str(text).split()).replace('"', "'")
    return one if len(one) <= length else one[:length - 3] + "..."


def _plan(raw: dict, node: dict, project: Path, *, check, verify, attempts, cost, raise_budget: bool, amend_check: bool,
          accept_unpriced: bool = False) -> dict:
    """Everything a run of this node needs to know before it starts, or NodeRefused: the command, the verifier, the budget (what is left of
    it, and what an amendment changes), and the amendment the run has to write."""
    _typed(raw, node)
    task = raw.get("task") if isinstance(raw.get("task"), str) else "TASK"
    name = node["id"]
    command = check or node.get("check") or None
    verifier = verify or node.get("verify") or None
    reason = _refusal(raw, node, command, project)
    if reason:
        raise NodeRefused(reason)
    amended = []  # (field, was, now)
    if transitions.dispatched(node):
        for field, now in (("check", check), ("verify", verify)):
            was = node.get(field)
            if now and was and now != was:
                amended.append((field, was, now))
        if amended and not amend_check:
            field, was, now = amended[0]
            raise NodeRefused(f"node {name} was already dispatched with the {field} {_cut(was, 100)!r} (sha256 {_sha(was)[:16]}), and {now!r} is "
                              f"another one: a changed {field} would let a node pass by a weaker test. If the change is meant, add --amend-check "
                              f"(it writes an amended event and a line in the graph's amendments); otherwise run with the node's own {field}")
    stored = node.get("budget") if isinstance(node.get("budget"), dict) else {}
    stored_cost, stored_attempts = stored.get("cost"), stored.get("attempts")
    changes = []
    if stored_cost is not None and cost is not None and cost != stored_cost:
        changes.append(f"cost ${stored_cost:g} -> ${cost:g}")
    if stored_attempts is not None and attempts is not None and attempts != stored_attempts:
        changes.append(f"attempts {stored_attempts} -> {attempts}")
    if changes and not raise_budget:
        raise NodeRefused(f"node {name} already has a budget (cost ${stored_cost}, attempts {stored_attempts}), and --cost and --attempts do not "
                          f"change it ({', '.join(changes)}). To change it on purpose, add --raise-budget (it writes an amended event)")
    limit_cost = cost if cost is not None else stored_cost
    limit_attempts = attempts if attempts is not None else (stored_attempts if stored_attempts is not None else DEFAULT_ATTEMPTS)
    if limit_cost is None:
        raise NodeRefused("a run needs a spend limit: give --cost DOLLARS, or put a budget cost in the node")
    used = node.get("attempts") if type(node.get("attempts")) is int else 0
    spent = node.get("spent") if _number(node.get("spent")) else None
    unpriced = node.get("unpriced") if type(node.get("unpriced")) is int else 0
    how = f"agentgraph node run --task {task} --node {name} --raise-budget --cost DOLLARS --attempts K"
    if raise_budget and cost is not None and spent is not None and cost < spent:
        raise NodeRefused(f"node {name} has already spent ${spent:.4f}, so a budget of ${cost:g} is below what is spent: give a cost above it")
    if raise_budget and attempts is not None and attempts < used:
        raise NodeRefused(f"node {name} has already used {used} attempts, so a budget of {attempts} is below what is used: give a number above it")
    if limit_attempts - used <= 0:
        raise NodeRefused(f"node {name} has used {used} of its {limit_attempts} attempts, so another run has none left. To go on, raise the "
                          f"budget by an explicit amendment: {how}")
    if limit_cost - (spent or 0.0) <= 0:
        raise NodeRefused(f"node {name} has spent ${spent or 0.0:.4f} of its ${limit_cost:g} budget, so another run has nothing left. To go on, "
                          f"raise the budget by an explicit amendment: {how}")
    if unpriced and not (raise_budget or accept_unpriced):
        known = f"at least ${spent:.4f}" if spent is not None else "an unknown amount"
        raise NodeRefused(f"node {name} had {unpriced} turn{'s' if unpriced != 1 else ''} with no known cost, so it spent {known} and what is left of its "
                          f"${limit_cost:g} budget cannot be known. To go on knowing that, add --accept-unpriced (or --raise-budget with a limit you "
                          f"trust): agentgraph node run --task {task} --node {name} --accept-unpriced")
    budget = dict(stored)  # the budget the node keeps: what it had, and what an amendment changes
    added = []
    if stored_cost is None or (cost is not None and raise_budget):
        budget["cost"] = limit_cost
        if stored and stored_cost is None:
            added.append(f"cost ${limit_cost:g} added")
    if (stored_attempts is None and (not stored or attempts is not None)) or (attempts is not None and raise_budget):
        budget["attempts"] = limit_attempts
        if stored and stored_attempts is None:
            added.append(f"attempts {limit_attempts} added")
    notes, lines = [], []
    if changes or added:
        notes.append("budget changed by node run --raise-budget: " + ", ".join(changes + added))
    if unpriced and (raise_budget or accept_unpriced):
        notes.append(f"continued with {unpriced} turn{'s' if unpriced != 1 else ''} of no known cost" + (" (--accept-unpriced)" if accept_unpriced else ""))
    for field, was, now in amended:
        line = (f"{name} {field} changed (sha256 {_sha(was)[:12]} -> {_sha(now)[:12]}): {_cut(was, 80)!r} -> {_cut(now, 80)!r}: "
                f"agentgraph node run --amend-check")
        notes.append(line)
        lines.append(line)
    amendment = {"event": "; ".join(notes), "line": "; ".join(lines) or None} if notes else None
    return {"command": command, "verifier": verifier, "budget": budget,
            "cost_left": round(limit_cost - (spent or 0.0), 8), "attempts_left": limit_attempts - used, "before": used, "spent": spent,
            "unpriced": unpriced, "amendment": amendment}


# ---- the run ----

def _flow_id(task: str, node: str) -> str:
    return ("node-" + re.sub(r"[^A-Za-z0-9_.-]+", "-", f"{task}-{node}"))[:80].rstrip("-.")


def _goal(raw: dict, node: dict, brief: str | None) -> str:
    scope = node.get("scope") or []
    lines = [f"Task: {raw.get('goal', '')}".rstrip(), f"Your part (node {node.get('id')}): {node.get('goal', '')}",
             f"Done when: {node.get('done_when', '')}",
             "Files you may change: " + (", ".join(scope) if scope else "none (read only)")]
    if brief:
        lines += ["", "Brief:", brief.strip()]
    return "\n".join(lines)


def _read_brief(name) -> str | None:
    if not name:
        return None
    try:
        text = Path(name).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise NodeRefused(f"cannot read the brief {name}: {exc}") from None
    if len(text) > BRIEF_LIMIT:
        raise NodeRefused(f"the brief is {len(text)} characters; the limit is {BRIEF_LIMIT}")
    return text


def _executor_spec(args) -> dict:
    spec = getattr(args, "executor", None)
    if spec:
        kind, _, name = spec.partition(":")
        if kind != "scripted" or not name:
            raise NodeRefused("--executor takes scripted:FILE (a JSON list or {agent: list} of scripted turns)")
        try:
            attempts = json.loads(Path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise NodeRefused(f"cannot read the scripted turns {name}: {exc}") from None
        if not isinstance(attempts, (list, dict)):
            raise NodeRefused("the scripted turns must be a JSON list or object")
        return {"kind": "scripted", "attempts": attempts}
    try:
        scratch = looprun.bench.open_scratch(args.scratch) if getattr(args, "scratch", None) else None
        return looprun.OpenCodeExecutor(getattr(args, "executable", None), scratch, getattr(args, "db", None)).spec()
    except (looprun.bench.BenchError, looprun.LoopError) as exc:
        raise NodeRefused(f"the opencode CLI cannot be used: {exc}") from None


def run(task: str, node_id: str, *, directory=None, check=None, verify=None, attempts=None, cost=None, attempt_seconds=None, brief=None,
        check_timeout=None, wait=False, executor_spec: dict | None = None, executor=None, args=None, raise_budget: bool = False,
        amend_check: bool = False, accept_unpriced: bool = False) -> tuple[str, str]:
    """Claim the node, write the loop run and start it (detached, or here with `wait`). Returns (run id, status line). NodeRefused (nothing
    changed) when the node cannot be run."""
    path, project = _locate(task, directory)
    raw = _read(path)
    node = _node_of(raw, node_id)
    _typed(raw, node)
    raw, node = _settle(path, project, task, node_id, raw, node)
    given = {"check": check, "verify": verify, "attempts": attempts, "cost": cost, "raise_budget": raise_budget, "amend_check": amend_check,
             "accept_unpriced": accept_unpriced}
    plan = _plan(raw, node, project, **given)
    command, verifier = plan["command"], plan["verifier"]
    worker = node["agent"]
    if verifier and verifier == worker:
        raise NodeRefused(f"the verifier must be a different agent from the worker ({worker})")
    if verifier and not AGENT.fullmatch(verifier):
        raise NodeRefused(f"{verifier!r} is not an agent name")
    text = _goal(raw, node, _read_brief(brief))
    flow = _flow_id(task, node_id)
    # the controller is given what is left of the budget, not the whole of it
    limits = {"attempts": plan["attempts_left"], "attempt_seconds": attempt_seconds if attempt_seconds is not None else DEFAULT_ATTEMPT_SECONDS,
              "cost": plan["cost_left"]}
    try:
        settings = looprun.validate({"version": looprun.VERSION, "flow": flow, "goal": text, "limits": limits, "session": "reuse",
                                     "check": {"command": command, **({"timeout": check_timeout} if check_timeout else {})}},
                                    f"the run of node {node_id}")
        loop_path = [worker, verifier, worker] if verifier else [worker, worker]
        steps = looprun.compile(loop_path, settings)
    except looprun.LoopError as exc:
        raise NodeRefused(str(exc)) from None
    if executor is not None:
        executor_spec = executor.spec()
    elif executor_spec is None:
        executor_spec = _executor_spec(args)
    run_id, folder = looprun._new_run_folder(project)
    now = round(time.time(), 3)
    before = plan["before"]  # the graph counts rounds over every run of the node
    retry = node.get("status", "pending") != "pending"  # the graph wants a retry to count as an attempt from the start
    definition = {"version": looprun.VERSION, "run": run_id, "created": now, "flow": flow, "path": loop_path, "structure": "", "settings": settings,
                  "steps": steps, "steps_sha256": looprun.steps_hash(steps), "goal": text, "workdir": str(project), "project": str(project),
                  "executor": executor_spec, "trial": False, "settings_sha256": hashlib.sha256(looprun._json(settings)).hexdigest(),
                  "node": {"task": task, "node": node_id, "graph": str(path), "before": before, "retry": retry, "scope": list(node.get("scope") or []),
                           "check_sha256": _sha(command), "spent_before": plan["spent"], "unpriced_before": plan["unpriced"]}}
    try:
        (folder / "definition.json").write_bytes(looprun._json(definition))
        state = {"version": looprun.VERSION, "run": run_id, "flow": flow, "status": "queued", "reason": None, "goal": text, "workdir": str(project),
                 "pid": None, "heartbeat": now, "created": now, "sessions": [], "attempts": [], "limits": settings["limits"],
                 "totals": {"cost": None, "seconds": 0, "sessions": None, "attempts": 0, "cost_unknown_steps": 0}, "trial": False,
                 "settings_sha256": definition["settings_sha256"], "steps_sha256": definition["steps_sha256"], "cursor": None,
                 "recoveries": [], "session_costs": {}}
        looprun._write_atomic(folder / "run.json", looprun._json(state))
        looprun._event(folder, attempt=0, step="-", outcome="queued", route="-")
        extra = {"loop_run": run_id, "check": command, "started": now, "ended": _DROP, "failure_kind": _DROP,
                 "budget": plan["budget"], **({"verify": verifier} if verifier else {})}
        _edit(path, node_id, {"status": "running", "attempts": str(before + retry), "stalled": False, "note": f"loop run {run_id}", "session": ""}, extra,
              guard=lambda current, item: _plan(current, item, project, **given), amendment=plan["amendment"])
    except BaseException as exc:
        shutil.rmtree(folder, ignore_errors=True)  # nothing ran: the folder was never the node's run
        if isinstance(exc, (graphedit.Refused, outputs.OutputError)):
            raise NodeRefused(str(exc)) from None
        raise
    if wait:
        drive(folder, path, task, node_id, executor)
        return run_id, status_line(task, node_id, directory)
    log = open(folder / "node.log", "ab")
    try:
        proc = subprocess.Popen([sys.executable, "-P", "-m", "agentgraph.noderun", "drive", "--run-dir", str(folder), "--graph", str(path),
                                 "--task", task, "--node", node_id], cwd=project, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True, env={**os.environ, "PYTHONPATH": str(paths.REPO)})
        threading.Thread(target=proc.wait, daemon=True).start()
    finally:
        log.close()
    return run_id, ""


def _snapshot(project: Path) -> tuple[dict, bool]:
    """({relative path: [size, mtime_ns]} of the files below the project, whether the walk was cut short at FILE_LIMIT). The folders in SKIP are
    left out, and of `.opencode` only `graph` is looked at (its `.history` is not): a worker that edits a task graph is seen."""
    found: dict = {}
    for root, dirs, files in os.walk(project):
        here = Path(root).relative_to(project).as_posix()
        if here == ".":
            dirs[:] = [d for d in dirs if d not in SKIP]
        elif here == ".opencode":
            dirs[:] = [d for d in dirs if d == "graph"]
            files = []
        elif here == ".opencode/graph":
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if d not in SKIP and d != ".opencode"]
        for name in files:
            if len(found) >= FILE_LIMIT:
                return found, True
            full = Path(root) / name
            try:
                info = full.lstat()
            except OSError:
                continue
            found[full.relative_to(project).as_posix()] = [info.st_size, info.st_mtime_ns]
    return found, False


def _node_digest(raw: dict, node_id: str) -> str:
    """The node's own definition: its id, type, agent, goal, scope, dependencies and done_when, and its check and verify. How its runs went
    (status, session, attempts, spent, ...) is not in it, and neither is any other node: the orchestrator adding or editing another node while
    the run goes is not a reason to look twice."""
    node = next((n for n in raw.get("nodes", []) if isinstance(n, dict) and n.get("id") == node_id), {})
    return _sha(json.dumps({key: node.get(key) for key in (*DEFINITION, "check", "verify")}, sort_keys=True, default=str))


def _record_others(folder: Path, raw: dict, node_id: str) -> None:
    """Remember the scopes of the other nodes of the graph that are running now: a file one of them writes is theirs, not this node's stray."""
    try:
        have = json.loads((folder / "others.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        have = {}
    now = {n["id"]: [e for e in n.get("scope") or [] if isinstance(e, str)] for n in raw.get("nodes", [])
           if isinstance(n, dict) and isinstance(n.get("id"), str) and n["id"] != node_id and n.get("status") == "running"
           and isinstance(n.get("scope"), list)}
    merged = {**have, **{k: sorted(set(have.get(k, [])) | set(v)) for k, v in now.items()}}
    if merged != have:
        try:
            (folder / "others.json").write_text(json.dumps(merged), encoding="utf-8")
        except OSError:
            pass


def drive(folder, path, task: str, node_id: str, executor=None) -> str | None:
    """The process that runs the controller for a node's run and brings the node up to date: while the run goes (a thread), and at the end."""
    folder, path = Path(folder), Path(path)
    project = Path(looprun.read_definition(folder)["project"])
    try:
        files, cut = _snapshot(project)
        (folder / "files-before.json").write_text(json.dumps({"complete": not cut, "files": files}), encoding="utf-8")
        first = _read(path)
        (folder / "graph-before.json").write_text(json.dumps({"digest": _node_digest(first, node_id)}), encoding="utf-8")
        _record_others(folder, first, node_id)
    except (OSError, NodeRefused):
        pass
    stop = threading.Event()
    thread = threading.Thread(target=_follow, args=(folder, path, node_id, stop), daemon=True)
    thread.start()
    status = None
    try:
        status = looprun.execute(folder, executor)
    except looprun.Busy:
        pass  # a controller already holds this run: it is not ours to end
    except Exception as exc:  # noqa: BLE001 - the run must say why it did not start
        looprun._never_ran(folder, None, exc)
        print(f"the controller failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        stop.set()
        thread.join(timeout=POLL * 3)
    if (folder / LOST).is_file():  # the node was taken from this run: nothing else will write its report
        try:
            state = looprun.read_run(folder)
            if state.get("status") in looprun.FINAL:
                _publish(path, project, folder, state, task, node_id, folder.name)
        except (looprun.LoopError, OSError, NodeRefused):
            pass
    reconcile(path, task, node_id)
    return status


def _counted(state: dict, definition: dict) -> int:
    """The node's attempts: those before this run, and this run's rounds (a retry has at least one)."""
    info = definition.get("node") or {}
    rounds = len(state.get("attempts") or [])
    return info.get("before", 0) + (max(rounds, 1) if info.get("retry") else rounds)


def _progress(state: dict, definition: dict) -> dict:
    """What the run says about the node now: its attempts, session, and the money over every run of the node (`spent` is None until some turn's
    cost is known; `unpriced` counts the turns whose cost is not)."""
    info = definition.get("node") or {}
    attempts = state.get("attempts") or []
    session = next((s.get("session") for a in attempts for s in a.get("steps") or [] if s.get("role") == "work" and s.get("session")), None)
    totals = state.get("totals") or {}
    cost, prior = totals.get("cost"), info.get("spent_before")
    spent = prior if cost is None else round((prior or 0.0) + cost, 8)
    return {"attempts": _counted(state, definition), "session": session, "spent": spent,
            "unpriced": (info.get("unpriced_before") or 0) + (totals.get("cost_unknown_steps") or 0)}


def _money_fields(now: dict) -> dict:
    """The `extra` fields for the money of `_progress`."""
    return {**({"spent": now["spent"]} if now["spent"] is not None else {}), "unpriced": now["unpriced"] if now["unpriced"] else _DROP}


def _differs(node: dict, now: dict) -> bool:
    """Whether the node does not yet say what the run says."""
    return (node.get("attempts") != now["attempts"] or (bool(now["session"]) and node.get("session") != now["session"])
            or (now["spent"] is not None and node.get("spent") != now["spent"]) or (node.get("unpriced") or 0) != now["unpriced"])


def _follow(folder: Path, path: Path, node_id: str, stop: threading.Event) -> None:
    """While the run goes: the node says what the run says, and a node that was taken from the run (somebody else set its status, or ran it
    again) ends the run: two writers must not work on one node."""
    definition = looprun.read_definition(folder)
    while not stop.wait(POLL):
        try:
            state = looprun.read_run(folder)
        except looprun.LoopError:
            continue
        if state.get("status") in looprun.FINAL:
            return
        now = _progress(state, definition)
        try:
            current = _read(path)
            _record_others(folder, current, node_id)
            item = _node_of(current, node_id)
            _owns(item, folder.name)
            if not _differs(item, now):
                continue
            change = {"attempts": str(now["attempts"])}
            if now["session"]:
                change["session"] = now["session"]

            def guard(raw, current):
                _owns(current, folder.name)
                if not _differs(current, now):
                    raise _Same()
            _edit(path, node_id, change, _money_fields(now), guard=guard)
        except _Same:
            continue
        except _Lost as exc:
            _lose(folder, str(exc))
            return
        except (graphedit.Refused, NodeRefused, outputs.OutputError, OSError):
            continue


LOST = "node-lost.txt"


def _lose(folder: Path, why: str) -> None:
    """Ask for the run to be cancelled, and say why in the report."""
    try:
        (folder / LOST).write_text(f"{why}; the run was asked to stop, as two writers must not work on one node\n", encoding="utf-8")
        looprun.cancel(folder)
    except (looprun.LoopError, looprun.Busy, OSError):
        pass


def _owns(node: dict, run_id: str) -> None:
    if node.get("loop_run") != run_id or node.get("status") != "running":
        raise _Lost(f"the node no longer belongs to this run (it is {node.get('status')}, its loop run is {node.get('loop_run')})")


# ---- the end ----

def _tail(folder: Path, relative, lines: int = 8) -> str:
    try:
        text = (folder / relative).read_bytes().decode("utf-8", "replace")
    except (OSError, TypeError):
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _command(definition: dict) -> str:
    return str(((definition.get("settings") or {}).get("check") or {}).get("command") or "")


def _watched(command: str, project: Path) -> list[str]:
    """What below the project the check command names: a word of it that is a file (a dotted name like `tests.test_x` counts as the file it
    imports from), or a folder (written with a trailing `/`: any file below it counts, as in `pytest tests/`)."""
    root = project.resolve()
    found: list[str] = []
    for word in re.split(r"[\s;&|<>()=,:]+", command):
        word = word.strip("'\"`")
        if not word or word.startswith("-"):
            continue
        for name in (word, word.replace(".", "/") + ".py" if re.fullmatch(r"\w+(\.\w+)+", word) else ""):
            if not name:
                continue
            try:
                full = (root / name).resolve()
                if root in full.parents and (full.is_file() or full.is_dir()):
                    relative = full.relative_to(root).as_posix() + ("/" if full.is_dir() else "")
                    if relative not in found:
                        found.append(relative)
                    break
            except (OSError, ValueError):
                continue
    return found


def _touched(names: list[str], watched: list[str]) -> list[str]:
    """Those of the changed files that are a watched file, or below a watched folder."""
    return [n for n in names if any(n == w or (w.endswith("/") and n.startswith(w)) for w in watched)]


def _ignored(project: Path, names: list[str]) -> set[str] | None:
    """Which of the files git ignores (one `git check-ignore` for the whole list); None when the project is not in a git work tree (or git is
    not there): then the question is not asked."""
    if not names:
        return set()
    try:
        done = subprocess.run(["git", "-C", str(project), "check-ignore", "-z", "--stdin"], input="\0".join(names) + "\0", capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode not in (0, 1):
        return None
    return {n for n in done.stdout.split("\0") if n}


def _inside(name: str, scope: list[str], project: Path) -> bool:
    """Whether the file is in the node's write scope (a file, a folder, or a pattern with * or ?)."""
    for entry in scope:
        entry = entry.strip()
        if os.path.isabs(entry):
            try:
                entry = Path(entry).relative_to(project).as_posix()
            except ValueError:
                continue
        entry = os.path.normpath(entry).replace(os.sep, "/")
        if entry in (".", ""):
            return True
        if name == entry or name.startswith(entry.rstrip("/") + "/") or (any(c in entry for c in "*?[") and fnmatch.fnmatch(name, entry)):
            return True
    return False


def _active_scopes(folder: Path, raw: dict, node_id: str, began: float, ended: float) -> list[str]:
    """The scope entries of the other nodes of the graph that were running at any time while this run went: those seen running by `_follow`,
    and those whose own start and end (or still running) overlap the run."""
    try:
        seen = json.loads((folder / "others.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    scopes = [e for entries in seen.values() if isinstance(entries, list) for e in entries if isinstance(e, str)]
    for other in raw.get("nodes", []):
        if not isinstance(other, dict) or other.get("id") == node_id or not isinstance(other.get("scope"), list):
            continue
        start, end = other.get("started"), other.get("finished") if _number(other.get("finished")) else other.get("ended")
        if other.get("status") == "running" or (_number(start) and start <= ended and _number(end) and end >= began):
            scopes += [e for e in other["scope"] if isinstance(e, str)]
    return scopes


def _assess(folder: Path, project: Path, definition: dict, path: Path, node_id: str) -> dict:
    """What changed in the project while the run went, and whether that makes a passing check suspect. Worked out once, when the run is over
    (`assessment.json` in the run folder keeps it): `inside` (files in the node's scope), `elsewhere` (the rest: may be other work), `deleted`,
    `complete` (False when the file count hit FILE_LIMIT: the lists are then incomplete and no deletion is reported), `no_scope` (the node has
    no scope: changed files were not compared) and `flags`, the reasons to look twice. There are three, and only three:
    a file (or a file below a folder) the check command names changed; this node's own definition, `check` or `verify` changed in the graph;
    a changed file lies outside the node's scope (the node has one), is in the scope of no other node that was running meanwhile, and is not
    ignored by git. Everything else is listed, without a flag."""
    cache = folder / "assessment.json"
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    info = definition.get("node") or {}
    command = _command(definition)
    try:
        saved = json.loads((folder / "files-before.json").read_text(encoding="utf-8"))
        before, complete = saved["files"], bool(saved.get("complete"))
    except (OSError, ValueError, KeyError, TypeError):
        before, complete = None, False
    scope = [entry for entry in info.get("scope") or [] if isinstance(entry, str) and entry.strip()]
    result = {"inside": [], "elsewhere": [], "deleted": [], "complete": complete, "flags": [], "known": before is not None, "no_scope": not scope}
    mine = f".opencode/graph/{path.name}"
    flags = result["flags"]
    try:
        raw = _read(path)
    except NodeRefused:
        raw = {}
    if before is not None:
        after, cut = _snapshot(project)
        result["complete"] = complete = complete and not cut
        changed = sorted(k for k in after if before.get(k) != after[k] and k != mine)
        deleted = sorted(k for k in before if k not in after and k != mine) if complete else []
        result["deleted"] = [k for k in deleted if not k.startswith(".opencode/graph/")]
        result["inside"] = [k for k in changed if _inside(k, scope, project)]
        result["elsewhere"] = [k for k in changed if not _inside(k, scope, project)]
        everything = [*changed, *result["deleted"]]
        watched = _watched(command, project)
        named = _touched(everything, watched)
        # a file the command names is always a flag; one that is only below a named folder is not when it is in a SKIP folder or git ignores it
        below = [k for k in named if k not in watched and not any(part in SKIP for part in k.split("/"))]
        others: list[str] = []
        stray: list[str] = []
        if scope:
            try:
                began = float(definition.get("created"))
            except (TypeError, ValueError):
                began = 0.0
            others = _active_scopes(folder, raw, node_id, began, time.time())
            stray = [k for k in everything if not _inside(k, scope, project) and not _inside(k, others, project) and not k.startswith(".opencode/graph/")]
        ignored = _ignored(project, sorted(set(below) | set(stray)))  # one call for both lists
        if ignored is not None:
            stray = [k for k in stray if k not in ignored]
        named = [k for k in named if k in watched or (k in below and not (ignored and k in ignored))]
        if named:
            flags.append("a file the check command names changed during the run: " + ", ".join(named[:SHOWN]) + (f" and {len(named) - SHOWN} more" if len(named) > SHOWN else ""))
        if stray:
            flags.append(f"{len(stray)} changed file{'s' if len(stray) != 1 else ''} lie{'s' if len(stray) == 1 else ''} outside the node's scope ({', '.join(scope)}), in no running "
                         "node's scope, not ignored by git: " + ", ".join(stray[:SHOWN]) + (f" and {len(stray) - SHOWN} more" if len(stray) > SHOWN else ""))
    try:
        first = json.loads((folder / "graph-before.json").read_text(encoding="utf-8"))["digest"]
        now = _node_of(raw, node_id)
        if _node_digest(raw, node_id) != first:
            if command and isinstance(now.get("check"), str) and now["check"] != command:
                flags.append(f"the node's check was changed in the graph during the run (the run used sha256 {_sha(command)[:16]}, the node has "
                             f"{_sha(now['check'])[:16]})")
            else:
                flags.append("the node's own definition (goal, scope, dependencies, done_when, check or verify) changed in the graph during the run")
    except (OSError, ValueError, KeyError, TypeError, NodeRefused):
        pass
    try:
        cache.write_text(json.dumps(result), encoding="utf-8")
    except OSError:
        pass
    return result


def _word(state: dict, assessment: dict | None = None) -> str:
    """passed-check, passed-check-flagged, failed, limit, blocked (running is for a run that is not over)."""
    word = WORD.get(state.get("status"), "blocked")
    return "passed-check-flagged" if word == "passed-check" and assessment and assessment.get("flags") else word


def _money(cost, unknown: int = 0) -> str:
    """The dollars of a run: exact, `at least` when some turns have no known cost, `unknown` when none has."""
    if cost is None:
        return "unknown"
    text = f"${cost:.4f}"
    return f"at least {text} ({unknown} turn{'s' if unknown != 1 else ''} with no known cost)" if unknown else text


def report_path(task: str, node_id: str, run_id: str) -> str:
    return f".opencode/artifacts/{task}/{node_id}/node-run-{run_id}.md"


def _listing(names: list[str], limit: int = 200) -> list[str]:
    return [f"- {name}" for name in names[:limit]] + ([f"- ... and {len(names) - limit} more"] if len(names) > limit else [])


def _report(folder: Path, definition: dict, state: dict, task: str, node_id: str, assessment: dict) -> str:
    totals = state.get("totals") or {}
    cost = totals.get("cost")
    limits = state.get("limits") or {}
    command = _command(definition)
    word = _word(state, assessment)
    lines = [f"# Node run {state['run']}: {task} / {node_id}", "",
             f"- result: {word} (loop run status {state.get('status')}): {state.get('reason') or ''}".rstrip(": "),
             f"- rounds: {len(state.get('attempts') or [])} of at most {limits.get('attempts')} (what was left of the node's budget)",
             f"- cost: {_money(cost, totals.get('cost_unknown_steps') or 0)} (limit ${limits.get('cost')}, what was left of the node's budget)",
             f"- seconds: {totals.get('seconds') or 0:.0f}",
             f"- check: {_cut(command, 200)} (sha256 {_sha(command)})",
             f"- worker: {definition['path'][0]}" + (f", verifier: {definition['path'][1]}" if len(definition["path"]) == 3 else ""),
             f"- sessions: {', '.join(state.get('sessions') or []) or 'none'}"]
    try:
        lines.append("- note: " + (folder / LOST).read_text(encoding="utf-8").strip())
    except OSError:
        pass
    lines.append("")
    if assessment.get("flags"):
        lines += ["## Flagged: the passed check needs a second look" if word == "passed-check-flagged" else "## Flags", ""]
        lines += [f"- {flag}" for flag in assessment["flags"]] + [""]
    lines += ["## Rounds", ""]
    for attempt in state.get("attempts") or []:
        lines.append(f"### Round {attempt.get('n')}")
        for entry in attempt.get("steps") or []:
            if entry.get("kind") == "check":
                lines += [f"- check: exit {entry.get('exit')} ({entry.get('outcome')})", "", "```",
                          _tail(folder, (attempt.get("check") or {}).get("output")) or "(no output)", "```"]
            elif entry.get("role") == "judge":
                finding = _tail(folder, entry.get("findings"), 3)
                lines.append(f"- verifier {entry.get('agent')}: {entry.get('outcome')}" + (f" - {finding.splitlines()[0][:200]}" if finding else ""))
            else:
                lines.append(f"- worker {entry.get('agent')}: {entry.get('outcome')}"
                             + (f", ${entry['cost']:.4f}" if entry.get("cost") is not None else ", cost unknown") + f", session {entry.get('session')}")
        lines.append("")
    lines += ["## Files changed", ""]
    if assessment.get("no_scope"):
        lines += ["No scope set, so changed files were not compared with it.", ""]
    lines += ["Inside the node's scope (the node's):", ""] + (_listing(assessment["inside"]) or ["none"]) + [
        "", "Also changed in the project during the run (may be other work):", ""] + (_listing(assessment["elsewhere"]) or ["none"]) + [""]
    if assessment.get("deleted"):
        lines += ["Deleted:", ""] + _listing(assessment["deleted"]) + [""]
    if not assessment.get("complete"):
        lines += [f"The project has more than {FILE_LIMIT} files (or the list before the run was not kept): this list is incomplete, "
                  "and no deletion is reported.", ""] if assessment.get("known") else [
                  "The files before the run were not recorded: no list of changed files.", ""]
    session_costs = state.get("session_costs") or {}
    lines += ["## Sessions", ""] + [f"- {sid}: " + ("$%.4f" % c if c is not None else "cost unknown") for sid, c in session_costs.items()] + [""]
    return "\n".join(lines)


def _last_check(state: dict) -> str:
    for attempt in reversed(state.get("attempts") or []):
        check = attempt.get("check")
        if check:
            return f"the last check exited {check.get('exit')}" if check.get("exit") is not None else f"the last check was {check.get('outcome')}"
    return "no check ran"


def _evidence(state: dict, report: str, definition: dict, assessment: dict) -> str:
    word = _word(state, assessment)
    totals = state.get("totals") or {}
    rounds, spent = len(state.get("attempts") or []), _money(totals.get("cost"), totals.get("cost_unknown_steps") or 0)
    command = _command(definition)
    pinned = f'check "{_cut(command)}" sha256 {_sha(command)}'
    if word in ("passed-check", "passed-check-flagged"):
        verifier = " and the verifier passed it" if state.get("passed_by") == "check+judges" else ""
        flagged = f" FLAGGED: {'; '.join(assessment['flags'])[:400]};" if word == "passed-check-flagged" else ""
        return f"the check passed{verifier} in round {rounds};{flagged} {spent}; {pinned}; report {report}"
    return (f"{word} after {rounds} round{'s' if rounds != 1 else ''}: {str(state.get('reason') or '')[:120]}; {_last_check(state)}; {spent}; "
            f"{pinned}; report {report}")


def reconcile(path, task: str, node_id: str) -> str:
    """Bring the node up to date with its run once the run is over; the status line. A node that says running while its run is final is
    ended here (a driver that was killed leaves it so); a run that is not over leaves the node as it is."""
    path = Path(path)
    raw = _read(path)
    node = _node_of(raw, node_id)
    run_id = node.get("loop_run")
    if isinstance(run_id, str) and run_id:
        project = outputs.project_of(path)
        folder = looprun.run_dir(project, run_id)
        try:
            state = looprun.read_run(folder)
        except looprun.LoopError:
            state = None
        if state and state.get("status") in looprun.FINAL and node.get("status") == "running":
            _finish(path, project, folder, state, task, node_id, run_id)
    return status_line(task, node_id, path=path)


def _publish(path: Path, project: Path, folder: Path, state: dict, task: str, node_id: str, run_id: str) -> tuple[str, dict]:
    """The report of a run that is over: written to the artifacts folder; (its path below the project, the assessment)."""
    definition = looprun.read_definition(folder)
    assessment = _assess(folder, project, definition, path, node_id)
    relative = report_path(task, node_id, run_id)
    target = project / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_report(folder, definition, state, task, node_id, assessment), encoding="utf-8")
    return relative, assessment


def _finish(path: Path, project: Path, folder: Path, state: dict, task: str, node_id: str, run_id: str) -> None:
    definition = looprun.read_definition(folder)
    relative, assessment = _publish(path, project, folder, state, task, node_id, run_id)
    status = state.get("status")
    word = _word(state, assessment)
    held = _node_of(_read(path), node_id).get("attempts")  # the graph may have been taken further while the run went: attempts never go down
    counted = max(held if type(held) is int else 0, _counted(state, definition))
    change = {"status": {"passed-check": "returned", "passed-check-flagged": "returned", "failed": "failed", "limit": "failed"}.get(word, "blocked"),
              "evidence": _evidence(state, relative, definition, assessment), "attempts": str(counted), "note": f"loop run {run_id}: {word}"}
    now = _progress(state, definition)
    if now["session"]:
        change["session"] = now["session"]
    extra = {"ended": state.get("ended") or round(time.time(), 3), **_money_fields(now)}
    if word == "limit":
        extra["failure_kind"] = "limit"
    elif word == "failed":
        extra["failure_kind"] = "code"
    elif status in ("check_error", "agent_error"):
        extra["failure_kind"] = "environment"
    try:
        try:
            _edit(path, node_id, change, extra, guard=lambda raw, item: _owns(item, run_id))
        except graphedit.Refused as exc:
            if change["status"] == "blocked":
                raise
            # the graph will not take the result: the node must not stay `running`, so it is blocked, with the refusal as the reason
            change = {"status": "blocked", "note": f"loop run {run_id}: {word}, result refused",
                      "evidence": f"the run ended {word} (report {relative}), but the graph refused the node's result: {str(exc)[:300]}"}
            _edit(path, node_id, change, {"ended": extra["ended"], "failure_kind": "environment"}, guard=lambda raw, item: _owns(item, run_id))
    except NodeRefused:
        pass  # somebody already brought the node up to date, or took it for another run
    except graphedit.Refused as exc:
        print(f"the node could not be updated: {exc}", file=sys.stderr)
        raise NodeRefused(f"the run is over but the node could not be updated: {exc}") from None


# ---- status, wait, cancel ----

def _state_of(path: Path, node_id: str) -> tuple[Path, dict | None, dict]:
    raw = _read(path)
    node = _node_of(raw, node_id)
    run_id = node.get("loop_run")
    if not isinstance(run_id, str) or not run_id:
        raise NodeRefused(f"node {node_id} has no run: start one with agentgraph node run")
    project = outputs.project_of(path)
    folder = looprun.run_dir(project, run_id)
    try:
        return folder, looprun.read_run(folder), node
    except looprun.LoopError:
        return folder, None, node


def status_line(task: str, node_id: str, directory=None, path=None) -> str:
    if path is None:
        path, _ = _locate(task, directory)
    path = Path(path)
    folder, state, node = _state_of(path, node_id)
    run_id = folder.name
    if state is None:
        return f"blocked attempts={node.get('attempts', 0)} cost=unknown seconds=0 report=- (loop run {run_id} is missing)"
    shown = looprun.shown_status(folder, state)
    final = state.get("status") in looprun.FINAL
    project = outputs.project_of(path)
    try:
        definition = looprun.read_definition(folder)
    except looprun.LoopError:
        definition = {}
    assessment = _assess(folder, project, definition, path, node_id) if final and state.get("status") == "passed" and definition else None
    word = _word(state, assessment) if final else ("running" if shown in ("queued", "running") else "blocked")
    totals = state.get("totals") or {}
    relative = report_path(task, node_id, run_id)
    report = relative if final and (project / relative).is_file() else "-"
    command = _command(definition)
    pinned = f' check="{_cut(command)}" sha256={_sha(command)}' if command else ""
    return (f"{word} attempts={len(state.get('attempts') or [])} cost={_money(totals.get('cost'), totals.get('cost_unknown_steps') or 0)} "
            f"seconds={totals.get('seconds') or 0:.0f}{pinned} report={report}")


def exit_code(line: str) -> int:
    """The exit code a status line stands for: 0 passed-check; 1 failed or limit (or stalled); 2 blocked, or passed-check-flagged (the check
    passed but something about the run needs a look); 3 running (for `wait`, the timeout)."""
    return EXIT.get(line.split()[0] if line.split() else "", 2)


def status(task: str, node_id: str, directory=None) -> str:
    path, _ = _locate(task, directory)
    return reconcile(path, task, node_id)


def wait(task: str, node_id: str, directory=None, timeout: float | None = None) -> str:
    """Block until the run is over (or no controller holds it: `blocked`), then the status line. WaitTimeout when `timeout` seconds pass first."""
    path, _ = _locate(task, directory)
    began = time.monotonic()
    while True:
        line = reconcile(path, task, node_id)
        if not line.startswith("running"):
            return line
        if timeout is not None and time.monotonic() - began >= timeout:
            raise WaitTimeout(f"still running after {timeout:g} seconds: {line}")
        time.sleep(0.25)


def _processes(sessions: list[str]) -> list[tuple[int, int]]:
    """(pid, parent pid) of the processes whose command line names one of the sessions (`opencode run ... --session ID`)."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=15, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        words = line.split()
        if len(words) > 2 and words[0].isdigit() and words[1].isdigit() and int(words[0]) != os.getpid() and "run" in words[2:] and any(
                a == "--session" and b in sessions for a, b in zip(words[2:], words[3:])):
            found.append((int(words[0]), int(words[1])))
    return found


def _leftovers(sessions: list[str]) -> list[int]:
    return [pid for pid, _ in _processes(sessions)]


def _orphan(pid: int, parent: int) -> bool:
    """A process a controller left behind: it leads its own process group (how looprun starts every child) and nobody is its parent any more."""
    try:
        return parent == 1 and os.getpgid(pid) == pid
    except OSError:
        return False


def _kill_leftovers(sessions: list[str]) -> tuple[int, list[int]]:
    """Stop the agent processes of a run whose controller is gone (a controller that was killed leaves the `opencode run` it started: that
    process has its own process group and goes on, and spends, with nobody to read its answer). Only an orphaned group leader is stopped, as a
    whole group: a process of the same session that is somebody's own (a person's `opencode run --session`, a job of a shell or a script)
    is left alone. Returns (how many were stopped, the pids that were left alone and are still running)."""
    sessions = [s for s in sessions if isinstance(s, str)]
    mine = [pid for pid, parent in _processes(sessions) if _orphan(pid, parent)]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in mine:
            try:
                os.killpg(pid, sig)
            except OSError:
                continue
        deadline = time.monotonic() + 2
        alive = mine
        while alive and time.monotonic() < deadline:
            alive = [pid for pid in alive if pid in _leftovers(sessions)]
            if alive:
                time.sleep(0.1)
        if not alive:
            break
    still = _leftovers(sessions)
    return len(mine) - len([pid for pid in mine if pid in still]), [pid for pid in still if pid not in mine]


def cancel(task: str, node_id: str, directory=None) -> str:
    path, project = _locate(task, directory)
    folder, state, node = _state_of(path, node_id)
    if state is None or state.get("status") in looprun.FINAL:
        raise NodeRefused(f"node {node_id} has no run that is going on ({status_line(task, node_id, path=path).split()[0]})")
    try:
        answer = looprun.cancel(folder)
    except looprun.Busy as exc:
        raise NodeRefused(str(exc)) from None
    if answer == "cancelled":  # no controller stood behind the run: whatever it started is still going
        _, spared = _kill_leftovers(state.get("sessions") or [])
        if spared:
            print(f"node {node_id}: a process on this run's session is still running and was left alone (it is not an orphaned group leader): pid "
                  + ", ".join(map(str, spared)))
    deadline = time.monotonic() + 20
    while answer == "requested" and time.monotonic() < deadline:
        if looprun.read_run(folder).get("status") in looprun.FINAL:
            break
        time.sleep(0.2)
    return reconcile(path, task, node_id)


# ---- command line ----

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentgraph node", description="run one node of a task graph as a loop")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--task", required=True)
        p.add_argument("--node", required=True)
        p.add_argument("--dir", type=Path, metavar="D", help="graph folder (default: the nearest .opencode/graph)")

    p = sub.add_parser("run", help="work the node in rounds until its check passes or a limit is reached")
    common(p)
    p.add_argument("--check", metavar="CMD", help="the command that decides (default: the node's check)")
    p.add_argument("--verify", metavar="AGENT", help="an agent that judges once the check passes (default: the node's verify)")
    p.add_argument("--attempts", type=int, metavar="K", help=f"rounds at most (default: the node's budget, else {DEFAULT_ATTEMPTS})")
    p.add_argument("--cost", type=float, metavar="DOLLARS", help="spend limit (required here or as the node's budget cost)")
    p.add_argument("--raise-budget", action="store_true", help="change the node's budget on purpose (without it --cost and --attempts only set a budget the node lacks)")
    p.add_argument("--accept-unpriced", action="store_true", help="go on although earlier turns had no known cost (the spend is only a lower bound)")
    p.add_argument("--amend-check", action="store_true", help="use another check or verifier than the node was dispatched with (an amendment)")
    p.add_argument("--attempt-seconds", type=float, metavar="S", help=f"seconds one worker turn may take (default {DEFAULT_ATTEMPT_SECONDS})")
    p.add_argument("--check-timeout", type=float, metavar="S", help="seconds the check may take (default 300)")
    p.add_argument("--brief", metavar="FILE", help="text added to the worker's first message")
    p.add_argument("--wait", action="store_true", help="run here and print the status line at the end")
    p.add_argument("--executor", metavar="scripted:FILE", help="scripted turns instead of opencode (tests)")
    p.add_argument("--scratch", metavar="DIR", help="a scratch config to run opencode with (benchmarks)")
    p.add_argument("--executable", metavar="PATH")
    p.add_argument("--db", metavar="FILE")
    for name, text in (("status", "one line: how the run stands"), ("wait", "wait for the run to end, then the same line"),
                       ("cancel", "stop the run")):
        p = sub.add_parser(name, help=text)
        common(p)
        if name == "wait":
            p.add_argument("--timeout", type=float, metavar="S")
    p = sub.add_parser("drive")  # the detached process of `run`
    p.add_argument("--run-dir", required=True)
    p.add_argument("--graph", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--node", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(args_in)
    try:
        if args.command == "drive":
            drive(args.run_dir, args.graph, args.task, args.node)
            return 0
        if args.command == "run":
            run_id, line = run(args.task, args.node, directory=args.dir, check=args.check, verify=args.verify, attempts=args.attempts,
                               cost=args.cost, attempt_seconds=args.attempt_seconds, brief=args.brief, check_timeout=args.check_timeout,
                               wait=args.wait, args=args, raise_budget=args.raise_budget, amend_check=args.amend_check,
                               accept_unpriced=args.accept_unpriced)
            if args.wait:
                print(line)
                return exit_code(line)
            print(f"run {run_id} started: agentgraph node status --task {args.task} --node {args.node}")
            return 0
        if args.command == "status":
            line = status(args.task, args.node, args.dir)
        elif args.command == "wait":
            line = wait(args.task, args.node, args.dir, args.timeout)
        else:
            line = cancel(args.task, args.node, args.dir)
        print(line)
        return exit_code(line) if args.command in ("status", "wait") else 0
    except WaitTimeout as exc:
        print(str(exc))
        return EXIT["running"]
    except (NodeRefused, graphedit.Refused, outputs.OutputError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
