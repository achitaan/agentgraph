"""Shared data contract for agentgraph.

`sources.py` builds a Snapshot from the config, the graph files and the
OpenCode database. `view.py` draws a Snapshot. Neither imports the other;
both import only this module. Standard library only.
"""
from __future__ import annotations

from collections import Counter, deque
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from posixpath import abspath, join, normpath

# --- vocabulary ---------------------------------------------------------

NODE_TYPES = ("research", "decision", "implement", "design", "check", "gate", "join", "human")
PATTERNS = ("chain", "route", "parallel", "vote", "orchestrate", "evaluate", "synthesize")
# "returned" = the worker reports PASS and the orchestrator has not evaluated it yet.
NODE_STATUSES = ("pending", "running", "returned", "passed", "failed", "blocked")
VERDICTS = ("confirmed", "refuted", "unverifiable")  # result of a gate node
FAILURE_KINDS = ("code", "evaluation", "environment", "input")
GRAPH_STATUSES = ("running", "passed", "blocked")
EVENT_NAMES = (
    "declared", "dispatched", "attempt", "returned", "evaluated", "retried", "stalled", "escalated",
    "blocked", "unblocked", "joined", "changed", "invalidated", "amended", "discovered", "completed",
)
_SYNTHETIC_ROOT = PurePosixPath("/__project__/root")
MAX_NODES = 1000
MAX_EVENTS = 2000
MAX_OBJECTIVES = 200
GRAPH_LIMITS = {"nodes": MAX_NODES, "events": MAX_EVENTS, "objectives": MAX_OBJECTIVES}
MAX_SCOPE_CONFLICTS = 100
MAX_FEEDBACK_ISSUES = 100
MAX_FEEDBACK_STEPS = 100_000  # Fallback traversal budget for cyclic graphs.
# Every list inside one node, and an objective's `nodes`, keeps at most this
# many entries, so no step can multiply two file-controlled lists unboundedly.
MAX_NODE_LIST = 200
NODE_LIST_FIELDS = ("depends_on", "scope", "changed_paths", "feedback_to", "artifacts", "inputs", "tiers")

# Roster groups, in display order. The orchestrator is the hub.
GROUPS = ("hub", "understand", "decide", "council", "build", "verify")
GROUP_TITLES = {
    "hub": "Orchestrator",
    "understand": "Understand",
    "decide": "Decide",
    "council": "Council",
    "build": "Build",
    "verify": "Verify",
}

# Which group each known agent belongs to. Unknown agents go to "build".
AGENT_GROUPS = {
    "orchestrator": "hub",
    "explorer": "understand",
    "researcher": "understand",
    "oracle": "decide",
    "oracle-lite": "decide",
    "council": "council",
    "impl-openai": "build",
    "impl-claude": "build",
    "impl-hard": "build",
    "designer": "build",
    "verifier": "verify",
    "reviewer": "verify",
}

# Standing pool sizes (from the routing prompt, not the config file).
POOL = {"impl-openai": 3, "impl-claude": 2}

# Agents that check every other agent's work; a view links them to all of them.
CHECKERS = frozenset({"verifier"})

# Escalation routes the orchestrator takes when a node fails the same way twice.
# A route is shown only when both of its agents are present.
ESCALATION = (
    ("impl-openai", "impl-claude"),
    ("impl-claude", "impl-hard"),
    ("impl-hard", "oracle"),
)


def group_of(agent: str) -> str:
    if agent.startswith("councillor-"):
        return "council"
    return AGENT_GROUPS.get(agent, "build")


def team_of(name: str) -> str:
    """Every agent belongs to the one main team; the field stays in the contract."""
    return "main"


def parent_of(name: str, present_names: Collection[str]) -> str:
    parent = "council" if name.startswith("councillor-") else ""
    return parent if parent in present_names else ""


# --- records ------------------------------------------------------------


@dataclass
class Agent:
    """One agent type in the roster (a node of the agent graph)."""

    name: str
    model: str  # display name, e.g. "GPT-6.1 Sol Ultrafast"
    effort: str  # e.g. "high"
    group: str  # one of GROUPS
    pool: int | None = None  # standing pool size, if any
    skills: list[str] = field(default_factory=list)  # skills it may load; ["*"] means all


