#!/usr/bin/env python3
"""l-suite: four small tasks, three ways to get each one done (direct, loop-1, loop-2), same model, same checks.

    run.py --method direct|loop-1|loop-2 --task t1..t4 --rep R      run one cell, append one row to the results file
    run.py --report                                                 print the tables from the rows
    run.py --verify-frozen                                          check FROZEN.sha256 against the files
    run.py --freeze                                                 write FROZEN.sha256 (only before the first paid run)
    run.py --spend                                                  money spent so far, from the rows

Everything runs from an EXPORTED copy of committed code (default <temp root>/v9-code, env L_SUITE_CODE), never from the
working tree. Agents run on a scratch OpenCode config (default <temp root>/v9-scratch, env L_SUITE_SCRATCH; made by
`agentgraph bench scratch-config`, every agent pinned to one model). Work folders, loop records and the loop structure
live under <temp root>/v9-work (env L_SUITE_WORK), never in the live config.

Methods
  direct   one headless `opencode run` of impl-openai with the goal and the visible check command (the goal names it; the
           prompt adds "run it before you finish and fix what it reports"). 600 s timeout.
  loop-1   `agentgraph loop` on impl-openai -> impl-openai, check = the visible check, 3 attempts.
  loop-2   `agentgraph loop` on impl-openai -> verifier -> impl-openai, same check, 3 attempts, other settings default.
  Loop limits: --attempts 3 --attempt-seconds 300 --cost 1.0, `--allow-asks` (so the loop's agent runs with `--auto`, as the direct
  run does; the work folders are throwaway).

After a method has finished, THIS script runs the visible check in the work folder and the hidden check on a copy of it
(`<suite>/<task>/hidden/check.py`, cwd = the copy). The hidden check is never inside a work folder while an agent runs and its
location is not mentioned in any goal. The rows say if an agent's transcript mentions the suite folder or the hidden check
(`peeked`).

A row (one JSON line in bench/results/l-suite.jsonl): method, task, rep, visible, hidden, status/reason/attempts/retries (loops;
direct: status = returned|timeout|error), sessions (sessions in the tree: every root session plus subagent sessions), calls
(assistant messages that carry token usage in `session_message`, summed over those sessions: one per model step), tokens
{input, output, reasoning, cache_read, cache_write}, cost (sum of those messages' cost from the database; null when the sessions
could not be read), loop_cost (`totals.cost` of run.json), seconds (wall clock of the method, checks excluded), run (loop run id),
roots (root session ids), errors, peeked, changed (files the agent changed), time.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path

SUITE = Path(__file__).resolve().parent
REPO = SUITE.parents[2]
TEMP = Path("/private/var/folders/pm/r8xm1mr525v913lct5tdg_5w0000gn/T/opencode")
CODE = Path(os.environ.get("L_SUITE_CODE", TEMP / "v9-code"))
SCRATCH = Path(os.environ.get("L_SUITE_SCRATCH", TEMP / "v9-scratch"))
WORK = Path(os.environ.get("L_SUITE_WORK", TEMP / "v9-work"))
RESULTS = REPO / "bench" / "results" / "l-suite.jsonl"
DISCARDED = REPO / "bench" / "results" / "l-suite-discarded.jsonl"
FROZEN_FILES = {1: SUITE / "FROZEN.sha256", 2: SUITE / "FROZEN2.sha256"}
FROZEN = FROZEN_FILES[1]
ROUND_TASKS = {1: ["t1", "t2", "t3", "t4"], 2: ["t5", "t6"]}
TASKS = ROUND_TASKS[1] + ROUND_TASKS[2]
ROUND_OF = {t: r for r, ts in ROUND_TASKS.items() for t in ts}
METHODS = ["direct", "loop-1", "loop-2"]
FLOWS = {"loop-1": "impl-openai -> impl-openai", "loop-2": "impl-openai -> verifier -> impl-openai"}
AGENT = "impl-openai"
DIRECT_TIMEOUT = 600
LOOP_LIMITS = {1: {"attempts": 3, "attempt_seconds": 300, "cost": 1.0}, 2: {"attempts": 3, "attempt_seconds": 420, "cost": 1.0}}
UNKNOWN_COST = 0.30
STOP_AT = {1: 5.40, 2: 2.60}
NOTE = ("Before you finish, run the visible check (`{cmd}`) in the project folder and fix whatever it reports. "
        "Stop when it passes.")


# ---- the suite ----

def task_info(task: str) -> dict:
    if task not in TASKS:
        raise SystemExit(f"unknown task {task!r}")
    base = SUITE / task
    info = json.loads((base / "task.json").read_text(encoding="utf-8"))
    info.update(id=task, dir=base, goal=(base / "goal.md").read_text(encoding="utf-8"))
    return info


def frozen_files(rnd: int = 1) -> list[Path]:
    files = []
    for task in ROUND_TASKS[rnd]:
        base = SUITE / task
        for part in ("fixture", "hidden", "reference", "naive"):
            files += [p for p in sorted((base / part).rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
        files += [base / "goal.md", base / "task.json"]
    return files


def freeze_lines(rnd: int = 1) -> list[str]:
    return [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(SUITE)}"
            for p in sorted(frozen_files(rnd), key=lambda p: str(p.relative_to(SUITE)))]


def verify_frozen(rounds=(1, 2)) -> list[str]:
    """The problems (empty = every hash still matches). Round 2's file is only required once it exists or a round-2 task runs."""
    out = []
    for rnd in rounds:
        path = FROZEN_FILES[rnd]
        if not path.is_file():
            out.append(f"{path.name} does not exist")
            continue
        want = path.read_text(encoding="utf-8").splitlines()
        have = freeze_lines(rnd)
        out += [f"{path.name} changed/added/removed: {line}" for line in sorted(set(want) ^ set(have))]
    return out


