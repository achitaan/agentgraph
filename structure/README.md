# Structure

This folder is the shape of the agent team, and the graph is drawn from it.

- Each sub-folder is a group. Its number sets the order round the ring (the first group after `hub` sits at the top); the name after the number is the group.
- Each `.md` file in a group is an agent, named by the file. Move a file to another folder and the agent moves in the graph; add a folder and a new group appears; delete a file and the agent falls back to the built-in placement.
- A file may begin with settings, one per line, before its description:
  - `pool: 3`: how many of this agent stand ready;
  - `escalates_to: impl-claude`: where its work goes when it fails twice;
  - `seats: councillor-`: agents whose names start with this sit in a circle round it.

Settings are read from the first 4096 bytes and at most the first 12 lines; blank lines before them are skipped, a blank line after them closes them, and the first other line starts the description.

The text after the settings is the agent's description: two or three plain sentences on what it does, when the orchestrator uses it and what it hands back, under 320 characters, with no model names (models come from the preset). It is carried in the snapshot as `agents[].about` and shown first in the agent's details in the graph view. `tests/test_structure_descriptions.py` checks it.

Settings are data: they show in the agent's details and never draw a line by themselves. A fresh download has agents and no connections; at rest the graph draws only the hub's lines to the agents and the council's to its seats. Every other line comes from a flow the user drew, or from a running task graph.

Which agents exist, and their models and skills, still come from the active preset in `oh-my-opencode-slim.json`: an agent with a file here and no entry there is not drawn. Without this folder the graph uses its built-in table.

## Flows

The `flows/` folder (never a group, and ignored by git through the repository's `.gitignore`: flows are each user's own) holds the flows drawn in the graph view: paths through several agents, each agent's output being the next one's input, with a short instruction for the orchestrator. One file per flow, `<id>.md`, where the id (lowercase letters, digits and `-`) is made from the path, such as `flows/impl-openai-verifier-impl-openai.md`:

```
path: impl-openai -> verifier -> impl-openai

verifier checks impl-openai's work. If it does not pass, send verifier's findings back to impl-openai and repeat until it passes.
```

- The path has two to 20 agents, each the name of an agent file here; a council seat stands for its council. A step to an agent already on the path sends the work back to it, and the path may go on after that. Refused: an unknown agent, the same agent twice in a row, the hub anywhere but first or last, and, right after a return, a second return that no task graph could follow: back from the first agent (its own work starts the flow and is not a check), or back to an agent whose work came after the current agent's (a check can only send work back to work done before it), as in `impl-openai → verifier → reviewer → verifier → reviewer`.
- `A -> A` alone is a loop of an agent on itself, allowed for any agent, the hub included: "A checks its own work against the task's evaluation and repeats until it passes." An agent has one at most; a second is refused. `agentgraph check` adds no rule for it but prints it with the others.
- At most 200 flow files are read. Adding a flow when 200 are there is refused before anything is written; files beyond 200 put there by hand are named in a warning.
- The instruction is the text after the blank line, kept exactly as written: at most 2000 characters, no control characters other than newline and tab. It is only ever read as text, never as settings. Left empty or only whitespace, on adding or on editing, it is made from the path again: that is how an edit goes back to the generated text.
- Draw one in the graph view (click an agent, click the next ones, then press Enter or click the last one again; the details then let you edit the instruction; later, right-click a flow to open them again, or click it and press Delete to remove it, with Ctrl/Cmd+Z to bring it back), or use `agentgraph structure flow add impl-openai verifier impl-openai [--text-file F|-]`. `flow text ID --text-file F|-` changes the instruction, `flow remove ID` removes the flow, and `agentgraph structure flows` lists them. These commands write only in `flows/`: a new file never replaces another, the folder goes when its last flow does, and no agent's file is touched. A file that cannot be read as a flow, or a symlink, is skipped with a warning.
- `agentgraph check` enforces each flow on the task graphs (see `GRAPH_FORMAT.md`) and prints the instructions after its result. In the snapshot they are `structure.flows`: `{"id": ..., "path": [...], "text": ..., "source": "drawn"}`. There are no default loops.
