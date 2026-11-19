import unittest

from agentgraph.viewstate import KEY_HELP, PANELS, ViewState, clamp, decode_keys, handle_key

# The documented keys (KEY_HELP), written out literally so that reordering
# PANELS or the key handling cannot pass unnoticed.
NUMBER_KEYS = {"1": "agents", "2": "tasks", "3": "loops", "4": "events", "5": "issues", "0": "all"}
TAB_ORDER = ["agents", "tasks", "loops", "events", "issues", "all"]
SHIFT_TAB_ORDER = ["issues", "events", "loops", "tasks", "agents", "all"]


class HandleKey(unittest.TestCase):
    def test_scroll_never_goes_negative(self):
        state = handle_key(ViewState(), "k")
        self.assertEqual(state.scroll, 0)
        self.assertEqual(handle_key(handle_key(state, "j"), "down").scroll, 2)
        self.assertEqual(handle_key(ViewState(scroll=3), "pgup", page=20).scroll, 0)
        self.assertEqual(handle_key(ViewState(scroll=3), " ", page=20).scroll, 23)

    def test_number_keys_follow_the_documented_mapping(self):
        for key, panel in NUMBER_KEYS.items():
            with self.subTest(key=key):
                start = ViewState(focus="tasks" if panel != "tasks" else "loops", scroll=7)
                state = handle_key(start, key)
                self.assertEqual((state.focus, state.scroll), (panel, 0))
                label = "dashboard" if panel == "all" else panel
                self.assertIn(f"{key} {label}", KEY_HELP)
        for key in ("a", "esc"):
            self.assertEqual(handle_key(ViewState(focus="loops", scroll=4, help=True), key), ViewState())

    def test_tab_and_shift_tab_visit_every_panel_in_order(self):
        for key, order in (("tab", TAB_ORDER), ("shift-tab", SHIFT_TAB_ORDER)):
            state = ViewState(scroll=3)
            for expected in order:
                state = handle_key(state, key)
                self.assertEqual((key, state.focus, state.scroll), (key, expected, 0))

    def test_unknown_key_and_help(self):
        self.assertEqual(handle_key(ViewState(scroll=2), "x"), ViewState(scroll=2))
        self.assertTrue(handle_key(ViewState(), "?").help)

    def test_clamp(self):
        self.assertEqual(clamp(handle_key(ViewState(), "G"), 12).scroll, 12)
        self.assertEqual(clamp(ViewState(focus="nope", scroll=-3), 5), ViewState())

    def test_panels_are_the_documented_ones(self):
        self.assertEqual(PANELS, ("all", "agents", "tasks", "loops", "events", "issues"))


class DecodeKeys(unittest.TestCase):
    def test_sequences_and_plain_keys(self):
        self.assertEqual(decode_keys(b"j\x1b[A\x1b[6~q"), ["j", "up", "pgdn", "q"])
        self.assertEqual(decode_keys(b"\x1b"), ["esc"])
        self.assertEqual(decode_keys(b"\t\x1b[Z\x03"), ["tab", "shift-tab", "ctrl-c"])

    def test_unknown_sequences_and_control_bytes_are_dropped(self):
        self.assertEqual(decode_keys(b"\x1b[1;5Cx\x00\x7f"), ["x"])
        self.assertEqual(decode_keys(b""), [])


if __name__ == "__main__":
    unittest.main()
