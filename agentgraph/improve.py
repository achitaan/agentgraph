"""Improvement proposals: a proposed prompt edit as a file the user can see, evaluate, approve, reject and revert.

One JSON file per proposal in `<ledger home>/proposals/<id>.json`, written atomically under the ledger's lock
(`ledger._lock`, the `.lock` file `agentgraph record` takes). A proposal holds:

- `title`, `why` (plain words), `evidence`: `{"cases": [...], "notes": [...], "runs": [...], "quotes": [...]}`,
  references (case ids from `agentgraph cases`, feedback note times, run ids) and a few short quotes, not copied text;
- `changes`: `[{"path", "base_sha256", "new_text"}]`, `path` relative to the config root, `base_sha256` the hash of the
  file the proposal was drafted against (null for a new file);
- `evaluation`: `{"before": [...], "after": [...]}`, one row per run: `{"case", "split", "outcome", "cost", "turns",
  "source", "time"}`. `split` is `cases.split_of(case)` (tune or held-out; any id has one, a bench scenario name too),
  `source` is a `cases` trial id or `bench:LABEL:SCENARIO:rN`;
- `state`: `drafted` -> `evaluated` (a first result was recorded) -> `applied` (approved and written) or `rejected`;
  `applied` -> `reverted`; a reverted proposal can be approved again; a rejected one can be reopened (back to `evaluated`
  with results, else `drafted`). `history`: `[{"time", "state", "who", "note"}]`,
  `who` is `user` (the user's own approval or choice, from a command with their words or from the view) or `command`;
- `applications`: one entry per approval: the backup folder, each file's hash before and after, and the evaluation
  summary at that moment.

Paths a proposal may change stay inside the config root and are prompt-like files only: `AGENTS.md`, and `*.md` under
`oh-my-opencode-slim/`, `skills/` and `agents/`. No `..`, absolute path, symlink on the way, or JSON setting.

Approve refuses when a file no longer has the hash the proposal was drafted against (`--force` overrides), copies each
existing file into `<config root>/backups/<time>-improve-<id>/` first, writes the new text atomically (all files, or
none), and appends the `improved` ledger row. It does not need a good evaluation: the user decides; the summary is
printed and recorded. Revert refuses when a file is no longer what approve wrote (`--force` overrides), puts back the
exact bytes from the backup, or deletes a file the proposal created. Reject changes no file.

Every change has a ledger row (`improvements.jsonl`): approve writes one with the proposal's recorded results as its
metric (runs, passes, mean cost, mean turns, before and after; no metric, and the row says so, when no result was
recorded), revert writes one naming the proposal, who undid it and why, and `note` records a change made by hand
(settings, an install, a revert, anything else) with each file's sha256 and never its contents. `changes` lists them.

Verdict of the evaluation (`summary`): held-out and tune pass rates before and after. Fewer than 2 held-out results
before or after -> "not enough runs". Otherwise a lower pass rate on either side -> "worse"; else a higher held-out
rate (tune not lower) -> "better"; else a higher tune rate with held-out equal -> "better on tune only" (the change
is not confirmed on cases it was not written from); else "no difference". The held-out side is the one that counts for
trust, but a drop anywhere is shown rather than hidden.

Ranking (`summary()["ranking"]`): first by passes (all results, both splits); when the pass rates are equal, by cost
(`cost_comparison`). Cost is in weighted tokens when the results carry them (bench rows with token counts: input 1,
cache read 0.1, cache write 1.25, output and reasoning 5 per token, so it does not depend on the model), else in dollars.
Rule: on the cases both sides have (all runs when they share none), with at least 3 runs a side that carry the cost
(fewer: "too few runs to tell"); "cheaper" or "dearer" when the means differ by more than twice the standard error of
the difference (the spread across runs) and by at least 5%; otherwise "no clear difference".

    python3 -m agentgraph.improve propose --title T --why W --change PATH=NEWFILE [--case ID ...] [--note-id ID ...]
    python3 -m agentgraph.improve result ID --phase before|after --case CASE --outcome pass|fail [--cost D --turns N --source S]
    python3 -m agentgraph.improve import-bench ID --phase before|after --label L [--results DIR]
    python3 -m agentgraph.improve show ID [--json] | list [--json]
    python3 -m agentgraph.improve approve ID --by-user "their words" [--force]
    python3 -m agentgraph.improve reject ID --reason R | reopen ID [--note N] | revert ID [--force] [--note N]
    python3 -m agentgraph.improve note --kind K --title T --why W --by-user WORDS [--files PATH ...] [--metric-before V
        --metric-after V --unit U] [--when TIME]  |  note --from-file F [--by-user WORDS]
    python3 -m agentgraph.improve changes [--json]
    python3 -m agentgraph.improve evidence [--json] [--limit N] | evidence --home SCRATCH --to-cases [--top N]   (see evidence.py)

Every command takes `--config-root DIR` (default: OpenCode's config folder) and `--home DIR` (default: $AGENTGRAPH_HOME,
else the active preset's ledger folder).
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import sys
import statistics
import tempfile
import time
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from . import cases, ledger, outputs, sources

PROPOSALS = "proposals"
STATES = ("drafted", "evaluated", "applied", "rejected", "reverted")
PROMPT_DIRS = ("oh-my-opencode-slim", "skills", "agents")
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")
TEXT_LIMIT = 400_000        # characters of one file's new text
CHANGES_LIMIT = 20
RESULTS_LIMIT = 500         # results kept per phase
SHORT = 1000                # a title, why, note
QUOTE = 200
MIN_HELD_OUT = 2
COST_MIN_RUNS = 3           # runs a side that carry the cost, or "too few runs to tell"
COST_STDERRS = 2.0          # a cost gap must be more than this many standard errors of the difference ...
COST_MIN_GAP = 0.05         # ... and at least this share of the larger mean
COST_RULE = (f"cost rule: weighted tokens when the results carry them, else dollars; at least {COST_MIN_RUNS} runs a side; "
             f"cheaper or dearer when the means differ by more than {COST_STDERRS:g} standard errors of the difference and by "
             f"{COST_MIN_GAP:.0%} or more; otherwise no clear difference")
NOTE_KINDS = ("settings", "install", "revert", "other")


class ImproveError(Exception):
    """A refusal: the message says what and why; nothing was changed."""


class NotFound(ImproveError):
    pass


# --- small helpers -----------------------------------------------------------------------------------


def _sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _clip(value, limit: int = SHORT) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _read_file(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write_atomic(path: Path, data: bytes) -> None:
    """Write `data` to `path` through a temporary file in the same folder; keeps an existing file's mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.stat().st_mode & 0o7777
    except FileNotFoundError:
        mode = 0o644
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".improve-")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _backups_folder(root: Path) -> Path:
    """`<root>/backups`, refused when it is a symlink (a write through it would land outside the root)."""
    folder = Path(os.path.realpath(root)) / "backups"
    if folder.is_symlink():
        raise ImproveError("the backups folder is a symlink; refused")
    return folder


