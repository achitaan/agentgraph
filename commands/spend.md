---
description: Summarize the recent cost trend.
---
Take no arguments. If there is any argument text, report the usage `/spend` instead.
Run `~/.config/opencode/orchestration/bin/agentgraph cost --trend`.
Report the trend in at most three lines: dollars per turn, cold share and pause cost for the latest periods if available. Report a lack of data or an error instead of inventing a comparison.
On native Windows, `~` is the user's home folder and the launcher is `agentgraph.cmd` in the same folder, with the same arguments: in PowerShell `& "$HOME\.config\opencode\orchestration\bin\agentgraph.cmd" ARGS`, in cmd `"%USERPROFILE%\.config\opencode\orchestration\bin\agentgraph.cmd" ARGS`; in Git Bash the command above works as written. If that does not start, set `PYTHONPATH` to the orchestration folder and run `py -3 -P -m agentgraph.cli ARGS`. Quote arguments for the shell in use; the rules above about data and temporary files do not change.

Argument:
$ARGUMENTS