@dataclass
class Session:
    """One OpenCode session: the orchestrator or a sub-agent run."""

    id: str
    parent_id: str | None
    agent: str
    title: str
    model: str  # display name
    effort: str
    busy: bool
    created: float  # unix seconds
    updated: float  # unix seconds
    cost: float = 0.0  # USD
    tokens_in: int = 0
    tokens_out: int = 0
    directory: str = ""
    context: int | None = None  # tokens in the context window at the last model call


@dataclass
class Artifact:
    """One revision of a file a node hands to downstream consumers."""

    id: str
    path: str
    revision: int


@dataclass
class ArtifactInput:
    """The producer and artifact revision read by a node."""

    from_node: str
    artifact: str
    revision: int


@dataclass
class Node:
    """One node of a task graph."""

    id: str
    type: str  # one of NODE_TYPES
    agent: str  # roster agent name, "orchestrator" or "user"
    goal: str
    status: str = "pending"  # one of NODE_STATUSES
    depends_on: list[str] = field(default_factory=list)
    scope: list[str] = field(default_factory=list)  # write scope; empty = read-only
    done_when: str = ""  # the evaluation that passes this node
    attempts: int = 0  # work/check passes used so far
    tiers: list[str] = field(default_factory=list)  # agents tried, in order
    evidence: str = ""  # one or two lines deciding the last result
    session: str | None = None  # OpenCode session id of the current run
    started: float | None = None  # unix seconds
    finished: float | None = None  # unix seconds
    baseline: str = ""  # evaluation result before any change was made
    outputs: str = ""  # what downstream nodes need from this node
    changed_paths: list[str] = field(default_factory=list)  # files actually changed
    failure_kind: str = ""  # one of FAILURE_KINDS, for the last failure
    stalled: bool = False  # attempts are repeating without new evidence
    verdict: str = ""  # one of VERDICTS, for gate nodes
    pattern: str = ""  # one of PATTERNS when declared
    feedback_to: list[str] = field(default_factory=list)  # rework edges, not dependencies
    artifacts: list[Artifact] = field(default_factory=list)
    inputs: list[ArtifactInput] = field(default_factory=list)


@dataclass
class Objective:
    """One requirement of the task, frozen when the graph is declared."""

    id: str
    text: str
    nodes: list[str] = field(default_factory=list)  # node ids that deliver it


@dataclass
class Event:
    """One line of a task graph's append-only log."""

    time: float  # unix seconds
    node: str  # node id, or "" for graph-level events
    event: str  # declared, dispatched, returned, evaluated, retried, escalated,
    #             blocked, unblocked, joined, changed, completed, attempt,
    #             stalled, invalidated, amended, discovered
    note: str = ""


@dataclass
class TaskGraph:
    path: str  # file the graph was read from
    task: str
    goal: str
    status: str  # one of GRAPH_STATUSES
    created: float
    updated: float
    nodes: list[Node] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    session: str | None = None  # orchestrator session that owns the graph
    revision: int = 1  # bumped on every amendment
    objectives: list[Objective] = field(default_factory=list)
    amendments: list[str] = field(default_factory=list)  # "what changed: why"
    lessons: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    """Everything the view needs for one frame."""

    now: float  # unix seconds
    roster: list[Agent] = field(default_factory=list)
    root: Session | None = None  # the orchestrator session being watched
    sessions: list[Session] = field(default_factory=list)  # root's descendants
    graphs: list[TaskGraph] = field(default_factory=list)  # newest first
    warnings: list[str] = field(default_factory=list)  # data that could not be read
    disabled_agents: list[str] = field(default_factory=list)  # config's disabled_agents


# --- helpers shared by both sides ---------------------------------------


