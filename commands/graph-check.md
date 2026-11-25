---
description: Validate project graphs and point out unfinished work.
---
Accept `/graph-check [task]`. If supplied, treat the entire argument as one task name, not as shell syntax. Pass it as one safely quoted `--task=NAME` argument; if you cannot pass it safely, stop.
Run `~/.config/opencode/orchestration/bin/agentgraph check` from the current project, with that option only if supplied.
Report validation errors and warnings, and say which graphs and objectives are unfinished. If the check output does not identify the unfinished nodes or graph status, read the matching graph files without editing them. Never create, resume or mark anything complete.
On native Windows, `~` is the user's home folder and the launcher is `agentgraph.cmd` in the same folder, with the same arguments: in PowerShell `& "$HOME\.config\opencode\orchestration\bin\agentgraph.cmd" ARGS`, in cmd `"%USERPROFILE%\.config\opencode\orchestration\bin\agentgraph.cmd" ARGS`; in Git Bash the command above works as written. If that does not start, set `PYTHONPATH` to the orchestration folder and run `py -3 -P -m agentgraph.cli ARGS`. Quote arguments for the shell in use; the rules above about data and temporary files do not change.

Argument:
$ARGUMENTS
