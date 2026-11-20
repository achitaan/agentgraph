"""A made-up Snapshot for developing and demonstrating the view without live data."""
from __future__ import annotations

import time

from .contract import POOL, Agent, Event, Node, Objective, Session, Snapshot, TaskGraph, group_of
from .contract import Artifact, ArtifactInput

_ROSTER = [
    ("orchestrator", "Claude Fable 5.2 Fast", "high"),
    ("explorer", "Claude Haiku 5.5", "max"),
    ("researcher", "GPT-6.2 Astra Fast", "high"),
    ("oracle", "GPT-6.2 Astra Fast", "high"),
    ("oracle-lite", "Gemini 4 Carbon", "high"),
    ("councillor-fable", "Claude Fable 5.2", "high"),
    ("councillor-astra", "GPT-6.2 Astra", "high"),
    ("councillor-carbon", "Gemini 4 Carbon", "high"),
    ("council", "Claude Opus 5.5", "high"),
    ("impl-openai", "GPT-6.2 Sol Ultrafast", "high"),
    ("impl-claude", "Claude Opus 5.5 Fast", "xhigh"),
    ("impl-hard", "Claude Fable 5.2 Fast", "high"),
    ("designer", "GPT-6.1 Sol Ultrafast", "high"),
    ("reviewer", "GPT-6.2 Astra Ultrafast", "high"),
]


