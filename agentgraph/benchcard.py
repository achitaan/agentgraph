"""The scorecard of `agentgraph bench card LABEL_A LABEL_B [--html FILE] [--suite NAME]`: on the same fixed tasks, is
setup B better than setup A?

A label names `bench/results/<label>.jsonl` (rows of `bench run` and `bench suite`). `l-suite:METHOD` names the rows of
`l-suite.jsonl` made by one method (`direct`, `loop-1`, `loop-2`). Runs are paired by task; a task only one label has is
listed and left out of the verdicts. Verdicts say how B compares with A, by a fixed rule that is printed on the card:

- Passing: a sign test over tasks. Each task goes to the setup that passed the larger share of its runs (a tie goes to
  neither); the two-sided exact p value of that split must be at most 0.10.
- Cost, time, turns, agent calls: the median over tasks of B divided by A (each task's value is the median over its runs);
  at least 5 tasks, and at least 80% of them lower (better), equal (same) or higher (worse).
- Anything else is `too few runs to say`. A verdict also needs at least 5 compared tasks.

The text and the page hold task ids, labels, numbers and the model name only, never a prompt, command, session id or path.
"""
from __future__ import annotations

import html
import re
import statistics
from fractions import Fraction
from math import comb
from pathlib import Path

from . import bench

MIN_TASKS = 5
MIN_AGREE = 0.8
MAX_P = 0.10
TOO_FEW = "too few runs to say"
LSUITE = "l-suite"
MEASURES = (("cost", "cost a run"), ("minutes", "time a run"), ("turns", "turns a run"), ("calls", "agent calls a run"))
RULE = (
    f"Passing: a sign test over tasks. Each task goes to the setup that passed the larger share of its runs; ties go to neither. "
    f"A verdict needs a p value of at most {MAX_P:.2f}.",
    f"Cost, time, turns and agent calls: the median over tasks of B divided by A (a task's value is the median of its runs). "
    f"A verdict needs at least {MIN_TASKS} tasks and at least {MIN_AGREE:.0%} of them pointing the same way.",
    f"Better, worse and same describe B compared with A, and every verdict needs at least {MIN_TASKS} tasks both have. "
    f"Anything else is \"{TOO_FEW}\".",
)


# ---- reading the runs ----

def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _scenario_run(row: dict):
    check = row.get("check")
    passed = check.get("passed") if isinstance(check, dict) else None
    if row.get("probe_agent") or not isinstance(passed, bool) or not isinstance(row.get("scenario"), str):
        return None
    calls = row.get("agent_calls")
    return {"task": row["scenario"], "passed": passed, "cost": _number(row.get("cost")), "seconds": _number(row.get("wall_seconds")),
            "turns": _number(row.get("turns")),
            "calls": sum(v for v in calls.values() if _number(v) is not None) if isinstance(calls, dict) else None}


def _lsuite_run(row: dict):
    """A run of the l-suite passes when its visible and its hidden check both did; `calls` there are model steps (turns)."""
    if row.get("fake") or not isinstance(row.get("task"), str) or not isinstance(row.get("visible"), bool) or not isinstance(row.get("hidden"), bool):
        return None
    roots, sessions = row.get("roots"), _number(row.get("sessions"))
    return {"task": row["task"], "passed": row["visible"] and row["hidden"], "cost": _number(row.get("cost")),
            "seconds": _number(row.get("seconds")), "turns": _number(row.get("calls")),
            "calls": max(0, sessions - len(roots)) if sessions is not None and isinstance(roots, list) else None}


def load_runs(spec: str, results: Path | None = None, suite: str | None = None) -> dict:
    """{label, runs, hashes, models, notes} for a label or `l-suite:METHOD`. A label with no file or no rows gives no runs
    and a note."""
    name, colon, method = spec.partition(":")
    lsuite = bool(colon) and name == LSUITE
    if colon and not lsuite:
        raise bench.BenchError(f"{spec!r}: only {LSUITE}:METHOD takes a method")
    if lsuite and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", method):
        raise bench.BenchError(f"{spec!r}: the method must be a plain name")
    notes, rows = [], []
    folder = Path(results) if results is not None else bench.RESULTS
    if (folder / f"{bench._label(name)}.jsonl").is_file():
        rows = bench.read_rows(name, results)
    else:
        notes.append(f"{spec}: no results file")
    if lsuite:
        rows = [r for r in rows if r.get("method") == method]
    if suite is not None:
        rows = [r for r in rows if r.get("suite") == suite]
    pairs = [(row, (_lsuite_run if lsuite else _scenario_run)(row)) for row in rows]
    skipped = sum(1 for _, run in pairs if run is None)
    pairs = [(row, run) for row, run in pairs if run is not None]
    if skipped and pairs:
        notes.append(f"{spec}: {skipped} rows without a pass or fail result were left out")
    if not pairs and not notes:
        notes.append(f"{spec}: no rows with a pass or fail result")
    hashes = sorted({str(row["config_hash"]) for row, _ in pairs if row.get("config_hash")})
    if len(hashes) > 1:
        notes.append(f"{spec}: rows from {len(hashes)} different configs ({', '.join(hashes)}); they are not one setup")
    models = sorted({str(row["model"]) for row, _ in pairs if row.get("model")})
    runs = [run for _, run in pairs]
    for run in runs:
        run["minutes"] = run["seconds"] / 60 if run["seconds"] is not None else None
    return {"label": spec, "runs": runs, "hashes": hashes, "models": models, "notes": notes}


