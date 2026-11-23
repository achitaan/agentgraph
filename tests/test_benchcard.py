"""agentgraph.benchcard: made-up result rows in a temporary folder; the real results and config are never read."""
import contextlib
import html
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from agentgraph import bench, benchcard

MODEL = "anthropic/claude-sonnet-5-5"


def row(label, task, passed, cost=0.1, seconds=60.0, turns=4, calls=2, config="c1", model=MODEL, **extra):
    return {"scenario": task, "label": label, "rep": 1, "check": {"passed": passed, "detail": "d"}, "cost": cost, "wall_seconds": seconds,
            "turns": turns, "agent_calls": {"impl": calls} if calls else {}, "config_hash": config, "model": model, **extra}


def lrow(method, task, visible=True, hidden=True, cost=0.05, seconds=30.0, calls=3, sessions=1, **extra):
    return {"method": method, "task": task, "rep": 1, "visible": visible, "hidden": hidden, "cost": cost, "seconds": seconds,
            "calls": calls, "sessions": sessions, "roots": ["x"], "model": MODEL, "config_hash": "h", "fake": False, **extra}


class CardBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, label, rows):
        (self.dir / f"{label}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def card(self, a="A", b="B", **kw):
        return benchcard.build_card(benchcard.load_runs(a, self.dir, kw.get("suite")), benchcard.load_runs(b, self.dir, kw.get("suite")),
                                    kw.get("suite"))

    def verdicts(self, **kw):
        return {v["measure"]: v["verdict"] for v in self.card(**kw)["verdicts"]}

    def tasks(self, n, a_passes, b_passes, reps=1, **kw):
        """n tasks, `reps` runs each; A passes a_passes[i] of them, B passes b_passes[i]."""
        for label, passes in (("A", a_passes), ("B", b_passes)):
            self.write(label, [row(label, f"t{i}", r < passes[i], **kw.get(label, {})) for i in range(n) for r in range(reps)])


class SignTestTests(unittest.TestCase):
    def test_p_values(self):
        self.assertEqual(benchcard.sign_test_p(0, 0), 1.0)
        self.assertAlmostEqual(benchcard.sign_test_p(0, 5), 0.0625)
        self.assertAlmostEqual(benchcard.sign_test_p(5, 0), 0.0625)
        self.assertAlmostEqual(benchcard.sign_test_p(0, 4), 0.125)
        self.assertAlmostEqual(benchcard.sign_test_p(1, 7), 0.0703125)
        self.assertAlmostEqual(benchcard.sign_test_p(1, 6), 0.125)
        self.assertAlmostEqual(benchcard.sign_test_p(3, 3), 1.0)
        self.assertAlmostEqual(benchcard.sign_test_p(1, 4), 0.375)        # 4 of 5
        self.assertAlmostEqual(benchcard.sign_test_p(1, 5), 0.21875)      # 5 of 6
        self.assertAlmostEqual(benchcard.sign_test_p(0, 6), 0.03125)      # 6 of 6
        self.assertAlmostEqual(benchcard.sign_test_p(1, 7), 0.0703125)    # 7 of 8


class PassingVerdictTests(CardBase):
    def test_five_tasks_won_by_b_is_better_and_by_a_is_worse(self):
        self.tasks(5, [0] * 5, [1] * 5)                      # p = 0.0625
        self.assertEqual(self.verdicts()["passing"], "better")
        self.tasks(5, [1] * 5, [0] * 5)
        self.assertEqual(self.verdicts()["passing"], "worse")

    def test_the_p_value_boundary(self):
        self.tasks(8, [0] * 8, [1] * 7 + [0])                # 7 to 0: p = 0.0156
        self.assertEqual(self.verdicts()["passing"], "better")
        self.tasks(8, [1] + [0] * 7, [0] + [1] * 7)          # 7 to 1: p = 0.0703
        self.assertEqual(self.verdicts()["passing"], "better")
        self.tasks(7, [1] + [0] * 6, [0] + [1] * 6)          # 6 to 1: p = 0.125, over 0.10
        self.assertEqual(self.verdicts()["passing"], benchcard.TOO_FEW)
        self.tasks(5, [1] + [0] * 4, [0, 1, 1, 1, 0])        # 3 to 1 with one tied
        self.assertEqual(self.verdicts()["passing"], benchcard.TOO_FEW)

    def test_the_boundaries_of_the_one_rule(self):
        for b_wins, a_wins, expected in ((5, 0, "better"), (4, 1, benchcard.TOO_FEW), (5, 1, benchcard.TOO_FEW), (6, 0, "better"),
                                         (7, 1, "better"), (1, 7, "worse"), (0, 5, "worse")):
            with self.subTest(b_wins=b_wins, a_wins=a_wins):
                n = b_wins + a_wins
                self.tasks(n, [0] * b_wins + [1] * a_wins, [1] * b_wins + [0] * a_wins)
                self.assertEqual(self.verdicts()["passing"], expected)

    def test_fewer_than_five_tasks_never_gives_a_verdict(self):
        self.tasks(4, [0] * 4, [1] * 4)                      # p = 0.125 anyway
        self.assertEqual(self.verdicts()["passing"], benchcard.TOO_FEW)
        self.tasks(4, [1] * 4, [1] * 4)                      # no difference, but only four tasks
        self.assertEqual(self.verdicts()["passing"], benchcard.TOO_FEW)

    def test_no_difference_over_five_tasks_is_same(self):
        self.tasks(5, [1, 1, 0, 0, 1], [1, 1, 0, 0, 1])
        self.assertEqual(self.verdicts()["passing"], "same")

    def test_the_larger_share_wins_a_task_when_the_run_counts_differ(self):
        self.write("A", [row("A", f"t{i}", True) for i in range(5)] + [row("A", f"t{i}", False) for i in range(5)])   # 1 of 2 each
        self.write("B", [row("B", f"t{i}", True) for i in range(5)])                                                    # 1 of 1 each
        card = self.card()
        self.assertIn("B passed a larger share on 5 tasks", card["verdicts"][0]["detail"])
        self.assertEqual(card["verdicts"][0]["verdict"], "better")

    def test_runs_passed_and_tasks_all_passed(self):
        self.tasks(3, [2, 1, 0], [2, 2, 2], reps=2)
        card = self.card()
        self.assertEqual((card["a"]["passed"], card["a"]["runs"], card["a"]["tasks_all_passed"], card["a"]["tasks"]), (3, 6, 1, 3))
        self.assertEqual((card["b"]["passed"], card["b"]["tasks_all_passed"]), (6, 3))


class MeasureVerdictTests(CardBase):
    def costs(self, a, b, **kw):
        n = len(a)
        self.write("A", [row("A", f"t{i}", True, cost=a[i], seconds=a[i] * 600, turns=a[i] * 10, calls=int(a[i] * 10), **kw) for i in range(n)])
        self.write("B", [row("B", f"t{i}", True, cost=b[i], seconds=b[i] * 600, turns=b[i] * 10, calls=int(b[i] * 10), **kw) for i in range(n)])
        return self.verdicts()

    def split(self, b_lower, a_lower, tied=0, **kw):
        """B is half of A on `b_lower` tasks, double on `a_lower`, equal on `tied`."""
        return self.costs([1] * (b_lower + a_lower + tied), [.5] * b_lower + [2] * a_lower + [1] * tied, **kw)

    def test_the_boundaries_of_the_one_rule_for_every_measure(self):
        for b_lower, a_lower, expected in ((5, 0, "better"), (4, 1, benchcard.TOO_FEW), (5, 1, benchcard.TOO_FEW), (6, 0, "better"),
                                           (7, 1, "better"), (1, 7, "worse"), (0, 5, "worse"), (3, 2, benchcard.TOO_FEW)):
            with self.subTest(b_lower=b_lower, a_lower=a_lower):
                verdicts = self.split(b_lower, a_lower)
                self.assertEqual({verdicts[k] for k in ("cost", "minutes", "turns", "calls")}, {expected})

    def test_ties_are_dropped_before_the_test(self):
        self.assertEqual(self.split(5, 0, tied=3)["cost"], "better")                 # 5 to 0 of 8 tasks
        self.assertEqual(self.split(4, 1, tied=3)["cost"], benchcard.TOO_FEW)

    def test_same_needs_every_task_within_five_percent(self):
        self.assertEqual(self.costs([1] * 5, [1] * 5)["cost"], "same")
        self.assertEqual(self.costs([1] * 5, [1.04, .96, 1.05, .95, 1])["cost"], "same")
        self.assertEqual(self.costs([1] * 5, [1.04, .96, 1.05, .95, 1.2])["cost"], benchcard.TOO_FEW)   # one task outside
        self.assertEqual(self.costs([1] * 5, [1, 1, 1, 1, 2])["cost"], benchcard.TOO_FEW)               # four equal of five

    def test_a_small_but_steady_difference_is_the_same(self):
        verdicts = self.costs([1] * 5, [.97] * 5)       # B lower on all five, but inside the band
        self.assertEqual(verdicts["cost"], "same")

    def test_the_detail_shows_the_size_and_the_p_value(self):
        detail = next(v["detail"] for v in self.card()["verdicts"] if v["measure"] == "cost") if self.split(5, 0) else ""
        self.assertIn("median B/A 0.50", detail)
        self.assertIn("p = 0.062", detail)

    def test_fewer_than_five_tasks_is_too_few_however_clear(self):
        self.assertEqual(self.costs([1] * 4, [.5] * 4)["cost"], benchcard.TOO_FEW)
        self.assertEqual(self.costs([1] * 5, [.5] * 5)["cost"], "better")

    def test_every_measure_has_a_verdict(self):
        verdicts = self.costs([1] * 5, [2] * 5)
        self.assertEqual({k: verdicts[k] for k in ("cost", "minutes", "turns", "calls")}, {"cost": "worse", "minutes": "worse", "turns": "worse", "calls": "worse"})

    def test_zeros_do_not_divide(self):
        self.write("A", [row("A", f"t{i}", True, cost=0.0, seconds=0.0, turns=0, calls=0) for i in range(5)])
        self.write("B", [row("B", f"t{i}", True, cost=0.0, seconds=0.0, turns=0, calls=0) for i in range(5)])
        self.assertEqual(set(self.verdicts().values()), {"same"})
        self.write("B", [row("B", f"t{i}", True, cost=0.5) for i in range(5)])               # A is zero, B is not
        card = self.card()
        cost = next(v for v in card["verdicts"] if v["measure"] == "cost")
        self.assertEqual(cost["verdict"], "worse")
        self.assertIn("A is zero", cost["detail"])
        benchcard.render_text(card), benchcard.render_html(card)

    def test_missing_numbers_leave_a_task_out_of_that_measure_only(self):
        self.write("A", [row("A", f"t{i}", True) for i in range(5)])
        self.write("B", [dict(row("B", f"t{i}", True), cost=None) for i in range(5)])
        verdicts = self.verdicts()
        self.assertEqual((verdicts["cost"], verdicts["minutes"]), (benchcard.TOO_FEW, "same"))


class BrokenRunTests(CardBase):
    def five(self, label, **kw):
        return [row(label, f"t{i}", True, **kw) for i in range(5)]

    def test_a_broken_run_is_a_failed_run_but_leaves_every_measure(self):
        crashed = dict(row("A", "t0", False, cost=0.0, seconds=0.0, turns=0, calls=0), error="boom", sessions=0)
        self.write("A", self.five("A", cost=0.4, seconds=120.0, turns=8, calls=4)[1:] + [crashed, row("A", "t0", True, cost=0.4, seconds=120.0, turns=8, calls=4)])
        card = self.card()
        self.assertEqual((card["a"]["runs"], card["a"]["passed"], card["a"]["broken"]), (6, 5, 1))
        self.assertEqual((card["a"]["cost"], card["a"]["minutes"], card["a"]["turns"], card["a"]["calls"]), (0.4, 2.0, 8, 4))
        task = next(t for t in card["table"] if t["task"] == "t0")["a"]
        self.assertEqual((task["runs"], task["passed"], task["broken"], task["cost"]), (2, 1, 1, 0.4))

    def test_either_sign_of_a_broken_run_counts(self):
        self.write("A", [dict(row("A", "t0", True), error="x"), dict(row("A", "t1", True), sessions=0), row("A", "t2", True, sessions=3),
                         {k: v for k, v in row("A", "t3", True).items() if k not in ("error", "sessions")}])
        runs = benchcard.load_runs("A", self.dir)["runs"]
        self.assertEqual([r["broken"] for r in runs], [True, True, False, False])
        self.write("l-suite", [lrow("direct", "t0", sessions=0), lrow("direct", "t1", error="x"), lrow("direct", "t2")])
        self.assertEqual([r["broken"] for r in benchcard.load_runs("l-suite:direct", self.dir)["runs"]], [True, True, False])

    def test_broken_runs_do_not_make_a_setup_look_cheap(self):
        self.write("A", self.five("A", cost=1.0))
        self.write("B", [dict(r, cost=0.0, error="boom", sessions=0, check={"passed": False, "detail": "d"}) for r in self.five("B", cost=1.0)[:3]] + self.five("B", cost=1.0)[3:])
        card = self.card()
        verdicts = {v["measure"]: v["verdict"] for v in card["verdicts"]}
        self.assertEqual((verdicts["cost"], verdicts["minutes"]), (benchcard.TOO_FEW, benchcard.TOO_FEW))   # two tasks have numbers
        self.assertEqual(card["b"]["cost"], 1.0)
        self.assertEqual(card["no_clean"], {"a": [], "b": ["t0", "t1", "t2"]})
        self.assertTrue(any("no unbroken run in B" in w and "t0, t1, t2" in w for w in card["warnings"]))
        self.assertTrue(any("B: 3 runs broken" in w for w in card["warnings"]))
        self.assertIn("A on 3 tasks", card["verdicts"][0]["detail"])                                          # broken runs still failed

    def test_the_card_shows_the_count_in_text_and_page(self):
        self.write("A", self.five("A"))
        self.write("B", self.five("B")[:4] + [dict(row("B", "t4", False), error="boom", sessions=0)])
        card = self.card()
        text, page = benchcard.render_text(card), benchcard.render_html(card)
        self.assertRegex(text, r"broken runs\s+-\s+1\n")
        self.assertIn("warning: B: 1 run broken", text)
        self.assertIn('<div class="small worse">1 run broken', page)
        self.assertEqual(page.count("broken, left out"), 1)                                                   # only B has any
        self.assertNotIn("boom", page + text)
        clean = benchcard.render_text(self.card("A", "A"))
        self.assertNotIn("broken", clean.split("How to read this")[0])


class LabelProblemsTests(CardBase):
    def test_tasks_in_one_label_only_are_listed_and_left_out(self):
        self.write("A", [row("A", f"t{i}", True) for i in range(5)] + [row("A", "only-a", False)])
        self.write("B", [row("B", f"t{i}", True) for i in range(5)] + [row("B", "only-b", False)])
        card = self.card()
        self.assertEqual((card["only_a"], card["only_b"], card["compared"]), (["only-a"], ["only-b"], [f"t{i}" for i in range(5)]))
        self.assertTrue(any("only-a" in w and "left out" in w for w in card["warnings"]))
        self.assertTrue(any("only-b" in w for w in card["warnings"]))
        self.assertEqual(card["verdicts"][0]["verdict"], "same")                            # the failing tasks are not in the comparison
        self.assertIn("warning: tasks only in A", benchcard.render_text(card))

    def test_mixed_config_hashes_in_one_label_warn(self):
        self.write("A", [row("A", f"t{i}", True, config="c1" if i < 3 else "c2") for i in range(5)])
        self.write("B", [row("B", f"t{i}", True) for i in range(5)])
        card = self.card()
        self.assertTrue(any("2 different configs" in w and "A" in w for w in card["warnings"]))
        self.assertFalse(any("different configs" in w and w.startswith("B") for w in card["warnings"]))

    def test_a_label_with_no_rows_or_no_file_gives_a_card_not_a_crash(self):
        self.write("A", [row("A", f"t{i}", True) for i in range(5)])
        self.write("B", [])
        for other in ("B", "never-written"):
            with self.subTest(other=other):
                card = self.card(b=other)
                self.assertEqual({v["verdict"] for v in card["verdicts"]}, {benchcard.TOO_FEW})
                self.assertEqual((card["b"]["runs"], card["b"]["passed"], card["compared"]), (0, 0, []))
                self.assertTrue(any(w.startswith(other) for w in card["warnings"]))
                text, page = benchcard.render_text(card), benchcard.render_html(card)
                self.assertIn(f"warning: {other}", text)
                self.assertIn("0 of 0", text)
                self.assertIn("-", page)
        card = self.card(a="B", b="B")
        self.assertEqual(card["table"], [])

    def test_rows_without_a_pass_or_fail_are_left_out(self):
        probe = {"scenario": "probe", "probe_agent": "x", "label": "A", "check": {"passed": None}}
        self.write("A", [row("A", "t0", True), probe, {"scenario": "t1", "check": None}, "junk"])
        self.assertEqual(len(benchcard.load_runs("A", self.dir)["runs"]), 1)
        self.assertTrue(any("left out" in n for n in benchcard.load_runs("A", self.dir)["notes"]))

    def test_models_that_differ_warn(self):
        self.write("A", [row("A", "t0", True)])
        self.write("B", [row("B", "t0", True, model="openai/other")])
        self.assertTrue(any("not use the same model" in w for w in self.card()["warnings"]))

    def test_models_used_that_differ_or_stray_from_the_pin_warn(self):
        self.write("A", [row("A", "t0", True, models_used=[MODEL])])
        self.write("B", [row("B", "t0", True, models_used=[MODEL, "openai/stray"])])
        warnings = self.card()["warnings"]
        self.assertTrue(any("ran different models" in w and "openai/stray" in w for w in warnings))
        self.assertTrue(any(w.startswith("B used a model other than its pinned model: openai/stray") for w in warnings))
        self.assertFalse(any(w.startswith("A used a model other") for w in warnings))
        self.write("B", [row("B", "t0", True, models_used=[MODEL])])
        self.assertFalse(any("model" in w for w in self.card()["warnings"]))

    def test_rows_without_models_used_give_no_model_warning(self):
        self.write("A", [row("A", "t0", True)])
        self.write("B", [row("B", "t0", True, models_used="junk")])
        self.assertFalse(any("model" in w for w in self.card()["warnings"]))

    def test_the_suite_option_keeps_only_that_suites_rows(self):
        self.write("A", [row("A", f"t{i}", True, suite="one") for i in range(5)] + [row("A", "extra", False)])
        self.write("B", [row("B", f"t{i}", True, suite="one") for i in range(5)])
        card = self.card(suite="one")
        self.assertEqual((card["a"]["runs"], card["suite"], card["only_a"]), (5, "one", []))
        self.assertEqual(self.card(suite="other")["a"]["runs"], 0)

    def test_bad_labels_are_refused(self):
        for spec in ("../x", "a/b", "other:method", "l-suite:", "l-suite:a b"):
            with self.subTest(spec=spec), self.assertRaises(bench.BenchError):
                benchcard.load_runs(spec, self.dir)


class LSuiteTests(CardBase):
    def test_a_method_of_the_l_suite_file_is_a_label(self):
        rows = [lrow(m, f"t{i}", hidden=not (m == "loop-2" and i == 0)) for m in ("direct", "loop-2") for i in range(5)]
        rows += [lrow("direct", "t9", fake=True), lrow("direct", "t8", visible=None)]
        self.write("l-suite", rows)
        card = self.card("l-suite:direct", "l-suite:loop-2")
        self.assertEqual((card["a"]["runs"], card["a"]["passed"], card["b"]["passed"]), (5, 5, 4))
        self.assertEqual(card["a"]["turns"], 3)                # `calls` are model steps
        self.assertEqual(card["a"]["calls"], 0)                # sessions beyond the root sessions
        self.assertTrue(any("left out" in w for w in card["warnings"]))
        self.assertIn("l-suite:loop-2", benchcard.render_text(card))
        self.assertEqual(self.card("l-suite:nothing", "l-suite:direct")["a"]["runs"], 0)

    def test_agent_calls_are_the_sessions_beyond_the_roots(self):
        self.write("l-suite", [lrow("direct", "t0", sessions=4)])
        self.assertEqual(benchcard.load_runs("l-suite:direct", self.dir)["runs"][0]["calls"], 3)


class TextAndPageTests(CardBase):
    def test_text_ends_with_the_rule(self):
        self.tasks(5, [0] * 5, [1] * 5)
        text = benchcard.run_card("A", "B", results=self.dir)
        lines = text.splitlines()
        self.assertIn("passing           better", text)
        self.assertIn("5 of 5", text)
        self.assertEqual(lines[-len(benchcard.RULE):], [f"  {t}" for t in benchcard.RULE])
        rule = " ".join(benchcard.RULE)
        for words in ("fewer than 5 tasks can never give a verdict", "p value of at most 0.10", "within 5% of 1", "every task tied", "broken run"):
            self.assertIn(words, rule)
        self.assertEqual(text.count("1 task "), 0)

    def test_html_escapes_everything_taken_from_the_rows(self):
        evil = "<script>alert(1)</script>"
        rows_a = [row("A", evil, True, model='<b onload="x">')] + [row("A", f"t{i}", True) for i in range(4)]
        self.write("A", rows_a)
        self.write("B", [row("B", evil, False, model="m&m")])
        card = self.card()
        page = benchcard.render_html(card)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("<b onload", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("m&amp;m", page)
        self.assertNotIn("<script", benchcard.render_text(card).lower().replace("<script>alert(1)</script>", ""))

    def test_html_is_self_contained_and_has_no_paths_prompts_or_sessions(self):
        secret = {"work": "/made/up/work", "root_session": "ses_abc123", "prompt": "SECRET PROMPT", "error": "boom /made/up/x"}
        self.write("A", [row("A", f"t{i}", i % 2 == 0, **secret) for i in range(5)])
        self.write("B", [row("B", f"t{i}", True, **secret) for i in range(5)])
        page = benchcard.render_html(self.card())
        for text in ("/Users", "ses_abc123", "SECRET PROMPT", "boom"):
            self.assertNotIn(text, page)
        self.assertNotRegex(page, r"https?:|<script|<link|@import|url\(|<img|<iframe|animation|transition")
        for colour in ("#121111", "#f0b79a", "#e8705f"):
            self.assertIn(colour, page)
        self.assertIn("How to read this", page)
        for text in benchcard.RULE:
            self.assertIn(f"<p>{html.escape(text)}</p>", page)
        body = re.sub(r"h1 \{[^}]*\}|h2 \{[^}]*\}|\.number \.big \{[^}]*\}", "", benchcard.CSS)   # headings and the big numbers aside
        self.assertEqual(sorted(set(re.findall(r"font(?:-size)?:\s*(\d+)px", body))), ["13", "16"])

    def test_html_dots_are_filled_for_passes_and_hollow_for_failures(self):
        self.write("A", [row("A", "t0", True), row("A", "t0", True), row("A", "t0", False)])
        self.write("B", [row("B", "t0", True)])
        page = benchcard.render_html(self.card())
        self.assertEqual((page.count('<span class="dot on">'), page.count('<span class="dot off">')), (3, 1))
        self.assertIn("2 of 3 runs passed", page)

    def test_html_shows_the_numbers_the_verdicts_and_the_warnings(self):
        self.tasks(5, [0] * 5, [1] * 5, A={"cost": 0.2}, B={"cost": 0.1, "config": "c2"})
        self.write("B", [row("B", f"t{i}", True, cost=0.1) for i in range(5)] + [row("B", "t9", True, config="zzz")])
        page = benchcard.render_html(self.card())
        self.assertIn("passing: better", page)
        self.assertIn("cost a run: better", page)
        self.assertIn("6 of 6", page)
        self.assertIn("$0.2000", page)
        self.assertIn("different configs", page)
        self.assertIn(MODEL, page)

    def test_the_command_prints_the_card_and_writes_the_page(self):
        self.tasks(5, [0] * 5, [1] * 5)
        page = self.dir / "out" / "card.html"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(bench.main(["card", "A", "B", "--results", str(self.dir), "--html", str(page)]), 0)
        self.assertIn("bench card: A (A) against B (B)", out.getvalue())
        self.assertTrue(page.read_text().startswith("<!DOCTYPE html>"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(bench.main(["card", "../x", "B", "--results", str(self.dir)]), 2)


if __name__ == "__main__":
    unittest.main()
