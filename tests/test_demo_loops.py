"""The demo's made-up loops (`agentgraph.demo.demo_loops`, `demo_run`): they look like the real API's answers and their settings are ones the controller accepts."""
import unittest

from agentgraph import demo, looprun, looptime

NOW = 1_800_000_000.0


class DemoLoopTests(unittest.TestCase):
    def test_every_demo_loop_has_settings_the_controller_accepts_unchanged(self):
        listing = demo.demo_loops(NOW)
        self.assertEqual([loop["flow"] for loop in listing["loops"]], [flow["id"] for flow in demo.demo_flows()])
        for loop in listing["loops"]:
            with self.subTest(flow=loop["flow"]):
                self.assertEqual(looprun.validate(loop["settings"], "x"), loop["settings"])  # already validated, defaults filled: a save of it round-trips
                self.assertTrue(loop["runnable"] and loop["path"][0] == loop["path"][1])

    def test_one_running_interval_run_and_one_paused_time_boxed_run(self):
        runs = demo.demo_loops(NOW)["runs"]
        running, paused = runs
        self.assertEqual((running["status"], running["timing"]["kind"], running["live"]), ("running", "interval", True))
        self.assertEqual((paused["status"], paused["timing"]["kind"], paused["live"]), ("paused", "timebox", False))
        self.assertEqual((running["timing"]["wakes"], running["timing"]["phase"]), (3, "waiting"))
        self.assertTrue(NOW < running["timing"]["next_at"] < running["timing"]["deadline"])  # the next wake is soon and before the end
        self.assertTrue(paused["timing"]["deadline"] > NOW and paused["timing"]["next_at"] is None)
        self.assertEqual(running["timing"]["deadline"] - running["timing"]["anchor"], 7200)  # "for 2 h"
        for row in runs:
            self.assertIsInstance(row["cost"], float)
            self.assertEqual(set(row["timing"]), set(looptime.view({"timing": {}})))  # the keys of a real row

    def test_a_run_in_detail_is_found_only_by_its_own_id(self):
        for row in demo.demo_loops(NOW)["runs"]:
            detail = demo.demo_run(row["run"], NOW)
            self.assertEqual((detail["run"]["status"], detail["run"]["flow"]), (row["status"], row["flow"]))
            self.assertEqual(detail["run"]["timing"]["deadline"], row["timing"]["deadline"])
            self.assertEqual(detail["definition"]["settings"]["timing"]["kind"], row["timing"]["kind"])
        self.assertIsNone(demo.demo_run("20261121-043755-90a7", NOW))


if __name__ == "__main__":
    unittest.main()