# ---- the numbers ----

def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _by_task(runs: list[dict]) -> dict:
    tasks: dict = {}
    for run in runs:
        tasks.setdefault(run["task"], []).append(run)
    return tasks


def _task_row(runs: list[dict] | None) -> dict | None:
    if not runs:
        return None
    return {"runs": len(runs), "passed": sum(1 for r in runs if r["passed"]),
            **{key: _median([r[key] for r in runs]) for key, _ in MEASURES}}


def _summary(data: dict) -> dict:
    runs = data["runs"]
    tasks = _by_task(runs)
    return {"label": data["label"], "runs": len(runs), "passed": sum(1 for r in runs if r["passed"]), "tasks": len(tasks),
            "tasks_all_passed": sum(1 for t in tasks.values() if all(r["passed"] for r in t)),
            **{key: _median([r[key] for r in runs]) for key, _ in MEASURES}}


def sign_test_p(wins_a: int, wins_b: int) -> float:
    """Two-sided exact p value of a split of `wins_a` and `wins_b` between two setups that win equally often."""
    n = wins_a + wins_b
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(wins_a, wins_b) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _ratio(a, b):
    """B divided by A; 1.0 when both are zero, infinity when only A is zero."""
    if a == 0:
        return 1.0 if b == 0 else float("inf")
    return b / a


def _tasks(n: int) -> str:
    return f"{n} task" + ("" if n == 1 else "s")


def passing_verdict(pairs: list[tuple[dict, dict]]) -> dict:
    """pairs: (task row of A, task row of B)."""
    a_more = sum(1 for a, b in pairs if Fraction(a["passed"], a["runs"]) > Fraction(b["passed"], b["runs"]))
    b_more = sum(1 for a, b in pairs if Fraction(a["passed"], a["runs"]) < Fraction(b["passed"], b["runs"]))
    tied = len(pairs) - a_more - b_more
    p = sign_test_p(a_more, b_more)
    detail = f"B passed a larger share on {_tasks(b_more)}, A on {_tasks(a_more)}, {tied} tied" + (f"; p = {p:.3f}" if a_more + b_more else "")
    if len(pairs) < MIN_TASKS:
        verdict = TOO_FEW
    elif a_more + b_more == 0:
        verdict = "same"
    elif p <= MAX_P:
        verdict = "better" if b_more > a_more else "worse"
    else:
        verdict = TOO_FEW
    return {"measure": "passing", "name": "passing", "verdict": verdict, "detail": detail, "tasks": len(pairs)}


def measure_verdict(key: str, name: str, pairs: list[tuple[dict, dict]]) -> dict:
    ratios = [_ratio(a[key], b[key]) for a, b in pairs if a[key] is not None and b[key] is not None]
    if not ratios:
        return {"measure": key, "name": name, "verdict": TOO_FEW, "detail": "no task has this number on both sides", "tasks": 0}
    lower, higher = sum(1 for r in ratios if r < 1), sum(1 for r in ratios if r > 1)
    equal = len(ratios) - lower - higher
    median = statistics.median(ratios)
    shown = "no ratio (A is zero)" if median == float("inf") else f"median B/A {median:.2f}"
    detail = f"{shown}; B lower on {_tasks(lower)}, higher on {_tasks(higher)}, equal on {equal}"
    verdict = TOO_FEW
    if len(ratios) >= MIN_TASKS:
        for count, word in ((lower, "better"), (equal, "same"), (higher, "worse")):
            if count / len(ratios) >= MIN_AGREE:
                verdict = word
    return {"measure": key, "name": name, "verdict": verdict, "detail": detail, "tasks": len(ratios)}