def waves(graph: TaskGraph) -> list[list[Node]]:
    """Group nodes into dependency layers: wave 0 has no dependencies, wave n
    depends only on earlier waves. Unknown dependency ids are ignored. Nodes
    caught in a cycle are returned together as a final wave.

    Kahn's algorithm, O(N + E) apart from sorting each layer into file order.
    When ids repeat, the first layer that places an id also retires every
    other node with that id, as a layer-by-layer scan would.
    """
    nodes = graph.nodes
    known = {node.id for node in nodes}
    waiting: list[int] = []
    dependents: dict[str, list[int]] = {}
    ready: list[int] = []
    for i, node in enumerate(nodes):
        deps = {dep for dep in node.depends_on if dep in known}
        waiting.append(len(deps))
        for dep in deps:
            dependents.setdefault(dep, []).append(i)
        if not deps:
            ready.append(i)
    placed_ids: set = set()
    done = [False] * len(nodes)
    layers: list[list[Node]] = []
    while ready:
        layer = sorted(ready)
        layers.append([nodes[i] for i in layer])
        new_ids = []
        for i in layer:
            done[i] = True
            if nodes[i].id not in placed_ids:
                placed_ids.add(nodes[i].id)
                new_ids.append(nodes[i].id)
        ready = []
        for node_id in new_ids:
            for j in dependents.get(node_id, ()):
                waiting[j] -= 1
                if not waiting[j] and not done[j] and nodes[j].id not in placed_ids:
                    ready.append(j)
    remaining = [node for i, node in enumerate(nodes) if not done[i] and node.id not in placed_ids]
    if remaining:
        layers.append(remaining)
    return layers


def node_busy(node: Node, busy_ids: Collection[str]) -> bool:
    """A node is busy when it is running and its session is busy now. A
    session is often reused for several nodes, so a busy session alone does
    not make a finished node busy."""
    return node.status == "running" and node.session is not None and node.session in busy_ids


def running_by_agent(snapshot: Snapshot) -> dict[str, int]:
    """How many sessions of each agent type are busy right now."""
    counts: dict[str, int] = {}
    for session in snapshot.sessions:
        if session.busy:
            counts[session.agent] = counts.get(session.agent, 0) + 1
    if snapshot.root and snapshot.root.busy:
        counts[snapshot.root.agent] = counts.get(snapshot.root.agent, 0) + 1
    return counts


def objective_coverage(graph: TaskGraph) -> tuple[int, int]:
    """(objectives fully delivered, total). An objective is delivered when it
    names at least one node and every node it names has passed."""
    status = {node.id: node.status for node in graph.nodes}
    done = sum(
        1
        for objective in graph.objectives
        if objective.nodes and all(status.get(node_id) == "passed" for node_id in objective.nodes)
    )
    return done, len(graph.objectives)


def feedback_edges(graph: TaskGraph) -> list[tuple[str, str]]:
    """Return rework edges with known endpoints in declaration order."""
    known = {node.id for node in graph.nodes}
    return [(node.id, target) for node in graph.nodes for target in dict.fromkeys(node.feedback_to)
            if target in known]


def _positive_revision(value: object) -> bool:
    return type(value) is int and value > 0


def stale_inputs(graph: TaskGraph) -> list[tuple[str, str, str, int, int]]:
    """List inputs older than their producer's declared revision, in node order."""
    revisions = {(node.id, artifact.id): artifact.revision
                 for node in graph.nodes for artifact in node.artifacts
                 if isinstance(artifact.id, str) and _positive_revision(artifact.revision)}
    stale = []
    for node in graph.nodes:
        for entry in node.inputs:
            if not (isinstance(entry.from_node, str) and isinstance(entry.artifact, str)
                    and _positive_revision(entry.revision)):
                continue
            current = revisions.get((entry.from_node, entry.artifact))
            if current is not None and entry.revision < current:
                stale.append((node.id, entry.from_node, entry.artifact, entry.revision, current))
    return stale


def _input_artifacts(nodes: list[Node]) -> dict[tuple[int, str], set[str]]:
    """Index artifact IDs by consuming node object and producing node ID."""
    artifacts = {}
    for node in nodes:
        for entry in node.inputs:
            artifacts.setdefault((id(node), entry.from_node), set()).add(entry.artifact)
    return artifacts