def _inside(base: Path, path: Path) -> bool:
    try:
        return Path(os.path.realpath(path)).is_relative_to(Path(os.path.realpath(base)))
    except (OSError, ValueError):
        return False


def _put_back(written: list, created: list, base: Path) -> list[str]:
    """Undo the writes (newest first) and remove the folders the apply made; the paths that could not be put back."""
    failed = []
    for target, current in reversed(written):
        try:
            if current is None:
                target.unlink(missing_ok=True)
            else:
                _write_atomic(target, current)
        except OSError:
            failed.append(str(target.relative_to(base)))
    for relative in sorted(created, key=len, reverse=True):
        try:
            (base / relative).rmdir()
        except OSError:
            pass
    return failed


def config_root(value=None) -> Path:
    root = Path(value).expanduser() if value is not None else sources.CONFIG_DIR
    if not root.is_dir():
        raise ImproveError(f"the config root {root} is not a folder")
    return root


def default_home(value=None) -> Path:
    if value is not None:
        return Path(value).expanduser()
    return outputs.edit_home()


def safe_path(root: Path, relative) -> Path:
    """The file `relative` names under the config root, or ImproveError. Only prompt-like files: `AGENTS.md`, and
    `*.md` below `oh-my-opencode-slim/`, `skills/` and `agents/`. No `..`, `.`, empty part, backslash, absolute path
    or symlink on the way (the root itself may be a link)."""
    if not isinstance(relative, str) or not relative or "\0" in relative or "\\" in relative:
        raise ImproveError(f"{relative!r} is not a path inside the config root")
    parts = relative.split("/")
    if relative.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise ImproveError(f"{relative!r} must be a plain path below the config root (no .., absolute path or empty part)")
    prompt_like = parts[-1].endswith(".md") and parts[-1] != ".md" and (
        relative == "AGENTS.md" or (len(parts) >= 2 and parts[0] in PROMPT_DIRS))
    if not prompt_like:
        raise ImproveError(f"{relative!r} is not a prompt file: only AGENTS.md and *.md under "
                           f"{', '.join(PROMPT_DIRS)} can be changed this way")
    real = Path(os.path.realpath(root))
    current = real
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ImproveError(f"{relative!r} goes through a symlink ({part}); refused")
    return real / relative


# --- the evaluation ----------------------------------------------------------------------------------


def _pair(rows: list[dict]) -> list[int]:
    return [sum(row["outcome"] == "pass" for row in rows), len(rows)]


def summary(evaluation: dict) -> dict:
    """{"tune": {"before": [passes, runs], "after": [...]}, "held_out": {...}, "verdict", "text"} (rules: module
    docstring)."""
    counts = {}
    for split in ("tune", "held-out"):
        counts["held_out" if split == "held-out" else split] = {
            phase: _pair([row for row in evaluation.get(phase, []) if row["split"] == split])
            for phase in ("before", "after")}

    def delta(side):
        (bp, bt), (ap, at) = counts[side]["before"], counts[side]["after"]
        return Fraction(ap, at) - Fraction(bp, bt) if bt and at else Fraction(0)

    held, tune = counts["held_out"], counts["tune"]
    if held["before"][1] < MIN_HELD_OUT or held["after"][1] < MIN_HELD_OUT:
        verdict = "not enough runs"
    elif delta("held_out") < 0 or delta("tune") < 0:
        verdict = "worse"
    elif delta("held_out") > 0:
        verdict = "better"
    elif delta("tune") > 0:
        verdict = "better on tune only"
    else:
        verdict = "no difference"
    text = (f"held-out {held['before'][0]}/{held['before'][1]} before, {held['after'][0]}/{held['after'][1]} after; "
            f"tune {tune['before'][0]}/{tune['before'][1]} before, {tune['after'][0]}/{tune['after'][1]} after: {verdict}")
    spent = cost_comparison(evaluation)
    return {**counts, "verdict": verdict, "text": text, "cost": spent, "ranking": _ranking(evaluation, spent)}


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mean_sd(values: list[float]) -> tuple[float, float]:
    return statistics.fmean(values), statistics.stdev(values) if len(values) > 1 else 0.0


def cost_comparison(evaluation: dict) -> dict:
    """Before against after on cost (rule: module docstring, `COST_RULE`): `{"unit", "basis", "before": {runs, mean, sd},
    "after": {...}, "change", "verdict", "text"}`; `verdict` is cheaper, dearer, no clear difference or too few runs to tell."""
    before, after = evaluation.get("before", []), evaluation.get("after", [])
    shared = {row["case"] for row in before} & {row["case"] for row in after}
    basis = "the cases both sides have" if shared else "all runs (the sides share no case)"
    if shared:
        before, after = [row for row in before if row["case"] in shared], [row for row in after if row["case"] in shared]
    chosen = None
    for unit, key in (("weighted tokens", "weighted"), ("dollars", "cost")):
        b = [row[key] for row in before if _number(row.get(key))]
        a = [row[key] for row in after if _number(row.get(key))]
        if len(b) >= COST_MIN_RUNS and len(a) >= COST_MIN_RUNS:
            chosen = unit, b, a
            break
    if chosen is None:
        counts = [max(sum(_number(row.get(key)) for row in rows) for key in ("weighted", "cost")) for rows in (before, after)]
        return {"unit": None, "basis": basis, "before": {"runs": counts[0]}, "after": {"runs": counts[1]}, "change": None,
                "verdict": "too few runs to tell",
                "text": f"too few runs to tell ({counts[0]} before, {counts[1]} after carry a cost; {COST_MIN_RUNS} a side are needed)"}
    unit, b, a = chosen
    (mb, sb), (ma, sa) = _mean_sd(b), _mean_sd(a)
    gap = mb - ma
    noise = math.sqrt(sb ** 2 / len(b) + sa ** 2 / len(a))
    large = max(mb, ma)
    if large > 0 and abs(gap) > COST_STDERRS * noise and abs(gap) >= COST_MIN_GAP * large:
        verdict = "cheaper" if gap > 0 else "dearer"
    else:
        verdict = "no clear difference"
    change = (ma - mb) / mb if mb else None
    digits = 3 if unit == "dollars" else 0
    text = (f"{verdict}: mean {ma:,.{digits}f} after against {mb:,.{digits}f} before ({unit}, "
            f"{len(a)} and {len(b)} runs, {basis}" + (f", {change:+.1%}" if change is not None else "") + ")")
    return {"unit": unit, "basis": basis, "before": {"runs": len(b), "mean": mb, "sd": sb},
            "after": {"runs": len(a), "mean": ma, "sd": sa}, "change": change, "verdict": verdict, "text": text}


