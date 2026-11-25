"""The test runner for CI: every test module in its own process, a time limit for each, one line per module.

    python tests/ci_run.py [--jobs N] [--limit SECONDS] [--only TEXT] [--logs FOLDER] [--budget SECONDS]

It finds the modules `python -m unittest discover -s tests -t .` would (`tests/test*.py`) and runs each as
`python -m unittest -v tests.<name>` in a child of its own. A module that runs past `--limit` (default 300) has the
stack of every one of its threads dumped (by the child itself, a few seconds before the limit), then its whole
process tree is stopped and the runner goes on with the next module: a hang costs one module, and the log says
which test it was in. A line per module is printed, and flushed, the moment the module ends, so even a job that is
cancelled shows everything up to that point; the end gives totals, the slowest modules, the failing tests and the
tail of each bad module's output. Each module's full output is in `--logs` (default `ci-logs`). The exit status is 0
only when every module passed. Standard library only, no shell: it runs the same on Windows and elsewhere.

    --budget SECONDS   stop starting new modules after this long (they are listed as NOT RUN); 0 (default) is no budget
    --tests FOLDER     the test folder (default: the `tests` next to this file); it must be a package
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agentgraph import osproc  # noqa: E402  (after the path is set)

DEFAULT_LIMIT = 300.0
TAIL_LINES = 60

# What the child runs: the stack dump is armed inside it, so it is the child's own threads that are shown.
CHILD = (
    "import faulthandler, sys, unittest\n"
    "faulthandler.dump_traceback_later(float(sys.argv[1]), exit=False)\n"
    "unittest.main(module=None, argv=['unittest', '-v'] + sys.argv[2:])\n"
)

_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
_FAILED = re.compile(r"^FAILED \((.*)\)\s*$", re.M)
_OK = re.compile(r"^OK(?: \((.*)\))?\s*$", re.M)
_NAMES = re.compile(r"^(?:ERROR|FAIL): (.+)$", re.M)
_STARTED = re.compile(r"^(\w+) \(([\w.]+)\)")
_DONE = re.compile(r"\.\.\. (?:ok|FAIL|ERROR|skipped\b.*|expected failure|unexpected success)\s*$", re.M)
_COUNT = re.compile(r"(\w[\w ]*?)=(\d+)")


class Result:
    def __init__(self, name, status, seconds, run=0, failures=0, errors=0, skips=0, names=(), tail="", last=None, log=None):
        self.name, self.status, self.seconds = name, status, seconds
        self.run, self.failures, self.errors, self.skips = run, failures, errors, skips
        self.names, self.tail, self.last, self.log = list(names), tail, last, log

    @property
    def good(self):
        return self.status == "ok"


def find_modules(tests: Path) -> list[str]:
    """`tests.test_x` for every `test*.py` directly in the folder, in name order (what discover finds there)."""
    return [f"{tests.name}.{path.stem}" for path in sorted(tests.glob("test*.py"))]


def last_started(text: str) -> str | None:
    """The last test whose line was printed before the output ended or the stack dump began (a hung test is the one
    printed with no result after it)."""
    cut = text.find("Timeout (")
    lines = (text[:cut] if cut >= 0 else text).splitlines()
    for line in reversed(lines):
        found = _STARTED.match(line)
        if found:
            return f"{found.group(1)} ({found.group(2)})"
    return None


def parse(text: str) -> dict:
    """Counts and failing names from a module's verbose unittest output; what cannot be found is 0."""
    out = {"run": 0, "failures": 0, "errors": 0, "skips": 0, "names": _NAMES.findall(text), "summary": False}
    ran = _RAN.search(text)
    if ran:
        out["run"] = int(ran.group(1))
        out["summary"] = True
    else:
        out["run"] = len(_DONE.findall(text))  # killed or crashed: how far it got
    for found in list(_FAILED.finditer(text)) + list(_OK.finditer(text)):
        for key, number in _COUNT.findall(found.group(1) or ""):
            key = key.strip()
            if key == "failures":
                out["failures"] = int(number)
            elif key == "errors":
                out["errors"] = int(number)
            elif key == "skipped":
                out["skips"] = int(number)
    return out


def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.splitlines()[-lines:])


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_module(module: str, *, limit: float, logs: Path, cwd: Path) -> Result:
    """Run one module in a child. Its output goes straight to a file (no pipe a grandchild could hold open)."""
    log = logs / f"{module}.log"
    dump_after = limit - 5 if limit > 10 else limit / 2
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", "PYTHONFAULTHANDLER": "1"}
    argv = [sys.executable, "-u", "-X", "faulthandler", "-c", CHILD, repr(dump_after), module]
    started = time.monotonic()
    with open(log, "wb") as stream:
        try:
            child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stream,
                                     stderr=subprocess.STDOUT, **osproc.group_kwargs())
        except OSError as exc:
            return Result(module, "FAIL", 0.0, tail=f"could not start the child: {exc}", log=log)
        timed_out = False
        try:
            child.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            osproc.stop_tree(child.pid, grace=3.0, alive=lambda _pid: child.poll() is None)
            try:  # stragglers that outlived the leader (or ignored the polite stop) go with the group
                osproc.kill_group(child.pid, True)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    seconds = time.monotonic() - started
    text = read(log)
    found = parse(text)
    if timed_out:
        status = "TIMEOUT"
    elif "unittest.loader._FailedTest" in text or "Failed to import test module" in text:
        status = "IMPORT ERROR"
    elif child.returncode == 0 and found["summary"] and not found["failures"] and not found["errors"]:
        status = "ok"
    else:
        status = "FAIL"
    return Result(module, status, seconds, found["run"], found["failures"], found["errors"], found["skips"],
                  found["names"], "" if status == "ok" else tail(text), last_started(text) if status != "ok" else None, log)


