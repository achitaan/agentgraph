"""The page's side of a saved loop: what `GET /api/loops`, `GET /api/loop/run` and `POST /api/loop` answer (contract:
`.opencode/artifacts/agentgraph-loops/v1/r1/api.md`, WEB_API.md). Pure functions over folders; the server only parses the
request and writes the reply. Everything about a loop (settings, runs, the check, the limits) is `agentgraph.looprun`'s; this
module holds no run state, a run is its own process and the files under `<project>/.opencode/loops/<run id>/` are the truth.

Every refusal is an `ApiError` carrying the HTTP status (400 the request or the controller's message, 404 an unknown run,
409 a stale `rev` or a run that is not running, with the current `rev` for the first). `rev` is the sha256 of the settings
file's bytes ("" when there is none); `save` and `remove` compare it under the lock the controller writes under, so two pages
cannot overwrite each other. A run id is checked against the run id pattern before any path is built; a work folder must be an
absolute folder that exists; a check's output is read as its last `OUTPUT_CHARS` characters."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import looprun
from .snapshot_json import STRUCTURE_DIR, load_structure

OUTPUT_CHARS = 20_000
RUNS_SHOWN = 50
GOAL_SHOWN = 200
STEP_TEXT_CHARS = 20_000
OPS = {"save": ({"op", "flow", "settings", "rev"}, {"op", "flow", "settings", "rev"}),
       "remove": ({"op", "flow", "rev"}, {"op", "flow", "rev"}),
       "start": ({"op", "flow"}, {"op", "flow", "goal", "workdir", "settings"}),
       "cancel": ({"op", "run"}, {"op", "run"}),
       "pause": ({"op", "run"}, {"op", "run"}),
       "resume": ({"op", "run"}, {"op", "run", "choice"}),
       "answer": ({"op", "run", "answer"}, {"op", "run", "answer", "note"}),
       "restore": ({"op", "flow", "version", "rev"}, {"op", "flow", "version", "rev"})}
SHAPES = ('{"op": "save", "flow": id, "settings": {...}, "rev": text}, {"op": "remove", "flow": id, "rev": text}, '
          '{"op": "start", "flow": id, "goal"?: text, "workdir"?: absolute folder, "settings"?: {...} (a trial run)}, {"op": "cancel", "run": id}, '
          '{"op": "pause", "run": id}, {"op": "resume", "run": id, "choice"?: "redo"|"accept"}, '
          '{"op": "answer", "run": id, "answer": "approve"|"reject"|"stop", "note"?: text} or {"op": "restore", "flow": id, "version": id, "rev": text}')


class ApiError(Exception):
    """A refused request: `status` (400, 404, 409), the message, and any extra fields of the reply (`rev` on a 409)."""

    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra

    def body(self) -> dict:
        return {"error": self.message, **self.extra}


def rev_of(data: bytes | None) -> str:
    return hashlib.sha256(data).hexdigest() if data is not None else ""


def _read(structure, flow: str) -> bytes | None:
    try:
        return looprun.settings_path(structure, flow).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ApiError(400, f"the saved loop of {flow} cannot be read ({exc.strerror})") from None


def _settings_of(structure, flow: str) -> tuple[dict | None, str]:
    """(the validated settings or None, the problem or "")."""
    try:
        return looprun.load_settings(structure, flow), ""
    except looprun.LoopError as exc:
        return None, str(exc)


def _flow_path(structure, flow: str) -> list[str]:
    try:
        return looprun.read_flow_of(structure, flow)[0]
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None


def _flow_id(flow) -> str:
    if not isinstance(flow, str) or not looprun.FLOW_ID.fullmatch(flow):
        raise ApiError(400, f"not a flow id: {str(flow)[:60]!r}")
    return flow


def _run_folder(project, run_id) -> Path:
    try:
        folder = looprun.run_dir(project, run_id)
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None
    if folder.is_symlink() or not folder.is_dir():
        raise ApiError(404, f"no run {run_id} in {looprun.loops_folder(project)}")
    return folder


def _state(folder: Path) -> dict:
    try:
        return looprun.read_run(folder)
    except looprun.LoopError:
        raise ApiError(404, f"no readable run record for {folder.name}") from None


def _definition(folder: Path) -> dict:
    try:
        return looprun.read_definition(folder)
    except looprun.LoopError:
        return {}


def _orphans(structure, known: set[str]) -> list[str]:
    try:
        names = [p.stem for p in (looprun.structure_dir(structure) / ".loops").iterdir() if p.suffix == ".json" and p.is_file()]
    except OSError:
        return []
    return sorted(name for name in names if looprun.FLOW_ID.fullmatch(name) and name not in known)


def _leaves(value, prefix: str = "") -> dict:
    """The settings as {dotted path: value} over their leaves: objects are walked (an empty one is a leaf), lists and scalars are leaves."""
    if isinstance(value, dict) and value:
        return {path: leaf for key, item in value.items() for path, leaf in _leaves(item, f"{prefix}.{key}" if prefix else str(key)).items()}
    return {prefix: value}


def _document(data: bytes | None):
    """The plain JSON of a settings file (None when it is missing or not JSON)."""
    try:
        return json.loads((data or b"").decode("utf-8")) if data else None
    except (ValueError, UnicodeDecodeError):
        return None


def _changed(now: dict, kept: dict) -> list[str]:
    """The sorted dotted paths whose values differ between two settings documents; a path present on one side only differs. No values."""
    a, b = _leaves(now) if now else {}, _leaves(kept) if kept else {}
    missing = object()
    return sorted(path for path in a.keys() | b.keys() if a.get(path, missing) != b.get(path, missing))


def _versions(structure, flow: str) -> list[dict]:
    """The kept earlier versions, newest first, each with `changed`: the sorted dotted setting paths whose values differ from the loop saved now
    (every path of the version when there is no saved loop; `[]` when identical). Both sides are compared as validated settings (defaults filled
    in) when both validate, else as the files' plain JSON."""
    try:
        rows = looprun.versions(structure, flow)
    except looprun.LoopError:
        return []
    path = looprun.settings_path(structure, flow)
    try:
        current = _document(path.read_bytes())
    except OSError:
        current = None
    folder = looprun._history(path)
    out = []
    for row in rows:
        try:
            kept = _document((folder / f"{row['version']}.json").read_bytes())
        except OSError:
            kept = None
        a, b = current, kept
        try:
            a, b = looprun.validate(current, path) if current is not None else None, looprun.validate(kept, path) if kept is not None else None
        except looprun.LoopError:
            a, b = current, kept
        out.append({**row, "changed": _changed(a if isinstance(a, dict) else {}, b if isinstance(b, dict) else {})})
    return out