# ---- the host ----

def host():
    sys.path.insert(0, str(CODE))
    from agentgraph import bench, sources  # noqa: PLC0415
    return bench, sources


def prepare_work(info: dict, folder: Path, bench) -> Path:
    """A fresh git folder with a copy of the fixture (like bench.prepare_work). The hidden check and reference are not copied."""
    if folder.exists():
        raise SystemExit(f"{folder} already exists: this cell was started before")
    work = folder / "work"
    work.mkdir(parents=True)
    shutil.copytree(info["dir"] / "fixture", work, dirs_exist_ok=True)
    bench._git(work, "init", "-q")
    bench._git(work, "add", "-A")
    bench._git(work, "commit", "-q", "-m", "fixture")
    return work


def ensure_loops(rnd: int = 1) -> Path:
    """The loop structure (two agents, two flows) and the saved loops. Idempotent."""
    structure = WORK / ("structure" if rnd == 1 else f"structure{rnd}")
    limits = LOOP_LIMITS[rnd]
    for part in ("30-build", "40-verify", "flows"):
        (structure / part).mkdir(parents=True, exist_ok=True)
    texts = {structure / "30-build" / "impl-openai.md": "The worker.\n", structure / "40-verify" / "verifier.md": "The judge.\n"}
    for flow, path in FLOWS.items():
        texts[structure / "flows" / f"{flow}.md"] = f"path: {path}\nname: {flow}\n\nl-suite {flow}.\n"
    for file, text in texts.items():
        if not file.is_file() or file.read_text(encoding="utf-8") != text:
            file.write_text(text, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(CODE)}
    for flow in FLOWS:
        if not (structure / ".loops" / f"{flow}.json").is_file():
            done = subprocess.run([str(CODE / "bin" / "agentgraph"), "loop", "set", flow, "--check", "python3 check.py", "--attempts",
                                   str(limits["attempts"]), "--attempt-seconds", str(limits["attempt_seconds"]), "--cost",
                                   str(limits["cost"]), "--allow-asks", "--structure", str(structure)],
                                  cwd=WORK, env=env, capture_output=True, text=True, timeout=60)
            if done.returncode != 0:
                raise SystemExit(f"loop set {flow} failed: {done.stderr or done.stdout}")
    return structure


def fake_executable(folder: Path) -> Path:
    """A stand-in for the opencode CLI for the dry run: the worker copies the reference solution, the judge passes."""
    path = folder / "fake-opencode"
    path.write_text(f'''#!{sys.executable}
import json, os, shutil, sys
args = sys.argv[1:]
agent = args[args.index("--agent") + 1]
session = args[args.index("--session") + 1] if "--session" in args else "ses_fake"
sys.stdin.read()
if agent == "verifier":
    text = "looks fine\\nVERDICT: PASS"
else:
    shutil.copytree(os.environ["L_SUITE_FAKE_REF"], os.getcwd(), dirs_exist_ok=True)
    text = "done"
print(json.dumps({{"type": "text", "sessionID": session, "part": {{"text": text}}}}))
''', encoding="utf-8")
    path.chmod(0o755)
    return path