def agent_flows(graph: TaskGraph, busy_ids: Collection[str] = frozenset()) -> list[dict]:
    """Aggregate dependency and feedback edges into deterministic agent flows.

    A data flow is active while one of its consuming nodes is busy (status
    `running` and its session in `busy_ids`, see `node_busy`). A feedback flow is active while its source has
    refuted or failed and the target node is running again. `returned` never
    counts: the worker has stopped and is waiting for evaluation.
    """
    by_id = {node.id: node for node in graph.nodes}
    artifacts = _input_artifacts(graph.nodes)
    flows = {}

    def add(source: Node, target: Node, kind: str) -> None:
        if (not source.agent or not target.agent or "user" in (source.agent, target.agent)
                or source.agent == target.agent):
            return
        key = (source.agent, target.agent, kind)
        flow = flows.setdefault(key, {"pairs": set(), "artifacts": set(), "active": False})
        flow["pairs"].add((source.id, target.id))
        flow["artifacts"].update(artifacts.get((id(target), source.id), ()))
        if kind == "data":
            flow["active"] |= node_busy(target, busy_ids)
        else:
            flow["active"] |= ((source.verdict == "refuted" or source.status == "failed")
                               and target.status == "running")

    for node in graph.nodes:
        for dep in dict.fromkeys(node.depends_on):
            if dep in by_id:
                add(by_id[dep], node, "data")
        for target in dict.fromkeys(node.feedback_to):
            if target in by_id:
                add(node, by_id[target], "feedback")
    return [{"from": source, "to": target, "kind": kind,
             "pairs": [list(pair) for pair in sorted(flow["pairs"])],
             "artifacts": sorted(flow["artifacts"]), "active": flow["active"]}
             for (source, target, kind), flow in sorted(flows.items())]


def agent_loops(graph: TaskGraph, busy_ids: Collection[str] = frozenset()) -> list[dict]:
    """Aggregate feedback edges into review loops from worker to evaluator and back."""
    by_id = {node.id: node for node in graph.nodes}
    artifacts = _input_artifacts(graph.nodes)
    # "Busy or running": a busy node (node_busy) is always running, so the
    # rule is the node's status alone; a reused busy session never counts.
    running = {id(node) for node in graph.nodes if node.status == "running" or node_busy(node, busy_ids)}
    loops = {}
    for evaluator in graph.nodes:
        if not evaluator.agent or evaluator.agent == "user":
            continue
        for target in dict.fromkeys(evaluator.feedback_to):
            worker = by_id.get(target)
            if worker is None or not worker.agent or worker.agent in ("user", evaluator.agent):
                continue
            loop = loops.setdefault((worker.agent, evaluator.agent), {
                "pairs": set(), "artifacts": set(), "rounds": 1, "working": False,
                "checking": False, "sent_back": False, "closed": True,
            })
            loop["pairs"].add((worker.id, evaluator.id))
            loop["artifacts"].update(artifacts.get((id(worker), evaluator.id), ()))
            loop["rounds"] = max(loop["rounds"], worker.attempts)
            loop["working"] |= id(worker) in running
            loop["checking"] |= id(evaluator) in running
            loop["sent_back"] |= evaluator.verdict == "refuted" or evaluator.status == "failed"
            loop["closed"] &= evaluator.status == "passed"

    result = []
    for (worker, evaluator), loop in loops.items():
        state = next((state for state in ("working", "checking", "sent_back", "closed") if loop[state]), "idle")
        result.append({"id": f"{worker}~{evaluator}", "worker": worker, "evaluator": evaluator,
                       "pairs": [list(pair) for pair in sorted(loop["pairs"])], "rounds": loop["rounds"],
                       "state": state, "active": state in ("working", "checking", "sent_back"),
                       "artifacts": sorted(loop["artifacts"])})
    return sorted(result, key=lambda loop: (loop["id"], loop["worker"], loop["evaluator"]))


WORK_NODE_TYPES = ("implement", "design")


