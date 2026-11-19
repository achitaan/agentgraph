#!/usr/bin/env python3
"""Draw the agent roster as a graph.

Reads the live config, so the diagram always shows the models and effort
levels that are actually configured. Writes agent-graph.md (Mermaid) and
agent-graph.html (open in a browser) next to this script.

Usage: python3 ~/.config/opencode/orchestration/build_agent_graph.py [--open]
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE.parent
PLUGIN_CONFIG = CONFIG_DIR / "oh-my-opencode-slim.json"
REVIEWER_FILE = CONFIG_DIR / "agents" / "reviewer.md"

# Standing pool sizes come from the routing prompt, not from the config file.
POOL = {"impl-openai": 3, "impl-claude": 2}


def pretty_model(model_id: str) -> str:
    """'openai/gpt-6.1-sol-ultrafast' -> 'GPT-6.1 Sol Ultrafast'."""
    name = model_id.split("/", 1)[-1]
    parts = name.split("-")
    out = []
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "gpt" and i + 1 < len(parts):
            out.append(f"GPT-{parts[i + 1]}")
            i += 2
            continue
        if part.isdigit():
            digits = [part]
            while i + 1 < len(parts) and parts[i + 1].isdigit():
                i += 1
                digits.append(parts[i])
            out.append(".".join(digits))
        else:
            out.append(part.capitalize())
        i += 1
    return " ".join(out)


def load_agents() -> dict:
    """Return {agent name: (model label, effort)} for every configured agent."""
    config = json.loads(PLUGIN_CONFIG.read_text())
    preset = config["presets"][config["preset"]]
    agents = {
        name: (pretty_model(spec["model"]), spec.get("variant", "default"))
        for name, spec in preset.items()
    }

    council = config.get("council", {})
    seats = council.get("presets", {}).get(council.get("default_preset", "default"), {})
    for seat, spec in seats.items():
        agents[f"councillor-{seat}"] = (pretty_model(spec["model"]), spec.get("variant", "default"))

    if REVIEWER_FILE.exists():
        text = REVIEWER_FILE.read_text()
        model = re.search(r"^model:\s*(\S+)", text, re.M)
        variant = re.search(r"^variant:\s*(\S+)", text, re.M)
        if model:
            agents["reviewer"] = (pretty_model(model.group(1)), variant.group(1) if variant else "default")
    return agents


def node_id(name: str) -> str:
    return name.replace("-", "_")


def node(agents: dict, name: str) -> str:
    model, effort = agents[name]
    count = POOL.get(name)
    title = f"{name} ×{count}" if count else name
    return f'{node_id(name)}["<b>{title}</b><br/>{model} · {effort}"]'


def build_mermaid(agents: dict) -> str:
    have = lambda name: name in agents  # noqa: E731
    seats = [name for name in agents if name.startswith("councillor-")]
    implementers = [n for n in ("impl-openai", "impl-claude", "impl-hard") if have(n)]
    lines = ["flowchart TD", "  user([You])", f"  {node(agents, 'orchestrator')}", "  user --> orchestrator", ""]

    def subgraph(key: str, title: str, names: list, extra: list = ()):  # noqa: B006
        names = [n for n in names if have(n)]
        if not names and not extra:
            return
        lines.append(f'  subgraph {key}["{title}"]')
        lines.extend(f"    {node(agents, n)}" for n in names)
        lines.extend(f"    {line}" for line in extra)
        lines.append("  end")

    subgraph("understand", "Understand (read-only)", ["explorer", "researcher"])
    subgraph("decide", "Decide (read-only)", ["oracle", "oracle-lite"] + seats + (["council"] if seats else []))
    subgraph("build", "Build (every node runs a loop until its evaluation passes)", implementers + ["designer"])
    subgraph(
        "verify",
        "Verify",
        ["reviewer"],
        ['evaluate{{"<b>orchestrator evaluation</b><br/>rerun each check, join, full suite"}}'],
    )
    lines += ["  done([Report to you])", ""]

    def edge(src: str, dst: str, label: str = "", dashed: bool = False):
        if (src in agents or src in ("build", "evaluate", "user")) and (dst in agents or dst in ("evaluate", "done")):
            arrow = "-.->" if dashed else "-->"
            text = f"|{label}|" if label else ""
            lines.append(f"  {node_id(src)} {arrow}{text} {node_id(dst)}")

    edge("orchestrator", "explorer", "search, docs")
    edge("orchestrator", "researcher", "research question")
    edge("orchestrator", "oracle", "hard decision")
    edge("orchestrator", "oracle-lite", "second opinion")
    for seat in seats:
        edge("orchestrator", seat, "council question" if seat == seats[0] else "")
        edge(seat, "council")
    for name in implementers:
        edge("orchestrator", name, "loop item" if name == implementers[0] else "")
    edge("orchestrator", "designer", "UI work")

    edge("impl-openai", "impl-claude", "same failure twice", dashed=True)
    edge("impl-claude", "impl-hard", dashed=True)
    edge("impl-hard", "oracle", "diagnosis", dashed=True)

    edge("build", "evaluate", "loop report")
    edge("evaluate", "reviewer", "risky change")
    edge("reviewer", "evaluate", "findings")
    edge("evaluate", "done", "all nodes passed")

    lines += [
        "",
        "  classDef hub fill:#1f2937,stroke:#111827,color:#ffffff",
        "  classDef read fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e",
        "  classDef write fill:#dcfce7,stroke:#16a34a,color:#14532d",
        "  classDef gate fill:#fef3c7,stroke:#d97706,color:#78350f",
        "  class orchestrator hub",
    ]
    read = [n for n in ["explorer", "researcher", "oracle", "oracle-lite", "council"] + seats if have(n)]
    write = [n for n in implementers + ["designer"] if have(n)]
    if read:
        lines.append(f"  class {','.join(node_id(n) for n in read)} read")
    if write:
        lines.append(f"  class {','.join(node_id(n) for n in write)} write")
    lines.append(f"  class {','.join(['evaluate'] + (['reviewer'] if have('reviewer') else []))} gate")
    return "\n".join(lines) + "\n"


NOTES = """- Every result returns to the orchestrator. Workers never talk to each other.
- Solid arrows are handoffs. Dashed arrows are escalation routes, which the orchestrator takes when a node fails the same way twice.
- Blue nodes are read-only, green nodes write files, amber nodes are gates.
- The pool sizes (×2, ×1, ×2) are the standing pool; the orchestrator can start more of any type.
"""

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Agent graph</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; color: #111827; }}
  h1 {{ font-size: 1.3rem; }}
  ul {{ color: #374151; line-height: 1.6; }}
  .mermaid {{ margin-top: 1.5rem; }}
</style>
</head>
<body>
<h1>Agent graph</h1>
<ul>
{notes}
</ul>
<pre class="mermaid">
{mermaid}
</pre>
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
  mermaid.initialize({{ startOnLoad: true, securityLevel: "loose", flowchart: {{ htmlLabels: true }} }});
</script>
</body>
</html>
"""


def main() -> None:
    agents = load_agents()
    mermaid = build_mermaid(agents)
    (HERE / "agent-graph.md").write_text(f"# Agent graph\n\n{NOTES}\n```mermaid\n{mermaid}```\n")
    notes_html = "\n".join(f"  <li>{line[2:]}</li>" for line in NOTES.strip().splitlines())
    html_path = HERE / "agent-graph.html"
    html_path.write_text(HTML.format(notes=notes_html, mermaid=mermaid))
    print(f"wrote {HERE / 'agent-graph.md'}")
    print(f"wrote {html_path}")
    if "--open" in sys.argv:
        subprocess.run(["open", str(html_path)], check=False)


if __name__ == "__main__":
    main()