# ---- the database ----

def descendants(db, root: str) -> list[str]:
    ids, queue = [], [root]
    while queue:
        current = queue.pop(0)
        if current in ids:
            continue
        ids.append(current)
        queue += [r[0] for r in db.execute("SELECT id FROM session_v2 WHERE parent_id=?", (current,))]
    return ids


def unknown_measure() -> dict:
    return {"sessions": None, "calls": None, "tokens": None, "cost": None, "peeked": None}


def measure(roots: list[str], bench, db_path: Path) -> dict:
    """Sessions, calls, tokens, cost summed over the roots' trees (bench.read_tree); peeked: messages that mention the suite."""
    out = {"sessions": 0, "calls": 0, "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0},
           "cost": 0.0, "peeked": 0}
    if not roots:
        return unknown_measure()
    for root in roots:
        tree = None
        for _ in range(6):
            try:
                tree = bench.read_tree(db_path, root)
            except sqlite3.Error:
                tree = None
            if tree and tree["sessions"]:
                break
            time.sleep(0.5)
        if not tree or not tree["sessions"]:
            return unknown_measure()
        out["sessions"] += tree["sessions"]
        out["calls"] += tree["turns"]
        out["cost"] += tree["cost"]
        for key in out["tokens"]:
            out["tokens"][key] += tree["tokens"].get(key, 0)
    try:
        with closing(bench.costmod._connect(db_path)) as db:
            ids = [i for root in roots for i in descendants(db, root)]
            for sid in ids:
                out["peeked"] += db.execute("SELECT count(*) FROM session_message WHERE session_id=? AND (data LIKE '%l-suite%' "
                                            "OR data LIKE '%hidden/check%' OR data LIKE '%FROZEN.sha256%')", (sid,)).fetchone()[0]
    except sqlite3.Error:
        out["peeked"] = None
    out["cost"] = round(out["cost"], 6)
    return out


# ---- the checks (run here, not by the agent) ----

def run_visible(info: dict, work: Path, env: dict) -> dict:
    try:
        done = subprocess.run(info["check"], shell=True, cwd=work, capture_output=True, text=True, errors="replace", timeout=120, env=env)
    except subprocess.TimeoutExpired:
        return {"pass": False, "detail": "timed out"}
    lines = [ln.strip() for ln in (done.stdout + "\n" + done.stderr).splitlines() if ln.strip()]
    return {"pass": done.returncode == 0, "detail": (lines[-1] if lines else f"exit {done.returncode}")[:160]}


def run_hidden(info: dict, work: Path, env: dict) -> dict:
    """The hidden check runs from the suite folder on a copy of the work folder, after the method has finished."""
    with tempfile.TemporaryDirectory(prefix="lsuite-hidden-") as tmp:
        copy = Path(tmp) / "work"
        shutil.copytree(work, copy, ignore=shutil.ignore_patterns(".git", "__pycache__", ".opencode"))
        try:
            done = subprocess.run([sys.executable, str(info["dir"] / "hidden" / info["hidden"])], cwd=copy, capture_output=True, text=True,
                                  errors="replace", timeout=120, env={**env, "PYTHONDONTWRITEBYTECODE": "1"})
        except subprocess.TimeoutExpired:
            return {"pass": False, "detail": "timed out"}
    lines = [ln.strip() for ln in (done.stdout + "\n" + done.stderr).splitlines() if ln.strip()]
    return {"pass": done.returncode == 0, "detail": (lines[-1] if lines else f"exit {done.returncode}")[:200]}


def changed_files(work: Path) -> list[str]:
    done = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=work, capture_output=True, text=True, timeout=60)
    return sorted({ln[3:] for ln in done.stdout.splitlines() if "__pycache__" not in ln})


# ---- one cell ----