def agent_work_loops(graph: TaskGraph, busy_ids: Collection[str] = frozenset()) -> list[dict]:
    """One work/check loop per agent that has implement or design nodes.

    `state` is the first match of: working (a node busy or running), waiting
    (a node returned), stuck (a node failed or blocked), closed (every node
    passed), else idle. One pass over the nodes, then a sort by agent.
    """
    loops: dict[str, dict] = {}
    for node in graph.nodes:
        if node.type not in WORK_NODE_TYPES or not node.agent or node.agent == "user":
            continue
        loop = loops.setdefault(node.agent, {"nodes": set(), "rounds": 0, "working": False,
                                             "waiting": False, "stuck": False, "closed": True})
        loop["nodes"].add(node.id)
        loop["rounds"] = max(loop["rounds"], node.attempts if type(node.attempts) is int else 0)
        loop["working"] |= node.status == "running" or node_busy(node, busy_ids)
        loop["waiting"] |= node.status == "returned"
        loop["stuck"] |= node.status in ("failed", "blocked")
        loop["closed"] &= node.status == "passed"
    result = []
    for agent in sorted(loops):
        loop = loops[agent]
        state = next((state for state in ("working", "waiting", "stuck", "closed") if loop[state]), "idle")
        result.append({"agent": agent, "nodes": sorted(loop["nodes"], key=str), "rounds": loop["rounds"],
                       "state": state, "active": state == "working"})
    return result


def _feedback_issues(nodes: list[Node], by_id: dict[str, Node]) -> list[str]:
    evaluators = [node for node in nodes if node.feedback_to]
    if not evaluators:
        return []
    children: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    parents: dict[str, set[str]] = {node_id: set() for node_id in by_id}
    for node in nodes:
        for dep in node.depends_on:
            if dep in by_id:
                children[dep].add(node.id)
                parents[node.id].add(dep)

    # Compute reachability once for the acyclic portion, rather than walking
    # the same downstream chain separately for every evaluator.
    bits = {node_id: 1 << i for i, node_id in enumerate(by_id)}
    descendants = dict.fromkeys(by_id, 0)
    remaining = {node_id: len(targets) for node_id, targets in children.items()}
    ready = deque(node_id for node_id, count in remaining.items() if not count)
    resolved = set()
    while ready:
        node_id = ready.popleft()
        resolved.add(node_id)
        for parent in parents[node_id]:
            descendants[parent] |= bits[node_id] | descendants[node_id]
            remaining[parent] -= 1
            if not remaining[parent]:
                ready.append(parent)

    issues = []
    steps = 0
    for node in evaluators:
        downstream = descendants[node.id]
        if node.id not in resolved:
            # Cycles cannot use the bottom-up masks. Bound the fallback work;
            # an incomplete check is a problem, never a claim of validity.
            targets = {target for target in node.feedback_to if target in by_id and target != node.id}
            seen = {node.id}
            stack = list(children[node.id])
            while stack and targets:
                if steps >= MAX_FEEDBACK_STEPS:
                    return issues + ["feedback reachability check limit reached; graph requires simplification"]
                steps += 1
                child = stack.pop()
                if child not in seen:
                    seen.add(child)
                    downstream |= bits[child]
                    targets.discard(child)
                    stack.extend(children[child])
        for target in node.feedback_to:
            issue = ""
            if target == node.id:
                issue = f"{node.id} sends feedback to itself"
            elif target not in by_id:
                issue = f"{node.id} sends feedback to unknown node {target}"
            elif downstream & bits[target]:
                issue = f"{node.id} sends feedback to downstream node {target}"
            if issue:
                if len(issues) == MAX_FEEDBACK_ISSUES:
                    return issues + ["... and more feedback problems (further checks skipped)"]
                issues.append(issue)
    return issues


def _repeated_scope_chains(layers: list[list[Node]]) -> list[list[str]]:
    # Track the longest same-scope worker path ending at each node. Emit a
    # representative path at each end, not every sliding triple or fork path.
    # Intern each distinct scope set once, so comparing two nodes is one
    # integer comparison rather than a set comparison per dependency.
    interned: dict[frozenset, int] = {}
    work = {node.id: interned.setdefault(frozenset(node.scope), len(interned))
            for layer in layers for node in layer if node.type in ("implement", "design")}
    paths: dict[str, tuple[int, str | None]] = {}
    extended: set[str] = set()
    for layer in layers:
        current = {}
        for node in layer:
            if node.id not in work:
                continue
            candidates = [dep for dep in dict.fromkeys(node.depends_on)
                          if dep in paths and work[node.id] == work[dep]]
            extended.update(candidates)
            if candidates:
                parent = max(candidates, key=lambda dep: paths[dep][0])
                current[node.id] = (1 + paths[parent][0], parent)
            else:
                current[node.id] = (1, None)
        # A final wave can contain a cycle; only extend paths from earlier waves.
        paths.update(current)

    chains = []
    for end, (length, _) in paths.items():
        if length >= 3 and end not in extended:
            chain = []
            step = end
            for _ in range(length):
                chain.append(step)
                step = paths[step][1]
            chains.append(list(reversed(chain)))
    return chains


