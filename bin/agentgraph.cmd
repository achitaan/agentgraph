@echo off
rem agentgraph launcher for Windows (cmd and PowerShell): the same commands as bin/agentgraph, run by agentgraph/cli.py.
setlocal
call "%~dp0_python.cmd"
if not defined AGENTGRAPH_PY (
  echo agentgraph needs Python 3.11 or newer. Install it from python.org, then check: py -3 --version 1>&2
  exit /b 1
)
set "PYTHONPATH=%~dp0.."
%AGENTGRAPH_PY% -P -m agentgraph.cli %*
exit /b %errorlevel%
