@echo off
rem Start (or restart) the backend only: the API on http://localhost:8000 and
rem the three workers -- sync, document processing, IFC -- each in its own
rem window. The web app is left alone (start.bat starts everything). Use it
rem after changing backend code: the workers have no hot reload.
setlocal
cd /d "%~dp0"

if not exist backend\venv (
    echo The platform is not set up on this PC yet: run setup.bat first.
    exit /b 1
)

rem The old processes first: a worker has no hot reload, and one left
rem running keeps the code it started with (app.workers.runtime).
call "%~dp0stop-backend.bat"

start "EP Platform - API" cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m uvicorn app.main:app --reload --reload-dir app --timeout-graceful-shutdown 3 --port 8000"
start "EP Platform - Worker" /belownormal cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m app.workers.sync_worker"
start "EP Platform - Documents" /belownormal cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m app.workers.document_worker"
start "EP Platform - IFC Worker" /belownormal cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m app.workers.ifc_worker"
endlocal