def shape_advice(graph: TaskGraph, layers: list[list[Node]] | None = None) -> list[str]:
    """Suggest ways to expose parallel work and keep rework on its original nodes.

    `layers` may pass in `waves(graph)` when the caller has computed it already.
    """
    advice = []
    try:
        if layers is None:
            layers = waves(graph)
        if len(graph.nodes) >= 4 and all(len(layer) == 1 for layer in layers):
            ids = ", ".join(layer[0].id for layer in layers)
            advice.append(f"Nodes {ids} form a single chain; what could run at the same time?")
        for node in graph.nodes:
            if (node.type == "gate" and node.status in ("passed", "failed")
                    and node.attempts > 1 and not node.feedback_to):
                advice.append(f"Gate {node.id} used multiple attempts; record its rework as feedback edges "
                              "to the nodes it sent back.")
        for chain in _repeated_scope_chains(layers):
            ids = ", ".join(chain)
            advice.append(f"Nodes {ids} look like fix rounds of one node and belong on that node as attempts.")
    except (AttributeError, TypeError, ValueError):
        # Advice is best-effort, even when a caller constructs a malformed graph.
        pass
    return advice


def _project_root(graph_path: str) -> PurePosixPath | None:
    path = PurePosixPath(normpath(graph_path))
    for parent in path.parents:
        if parent.name == ".opencode":
            return PurePosixPath(abspath(parent.parent))
    return None


def _path_parts(value: str, project_root: PurePosixPath | None = None) -> tuple[str, ...]:
    # An absolute value replaces the base; a relative one must be anchored
    # before '..' can be compared correctly against other paths.
    return PurePosixPath(normpath(join(project_root or _SYNTHETIC_ROOT, value.strip()))).parts


def _contains(scope: tuple[str, ...], path: tuple[str, ...]) -> bool:
    return path[:len(scope)] == scope


class _Prefixes:
    """Number every path prefix once, so containment is a set lookup.

    A path's prefix keys are found by walking its parts one at a time
    (linear in the path), never by slicing tuples. `scope` contains `path`
    exactly when the key of the whole `scope` is one of `path`'s prefix keys.
    """

    def __init__(self) -> None:
        self._table: dict[tuple[int, str], int] = {}

    def keys(self, parts: tuple[str, ...]) -> list[int]:
        """Prefix keys of `parts`, shortest first, adding any that are new."""
        keys, key = [], -1
        for part in parts:
            key = self._table.setdefault((key, part), len(self._table))
            keys.append(key)
        return keys

    def covered(self, parts: tuple[str, ...], owned: set[int]) -> bool:
        """True when some prefix of `parts` (itself included) is in `owned`."""
        key = -1
        for part in parts:
            key = self._table.get((key, part))
            if key is None:
                return False
            if key in owned:
                return True
        return False