def rows_of(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def spend(rnd=None) -> tuple[float, int]:
    """(dollars, unknown costs) over every real row ever made, kept or discarded (of one round, or all); an unknown cost counts as $0.30."""
    total, unknown = 0.0, 0
    for path in (RESULTS, DISCARDED):
        for row in rows_of(path):
            if row.get("fake") or (rnd is not None and ROUND_OF.get(row.get("task")) != rnd):
                continue
            if row.get("cost") is None:
                total += UNKNOWN_COST
                unknown += 1
            else:
                total += row["cost"]
    return round(total, 4), unknown


def run_cell(method: str, task: str, rep: int, args) -> dict:
    rnd = ROUND_OF[task]
    problems = verify_frozen((1, 2) if FROZEN_FILES[2].is_file() else (1,)) if rnd == 1 else verify_frozen((1, 2))
    if problems:
        raise SystemExit("the suite is not frozen or changed since: " + "; ".join(problems[:3]))
    results = Path(args.results)
    if not args.again and any((r["method"], r["task"], r["rep"]) == (method, task, rep) for r in rows_of(results)):
        raise SystemExit(f"{method} {task} r{rep} already has a row in {results}")
    total, _ = spend(rnd)
    stop_at = args.stop_at if args.stop_at is not None else STOP_AT[rnd]
    if not args.fake and total >= stop_at:
        raise SystemExit(f"round {rnd} spend {total:.2f} has reached the stop line {stop_at:.2f}: not starting {method} {task} r{rep}")
    bench, sources = host()
    scratch = bench.open_scratch(SCRATCH)
    info = task_info(task)
    folder = WORK / ("dry" if args.fake else "runs") / f"{method}-{task}-r{rep}"
    if args.fake and folder.exists():
        shutil.rmtree(folder)
    work = prepare_work(info, folder, bench)
    env = bench.run_env(scratch, work)
    db_path = Path(sources.DB_PATH)
    row = {"method": method, "task": task, "rep": rep, "round": rnd, "time": int(time.time()), "model": scratch.get("pin_model"),
           "config_hash": scratch["hash"], "fake": bool(args.fake), "work": str(work)}
    roots: list[str] = []
    errors: list[str] = []
    if method == "direct":
        prompt = info["goal"].rstrip() + "\n\n" + NOTE.format(cmd=info["check"])
        session = None
        if args.fake:
            shutil.copytree(info["dir"] / "reference", work, dirs_exist_ok=True)
            result = {"code": 0, "seconds": 0.0, "stdout": "", "stderr": "", "timed_out": False}
        else:
            title = f"agentgraph-bench: l-suite direct {task} r{rep}"
            cmd = bench.build_command(bench.executable_path(), agent=AGENT, model=scratch.get("pin_model"), title=title, auto=True)
            result = bench._spawn(cmd, prompt, work, env, DIRECT_TIMEOUT)
            events = bench.sidechat._events(result["stdout"])
            session = events["session"] or bench.find_root(db_path, title)
            if events["error"]:
                errors.append(events["error"])
            if result["timed_out"]:
                errors.append("timed out")
            elif result["code"]:
                errors.append(bench._first_line(result["stderr"]) or f"exit {result['code']}")
        roots = [session] if session else []
        status = "timeout" if result["timed_out"] else ("error" if errors else "returned")
        row.update(status=status, reason=None, attempts=None, retries=None, run=None, seconds=round(result["seconds"], 1))
    else:
        structure = ensure_loops(rnd)
        proj = folder / "proj"
        proj.mkdir()
        shutil.copy(info["dir"] / "goal.md", proj / "goal.md")
        cmd = [str(CODE / "bin" / "agentgraph"), "loop", "start", method, "--goal-file", str(proj / "goal.md"), "--workdir", str(work),
               "--wait", "--scratch", str(SCRATCH), "--structure", str(structure), "--project", str(proj)]
        run_env = {**os.environ, "PYTHONPATH": str(CODE)}
        if args.fake:
            cmd += ["--executable", str(fake_executable(folder)), "--db", str(folder / "no.db")]
            run_env["L_SUITE_FAKE_REF"] = str(info["dir"] / "reference")
        started = time.time()
        try:
            done = subprocess.run(cmd, cwd=proj, env=run_env, capture_output=True, text=True, errors="replace", timeout=2400)
            if done.returncode not in (0, 1):
                errors.append(f"loop start exit {done.returncode}: {(done.stderr or done.stdout)[-160:]}")
        except subprocess.TimeoutExpired:
            errors.append("the loop process was stopped after 2400 s")
        wall = round(time.time() - started, 1)
        runs = sorted((proj / ".opencode" / "loops").glob("*/run.json"))
        state = json.loads(runs[-1].read_text(encoding="utf-8")) if runs else {}
        attempts = state.get("attempts") or []
        steps = [s for a in attempts for s in a.get("steps", [])]
        roots = [] if args.fake else sorted({s["session"] for s in steps if s.get("session")})
        totals = state.get("totals") or {}
        row.update(status=state.get("status"), reason=(state.get("reason") or "")[:200], attempts=totals.get("attempts"),
                   retries=sum(1 for s in steps if (s.get("try") or 1) > 1), run=state.get("run"), seconds=wall,
                   loop_seconds=totals.get("seconds"), loop_cost=totals.get("cost"), passed_by=state.get("passed_by"),
                   step_outcomes=[f"{s.get('agent')}:{s.get('outcome')}" for s in steps])
        if not runs:
            errors.append("no run record was made")
    row.update(unknown_measure())
    if roots:
        row.update(measure(roots, bench, db_path))
    if args.fake:
        row["cost"] = 0.0
    visible = run_visible(info, work, env)
    hidden = run_hidden(info, work, env)
    row.update(visible=visible["pass"], visible_detail=visible["detail"], hidden=hidden["pass"], hidden_detail=hidden["detail"],
               changed=changed_files(work), roots=roots, errors=errors)
    with Path(results).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, sort_keys=True) + "\n")
    return row


