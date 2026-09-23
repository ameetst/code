@echo off
rem Starts the webapp on http://127.0.0.1:8000 and opens it once it's actually up.
rem Read-only by default; set SHARPE_READ_ONLY=0 to enable write routes (none exist yet).
cd /d "%~dp0\.."

if not exist "webapp\.venv\Scripts\python.exe" (
    echo Creating venv ^(inherits your existing pandas/numpy install^)...
    python -m venv --system-site-packages webapp\.venv || exit /b 1
    "webapp\.venv\Scripts\python.exe" -m pip install -r webapp\requirements.txt || exit /b 1
)

rem Runs in its own window so this script is free to poll for readiness below.
rem Importing pandas/numpy/scipy/momentum_lib takes several seconds -- opening
rem the browser immediately (before the port is bound) is what caused the
rem ERR_CONNECTION_REFUSED some users hit. Close the "Sharpe Webapp" window to stop the server.
start "Sharpe Webapp" /min "webapp\.venv\Scripts\python.exe" -m waitress --host=127.0.0.1 --port=8000 webapp.app:app

echo Starting server (first load imports pandas/numpy/scipy -- can take several seconds)...
:waitloop
curl -s -f -o nul http://127.0.0.1:8000/api/health
if errorlevel 1 (
    rem ~1s delay. Not `timeout`, which refuses to run when stdin is redirected
    rem (e.g. launched from another script or a task runner).
    ping -n 2 127.0.0.1 >nul
    goto waitloop
)

start "" http://127.0.0.1:8000
echo Sharpe webapp running at http://127.0.0.1:8000 -- close the "Sharpe Webapp" window to stop it.
