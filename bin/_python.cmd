@echo off
rem Sets AGENTGRAPH_PY to a command that starts Python 3.11 or newer; empty when there is none.
rem AGENTGRAPH_PYTHON (a full path, with or without quotes) wins, and must itself be 3.11 or newer; then "py -3"; then "python".
rem Used by agentgraph.cmd and install.cmd.
set "AGENTGRAPH_PY="
if not defined AGENTGRAPH_PYTHON goto probe
set "AGENTGRAPH_CHOSEN=%AGENTGRAPH_PYTHON:"=%"
"%AGENTGRAPH_CHOSEN%" -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 set "AGENTGRAPH_PY="%AGENTGRAPH_CHOSEN%""
set "AGENTGRAPH_CHOSEN="
exit /b 0
:probe
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 set "AGENTGRAPH_PY=py -3"
if defined AGENTGRAPH_PY exit /b 0
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 set "AGENTGRAPH_PY=python"
exit /b 0
