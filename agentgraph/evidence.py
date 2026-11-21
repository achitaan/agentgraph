"""Evidence for a proposal beyond the nodes that needed more than one attempt: cost, rules broken, the user's feedback.

`agentgraph improve evidence` reads what is already on disk and prints three ranked lists (`--json` for the same as data).
No model is called, no message text is read, nothing is written (except `--to-cases`, below).

1. Cost outliers. A worker session is listed when its cost per turn, or its total cost, is at least `--factor` times the
   median of its agent's worker sessions (the agent needs `--min-sessions` of them, and the session `--min-cost` dollars).
   Read from the session database through `cost.load_billing`; the context size is the largest `input + cache read +
   cache write` of any turn. Ranked by dollars above the agent's median session. Only ids, agent, model, turns, cost and
   context are shown, never what was said.
2. Rules broken that code can see, from the task graphs (`.opencode/graph/*.json`) and the bench results: a node passed with
   no evidence line, three or more attempts with no `stalled`, `changed_paths` outside `scope`, a worker that ran with no
   session, three or more worker nodes in a chain with the same scope (fix rounds as separate nodes, not retries of one),
   and a bench check that failed in most of its runs. Each rule shows how many times it broke, out of how many it could
   have, and where. Only plain names are shown; text from a graph file is counted, not printed.
3. Feedback groups. The user's notes (`feedback.jsonl`, every `memory*` folder next to the orchestration, or the files
   named) grouped by agent and then by topic: two notes are alike when they share enough of their words (the overlap
   coefficient of their word sets, `SIMILAR`, at least `SHARED` words; stop words left out). A group shows its size, its
   most common words, the note ids and the first 100 characters of one example.

`--to-cases` turns the top `--top` items of each list (or the `--pick KEY` items) into cases of kind `cost`, `rule` or
`feedback` in the memory home, so `agentgraph cases` lists them and a proposal can cite them. They are not replayable.
It refuses a live home: `--home` must be a scratch folder.

    python3 -m agentgraph.improve evidence [--json] [--limit N] [--db DB] [--graphs DIR] [--results DIR] [--feedback FILE ...]
    python3 -m agentgraph.improve evidence --home SCRATCH --to-cases [--top N] [--only cost|rule|feedback] [--pick KEY ...]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

from . import bench, cases, contract, cost, ledger, sources
from .handedits import AGENT_NAME, OUTPUT_NAME

ORCHESTRATION = Path(__file__).resolve().parent.parent
FACTOR = 3.0
MIN_SESSIONS = 5
MIN_COST = 1.0
ATTEMPTS = 3
CHAIN = 3
SIMILAR = 0.3           # overlap coefficient two notes need to be in one group
SHARED = 2              # words two notes need to share
TOP_WORDS = 6
EXAMPLE = 100
WHERE = 8               # places a rule shows
WORKER_DONE = ("passed", "failed")
STOP = frozenset("""a about above after again all also am an and any are as at be because been before being below between
both but by can cannot could did do does doing down during each few for from further had has have having he her here hers him
his how i if in into is it its just like me more most my no nor not now of off on once only or other our out over own same
she should so some such than that the their them then there these they this those through to too under until up us very was
we were what when where which while who whom why will with would you your yours dont doesnt isnt thats youre ive im lets one
also still really maybe something thing things""".split())
RULES = {
    "passed_without_evidence": "a node was marked passed with no evidence line",
    "attempts_without_stall": f"{ATTEMPTS} or more attempts and the node was never marked stalled",
    "changed_outside_scope": "a node changed a path outside its scope",
    "worker_without_session": "a worker node that passed or failed has no session recorded",
    "fix_rounds_as_nodes": f"{CHAIN} or more worker nodes in a chain with the same scope: fix rounds as separate nodes, not retries of one (of: worker nodes)",
    "bench_check_fails_mostly": "a bench check failed in more than half of its runs (two runs or more)",
}
CONTEXT = """
    SELECT session_id, max(CASE WHEN json_valid(data) THEN
        coalesce(json_extract(data, '$.tokens.input'), 0) + coalesce(json_extract(data, '$.tokens.cache.read'), 0)
        + coalesce(json_extract(data, '$.tokens.cache.write'), 0) END)
    FROM session_message WHERE type = 'assistant' GROUP BY session_id