def _ranking(evaluation: dict, spent: dict) -> dict:
    """Before against after: by passes first, and by cost when the pass rates are equal."""
    (bp, bt), (ap, at) = _pair(evaluation.get("before", [])), _pair(evaluation.get("after", []))
    if not bt or not at:
        return {"by": None, "result": "no results on one side", "text": "no results on one side"}
    if Fraction(ap, at) != Fraction(bp, bt):
        word = "more" if Fraction(ap, at) > Fraction(bp, bt) else "fewer"
        text = f"by passes: {word} after ({ap}/{at} after, {bp}/{bt} before)"
        return {"by": "passes", "result": f"{word} passes", "text": text}
    return {"by": "cost", "result": spent["verdict"],
            "text": f"same passes ({ap}/{at} after, {bp}/{bt} before); by cost: {spent['text']}"}


def _result_row(case: str, outcome: str, cost, turns, source, weighted=None) -> dict:
    if not isinstance(case, str) or not REFERENCE.fullmatch(case):
        raise ImproveError("a case id is letters, digits, '.', '_', '-' or ':' (at most 64), e.g. a `cases` id or a bench scenario")
    if outcome not in ("pass", "fail"):
        raise ImproveError("outcome is pass or fail")
    for name, value in (("cost", cost), ("turns", turns), ("weighted", weighted)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
                                  or value != value or value == float("inf")):
            raise ImproveError(f"{name} must be a number, 0 or more")
    row = {"case": case, "split": cases.split_of(case), "outcome": outcome, "cost": cost, "turns": turns,
           "source": _clip(source, 200) or None}
    if weighted is not None:
        row["weighted"] = weighted
    return row


# --- records -----------------------------------------------------------------------------------------


def _folder(home: Path) -> Path:
    return Path(home) / PROPOSALS


def _path(home: Path, proposal_id) -> Path:
    if not isinstance(proposal_id, str) or not ID.fullmatch(proposal_id):
        raise ImproveError("a proposal id is letters, digits, '.', '_' or '-' (at most 64)")
    return _folder(home) / f"{proposal_id}.json"


def _load(home: Path, proposal_id) -> dict:
    path = _path(home, proposal_id)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NotFound(f"no proposal {proposal_id}") from None
    except (OSError, ValueError) as exc:
        raise ImproveError(f"proposal {proposal_id} cannot be read: {exc}") from None
    if not isinstance(record, dict) or record.get("state") not in STATES or not isinstance(record.get("changes"), list):
        raise ImproveError(f"proposal {proposal_id} is damaged")
    return record


def _save(home: Path, record: dict) -> None:
    path = _path(home, record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, (json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))


def _move(record: dict, state: str, who: str, note: str, now: float | None = None) -> None:
    record["state"] = state
    record["history"].append({"time": ledger._now(now), "state": state, "who": who, "note": _clip(note)})


