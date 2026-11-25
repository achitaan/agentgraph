@echo off
rem Windows front door for the toolkit installer: install.cmd [--dry-run] or --list-backups or --restore NAME [--dry-run]
rem The same work as install.sh; agentgraph/installcli.py does it.
setlocal
call "%~dp0bin\_python.cmd"
if not defined AGENTGRAPH_PY (
  echo install.cmd needs Python 3.11 or newer. Install it from python.org, then check: py -3 --version 1>&2
  exit /b 1
)
set "PYTHONPATH=%~dp0."
%AGENTGRAPH_PY% -P -m agentgraph.installcli %*
exit /b %errorlevel%