"""


# --- 1. cost -----------------------------------------------------------------------------------------


def cost_outliers(db_path: Path, *, factor: float = FACTOR, min_sessions: int = MIN_SESSIONS, min_cost: float = MIN_COST) -> list[dict]:
    """The worker sessions far above their agent's median (see the module docstring), the dearest excess first."""
    with closing(cost._connect(db_path)) as db:
        parents = [row[0] for row in db.execute("SELECT DISTINCT parent_id FROM session_v2 WHERE parent_id IS NOT NULL")]
        context = {row[0]: int(row[1] or 0) for row in db.execute(CONTEXT)}
    sessions = []
    for parent in parents:
        sessions += [{**row, "root": parent} for row in cost.load_billing(parent, db_path)["workers"]["list"] if row["turns"] > 0]
    by_agent: dict[str, list[dict]] = {}
    for row in sessions:
        by_agent.setdefault(row["agent"], []).append(row)
    found = []
    for agent, rows in by_agent.items():
        if len(rows) < min_sessions:
            continue
        median_turn = statistics.median(row["billed"] / row["turns"] for row in rows)
        median_total = statistics.median(row["billed"] for row in rows)
        for row in rows:
            per_turn = row["billed"] / row["turns"]
            ratios = {"per turn": per_turn / median_turn if median_turn else 0.0,
                      "total": row["billed"] / median_total if median_total else 0.0}
            reasons = [name for name, ratio in ratios.items() if ratio >= factor]
            if reasons and row["billed"] >= min_cost:
                found.append({"key": f"cost:{row['id']}", "session": row["id"], "root": row["root"], "agent": agent,
                              "model": row["model"], "turns": row["turns"], "billed": row["billed"], "per_turn": per_turn,
                              "context": context.get(row["id"], 0), "median_per_turn": median_turn,
                              "median_total": median_total, "times_per_turn": ratios["per turn"],
                              "times_total": ratios["total"], "reasons": reasons, "excess": row["billed"] - median_total,
                              "agent_sessions": len(rows)})
    return sorted(found, key=lambda item: (-item["excess"], item["session"]))


# --- 2. rules ----------------------------------------------------------------------------------------


def _where(graph, node) -> str:
    return f"{graph.task if OUTPUT_NAME.fullmatch(graph.task) else '(not a plain name)'}/" \
           f"{node.id if OUTPUT_NAME.fullmatch(node.id) else '(not a plain name)'}"


def _outside_scope(graph, node) -> bool:
    root = contract._project_root(graph.path)
    scope = [contract._path_parts(item, root) for item in node.scope if isinstance(item, str) and item.strip()]
    return any(not any(contract._contains(allowed, contract._path_parts(path, root)) for allowed in scope)
               for path in node.changed_paths if isinstance(path, str) and path.strip())


def graph_rules(graphs: list) -> dict[str, dict]:
    """`{rule: {"count", "of", "where"}}` for the five graph rules."""
    found = {name: {"count": 0, "of": 0, "where": []} for name in RULES if name != "bench_check_fails_mostly"}

    def note(name: str, graph, node) -> None:
        found[name]["count"] += 1
        found[name]["where"].append(_where(graph, node))

    for graph in graphs:
        for node in graph.nodes:
            workers = cases._worker(node)
            found["fix_rounds_as_nodes"]["of"] += node.type in contract.WORK_NODE_TYPES
            found["passed_without_evidence"]["of"] += node.status == "passed"
            if node.status == "passed" and not (isinstance(node.evidence, str) and node.evidence.strip()):
                note("passed_without_evidence", graph, node)
            found["attempts_without_stall"]["of"] += node.attempts >= ATTEMPTS
            if node.attempts >= ATTEMPTS and not node.stalled:
                note("attempts_without_stall", graph, node)
            if node.changed_paths:
                found["changed_outside_scope"]["of"] += 1
                if _outside_scope(graph, node):
                    note("changed_outside_scope", graph, node)
            if workers and node.status in WORKER_DONE:
                found["worker_without_session"]["of"] += 1
                if not node.session:
                    note("worker_without_session", graph, node)
        layers = contract.waves(graph)
        for chain in contract._repeated_scope_chains(layers):
            found["fix_rounds_as_nodes"]["count"] += 1
            found["fix_rounds_as_nodes"]["where"].append(f"{graph.task if OUTPUT_NAME.fullmatch(graph.task) else '(not a plain name)'}/"
                                                         + "+".join(item if OUTPUT_NAME.fullmatch(item) else "?" for item in chain))
    return found


_CRITERION = re.compile(r"(?:^|; )([a-z][a-z-]*) (ok|FAIL|n/a)\b|(?:^|; )(cost) \S+ (<=|>) ")