def demo_snapshot(now: float | None = None) -> Snapshot:
    now = time.time() if now is None else now
    t0 = now - 780  # the run started 13 minutes ago

    roster = [Agent(name, model, effort, group_of(name), POOL.get(name)) for name, model, effort in _ROSTER]
    models = {name: (model, effort) for name, model, effort in _ROSTER}

    def session(sid, agent, title, busy, start, end, cost, tin, tout, parent="ses_root"):
        model, effort = models[agent]
        return Session(sid, parent, agent, title, model, effort, busy, t0 + start, t0 + end, cost, tin, tout, "/work/api")

    root = session("ses_root", "orchestrator", "Add per-user rate limiting", True, 0, 778, 2.41, 5200, 14100, parent=None)
    sessions = [
        session("ses_a", "explorer", "Map route handlers and middleware", False, 10, 90, 0.04, 310, 1900),
        session("ses_b", "researcher", "Token bucket vs sliding window", False, 12, 205, 0.61, 480, 6200),
        session("ses_c", "oracle", "Choose limiter algorithm and storage", False, 210, 300, 0.92, 350, 4100),
        session("ses_d", "impl-openai", "Limiter core module", False, 305, 520, 0.38, 900, 8800),
        session("ses_e", "impl-openai", "Config and env wiring", False, 305, 410, 0.12, 400, 2600),
        session("ses_f", "impl-openai", "Middleware integration", False, 305, 600, 0.44, 950, 9100),
        session("ses_g", "impl-claude", "Middleware integration (escalated)", True, 605, 776, 1.35, 2100, 15800),
        session("ses_h", "designer", "429 error page", True, 420, 777, 0.09, 600, 5200),
        session("ses_i", "reviewer", "Review the combined change", False, 545, 575, 0.21, 700, 3100),
    ]

    def node(nid, ntype, agent, goal, status, deps=(), scope=(), done_when="", attempts=0, tiers=None,
             evidence="", sess=None, start=None, end=None):
        return Node(nid, ntype, agent, goal, status, list(deps), list(scope), done_when, attempts,
                    list(tiers if tiers is not None else ([agent] if attempts else [])), evidence, sess,
                    None if start is None else t0 + start, None if end is None else t0 + end)

    nodes = [
        node("n1", "research", "explorer", "Map every public route handler and the middleware chain", "passed",
             done_when="Handlers listed with file:line", attempts=1,
             evidence="14 handlers in src/routes; chain in src/app.ts:40", sess="ses_a", start=10, end=90),
        node("n2", "research", "researcher", "Compare token bucket and sliding window for bursty clients", "passed",
             done_when="Recommendation with sources", attempts=1,
             evidence="Token bucket fits burst allowance; 3 sources", sess="ses_b", start=12, end=205),
        node("n3", "decision", "oracle", "Choose the algorithm and where counters live", "passed", deps=("n1", "n2"),
             done_when="Decision with interface sketch", attempts=1,
             evidence="Token bucket, Redis counters, limiter interface agreed", sess="ses_c", start=210, end=300),
        node("n4", "implement", "impl-openai", "Limiter core: bucket math and Redis script", "passed", deps=("n3",),
             scope=("src/limiter/", "tests/limiter/"), done_when="pytest tests/limiter -q", attempts=3,
             evidence="18 passed in 1.2s", sess="ses_d", start=305, end=520),
        node("n5", "implement", "impl-openai", "Config keys and env wiring", "passed", deps=("n3",),
             scope=("src/config.py", ".env.example"), done_when="pytest tests/config -q", attempts=1,
             evidence="6 passed", sess="ses_e", start=305, end=410),
        node("n6", "implement", "impl-claude", "Apply the limiter in the middleware chain", "running", deps=("n3",),
             scope=("src/app.ts", "src/middleware/"), done_when="npm test -- middleware", attempts=5,
             tiers=("impl-openai", "impl-claude"),
             evidence="2 of 9 failing: headers missing on 429 (same failure twice, escalated)",
             sess="ses_g", start=305),
        node("n7", "design", "designer", "429 error page and retry-after message", "running", deps=("n3",),
             scope=("web/errors/",), done_when="Page renders at 3 widths; copy reviewed", attempts=1,
             sess="ses_h", start=420),
        node("n8", "join", "orchestrator", "Combine the lanes and run the integration tests", "pending",
             deps=("n4", "n5", "n6", "n7"), done_when="npm run test:integration"),
        node("n9", "gate", "reviewer", "Independent review of the combined change", "failed", deps=("n8",),
             done_when="No critical or major findings", attempts=1,
             evidence="2 major findings: no Retry-After on 429; error page omits the retry time",
             sess="ses_i", start=545),
        node("n10", "human", "user", "Approve the default limits before rollout", "pending", deps=("n9",),
             done_when="User confirms limits"),
        node("n11", "check", "orchestrator", "Full test suite, typecheck and lint", "pending", deps=("n10",),
             done_when="npm run ci"),
    ]

    by_id = {n.id: n for n in nodes}
    artifact_root = ".opencode/artifacts/add-rate-limit"
    for nid, artifact_id, filename in (
        ("n1", "route-map", "route-map.md"),
        ("n2", "recommendation", "recommendation.md"),
        ("n3", "decision", "decision.md"),
        ("n4", "limiter", "limiter.md"),
        ("n5", "configuration", "configuration.md"),
        ("n9", "findings", "findings.md"),
    ):
        producer = by_id[nid]
        producer.artifacts = [Artifact(artifact_id, f"{artifact_root}/{nid}/r1/{filename}", 1)]
    for nid in ("n4", "n5", "n6", "n7"):
        by_id[nid].scope.append(f"{artifact_root}/{nid}/")
        by_id[nid].inputs = [ArtifactInput("n3", "decision", 1)]
    by_id["n3"].inputs = [ArtifactInput("n1", "route-map", 1), ArtifactInput("n2", "recommendation", 1)]
    by_id["n8"].inputs = [ArtifactInput(nid, artifact.id, artifact.revision)
                          for nid in ("n4", "n5", "n6", "n7") for artifact in by_id[nid].artifacts]
    for pattern, ids in (
        ("parallel", ("n1", "n2", "n4", "n5", "n6", "n7")),
        ("synthesize", ("n3", "n8")),
        ("evaluate", ("n9",)),
        ("chain", ("n10", "n11")),
    ):
        for nid in ids:
            by_id[nid].pattern = pattern
    # The decision accepted the route map; it would otherwise go back to n1.
    by_id["n3"].feedback_to = ["n1"]
    by_id["n9"].feedback_to = ["n4", "n5", "n6", "n7"]
    # The gate refuted the first combined change and sent n6 and n7 back with
    # its findings; the join waits for the rework.
    by_id["n9"].verdict = "refuted"
    for nid in ("n6", "n7"):
        by_id[nid].inputs.append(ArtifactInput("n9", "findings", 1))
    by_id["n4"].baseline = "0 of 18 passing (tests written first)"
    by_id["n4"].outputs = "Limiter.check(user_id) -> Decision; Redis key rl:{user}"
    by_id["n4"].changed_paths = ["src/limiter/bucket.py", "src/limiter/redis.lua", "tests/limiter/test_bucket.py"]
    by_id["n5"].status = "returned"  # the implementer reports PASS; the orchestrator has not evaluated it yet
    by_id["n5"].finished = None
    by_id["n6"].baseline = "5 of 9 passing"
    by_id["n6"].failure_kind = "code"
    by_id["n6"].stalled = True

    def event(at, node_id, name, note=""):
        return Event(t0 + at, node_id, name, note)

    events = [
        event(0, "", "declared", "11 nodes, 7 waves"),
        event(10, "n1", "dispatched", "explorer"),
        event(12, "n2", "dispatched", "researcher"),
        event(90, "n1", "evaluated", "passed"),
        event(205, "n2", "evaluated", "passed"),
        event(210, "n3", "dispatched", "oracle"),
        event(300, "n3", "evaluated", "passed"),
        event(305, "n4", "dispatched", "impl-openai"),
        event(305, "n5", "dispatched", "impl-openai"),
        event(305, "n6", "dispatched", "impl-openai"),
        event(410, "n5", "returned", "PASS claimed: 6 passed; awaiting evaluation"),
        event(420, "n7", "dispatched", "designer"),
        event(470, "n6", "retried", "2 of 9 failing: headers missing on 429"),
        event(520, "n4", "evaluated", "passed: 18 passed after 3 attempts"),
        event(545, "n9", "dispatched", "reviewer"),
        event(575, "n9", "evaluated", "refuted: no Retry-After on 429; error page omits the retry time"),
        event(576, "n6", "retried", "sent back by n9 with findings r1"),
        event(576, "n7", "retried", "sent back by n9 with findings r1"),
        event(600, "n6", "escalated", "same failure twice: impl-openai -> impl-claude"),
        event(605, "n6", "dispatched", "impl-claude"),
    ]

    graph = TaskGraph("/work/api/.opencode/graph/add-rate-limit.json", "add-rate-limit",
                      "Add per-user rate limiting to the public API", "running", t0, now - 5, nodes, events,
                      "ses_root")
    graph.objectives = [
        Objective("o1", "Requests over the limit get a 429 with Retry-After", ["n4", "n6"]),
        Objective("o2", "Limits are configurable per environment", ["n5"]),
        Objective("o3", "Users see a clear error page", ["n7"]),
        Objective("o4", "No regression in the existing suite", ["n11"]),
    ]
    return Snapshot(now, roster, root, sessions, [graph], [])


