@echo off
rem Start the Engineering Project Platform: the API on http://localhost:8000,
rem the background worker, and the web app on http://localhost:5173, each in
rem its own window. Run setup.bat once first on a new PC.
setlocal
cd /d "%~dp0"

if not exist backend\venv (
    echo The platform is not set up on this PC yet: run setup.bat first.
    exit /b 1
)

rem --timeout-graceful-shutdown: a reload waits at most 3 s for open page
rem connections, instead of hanging on them with the old code still serving.
start "EP Platform - API" cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m uvicorn app.main:app --reload --reload-dir app --timeout-graceful-shutdown 3 --port 8000"
rem The worker runs every document sync, so reading a project folder never slows
rem the pages. Below normal priority: the engineer's programs and the API come
rem first. It has no hot reload: after changing backend code, close its window
rem and start it again with the same command.
start "EP Platform - Worker" /belownormal cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m app.workers.sync_worker"
start "EP Platform - Web" cmd /k "cd /d "%~dp0frontend" && npm run dev"

timeout /t 8 >nul
start "" http://localhost:5173
endlocal