def bench_rules(results: Path) -> dict:
    """Bench criteria that failed in more than half of their runs, over every result file. A run is counted once per
    criterion (a label that scores the same run again, such as `p4-after-cost` after `p4-after`, does not count it twice).
    The criteria are read from each row's check detail: `name ok`, `name FAIL`, `cost N <= N` or `cost N > N`."""
    runs: dict[tuple[str, str, str], bool] = {}
    labels: dict[tuple[str, str], set[str]] = {}
    for path in sorted(Path(results).glob("*.jsonl")):
        try:
            rows = bench.read_rows(path.stem, results)
        except (bench.BenchError, OSError):
            continue
        for row in rows:
            check = row.get("check")
            scenario = row.get("scenario")
            if not isinstance(check, dict) or not isinstance(check.get("detail"), str) or not isinstance(scenario, str):
                continue
            run = str(row.get("root_session") or f"{path.stem}:{row.get('rep')}:{row.get('time')}")
            for match in _CRITERION.finditer(check["detail"]):
                name = match.group(1) or match.group(3)
                failed = match.group(2) == "FAIL" or match.group(4) == ">"
                if match.group(2) == "n/a":
                    continue
                runs[(scenario, name, run)] = failed
                labels.setdefault((scenario, name), set()).add(path.stem)
    totals: dict[tuple[str, str], list[int]] = {}
    for (scenario, name, _), failed in runs.items():
        total = totals.setdefault((scenario, name), [0, 0])
        total[0] += failed
        total[1] += 1
    where = [f"{scenario}/{name}: {failed} of {count} runs (labels {', '.join(sorted(labels[(scenario, name)])[:3])})"
             for (scenario, name), (failed, count) in sorted(totals.items()) if count >= 2 and failed * 2 > count]
    return {"count": len(where), "of": len(totals), "where": where}


def rule_breaks(graph_dir: Path | None, results: Path | None) -> list[dict]:
    """The rules, most broken first (a rule that never broke is listed with a count of 0)."""
    found = {}
    if graph_dir is not None and Path(graph_dir).is_dir():
        graphs, _ = sources.load_graphs(Path(graph_dir))
        found.update(graph_rules(graphs))
    found["bench_check_fails_mostly"] = bench_rules(results) if results is not None and Path(results).is_dir() \
        else {"count": 0, "of": 0, "where": []}
    rows = [{"key": f"rule:{name}", "rule": name, "text": RULES[name], "count": value["count"], "of": value["of"],
             "where": value["where"][:WHERE], "more": max(0, len(value["where"]) - WHERE)}
            for name, value in found.items() if name in RULES]
    return sorted(rows, key=lambda item: (-item["count"], item["rule"]))


# --- 3. feedback -------------------------------------------------------------------------------------


def words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z][a-z']{2,}", text.lower()) if word not in STOP]


def _alike(first: set[str], second: set[str]) -> bool:
    shared = len(first & second)
    return shared >= SHARED and shared / min(len(first), len(second)) >= SIMILAR


def feedback_notes(files: list[Path]) -> list[dict]:
    """The valid notes of the given `feedback.jsonl` files, as `ledger._feedback_notes` reads them (id, time, text, agent,
    task); each file's own folder is its home."""
    found = []
    for path in files:
        found += ledger._feedback_notes(Path(path).parent) if Path(path).name == "feedback.jsonl" else []
    return found


def feedback_groups(notes: list[dict]) -> list[dict]:
    """The notes grouped by agent, then by topic (greedy, in time order: a note joins the group whose member it is most like,
    else starts one), the biggest first. Groups of one are kept: they are evidence too, only the weakest."""
    groups: list[dict] = []
    for note in sorted(notes, key=lambda item: item["time"]):
        bag = set(words(note["text"]))
        agent = note.get("agent") if isinstance(note.get("agent"), str) and AGENT_NAME.fullmatch(note["agent"]) else None
        best, score = None, 0.0
        for group in groups:
            if group["agent"] != agent:
                continue
            for member in group["_bags"]:
                if _alike(bag, member):
                    similarity = len(bag & member) / min(len(bag), len(member))
                    if similarity > score:
                        best, score = group, similarity
        if best is None:
            best = {"agent": agent, "ids": [], "notes": [], "_bags": []}
            groups.append(best)
        best["ids"].append(note["id"])
        best["notes"].append(note)
        best["_bags"].append(bag)
    out = []
    for number, group in enumerate(sorted(groups, key=lambda item: (-len(item["ids"]), item["notes"][0]["time"])), 1):
        counts = Counter(word for bag in group["_bags"] for word in bag)
        top = [word for word, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:TOP_WORDS]]
        example = group["notes"][0]["text"].strip().replace("\n", " ")[:EXAMPLE]
        out.append({"key": f"feedback:{group['agent'] or 'none'}:{group['ids'][0][:12]}", "agent": group["agent"], "size": len(group["ids"]),
                    "words": top, "ids": group["ids"], "example": example, "first": group["notes"][0]["time"],
                    "last": group["notes"][-1]["time"]})
    return out


