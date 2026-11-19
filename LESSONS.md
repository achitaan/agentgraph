# Lessons from earlier task graphs

One line per lesson: what happened, and what to do differently. Keep this file under 50 lines; replace weaker lessons instead of appending forever.

- agentgraph-cli: review finished lanes as they pass, not once at the end. A late first review refuted two objectives after most work was built on them.
- agentgraph-cli: declare a node before every edit, including interface stubs and config changes the user asks for mid-run; undeclared edits fail the join audit.
- agentgraph-cli: a refuted gate's fix nodes do not depend on the gate; the gate depends on them. Ask the reviewer which findings block and which are known limits.
- agentgraph-cli: confirm an agent's model is reachable before dispatching to it. The designer's first run died on an unavailable model after eight minutes.
- agentgraph-cli: never copy into an existing backup folder; use a new folder name each time. One copy overwrote two original backups.