def _artifact_issues(graph: TaskGraph, by_id: dict[str, Node],
                     owned: dict[str, set[int]], prefixes: _Prefixes,
                     project_root: PurePosixPath | None) -> list[str]:
    issues = []
    artifact_by_node = {}
    artifact_root = prefixes.keys(_path_parts(".opencode/artifacts/", project_root))[-1]
    # A node may also read what an evaluator hands back with its rework.
    feedback_sources: dict[str, set[str]] = {}
    for evaluator in graph.nodes:
        for target in evaluator.feedback_to:
            feedback_sources.setdefault(target, set()).add(evaluator.id)
    for node_id, producer in by_id.items():
        artifacts = artifact_by_node[node_id] = {}
        for artifact in producer.artifacts:
            if isinstance(artifact.id, str):
                artifacts.setdefault(artifact.id, artifact)
    for node in graph.nodes:
        ids = Counter(artifact.id for artifact in node.artifacts if isinstance(artifact.id, str))
        issues.extend(f"{node.id} has duplicate artifact id {artifact_id}"
                      for artifact_id in sorted(ids) if ids[artifact_id] > 1)
        for i, artifact in enumerate(node.artifacts):
            for field_name in ("id", "path"):
                value = getattr(artifact, field_name)
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"{node.id} artifact[{i}] {field_name} must be a non-blank string")
            if not _positive_revision(artifact.revision):
                issues.append(f"{node.id} artifact[{i}] revision must be a positive integer")
            if isinstance(artifact.path, str) and artifact.path.strip():
                allowed = owned[node.id] if node.scope else {artifact_root}
                if not prefixes.covered(_path_parts(artifact.path, project_root), allowed):
                    boundary = "its scope" if node.scope else ".opencode/artifacts/"
                    issues.append(f"{node.id} artifact {artifact.id!r} path {artifact.path!r} outside {boundary}")
        dependencies = set(node.depends_on) | feedback_sources.get(node.id, set())
        for i, entry in enumerate(node.inputs):
            for field_name, value in (("from", entry.from_node), ("artifact", entry.artifact)):
                if not isinstance(value, str) or not value.strip():
                    issues.append(f"{node.id} input[{i}] {field_name} must be a non-blank string")
            if not _positive_revision(entry.revision):
                issues.append(f"{node.id} input[{i}] revision must be a positive integer")
            if entry.from_node not in dependencies:
                issues.append(f"{node.id} input from {entry.from_node} is not in depends_on "
                              "and does not send feedback to it")
            artifacts = artifact_by_node.get(entry.from_node, {}) if isinstance(entry.from_node, str) else {}
            artifact = artifacts.get(entry.artifact) if isinstance(entry.artifact, str) else None
            if artifact is None:
                issues.append(f"{node.id} input references unknown artifact {entry.artifact!r} on {entry.from_node}")
            elif (_positive_revision(entry.revision) and _positive_revision(artifact.revision)
                  and entry.revision > artifact.revision):
                issues.append(f"{node.id} input {entry.artifact!r} from {entry.from_node} reads revision "
                              f"{entry.revision} above current revision {artifact.revision}")
    for node_id, producer, artifact_id, read, current in stale_inputs(graph):
        status = by_id[node_id].status
        if status in ("running", "returned", "passed"):
            issues.append(f"{node_id} is {status} with stale input {artifact_id!r} from {producer}: "
                          f"read revision {read}, current revision {current}")
    return issues


def _dependency_issues(by_id: dict[str, Node], scope_keys: dict[str, list[list[int]]]) -> list[str]:
    """Check for cycles and compare scopes only between unordered writers.

    `scope_keys` holds, per node, the prefix keys of each scope entry (see
    `_Prefixes`). Two entries overlap when one is a prefix of the other, so
    candidate writers are found through two indexes instead of comparing
    every pair of entries.
    """
    children: dict[str, list[str]] = {node_id: [] for node_id in by_id}
    indegree: dict[str, int] = {}
    for node_id, node in by_id.items():
        deps = {dep for dep in node.depends_on if dep in by_id and dep != node_id}
        indegree[node_id] = len(deps)
        for dep in deps:
            children[dep].append(node_id)

    ready = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    writers = [node_id for node_id in by_id if scope_keys[node_id]]
    # Writers already visited, by the key of a whole scope entry they own, and
    # by every proper prefix of an entry they own.
    owners: dict[int, int] = {}
    below: dict[int, int] = {}
    writer_indices = {node_id: i for i, node_id in enumerate(writers)}
    # Kahn's dependency order lets each node inherit all its ancestor writers
    # just once. The masks also skip all ordered writer pairs without a scan.
    ancestors = dict.fromkeys(by_id, 0)
    previous_writers = 0
    conflicts: list[tuple[int, int]] = []
    conflicts_limited = False
    while ready:
        node_id = ready.popleft()
        upstream = ancestors[node_id]
        if node_id in writer_indices:
            i = writer_indices[node_id]
            parallel = previous_writers & ~upstream
            if parallel and not conflicts_limited:
                overlapping = 0
                for keys in scope_keys[node_id]:
                    for key in keys:  # visited entries equal to or containing this one
                        overlapping |= owners.get(key, 0)
                    overlapping |= below.get(keys[-1], 0)  # entries inside this one
                hits = overlapping & parallel
                while hits:
                    bit = hits & -hits
                    j = bit.bit_length() - 1
                    hits ^= bit
                    if len(conflicts) == MAX_SCOPE_CONFLICTS:
                        conflicts_limited = True
                        break
                    conflicts.append((min(i, j), max(i, j)))
            bit = 1 << i
            for keys in scope_keys[node_id]:
                owners[keys[-1]] = owners.get(keys[-1], 0) | bit
                for key in keys[:-1]:
                    below[key] = below.get(key, 0) | bit
            previous_writers |= bit
            upstream |= bit

        for child in children[node_id]:
            ancestors[child] |= upstream
            indegree[child] -= 1
            if not indegree[child]:
                ready.append(child)

    issues = []
    # Nodes left here can never become ready, so there is no parallel schedule
    # for them to audit until the cycle is fixed.
    remaining = [node_id for node_id, degree in indegree.items() if degree]
    if remaining:
        issues.append("dependency cycle among " + ", ".join(remaining))
    issues.extend(f"{writers[i]} and {writers[j]} can run in parallel but share write scope"
                  for i, j in sorted(conflicts))
    if conflicts_limited:
        issues.append("... and 1 or more parallel writers share write scope (further conflicts not counted)")
    return issues