def default_feedback_files() -> list[Path]:
    return sorted(ORCHESTRATION.glob("memory*/feedback.jsonl"))


# --- the report --------------------------------------------------------------------------------------


def collect(*, db: Path | None, graphs: Path | None, results: Path | None, feedback: list[Path], factor: float = FACTOR,
            min_sessions: int = MIN_SESSIONS, min_cost: float = MIN_COST) -> dict:
    """`{"cost": [...], "rules": [...], "feedback": [...], "notes": {...}}`; a source that cannot be read leaves its list
    empty and says why under `notes`."""
    report: dict = {"cost": [], "rules": [], "feedback": [], "notes": {}}
    try:
        if db is not None:
            report["cost"] = cost_outliers(db, factor=factor, min_sessions=min_sessions, min_cost=min_cost)
    except Exception as exc:  # noqa: BLE001 - one unreadable source must not hide the others
        report["notes"]["cost"] = f"not read: {exc}"
    try:
        report["rules"] = rule_breaks(graphs, results)
    except Exception as exc:  # noqa: BLE001
        report["notes"]["rules"] = f"not read: {exc}"
    try:
        notes = feedback_notes(feedback)
        report["feedback"] = feedback_groups(notes)
        report["notes"]["feedback_rows"] = len(notes)
    except Exception as exc:  # noqa: BLE001
        report["notes"]["feedback"] = f"not read: {exc}"
    return report


def _k(tokens: int) -> str:
    return f"{tokens / 1000:,.0f}k"


def text(report: dict, limit: int = 10) -> str:
    lines = []
    lines.append(f"COST OUTLIERS ({len(report['cost'])}): worker sessions far above their agent's median, dearest excess first")
    for item in report["cost"][:limit]:
        lines.append(f"  {item['session']}  {item['agent']:<14} {item['turns']:>4} turns  ${item['billed']:>7.2f}  "
                     f"${item['per_turn']:.3f}/turn ({item['times_per_turn']:.1f}x median)  total {item['times_total']:.1f}x median  "
                     f"context {_k(item['context'])}  [{', '.join(item['reasons'])}]")
    if report["notes"].get("cost"):
        lines.append("  " + report["notes"]["cost"])
    lines.append("")
    lines.append(f"RULES BROKEN ({sum(item['count'] for item in report['rules'])} times): what the graphs and bench results show")
    for item in report["rules"]:
        lines.append(f"  {item['rule']:<26} broke {item['count']} of {item['of']}  {item['text']}")
        for place in item["where"]:
            lines.append(f"      {place}")
        if item["more"]:
            lines.append(f"      ... and {item['more']} more")
    if report["notes"].get("rules"):
        lines.append("  " + report["notes"]["rules"])
    lines.append("")
    rows = report["notes"].get("feedback_rows", 0)
    lines.append(f"FEEDBACK GROUPS ({len(report['feedback'])} groups from {rows} notes): by agent, then by topic")
    for item in report["feedback"][:limit]:
        lines.append(f"  {item['size']:>3} notes  {item['agent'] or 'no agent':<14} words: {', '.join(item['words'])}")
        lines.append(f"      ids: {', '.join(note_id[:12] for note_id in item['ids'][:6])}" + (" ..." if len(item["ids"]) > 6 else ""))
        lines.append(f"      e.g. {item['example']}")
    if report["notes"].get("feedback"):
        lines.append("  " + report["notes"]["feedback"])
    return "\n".join(lines)


# --- to cases ----------------------------------------------------------------------------------------


def live_homes() -> list[Path]:
    from . import outputs
    homes = [path.parent.resolve() for path in ORCHESTRATION.glob("memory*/.lock")] + [path.resolve() for path in ORCHESTRATION.glob("memory*") if path.is_dir()]
    try:
        homes.append(outputs.edit_home().resolve())
    except Exception:  # noqa: BLE001
        pass
    return homes