# ---- the report ----

def num(value, digits=0) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def method_table(rows: list[dict]) -> list[str]:
    out = ["| method | n | visible pass | hidden pass | hidden regressions | sessions | calls | input | output | cache read | cache write | cost $ | minutes | attempts | retries | final status |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for method in METHODS:
        mine = [r for r in rows if r["method"] == method]
        if not mine:
            continue

        def total(key, sub=None):
            vals = [(r[key] if sub is None else (r[key] or {}).get(sub)) for r in mine]
            return None if any(v is None for v in vals) else sum(vals)

        unknown = sum(1 for r in mine if r.get("cost") is None)
        cost = sum(r["cost"] for r in mine if r.get("cost") is not None)
        statuses: dict[str, int] = {}
        for r in mine:
            statuses[r.get("status") or "?"] = statuses.get(r.get("status") or "?", 0) + 1
        loop = method != "direct"
        out.append("| " + " | ".join([
            method, str(len(mine)), f"{sum(1 for r in mine if r['visible'])}/{len(mine)}", f"{sum(1 for r in mine if r['hidden'])}/{len(mine)}",
            f"{sum(1 for r in mine if r['visible'] and not r['hidden'])}/{len(mine)}",
            num(total("sessions")), num(total("calls")), num(total("tokens", "input")), num(total("tokens", "output")),
            num(total("tokens", "cache_read")), num(total("tokens", "cache_write")),
            num(cost, 3) + (f" (+{unknown} unknown)" if unknown else ""), num(sum(r["seconds"] for r in mine) / 60, 1),
            str(sum(r.get("attempts") or 0 for r in mine)) if loop else "n/a",
            str(sum(r.get("retries") or 0 for r in mine)) if loop else "n/a",
            ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))]) + " |")
    return out