def _new_id(now: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + "-" + secrets.token_hex(2)


def _references(values, what: str) -> list[str]:
    found = []
    for value in values or []:
        if not isinstance(value, str) or not REFERENCE.fullmatch(value):
            raise ImproveError(f"a {what} is letters, digits, '.', '_', '-' or ':' (at most 64): {value!r}")
        if value not in found:
            found.append(value)
    return found


# --- commands ----------------------------------------------------------------------------------------


def propose(home: Path, root: Path, title: str, why: str, changes: list[tuple[str, str]], *, cases_: list[str] | None = None,
            notes: list[str] | None = None, runs: list[str] | None = None, quotes: list[str] | None = None,
            now: float | None = None) -> dict:
    """Draft a proposal. `changes`: [(path under the config root, new text)]."""
    title, why = _clip(title), _clip(why)
    if not title or not why:
        raise ImproveError("a proposal needs a title and a why, in plain words")
    if not changes or len(changes) > CHANGES_LIMIT:
        raise ImproveError(f"a proposal needs 1 to {CHANGES_LIMIT} changes")
    done, entries = set(), []
    for relative, text in changes:
        target = safe_path(root, relative)
        if relative in done:
            raise ImproveError(f"{relative} appears twice")
        done.add(relative)
        if not isinstance(text, str) or len(text) > TEXT_LIMIT:
            raise ImproveError(f"the new text of {relative} must be text of at most {TEXT_LIMIT} characters")
        current = _read_file(target) if target.is_file() or not target.exists() else None
        if current is None and target.exists():
            raise ImproveError(f"{relative} is not a regular file")
        if current is not None and current == text.encode("utf-8"):
            raise ImproveError(f"{relative}: the new text is the same as the file now; nothing to propose")
        entries.append({"path": relative, "base_sha256": _sha(current), "new_text": text})
    when = ledger._now(now)
    record = {"id": _new_id(when), "created": when, "title": title, "why": why,
              "evidence": {"cases": _references(cases_, "case id"), "notes": _references(notes, "note id"),
                           "runs": _references(runs, "run id"),
                           "quotes": [_clip(q, QUOTE) for q in (quotes or [])[:5] if _clip(q, QUOTE)]},
              "changes": entries, "evaluation": {"before": [], "after": []}, "state": "drafted",
              "history": [], "applications": []}
    _move(record, "drafted", "command", "proposed", when)
    with ledger._lock(Path(home), writing=True):
        _save(home, record)
    return record


def add_results(home: Path, proposal_id: str, phase: str, rows: list[dict], *, now: float | None = None) -> dict:
    """Add result rows (as `_result_row` makes them) to a phase; a row with the same `source` and case already in that phase replaces
    that earlier row, so importing the same bench label twice does not count a run twice."""
    if phase not in ("before", "after"):
        raise ImproveError("phase is before or after")
    with ledger._lock(Path(home), writing=True):
        record = _load(home, proposal_id)
        if record["state"] == "rejected":
            raise ImproveError(f"proposal {proposal_id} was rejected; no results can be added")
        existing = record["evaluation"][phase]
        for row in rows:
            row = {**row, "time": ledger._now(now)}
            existing[:] = [old for old in existing if not (row["source"] and (old.get("source"), old.get("case")) == (row["source"], row["case"]))]
            existing.append(row)
        del existing[:-RESULTS_LIMIT]
        if record["state"] == "drafted":
            _move(record, "evaluated", "command", f"{len(rows)} {phase} result(s)", now)
        _save(home, record)
    return record


def add_result(home: Path, proposal_id: str, phase: str, case: str, outcome: str, *, cost=None, turns=None,
               source=None, now: float | None = None) -> dict:
    return add_results(home, proposal_id, phase, [_result_row(case, outcome, cost, turns, source)], now=now)


def import_bench(home: Path, proposal_id: str, phase: str, label: str, results: Path | None = None, *,
                 now: float | None = None) -> tuple[dict, int]:
    """Bench result rows of `label` as results: the scenario name is the case, the row's check the outcome (a row
    with no check, such as a probe, is skipped). Each result also carries the run's weighted tokens when the row has token
    counts (`bench.weighted_tokens`), next to its dollars."""
    from . import bench
    try:
        rows = bench.read_rows(label, results)
    except bench.BenchError as exc:
        raise ImproveError(str(exc)) from None
    found = []
    for row in rows:
        check = row.get("check")
        if not isinstance(check, dict) or not isinstance(check.get("passed"), bool) or not isinstance(row.get("scenario"), str):
            continue
        number = lambda value: value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        found.append(_result_row(row["scenario"], "pass" if check["passed"] else "fail", number(row.get("cost")),
                                 number(row.get("turns")), f"bench:{label}:{row['scenario']}:r{row.get('rep', '?')}",
                                 weighted=bench.weighted_tokens(row)))
    if not found:
        raise ImproveError(f"bench label {label!r} has no scenario rows with a check")
    return add_results(home, proposal_id, phase, found, now=now), len(found)


def _diff(relative: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{relative}", f"b/{relative}"))


def _current_text(target: Path) -> tuple[str, str | None]:
    data = _read_file(target) if target.is_file() or not target.exists() else None
    return (data.decode("utf-8", "replace") if data is not None else ""), _sha(data)


def _backup_text(root: Path, application: dict, item: dict) -> str | None:
    """The text the file had before the application ("" for a file it created); None when the backup is gone or no
    longer what was saved (the caller then diffs against the file now)."""
    if item.get("before_sha256") is None:
        return ""
    try:
        folder = Path(application["backup"])
        if not _inside(_backups_folder(root), folder):
            return None
        data = _read_file(folder / item["path"])
    except (OSError, ImproveError, KeyError):
        return None
    return data.decode("utf-8", "replace") if data is not None and _sha(data) == item["before_sha256"] else None


def describe(home: Path, root: Path, record: dict, *, full: bool = True) -> dict:
    """The record with its evaluation summary and, per change, the size and diff of the proposal's own change (applied:
    the backup against the new text, `diff_against: "backup"`; else the file now against it, `"file"`) and whether the
    file still has the hash the proposal was drafted against (`base_matches`) or the one it wrote (`after_matches`).
    The line counts of the whole proposal (`added`, `removed`) follow the same diffs."""
    last = record["applications"][-1] if record["applications"] else None
    applied = {item["path"]: item for item in last["files"]} if last and record["state"] == "applied" else {}
    files, added, removed = [], 0, 0
    for change in record["changes"]:
        entry = {"path": change["path"], "base_sha256": change["base_sha256"],
                 "new_sha256": _sha(change["new_text"].encode("utf-8"))}
        try:
            target = safe_path(root, change["path"])
            text, current = _current_text(target)
        except ImproveError as exc:
            text, current, entry["error"] = "", None, str(exc)
        entry.update(current_sha256=current, base_matches=current == change["base_sha256"],
                     after_matches=(current == applied[change["path"]]["after_sha256"]) if change["path"] in applied else None)
        # An applied proposal's own change is the backup (the bytes before it was applied) against its new text: the file
        # now already holds the new text. Otherwise (drafted, evaluated, rejected, reverted) the file now against it.
        base, entry["diff_against"] = text, "file"
        if change["path"] in applied:
            before = _backup_text(root, last, applied[change["path"]])
            if before is not None:
                base, entry["diff_against"] = before, "backup"
        plus, minus = outputs._line_counts(base, change["new_text"])
        entry.update(added=plus, removed=minus, diff=_diff(change["path"], base, change["new_text"]))
        added, removed = added + plus, removed + minus
        files.append(entry)
    result = {key: value for key, value in record.items() if key != "changes"}
    result.update(summary=summary(record["evaluation"]), files=files, added=added, removed=removed)
    if full:
        result["changes"] = record["changes"]
    return result


def show(home: Path, root: Path, proposal_id: str) -> dict:
    with ledger._lock(Path(home), writing=False):
        return describe(home, root, _load(home, proposal_id))


def list_proposals(home: Path, root: Path) -> list[dict]:
    """Every proposal, newest first, without its texts (`id, created, title, state, summary, added, removed, files`)."""
    found = []
    with ledger._lock(Path(home), writing=False):
        for path in sorted(_folder(home).glob("*.json")) if _folder(home).is_dir() else []:
            if not ID.fullmatch(path.stem):
                continue
            try:
                record = _load(home, path.stem)
            except ImproveError:
                continue
            full = describe(home, root, record, full=False)
            found.append({"id": record["id"], "created": record["created"], "title": record["title"],
                          "state": record["state"], "summary": full["summary"], "added": full["added"],
                          "removed": full["removed"], "files": [item["path"] for item in full["files"]]})
    return sorted(found, key=lambda item: (item["created"], item["id"]), reverse=True)


def digest(home: Path, limit: int = 30) -> list[dict]:
    """The proposals as `{id, created, title, state, verdict}`, newest first, read from the records alone (no file is
    compared, so it is cheap): what the snapshot carries for the terminal panel."""
    found = []
    with ledger._lock(Path(home), writing=False):
        for path in sorted(_folder(home).glob("*.json")) if _folder(home).is_dir() else []:
            if not ID.fullmatch(path.stem):
                continue
            try:
                record = _load(home, path.stem)
            except ImproveError:
                continue
            found.append({"id": record["id"], "created": record["created"], "title": str(record["title"])[:200],
                          "state": record["state"], "verdict": summary(record["evaluation"])["verdict"]})
    return sorted(found, key=lambda item: (item["created"], item["id"]), reverse=True)[:limit]


METRIC_UNIT = "runs, passes, mean cost (dollars), mean turns, mean weighted tokens"


def _phase_metric(rows: list[dict]) -> dict | None:
    def mean(key):
        values = [row[key] for row in rows if _number(row.get(key))]
        return round(statistics.fmean(values), 6) if values else None
    return {"runs": len(rows), "passes": sum(row["outcome"] == "pass" for row in rows), "mean_cost": mean("cost"),
            "mean_turns": mean("turns"), "mean_weighted_tokens": mean("weighted")} if rows else None


def _phase_text(phase: str, metric: dict | None) -> str:
    if metric is None:
        return f"{phase}: no runs"
    parts = [f"{metric['runs']} runs", f"{metric['passes']} passed"]
    if metric["mean_cost"] is not None:
        parts.append(f"mean cost ${metric['mean_cost']:.3f}")
    if metric["mean_turns"] is not None:
        parts.append(f"mean turns {metric['mean_turns']:.1f}")
    if metric["mean_weighted_tokens"] is not None:
        parts.append(f"mean weighted tokens {metric['mean_weighted_tokens']:,.0f}")
    return f"{phase}: " + ", ".join(parts)


def evaluation_metric(evaluation: dict) -> tuple[dict | None, dict | None, str | None]:
    """The proposal's recorded results as `(before, after, text)`; all three None when no result was recorded."""
    before, after = _phase_metric(evaluation.get("before", [])), _phase_metric(evaluation.get("after", []))
    if before is None and after is None:
        return None, None, None
    return before, after, "; ".join((_phase_text("before", before), _phase_text("after", after)))


def approve(home: Path, root: Path, proposal_id: str, *, who: str, note: str, force: bool = False,
            now: float | None = None) -> dict:
    """Apply the proposal (see the module docstring). `who` is `user` (their words in `note`) or `command`."""
    if who == "user" and not _clip(note):
        raise ImproveError("approval needs the user's own words (--by-user)")
    with ledger._lock(Path(home), writing=True):
        record = _load(home, proposal_id)
        if record["state"] not in ("drafted", "evaluated", "reverted"):
            raise ImproveError(f"proposal {proposal_id} is {record['state']}; only a drafted, evaluated or reverted one can be approved")
        plan = []
        for change in record["changes"]:
            target = safe_path(root, change["path"])
            current = _read_file(target) if target.is_file() or not target.exists() else None
            if current is None and target.exists():
                raise ImproveError(f"{change['path']} is not a regular file")
            if _sha(current) != change["base_sha256"] and not force:
                raise ImproveError(f"{change['path']} changed since the proposal was drafted (its hash is no longer "
                                   f"the one the change was made against); nothing was written. Propose again, or --force")
            try:
                data = change["new_text"].encode("utf-8")
            except (UnicodeEncodeError, AttributeError):
                raise ImproveError(f"the new text of {change['path']} cannot be written as UTF-8; nothing was written") from None
            plan.append((change, target, current, data))
        when = ledger._now(now)
        folder = _backups_folder(root) / (time.strftime("%Y%m%d-%H%M%S", time.localtime(when)) + f"-improve-{record['id']}")
        suffix = 1
        while folder.exists() or folder.is_symlink():
            suffix += 1
            folder = folder.with_name(f"{folder.name.split('~')[0]}~{suffix}")
        files = []
        for change, target, current, data in plan:
            if current is not None:
                _write_atomic(folder / change["path"], current)
        folder.mkdir(parents=True, exist_ok=True)
        _write_atomic(folder / "manifest.json", (json.dumps({"proposal": record["id"], "files": [
            {"path": change["path"], "existed": current is not None, "sha256": _sha(current)}
            for change, _, current, _ in plan]}, indent=2) + "\n").encode("utf-8"))
        base = Path(os.path.realpath(root))
        written, created = [], []
        try:
            for change, target, current, data in plan:
                if current is None:
                    cursor = target.parent
                    while not cursor.exists():
                        created.append(str(cursor.relative_to(base)))
                        cursor = cursor.parent
                _write_atomic(target, data)
                written.append((target, current))
                files.append({"path": change["path"], "before_sha256": _sha(current), "after_sha256": _sha(data)})
            record["applications"].append({"time": when, "backup": str(folder), "files": files, "created_dirs": created,
                                           "forced": bool(force), "evaluation": summary(record["evaluation"]),
                                           "reverted": None, "ledger_time": None})
            _move(record, "applied", who, note if who == "user" else (note or "approved"), when)
            _save(home, record)
        except BaseException as exc:
            failed = _put_back(written, created, base)
            if failed:
                raise ImproveError(f"could not write {exc}; {', '.join(failed)} could not be put back (the originals are in "
                                   f"{folder})") from exc
            if isinstance(exc, OSError):
                raise ImproveError(f"could not write {exc}; the files written so far were put back") from None
            raise
    # `ledger.improved` takes the same lock itself, so it runs after ours is released.
    row = None
    before, after, metric = evaluation_metric(record["evaluation"])
    verdict = summary(record["evaluation"])["verdict"] if metric else "no results recorded"
    try:
        row = ledger.improved(Path(home), now, [change["path"] for change in record["changes"]],
                              f"{record['title']} (proposal {record['id']}; {verdict})",
                              signals=[f"proposal {record['id']}"] + [f"case {item}" for item in record["evidence"]["cases"][:5]],
                              metric=metric, extra={
                                  "kind": "proposal", "proposal": record["id"], "by": who,
                                  "by_words": _clip(note) if who == "user" else None, "why": _clip(record["why"]),
                                  "metric_before": before, "metric_after": after, "unit": METRIC_UNIT,
                                  "files_sha256": {item["path"]: item["after_sha256"] for item in record["applications"][-1]["files"]},
                                  "source": "improve approve"})
    except (OSError, ValueError) as exc:
        with ledger._lock(Path(home), writing=True):
            record = _load(home, proposal_id)
            _move(record, "applied", "command", f"files written, but the improved row failed: {exc}")
            _save(home, record)
        raise ImproveError(f"the files were written (proposal {proposal_id} is applied) but the ledger row failed: {exc}") from None
    with ledger._lock(Path(home), writing=True):
        record = _load(home, proposal_id)
        record["applications"][-1]["ledger_time"] = row["time"]
        _save(home, record)
    return record


def reject(home: Path, proposal_id: str, reason: str, *, who: str = "user", now: float | None = None) -> dict:
    if not _clip(reason):
        raise ImproveError("rejecting needs a reason")
    with ledger._lock(Path(home), writing=True):
        record = _load(home, proposal_id)
        if record["state"] not in ("drafted", "evaluated", "reverted"):
            raise ImproveError(f"proposal {proposal_id} is {record['state']}; only a drafted, evaluated or reverted one can be rejected")
        _move(record, "rejected", who, reason, now)
        _save(home, record)
    return record


def reopen(home: Path, proposal_id: str, *, who: str = "user", note: str = "", now: float | None = None) -> dict:
    """Undo a rejection: back to `evaluated` when the proposal has results, else `drafted`. No file changes."""
    with ledger._lock(Path(home), writing=True):
        record = _load(home, proposal_id)
        if record["state"] != "rejected":
            raise ImproveError(f"proposal {proposal_id} is {record['state']}; only a rejected one can be reopened")
        evaluation = record["evaluation"]
        _move(record, "evaluated" if evaluation["before"] or evaluation["after"] else "drafted", who, note or "reopened", now)
        _save(home, record)
    return record


def revert(home: Path, root: Path, proposal_id: str, *, who: str = "command", note: str = "", force: bool = False,
           now: float | None = None) -> dict:
    with ledger._lock(Path(home), writing=True):
        record = _load(home, proposal_id)
        if record["state"] != "applied" or not record["applications"]:
            raise ImproveError(f"proposal {proposal_id} is {record['state']}; only an applied one can be reverted")
        application = record["applications"][-1]
        folder = Path(application["backup"])
        base = Path(os.path.realpath(root))
        if not _inside(_backups_folder(root), folder):
            raise ImproveError(f"the backup folder {folder} is not inside the config root's backups; nothing was restored")
        created = []
        for relative in application.get("created_dirs", []):
            parts = relative.split("/") if isinstance(relative, str) else [""]
            if any(part in ("", ".", "..") for part in parts) or relative.startswith("/") or not _inside(base, base / relative) or (base / relative).is_symlink():
                raise ImproveError(f"the record names a created folder ({relative!r}) that is not inside the config root; nothing was restored")
            created.append(relative)
        plan = []
        for item in application["files"]:
            target = safe_path(root, item["path"])
            current = _read_file(target) if target.is_file() or not target.exists() else None
            if _sha(current) == item["before_sha256"]:
                continue  # already put back by an earlier attempt that failed halfway
            if _sha(current) != item["after_sha256"] and not force:
                raise ImproveError(f"{item['path']} was changed after the proposal was applied; nothing was restored. "
                                   f"Look at it, or --force to put the original back anyway")
            original = None
            if item["before_sha256"] is not None:
                original = _read_file(folder / item["path"])
                if _sha(original) != item["before_sha256"]:
                    raise ImproveError(f"the backup of {item['path']} in {folder} is missing or changed; nothing was restored")
            plan.append((target, current, original))
        for target, current, original in plan:
            if original is None:
                target.unlink(missing_ok=True)
            else:
                _write_atomic(target, original)
        for relative in sorted(created, key=len, reverse=True):
            try:
                (base / relative).rmdir()
            except OSError:
                pass
        application["reverted"] = ledger._now(now)
        _move(record, "reverted", who, note or "reverted", now)
        _save(home, record)
    # Like approve: the ledger takes its own lock, so the row is written once ours is released.
    try:
        hashes = {item["path"]: _sha(_read_file(safe_path(root, item["path"]))) for item in application["files"]}
        ledger.changed(Path(home), [{
            "when": ledger._now(now), "kind": "revert", "summary": f"Reverted proposal {record['id']}: {record['title']}",
            "files": [item["path"] for item in application["files"]], "files_sha256": hashes, "by": who,
            "why": _clip(note) or "reverted", "proposal": record["id"], "source": "improve revert",
            "signals": [f"proposal {record['id']}"]}])
    except (OSError, ValueError, ImproveError) as exc:
        raise ImproveError(f"the files were put back (proposal {proposal_id} is reverted) but the ledger row failed: {exc}") from None
    return record


# --- changes made outside `improve` -------------------------------------------------------------------


def _when(value, now: float) -> float:
    """The time of a change: nothing (now), a number of seconds, or a date, `YYYY-MM-DD[ HH:MM[:SS]]` (local time unless
    it carries an offset)."""
    if value is None or value == "":
        return now
    if _number(value):
        return float(value)
    if not isinstance(value, str):
        raise ImproveError(f"--when {value!r} is not a time")
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.strip().replace(" ", "T", 1)).timestamp()
    except ValueError:
        raise ImproveError(f"--when {value!r} is not a time: use YYYY-MM-DD, YYYY-MM-DD HH:MM[:SS] or seconds") from None


def _metric_arg(value):
    """A metric given on the command line or in a file: a number stays a number, text stays text."""
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return _clip(value) or None
        return int(number) if number.is_integer() and "." not in value else number
    return value


def _plain_relative(relative) -> str:
    if (not isinstance(relative, str) or not relative or "\0" in relative or "\\" in relative or relative.startswith("/")
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ImproveError(f"{relative!r} must be a plain path below the config root (no .., absolute path or empty part)")
    return relative


def _file_hash(root: Path, relative: str) -> str | None:
    """The sha256 of the file under the config root (None when it does not exist). The file is only hashed, in chunks;
    its contents go nowhere."""
    target = Path(os.path.realpath(root)) / relative
    real = Path(os.path.realpath(target))
    if not _inside(root, real):
        raise ImproveError(f"{relative} resolves outside the config root; refused")
    if not real.exists():
        return None
    if not real.is_file():
        raise ImproveError(f"{relative} is not a regular file")
    digest = hashlib.sha256()
    with real.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _note_fields(root: Path, spec: dict, by_user, now: float, *, hash_now: bool) -> dict:
    kind = spec.get("kind")
    if kind not in NOTE_KINDS:
        raise ImproveError(f"--kind is one of {', '.join(NOTE_KINDS)}")
    title, why = _clip(spec.get("title")), _clip(spec.get("why"))
    if not title or not why:
        raise ImproveError("a note needs a title and a why, in plain words")
    words = _clip(spec.get("by_user") or by_user)
    if not words:
        raise ImproveError("a note needs the user's own words saying who approved the change (--by-user)")
    files = spec.get("files") or []
    if not isinstance(files, list) or len(files) > CHANGES_LIMIT:
        raise ImproveError(f"files is a list of at most {CHANGES_LIMIT} paths")
    given = spec.get("sha256") or {}
    if not isinstance(given, dict):
        raise ImproveError("sha256 is a map from path to hash")
    hashes = {}
    for relative in files:
        _plain_relative(relative)
        hashes[relative] = _file_hash(root, relative) if hash_now else given.get(relative)
    unit = _clip(spec.get("unit"), 200) or None
    source = _clip(spec.get("source"), 400) or None
    return {"when": _when(spec.get("when"), now), "kind": kind, "summary": title, "files": files, "files_sha256": hashes,
            "why": why, "by": "user", "by_words": words, "metric_before": _metric_arg(spec.get("metric_before")),
            "metric_after": _metric_arg(spec.get("metric_after")), "unit": unit, "source": source}


def note(home: Path, root: Path, kind: str, title: str, why: str, *, by_user: str, files: list[str] | None = None,
         metric_before=None, metric_after=None, unit: str | None = None, when=None, now: float | None = None) -> dict:
    """Record a change made outside `improve` as one ledger row; no file but the ledger changes. Each file in `files` (paths
    below the config root) is recorded with its current sha256, or null if it does not exist; its contents are never
    read into the row."""
    stamp = ledger._now(now)
    spec = {"kind": kind, "title": title, "why": why, "files": files or [], "metric_before": metric_before,
            "metric_after": metric_after, "unit": unit, "when": when}
    row = _note_fields(root, spec, by_user, stamp, hash_now=True)
    return _write_notes(home, [row], stamp)[0]


def note_file(home: Path, root: Path, path: Path, *, by_user: str | None = None, now: float | None = None) -> list[dict]:
    """Apply a file of notes, one JSON object a line: `kind, title, why, files, sha256 {path: hash}, metric_before,
    metric_after, unit, when (required), by_user, source`. The hashes are the ones the file gives (a change made in the past has no
    "current" hash); a row with no `by_user` takes `by_user`. All rows are recorded or none: a bad row, a missing
    approval or a row already in the ledger refuses the whole file."""
    stamp = ledger._now(now)
    try:
        lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ImproveError(f"cannot read {path} as UTF-8 text: {exc}") from None
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            spec = json.loads(line)
            if not isinstance(spec, dict):
                raise ValueError("not an object")
            if spec.get("when") in (None, ""):
                raise ValueError("a row in a file needs `when`, so that applying the file twice is seen and refused")
            rows.append(_note_fields(root, spec, by_user, stamp, hash_now=False))
        except (ValueError, ImproveError) as exc:
            raise ImproveError(f"{path} line {number}: {exc}; nothing was recorded") from None
    if not rows:
        raise ImproveError(f"{path} has no notes")
    return _write_notes(home, rows, stamp)


def _write_notes(home: Path, rows: list[dict], now: float) -> list[dict]:
    try:
        return ledger.changed(Path(home), [{**row, "now": now} for row in rows])
    except ValueError as exc:
        raise ImproveError(f"{exc}; nothing was recorded") from None


def _metric_text(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, dict):
        return _phase_text("", value).lstrip(": ").strip() if "runs" in value else ", ".join(f"{k} {v}" for k, v in value.items())
    return f"{value:,}" if isinstance(value, int) else f"{value:,.4g}" if isinstance(value, float) else str(value)


def changes(home: Path) -> list[dict]:
    """Every change in the ledger, oldest first (`ledger.changes`)."""
    return ledger.changes(Path(home))


def _changes_text(found: list[dict]) -> str:
    if not found:
        return "no changes recorded"
    lines = []
    for item in found:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(item["time"]))
        line = f"{when}  {item['kind']:<9} {item['title']}"
        if item["metric_before"] is not None or item["metric_after"] is not None:
            unit = f" ({item['unit']})" if item["unit"] else ""
            line += f"\n{'':>29}before {_metric_text(item['metric_before'])}; after {_metric_text(item['metric_after'])}{unit}"
        elif item["kind"] != "improved":
            line += f"\n{'':>29}no metric recorded"
        lines.append(line)
    return "\n".join(lines)


# --- command line ------------------------------------------------------------------------------------


def _money(value) -> str:
    return "-" if value is None else f"${value:.2f}" if isinstance(value, float) else str(value)


def _show_text(shown: dict) -> str:
    lines = [f"proposal {shown['id']}  [{shown['state']}]  {shown['title']}",
             f"why: {shown['why']}"]
    evidence = shown["evidence"]
    for key in ("cases", "notes", "runs"):
        if evidence.get(key):
            lines.append(f"evidence {key}: {', '.join(evidence[key])}")
    lines.extend(f'quote: "{quote}"' for quote in evidence.get("quotes", []))
    lines.append("evaluation: " + shown["summary"]["text"])
    lines.append("ranking: " + shown["summary"]["ranking"]["text"])
    lines.append(COST_RULE)
    for phase in ("before", "after"):
        for row in shown["evaluation"][phase]:
            lines.append(f"  {phase:<6} {row['split']:<8} {row['case']} {row['outcome']}  cost {_money(row['cost'])}"
                         f"  turns {_money(row['turns'])}  {row.get('source') or ''}".rstrip())
    for entry in shown["files"]:
        notes = []
        if not entry["base_matches"] and shown["state"] != "applied":
            notes.append("file changed since drafting")
        if entry.get("after_matches") is False:
            notes.append("file edited since applying")
        lines.append(f"change {entry['path']}  +{entry['added']} -{entry['removed']}" + (f"  ({'; '.join(notes)})" if notes else ""))
        lines.append(entry["diff"].rstrip("\n") if entry["diff"] else
                     "  (the backup and the new text are the same)" if entry.get("diff_against") == "backup"
                     else "  (no difference from the file now)")
    for item in shown["history"]:
        when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item["time"]))
        lines.append(f"history {when} {item['state']} by {item['who']}: {item['note']}")
    if shown["applications"]:
        lines.append("backup: " + shown["applications"][-1]["backup"])
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentgraph improve", description=__doc__.split("\n\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config-root", type=Path, help="OpenCode's config folder (default: the live one)")
    common.add_argument("--home", type=Path, help="ledger home (default: $AGENTGRAPH_HOME or the active preset's)")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("propose", parents=[common], help="draft a proposal")
    p.add_argument("--title", required=True)
    p.add_argument("--why", required=True)
    p.add_argument("--change", action="append", required=True, metavar="PATH=NEWFILE")
    p.add_argument("--case", action="append", default=[], metavar="ID")
    p.add_argument("--note-id", action="append", default=[])
    p.add_argument("--run", action="append", default=[])
    p.add_argument("--quote", action="append", default=[])
    p = commands.add_parser("result", parents=[common], help="record one run before or after the change")
    p.add_argument("id")
    p.add_argument("--phase", required=True, choices=("before", "after"))
    p.add_argument("--case", required=True)
    p.add_argument("--outcome", required=True, choices=("pass", "fail"))
    p.add_argument("--cost", type=float)
    p.add_argument("--turns", type=int)
    p.add_argument("--source")
    p = commands.add_parser("import-bench", parents=[common], help="record the rows of a bench label as results")
    p.add_argument("id")
    p.add_argument("--phase", required=True, choices=("before", "after"))
    p.add_argument("--label", required=True)
    p.add_argument("--results", type=Path, help="bench results folder (default: bench/results)")
    line = "answer as one line of JSON, an error as {\"error\"} (what the desktop panel reads)"
    p = commands.add_parser("show", parents=[common], help="one proposal with its diffs")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")
    p.add_argument("--line", action="store_true", help=line)
    p = commands.add_parser("list", parents=[common], help="every proposal")
    p.add_argument("--json", action="store_true")
    p.add_argument("--line", action="store_true", help=line)
    p = commands.add_parser("approve", parents=[common], help="apply a proposal (after a backup)")
    p.add_argument("id")
    p.add_argument("--by-user", required=True, metavar="WORDS", help="the user's own words approving it")
    p.add_argument("--force", action="store_true", help="apply even if a file changed since drafting")
    p.add_argument("--line", action="store_true", help=line + "; it answers the proposal and is the user's own click")
    p = commands.add_parser("reject", parents=[common], help="reject a proposal; no file changes")
    p.add_argument("id")
    p.add_argument("--reason", required=True)
    p.add_argument("--line", action="store_true", help=line + "; it answers the proposal and is the user's own click")
    p = commands.add_parser("reopen", parents=[common], help="undo a rejection; no file changes")
    p.add_argument("id")
    p.add_argument("--note", default="")
    p.add_argument("--line", action="store_true", help=line + "; it answers the proposal and is the user's own click")
    p = commands.add_parser("revert", parents=[common], help="put back what an applied proposal replaced")
    p.add_argument("id")
    p.add_argument("--force", action="store_true", help="restore even if a file was edited after applying")
    p.add_argument("--note", default="")
    p.add_argument("--line", action="store_true", help=line + "; it answers the proposal and is the user's own click")
    p = commands.add_parser("note", parents=[common], help="record a change made outside improve (settings, an install, a revert...)")
    p.add_argument("--kind", choices=NOTE_KINDS)
    p.add_argument("--title")
    p.add_argument("--why")
    p.add_argument("--files", nargs="*", default=[], metavar="PATH", help="paths below the config root; each is recorded with its sha256, never its contents")
    p.add_argument("--metric-before", metavar="VALUE")
    p.add_argument("--metric-after", metavar="VALUE")
    p.add_argument("--unit", help="what the metric counts")
    p.add_argument("--when", help="when it was changed: YYYY-MM-DD, YYYY-MM-DD HH:MM[:SS] or seconds (default: now)")
    p.add_argument("--by-user", metavar="WORDS", help="the user's own words approving the change (required)")
    p.add_argument("--from-file", type=Path, metavar="FILE", help="record every note in a JSON-lines file (all or none); rows with no by_user take --by-user")
    p = commands.add_parser("evidence", parents=[common], help="cost outliers, rules broken and feedback groups from what is on disk (no model call)")
    from . import evidence
    evidence.add_arguments(p)
    p = commands.add_parser("changes", parents=[common], help="every change in time order: date, kind, title, metric before and after")
    p.add_argument("--json", action="store_true")
    return parser


