"""p2-flow check: the hidden tests pass AND the declared task graph applies the drawn flow
`impl-openai -> verifier` (every implement/design/research/decision node run by impl-openai has a node run by
verifier that depends on it, or a gate of verifier's whose feedback_to names it). Writes p2check.json in the work
folder and prints one last line: `tests ok|FAIL; flow applied yes|no (...)`. Exit 0 only when both hold."""
import glob
import json
import subprocess
import sys

START_TYPES = {"implement", "design", "research", "decision"}


def flow_state():
    graphs = sorted(glob.glob(".opencode/graph/*.json"))
    if not graphs:
        return {"applied": False, "why": "no graph file", "graphs": 0, "impl_nodes": 0, "followed": 0, "verifier_nodes": 0}
    impl = followed = verifier_nodes = 0
    missing = []
    for path in graphs:
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        nodes = [n for n in data.get("nodes", []) if isinstance(n, dict)]
        verifiers = [n for n in nodes if n.get("agent") == "verifier"]
        verifier_nodes += len(verifiers)
        for n in nodes:
            if n.get("agent") != "impl-openai" or n.get("type") not in START_TYPES:
                continue
            impl += 1
            after = [v for v in verifiers if n.get("id") in (v.get("depends_on") or [])
                     or (v.get("type") == "gate" and n.get("id") in (v.get("feedback_to") or []))]
            if after:
                followed += 1
            else:
                missing.append(f"{data.get('task')}:{n.get('id')}")
    ok = impl > 0 and followed == impl
    why = "ok" if ok else ("no impl-openai node" if impl == 0 else "not followed: " + ",".join(missing))
    return {"applied": ok, "why": why, "graphs": len(graphs), "impl_nodes": impl, "followed": followed,
            "verifier_nodes": verifier_nodes}


tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-q"],
                       capture_output=True, text=True)
state = flow_state()
state["tests_ok"] = tests.returncode == 0
json.dump(state, open("p2check.json", "w"))
print(f"tests {'ok' if state['tests_ok'] else 'FAIL'}; flow applied {'yes' if state['applied'] else 'no'} "
      f"({state['why']}; impl {state['impl_nodes']}, followed {state['followed']}, verifier nodes {state['verifier_nodes']})")
sys.exit(0 if state["tests_ok"] and state["applied"] else 1)
