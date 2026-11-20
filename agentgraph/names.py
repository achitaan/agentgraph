"""The one definition of a valid name, per kind. Every surface that takes a name checks it against these: the
endpoints (agentgraph.server), the command lines, the Desktop plugin's Ipc (`desktop-plugin/src/plugin.cjs` keeps
the same patterns as JavaScript regular expressions; tests/test_review4.py (Names) and the Desktop harness prove they are equal) and the terminal
panel (`tui-plugin/src`, its flow id).

- agent: an agent's name, also its file name in OpenCode's agents folder (`NAME.md`): a lowercase letter, then up
  to 63 lowercase letters, digits, `_` and `-`.
- flow id: a drawn flow's (and a group's) file name in the structure folder: a lowercase letter or digit, then up
  to 79 lowercase letters, digits and `-`.
- flow name (a path's name), which is also its command's name (`/NAME`, the file `NAME.md` in OpenCode's commands
  folder): 2 to 32 lowercase letters, digits and `-`, not starting with `-`.
- skill: a skill's id, its folder in the skills folder: a lowercase letter, then up to 63 lowercase letters, digits
  and `-`.
- mcp: an MCP server's name, as the view shows it: 1 to 64 letters, digits, `_`, `-` and `.`. A server with any other
  name is not shown (agentgraph.mcpview).

Each pattern is written without anchors; match it with `fullmatch`.
"""
from __future__ import annotations

import re

AGENT_PATTERN = r"[a-z][a-z0-9_-]{0,63}"
FLOW_ID_PATTERN = r"[a-z0-9][a-z0-9-]{0,79}"
FLOW_NAME_PATTERN = r"[a-z0-9][a-z0-9-]{1,31}"
COMMAND_PATTERN = FLOW_NAME_PATTERN  # a path's command is /NAME: the path's name
SKILL_PATTERN = r"[a-z][a-z0-9-]{0,63}"
MCP_PATTERN = r"[A-Za-z0-9_.-]{1,64}"

AGENT = re.compile(AGENT_PATTERN)
FLOW_ID = re.compile(FLOW_ID_PATTERN)
FLOW_NAME = re.compile(FLOW_NAME_PATTERN)
COMMAND = re.compile(COMMAND_PATTERN)
SKILL = re.compile(SKILL_PATTERN)
MCP = re.compile(MCP_PATTERN)

# The plugin's constant for each kind (desktop-plugin/src/plugin.cjs), checked for equality by the tests.
PLUGIN_CONSTANTS = {"AGENT_NAME": AGENT_PATTERN, "FLOW_ID": FLOW_ID_PATTERN, "FLOW_NAME": FLOW_NAME_PATTERN,
                    "SKILL_NAME": SKILL_PATTERN,
                    "MCP_NAME": MCP_PATTERN}


def valid(kind: str, value) -> bool:
    """Whether `value` is a valid name of `kind` ("agent", "flow id", "flow name", "command", "skill", "mcp")."""
    pattern = {"agent": AGENT, "flow id": FLOW_ID, "flow name": FLOW_NAME, "command": COMMAND, "skill": SKILL, "mcp": MCP}[kind]
    return isinstance(value, str) and pattern.fullmatch(value) is not None