def demo_setup() -> dict:
    """The snapshot's `setup` object for the demo: sample names only, no file is read."""
    return {"preset": "normal", "presets": ["ultra-fast", "fast", "normal"], "override": None,
            "saved": [{"name": "cheap-week", "saved": "2026-11-12T09:30:00Z", "preset": "fast", "rev": "0123456789abcdef"},
                      {"name": "full-power", "saved": "2026-11-18T16:05:00Z", "preset": "normal",
                       "rev": "fedcba9876543210"}],
            "undo": None}


# --- the example chat of each demo session ---------------------------------------------

# Each script lists (seconds before now, kind, text or tool name, tool status). A busy session's script ends on
# what it is doing now; a finished session gets the generic one.
_WORKING = {
    "ses_root": [
        (700, "user", "Add per-user rate limiting to the public API", None),
        (690, "reasoning", "Two lanes are done. The middleware lane failed twice, so it moves up a tier.\nThe error page lane is still running.", None),
        (680, "text", "Escalating the middleware work to impl-claude and waiting for the error page.", None),
        (676, "tool", "subagent", "completed"),
        (20, "reasoning", "Both lanes are working. Nothing to decide until one of them returns.", None),
        (5, "tool", "subagent", "running"),
    ],
    "ses_g": [
        (200, "user", "Apply the limiter in the middleware chain. Same failure twice: headers are missing on a 429.", None),
        (190, "reasoning", "The 429 path returns before the header middleware runs.\nMove the Retry-After header into the limiter's own reply.", None),
        (150, "text", "Found it: the limiter answers before the header step. I am setting Retry-After there and rerunning the middleware tests.", None),
        (140, "tool", "read", "completed"),
        (120, "tool", "edit", "completed"),
        (6, "tool", "shell", "running"),
    ],
    "ses_h": [
        (300, "user", "429 error page and retry-after message", None),
        (280, "tool", "read", "completed"),
        (240, "text", "The page shows a generic message. It needs the retry time from the header.", None),
        (3, "reasoning", "Check the three widths before writing the copy.\nThe retry time can be read from the Retry-After header.", None),
    ],
}


