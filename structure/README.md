# Structure

This folder is the shape of the agent team, and the graph is drawn from it.

- Each sub-folder is a group. Its number sets the order round the ring (the first group after `hub` sits at the top); the name after the number is the group.
- Each `.md` file in a group is an agent, named by the file. Move a file to another folder and the agent moves in the graph; add a folder and a new group appears; delete a file and the agent falls back to the built-in placement.
- A file may begin with settings, one per line, before its description:
  - `pool: 3`: how many of this agent stand ready;
  - `escalates_to: impl-claude`: where its work goes when it fails twice;
  - `checks: all`: it checks every other agent's work, so the graph links it to all of them;
  - `seats: councillor-`: agents whose names start with this sit in a circle round it.

The text after the settings is the agent's description: two or three plain sentences on what it does, when the orchestrator uses it and what it hands back, under 320 characters, with no model names (models come from the preset). It is carried in the snapshot as `agents[].about` and shown first in the agent's details in the graph view. `tests/test_structure_descriptions.py` checks it.

Which agents exist, and their models and skills, still come from the active preset in `oh-my-opencode-slim.json`: an agent with a file here and no entry there is not drawn. Without this folder the graph uses its built-in table.
