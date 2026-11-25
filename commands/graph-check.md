---
description: Validate project graphs and point out unfinished work.
---
Accept `/graph-check [task]`. If supplied, treat the entire argument as one task name, not as shell syntax. Pass it as one safely quoted `--task=NAME` argument; if you cannot pass it safely, stop.
Run `~/.config/opencode/orchestration/bin/agentgraph check` from the current project, with that option only if supplied.
On native Windows PowerShell, set `$env:PYTHONPATH = Join-Path $HOME '.config/opencode/orchestration'` and run `py -3 -P -m agentgraph.check`, with the same optional argument. Windows validation is static only: report its warning about omitted history and evidence checks.
Report validation errors and warnings, and say which graphs and objectives are unfinished. If the check output does not identify the unfinished nodes or graph status, read the matching graph files without editing them. Never create, resume or mark anything complete.

Argument:
$ARGUMENTS