def _generic(title):
    return [
        (200, "user", title, None),
        (190, "reasoning", "Read the relevant files first, then answer.", None),
        (180, "tool", "read", "completed"),
        (170, "tool", "grep", "completed"),
        (150, "text", "Done. The result is written to the task's output.", None),
    ]


DEMO_SIDE = "ses_demoside"


def demo_sidechat() -> dict:
    """The snapshot's `sidechat` object for the demo: a fixed sample, nothing is read or called."""
    return {"installed": True, "session": DEMO_SIDE, "model": "openai/gpt-6-luna", "busy": False, "spent": 0.0042}


def demo_side_answer() -> dict:
    """What `agentgraph sidechat ask --demo` returns: a fixed sample answer, no call is made."""
    return {"answer": "n6 (the middleware lane) is retrying on a higher tier after failing the same test twice; "
                      "n4 passed. The brief does not show why n6 fails beyond its own evidence line.",
            "session": DEMO_SIDE, "model": "openai/gpt-6-luna", "fallback": False,
            "brief": {"chars": 6200, "full": True, "task": "add-rate-limit",
                      "sections": {"graph": 2900, "artifacts": 410, "chat": 2600}, "files": 0},
            "cost": 0.0021, "tokens": {"input": 3, "output": 61, "cache_write": 2200, "cache_read": 0}, "tools": 0,
            "spent": 0.0042, "seconds": 2.4}


def demo_side_brief() -> dict:
    return {"chars": 6200, "limit": 24000, "full": True, "task": "add-rate-limit",
            "sections": {"graph": 2900, "artifacts": 410, "chat": 2600}, "files": 0, "question_chars": 20,
            "session": DEMO_SIDE}


def demo_chat(session_id, now: float | None = None) -> dict | None:
    """The example chat of a demo session as a feed (see chatfeed.read_chat), or None for another id.
    The same `now` gives the same feed."""
    now = time.time() if now is None else now
    if session_id == DEMO_SIDE:
        return {"session": DEMO_SIDE, "agent": "side-chat", "busy": False, "truncated": False, "more": False,
                "warning": None,
                "items": [{"kind": "user", "time": round(now - 120, 3), "text": "Why is n6 stuck?", "cut": False},
                          {"kind": "text", "time": round(now - 118, 3), "cut": False,
                           "text": demo_side_answer()["answer"]}]}
    snap = demo_snapshot(now)
    session = next((s for s in [snap.root, *snap.sessions] if s is not None and s.id == session_id), None)
    if session is None:
        return None
    script = _WORKING.get(session.id) if session.busy else None
    script = script or _generic(session.title)
    items = []
    for ago, kind, value, status in script:
        item = {"kind": kind, "time": round(now - ago, 3)}
        if kind == "tool":
            item.update(name=value, status=status)
        else:
            item.update(text=value, cut=False)
        items.append(item)
    return {"session": session.id, "agent": session.agent, "busy": session.busy, "items": items,
            "truncated": False, "more": False, "warning": None}


def demo_activity(now: float | None = None) -> dict[str, dict]:
    """`activity` of each busy demo session, from the last item of its chat."""
    from .chatfeed import activity_of
    snap = demo_snapshot(now)
    found = {}
    for session in [snap.root, *snap.sessions]:
        feed = demo_chat(session.id, now) if session is not None and session.busy else None
        activity = activity_of(feed["items"][-1]) if feed and feed["items"] else None
        if activity is not None:
            found[session.id] = activity
    return found
