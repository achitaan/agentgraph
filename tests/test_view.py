import unittest

from agentgraph import view
from agentgraph.contract import Node, Session, Snapshot, TaskGraph
from agentgraph.viewstate import ViewState

NOW = 1_000_000.0


def session(sid, busy, idle_since=None):
    return Session(id=sid, parent_id=None, agent="impl", title="t", model="m", effort="high", busy=busy,
                   created=NOW - 5000, updated=NOW - 800, idle_since=idle_since)


def snapshot(*, liveness=True, nodes=None):
    nodes = nodes if nodes is not None else [
        Node(id="a-live", type="implement", agent="impl", goal="busy work", status="running", session="s-live"),
        Node(id="b-quiet", type="implement", agent="impl", goal="idle work", status="running", session="s-quiet"),
        Node(id="c-orphan", type="implement", agent="impl", goal="lost work", status="running", session="gone"),
        Node(id="d-silent", type="implement", agent="impl", goal="busy but pending", status="pending", session="s-silent"),
    ]
    graph = TaskGraph(path="g.json", task="t", goal="g", status="running", created=NOW - 9000, updated=NOW - 10, nodes=nodes)
    sessions = [session("s-live", True), session("s-quiet", False, NOW - 720), session("s-silent", True)]
    known = {s.id: s for s in sessions} if liveness else None
    return Snapshot(now=NOW, root=None, sessions=sessions if liveness else [], graphs=[graph], known=known)


def frame(snap, now=NOW, width=160, focus="tasks"):
    snap.now = now
    canvas = view.render(snap, width, 40, state=ViewState(focus=focus))
    return "\n".join("".join(cell.ch for cell in row).rstrip() for row in canvas.rows)


def line_with(text, needle):
    return next(line for line in text.splitlines() if needle in line)


class Liveness(unittest.TestCase):
    def test_only_a_live_node_blinks(self):
        snap = snapshot()
        # the glyph of a live running node alternates; a quiet or orphan one never changes
        glyphs = {"a-live": set(), "b-quiet": set(), "c-orphan": set()}
        for tick in (0.0, 0.5, 1.0, 1.5):
            text = frame(snap, now=NOW + tick)
            for node in glyphs:
                glyphs[node].add(line_with(text, node).split(node)[1].lstrip()[0])
        self.assertEqual(glyphs["a-live"], {"▶", "▷"})
        self.assertEqual(glyphs["b-quiet"], {"▷"})
        self.assertEqual(glyphs["c-orphan"], {"▷"})

    def test_quiet_and_orphan_and_unreported_say_what_they_are(self):
        text = frame(snapshot())
        self.assertIn("REPORTED RUNNING · IDLE 12M", line_with(text, "b-quiet"))
        self.assertIn("REPORTED RUNNING · NO SESSION", line_with(text, "c-orphan"))
        self.assertIn("WORKING · NOT MARKED RUNNING", line_with(text, "d-silent"))
        live = line_with(text, "a-live")
        self.assertIn("RUNNING", live)
        self.assertNotIn("REPORTED", live)

    def test_a_quiet_row_has_no_pulsing_background(self):
        ctx = view._context(snapshot(), "", "", ViewState(focus="tasks"))
        by_id = ctx.graph.by_id
        self.assertIsNone(view._row_bg(by_id["b-quiet"], NOW))
        self.assertIsNone(view._row_bg(by_id["c-orphan"], NOW))
        self.assertIsNotNone(view._row_bg(by_id["a-live"], NOW))

    def test_narrow_cards_use_short_words(self):
        text = frame(snapshot(), width=60, focus="all")
        self.assertIn("IDLE 12M", text)
        self.assertIn("NO SESSION", text)

    def test_without_the_field_a_running_node_is_drawn_as_it_always_was(self):
        # no session list and no database: nothing is confirmed, so the node with a session id keeps its look
        nodes = [Node(id="x-run", type="implement", agent="impl", goal="work", status="running", session="s1")]
        snap = snapshot(liveness=False, nodes=nodes)
        text = frame(snap)
        row = line_with(text, "x-run")
        self.assertIn("RUNNING", row)
        for word in ("REPORTED", "IDLE", "NO SESSION", "NOT MARKED"):
            self.assertNotIn(word, row)
        ctx = view._context(snap, "", "", ViewState(focus="tasks"))
        node = ctx.graph.by_id["x-run"]
        self.assertIsNone(getattr(node, "liveness", None))
        self.assertEqual({view._glyph(node, t / 2) for t in range(4)}, {"▶", "▷"})
        self.assertIsNotNone(view._row_bg(node, NOW))


if __name__ == "__main__":
    unittest.main()
