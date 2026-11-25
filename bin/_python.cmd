@echo off
rem Sets AGENTGRAPH_PY to a command that starts Python 3.11 or newer; empty when there is none.
rem AGENTGRAPH_PYTHON (a full path) wins; then "py -3"; then "python". Used by agentgraph.cmd and install.cmd.
set "AGENTGRAPH_PY="
if defined AGENTGRAPH_PYTHON set "AGENTGRAPH_PY="%AGENTGRAPH_PYTHON%""
if defined AGENTGRAPH_PY exit /b 0
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 set "AGENTGRAPH_PY=py -3"
if defined AGENTGRAPH_PY exit /b 0
python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>nul
if not errorlevel 1 set "AGENTGRAPH_PY=python"
exit /b 0