def validate(graph: TaskGraph) -> list[str]:
    """Report structural, lifecycle, and write-scope problems in a task graph."""
    ids = Counter(node.id for node in graph.nodes)
    issues = [f"duplicate node id {node_id}" for node_id in sorted(ids) if ids[node_id] > 1]
    by_id = {node.id: node for node in graph.nodes}
    project_root = _project_root(graph.path)
    prefixes = _Prefixes()
    scope_keys: dict[str, list[list[int]]] = {}
    owned: dict[str, set[int]] = {}
    for node in graph.nodes:
        if not node.id.strip():
            issues.append("node has blank id")
        if node.pattern and node.pattern not in PATTERNS:
            issues.append(f"{node.id} has unknown pattern {node.pattern!r}")
        for dep in node.depends_on:
            if dep == node.id:
                issues.append(f"{node.id} depends on itself")
            elif dep not in by_id:
                issues.append(f"{node.id} depends on unknown node {dep}")
            elif node.status in ("running", "returned") and by_id[dep].status != "passed":
                issues.append(f"{node.id} is {node.status} before dependency {dep} has passed")
        if not node.done_when.strip():
            issues.append(f"{node.id} has no done_when evaluation")
        if graph.status == "passed" and node.status != "passed":
            issues.append(f"graph is passed but node {node.id} is {node.status}")
        if node.status == "passed" and not node.evidence.strip():
            issues.append(f"{node.id} passed with no evidence")
        if node.type == "gate" and node.status == "passed" and node.verdict != "confirmed":
            issues.append(f"{node.id} is a passed gate without a confirmed verdict")

        keys = scope_keys[node.id] = []
        for path in node.scope:
            if path.strip():
                keys.append(prefixes.keys(_path_parts(path, project_root)))
            else:
                issues.append(f"{node.id} has a blank scope entry")
        mine = owned[node.id] = {entry[-1] for entry in keys}
        for path in node.changed_paths:
            if not prefixes.covered(_path_parts(path, project_root), mine):
                issues.append(f"{node.id} changed path {path!r} outside its scope")

    objective_ids = Counter(objective.id for objective in graph.objectives)
    issues.extend(f"duplicate objective id {obj_id}" for obj_id in sorted(objective_ids)
                  if objective_ids[obj_id] > 1)
    for objective in graph.objectives:
        if not objective.nodes:
            issues.append(f"objective {objective.id} is not delivered by any node")
        for node_id in objective.nodes:
            if node_id not in by_id:
                issues.append(f"objective {objective.id} names unknown node {node_id}")

    if graph.status == "passed" and graph.objectives:
        delivered, total = objective_coverage(graph)
        if delivered != total:
            issues.append("graph is passed but not all objectives are delivered")

    issues.extend(_artifact_issues(graph, by_id, owned, prefixes, project_root))
    issues.extend(_feedback_issues(graph.nodes, by_id))
    issues.extend(_dependency_issues(by_id, scope_keys))
    return issues