def _summary(project, run_id: str) -> dict | None:
    folder = looprun.run_dir(project, run_id)
    try:
        state = looprun.read_run(folder)
    except looprun.LoopError:
        return None
    attempts = state.get("attempts") or []
    checks = [a["check"] for a in attempts if a.get("check")]
    command = (((_definition(folder).get("settings") or {}).get("check")) or {}).get("command") or ""
    totals = state.get("totals") or {}
    return {"run": run_id, "flow": state.get("flow"), "status": looprun.shown_status(folder, state), "reason": state.get("reason") or "",
            "live": looprun._held(folder), "attempts": len(attempts), "limit": (state.get("limits") or {}).get("attempts"),
            "cost": totals.get("cost"), "seconds": totals.get("seconds") or 0, "created": state.get("created"), "ended": state.get("ended"),
            "goal": (state.get("goal") or "")[:GOAL_SHOWN], "check": command,
            "last_check": {"outcome": checks[-1].get("outcome"), "exit": checks[-1].get("exit")} if checks else None,
            "passed_by": state.get("passed_by"), "waiting": state.get("waiting"), "trial": bool(state.get("trial")),
            "settings_sha256": state.get("settings_sha256")}


def listing(structure_dir, project, scratch) -> dict:
    """`GET /api/loops`: every drawn flow with its saved loop, the settings files without a flow, and the newest runs (none, and `project` "", when there is no project)."""
    shape = load_structure(structure_dir)
    loops, known = [], set()
    for flow in (shape["flows"] if shape else []):
        known.add(flow["id"])
        reason = looprun.path_refusal(flow["path"], shape["group_of"])
        runnable = not reason
        settings, problem = _settings_of(structure_dir, flow["id"])
        loops.append({"flow": flow["id"], "name": flow.get("name") or "", "path": list(flow["path"]), "agent": flow["path"][0],
                      "runnable": runnable, "reason": reason, "settings": settings, "problem": problem,
                      "rev": rev_of(_read(structure_dir, flow["id"])), "versions": _versions(structure_dir, flow["id"])})
    known.update(flow["id"] for flow in (shape["unreadable_flows"] if shape else []))
    runs = [row for row in (_summary(project, run_id) for run_id in looprun.list_runs(project)[:RUNS_SHOWN]) if row is not None] if project is not None else []
    return {"project": str(project) if project is not None else "", "scratch": bool(scratch), "loops": loops, "orphans": _orphans(structure_dir, known), "runs": runs}


