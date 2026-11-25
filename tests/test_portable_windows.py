"""portable.tokenize also knows a Windows config folder: backslashes, forward slashes, JSON escapes."""
import unittest

from agentgraph import portable

FOLDER = "C:\\Users\\NAME\\.config\\opencode"


class WindowsPathTests(unittest.TestCase):
    def test_both_spellings_become_the_token(self):
        text = ("run C:\\Users\\NAME\\.config\\opencode\\orchestration\\bin\\agentgraph.cmd and "
                "C:/Users/NAME/.config/opencode/orchestration/bin/agentgraph")
        out = portable.tokenize(text, [FOLDER], False)
        self.assertEqual(out, "run @OPENCODE_CONFIG_DIR@\\orchestration\\bin\\agentgraph.cmd and "
                              "@OPENCODE_CONFIG_DIR@/orchestration/bin/agentgraph")

    def test_json_escaped_path(self):
        text = '"C:\\\\Users\\\\NAME\\\\.config\\\\opencode\\\\orchestration\\\\bin\\\\agentgraph.cmd review-copy *"'
        out = portable.tokenize(text, [FOLDER], True)
        self.assertEqual(out, '"@OPENCODE_CONFIG_DIR@\\\\orchestration\\\\bin\\\\agentgraph.cmd review-copy *"')

    def test_a_longer_folder_name_is_left_alone(self):
        text = "C:\\Users\\NAME\\.config\\opencode-old\\x"
        self.assertEqual(portable.tokenize(text, [FOLDER], False), text)

    def test_trailing_separator_and_posix_unchanged(self):
        self.assertEqual(portable.tokenize("C:\\Users\\NAME\\.config\\opencode\\x", [FOLDER + "\\"], False),
                         "@OPENCODE_CONFIG_DIR@\\x")
        # a backslash inside a POSIX folder name is not a Windows path: no "/" spelling is invented
        self.assertEqual(portable.tokenize("/h/a/b", ["/h/a\\b"], False), "/h/a/b")


if __name__ == "__main__":
    unittest.main()