def build_card(a: dict, b: dict, suite: str | None = None) -> dict:
    """The whole card as plain data, from two `load_runs` results."""
    tasks_a, tasks_b = _by_task(a["runs"]), _by_task(b["runs"])
    both = sorted(set(tasks_a) & set(tasks_b))
    pairs = [(_task_row(tasks_a[t]), _task_row(tasks_b[t])) for t in both]
    verdicts = [passing_verdict(pairs)] + [measure_verdict(key, name, pairs) for key, name in MEASURES]
    warnings = list(a["notes"]) + list(b["notes"])
    only_a, only_b = sorted(set(tasks_a) - set(tasks_b)), sorted(set(tasks_b) - set(tasks_a))
    if only_a:
        warnings.append(f"tasks only in {a['label']}, left out of the verdicts: {', '.join(only_a)}")
    if only_b:
        warnings.append(f"tasks only in {b['label']}, left out of the verdicts: {', '.join(only_b)}")
    if a["models"] and b["models"] and a["models"] != b["models"]:
        warnings.append("the two setups did not use the same model")
    return {"a": _summary(a), "b": _summary(b), "compared": both, "only_a": only_a, "only_b": only_b, "verdicts": verdicts,
            "table": [{"task": t, "a": _task_row(tasks_a.get(t)), "b": _task_row(tasks_b.get(t)), "compared": t in both}
                      for t in sorted(set(tasks_a) | set(tasks_b))],
            "warnings": warnings, "suite": suite, "models": sorted(set(a["models"]) | set(b["models"]))}


# ---- text ----

def _money(value) -> str:
    return "-" if value is None else (f"${value:.4f}" if value < 1 else f"${value:.2f}")


def _plain(value) -> str:
    return "-" if value is None else f"{value:.1f}".rstrip("0").rstrip(".")


def _minutes(value) -> str:
    return "-" if value is None else f"{value:.1f}"


def _cells(summary: dict) -> list[str]:
    return [f"{summary['passed']} of {summary['runs']}", f"{summary['tasks_all_passed']} of {summary['tasks']}", _money(summary["cost"]),
            _minutes(summary["minutes"]), _plain(summary["turns"]), _plain(summary["calls"])]


def render_text(card: dict) -> str:
    a, b = card["a"], card["b"]
    lines = [f"bench card: {a['label']} (A) against {b['label']} (B)" + (f", suite {card['suite']}" if card["suite"] else "")]
    if card["models"]:
        lines.append("model: " + ", ".join(card["models"]))
    lines += [f"warning: {w}" for w in card["warnings"]]
    lines.append("")
    width = max(14, len(a["label"]) + 2, len(b["label"]) + 2)
    lines.append(f"{'':<24}{a['label']:>{width}}{b['label']:>{width}}")
    names = ("runs passed", "tasks all passed", "cost a run (median)", "minutes a run (median)", "turns (median)", "agent calls (median)")
    for name, x, y in zip(names, _cells(a), _cells(b)):
        lines.append(f"{name:<24}{x:>{width}}{y:>{width}}")
    lines += ["", f"B compared with A, on {_tasks(len(card['compared']))} both have:"]
    lines += [f"  {v['name']:<18}{v['verdict']:<22}{v['detail']}" for v in card["verdicts"]]
    lines += ["", "How to read this:"] + [f"  {text}" for text in RULE]
    return "\n".join(lines) + "\n"


# ---- the page ----

CSS = """
html { background: #121111; }
body { margin: 0 auto; max-width: 980px; padding: 56px 32px 80px; background: #121111; color: #e9ddd3;
       font: 16px/1.6 -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; }
h1 { font-size: 30px; font-weight: 500; margin: 0 0 8px; }
h2 { font-size: 20px; font-weight: 500; margin: 64px 0 20px; color: #f0b79a; }
.small, td.small, th { font-size: 13px; }
.muted, .neutral { color: #8f8a85; }
.accent, .better { color: #f0b79a; }
.worse { color: #e8705f; }
.setups { display: grid; grid-template-columns: 1fr 1fr; gap: 48px; margin-top: 40px; }
.setup h3 { font-size: 16px; font-weight: 500; margin: 0 0 16px; }
.numbers { display: flex; gap: 32px; }
.number .big { font-size: 40px; line-height: 1.1; font-weight: 300; color: #f0b79a; }
.number .small { display: block; margin-top: 8px; color: #8f8a85; }
ul.verdicts { list-style: none; padding: 0; margin: 0; }
ul.verdicts li { padding: 12px 0; border-bottom: 1px solid #2a2726; }
ul.verdicts .word { display: inline-block; min-width: 230px; }
table { border-collapse: collapse; width: 100%; margin-top: 8px; }
th, td { text-align: left; padding: 14px 12px 14px 0; border-bottom: 1px solid #2a2726; font-weight: 400; vertical-align: middle; }
th { color: #8f8a85; }
.dot { display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 5px; box-sizing: border-box; }
.dot.on { background: #f0b79a; border: 1.5px solid #f0b79a; }
.dot.off { background: transparent; border: 1.5px solid #e8705f; }
.dots .count { margin-left: 6px; color: #8f8a85; font-size: 13px; }
.how p { margin: 0 0 14px; }
.how ul { margin: 0; padding-left: 20px; }
@media (max-width: 720px) { .setups { grid-template-columns: 1fr; } .numbers { flex-wrap: wrap; } }
"""


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _verdict_class(verdict: str) -> str:
    return verdict if verdict in ("better", "worse") else "neutral"