def _output(path: Path) -> tuple[str, bool]:
    try:
        text = path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return "", False
    return text[-OUTPUT_CHARS:], len(text) > OUTPUT_CHARS


def _text(folder: Path, relative) -> str:
    """The last STEP_TEXT_CHARS characters of a file the record names (a plain file inside the run folder), "" when there is none."""
    if not isinstance(relative, str) or not relative:
        return ""
    path = folder / relative
    try:
        inside = path.resolve(strict=True).is_relative_to(folder.resolve()) and not path.is_symlink()
        return path.read_bytes().decode("utf-8", "replace")[-STEP_TEXT_CHARS:] if inside and path.is_file() else ""
    except OSError:
        return ""


def run_detail(project, run_id) -> dict:
    """`GET /api/loop/run`: the run record (its status as shown, `live`), its definition and every attempt's message and check output."""
    folder = _run_folder(project, run_id)
    state = _state(folder)
    definition = _definition(folder)
    attempts = []
    for index, attempt in enumerate(state.get("attempts") or [], 1):
        n = attempt.get("n") if isinstance(attempt.get("n"), int) and not isinstance(attempt.get("n"), bool) else index
        message = folder / f"attempt-{n}" / "message.md"
        try:
            sent = message.read_text(encoding="utf-8", errors="replace")
        except OSError:
            sent = ""
        output, truncated = _output(folder / f"attempt-{n}" / "check.out")
        steps = [{"step": entry.get("step"), "try": entry.get("try"), "message": _text(folder, entry.get("message")), "reply": _text(folder, entry.get("reply")),
                  "findings": _text(folder, entry.get("findings"))} for entry in attempt.get("steps") or [] if isinstance(entry, dict)]
        attempts.append({"n": n, "message": sent, "check_output": output, "check_output_truncated": truncated, "steps": steps})
    keys = ("settings", "path", "steps", "steps_sha256", "goal", "workdir", "created")
    return {"run": {**state, "status": looprun.shown_status(folder, state), "live": looprun._held(folder)},
            "definition": {key: definition.get(key) for key in keys}, "attempts": attempts}


def _check_rev(rev, current: str) -> None:
    if not isinstance(rev, str):
        raise ApiError(400, "rev must be text (the rev the page read, \"\" for a first save)")
    if rev != current:
        raise ApiError(409, "the loop was changed since this page read it", rev=current)