def line(result: Result) -> str:
    return (f"{result.status:<12} {result.name:<42} {result.seconds:7.1f}s  run={result.run} "
            f"fail={result.failures} err={result.errors} skip={result.skips}")


def totals(results: list[Result], not_run: list[str], seconds: float) -> dict:
    return {
        "modules": len(results), "run": sum(r.run for r in results), "failures": sum(r.failures for r in results),
        "errors": sum(r.errors for r in results), "skips": sum(r.skips for r in results),
        "timeouts": sum(1 for r in results if r.status == "TIMEOUT"),
        "import_errors": sum(1 for r in results if r.status == "IMPORT ERROR"),
        "bad": sum(1 for r in results if not r.good), "not_run": len(not_run), "seconds": seconds,
    }


def totals_line(t: dict) -> str:
    return (f"TOTALS: {t['modules']} modules, Ran {t['run']} tests, failures={t['failures']}, errors={t['errors']}, "
            f"skipped={t['skips']}, timeouts={t['timeouts']}, import errors={t['import_errors']}, "
            f"not run={t['not_run']}, {t['seconds']:.0f}s")


def report(results: list[Result], not_run: list[str], seconds: float, write=print) -> dict:
    t = totals(results, not_run, seconds)
    write("")
    write("=" * 78)
    write(totals_line(t))
    write("")
    write("Slowest modules:")
    for result in sorted(results, key=lambda r: -r.seconds)[:10]:
        write(f"  {result.seconds:7.1f}s  {result.name}  ({result.status})")
    bad = [r for r in results if not r.good]
    if bad:
        write("")
        write("Failing or erroring tests:")
        for result in bad:
            if result.status == "TIMEOUT":
                write(f"  TIMEOUT {result.name}: last test started: {result.last or 'none printed'}")
            elif not result.names:
                write(f"  {result.status} {result.name}")
            for name in result.names:
                write(f"  {name}")
    if not_run:
        write("")
        write(f"NOT RUN (budget used up): {', '.join(not_run)}")
    for result in bad:
        write("")
        write("-" * 78)
        extra = f"; last test started: {result.last}" if result.last else ""
        write(f"{result.status} {result.name}: last {TAIL_LINES} lines of {result.log}{extra}")
        write(result.tail)
    write("")
    write(totals_line(t))
    return t


def step_summary(results: list[Result], not_run: list[str], seconds: float, label: str) -> str:
    t = totals(results, not_run, seconds)
    out = [f"### Python tests {label}: {'passed' if not t['bad'] and not not_run else 'FAILED'}", "", f"`{totals_line(t)}`", ""]
    bad = [r for r in results if not r.good]
    for result in bad:
        out.append(f"- **{result.status}** `{result.name}`" + (f", last test started: `{result.last}`" if result.last else ""))
        out.extend(f"  - `{name}`" for name in result.names[:20])
    if not_run:
        out.append(f"- not run: {len(not_run)} modules")
    return "\n".join(out) + "\n"


def run_all(modules: list[str], *, jobs: int, limit: float, logs: Path, cwd: Path, budget: float = 0.0,
            emit=None) -> tuple[list[Result], list[str], float]:
    emit = emit or (lambda text: print(text, flush=True))
    logs.mkdir(parents=True, exist_ok=True)
    begun = time.monotonic()
    lock = threading.Lock()
    results: list[Result] = []
    not_run: list[str] = []

    def work(module):
        if budget and time.monotonic() - begun > budget:
            with lock:
                not_run.append(module)
            return
        result = run_module(module, limit=limit, logs=logs, cwd=cwd)
        with lock:
            results.append(result)
            emit(line(result))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        list(pool.map(work, modules))
    return results, sorted(not_run), time.monotonic() - begun


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # a console code page must not crash a report line
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Run each test module in its own process with a time limit.")
    parser.add_argument("--jobs", type=int, default=1, help="modules run at once (default 1)")
    parser.add_argument("--limit", type=float, default=DEFAULT_LIMIT, help="seconds a module may take (default 300)")
    parser.add_argument("--only", default="", help="run only the modules whose name contains this text")
    parser.add_argument("--logs", default="ci-logs", help="folder for each module's full output (default ci-logs)")
    parser.add_argument("--budget", type=float, default=0.0, help="seconds after which no new module is started (0: none)")
    parser.add_argument("--tests", default=str(HERE), help="the test package folder (default: this file's)")
    args = parser.parse_args(argv)

    tests = Path(args.tests).resolve()
    modules = [m for m in find_modules(tests) if args.only in m]
    if not modules:
        print(f"no test module in {tests} matches {args.only!r}", flush=True)
        return 2
    label = f"on {sys.platform}, Python {sys.version_info.major}.{sys.version_info.minor}, jobs {args.jobs}"
    print(f"Running {len(modules)} modules {label}, limit {args.limit:.0f}s each, logs in {args.logs}", flush=True)
    results, not_run, seconds = run_all(modules, jobs=args.jobs, limit=args.limit, logs=Path(args.logs), cwd=tests.parent,
                                        budget=args.budget)
    totals_ = report(results, not_run, seconds, write=lambda text: print(text, flush=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write(step_summary(results, not_run, seconds, label))
        except OSError:
            pass
    return 0 if not totals_["bad"] and not not_run else 1


if __name__ == "__main__":
    sys.exit(main())