def task_table(rows: list[dict]) -> list[str]:
    out = ["| task | method | n | V pass | H pass | sessions | calls | cost $ | seconds | attempts | status |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for task in TASKS:
        for method in METHODS:
            mine = sorted((r for r in rows if r["task"] == task and r["method"] == method), key=lambda r: r["rep"])
            if not mine:
                continue

            def seq(key, fmt=str):
                return "/".join("n/a" if r.get(key) is None else fmt(r[key]) for r in mine)

            out.append("| " + " | ".join([
                task, method, str(len(mine)), f"{sum(1 for r in mine if r['visible'])}/{len(mine)}", f"{sum(1 for r in mine if r['hidden'])}/{len(mine)}",
                seq("sessions"), seq("calls"), seq("cost", lambda v: f"{v:.3f}"), seq("seconds", lambda v: f"{v:.0f}"),
                seq("attempts") if method != "direct" else "n/a", "/".join(str(r.get("status")) for r in mine)]) + " |")
    return out


def report(rows: list[dict]) -> str:
    rows = [r for r in rows if not r.get("fake")]
    if not rows:
        return "no rows"
    for r in rows:
        r.setdefault("round", ROUND_OF.get(r["task"], 1))
    rounds = sorted({r["round"] for r in rows})
    out = [f"{len(rows)} rows; rounds {rounds}; tasks {sorted({r['task'] for r in rows})}; reps {sorted({r['rep'] for r in rows})}"]
    for rnd in rounds:
        mine = [r for r in rows if r["round"] == rnd]
        out += ["", f"## Round {rnd} by method (tasks {ROUND_TASKS[rnd]}; sums over all runs of the method; n = runs)", ""] + method_table(mine)
    if len(rounds) > 1:
        out += ["", f"## Both rounds together (n = runs; the rounds are shown apart above and in the per-task table)", ""] + method_table(rows)
    out += ["", "## Per task and method (one value per repetition, in rep order; V = visible check, H = hidden check; round 1 = t1-t4, round 2 = t5-t6)", ""]
    out += task_table(rows)
    out += ["", "## Rows", "",
            "| round | method | task | rep | V | H | status | attempts | sessions | calls | cost $ | seconds | peeked | run / session |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r["round"], METHODS.index(r["method"]), r["task"], r["rep"])):
        out.append("| " + " | ".join([str(r["round"]), r["method"], r["task"], str(r["rep"]), "pass" if r["visible"] else "FAIL", "pass" if r["hidden"] else "FAIL",
                                      str(r.get("status")), num(r.get("attempts")), num(r.get("sessions")), num(r.get("calls")),
                                      num(r.get("cost"), 3), num(r.get("seconds")), num(r.get("peeked")),
                                      str(r.get("run") or (r.get("roots") or ["-"])[0])]) + " |")
    total, unknown = spend()
    parts = ", ".join(f"round {rnd} ${spend(rnd)[0]:.4f}" for rnd in rounds)
    out += ["", f"spend over every row ever made (kept and discarded; an unknown cost counts ${UNKNOWN_COST:.2f}): ${total:.4f} in all ({parts}), {unknown} unknown"]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--method", choices=METHODS)
    ap.add_argument("--task", choices=TASKS)
    ap.add_argument("--rep", type=int)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--verify-frozen", action="store_true")
    ap.add_argument("--spend", action="store_true")
    ap.add_argument("--fake", action="store_true", help="dry run: a stand-in agent copies reference/ (no model, no spend)")
    ap.add_argument("--again", action="store_true", help="allow a second row for the same cell")
    ap.add_argument("--results", default=str(RESULTS))
    ap.add_argument("--stop-at", type=float, default=None, help="default: 5.40 for round 1, 2.60 for round 2")
    ap.add_argument("--round", type=int, choices=(1, 2), default=None, help="for --freeze and --spend")
    args = ap.parse_args()
    args.round_given = args.round is not None
    args.round = args.round or 1
    if args.freeze:
        path = FROZEN_FILES[args.round]
        if path.exists():
            raise SystemExit(f"{path.name} already exists: a frozen suite is never re-frozen by this script (delete it by hand on purpose)")
        path.write_text("\n".join(freeze_lines(args.round)) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(freeze_lines(args.round))} files)")
        return 0
    if args.verify_frozen:
        problems = verify_frozen()
        print("frozen hashes match" if not problems else "\n".join(problems))
        return 1 if problems else 0
    if args.spend:
        total, unknown = spend(args.round if args.round_given else None)
        print(f"${total:.4f} spent{f' in round {args.round}' if args.round_given else ' in all rounds'}, {unknown} unknown cost(s) counted at ${UNKNOWN_COST:.2f}")
        return 0
    if args.report:
        print(report(rows_of(Path(args.results))))
        return 0
    if not (args.method and args.task and args.rep):
        ap.error("--method, --task and --rep are required (or --report)")
    row = run_cell(args.method, args.task, args.rep, args)
    print(json.dumps({k: row.get(k) for k in ("method", "task", "rep", "visible", "hidden", "status", "attempts", "sessions", "calls",
                                                "cost", "seconds", "peeked", "errors")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