def save(structure_dir, flow, settings, rev) -> dict:
    """Save the loop of a flow that loops back (A -> A, or A -> J1 -> ... -> A) if `rev` is the file's current rev; what the walker would refuse
    (a bad route, a step on an agent that is not on the path) is refused here too. `{"settings", "rev", "before"}`."""
    flow = _flow_id(flow)
    if not isinstance(settings, dict):
        raise ApiError(400, "the settings must be a JSON object")
    path = _flow_path(structure_dir, flow)
    shape = load_structure(structure_dir)
    refusal = looprun.path_refusal(path, shape["group_of"] if shape else None)
    if refusal:
        raise ApiError(400, refusal)
    target = looprun.settings_path(structure_dir, flow)
    try:
        clean = looprun.validate({"version": looprun.VERSION, "flow": flow, **settings}, target)
        if clean["flow"] != flow:
            raise looprun.LoopError(f"{target}: the settings are for flow {clean['flow']!r}")
        try:
            looprun.compile(path, clean, shape["group_of"] if shape else None)
        except looprun.NoCheck:
            pass  # saved as it is; a run of it is `invalid` until a check or a judge exists
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None
    data = (json.dumps(clean, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with looprun._Locked(target.parent):
        old = _read(structure_dir, flow)
        _check_rev(rev, rev_of(old))
        before = _settings_of(structure_dir, flow)[0] if old is not None else None
        looprun._keep(target)
        looprun._write_atomic(target, data)
    return {"settings": clean, "rev": rev_of(data), "before": before}


def remove(structure_dir, flow, rev) -> dict:
    """Delete a saved loop if `rev` is current (runs already made are unchanged). `{"before"}`: what Undo saves back."""
    flow = _flow_id(flow)
    target = looprun.settings_path(structure_dir, flow)
    if not target.parent.is_dir():
        _check_rev(rev, "")
        raise ApiError(400, f"no loop is saved for flow {flow}")
    with looprun._Locked(target.parent):
        old = _read(structure_dir, flow)
        _check_rev(rev, rev_of(old))
        if old is None:
            raise ApiError(400, f"no loop is saved for flow {flow}")
        before = _settings_of(structure_dir, flow)[0]
        looprun._keep(target)
        target.unlink(missing_ok=True)
    return {"before": before}


def restore(structure_dir, flow, version, rev) -> dict:
    """Put a kept version back as the saved loop if `rev` is current. `{"settings", "rev", "before"}` like `save`; what it replaced is kept."""
    flow = _flow_id(flow)
    if not isinstance(version, str):
        raise ApiError(400, "the version must be text")
    before = _settings_of(structure_dir, flow)[0]
    try:
        looprun.restore(structure_dir, flow, version, check=lambda old: _check_rev(rev, rev_of(old)))
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None
    data = _read(structure_dir, flow)
    return {"settings": _settings_of(structure_dir, flow)[0], "rev": rev_of(data), "before": before}


def start(structure_dir, project, flow, goal=None, workdir=None, *, scratch=None, executor_spec=None, settings=None) -> dict:
    """Write the run folder and start it detached. `{"run", "status": "running" | "invalid", "reason"}`. `settings` makes a trial run (the
    saved loop is not read or changed)."""
    flow = _flow_id(flow)
    if settings is not None and not isinstance(settings, dict):
        raise ApiError(400, "the settings must be a JSON object")
    if goal is not None and not isinstance(goal, str):
        raise ApiError(400, "the goal must be text")
    if workdir is not None:
        if not isinstance(workdir, str) or not Path(workdir).is_absolute():
            raise ApiError(400, "the work folder must be an absolute path")
        if not Path(workdir).is_dir():
            raise ApiError(400, f"the work folder {workdir} does not exist")
    if not Path(project).is_dir():
        raise ApiError(400, f"the project folder {project} does not exist")
    try:
        run_id = looprun.start(project, flow, structure=structure_dir, goal=goal if goal and goal.strip() else None, workdir=workdir,
                               scratch=scratch, executor_spec=executor_spec, settings=settings)
    except looprun.Busy as exc:
        raise ApiError(409, str(exc)) from None
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None
    state = looprun.read_run(looprun.run_dir(project, run_id))
    invalid = state.get("status") == "invalid"
    return {"run": run_id, "status": "invalid" if invalid else "running", "reason": (state.get("reason") or "") if invalid else ""}


def cancel(project, run_id) -> dict:
    """Stop a run: a live controller is asked (before the next attempt, or now if an agent turn is running; the reply is `{"run"}`, as in release 1); with no
    controller (interrupted, paused, waiting, or queued whose controller never started) the run is marked cancelled here (`{"run", "result": "cancelled"}`); so is a run that
    a controller leaves `paused` while the request is being written (`looprun.cancel` waits up to `CANCEL_WAIT` seconds for it, except for a plainly running run). 409 for a run that is over."""
    folder = _run_folder(project, run_id)
    _state(folder)
    try:
        result = looprun.cancel(folder)
    except looprun.Busy as exc:
        raise ApiError(409, str(exc)) from None
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None
    return {"run": run_id} if result == "requested" else {"run": run_id, "result": result}  # a live controller is asked: the reply is the one of release 1


def pause(project, run_id) -> dict:
    """Ask a running run to stop after the step it is on (that step is finished). 409 unless a controller is running it."""
    folder = _run_folder(project, run_id)
    _state(folder)
    try:
        looprun.pause(folder)
    except looprun.Busy as exc:
        raise ApiError(409, str(exc)) from None
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None
    return {"run": run_id}


def resume(project, run_id, choice=None) -> dict:
    """Start a controller on an interrupted, paused or waiting run, from its cursor and pinned definition. `choice` is `redo` or `accept`
    for a paused step whose fate is unknown. A queued run older than 30 s with no controller (shown `interrupted`) is started. 409 for a run that is over, queued
    and young, or with a live controller; 400 for a choice that does not fit or a definition that no longer matches its pinned hashes."""
    folder = _run_folder(project, run_id)
    _state(folder)
    if choice is not None and choice not in looprun.CHOICES:
        raise ApiError(400, f"the choice must be one of {', '.join(looprun.CHOICES)}")
    try:
        looprun.resume(folder, choice)
    except looprun.Busy as exc:
        raise ApiError(409, str(exc)) from None
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None
    return {"run": run_id, "status": "running"}


def answer_run(project, run_id, word, note="") -> dict:
    """A person's answer (`approve`, `reject`, `stop`) to a run that waits for one: the controller takes it (a live one at once, or the next
    `resume`). 409 when the run is not waiting or already has an answer; 400 for a word or note that is not usable."""
    folder = _run_folder(project, run_id)
    _state(folder)
    try:
        looprun.answer(folder, word, note)
    except looprun.Busy as exc:
        raise ApiError(409, str(exc)) from None
    except looprun.LoopError as exc:
        raise ApiError(400, str(exc)) from None
    return {"run": run_id}


def post(request, structure_dir, project, *, scratch=None, executor_spec=None) -> dict:
    """Run one `POST /api/loop` body (a dict whose keys and types were checked by `shape_ok`): the reply's fields, `ok` first."""
    op = request["op"]
    if op == "save":
        answer = save(structure_dir, request["flow"], request["settings"], request["rev"])
    elif op == "remove":
        answer = remove(structure_dir, request["flow"], request["rev"])
    elif op == "start":
        answer = start(structure_dir, project, request["flow"], request.get("goal"), request.get("workdir"), scratch=scratch,
                       executor_spec=executor_spec, settings=request.get("settings"))
    elif op == "restore":
        answer = restore(structure_dir, request["flow"], request["version"], request["rev"])
    elif op == "pause":
        answer = pause(project, request["run"])
    elif op == "resume":
        answer = resume(project, request["run"], request.get("choice"))
    elif op == "answer":
        answer = answer_run(project, request["run"], request["answer"], request.get("note", ""))
    else:
        answer = cancel(project, request["run"])
    return {"ok": True, **answer}


def shape_ok(request) -> bool:
    """The body is an object with a known `op`, exactly its keys, and the right types (text for ids, an object for settings)."""
    if not isinstance(request, dict) or not isinstance(request.get("op"), str) or request["op"] not in OPS:
        return False
    required, allowed = OPS[request["op"]]
    if not required <= set(request) <= allowed:
        return False
    return all(isinstance(value, dict if key == "settings" else str) for key, value in request.items())


REQUEST_LIMIT = 65536  # bytes of the one request line the Desktop bridge may send (the same limit as its own check)
NEEDS_PROJECT = {"start", "cancel", "pause", "resume", "answer"}


def _request_line(stream) -> dict:
    """The one JSON request the command was given on stdin, or an `ApiError`."""
    raw = stream.read(REQUEST_LIMIT + 1)
    if len(raw) > REQUEST_LIMIT:
        raise ApiError(400, "the loop request is too large")
    try:
        lines = [line for line in raw.decode("utf-8").split("\n") if line.strip()]
        if len(lines) != 1:
            raise ValueError
        request = json.loads(lines[0])
    except ValueError:
        raise ApiError(400, "expected one JSON request on one line: " + SHAPES) from None
    return request


def answer_request(request, structure_dir, project, *, scratch=None, executor_spec=None) -> dict:
    """What the Desktop bridge asks for, answered as the page server answers it: `{"op": "read"}` the listing, `{"op": "read", "run": id}` one run, and any
    write of `POST /api/loop` (the same `shape_ok`, the same functions, the same `ApiError`s). `project` None (no session folder): the listing has no runs and
    what needs a run folder (read one run, start, cancel, pause, resume, answer) is refused in words."""
    if not isinstance(request, dict):
        raise ApiError(400, "expected one JSON object: " + SHAPES)
    if request.get("op") == "read":
        if not set(request) <= {"op", "run"} or not isinstance(request.get("run", ""), str):
            raise ApiError(400, 'expected {"op": "read"} or {"op": "read", "run": id}')
        if "run" not in request:
            return listing(structure_dir, project, scratch)
        if project is None:
            raise ApiError(404, f"no run {request['run'][:40]}: no project folder is open")
        return run_detail(project, request["run"])
    if not shape_ok(request):
        raise ApiError(400, "expected " + SHAPES)
    if project is None and request["op"] in NEEDS_PROJECT:
        raise ApiError(400, "no project folder is open, so there is nowhere to start or change a run")
    return post(request, structure_dir, project, scratch=scratch, executor_spec=executor_spec)


def main(argv=None, stdin=None, stdout=None) -> int:
    """`python3 -P -m agentgraph.loopapi [--project DIR] [--structure DIR]`, the Desktop bridge's command: one JSON request on stdin, one line of JSON on
    stdout (the page's answer; or `{"error", "status", "rev"?}` with exit 1), never a traceback. `--project` is the folder whose runs are listed and where
    runs are made (none: no runs); the structure folder is the page server's default (`$AGENTGRAPH_PERSONAL`, else the checkout's) unless `--structure`."""
    out = stdout or sys.stdout
    try:
        parser = argparse.ArgumentParser(prog="agentgraph.loopapi", add_help=False, exit_on_error=False)
        parser.add_argument("--project", type=Path)
        parser.add_argument("--structure", type=Path)
        args = parser.parse_args(argv)
        structure = args.structure if args.structure is not None else Path(STRUCTURE_DIR)
        doc = answer_request(_request_line(stdin or sys.stdin.buffer), structure, args.project.absolute() if args.project is not None else None)
        text, code = json.dumps(doc, allow_nan=False), 0
    except ApiError as exc:
        text, code = json.dumps({**exc.body(), "status": exc.status}), 1
    except (argparse.ArgumentError, SystemExit):
        text, code = json.dumps({"error": "usage: python3 -m agentgraph.loopapi [--project DIR] [--structure DIR]", "status": 400}), 1
    except Exception as exc:  # the bridge reads one document: whatever went wrong is said in a line, not as a traceback
        text, code = json.dumps({"error": " ".join(f"the loops command failed: {type(exc).__name__}: {exc}".split())[:300], "status": 500}), 1
    out.write(text + "\n")
    out.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