def _dots(row) -> str:
    if row is None:
        return '<span class="muted">-</span>'
    on, off = row["passed"], row["runs"] - row["passed"]
    dots = '<span class="dot on"></span>' * on + '<span class="dot off"></span>' * off
    return (f'<span class="dots" role="img" aria-label="{on} of {row["runs"]} runs passed">{dots}'
            f'<span class="count">{on}/{row["runs"]}</span></span>')


def _setup_block(name: str, summary: dict) -> str:
    return (f'<div class="setup"><h3>{_e(name)}</h3><div class="numbers">'
            f'<div class="number"><span class="big">{_e(summary["passed"])} of {_e(summary["runs"])}</span><span class="small">runs passed</span></div>'
            f'<div class="number"><span class="big">{_e(_money(summary["cost"]))}</span><span class="small">cost a run</span></div>'
            f'<div class="number"><span class="big">{_e(_minutes(summary["minutes"]))}</span><span class="small">minutes a run</span></div>'
            "</div></div>")


def _cell(row, key: str) -> str:
    if row is None or row[key] is None:
        return '<td class="small muted">-</td>'
    return f'<td class="small">{_e(_money(row[key]) if key == "cost" else _minutes(row[key]))}</td>'


def render_html(card: dict) -> str:
    a, b = card["a"], card["b"]
    subtitle = f"A is {_e(a['label'])}, B is {_e(b['label'])}" + (f" · suite {_e(card['suite'])}" if card["suite"] else "")
    verdicts = "".join(
        f'<li><span class="word {_verdict_class(v["verdict"])}">{_e(v["name"])}: {_e(v["verdict"])}</span>'
        f'<span class="small muted">{_e(v["detail"])}</span></li>' for v in card["verdicts"])
    rows = []
    for item in card["table"]:
        flag = "" if item["compared"] else ' <span class="small muted">(not compared)</span>'
        cells = "".join(_cell(item[side], key) for key in ("cost", "minutes") for side in ("a", "b"))
        rows.append(f"<tr><td>{_e(item['task'])}{flag}</td><td>{_dots(item['a'])}</td><td>{_dots(item['b'])}</td>{cells}</tr>")
    table = ("<table><thead><tr><th>Task</th><th>Passed in A</th><th>Passed in B</th><th>Cost in A</th><th>Cost in B</th>"
             "<th>Minutes in A</th><th>Minutes in B</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
             if rows else '<p class="muted">No runs to show.</p>')
    notes = "".join(f"<li>{_e(w)}</li>" for w in card["warnings"])
    how = (f'<div class="how"><p>{_e(RULE[0])}</p><p>{_e(RULE[1])}</p><p>{_e(RULE[2])}</p>'
           f'<p>Runs: {_e(a["runs"])} in A and {_e(b["runs"])} in B; {_e(len(card["compared"]))} tasks are in both. '
           f'Model: {_e(", ".join(card["models"]) or "not recorded")}. The numbers at the top use all of a setup\'s own runs; '
           f'the verdicts use only the tasks both have.</p>'
           + (f'<p class="accent">Warnings</p><ul>{notes}</ul>' if notes else '<p class="muted">No warnings.</p>') + "</div>")
    return ('<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>Bench card: {_e(a['label'])} against {_e(b['label'])}</title><style>{CSS}</style></head><body>"
            f'<h1>{_e(a["label"])} <span class="muted">against</span> {_e(b["label"])}</h1><div class="small muted">{subtitle}</div>'
            f'<div class="setups">{_setup_block("A: " + a["label"], a)}{_setup_block("B: " + b["label"], b)}</div>'
            f'<h2>B compared with A</h2><ul class="verdicts">{verdicts}</ul>'
            f"<h2>Task by task</h2>{table}"
            f'<h2>How to read this</h2>{how}</body></html>\n')


def run_card(label_a: str, label_b: str, *, results: Path | None = None, suite: str | None = None, html_file: Path | None = None) -> str:
    """The card's text; with `html_file` the page is written there too."""
    card = build_card(load_runs(label_a, results, suite), load_runs(label_b, results, suite), suite)
    if html_file is not None:
        html_file = Path(html_file)
        html_file.parent.mkdir(parents=True, exist_ok=True)
        html_file.write_text(render_html(card), encoding="utf-8")
    return render_text(card)
