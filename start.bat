@echo off
rem Start the Engineering Project Platform: the API on http://localhost:8000,
rem the sync worker, the document processing worker, the IFC worker, and the
rem web app on http://localhost:5173, each in its own window. Run setup.bat
rem once first on a new PC.
setlocal
cd /d "%~dp0"

if not exist backend\venv (
    echo The platform is not set up on this PC yet: run setup.bat first.
    exit /b 1
)

rem The old backend processes first (the web app is left alone): a worker
rem has no hot reload, and one left running keeps the code it started with.
call "%~dp0stop-backend.bat"

rem --timeout-graceful-shutdown: a reload waits at most 3 s for open page
rem connections, instead of hanging on them with the old code still serving.
start "EP Platform - API" cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m uvicorn app.main:app --reload --reload-dir app --timeout-graceful-shutdown 3 --port 8000"
rem The sync worker runs every file sync (the index: a stat per file, seconds
rem a project), so listing a project folder never slows the pages. Below
rem normal priority: the engineer's programs and the API come first. It has
rem no hot reload: after changing backend code, close its window and start it
rem again with the same command.
start "EP Platform - Worker" /belownormal cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m app.workers.sync_worker"
rem The document processing worker reads the documents the sync found (the
rem PDFs, OCR, the AI on material submittal forms), one project at a time,
rem in the background while the platform is in use. Same rule: no hot reload.
start "EP Platform - Documents" /belownormal cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m app.workers.document_worker"
rem The IFC worker reads the uploaded IFC drawings (DWG conversion, CAD
rem extraction, the AI symbol review), IFC_WORKER_CONCURRENCY at a time, so
rem a building's drawings never slow the pages. Same rule: no hot reload.
start "EP Platform - IFC Worker" /belownormal cmd /k "cd /d "%~dp0backend" && venv\Scripts\python -m app.workers.ifc_worker"
start "EP Platform - Web" cmd /k "cd /d "%~dp0frontend" && npm run dev"

timeout /t 8 >nul
start "" http://localhost:5173
endlocal
