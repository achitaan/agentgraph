"""p4 check: hidden tests (optional) AND one behaviour read from the files and the OpenCode database (read-only).

    python3 p4check.py MODE [--tests] [--min-nodes N] [--files N --names FILE]

MODE: solo (no subagent session ran), graph (a task graph in .opencode/graph/ with >= N nodes, whose first write by the orchestrator
(a write/edit call, or a shell command that redirects into .opencode/graph) came before its first subagent dispatch), loop (at least one impl-* worker ran and every impl-* worker's final message carries
a `<loop>` block with a `Status:` line), artifact (an inventory file under .opencode/artifacts/ names >= N of the
names in FILE, and the orchestrator's final message names a path under .opencode/artifacts/).
The session tree is found by the work folder (session_v2.directory = cwd). P4_DB overrides the database path (the
handmade fixtures in verify_checks.py use it). Prints ONE last line of numbers/flags and never any message text.
Exit 0 only when every part holds."""
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys

DB = os.environ.get("P4_DB") or os.path.expanduser("~/.local/share/opencode/opencode.db")


def connect():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)


def tree():
    cwd = os.getcwd()
    dirs = {cwd, os.path.realpath(cwd)}
    with connect() as db:
        root = None
        for _ in range(1):
            marks = ",".join("?" * len(dirs))
            row = db.execute(f"SELECT id FROM session_v2 WHERE parent_id IS NULL AND directory IN ({marks}) "
                             "ORDER BY time_created DESC LIMIT 1", tuple(dirs)).fetchone()
            root = row[0] if row else None
        if not root:
            return None
        found, queue = [], [(root, None)]
        seen = set()
        while queue:
            sid, parent = queue.pop(0)
            if sid in seen:
                continue
            seen.add(sid)
            row = db.execute("SELECT agent, cost, time_created FROM session_v2 WHERE id=?", (sid,)).fetchone()
            if row:
                found.append({"id": sid, "parent": parent, "agent": row[0] or "unknown", "cost": float(row[1] or 0),
                              "created": int(row[2])})
            queue += [(r[0], sid) for r in db.execute("SELECT id FROM session_v2 WHERE parent_id=?", (sid,))]
        return {"root": root, "sessions": found}


def final_text(sid):
    """The text of the last assistant message of a session that has any text."""
    with connect() as db:
        rows = db.execute("SELECT data FROM session_message WHERE session_id=? AND type='assistant' ORDER BY seq DESC", (sid,))
        for (data,) in rows:
            try:
                parts = json.loads(data).get("content") or []
            except ValueError:
                continue
            text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
            if text.strip():
                return text
    return ""


def tests_ok():
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q"], capture_output=True)
    return done.returncode == 0


def workers(state):
    return [s for s in state["sessions"] if s["parent"] is not None]


def check_solo(state, args):
    n = len(workers(state))
    return n == 0, f"subagent sessions {n}"


def graph_files():
    out = []
    for path in sorted(glob.glob(".opencode/graph/*.json")):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        nodes = [n for n in data.get("nodes", []) if isinstance(n, dict)] if isinstance(data, dict) else []
        out.append((path, len(nodes)))
    return out


WRITES = re.compile(r">|\btee\b|json\.dump|write_text|open\(|\bgraph (init|new|declare)\b")


def tool_parts(sid):
    """(seq, index, name, created ms, input dict) for each tool call of the session, in order. Input text is only
    looked at in code, never printed. (The graph file's own birth time is useless: the toolkit rewrites the file
    on every update, so the file is born again each time.)"""
    out = []
    with connect() as db:
        for seq, data in db.execute("SELECT seq, data FROM session_message WHERE session_id=? AND type='assistant' ORDER BY seq", (sid,)):
            try:
                parts = json.loads(data).get("content") or []
            except ValueError:
                continue
            for i, p in enumerate(parts):
                if isinstance(p, dict) and p.get("type") == "tool":
                    state = p.get("state") if isinstance(p.get("state"), dict) else {}
                    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
                    created = (p.get("time") or {}).get("created")
                    out.append((seq, i, p.get("name"), created if isinstance(created, int) else None, inp))
    return out


def declares_graph(name, inp):
    blob = json.dumps(inp)
    if ".opencode/graph" not in blob:
        return False
    if name in ("write", "edit", "apply_patch", "patch"):
        return True
    return name == "shell" and bool(WRITES.search(str(inp.get("command", ""))))


def check_graph(state, args):
    graphs = [g for g in graph_files() if g[1] >= args["min_nodes"]]
    if not graphs:
        return False, f"no task graph with >= {args['min_nodes']} nodes ({len(graph_files())} graph files)"
    parts = tool_parts(state["root"])
    declared = min((p[3] for p in parts if p[3] is not None and declares_graph(p[2], p[4])), default=None)
    dispatches = [p[3] for p in parts if p[2] == "subagent" and p[3] is not None]
    dispatches += [s["created"] for s in workers(state)]
    first = min(dispatches, default=None)
    nodes = max(g[1] for g in graphs)
    if declared is None:
        return False, f"graph with {nodes} nodes, but no call that wrote it was found"
    if first is not None and declared >= first:
        return False, f"graph with {nodes} nodes written {int(declared - first)} ms after the first dispatch"
    return True, f"graph with {nodes} nodes" + (f" written {int(first - declared)} ms before the first dispatch" if first else ", no dispatch")


def check_loop(state, args):
    impl = [s for s in workers(state) if s["agent"].startswith("impl-")]
    if not impl:
        return False, "no impl-* worker ran"
    good = 0
    for s in impl:
        text = final_text(s["id"])
        if re.search(r"<loop>", text) and re.search(r"Status:\s*(PASS|BLOCKED)", text):
            good += 1
    return good == len(impl), f"{good} of {len(impl)} worker returns carry a loop block"


def check_artifact(state, args):
    names = [ln.strip() for ln in open(args["names"], encoding="utf-8") if ln.strip()]
    best = 0
    for path in glob.glob(".opencode/artifacts/**/*", recursive=True):
        if os.path.isfile(path):
            try:
                body = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            best = max(best, sum(1 for n in names if re.search(r"\b" + re.escape(n) + r"\b", body)))
    mentions = ".opencode/artifacts/" in final_text(state["root"])
    return best >= args["files"] and mentions, f"artifact names {best} of {len(names)}; final message names an artifact path {'yes' if mentions else 'no'}"


def check_graph_if_workers(state, args):
    n = len(workers(state))
    if n == 0:
        return True, "no worker session, so no graph is needed"
    ok, detail = check_graph(state, args)
    return ok, f"subagent sessions {n}; {detail}"


MODES = {"solo": check_solo, "graph": check_graph, "graph-if-workers": check_graph_if_workers, "loop": check_loop,
         "artifact": check_artifact}


def main(argv):
    mode, args = argv[0], {"min_nodes": 2, "files": 24, "names": "p4names.txt"}
    want_tests = "--tests" in argv
    for flag, key in (("--min-nodes", "min_nodes"), ("--files", "files"), ("--names", "names")):
        if flag in argv:
            value = argv[argv.index(flag) + 1]
            args[key] = value if key == "names" else int(value)
    t_ok = tests_ok() if want_tests else True
    state = tree()
    if state is None:
        print(f"tests {'ok' if t_ok else 'FAIL'}; behaviour {mode} FAIL (no session found for this folder)")
        return 1
    ok, detail = MODES[mode](state, args)
    print(f"tests {('ok' if t_ok else 'FAIL') if want_tests else 'n/a'}; {mode} {'ok' if ok else 'FAIL'} ({detail})")
    return 0 if ok and t_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