def to_cases(report: dict, home: Path, *, top: int, only: str | None, pick: list[str], now: float | None = None) -> tuple[int, list[str]]:
    """Add the chosen items as evidence cases to `home` (a scratch home: a live one is refused); (cases added, keys chosen)."""
    if Path(home).resolve() in live_homes():
        raise ValueError(f"{home} is a live memory folder; --to-cases writes to a scratch home only")
    stamp = ledger._now(now)
    chosen, made = [], []
    for kind, name in (("cost", "cost"), ("rule", "rules"), ("feedback", "feedback")):
        if only not in (None, kind):
            continue
        items = report[name]
        picked = [item for item in items if item["key"] in pick] if pick else [item for item in items[:top] if kind != "rule" or item["count"]]
        for item in picked:
            chosen.append(item["key"])
            if kind == "cost":
                made.append(cases.evidence_case(
                    "cost", item["key"], item["agent"], f"worker session {item['session']} of {item['agent']} cost "
                    f"${item['billed']:.2f} over {item['turns']} turns", f"{item['times_per_turn']:.1f}x the agent's median cost per turn, "
                    f"{item['times_total']:.1f}x its median session; context up to {item['context']} tokens", now=stamp,
                    task="cost-evidence", node=item["session"], findings=[item["session"]]))
            elif kind == "rule":
                made.append(cases.evidence_case(
                    "rule", item["key"], "orchestrator", f"{item['rule']}: broke {item['count']} of {item['of']}", item["text"],
                    now=stamp, task="rule-evidence", node=item["rule"], findings=item["where"]))
            else:
                made.append(cases.evidence_case(
                    "feedback", item["key"], item["agent"] or "unknown", f"{item['size']} notes: {', '.join(item['words'])}",
                    item["example"], now=stamp, task="feedback-evidence", time=item["last"], findings=item["ids"]))
    return cases.add_evidence(home, made), chosen


# --- command line ------------------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=10, help="items shown in each list (default 10)")
    parser.add_argument("--db", type=Path, help="OpenCode session database (default: the live one)")
    parser.add_argument("--no-cost", action="store_true", help="leave out the cost list")
    parser.add_argument("--graphs", type=Path, help="task graph folder (default: the orchestration's own .opencode/graph)")
    parser.add_argument("--results", type=Path, help="bench results folder (default: bench/results)")
    parser.add_argument("--feedback", type=Path, action="append", help="a feedback.jsonl file (repeatable; default: every memory*/feedback.jsonl)")
    parser.add_argument("--factor", type=float, default=FACTOR, help=f"times the agent's median that makes a session an outlier (default {FACTOR:g})")
    parser.add_argument("--min-sessions", type=int, default=MIN_SESSIONS, help="worker sessions an agent needs to have a median")
    parser.add_argument("--min-cost", type=float, default=MIN_COST, help="dollars a session must cost to be listed")
    parser.add_argument("--to-cases", action="store_true", help="add the top items as cases (kind cost, rule, feedback) to --home, a scratch folder")
    parser.add_argument("--top", type=int, default=3, help="items of each list --to-cases takes (default 3)")
    parser.add_argument("--only", choices=("cost", "rule", "feedback"), help="--to-cases: one list only")
    parser.add_argument("--pick", action="append", default=[], metavar="KEY", help="--to-cases: this item (its key is in --json), repeatable")


def run(args: argparse.Namespace) -> int:
    graphs = args.graphs if args.graphs is not None else ORCHESTRATION / ".opencode" / "graph"
    results = args.results if args.results is not None else bench.RESULTS
    feedback = args.feedback if args.feedback else default_feedback_files()
    report = collect(db=None if args.no_cost else args.db or sources.DB_PATH, graphs=graphs, results=results, feedback=feedback,
                     factor=args.factor, min_sessions=args.min_sessions, min_cost=args.min_cost)
    if args.to_cases:
        if args.home is None:
            print("improve evidence: --to-cases needs --home, a scratch memory folder", file=sys.stderr)
            return 1
        try:
            added, chosen = to_cases(report, args.home, top=args.top, only=args.only, pick=args.pick)
        except (OSError, ValueError) as exc:
            print(f"improve evidence: {exc}", file=sys.stderr)
            return 1
        print(f"added {added} case(s) of {len(chosen)} chosen to {args.home}: {', '.join(chosen) or 'nothing chosen'}")
        return 0
    limit = max(1, args.limit)
    if args.json:
        print(json.dumps({**report, "cost": report["cost"][:limit], "rules": report["rules"][:limit],
                          "feedback": report["feedback"][:limit]}, indent=2, ensure_ascii=False, allow_nan=False))
    else:
        print(text(report, limit))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentgraph improve evidence", description=__doc__.split("\n\n")[0])
    add_arguments(parser)
    try:
        return run(parser.parse_args(argv))
    except (OSError, ValueError) as exc:
        print(f"improve evidence: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