def _changes(values: list[str]) -> list[tuple[str, str]]:
    found = []
    for value in values:
        relative, separator, source = value.partition("=")
        if not separator or not relative or not source:
            raise ImproveError(f"--change needs PATH=NEWFILE, got {value!r}")
        try:
            found.append((relative, Path(source).expanduser().read_bytes().decode("utf-8")))
        except (OSError, UnicodeDecodeError) as exc:
            raise ImproveError(f"cannot read {source} as UTF-8 text: {exc}") from None
    return found


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    line = getattr(args, "line", False)
    try:
        root, home = config_root(args.config_root), default_home(args.home)
        if args.command == "propose":
            record = propose(home, root, args.title, args.why, _changes(args.change), cases_=args.case,
                             notes=args.note_id, runs=args.run, quotes=args.quote)
            print(f"proposed {record['id']}: {len(record['changes'])} change(s); `improve show {record['id']}` to read it")
        elif args.command == "result":
            record = add_result(home, args.id, args.phase, args.case, args.outcome, cost=args.cost, turns=args.turns,
                                source=args.source)
            print(f"recorded; {summary(record['evaluation'])['text']}")
        elif args.command == "import-bench":
            record, count = import_bench(home, args.id, args.phase, args.label, args.results)
            print(f"imported {count} {args.phase} row(s); {summary(record['evaluation'])['text']}")
        elif args.command == "note":
            if args.from_file is not None:
                if args.kind or args.title or args.why or args.files or args.metric_before or args.metric_after or args.unit or args.when:
                    raise ImproveError("--from-file takes the notes from the file; only --by-user goes with it")
                done = note_file(home, root, args.from_file, by_user=args.by_user)
                print(f"recorded {len(done)} note(s) from {args.from_file}")
            else:
                if not args.kind or not args.title or not args.why:
                    raise ImproveError("note needs --kind, --title and --why (or --from-file)")
                done = [note(home, root, args.kind, args.title, args.why, by_user=args.by_user or "", files=args.files,
                             metric_before=_metric_arg(args.metric_before), metric_after=_metric_arg(args.metric_after),
                             unit=args.unit, when=args.when)]
                print(f"recorded {args.kind}: {args.title}")
        elif args.command == "evidence":
            from . import evidence
            return evidence.run(args)
        elif args.command == "changes":
            found = changes(home)
            print(json.dumps({"changes": found}, indent=2, ensure_ascii=False) if args.json else _changes_text(found))
        elif args.command == "show":
            shown = show(home, root, args.id)
            print(_one_line(shown) if line else json.dumps(shown, indent=2, ensure_ascii=False) if args.json else _show_text(shown))
        elif args.command == "list":
            items = list_proposals(home, root)
            if line:
                print(_one_line({"proposals": items}))
            elif args.json:
                print(json.dumps({"proposals": items}, indent=2, ensure_ascii=False))
            else:
                for item in items:
                    print(f"{item['id']}  {item['state']:<9} {item['summary']['verdict']:<16} +{item['added']} -{item['removed']}  {item['title']}")
                if not items:
                    print("no proposals")
        elif args.command == "approve":
            record = approve(home, root, args.id, who="user", note=args.by_user, force=args.force)
            if line:
                print(_one_line(show(home, root, args.id)))
            else:
                print(f"applied {record['id']}; backup {record['applications'][-1]['backup']}")
                print("evaluation: " + summary(record["evaluation"])["text"])
                print("ranking: " + summary(record["evaluation"])["ranking"]["text"])
        elif args.command == "reject":
            reject(home, args.id, args.reason, who="user" if line else "command")
            print(_one_line(show(home, root, args.id)) if line else f"rejected {args.id}; no file was changed")
        elif args.command == "reopen":
            record = reopen(home, args.id, who="user" if line else "command", note=args.note)
            print(_one_line(show(home, root, args.id)) if line else f"reopened {args.id}; it is {record['state']} again")
        else:
            revert(home, root, args.id, who="user" if line else "command", note=args.note, force=args.force)
            print(_one_line(show(home, root, args.id)) if line else f"reverted {args.id}; the files are as they were before it was applied")
        return 0
    except (ImproveError, OSError) as exc:
        if line:
            print(_one_line({"error": str(exc)}))
        else:
            print(f"improve: {exc}", file=sys.stderr)
        return 1


def _one_line(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


if __name__ == "__main__":
    sys.exit(main())
